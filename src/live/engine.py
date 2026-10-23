"""ASR engine process: audio in -> VAD -> clause commits -> STT. Speaks JSON lines.

  <stt venv>/bin/python src/live/engine.py --stt jackrabbit-tdt-boost [--device NAME|INDEX]
                                           [--file audio.wav --realtime]
stdout events (one JSON per line):
  {"type":"ready", ...}
  {"type":"partial","text":...}                                  (UI only)
  {"type":"clause","id":n,"ro":..,"multi":..,"t_start":..,"t_end":..,"t_emit":..,"stt_s":..}
  {"type":"eof"} when a --file source ends
stdin commands: {"cmd":"mode","mode":"auto|ro-en|en-ro"}, {"cmd":"stop"}
t_end = wall-clock time of the last voiced frame of the clause (the latency origin).
Never imports google_stt."""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import queue
import sys
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
from segmenter import PauseTracker          # noqa: E402
from vad import FRAME, SileroVAD            # noqa: E402

SR = 16000
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
        self(np.zeros(SR, dtype=np.float32))            # warm-up (MPS kernels compile)

    def __call__(self, audio: np.ndarray) -> str:
        torch, m = self.torch, self.m
        with torch.inference_mode():
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
        import tempfile

        import soundfile as sf
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as f:
            sf.write(f.name, audio, SR, subtype="PCM_16")
            return self.m.transcribe(f.name).text.strip()


def make_stt(name: str):
    return ParakeetSTT() if name == "parakeet-mlx" else NemoSTT(name)


# --- audio sources ------------------------------------------------------------------------

def file_source(path: str, realtime: bool, q: queue.Queue, stop: threading.Event) -> None:
    import soundfile as sf
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    x = x.mean(1)
    if sr != SR:
        import subprocess
        x = np.frombuffer(subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path, "-ac", "1",
                                          "-ar", str(SR), "-f", "f32le", "-"], capture_output=True,
                                         check=True).stdout, np.float32)
    t0 = time.time()
    for i in range(0, len(x) - FRAME + 1, FRAME):
        if stop.is_set():
            break
        if realtime:
            due = t0 + (i + FRAME) / SR
            d = due - time.time()
            if d > 0:
                time.sleep(d)
        q.put((time.time(), x[i:i + FRAME]))
    q.put(None)


def mic_source(device, q: queue.Queue, stop: threading.Event) -> None:
    import sounddevice as sd

    def cb(indata, frames, t, status):
        q.put((time.time(), indata[:, 0].copy()))
    dev = int(device) if isinstance(device, str) and device.isdigit() else device
    try:
        with sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=FRAME,
                            device=dev, callback=cb):
            while not stop.is_set():
                time.sleep(0.1)
    except Exception as e:                       # wrong index, device busy, unplugged mid-run
        emit({"type": "error", "where": "audio input", "msg": f"{type(e).__name__}: {e}"})


# --- main loop ----------------------------------------------------------------------------

