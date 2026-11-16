#!/usr/bin/env python3
"""Chooser test: can anything pick the right candidate without knowing the answer?

stt_ceiling.py found the right text in the live recogniser's candidate list (or in another
recogniser's output) for a third to two thirds of its errors, and that votes do not find it.
Here each candidate gets two more opinions, and a weighted sum of them picks:

  .venv-nemo/bin/python scripts/stt_choose.py acoustic SERMON
      as sound: the probability the model's other decoder (TDT) and its CTC head (no language
      model) give each candidate's text, from the same encoder output. -> choose-<sermon>.ac.json
  .venv-mt/bin/python scripts/stt_choose.py gemma SERMON
      as language: the probability Gemma gives each candidate after the transcript so far (the
      recogniser's own first choices, as live would have). -> choose-<sermon>.lm.json
  .venv/bin/python scripts/stt_choose.py score SERMON [SERMON ...]
      the weights are tuned on the other sermons and tested on the one left out.

Needs the ceil-<sermon>.nemo.json / .mlx.json of stt_ceiling.py. Nothing here changes the app."""
from __future__ import annotations

import argparse
import collections
import itertools
import json
import pathlib
import sys
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from stt_ceiling import OUT, REPO, SR, _audio, _dist, _walk  # noqa: E402

GEMMA = "mlx-community/gemma-4-12B-it-qat-4bit"
ASK = "Scrie transcrierea unei predici dintr-o biserică evanghelică, în limba română, cuvânt cu cuvânt."
LETTERS = "ABCDEFGH"
PICK = ("Un program de recunoaștere a vorbirii ascultă o predică într-o biserică evanghelică și nu este sigur ce a "
        "spus predicatorul în fraza următoare.\n\nCe s-a spus înainte: «{before}»\n\nVariante pentru fraza următoare:\n")
PICK_END = "\n\nCare variantă a spus-o cel mai probabil predicatorul? Răspunde doar cu litera."


def _end(sermon: str):
    try:
        import bench
        return bench.reference(sermon)[1]
    except Exception:                                     # noqa: BLE001  bench needs the scoring environment
        return None


def _load(sermon: str):
    ne = json.loads((OUT / f"ceil-{sermon}.nemo.json").read_text())["segs"]
    ml = json.loads((OUT / f"ceil-{sermon}.mlx.json").read_text())["segs"]
    assert len(ne) == len(ml) and all(a["s"] == b["s"] for a, b in zip(ne, ml))
    return ne, ml


def _texts(g: dict, w: dict) -> list[tuple[str, str]]:
    """A segment's candidate texts as written, each once, with where it was first seen:
    the beam-8 list, the other decoder, Whisper, then the rest of the beam-32 list."""
    seen, out = set(), []
    for src, t in ([("j8", t) for t, _ in g["j8"]] + [("tdt", g["tdt"]), ("whisper", w["whisper"])]
                   + [("j32", t) for t, _ in g["j32"]]):
        t = t.strip()
        if t and t not in seen:
            seen.add(t)
            out.append((src, t))
    return out


def _mlx_caps(mx) -> None:
    """MLX keeps every freed buffer for reuse, and here every call has another shape: without a
    cap its cache grew past the Mac's memory (2026-11-16, two of these at once: 39 GB of swap)."""
    mx.set_cache_limit(1 << 30)
    mx.set_memory_limit(16 << 30)


def _mem(mx) -> str:
    return f"memory: peak {mx.get_peak_memory() / 2 ** 30:.1f} GB, kept for reuse {mx.get_cache_memory() / 2 ** 30:.1f} GB"


def _skip(g: dict, end) -> bool:
    return end is not None and (g["s"] + g["e"]) / 2 / SR >= end


# --- as sound -----------------------------------------------------------------------------

