"""ProPresenter on/off (Settings → Send captions to ProPresenter) and the captions display,
including the view-only network page."""
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
from live.mock_propresenter import MockState, serve         # noqa: E402
import preflight                                            # noqa: E402


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


@pytest.fixture
def mock():
    port = _port()
    srv, st = serve(port, state=MockState())
    st.port = port
    yield st
    srv.shutdown()
    srv.server_close()


def _live(tmp_path, monkeypatch, pp_port=None, **pp):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["propresenter"].update(port=pp_port or _port(), health_every_s=0, backoff_s=(0,), **pp)
    live = Live(cfg, types.SimpleNamespace(file=None, settings=str(tmp_path / "settings.yaml")))
    live.settings_path = tmp_path / "settings.yaml"
    live.log_path = tmp_path / "log.jsonl"
    return live


def test_turning_propresenter_off_clears_the_big_screen_and_sends_nothing(tmp_path, monkeypatch, mock):
    live = _live(tmp_path, monkeypatch, mock.port)
    live._show("Grace and peace")
    assert mock.visible == {"Live Translation": "Grace and peace"}
    assert live.put_settings({"values": {"propresenter.enabled": False}})[0] == 200
    assert mock.visible == {}                                     # the last caption does not stay up
    n = len(mock.events)
    shown, sent, outcome = live._show("Brothers and sisters")
    assert sent is None and outcome == "ok" and "Brothers" in shown and len(mock.events) == n
    st = live.state()
    assert st["pp"]["enabled"] is False and st["problem"] is None
    assert "turned off" in live.pp_test()["detail"]
    assert live.put_settings({"values": {"propresenter.enabled": True}})[0] == 200
    assert "Brothers and sisters" in mock.visible["Live Translation"]   # back up at once
    assert live.state()["pp"]["enabled"] is True


def test_starting_with_propresenter_off_never_connects(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, enabled=False)
    assert live.pp.status()["enabled"] is False
    live._show("Hello")
    assert live._problem_code("ready") is None
    checks = {c["id"]: c for c in preflight.run(live)["checks"]}
    assert checks["propresenter"]["ok"] and "turned off" in checks["propresenter"]["detail"]
    assert "pp_message" not in checks


def test_display_settings_reach_the_state(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    assert live.state()["display"] == {"size": "large", "lines": 2, "colors": "white_on_black",
                                       "network": False, "show_hidden": False, "url": None, "network_error": None}
    assert live.put_settings({"values": {"display.size": "x-large", "display.lines": 1,
                                         "display.colors": "yellow_on_black"}})[0] == 200
    d = live.state()["display"]
    assert (d["size"], d["lines"], d["colors"]) == ("x-large", 1, "yellow_on_black")
    assert live.put_settings({"values": {"display.lines": 4}})[0] == 400


def test_the_network_page_is_view_only(tmp_path, monkeypatch):
    pytest.importorskip("uvicorn")
    pytest.importorskip("websockets")
    import websockets.sync.client as wsc
    live = _live(tmp_path, monkeypatch)
    port = _port()
    live.cfg.setdefault("display", {})["port"] = port
    try:
        assert live.put_settings({"values": {"display.network": True}})[0] == 200
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                html = urllib.request.urlopen(base + "/", timeout=1).read().decode()
                break
            except OSError:
                time.sleep(0.1)
        assert "<title>" in html and "Captions" in html
        assert live.state()["display"]["url"].endswith(f":{port}/")
        live._show("Peace be with you")
        with wsc.connect(f"ws://127.0.0.1:{port}/ws") as ws:
            feed = json.loads(ws.recv(timeout=3))
        assert set(feed) == {"screen", "paused", "held", "running", "display"}
        assert "Peace be with you" in feed["screen"]
        for method, path in (("POST", "/api/start"), ("GET", "/api/state"), ("POST", "/ws"), ("GET", "/ui/")):
            req = urllib.request.Request(base + path, method=method, data=b"{}" if method == "POST" else None)
            with pytest.raises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(req, timeout=2)
            assert e.value.code in (404, 405), path                # nothing else exists there
        assert live.put_settings({"values": {"display.network": False}})[0] == 200
        assert live.state()["display"]["url"] is None
        time.sleep(0.5)
        with pytest.raises(OSError):
            urllib.request.urlopen(base + "/", timeout=1)
    finally:
        live._net_stop()


def test_a_taken_port_is_reported_not_fatal(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen()
    try:
        live.cfg.setdefault("display", {})["port"] = s.getsockname()[1]
        assert live.put_settings({"values": {"display.network": True}})[0] == 200
        d = live.state()["display"]
        assert d["url"] is None and "in use" in d["network_error"]
    finally:
        s.close()
        live._net_stop()


def test_the_display_page_is_self_contained():
    """The view-only server serves this one file: no other file may be needed."""
    import re
    html = (ROOT / "src" / "live" / "ui" / "display.html").read_text()
    assert not re.search(r'<(script|link|img)[^>]+(src|href)=', html)
    assert "/ws" in html and "requestFullscreen" in html and 'messageHandlers.display' in html
