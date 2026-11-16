#!/usr/bin/env python3
"""Recogniser-only replay of a corrected sermon: the engine without the translator.

  .venv-nemo/bin/python scripts/stt_replay.py live SERMON [--name N] [--mode ro-en|auto]
                                                  [--profile quality] [--set key=value ...] [--terms FILE]
      the live engine (VAD, cuts, ContextSTT) fed the recording frame by frame, as fast as the
      recogniser goes (a 30 min sermon in ~4 min). Every clause the engine emits is written to
      logs/bench/stt/<name>.jsonl with what the recogniser heard for it (the whole hypothesis
      with its context, the committed context text, pause or forced cut), and the voice
      detector's per-frame probabilities to <name>.vad.npy.
  .venv-nemo/bin/python scripts/stt_replay.py offline SERMON [--name N] [--no-boost]
      the same recogniser on segments cut only at pauses (<= 20 s, src/batch_transcribe.segment),
      each recognised alone: the ceiling the live transcript is measured against.
  .venv-nemo/bin/python scripts/stt_replay.py wer NAME [NAME ...]
      Romanian transcript WER of such runs against the corrected text (as bench.py ref scores it).

SERMON: br-random-1, nelu-suciu, luigi-mitoi (logs/bench/audio/corr_<sermon>.wav), or a WAV path.
Frame times are the recording's own clock (start_wall + position), as in bench.py runs. The
automatic gain runs as live. No translator, no repairs: the text is the recogniser's own."""
from __future__ import annotations

import argparse
import json
import pathlib
import queue
import sys
import threading
import time

import numpy as np

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = REPO / "logs" / "bench" / "stt"
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "live"))
SR = 16000


def _audio(sermon: str) -> tuple[pathlib.Path, float]:
    p = pathlib.Path(sermon)
    if not p.exists():
        p = REPO / "logs" / "bench" / "audio" / f"corr_{sermon}.wav"
    side = p.with_suffix(".json")
    t0 = json.loads(side.read_text())["start_wall"] if side.exists() else 0.0
    return p, t0


def _live_cfg(profile: str, sets: list[str]) -> dict:
    import yaml
    cfg = yaml.safe_load((REPO / "config.yaml").read_text())
    lc = {**cfg["live"], **(cfg.get("profiles", {}).get(profile) or {})}
    for kv in sets or []:
        k, _, v = kv.partition("=")
        try:
            lc[k.removeprefix("live.")] = json.loads(v)
        except json.JSONDecodeError:
            lc[k.removeprefix("live.")] = v
    return lc


class _SttTap:
    """The recogniser, remembering what it returned last (the whole hypothesis, context included)."""
    def __init__(self, stt):
        self.stt, self.last, self.last_n = stt, "", 0

    def __call__(self, audio, *a, **kw):
        out = self.stt(audio, *a, **kw)
        self.last, self.last_n = out, len(audio)
        return out

    def __getattr__(self, k):
        f = getattr(self.stt, k)
        if k != "words":
            return f

        def words(audio):
            text, w = f(audio)
            self.last, self.last_n = text, len(audio)
            return text, w
        return words


class _CtxTap:
    """ContextSTT, remembering each call: the committed text before it and what was heard."""
    def __init__(self, ctx):
        self.c, self.rec = ctx, None

    def __call__(self, chunk, final=False, ahead=None):
        before, n_ctx = self.c.ctx_text, len(self.c.ctx_audio)
        t = time.time()
        out = self.c(chunk, final, ahead) if ahead is not None else self.c(chunk, final)
        hyp = self.c.stt.last
        self.rec = {"final": bool(final), "ctx_text": before, "ctx_s": round(n_ctx / SR, 3),
                    "chunk_s": round(len(chunk) / SR, 3),
                    "ahead_s": round(len(ahead) / SR, 3) if ahead is not None else 0.0, "stt_wall_s": round(time.time() - t, 3),
                    "hyp": hyp if isinstance(hyp, str) else getattr(hyp, "text", str(hyp)),
                    **({"debug": self.c.debug} if getattr(self.c, "debug", None) else {})}
        return out

    def __getattr__(self, k):
        return getattr(self.c, k)


