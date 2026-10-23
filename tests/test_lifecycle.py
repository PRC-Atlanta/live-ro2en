"""S1: lifecycle, readiness and the audio level. The engine side runs without models
(bare Engine objects in-process, or tests/fake_engine.py as the real child process: the real
command loop, hold/listen, level meter, file source and exit watch, with fake models)."""
import json
import os
import pathlib
import queue
import signal
import subprocess
import sys
import threading
import time
import types

import numpy as np
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live                     # noqa: E402
import live.engine as engmod                  # noqa: E402
import lifecycle                              # noqa: E402
from segmenter import PauseTracker            # noqa: E402
from test_contract import C, check, check_state   # noqa: E402

FAKE = ROOT / "tests" / "fake_engine.py"


def until(cond, timeout=5.0, every=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(every)
    return bool(cond())


# --- engine, in-process --------------------------------------------------------------------
def _bare(mode="ro-en"):
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.pt, e.pre_roll, e.partial_every = mode, PauseTracker(frame_ms=32), 8, 0
    e.min_commit = e.max_commit = 0
    e.no_audio_s = e.silent_s = 0
    e.realtime, e.backlog_s, e.catchup_max, e.english_ok = False, 0, 0, True
    e.n, e._id_lock = 0, threading.Lock()
    e.vad = lambda fr: float(fr[0])
    e.streamer, e.wcfg, e._wstream, e.whistle = None, {}, None, None
    e.calls = []
    e.transcribe = lambda audio, t0, t1, t_speech=None, final=True: e.calls.append(len(audio))
    return e


def _frames(q, n, speech):
    for i in range(n):
        q.put((time.time(), np.full(engmod.FRAME, 1.0 if speech else 0.0, dtype=np.float32)))


def _run(e, q, monkeypatch, **kw):
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    e.run(q, **kw)
    return out


def test_level_is_about_minus_infinity_for_zero_frames(monkeypatch):
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    m = engmod.LevelMeter(10)
    for i in range(10):                                   # 320 ms of exact zeros
        m.feed(np.zeros(engmod.FRAME, np.float32), now=100 + i * 0.032)
    assert out and all(ev == {"type": "level", "rms_db": -120.0, "peak_db": -120.0, "speech": False} for ev in out)
    out.clear()
    t = np.arange(engmod.FRAME * 4) / engmod.SR
    sine = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    m.voice(True)
    for i in range(4):
        m.feed(sine[i * engmod.FRAME:(i + 1) * engmod.FRAME], now=200 + i * 0.05)
    ev = out[-1]
    assert abs(ev["peak_db"] - (-6.0)) < 0.2 and abs(ev["rms_db"] - (-9.0)) < 0.3 and ev["speech"] is True
    json.dumps(ev, allow_nan=False)                       # valid JSON for the browser


def test_level_rate_is_bounded():
    assert engmod.LevelMeter(100).every == pytest.approx(1 / 20) and engmod.LevelMeter(1).every == pytest.approx(1 / 5)


def test_hold_transcribes_nothing_until_listen_then_commits_on_listen_off(monkeypatch):
    e = _bare()
    q = queue.Queue()
    _frames(q, 30, speech=True)                           # heard while holding: meter only
    q.put({"listen": True})
    _frames(q, 20, speech=True)                           # live, mid-sentence
    q.put({"listen": False})                              # Stop: commit it, then the ack
    _frames(q, 30, speech=True)                           # holding again
    q.put(None)
    out = _run(e, q, monkeypatch, hold=True)
    # the 8-frame pre-roll from the hold is kept, so the first word after Start is not cut
    assert e.calls == [28 * engmod.FRAME]
    assert [x for x in out if x["type"] == "listen"] == [{"type": "listen", "on": True}, {"type": "listen", "on": False}]
    assert out[-1] == {"type": "eof"}


def test_listen_is_always_answered(monkeypatch):
    e = _bare()
    q = queue.Queue()
    q.put({"listen": False}); q.put({"listen": True}); q.put({"listen": True}); q.put(None)
    out = _run(e, q, monkeypatch)                         # not holding: already live
    assert [x["on"] for x in out if x["type"] == "listen"] == [False, True, True]


def test_cfg_and_keywords_apply_without_a_restart(monkeypatch):
    e = _bare()
    got = []
    e.whistle = types.SimpleNamespace(set_keywords=got.append)
    e.en_rec = "parakeet"
    q = queue.Queue()
    q.put({"cfg": {"min_pause_ms": 700, "vad_threshold": 0.6, "min_commit_s": 3.2, "max_commit_s": 6.4,
                   "digital_silence_s": 4, "en_recognizer": "whistle", "whistle": {"x": 1}}})
    q.put({"keywords": ["Ionuț Pop", "Efeseni"]})
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert (e.pt.min_pause_ms, e.pt.threshold, e.min_commit, e.max_commit, e.silent_s) == \
        (700, 0.6, int(3.2 * 16000 / 512), int(6.4 * 16000 / 512), 4.0)
    assert got == [["Ionuț Pop", "Efeseni"]]
    assert {"type": "warn", "msg": "applied at the next start: en_recognizer"} in out


def test_mic_denied_is_not_reported_as_digital_silence(monkeypatch):
    e = _bare()
    e.silent_s = 0.05
    q = queue.Queue()
    q.put({"fault": "mic_denied", "msg": "the Mac does not allow this app to use the microphone"})
    for _ in range(10):                                   # macOS hands a denied app zeros
        q.put((time.time(), np.zeros(engmod.FRAME, np.float32))); time.sleep(0.01)
    _frames(q, 2, speech=True)                            # permission granted: real sound
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert [(x["ok"], x["fault"]) for x in out if x["type"] == "audio"] == [(False, "mic_denied"), (True, None)]


def test_permissions_answer_is_one_of_the_contract_values():
    import permissions
    check(permissions.status(), C["types"]["permissions"])


def test_stdin_commands_and_exit_on_eof(monkeypatch):
    exits = []
    monkeypatch.setattr(engmod.os, "_exit", exits.append)
    q, stop = queue.Queue(), threading.Event()
    ctl = engmod.SourceCtl("Built-in Mic", hold=True)
    lines = [json.dumps(c) + "\n" for c in (
        {"cmd": "listen", "on": True}, {"cmd": "cfg", "cfg": {"min_pause_ms": 500}},
        {"cmd": "keywords", "words": ["Ionuț", 3]}, {"cmd": "device", "device": "USB Interface"},
        {"cmd": "mode", "mode": "en-ro"})] + ["not json\n"]
    engmod.stdin_loop(iter(lines), q, stop, ctl, grace_s=0.05)     # then EOF: the app is gone
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert items == [{"listen": True}, {"cfg": {"min_pause_ms": 500}}, {"keywords": ["Ionuț"]},
                     {"mode": "en-ro"}, None]
    assert ctl.go.is_set() and ctl.changed.is_set() and ctl.device == "USB Interface" and stop.is_set()
    assert until(lambda: exits == [0], 1)                  # exits even if a recognition call hangs


def test_parent_death_stops_the_engine(monkeypatch):
    exits = []
    monkeypatch.setattr(engmod.os, "_exit", exits.append)
    monkeypatch.setattr(engmod.os, "getppid", lambda: 1)  # re-parented: the app is gone
    q, stop = queue.Queue(), threading.Event()
    engmod.parent_watch(4242, q, stop, every_s=0.01)
    assert stop.is_set() and q.get_nowait() is None and until(lambda: exits == [0], 2)


def test_mic_follows_the_chosen_device_and_names_it(monkeypatch):
    devices = [{"name": "Built-in Mic", "max_input_channels": 1}, {"name": "USB Interface", "max_input_channels": 2}]
    opened = []

    class Stream:
        def __init__(self, callback, device=None, **k):
            opened.append(device); self.cb = callback
        def __enter__(self):
            threading.Thread(target=lambda: [self.cb(np.full((engmod.FRAME, 1), 0.2, np.float32), 0, None, None)
                                             for _ in range(3)], daemon=True).start()
            return self
        def __exit__(self, *a):
            return False
    sd = types.SimpleNamespace(InputStream=Stream, default=types.SimpleNamespace(device=(0, 1)),
                               query_devices=lambda d=None: devices if d is None else
                               (devices[d] if isinstance(d, int) else next(x for x in devices if x["name"] == d)))
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(engmod, "_mic_denied", lambda: False)
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    q, stop = queue.Queue(), threading.Event()
    ctl = engmod.SourceCtl(None, engmod.LevelMeter(20))
    th = threading.Thread(target=engmod.mic_source, args=(None, q, stop), kwargs={"ctl": ctl}, daemon=True)
    th.start()
    assert until(lambda: {"type": "input", "device": "Built-in Mic"} in out, 2)   # the system default, by name
    ctl.switch("USB Interface")
    assert until(lambda: {"type": "input", "device": "USB Interface"} in out, 2)
    stop.set(); th.join(2)
    assert opened[:2] == [None, "USB Interface"]


def test_denied_microphone_is_reported_on_open(monkeypatch):
    class Stream:
        def __init__(self, callback, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Stream))
    monkeypatch.setattr(engmod, "_mic_denied", lambda: True)
    monkeypatch.setattr(engmod, "emit", lambda ev: None)
    q, stop = queue.Queue(), threading.Event()
    th = threading.Thread(target=engmod.mic_source, args=(None, q, stop), kwargs={"stall_s": 60}, daemon=True)
    th.start()
    assert until(lambda: not q.empty(), 2)
    stop.set(); th.join(2)
    assert q.get_nowait()["fault"] == "mic_denied"


