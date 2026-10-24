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

import gate
import guards
import overlap
import paths
import song
import translate
from captions import tidy_clause
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


def is_name_list(text: str) -> bool:
    """Mostly capitalised words ("Despina, Carmen, Dan, David, Cecilia."): names, which the
    translator rightly leaves as they are (idea 15). Needs at least two words."""
    words = re.findall(r"[^\W\d_]+", text)
    return len(words) >= 2 and sum(w[0].isupper() for w in words) >= 0.75 * len(words)


# a phrase that stops on one of these was cut mid-thought ("aduc condoleanțe din."): it waits
# for the next one (idea 20). Compared with diacritics: "sa" (his) ends phrases, "să" does not.
DANGLING = frozenset("din de la în cu pe pentru și că care dar iar sau ca să "
                     "of to in on for and but that which the a an".split())


def dangling(text: str) -> bool:
    words = tokens(text)
    return bool(words) and words[-1] in DANGLING


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
    """Clause `a` followed by clause `b` as one clause (text fields joined, a's start). The
    English texts lose a start that repeats a's end (idea 16: recognised twice)."""
    b = {**b, **{k: overlap.trim(a.get(k) or "", b.get(k) or "")[0] for k in ("multi", "en")
                 if a.get(k) and b.get(k) and b["t_start"] - a["t_end"] < OVERLAP_GAP_S}}
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


KEEPALIVE_TICK_S = 15.0                  # how often idle translators are checked
OVERLAP_GAP_S = 2.0                      # a repeat across a longer pause was said twice, not heard twice

