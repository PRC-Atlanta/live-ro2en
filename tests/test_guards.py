"""Numbers/negations guard (idea 5) and the translator output cleanup (ideas 8, 22)."""
import sys
import types

import pytest

from live import guards as G
from live import translate as T


# --- numbers ---------------------------------------------------------------------------------
@pytest.mark.parametrize("text,lang,vals", [
    ("cei paisprezece copii", "ro", {14}),
    ("their fifteen children", "en", {15}),
    ("celor 14 nepoți", "ro", {14}),
    ("nouăsprezece", "ro", {19}),
    ("douăzeci și doi de ani", "ro", {22}),
    ("o mie nouă sute șaizeci și trei", "ro", {1963}),
    ("două mii doisprezece", "ro", {2012}),
    ("al paisprezecelea, a treia zi", "ro", {14, 3}),
    ("Nouă copii au rămas", "ro", {9}),
    ("ne-a spus nouă că vine", "ro", set()),           # "to us", not nine
    ("fourteen", "en", {14}),
    ("the fifteenth", "en", {15}),
    ("May nineteenth, nineteen sixty-three", "en", {19, 1963}),
    ("two thousand twelve", "en", {2012}),
    ("two thousand and twelve", "en", {2012}),
    ("In nineteen ninety-six", "en", {1996}),
    ("This is twenty twenty six", "en", {2026}),
    ("nineteen oh five", "en", {1905}),
    ("a hundred and twenty people", "en", {120}),
    ("twenty-first", "en", {21}),
    ("Ioan 3:16, 2.000 de oameni", "ro", {3, 16, 2000}),
    ("one of them, someone", "en", set()),              # 1 is left out
    ("wait a second", "en", set()),
    ("both of them", "en", {2}),
    ("amândoi", "ro", {2}),
])
def test_numbers_read_words_and_digits(text, lang, vals):
    assert G.numbers(text, lang) == vals


@pytest.mark.parametrize("text,out", [
    ("May nineteenth, nineteen sixty-three", "May 19, 1963"),
    ("on the morning of October fifteenth", "on the morning of October 15"),
    ("In nineteen ninety-six we moved", "In 1996 we moved"),
    ("two thousand twelve", "2012"),
    ("his fourteen grandchildren", "his 14 grandchildren"),
    ("the fifteenth chapter", "the 15th chapter"),
    ("the two of us and three more", "the two of us and three more"),   # small numbers stay words
    ("no numbers here", "no numbers here"),
])
def test_english_numbers_to_digits_before_translation(text, out):
    assert G.to_digits(text, "en") == out


@pytest.mark.parametrize("text, lang, vals", [
    ("Dumnezeu ne dă o nouă viață", "ro", set()),                # a new life, not nine
    ("spune-o mie", "ro", set()),                                # say it to me, not 1000
    ("cele nouă porunci", "ro", {9}), ("o mie de oameni", "ro", {1000}),
    ("twenty thirty years ago", "en", {20, 30}),                 # a range, not 2030
    ("we were fifteen twenty people", "en", {15, 20}),
    ("back in nineteen eighty times were hard", "en", {1980}),   # a year after "in"
    ("in twenty twenty people came", "en", {2020}),
    ("a group of fifteen twenty people", "en", {15, 20}), ("a period of twenty thirty years", "en", {20, 30}),
    ("since nineteen eighty people", "en", {1980}),
])
def test_numbers_that_are_not_what_they_look_like(text, lang, vals):
    assert G.numbers(text, lang) == vals


def test_romanian_retry_source_gets_digits():
    assert G.to_digits("cei paisprezece copii și doi nepoți", "ro", min_value=2) == "cei 14 copii și 2 nepoți"
    assert G.to_digits("al paisprezecelea", "ro", min_value=2) == "al paisprezecelea"   # ordinals stay


# --- negations -------------------------------------------------------------------------------
@pytest.mark.parametrize("text,lang,neg", [
    ("între cele două ne mișcăm", "ro", False),
    ("we do not move between the two", "en", True),
    ("dar privită de sus, nu-i așa?", "ro", False),             # tag question
    ("but seen from above, it isn't, is it?", "en", True),      # the 13:29 error: a real "isn't"
    ("it's good, isn't it?", "en", False),
    ("e bine, nu?", "ro", False),
    ("N-am văzut pe nimeni", "ro", True),
    ("nu am decât doi copii", "ro", False),                     # nu ... decât = only
    ("nu așa se face", "ro", True),
    ("I don’t know", "en", True),
    ("Nothing is impossible", "en", True),
    ("Dumnezeu este bun", "ro", False),
])
def test_negations(text, lang, neg):
    assert G.negated(text, lang) is neg


