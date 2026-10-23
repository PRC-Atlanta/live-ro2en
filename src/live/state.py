"""What the control page sees: one state dict, pushed to every /ws listener on change.
Modules add fields with `register_state(fn)` (fn returns a dict merged into the state)
instead of editing this file. Mixed into live.app.Live."""
from __future__ import annotations

import statistics


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
                  "lines": [{"src": h["source"], "dst": h["translation"], "lat": h["latency_s"],
                             "lang": h["lang"]} for h in self.history],
                  "screen": self.caps.text, "pp_error": self.pp.last_error,
                  "warnings": list(self.warnings), "counts": dict(self.counts),
                  "alerts": self.alerts(),
                  "translator": self.lc["translator"], "stt": self.lc["stt_model"]}
        for fn in self._state_fns:           # outside the lock: a provider may take its own
            st.update(fn())
        return st

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
