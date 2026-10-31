"""Rolling caption buffer: word-wraps translated clauses into at most `max_lines` lines of
`max_chars` and returns the text to display. Only whole lines scroll off the top, so the
screen never shows a half-replaced line (no flicker from re-wrapping old text).

Each clause continues the last line (words already on screen never move), so lines fill up
and the box scrolls once per full line, not once per phrase. With `min_line_s` a line stays
on screen at least that long before it scrolls off: lines that would push it off sooner wait
in `queue` and come up from `advance()` (the caller's timer, at `next_due()`). The wait is
bounded: more than `max_queue` waiting lines go up at once.

With `reveal` a clause given its speaking time (`audio_s`) does not land in one jump: its
first words go up at once and the rest wait in `pending`, coming up a few at a time from the
same `advance()`/`next_due()` timer, spread over the speaking time plus REVEAL_SLACK_S (the
next clause lands about one speaking time later, give or take the translator), never slower
than `reveal_wps`. So the screen moves at speaking pace instead of tick-tick-tick-pause. Words
still pending when the next clause comes roll on into it, the pace quickening to take them in,
instead of jumping up at once; only more than CARRY_MAX are shown first, so the text never
falls far behind. A step never starts a new line with fewer than NEW_LINE_WORDS words: it fills the
current line instead, or brings enough words down, so the box never scrolls up for one word;
a clause's last word or two that would go down alone wait up to ORPHAN_WAIT_S for the next
clause to join them. While a full line waits for the one above to have been read, the reveal
waits too: words never pile up out of sight and then appear together.

The operator can take the last caption back (`retract`), replace it (`replace_last`) and
change the size of the box at runtime (`resize`).

`tidy_clause` turns a translated chunk into a continuation of the running caption: chunks
are cut at short pauses, and the recogniser (and then the translator) end every one with a
period, so the caption read as a full stop every few words."""
from __future__ import annotations

import math
import re
import textwrap
import time

