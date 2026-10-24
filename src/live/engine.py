"""ASR engine process: audio in -> VAD -> clause commits -> STT. Speaks JSON lines.

  <stt venv>/bin/python src/live/engine.py --stt jackrabbit-tdt-boost [--device NAME|INDEX]
                                           [--file audio.wav --realtime] [--hold]
stdout events (one JSON per line; docs/app_contract.md has the full list):
  {"type":"loading","label":..,"pct":..}                         while the models load
  {"type":"ready", ..., "hold": bool}
  {"type":"level","rms_db":..,"peak_db":..,"speech":..,"gain_db":..,"auto":..}  ~10 Hz, the sound meter
  {"type":"input","device":name}                                 each time the mic is opened
  {"type":"listen","on":bool}                                    hold <-> live acknowledged
  {"type":"partial","text":...}                                  (UI only)
  {"type":"clause","id":n,"ro":..,"multi":..,"t_start":..,"t_end":..,"t_emit":..,"stt_s":..}
  {"type":"eof"} when a --file source ends
stdin commands: mode, stop, listen {on}, cfg {cfg}, keywords {words, ro}, device {device}, gain {db},
  agc {on, target_db}: automatic input gain (AutoGain) on/off and its target speech level.
--hold: load and warm up, open the input for the level meter only, and transcribe nothing
until {"cmd":"listen","on":true} (a --file source waits to play until then).
The engine exits on stdin EOF and when its parent dies, so it never outlives the app.
t_end = wall-clock time of the last voiced frame of the clause (the latency origin).
Never imports google_stt."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import sys
from collections import OrderedDict, deque
import threading
import time

import numpy as np

# Fully offline at runtime: no model hub lookups, no telemetry. Models are fetched once by
# setup.sh. Set LIVE_ALLOW_NETWORK=1 only for setup/updates.
if os.environ.get("LIVE_ALLOW_NETWORK") != "1":
    for _k, _v in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("HF_DATASETS_OFFLINE", "1"),
                   ("NEEDLE_TELEMETRY", "0"), ("DO_NOT_TRACK", "1")):
        os.environ[_k] = _v
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import netaudit  # noqa: E402
netaudit.install()                 # no-op unless NET_AUDIT_LOG is set (offline tests)

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from langid import score as lang_score      # noqa: E402
from segmenter import PauseTracker          # noqa: E402
from vad import FRAME, SileroVAD            # noqa: E402

SR = 16000
LEVEL_FLOOR_DB = -120.0                     # "minus infinity" for the meter (JSON has no -inf)
GAIN_MIN_DB, GAIN_MAX_DB = -12.0, 30.0      # the input gain fader's range
AGC_TARGET_DB = -20.0                       # the middle of the meter's good band (-30..-10 dBFS)
NAME_BOOST = 1.0                            # weight of today's names in the Romanian beam search (0 = off)
_OUT = sys.stdout


def protect_stdout() -> None:
    """Events go to a private copy of stdout; fd 1 is pointed at stderr so library prints
    (NeMo, progress bars) cannot corrupt the JSON-lines protocol."""
    global _OUT
    _OUT = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr


_EMIT_LOCK = threading.Lock()


def emit(obj) -> None:
    with _EMIT_LOCK:                       # the Whistle stream thread emits too
        _OUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
    _OUT.flush()


# --- STT backends (in-memory audio) -------------------------------------------------------

class NemoSTT:
    """NeMo hybrid TDT/CTC model called directly: preprocessor + encoder on the Apple GPU
    (MPS), decoding on CPU. Output matches model.transcribe() on CPU (verified on the Bible
    segments) at ~15x lower latency; DECISIONS.md has the details."""
    def __init__(self, name: str):
        import torch
        import stt_run
        self.torch = torch
        self.m = stt_run.MODELS[name]().model
        self.dev = "mps" if torch.backends.mps.is_available() else "cpu"
        self.m.preprocessor.to(self.dev)
        self.m.encoder.to(self.dev)
        self.ctc = getattr(self.m, "cur_decoder", "rnnt") == "ctc"
        self.lock = threading.Lock()                     # a names change swaps the beam search's models
        self.names: list[str] = []
        self._fusion = None                              # the beam search's own models (the 4-gram LM)
        self(np.zeros(SR, dtype=np.float32))            # warm-up (MPS kernels compile)

    def set_names(self, names: list[str], alpha: float = NAME_BOOST) -> bool:
        """Today's names boosted in the CTC beam search (idea 2): NeMo's phrase-boosting tree
        (GPU-PB) fused next to the 4-gram LM, rebuilt when the names change (a few ms for a
        few dozen names). False where the decoder has no fusion (greedy, TDT)."""
        sa = getattr(getattr(getattr(self.m, "ctc_decoding", None), "decoding", None), "search_algorithm", None)
        if not self.ctc or not hasattr(sa, "fusion_models"):
            return False
        names = list(dict.fromkeys(n.strip() for n in names or [] if n and n.strip()))
        if names == self.names:
            return True
        tree = None
        if names and alpha > 0:
            from nemo.collections.asr.parts.context_biasing.boosting_graph_batched import (
                BoostingTreeModelConfig, GPUBoostingTreeModel)
            tree = GPUBoostingTreeModel.from_config(
                BoostingTreeModelConfig(key_phrases_list=names, use_triton=False, context_score=1.0,
                                        depth_scaling=2.0), tokenizer=self.m.tokenizer)
        with self.lock:
            if self._fusion is None:
                self._fusion = (list(sa.fusion_models or []), list(sa.fusion_models_alpha or []))
            models, alphas = self._fusion
            if tree is not None:
                models, alphas = models + [tree], alphas + [alpha]
            sa.fusion_models, sa.fusion_models_alpha = (models or None), (alphas or None)
            self.names = names
        return True

    def __call__(self, audio: np.ndarray) -> str:
        torch, m = self.torch, self.m
        with self.lock, torch.inference_mode():
            sig = torch.from_numpy(np.ascontiguousarray(audio, dtype=np.float32)).unsqueeze(0).to(self.dev)
            ln = torch.tensor([len(audio)], device=self.dev)
            p, pl = m.preprocessor(input_signal=sig, length=ln)
            e, el = m.encoder(audio_signal=p, length=pl)
            e, el = e.float().cpu(), el.cpu()
            if self.ctc:
                lp = m.ctc_decoder(encoder_output=e)
                h = m.ctc_decoding.ctc_decoder_predictions_tensor(lp, decoder_lengths=el, return_hypotheses=False)
            else:
                h = m.decoding.rnnt_decoder_predictions_tensor(encoder_output=e, encoded_lengths=el)
            h = h[0] if isinstance(h, tuple) else h
            h = h[0]
            return h if isinstance(h, str) else h.text


class ParakeetSTT:
    """Multilingual (RO+EN) model used for language detection and English speech."""
    def __init__(self, repo: str = "mlx-community/parakeet-tdt-0.6b-v3"):
        from parakeet_mlx import from_pretrained
        self.m = from_pretrained(repo)

    def __call__(self, audio: np.ndarray) -> str:
        # what transcribe(path) does, minus its ffmpeg decode: the app ships without ffmpeg
        import mlx.core as mx
        from parakeet_mlx.audio import get_logmel
        cfg = self.m.preprocessor_config
        assert cfg.sample_rate == SR, cfg.sample_rate
        if len(audio) < cfg.hop_length:
            return ""
        x = mx.array(np.clip(audio, -1.0, 1.0).astype(np.float32))    # float32, as load_audio gives
        return self.m.generate(get_logmel(x, cfg))[0].text.strip()


def make_stt(name: str):
    return ParakeetSTT() if name == "parakeet-mlx" else NemoSTT(name)


# --- audio sources ------------------------------------------------------------------------

def to_db(x: float) -> float:
    return max(LEVEL_FLOOR_DB, round(20.0 * float(np.log10(x)), 1)) if x > 0 else LEVEL_FLOOR_DB


class LevelMeter:
    """Input level for the sound meter: rms and peak over each 1/hz window, sent as `level`
    events from the capture side, so the meter keeps moving while a recognition call runs.
    `speech` is the voice detector's verdict, set by the processing loop (`voice`)."""

    def __init__(self, hz: float = 10.0):
        self.every = 1.0 / min(20.0, max(5.0, hz))
        self._lock = threading.Lock()
        self._sq, self._n, self._peak, self._t = 0.0, 0, 0.0, None
        self._voiced = self._last = False
        self.gain_db, self.auto = 0.0, False       # set by SourceCtl: the knob follows the AGC

    def voice(self, on: bool) -> None:
        self._last = on
        self._voiced |= on

    def feed(self, fr: np.ndarray, now: float | None = None) -> None:
        now = time.time() if now is None else now
        x = np.asarray(fr, dtype=np.float32)
        ev = None
        with self._lock:
            if self._t is None:
                self._t = now
            self._sq += float(np.dot(x, x))
            self._n += len(x)
            self._peak = max(self._peak, float(np.abs(x).max()) if len(x) else 0.0)
            if now - self._t >= self.every:
                rms = (self._sq / self._n) ** 0.5 if self._n else 0.0
                ev = {"rms_db": to_db(rms), "peak_db": to_db(self._peak), "speech": self._voiced or self._last,
                      "gain_db": round(self.gain_db, 1), "auto": self.auto}
                self._sq, self._n, self._peak, self._t, self._voiced = 0.0, 0, 0.0, now, False
        if ev:
            emit({"type": "level", **ev})


