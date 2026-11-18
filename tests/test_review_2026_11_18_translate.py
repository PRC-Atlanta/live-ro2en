"""Review 2026-11-18, translation and AI fixes (src/live/translate.py, ai.py). No model is loaded:
mlx_lm, the tokenizer and the API client are fakes."""
import pathlib
import sys
import threading
import time
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))

import ai                                 # noqa: E402
import scripture as S                     # noqa: E402
from live import translate as T           # noqa: E402


def _mlx(monkeypatch, answers):
    """An MLXLLMTranslator without a model: each generation returns the next of `answers`."""
    it = iter(answers)
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(generate=lambda model, tok, prompt, **k: next(it)))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=lambda **k: None))
    t = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    t.model, t.max_tokens, t.context, t.context_clauses = None, 200, [], 2
    t.reading, t.verses, t.last_verse = S.Reading(), None, None
    t.tok = types.SimpleNamespace(apply_chat_template=lambda m, **k: m[-1]["content"], encode=lambda s: s.split())
    return t


# --- P2: one reset per service clears everything the translator learnt -------------------------
def test_reset_context_forgets_iacov_reading_and_verse_lock(monkeypatch):
    t = _mlx(monkeypatch, [])
    t.verses = types.SimpleNamespace(reset=lambda: setattr(t, "_verses_reset", True))
    t.context = [("a", "b", "ro")]
    t._iacov_ot = (True, time.time())              # last week's Genesis 32
    t.reading.ref, t.reading.left = S.find("Geneza 32"), 3
    t.last_verse = {"ref": "Genesis 32:28"}
    assert t._old_testament()
    t.reset_context()
    assert t.context == [] and t._iacov_ot is None and t.reading.ref is None and t.last_verse is None
    assert t._verses_reset and not t._old_testament()        # today's Iacov is James again


def test_real_matcher_reset_drops_lock_history_and_recent():
    import verses
    m = verses.Matcher(path=ROOT / "no-such-bible.npz", background=False)
    m.lock, m.lock_left, m.recent = (10, 12), 3, {10: (5, 1)}
    m.history.extend(["x", "y"])
    m.reset()
    assert m.lock is None and m.lock_left == 0 and not m.recent and not m.history


# --- P2: prep_en keeps deliberate repetitions -----------------------------------------------------
def test_prep_en_keeps_comma_separated_repetitions():
    for s in ("Holy, holy, holy is the Lord", "Truly, truly, I say to you", "No, no, no.", "Amen, amen.",
              "My God, my God, why"):
        assert T.prep_en(s) == s


def test_prep_en_still_collapses_stutters():
    assert T.prep_en("I I think so") == "I think so"
    assert T.prep_en("We read the the book") == "We read the book"
    assert T.prep_en("And I and I booked") == "And I booked"
    assert T.prep_en("The blue the blueprint") == "The blueprint"
    assert T.prep_en("I know that that is true") == "I know that that is true"


# --- P3: the echo retry gets the same post-processing ----------------------------------------------
def test_echo_retry_output_gets_cu_reference(monkeypatch):
    echo = "a living sign before You and before"
    t = _mlx(monkeypatch, [echo, "Turn to 26 with 24 now"])
    t.context = [("un semn viu înaintea Ta", echo, "ro")]
    out = t.translate("Deschideți la 26 cu 24 acum", "ro", "en", use_context=True)
    assert out == "Turn to 26:24 now"


# --- P3: modernize leaves quoted titles alone -------------------------------------------------------
def test_modernize_skips_quoted_titles():
    assert T.modernize('We sing "How Great Thou Art" because thou art great') == \
        'We sing "How Great Thou Art" because you are great'
    assert T.modernize("We sing «How Great Thou Art» now, thy will") == "We sing «How Great Thou Art» now, your will"
    assert T.modernize("Thou art my God") == "You are my God"


# --- P3: NLLB sets the language tag and encodes under one lock ------------------------------------
def test_nllb_language_tag_and_encode_are_not_interleaved():
    tr = T.NLLBTranslator.__new__(T.NLLBTranslator)
    tr._tok_lock = threading.Lock()
    seen, gate = [], threading.Event()

    class Tok:
        src_lang = None

        def encode(self, text):
            lang = self.src_lang
            gate.wait(0.2)                       # the other thread tries to change the tag meanwhile
            seen.append((text, lang))
            return []
    tr.tok = Tok()
    tr.tok.convert_ids_to_tokens = lambda ids: []
    tr.tok.convert_tokens_to_ids = lambda t: []
    tr.tok.decode = lambda *a, **k: "x"
    hyp = types.SimpleNamespace(hypotheses=[["eng_Latn", "x"]])
    tr.tr = types.SimpleNamespace(translate_batch=lambda *a, **k: [hyp])
    tr.beam = 2
    a = threading.Thread(target=tr.translate, args=("Bună ziua", "ro", "en"))
    a.start()
    time.sleep(0.05)
    b = threading.Thread(target=tr.translate, args=("Good day", "en", "ro"))
    b.start()
    gate.set()
    a.join(), b.join()
    assert dict(seen) == {"Bună ziua": "ron_Latn", "Good day": "eng_Latn"}


# --- P3: the AI client is built on a thread, never on the caption path ------------------------------
def test_usable_never_builds_the_client_on_the_caller(monkeypatch):
    e = ai.Engine({})
    started, release = threading.Event(), threading.Event()
    built = object()

    def slow_client():
        started.set()
        release.wait(2)
        with e._lock:
            e._client = built
        return built
    monkeypatch.setattr(e, "client", slow_client)
    monkeypatch.setattr(e, "_lookup", lambda: None)
    t0 = time.perf_counter()
    assert e.usable() is False                   # not built yet: local, at once
    assert time.perf_counter() - t0 < 0.1 and started.wait(1)
    release.set()
    e._warm_thread.join(1)
    assert e.usable() is True


def test_no_key_is_tried_once_not_every_phrase(monkeypatch, tmp_path):
    e = ai.Engine({}, root=tmp_path)
    assert not e.usable()
    e._warm_thread.join(1)
    first = e._warm_thread
    assert not e.usable() and e._warm_thread is first      # no new thread per phrase
    e.reload_key()
    assert not e._warm_tried
