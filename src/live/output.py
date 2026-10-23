"""What is on the ProPresenter screen, and the operator's say over it: blank, hide/show
(pause), hold, remove last (retract), fix last (correct), box size (layout), and the
ProPresenter health monitor, tests and reconnect. Mixed into live.app.Live (owned by S2).

The pipeline puts each translation on screen through `_show(translation)`, which honours
Hide and Hold. Every caps/ProPresenter change goes through `_out_lock`, so an operator
action never interleaves with a caption being sent."""
from __future__ import annotations

import collections
import threading

TEST_CAPTION = "Test caption from Live RO2EN\nIf you can read this, captions will work."
LAYOUT_LIMITS = {"lines": (1, 6), "chars": (10, 120)}


class OutputMixin:
    def _mixin_init(self):
        self.paused = False                    # Hide: nothing reaches ProPresenter
        self.held = False                      # Hold: the screen keeps the current caption
        self._corrections: collections.Counter = collections.Counter()   # per service log
        self._out_lock = threading.RLock()
        self._pp_thread: threading.Thread | None = None
        self._pp_stop = threading.Event()
        self._test_timer: threading.Timer | None = None
        self.register_state(self._output_state)

    def _output_state(self) -> dict:
        pp = self.pp
        st = pp.status() if hasattr(pp, "status") else {
            "connected": getattr(pp, "last_error", None) is None, "since": None,
            "last_ms": getattr(pp, "last_ms", None), "error_code": None}
        return {"paused": self.paused, "held": self.held, "pp": st,
                "corrections_n": self._corrections[str(self.log_path)]}

    # --- the pipeline's way onto the screen ------------------------------------------------
    def _show(self, translation: str) -> tuple[str, bool | None, str]:
        """Add a translation to the captions and send them, unless hidden or held.
        Returns (screen text, sent ok or None when nothing was sent, outcome) where outcome
        is "ok", "paused" (Hide is on: not added, so Show starts clean) or "held"."""
        with self._out_lock:
            if self.paused:
                return "", None, "paused"
            shown = self.caps.add(translation)
            if self.held:
                return shown, None, "held"
            return shown, self.pp.show(shown), "ok"

    def _push(self) -> bool:
        """Send what the captions hold now (an empty box hides the message)."""
        text = "" if self.paused else self.caps.text
        return self.pp.show(text) if text else self.pp.clear()

    # --- operator controls -----------------------------------------------------------------
    def clear(self):
        """Blank now: one-shot; captions continue with the next phrase."""
        with self._out_lock:
            self.caps.clear()
            self.pp.clear()
        self._broadcast()

    def blank(self):
        self.clear()
        self._log_event({"event": "blank"})

    def pause(self, on: bool = True):
        """Hide (on) / Show (off). Hide blanks ProPresenter and the stage display; recognition,
        translation and the log carry on. Show starts clean: nothing from the hidden time is
        dumped, and the translator's context is reset."""
        on = bool(on)
        with self._out_lock:
            if on == self.paused:
                return
            self.paused = on
            self.caps.clear()
            self.pp.clear()
        if not on:
            self._reset_translator_context()
        self._log_event({"event": "pause", "on": on})
        self._broadcast()

    def _reset_translator_context(self):
        tr = self.tr
        if tr is None:
            return
        if hasattr(tr, "reset_context"):
            tr.reset_context()
        elif hasattr(tr, "context"):
            tr.context = []

    def hold(self, on: bool = True):
        """Hold (on): the screen keeps the current caption while new ones are only collected.
        Release (off): the screen jumps to the current captions."""
        on = bool(on)
        with self._out_lock:
            if on == self.held:
                return
            self.held = on
            if not on:
                self._push()
        self._log_event({"event": "hold", "on": on})
        self._broadcast()

    def retract(self) -> bool:
        """Remove last caption: the screen shows the lines from before it."""
        with self._out_lock:
            before = self.caps.text
            text = self.caps.retract()
            if text is None:
                return False
            if not (self.paused or self.held):
                self._push()
        self._note_correction({"event": "correction", "kind": "retract", "removed": before, "screen": text})
        return True

    def correct(self, text: str) -> bool:
        """Fix last caption: the operator's text replaces the last caption on screen."""
        text = " ".join(str(text).split())
        if not text:
            return False
        with self._out_lock:
            before = self.caps.text
            shown = self.caps.replace_last(text)
            if shown is None:
                return False
            if not (self.paused or self.held):
                self._push()
        with self.lock:
            if self.history:
                self.history = self.history[:-1] + [{**self.history[-1], "translation": text,
                                                     "corrected": True}]
        self._note_correction({"event": "correction", "kind": "correct", "text": text,
                               "replaced": before, "screen": shown})
        return True

    def _note_correction(self, rec: dict):
        with self.lock:
            self._corrections[str(self.log_path)] += 1
        self._log_event(rec)
        self._broadcast()

    def layout(self, lines: int | None = None, chars: int | None = None):
        """Caption box size at runtime (not saved: settings own the saved size)."""
        for name, v in (("lines", lines), ("chars", chars)):
            lo, hi = LAYOUT_LIMITS[name]
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                raise ValueError(f"{name} must be a whole number from {lo} to {hi}")
        with self._out_lock:
            before = self.caps.text
            self.caps.resize(lines, chars)
            if self.caps.text != before and not (self.paused or self.held):
                self._push()
        self._log_event({"event": "layout", "lines": self.caps.max_lines, "chars": self.caps.max_chars})
        self._broadcast()

    # --- ProPresenter health, tests, reconnect ---------------------------------------------
    def start_pp_monitor(self):
        """Ping ProPresenter every `propresenter.health_every_s` (default 5 s; 0 = off),
        retrying on the backoff schedule while it is unreachable, and re-send the caption
        after an outage or a manual clear. Idempotent."""
        every = float((self.cfg.get("propresenter") or {}).get("health_every_s", 5))
        if every <= 0 or (self._pp_thread and self._pp_thread.is_alive()):
            return
        self._pp_stop.clear()
        self._pp_thread = threading.Thread(target=self._pp_monitor, args=(every,), daemon=True,
                                           name="pp-health")
        self._pp_thread.start()

    def stop_pp_monitor(self):
        self._pp_stop.set()

    def _pp_monitor(self, every: float):
        while not self._pp_stop.wait(0.25):
            try:
                self._pp_tick(every)
            except Exception:                  # noqa: BLE001 - the monitor must never die
                pass

    def _pp_tick(self, every: float = 5.0):
        pp = self.pp
        if not hasattr(pp, "health") or not pp.health_due(every):
            return
        before = (pp.status(), pp.resends)
        with self._out_lock:                   # a resend must not overtake a caption
            pp.health()
        if (pp.status(), pp.resends) != before:
            self._log_event({"event": "pp_health", **pp.status(), "resends": pp.resends})
            self._broadcast()

    def pp_test(self) -> dict:
        """Settings/preflight check: reachable, the message exists, the message list."""
        pp = self.pp
        if not hasattr(pp, "health"):
            return {"ok": False, "detail": "no ProPresenter adapter", "messages": [], "message_found": False,
                    "pp": self._output_state()["pp"]}
        with self._out_lock:
            st = pp.health(resend=False)
            msgs = (pp.list_messages() if st["connected"] else None)
        found = msgs is not None and pp.message_id in msgs
        if not st["connected"]:
            detail = f"ProPresenter is not answering at {pp.base} ({pp.last_error})"
        elif st["error_code"] == "auth":
            detail = "ProPresenter refused the connection (check its network settings)"
        elif st["error_code"] == "message_missing" or (msgs is not None and not found):
            detail = f'ProPresenter has no message called "{pp.message_id}"'
        else:
            detail = "ok"
        ok = st["connected"] and st["error_code"] is None and (msgs is None or found)
        self._broadcast()
        return {"ok": bool(ok), "detail": detail, "messages": msgs or [], "message_found": found, "pp": pp.status()}

    def pp_test_caption(self, restore_after_s: float = 4.0) -> dict:
        """Show a test caption, then put back what was there."""
        with self._out_lock:
            ok = self.pp.show(TEST_CAPTION)
        if self._test_timer:
            self._test_timer.cancel()
        if ok:
            self._test_timer = threading.Timer(restore_after_s, self._restore_after_test)
            self._test_timer.daemon = True
            self._test_timer.start()
        self._log_event({"event": "pp_test_caption", "ok": ok})
        self._broadcast()
        return {"ok": bool(ok), "detail": "ok" if ok else (self.pp.last_error or "not sent"),
                "pp": self._output_state()["pp"]}

    def _restore_after_test(self):
        with self._out_lock:
            if getattr(self.pp, "_want", TEST_CAPTION) == TEST_CAPTION:   # nothing newer shown since
                self._push()
        self._broadcast()

    def reconnect(self):
        """Operator pressed Reconnect: check now, forget the backoff, re-send the caption."""
        pp = self.pp
        if hasattr(pp, "reconnect"):
            with self._out_lock:
                pp.reconnect()
            self._log_event({"event": "pp_reconnect", **pp.status()})
        self._broadcast()
