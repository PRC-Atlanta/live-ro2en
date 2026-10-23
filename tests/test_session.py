"""S9: preflight, the post-service report (summary, rating, index.csv, exports), diagnostics
and the rehearsal run. Nothing here touches the real Desktop or the repo's logs."""
import asyncio
import csv
import json
import pathlib
import socket
import sys
import time
import types
import zipfile

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live  # noqa: E402
import paths  # noqa: E402
import preflight  # noqa: E402
import report  # noqa: E402
from mock_propresenter import MockState, serve  # noqa: E402

C = json.loads((ROOT / "src" / "live" / "ui" / "contract.json").read_text())
T0 = 1760000000.0                     # a whole second, in the past
ISO = lambda t: __import__("datetime").datetime.fromtimestamp(t).isoformat()
NAME = __import__("datetime").datetime.fromtimestamp(T0).strftime("%Y-%m-%d_%H%M%S") + ".jsonl"


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    """Logs and the Desktop live in tmp; the engine interpreter is this one."""
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LIVE_ENGINE_PYTHON", sys.executable)
    desk = tmp_path / "Desktop"
    monkeypatch.setattr(paths, "desktop", lambda: desk)
    return desk


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _live(tmp_path, pp_port=None):
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    if pp_port:
        cfg["propresenter"]["port"] = pp_port
    cfg["live"]["preflight_min_free_gb"] = 0
    live = Live(cfg, types.SimpleNamespace(file=None))
    live.lc["preflight_min_free_gb"] = 0
    live._models_missing = lambda: []     # phase must not depend on this machine's model cache
    live._models_missing_for = lambda profile: []
    live.tr =types.SimpleNamespace(translate=lambda t, *a, **k: f"EN: {t}")
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return report.attach(live)


def _rec(i, t, fw, outcome="ok", **kw):
    if outcome != "ok":
        return {"id": i, "outcome": outcome, "t_start": t, "t_end": t + 1, "ro": "x", "time": ISO(t)}
    return {"id": i, "outcome": "ok", "t_start": t, "t_end": t + 2, "t_display": t + 3, "lang": "ro",
            "source": f"fraza {i}", "translation": f"phrase {i}", "latency_s": 1.0, "first_word_s": fw,
            "pp_ok": True, **kw}


def _fixture(path: pathlib.Path) -> pathlib.Path:
    recs = [_rec(1, T0 + 1, 2.5), _rec(2, T0 + 5, 4.0), _rec(3, T0 + 9, 7.5, pp_ok=False),
            _rec(4, T0 + 12, 0, "empty_mt"), _rec(5, T0 + 13, 0, "mt_echo"), _rec(6, T0 + 20, 3.0),
            {"event": "warn", "msg": "w", "time": ISO(T0 + 10)},
            {"event": "correct", "text": "fixed", "time": ISO(T0 + 11)}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs) + '{"torn": ')   # killed mid-write
    return path


# --- preflight -------------------------------------------------------------------------------
def test_preflight_passes_against_the_mock(tmp_path):
    port = free_port()
    srv, _ = serve(port)
    try:
        live = _live(tmp_path, port)
        res = preflight.run(live)
    finally:
        srv.shutdown()
    by = {c["id"]: c for c in res["checks"]}
    assert res["ok"], res
    assert {"disk", "logs", "engine", "glossary", "propresenter", "pp_message"} <= set(by)
    assert f"127.0.0.1:{port}" in by["propresenter"]["detail"]
    assert live.state()["preflight"] == res                # the state field the UI shows


def test_preflight_closed_port_fails_with_detail(tmp_path):
    port = free_port()                                     # nothing listens there
    res = preflight.run(_live(tmp_path, port))
    pp = next(c for c in res["checks"] if c["id"] == "propresenter")
    assert not res["ok"] and not pp["ok"] and pp["code"] == "pp_unreachable"
    assert f"127.0.0.1:{port}" in pp["detail"] and "Connection" in pp["detail"]
    assert pp["code"] in C["types"]["problem_code"]["enum"]


def test_preflight_missing_message_and_low_disk(tmp_path):
    port = free_port()
    srv, _ = serve(port, state=MockState(messages=("Sermon Notes",)))
    try:
        live = _live(tmp_path, port)
        live.lc["preflight_min_free_gb"] = 10 ** 9          # no disk is that big
        res = preflight.run(live)
    finally:
        srv.shutdown()
    by = {c["id"]: c for c in res["checks"]}
    assert by["propresenter"]["ok"]
    assert not by["pp_message"]["ok"] and by["pp_message"]["code"] == "pp_message_missing"
    assert '"Sermon Notes"' in by["pp_message"]["detail"]
    assert not by["disk"]["ok"] and by["disk"]["code"] == "disk_full"