def _wav(path, sr, seconds=1.0):
    import soundfile as sf
    t = np.arange(int(sr * seconds)) / sr
    sf.write(str(path), (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32), sr)
    return path


def test_44k_rehearsal_file_plays_without_ffmpeg(tmp_path, monkeypatch):
    wav = _wav(tmp_path / "rehearsal_44k.wav", 44100, 2.0)
    monkeypatch.setenv("PATH", str(tmp_path))             # no ffmpeg anywhere
    q, stop = queue.Queue(), threading.Event()
    engmod.file_source(str(wav), False, q, stop)
    items = [q.get_nowait() for _ in range(q.qsize())]
    assert items[-1] is None
    n = sum(len(fr) for _, fr in items[:-1])
    assert abs(n - 32000) <= engmod.FRAME and all(fr.dtype == np.float32 for _, fr in items[:-1])


def test_44k_file_plays_in_the_engine_venv_without_ffmpeg(tmp_path):
    """The same with the engine's own interpreter (where soxr is installed), if it is here."""
    py = os.environ.get("LIVE_ENGINE_PYTHON") or str(ROOT / ".venv-nemo" / "bin" / "python")
    if not pathlib.Path(py).exists():
        pytest.skip("no engine interpreter in this checkout (set LIVE_ENGINE_PYTHON)")
    wav = _wav(tmp_path / "r44.wav", 44100, 2.0)
    code = ("import queue,sys,threading; sys.path.insert(0, sys.argv[1]); import engine, soxr; "
            "q=queue.Queue(); engine.file_source(sys.argv[2], False, q, threading.Event()); "
            "print(sum(len(x[1]) for x in list(q.queue) if x is not None))")
    r = subprocess.run([py, "-c", code, str(ROOT / "src" / "live"), str(wav)], capture_output=True, text=True,
                       env={**os.environ, "PATH": str(tmp_path)}, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    assert abs(int(r.stdout.split()[-1]) - 32000) <= engmod.FRAME


def test_file_in_hold_waits_for_start():
    q, stop = queue.Queue(), threading.Event()
    ctl = engmod.SourceCtl(None, hold=True)
    th = threading.Thread(target=engmod.file_source, args=(str(_wav(pathlib.Path(os.environ.get("TMPDIR", "/tmp"))
                                                                     / f"hold_{os.getpid()}.wav", 16000, 0.5)),
                                                           False, q, stop), kwargs={"ctl": ctl}, daemon=True)
    th.start(); time.sleep(0.3)
    assert q.empty()                                       # nothing plays before Start
    ctl.go.set(); th.join(5)
    assert q.qsize() > 10


# --- orchestrator with the fake engine child ----------------------------------------------
@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LIVE_ENGINE_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_MIC", "1")
    monkeypatch.setattr(lifecycle, "ENGINE_SCRIPT", FAKE)
    return tmp_path


def _live(tmp_path, file=None):
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    live = Live(cfg, types.SimpleNamespace(file=file, settings=str(tmp_path / "settings.yaml")))
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "Brothers and sisters, today we read from Romans.")
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    live._models_missing = lambda: []
    live.preload_translators = lambda: None       # no real spare translator in tests
    return live


