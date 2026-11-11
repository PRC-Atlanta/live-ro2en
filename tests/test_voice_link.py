"""Live Voice M2/M5: the orchestrator's side (src/live/voicelink.py): start, the state it keeps the
voice in, summary requests, restart on death and on a hang, what the operator sees (plan §3, §5)."""
import json
import pathlib
import sys
import time
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import voicelink                                         # noqa: E402  (the module live.app itself imports)
from live.app import Live                                # noqa: E402
from test_contract import C, check_state                 # noqa: E402


class PP:
    last_error = None

    def show(self, text):
        return True

    def clear(self):
        return True


@pytest.fixture
def make(tmp_path, monkeypatch):
    lives = []
    monkeypatch.setattr(voicelink, "VOICE_SCRIPT", ROOT / "tests" / "fake_voice.py")
    monkeypatch.setenv("LIVE_VOICE_PYTHON", sys.executable)
    monkeypatch.setenv("LIVE_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("FAKE_VOICE_OUT", str(tmp_path / "got.jsonl"))
    monkeypatch.setattr(voicelink, "STATE_EVERY_S", 0.1)

    def _make(enabled=True, model=True, **voice):
        cfg = yaml.safe_load(open(ROOT / "config.yaml"))
        cfg["voice"] = {**cfg["voice"], "enabled": enabled, **voice}
        live = Live(cfg, types.SimpleNamespace(file=None))
        live.log_path = tmp_path / "log.jsonl"
        live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "Tradus: " + t)
        live.pp = PP()
        live.voice_model_ready = lambda: model
        lives.append(live)
        return live
    _make.got = lambda: [json.loads(l) for l in open(tmp_path / "got.jsonl")] if (tmp_path / "got.jsonl").exists() else []
    yield _make
    for live in lives:
        live._shutting_down = True
        live.voice_stop()


def _until(fn, s=8.0):
    end = time.time() + s
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.03)
    return fn()


def test_off_by_default_and_nothing_starts(make):
    live = make(enabled=False)
    assert live.voice_start() is False and live.voice is None
    st = live.state()
    check_state(st)
    assert st["voice"]["state"] == "off" and st["voice"]["url"] is None and st["problem"] is None
    assert yaml.safe_load(open(ROOT / "defaults.yaml"))["voice"]["enabled"] is False


def test_start_ready_state_every_second_and_what_the_operator_sees(make):
    live = make()
    assert live.voice_start()
    assert _until(lambda: live._voice_ready)
    assert _until(lambda: live.state()["voice"]["listeners"] == {"ro": 2, "en": 1})
    st = live.state()
    check_state(st)
    assert st["voice"]["state"] == "ready" and st["voice"]["enabled"] and st["voice"]["port"] == 18767
    got = _until(lambda: [m for m in make.got() if m["type"] == "state"][3:] and make.got())
    argv = next(m for m in got if m["type"] == "argv")["argv"]
    cfg = json.loads(argv[argv.index("--cfg") + 1])
    assert cfg["port"] == 8767 and cfg["voices"] == {"ro": "M2", "en": "M2"} and cfg["limit_s"] == 10.0
    assert "--log" in argv and "/voice/" in argv[argv.index("--log") + 1]
    state = next(m for m in got if m["type"] == "state")
    assert set(state) == {"type", "seq", "paused", "song", "held", "running", "blank_n", "profile", "summarizer"}
    seqs = [m["seq"] for m in make.got() if m["type"] == "state"]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)       # numbered, in order
    assert any(m["type"] == "config" for m in got)           # sent again after ready
    assert live.voice_start()                                # idempotent: still one process
    assert len([m for m in make.got() if m["type"] == "argv"]) == 1


def test_operator_state_reaches_the_voice_at_once(make):
    live = make()
    live.voice_start()
    _until(lambda: live._voice_ready)
    live.pause(True)
    assert _until(lambda: any(m["type"] == "state" and m["paused"] for m in make.got()), 1.0)
    live.pause(False)
    live.blank()
    assert _until(lambda: any(m["type"] == "state" and m["blank_n"] == 1 and not m["paused"] for m in make.got()), 1.0)
    live.song(True)
    assert _until(lambda: any(m["type"] == "state" and m["song"] for m in make.got()), 1.0)
    live.song(False)
    live.hold(True)
    assert _until(lambda: any(m["type"] == "state" and m["held"] and not m["song"] for m in make.got()), 1.0)


