#!/usr/bin/env python3
"""Talk to a running Live RO2EN app and read its service logs: one command per routine check.
Standard library only (any python3).

  scripts/livectl.py url                     the control page of the running app (or "not running")
  scripts/livectl.py status                  one screen: phase, profile, language, input, level, gain,
                                             pace, warnings, what is on screen, the last phrases
  scripts/livectl.py watch [SECONDS] [N]     a status line every SECONDS (default 30), N times (default 10)
  scripts/livectl.py post ACTION [JSON]      POST /api/ACTION, e.g. post profile '{"profile":"fast"}'
  scripts/livectl.py set KEY VALUE           change a setting, e.g. set live.input_gain_db 22
  scripts/livectl.py get PATH                GET /api/PATH (json), e.g. get settings
  scripts/livectl.py logs                    the service logs, newest last (repo and installed app)
  scripts/livectl.py summary [LOG]           analysis of a service log (default: the newest)
  scripts/livectl.py transcript [LOG] [OUT]  Romanian | English side by side, one phrase per pair
  scripts/livectl.py monitor [SECONDS]       event stream for Claude's Monitor tool: every SECONDS
                                             (default 60) the new phrases RO|EN; at once: clipping,
                                             behind, English detected, slow phrase, phase change, exit.
                                             Resumes where the last run stopped (re-arm safe).

The app only accepts state-changing calls with a JSON content type (or its own page's origin):
this script sends that. Repo runs listen on 8765; the installed app picks a free port each launch,
found here from the listening Python process."""
from __future__ import annotations

import json
import pathlib
import statistics as st
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = pathlib.Path(__file__).resolve().parents[1]
LOG_DIRS = [REPO / "logs" / "services", pathlib.Path.home() / "Library" / "Logs" / "Live RO2EN" / "services"]


# --- the running app ------------------------------------------------------------------------
def _ok(base: str) -> bool:
    try:
        with urllib.request.urlopen(base + "/api/health", timeout=1.5) as r:
            return r.status == 200
    except Exception:                                     # noqa: BLE001
        return False


def find_url() -> str | None:
    if _ok("http://127.0.0.1:8765"):
        return "http://127.0.0.1:8765"
    try:                                                  # the installed app: a free port per launch
        out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN", "-a", "-c", "Python", "-c", "python"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:                                     # noqa: BLE001
        return None
    for line in out.splitlines():
        if "127.0.0.1:" in line:
            port = line.rsplit("127.0.0.1:", 1)[1].split()[0]
            if port.isdigit() and _ok(f"http://127.0.0.1:{port}"):
                return f"http://127.0.0.1:{port}"
    return None


def call(method: str, path: str, body: dict | None = None):
    base = find_url()
    if not base:
        sys.exit("Live RO2EN is not running.")
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"{base}/api/{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        try:
            return {"http_error": e.code, **json.loads(e.read())}
        except Exception:                                 # noqa: BLE001
            return {"http_error": e.code}


def _db(x):
    return "–" if x is None else f"{x:.0f} dB"


def status_text(s: dict, short: bool = False) -> str:
    a = s.get("audio") or {}
    pace = s.get("pace") or {}
    back = s.get("backlog") or {}
    line = (f"{time.strftime('%H:%M:%S')} {s.get('phase')} · {s.get('profile')} · mode {s.get('mode')} "
            f"(now {s.get('lang')}) · in {_db(a.get('rms_db'))} peak {_db(a.get('peak_db'))} "
            f"gain {a.get('gain_db', 0):+.0f} · speech {'yes' if a.get('speech') else 'no'} · "
            f"pace {pace.get('state')}{'' if not pace.get('behind_s') else ' ' + str(pace['behind_s']) + ' s'} · "
            f"queue {back.get('n', 0)}")
    if short:
        return line
    out = [line, f"status: {s.get('status')}", f"input: {a.get('device')}  audio ok: {a.get('ok')}  engine: {a.get('engine')}"]
    pp = s.get("pp") or {}
    out.append(f"ProPresenter: {'on' if pp.get('enabled') else 'off'}{', connected' if pp.get('connected') else ''}"
               f"  hidden: {s.get('paused')}  held: {s.get('held')}")
    for w in (s.get("warnings") or [])[-5:]:
        out.append(f"warning: {w}")
    out.append("on screen: " + repr(s.get("screen") or ""))
    for ln in (s.get("lines") or [])[-4:]:
        out.append(f"  [{ln.get('lang')} {ln.get('lat')} s] {ln.get('src')}\n      -> {ln.get('dst')}")
    return "\n".join(out)


# --- service logs --------------------------------------------------------------------------
def logs() -> list[pathlib.Path]:
    found = [p for d in LOG_DIRS if d.is_dir() for p in d.glob("20*.jsonl")]
    return sorted(found, key=lambda p: p.name)


def pick(arg: str | None) -> pathlib.Path:
    if arg:
        p = pathlib.Path(arg)
        if not p.exists():
            p = next((q for q in logs() if q.name.startswith(arg)), p)
        return p
    if not logs():
        sys.exit("no service logs yet")
    return logs()[-1]


def read(p: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in p.open() if line.strip()]


