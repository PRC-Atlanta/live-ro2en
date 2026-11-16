#!/usr/bin/env python3
"""Ceiling check: what the errors left in the Romanian transcript could be fixed by.

  .venv-nemo/bin/python scripts/stt_ceiling.py nemo SERMON
      the offline segments of stt_replay.py (cut at pauses, <= 20 s), and for each one the live
      recogniser's whole candidate list (CTC + 4-gram LM + church phrases, beam 8), the same
      with beam 32, and what the model's other decoder (TDT, greedy) hears from the same
      encoder output. -> logs/bench/stt/ceil-<sermon>.nemo.json
  .venv-mlx/bin/python scripts/stt_ceiling.py mlx SERMON
      Whisper large-v3 and Parakeet on the same segments. -> ceil-<sermon>.mlx.json
  .venv/bin/python scripts/stt_ceiling.py score SERMON [SERMON ...] [--live RUN ...] [--list FILE]
      WER of each, the best any choice among candidates could do (oracle: the candidate with
      the fewest errors, picked with the reference in hand, so a ceiling and not a result),
      what plain votes really give, which of the live recogniser's wrong words another
      recogniser has right, and the errors by kind. --live RUN (a stt_replay.py live run of the
      same sermon, in the order of the sermons): the same question for the live transcript's
      wrong words.

Nothing here changes the app. Scores are on tokens() (as bench.py ref and stt_replay.py wer)."""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "logs" / "bench" / "stt"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "live"))
sys.path.insert(0, str(REPO / "scripts"))
SR = 16000


def _audio(sermon: str) -> tuple[pathlib.Path, float]:
    p = REPO / "logs" / "bench" / "audio" / f"corr_{sermon}.wav"
    side = p.with_suffix(".json")
    return p, (json.loads(side.read_text())["start_wall"] if side.exists() else 0.0)


# --- collecting ---------------------------------------------------------------------------

def _candidates(stt, audio: np.ndarray, tdt: bool) -> tuple[list[tuple[str, float]], str | None]:
    """The beam search's whole list for one piece of audio, best first, and the TDT decoder's text."""
    torch, m = stt.torch, stt.m
    dec = m.ctc_decoding
    search = dec.decoding.search_algorithm
    with stt.lock, torch.inference_mode():
        sig = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0).to(stt.dev)
        ln = torch.tensor([len(audio)], device=stt.dev)
        p, pl = m.preprocessor(input_signal=sig, length=ln)
        e, el = m.encoder(audio_signal=p, length=pl)
        e, el = e.float().cpu(), el.cpu()
        lp = m.ctc_decoder(encoder_output=e)
        nbest = search(lp, el).to_nbest_hyps_list(score_norm=False)[0]
        fold = getattr(dec.decoding, "override_fold_consecutive_value", None)
        out = []
        for h in nbest.n_best_hypotheses:
            score = float(h.score)
            out.append((dec.decode_hypothesis([h], True if fold is None else fold)[0].text, score))
        other = None
        if tdt:
            h = m.decoding.rnnt_decoder_predictions_tensor(encoder_output=e, encoded_lengths=el)
            h = h[0] if isinstance(h, tuple) else h
            h = h[0]
            other = h if isinstance(h, str) else h.text
    return out, other


def nemo(a) -> None:
    import engine
    from batch_transcribe import segment
    from vad import SileroVAD
    path, t0 = _audio(a.sermon)
    x = engine.read_audio(str(path))
    segs = segment(x, SileroVAD(), max_s=20.0)
    if a.limit:
        segs = segs[:a.limit]
    b8 = engine.NemoSTT("jackrabbit-ctc-lm-b8")
    b8.set_names(list(engine.CHURCH_PHRASES), engine.NAME_BOOST)
    b32 = engine.NemoSTT("jackrabbit-ctc-lm")
    b32.set_names(list(engine.CHURCH_PHRASES), engine.NAME_BOOST)
    rows, t, same = [], time.time(), 0
    for i, (s, e) in enumerate(segs):
        c8, tdt = _candidates(b8, x[s:e], True)
        c32, _ = _candidates(b32, x[s:e], False)
        if i < 25:                                        # the list's first entry is what live shows
            same += (c8[0][0] if c8 else "") == (b8(x[s:e]) or "")
        rows.append({"s": s, "e": e, "j8": c8, "j32": c32, "tdt": tdt or ""})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"ceil-{a.sermon}.nemo.json").write_text(json.dumps(
        {"sermon": a.sermon, "t0": t0, "segs": rows, "wall_s": round(time.time() - t, 1)}, ensure_ascii=False))
    print(f"{a.sermon}: {len(rows)} segments in {time.time() - t:.0f} s; first candidate = the recogniser's "
          f"own text on {same}/{min(25, len(rows))} checked")


