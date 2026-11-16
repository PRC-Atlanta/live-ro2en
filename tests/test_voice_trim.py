"""Live Voice: the model's silence around a sentence is cut to small margins (replay of 2026-11-16)."""
import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice import synth  # noqa: E402

SR = synth.SR


def _wave(lead_s, speech_s, tail_s):
    t = np.arange(int(speech_s * SR)) / SR
    speech = 0.3 * np.sin(2 * np.pi * 220 * t).astype(np.float32)
    return np.concatenate([np.zeros(int(lead_s * SR), np.float32), speech, np.zeros(int(tail_s * SR), np.float32)])


def test_trim_keeps_the_speech_and_small_margins():
    w = synth.trim(_wave(0.45, 2.0, 0.5))
    assert abs(len(w) / SR - (2.0 + synth.TRIM_LEAD_S + synth.TRIM_TAIL_S)) < 0.02
    assert np.abs(w).max() > 0.29                      # nothing of the speech is lost


def test_a_quiet_ending_above_the_floor_is_kept():
    w = _wave(0.4, 1.0, 0.4)
    k = int(1.4 * SR)
    w[k - int(0.2 * SR):k] *= 10 ** (-30 / 20)         # a last consonant 30 dB down: still speech
    assert abs(len(synth.trim(w)) / SR - (1.0 + synth.TRIM_LEAD_S + synth.TRIM_TAIL_S)) < 0.02


def test_silence_and_tiny_waves_are_left_alone():
    z = np.zeros(SR, np.float32)
    assert len(synth.trim(z)) == SR
    assert len(synth.trim(np.ones(10, np.float32))) == 10
