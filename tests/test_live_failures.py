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


def _live(tmp_path, translate=lambda t, *a, **k: f"EN: {t}"):
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
    live.lc["engine_restarts"] = 0         # restarts used up (tests/test_lifecycle.py: the restart)
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
    live.caps.flush()                                                  # the rest is revealed at speaking pace
    assert len(live.lat) == 1 and live.caps.text == "Brothers and sisters, today we read"   # the period waits for a pause


def _bare_engine(mode="auto"):
    """An Engine without models: VAD reads the frame's first sample as speech probability."""
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.pt, e.pre_roll, e.partial_every = mode, PauseTracker(frame_ms=32), 8, 0
    e.min_commit = e.max_commit = 0
    e.no_audio_s = e.silent_s = 0                # watchdog off unless a test turns it on
    e.realtime, e.backlog_s, e.catchup_max, e.multi = False, 0, 0, object()
    e.n, e._id_lock = 0, threading.Lock()
    e.vad = lambda fr: float(fr[0])
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
    e = _bare_engine("auto")
    q = queue.Queue()
    _frames(q, 20, speech=True)          # Romanian half sentence
    q.put({"mode": "en-ro"})             # operator locks EN->RO
    _frames(q, 10, speech=True, t0=1.0)
    q.put({"mode": "auto"})
    _frames(q, 15, speech=True, t0=2.0)
    q.put(None)
    _run(e, q, monkeypatch)
    # each part was transcribed under its own mode, never spliced onto later audio
    assert e.calls == [("auto", 20 * engmod.FRAME, True), ("en-ro", 10 * engmod.FRAME, True),
                       ("auto", 15 * engmod.FRAME, True)]


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
    live.lc["phrase_min_words"] = 0                                     # the short-fragment hold alone
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
                              {"type": "error", "where": "engine", "msg": "RuntimeError: x"}], rc=0)
    live._stopping = True
    live._reader(live.engine)
    live.running = True                               # alerts show while the service runs
    assert live.alerts()[0] == "NO AUDIO: no audio from the input device for 3 s"
    assert live.warnings == ["engine: RuntimeError: x"]
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


def test_backlog_is_merged_into_one_translation(tmp_path):
    calls = []
    live = _live(tmp_path, translate=lambda t, *a, **k: calls.append(t) or "Translated text, chapter 8.")
    live.lang.set_mode("ro-en")
    texts = ["Frați și surori, astăzi", "citim din epistola către Romani,", "capitolul opt,", "versetul unu."]
    for i, t in enumerate(texts[1:], 2):
        live._work.put(_clause(i, ago=6, ro=t))
    ev, n = live._catch_up(_clause(1, ago=7, ro=texts[0]))
    assert n == 4 and ev["caught_up"] == 4 and live._work.qsize() == 0
    live._handle(ev)
    assert calls == [" ".join(texts)]                       # one call for the whole backlog
    assert _log(live)[0]["caught_up"] == 4 and live.counts["caught_up"] == 4
    on_time, n = live._catch_up(_clause(9, ago=0.5, ro="Pavel scrie."))
    assert n == 1 and "caught_up" not in on_time            # not behind: clause by clause


def test_echoed_and_cut_off_translations(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: t)     # returns the source unchanged
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="Frați și surori, astăzi citim din Romani."))
    live._handle(_clause(2, ro="Amin."))                        # short: may legitimately match
    assert [r["outcome"] for r in _log(live)] == ["mt_echo", "ok"]
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "Brothers and sisters, today we",
                                    last_truncated=True)
    live._handle(_clause(3, ro="Frați și surori, astăzi citim din Romani."))
    assert _log(live)[-1]["mt_truncated"] is True and live.counts["mt_truncated"] == 1


