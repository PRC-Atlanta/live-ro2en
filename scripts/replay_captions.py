#!/usr/bin/env python3
"""Replay a service log's captions through the caption buffer on a simulated clock, with the
word-paced reveal off and on (docs/translation_ideas.md idea 21), and report how the screen
changes: how often, how many characters each change adds, and how much later the last word
of each phrase goes up than the phrase reached the screen (t_display). Standard library only.

  scripts/replay_captions.py LOG.jsonl [LOG.jsonl ...] [--chars 42] [--lines 2]
                             [--min-line-s 2.5] [--wps 3] [--pause-s 1.0]

Phrases are tidied as the pipeline does (tidy_clause: chunk periods wait for a sentence-length
pause) and added at their logged t_display with their logged audio_s; the pace timer is
ticked at every next_due(), as OutputMixin._pace does."""
from __future__ import annotations

import argparse
import json
import pathlib
import statistics as st
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from captions import RollingCaptions, tidy_clause      # noqa: E402


def phrases(path: pathlib.Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("outcome") == "ok" and r.get("translation") and r.get("t_display"):
            out.append(r)
    return sorted(out, key=lambda r: r["t_display"])


def replay(recs: list[dict], a, reveal: bool) -> dict:
    now = [0.0]
    c = RollingCaptions(a.chars, a.lines, min_line_s=a.min_line_s, clock=lambda: now[0],
                        reveal=reveal, reveal_wps=a.wps)
    words: list[str] = []                  # every word added, in order
    ends: list[tuple[int, float]] = []     # (word count at the end of each phrase, its t_display)
    shown_n, last_text, last_change = 0, "", None
    changes, gaps, delays, flushed = [], [], [], 0
    open_, prev_end = False, None

    def visible() -> int:
        return len(words) - len(c.pending) - sum(len(q.split()) for q in c.queue)

    def note():
        nonlocal shown_n, last_text, last_change
        if c.text == last_text:
            return
        n = visible()
        changes.append(sum(len(w) + 1 for w in words[shown_n:n]) - (1 if shown_n == 0 else 0))
        if last_change is not None:
            gaps.append(now[0] - last_change)
        shown_n, last_text, last_change = n, c.text, now[0]
        while ends and ends[0][0] <= n:
            delays.append(now[0] - ends.pop(0)[1])

    def tick(until: float):
        while (due := c.next_due()) is not None and due <= until:
            now[0] = max(now[0], due)
            c.advance()
            note()

    for r in recs:
        t = r["t_display"]
        tick(t)
        now[0] = t
        start = r.get("t_speech", r["t_start"])
        prev_open = open_
        continues = prev_open and prev_end is not None and start - prev_end < a.pause_s
        text, open_ = tidy_clause(r["translation"], continues, "ro" if r.get("lang") == "en" else "en",
                                  r.get("source", ""))
        prev_end = r["t_end"]
        if not text:
            continue
        flushed += bool(c.pending)
        c.add(text, "." if prev_open and not continues else "", audio_s=r.get("audio_s"))
        words.extend(text.split())
        ends.append((len(words), t))
        note()
    tick(float("inf"))
    return {"changes": changes, "gaps": gaps, "delays": delays, "phrases": len(recs),
            "flushed": flushed}


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="+", type=pathlib.Path)
    ap.add_argument("--chars", type=int, default=42)
    ap.add_argument("--lines", type=int, default=2)
    ap.add_argument("--min-line-s", type=float, default=2.5)
    ap.add_argument("--wps", type=float, default=3.0)
    ap.add_argument("--pause-s", type=float, default=1.0)
    a = ap.parse_args()
    print(f"box {a.lines}x{a.chars}, min_line_s {a.min_line_s}, reveal {a.wps} words/s")
    for reveal in (False, True):
        agg = {"changes": [], "gaps": [], "delays": [], "phrases": 0, "flushed": 0}
        for log in a.logs:
            res = replay(phrases(log), a, reveal)
            for k in agg:
                agg[k] += res[k]
        ch, gaps, d = agg["changes"], agg["gaps"], agg["delays"]
        one = sum(n > a.chars for n in ch) / max(1, len(ch))
        print(f"\nreveal {'on' if reveal else 'off'}: {agg['phrases']} phrases, {len(ch)} screen changes")
        print(f"  chars added per change: median {st.median(ch):.0f}, p90 {pct(ch, .9):.0f}, max {max(ch)}"
              f"  ({one:.0%} add more than a line's worth)")
        print(f"  time between changes:   median {st.median(gaps):.1f} s, p10 {pct(gaps, .1):.1f} s,"
              f" p90 {pct(gaps, .9):.1f} s  (gaps over 2 s: {sum(g > 2 for g in gaps) / len(gaps):.0%})")
        print(f"  last word after t_display: median {st.median(d):.2f} s, p90 {pct(d, .9):.2f} s, max {max(d):.2f} s")
        if reveal:
            print(f"  phrases whose reveal was cut short by the next phrase: {agg['flushed']}"
                  f" ({agg['flushed'] / max(1, agg['phrases']):.0%})")


if __name__ == "__main__":
    main()
