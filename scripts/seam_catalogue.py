#!/usr/bin/env python3
"""Seam catalogue: the places where a chunk cut fell inside a word (idea 25), for quick checks.

  scripts/seam_catalogue.py build RUN [RUN ...]   from full corrected-sermon runs (bench.py run
                                                  logs/bench/audio/corr_<name>.wav): every phrase
                                                  boundary (next piece within 0.7 s) whose two edge
                                                  words look like one word («important.» | «importantă»)
                                                  -> logs/bench/seams.json
  scripts/seam_catalogue.py clips [--lead S]      one short recording of all of them, each from a
                                                  pause ~S s before it (default 20), 8 s of silence
                                                  between -> logs/bench/audio/seamclips.wav (+ .json)

Each entry is labelled against the corrected Romanian transcript:
  seam  the first word is wrong, the cut-off start of the second («important.» | «importantă»):
        right is the word before it, then the second
  dup   the first word is right and the second is the same word heard again («sărac.» | «sărat»):
        right is the first, then the word after the second
  keep  both words are right (two real words that look alike): right is both, side by side
A run in which the recogniser did not give the two words side by side counts as not reproduced.

Then:  scripts/bench.py run logs/bench/audio/seamclips.wav --speed 3 --name seams-X
       scripts/bench.py score seams-X --errors logs/bench/seams.json
A full corrected-sermon run is scored against the same file (entries carry their sermon and time
in it), so the clips can be checked against the full replay they stand for."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "live"))
import bench                                                    # noqa: E402
from live.context_stt import same_word                          # noqa: E402
from live.pipeline import CUT_GAP_S                             # noqa: E402
from scoring import align                                       # noqa: E402
from textnorm import fold_diacritics, tokens                    # noqa: E402

OUT = bench.BENCH / "seams.json"
CLIPS = bench.BENCH / "audio" / "seamclips.wav"
CLIPS_START = "2026-10-23T10:00:00"     # its own day: the corrected sermons all start 2026-10-22 10:00
GAP_S = 8.0                             # silence between clips: longer than the 6 s context
TAIL_S = 8.0
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")


def _key(w: str) -> str:
    return fold_diacritics(w.lower())


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def build(runs: list[str]) -> None:
    entries = []
    for run in runs:
        log, info = bench._run_log(run)
        sermon = pathlib.Path(info["audio"]).stem.removeprefix("corr_")
        if sermon not in bench.SERMONS:
            sys.exit(f"{run}: not a corrected-sermon run")
        lines, end = bench.reference(sermon)
        t0 = info["segment_start_wall"]
        # every caption made, also one a redo replaced later, with the recogniser's own words (ro):
        # the catalogue is the same whether the run's redo was on or off
        ph = [r for r in bench._records(log) if r.get("outcome") == "ok" and "t_start" in r
              and (end is None or r["t_start"] - t0 < end + 1.0)]
        src = [r.get("ro") or "" for r in ph]
        # which output tokens are wrong, against the corrected transcript
        hyp, owner = [], []
        for i, s in enumerate(src):
            for w in tokens(s):
                hyp.append(w)
                owner.append(i)
        a = align([w for x in lines for w in tokens(x["ro"])], hyp, free_ends=False)
        wrong = [r != h for r, h in a.pairs if h is not None]
        last = {o: k for k, o in enumerate(owner)}
        first = {o: k for k, o in reversed(list(enumerate(owner)))}
        for i in range(len(ph) - 1):
            gap = ph[i + 1].get("t_speech", ph[i + 1]["t_start"]) - ph[i]["t_end"]
            pw, nw = _words(src[i]), _words(src[i + 1])
            if gap > CUT_GAP_S or not pw or not nw or i not in last or i + 1 not in first \
                    or not same_word(pw[-1], nw[0]):
                continue
            if _key(pw[-1]) == _key(nw[0]):
                continue                        # the same word twice: a repeat or a seam, can't tell by text
            cat = "seam" if wrong[last[i]] else "dup" if wrong[first[i + 1]] else "keep"
            t = dt.datetime.fromtimestamp(ph[i]["t_end"]).strftime("%H:%M:%S")
            entries.append({"id": f"{sermon[:2].upper()}{len(entries) + 1:03d}", "sermon": sermon, "t_src": t,
                            "cat": cat, "on": "ro", "pair": [pw[-1], nw[0]],
                            "before": pw[-2] if len(pw) > 1 else None, "after": nw[1] if len(nw) > 1 else None,
                            "note": f"«{' '.join(src[i].split()[-3:])}» | «{' '.join(src[i + 1].split()[:3])}» "
                                    f"gap {gap:.1f} s",
                            "was": f"{src[i]} | {src[i + 1]}"})
    doc = {"how": "pair = the last word of one piece and the first of the next, as the recogniser gave them "
                  "(look-alikes, the pieces under 0.7 s apart). seam: the first is the cut-off start of the "
                  "second (right: the word before, then the second). dup: the first is right and the second "
                  "is the same word heard again (right: the first, then the word after). keep: two real "
                  "words (right: both). Judged on the Romanian transcript near t (bench.judge_pair): not "
                  "reproduced when the recogniser did not give the two words side by side this time. "
                  "t_src: time in the corrected sermon's replay (corr_<sermon>.wav); t: time in seamclips.wav.",
           "day": CLIPS_START[:10], "built_from": runs, "errors": entries}
    if OUT.exists():                            # keep the clip times if the clips are already cut
        old = {(e["sermon"], e["t_src"]): e.get("t") for e in json.loads(OUT.read_text())["errors"]}
        for e in entries:
            if old.get((e["sermon"], e["t_src"])):
                e["t"] = old[(e["sermon"], e["t_src"])]
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    n = {c: sum(e["cat"] == c for e in entries) for c in ("seam", "dup", "keep")}
    print(f"{OUT}: {len(entries)} entries ({n['seam']} seam, {n['dup']} dup, {n['keep']} keep)")


def _pause_before(phrases: list[dict], t: float, lead: float) -> float:
    """A phrase start after a real pause, ~lead s before t (the cuts then fall as in the full replay)."""
    starts = [q.get("t_speech", q["t_start"]) for p, q in zip(phrases, phrases[1:])
              if q.get("t_speech", q["t_start"]) - p["t_end"] >= 0.3]
    near = [s for s in starts if t - 2 * lead <= s <= t - lead / 2]
    return min(near, key=lambda s: abs(s - (t - lead))) - 0.2 if near else t - lead


def clips(lead: float) -> None:
    doc = json.loads(OUT.read_text())
    by_sermon: dict[str, list[dict]] = {}
    for e in doc["errors"]:
        by_sermon.setdefault(e["sermon"], []).append(e)
    pcm_all, sr0, ch0, width0 = b"", None, None, None
    clock = dt.datetime.fromisoformat(CLIPS_START).timestamp()
    spans = []
    for sermon, es in sorted(by_sermon.items()):
        audio = bench.BENCH / "audio" / f"corr_{sermon}.wav"
        day = bench._meta(audio)["start_iso"][:10]
        run = next(r for r in doc["built_from"] if sermon in json.loads(
            (bench.BENCH / "runs" / r / "run.json").read_text())["audio"])
        log, _ = bench._run_log(run)
        phrases = [r for r in bench._records(log) if r.get("outcome") and "t_start" in r]
        # merge the entries' stretches that overlap
        stretches: list[list] = []
        for e in sorted(es, key=lambda e: e["t_src"]):
            t = bench._clock(day, e["t_src"])
            a, b = _pause_before(phrases, t, lead), t + TAIL_S
            if stretches and a <= stretches[-1][1]:
                stretches[-1][1] = max(stretches[-1][1], b)
                stretches[-1][2].append((e, t))
            else:
                stretches.append([a, b, [(e, t)]])
        off, ch, sr, width = bench._wav_layout(audio)
        sr0, ch0, width0 = sr0 or sr, ch0 or ch, width0 or width
        if (sr, ch, width) != (sr0, ch0, width0):
            sys.exit(f"{audio}: not the same format as the other recordings")
        t_rec = bench._meta(audio)["start_wall"]
        frame = ch * width
        with open(audio, "rb") as f:
            for a, b, items in stretches:
                a = max(a, t_rec)                       # an entry in the recording's first seconds
                i, j = int((a - t_rec) * sr), int((b - t_rec) * sr)
                f.seek(off + i * frame)
                pcm_all += f.read((j - i) * frame)
                for e, t in items:
                    e["t"] = dt.datetime.fromtimestamp(clock + (t - a)).strftime("%H:%M:%S")
                spans.append({"sermon": sermon, "from": dt.datetime.fromtimestamp(a).strftime("%H:%M:%S"),
                              "to": dt.datetime.fromtimestamp(b).strftime("%H:%M:%S"),
                              "at": dt.datetime.fromtimestamp(clock).strftime("%H:%M:%S")})
                clock += (j - i) / sr
                pcm_all += b"\0" * (int(GAP_S * sr) * frame)
                clock += int(GAP_S * sr) / sr
    bench._write_wav(CLIPS, pcm_all, sr0, ch0, width0)
    start = dt.datetime.fromisoformat(CLIPS_START)
    CLIPS.with_suffix(".json").write_text(json.dumps(
        {"start_wall": start.timestamp(), "start_iso": CLIPS_START, "source": str(OUT), "clips": spans}, indent=1))
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    total = len(pcm_all) / (ch0 * width0) / sr0
    print(f"{CLIPS}: {len(spans)} clips, {total / 60:.1f} min (x3 replay ≈ {total / 180:.1f} min)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("runs", nargs="+")
    c = sub.add_parser("clips")
    c.add_argument("--lead", type=float, default=20.0)
    a = p.parse_args()
    if a.cmd == "build":
        build(a.runs)
    else:
        clips(a.lead)


if __name__ == "__main__":
    main()
