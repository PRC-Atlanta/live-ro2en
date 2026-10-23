"""Speed + simulated streaming latency for one local STT model (run in its venv, on a quiet
machine). Uses the same in-memory path the live engine uses where one exists.

  .venv-nemo/bin/python src/stt_latency.py jackrabbit-tdt-boost
Takes 20 Bible segments; measures (a) RTF on full segments and (b) STT time for 3 s and
6 s clause-sized chunks. Simulated streaming latency = pause wait (config min_pause_ms)
+ p90 STT time for a 6 s clause. Appends to results/stt_latency.csv."""
from __future__ import annotations

import csv
import pathlib
import statistics
import sys
import time

import numpy as np
import soundfile as sf
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "src" / "live"))


def make(name: str):
    """Callable taking a float32 16 kHz array."""
    import tempfile
    if name.startswith(("jackrabbit", "sped")):
        from engine import NemoSTT
        return NemoSTT(name), "nemo-mps-encoder"
    if name == "parakeet-mlx":
        from engine import ParakeetSTT
        return ParakeetSTT(), "mlx"
    import stt_run
    fn = stt_run.MODELS[name]()

    def via_file(x):
        with tempfile.NamedTemporaryFile(suffix=".wav") as f:
            sf.write(f.name, x, 16000, subtype="PCM_16")
            return fn(f.name)
    return via_file, "file-api"


def main(name: str) -> None:
    rows = list(csv.DictReader(open(ROOT / "work/bible/manifest.csv")))[::5][:20]
    xs = [sf.read(ROOT / "work/bible/segments" / f"{r['segment']}.wav", dtype="float32")[0] for r in rows]
    fn, path = make(name)
    fn(xs[0])                                            # warm-up
    t = time.perf_counter()
    for x in xs:
        fn(x)
    rtf = (time.perf_counter() - t) / sum(len(x) / 16000 for x in xs)
    res = {}
    for L in (3, 6):
        ts = []
        for x in xs:
            c = x[: L * 16000]
            t = time.perf_counter(); fn(c); ts.append(time.perf_counter() - t)
        ts.sort()
        res[L] = (statistics.median(ts), ts[int(0.9 * (len(ts) - 1))])
    pause = yaml.safe_load(open(ROOT / "config.yaml"))["live"]["min_pause_ms"] / 1000
    row = {"model": name, "runtime_path": path, "rtf": round(rtf, 4),
           "clause3s_med": round(res[3][0], 3), "clause6s_med": round(res[6][0], 3), "clause6s_p90": round(res[6][1], 3),
           "sim_stream_latency_s": round(pause + res[6][1], 3)}
    out = ROOT / "results" / "stt_latency.csv"
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(row)


if __name__ == "__main__":
    main(sys.argv[1])
