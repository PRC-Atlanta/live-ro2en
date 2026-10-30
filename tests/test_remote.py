"""Live Remote (remote.py): the PIN-protected /remote/* routes on the captions display's
network server, the PIN, the lockout, and the page staying hidden with only the remote on."""
import json
import os
import pathlib
import sys
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live                                   # noqa: E402
import display                                              # noqa: E402
import remote                                               # noqa: E402

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient                   # noqa: E402
from starlette.websockets import WebSocketDisconnect        # noqa: E402


def _live(tmp_path, monkeypatch, **disp):
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LIVE_USER_DIR", str(tmp_path / "user"))
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["propresenter"].update(health_every_s=0)
    cfg.setdefault("display", {}).update(disp)
    live = Live(cfg, types.SimpleNamespace(file=None, settings=str(tmp_path / "settings.yaml")))
    live.settings_path = tmp_path / "settings.yaml"
    live.log_path = tmp_path / "log.jsonl"
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return live


BASE = "http://127.0.0.1:8766"                               # a Host the server answers to


def _client(live, ip="192.168.1.30"):
    return TestClient(display.network_app(live), base_url=BASE, client=(ip, 50000))


@pytest.fixture
def rig(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, remote=True, network=False)
    return live, _client(live), {"X-Live-Pin": live.remote_pin()}


def test_the_pin_is_six_digits_kept_and_renewed(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, remote=True)
    pin = live.remote_pin()
    assert len(pin) == 6 and pin.isdigit()
    f = tmp_path / "user" / "remote_pin"
    assert f.read_text().strip() == pin and (f.stat().st_mode & 0o777) == 0o600
    again = _live(tmp_path, monkeypatch, remote=True)
    assert again.remote_pin() == pin                           # the same after a restart
    assert live.state()["remote"] == {"enabled": True, "pin": pin, "clients": 0}
    monkeypatch.setattr(remote, "new_pin", lambda: "654321" if pin != "654321" else "123456")
    new = live.renew_remote_pin()
    assert new != pin and f.read_text().strip() == new and live.remote_pin() == new
    off = _live(tmp_path, monkeypatch, remote=False)
    assert off.state()["remote"] == {"enabled": False, "pin": None, "clients": 0}


def test_every_route_needs_the_pin(rig):
    live, client, _ = rig
    for method, path in (("GET", "/remote/state"), ("POST", "/remote/pause"), ("POST", "/remote/blank"),
                         ("POST", "/remote/retract"), ("GET", "/remote/terms"), ("POST", "/remote/terms")):
        assert client.request(method, path, json={}).status_code == 401, path
        assert client.request(method, path, json={}, headers={"X-Live-Pin": "000000x"}).status_code == 401
        live._remote_fails.clear()                             # not the lockout's test
    assert live.paused is False


def test_browsers_and_foreign_host_names_are_refused(rig):
    """A web page (it always sends Origin cross-site) or a DNS-rebinding name gets 403, even
    with the right PIN; Live Remote sends neither."""
    live, client, pin = rig
    assert client.post("/remote/pause", json={"on": True}, headers={**pin, "Origin": "http://evil.example"}).status_code == 403
    assert client.get("/remote/state", headers={**pin, "Host": "attacker.example"}).status_code == 403
    for host in ("192.168.1.20:8766", "[fe80::1]:8766", "localhost:8766", f"{__import__('socket').gethostname()}:8766"):
        assert client.get("/remote/state", headers={**pin, "Host": host}).status_code == 200, host
    assert live.paused is False


def test_hide_hold_song_clear_and_remove_last(rig):
    live, client, pin = rig
    r = client.post("/remote/pause", json={"on": True}, headers=pin)
    assert r.status_code == 200 and r.json()["paused"] is True and live.paused
    assert client.post("/remote/pause", json={"on": False}, headers=pin).json()["paused"] is False
    assert client.post("/remote/hold", json={"on": True}, headers=pin).json()["held"] is True
    client.post("/remote/hold", json={"on": False}, headers=pin)
    assert client.post("/remote/song", json={"on": True}, headers=pin).json()["song"] is True
    client.post("/remote/song", json={"on": False}, headers=pin)
    assert client.post("/remote/pause", json={"on": "yes"}, headers=pin).status_code == 400
    live._show("Grace and peace to you")
    assert "Grace" in client.get("/remote/state", headers=pin).json()["screen"]
    assert client.post("/remote/retract", headers=pin).status_code == 200
    assert client.post("/remote/retract", headers=pin).status_code == 409   # nothing left to remove
    live._show("Brothers and sisters")
    assert client.post("/remote/blank", headers=pin).json()["screen"] == ""


