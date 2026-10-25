"""Stream H: Bible verses recognised by their words, captioned with the published text
(data/bible: WEB for RO→EN, Cornilescu for EN→RO). The cases are pieces from the services of
2026-10-23/24 as the recogniser wrote them. No model is loaded."""
import gzip
import json
import pathlib
import sys
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))

import scripture as S        # noqa: E402
import verses as V           # noqa: E402
from live import translate as T   # noqa: E402


@pytest.fixture(scope="module")
def bible():
    return V.Matcher(background=False).bible


@pytest.fixture
def m(bible):
    matcher = V.Matcher(background=False)
    assert matcher.bible is bible                     # one copy shared by every translator
    return matcher


def read(m, pieces, src="ro", tgt="en"):
    """Feed pieces in order through the reference detector and the matcher, like the translator."""
    reading, out = S.Reading(), []
    for p in pieces:
        out.append(m.match(p, src, tgt, reading.update(p)))
    return out


# --- the data -----------------------------------------------------------------------------
def test_texts_are_complete_and_aligned(bible):
    assert len(bible.refs) > 31000 and len(S.BOOKS) == 66
    i = bible.index[(19, 23, 1)]                          # Psalm 23:1: no title in the WEB verse
    assert bible.text["en"][i] == "The LORD is my shepherd; I shall lack nothing."
    assert bible.text["ro"][i] == "Domnul este Păstorul meu: nu voi duce lipsă de nimic."
    i = bible.index[(43, 11, 25)]                         # John 11, missing from the Cyrillic source
    assert "Învierea și Viața" in bible.text["ro"][i]
    i = bible.index[(1, 35, 18)]                          # ro_vdc's inlined notes are dropped
    assert "(" not in bible.text["ro"][i] and "Ben-Oni" in bible.text["ro"][i]
    assert bible.lexicon[("ferice", "blessed")] > 0.3


def test_data_is_bundled_and_documented():
    sys.path.insert(0, str(ROOT / "packaging"))
    import app_files
    assert {"data/bible/verses.tsv.gz", "data/bible/lexicon.tsv.gz", "data/bible_books.csv"} <= set(app_files.DATA)
    readme = (ROOT / "data" / "bible" / "README.md").read_text()
    assert "public domain" in readme.lower() and "World English Bible" in readme and "Cornilescu" in readme
    with gzip.open(ROOT / "data" / "bible" / "verses.tsv.gz", "rt", encoding="utf-8") as f:
        assert f.readline().split("\t") == ["book", "chapter", "verse", "ro", "web", "kjv\n"]


# --- recognising verses -------------------------------------------------------------------
def test_psalm_71_3_with_recognition_errors(m):
    out = read(m, ["Fii o stâncă de adăpost pentru mine.", "unde să pot fugi totdeauna.",
                   "ai hotărât să mă scap.", "căci tu ești stânca.", "și cetățenia. mea."])
    assert [o.ref for o in out] == ["Psalm 71:3"] * 5
    assert out[0].caption == "Be to me a rock of refuge"
    assert "Give the command to save me" in out[2].caption            # not "cast me off"
    assert out[4].caption == "and my fortress."
    assert out[0].log() == {"ref": "Psalm 71:3", "score": 1.0, "words": 7, "near": False}


def test_a_verse_in_one_piece_and_the_words_around_it(m):
    o = m.match("Și Pavel spunea foarte frumos: pentru mine a trăi este Hristos și a muri este un câștig")
    assert o.ref == "Philippians 1:21" and o.caption == "For to me to live is Christ, and to die is gain."
    assert o.before == "Și Pavel spunea foarte frumos"                # translated by the model
    m.reset()
    o = m.match("Căci El știe din ce suntem făcuți, își aduce aminte că suntem țărână, dragii mei, "
                "și asta ne smerește.")
    assert o.ref == "Psalm 103:14" and o.caption == "For he knows how we are made. He remembers that we are dust."
    assert o.after == "dragii mei, și asta ne smerește"


def test_does_not_run_ahead_or_repeat(m):
    out = read(m, ["fiindcă atât de mult a iubit Dumnezeu lumea.", "că a dat pe singurul său fiu,",
                   "pentru ca oricine crede în El să nu."])
    assert [o.caption for o in out] == ["For God so loved the world,", "that he gave his only born Son,",
                                        "that whoever believes in him should not"]
    assert "eternal" not in " ".join(o.caption for o in out)           # not said yet
    assert m.match("pentru ca oricine crede în El să nu.") is None      # said again: the model translates


def test_announced_reference_allows_short_pieces(m):
    out = read(m, ["Evrei 9, versetul 27, oamenilor le este rânduit să moară o singură dată."])
    assert out[0].ref == "Hebrews 9:27" and out[0].near
    assert out[0].before == "Evrei 9, versetul 27"
    assert "appointed for men to die once" in out[0].caption


def test_english_quote_gives_cornilescu(m):
    o = m.match("You shall love the Lord your God with all your heart, with all your soul.", "en", "ro")
    assert o.ref == "Deuteronomy 6:5"
    assert o.caption.startswith("Să iubești pe Domnul Dumnezeul tău cu toată inima ta")


@pytest.mark.parametrize("text", [
    "te rugăm în numele lui Isus Hristos, să-i întărești tu, Doamne, în momentele acestea.",
    "Dumnezeu. Mesajul pe care l-am primit de la sora Paraschiva.",
    "vă încurajăm să vă încredeți în Domnul.",
    "mă voi duce la Dumnezeul pe care l-am slujit, mă voi întâlni cu Tatăl tău.",
    "Când vezi un orfan, să nu treci pe lângă el, să-i dai mâna să-l saluți.",
    "pentru slava lui Dumnezeu, se face o slujbă duhovnicească.",
])
def test_common_bible_language_is_not_a_verse(m, text):
    assert m.match(text) is None


