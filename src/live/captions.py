"""Rolling caption buffer: word-wraps translated clauses into at most `max_lines` lines of
`max_chars` and returns the text to display. Only whole lines scroll off the top, so the
screen never shows a half-replaced line (no flicker from re-wrapping old text).

Each clause continues the last line (words already on screen never move), so lines fill up
and the box scrolls once per full line, not once per phrase. With `min_line_s` a line stays
on screen at least that long before it scrolls off: lines that would push it off sooner wait
in `queue` and come up from `advance()` (the caller's timer, at `next_due()`). The wait is
bounded: more than `max_queue` waiting lines go up at once.

The operator can take the last caption back (`retract`), replace it (`replace_last`) and
change the size of the box at runtime (`resize`).

`tidy_clause` turns a translated chunk into a continuation of the running caption: chunks
are cut at short pauses, and the recogniser (and then the translator) end every one with a
period, so the caption read as a full stop every few words."""
from __future__ import annotations

import re
import textwrap
import time

# Words that start a chunk lowercase when it continues the previous one (never names, God, I)
_LOWER = set("""a an the and but or nor so yet to of in on at for from with by as into onto upon
about after before over under through than then that which who whom whose when where while if
because though although since until unless whether this these those there here our my your his
her their its we they you he she it us them me be is are was were been am has have had do does
did will would can could may might must shall should not no all every each how what why also
even just only now again let one some any many much more most such both either neither very""".split())
_LOWER_RO = set("""și să că de la în pe cu din pentru prin spre fără sau dar iar ci însă ori deci
care ce cine când unde cum cât dacă nu mai ne vă se le îi îl o un una niște al a ale ai este
sunt era au am ați avem fie fost noi voi ei ele el ea tot toți toate acest această acești aceste
acel acea cei cele lui lor astfel apoi atunci chiar doar încă""".split())
# capitalised wherever they stand
_KEEP_CAP = {"i", "god", "lord", "jesus", "christ", "spirit", "holy", "father", "savior", "saviour",
             "lamb", "bible", "gospel", "dumnezeu", "dumnezeul", "dumnezeului", "domnul", "domnului",
             "isus", "hristos", "duhul", "tatăl", "biblia", "evanghelia", "mântuitorul"}


def tidy_clause(text: str, continues: bool, tgt: str = "en", src: str = "") -> tuple[str, bool]:
    """(text to show, open). A chunk-final period is taken off: whether a sentence really
    ended there is decided when the next chunk arrives (`continues`: it followed the open
    chunk without a sentence-length pause). open = the shown text does not end a sentence
    (? ! … and "..." are kept and close it). A continuing chunk starts lowercase when its
    first word is a common function word, or when the source chunk `src` started lowercase
    (the recogniser capitalises names only) and the word is not God, Lord, I...; any other
    chunk starts with a capital."""
    t = " ".join(text.split())
    if not t:
        return t, False
    is_open = bool(re.search(r"(?<!\.)\.$", t))
    if is_open:
        t = t[:-1].rstrip()
    is_open = is_open or not t.endswith(tuple(".!?…\"”'»"))
    if continues:
        m = re.match(r"(\W*)(\w+)", t)
        low = _LOWER_RO if tgt == "ro" else _LOWER
        w = m.group(2).lower() if m else ""
        s = re.match(r"\W*(\w)", src or "")
        common = w in low or (s is not None and s.group(1).islower() and w not in _KEEP_CAP)
        if m and m.group(2)[:1].isupper() and m.group(2)[1:].islower() and common:
            t = t[:m.start(2)] + t[m.start(2)].lower() + t[m.start(2) + 1:]
    else:
        m = re.match(r"(\W*)(\w)", t)
        if m and m.group(2).islower():
            t = t[:m.start(2)] + m.group(2).upper() + t[m.start(2) + 1:]
    return t, is_open


class RollingCaptions:
    def __init__(self, max_chars: int = 42, max_lines: int = 2, undo_depth: int = 20,
                 min_line_s: float = 0.0, max_queue: int = 3, clock=time.monotonic):
        self.max_chars = max_chars
        self.max_lines = max_lines
        self.min_line_s = float(min_line_s)
        self.max_queue = max_queue
        self.clock = clock
        self.lines: list[str] = []                 # on screen
        self.queue: list[str] = []                 # waiting for a line to have been read
        self._since: list[float] = []              # when each screen line came up
        self._undo: list[tuple] = []               # the state before each add(), newest last
        self._undo_depth = undo_depth

    def _wrap(self, text: str) -> list[str]:
        return textwrap.wrap(text, self.max_chars, break_long_words=False,
                             break_on_hyphens=False) or ([text] if text else [])

    def _snap(self) -> tuple:
        return list(self.lines), list(self.queue), list(self._since)

    def add(self, clause: str, close: str = "") -> str:
        """Append a clause. `close` (e.g. ".") first ends the previous caption, which was
        left open (see tidy_clause)."""
        clause = " ".join(clause.split())
        if not clause:
            return self.text
        self._undo = (self._undo + [self._snap()])[-self._undo_depth:]
        stream = self.queue if self.queue else self.lines
        if close and stream and not stream[-1].endswith(tuple(".!?…:;,")):
            stream[-1] += close
        if stream and len(stream[-1]) <= self.max_chars:
            # continue the last line: wrap() is greedy, so its words stay where they are
            first, *rest = self._wrap(f"{stream[-1]} {clause}")
            stream[-1] = first
        else:
            rest = self._wrap(clause)
        self.queue.extend(rest)
        self.advance(force=False)
        return self.text

    def advance(self, force: bool = False) -> bool:
        """Bring waiting lines up as far as the dwell allows. True when the screen changed."""
        now, changed = self.clock(), False
        while self.queue:
            if len(self.lines) >= self.max_lines:
                ripe = now - self._since[0] >= self.min_line_s
                if not (ripe or force or len(self.queue) > self.max_queue):
                    break
                self.lines.pop(0)
                self._since.pop(0)
            self.lines.append(self.queue.pop(0))
            self._since.append(now)
            changed = True
        return changed

    def next_due(self) -> float | None:
        """When advance() can bring the next waiting line up (clock time), or None."""
        if not self.queue or not self._since:
            return None
        return self._since[0] + self.min_line_s

    def retract(self) -> str | None:
        """Take the last caption back: the screen shows what it showed before it. None when
        there is nothing to take back (empty, or just cleared/resized)."""
        if not self._undo:
            return None
        self.lines, self.queue, self._since = self._undo.pop()
        return self.text

    def replace_last(self, clause: str) -> str | None:
        """The operator's text in place of the last caption. None when there is no last
        caption to replace. It goes up at once."""
        if not self._undo:
            return None
        self.lines, self.queue, self._since = self._undo.pop()
        self.add(clause)
        self.advance(force=True)
        return self.text

    def resize(self, max_lines: int | None = None, max_chars: int | None = None) -> str:
        """New box size: the current text is wrapped again and only whole lines are kept.
        Undo history is dropped (its line breaks belong to the old size)."""
        if max_chars:
            self.max_chars = int(max_chars)
        if max_lines:
            self.max_lines = int(max_lines)
        self.lines = self._wrap(" ".join(self.lines + self.queue))[-self.max_lines:]
        self.queue = []
        self._since = [self.clock()] * len(self.lines)
        self._undo = []
        return self.text

    def clear(self) -> str:
        self.lines, self.queue, self._since = [], [], []
        self._undo = []                            # Blank is final: retract cannot bring it back
        return ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
