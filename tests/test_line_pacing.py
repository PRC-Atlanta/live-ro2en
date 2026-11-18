"""Read-time line pacing in the caption box (captions.RollingCaptions, read_wps): a line scrolls
off only once it has been read, lines are read one after another, words added to a line on
screen are read from when they appeared, the lag guard reads faster while the waiting lines
are far behind the speech, and nothing is ever dropped."""
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from captions import HARD_QUEUE, RollingCaptions  # noqa: E402


def _caps(**kw):
    now = [0.0]
    kw = {"max_chars": 7, "max_lines": 2, "read_wps": 1.0, "min_read_s": 1.0, "max_lag_s": 0,
          "clock": lambda: now[0], **kw}
    return RollingCaptions(**kw), now


def _tick(c, now, until, seen=lambda: None):
    """Run the pace timer as OutputMixin._pace does, up to `until` (`seen` after each step)."""
    for _ in range(10000):
        due = c.next_due()
        if due is None or due > until:
            break
        now[0] = max(now[0], due)
        if not c.advance():
            now[0] += 0.01
        seen()
    now[0] = max(now[0], until)


def test_lines_that_cannot_go_up_yet_wait_in_order():
    c, now = _caps()
    c.add("aaa bbb ccc ddd eee fff ggg")
    assert c.lines == ["aaa bbb", "ccc ddd"] and c.queue == ["eee fff", "ggg"]
    assert c.next_due() == 2.0                       # two words at 1 word/s
    now[0] = 1.9
    assert not c.advance()
    now[0] = 2.0
    assert c.advance() and c.lines == ["ccc ddd", "eee fff"] and c.queue == ["ggg"]


def test_lines_are_read_one_after_another():
    c, now = _caps()
    c.add("aaa bbb ccc ddd eee fff ggg")             # both screen lines came up at 0
    _tick(c, now, 2.0)
    assert c.lines == ["ccc ddd", "eee fff"]
    # "ccc ddd" was up since 0, but the reader only starts it once "aaa bbb" is read (2 s)
    assert c.next_due() == 4.0
    _tick(c, now, 3.9)
    assert c.lines == ["ccc ddd", "eee fff"]
    _tick(c, now, 4.0)
    assert c.lines == ["eee fff", "ggg"] and not c.queue


def test_words_added_on_screen_are_read_from_when_they_appeared():
    c, now = _caps(max_chars=20, max_lines=1)
    c.add("aa bb")                                   # read by 2
    now[0] = 5.0
    c.add("cc dd")                                   # the line grows at 5: two more words
    assert c.lines == ["aa bb cc dd"]
    now[0] = 5.1
    c.add("eeeeeeeeeeeeeeeeee")                      # the next line has to wait for it
    assert c.queue == ["eeeeeeeeeeeeeeeeee"]
    assert c.next_due() == 7.0                       # not 4 s from when the line first came up
    now[0] = 6.9
    assert not c.advance()
    now[0] = 7.0
    assert c.advance() and c.lines == ["eeeeeeeeeeeeeeeeee"]


def test_a_short_line_still_gets_min_read_s():
    c, now = _caps(min_read_s=1.2, read_wps=2.7, max_chars=4, max_lines=1)
    c.add("aa bb")
    assert c.lines == ["aa"] and c.next_due() == 1.2


def test_the_lag_guard_reads_faster_and_goes_back_once_caught_up():
    c, now = _caps(max_lines=1, max_lag_s=10.0, catchup_wps=2.0)
    c.add("aaa bbb ccc ddd", spoken_ago=0.0)
    assert c.next_due() == 2.0                       # in time: 1 word/s
    c, now = _caps(max_lines=1, max_lag_s=10.0, catchup_wps=2.0)
    c.add("aaa bbb ccc ddd", spoken_ago=9.5)         # "ccc ddd" falls 10 s behind at 0.5
    assert c.next_due() == 1.0                       # read at 2 words/s from then on
    now[0] = 0.4
    assert not c.advance()
    now[0] = 1.0
    assert c.advance() and c.lines == ["ccc ddd"]
    c.add("eee fff", spoken_ago=0.0)                 # caught up: back to 1 word/s
    assert c.next_due() == 3.0


