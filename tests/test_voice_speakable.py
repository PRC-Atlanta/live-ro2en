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
