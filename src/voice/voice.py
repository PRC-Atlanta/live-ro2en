"""Live Voice, the voice process (docs/live_voice_plan.md §3, §4, §14). A child of the orchestrator,
like the engine: JSON lines on stdin and stdout.

  .venv-voice/bin/python src/voice/voice.py --cfg '{"port": 8767, ...}' [--log logs/voice/x.jsonl]

in   config · sentence · replace · retract · state · summary · say · stop      (EOF on stdin also ends it)
out  ready · summarize · item · status · warn · error

One generation thread speaks the sentences in order (Supertonic 3 on the CPU); each language has its
own queue, play clock, MP3 encoder and stream (/listen = /listen/ro, /listen/en). The play clocks
never stop: silence is encoded too, so a phone keeps the stream. It only accepts connections; it never
opens one (netaudit)."""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import sys
import threading
import time

# Fully offline: no hub lookups, no telemetry (LIVE_ALLOW_NETWORK=1 is for setup only, never needed here)
if os.environ.get("LIVE_ALLOW_NETWORK") != "1":
    for _k, _v in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("HF_DATASETS_OFFLINE", "1"),
                   ("DO_NOT_TRACK", "1")):
        os.environ[_k] = _v
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))             # the `voice` package (src/voice), before this script's folder
sys.path.append(str(HERE.parent / "live"))        # netaudit, awake: last, so nothing there shadows a library
import netaudit  # noqa: E402
netaudit.install()                 # no-op unless NET_AUDIT_LOG is set (offline tests)

from voice import pacing, speakable, stream  # noqa: E402

FADE_S = 0.3                       # Hide / Song / Blank: the item playing fades out over this
STATUS_EVERY_S = 1.0
NOTIFY_EVERY = 8                   # listeners are sent new audio every 8 frames (0.2 s): 5 writes a second each
LANGS = ("ro", "en")
_OUT = sys.stdout
_EMIT_LOCK = threading.Lock()


def protect_stdout() -> None:
    """Events go to a private copy of stdout; fd 1 is pointed at stderr so library prints cannot
    corrupt the JSON-lines protocol (DECISIONS 2026-10-21)."""
    global _OUT
    _OUT = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    sys.stdout = sys.stderr


def emit(obj) -> None:
    try:
        with _EMIT_LOCK:
            _OUT.write(json.dumps(obj, ensure_ascii=False) + "\n")
            _OUT.flush()
    except (OSError, ValueError):          # the orchestrator is gone: the stdin loop ends the process
        pass


class Item:
    """Something to play: a sentence, a summary of several, or a test line."""
    _n = 0

    def __init__(self, lang: str, kind: str, text: str, covers: list[dict], pcm, gen_s: float, speed: float,
                 pause_before: float, summarize_s: float = 0.0):
        Item._n += 1
        self.n = Item._n
        self.lang, self.kind, self.text, self.covers = lang, kind, text, covers
        self.pcm, self.gen_s, self.speed = pcm, gen_s, speed
        self.audio_s = len(pcm) / stream.SR
        self.pause_before, self.summarize_s = pause_before, summarize_s
        self.gen_end = time.time()
        self.play_start: float | None = None
        self.cut = False
        self.stale: str | None = None          # a replace arrived while it waited: its new text

    @property
    def sids(self) -> list:
        return [c.get("sid") for c in self.covers]

    @property
    def spk_end(self):
        return self.covers[-1].get("spk_end") if self.covers else None

    def view(self) -> dict:
        """What pacing.plan needs of a queued item."""
        return {"gen_end": self.gen_end, "pause_before": self.pause_before, "audio_s": self.audio_s,
                "covers": self.covers}


