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
a re-run after a change that does not touch the prompt costs nothing. --max-calls (default 400) caps the
paid API calls of one replay (cache hits are free): past it the replay is stopped and nothing is scored
(exit 4). Needs an API key (Keychain, or ANTHROPIC_API_KEY in .env); the key is never printed."""
from __future__ import annotations

import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def _paid_calls(path: pathlib.Path) -> int:
    """API calls paid for so far: the app writes one line per call to claude_usage.jsonl in its log
    folder (src/live/ai.py; a cache hit writes none)."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def replay(cmd: list[str], env: dict, usage: pathlib.Path, max_calls: int) -> tuple[int, int]:
    """Run one bench replay; stop it (the bench and the app it started, as a group) once it has paid
    for more than max_calls API calls (0 = no cap). (return code, calls paid in this replay)."""
    before = _paid_calls(usage)                    # a re-run of the same name appends to the same file
    p = subprocess.Popen(cmd, cwd=str(ROOT), env=env, start_new_session=True)
    while p.poll() is None:
        n = _paid_calls(usage) - before
        if max_calls and n > max_calls:
            os.killpg(p.pid, signal.SIGTERM)
            try:
                p.wait(30)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
            return -1, n
        time.sleep(1.0)
    return p.returncode, _paid_calls(usage) - before


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
    ap.add_argument("--max-calls", type=int, default=400,
                    help="stop a replay that pays for more API calls than this (cache hits are free; 0 = no cap)")
    a = ap.parse_args()
    for k in ("audio", "errors"):                                  # relative paths are the repo's, wherever this runs
        p = pathlib.Path(getattr(a, k))
        setattr(a, k, str(p if p.is_absolute() or p.exists() else ROOT / p))
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
            rc, paid = replay(cmd, env, bench.BENCH / "runs" / name / "claude_usage.jsonl", a.max_calls)
            if rc == -1:
                print(f"ai_regression: the {mode} replay was stopped after {paid} paid API calls "
                      f"(--max-calls {a.max_calls}); nothing scored", file=sys.stderr)
                return 4
            if rc:
                print(f"ai_regression: the {mode} replay failed ({rc})", file=sys.stderr)
                return 3
        res[mode] = bench.score(name, a.errors, quiet=True)
    loc, ai = res["local"], res["ai"]
    # wrong: still wrong, dropped (nothing shown there) or not recognised; "fixed?" is neither
    wrong = {m: sum(r[k] for k in bench.WRONG) for m, r in res.items()}
    used = {}
    for r in bench._records(pathlib.Path(ai["log"])):               # which engine really answered, in the AI run
        if r.get("outcome"):
            used[r.get("engine") or "local"] = used.get(r.get("engine") or "local", 0) + 1
    print(f"marked errors in range: {loc['errors_in_range']}")
    for m, r, tail in (("Local", loc, ""), ("AI   ", ai, f"; phrases by engine {used}")):
        print(f"  {m}: {wrong[m.strip().lower()]} still wrong (fixed {r['fixed']}, fixed? {r['fixed?']}, "
              f"of the wrong: dropped {r['dropped']}, missing {r['missing']}){tail}")
    worse = sorted(k for k, s in ai["per_error"].items() if s in bench.WRONG and loc["per_error"].get(k) == "fixed")
    better = sorted(k for k, s in ai["per_error"].items() if s == "fixed" and loc["per_error"].get(k) in bench.WRONG)
    print(f"  right with Local, wrong with AI: {', '.join(worse) or '-'}")
    print(f"  wrong with Local, right with AI: {', '.join(better) or '-'}")
    if wrong["ai"] > wrong["local"]:
        print("FAIL: AI leaves more of the marked errors wrong than Local")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
