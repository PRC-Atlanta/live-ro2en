"""S2: the operator's controls over the screen (hide, blank, hold, remove last, fix last,
box size) and ProPresenter robustness (health, backoff, reconnect, resend after an outage or
a manual clear), against the mock ProPresenter with fault injection."""
import json
import pathlib
import socket
import sys
import time
import types
import urllib.request

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live                                   # noqa: E402
from live.captions import RollingCaptions                   # noqa: E402
from live.mock_propresenter import MockState, serve         # noqa: E402
from live.propresenter import ProPresenter                  # noqa: E402

C = json.loads((ROOT / "src" / "live" / "ui" / "contract.json").read_text())


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def mock():
    port = _port()
    srv, st = serve(port)
    st.port = port
    yield st
    srv.shutdown()
    srv.server_close()


def _mock_call(st, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{st.port}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=2) as r:
        return json.loads(r.read() or b"null")


def _api_calls(st, since=0):
    return [e for e in st.events[since:] if not e["path"].startswith("/mock/")]


def _live(tmp_path, port, **pp_kw):
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["propresenter"].update(port=port, health_every_s=0, **pp_kw)
    live = Live(cfg, types.SimpleNamespace(file=None))
    live.pp = ProPresenter(**{**cfg["propresenter"], "backoff_s": (0,)})
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: f"EN: {t}", context=[("a", "b", "ro")])
    return live


