"""Live Voice, the sentence step (docs/live_voice_plan.md §2): caption chunks that reached the screen
become whole sentences the voice can speak.

`SentenceBuilder` is the rule itself, ported from work/tts_bakeoff/src/live_sim/live_sim.py
`sentences()` with the same constants; it knows nothing of clocks or threads. `SpeechMixin` (mixed
into live.app.Live) feeds it from the pipeline, answers "did the speaker pause?" from what is known
live (§2.2), applies the operator's corrections (§2.4), and publishes each sentence to the service
log and to the voice process.

One builder per voice language: "ro" speaks the Romanian translation of an English speaker, "en" the
English translation of a Romanian speaker (§14)."""
from __future__ import annotations

import collections
import os
import re
import threading
import time

import scripture
from captions import _KEEP_CAP, _LOWER

DEFAULTS = {"pause_s": 0.6, "max_words": 30, "cap_words": 45, "comma_lo_s": 0.3, "flush_s": 4.0}
KEEP = 10                       # published sentences an operator's correction can still reach
NO_NEXT = 99.0                  # "nothing follows": a gap longer than any pause
# capitalised wherever they stand (clean_sermon.ALWAYS_CAP in the simulation)
ALWAYS_CAP_RO = {"Dumnezeu", "Dumnezeul", "Dumnezeului", "Domnul", "Domnului", "Isus", "Hristos", "Duhul",
                 "Duhului", "Biblia", "Bibliei", "Biblie", "Pavel", "Tesaloniceni", "Cristos"}
ALWAYS_CAP_EN = {w.capitalize() for w in _KEEP_CAP} | {"I"}
ORD = {"ro": {"1": "Întâi", "2": "A doua", "3": "A treia"}, "en": {"1": "First", "2": "Second", "3": "Third"}}
_STRIP = {"ro": ",.;:?!", "en": ",.;:?!\"'"}
_SENT_END = {"ro": (".", "?", "!"), "en": (".", "?", "!", ":")}


def _numbered_books(lang: str) -> list[str]:
    """The books that take a number (1 Corinteni, 2 Timotei...), from data/bible_books.csv."""
    out = set()
    for b in scripture.BOOKS:
        names = [b.ro if lang == "ro" else b.en]
        names += list(getattr(b, "ro_variants" if lang == "ro" else "en_variants", None) or [])
        for n in names:
            m = re.match(r"^([123]) (.+)$", n or "")
            if m:
                out.add(m[2])
    return sorted(out, key=lambda s: (-len(s), s))


_BOOK_NUM: dict[str, re.Pattern] = {}


def book_numbers(text: str, lang: str) -> str:
    """"2 Timotei" -> "A doua Timotei", "1 Corinthians" -> "First Corinthians" (§2.2 rule 4, §14).
    The simulation's list missed Timotei; this one is the Bible book list. A number that follows
    "capitolul / chapter / versetul / verse" is a chapter or verse, not a book number."""
    if lang not in _BOOK_NUM:
        names = "|".join(map(re.escape, _numbered_books(lang)))
        _BOOK_NUM[lang] = re.compile(rf"(?<![\w:,.])([123]) (?=(?:{names})\b)") if names else None
    pat = _BOOK_NUM[lang]
    if pat is None:
        return text
    before = ("capitolul", "capitolele", "versetul", "versetele", "chapter", "chapters", "verse", "verses")

    def sub(m):
        prev = text[:m.start()].rstrip().rsplit(" ", 1)[-1].lower().strip(",.;:")
        return m[0] if prev in before else ORD[lang][m[1]] + " "
    return pat.sub(sub, text)


def seen_names(text: str, lang: str) -> set[str]:
    """Words capitalised in the middle of a sentence: names (live_sim.proper_nouns)."""
    out, words = set(), text.split()
    for prev, w in zip(words, words[1:]):
        w = w.strip(_STRIP[lang])
        if w[:1].isupper() and not prev.endswith(_SENT_END[lang]):
            out.add(w)
    return out


