#!/usr/bin/env python3
"""Offline check of the confidence gate and the language switching over service logs.

  scripts/gate_report.py LOG.jsonl [LOG.jsonl ...] [--min-real 0.6] [--max-frag 0.25] [--all] [--switching]

Gate: runs src/live/gate.check on every logged phrase's source text in the language it was
translated from, and lists what it would have dropped (time, reason, real-word share, source,
the caption that was shown), so a person can look for real speech among the drops.

Switching: replays langid.LangTracker over the phrases (old rule vs the new one) and lists
the phrases where the language differs from the one logged. Logs written before the gate
only keep the transcript that was translated (`source`), not both recognisers' (`ro`,
`multi`), so the replay uses the source as the probe and the vote; failure records (empty,
echo) carry both."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import gate     # noqa: E402
from langid import LangTracker     # noqa: E402


def hhmmss(t) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S") if isinstance(t, (int, float)) else "--:--:--"


def phrases(path: str) -> list[dict]:
    out = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if "outcome" in r and r.get("lang") in ("ro", "en"):
            out.append(r)
    return out


def gate_report(recs: list[dict], min_real: float, max_frag: float, show_all: bool) -> tuple[int, int, int]:
    lex = gate.lexicon()
    n = dropped = cuts = 0
    for r in recs:
        src = r.get("source")
        if not src:
            continue
        n += 1
        kept, why, cut = gate.screen(src, r["lang"], lex, min_real=min_real, max_frag=max_frag)
        f = gate.features(src, r["lang"], lex)
        tag = "DROP " + why if not kept else "CUT" if cut else "ok"
        if not kept or cut or show_all:
            dropped += not kept
            cuts += bool(kept and cut)
            print(f"  {hhmmss(r.get('t_start'))} {r['lang']} {tag:16} "
                  f"real {f['real']}/{f['words']} frag {f['frag']} foreign {f['foreign']}  "
                  f"{src!r} -> {(r.get('translation') or '')[:70]!r}" + (f"  [cut: {cut}]" if kept and cut else ""))
    return n, dropped, cuts


def probe_of(r: dict) -> tuple[str, str | None]:
    """(multilingual transcript, Romanian transcript or None when the log does not have it)."""
    if "multi" in r:                                  # failure records, and logs written after the gate
        return r.get("multi") or r.get("ro") or "", (r.get("ro") or "") if r.get("multi") else None
    src = r.get("source") or ""
    return src, src if r["lang"] == "ro" else None    # an RO phrase's source is the RO transcript


def switching_report(recs: list[dict]) -> None:
    """Replays the old rule and the new one; prints each switch of the new rule and where the
    two disagree, with the language that was logged live."""
    if not recs:
        return
    old = LangTracker("auto", home_words=0, min_vote_words=0)
    new = LangTracker("auto")
    prev_o = prev_n = "ro"
    same_o = 0
    for r in recs:
        multi, ro = probe_of(r)
        lo, ln = old.update(multi), new.update(multi, ro=ro)
        same_o += lo == r["lang"]
        if lo != ln or ln != prev_n or lo != prev_o:
            mark = "  " if lo == ln else "≠ "
            print(f"  {mark}{hhmmss(r.get('t_start'))} logged {r['lang']} old {lo} new {ln}  "
                  f"{(multi or '')[:80]!r}" + (f"  ro={ro[:40]!r}" if ro is not None and ro != multi else ""))
        prev_o, prev_n = lo, ln
    print(f"   switching: old rule reproduces the logged language on {same_o} of {len(recs)} phrases")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--min-real", type=float, default=0.6)
    ap.add_argument("--max-frag", type=float, default=0.25)
    ap.add_argument("--all", action="store_true", help="list every phrase, not only the drops")
    ap.add_argument("--switching", action="store_true", help="also replay the language switching")
    a = ap.parse_args()
    for log in a.logs:
        print(f"== {log}")
        n, d, c = gate_report(phrases(log), a.min_real, a.max_frag, a.all)
        print(f"   gate: {d} of {n} phrases dropped ({100 * d / max(n, 1):.1f}%), {c} more had a sentence cut")
        if a.switching:
            switching_report(phrases(log))
    return 0


if __name__ == "__main__":
    sys.exit(main())
