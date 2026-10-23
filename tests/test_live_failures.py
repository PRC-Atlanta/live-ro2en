"""Failures in the live pipeline must be visible (status, warnings, log records, counts),
never look like a quiet room or a successful caption."""
import json
import pathlib
import queue
import sys
import threading
import time
import types

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from live.app import Live
from live.segmenter import PauseTracker
import live.engine as engmod

CFG = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"


def _live(tmp_path, translate=lambda t, *a, **k: f"<{t}>"):
    live = Live(yaml.safe_load(open(CFG)), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=translate)
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return live


def _clause(i, ago=0.5, **kw):
    """A clause whose speech ended `ago` seconds before now."""
    t = time.time() - ago
    return {"id": i, "ro": "", "multi": "", "en": "", "t_start": t - 1, "t_end": t, "t_emit": t + 0.1,
            "stt_s": 0.1, "audio_s": 1.0, **kw}


def _log(live):
    return [json.loads(l) for l in open(live.log_path)]


class FakeEngine:
    def __init__(self, events, rc):
        self.stdout = iter(json.dumps(e) + "\n" for e in events)
        self.returncode = rc

    def wait(self, *_):
        return self.returncode

    def poll(self):
        return self.returncode


def test_engine_death_is_shown(tmp_path):
    live = _live(tmp_path)
    live.engine = FakeEngine([{"type": "ready", "source": "default"},
                              {"type": "warn", "msg": "multilingual model unavailable: boom"}], rc=1)
    live.running = True
    live._reader(live.engine)
    st = live.state()
    assert not st["running"] and "ENGINE STOPPED (exit 1)" in st["status"]
    assert st["warnings"] == ["multilingual model unavailable: boom"]
    assert [r.get("event") for r in _log(live)] == ["warn", "engine_exit"]


def test_normal_eof_is_not_an_engine_failure(tmp_path):
    live = _live(tmp_path)
    live.engine = FakeEngine([{"type": "ready", "source": "x.wav"}, {"type": "eof"}], rc=0)
    live._reader(live.engine)
    assert live.status == "input ended" and live.eof.is_set()


def test_held_fragment_keeps_english_words(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or "ok")
    live.lang.set_mode("en-ro")
    live._on_clause(_clause(1, en="Good morning church", multi="Good morning church"))   # short: held
    live._on_clause(_clause(2, en="today we read from the Gospel of John.",
                            multi="today we read from the Gospel of John."))
    live._drain()
    assert " ".join(seen) == "Good morning church today we read from the Gospel of John."


def test_failed_and_empty_chunks_are_logged_and_counted(tmp_path):
    def tr(text, *a, **k):
        if "eroare" in text:
            raise RuntimeError("translator crashed")
        return "" if "gol" in text else "Brothers and sisters, today we read."
    live = _live(tmp_path, translate=tr)
    live.lang.set_mode("ro-en")
    (tmp_path / "logs").mkdir()
    import live.app as appmod
    real_root, appmod.ROOT = appmod.ROOT, tmp_path
    try:
        live._handle(_clause(1, ro="Frați și surori, astăzi citim din Romani."))
        live._handle(_clause(2, ro="   "))                                # recognizer gave nothing
        live._handle(_clause(3, ro="un text gol aici acum."))              # translator gave nothing
        live._handle(_clause(4, ro="aici este o eroare mare."))            # translator raised
    finally:
        appmod.ROOT = real_root
    assert [r["outcome"] for r in _log(live)] == ["ok", "empty_stt", "empty_mt", "error"]
    assert live.state()["counts"] == {"ok": 1, "empty_stt": 1, "empty_mt": 1, "error": 1}
    assert len(live.lat) == 1 and live.caps.text == "Brothers and sisters, today we read."