REVEAL_HEAD = 3            # words that go up with the clause at once (no delay to the first word)
REVEAL_SLACK_S = 0.4       # reveal over the clause's speaking time plus this...
REVEAL_MAX_S = 8.0         # ...counting at most this much speaking time
CARRY_MAX = 20             # pending words that roll on, faster, into the next clause (more: shown first)
NEW_LINE_WORDS = 3         # a reveal step starts a new line with at least this many words
ORPHAN_WAIT_S = 2.5        # a clause's last words that would start a line alone wait this long for the next
MIN_STEP_S = 0.5           # timer-driven screen changes at most twice a second (ProPresenter)

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
                 min_line_s: float = 0.0, max_queue: int = 3, clock=time.monotonic,
                 reveal: bool = False, reveal_wps: float = 1.5):
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
        self.reveal = bool(reveal)
        self.reveal_wps = float(reveal_wps)
        self.pending: list[str] = []               # words of the last clause not revealed yet
        self._step = (2, MIN_STEP_S)               # (words, seconds) per reveal step
        self._reveal_due = 0.0                     # when the next pending words go up
        self._shown_at = float("-inf")             # when the screen last changed

    def _wrap(self, text: str) -> list[str]:
        return textwrap.wrap(text, self.max_chars, break_long_words=False,
                             break_on_hyphens=False) or ([text] if text else [])

    def _snap(self) -> tuple:
        return list(self.lines), list(self.queue), list(self._since), list(self.pending)

    def _restore(self, snap: tuple):
        self.lines, self.queue, self._since, self.pending = (list(x) for x in snap)

    def _append(self, text: str):
        """Words onto the end of the running text (the last waiting line, else the screen)."""
        stream = self.queue if self.queue else self.lines
        if stream and len(stream[-1]) <= self.max_chars:
            # continue the last line: wrap() is greedy, so its words stay where they are
            first, *rest = self._wrap(f"{stream[-1]} {text}")
            stream[-1] = first
        else:
            rest = self._wrap(text)
        self.queue.extend(rest)

    def _take(self, words: list[str], n: int) -> int:
        """How many of `words` the next step puts up, about `n`: never a new line started
        with fewer than NEW_LINE_WORDS words (fill the current line instead, or bring that
        many down), and never one word left behind."""
        n = min(n, len(words))
        if len(words) - n == 1:
            n = len(words)
        if self._short_new_line(words, n) and (n < len(words) or n > 1):
            fits = next((m for m in range(n, 0, -1) if not self._lines_after(words, m)[1:]), 0)
            n = fits or min(len(words), max(n, NEW_LINE_WORDS))
        return n

    def _last_line(self) -> str:
        stream = self.queue if self.queue else self.lines
        return stream[-1] if stream and len(stream[-1]) <= self.max_chars else ""

    def _lines_after(self, words: list[str], m: int) -> list[str]:
        """The running text's last line with words[:m] appended, wrapped (more than one
        line: they start a new one)."""
        return self._wrap(f"{self._last_line()} {' '.join(words[:m])}".strip())

    def _short_new_line(self, words: list[str], m: int) -> bool:
        """Appending words[:m] starts a new line with fewer than NEW_LINE_WORDS words."""
        wrapped = self._lines_after(words, m)
        return bool(self._last_line()) and len(wrapped) > 1 and len(wrapped[-1].split()) < NEW_LINE_WORDS

    def _orphan_wait(self) -> bool:
        """The next step is the clause's last word or two, alone on a new line: they wait
        for the next clause (until ORPHAN_WAIT_S past their time)."""
        k = self._step[0]
        return (self._take(self.pending, k) == len(self.pending)
                and self._short_new_line(self.pending, len(self.pending)))

    def add(self, clause: str, close: str = "", audio_s: float | None = None) -> str:
        """Append a clause. `close` (e.g. ".") first ends the previous caption, which was
        left open (see tidy_clause). With `reveal` and the clause's speaking time `audio_s`,
        only its first words go up now (see the module docstring)."""
        clause = " ".join(clause.split())
        if not clause:
            return self.text
        before = self.text
        words = clause.split()
        revealing = self.reveal and audio_s
        if not revealing or len(self.pending) > CARRY_MAX:
            self._reveal_all()                     # the last clause's words first: none lag behind
        self._undo = (self._undo + [self._snap()])[-self._undo_depth:]
        if self.pending:                           # a few words of the last clause roll on into this one
            if close and not self.pending[-1].endswith(tuple(".!?…:;,")):
                self.pending[-1] += close
            self.pending += words
            due = self._reveal_due
            self._plan(len(self.pending), float(audio_s))
            self._reveal_due = min(due, self._reveal_due)   # keep the step already under way
            self.advance(force=False)
            return self.text
        stream = self.queue if self.queue else self.lines
        if close and stream and not stream[-1].endswith(tuple(".!?…:;,")):
            stream[-1] += close
        if revealing and len(words) > REVEAL_HEAD + 1:
            head = self._take(words, REVEAL_HEAD)
            if head < len(words):
                self._plan(len(words) - head, float(audio_s))
                words, self.pending = words[:head], words[head:]
        self._append(" ".join(words))
        self.advance(force=False)
        if self.text != before:
            self._shown_at = self.clock()
        return self.text

    def _plan(self, n: int, audio_s: float):
        """Steps of 2-3 words (more only when needed), at least MIN_STEP_S apart, so `n`
        words finish within the speaking time plus REVEAL_SLACK_S and never slower than
        reveal_wps."""
        budget = min(audio_s, REVEAL_MAX_S) + REVEAL_SLACK_S
        rate = max(self.reveal_wps, n / budget if budget > 0 else n / MIN_STEP_S, 0.1)
        k = max(2, math.ceil(rate * MIN_STEP_S))
        self._step = (k, max(MIN_STEP_S, k / rate))
        self._reveal_due = self.clock() + self._step[1]

    def _reveal_all(self):
        if self.pending:
            self._append(" ".join(self.pending))
            self.pending = []

    def flush(self) -> bool:
        """Every pending word up now (Hold released). True when the screen changed."""
        before = self.text
        self._reveal_all()
        self.advance(force=False)
        return self.text != before

    def advance(self, force: bool = False) -> bool:
        """Reveal the pending words that are due, then bring waiting lines up as far as the
        dwell allows. True when the screen changed."""
        now, before = self.clock(), self.text
        k, dt = self._step
        self._rise(now, force)
        if self.pending and not self.queue and now >= self._reveal_due - 1e-6 and not (
                self._orphan_wait() and now < self._reveal_due + ORPHAN_WAIT_S - 1e-6):
            n = self._take(self.pending, k)
            self._append(" ".join(self.pending[:n]))
            del self.pending[:n]
            self._reveal_due = max(self._reveal_due, now) + dt   # after a wait: on from now
            self._rise(now, force)
        changed = self.text != before
        if changed:
            self._shown_at = now
        return changed

    def _rise(self, now: float, force: bool):
        """Waiting lines up as far as the dwell allows."""
        while self.queue:
            if len(self.lines) >= self.max_lines:
                ripe = now - self._since[0] >= self.min_line_s - 1e-6
                if not (ripe or force or len(self.queue) > self.max_queue):
                    break
                self.lines.pop(0)
                self._since.pop(0)
            self.lines.append(self.queue.pop(0))
            self._since.append(now)

    def next_due(self) -> float | None:
        """When advance() can next change the screen (clock time): the next reveal step or
        the next waiting line, or None. With reveal, never sooner than MIN_STEP_S after the
        last change, so a reveal step and a scroll that fall close together go up as one."""
        due = []
        if self.pending and not self.queue:        # a waiting line holds the reveal back
            due.append(self._reveal_due + (ORPHAN_WAIT_S if self._orphan_wait() else 0.0))
        if self.queue and self._since:
            due.append(self._since[0] + self.min_line_s)
        if not due:
            return None
        return max(min(due), self._shown_at + MIN_STEP_S) if self.reveal else min(due)

    def retract(self) -> str | None:
        """Take the last caption back: the screen shows what it showed before it. None when
        there is nothing to take back (empty, or just cleared/resized)."""
        if not self._undo:
            return None
        self._restore(self._undo.pop())
        self._reveal_all()                         # the caption before it, whole
        self.advance(force=True)
        return self.text

    def replace_last(self, clause: str) -> str | None:
        """The operator's text in place of the last caption. None when there is no last
        caption to replace. It goes up at once, in place of all of the last caption (also
        its words not revealed yet)."""
        if not self._undo:
            return None
        self._restore(self._undo.pop())
        self.add(clause)
        self.advance(force=True)
        return self.text

    def resize(self, max_lines: int | None = None, max_chars: int | None = None) -> str:
        """New box size: the current text is wrapped again and only whole lines are kept.
        Undo history is dropped (its line breaks belong to the old size). Words still
        pending are part of the current text: they go up now rather than get lost."""
        if max_chars:
            self.max_chars = int(max_chars)
        if max_lines:
            self.max_lines = int(max_lines)
        self.lines = self._wrap(" ".join(self.lines + self.queue + self.pending))[-self.max_lines:]
        self.queue, self.pending = [], []
        self._since = [self.clock()] * len(self.lines)
        self._undo = []
        return self.text

    def clear(self) -> str:
        self.lines, self.queue, self._since, self.pending = [], [], [], []
        self._undo = []                            # Blank is final: retract cannot bring it back
        return ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