class _VadTap:
    def __init__(self, vad):
        self.vad, self.p = vad, []

    def __call__(self, fr):
        p = self.vad(fr)
        self.p.append(p)
        return p

    def __getattr__(self, k):
        return getattr(self.vad, k)


def live(a) -> None:
    import engine
    from vad import FRAME
    path, t0 = _audio(a.sermon)
    lc = _live_cfg(a.profile, a.set)
    lc["partial_every_s"] = 0
    if a.terms:
        lc["ro_names"] = [x.strip() for x in pathlib.Path(a.terms).read_text().splitlines()
                          if x.strip() and not x.startswith("#")]
    name = a.name or f"{pathlib.Path(a.sermon).stem}-live"
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{name}.jsonl"
    clauses: list[dict] = []
    eng = None

    def emit(obj):
        if obj.get("type") == "clause":
            rec = getattr(eng.stt_ctx, "rec", None) or {}
            # t_cut: the audio clock when the chunk was cut (the last frame the engine had taken)
            clauses.append({**obj, "t_cut": round(t0 + len(eng.vad.p) * FRAME / SR, 3), "trace": rec})
        elif obj.get("type") in ("error", "warn"):
            print(obj, file=sys.stderr, flush=True)
    engine.emit = emit
    eng = engine.Engine(lc["stt_model"], a.mode, lc)
    eng.realtime = False
    eng.stt_ctx.stt = _SttTap(eng.stt_ctx.stt)
    eng.stt_ctx = _CtxTap(eng.stt_ctx)
    eng.vad = _VadTap(eng.vad)
    ctl = engine.SourceCtl(None, None, gain_db=float(lc.get("input_gain_db", 0.0)),
                           auto=bool(lc.get("input_gain_auto", False)),
                           target_db=lc.get("input_gain_target_db", engine.AGC_TARGET_DB))
    eng.ctl = ctl
    x = engine.read_audio(str(path))
    if a.minutes:
        x = x[:int(a.minutes * 60 * SR)]
    q: queue.Queue = queue.Queue(maxsize=1)            # in step with the engine: the gain moves as live

    def feed():
        for i in range(0, len(x) - FRAME + 1, FRAME):
            ts = t0 + (i + FRAME) / SR
            q.put((ts, ctl.amp(x[i:i + FRAME], ts)))
        q.put(None)
    threading.Thread(target=feed, daemon=True).start()
    t = time.time()
    eng.run(q, hold=False, idle_watch=False)
    wall = time.time() - t
    with open(out, "w") as f:
        for c in clauses:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    np.save(OUT / f"{name}.vad.npy", np.array(eng.vad.p, dtype=np.float32))
    (OUT / f"{name}.json").write_text(json.dumps(
        {"kind": "live", "sermon": a.sermon, "audio": str(path), "t0": t0, "mode": a.mode, "profile": a.profile,
         "set": a.set, "terms": a.terms, "wall_s": round(wall, 1), "audio_s": round(len(x) / SR, 1),
         "git": _git(), "cfg": {k: lc.get(k) for k in ("context_s", "lookahead_words", "min_commit_s", "max_commit_s",
                                                         "max_clause_s", "min_pause_ms", "vad_threshold", "timed_commit",
                                                         "commit_edge_s")}}, indent=1))
    print(f"{out}: {len(clauses)} clauses, {len(x) / SR / 60:.1f} min of audio in {wall:.0f} s")


