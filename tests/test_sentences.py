"""live.translate_sentences: whole sentences for the translator, cut from the recogniser's pieces
(src/live/sentences.py, pipeline._on_piece; offline: scripts/sentence_cut.py)."""
import collections
import json
import pathlib
import sys
import time
import types

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
from live.app import Live
from live import translate
from live.pipeline import piece_parts
from live.sentences import SentenceCutter, sentence_text


def _p(i, t0, t1, ro, **kw):
    return {"id": i, "ro": ro, "multi": "", "en": "", "t_start": t0, "t_end": t1, "t_emit": t1 + 0.1,
            "stt_s": 0.1, "audio_s": t1 - t0, **kw}


def _ro(unit):
    return " ".join(e["ro"] for e in unit[0])


# --- the cutter ------------------------------------------------------------------------------
def test_a_full_stop_before_a_lower_case_word_is_not_a_sentence_end():
    c = SentenceCutter()
    assert c.add(_p(1, 0, 3, "Doresc să vorbesc despre părtășia cu noi.")) == []
    assert c.waiting_end                                     # . at the end: wait for the next word
    assert c.add(_p(2, 3.3, 6, "experiența pe care o avem.")) == []
    (u,) = c.add(_p(3, 6.3, 8, "Este bine."))
    assert u[1] == "end" and _ro(u) == "Doresc să vorbesc despre părtășia cu noi. experiența pe care o avem."
    assert [e["id"] for e in c.evs] == [3]


def test_fake_stops_are_taken_out_of_the_sentence_text():
    assert sentence_text("părtășia cu noi. experiența pe care o avem.") == "părtășia cu noi experiența pe care o avem."
    assert sentence_text("Amin... și apoi. Este") == "Amin... și apoi. Este"


def test_a_sentence_end_inside_a_piece_splits_it():
    c = SentenceCutter()
    c.add(_p(1, 0, 2, "Iubită biserică,"))
    (u,) = c.add(_p(2, 2.2, 6, "vă salut pe toți. Astăzi vorbim despre har."))
    assert _ro(u) == "Iubită biserică, vă salut pe toți."
    head = u[0][-1]
    assert head["id"] == 2 and 2.2 < head["t_end"] < 6
    (tail,) = c.evs
    assert tail["ro"] == "Astăzi vorbim despre har." and tail["split_of"] == 2 and tail["id"] != 2
    assert abs(tail["t_speech"] - head["t_end"]) < 1e-9


def test_a_short_sentence_is_not_cut_before_three_words():
    c = SentenceCutter()
    assert c.add(_p(1, 0, 1, "Dar acum.")) == []
    assert c.add(_p(2, 1.2, 4, "Dumnezeu ne vorbește.")) == []      # «Dar acum.» has 2 words: one sentence


def test_a_long_pause_sends_what_is_buffered():
    c = SentenceCutter(gap_s=2.5)
    c.add(_p(1, 0, 3, "și am mers acolo cu"))
    (u,) = c.add(_p(2, 6, 8, "Ei au venit."))
    assert u[1] == "gap" and _ro(u) == "și am mers acolo cu"


def test_a_long_sentence_is_cut_at_a_comma_before_a_conjunction():
    c = SentenceCutter(cap_s=12)
    c.add(_p(1, 0, 5, "noi am fost acolo multă vreme, și am văzut"))
    c.add(_p(2, 5.2, 10, "cum lucrează Dumnezeu în viețile oamenilor"))
    (u,) = c.add(_p(3, 10.2, 15, "care vin la El cu inima deschisă"))
    assert u[1] == "cap" and _ro(u) == "noi am fost acolo multă vreme,"
    assert " ".join(e["ro"] for e in c.evs).startswith("și am văzut cum lucrează")


def test_flush_returns_the_rest():
    c = SentenceCutter()
    c.add(_p(1, 0, 3, "Și el a spus."))
    assert c.flush("quiet")[1] == "quiet" and c.evs == [] and c.flush() is None


def test_piece_parts_cuts_a_sentence_back_at_its_pieces():
    assert piece_parts("Ioan 3 cu 16 spune Căci atât de mult a iubit Dumnezeu lumea",
                       ["Ioan 3 cu 16 spune.", "Căci atât de mult a iubit Dumnezeu lumea."]) \
        == ["Ioan 3 cu 16 spune", "Căci atât de mult a iubit Dumnezeu lumea"]
    assert piece_parts("un doi trei", ["un doi.", "x y z."]) == ["un doi trei"]


# --- the pipeline ------------------------------------------------------------------------------
class PP:
    last_error = None

    def show(self, text):
        return True

    def clear(self):
        return True


