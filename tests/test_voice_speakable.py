"""Live Voice: speakable text (plan §4.1, §14) and the pinned model files (§7)."""
import pathlib

import pytest

from voice import model, speakable

S = speakable.speakable


@pytest.mark.parametrize("lang,text,want", [
    ("ro", "Deschideți la Romani 8:28.", "Deschideți la Romani 8, versetul 28."),
    ("ro", "Citim din 1 Corinteni 13:4-7.", "Citim din Întâi Corinteni 13, versetele 4 până la 7."),
    ("ro", "2 Timotei 3:16 ne spune", "A doua Timotei 3, versetul 16 ne spune"),
    ("ro", "3 Ioan 1:4", "A treia Ioan 1, versetul 4"),
    ("ro", "Geneza 1:1: La început", "Geneza 1, versetul 1: La început"),
    ("ro", "Psalmul 23:1", "Psalmul 23, versetul 1"),
    ("ro", "La 3:40 dimineața, ora 10:30.", "La 3:40 dimineața, ora 10:30."),          # a clock time stays
    ("ro", "Apocalipsa 21:4 spune „că El” va", "Apocalipsa 21, versetul 4 spune că El va"),
    ("ro", "În anul 1989 erau 2.500 de oameni.", "În anul 1989 erau 2.500 de oameni."),
    ("en", "Open your Bibles to John 3:16.", "Open your Bibles to John 3, verse 16."),
    ("en", "in Second Corinthians 5:7 that", "in Second Corinthians 5, verse 7 that"),
    ("en", "We read from 1 Corinthians 13:4-7.", "We read from First Corinthians 13, verses 4 to 7."),
    ("en", "2 Timothy 3:16 and 3 John 1:4", "Second Timothy 3, verse 16 and Third John 1, verse 4"),
    ("en", "on October 15th, at 1:11.", "on October 15th, at 1:11."),
    ("en", "He said, \"don’t be afraid\".", "He said, don’t be afraid."),
    ("en", "It cost 160 dollars and took 3 days.", "It cost 160 dollars and took 3 days."),
])
def test_speakable(lang, text, want):
    assert S(text, lang) == want


def test_numbered_books_come_from_the_book_list():
    names, numbered = speakable.books("ro")
    assert {"Timotei", "Corinteni", "Petru", "Ioan", "Samuel", "Tesaloniceni"} <= set(numbered)
    assert {"Timothy", "Kings", "Chronicles"} <= set(speakable.books("en")[1])


def test_the_voice_model_is_pinned_with_a_hash_for_every_file():
    line = model.lock_line()
    assert line and len(line["rev"]) == 40 and line["size"] > 300_000_000
    hashes = model.hashes()
    assert set(line["files"]) == set(hashes) and all(len(h) == 64 for h in hashes.values())
    assert set(model.NEEDED) <= set(hashes) and "voice_styles/M2.json" in hashes and "LICENSE" in hashes


def test_model_dir_and_verify(tmp_path, monkeypatch):
    lock = tmp_path / "lock.txt"
    import hashlib
    d = tmp_path / "m"
    lines = ["hf Supertone/supertonic-3 " + "a" * 40 + " size=1 profile=voice files=x"]
    for f in model.NEEDED + ("voice_styles/M2.json",):
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_bytes(f.encode())
        lines.append(f"sha256 supertonic-3/{f} {hashlib.sha256(f.encode()).hexdigest()}")
    lock.write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("LIVE_VOICE_MODEL_DIR", str(d))
    assert model.model_dir(lock=lock) == d and model.verify(d, lock) == []
    assert model.model_dir(("M2", "F1"), lock=lock) != d                 # a voice that is not there
    (d / "config.json").write_bytes(b"changed")
    (d / "onnx/tts.json").unlink()
    assert sorted(model.verify(d, lock)) == ["config.json: differs", "onnx/tts.json: missing"]
    monkeypatch.setenv("LIVE_HOME", str(tmp_path / "home"))                 # packaged: never ~/.cache/supertonic3
    assert pathlib.Path("~/.cache/supertonic3").expanduser() not in model.candidates(lock)


