"""Split corrected sermon data into train / holdout NeMo manifests.

  .venv/bin/python src/finetune/build_manifests.py [--holdout-frac 0.1] [--allow-pending]
Input: data/finetune/manifest_clean.jsonl and manifest_google.jsonl (exported by src/correct_tool.py).
Output: data/finetune/train.jsonl, data/finetune/holdout.jsonl, data/finetune/split.json.
Rules: split BY SERMON (no speaker/session leakage); Bible audio is always rejected;
sermons used for STT evaluation (work/*/manifest.csv sermon sets, review_packet/) always go
to the holdout, never to training, so later sermon WERs are not inflated;
rows whose text is unedited Google output ("pending_terms_check") are excluded unless
--allow-pending is given, which should only happen after DECISIONS.md records that
Google's terms allow it."""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import random

ROOT = pathlib.Path(__file__).resolve().parents[2]
D = ROOT / "data" / "finetune"
FORBIDDEN = ("BibleAudio", "work/bible", "work/e2e")
NOT_SERMON_EVAL = ("bible", "e2e", "archive")   # Bible/e2e: rejected above; archive: training pool


def eval_sermons(root: pathlib.Path = ROOT) -> set[str]:
    """Stems of every sermon an STT evaluation was run on."""
    stems = set()
    for man in (root / "work").glob("*/manifest.csv"):
        if man.parent.name not in NOT_SERMON_EVAL:
            stems |= {r["book_stem"] for r in csv.DictReader(open(man))}
    pk = root / "review_packet" / "packet.json"
    if pk.exists():
        stems |= {c["excerpt"].rsplit("_", 1)[0] for c in json.loads(pk.read_text())["clips"]}
    return stems


def split_sermons(sermons: list[str], holdout_frac: float, seed: int, forced: set[str]) -> set[str]:
    """Holdout sermons: every evaluation sermon, plus a random share of the rest."""
    rest = sorted(set(sermons) - forced)
    random.Random(seed).shuffle(rest)
    n_hold = max(1, round(len(sermons) * holdout_frac)) if len(sermons) > 1 else 0
    return (set(sermons) & forced) | set(rest[:max(0, n_hold - len(set(sermons) & forced))])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout-frac", type=float, default=0.1)
    ap.add_argument("--allow-pending", action="store_true")
    ap.add_argument("--seed", type=int, default=13)
    a = ap.parse_args()
    rows = [json.loads(l) for f in sorted(D.glob("manifest_*.jsonl")) if f.name != "manifest_all.jsonl"
            for l in f.read_text().splitlines() if l.strip()]
    # the same segment may be corrected in both modes: prefer the Google-free (clean) version
    by_audio = {}
    for r in rows:
        if r["audio_filepath"] not in by_audio or not r.get("pending_terms_check"):
            by_audio[r["audio_filepath"]] = r
    rows = list(by_audio.values())
    bad = [r for r in rows if any(f in r["audio_filepath"] for f in FORBIDDEN)]
    if bad:
        raise SystemExit(f"refusing: {len(bad)} rows point at Bible/eval audio (evaluation only, never training)")
    pend = [r for r in rows if r.get("pending_terms_check")]
    if not a.allow_pending:
        rows = [r for r in rows if not r.get("pending_terms_check")]
    rows = [r for r in rows if r["text"].strip() and 0.5 <= r["duration"] <= 30]
    sermons = sorted({r["sermon"] for r in rows})
    forced = eval_sermons()
    hold = split_sermons(sermons, a.holdout_frac, a.seed, forced)
    if not set(sermons) - hold:
        raise SystemExit(f"no training data: every corrected sermon ({', '.join(sermons) or 'none'}) is held out "
                         "(evaluation sermons always are). Correct at least one other sermon first.")
    keys = ("audio_filepath", "duration", "text")
    for name, sel in (("train", lambda r: r["sermon"] not in hold), ("holdout", lambda r: r["sermon"] in hold)):
        with open(D / f"{name}.jsonl", "w") as f:
            for r in rows:
                if sel(r):
                    f.write(json.dumps({k: r[k] for k in keys}, ensure_ascii=False) + "\n")
    info = {"sermons_train": sorted(set(sermons) - hold), "sermons_holdout": sorted(hold),
            "sermons_eval_forced_holdout": sorted(set(sermons) & forced),
            "rows": len(rows), "excluded_pending_terms": 0 if a.allow_pending else len(pend),
            "hours_train": round(sum(r["duration"] for r in rows if r["sermon"] not in hold) / 3600, 2)}
    (D / "split.json").write_text(json.dumps(info, indent=1))
    print(info)


if __name__ == "__main__":
    main()