class Engine:
    def __init__(self, stt_name: str, mode: str, cfg: dict):
        self.mode = mode
        self.stt = make_stt(stt_name)
        self.multi = None
        if stt_name != "parakeet-mlx":
            try:
                self.multi = ParakeetSTT()
            except Exception as e:                      # language auto-detect degrades to RO only
                emit({"type": "warn", "msg": f"multilingual model unavailable: {e}"})
        else:
            self.multi = self.stt
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
        # audio watchdog: no frames at all (device gone) or exact digital silence (muted/dead
        # interface) for this long is reported; a real quiet room still has a noise floor
        self.no_audio_s = float(cfg.get("audio_watchdog_s", 2.0))
        self.silent_s = float(cfg.get("digital_silence_s", 10.0))
        # English recognizer: Whistle (streaming, offline) or the previous Parakeet path (A/B)
        self.whistle = None
        self.wcfg = cfg.get("whistle") or {}
        self.en_rec = cfg.get("en_recognizer", "parakeet")
        if self.en_rec == "whistle":
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
            from parakeet_stream import ParakeetStreamSTT
            self.streamer = ParakeetStreamSTT(cfg.get("parakeet_stream") or {})
        self._wstream = None                             # (assembler, wall_t0, chunk buffer)

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
        if self.whistle is not None and self.mode != "ro-en":
            en = self.whistle(audio)                                         # English text
        if self.mode == "en-ro" and not ro:
            ro = en or multi
        self.n += 1
        emit({"type": "clause", "id": self.n, "ro": ro.strip(), "multi": (multi or en).strip(), "en": en.strip(),
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
            self._stop_whistle_stream()
            return False
        if self._wstream is None:
            from whistle_en import ClauseAssembler, words_from_event
            wall_t0 = ts - FRAME / SR

            def on_clause(text, start, end, pass_ms):
                self.n += 1
                emit({"type": "clause", "id": self.n, "ro": "", "multi": text, "en": text, "source": f"{self.en_rec}",
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

    def _stop_whistle_stream(self) -> None:
        if self._wstream is not None:
            if self._wstream["buf"]:
                self.streamer.feed(np.concatenate(self._wstream["buf"]))
            self.streamer.end_stream()                   # yields the final tail -> flush
            self._wstream = None

    def run(self, q: queue.Queue, stop: threading.Event | None = None) -> None:
        """Process queued audio until the None sentinel. Items are (time, frame) or
        {"mode": m}; a mode change travels through the queue so the speech buffered before it
        is committed under the old mode, never dropped or spliced onto later audio."""
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
                if self.no_audio_s and time.time() - last_frame > self.no_audio_s:
                    audio_state("no_frames", f"no audio from the input device for {time.time() - last_frame:.0f} s")
                continue
            if item is None:
                self._stop_whistle_stream()
                commit_all()
                emit({"type": "eof"})
                return
            if isinstance(item, dict):
                if item.get("mode") and item["mode"] != self.mode:
                    commit_all()
                    self.mode = item["mode"]
                continue
            ts, fr = item
            last_frame = time.time()
            if np.abs(fr).max() > 1e-6:
                last_sound = last_frame
            if self.silent_s and last_frame - last_sound > self.silent_s:
                audio_state("digital_silence",
                            f"input is digital silence for {last_frame - last_sound:.0f} s (muted or dead mic?)")
            else:
                audio_state(None)
            p = self.vad(fr)
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
            if self.max_commit and len(frames) >= self.max_commit:
                ev = "force"
            if ev == "pause" and len(frames) < self.min_commit:
                continue                                  # short pause inside a sentence: keep going
            first_voiced = next((t for t, pp in zip(times, probs) if pp >= self.pt.threshold), times[0])
            if ev == "pause":
                # trim trailing silence, keep 100 ms
                k = max(i for i, pp in enumerate(probs) if pp >= self.pt.threshold) + 4
                self.transcribe(np.concatenate(frames[:k]), times[0], last_voiced, first_voiced)
                frames, probs, times = [], [], []
            elif ev == "force":
                # cut at the least-voiced frame in the last 2.5 s (never mid-word if avoidable)
                lo = max(1, len(probs) - int(2.5 * SR / FRAME))
                hi = max(lo + 1, len(probs) - int(0.3 * SR / FRAME))
                cut = lo + int(np.argmin(probs[lo:hi]))
                self.transcribe(np.concatenate(frames[:cut]), times[0], times[cut - 1], first_voiced, final=False)
                frames, probs, times = frames[cut:], probs[cut:], times[cut:]
            elif self.partial_every and ts - last_partial > self.partial_every and len(frames) > 30:
                last_partial = ts
                try:
                    emit({"type": "partial", "text": self.stt(np.concatenate(frames)).strip()})
                except Exception:
                    pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stt", default="jackrabbit-tdt-boost")
    ap.add_argument("--mode", default="auto")
    ap.add_argument("--device", default=None)
    ap.add_argument("--file")
    ap.add_argument("--realtime", action="store_true")
    ap.add_argument("--cfg", default="{}")
    ap.add_argument("--no-partials", action="store_true")
    a = ap.parse_args()
    protect_stdout()
    cfg = json.loads(a.cfg)
    if a.no_partials:
        cfg["partial_every_s"] = 0
    eng = Engine(a.stt, a.mode, cfg)
    stop = threading.Event()
    q: queue.Queue = queue.Queue()

    def stdin_loop():
        for line in sys.stdin:
            try:
                c = json.loads(line)
            except json.JSONDecodeError:
                continue
            if c.get("cmd") == "mode":
                q.put({"mode": c["mode"]})       # applied in order with the audio
            elif c.get("cmd") == "stop":
                q.put(None); stop.set(); return  # sentinel first: buffered speech is still committed
    threading.Thread(target=stdin_loop, daemon=True).start()
    src = threading.Thread(target=file_source if a.file else mic_source,
                           args=((a.file, a.realtime) if a.file else (a.device,)) + (q, stop), daemon=True)
    emit({"type": "ready", "stt": a.stt, "multi": eng.multi is not None, "source": a.file or a.device or "default"})
    src.start()
    eng.run(q, stop)


if __name__ == "__main__":
    main()
