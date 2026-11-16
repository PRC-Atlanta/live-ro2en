#!/usr/bin/env python3
"""Sentence-cut experiment: translate whole sentences instead of the app's pieces, offline.

  .venv-mt/bin/python scripts/sentence_cut.py translate RUN [--cap 12] [--limit N]
  .venv-mt/bin/python scripts/sentence_cut.py report RUN [--wps 2.7] [--budget 20]

RUN is a corrected-sermon replay with a reference translation (st3-luigi-mitoi). From its log:

  app        the captions as the app showed them (all guards on): the live baseline
  pieces     the app's shown Romanian pieces, translated again here, one by one, with context
  sentences  the same words re-cut at the recogniser's sentence ends (. ! ? …), a long sentence
             cut at `--cap` s at its best clause point (a comma before și / dar / care …), then
             translated here the same way

pieces and sentences use the same translator code and settings, so the only difference is the cut.
A sentence is ready when the piece holding its last word was recognised (that piece's t_end + its
recogniser time) plus its own translation time.

`report` scores each against the reference (chrF++ / BLEU over ~20 s stretches, as `bench.py ref`)
and plays them through a reading queue: a caption stays up at least its reading time
(`--min-s` + words / `--wps`), the next one waits for it. Lag = shown time - end of its speech.
`--budget`: when a caption would be shown later than this, the queue shortens it (a summary made
here with the same model, at most `--short-ratio` of its words) - the `queue+short` line.
Writes logs/bench/runs/RUN/sentence_cut.json (all captions, for the player page)."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import statistics as st
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "src" / "live"))
import bench                                                    # noqa: E402
from live.segmenter import _CONJ                                # noqa: E402

HARD = re.compile(r"[.!?…]+[\"»”]?$")
# never shortened: a verse, a reference or a quotation (2026-11-16: 1 John 4:5 lost half)
SACRED = re.compile(r"\d+\s*:\s*\d+|\d+ cu \d+|[„“”\"]|\bversetul\b|\bcapitolul\b", re.I)


def _shown(arg: str):
    """The run's shown Romanian phrases, as bench.ref_score counts them, and the run's clock origin."""
    log, info = bench._run_log(arg)
    name = pathlib.Path(info.get("audio", "")).stem.removeprefix("corr_")
    lines, end = bench.reference(name)
    t0 = info["segment_start_wall"]
    ph = [r for r in bench._records(log) if r.get("outcome") and "t_start" in r
          and (end is None or r["t_start"] - t0 < end + 1.0)]
    scr = bench.on_screen(ph)
    ok = [r for r in scr if r["outcome"] == "ok" and r.get("lang", "ro") == "ro"]
    return ok, lines, t0, log.parent.parent if log.parent.name == "services" else log.parent


def words_of(ph: list[dict], t0: float) -> list[dict]:
    """The pieces' words with a time each, spread evenly over the piece's speech."""
    out = []
    for k, r in enumerate(ph):
        ws = (r.get("source") or r.get("ro") or "").split()
        a, b = r.get("t_speech", r["t_start"]) - t0, r["t_end"] - t0
        ready = b + float(r.get("stt_s") or 0)
        for i, w in enumerate(ws):
            out.append({"w": w, "t": a + (b - a) * (i + 1) / len(ws), "piece": k, "ready": ready})
    return out


def cut_sentences(words: list[dict], cap_s: float, min_words: int = 3, gap_s: float = 2.5) -> list[list[dict]]:
    """Cut at sentence ends; a sentence longer than cap_s is cut at its last clause point
    (a comma before a conjunction, else any comma, else where the cap fell)."""
    out, cur = [], []
    for k, w in enumerate(words):
        if cur and w["t"] - cur[-1]["t"] > gap_s:     # the speaker stopped: send what there is
            out.append(cur)
            cur = []
        cur.append(w)
        nxt = words[k + 1]["w"] if k + 1 < len(words) else "X"
        # the recogniser ends every piece with a full stop; a real sentence end is followed by a
        # capital («…noi. | experiența» is one sentence, «…părtășie. | Este» two)
        if HARD.search(w["w"]) and len(cur) >= min_words and (nxt[:1].isupper() or nxt[:1].isdigit()):
            out.append(cur)
            cur = []
            continue
        if cur[-1]["t"] - cur[0]["t"] >= cap_s:
            # best cut point, by rank: comma before a conjunction 3, a pause (piece end) 2, a comma 1
            best, rank = None, 0
            for i in range(len(cur) - 2, 1, -1):
                r = 0
                if cur[i]["w"].endswith((",", ";", ":")):
                    r = 3 if cur[i + 1]["w"].lower().strip(",.;:") in _CONJ else 1
                elif cur[i]["piece"] != cur[i + 1]["piece"]:
                    r = 2
                if r > rank:
                    best, rank = i, r
            i = best if best is not None else len(cur) - 1
            out.append(cur[:i + 1])
            cur = cur[i + 1:]
    if cur:
        out.append(cur)
    return out


def sentence_text(s: list[dict]) -> str:
    """The words, without the recogniser's piece-end full stops inside the sentence."""
    ws = [w["w"] for w in s]
    return " ".join(re.sub(r"\.$", "", a) if a.endswith(".") and not a.endswith("..") and b[:1].islower()
                    else a for a, b in zip(ws, ws[1:] + ["X"]))


