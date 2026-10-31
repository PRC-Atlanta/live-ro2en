"""The UI's mock backend (src/live/ui/mock_state.js mockApi) must answer exactly as the real
backend does: every response it gives, success or refusal, has the type contract.json lists for
that endpoint (checked with test_contract's validator), and the settings it offers are the ones
src/live/settings.py exposes. The Setup, Settings and After screens are built and tested against
this mock, so a drift here is a screen that breaks on the real app."""
import json
import pathlib
import re
import shutil
import subprocess

import pytest

from test_contract import C, check, check_state

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "live" / "ui"
NODE = shutil.which("node")

# [scenario, method, path, body]: every S5b call, its refusals, and planned endpoints
CALLS = [
    ["fresh_install", "GET", "setup", None],
    ["fresh_install", "POST", "setup/download", {"profile": "quality"}],
    ["fresh_install", "+3000"],
    ["fresh_install", "GET", "setup", None],
    ["fresh_install", "POST", "setup/cancel", {}],
    ["fresh_install", "POST", "setup/download", {}],
    ["fresh_install", "POST", "setup/download", {"profile": "best"}],
    ["fresh_install", "+10000"],
    ["fresh_install", "GET", "setup", None],
    ["fresh_install", "GET", "permissions", None],
    ["fresh_install", "POST", "preload", {}],
    ["fresh_install", "GET", "devices", None],
    ["fresh_install", "POST", "rate", {"score": 5, "tags": [], "note": ""}],
    ["fresh_install", "POST", "export", {"format": "txt"}],
    ["setup_small_mac", "GET", "setup", None],
    ["setup_pp_down", "POST", "pp/test", {}],
    ["setup_pp_down", "POST", "pp/test_caption", {}],
    ["settings", "GET", "settings", None],
    ["settings", "PUT", "settings", {"values": {"propresenter.max_lines": 3, "live.input_device": "Behringer UMC204HD"}}],
    ["settings", "PUT", "settings", {"live.autostart": True}],
    ["settings", "PUT", "settings", {"values": {"live.translator": "x"}}],
    ["settings", "PUT", "settings", {"live": {"autostart": True}}],
    ["settings", "PUT", "settings", {"values": {"propresenter.port": "abc", "propresenter.host": "a b"}}],
    ["settings", "PUT", "settings", {"reset": True}],
    ["settings", "PUT", "settings", {"values": {"propresenter.message_id": "Captions"}}],
    ["settings", "POST", "pp/test", {}],
    ["settings", "POST", "pp/test_caption", {}],
    ["settings", "PUT", "settings", {"values": {"propresenter.message_id": "Live Translation"}}],
    ["settings", "POST", "pp/test", {}],
    ["settings", "POST", "pp/test_caption", {}],
    ["settings", "GET", "keywords", None],
    ["settings", "PUT", "keywords", {"user": ["Ionuț Pop", "Ionuț Pop", "Pastor Brânzei"]}],
    ["settings", "PUT", "keywords", {"user": "Ionuț"}],
    ["settings", "GET", "blocklist", None],
    ["settings", "PUT", "blocklist", {"user": ["some word", "some word"]}],
    ["settings", "PUT", "blocklist", {"user": "word"}],
    ["settings", "GET", "spellings", None],
    ["settings", "PUT", "spellings", {"user": {"Iisus": "Isus"}}],
    ["settings", "PUT", "spellings", {"user": {"Iisus": ""}}],
    ["settings", "GET", "terms", None],
    ["settings", "POST", "terms", {"lines": ["Ionut Pop = Ionuț Pop", "Filadelfia"], "save": False}],
    ["settings", "POST", "terms", {"lines": 3}],
    ["settings", "GET", "terms", None],
    ["settings", "POST", "mode", {"mode": "en-ro"}],
    ["settings", "PUT", "settings", {"values": {"display.remote": True}}],
    ["settings", "POST", "remote/pin", {}],
    ["settings_live", "PUT", "settings", {"values": {"live.vad_threshold": 0.65}}],
    ["settings_live", "POST", "setup/download", {"profile": "fast"}],
    ["after", "POST", "rate", {"score": 4, "tags": ["Too slow"], "note": "fine"}],
    ["after", "POST", "rate", {"score": 0}],
    ["after", "POST", "export", {"format": "docx"}],
    ["after", "POST", "export", {"format": "pdf"}],
    ["after", "POST", "unload", {}],
    ["live", "POST", "start", {}],
    ["live", "POST", "pause", {"on": True}],
    ["live", "POST", "pause", {"on": "yes"}],
    ["live", "POST", "hold", {"on": False}],
    ["live", "POST", "retract", {}],
    ["live", "POST", "correct", {"text": "Grace and peace"}],
    ["live", "POST", "correct", {}],
    ["live", "POST", "profile", {"profile": "quality"}],
    ["live", "POST", "rehearsal", {}],
    ["live", "POST", "stop", {}],
    ["live", "POST", "blank", {}],
    ["live", "POST", "diagnostics", {}],
    ["settings", "POST", "retract", {}],
]