def test_mismatches_name_what_differs():
    assert G.mismatches("cei paisprezece copii", "their fifteen children", "ro", "en") == \
        {"numbers": {"source": [14], "translation": [15]}}
    assert G.mismatches("între cele două ne mișcăm", "we do not move between the two", "ro", "en") == \
        {"negation": {"source": False, "translation": True}}
    assert G.mismatches("celor 14 nepoți", "to their fourteen grandchildren", "ro", "en") == {}
    assert G.mismatches("May 19, 1963", "pe 19 mai 1963", "en", "ro") == {}


# --- the guard --------------------------------------------------------------------------------
class LLM:
    """A translator with context, like MLXLLMTranslator: answers from a table."""
    def __init__(self, answers):
        self.answers, self.calls, self.context, self.last_truncated = answers, [], [("a", "b", "ro")], False

    def translate(self, text, src, tgt, glossary=None, use_context=False):
        self.calls.append((text, use_context))
        if not use_context:
            self.context = []
        return self.answers[text]


def test_guard_keeps_a_retry_that_matches():
    tr = LLM({"cei 14 copii": "their 14 children"})
    out, rec = G.check(tr, "cei paisprezece copii", "their fifteen children", "ro", "en")
    assert out == "their 14 children"
    assert rec["guard"]["kept"] == "retry" and rec["guard"]["first"] == "their fifteen children"
    assert tr.calls == [("cei 14 copii", False)]          # digits, no context
    assert tr.context == [("a", "b", "ro")]               # the context is left as it was


def test_guard_keeps_the_first_when_the_retry_also_differs():
    tr = LLM({"între cele două ne mișcăm": "we do not move between the two"})
    out, rec = G.check(tr, "între cele două ne mișcăm", "we don't move between the two", "ro", "en")
    assert out == "we don't move between the two"
    assert rec["guard"]["kept"] == "first" and rec["guard"]["retry"] == "we do not move between the two"
    assert "negation" in rec["guard"]["issues"]


def test_guard_is_silent_when_all_agree():
    tr = LLM({})
    assert G.check(tr, "celor 14 nepoți", "to their 14 grandchildren", "ro", "en") == \
        ("to their 14 grandchildren", {})
    assert G.check(tr, "x", "", "ro", "en") == ("", {})
    assert tr.calls == []


def test_guard_without_context_retries_only_a_changed_source():
    class NLLB:
        calls = 0

        def translate(self, text, src, tgt, glossary=None):
            NLLB.calls += 1
            return "they don't move"
    out, rec = G.check(NLLB(), "ne mișcăm", "we do not move", "ro", "en")
    assert out == "we do not move" and rec["guard"]["retry"] is None and NLLB.calls == 0


def test_a_failed_retry_keeps_the_caption():
    class Broken(LLM):
        def translate(self, *a, **k):
            raise RuntimeError("gpu")
    out, rec = G.check(Broken({}), "cei paisprezece copii", "fifteen children", "ro", "en")
    assert out == "fifteen children" and rec["guard"]["error"].startswith("RuntimeError")


# --- output cleanup (ideas 8, 22) ------------------------------------------------------------
@pytest.mark.parametrize("raw,clean", [
    ("școala<image|>împinge ca o educație sexuală.", "școala împinge ca o educație sexuală."),
    ("plecarea lui în<image|>eternitate.", "plecarea lui în eternitate."),
    ("dar cu năde<image|>jde de a ne întâlni", "dar cu nădejde de a ne întâlni"),
    ("pentru această compan<image|>ie", "pentru această companie"),
    ("învățători, instru<image|>ctori?", "învățători, instructori?"),
    ("We, as a family, the Barbus,<image|>value you very much.", "We, as a family, the Barbus, value you very much."),
    ("<image|>se vorbește despre voia lui Dumnezeu", "se vorbește despre voia lui Dumnezeu"),
    ("ziua de mâ<audio|>ine.", "ziua de mâine."),
    ("în urma multor rugăciuni ale noastre, multor rugăciuni ale vo<image|><|channel>thought\n"
     "<channel|>în urma multor rugăciuni ale noastre, multor rugăciuni ale voastre.",
     "în urma multor rugăciuni ale noastre, multor rugăciuni ale voastre."),
    ("unde este ceasul dumneavo<image|><|channel>thought\n<channel|><|channel>thought\n<channel|>",
     "unde este ceasul dumneavo"),                          # nothing after the channel: keep the start
    ("Hello<|channel>thought\nlet me think", "Hello"),         # a thought that never closed
    ("<think>hm</think>Amen.", "Amen."),
    ('"Amen."', "Amen."),
    ("First paragraph.\n\nSecond.", "First paragraph."),
])
def test_control_tokens_and_thinking_are_cleaned(raw, clean):
    assert T.clean_output(raw) == clean


