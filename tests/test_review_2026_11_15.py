"""Lessons of the Sunday 2026-11-15 morning service (docs/translation_ideas.md, the bullets dated
"2026-11-15 morning" under ideas 3, 5, 6, 9, 11, 12, 20, 25, 28 and 35). The Romanian lines are the
ones seen live; people's names are placeholders."""
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
import guards as G                                       # noqa: E402
import scripture as S                                    # noqa: E402
import verses as V                                       # noqa: E402
from live import engine, gate                            # noqa: E402
from live import translate as T                          # noqa: E402
from live.app import Live                                # noqa: E402
from live.pipeline import christmas_to_hristos, cut_word, non_english, open_end, seam_word   # noqa: E402

LEX = gate.lexicon()


class PP:
    last_error = None

    def __init__(self):
        self.shown = []

    def show(self, text):
        self.shown.append(text)
        return True

    def clear(self):
        return True


def _live(tmp_path, translate=lambda t, src, tgt, *a, **k: f"[{tgt}] {t}"):
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.seen = []
    live.tr = types.SimpleNamespace(translate=lambda t, src, tgt, *a, **k: live.seen.append((src, tgt, t)) or translate(t, src, tgt))
    live.pp = PP()
    return live


def _clause(i, t0, t1, ro="", multi=""):
    return {"id": i, "ro": ro, "multi": multi, "en": "", "t_start": t0, "t_end": t1, "t_emit": t1 + 0.1,
            "stt_s": 0.1, "audio_s": t1 - t0}


def _log(live):
    return [json.loads(line) for line in open(live.log_path)] if live.log_path.exists() else []


# --- 1. idea 35: the object, subject or «nu» in the next piece ---------------------------------
@pytest.mark.parametrize("text", ["El este mijlocitorul, singurul mijlocitor. Maria.",
                                  "lumea îl urăște pe Hristos pentru că iubește.",
                                  "Nimicul în împărăție nu este forma.",
                                  "să fim egoiști, așa ne învață."])
def test_a_piece_that_needs_the_next_one_waits(text):
    assert open_end(text)


@pytest.mark.parametrize("text", ["Dumnezeu ne iubește.", "Să ne rugăm. Amin.", "El este bun.", "Nu este așa?",
                                  "Tatăl îl iubește pe Fiul.", "Domnul să vă binecuvânteze."])
def test_a_complete_piece_goes_at_once(text):
    assert not open_end(text)


@pytest.mark.parametrize("a,b,joined", [
    ("El este mijlocitorul, singurul mijlocitor. Maria.", "nu Maria, nu toți sfinții, nu altcineva.",
     "El este mijlocitorul, singurul mijlocitor. Maria. nu Maria, nu toți sfinții, nu altcineva."),
    ("lumea îl urăște pe Hristos pentru că iubește.", "păcatul. Și un om a spus adevărul.",
     "lumea îl urăște pe Hristos pentru că iubește. păcatul. Și un om a spus adevărul."),
    ("Nimicul în împărăție nu este forma.", "eșecului, este expresia plinătății.",
     "Nimicul în împărăție nu este forma. eșecului, este expresia plinătății."),
    ("să fim egoiști, așa ne învață.", "stăpânitorul lumii acesteia.",
     "să fim egoiști, așa ne învață. stăpânitorul lumii acesteia."),
])
def test_the_piece_and_the_next_one_are_translated_together(tmp_path, a, b, joined):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0, hold_slow_s=30, hold_max_s=20, max_chunk_s=0)
    t = time.time() - 20
    live._on_clause(_clause(1, t, t + 3, ro=a))
    assert live._pending is not None
    live._on_clause(_clause(2, t + 3.2, t + 6, ro=b))        # starts 0.2 s later
    live._drain()
    assert [r["source"] for r in _log(live)] == [joined]


def test_after_a_pause_the_next_piece_is_a_new_thought(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0, hold_slow_s=30, hold_max_s=20, max_chunk_s=0)
    t = time.time() - 20
    live._on_clause(_clause(1, t, t + 3, ro="lumea îl urăște pe Hristos pentru că iubește."))
    live._on_clause(_clause(2, t + 5, t + 8, ro="Haideți să ne rugăm împreună acum."))   # 2 s pause
    live._drain()
    assert [r["source"] for r in _log(live)] == ["lumea îl urăște pe Hristos pentru că iubește.",
                                                 "Haideți să ne rugăm împreună acum."]


