#!/usr/bin/env python3
"""Latency of bench runs, from their logs: recogniser and translator time per Romanian phrase
(median/p90) and, for a real-time replay (--speed 1), how long the first word of a phrase took
to reach the screen.

  .venv-mt/bin/python scripts/bench_latency.py RUN [RUN ...]      (names under logs/bench/runs/)

A replay stamps audio on the recording's clock, so first_word_s in the log is off by a constant;
it is taken out here (see D below). Faster replays share the machine with nothing in real time:
their recogniser and translator times are only a rough guide."""
import json, sys, glob, statistics as st
import numpy as np
for run in sys.argv[1:]:
    f = sorted(glob.glob(f"logs/bench/runs/{run}/services/*.jsonl"))[-1]
    info = json.load(open(f"logs/bench/runs/{run}/run.json"))
    ok = [json.loads(l) for l in open(f) if '"outcome": "ok"' in l]
    ro = [r for r in ok if r.get("lang") == "ro"]
    stt = [r["stt_s"] for r in ro if r.get("stt_s") is not None]
    mt = [r["mt_s"] for r in ro if r.get("mt_s") is not None]
    line = (f"{run:22s} {info.get('git'):9s} x{info.get('speed'):g}  {len(ro)} RO phrases | recogniser {st.median(stt):.2f}/{np.percentile(stt, 90):.2f} s"
            f" | translator {st.median(mt):.2f}/{np.percentile(mt, 90):.2f} s (median/p90)")
    if info.get("speed") == 1.0:
        # a replay stamps frames on the recording's clock: wall time runs a constant D ahead of it.
        # The shortest wait between a chunk's last voiced frame and its cut is the pause itself
        # (min_pause_ms 250 = 8 frames, 0.256 s), which gives D.
        D = min(r["vad_wait_s"] for r in ok if r.get("vad_wait_s") is not None) - 0.256
        fw = [r["first_word_s"] - D for r in ro if r.get("first_word_s") is not None]
        lat = [r["latency_s"] - D for r in ro if r.get("latency_s") is not None]
        line += (f" | first word median {st.median(fw):.2f} s, p90 {np.percentile(fw, 90):.2f} s, <= 6 s {sum(x <= 6 for x in fw) / len(fw) * 100:.1f}%"
                 f", <= 8 s {sum(x <= 8 for x in fw) / len(fw) * 100:.1f}% | after last word median {st.median(lat):.2f} s")
    print(line)
