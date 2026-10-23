import sys, pathlib, socket, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from live.segmenter import PauseTracker, split_clauses
from live.captions import RollingCaptions
from live.langid import LangTracker, score
from live.propresenter import ProPresenter
from live.mock_propresenter import serve


def test_split_on_sentence_and_conjunction_commas():
    c, tail = split_clauses("La început Dumnezeu a făcut cerurile. Pământul era pustiu, și gol, iar întuneric era")
    assert c == ["La început Dumnezeu a făcut cerurile.", "Pământul era pustiu,"]
    assert tail == "și gol, iar întuneric era"        # 2-word clause merges forward


def test_no_split_on_plain_comma_or_inside_clause():
    c, tail = split_clauses("Petru, Iacov și Ioan au mers")
    assert c == [] and tail == "Petru, Iacov și Ioan au mers"


def test_short_clauses_merge():
    c, tail = split_clauses("Amin. Frați și surori, astăzi citim din Romani.")
    assert c == ["Amin. Frați și surori, astăzi citim din Romani."]
    assert tail == ""


def test_pause_tracker():
    pt = PauseTracker(frame_ms=32, min_pause_ms=320, max_clause_s=1.0)
    ev = [pt.push(1.0) for _ in range(10)] + [pt.push(0.0) for _ in range(10)]
    assert ev.count("pause") == 1 and "force" not in ev
    ev = [pt.push(1.0) for _ in range(40)]
    assert ev.count("force") == 1


def test_rolling_captions_keep_whole_lines():
    rc = RollingCaptions(max_chars=20, max_lines=2)
    rc.add("In the beginning")
    assert rc.text == "In the beginning"
    rc.add("God created the heaven and the earth.")
    assert rc.text.split("\n") == ["heaven and the", "earth."]
    assert all(len(l) <= 20 for l in rc.text.split("\n"))


def test_langid_hysteresis():
    assert score("Și Dumnezeu a zis să fie lumină") > 0 > score("And God said let there be light")
    lt = LangTracker("auto", initial="ro", confirm=2, strong=9)   # hysteresis path only
    assert lt.update("And God said let there be light") == "ro"
    assert lt.update("and the light was good and he saw it") == "en"
    lt.set_mode("ro-en")
    assert lt.update("And the evening and the morning were the first day") == "ro"


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def test_propresenter_adapter_against_mock():
    port = _free_port()
    srv, st = serve(port)
    try:
        pp = ProPresenter(port=port, message_id="Live Translation", token_name="Text", stage_message=True)
        assert pp.ping()
        assert pp.show("Hello\nworld")
        assert st.visible["Live Translation"] == "Hello\nworld" and st.stage == "Hello\nworld"
        n = len(st.events)
        assert pp.show("Hello\nworld") and len(st.events) == n         # unchanged text not resent
        assert pp.clear() and "Live Translation" not in st.visible and st.stage is None
        bad = ProPresenter(port=port, message_id="Nope")
        assert not bad.show("x") and "404" in bad.last_error
        dead = ProPresenter(port=_free_port(), timeout_s=0.3)
        assert not dead.show("x") and dead.last_error
    finally:
        srv.shutdown()


def test_langid_strong_switch_is_immediate():
    lt = LangTracker("auto", initial="ro", confirm=2)
    assert lt.update("For God so loved the world, that he gave his only begotten Son") == "en"


def test_postedit_ro():
    from live.translate import postedit
    assert postedit("Iisus i-a zis: Eu sunt calea. Şi viaţa.", "ro") == "Isus i-a zis: Eu sunt calea. Și viața."


def test_profiles_override_live_settings():
    import types, yaml
    from live.app import Live
    cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
    cfg["live"]["profile"] = "fast"
    live = Live(cfg, types.SimpleNamespace(file=None))
    assert live.lc["translator"].startswith("nllb") and live.lc["min_commit_s"] == 0
    q = live._profile_cfg("quality")
    assert q["translator"].startswith("mlx:") and q["max_chunk_s"] > 0 and live.lc["max_chunk_s"] > 0
    live.set_profile("quality")          # not running: just swaps settings, drops the loaded translator
    assert live.profile == "quality" and live.tr is None and live.lc["context_clauses"] == 4


def test_chunk_error_does_not_stop_service(tmp_path):
    import types, yaml
    from live.app import Live
    cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
    live = Live(cfg, types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda *a, **k: 1 / 0)
    ev = {"id": 1, "ro": "Frați și surori, astăzi citim din Romani.", "multi": "", "t_start": 0, "t_end": 1,
          "t_emit": 1.1, "stt_s": 0.1, "audio_s": 1.0}
    import live.app as appmod
    real_root = appmod.ROOT
    (tmp_path / "logs").mkdir()
    appmod.ROOT = tmp_path
    try:
        live._handle(ev)                   # must not raise
    finally:
        appmod.ROOT = real_root
    assert "error on one chunk" in live.status


def test_lock_at_startup_sets_language():
    assert LangTracker("en-ro").current == "en" and LangTracker("ro-en").current == "ro"
    assert LangTracker("auto", initial="en").current == "en"


def test_context_stt_returns_only_new_words():
    import numpy as np
    from live.context_stt import ContextSTT
    script = "for god so loved the world that he gave his only begotten son".split()
    # fake recognizer: audio length in "words" (1 sample = 1 word) -> that many words of the script,
    # ending at the current position, so context audio re-produces the context words
    pos = {"end": 0}
    def fake(audio):
        n = len(audio)
        return " ".join(script[pos["end"] - n:pos["end"]])
    c = ContextSTT(fake, ctx_s=4 / 16000, max_ctx_words=4)    # context = last 4 "words"
    out = []
    for k in (3, 3, 4):
        pos["end"] += k
        out.append(c(np.zeros(k, dtype=np.float32)))
    assert out == ["for god so", "loved the world", "that he gave his"]
