"""Machine speed test: the live app's recognition and translation on the same input, so two
Macs can be compared. Audio is made with macOS's Romanian voice (say -v Ioana), so no
recordings are needed. Prints one JSON line per model.

  scripts/speed_bench.sh            # wrapper: picks the repo venvs or the installed app
  python speed_bench.py stt jackrabbit-ctc-lm-b8|parakeet-mlx
  python speed_bench.py mt nllb:nllb-200-distilled-1.3B|mlx:mlx-community/gemma-4-12B-it-qat-4bit
"""
from __future__ import annotations

import json
import os
import pathlib
import platform
import statistics
import subprocess
import sys
import tempfile
import time

SRC = pathlib.Path(os.environ.get("BENCH_SRC") or pathlib.Path(__file__).resolve().parents[1] / "src")
DATA = pathlib.Path(__file__).resolve().parents[1] / "data" / "mt" / "bible_verses.jsonl"
sys.path.insert(0, str(SRC)); sys.path.insert(0, str(SRC / "live"))
N = 30


def sentences() -> list[str]:
    return [json.loads(l)["ro_stt"] for l in DATA.read_text().splitlines()][::5][:N]


def chip() -> str:
    return subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()


def q(ts: list[float]) -> dict:
    ts = sorted(ts)
    return {"median_s": round(statistics.median(ts), 3), "p90_s": round(ts[int(0.9 * (len(ts) - 1))], 3)}


def clips(sec: float) -> list:
    """N clips of exactly `sec` seconds of Romanian speech (16 kHz mono)."""
    import numpy as np
    import soundfile as sf
    cache = pathlib.Path(tempfile.gettempdir()) / "ro2en_speed_bench"
    cache.mkdir(exist_ok=True)
    out = []
    for i, s in enumerate(sentences()):
        wav = cache / f"{i:02d}.wav"
        if not wav.exists():
            aiff = cache / f"{i:02d}.aiff"
            subprocess.run(["say", "-v", "Ioana", "-r", "170", "-o", str(aiff), s + " " + s], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(wav)], check=True)
        x = sf.read(wav, dtype="float32")[0]
        n = int(sec * 16000)
        out.append(np.pad(x, (0, max(0, n - len(x))))[:n])
    return out


def stt(name: str) -> dict:
    from engine import NemoSTT, ParakeetSTT
    t = time.perf_counter()
    fn = ParakeetSTT() if name == "parakeet-mlx" else NemoSTT(name)
    load = time.perf_counter() - t
    res = {}
    for L in (3, 6):
        xs = clips(L)
        fn(xs[0])
        ts = []
        for x in xs:
            t = time.perf_counter(); fn(x); ts.append(time.perf_counter() - t)
        res[f"clause{L}s"] = q(ts)
    return {"load_s": round(load, 1), **res}


def mt(spec: str) -> dict:
    from translate import Glossary, make_translator
    t = time.perf_counter()
    tr = make_translator(spec)
    gl = Glossary()
    tr.translate("Dumnezeu este bun.", "ro", "en", gl)
    load = time.perf_counter() - t
    ts = []
    for s in sentences():
        t = time.perf_counter(); tr.translate(s, "ro", "en", gl); ts.append(time.perf_counter() - t)
    return {"load_s": round(load, 1), "n": len(ts), **q(ts)}


if __name__ == "__main__":
    kind, name = sys.argv[1], sys.argv[2]
    r = stt(name) if kind == "stt" else mt(name)
    print(json.dumps({"machine": chip(), "host": platform.node(), "kind": kind, "model": name, **r}), flush=True)
