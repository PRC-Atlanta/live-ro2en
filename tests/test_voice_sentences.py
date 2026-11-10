"""Live Voice M1/M3: the sentence step (docs/live_voice_plan.md §2). The chunk records and the
simulation's sentences in tests/data/voice come from scripts/voice_testdata.py."""
import csv
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
from live import speech                                  # noqa: E402
from live.app import Live                                # noqa: E402
from live.speech import NO_NEXT, SentenceBuilder, level_gap   # noqa: E402

DATA = ROOT / "tests" / "data" / "voice"
RUNS = (("eulogy", "en"), ("alex", "en"), ("nelu", "ro"))      # (run, the speaker's language)


def _run(name, spk):
    rows = [json.loads(l) for l in open(DATA / f"{name}.chunks.jsonl")]
    off = json.load(open(DATA / f"{name}.clock.json"))["offset_s"]
    chunks = sorted((r for r in rows if r["outcome"] == "ok" and r.get("translation") and r["lang"] == spk
                     and "t_display" in r), key=lambda r: r["t_end"])
    want = list(csv.DictReader(open(DATA / f"{name}.sentences.csv")))
    return rows, chunks, off, want


def _chunk(c, off=0.0):
    return {"id": c["id"], "text": c["translation"], "src": c.get("source", ""),
            "t_speech": c.get("t_speech", c["t_start"]), "t_end": c["t_end"], "shown": c["t_display"] - off}


# --- §9: the hindsight mode (next chunk known) reproduces the simulation exactly ----------------
@pytest.mark.parametrize("name,spk", RUNS)
def test_hindsight_reproduces_the_simulations_sentences(name, spk):
    _, chunks, off, want = _run(name, spk)
    tgt = "ro" if spk == "en" else "en"
    names = set()
    for c in chunks:
        names |= speech.seen_names(c["translation"].strip(), tgt)
    b, out = SentenceBuilder(tgt, names=names), []
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1] if i + 1 < len(chunks) else None
        gap = nxt.get("t_speech", nxt["t_start"]) - c["t_end"] if nxt else NO_NEXT
        out += b.add(_chunk(c, off), gap, last=nxt is None)
    assert [s["text"] for s in out] == [w["text"] for w in want]
    assert [s["why"] for s in out] == [w["why"] for w in want]
    assert [" ".join(map(str, s["chunks"])) for s in out] == [w["chunk_ids"] for w in want]
    for s, w in zip(out, want):
        assert abs(s["ready"] - float(w["ready_live"])) < 0.002 and abs(s["spk_end"] - float(w["spk_end"])) < 0.002
        assert s["words"] == int(w["words"]) and s["lang"] == tgt


# --- §2.2: the live rule. Names grow as the service goes, and the pause is known from what has
# arrived: here from every phrase the engine committed (any outcome), as the voice-activity readings
# are not in the log. Boundaries must match the simulation for >= 95% of sentences. -----------------
@pytest.mark.parametrize("name,spk", RUNS)
def test_live_rule_matches_the_simulation(name, spk):
    rows, chunks, off, want = _run(name, spk)
    tgt = "ro" if spk == "en" else "en"
    starts = sorted(r.get("t_speech", r["t_start"]) for r in rows if r.get("t_start") is not None)
    b, out = SentenceBuilder(tgt), []
    for r in sorted((r for r in rows if r.get("t_end") is not None), key=lambda r: r["t_end"]):
        if r["outcome"] != "ok" or not r.get("translation"):
            s = b.close_if_ended(r["t_end"])              # not captioned: a pause if the sentence had ended
            out += [s] if s else []
        elif r["lang"] != spk:                            # the speaker switched: the open sentence is closed
            s = b.flush()
            out += [s] if s else []
        else:
            nxt = next((s for s in starts if s >= r["t_end"] - 0.05), None)
            out += b.add(_chunk(r, off), nxt - r["t_end"] if nxt is not None else NO_NEXT)
    s = b.flush()
    out += [s] if s else []
    got, ref = {tuple(s["chunks"]) for s in out}, [tuple(map(int, w["chunk_ids"].split())) for w in want]
    same = sum(r in got for r in ref)
    assert same / len(ref) >= 0.95, f"{name}: {same}/{len(ref)} sentences have the simulation's boundaries"


