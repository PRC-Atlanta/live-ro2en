"""Idea 12 (docs/translation_ideas.md, plan of 2026-11-09): Romanian said while the captions are in
English mode is captioned from the Romanian recogniser's transcript, and two such phrases in a row bring
the language back to Romanian. The lines are from the 2026-10-24 eulogy replay (logs/bench/runs/
tts-live-en-1244) and the 2026-11-08 evening notes."""
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
from live import gate                                    # noqa: E402
from live.app import Live                                # noqa: E402
from live.pipeline import romanian_inside_english        # noqa: E402

LEX = gate.lexicon()
ROMANIAN = [   # (the English recogniser's text, the Romanian recogniser's text of the same audio)
    ("He grigod and celeste. Wami in Ludum Neze was gigavin Celeste.", "Oamenii lui Dumnezeu e greu de înțeles."),
    ("Unnom growden Celeste, hard to understand.", "un om greu de înțeles, hai pe dește."),
    ("Dragosh Puha has financed. Dragoskuha financing Kuban evangelized.", "Dragoș Pha a finanțat. cu bani, evanghelizări."),
    ("in FSN capitolopatru, versetus shaptashpe", "în Efeseni capitolul patru, versetul șaptesprezece"),
]
ENGLISH = [    # real English: the Romanian recogniser writes scraps, and nothing changes
    ("And I encourage you guys to take a couple moments", "And ai încurajă iu gaiz tu teic a capăl moments"),
    ("It's not sexual. That's number two, by the way.", "its not sex. Dets nambăr tu bai dă uei."),
    ("He was a true man of God and he loved the Lord.", "hi uoz e tru men of gad end hi lavd dă lord"),
    ("Matthew chapter 25, starting with verse 14.", "Matiu ceaptăr tuenti faiv starting uit vers fortin"),
    ("Praise the Lord for this day.", ""),
    ("Amen.", "Amin."),
]


@pytest.mark.parametrize("en,ro", ROMANIAN)
def test_romanian_said_in_english_mode_is_recognised(en, ro):
    why = romanian_inside_english(en, ro, LEX)
    assert why and why["ro_share"] - why["en_share"] >= 0.2 and why["ro_score"] > 0.15


@pytest.mark.parametrize("en,ro", ENGLISH)
def test_real_english_is_left_alone(en, ro):
    assert romanian_inside_english(en, ro, LEX) is None


class PP:
    last_error = None

    def show(self, text):
        return True

    def clear(self):
        return True


def _live(tmp_path):
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.seen = []
    live.tr = types.SimpleNamespace(translate=lambda t, src, tgt, *a, **k: live.seen.append((src, tgt, t)) or f"[{tgt}] {t}")
    live.pp = PP()
    return live


def _clause(i, t0, multi, ro):
    return {"id": i, "ro": ro, "multi": multi, "en": "", "t_start": t0, "t_end": t0 + 3, "t_emit": t0 + 3.1,
            "stt_s": 0.1, "audio_s": 3.0}


def test_the_romanian_transcript_is_translated_and_two_in_a_row_bring_the_language_home(tmp_path):
    live = _live(tmp_path)
    live.lang.current = "en"                             # an English guest is speaking
    t = time.time() - 60
    live._handle(_clause(1, t, "He was a true man of God and he loved the Lord.", "hi uoz e tru men of gad"))
    assert live.seen[-1][:2] == ("en", "ro") and live.lang.current == "en"
    live._handle(_clause(2, t + 4, *ROMANIAN[2]))
    assert live.seen[-1][:2] == ("ro", "en") and any("finanțat" in s[2] for s in live.seen)
    assert live.lang.current == "en"                     # one such phrase: a quote, the mode stays
    live._handle(_clause(3, t + 8, *ROMANIAN[0]))
    assert live.seen[-1][0] == "ro" and live.lang.current == "ro"     # two in a row: back to Romanian
    recs = [json.loads(l) for l in open(live.log_path)]
    assert [bool(r.get("ro_inside_en")) for r in recs] == [False, True, True]
    assert recs[1]["gate_switched"].startswith("Dragosh") and recs[1]["lang"] == "ro"
    assert live.counts["ro_inside_en"] == 2


def test_an_english_phrase_in_between_resets_the_count_and_a_lock_turns_it_off(tmp_path):
    live = _live(tmp_path)
    live.lang.current = "en"
    t = time.time() - 60
    live._handle(_clause(1, t, *ROMANIAN[2]))
    live._handle(_clause(2, t + 4, "He was a true man of God and he loved the Lord.", "hi uoz e tru men"))
    live._handle(_clause(3, t + 8, *ROMANIAN[0]))
    assert live.lang.current == "en"                     # not two in a row
    live.lang.set_mode("en-ro")                          # locked to an English speaker: nothing is second-guessed
    live._handle(_clause(4, t + 12, *ROMANIAN[1]))
    assert live.seen[-1][0] == "en"
    live.lang.set_mode("auto")
    live.lang.current = "en"
    live.lc["ro_inside_en"] = False                      # and the setting turns it off
    live._handle(_clause(5, t + 16, *ROMANIAN[1]))
    assert live.seen[-1][0] == "en"
