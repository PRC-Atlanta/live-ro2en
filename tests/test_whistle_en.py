"""Whistle English path: clause assembly from committed words, offline env, depth/weights."""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from whistle_en import ClauseAssembler, load_keywords, offline_env, weights_path, words_from_event


def W(word, start, end):
    return {"word": word, "start": start, "end": end}


def collect(**kw):
    out = []
    return out, ClauseAssembler(lambda text, s, e, ms: out.append((text, s, e)), **kw)


def test_cut_at_sentence_punctuation():
    out, a = collect()
    a.add([W("For", 0.0, 0.2), W("God", 0.2, 0.4), W("so", 0.4, 0.5), W("loved", 0.5, 0.8), W("the", 0.8, 0.9),
           W("world.", 0.9, 1.3), W("And", 1.4, 1.5)])
    assert out == [("For God so loved the world.", 0.0, 1.3)]
    assert [w["word"] for w in a.words] == ["And"]


def test_cut_at_pause_from_timestamps():
    out, a = collect(pause_s=0.35)
    a.add([W("In", 0.0, 0.2), W("the", 0.2, 0.3), W("beginning", 0.3, 0.8)])
    a.add([W("God", 1.5, 1.8)])                     # 0.7 s gap -> previous clause closes
    assert out == [("In the beginning", 0.0, 0.8)]


def test_comma_needs_enough_words_and_cap_limits_length():
    out, a = collect(comma_min_words=6, max_chunk_s=2.5)
    a.add([W("Brothers,", 0.0, 0.4), W("and", 0.4, 0.5)])
    assert out == []                                # short comma clause is not cut
    a.add([W(f"w{i}", 0.5 + i * 0.3, 0.7 + i * 0.3) for i in range(10)])
    assert out and out[0][2] - out[0][1] >= 2.5 - 0.31


def test_tick_flushes_after_pause_without_pending_text():
    out, a = collect(pause_s=0.35)
    a.add([W("Amen", 0.0, 0.4)])
    a.tick(0.6, pending="and")                      # pending text: keep waiting
    assert out == []
    a.tick(0.9, pending="")
    assert out == [("Amen", 0.0, 0.4)]


def test_only_committed_words_are_used():
    ev = {"text": "God so", "words": [W("God", 0.3, 0.6), W("so", 0.6, 0.8)], "pending": "loved the world"}
    assert [w["word"] for w in words_from_event(ev)] == ["God", "so"]
    assert words_from_event({"text": "", "words": [], "pending": "anything"}) == []


def test_offline_env_and_depth(tmp_path, monkeypatch):
    for k in ("NEEDLE_TELEMETRY", "DO_NOT_TRACK", "HF_HUB_OFFLINE", "NEEDLE_WHISTLE_WEIGHTS"):
        monkeypatch.delenv(k, raising=False)
    offline_env({})
    assert os.environ["NEEDLE_TELEMETRY"] == "0" and os.environ["DO_NOT_TRACK"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["NEEDLE_WHISTLE_WEIGHTS"].endswith("whistle.cact")
    assert weights_path({"depth": 4, "weights_by_depth": {4: "models/whistle/w4.cact"}}).name == "w4.cact"
    import pytest
    with pytest.raises(ValueError):
        weights_path({"depth": 3})


def test_keywords_file_loads():
    kw = load_keywords("keywords_en.txt").split("\n")
    assert "Nebuchadnezzar" in kw and "propitiation" in kw and not any(k.startswith("#") for k in kw)
