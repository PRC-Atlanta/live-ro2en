"""Committed clause -> translation -> caption -> log record, in arrival order by one worker.
Mixed into live.app.Live (owned by S4)."""
from __future__ import annotations

import collections
import datetime as dt
import gc
import json
import pathlib
import queue
import re
import sys
import threading
import time
import unicodedata

import paths
import translate
from langid import score as lang_score
from segmenter import split_clauses
from textnorm import apply_spellings, fold_diacritics, tokens

# A change of these needs a new engine process; every other profile setting is applied live.
RESTART_KEYS = ("stt_model", "stt_venv", "en_recognizer")
AUTO_ACTIONS_KEEP = 20


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


# --- safety filter: what a translator must never put on the church screen --------------------
def _key(text: str) -> tuple[str, ...]:
    return tuple(fold_diacritics(t) for t in tokens(text))


def _max_repeat(keys: list[str], max_unit: int = 8) -> int:
    """The most times any run of 1..max_unit words repeats back to back."""
    best = 1
    for u in range(1, max_unit + 1):
        for i in range(len(keys) - 2 * u + 1):
            k = 1
            while keys[i + k * u:i + (k + 1) * u] == keys[i:i + u]:
                k += 1
            best = max(best, k)
    return best


def trim_loops(out: str, src: str, min_repeats: int = 4, max_unit: int = 8) -> tuple[str, bool]:
    """A translator caught in a loop ('and he said, and he said, and he said, ...') repeats a
    phrase back to back. Keep its first occurrence. A source that itself repeats as much
    ('Aleluia, aleluia, aleluia, aleluia') is the speaker's, and is left alone.
    "Holy, holy, holy" (3 times) is below the default threshold."""
    words = out.split()
    keys = [" ".join(_key(w)) for w in words]
    if _max_repeat(keys, max_unit) < min_repeats or _max_repeat(list(_key(src)), max_unit) >= min_repeats:
        return out, False
    for u in range(1, max_unit + 1):           # shortest unit first: 'the the the the' -> 'the'
        i = 0
        while i + 2 * u <= len(keys):
            k = 1
            while keys[i + k * u:i + (k + 1) * u] == keys[i:i + u]:
                k += 1
            if k >= min_repeats:
                del words[i + u:i + k * u], keys[i + u:i + k * u]
            i += 1
    return " ".join(words), True


def overlong(out: str, src: str, ratio: float = 3.0, min_words: int = 8) -> bool:
    """More than `ratio` times the source's words (and at least min_words): the translator
    added text that was not said (a re-translated context sentence, an invented ending)."""
    n_out, n_src = len(out.split()), max(len(src.split()), 1)
    return bool(ratio) and n_out >= min_words and n_out > ratio * n_src


def blocklist_re(words) -> re.Pattern | None:
    """One pattern for the blocklist: whole words or phrases, any case, diacritics optional."""
    alts = sorted({" ".join(_key(w)) for w in words if _key(w)}, key=len, reverse=True)
    if not alts:
        return None
    return re.compile(r"(?<![\w-])(?:" + "|".join(r"\W+".join(map(re.escape, a.split())) for a in alts)
                      + r")(?![\w-])", re.IGNORECASE)


def mask(text: str, pat: re.Pattern | None, sub: str = "***") -> tuple[str, int]:
    """Replace every blocklist match with `sub`; returns (text, matches)."""
    if pat is None:
        return text, 0
    text = unicodedata.normalize("NFC", text)
    folded = fold_diacritics(text.lower())
    if len(folded) != len(text):               # a case mapping changed the length: match as is
        folded = text
    spans = [m.span() for m in pat.finditer(folded)]
    for a, b in reversed(spans):
        text = text[:a] + sub + text[b:]
    return text, len(spans)


