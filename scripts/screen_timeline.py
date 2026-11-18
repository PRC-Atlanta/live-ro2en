#!/usr/bin/env python3
"""A service or replay log as a timeline: for each phrase, when it was heard, translated, each line
that went up on the screen, and what Live Voice said.

    python scripts/screen_timeline.py LOG.jsonl [-o OUT.txt]
    python scripts/screen_timeline.py logs/bench/runs/NAME        # a bench run folder

Times are the position in the recording (m:ss from its start; a bench run's run.json gives the
start), and each step's "+s" is seconds after the speaker finished that phrase. Screen lines need
the `screen` events in LOG.screen.ndjson beside it (output.py `_log_screen`); a log from before
them shows no screen lines.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics
import sys


def _records(path: pathlib.Path) -> list[dict]:
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def _norm(s: str) -> list[str]:
    return re.findall(r"\w+", s.lower())


def _contains(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def _clock(s: float) -> str:
    s = max(0.0, s)
    return f"{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:04.1f}" if s >= 3600 else \
        f"{int(s // 60)}:{s % 60:04.1f}"


def find_log(arg: str) -> tuple[pathlib.Path, float | None]:
    p = pathlib.Path(arg)
    if p.is_dir():
        logs = sorted((p / "services").glob("*.jsonl")) or sorted(p.glob("*.jsonl"))
        if not logs:
            sys.exit(f"no .jsonl log in {p}")
        p = logs[-1]
    t0 = None
    run = p.parent.parent / "run.json"
    if run.exists():
        t0 = json.loads(run.read_text()).get("segment_start_wall")
    return p, t0


def build(recs: list[dict], t0: float | None) -> list[str]:
    clauses = [r for r in recs if r.get("outcome") and r.get("t_end")]
    if not clauses:
        return ["(no phrases in this log)"]
    # In a bench replay speech times (t_start, t_end) are on the recording's clock and everything
    # else on the wall clock; a sentence record has its speech end on the wall clock (spk_end),
    # which gives the gap between the two. A live log has one clock: the gap is 0.
    by_id = {r.get("id"): r for r in clauses}
    gaps = [s["spk_end"] - by_id[s["chunks"][-1]]["t_end"] for s in recs
            if s.get("event") == "sentence" and s.get("spk_end") and s.get("chunks")
            and s["chunks"][-1] in by_id]
    shift = statistics.median(gaps) if gaps else 0.0
    t0 = t0 if t0 is not None else min(r.get("t_start", r["t_end"]) for r in clauses)

    sentences = {r["sid"]: r for r in recs if r.get("event") == "sentence"}
    by_chunk = {c: s for s in sentences.values() for c in s.get("chunks") or []}
    voice: dict[int, dict] = {}
    for r in recs:
        if r.get("event") == "voice_item":
            for sid in r.get("sids") or []:
                voice.setdefault(sid, r)
    said: dict[int, str] = {}
    for r in recs:
        if r.get("event") == "voice_summary" and r.get("ok") and r.get("text"):
            for sid in r.get("sids") or []:
                said[sid] = r["text"]

    # Each new screen line goes to the latest phrase shown by then whose translation holds the
    # line's last words (a line can end one sentence and start the next: it counts for the next),
    # else its first words, else the latest phrase shown by then.
    rows = [{"c": c, "screen": []} for c in clauses]
    shown = lambda row, t: row["c"].get("t_display") and row["c"]["t_display"] <= t + 0.05
    for r in recs:
        if r.get("event") != "screen":
            continue
        t = r.get("t")
        for line in r.get("new") or []:
            words = _norm(line)
            if not words:
                continue
            pick = None
            for probe in (words[-2:], words[:3]):
                pick = next((row for row in reversed(rows) if shown(row, t)
                             and _contains(_norm(row["c"].get("translation") or ""), probe)), None)
                if pick:
                    break
            pick = pick or next((row for row in reversed(rows) if shown(row, t)), rows[0])
            pick["screen"].append((t, line, r.get("waiting", 0)))
    for row in rows:
        row["screen"].sort(key=lambda x: x[0])

    out = [f"{'when':>9}  {'+s':>6}  step", "-" * 100]
    for row in rows:
        c = row["c"]
        end = c["t_end"]
        def at(wall: float) -> str:
            return f"{wall - shift - end:+6.1f}"
        out.append("")
        out.append(f"{_clock(c.get('t_start', end) - t0):>9}  {'':>6}  HEARD       {c.get('source', '')}"
                   + ("" if c.get("outcome") == "ok" else f"   [{c['outcome']}]"))
        if c.get("translation"):
            out.append(f"{_clock(c['t_display'] - shift - t0):>9}  {at(c['t_display'])}  TRANSLATED  "
                       f"{c['translation']}")
        for t, line, waiting in row["screen"]:
            out.append(f"{_clock(t - shift - t0):>9}  {at(t)}  SCREEN      {line}"
                       + (f"   ({waiting} line{'s' if waiting > 1 else ''} waiting)" if waiting else ""))
        s = by_chunk.get(c.get("id"))
        v = voice.get(s["sid"]) if s else None
        if v and s and c.get("id") == (s.get("chunks") or [None])[-1]:
            text = said.get(s["sid"]) or s.get("text") or ""
            tag = "VOICE (summary)" if s["sid"] in said else "VOICE      "
            out.append(f"{_clock(v['play_start'] - shift - t0):>9}  {at(v['play_start'])}  {tag} {text}"
                       + ("   [cut]" if v.get("cut") else ""))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("log", help="a services .jsonl log, or a bench run folder")
    ap.add_argument("-o", "--out", help="write here (default: print)")
    a = ap.parse_args()
    path, t0 = find_log(a.log)
    side = path.with_name(path.stem + ".screen.ndjson")
    recs = _records(path) + (_records(side) if side.exists() else [])
    text = "\n".join(build(recs, t0)) + "\n"
    if a.out:
        pathlib.Path(a.out).write_text(text)
        print(f"wrote {a.out}")
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
