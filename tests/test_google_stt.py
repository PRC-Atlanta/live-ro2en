"""Google module tests with mocked Speech and Storage clients (no network)."""
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from google.api_core import exceptions as gexc
from google.cloud.speech_v2.types import cloud_speech as cs

import google_stt as g

ENV = {"GOOGLE_APPLICATION_CREDENTIALS": "/dev/null", "GCP_PROJECT_ID": "proj",
       "GCS_BUCKET": "bkt", "GOOGLE_STT_LOCATION": "us-central1"}
CFG = {"model": "chirp_2", "language_codes": ["ro-RO"], "strategy": "DYNAMIC_BATCHING",
       "usage_cap_minutes": 10, "input_prefix": "input/", "output_prefix": "output/",
       "max_files_per_request": 2, "phrase_boost": 10}


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("OPS_FILE", "USAGE_FILE"):
        monkeypatch.setattr(g, name, tmp_path / "logs" / pathlib.Path(getattr(g, name)).name)
    monkeypatch.setattr(g, "OUT_DIR", tmp_path / "out")
    monkeypatch.setattr(g, "FLAC_DIR", tmp_path / "flac")
    monkeypatch.setattr(g, "DECISIONS_FILE", tmp_path / "DECISIONS.md")
    files = []
    for n in ("a", "b", "c"):
        f = tmp_path / f"{n}.flac"
        f.write_bytes(b"x")
        files.append(f)
    clients = {}

    def factory(endpoint):
        c = MagicMock()
        c.batch_recognize.side_effect = lambda request: SimpleNamespace(
            operation=SimpleNamespace(name=f"op-{len(c.batch_recognize.call_args_list)}"))
        clients[endpoint] = c
        return c

    storage = MagicMock()
    storage.bucket.return_value.blob.return_value.exists.return_value = False
    stt = g.GoogleSTT(cfg=dict(CFG), env=ENV, speech_factory=factory, storage_client=storage,
                      ffprobe=lambda p: 3.0)
    return SimpleNamespace(stt=stt, files=files, clients=clients, storage=storage, tmp=tmp_path)


def test_regional_endpoint_and_recognizer(env):
    s = env.stt
    assert s.recognizer("chirp_2") == "projects/proj/locations/us-central1/recognizers/_"
    assert s.endpoint_for(s.location_for("chirp_2")) == "us-central1-speech.googleapis.com"
    assert s.recognizer("chirp_3") == "projects/proj/locations/us/recognizers/_"


def test_request_construction(env):
    req = env.stt.build_request(["gs://bkt/input/a.flac"], "chirp_2", "r1", dynamic=True)
    assert req.processing_strategy == cs.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING
    assert req.config.model == "chirp_2" and list(req.config.language_codes) == ["ro-RO"]
    assert req.config.features.enable_automatic_punctuation and req.config.features.enable_word_time_offsets
    assert "auto_decoding_config" in req.config
    assert req.recognition_output_config.gcs_output_config.uri == "gs://bkt/output/r1/"
    assert len(req.config.adaptation.phrase_sets[0].inline_phrase_set.phrases) > 100


def test_submit_batches_logs_and_persists(env):
    names = env.stt.submit(env.files[:2], tag="t")
    assert len(names) == 1                                   # 2 files per request
    ops = g.load_ops()
    assert ops[names[0]]["files"] == ["a", "b"] and ops[names[0]]["strategy"] == "DYNAMIC_BATCHING"
    assert env.stt.minutes_used() == 6.0
    # resubmitting the same files is a no-op (re-attach, don't resubmit)
    assert env.stt.submit(env.files[:2], tag="t") == []


def test_cap_enforced(env):
    env.stt.submit(env.files[:2])                            # 6 of 10 minutes
    env.stt.cfg["usage_cap_minutes"] = 8
    with pytest.raises(g.CapExceeded):
        env.stt.submit(env.files[2:])                        # 6 + 3 > 8
    assert len(g.load_ops()) == 1
    env.stt.cfg["usage_cap_minutes"] = 10
    assert env.stt.submit(env.files[2:]) and env.stt.minutes_used() == 9.0


