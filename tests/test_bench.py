"""The test bench (scripts/bench.py) and the engine's audio clock for bench replays."""
import importlib.util
import pathlib
import queue
import sys
import threading

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "live"))
import engine  # noqa: E402

spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def test_bench_clock_reads_the_environment(monkeypatch):
    monkeypatch.delenv("LIVE_FILE_SPEED", raising=False)
    assert engine.bench_clock() is None
    monkeypatch.setenv("LIVE_FILE_SPEED", "3")
    monkeypatch.setenv("LIVE_FILE_T0", "1000.5")
    assert engine.bench_clock() == (3.0, 1000.5)
    monkeypatch.setenv("LIVE_FILE_SPEED", "fast")
    assert engine.bench_clock() is None


def test_file_source_stamps_frames_on_the_audio_clock(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_FILE_SPEED", "50")
    monkeypatch.setenv("LIVE_FILE_T0", "1000.0")
    monkeypatch.setattr(engine, "read_audio", lambda p: np.zeros(engine.SR, dtype=np.float32))   # 1 s
    q, stop = queue.Queue(), threading.Event()
    engine.file_source("x.wav", True, q, stop)
    items = []
    while (it := q.get()) is not None:
        items.append(it)
    ts = [t for t, _ in items]
    assert abs(ts[0] - (1000.0 + engine.FRAME / engine.SR)) < 1e-9
    assert abs(ts[-1] - 1000.0 - len(items) * engine.FRAME / engine.SR) < 1e-9   # recording time, not wall time


def test_wav_cut_and_layout_of_a_growing_file(tmp_path):
    pcm = (np.arange(48000 * 3) % 100).astype("<i2").tobytes()     # 3 s at 48 kHz
    src = tmp_path / "rec.wav"
    bench._write_wav(src, pcm, 48000)
    raw = bytearray(src.read_bytes())
    raw[4:8] = raw[40:44] = b"\x00\x00\x00\x00"                     # sizes not written yet (still recording)
    src.write_bytes(raw)
    src.with_suffix(".json").write_text('{"start_wall": 100.0, "start_iso": "2026-10-24T12:00:00"}')
    assert bench._wav_layout(src) == (44, 1, 48000, 2)
    out = tmp_path / "seg.wav"
    assert bench.cut(src, 101.0, 102.0, out) == 101.0
    assert bench._wav_layout(out)[0] == 44 and out.stat().st_size == 44 + 48000 * 2


def test_judge_errors_line_by_line():
    day = "2026-10-24"
    t = bench._clock(day, "12:49:30")
    ph = [{"outcome": "ok", "t_start": t - 2, "t_end": t, "translation": "He gave up."},
          {"outcome": "ok", "t_start": t, "t_end": t + 3, "translation": "Ed."}]
    assert bench.judge({"t": "12:49:30", "bad": r"^Ed\.?$"}, ph, day)[0] == "still"
    assert bench.judge({"t": "12:49:30", "bad": r"polarize"}, ph, day)[0] == "fixed"
    assert bench.judge({"t": "12:49:30", "good": r"renun"}, ph, day)[0] == "still"
    assert bench.judge({"t": "12:49:30", "bad": r"x", "good": r"renun"}, ph, day)[0] == "fixed?"
    assert bench.judge({"t": "13:30:00", "bad": r"x"}, ph, day)[0] == "missing"
    assert bench.metrics(ph)["short_lt5"] == 1.0



def test_timed_reference_reads_times_pairs_lines_and_drops_tags(tmp_path, monkeypatch):
    f = tmp_path / "s.txt"
    f.write_text("# Title\r\n\r\nintro\r\n\r\n---\r\n\r\n"
                 "**[0:06]** Bună seara [voce dreasă] tuturor.\\\r\n*Good evening [clears throat] everyone.*\r\n\r\n"
                 "**[0:59]** [voce dreasă]\\\r\n*[clears throat]*\r\n\r\n"
                 "**[1:02:03]** Amin.\\\r\n*Amen [to that].*", encoding="utf-8")
    monkeypatch.setitem(bench.SERMONS, "t", {"timed": str(f)})
    lines, end = bench.reference("t")
    assert end is None
    assert lines == [{"ro": "Bună seara tuturor.", "en": "Good evening everyone.", "t": 6},
                     {"ro": "Amin.", "en": "Amen to that.", "t": 3723}]


def test_windows_keep_whole_lines_and_place_phrases_by_their_middle():
    lines = [{"t": t} for t in (0, 8, 19, 21, 30, 45)]
    phrases = [(0.0, 5.0), (17.0, 24.0), (39.0, 46.0), (46.0, 50.0)]
    w = bench.windows(lines, phrases, 20.0)
    assert [x["lines"] for x in w] == [[0, 1, 2], [3, 4], [5]]
    assert [x["phrases"] for x in w] == [[0, 1], [2], [3]]       # middles 2.5, 20.5 | 42.5 | 48
    assert [(x["t0"], x["t1"]) for x in w] == [(0, 21), (21, 45), (45, 50.0)]
