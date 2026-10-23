"""Failures in the live pipeline must be visible (status, warnings, log records, counts),
never look like a quiet room or a successful caption."""
import json
import pathlib
import queue
import sys
import types

import numpy as np
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
from live.app import Live
from live.segmenter import PauseTracker
import live.engine as engmod

CFG = pathlib.Path(__file__).resolve().parents[1] / "config.yaml"


def _live(tmp_path, translate=lambda t, *a, **k: f"<{t}>"):
    live = Live(yaml.safe_load(open(CFG)), types.SimpleNamespace(file=None))
    live.log_path = tmp_path / "log.jsonl"
    live.tr = types.SimpleNamespace(translate=translate)
    live.pp = types.SimpleNamespace(show=lambda text: True, clear=lambda: True, last_error=None)
    return live


def _clause(i, **kw):
    return {"id": i, "ro": "", "multi": "", "en": "", "t_start": i, "t_end": i + 1, "t_emit": i + 1.1,
            "stt_s": 0.1, "audio_s": 1.0, **kw}


def _log(live):
    return [json.loads(l) for l in open(live.log_path)]


class FakeEngine:
    def __init__(self, events, rc):
        self.stdout = iter(json.dumps(e) + "\n" for e in events)
        self.returncode = rc

    def wait(self, *_):
        return self.returncode

    def poll(self):
        return self.returncode


def test_engine_death_is_shown(tmp_path):
    live = _live(tmp_path)
    live.engine = FakeEngine([{"type": "ready", "source": "default"},
                              {"type": "warn", "msg": "multilingual model unavailable: boom"}], rc=1)
    live.running = True
    live._reader(live.engine)
    st = live.state()
    assert not st["running"] and "ENGINE STOPPED (exit 1)" in st["status"]
    assert st["warnings"] == ["multilingual model unavailable: boom"]
    assert [r.get("event") for r in _log(live)] == ["warn", "engine_exit"]


def test_normal_eof_is_not_an_engine_failure(tmp_path):
    live = _live(tmp_path)
    live.engine = FakeEngine([{"type": "ready", "source": "x.wav"}, {"type": "eof"}], rc=0)
    live._reader(live.engine)
    assert live.status == "input ended" and live.eof.is_set()


def test_held_fragment_keeps_english_words(tmp_path):
    seen = []
    live = _live(tmp_path, translate=lambda t, *a, **k: seen.append(t) or "ok")
    live.lang.set_mode("en-ro")
    live._on_clause(_clause(1, en="Good morning church", multi="Good morning church"))   # short: held
    live._on_clause(_clause(2, en="today we read from the Gospel of John.",
                            multi="today we read from the Gospel of John."))
    assert " ".join(seen) == "Good morning church today we read from the Gospel of John."


def test_failed_and_empty_chunks_are_logged_and_counted(tmp_path):
    def tr(text, *a, **k):
        if "eroare" in text:
            raise RuntimeError("translator crashed")
        return "" if "gol" in text else "Brothers and sisters, today we read."
    live = _live(tmp_path, translate=tr)
    live.lang.set_mode("ro-en")
    (tmp_path / "logs").mkdir()
    import live.app as appmod
    real_root, appmod.ROOT = appmod.ROOT, tmp_path
    try:
        live._handle(_clause(1, ro="Frați și surori, astăzi citim din Romani."))
        live._handle(_clause(2, ro="   "))                                # recognizer gave nothing
        live._handle(_clause(3, ro="un text gol aici acum."))              # translator gave nothing
        live._handle(_clause(4, ro="aici este o eroare mare."))            # translator raised
    finally:
        appmod.ROOT = real_root
    assert [r["outcome"] for r in _log(live)] == ["ok", "empty_stt", "empty_mt", "error"]
    assert live.state()["counts"] == {"ok": 1, "empty_stt": 1, "empty_mt": 1, "error": 1}
    assert len(live.lat) == 1 and live.caps.text == "Brothers and sisters, today we read."


def _bare_engine(mode="auto", streamer=None):
    """An Engine without models: VAD reads the frame's first sample as speech probability."""
    e = engmod.Engine.__new__(engmod.Engine)
    e.mode, e.pt, e.pre_roll, e.partial_every = mode, PauseTracker(frame_ms=32), 8, 0
    e.min_commit = e.max_commit = 0
    e.vad = lambda fr: float(fr[0])
    e.streamer, e.wcfg, e._wstream = streamer, {"chunk_s": 0.064}, None
    e.calls = []
    e.transcribe = lambda audio, t0, t1, t_speech=None, final=True: e.calls.append((e.mode, len(audio), final))
    return e


def _frames(q, n, speech, t0=0.0):
    for i in range(n):
        q.put((t0 + i * 0.032, np.full(engmod.FRAME, 1.0 if speech else 0.0, dtype=np.float32)))


def _run(e, q, monkeypatch):
    out = []
    monkeypatch.setattr(engmod, "emit", out.append)
    e.run(q)
    return out


def test_stop_commits_buffered_speech_then_eof(monkeypatch):
    e = _bare_engine()
    q = queue.Queue()
    _frames(q, 20, speech=True)          # mid-sentence, no pause yet
    q.put(None)
    out = _run(e, q, monkeypatch)
    assert e.calls == [("auto", 20 * engmod.FRAME, True)] and out == [{"type": "eof"}]


def test_mode_switch_commits_speech_under_old_mode(monkeypatch):
    fed = []
    streamer = types.SimpleNamespace(start_stream=lambda cb: None, feed=fed.append, end_stream=lambda: None)
    e = _bare_engine("auto", streamer)
    q = queue.Queue()
    _frames(q, 20, speech=True)          # Romanian half sentence
    q.put({"mode": "en-ro"})             # operator locks EN->RO: streaming takes over
    _frames(q, 10, speech=True, t0=1.0)
    q.put({"mode": "auto"})
    _frames(q, 15, speech=True, t0=2.0)
    q.put(None)
    _run(e, q, monkeypatch)
    # the half sentence was transcribed before the switch and not spliced onto later audio
    assert e.calls == [("auto", 20 * engmod.FRAME, True), ("auto", 15 * engmod.FRAME, True)]
    assert sum(len(f) for f in fed) == 10 * engmod.FRAME
