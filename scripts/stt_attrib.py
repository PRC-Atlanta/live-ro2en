#!/usr/bin/env python3
"""Where do the live Romanian transcript's extra errors come from? (idea 25)

  scripts/stt_attrib.py LIVE OFFLINE [--app RUN] [--list FILE] [--edge 2]

LIVE and OFFLINE are recogniser-only replays of one corrected sermon (scripts/stt_replay.py live /
offline). Both are aligned to the corrected Romanian transcript; an error the live replay has where
the offline one is right is caused by the live cutting and stitching, and is classed by where it
sits in the live output:
  seam-forced   within --edge words of a boundary made by a forced cut (mid-speech)
  seam-pause    within --edge words of a boundary made at a pause
  inside        elsewhere in a chunk
Each class is split into repeat (an inserted word that the previous words already hold: stitched
twice), cut (a deleted or changed word at a boundary) and other. Errors both have are the
recogniser's own (or the reference's); errors only offline has are what the live context fixed.
Scoring noise is counted apart: pairs equal on the phonetic key with diacritics folded, digits.

--app RUN (a bench.py run of the same sermon): what the app's pipeline did after the recogniser:
the recogniser's text per phrase (ro) against the repaired text (source), and what was not shown."""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
import bench                                                    # noqa: E402
import stt_replay                                               # noqa: E402
from scoring import align                                       # noqa: E402
from textnorm import fold_diacritics, phon_key, tokens          # noqa: E402


def _nkey(w: str) -> str:
    return fold_diacritics(phon_key(w))


def noise(r: str | None, h: str | None) -> bool:
    """A scoring difference, not a hearing one: same word on the phonetic key, or a figure."""
    if r is None or h is None:
        return (r or h or "").isdigit()
    return _nkey(r) == _nkey(h) or r.isdigit() or h.isdigit()


def walk(ref: list[str], hyp: list[str]) -> list[dict]:
    """The alignment as a list of errors: {kind S|D|I, ri (ref index; for I the next ref word),
    hi (hyp index; for D the next hyp word), r, h}."""
    a = align(ref, hyp, free_ends=False)
    out, ri, hi = [], 0, 0
    for r, h in a.pairs:
        if r is not None and h is not None:
            if r != h:
                out.append({"kind": "S", "ri": ri, "hi": hi, "r": r, "h": h})
            ri, hi = ri + 1, hi + 1
        elif r is None:
            out.append({"kind": "I", "ri": ri, "hi": hi, "r": None, "h": h})
            hi += 1
        else:
            out.append({"kind": "D", "ri": ri, "hi": hi, "r": r, "h": None})
            ri += 1
    return out


def clause_tokens(recs: list[dict], field: str = "ro") -> tuple[list[str], list[int], list[int], list[int]]:
    """tokens, the clause each belongs to, its position in the clause, the clause's token count."""
    toks, owner, pos, size = [], [], [], []
    for ci, r in enumerate(recs):
        ws = tokens(r.get(field) or "")
        for k, w in enumerate(ws):
            toks.append(w)
            owner.append(ci)
            pos.append(k)
            size.append(len(ws))
    return toks, owner, pos, size


def sermon_of(info: dict) -> str:
    return pathlib.Path(info["audio"]).stem.removeprefix("corr_")