class AutoGain:
    """Automatic input gain (idea 19): one fixed gain cannot suit a quiet reader (-41 dBFS)
    and a duet near clipping (-5 dBFS), so the gain follows the speech level.

    The level it steers by is the *raw* input (before the gain), so moving the gain never
    moves its own measure: the capture thread notes each frame's raw rms (`raw`), the
    processing loop says which frames the voice detector called speech (`step`). Only speech
    frames count and only they advance the clock, so the gain freezes while nobody speaks
    (no pumping up the room noise between speakers). Clock = the frames' own timestamps, so a
    fast bench replay behaves as live and tests pass any times they like.
    Not thread-safe by itself: SourceCtl holds its lock around every call."""

    def __init__(self, target_db: float = AGC_TARGET_DB, window_s: float = 20.0, rate_db_s: float = 1.0,
                 min_speech_s: float = 2.0, deadband_db: float = 1.0, limit_db: float = -1.0,
                 limit_hold_s: float = 10.0, floor_db: float = -80.0):
        self.on = False
        self.target_db = target_db
        self.window_s = window_s                 # running median over this much speech time
        self.rate = rate_db_s                    # the slow part: at most this many dB per second of speech
        self.min_n = int(min_speech_s * SR / FRAME)   # no verdict on less speech than this
        self.deadband = deadband_db              # closer than this to the target: leave it
        self.limit = 10.0 ** (limit_db / 20.0)   # the fast part: a peak past this cuts at once
        self.limit_hold_s = limit_hold_s         # and the gain may not creep back up for a while
        self.floor_db = floor_db                 # digital silence called speech: ignored
        self._raw: OrderedDict = OrderedDict()   # ts -> raw rms dB, waiting for the VAD's verdict
        self._win: deque = deque()               # (ts, raw dB) of recent speech frames
        self._last_t = None
        self._hold_until = float("-inf")

    def raw(self, ts: float, fr: np.ndarray) -> None:
        """Capture thread: the raw frame's level, kept until the loop reaches that frame."""
        x = np.asarray(fr, dtype=np.float32)
        self._raw[ts] = to_db(float(np.sqrt(np.dot(x, x) / len(x)))) if len(x) else LEVEL_FLOOR_DB
        while len(self._raw) > 4000:             # ~2 min of frames: a long backlog just loses them
            self._raw.popitem(last=False)

    def median(self) -> float | None:
        if len(self._win) < self.min_n:
            return None
        return float(np.median([d for _, d in self._win]))

    def step(self, ts: float, speech: bool, gain_db: float) -> float | None:
        """Processing loop, once per frame: the new gain in dB, or None to keep it."""
        db = self._raw.pop(ts, None)
        while self._raw and next(iter(self._raw)) < ts:      # frames the loop never asked about
            self._raw.popitem(last=False)
        if not speech or db is None or db <= self.floor_db:
            return None
        self._win.append((ts, db))
        while self._win and self._win[0][0] < ts - self.window_s:
            self._win.popleft()
        dt = 0.0 if self._last_t is None else min(max(0.0, ts - self._last_t), 2 * FRAME / SR)
        self._last_t = ts                        # silence in between does not count as time
        med = self.median()
        if not self.on or med is None:
            return None
        want = min(GAIN_MAX_DB, max(GAIN_MIN_DB, self.target_db - med))
        err = want - gain_db
        if abs(err) <= self.deadband or (err > 0 and ts < self._hold_until):
            return None
        return gain_db + max(-self.rate * dt, min(self.rate * dt, err))

    def limited(self, ts: float | None, raw_peak: float, gain_db: float) -> float | None:
        """Capture thread, before the frame is gained: a lower gain in dB when this frame would
        peak past the limit (applied to this very frame, so clipping never reaches the
        recogniser), else None."""
        if not self.on or raw_peak <= 0 or raw_peak * 10.0 ** (gain_db / 20.0) <= self.limit:
            return None
        if ts is not None:
            self._hold_until = ts + self.limit_hold_s
        return max(GAIN_MIN_DB, 20.0 * float(np.log10(self.limit / raw_peak)))