# idea 5: «n-» answered only by an English tag question
def test_a_negation_kept_only_in_a_tag_question_is_lost():
    issues = G.mismatches("n-a învins cu 22.000 de persoane, nu?", "I defeated them with 22,000 people, didn't I?",
                          "ro", "en")
    assert issues["negation"]["source"] and not issues["negation"]["translation"] and issues["negation"]["tag"]
    assert "main clause" in G._note(issues, "", "ro") and G._fallback_may_fix(issues)
    assert not G.mismatches("n-a învins cu 22.000 de persoane, nu?",
                            "He did not win with 22,000 people, did he?", "ro", "en")


def test_the_guard_retries_and_takes_a_version_that_keeps_the_not():
    class Tr:
        context, takes_note = [], True

        def translate(self, text, src, tgt, gloss=None, use_context=False, note=""):
            return "He did not win with 22,000 people, right?" if "main clause" in note else \
                "I defeated them with 22,000 people, didn't I?"
    out, rec = G.check(Tr(), "n-a învins cu 22.000 de persoane, nu?", "I defeated them with 22,000 people, didn't I?",
                       "ro", "en")
    assert out.startswith("He did not win") and rec["guard"]["kept"] == "note"


def test_a_negative_statement_turned_into_a_question_is_flagged():
    src = "N-ai vrea să faci parte din Laodiceea, care e tot biserică."
    issues = G.mismatches(src, "Wouldn't you like to be part of Laodicea, which is also the church.", "ro", "en")
    assert issues["negation"]["translation"] == "question" and "statement" in G._note(issues, src, "ro")
    assert not G.mismatches(src, "You would not want to be part of Laodicea, which is also a church.", "ro", "en")
    assert not G.mismatches("N-ai vrea să faci parte din Laodiceea?",
                            "Wouldn't you like to be part of Laodicea?", "ro", "en")   # a real question


# --- 2. idea 9: church terms -------------------------------------------------------------------
@pytest.mark.parametrize("src,ro,en", [
    ("aceiași oameni după-amiaza să zică: Răstignește-l!", "răstignește", "crucify"),
    ("biserica, firească, care a căzut din iubire", "firească", "carnal / worldly"),
    ("să-i lăsăm în patimile și în voia lor", "patimile", "the passions / vices"),
    ("Frica de oameni e o cursă", "cursă", "snare"),
    ("am avut o răpire sufletească", "răpire", "rapture"),
    ("să producă în noi acest chip al lui Hristos", "chip", "image / likeness"),
    ("să strălucească lumina slavei Domnului Isus", "slavei", "of the glory"),
])
def test_glossary_rows(src, ro, en):
    assert (ro, en) in [(r["ro"], r["en"]) for r in T.Glossary().relevant(src)]


@pytest.mark.parametrize("src,start", [
    ("ferească Domnul de predicatori care nu știu că sunt iubiți", "God save us from"),
    ("ne mai înviorăm", "to be refreshed"),
    ("s-au săturat oamenii de vopsele peste rugină", "are fed up with"),
    ("s-a dus vremea cultelor importanței, personalității", "the cult of"),
    ("te voi păzi, nefăcându-ți parte de ceasul încercării", "sparing you"),
    ("Domnule doctor, ce mai faceți?", "doctor / sir"),
    ("să-l lăudăm pe celui prea înalt", "the Most High"),
])
def test_idiom_rows(src, start):
    assert any(r["en"].startswith(start) for r in T.Glossary().idioms(src)), src


def test_a_nu_este_question_stays_a_question():
    assert any("rhetorical question" in n for n in G.notes("creștem în această realitate. Nu este extraordinar.", "ro"))
    assert not any("rhetorical" in n for n in G.notes("Nimicul în împărăție nu este forma.", "ro"))
    assert "stays a question" in T.SYSTEM[("ro", "en")]


# --- 3. idea 11: sir / Mr., and You/He lower-cased for people ----------------------------------
@pytest.mark.parametrize("src", ["Și am zis, dar de ce, domnul B? Am vândut toată averea.",
                                 "Sunați-l pe Domnul din America să vă spună ce trebuie să faceți.",
                                 "a spus, domnule, dar ce vrei de la mine", "Domnule doctor, mulțumesc"])
