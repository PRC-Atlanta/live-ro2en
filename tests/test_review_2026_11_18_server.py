"""Review 2026-11-18, fix agent A (lifecycle, output, state, routes, display): regression tests.
No models: a stub translator, a fake ProPresenter, tests/fake_engine.py for the warm engine."""
import asyncio
import json
import pathlib
import sys
import threading
import time
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
sys.path.insert(0, str(ROOT / "tests"))
from live.app import Live, autostart_when_up, build_app   # noqa: E402
import lifecycle                                          # noqa: E402
import output                                             # noqa: E402
import routes                                             # noqa: E402
from propresenter import ProPresenter                     # noqa: E402

FAKE = ROOT / "tests" / "fake_engine.py"
ME = {"origin": "http://127.0.0.1:8765"}


def until(cond, timeout=5.0, every=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(every)
    return bool(cond())


class PP:
    """A ProPresenter that remembers what it was asked to show."""
    def __init__(self, code=None):
        self.code, self.last_error, self.shown, self._want = code, None, [], None
    def status(self):
        return {"connected": self.code is None, "since": None, "last_ms": None, "error_code": self.code, "enabled": True}
    def show(self, text):
        self._want = text
        self.shown.append(text)
        return True
    def clear(self):
        return self.show("")


def _live(tmp_path, monkeypatch, file=None):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["propresenter"]["health_every_s"] = 0
    for prof in cfg["profiles"].values():
        prof.pop("translator_en_ro", None)
    live = Live(cfg, types.SimpleNamespace(file=file, settings=str(tmp_path / "settings.yaml")))
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: f"EN: {t}", context=[("a", "b", "ro")])
    live.pp = PP()
    live.log_path = tmp_path / "log.jsonl"
    live._models_missing = lambda: []
    live.preload_translators = lambda: None
    return live


