"""English speech recognition with Cactus Whistle (cactus-needle), fully offline.

Used ONLY for English audio (EN→RO). RO→EN keeps Jackrabbit.

  WhistleSTT          one model per process, loaded once; .transcribe(samples) for a clause
                      and .start_stream()/.feed()/.end_stream() for live streaming
  ClauseAssembler     turns committed words (never provisional ones) into clauses, cut at
                      punctuation, at pauses taken from word timestamps, or at a length cap

Offline: telemetry is switched off and the vendored engine/weights are pinned via env vars
BEFORE needle is imported (see `offline_env`). Verified API: needle 3.2.8,
needle/agent/whistle.py (Whistle.transcribe / Whistle.stream)."""
from __future__ import annotations

import os
import pathlib
import queue
import re
import threading
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[2]
SR = 16000
_HARD = re.compile(r"[.!?;:…]$")
_SOFT = re.compile(r",$")


def offline_env(cfg: dict) -> None:
    """Must run before `import needle`: no telemetry, no first-use downloads."""
    os.environ["NEEDLE_TELEMETRY"] = "0"
    os.environ["DO_NOT_TRACK"] = "1"
    os.environ["HF_HUB_OFFLINE"] = "1"
    lib = ROOT / cfg.get("lib", "models/whistle/libneedle.dylib")
    if lib.exists():
        os.environ["NEEDLE3_LIB_PATH"] = str(lib)
    os.environ["NEEDLE_WHISTLE_WEIGHTS"] = str(weights_path(cfg))


def weights_path(cfg: dict) -> pathlib.Path:
    """`depth: full` = shipped whistle.cact; other depths map to files built with
    `needle whistle build --layers N` (listed under `weights_by_depth`)."""
    depth = str(cfg.get("depth", "full"))
    files = {"full": cfg.get("weights", "models/whistle/whistle.cact"),
             **{str(k): v for k, v in (cfg.get("weights_by_depth") or {}).items()}}
    if depth not in files:
        raise ValueError(f"whistle.depth={depth!r} has no weights file; known: {sorted(files)}")
    return ROOT / files[depth]


def load_keywords(path: str | pathlib.Path) -> str:
    p = ROOT / path
    if not p.exists():
        return ""
    words = [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.lstrip().startswith("#")]
    return "\n".join(dict.fromkeys(words))


class WhistleSTT:
    def __init__(self, cfg: dict):
        offline_env(cfg)
        import needle
        self.model = needle.Whistle(weights=os.environ["NEEDLE_WHISTLE_WEIGHTS"])
        self.keywords = load_keywords(cfg.get("keywords_file", "keywords_en.txt")) or None
        self.lock = threading.Lock()          # one model per process, not thread-safe
        self._q: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self.transcribe(np.zeros(SR, dtype=np.float32))     # load + warm up once at startup

    def transcribe(self, audio: np.ndarray, word_timestamps: bool = False) -> dict:
        with self.lock:
            return self.model.transcribe(np.ascontiguousarray(audio, dtype=np.float32), language="en",
                                         keywords=self.keywords, word_timestamps=word_timestamps)

    def __call__(self, audio: np.ndarray) -> str:
        return (self.transcribe(audio).get("text") or "").strip()

    # --- streaming --------------------------------------------------------------------
    def start_stream(self, on_event) -> None:
        """Run Whistle.stream over chunks fed via feed(); on_event(dict) gets every result."""
        self._q = queue.Queue()

        def chunks():
            while True:
                c = self._q.get()
                if c is None:
                    return
                yield c

        def run():
            with self.lock:
                for ev in self.model.stream(chunks(), language="en", keywords=self.keywords):
                    on_event(ev)
            on_event({"end": True})
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def feed(self, chunk: np.ndarray) -> None:
        if self._q is not None:
            self._q.put(np.ascontiguousarray(chunk, dtype=np.float32))

    def end_stream(self, wait: float = 10.0) -> None:
        if self._q is not None:
            self._q.put(None)
            self._thread.join(wait)
            self._q = None

    @property
    def streaming(self) -> bool:
        return self._q is not None


class ClauseAssembler:
    """Committed Whistle words -> clauses. Times are stream seconds; `t0` maps them to wall clock.
    Emit when a word ends a sentence/clause (. ? ! ; :), when the gap to the next committed word
    is >= pause_s, at a comma once the clause has >= comma_min_words, or when the clause spans
    max_chunk_s (keeps the first word inside the 6 s ceiling)."""

    def __init__(self, emit, pause_s=0.35, max_chunk_s=2.5, comma_min_words=6, min_words=2):
        self.emit = emit
        self.pause_s, self.max_chunk_s = pause_s, max_chunk_s
        self.comma_min_words, self.min_words = comma_min_words, min_words
        self.words: list[dict] = []
        self.t0 = None

    def add(self, words: list[dict], pass_ms: float = 0.0) -> None:
        for w in words:
            text = (w.get("word") or w.get("text") or "").strip()
            if not text:
                continue
            w = {"word": text, "start": float(w.get("start", 0)), "end": float(w.get("end", 0))}
            if self.words and w["start"] - self.words[-1]["end"] >= self.pause_s:
                self.flush(pass_ms)
            self.words.append(w)
            n = len(self.words)
            span = self.words[-1]["end"] - self.words[0]["start"]
            if (_HARD.search(text) and n >= self.min_words) or (_SOFT.search(text) and n >= self.comma_min_words) \
                    or span >= self.max_chunk_s:
                self.flush(pass_ms)

    def tick(self, now: float, pending: str = "", pass_ms: float = 0.0, hard_limit: float = 2.0) -> None:
        """Called on every stream event: close the clause once the speaker has paused
        (nothing pending and no new word for pause_s), or after hard_limit regardless."""
        if not self.words:
            return
        idle = now - self.words[-1]["end"]
        if (idle >= self.pause_s and not pending.strip()) or idle >= hard_limit:
            self.flush(pass_ms)

    def flush(self, pass_ms: float = 0.0) -> None:
        if not self.words:
            return
        ws, self.words = self.words, []
        self.emit(" ".join(w["word"] for w in ws), ws[0]["start"], ws[-1]["end"], pass_ms)


def words_from_event(ev: dict) -> list[dict]:
    """Committed words of a stream event; falls back to splitting committed text evenly
    when an event carries text without word timings."""
    ws = ev.get("words") or []
    if ws:
        return ws
    text = (ev.get("text") or "").strip()
    if not text:
        return []
    now = float(ev.get("received", 0.0))
    toks = text.split()
    return [{"word": t, "start": now, "end": now} for t in toks]