def attribute(live_name: str, off_name: str, edge: int = 2) -> dict:
    live, linfo = stt_replay.load(live_name)
    off, oinfo = stt_replay.load(off_name)
    sermon = sermon_of(linfo)
    lines, end = bench.reference(sermon)
    t0 = linfo["t0"]
    # the corrected stretch ends at `end`: a clause counts when its middle lies before it (bench.py ref
    # takes every phrase that starts up to 1 s after it, which adds that phrase's words as extra ones)
    live = [r for r in live if end is None or (r["t_start"] + r["t_end"]) / 2 - t0 < end]
    off = [r for r in off if end is None or (r["t_start"] + r["t_end"]) / 2 - oinfo["t0"] < end]
    ref = [w for x in lines for w in tokens(x["ro"])]
    lt, owner, pos, size = clause_tokens(live)
    ot = clause_tokens(off)[0]
    le, oe = walk(ref, lt), walk(ref, ot)
    off_bad = collections.Counter()                     # ref index -> offline errors there
    for e in oe:
        off_bad[e["ri"]] += 1
    live_bad = collections.Counter(e["ri"] for e in le)
    forced_after = [not (r.get("trace") or {}).get("final", True) for r in live]   # clause ended by a forced cut

    def place(e: dict) -> tuple[str, int]:
        """(class, clause index of the boundary or the clause)."""
        hi = min(e["hi"], len(lt) - 1)
        if e["kind"] == "D":
            # between hyp words hi-1 and hi
            a, b = max(hi - 1, 0), hi
            if owner[a] != owner[b]:
                return ("seam-forced" if forced_after[owner[a]] else "seam-pause"), owner[a]
            c = owner[b]
            if pos[b] < edge and c > 0:
                return ("seam-forced" if forced_after[c - 1] else "seam-pause"), c - 1
            if size[a] - pos[a] <= edge and owner[a] < len(live) - 1:
                return ("seam-forced" if forced_after[owner[a]] else "seam-pause"), owner[a]
            return "inside", c
        c = owner[hi]
        if pos[hi] < edge and c > 0:
            return ("seam-forced" if forced_after[c - 1] else "seam-pause"), c - 1
        if size[hi] - pos[hi] <= edge and c < len(live) - 1:
            return ("seam-forced" if forced_after[c] else "seam-pause"), c
        return "inside", c

    def how(e: dict) -> str:
        if e["kind"] == "I":
            hi = e["hi"]
            before = [_nkey(w) for w in lt[max(0, hi - 4):hi]]
            after = [_nkey(w) for w in lt[hi + 1:hi + 3]]
            k = _nkey(e["h"])
            if k in before or k in after:
                return "repeat"
            if any(len(k) >= 3 and (x.startswith(k[:3])) for x in before[-2:] + after[:1]):
                return "stub"                           # a word and its cut-off start, both kept
        return "other"

    rows = []
    for e in le:
        shared = any(off_bad[i] for i in (e["ri"] - 1, e["ri"], e["ri"] + 1))
        cls, c = place(e)
        tail = e["kind"] == "I" and e["ri"] >= len(ref)          # past the end of the corrected stretch
        rows.append({**e, "noise": noise(e["r"], e["h"]) or tail, "shared": shared, "cls": cls, "clause": c,
                     "how": how(e)})
    off_only = [e for e in oe if not any(live_bad[i] for i in (e["ri"] - 1, e["ri"], e["ri"] + 1))]
    n_edge = sum(1 for i in range(len(lt)) if (pos[i] < edge and owner[i] > 0)
                 or (size[i] - pos[i] <= edge and owner[i] < len(live) - 1))
    n_forced_edge = sum(1 for i in range(len(lt)) if (pos[i] < edge and owner[i] > 0 and forced_after[owner[i] - 1])
                        or (size[i] - pos[i] <= edge and owner[i] < len(live) - 1 and forced_after[owner[i]]))
    return {"sermon": sermon, "t0": t0, "ref": ref, "live": live, "lt": lt, "owner": owner, "pos": pos, "rows": rows,
            "off_errors": oe, "off_only": off_only, "n_ref": len(ref), "forced_after": forced_after,
            "edge_share": n_edge / max(len(lt), 1), "forced_edge_share": n_forced_edge / max(len(lt), 1),
            "boundaries": {"forced": sum(forced_after[:-1]), "pause": len(live) - 1 - sum(forced_after[:-1])}}


def _sdi(es) -> str:
    c = collections.Counter(e["kind"] for e in es)
    return f"{len(es):4d} (S{c['S']} D{c['D']} I{c['I']})"


def report(res: dict, list_path: str | None) -> None:
    rows, n = res["rows"], res["n_ref"]
    real = [e for e in rows if not e["noise"]]
    only = [e for e in real if not e["shared"]]
    print(f"{res['sermon']}: {n} reference words; {len(res['live'])} live clauses, "
          f"{res['boundaries']['forced']} forced cuts, {res['boundaries']['pause']} pause boundaries; "
          f"{res['edge_share'] * 100:.0f}% of live words within the edge of a boundary "
          f"({res['forced_edge_share'] * 100:.0f}% of a forced one)")
    print(f"  offline errors          {_sdi(res['off_errors'])}  {len(res['off_errors']) / n * 100:.2f}%")
    print(f"  live errors             {_sdi(rows)}  {len(rows) / n * 100:.2f}%")
    print(f"    scoring noise (e)     {_sdi([e for e in rows if e['noise']])}")
    print(f"    shared with offline   {_sdi([e for e in real if e['shared']])}")
    print(f"    live only             {_sdi(only)}  = {len(only) / n * 100:.2f} points")
    for cls in ("seam-forced", "seam-pause", "inside"):
        es = [e for e in only if e["cls"] == cls]
        hw = collections.Counter(e["how"] for e in es)
        print(f"      {cls:12s}        {_sdi(es)}   repeat {hw['repeat']}, stub {hw['stub']}, other {hw['other']}")
    print(f"  offline only (live right) {_sdi(res['off_only'])}")
    if list_path:
        ref, lt, owner, fa = res["ref"], res["lt"], res["owner"], res["forced_after"]
        out = []
        for e in only:
            hi = min(e["hi"], len(lt) - 1)
            lo, up = max(0, hi - 6), min(len(lt), hi + 7)
            words = []
            for i in range(lo, up):
                if i > lo and owner[i] != owner[i - 1]:
                    words.append("‖" if fa[owner[i - 1]] else "|")
                words.append(f"[{lt[i]}]" if i == e["hi"] and e["kind"] != "D" else lt[i])
            t = res["live"][owner[hi]]["t_start"]
            out.append(f"{e['cls']:12s} {e['kind']} {e['how']:6s} ref «{' '.join(ref[max(0, e['ri'] - 5):e['ri']])} "
                       f"[{e['r'] or '∅'}] {' '.join(ref[e['ri'] + 1:e['ri'] + 5])}»\n"
                       f"{'':21s} live «{' '.join(words)}»  @{bench._mmss(t - res["t0"])}")
        pathlib.Path(list_path).write_text("\n".join(out) + "\n")
        print(f"  live-only errors listed in {list_path}")


