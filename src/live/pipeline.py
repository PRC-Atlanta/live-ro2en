"""Committed clause -> translation -> caption -> log record, in arrival order by one worker.
Mixed into live.app.Live (owned by S4)."""
from __future__ import annotations

import collections
import datetime as dt
import gc
import json
import pathlib
import queue
import re
import sys
import threading
import time
import unicodedata

import gate
import guards
import names as namelist
import overlap
import paths
import sentences
import song
import translate
from captions import tidy_clause
from langid import score as lang_score
from segmenter import DANGLING, NEGATION_END, split_clauses
from textnorm import apply_spellings, fold_diacritics, tokens

# A change of these needs a new engine process; every other profile setting is applied live.
RESTART_KEYS = ("stt_model", "stt_venv")
AUTO_ACTIONS_KEEP = 20


def is_echo(src: str, out: str, min_words: int = 4) -> bool:
    """The translator returned the source unchanged (same words, diacritics ignored).
    Short clauses are exempt: a name or 'Amin' may legitimately be the same."""
    s, o = [fold_diacritics(t) for t in tokens(src)], [fold_diacritics(t) for t in tokens(out)]
    return len(s) >= min_words and s == o


def is_name_list(text: str) -> bool:
    """Mostly capitalised words ("Despina, Carmen, Dan, David, Cecilia."): names, which the
    translator rightly leaves as they are (idea 15). Needs at least two words."""
    words = re.findall(r"[^\W\d_]+", text)
    return len(words) >= 2 and sum(w[0].isupper() for w in words) >= 0.75 * len(words)


def dangling(text: str) -> bool:
    words = tokens(text)
    if not words:
        return False
    if words[-1] in DANGLING or words[-1] in NEGATION_END:
        return True
    return words[-1] == "mai" and len(words) > 1 and words[-2] in NEGATION_END     # «nu mai.»


# idea 35 (2026-11-15 morning): the meaning reversed because the object, the subject or the «nu»
# came in the next piece: «pentru că iubește. | păcatul» → "because it loves Him.", «așa ne învață.
# | stăpânitorul lumii acesteia» → "that is what He teaches us", «Nimicul … nu este forma. |
# eșecului» → "Nothing … is the form.", «singurul mijlocitor. Maria. | nu Maria» → "Mary.".
# A piece that ends like this waits for the next one when that one follows at once (OPEN_GAP_S).
TRANSITIVE_END = frozenset(fold_diacritics(w) for w in (
    "iubește iubesc iubim iubiți iubea urăște urăsc urâm urâți ura învață învăța învățăm "
    "spune spun spunem spuneți spunea zice zic zicem zicea cere cerea caută căuta primește "
    "aduce arată arăta poruncește cunoaște".split()))
SAY_END = frozenset(fold_diacritics(w) for w in
                    "învață învăța învățăm spune spunea zice zicea arată arăta cere cerea poruncește".split())
_OBJECT_CLITIC = frozenset(fold_diacritics(w) for w in "îl il l o îi ii i le ne vă va te mă ma ți ti mi".split())
_NU_ESTE = (("nu", "este"), ("nu", "e"), ("nu", "era"), ("nu", "sunt"))
OPEN_GAP_S = 1.0               # «nu este forma.» | «eșecului» came 0.99 s apart (bench 2026-11-15)
OPEN_EXTRA_S = 6.0             # such a piece may merge past hold_max_s by this much («…pentru că iubește.» was 6.8 s)


def open_end(text: str) -> bool:
    """A Romanian piece whose last words need the next piece to keep their meaning (idea 35):
    a bare transitive verb with no object («pentru că iubește.»; a verb of saying or teaching also
    after a clitic, its subject still to come: «așa ne învață.»), «nu este + noun» at the end («nu
    este forma.»), or a last sentence of one capitalised word («… mijlocitor. Maria.»)."""
    t = text.strip()
    if not t or t.endswith(("?", "!")):
        return False
    words = [fold_diacritics(w) for w in tokens(t)]
    if len(words) < 2:
        return False
    last, before = words[-1], words[-2]
    if last in TRANSITIVE_END and (last in SAY_END or before not in _OBJECT_CLITIC):
        return True
    if len(words) >= 3 and tuple(words[-3:-1]) in _NU_ESTE and len(last) >= 4:
        return True
    sents = [s for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]
    if len(sents) >= 2:
        one = re.findall(r"[^\W\d_]+", sents[-1])
        if len(one) == 1 and one[0][:1].isupper() and len(one[0]) >= 3 \
                and fold_diacritics(one[0].lower()) not in _ALONE_FOLDED:
            return True
    return False


# a Romanian piece of 1-2 words that is not a sentence: clitics and small words alone («să-l.» →
# "call out.", 2026-11-08 evening) or a scrap of a word («rom.»). Words a whole utterance can be:
_FUNCTION_RO = frozenset("să l il îl o i îi le se ne vă te mă și n a al ale de la cu pe în din că "
                         "care ca dar iar sau mai prin spre".split())
_ALONE_RO = frozenset("da nu bun bine așa ok hai aha mda păi eu tu el ea noi voi ei ele ce cum cât cine "
                      "oh vai uite iată amin gata sigur".split())
_ALONE_FOLDED = frozenset(fold_diacritics(w) for w in _ALONE_RO) | {"doamne", "aleluia", "osana", "isus", "hristos"}


# engine.QUIET_MAX_S (not imported: engine.py loads numpy and the VAD): the engine sends `quiet`
# events up to 3 s into a silence, so a sentence_gap_s or sentence_quiet_s above it would never be
# reached by one (only by sentence_wait_s); they are clamped to it where used
QUIET_MAX_S = 3.0
CUT_GAP_S = 0.7                # idea 25: the next piece follows at once (the word went on across the cut)
_LAST_WORD = re.compile(r"([^\W\d_]+)[^\w]*$")
_FIRST_WORD = re.compile(r"^[^\w]*([^\W\d_]+)")


def cut_word(prev_src: str, src: str, lex: set) -> str | None:
    """Idea 25 (2026-11-15 morning): the previous piece's last word is the recogniser's guess at the
    start of this piece's first word: «parca.» | «parcurge», «autor.» | «autentică», «păstrat.» |
    «păstor», «multe.» | «multiplice», «nepărere.» | «neprihănire». The previous piece without that
    word, joined to this one; None when the words are not one cut word. A real word that is all of
    the next one's start («bine.» | «binecuvântat») is a word of its own."""
    m1, m2 = _LAST_WORD.search(prev_src or ""), _FIRST_WORD.search(src or "")
    if not m1 or not m2 or len(tokens(prev_src)) < 2:
        return None
    if not m2.group(1)[:1].islower():
        return None             # a word that goes on across a cut goes on in lower case (review 2026-11-18)
    w, v = fold_diacritics(m1.group(1).lower()), fold_diacritics(m2.group(1).lower())
    if len(w) < 4 or v == w:
        return None
    cp = 0
    while cp < min(len(w), len(v)) and w[cp] == v[cp]:
        cp += 1
    known = w in lex
    # a real word needs a long shared start and a longer word after it («multe.» | «multiplice»); two
    # real words of the same length are two words («bogat sau sărac.» | «sărat», bench 2026-11-15)
    one = (known and cp >= 4 and cp / len(w) >= 0.6 and len(v) > len(w) and not v.startswith(w)) \
        or (not known and cp >= 3 and len(v) > len(w))      # the lexicon lacks many inflected forms
    if not one:
        return None
    return f"{prev_src[:m1.start(1)].rstrip()} {src.lstrip()}".strip()


def seam_word(prev_src: str, src: str, seam: list | None) -> str | None:
    """Idea 25, the recogniser's side: ContextSTT found the previous chunk's last word unmatched
    when it heard it again with the next chunk, and this piece's first word is the whole word
    («de important.» | «importantă. cât», «la dispoziție.» | «dispoziția noastră», «dea ex.» |
    «exemple»). The previous piece without its last word, joined to this one; None when the
    seam does not name these two words (a repair or spelling changed them)."""
    if not seam or len(seam) != 2:
        return None
    m1, m2 = _LAST_WORD.search(prev_src or ""), _FIRST_WORD.search(src or "")
    if not m1 or not m2:
        return None
    key = lambda x: fold_diacritics("".join(re.findall(r"[^\W\d_]+", x)).lower())   # noqa: E731
    if key(m1.group(1)) != key(seam[0]) or key(m2.group(1)) != key(seam[1]):
        return None
    return f"{prev_src[:m1.start(1)].rstrip()} {src.lstrip()}".strip()


def _spoken_ago(ev: dict) -> float | None:
    """Seconds since the chunk's speech ended (its t_end is wall-clock time), for the caption
    box's lag guard; None when unknown or implausible."""
    try:
        ago = time.time() - float(ev["t_end"])
    except (KeyError, TypeError, ValueError):
        return None
    return ago if 0.0 <= ago < 120.0 else None


_CHRISTMAS = re.compile(r"\bChristmas\b(?!\s+Child)")


