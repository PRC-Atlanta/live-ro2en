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
      --terms FILE                                  today's names, one per line (as typed in Names & words)
      --code DIR                                    run another checkout's code (a branch worktree;
                                                    models/ and the venvs are linked in if missing)
  scripts/bench.py score RUN|LOG [--errors FILE]  per-error status + overall numbers
  scripts/bench.py compare RUN_A RUN_B            the two scores side by side
  scripts/bench.py check [--errors FILE]          validate the error list against the live logs
  scripts/bench.py ref RUN [RUN ...] [--window S]  a corrected-sermon run (audio corr_<name>.wav, see
                                                  SERMONS): Romanian transcript WER against the corrected
                                                  text and, with a reference translation, the English
                                                  translation's chrF++/BLEU against it; writes
                                                  ref_score.json and ref_side.md into the run folder

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
sys.path.insert(0, str(REPO / "src"))                    # scoring, textnorm


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
    if args.terms:
        env["LIVE_TERMS_FILE"] = str(pathlib.Path(args.terms).resolve())
        info["terms"] = env["LIVE_TERMS_FILE"]
    env.pop("LIVE_HOME", None)
    (rdir / "user").mkdir(exist_ok=True)                 # names from --terms only: not what was typed in
    env["LIVE_USER_DIR"] = str(rdir / "user")           # Settings of this checkout (a --code worktree has none)
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
    return _error_file(path)["errors"]


def _error_file(path: str | None) -> dict:
    p = pathlib.Path(path) if path else DEFAULT_ERRORS
    return json.loads(p.read_text())


def _errors_day(path: str | None) -> str:
    """The service the known errors were seen in: "day" in the file, else its name
    (errors_2026-10-24.json), else the first list's day."""
    p = pathlib.Path(path) if path else DEFAULT_ERRORS
    m = re.search(r"\d{4}-\d{2}-\d{2}", p.name)
    return _error_file(path).get("day") or (m.group(0) if m else "2026-10-24")


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
    if day != _errors_day(errors_path):                 # another service's recording: nothing to judge
        errs = []
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


# --- corrected sermons --------------------------------------------------------------------------
# Replayed from logs/bench/audio/corr_<name>.wav (start_wall 2026-10-22 10:00:00). "corrections":
# human-corrected Romanian transcript per recogniser segment (unverified segments keep the
# recogniser's text; "last": only segments up to this one are corrected). "timed": Romanian
# transcript lines, each starting with its time (**[m:ss]**) and followed by its reference English
# translation in *italics*.
SERMONS = {
    "br-random-1": {"corrections": "data/finetune/corrections_clean/br-random-1.json"},
    "nelu-suciu": {"corrections": "data/finetune/corrections_clean/nelu-suciu.json", "last": 104},
    "luigi-mitoi": {"timed": "Audio/Luigi Mitoi - Ce facem cand - With time stamps.txt"},
}
SEGMENTS = REPO / "transcripts" / "local" / "jackrabbit-ctc-lm-b8" / "archive"
TAG = re.compile(r"\s*\[(?:clears throat|voce dreas[ăa])\]", re.I)  # not speech
STAMP = re.compile(r"^\*\*\[(?:(\d+):)?(\d+):(\d\d)\]\*\*\s*")


def reference(name: str) -> tuple[list[dict], float | None]:
    """The sermon's reference lines [{"ro", "en", "t"}] ("en" and "t", the start in seconds
    into the recording, None without a timed reference translation), and where the corrected
    stretch ends (seconds into the recording; None = the whole of it)."""
    spec = SERMONS[name]
    if "timed" in spec:
        body = (REPO / spec["timed"]).read_text(encoding="utf-8").split("\n---", 1)[1]
        out = []
        for ln in (x.strip().rstrip("\\").strip() for x in body.splitlines()):
            m = STAMP.match(ln)
            if m:
                h, mi, se = m.groups()
                out.append({"ro": _unbracket(TAG.sub("", ln[m.end():])), "en": None,
                            "t": int(h or 0) * 3600 + int(mi) * 60 + int(se)})
            elif ln.startswith("*") and ln.endswith("*") and len(ln) > 1:
                if not out or out[-1]["en"] is not None:
                    sys.exit(f"{spec['timed']}: English translation without its Romanian line: {ln[:60]}")
                out[-1]["en"] = _unbracket(TAG.sub("", ln.strip("*")))
            elif ln:
                sys.exit(f"{spec['timed']}: line without a time: {ln[:60]}")
        if not out or any(x["en"] is None for x in out):
            sys.exit(f"{spec['timed']}: no lines, or a Romanian line without its English translation")
        if any(a["t"] > b["t"] for a, b in zip(out, out[1:])):
            sys.exit(f"{spec['timed']}: times go backwards")
        return [x for x in out if x["ro"] or x["en"]], None
    corr = json.loads((REPO / spec["corrections"]).read_text())
    out, end = [], None
    for x in _records(SEGMENTS / f"{name}.jsonl"):
        if spec.get("last") is not None and int(x["segment"].split("/")[1]) > spec["last"]:
            break
        c = corr.get(x["segment"])
        out.append({"ro": c["corrected"] if c and c.get("verified") else x["text"], "en": None, "t": None})
        end = x["end"]
    return out, (end if spec.get("last") is not None else None)