def test_it_is_restarted_when_it_dies_three_times_then_the_operator_is_told(make, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_DIE", "0.3")
    live = make()
    live.running = True
    live.voice_start()
    assert _until(lambda: live._voice_dead, 10.0)
    assert len([m for m in make.got() if m["type"] == "argv"]) == 1 + voicelink.RESTARTS
    st = live.state()
    check_state(st)
    assert st["voice"]["state"] == "stopped" and st["problem"]["code"] == "voice_stopped"
    events = [json.loads(l) for l in open(live.log_path)]
    assert [e["event"] for e in events if e["event"].startswith("voice_")] == ["voice_restart"] * 3 + ["voice_stopped"]
    # turning Live Voice off and on starts it again
    monkeypatch.delenv("FAKE_VOICE_DIE")
    live._voice_settings_changed({"voice.enabled": True})
    assert _until(lambda: live._voice_ready and live.state()["voice"]["state"] == "ready")
    assert live.state()["problem"] is None


def test_it_is_restarted_when_it_hangs(make, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_HANG", "0.4")
    monkeypatch.setattr(voicelink, "HANG_S", 0.6)
    live = make()
    live.voice_start()
    first = _until(lambda: live.voice)
    assert _until(lambda: live.voice is not first and live.voice is not None, 6.0)   # a new process
    assert first.poll() is not None                                                   # the hung one was ended


def test_it_is_restarted_when_it_never_becomes_ready(make, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_NOT_READY", "1")
    monkeypatch.setattr(voicelink, "START_GRACE_S", 0.4)
    live = make()
    live.voice_start()
    assert live.state()["voice"]["state"] == "starting"
    assert _until(lambda: live._voice_dead, 10.0)


def test_a_missing_model_or_interpreter_is_a_red_light_not_a_crash(make, monkeypatch):
    live = make(model=False)
    assert live.voice_start() is False
    st = live.state()
    assert st["voice"]["state"] == "stopped" and "not downloaded" in st["voice"]["error"]
    assert st["problem"]["code"] == "voice_stopped" and st["problem"]["severity"] == "warn"
    monkeypatch.setenv("LIVE_VOICE_PYTHON", "/nonexistent/python")
    live2 = make()
    assert live2.voice_start() is False and live2.state()["voice"]["state"] == "stopped"


def test_behind_turns_the_light_amber_and_raises_the_alert_only_in_a_service(make, monkeypatch):
    monkeypatch.setenv("FAKE_VOICE_BEHIND", "14.5")
    live = make()
    live.voice_start()
    assert _until(lambda: live.state()["voice"]["state"] == "behind")
    assert live.state()["voice"]["behind_s"] == 14.5 and live.state()["problem"] is None
    live.running, live._listening = True, True
    st = live.state()
    check_state(st)
    assert st["problem"]["code"] == "voice_behind" and st["problem"]["severity"] == "info"
    live.behind_s = 9.0                                      # captions behind is the more urgent problem
    assert live.state()["problem"]["code"] == "captions_behind"


def test_sentences_reach_the_voice_and_played_items_are_kept_for_after(make):
    live = make()
    live.voice_start()
    _until(lambda: live._voice_ready)
    live.running = True
    live._voice_new_run()
    for sid, delay in ((1, 3.0), (2, 5.0), (3, 12.0)):
        live._voice_send({"type": "sentence", "sid": sid, "lang": "ro", "text": "Bună dimineața tuturor.", "delay": delay})
    assert _until(lambda: len(live._voice_items) == 3)
    assert _until(lambda: live._voice_peak.get("ro"))        # the next status brings the listeners
    s = live.voice_summary()
    assert s["items"] == 3 and s["delay_median_s"] == 5.0 and s["within_10s_pct"] == 67
    assert s["peak_listeners"] == {"ro": 2, "en": 1} and s["summaries"] == 0
    events = [json.loads(l) for l in open(live.log_path)]
    assert [e["sids"] for e in events if e["event"] == "voice_item"] == [[1], [2], [3]]


def test_test_voice(make):
    live = make()
    assert live.voice_test("ro") is False                    # not running
    live.voice_start()
    _until(lambda: live._voice_ready)
    assert live.voice_test("en")
    say = _until(lambda: [m for m in make.got() if m["type"] == "say"])
    assert say[0]["lang"] == "en" and "test" in say[0]["text"]
    time.sleep(0.3)
    assert live._voice_items == []                           # a test line is not part of the service's numbers


def test_a_summary_request_is_answered_with_the_guards_applied(make):
    live = make()

    class Gen:
        name = "local"
        tr = object()

        def generate(self, texts, lang):
            return "Tatăl meu m-a învățat să iubesc Biblia."

    live._voice_summarizer = lambda: Gen()
    live.voice_start()
    _until(lambda: live._voice_ready)
    live.running = True
    assert _until(lambda: any(m["type"] == "state" and m["summarizer"] for m in make.got()), 2.0)
    text = ("Tatăl meu m-a învățat de mic copil să citesc din Biblie în fiecare seară, iar eu am păstrat acest "
            "obicei toată viața mea până astăzi.")
    live._voice_send({"type": "sentence", "sid": 5, "lang": "ro", "text": "SUMMARIZE " + text})
    reply = _until(lambda: [m for m in make.got() if m["type"] == "summary"])[0]
    assert reply["req"] == 5 and reply["ok"] is True and reply["text"].startswith("Tatăl meu") and reply["engine"] == "local"
    # a summary that changes whose father it is is refused
    Gen.generate = lambda self, texts, lang: "Tatăl său l-a învățat să iubească Biblia."
    live._voice_send({"type": "sentence", "sid": 6, "lang": "ro", "text": "SUMMARIZE " + text})
    reply = _until(lambda: [m for m in make.got() if m["type"] == "summary" and m["req"] == 6])[0]
    assert reply["ok"] is False and reply["reason"].startswith("person")
    ev = [json.loads(l) for l in open(live.log_path)]
    assert [e["ok"] for e in ev if e["event"] == "voice_summary"] == [True, False]
    assert live.voice_summary()["summaries_refused"] == 1


def test_no_summarizer_is_said_in_advance_and_in_the_reply(make):
    live = make()
    live._voice_summarizer = lambda: None
    live.voice_start()
    _until(lambda: live._voice_ready)
    assert _until(lambda: [m for m in make.got() if m["type"] == "state"])[-1]["summarizer"] is False
    live._voice_send({"type": "sentence", "sid": 1, "lang": "ro", "text": "SUMMARIZE " + "cuvânt " * 20})
    reply = _until(lambda: [m for m in make.got() if m["type"] == "summary"])[0]
    assert reply["ok"] is False and reply["reason"] == "no_summarizer"


def test_a_summary_never_brings_in_a_blocklisted_word_and_scripture_is_refused_by_the_orchestrator(make):
    live = make()

    class Gen:
        name, tr = "local", object()

        def generate(self, texts, lang):
            return "Tatăl meu m-a învățat să iubesc porcăria aceea."
    live._voice_summarizer = lambda: Gen()
    live.cfg.setdefault("safety", {})["blocklist"] = ["porcăria"]
    live.voice_start()
    _until(lambda: live._voice_ready)
    live.running = True
    text = ("Tatăl meu m-a învățat de mic copil să citesc din Biblie în fiecare seară, iar eu am păstrat acest "
            "obicei toată viața mea până astăzi.")
    live._voice_send({"type": "sentence", "sid": 5, "lang": "ro", "text": "SUMMARIZE " + text})
    reply = _until(lambda: [m for m in make.got() if m["type"] == "summary"])[0]
    assert reply["ok"] is False and reply["reason"] == "blocklist"
    live._speech_holy = {6: True}                                    # the orchestrator published sid 6 as Scripture
    live._voice_send({"type": "sentence", "sid": 6, "lang": "ro", "text": "SUMMARIZE " + text})
    reply = _until(lambda: [m for m in make.got() if m["type"] == "summary" and m["req"] == 6])[0]
    assert reply["ok"] is False and reply["reason"] == "scripture"


def test_a_voice_that_stops_reading_never_blocks_the_sender(make):
    live = make()
    live.voice_start()
    _until(lambda: live._voice_ready)
    import queue as _q
    live._voice_out = _q.Queue(maxsize=3)                            # as if 2000 messages were already waiting
    t0 = time.time()
    sent = [live._voice_send({"type": "sentence", "sid": i, "lang": "ro", "text": "x"}) for i in range(50)]
    assert time.time() - t0 < 0.5 and sent.count(True) == 3 and live._voice_dropped == 47


def test_off_then_on_in_quick_succession_ends_on(make):
    live = make()
    live.voice_start()
    first = _until(lambda: live.voice)
    _until(lambda: live._voice_ready)
    live.cfg["voice"]["enabled"] = False
    live._voice_settings_changed({"voice.enabled": False})
    live.cfg["voice"]["enabled"] = True
    live._voice_settings_changed({"voice.enabled": True})
    assert _until(lambda: live.voice is not None and live.voice is not first and live._voice_ready, 10.0)
    assert live.state()["voice"]["state"] in ("ready", "speaking") and first.poll() is not None
    live.cfg["voice"]["speed"] = 1.3                                 # a speed change is only sent, nothing restarts
    cur = live.voice
    live._voice_restarts.append(time.monotonic())
    live._voice_settings_changed({"voice.speed": 1.3})
    assert _until(lambda: [m for m in make.got() if m["type"] == "config" and m["cfg"].get("speed") == 1.3], 3.0)
    assert live.voice is cur and len(live._voice_restarts) == 1      # and the restart count is kept


def test_a_play_clock_that_stopped_is_a_hang(make, monkeypatch):
    monkeypatch.setattr(voicelink, "HANG_S", 0.5)
    live = make()
    live.voice_start()
    first = _until(lambda: live.voice)
    _until(lambda: live._voice_ready and live._voice_status)
    orig = live._voice_on
    live._voice_on = lambda ev: orig({**ev, "clock_age_s": 9.0} if ev.get("type") == "status" and live.voice is first else ev)
    assert _until(lambda: live.voice is not first and live.voice is not None, 6.0)
    events = [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []
    live.running = False


def test_captions_come_first_a_summary_waits_for_the_translator(make):
    live = make(summary_timeout_s=0.5)
    calls = []

    class Gen:
        name, tr = "local", object()

        def generate(self, texts, lang):
            calls.append(1)
            return "x"

    live._voice_summarizer = lambda: Gen()
    with live._proc_lock:                                    # a phrase is being translated
        res = live._voice_summary_local(["cuvânt " * 20], "ro", time.time() + 2.0)
    assert res["ok"] is False and res["reason"] == "busy" and calls == []
    # with too little time left it is not even started: the voice would have given up before it ended
    res = live._voice_summary_local(["cuvânt " * 20], "ro", time.time() + 1.0)
    assert res["reason"] == "busy" and calls == []
    live._pending = {"id": 1}                                # a held fragment is on its way: captions first
    res = live._voice_summary_local(["cuvânt " * 20], "ro", time.time() + 1.9)
    assert res["reason"] == "busy" and calls == []


def test_quit_ends_the_voice_process(make):
    live = make()
    live.voice_start()
    v = _until(lambda: live.voice)
    _until(lambda: live._voice_ready)
    live.shutdown()
    assert v.poll() is not None and live.voice is None
    assert any(m["type"] == "stop" for m in make.got())


def test_the_voice_test_endpoint(make):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    live = make()
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    assert c.post("/api/voice/test", json={"lang": "ro"}, headers=me).json() == {"ok": False}
    assert c.post("/api/voice/test", json={"lang": "fr"}, headers=me).status_code == 400
    live.voice_start()
    _until(lambda: live._voice_ready)
    assert c.post("/api/voice/test", json={"lang": "en"}, headers=me).json() == {"ok": True}