# --- the rule's text changes -----------------------------------------------------------------------
def _c(i, text, t0, t1, **kw):
    return {"id": i, "text": text, "t_speech": t0, "t_end": t1, "shown": t1 + 1.5, **kw}


def test_a_period_without_a_pause_becomes_a_comma_and_a_capital_is_lowered():
    b = SentenceBuilder("ro")
    assert b.add(_c(1, "Și vă încurajez.", 0, 3), 0.1) == []
    (s,) = b.add(_c(2, "Să luați câteva clipe.", 3.1, 6), NO_NEXT)
    assert s["text"] == "Și vă încurajez, să luați câteva clipe." and s["why"] == "pause"
    assert s["chunks"] == [1, 2] and s["spk_start"] == 0 and s["spk_end"] == 6 and s["ready"] == 7.5


def test_a_name_keeps_its_capital_and_a_doubled_word_is_dropped():
    b = SentenceBuilder("ro", names={"Dragoș"})
    b.add(_c(1, "L-am cunoscut pe", 0, 2), 0.1)
    b.add(_c(2, "Dragoș în casa", 2, 4), 0.1)
    (s,) = b.add(_c(3, "Casa lui Dumnezeu.", 4, 6), 1.0)
    assert s["text"] == "L-am cunoscut pe Dragoș în casa lui Dumnezeu."
    b.add(_c(4, "Apoi", 7, 8), 0.1)
    (s,) = b.add(_c(5, "Dumnezeu a vorbit.", 8, 9), 1.0)              # ALWAYS_CAP
    assert s["text"] == "Apoi Dumnezeu a vorbit."


def test_names_grow_as_they_are_seen_mid_sentence():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Fratele Puha a venit.", 0, 2), 1.0)
    b.add(_c(2, "Și atunci", 3, 4), 0.1)
    (s,) = b.add(_c(3, "Puha a spus da.", 4, 6), 1.0)
    assert s["text"] == "Și atunci Puha a spus da."


def test_a_short_breath_becomes_a_comma():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Când am ajuns acolo", 0, 2), 0.4)
    (s,) = b.add(_c(2, "am văzut tot.", 2.4, 4), 1.0)
    assert s["text"] == "Când am ajuns acolo, am văzut tot."


def test_book_numbers_include_timotei():
    b = SentenceBuilder("ro")
    (s,) = b.add(_c(1, "Citim din 2 Timotei 3:16.", 0, 2), 1.0)
    assert s["text"] == "Citim din A doua Timotei 3:16."
    (s,) = b.add(_c(2, "Apoi 1 Corinteni 13 și 3 Ioan.", 3, 5), 1.0)
    assert s["text"] == "Apoi Întâi Corinteni 13 și A treia Ioan."
    (s,) = b.add(_c(3, "În capitolul 2 Ioan spune asta.", 6, 8), 1.0)
    assert s["text"] == "În capitolul 2 Ioan spune asta."
    e = SentenceBuilder("en")
    (s,) = e.add(_c(1, "Paul says in 2 Corinthians 5:7 and 1 Timothy 2.", 0, 2), 1.0)
    assert s["text"] == "Paul says in Second Corinthians 5:7 and First Timothy 2."


def test_a_long_sentence_closes_at_a_comma_and_never_passes_the_cap():
    b = SentenceBuilder("ro")
    assert b.add(_c(1, " ".join(["cuvânt"] * 28), 0, 5), 0.0) == []
    (s,) = b.add(_c(2, "și încă două,", 5, 6), 0.0)
    assert s["why"] == "long" and s["text"].endswith("două.") and s["ready"] == 7.5
    assert b.add(_c(3, " ".join(["vorbă"] * 40), 6, 12), 0.0) == []
    (s,) = b.add(_c(4, "alte cinci vorbe fără punct", 12, 13), 0.0)
    assert s["why"] == "long" and s["words"] == 45 and s["text"].endswith("punct.")