def test_dynamic_batch_fallback(env):
    calls = []

    def factory(endpoint):
        c = MagicMock()

        def br(request):
            calls.append(request.processing_strategy)
            if request.processing_strategy:
                raise gexc.InvalidArgument("dynamic batching not supported")
            return SimpleNamespace(operation=SimpleNamespace(name="op-std"))
        c.batch_recognize.side_effect = br
        return c
    env.stt._speech_factory = factory
    env.stt._speech = {}
    env.stt.submit(env.files[:1])
    assert g.load_ops()["op-std"]["strategy"] == "STANDARD" and len(calls) == 2
    assert "fell back" in (env.tmp / "DECISIONS.md").read_text()


def test_parse_results():
    data = {"results": [
        {"alternatives": [{"transcript": "La început", "confidence": 0.9,
                           "words": [{"word": "La", "startOffset": "0.400s", "endOffset": "0.600s"},
                                     {"word": "început", "startOffset": "0.600s", "endOffset": "1.200s"}]}],
         "resultEndOffset": "1.300s", "languageCode": "ro-ro"},
        {"alternatives": [{}], "resultEndOffset": "2s"},
        {"alternatives": [{"transcript": " Dumnezeu "}], "resultEndOffset": "3.5s"}]}
    text, segs = g.parse_results(data)
    assert text == "La început Dumnezeu"
    assert segs[0]["start"] == 0.4 and segs[0]["end"] == 1.3 and segs[0]["words"][1]["end"] == 1.2
    assert segs[1]["start"] == 2.0 and segs[1]["end"] == 3.5


def test_poll_downloads_results(env):
    name = env.stt.submit(env.files[:1], tag="t")[0]
    client = env.clients["us-central1-speech.googleapis.com"]
    op = MagicMock(done=True)
    op.HasField.return_value = False
    op.response.value = cs.BatchRecognizeResponse.serialize(cs.BatchRecognizeResponse())
    client.transport.operations_client.get_operation.return_value = op
    blob = MagicMock()
    blob.name = g.load_ops()[name]["output"].split("gs://bkt/")[1] + "a_transcript_123.json"
    blob.download_as_bytes.return_value = json.dumps(
        {"results": [{"alternatives": [{"transcript": "Amin"}], "resultEndOffset": "1s"}]}).encode()
    env.storage.list_blobs.return_value = [blob]
    env.stt.poll(once=True)
    assert g.load_ops()[name]["status"] == "done"
    assert (env.tmp / "out" / "chirp_2" / "a.txt").read_text().strip() == "Amin"


def test_live_app_does_not_import_google():
    """AST check: nothing the live app loads imports google_stt or google.cloud."""
    import ast
    src = pathlib.Path(g.__file__).parent
    files = list(src.glob("live/*.py")) + [src / "stt_run.py", src / "textnorm.py", src / "scoring.py"]
    for p in files:
        for node in ast.walk(ast.parse(p.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for n in names:
                assert "google_stt" not in n and not n.startswith("google.cloud"), (p, n)


def test_long_file_split_and_merge(env, monkeypatch):
    import numpy as np, soundfile as sf
    long = env.tmp / "long.flac"
    x = (np.random.randn(16000 * 50) * 0.1).astype("float32")
    x[16000 * 24:16000 * 26] = 0                       # a pause near the middle
    sf.write(long, x, 16000, format="FLAC")
    env.stt.cfg["max_file_minutes"] = 0.5               # 30 s parts -> 2 parts
    env.stt._ffprobe = g.GoogleSTT._ffprobe_minutes
    env.stt.cfg["usage_cap_minutes"] = 100
    names = env.stt.submit([long], tag="t")
    info = g.load_ops()[names[0]]
    assert sorted(info["parts"]) == ["long__p00", "long__p01"]
    off = info["parts"]["long__p01"][1]
    assert 23.9 < off < 26.1                             # cut inside the pause
    dest = env.tmp / "out" / "chirp_2"; dest.mkdir(parents=True)
    for p, o, w in (("long__p00", 0, "Unu"), ("long__p01", off, "Doi")):
        (dest / f"{p}.json").write_text(json.dumps({"segments": [{"start": 1.0, "end": 2.0, "text": w,
                                                                    "words": [{"word": w, "start": 1.0, "end": 2.0}]}]}))
    g.GoogleSTT.merge_parts(info, dest)
    merged = json.loads((dest / "long.json").read_text())
    assert (dest / "long.txt").read_text().strip() == "Unu Doi"
    assert abs(merged["segments"][1]["words"][0]["start"] - (1.0 + off)) < 1e-6