class PipelineMixin:
    RESTART_KEYS = RESTART_KEYS

    def _mixin_init(self):
        self.auto_actions: list[dict] = []     # what the app did by itself, for the operator
        self._spare_tr: dict = {}              # translator spec -> loaded translator (hot switch)
        self._spare_lock = threading.Lock()    # a spare being kept warm is not handed over mid-call
        self._tr_used: dict = {}               # id(translator) -> when it last ran (keep-warm)
        self._keepalive: threading.Thread | None = None
        self._recent_out: collections.deque = collections.deque(maxlen=2)   # (source key, output key)
        self._current: dict | None = None      # the clause the worker is on (backlog)
        self._block: tuple = (None, None)      # (words, compiled pattern)
        self._pending_cap = 0.0                # the span a held phrase may grow to (_on_clause)
        self._prev_src: tuple = ("", "", None) # (language, text, t_end) of the last phrase (overlap)
        self._song_det = song.SongDetector()
        threading.Thread(target=gate.lexicon, daemon=True).start()   # the gate's word lists (~0.2 s)
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

    def _load_one(self, spec: str, lc: dict, direction: tuple = ("ro", "en")):
        """Translator `spec` loaded and warmed up in the direction it serves (outside _proc_lock)."""
        tr = translate.make_translator(spec)
        if hasattr(tr, "context_clauses"):
            tr.context_clauses = int(lc.get("context_clauses", 2))
        tr.translate(translate.WARMUP[direction], *direction, self.gloss)    # warm-up
        if hasattr(tr, "reset_context"):
            tr.reset_context()
        self._tr_used[id(tr)] = time.time()
        self._start_keepalive()
        return tr

    def _load_warm(self, lc: dict, have: dict | None = None):
        """A loaded, warmed-up translator for profile settings `lc` (outside _proc_lock): one,
        or one per direction (translate.ByDirection). Members in `have` (spec -> loaded) are
        reused, the others loaded and added to it."""
        tr = translate.assemble(lc, {} if have is None else have, lambda spec, d: self._load_one(spec, lc, d))
        if hasattr(tr, "context_clauses"):
            tr.context_clauses = int(lc.get("context_clauses", 2))
        return tr

    def _live_members(self) -> dict:
        """spec -> loaded translator of the current profile (one, or one per direction)."""
        return translate.members(self.tr, self.lc)

    def _last_used(self, tr) -> float:
        """When the loaded translator `tr` last ran (as the live translator, a member or a spare)."""
        live = self.tr
        used = live.used.get(id(tr), 0) if isinstance(live, translate.ByDirection) else 0
        return max(self._tr_used.get(id(tr), 0), used)

    # --- keep-warm: an idle model is paged out by macOS and its next phrase waits for it ------
    def _start_keepalive(self):
        """One background thread per app: every few seconds, `_keepalive_tick`."""
        t = getattr(self, "_keepalive", None)
        if t is not None and t.is_alive():
            return
        def loop():
            while not getattr(self, "_shutting_down", False):
                time.sleep(KEEPALIVE_TICK_S)
                try:
                    self._keepalive_tick()
                except Exception:                # noqa: BLE001 - never take the app down
                    pass
        self._keepalive = threading.Thread(target=loop, daemon=True, name="translator-keepalive")
        self._keepalive.start()

    def _keepalive_tick(self, now: float | None = None) -> int:
        """Translate a word on every loaded translator idle for translator_keepalive_s, so its
        weights stay in memory: after ~20 min as an unused spare, Gemma's first phrase took
        ~27 s (paged out). The live translator only when it is free; its context is kept.
        Returns how many were touched."""
        every = float(self.lc.get("translator_keepalive_s", 60) or 0)
        if not every:
            return 0
        now = time.time() if now is None else now
        n = 0
        for tr in list(self._spare_tr.values()):
            if now - self._tr_used.get(id(tr), 0) < every:
                continue
            with self._spare_lock:
                if tr in self._spare_tr.values():     # not handed over meanwhile
                    self._ping(tr)
                    n += 1
        tr = self.tr                                  # one, or one per direction: each on its own
        idle = [m for m in self._live_members().values() if now - self._last_used(m) >= every]
        if idle and self._proc_lock.acquire(blocking=False):
            try:
                if tr is self.tr:
                    for m in idle:
                        self._ping(m)
                        n += 1
            finally:
                self._proc_lock.release()
        return n

    def _ping(self, tr):
        ctx = list(tr.context) if isinstance(getattr(tr, "context", None), list) else None
        try:
            tr.translate("Amin.", "ro", "en")
        finally:
            if ctx is not None:
                tr.context = ctx
            self._tr_used[id(tr)] = time.time()

    def preload_translators(self):
        """Load every profile's translator that is not loaded yet, as spares, so the first
        switch is instant too. Only when RAM allows; slow (call it from a thread)."""
        if not self._keep_both_translators():
            return
        for name in self.cfg.get("profiles", {}):
            lc = self._profile_cfg(name)
            for d, spec in translate.direction_specs(lc).items():   # one per direction may differ
                if spec in translate.direction_specs(self.lc).values() or spec in self._spare_tr:
                    continue
                tr = self._load_one(spec, lc, d)
                with self._proc_lock:
                    if spec not in translate.direction_specs(self.lc).values():
                        if self._keep_both_translators():  # "Never" may have been chosen while it loaded
                            self._spare_tr.setdefault(spec, tr)
                    elif spec not in self._live_members():   # switched to this profile while it
                        self._spare_tr.setdefault(spec, tr)  # loaded: load_translator takes it

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
        old_key, key = translate.translator_key(self.lc), translate.translator_key(new)
        need = set(translate.direction_specs(new).values())
        keep, loaded, t0 = self._keep_both_translators(), False, time.time()
        tr, pool = None, {}                      # pool: spec -> loaded, the new profile's members
        if key != old_key:
            with self._spare_lock:               # waits for a keep-warm call on it to end
                pool = {s: self._spare_tr.pop(s) for s in need if s in self._spare_tr}
            pool.update({s: m for s, m in self._live_members().items() if s in need})   # shared
            if need - set(pool) and keep and self.running:
                tr, loaded = self._load_warm(new, pool), True    # the old one translates meanwhile
        wait_s = 0.0
        with self._proc_lock:                    # never pulled from under a translation
            t1 = time.time()
            if key != old_key:
                old = self._live_members()
                for m in old.values():
                    self._tr_used[id(m)] = self._last_used(m)    # its idle time goes with it
                self.tr = None
                for s, m in old.items():
                    if s in need:
                        pool.setdefault(s, m)
                    elif keep:
                        self._spare_tr[s] = m
                old = None
                if not keep:
                    self._spare_tr.clear()
                    self._free_memory()
                missing = need - set(pool)
                if tr is None and (self.running or not missing):   # warm reload: the engine keeps listening
                    try:
                        tr, loaded = self._load_warm(new, pool), bool(missing)
                    except Exception:
                        self.tr = self._load_warm(self.lc, pool)   # back to the old profile, still live
                        raise
                elif tr is None:                 # idle: what is loaded waits for load_translator
                    for s, m in pool.items():
                        self._spare_tr.setdefault(s, m)
                self.tr = tr
            if hasattr(self, "_profile_cfg"):    # a Settings save during the load went into cfg
                cur = {k: v for k, v in self._profile_cfg(name).items() if k != "translator_en_ro"}
                new = {**cur, **{k: new[k] for k in ("translator", "translator_en_ro") if k in new}}   # as loaded
            if self.tr is not None and hasattr(self.tr, "context_clauses"):
                self.tr.context_clauses = int(new.get("context_clauses", 2))
            if self.tr is not None and hasattr(self.tr, "reset_context"):
                self.tr.reset_context()
            self.profile, self.lc = name, new
            self._recent_out.clear()
            wait_s = time.time() - t1
        return {"translator": key, "loaded": loaded, "kept_spare": keep and key != old_key,
                "load_s": round(time.time() - t0, 2), "blocked_s": round(wait_s, 2)}

    # --- clauses -------------------------------------------------------------------------
    def _on_clause(self, ev: dict):
        """Hold very short fragments ('Dar acum.') briefly and merge them with the next
        clause: translating 1-3 words alone is poor. Sentence-final fragments go now.
        Slow speakers (idea 20): a phrase under phrase_min_words, or one that stops on "din",
        "și", "of"..., waits longer (hold_slow_s) and may grow to hold_max_s, because most of
        a slow speaker's seconds are pauses."""
        with self.lock:
            if self._timer:
                self._timer.cancel()
            self._hold_gen += 1                        # a timer that already fired is now stale
            p = self._pending
            cap = self.lc.get("max_chunk_s", 0)
            p_cap = getattr(self, "_pending_cap", cap)
            if p and p_cap and ev["t_end"] - p.get("t_speech", p["t_start"]) > p_cap:
                # merging would make the first word wait too long: send the fragment on its own
                self._pending = None
                self._submit(p)
                p = None
            if p:
                ev = merge_clauses(p, ev)
            self._pending = None
            text = ev.get("multi") if self.lang.current == "en" else ev["ro"]
            text = text or ev["ro"] or ""
            n, final = len(text.split()), text.rstrip().endswith(("?", "!"))
            slow = n > 0 and not final and (n < int(self.lc.get("phrase_min_words", 0) or 0)
                                  or (self.lc.get("hold_dangling", True) and dangling(text)))
            short = not final and n < self.lc.get("hold_min_words", 4)
            limit = max(cap, float(self.lc.get("hold_max_s", 10.0) or 0)) if slow and cap else cap
            if (slow or short) and not (limit and ev["t_end"] - ev.get("t_speech", ev["t_start"]) > limit):
                self._pending, self._pending_cap = ev, limit
                wait = float(self.lc.get("hold_slow_s", 6.5)) if slow else self.lc.get("hold_s", 1.2)
                self._timer = threading.Timer(wait, self._flush, args=(self._hold_gen,))
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
               **self._input_fields(ev),
               "time": dt.datetime.now().isoformat(timespec="milliseconds"), **extra}
        with self.lock:
            self.counts[outcome] += 1
        self._write_log(rec)

    def _input_fields(self, ev: dict) -> dict:
        """The input gain and level while the phrase was spoken (lifecycle._level_over)."""
        lvl = None
        if hasattr(self, "_level_over") and ev.get("t_start") is not None and ev.get("t_end") is not None:
            lvl = self._level_over(ev["t_start"], ev["t_end"])
        if hasattr(self, "_gain_now"):
            return {"gain_db": self._gain_now(), "gain_auto": self._gain_auto(), "level": lvl}
        return {"gain_db": float(self.lc.get("input_gain_db", 0) or 0), "gain_auto": False, "level": lvl}

    def _log_event(self, rec: dict):
        self._write_log({**rec, "time": dt.datetime.now().isoformat(timespec="milliseconds")})

    def _write_log(self, rec: dict):
        if self.log_path:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _source(self, ev: dict) -> tuple[str, str, dict]:
        """(language, text to translate, log fields). In auto mode the multilingual transcript
        decides, with the Romanian recogniser's own transcript as a second vote (langid).
        Romanian with nothing from the Romanian recogniser: the multilingual text, unless it
        reads as English (idea 10); clean English there is translated EN→RO this once (idea 12)."""
        ro, multi = ev.get("ro") or "", ev.get("multi") or ""
        auto = self.lang.mode == "auto"
        lang = self.lang.update(multi or ro, ro=ro if multi else None)
        # English text: Whistle when configured (ev["en"]), else the Parakeet text (A/B switch)
        english = ev.get("en") or multi
        if lang == "en":
            return "en", english or ro, {}
        if ro.strip() or not multi.strip():
            return "ro", ro, {}
        s = lang_score(multi)
        if auto and s <= 0 and self._gate_ok(english, "en") and not self._gate_ok(multi, "ro"):
            return "en", english, {"fallback": "english"}   # an English sentence the RO model missed
        if s >= 0:
            return "ro", multi, {"fallback": "multi"}   # the RO model heard nothing ("binecuvinteze.")
        return "ro", "", {}

    def _gate_names(self) -> set:
        rows = getattr(self.gloss, "rows", None) or []
        if getattr(self, "_gate_rows", None) is not rows:
            self._gate_rows, self._gate_name_set = rows, gate.names_from(rows)
        return self._gate_name_set

    def _gate_min_real(self) -> float:
        return float(self.lc.get("gate_min_real", 0.6) or 0)

    def _gate_ok(self, text: str, lang: str) -> bool:
        if not self._gate_min_real():
            return lang == "ro"                         # gate off: no English rescue, as before
        return gate.check(text, lang, gate.lexicon(), self._gate_names(), self._gate_min_real())[0]

    def _gate(self, ev: dict, src: str, lang: str) -> tuple[str | None, str, dict]:
        """The confidence gate (idea 17): (text to translate or None, language, log fields).
        Garbled sentences are cut; a phrase with nothing reliable left is logged as
        `low_confidence` and not translated. When the other recogniser's transcript of the same
        audio is clean in its own language, that one is translated instead (in auto mode):
        English heard by the Romanian model as "Gay va polua...", "Slavă lui Dumnezeu" heard by
        the English one as "slava ludum nezukum tum"."""
        min_real = self._gate_min_real()
        if not min_real:
            return src, lang, {}
        names = self._gate_names()
        kept, why, cut = gate.screen(src, lang, gate.lexicon(), names, min_real)
        if kept and not cut:
            return src, lang, {}
        other = "en" if lang == "ro" else "ro"
        alt = ((ev.get("en") or ev.get("multi")) if other == "en" else ev.get("ro")) or ""
        alt = apply_spellings(alt, self.cfg.get("spellings") or {})
        if (not kept and self.lang.mode == "auto" and alt.strip() and alt.strip() != src.strip()
                and (lang_score(alt) < -0.15 if other == "en" else lang_score(alt) > 0.15)
                and gate.screen(alt, other, gate.lexicon(), names, min_real) == (alt.strip(), "", [])):
            return alt, other, {"gate_switched": src}
        if not kept and gate.screen(src, other, gate.lexicon(), names, min_real) == (src.strip(), "", []):
            return src, lang, {}                        # clean speech in the other language: a quote, shown as is
        if kept:
            with self.lock:
                self.counts["gate_cut"] += 1
            return kept, lang, {"gate_cut": cut}
        self._log_failure(ev, "low_confidence", lang=lang, source=src, reason=why,
                          features=gate.features(src, lang, gate.lexicon(), names))
        return None, lang, {}

    def _trim_overlap(self, ev: dict, lang: str, text: str) -> tuple[str | None, dict]:
        """English text without a start that repeats the end of the previous phrase (idea 16):
        (text, log fields), or None when the whole phrase was a repeat (logged)."""
        prev_lang, prev, prev_end = getattr(self, "_prev_src", ("", "", None))
        self._prev_src = (lang, text, ev["t_end"])
        if (lang != "en" or prev_lang != "en" or prev_end is None or not self.lc.get("overlap_trim", True)
                or ev["t_start"] - prev_end >= OVERLAP_GAP_S):
            return text, {}
        kept, dropped = overlap.trim(prev, text, lang)
        if not dropped:
            return text, {}
        with self.lock:
            self.counts["overlap_trimmed"] += 1
        if not kept.strip():
            self._log_failure(ev, "overlap_repeat", lang=lang, source=text)
            return None, {}
        return kept, {"overlap_trimmed": dropped}

    def _song_hold(self, ev: dict, lang: str, text: str) -> bool:
        """During a song (the operator's Song key, or singing detected with live.song_detect)
        the phrase is logged as `song` and not translated (idea 18). Every phrase feeds the
        detector, so it knows the speech level when a song starts."""
        inp = self._input_fields(ev)
        det = getattr(self, "_song_det", None)
        on, f = det.update(text, ev.get("t_speech", ev["t_start"]), ev["t_end"], inp.get("level"),
                           inp.get("gain_db")) if det else (False, {})
        if self.lc.get("song_detect", False) and hasattr(self, "song"):
            if on and not self.song_on and time.time() >= getattr(self, "_song_snooze", 0.0):
                self.song(True, auto=True)
            elif not on and self.song_on and self.song_auto:
                self.song(False, auto=True)
        if not getattr(self, "song_on", False):
            return False
        self._log_failure(ev, "song", lang=lang, source=text, song={**f, "auto": self.song_auto})
        return True

    def _handle_locked(self, ev: dict):
        lang, src_text, src_info = self._source(ev)
        src_text = apply_spellings(src_text, self.cfg.get("spellings") or {})
        if self._song_hold(ev, lang, src_text):          # a song: not captioned (logged)
            return
        src_text, trimmed = self._trim_overlap(ev, lang, src_text)
        if src_text is None:                            # all of it said in the last phrase (logged)
            return
        src_info.update(trimmed)
        if not src_text.strip():
            self._log_failure(ev, "empty_stt", lang=lang)
            return
        src_text, lang, gated = self._gate(ev, src_text, lang)
        if src_text is None:                            # unreliable recognition (logged)
            return
        src_info.update(gated)
        tgt = "en" if lang == "ro" else "ro"
        clauses, tail = split_clauses(src_text)
        parts = clauses + ([tail] if tail else [])        # at a pause everything is committed
        if ev.get("caught_up"):                           # catching up: few calls, each bounded
            parts = pack_parts(parts, int(self.lc.get("catchup_max_words", 60)))
        t0 = time.time()
        out, truncated, verse = [], False, []
        for p in parts:
            out.append(self.tr.translate(p, lang, tgt, self.gloss, use_context=True)
                       if hasattr(self.tr, "context") else self.tr.translate(p, lang, tgt, self.gloss))
            truncated |= bool(getattr(self.tr, "last_truncated", False))
            verse += [getattr(self.tr, "last_verse", None)] if getattr(self.tr, "last_verse", None) else []
        translation = " ".join(o for o in out if o)
        # a published verse is not checked for numbers/negations ("nu voi duce lipsă" = "I shall lack nothing")
        translation, guard = (translation, {}) if verse else \
            guards.check(self.tr, src_text, translation, lang, tgt, self.gloss)
        t_tr = time.time() - t0
        self._tr_used[id(self.tr)] = time.time()
        if not translation.strip():                     # nothing new to show: not a success
            self._log_failure(ev, "empty_mt", lang=lang, source=src_text, mt_s=round(t_tr, 3))
            return
        passthrough = False
        if is_echo(src_text, translation):
            s = lang_score(src_text)
            if (tgt == "en" and s < -0.15) or (tgt == "ro" and s > 0.15) or is_name_list(src_text):
                passthrough = True                      # an English quote in RO→EN, or a list of names: show it
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
        # one running caption, not a sentence per chunk: the chunk's period waits for the
        # next chunk, and is shown only if a sentence-length pause came first
        start = ev.get("t_speech", ev["t_start"])
        prev_end, prev_open = getattr(self, "_cap_end", None), getattr(self, "_cap_open", False)
        continues = prev_open and prev_end is not None and \
            start - prev_end < float(self.lc.get("sentence_pause_s", 1.0))
        screen_text, self._cap_open = tidy_clause(translation, continues, tgt, src_text)
        self._cap_end = ev["t_end"]
        t1 = time.time()
        shown, ok, outcome = self._show(screen_text, close="." if prev_open and not continues else "",
                                        audio_s=ev.get("audio_s"))
        t_disp = time.time()
        latency = t_disp - ev["t_end"]
        n_g, ok_g = self.gloss.adherence(src_text, translation, lang)
        rec = {"id": ev["id"], "outcome": outcome, "t_start": ev["t_start"], "t_end": ev["t_end"], "t_display": t_disp,
               "lang": lang, "source": src_text, "ro": ev.get("ro", ""), "multi": ev.get("multi", ""), **src_info,
               "en_recognizer": (ev.get("source") or ("whistle" if ev.get("en") else "parakeet")) if lang == "en" else None, "translation": translation, "latency_s": round(latency, 3),
               # average delay per word, approximating words as evenly spread over the commit
               "word_lag_s": round(latency + (ev["t_end"] - ev["t_start"]) / 2, 3), "profile": self.profile,
               "t_speech": ev.get("t_speech", ev["t_start"]),
               "first_word_s": round(t_disp - ev.get("t_speech", ev["t_start"]), 3),  # what a bilingual listener notices
               "stt_s": ev["stt_s"], "vad_wait_s": round(ev["t_emit"] - ev["t_end"] - ev["stt_s"], 3),
               "mt_s": round(t_tr, 3), "pp_s": round(t_disp - t1, 4), "pp_ok": ok,
               "caught_up": ev.get("caught_up", 1), "mt_truncated": truncated, "passthrough": passthrough,
               "pp_error": self.pp.last_error, "audio_s": ev["audio_s"], "glossary": [n_g, ok_g],
               **self._input_fields(ev),
               **safety, **guard, **({"verse": verse[0] if len(verse) == 1 else verse} if verse else {}),
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
