"""Stream F (idea 19): the automatic input gain. The controller is driven with synthetic
raw levels and voice-detector verdicts on its own frame clock; the thread hand-off, the
engine command, the settings and the phrase log are checked without models."""
import json
import pathlib
import queue
import sys
import threading
import types

import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import live.engine as engmod                  # noqa: E402
from segmenter import PauseTracker            # noqa: E402

DT = engmod.FRAME / engmod.SR                 # 32 ms


def tone(db: float, n: int = engmod.FRAME) -> np.ndarray:
    """A frame whose rms is db dBFS (a square wave: peak = rms)."""
    a = 10 ** (db / 20)
    return np.where(np.arange(n) % 2, a, -a).astype(np.float32)


def talk(ctl, t0: float, seconds: float, db: float, speech: bool = True) -> float:
    """Frames through the capture side (amp) and the loop's verdict (voiced); returns the end time."""
    t = t0
    for _ in range(int(round(seconds / DT))):
        t += DT
        ctl.amp(tone(db), t)
        ctl.voiced(t, speech)
    return t


def auto_ctl(gain_db=0.0, target=-20.0, meter=None):
    return engmod.SourceCtl(gain_db=gain_db, auto=True, target_db=target, meter=meter)


def test_a_quiet_speaker_is_raised_slowly_toward_the_target():
    ctl = auto_ctl()
    t = talk(ctl, 0.0, 1.9, -40)                       # under 2 s: no verdict on so little speech
    assert ctl.gain_db == 0.0
    t = talk(ctl, t, 5.1, -40)
    assert 3.5 <= ctl.gain_db <= 5.1 + 1e-6           # at most 1 dB per second of speech
    talk(ctl, t, 30.0, -40)
    assert 18.5 <= ctl.gain_db <= 20.0                # -40 + gain lands within the deadband of -20


def test_a_loud_speaker_is_lowered_and_the_range_holds():
    ctl = auto_ctl(gain_db=15)
    talk(ctl, 0.0, 40.0, -10)                          # raw -10 needs -10 dB
    assert -11.0 <= ctl.gain_db <= -9.0
    quiet = auto_ctl(gain_db=25)
    talk(quiet, 0.0, 40.0, -70)                        # would need +50: the fader stops at +30
    assert engmod.GAIN_MAX_DB - 1.0 <= quiet.gain_db <= engmod.GAIN_MAX_DB


def test_the_gain_freezes_while_nobody_speaks():
    ctl = auto_ctl(gain_db=10)
    t = talk(ctl, 0.0, 10.0, -40)
    g = ctl.gain_db
    t = talk(ctl, t, 30.0, -60, speech=False)          # room noise between speakers: not pumped up
    t = talk(ctl, t, 30.0, -26, speech=False)          # louder music the detector rejects: no duck either
    assert ctl.gain_db == g
    # a pause does not count as time: 5 s of silence (the 20 s window still holds the speech
    # before it, so the gain could move) and the next speech frame moves it by one frame's step
    ctl = auto_ctl(gain_db=10)
    t = talk(ctl, 0.0, 10.0, -40)
    g = ctl.gain_db
    talk(ctl, t + 5.0, DT, -40)
    assert ctl.agc.median() is not None and ctl.gain_db != g
    assert abs(ctl.gain_db - g) <= 2 * DT * 1.0 + 1e-9


def test_the_median_ignores_a_few_loud_frames():
    ctl = auto_ctl(gain_db=20)                          # raw -40 + 20 = -20: on target
    t = 0.0
    for i in range(600):                               # 1 frame in 10 is a shout (not clipping)
        t += DT
        ctl.amp(tone(-25 if i % 10 == 0 else -40), t)
        ctl.voiced(t, True)
    assert abs(ctl.gain_db - 20.0) <= 1.0


def test_the_limiter_cuts_at_once_and_holds_the_gain_down():
    ctl = auto_ctl(gain_db=20)
    t = talk(ctl, 0.0, 5.0, -40)
    out = ctl.amp(tone(-6), t + DT)                    # a sudden shout: +20 dB would clip hard
    assert np.abs(out).max() <= 10 ** (-1 / 20) + 1e-6   # this very frame stays under -1 dBFS
    assert ctl.gain_db == pytest.approx(5.0, abs=0.01)
    ctl.voiced(t + DT, True)
    g = ctl.gain_db
    t = talk(ctl, t + DT, 5.0, -40)                    # quiet again: no creeping back up for 10 s
    assert ctl.gain_db == g
    talk(ctl, t, 10.0, -40)
    assert ctl.gain_db > g                            # then it recovers slowly


def test_off_never_moves_the_gain_but_keeps_listening():
    ctl = engmod.SourceCtl(gain_db=7)
    t = talk(ctl, 0.0, 20.0, -40)
    assert ctl.gain_db == 7
    assert np.abs(ctl.amp(tone(-1), t + DT)).max() == 1.0   # manual: no limiter (the operator's fader)
    ctl.set_auto(True)
    talk(ctl, t + DT, DT, -40)                         # the median is already known: it acts at once
    assert ctl.gain_db > 7
    ctl.set_auto(False)
    g = ctl.gain_db
    talk(ctl, t + 1, 10.0, -40)
    assert ctl.gain_db == g                            # off keeps the gain it reached: no jump


