"""src/live/ui/contract.json is what the UI, the Mac shell and the parallel sessions build
against: the backend must match it, and nothing may exist that it does not list."""
import json
import pathlib
import re
import sys
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live  # noqa: E402

C = json.loads((ROOT / "src" / "live" / "ui" / "contract.json").read_text())


def check(v, t, where="$"):
    """Raise AssertionError unless value v has contract type t."""
    if isinstance(t, str):
        if t.startswith("@"):
            return check(v, C["types"][t[1:]], where)
        alts = t.rstrip("?").split("|")
        ok = {"bool": lambda x: isinstance(x, bool), "str": lambda x: isinstance(x, str),
              "int": lambda x: isinstance(x, int) and not isinstance(x, bool),
              "num": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
              "null": lambda x: x is None}
        assert any(ok[a](v) for a in alts), f"{where}: {v!r} is not {t}"
    elif "enum" in t:
        assert v in t["enum"], f"{where}: {v!r} not in {t['enum']}"
    elif "list" in t:
        assert isinstance(v, list), f"{where}: not a list"
        for i, x in enumerate(v):
            check(x, t["list"], f"{where}[{i}]")
    elif "map" in t:
        assert isinstance(v, dict), f"{where}: not a map"
        for k, x in v.items():
            check(x, t["map"], f"{where}.{k}")
    elif "object" in t:
        assert isinstance(v, dict), f"{where}: not an object"
        for k, ft in t["object"].items():
            if isinstance(ft, str) and ft.endswith("?") and k not in v:
                continue
            assert k in v, f"{where}: missing {k}"
            check(v[k], ft, f"{where}.{k}")
    else:
        raise AssertionError(f"{where}: unknown contract type {t}")


def check_state(st):
    assert set(st) <= set(C["state"]), f"state fields missing from contract.json: {set(st) - set(C['state'])}"
    for k, f in C["state"].items():
        if f["built"]:
            assert k in st, f"built state field {k} is missing"
        if k in st and not (f.get("nullable") and st[k] is None):
            check(st[k], f["type"], f"state.{k}")


def _live(tmp_path):
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: f"EN: {t}")
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return live


def test_state_matches_contract(tmp_path):
    live = _live(tmp_path)
    check_state(live.state())
    live.lang.set_mode("ro-en")
    now = __import__("time").time()
    live._handle({"id": 1, "ro": "Frați și surori, astăzi citim din Romani.", "multi": "", "en": "",
                  "t_start": now - 2, "t_end": now - 1, "t_emit": now - 0.9, "stt_s": 0.1, "audio_s": 1.0})
    check_state(live.state())                  # with a line, latencies and counts filled in


def test_registered_state_fields_are_merged(tmp_path):
    live = _live(tmp_path)
    live.register_state(lambda: {"paused": False})
    assert live.state()["paused"] is False


def _routes(routes):
    """Every route, looking inside included routers (newer FastAPI keeps them nested)."""
    for r in routes:
        sub = getattr(r, "original_router", None)
        yield from _routes(sub.routes) if sub is not None else [r]


def test_endpoints_match_contract(tmp_path):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    app = build_app(_live(tmp_path))
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    listed = {(e["method"], e["path"]) for e in C["endpoints"]}
    served = set()
    for r in _routes(app.routes):
        if type(r).__name__ == "WebSocketRoute":
            served.add(("WS", r.path))
        elif type(r).__name__ == "Mount":
            served.add(("GET", r.path + "/"))
        elif not r.path.startswith(("/docs", "/redoc", "/openapi")):
            served |= {(m, r.path) for m in r.methods - {"HEAD"}}
    assert served == listed, f"served but not listed: {served - listed}; listed but not served: {listed - served}"
    for e in C["endpoints"]:
        m, p = e["method"], e["path"]
        if m == "WS":
            with c.websocket_connect(p, headers={"host": "127.0.0.1:8765", **me}) as ws:
                check_state(ws.receive_json())
            continue
        if e["built"] and (m != "POST" or p in ("/api/clear",)) and p != "/api/devices":
            r = c.request(m, p, json={} if m != "GET" else None, headers=me)
            assert r.status_code == 200, (p, r.status_code)
            if e.get("response") == "html":
                assert "<html" in r.text.lower() or "<!doctype" in r.text.lower(), p
            elif e.get("response") == "@state":
                check_state(r.json())
        elif not e["built"]:
            r = c.request(m, p, json={}, headers=me)
            assert r.status_code == 501, (p, r.status_code)
            check(r.json(), C["types"]["not_built"], p)
            assert r.json()["owner"] == e["owner"]


def test_engine_events_and_commands_match_contract():
    src = (ROOT / "src" / "live" / "engine.py").read_text()
    emitted = set(re.findall(r'emit\(\{"type": "(\w+)"', src))
    built = {k for k, v in C["engine"]["events"].items() if v["built"]}
    assert emitted == built, f"emitted {emitted ^ built} differ from contract.json"
    handled = set(re.findall(r'c\.get\("cmd"\) == "(\w+)"', src))
    assert handled == {k for k, v in C["engine"]["commands"].items() if v["built"]}


def test_every_problem_code_and_flag_is_documented():
    doc = (ROOT / "docs" / "app_contract.md").read_text()
    for code in C["types"]["problem_code"]["enum"]:
        assert f"`{code}`" in doc, code
    app_src = (ROOT / "src" / "live" / "app.py").read_text()
    for flag, f in C["flags"].items():
        assert (f'"{flag}"' in app_src) == f["built"], flag
    # and the other way: every flag app.py defines is in the contract
    defined = set(re.findall(r'add_argument\("(--[\w-]+)"', app_src))
    assert defined <= set(C["flags"]), f"flags missing from contract.json: {defined - set(C['flags'])}"
    for var in C["env"]:
        assert f"`{var}`" in doc, var


def test_live_venv_can_serve_the_websocket():
    """uvicorn without websockets/wsproto answers every /ws upgrade with 'Unsupported upgrade
    request', so the control page never gets live updates (TestClient does not catch it)."""
    reqs = (ROOT / "requirements" / "mt.txt").read_text().split()
    assert any(r.startswith(("websockets==", "wsproto==")) for r in reqs)