def offline(a) -> None:
    import engine
    from batch_transcribe import segment
    from vad import SileroVAD
    path, t0 = _audio(a.sermon)
    name = a.name or f"{pathlib.Path(a.sermon).stem}-offline"
    OUT.mkdir(parents=True, exist_ok=True)
    stt = engine.NemoSTT(a.stt)
    if not a.no_boost:
        stt.set_names(list(engine.CHURCH_PHRASES), engine.NAME_BOOST)
    x = engine.read_audio(str(path))
    if a.minutes:
        x = x[:int(a.minutes * 60 * SR)]
    segs = segment(x, SileroVAD(), max_s=a.max_s)
    t = time.time()
    with open(OUT / f"{name}.jsonl", "w") as f:
        for i, (s, e) in enumerate(segs):
            text = stt(x[s:e]) or ""
            f.write(json.dumps({"type": "clause", "id": i + 1, "ro": text.strip(), "t_start": t0 + s / SR,
                                "t_end": t0 + e / SR, "audio_s": round((e - s) / SR, 3),
                                "trace": {"final": True}}, ensure_ascii=False) + "\n")
    wall = time.time() - t
    (OUT / f"{name}.json").write_text(json.dumps(
        {"kind": "offline", "sermon": a.sermon, "audio": str(path), "t0": t0, "boost": not a.no_boost,
         "max_s": a.max_s, "wall_s": round(wall, 1), "git": _git()}, indent=1))
    d = sorted((e - s) / SR for s, e in segs)
    print(f"{OUT / (name + '.jsonl')}: {len(segs)} segments (median {d[len(d) // 2]:.1f} s) in {wall:.0f} s")


def _git() -> str:
    import subprocess
    r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    d = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "src"], capture_output=True, text=True)
    return r.stdout.strip() + ("+dirty" if d.stdout.strip() else "")


def load(name: str) -> tuple[list[dict], dict]:
    p = pathlib.Path(name)
    if not p.exists():
        p = OUT / f"{name}.jsonl"
    return [json.loads(x) for x in p.open() if x.strip()], json.loads(p.with_suffix(".json").read_text())


def wer(names: list[str]) -> None:
    sys.path.insert(0, str(REPO / "scripts"))
    import bench
    from scoring import score
    from textnorm import key_tokens, tokens
    for name in names:
        recs, info = load(name)
        sermon = pathlib.Path(info["audio"]).stem.removeprefix("corr_")
        lines, end = bench.reference(sermon)
        recs = [r for r in recs if end is None or (r["t_start"] + r["t_end"]) / 2 - info["t0"] < end]
        ref = " ".join(x["ro"] for x in lines)
        hyp = " ".join(r.get("ro") or "" for r in recs)
        s = score(tokens(ref), tokens(hyp), free_ends=False)
        k = score(key_tokens(ref, numbers=True), key_tokens(hyp, numbers=True), free_ends=False)
        wait = sorted(r["t_cut"] - r.get("t_speech", r["t_start"]) for r in recs if r.get("t_cut") and (r.get("ro") or "").strip())
        lat = (f"   first word waits for the cut: median {wait[len(wait) // 2]:.2f} s, p90 {wait[int(len(wait) * 0.9)]:.2f} s, "
               f"> 5 s {sum(w > 5 for w in wait) / len(wait) * 100:.0f}%") if wait else ""
        forced = sum(not (r.get("trace") or {}).get("final", True) for r in recs)
        print(f"{name:24s} {sermon:12s} WER {s['wer'] * 100:5.2f}% (S{s['sub']} D{s['del']} I{s['ins']} / {s['n_ref']})"
              f"   key {k['wer'] * 100:5.2f}%   {len(recs)} clauses, {forced} forced{lat}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    lv = sub.add_parser("live")
    lv.add_argument("sermon")
    lv.add_argument("--name")
    lv.add_argument("--mode", default="ro-en")
    lv.add_argument("--profile", default="quality")
    lv.add_argument("--set", action="append")
    lv.add_argument("--terms")
    lv.add_argument("--minutes", type=float)
    of = sub.add_parser("offline")
    of.add_argument("sermon")
    of.add_argument("--name")
    of.add_argument("--stt", default="jackrabbit-ctc-lm-b8")
    of.add_argument("--no-boost", action="store_true")
    of.add_argument("--max-s", type=float, default=20.0)
    of.add_argument("--minutes", type=float)
    w = sub.add_parser("wer")
    w.add_argument("names", nargs="+")
    a = ap.parse_args()
    {"live": live, "offline": offline}.get(a.cmd, lambda a: wer(a.names))(a)


if __name__ == "__main__":
    main()