def test_the_target_is_clamped_to_the_good_band():
    ctl = auto_ctl(target=-50)
    assert ctl.agc.target_db == -30.0
    ctl.set_auto(True, -5)
    assert ctl.agc.target_db == -10.0
    ctl.set_auto(True, "loud")                          # nonsense is ignored
    assert ctl.agc.target_db == -10.0


def test_the_level_events_carry_the_chosen_gain(monkeypatch):
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    meter = engmod.LevelMeter(10)
    ctl = auto_ctl(meter=meter)
    t = talk(ctl, 0.0, 10.0, -40)
    meter.feed(tone(-30), now=100.0)
    meter.feed(tone(-30), now=100.2)
    assert out[-1]["auto"] is True and out[-1]["gain_db"] == round(ctl.gain_db, 1) > 0


def test_the_capture_and_loop_threads_hand_off_safely():
    ctl = auto_ctl(gain_db=10)
    stamps = queue.Queue()
    n = 2000

    def capture():
        for i in range(n):
            ts = (i + 1) * DT
            ctl.amp(tone(-40), ts)
            stamps.put(ts)
        stamps.put(None)

    th = threading.Thread(target=capture)
    th.start()
    while (ts := stamps.get()) is not None:            # the loop runs behind the capture, as live
        ctl.voiced(ts, True)
    th.join()
    assert 10.0 < ctl.gain_db <= 20.0 and len(ctl.agc._raw) == 0


def test_engine_run_feeds_the_voice_verdicts_to_the_controller(monkeypatch):
    monkeypatch.setattr(engmod, "emit", lambda ev: None)
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.pt, e.pre_roll, e.partial_every = "ro-en", PauseTracker(frame_ms=32), 8, 0
    e.min_commit = e.max_commit = 0
    e.no_audio_s = e.silent_s = 0
    e.realtime, e.backlog_s, e.catchup_max, e.multi = False, 0, 0, object()
    e.n, e._id_lock = 0, threading.Lock()
    e.vad = lambda fr: 0.9                               # everything is speech
    e.transcribe = lambda *a, **k: None
    e.ctl = ctl = auto_ctl()
    q = queue.Queue()
    for i in range(300):                               # ~10 s at raw -40, through the capture side
        ts = (i + 1) * DT
        q.put((ts, ctl.amp(tone(-40), ts)))
    q.put(None)
    e.run(q)
    assert ctl.gain_db > 5


def test_agc_command_and_cfg_keys(monkeypatch):
    ctl = engmod.SourceCtl()
    q, stop = queue.Queue(), threading.Event()
    engmod.stdin_loop(iter(['{"cmd":"agc","on":true,"target_db":-24}\n', '{"cmd":"stop"}\n']), q, stop, ctl)
    assert ctl.agc.on is True and ctl.agc.target_db == -24.0
    e = engmod.Engine.__new__(engmod.Engine)
    assert e.apply_cfg({"input_gain_auto": True, "input_gain_target_db": -18, "input_gain_db": 3}) == []


# --- the app side --------------------------------------------------------------------------
from test_settings import ME, client, home, make_live   # noqa: E402,F401


def test_auto_setting_reaches_the_engine_and_the_knob_follows_it(home):
    live = make_live(home, running=True)
    sent = []
    live._send = lambda cmd: sent.append(cmd) or True
    client(live).put("/api/settings", json={"values": {"live.input_gain_auto": False}}, headers=ME)   # on by default
    sent.clear()
    r = client(live).put("/api/settings", json={"values": {"live.input_gain_auto": True}}, headers=ME).json()
    assert r["restart_required"] is False
    assert sent == [{"cmd": "agc", "on": True, "target_db": -20.0}]
    assert live.state()["audio"]["gain_auto"] is True
    live._on_level({"rms_db": -25.0, "peak_db": -18.0, "speech": True, "gain_db": 14.5, "auto": True})
    assert live.state()["audio"]["gain_db"] == 14.5            # the knob shows the engine's choice
    cfg = live._engine_cfg()
    assert cfg["input_gain_auto"] is True and cfg["input_gain_target_db"] == -20
    assert cfg["input_gain_db"] == 14.5                        # a restarted engine carries on from there
    # a hand turn: the UI saves auto off with the value; agc off goes before the gain
    sent.clear()
    client(live).put("/api/settings", json={"values": {"live.input_gain_db": 9, "live.input_gain_auto": False}},
                     headers=ME)
    assert sent == [{"cmd": "agc", "on": False, "target_db": -20.0}, {"cmd": "gain", "db": 9.0}]
    assert live.state()["audio"]["gain_db"] == 9.0 and live.state()["audio"]["gain_auto"] is False
    sent.clear()
    client(live).put("/api/settings", json={"values": {"live.input_gain_target_db": -24}}, headers=ME)
    assert sent == [{"cmd": "agc", "on": False, "target_db": -24.0}]
    r = client(live).put("/api/settings", json={"values": {"live.input_gain_target_db": -5}}, headers=ME)
    assert r.status_code == 400                                # outside the meter's good band


def test_each_phrase_logs_whether_auto_was_on(home):
    live = make_live(home)
    live.lang.set_mode("ro-en")
    live.lc["input_gain_auto"] = True
    live._on_level({"rms_db": -25.0, "peak_db": -18.0, "speech": True, "gain_db": 17.0, "auto": True})
    f = live._input_fields({"t_start": 0, "t_end": 1})
    assert f["gain_auto"] is True and f["gain_db"] == 17.0
    live.lc["input_gain_auto"] = False
    assert live._input_fields({})["gain_auto"] is False