def _bare_engine(mode="auto", streamer=None):
    """An Engine without models: VAD reads the frame's first sample as speech probability."""
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.pt, e.pre_roll, e.partial_every = mode, PauseTracker(frame_ms=32), 8, 0
    e.min_commit = e.max_commit = 0
    e.no_audio_s = e.silent_s = 0                # watchdog off unless a test turns it on
    e.vad = lambda fr: float(fr[0])
    e.streamer, e.wcfg, e._wstream = streamer, {"chunk_s": 0.064}, None
    e.calls = []
    e.transcribe = lambda audio, t0, t1, t_speech=None, final=True: e.calls.append((e.mode, len(audio), final))
    return e


def _frames(q, n, speech, t0=0.0):
    for i in range(n):
        q.put((t0 + i * 0.032, np.full(engmod.FRAME, 1.0 if speech else 0.0, dtype=np.float32)))


def _run(e, q, monkeypatch):
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    e.run(q)
    return out


def test_stop_commits_buffered_speech_then_eof(monkeypatch):
    e = _bare_engine()
    q = queue.Queue()
    _frames(q, 20, speech=True)          # mid-sentence, no pause yet
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert e.calls == [("auto", 20 * engmod.FRAME, True)] and out == [{"type": "eof"}]


def test_mode_switch_commits_speech_under_old_mode(monkeypatch):
    fed = []
    streamer = types.SimpleNamespace(start_stream=lambda cb: None, feed=fed.append, end_stream=lambda: None)
    e = _bare_engine("auto", streamer)
    q = queue.Queue()
    _frames(q, 20, speech=True)          # Romanian half sentence
    q.put({"mode": "en-ro"})             # operator locks EN->RO: streaming takes over
    _frames(q, 10, speech=True, t0=1.0)
    q.put({"mode": "auto"})
    _frames(q, 15, speech=True, t0=2.0)
    q.put(None)
    _run(e, q, monkeypatch)
    # the half sentence was transcribed before the switch and not spliced onto later audio
    assert e.calls == [("auto", 20 * engmod.FRAME, True), ("auto", 15 * engmod.FRAME, True)]
    assert sum(len(f) for f in fed) == 10 * engmod.FRAME


def test_watchdog_reports_dead_and_silent_input(monkeypatch):
    e = _bare_engine()
    e.no_audio_s, e.silent_s = 0.6, 0.3
    q = queue.Queue()

    def source():                        # device delivers nothing for 1.2 s, then digital zeros
        time.sleep(1.2)
        for _ in range(15):
            q.put((time.time(), np.zeros(engmod.FRAME, dtype=np.float32))); time.sleep(0.032)
        _frames(q, 3, speech=True, t0=time.time())       # sound again
        q.put(None)
    threading.Thread(target=source, daemon=True).start()
    out = _run(e, q, monkeypatch)
    audio = [(x["ok"], x["msg"].split(" ")[0]) for x in out if x["type"] == "audio"]
    # nothing arrives -> zeros arrive (still a fault, now named differently) -> sound: recovered
    assert audio == [(False, "no"), (False, "input"), (True, "audio")]


def test_transcribe_error_does_not_kill_engine(monkeypatch):
    e = _bare_engine()
    del e.transcribe                     # use the real wrapper around a failing recognizer
    e._transcribe = lambda *a, **k: 1 / 0
    q = queue.Queue()
    _frames(q, 20, speech=True)
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert [x["type"] for x in out] == ["error", "eof"] and out[0]["where"] == "transcribe"


def test_dead_english_stream_is_reported_and_not_fed():
    from live.whistle_en import WhistleSTT
    w = WhistleSTT.__new__(WhistleSTT)
    w.lock, w.keywords, w._q, w._thread, w.error = threading.Lock(), None, None, None, None

    def stream(chunks, **k):
        next(chunks)
        raise RuntimeError("engine crashed")
        yield
    w.model = types.SimpleNamespace(stream=stream)
    events = []
    w.start_stream(events.append)
    w.feed(np.zeros(160)); w._thread.join(2)
    assert events == [{"error": "RuntimeError: engine crashed"}]
    w.feed(np.zeros(160))
    assert w._q.qsize() == 0             # nothing piles up behind a dead stream
    w.end_stream(wait=1)
    # the engine stops streaming and falls back to the clause path
    e = _bare_engine("en-ro", w)
    assert e._whistle_streaming(0.0, np.zeros(engmod.FRAME, dtype=np.float32), 1.0) is False


