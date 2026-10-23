"""S4: the hot Fast <-> Quality switch (no engine restart), pace and backlog state, and the
safety filter (translator loops, over-long outputs, the blocklist)."""
import io
import json
import pathlib
import sys
import threading
import time
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live                                     # noqa: E402
import live.pipeline as P                                     # noqa: E402
import pipeline as P2                                         # noqa: E402  (the module Live uses)

CFG = ROOT / "config.yaml"


def _live(tmp_path, translate=lambda t, *a, **k: f"EN: {t}"):
    live = Live(yaml.safe_load(open(CFG)), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=translate)
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    live.lang.set_mode("ro-en")
    return live


def _clause(i, ro, ago=0.5):
    t = time.time() - ago
    return {"id": i, "ro": ro, "multi": "", "en": "", "t_start": t - 1, "t_end": t, "t_emit": t + 0.1,
            "stt_s": 0.1, "audio_s": 1.0}


def _log(live):
    return [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []


def _outcomes(live):
    return [r["outcome"] for r in _log(live) if "outcome" in r]


# --- safety filter ---------------------------------------------------------------------------
def test_translator_loop_inside_one_output_is_cut():
    out, cut = P.trim_loops("And he said, and he said, and he said, and he said, and he said to them",
                            "Și le-a zis")
    assert cut and out == "And he said, to them"
    assert P.trim_loops("Holy, holy, holy is the Lord", "Sfânt, sfânt, sfânt este Domnul") == \
        ("Holy, holy, holy is the Lord", False)                        # three times is scripture
    src = "Aleluia, aleluia, aleluia, aleluia!"
    assert P.trim_loops("Hallelujah, hallelujah, hallelujah, hallelujah!", src)[1] is False  # the speaker's


def test_repeated_output_is_dropped_and_counted(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: "Thank you for watching.")
    for i, ro in enumerate(["Frați și surori, bine ați venit.", "Astăzi citim din Romani.",
                            "Capitolul opt, versetul unu.", "Pavel scrie bisericii."], 1):
        live._handle(_clause(i, ro))
    assert _outcomes(live) == ["ok", "ok", "mt_loop", "mt_loop"]
    assert live.counts["mt_loop"] == 2 and live.counts["ok"] == 2
    assert live.state()["auto_actions"][-1]["msg"].startswith("Dropped a caption repeated")


def test_speaker_repeating_himself_is_not_a_loop(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: "Glory to God, amen.")
    for i in range(1, 4):
        live._handle(_clause(i, "Slavă lui Dumnezeu, amin."))
    assert _outcomes(live) == ["ok", "ok", "ok"]


def test_overlong_output_is_dropped(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k:
                 "Brothers and sisters, today we read from Romans chapter eight, verse one, and then")
    live._handle(_clause(1, "Frați și surori."))
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "Amen, amen.")
    live._handle(_clause(2, "Amin."))                                  # short outputs are never judged
    assert _outcomes(live) == ["mt_overlong", "ok"] and live.counts["mt_overlong"] == 1
    assert live.caps.text == "Amen, amen."


def test_blocklist_masks_words(tmp_path):
    live = _live(tmp_path, translate=lambda t, *a, **k: "This damn thing, said the Prost man.")
    live.cfg["safety"] = {"blocklist": ["damn", "proşt"]}             # cedilla and diacritics folded
    live._handle(_clause(1, "Frați și surori, astăzi citim."))
    rec = _log(live)[-1]
    assert rec["outcome"] == "ok" and rec["masked"] == 2
    assert live.caps.text.replace("\n", " ") == "This *** thing, said the *** man."
    assert live.counts["masked"] == 2


def test_blocklist_file_and_phrases(tmp_path):
    (tmp_path / "block.txt").write_text("# comment\nbad word\n", encoding="utf-8")
    live = _live(tmp_path, translate=lambda t, *a, **k: "A Bad  Word here, a badword there")
    live.cfg["paths"]["blocklist"] = str(tmp_path / "block.txt")
    live._handle(_clause(1, "Frați și surori, astăzi citim."))
    assert live.caps.text.replace("\n", " ") == "A *** here, a badword there"
    assert live.history[-1]["translation"] == "A *** here, a badword there"   # the log has what was shown


