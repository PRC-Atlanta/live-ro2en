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

    def start(**kw):
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
    # said after Start (which resets the warnings); Start failed: the operator is asked to press it
    assert lifecycle.RESUMED_FAILED_WARNING in live.warnings and lifecycle.RESUMED_WARNING not in live.warnings


def test_resumed_alone_starts_nothing_and_a_dead_server_starts_nothing(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    live.start = lambda **kw: pytest.fail("must not start")
    autostart_when_up(live, types.SimpleNamespace(started=True, should_exit=False), False, True)
    assert lifecycle.RESUMED_FAILED_WARNING in live.warnings and not live.running   # nothing started: say so
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
    assert live.pp.shown == [] and live.pp._want == ""             # nothing sent under _control ...
    live._send_wanted_screen()
    assert live.pp.shown == [""]                                    # ... the screen is cleared after it
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


# === round 2 ===================================================================================
# --- P2: a resumed relaunch is "starting" (live for the Mac app) until its Start has run -------
def test_autostart_pending_reports_starting_even_while_loading(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    server = types.SimpleNamespace(started=False, should_exit=False)
    go, phases = threading.Event(), []

    def start(**kw):
        phases.append(live._phase())
        go.wait(3)
        live.running = True                               # a Start that went live
    live.start = start
    live.phase_hint = "loading"                           # the preload is still loading the models
    th = threading.Thread(target=autostart_when_up, args=(live, server, True, True), daemon=True)
    th.start()
    assert until(lambda: live._autostart_pending)
    assert live._phase() == "starting" and live.state()["phase"] == "starting"   # LiveActivity.isLive
    server.started = True
    assert until(lambda: phases)
    assert phases == ["starting"]
    go.set()
    th.join(3)
    assert not live._autostart_pending
    assert lifecycle.RESUMED_WARNING in live.warnings and lifecycle.RESUMED_FAILED_WARNING not in live.warnings


def test_autostart_that_fails_ends_starting(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    live.start = lambda **kw: (_ for _ in ()).throw(RuntimeError("no input"))
    live._start_error = "start failed: no input"
    autostart_when_up(live, types.SimpleNamespace(started=True, should_exit=False), True, True)
    assert not live._autostart_pending and live._phase() == "error"
    assert lifecycle.RESUMED_FAILED_WARNING in live.warnings


# === round 3 ===================================================================================
# --- P3: only a --resumed relaunch is "starting" before its Start; a plain autostart is not -------
def test_plain_autostart_is_not_starting_before_its_start(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    server = types.SimpleNamespace(started=False, should_exit=False)
    go, phases = threading.Event(), []

    def start(**kw):
        phases.append(live._phase())
        go.wait(3)
    live.start = start
    live.phase_hint = "loading"                           # a first model load
    th = threading.Thread(target=autostart_when_up, args=(live, server, True, False), daemon=True)
    th.start()
    assert until(lambda: live._autostart_waiting)
    assert not live._autostart_pending and live._phase() == "loading"   # not live for sleep/quit
    server.started = True
    assert until(lambda: phases)
    assert phases == ["loading"]
    go.set()
    th.join(3)
    assert not live._autostart_waiting


def test_main_marks_only_a_resumed_autostart_as_pending():
    src = (ROOT / "src" / "live" / "app.py").read_text()
    main = src[src.index("def main"):src.index("def autostart_when_up")]
    assert "live._autostart_pending = bool(autostart) and a.resumed" in main


# --- P3: Stop while an autostart waits for the preload: it does not go live ----------------------
def test_stop_during_a_pending_autostart_cancels_it(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    server = types.SimpleNamespace(started=True, should_exit=False)
    loaded = threading.Event()
    live._preload_thread = threading.Thread(target=lambda: loaded.wait(5), daemon=True)
    live._preload_thread.start()                          # the models are still loading
    went = []
    live._go_live = lambda *a, **k: went.append("go_live")
    live._start_engine = lambda *a, **k: went.append("engine")
    live.load_translator = lambda *a, **k: None
    th = threading.Thread(target=autostart_when_up, args=(live, server, True, True), daemon=True)
    th.start()
    assert until(lambda: live._autostart_pending)
    assert live._phase() == "starting"
    live.stop()                                           # the operator's Stop, before Start has run
    assert live._autostart_cancel and not live._autostart_pending
    assert live._phase() != "starting"
    loaded.set()                                          # the preload ends: start() goes on, and returns
    th.join(5)
    assert not th.is_alive()
    assert went == [] and not live.running
    assert lifecycle.RESUMED_FAILED_WARNING not in live.warnings      # the operator stopped it
    live._retire_engine = lambda: None
    live.start()                                          # the operator's own Start is not cancelled
    assert went == ["engine"] and live.running


# --- P2: Fix last checks the caption box's last caption by its number ---------------------------
def _shown(live, text):
    """What the pipeline does: the caption into the box, then its line into the history."""
    live._show(text)
    live.history.append({"source": "x", "translation": text, "latency_s": 1.0, "lang": "ro",
                         "caption_seq": live.caps.last_seq})


def test_fix_last_refuses_after_a_remove_last_in_between(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    _shown(live, "First caption.")
    _shown(live, "Second caption.")
    seq = live.state()["lines"][-1]["seq"]
    assert seq == live.caps.last_seq
    live.retract()                                        # Remove last while the operator types
    with pytest.raises(output.StaleCorrection):
        live.correct("Fixed second.", based_on="Second caption.", based_on_seq=seq)
    assert live.caps.text == "First caption."             # the caption under it is untouched


def test_fix_last_refuses_a_newer_caption_not_in_the_lines_yet(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    _shown(live, "First caption.")
    seq = live.state()["lines"][-1]["seq"]
    live._show("Second caption.")                         # on screen, its line not appended yet
    with pytest.raises(output.StaleCorrection):
        live.correct("Fixed first.", based_on="First caption.", based_on_seq=seq)
    assert "Second caption." in live.caps.text


def test_fix_last_by_number_fixes_that_line_and_can_fix_it_again(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    _shown(live, "First caption.")
    _shown(live, "Second caption.")
    live.history.append({"source": "song", "translation": "", "latency_s": 1.0, "lang": "ro"})   # not in the box
    seq = live.caps.last_seq
    assert live.correct("Fixed second.", based_on="Second caption.", based_on_seq=seq) is True
    assert live.caps.text.endswith("Fixed second.") and live.caps.last_seq == seq   # the same caption
    assert live.history[1]["translation"] == "Fixed second." and live.history[1]["corrected"]
    assert live.correct("Fixed twice.", based_on_seq=seq) is True
    assert live.correct("No numbers.") is True                       # older pages: as before


def test_fix_last_route_takes_the_number(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    live = _live(tmp_path, monkeypatch)
    _shown(live, "Only caption.")
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    seq = live.caps.last_seq
    r = c.post("/api/correct", json={"text": "Fixed.", "based_on_seq": "1"}, headers=ME)
    assert r.status_code == 400
    r = c.post("/api/correct", json={"text": "Fixed.", "based_on": "Only caption.", "based_on_seq": seq + 1}, headers=ME)
    assert r.status_code == 409 and r.json()["error"] == "stale"
    r = c.post("/api/correct", json={"text": "Fixed.", "based_on": "Only caption.", "based_on_seq": seq}, headers=ME)
    assert r.status_code == 200 and live.caps.text == "Fixed."
    js = (ROOT / "src" / "live" / "ui" / "screens" / "live.js").read_text()
    assert "based_on_seq: last.seq" in js


# --- round 3, P3: after Remove last, Fix last fixes the caption still on screen -----------------
def test_state_carries_last_seq_and_fix_last_works_after_a_remove_last(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    assert live.state()["last_seq"] is None                # nothing in the caption box yet
    _shown(live, "First caption.")
    first = live.caps.last_seq
    _shown(live, "Second caption.")
    assert live.state()["last_seq"] == live.caps.last_seq != first
    live.retract()                                        # Remove last
    st = live.state()
    assert st["last_seq"] == first                        # the caption on screen now
    line = next(l for l in st["lines"] if l["seq"] == st["last_seq"])   # what the Fix dialog picks
    assert line["dst"] == "First caption."
    assert live.correct("Fixed first.", based_on=line["dst"], based_on_seq=line["seq"]) is True
    assert live.caps.text == "Fixed first."
    assert live.history[0]["translation"] == "Fixed first." and live.history[0]["corrected"]
    js = (ROOT / "src" / "live" / "ui" / "screens" / "live.js").read_text()
    assert "l.seq === s.last_seq" in js


# --- P3: Fix last's text shows where the caption it replaces is ---------------------------------
def test_replace_last_on_screen_shows_at_once():
    import captions
    now = [0.0]
    c = captions.RollingCaptions(max_chars=14, max_lines=1, read_wps=1.0, min_read_s=1.0, max_lag_s=0,
                                 clock=lambda: now[0])
    c.add("one two three")
    c.add("four five six seven eight")
    now[0] = 10.0                                         # "one two three" read: its first line comes up
    assert c.advance() and c.lines == ["four five six"] and c.queue == ["seven eight"]
    assert c.replace_last("FIXED words") == "FIXED words"   # at once, not after a read
    assert not c.queue and c.retract() == "one two three"


def test_replace_last_waiting_stays_in_its_place():
    import captions
    now = [0.0]
    c = captions.RollingCaptions(max_chars=20, max_lines=1, read_wps=1.0, min_read_s=1.0, max_lag_s=0,
                                 clock=lambda: now[0])
    c.add("one two three four five six seven eight nine ten")
    c.add("eleven twelve")                                # all of it waits behind the lines before it
    screen, waiting = c.text, " ".join(c.queue)
    assert screen == "one two three four" and waiting.endswith("ten eleven twelve")
    assert c.replace_last("ELEVEN") == screen             # the screen does not change
    assert " ".join(c.queue) == waiting[:-len("eleven twelve")] + "ELEVEN"   # in its place, still waiting
    assert c.last_seq == 2


# --- P3: a profile switch that restarts the engine is not a new service -------------------------
def test_profile_switch_restart_does_not_wipe_the_service(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.paths, "services_dir", lambda d, root: tmp_path / "services")
    other = next(n for n in live.cfg["profiles"] if n != live.profile)
    key = live.RESTART_KEYS[0]
    live.cfg["profiles"][other] = {**(live.cfg["profiles"][other] or {}), key: "another-model"}
    live.running, live.engine = True, types.SimpleNamespace(poll=lambda: None)
    calls = []
    live.stop = lambda: calls.append("stop")
    live.start = lambda device=None, fresh=True: calls.append(("start", fresh))
    live._swap_profile = lambda name, new: {}
    live.set_profile(other)
    assert calls == ["stop", ("start", False)]
    _shown(live, "Mid-service caption.")
    live.pp.shown.clear()
    live._ai_tr = ai_tr = types.SimpleNamespace(reset_context=lambda: pytest.fail("context kept"))
    live._new_run(fresh=False)
    assert live.caps.text == "Mid-service caption." and live.history and live.pp.shown == []
    assert live._ai_tr is ai_tr
    assert not live._screen_due


# --- P3: Start with Hold on keeps the held screen; Clear during a song keeps ♪ ------------------
def test_new_run_with_hold_on_clears_the_box_but_not_the_held_screen(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    _shown(live, "Held caption.")
    live.hold(True)
    live.pp.shown.clear()
    live._new_run_screen()
    live._send_wanted_screen()
    assert live.caps.text == "" and live.pp.shown == [] and live.pp._want == "Held caption."


def test_clear_during_a_song_keeps_the_song_symbol(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    live.song(True)
    live.pp.shown.clear()
    live.blank()
    assert live.pp.shown == ["♪"]
    live.song(False)
    live.blank()
    assert live.pp.shown[-1] == ""


# --- P3: the event-failure count is per service -------------------------------------------------
def test_event_errors_reset_for_a_new_service(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    monkeypatch.setattr(lifecycle.paths, "services_dir", lambda d, root: tmp_path / "services")
    live._event_errors = lifecycle.EVENT_ERRORS_LOGGED + 5
    live._new_run()
    assert live._event_errors == 0


# --- P3: the Clear button is not inside the tablist ---------------------------------------------
def test_clear_button_sits_outside_the_tablist():
    js = (ROOT / "src" / "live" / "ui" / "screens" / "live.js").read_text()
    i = js.index('role="tablist"')
    tablist = js[i:js.index("</div>", i)]
    assert 'role="tab"' in tablist and "data-clear" not in tablist and "tabhint" not in tablist
    assert "data-clear" in js
