"""The confidence gate (idea 17), language votes (idea 12), the empty-phrase fallback (idea 10)
and name lists (idea 15). Examples are from the 2026-10-23/24 service logs."""
import json
import pathlib

import pytest
import sys
import time
import types

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from live import gate
from live.app import Live
from live.langid import LangTracker
from live.pipeline import is_name_list

CFG = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
LEX = gate.lexicon()


def _ok(text, lang):
    return gate.check(text, lang, LEX)[0]


def test_gate_drops_garbled_recognition():
    assert not _ok("slava ludum nezukum tum.", "en")                  # -> "glory to gambling"
    assert not _ok("Gay va polua. vis nou, băi, gay va polare, my.s Ts Millions.", "ro")
    assert not _ok("mi-as fi tonic, T, GS, M.", "ro")
    assert not _ok("asteroid, munghay, inima, família pura do am.", "en")
    assert not _ok("Eu cuidarei isso, vou virar de mim.", "ro")
    for scrap in ("F.", "T.", "A."):                                   # "F." -> "Friends,"
        assert not _ok(scrap, "ro")
    for garble in ("Nezukum.", "Tsoom."):           # one word, capitalised as every phrase's first
        assert not _ok(garble, "en")


def test_gate_keeps_real_short_phrases_and_names():
    for text, lang in (("Amin!", "ro"), ("Amen.", "en"), ("Aleluia", "ro"), ("Adă.", "ro"),
                       ("Despina, Carmen, Dan, David, Cecilia.", "ro"), ("Costică Hariga.", "ro"),
                       ("nici medicamente, nici perfuzii, nici ambulanță.", "ro"), ("n-aveți voie?", "ro"),
                       ("s-a citit.", "ro"), ("It's hard.", "en"), ("a little child.", "en"),
                       ("Ragush Puha, beloved husband,", "en"), ("19, 1963, in Vicovisus, Souchava, Romania.", "en"),
                       ("He renounced. Certain deals came on his desk.", "en"), ("o viață.", "ro")):
        assert _ok(text, lang), text


def test_gate_cuts_a_garbled_sentence_and_keeps_the_rest():
    kept, why, cut = gate.screen("nezurkamai sum tsoom. It's okay to cry because you loved your dad.", "en", LEX)
    assert kept == "It's okay to cry because you loved your dad." and cut == ["nezurkamai sum tsoom."]
    assert gate.screen("slava ludum nezukum tum.", "en", LEX)[0] == ""


def test_todays_names_count_as_real_words():
    names = gate.names_from([{"ro": "Zăgrean Benny", "en": "Zagrean Benny"}])
    assert gate.features("Zăgrean Benny", "ro", LEX, names)["names"] == 2


def test_name_list():
    assert is_name_list("Despina, Carmen, Dan, David, Cecilia.") and is_name_list("Isaac, Emily, Michelle, Michael,")
    assert not is_name_list("Frați și surori, astăzi citim din Romani.") and not is_name_list("Amin.")


# --- language votes (idea 12) ---------------------------------------------------------
def test_garbled_romanian_does_not_leave_home_when_the_romanian_transcript_is_fine():
    lt = LangTracker("auto", initial="ro")
    # 11:42: a prayer the multilingual model wrote as English (score -0.5, 10 words)
    assert lt.update("Family PUV, the most as court and the first time.",
                     ro="Familia Puha, te rugăm frumos, ascultă-ne și în ziua de astăzi.") == "ro"
    for _ in range(3):
        assert lt.update("in this, they regard from us, my last day here",
                         ro="în acest timp ne uităm la tine, Doamne, ultima zi") == "ro"


def test_english_with_garbled_or_empty_romanian_leaves_home():
    lt = LangTracker("auto", initial="ro")
    assert lt.update("He renounced. Certain deals came on his desk and he said no to all of them.",
                     ro="Gay va polua. vis nou, băi, gay va polare, my.s Ts Millions.") == "en"
    lt = LangTracker("auto", initial="ro", strong_away=9)
    for _ in range(2):
        assert lt.update("He didn't care about the earthly things", ro="") == "ro"
    assert lt.update("Everything he did was for that purpose.", ro="") == "en"   # empty RO = English votes


def test_short_home_phrase_does_not_end_an_english_reading():
    lt = LangTracker("auto", initial="ro")
    lt.current = "en"
    assert lt.update("pasă de.") == "en"                     # 11:46: one diacritic, 2 words
    assert lt.update("cu ce, și.") == "en"
    assert lt.update("He didn't care about the earthly things, the earthly possessions") == "en"
    assert lt.update("Dumnezeu să vă binecuvânteze pe toți.") == "ro"   # 4+ words, clearly Romanian