def test_the_feed_has_what_the_toolbar_shows(rig):
    live, client, pin = rig
    feed = client.get("/remote/state", headers=pin).json()
    assert set(feed) == {"name", "phase", "running", "paused", "held", "song", "screen", "lag_s", "terms_n", "pp"}
    assert set(feed["pp"]) == {"enabled", "connected"}
    assert "pin" not in json.dumps(feed)


def test_names_and_words_go_to_today_and_can_be_kept(rig):
    live, client, pin = rig
    r = client.post("/remote/terms", json={"lines": ["Ionuț Pop", "Romani = Romans"]}, headers=pin)
    assert r.status_code == 200 and r.json()["n"] == 2
    assert client.get("/remote/terms", headers=pin).json()["lines"] == ["Ionuț Pop", "Romani = Romans"]
    assert client.get("/remote/state", headers=pin).json()["terms_n"] == 2
    assert live.user_keywords() == []                          # not kept unless asked
    client.post("/remote/terms", json={"lines": ["Ionuț Pop"], "save": True}, headers=pin)
    assert "Ionuț Pop" in live.user_keywords()
    assert client.post("/remote/terms", json={"lines": "x" * 3}, headers=pin).status_code == 200
    assert client.post("/remote/terms", json={"lines": [1, 2]}, headers=pin).status_code == 400
    assert client.post("/remote/terms", content=b"lines=a", headers={**pin, "content-type": "text/plain"}).status_code == 415


def test_too_many_wrong_pins_lock_that_client_out(rig):
    live, client, pin = rig
    wrong = {"X-Live-Pin": "999999" if pin["X-Live-Pin"] != "999999" else "111111"}
    for _ in range(remote.MAX_FAILS):
        assert client.get("/remote/state", headers=wrong).status_code == 401
    assert client.get("/remote/state", headers=pin).status_code == 429      # even the right PIN, for a minute
    assert _client(live, "192.168.1.40").get("/remote/state", headers=pin).status_code == 200   # not anyone else
    ip = "192.168.1.30"
    live._remote_fails[ip] = [t - remote.FAIL_WINDOW_S for t in live._remote_fails[ip]]
    assert client.get("/remote/state", headers=pin).status_code == 200


def test_nobody_can_lock_the_remote_out_without_guessing_pins(rig):
    """No PIN, or a browser's request: refused, but never counted towards the lockout."""
    live, client, pin = rig
    for _ in range(remote.MAX_FAILS * 3):
        assert client.get("/remote/state").status_code == 401
        assert client.post("/remote/pause", content=b"x", headers={"Origin": "http://evil.example",
                                                                   "content-type": "text/plain"}).status_code == 403
    assert client.get("/remote/state", headers=pin).status_code == 200


def test_a_new_pin_ends_a_lockout(rig):
    live, client, pin = rig
    for _ in range(remote.MAX_FAILS):
        client.get("/remote/state", headers={"X-Live-Pin": "000000" if pin["X-Live-Pin"] != "000000" else "111111"})
    assert client.get("/remote/state", headers=pin).status_code == 429
    new = live.renew_remote_pin()
    assert client.get("/remote/state", headers={"X-Live-Pin": new}).status_code == 200


def test_the_pin_is_not_in_the_diagnostics(tmp_path, rig):
    import zipfile
    import report
    live, _, pin = rig
    out = report.diagnostics(tmp_path, tmp_path / "logs", tmp_path / "services", [], live.state(), {})
    with zipfile.ZipFile(out) as z:
        st = json.loads(z.read("state.json"))
    assert st["remote"]["pin"] == "<redacted>" and pin["X-Live-Pin"] not in json.dumps(st)


def test_remote_off_answers_404_and_the_page_needs_network(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, remote=False, network=True)
    client = _client(live)
    assert client.get("/remote/state", headers={"X-Live-Pin": live.remote_pin()}).status_code == 404
    assert client.get("/").status_code == 200
    only_remote = _live(tmp_path, monkeypatch, remote=True, network=False)
    client = _client(only_remote)
    assert client.get("/").status_code == 404                  # the remote alone does not publish the page
    assert only_remote.state()["display"]["url"] is None