# --- pace and backlog ---------------------------------------------------------------------
def test_pace_and_backlog_state(tmp_path):
    live = _live(tmp_path)
    st = live.state()
    assert st["pace"] == {"state": "ok", "behind_s": None} and st["backlog"] == {"n": 0, "oldest_s": None}
    live._work.put(_clause(1, "Unu doi trei patru.", ago=7))
    live._work.put(_clause(2, "Cinci șase șapte opt.", ago=2))
    st = live.state()
    assert st["backlog"]["n"] == 2 and 6.5 <= st["backlog"]["oldest_s"] <= 8
    assert st["pace"]["state"] == "slow" and st["pace"]["behind_s"] >= 6.5   # over lag_warn_s (6)
    live._work.get(); live._work.get()
    live.behind_s = 9.5
    assert live.state()["pace"] == {"state": "behind", "behind_s": 9.5}
    live.behind_s = None
    live.running, live.stt_behind = True, "recognition 4 s behind"
    assert live.state()["pace"]["state"] == "slow"


def test_backlog_counts_the_clause_being_translated(tmp_path):
    go = threading.Event()
    live = _live(tmp_path, translate=lambda t, *a, **k: go.wait(5) and "Brothers and sisters.")
    live._submit(_clause(1, "Frați și surori, astăzi citim.", ago=1))
    time.sleep(0.2)
    assert live.state()["backlog"]["n"] == 1
    go.set()
    live._drain()
    assert live.state()["backlog"] == {"n": 0, "oldest_s": None}


def test_catch_up_is_an_auto_action(tmp_path):
    live = _live(tmp_path)
    live._work.put(_clause(2, "citim din Romani.", ago=6))
    ev, n = live._catch_up(_clause(1, "Frați și surori,", ago=7))
    assert n == 2 and live.state()["auto_actions"][-1]["msg"] == "Caught up: 2 phrases shown together"


# --- hot switch -----------------------------------------------------------------------------
class FakeEngine:
    """A running engine: records the commands it gets, never exits."""
    def __init__(self):
        self.stdin = io.StringIO()
        self.returncode = None

    def poll(self):
        return None

    def cmds(self):
        return [json.loads(l) for l in self.stdin.getvalue().splitlines()]


class FakeTr:
    def __init__(self, spec, delay=0.0):
        self.spec, self.delay, self.context, self.context_clauses = spec, delay, [("x", "y", "ro")], 2

    def translate(self, text, *a, **k):
        time.sleep(self.delay)
        return f"[{self.spec}] {text}"

    def reset_context(self):
        self.context = []


def _running(tmp_path, monkeypatch, keep, load_s=0.0):
    live = _live(tmp_path)
    live.cfg["profiles"]["fast"]["translator"] = "fake:fast"
    live.cfg["profiles"]["quality"]["translator"] = "fake:quality"
    live.profile, live.lc = "fast", live._profile_cfg("fast")
    live.lc["keep_both_translators"] = keep
    live.cfg["profiles"]["quality"]["keep_both_translators"] = keep
    live.cfg["profiles"]["fast"]["keep_both_translators"] = keep
    live.tr = FakeTr("fake:fast")
    live.engine, live.running = FakeEngine(), True
    loads = []

    def make(spec):
        loads.append(spec)
        time.sleep(load_s)
        return FakeTr(spec)
    monkeypatch.setattr(P2.translate, "make_translator", make)
    live.stop = lambda: pytest.fail("the hot switch must not stop the engine")
    live.start = lambda *a: pytest.fail("the hot switch must not start an engine")
    return live, loads


