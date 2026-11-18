"""Review 2026-11-18, Live Voice fixes: the voice process (src/voice/voice.py, pacing.py) and its
supervision in the orchestrator (src/live/voicelink.py). No model is loaded: the voice side uses a
stand-in synth, the orchestrator side tests/fake_voice.py (through test_voice_link's fixture)."""
import json
import socket
import sys
import time

import numpy as np
import pytest

from test_voice_link import _until, make  # noqa: F401  (the fixture)
import voicelink                                          # noqa: E402  (test_voice_link put src/live on the path)
from voice import pacing, stream                          # noqa: E402
from voice import voice as vv                             # noqa: E402


class Synth:
    def __init__(self, fail=False):
        self.said, self.fail = [], fail

    def say(self, text, lang="ro", speed=1.25):
        self.said.append(text)
        if self.fail:
            raise RuntimeError("synth failed on purpose")
        return np.zeros(int(stream.SR * 0.3 * max(1, len(text.split()))), dtype=np.float32)

    def warm_up(self, langs=("ro", "en")):
        pass


def _voice(tmp_path, **cfg):
    v = vv.Voice({"en": {"enabled": False}, **cfg}, str(tmp_path / "voice.jsonl"), synth=Synth())
    v.state["summarizer"] = True
    return v


def _log(tmp_path):
    p = tmp_path / "voice.jsonl"
    return [json.loads(l) for l in open(p)] if p.exists() else []


WORDS = "unu doi trei patru cinci sase sapte opt noua zece unsprezece doisprezece treisprezece"


# --- 1. status --------------------------------------------------------------------------------------
def test_status_reports_the_failures_and_its_thread_never_dies(tmp_path, monkeypatch):
    v = _voice(tmp_path)
    v.gen_err_t.extend([time.time()] * 3)
    v.gen_ok_t.append(time.time() - 120)                 # outside the minute
    v.clock_err_t.extend([time.time()] * 25)
    v.clock_error = "ValueError: bad frame"
    v.gen_t0 = time.time() - 12
    st = v.status()
    assert st["gen_errors_recent"] == 3 and st["gen_ok_recent"] == 0
    assert st["clock_errors_recent"] == 25 and st["clock_error"] == "ValueError: bad frame"
    assert 11.5 <= st["gen_age_s"] <= 13

    out, calls = [], {"n": 0}
    monkeypatch.setattr(vv, "STATUS_EVERY_S", 0.01)
    monkeypatch.setattr(vv, "emit", out.append)

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("deque mutated during iteration")
        if calls["n"] >= 3:
            v.stop.set()
        return {"type": "status"}
    v.status = flaky
    v.status_loop()                                     # returns only because stop was set: it outlived the error
    assert calls["n"] >= 3 and v.status_errors == 1 and out


# --- 2. bare "exit N" ------------------------------------------------------------------------------
def test_a_taken_port_fails_before_the_model_is_loaded(tmp_path, monkeypatch):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    port = s.getsockname()[1]
    monkeypatch.setattr(stream, "Encoder", lambda kbps: object())
    out = []
    monkeypatch.setattr(vv, "emit", out.append)
    v = vv.Voice({"en": {"enabled": False}, "port": port, "host": "127.0.0.1"}, None, synth=None)
    try:
        t0 = time.time()
        assert v.start() is False
        assert v.synth is None and time.time() - t0 < 5    # the model was never loaded
        assert out and out[0]["where"] == "listen"
    finally:
        s.close()


def test_the_stopped_text_carries_the_last_error(make):
    live = make()
    live._voice_restarts.extend([time.monotonic()] * voicelink.RESTARTS)
    live._voice_error = "listen: port 8767: OSError: [Errno 48] Address already in use"
    assert live._voice_restart("exit 4") is False
    assert "exit 4" in live._voice_dead and "Address already in use" in live._voice_dead


# --- 3. failures reach the operator -----------------------------------------------------------------
def test_generations_that_keep_failing_turn_the_light_amber(make):
    live = make()
    live.voice_start()
    assert _until(lambda: live._voice_ready and live._voice_status)
    orig = live._voice_on
    live._voice_error = "generate: RuntimeError: boom"
    live._voice_on = lambda ev: orig({**ev, "gen_errors_recent": 4, "gen_ok_recent": 1}
                                     if ev.get("type") == "status" else ev)
    assert _until(lambda: live.state()["voice"]["state"] == "behind")
    st = live.state()["voice"]
    assert "keep failing" in st["error"] and "boom" in st["error"]
    # back to normal: the light too
    live._voice_on = orig
    assert _until(lambda: live.state()["voice"]["state"] in ("ready", "speaking"))
    assert live.state()["voice"]["error"] is None


def test_a_play_clock_that_keeps_failing_is_amber_then_restarted(make, monkeypatch):
    live = make()
    live.voice_start()
    first = _until(lambda: live.voice)
    assert _until(lambda: live._voice_ready and live._voice_status)
    orig = live._voice_on
    live._voice_on = lambda ev: orig({**ev, "clock_errors_recent": 40, "clock_error": "OSError: lame"}
                                     if ev.get("type") == "status" and live.voice is first else ev)
    live.running = True
    assert _until(lambda: live.voice is not first and live.voice is not None, 6.0)
    events = [json.loads(l) for l in open(live.log_path)]
    assert any(e["event"] == "voice_failing" and "play clock" in e["why"] for e in events)
    assert any(e["event"] == "voice_restart" and e["why"] == "the play clock keeps failing" for e in events)
    live.running = False