class SentenceBuilder:
    """Chunks in, sentences out. `add(chunk, gap)` takes one chunk that reached the screen and the
    silence that followed it (seconds until the speaker spoke again; NO_NEXT when nothing followed)
    and returns the sentences it closed (0 or 1)."""

    def __init__(self, lang: str, cfg: dict | None = None, names=(), next_sid=None):
        self.lang = lang
        self.cfg = {**DEFAULTS, **{k: v for k, v in (cfg or {}).items() if k in DEFAULTS}}
        self.names = set(names) | (ALWAYS_CAP_RO if lang == "ro" else ALWAYS_CAP_EN)
        self.parts: list[dict] = []                    # the open sentence
        self.done: collections.deque = collections.deque(maxlen=KEEP)   # published, newest last
        self._n = 0
        self._next_sid = next_sid or self._count
        self.last_id = None                            # the chunk added last (Remove last / Fix last)

    def _count(self) -> int:
        self._n += 1
        return self._n

    # --- the rule ------------------------------------------------------------------------
    def is_name(self, word: str) -> bool:
        w = word.strip(",.;:?!„”\"")
        if self.lang == "en" and w.lower() in _LOWER and w != "I":
            return False
        return w in self.names

    def _fit(self, text: str) -> str:
        """A chunk that continues the open sentence: its capital goes unless it is a name, and a word
        doubled across the boundary is dropped ("…în casa / Casa lui…")."""
        if self.parts:
            ws = text.split()
            first = ws[0] if ws else ""
            if first[:1].isupper() and not self.is_name(first):
                text = first[0].lower() + text[1:]
            prev_words = self.parts[-1]["text"].split()
            prev_last = prev_words[-1].strip(",.;:?!").lower() if prev_words else ""
            if prev_last and first.lower().strip(",.;:?!") == prev_last:
                text = text.split(" ", 1)[1] if " " in text else ""
        return book_numbers(text, self.lang)

    def add(self, chunk: dict, gap: float = NO_NEXT, last: bool = False) -> list[dict]:
        c = self.cfg
        raw = (chunk.get("text") or "").strip()
        self.names |= seen_names(raw, self.lang)
        text = self._fit(raw)
        if not text:
            return [self._close("end")] if self.parts and last else []
        self.last_id = chunk.get("id")
        words = sum(len(p["text"].split()) for p in self.parts) + len(text.split())
        ends = text.endswith(("?", "!", "."))
        part = {**chunk, "text": text, "raw": raw, "known": None}
        if ends and gap >= c["pause_s"]:
            part["known"] = chunk.get("t_end", 0.0) + c["pause_s"]
            self.parts.append(part)
            return [self._close("pause")]
        if last:
            part["text"] = text if ends else text + "."
            self.parts.append(part)
            return [self._close("end")]
        if words >= c["cap_words"] or (words >= c["max_words"] and (text.endswith((",", ";", ":")) or ends)):
            part["text"] = text.rstrip(",;:.") + "."
            self.parts.append(part)
            return [self._close("long")]
        if text.endswith("."):                         # every chunk ends in a period: no pause, a comma
            part["text"] = text[:-1] + ","
        elif not ends and c["comma_lo_s"] <= gap < c["pause_s"] and not text.endswith((",", ";", ":")):
            part["text"] = text + ","                  # a short breath
        self.parts.append(part)
        return []

    @staticmethod
    def _join(parts: list[dict], closed: bool = True) -> str:
        s = re.sub(r"\s+", " ", " ".join(p["text"] for p in parts)).strip()
        if not s:
            return s
        if closed and not s.endswith(("?", "!", ".")):
            s = s.rstrip(",;:") + "."
        return re.sub(r"([?!])\.$", r"\1", s[0].upper() + s[1:])

    def _sentence(self, sid: int, parts: list[dict], why: str) -> dict:
        last = parts[-1]
        shown = last.get("shown", 0.0)
        text = self._join(parts)
        return {"sid": sid, "lang": self.lang, "text": text, "words": len(text.split()), "why": why,
                "chunks": [p.get("id") for p in parts],
                "spk_start": parts[0].get("t_speech", 0.0), "spk_end": last.get("t_end", 0.0),
                "shown_last": shown, "ready": max(shown, last["known"] if last.get("known") is not None else shown),
                "scripture": any(bool(p.get("scripture")) for p in parts),
                "ref": next((p["ref"] for p in parts if p.get("ref")), None),
                "caught_up": max(int(p.get("caught_up") or 1) for p in parts),
                "src": " ".join(p.get("src") or "" for p in parts).strip()}

    def _close(self, why: str) -> dict:
        parts, self.parts = self.parts, []
        s = self._sentence(self._next_sid(), parts, why)
        self.done.append({"sid": s["sid"], "parts": parts, "why": why})
        return s

    def flush(self, why: str = "flush") -> dict | None:
        """Close and return the open sentence (a language switch, Stop, a speaker who trailed off)."""
        return self._close(why) if self.parts else None

    def close_if_ended(self, t: float) -> dict | None:
        """What the speaker said next was not captioned (the recogniser heard nothing, or the phrase was
        dropped), and it ended at `t`: if the open sentence's last chunk ended a sentence and `t` is a
        pause later, the sentence is closed there. In the simulation such a phrase simply was not there,
        so the next captioned chunk came after a pause."""
        if not self.parts:
            return None
        last = self.parts[-1]
        if not last["raw"].endswith((".", "?", "!")) or t - last.get("t_end", 0.0) < self.cfg["pause_s"]:
            return None
        if last["text"].endswith(","):
            last["text"] = last["text"][:-1] + "."
        last["known"] = last.get("t_end", 0.0) + self.cfg["pause_s"]
        return self._close("pause")

    def discard(self) -> int:
        """Drop the open sentence (Hide, Blank, Song); returns how many chunks went."""
        n, self.parts = len(self.parts), []
        return n

    # --- corrections (§2.4) -----------------------------------------------------------------
    def _find(self, chunk_id):
        for i, p in enumerate(self.parts):
            if p.get("id") == chunk_id:
                return None, i
        for d in reversed(self.done):
            for i, p in enumerate(d["parts"]):
                if p.get("id") == chunk_id:
                    return d, i
        return None, None

    def _refit(self, parts: list[dict]):
        """After a change inside a sentence: a part that is no longer last gets a comma for its period."""
        for p in parts[:-1]:
            if p["text"].endswith("."):
                p["text"] = p["text"][:-1] + ","

    def _changed(self, d) -> tuple[str, dict | None]:
        if d is None:
            for p in self.parts:                       # nothing in an open sentence ends it
                if p["text"].endswith("."):
                    p["text"] = p["text"][:-1] + ","
            return "open", None
        if not d["parts"]:
            self.done.remove(d)
            return "retract", {"sid": d["sid"], "lang": self.lang}
        self._refit(d["parts"])
        return "replace", self._sentence(d["sid"], d["parts"], d["why"])

    def retract(self, chunk_id) -> tuple[str, dict | None]:
        """Take a chunk out: ("open", None) when it was in the open sentence, ("replace", sentence) or
        ("retract", {sid}) when it was in a published one, ("none", None) when it is not kept any more."""
        d, i = self._find(chunk_id)
        if i is None:
            return "none", None
        del (self.parts if d is None else d["parts"])[i]
        if self.last_id == chunk_id:
            self.last_id = None
        return self._changed(d)

    def set_text(self, chunk_id, text: str) -> tuple[str, dict | None]:
        """Swap a chunk's text (Fix last, a late verse); empty text takes the chunk out."""
        text = " ".join((text or "").split())
        if not text:
            return self.retract(chunk_id)
        d, i = self._find(chunk_id)
        if i is None:
            return "none", None
        (self.parts if d is None else d["parts"])[i]["text"] = text
        return self._changed(d)


