"""STT test set from recordings that come with a timed transcript (<name>.wav + <name>_ro.srt).

  .venv/bin/python src/srt_set.py build [--audio-dir "Audio translated Samples"] [--set podcast]
                                        [--window luigi-1=84.6-1471.6] [--drop-cues luigi-1=1-6]
  .venv/bin/python src/srt_set.py score [--set podcast] [--source whisper-large-v3-mlx]

build: each recording is one excerpt, cut at pauses into <=20 s segments that cover all of it
(work/<set>/segments/*.wav + manifest.csv, the same columns as the sermon set, so
src/stt_run.py runs on it unchanged). The .srt text, with its cue times, goes to
work/<set>/refs/<excerpt>.json. When the audio is the raw recording and the .srt was made from
an edited version, --window keeps only the part of the audio the .srt covers (seconds of the
audio) and --drop-cues leaves out .srt cues that are not in the raw audio at that place (a
teaser clip at the start; cue numbers as in the file). Both are kept in the refs file.

score: every transcripts/local/*/<set>.jsonl against the .srt, whole recording against whole
reference (no edge effects from cutting the reference by time). Writes
results/<set>_scores.csv and results/<set>_ref_suspects.md: places where most of the models
from other families agree on the same words and the reference has something else. Those are
probably reference errors. Fix them in the .srt and score again.

The reference is a corrected machine transcript, so the model that made it (--source) and
its family score too well and are flagged in the table. Suspects are voted by one model
per family, never the source's family."""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import re
import sys
from collections import Counter, defaultdict

import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from bible_testset import SR, decode, split_segments       # noqa: E402
from score_bible import load_terms, term_stats              # noqa: E402
from scoring import align, cer                               # noqa: E402
from sermon_set import adaptive_thresh, duration             # noqa: E402
from textnorm import key_tokens, tokens                      # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
# a definite article after a hyphen on a loanword ("podcast-ul", "podcast-urile") is one word,
# as models write it ("podcastul"); tokens() would split it in two
_ARTICLE_RE = re.compile(r"(\w)-(ul|ului|uri|urile|urilor|ii|ilor)\b", re.IGNORECASE)


def keys(text: str, fold: bool = False) -> list[str]:
    return key_tokens(_ARTICLE_RE.sub(r"\1\2", text), fold, numbers=True)
# one model per family votes on reference suspects (as in the sermon consensus)
FAMILY_VOTERS = ["jackrabbit-ctc-lm-b8", "jackrabbit-stream-tdt", "sped-tdt", "canary-asr",
                 "parakeet-mlx", "whisper-large-v3-mlx"]
FAMILY = {"jackrabbit": "jackrabbit", "sped": "sped", "canary": "canary", "parakeet": "parakeet",
          "whisper": "whisper"}


def family(model: str) -> str:
    return next((f for k, f in FAMILY.items() if model.startswith(k)), model)


def parse_srt(path: pathlib.Path) -> list[dict]:
    """[{start, end, text}] in file order."""
    def sec(t: str) -> float:
        h, m, s = t.replace(",", ".").split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.strip().splitlines()
        timing = next((i for i, l in enumerate(lines) if "-->" in l), None)
        if timing is None:
            continue
        a, b = (x.strip() for x in lines[timing].split("-->"))
        cues.append({"start": sec(a), "end": sec(b.split()[0]), "text": " ".join(lines[timing + 1:]).strip()})
    return cues


def _per_stem(specs: list[str], conv) -> dict[str, tuple]:
    """['luigi-1=84.6-1471.6'] -> {'luigi-1': (84.6, 1471.6)}"""
    out = {}
    for s in specs or []:
        stem, span = s.split("=")
        a, b = span.split("-")
        out[stem] = (conv(a), conv(b))
    return out