def christmas_to_hristos(text: str, prev: str = "", info: dict | None = None) -> str:
    """Idea 28 (2026-11-15): the Romanian recogniser wrote «Hristos» as the English "Christmas"
    (the boost for "Operation Christmas Child" pulls it): "Christmas is risen!" on screen. In a
    Romanian piece it is «Hristos», unless «Crăciun» is said here or just before."""
    if "Christmas" not in text or re.search(r"cr[ăa]ciun", f"{prev} {text}", re.I):
        return text
    fixed = _CHRISTMAS.sub("Hristos", text)
    if fixed != text and info is not None:
        info["christmas"] = text
    return fixed


def _echo_key(word: str) -> str:
    """A word without its ending or diacritics, for echo matching («mădularele» = «madularele»)."""
    return fold_diacritics(word.lower())[:5]


def scrap(text: str) -> bool:
    """A Romanian piece too small to mean anything on its own (idea 17): ≤ 2 words, all of them
    small function words, or a single word of ≤ 3 letters that is not an utterance by itself.
    Questions and exclamations stay."""
    t = text.strip()
    if not t or t.endswith(("?", "!")):
        return False
    words = [w.lower() for w in re.findall(r"[^\W\d_]+", t)]
    if not words or len(words) > 2 or re.search(r"\d", t):
        return False
    if all(w in _FUNCTION_RO for w in words):
        return True
    return len(words) == 1 and len(words[0]) <= 3 and words[0] not in _ALONE_RO \
        and fold_diacritics(words[0]) not in {fold_diacritics(w) for w in _ALONE_RO}


EN_FOREIGN_MAX = 2             # idea 12: an English piece with this many non-English words is not captioned EN→RO


def non_english(text: str, names=frozenset()) -> int:
    """Lower-case words of an English transcript that are not English («kut», «coborila», «cu»,
    «tre»): the English recogniser writing Romanian speech. Capitalised words (names) don't count."""
    lex = gate.lexicon()
    en, known = lex["en"], lex["names"] | set(names)
    n = 0
    for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", text):
        if w[:1].isupper() or "'" in w:
            continue
        f = fold_diacritics(w.lower())
        if f not in en and f not in known:
            n += 1
    return n


RO_IN_EN_MARGIN = 0.2          # idea 12: the Romanian transcript's real-word share must beat the English one's by this
RO_IN_EN_MIN_WORDS = 3


def romanian_inside_english(en_text: str, ro_text: str, lex: dict, names=frozenset(),
                            margin: float = RO_IN_EN_MARGIN) -> dict | None:
    """Idea 12 (2026-11-08 evening): Romanian said while the captions are in English mode. The English
    recogniser writes it as romanised scraps ("in FSN capitolopatru, versetus shaptashpe", "Voi Parinsi")
    that are few enough to pass the gate. When the Romanian recogniser's transcript of the same audio
    has a clearly higher share of real words (by `margin`) and reads as Romanian (lang_score > 0.15),
    that one is the text: the shares and the score, else None."""
    ro_text = (ro_text or "").strip()
    if not ro_text or len(tokens(ro_text)) < RO_IN_EN_MIN_WORDS:
        return None
    score = lang_score(ro_text)
    if score <= 0.15:
        return None
    en_share = gate.features(en_text, "en", lex, names)["real_share"]
    ro_share = gate.features(ro_text, "ro", lex, names)["real_share"]
    if ro_share - en_share < margin:
        return None
    return {"en_share": en_share, "ro_share": ro_share, "ro_score": round(score, 2)}


def _whole_phrase(tr, lang: str, tgt: str) -> bool:
    """The translator for this direction takes a whole phrase in one call instead of clause by
    clause (the experimental online Claude translator: each call is a network round trip)."""
    m = tr.trs.get((lang, tgt), tr) if isinstance(getattr(tr, "trs", None), dict) else tr   # ByDirection
    return bool(getattr(m, "whole_phrase", False))


def pack_parts(parts: list[str], max_words: int) -> list[str]:
    """Join consecutive clauses into calls of at most max_words (a clause longer than that
    stays whole), so a backlog is translated in few calls without overrunning max_tokens."""
    out: list[str] = []
    for p in parts:
        if out and len(out[-1].split()) + len(p.split()) <= max_words:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def merge_clauses(a: dict, b: dict) -> dict:
    """Clause `a` followed by clause `b` as one clause (text fields joined, a's start). The
    English texts lose a start that repeats a's end (idea 16: recognised twice)."""
    b = {**b, **{k: overlap.trim(a.get(k) or "", b.get(k) or "")[0] for k in ("multi", "en")
                 if a.get(k) and b.get(k) and b["t_start"] - a["t_end"] < OVERLAP_GAP_S}}
    b = {**b, "ro_wait": bool(a.get("ro_wait") and b.get("ro_wait"))}    # only when neither part has its words
    # where b's words start inside the joined Romanian transcript (joint census: a word lost at a cut)
    at = len((a.get("ro") or "").split())
    joints = [*(a.get("joints") or []),
              {"at": at, "gap": round(b.get("t_speech", b["t_start"]) - a["t_end"], 3),
               **({"reheard": b["reheard"]} if b.get("reheard") else {})},
              *({**j, "at": j["at"] + at} for j in b.get("joints") or [])]
    # what belongs to the merged clause's start is a's: the re-heard tail, the recogniser's seam
    # (the joint before a's first word, for seam_word) and whether it starts with a split piece
    start = ("reheard", "seam", "sent_split")
    b = {k: v for k, v in b.items() if k not in start}
    # caught up (_catch_up): the sentences' pieces and cut words, all of them, in order
    sent = {k: [*(a.get(k) or []), *(b.get(k) or [])] for k in ("sent_pieces", "sent_cut_words")
            if a.get(k) or b.get(k)}
    return {**b, **{k: a[k] for k in start if a.get(k)}, **sent, "joints": joints,
            **{k: f"{a.get(k) or ''} {b.get(k) or ''}".strip() for k in ("ro", "multi", "en")},
            "t_start": a["t_start"], "t_speech": a.get("t_speech", a["t_start"]),
            # stt_s stays b's (what delayed b's t_emit, so vad_wait_s stays right); total kept apart
            "stt_s_total": a.get("stt_s_total", a["stt_s"]) + b["stt_s"], "audio_s": a["audio_s"] + b["audio_s"]}


def sentence_fields(ev: dict) -> dict:
    """For the log: how the sentence buffer closed this phrase, how many pieces it holds and how long
    its last piece waited in the buffer (live.translate_sentences)."""
    if not ev.get("sentence"):
        return {}
    return {"sentence": ev["sentence"], "sent_pieces": len(ev.get("sent_pieces") or []),
            "sent_wait_s": ev.get("sent_wait_s"),
            **({"sent_cut_words": ev["sent_cut_words"]} if ev.get("sent_cut_words") else {})}


def piece_parts(text: str, pieces: list[str]) -> list[str]:
    """A sentence back in its pieces' clauses (a Bible verse in it is matched piece by piece): cut at
    the pieces' word counts when they add up, else at its clause punctuation."""
    words, counts = text.split(), [len(p.split()) for p in pieces]
    segs = [text]
    if sum(counts) == len(words):
        segs, i = [], 0
        for n in counts:
            if n:
                segs.append(" ".join(words[i:i + n]))
            i += n
    out = []
    for seg in segs:
        clauses, tail = split_clauses(seg)
        out += clauses + ([tail] if tail else [])
    return out


def joint_fields(ev: dict) -> dict:
    """For the log: the committed tail as the recogniser heard it again at this piece's start, and
    the chunk joints inside a piece made of several chunks (scripts/seam_catalogue.py lost)."""
    return {k: ev[k] for k in ("reheard", "joints") if ev.get(k)}


# --- safety filter: what a translator must never put on the church screen --------------------
def _key(text: str) -> tuple[str, ...]:
    return tuple(fold_diacritics(t) for t in tokens(text))


def _max_repeat(keys: list[str], max_unit: int = 8) -> int:
    """The most times any run of 1..max_unit words repeats back to back."""
    best = 1
    for u in range(1, max_unit + 1):
        for i in range(len(keys) - 2 * u + 1):
            k = 1
            while keys[i + k * u:i + (k + 1) * u] == keys[i:i + u]:
                k += 1
            best = max(best, k)
    return best


def trim_loops(out: str, src: str, min_repeats: int = 4, max_unit: int = 8) -> tuple[str, bool]:
    """A translator caught in a loop ('and he said, and he said, and he said, ...') repeats a
    phrase back to back. Keep its first occurrence. A source that itself repeats as much
    ('Aleluia, aleluia, aleluia, aleluia') is the speaker's, and is left alone.
    "Holy, holy, holy" (3 times) is below the default threshold."""
    words = out.split()
    keys = [" ".join(_key(w)) for w in words]
    if _max_repeat(keys, max_unit) < min_repeats or _max_repeat(list(_key(src)), max_unit) >= min_repeats:
        return out, False
    for u in range(1, max_unit + 1):           # shortest unit first: 'the the the the' -> 'the'
        i = 0
        while i + 2 * u <= len(keys):
            k = 1
            while keys[i + k * u:i + (k + 1) * u] == keys[i:i + u]:
                k += 1
            if k >= min_repeats:
                del words[i + u:i + k * u], keys[i + u:i + k * u]
            i += 1
    return " ".join(words), True


