#!/usr/bin/env python3
"""The AI engine against a service's list of marked errors (docs/live_voice_plan.md §15): replay the
recording once with the local models and once with AI chosen, score both against the same error list
(scripts/bench.py score), and fail when AI leaves more of the marked errors wrong than Local does.

  .venv-mt/bin/python scripts/ai_regression.py --audio logs/recordings/2026-11-08_191355.wav \\
      --errors logs/bench/errors_2026-11-08.json [--from HH:MM:SS --to HH:MM:SS] [--gain DB] [--speed 2]

Written for the Sunday evening 2026-11-08 clips (39 marked errors; PR #6 left 11 wrong with the local
models). The recording and its error list are kept on the church's Mac only (logs/ is not in git), so
this exits with 2, saying what is missing, where they are not present.

AI answers are kept in logs/bench/ai_cache.jsonl (LIVE_AI_CACHE): the same input is paid for once, so
a re-run after a change that does not touch the prompt costs nothing. Needs an API key (Keychain, or
ANTHROPIC_API_KEY in .env); the key is never printed."""
from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--audio", default="logs/recordings/2026-11-08_191355.wav")
    ap.add_argument("--errors", default="logs/bench/errors_2026-11-08.json")
    ap.add_argument("--from", dest="t_from")
    ap.add_argument("--to", dest="t_to")
    ap.add_argument("--gain", default="0")
    ap.add_argument("--speed", default="2")
    ap.add_argument("--name", default="aireg")
    ap.add_argument("--reuse", action="store_true", help="score the runs of this name that are already there")
    a = ap.parse_args()
    missing = [p for p in (a.audio, a.errors) if not (ROOT / p).exists() and not pathlib.Path(p).exists()]
    if missing:
        print("ai_regression: not on this Mac: " + ", ".join(missing), file=sys.stderr)
        return 2
    import bench                                                   # noqa: PLC0415  (scripts/bench.py)
    res = {}
    for mode, extra in (("local", []), ("ai", ["--set", "ai.enabled=true"])):
        name = f"{a.name}-{mode}"
        if not (a.reuse and (bench.BENCH / "runs" / name / "run.json").exists()):
            cmd = [sys.executable, str(ROOT / "scripts" / "bench.py"), "run", a.audio, "--gain", a.gain,
                   "--speed", a.speed, "--name", name, *extra]
            for flag, v in (("--from", a.t_from), ("--to", a.t_to)):
                if v:
                    cmd += [flag, v]
            env = {**os.environ, "LIVE_AI_CACHE": str(ROOT / "logs" / "bench" / "ai_cache.jsonl")}
            rc = subprocess.run(cmd, cwd=str(ROOT), env=env).returncode
            if rc:
                print(f"ai_regression: the {mode} replay failed ({rc})", file=sys.stderr)
                return 3
        res[mode] = bench.score(name, a.errors, quiet=True)
    loc, ai = res["local"], res["ai"]
    wrong = {m: r["still"] + r["missing"] for m, r in res.items()}
    used = {}
    for r in bench._records(pathlib.Path(ai["log"])):               # which engine really answered, in the AI run
        if r.get("outcome"):
            used[r.get("engine") or "local"] = used.get(r.get("engine") or "local", 0) + 1
    print(f"marked errors in range: {loc['errors_in_range']}")
    print(f"  Local: {wrong['local']} still wrong (fixed {loc['fixed']})")
    print(f"  AI   : {wrong['ai']} still wrong (fixed {ai['fixed']}); phrases by engine {used}")
    worse = sorted(k for k, s in ai["per_error"].items() if not s.startswith("fixed") and loc["per_error"].get(k, "").startswith("fixed"))
    better = sorted(k for k, s in ai["per_error"].items() if s.startswith("fixed") and not loc["per_error"].get(k, "").startswith("fixed"))
    print(f"  right with Local, wrong with AI: {', '.join(worse) or '-'}")
    print(f"  wrong with Local, right with AI: {', '.join(better) or '-'}")
    if wrong["ai"] > wrong["local"]:
        print("FAIL: AI leaves more of the marked errors wrong than Local")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