def test_glossary_alternatives_and_optional_article():
    from live.translate import Glossary
    g = Glossary(CFG.parent / "glossary.csv")
    assert any(r["ro"] == "Paște" for r in g.relevant("Today we celebrate Easter together", "en"))
    assert any(r["ro"] == "Paște" for r in g.relevant("They kept the Passover", "en"))
    assert any(r["en"] == "the Holy Spirit" for r in g.relevant("filled with Holy Spirit", "en"))
    n, ok = g.adherence("Astăzi sărbătorim Paștele, Paște fericit", "Today we celebrate Easter", "ro")
    assert n >= 1 and ok == n


def test_llm_context_carries_over_a_language_switch(monkeypatch):
    import live.translate as T
    prompts = []
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(
        generate=lambda model, tok, prompt, **k: prompts.append(prompt) or "out"))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=lambda **k: None))
    tr = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    tr.model, tr.max_tokens, tr.context, tr.context_clauses = None, 200, [], 2
    tr.tok = types.SimpleNamespace(apply_chat_template=lambda m, **k: m[-1]["content"], encode=lambda s: s.split())
    for t in ("Unu doi trei.", "Patru cinci.", "Șase șapte."):
        tr.translate(t, "ro", "en", use_context=True)
    assert len(tr.context) == 2 and "Patru cinci." in prompts[-1]       # bounded, same language
    tr.translate("And now in English.", "en", "ro", use_context=True)
    assert "Romanian: Șase șapte." in prompts[-1]                        # the last pieces carry over
    assert tr.context[-1] == ("And now in English.", "out", "en") and len(tr.context) == 2
    tr.translate("Fără context.", "ro", "en")
    assert "Previous pieces" not in prompts[-1] and tr.context == []      # use_context off: none


def test_skipped_pause_commits_once_long_enough(monkeypatch):
    e = _bare_engine()
    e.min_commit = 40                               # quality profile: at least 40 frames per commit
    q = queue.Queue()
    _frames(q, 20, speech=True)                     # a short sentence...
    _frames(q, 60, speech=False, t0=1.0)            # ...then a long silence, no more speech
    q.put("CHECK")                                  # marker: what was committed before eof?
    q.put(None)
    seen = []
    real_get = q.get
    def get(*a, **k):
        item = real_get(*a, **k)
        if item == "CHECK":
            seen.append(list(e.calls)); item = real_get(*a, **k)
        return item
    q.get = get
    _run(e, q, monkeypatch)
    assert len(seen[0]) == 1                        # committed in the silence, not held until eof


def test_backlog_is_reported_and_recognised_in_fewer_calls(monkeypatch):
    e = _bare_engine()
    e.realtime, e.backlog_s, e.catchup_max = True, 1.0, 400
    q = queue.Queue()
    old = time.time() - 10                          # everything queued was captured 10 s ago
    for k in range(3):                              # three sentences with pauses between them
        _frames(q, 20, speech=True, t0=old + k)
        _frames(q, 15, speech=False, t0=old + k + 0.6)
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert {"type": "backlog", "ok": False} .items() <= next(x for x in out if x["type"] == "backlog").items()
    assert len(e.calls) == 1                        # one longer call instead of three


def test_no_multilingual_model_means_romanian_only(monkeypatch, tmp_path):
    e = _bare_engine("ro-en")
    e.multi = None                       # Parakeet did not load: no English, no auto
    q = queue.Queue()
    q.put({"mode": "auto"}); q.put(None)
    out = _run(e, q, monkeypatch)
    assert e.mode == "ro-en" and out[0]["type"] == "mode" and out[0]["mode"] == "ro-en"
    live = _live(tmp_path)
    live.lang.set_mode("auto")
    live.engine = FakeEngine([out[0], {"type": "backlog", "ok": False, "s": 5.0,
                                       "msg": "speech recognition is 5 s behind the audio"}], rc=0)
    live._stopping = True
    live._reader(live.engine)
    live.running = True
    assert live.lang.mode == "ro-en" and "RO→EN only" in live.warnings[0]
    assert live.alerts()[0].startswith("RECOGNITION BEHIND")