def test_the_websocket_feed_counts_clients_and_drops_an_old_pin(rig):
    live, client, pin = rig
    with client.websocket_connect("ws://127.0.0.1:8766/remote/ws", headers=pin) as ws:
        first = json.loads(ws.receive_text())
        assert first["paused"] is False
        assert live.state()["remote"]["clients"] == 1
        live.pause(True)
        assert json.loads(ws.receive_text())["paused"] is True
        live.renew_remote_pin()
        with pytest.raises(WebSocketDisconnect) as e:          # closed: the PIN it used is gone
            for _ in range(10):
                ws.receive_text()
        assert e.value.code == 4401
    assert live.state()["remote"]["clients"] == 0
    with client.websocket_connect("ws://127.0.0.1:8766/remote/ws", headers={"X-Live-Pin": "nope"}) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
        assert e.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as e:              # a browser's WebSocket: refused
        with client.websocket_connect("ws://127.0.0.1:8766/remote/ws", headers={"X-Live-Pin": live.remote_pin(), "Origin": "http://evil.example"}) as ws:
            ws.receive_text()
    assert e.value.code == 4403


def test_turning_the_remote_on_starts_the_server_without_the_page(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch)
    started = []
    monkeypatch.setattr(live, "_net_start", lambda: (started.append(1), setattr(live, "_net_server", object())))
    monkeypatch.setattr(live, "_net_stop", lambda: setattr(live, "_net_server", None))
    monkeypatch.setattr(live, "_bonjour_sync", lambda: None)
    assert live.put_settings({"values": {"display.remote": True}})[0] == 200
    assert started == [1] and live._net_server is not None
    assert live.put_settings({"values": {"display.network": True}})[0] == 200
    assert started == [1]                                      # one server for both
    assert live.put_settings({"values": {"display.remote": False}})[0] == 200
    assert live._net_server is not None                        # the page still needs it
    assert live.put_settings({"values": {"display.network": False}})[0] == 200
    assert live._net_server is None


class _FakeProc:
    def __init__(self, *a, **k):
        self.args, self.pid, self.code = a[0], 4242, None
        _FakeProc.made.append(self)

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        self.code = 0

    def terminate(self):
        self.code = -15

    kill = terminate


def test_bonjour_follows_the_remote_and_comes_back_if_dns_sd_ends(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, remote=True)
    _FakeProc.made = []
    monkeypatch.setattr(remote.sys, "platform", "darwin")
    monkeypatch.setattr(remote.shutil, "which", lambda n: "/usr/bin/dns-sd")
    monkeypatch.setattr(remote.subprocess, "Popen", _FakeProc)
    killed = []
    monkeypatch.setattr(remote.os, "killpg", lambda pid, sig: (killed.append(pid), setattr(_FakeProc.made[-1], "code", -15)))
    live._net_server, live._net_error, live._net_host = object(), None, "church-mac.local"
    live._bonjour_sync()
    assert len(_FakeProc.made) == 1
    args = _FakeProc.made[0].args
    assert args[:2] == ["/bin/sh", "-c"] and "Live RO2EN (church-mac)" in args and str(os.getpid()) in args
    live._bonjour_sync()
    assert len(_FakeProc.made) == 1                            # idempotent
    _FakeProc.made[0].code = 1                                 # dns-sd quit by itself
    live._bonjour_sync()
    assert len(_FakeProc.made) == 2
    live.cfg["display"]["remote"] = False
    live._bonjour_sync()
    assert killed == [4242] and live._bonjour is None
    live.cfg["display"]["remote"] = True
    live._bonjour_sync()
    live.shutdown()
    assert live._bonjour is None                               # the app quitting ends the advert


def test_many_addresses_guessing_hit_the_hourly_cap_but_not_the_real_remote(rig):
    live, client, pin = rig
    assert client.get("/remote/state", headers=pin).status_code == 200      # the real remote, .30
    wrong = {"X-Live-Pin": "999999" if pin["X-Live-Pin"] != "999999" else "111111"}
    for i in range(remote.GLOBAL_FAILS):
        assert _client(live, f"10.0.0.{i + 1}").get("/remote/state", headers=wrong).status_code == 401
    assert _client(live, "10.0.1.1").get("/remote/state", headers=pin).status_code == 429   # even with the PIN
    assert client.get("/remote/state", headers=pin).status_code == 200      # the remote that knew it carries on
    live.renew_remote_pin()
    assert _client(live, "10.0.1.1").get("/remote/state", headers={"X-Live-Pin": live.remote_pin()}).status_code == 200


def test_the_bonjour_watcher_restarts_dns_sd_and_ends_with_its_parent(tmp_path):
    """WATCH for real, with a fake dns-sd that quits after half a second."""
    import subprocess
    import time
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "runs"
    (bindir / "dns-sd").write_text(f"#!/bin/sh\necho \"$*\" >> {log}\nsleep 0.5\n")
    (bindir / "dns-sd").chmod(0o755)
    parent = subprocess.Popen(["sleep", "30"])
    env = {**os.environ, "PATH": f"{bindir}:/usr/bin:/bin", "LIVE_BONJOUR_TICK_S": "0.2"}
    w = subprocess.Popen(["/bin/sh", "-c", remote.WATCH, "sh", "Live RO2EN (test)", "18766", str(parent.pid)], env=env)
    try:
        time.sleep(2.5)
        runs = log.read_text().splitlines()
        assert len(runs) >= 2 and runs[0] == "-R Live RO2EN (test) _liveroen._tcp local 18766 v=1"
        parent.kill()
        parent.wait()
        assert w.wait(5) == 0                                  # gone with its parent
    finally:
        parent.kill()
        w.kill()