# --- P1: one bad engine event must not end the reader ------------------------------------------
def test_reader_survives_an_event_that_raises(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    lines = [json.dumps({"type": "partial"}) + "\n",                  # no "text": KeyError in the handler
             json.dumps({"type": "partial"}) + "\n",
             json.dumps({"type": "partial", "text": "still reading"}) + "\n"]
    eng = types.SimpleNamespace(stdout=iter(lines), wait=lambda: 0, pid=None)
    live._reader(eng)                                                 # an older engine: returns after the loop
    assert live.partial == "still reading"
    assert live._event_errors == 2
    assert live.warnings.count(lifecycle.EVENT_FAILED_WARNING) == 1   # warned once
    log = (tmp_path / "logs" / "app_errors.log").read_text()
    assert log.count("engine event failed") == 2 and "KeyError" in log


def test_reader_survives_an_unwritable_error_log(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.paths, "log_dir", lambda root: (_ for _ in ()).throw(OSError("read-only")))
    eng = types.SimpleNamespace(stdout=iter(['{"type": "partial"}\n', '{"type": "partial", "text": "ok"}\n']),
                                wait=lambda: 0, pid=None)
    live._reader(eng)
    assert live.partial == "ok"


# --- P1: autostart after the web server answers; --resumed -------------------------------------
def test_autostart_waits_for_the_server_and_survives_a_failing_start(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    server = types.SimpleNamespace(started=False, should_exit=False)
    calls = []

    def start():
        calls.append(server.started)
        raise RuntimeError("no input")
    live.start = start
    th = threading.Thread(target=autostart_when_up, args=(live, server, True, True), daemon=True)
    th.start()
    time.sleep(0.3)
    assert calls == []                                    # not before the server is up
    server.started = True
    th.join(3)
    assert calls == [True] and not th.is_alive()
    assert lifecycle.RESUMED_WARNING in live.warnings      # said after Start (which resets the warnings)


def test_resumed_alone_starts_nothing_and_a_dead_server_starts_nothing(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    live.start = lambda: pytest.fail("must not start")
    autostart_when_up(live, types.SimpleNamespace(started=True, should_exit=False), False, True)
    assert lifecycle.RESUMED_WARNING in live.warnings and not live.running
    autostart_when_up(live, types.SimpleNamespace(started=False, should_exit=True), True, False)


def test_resumed_flag_is_parsed_and_main_no_longer_starts_before_uvicorn():
    src = (ROOT / "src" / "live" / "app.py").read_text()
    assert '"--resumed"' in src
    main = src[src.index("def main"):src.index("def autostart_when_up")]
    i_server = main.index("server.run()")
    assert "live.start()" not in main[main.index("import uvicorn"):i_server]


# --- P1: a test caption that was not sent never comes back -------------------------------------
def test_unsent_test_caption_is_not_wanted_later(tmp_path, monkeypatch):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()                                             # nothing listens there
    live = _live(tmp_path, monkeypatch)
    live.pp = ProPresenter(**{**live.cfg["propresenter"], "port": port, "backoff_s": (30,), "timeout": 0.5})
    live.pp._want = "Brothers and sisters"                # the service caption
    res = live.pp_test_caption(restore_after_s=0.05)
    assert not res["ok"]
    assert live.pp._want == "Brothers and sisters"        # the health check would re-send this, not the test
    res = live.pp_test_caption(restore_after_s=0.05)      # again, while backing off
    assert not res["ok"] and live.pp._want == "Brothers and sisters"


# --- P1: nothing carries over into the next service --------------------------------------------
def test_new_run_starts_clean(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.paths, "services_dir", lambda d, root: tmp_path / "services")
    resets = []
    live.tr = types.SimpleNamespace(reset_context=lambda: resets.append("local"))
    live._ai_tr = types.SimpleNamespace(reset_context=lambda: resets.append("ai"))
    live._show("Last week's caption")
    live.history = [{"source": "a", "translation": "b", "latency_s": 1.0, "lang": "ro", "t_display": time.time()}]
    live.lat = [1.0, 2.0]
    live._restarts.extend([time.monotonic(), time.monotonic()])
    live.pp.shown.clear()
    live._new_run()
    st = live.state()
    assert st["screen"] == "" and live.caps.retract() is None     # no undo into the last service
    assert live.pp.shown == [""]                                    # the screen is cleared
    assert st["lines"] == [] and live.lat == [] and st["latency_last"] is None
    assert len(live._restarts) == 0
    assert "local" in resets and live._ai_tr is None                # the AI translator is made anew


def test_new_run_keeps_the_song_symbol(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.paths, "services_dir", lambda d, root: tmp_path / "services")
    live.song(True)
    live.pp.shown.clear()
    live._new_run()
    assert live.pp.shown == []


# --- P1: Fix last caption never overwrites a newer caption -------------------------------------
def test_fix_last_refuses_when_a_newer_caption_came_in(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    for i, t in enumerate(("First caption.", "Second caption.")):
        live._show(t)
        live.history.append({"source": str(i), "translation": t, "latency_s": 1.0, "lang": "ro"})
    with pytest.raises(output.StaleCorrection):
        live.correct("Fixed first.", based_on="First caption.")
    assert "Second caption." in live.caps.text
    assert live.correct("Fixed second.", based_on="Second caption.") is True
    assert live.caps.text.endswith("Fixed second.")
    assert live.correct("Fixed again.") is True                     # without based_on: as before


def test_fix_last_route_answers_stale(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    live = _live(tmp_path, monkeypatch)
    live._show("Newer caption.")
    live.history.append({"source": "x", "translation": "Newer caption.", "latency_s": 1.0, "lang": "ro"})
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    r = c.post("/api/correct", json={"text": "Fixed.", "based_on": "Older caption."}, headers=ME)
    assert r.status_code == 409 and r.json()["error"] == "stale"
    assert "Newer caption." in live.caps.text
    r = c.post("/api/correct", json={"text": "Fixed.", "based_on": "Newer caption."}, headers=ME)
    assert r.status_code == 200 and live.caps.text.endswith("Fixed.")


# --- the Clear button: Blank wipes the box, its undo and the screen, and is logged -------------
def test_clear_button_wipes_everything_and_is_allowed_from_live_remote(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    import remote
    live = _live(tmp_path, monkeypatch)
    live._show("Something on screen.")
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    r = c.post("/api/blank", json={}, headers=ME)
    assert r.status_code == 200 and r.json()["screen"] == ""
    assert live.caps.retract() is None and live.pp.shown[-1] == ""
    assert "blank" in [json.loads(x).get("event") for x in open(live.log_path)]
    assert ("POST", "/api/blank") in remote.APP_CALLS
    js = (ROOT / "src" / "live" / "ui" / "screens" / "live.js").read_text()
    assert "data-clear" in js and 'run("blank")' in js


# --- P2: ProPresenter problems do not hide lower-ranked ones from the banner -------------------
def test_pp_problem_ranks_below_the_others(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    p = lambda: (live.state()["problem"] or {}).get("code")         # noqa: E731
    live.pp = PP("unreachable")
    assert p() == "pp_unreachable"                                  # alone: still reported
    live._preflight = {"checks": [{"id": "disk", "ok": False, "code": "disk_full"}]}
    assert p() == "disk_full"
    live._preflight = None
    live.running, live._listening, live.behind_s = True, True, 8.0
    assert p() == "captions_behind"


# --- P2: /ws gets the newest state; the state is built once per broadcast ----------------------
def test_broadcast_builds_the_state_once(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    got = []

    class L:
        put_nowait = staticmethod(got.append)
    for _ in range(3):
        live.listeners.add(L())
    n = []
    real = live.state
    live.state = lambda: n.append(1) or real()
    live._broadcast()
    assert len(n) == 1 and len(got) == 3 and got[0] is got[1] is got[2]


def test_ws_feed_sends_the_newest_state_not_the_oldest():
    live = types.SimpleNamespace(listeners=set(), state=lambda: {"n": 0})
    sent, gate, stop = [], None, []

    class Sock:
        async def send_json(self, s):
            sent.append(s["n"])
            if len(sent) == 1:
                await gate.wait()                         # the page is slow: states pile up meanwhile
            else:
                stop.append(1)

    async def main():
        nonlocal gate
        gate = asyncio.Event()
        task = asyncio.create_task(routes.state_feed(live, Sock(), still_ok=lambda: not stop))
        while not live.listeners:
            await asyncio.sleep(0.01)
        b = next(iter(live.listeners))
        for i in range(1, 80):
            b.put_nowait({"n": i})
        await asyncio.sleep(0.05)
        gate.set()
        await asyncio.wait_for(task, 3)
    asyncio.run(main())
    assert sent == [0, 79]


# --- P2: the warm engine's ready-wait has a deadline -------------------------------------------
def test_a_hung_preload_ends_in_an_error_not_loading_for_ever(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_ENGINE_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_MIC", "1")
    monkeypatch.setenv("FAKE_LOAD_S", "2")
    monkeypatch.setattr(lifecycle, "ENGINE_SCRIPT", FAKE)
    monkeypatch.setattr(lifecycle, "PRELOAD_READY_S", 0.5)
    live = _live(tmp_path, monkeypatch)
    try:
        live.preload()
        assert until(lambda: live.state()["phase"] == "error", 20)
        assert "did not finish loading" in live._start_error and not live._engine_alive()
        assert live.state()["problem"]["code"] == "engine_stopped"
    finally:
        live.shutdown()


# --- P2/P3: the routes -------------------------------------------------------------------------
def test_mode_route_refuses_an_unknown_mode(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    live = _live(tmp_path, monkeypatch)
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    for b in ({}, {"mode": "fr-en"}, {"mode": 3}):
        assert c.post("/api/mode", json=b, headers=ME).status_code == 400
    assert c.post("/api/mode", content=b"mode=auto", headers={**ME, "content-type": "text/plain"}).status_code == 415
    r = c.post("/api/mode", json={"mode": "en-ro"}, headers=ME)
    assert r.status_code == 200 and r.json()["mode"] == "en-ro"
    assert c.post("/api/engine", content=b"engine=ai", headers={**ME, "content-type": "text/plain"}).status_code == 415
    assert c.post("/api/voice/test", json={"lang": "fr"}, headers=ME).status_code == 400


def test_devices_restarts_portaudio_before_listing(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    calls = []
    fake = types.SimpleNamespace(
        _terminate=lambda: calls.append("terminate"), _initialize=lambda: calls.append("init"),
        query_devices=lambda: calls.append("query") or [{"name": "Mixer", "max_input_channels": 2},
                                                        {"name": "Speakers", "max_input_channels": 0}])
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    live = _live(tmp_path, monkeypatch)
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    r = c.get("/api/devices", headers=ME)
    assert r.json() == [{"index": 0, "name": "Mixer"}]
    assert calls == ["terminate", "init", "query"]


# --- P3: the network page stops when display.network goes off ----------------------------------
def test_network_page_feed_closes_when_turned_off(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from starlette.websockets import WebSocketDisconnect
    import display
    live = _live(tmp_path, monkeypatch)
    live.cfg.setdefault("display", {})["network"] = True
    c = TestClient(display.network_app(live))
    with c.websocket_connect("/ws") as ws:
        assert "screen" in ws.receive_json()
        live.cfg["display"]["network"] = False
        live._show("A new caption")                       # would be sent if the feed went on
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