def _q(vals, q):
    vals = sorted(v for v in vals if v is not None)
    return vals[min(len(vals) - 1, int(q * len(vals)))] if vals else None


def summary(p: pathlib.Path) -> str:
    rs = read(p)
    ph = [r for r in rs if r.get("outcome")]
    ok = [r for r in ph if r["outcome"] == "ok"]
    out = [f"{p}", f"phrases: {len(ph)}  " + "  ".join(
        f"{k}={sum(r['outcome'] == k for r in ph)}" for k in sorted({r['outcome'] for r in ph}))]
    if ph:
        out.append(f"from {ph[0]['time'][11:19]} to {ph[-1]['time'][11:19]}")
    for prof in sorted({r.get("profile") for r in ok}):
        sel = [r for r in ok if r.get("profile") == prof]
        f = lambda k: f"{st.median(r[k] for r in sel if r.get(k) is not None):.1f}/{_q([r.get(k) for r in sel], .9):.1f}"
        words = [len((r.get("translation") or "").split()) for r in sel]
        out.append(f"{prof:8s} n={len(sel):4d}  median/p90 s: audio {f('audio_s')}  stt {f('stt_s')}  mt {f('mt_s')}  "
                   f"first word {f('first_word_s')}  latency {f('latency_s')}  | words {st.median(words):.0f}, "
                   f"<5 words {sum(w < 5 for w in words) / len(words):.0%}")
    lv = [r for r in ok if isinstance(r.get("level"), dict) and r["level"].get("speech_db") is not None]
    if lv:
        out.append(f"input level (speech) median {st.median(r['level']['speech_db'] for r in lv):.0f} dBFS, "
                   f"peak max {max(r['level']['peak_db'] for r in lv):.0f}; gain used: "
                   + ", ".join(f"{g:+.0f}" for g in sorted({r.get('gain_db', 0) for r in lv})))
    for r in rs:
        if r.get("event"):
            out.append(f"event {r['time'][11:19]} " + json.dumps({k: v for k, v in r.items() if k != "time"},
                                                                ensure_ascii=False)[:160])
    flips = [r for r in ok if r.get("lang") == "en"]
    if flips and len(flips) < len(ok) / 2:
        out.append(f"phrases taken as English ({len(flips)}), check for Romanian on the English screen:")
        out += [f"  {r['time'][11:19]} {r['source'][:60]!r} -> {r['translation'][:50]!r}" for r in flips[:10]]
    slow = [r for r in ok if (r.get("mt_s") or 0) > 8 or (r.get("first_word_s") or 0) > 20 or (r.get("stt_s") or 0) > 3]
    if slow:
        out.append(f"slow phrases ({len(slow)}):")
        out += [f"  {r['time'][11:19]} {r.get('profile')} stt {r.get('stt_s', 0):.1f} mt {r.get('mt_s', 0):.1f} "
                f"first word {r.get('first_word_s', 0):.0f} s caught_up {r.get('caught_up')}" for r in slow[:12]]
    empty = [r for r in ph if r["outcome"] != "ok"]
    if empty:
        out.append("not shown: " + ", ".join(f"{r['time'][11:19]} {r['outcome']}" for r in empty[:15]))
    return "\n".join(out)


def transcript(p: pathlib.Path) -> str:
    rows = []
    for i, r in enumerate(r for r in read(p) if r.get("outcome") == "ok"):
        tag = ("Q" if r.get("profile") == "quality" else "F") + ("*" if r.get("lang") == "en" else " ")
        rows.append(f"{i:03d} {r['time'][11:19]} {tag} RO: {r['source']}\n                EN: {r['translation']}")
    return "\n".join(rows) + "\n"