def _endpoint(method, path):
    return next((e for e in C["endpoints"] if e["method"] == method and e["path"] == f"/api/{path}"), None)


@pytest.fixture(scope="module")
def answers():
    if not NODE:
        pytest.skip("node is not installed")
    script = """
      const { mockBackend: B, mockApi } = await import("./mock_state.js");
      const calls = JSON.parse(process.argv[1]);
      const mem = new Map();
      const storage = { getItem: k => mem.get(k) ?? null, setItem: (k, v) => mem.set(k, String(v)), removeItem: k => mem.delete(k) };
      let clock = 1e6, name = null;
      B.delayMs = 0; B.now = () => clock;
      const out = [];
      for (const [scenario, method, path, body] of calls) {
        if (scenario !== name) { B.use(scenario, storage, true); name = scenario; }
        if (method.startsWith("+")) { clock += Number(method.slice(1)); continue; }
        const r = await mockApi(method, path, body ?? undefined);
        out.push({ scenario, method, path, body, status: r.status, data: r.data });
      }
      console.log(JSON.stringify(out));"""
    flat = [c if len(c) == 4 else [c[0], c[1], None, None] for c in CALLS]
    r = subprocess.run([NODE, "--input-type=module", "-e", script, json.dumps(flat)], cwd=UI,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr or r.stdout
    return json.loads(r.stdout)


def test_every_mock_answer_matches_the_contract(answers):
    for a in answers:
        where = f"{a['scenario']}: {a['method']} /api/{a['path']} {json.dumps(a['body'], ensure_ascii=False)}"
        e = _endpoint(a["method"], a["path"])
        assert e is not None, f"{where}: not in contract.json"
        if a["status"] == 501:
            assert not e["built"], f"{where}: the real endpoint is built, the mock says 501"
            check(a["data"], C["types"]["not_built"], where)
            assert a["data"]["owner"] == e["owner"], where
        elif a["status"] == 200:
            assert e["built"], f"{where}: planned endpoints answer 501"
            if e["response"] == "@state":
                check_state(a["data"])
            else:
                check(a["data"], e["response"], where)
        elif a["status"] == 500:
            assert a["path"] == "mode" and a["data"] is None, where    # the route reads body["mode"]
        else:
            types = e.get("errors", {}).get(str(a["status"]))
            assert types, f"{where}: contract.json lists no {a['status']} answer"
            errs = []
            for t in types:
                try:
                    check(a["data"], t, where)
                    break
                except AssertionError as err:
                    errs.append(str(err))
            else:
                raise AssertionError(f"{where}: {a['data']} matches none of {types}: {errs}")


def test_the_mock_answers_like_the_backend_where_it_matters(answers):
    by = {}
    for a in answers:
        by.setdefault((a["scenario"], a["method"], a["path"]), []).append(a)
    dl = by[("fresh_install", "POST", "setup/cancel")][0]["data"]["download"]
    assert dl["state"] == "cancelled" and not dl["running"] and dl["pct"] > 0
    resumed = by[("fresh_install", "POST", "setup/download")][1]["data"]["download"]
    assert resumed["state"] == "running" and resumed["pct"] >= dl["pct"], "a new download resumes the partial files"
    assert by[("fresh_install", "GET", "setup")][-1]["data"]["profiles"]["quality"]["ready"]
    puts = by[("settings", "PUT", "settings")]
    assert [p["status"] for p in puts] == [200, 200, 400, 400, 400, 200, 200, 200, 200]
    assert puts[2]["data"] == {"error": "rejected", "keys": ["live.translator"], "detail": "these settings cannot be changed here"}
    assert puts[3]["data"]["keys"] == ["live"], "nested {live: {...}} is not the API's shape"
    assert set(puts[4]["data"]["detail"]) == {"propresenter.port", "propresenter.host"}
    assert puts[0]["data"]["applies"] == {"propresenter.max_lines": "now", "live.input_device": "start"}
    assert puts[0]["data"]["restart_required"] is False
    assert by[("settings_live", "PUT", "settings")][0]["data"]["restart_required"] is True
    assert by[("settings_live", "POST", "setup/download")][0]["status"] == 409
    tests = by[("settings", "POST", "pp/test")]
    assert tests[0]["data"]["ok"] is False and tests[0]["data"]["message_found"] is False
    assert tests[1]["data"]["ok"] is True and tests[1]["data"]["message_found"] is True
    assert by[("setup_pp_down", "POST", "pp/test")][0]["data"]["pp"]["error_code"] == "unreachable"
    kw = by[("settings", "PUT", "keywords")][0]["data"]
    assert kw["user"] == ["Ionuț Pop", "Pastor Brânzei"] and kw["bundled"]
    assert by[("settings", "GET", "terms")][1]["data"] == {"lines": ["Ionut Pop = Ionuț Pop", "Filadelfia"], "n": 2}
    assert by[("settings", "POST", "terms")][0]["data"]["session_terms_n"] == 2
    rm = by[("settings", "POST", "remote/pin")][0]["data"]["remote"]
    assert rm["enabled"] is True and len(rm["pin"]) == 6 and rm["pin"].isdigit()


def test_the_real_backend_answers_with_the_same_types(tmp_path, monkeypatch):
    """The other half: the real routes give these types too (GETs and refusals; nothing saved)."""
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    from test_contract import _live
    live = _live(tmp_path)
    live.settings_path = tmp_path / "settings.yaml"
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    for path in ("settings", "keywords", "spellings", "terms", "setup"):
        r = c.get(f"/api/{path}")
        assert r.status_code == 200, path
        check(r.json(), _endpoint("GET", path)["response"], path)
    for body, t in (({"values": {"live.translator": "x"}}, "settings_rejected"),
                    ({"live": {"autostart": True}}, "settings_rejected"),
                    ({"values": {"propresenter.max_lines": 9}}, "settings_invalid")):
        r = c.put("/api/settings", json=body, headers=me)
        assert r.status_code == 400, body
        check(r.json(), C["types"][t], json.dumps(body))
    r = c.put("/api/keywords", json={"user": "x"}, headers=me)
    assert r.status_code == 400
    check(r.json(), C["types"]["settings_invalid"], "keywords")
    r = c.post("/api/setup/download", json={"profile": "best"}, headers=me)
    assert r.status_code == 400
    check(r.json(), C["types"]["bad_profile"], "setup/download")
    r = c.post("/api/export", json={"format": "pdf"}, headers=me)
    assert r.status_code == 400
    check(r.json(), C["types"]["bad_request"], "export")


def test_the_mock_offers_the_settings_the_backend_exposes():
    import settings as real
    src = (UI / "mock_state.js").read_text()
    block = src[src.index("const MOCK_FIELDS = {"):src.index("const MOCK_DEFAULTS")]
    mock = {k: (g, t, a) for k, g, t, a in re.findall(
        r'"([\w.]+)": \{ group: "(\w+)", type: "(\w+)",.*?apply: "(\w+)"', block)}
    assert mock == {k: (f["group"], f["type"], f["apply"]) for k, f in real.FIELDS.items()}
    for k, f in real.FIELDS.items():
        for lim in ("min", "max"):
            if lim in f:
                assert re.search(rf'"{re.escape(k)}": \{{[^}}]*\b{lim}: {f[lim]:g}\b', block), (k, lim)


def test_every_call_the_screens_make_is_in_the_contract_and_the_mock():
    calls = set()
    for p in [*(UI / "screens").rglob("*.js")]:
        calls |= set(re.findall(r'call\("(GET|PUT|POST)", "([\w/]+)"', p.read_text()))
    calls |= {("POST", "setup/download"), ("POST", "setup/cancel")}     # setup's act(path)
    exercised = {(c[1], c[2]) for c in CALLS if len(c) == 4}
    for m, p in sorted(calls):
        assert _endpoint(m, p), f"{m} /api/{p} is not in contract.json"
        assert (m, p) in exercised, f"{m} /api/{p} is not covered by this test"
