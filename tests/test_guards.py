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