@pytest.mark.parametrize("raw,clean", [
    ('și a muri este profit. Dragul spune să-și<image|><image|>*(Note: The source text "Dragur says '
     'run his race" appears to be a fragment. If "Dragur" is a name, the translation is: …)*',
     "și a muri este profit. Dragul spune să-și"),
    ("el știa că trebuie să ne ab<image|>*Note: Since the sentence is incomplete", "el știa că trebuie să ne ab"),
    ("și a muri este câștig. (Note: literal)", "și a muri este câștig."),
    ("Translation: Și a muri este câștig.", "Și a muri este câștig."),
    ("Note: the source is unclear.", ""),
    ("Am notat: da.", "Am notat: da."),                       # "notat" is not a note label
    ("el știa că trebuie să ne ab *Note: Since the sentence is incomplete", "el știa că trebuie să ne ab"),
    ("și noi ne rugăm Note: the source is incomplete", "și noi ne rugăm"),
    ("Amen.” Note: x", "Amen.”"),
    # a label word inside a sentence is the translation, not a note
    ("And here is the explanation: God loves us.", "And here is the explanation: God loves us."),
    ("Take note: God is faithful.", "Take note: God is faithful."),
    ("He gave this Explanation: God is love.", "He gave this Explanation: God is love."),
    ("Here is my Note: God is love", "Here is my Note: God is love"),
    ("He said, “Translation: God is love.”", "He said, “Translation: God is love.”"),
])
def test_notes_to_the_reader_are_cut(raw, clean):
    assert T.clean_output(raw) == clean


def _llm(monkeypatch, answer, prompts=None):
    prompts = [] if prompts is None else prompts
    kw = {}

    def generate(model, tok, prompt, **k):
        prompts.append(prompt)
        kw.update(k)
        return answer
    monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(generate=generate))
    monkeypatch.setitem(sys.modules, "mlx_lm.sample_utils", types.SimpleNamespace(make_sampler=lambda **k: None))
    tr = T.MLXLLMTranslator.__new__(T.MLXLLMTranslator)
    tr.model, tr.max_tokens, tr.context, tr.context_clauses = None, 200, [], 2
    tr.tok = types.SimpleNamespace(apply_chat_template=lambda m, **k: m[-1]["content"], encode=lambda s: s.split())
    return tr, prompts, kw


def test_a_tiny_fragment_is_translated_alone_and_never_becomes_sentences(monkeypatch):
    tr, prompts, _ = _llm(monkeypatch, "We pray for the family. And we thank God.")
    tr.context = [("Ne rugăm pentru familie.", "We pray for the family.", "ro")]
    assert tr.translate("T.", "ro", "en", use_context=True) == ""
    assert "Previous sentences" not in prompts[-1]
    assert tr.context == [("Ne rugăm pentru familie.", "We pray for the family.", "ro")]   # kept, not extended


def test_short_answers_survive_the_fragment_rule(monkeypatch):
    tr, _, _ = _llm(monkeypatch, "Yes.")
    assert tr.translate("Da.", "ro", "en", use_context=True) == "Yes."


def test_english_numbers_reach_the_translator_as_digits(monkeypatch):
    tr, prompts, _ = _llm(monkeypatch, "pe 15 octombrie")
    tr.translate("on October fifteenth", "en", "ro")
    assert "October 15" in prompts[-1]


def test_translate_cleans_the_output_and_bans_special_tokens(monkeypatch):
    tr, _, kw = _llm(monkeypatch, "dar cu năde<image|>jde")
    assert tr.translate("dar cu nădejde", "en", "ro") == "dar cu nădejde"
    assert kw["logits_processors"] == []                    # a tokenizer without special ids: nothing to ban