def test_a_man_called_domnul_is_sir_not_the_lord(src):
    assert any("never the Lord" in n for n in G.notes(src, "ro"))


@pytest.mark.parametrize("src", ["Domnul din ceruri ne vede.", "Domnul Isus ne iubește.", "Doamne, ajută-ne."])
def test_god_called_domnul_has_no_such_note(src):
    assert not any("never the Lord" in n for n in G.notes(src, "ro"))


def test_you_and_he_for_a_person_are_lower_case():
    out = G.lower_people("he said, sir, but what do You want from me, what do You want me to give You because. You "
                         "saved my life", "a spus, domnule, dar ce vrei de la mine, ce vrei să-ți dau pentru că. "
                         "mi-ai salvat viața", "")
    assert out == ("he said, sir, but what do you want from me, what do you want me to give you because. you "
                   "saved my life")
    assert G.lower_people("but that you serve Him. And by doing so", "ci să-l slujești. Și făcând așa",
                          "să ierți pe aproapele tău.") == "but that you serve him. And by doing so"


@pytest.mark.parametrize("out,src,prev", [
    ("that we lift Him up before God", "nu-l ridicăm către Dumnezeu", ""),
    ("but He also offers us love.", "dar El ne și oferă iubire.", "Dumnezeu nu e doar iubire, El este iubire."),
    ("let His name be glorified", "și astăzi slăviți să fie numele Lui", ""),
    ("and we praise Him", "și Îl lăudăm", "pe Domnul Isus Hristos"),
])
def test_you_and_he_stay_when_god_is_named(out, src, prev):
    assert G.lower_people(out, src, prev) == out


def test_the_pipeline_lowers_them_and_logs_it(tmp_path):
    live = _live(tmp_path, translate=lambda t, src, tgt: "he said, what do You want from me, You saved my life")
    live.lang.set_mode("ro-en")
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="a spus, domnule, ce vrei de la mine, mi-ai salvat viața."))
    rec = _log(live)[-1]
    assert rec["translation"] == "he said, what do you want from me, you saved my life"
    assert rec["pronouns_lowered"].startswith("he said, what do You")


# --- 4. idea 3: verses ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def matcher():
    return V.Matcher(background=False)


def _read(m, pieces):
    m.reset()
    reading, out = S.Reading(), []
    for p in pieces:
        out.append(m.match(p, "ro", "en", reading.update(p), reading.announced))
    return out


def test_a_verse_caption_ends_where_the_speaker_stopped(matcher):
    out = _read(matcher, ["Isaia 56, versetul 1.", "Păziți!", "ce este drept. faceți ce este bine."])
    assert out[-1] is not None and not out[-1].caption.rstrip(" ,").endswith(("for my", "my"))
    out = _read(matcher, ["Isaia 56, versetul 2.", "ferice de omul care face lucrul acesta și de fiul omului.",
                          "care rămâne statornic în el."])
    assert out[-1].caption.rstrip().endswith("holds it fast;")


def test_a_verse_matched_one_part_late_replaces_the_part_before_it(tmp_path):
    """John 10:9: «El zicea: Eu sunt ușa, | dacă intră cineva prin mine…» — the first part went to the
    translator, the second matched the verse from «Eu sunt ușa»: "I am the door" was on screen twice."""
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    calls = []

    def tr(t, src, tgt, gloss=None, use_context=False):
        calls.append(t)
        if len(calls) == 1:
            live.tr.last_verse = None
            return "He said, “I am the door,"
        live.tr.last_verse = {"ref": "John 10:9", "score": 0.92, "words": 11, "near": False, "replace_prev": True}
        return "I am the door. If anyone enters in by me, he will be saved,"
    live.tr = types.SimpleNamespace(translate=tr, last_verse=None, context=[])
    t = time.time() - 20
    live._handle(_clause(1, t, t + 4, ro="El zicea: Eu sunt ușa, dacă intră cineva prin mine și va fi mântuit."))
    rec = _log(live)[-1]
    assert rec["translation"] == "I am the door. If anyone enters in by me, he will be saved,"
    assert rec["verse_dropped"] == ["He said, “I am the door,"]


