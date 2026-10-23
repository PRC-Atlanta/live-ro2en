"""Benchmark clean-up: errors stay visible, metrics say what they measure."""
import json
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "finetune"))


def test_unparsable_batch_response_is_not_plain_done(tmp_path, monkeypatch, capsys):
    import google_stt as g
    monkeypatch.setattr(g, "OPS_FILE", tmp_path / "ops.json")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/dev/null")
    env = {"GOOGLE_APPLICATION_CREDENTIALS": "/dev/null", "GCP_PROJECT_ID": "p", "GCS_BUCKET": "b"}
    stt = g.GoogleSTT(cfg={"model": "chirp_2"}, env=env,
                      speech_factory=lambda e: client, storage_client=MagicMock(), ffprobe=lambda p: 1.0)
    client = MagicMock()
    op = MagicMock(done=True)
    op.HasField.return_value = False
    op.response.value = b"\xff not a proto"
    client.transport.operations_client.get_operation.return_value = op
    g.save_ops({"op-1": {"status": "pending"}})
    monkeypatch.setattr(stt, "download", lambda info: None)
    assert stt.check("op-1", {"location": "us-central1"})
    status = g.load_ops()["op-1"]["status"]
    assert status.startswith("done, errors unknown (") and status != "done"
    assert "could not parse BatchRecognize response" in capsys.readouterr().err


@pytest.mark.parametrize("script", sorted((ROOT / "scripts").glob("*.sh")), ids=lambda p: p.name)
def test_scripts_parse(script):
    assert subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0


def test_sermon_pipeline_has_no_ls_word_splitting():
    s = (ROOT / "scripts" / "run_sermon_pipeline.sh").read_text()
    assert "$(ls " not in s and 'AUDIO=(Audio/*)' in s


def test_eval_holdout_decoder_option():
    import eval_holdout as eh
    a = eh.parse_args(["--model", "ft.nemo"])
    assert a.decoder == eh.LIVE_DECODER == "ctc-lm-b8"
    assert eh.DECODERS["ctc-lm-b8"] == ("ctc", "lm-4gram-ro.nemo", 8)
    assert eh.parse_args(["--model", "ft.nemo", "--decoder", "tdt-greedy"]).decoder == "tdt-greedy"
    with pytest.raises(SystemExit):
        eh.parse_args(["--model", "ft.nemo", "--decoder", "beam-999"])
    with pytest.raises(SystemExit):
        eh.parse_args([])                               # one of --model / --registry


def test_term_split_by_boost_list():
    from score_bible import boost_terms, split_terms, uses_boost
    assert split_terms({"isus": [10, 9, 1]}, {"isus"}) == {"term_set": "boost list"}
    s = split_terms({"isus": [10, 9, 1], "har": [4, 1, 0]}, {"isus"})
    assert s["term_acc_boosted"] == 90.0 and s["term_acc_unboosted"] == 25.0 and s["term_n_unboosted"] == 4
    assert "isus" in boost_terms()
    assert uses_boost("jackrabbit-ctc-lm-boost") and uses_boost("google-chirp_2")
    assert not uses_boost("jackrabbit-ctc-lm-b8") and not uses_boost("google-chirp_3")


def test_rover_needs_three_voters_and_sort_is_one_metric():
    from scoring import MIN_VOTERS, rover
    from score_sermons import sort_rows
    a, b = ["a", "b", "c"], ["a", "x", "c"]
    assert MIN_VOTERS == 3 and rover([b, a]) == b               # two voters: just the first one
    assert rover([b, a, a]) == a
    rows = [{"model": "m1", "consensus_wer": 1.0, "silver_wer": 9.0},
            {"model": "m2", "consensus_wer": 5.0, "silver_wer": 2.0}]
    sort_rows(rows)
    assert [r["model"] for r in rows] == ["m2", "m1"]          # silver everywhere: silver
    rows.append({"model": "g", "consensus_wer": 3.0})
    rows.append({"model": "few", "consensus_wer": None})
    sort_rows(rows)
    assert [r["model"] for r in rows] == ["m1", "g", "m2", "few"]   # else consensus, blank last
    rows = [{"model": "google-chirp_3", "consensus_wer": 1.0},       # the silver model's own row
            {"model": "m1", "consensus_wer": 2.0, "silver_wer": 9.0},
            {"model": "m2", "consensus_wer": 5.0, "silver_wer": 3.0}]
    sort_rows(rows, "google-chirp_3")
    assert [r["model"] for r in rows] == ["m2", "m1", "google-chirp_3"]   # silver order, reference last


def test_article_o_is_not_a_chapter_number():
    from textnorm import key_tokens, strip_headers
    assert strip_headers(key_tokens("capitolul o femeie a zis")) == key_tokens("capitolul o femeie a zis")
    assert strip_headers(key_tokens("Capitolul un om bogat")) == key_tokens("Capitolul un om bogat")
    assert strip_headers(key_tokens("Psalmul o sută trei Binecuvântează")) == key_tokens("Binecuvântează")
    assert strip_headers(key_tokens("capitolul unu un om")) == key_tokens("un om")


