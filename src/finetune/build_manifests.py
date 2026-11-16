"""Training / dev / holdout NeMo manifests for self-training on the sermon archive.

  .venv/bin/python src/finetune/build_manifests.py [--max-disagreement 0.10] [--holdout br-random-1 nelu-suciu]

Sources (all Google-free):
  corrected  hand-corrected Romanian transcript, data/finetune/corrections_clean/<stem>.json
             (src/correct_tool.py --google none; verified segments; source "local" / "local-alt").
             Takes precedence over a pseudo-label for the same audio.
  pseudo     the recogniser's own text for an archive segment, kept only when a second, different
             recogniser agrees closely: data/finetune/agreement.jsonl (src/finetune/agreement.py),
             disagreement (WER of the pseudo-label against the second opinion) <= --max-disagreement.
Never used: Google-derived text (data/finetune/corrections/, anything with source "google" or
"pending_terms_check"); the Bible audio and other evaluation-only audio (FORBIDDEN); any audio of
a held-out sermon.

Held out: luigi-mitoi always (the test sermon); --holdout adds benchmark sermons (br-random-1,
nelu-suciu). A held-out sermon is excluded by its whole source recording (found from
logs/bench/audio/corr_<name>.json "source"), and every training segment of >= 5 s is also
checked by content against the held-out audio (100 ms energy envelopes); a match is refused.

Earlier evaluations (eval_sermons(): the Phase 1 sermon excerpts in work/sermon, the review
packet) are reported, not held out: --exclude-eval-excerpts drops the training segments that
overlap those excerpts in time.

Writes data/finetune/train.jsonl, dev.jsonl (contiguous blocks of the training pool, for the
validation loss only), holdout.jsonl (corrected segments of held-out sermons, for
eval_holdout.py), <name>_provenance.jsonl (the same rows with source, sermon, disagreement) and
split.json (hours per source)."""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
D = ROOT / "data" / "finetune"
BENCH_AUDIO = ROOT / "logs" / "bench" / "audio"
TEST = ("luigi-mitoi",)                          # never in training, whatever the flags
BENCHMARKS = ("br-random-1", "nelu-suciu", "luigi-mitoi")
FORBIDDEN = ("BibleAudio", "work/bible", "work/e2e", "work/podcast", "logs/bench", "logs/recordings")
NOT_SERMON_EVAL = ("bible", "e2e", "archive")   # Bible/e2e: rejected above; archive: training pool
MIN_S, MAX_S = 0.5, 20.5
SR = 16000
HOP = 1600                                       # 100 ms envelope frames
MATCH = 0.95                                     # envelope correlation that counts as the same audio
PROBE_MIN_S = 5.0                                # shorter segments are not distinctive enough to probe


def eval_sermons(root: pathlib.Path = ROOT) -> set[str]:
    """Stems of every sermon an STT evaluation was run on (Phase 1 sets, review packet)."""
    stems = set()
    for man in (root / "work").glob("*/manifest.csv"):
        if man.parent.name not in NOT_SERMON_EVAL:
            stems |= {r["book_stem"] for r in csv.DictReader(open(man))}
    pk = root / "review_packet" / "packet.json"
    if pk.exists():
        stems |= {c["excerpt"].rsplit("_", 1)[0] for c in json.loads(pk.read_text())["clips"]}
    return stems


def split_sermons(sermons: list[str], holdout_frac: float, seed: int, forced: set[str]) -> set[str]:
    """Holdout sermons: every forced one, plus a random share of the rest (kept for a by-sermon
    split once the archive has many sermons)."""
    rest = sorted(set(sermons) - forced)
    random.Random(seed).shuffle(rest)
    n_hold = max(1, round(len(sermons) * holdout_frac)) if len(sermons) > 1 else 0
    return (set(sermons) & forced) | set(rest[:max(0, n_hold - len(set(sermons) & forced))])


# --- held-out audio ---------------------------------------------------------------------------

def heldout_keys(names: list[str]) -> dict[str, set[str]]:
    """Held-out sermon -> names that identify its audio: the sermon name and the stem of the
    archive recording it was cut from (corr_<name>.json "source", checked by audio fingerprint)."""
    out = {}
    for n in names:
        keys = {n}
        side = BENCH_AUDIO / f"corr_{n}.json"
        if side.exists():
            src = json.loads(side.read_text()).get("source")
            if src:
                keys.add(pathlib.Path(src).stem)
        out[n] = keys
    return out


def envelope(x: np.ndarray) -> np.ndarray:
    n = len(x) // HOP
    return np.log(1e-8 + (x[:n * HOP].reshape(n, HOP).astype(np.float64) ** 2).mean(1))


def _audio16(path: pathlib.Path) -> np.ndarray:
    import soundfile as sf
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != SR:
        raise SystemExit(f"{path}: {sr} Hz, expected {SR}")
    return x.mean(1)