def test_preflight_wants_the_text_token_and_the_chosen_quality_s_models(tmp_path):
    port = free_port()
    srv, _ = serve(port, state=MockState(token="Body"))       # the message exists, without a Text token
    try:
        live = _live(tmp_path, port)
        live._models_missing_for = lambda profile: ["org/best"] if profile == "quality" else []
        live.profile = "quality"
        res = preflight.run(live)
    finally:
        srv.shutdown()
    by = {c["id"]: c for c in res["checks"]}
    assert not res["ok"]
    assert not by["pp_message"]["ok"] and '"Text"' in by["pp_message"]["detail"] and "Body" in by["pp_message"]["detail"]
    assert not by["profile_models"]["ok"] and by["profile_models"]["code"] == "models_missing"


def test_preflight_reads_s1_health_from_the_state(tmp_path):
    out = preflight.state_checks({"phase": "needs_setup", "audio": {"ok": False, "device": "Behringer"}})
    by = {c["id"]: c for c in out}
    assert by["models"]["code"] == "models_missing" and by["sound"]["code"] == "no_sound"
    assert preflight.state_checks({}) == []                # fields not there yet: nothing claimed


# --- report ----------------------------------------------------------------------------------
def test_report_summary_counts_and_exports(tmp_path):
    log = _fixture(tmp_path / "logs" / "services" / NAME)
    recs = report.load(log)
    s = report.summary(recs)
    assert s["phrases"] == 4 and s["failed"] == 2
    assert s["outcomes"] == {"ok": 4, "empty_mt": 1, "mt_echo": 1}
    assert s["first_word_ok_pct"] == 75.0                  # 7.5 s is over the 6 s gate
    assert s["pp_errors"] == 1 and s["corrections"] == 1 and s["warnings"] == 1
    assert s["elapsed_s"] == 22.0 and s["kind"] == "service"
    assert s["rating"] == {"score": None, "tags": [], "note": ""}

    srt = report.to_srt(recs, start=T0)
    blocks = srt.strip().split("\n\n")
    assert len(blocks) == 4
    assert blocks[0].splitlines() == ["1", "00:00:04,000 --> 00:00:08,000", "phrase 1"]
    assert blocks[-1].splitlines()[1] == "00:00:23,000 --> 00:00:27,000"
    txt = report.to_txt(recs, start=T0)
    assert "[00:00:01] RO: fraza 1" in txt and "EN: phrase 1" in txt and "fraza 4" not in txt

    desk = tmp_path / "Desktop"
    for fmt in ("txt", "srt", "docx"):
        p = report.export(log, fmt, desk)
        assert p.parent == desk and p.suffix == f".{fmt}" and report._stamp(log) in p.name
    with zipfile.ZipFile(next(desk.glob("*.docx"))) as z:
        doc = z.read("word/document.xml").decode()
        assert "[Content_Types].xml" in z.namelist() and "phrase 6" in doc and "fraza 6" in doc
    assert report.export(log, "txt", desk).name.endswith("(2).txt")    # never overwrites


def test_rating_goes_into_the_log_and_index(tmp_path):
    log = _fixture(tmp_path / "svc" / NAME)
    index = tmp_path / "svc" / "index.csv"
    s = report.rate(log, index, 4, ["names wrong", " "], "  Pastor's name misspelt ")
    assert s["rating"] == {"score": 4, "tags": ["names wrong"], "note": "Pastor's name misspelt"}
    assert report.load(log)[-1]["event"] == "rating"
    report.rate(log, index, 5)                             # a second rating replaces the row
    rows = list(csv.DictReader(open(index)))
    assert len(rows) == 1 and rows[0]["score"] == "5" and rows[0]["phrases"] == "4"
    assert rows[0]["log"] == log.name and rows[0]["first_word_ok_pct"] == "75.0"
    for bad in (0, 6, "5", True, 4.5):
        with pytest.raises(ValueError):
            report.rate(log, index, bad)


def test_find_log_stays_in_the_services_folder(tmp_path):
    svc = tmp_path / "svc"
    a = _fixture(svc / "2025-01-01_090000.jsonl")
    b = _fixture(svc / NAME)
    assert report.find_log(svc, None) == b
    assert report.find_log(svc, a.name) == a
    assert report.find_log(svc, None, current=a) == a
    for bad in ("../config.yaml", "/etc/passwd", "x/../../y.jsonl", "nope.jsonl"):
        assert report.find_log(svc, bad) is None