class SourceCtl:
    """What the capture thread should do: the input device to use (switched live with the
    `device` command, e.g. while the Setup wizard tries inputs), when a file in hold may
    start playing (`go`), the input gain (the `gain` command, the operator's fader) and the
    level meter it feeds."""

    def __init__(self, device=None, meter: LevelMeter | None = None, hold: bool = False,
                 gain_db: float = 0.0, auto: bool = False, target_db: float = AGC_TARGET_DB):
        self.device = device
        self.meter = meter
        self._lock = threading.Lock()            # the gain moves from three threads (stdin, capture, loop)
        self.agc = AutoGain()
        self.gain_db, self.gain = 0.0, 1.0
        self.set_gain(gain_db)
        self.set_auto(auto, target_db)
        self.changed = threading.Event()
        self.go = threading.Event()
        if not hold:
            self.go.set()

    def switch(self, device) -> None:
        self.device = device
        self.changed.set()

    def _put(self, db: float) -> None:
        """Under the lock: the new gain, also told to the meter (the knob follows it)."""
        self.gain_db = min(GAIN_MAX_DB, max(GAIN_MIN_DB, float(db)))
        self.gain = 10.0 ** (self.gain_db / 20.0)
        if self.meter is not None:
            self.meter.gain_db = self.gain_db

    def set_gain(self, db) -> None:
        try:
            db = float(db)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._put(db)

    def set_auto(self, on, target_db=None) -> None:
        """`agc` command: automatic gain on/off (off keeps the gain it reached: no jump) and
        its target speech level, clamped to the meter's good band."""
        with self._lock:
            self.agc.on = bool(on)
            try:
                if target_db is not None:
                    self.agc.target_db = min(-10.0, max(-30.0, float(target_db)))
            except (TypeError, ValueError):
                pass
            if self.meter is not None:
                self.meter.auto = self.agc.on

    def voiced(self, ts: float, speech: bool) -> None:
        """From the processing loop, per frame: the voice detector's verdict on the frame
        captured at ts. The controller may move the gain; the capture thread applies it from
        its next frame."""
        with self._lock:
            db = self.agc.step(ts, speech, self.gain_db)
            if db is not None:
                self._put(db)

    def amp(self, fr: np.ndarray, ts: float | None = None) -> np.ndarray:
        """The frame with the input gain applied (clipped to full scale, as an interface would).
        The meter, the voice detector and the recogniser all hear the gained signal. With a
        timestamp, the raw level is noted for the automatic gain, and its limiter may lower
        the gain for this very frame."""
        with self._lock:
            if ts is not None:
                self.agc.raw(ts, fr)
            if self.agc.on and len(fr):
                db = self.agc.limited(ts, float(np.abs(fr).max()), self.gain_db)
                if db is not None:
                    self._put(db)
            g = self.gain
        return fr if g == 1.0 else np.clip(fr * g, -1.0, 1.0).astype(np.float32)


def resample(x: np.ndarray, sr: int) -> np.ndarray:
    """To 16 kHz with soxr (a small wheel: no ffmpeg needed). Linear interpolation is the
    fallback if soxr is missing, so a rehearsal still plays."""
    if sr == SR:
        return x
    try:
        import soxr
        return soxr.resample(x, sr, SR).astype(np.float32)
    except ImportError:
        n = int(round(len(x) * SR / sr))
        return np.interp(np.arange(n) * (sr / SR), np.arange(len(x)), x).astype(np.float32)


def read_audio(path: str) -> np.ndarray:
    """Mono float32 at 16 kHz from any file libsndfile reads (wav, flac, mp3, aiff, ogg)."""
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    return resample(np.ascontiguousarray(x.mean(1), dtype=np.float32), int(sr))


def bench_clock() -> tuple[float, float] | None:
    """(speed, t0) for a test-bench replay (scripts/bench.py), else None. LIVE_FILE_SPEED plays
    a file that many times faster than real time; frames are stamped on the *audio* clock
    LIVE_FILE_T0 + position, so the run's phrase times line up with the recorded service's
    log. Everything that counts frames (pauses, phrase length) behaves as live."""
    try:
        speed = float(os.environ.get("LIVE_FILE_SPEED") or 0)
        return (speed, float(os.environ.get("LIVE_FILE_T0") or time.time())) if speed > 0 else None
    except ValueError:
        return None


