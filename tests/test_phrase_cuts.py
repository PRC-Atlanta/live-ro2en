"""Stream C: phrases cut by words, not seconds (idea 20); the English listener's repeated
overlap trimmed (idea 16); songs held by the Song key or the song detector (idea 18)."""
import json
import pathlib
import sys
import time
import types

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from live.app import Live
from live import overlap, report, song
from live.pipeline import dangling, merge_clauses

CFG = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"


class PP:
    def __init__(self):
        self.shown, self.last_error = [], None

    def show(self, text):
        self.shown.append(text)
        return True

    def clear(self):
        self.shown.append("")
        return True


def _live(tmp_path, translate=lambda t, *a, **k: f"EN: {t}"):
    live = Live(yaml.safe_load(open(CFG)), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=translate)
    live.pp = PP()
    return live


def _clause(i, t_start, t_end, **kw):
    return {"id": i, "ro": "", "multi": "", "en": "", "t_start": t_start, "t_end": t_end, "t_emit": t_end + 0.1,
            "stt_s": 0.1, "audio_s": t_end - t_start, **kw}


def _log(live):
    return [json.loads(l) for l in open(live.log_path)] if live.log_path.exists() else []


# --- idea 20: words, not seconds ------------------------------------------------------------
def test_a_phrase_that_stops_on_a_preposition_or_conjunction_is_dangling():
    for t in ("dragă comunitate din.", "aduc condoleanțe din", "și el a spus că", "He told us about the",
              "I came with my dad and", "Vreau să"):
        assert dangling(t), t
    for t in ("Domnul să vă mângâie.", "cu soția sa.", "He was a true man of God.", "", "Amin!"):
        assert not dangling(t), t