def overlong(out: str, src: str, ratio: float = 3.0, min_words: int = 8) -> bool:
    """More than `ratio` times the source's words (and at least min_words): the translator
    added text that was not said (a re-translated context sentence, an invented ending)."""
    n_out, n_src = len(out.split()), max(len(src.split()), 1)
    return bool(ratio) and n_out >= min_words and n_out > ratio * n_src


def blocklist_re(words) -> re.Pattern | None:
    """One pattern for the blocklist: whole words or phrases, any case, diacritics optional."""
    alts = sorted({" ".join(_key(w)) for w in words if _key(w)}, key=len, reverse=True)
    if not alts:
        return None
    return re.compile(r"(?<![\w-])(?:" + "|".join(r"\W+".join(map(re.escape, a.split())) for a in alts)
                      + r")(?![\w-])", re.IGNORECASE)


def mask(text: str, pat: re.Pattern | None, sub: str = "***") -> tuple[str, int]:
    """Replace every blocklist match with `sub`; returns (text, matches)."""
    if pat is None:
        return text, 0
    text = unicodedata.normalize("NFC", text)
    folded = fold_diacritics(text.lower())
    if len(folded) != len(text):               # a case mapping changed the length: match as is
        folded = text
    spans = [m.span() for m in pat.finditer(folded)]
    for a, b in reversed(spans):
        text = text[:a] + sub + text[b:]
    return text, len(spans)


DRAIN_TIMEOUT_S = 30.0                   # Start/Stop wait at most this for the clauses still in hand
KEEPALIVE_TICK_S = 15.0                  # how often idle translators are checked
OVERLAP_GAP_S = 2.0
ECHO_TAIL_WORDS = 5                      # idea 20: a 2-3 word echo is all in the previous piece's last 5 words
ECHO_MAX_WORDS = 3
ECHO_GAP_S = 1.5                        # a one-word echo of the last word comes right after it                      # a repeat across a longer pause was said twice, not heard twice