def file_source(path: str, realtime: bool, q: queue.Queue, stop: threading.Event,
                ctl: SourceCtl | None = None) -> None:
    x = read_audio(path)
    bench = bench_clock()
    if ctl is not None:                          # hold: the file plays from Start, not before
        while not ctl.go.wait(0.1):
            if stop.is_set():
                q.put(None)
                return
    meter = ctl.meter if ctl is not None else None
    t0 = time.time()
    speed = bench[0] if bench else 1.0
    for i in range(0, len(x) - FRAME + 1, FRAME):
        if stop.is_set():
            break
        if realtime or bench:
            due = t0 + (i + FRAME) / SR / speed
            d = due - time.time()
            if d > 0:
                time.sleep(d)
        fr = x[i:i + FRAME]
        ts = bench[1] + (i + FRAME) / SR if bench else time.time()
        if ctl is not None:
            fr = ctl.amp(fr, ts)
        if meter is not None:
            meter.feed(fr)
        q.put((ts, fr))
    q.put(None)


def _device_name(sd, dev) -> str | None:
    """The name of the input actually opened (the system default when dev is None)."""
    try:
        if dev is None:
            d = sd.default.device
            dev = d[0] if isinstance(d, (list, tuple)) else d
        return sd.query_devices(dev)["name"]
    except Exception:                            # noqa: BLE001  a fake or odd backend: unnamed
        return None


def _mic_denied() -> bool:
    try:
        import permissions
        return permissions.microphone() in ("denied", "restricted")
    except Exception:                            # noqa: BLE001  unknown: let the audio speak
        return False


def _reopen_device(sd, dev, name: str | None):
    """PortAudio lists devices once, at initialisation: a re-plugged interface is only seen
    after a re-init, and its index may have changed. Find it again by name (or keep the
    system default when none was chosen)."""
    try:
        sd._terminate()
        sd._initialize()
    except Exception:                            # noqa: BLE001  best effort; the open will tell
        pass
    if name is None:
        return dev
    for i, d in enumerate(sd.query_devices()):
        if d.get("name") == name and d.get("max_input_channels", 0) > 0:
            return i
    return dev


def mic_source(device, q: queue.Queue, stop: threading.Event, stall_s: float = 2.0,
               retry_s: tuple[float, ...] = (1, 2, 5), ctl: SourceCtl | None = None) -> None:
    """Capture until stop. A device that fails to open, errors, or stops delivering audio
    (USB glitch, unplugged and replugged) is reported and reopened with backoff, so the
    service recovers on its own instead of waiting for the operator to restart it.
    `ctl.switch(device)` moves to another input without restarting the engine."""
    import sounddevice as sd
    last = {"t": time.time()}
    meter = ctl.meter if ctl is not None else None

    def cb(indata, frames, t, status):
        last["t"] = time.time()
        fr = indata[:, 0].copy()
        if ctl is not None:
            fr = ctl.amp(fr, last["t"])
        if meter is not None:
            meter.feed(fr, last["t"])
        q.put((last["t"], fr))

    def pick(d):
        return int(d) if isinstance(d, str) and d.isdigit() else d
    dev = pick(device)
    name = None                                  # the device's name, to find it again after a replug
    failures = 0
    while not stop.is_set():
        if ctl is not None and ctl.changed.is_set():     # the operator chose another input
            ctl.changed.clear()
            dev, name, failures = pick(ctl.device), None, 0
        try:
            if failures:
                dev = _reopen_device(sd, dev, name)
            elif dev is not None:
                name = sd.query_devices(dev)["name"]
            if _mic_denied():                    # macOS gives a denied app silence, not an error
                q.put({"fault": "mic_denied", "msg": "the Mac does not allow this app to use the microphone"})
            with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
                                device=dev, callback=cb):
                last["t"] = time.time()
                emit({"type": "input", "device": name or _device_name(sd, dev)})
                if failures:
                    emit({"type": "warn", "msg": "audio input reopened"})
                failures = 0
                while not stop.is_set():
                    time.sleep(0.1)
                    if ctl is not None and ctl.changed.is_set():
                        break
                    if time.time() - last["t"] > stall_s:
                        raise RuntimeError(f"no audio callbacks for {stall_s:.0f} s")
        except Exception as e:                   # wrong index, device busy, unplugged mid-run
            emit({"type": "error", "where": "audio input", "msg": f"{type(e).__name__}: {e} (reopening)"})
            end = time.time() + retry_s[min(failures, len(retry_s) - 1)]
            while time.time() < end and not stop.is_set() and not (ctl is not None and ctl.changed.is_set()):
                time.sleep(0.02)
            failures += 1


# --- main loop ----------------------------------------------------------------------------

def loading(label: str, pct: float) -> None:
    """Model-loading progress for the launch screen (`progress` in the app state)."""
    emit({"type": "loading", "label": label, "pct": float(pct)})


