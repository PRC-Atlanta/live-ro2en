"""The real voice process (src/voice/voice.py) with a synth that needs no model: a tone whose length
follows the words and the speed, generated in `FAKE_GEN_S` seconds per word (default 0.01).
Run with .venv-voice (numpy, lameenc):  .venv-voice/bin/python tests/fake_voice_synth.py --cfg '{...}'"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import numpy as np  # noqa: E402

from voice import voice  # noqa: E402

SEC_PER_WORD = float(os.environ.get("FAKE_AUDIO_S", "0.3"))       # at speed 1.0
GEN_PER_WORD = float(os.environ.get("FAKE_GEN_S", "0.01"))


class Synth:
    said = []

    def say(self, text, lang="ro", speed=1.25):
        n = max(1, len(text.split()))
        time.sleep(GEN_PER_WORD * n)
        if "CRASH" in text:
            os._exit(9)
        if "RAISE" in text:
            raise RuntimeError("synth failed on purpose")
        t = np.arange(int(44100 * SEC_PER_WORD * n / speed)) / 44100
        return (0.3 * np.sin(2 * np.pi * (440 if lang == "ro" else 660) * t)).astype(np.float32)

    def warm_up(self, langs=("ro", "en")):
        pass


if __name__ == "__main__":
    sys.exit(voice.main(make_synth=Synth))