def acoustic(a) -> None:
    import torch
    import torch.nn.functional as F
    sys.path.insert(0, str(REPO / "src" / "finetune"))
    import engine
    from tdt_loss import tdt_forward_logprob
    x = engine.read_audio(str(_audio(a.sermon)[0]))
    segs, ml = _load(a.sermon)
    end = _end(a.sermon)
    stt = engine.NemoSTT("jackrabbit-ctc-lm-b8")
    m, tok = stt.m, stt.m.tokenizer
    blank = m.decoder.blank_idx
    durations = [int(d) for d in m.decoding.decoding.durations]
    nd = len(durations)
    unk = tok.tokenizer.unk_id()
    rows, t0, done = [], time.time(), 0
    for g, w in zip(segs, ml):
        if _skip(g, end) or (a.limit and done >= a.limit):
            rows.append({})
            continue
        done += 1
        texts = [t for _, t in _texts(g, w)]
        ids = [tok.text_to_ids(t) for t in texts]
        out: dict[str, list] = {}
        with torch.inference_mode():
            au = np.ascontiguousarray(x[g["s"]:g["e"]], dtype=np.float32)
            sig = torch.from_numpy(au).unsqueeze(0).to(stt.dev)
            p, pl = m.preprocessor(input_signal=sig, length=torch.tensor([len(au)], device=stt.dev))
            e, el = m.encoder(audio_signal=p, length=pl)
            e, T = e.float().cpu(), int(el[0])
            e = e[:, :, :T]
            lp = m.ctc_decoder(encoder_output=e)[0]                       # T x V+1, log-probs
            order = sorted((i for i in range(len(ids)) if ids[i] and unk not in ids[i]), key=lambda i: len(ids[i]))
            for at in range(0, len(order), a.batch):
                chunk = order[at:at + a.batch]
                B, U = len(chunk), max(len(ids[i]) for i in chunk)
                lab = torch.zeros(B, U, dtype=torch.long)
                for b, i in enumerate(chunk):
                    lab[b, :len(ids[i])] = torch.tensor(ids[i])
                ll, tl = torch.tensor([len(ids[i]) for i in chunk]), torch.full((B,), T)
                ctc = -F.ctc_loss(lp.unsqueeze(1).expand(T, B, -1), lab, tl, ll, blank=lp.shape[-1] - 1,
                                  reduction="none")
                dec, _, _ = m.decoder(targets=lab, target_length=ll)      # B x D x U+1
                j = m.joint.joint(e.transpose(1, 2).expand(B, -1, -1), dec.transpose(1, 2)).float()
                tdt = tdt_forward_logprob(torch.log_softmax(j[..., :-nd], -1), torch.log_softmax(j[..., -nd:], -1),
                                          lab, tl, ll, blank, durations)
                for b, i in enumerate(chunk):
                    c, d = float(ctc[b]), float(tdt[b])
                    out[texts[i]] = [c if np.isfinite(c) else None, d if d > -1e15 else None, len(ids[i])]
        rows.append(out)
    (OUT / f"choose-{a.sermon}.ac.json").write_text(json.dumps(
        {"sermon": a.sermon, "segs": rows, "wall_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    n = sum(len(r) for r in rows)
    print(f"{a.sermon}: {done} segments, {n} candidates scored in {time.time() - t0:.0f} s "
          f"({(time.time() - t0) / max(done, 1):.2f} s a segment)")


# --- as another recogniser hears it ---------------------------------------------------------

def whisper(a) -> None:
    """The probability Whisper large-v3 gives each candidate's text for the same audio (its
    decoder made to read the text out, not its own transcript)."""
    import mlx.core as mx
    import soundfile as sf
    from mlx_whisper.audio import N_FRAMES, log_mel_spectrogram, pad_or_trim
    from mlx_whisper.load_models import load_model
    from mlx_whisper.tokenizer import get_tokenizer
    _mlx_caps(mx)
    model = load_model("mlx-community/whisper-large-v3-mlx", dtype=mx.float16)
    tk = get_tokenizer(model.is_multilingual, num_languages=model.num_languages, language="ro", task="transcribe")
    sot = list(tk.sot_sequence_including_notimestamps)
    x, sr = sf.read(str(_audio(a.sermon)[0]), dtype="float32", always_2d=True)
    assert sr == SR, sr
    x = np.ascontiguousarray(x.mean(1), dtype=np.float32)
    segs, ml = _load(a.sermon)
    end = _end(a.sermon)
    rows, t0, done = [], time.time(), 0
    for g, w in zip(segs, ml):
        if _skip(g, end) or (a.limit and done >= a.limit):
            rows.append({})
            continue
        done += 1
        texts = [t for _, t in _texts(g, w)]
        mel = pad_or_trim(log_mel_spectrogram(x[g["s"]:g["e"]], n_mels=model.dims.n_mels, padding=N_FRAMES * 160),
                          N_FRAMES, axis=-2).astype(mx.float16)
        feat = model.embed_audio(mel[None])
        ids = [sot + tk.encode(" " + t) + [tk.eot] for t in texts]
        out: dict[str, list] = {}
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        for at in range(0, len(order), a.batch):
            chunk = order[at:at + a.batch]
            L = max(len(ids[i]) for i in chunk)
            tok = np.full((len(chunk), L), tk.eot, dtype=np.int32)
            for b, i in enumerate(chunk):
                tok[b, :len(ids[i])] = ids[i]
            lg = model.logits(mx.array(tok[:, :-1]), mx.broadcast_to(feat, (len(chunk),) + feat.shape[1:])).astype(mx.float32)
            ls = np.array(lg - mx.logsumexp(lg, axis=-1, keepdims=True))
            for b, i in enumerate(chunk):
                n = len(ids[i])
                lp = float(sum(ls[b, k - 1, ids[i][k]] for k in range(len(sot), n)))
                out[texts[i]] = [lp, n - len(sot)]
        rows.append(out)
        mx.clear_cache()
    (OUT / f"choose-{a.sermon}.wh.json").write_text(json.dumps(
        {"sermon": a.sermon, "segs": rows, "wall_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    print(f"{a.sermon}: {done} segments, {sum(len(r) for r in rows)} candidates scored in {time.time() - t0:.0f} s "
          f"({(time.time() - t0) / max(done, 1):.2f} s a segment); {_mem(mx)}")


# --- as language --------------------------------------------------------------------------

def gemma(a) -> None:
    import mlx.core as mx
    from mlx_lm import load
    from mlx_lm.models.cache import make_prompt_cache, trim_prompt_cache
    from textnorm import tokens
    _mlx_caps(mx)
    model, tok = load(GEMMA)

    def chat(text: str) -> str:
        msgs = [{"role": "user", "content": text}]
        try:
            return tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        except Exception:                                 # noqa: BLE001  templates without that switch
            return tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)

    def enc(text: str) -> list[int]:
        bos = getattr(tok, "bos_token", None)
        return list(tok.encode(text, add_special_tokens=bos is None or not text.startswith(bos)))

    def logp(ids: list[int], cache=None):
        lg = model(mx.array([ids]), cache=cache).astype(mx.float32)        # the model answers in 16 bits
        return lg - mx.logsumexp(lg, axis=-1, keepdims=True)

    head = chat(ASK)                              # Gemma only predicts sensibly inside its chat format
    letters = [enc_one for enc_one in (tok.encode(c, add_special_tokens=False) for c in LETTERS)]
    assert all(len(x) == 1 for x in letters), letters
    letters = [x[0] for x in letters]
    segs, ml = _load(a.sermon)
    end = _end(a.sermon)
    ctx: list[str] = []
    rows, t0, done, took = [], time.time(), 0, []
    for g, w in zip(segs, ml):
        top = g["j8"][0][0].strip() if g["j8"] else ""
        if _skip(g, end) or (a.limit and done >= a.limit):
            rows.append({})
            ctx += top.split()
            continue
        done += 1
        t1 = time.time()
        reps: dict[str, str] = {}                 # one text as written per different wording
        for _, t in _texts(g, w):
            key = " ".join(tokens(t))
            if key and key not in reps and len(reps) < a.cap:
                reps[key] = t
        before = " ".join(ctx[-a.context:])
        seqs = {k: enc(head + before + (" " if before else "") + t) for k, t in reps.items()}
        out: dict[str, list] = {}
        pick: dict[str, float] = {}
        cache = None
        if len(seqs) > 1:
            # 1. as a continuation: log P(candidate | the transcript before it)
            first = next(iter(seqs.values()))
            n = min(len(s) for s in seqs.values())
            for s in seqs.values():
                k = 0
                while k < n and s[k] == first[k]:
                    k += 1
                n = k
            cache = make_prompt_cache(model)
            base = logp(first[:n], cache)[:, -1, :]                        # what follows the shared start
            mx.eval(base)
            for k, s in seqs.items():
                rest = s[n:]
                if not rest:
                    out[k] = [0.0, 0]
                    continue
                lp = base[0, rest[0]]
                if len(rest) > 1:
                    ls = logp(rest[:-1], cache)
                    lp = lp + mx.take_along_axis(ls[0], mx.array(rest[1:])[:, None], axis=1).sum()
                    mx.eval(lp)
                    trim_prompt_cache(cache, len(rest) - 1)
                out[k] = [float(lp), len(rest)]
            # 2. as a question: which of these did the preacher say?
            opts = list(reps.items())[:len(LETTERS)]
            q = (PICK.format(before=before or "(începutul predicii)")
                 + "\n".join(f"{LETTERS[i]}. {t}" for i, (_, t) in enumerate(opts)) + PICK_END)
            ls = logp(enc(chat(q)))[0, -1]
            mx.eval(ls)
            pick = {k: float(ls[letters[i]]) for i, (k, _) in enumerate(opts)}
        took.append(time.time() - t1)
        rows.append({"reps": reps, "lp": out, "pick": pick})
        ctx += top.split()
        del cache
        mx.clear_cache()
    (OUT / f"choose-{a.sermon}.lm.json").write_text(json.dumps(
        {"sermon": a.sermon, "model": GEMMA, "context_words": a.context, "segs": rows,
         "wall_s": round(time.time() - t0, 1)}, ensure_ascii=False))
    print(f"{a.sermon}: {done} segments in {time.time() - t0:.0f} s; a segment: median {np.median(took):.2f} s, "
          f"p90 {np.percentile(took, 90):.2f} s, {np.mean([len(r['reps']) for r in rows if r]):.1f} wordings; {_mem(mx)}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("acoustic")
    p.add_argument("sermon")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--batch", type=int, default=8)
    p = sub.add_parser("gemma")
    p.add_argument("sermon")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--cap", type=int, default=16, help="different wordings scored per segment")
    p.add_argument("--context", type=int, default=80, help="words of transcript before the segment")
    p = sub.add_parser("whisper")
    p.add_argument("sermon")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--batch", type=int, default=16)
    s = sub.add_parser("score")
    s.add_argument("sermons", nargs="+")
    a = ap.parse_args()
    {"acoustic": acoustic, "gemma": gemma, "whisper": whisper, "score": score}[a.cmd](a)


# --- choosing -----------------------------------------------------------------------------

FEATS = ["tdt", "ctc", "wh", "lm", "pick", "words", "top", "outside"]
GRID = {"tdt": [0, .1, .2, .35, .5, .75, 1, 1.5, 2, 3, 5], "ctc": [0, .1, .2, .35, .5, .75, 1, 1.5, 2, 3, 5],
        "wh": [0, .1, .2, .35, .5, .75, 1, 1.5, 2, 3, 5],
        "lm": [0, .03, .06, .1, .15, .2, .3, .4, .5, .75, 1, 1.5], "pick": [0, .1, .2, .35, .5, .75, 1, 1.5, 2, 3, 5],
        "words": [-4, -3, -2, -1.5, -1, -.5, 0, .5, 1, 1.5, 2, 3, 4], "top": [0, .5, 1, 1.5, 2, 3, 4, 6, 8, 12],
        "outside": [0, -1, -2, -4, -8, -16]}
POOLS = {"beam-8 list": ("j8",), "beam-32 list": ("j8", "j32"),
         "beam-8 list, other decoder, Whisper": ("j8", "tdt", "whisper"),
         "beam-32 list, other decoder, Whisper": ("j8", "j32", "tdt", "whisper")}
JUDGES = [("other decoder", ["tdt"]), ("plain CTC", ["ctc"]), ("other decoder + plain CTC", ["tdt", "ctc"]),
          ("Gemma, as continuation", ["lm"]), ("Gemma, asked to pick", ["pick"]),
          ("Gemma, both", ["lm", "pick"]), ("other decoder + Gemma continuation", ["tdt", "lm"]),
          ("other decoder + Gemma pick", ["tdt", "pick"]),
          ("Whisper as judge", ["wh"]), ("other decoder + Whisper", ["tdt", "wh"]),
          ("Whisper + Gemma continuation", ["wh", "lm"]), ("other decoder + Whisper + Gemma", ["tdt", "wh", "lm"]),
          ("everything", ["tdt", "ctc", "wh", "lm", "pick"])]


def _sermon(sermon: str) -> dict:
    """Per kept segment: its stretch of the reference and its different wordings, each with what
    every judge said. A wording's sound score is that of its most likely spelling."""
    import bench
    from textnorm import tokens
    ne, ml = _load(sermon)
    ac = json.loads((OUT / f"choose-{sermon}.ac.json").read_text())["segs"]
    lp, wp = OUT / f"choose-{sermon}.lm.json", OUT / f"choose-{sermon}.wh.json"
    lm = json.loads(lp.read_text())["segs"] if lp.exists() else [{} for _ in ne]      # a judge not run yet:
    wh = json.loads(wp.read_text())["segs"] if wp.exists() else [{} for _ in ne]      # it changes nothing
    ref_lines, end = bench.reference(sermon)
    ref = tokens(" ".join(x["ro"] for x in ref_lines))
    segs = []
    for g, w, sa, sl, sw in zip(ne, ml, ac, lm, wh):
        if _skip(g, end):
            continue
        b8, b32 = dict(reversed(g["j8"])), dict(reversed(g["j32"]))      # the best score of a text
        cands: dict[tuple, dict] = {}
        for src, t in _texts(g, w):
            key = tuple(tokens(t))
            if not key:
                continue
            c = cands.setdefault(key, {"key": key, "src": set(), "b8": None, "b32": None, "tdt": None, "ctc": None,
                                       "wh": None, "lm": None, "pick": None})
            c["src"].add(src)
            if t in b32:
                c["src"].add("j32")
            for name, v in (("b8", b8.get(t)), ("b32", b32.get(t)), ("ctc", (sa.get(t) or [None] * 3)[0]),
                            ("tdt", (sa.get(t) or [None] * 3)[1]), ("wh", (sw.get(t) or [None])[0])):
                if v is not None and (c[name] is None or v > c[name]):
                    c[name] = v
        for key, c in cands.items():
            k = " ".join(key)
            if k in sl.get("lp", {}):
                c["lm"] = sl["lp"][k][0]
            if k in sl.get("pick", {}):
                c["pick"] = sl["pick"][k]
        segs.append({"cands": list(cands.values()), "top": tuple(tokens(g["j8"][0][0])) if g["j8"] else ()})
    flat = [t for sg in segs for t in sg["top"]]
    _, _, before = _walk(ref, flat)
    starts, pos = [], 0
    for sg in segs:
        starts.append(before[pos] if pos < len(before) else len(ref))
        pos += len(sg["top"])
    starts[0] = 0
    for sg, s, e in zip(segs, starts, starts[1:] + [len(ref)]):
        for c in sg["cands"]:
            c["err"] = _dist(ref[s:e], list(c["key"]))
    return {"sermon": sermon, "ref": ref, "segs": segs}


def _table(d: dict, pool: tuple, feats: list[str]):
    """Segments x candidates, the recogniser's first choice first: the list's own score and the
    judges' scores (each as the difference from the first choice), errors, and the words. A
    candidate that a judge in use has no score for is out."""
    which = "b32" if "j32" in pool else "b8"
    rows = []
    for sg in d["segs"]:
        top = next((c for c in sg["cands"] if c["key"] == sg["top"]), None)
        if top is None:                                   # the recogniser heard nothing here
            rows.append((None, []))
            continue
        cs = [top]
        if all(top[f] is not None for f in feats):
            cs += [c for c in sg["cands"] if c is not top and c["src"] & set(pool)
                   and all(c[f] is not None for f in feats)]
        rows.append((top, cs))
    n, K = len(rows), max(max(len(cs) for _, cs in rows), 1)
    base = np.full((n, K), -np.inf)
    base[:, 0] = 0.0
    F = np.zeros((n, K, len(FEATS)))
    err = np.zeros((n, K), dtype=int)
    keys = [[[]] for _ in rows]
    for i, (top, cs) in enumerate(rows):
        if top is None:
            continue
        keys[i] = [list(c["key"]) for c in cs]
        low = min([c[which] for c in cs if c[which] is not None] or [0.0])   # not in the list: as its last
        for k, c in enumerate(cs):
            base[i, k] = (c[which] if c[which] is not None else low) - (top[which] if top[which] is not None else low)
            err[i, k] = c["err"]
            for j, f in enumerate(FEATS):
                if f == "words":
                    F[i, k, j] = len(c["key"]) - len(top["key"])
                elif f == "top":
                    F[i, k, j] = float(c is top)
                elif f == "outside":
                    F[i, k, j] = float(c[which] is None)
                elif f in feats and c is not top:
                    F[i, k, j] = c[f] - top[f]
    return base, F, err, keys


def _errors(tab, w: np.ndarray) -> int:
    base, F, err, _ = tab
    return int(err[np.arange(len(base)), np.argmax(base + F @ w, axis=1)].sum())   # ties: the list's order


def _tune(tabs: list, free: list[str]) -> np.ndarray:
    """Weights with the fewest errors on these sermons: one weight at a time over its grid until
    nothing improves, from several starts (a weight alone often cannot help before another is
    set); of equal results the first found, and each weight moves only for a strict gain."""
    def descend(w: np.ndarray) -> tuple[int, np.ndarray]:
        best = sum(_errors(t, w) for t in tabs)
        for _ in range(8):
            moved = False
            for f in free:
                j = FEATS.index(f)
                for v in sorted(GRID[f], key=abs):
                    w2 = w.copy()
                    w2[j] = v
                    e = sum(_errors(t, w2) for t in tabs)
                    if e < best:
                        best, w, moved = e, w2, True
            if not moved:
                break
        return best, w

    starts = [np.zeros(len(FEATS))]
    judges = [f for f in free if f not in ("words", "top", "outside")]
    for scale in (0.3, 1.0):
        for sub in [judges] + ([[f] for f in judges] if len(judges) > 1 else []):
            w = np.zeros(len(FEATS))
            for f in sub:
                w[FEATS.index(f)] = GRID[f][len(GRID[f]) // 3] * scale * 2
            for top in (0, 2):
                w2 = w.copy()
                w2[FEATS.index("top")] = top
                if "outside" in free:
                    w2[FEATS.index("outside")] = -4
                starts.append(w2)
    return min((descend(w) for w in starts), key=lambda r: r[0])[1]


def score(a) -> None:
    data = [_sermon(s) for s in a.sermons]
    words = sum(len(d["ref"]) for d in data)

    def whole(d, tab, w) -> tuple[int, int, int]:
        """Errors of the whole text; segments changed for the better, for the worse."""
        base, F, err, keys = tab
        pick = np.argmax(base + F @ w, axis=1)
        e = err[np.arange(len(pick)), pick]
        return (_dist(d["ref"], [t for i, k in enumerate(pick) for t in keys[i][k]]),
                int((e < err[:, 0]).sum()), int((e > err[:, 0]).sum()))

    zero = np.zeros(len(FEATS))
    live = sum(whole(d, _table(d, POOLS["beam-8 list"], []), zero)[0] for d in data)
    print(f"{len(data)} sermons, {words} reference words. Errors are of the whole text against the reference.")
    print("'held out': weights tuned on the other sermons; 'fitted': tuned on all, so too kind.\n")
    for pname, pool in POOLS.items():
        tabs0 = [_table(d, pool, []) for d in data]
        own = sum(whole(d, t, zero)[0] for d, t in zip(data, tabs0))
        ceil = sum(int(np.where(np.isfinite(t[0]), t[2], 10 ** 6).min(axis=1).sum()) for t in tabs0)
        print(f"=== candidates: {pname} ===")
        print(f"  {'the live recogniser (beam 8)':38s} {live:4d} errors {live / words * 100:5.2f}%")
        if own != live:
            print(f"  {'this list, its own first choice':38s} {own:4d} errors {own / words * 100:5.2f}%")
        print(f"  {'ceiling (reference in hand)':38s} {ceil:4d} errors {ceil / words * 100:5.2f}%  (by segment)")
        extra = ["outside"] if set(pool) & {"tdt", "whisper"} else []
        for jname, feats in JUDGES:
            tabs = [_table(d, pool, feats) for d in data]
            free = feats + ["words", "top"] + extra
            held, better, worse, per = 0, 0, 0, []
            for i, d in enumerate(data):
                w = _tune([t for k, t in enumerate(tabs) if k != i] if len(data) > 1 else tabs, free)
                e, up, down = whole(d, tabs[i], w)
                held, better, worse = held + e, better + up, worse + down
                per.append(f"{d['sermon']} {e}")
            wf = _tune(tabs, free)
            fit = sum(whole(d, t, wf)[0] for d, t in zip(data, tabs))
            ws = ", ".join(f"{f} {wf[FEATS.index(f)]:g}" for f in free)
            print(f"  {jname:38s} {held:4d} held out ({(held - live) / live * 100:+5.1f}%; segments better {better}, "
                  f"worse {worse})  {fit:4d} fitted  [{'; '.join(per)}]  weights: {ws}")
        print()


if __name__ == "__main__":
    main()
