"""Live Voice pacing (docs/live_voice_plan.md §4.3): how fast to speak a sentence, when to ask for a
catch-up summary, when to skip ahead. Pure functions and small state, no clocks and no audio, ported
from work/tts_bakeoff/src/live_sim/live_sim_pace.py (the voiced runs the user listened to).

The voice process calls `plan()` when its generation thread takes up a sentence; the tests replay the
recorded runs through the same functions in simulated time (`simulate`)."""
from __future__ import annotations

import statistics

DEFAULTS = {
    "speed": 1.25, "speed_max": 1.5, "speed_lag_lo_s": 2.0, "speed_lag_hi_s": 6.0, "speed_step": 0.1,
    "speed_round": 0.05, "gap_s": 0.25, "limit_s": 10.0, "skip_s": 20.0, "summary_timeout_s": 4.0,
    "summary_min_words": 12,
}
SKIP_PAUSE_S = 0.6            # the pause a skip leaves (no chime)
SEC_PER_WORD0 = 0.05          # est_gen before any sentence has been generated
SPW_KEEP = 20                 # est_gen = words x the median seconds per word of the last 20 items


def settings(cfg: dict | None) -> dict:
    return {**DEFAULTS, **{k: float(v) for k, v in (cfg or {}).items() if k in DEFAULTS and v is not None}}


def next_speed(prev: float, lag: float, c: dict) -> tuple[float, float]:
    """(speed for this sentence, the unrounded target). 1.25 at <= 2 s behind, 1.5 at >= 6 s; the
    target is rounded to the 0.05 grid and the speed moves at most 0.1 per sentence. A target within
    one grid step of the current speed keeps it, as in the voiced runs (live_sim_pace.next_speed):
    the lag wobbles by a few tenths from sentence to sentence."""
    lo, hi = c["speed"], c["speed_max"]
    span = max(c["speed_lag_hi_s"] - c["speed_lag_lo_s"], 1e-9)
    target = lo + (hi - lo) * min(max((lag - c["speed_lag_lo_s"]) / span, 0.0), 1.0)
    grid = c["speed_round"] or 0.05
    if abs(target - prev) < grid - 1e-9:
        return prev, target
    snapped = round(target / grid) * grid
    speed = min(max(snapped, prev - c["speed_step"]), prev + c["speed_step"])
    return round(min(max(speed, lo), hi), 2), target


class Pace:
    """What one language's stream remembers between sentences."""

    def __init__(self, cfg: dict | None = None):
        self.c = settings(cfg)
        self.speed = self.c["speed"]
        self.spw: list[float] = []            # generation seconds per word, newest last

    def configure(self, cfg: dict | None):
        self.c = settings(cfg)
        self.speed = min(max(self.speed, self.c["speed"]), self.c["speed_max"])

    def est_gen(self, words: int) -> float:
        return words * (statistics.median(self.spw[-SPW_KEEP:]) if self.spw else SEC_PER_WORD0)

    def generated(self, gen_s: float, words: int):
        self.spw = (self.spw + [gen_s / max(1, words)])[-SPW_KEEP:]

    def plan(self, s: dict, now: float, play_free: float, queued: list[dict], summarizer: bool = True) -> dict:
        """The decision for sentence `s` taken up at `now`. `play_free`: when the item playing ends
        (or ended); `queued`: generated items not started yet, each {gen_end, pause_before, audio_s,
        covers: [sentence…]}. Sets and returns the speed; `ask` = the batch to summarize, or None with
        `why` saying why not."""
        c = self.c
        tail = play_free
        for q in queued:
            tail = max(q["gen_end"], tail + q["pause_before"]) + q["audio_s"]
        est_start = max(now + self.est_gen(int(s["words"])), tail + c["gap_s"])
        lag = est_start - s["spk_end"]
        self.speed, target = next_speed(self.speed, lag, c)
        out = {"lag": lag, "target": target, "speed": self.speed, "est_start": est_start, "ask": None, "why": None}
        if lag > c["limit_s"]:
            batch = [x for q in queued for x in q["covers"]] + [s]
            words = sum(len(x["text"].split()) for x in batch)
            if any(x.get("scripture") for x in batch):
                out["why"] = "scripture"
            elif words < c["summary_min_words"]:
                out["why"] = f"too_short ({words} words)"
            elif not summarizer:
                out["why"] = "no_summarizer"
            else:
                out["ask"] = batch
        return out

    def skip(self, lag: float, queued: list[dict]) -> list[dict]:
        """No summary could be had and the sentence is more than skip_s behind: the queued items to drop
        (never Scripture; the sentence in hand, the newest, is kept)."""
        if lag <= self.c["skip_s"]:
            return []
        return [q for q in queued if not any(x.get("scripture") for x in q["covers"])]