def app_side(run: str) -> None:
    """The pipeline after the recogniser: repairs (ro -> source) and phrases not shown."""
    log, info = bench._run_log(run)
    sermon = sermon_of(info)
    lines, end = bench.reference(sermon)
    t0 = info["segment_start_wall"]
    ph = [r for r in bench._records(log) if r.get("outcome") and "t_start" in r
          and (end is None or r["t_start"] - t0 < end + 1.0)]
    by_id = {r.get("id"): r for r in bench.on_screen(ph)}
    kept = [r for r in ph if not (by_id[r.get("id")].get("outcome") == "replaced"
                                  and by_id.get(by_id[r.get("id")]["replaced_by"], {}).get("cut_word"))]
    ref = [w for x in lines for w in tokens(x["ro"])]

    def errs(recs, field):
        return walk(ref, [w for r in recs for w in tokens(r.get(field) or "")])
    raw = errs(ph, "ro")                                  # the recogniser's words, every phrase
    src = errs(kept, "source") if all("source" in r or r["outcome"] != "ok" for r in kept) else None
    src_all = walk(ref, [w for r in kept for w in tokens(r.get("source") or r.get("ro") or "")])
    shown = walk(ref, [w for r in kept if r["outcome"] == "ok" for w in tokens(r.get("source") or r.get("ro") or "")])
    n = len(ref)
    print(f"{sermon} app run {run} ({info.get('git')}): {len(ph)} phrases")
    print(f"  recogniser text (ro), all phrases        {_sdi(raw)}  {len(raw) / n * 100:.2f}%")
    print(f"  after the repairs (source), all phrases  {_sdi(src_all)}  {len(src_all) / n * 100:.2f}%")
    print(f"  shown only                               {_sdi(shown)}  {len(shown) / n * 100:.2f}%")
    outc = collections.Counter(r["outcome"] for r in ph)
    lang = collections.Counter(r.get("lang") for r in ph if r["outcome"] == "ok")
    words_lost = {k: sum(len(tokens(r.get("source") or r.get("ro") or "")) for r in ph if r["outcome"] == k)
                  for k in outc if k != "ok"}
    print(f"  outcomes {dict(outc)}; words in phrases not shown {words_lost}; shown language {dict(lang)}")
    flags = collections.Counter()
    changed = []
    for r in ph:
        if r["outcome"] != "ok":
            continue
        for k in ("names_fixed", "numbers_unglued", "ne_split", "month_days", "numbers_longer", "christmas",
                  "cut_word", "gate_cut", "gate_switched", "fallback", "overlap_trimmed", "replaced_prev"):
            if r.get(k):
                flags[k] += 1
        a, b = tokens(r.get("ro") or ""), tokens(r.get("source") or "")
        if a != b and not r.get("cut_word"):
            changed.append((r.get("ro"), r.get("source")))
    print(f"  repair flags on shown phrases: {dict(flags)}; phrases whose words changed (no redo): {len(changed)}")
    for a, b in changed[:40]:
        da = [w for w in tokens(a or "") if w not in tokens(b or "")]
        db = [w for w in tokens(b or "") if w not in tokens(a or "")]
        print(f"      {da} -> {db}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("live", nargs="?")
    ap.add_argument("offline", nargs="?")
    ap.add_argument("--app")
    ap.add_argument("--list")
    ap.add_argument("--edge", type=int, default=2)
    a = ap.parse_args()
    if a.live and a.offline:
        report(attribute(a.live, a.offline, a.edge), a.list)
    if a.app:
        app_side(a.app)


if __name__ == "__main__":
    main()
