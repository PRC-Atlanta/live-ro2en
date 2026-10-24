"""Captions read as one running text: lines fill before scrolling, a line stays up for
min_line_s, chunk-final periods wait for a real pause; archaic English is modernised; the
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
