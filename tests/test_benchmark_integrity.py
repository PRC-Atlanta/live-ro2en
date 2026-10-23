"""Benchmark plumbing must not make a failed or partial run look better than it is."""
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "finetune"))


def test_empty_and_truncated_hypotheses_are_penalised():
    from score_bible import excerpt_spans
    from scoring import score
    book = [f"w{i}" for i in range(500)]
    passage = book[100:140]
    hk_of = {("good", "ex"): passage, ("other", "ex"): passage[:5] + ["x"] + passage[6:],
             ("cut", "ex"): passage[:20], ("empty", "ex"): []}
    span = excerpt_spans(hk_of, {"ex": 1}, {1: book})
    assert span["ex"] == (100, 140)                    # median of the models that found it
    ref = book[slice(*span["ex"])]
    s = {m: score(ref, hk, free_ends=False) for (m, _), hk in hk_of.items()}
    assert s["good"]["wer"] == 0 and {x["n_ref"] for x in s.values()} == {40}
    assert s["cut"]["del"] == 20 and s["empty"]["wer"] == 1.0


def test_google_output_stem_respects_separator():
    from google_stt import output_stem
    stems = ["br-random-1", "br-random-10"]
    assert output_stem("br-random-10_transcript_0.json", stems) == "br-random-10"
    assert output_stem("br-random-1_transcript_0.json", stems) == "br-random-1"
    assert output_stem("other_transcript_0.json", stems) is None


def test_eval_sermons_always_held_out(tmp_path):
    import build_manifests as bm
    (tmp_path / "work" / "sermon").mkdir(parents=True)
    (tmp_path / "work" / "sermon" / "manifest.csv").write_text(
        "segment,excerpt,book_stem\ns1,ev-a_00010,ev-a\n")
    (tmp_path / "work" / "archive").mkdir()
    (tmp_path / "work" / "archive" / "manifest.csv").write_text("segment,excerpt,book_stem\nx,x,train-1\n")
    (tmp_path / "review_packet").mkdir()
    (tmp_path / "review_packet" / "packet.json").write_text(json.dumps({"clips": [{"excerpt": "ev-b_00349"}]}))
    forced = bm.eval_sermons(tmp_path)
    assert forced == {"ev-a", "ev-b"}
    sermons = ["ev-a", "ev-b"] + [f"train-{i}" for i in range(20)]
    for seed in range(5):
        hold = bm.split_sermons(sermons, 0.1, seed, forced)
        assert forced <= hold and len(hold) == 2       # the forced ones fill the 10 % share
    assert len(bm.split_sermons(sermons, 0.25, 0, forced)) == 6


def test_stt_queue_reports_failed_model(tmp_path):
    fake = tmp_path / "venv" / "bin"
    fake.mkdir(parents=True)
    (fake / "python").write_text("#!/bin/sh\necho boom\nexit 3\n")
    (fake / "python").chmod(0o755)
    logs = tmp_path / "logs"
    logs.mkdir()
    import os
    r = subprocess.run(["bash", str(ROOT / "scripts" / "run_stt_queue.sh"), str(tmp_path / "venv"),
                        "m.csv", "segs", "pytest-queue", "fake-model"], capture_output=True, text=True,
                       env={**os.environ, "LOGS": str(logs)})
    assert r.returncode == 1
    assert "exit=3" in r.stdout and "QUEUE-FAILED: fake-model" in r.stdout
    assert (logs / "stt_pytest-queue_fake-model.log").read_text().strip() == "boom"   # the model really ran