def mlx(a) -> None:
    import mlx.core as mx
    import mlx_whisper
    import soundfile as sf
    from parakeet_mlx import from_pretrained
    from parakeet_mlx.audio import get_logmel
    path, _ = _audio(a.sermon)
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    assert sr == SR, sr
    x = np.ascontiguousarray(x.mean(1), dtype=np.float32)
    segs = json.loads((OUT / f"ceil-{a.sermon}.nemo.json").read_text())["segs"]
    if a.limit:
        segs = segs[:a.limit]
    pk = from_pretrained("mlx-community/parakeet-tdt-0.6b-v3")
    cfg = pk.preprocessor_config
    rows, t = [], time.time()
    for g in segs:
        au = x[g["s"]:g["e"]]
        w = mlx_whisper.transcribe(au, path_or_hf_repo="mlx-community/whisper-large-v3-mlx", language="ro",
                                   condition_on_previous_text=False)["text"]
        p = pk.generate(get_logmel(mx.array(np.clip(au, -1.0, 1.0)), cfg))[0].text if len(au) >= cfg.hop_length else ""
        rows.append({"s": g["s"], "e": g["e"], "whisper": w.strip(), "parakeet": p.strip()})
    (OUT / f"ceil-{a.sermon}.mlx.json").write_text(json.dumps(
        {"sermon": a.sermon, "segs": rows, "wall_s": round(time.time() - t, 1)}, ensure_ascii=False))
    print(f"{a.sermon}: {len(rows)} segments in {time.time() - t:.0f} s")


# --- scoring ------------------------------------------------------------------------------

def _dist(ref: list[str], hyp: list[str]) -> int:
    from rapidfuzz.distance import Levenshtein
    return Levenshtein.distance(ref, hyp)


def _walk(ref: list[str], hyp: list[str]):
    """Per reference word: None (right), the wrong word heard, or "" (missing); the extra words
    as (reference position they come before, word, hyp position); per hyp word, how many
    reference words came before it."""
    from scoring import align
    state: list = [None] * len(ref)
    extra, before = [], []
    i = 0
    for r, h in align(ref, hyp, free_ends=False).pairs:
        if h is not None:
            before.append(i)
        if r is None:
            extra.append((i, h, len(before) - 1))
            continue
        state[i] = None if r == h else (h or "")
        i += 1
    return state, extra, before


def _vote(back: list[str], others: list[list[str]]) -> list[str]:
    """Word vote with a fixed backbone: a backbone word changes (or goes, or a word is added)
    only when more than half of all voters say the same other thing."""
    from scoring import align
    slots = [[t] for t in back]
    gaps = [[] for _ in range(len(back) + 1)]
    for h in others:
        k, ins = 0, []
        for r, t in align(back, h, free_ends=False).pairs:
            if r is None:
                ins.append(t)
                continue
            if ins:
                gaps[k].append(" ".join(ins)); ins = []
            slots[k].append(t)
            k += 1
        if ins:
            gaps[k].append(" ".join(ins))
    n, out = len(others) + 1, []
    for k in range(len(back) + 1):
        if gaps[k]:
            cand, cnt = collections.Counter(gaps[k]).most_common(1)[0]
            if cnt > n / 2:
                out += cand.split()
        if k < len(back):
            best, cnt = collections.Counter(slots[k]).most_common(1)[0]
            if cnt <= n / 2:
                best = back[k]
            if best is not None:
                out.append(best)
    return out


