"""Committed clause -> translation -> caption -> log record, in arrival order by one worker.
Mixed into live.app.Live (owned by S4)."""
from __future__ import annotations

import datetime as dt
import json
import queue
import threading
import time

import paths
from langid import score as lang_score
from segmenter import split_clauses
from textnorm import apply_spellings, fold_diacritics, tokens


def is_echo(src: str, out: str, min_words: int = 4) -> bool:
    """The translator returned the source unchanged (same words, diacritics ignored).
    Short clauses are exempt: a name or 'Amin' may legitimately be the same."""
    s, o = [fold_diacritics(t) for t in tokens(src)], [fold_diacritics(t) for t in tokens(out)]
    return len(s) >= min_words and s == o


def pack_parts(parts: list[str], max_words: int) -> list[str]:
    """Join consecutive clauses into calls of at most max_words (a clause longer than that
    stays whole), so a backlog is translated in few calls without overrunning max_tokens."""
    out: list[str] = []
    for p in parts:
        if out and len(out[-1].split()) + len(p.split()) <= max_words:
            out[-1] = f"{out[-1]} {p}"
        else:
            out.append(p)
    return out


def merge_clauses(a: dict, b: dict) -> dict:
    """Clause `a` followed by clause `b` as one clause (text fields joined, a's start)."""
    return {**b, **{k: f"{a.get(k) or ''} {b.get(k) or ''}".strip() for k in ("ro", "multi", "en")},
            "t_start": a["t_start"], "t_speech": a.get("t_speech", a["t_start"]),
            # stt_s stays b's (what delayed b's t_emit, so vad_wait_s stays right); total kept apart
            "stt_s_total": a.get("stt_s_total", a["stt_s"]) + b["stt_s"], "audio_s": a["audio_s"] + b["audio_s"]}