@pytest.mark.parametrize("pieces,chapter,verse", [
    (["Domnul i s-a arătat în Geneza.", "26 cu 24. Domnul i s-a arătat chiar în noaptea aceea."], 26, 24),
    (["și spune cuvântul așa, Ioan 1, 18 Neni.", "cu 18, Nimeni n-a văzut vreodată pe Dumnezeu"], 1, 18),
])
def test_a_reference_survives_a_piece_break(pieces, chapter, verse):
    r = S.Reading()
    refs = [r.update(p) for p in pieces]
    assert refs[-1].chapter == chapter and refs[-1].verse == verse and r.announced


def test_a_book_name_at_the_end_of_a_piece_needs_chapter_and_verse_next():
    r = S.Reading()
    r.update("l-am salutat pe fratele Ioan.")
    assert r.update("12 ucenici au venit atunci") is None


@pytest.mark.parametrize("src,out,chapter,verse,want", [
    ("26 cu 24. Domnul i s-a arătat", "26 with 24. The Lord appeared", None, None, "26:24. The Lord appeared"),
    ("cu 18, Nimeni n-a văzut", "with 18, No one has ever seen God", 1, 18, "1:18, No one has ever seen God"),
    ("din Coloseni 1 cu 13 și 14", "from Colossians 1 with 13 and 14", None, None, "from Colossians 1:13 and 14"),
    ("cu 3 oameni", "with 3 people", 5, 7, "with 3 people"),        # not the verse in force: left alone
])
def test_n_cu_m_is_chapter_and_verse(src, out, chapter, verse, want):
    assert T.cu_reference(src, out, chapter, verse) == want


def test_a_full_stop_inside_n_cu_m_is_the_recogniser_s():
    assert T._CU_DOT.sub(r"\1 cu \2", "din Cosen 1 cu. 13 și 14") == "din Cosen 1 cu 13 și 14"


# --- 5. idea 28: «Christmas» and the boosted phrases ---------------------------------------------
def test_christmas_in_romanian_is_hristos():
    assert christmas_to_hristos("Christmas s-a înviat!") == "Hristos s-a înviat!"
    assert christmas_to_hristos("ne cheamă pe fiecare. Christmas. Fiul lui Dumnezeu") == \
        "ne cheamă pe fiecare. Hristos. Fiul lui Dumnezeu"
    assert christmas_to_hristos("Operation Christmas Child, cutiile") == "Operation Christmas Child, cutiile"
    assert christmas_to_hristos("un cadou de Christmas", "de Crăciun") == "un cadou de Christmas"


def test_church_phrases_boosted():
    for p in ("mărit să fie numele Lui", "chipul lui Hristos", "slava Domnului", "slavei Domnului",
              "pocăit", "pocăită", "pocăiți"):
        assert p in engine.CHURCH_PHRASES
    assert "păcălit" not in engine.CHURCH_PHRASES


# --- 6. idea 6: people's names ---------------------------------------------------------------------
@pytest.mark.parametrize("text,kept", [
    ("Îl salut pe fratele Ilie, pe fratele.", ["Ilie"]),
    ("eu și împreună cu Iosif Popescu.", ["Iosif Popescu"]),
    ("pe Moise, Ilie Popescu. și ne bucurăm de copii", ["Ilie Popescu", "Moise"]),
    ("fratele Dani și fratele Iosif vor fi în mijlocul nostru", ["Iosif"]),
    ("Fratele Benu și fratele Ilie Puha a promis", ["Ilie Puha"]),
])
def test_a_person_with_a_bible_name_keeps_it(text, kept):
    assert T.persons(text, T.Glossary()) == kept


@pytest.mark.parametrize("text", ["Dumnezeu i-a vorbit lui Moise pe munte", "Ilie Tișbitul a zis",
                                  "Domnul Isus Hristos", "Iosif a fost vândut de frații lui"])
def test_the_bible_figure_is_left_to_the_glossary(text):
    assert T.persons(text, T.Glossary()) == []


# --- 7. idea 12: Romanian heard as English ---------------------------------------------------------
def test_non_english_words_are_counted():
    assert non_english("Get one coborila up cu tre de woman.") >= 2
    assert non_english("She shall kut the children.") == 1
    assert non_english("Praise the Lord for this day, brother Dragoș.") == 0