def test_a_phrase_of_several_verses_is_not_one_of_them(m):
    assert read(m, ["and it can be a day of joy where we enter into the", "kingdom of heaven."], "en", "ro") == \
        [None, None]                                                    # Matthew 5:20, 7:21, 18:3, 19:23 ...


def test_language_switch_and_reset_forget_the_reading(m):
    assert m.match("Fii o stâncă de adăpost pentru mine.") is not None
    assert m.match("and with all your mind.", "en", "ro") is None       # the window starts again
    m.reset()
    assert m.match("ai hotărât să mă scap.") is None                     # alone: too short to be sure


def test_not_ready_means_no_match(tmp_path):
    assert V.Matcher(tmp_path / "missing.tsv.gz").match("Fii o stâncă de adăpost pentru mine.") is None


# --- the translator and the pipeline ------------------------------------------------------
@pytest.fixture
def tr(monkeypatch, m):
    prompts = []
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(
        generate=lambda model, tok, prompt, **k: prompts.append(prompt) or "MODEL"))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=lambda **k: None))
    t = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    t.model, t.max_tokens, t.context, t.context_clauses = None, 200, [], 2
    t.reading, t.verses, t.last_verse = S.Reading(), m, None
    t.tok = types.SimpleNamespace(apply_chat_template=lambda m, **k: m[-1]["content"], encode=lambda s: s.split())
    t.prompts = prompts
    return t


def test_translator_captions_the_verse_and_translates_the_rest(tr):
    out = tr.translate("Și Pavel spunea foarte frumos: pentru mine a trăi este Hristos și a muri este un câștig",
                       "ro", "en", use_context=True)
    assert out == "MODEL For to me to live is Christ, and to die is gain."
    assert tr.prompts[-1].endswith("Și Pavel spunea foarte frumos")     # only the words around the verse
    assert tr.last_verse["ref"] == "Philippians 1:21"
    assert tr.context[-1][1] == out                                      # context for the next piece
    assert tr.translate("Desigur că pot rămâne întrebări.", "ro", "en", use_context=True) == "MODEL"
    assert tr.last_verse is None
    tr.reset_context()
    assert tr.verses.history == type(tr.verses.history)()


def test_pipeline_logs_the_verse_and_skips_the_guard(tmp_path, monkeypatch):
    from live.app import Live
    import guards
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    caption = "The LORD is my shepherd; I shall lack nothing."
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: caption,
                                    last_verse={"ref": "Psalms 23:1", "score": 1.0, "words": 9, "near": False})
    monkeypatch.setattr(guards, "check", lambda *a, **k: pytest.fail("a verse is not re-translated"))
    live.lang.set_mode("ro-en")
    live._handle({"id": 1, "ro": "Domnul este Păstorul meu, nu voi duce lipsă de nimic.", "multi": "", "en": "",
                  "t_start": 0.0, "t_end": 1.0, "t_emit": 1.1, "stt_s": 0.1, "audio_s": 1.0})
    rec = [json.loads(line) for line in open(live.log_path)][-1]
    assert rec["outcome"] == "ok" and rec["verse"]["ref"] == "Psalms 23:1" and rec["translation"] == caption


# --- 2026-10-25 -------------------------------------------------------------------------------
def test_a_paraphrase_is_not_spliced_with_the_verse(m):
    """"multe versete spun că el ascultă rugăciunea": no reference, the verse starts after
    "că el": the speaker's own sentence, not Psalm 65:2."""
    out = read(m, ["și 139 și multe versete din Biblie sunt care.",
                   "că el ascultă rugăciunea, de aceea, toți oamenii vor veni la el."])
    assert out[-1] is None


def test_short_verse_words_are_held_for_the_next_caption(m):
    """Matthew 28:20: "Și iată." between two verse pieces is not translated ("And behold."
    before the verse's own "Behold")."""
    out = read(m, ["Matei 28, versetul 19.", "Duceți-vă și faceți ucenici din toate neamurile.",
                   "botezindu-i în numele Tatălui și al Fiului.",
                   "și al Duhului Sfânt, și învățați-i să păzească.", "tot ce v-am poruncit.", "Și iată.",
                   "eu sunt cu voi în toate zilele."])
    assert out[5] is not None and out[5].caption == "" and out[5].log()["held"]
    assert "Behold" in out[6].caption


def test_a_verse_matched_one_piece_late_redoes_the_previous_caption(m):
    """Daniel 3:18: "că noi vom sluji Dumnezeu." (the recogniser lost "nu") went to the
    translator ("we will serve God."); the next piece matches: caption from there, in place."""
    out = read(m, ["dar mai frumos, versetul 18 spune așa.", "și chiar dacă nu ne va scoate.",
                   "să știi, împărate,", "că noi vom sluji Dumnezeu.",
                   "Dumnezeilor tăi și nici nu ne vom închina chipului tău de aur."])
    caps = " ".join(v.caption for v in out if v)
    assert "we will not serve" in caps and any(v and v.replace_prev for v in out)


def test_counts_are_not_a_verse(m):
    """"de două ori sau de trei ori" matched Job 33:29 ("twice, yes three times, with a man")
    in the middle of a missionary's story (2026-10-25)."""
    out = read(m, ["planul pe care eu o am cu viața ta.",
                   "și am zis, Doamne, da, nu-i mai bine ca să. vină o dată pe an sau de două ori sau de trei ori pe an."])
    assert out[-1] is None