class Player:
    """One language's play clock: takes the next samples of the item now playing, or silence."""

    def __init__(self, lang: str, on_done):
        self.lang = lang
        self.queue: list[Item] = []
        self.cur: Item | None = None
        self.pos = 0
        self.last_end = -1e18                  # when the last item ended
        self.fade_from: int | None = None      # sample position where a fade-out began
        self.on_done = on_done
        self.lock = threading.Lock()

    def play_free(self, now: float) -> float:
        with self.lock:
            if self.cur is not None:
                return now + (len(self.cur.pcm) - self.pos) / stream.SR
            return self.last_end

    def frame(self, now: float, n: int = stream.FRAME):
        import numpy as np
        out = np.zeros(n, dtype=np.float32)
        done = None
        with self.lock:
            if self.cur is None and self.queue and now >= self.last_end + self.queue[0].pause_before:
                self.cur, self.pos, self.fade_from = self.queue.pop(0), 0, None
                self.cur.play_start = now
            it = self.cur
            if it is not None:
                chunk = it.pcm[self.pos:self.pos + n]
                if self.fade_from is not None:             # fading out: a linear ramp over FADE_S
                    total = int(FADE_S * stream.SR)
                    k = np.arange(self.pos - self.fade_from, self.pos - self.fade_from + len(chunk))
                    chunk = chunk * np.clip(1.0 - k / total, 0.0, 1.0).astype(np.float32)
                out[:len(chunk)] = chunk
                self.pos += len(chunk)
                faded = self.fade_from is not None and self.pos - self.fade_from >= int(FADE_S * stream.SR)
                if self.pos >= len(it.pcm) or faded:
                    it.cut = it.cut or faded
                    it.t_end = now + n / stream.SR
                    self.cur, self.fade_from, done = None, None, it
                    self.last_end = now + n / stream.SR
        if done is not None:
            self.on_done(done, self.pos / stream.SR)
        return out

    def silence(self) -> list[Item]:
        """Hide / Song / Blank: fade the item playing, drop what waits. Returns the dropped items."""
        with self.lock:
            dropped, self.queue = self.queue, []
            if self.cur is not None and self.fade_from is None:
                self.fade_from = self.pos
                self.cur.cut = True
            return dropped