class PipelineMixin:
    def _on_clause(self, ev: dict):
        """Hold very short fragments ('Dar acum.') briefly and merge them with the next
        clause: translating 1-3 words alone is poor. Sentence-final fragments go now."""
        with self.lock:
            if self._timer:
                self._timer.cancel()
            self._hold_gen += 1                        # a timer that already fired is now stale
            p = self._pending
            cap = self.lc.get("max_chunk_s", 0)
            if p and cap and ev["t_end"] - p.get("t_speech", p["t_start"]) > cap:
                # merging would make the first word wait too long: send the fragment on its own
                self._pending = None
                self._submit(p)
                p = None
            if p:
                ev = merge_clauses(p, ev)
            self._pending = None
            text = ev.get("multi") if self.lang.current == "en" else ev["ro"]
            text = text or ev["ro"] or ""
            short = len(text.split()) < self.lc.get("hold_min_words", 4) and not text.rstrip().endswith(("?", "!"))
            if short and not (cap and ev["t_end"] - ev.get("t_speech", ev["t_start"]) > cap):
                self._pending = ev
                self._timer = threading.Timer(self.lc.get("hold_s", 1.2), self._flush, args=(self._hold_gen,))
                self._timer.daemon = True
                self._timer.start()
                return
            self._submit(ev)                 # under the lock: a timer flush cannot slip in between

    def _flush(self, gen: int | None = None):
        """Send the held fragment. A hold timer passes its generation: if a newer clause has
        arrived since (and merged or re-held the fragment), the timer is stale and does nothing."""
        with self.lock:
            if gen is not None and gen != self._hold_gen:
                return
            ev, self._pending = self._pending, None
            if ev:
                self._submit(ev)

    def _submit(self, ev: dict):
        self._work.put(ev)
        with self._worker_lock:
            if not (self._worker and self._worker.is_alive()):
                self._worker = threading.Thread(target=self._work_loop, daemon=True)
                self._worker.start()

    def _work_loop(self):
        while True:
            ev = self._work.get()
            n = 1
            try:
                ev, n = self._catch_up(ev)
                self._handle(ev)
                self._broadcast()
            finally:
                for _ in range(n):
                    self._work.task_done()

    def _catch_up(self, ev: dict) -> tuple[dict, int]:
        """Behind (this clause already late, more waiting): merge the waiting clauses into
        one, translated in a single call, so the captions get back to the speaker instead of
        falling further behind clause by clause. Nothing is dropped."""
        warn_s = float(self.lc.get("lag_warn_s", 6.0))
        cap = int(self.lc.get("catchup_max_words", 60))
        if not warn_s or not cap or time.time() - ev["t_end"] < warn_s / 2:
            return ev, 1
        n = 1
        while max(len((ev.get(k) or "").split()) for k in ("ro", "multi", "en")) < cap:
            try:
                nxt = self._work.get_nowait()
            except queue.Empty:
                break
            ev, n = merge_clauses(ev, nxt), n + 1
        if n > 1:
            ev = {**ev, "caught_up": n}
            with self.lock:
                self.counts["caught_up"] += n
        return ev, n

    def _drain(self):
        """Wait until every submitted clause has been translated, shown and logged."""
        self._work.join()

    def _handle(self, ev: dict):
        """Never let one bad chunk stop the service: log it and carry on."""
        with self._proc_lock:
            try:
                self._handle_locked(ev)
            except Exception as e:                       # noqa: BLE001
                import traceback
                self.status = f"error on one chunk ({type(e).__name__}); still listening"
                logs = paths.log_dir(self.root)
                logs.mkdir(parents=True, exist_ok=True)
                with open(logs / "app_errors.log", "a") as f:
                    f.write(f"{dt.datetime.now().isoformat()} {ev.get('ro', '')[:200]!r}\n{traceback.format_exc()}\n")
                self._log_failure(ev, "error", error=f"{type(e).__name__}: {e}")

    def _log_failure(self, ev: dict, outcome: str, **extra):
        """A clause that produced no caption still gets a log record and a count, so empty
        or failed chunks are visible in the metrics instead of silently missing."""
        rec = {"id": ev.get("id"), "outcome": outcome, "t_start": ev.get("t_start"), "t_end": ev.get("t_end"),
               "ro": ev.get("ro", ""), "multi": ev.get("multi", ""), "en": ev.get("en", ""),
               "audio_s": ev.get("audio_s"), "stt_s": ev.get("stt_s"), "profile": self.profile,
               "time": dt.datetime.now().isoformat(timespec="milliseconds"), **extra}
        with self.lock:
            self.counts[outcome] += 1
        self._write_log(rec)

    def _log_event(self, rec: dict):
        self._write_log({**rec, "time": dt.datetime.now().isoformat(timespec="milliseconds")})

    def _write_log(self, rec: dict):
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _handle_locked(self, ev: dict):
        # language: in auto mode decide from the multilingual transcript
        probe = ev.get("multi") or ev.get("ro") or ""
        lang = self.lang.update(probe)
        # English text: Whistle when configured (ev["en"]), else the Parakeet text (A/B switch)
        src_text = ev["ro"] if lang == "ro" else (ev.get("en") or ev.get("multi") or ev["ro"])
        src_text = apply_spellings(src_text, self.cfg.get("spellings") or {})
        if not src_text.strip():
            self._log_failure(ev, "empty_stt", lang=lang)
            return
        tgt = "en" if lang == "ro" else "ro"
        clauses, tail = split_clauses(src_text)
        parts = clauses + ([tail] if tail else [])        # at a pause everything is committed
        if ev.get("caught_up"):                           # catching up: few calls, each bounded
            parts = pack_parts(parts, int(self.lc.get("catchup_max_words", 60)))
        t0 = time.time()
        out, truncated = [], False
        for p in parts:
            out.append(self.tr.translate(p, lang, tgt, self.gloss, use_context=True)
                       if hasattr(self.tr, "context") else self.tr.translate(p, lang, tgt, self.gloss))
            truncated |= bool(getattr(self.tr, "last_truncated", False))
        translation = " ".join(o for o in out if o)
        t_tr = time.time() - t0
        if not translation.strip():                     # nothing new to show: not a success
            self._log_failure(ev, "empty_mt", lang=lang, source=src_text, mt_s=round(t_tr, 3))
            return
        passthrough = False
        if is_echo(src_text, translation):
            s = lang_score(src_text)
            if (tgt == "en" and s < -0.15) or (tgt == "ro" and s > 0.15):
                passthrough = True                      # e.g. an English quote in RO→EN: show it
                with self.lock:
                    self.counts["passthrough"] += 1
            else:                                       # untranslated source is not a caption
                self._log_failure(ev, "mt_echo", lang=lang, source=src_text, translation=translation,
                                  mt_s=round(t_tr, 3))
                return
        if truncated:                                   # shown (better than nothing), but counted
            with self.lock:
                self.counts["mt_truncated"] += 1
        shown = self.caps.add(translation)
        t1 = time.time()
        ok = self.pp.show(shown)
        t_disp = time.time()
        latency = t_disp - ev["t_end"]
        n_g, ok_g = self.gloss.adherence(src_text, translation, lang)
        rec = {"id": ev["id"], "outcome": "ok", "t_start": ev["t_start"], "t_end": ev["t_end"], "t_display": t_disp,
               "lang": lang, "source": src_text,
               "en_recognizer": (ev.get("source") or ("whistle" if ev.get("en") else "parakeet")) if lang == "en" else None, "translation": translation, "latency_s": round(latency, 3),
               # average delay per word, approximating words as evenly spread over the commit
               "word_lag_s": round(latency + (ev["t_end"] - ev["t_start"]) / 2, 3), "profile": self.profile,
               "t_speech": ev.get("t_speech", ev["t_start"]),
               "first_word_s": round(t_disp - ev.get("t_speech", ev["t_start"]), 3),  # what a bilingual listener notices
               "stt_s": ev["stt_s"], "vad_wait_s": round(ev["t_emit"] - ev["t_end"] - ev["stt_s"], 3),
               "mt_s": round(t_tr, 3), "pp_s": round(t_disp - t1, 4), "pp_ok": ok,
               "caught_up": ev.get("caught_up", 1), "mt_truncated": truncated, "passthrough": passthrough,
               "pp_error": self.pp.last_error, "audio_s": ev["audio_s"], "glossary": [n_g, ok_g],
               "time": dt.datetime.now().isoformat(timespec="milliseconds")}
        warn_s = float(self.lc.get("lag_warn_s", 6.0))
        late = bool(warn_s) and latency > warn_s
        self.behind_s = round(latency, 1) if late else None     # clears on the next timely caption
        with self.lock:
            self.history = (self.history + [rec])[-10:]
            self.counts["ok"] += 1
            if late:
                self.counts["late"] += 1
            if ok:
                self.lat.append(latency)
            else:
                self.counts["pp_error"] += 1
        self._write_log(rec)