def test_hot_switch_keeps_the_engine_and_sends_cfg(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    engine, fast_tr = live.engine, live.tr
    live.set_profile("quality")
    assert live.engine is engine and live.running and live.profile == "quality"
    assert loads == ["fake:quality"] and live.tr.spec == "fake:quality"
    assert live.tr.context == [] and live.tr.context_clauses == 4             # the profile's context size
    cmds = engine.cmds()
    assert [c["cmd"] for c in cmds] == ["cfg"]
    assert cmds[0]["cfg"]["min_pause_ms"] == 250 and cmds[0]["cfg"]["max_clause_s"] == 2.5
    assert "stt_model" not in cmds[0]["cfg"] and "en_recognizer" not in cmds[0]["cfg"]
    assert all(isinstance(v, (int, float, str, bool)) for v in cmds[0]["cfg"].values())   # contract: map num|str|bool
    live._handle(_clause(1, "Frați și surori, astăzi citim."))
    assert _log(live)[-1]["translation"].startswith("[fake:quality]") and _log(live)[-1]["profile"] == "quality"
    # switching back is instant: the fast translator stayed loaded
    live.set_profile("fast")
    assert live.tr is fast_tr and loads == ["fake:quality"] and fast_tr.context == []
    assert [c["cmd"] for c in engine.cmds()] == ["cfg", "cfg"]
    ev = [r for r in _log(live) if r.get("event") == "profile"]
    assert [(e["from"], e["to"], e["hot"], e["engine_cfg"]) for e in ev] == [
        ("fast", "quality", True, True), ("quality", "fast", True, True)]
    assert ev[1]["loaded"] is False


def test_hot_switch_translates_while_the_new_translator_loads(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True, load_s=0.8)
    t = threading.Thread(target=live.set_profile, args=("quality",))
    t.start(); time.sleep(0.1)
    t0 = time.time()
    live._handle(_clause(1, "Frați și surori, astăzi citim."))       # not blocked by the load
    assert time.time() - t0 < 0.4 and _log(live)[-1]["translation"].startswith("[fake:fast]")
    t.join()
    assert live.tr.spec == "fake:quality"
    assert [r for r in _log(live) if r.get("event") == "profile"][0]["blocked_s"] < 0.2


def test_low_memory_switch_reloads_with_the_engine_running(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=False, load_s=0.3)
    live.set_profile("quality")
    assert loads == ["fake:quality"] and live._spare_tr == {} and live.tr.spec == "fake:quality"
    live.set_profile("fast")
    assert loads == ["fake:quality", "fake:fast"]                    # nothing kept: reloaded
    assert [c["cmd"] for c in live.engine.cmds()] == ["cfg", "cfg"]
    assert all(r["hot"] for r in _log(live) if r.get("event") == "profile")


def test_clauses_wait_during_a_warm_reload_and_are_caught_up(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=False, load_s=0.5)
    t = threading.Thread(target=live.set_profile, args=("quality",))
    t.start(); time.sleep(0.1)
    live._submit(_clause(1, "Frați și surori, astăzi citim."))
    live._submit(_clause(2, "din epistola către Romani."))
    t.join(); live._drain()
    recs = [r for r in _log(live) if r.get("outcome") == "ok"]
    assert recs and all(r["translation"].startswith("[fake:quality]") for r in recs)


def test_a_different_speech_model_still_restarts_the_engine(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    live.cfg["profiles"]["quality"]["stt_model"] = "another-model"
    calls = []
    live.stop = lambda: (calls.append("stop"), setattr(live, "running", False))
    live.start = lambda *a: calls.append("start")
    live.set_profile("quality")
    assert calls == ["stop", "start"] and live.engine.cmds() == [] and loads == []   # start() loads it
    assert live.tr is None and live._spare_tr["fake:fast"].spec == "fake:fast"


def test_switch_while_stopped_only_swaps_settings(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    live.running, live.engine = False, None
    live.set_profile("quality")
    assert loads == [] and live.tr is None and live.profile == "quality"    # loaded at Start
    live.set_profile("fast")
    assert live.tr.spec == "fake:fast"                                      # the kept one is back


def test_preload_translators_loads_the_other_profile(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    live.preload_translators()
    assert loads == ["fake:quality"]
    live.set_profile("quality")
    assert loads == ["fake:quality"] and live.tr.spec == "fake:quality"


def test_profile_route_switches_hot(tmp_path, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import build_app
    live, loads = _running(tmp_path, monkeypatch, keep=True)
    port = int(live.cfg["live"]["web_port"])
    c = TestClient(build_app(live), base_url=f"http://127.0.0.1:{port}")
    r = c.post("/api/profile", json={"profile": "quality"}, headers={"Origin": f"http://127.0.0.1:{port}"})
    assert r.status_code == 200 and r.json()["profile"] == "quality"
    assert [x["cmd"] for x in live.engine.cmds()] == ["cfg"]


# --- translator objects ------------------------------------------------------------------
def test_every_translator_has_reset_context():
    import live.translate as T
    tr = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    tr.context = [("a", "b", "ro")]
    tr.reset_context()
    assert tr.context == []
    T.NLLBTranslator.__new__(T.NLLBTranslator).reset_context()      # nothing to forget


def test_nllb_loads_from_the_model_setup_location(monkeypatch, tmp_path):
    import live.translate as T
    seen = {}
    monkeypatch.setitem(sys.modules, "ctranslate2", types.SimpleNamespace(
        Translator=lambda path, **k: seen.setdefault("path", path)))
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(
        AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda name: name)))
    import model_setup
    monkeypatch.setattr(model_setup, "ct2_dir", lambda name, root: tmp_path / "hf" / name)
    T.NLLBTranslator("nllb-200-distilled-1.3B")
    assert seen["path"] == str(tmp_path / "hf" / "nllb-200-distilled-1.3B")


def test_failed_warm_reload_goes_back_to_the_old_profile(tmp_path, monkeypatch):
    live, loads = _running(tmp_path, monkeypatch, keep=False)

    def make(spec):
        loads.append(spec)
        if spec == "fake:quality":
            raise RuntimeError("model files missing")
        return FakeTr(spec)
    monkeypatch.setattr(P2.translate, "make_translator", make)
    with pytest.raises(RuntimeError):
        live.set_profile("quality")
    assert live.profile == "fast" and live.tr.spec == "fake:fast" and live.engine.cmds() == []