def test_dropped_messages_go_into_the_service_log(make):
    live = make()
    live.running = True
    live._voice_dropped = 5
    live._voice_check_failing({})
    events = [json.loads(l) for l in open(live.log_path)]
    assert any(e["event"] == "voice_dropped" and e["n"] == 5 for e in events)


# --- 4. a stuck generation -----------------------------------------------------------------------
def test_a_generation_stuck_past_the_limit_restarts_the_voice(make, monkeypatch):
    monkeypatch.setattr(voicelink, "GEN_STUCK_S", 2.0)
    live = make()
    live.voice_start()
    first = _until(lambda: live.voice)
    assert _until(lambda: live._voice_ready and live._voice_status)
    orig = live._voice_on
    live._voice_on = lambda ev: orig({**ev, "gen_age_s": 2.5} if ev.get("type") == "status" and live.voice is first
                                     else ev)
    assert _until(lambda: live.voice is not first and live.voice is not None, 6.0)


def test_gen_age_is_reported_while_a_job_runs(tmp_path):
    v = _voice(tmp_path)
    assert v.status()["gen_age_s"] is None


# --- 5. the mixed stream keeps the pause between languages ----------------------------------------
def test_the_pause_counts_from_the_other_languages_item():
    p = vv.Player("en", lambda it, s: None)
    it = vv.Item("en", "sentence", "hello", [], np.ones(stream.FRAME * 4, dtype=np.float32), 0.1, 1.0, 0.5)
    p.queue.append(it)
    now = 1000.0
    other_ended = now - 0.1                       # the Romanian translation's item ended 0.1 s ago
    assert not p.frame(now, last_end=other_ended).any()     # 0.5 s pause: not yet
    assert p.cur is None
    assert p.frame(now + 0.45, last_end=other_ended).any()
    assert p.cur is it


# --- 6. nothing to say --------------------------------------------------------------------------------
def test_a_sentence_with_nothing_speakable_is_skipped_and_logged(tmp_path):
    v = _voice(tmp_path)
    s = {"type": "sentence", "lang": "ro", "sid": 7, "text": "*** ***", "spk_end": time.time(), "words": 2}
    v.take_up(s)
    assert v.synth.said == [] and not v.players["ro"].queue
    assert any(r["event"] == "skipped" and r.get("why") == "nothing to say" for r in _log(tmp_path))


# --- 7. speed_max never below speed ---------------------------------------------------------------
def test_speed_max_is_raised_to_speed():
    c = pacing.settings({"speed": 1.6, "speed_max": 1.4})
    assert c["speed_max"] == 1.6
    assert pacing.next_speed(1.6, 10.0, c)[0] == 1.6
    assert pacing.settings({"speed": 1.2, "speed_max": 1.5})["speed_max"] == 1.5


# --- 8. a summary thrown away is logged as not accepted ------------------------------------------
def test_a_summary_thrown_away_is_logged_as_changed_meanwhile(tmp_path):
    v = _voice(tmp_path)

    def ask(lang, batch):
        v.current_stale = ("replace", "corectat")       # a correction arrives while it is written
        return {"ok": True, "text": "Pe scurt.", "reason": None, "gemma_s": 0.5, "took_s": 0.5}
    v.ask_summary = ask
    s = {"lang": "ro", "sid": 1, "text": WORDS, "spk_end": time.time() - 30, "words": len(WORDS.split())}
    v.current = s
    v.take_up(s)
    rec = [r for r in _log(tmp_path) if r["event"] == "summarize"]
    assert rec and rec[-1]["accepted"] is False and rec[-1]["reason"] == "changed_meanwhile"
    assert v.counts["summaries_late"] == 1 and not v.counts["summaries_refused"]


# --- 10. a catch-up summary keeps the test line ---------------------------------------------------
def test_a_catch_up_summary_does_not_drop_a_test_line(tmp_path):
    v = _voice(tmp_path)
    p = v.players["ro"]
    pcm = np.zeros(stream.SR * 2, dtype=np.float32)
    test = vv.Item("ro", "test", "Aceasta este o proba.", [], pcm, 0.1, 1.25, 0.25)
    old = vv.Item("ro", "sentence", WORDS, [{"sid": 0, "text": WORDS, "spk_end": time.time() - 40}], pcm, 0.1,
                  1.25, 0.25)
    p.queue.extend([test, old])
    v.ask_summary = lambda lang, batch: {"ok": True, "text": "Pe scurt.", "reason": None, "gemma_s": 0.5,
                                         "took_s": 0.5}
    s = {"lang": "ro", "sid": 1, "text": WORDS, "spk_end": time.time() - 30, "words": len(WORDS.split())}
    v.current = s
    v.take_up(s)
    kinds = [q.kind for q in p.queue]
    assert kinds == ["test", "summary"], kinds


# --- 9. a new process is set up before it becomes self.voice -------------------------------------
def test_the_new_process_is_reset_before_it_is_assigned(make):
    live = make()
    seen = []
    base = type(live)

    class Spy(base):
        @property
        def voice(self):
            return self.__dict__.get("_spy_voice")

        @voice.setter
        def voice(self, v):
            if v is not None:
                seen.append((self._voice_ready, self._voice_status_t, self._voice_out is old_q[0]))
            self.__dict__["_spy_voice"] = v

    old_q = [live._voice_out]
    live._voice_ready, live._voice_status_t = True, time.time() - 100
    live.__class__ = Spy
    try:
        assert live.voice_start()
        assert seen == [(False, 0.0, False)]
    finally:
        live._shutting_down = True
        live.voice_stop()
        live.__class__ = base
