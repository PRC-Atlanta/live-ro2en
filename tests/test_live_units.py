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
    assert rc.text.split("\n") == ["created the heaven", "and the earth."]   # the first line filled up
    assert all(len(l) <= 20 for l in rc.text.split("\n"))


def test_langid_hysteresis():
    assert score("Și Dumnezeu a zis să fie lumină") > 0 > score("And God said let there be light")
    lt = LangTracker("auto", initial="ro", confirm=2, strong=9, strong_away=9)   # hysteresis path only
    assert lt.update("And God said let there be light") == "ro"
    assert lt.update("and the light was good and he saw it") == "ro"   # leaving home takes 3
    assert lt.update("and he called the light day") == "en"
    assert lt.update("Și Dumnezeu a zis să fie lumină") == "en"
    assert lt.update("și a fost seară și a fost dimineață") == "ro"    # coming back takes 2
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


def test_langid_short_garbled_phrase_does_not_leave_home():
    # from a service log: Romanian speech the multilingual model wrote as English
    lt = LangTracker("auto", initial="ro")
    for t in ("nine, so not the chairs of name.", "Aia înseamnă să n-ai nimic cu nimeni.",
              "So the paraskiver.", "could you have to be able to do that."):
        assert lt.update(t) == "ro"
    lt = LangTracker("auto", initial="en")                 # home English: short Romanian does not leave it either
    assert lt.update("Și Dumnezeu a zis să fie lumină") == "en"
    assert lt.update("Dumnezeu este bun") == "en"


def test_postedit_ro():
    from live.translate import postedit
    assert postedit("Iisus i-a zis: Eu sunt calea. Şi viaţa.", "ro") == "Isus i-a zis: Eu sunt calea. Și viața."


def test_nllb_amen_stays_amin():
    import types
    from live.translate import NLLBTranslator

    def fake(decoded):                      # NLLB without the model: decode returns `decoded`
        tr = NLLBTranslator.__new__(NLLBTranslator)
        hyp = types.SimpleNamespace(hypotheses=[["ron_Latn", "x"]])
        tr.tr = types.SimpleNamespace(translate_batch=lambda *a, **k: [hyp])
        tr.tok = types.SimpleNamespace(encode=lambda t: [], convert_ids_to_tokens=lambda ids: [],
                                       convert_tokens_to_ids=lambda t: [], decode=lambda *a, **k: decoded)
        tr.beam = 2
        return tr
    out = fake("Să ne rugăm pentru ei. Amintește-mi.").translate("Let's pray for them. Amen.", "en", "ro")
    assert out == "Să ne rugăm pentru ei. Amin."
    assert fake("Amintește-mi mâine.").translate("Remind me tomorrow.", "en", "ro") == "Amintește-mi mâine."


def test_profiles_override_live_settings():
    import types, yaml
    from live.app import Live
    cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml"))
    cfg["live"]["profile"] = "fast"
    live = Live(cfg, types.SimpleNamespace(file=None))
    assert live.lc["translator"].startswith("nllb") and live.lc["min_commit_s"] == 2.0
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


def test_context_stt_flags_a_word_cut_by_the_chunk():
    import numpy as np
    from live.context_stt import ContextSTT, same_word
    heard = iter(["este o seară de important", "o seară de importantă cât importanță"])
    c = ContextSTT(lambda audio: next(heard), ctx_s=1.0)
    assert c(np.zeros(8000, dtype=np.float32)) == "este o seară de important" and c.seam is None
    assert c(np.zeros(8000, dtype=np.float32)) == "importantă cât importanță"
    assert c.seam == ("important", "importantă")
    # the first chunk ended at a real pause: its last word was whole, a look-alike next word is its own
    heard = iter(["că ești bogat sau sărac", "bogat sau sărat tu lupți"])
    c = ContextSTT(lambda audio: next(heard), ctx_s=1.0)
    c(np.zeros(8000, dtype=np.float32), final=True)
    assert c(np.zeros(8000, dtype=np.float32)) == "sărat tu lupți" and c.seam is None
    # a word said again is matched, not a seam
    heard = iter(["pe Isus", "pe Isus nu este"])
    c = ContextSTT(lambda audio: next(heard), ctx_s=1.0)
    c(np.zeros(8000, dtype=np.float32))
    assert c(np.zeros(8000, dtype=np.float32)) == "nu este" and c.seam is None
    assert same_word("ex.", "exemple") and same_word("mâncat", "mântuit") and same_word("dispoziție", "dispoziția")
    assert not same_word("Laodicea.", "observăm") and not same_word("e", "este")


