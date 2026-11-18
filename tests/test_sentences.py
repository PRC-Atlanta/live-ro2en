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
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))
    cfg["live"].update(caption_pages=False, caption_flow=False)  # these tests are about the rolling lines
    live = Live(cfg, types.SimpleNamespace(file=None))
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


def test_an_english_speaker_is_cut_by_the_english_transcript():
    c = SentenceCutter(key="multi")
    c.add(_p(1, 0, 2, "", multi="Dear church,"))
    (u,) = c.add(_p(2, 2.2, 6, "a b c d e f", multi="welcome to you all. Today we talk about grace."))
    assert " ".join(e["multi"] for e in u[0]) == "Dear church, welcome to you all."
    (tail,) = c.evs
    assert tail["multi"] == "Today we talk about grace." and tail["split_of"] == 2
    assert u[0][-1]["ro"] == "a b c" and tail["ro"] == "d e f"    # the other transcript cut at the same share


def test_english_pieces_are_translated_as_one_sentence(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("en-ro")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "", multi="that many young people have to be weary of."))
    live._on_clause(_p(2, t + 3.3, t + 6, "", multi="specifically the mind, the eyes and the heart."))
    assert live._sent_evs() and live._work.empty()             # nothing translated yet
    live._on_clause(_p(3, t + 6.3, t + 8, "", multi="And it is so subtle."))
    live._drain()
    (r,) = _log(live)
    assert live.calls == ["that many young people have to be weary of specifically the mind, the eyes and the heart."]
    assert r["source"] == live.calls[0] and r["sentence"] == "end" and r["sent_pieces"] == 2
    live._flush()
    live._drain()
    assert [x["source"] for x in _log(live)][-1] == "And it is so subtle."


def test_a_change_of_language_sends_the_open_sentence(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Dumnezeu este bun cu noi"))
    live.lang.set_mode("en-ro")
    live._on_clause(_p(2, t + 3.3, t + 6, "", multi="God is good to us all the time"))
    live._drain()
    assert [(r["source"], r["sentence"]) for r in _log(live)] == [("Dumnezeu este bun cu noi", "lang")]
    assert [e["multi"] for e in live._sent_evs()] == ["God is good to us all the time"]


def test_an_english_piece_with_no_english_transcript_bypasses_the_buffer(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("en-ro")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Bine."))
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
    cfg["live"].update(caption_pages=False, caption_flow=False)  # these tests are about the rolling lines
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


def test_the_heard_panel_streams_each_piece_as_it_is_heard(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("en-ro")
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "", multi="that many young people have to be weary of."))
    assert [h["src"] for h in live.state()["heard"]] == ["that many young people have to be weary of."]
    live._on_clause(_p(2, t + 3.3, t + 6, "", multi="specifically the mind, the eyes and the heart."))
    assert [h["src"] for h in live.state()["heard"]][-1] == "specifically the mind, the eyes and the heart."
    assert live.state()["lines"] == []                          # nothing translated yet
    live.lc["translate_sentences"] = False
    live._on_clause(_p(3, t + 6.3, t + 8, "", multi="And it is so subtle."))
    assert live.state()["heard"] == []                          # off: Heard from the lines again


def test_a_whole_sentence_lands_on_the_screen_at_once(tmp_path):
    live = _live(tmp_path)
    live.caps.reveal = True
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 4, "Doresc să vorbesc astăzi despre părtășia pe care o avem", audio_s=4.0))
    live._on_clause(_p(2, t + 4.2, t + 9, "cu Dumnezeu și unii cu alții în biserică.", audio_s=4.8))
    live._on_clause(_p(3, t + 9.3, t + 10, "Este bine."))
    live._drain()
    assert not live.caps.pending                                # no words held back for a reveal
    assert "biserică" in " ".join(live.caps.lines + live.caps.queue)


def test_a_long_sentence_is_cut_at_its_commas_into_parts_that_fit_the_box():
    from live.captions import comma_parts
    s = ("Pornografia nu mai este o problemă izolată doar pentru bărbați, ci este și o problemă pentru "
         "fetele tinere, iar în această dimineață vrem să vorbim despre pericolele ascunse.")
    parts = comma_parts(s, 84)
    assert " ".join(parts) == s and len(parts) == 3
    assert parts[0].endswith("bărbați,") and parts[1].endswith("tinere,")
    assert comma_parts("Dumnezeu este bun, mereu.", 84) == ["Dumnezeu este bun, mereu."]   # fits: one part
    assert comma_parts("Da, da, " + "cuvânt " * 20, 30)[0].startswith("Da, da, cuvânt")      # short ones join


def test_each_comma_part_starts_a_line_of_its_own():
    from live.captions import RollingCaptions
    c = RollingCaptions(max_chars=42, max_lines=2, read_wps=2.7)      # the app's reading-time pacing
    c.add("Ieri am fost acolo")
    c.add("x", parts=["Pornografia nu mai este o problemă izolată doar pentru bărbați,",
                      "ci este și o problemă pentru fetele tinere."])
    rows = c.lines + c.queue
    assert rows[0].startswith("Ieri am fost acolo Pornografia")      # the first part goes on the line
    assert c.lines[-1].endswith("bărbați,")                            # the screen stops at the comma
    assert c.queue[0].startswith("ci este")                            # the next part waits on a line of its own


def test_a_new_sentence_starts_a_line_of_its_own():
    """Alex replay 2026-11-17: 78 of 260 lines held one sentence's end and the next one's start."""
    from live.captions import RollingCaptions
    c = RollingCaptions(max_chars=42, max_lines=2, read_wps=2.7)
    c.add("Ne afectează căsniciile.", fresh=True)
    c.add("În august 2007, pe un pod din Mississippi.", fresh=True)
    rows = c.lines + c.queue
    assert rows == ["Ne afectează căsniciile.", "În august 2007, pe un pod din Mississippi."]
    c.add("mai exact pe I-35 West.")                                     # not fresh: it continues
    assert (c.lines + c.queue)[-1].endswith("I-35 West.")


def test_a_sentence_on_its_own_lines_is_wrapped_evenly():
    """No sentence ends on a line of one or two words (“urmeze”, “noastre”)."""
    from live.captions import RollingCaptions
    c = RollingCaptions(max_chars=42, max_lines=2, read_wps=2.7)
    text = "Dumnezeu ne-a dat un plan pentru viețile noastre și ne cere să-l urmeze"
    c.add(text, fresh=True)
    rows = c.lines + c.queue
    assert " ".join(rows) == text and len(rows) == 2
    assert min(len(r.split()) for r in rows) >= 5 and all(len(r) <= 42 for r in rows)



def test_each_whole_sentence_from_the_pipeline_starts_its_own_line(tmp_path):
    live = _live(tmp_path)
    t = time.time() - 20
    live._on_clause(_p(1, t, t + 3, "Ne afectează căsniciile."))
    live._on_clause(_p(2, t + 3.3, t + 6, "Este bine."))
    live._flush()
    live._drain()
    rows = live.caps.lines + live.caps.queue
    assert any(r.startswith("EN: Este bine") or r.startswith("Este bine") for r in rows), rows
    assert not any("căsniciile" in r and "Este" in r for r in rows), rows