def translate(args) -> None:
    import translate as tmod  # noqa: PLC0415
    ph, lines, t0, rdir = _shown(args.run)
    words = words_of(ph, t0)
    sents = cut_sentences(words, args.cap)
    units = {
        "pieces": [{"ro": r.get("source") or r.get("ro"), "t_start": r.get("t_speech", r["t_start"]) - t0,
                    "t_end": r["t_end"] - t0, "ready": r["t_end"] - t0 + float(r.get("stt_s") or 0)} for r in ph],
        "sentences": [{"ro": sentence_text(s), "t_start": s[0]["t"] - 0.3, "t_end": s[-1]["t"], "ready": max(w["ready"] for w in s)} for s in sents],
    }
    if args.limit:
        units = {k: v[:args.limit] for k, v in units.items()}
    gloss = tmod.Glossary(REPO / "glossary.csv")
    tr = tmod.make_translator(args.translator)
    tr.context_clauses = args.context
    tr.translate(tmod.WARMUP[("ro", "en")], "ro", "en", gloss)
    out = {"run": args.run, "cap_s": args.cap, "translator": args.translator, "context": args.context}
    for kind, us in units.items():
        tr.reset_context()
        tr._kv, tr._kv_ids = None, []
        t_all = time.time()
        for i, u in enumerate(us):
            t = time.time()
            u["en"] = tr.translate(u["ro"], "ro", "en", gloss, use_context=True)
            u["mt_s"] = round(time.time() - t, 3)
            if i % 25 == 0:
                print(f"{kind} {i}/{len(us)}  {u['mt_s']:.2f}s  {u['ro'][:60]} -> {u['en'][:60]}", flush=True)
        out[kind] = us
        print(f"{kind}: {len(us)} in {time.time() - t_all:.0f} s", flush=True)
    out["app"] = [{"ro": r.get("source") or r.get("ro"), "en": r.get("translation") or "",
                   "t_start": r.get("t_speech", r["t_start"]) - t0, "t_end": r["t_end"] - t0,
                   "ready": r["t_end"] - t0 + float(r.get("stt_s") or 0), "mt_s": float(r.get("mt_s") or 0)}
                  for r in ph]
    (rdir / "sentence_cut.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(f"-> {rdir / 'sentence_cut.json'}")


def shorten(args, units: list[dict], need: list[int]) -> None:
    """A shorter caption for each unit index in `need` (the same model, one call each)."""
    from mlx_lm import generate  # noqa: PLC0415
    from mlx_lm.sample_utils import make_sampler  # noqa: PLC0415
    import translate as tmod  # noqa: PLC0415
    tr = tmod.make_translator(args.translator)
    for i in need:
        u = units[i]
        n = max(4, round(len(u["en"].split()) * args.short_ratio))
        msg = [{"role": "user", "content":
                f"Shorten this live sermon caption to at most {n} words. Keep the main point, the speaker's "
                f"person (I/we/you) and any names, numbers or Bible references. Add nothing that is not in it. "
                f"Reply with the shortened caption only.\n\nCaption: {u['en']}"}]
        p = tr.tok.apply_chat_template(msg, add_generation_prompt=True, tokenize=False, enable_thinking=False)
        t = time.time()
        u["short"] = tmod.clean_output(generate(tr.model, tr.tok, prompt=p, max_tokens=120,
                                                sampler=make_sampler(temp=0.0), verbose=False)).strip()
        u["short_s"] = round(time.time() - t, 3)


def read_s(text: str, args) -> float:
    return args.min_s + len(text.split()) / args.wps


def play(units: list[dict], args, queue: bool, short: bool = False) -> list[dict]:
    """Show times. queue=False: each caption goes up when ready and stays until the next (the app
    today). queue=True: a caption waits until the one before has had its reading time."""
    out, free = [], 0.0
    for u in units:
        text = u.get("short") if short and u.get("short") else u["en"]
        if not text.strip():
            continue
        ready = u["ready"] + u.get("mt_s", 0) + (u.get("short_s", 0) if short and u.get("short") else 0)
        show = max(ready, free) if queue else ready
        out.append({**u, "text": text, "show": show, "lag": show - u["t_end"], "need": read_s(text, args),
                    "short_of": u["en"] if text is not u["en"] and text != u["en"] else ""})
        free = show + read_s(text, args)
    for a, b in zip(out, out[1:] + [None]):
        a["up"] = (b["show"] - a["show"]) if b else a["need"]
    return out


def stats(cap: list[dict]) -> dict:
    lags = sorted(c["lag"] for c in cap)
    p = lambda q: lags[min(len(lags) - 1, int(q * len(lags)))]   # noqa: E731
    short_up = sum(1 for c in cap if c["up"] < c["need"] - 0.05)
    return {"captions": len(cap), "words_median": st.median(len(c["text"].split()) for c in cap),
            "lag_median": p(0.5), "lag_p90": p(0.9), "lag_max": lags[-1],
            "over_20s": sum(1 for x in lags if x > 20), "too_short_up": short_up}


def score(cap: list[dict], lines: list[dict]) -> tuple[float, float]:
    import sacrebleu  # noqa: PLC0415
    wins = bench.windows(lines, [(c["t_start"], c["t_end"]) for c in cap], 20.0)
    refs = [" ".join(lines[i]["en"] for i in w["lines"]) for w in wins]
    hyps = [" ".join(cap[j]["text"].strip() for j in w["phrases"]) for w in wins]
    return (sacrebleu.corpus_chrf(hyps, [refs], word_order=2).score, sacrebleu.corpus_bleu(hyps, [refs]).score)


def report(args) -> None:
    _, lines, _, rdir = _shown(args.run)
    path = rdir / "sentence_cut.json"
    data = json.loads(path.read_text())
    sents = data["sentences"]
    pre = play(sents, args, queue=True)
    # a sentence holding a piece the app recognised as a verse is never shortened
    ph, _, t0, _ = _shown(args.run)
    cut = cut_sentences(words_of(ph, t0), data["cap_s"])
    if len(cut) == len(sents):
        for u, ws in zip(sents, cut):
            u["verse"] = any(ph[w["piece"]].get("verse") for w in ws)
    # which sentences the queue would shorten: replay the queue, shortening as it goes
    if args.budget:
        free, need = 0.0, []
        for i, u in enumerate(sents):
            if not u["en"].strip():
                continue
            ready = u["ready"] + u.get("mt_s", 0)
            show = max(ready, free)
            text = u["en"]
            if show + read_s(text, args) - u["t_end"] > args.budget and len(text.split()) > 6 \
                    and not u.get("verse") and not SACRED.search(u["ro"] + " " + text):
                u["_over"] = True
                need.append(i)
                text = " ".join(text.split()[:max(4, round(len(text.split()) * args.short_ratio))])
            free = show + read_s(text, args)
        todo = [i for i in need if "short" not in sents[i]]
        if todo:
            print(f"shortening {len(todo)} captions …", flush=True)
            shorten(args, sents, todo)
            path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    rows = {
        "app (as shown)": play(data["app"], args, queue=False),
        "pieces, no queue": play(data["pieces"], args, queue=False),
        "pieces + queue": play(data["pieces"], args, queue=True),
        "sentences, no queue": play(sents, args, queue=False),
        "sentences + queue": pre,
        "sentences + queue + short": play(sents, args, queue=True, short=True),
    }
    print(f"{data['run']}: cap {data['cap_s']} s, reading {args.min_s} s + words / {args.wps}, budget {args.budget} s\n")
    print(f"{'':28} {'chrF++':>6} {'BLEU':>5} {'caps':>5} {'words':>5} {'lag med':>7} {'p90':>5} {'max':>5} "
          f"{'>20s':>5} {'up<read':>7}")
    res = {}
    for name, cap in rows.items():
        c, b = score(cap, lines)
        s = stats(cap)
        res[name] = {"chrf": c, "bleu": b, **s}
        print(f"{name:28} {c:6.1f} {b:5.1f} {s['captions']:5d} {s['words_median']:5.0f} {s['lag_median']:7.1f} "
              f"{s['lag_p90']:5.1f} {s['lag_max']:5.1f} {s['over_20s']:5d} {s['too_short_up']:7d}")
    data["report"] = {"wps": args.wps, "min_s": args.min_s, "budget": args.budget, "rows": res}
    data["timelines"] = {k: [{x: c[x] for x in ("ro", "text", "t_start", "t_end", "show", "up", "need", "lag", "short_of")}
                             for c in v] for k, v in rows.items()}
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False))
    page(args)