@pytest.mark.parametrize("multi", ["She shall kut the children.", "Get one coborila up cu tre de woman."])
def test_a_lone_english_piece_with_foreign_words_is_not_captioned_in_romanian(tmp_path, multi):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="", multi=multi))   # the Romanian recogniser heard nothing
    assert not any(tgt == "ro" for _, tgt, _ in live.seen)
    assert _log(live)[-1]["outcome"] != "ok"


def test_a_clean_english_sentence_the_romanian_model_missed_still_goes(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="", multi="Praise the Lord for this wonderful day."))
    assert live.seen[-1][:2] == ("en", "ro")


def test_english_mode_drops_a_piece_with_two_foreign_words(tmp_path):
    live = _live(tmp_path)
    live.lang.current = "en"
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="", multi="Get one coborila up cu tre de woman."))
    rec = _log(live)[-1]
    assert rec["outcome"] == "low_confidence" and rec["reason"] == "not_english"


# --- 8. idea 20: 2-3 word echoes -----------------------------------------------------------------
@pytest.mark.parametrize("a,b", [("să se atingă de trupul ei de mădularele ei.", "madularele ei."),
                                 ("intră în aritmie și te îmbolnăvești de inimă.", "îmbolnăvești de inimă.")])
def test_a_short_echo_of_the_previous_piece_s_end_is_dropped(tmp_path, a, b):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro=a))
    live._handle(_clause(2, t + 3.03, t + 4, ro=b))
    assert [r["outcome"] for r in _log(live)] == ["ok", "echo_word"]


def test_new_words_after_the_same_end_are_kept(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="să se atingă de trupul ei de mădularele ei."))
    live._handle(_clause(2, t + 3.03, t + 4, ro="și de inima ei."))
    assert [r["outcome"] for r in _log(live)] == ["ok", "ok"]


# --- 9. idea 25: a word cut in two -----------------------------------------------------------------
@pytest.mark.parametrize("a,b,joined", [
    ("înainte de a parca.", "parcurge câteva versete", "înainte de a parcurge câteva versete"),
    ("această nepărere.", "neprihănire, Noi suntem", "această neprihănire, Noi suntem"),
    ("o realitate viabilă autor.", "autentică, e un mediu.", "o realitate viabilă autentică, e un mediu."),
    ("cu un scop precis să se multe.", "multiplice această iubire", "cu un scop precis să se multiplice această iubire"),
])
def test_a_cut_word_is_carried_by_the_next_piece(a, b, joined):
    assert cut_word(a, b, LEX["ro"]) == joined


@pytest.mark.parametrize("a,b", [("Este bine.", "Binecuvântat să fie Domnul"), ("suntem credincioși.", "Credința noastră"),
                                 ("că ești bogat sau sărac.", "sărat, tu lupți"), ("trăiesc cu un Dumnezeu mare.", "măriți, ei sunt"),
                                 ("trebuie să învețe diferența.", "dintre vocea lui Dumnezeu"), ("era erezie.", "eretic la marginea"),
                                 ("fiind ordinat și păstrat.", "păstor, evanghelist"),
                                 ("slavă Domnul.", "Domnului Isus"), ("Amin.", "Aleluia")])
def test_two_real_words_are_left_alone(a, b):
    assert cut_word(a, b, LEX["ro"]) is None


def test_the_previous_caption_is_redone_without_the_cut_word(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0)
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="aș vrea să spunem un cuvânt înainte de a parca."))
    live._handle(_clause(2, t + 3.2, t + 6, ro="parcurge câteva versete: Dumnezeu mă iubește."))
    recs = _log(live)
    assert recs[-1]["source"] == "aș vrea să spunem un cuvânt înainte de a parcurge câteva versete: Dumnezeu mă iubește."
    assert recs[-1]["cut_word"]["previous"].endswith("parca.")


# idea 25, the recogniser's side: ContextSTT saw the cut word again whole (bench 2026-11-15: PR 9's
# spelling rule caught 2 of 99 boundaries with an error; most were the same word said twice)
@pytest.mark.parametrize("a,b,seam,joined", [
    ("va fi atât de important.", "importantă. cât", ["important.", "importantă."], "va fi atât de importantă. cât"),
    ("au pus la dispoziție.", "dispoziția noastră toate", ["dispoziție.", "dispoziția"], "au pus la dispoziția noastră toate"),
    ("și încep să dea ex.", "exemple de ce", ["ex.", "exemple"], "și încep să dea exemple de ce"),
])
def test_a_seam_from_the_recogniser_carries_the_whole_word(a, b, seam, joined):
    assert cut_word(a, b, LEX["ro"]) is None              # the spelling rule alone leaves these
    assert seam_word(a, b, seam) == joined