def _kind(r: str | None, h: str | None, names: set[str], short: set[str]) -> str:
    from textnorm import fold_diacritics
    from rapidfuzz.distance import Levenshtein
    num = lambda w: bool(w) and (w.isdigit() or w in NUMS)       # noqa: E731
    small = lambda w: bool(w) and (len(w) <= 3 or w in short)    # noqa: E731
    if r is None:
        return "extra: short word" if small(h) else "extra: other word"
    if not h:
        return "missing: short word" if small(r) else "missing: other word"
    if num(r) or num(h):
        return "number"
    if r in names:
        return "name"
    if fold_diacritics(r) == fold_diacritics(h):
        return "diacritics only"
    if small(r) and small(h):
        return "short word for short word"
    pre = 0
    while pre < min(len(r), len(h)) and r[pre] == h[pre]:
        pre += 1
    if pre >= 3 and max(len(r), len(h)) - pre <= 3:
        return "word ending"
    if Levenshtein.normalized_similarity(r, h) >= 0.6:
        return "sounds alike"
    return "different word"


NUMS = set("""unu una doi două trei patru cinci șase șapte opt nouă zece unsprezece doisprezece douăsprezece
treisprezece paisprezece cincisprezece șaisprezece șaptesprezece optsprezece nouăsprezece douăzeci treizeci
patruzeci cincizeci șaizeci șaptezeci optzeci nouăzeci sută sute mie mii""".split())