def _live(tmp_path, translate_fn=None, **lc):
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.calls = []

    def tr(t, *a, **k):
        live.calls.append(t)
        return f"EN: {t}"
    live.tr = types.SimpleNamespace(translate=translate_fn or tr)
    live.pp = PP()
    live.lang.set_mode("ro-en")
    live.lc.update(translate_sentences=True, **lc)
    return live


def _log(live):
    return [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []


def test_pieces_are_translated_as_one_sentence(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Doresc să vorbesc despre părtășia cu noi."))
    live._on_clause(_p(2, t + 3.3, t + 6, "experiența pe care o avem."))
    assert live._sent_evs() and live._work.empty()             # nothing translated yet
    live._on_clause(_p(3, t + 6.3, t + 8, "Este bine."))
    live._drain()
    (r,) = _log(live)
    assert live.calls == ["Doresc să vorbesc despre părtășia cu noi experiența pe care o avem."]
    assert r["source"] == live.calls[0] and r["sentence"] == "end" and r["sent_pieces"] == 2
    assert r["id"] == 2 and r["t_start"] == t and r["t_end"] == t + 6
    assert isinstance(r["sent_wait_s"], float)
    live._flush()
    live._drain()
    assert [x["source"] for x in _log(live)][-1] == "Este bine."


def test_a_quiet_engine_sends_a_sentence_that_ended(tmp_path):
    live = _live(tmp_path, sentence_quiet_s=1.0)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Dumnezeu este bun cu noi."))
    live._on_quiet({"t": t + 3.5, "since": t + 3})
    assert live._sent_evs()                                     # 0.5 s: not yet
    live._on_quiet({"t": t + 4.0, "since": t + 3})
    live._drain()
    assert [(r["source"], r["sentence"]) for r in _log(live)] == [("Dumnezeu este bun cu noi.", "quiet")]


def test_a_quiet_engine_waits_for_a_sentence_that_goes_on(tmp_path):
    live = _live(tmp_path, sentence_quiet_s=1.0, sentence_gap_s=2.5)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "și am mers acolo cu"))
    live._on_quiet({"t": t + 4.5, "since": t + 3})
    assert live._sent_evs()                                     # no full stop: wait up to the gap
    live._on_quiet({"t": t + 5.5, "since": t + 3})
    live._drain()
    assert [r["sentence"] for r in _log(live)] == ["gap"]


def test_the_wait_timer_sends_the_sentence_when_nothing_follows(tmp_path):
    live = _live(tmp_path, sentence_wait_s=0.2)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Dumnezeu este bun cu noi."))
    time.sleep(0.5)
    live._drain()
    assert [r["sentence"] for r in _log(live)] == ["wait"]


def test_english_captions_bypass_the_buffer(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("en-ro")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "", multi="We are glad you are here with us today."))
    live._flush()
    live._drain()
    assert not live._sent_evs() and [r.get("sentence") for r in _log(live)] == [None]


def test_switching_the_setting_off_sends_the_buffer(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Dumnezeu este bun cu noi."))
    live.lc["translate_sentences"] = False
    live._on_clause(_p(2, t + 3.3, t + 7, "Să ne rugăm împreună acum pentru toți."))
    live._flush()
    live._drain()
    assert [r["source"] for r in _log(live)] == ["Dumnezeu este bun cu noi.", "Să ne rugăm împreună acum pentru toți."]


def test_a_word_cut_at_a_piece_joint_is_one_word_inside_the_sentence(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "înainte de a parca."))
    live._on_clause(_p(2, t + 3.2, t + 6, "parcurge câteva versete."))
    live._flush()
    live._drain()
    (r,) = _log(live)
    assert r["source"] == "înainte de a parcurge câteva versete." and r["sent_cut_words"] == [["parca.", "parcurge"]]


# --- Bible verses: a sentence holding a verse piece goes piece by piece ------------------------
class FakeMatcher:
    """Matches a piece that holds «iubit Dumnezeu lumea» (and only on its own)."""
    bible = object()

    def __init__(self):
        self.history, self.recent, self.tail = collections.deque(), {}, None

    def take_tail(self, text, src="ro"):
        return text

    def match(self, text, src="ro", tgt="en", ref=None, announced=False):
        self.history.extend(text.split())
        return "verse" if "iubit Dumnezeu lumea" in text and not text.startswith("Ioan") else None


def test_verse_ahead_probes_a_copy_of_the_matcher():
    tr = types.SimpleNamespace(verses=FakeMatcher(), reading=translate.scripture.Reading())
    assert translate.verse_ahead(tr, ["Ioan 3 cu 16 spune.", "Căci atât de mult a iubit Dumnezeu lumea."])
    assert not translate.verse_ahead(tr, ["Noi știm că Dumnezeu ne iubește."])
    assert not tr.verses.history                                # the real matcher saw nothing


