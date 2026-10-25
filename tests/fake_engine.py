"""engine.py without models, for tests/test_lifecycle.py: the real command loop, hold and
listen, level meter, file source and exit watch run; only the models are fake (the voice
detector hears any loud frame, recognition returns one fixed sentence).

  FAKE_MIC=1      replace the sound input with speech bursts at real time (honours `device`)
  FAKE_LOAD_S=n   how long "loading the models" takes (default 0.3)"""
import os
import pathlib
import sys
import threading
import time

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src" / "live"))
import engine as E                      # noqa: E402
from segmenter import PauseTracker      # noqa: E402

SENT = "Frați și surori, astăzi citim din epistola către Romani."


class FakeEngine(E.Engine):
    def __init__(self, stt, mode, cfg):     # noqa: D401  (no models: Engine.__init__ is skipped)
        E.loading("Loading speech recognition", 5)
        time.sleep(float(os.environ.get("FAKE_LOAD_S", "0.3")))
        self.mode, self.multi = mode, object()    # a stand-in Parakeet: English modes are accepted
        self.pt = PauseTracker(frame_ms=32, min_pause_ms=int(cfg.get("min_pause_ms", 350)), max_clause_s=7.0)
        self.pre_roll, self.partial_every, self.min_commit, self.max_commit = 8, 0, 0, 0
        self.no_audio_s = float(cfg.get("audio_watchdog_s", 2.0))
        self.silent_s = float(cfg.get("digital_silence_s", 10.0))
        self.realtime, self.backlog_s, self.catchup_max = True, 0, 0
        self.n, self._id_lock = 0, threading.Lock()
        self._held_t = None
        self.vad = lambda fr: 1.0 if float(np.abs(fr).max()) > 0.1 else 0.0
        E.loading("Ready", 100)

    def transcribe(self, audio, t_start, t_end, t_speech=None, final=True):
        E.emit({"type": "clause", "id": self._next_id(), "ro": SENT, "multi": "",
                "t_start": t_start, "t_speech": t_speech or t_start, "t_end": t_end, "t_emit": time.time(),
                "stt_s": 0.01, "audio_s": round(len(audio) / E.SR, 3)})


def fake_mic(device, q, stop, stall_s=2.0, retry_s=(1,), ctl=None):
    """0.6 s of 'speech', 0.6 s of quiet, paced in real time."""
    name = lambda d: "Fake Default Mic" if d is None else str(d)     # noqa: E731
    E.emit({"type": "input", "device": name(device)})
    i, t0 = 0, time.time()
    while not stop.is_set():
        if ctl is not None and ctl.changed.is_set():
            ctl.changed.clear()
            E.emit({"type": "input", "device": name(ctl.device)})
        loud = (i // 19) % 2 == 0
        fr = (np.random.default_rng(i).uniform(-0.5, 0.5, E.FRAME) if loud
              else np.full(E.FRAME, 0.001)).astype(np.float32)
        d = t0 + (i + 1) * E.FRAME / E.SR - time.time()
        if d > 0:
            time.sleep(d)
        now = time.time()
        if ctl is not None and ctl.meter is not None:
            ctl.meter.feed(fr, now)
        q.put((now, fr))
        i += 1


if __name__ == "__main__":
    if os.environ.get("FAKE_MIC"):
        E.mic_source = fake_mic
    E.main(make_engine=FakeEngine)
