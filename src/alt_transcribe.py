"""Second-opinion transcripts for the archive segments (for correction highlights without Google).

  .venv-mlx/bin/python src/alt_transcribe.py [--model whisper-large-v3-mlx]
Reads transcripts/local/<primary>/archive/<stem>.jsonl (segment list from batch_transcribe.py),
transcribes the same WAV segments with --model, and writes
transcripts/local/<model>/archive/<stem>.jsonl with identical segment ids. Resumable."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="whisper-large-v3-mlx")
    ap.add_argument("--primary", default="jackrabbit-ctc-lm-b8")
    a = ap.parse_args()
    import stt_run
    fn = stt_run.MODELS[a.model]()
    out_dir = ROOT / "transcripts" / "local" / a.model / "archive"
    out_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted((ROOT / "transcripts" / "local" / a.primary / "archive").glob("*.jsonl")):
        dst = out_dir / src.name
        done = {json.loads(l)["segment"] for l in dst.read_text().splitlines()} if dst.exists() else set()
        with open(dst, "a") as f:
            for line in src.read_text().splitlines():
                s = json.loads(line)
                if s["segment"] in done:
                    continue
                f.write(json.dumps({**s, "text": fn(str(ROOT / s["audio"])).strip()}, ensure_ascii=False) + "\n")
                f.flush()
        print("done", src.stem, flush=True)


if __name__ == "__main__":
    main()