def test_digits_spelled_out_for_scoring():
    from textnorm import key_tokens, num_words
    assert [num_words(n) for n in (0, 7, 12, 21, 100, 123, 200, 999)] == [
        "zero", "șapte", "doisprezece", "douăzeci și unu", "o sută", "o sută douăzeci și trei",
        "două sute", "nouă sute nouăzeci și nouă"]
    assert key_tokens("cei 12 apostoli", numbers=True) == key_tokens("cei doisprezece apostoli")
    assert key_tokens("cei 12 apostoli") != key_tokens("cei doisprezece apostoli")   # opt-in
    assert key_tokens("07 și 3a", numbers=True) == key_tokens("07 și 3a")    # leading zero, mixed: kept
    assert num_words(1260) == "o mie două sute șaizeci" and num_words(2026) == "două mii douăzeci și șase"
    assert num_words(20000) == "douăzeci de mii" and num_words(144000).endswith("patru de mii")
    # both genders fold to one form, on either side: '42 de luni' is spoken 'patruzeci și două'
    assert key_tokens("42 de luni", numbers=True) == key_tokens("patruzeci și două de luni", numbers=True)
    assert key_tokens("1260 de zile", numbers=True) == key_tokens("o mie două sute șaizeci de zile", numbers=True)


def test_review_page_edits_whole_clip_text():
    from review_packet import PAGE
    assert "querySelectorAll('.t').forEach" in PAGE and "t.contentEditable=true" in PAGE
    assert "m.contentEditable" not in PAGE                      # not only the highlights
    assert "querySelector('.t').textContent" in PAGE             # save takes the full clip text
    assert "alte cuvinte" in PAGE and "other words" in PAGE


def test_bible_lm_holds_out_excerpt_chapters_with_margin():
    import build_bible_lm as lm
    excl = lm.held_out({"ps": (19, 54, 56), "gen": (1, 1, 1)})
    assert excl == {(19, c) for c in range(53, 58)} | {(1, 0), (1, 1), (1, 2)}
    assert "bible_verses.jsonl" not in (ROOT / "src" / "build_bible_lm.py").read_text().split('"""')[2]


def test_spot_options_cover_the_same_words():
    from spot_check import apply_answers, find_spots
    for ref, alt, want_ref, want_alt in [
            ("acum să-l iau acum", "acum sa l iau acum", "să-l", "sa l"),          # hyphen split
            ("el a zis bine", "e-a zis bine", "el a", "e-a"),                      # hyphen join
            ("la motivați să ne rugăm", "la motivațe ne rugăm", "motivați să", "motivațe")]:   # merged words
        sp = find_spots(ref, ref, ref, alt)
        assert [(s["ref"], s["alt"]) for s in sp] == [(want_ref, want_alt)]
        picked = apply_answers({"x": ref}, [{**sp[0], "id": "x#0", "excerpt": "x", "swap": False}],
                               {"x#0": {"choice": "B"}})[0]["x"]
        assert picked == alt                                    # Whisper's version, whole


def test_empty_other_answer_is_unanswered():
    from spot_check import apply_answers
    spot = {"id": "x#0", "excerpt": "x", "w0": 1, "w1": 2, "ref": "b", "alt": "c", "swap": False}
    new, tally = apply_answers({"x": "a b d"}, [spot], {"x#0": {"choice": "other", "text": "  "}})
    assert new["x"] == "a b d" and tally["unanswered"] == 1


def test_spot_check_refuses_fully_editable_packet(tmp_path, monkeypatch):
    import json, pytest, spot_check
    (tmp_path / "packet.json").write_text(json.dumps({"set": "sermon", "editable": "all", "clips": []}))
    monkeypatch.setattr(spot_check, "PACKET", tmp_path)
    with pytest.raises(SystemExit, match="fully editable"):
        spot_check.build()


def test_no_training_data_is_an_error(tmp_path, monkeypatch):
    import json, pytest, sys
    import build_manifests as bm
    (tmp_path / "manifest_clean.jsonl").write_text(json.dumps(
        {"audio_filepath": "work/archive/ev-a/0001.wav", "duration": 3.0, "text": "amin", "sermon": "ev-a"}) + "\n")
    monkeypatch.setattr(bm, "D", tmp_path)
    monkeypatch.setattr(bm, "eval_sermons", lambda root=None: {"ev-a"})
    monkeypatch.setattr(sys, "argv", ["build_manifests.py"])
    with pytest.raises(SystemExit, match="no training data"):
        bm.main()


def test_eval_holdout_decoder_with_registry_is_rejected():
    import pytest
    import eval_holdout as eh
    with pytest.raises(SystemExit):
        eh.parse_args(["--registry", "jackrabbit-ctc-lm-b8", "--decoder", "tdt-greedy"])


def test_sermon_pipeline_fails_when_scoring_fails(tmp_path):
    import shutil, subprocess
    root = pathlib.Path(__file__).resolve().parents[1]
    shutil.copytree(root / "scripts", tmp_path / "scripts")
    (tmp_path / "Audio").mkdir(); (tmp_path / "Audio" / "a sermon.wav").write_text("")
    (tmp_path / "logs").mkdir()
    for v in (".venv", ".venv-nemo", ".venv-mlx", ".venv-moondream"):
        b = tmp_path / v / "bin"; b.mkdir(parents=True)
        (b / "python").write_text('#!/bin/sh\ncase "$*" in *score_sermons*) exit 1;; esac\nexit 0\n')
        (b / "python").chmod(0o755)
    r = subprocess.run(["bash", str(tmp_path / "scripts" / "run_sermon_pipeline.sh")], capture_output=True, text=True)
    assert r.returncode == 1 and "INCOMPLETE" in r.stdout and "pipeline done" not in r.stdout
