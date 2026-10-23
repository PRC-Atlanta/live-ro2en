"""Rolling caption buffer: word-wraps translated clauses into at most `max_lines` lines of
`max_chars` and returns the text to display. Only whole lines scroll off the top, so the
screen never shows a half-replaced line (no flicker from re-wrapping old text)."""
from __future__ import annotations

import textwrap


class RollingCaptions:
    def __init__(self, max_chars: int = 42, max_lines: int = 2):
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.lines: list[str] = []

    def add(self, clause: str) -> str:
        clause = " ".join(clause.split())
        if not clause:
            return self.text
        # continue the last line if the clause fits there, else start new lines
        if self.lines and len(self.lines[-1]) + 1 + len(clause) <= self.max_chars:
            self.lines[-1] = f"{self.lines[-1]} {clause}"
        else:
            self.lines.extend(textwrap.wrap(clause, self.max_chars, break_long_words=False,
                                            break_on_hyphens=False) or [clause])
        self.lines = self.lines[-self.max_lines:]
        return self.text

    def clear(self) -> str:
        self.lines = []
        return ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
