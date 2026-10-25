"""scripts/clip_recording.py: which moments of a service recording are kept, and how they are cut."""
import importlib.util
import json
import pathlib

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("clip_recording", ROOT / "scripts" / "clip_recording.py")
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

T0 = 1_000_000.0


def _ok(t, lang="ro", src="Frați și surori, astăzi citim din Romani.", **kw):
    return {"outcome": "ok", "t_start": T0 + t, "t_end": T0 + t + 4, "lang": lang, "source": src,
            "translation": "x", **kw}


def test_moments_flag_what_is_worth_replaying_and_not_the_rest():
    recs = [_ok(10), _ok(20), {"outcome": "low_confidence", "t_start": T0 + 30, "source": "slava ludum"},
            {"outcome": "empty_stt", "t_start": T0 + 35},                    # silence: not a moment
            _ok(40, lang="en", src="and"), _ok(50),                        # flips straight back
            _ok(60, lang="en", src="We thank you as a family for being here with us"),
            _ok(70, lang="en", src="I have spent much of my life trying to understand him"),   # sustained: fine
            _ok(80, verse=[{"ref": "Philippians 1:21"}])]
    m = C.moments(recs, [{"t": T0 + 5, "note": "Romanian on the English screen"}])
    whys = [w for _, _, w in m]
    assert whys[0].startswith("mark:") and m[0][1] == 0
    assert any(w.startswith("low_confidence") for w in whys)
    assert any("switch to en and back" in w for w in whys)
    assert not any("We thank you" in w for w in whys)            # a real switch to English is not flagged
    assert not any("empty_stt" in w for w in whys)
    assert any("Philippians 1:21" in w for w in whys)


def test_clipping_is_sampled_not_kept_everywhere():
    recs = [_ok(i * 5, level={"peak_db": -0.2}) for i in range(100)]
    assert sum("clipping" in w for _, _, w in C.moments(recs, [])) == 4


def test_plan_merges_caps_and_never_repeats_audio():
    moms = [(T0 + 100, 1, "a"), (T0 + 104, 1, "b"), (T0 + 400, 2, "c"), (T0 + 1000, 0, "mark")]
    clips = C.plan(moms, T0, T0 + 2000, before=20, after=10, max_s=70)
    assert [round(c["start"] - T0) for c in clips] == [80, 980]   # a+b merged; c over the budget; the mark always
    assert clips[0]["why"] == ["a", "b"]
    long = [(T0 + 100 + 20 * i, 1, str(i)) for i in range(20)]   # a busy stretch
    clips = C.plan(long, T0, T0 + 2000, before=20, after=10, max_s=3600, max_clip=90)
    assert all(c["end"] - c["start"] <= 90 for c in clips)
    assert all(a["end"] <= b["start"] for a, b in zip(clips, clips[1:]))


def test_cut_writes_replayable_clips_and_deletes_the_full_recording(tmp_path, monkeypatch):
    rec = tmp_path / "2026-10-25_100000.wav"
    C.bench._write_wav(rec, np.zeros(16000 * 120, dtype="<i2").tobytes(), 16000)     # 2 minutes
    rec.with_suffix(".json").write_text(json.dumps({"start_wall": T0, "start_iso": "2026-10-25T10:00:00",
                                                    "samplerate": 16000}))
    log = tmp_path / "service.jsonl"
    log.write_text(json.dumps({"outcome": "mt_overlong", "t_start": T0 + 60, "source": "T."}) + "\n")
    monkeypatch.setattr(C, "RECORDINGS", tmp_path)
    args = type("A", (), {"rec": str(rec), "log": str(log), "before": 20.0, "after": 10.0,
                          "max_min": 30.0, "delete_full": True})()
    C.cut(args, really=True)
    clips = sorted((tmp_path / "clips" / rec.stem).glob("*.wav"))
    assert len(clips) == 1 and not rec.exists()
    meta = json.loads(clips[0].with_suffix(".json").read_text())
    assert abs(meta["start_wall"] - (T0 + 40)) < 0.01 and "mt_overlong" in meta["why"][0]
    assert (tmp_path / "clips" / rec.stem / "index.md").exists()