def test_state_fields_elapsed_and_first_word(tmp_path):
    live = _live(tmp_path)
    st = live.state()
    assert st["elapsed_s"] is None and st["first_word_ok_pct"] is None and st["preflight"] is None
    svc = paths.services_dir(live.lc["transcript_log_dir"], live.root)
    live.log_path = _fixture(svc / NAME)
    st = live.state()
    assert st["first_word_ok_pct"] == 75.0 and st["elapsed_s"] == 23.0     # from the start in the name
    with open(live.log_path, "a") as f:                                    # finish the torn line, add one
        f.write('}\n' + json.dumps(_rec(7, T0 + 30, 9.0)) + "\n")
    assert live.state()["first_word_ok_pct"] == 60.0                       # read incrementally
    live.running = True
    assert live.state()["elapsed_s"] > 1000                                # running: up to now
    live.running = False
    live.state()                                                           # the run ended
    rows = list(csv.DictReader(open(svc / "index.csv")))
    assert [r["log"] for r in rows] == [NAME]
    for k in ("elapsed_s", "first_word_ok_pct", "preflight"):
        f = C["state"][k]
        assert f["owner"] == "S9"


# --- diagnostics -----------------------------------------------------------------------------
def test_diagnostics_zip_has_logs_but_no_secrets(tmp_path):
    logs = tmp_path / "logs"
    svc = logs / "services"
    _fixture(svc / NAME)
    (logs / "engine.log").write_text("engine started\n")
    (logs / "app_errors.log").write_text("trace\n")
    for name in (".env", "google.json.key", "my-service-account.json", "cert.pem", "creds.json"):
        (logs / name).write_text("GOOGLE_API_KEY=abc123\n")
    (logs / "leak.json").write_text('{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----"}')
    home = tmp_path / "home"
    home.mkdir()
    (home / "settings.yaml").write_text("live:\n  profile: fast\n  gemini_api_key: sk-123\npassword: hunter2\n"
                                        "propresenter:\n  token_name: Text\n")
    (home / ".env").write_text("KEY=1")
    out = report.diagnostics(tmp_path / "Desktop", logs, svc, [home / "settings.yaml", home / ".env"],
                             {"status": "idle"})
    assert out.parent == tmp_path / "Desktop" and out.suffix == ".zip"
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {"logs/engine.log", "logs/app_errors.log", f"logs/services/{NAME}",
                "settings/settings.yaml", "system.json", "state.json"} <= names
        assert not any(n.rsplit("/", 1)[-1].startswith(".env") or n.endswith((".key", ".pem")) or
                       "service-account" in n or "creds" in n or "leak" in n for n in names), names
        s = z.read("settings/settings.yaml").decode()
        assert "sk-123" not in s and "hunter2" not in s and "profile: fast" in s and "token_name: Text" in s
        assert not any(b"abc123" in z.read(n) for n in names)


# --- routes ----------------------------------------------------------------------------------
def _call(app, method, path, body=None):
    """One request straight through the ASGI app (no httpx in the test venv)."""
    raw = b"" if body is None else json.dumps(body).encode()
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path,
             "raw_path": path.encode(), "query_string": b"", "root_path": "", "scheme": "http",
             "server": ("127.0.0.1", 8765), "client": ("127.0.0.1", 50000),
             "headers": [(b"host", b"127.0.0.1:8765"), (b"origin", b"http://127.0.0.1:8765"),
                         (b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]}
    sent, msgs = [], [{"type": "http.request", "body": raw, "more_body": False}]

    async def receive():
        return msgs.pop(0) if msgs else {"type": "http.disconnect"}

    async def send(m):
        sent.append(m)
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(data) if data else None