def test_the_lag_guard_looks_at_the_last_waiting_line_not_just_the_next():
    c, now = _caps(max_lines=1, max_lag_s=10.0, catchup_wps=2.0)
    c.add("aaa bbb ccc ddd eee fff ggg hhh", spoken_ago=5.0)
    assert c.queue == ["ccc ddd", "eee fff", "ggg hhh"]
    # at 1 word/s "ggg hhh" would come up at 6, 11 s after its speech: read faster now,
    # although the next waiting line is only 5 s behind
    assert c.projected_lag() == 11.0 and c.next_due() == 1.0
    now[0] = 1.0
    assert c.advance() and c.lines == ["ccc ddd"]
    assert c.projected_lag() == 10.0                 # within max_lag_s: back to 1 word/s
    assert c.next_due() == 3.0


def test_the_lag_guard_is_off_when_max_lag_s_is_0():
    c, now = _caps(max_lines=1, max_lag_s=0.0, catchup_wps=2.0)
    c.add("aaa bbb ccc ddd", spoken_ago=50.0)
    assert c.next_due() == 2.0


def test_off_by_default_keeps_min_line_s():
    now = [0.0]
    c = RollingCaptions(max_chars=10, max_lines=2, min_line_s=2.0, clock=lambda: now[0])
    c.add("aaaa bbbb cccc dddd eeee")
    assert c.next_due() == 2.0                       # the old dwell, not read time


def test_retract_takes_the_reading_back_too():
    c, now = _caps(max_chars=20, max_lines=1)
    c.add("aa bb")
    now[0] = 5.0
    c.add("cc dd")
    now[0] = 5.1
    c.add("eeeeeeeeeeeeeeeeee")
    assert c.retract() == "aa bb cc dd" and not c.queue
    now[0] = 5.2
    c.add("ff")
    c.add("gggggggggggggggggg")
    assert c.next_due() == 8.0                       # "cc dd" read 5-7, then "ff"


def test_nothing_is_lost_and_every_line_is_up_long_enough_to_read():
    rnd = random.Random(7)
    vocab = "the lord is my shepherd and i shall not want he makes me lie down in green pastures".split()
    gone, up = [], {}

    class Lines(list):
        def pop(self, i=-1):
            s = super().pop(i)
            if i == 0:
                gone.append((s, now[0] - up[s.split()[0]]))
            return s

    now = [0.0]
    c = RollingCaptions(max_chars=42, max_lines=2, read_wps=2.7, min_read_s=1.2, max_lag_s=20,
                        catchup_wps=3.3, reveal=True, reveal_wps=1.5, clock=lambda: now[0])
    c.lines = Lines()
    sent, lags, queued = [], [], []
    t = 0.0

    def seen():
        for s in c.lines:
            up.setdefault(s.split()[0], now[0])

    for _ in range(120):                             # a fast speaker: ~3.5 words/s of captions
        n = rnd.randint(3, 16)
        clause = " ".join(f"{rnd.choice(vocab)}{len(sent) + k}" for k in range(n))
        sent += clause.split()
        t += n / 3.5
        _tick(c, now, t, seen)
        c.add(clause, audio_s=n / 3.5, spoken_ago=1.5)
        seen()
        lags.append(c.projected_lag())
        queued.append(len(c.queue))
    for _ in range(2000):
        if not (c.queue or c.pending):
            break
        _tick(c, now, now[0] + 0.5, seen)
    shown = " ".join([s for s, _ in gone] + list(c.lines)).split()
    assert shown == sent                             # every word, in order, none twice
    # every line that scrolled off was up (since it first came up) at least min_read_s, and its
    # reading time at the catch-up speed unless the box was past its hard limit (review
    # 2026-11-18: a speaker faster than catchup_wps must not leave it behind without bound)
    assert all(u >= 1.2 - 0.05 for _, u in gone)
    short = [(s, u) for s, u in gone if u < max(1.2, len(s.split()) / 3.3) - 0.05]
    assert len(short) < len(gone) // 10
    # the hard limit holds: without it this speaker leaves the box ~42 s behind with 22 lines
    # waiting; with it ~25 s and at most HARD_QUEUE lines plus what one clause adds (2 lines)
    assert max(lags) <= 30
    assert max(queued) <= HARD_QUEUE + 2
