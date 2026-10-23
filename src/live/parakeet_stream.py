"""EXPERIMENTAL, NOT RECOMMENDED (see DECISIONS.md): streaming English recognition with Parakeet-TDT-0.6B-v3 (parakeet-mlx `transcribe_stream`).

Same interface as WhistleSTT's streaming part, so the engine can use either:
start_stream(on_event) / feed(chunk) / end_stream(). Events look like Whistle's:
{"text", "words": [{"word","start","end"}], "pending", "received", "pass_ms"}.
Words are committed once two consecutive passes agree on them (local agreement, as Whistle
does); parakeet-mlx's own finalization waits for a ~20 s context window, too slow for live.
API verified in parakeet_mlx/parakeet.py: BaseParakeet.transcribe_stream(context_size, depth,
...) -> StreamingParakeet (context manager) with add_audio(mx.array), finalized_tokens,
draft_tokens (AlignedToken: text, start, end)."""
from __future__ import annotations

import queue
import threading
import time

import numpy as np

SR = 16000


def _tokens_to_words(tokens, carry: dict | None = None) -> list[dict]:
    """SentencePiece pieces -> words: a piece starting with a space starts a new word.
    `carry` is an unfinished word from the previous batch that the first pieces may continue."""
    words: list[dict] = [dict(carry)] if carry else []
    for t in tokens:
        txt = t.text
        if not words or txt.startswith(" "):
            if txt.strip():
                words.append({"word": txt.strip(), "start": float(t.start), "end": float(t.end)})
        else:
            words[-1]["word"] += txt
            words[-1]["end"] = float(t.end)
    return words


class ParakeetStreamSTT:
    """MLX arrays are bound to the thread that created them, so one worker thread owns the
    model for its whole life and runs every stream; feed()/end_stream() only queue work."""

    def __init__(self, cfg: dict | None = None, repo: str = "mlx-community/parakeet-tdt-0.6b-v3"):
        cfg = cfg or {}
        self.context = tuple(cfg.get("context_size", (256, 256)))
        self.depth = int(cfg.get("depth", 1))
        self._cmd: queue.Queue = queue.Queue()
        self._ready = threading.Event()
        self._done = threading.Event()
        self._err: Exception | None = None
        self.streaming = False
        self.error: str | None = None                # set when a stream session failed
        threading.Thread(target=self._worker, args=(repo,), daemon=True).start()
        self._ready.wait()
        if self._err:
            raise self._err

    def _worker(self, repo: str) -> None:
        try:
            import mlx.core as mx
            from parakeet_mlx import from_pretrained
            model = from_pretrained(repo)
        except Exception as e:                       # noqa: BLE001
            self._err = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            cmd, on_event = self._cmd.get()          # ("start", callback)
            if cmd != "start":                       # audio left over from a failed session
                continue
            try:
                self._session(model, mx, on_event)
            except Exception as e:                   # noqa: BLE001  report, never die silently
                self.error = f"{type(e).__name__}: {e}"
                on_event({"error": self.error})
            self._done.set()

    def _session(self, model, mx, on_event) -> None:
        committed, prev, received = 0, [], 0.0
        norm = lambda w: w["word"].lower().strip(".,;:!?\"'")
        with model.transcribe_stream(context_size=self.context, depth=self.depth) as st:
            while True:
                c = self._cmd.get()[1]
                if c is None:
                    break
                t = time.perf_counter()
                st.add_audio(mx.array(c))
                received += len(c) / SR
                hyp = _tokens_to_words(list(st.finalized_tokens) + list(st.draft_tokens))
                # local agreement: commit the words two consecutive passes agree on;
                # the last hypothesis word is never committed (it may still grow)
                k = 0
                while k < min(len(hyp), len(prev)) - 1 and norm(hyp[k]) == norm(prev[k]) \
                        and hyp[k]["word"] == prev[k]["word"]:
                    k += 1
                new = hyp[committed:k] if k > committed else []
                committed = max(committed, k)
                prev = hyp
                on_event({"text": " ".join(w["word"] for w in new), "words": new,
                          "pending": " ".join(w["word"] for w in hyp[committed:]),
                          "received": received, "pass_ms": (time.perf_counter() - t) * 1000})
            hyp = _tokens_to_words(list(st.finalized_tokens) + list(st.draft_tokens))
            tail = hyp[committed:]
            on_event({"text": " ".join(w["word"] for w in tail), "words": tail, "pending": "",
                      "received": received, "pass_ms": 0.0})
        on_event({"end": True})

    def start_stream(self, on_event) -> None:
        self._done.clear()
        self.error = None
        self._cmd.put(("start", on_event))
        self.streaming = True

    def feed(self, chunk: np.ndarray) -> None:
        if self.streaming and not self.error:          # a dead stream must not grow the queue
            self._cmd.put(("audio", np.ascontiguousarray(chunk, dtype=np.float32)))

    def end_stream(self, wait: float = 15.0) -> None:
        if self.streaming:
            self._cmd.put(("audio", None))
            self._done.wait(wait)
            self.streaming = False
