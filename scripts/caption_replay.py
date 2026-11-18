#!/usr/bin/env python3
"""Replay a run's translations through the caption box on a simulated clock: no models, no
audio, done in a second. Each shown phrase is added at the moment it was shown in the run, the
box's own timer is followed exactly, and every change is written as a `screen` event (the same
shape as output.py's <log>.screen.ndjson), so a pacing change can be tried on a saved run.

    python scripts/caption_replay.py logs/bench/runs/NAME [--pages] [--set read_wps=2.4 ...] -o OUT.ndjson

Phrases are added as whole sentences (live.translate_sentences): each on a line of its own, a
long one in its comma parts. The run's own screen log (if any) is the check that the replay
matches what the app did.
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import statistics
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.captions import RollingCaptions, comma_parts  # noqa: E402


class Clock:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def phrases(run: str) -> list[dict]:
    log = [p for p in glob.glob(run + "/services/*.jsonl")][0]
    recs = [json.loads(l) for l in open(log) if l.strip()]
    by_id = {r.get("id"): r for r in recs if r.get("outcome") and r.get("t_end")}
    gaps = [s["spk_end"] - by_id[s["chunks"][-1]]["t_end"] for s in recs
            if s.get("event") == "sentence" and s.get("spk_end") and s.get("chunks") and s["chunks"][-1] in by_id]
    shift = statistics.median(gaps) if gaps else 0.0
    out = [{"t": r["t_display"], "text": r["translation"], "spoken": r["t_end"] + shift}
           for r in recs if r.get("outcome") == "ok" and r.get("translation") and r.get("t_display")]
    return sorted(out, key=lambda p: p["t"])


def replay(ph: list[dict], max_chars=42, max_lines=2, **kw) -> list[dict]:
    clock = Clock(ph[0]["t"] - 1)
    caps = RollingCaptions(max_chars, max_lines, clock=clock, reveal=True, **kw)
    events, last = [], None

    def log():
        nonlocal last
        if caps.lines != last:
            new = [ln for ln in caps.lines if ln not in (last or [])]
            events.append({"event": "screen", "lines": list(caps.lines), "new": new,
                           "t": round(clock.t, 3), "waiting": len(caps.queue)})
            last = list(caps.lines)

    def run_until(t: float):
        while True:
            due = caps.next_due()
            if due is None or due > t:
                break
            clock.t = max(clock.t, due)
            if caps.advance():
                log()
            elif caps.next_due() is not None and caps.next_due() <= clock.t + 1e-9:
                clock.t += 0.05            # nothing moved at its due time: step on
        clock.t = max(clock.t, t)

    for p in ph:
        run_until(p["t"])
        text = " ".join(p["text"].split())
        caps.add(text, audio_s=None, spoken_ago=max(0.0, p["t"] - p["spoken"]),
                 parts=comma_parts(text, max_chars * max_lines), fresh=True)
        log()
    run_until(ph[-1]["t"] + 120)
    return events


def stats(events: list[dict], label: str):
    t = [e["t"] for e in events]
    g = [b - a for a, b in zip(t, t[1:])]
    mins = (t[-1] - t[0]) / 60
    print(f"{label}: {len(events)} changes ({len(events) / mins:.1f}/min), gap median {statistics.median(g):.1f} s, "
          f"under 1.5 s {sum(x < 1.5 for x in g)}, most waiting {max(e['waiting'] for e in events)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("--pages", action="store_true", help="two-line pages instead of rolling lines")
    ap.add_argument("--set", action="append", default=[], help="a RollingCaptions setting, e.g. read_wps=2.4")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    kw = {"read_wps": 2.7, "min_read_s": 1.2, "max_lag_s": 20.0, "catchup_wps": 3.3}
    for s in a.set:
        k, v = s.split("=", 1)
        kw[k] = float(v)
    if a.pages:
        kw["pages"] = True
    ev = replay(phrases(a.run), **kw)
    side = glob.glob(a.run + "/services/*.screen.ndjson")
    if side:
        stats([json.loads(l) for l in open(side[0]) if l.strip()], "the run    ")
    stats(ev, "this replay")
    if a.out:
        pathlib.Path(a.out).write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in ev))
        print("wrote", a.out)


if __name__ == "__main__":
    main()