def build(audio_dir: pathlib.Path, set_name: str, windows: dict, drops: dict) -> None:
    out = ROOT / "work" / set_name
    (out / "segments").mkdir(parents=True, exist_ok=True)
    (out / "refs").mkdir(parents=True, exist_ok=True)
    rows = []
    for srt in sorted(audio_dir.glob("*_ro.srt")):
        audio = next((p for p in audio_dir.glob(f"{srt.name[:-len('_ro.srt')]}.*") if p.suffix != ".srt"), None)
        if audio is None:
            print(f"skip {srt.name}: no audio next to it"); continue
        stem = re.sub(r"[^\w-]+", "-", audio.stem).strip("-").lower()
        t0, t1 = windows.get(stem, (0.0, duration(audio) + 1))
        x = decode(audio, t0, t1 - t0)
        for i, (sa, sb) in enumerate(split_segments(x, 20, 0.2, adaptive_thresh(x))):
            seg = f"{stem}_{i:03d}"
            if not (out / "segments" / f"{seg}.wav").exists():
                sf.write(out / "segments" / f"{seg}.wav", x[sa:sb], SR, subtype="PCM_16")
            rows.append({"segment": seg, "excerpt": stem, "book_stem": audio.stem, "book_number": 0,
                         "abs_start_s": round(t0 + sa / SR, 3), "dur_s": round((sb - sa) / SR, 3)})
        (out / "refs" / f"{stem}.json").write_text(json.dumps(
            {"audio": str(audio.relative_to(ROOT)), "srt": str(srt.relative_to(ROOT)), "window": [t0, t1],
             "drop_cues": list(drops.get(stem, ())), "cues": parse_srt(srt)},
            ensure_ascii=False, indent=1))
    with open(out / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"recordings={len({r['excerpt'] for r in rows})} segments={len(rows)} "
          f"minutes={sum(r['dur_s'] for r in rows) / 60:.1f}")


def _ref(set_name: str, ex: str) -> tuple[str, list[float]]:
    """Reference text (re-read from the .srt, so fixes there count) and each key token's cue time."""
    meta = json.loads((ROOT / "work" / set_name / "refs" / f"{ex}.json").read_text())
    cues = parse_srt(ROOT / meta["srt"])
    if meta.get("drop_cues"):
        a, b = meta["drop_cues"]
        cues = cues[:a - 1] + cues[b:]
    times = [c["start"] for c in cues for _ in keys(c["text"])]
    return " ".join(c["text"] for c in cues), times


def _hyps(set_name: str, order: dict[str, list[str]]) -> dict[str, dict[str, str]]:
    hyps: dict[str, dict[str, str]] = defaultdict(dict)
    for f in sorted((ROOT / "transcripts" / "local").glob(f"*/{set_name}.jsonl")):
        if f.parent.name.startswith("canary-ast"):           # English output
            continue
        rows = {j["segment"]: j for j in map(json.loads, f.read_text().splitlines()) if j}
        for ex, segs in order.items():
            if all(s in rows for s in segs):
                hyps[f.parent.name][ex] = " ".join(rows[s]["text"] for s in segs)
            else:
                print(f"SKIPPED {f.parent.name} on {ex}: {sum(s in rows for s in segs)}/{len(segs)} segments",
                      file=sys.stderr)
    return hyps


def _votes(ref: list[str], hyp: list[str]) -> dict[tuple, str]:
    """What hyp says where it differs from ref: {('sub', i): words} for ref token i replaced or
    dropped ('' = dropped), {('ins', i): words} for words inserted before ref token i."""
    out: dict[tuple, str] = {}
    k, ins = 0, []
    for r, h in align(ref, hyp, free_ends=False).pairs:
        if r is None:
            ins.append(h); continue
        if ins:
            out[("ins", k)] = " ".join(ins); ins = []
        if h != r:
            out[("sub", k)] = h or ""
        k += 1
    if ins:
        out[("ins", k)] = " ".join(ins)
    return out


def suspects(set_name: str, ex: str, ref_text: str, times: list[float], hyps: dict[str, str],
             source: str, min_votes: int) -> list[dict]:
    voters = [m for m in FAMILY_VOTERS if m in hyps and family(m) != family(source)]
    ref = keys(ref_text)
    tally: dict[tuple, Counter] = defaultdict(Counter)
    for m in voters:
        for pos, words in _votes(ref, keys(hyps[m])).items():
            tally[pos][words] += 1
    out = []
    for (kind, i), c in sorted(tally.items(), key=lambda kv: kv[0][1]):
        words, n = c.most_common(1)[0]
        if n >= min_votes:
            ctx = " ".join(ref[max(0, i - 4):i]), " ".join(ref[i + (kind == "sub"):i + 5])
            out.append({"time": times[min(i, len(times) - 1)], "kind": kind, "ref": ref[i] if kind == "sub" else "",
                        "models": words, "votes": f"{n}/{len(voters)}", "ctx": ctx})
    return out