# --- in the pipeline ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_developer_lists(tmp_path, monkeypatch):
    """The app reads the user's saved names and words (user/ in a dev checkout); these tests
    must not depend on what the developer saved there."""
    monkeypatch.setenv("LIVE_HOME", str(tmp_path / "home"))

def _live(tmp_path, translate=lambda t, *a, **k: f"EN: {t}"):
    live = Live(yaml.safe_load(open(CFG)), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    calls = []
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: (calls.append((t, a[:2])), translate(t, *a, **k))[1])
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return live, calls


def _clause(i, **kw):
    t = time.time() - 0.5
    return {"id": i, "ro": "", "multi": "", "en": "", "t_start": t - 1, "t_end": t, "t_emit": t + 0.1,
            "stt_s": 0.1, "audio_s": 1.0, **kw}


def _log(live):
    return [json.loads(l) for l in open(live.log_path)]


def test_garbled_phrase_is_logged_not_translated(tmp_path):
    live, calls = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="Gay va polua. vis nou, băi, gay va polare, my.s Ts Millions.", multi=""))
    rec = _log(live)[0]
    assert rec["outcome"] == "low_confidence" and rec["source"].startswith("Gay va polua") and not calls
    assert rec["reason"] == "non_words" and live.counts["low_confidence"] == 1
    live.lc["gate_min_real"] = 0                                  # off: translated as before
    live._handle(_clause(2, ro="Gay va polua. vis nou, băi, gay va polare, my.s Ts Millions.", multi=""))
    assert _log(live)[-1]["outcome"] == "ok" and calls


def test_garbled_sentence_is_cut_before_translation(tmp_path):
    live, calls = _live(tmp_path)
    live.lang.set_mode("en-ro")
    live._handle(_clause(1, multi="nezurkamai sum tsoom. It's okay to cry because you loved your dad."))
    rec = _log(live)[0]
    assert calls[0][0] == "It's okay to cry because you loved your dad."
    assert rec["outcome"] == "ok" and rec["gate_cut"] == ["nezurkamai sum tsoom."]


def test_the_other_recognisers_clean_transcript_is_used(tmp_path):
    live, calls = _live(tmp_path)
    live.lang.current = "en"                                      # auto, in an English stretch
    live._handle(_clause(1, ro="Slavă lui Dumnezeu.", multi="slava ludum nezukum tum."))
    rec = _log(live)[0]
    assert calls == [("Slavă lui Dumnezeu.", ("ro", "en"))] and rec["lang"] == "ro"
    assert rec["gate_switched"] == "slava ludum nezukum tum."


def test_empty_romanian_with_clean_english_is_translated_to_romanian(tmp_path):
    live, calls = _live(tmp_path)                                 # auto, home Romanian
    live._handle(_clause(1, ro="", multi="Hello, my name is Claudiu, I'm Dragoș' younger brother."))
    rec = _log(live)[0]
    assert rec["outcome"] == "ok" and rec["lang"] == "en" and rec["fallback"] == "english"
    assert calls[0][1] == ("en", "ro")
    live.lang.set_mode("ro-en")                                   # locked Romanian: still dropped
    live._handle(_clause(2, ro="", multi="He renounced. Certain deals came on his desk."))
    assert _log(live)[-1]["outcome"] == "empty_stt"


def test_empty_romanian_falls_back_to_an_unscored_word(tmp_path):
    live, calls = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="", multi="binecuvinteze."))     # scores 0: no diacritics, no common word
    rec = _log(live)[0]
    assert rec["outcome"] == "ok" and rec["fallback"] == "multi" and calls[0][0] == "binecuvinteze."


def test_list_of_names_is_shown_not_dropped_as_echo(tmp_path):
    live, _ = _live(tmp_path, translate=lambda t, *a, **k: t)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="Despina, Carmen, Dan, David, Cecilia."))
    live._handle(_clause(2, ro="Frați și surori, astăzi citim din Romani."))
    assert [r["outcome"] for r in _log(live)] == ["ok", "mt_echo"] and _log(live)[0]["passthrough"] is True


def test_log_record_keeps_both_transcripts(tmp_path):
    live, _ = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live._handle(_clause(1, ro="Dumnezeu este bun.", multi="Dumnezeu este bun."))
    rec = _log(live)[0]
    assert rec["ro"] == "Dumnezeu este bun." and rec["multi"] == "Dumnezeu este bun."