def test_preload_then_start_is_live_in_under_a_second(sandbox):
    live = _live(sandbox)
    try:
        live.preload()
        assert live.state()["phase"] == "loading"
        assert until(lambda: live.state()["phase"] == "ready", 10)
        st = live.state()
        check_state(st)
        assert st["audio"]["device"] == "Fake Default Mic"
        assert until(lambda: live.state()["audio"]["rms_db"] is not None, 3)   # the meter works in hold
        assert live.counts == {} and live.log_path is None                    # nothing transcribed yet
        t0 = time.time()
        live.start()
        assert until(lambda: live.state()["phase"] in ("listening", "captioning"), 1.0)
        assert time.time() - t0 < 1.0
        assert until(lambda: live.state()["phase"] == "captioning", 5)        # captions flow
        live.stop()
        st = live.state()
        assert st["phase"] == "ready" and live._engine_alive()               # warm for the next service
        n = live.counts["ok"]
        log = live.log_path
        t0 = time.time()
        live.start()                                                          # second service: a new log
        assert time.time() - t0 < 1.0 and live.log_path != log
        assert until(lambda: live.counts.get("ok", 0) >= 1, 5) and n >= 1
    finally:
        live.shutdown()
    assert not live._engine_alive()


def test_killed_engine_restarts_and_captions_resume(sandbox):
    live = _live(sandbox)
    try:
        live.preload(wait=True)
        live.start()
        assert until(lambda: live.counts.get("ok", 0) >= 1, 5)
        first = live.engine
        os.kill(first.pid, signal.SIGKILL)                                    # crash mid-service
        assert until(lambda: live.engine is not first and live._listening, 5)
        n = live.counts["ok"]
        assert until(lambda: live.counts["ok"] > n, 5)                        # captions resume
        st = live.state()
        assert st["running"] and st["phase"] == "captioning" and st["problem"] is None
        assert any("restarted" in w for w in st["warnings"])
        events = [json.loads(l).get("event") for l in open(live.log_path)]
        assert "engine_exit" in events and "engine_restart" in events
        for _ in range(2):                                                    # the budget: 2 per minute
            eng = live.engine
            os.kill(eng.pid, signal.SIGKILL)
            assert until(lambda: live.engine is not eng or not live.running, 5)
        assert until(lambda: not live.running, 5)
        st = live.state()
        assert st["phase"] == "error" and st["problem"]["code"] == "engine_stopped"
        assert "ENGINE STOPPED" in st["status"]
    finally:
        live.shutdown()