def test_e2e_gate():
    from live.e2e import gate
    a = types.SimpleNamespace(max_failed_frac=0.15, min_first_word_6s=0.8, ref_text=None, max_wer=0.15)
    good = {"eof_reached": True, "engine_exit": 0, "clauses": 19, "failed_chunks": 1, "frac_first_word_le_6s": 0.9}
    assert gate(good, [], a) == {"pass": True, "fail_reasons": []}
    bad = {**good, "eof_reached": False, "failed_chunks": 9, "frac_first_word_le_6s": 0.5}
    r = gate(bad, [], a)
    assert not r["pass"] and len(r["fail_reasons"]) == 3


def test_setup_downloads_are_pinned():
    root = CFG.parent
    lock = [l.split() for l in open(root / "requirements" / "models.lock.txt") if l.strip() and not l.startswith("#")]
    hf = {l[1] for l in lock if l[0] == "hf"}
    cfg = yaml.safe_load(open(CFG))
    specs = {cfg["live"]["translator"]} | {p.get(k) or "" for p in cfg["profiles"].values()
                                           for k in ("translator", "translator_en_ro")}
    needed = {s.split(":", 1)[1] for s in specs if s.startswith("mlx:")} | \
        {"facebook/" + s.split(":", 1)[1] for s in specs if s.startswith("nllb:")} | \
        {"surogate/jackrabbit-110m-ro", "mlx-community/parakeet-tdt-0.6b-v3"}
    assert needed <= hf
    assert all(len(l[2]) in (40, 64) for l in lock if l[0] in ("hf", "dataset", "sha256"))
    setup = (root / "setup.sh").read_text()
    assert "/master/" not in setup and "check models/vad/silero_vad.onnx" in setup


def test_double_start_loads_and_spawns_once(tmp_path):
    live = _live(tmp_path)
    calls = {"load": 0, "engine": 0}
    def slow_load():
        calls["load"] += 1; time.sleep(0.3)
    live.load_translator = slow_load
    live._start_engine = lambda device=None: calls.__setitem__("engine", calls["engine"] + 1)
    ts = [threading.Thread(target=live.start) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert calls == {"load": 1, "engine": 1} and live.running


def test_profile_switch_waits_for_the_clause_being_translated(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: time.sleep(0.4) or "Brothers and sisters, we read.")
    live.lang.set_mode("ro-en")
    live.lc["translator"] = "nllb:other"                  # the quality profile uses another translator
    t = threading.Thread(target=live._handle, args=(_clause(1, ro="Frați și surori, astăzi citim."),))
    t.start(); time.sleep(0.1)
    other = next(p for p in live.cfg["profiles"] if p != live.profile)
    live.set_profile(other)                              # not running: swaps settings, drops tr
    t.join()
    assert _log(live)[0]["outcome"] == "ok" and live.tr is None


def test_stale_hold_timer_does_not_flush_the_next_fragment(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or "x y z w")
    live.lang.set_mode("ro-en")
    live._on_clause(_clause(1, ro="Dar acum"))              # held, timer generation g
    stale = live._hold_gen
    live._on_clause(_clause(2, ro="vom"))                   # merged, still short: re-held, newer generation
    live._flush(stale)                                      # the old timer fires late
    live._drain()
    assert seen == [] and live._pending["ro"] == "Dar acum vom"
    live._timer.cancel()


def test_backlog_parts_and_merged_timing():
    from live.app import merge_clauses, pack_parts
    parts = ["unu doi trei.", "patru cinci.", "șase șapte opt nouă zece.", "unsprezece."]
    assert pack_parts(parts, 5) == ["unu doi trei. patru cinci.", "șase șapte opt nouă zece.", "unsprezece."]
    a, b = _clause(1, ago=4), _clause(2, ago=1)
    m = merge_clauses({**a, "stt_s": 0.5}, {**b, "stt_s": 0.3})
    assert m["stt_s"] == 0.3 and m["stt_s_total"] == 0.8 and m["t_emit"] - m["t_end"] - m["stt_s"] >= -0.25


def test_english_quote_in_ro_en_is_shown_not_dropped(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: t)       # the model leaves English as it is
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="For God so loved the world, that he gave his only Son"))
    rec = _log(live)[0]
    assert rec["outcome"] == "ok" and rec["passthrough"] is True and live.counts["passthrough"] == 1


