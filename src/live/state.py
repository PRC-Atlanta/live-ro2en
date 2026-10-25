"""What the control page sees: one state dict, pushed to every /ws listener on change.
Modules add fields with `register_state(fn)` (fn returns a dict merged into the state)
instead of editing this file. Mixed into live.app.Live."""
from __future__ import annotations

import datetime as dt
import statistics
import time
import traceback

import paths

LAG_HOLD_S = 30.0          # the dial's lag empties this long after the last translated line


class StateMixin:
    def register_state(self, fn):
        """Add fields to every state(); called once per module, from Live.__init__."""
        self._state_fns.append(fn)

    def state(self) -> dict:
        with self.lock:
            lat = self.lat[-30:]
            st = {"running": self.running, "status": self.status, "mode": self.lang.mode, "profile": self.profile,
                  "profiles": list(self.cfg.get("profiles", {})),
                  "lang": self.lang.current, "partial": self.partial,
                  "latency_last": round(lat[-1], 2) if lat else None,
                  "latency_median": round(statistics.median(lat), 2) if lat else None,
                  "lag_s": self._lag_s(),
                  "lines": [{"src": h["source"], "dst": h["translation"], "lat": h["latency_s"],
                             "lang": h["lang"]} for h in self.history],
                  "screen": self.caps.text, "pp_error": self.pp.last_error,
                  "warnings": list(self.warnings), "counts": dict(self.counts),
                  "alerts": self.alerts(),
                  "translator": self.lc["translator"], "stt": self.lc["stt_model"]}
        for fn in self._state_fns:           # outside the lock: a provider may take its own
            try:
                st.update(fn())
            except Exception:                # noqa: BLE001  one module's bug must not stop /ws and every reply
                self._state_failed(fn)
        return st

    def _lag_s(self) -> float | None:
        """The translation lag the dial shows: the newest translated line's delay, whether or not
        it reached ProPresenter (Hide, ProPresenter off); None once nothing new came for LAG_HOLD_S."""
        h = next((h for h in reversed(self.history) if h.get("translation") and h.get("latency_s") is not None), None)
        if h is None or time.time() - h.get("t_display", 0) > LAG_HOLD_S:
            return None
        return round(h["latency_s"], 1)

    def _state_failed(self, fn):
        """Log a failing provider to app_errors.log, once per provider (state() runs many times a second)."""
        name = getattr(fn, "__qualname__", repr(fn))
        seen = self.__dict__.setdefault("_state_errors", set())
        if name in seen:
            return
        seen.add(name)
        try:
            logs = paths.log_dir(self.root)
            logs.mkdir(parents=True, exist_ok=True)
            with open(logs / "app_errors.log", "a") as f:
                f.write(f"{dt.datetime.now().isoformat()} state provider {name} failed\n{traceback.format_exc()}\n")
        except OSError:
            pass

    def alerts(self) -> list[str]:
        """Live faults the operator must see now (cleared when they recover)."""
        out = []
        if self.audio_fault and self.running:
            out.append(f"NO AUDIO: {self.audio_fault}")
        if self.stt_behind and self.running:
            out.append(f"RECOGNITION BEHIND: {self.stt_behind}; catching up in longer chunks")
        if self.behind_s:
            out.append(f"CAPTIONS BEHIND: last one {self.behind_s} s after the speech "
                       f"(> {self.lc.get('lag_warn_s', 6.0)} s); the computer cannot keep up")
        return out

    def _broadcast(self):
        for q in list(self.listeners):
            try:
                q.put_nowait(self.state())
            except Exception:
                pass