def test_stage_success_does_not_hide_caption_error():
    from live.propresenter import ProPresenter
    from live.mock_propresenter import serve
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv, _ = serve(port)
    try:
        pp = ProPresenter(port=port, message_id="Nope", stage_message=True)
        assert not pp.show("x") and "404" in pp.last_error
        assert not pp.clear() and "404" in pp.last_error
    finally:
        srv.shutdown()


def test_clauses_are_shown_in_order(tmp_path):
    def tr(text, *a, **k):
        if text.startswith("Dar"):
            time.sleep(0.3)              # the first (held) fragment translates slowly
        return text
    live = _live(tmp_path, translate=tr)
    live.lang.set_mode("ro-en")
    live.lc["max_chunk_s"] = 2.0
    live._on_clause(_clause(1, ago=4, ro="Dar acum"))                  # short: held
    live._on_clause(_clause(2, ago=0.5, ro="vom citi din Romani capitolul opt."))  # too far: sent apart
    live._drain()
    assert [r["id"] for r in _log(live)] == [1, 2]


def test_lag_and_audio_alerts(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ago=9, ro="Frați și surori, astăzi citim din Romani."))
    assert any(a.startswith("CAPTIONS BEHIND") for a in live.alerts())
    live._handle(_clause(2, ago=0.5, ro="Pavel le scrie romanilor despre har."))
    assert live.alerts() == [] and live.counts["late"] == 1
    live.engine = FakeEngine([{"type": "audio", "ok": False, "msg": "no audio from the input device for 3 s"},
                              {"type": "error", "where": "english stream", "msg": "RuntimeError: x"}], rc=0)
    live._stopping = True
    live._reader(live.engine)
    live.running = True                               # alerts show while the service runs
    assert live.alerts()[0] == "NO AUDIO: no audio from the input device for 3 s"
    assert live.warnings == ["english stream: RuntimeError: x"]
    live.audio_fault = None
    assert live.alerts() == []


def test_control_api_rejects_other_origins():
    from live.app import request_allowed
    hosts, port = {"127.0.0.1", "localhost", "::1"}, 8765
    ok_page = {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765", "content-type": "application/json"}
    assert request_allowed(ok_page, "POST", hosts, port)
    assert request_allowed({"host": "localhost:8765"}, "GET", hosts, port)
    assert request_allowed({"host": "[::1]:8765", "origin": "http://[::1]:8765"}, "WS", hosts, port)
    assert not request_allowed({**ok_page, "origin": "https://evil.example"}, "POST", hosts, port)
    assert not request_allowed({**ok_page, "origin": "http://127.0.0.1:9999"}, "POST", hosts, port)
    assert not request_allowed({"host": "evil.example:8765"}, "GET", hosts, port)        # DNS rebinding
    assert not request_allowed({"host": "127.0.0.1:8765", "origin": "https://evil.example"}, "WS", hosts, port)
    assert not request_allowed({"host": "127.0.0.1:8765", "content-type": "text/plain"}, "POST", hosts, port)


def test_control_api_end_to_end(tmp_path):
    """Runs where FastAPI is installed (.venv-mt): the live page's WebSocket connects, and
    another site can neither drive the API nor open the socket."""
    import pytest
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    live = _live(tmp_path)
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}
    with c.websocket_connect("/ws", headers=me) as ws:
        assert "alerts" in ws.receive_json()
    assert c.post("/api/clear", json={}, headers={"origin": me["origin"]}).status_code == 200
    evil = {"origin": "https://evil.example", "content-type": "text/plain"}
    assert c.post("/api/stop", content="", headers=evil).status_code == 403
    assert c.get("/api/state", headers={"host": "evil.example:8765"}).status_code == 403
    with pytest.raises(Exception):
        with c.websocket_connect("/ws", headers={**me, "origin": "https://evil.example"}) as ws:
            ws.receive_json()