def test_question_and_exclamation_marks_are_kept():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Ce credeți?", 0, 2), 0.1)
    (s,) = b.add(_c(2, "Vă surprinde!", 2, 4), 1.0)
    assert s["text"] == "Ce credeți? vă surprinde!"
    b.add(_c(3, "Chiar așa", 5, 6), 0.0)
    assert b.flush()["text"] == "Chiar așa."


def test_english_rules():
    b = SentenceBuilder("en")
    b.add(_c(1, "Praise be the Lord.", 0, 2), 0.1)
    b.add(_c(2, "The Lord has spoken.", 2, 4), 0.1)                # "The" is a function word: lowered
    b.add(_c(3, "God is good.", 4, 6), 0.1)                        # God keeps its capital
    (s,) = b.add(_c(4, "I know it.", 6, 8), 1.0)                   # so does I
    assert s["text"] == "Praise be the Lord, the Lord has spoken, God is good, I know it."
    assert s["lang"] == "en"


def test_scripture_reference_and_source_are_carried():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Isus a zis.", 0, 2, src="Jesus said.", scripture=False), 0.1)
    (s,) = b.add(_c(2, "Eu sunt calea.", 2, 4, src="I am the way.", scripture=True, ref="Ioan 14:6", caught_up=2), 1.0)
    assert s["scripture"] is True and s["ref"] == "Ioan 14:6" and s["caught_up"] == 2
    assert s["src"] == "Jesus said. I am the way."


# --- "did the speaker pause?" from the voice-activity readings (§2.2) -------------------------------
def _hist(t0, flags, step=0.1):
    return [(t0 + (i + 1) * step, -30.0, -20.0, f) for i, f in enumerate(flags)]


def test_level_gap():
    T = 100.0
    assert level_gap(_hist(T, [1, 1, 0, 0, 0, 0, 0, 0]), T, 0.6) == NO_NEXT            # silence for 0.6 s
    assert level_gap(_hist(T, [1, 1, 1, 1, 1, 1, 1, 1]), T, 0.6) == 0.0               # never stopped
    assert abs(level_gap(_hist(T, [1, 0, 0, 1, 1, 1, 1, 1]), T, 0.6) - 0.4) < 1e-6    # a breath
    assert level_gap(_hist(T, [1, 0, 0]), T, 0.6) is None                             # not known yet
    assert level_gap([], T, 0.6) is None
    assert abs(level_gap(_hist(T, [1, 0, 0]) + _hist(T + 0.3, [1, 1, 1, 1]), T, 0.6) - 0.4) < 1e-6


# --- corrections (§2.4) -----------------------------------------------------------------------------
def test_retract_in_the_open_sentence_and_in_a_published_one():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Unu.", 0, 1), 0.1)
    b.add(_c(2, "Doi.", 1, 2), 0.1)
    assert b.retract(2) == ("open", None)
    (s,) = b.add(_c(3, "Trei.", 2, 3), 1.0)
    assert s["text"] == "Unu, trei." and s["chunks"] == [1, 3]
    kind, r = b.retract(3)
    assert kind == "replace" and r["sid"] == s["sid"] and r["text"] == "Unu." and r["chunks"] == [1]
    kind, r = b.retract(1)
    assert kind == "retract" and r["sid"] == s["sid"]
    assert b.retract(1) == ("none", None)


def test_fix_last_swaps_the_chunks_text():
    b = SentenceBuilder("ro")
    b.add(_c(1, "Unu.", 0, 1), 0.1)
    assert b.set_text(1, "Primul.") == ("open", None)
    (s,) = b.add(_c(2, "Doi.", 1, 2), 1.0)
    assert s["text"] == "Primul, doi."
    kind, r = b.set_text(2, "al doilea")
    assert kind == "replace" and r["text"] == "Primul, al doilea." and r["sid"] == s["sid"]
    kind, r = b.set_text(2, "")                                    # nothing left of it: taken out
    assert kind == "replace" and r["text"] == "Primul."