def test_session_routes(tmp_path, sandbox):
    pytest.importorskip("fastapi")
    from live.app import build_app
    port = free_port()
    live = _live(tmp_path, port)
    app = build_app(live)
    st, res = _call(app, "POST", "/api/preflight", {})
    assert st == 200 and not res["ok"] and res["checks"]                  # nothing on that port
    st, res = _call(app, "GET", "/api/state")
    assert res["preflight"]["checks"] and "elapsed_s" in res and "first_word_ok_pct" in res

    assert _call(app, "POST", "/api/export", {"format": "txt"})[0] == 404  # no service yet
    svc = paths.services_dir(live.lc["transcript_log_dir"], live.root)
    log = _fixture(svc / NAME)
    st, res = _call(app, "POST", "/api/export", {"format": "srt"})
    assert st == 200 and pathlib.Path(res["path"]).parent == sandbox and res["log"] == log.name
    assert _call(app, "POST", "/api/export", {"format": "pdf"})[0] == 400
    assert _call(app, "POST", "/api/export", {"format": "txt", "log": "../../config.yaml"})[0] == 404

    st, res = _call(app, "POST", "/api/rate", {"score": 3, "tags": ["too slow"], "note": "ok"})
    assert st == 200 and res["summary"]["rating"]["score"] == 3
    assert (svc / "index.csv").is_file()
    assert _call(app, "POST", "/api/rate", {"score": 9})[0] == 400

    st, res = _call(app, "POST", "/api/diagnostics", {})
    assert st == 200 and pathlib.Path(res["path"]).parent == sandbox
    with zipfile.ZipFile(res["path"]) as z:
        assert not any(n.endswith(".env") for n in z.namelist())

    assert _call(app, "POST", "/api/rehearsal", {"sample": "../../config.yaml"})[0] == 404
    live.running = True
    assert _call(app, "POST", "/api/rehearsal", {})[0] == 409
    live.running = False


def test_export_and_diagnostics_fall_back_when_the_desktop_cannot_be_written(tmp_path, sandbox):
    pytest.importorskip("fastapi")
    from live.app import build_app
    live = _live(tmp_path)
    app = build_app(live)
    _fixture(paths.services_dir(live.lc["transcript_log_dir"], live.root) / NAME)
    sandbox.write_text("not a folder")                      # like macOS refusing the Desktop
    alt = paths.log_dir(live.root) / "exports"
    st, res = _call(app, "POST", "/api/export", {"format": "docx"})
    assert st == 200 and pathlib.Path(res["path"]).parent == alt
    st, res = _call(app, "POST", "/api/diagnostics", {})
    assert st == 200 and pathlib.Path(res["path"]).parent == alt
    for p in alt.iterdir():
        p.unlink()
    alt.rmdir()
    alt.write_text("also not a folder")                     # nowhere to write: a clear refusal, not a 500
    st, res = _call(app, "POST", "/api/export", {"format": "txt"})
    assert st == 507 and res["error"] == "write_failed" and res["detail"].endswith(".")


def test_rehearsal_plays_the_file_once_then_stops(tmp_path, monkeypatch):
    samples = tmp_path / "data" / "samples"
    samples.mkdir(parents=True)
    (samples / "sermon.wav").write_bytes(b"")
    (samples / "notes.txt").write_text("not audio")
    monkeypatch.setattr(paths, "samples_dir", lambda root=None: samples)
    assert [p.name for p in preflight.samples(None)] == ["sermon.wav"]
    assert preflight.find_sample(None, "sermon") == samples / "sermon.wav"
    assert preflight.find_sample(None, "notes.txt") is None

    live = _live(tmp_path)
    seen, stops = [], []

    def start(device=None):
        seen.append(live.args.file)
        live.log_path = tmp_path / "r.jsonl"
        live.running = True
    monkeypatch.setattr(live, "start", start)
    monkeypatch.setattr(live, "stop", lambda: (stops.append(1), setattr(live, "running", False)))
    live.lc["hold_s"] = 0
    th = preflight.rehearse(live, samples / "sermon.wav", timeout_s=30)
    assert seen == [str(samples / "sermon.wav")] and live.args.file is None   # the next Start uses the mic
    first = report.load(tmp_path / "r.jsonl")[0]
    assert first["event"] == "rehearsal" and first["sample"] == "sermon.wav"
    live.eof.set()
    th.join(5)
    assert stops == [1] and not th.is_alive()
    assert report.summary(report.load(tmp_path / "r.jsonl"))["kind"] == "rehearsal"


def test_docx_survives_control_characters_and_csv_notes_cannot_be_formulas(tmp_path):
    import xml.dom.minidom
    import io as _io
    recs = [_rec(1, T0 + 1, 2.0, source="frați\x0bsurori", translation="brothers \x01and sisters\x1f")]
    with zipfile.ZipFile(_io.BytesIO(report.to_docx(recs, T0, "t"))) as z:
        xml.dom.minidom.parseString(z.read("word/document.xml"))         # Word would refuse a bad one
    assert report._cell("=HYPERLINK(\"http://x\")").startswith("'=") and report._cell("@SUM").startswith("'")
    assert report._cell("fine, thanks") == "fine, thanks"