def test_a_sentence_with_a_verse_piece_is_translated_piece_by_piece(tmp_path):
    live = _live(tmp_path)
    live.tr.verses, live.tr.reading = FakeMatcher(), translate.scripture.Reading()
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Ioan 3 cu 16 spune."))
    live._on_clause(_p(2, t + 3.2, t + 7, "căci atât de mult a iubit Dumnezeu lumea."))
    live._flush()
    live._drain()
    (r,) = _log(live)
    assert live.calls == ["Ioan 3 cu 16 spune", "căci atât de mult a iubit Dumnezeu lumea."]
    assert r["sentence_verse"] is True


# --- Live Voice: a whole sentence is a sentence for the voice too ------------------------------
def test_live_voice_closes_a_whole_sentence_at_once(tmp_path):
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["voice"] = {**cfg["voice"], "enabled": True}
    live = Live(cfg, types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=lambda t, *a, **k: "God is good to us." if t.startswith("Dumnezeu")
                                    else "Let us pray together now.")
    live.pp = PP()
    live._voice_send = lambda m: None
    live.lang.set_mode("ro-en")
    live.lc.update(translate_sentences=True)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Dumnezeu este bun cu noi."))
    live._on_clause(_p(2, t + 3.2, t + 6, "Să ne rugăm împreună acum."))     # 0.2 s later: no pause
    live._drain()
    sents = [json.loads(l) for l in open(live.log_path) if '"event": "sentence"' in l]
    assert [s["text"] for s in sents] == ["God is good to us."]
    live._speech_stop()


def test_the_caption_lag_guard_gets_the_sentences_own_end(tmp_path):
    live = _live(tmp_path)
    seen, show = [], live._show
    live._show = lambda *a, **k: (seen.append(k.get("spoken_ago")), show(*a, **k))[1]
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Iubită biserică,"))
    live._on_clause(_p(2, t + 3.2, t + 7, "vă salut pe toți. Astăzi vorbim despre har."))   # cut inside piece 2
    live._drain()
    (r,) = _log(live)
    assert r["source"] == "Iubită biserică, vă salut pe toți." and r["t_end"] < t + 7
    assert len(seen) == 1 and abs(seen[0] - (time.time() - r["t_end"])) < 1.0


def test_an_english_piece_mid_sentence_ends_the_sentence_and_votes_alone(tmp_path):
    """Auto mode, Romanian captions: an interpreter's English piece is not merged into the open
    Romanian sentence (it would be translated as Romanian and vote once for the whole sentence)."""
    live = _live(tmp_path)
    live.lang.set_mode("auto")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Doresc să vorbesc despre părtășia cu noi,",
                       multi="Doresc să vorbesc despre părtășia cu noi,"))
    assert live._sent_evs()
    live._on_clause(_p(2, t + 3.3, t + 6, "Do resc. tu wat",
                       multi="I want to talk about the fellowship with us."))
    assert not live._sent_evs()                                # the Romanian sentence went first
    live._flush()
    live._drain()
    recs = _log(live)
    assert recs[0]["sentence"] == "lang" and recs[0]["sent_pieces"] == 1
    assert recs[1].get("sentence") is None                     # the English piece took the usual way


def test_a_short_english_looking_piece_stays_in_the_sentence(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("auto")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Doresc să vorbesc despre părtășia,", multi="Doresc să vorbesc despre părtășia,"))
    live._on_clause(_p(2, t + 3.3, t + 4, "amin și", multi="Amen and"))
    assert len(live._sent_evs()) == 2


def test_garbled_romanian_written_as_english_stays_in_the_sentence(tmp_path):
    """The Romanian recogniser's transcript still reads as Romanian: the multilingual model's English
    is a mishearing (langid idea 12), not a language switch."""
    live = _live(tmp_path)
    live.lang.set_mode("auto")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Doresc să vorbesc despre părtășia,", multi="Doresc să vorbesc despre părtășia,"))
    live._on_clause(_p(2, t + 3.3, t + 6, "și experiența pe care o avem cu Dumnezeu",
                       multi="and the experience that we have with God"))
    assert len(live._sent_evs()) == 2


def test_a_locked_romanian_mode_never_breaks_the_sentence_on_language(tmp_path):
    live = _live(tmp_path)                                     # ro-en
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Doresc să vorbesc despre părtășia,", multi="Doresc să vorbesc despre părtășia,"))
    live._on_clause(_p(2, t + 3.3, t + 6, "Do resc. tu wat", multi="I want to talk about the fellowship with us."))
    assert len(live._sent_evs()) == 2
