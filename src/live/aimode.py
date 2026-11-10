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
        if not self.ai_selected() or self.tr is None:
            return self.tr
        eng = self.ai()
        if not eng.has_key():
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
        if name == "ai" and not self.ai().has_key():
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
            if self._ai_tr is not None:
                self._ai_tr.reset_context()             # a change of engine starts clean, like a profile switch
            if hasattr(self.tr, "reset_context"):
                self.tr.reset_context()
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
        if not self.ai().has_key() and self.ai_selected():
            self.put_settings({"values": {"ai.enabled": False}}, by_hand=False)
        return 200, self.state()

    # --- what the operator sees ---------------------------------------------------------------------
    def _ai_state(self) -> dict:
        sel = self.ai_selected()
        eng = self._ai_engine
        if eng is None and not sel:                     # never asked for: do not even look for a key yet
            eng = self.ai() if getattr(self, "_ai_looked", False) else None
        if eng is None:
            self._ai_looked = True
            eng = self.ai()
        has = eng.has_key()
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
                       "latency_median_s": eng.latency()["median_s"]}}

    # --- the voice's summaries follow the same rule (voicelink.py) ---------------------------------------
    def _voice_summary(self, texts: list[str], lang: str, deadline: float) -> dict:
        if self.ai_selected() and self.ai().usable():
            why = summarize.refuse(texts, False)
            if why:
                return {"ok": False, "reason": why, "text": "", "gemma_s": 0.0, "engine": None}
            left = deadline - time.time() - 0.2
            if left > 0.5:
                gen = ai.ClaudeSummarizer(self.ai(), timeout=min(left, float(self.ai_cfg().get("summary_timeout_s", 3.0))))
                res = summarize.summarize(gen, texts, lang)
                if not str(res.get("reason") or "").startswith("error"):
                    return res                          # an answer, accepted or refused by a guard
        return self._voice_summary_local(texts, lang, deadline)

    def _voice_can_summarize(self) -> bool:
        if self.ai_selected() and self._ai_engine is not None and self._ai_engine.usable():
            return True
        return super()._voice_can_summarize()
