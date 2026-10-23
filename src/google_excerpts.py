"""Cut Google full-recording transcripts into the evaluation excerpts using word timings.

  .venv/bin/python src/google_excerpts.py [--set sermon] [--model chirp_3]
Reads transcripts/google/<model>/<recording>.json (from src/google_stt.py) and
work/<set>/manifest.csv; writes transcripts/google/<model>/<excerpt>.txt with the words
whose start time falls inside the excerpt. Sending whole sermons once and cutting here
avoids paying for the excerpt audio twice."""
from __future__ import annotations

import argparse
import json
import pathlib

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="sermon")
    ap.add_argument("--model", default="chirp_3")
    a = ap.parse_args()
    man = pd.read_csv(ROOT / "work" / a.set / "manifest.csv")
    gdir = ROOT / "transcripts" / "google" / a.model
    for ex, g in man.groupby("excerpt"):
        rec = g.book_stem.iloc[0]
        src = gdir / f"{rec}.json"
        if not src.exists():
            print("missing", src.name); continue
        t0 = g.abs_start_s.min()
        t1 = (g.abs_start_s + g.dur_s).max()
        segs = json.loads(src.read_text())["segments"]
        words = [w for s in segs for w in (s.get("words") or [])]
        if words:
            text = " ".join(w["word"] for w in words if t0 <= w["start"] < t1)
        else:      # no word timings: fall back to whole result segments that overlap
            text = " ".join(s["text"] for s in segs if s["end"] > t0 and s["start"] < t1)
        (gdir / f"{ex}.txt").write_text(text.strip() + "\n")
        print(ex, len(text.split()), "words", "(word timings)" if words else "(segment fallback)")


if __name__ == "__main__":
    main()