class Voice:
    def __init__(self, cfg: dict, log_path: str | None = None, synth=None):
        self.cfg = dict(cfg)
        self.clock_error: str | None = None
        self.langs = [l for l in LANGS if l == "ro" or (cfg.get("en") or {}).get("enabled", True)]
        self.pace = {l: pacing.Pace(cfg) for l in self.langs}
        self.players = {l: Player(l, self._played) for l in self.langs}
        self.channels = {l: stream.Channel(l, int(cfg.get("bitrate_kbps", 64))) for l in self.langs}
        self.encoders = {}
        self.server: stream.Server | None = None
        self.synth = synth
        self.lk = threading.RLock()                # inbox, state, summary replies
        self.inbox: collections.deque = collections.deque()    # ("sentence", s) | ("regen", item) | ("say", lang, text)
        self.wake = threading.Condition(self.lk)
        self.current: dict | None = None           # the sentence being generated
        self.current_stale: tuple | None = None    # (kind, text) that arrived for it meanwhile
        self.state = {"paused": False, "song": False, "held": False, "running": False, "blank_n": 0,
                      "summarizer": False, "profile": None}
        self.state_seq = 0
        self.hush_n = 0                            # Hide / Song / Blank so far
        self.clock_t = time.time()                 # when the play clock last turned (status: clock_age_s)
        self.played: collections.deque = collections.deque()   # items that ended, for the reporting thread
        self.summaries: dict[int, dict] = {}
        self.req = 0
        self.errors = 0
        self.rtf: collections.deque = collections.deque(maxlen=20)   # (gen_s, audio_s)
        self.stop = threading.Event()
        self.log_path = pathlib.Path(log_path) if log_path else None
        self._log_lock = threading.Lock()
        self._last_status_log = 0.0
        self.counts = collections.Counter()

    # --- log ----------------------------------------------------------------------------------
    def log(self, rec: dict):
        if self.log_path is None:
            return
        try:
            with self._log_lock:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a") as f:
                    f.write(json.dumps({"t": round(time.time(), 3), **rec}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def silenced(self) -> bool:
        return bool(self.state.get("paused") or self.state.get("song"))

    # --- messages from the orchestrator ------------------------------------------------------------
    def handle(self, msg: dict):
        t = msg.get("type")
        if t == "sentence":
            self.on_sentence(msg)
        elif t in ("replace", "retract"):
            self.on_change(t, msg)
        elif t == "state":
            self.on_state(msg)
        elif t == "summary":
            with self.wake:
                self.summaries[int(msg.get("req", -1))] = msg
                self.wake.notify_all()
        elif t == "config":
            self.on_config(msg.get("cfg") or {})
        elif t == "say":
            lang = msg.get("lang") if msg.get("lang") in self.langs else "ro"
            text = str(msg.get("text") or "")[:400]
            if text.strip():
                with self.wake:
                    self.inbox.append(("say", lang, text))
                    self.wake.notify_all()
        elif t == "stop":
            self.stop.set()
            with self.wake:
                self.wake.notify_all()

    def on_config(self, cfg: dict):
        self.cfg.update({k: v for k, v in cfg.items() if k not in ("port", "threads", "voices", "steps", "en",
                                                                   "bitrate_kbps")})
        for p in self.pace.values():
            p.configure(self.cfg)
        if self.server is not None:
            self.server.preroll_s = float(self.cfg.get("preroll_s", 1.0))

    def on_sentence(self, s: dict):
        lang = s.get("lang")
        if lang not in self.langs or not str(s.get("text") or "").strip():
            return
        self.log({"event": "sentence", **{k: v for k, v in s.items() if k != "type"}})
        if self.silenced():
            self.counts["dropped_silenced"] += 1
            self.log({"event": "dropped", "why": "silenced", "sid": s.get("sid")})
            return
        with self.wake:
            self.inbox.append(("sentence", {**s, "words": len(str(s["text"]).split())}))
            self.wake.notify_all()

    def on_change(self, kind: str, msg: dict):
        """replace {sid, text} / retract {sid} (§2.4): not started yet -> dropped or generated again;
        playing or played -> nothing (logged too_late)."""
        sid, text = msg.get("sid"), str(msg.get("text") or "")
        if kind == "replace" and not text.strip():
            kind = "retract"
        where = "too_late"
        with self.wake:
            for i, job in enumerate(self.inbox):
                if job[0] == "sentence" and job[1].get("sid") == sid:
                    if kind == "retract":
                        del self.inbox[i]
                    else:
                        job[1].update(text=text, words=len(text.split()))
                    where = "waiting"
                    break
            else:
                if self.current is not None and self.current.get("sid") == sid:
                    self.current_stale, where = (kind, text), "generating"
                else:
                    for p in self.players.values():
                        with p.lock:
                            it = next((q for q in p.queue if q.kind == "sentence" and q.sids == [sid]), None)
                            if it is None:
                                continue
                            if kind == "retract":
                                p.queue.remove(it)
                            else:
                                pending = it.stale is not None      # a regen job is already waiting for it
                                it.stale = text
                                if not pending:
                                    self.inbox.appendleft(("regen", it))
                            where = "queued"
                            break
            self.wake.notify_all()
        self.counts[f"{kind}_{where}"] += 1
        self.log({"event": kind, "sid": sid, "where": where, **({"text": text} if kind == "replace" else {})})

    def on_state(self, st: dict):
        seq = st.get("seq")
        if isinstance(seq, int):                            # an older state that arrives late changes nothing
            if seq < self.state_seq:
                return
            self.state_seq = seq
        was, blank_was = self.silenced(), int(self.state.get("blank_n") or 0)
        self.state.update({k: st[k] for k in self.state if k in st})
        self.state["blank_n"] = max(blank_was, int(self.state.get("blank_n") or 0))
        blank = self.state["blank_n"] > blank_was          # Blank is one-shot: a counter that only goes up
        if (self.silenced() and not was) or blank:
            self.hush("silenced" if self.silenced() else "blank")

    def hush(self, why: str):
        """Silence now: fade out what plays, drop what waits (Hide, Song, Blank)."""
        with self.wake:
            self.hush_n += 1
            waiting = [j[1] for j in self.inbox if j[0] == "sentence"]
            self.inbox = collections.deque(j for j in self.inbox if j[0] == "say")
            if self.current is not None:
                self.current_stale = ("retract", "")
            dropped = [it for p in self.players.values() for it in p.silence()]
        sids = [s.get("sid") for s in waiting] + [sid for it in dropped for sid in it.sids]
        self.counts["hush"] += 1
        self.log({"event": "hush", "why": why, "dropped_sids": sids})

    # --- generation -------------------------------------------------------------------------------
    def say(self, text: str, lang: str, speed: float):
        t0 = time.perf_counter()
        pcm = self.synth.say(speakable.speakable(text, lang), lang, speed)
        return pcm, time.perf_counter() - t0

    def gen_loop(self):
        while not self.stop.is_set():
            with self.wake:
                while not self.inbox and not self.stop.is_set():
                    self.wake.wait(0.5)
                if self.stop.is_set():
                    return
                job = self.inbox.popleft()
                self.current = job[1] if job[0] == "sentence" else None
                self.current_stale = None
            try:
                if job[0] == "sentence":
                    self.take_up(job[1])
                elif job[0] == "regen":
                    self.regen(job[1])
                elif job[0] == "say":
                    pcm, gen_s = self.say(job[2], job[1], self.pace[job[1]].c["speed"])
                    self.enqueue(Item(job[1], "test", job[2], [], pcm, gen_s, self.pace[job[1]].c["speed"],
                                      self.pace[job[1]].c["gap_s"]))
            except Exception as e:                          # noqa: BLE001  one sentence never stops the voice
                self.errors += 1
                emit({"type": "error", "where": "generate", "msg": f"{type(e).__name__}: {e}"[:300]})
                self.log({"event": "error", "where": "generate", "msg": f"{type(e).__name__}: {e}"})
            finally:
                with self.wake:
                    self.current = None

    def enqueue(self, it: Item):
        if it.kind != "test" and self.silenced():
            return
        it.gen_end = time.time()
        p = self.players[it.lang]
        with p.lock:
            p.queue.append(it)

    def regen(self, it: Item):
        """A queued sentence whose text was replaced: spoken again, in the same place in the queue. If
        another replace arrived while it was spoken, the newest text wins."""
        p = self.players[it.lang]
        for _ in range(4):
            with p.lock:
                text = it.stale
                if text is None or it not in p.queue:
                    return
            pcm, gen_s = self.say(text, it.lang, it.speed)
            with p.lock:
                if it not in p.queue:                       # it started playing, or was dropped, meanwhile
                    return
                if it.stale != text:
                    continue
                it.text, it.pcm, it.gen_s, it.stale = text, pcm, gen_s, None
                it.audio_s = len(pcm) / stream.SR
                it.covers = [{**it.covers[0], "text": text}] if it.covers else it.covers
                return

    def ask_summary(self, lang: str, batch: list[dict]) -> dict:
        """Ask the orchestrator to condense `batch`; {ok, text, reason, gemma_s}. Gives up after
        summary_timeout_s (§4.3): the sentence is then spoken normally."""
        with self.wake:
            self.req += 1
            req = self.req
        timeout = float(self.pace[lang].c["summary_timeout_s"])
        t0 = time.time()
        emit({"type": "summarize", "req": req, "lang": lang, "sids": [x.get("sid") for x in batch],
              "texts": [x["text"] for x in batch]})
        with self.wake:
            while req not in self.summaries and time.time() - t0 < timeout and not self.stop.is_set():
                self.wake.wait(timeout - (time.time() - t0) + 0.01)
            r = self.summaries.pop(req, None)
            self.summaries.clear()
        took = time.time() - t0
        if r is None:
            return {"ok": False, "reason": "timeout", "took_s": took}
        return {"ok": bool(r.get("ok")) and bool(str(r.get("text") or "").strip()), "text": r.get("text"),
                "reason": r.get("reason"), "gemma_s": r.get("gemma_s"), "took_s": took}

    def take_up(self, s: dict):
        lang = s["lang"]
        pace, player = self.pace[lang], self.players[lang]
        now = time.time()
        hush_n = self.hush_n                                # Hide, Song or Blank from here on drops this sentence
        with self.wake:
            newer = sum(1 for j in self.inbox if j[0] == "sentence" and j[1].get("lang") == lang)
        if newer and not s.get("scripture") and s.get("spk_end") and now - s["spk_end"] > 2 * pace.c["skip_s"]:
            # generation itself is far behind (slower than real time): not worth generating any more
            self.counts["skips"] += 1
            self.counts["sentences_skipped"] += 1
            self.log({"event": "skipped", "lang": lang, "lag_est_s": round(now - s["spk_end"], 2),
                      "sids": [s.get("sid")], "why": "not generated"})
            return
        with player.lock:
            queued = list(player.queue)
        d = pace.plan(s, now, player.play_free(now), [q.view() for q in queued],
                      summarizer=bool(self.state.get("summarizer")))
        pause, item, summarize_s = pace.c["gap_s"], None, 0.0
        if d["lag"] > pace.c["limit_s"]:
            rec = {"event": "summarize", "lang": lang, "lag_est_s": round(d["lag"], 2), "speed": d["speed"],
                   "sids": [x.get("sid") for q in queued for x in q.covers] + [s.get("sid")]}
            if d["ask"]:
                r = self.ask_summary(lang, d["ask"])
                summarize_s = r["took_s"]
                rec.update(accepted=r["ok"], reason=r.get("reason"), out=r.get("text"), gemma_s=r.get("gemma_s"),
                           took_s=round(summarize_s, 3))
                if r["ok"] and not self._stale_now() and hush_n == self.hush_n:
                    pcm, gen_s = self.say(r["text"], lang, d["speed"])
                    pace.generated(gen_s, len(r["text"].split()))
                    self.rtf.append((gen_s, len(pcm) / stream.SR))
                    with player.lock:
                        # The summary replaces the items it covers. If one of them started playing while
                        # the summary was written (up to summary_timeout_s), the summary is dropped: that
                        # sentence would be heard twice. The simulation cleared the queue at once.
                        if all(q in player.queue and q.stale is None for q in queued):   # none started, removed or replaced
                            player.queue = [q for q in player.queue if q not in queued]
                            item = Item(lang, "summary", r["text"], d["ask"], pcm, gen_s, d["speed"], pause, summarize_s)
                    if item is not None:
                        self.counts["summaries"] += 1
                    else:
                        rec.update(accepted=False, reason="started_meanwhile")
                        self.counts["summaries_late"] += 1
                else:
                    self.counts["summaries_refused"] += 1
            else:
                rec.update(accepted=False, reason=d["why"])
                self.counts["summaries_not_asked"] += 1
            self.log(rec)
        if item is None:
            with player.lock:
                drop = pace.skip(d["lag"], [q.view() for q in player.queue])
                if drop:
                    gone = [q for q in player.queue if not any(c.get("scripture") for c in q.covers)]
                    player.queue = [q for q in player.queue if q not in gone]
                else:
                    gone = []
            if gone:
                pause = pacing.SKIP_PAUSE_S
                self.counts["skips"] += 1
                self.counts["sentences_skipped"] += sum(len(q.covers) for q in gone)
                self.log({"event": "skipped", "lang": lang, "lag_est_s": round(d["lag"], 2),
                          "sids": [sid for q in gone for sid in q.sids]})
            text, pcm, gen_s = s["text"], None, 0.0
            for _ in range(4):                              # spoken again while corrections keep arriving
                stale = self._stale_now()
                if stale and stale[0] == "retract":
                    return
                want = stale[1] if stale else text
                if pcm is not None and want == text:
                    break
                text = want
                pcm, gen_s = self.say(text, lang, d["speed"])
            pace.generated(gen_s, len(text.split()))
            self.rtf.append((gen_s, len(pcm) / stream.SR))
            item = Item(lang, "sentence", text, [{**s, "text": text}], pcm, gen_s, d["speed"], pause, summarize_s)
        item.lag_est, item.target = d["lag"], d["target"]
        with self.wake:                                     # one step with on_change and hush: a correction or
            stale = self.current_stale                      # a Blank is either seen here or finds the item queued
            if hush_n != self.hush_n or (stale and stale[0] == "retract"):
                return
            if stale and item.kind == "sentence" and stale[1] != item.text:
                item.stale = stale[1]
                self.inbox.appendleft(("regen", item))
            self.current = None
            self.enqueue(item)

    def _stale_now(self):
        with self.wake:
            return self.current_stale

    # --- play clocks and streams --------------------------------------------------------------------
    def _played(self, it: Item, played_s: float):
        """Called by the play clock when an item ends: only noted here. Writing to the orchestrator and
        to the log happens on another thread, so a slow pipe or disk never stalls the audio."""
        self.played.append((it, played_s))

    def report_loop(self):
        while not self.stop.wait(0.05):
            while self.played:
                it, played_s = self.played.popleft()
                try:
                    self._report(it, played_s)
                except Exception:                           # noqa: BLE001
                    self.errors += 1

    def _report(self, it: Item, played_s: float):
        delay = (it.play_start - it.spk_end) if it.play_start and it.spk_end else None
        rec = {"lang": it.lang, "kind": it.kind, "sids": it.sids, "gen_s": round(it.gen_s, 3),
               "audio_s": round(it.audio_s, 3), "play_start": round(it.play_start or 0.0, 3),
               "delay_after_end_s": round(delay, 3) if delay is not None else None, "cut": it.cut,
               "speed": it.speed, "summarize_s": round(it.summarize_s, 3), "words": len(it.text.split()),
               "pause_before": it.pause_before, "scripture": any(c.get("scripture") for c in it.covers)}
        if it.kind != "test":
            self.counts["items"] += 1
        emit({"type": "item", **rec})
        self.log({"event": "item", **rec, "played_s": round(played_s, 3), "text": it.text,
                  "lag_est_s": round(getattr(it, "lag_est", 0.0), 2), "gen_end": round(it.gen_end, 3),
                  "t_end": round(getattr(it, "t_end", time.time()), 3)})

    def clock_loop(self):
        """Real time, one MP3 frame (26 ms) at a time, for every language; silence is encoded too. Paced
        by the monotonic clock (a wall-clock correction must not make a burst or a gap); items are stamped
        with wall time. A frame that fails is replaced by nothing and counted: the clock never dies."""
        frame_s = stream.FRAME / stream.SR
        t0, n = time.monotonic(), 0
        while not self.stop.is_set():
            due = t0 + n * frame_s
            now = time.monotonic()
            if now < due:
                time.sleep(min(due - now, 0.05))
                continue
            if now - due > 2.0:                             # the process was stalled: do not burst 2 s of audio
                t0, n = now, 0
                due = now
            wall = time.time() - (now - due)
            for lang in self.langs:
                try:
                    pcm = self.players[lang].frame(wall)
                    data = self.encoders[lang].encode(pcm)
                    self.channels[lang].put(data, stream.FRAME)
                    if data and self.server is not None and n % NOTIFY_EVERY == 0:
                        self.server.notify(lang)
                except Exception as e:                      # noqa: BLE001
                    self.errors += 1
                    self.clock_error = f"{type(e).__name__}: {e}"[:200]
            self.clock_t = time.time()
            n += 1

    # --- status ---------------------------------------------------------------------------------------
    def status(self) -> dict:
        now = time.time()
        langs = {}
        with self.wake:
            waiting = [j[1] for j in self.inbox if j[0] == "sentence"]
        for lang in self.langs:
            p, ch = self.players[lang], self.channels[lang]
            with p.lock:
                cur, queue = p.cur, list(p.queue)
            behind = None
            if cur is not None and cur.spk_end and cur.play_start:
                behind = cur.play_start - cur.spk_end
            else:
                nxt = queue[0].spk_end if queue and queue[0].spk_end else next(
                    (s.get("spk_end") for s in waiting if s.get("lang") == lang), None)
                if nxt:
                    behind = now - nxt
            langs[lang] = {"listeners": ch.listeners, "peak": ch.peak,
                           "queue_n": len(queue) + sum(1 for s in waiting if s.get("lang") == lang),
                           "queue_s": round(sum(q.audio_s for q in queue), 1),
                           "behind_s": round(behind, 1) if behind is not None else None,
                           "playing_sid": (cur.sids[0] if cur is not None and cur.sids else None),
                           "speed": self.pace[lang].speed}
        gen, aud = sum(g for g, _ in self.rtf), sum(a for _, a in self.rtf)
        behind = [v["behind_s"] for v in langs.values() if v["behind_s"] is not None]
        return {"type": "status", "listeners": sum(v["listeners"] for v in langs.values()),
                "queue_n": sum(v["queue_n"] for v in langs.values()),
                "queue_s": round(sum(v["queue_s"] for v in langs.values()), 1),
                "behind_s": max(behind) if behind else None,
                "playing_sid": next((v["playing_sid"] for v in langs.values() if v["playing_sid"] is not None), None),
                "rtf_recent": round(gen / aud, 3) if aud else None, "errors": self.errors, "langs": langs,
                "clock_age_s": round(now - self.clock_t, 2),
                "counts": dict(self.counts)}

    def status_loop(self):
        while not self.stop.wait(STATUS_EVERY_S):
            st = self.status()
            emit(st)
            if time.time() - self._last_status_log >= 10.0:
                self._last_status_log = time.time()
                self.log({"event": "status", **{k: v for k, v in st.items() if k != "type"}})

    # --- start ---------------------------------------------------------------------------------------
    def start(self) -> bool:
        cfg = self.cfg
        if self.synth is None:
            from voice import synth
            self.synth = synth.Synth(threads=int(cfg.get("threads", 4)),
                                     voices={l: (cfg.get("voices") or {}).get(l, "M2") for l in self.langs},
                                     steps=int(cfg.get("steps", 8)))
            self.synth.warm_up(self.langs)
        self.encoders = {l: stream.Encoder(int(cfg.get("bitrate_kbps", 64))) for l in self.langs}
        self.server = stream.Server(self.channels, host=str(cfg.get("host", "0.0.0.0")), port=int(cfg.get("port", 8767)),
                                    preroll_s=float(cfg.get("preroll_s", 1.0)),
                                    max_clients=int(cfg.get("max_clients", 300)),
                                    per_addr=int(cfg.get("per_addr", 150)),
                                    rehearsal=bool(cfg.get("rehearsal", False)), log=self.log)
        if not self.server.start():
            emit({"type": "error", "where": "listen", "msg": f"port {cfg.get('port', 8767)}: {self.server.error}"})
            return False
        for fn, name in ((self.clock_loop, "voice-clock"), (self.gen_loop, "voice-gen"),
                         (self.status_loop, "voice-status"), (self.report_loop, "voice-report")):
            threading.Thread(target=fn, daemon=True, name=name).start()
        self.log({"event": "start", "port": self.server.port, "langs": self.langs,
                  "cfg": {k: v for k, v in cfg.items() if k != "log"}})
        emit({"type": "ready", "port": self.server.port, "langs": self.langs})
        return True


def stdin_loop(voice: Voice, stdin) -> None:
    """One JSON object per line; EOF (the orchestrator is gone) ends the process, as `stop` does."""
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if isinstance(msg, dict):
            try:
                voice.handle(msg)
            except Exception as e:                          # noqa: BLE001
                emit({"type": "error", "where": "message", "msg": f"{type(e).__name__}: {e}"[:300]})
        if voice.stop.is_set():
            return
    voice.stop.set()


def main(argv=None, make_synth=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfg", default="{}", help="the voice: settings as JSON")
    ap.add_argument("--log", help="the voice log (jsonl)")
    a = ap.parse_args(argv)
    protect_stdout()
    try:
        cfg = json.loads(a.cfg)
    except ValueError:
        cfg = {}
    try:
        import awake
        awake.hold("Live RO2EN voice")         # no App Nap: a napped play clock would stutter
    except Exception:                          # noqa: BLE001
        pass
    voice = Voice(cfg, a.log, synth=make_synth() if make_synth else None)
    try:
        ok = voice.start()
    except Exception as e:                     # noqa: BLE001  the model is missing or does not load
        emit({"type": "error", "where": "start", "msg": f"{type(e).__name__}: {e}"[:300]})
        return 3
    if not ok:
        return 4
    ppid = os.getppid()

    def parent_watch():
        while not voice.stop.wait(0.5):
            if os.getppid() != ppid:
                voice.stop.set()
    threading.Thread(target=parent_watch, daemon=True, name="voice-parent").start()
    threading.Thread(target=stdin_loop, args=(voice, sys.stdin), daemon=True, name="voice-stdin").start()
    voice.stop.wait()
    voice.log({"event": "stop", "counts": dict(voice.counts)})
    if voice.server is not None:
        voice.server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