def test_chosen_input_is_followed_while_holding(sandbox):
    live = _live(sandbox)
    try:
        live.preload(wait=True)
        assert until(lambda: live.state()["audio"]["device"] == "Fake Default Mic", 3)
        code, _ = live.put_settings({"values": {"live.input_device": "USB Interface"}})
        assert code == 200
        assert until(lambda: live.state()["audio"]["device"] == "USB Interface", 3)
        assert until(lambda: live.state()["audio"]["rms_db"] is not None, 3)
        assert live._engine_alive() and live.state()["phase"] == "ready"      # no restart
    finally:
        live.shutdown()


def test_file_in_hold_plays_from_start(sandbox):
    wav = _wav(sandbox / "speech.wav", 16000, 3.0)
    live = _live(sandbox, file=str(wav))
    try:
        live.preload(wait=True)
        assert live.state()["phase"] == "ready" and live._engine_src == str(wav)
        live.start()
        assert until(live.eof.is_set, 10)
        assert live.counts.get("ok", 0) >= 1
    finally:
        live.shutdown()


def test_orchestrator_killed_means_engine_exits_within_2_s(sandbox, tmp_path):
    code = f"""
import sys, time, types, yaml
sys.path.insert(0, {str(ROOT / 'src')!r}); sys.path.insert(0, {str(ROOT / 'src' / 'live')!r})
import lifecycle, pathlib
lifecycle.ENGINE_SCRIPT = pathlib.Path({str(FAKE)!r})
from live.app import Live
live = Live(yaml.safe_load(open({str(ROOT / 'config.yaml')!r})), types.SimpleNamespace(file=None, settings={str(tmp_path / 's.yaml')!r}))
live._models_missing = lambda: []
live.preload_translators = lambda: None
live.tr = object()
live.preload(wait=True)
print(live.engine.pid, flush=True)
time.sleep(60)
"""
    app = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    try:
        epid = int(app.stdout.readline())
        assert lifecycle._alive(epid)
        app.kill()                                                             # kill -9
        app.wait()
        t0 = time.time()
        assert until(lambda: not lifecycle._alive(epid) or _zombie(epid), 2.0), "engine outlived the app"
        assert time.time() - t0 < 2.0
    finally:
        app.kill()