def test_a_seam_that_does_not_name_these_words_is_ignored():
    assert seam_word("de important.", "importantă. cât", None) is None
    assert seam_word("de important.", "importantă. cât", ["mare", "importantă"]) is None   # a repair changed it
    assert seam_word("de important.", "cât de", ["important", "importantă"]) is None


def test_the_previous_caption_is_redone_on_a_seam(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0)
    t = time.time() - 20
    live._handle(_clause(1, t, t + 3, ro="este o seară care va fi atât de important."))
    live._handle({**_clause(2, t + 3.0, t + 6, ro="importantă cât importanță îi vom da noi."),
                  "seam": ["important.", "importantă"]})
    recs = _log(live)
    assert recs[-1]["source"] == "este o seară care va fi atât de importantă cât importanță îi vom da noi."
    assert recs[-1]["cut_word"]["seam"] == ["important.", "importantă"]


# --- the bench: a caption taken back on screen is not judged as shown ----------------------------
def test_the_bench_drops_a_caption_a_redo_replaced():
    sys.path.insert(0, str(ROOT / "scripts"))
    import bench
    ph = [{"id": 1, "outcome": "ok", "translation": "the Lord appeared to me from afar."},
          {"id": 2, "outcome": "echo_word"},
          {"id": 3, "outcome": "ok", "translation": "The Lord appeared of old to me", "replaced_prev": "x"},
          {"id": 4, "outcome": "ok", "translation": "before we go through", "cut_word": {"previous": "y"}}]
    out = bench.on_screen(ph)
    assert [r["outcome"] for r in out] == ["replaced", "echo_word", "replaced", "ok"]
    assert ph[0]["outcome"] == "ok"                       # the input is left as it was


# --- idea 23 (narrow): God put into a sentence that doesn't name Him -------------------------------
def test_an_invented_god_is_flagged_and_retried_without_it():
    src = "a avut cancer în metastază. tot trupul i-a cuprins de cancer."
    assert G.invented_god(src, "she had metastatic cancer. God stopped the cancer in her entire body.")
    assert G.mismatches(src, "God stopped the cancer in her entire body.", "ro", "en").get("god")

    class Tr:
        context, takes_note = [], True

        def translate(self, text, s, t, gloss=None, use_context=False, note=""):
            return "the cancer spread through her whole body." if "does not name God" in note else \
                "God stopped the cancer in her entire body."
    out, rec = G.check(Tr(), src, "God stopped the cancer in her entire body.", "ro", "en")
    assert out == "the cancer spread through her whole body." and rec["guard"]["kept"] == "note"


@pytest.mark.parametrize("src,out", [
    ("lăudat să fie Domnul", "praise the Lord"), ("Mulțumim lui Dumnezeu", "We thank God"),
    ("Tale, avem nevoie de puterea ta.", "Lord, we need Your power."),
    ("și Domn al domnilor, împăratului.", "and Lord of lords, the king."),
    ("a mers la doctor", "he went to the doctor"),
])
def test_god_named_in_the_source_is_not_flagged(src, out):
    assert not G.invented_god(src, out)


@pytest.mark.parametrize("text,out,want", [
    ("fratele Dani și fratele Iosif vor fi", "brother Dani and brother Joseph will be", "brother Dani and brother Iosif will be"),
    ("eu și împreună cu Iosif Popescu.", "I and together with Joseph Popescu.", "I and together with Iosif Popescu."),
    ("pe Moise, Ilie Popescu.", "to Moses, Elijah Popescu.", "to Moise, Ilie Popescu."),
    ("Iosif a fost vândut de frații lui", "Joseph was sold by his brothers", "Joseph was sold by his brothers"),
])
def test_the_bible_form_of_a_kept_name_is_put_back(text, out, want):
    assert T.keep_persons(text, out, T.Glossary()) == want


def test_a_capital_after_the_recogniser_s_stop_in_mid_sentence_is_lowered():
    assert G.lower_people("what do you want me to give you because. You saved my life",
                          "ce vrei să-ți dau pentru că. mi-ai salvat viața", "") == \
        "what do you want me to give you because. you saved my life"


