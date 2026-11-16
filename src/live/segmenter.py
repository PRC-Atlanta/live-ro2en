"""Clause segmentation for the live pipeline.

Audio side: `PauseTracker` turns per-frame VAD speech probabilities into "a pause of at
least min_pause_ms just happened" events and forces a commit after max_clause_s of speech.

Text side: `split_clauses` cuts a committed transcript at clause punctuation, never inside
a clause; a trailing fragment without closing punctuation is returned separately so the
caller can hold it until the next pause (unless it was force-committed).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_HARD_END = re.compile(r"[.!?…]$")
_WORD = re.compile(r"[^\W\d_]+")
# Romanian/English words that commonly start a dependent clause; a comma before them is
# a safe split point, other commas are not.
_CONJ = {"și", "dar", "iar", "că", "care", "când", "pentru", "deoarece", "fiindcă", "ca",
         "însă", "ci", "deci", "astfel", "dacă", "unde", "and", "but", "because", "which",
         "when", "so", "that", "who", "where", "if", "for"}


# a phrase that stops on one of these was cut mid-thought ("aduc condoleanțe din."): it waits
# for the next one (idea 20). Compared with diacritics: "sa" (his) ends phrases, "să" does not.
DANGLING = frozenset("din de la în cu pe pentru și că care dar iar sau ca să "
                     "of to in on for and but that which the a an".split())


# a Romanian phrase that stops on a negation lost it at the cut: «în ceața aceea că nu.» |
# «mai recunoaștem» came out "where we do not" | "we again recognize" (2026-11-08 evening, idea 35)
NEGATION_END = frozenset({"nu", "nici", "n"})
SENTENCE_END = (".", "!", "?", "…")


def held_back(words: list[str]) -> int:
    """How many words at the end of a piece cut in mid-speech go with what follows (at most 2): a
    small word that leans on the next one («…de un ușier, de» | «un învățător», «crezând că» |
    «toate sunt», «adevărat, nu» | «predicăm»), or the lone first word of a new sentence («…multă
    grijă. Priviți» | «cât de»). The recogniser, hearing the audio only up to the cut, used not to
    give these at all; with the audio past the cut it does (idea 25), and a piece that ends on
    one is held by the pipeline (`dangling`), so the first word came later (bench 2026-11-15:
    within 8 s 85.8 -> 80.1%). A word that closes a sentence stays («Țineți-vă tari? Nu.»)."""
    n = 0
    while n < 2 and n < len(words):
        w = words[-1 - n].rstrip()
        if w.endswith(SENTENCE_END):
            break
        toks = _WORD.findall(w.lower())
        leans = bool(toks) and (toks[-1] in DANGLING or toks[-1] in NEGATION_END)
        starts = n + 1 < len(words) and words[-2 - n].rstrip().endswith(SENTENCE_END)
        if not (leans or starts):
            break
        n += 1
    return n


@dataclass
class PauseTracker:
    frame_ms: int = 32
    threshold: float = 0.5
    min_pause_ms: int = 350
    max_clause_s: float = 7.0
    _silence_ms: int = 0
    _speech_ms: int = 0
    _in_speech: bool = False
    events: list = field(default_factory=list)

    def push(self, prob: float) -> str | None:
        """Feed one VAD frame. Returns 'pause' at the end of a qualifying pause, 'force'
        when speech ran longer than max_clause_s, otherwise None."""
        if prob >= self.threshold:
            self._in_speech = True
            self._silence_ms = 0
            self._speech_ms += self.frame_ms
            if self._speech_ms >= self.max_clause_s * 1000:
                self._speech_ms = 0
                return "force"
            return None
        if not self._in_speech:
            return None
        self._silence_ms += self.frame_ms
        if self._silence_ms >= self.min_pause_ms:
            self._in_speech = False
            self._speech_ms = 0
            self._silence_ms = 0
            return "pause"
        return None

    def reset(self) -> None:
        self._silence_ms = self._speech_ms = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech


def split_clauses(text: str, min_words: int = 3) -> tuple[list[str], str]:
    """Return (complete_clauses, trailing_fragment). Splits after . ! ? ; : … always, and
    after a comma only when the next word starts a new clause (conjunction/relative).
    Clauses shorter than min_words are merged with the next one."""
    text = " ".join(text.split())
    if not text:
        return [], ""
    parts: list[str] = []
    start = 0
    for m in re.finditer(r"[.!?;:…]+(?=\s|$)|,(?=\s)", text):
        end = m.end()
        if text[m.start()] == ",":
            nxt = text[end:].split(maxsplit=1)
            if not nxt or nxt[0].lower().strip(",.;:") not in _CONJ:
                continue
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        start = end
    tail = text[start:].strip()
    merged: list[str] = []
    buf = ""
    for p in parts:
        buf = f"{buf} {p}".strip()
        if len(buf.split()) >= min_words:
            merged.append(buf)
            buf = ""
    if buf:
        tail = f"{buf} {tail}".strip()
    return merged, tail


def ends_clause(text: str) -> bool:
    return bool(_HARD_END.search(text.strip()))