def test_catch_up_is_not_cut_by_the_short_phrase_force(monkeypatch):
    e = _bare_engine()
    e.pt = PauseTracker(frame_ms=32, max_clause_s=0.5)          # would force-cut every 0.5 s
    e.realtime, e.backlog_s, e.catchup_max = True, 1.0, 400
    q = queue.Queue()
    _frames(q, 60, speech=True, t0=time.time() - 10)           # 1.9 s of unbroken, late speech
    _frames(q, 15, speech=False, t0=time.time() - 8)
    q.put(None)
    _run(e, q, monkeypatch)
    assert len(e.calls) == 1                                    # one call, not four 0.5 s pieces


def test_mic_is_reopened_after_a_failure(monkeypatch):
    opens = []

    class Stream:
        def __init__(self, callback, **k):
            self.cb = callback
            opens.append(1)
            if len(opens) == 1:
                raise RuntimeError("device unavailable")
        def __enter__(self):
            def feed():
                for _ in range(5):
                    self.cb(np.zeros((engmod.FRAME, 1), dtype=np.float32), engmod.FRAME, None, None)
                    time.sleep(0.01)
            threading.Thread(target=feed, daemon=True).start()
            return self
        def __exit__(self, *a):
            return False
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(InputStream=Stream))
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    q, stop = queue.Queue(), threading.Event()
    th = threading.Thread(target=engmod.mic_source, args=(None, q, stop), kwargs={"retry_s": (0.05,)}, daemon=True)
    th.start(); time.sleep(0.4); stop.set(); th.join(2)
    assert out[0]["type"] == "error" and "reopening" in out[0]["msg"]
    assert {"type": "warn", "msg": "audio input reopened"} in out and q.qsize() >= 5


def test_pinned_refs_let_offline_loading_find_the_model(tmp_path):
    from huggingface_hub import try_to_load_from_cache
    sys.path.insert(0, str(CFG.parent / "src"))
    import model_lock
    lock = tmp_path / "lock.txt"
    lock.write_text("hf org/model " + "a" * 40 + "\n")
    snap = tmp_path / "models--org--model" / "snapshots" / ("a" * 40)
    snap.mkdir(parents=True); (snap / "config.json").write_text("{}")
    # downloaded by commit hash: no refs/main, so a revision-less (offline runtime) lookup misses
    assert try_to_load_from_cache("org/model", "config.json", cache_dir=tmp_path) is None
    monkeypatch_lock = model_lock.LOCK
    model_lock.LOCK = lock
    try:
        assert model_lock.pin_all(tmp_path) == ["org/model"]
    finally:
        model_lock.LOCK = monkeypatch_lock
    assert try_to_load_from_cache("org/model", "config.json", cache_dir=tmp_path) == str(snap / "config.json")


def test_control_api_https_proxy_and_lan_host():
    from live.app import request_allowed
    hosts, port = {"127.0.0.1", "localhost", "::1", "church-mac.local"}, 8765
    lan = {"host": "church-mac.local", "origin": "https://church-mac.local", "content-type": "application/json"}
    assert request_allowed(lan, "POST", hosts, port)                         # listed host behind a proxy
    assert not request_allowed({**lan, "origin": "https://evil.example"}, "POST", hosts, port)
    assert not request_allowed({"host": "127.0.0.1:8765", "origin": "https://127.0.0.1"}, "POST", hosts, port)