def best_match(probe: np.ndarray, env: np.ndarray) -> float:
    """Highest Pearson correlation of `probe` with any equally long window of `env` (FFT)."""
    L = len(probe)
    if L < 2 or len(env) < L:
        return 0.0
    p = probe - probe.mean()
    ps = np.sqrt((p ** 2).sum())
    if ps == 0:
        return 0.0
    n = 1 << int(np.ceil(np.log2(len(env) + L)))
    raw = np.fft.irfft(np.fft.rfft(env, n) * np.conj(np.fft.rfft(p, n)), n)[:len(env) - L + 1]
    c1 = np.concatenate([[0.0], np.cumsum(env)])
    c2 = np.concatenate([[0.0], np.cumsum(env ** 2)])
    s1, s2 = c1[L:] - c1[:-L], c2[L:] - c2[:-L]
    sd = np.sqrt(np.maximum(s2 - s1 ** 2 / L, 1e-12))
    return float((raw / (ps * sd)).max())


def content_check(rows: list[dict], held: list[str]) -> dict:
    """Every training segment of >= PROBE_MIN_S against every held-out recording available as
    logs/bench/audio/corr_<name>.wav. Returns the closest score per held-out sermon; exits on a match."""
    envs = {n: envelope(_audio16(BENCH_AUDIO / f"corr_{n}.wav")) for n in held
            if (BENCH_AUDIO / f"corr_{n}.wav").exists()}
    worst = {n: (0.0, None) for n in envs}
    probes = 0
    for r in rows:
        if r["duration"] < PROBE_MIN_S:
            continue
        pe = envelope(_audio16(pathlib.Path(r["audio_filepath"])))
        probes += 1
        for n, env in envs.items():
            s = best_match(pe, env)
            if s > worst[n][0]:
                worst[n] = (s, r["segment"])
            if s >= MATCH:
                raise SystemExit(f"refusing: {r['audio_filepath']} matches held-out {n} audio (envelope corr {s:.3f})")
    return {"probes": probes, "threshold": MATCH,
            "closest": {n: {"corr": round(s, 3), "segment": p} for n, (s, p) in worst.items()},
            "no_audio_to_check": sorted(set(held) - set(envs))}


# --- sources -------------------------------------------------------------------------------------

def _spell(text: str) -> str:
    from textnorm import apply_spellings, load_spellings
    return apply_spellings(text, load_spellings(user_path=ROOT / "no-user-settings.yaml"))  # bundled only


