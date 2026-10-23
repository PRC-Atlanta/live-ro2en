"""Batch-transcribe the whole sermon archive with the chosen local model (Phase 4).

  .venv-nemo/bin/python src/batch_transcribe.py [--model jackrabbit-ctc-lm-boost] [--audio-dir Audio]
For every recording: Silero VAD splits speech at pauses into <=20 s segments (saved as
16 kHz WAV under work/archive/<stem>/), each segment is transcribed, and the result is
written to transcripts/local/<model>/archive/<stem>.jsonl (segment, start, end, text,
audio path). Files already done are skipped, so it can be stopped and restarted."""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "live"))
from vad import FRAME, SileroVAD           # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SR = 16000
EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".wma", ".mp4"}


def load(path: pathlib.Path) -> np.ndarray:
    out = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32)


def segment(x: np.ndarray, vad: SileroVAD, max_s=20.0, min_pause_s=0.3, thr=0.5) -> list[tuple[int, int]]:
    """Speech regions cut at pauses; regions longer than max_s are cut at the least-voiced frame."""
    vad.reset()
    n = len(x) // FRAME
    p = np.array([vad(x[i * FRAME:(i + 1) * FRAME]) for i in range(n)])
    speech = p >= thr
    regions, i = [], 0
    min_gap = int(min_pause_s * SR / FRAME)
    while i < n:
        if not speech[i]:
            i += 1; continue
        j, gap = i, 0
        while j < n and gap < min_gap:
            gap = 0 if speech[j] else gap + 1
            j += 1
        regions.append((max(0, i - 4), min(n, j - gap + 4)))      # 128 ms padding
        i = j
    out, maxf = [], int(max_s * SR / FRAME)
    for a, b in regions:
        while b - a > maxf:
            lo, hi = a + maxf // 2, a + maxf
            c = lo + int(np.argmin(p[lo:hi]))
            out.append((a, c)); a = c
        if b - a > 8:
            out.append((a, b))
    return [(a * FRAME, b * FRAME) for a, b in out]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="jackrabbit-ctc-lm-b8")
    ap.add_argument("--audio-dir", default="Audio")
    ap.add_argument("--limit", type=int, default=0, help="only the first N files (testing)")
    a = ap.parse_args()
    files = sorted(p for p in (ROOT / a.audio_dir).rglob("*") if p.suffix.lower() in EXTS
                   and not p.name.startswith("EN"))      # EN* = English recordings, not Romanian sermons
    if a.limit:
        files = files[:a.limit]
    if not files:
        print(f"no audio in {a.audio_dir}/"); return
    from engine import make_stt
    stt = make_stt(a.model)
    vad = SileroVAD()
    out_dir = ROOT / "transcripts" / "local" / a.model / "archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        dst = out_dir / f"{f.stem}.jsonl"
        if dst.exists():
            print("skip", f.name); continue
        t0 = time.time()
        x = load(f)
        segs = segment(x, vad)
        wav_dir = ROOT / "work" / "archive" / f.stem
        wav_dir.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".partial")
        with open(tmp, "w") as fh:
            for k, (s, e) in enumerate(segs):
                wav = wav_dir / f"{k:04d}.wav"
                if not wav.exists():
                    sf.write(wav, x[s:e], SR, subtype="PCM_16")
                text = stt(x[s:e])
                fh.write(json.dumps({"segment": f"{f.stem}/{k:04d}", "start": round(s / SR, 3), "end": round(e / SR, 3),
                                     "text": text.strip(), "audio": str(wav.relative_to(ROOT))}, ensure_ascii=False) + "\n")
        tmp.rename(dst)
        print(f"{f.name}: {len(segs)} segments, {len(x) / SR / 60:.1f} min audio in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()
