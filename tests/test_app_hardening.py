"""Request bodies and state providers: the guards behind the Origin check and the /ws loop."""
import pathlib
import sys
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
TestClient = pytest.importorskip("fastapi.testclient").TestClient
from live.app import Live, build_app  # noqa: E402

ME = {"origin": "http://127.0.0.1:8765"}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("LIVE_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LIVE_LOG_DIR", raising=False)
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    calls = []
    live.start = lambda device=None: calls.append(("start", device))
    live.set_profile = lambda name: calls.append(("profile", name))
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    return types.SimpleNamespace(live=live, c=c, calls=calls, home=tmp_path / "home")


@pytest.mark.parametrize("ctype", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"])
def test_a_body_that_is_not_json_is_refused_before_the_handler(app, ctype):
    r = app.c.post("/api/start", content=b'{"device": 3}', headers={**ME, "content-type": ctype})
    assert r.status_code == 415 and app.calls == []
    r = app.c.post("/api/profile", content=b'{"profile": "quality"}', headers={**ME, "content-type": ctype})
    assert r.status_code == 415 and app.calls == []


def test_bad_json_and_non_objects_are_400(app):
    for raw in (b"{nope", b"[3]", b'"fast"'):
        r = app.c.post("/api/start", content=raw, headers={**ME, "content-type": "application/json"})
        assert r.status_code == 400, raw
    assert app.calls == []
    assert app.c.post("/api/start", json={"device": 3}, headers=ME).status_code == 200
    assert app.c.post("/api/start", headers=ME).status_code == 200          # no body at all is fine
    assert app.calls == [("start", 3), ("start", None)]


def test_a_failing_state_provider_is_logged_once_and_skipped(app):
    def broken():
        raise RuntimeError("provider bug")
    app.live.register_state(broken)
    for _ in range(3):
        r = app.c.get("/api/state")
        assert r.status_code == 200 and "running" in r.json() and "phase" in r.json()
    assert app.c.post("/api/clear", headers=ME).status_code == 200
    log = (app.home / "logs" / "app_errors.log").read_text()
    assert log.count("RuntimeError: provider bug") == 1 and "broken" in log


def test_root_opens_the_operator_ui_and_the_ui_is_never_cached(app):
    r = app.c.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/ui/"
    for path in ("/ui/", "/ui/screens/live.js", "/ui/contract.json"):
        r = app.c.get(path)
        assert r.status_code == 200 and r.headers["cache-control"] == "no-cache", path
    assert app.c.get("/legacy").status_code == 404
