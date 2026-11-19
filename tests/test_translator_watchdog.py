"""A translator call that never returns (MLX / CTranslate2 hung): translator_stuck after
pipeline.TR_STUCK_S, ranked above captions_behind, and POST /api/restart_captions, which answers
and then hands over to a replaceable relaunch hook (never a real exit or exec here)."""
import pathlib
import sys
import threading
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import lifecycle                              # noqa: E402
import pipeline                               # noqa: E402
from test_contract import C, check            # noqa: E402
from test_lifecycle import _live, until       # noqa: E402

ME = {"origin": "http://127.0.0.1:8765"}


def _clause(ro="Frați și surori, astăzi citim din Romani."):
    now = time.time()
    return {"id": 1, "ro": ro, "multi": "", "en": "", "t_start": now - 2, "t_end": now - 1,
            "t_emit": now - 0.9, "stt_s": 0.1, "audio_s": 1.0}


def _running(live):
    live.engine = types.SimpleNamespace(poll=lambda: None)
    live.running, live._listening = True, True
    live.lang.set_mode("ro-en")


def _blocking(live):
    """A translator whose call waits on an Event: a hung model call until the test releases it."""
    release, entered = threading.Event(), threading.Event()

    def translate(t, *a, **k):
        entered.set()
        release.wait(10)
        return "Brothers and sisters, today we read from Romans."
    live.tr = types.SimpleNamespace(translate=translate)
    return release, entered