def test_dangling_pieces_of_a_slow_speaker_wait_and_merge_past_max_chunk(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(max_chunk_s=5.5, phrase_min_words=0, hold_dangling=True, hold_slow_s=30, hold_max_s=10)
    t = time.time() - 20
    live._on_clause(_clause(1, t, t + 2.7, ro="dragă comunitate din."))     # 12:42:54
    assert live._pending is not None                                         # waits for the rest
    live._on_clause(_clause(2, t + 3.1, t + 6.9, ro="Atlanta, frați și surori de pretutindeni."))
    live._drain()
    recs = _log(live)
    assert [r["source"] for r in recs] == ["dragă comunitate din. Atlanta, frați și surori de pretutindeni."]
    assert live._pending is None


def test_phrase_min_words_holds_short_phrases_and_hold_max_s_caps_the_span(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(max_chunk_s=5.5, phrase_min_words=6, hold_dangling=True, hold_slow_s=30, hold_max_s=10)
    t = time.time() - 30
    live._on_clause(_clause(1, t, t + 3, ro="Iubită biserică."))
    assert live._pending is not None
    live._on_clause(_clause(2, t + 9, t + 13, ro="Am o singură idee pentru voi toți astăzi."))  # 13 s > hold_max_s
    live._drain()
    assert [r["source"] for r in _log(live)] == ["Iubită biserică.", "Am o singură idee pentru voi toți astăzi."]


def test_a_question_and_a_long_phrase_go_at_once(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=6, hold_slow_s=30)
    t = time.time() - 10
    live._on_clause(_clause(1, t, t + 2, ro="Știți ce?"))
    live._on_clause(_clause(2, t + 3, t + 7, ro="Fratele Dragoș a fost prieten cu Ticu."))
    live._drain()
    assert [r["source"] for r in _log(live)] == ["Știți ce?", "Fratele Dragoș a fost prieten cu Ticu."]
    assert live._pending is None


def test_a_dangling_phrase_with_nothing_after_it_is_sent_after_hold_slow_s(tmp_path):
    live = _live(tmp_path)
    live.lang.set_mode("ro-en")
    live.lc.update(phrase_min_words=0, hold_dangling=True, hold_slow_s=0.2, hold_s=5)
    t = time.time() - 3
    live._on_clause(_clause(1, t, t + 2, ro="aduc condoleanțe din partea familiei de."))
    time.sleep(0.5)
    live._drain()
    assert [r["source"] for r in _log(live)] == ["aduc condoleanțe din partea familiei de."]


# --- idea 16: repeated overlap ---------------------------------------------------------------
def test_trim_drops_a_repeated_start_numbers_by_value():
    prev = "Ragosh Puha was born on May nineteenth, nineteen sixty-three."
    assert overlap.trim(prev, "19, 1963, in Vicovisus, Souchava, Romania.") == \
        ("in Vicovisus, Souchava, Romania.", "19, 1963")
    assert overlap.trim("to have good church attendance. In two thousand twelve",
                        "2012 they began hosting Friday evening") == ("they began hosting Friday evening", "2012")
    assert overlap.trim("waiting like Habakkuk this morning.", "like Habakkuk this morning. Amen") == \
        ("Amen", "like Habakkuk this morning")


def test_trim_is_conservative():
    assert overlap.trim("and Claudia.", "Claudia, and Michael.") == ("Claudia, and Michael.", "")   # one word
    assert overlap.trim("He knew his job", "and he did it well.") == ("and he did it well.", "")
    assert overlap.trim("", "Hello there.") == ("Hello there.", "")
    assert overlap.trim("verse five.", "5. It says a father.") == ("It says a father.", "5")          # a number


def test_merging_two_english_clauses_trims_the_overlap():
    a = _clause(1, 0, 3, multi="to have good church attendance. In two thousand twelve")
    b = _clause(2, 3.5, 7, multi="2012 they began hosting Friday evening", ro="")
    m = merge_clauses(a, b)
    assert m["multi"] == "to have good church attendance. In two thousand twelve they began hosting Friday evening"
    far = merge_clauses(a, {**b, "t_start": 9})                              # after a long pause: said twice
    assert far["multi"].endswith("twelve 2012 they began hosting Friday evening")


def test_english_phrase_repeating_the_last_one_is_trimmed_before_translation(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or f"RO: {t}")
    live.lang.set_mode("en-ro")
    t = time.time() - 20
    live._handle(_clause(1, t, t + 4, multi="Ragosh Puha was born on May nineteenth, nineteen sixty-three."))
    live._handle(_clause(2, t + 4.3, t + 8, multi="19, 1963, in Vicovisus, Souchava, Romania."))
    live._handle(_clause(3, t + 8.3, t + 10, multi="Souchava, Romania."))               # all of it a repeat
    recs = _log(live)
    assert recs[1]["source"] == "in Vicovisus, Souchava, Romania." and recs[1]["overlap_trimmed"] == "19, 1963"
    assert recs[2]["outcome"] == "overlap_repeat" and "Souchava, Romania." not in seen
    live.lc["overlap_trim"] = False
    live._handle(_clause(4, t + 10.3, t + 12, multi="Souchava, Romania, to his family."))
    assert _log(live)[-1]["source"] == "Souchava, Romania, to his family."


# --- idea 18: songs --------------------------------------------------------------------------
def test_song_key_holds_captions_and_shows_the_symbol(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or f"EN: {t}")
    live.lang.set_mode("ro-en")
    t = time.time() - 20
    live._handle(_clause(1, t, t + 4, ro="Binecuvântat să fie numele Domnului."))
    live.song(True)
    assert live.state()["song"] is True and live.state()["song_auto"] is False
    assert live.pp.shown[-1] == "♪" and live.caps.text == ""
    live._handle(_clause(2, t + 5, t + 9, ro="îmbete, căci am trăit curaj."))
    assert seen == ["Binecuvântat să fie numele Domnului."]                     # lyrics not translated
    assert live._show("late translation")[2] == "song"
    live.lc["song_symbol"] = ""
    live.song(False)
    assert live.pp.shown[-1] == "" and live.state()["song"] is False
    live._handle(_clause(3, t + 10, t + 14, ro="Dragii mei, ne-am adunat astăzi aici."))
    recs = _log(live)
    assert [r.get("outcome") or r.get("event") for r in recs] == ["ok", "song", "song", "song", "ok"]
    assert [r["on"] for r in recs if r.get("event") == "song"] == [True, False]
    assert report.summary(recs)["failed"] == 0


def _phrase(det, t, text, speech_db, peak_db, dur=4.0, gain=18.0):
    return det.update(text, t, t + dur, {"speech_db": speech_db, "peak_db": peak_db}, gain)


def _speech(det, t0, n=10):
    for i in range(n):
        _phrase(det, t0 + 5 * i, "frați și surori, astăzi citim din epistola către Romani.", -21, -4)


def test_song_detector_starts_on_loud_steady_slow_lines_and_ends_at_speech():
    det = song.SongDetector()
    _speech(det, 0)
    states = [_phrase(det, 100 + 5 * i, line, -6, -1.5)[0] for i, line in enumerate(
        ["va fi o dimineață.", "când ne vom ridica.", "spre cer, spre noua țară.", "cu toții vom pleca."])]
    assert states == [False, False, True, True]
    on, f = _phrase(det, 130, "Binecuvântăm numele Tatălui, al Fiului și al Duhului Sfânt.", -19, -4)
    assert not on and f["crest_db"] == 15


def test_song_detector_ignores_a_loud_slow_preacher_and_gain_changes():
    det = song.SongDetector()
    _speech(det, 0)
    for i in range(6):                                         # +20 dB but speech-like peaks
        on, _ = _phrase(det, 100 + 4 * i, "139, versetul 16.", -8, 4.0, gain=23)
        assert not on
    det2 = song.SongDetector()
    _speech(det2, 0)
    for i in range(6):                                         # the gain went up 15 dB: same voice
        on, _ = _phrase(det2, 100 + 4 * i, "frați și surori, astăzi citim din epistola către Romani.", -6, 11, gain=33)
        assert not on


def test_song_detect_setting_holds_and_releases_by_itself(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or f"EN: {t}")
    live.lang.set_mode("ro-en")
    levels = {}
    live._input_fields = lambda ev: {"gain_db": 18.0, "gain_auto": False, "level": levels[ev["id"]]}
    t, i = time.time() - 200, 0

    def say(text, speech_db, peak_db):
        nonlocal i
        i += 1
        levels[i] = {"speech_db": speech_db, "peak_db": peak_db}
        live._handle(_clause(i, t + 5 * i, t + 5 * i + 4, ro=text))

    for _ in range(10):
        say("Frați și surori, astăzi citim din epistola către Romani.", -21, -4)
    for line in ("va fi o dimineață.", "când ne vom ridica.", "spre cer, spre noua țară.", "cu toții vom pleca."):
        say(line, -6, -1.5)
    assert not live.song_on                                     # off by default
    live.lc["song_detect"] = True
    for line in ("va fi o dimineață.", "când ne vom ridica.", "spre cer, spre noua țară."):
        say(line, -6, -1.5)
    assert live.song_on and live.song_auto and live.pp.shown[-1] == "♪"
    say("Binecuvântăm numele Tatălui, al Fiului și al Duhului Sfânt.", -19, -4)
    assert not live.song_on and seen[-1] == "Binecuvântăm numele Tatălui, al Fiului și al Duhului Sfânt."
    events = [r for r in _log(live) if r.get("event") == "song"]
    assert [(e["on"], e["auto"]) for e in events] == [(True, True), (False, True)]


def test_operator_ending_a_detected_song_keeps_the_detector_quiet(tmp_path):
    live = _live(tmp_path)
    live.song(True, auto=True)
    live.song(False)                                            # the operator: it was not a song
    assert not live.song_on and live._song_snooze > time.time() + 60
    live.song(True, auto=True)
    live.song(True)                                             # pressed during a detected song: theirs now
    assert live.song_on and not live.song_auto
