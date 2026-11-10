"""The experimental 'claude:' translator (Live Voice test, 2026-11-10): Gemma's exact prompt, sent
to the Anthropic API. No network here: the client is a fake."""
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))

import scripture as S        # noqa: E402
import verses as V           # noqa: E402
from live import translate as T   # noqa: E402


class FakeClient:
    def __init__(self, text="Bună dimineața.", stop="end_turn"):
        self.calls, self.text, self.stop = [], text, stop
        self.messages = types.SimpleNamespace(create=self.create)

    def create(self, **kw):
        self.calls.append(kw)
        r = types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text=self.text)],
                                  stop_reason=self.stop, _request_id="req_x",
                                  usage=types.SimpleNamespace(input_tokens=100, output_tokens=5))
        return r


def make(client, log=None):
    t = T.ClaudeTranslator.__new__(T.ClaudeTranslator)
    t.client, t.model_id, t.repo, t.max_tokens = client, "claude-haiku-5-5", "claude-haiku-5-5", 200
    t.context, t.context_clauses, t.last_truncated, t.last_verse, t._stop = [], 2, False, None, None
    t.reading, t.verses, t._usage_log = S.Reading(), V.Matcher(), log
    return t


def test_refuses_without_network_permission(monkeypatch):
    monkeypatch.delenv("LIVE_ALLOW_NETWORK", raising=False)
    with pytest.raises(RuntimeError, match="LIVE_ALLOW_NETWORK"):
        T.make_translator("claude:claude-haiku-5-5")


def test_sends_gemmas_messages(tmp_path):
    c = FakeClient()
    t = make(c, tmp_path / "u.jsonl")
    t.context = [("We gather today", "Ne adunăm astăzi", "en")]
    want = T.MLXLLMTranslator._messages(t, "to remember our father.", "en", "ro", None)
    out = t.translate("to remember our father.", "en", "ro", None, use_context=True)
    assert out == "Bună dimineața."
    kw = c.calls[0]
    assert kw["system"] == want[0]["content"] == T.SYSTEM[("en", "ro")]
    assert kw["messages"] == want[1:]
    assert "English: We gather today" in kw["messages"][0]["content"]
    assert kw["thinking"] == {"type": "disabled"} and kw["max_tokens"] == 200
    assert '"in": 100' in (tmp_path / "u.jsonl").read_text()


def test_tuned_adds_rules_and_drops_comments():
    c = FakeClient(text="The input is garbled, so I can't translate it.")
    t = make(c)
    t.tuned = True
    assert t.translate("He grigod and celeste.", "en", "ro", None) == ""
    assert c.calls[0]["system"].startswith(T.SYSTEM[("en", "ro")]) and "single hyphen" in c.calls[0]["system"]
    c.text = "-"
    assert t.translate("He grigod and celeste.", "en", "ro", None) == ""
    c.text = "Traducerea textului."                         # the word is in the source: a translation
    assert t.translate("The translation of the text.", "en", "ro", None) == "Traducerea textului."


def test_whole_phrase_only_for_a_translator_that_asks():
    import pipeline
    t = make(FakeClient())
    assert not pipeline._whole_phrase(t, "en", "ro")
    t.whole_phrase = True
    assert pipeline._whole_phrase(t, "en", "ro")
    both = T.ByDirection({("ro", "en"): object(), ("en", "ro"): t}, {("ro", "en"): "a", ("en", "ro"): "b"})
    assert pipeline._whole_phrase(both, "en", "ro") and not pipeline._whole_phrase(both, "ro", "en")


def test_truncation_from_stop_reason():
    t = make(FakeClient(stop="max_tokens"))
    t.translate("A long piece.", "en", "ro", None)
    assert t.last_truncated is True