def _unbracket(text: str) -> str:
    """Words the translator added in [brackets] stay, without the brackets."""
    return re.sub(r"\[([^\]]*)\]", r"\1", text).strip()


def windows(lines: list[dict], phrases: list[tuple[float, float]], window_s: float) -> list[dict]:
    """~window_s stretches of whole reference lines (by their start times); each app phrase
    (start, end, in seconds into the recording) goes to the stretch its middle falls in."""
    out = []
    for i, x in enumerate(lines):
        if not out or x["t"] - out[-1]["t0"] >= window_s:
            out.append({"t0": x["t"], "lines": [], "phrases": []})
        out[-1]["lines"].append(i)
    for j, (a, b) in enumerate(phrases):
        mid = (a + b) / 2
        k = max([n for n, w in enumerate(out) if w["t0"] <= mid] or [0])
        out[k]["phrases"].append(j)
    for w, nxt in zip(out, out[1:] + [None]):
        w["t1"] = nxt["t0"] if nxt else max([phrases[j][1] for j in w["phrases"]] or [w["t0"]])
    return out


def ref_score(arg: str, window_s: float = 20.0, quiet: bool = False) -> dict:
    """Score a corrected-sermon run: Romanian transcript WER against the corrected text, and,
    where the sermon has a reference translation, the English translation against it (chrF++
    and BLEU over ~window_s stretches of the reference's timed lines)."""
    from scoring import score as wer  # noqa: PLC0415
    from textnorm import tokens  # noqa: PLC0415
    log, info = _run_log(arg)
    name = pathlib.Path(info.get("audio", "")).stem.removeprefix("corr_")
    if name not in SERMONS:
        sys.exit(f"{arg}: not a corrected-sermon run (audio {info.get('audio')}); known: {', '.join(SERMONS)}")
    lines, end = reference(name)
    t0 = info["segment_start_wall"]
    ph = [r for r in _records(log) if r.get("outcome") and "t_start" in r
          and (end is None or r["t_start"] - t0 < end + 1.0)]
    ro = [tokens(r.get("source") or r.get("ro") or "") for r in ph]
    ref_ro = [tokens(x["ro"]) for x in lines]
    ref_words = [w for ws in ref_ro for w in ws]
    w_all = wer(ref_words, [w for ws in ro for w in ws], free_ends=False)
    w_shown = wer(ref_words, [w for r, ws in zip(ph, ro) if r["outcome"] == "ok" for w in ws], free_ends=False)
    out = {"run": str(log), "sermon": name, "git": info.get("git"), "phrases": len(ph),
           "ro_wer_all": w_all, "ro_wer_shown": w_shown}
    side = [f"# {name}: run {info.get('git')} ({log.parent.parent.name})\n"]
    if lines[0]["t"] is not None:
        import sacrebleu  # noqa: PLC0415 - in .venv-mt
        wins = windows(lines, [(r["t_start"] - t0, r["t_end"] - t0) for r in ph], window_s)
        refs, hyps = [], []
        for w in wins:
            ps = [ph[j] for j in w["phrases"]]
            w["ref"] = " ".join(lines[i]["en"] for i in w["lines"])
            # captions as shown; a phrase taken for English got a Romanian caption: missing here
            w["hyp"] = " ".join((p.get("translation") or "").strip() for p in ps
                                if p["outcome"] == "ok" and p.get("lang", "ro") == "ro")
            w["t"] = (w["t0"], w["t1"])
            w["chrf"] = sacrebleu.sentence_chrf(w["hyp"], [w["ref"]], word_order=2).score
            refs.append(w["ref"])
            hyps.append(w["hyp"])
            side.append(f"\n**{_mmss(w['t'][0])}–{_mmss(w['t'][1])}** chrF++ {w['chrf']:.0f}  \n"
                        f"RO (reference): {' '.join(lines[i]['ro'] for i in w['lines'])}  \n"
                        f"EN (reference): {w['ref']}  \nEN (app): {w['hyp'] or '—'}  ")
            for p in ps:
                if p["outcome"] != "ok":
                    side.append(f"  _not shown ({p['outcome']}): {p.get('source') or ''}_  ")
        out.update({"en_chrf": sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score,
                    "en_bleu": sacrebleu.corpus_bleu(hyps, [refs]).score, "windows": len(wins),
                    "worst": [{"t": _mmss(w["t"][0]), "chrf": round(w["chrf"], 1), "ref": w["ref"], "app": w["hyp"]}
                              for w in sorted(wins, key=lambda w: w["chrf"])[:8]]})
    rdir = log.parent.parent
    (rdir / "ref_score.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    if len(side) > 1:
        (rdir / "ref_side.md").write_text("\n".join(side) + "\n")
    if not quiet:
        print(f"{name} ({info.get('git')}): {len(ph)} phrases\n  Romanian transcript WER: all phrases "
              f"{_wer(w_all)}, shown only {_wer(w_shown)}")
        if "en_chrf" in out:
            print(f"  English translation vs reference: chrF++ {out['en_chrf']:.1f}, BLEU {out['en_bleu']:.1f} "
                  f"({out['windows']} stretches of ~{window_s:g} s; side by side in {rdir / 'ref_side.md'})")
            print("  lowest chrF++:")
            for w in out["worst"]:
                print(f"    {w['t']}  {w['chrf']:4.0f}  ref: {w['ref'][:90]}\n                app: {w['app'][:90]}")
    return out


def _mmss(s: float | None) -> str:
    return "--:--" if s is None else f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def _wer(s: dict) -> str:
    return f"{s['wer'] * 100:.2f}% (S{s['sub']} D{s['del']} I{s['ins']} / {s['n_ref']})"


def ref_table(runs: list[str], window_s: float) -> None:
    """One row per run, then the pooled Romanian transcript WER (as in translation idea 27)."""
    rows = [ref_score(r, window_s, quiet=len(runs) > 1) for r in runs]
    if len(rows) < 2:
        return
    print(f"{'sermon':12s} {'code':12s} {'RO WER all':>10s} {'RO WER shown':>12s} {'EN chrF++':>9s} {'EN BLEU':>7s}")
    for r in rows:
        en = (f"{r['en_chrf']:9.1f} {r['en_bleu']:7.1f}") if "en_chrf" in r else f"{'–':>9s} {'–':>7s}"
        print(f"{r['sermon']:12s} {str(r['git']):12s} {r['ro_wer_all']['wer'] * 100:9.2f}% "
              f"{r['ro_wer_shown']['wer'] * 100:11.2f}% {en}")
    for git in dict.fromkeys(r["git"] for r in rows):
        same = [r for r in rows if r["git"] == git]
        pooled = []
        for k in ("ro_wer_all", "ro_wer_shown"):
            n = sum(r[k]["n_ref"] for r in same)
            e = sum(r[k]["sub"] + r[k]["del"] + r[k]["ins"] for r in same)
            pooled.append(f"{k.removeprefix('ro_wer_')} {e / max(n, 1) * 100:.2f}%")
        print(f"  {git}: pooled Romanian transcript WER {', '.join(pooled)} "
              f"({len(same)} sermons, {sum(r['ro_wer_all']['n_ref'] for r in same)} words)")


def check(errors_path: str | None) -> None:
    """Every known error must be visible in the live logs (bad found, or good absent), else the
    pattern is wrong. The live service is the baseline: all should come out 'still'."""
    errs, day = _errors(errors_path), _errors_day(errors_path)
    recs = [r for p in sorted(LIVE_LOGS.glob(f"{day}_*.jsonl")) for r in _records(p) if r.get("outcome")]
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
    r.add_argument("--terms")
    s = sub.add_parser("score")
    s.add_argument("run")
    s.add_argument("--errors")
    c = sub.add_parser("compare")
    c.add_argument("a")
    c.add_argument("b")
    c.add_argument("--errors")
    k = sub.add_parser("check")
    k.add_argument("--errors")
    f = sub.add_parser("ref")
    f.add_argument("runs", nargs="+")
    f.add_argument("--window", type=float, default=20.0)
    a = ap.parse_args()
    if a.cmd == "snapshot":
        snapshot()
    elif a.cmd == "run":
        run(a)
    elif a.cmd == "score":
        score(a.run, a.errors)
    elif a.cmd == "compare":
        compare(a.a, a.b, a.errors)
    elif a.cmd == "ref":
        ref_table(a.runs, a.window)
    else:
        check(a.errors)


if __name__ == "__main__":
    main()