def _pos_file(log: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(tempfile.gettempdir()) / f"livectl-monitor-{log.stem}.pos"


def monitor(every: float = 60.0) -> None:
    """Print one block per event, flushed; exit when the app is gone. Phrases are read from the
    newest service log (a new Start makes a new log: followed), from where the previous monitor
    run stopped, so a re-armed monitor neither repeats nor skips phrases."""
    def say(text):
        print(text, flush=True)
    log, seen, pending, prev_lang = None, 0, [], None
    last_digest, last_phase, clip_n, alerted = time.time(), None, 0, set()
    gone = 0
    while True:
        try:
            s = call("GET", "state") if find_url() else None
        except SystemExit:
            s = None
        if s is None:
            gone += 1
            if gone >= 3:
                if pending:
                    say(_digest(pending))
                say("APP STOPPED: the control server is gone")
                return
            time.sleep(2)
            continue
        gone = 0
        phase = s.get("phase")
        if phase != last_phase:
            if last_phase is not None:
                say(f"PHASE {last_phase} -> {phase} ({s.get('status')})")
            last_phase = phase
        a = s.get("audio") or {}
        clip_n = clip_n + 1 if (a.get("peak_db") or -120) >= -1 else 0
        if clip_n == 5:                                     # ~10 s of peaks at full scale
            say(f"ALERT clipping: peak {a.get('peak_db')} dB, level {a.get('rms_db')} dB, gain {a.get('gain_db')} dB")
        pace = s.get("pace") or {}
        if pace.get("state") == "behind" and "behind" not in alerted:
            alerted.add("behind")
            say(f"ALERT captions behind by {pace.get('behind_s')} s (queue {(s.get('backlog') or {}).get('n')})")
        elif pace.get("state") == "ok":
            alerted.discard("behind")
        for w in s.get("warnings") or []:
            if w not in alerted:
                alerted.add(w)
                say(f"WARNING {w}")
        newest = logs()[-1] if logs() else None
        if newest is not None and newest != log:
            log = newest
            try:
                seen = int(_pos_file(log).read_text())
            except (OSError, ValueError):
                seen = 0 if time.time() - log.stat().st_mtime < 5 else sum(1 for _ in log.open())
        if log is not None:
            rows = log.open().readlines()
            for line in rows[seen:]:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("outcome"):
                    pending.append(r)
                    if r["outcome"] == "ok" and r.get("lang") != prev_lang and prev_lang and s.get("mode") == "auto":
                        say(f"ALERT language switched {prev_lang} -> {r.get('lang')}: "
                            f"{r.get('source', '')[:70]!r} -> {r.get('translation', '')[:60]!r}")
                    if r["outcome"] == "ok":
                        prev_lang = r.get("lang")
                    if (r.get("mt_s") or 0) > 8 or (r.get("first_word_s") or 0) > 20:
                        say(f"ALERT slow phrase {r['time'][11:19]}: mt {r.get('mt_s', 0):.1f} s, "
                            f"first word {r.get('first_word_s', 0):.0f} s ({r.get('profile')})")
                elif r.get("event") in ("profile", "backlog"):
                    say("EVENT " + json.dumps({k: v for k, v in r.items() if k != "time"}, ensure_ascii=False)[:200])
            seen = len(rows)
            _pos_file(log).write_text(str(seen))
        if pending and time.time() - last_digest >= every:
            say(_digest(pending))
            pending, last_digest = [], time.time()
        time.sleep(2)


def _said(r: dict) -> str:
    """When the phrase was spoken, HH:MM:SS: the time `clip_recording.py mark --at` wants."""
    t = r.get("t_speech") or r.get("t_start")
    return time.strftime("%H:%M:%S", time.localtime(t)) if isinstance(t, (int, float)) and t > 1e9 else "--:--:--"


def _digest(rows: list[dict]) -> str:
    ok = [r for r in rows if r["outcome"] == "ok"]
    head = (f"PHRASES {rows[0]['time'][11:19]}-{rows[-1]['time'][11:19]}: {len(ok)} shown, "
            f"{len(rows) - len(ok)} not")
    if ok:
        head += (f"; first word median {st.median(r['first_word_s'] for r in ok):.1f} s, "
                 f"mt median {st.median(r['mt_s'] for r in ok):.1f} s")
        lv = [r["level"]["speech_db"] for r in ok if isinstance(r.get("level"), dict) and r["level"].get("speech_db") is not None]
        if lv:
            head += f", speech level {st.median(lv):.0f} dB"
    body = [f"  {_said(r)} {'Q' if r.get('profile') == 'quality' else 'F'}{r.get('lang', '')} RO: {r.get('source', '')}\n"
            f"                 EN: {r.get('translation', '')}" for r in ok]
    body += [f"  {_said(r)} -- {r['outcome']}: {(r.get('ro') or r.get('multi') or '')[:60]!r}"
             for r in rows if r["outcome"] != "ok"]
    return "\n".join([head] + body)


def main(argv: list[str]) -> None:
    cmd, args = (argv[0], argv[1:]) if argv else ("status", [])
    if cmd == "url":
        print(find_url() or "not running")
    elif cmd == "status":
        print(status_text(call("GET", "state")))
    elif cmd == "watch":
        every, n = float(args[0]) if args else 30.0, int(args[1]) if len(args) > 1 else 10
        for i in range(n):
            print(status_text(call("GET", "state"), short=True), flush=True)
            if i < n - 1:
                time.sleep(every)
    elif cmd == "post":
        print(json.dumps(call("POST", args[0], json.loads(args[1]) if len(args) > 1 else {}), ensure_ascii=False)[:2000])
    elif cmd == "set":
        try:
            val = json.loads(args[1])
        except json.JSONDecodeError:
            val = args[1]
        print(json.dumps(call("PUT", "settings", {"values": {args[0]: val}}), ensure_ascii=False)[:2000])
    elif cmd == "get":
        print(json.dumps(call("GET", args[0]), ensure_ascii=False, indent=1)[:6000])
    elif cmd == "monitor":
        monitor(float(args[0]) if args else 60.0)
    elif cmd == "logs":
        for p in logs()[-15:]:
            print(f"{p}  {p.stat().st_size // 1024} KB")
    elif cmd == "summary":
        print(summary(pick(args[0] if args else None)))
    elif cmd == "transcript":
        p = pick(args[0] if args else None)
        text = transcript(p)
        if len(args) > 1:
            pathlib.Path(args[1]).write_text(text)
            print(f"{args[1]}: {text.count(chr(10)) // 2} phrases")
        else:
            print(text, end="")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
