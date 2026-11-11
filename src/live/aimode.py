"""The AI / Local knob (docs/live_voice_plan.md "AI translation"): which engine translates the captions
and writes the voice's summaries. Local = the profile's own models (NLLB, Gemma), nothing leaves the
Mac. AI = Claude Haiku 5.5 through ai.Engine, with the local models as a hot spare (translate.
HybridTranslator falls back per phrase). Changed live, no restart. Mixed into live.app.Live."""
from __future__ import annotations

import threading
import time

import ai
import summarize
import translate


class AIMixin:
    def _mixin_init(self):
        self._ai_engine: ai.Engine | None = None
        self._ai_tr: translate.HybridTranslator | None = None
        self._ai_lock = threading.Lock()
        self._ai_reset = False                         # the engine changed: contexts start clean at the next phrase
        self._ai_used = {k: {"phrases": 0, "s": 0.0} for k in ("ai", "local", "local_fallback")}   # this service (After)
        self._ai_used_prev: float | None = None
        self.register_state(self._ai_state)

    # --- settings -----------------------------------------------------------------------------
    def ai_cfg(self) -> dict:
        return self.cfg.get("ai") or {}

    def ai_selected(self) -> bool:
        return bool(self.ai_cfg().get("enabled", False))

    def ai(self) -> ai.Engine:
        with self._ai_lock:
            if self._ai_engine is None:
                self._ai_engine = ai.Engine(self.ai_cfg(), root=self.root, log=self._ai_event)
            return self._ai_engine

    def _ai_event(self, rec: dict):
        """engine_down / engine_up / engine into the service log while a service runs, and the
        operator's list of what the app did by itself."""
        if rec.get("event") == "engine_down":
            self._auto("AI is not answering: translating with the local models")
        elif rec.get("event") == "engine_up":
            self._auto("AI is answering again")
        if getattr(self, "running", False) and getattr(self, "log_path", None):
            try:
                self._log_event(rec)
            except Exception:                           # noqa: BLE001
                pass
        self._broadcast()

    # --- the translator the pipeline uses for this phrase ----------------------------------------
    def _translator(self):
        """The AI translator when AI is chosen and there is a key; else the local one (`self.tr`).
        The local translator is always loaded: it is the hot spare, and the fallback inside AI mode."""
        if self._ai_reset:                              # the knob was turned: both start clean, here, between
            self._ai_reset = False                      # two phrases, never under a translation in progress
            if self._ai_tr is not None:
                self._ai_tr.reset_context()
            if hasattr(self.tr, "reset_context"):
                self.tr.reset_context()
        if not self.ai_selected() or self.tr is None:
            return self.tr
        eng = self.ai()
        if not eng.key_present():
            return self.tr
        if self._ai_tr is None or self._ai_tr.engine is not eng:
            self._ai_tr = translate.HybridTranslator(eng, lambda: self.tr,
                                                     context_clauses=int(self.lc.get("context_clauses", 2)))
        self._ai_tr.context_clauses = int(self.lc.get("context_clauses", 2))
        return self._ai_tr

    def _reset_translator_context(self):
        """Show, Song off (output.py): both translators forget, whichever is in use."""
        sup = getattr(super(), "_reset_translator_context", None)
        if sup is not None:
            sup()
        if self._ai_tr is not None:
            self._ai_tr.reset_context()

    # --- the knob --------------------------------------------------------------------------------
    def set_engine(self, name: str) -> tuple[int, dict]:
        """"ai" or "local", now. AI needs a key: without one the knob stays on Local and says why."""
        if name not in ("ai", "local"):
            return 400, {"error": "bad_request", "detail": "engine must be ai or local"}
        if name == "ai" and not self.ai().key_present():
            return 409, {"error": "no_key", "detail": "Add an Anthropic API key in Settings first."}
        code, body = self.put_settings({"values": {"ai.enabled": name == "ai"}}, by_hand=False)
        if code != 200:
            return code, body
        return 200, self.state()

    def _ai_settings_changed(self, changed: dict):
        if not any(k.startswith("ai.") for k in changed):
            return
        if self._ai_engine is not None:
            self._ai_engine.configure(self.ai_cfg())
        if "ai.enabled" in changed:
            self._ai_reset = True                       # a change of engine starts clean, like a profile switch
            if getattr(self, "running", False):
                self._log_event({"event": "engine", "selected": "ai" if self.ai_selected() else "local"})

    def set_ai_key(self, key: str | None) -> tuple[int, dict]:
        """Store (or with None remove) the API key in the macOS Keychain. The key is never echoed."""
        if key is None:
            ai.keychain_delete()
        else:
            key = str(key).strip()
            if not ai.KEY_RE.match(key):
                return 400, {"error": "bad_request", "detail": "That does not look like an Anthropic API key (sk-ant-…)."}
            if not ai.keychain_set(key):
                return 507, {"error": "write_failed", "detail": "The key could not be saved in the Keychain."}
        self.ai().reload_key()
        if not self.ai().key_present() and self.ai_selected():
            self.put_settings({"values": {"ai.enabled": False}}, by_hand=False)
        return 200, self.state()

    # --- what the operator sees ---------------------------------------------------------------------
    def _ai_state(self) -> dict:
        sel = self.ai_selected()
        eng = self.ai()
        has = eng.key_present(wait=False)               # only whether a key exists: no client, no host allowed,
                                                        # and never waiting for the Keychain in the state feed
        if sel and not has and not eng._key_checked:    # AI chosen and the lookup still running at launch:
            has = True                                  # not "no key" for that moment
        if not sel:
            state = "local"
        elif not has:
            state = "no_key"
        else:
            state = "ai" if eng.up else "fallback"
        return {"ai": {"selected": "ai" if sel else "local", "state": state, "available": has,
                       "key_source": eng.key_source(), "model": eng.model,
                       "reason": eng.down_reason if state == "fallback" else None,
                       "down_s": round(time.time() - eng.down_since, 1) if state == "fallback" and eng.down_since else None,
                       "latency_median_s": eng.latency()["median_s"],
                       "used": {k: {"phrases": v["phrases"], "s": round(v["s"], 1)} for k, v in self._ai_used.items()}}}

    def _engine_note(self, rec: dict):
        """A caption was logged: its engine's count and seconds for After (report.engines, live)."""
        e = rec.get("engine") if rec.get("engine") in self._ai_used else "local"
        t = rec.get("t_end")
        if not isinstance(t, (int, float)):
            return
        span = rec.get("audio_s") if self._ai_used_prev is None else t - self._ai_used_prev
        self._ai_used[e]["phrases"] += 1
        self._ai_used[e]["s"] += min(max(float(span or 0.0), 0.0), 30.0)
        self._ai_used_prev = t

    def _engine_new_run(self):
        self._ai_used = {k: {"phrases": 0, "s": 0.0} for k in self._ai_used}
        self._ai_used_prev = None

    # --- the voice's summaries follow the same rule (voicelink.py) ---------------------------------------
    def _voice_summary(self, texts: list[str], lang: str, deadline: float) -> dict:
        if self.ai_selected() and self.ai().usable():
            why = summarize.refuse(texts, False)
            if why:
                return {"ok": False, "reason": why, "text": "", "gemma_s": 0.0, "engine": None}
            left = deadline - time.time() - 0.2
            if left > 0.5:
                gen = ai.ClaudeSummarizer(self.ai(), timeout=min(left, float(self.ai_cfg().get("summary_timeout_s", 3.0))))
                # (a summary that times out says nothing about captions: ClaudeSummarizer calls with soft=True)
                res = summarize.summarize(gen, texts, lang)
                if not str(res.get("reason") or "").startswith("error"):
                    return res                          # an answer, accepted or refused by a guard
        return self._voice_summary_local(texts, lang, deadline)

    def _voice_can_summarize(self) -> bool:
        if self.ai_selected() and self._ai_engine is not None and self._ai_engine.usable():
            return True
        return super()._voice_can_summarize()
