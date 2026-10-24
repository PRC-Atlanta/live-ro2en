#!/usr/bin/env python3
"""Test bench: replay a recorded service through the real app and score it against a list of
known errors, so each translation change is measured on the same material.

  scripts/bench.py snapshot                       copy the newest recording (even one still being
                                                  written) to logs/bench/audio/
  scripts/bench.py run AUDIO [options]            replay it through engine + pipeline, headless
      --from HH:MM:SS --to HH:MM:SS                 a stretch of the service (wall-clock times)
      --gain DB  --profile quality|fast  --mode auto|ro-en|en-ro
      --speed N                                     replay N times faster (default 2; 1 = real time)
      --set live.key=value ...                      any other config override
      --name NAME                                   run folder name (default: time + settings)
      --code DIR                                    run another checkout's code (a branch worktree;
                                                    models/ and the venvs are linked in if missing)
  scripts/bench.py score RUN|LOG [--errors FILE]  per-error status + overall numbers
  scripts/bench.py compare RUN_A RUN_B            the two scores side by side
  scripts/bench.py check [--errors FILE]          validate the error list against the live logs

The engine plays the file on an audio clock (LIVE_FILE_SPEED / LIVE_FILE_T0): frame times are
the recording's own wall-clock times, so a replayed phrase lines up with the live log and the
error list. Pauses and phrase cuts count frames, so they come out as live; the short-fragment
hold (live.hold_s) is scaled by the speed. Catch-up merging is switched off (it would depend
on how fast the replay runs). Latency numbers are only meaningful at --speed 1.

Runs never use port 8765 (headless), never send to ProPresenter, and keep their logs under
logs/bench/runs/<name>/. Recordings and results stay on this Mac (logs/ is git-ignored)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import re
import statistics as st
import struct
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
BENCH = REPO / "logs" / "bench"
RECORDINGS = REPO / "logs" / "recordings"
LIVE_LOGS = REPO / "logs" / "services"
DEFAULT_ERRORS = BENCH / "errors_2026-10-24.json"
APP_PY = REPO / ".venv-mt" / "bin" / "python"


# --- audio ----------------------------------------------------------------------------------
def _wav_layout(path: pathlib.Path) -> tuple[int, int, int, int]:
    """(data offset, channels, samplerate, bytes per sample) of a PCM WAV, read from its chunks:
    a recording still being written has no final sizes in its header yet."""
    with open(path, "rb") as f:
        head = f.read(4096)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        sys.exit(f"{path}: not a WAV file")
    pos, fmt = 12, None
    while pos + 8 <= len(head):
        cid, size = head[pos:pos + 4], struct.unpack("<I", head[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            _, ch, sr, _, _, bits = struct.unpack("<HHIIHH", head[pos + 8:pos + 24])
            fmt = (ch, sr, bits // 8)
        if cid == b"data":
            if fmt is None:
                sys.exit(f"{path}: no fmt chunk")
            return pos + 8, *fmt
        pos += 8 + size + (size & 1)
    sys.exit(f"{path}: no data chunk in the first 4 KB")


def _write_wav(path: pathlib.Path, pcm: bytes, sr: int, ch: int = 1, width: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE")
        f.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * ch * width, ch * width, width * 8))
        f.write(b"data" + struct.pack("<I", len(pcm)) + pcm)


def _meta(audio: pathlib.Path) -> dict:
    side = audio.with_suffix(".json")
    if not side.exists():
        sys.exit(f"{side}: missing (needs start_wall, written by record_input.py)")
    return json.loads(side.read_text())


def snapshot() -> pathlib.Path:
    recs = sorted(RECORDINGS.glob("*.wav"))
    if not recs:
        sys.exit("no recordings in logs/recordings/")
    src = recs[-1]
    off, ch, sr, width = _wav_layout(src)
    size = src.stat().st_size
    nbytes = (size - off) // (ch * width) * (ch * width)
    with open(src, "rb") as f:
        f.seek(off)
        pcm = f.read(nbytes)
    meta = _meta(src)
    end = dt.datetime.fromtimestamp(meta["start_wall"] + nbytes / (ch * width) / sr)
    out = BENCH / "audio" / f"{src.stem}_to_{end:%H%M}.wav"
    _write_wav(out, pcm, sr, ch, width)
    out.with_suffix(".json").write_text(json.dumps({**meta, "source": str(src),
                                                    "end_iso": end.isoformat(timespec="seconds")}, indent=1))
    print(f"{out}  ({nbytes / (ch * width) / sr / 60:.1f} min, until {end:%H:%M:%S})")
    return out


def _clock(day: str, hms: str) -> float:
    return dt.datetime.fromisoformat(f"{day}T{hms}").timestamp()


def cut(audio: pathlib.Path, t_from: float | None, t_to: float | None, out: pathlib.Path) -> float:
    """Write the stretch [t_from, t_to] (wall clock) of `audio` to `out`; return its start time."""
    meta = _meta(audio)
    off, ch, sr, width = _wav_layout(audio)
    frame = ch * width
    total = (audio.stat().st_size - off) // frame
    t0 = meta["start_wall"]
    a = 0 if t_from is None else max(0, int((t_from - t0) * sr))
    b = total if t_to is None else min(total, int((t_to - t0) * sr))
    if b <= a:
        sys.exit("the --from/--to stretch is outside the recording")
    with open(audio, "rb") as f:
        f.seek(off + a * frame)
        pcm = f.read((b - a) * frame)
    _write_wav(out, pcm, sr, ch, width)
    return t0 + a / sr


# --- run --------------------------------------------------------------------------------------
def _set(cfg: dict, key: str, value) -> None:
    *head, last = key.split(".")
    cur = cfg
    for k in head:
        cur = cur.setdefault(k, {})
    cur[last] = value


def run(args) -> pathlib.Path:
    audio = pathlib.Path(args.audio).resolve()
    meta = _meta(audio)
    day = meta["start_iso"][:10]
    t_from = _clock(day, args.t_from) if args.t_from else None
    t_to = _clock(day, args.t_to) if args.t_to else None
    name = args.name or "_".join(x for x in (
        dt.datetime.now().strftime("%m%d-%H%M%S"), args.profile, f"g{args.gain:+g}",
        (args.t_from or "").replace(":", ""), f"x{args.speed:g}") if x)
    rdir = BENCH / "runs" / name
    rdir.mkdir(parents=True, exist_ok=True)
    seg = rdir / "segment.wav"
    seg_t0 = cut(audio, t_from, t_to, seg)

    import yaml  # noqa: PLC0415 - in .venv-mt; fine when run as `.venv-mt/bin/python scripts/bench.py`
    code = pathlib.Path(args.code).resolve() if args.code else REPO
    for need in ("models", ".venv-mt", ".venv-nemo"):          # git-ignored, so absent in a worktree
        if not (code / need).exists() and (REPO / need).exists():
            (code / need).symlink_to(REPO / need)
    cfg = yaml.safe_load((code / "config.yaml").read_text())
    speed = max(0.25, float(args.speed))
    overrides = {
        "propresenter.enabled": False, "propresenter.health_every_s": 0,
        "display.network": False, "live.autostart": False, "live.preload": False,
        "live.lag_warn_s": 0, "live.catchup_max_words": 0,
        "live.hold_s": float(cfg["live"].get("hold_s", 1.2)) / speed,
        "live.input_gain_db": float(args.gain), "live.language_mode": args.mode,
        "live.input_device": None, "live.translator_keepalive_s": 0,
    }
    for kv in args.set or []:
        k, _, v = kv.partition("=")
        try:
            overrides[k] = json.loads(v)
        except json.JSONDecodeError:
            overrides[k] = v
    for k, v in overrides.items():
        _set(cfg, k, v)
    (rdir / "config.yaml").write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False))
    (rdir / "settings.yaml").write_text("schema_version: 1\n")
    git = subprocess.run(["git", "-C", str(code), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    dirty = bool(subprocess.run(["git", "-C", str(code), "status", "--porcelain", "src", "config.yaml"],
                                capture_output=True, text=True).stdout.strip())
    info = {"audio": str(audio), "from": args.t_from, "to": args.t_to, "segment_start_wall": seg_t0,
            "gain": args.gain, "profile": args.profile, "mode": args.mode, "speed": speed,
            "overrides": overrides, "git": git + ("+dirty" if dirty else ""), "code": str(code),
            "started": dt.datetime.now().isoformat(timespec="seconds")}
    (rdir / "run.json").write_text(json.dumps(info, indent=1))
    env = {**os.environ, "LIVE_LOG_DIR": str(rdir), "LIVE_FILE_SPEED": str(speed), "LIVE_FILE_T0": repr(seg_t0)}
    env.pop("LIVE_HOME", None)
    cmd = [str(APP_PY), str(code / "src" / "live" / "app.py"), "--config", str(rdir / "config.yaml"),
           "--settings", str(rdir / "settings.yaml"), "--file", str(seg), "--autostart",
           "--exit-on-eof", "--headless", "--profile", args.profile, "--mode", args.mode]
    print(f"run {name}: {args.profile}, gain {args.gain:+g} dB, x{speed:g} -> {rdir}", flush=True)
    t = time.time()
    with open(rdir / "app.log", "w") as out:
        rc = subprocess.run(cmd, cwd=str(code), env=env, stdout=out, stderr=subprocess.STDOUT).returncode
    logs = sorted((rdir / "services").glob("*.jsonl"))
    info.update({"returncode": rc, "wall_s": round(time.time() - t, 1), "log": str(logs[-1]) if logs else None})
    (rdir / "run.json").write_text(json.dumps(info, indent=1))
    if rc != 0 or not logs:
        sys.exit(f"run failed (exit {rc}); see {rdir / 'app.log'}")
    print(f"done in {info['wall_s']:.0f} s: {logs[-1]}")
    return rdir


# --- scoring ----------------------------------------------------------------------------------
def _records(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def _run_log(arg: str) -> tuple[pathlib.Path, dict]:
    p = pathlib.Path(arg)
    if not p.exists():
        p = BENCH / "runs" / arg
    if p.is_dir():
        info = json.loads((p / "run.json").read_text())
        return pathlib.Path(info["log"]), info
    return p, {}


def _errors(path: str | None) -> list[dict]:
    p = pathlib.Path(path) if path else DEFAULT_ERRORS
    return json.loads(p.read_text())["errors"]


def _day_ts(day: str, hms: str) -> float:
    return _clock(day, hms)


def judge(err: dict, phrases: list[dict], day: str, window: float = 25.0) -> tuple[str, str]:
    """('fixed' | 'still' | 'missing', the text found) for one known error."""
    t = _day_ts(day, err["t"])
    near = [r for r in phrases if r.get("t_end", 0) >= t - window and r.get("t_start", 0) <= t + window]
    lines = [(r.get("translation") or "").strip() for r in near if r.get("outcome") == "ok"]
    text = " | ".join(lines)
    if not near:
        return "missing", ""
    bad, good = err.get("bad"), err.get("good")
    if bad and any(re.search(bad, ln, re.I) for ln in lines):    # line by line: ^ and $ mean a whole caption
        return "still", text
    if good and not any(re.search(good, ln, re.I) for ln in lines):
        return "still" if not bad else "fixed?", text
    return "fixed", text


GARBAGE = re.compile(r"<[^>\s]*\|>|<\|[^>]*>|\(Note|Note:|\[[a-z ]+\]", re.I)


def metrics(recs: list[dict], t_lo: float | None = None, t_hi: float | None = None) -> dict:
    ph = [r for r in recs if r.get("outcome")
          and (t_lo is None or r.get("t_start", 0) >= t_lo) and (t_hi is None or r.get("t_start", 0) <= t_hi)]
    ok = [r for r in ph if r["outcome"] == "ok"]
    words = [len((r.get("translation") or "").split()) for r in ok]
    flips = sum(1 for a, b in zip(ok, ok[1:]) if a.get("lang") != b.get("lang"))
    out = {"phrases": len(ph), "shown": len(ok),
           "not_shown": {k: sum(r["outcome"] == k for r in ph) for k in sorted({r["outcome"] for r in ph}) if k != "ok"},
           "words_median": st.median(words) if words else 0,
           "short_lt5": round(sum(w < 5 for w in words) / len(words), 3) if words else 0,
           "model_junk": sum(bool(GARBAGE.search(r.get("translation") or "")) for r in ok),
           "language_switches": flips,
           "mt_median_s": round(st.median(r.get("mt_s") or 0 for r in ok), 2) if ok else None}
    lv = [r["level"]["speech_db"] for r in ok if isinstance(r.get("level"), dict) and r["level"].get("speech_db") is not None]
    if lv:
        out["speech_db_median"] = round(st.median(lv), 1)
    return out


def score(arg: str, errors_path: str | None, quiet: bool = False) -> dict:
    log, info = _run_log(arg)
    recs = _records(log)
    errs = _errors(errors_path)
    ph = [r for r in recs if r.get("outcome")]
    if not ph:
        sys.exit(f"{log}: no phrases")
    day = dt.datetime.fromtimestamp(ph[0]["t_start"]).date().isoformat()
    lo, hi = ph[0]["t_start"], ph[-1]["t_end"]
    res = {}
    for e in errs:
        t = _day_ts(day, e["t"])
        if not (lo - 5 <= t <= hi + 5):
            continue
        res[e["id"]] = (*judge(e, ph, day), e)
    counts: dict = {}
    for status, _, e in res.values():
        counts.setdefault(e["cat"], {}).setdefault(status, 0)
        counts[e["cat"]][status] += 1
    summary = {"log": str(log), "run": info, "errors_in_range": len(res),
               "fixed": sum(s.startswith("fixed") for s, _, _ in res.values()),
               "still": sum(s == "still" for s, _, _ in res.values()),
               "missing": sum(s == "missing" for s, _, _ in res.values()),
               "by_category": counts, "metrics": metrics(recs, lo, hi),
               "per_error": {k: s for k, (s, _, _) in res.items()}}
    if not quiet:
        print(f"{log}\n{len(res)} known errors in range: fixed {summary['fixed']}, still {summary['still']}, "
              f"missing {summary['missing']}")
        for cat, c in sorted(counts.items()):
            print(f"  {cat:12s} " + ", ".join(f"{k} {v}" for k, v in sorted(c.items())))
        print("metrics: " + json.dumps(summary["metrics"], ensure_ascii=False))
        for k, (s, text, e) in res.items():
            mark = {"fixed": "OK ", "fixed?": "ok?", "still": "XX ", "missing": "-- "}[s]
            print(f"  {mark} {e['id']:4s} {e['t']} {e['cat']:10s} {e['note'][:60]}")
            if s != "fixed" and text:
                print(f"         now: {text[:150]}")
    return summary


def compare(a: str, b: str, errors_path: str | None) -> None:
    sa, sb = score(a, errors_path, quiet=True), score(b, errors_path, quiet=True)
    print(f"A: {sa['log']}\nB: {sb['log']}")
    print(f"{'':22s}{'A':>10s}{'B':>10s}")
    for k in ("errors_in_range", "fixed", "still", "missing"):
        print(f"{k:22s}{sa[k]:>10}{sb[k]:>10}")
    for k in sa["metrics"]:
        print(f"{k:22s}{str(sa['metrics'].get(k)):>10.10s}{str(sb['metrics'].get(k)):>10.10s}")
    changed = [(k, sa["per_error"].get(k), sb["per_error"].get(k)) for k in sorted(set(sa["per_error"]) | set(sb["per_error"]))
               if sa["per_error"].get(k) != sb["per_error"].get(k)]
    if changed:
        print("errors that changed:")
        for k, x, y in changed:
            print(f"  {k}: {x} -> {y}")


def check(errors_path: str | None) -> None:
    """Every known error must be visible in the live logs (bad found, or good absent), else the
    pattern is wrong. The live service is the baseline: all should come out 'still'."""
    errs = _errors(errors_path)
    recs = [r for p in sorted(LIVE_LOGS.glob("2026-10-24_*.jsonl")) for r in _records(p) if r.get("outcome")]
    day = "2026-10-24"
    bad = 0
    for e in errs:
        s, text = judge(e, recs, day)
        flag = "ok " if s == "still" else "BAD"
        bad += s != "still"
        print(f"  {flag} {e['id']:4s} {e['t']} {s:8s} {e['note'][:50]}" + ("" if s == "still" else f"\n       {text[:160]}"))
    print(f"{len(errs) - bad}/{len(errs)} errors reproduce on the live log")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("snapshot")
    r = sub.add_parser("run")
    r.add_argument("audio")
    r.add_argument("--from", dest="t_from")
    r.add_argument("--to", dest="t_to")
    r.add_argument("--gain", type=float, default=0.0)
    r.add_argument("--profile", default="quality")
    r.add_argument("--mode", default="auto")
    r.add_argument("--speed", type=float, default=2.0)
    r.add_argument("--set", action="append")
    r.add_argument("--name")
    r.add_argument("--code")
    s = sub.add_parser("score")
    s.add_argument("run")
    s.add_argument("--errors")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--errors")
    k = sub.add_parser("check")
    k.add_argument("--errors")
    a = ap.parse_args()
    if a.cmd == "snapshot":
        snapshot()
    elif a.cmd == "run":
        run(a)
    elif a.cmd == "score":
        score(a.run, a.errors)
    elif a.cmd == "compare":
        compare(a.a, a.b, a.errors)
    else:
        check(a.errors)


if __name__ == "__main__":
    main()