def roll(units: list[dict], args, queue: bool, needs: list[float] | None = None, fresh: bool = False) -> list[dict]:
    """The rolling screen of captions.py: translations word-wrapped into lines of args.chars, the
    last line continued by the next translation, args.lines lines on screen, older lines scrolling
    off the top. Each line: parts [[time, text so far]], show (its first words up), need (its reading
    time), up (time on screen before it scrolled off), lag (its last words up - end of their speech).
    queue: a new line waits until the line it pushes off has been up for its reading time.
    fresh: every translation starts on a new line, so a line appears whole and never grows."""
    import textwrap  # noqa: PLC0415
    wrap = lambda t: textwrap.wrap(t, args.chars, break_long_words=False)  # noqa: E731
    out: list[dict] = []
    for u in units:
        text = (u.get("en") or "").strip()
        if not text:
            continue
        ready = u["ready"] + u.get("mt_s", 0)
        if out and len(out[-1]["text"]) < args.chars and not fresh:
            first, *rest = wrap(out[-1]["text"] + " " + text)
            last = out[-1]
            if first != last["text"]:
                last["parts"].append([max(ready, last["show"]), first])
                last.update(text=first, t_end=u["t_end"], ro=u["ro"])
            ready = max(ready, last["parts"][-1][0])
        else:
            rest = wrap(text)
        for ln in rest:
            i = len(out)
            show = ready
            if queue and fresh and i:
                # whole lines: a line comes up once the one above has been read, so the eyes stay
                # on the bottom line and the next line arrives where they already are
                show = max(show, read_done(out, i - 1, args))
            elif queue and i >= args.lines:
                # a line scrolls off only after it has been read; lines are read one after another
                show = max(show, read_done(out, i - args.lines, args))
            if i:
                show = max(show, out[i - 1]["show"])
            out.append({"text": ln, "parts": [[show, ln]], "show": show, "t_end": u["t_end"], "ro": u["ro"],
                        "need": max(args.min_line_s, len(ln.split()) / args.wps)})
            ready = show
    for k in range(len(out)):
        out[k].pop("read", None)
    if out:
        read_done(out, len(out) - 1, args)
    for i, ln in enumerate(out):
        ln["need"] = ln["read"] - ln["show"]                  # until a reader in order has read it
        ln["up"] = (out[i + args.lines]["show"] - ln["show"]) if i + args.lines < len(out) else 99.0
        ln["lag"] = ln["parts"][-1][0] - ln["t_end"]
    return out


