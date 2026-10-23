"""Build the Bible evaluation set: excerpts cut at silence gaps (FLAC, for Google) and
<=20 s segments (WAV, for local models), plus work/bible/manifest.csv.

The Bible audio is EVALUATION ONLY (unknown source/licence): never train on it."""
from __future__ import annotations

import csv
import pathlib
import subprocess

import numpy as np
import soundfile as sf
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
SR = 16000
FRAME = 320  # 20 ms


def decode(path: pathlib.Path, start: float, dur: float) -> np.ndarray:
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", str(start), "-t", str(dur),
           "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, check=True, capture_output=True).stdout, np.float32)


def silence_runs(x: np.ndarray, min_sil: float, thresh: float = 3e-3) -> list[tuple[int, int]]:
    """(start_sample, end_sample) of runs quieter than thresh lasting >= min_sil."""
    n = len(x) // FRAME
    rms = np.sqrt((x[: n * FRAME].reshape(n, FRAME) ** 2).mean(1))
    quiet = rms < thresh
    runs, i = [], 0
    while i < n:
        if quiet[i]:
            j = i
            while j < n and quiet[j]:
                j += 1
            if (j - i) * FRAME >= min_sil * SR:
                runs.append((i * FRAME, j * FRAME))
            i = j
        else:
            i += 1
    return runs


def split_segments(x: np.ndarray, max_s: float, min_sil: float, thresh: float = 3e-3) -> list[tuple[int, int]]:
    """Cut x into <=max_s pieces at silence midpoints; force-cut at the quietest frame
    if a speech run is longer than max_s."""
    cuts = [(a + b) // 2 for a, b in silence_runs(x, min_sil, thresh)]
    bounds, start, maxn = [], 0, int(max_s * SR)
    while len(x) - start > maxn:
        cand = [c for c in cuts if start + SR < c <= start + maxn]
        if cand:
            end = cand[-1]
        else:
            seg = x[start + SR: start + maxn]
            n = len(seg) // FRAME
            e = (seg[: n * FRAME].reshape(n, FRAME) ** 2).mean(1)
            end = start + SR + int(np.argmin(e)) * FRAME
        bounds.append((start, end))
        start = end
    bounds.append((start, len(x)))
    return [(a, b) for a, b in bounds if b - a > 0.3 * SR]


def main() -> None:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    bt = cfg["bible_testset"]
    src_dir = ROOT / cfg["paths"]["bible_audio_dir"]
    out = ROOT / "work" / "bible"
    (out / "excerpts").mkdir(parents=True, exist_ok=True)
    (out / "segments").mkdir(parents=True, exist_ok=True)
    rows = []
    for stem, spans in bt["excerpts"].items():
        mp3 = src_dir / f"{stem}.mp3"
        for k, span in enumerate(spans):
            start, length = span[0], span[1]
            pad = 15.0
            s0 = max(0.0, start - pad)
            x = decode(mp3, s0, length + 2 * pad)
            runs = silence_runs(x, 0.4)
            a = 0 if start == 0 else next((r[1] - 1600 for r in runs if r[0] >= (start - s0) * SR), 0)
            target = a + int(length * SR)
            b = max((r[0] + 1600 for r in runs if r[0] <= target and r[0] > a), default=len(x))
            clip = x[a:b]
            name = span[2] if len(span) > 2 else f"{stem}_{k:02d}"
            if not (out / "excerpts" / f"{name}.flac").exists():
                sf.write(out / "excerpts" / f"{name}.flac", clip, SR, format="FLAC", subtype="PCM_16")
            for i, (sa, sb) in enumerate(split_segments(clip, bt["max_segment_s"], bt["min_silence_s"])):
                seg = f"{name}_{i:03d}"
                if not (out / "segments" / f"{seg}.wav").exists():
                    sf.write(out / "segments" / f"{seg}.wav", clip[sa:sb], SR, subtype="PCM_16")
                rows.append({"segment": seg, "excerpt": name, "book_stem": stem,
                             "book_number": int(stem.split("-")[0]),
                             "abs_start_s": round(s0 + (a + sa) / SR, 3),
                             "dur_s": round((sb - sa) / SR, 3)})
            print(f"{name}: {len(clip) / SR / 60:.2f} min", flush=True)
    with open(out / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tot = sum(r["dur_s"] for r in rows)
    print(f"segments={len(rows)} total_min={tot / 60:.1f} max_seg={max(r['dur_s'] for r in rows)}")


if __name__ == "__main__":
    main()
