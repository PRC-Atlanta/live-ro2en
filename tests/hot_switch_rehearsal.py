"""S4 acceptance rehearsal (heavy: loads the speech models and both translators; not run by
pytest). Plays the mixed RO/EN rehearsal file through the full pipeline into the mock
ProPresenter, like `e2e.py --mixed`, and switches profile twice while it plays.

  .venv-mt/bin/python tests/hot_switch_rehearsal.py [--first quality] [--at 40 100] [--strict]

Passes when: one engine process for the whole run (no stop/ready pair), every switch was hot
(engine cfg sent, no restart), the caption gap across each switch is at most --max-gap s, and
the e2e gate (eof, failed chunks, first word within 6 s) passes."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import e2e                                    # noqa: E402
from app import Live                          # noqa: E402
from mock_propresenter import serve           # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--first", default="quality")
    ap.add_argument("--at", type=float, nargs="+", default=[40.0, 100.0], help="switch times (s into the audio)")
    ap.add_argument("--max-gap", type=float, default=3.0)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    audio = str(e2e.make_mixed(ROOT / "work" / "e2e" / "mixed.wav"))
    cfg["live"].update(profile=a.first, language_mode="auto")
    port = e2e.free_port()
    srv, mock = serve(port)
    cfg["propresenter"]["port"] = port
    live = Live(cfg, types.SimpleNamespace(file=audio))
    spawned = []
    orig = live._start_engine
    live._start_engine = lambda device=None: (spawned.append(time.time()), orig(device))[1]
    live.load_translator()
    live.start()
    t0 = time.time()
    profiles = list(cfg["profiles"])
    switches = []

    def switcher():
        for at in a.at:
            while time.time() - t0 < at and not live.eof.is_set():
                time.sleep(0.05)
            if live.eof.is_set():
                return
            to = next(p for p in profiles if p != live.profile)
            ts = time.time()
            live.set_profile(to)
            switches.append({"to": to, "t": ts, "call_s": round(time.time() - ts, 2)})
    th = threading.Thread(target=switcher, daemon=True)
    th.start()
    import soundfile as sf
    deadline = time.time() + sf.info(audio).duration + 180
    while not live.eof.is_set() and time.time() < deadline:
        time.sleep(0.2)
    time.sleep(cfg["live"].get("hold_s", 1.2) + 2.0)
    th.join(5)
    live.stop()
    srv.shutdown()
    rows = [json.loads(l) for l in open(live.log_path)]
    recs = [r for r in rows if r.get("outcome") == "ok" and "event" not in r]
    outcomes: dict = {}
    for r in rows:
        if "outcome" in r and "event" not in r:
            outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
    trig = sorted(e["t"] for e in mock.events if e["path"].endswith("/trigger") and "t" in e) or \
        sorted(r["t_display"] for r in recs)
    gaps = []
    for s in switches:
        before = [t for t in trig if t <= s["t"]]
        after = [t for t in trig if t > s["t"]]
        s["gap_s"] = round(after[0] - before[-1], 2) if before and after else None
        gaps.append(s["gap_s"])
    prof_ev = [r for r in rows if r.get("event") == "profile"]
    fw = sorted(r["first_word_s"] for r in recs)
    summary = {"clauses": len(recs), "failed_chunks": sum(v for k, v in outcomes.items() if k != "ok"),
               "outcomes": outcomes, "eof_reached": live.eof.is_set(),
               "engine_exit": live.engine.returncode if live.engine else None,
               "frac_first_word_le_6s": round(sum(x <= 6 for x in fw) / max(len(fw), 1), 3),
               "engines_spawned": len(spawned), "switches": switches,
               "profile_events": prof_ev, "profiles_seen": sorted({r["profile"] for r in recs}),
               "log": str(live.log_path)}
    gate = e2e.gate(summary, recs, types.SimpleNamespace(max_failed_frac=0.15, min_first_word_6s=0.8, ref_text=None))
    reasons = list(gate["fail_reasons"])
    if len(spawned) != 1:
        reasons.append(f"{len(spawned)} engine processes (a switch restarted the engine)")
    if len(switches) != len(a.at):
        reasons.append(f"only {len(switches)} of {len(a.at)} switches happened")
    if not all(e.get("hot") and e.get("engine_cfg") for e in prof_ev):
        reasons.append("a switch was not hot")
    for g in gaps:
        if g is None or g > a.max_gap:
            reasons.append(f"caption gap {g} s across a switch (> {a.max_gap} s)")
    summary.update(passed=not reasons, fail_reasons=reasons)
    print(json.dumps(summary, ensure_ascii=False, default=str))
    if a.strict and reasons:
        sys.exit(1)


if __name__ == "__main__":
    main()
