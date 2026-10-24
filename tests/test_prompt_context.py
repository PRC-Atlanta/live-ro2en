"""Stream D: the Gemma prompt (context pairs, idioms, names, the Scripture hint) and its data.
Prompt construction only: no model is loaded."""
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))

import names as N            # noqa: E402
import scripture as S        # noqa: E402
from live import translate as T   # noqa: E402


@pytest.fixture
def tr(monkeypatch):
    prompts = []
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(
        generate=lambda model, tok, prompt, **k: prompts.append(prompt) or "out"))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=lambda **k: None))
    t = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    t.model, t.max_tokens, t.context, t.context_clauses = None, 200, [], 4
    t.reading = S.Reading()
    t.tok = types.SimpleNamespace(apply_chat_template=lambda m, **k: m[-1]["content"], encode=lambda s: s.split())
    t.prompts = prompts
    return t


@pytest.fixture
def gloss():
    g = T.Glossary(ROOT / "glossary.csv")
    g.set_session([{"ro": "Dragoș Puha", "en": "Dragoș Puha"}, {"ro": "Paraschiva", "en": "Paraschiva"},
                   {"ro": "Pavel", "en": "Pavel"}, {"ro": "Jonathan", "en": "Jonathan"}])
    return g


# --- idea 4: the previous (source, translation) pairs -----------------------------------
def test_prompt_shows_previous_pairs_and_asks_to_continue(tr):
    tr.context = [("Dacă nu știm asta, nu suntem", "If we don't know this, we are not", "ro")]
    p = tr._prompt("pregătiți pentru momentul morții.", "ro", "en", None)
    assert "Romanian: Dacă nu știm asta, nu suntem\nEnglish: If we don't know this, we are not" in p
    assert p.endswith("Translate the next piece into English:\npregătiți pentru momentul morții.")
    assert "continue that sentence" in T.SYSTEM[("ro", "en")]
    assert "never complete it with words" in T.SYSTEM[("ro", "en")]
    assert "never repeat" in T.SYSTEM[("en", "ro")]


def test_context_is_bounded_and_survives_a_switch(tr):
    for t in ("unu", "doi", "trei", "patru", "cinci"):
        tr.translate(t, "ro", "en", use_context=True)
    assert [c[0] for c in tr.context] == ["doi", "trei", "patru", "cinci"]
    tr.translate("but it wasn't right by the Bible", "en", "ro", use_context=True)
    assert "Romanian: cinci\nEnglish: out" in tr.prompts[-1] and "Romanian: patru" in tr.prompts[-1]
    assert "Romanian: trei" not in tr.prompts[-1]                    # only the last two after a switch
    tr.reset_context()
    tr.translate("șase", "ro", "en", use_context=True)
    assert "Previous pieces" not in tr.prompts[-1]


# --- idea 11: idioms and the pronoun for God ---------------------------------------------
def test_idioms_only_when_said(tr, gloss):
    p = tr._prompt("Așa a găsit El cu cale, te rugăm frumos, în repetate rânduri", "ro", "en", gloss)
    assert "- a găsi cu cale = to see fit" in p and "- te rugăm frumos = we humbly pray" in p
    assert "- în repetate rânduri = repeatedly" in p
    assert "liniuța" not in p and "dash" not in p
    p = tr._prompt("Mulțumesc, domnule, pentru biserică", "ro", "en", gloss)
    assert "domnule = sir" in p
    assert "Doamne" not in tr._prompt("Doamne, ajută-ne", "ro", "en", gloss).split("Translate")[0]
    assert gloss.idioms("cu cale", "en") == []
    assert "only for God" in T.SYSTEM[("ro", "en")] and "your father" in T.SYSTEM[("ro", "en")]


