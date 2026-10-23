"""Rolling caption buffer: word-wraps translated clauses into at most `max_lines` lines of
`max_chars` and returns the text to display. Only whole lines scroll off the top, so the
screen never shows a half-replaced line (no flicker from re-wrapping old text).

The operator can take the last caption back (`retract`), replace it (`replace_last`) and
change the size of the box at runtime (`resize`)."""
from __future__ import annotations

import textwrap


class RollingCaptions:
    def __init__(self, max_chars: int = 42, max_lines: int = 2, undo_depth: int = 20):
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.lines: list[str] = []
        self._undo: list[list[str]] = []           # the lines before each add(), newest last
        self._undo_depth = undo_depth

    def _wrap(self, text: str) -> list[str]:
        return textwrap.wrap(text, self.max_chars, break_long_words=False,
                             break_on_hyphens=False) or ([text] if text else [])

    def add(self, clause: str) -> str:
        clause = " ".join(clause.split())
        if not clause:
            return self.text
        self._undo = (self._undo + [list(self.lines)])[-self._undo_depth:]
        # continue the last line if the clause fits there, else start new lines
        if self.lines and len(self.lines[-1]) + 1 + len(clause) <= self.max_chars:
            self.lines[-1] = f"{self.lines[-1]} {clause}"
        else:
            self.lines.extend(self._wrap(clause))
        self.lines = self.lines[-self.max_lines:]
        return self.text

    def retract(self) -> str | None:
        """Take the last caption back: the screen shows what it showed before it. None when
        there is nothing to take back (empty, or just cleared/resized)."""
        if not self._undo:
            return None
        self.lines = self._undo.pop()
        return self.text

    def replace_last(self, clause: str) -> str | None:
        """The operator's text in place of the last caption. None when there is no last
        caption to replace."""
        if not self._undo:
            return None
        self.lines = self._undo.pop()
        return self.add(clause)

    def resize(self, max_lines: int | None = None, max_chars: int | None = None) -> str:
        """New box size: the current text is wrapped again and only whole lines are kept.
        Undo history is dropped (its line breaks belong to the old size)."""
        if max_chars:
            self.max_chars = int(max_chars)
        if max_lines:
            self.max_lines = int(max_lines)
        self.lines = self._wrap(" ".join(self.lines))[-self.max_lines:]
        self._undo = []
        return self.text

    def clear(self) -> str:
        self.lines = []
        self._undo = []                            # Blank is final: retract cannot bring it back
        return ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
