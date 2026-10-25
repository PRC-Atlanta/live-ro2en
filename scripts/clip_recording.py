#!/usr/bin/env python3
"""Keep only the useful parts of a service recording: clips around the moments worth replaying.

  scripts/clip_recording.py mark [--at HH:MM:SS] NOTE   during the service: remember this moment
                                                       (now, or a given time) with a note
  scripts/clip_recording.py plan [REC] [options]        list the clips that `cut` would make
  scripts/clip_recording.py cut  [REC] [options]        cut them; --delete-full removes the full
                                                       recording once every clip is checked
      --log LOG          the service log (default: the newest one that overlaps the recording)
      --before S / --after S   seconds kept around each moment (default 20 / 10)
      --max-min M        total clip length cap in minutes (default 30); marks are kept first

Moments: the operator's / Claude's marks (`mark`), then what the service log flags by itself —
phrases not shown for a reason worth checking (confidence gate, over-long or echoed output,
errors), guard retries, language switches, gate decisions, slow phrases, verse captions, name
repairs, clipping, backlog events. Clips that touch are merged.

Each clip is a WAV with a .json beside it holding its wall-clock start (like the full
recording), so `scripts/bench.py run <clip>.wav` replays it and the error list lines up.
Clips go to logs/recordings/clips/<recording>/ with an index.md of why each was kept.
Everything stays in logs/ (git-ignored): these are recordings of real people. Stdlib only."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench  # noqa: E402  (WAV helpers; no third-party imports at module level)

REPO = HERE.parent
RECORDINGS = REPO / "logs" / "recordings"
LOG_DIRS = [REPO / "logs" / "services", pathlib.Path.home() / "Library" / "Logs" / "Live RO2EN" / "services"]
WORTH = {"low_confidence", "mt_overlong", "mt_echo", "mt_loop", "error", "empty_mt"}   # not 'empty_stt': silence


def newest_recording() -> pathlib.Path:
    recs = sorted(RECORDINGS.glob("*.wav"))
    if not recs:
        sys.exit("no recordings in logs/recordings/")
    return recs[-1]


def marks_path(rec: pathlib.Path) -> pathlib.Path:
    return rec.with_suffix(".marks.jsonl")


def mark(at: str | None, note: str) -> None:
    rec = newest_recording()
    if at:
        day = bench._meta(rec)["start_iso"][:10]
        t = dt.datetime.fromisoformat(f"{day}T{at}").timestamp()
    else:
        t = dt.datetime.now().timestamp()
    with marks_path(rec).open("a") as f:
        f.write(json.dumps({"t": t, "note": note}, ensure_ascii=False) + "\n")
    print(f"marked {dt.datetime.fromtimestamp(t):%H:%M:%S} in {rec.name}: {note}")


def service_logs(t0: float, t1: float) -> list[pathlib.Path]:
    """Every service log with phrases inside [t0, t1] (an app restart mid-service makes two)."""
    found = []
    for d in LOG_DIRS:
        for p in sorted(d.glob("20*.jsonl")) if d.is_dir() else []:
            try:
                first = next(json.loads(ln) for ln in p.open() if '"t_start"' in ln)
            except (StopIteration, json.JSONDecodeError):
                continue
            if first.get("t_start", 0) <= t1 and p.stat().st_mtime >= t0:
                found.append(p)
    return found


def moments(recs: list[dict], marks: list[dict]) -> list[tuple[float, int, str]]:
    """(time, priority, reason): 0 = a mark, 1 = something went wrong, 2 = worth a look,
    3 = clipping (common on a hot input: kept only if the budget allows)."""
    out = [(m["t"], 0, f"mark: {m.get('note', '')}") for m in marks]
    shown = [r for r in recs if r.get("outcome") == "ok" and r.get("t_start") is not None]
    for i in range(1, len(shown)):           # a switch is suspicious when it flips straight back
        a, r = shown[i - 1], shown[i]          # or a very short phrase made it; sustained ones are fine
        if r.get("lang") == a.get("lang"):
            continue
        back = i + 1 < len(shown) and shown[i + 1].get("lang") == a.get("lang")
        short = len((r.get("source") or "").split()) <= 4
        if back or short:
            out.append((r["t_start"], 2, f"switch to {r.get('lang')}{' and back' if back else ''}: "
                                          f"{(r.get('source') or '')[:50]!r}"))
    clip_t = []
    for r in recs:
        t = r.get("t_start")
        if t is None:
            if r.get("event") == "backlog" and r.get("ok") is False:
                out.append((dt.datetime.fromisoformat(r["time"]).timestamp(), 1, f"backlog {r.get('s')} s"))
            continue
        o = r.get("outcome")
        src = (r.get("source") or r.get("ro") or r.get("multi") or "")[:50]
        if o in WORTH:
            out.append((t, 1, f"{o}: {src!r}"))
        if r.get("guard"):
            out.append((t, 1, f"guard: {src!r}"))
        if r.get("gate_switched") or r.get("gate_cut") or r.get("fallback"):
            out.append((t, 1, f"gate: {src!r}"))
        if (r.get("mt_s") or 0) > 6 or (r.get("first_word_s") or 0) > 20:
            out.append((t, 1, f"slow: mt {r.get('mt_s', 0):.1f} s"))
        if o == "ok":
            if r.get("verse"):
                v = r["verse"][0] if isinstance(r["verse"], list) else r["verse"]
                out.append((t, 2, f"verse {v.get('ref') if isinstance(v, dict) else v}"))
            if r.get("names_fixed"):
                out.append((t, 2, f"names {r['names_fixed']}"))
            lv = r.get("level") if isinstance(r.get("level"), dict) else {}
            if (lv.get("peak_db") or -120) > -1:
                clip_t.append((t, lv["peak_db"]))
    if clip_t:                                 # a hot input clips all day: a few samples, spread out
        step = max(1, len(clip_t) // 4)
        out += [(t, 3, f"clipping (peak {pk} dB; {len(clip_t)} phrases clipped in all)") for t, pk in clip_t[::step][:4]]
    return sorted(out)


def plan(moms: list[tuple[float, int, str]], t0: float, t1: float, before: float, after: float,
         max_s: float, max_clip: float = 90.0) -> list[dict]:
    """Windows around the moments, merged when they touch (up to max_clip seconds per clip, so a
    busy stretch doesn't chain into one long clip), kept by priority until max_s."""
    wins = sorted(((max(t0, t - before), min(t1, t + after), p, why) for t, p, why in moms if t0 <= t <= t1),
                  key=lambda w: (w[2], w[0]))
    kept: list[dict] = []
    total = 0.0
    for a, b, p, why in wins:
        mid = a + before if a > t0 else b - after          # the moment itself
        inside = next((k for k in kept if k["start"] <= mid <= k["end"]), None)
        if inside:                                          # already in a clip: just say why
            inside["why"].append(why)
            inside["priority"] = min(inside["priority"], p)
            continue
        hit = next((k for k in kept if a <= k["end"] + 5 and b >= k["start"] - 5
                    and max(k["end"], b) - min(k["start"], a) <= max_clip), None)
        if hit:
            new_a, new_b = min(hit["start"], a), max(hit["end"], b)
            add = (new_b - new_a) - (hit["end"] - hit["start"])
            if total + add > max_s and p > 0:
                continue
            total += add
            hit.update(start=new_a, end=new_b, priority=min(hit["priority"], p))
            hit["why"].append(why)
            continue
        for k in kept:                                      # never the same audio in two clips
            if k["start"] < b <= k["end"]:
                b = k["start"]
            if k["start"] <= a < k["end"]:
                a = k["end"]
        if b - a < 3 or (total + (b - a) > max_s and p > 0):
            continue
        total += b - a
        kept.append({"start": a, "end": b, "priority": p, "why": [why]})
    return sorted(kept, key=lambda k: k["start"])


def _gather(rec_arg: str | None, log_arg: str | None):
    rec = pathlib.Path(rec_arg) if rec_arg else newest_recording()
    meta = bench._meta(rec)
    off, ch, sr, width = bench._wav_layout(rec)
    t0 = meta["start_wall"]
    t1 = t0 + (rec.stat().st_size - off) / (ch * width) / sr
    logs = [pathlib.Path(log_arg)] if log_arg else service_logs(t0, t1)
    recs = [json.loads(ln) for p in logs for ln in p.open() if ln.strip()]
    mp = marks_path(rec)
    marks = [json.loads(ln) for ln in mp.open() if ln.strip()] if mp.exists() else []
    return rec, meta, t0, t1, logs, recs, marks


def hms(t: float) -> str:
    return dt.datetime.fromtimestamp(t).strftime("%H:%M:%S")


def cut(args, really: bool) -> None:
    rec, meta, t0, t1, logs, recs, marks = _gather(args.rec, args.log)
    clips = plan(moments(recs, marks), t0, t1, args.before, args.after, args.max_min * 60)
    total = sum(c["end"] - c["start"] for c in clips)
    print(f"{rec.name}: {(t1 - t0) / 60:.0f} min recorded; logs {', '.join(p.name for p in logs) or 'none'}; "
          f"{len(marks)} marks; {len(clips)} clips, {total / 60:.1f} min")
    for c in clips:
        print(f"  {hms(c['start'])}-{hms(c['end'])}  " + "; ".join(c["why"])[:150])
    if not really:
        return
    out = RECORDINGS / "clips" / rec.stem
    out.mkdir(parents=True, exist_ok=True)
    lines = [f"# Clips of {rec.name}", "", "Service logs: " + (", ".join(f"`{p}`" for p in logs) or "none"), ""]
    ok = True
    for c in clips:
        wav = out / f"{hms(c['start']).replace(':', '')}-{hms(c['end']).replace(':', '')}.wav"
        start = bench.cut(rec, c["start"], c["end"], wav)
        wav.with_suffix(".json").write_text(json.dumps({**meta, "start_wall": start,
                                                        "start_iso": dt.datetime.fromtimestamp(start).isoformat(timespec="milliseconds"),
                                                        "source": str(rec), "why": c["why"]}, ensure_ascii=False, indent=1))
        need = 44 + int((c["end"] - c["start"]) * meta["samplerate"]) * 2 * 0.98
        ok &= wav.stat().st_size >= need
        lines.append(f"- `{wav.name}` {hms(c['start'])}-{hms(c['end'])}: " + "; ".join(c["why"]))
    (out / "index.md").write_text("\n".join(lines) + "\n")
    if marks_path(rec).exists():
        (out / "marks.jsonl").write_text(marks_path(rec).read_text())
    print(f"clips in {out}")
    if args.delete_full:
        if not ok:
            sys.exit("a clip came out short: the full recording is kept")
        rec.unlink()
        rec.with_suffix(".json").unlink(missing_ok=True)
        marks_path(rec).unlink(missing_ok=True)
        print(f"deleted the full recording {rec.name} (clips and marks kept)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("mark")
    m.add_argument("--at")
    m.add_argument("note", nargs="+")
    for name in ("plan", "cut"):
        c = sub.add_parser(name)
        c.add_argument("rec", nargs="?")
        c.add_argument("--log")
        c.add_argument("--before", type=float, default=20.0)
        c.add_argument("--after", type=float, default=10.0)
        c.add_argument("--max-min", type=float, default=30.0)
        if name == "cut":
            c.add_argument("--delete-full", action="store_true")
    a = ap.parse_args()
    if a.cmd == "mark":
        mark(a.at, " ".join(a.note))
    else:
        cut(a, really=a.cmd == "cut")


if __name__ == "__main__":
    main()
