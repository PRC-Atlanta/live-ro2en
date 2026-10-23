"""Spot check: find only the locked words where the alternative differs, and map blind
A/B answers back onto the reference."""
from spot_check import apply_answers, find_spots


def test_spots_are_only_locked_disagreements():
    google = "eu m-am luptat lupta cea bună am sfârșit alergarea"
    local = "eu m-am luptat lupta cea bună am sfîrșit alergare"          # differs at the end: highlighted
    ref = "eu m-am luptat lupta cea bună am sfârșit alergarea"            # reviewer kept Google's
    alt = "eu m-am luptat luptat cea bună am sfârșit alergaria"           # Whisper
    spots = find_spots(ref, google, local, alt)
    assert [(s["ref"], s["alt"]) for s in spots] == [("lupta", "luptat")]   # 'alergarea' was editable
    assert spots[0]["before"].endswith("luptat") and spots[0]["after"].startswith("cea")


def test_inserted_word_is_a_spot():
    t = "mai avem noi o cântare românii și o cântăm"
    spots = find_spots(t, t, t, "mai avem noi o cântare în românii și o cântăm")
    assert [(s["ref"], s["alt"], s["w0"], s["w1"]) for s in spots] == [("", "în", 5, 5)]


def test_answers_map_back_through_the_random_swap():
    corr = {"x": "eu m-am luptat lupta cea bună"}
    spot = {"id": "x#0", "excerpt": "x", "w0": 3, "w1": 4, "ref": "lupta", "alt": "luptat"}
    swapped = {**spot, "swap": True}                     # A shows Whisper's version
    assert apply_answers(corr, [swapped], {"x#0": {"choice": "A"}})[0]["x"].endswith("luptat cea bună")
    assert apply_answers(corr, [swapped], {"x#0": {"choice": "B"}})[0]["x"] == corr["x"]
    plain = {**spot, "swap": False}
    new, tally = apply_answers(corr, [plain], {"x#0": {"choice": "other", "text": "luptă"}})
    assert new["x"] == "eu m-am luptat luptă cea bună" and tally["other"] == 1
    assert apply_answers(corr, [plain], {})[1]["unanswered"] == 1