def corrected_rows(corr_dir: pathlib.Path, seg_index: dict[str, dict]) -> tuple[list[dict], int]:
    """Verified Google-free corrections, keyed by archive segment; and how many Google-started
    ones were skipped (Google Cloud Service Specific Terms §17(b), DECISIONS.md 2026-10-22)."""
    rows, google = [], 0
    for f in sorted(corr_dir.glob("*.json")) if corr_dir.is_dir() else []:
        for seg, c in json.loads(f.read_text()).items():
            if not c.get("verified"):
                continue
            if c.get("source") not in ("local", "local-alt") or c.get("pending_terms_check"):
                google += 1
                continue
            s = seg_index.get(seg)
            if s is None:
                raise SystemExit(f"{f.name}: correction for {seg}, which agreement.jsonl does not list "
                                 "(re-run src/finetune/agreement.py)")
            rows.append({**s, "text": _spell(c["corrected"].strip()), "source": "corrected",
                         "corr_source": c["source"], "human_edited": bool(c.get("edited"))})
    return rows, google


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-disagreement", type=float, default=0.10,
                    help="keep a pseudo-label when its WER against the second recogniser is <= this "
                         "(fraction, 0.10 = 10%%); negative: no pseudo-labels")
    ap.add_argument("--metric", choices=("wer", "wer_key"), default="wer",
                    help="agreement measure (wer: textnorm.tokens; wer_key: phonetic keys, digits spelled out)")
    ap.add_argument("--holdout", nargs="*", default=[], choices=BENCHMARKS,
                    help="benchmark sermons held out in addition to luigi-mitoi (always held out)")
    ap.add_argument("--no-corrected", action="store_true", help="pseudo-labels only")
    ap.add_argument("--exclude-eval-excerpts", action="store_true",
                    help="drop training segments overlapping the Phase 1 sermon excerpts (work/sermon/manifest.csv)")
    ap.add_argument("--dev-every", type=int, default=20,
                    help="dev set: the middle block of 20 consecutive segments in every N blocks (0: none)")
    ap.add_argument("--skip-content-check", action="store_true")
    ap.add_argument("--agreement", default=None, help="default data/finetune/agreement.jsonl")
    ap.add_argument("--corrections", default=None, help="default data/finetune/corrections_clean")
    ap.add_argument("--out", default=None, help="default data/finetune")
    a = ap.parse_args(argv)
    agreement = pathlib.Path(a.agreement or D / "agreement.jsonl")
    out = pathlib.Path(a.out or D)

    held = sorted(set(TEST) | set(a.holdout))
    keys_of = heldout_keys(held)
    agree = [json.loads(l) for l in open(agreement) if l.strip()] if agreement.exists() else []
    if not agree:
        print(f"warning: {agreement} missing or empty: no pseudo-labels (run src/finetune/agreement.py)", file=sys.stderr)
    seg_index = {r["segment"]: {"segment": r["segment"], "sermon": r["sermon"],
                                "audio_filepath": str((ROOT / r["audio"]).resolve()), "duration": r["duration"],
                                "start": r["start"], "end": r["end"]} for r in agree}

    corr, google = ([], 0) if a.no_corrected else corrected_rows(pathlib.Path(a.corrections or D / "corrections_clean"),
                                                                 seg_index)
    corr_by = {r["segment"]: r for r in corr}
    pseudo, rejected = [], 0
    if a.max_disagreement >= 0:
        for r in agree:
            if r["segment"] in corr_by or not r["label"].strip():
                continue
            if r[a.metric] > a.max_disagreement + 1e-9:
                rejected += 1
                continue
            pseudo.append({**seg_index[r["segment"]], "text": _spell(r["label"]), "source": "pseudo",
                           "disagreement": r[a.metric]})
    pool = sorted(corr + pseudo, key=lambda r: r["segment"])

    def held_by(r: dict) -> str | None:
        for n, keys in keys_of.items():
            if r["sermon"] in keys or any(f"/{k}/" in r["audio_filepath"] or k in pathlib.Path(r["audio_filepath"]).name
                                          for k in keys):
                return n
        return None

    bad = [r for r in pool if any(f in r["audio_filepath"] for f in FORBIDDEN)]
    if bad:
        raise SystemExit(f"refusing: {len(bad)} rows point at evaluation-only audio, e.g. {bad[0]['audio_filepath']}")
    holdout = [r for r in corr if held_by(r)]
    train_pool = [r for r in pool if not held_by(r) and MIN_S <= r["duration"] <= MAX_S and r["text"].strip()]

    spans: dict[str, list[tuple[float, float]]] = {}
    man = ROOT / "work" / "sermon" / "manifest.csv"
    if man.exists():
        for e in csv.DictReader(open(man)):
            s0 = float(e["abs_start_s"])
            spans.setdefault(e["book_stem"], []).append((s0, s0 + float(e["dur_s"])))
    in_excerpt = [r for r in train_pool if any(r["start"] < b and r["end"] > s for s, b in spans.get(r["sermon"], []))]
    if a.exclude_eval_excerpts:
        drop = {r["segment"] for r in in_excerpt}
        train_pool = [r for r in train_pool if r["segment"] not in drop]

    if not train_pool:
        raise SystemExit(f"no training data: nothing left after holding out {', '.join(held)} and the filters "
                         f"({len(corr)} corrected, {len(pseudo)} pseudo-labelled segments before that)")
    content = {"skipped": True} if a.skip_content_check else content_check(train_pool, held)

    train, dev = [], []
    for sm in sorted({r["sermon"] for r in train_pool}):
        rows = [r for r in train_pool if r["sermon"] == sm]
        for i, r in enumerate(rows):
            (dev if a.dev_every and (i // 20) % a.dev_every == a.dev_every // 2 else train).append(r)

    out.mkdir(parents=True, exist_ok=True)
    cols = ("audio_filepath", "duration", "text")
    for name, rows in (("train", train), ("dev", dev), ("holdout", holdout)):
        with open(out / f"{name}.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps({k: r[k] for k in cols}, ensure_ascii=False) + "\n")
        with open(out / f"{name}_provenance.jsonl", "w") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def hours(rows, **kw):
        return round(sum(r["duration"] for r in rows if all(r.get(k) == v for k, v in kw.items())) / 3600, 3)
    prev_eval = sorted(eval_sermons() & {r["sermon"] for r in train})
    info = {"held_out": held, "held_out_audio": {n: sorted(k) for n, k in keys_of.items()},
            "max_disagreement": a.max_disagreement, "metric": a.metric,
            "excluded_google_corrections": google, "agreement_segments": len(agree),
            "pseudo_rejected_by_agreement": rejected,
            "train": {"utts": len(train), "hours": hours(train), "hours_corrected": hours(train, source="corrected"),
                      "hours_pseudo": hours(train, source="pseudo"),
                      "by_sermon": {sm: {"corrected_h": hours(train, sermon=sm, source="corrected"),
                                         "pseudo_h": hours(train, sermon=sm, source="pseudo")}
                                    for sm in sorted({r["sermon"] for r in train})}},
            "dev": {"utts": len(dev), "hours": hours(dev)},
            "holdout": {"utts": len(holdout), "hours": hours(holdout), "sermons": sorted({r["sermon"] for r in holdout})},
            "earlier_eval_sermons_in_training": prev_eval,
            "eval_excerpt_overlap": {"segments": len(in_excerpt), "hours": hours(in_excerpt),
                                     "dropped": a.exclude_eval_excerpts},
            "content_check": content}
    (out / "split.json").write_text(json.dumps(info, indent=1, ensure_ascii=False))
    print(json.dumps(info, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