@pytest.mark.parametrize("lang,text,want", [
    # a number before a name that is not a book number (M0 review)
    ("en", "In chapter 3 John writes that", "In chapter 3 John writes that"),
    ("ro", "În capitolul 2 Ioan spune că", "În capitolul 2 Ioan spune că"),
    ("en", "Psalm 23, verse 1 John", "Psalm 23, verse 1 John"),
    ("en", "between 2 John's", "between 2 John's"),
    ("ro", "citim din 2 Timotei.", "citim din A doua Timotei."),
    ("ro", "1 Tesaloniceni, capitolul 4", "Întâi Tesaloniceni, capitolul 4"),
    # a clock time after a name
    ("en", "Mark 10:30 am meeting", "Mark 10:30 am meeting"),
    ("en", "at Job 9:00 tomorrow", "at Job 9:00 tomorrow"),
    # ranges and lists are rewritten whole
    ("ro", "Ioan 3:16-4:2", "Ioan 3, versetul 16 până la capitolul 4, versetul 2"),
    ("ro", "Ioan 3:16 și 10:30 ne vedem", "Ioan 3, versetul 16 și 10:30 ne vedem"),
    ("ro", "Ioan 3:16,17", "Ioan 3, versetele 16 și 17"),
    ("en", "John 3:16,17,18 people", "John 3, verse 16,17,18 people"),
    ("en", "Romans 8:28, 29 and 30 men", "Romans 8, verse 28, 29 and 30 men"),
    ("en", "1 John 1:9 and Psalm 90:1-2", "First John 1, verse 9 and Psalm 90, verses 1 to 2"),
])
def test_speakable_review_cases(lang, text, want):
    assert S(text, lang) == want


def test_level_is_bounded_and_does_not_touch_its_input():
    np = pytest.importorskip("numpy")
    from voice import synth
    quiet = np.full(44100, 1e-5, np.float32)
    assert np.abs(synth.level(quiet)).max() <= 1e-4 + 1e-9             # +20 dB at most
    x = np.sin(np.arange(44100) * 0.05).astype(np.float32) * 0.05
    y, before = synth.level(x), x.copy()
    assert (x == before).all() and abs(20 * np.log10(np.sqrt((y[2000:-2000] ** 2).mean())) + 20) < 1.5
    assert np.abs(synth.level(x * 100)).max() <= 10 ** (-1 / 20) + 1e-6
    assert synth.level(np.zeros(0)).shape == (0,) and synth.level(np.ones(1)).shape == (1,)
    assert not np.isnan(synth.level(np.array([np.nan, 0.5, -0.5], np.float32))).any()


def test_candidates_order_and_absent_lock_line(tmp_path, monkeypatch):
    for k in ("HF_HUB_CACHE", "HF_HOME", "LIVE_HOME", "LIVE_VOICE_MODEL_DIR"):
        monkeypatch.delenv(k, raising=False)
    rev = model.lock_line()["rev"]
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    assert model.candidates()[0] == tmp_path / "hf" / "hub" / "models--Supertone--supertonic-3" / "snapshots" / rev
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "hub"))
    assert model.candidates()[0].parts[-4] == "hub" and str(tmp_path / "hub") in str(model.candidates()[0])
    monkeypatch.setenv("LIVE_VOICE_MODEL_DIR", str(tmp_path / "own"))
    assert model.candidates()[0] == tmp_path / "own"
    empty = tmp_path / "lock.txt"
    empty.write_text("hf other/repo " + "b" * 40 + "\n")
    assert model.lock_line(empty) is None and model.hashes(empty) == {}


def test_the_voice_line_is_not_part_of_the_caption_profiles():
    import model_lock
    assert all(i["repo"] != model.REPO for i in model_lock.subset("quality"))


@pytest.mark.parametrize("lang,text,want", [
    ("en", "Mark 10:30 AM meeting", "Mark 10:30 AM meeting"),
    ("ro", "Ioan 3:16 am citit", "Ioan 3, versetul 16 am citit"),                 # "am" is a Romanian verb
    ("ro", "Ioan 3:16-18 am citit", "Ioan 3, versetele 16 până la 18 am citit"),
    ("en", "Psalm 23:1-6 am", "Psalm 23, verses 1 to 6 am"),
    ("en", "verse 3 and 10:30 we meet", "verse 3 and 10:30 we meet"),             # no book: a clock time
    ("ro", "În versetul 28 și 10:30 ne vedem", "În versetul 28 și 10:30 ne vedem"),
    ("en", "John 3:16, 17 people came", "John 3, verse 16, 17 people came"),
    ("en", "John 3:16,17,18.", "John 3, verses 16, 17 and 18."),
    ("ro", "Ioan 3:16-17,20", "Ioan 3, versetele 16 până la 17,20"),              # the range is spoken, the rest left
])
def test_speakable_fix_review_cases(lang, text, want):
    assert S(text, lang) == want
