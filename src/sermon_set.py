"""Build the sermon evaluation set from ./Audio: 2-3 min excerpts at ~20/50/80 % of each
sermon (round-robin over files) until ~25 min, cut at pauses into <=20 s segments.

  .venv/bin/python src/sermon_set.py [--minutes 25] [--audio-dir Audio] [--out work/sermon]
Writes <out>/excerpts/*.flac, <out>/segments/*.wav, <out>/manifest.csv (same columns as the
Bible manifest, book_number=0). Re-running keeps existing files."""
from __future__ import annotations

import argparse
import csv
import pathlib
import subprocess
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from bible_testset import FRAME, SR, decode, split_segments   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".mp4"}


def duration(p: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True)
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def adaptive_thresh(x: np.ndarray) -> float:
    n = len(x) // FRAME
    rms = np.sqrt((x[: n * FRAME].reshape(n, FRAME) ** 2).mean(1))
    return float(max(np.percentile(rms, 15) * 1.8, 1e-3))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=25)
    ap.add_argument("--audio-dir", default="Audio")
    ap.add_argument("--out", default="work/sermon")
    ap.add_argument("--excerpt-s", type=float, default=150)
    a = ap.parse_args()
    files = sorted(p for p in (ROOT / a.audio_dir).rglob("*") if p.suffix.lower() in EXTS
                   and not p.name.startswith("EN"))      # EN* = English recordings (EN→RO tests)
    if not files:
        print(f"no audio in {a.audio_dir}/ — add sermon recordings and rerun"); return
    out = ROOT / a.out
    (out / "excerpts").mkdir(parents=True, exist_ok=True)
    (out / "segments").mkdir(parents=True, exist_ok=True)
    durs = {f: duration(f) for f in files}
    plan, total = [], 0.0
    for pos in (0.5, 0.2, 0.8, 0.35, 0.65):
        for f in files:
            if total >= a.minutes * 60:
                break
            if durs[f] < a.excerpt_s * 2:
                continue
            plan.append((f, pos * durs[f])); total += a.excerpt_s
    rows = []
    for f, start in plan:
        stem = f"{f.stem}_{int(start):05d}"
        x = decode(f, start, a.excerpt_s)
        # trim to pause boundaries so excerpts don't start/end mid-word
        n = len(x) // FRAME
        rms = np.sqrt((x[: n * FRAME].reshape(n, FRAME) ** 2).mean(1))
        th = adaptive_thresh(x)
        quiet = np.where(rms < th)[0]
        a0 = int(quiet[quiet < n // 4][0]) * FRAME if (quiet < n // 4).any() else 0
        b0 = int(quiet[quiet > 3 * n // 4][-1]) * FRAME if (quiet > 3 * n // 4).any() else len(x)
        clip = x[a0:b0]
        if not (out / "excerpts" / f"{stem}.flac").exists():
            sf.write(out / "excerpts" / f"{stem}.flac", clip, SR, format="FLAC", subtype="PCM_16")
        segs = split_segments(clip, 20, 0.2, th)
        for i, (sa, sb) in enumerate(segs):
            seg = f"{stem}_{i:03d}"
            if not (out / "segments" / f"{seg}.wav").exists():
                sf.write(out / "segments" / f"{seg}.wav", clip[sa:sb], SR, subtype="PCM_16")
            rows.append({"segment": seg, "excerpt": stem, "book_stem": f.stem, "book_number": 0,
                         "abs_start_s": round(start + (a0 + sa) / SR, 3), "dur_s": round((sb - sa) / SR, 3)})
    with open(out / "manifest.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
    print(f"excerpts={len(plan)} segments={len(rows)} minutes={sum(r['dur_s'] for r in rows) / 60:.1f}")


if __name__ == "__main__":
    main()
