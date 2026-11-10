"""Live Voice M0 measurements (docs/live_voice_plan.md §6, §11): how fast Supertonic 3 speaks on this
Mac for a given onnxruntime thread count, and sample clips to check how it reads digits and references.

  .venv-voice/bin/python scripts/voice_m0.py --threads 4 --out logs/voice_m0/t4 [--raw]
  taskpolicy -b .venv-voice/bin/python scripts/voice_m0.py --threads 4 --out logs/voice_m0/t4_bg

--raw voices the text as written (no speakable-text step), to hear what the model does with digits.
Prints one JSON line: rtf (generation seconds / audio seconds), median seconds per sentence, per language."""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw", action="store_true")
    ap.add_argument("--speed", type=float, default=1.25)
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()
    from voice import speakable, synth
    import soundfile as sf
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    tts = synth.Synth(threads=a.threads)
    load_s = time.perf_counter() - t0
    res = {"threads": a.threads, "load_s": round(load_s, 2), "speed": a.speed, "raw": a.raw}
    for lang in ("ro", "en"):
        lines = [l.strip() for l in (ROOT / "data" / "voice" / f"m0_{lang}.txt").read_text().splitlines() if l.strip()]
        gen, aud, rows = [], [], []
        for _ in range(a.repeat):
            for i, line in enumerate(lines):
                text = line if a.raw else speakable.speakable(line, lang)
                t = time.perf_counter()
                wav = tts.say(text, lang, a.speed)
                g = time.perf_counter() - t
                gen.append(g)
                aud.append(len(wav) / synth.SR)
                sf.write(out / f"{lang}_{i:02d}.wav", wav, synth.SR)
                rows.append({"i": i, "text": text, "gen_s": round(g, 3), "audio_s": round(aud[-1], 3)})
        res[lang] = {"rtf": round(sum(gen) / sum(aud), 3), "gen_s_median": round(statistics.median(gen), 3),
                     "gen_s_max": round(max(gen), 3), "audio_s": round(sum(aud), 1)}
        (out / f"{lang}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    print(json.dumps(res))


if __name__ == "__main__":
    main()
