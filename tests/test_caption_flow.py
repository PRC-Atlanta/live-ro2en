"""Captions read as one running text: lines fill before scrolling, a line stays up for
min_line_s, a long phrase is revealed a few words at a time at speaking pace, chunk-final
periods wait for a real pause; archaic English is modernised; the
input gain fader reaches every captured frame."""
import queue
import sys
import pathlib
import threading

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from captions import RollingCaptions, tidy_clause      # noqa: E402
from translate import modernize, postedit                # noqa: E402
import engine                                            # noqa: E402


def test_clauses_fill_the_last_line_before_a_new_one():
    c = RollingCaptions(max_chars=20, max_lines=2)
    c.add("one two")
    c.add("three four")
    assert c.lines == ["one two three four"]               # not two half-empty lines
    c.add("five six seven")
    assert c.lines == ["one two three four", "five six seven"]


def test_a_line_stays_up_min_line_s_before_it_scrolls_off():
    now = [0.0]
    c = RollingCaptions(max_chars=10, max_lines=2, min_line_s=2.0, clock=lambda: now[0])
    c.add("aaaa bbbb cccc dddd eeee")                     # three lines at once
    assert c.lines == ["aaaa bbbb", "cccc dddd"] and c.queue == ["eeee"]
    assert c.next_due() == 2.0
    now[0] = 1.0
    assert not c.advance()
    now[0] = 2.0
    assert c.advance() and c.lines == ["cccc dddd", "eeee"] and not c.queue


def test_waiting_is_bounded():
    c = RollingCaptions(max_chars=4, max_lines=1, min_line_s=99, max_queue=2, clock=lambda: 0.0)
    c.add("aa bb cc dd ee")
    assert len(c.queue) <= 2                               # older ones went up anyway


def test_retract_restores_the_waiting_lines_too():
    c = RollingCaptions(max_chars=10, max_lines=1, min_line_s=5, clock=lambda: 0.0)
    c.add("aaaa")
    c.add("bbbbbbb cccc")
    assert c.retract() == "aaaa" and c.queue == []


def test_chunk_periods_wait_for_a_pause():
    t, open_ = tidy_clause("Tonight, may God do.", continues=False, src="în seara aceasta")
    assert (t, open_) == ("Tonight, may God do", True)
    t, open_ = tidy_clause("Continue to do well.", continues=True, src="continue să vă bine.")
    assert t == "continue to do well"
    assert tidy_clause("God is good.", continues=True, src="dumnezeu e bun")[0] == "God is good"
    assert tidy_clause("And the Lord said.", continues=True, src="Și")[0] == "and the Lord said"
    assert tidy_clause("Why?", continues=False) == ("Why?", False)
    assert tidy_clause("we can...", continues=False) == ("We can...", False)
    c = RollingCaptions(max_chars=60, max_lines=2)
    c.add("Dear brothers and sisters")
    c.add("We gather tonight", close=".")                  # a long pause came first
    assert c.text == "Dear brothers and sisters. We gather tonight"


def test_archaic_english_is_modernised():
    assert modernize("Thou art my God, I will praise thee; thy word hath power.") == \
        "You are my God, I will praise you; your word has power."
    assert postedit("Art thou there? Come unto me.", "en") == "Are you there? Come to me."
    assert postedit("Te iubesc, thee", "ro").endswith("thee")    # Romanian output untouched


def test_gain_reaches_the_captured_frames():
    ctl = engine.SourceCtl(gain_db=20.0)
    fr = np.full(512, 0.01, dtype=np.float32)
    assert np.allclose(ctl.amp(fr), 0.1, atol=1e-6)
    ctl.set_gain(0)
    assert ctl.amp(fr) is fr
    ctl.set_gain(100)                                      # clamped to the fader's range
    assert np.isclose(ctl.gain, 10 ** (engine.GAIN_MAX_DB / 20))
    assert np.abs(engine.SourceCtl(gain_db=30).amp(np.full(4, 0.5, np.float32))).max() <= 1.0
    ctl.set_gain("loud")                                   # nonsense is ignored
    assert np.isclose(ctl.gain, 10 ** (engine.GAIN_MAX_DB / 20))


def test_gain_command_from_the_app():
    ctl = engine.SourceCtl()
    q, stop = queue.Queue(), threading.Event()
    engine.stdin_loop(iter(['{"cmd":"gain","db":6}\n', '{"cmd":"stop"}\n']), q, stop, ctl)
    assert np.isclose(ctl.gain, 10 ** (6 / 20))


def test_idle_translators_are_kept_warm_without_touching_a_busy_one():
    import types
    import yaml
    from live.app import Live
    cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
    live = Live(cfg, types.SimpleNamespace(file=None))
    calls = []

    class Tr:
        def __init__(self, name):
            self.name, self.context = name, [("a", "b", "ro")]

        def translate(self, text, *a, **k):
            calls.append(self.name)
            self.context = []                               # as MLXLLMTranslator does without use_context
            return "Amen."
    live.tr, spare = Tr("live"), Tr("spare")
    live._spare_tr = {"spare": spare}
    live.lc["translator_keepalive_s"] = 60
    now = 1000.0
    assert live._keepalive_tick(now) == 2 and sorted(calls) == ["live", "spare"]
    assert live.tr.context == [("a", "b", "ro")]            # the live context survives the ping
    calls.clear()
    live._tr_used = {id(live.tr): now, id(spare): now}
    assert live._keepalive_tick(now + 30) == 0               # used recently: left alone
    live._proc_lock.acquire()                                # translating a phrase right now
    try:
        assert live._keepalive_tick(now + 120) == 1 and calls == ["spare"]
    finally:
        live._proc_lock.release()
    live.lc["translator_keepalive_s"] = 0
    assert live._keepalive_tick(now + 999) == 0             # off


