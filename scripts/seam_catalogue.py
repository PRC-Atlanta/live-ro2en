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
in it), so the clips can be checked against the full replay they stand for.

  scripts/seam_catalogue.py lost RUN [RUN ...] [--all]
                                                  the joint census: words of the corrected transcript
                                                  missing near a cut (a word lost at a joint), short
                                                  words only unless --all -> logs/bench/lost.json
Each lost word gets a cause:
  unheard   no recogniser pass has it (the cut swallowed it)
  reheard   the next pass heard it again in the committed tail, but it was not taken (the
            alignment cut it, or it was never committed): a re-hearing check would get it back
  unknown   the run's log predates the `reheard` field
  filtered:<outcome>   recognised, but its piece was not shown (echo_word, scrap, low_confidence…)
  edited    recognised in a shown piece, then removed (repair, overlap trim, cut-word redo)"""
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
LOST = bench.BENCH / "lost.json"
NEAR = 1                                # a word within this many words of a cut counts as at the joint
# Romanian words of three letters or more that are still short function words (folded)
FUNCTION = {"lui", "sau", "dar", "iar", "nici", "mai", "este", "era", "sunt", "din", "prin", "spre",
            "care", "cum", "cand", "unde", "pentru", "dupa", "fara", "imi", "isi", "ita", "una", "unui",
            "unei", "ale", "cel", "cea", "cei", "cele", "acest", "asta", "fie", "fost", "avem", "aveti"}
CLIPS = bench.BENCH / "audio" / "seamclips.wav"
CLIPS_START = "2026-10-23T10:00:00"     # its own day: the corrected sermons all start 2026-10-22 10:00
GAP_S = 8.0                             # silence between clips: longer than the 6 s context
TAIL_S = 8.0
_WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")
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


def short(w: str) -> bool:
    k = _key(w)
    return len(k) <= 3 or k in FUNCTION


def ro_stream(recs: list[dict]) -> tuple[list[str], list[int], dict[int, dict]]:
    """The Romanian transcript as recognised, every piece in order (shown or not): its tokens, the
    record each came from, and the joints {token index where a chunk starts: {gap, reheard, kind}}."""
    hyp, owner, joints = [], [], {}
    prev_end = None
    for i, r in enumerate(recs):
        words = (r.get("ro") or "").split()
        if not words:
            continue
        inner = {j["at"]: j for j in r.get("joints") or [] if 0 < j["at"] < len(words)}
        if prev_end is not None:
            joints[len(hyp)] = {"gap": round(r.get("t_speech", r["t_start"]) - prev_end, 3),
                                "reheard": r.get("reheard"), "kind": "piece", "rec": i}
        for wi, w in enumerate(words):
            if wi in inner:
                joints[len(hyp)] = {"gap": inner[wi].get("gap"), "reheard": inner[wi].get("reheard"),
                                    "kind": "chunk", "rec": i}
            for t in tokens(w):
                hyp.append(t)
                owner.append(i)
        prev_end = r["t_end"]
    return hyp, owner, joints


def ref_map(ref: list[str], hyp: list[str]) -> list[tuple[int | None, int]]:
    """For each reference token: (the hyp token it is matched or substituted with, None if deleted;
    the hyp slot it sits at: the index of the next hyp token)."""
    a = align(ref, hyp, free_ends=True)
    out: list[tuple[int | None, int]] = [(None, 0)] * len(ref)
    ri, hi = a.ref_start, 0
    for r, h in a.pairs:
        if r is None:
            hi += 1
            continue
        out[ri] = (hi if h is not None else None, hi)
        ri += 1
        hi += h is not None
    return out


def _spacing(ref: list[str], k: int, hyp: list[str], slot: int) -> bool:
    """The reference word and its neighbour were heard as one word (or the other way round)."""
    near = {_key(t) for t in hyp[max(0, slot - 1):slot + 1]}
    joined = {_key(ref[k - 1] + ref[k]) if k else None, _key(ref[k] + ref[k + 1]) if k + 1 < len(ref) else None}
    return bool(near & joined)


def near_joint(pos: int, joints: dict, deleted: bool) -> int | None:
    """The joint (token index where a chunk starts) within NEAR words of a hyp token, or of a
    deletion slot (between pos - 1 and pos); None if there is none."""
    best = None
    for d in range(NEAR + 1):
        for j in ((pos - d, pos + d) if deleted else (pos + 1 + d, pos - d)):
            if j in joints:
                return j
    return best


def lost(runs: list[str], all_words: bool) -> None:
    entries, n = [], {"ref_near_mid": 0, "lost_near_mid": 0, "ref_near_pause": 0, "lost_near_pause": 0,
                      "ref_away": 0, "lost_away": 0, "joints_mid": 0, "joints_pause": 0}
    for run in runs:
        log, info = bench._run_log(run)
        if not log.exists():
            log = next(((bench.BENCH / "runs" / run) / "services").glob("*.jsonl"))
        sermon = pathlib.Path(info["audio"]).stem.removeprefix("corr_")
        if sermon not in bench.SERMONS:
            sys.exit(f"{run}: not a corrected-sermon run")
        lines, end = bench.reference(sermon)
        t0 = info["segment_start_wall"]
        recs = bench.on_screen(sorted(
            (r for r in bench._records(log) if r.get("outcome") and "t_start" in r
             and (end is None or r["t_start"] - t0 < end + 1.0)), key=lambda r: r["t_start"]))
        has_reheard = any(r.get("reheard") for r in recs)
        ref = [w for x in lines for w in tokens(x["ro"])]
        hyp, owner, joints = ro_stream(recs)
        mid = {j for j, v in joints.items() if v["gap"] is not None and v["gap"] <= CUT_GAP_S}
        n["joints_mid"] += len(mid)
        n["joints_pause"] += len(joints) - len(mid)
        # the shown Romanian transcript, to tell a word recognised but not shown
        # a piece a verse caption took over (`replaced_prev`) was shown, as the verse
        by_id = {r.get("id"): r for r in recs}
        verse_took = {i for i, r in enumerate(recs) if r["outcome"] == "replaced"
                      and (by_id.get(r.get("replaced_by")) or {}).get("replaced_prev")}
        shown, sowner = [], []
        for i, r in enumerate(recs):
            if r["outcome"] == "ok" or i in verse_took:
                for t in tokens(r.get("source") or ""):
                    shown.append(t)
                    sowner.append(i)
        rm, sm = ref_map(ref, hyp), ref_map(ref, shown)
        lo = next((k for k, (h, _) in enumerate(rm) if h is not None), len(ref))
        hi_ = max((k for k, (h, _) in enumerate(rm) if h is not None), default=-1)
        for k in range(lo, hi_ + 1):
            w = ref[k]
            if not all_words and not short(w):
                continue
            h, slot = rm[k]
            j = near_joint(slot if h is None else h, joints, h is None)
            heard = h is not None and _key(hyp[h]) == _key(w)
            on_screen = sm[k][0] is not None and _key(shown[sm[k][0]]) == _key(w)
            where = "away" if j is None else ("near_mid" if j in mid else "near_pause")
            n[f"ref_{where}"] += 1
            if on_screen or (h is not None and not heard and sm[k][0] is not None):
                continue                        # shown, or misheard (a substitution: not a lost word)
            if h is not None and not heard:
                continue
            if h is None and _spacing(ref, k, hyp, slot):
                continue                        # «la odicea» said, «laodicea» heard: not lost
            n[f"lost_{where}"] += 1
            if j is None:
                continue
            jt = joints[j]
            if not heard:
                rh = {_key(t) for t in tokens(jt.get("reheard") or "")}
                cause = ("reheard" if _key(w) in rh else "unheard") if has_reheard else "unknown"
            else:
                o = recs[owner[h]]
                cause = "edited" if o["outcome"] == "ok" else f"filtered:{o['outcome']}"
            at = recs[jt["rec"]]
            ctx_h = hyp[max(0, j - 4):j] + ["|"] + hyp[j:j + 4]
            entries.append({
                "sermon": sermon, "run": run, "word": w, "cause": cause, "joint": where.removeprefix("near_"),
                "kind": jt["kind"], "gap": jt["gap"],
                "t_src": dt.datetime.fromtimestamp(at.get("t_speech", at["t_start"])).strftime("%H:%M:%S"),
                "said": " ".join(ref[max(0, k - 4):k] + [f"[{w}]"] + ref[k + 1:k + 5]),
                "heard": " ".join(ctx_h), **({"reheard": jt["reheard"]} if jt.get("reheard") else {})})
    causes: dict[str, int] = {}
    for e in entries:
        if e["joint"] == "mid":
            causes[e["cause"]] = causes.get(e["cause"], 0) + 1
    rate = {w: round(100 * n[f"lost_{w}"] / n[f"ref_{w}"], 2) if n[f"ref_{w}"] else None
            for w in ("near_mid", "near_pause", "away")}
    doc = {"how": f"words of the corrected transcript ({'all' if all_words else 'short'} words) missing "
                  f"within {NEAR} word(s) of a cut; mid = the next chunk followed within {CUT_GAP_S} s",
           "built_from": runs, "counts": n, "lost_pct": rate, "causes_mid": causes, "lost": entries}
    LOST.write_text(json.dumps(doc, indent=1, ensure_ascii=False))
    kind = "all" if all_words else "short"
    print(f"joints: {n['joints_mid']} mid-speech, {n['joints_pause']} after a pause")
    for w, label in (("near_mid", "near a mid-speech cut"), ("near_pause", "near a cut at a pause"),
                     ("away", "away from cuts")):
        print(f"  {kind} words {label:24} {n[f'lost_{w}']:4} lost of {n[f'ref_{w}']:5}  ({rate[w]} %)")
    print("causes at mid-speech cuts: " + ", ".join(f"{c} {v}" for c, v in sorted(causes.items(), key=lambda x: -x[1])))
    for e in entries:
        if e["joint"] == "mid":
            print(f"  {e['sermon'][:8]:8} {e['t_src']} {e['cause']:22} said «{e['said']}»  heard «{e['heard']}»"
                  + (f"  reheard «{e['reheard']}»" if e.get("reheard") else ""))
    print(f"-> {LOST}")


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
    lo = sub.add_parser("lost")
    lo.add_argument("runs", nargs="+")
    lo.add_argument("--all", action="store_true", help="every word, not just the short ones")
    a = p.parse_args()
    if a.cmd == "build":
        build(a.runs)
    elif a.cmd == "lost":
        lost(a.runs, a.all)
    else:
        clips(a.lead)


if __name__ == "__main__":
    main()
