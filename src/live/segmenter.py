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
# Romanian/English words that commonly start a dependent clause; a comma before them is
# a safe split point, other commas are not.
_CONJ = {"și", "dar", "iar", "că", "care", "când", "pentru", "deoarece", "fiindcă", "ca",
         "însă", "ci", "deci", "astfel", "dacă", "unde", "and", "but", "because", "which",
         "when", "so", "that", "who", "where", "if", "for"}


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
