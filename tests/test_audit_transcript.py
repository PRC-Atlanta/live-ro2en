"""scripts/audit_transcript.py: transcript parsing, doubtful-word list, silence map, page build."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import audit_transcript as A                              # noqa: E402

TEXT = """# Transcript (UNVERIFIED): x.ogg

Supplied by the user, not checked. Words that need a listen: "Vehează" (veghează),
"fal și destruction" (fall and destruction).

Prima linie.
Vehează, deci, asupra ta.
A treia linie.
"""


def test_parse_splits_title_and_note_from_spoken_lines():
    pre, lines = A.parse_transcript(TEXT)
    assert pre[0].startswith("# Transcript") and pre[1] == "" and pre[2].startswith("Supplied")
    assert lines == ["Prima linie.", "Vehează, deci, asupra ta.", "A treia linie."]


def test_parse_without_a_title_keeps_every_line():
    assert A.parse_transcript("unu\ndoi\n\ntrei\n") == ([], ["unu", "doi", "trei"])


def test_title_only_file_keeps_its_single_block_as_the_body():
    assert A.parse_transcript("# T\n\nunu\ndoi\n") == (["# T"], ["unu", "doi"])


def test_suspect_terms_are_the_quoted_words_of_the_note():
    pre, _ = A.parse_transcript(TEXT)
    assert A.suspect_terms(pre) == ["Vehează", "fal și destruction"]


def test_silences_become_speech_stretches():
    log = ("[silencedetect @ 0x1] silence_start: 0\n[silencedetect @ 0x1] silence_end: 2.5 | d: 2.5\n"
           "[silencedetect @ 0x1] silence_start: 10\n[silencedetect @ 0x1] silence_end: 12 | d: 2\n"
           "[silencedetect @ 0x1] silence_start: 58.5\n")
    assert A.parse_silences(log, 60.0) == [[2.5, 10.0], [12.0, 58.5]]
    assert A.parse_silences("", 60.0) == [[0.0, 60.0]]


def test_build_embeds_text_and_audio_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(A.shutil, "which", lambda _: None)
    t, a, out = tmp_path / "s.transcript.md", tmp_path / "s.ogg", tmp_path / "s.audit.html"
    t.write_text(TEXT, encoding="utf-8")
    a.write_bytes(b"OggS</script>")
    d = A.build(t, a, out, "original")
    page = out.read_text(encoding="utf-8")
    assert d["name"] == "s" and len(d["lines"]) == 3
    assert "data:audio/ogg;base64," in page and "Vehează, deci, asupra ta." in page
    assert "__DATA__" not in page and "__AUDIO__" not in page