def simulate(sents: list[dict], say, summarize=None, cfg: dict | None = None) -> tuple[list, list, list]:
    """The recorded-timings replay of live_sim_pace.simulate, on the functions above. `sents`: sentences
    with n, text, words, spk_end, ready, scripture. `say(text, name, speed)` -> {gen_s, audio_s}.
    `summarize(texts)` -> {ok, text, reason, gemma_s} or None for "no summarizer".
    Returns (played, skipped, requests)."""
    pace = Pace(cfg)
    c = pace.c
    played, queue, skipped, requests = [], [], [], []
    gen_free, play_free = -1e18, -1e18

    def advance(t):
        nonlocal play_free
        while queue:
            it = queue[0]
            s0 = max(it["gen_end"], play_free + it["pause_before"])
            if s0 > t:
                break
            queue.pop(0)
            it["play_start"], it["play_end"] = s0, s0 + it["audio_s"]
            play_free = it["play_end"]
            played.append(it)

    for s in sents:
        t = max(s["ready"], gen_free)
        advance(t)
        d = pace.plan(s, t, play_free, queue, summarizer=summarize is not None)
        base = {"avail": s["ready"], "gen_start": t, "lag_est": d["lag"], "target": d["target"], "speed": d["speed"],
                "pause_before": c["gap_s"], "gemma_s": 0.0}
        item, t_voice = None, t
        if d["lag"] > c["limit_s"]:
            batch = d["ask"] or [x for q in queue for x in q["covers"]] + [s]
            req = {"first": batch[0]["n"], "last": batch[-1]["n"], "lag_est_s": round(d["lag"], 2),
                   "accepted": False, "reason": d["why"]}
            if d["ask"]:
                r = summarize([x["text"] for x in batch])
                took = float(r.get("gemma_s") or 0.0)
                reason = "timeout" if took > c["summary_timeout_s"] else (None if r.get("ok") else r.get("reason"))
                req.update(accepted=reason is None, reason=reason, out=r.get("text"), gemma_s=took)
                t_voice = t + min(took, c["summary_timeout_s"])
                if reason is None:
                    v = say(r["text"], f"sum_{batch[0]['n']:03d}_{batch[-1]['n']:03d}", d["speed"])
                    pace.generated(v["gen_s"], len(r["text"].split()))
                    item = {**base, "kind": "summary", "covers": batch, "text": r["text"], "gemma_s": took,
                            "gen_s": v["gen_s"], "gen_end": t_voice + v["gen_s"], "audio_s": v["audio_s"]}
                    queue.clear()
            requests.append(req)
        if item is None:
            drop = pace.skip(d["lag"], queue)
            if drop:
                for q in drop:
                    queue.remove(q)
                    skipped.append({**q, "kind": "skipped", "skipped_at": t})
                base["pause_before"] = SKIP_PAUSE_S
            v = say(s["text"], f"s{s['n']:03d}", d["speed"])
            pace.generated(v["gen_s"], int(s["words"]))
            item = {**base, "kind": "sentence", "covers": [s], "text": s["text"], "gen_s": v["gen_s"],
                    "gen_end": t_voice + v["gen_s"], "audio_s": v["audio_s"]}
        gen_free = item["gen_end"]
        queue.append(item)
    advance(1e18)
    return played, skipped, requests