class PipelineMixin:
    RESTART_KEYS = RESTART_KEYS

    def _mixin_init(self):
        self.auto_actions: list[dict] = []     # what the app did by itself, for the operator
        self._spare_tr: dict = {}              # translator spec -> loaded translator (hot switch)
        self._spare_lock = threading.Lock()    # a spare being kept warm is not handed over mid-call
        self._tr_used: dict = {}               # id(translator) -> when it last ran (keep-warm)
        self._keepalive: threading.Thread | None = None
        self._recent_out: collections.deque = collections.deque(maxlen=2)   # (source key, output key)
        self._current: dict | None = None      # the clause the worker is on (backlog)
        self._block: tuple = (None, None)      # (words, compiled pattern)
        self._pending_cap = 0.0                # the span a held phrase may grow to (_on_clause)
        self._prev_src: tuple = ("", "", None) # (language, text, t_end) of the last phrase (overlap)
        self._song_det = song.SongDetector()
        threading.Thread(target=gate.lexicon, daemon=True).start()   # the gate's word lists (~0.2 s)
        self.register_state(self._pipeline_state)

    # --- pace, backlog, auto actions -----------------------------------------------------
    def _backlog(self) -> dict:
        """Clauses waiting for the translator (plus the one it is on) and the oldest's age."""
        with self._work.mutex:
            waiting = list(self._work.queue)
        cur = self._current
        evs = ([cur] if cur else []) + waiting
        ends = [e["t_end"] for e in evs if isinstance(e, dict) and isinstance(e.get("t_end"), (int, float))]
        return {"n": len(evs), "oldest_s": round(max(0.0, time.time() - min(ends)), 1) if ends else None}

    def _pipeline_state(self) -> dict:
        backlog = self._backlog()
        warn = float(self.lc.get("lag_warn_s", 6.0) or 0)
        delay = max(self.behind_s or 0.0, backlog["oldest_s"] or 0.0)
        if warn and delay > 1.5 * warn:
            pace = "behind"
        elif (warn and delay > warn) or (self.stt_behind and self.running):
            pace = "slow"
        else:
            pace = "ok"
        return {"pace": {"state": pace, "behind_s": round(delay, 1) if pace != "ok" and delay else None},
                "backlog": backlog, "auto_actions": list(self.auto_actions)}

    def _auto(self, msg: str):
        """Something the app did by itself (caught up, dropped a looping caption...), shown to
        the operator, newest last. The clause's own log record already says what happened."""
        with self.lock:
            self.auto_actions = (self.auto_actions + [{"t": round(time.time(), 3), "msg": msg}])[-AUTO_ACTIONS_KEEP:]

    # --- hot Fast <-> Quality switch (called by LifecycleMixin.set_profile) --------------
    def _keep_both_translators(self) -> bool:
        """Keep the other profile's translator loaded: `live.keep_both_translators`
        true/false, or auto (the default) = this Mac has the RAM for it (>= 24 GB)."""
        v = self.lc.get("keep_both_translators", "auto")
        if v is True:
            return True
        import model_setup
        roomy = model_setup.hardware()["ram_bytes"] >= model_setup.QUALITY_RAM
        if v is False:                       # "Never", unless Live Voice needs Gemma for its summaries while
            return roomy and self._voice_wants_gemma()   # Quickest translates with NLLB (plan §2.5)
        return roomy

    def _voice_wants_gemma(self) -> bool:
        return bool(getattr(self, "voice_enabled", lambda: False)())

    def _load_one(self, spec: str, lc: dict, direction: tuple = ("ro", "en")):
        """Translator `spec` loaded and warmed up in the direction it serves (outside _proc_lock)."""
        tr = translate.make_translator(spec)
        if hasattr(tr, "context_clauses"):
            tr.context_clauses = int(lc.get("context_clauses", 2))
        tr.translate(translate.WARMUP[direction], *direction, self.gloss)    # warm-up
        if hasattr(tr, "reset_context"):
            tr.reset_context()
        self._tr_used[id(tr)] = time.time()
        self._start_keepalive()
        return tr

    def _load_warm(self, lc: dict, have: dict | None = None):
        """A loaded, warmed-up translator for profile settings `lc` (outside _proc_lock): one,
        or one per direction (translate.ByDirection). Members in `have` (spec -> loaded) are
        reused, the others loaded and added to it."""
        tr = translate.assemble(lc, {} if have is None else have, lambda spec, d: self._load_one(spec, lc, d))
        if hasattr(tr, "context_clauses"):
            tr.context_clauses = int(lc.get("context_clauses", 2))
        return tr

    def _live_members(self) -> dict:
        """spec -> loaded translator of the current profile (one, or one per direction)."""
        return translate.members(self.tr, self.lc)

    def _last_used(self, tr) -> float:
        """When the loaded translator `tr` last ran (as the live translator, a member or a spare)."""
        live = self.tr
        used = live.used.get(id(tr), 0) if isinstance(live, translate.ByDirection) else 0
        return max(self._tr_used.get(id(tr), 0), used)

    # --- keep-warm: an idle model is paged out by macOS and its next phrase waits for it ------
    def _start_keepalive(self):
        """One background thread per app: every few seconds, `_keepalive_tick`."""
        t = getattr(self, "_keepalive", None)
        if t is not None and t.is_alive():
            return
        def loop():
            while not getattr(self, "_shutting_down", False):
                time.sleep(KEEPALIVE_TICK_S)
                try:
                    self._keepalive_tick()
                except Exception:                # noqa: BLE001 - never take the app down
                    pass
        self._keepalive = threading.Thread(target=loop, daemon=True, name="translator-keepalive")
        self._keepalive.start()

    def _keepalive_tick(self, now: float | None = None) -> int:
        """Translate a word on every loaded translator idle for translator_keepalive_s, so its
        weights stay in memory: after ~20 min as an unused spare, Gemma's first phrase took
        ~27 s (paged out). The live translator only when it is free; its context is kept.
        Returns how many were touched."""
        every = float(self.lc.get("translator_keepalive_s", 60) or 0)
        if not every:
            return 0
        now = time.time() if now is None else now
        n = 0
        for tr in list(self._spare_tr.values()):
            if now - self._tr_used.get(id(tr), 0) < every:
                continue
            with self._spare_lock:
                if tr in self._spare_tr.values():     # not handed over meanwhile
                    self._ping(tr)
                    n += 1
        tr = self.tr                                  # one, or one per direction: each on its own
        idle = [m for m in self._live_members().values() if now - self._last_used(m) >= every]
        if idle and self._proc_lock.acquire(blocking=False):
            try:
                if tr is self.tr:
                    for m in idle:
                        self._ping(m)
                        n += 1
            finally:
                self._proc_lock.release()
        return n

    def _ping(self, tr):
        ctx = list(tr.context) if isinstance(getattr(tr, "context", None), list) else None
        try:
            tr.translate("Amin.", "ro", "en")
        finally:
            if ctx is not None:
                tr.context = ctx
            self._tr_used[id(tr)] = time.time()

    def preload_translators(self):
        """Load every profile's translator that is not loaded yet, as spares, so the first
        switch is instant too. Only when RAM allows; slow (call it from a thread)."""
        if not self._keep_both_translators():
            return
        for name in self.cfg.get("profiles", {}):
            lc = self._profile_cfg(name)
            for d, spec in translate.direction_specs(lc).items():   # one per direction may differ
                if spec in translate.direction_specs(self.lc).values() or spec in self._spare_tr:
                    continue
                tr = self._load_one(spec, lc, d)
                with self._proc_lock:
                    if spec not in translate.direction_specs(self.lc).values():
                        if self._keep_both_translators():  # "Never" may have been chosen while it loaded
                            self._spare_tr.setdefault(spec, tr)
                    elif spec not in self._live_members():   # switched to this profile while it
                        self._spare_tr.setdefault(spec, tr)  # loaded: load_translator takes it

    def _free_memory(self):
        gc.collect()
        mx = sys.modules.get("mlx.core")
        clear = getattr(mx, "clear_cache", None) or getattr(getattr(mx, "metal", None), "clear_cache", None)
        if clear:
            try:
                clear()
            except Exception:                    # noqa: BLE001 - best effort
                pass

    def _swap_profile(self, name: str, new: dict) -> dict:
        """Put profile `name` (settings `new`) in place between two clauses. With RAM for
        both, the new translator loads while the old one keeps translating, and the old one
        is kept as a spare; otherwise (or with no spare) the old one is freed first and the
        clauses that arrive meanwhile wait in the queue (caught up after). Returns details
        for the log."""
        old_key, key = translate.translator_key(self.lc), translate.translator_key(new)
        need = set(translate.direction_specs(new).values())
        keep, loaded, t0 = self._keep_both_translators(), False, time.time()
        tr, pool = None, {}                      # pool: spec -> loaded, the new profile's members
        if key != old_key:
            with self._spare_lock:               # waits for a keep-warm call on it to end
                pool = {s: self._spare_tr.pop(s) for s in need if s in self._spare_tr}
            pool.update({s: m for s, m in self._live_members().items() if s in need})   # shared
            if need - set(pool) and keep and self.running:
                tr, loaded = self._load_warm(new, pool), True    # the old one translates meanwhile
        wait_s = 0.0
        with self._proc_lock:                    # never pulled from under a translation
            t1 = time.time()
            if key != old_key:
                old = self._live_members()
                for m in old.values():
                    self._tr_used[id(m)] = self._last_used(m)    # its idle time goes with it
                self.tr = None
                for s, m in old.items():
                    if s in need:
                        pool.setdefault(s, m)
                    elif keep:
                        self._spare_tr[s] = m
                old = None
                if not keep:
                    self._spare_tr.clear()
                    self._free_memory()
                missing = need - set(pool)
                if tr is None and (self.running or not missing):   # warm reload: the engine keeps listening
                    try:
                        tr, loaded = self._load_warm(new, pool), bool(missing)
                    except Exception:
                        self.tr = self._load_warm(self.lc, pool)   # back to the old profile, still live
                        raise
                elif tr is None:                 # idle: what is loaded waits for load_translator
                    for s, m in pool.items():
                        self._spare_tr.setdefault(s, m)
                self.tr = tr
            if hasattr(self, "_profile_cfg"):    # a Settings save during the load went into cfg
                cur = {k: v for k, v in self._profile_cfg(name).items() if k != "translator_en_ro"}
                new = {**cur, **{k: new[k] for k in ("translator", "translator_en_ro") if k in new}}   # as loaded
            if self.tr is not None and hasattr(self.tr, "context_clauses"):
                self.tr.context_clauses = int(new.get("context_clauses", 2))
            if self.tr is not None and hasattr(self.tr, "reset_context"):
                self.tr.reset_context()
            self.profile, self.lc = name, new
            self._recent_out.clear()
            wait_s = time.time() - t1
        return {"translator": key, "loaded": loaded, "kept_spare": keep and key != old_key,
                "load_s": round(time.time() - t0, 2), "blocked_s": round(wait_s, 2)}

    # --- clauses -------------------------------------------------------------------------
    def _on_clause(self, ev: dict):
        """Hold very short fragments ('Dar acum.') briefly and merge them with the next
        clause: translating 1-3 words alone is poor. Sentence-final fragments go now.
        Slow speakers (idea 20): a phrase under phrase_min_words, or one that stops on "din",
        "și", "of"..., waits longer (hold_slow_s) and may grow to hold_max_s, because most of
        a slow speaker's seconds are pauses.
        With `live.translate_sentences` a Romanian piece goes to the sentence buffer instead
        (_on_piece): the translator gets whole sentences, and these holds are not needed."""
        if self._sentences_on():
            if self._on_piece(ev):
                return
        elif self._sent_evs():                         # the setting was just switched off
            with self.lock:
                self._sent_flush("flush")
        with self.lock:
            if self._timer:
                self._timer.cancel()
            self._hold_gen += 1                        # a timer that already fired is now stale
            p = self._pending
            cap = self.lc.get("max_chunk_s", 0)
            p_cap = getattr(self, "_pending_cap", cap)
            if p and p_cap and ev["t_end"] - p.get("t_speech", p["t_start"]) > p_cap:
                # merging would make the first word wait too long: send the fragment on its own
                self._pending = None
                self._submit(p)
                p = None
            if p and p.get("open_end") and ev["t_start"] - p["t_end"] > OPEN_GAP_S:
                self._pending = None                   # idea 35: held for its object, but the next piece
                self._submit(p)                        # came after a pause: a new thought, sent apart
                p = None
            if p:
                ev = merge_clauses(p, ev)
                ev.pop("open_end", None)
            self._pending = None
            text = ev.get("multi") if self.lang.current == "en" else ev["ro"]
            text = text or ev["ro"] or ""
            n, final = len(text.split()), text.rstrip().endswith(("?", "!"))
            opened = (self.lang.current != "en" and self.lc.get("hold_open_end", True) and open_end(text))
            if opened:
                ev["open_end"] = True
            slow = n > 0 and not final and (n < int(self.lc.get("phrase_min_words", 0) or 0)
                                  or (self.lc.get("hold_dangling", True) and dangling(text)) or opened)
            short = not final and n < self.lc.get("hold_min_words", 4)
            limit = max(cap, float(self.lc.get("hold_max_s", 10.0) or 0)) if slow and cap else cap
            if opened and limit:
                limit = max(limit, float(self.lc.get("hold_max_s", 10.0) or 0) + OPEN_EXTRA_S)
            if (slow or short) and not (limit and ev["t_end"] - ev.get("t_speech", ev["t_start"]) > limit):
                self._pending, self._pending_cap = ev, limit
                wait = float(self.lc.get("hold_slow_s", 6.5)) if slow else self.lc.get("hold_s", 1.2)
                self._timer = threading.Timer(wait, self._flush, args=(self._hold_gen,))
                self._timer.daemon = True
                self._timer.start()
                return
            self._submit(ev)                 # under the lock: a timer flush cannot slip in between

    def _flush(self, gen: int | None = None):
        """Send the held fragment. A hold timer passes its generation: if a newer clause has
        arrived since (and merged or re-held the fragment), the timer is stale and does nothing."""
        with self.lock:
            if gen is not None and gen != self._hold_gen:
                return
            ev, self._pending = self._pending, None
            if ev:
                self._submit(ev)
            if gen is None:                            # Stop, end of input: the open sentence goes too
                self._sent_flush("flush")

    # --- whole sentences (live.translate_sentences, sentences.py) --------------------------
    def _sentences_on(self) -> bool:
        return bool(self.lc.get("translate_sentences", False))

    def _sent_cutter(self) -> sentences.SentenceCutter:
        c = getattr(self, "_sent_cut", None)
        if c is None:
            c = self._sent_cut = sentences.SentenceCutter()
        c.cap_s = float(self.lc.get("sentence_cap_s", 12.0) or 12.0)
        c.gap_s = self._sent_gap_s()
        return c

    def _sent_gap_s(self) -> float:
        return min(float(self.lc.get("sentence_gap_s", 2.5) or 2.5), QUIET_MAX_S)   # see QUIET_MAX_S

    def _sent_evs(self) -> list[dict]:
        """The pieces waiting in the sentence buffer (Live Voice asks when the speaker spoke next)."""
        c = getattr(self, "_sent_cut", None)
        return list(c.evs) if c is not None else []

    def _on_piece(self, ev: dict) -> bool:
        """A piece into the sentence buffer; the sentences it closes go to the translator. False when
        the piece is not for the buffer (English captions, or no Romanian transcript to cut by): the
        buffer is sent first and the piece takes the usual way."""
        with self.lock:
            ro, multi = (ev.get("ro") or "").strip(), (ev.get("multi") or "").strip()
            if self.lang.current == "en" or (not ro and multi and not ev.get("ro_wait")):
                self._sent_flush("lang")
                return False
            if self._pending:                          # held before the setting was switched on
                if self._timer:
                    self._timer.cancel()
                self._hold_gen += 1
                p, self._pending = self._pending, None
                self._submit(p)
            for unit, why in self._sent_cutter().add({**ev, "_arrived": time.time()}):
                self._sent_submit_safe(unit, why)
            self._sent_arm()
            return True

    def _sent_submit(self, unit: list[dict], why: str):
        """One sentence (its pieces) to the translator as one clause. Inside it the recogniser's fake
        full stops go, and a word cut in two at a piece joint is one word again (idea 25)."""
        lex = gate.lexicon()["ro"] if self.lc.get("cut_word_redo", True) else None
        ev, fixed = unit[0], []
        for b in unit[1:]:
            a_ro, b_ro = ev.get("ro") or "", b.get("ro") or ""
            if lex is not None and a_ro and b_ro and b.get("t_speech", b["t_start"]) - ev["t_end"] <= CUT_GAP_S:
                joined = cut_word(a_ro, b_ro, lex)
                if joined is None and self.lc.get("cut_word_seam", True):
                    joined = seam_word(a_ro, b_ro, b.get("seam"))
                if joined is not None:
                    fixed.append([a_ro.split()[-1], b_ro.split()[0]])
                    ev = {**ev, "ro": joined[:len(joined) - len(b_ro.lstrip())].rstrip()}
            ev = merge_clauses(ev, b)
        arrived = ev.pop("_arrived", None)
        # starts with the tail of a piece the cutter split: its start time is the cut, not a pause
        # (_handle_locked does no cut-word or echo check across it)
        split = {"sent_split": True} if unit[0].get("split_of") is not None else {}
        ev = {**ev, **split, "ro": sentences.sentence_text(ev.get("ro") or ""), "sentence": why,
              "sent_pieces": [e.get("ro") or "" for e in unit],
              "sent_wait_s": round(time.time() - arrived, 3) if arrived else None,
              **({"sent_cut_words": fixed} if fixed else {})}
        self._submit(ev)

    def _sent_submit_safe(self, unit: list[dict], why: str):
        """_sent_submit, but the pieces already taken from the buffer are never lost: if joining
        them fails, each goes to the translator as it came (logged to app_errors.log)."""
        try:
            self._sent_submit(unit, why)
        except Exception:                               # noqa: BLE001
            import traceback
            self._app_error(f"sentence buffer ({why}): {len(unit)} pieces sent as they came\n"
                            f"{traceback.format_exc()}")
            for e in unit:
                self._submit({k: v for k, v in e.items() if k != "_arrived"})

    def _app_error(self, text: str):
        """A line in app_errors.log; never raises (it is called where an error is already being handled)."""
        try:
            logs = paths.log_dir(self.root)
            logs.mkdir(parents=True, exist_ok=True)
            with open(logs / "app_errors.log", "a") as f:
                f.write(f"{dt.datetime.now().isoformat()} {text}\n")
        except Exception:                               # noqa: BLE001
            pass

    def _sent_arm(self):
        """A safety net when no `quiet` event comes (an engine without them): the open sentence goes
        after sentence_wait_s with no new piece."""
        t = getattr(self, "_sent_timer", None)
        if t is not None:
            t.cancel()
        self._sent_gen = getattr(self, "_sent_gen", 0) + 1
        if not self._sent_evs():
            return
        self._sent_timer = threading.Timer(float(self.lc.get("sentence_wait_s", 8.0) or 8.0),
                                           self._sent_timeout, args=(self._sent_gen,))
        self._sent_timer.daemon = True
        self._sent_timer.start()

    def _sent_timeout(self, gen: int):
        with self.lock:
            if gen == getattr(self, "_sent_gen", 0):
                self._sent_flush("wait")

    def _sent_flush(self, why: str):
        """Send the open sentence now (call under self.lock)."""
        c = getattr(self, "_sent_cut", None)
        u = c.flush(why) if c is not None else None
        if u is not None:
            self._sent_submit_safe(*u)
        t = getattr(self, "_sent_timer", None)
        if t is not None:
            t.cancel()
            self._sent_timer = None

    def _on_quiet(self, ev: dict):
        """The engine heard silence (`quiet`: t = now on the audio clock, since = the last voiced
        frame, everything before it already sent as pieces). A buffered sentence that ends in
        . ! ? goes after sentence_quiet_s of it, anything after sentence_gap_s."""
        with self.lock:
            c = getattr(self, "_sent_cut", None)
            if c is None or not c.evs or not isinstance(ev.get("t"), (int, float)):
                return
            if isinstance(ev.get("since"), (int, float)) and ev["since"] > c.last_end + 0.3:
                return                                 # speech after the buffer, not here yet
            silence = ev["t"] - c.last_end
            gap = self._sent_gap_s()
            quiet = min(float(self.lc.get("sentence_quiet_s", 1.0) or 1.0), QUIET_MAX_S)   # see QUIET_MAX_S
            if silence >= gap:
                self._sent_flush("gap")
            elif c.waiting_end and silence >= quiet:
                self._sent_flush("quiet")

    def _submit(self, ev: dict):
        self._work.put(ev)
        with self._worker_lock:
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._work_loop, daemon=True)
                self._worker.start()

    def _work_loop(self):
        while True:
            ev = self._work.get()
            n = 1
            try:
                self._current = ev
                ev, n = self._catch_up(ev)
                self._current = ev
                self._handle(ev)
                self._current = None
                self._broadcast()
            finally:
                self._current = None
                for _ in range(n):
                    self._work.task_done()

    def _catch_up(self, ev: dict) -> tuple[dict, int]:
        """Behind (this clause already late, more waiting): merge the waiting clauses into
        one, translated in a single call, so the captions get back to the speaker instead of
        falling further behind clause by clause. Nothing is dropped."""
        warn_s = float(self.lc.get("lag_warn_s", 6.0))
        cap = int(self.lc.get("catchup_max_words", 60))
        if not warn_s or not cap or time.time() - ev["t_end"] < warn_s / 2:
            return ev, 1
        n = 1
        while max(len((ev.get(k) or "").split()) for k in ("ro", "multi", "en")) < cap:
            try:
                nxt = self._work.get_nowait()
            except queue.Empty:
                break
            ev, n = merge_clauses(ev, nxt), n + 1
        if n > 1:
            ev = {**ev, "caught_up": n}
            with self.lock:
                self.counts["caught_up"] += n
            self._auto(f"Caught up: {n} phrases shown together")
        return ev, n

    def _drain(self, timeout_s: float = DRAIN_TIMEOUT_S):
        """Wait until every submitted clause has been translated, shown and logged, but not for
        ever (Start and Stop wait here under _control): a worker that died is started again, and
        what is still waiting after timeout_s is dropped and logged (`drain_timeout`)."""
        deadline = time.time() + timeout_s
        while self._work.unfinished_tasks:
            if time.time() >= deadline:
                dropped = []
                while True:
                    try:
                        ev = self._work.get_nowait()
                    except queue.Empty:
                        break
                    self._work.task_done()
                    dropped.append(ev)
                    try:
                        self._log_failure(ev, "drain_timeout")
                    except Exception:                   # noqa: BLE001
                        pass
                self._app_error(f"drain: still busy after {timeout_s:.0f} s; {len(dropped)} waiting "
                                f"clauses dropped: {[str(e.get('ro', ''))[:60] for e in dropped]!r}")
                return
            with self._worker_lock:
                if not self._work.empty() and not (self._worker and self._worker.is_alive()):
                    self._worker = threading.Thread(target=self._work_loop, daemon=True)
                    self._worker.start()
            time.sleep(0.05)

    def _handle(self, ev: dict):
        """Never let one bad chunk stop the service: log it and carry on."""
        with self._proc_lock:
            try:
                self._handle_locked(ev)
            except Exception as e:                       # noqa: BLE001
                import traceback
                self.status = f"error on one chunk ({type(e).__name__}); still listening"
                # a second error here (disk full, a bad log path) must not end the worker thread
                self._app_error(f"{str(ev.get('ro', ''))[:200]!r}\n{traceback.format_exc()}")
                try:
                    self._log_failure(ev, "error", error=f"{type(e).__name__}: {e}")
                except Exception:                       # noqa: BLE001
                    self._app_error(f"logging the error failed\n{traceback.format_exc()}")

    def _log_failure(self, ev: dict, outcome: str, **extra):
        """A clause that produced no caption still gets a log record and a count, so empty
        or failed chunks are visible in the metrics instead of silently missing."""
        rec = {"id": ev.get("id"), "outcome": outcome, "t_start": ev.get("t_start"), "t_end": ev.get("t_end"),
               "ro": ev.get("ro", ""), "multi": ev.get("multi", ""), "en": ev.get("en", ""),
               "audio_s": ev.get("audio_s"), "stt_s": ev.get("stt_s"), "profile": self.profile,
               **self._input_fields(ev), **joint_fields(ev), **sentence_fields(ev),
               "time": dt.datetime.now().isoformat(timespec="milliseconds"), **extra}
        with self.lock:
            self.counts[outcome] += 1
        self._write_log(rec)
        if hasattr(self, "_speech_dropped") and outcome != "stt_error":   # Live Voice (speech.py)
            try:
                self._speech_dropped(ev)
            except Exception:                           # noqa: BLE001  the voice never stops a caption
                self._state_failed(self._speech_dropped)

    def _guard_fallback(self):
        """A loaded NLLB for the guard's last try (idea 5): Quality keeps one for English →
        Romanian, and NLLB translates Romanian → English too. None when none is loaded."""
        pool = list(translate.members(self.tr, self.lc).values()) if self.tr is not None else []
        try:
            pool += list((getattr(self, "_spare_tr", None) or {}).values())
        except RuntimeError:                            # the spares thread changed it meanwhile
            pass
        return next((t for t in pool if isinstance(t, translate.NLLBTranslator)), None)

    def _input_fields(self, ev: dict) -> dict:
        """The input gain and level while the phrase was spoken (lifecycle._level_over)."""
        lvl = None
        if hasattr(self, "_level_over") and ev.get("t_start") is not None and ev.get("t_end") is not None:
            lvl = self._level_over(ev["t_start"], ev["t_end"])
        if hasattr(self, "_gain_now"):
            return {"gain_db": self._gain_now(), "gain_auto": self._gain_auto(), "level": lvl}
        return {"gain_db": float(self.lc.get("input_gain_db", 0) or 0), "gain_auto": False, "level": lvl}

    def _log_event(self, rec: dict):
        self._write_log({**rec, "time": dt.datetime.now().isoformat(timespec="milliseconds")})

    def _write_log(self, rec: dict):
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _source(self, ev: dict) -> tuple[str, str, dict]:
        """(language, text to translate, log fields). In auto mode the multilingual transcript
        decides, with the Romanian recogniser's own transcript as a second vote (langid).
        Romanian with nothing from the Romanian recogniser: the multilingual text, unless it
        reads as English (idea 10); clean English there is translated EN→RO this once (idea 12)."""
        ro, multi = ev.get("ro") or "", ev.get("multi") or ""
        auto = self.lang.mode == "auto"
        lang = self.lang.update(multi or ro, ro=ro if multi else None)
        english = multi                                   # English is Parakeet's text
        if lang == "en":
            return "en", english or ro, {}
        if ro.strip() or not multi.strip():
            return "ro", ro, {}
        if ev.get("ro_wait"):
            # idea 25: the Romanian recogniser keeps this chunk's words for the next one; Parakeet's
            # text of the same audio would say them twice (luigi-mitoi 24:59, 17 words, bench 2026-11-15)
            return "ro", "", {}
        s = lang_score(multi)
        if auto and s <= 0 and self._gate_ok(english, "en") and not self._gate_ok(multi, "ro") \
                and not non_english(english, self._gate_names()):
            # an English sentence the RO model missed; a lone one with no Romanian transcript to check
            # it against must be all English words: fast Romanian read during Gen 26:24 came out as
            # "She shall kut the children." → «Ea va tăia copiii.» on screen (2026-11-15, idea 12)
            return "en", english, {"fallback": "english"}
        if s >= 0:
            return "ro", multi, {"fallback": "multi"}   # the RO model heard nothing ("binecuvinteze.")
        return "ro", "", {}

    def _gate_names(self) -> set:
        rows = getattr(self.gloss, "rows", None) or []
        if getattr(self, "_gate_rows", None) is not rows:
            self._gate_rows, self._gate_name_set = rows, gate.names_from(rows)
        return self._gate_name_set

    def _repair_names(self, text: str, lang: str, info: dict) -> str:
        """The Romanian recogniser's near-misses of today's names, back to the name (idea 2):
        'familia Pha' → 'familia Puha', 'Drago' → 'Dragoș'; logged as `names_fixed`."""
        if lang != "ro" or getattr(self, "gloss", None) is None:
            return text
        text, fixed = namelist.repair_ro(text, namelist.ro_names(self.gloss), gate.lexicon()["ro"],
                                           gate.lexicon()["names"])
        if fixed:
            info["names_fixed"] = [list(f) for f in fixed]
        return text

    def _echo_word(self, ev: dict, text: str, check: bool = True) -> bool:
        """A one-word phrase that repeats the previous phrase's last word, right after it: the
        recogniser re-hearing the tail ("…pentru cele trei familii." then "famili." → its own
        caption "families."; "trecut.", "dominicală.", 2026-10-25). Logged as `echo_word`."""
        import difflib
        words = re.findall(r"[^\W\d_]+", text)
        prev, prev_end = getattr(self, "_last_word", ("", None))
        tail = getattr(self, "_last_tail", ())
        self._last_word = (fold_diacritics(words[-1].lower()) if words else "", ev["t_end"])
        self._last_tail = tuple(_echo_key(w) for w in words[-ECHO_TAIL_WORDS:])
        if not check or not words or not prev or prev_end is None or ev["t_start"] - prev_end > ECHO_GAP_S:
            return False
        if len(words) == 1:
            w = fold_diacritics(words[0].lower())
            if len(w) < 4 or difflib.SequenceMatcher(None, w, prev).ratio() < 0.75:
                return False
        # idea 20 (2026-11-15 morning): 2-3 words re-heard: «…de mădularele ei.» then «madularele ei.»
        # → "her limbs." twice, «îmbolnăvești de inimă.» → "heart disease" twice
        elif len(words) > ECHO_MAX_WORDS or not all(_echo_key(w) in tail for w in words) \
                or not any(len(w) >= 4 for w in words):
            return False
        self._log_failure(ev, "echo_word", source=text)
        return True

    def _repair_numbers(self, ev: dict, text: str, lang: str, info: dict) -> str:
        """Numbers the Romanian recogniser mangles (idea 5): a spoken range glued into one
        number ("4567.000" -> "4.000-7.000"), and a number word the other recogniser heard
        longer ("doi fii" where it heard "12 fii")."""
        if lang != "ro":
            return text
        fixed = guards.unglue(text)
        if fixed != text:
            info["numbers_unglued"] = text
        split = guards.split_ne(fixed, gate.lexicon()["ro"])
        if split != fixed:
            info["ne_split"], fixed = fixed, split
        alt = ev.get("multi") or ""
        fixed, days = guards.month_days(fixed, alt)
        if days:
            info["month_days"] = [list(x) for x in days]
        if alt and lang_score(alt) > 0:                 # the other transcript is Romanian too
            fixed, longer = guards.longer_numbers(fixed, alt)
            if longer:
                info["numbers_longer"] = [list(x) for x in longer]
        return fixed

    def _gate_min_real(self) -> float:
        return float(self.lc.get("gate_min_real", 0.6) or 0)

    def _gate_ok(self, text: str, lang: str) -> bool:
        if not self._gate_min_real():
            return lang == "ro"                         # gate off: no English rescue, as before
        return gate.check(text, lang, gate.lexicon(), self._gate_names(), self._gate_min_real())[0]

    def _gate(self, ev: dict, src: str, lang: str) -> tuple[str | None, str, dict]:
        """The confidence gate (idea 17): (text to translate or None, language, log fields).
        Garbled sentences are cut; a phrase with nothing reliable left is logged as
        `low_confidence` and not translated. When the other recogniser's transcript of the same
        audio is clean in its own language, that one is translated instead (in auto mode):
        English heard by the Romanian model as "Gay va polua...", "Slavă lui Dumnezeu" heard by
        the English one as "slava ludum nezukum tum"."""
        min_real = self._gate_min_real()
        if not min_real:
            return src, lang, {}
        names = self._gate_names()
        if lang == "en" and self.lang.mode == "auto" and self.lang.home == "ro" and self.lc.get("ro_inside_en", True):
            # idea 12: Romanian said in English mode passes the gate as romanised scraps; the Romanian
            # recogniser's transcript of the same audio is the text when it is clearly the better one
            ro_text = apply_spellings(ev.get("ro") or "", self.cfg.get("spellings") or {})
            better = romanian_inside_english(src, ro_text, gate.lexicon(), names)
            if better:
                info = {"gate_switched": src, "ro_inside_en": better}
                ro_text = self._repair_names(ro_text, "ro", info)
                with self.lock:
                    self.counts["ro_inside_en"] += 1
                self._ro_in_en = getattr(self, "_ro_in_en", 0) + 1
                if self._ro_in_en >= 2:                 # two in a row: the speaker is back in Romanian
                    self.lang.go_home()                 # (the way back took a minute on 2026-11-08, 19:17)
                    self._ro_in_en = 0
                return ro_text, "ro", info
            self._ro_in_en = 0
            if non_english(src, names) >= EN_FOREIGN_MAX:   # "Get one coborila up cu tre de woman."
                self._log_failure(ev, "low_confidence", lang=lang, source=src, reason="not_english",
                                  features=gate.features(src, lang, gate.lexicon(), names))
                return None, lang, {}
        kept, why, cut = gate.screen(src, lang, gate.lexicon(), names, min_real)
        if kept and not cut:
            return src, lang, {}
        other = "en" if lang == "ro" else "ro"
        alt = (ev.get("multi") if other == "en" else ev.get("ro")) or ""
        alt = apply_spellings(alt, self.cfg.get("spellings") or {})
        if (not kept and self.lang.mode == "auto" and alt.strip() and alt.strip() != src.strip()
                and (lang_score(alt) < -0.15 if other == "en" else lang_score(alt) > 0.15)
                and gate.screen(alt, other, gate.lexicon(), names, min_real) == (alt.strip(), "", [])):
            return alt, other, {"gate_switched": src}
        if not kept and gate.screen(src, other, gate.lexicon(), names, min_real) == (src.strip(), "", []):
            return src, lang, {}                        # clean speech in the other language: a quote, shown as is
        if kept:
            with self.lock:
                self.counts["gate_cut"] += 1
            return kept, lang, {"gate_cut": cut}
        self._log_failure(ev, "low_confidence", lang=lang, source=src, reason=why,
                          features=gate.features(src, lang, gate.lexicon(), names))
        return None, lang, {}

    def _trim_overlap(self, ev: dict, lang: str, text: str) -> tuple[str | None, dict]:
        """English text without a start that repeats the end of the previous phrase (idea 16):
        (text, log fields), or None when the whole phrase was a repeat (logged)."""
        prev_lang, prev, prev_end = getattr(self, "_prev_src", ("", "", None))
        self._prev_src = (lang, text, ev["t_end"])
        if (lang != "en" or prev_lang != "en" or prev_end is None or not self.lc.get("overlap_trim", True)
                or ev["t_start"] - prev_end >= OVERLAP_GAP_S):
            return text, {}
        kept, dropped = overlap.trim(prev, text, lang)
        if not dropped:
            return text, {}
        with self.lock:
            self.counts["overlap_trimmed"] += 1
        if not kept.strip():
            self._log_failure(ev, "overlap_repeat", lang=lang, source=text)
            return None, {}
        return kept, {"overlap_trimmed": dropped}

    def _song_hold(self, ev: dict, lang: str, text: str) -> bool:
        """During a song (the operator's Song key, or singing detected with live.song_detect)
        the phrase is logged as `song` and not translated (idea 18). Every phrase feeds the
        detector, so it knows the speech level when a song starts."""
        inp = self._input_fields(ev)
        det = getattr(self, "_song_det", None)
        on, f = det.update(text, ev.get("t_speech", ev["t_start"]), ev["t_end"], inp.get("level"),
                           inp.get("gain_db")) if det else (False, {})
        if self.lc.get("song_detect", False) and hasattr(self, "song"):
            if on and not self.song_on and time.time() >= getattr(self, "_song_snooze", 0.0):
                self.song(True, auto=True)
            elif not on and self.song_on and self.song_auto:
                self.song(False, auto=True)
        if not getattr(self, "song_on", False):
            return False
        self._log_failure(ev, "song", lang=lang, source=text, song={**f, "auto": self.song_auto})
        return True

    def _handle_locked(self, ev: dict):
        # the parts of the previous phrase's caption, only while it is the last one in the box
        last_out, self._last_out = getattr(self, "_last_out", None), None
        last_src = getattr(self, "_last_src", "")
        # live.translate_sentences: this phrase starts where the sentence buffer cut a piece (its start
        # time is the cut, so the 0 s "gap" is not a pause), or the previous phrase ended a sentence
        # there («…noi. | Este»): no word goes on across it (cut_word, seam_word), and after a cut
        # no echo check either (review 2026-11-18)
        prev_sentence, self._prev_sentence = getattr(self, "_prev_sentence", None), ev.get("sentence")
        at_split = bool(ev.get("sentence") and ev.get("sent_split"))
        at_sentence_end = at_split or bool(ev.get("sentence") and prev_sentence == "end")
        # a sentence that ended in a phrase that is then dropped (scrap, echo, gate...) still ended:
        # the next caption starts a new sentence; set here, before the early returns (a shown
        # phrase sets it again below)
        cap_ended = getattr(self, "_cap_ended", False)
        self._cap_ended = cap_ended or ev.get("sentence") in sentences.ENDS
        lang, src_text, src_info = self._source(ev)
        src_text = apply_spellings(src_text, self.cfg.get("spellings") or {})
        if self._song_hold(ev, lang, src_text):          # a song: not captioned (logged)
            return
        src_text, trimmed = self._trim_overlap(ev, lang, src_text)
        if src_text is None:                            # all of it said in the last phrase (logged)
            return
        src_info.update(trimmed)
        src_text = self._repair_names(src_text, lang, src_info)
        src_text = self._repair_numbers(ev, src_text, lang, src_info)
        if lang == "ro":
            src_text = christmas_to_hristos(src_text, getattr(self, "_prev_piece_src", ""), src_info)
        if self._echo_word(ev, src_text, check=not at_split):   # "trecute." then "trecut." alone (logged)
            return
        if lang == "ro" and scrap(src_text):            # «să-l.» → "call out.", «rom.» (logged)
            self._log_failure(ev, "scrap", source=src_text)
            return
        if not src_text.strip():
            self._log_failure(ev, "empty_stt", lang=lang)
            return
        src_text, lang, gated = self._gate(ev, src_text, lang)
        if src_text is None:                            # unreliable recognition (logged)
            return
        src_info.update(gated)
        tgt = "en" if lang == "ro" else "ro"
        tr = self._translator() if hasattr(self, "_translator") else self.tr   # AI or local (aimode.py)
        if hasattr(tr, "begin_phrase"):
            tr.begin_phrase()
        prev_piece, self._prev_piece_src = getattr(self, "_prev_piece_src", ""), src_text
        # idea 25: the previous caption's last word was the start of this piece's first word («înainte
        # de a parca.» | «parcurge câteva versete» → "before we park."): both are translated again as one
        # piece, without that word, in place of the previous caption
        cut_redo = False
        last_end = getattr(self, "_last_end", None)
        if lang == "ro" and last_out and last_src and last_end is not None and self.lc.get("cut_word_redo", True) \
                and not at_sentence_end and ev.get("t_speech", ev["t_start"]) - last_end <= CUT_GAP_S:
            joined = cut_word(last_src, src_text, gate.lexicon()["ro"])
            by_seam = joined is None and self.lc.get("cut_word_seam", True)
            if by_seam:
                joined = seam_word(last_src, src_text, ev.get("seam"))
            if joined is not None:
                src_info["cut_word"] = {"previous": last_src, "replaced_prev": " ".join(last_out),
                                        **({"seam": ev["seam"]} if by_seam else {})}
                src_text, cut_redo = joined, True
                ctx = getattr(tr, "context", None)
                if isinstance(ctx, list) and ctx:
                    tr.context = ctx[:-1]               # the previous piece is translated again, not context
        clauses, tail = split_clauses(src_text)
        parts = clauses + ([tail] if tail else [])        # at a pause everything is committed
        if _whole_phrase(tr, lang, tgt):             # an online translator: one round trip per phrase
            parts = [src_text]
        elif ev.get("sentence") and lang == "ro":
            # a whole sentence in one call, unless one of its pieces is part of a Bible verse: then
            # piece by piece as before, so the verse keeps its published text (verses.shown)
            pieces = [apply_spellings(p, self.cfg.get("spellings") or {}) for p in ev.get("sent_pieces") or []]
            if len(pieces) > 1 and translate.verse_ahead(tr, pieces, lang, tgt):
                parts = piece_parts(src_text, pieces)
                src_info["sentence_verse"] = True
            elif ev.get("caught_up"):
                parts = piece_parts(src_text, pieces)   # several sentences: packed into bounded calls below
            else:
                parts = [src_text]
        if ev.get("caught_up"):                           # catching up: few calls, each bounded
            parts = pack_parts(parts, int(self.lc.get("catchup_max_words", 60)))
        t0 = time.time()
        out, truncated, verse, is_verse, vlogs = [], False, [], [], []
        for p in parts:
            out.append(tr.translate(p, lang, tgt, self.gloss, use_context=True)
                       if hasattr(tr, "context") else tr.translate(p, lang, tgt, self.gloss))
            truncated |= bool(getattr(tr, "last_truncated", False))
            verse += [getattr(tr, "last_verse", None)] if getattr(tr, "last_verse", None) else []
            is_verse.append(bool(getattr(tr, "last_verse", None)))
            vlogs.append(getattr(tr, "last_verse", None) or {})
        # a published verse is not checked for numbers/negations ("nu voi duce lipsă" = "I shall
        # lack nothing"); the other parts of the same phrase still are
        if not verse:
            translation, guard = guards.check(tr, src_text, " ".join(o for o in out if o),
                                              lang, tgt, self.gloss, fallback=self._guard_fallback())
        else:
            guard = {}
            for i, (p, o) in enumerate(zip(parts, out)):
                if o and not is_verse[i]:
                    out[i], g = guards.check(tr, p, o, lang, tgt, self.gloss,
                                             fallback=self._guard_fallback())
                    guard = guard or g
            # idea 3 (2026-11-15): a verse matched one part late starts with the part before it, which
            # the translator already did: "He said, “I am the door, I am the door. If anyone enters…"
            # (John 10:9), Eph 1:3, Rev 2:4. That part goes when it says the verse again.
            for i in range(1, len(out)):
                if is_verse[i] and vlogs[i].get("replace_prev") and not is_verse[i - 1] and out[i - 1]:
                    kept = translate.around_verse(out[i - 1], out[i])
                    if kept != out[i - 1]:
                        src_info.setdefault("verse_dropped", []).append(out[i - 1])
                        out[i - 1] = kept
            translation = " ".join(o for o in out if o)
        # a verse matched one piece late (Daniel 3:18): its caption starts with the previous
        # phrase's last part, so it replaces that phrase's caption, keeping the parts before
        # (but not one that says the verse again: "I am the living water," He said, out, “If anyone
        # is thirsty…", John 7:37, 2026-11-15)
        redo = bool(is_verse and is_verse[0] and verse and verse[0].get("replace_prev") and last_out)
        if redo:
            keep = [k for k in (translate.around_verse(x, out[0]) for x in last_out[:-1]) if k]
            translation = " ".join(keep + [translation])
            src_info["replaced_prev"] = " ".join(last_out)
        if lang == "ro" and tgt == "en" and not verse and self.lc.get("pronoun_check", True):
            fixed = guards.lower_people(translation, src_text, prev_piece)
            if fixed != translation:                    # idea 11: You/He for a person, no God word near
                src_info["pronouns_lowered"] = translation
                translation = fixed
        if guards.odd_figures(src_text):                # "1.271701 milion": never shown as a figure
            translation = guards.hide_odd(src_text, translation)
            src_info["odd_figure"] = True
        t_tr = time.time() - t0
        used = set(getattr(tr, "engines_used", None) or ()) if tr is not self.tr else {"local"}
        # AI chosen: "ai" when only the API answered, or when no model was asked at all (a published
        # verse); "local_fallback" when the local model answered any part of it
        engine = "local" if tr is self.tr else "local_fallback" if "local" in used else "ai"
        if "local" in used:
            self._tr_used[id(self.tr)] = time.time()
        src_info["engine"] = engine
        if engine == "local_fallback":
            src_info["engine_reason"] = getattr(tr, "last_reason", None)
            with self.lock:
                self.counts["ai_fallback"] += 1
        if not translation.strip():                     # nothing new to show: not a success
            self._log_failure(ev, "empty_mt", lang=lang, source=src_text, mt_s=round(t_tr, 3))
            return
        passthrough = False
        if is_echo(src_text, translation):
            s = lang_score(src_text)
            if (tgt == "en" and s < -0.15) or (tgt == "ro" and s > 0.15) or is_name_list(src_text):
                passthrough = True                      # an English quote in RO→EN, or a list of names: show it
                with self.lock:
                    self.counts["passthrough"] += 1
            else:                                       # untranslated source is not a caption
                self._log_failure(ev, "mt_echo", lang=lang, source=src_text, translation=translation,
                                  mt_s=round(t_tr, 3))
                return
        translation, safety = self._safety(ev, f"{last_src} {src_text}" if redo else src_text, translation,
                                           lang, t_tr)
        redo = redo or cut_redo
        if translation is None:                         # dropped by the safety filter (logged)
            return
        if truncated:                                   # shown (better than nothing), but counted
            with self.lock:
                self.counts["mt_truncated"] += 1
        # one running caption, not a sentence per chunk: the chunk's period waits for the
        # next chunk, and is shown only if a sentence-length pause came first
        start = ev.get("t_speech", ev["t_start"])
        prev_end, prev_open = getattr(self, "_cap_end", None), getattr(self, "_cap_open", False)
        continues = prev_open and prev_end is not None and \
            start - prev_end < float(self.lc.get("sentence_pause_s", 1.0)) and \
            not cap_ended                               # the sentence buffer saw the sentence end there
        self._cap_ended = ev.get("sentence") in sentences.ENDS
        screen_text, self._cap_open = tidy_clause(translation, continues, tgt, src_text)
        self._cap_end = ev["t_end"]
        t1 = time.time()
        with self._out_lock:                            # one step: no tick shows the box in between
            if redo:
                self.caps.retract()                     # the previous caption goes; this replaces it
            shown, ok, outcome = self._show(screen_text, close="." if prev_open and not continues and not redo else "",
                                            audio_s=ev.get("audio_s"), spoken_ago=_spoken_ago(ev))
            voice_mark = None
            if hasattr(self, "_speech_mark"):           # Live Voice: this chunk's place among the shown ones
                try:
                    voice_mark = self._speech_mark(ev, tgt, outcome, redo=redo)
                except Exception:                       # noqa: BLE001  the voice never stops a caption
                    self._state_failed(self._speech_mark)
        if shown:                                       # in the caption box: a late verse may replace it
            self._last_out = [translation] if redo else [o for o in out if o]
            self._last_src = f"{last_src} {src_text}" if redo and not cut_redo else src_text
            self._last_end = ev["t_end"]
        t_disp = time.time()
        latency = t_disp - ev["t_end"]
        n_g, ok_g = self.gloss.adherence(src_text, translation, lang)
        rec = {"id": ev["id"], "outcome": outcome, "t_start": ev["t_start"], "t_end": ev["t_end"], "t_display": t_disp,
               "lang": lang, "source": src_text, "ro": ev.get("ro", ""), "multi": ev.get("multi", ""), **src_info,
               "translation": translation, "latency_s": round(latency, 3),
               # average delay per word, approximating words as evenly spread over the commit
               "word_lag_s": round(latency + (ev["t_end"] - ev["t_start"]) / 2, 3), "profile": self.profile,
               "t_speech": ev.get("t_speech", ev["t_start"]),
               "first_word_s": round(t_disp - ev.get("t_speech", ev["t_start"]), 3),  # what a bilingual listener notices
               "stt_s": ev["stt_s"], "vad_wait_s": round(ev["t_emit"] - ev["t_end"] - ev["stt_s"], 3),
               "mt_s": round(t_tr, 3), "pp_s": round(t_disp - t1, 4), "pp_ok": ok,
               "caught_up": ev.get("caught_up", 1), "mt_truncated": truncated, "passthrough": passthrough,
               "pp_error": self.pp.last_error, "audio_s": ev["audio_s"], "glossary": [n_g, ok_g],
               **self._input_fields(ev), **joint_fields(ev), **sentence_fields(ev),
               **safety, **guard, **({"verse": verse[0] if len(verse) == 1 else verse} if verse else {}),
               "time": dt.datetime.now().isoformat(timespec="milliseconds")}
        warn_s = float(self.lc.get("lag_warn_s", 6.0))
        late = bool(warn_s) and latency > warn_s
        self.behind_s = round(latency, 1) if late else None     # clears on the next timely caption
        with self.lock:
            self.history = (self.history + [rec])[-10:]
            self.counts[outcome] += 1
            if late:
                self.counts["late"] += 1
            if ok:
                self.lat.append(latency)
            elif ok is False:                           # None: nothing was sent (hidden or held)
                self.counts["pp_error"] += 1
        self._write_log(rec)
        if hasattr(self, "_engine_note"):
            self._engine_note(rec)
        if hasattr(self, "_monitor_phrase"):            # the Monitor screen's row (monitor.py), before the
            try:                                        # sentence step can group it with others
                self._monitor_phrase(rec)
            except Exception:                           # noqa: BLE001  the monitor never stops a caption
                self._state_failed(self._monitor_phrase)
        if hasattr(self, "_speech_chunk"):              # Live Voice: the sentence step (speech.py)
            try:
                self._speech_chunk(ev, tgt, translation, src_text, outcome, t_disp, verse=verse, mark=voice_mark)
            except Exception:                           # noqa: BLE001  the voice never stops a caption
                self._state_failed(self._speech_chunk)

    def _blocklist(self) -> re.Pattern | None:
        """`safety.blocklist` (words or phrases), the lines of `paths.blocklist` if set, the
        user's words, and the bundled blocklist.txt unless `safety.filter_words` is off."""
        safety = self.cfg.get("safety") or {}
        words = list(safety.get("blocklist") or [])
        files = [paths.user_blocklist(self.root)]
        if safety.get("filter_words", True):
            files.append(paths.resource("blocklist.txt", self.root))
        f = (self.cfg.get("paths") or {}).get("blocklist")
        if f:
            files.append(paths.resource(f, self.root))
        for p in files:
            if pathlib.Path(p).is_file():
                words += [w.strip() for w in pathlib.Path(p).read_text(encoding="utf-8").splitlines()
                          if w.strip() and not w.lstrip().startswith("#")]
        words = tuple(words)
        if self._block[0] != words:
            self._block = (words, blocklist_re(words))
        return self._block[1]

    def _safety(self, ev: dict, src: str, out: str, lang: str, mt_s: float) -> tuple[str | None, dict]:
        """The safety filter, after the echo check: a translator loop inside one output is cut
        to its first occurrence; an over-long output (`mt_overlong`) and the same output a
        third time in a row for different speech (`mt_loop`) are dropped; blocklist words are
        masked. Returns (text to show or None, fields for the log record)."""
        lc, extra = self.lc, {}
        out, trimmed = trim_loops(out, src, int(lc.get("mt_loop_repeats", 4)))
        if trimmed:
            extra["loop_trimmed"] = True
            with self.lock:
                self.counts["loop_trimmed"] += 1
            self._auto("Cut a phrase the translator repeated")
        if overlong(out, src, float(lc.get("mt_max_ratio", 3.0)), int(lc.get("mt_ratio_min_words", 8))):
            self._log_failure(ev, "mt_overlong", lang=lang, source=src, translation=out, mt_s=round(mt_s, 3))
            self._auto("Dropped a caption much longer than what was said")
            return None, extra
        sk, ok_ = _key(src), _key(out)
        prev = list(self._recent_out)
        if (len(ok_) >= 2 and len(prev) == 2 and all(o == ok_ for _, o in prev)
                and not all(s == sk for s, _ in prev)):
            self._log_failure(ev, "mt_loop", lang=lang, source=src, translation=out, mt_s=round(mt_s, 3))
            self._auto("Dropped a caption repeated a third time")
            return None, extra
        self._recent_out.append((sk, ok_))
        out, n = mask(out, self._blocklist())
        if n:
            extra["masked"] = n
            with self.lock:
                self.counts["masked"] += n
            self._auto("Hid a word from the blocklist")
        return out, extra