# --- ideas 6 and 14: names stay names ----------------------------------------------------
def test_todays_names_are_kept_and_bible_names_marked(tr, gloss):
    p = tr._prompt("Pavel și Paraschiva, împreună cu sora Paraschiva", "ro", "en", gloss)
    assert "Names (keep exactly as written): Paraschiva, Pavel" in p or \
        "Names (keep exactly as written): Pavel, Paraschiva" in p
    assert "- Pavel = Paul (only for the Bible figure)" in p
    assert "- sora = Sister (sora Paraschiva = Sister Paraschiva)" in p
    assert "Ilie = Elijah (only" not in tr._prompt("Ilie a spus", "ro", "en", gloss)   # no Ilie today
    p = tr._prompt("Nothing about names here.", "en", "ro", gloss)
    assert "Names" not in p


def test_glossary_place_names_are_kept():
    g = T.Glossary(ROOT / "glossary.csv")
    assert {"Suceava", "Vicovu de Sus", "Philadelphia"} <= set(g.names)
    assert "Ilie" not in g.names and "Pavel" not in g.names       # Bible names are translated


def test_english_spellings_map_back_to_the_names(tr, gloss):
    tr.translate("Dragosh Puha was born in Souchava, near Vicovisus. Ragosh and Potaskiva", "en", "ro",
                 gloss, use_context=True)
    p = tr.prompts[-1]
    assert "Dragoș Puha was born in Suceava, near Vicovu de Sus. Dragoș and Paraschiva" in p
    assert "Names (keep exactly as written):" in p and "Dragoș Puha" in p
    assert tr.context[-1][0].startswith("Dragoș Puha was born")


@pytest.mark.parametrize("heard, expect", [
    ("Draguish Purha said", "Dragoș Puha said"), ("Dragoskuha said", "Dragoș Puha said"),
    ("Brother Pua", "Brother Puha"), ("Suchava county", "Suceava county"),
    # left alone: common words, Bible names, lower case, other people
    ("Dragons and the Dragon", "Dragons and the Dragon"), ("Paul and Peter", "Paul and Peter"),
    ("dragosh in lower case", "dragosh in lower case"), ("Pastor, Prayer, Paradise", "Pastor, Prayer, Paradise"),
    ("Jonathan and Joseph", "Jonathan and Joseph"), ("Season of Victory", "Season of Victory"),
])
def test_map_back_is_conservative(heard, expect):
    assert N.map_back(heard, ["Dragoș Puha", "Paraschiva", "Suceava", "Vicovu de Sus", "Jonathan"]) == expect


# --- idea 3 (step 1) and 9: announced references and the book table ----------------------
def test_book_table_has_all_66_books():
    assert len(S.BOOKS) == 66
    en = {b.en for b in S.BOOKS}
    assert {"Hebrews", "1 Thessalonians", "Revelation", "Acts", "Psalm", "Isaiah"} <= en


@pytest.mark.parametrize("text, en, ro", [
    ("Evrei, capitolul 6, versetul 10", "Hebrews 6:10", "Evrei 6:10"),
    ("Romani, capitolul 14, de la versetul 7 până la versetul 9", "Romans 14:7-9", "Romani 14:7-9"),
    ("2 Corinteni 5 cu 7", "2 Corinthians 5:7", "2 Corinteni 5:7"),
    ("Psalmul 139, versetul 16", "Psalm 139:16", "Psalmul 139:16"),
    ("1 Tesaloniceni, capitolul 4, versetul 12, spune.", "1 Thessalonians 4:12", "1 Tesaloniceni 4:12"),
    ("In Hebrew thirteen seven says this", "Hebrews 13:7", "Evrei 13:7"),
    ("Matthew 16:24", "Matthew 16:24", "Matei 16:24"),
    ("First Corinthians thirteen four", "1 Corinthians 13:4", "1 Corinteni 13:4"),
    ("Psalm one hundred nineteen", "Psalm 119", "Psalmul 119"),
    ("34 cu 16 din cartea Poc spune", "34:16", "34:16"),               # book not understood
    ("pe Poc, capitolul 3, versetul 16.", "3:16", "3:16"),
])
def test_references_are_found(text, en, ro):
    r = S.find(text)
    assert r is not None and (r.name("en"), r.name("ro")) == (en, ro)