def _zombie(pid):
    return subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip().startswith("Z")


def test_stale_engine_from_a_crashed_launch_is_ended(sandbox, tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    stale = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", str(FAKE)])
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])          # not our engine
    dead_app = 2 ** 22 + 7
    (run / f"engine-{dead_app}.pid").write_text(f"{stale.pid} {dead_app}\n")
    (run / f"engine-{dead_app + 1}.pid").write_text(f"{other.pid} {dead_app + 1}\n")
    try:
        live = _live(sandbox)
        live.claim_pidfiles(run)
        assert stale.wait(5) is not None and other.poll() is None
        assert (run / "app.pid").read_text().strip() == str(os.getpid())
        assert not list(run.glob(f"engine-{dead_app}*.pid"))
        live.preload(wait=True)
        assert (run / f"engine-{os.getpid()}.pid").read_text().split()[0] == str(live.engine.pid)
        live.shutdown()
        assert not (run / f"engine-{os.getpid()}.pid").exists() and not (run / "app.pid").exists()
    finally:
        stale.kill(); other.kill()


def test_profile_switch_in_hold_keeps_or_replaces_the_warm_engine(sandbox):
    live = _live(sandbox)
    other = next(p for p in live.cfg["profiles"] if p != live.profile)
    live.cfg["profiles"][other]["translator"] = live.lc["translator"]      # no translator reload here
    try:
        live.preload(wait=True)
        first = live.engine
        live.set_profile(other)                     # same speech model: cfg sent, same engine
        time.sleep(0.3)
        assert live.engine is first and live._engine_alive()
        back = live.profile
        live.cfg["profiles"][back := next(p for p in live.cfg["profiles"] if p != live.profile)]["stt_model"] = "other-model"
        live.set_profile(back)                      # another speech model: a new warm engine
        assert until(lambda: live.engine is not first and live.state()["phase"] == "ready", 10)
        assert live._engine_hold and live._engine_spec == live._spec()
        t0 = time.time()
        live.start()
        assert until(lambda: live.state()["phase"] in ("listening", "captioning"), 1.0) and time.time() - t0 < 1.0
    finally:
        live.shutdown()


def test_captions_behind_comes_from_the_pace(tmp_path):
    live = _live(tmp_path)
    live.engine = types.SimpleNamespace(poll=lambda: None)
    live.running, live._listening = True, True
    now = time.time()
    live._work.put({"t_end": now - 20, "ro": "x"})          # a clause waiting 20 s for the translator
    assert live.state()["pace"]["state"] == "behind"
    assert live.state()["problem"]["code"] == "captions_behind"
    live._work.get_nowait()
    live.engine, live.running = None, False