def test_a_hung_call_becomes_translator_stuck_and_is_logged_once(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TR_STUCK_S", 0.3)
    live = _live(tmp_path)
    errors = []
    live._app_error = errors.append
    _running(live)
    release, entered = _blocking(live)
    ev = live._current = _clause()                               # as _work_loop sets it
    t = threading.Thread(target=live._handle, args=(ev,), daemon=True)
    try:
        t.start()
        assert entered.wait(5)
        assert live._tr_t0 is not None
        assert until(lambda: (live.state()["problem"] or {}).get("code") == "translator_stuck", 3)
        p = live.state()["problem"]
        assert p == {"code": "translator_stuck", "severity": "error",
                     "action": {"id": "restart_captions", "label": "Restart captions"}}
        live.state()
        stuck = [e for e in errors if e.startswith("translator_stuck")]
        assert len(stuck) == 1 and "Romani" in stuck[0]          # once per call, not per state
        live.running = False                                     # only while captions run
        assert live._problem_code_but_pp(live._phase()) != "translator_stuck"
        live.running = True
    finally:
        release.set()
        t.join(5)
    assert live._tr_t0 is None                                   # cleared when the work ends
    assert (live.state()["problem"] or {}).get("code") != "translator_stuck"
    live.engine, live.running = None, False


def test_a_normal_call_is_not_stuck(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TR_STUCK_S", 0.3)
    live = _live(tmp_path)
    _running(live)
    live._handle(_clause())
    assert live._tr_t0 is None
    time.sleep(0.4)
    assert not live.translator_stuck()
    assert (live.state()["problem"] or {}).get("code") != "translator_stuck"
    live.engine, live.running = None, False


def test_a_failing_call_clears_the_clock(tmp_path):
    live = _live(tmp_path)
    live._app_error = lambda text: None
    _running(live)

    def boom(*a, **k):
        raise RuntimeError("boom")
    live.tr = types.SimpleNamespace(translate=boom)
    live._handle(_clause())
    assert live._tr_t0 is None
    live.engine, live.running = None, False


def test_translator_stuck_outranks_captions_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "TR_STUCK_S", 0.3)
    live = _live(tmp_path)
    live._app_error = lambda text: None
    _running(live)
    live._mic_status = lambda: "granted"
    live.behind_s = 30.0
    live.stt_behind = "speech recognition is 5 s behind the audio"
    assert live._problem_code_but_pp(live._phase()) == "recognition_behind"
    live._tr_t0 = time.monotonic() - 1.0                         # a call running for 1 s > 0.3 s
    assert live._problem_code_but_pp(live._phase()) == "translator_stuck"
    live.stt_behind = None
    assert live._problem_code_but_pp(live._phase()) == "translator_stuck"
    live._tr_t0 = None
    assert live._problem_code_but_pp(live._phase()) == "captions_behind"
    live._tr_t0 = time.monotonic() - 1.0
    live._audio_kind = "digital_silence"                         # the engine/audio problems still come first
    assert live._problem_code_but_pp(live._phase()) == "no_sound"
    live._audio_kind, live._tr_t0, live.behind_s = None, None, None
    live.engine, live.running = None, False


def test_problem_entry():
    assert lifecycle.PROBLEMS["translator_stuck"] == ("error", "restart_captions", "Restart captions")
    assert "translator_stuck" in C["types"]["problem_code"]["enum"]


def _client(live):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    return TestClient(build_app(live), base_url="http://127.0.0.1:8765")


def test_restart_captions_refuses_when_not_running(tmp_path):
    live = _live(tmp_path)
    calls = []
    live.relaunch_process = lambda: calls.append(1)
    c = _client(live)
    r = c.post("/api/restart_captions", json={}, headers=ME)
    assert r.status_code == 409
    check(r.json(), C["types"]["not_running"], "restart_captions")
    assert r.json()["detail"] == "Captions are not running."
    time.sleep(0.4)
    assert calls == []
    assert c.post("/api/restart_captions", json={}, headers={"origin": "https://evil.example"}).status_code == 403


def test_restart_captions_answers_then_relaunches_without_the_lock(tmp_path):
    live = _live(tmp_path)
    errors = []
    live._app_error = errors.append
    calls = []
    live.relaunch_process = lambda: calls.append(live._proc_lock.locked())
    ended = []
    eng = types.SimpleNamespace(pid=999999, poll=lambda: None if not ended else 0)
    eng.wait = lambda timeout=None: 0
    live.running, live._listening, live.engine = True, True, eng
    terminated = []
    orig = lifecycle._terminate
    lifecycle._terminate = lambda pid, wait_s=2.0: (terminated.append(pid), ended.append(1))
    live._proc_lock.acquire()                                    # the hung call holds it
    try:
        r = _client(live).post("/api/restart_captions", json={}, headers=ME)
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert until(lambda: calls, 3)
    finally:
        live._proc_lock.release()
        lifecycle._terminate = orig
    assert calls == [True]                                       # it never waited for the translator
    assert terminated == [999999] and eng.retired is True        # the engine was ended, as ours
    assert live._shutting_down                                   # no engine/voice restart behind it
    assert any(e.startswith("restart_captions") for e in errors)
    live.engine, live.running = None, False


def test_relaunch_exits_when_supervised_and_execs_otherwise(monkeypatch):
    done = []
    def _exit(code):
        done.append(("exit", code))
        raise SystemExit(code)                                   # os._exit never returns
    monkeypatch.setattr(lifecycle.os, "_exit", _exit)
    monkeypatch.setattr(lifecycle.os, "execv", lambda exe, argv: done.append(("exec", exe, argv)))
    monkeypatch.setenv("LIVE_SUPERVISED", "1")
    with pytest.raises(SystemExit):
        lifecycle._relaunch()
    assert done == [("exit", lifecycle.RESTART_EXIT_CODE)]
    done.clear()
    monkeypatch.delenv("LIVE_SUPERVISED")
    monkeypatch.setattr(lifecycle.sys, "orig_argv", ["python", "-s", "src/live/app.py", "--port", "8765", "--autostart"],
                        raising=False)
    lifecycle._relaunch()
    assert done == [("exec", sys.executable,
                     [sys.executable, "-s", "src/live/app.py", "--port", "8765", "--autostart", "--resumed"])]


def test_live_remote_full_view_may_restart_the_captions():
    """The banner shows in Live Remote's full view too; an operator at the iPad during a hang needs it."""
    import remote
    assert ("POST", "/api/restart_captions") in remote.APP_CALLS