def test_only_the_last_ten_published_sentences_can_be_corrected():
    b = SentenceBuilder("ro")
    for i in range(12):
        b.add(_c(i, f"Propoziția {i}.", i, i + 0.5), 1.0)
    assert b.retract(0) == ("none", None) and b.retract(2)[0] == "retract"


# --- the mixin: pipeline -> sentences -> the log and the voice ---------------------------------------
class PP:
    last_error = None

    def show(self, text):
        return True

    def clear(self):
        return True


def _live(tmp_path, monkeypatch=None, **voice):
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["voice"] = {**cfg["voice"], "enabled": True, **voice}
    live = Live(cfg, types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: {"Good morning.": "Bună dimineața.",
                                                                  "We are glad.": "Ne bucurăm.",
                                                                  "Bună ziua.": "Good day."}.get(t, "Tradus: " + t))
    live.pp = PP()
    live.sent = []
    live._voice_send = lambda m: live.sent.append(m) if m.get("type") != "state" else None   # no voice process
    return live


def _clause(i, t0, t1, **kw):
    return {"id": i, "ro": "", "multi": "", "en": "", "t_start": t0, "t_end": t1, "t_emit": t1 + 0.1,
            "stt_s": 0.1, "audio_s": t1 - t0, **kw}


def _chunk_in(live, ev, tgt, text, src, verse=None, redo=False, outcome="ok"):
    """What the pipeline does for one shown chunk: mark it on screen, then hand it to the sentence step."""
    mark = live._speech_mark(ev, tgt, outcome, redo=redo)
    live._speech_chunk(ev, tgt, text, src, outcome, time.time(), verse=verse, mark=mark)
    return mark


def _events(live, name=None):
    recs = [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []
    return [r for r in recs if (r.get("event") == name if name else "event" in r)]


def _say(live, i, t0, t1, text, lang="en"):
    live.lang.set_mode("en-ro" if lang == "en" else "ro-en")
    live._on_clause(_clause(i, t0, t1, multi=text) if lang == "en" else _clause(i, t0, t1, ro=text))
    live._flush()
    live._drain()


def test_sentences_are_logged_and_sent_with_the_plans_fields(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 30
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))        # a pause after the first phrase
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    (s,) = _events(live, "sentence")
    assert s["lang"] == "ro" and s["why"] == "pause" and s["chunks"] == [1] and s["sid"] == 1
    assert s["text"].startswith("Tradus: Good morning") and s["scripture"] is False
    assert abs(s["spk_end"] - (t + 3)) < 1e-3 and s["ready"] >= s["shown_last"] > s["spk_end"]
    assert set(s) >= {"sid", "lang", "text", "words", "why", "chunks", "spk_start", "spk_end", "shown_last",
                      "ready", "scripture", "ref", "caught_up", "src"}
    assert live.sent == [{"type": "sentence", **{k: v for k, v in s.items() if k not in ("event", "time")}}]


def test_nothing_is_built_while_live_voice_is_off(tmp_path):
    live = _live(tmp_path, enabled=False)
    t = time.time() - 30
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    live._speech_stop()
    assert _events(live, "sentence") == [] and live.sent == []