class Engine:
    meter: LevelMeter | None = None                      # set by main(); tests run without one
    ctl: SourceCtl | None = None                         # set by main(): the automatic gain hears the VAD

    def __init__(self, stt_name: str, mode: str, cfg: dict):
        self.mode = mode
        loading("Loading speech recognition", 5)
        self.stt = make_stt(stt_name)
        self.name_boost = float(cfg.get("ro_name_boost", NAME_BOOST))
        self.set_ro_names(cfg.get("ro_names") or [])
        self.multi = None
        if stt_name != "parakeet-mlx":
            loading("Loading language detection", 45)
            try:
                self.multi = ParakeetSTT()
            except Exception as e:                      # language auto-detect degrades to RO only
                emit({"type": "warn", "msg": f"multilingual model unavailable: {e}"})
        else:
            self.multi = self.stt
        loading("Loading the voice detector", 70)
        self.vad = SileroVAD()
        self.pt = PauseTracker(frame_ms=32, threshold=cfg.get("vad_threshold", 0.5),
                               min_pause_ms=cfg.get("min_pause_ms", 350),
                               max_clause_s=cfg.get("max_clause_s", 7.0))
        self.pre_roll = 8                                # frames (256 ms) kept before speech
        self.partial_every = cfg.get("partial_every_s", 1.0)
        # quality profile: keep accumulating across short pauses until min_commit_s of audio,
        # so STT and the translator see whole sentences; never hold more than max_commit_s
        self.min_commit = int(cfg.get("min_commit_s", 0) * SR / FRAME)
        self.max_commit = int(cfg.get("max_commit_s", 0) * SR / FRAME)
        self.n = 0
        self._id_lock = threading.Lock()                 # the stream thread numbers clauses too
        # audio watchdog: no frames at all (device gone) or exact digital silence (muted/dead
        # interface) for this long is reported; a real quiet room still has a noise floor
        self.no_audio_s = float(cfg.get("audio_watchdog_s", 2.0))
        self.silent_s = float(cfg.get("digital_silence_s", 10.0))
        # speech-recognition backlog (live input only): when frames are dequeued more than
        # backlog_s after capture, short pauses are skipped while queued audio remains, so the
        # backlog is recognised in fewer, longer calls (up to catchup_max_s of audio each)
        self.realtime = True
        self.backlog_s = float(cfg.get("stt_backlog_s", 3.0))
        self.catchup_max = int(cfg.get("catchup_max_s", 20.0) * SR / FRAME)
        # English recognizer: Whistle (streaming, offline) or the previous Parakeet path (A/B)
        self.whistle = None
        self.wcfg = cfg.get("whistle") or {}
        self.en_rec = cfg.get("en_recognizer", "parakeet")
        if self.en_rec == "whistle":
            loading("Loading English recognition", 80)
            from whistle_en import WhistleSTT
            self.whistle = WhistleSTT(self.wcfg)
        self.streamer = self.whistle                     # English streaming recognizer (EN→RO locked)
        # short live chunks are transcribed with the previous context_s seconds of audio
        # (RO 11.3 -> 6.9 %, EN 10.3 -> 2.4 % WER vs Google; see DECISIONS.md)
        from context_stt import ContextSTT
        ctx = float(cfg.get("context_s", 6.0))
        hold = int(cfg.get("lookahead_words", 1))
        self.stt_ctx = ContextSTT(self.stt, ctx, hold_words=hold)
        self.multi_ctx = ContextSTT(self.multi, ctx, hold_words=hold) if self.multi is not None and self.multi is not self.stt else None
        self._held_t = None                              # when the held word(s) were spoken
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(max_workers=1)
        if self.en_rec == "parakeet-stream":
            loading("Loading English recognition", 85)
            from parakeet_stream import ParakeetStreamSTT
            self.streamer = ParakeetStreamSTT(cfg.get("parakeet_stream") or {})
        self._wstream = None                             # (assembler, wall_t0, chunk buffer)
        # English needs a recognizer that hears it: auto needs one to detect the language
        self.english_ok = self.multi is not None or self.whistle is not None or self.streamer is not None
        if not self.english_ok and self.mode != "ro-en":
            self._refuse(self.mode)
            self.mode = "ro-en"
        loading("Ready", 100)

    # --- live changes (stdin commands, applied in order with the audio) -------------------
    def apply_cfg(self, cfg: dict) -> list[str]:
        """`{"cmd":"cfg"}`: pause and commit settings without a restart (a profile switch).
        Returns the keys that would need a restart instead (they are ignored here)."""
        ctxs = [c for c in (getattr(self, "stt_ctx", None), getattr(self, "multi_ctx", None)) if c is not None]
        later = []
        for k, v in (cfg or {}).items():
            try:
                if k == "vad_threshold":
                    self.pt.threshold = float(v)
                elif k == "min_pause_ms":
                    self.pt.min_pause_ms = int(v)
                elif k == "max_clause_s":
                    self.pt.max_clause_s = float(v)
                elif k == "partial_every_s":
                    self.partial_every = float(v)
                elif k == "min_commit_s":
                    self.min_commit = int(float(v) * SR / FRAME)
                elif k == "max_commit_s":
                    self.max_commit = int(float(v) * SR / FRAME)
                elif k == "audio_watchdog_s":
                    self.no_audio_s = float(v)
                elif k == "digital_silence_s":
                    self.silent_s = float(v)
                elif k == "stt_backlog_s":
                    self.backlog_s = float(v)
                elif k == "catchup_max_s":
                    self.catchup_max = int(float(v) * SR / FRAME)
                elif k == "lookahead_words":
                    for c in ctxs:
                        c.hold_words = int(v)
                elif k == "context_s":
                    for c in ctxs:
                        c.ctx_n, c.max_ctx_words = int(float(v) * SR), max(1, int(float(v) * 3))
                elif k == "en_recognizer" and v == getattr(self, "en_rec", v):
                    pass                                          # unchanged: nothing to do
                elif k in ("input_gain_db", "input_gain_auto", "input_gain_target_db"):
                    pass                  # the capture side's: the `gain`/`agc` commands set them live
                elif k == "ro_names":
                    self.set_ro_names(v if isinstance(v, list) else [])
                elif k in ("whistle", "parakeet_stream"):
                    pass                  # recognizer options: names come by `keywords`, the rest at the next start
                else:
                    later.append(k)
            except (TypeError, ValueError):
                later.append(k)
        return later

    def set_keywords(self, words: list[str], ro: list[str] | None = None) -> None:
        """`{"cmd":"keywords"}`: the user's and today's names for the English recognizer, and
        (`ro`) as Romanian speech has them, for the Romanian one."""
        w = getattr(self, "whistle", None)
        if w is not None and hasattr(w, "set_keywords"):
            w.set_keywords([str(x) for x in words or [] if str(x).strip()])
        if ro is not None:
            self.set_ro_names(ro)

    def set_ro_names(self, names: list[str]) -> None:
        stt = getattr(self, "stt", None)
        if stt is None or not hasattr(stt, "set_names"):
            return
        try:
            stt.set_names([str(x) for x in names or [] if str(x).strip()], self.name_boost)
        except Exception as e:                           # noqa: BLE001  recognition goes on unboosted
            emit({"type": "warn", "msg": f"names not boosted in Romanian recognition: {type(e).__name__}: {e}"})

    def _next_id(self) -> int:
        with self._id_lock:
            self.n += 1
            return self.n

    def _refuse(self, mode: str) -> None:
        emit({"type": "mode", "mode": "ro-en",
              "msg": f"{mode} needs the multilingual model, which is unavailable: running RO→EN only"})

    def transcribe(self, audio: np.ndarray, t_start: float, t_end: float, t_speech: float | None = None,
                   final: bool = True) -> None:
        """One failed chunk (model/MPS error) is reported as an error event; the engine keeps
        listening instead of dying mid-service."""
        try:
            self._transcribe(audio, t_start, t_end, t_speech, final)
        except Exception as e:                   # noqa: BLE001
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit({"type": "error", "where": "transcribe", "msg": f"{type(e).__name__}: {e}",
                  "t_start": t_start, "t_end": t_end, "audio_s": round(len(audio) / SR, 3)})

    def _transcribe(self, audio: np.ndarray, t_start: float, t_end: float, t_speech: float | None = None,
                    final: bool = True) -> None:
        t = time.time()
        en_locked_whistle = self.mode == "en-ro" and self.whistle is not None
        need_ro = self.mode != "en-ro" or (self.multi is None and not self.whistle)
        need_multi = self.multi is not None and self.mode != "ro-en" and not en_locked_whistle
        multi = ""
        if need_ro and need_multi and self.multi_ctx is not None:
            # Auto mode: Jackrabbit (torch, helper thread) and Parakeet (MLX, must stay on this
            # thread) run at the same time instead of one after the other
            fut = self._pool.submit(self.stt_ctx, audio, final)
            multi = self.multi_ctx(audio, final)
            ro = fut.result()
        else:
            ro = self.stt_ctx(audio, final) if need_ro else ""
            if need_multi:
                multi = ro if self.multi is self.stt else self.multi_ctx(audio, final)  # language detection / English
        # look-ahead: words held from the previous (mid-speech) cut appear now, so the delay
        # clock starts when they were spoken (~0.4 s per held word before that cut)
        t_first = t_speech or t_start
        if self._held_t is not None:
            t_first = min(t_first, self._held_t)
        held = getattr(self.stt_ctx if need_ro else (self.multi_ctx or self.stt_ctx), "held", 0)
        self._held_t = (t_end - 0.4 * held) if (held and not final) else None
        en = ""
        # auto: Whistle only when the clause is not clearly Romanian (it would be discarded)
        clearly_ro = self.mode == "auto" and bool(multi) and lang_score(multi) > 0.15
        if self.whistle is not None and self.mode != "ro-en" and not clearly_ro:
            en = self.whistle(audio)                                         # English text
        if self.mode == "en-ro" and not ro:
            ro = en or multi
        emit({"type": "clause", "id": self._next_id(), "ro": ro.strip(), "multi": (multi or en).strip(), "en": en.strip(),
              "t_start": t_start, "t_speech": t_first, "t_end": t_end, "t_emit": time.time(), "stt_s": round(time.time() - t, 4),
              "audio_s": round(len(audio) / SR, 3)})

    # --- Whistle streaming (EN→RO locked mode) -------------------------------------------
    def _whistle_streaming(self, ts: float, fr: np.ndarray, p: float) -> bool:
        """Feed audio to Whistle while EN→RO is locked. Non-speech frames (VAD below
        whistle.vad_threshold: music, choir, noise) are replaced by silence, which Whistle
        returns as empty text. Returns True when the frame was consumed by the stream."""
        want = self.streamer is not None and self.mode == "en-ro" and self.wcfg.get("stream", True) \
            and not getattr(self.streamer, "error", None)      # a failed stream: use the clause path
        if not want:
            self._stop_whistle_stream(wait=False)        # mode switch: never stall the audio loop
            return False
        if self._wstream is None:
            from whistle_en import ClauseAssembler, words_from_event
            wall_t0 = ts - FRAME / SR

            def on_clause(text, start, end, pass_ms):
                emit({"type": "clause", "id": self._next_id(), "ro": "", "multi": text, "en": text, "source": f"{self.en_rec}",
                      "t_start": wall_t0 + start, "t_speech": wall_t0 + start, "t_end": wall_t0 + end,
                      "t_emit": time.time(), "stt_s": round(pass_ms / 1000, 4), "audio_s": round(end - start, 3)})
            asm = ClauseAssembler(on_clause, pause_s=self.wcfg.get("pause_s", 0.35),
                                  max_chunk_s=self.wcfg.get("max_chunk_s", 2.5))

            def on_event(ev):
                if ev.get("error"):
                    emit({"type": "error", "where": "english stream", "msg": ev["error"]})
                if ev.get("end") or ev.get("error"):
                    asm.flush(); return
                asm.add(words_from_event(ev), ev.get("pass_ms", 0.0))
                asm.tick(float(ev.get("received", 0.0)), ev.get("pending", ""), ev.get("pass_ms", 0.0))
            self.streamer.start_stream(on_event)
            self._wstream = {"buf": [], "n": 0, "chunk": int(self.wcfg.get("chunk_s", 1.0) * SR)}
        st = self._wstream
        gate = self.wcfg.get("vad_threshold", 0.6)
        st["buf"].append(fr if p >= gate else np.zeros_like(fr))
        st["n"] += len(fr)
        if st["n"] >= st["chunk"]:
            self.streamer.feed(np.concatenate(st["buf"]))
            st["buf"], st["n"] = [], 0
        return True

    def _stop_whistle_stream(self, wait: bool = True) -> None:
        """End the English stream. Its tail is still recognised and emitted by the stream's own
        thread; only end of input waits for it (so eof comes after the last clause)."""
        if self._wstream is not None:
            if self._wstream["buf"]:
                self.streamer.feed(np.concatenate(self._wstream["buf"]))
            self.streamer.end_stream(**({} if wait else {"wait": 0}))
            self._wstream = None

    def run(self, q: queue.Queue, stop: threading.Event | None = None, hold: bool = False,
            idle_watch: bool = True) -> None:
        """Process queued audio until the None sentinel. Items are (time, frame) or a command
        dict ({"mode"}, {"listen"}, {"cfg"}, {"keywords"}, {"fault"}); commands travel through
        the queue so the speech buffered before one is committed under the old settings,
        never dropped or spliced onto later audio.
        hold: start in hold (level meter and watchdog only, nothing transcribed) until
        {"listen": True}. idle_watch=False: no "no frames" alarm while holding (a file that
        waits for Start delivers nothing yet)."""
        listening = not hold
        denied = False                                   # macOS refused the mic: silence is expected
        meter = getattr(self, "meter", None)
        ctl = getattr(self, "ctl", None)
        frames: list[np.ndarray] = []
        probs: list[float] = []
        times: list[float] = []
        pre: list[tuple[float, np.ndarray]] = []
        last_voiced = 0.0
        last_partial = 0.0

        def commit_all() -> None:
            nonlocal frames, probs, times
            if frames:
                first = next((t for t, pp in zip(times, probs) if pp >= self.pt.threshold), times[0])
                self.transcribe(np.concatenate(frames), times[0], last_voiced or times[-1], first)
            frames, probs, times = [], [], []
            self.pt.reset()

        last_frame = last_sound = time.time()
        behind = pause_pending = False
        alarm = None                                     # current audio fault, reported once

        def audio_state(kind: str | None, msg: str = "") -> None:
            nonlocal alarm
            if kind != alarm:                            # once per change of fault, not per tick
                alarm = kind
                emit({"type": "audio", "ok": kind is None, "fault": kind, "msg": msg or "audio input back"})

        while True:
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                if self.no_audio_s and (listening or idle_watch) and time.time() - last_frame > self.no_audio_s:
                    audio_state("no_frames", f"no audio from the input device for {time.time() - last_frame:.0f} s")
                continue
            if item is None:
                self._stop_whistle_stream()
                commit_all()
                emit({"type": "eof"})
                return
            if isinstance(item, dict):
                if "listen" in item:
                    on = bool(item["listen"])
                    if on and not listening:             # Start: the warm engine goes live
                        listening, pause_pending, last_partial = True, False, 0.0
                        last_frame = time.time()
                        self.pt.reset()
                        for c in (getattr(self, "stt_ctx", None), getattr(self, "multi_ctx", None)):
                            if c is not None:
                                c.reset()                # no context from before this service
                        self._held_t = None
                    elif not on and listening:           # Stop: commit what was said, then hold
                        self._stop_whistle_stream()
                        commit_all()
                        listening = False
                    emit({"type": "listen", "on": listening})   # after the last clause of the run
                    continue
                if "cfg" in item:
                    later = self.apply_cfg(item["cfg"])
                    if later:
                        emit({"type": "warn", "msg": f"applied at the next start: {', '.join(sorted(later))}"})
                    continue
                if "keywords" in item:
                    self.set_keywords(item["keywords"], item.get("ro"))
                    continue
                if "fault" in item:                      # from the capture thread
                    denied = item["fault"] == "mic_denied"
                    audio_state(item["fault"], item.get("msg", ""))
                    continue
                m = item.get("mode")
                if m and m != "ro-en" and not self.english_ok:
                    self._refuse(m)                      # stays RO→EN; the app is told why
                elif m and m != self.mode:
                    commit_all()
                    self.mode = m
                continue
            ts, fr = item
            last_frame = time.time()
            lag = last_frame - ts
            if self.realtime and self.backlog_s:
                if not behind and lag > self.backlog_s:
                    behind = True
                    emit({"type": "backlog", "ok": False, "s": round(lag, 1),
                          "msg": f"speech recognition is {lag:.0f} s behind the audio"})
                elif behind and lag < self.backlog_s / 3:
                    behind = False
                    emit({"type": "backlog", "ok": True, "s": round(lag, 1), "msg": "caught up"})
            if np.abs(fr).max() > 1e-6:
                last_sound = last_frame
                denied = False                           # real sound: the permission is there
            if denied:
                pass                                     # keep mic_denied, not "digital silence"
            elif self.silent_s and last_frame - last_sound > self.silent_s:
                audio_state("digital_silence",
                            f"input is digital silence for {last_frame - last_sound:.0f} s (muted or dead mic?)")
            else:
                audio_state(None)
            p = self.vad(fr)
            if meter is not None:
                meter.voice(p >= self.pt.threshold)
            if ctl is not None:
                ctl.voiced(ts, p >= self.pt.threshold)      # the automatic gain steers by speech only
            if not listening:                            # hold: meter only; keep the pre-roll
                pre = (pre + [(ts, fr)])[-self.pre_roll:]
                continue
            if self._whistle_streaming(ts, fr, p):
                continue
            if not frames and p < self.pt.threshold:
                pre = (pre + [(ts, fr)])[-self.pre_roll:]
                self.pt.push(p)
                continue
            if not frames:
                times = [t for t, _ in pre]
                frames = [f for _, f in pre]
                probs = [0.0] * len(pre)
                pre = []
            frames.append(fr); probs.append(p); times.append(ts)
            if p >= self.pt.threshold:
                last_voiced = ts
            ev = self.pt.push(p)
            if ev == "force" and behind and not q.empty() and len(frames) < self.catchup_max:
                ev = None                                 # catching up: bounded by catchup_max_s
            if self.max_commit and len(frames) >= self.max_commit:
                ev = "force"
            # keep accumulating across a pause: quality profile (min_commit) or a backlog
            # that is still queued (recognise it in one longer call instead of many short ones)
            want_more = len(frames) < self.min_commit or \
                (behind and not q.empty() and len(frames) < self.catchup_max)
            if ev == "pause" and want_more:
                pause_pending = True                      # remember it: the tracker won't repeat it
                continue
            if ev is None and pause_pending and not self.pt.in_speech and not want_more:
                ev = "pause"                              # the skipped pause, now long enough
            if ev:
                pause_pending = False
            first_voiced = next((t for t, pp in zip(times, probs) if pp >= self.pt.threshold), times[0])
            if ev == "pause":
                # trim trailing silence, keep 100 ms
                k = max((i for i, pp in enumerate(probs) if pp >= self.pt.threshold), default=len(probs) - 5) + 4
                self.transcribe(np.concatenate(frames[:k]), times[0], last_voiced, first_voiced)
                frames, probs, times = [], [], []
            elif ev == "force":
                # cut at the least-voiced frame in the last 2.5 s (never mid-word if avoidable)
                lo = max(1, len(probs) - int(2.5 * SR / FRAME))
                hi = max(lo + 1, len(probs) - int(0.3 * SR / FRAME))
                cut = lo + int(np.argmin(probs[lo:hi]))
                self.transcribe(np.concatenate(frames[:cut]), times[0], times[cut - 1], first_voiced, final=False)
                frames, probs, times = frames[cut:], probs[cut:], times[cut:]
            elif self.partial_every and not behind and ts - last_partial > self.partial_every and len(frames) > 30:
                last_partial = ts
                try:
                    emit({"type": "partial", "text": self.stt(np.concatenate(frames)).strip()})
                except Exception:
                    pass