def test_banned_ids_keep_end_of_turn():
    tok = types.SimpleNamespace(all_special_ids=[0, 1, 2, 50, 106, 258882, 258883], eos_token_ids={1, 106, 50})
    assert T.banned_ids(tok) == [0, 2, 258882, 258883]
    assert T.banned_ids(types.SimpleNamespace()) == []


def test_guard_leaves_an_echoed_quote_to_the_echo_check():
    tr = LLM({})
    assert G.check(tr, "I will not fear.", "I will not fear.", "ro", "en") == ("I will not fear.", {})
    assert tr.calls == []


# --- 2026-10-25: reversed meanings that reached the screen, and false alarms -----------------
class NoteLLM(LLM):
    """Gemma-like: takes a note; answers by (text, note)."""
    takes_note = True

    def translate(self, text, src, tgt, glossary=None, use_context=False, note=""):
        self.calls.append((text, use_context, note))
        return self.answers.get((text, bool(note)), self.answers.get(text))


class FakeNLLB:
    def __init__(self, answer):
        self.answer, self.calls = answer, 0

    def translate(self, text, src, tgt, glossary=None):
        self.calls += 1
        return self.answer


def test_a_retry_with_the_same_mistake_gets_a_note():
    src = "Ne-ai mai dat o zi binecuvântată."
    tr = NoteLLM({(src, False): "You haven't given a blessed day.",
                  (src, True): "You have given us another blessed day."})
    out, rec = G.check(tr, src, "You have not given us a blessed day.", "ro", "en")
    assert out == "You have given us another blessed day."
    assert rec["guard"]["kept"] == "note" and "NO negation" in tr.calls[-1][2]


def test_the_fallback_translator_is_the_last_try():
    src = "ne mai trebuie fonduri"
    tr = NoteLLM({src: "we don't need any more funds"})
    nllb = FakeNLLB("we need more funds")
    out, rec = G.check(tr, src, "we don't need any more funds", "ro", "en", fallback=nllb)
    assert out == "we need more funds" and rec["guard"]["kept"] == "fallback" and nllb.calls == 1


def test_when_every_translator_disagrees_with_the_check_the_first_stays():
    """Three translators against the detector: the detector is the likely one at fault, and
    NLLB's weaker output must not replace Gemma's."""
    src = "dacă tu ne-ai chemat să ne mutăm"
    tr = NoteLLM({src: "if You didn't call us to move"})
    nllb = FakeNLLB("if You didn't call us")
    out, rec = G.check(tr, src, "if You didn't call us to move", "ro", "en", fallback=nllb)
    assert out == "if You didn't call us to move" and rec["guard"]["kept"] == "first"


def test_a_fallback_that_fixes_the_negation_wins_even_with_a_number_left():
    src = "ne mai trebuie 12 fonduri"
    tr = NoteLLM({src: "we don't need 12 funds"})
    out, rec = G.check(tr, src, "we don't need twelve funds", "ro", "en", fallback=FakeNLLB("we need more funds"))
    assert out == "we need more funds" and rec["guard"]["unresolved"]


@pytest.mark.parametrize("src,out", [
    ("Dumnezeu ne mântuiește", "God does not save us"),
    ("Trebuie să ne pocăim", "We must not repent"),
    ("Vă rog să ne iertați", "Please do not forgive us"),
    ("El a venit pentru a doua oară.", "He did not come for the second time."),
])
def test_ne_pronoun_and_ordinals_keep_the_guard(src, out):
    assert "negation" in G.mismatches(src, out, "ro", "en")


def test_second_time_is_not_a_number_either_side():
    assert G.mismatches("El a venit pentru a doua oară.", "He came for the second time.", "ro", "en") == {}


def test_longer_number_only_when_the_other_did_not_hear_the_small_one():
    assert G.longer_numbers("cinci pâini și doi pești", "5 pâini și 2 pești, 12 coșuri")[1] == []


def test_no_fallback_keeps_the_first_as_before():
    src = "ne mai trebuie fonduri"
    tr = NoteLLM({src: "we don't need any more funds"})
    out, rec = G.check(tr, src, "we don't need funds", "ro", "en")
    assert out == "we don't need funds" and rec["guard"]["kept"] == "first"