class _TimedSTT:
    """A recogniser that tells when each word was heard: scripted (word, start_s, end_s) lists."""
    def __init__(self, *calls):
        self.calls, self.heard = list(calls), []

    def words(self, audio):
        self.heard.append(len(audio) / 16000)
        w = self.calls.pop(0)
        return " ".join(x[0] for x in w), w

    def __call__(self, audio):
        return self.words(audio)[0]


def _sec(s):
    import numpy as np
    return np.zeros(int(s * 16000), dtype=np.float32)


# idea 25: words are committed by when they were heard, and a forced cut hears the audio past it
def test_context_stt_commits_by_time_and_hears_past_a_forced_cut():
    from live.context_stt import ContextSTT
    stt = _TimedSTT(
        [("a", 0.2, 0.4), ("b", 0.8, 1.0), ("c.", 1.5, 1.8)],
        # context 2 s + chunk 2 s + 1 s ahead: the cut at 4.0 falls in «importantă», heard whole in the audio ahead
        [("a", 0.2, 0.4), ("b", 0.8, 1.0), ("c", 1.5, 1.8), ("d", 2.3, 2.6), ("e", 3.0, 3.4),
         ("importantă", 3.8, 4.4), ("cât", 4.5, 4.7)],
        # context = the last 2 s, so the clock moved by 2 s
        [("d", 0.3, 0.6), ("e", 1.0, 1.4), ("importantă", 1.8, 2.4), ("cât", 2.5, 2.7), ("x.", 3.0, 3.5)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=True) == "a b c."
    assert c(_sec(2), final=False, ahead=_sec(1)) == "d e importantă"
    assert stt.heard[-1] == 5.0                              # context + chunk + the audio ahead
    assert c(_sec(2), final=True) == "cât x." and c.seam is None
    assert stt.heard[-1] == 4.0                              # the audio ahead was not kept as context


def test_context_stt_the_word_after_the_pause_a_forced_cut_sits_in_waits():
    from live.context_stt import ContextSTT
    # «mare» is stamped at 1.82, before the cut at 2.0, but the voice had been silent for 0.3 s there
    script = lambda: _TimedSTT([("de", 0.9, 1.0), ("tinerețe", 1.1, 1.74), ("mare", 1.82, 1.9), ("seara", 2.1, 2.4)],   # noqa: E731
                               [("de", 0.9, 1.0), ("tinerețe", 1.1, 1.74), ("mare", 1.82, 1.9), ("seara", 2.1, 2.4),
                                ("aceasta.", 2.5, 3.0)])
    c = ContextSTT(script(), ctx_s=4.0)
    assert c(_sec(2), final=False, ahead=_sec(1), quiet_s=0.3) == "de tinerețe"
    assert c(_sec(1.5), final=True) == "mare seara aceasta."
    c = ContextSTT(script(), ctx_s=4.0)                      # the cut in speech: the word began before it
    assert c(_sec(2), final=False, ahead=_sec(1)) == "de tinerețe mare"


def test_context_stt_a_forced_piece_does_not_end_on_a_word_that_leans_on_the_next():
    from live.context_stt import ContextSTT
    from segmenter import held_back
    assert held_back("dezamăgiți de un ușier, de".split()) == 1
    assert held_back("orice predică, crezând că".split()) == 1
    assert held_back("Nu este adevărat, nu".split()) == 1
    assert held_back("cu foarte multă grijă. Priviți".split()) == 1      # the lone first word of a sentence
    assert held_back("care s-a".split()) == 2 and held_back("și de".split()) == 2
    assert held_back("Țineți-vă tari? Nu.".split()) == 0                # a sentence of its own stays
    assert held_back("de un ușier".split()) == 0 and held_back([]) == 0
    stt = _TimedSTT([("de", 0.3, 0.4), ("un", 0.5, 0.6), ("ușier,", 0.7, 1.3), ("de", 1.6, 1.7), ("un", 2.05, 2.1)],
                    [("de", 0.3, 0.4), ("un", 0.5, 0.6), ("ușier,", 0.7, 1.3), ("de", 1.6, 1.7), ("un", 2.05, 2.1),
                     ("învățător.", 2.3, 3.0)])
    c = ContextSTT(stt, ctx_s=4.0)
    assert c(_sec(2), final=False, ahead=_sec(0.5)) == "de un ușier,"
    assert c(_sec(1.5), final=True) == "de un învățător."
    stt = _TimedSTT([("crezând", 0.5, 0.9), ("că", 1.2, 1.3)])            # at a pause nothing is held back
    assert ContextSTT(stt, ctx_s=4.0)(_sec(2), final=True) == "crezând că"


def test_context_stt_a_word_mostly_before_the_cut_is_committed_once():
    from live.context_stt import ContextSTT
    stt = _TimedSTT(
        [("a", 0.2, 0.4), ("importantă", 1.6, 2.2), ("cât", 2.4, 2.6)],          # cut at 2.0, 1 s ahead
        # heard again one frame later: still the committed word, not a new one
        [("a", 0.2, 0.4), ("importantă", 1.92, 2.28), ("cât", 2.4, 2.6), ("y.", 3.0, 3.4)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=False, ahead=_sec(1)) == "a importantă"
    assert c(_sec(2), final=True) == "cât y."


def test_context_stt_the_last_word_heard_later_or_earlier_is_not_said_twice_or_lost():
    from live.context_stt import ContextSTT
    # the one-frame word «și» committed at 1.84-1.92; next time it sits two frames later, past the old boundary
    stt = _TimedSTT([("a", 0.2, 0.4), ("și", 1.84, 1.92)],
                    [("a", 0.2, 0.4), ("și", 2.0, 2.08), ("b.", 2.5, 2.9)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=True) == "a și"
    assert c(_sec(2), final=True) == "b."
    # the first new word sits a frame before the old boundary: it is still new
    stt = _TimedSTT([("a", 0.2, 0.4), ("și", 1.84, 1.92)],
                    [("a", 0.2, 0.4), ("și", 1.76, 1.84), ("b", 1.84, 1.92), ("c.", 2.5, 2.9)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=True) == "a și"
    assert c(_sec(2), final=True) == "b c."


def test_context_stt_a_word_at_the_edge_of_the_audio_waits():
    from live.context_stt import ContextSTT
    stt = _TimedSTT([("apoi", 0.2, 0.4), ("impor", 1.7, 2.0)],                   # forced, nothing ahead
                    [("apoi", 0.2, 0.4), ("importantă", 1.7, 2.4), ("b.", 3.0, 3.4)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=False) == "apoi"
    assert c(_sec(2), final=True) == "importantă b."


def test_context_stt_finds_the_committed_words_again_when_their_times_moved():
    from live.context_stt import ContextSTT
    # «în mod» was committed at the end of the audio; heard again, both sit 0.24 s later than the clock says
    stt = _TimedSTT([("se", 0.4, 0.5), ("maturizează", 0.6, 1.1), ("în", 1.4, 1.48), ("mod", 1.48, 1.56),
                     ("constant.", 1.64, 2.0)],
                    [("se", 0.5, 0.6), ("maturizează", 0.7, 1.3), ("în", 1.64, 1.72), ("mod", 1.72, 1.8),
                     ("constant", 2.0, 2.4), ("și.", 3.0, 3.2)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(1.6), final=False, ahead=_sec(0.4)) == "se maturizează în mod"
    assert c(_sec(2), final=True) == "constant și."
    # a word really said twice stays twice
    stt = _TimedSTT([("este", 0.4, 0.5), ("foarte", 1.2, 1.6)],
                    [("este", 0.4, 0.5), ("foarte", 1.2, 1.6), ("foarte", 2.1, 2.5), ("bine.", 2.6, 3.0)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=True) == "este foarte"
    assert c(_sec(2), final=True) == "foarte bine."


def test_context_stt_tells_when_it_keeps_all_the_words_for_the_next_chunk():
    from live.context_stt import ContextSTT
    stt = _TimedSTT([("a", 0.2, 0.4), ("b.", 1.0, 1.4)],
                    [("a", 0.2, 0.4), ("b", 1.0, 1.4), ("importantă", 2.3, 2.9)],     # cut at 2.5, inside the word
                    [("b", 0.5, 0.9), ("importantă", 1.8, 2.4), ("c.", 2.6, 3.0)])
    c = ContextSTT(stt, ctx_s=2.0)
    assert c(_sec(2), final=True) == "a b." and c.waiting == 0
    assert c(_sec(0.5), final=False, ahead=_sec(0.5)) == "" and c.waiting == 1
    assert c(_sec(2), final=True) == "importantă c." and c.waiting == 0


def test_context_stt_says_late_a_clause_the_last_chunk_lost():
    from live.context_stt import ContextSTT
    stt = _TimedSTT(
        [("lăudat", 0.2, 0.5), ("să", 0.6, 0.7), ("fie", 0.8, 0.9), ("Domnul", 1.2, 1.8)],
        # 4 s of speech heard as two words
        [("lăudat", 0.2, 0.5), ("să", 0.6, 0.7), ("fie", 0.8, 0.9), ("Domnul", 1.2, 5.2), ("acesta.", 5.4, 5.9)],
        # with the next chunk the clause is there: context = the last 5 s, clock moved by 1 s
        [("fie", 0.0, 0.1), ("Domnultarea", 1.1, 1.6), ("care", 2.3, 2.4), ("s-a", 2.5, 2.7), ("cântat", 2.8, 3.1),
         ("înainte", 3.2, 3.5), ("de", 3.6, 3.7), ("mesajul", 3.8, 4.2), ("acesta,", 4.4, 4.9), ("mi-a", 5.2, 5.4),
         ("dat.", 5.5, 5.8)],
        # one changed word is not said again
        [("mesajul", 0.0, 0.2), ("acela,", 0.4, 0.9), ("mi-a", 1.2, 1.4), ("dat", 1.5, 1.8), ("curaj.", 5.2, 5.6)])
    c = ContextSTT(stt, ctx_s=5.0)
    assert c(_sec(2), final=True) == "lăudat să fie Domnul"
    assert c(_sec(4), final=True) == "acesta."                # «Domnul», stretched over the clause, is the old word
    assert c(_sec(1), final=True) == "care s-a cântat înainte de mesajul mi-a dat."
    assert c(_sec(4), final=True) == "curaj."


def test_context_stt_words_found_again_later_are_not_a_lost_clause():
    from live.context_stt import ContextSTT
    # «bb cc dd» committed from the end of the audio, heard 0.45 s later next time (the anchor skips
    # them): the call after that must not take them for words the last chunk lost
    stt = _TimedSTT(
        [("aa", 0.5, 0.8), ("zz.", 3.0, 3.4)],
        [("aa", 0.5, 0.8), ("zz", 3.0, 3.4), ("bb", 4.3, 4.5), ("cc", 4.8, 5.0), ("dd", 5.3, 5.5), ("ee", 6.1, 6.4)],
        [("zz", 3.0, 3.4), ("bb", 4.75, 4.95), ("cc", 5.25, 5.45), ("dd", 5.75, 5.95), ("ee", 6.3, 6.5),
         ("ff", 7.0, 7.3), ("gg", 8.1, 8.4)],
        [("bb", 2.75, 2.95), ("cc", 3.25, 3.45), ("dd", 3.75, 3.95), ("ee", 4.3, 4.5), ("ff", 5.0, 5.3),
         ("gg", 6.1, 6.4), ("hh.", 7.0, 7.4)])
    c = ContextSTT(stt, ctx_s=6.0)
    assert c(_sec(4), final=True) == "aa zz."
    assert c(_sec(2), final=False, ahead=_sec(0.5)) == "bb cc dd"
    assert c(_sec(2), final=False, ahead=_sec(0.5)) == "ee ff"
    assert c(_sec(2), final=True) == "gg hh."


def test_context_stt_words_wholly_in_the_audio_ahead_are_not_waiting():
    from live.context_stt import ContextSTT
    stt = _TimedSTT([("a", 0.2, 0.4), ("b.", 1.0, 1.4)],
                    [("a", 0.2, 0.4), ("b", 1.0, 1.4), ("c", 2.7, 2.9), ("d", 3.1, 3.3)])   # chunk 2.0-2.5: nothing in it
    c = ContextSTT(stt, ctx_s=2.0)
    c(_sec(2), final=True)
    assert c(_sec(0.5), final=False, ahead=_sec(1)) == "" and c.waiting == 0


def test_context_stt_a_waiting_word_survives_a_call_that_hears_nothing():
    from live.context_stt import ContextSTT
    stt = _TimedSTT([("a", 0.2, 0.4), ("b", 1.0, 1.4), ("c", 1.9, 2.5)],        # cut at 2.0: «c» waits
                    [],
                    [("c", 1.9, 2.5), ("d.", 3.0, 3.4)])                         # all 2.5 s are still the context
    c = ContextSTT(stt, ctx_s=3.0)
    assert c(_sec(2), final=False, ahead=_sec(0.6)) == "a b"
    assert c(_sec(0.5), final=False) == ""
    assert c(_sec(2), final=True) == "c d."


def test_context_stt_without_word_times_stitches_by_text():
    from live.context_stt import ContextSTT

    class NoTimes(_TimedSTT):
        def words(self, audio):
            return self.calls[0], []                         # the text, but no times

        def __call__(self, audio):
            return self.calls.pop(0)
    c = ContextSTT(NoTimes("for god so", "for god so loved the world"), ctx_s=1.0)
    assert c(_sec(0.5)) == "for god so" and c(_sec(0.5), ahead=_sec(0.5)) == "loved the world"
    c = ContextSTT(_TimedSTT([("for", 0.1, 0.2)], [("for", 0.1, 0.2), ("god", 0.6, 0.8)]), ctx_s=1.0, timed=False)
    assert c(_sec(0.5)) == "for" and c(_sec(0.5)) == "god"


# 2026-11-06 / 2026-11-08: NLLB English → Romanian
def test_nllb_english_without_fillers_and_stutters_split_in_sentences():
    from live import translate as T
    assert T.prep_en("Um in the Bible we find") == "In the Bible we find"
    assert T.prep_en("And I and I booked") == "And I booked"
    assert T.prep_en("The blue the blueprint") == "The blueprint"
    assert T.prep_en("I know that that is true") == "I know that that is true"
    assert T.split_sentences("I'm not talking about continuous abuse. You need to seek help") == \
        ["I'm not talking about continuous abuse.", "You need to seek help"]
    assert T.split_sentences("St. Paul wrote. Pride.") == ["St. Paul wrote.", "Pride."]


def test_nllb_subtitle_dialogue_and_a_feminine_preacher_are_fixed():
    from live import translate as T
    assert T.nllb_ro_fix("why?", "- De ce nu?") == "De ce?"
    assert T.nllb_ro_fix("that?", "- Asta? - Da.") == "Asta?"
    assert T.nllb_ro_fix("a little?", "- Puțin?") == "Puțin?"
    assert T.nllb_ro_fix("Be careful", "Fii atentă.") == "Fii atent."
    assert T.nllb_ro_fix("don't be overwhelmed", "Nu te lăsa copleșită.") == "Nu te lăsa copleșit."
    assert T.nllb_ro_fix("She was proud", "Ea era mândră.") == "Ea era mândră."
    assert T.nllb_ro_fix("and I said", "si am spus") == "și am spus"