def level_gap(hist, t_end: float, pause_s: float) -> float | None:
    """Seconds of silence after `t_end` from the engine's voice-activity readings `hist`
    ((t, rms, peak, speech), ~10 Hz, the capture clock): 0.0 when the speech never stopped, the time
    to the first speech after a silent reading, NO_NEXT when the silence lasted `pause_s`, and None
    while the readings do not reach `t_end + pause_s` yet. The reading that holds `t_end`, and the one
    after it, still say speech (the meter's flag covers the previous reading too): they are skipped
    only as long as they say so."""
    after = [(r[0], bool(r[3])) for r in hist if r[0] > t_end]
    silent = False
    for t, speech in after:
        if t - t_end > pause_s:
            break
        if not speech:
            silent = True
        elif silent:
            return t - t_end
    if after and after[-1][0] >= t_end + pause_s:
        return NO_NEXT if silent else 0.0
    return None


class SpeechMixin:
    """The pipeline's side of Live Voice: chunks -> sentences -> the log and the voice process."""

    def _mixin_init(self):
        self._speech_lock = threading.RLock()
        self._speech_q: list[dict] = []                # chunks waiting for "did the speaker pause?"
        self._speech_sid = 0
        self._speech_builders: dict[str, SentenceBuilder] = {}
        self._speech_timer: threading.Timer | None = None
        self._speech_flush_timer: threading.Timer | None = None
        self._speech_last_add = 0.0
        self._speech_reading = scripture.Reading()     # §2.1: replayed over every shown chunk
        self._speech_since_verse = 10 ** 9
        self._speech_off: float | None = None          # bench replay: the recording's clock -> now
        self.sentences_n = 0

    # --- settings -----------------------------------------------------------------------
    def voice_cfg(self) -> dict:
        return self.cfg.get("voice") or {}

    def voice_enabled(self) -> bool:
        return bool(self.voice_cfg().get("enabled", False))

    def voice_lang_enabled(self, lang: str) -> bool:
        if not self.voice_enabled():
            return False
        return lang == "ro" or bool((self.voice_cfg().get("en") or {}).get("enabled", True))

    def _speech_next_sid(self) -> int:
        self._speech_sid += 1
        return self._speech_sid

    def _speech_builder(self, lang: str) -> SentenceBuilder:
        b = self._speech_builders.get(lang)
        if b is None:
            b = self._speech_builders[lang] = SentenceBuilder(lang, self.voice_cfg(), next_sid=self._speech_next_sid)
        b.cfg = {**DEFAULTS, **{k: v for k, v in self.voice_cfg().items() if k in DEFAULTS}}
        b.names |= self._speech_names(lang)
        return b

    def _speech_names(self, lang: str) -> set[str]:
        """Names known before they are heard: the glossary's and today's names, keywords_en.txt."""
        out = set()
        gloss = getattr(self, "gloss", None)
        for n in list(getattr(gloss, "names", None) or []):
            out.update(w for w in str(n).split() if w[:1].isupper())
        for r in list(getattr(gloss, "rows", None) or []):
            v = str(r.get(lang) or "")
            if v[:1].isupper() and " " not in v:
                out.add(v)
        for fn in ("_session_words", "user_keywords", "_bundled_keywords"):
            try:
                for w in getattr(self, fn)() if hasattr(self, fn) else []:
                    out.update(x for x in str(w).split() if x[:1].isupper())
            except Exception:                           # noqa: BLE001  a missing list is no reason to stop
                pass
        return out

    # --- clocks ---------------------------------------------------------------------------
    def _speech_offset(self) -> float:
        """Phrase times are the engine's. In a service that is this process's clock too. In a bench
        replay the engine stamps the recording's clock (LIVE_FILE_SPEED / LIVE_FILE_T0): the offset is
        the smallest (arrival − capture time) seen over the level readings, so a replay and a service
        look the same to the voice (§2.3)."""
        if float(os.environ.get("LIVE_FILE_SPEED") or 0) <= 0:
            return 0.0
        return self._speech_off or 0.0

    def _speech_level(self, ev: dict, now: float):
        """Called for every level reading (lifecycle._on_level)."""
        t = ev.get("t")
        if isinstance(t, (int, float)) and float(os.environ.get("LIVE_FILE_SPEED") or 0) > 0:
            off = now - float(t)
            if self._speech_off is None or off < self._speech_off:
                self._speech_off = off

    # --- from the pipeline --------------------------------------------------------------------
    def _speech_chunk(self, ev: dict, tgt: str, text: str, src: str, outcome: str, t_display: float,
                      verse=None, redo_prev: str | None = None):
        """A chunk went through the pipeline (called from _handle_locked after _show). `text` is the
        translation before tidy_clause; `redo_prev` (a late verse) is what is left of the previous
        chunk's text, "" when nothing is."""
        if not self.voice_enabled():
            return
        with self._speech_lock:
            ref = self._speech_reading.update(src or "")
            self._speech_since_verse = 0 if verse else self._speech_since_verse + 1
            holy = ref is not None or self._speech_since_verse <= scripture.CHUNKS
            if outcome not in ("ok", "held") or not self.voice_lang_enabled(tgt):
                return
            other = "en" if tgt == "ro" else "ro"
            self._speech_pump(force=True, only=other)   # the speaker switched language: decide what waits,
            self._speech_flush(other, "flush")          # then close the other language's sentence
            if redo_prev is not None:
                self._speech_redo(tgt, redo_prev)
            v = verse[0] if isinstance(verse, list) and verse else verse
            self._speech_q.append({
                "id": ev.get("id"), "lang": tgt, "text": text, "src": src,
                "t_speech": ev.get("t_speech", ev.get("t_start")), "t_end": ev.get("t_end"),
                "shown": t_display - self._speech_offset(), "wall": time.time(), "scripture": holy,
                "ref": (v or {}).get("ref") if isinstance(v, dict) else (ref.name(tgt) if ref is not None else None),
                "caught_up": ev.get("caught_up", 1)})
            self._speech_pump()

    def _speech_gap(self, c: dict, nxt: dict | None) -> float | None:
        """Seconds from the end of chunk `c` to the next speech; None when it is not known yet (§2.2)."""
        pause = float(self.voice_cfg().get("pause_s", DEFAULTS["pause_s"]))
        t_end = c["t_end"]
        starts = [nxt["t_speech"]] if nxt is not None else []
        waiting = []
        try:
            with self._work.mutex:
                waiting = list(self._work.queue)
        except Exception:                               # noqa: BLE001
            pass
        for e in [getattr(self, "_pending", None), getattr(self, "_current", None)] + waiting:
            if isinstance(e, dict) and e.get("id") != c["id"]:
                s = e.get("t_speech", e.get("t_start"))
                if isinstance(s, (int, float)) and s >= t_end - 0.05:
                    starts.append(s)
        if starts:
            return max(0.0, min(starts) - t_end)
        gap = level_gap(list(getattr(self, "_level_hist", None) or []), t_end, pause)
        if gap is None and time.time() - c["wall"] > pause + 1.5:
            return NO_NEXT                              # no level readings (no engine): a pause
        return gap

    def _speech_pump(self, force: bool = False, only: str | None = None):
        """Decide the waiting chunks in order. `force`: do not wait for the level readings."""
        with self._speech_lock:
            while self._speech_q:
                c = self._speech_q[0]
                if only is not None and c["lang"] != only:
                    break
                nxt = self._speech_q[1] if len(self._speech_q) > 1 else None
                gap = self._speech_gap(c, nxt)
                if gap is None:
                    if not force:
                        self._speech_arm(0.15)
                        break
                    gap = NO_NEXT
                self._speech_q.pop(0)
                b = self._speech_builder(c["lang"])
                for s in b.add(c, gap):
                    self._speech_publish(s)
                self._speech_last_add = time.time()
            if any(b.parts for b in self._speech_builders.values()):
                self._speech_arm_flush()

    def _speech_arm(self, after_s: float):
        t = self._speech_timer
        if t is not None and t.is_alive():
            return
        self._speech_timer = threading.Timer(after_s, self._speech_pump)
        self._speech_timer.daemon = True
        self._speech_timer.start()

    def _speech_arm_flush(self):
        t = self._speech_flush_timer
        if t is not None and t.is_alive():
            return
        self._speech_flush_timer = threading.Timer(0.5, self._speech_flush_tick)
        self._speech_flush_timer.daemon = True
        self._speech_flush_timer.start()

    def _speech_idle(self) -> bool:
        """Nothing is on its way: no phrase held, queued or being translated, and no speech just now."""
        if getattr(self, "_pending", None) is not None or getattr(self, "_current", None) is not None:
            return False
        try:
            if not self._work.empty():
                return False
        except Exception:                               # noqa: BLE001
            pass
        last = getattr(self, "_last_voice_t", None)
        return last is None or time.time() - last > 1.0

    def _speech_flush_tick(self):
        """A speaker who trails off must not hold a sentence back for ever: `flush_s` after the last
        chunk with nothing on its way, the open sentence is published; after 3 × flush_s whatever
        the engine hears (§2.2)."""
        with self._speech_lock:
            self._speech_flush_timer = None
            if self._speech_q or not any(b.parts for b in self._speech_builders.values()):
                return
            flush_s = float(self.voice_cfg().get("flush_s", DEFAULTS["flush_s"]))
            waited = time.time() - self._speech_last_add
            if waited >= 3 * flush_s or (waited >= flush_s and self._speech_idle()):
                for lang in list(self._speech_builders):
                    self._speech_flush(lang, "flush")
            else:
                self._speech_arm_flush()

    def _speech_dropped(self, ev: dict):
        """A phrase that was not captioned (pipeline._log_failure): see SentenceBuilder.close_if_ended."""
        t = ev.get("t_end")
        if not self.voice_enabled() or not isinstance(t, (int, float)):
            return
        with self._speech_lock:
            self._speech_pump(force=True)
            for b in self._speech_builders.values():
                s = b.close_if_ended(t)
                if s is not None:
                    self._speech_publish(s)

    def _speech_flush(self, lang: str, why: str = "flush"):
        with self._speech_lock:
            b = self._speech_builders.get(lang)
            s = b.flush(why) if b is not None else None
            if s is not None:
                self._speech_publish(s)

    def _speech_publish(self, s: dict):
        off = self._speech_offset()
        s = {**s, **{k: round(s[k] + off, 3) for k in ("spk_start", "spk_end", "shown_last", "ready")}}
        self.sentences_n += 1
        self._log_event({"event": "sentence", **s})
        send = getattr(self, "_voice_send", None)
        if send is not None:
            send({"type": "sentence", **s})

    # --- the operator (§2.4) --------------------------------------------------------------------
    def _speech_tell(self, kind: str, s: dict | None, why: str):
        """A published sentence changed: tell the voice (replace / retract) and log it."""
        if kind not in ("replace", "retract") or s is None:
            return
        msg = {"type": kind, "sid": s["sid"], "lang": s["lang"], **({"text": s["text"]} if kind == "replace" else {})}
        self._log_event({"event": "sentence_" + kind, "why": why, **{k: v for k, v in msg.items() if k != "type"}})
        send = getattr(self, "_voice_send", None)
        if send is not None:
            send(msg)

    def _speech_last(self) -> tuple[SentenceBuilder | None, object]:
        """The builder and id of the chunk shown last (waiting chunks are decided first)."""
        self._speech_pump(force=True)
        best = None
        for b in self._speech_builders.values():
            if b.last_id is not None:
                d, i = b._find(b.last_id)
                if i is not None:
                    p = (b.parts if d is None else d["parts"])[i]
                    if best is None or p.get("wall", 0) > best[2]:
                        best = (b, b.last_id, p.get("wall", 0))
        return (best[0], best[1]) if best else (None, None)

    def _speech_retract(self):
        """Remove last (Backspace, Live Remote): the chunk shown last leaves its sentence."""
        if not self.voice_enabled():
            return
        with self._speech_lock:
            b, cid = self._speech_last()
            if b is not None:
                self._speech_tell(*b.retract(cid), why="retract")

    def _speech_correct(self, text: str):
        """Fix last (E): the operator's text replaces the chunk shown last."""
        if not self.voice_enabled():
            return
        with self._speech_lock:
            b, cid = self._speech_last()
            if b is not None:
                self._speech_tell(*b.set_text(cid, text), why="correct")

    def _speech_redo(self, lang: str, prev_text: str):
        """A late verse: the previous chunk keeps only `prev_text` (its parts before the verse)."""
        self._speech_pump(force=True)
        b = self._speech_builders.get(lang)
        if b is not None and b.last_id is not None:
            self._speech_tell(*b.set_text(b.last_id, prev_text), why="late_verse")

    def _speech_discard(self, why: str):
        """Hide, Blank, Song: the open sentence and the chunks still waiting are dropped."""
        with self._speech_lock:
            n = len(self._speech_q) + sum(b.discard() for b in self._speech_builders.values())
            self._speech_q.clear()
            self._speech_reading.reset()
            self._speech_since_verse = 10 ** 9
            if n and self.voice_enabled():
                self._log_event({"event": "sentence_discard", "why": why, "chunks": n})

    def _speech_stop(self):
        """Stop: what is open is published (`flush`), so the last words are spoken."""
        with self._speech_lock:
            self._speech_pump(force=True)
            for lang in list(self._speech_builders):
                self._speech_flush(lang, "flush")
