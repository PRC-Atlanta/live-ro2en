"""The Monitor screen's rows: what was transcribed, what was translated and what Live Voice said,
each with its time, one row per segment. Mixed into live.app.Live; the pipeline, the sentence
builder (speech.py) and the voice link (voicelink.py) call the three `_monitor_*` hooks.

A row starts as one phrase (transcript + translation). When Live Voice groups phrases into a
sentence, their rows become one row; the voice's item for that sentence (or a catch-up summary of
several) fills the third column. With Live Voice off, every phrase stays a row of its own."""
from __future__ import annotations

import threading

from fastapi import APIRouter

MAX_ROWS = 200


class MonitorMixin:
    def _mixin_init(self):
        self._mon_lock = threading.Lock()
        self._mon_rows: list[dict] = []
        self._mon_rev = 0                       # bumped on every change: the screen asks only when it moved
        self._mon_seq = 0

    # ---- hooks ------------------------------------------------------------------------------
    def _monitor_phrase(self, rec: dict):
        """A phrase was transcribed and translated (pipeline: the phrase record)."""
        try:
            t_disp = float(rec.get("t_display") or 0) or None
            t_heard = (t_disp - float(rec.get("mt_s") or 0) - float(rec.get("pp_s") or 0)) if t_disp else rec.get("t_end")
            row = {"phrases": [rec.get("id")], "sids": [], "lang": rec.get("lang"),
                   "heard": rec.get("source") or "", "heard_t": t_heard,
                   "said_t": rec.get("t_speech") or rec.get("t_start"),
                   "translated": rec.get("translation") or "", "translated_t": t_disp,
                   "outcome": rec.get("outcome"), "engines": [rec.get("engine") or "local"],
                   "voiced": "", "voiced_t": None, "voice_kind": None}
        except (TypeError, ValueError):
            return
        with self._mon_lock:
            self._mon_seq += 1
            row["key"] = self._mon_seq
            self._mon_rows = (self._mon_rows + [row])[-MAX_ROWS:]
            self._mon_rev += 1

    def _monitor_sentence(self, s: dict):
        """Live Voice made a sentence of these phrases (speech.py: `chunks` are phrase ids)."""
        chunks, sid = set(s.get("chunks") or []), s.get("sid")
        with self._mon_lock:
            idx = [i for i, r in enumerate(self._mon_rows) if chunks & set(r["phrases"])]
            if not idx:
                return
            self._merge(idx)["sids"].append(sid)
            self._mon_rev += 1

    def _monitor_voice(self, ev: dict):
        """Live Voice finished saying an item (voice.py's `item`: a sentence, or a summary of several)."""
        sids = set(ev.get("sids") or [])
        if not sids or ev.get("kind") == "test":
            return
        with self._mon_lock:
            idx = [i for i, r in enumerate(self._mon_rows) if sids & set(r["sids"])]
            if not idx:
                return
            row = self._merge(idx)
            text = (ev.get("text") or "").strip()
            row["voiced"] = (row["voiced"] + " " + text).strip() if row["voiced"] else text
            row["voiced_t"] = row["voiced_t"] or ev.get("play_start") or None
            row["voice_kind"] = ev.get("kind")
            self._mon_rev += 1

    def _merge(self, idx: list[int]) -> dict:
        """The rows at `idx` (sorted) become the first of them. Under _mon_lock."""
        first = self._mon_rows[idx[0]]
        for i in idx[1:]:
            r = self._mon_rows[i]
            first["phrases"] += r["phrases"]
            first["engines"] += [e for e in r["engines"] if e not in first["engines"]]
            first["sids"] += [x for x in r["sids"] if x not in first["sids"]]
            first["heard"] = f"{first['heard']} {r['heard']}".strip()
            first["translated"] = f"{first['translated']} {r['translated']}".strip()
            first["translated_t"] = first["translated_t"] or r["translated_t"]
            if r["voiced"]:
                first["voiced"] = f"{first['voiced']} {r['voiced']}".strip()
                first["voiced_t"] = first["voiced_t"] or r["voiced_t"]
        for i in reversed(idx[1:]):
            del self._mon_rows[i]
        return first

    # ---- what the screen reads ----------------------------------------------------------------
    def monitor(self) -> dict:
        with self._mon_lock:
            return {"rev": self._mon_rev, "rows": [dict(r) for r in self._mon_rows]}

    def monitor_clear(self):
        with self._mon_lock:
            self._mon_rows = []
            self._mon_rev += 1


def router(live) -> APIRouter:
    r = APIRouter()

    @r.get("/api/monitor")
    async def rows(since: int = -1):
        m = live.monitor()
        return {"rev": m["rev"]} if since == m["rev"] else m

    @r.post("/api/monitor/clear")
    async def clear():
        live.monitor_clear()
        return {"ok": True}

    return r