@pytest.mark.parametrize("src,out", [
    ("Domnul a doua m-a întrebat: Emmanuel", "the Lord asked me a second time: Emmanuel"),
    ("m-a întrebat a doua oară", "he asked me again"),
    ("dacă sunt suflete nete. ne mântuite, Doamne", "if there are souls who are not saved, Lord"),
    ("suflete nemântuite", "unsaved souls"),
])
def test_no_false_alarm(src, out):
    assert G.mismatches(src, out, "ro", "en") == {}


def test_real_mismatches_still_caught():
    assert "numbers" in G.mismatches("a treia zi", "on the second day", "ro", "en")
    assert "negation" in G.mismatches("ne mai trebuie fonduri", "we don't need any more funds", "ro", "en")


@pytest.mark.parametrize("text,has_mai,has_tag", [
    ("Ne-ai mai dat o zi binecuvântată.", True, False),
    ("ne mai trebuie fonduri", True, False),
    ("nu mai avem finanțe", False, False),           # a real "nu mai": no note
    ("nici nu mai vine", False, False),
    ("nu-i așa că uneori avem impresia că", False, True),
    ("e bine așa", False, False),
])
def test_notes(text, has_mai, has_tag):
    n = " ".join(G.notes(text, "ro"))
    assert ("«mai»" in n) == has_mai and ("nu-i așa" in n) == has_tag
    assert G.notes(text, "en") == []


@pytest.mark.parametrize("text,want", [
    ("sunt în jur de vreo 4567.000 de copii", "sunt în jur de vreo 4.000-7.000 de copii"),
    ("să valoreze 50067.000 de dolari", "să valoreze 5.000-7.000 de dolari"),
    ("cam 345 de mii de oameni", "cam 345 de mii de oameni"),   # 3 digits: a real amount
    ("123.000 lei", "123.000 lei"),
    ("7.641 de insule", "7.641 de insule"),
    ("în 2024.000", "în 2024.000"),
    ("1.207 dolari", "1.207 dolari"),
])
def test_unglue(text, want):
    assert G.unglue(text) == want


def test_the_other_recogniser_heard_the_longer_number():
    assert G.longer_numbers("tatăl celor doi fii", "tatăl celor 12 fii.") == ("tatăl celor 12 fii", [("doi", 12)])
    assert G.longer_numbers("cei doi frați", "cei doi frați") == ("cei doi frați", [])
    assert G.longer_numbers("două sute de oameni", "douăzeci de oameni")[1] == []   # "două sute" is 200


def test_odd_figures_are_not_shown():
    src = "1.271701 milion. 271.700.000."
    assert G.odd_figures(src) and not G.odd_figures("7.641 de insule") and not G.odd_figures("1.207")
    assert G.hide_odd(src, "1.271701 million. 271,700,000.") == "[amount unclear]. [amount unclear]."
    assert G.hide_odd("7.641 de insule", "7,641 islands") == "7,641 islands"


def test_twenty_second_is_22():
    assert G.numbers("on November twenty-second", "en") == {22}


# --- glossary forms, people with Bible names, Jacob/James -------------------------------------
def test_glossary_matches_other_forms_after_exact_ones():
    g = T.Glossary()
    assert ("sicriu", "coffin") in [(r["ro"], r["en"]) for r in g.relevant("cum se perindau sicriile în față")]
    assert g.relevant("apostolul Pavel")[0]["en"] == "Paul"           # exact matches first
    assert [r["ro"] for r in g.relevant("Îl salutăm pe fratele Ionuț")] == []   # fratele ≠ frați


def test_people_with_a_bible_name_and_a_surname():
    g = T.Glossary()
    assert T.persons("Fratele Benu și fratele Ilie Puha a promis", g) == ["Ilie Puha"]
    assert T.persons("Domnul Isus Hristos", g) == []
    assert T.persons("fratele Ioan Botezătorul", g) == []


def test_te_rog_frumos_is_please():
    g = T.Glossary()
    rows = {r["ro"]: r["en"] for r in g.idioms("Te rog frumos, că trebuie să-mi supraveghez fabrica.")}
    assert rows.get("te rog frumos", "").startswith("please")
    assert {r["ro"]: r["en"] for r in g.idioms("te rugăm frumos, Doamne")}["te rugăm frumos"] == "we humbly pray"