def score(a) -> None:
    import re
    import bench
    from segmenter import DANGLING, NEGATION_END
    from textnorm import tokens
    short = {t for w in (set(DANGLING) | set(NEGATION_END)) for t in tokens(w)}
    total = collections.Counter()
    kinds_all, pairs_all, lines = collections.Counter(), collections.Counter(), []
    for si, sermon in enumerate(a.sermons):
        ne = json.loads((OUT / f"ceil-{sermon}.nemo.json").read_text())
        mp = OUT / f"ceil-{sermon}.mlx.json"
        ml = json.loads(mp.read_text())["segs"] if mp.exists() else None
        ref_lines, end = bench.reference(sermon)
        raw = " ".join(x["ro"] for x in ref_lines)
        ref = tokens(raw)
        names = {t for m in re.finditer(r"(?<![.!?…:„\"«]\s)(?<!^)\b([A-ZĂÂÎȘȚ][a-zăâîșț]+)", raw) for t in tokens(m.group(1))}
        names -= short
        segs, unk = [], 0
        for k, g in enumerate(ne["segs"]):
            if end is not None and (g["s"] + g["e"]) / 2 / SR >= end:
                continue
            c = {"j8n": [tokens(t) for t, _ in g["j8"]] or [[]], "j32n": [tokens(t) for t, _ in g["j32"]] or [[]],
                 "tdt": tokens(g["tdt"])}
            c["j8"], c["j32"] = c["j8n"][0], c["j32n"][0]
            if ml:
                assert ml[k]["s"] == g["s"], "the mlx run is of other segments"
                # Parakeet now and then answers with hundreds of <unk>: not words
                c["whisper"], c["parakeet"] = tokens(ml[k]["whisper"]), tokens(ml[k]["parakeet"].replace("<unk>", " "))
                unk += "<unk>" in ml[k]["parakeet"]
            segs.append(c)
        systems = ["j8", "j32", "tdt"] + (["whisper", "parakeet"] if ml else [])
        flat = {s: [t for c in segs for t in c[s]] for s in systems}
        walk = {s: _walk(ref, flat[s]) for s in systems}
        # each segment's stretch of the reference, from where the live recogniser's own words fall
        state, extra, before = walk["j8"]
        starts, pos = [], 0
        for c in segs:
            starts.append(before[pos] if pos < len(before) else len(ref))
            pos += len(c["j8"])
        starts[0] = 0
        spans = [ref[s:e] for s, e in zip(starts, starts[1:] + [len(ref)])]
        n = len(ref)

        def seg_errors(pick) -> int:
            return sum(min(_dist(sp, cand) for cand in pick(c)) for sp, c in zip(spans, segs))

        def whole(words: list[str]) -> int:
            return _dist(ref, words)

        rows = [("live recogniser (beam 8)", whole(flat["j8"])), ("  beam 32", whole(flat["j32"])),
                ("  other decoder (TDT)", whole(flat["tdt"]))]
        if ml:
            rows += [("Whisper large-v3", whole(flat["whisper"])), ("Parakeet", whole(flat["parakeet"]))]
        base = seg_errors(lambda c: [c["j8"]])
        rows += [("-- ceilings (best candidate picked with the reference in hand), by segment --", None),
                 ("live recogniser, by segment", base),
                 ("best of its beam-8 list", seg_errors(lambda c: c["j8n"])),
                 ("best of its beam-32 list", seg_errors(lambda c: c["j32n"])),
                 ("best of beam 8 or other decoder", seg_errors(lambda c: [c["j8"], c["tdt"]]))]
        if ml:
            rows += [("best of beam 8 or Whisper", seg_errors(lambda c: [c["j8"], c["whisper"]])),
                     ("best of beam 8 or Parakeet", seg_errors(lambda c: [c["j8"], c["parakeet"]])),
                     ("best of beam 8, TDT, Whisper, Parakeet", seg_errors(lambda c: [c["j8"], c["tdt"], c["whisper"], c["parakeet"]])),
                     ("best of all lists and recognisers", seg_errors(lambda c: c["j8n"] + c["j32n"] + [c["tdt"], c["whisper"], c["parakeet"]]))]
            rows += [("-- real votes, word by word, live recogniser as backbone --", None),
                     ("vote: beam 8 + TDT + Whisper", whole([t for c in segs for t in _vote(c["j8"], [c["tdt"], c["whisper"]])])),
                     ("vote: beam 8 + Whisper + Parakeet", whole([t for c in segs for t in _vote(c["j8"], [c["whisper"], c["parakeet"]])])),
                     ("vote: beam 8 + TDT + Whisper + Parakeet (3 of 4)", whole([t for c in segs for t in _vote(c["j8"], [c["tdt"], c["whisper"], c["parakeet"]])])),
                     ("vote: beam 8 + TDT + Parakeet", whole([t for c in segs for t in _vote(c["j8"], [c["tdt"], c["parakeet"]])]))]
        if ml:
            def near(c, pool, judges):            # the candidate nearest to what the judges heard; ties: the list's order
                return min(c[pool], key=lambda cand: sum(_dist(cand, c[j]) for j in judges))

            def pick(pool, judges):
                return whole([t for c in segs for t in near(c, pool, judges)])
            rows += [("-- real choices: the candidate nearest to another recogniser's text --", None),
                     ("beam-8 list, nearest to Whisper", pick("j8n", ["whisper"])),
                     ("beam-32 list, nearest to Whisper", pick("j32n", ["whisper"])),
                     ("beam-8 list, nearest to TDT", pick("j8n", ["tdt"])),
                     ("beam-32 list, nearest to TDT", pick("j32n", ["tdt"])),
                     ("beam-8 list, nearest to Whisper + TDT", pick("j8n", ["whisper", "tdt"])),
                     ("beam-32 list, nearest to Whisper + TDT", pick("j32n", ["whisper", "tdt"])),
                     ("beam-32 list, nearest to Whisper + TDT + Parakeet", pick("j32n", ["whisper", "tdt", "parakeet"])),
                     ("beam 8 or Whisper, whichever TDT is nearer to",
                      whole([t for c in segs for t in min([c["j8"], c["whisper"]], key=lambda x: _dist(x, c["tdt"]))]))]
        print(f"\n=== {sermon}: {n} reference words, {len(segs)} segments ===")
        if ml and unk:
            print(f"  Parakeet answered <unk>… on {unk} segments (dropped from its text here)")
        for label, err in rows:
            if err is None:
                print(label)
                continue
            print(f"  {label:50s} {err:4d} errors  {err / n * 100:5.2f}%")
            total[label] += err
        total["words"] += n
        # where the right candidate sits in the list
        rank = collections.Counter()
        for sp, c in zip(spans, segs):
            d = [_dist(sp, cand) for cand in c["j8n"]]
            if min(d) < d[0]:
                rank[d.index(min(d)) + 1] += d[0] - min(d)
        print("  beam-8 list: errors saved by candidate rank " + ", ".join(f"#{k}: {v}" for k, v in sorted(rank.items())))
        print(f"  beam-8 list: {np.mean([len({' '.join(x) for x in c['j8n']}) for c in segs]):.1f} different texts per segment")
        # which of the live recogniser's wrong words another has right
        wrong = [i for i, s in enumerate(state) if s is not None]
        print(f"  the live recogniser's wrong or missing words: {len(wrong)}, extra words: {len(extra)}")
        others = [s for s in systems if s != "j8"]
        for s in others:
            ok = sum(walk[s][0][i] is None for i in wrong)
            lost = sum(walk[s][0][i] is not None for i in range(n) if state[i] is None)
            print(f"    {s:9s} has {ok:3d} of them right ({ok / max(len(wrong), 1) * 100:3.0f}%), and has {lost:3d} wrong that the live recogniser has right")
        anyone = sum(any(walk[s][0][i] is None for s in others if s != "j32") for i in wrong)
        print(f"    any other recogniser (TDT, Whisper, Parakeet): {anyone} ({anyone / max(len(wrong), 1) * 100:.0f}%)")
        kinds = collections.Counter()
        for i in wrong:
            k = _kind(ref[i], state[i], names, short)
            kinds[k] += 1
            pairs_all[(ref[i], state[i] or "—")] += 1
            lines.append(f"{sermon}\t{k}\t{ref[i]}\t{state[i] or '—'}\t" + " ".join(ref[max(0, i - 4):i + 5])
                         + "\t" + ",".join(s for s in others if walk[s][0][i] is None))
        for i, h, _ in extra:
            k = _kind(None, h, names, short)
            kinds[k] += 1
            pairs_all[("—", h)] += 1
            lines.append(f"{sermon}\t{k}\t—\t{h}\t" + " ".join(ref[max(0, i - 4):i + 4]) + "\t")
        kinds_all.update(kinds)
        print("  by kind: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common()))
        if a.live and si < len(a.live):
            recs = [json.loads(x) for x in (OUT / f"{a.live[si]}.jsonl").open() if x.strip()]
            info = json.loads((OUT / f"{a.live[si]}.json").read_text())
            recs = [r for r in recs if end is None or (r["t_start"] + r["t_end"]) / 2 - info["t0"] < end]
            lv = tokens(" ".join(r.get("ro") or "" for r in recs))
            ls, lx, _ = _walk(ref, lv)
            lw = [i for i, s in enumerate(ls) if s is not None]
            print(f"  live transcript {a.live[si]}: {_dist(ref, lv)} errors ({_dist(ref, lv) / n * 100:.2f}%), "
                  f"{len(lw)} wrong or missing words, {len(lx)} extra")
            for s in systems:
                ok = sum(walk[s][0][i] is None for i in lw)
                print(f"    {s:9s} (on whole segments) has {ok:3d} of them right ({ok / max(len(lw), 1) * 100:3.0f}%)")
    if len(a.sermons) > 1:
        n = total.pop("words")
        print(f"\n=== all {len(a.sermons)} sermons: {n} reference words ===")
        for label, err in total.items():
            print(f"  {label:50s} {err:4d} errors  {err / n * 100:5.2f}%")
        print("  by kind: " + ", ".join(f"{k} {v}" for k, v in kinds_all.most_common()))
        print("  most frequent: " + ", ".join(f"{r}→{h} ×{c}" for (r, h), c in pairs_all.most_common(25)))
    if a.list:
        pathlib.Path(a.list).write_text("sermon\tkind\treference\theard\tcontext\tright in\n" + "\n".join(lines) + "\n")
        print(f"\n{len(lines)} errors listed in {a.list}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("nemo", "mlx"):
        p = sub.add_parser(name)
        p.add_argument("sermon")
        p.add_argument("--limit", type=int, default=0)
    s = sub.add_parser("score")
    s.add_argument("sermons", nargs="+")
    s.add_argument("--live", nargs="*")
    s.add_argument("--list")
    a = ap.parse_args()
    {"nemo": nemo, "mlx": mlx, "score": score}[a.cmd](a)


if __name__ == "__main__":
    main()