def main(make_engine=None) -> None:
    """make_engine(stt, mode, cfg): the Engine; tests pass one without models."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", default="jackrabbit-tdt-boost")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--device", default=None)
    ap.add_argument("--file")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--cfg", default="{}")
    ap.add_argument("--no-partials", action="store_true")
    ap.add_argument("--hold", action="store_true", help="load, meter the input, transcribe only after listen")
    a = ap.parse_args()
    protect_stdout()
    import awake
    awake.hold("Live RO2EN speech recognition")   # a napped engine fell minutes behind
    cfg = json.loads(a.cfg)
    if a.no_partials:
        cfg["partial_every_s"] = 0
    stop = threading.Event()
    q: queue.Queue = queue.Queue()
    meter = LevelMeter(float(cfg.get("level_hz", 10)))
    ctl = SourceCtl(a.device, meter, hold=bool(a.hold and a.file), gain_db=cfg.get("input_gain_db", 0.0),
                    auto=bool(cfg.get("input_gain_auto", False)),
                    target_db=cfg.get("input_gain_target_db", AGC_TARGET_DB))
    # commands and the exit watch run from the start: an app that dies while the models are
    # still loading must not leave the engine behind
    threading.Thread(target=stdin_loop, args=(sys.stdin, q, stop, ctl), daemon=True).start()
    threading.Thread(target=parent_watch, args=(os.getppid(), q, stop), daemon=True).start()
    try:
        eng = (make_engine or Engine)(a.stt, a.mode, cfg)
    except Exception as e:                       # noqa: BLE001  shown by the app, then exit 1
        emit({"type": "error", "where": "engine", "msg": f"models failed to load: {type(e).__name__}: {e}"})
        raise
    eng.realtime = (not a.file or a.realtime) and bench_clock() is None   # a fast file run is not "behind"
    eng.meter = meter
    eng.ctl = ctl
    src = threading.Thread(target=file_source if a.file else mic_source,
                           args=((a.file, a.realtime) if a.file else (a.device,)) + (q, stop),
                           kwargs={"ctl": ctl}, daemon=True)
    emit({"type": "ready", "stt": a.stt, "multi": eng.multi is not None, "source": a.file or a.device or "default",
          "hold": bool(a.hold)})
    src.start()
    eng.run(q, stop, hold=a.hold, idle_watch=not a.file)


def _exit_soon(q: queue.Queue, stop: threading.Event, grace_s: float) -> None:
    """The app is gone: stop now, and exit for sure within grace_s even if a recognition call
    or the model loading is still running (nobody is left to read the result)."""
    stop.set()
    q.put(None)
    t = threading.Timer(grace_s, lambda: os._exit(0))
    t.daemon = True
    t.start()


def stdin_loop(stdin, q: queue.Queue, stop: threading.Event, ctl: SourceCtl, grace_s: float = 1.0) -> None:
    """Commands from the app, one JSON object per line. End of input means the app closed
    the pipe or died (even kill -9): the engine exits instead of holding the mic."""
    for line in stdin:
        try:
            c = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(c, dict):
            continue
        if c.get("cmd") == "mode":
            q.put({"mode": c["mode"]})           # applied in order with the audio
        elif c.get("cmd") == "listen":
            on = bool(c.get("on", True))
            if on:
                ctl.go.set()                     # a file waiting in hold starts playing
            q.put({"listen": on})
        elif c.get("cmd") == "cfg":
            q.put({"cfg": c.get("cfg") if isinstance(c.get("cfg"), dict) else {}})
        elif c.get("cmd") == "keywords":
            item = {"keywords": [w for w in c.get("words") or [] if isinstance(w, str)]}
            if isinstance(c.get("ro"), list):            # the names as Romanian speech has them
                item["ro"] = [w for w in c["ro"] if isinstance(w, str)]
            q.put(item)
        elif c.get("cmd") == "device":
            ctl.switch(c.get("device"))          # the mic thread reopens on the new input
        elif c.get("cmd") == "gain":
            ctl.set_gain(c.get("db", 0.0))       # from the next frame: no reopen, no restart
        elif c.get("cmd") == "agc":
            ctl.set_auto(c.get("on", False), c.get("target_db"))
        elif c.get("cmd") == "stop":
            q.put(None); stop.set(); return      # sentinel first: buffered speech is still committed
    _exit_soon(q, stop, grace_s)


def parent_watch(ppid: int, q: queue.Queue, stop: threading.Event, every_s: float = 0.5) -> None:
    """Exit when the app that started us is gone (we were re-parented)."""
    while not stop.is_set():
        if os.getppid() != ppid:
            _exit_soon(q, stop, 1.0)
            return
        time.sleep(every_s)


if __name__ == "__main__":
    main()