def test_a_language_switch_closes_the_open_sentence_in_both_directions(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 60
    live._level_hist.extend(_hist(t + 3, [1] * 10))                         # no pause: the sentence stays open
    _say(live, 1, t, t + 3, "This is the first part of what I say")
    assert _events(live, "sentence") == []
    live._level_hist.extend(_hist(t + 8, [1] * 10))
    _say(live, 2, t + 5, t + 8, "Acum vorbesc în limba română cu voi toți", lang="ro")
    (s,) = _events(live, "sentence")
    assert s["lang"] == "ro" and s["why"] == "flush" and s["chunks"] == [1]
    _say(live, 3, t + 10, t + 13, "And now I speak English again to you")
    en = [x for x in _events(live, "sentence") if x["lang"] == "en"]
    assert len(en) == 1 and en[0]["why"] == "flush" and en[0]["chunks"] == [2]


def test_the_english_stream_can_be_turned_off(tmp_path):
    live = _live(tmp_path, en={"enabled": False})
    t = time.time() - 30
    _say(live, 1, t, t + 3, "Acum vorbesc în limba română cu voi toți", lang="ro")
    live._speech_stop()
    assert _events(live, "sentence") == []


def test_stop_publishes_the_open_sentence(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 30
    live._level_hist.extend(_hist(t + 3, [1] * 10))
    _say(live, 1, t, t + 3, "This is the first part of what I say")
    live._speech_stop()
    (s,) = _events(live, "sentence")
    assert s["why"] == "flush" and s["text"].endswith(".")


def test_a_speaker_who_trails_off_is_flushed(tmp_path):
    live = _live(tmp_path, flush_s=0.2)
    t = time.time() - 30
    live._level_hist.extend(_hist(t + 3, [1] * 10))
    _say(live, 1, t, t + 3, "This is the first part of what I say")
    deadline = time.time() + 5
    while not _events(live, "sentence") and time.time() < deadline:
        time.sleep(0.1)
    assert [s["why"] for s in _events(live, "sentence")] == ["flush"]


def test_hide_blank_and_song_discard_the_open_sentence(tmp_path):
    for act in (lambda l: l.pause(True), lambda l: l.blank(), lambda l: l.song(True)):
        d = tmp_path / str(time.time_ns())
        d.mkdir()
        live = _live(d)
        t = time.time() - 30
        live._level_hist.extend(_hist(t + 3, [1] * 10))
        _say(live, 1, t, t + 3, "This is the first part of what I say")
        act(live)
        live._speech_stop()
        assert _events(live, "sentence") == [] and _events(live, "sentence_discard")[0]["chunks"] == 1


def test_hold_does_not_stop_the_voice(tmp_path):
    live = _live(tmp_path)
    live.hold(True)
    t = time.time() - 30
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    assert len(_events(live, "sentence")) == 1


def test_a_hidden_phrase_is_not_voiced(tmp_path):
    live = _live(tmp_path)
    live.pause(True)
    t = time.time() - 30
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    live._speech_stop()
    assert _events(live, "sentence") == []


def test_remove_last_and_fix_last_reach_the_voice(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 60
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    assert live.correct("Bună dimineața tuturor.")
    assert live.sent[-1] == {"type": "replace", "sid": 1, "lang": "ro", "text": "Bună dimineața tuturor."}
    assert live.retract()
    assert live.sent[-1] == {"type": "retract", "sid": 1, "lang": "ro"}
    assert [e["why"] for e in _events(live, "sentence_replace") + _events(live, "sentence_retract")] == ["correct", "retract"]
    # in the open sentence nothing is sent: the sentence is simply built without the chunk
    live._level_hist.extend(_hist(t + 13, [1] * 10))
    _say(live, 2, t + 10, t + 13, "This part will be taken away again")
    n = len(live.sent)
    assert live.retract() and len(live.sent) == n
    live._speech_stop()
    assert len(_events(live, "sentence")) == 1


def test_speech_that_was_not_captioned_counts_as_the_pause(tmp_path):
    """A sentence had ended, the speaker went on at once, but that phrase came out empty: the sentence
    is closed there (the simulation never saw such a phrase, so its next chunk came after a pause)."""
    live = _live(tmp_path)
    t = time.time() - 60
    live._level_hist.extend(_hist(t + 3, [1] * 10))                         # speech goes on: no pause yet
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    assert _events(live, "sentence") == []
    live._log_failure(_clause(2, t + 3.1, t + 5), "empty_stt", lang="en")
    (s,) = _events(live, "sentence")
    assert s["why"] == "pause" and s["chunks"] == [1] and s["text"].endswith("here.")
    # a chunk that did not end a sentence stays open
    live._level_hist.extend(_hist(t + 13, [1] * 10))
    _say(live, 3, t + 10, t + 13, "This is the first part of what I say")
    live._log_failure(_clause(4, t + 13.1, t + 15), "empty_stt", lang="en")
    assert len(_events(live, "sentence")) == 1


# --- M1 review: timers, Remove last twice, the English stream off, a late verse ------------------------
def _wait_for(fn, s=6.0):
    end = time.time() + s
    while not fn() and time.time() < end:
        time.sleep(0.05)
    return fn()


def test_level_readings_that_arrive_late_still_decide_the_chunk(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 3.2
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")     # no readings yet: it waits
    assert _events(live, "sentence") == []
    time.sleep(0.5)                                                         # several timer rounds later
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
    assert _wait_for(lambda: _events(live, "sentence"), 2.0)
    assert _events(live, "sentence")[0]["why"] == "pause"


def test_with_no_level_readings_at_all_the_chunk_is_decided_as_a_pause(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 3.2
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    assert _wait_for(lambda: _events(live, "sentence"), 5.0)                # pause_s + 1.5 s, by the timer alone


def test_remove_last_twice_takes_two_chunks(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 60
    for i, t0 in ((1, 0), (2, 4), (3, 8)):
        live._level_hist.extend(_hist(t + t0 + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
        _say(live, i, t + t0, t + t0 + 3, f"This is sentence number {i} for the test.")
    assert [s["sid"] for s in _events(live, "sentence")] == [1, 2, 3]
    assert live.retract() and live.retract()
    assert [m for m in live.sent if m["type"] == "retract"] == [
        {"type": "retract", "sid": 3, "lang": "ro"}, {"type": "retract", "sid": 2, "lang": "ro"}]
    assert live.correct("Prima propoziție.")
    assert live.sent[-1] == {"type": "replace", "sid": 1, "lang": "ro", "text": "Prima propoziție."}


def test_remove_last_on_an_unvoiced_caption_leaves_the_voiced_ones(tmp_path):
    live = _live(tmp_path, en={"enabled": False})
    t = time.time() - 60
    live._level_hist.extend(_hist(t + 3, [1, 0, 0, 0, 0, 0, 0, 0]))
    _say(live, 1, t, t + 3, "Good morning everyone and welcome here.")
    _say(live, 2, t + 5, t + 8, "Acum vorbesc în limba română cu voi toți", lang="ro")   # not voiced
    n = len(live.sent)
    assert live.retract() and len(live.sent) == n                           # the English caption went: no message
    assert live.retract() and live.sent[-1] == {"type": "retract", "sid": 1, "lang": "ro"}


def test_a_language_switch_closes_the_sentence_also_when_the_english_stream_is_off(tmp_path):
    live = _live(tmp_path, en={"enabled": False})
    t = time.time() - 60
    live._level_hist.extend(_hist(t + 3, [1] * 10))
    _say(live, 1, t, t + 3, "This is the first part of what I say")
    live._level_hist.extend(_hist(t + 8, [1] * 10))
    _say(live, 2, t + 5, t + 8, "Acum vorbesc în limba română cu voi toți", lang="ro")
    (s,) = _events(live, "sentence")
    assert s["lang"] == "ro" and s["why"] == "flush" and s["ready"] >= s["shown_last"]


def test_a_new_service_starts_with_nothing_left_over(tmp_path):
    live = _live(tmp_path)
    _chunk_in(live, {"id": 1, "t_start": 1.0, "t_end": 2.0}, "ro", "Bună dimineața", "Good morning")
    live._speech_reset()
    live._speech_stop()
    assert _events(live, "sentence") == [] and not live._speech_shown


def test_scripture_window_restarts_at_every_verse_match(tmp_path):
    live = _live(tmp_path)
    for i in range(1, 12):
        _chunk_in(live, {"id": i, "t_start": i - 0.3, "t_end": float(i)}, "ro", f"Text {i}.", "plain words",
                  verse=[{"ref": "Matthew 25:25"}] if i in (2, 6) else [])
    live._speech_stop()
    marks = {c: s["scripture"] for s in _events(live, "sentence") for c in s["chunks"]}
    assert [marks[i] for i in range(1, 12)] == [False] + [True] * 10        # 2..8 by the first match, 6..12 by the second


def test_a_late_verse_takes_the_previous_caption_back_as_on_screen(tmp_path):
    """On screen a late verse removes the previous caption and shows one combined caption. The voice
    does the same: the previous chunk leaves its sentence, the new chunk carries the combined text."""
    live = _live(tmp_path)
    _chunk_in(live, {"id": 1, "t_start": 10.0, "t_end": 12.0}, "ro", "El a zis. Chiar dacă nu.", "He said. Even if not.")
    _chunk_in(live, {"id": 2, "t_start": 12.1, "t_end": 14.0}, "ro", "El a zis. Și chiar de nu ne va scoate, să știi.",
              "and even if he does not", verse=[{"ref": "Daniel 3:18"}], redo=True)
    live._speech_stop()
    (s,) = _events(live, "sentence")
    assert s["text"] == "El a zis. Și chiar de nu ne va scoate, să știi." and s["chunks"] == [2]
    assert s["scripture"] and s["ref"] == "Daniel 3:18"
    assert list(live._speech_shown) == [(2, "ro")]
    # the previous chunk was already published: the voice is told to take it back
    (tmp_path / "b").mkdir()
    live2 = _live(tmp_path / "b")
    _chunk_in(live2, {"id": 1, "t_start": 10.0, "t_end": 12.0}, "ro", "Chiar dacă nu.", "x")
    live2._speech_pump(force=True)
    assert live2.sent[0]["type"] == "sentence"
    _chunk_in(live2, {"id": 2, "t_start": 13.0, "t_end": 14.0}, "ro", "Și chiar de nu.", "y",
              verse=[{"ref": "Daniel 3:18"}], redo=True)
    assert live2.sent[1] == {"type": "retract", "sid": 1, "lang": "ro"}
    # Remove last after a late verse takes the combined caption, then the one before it
    (tmp_path / "c").mkdir()
    live3 = _live(tmp_path / "c")
    for i, text in ((0, "Prima frază."), (1, "A doua frază."), (2, "El a zis.")):
        _chunk_in(live3, {"id": i, "t_start": i * 5.0, "t_end": i * 5.0 + 2}, "ro", text, "x")
    _chunk_in(live3, {"id": 3, "t_start": 12.1, "t_end": 14.0}, "ro", "El a zis. Și chiar de nu.", "y", redo=True)
    live3._speech_retract()
    live3._speech_retract()
    live3._speech_stop()
    gone = {e["sid"] for e in _events(live3, "sentence_retract")}
    assert [s["chunks"] for s in _events(live3, "sentence") if s["sid"] not in gone] == [[0]]


def test_operator_actions_between_the_screen_and_the_sentence_step(tmp_path):
    ev = {"id": 1, "t_start": 1.0, "t_end": 2.0}
    for i, act in enumerate((lambda l: l._speech_discard("blank"),          # Blank
                             lambda l: l._speech_retract(),                  # Remove last
                             lambda l: (l._speech_discard("hide"), l._speech_discard("show")))):
        (tmp_path / str(i)).mkdir()
        live = _live(tmp_path / str(i))
        _chunk_in(live, {"id": 0, "t_start": 0.0, "t_end": 0.5}, "ro", "Înainte.", "Before.")
        live._speech_pump(force=True)
        n = len(live.sent)
        mark = live._speech_mark(ev, "ro", "ok")                             # on screen…
        act(live)                                                            # …the operator acts…
        live._speech_chunk(ev, "ro", "Bună dimineața.", "Good morning.", "ok", time.time(), mark=mark)
        live._speech_stop()                                                  # …and the chunk is not voiced
        assert [s["chunks"] for s in _events(live, "sentence")] == [[0]], i
        assert live.sent[n:] == [], i                                        # nor is the one before taken back