def read_done(out: list[dict], j: int, args) -> float:
    """When a reader who reads the lines in order has finished line j (final: not the last line):
    not before the line above is read, its first words are up and its last words are up."""
    for k in range(j + 1):
        x = out[k]
        if "read" in x and k < j:
            continue
        before = out[k - 1]["read"] if k else 0.0
        n_all = len(x["text"].split())
        added = n_all - len(x["parts"][0][1].split())
        x["read"] = max(max(x["show"], before) + max(args.min_line_s, n_all / args.wps),
                        x["parts"][-1][0] + (max(args.min_line_s, added / args.wps) if added else 0.0))
    return out[j]["read"]


def roll_stats(ls: list[dict]) -> dict:
    lags = sorted(x["lag"] for x in ls)
    p = lambda q: lags[min(len(lags) - 1, int(q * len(lags)))]   # noqa: E731
    return {"lines": len(ls), "lag_median": p(0.5), "lag_p90": p(0.9), "lag_max": lags[-1],
            "over_20s": sum(1 for x in lags if x > 20),
            "too_short_up": sum(1 for x in ls if x["up"] < x["need"] - 0.05)}


def paginate(units: list[dict], args) -> list[dict]:
    """Pages (pop-on): each translation word-wrapped and split into pages of args.lines lines, the
    words shared evenly between its pages; a page replaces the whole screen once the one before has
    been up for its reading time. Nothing moves: the eye always starts at the top left."""
    import textwrap  # noqa: PLC0415
    wrap = lambda t: textwrap.wrap(t, args.chars, break_long_words=False)  # noqa: E731
    out: list[dict] = []
    for u in units:
        text = (u.get("en") or "").strip()
        if not text:
            continue
        ready = u["ready"] + u.get("mt_s", 0)
        n = -(-len(wrap(text)) // args.lines)                 # pages needed
        words = text.split()
        pages, k = [], 0
        for j in range(n):                                    # even share of the words per page,
            m = round(len(words) * (j + 1) / n)               # moved to a punctuation mark close by
            if j < n - 1:
                near = [c for c in range(max(k + 2, m - 4), min(len(words) - 1, m + 4) + 1)
                        if words[c - 1].endswith((".", "?", "!", ";", ":", ","))]
                if near:
                    m = min(near, key=lambda c: (not words[c - 1].endswith((".", "?", "!")), abs(c - m)))
            pages.append(" ".join(words[k:m]))
            k = m
        if any(len(wrap(pg)) > args.lines for pg in pages):   # an uneven wrap: plain greedy pages
            ls = wrap(text)
            pages = [" ".join(ls[i:i + args.lines]) for i in range(0, len(ls), args.lines)]
        for pg in pages:
            show = max(ready, out[-1]["show"] + out[-1]["need"]) if out else ready
            out.append({"lines": wrap(pg), "show": show, "t_end": u["t_end"], "ro": u["ro"],
                        "need": args.min_s + len(pg.split()) / args.wps})
    for i, pg in enumerate(out):
        pg["up"] = (out[i + 1]["show"] - pg["show"]) if i + 1 < len(out) else 99.0
        pg["lag"] = pg["show"] - pg["t_end"]
    return out


def buffer_frames(units: list[dict], args, fix: bool) -> tuple[list[dict], dict]:
    """The app's own caption box (captions.RollingCaptions + tidy_clause, as pipeline.py and
    output.py drive them) on a simulated clock, with the live settings (min_line_s 2.5, reveal at
    speaking pace). fix: a line's dwell restarts when words are added to it on screen (today it
    counts from when the line first came up, so a line filled out by a long translation can scroll
    off at once, its new words never seen). Returns screen frames and the stats."""
    from captions import RollingCaptions, tidy_clause  # noqa: PLC0415
    now = [0.0]
    seen: dict[str, float] = {}          # a line's text -> when it was first on screen in that form
    popped: list[dict] = []
    gone_words = [0]

    class Lines(list):
        def pop(self, i=-1):
            s = super().pop(i)
            if i == 0:
                gone_words[0] += len(s.split())
                need = max(args.min_line_s, len(s.split()) / args.wps)
                popped.append({"up": now[0] - seen[s] if s in seen else 0.0, "need": need, "seen": s in seen})
            return s

    class Box(RollingCaptions):
        def _append(self, text):
            before = self.lines[-1] if self.lines else None
            super()._append(text)
            if fix and self.lines and before is not None and self.lines[-1] != before and len(self._since) == len(self.lines):
                self._since[-1] = self.clock()

    box = Box(max_chars=args.chars, max_lines=args.lines, min_line_s=2.5, clock=lambda: now[0],
              reveal=True, reveal_wps=1.5)
    box.lines = Lines()
    frames, total, ends = [], [0], []     # ends: (words so far after unit k, t_end, ro)
    last_text = [None]

    def record():
        txt = tuple(box.lines)
        for s in txt:
            seen.setdefault(s, now[0])
        if txt != last_text[0]:
            last_text[0] = txt
            shown_to = gone_words[0] + sum(len(s.split()) for s in txt)
            k = next((j for j, e in enumerate(ends) if e[0] >= shown_to), len(ends) - 1)
            top = txt[0] if txt else ""
            frames.append({"t": now[0], "lines": list(txt), "shown_to": shown_to, "lag": now[0] - ends[k][1] if ends else 0,
                           "ro": ends[k][2] if ends else "", "top_since": seen.get(top, now[0]),
                           "top_need": max(args.min_line_s, len(top.split()) / args.wps),
                           "gone": sum(1 for p in popped if p["up"] < p["need"] - 0.05), "popped": len(popped)})

    def run_due(until: float):
        for _ in range(10000):
            due = box.next_due()
            if due is None or due > until:
                return
            now[0] = max(now[0], due)
            if not box.advance():
                now[0] += 0.05                 # nothing moved: step on, never loop in place
            record()

    prev_end, prev_open = None, False
    for u in sorted((u for u in units if (u.get("en") or "").strip()), key=lambda u: u["ready"] + u.get("mt_s", 0)):
        ta = u["ready"] + u.get("mt_s", 0)
        run_due(ta)
        now[0] = max(now[0], ta)
        cont = prev_open and prev_end is not None and u["t_start"] - prev_end < 1.0
        text, is_open = tidy_clause(u["en"], cont, "en", u["ro"])
        close = "." if prev_open and not cont else ""
        total[0] += len(text.split())
        ends.append((total[0], u["t_end"], u["ro"]))
        box.add(text, close, audio_s=max(0.5, u["t_end"] - u["t_start"]))
        record()
        prev_end, prev_open = u["t_end"], is_open
    run_due(float("inf"))
    # lag per unit: when its last word was first on screen
    lags, f = [], 0
    for w, t_end, _ in ends:
        while f < len(frames) and gone_words_at(frames[f]) < w:
            f += 1
        if f < len(frames):
            lags.append(frames[f]["t"] - t_end)
    lags.sort()
    p = lambda q: lags[min(len(lags) - 1, int(q * len(lags)))]   # noqa: E731
    stats = {"lines": len(popped), "lag_median": p(0.5), "lag_p90": p(0.9), "lag_max": lags[-1],
             "over_20s": sum(1 for x in lags if x > 20),
             "too_short_up": sum(1 for x in popped if x["up"] < x["need"] - 0.05),
             "never_seen": sum(1 for x in popped if not x["seen"])}
    return frames, stats


def gone_words_at(fr: dict) -> int:
    return fr["shown_to"]


SCREENS = {   # player name: (units, display, translation-score row)
    "today: the app's caption box": ("app", "buffer", "app (as shown)"),
    "today's box, dwell fixed": ("app", "buffer+fix", "app (as shown)"),
    "sentences, today's box, dwell fixed": ("sentences", "buffer+fix", "sentences, no queue"),
    "sentences + line pacing": ("sentences", "grow+pace", "sentences, no queue"),
    "sentences, whole lines, paced": ("sentences", "whole+pace", "sentences, no queue"),
    "sentences, pages": ("sentences", "pages", "sentences, no queue"),
    "pieces, whole lines, paced": ("pieces", "whole+pace", "pieces, no queue"),
    "pieces, pages": ("pieces", "pages", "pieces, no queue"),
}


def page(args) -> None:
    """player.html next to the run's segment.wav: two rolling screens side by side, with the audio."""
    _, _, _, rdir = _shown(args.run)
    data = json.loads((rdir / "sentence_cut.json").read_text())
    rows, screens = {}, {}
    print(f"{data['run']}: rolling {args.lines} x {args.chars} chars, a line needs max({args.min_line_s} s, "
          f"words / {args.wps})\n{'':26}{'chrF++':>7}{'BLEU':>6}{'lines':>6}{'lag med':>8}{'p90':>6}{'max':>6}"
          f"{'>20s':>6}{'up<read':>8}")
    for name, (kind, mode, srow) in SCREENS.items():
        if mode.startswith("buffer"):
            frames, st_ = buffer_frames(data[kind], args, fix=mode.endswith("fix"))
            sc = data.get("report", {}).get("rows", {}).get(srow, {})
            rows[name] = {"chrf": sc.get("chrf"), "bleu": sc.get("bleu"), **st_}
            r = rows[name]
            print(f"{name:36}{r['chrf'] or 0:7.1f}{r['bleu'] or 0:6.1f}{r['lines']:6d}{r['lag_median']:8.1f}"
                  f"{r['lag_p90']:6.1f}{r['lag_max']:6.1f}{r['over_20s']:6d}{r['too_short_up']:8d}  never on screen: {r['never_seen']}")
            screens[name] = {"mode": "frames", "items": frames}
            continue
        if mode == "pages":
            ls = paginate(data[kind], args)
        else:
            queue, fresh = mode.endswith("+pace"), mode.startswith("whole")
            ls = roll(data[kind], args, queue, fresh=fresh)
        sc = data.get("report", {}).get("rows", {}).get(srow, {})
        rows[name] = {"chrf": sc.get("chrf"), "bleu": sc.get("bleu"), **roll_stats(ls)}
        r = rows[name]
        print(f"{name:36}{r['chrf'] or 0:7.1f}{r['bleu'] or 0:6.1f}{r['lines']:6d}{r['lag_median']:8.1f}"
              f"{r['lag_p90']:6.1f}{r['lag_max']:6.1f}{r['over_20s']:6d}{r['too_short_up']:8d}")
        keys = ("lines",) if mode == "pages" else ("parts",)
        screens[name] = {"mode": "pages" if mode == "pages" else "roll",
                         "items": [{k: x[k] for k in keys + ("show", "need", "up", "lag", "ro")} for x in ls]}
    jumps = [{"t": 0, "label": "start"}]
    for x in sorted(screens["sentences, pages"]["items"], key=lambda x: -x["lag"])[:3]:
        jumps.append({"t": max(0, x["show"] - 12), "label": f"sentence pages furthest behind ({x['lag']:.0f} s)"})
    fr = screens["today: the app's caption box"]["items"]
    for a, b in zip(fr, fr[1:]):                     # where today's box loses a line before it can be read
        if b["gone"] > a["gone"] and len(jumps) < 8:
            jumps.append({"t": max(0, b["t"] - 6), "label": "today's box: a line gone before it could be read"})
    jumps.sort(key=lambda j: j["t"])
    keep = {"run": data["run"], "cap_s": data["cap_s"], "screens": screens, "rows": rows, "jumps": jumps,
            "conf": {"lines": args.lines, "chars": args.chars, "wps": args.wps, "min_line_s": args.min_line_s},
            "app": [{"ro": u["ro"], "ready": u["ready"]} for u in data["app"]]}
    html = (REPO / "scripts" / "sentence_cut_player.html").read_text()
    html = html.replace("__AUDIO__", "segment.wav").replace("__DATA__", json.dumps(keep, ensure_ascii=False).replace("</", "<\\/"))
    (rdir / "player.html").write_text(html)
    print(f"-> {rdir / 'player.html'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("translate", "report", "page"):
        p = sub.add_parser(name)
        p.add_argument("run")
        p.add_argument("--translator", default="mlx:mlx-community/gemma-4-12B-it-qat-4bit")
        p.add_argument("--context", type=int, default=4)
        p.add_argument("--cap", type=float, default=12.0)
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--wps", type=float, default=2.7)
        p.add_argument("--min-s", type=float, default=1.0)
        p.add_argument("--budget", type=float, default=20.0)
        p.add_argument("--short-ratio", type=float, default=0.6)
        p.add_argument("--lines", type=int, default=2)
        p.add_argument("--chars", type=int, default=42)
        p.add_argument("--min-line-s", type=float, default=1.2)
    args = ap.parse_args()
    {"translate": translate, "report": report, "page": page}[args.cmd](args)


if __name__ == "__main__":
    main()