def _log(live):
    return [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []


def _clause(i, ro, ago=0.5):
    t = time.time() - ago
    return {"id": i, "ro": ro, "multi": "", "en": "", "t_start": t - 1, "t_end": t, "t_emit": t + 0.1,
            "stt_s": 0.1, "audio_s": 1.0}


# --- captions ------------------------------------------------------------------------------
def test_retract_and_replace_last_reshow_previous_lines():
    c = RollingCaptions(max_chars=20, max_lines=2)
    c.add("one two three")
    c.add("four five six seven")
    c.add("eight nine ten")
    assert c.text == "four five six seven\neight nine ten"
    assert c.retract() == "one two three\nfour five six seven"       # the line that scrolled off is back
    assert c.replace_last("FOUR") == "one two three FOUR"
    assert c.retract() == "one two three"
    assert c.retract() == ""
    assert c.retract() is None and c.replace_last("x") is None
    c.add("alpha")
    c.clear()
    assert c.retract() is None                                         # Blank is final


def test_resize_rewraps_whole_lines():
    c = RollingCaptions(max_chars=42, max_lines=2)
    c.add("For God so loved the world that he gave his only Son")
    c.resize(max_lines=3, max_chars=20)
    assert all(len(l) <= 20 for l in c.lines) and len(c.lines) == 3
    assert c.text.replace("\n", " ").endswith("gave his only Son")
    c.resize(max_lines=1)
    assert c.lines == ["only Son"] or len(c.lines) == 1


# --- hide / show ---------------------------------------------------------------------------
def test_hide_blanks_and_makes_no_propresenter_call(tmp_path, mock):
    live = _live(tmp_path, mock.port, stage_message=True)
    live._show("Brothers and sisters")
    assert mock.visible == {"Live Translation": "Brothers and sisters"} and mock.stage
    live.pause(True)
    assert live.state()["paused"] is True and mock.visible == {} and mock.stage is None
    n = len(mock.events)
    assert live._show("This must not be shown") == ("", None, "paused")
    assert len(mock.events) == n                                      # no ProPresenter call at all
    assert live.caps.text == ""
    live.pause(False)
    assert live.tr.context == []                                      # translator context reset
    assert live._show("After the prayer")[0] == "After the prayer"    # starts clean, no backlog dump
    assert mock.visible["Live Translation"] == "After the prayer"
    assert [r["on"] for r in _log(live) if r.get("event") == "pause"] == [True, False]


@pytest.mark.skipif("self._show(" not in (ROOT / "src" / "live" / "pipeline.py").read_text(),
                    reason="pipeline.py (S4) does not call OutputMixin._show yet")
def test_hidden_clauses_are_logged_as_paused(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live.lang.set_mode("ro-en")
    live.pause(True)
    n = len(mock.events)
    live._handle(_clause(1, "Frați și surori, astăzi citim din Romani."))
    assert len(mock.events) == n
    assert [r["outcome"] for r in _log(live) if "outcome" in r] == ["paused"]


# --- blank, hold, retract, correct, layout --------------------------------------------------
def test_blank_resets_so_the_same_text_is_sent_again(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live._show("Amen")
    live.blank()
    assert mock.visible == {}
    n = len(_api_calls(mock))
    live._show("Amen")
    assert mock.visible["Live Translation"] == "Amen" and len(_api_calls(mock)) > n
    assert any(r.get("event") == "blank" for r in _log(live))


def test_hold_keeps_the_screen_until_released(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live._show("First.")
    live.hold(True)
    assert live._show("Second.") == ("First. Second.", None, "held")
    assert mock.visible["Live Translation"] == "First."
    live.hold(False)
    assert mock.visible["Live Translation"] == "First. Second." and live.state()["held"] is False


def test_retract_reshows_previous_lines_and_is_logged(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live.caps.max_chars, live.caps.max_lines = 20, 2
    for t in ("one two three", "four five six seven", "eight nine ten"):
        live._show(t)
    assert live.retract() is True
    assert mock.visible["Live Translation"] == "one two three\nfour five six seven"
    assert live.state()["corrections_n"] == 1
    rec = [r for r in _log(live) if r.get("event") == "correction"][-1]
    assert rec["kind"] == "retract" and rec["removed"].endswith("eight nine ten")


def test_correct_replaces_last_caption(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, "Pastorul Ionuț ne-a vorbit astăzi."))     # shown via the pipeline
    assert live.correct("Pastor Ionuț spoke to us today.") is True
    assert mock.visible["Live Translation"] == "Pastor Ionuț spoke to us today."
    st = live.state()
    assert st["lines"][-1]["dst"] == "Pastor Ionuț spoke to us today." and st["corrections_n"] == 1
    assert [r["kind"] for r in _log(live) if r.get("event") == "correction"] == ["correct"]
    live.log_path = tmp_path / "next_service.jsonl"                    # a new service counts afresh
    assert live.state()["corrections_n"] == 0


def test_layout_resizes_and_rejects_bad_sizes(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live._show("For God so loved the world that he gave his only Son")
    live.layout(lines=3, chars=20)
    assert all(len(l) <= 20 for l in mock.visible["Live Translation"].split("\n"))
    for bad in ({"lines": 0}, {"chars": 500}, {"lines": True}):
        with pytest.raises(ValueError):
            live.layout(**bad)


# --- ProPresenter robustness ----------------------------------------------------------------
def test_down_three_failures_then_up_resends_current_text(mock):
    pp = ProPresenter(port=mock.port, backoff_s=(0,))
    assert pp.show("A") and pp.status()["connected"]
    _mock_call(mock, "/mock/down")
    for t in ("B", "C", "D"):                                           # three captions fail
        assert pp.show(t) is False
    st = pp.status()
    assert st == {**st, "connected": False, "error_code": "unreachable"} and pp.fails >= 3
    assert pp.health()["connected"] is False
    _mock_call(mock, "/mock/up")
    assert pp.health() == {**pp.status(), "connected": True, "error_code": None}
    assert mock.visible["Live Translation"] == "D" and pp.resends == 1   # the current text, re-sent
    assert pp.last_error is None


def test_backoff_skips_sends_while_unreachable(mock):
    pp = ProPresenter(port=mock.port, backoff_s=(30,))
    _mock_call(mock, "/mock/down")
    n = len(mock.events)
    assert pp.show("A") is False
    assert pp.show("B") is False and pp.show("C") is False
    assert len(mock.events) == n + 1                                    # only the first one tried
    assert not pp.health_due(5)
    _mock_call(mock, "/mock/up")
    st = pp.reconnect()                                                 # Reconnect ignores the backoff
    assert st["connected"] and mock.visible["Live Translation"] == "C"


def test_manual_clear_in_propresenter_is_followed_by_a_resend(mock):
    pp = ProPresenter(port=mock.port)
    pp.show("Grace and peace")
    pp.health()
    assert pp.resends == 0                                              # all fine: nothing re-sent
    _mock_call(mock, "/mock/clear")                                     # someone clears it by hand
    pp.health()
    assert mock.visible["Live Translation"] == "Grace and peace" and pp.resends == 1
    pp.clear()
    pp.health()
    assert mock.visible == {} and pp.resends == 1                       # a wanted blank stays blank


def test_message_missing_is_reported():
    port = _port()
    srv, st = serve(port, state=MockState(messages=("Announcements",)))
    try:
        pp = ProPresenter(port=port, message_id="Live Translation")
        assert pp.health()["error_code"] == "message_missing"
        assert pp.status()["connected"] is True
        assert pp.show("x") is False and pp.error_code == "message_missing"
        assert pp.list_messages() == ["Announcements"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_mock_fail_injects_n_errors(mock):
    pp = ProPresenter(port=mock.port)
    assert _mock_call(mock, "/mock/fail", {"n": 2}) == {"fail_n": 2}
    assert pp.show("one") is False and "500" in pp.last_error
    assert pp.show("one") is False
    assert pp.show("one") is True and mock.visible["Live Translation"] == "one"


def test_unreachable_host_and_stage_failure_keep_main_error():
    pp = ProPresenter(port=_port(), timeout_s=0.3, backoff_s=(0,))
    assert pp.show("x") is False and pp.status()["error_code"] == "unreachable"
    assert pp.list_messages() is None


def test_a_slow_health_ping_does_not_hold_up_a_caption():
    """ProPresenter answers captions but its /version hangs: the health check times out on its
    own thread while a caption goes straight through."""
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _ok(self):
            if self.path == "/version":
                time.sleep(1.0)
            self.send_response(204)
            self.end_headers()
        do_GET = do_POST = do_PUT = do_DELETE = _ok

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        pp = ProPresenter(port=srv.server_address[1], timeout_s=3)
        th = threading.Thread(target=pp.health)
        th.start(); time.sleep(0.1)
        t0 = time.time()
        assert pp.show("Grace and peace") is True
        assert time.time() - t0 < 0.5
        th.join()
    finally:
        srv.shutdown()
        srv.server_close()


def test_unreachable_main_caption_skips_the_stage_mirror():
    pp = ProPresenter(port=_port(), timeout_s=0.3, backoff_s=(0,), stage_message=True)
    calls = []
    real = pp._req
    pp._req = lambda *a, **k: calls.append(a[1]) or real(*a, **k)
    assert pp.show("x") is False and pp.error_code == "unreachable"
    assert calls == ["/v1/message/Live%20Translation/trigger"]


def test_test_caption_during_hold_puts_the_held_caption_back(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live._show("Peace be with you")
    live.hold(True)
    live._show("Collected while held")                                  # not on screen
    live.pp_test_caption(restore_after_s=60)
    assert "Test caption" in mock.visible["Live Translation"]
    live._test_timer.cancel()
    live._restore_after_test()
    assert mock.visible["Live Translation"] == "Peace be with you"     # Hold still in force
    live.hold(False)
    assert "Collected while held" in mock.visible["Live Translation"]


def test_monitor_tick_resends_and_broadcasts(tmp_path, mock):
    live = _live(tmp_path, mock.port)
    live._show("Hallelujah")
    _mock_call(mock, "/mock/clear")
    live._pp_tick(every=0)
    assert mock.visible["Live Translation"] == "Hallelujah"
    assert any(r.get("event") == "pp_health" for r in _log(live))
    live.pause(True)
    _mock_call(mock, "/mock/down")
    live._pp_tick(every=0)
    _mock_call(mock, "/mock/up")
    live._pp_tick(every=0)
    assert mock.visible == {}                                           # Hide stays in force


# --- the HTTP API ---------------------------------------------------------------------------
def test_output_api_end_to_end(tmp_path, mock):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    from test_contract import check, check_state
    live = _live(tmp_path, mock.port)
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}

    def post(path, b=None):
        r = c.post(path, json=b or {}, headers=me)
        return r

    live._show("Peace be with you")
    r = post("/api/pp/test")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["message_found"]
    check(r.json(), C["types"]["pp_test"], "pp/test")
    r = post("/api/pp/test_caption")
    check(r.json(), C["types"]["pp_test_caption"], "pp/test_caption")
    assert r.json()["ok"] and "Test caption" in mock.visible["Live Translation"]
    live._test_timer.cancel()
    live._restore_after_test()
    assert mock.visible["Live Translation"] == "Peace be with you"
    for path, b in (("/api/pause", {"on": True}), ("/api/pause", {"on": False}), ("/api/hold", {"on": True}),
                    ("/api/hold", {"on": False}), ("/api/blank", None),
                    ("/api/layout", {"lines": 3}), ("/api/reconnect", None)):
        r = post(path, b)
        assert r.status_code == 200, (path, r.text)
        check_state(r.json())
    for path, b in (("/api/retract", None), ("/api/correct", {"text": "Nothing to fix"})):   # the box is empty
        r = post(path, b)
        assert r.status_code == 409, (path, r.text)
        check(r.json(), C["types"]["nothing_to_change"], path)
    live._show("Remove me")
    r = post("/api/retract")
    assert r.status_code == 200 and "Remove me" not in r.json()["screen"]
    live._show("Typo here")
    r = post("/api/correct", {"text": "Typo fixed"})
    assert r.status_code == 200 and r.json()["screen"] == "Typo fixed"
    for path, b in (("/api/correct", {}), ("/api/correct", {"text": "  "}), ("/api/layout", {"lines": 99}),
                    ("/api/layout", {"chars": 1.5}), ("/api/pause", {"on": "false"})):
        r = post(path, b)
        assert r.status_code == 400, (path, b)
        check(r.json(), C["types"]["bad_request"], path)
    assert c.post("/api/pause", json={"on": True}, headers={"origin": "https://evil.example"}).status_code == 403
    _mock_call(mock, "/mock/down")
    live.pp.backoff = (0,)
    r = post("/api/pp/test")
    assert r.json()["ok"] is False and r.json()["pp"]["error_code"] == "unreachable"
