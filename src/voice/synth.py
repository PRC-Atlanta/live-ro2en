"""Supertonic 3 on the CPU: one loaded model, one voice per language, a float32 mono wave per call.
Set up as in the listening tests (work/tts_bakeoff/src/live_sim/st_worker_speed.py): 8 steps, the
preset voice, the speaking speed per sentence. Never downloads: the files must already be there."""
from __future__ import annotations

import os

import numpy as np

from . import model

SR = 44100
TARGET_RMS_DB = -20.0          # every item at the same speech level (plan §4.1)
PEAK_DB = -1.0
EDGE_FADE_S = 0.010
MAX_GAIN = 10.0               # +20 dB at most
WARMUP = {"ro": "Bună ziua.", "en": "Good morning."}


def level(wav: np.ndarray, rms_db: float = TARGET_RMS_DB, peak_db: float = PEAK_DB, sr: int = SR) -> np.ndarray:
    """The wave at `rms_db` (measured over its loud parts), peaks held under `peak_db`, 10 ms fades."""
    wav = np.nan_to_num(np.array(wav, dtype=np.float32, copy=True).reshape(-1), nan=0.0, posinf=0.0, neginf=0.0)
    if not len(wav):
        return wav
    win = max(1, int(0.05 * sr))
    n = len(wav) // win
    if n:
        frames = np.sqrt((wav[:n * win].reshape(n, win) ** 2).mean(1))
        loud = frames[frames > frames.max() * 0.1] if frames.max() > 0 else frames
        rms = float(np.sqrt((loud ** 2).mean())) if len(loud) else 0.0
    else:
        rms = float(np.sqrt((wav ** 2).mean()))
    if rms > 1e-6:                                  # near-silence is not raised into noise
        wav = wav * min(10 ** (rms_db / 20) / rms, MAX_GAIN)
    peak = float(np.abs(wav).max())
    top = 10 ** (peak_db / 20)
    if peak > top:
        wav = wav * (top / peak)
    k = min(int(EDGE_FADE_S * sr), len(wav) // 2)
    if k:
        ramp = np.linspace(0.0, 1.0, k, dtype=np.float32)
        wav[:k] *= ramp
        wav[-k:] *= ramp[::-1]
    return wav.astype(np.float32)


class Synth:
    def __init__(self, threads: int = 4, voices: dict | None = None, steps: int = 8, model_dir=None):
        os.environ.setdefault("SUPERTONIC_INTER_OP_THREADS", "1")
        from supertonic import TTS
        self.voices = dict(voices or {"ro": "M2", "en": "M2"})
        d = model_dir or model.model_dir(tuple(set(self.voices.values())))
        if d is None:
            raise FileNotFoundError("the voice model (Supertonic 3) is not downloaded")
        bad = model.verify(d)
        if bad:                                     # not the pinned files (requirements/models.lock.txt)
            raise RuntimeError(f"the voice model in {d} is not the pinned one: {'; '.join(bad[:3])}")
        self.model_dir = d
        self.tts = TTS(model="supertonic-3", model_dir=str(d), auto_download=False,
                       intra_op_num_threads=int(threads), inter_op_num_threads=1)
        if int(self.tts.sample_rate) != SR:
            raise RuntimeError(f"unexpected sample rate {self.tts.sample_rate}")
        self.steps = int(steps)
        self.styles = {v: self.tts.get_voice_style(voice_name=v) for v in set(self.voices.values())}

    def say(self, text: str, lang: str = "ro", speed: float = 1.25) -> np.ndarray:
        """`text` spoken in `lang`, levelled (float32, 44.1 kHz mono). The text must be speakable already."""
        wav, _ = self.tts.synthesize(text, voice_style=self.styles[self.voices[lang]], lang=lang,
                                     total_steps=self.steps, speed=float(speed))
        return level(np.asarray(wav, dtype=np.float32).reshape(-1))

    def warm_up(self, langs=("ro", "en")):
        for lang in langs:
            self.say(WARMUP[lang], lang)