def test_replugged_mic_is_found_again_by_name(monkeypatch):
    devices = [{"name": "Built-in Mic", "max_input_channels": 1}, {"name": "USB Interface", "max_input_channels": 2}]
    opened, inits = [], []

    class Stream:
        def __init__(self, callback, device=None, **k):
            opened.append(device); self.cb = callback
            if len(opened) == 1:
                raise RuntimeError("device lost")              # unplugged
        def __enter__(self):
            threading.Thread(target=lambda: [self.cb(np.zeros((engmod.FRAME, 1), np.float32), 0, None, None)
                                             for _ in range(3)], daemon=True).start()
            return self
        def __exit__(self, *a):
            return False

    def reinit():                                            # replugged: PortAudio renumbers devices
        inits.append(1); devices.insert(0, {"name": "Virtual Out", "max_input_channels": 0})
    sd = types.SimpleNamespace(InputStream=Stream, _terminate=lambda: None, _initialize=reinit,
                               query_devices=lambda d=None: devices if d is None else devices[d])
    monkeypatch.setitem(sys.modules, "sounddevice", sd)
    monkeypatch.setattr(engmod, "emit", lambda ev: None)
    q, stop = queue.Queue(), threading.Event()
    th = threading.Thread(target=engmod.mic_source, args=("1", q, stop), kwargs={"retry_s": (0.05,)}, daemon=True)
    th.start(); time.sleep(0.3); stop.set(); th.join(2)
    assert opened[:2] == [1, 2] and inits == [1]               # re-initialised, same interface by name


def test_recurring_faults_do_not_flood_warnings(tmp_path):
    live = _live(tmp_path)
    evs = [{"type": "error", "where": "audio input", "msg": f"PortAudioError: lost ({k})"} for k in range(50)]
    evs += [{"type": "warn", "msg": "audio input reopened"}]
    evs += [{"type": "error", "where": "engine", "msg": "RuntimeError: x"}] * 5
    evs += [{"type": "warn", "msg": f"note {k}"} for k in range(30)]
    live.engine = FakeEngine(evs, rc=0)
    live._stopping = True
    live._reader(live.engine)
    assert live.audio_fault is None                          # cleared when the device came back
    assert len(live.warnings) == 20 and live.warnings.count("engine: RuntimeError: x") <= 1
    assert not any("audio input" in w for w in live.warnings)


def test_old_reader_does_not_touch_a_new_run(tmp_path):
    live = _live(tmp_path)
    old = FakeEngine([{"type": "ready", "source": "x"}], rc=1)
    live.engine, live.running = old, True
    new = FakeEngine([], rc=None)
    real_drain = live._drain
    def drain_while_new_run_starts():
        real_drain(); live.engine = new                       # Start happened during the drain
    live._drain = drain_while_new_run_starts
    live._reader(old)
    assert live.running and "ENGINE STOPPED" not in live.status


def test_empty_ro_transcript_falls_back_to_the_multilingual_one(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="", multi="neprihănirea."))          # RO model heard nothing
    live._handle(_clause(2, ro="", multi="and then"))               # not Romanian: still dropped
    assert [r["outcome"] for r in _log(live)] == ["ok", "empty_stt"]
    assert "EN: neprihănirea" in live.caps.text


def test_each_phrase_logs_the_input_gain_and_level(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc["input_gain_db"] = 22
    ev = _clause(1, ro="Frați și surori, astăzi citim din Romani.", ago=2.0)
    t0, t1 = ev["t_start"], ev["t_end"]
    for i in range(10):                                  # 10 readings over the phrase, 10 Hz
        live._level_hist.append((t0 + i * 0.1, -40.0 if i % 2 else -60.0, -30.0, bool(i % 2)))
    live._level_hist.append((t1 + 5, -10.0, -1.0, True))  # after the phrase: not counted
    live._handle(ev)
    live._handle(_clause(2, ro="   "))                   # nothing heard: still logged, no readings
    rec, empty = _log(live)
    assert rec["gain_db"] == 22.0 and empty["gain_db"] == 22.0
    assert rec["level"] == {"speech_db": -40.0, "all_db": -43.0, "peak_db": -30.0, "n": 10}
    assert empty["level"] is None
    live._on_level({"rms_db": -50.0, "peak_db": -45.0, "speech": True})   # the engine's level events feed it
    assert live._level_hist[-1][1:] == (-50.0, -45.0, True)
