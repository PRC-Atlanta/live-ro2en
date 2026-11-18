"""Rolling caption buffer: word-wraps translated clauses into at most `max_lines` lines of
`max_chars` and returns the text to display. Only whole lines scroll off the top, so the
screen never shows a half-replaced line (no flicker from re-wrapping old text).

Each clause continues the last line (words already on screen never move), so lines fill up
and the box scrolls once per full line, not once per phrase. With `min_line_s` a line stays
on screen at least that long before it scrolls off: lines that would push it off sooner wait
in `queue` and come up from `advance()` (the caller's timer, at `next_due()`). The wait is
bounded: more than `max_queue` waiting lines go up at once.

With `read_wps` (read-time pacing, replaces `min_line_s`) a line may scroll off only once it
has been read: a reader reads the lines in order at `read_wps` words a second, each line at
least `min_read_s`, so it finishes line i at max(it came up, line i-1 read) + its reading
time. Words added to a line already on screen are read from when they appeared (the line's
reading is not counted from when it first came up). Lines that cannot go up yet wait in
`queue`, in order; none is dropped and `max_queue` does not apply. Lag guard: while a waiting
line (its speech end: `spoken_ago` given to add(), else
from when it was added) or the reveal's words behind them would come up more than `max_lag_s`
behind at `read_wps`, reading is counted at `catchup_wps`, back to `read_wps` once caught up.

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

With `pages` (two-line pages, with read_wps) lines never move up: a full box stays until all of
its lines have been read, then the next lines replace them all at once, as subtitles do. A box
not yet full fills in below. The lag guard and catch-up work as with rolling lines.

With `flow` (and pages) the text runs on from page to page as one text, like a set of subtitles laid
out by hand: every page is full, a sentence may end and the next begin on the same page, and each
page's two line breaks are chosen together where a reader pauses: after . ! ? best, then after , ; :,
then before "and", "but", "that", "who"..., never after "the", "of", "a"... Lines aim at `max_chars`
and may run to `flow_max_chars`. The words after the last good break wait for the next caption; when
none comes (the speaker paused: FLOW_WAIT_S) and the box is ready for a page, they go up as they are.

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
FLOW_WAIT_S = 1.5          # flow: words that do not fill a page wait this long for the next caption

# flow: where a line may end (the reader's pauses), and the words a line should not end on
_BREAK_BEFORE = set("""and but or nor so yet because that which who whom whose when where while if then though
although until unless since as than și dar sau iar ci că care când unde dacă pentru deoarece fiindcă ca""".split())
_WEAK_END = set("""the a an of to in on at for with by from into onto about over under through and or but nor
my your his her our their its this these those that is are was were be been am will would can could shall
should may might must not no very so as than such every each some any i we you he she they it le la de din
pe în cu un o al ale ai a și să se nu ne vă îi îl prin spre sub peste fără despre către lângă unui unei cel cea
cei cele mai foarte""".split()) | _BREAK_BEFORE     # a word a line may start on is a poor word to end one on

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


def comma_parts(text: str, room: int) -> list[str]:
    """A long sentence's translation cut after its commas (and ; :) into parts that each fit the
    caption box (`room` characters: its lines times its line length), so the screen moves on
    clause by clause instead of mid-clause: commas are joined into a part while it fits; a part
    under 3 words joins its neighbour; a clause longer than the box stays whole (it wraps).
    A sentence that fits the box is one part."""
    words = text.split()
    if len(text) <= room or len(words) < 6:
        return [" ".join(words)]
    cuts, cur = [], []
    for w in words:
        cur.append(w)
        if w.endswith((",", ";", ":")):
            cuts.append(cur)
            cur = []
    if cur:
        cuts.append(cur)
    out: list[list[str]] = []
    for c in cuts:
        if out and (len(" ".join(out[-1] + c)) <= room or len(c) < 3 or len(out[-1]) < 3):
            out[-1] = out[-1] + c
        else:
            out.append(c)
    return [" ".join(p) for p in out]


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
                 reveal: bool = False, reveal_wps: float = 1.5, read_wps: float = 0.0,
                 min_read_s: float = 1.2, max_lag_s: float = 20.0, catchup_wps: float = 3.3,
                 pages: bool = False, flow: bool = False, flow_max_chars: int = 50):
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
        self.read_wps = float(read_wps)            # read-time pacing (0 = off: min_line_s)
        self.min_read_s = float(min_read_s)
        self.max_lag_s = float(max_lag_s)
        self.catchup_wps = float(catchup_wps)
        self.pages = bool(pages)                   # two-line pages: the box is replaced whole, never scrolls
        self.flow = bool(flow)                     # with pages: the text runs on, pages laid out at reading pauses
        self.flow_max_chars = int(flow_max_chars)
        self._flow: list[str] = []                 # flow: words not on a page yet
        self._flow_t = float("-inf")               # flow: when words last came
        self._qpages: list[int] = []               # flow: lines per waiting page (queue holds them in order)
        self._reads: list[list[tuple[float, int]]] = []   # per screen line: (when, words added)
        self._qspoken: list[float] = []            # per waiting line: when its last words were spoken
        self._spoken = 0.0                         # when the clause being added was spoken (clock)
        self._prev_read = float("-inf")            # when the reader finished the last line gone

    def _wrap(self, text: str) -> list[str]:
        return textwrap.wrap(text, self.max_chars, break_long_words=False,
                             break_on_hyphens=False) or ([text] if text else [])

    def _wrap_even(self, text: str) -> list[str]:
        """Wrapped onto as few lines as greedy wrapping, with the words spread evenly over them, so
        a text that starts its own line never ends on a line of one or two words."""
        lines = self._wrap(text)
        if len(lines) < 2:
            return lines
        width = self.max_chars
        while width > 10 and len(self._wrap_at(text, width - 1)) == len(lines):
            width -= 1
        return self._wrap_at(text, width)

    def _wrap_at(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False) or [text]

    def _snap(self) -> tuple:
        return (list(self.lines), list(self.queue), list(self._since), list(self.pending),
                [list(r) for r in self._reads], list(self._qspoken), self._prev_read,
                list(self._flow), list(self._qpages))

    def _restore(self, snap: tuple):
        self.lines, self.queue, self._since, self.pending = (list(x) for x in snap[:4])
        self._reads = [list(r) for r in snap[4]]
        self._qspoken, self._prev_read = list(snap[5]), snap[6]
        self._flow, self._qpages = list(snap[7]), list(snap[8])

    def _append(self, text: str, new_line: bool = False):
        """Words onto the end of the running text (the last waiting line, else the screen);
        `new_line`: on a line of their own (a long sentence's next part, after its comma)."""
        stream = self.queue if self.queue else self.lines
        if stream and len(stream[-1]) <= self.max_chars and not new_line:
            # continue the last line: wrap() is greedy, so its words stay where they are
            first, *rest = self._wrap(f"{stream[-1]} {text}")
            if stream is self.queue:
                self._qspoken[-1] = self._spoken
            elif first != stream[-1] and len(self._reads) == len(self.lines):
                # words added on screen: read from now, not from when the line came up
                self._reads[-1].append((self.clock(), len(first.split()) - len(stream[-1].split())))
            stream[-1] = first
        else:
            rest = self._wrap_even(text)
        self.queue.extend(rest)
        self._qspoken.extend([self._spoken] * len(rest))

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

    def add(self, clause: str, close: str = "", audio_s: float | None = None,
            spoken_ago: float | None = None, parts: list[str] | None = None, fresh: bool = False) -> str:
        """Append a clause. `close` (e.g. ".") first ends the previous caption, which was
        left open (see tidy_clause). With `reveal` and the clause's speaking time `audio_s`,
        only its first words go up now (see the module docstring). `spoken_ago`: how long
        ago its speech ended (the lag guard; default: now). `parts` (a long sentence cut at its
        commas, joined = `clause`, no reveal): each part after the first starts a line of its own,
        so the screen moves on clause by clause, as the lines are read. `fresh` (a new whole sentence):
        it starts a line of its own instead of continuing the last one; a text on its own line is
        wrapped evenly (`_wrap_even`), so it never ends on a line of one or two words."""
        if parts and len(parts) > 1:
            audio_s = None
        else:
            parts = None
        clause = " ".join(clause.split())
        if not clause:
            return self.text
        before = self.text
        if self._flowing():
            self._spoken = self.clock() - max(0.0, float(spoken_ago or 0.0))
            self._undo = (self._undo + [self._snap()])[-self._undo_depth:]
            if close and self._flow and not self._flow[-1].endswith(tuple(".!?…:;,")):
                self._flow[-1] += close
            elif close and not self._flow:
                stream = self.queue if self.queue else self.lines
                if stream and not stream[-1].endswith(tuple(".!?…:;,")):
                    stream[-1] += close
            self._flow += clause.split()
            self._flow_t = self.clock()
            self._flow_layout(final=False)
            self.advance(force=False)
            if self.text != before:
                self._shown_at = self.clock()
            return self.text
        words = clause.split()
        revealing = self.reveal and audio_s
        if not revealing or len(self.pending) > CARRY_MAX:
            self._reveal_all()                     # the last clause's words first: none lag behind
        self._spoken = self.clock() - max(0.0, float(spoken_ago or 0.0))
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
        if parts:
            for i, part in enumerate(parts):
                self._append(" ".join(part.split()), new_line=i > 0 or fresh)
        else:
            self._append(" ".join(words), new_line=fresh and not revealing)
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
        if self._flowing() and self._flow:             # flow: the words still waiting for a page too
            self._flow_layout(final=True)
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
        """Waiting lines up as far as the dwell (or the reading) allows."""
        self._sync(now)
        if self._flowing():
            return self._rise_flow(now, force)
        while self.queue:
            if len(self.lines) >= self.max_lines:
                if self.pages and self.read_wps > 0:
                    done = self._page_done(self._wps(now))
                    if not (now >= done - 1e-6 or force):
                        break
                    self._prev_read = min(done, now)
                    self.lines, self._since, self._reads = [], [], []   # the next page replaces it
                    continue
                if self.read_wps > 0:
                    done = self._read_done(self._wps(now))
                    if not (now >= done - 1e-6 or force):
                        break
                    self._prev_read = min(done, now)   # forced: the reader goes on from here
                else:
                    ripe = now - self._since[0] >= self.min_line_s - 1e-6
                    if not (ripe or force or len(self.queue) > self.max_queue):
                        break
                self.lines.pop(0)
                self._since.pop(0)
                if self._reads:
                    self._reads.pop(0)
            line = self.queue.pop(0)
            self._qspoken.pop(0)
            self.lines.append(line)
            self._since.append(now)
            self._reads.append([(now, len(line.split()))])

    def _sync(self, now: float):
        """Per-line bookkeeping matches the lines (a caller may set lines/queue directly)."""
        if len(self._reads) != len(self.lines):
            self._reads = [[(now, len(ln.split()))] for ln in self.lines]
        if len(self._qspoken) != len(self.queue):
            self._qspoken = [now] * len(self.queue)

    def _read_done(self, wps: float) -> float:
        """When a reader reading the lines in order at `wps` has read the top line: not
        before it came up and the line above it was read, then its words at `wps` (words added
        later: from when they appeared), at least min_read_s."""
        (t0, n0), *grown = self._reads[0]
        start = max(t0, self._prev_read)
        done = start + n0 / wps
        for t, n in grown:
            done = max(done, t) + n / wps
        return max(done, start + self.min_read_s)

    # --- flow: full pages, laid out where a reader pauses -----------------------------------------
    def _flowing(self) -> bool:
        return self.flow and self.pages and self.read_wps > 0

    def _box_ready(self, now: float) -> bool:
        """The box can take the next page now: empty, or its page has been read."""
        return not self.lines or now >= self._page_done(self._wps(now)) - 1e-6

    def _rise_flow(self, now: float, force: bool):
        """The next waiting page up once the page on screen has been read (or at once on an empty box);
        then the words left over, if they waited FLOW_WAIT_S and nothing is queued."""
        while True:
            if not self.queue and self._flow and (force or (now - self._flow_t >= FLOW_WAIT_S - 1e-6
                                                             and self._box_ready(now))):
                self._flow_layout(final=True)
            if not self.queue:
                return
            if self.lines:
                done = self._page_done(self._wps(now))
                if not (now >= done - 1e-6 or force):
                    return
                self._prev_read = min(done, now)
            n = self._qpages.pop(0) if self._qpages else min(self.max_lines, len(self.queue))
            page, self.queue = self.queue[:n], self.queue[n:]
            self._qspoken = self._qspoken[n:]
            self.lines, self._since = list(page), [now] * len(page)
            self._reads = [[(now, len(ln.split()))] for ln in page]
            if not force:
                return

    def _flow_layout(self, final: bool):
        """Pages out of the waiting words: every page whose end is followed by more words (so its last
        break is a real one) goes to the queue; with `final` the rest too, as one last page."""
        while self._flow:
            best = self._best_page(self._flow, final)
            if best is None:
                return
            lines, used = best
            self.queue += lines
            self._qspoken += [self._spoken] * len(lines)
            self._qpages.append(len(lines))
            del self._flow[:used]

    def _break_cost(self, words: list[str], k: int) -> float:
        """How good a line end after words[k] is (0 = a sentence end)."""
        w = words[k]
        nxt = words[k + 1] if k + 1 < len(words) else None
        bare = w.lower().strip(".,;:!?…\"'”“()—-")
        if w.endswith((".", "!", "?", "…", ".\"", "?\"", "!\"", ".”")):
            cost = 0.0
        elif w.endswith((",", ";", ":", "—")):
            cost = 2.0
        elif nxt is not None and nxt.lower().strip(".,;:!?…\"'”“(") in _BREAK_BEFORE:
            cost = 4.0
        else:
            cost = 8.0
        if bare in _WEAK_END and not w.endswith((",", ";", ":", ".", "!", "?")):
            cost += 6.0
        return cost

    def _line_cost(self, words: list[str], i: int, j: int, full: bool) -> float | None:
        """words[i:j] as one line: None when too long; past max_chars costs a little per character;
        a line short of max_chars costs too when `full` (a page that more words follow)."""
        n = len(" ".join(words[i:j]))
        if n > self.flow_max_chars - 1 and j - i > 1:      # room for a full stop that comes later (close)
            return None
        cost = max(0, n - self.max_chars) * 1.0
        if full:
            cost += max(0, self.max_chars - n) / self.max_chars * 20.0
        return cost

    def _best_page(self, words: list[str], final: bool) -> tuple[list[str], int] | None:
        """(lines, words used) for the next page, or None when it must wait for more words. A page
        ends at a break with words after it; `final`: the words as they are, on as few lines as fit."""
        best, n, lines = None, len(words), self.max_lines
        fits = len(" ".join(words)) <= self.flow_max_chars * lines
        if fits and not final:
            return None                             # it all fits one page: wait for more, or the pause
        # j: the page's last word + 1; i: line 1's last word + 1 (i == j: one line)
        for j in range(1, n + 1):
            if j == n and not final:
                break
            if final and fits and j < n:
                continue                            # the last words all fit: one page, all of them
            if len(" ".join(words[:j])) > self.flow_max_chars * lines + lines:
                break
            end = self._break_cost(words, j - 1) if j < n else 0.0
            full = j < n
            for i in range(1, j + 1):
                if lines == 1 and i != j:
                    continue
                c1 = self._line_cost(words, 0, i, full or i < j)
                if c1 is None:
                    continue
                if i == j:
                    if full and lines > 1:
                        continue                    # a page that more words follow has all its lines
                    cost = c1 + end
                    cand = [" ".join(words[:j])]
                else:
                    c2 = self._line_cost(words, i, j, full)
                    if c2 is None:
                        continue
                    cost = c1 + c2 + self._break_cost(words, i - 1) + end
                    cand = [" ".join(words[:i]), " ".join(words[i:j])]
                if best is None or cost < best[0] - 1e-9 or (abs(cost - best[0]) < 1e-9 and j > best[2]):
                    best = (cost, cand, j)
        if best is None:
            if final or len(" ".join(words)) > self.flow_max_chars * lines:
                cand = self._wrap_at(" ".join(words), self.flow_max_chars)[:lines]
                return cand, sum(len(x.split()) for x in cand)
            return None
        if not final and len(" ".join(words[best[2]:])) == 0:
            return None
        return best[1], best[2]

    def _page_done(self, wps: float) -> float:
        """When the reader has read every line in the box (pages): line by line, as _read_done."""
        prev = self._prev_read
        for (t0, n0), *grown in self._reads:
            start = max(t0, prev)
            done = start + n0 / wps
            for t, n in grown:
                done = max(done, t) + n / wps
            prev = max(done, start + self.min_read_s)
        return prev

    def _guard_on(self) -> bool:
        return self.max_lag_s > 0 and self.catchup_wps > self.read_wps

    def _wps(self, now: float) -> float:
        """Reading speed now: catchup_wps while a waiting line (or the reveal's words behind
        them) would come up more than max_lag_s after its speech at read_wps."""
        if self._guard_on() and self.projected_lag(now) > self.max_lag_s:
            return self.catchup_wps
        return self.read_wps

    def projected_lag(self, now: float | None = None) -> float:
        """How far behind its speech the furthest-behind waiting line will be when it comes up,
        read at read_wps: waiting line j comes up once the line max_lines above it is read, and
        the lines are read one after another (each its reading time). 0 when nothing waits."""
        now = self.clock() if now is None else now
        if not (self.queue or self.pending or self._flow) or self.read_wps <= 0:
            return 0.0
        seq = self.lines + self.queue
        spoken = [now] * len(self.lines) + list(self._qspoken)
        if self._flow:                        # flow: words not on a page yet come after the waiting pages
            more = self._wrap(" ".join(self._flow))
            seq, spoken = seq + more, spoken + [self._spoken] * len(more)
        if self.pending:                      # the reveal's words come after the waiting lines
            more = self._wrap(f"{seq[-1]} {' '.join(self.pending)}")[1:] if seq else \
                self._wrap(" ".join(self.pending))
            seq, spoken = seq + more, spoken + [self._spoken] * len(more)
        if len(self.lines) < self.max_lines or not self._reads:
            return max(0.0, now - min(spoken[len(self.lines):], default=now))
        if self.pages:
            return self._page_lag(now, seq, spoken)
        read = [max(now, self._read_done(self.read_wps))]
        for ln in seq[1:]:
            read.append(read[-1] + max(self.min_read_s, len(ln.split()) / self.read_wps))
        return max(0.0, max(read[j - self.max_lines] - spoken[j]
                            for j in range(len(self.lines), len(seq))))

    def _page_lag(self, now: float, seq: list[str], spoken: list[float]) -> float:
        """projected_lag with pages: each later page comes up once the page before it is read."""
        n, worst = self.max_lines, 0.0
        up = max(now, self._page_done(self.read_wps))      # the next page comes up then
        read = up
        for i in range(len(self.lines), len(seq), n):
            page = range(i, min(i + n, len(seq)))
            for j in page:
                worst = max(worst, up - spoken[j])
                read += max(self.min_read_s, len(seq[j].split()) / self.read_wps)
            up = read
        return max(0.0, worst)

    def next_due(self) -> float | None:
        """When advance() can next change the screen (clock time): the next reveal step or
        the next waiting line, or None. With reveal, never sooner than MIN_STEP_S after the
        last change, so a reveal step and a scroll that fall close together go up as one."""
        due = []
        if self._flowing() and self._flow and not self.queue:
            now = self.clock()
            self._sync(now)
            ready = now if not self.lines else self._page_done(self._wps(now))
            due.append(max(self._flow_t + FLOW_WAIT_S, ready))
        if self.pending and not self.queue:        # a waiting line holds the reveal back
            due.append(self._reveal_due + (ORPHAN_WAIT_S if self._orphan_wait() else 0.0))
        if self.queue and self.read_wps > 0:
            self._sync(self.clock())
            if len(self.lines) < self.max_lines or not self._reads:
                due.append(self.clock())       # room on screen: up at once
            else:
                due.append((self._page_done if self.pages else self._read_done)(self._wps(self.clock())))
        elif self.queue and self._since:
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
        self.lines = self._wrap(" ".join(self.lines + self.queue + self.pending + self._flow))[-self.max_lines:]
        self.queue, self.pending, self._qspoken = [], [], []
        self._flow, self._qpages = [], []
        self._since = [self.clock()] * len(self.lines)
        self._reads = [[(self.clock(), len(ln.split()))] for ln in self.lines]
        self._undo = []
        return self.text

    def clear(self) -> str:
        self.lines, self.queue, self._since, self.pending = [], [], [], []
        self._reads, self._qspoken = [], []
        self._flow, self._qpages = [], []
        self._undo = []                            # Blank is final: retract cannot bring it back
        return ""

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def waiting_text(self) -> str:
        """The words in the box that are not on screen yet (waiting lines, flow words), as one text."""
        return " ".join(self.queue + self._flow + self.pending)