@pytest.mark.parametrize("text", ["I lost my job 3 years ago", "in Hebrew the word means grace",
                                  "romani 3", "Ioan a venit", "am venit cu 7 oameni"])
def test_no_reference_in_ordinary_speech(text):
    assert S.find(text) is None


def test_reading_hint_follows_the_next_chunks(tr, gloss):
    tr.translate("Evrei 13, versetul 7, spune așa:", "ro", "en", gloss, use_context=True)
    assert 'Write the reference as "Hebrews 13:7"' in tr.prompts[-1]
    tr.translate("Aduceți-vă aminte de mai marii voștri,", "ro", "en", gloss, use_context=True)
    p = tr.prompts[-1]
    assert "Scripture: The speaker is reading Hebrews 13:7 (Evrei 13:7)" in p and "NKJV" in p
    assert "do not run ahead" in p and "Write the reference" not in p
    tr.translate("versetul 8: Isus Hristos este același", "ro", "en", gloss, use_context=True)
    assert "Hebrews 13:8" in tr.prompts[-1]
    for _ in range(S.CHUNKS):
        tr.translate("și apoi altceva", "ro", "en", gloss, use_context=True)
    assert "Scripture" not in tr.prompts[-1]                      # the reading is over


def test_reading_hint_en_to_ro_and_unclear_book(tr, gloss):
    tr.translate("In Hebrew thirteen seven says this, remember those", "en", "ro", gloss, use_context=True)
    assert 'Write the reference as "Evrei 13:7"' in tr.prompts[-1] and "Cornilescu" in tr.prompts[-1]
    tr.reset_context()
    tr.translate("34 cu 16 din cartea Poc spune următoarele", "ro", "en", gloss, use_context=True)
    assert "write only the numbers, never name a book" in tr.prompts[-1]


# --- idea 9: glossary rows, both directions ----------------------------------------------
@pytest.mark.parametrize("text, lang, pair", [
    ("slujba de priveghi", "ro", ("priveghi", "wake service")),
    ("slujba de privechi", "ro", ("privechi", "wake service")),
    ("lângă sicriu", "ro", ("sicriu", "coffin")),
    ("cu glasul unui arhanghel", "ro", ("arhanghel", "archangel")),
    ("venirea mirelui Isus", "ro", ("mirelui", "the bridegroom")),
    ("citim necrologul", "ro", ("necrologul", "the obituary")),
    ("God's kingdom and the kingdom of God", "en", ("Împărăția lui Dumnezeu", "the kingdom of God")),
    ("he said to his disciples", "en", ("ucenici", "disciples")),
    ("let him deny himself", "en", ("să se lepede de sine", "deny himself")),
    ("God is faithful", "en", ("credincios", "faithful")),
    ("a prophet's reward", "en", ("răsplată", "reward")),
])
def test_new_glossary_rows(text, lang, pair):
    g = T.Glossary(ROOT / "glossary.csv")
    assert pair in [(r["ro"], r["en"]) for r in g.relevant(text, lang)]


def test_en_to_ro_skips_recogniser_spellings_and_inflections():
    g = T.Glossary(ROOT / "glossary.csv")
    assert [r["ro"] for r in g.relevant("the wake service tonight", "en")] == ["priveghi"]
    assert [r["ro"] for r in g.relevant("the obituary", "en")] == ["necrolog"]


def test_prompt_stays_short(tr, gloss):
    """A busy chunk (glossary, idiom, names, hint, 4 pairs) stays well under ~2.5k characters."""
    tr.context = [("împreună cu soția sa, Paraschiva,", "together with his wife, Paraschiva,", "ro")] * 4
    p = T.SYSTEM[("ro", "en")] + tr._prompt("mama noastră, au crescut toți cei 14 copii, te rugăm frumos.",
                                            "ro", "en", gloss, S.Reading().hint(S.find("Evrei 13:7"), "ro", "en"))
    assert len(p) < 2500