# --- word-paced reveal (idea 21) ------------------------------------------------------------
TEN = "one two three four five six seven eight nine ten"


def _reveal_caps(**kw):
    now = [0.0]
    kw = {"max_chars": 60, "max_lines": 2, "reveal": True, "clock": lambda: now[0], **kw}
    return RollingCaptions(**kw), now


def _run(c, now, until):
    """Tick the caption timer as OutputMixin._pace does: [(time, text)] at each change."""
    seen = []
    while (due := c.next_due()) is not None and due <= until:
        now[0] = due
        if c.advance():
            seen.append((now[0], c.text))
    return seen


def _gaps(steps):
    times = [0.0] + [t for t, _ in steps]
    return [b - a for a, b in zip(times, times[1:])]


def test_reveal_shows_the_first_words_at_once_and_the_rest_in_small_steps():
    c, now = _reveal_caps()
    assert c.add(TEN, audio_s=4.0) == "one two three"           # no delay to the first words
    steps = _run(c, now, 10)
    assert steps[-1][1] == TEN and not c.pending
    assert steps[-1][0] <= 0.8 * 4.0                            # done within the speaking time
    texts = ["one two three"] + [t for _, t in steps]
    assert all(2 <= len(b.split()) - len(a.split()) <= 3 for a, b in zip(texts, texts[1:]))
    assert min(_gaps(steps)) >= 0.5 - 1e-9                      # at most 2 updates a second


def test_a_long_phrase_said_fast_is_revealed_faster_but_still_at_most_twice_a_second():
    c, now = _reveal_caps(max_chars=120, max_lines=3)
    words = " ".join(f"w{i}" for i in range(24))
    c.add(words, audio_s=3.0)
    steps = _run(c, now, 10)
    assert steps[-1][1] == words and steps[-1][0] <= 0.8 * 3.0 + 1e-9
    assert min(_gaps(steps)) >= 0.5 - 1e-9


def test_the_next_phrase_flushes_the_words_still_pending():
    c, now = _reveal_caps(max_chars=60)
    c.add(TEN, audio_s=4.0)
    now[0] = 0.7
    c.advance()
    assert c.pending
    c.add("eleven twelve thirteen fourteen fifteen", audio_s=2.0)
    assert c.text.replace("\n", " ").startswith(TEN + " eleven twelve thirteen")   # nothing lags behind
    assert c.pending == ["fourteen", "fifteen"]


def test_reveal_waits_for_the_line_above_to_have_been_read():
    c, now = _reveal_caps(max_chars=11, max_lines=1, min_line_s=2.0)
    c.add("aaa bbb ccc ddd eee fff", audio_s=4.0)
    assert c.text == "aaa bbb ccc"
    now[0] = 0.7
    c.advance()                                                 # "ddd" wraps: it waits in the queue
    assert c.text == "aaa bbb ccc" and c.queue
    steps = _run(c, now, 10)
    assert steps[0][0] >= 2.0                                   # the first line stayed up min_line_s
    assert c.text == "ddd eee fff" and not c.pending and not c.queue


def test_dwell_and_reveal_close_together_go_up_as_one_change():
    c, now = _reveal_caps(max_chars=10, max_lines=1, min_line_s=0.6)
    c.add("aaaa bbbb cccc dddd eeee ffff gggg", audio_s=5.0)
    steps = _run(c, now, 20)
    assert steps[-1][1] == "gggg" and min(_gaps(steps)) >= 0.5 - 1e-9


def test_retract_and_fix_last_act_on_the_whole_caption_including_pending_words():
    c, now = _reveal_caps(max_chars=80)
    c.add("Amen")
    c.add(TEN, audio_s=4.0)
    assert c.pending
    assert c.retract() == "Amen" and not c.pending
    assert _run(c, now, 10) == []                               # nothing of it comes back later
    c.add(TEN, audio_s=4.0)
    assert c.replace_last("Fixed text") == "Amen Fixed text" and not c.pending
    c.add(TEN, audio_s=4.0)
    c.add("next", audio_s=1.0)                                  # flushed into the caption before it
    assert c.retract().replace("\n", " ") == "Amen Fixed text " + TEN


def test_clear_drops_pending_words_and_resize_keeps_them():
    c, now = _reveal_caps()
    c.add(TEN, audio_s=4.0)
    c.clear()
    assert c.text == "" and not c.pending and c.next_due() is None
    c.add(TEN, audio_s=4.0)
    c.resize(max_chars=20)
    assert c.text.replace("\n", " ").endswith("nine ten") and not c.pending


def test_flush_shows_every_pending_word():
    c, now = _reveal_caps()
    c.add(TEN, audio_s=4.0)
    assert c.flush() and c.text == TEN and c.next_due() is None


def test_reveal_off_or_without_speaking_time_shows_the_phrase_at_once():
    off, _ = _reveal_caps(reveal=False)
    assert off.add(TEN, audio_s=4.0) == TEN and not off.pending and off.next_due() is None
    on, _ = _reveal_caps()
    assert on.add(TEN) == TEN and not on.pending                # operator text, tests: no speaking time
    c, _ = _reveal_caps()
    assert c.add("one two three four", audio_s=2.0) == "one two three four"   # never a lone last word