# --- full view (/remote/app/): the Live screen in Live Remote's window ----------------------
@pytest.fixture
def full(rig):
    live, client, _ = rig
    client.cookies.set(remote.PIN_COOKIE, live.remote_pin())
    return live, client


def test_full_view_serves_the_live_screen_with_the_pin_cookie_only(rig):
    live, client, pin = rig
    assert client.get("/remote/app/ui/").status_code == 401                  # no cookie
    assert client.get("/remote/app/ui/", headers=pin).status_code == 401     # the header is not the cookie
    client.cookies.set(remote.PIN_COOKIE, "000000" if live.remote_pin() != "000000" else "111111")
    assert client.get("/remote/app/ui/").status_code == 401
    client.cookies.set(remote.PIN_COOKIE, live.remote_pin())
    r = client.get("/remote/app/ui/")
    assert r.status_code == 200 and "<main id=\"screen\">" in r.text
    assert client.get("/remote/app/ui/core/api.js").status_code == 200
    assert client.get("/remote/app/", follow_redirects=False).headers["location"] == "/remote/app/ui/#live"


def test_full_view_takes_its_own_origin_and_refuses_others(full):
    live, client = full
    own = {"Origin": BASE}
    assert client.post("/remote/app/api/pause", json={"on": True}, headers=own).status_code == 200
    assert live.paused
    for bad in ("http://evil.example", "http://127.0.0.1:8765", "https://127.0.0.1:8766", "null"):
        assert client.post("/remote/app/api/pause", json={"on": False}, headers={"Origin": bad}).status_code == 403, bad
    assert live.paused
    assert client.get("/remote/app/ui/", headers={"Host": "rebind.example:8766"}).status_code == 403


def test_full_view_does_only_what_the_live_screen_does(full):
    live, client = full
    own = {"Origin": BASE}
    for method, path in [("POST", "/api/shutdown"), ("POST", "/api/unload"), ("POST", "/api/rehearsal"),
                         ("GET", "/api/settings"), ("POST", "/api/import_config"), ("POST", "/api/remote/pin"),
                         ("PUT", "/api/keywords"), ("GET", "/api/setup"), ("POST", "/api/export")]:
        r = client.request(method, "/remote/app" + path, json={}, headers=own)
        assert r.status_code == 403, (method, path, r.status_code)
    assert client.get("/remote/app/api/terms").status_code == 200
    assert client.post("/remote/app/api/blank", json={}, headers=own).status_code == 200


def test_full_view_settings_are_the_gain_and_propresenter_switch_only(full):
    live, client = full
    own = {"Origin": BASE}
    r = client.put("/remote/app/api/settings", json={"values": {"live.input_gain_db": 6}}, headers=own)
    assert r.status_code == 200 and live.cfg["live"]["input_gain_db"] == 6
    for values in ({"live.profile": "quality"}, {"live.input_gain_db": 3, "display.remote": False}, {}, None):
        r = client.put("/remote/app/api/settings", json={"values": values}, headers=own)
        assert r.status_code == 403, values
    assert live.cfg["live"]["input_gain_db"] == 6 and live.remote_enabled()


def test_full_view_feed_sends_the_state_and_ends_with_a_new_pin(full):
    live, client = full
    with client.websocket_connect("ws://127.0.0.1:8766/remote/app/ws", headers={"Origin": BASE}) as ws:
        s = ws.receive_json()
        assert "phase" in s and s["remote"]["clients"] >= 1
        live.renew_remote_pin()
        with pytest.raises(WebSocketDisconnect) as e:
            for _ in range(10):
                ws.receive_json()
        assert e.value.code == 4401
    client.cookies.set(remote.PIN_COOKIE, "")
    with client.websocket_connect("ws://127.0.0.1:8766/remote/app/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_full_view_is_gone_with_the_remote_off(tmp_path, monkeypatch):
    live = _live(tmp_path, monkeypatch, remote=False, network=True)
    client = _client(live)
    client.cookies.set(remote.PIN_COOKIE, live.remote_pin())
    assert client.get("/remote/app/ui/").status_code == 404
    assert client.post("/remote/app/api/start", json={}).status_code == 404