def test_a_long_open_piece_still_waits_and_merges(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0, hold_slow_s=30, hold_max_s=10, max_chunk_s=5.5)
    t = time.time() - 30
    live._on_clause(_clause(1, t, t + 6.8, ro="adică lumea îl urăște pe Hristos pentru că iubește."))
    live._on_clause(_clause(2, t + 7.1, t + 13.6, ro="păcatul. Și un om a spus adevărul lui Dumnezeu."))
    live._drain()
    assert len(_log(live)) == 1 and _log(live)[0]["source"].endswith("iubește. păcatul. Și un om a spus adevărul lui Dumnezeu.")


# the seam catalogue (scripts/seam_catalogue.py): a pair is judged only where the recogniser gave it
def test_the_bench_judges_a_seam_pair_only_where_it_was_reproduced():
    sys.path.insert(0, str(ROOT / "scripts"))
    import bench

    def recs(*pieces):
        return [{"outcome": o, "ro": ro, "source": src} for o, ro, src in pieces]
    seam = {"cat": "seam", "pair": ["important.", "importantă"], "before": "de", "after": "cât"}
    # the stub and the whole word, both shown: still wrong
    assert bench.judge_pair(seam, recs(("ok", "atât de important.", "atât de important."),
                                       ("ok", "importantă cât", "importantă cât")))[0] == "still"
    # the redo joined them
    assert bench.judge_pair(seam, recs(("replaced", "atât de important.", "atât de important."),
                                       ("ok", "importantă cât", "atât de importantă cât")))[0] == "fixed"
    # the recogniser gave the word once: right without a redo
    assert bench.judge_pair(seam, recs(("ok", "atât de", "atât de"), ("ok", "importantă cât", "importantă cât")))[0] == "fixed"
    # the first word is right and the second its echo: joining them on the stub keeps the wrong one
    dup = {"cat": "dup", "pair": ["sărac.", "sărat"], "before": "sau", "after": "tu"}
    assert bench.judge_pair(dup, recs(("replaced", "bogat sau sărac.", "bogat sau sărac."),
                                      ("ok", "sărat tu lupți", "bogat sau sărat tu lupți")))[0] == "still"
    assert bench.judge_pair(dup, recs(("ok", "bogat sau sărac.", "bogat sau sărac."),
                                      ("ok", "tu lupți", "tu lupți")))[0] == "fixed"
    # two real words: right when both are shown; no cut there this time = not reproduced
    keep = {"cat": "keep", "pair": ["culoar.", "culoare"], "before": "lângă", "after": "n-a"}
    assert bench.judge_pair(keep, recs(("ok", "pe lângă culoar.", "pe lângă culoar."),
                                       ("ok", "culoare n-a avut", "culoare n-a avut")))[0] == "fixed"
    assert bench.judge_pair(keep, recs(("replaced", "pe lângă culoar.", "pe lângă culoar."),
                                       ("ok", "culoare n-a avut", "pe lângă culoare n-a avut")))[0] == "still"
    assert bench.judge_pair(keep, recs(("ok", "pe lângă culoare n-a avut", "pe lângă culoare n-a avut")))[0] == "missing"


# idea 25: no Romanian text because the recogniser keeps the words for the next chunk is not
# "heard nothing": Parakeet's text of the same audio would say them twice
def test_a_chunk_whose_words_wait_does_not_fall_back_on_the_other_recogniser(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("auto")
    ev = {"ro": "", "multi": "nu e nevoie de o biserică"}
    assert live._source(dict(ev))[1:] == ("nu e nevoie de o biserică", {"fallback": "multi"})
    assert live._source({**ev, "ro_wait": True})[1:] == ("", {})
    from live.pipeline import merge_clauses
    part = {"ro": "", "multi": "binecuvinteze", "t_start": 0.0, "t_end": 1.0, "stt_s": 0.1, "audio_s": 1.0}
    wait = {**part, "multi": "", "t_start": 1.0, "t_end": 2.0, "ro_wait": True}
    assert not merge_clauses(part, wait).get("ro_wait")      # the first part's text is still wanted
    assert merge_clauses(wait, {**wait, "t_start": 2.0, "t_end": 3.0}).get("ro_wait")