class PipelineMixin:
    RESTART_KEYS = RESTART_KEYS

    def _mixin_init(self):
        self.auto_actions: list[dict] = []     # what the app did by itself, for the operator
        self._spare_tr: dict = {}              # translator spec -> loaded translator (hot switch)
        self._recent_out: collections.deque = collections.deque(maxlen=2)   # (source key, output key)
        self._current: dict | None = None      # the clause the worker is on (backlog)
        self._block: tuple = (None, None)      # (words, compiled pattern)
        self.register_state(self._pipeline_state)

    # --- pace, backlog, auto actions -----------------------------------------------------
    def _backlog(self) -> dict:
        """Clauses waiting for the translator (plus the one it is on) and the oldest's age."""
        with self._work.mutex:
            waiting = list(self._work.queue)
        cur = self._current
        evs = ([cur] if cur else []) + waiting
        ends = [e["t_end"] for e in evs if isinstance(e, dict) and isinstance(e.get("t_end"), (int, float))]
        return {"n": len(evs), "oldest_s": round(max(0.0, time.time() - min(ends)), 1) if ends else None}

    def _pipeline_state(self) -> dict:
        backlog = self._backlog()
        warn = float(self.lc.get("lag_warn_s", 6.0) or 0)
        delay = max(self.behind_s or 0.0, backlog["oldest_s"] or 0.0)
        if warn and delay > 1.5 * warn:
            pace = "behind"
        elif (warn and delay > warn) or (self.stt_behind and self.running):
            pace = "slow"
        else:
            pace = "ok"
        return {"pace": {"state": pace, "behind_s": round(delay, 1) if pace != "ok" and delay else None},
                "backlog": backlog, "auto_actions": list(self.auto_actions)}

    def _auto(self, msg: str):
        """Something the app did by itself (caught up, dropped a looping caption...), shown to
        the operator, newest last. The clause's own log record already says what happened."""
        with self.lock:
            self.auto_actions = (self.auto_actions + [{"t": round(time.time(), 3), "msg": msg}])[-AUTO_ACTIONS_KEEP:]

    # --- hot Fast <-> Quality switch (called by LifecycleMixin.set_profile) --------------
    def _keep_both_translators(self) -> bool:
        """Keep the other profile's translator loaded: `live.keep_both_translators`
        true/false, or auto (the default) = this Mac has the RAM for it (>= 24 GB)."""
        v = self.lc.get("keep_both_translators", "auto")
        if isinstance(v, bool):
            return v
        import model_setup
        return model_setup.hardware()["ram_bytes"] >= model_setup.QUALITY_RAM

    def _load_warm(self, lc: dict):
        """A loaded, warmed-up translator for profile settings `lc` (outside _proc_lock)."""
        tr = translate.make_translator(lc["translator"])
        if hasattr(tr, "context_clauses"):
            tr.context_clauses = int(lc.get("context_clauses", 2))
        tr.translate("Bună dimineața.", "ro", "en", self.gloss)    # warm-up
        if hasattr(tr, "reset_context"):
            tr.reset_context()
        return tr

    def preload_translators(self):
        """Load every profile's translator that is not loaded yet, as spares, so the first
        switch is instant too. Only when RAM allows; slow (call it from a thread)."""
        if not self._keep_both_translators():
            return
        for name in self.cfg.get("profiles", {}):
            lc = self._profile_cfg(name)
            spec = lc["translator"]
            if spec == self.lc["translator"] or spec in self._spare_tr:
                continue
            tr = self._load_warm(lc)
            with self._proc_lock:
                if spec != self.lc["translator"]:
                    self._spare_tr.setdefault(spec, tr)
                elif self.tr is None:            # switched to this profile while it loaded
                    self.tr = tr

    def _free_memory(self):
        gc.collect()
        mx = sys.modules.get("mlx.core")
        clear = getattr(mx, "clear_cache", None) or getattr(getattr(mx, "metal", None), "clear_cache", None)
        if clear:
            try:
                clear()
            except Exception:                    # noqa: BLE001 - best effort
                pass

    def _swap_profile(self, name: str, new: dict) -> dict:
        """Put profile `name` (settings `new`) in place between two clauses. With RAM for
        both, the new translator loads while the old one keeps translating, and the old one
        is kept as a spare; otherwise (or with no spare) the old one is freed first and the
        clauses that arrive meanwhile wait in the queue (caught up after). Returns details
        for the log."""
        old_spec, spec = self.lc["translator"], new["translator"]
        keep, loaded, t0 = self._keep_both_translators(), False, time.time()
        tr = None
        if spec != old_spec:
            tr = self._spare_tr.pop(spec, None)
            if tr is None and keep and self.running:
                tr, loaded = self._load_warm(new), True      # the old one translates meanwhile
        wait_s = 0.0
        with self._proc_lock:                    # never pulled from under a translation
            t1 = time.time()
            if spec != old_spec:
                old, self.tr = self.tr, None
                if old is not None and keep:
                    self._spare_tr[old_spec] = old
                old = None
                if not keep:
                    self._spare_tr.clear()
                    self._free_memory()
                if tr is None and self.running:  # warm reload: the engine keeps listening
                    try:
                        tr, loaded = self._load_warm(new), True
                    except Exception:
                        self.tr = self._load_warm(self.lc)   # back to the old profile, still live
                        raise
                self.tr = tr
            if self.tr is not None and hasattr(self.tr, "context_clauses"):
                self.tr.context_clauses = int(new.get("context_clauses", 2))
            if self.tr is not None and hasattr(self.tr, "reset_context"):
                self.tr.reset_context()
            self.profile, self.lc = name, new
            self._recent_out.clear()
            wait_s = time.time() - t1
        return {"translator": spec, "loaded": loaded, "kept_spare": keep and spec != old_spec,
                "load_s": round(time.time() - t0, 2), "blocked_s": round(wait_s, 2)}

    # --- clauses -------------------------------------------------------------------------
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
                self._current = ev
                ev, n = self._catch_up(ev)
                self._current = ev
                self._handle(ev)
                self._current = None
                self._broadcast()
            finally:
                self._current = None
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
            self._auto(f"Caught up: {n} phrases shown together")
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
        translation, safety = self._safety(ev, src_text, translation, lang, t_tr)
        if translation is None:                         # dropped by the safety filter (logged)
            return
        if truncated:                                   # shown (better than nothing), but counted
            with self.lock:
                self.counts["mt_truncated"] += 1
        t1 = time.time()
        shown, ok, outcome = self._show(translation)    # honours Hide/Hold (OutputMixin)
        t_disp = time.time()
        latency = t_disp - ev["t_end"]
        n_g, ok_g = self.gloss.adherence(src_text, translation, lang)
        rec = {"id": ev["id"], "outcome": outcome, "t_start": ev["t_start"], "t_end": ev["t_end"], "t_display": t_disp,
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
               **safety,
               "time": dt.datetime.now().isoformat(timespec="milliseconds")}
        warn_s = float(self.lc.get("lag_warn_s", 6.0))
        late = bool(warn_s) and latency > warn_s
        self.behind_s = round(latency, 1) if late else None     # clears on the next timely caption
        with self.lock:
            self.history = (self.history + [rec])[-10:]
            self.counts[outcome] += 1
            if late:
                self.counts["late"] += 1
            if ok:
                self.lat.append(latency)
            elif ok is False:                           # None: nothing was sent (hidden or held)
                self.counts["pp_error"] += 1
        self._write_log(rec)

    def _blocklist(self) -> re.Pattern | None:
        """`safety.blocklist` (words or phrases) plus the lines of `paths.blocklist`, if set."""
        safety = self.cfg.get("safety") or {}
        words = list(safety.get("blocklist") or [])
        f = (self.cfg.get("paths") or {}).get("blocklist")
        if f:
            p = paths.resource(f, self.root)
            if pathlib.Path(p).is_file():
                words += [w.strip() for w in pathlib.Path(p).read_text(encoding="utf-8").splitlines()
                          if w.strip() and not w.lstrip().startswith("#")]
        words = tuple(words)
        if self._block[0] != words:
            self._block = (words, blocklist_re(words))
        return self._block[1]

    def _safety(self, ev: dict, src: str, out: str, lang: str, mt_s: float) -> tuple[str | None, dict]:
        """The safety filter, after the echo check: a translator loop inside one output is cut
        to its first occurrence; an over-long output (`mt_overlong`) and the same output a
        third time in a row for different speech (`mt_loop`) are dropped; blocklist words are
        masked. Returns (text to show or None, fields for the log record)."""
        lc, extra = self.lc, {}
        out, trimmed = trim_loops(out, src, int(lc.get("mt_loop_repeats", 4)))
        if trimmed:
            extra["loop_trimmed"] = True
            with self.lock:
                self.counts["loop_trimmed"] += 1
            self._auto("Cut a phrase the translator repeated")
        if overlong(out, src, float(lc.get("mt_max_ratio", 3.0)), int(lc.get("mt_ratio_min_words", 8))):
            self._log_failure(ev, "mt_overlong", lang=lang, source=src, translation=out, mt_s=round(mt_s, 3))
            self._auto("Dropped a caption much longer than what was said")
            return None, extra
        sk, ok_ = _key(src), _key(out)
        prev = list(self._recent_out)
        if (len(ok_) >= 2 and len(prev) == 2 and all(o == ok_ for _, o in prev)
                and not all(s == sk for s, _ in prev)):
            self._log_failure(ev, "mt_loop", lang=lang, source=src, translation=out, mt_s=round(mt_s, 3))
            self._auto("Dropped a caption repeated a third time")
            return None, extra
        self._recent_out.append((sk, ok_))
        out, n = mask(out, self._blocklist())
        if n:
            extra["masked"] = n
            with self.lock:
                self.counts["masked"] += n
            self._auto("Hid a word from the blocklist")
        return out, extra
