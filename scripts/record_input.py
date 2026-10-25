#!/usr/bin/env python3
"""Record the live input to a WAV file, alongside the app (macOS lets both read the device).

  .venv-nemo/bin/python scripts/record_input.py [--device "Dante Virtual Soundcard"] [--out DIR] [--rate 16000]

Records channel 1 (the channel the engine uses) at the device's own rate (or --rate: 16000 is
what the recogniser uses, ~2 MB a minute; macOS converts), 16-bit, before any
app gain, so any gain can be applied afterwards. Writes logs/recordings/<date>_<time>.wav and a
.json beside it with the wall-clock start time (to line the audio up with the service log's
t_start/t_end). Ctrl-C (or SIGTERM) stops and closes the file cleanly.

Church recordings stay on this Mac: logs/ is git-ignored and never bundled."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import queue
import signal
import sys
import time

import sounddevice as sd
import soundfile as sf

REPO = pathlib.Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="Dante Virtual Soundcard")
    ap.add_argument("--out", default=str(REPO / "logs" / "recordings"))
    ap.add_argument("--rate", type=int, default=0, help="sample rate (0 = the device's own)")
    a = ap.parse_args()
    info = sd.query_devices(a.device, "input")
    sr = a.rate or int(info["default_samplerate"])
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    wav = out / f"{stem}.wav"
    q: queue.Queue = queue.Queue()
    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))

    def cb(indata, frames, t, status):
        q.put(indata[:, 0].copy())

    t0 = time.time()
    (out / f"{stem}.json").write_text(json.dumps(
        {"device": info["name"], "samplerate": sr, "channel": 1, "start_wall": t0,
         "start_iso": dt.datetime.fromtimestamp(t0).isoformat(timespec="milliseconds"),
         "gain": "none (raw input, before the app's input gain)"}, indent=1))
    n = 0
    print(f"recording {info['name']} ch1 @ {sr} Hz -> {wav}  (Ctrl-C to stop)", flush=True)
    with sf.SoundFile(wav, "w", samplerate=sr, channels=1, subtype="PCM_16") as f, \
            sd.InputStream(device=a.device, channels=1, samplerate=sr, dtype="float32", callback=cb):
        last = time.time()
        try:
            while not stop["now"]:
                try:
                    block = q.get(timeout=0.5)
                except queue.Empty:
                    continue
                f.write(block)
                n += len(block)
                if time.time() - last > 60:
                    last = time.time()
                    print(f"{dt.datetime.now():%H:%M:%S} {n / sr / 60:.1f} min recorded", flush=True)
        except KeyboardInterrupt:
            pass
        while not q.empty():
            f.write(q.get())
    print(f"stopped: {wav} ({n / sr / 60:.1f} min)", flush=True)


if __name__ == "__main__":
    sys.exit(main())