def score_set(set_name: str, source: str, min_votes: int) -> None:
    import pandas as pd
    man = pd.read_csv(ROOT / "work" / set_name / "manifest.csv")
    order = man.groupby("excerpt")["segment"].apply(list).to_dict()
    hyps = _hyps(set_name, order)
    terms = load_terms()
    refs = {ex: _ref(set_name, ex) for ex in order}
    speed = {}
    for m in hyps:
        js = [json.loads(l) for l in (ROOT / "transcripts" / "local" / m / f"{set_name}.jsonl").read_text().splitlines() if l]
        speed[m] = sum(j["infer_s"] for j in js) / max(sum(j["audio_s"] for j in js), 1e-9)
    rows = []
    for m, exs in hyps.items():
        if len(exs) < len(order):
            continue
        agg = Counter()
        for ex, (ref_text, _) in refs.items():
            for fold, tag in ((False, ""), (True, "nd_")):
                rk, hk = keys(ref_text, fold), keys(exs[ex], fold)
                a = align(rk, hk, free_ends=False)
                agg[tag + "err"] += a.errors; agg[tag + "n"] += a.n_ref
                if not fold:
                    agg["sub"] += a.sub; agg["del"] += a.dele; agg["ins"] += a.ins
                    agg["cer_x"] += cer(" ".join(rk), " ".join(hk)) * a.n_ref
            for n_ref, hit, near in term_stats(tokens(ref_text), tokens(exs[ex]), terms).values():
                agg["t_n"] += n_ref; agg["t_hit"] += hit; agg["t_near"] += near
        rows.append({"model": m, "source_family": family(m) == family(source),
                     "wer": round(100 * agg["err"] / agg["n"], 2), "wer_nodiac": round(100 * agg["nd_err"] / agg["nd_n"], 2),
                     "cer": round(100 * agg["cer_x"] / agg["n"], 2),
                     "sub": agg["sub"], "del": agg["del"], "ins": agg["ins"], "n_words": agg["n"],
                     "term_acc": round(100 * agg["t_hit"] / max(agg["t_n"], 1), 1),
                     "term_near": round(100 * agg["t_near"] / max(agg["t_n"], 1), 1), "term_n": agg["t_n"],
                     "rtf": round(speed[m], 3)})
    rows.sort(key=lambda r: (r["source_family"], r["wer"]))
    out = ROOT / "results"
    with open(out / f"{set_name}_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(pd.DataFrame(rows).to_string(index=False))

    lines = [f"# Likely reference errors ({set_name})\n",
             f"Spots where at least {min_votes} of the models below (one per family, not the "
             f"reference's own family, {family(source)}) write the same thing and the .srt has something else. "
             "Listen at the time given; if the models are right, fix the .srt and rerun "
             f"`src/srt_set.py score --set {set_name}`. Words are shown in scoring form (â→î, ea→ia, ...).\n"]
    total = 0
    for ex, (ref_text, times) in refs.items():
        sus = suspects(set_name, ex, ref_text, times, {m: hyps[m][ex] for m in hyps if ex in hyps[m]},
                       source, min_votes)
        total += len(sus)
        voters = [m for m in FAMILY_VOTERS if m in hyps and family(m) != family(source)]
        lines.append(f"\n## {ex} — {len(sus)} spots (voters: {', '.join(voters)})\n")
        lines.append("| .srt time | Reference | Models | Votes | Context |\n|---|---|---|---|---|")
        for s in sus:
            mm, ss = divmod(int(s["time"]), 60)
            refw = s["ref"] if s["kind"] == "sub" else "—"
            mod = s["models"] or "(nothing)"
            lines.append(f"| {mm:02d}:{ss:02d} | {refw} | {mod} | {s['votes']} | …{s['ctx'][0]} **[{refw if s['kind'] == 'sub' else '+'}]** {s['ctx'][1]}… |")
    (out / f"{set_name}_ref_suspects.md").write_text("\n".join(lines) + "\n")
    print(f"{total} reference suspects -> results/{set_name}_ref_suspects.md")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--audio-dir", default="Audio translated Samples")
    b.add_argument("--set", default="podcast")
    b.add_argument("--window", action="append", help="STEM=FROM-TO seconds of audio to keep")
    b.add_argument("--drop-cues", action="append", help="STEM=A-B .srt cue numbers to leave out")
    s = sub.add_parser("score")
    s.add_argument("--set", default="podcast")
    s.add_argument("--source", default="whisper-large-v3-mlx", help="the model the reference was made with")
    s.add_argument("--min-votes", type=int, default=3)
    a = ap.parse_args()
    if a.cmd == "build":
        build(ROOT / a.audio_dir, a.set, _per_stem(a.window, float), _per_stem(a.drop_cues, int))
    else:
        score_set(a.set, a.source, a.min_votes)


if __name__ == "__main__":
    main()