def test_unload_frees_and_preload_brings_it_back(sandbox):
    live = _live(sandbox)
    try:
        live.preload(wait=True)
        live.unload()
        st = live.state()
        assert st["phase"] == "stopped" and live.tr is None and not live._engine_alive()
        live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "ok then.")
        live.preload(wait=True)
        assert live.state()["phase"] == "ready"
    finally:
        live.shutdown()


# --- state: phase, problem, readiness ---------------------------------------------------------
class PP:
    def __init__(self, code=None):
        self.code, self.last_error = code, None
    def status(self):
        return {"connected": self.code is None, "since": None, "last_ms": None, "error_code": self.code}
    def show(self, text):
        return True
    def clear(self):
        return True


def test_problem_codes_follow_their_signals(tmp_path):
    live = _live(tmp_path)
    alive = types.SimpleNamespace(poll=lambda: None)
    p = lambda: (live.state()["problem"] or {}).get("code")           # noqa: E731
    assert p() is None and live.state()["phase"] == "ready"
    live._models_missing = lambda: ["nvidia/parakeet"]
    assert live.state()["phase"] == "needs_setup" and p() == "models_missing"
    live._models_missing = lambda: []
    live.engine = alive
    for kind, code in (("mic_denied", "mic_denied"), ("no_frames", "device_lost"),
                       ("device_lost", "device_lost"), ("digital_silence", "no_sound")):
        live._audio_kind = kind
        assert p() == code, kind
    live._audio_kind = None
    for err, code in (("unreachable", "pp_unreachable"), ("auth", "pp_unreachable"),
                      ("message_missing", "pp_message_missing")):
        live.pp = PP(err)
        assert p() == code, err
    live.pp = PP()
    live._preflight = {"checks": [{"id": "disk", "ok": False, "detail": "0.2 GB free", "code": "disk_full"}]}
    assert p() == "disk_full"
    live._preflight = None
    live.running, live._listening = True, True
    live.stt_behind = "speech recognition is 5 s behind the audio"
    assert p() == "recognition_behind"
    live.stt_behind, live.behind_s = None, 8.2
    assert p() == "captions_behind"
    live.behind_s, live.running = None, False
    live._english_unavailable = True
    live.lang.set_mode("auto")
    assert p() == "english_unavailable"
    live.lang.set_mode("ro-en")
    assert p() is None
    live.engine = None
    live._engine_dead = "ENGINE STOPPED (exit 1)"
    assert live.state()["phase"] == "error" and p() == "engine_stopped"
    st = live.state()
    check_state(st)


def test_problem_actions_match_the_ui_strings():
    """The banner's action ids are the ones strings.en.js defines for each code."""
    js = (ROOT / "src" / "live" / "ui" / "i18n" / "strings.en.js").read_text()
    for code, (sev, aid, label) in lifecycle.PROBLEMS.items():
        assert code in C["types"]["problem_code"]["enum"]
        i = js.index(f"{code}:")
        assert f'id: "{aid}"' in js[i:i + 400], code
    assert set(lifecycle.PROBLEMS) == set(C["types"]["problem_code"]["enum"])


def test_lifecycle_endpoints_match_the_contract(tmp_path):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    live = _live(tmp_path)
    exits = []
    live.exit_process = lambda: exits.append(1)
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    check(c.get("/api/health").json(), C["types"]["health"])
    rd = c.get("/api/readiness").json()
    check({k: v for k, v in rd.items() if k not in ("progress", "problem")},
          {"object": {k: v for k, v in C["types"]["readiness"]["object"].items() if k not in ("progress", "problem")}})
    assert rd["ready"] is True and rd["phase"] == "ready"
    check(c.get("/api/permissions").json(), C["types"]["permissions"])
    r = c.post("/api/shutdown", json={}, headers=me)
    assert r.status_code == 200 and r.json() == {"ok": True} and live._shutting_down
    assert until(lambda: exits == [1], 2)
    assert c.post("/api/shutdown", json={}, headers={"origin": "https://evil.example"}).status_code == 403
