"""Live Voice in the orchestrator (docs/live_voice_plan.md §3, §5): start the voice process
(src/voice/voice.py) when Live Voice is on, keep it in step with the operator, answer its summary
requests, restart it when it dies or hangs, and tell the operator how it is doing.

The voice is never allowed to hurt the captions: every call here is best effort, and a voice that is
down only turns the VOICE light red. Mixed into live.app.Live."""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import queue
import socket
import statistics
import subprocess
import sys
import threading
import time

import paths
import summarize

HERE = pathlib.Path(__file__).resolve().parent
VOICE_SCRIPT = HERE.parent / "voice" / "voice.py"      # tests point this at a voice without a model
RESTARTS, RESTART_WINDOW_S = 3, 120.0                  # the shell's rule (ProcessSupervisor.swift)
HANG_S = 5.0                                           # no status for this long: restarted
START_GRACE_S = 60.0                                   # the model loads and warms up before `ready`
STATE_EVERY_S = 1.0
BEHIND_S = 10.0                                        # the light turns amber past this
LOG_MAX = 20_000_000
SENT_KEYS = ("enabled", "en", "voices", "steps", "threads", "port", "bitrate_kbps", "preroll_s", "gap_s", "speed", "per_addr",
             "speed_max", "speed_lag_lo_s", "speed_lag_hi_s", "speed_step", "speed_round", "limit_s", "skip_s",
             "summary_timeout_s", "summary_min_words", "rehearsal", "max_clients", "host")


def lan_address() -> str | None:
    """This Mac's address on the local network (what a phone types), without sending anything: a UDP
    socket is only pointed at a private address to learn which interface the system would use."""
    for probe in ("10.255.255.255", "192.168.255.255", "172.31.255.255"):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((probe, 9))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            pass
        finally:
            s.close()
    return None


class VoiceMixin:
    def _mixin_init(self):
        self.voice: subprocess.Popen | None = None
        self._voice_lock = threading.RLock()           # start / stop / restart
        self._voice_out: queue.Queue = queue.Queue(maxsize=2000)   # messages for its stdin (never blocks a caption)
        self._voice_writer: threading.Thread | None = None
        self._voice_dropped = 0                        # messages dropped because it was not reading
        self._voice_seq = 0                            # state messages are numbered: an old one is ignored
        self._voice_state_lock = threading.Lock()
        self._voice_cfg_lock = threading.Lock()        # one settings change at a time
        self._voice_ready = False
        self._voice_port: int | None = None
        self._voice_status: dict = {}
        self._voice_status_t = 0.0
        self._voice_started = 0.0
        self._voice_restarts: collections.deque = collections.deque()
        self._voice_dead: str | None = None            # stopped for good: voice_stopped
        self._voice_error: str | None = None           # the last error it reported
        self._voice_stopping = False
        self._voice_thread: threading.Thread | None = None
        self._voice_tick: threading.Thread | None = None
        self._voice_blank_n = 0
        self._voice_items: list[dict] = []             # this service's played items (After)
        self._voice_counts: collections.Counter = collections.Counter()
        self._voice_peak: dict = {}
        self._voice_summ_lock = threading.Lock()       # one summary at a time
        self._voice_lan: tuple[float, str | None] = (0.0, None)
        self.register_state(self._voice_state)

    # --- settings -----------------------------------------------------------------------------
    def _voice_cfg_msg(self) -> dict:
        v = self.voice_cfg()
        return {k: v[k] for k in SENT_KEYS if k in v}

    def voice_model_ready(self) -> bool:
        try:
            sys.path.insert(0, str(HERE.parent)) if str(HERE.parent) not in sys.path else None
            from voice import model
            voices = tuple(set((self.voice_cfg().get("voices") or {"ro": "M2"}).values()))
            return model.model_dir(voices) is not None
        except Exception:                               # noqa: BLE001
            return False

    # --- start, stop, restart --------------------------------------------------------------------
    def voice_start(self) -> bool:
        """Start the voice process if Live Voice is on and it is not running. Called when the app opens
        (not at Start: phones connect and hear silence before the service), and when it is turned on."""
        with self._voice_lock:
            if not self.voice_enabled() or getattr(self, "_shutting_down", False):
                return False
            if self.voice is not None and self.voice.poll() is None:
                return True
            if not self.voice_model_ready():
                self._voice_dead = "the voice model is not downloaded"
                self._voice_error = self._voice_dead
                return False
            return self._voice_spawn()

    def _voice_spawn(self) -> bool:
        py = paths.voice_python(self.root)
        logs = paths.voice_log_dir(self.root)
        try:
            logs.mkdir(parents=True, exist_ok=True)
            vlog = logs / f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
            err_path = paths.log_dir(self.root) / "voice.log"
            try:
                if err_path.stat().st_size > LOG_MAX:
                    err_path.replace(err_path.with_name("voice.old.log"))
            except OSError:
                pass
            cmd = [str(py), str(VOICE_SCRIPT), "--cfg", json.dumps(self._voice_cfg_msg()), "--log", str(vlog)]
            with open(err_path, "a") as err:
                self.voice = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                              text=True, bufsize=1, env=paths.voice_env())
        except Exception as e:                          # noqa: BLE001  no interpreter, no log folder…
            self.voice = None
            self._voice_dead = self._voice_error = f"the voice could not start: {type(e).__name__}: {e}"
            return False
        self._voice_ready, self._voice_stopping = False, False
        self._voice_out = queue.Queue(maxsize=2000)    # nothing meant for the last process reaches this one
        self._voice_writer = threading.Thread(target=self._voice_write_loop, args=(self.voice, self._voice_out),
                                              daemon=True, name="voice-writer")
        self._voice_writer.start()
        self._voice_started = time.time()
        self._voice_status, self._voice_status_t = {}, 0.0
        self._voice_dead = None
        self._voice_log = vlog
        self._voice_write_pid(self.voice)
        self._voice_thread = threading.Thread(target=self._voice_reader, args=(self.voice,), daemon=True,
                                              name="voice-reader")
        self._voice_thread.start()
        if self._voice_tick is None or not self._voice_tick.is_alive():
            self._voice_tick = threading.Thread(target=self._voice_loop, daemon=True, name="voice-state")
            self._voice_tick.start()
        self._voice_state_now()
        return True

    def voice_stop(self, wait_s: float = 3.0):
        """End the voice process (Quit, or Live Voice turned off)."""
        with self._voice_lock:
            v, self._voice_stopping = self.voice, True
            if v is None:
                return
            if v.poll() is None:
                self._voice_send({"type": "stop"})
                self._voice_send(None)                  # the writer closes its stdin after that: EOF also ends it
                try:
                    v.wait(wait_s)
                except Exception:                       # noqa: BLE001
                    v.kill()
            self.voice = None
            self._voice_ready = False
            self._voice_drop_pid()

    def voice_wait_ready(self, timeout: float = 90.0) -> bool:
        end = time.time() + timeout
        while time.time() < end and not self._voice_ready and self.voice is not None:
            time.sleep(0.1)
        return self._voice_ready

    def voice_drain(self, timeout: float = 180.0) -> bool:
        """Wait until the voice has spoken everything it was sent (a bench replay ends with it)."""
        if self.voice is None or not self._voice_ready:
            return False
        end, quiet = time.time() + timeout, 0
        while time.time() < end and self.voice is not None and self.voice.poll() is None:
            st = self._voice_status
            idle = not st.get("queue_n") and st.get("playing_sid") is None \
                and not any(b.parts for b in self._speech_builders.values()) and not self._speech_q
            quiet = quiet + 1 if idle else 0
            if quiet >= 4:                              # idle over three status reports in a row
                return True
            time.sleep(1.0)
        return False

    def _voice_write_pid(self, v):
        d = getattr(self, "_pid_dir", None)
        if d is None or not getattr(v, "pid", None):
            return
        try:
            (d / f"voice-{os.getpid()}.pid").write_text(f"{v.pid} {os.getpid()}\n")
        except OSError:
            pass

    def _voice_drop_pid(self):
        d = getattr(self, "_pid_dir", None)
        if d is not None:
            try:
                (d / f"voice-{os.getpid()}.pid").unlink()
            except OSError:
                pass

    def _voice_restart(self, why: str, proc=None) -> bool:
        """Process `proc` died or hung: start the voice again, at most RESTARTS times per
        RESTART_WINDOW_S (then voice_stopped until the operator turns Live Voice off and on, or the app
        restarts). A process that was already replaced is nobody's business any more: the reader of a
        process the hang check killed must not restart its successor."""
        with self._voice_lock:
            if self._voice_stopping or getattr(self, "_shutting_down", False) or not self.voice_enabled():
                return False
            if proc is not None and proc is not self.voice:
                return False
            v = self.voice
            if v is not None and v.poll() is None:
                try:
                    v.kill()
                    v.wait(2)
                except Exception:                       # noqa: BLE001
                    pass
            now = time.monotonic()
            while self._voice_restarts and now - self._voice_restarts[0] > RESTART_WINDOW_S:
                self._voice_restarts.popleft()
            if len(self._voice_restarts) >= RESTARTS:
                self.voice, self._voice_ready = None, False
                self._voice_dead = f"the voice stopped ({why}) and was not restarted again"
                self._voice_event({"event": "voice_stopped", "why": why})
                self._broadcast()
                return False
            self._voice_restarts.append(now)
            self._voice_event({"event": "voice_restart", "why": why})
            ok = self._voice_spawn()
            self._broadcast()
            return ok

    def _voice_event(self, rec: dict):
        """Into the service log. Played items and summaries also after Stop: the voice finishes its queue
        (§2.4), and those items belong to the service that just ended."""
        late = rec.get("event") in ("voice_item", "voice_summary") and getattr(self, "_ran", True)
        if (getattr(self, "running", False) or late) and getattr(self, "log_path", None):
            try:
                self._log_event(rec)
            except Exception:                           # noqa: BLE001
                pass

    # --- to the voice ---------------------------------------------------------------------------
    def _voice_send(self, msg: dict | None) -> bool:
        """Hand a message to the writer thread. Never blocks: the pipeline, the operator's actions and
        the screen's lock call this, and a voice that has stopped reading must not hold any of them. When
        2000 messages wait (it is frozen; the hang check restarts it) the message is dropped and counted."""
        v = self.voice
        if v is None or v.poll() is not None:
            return False
        try:
            self._voice_out.put_nowait(msg)
            return True
        except queue.Full:
            self._voice_dropped += 1
            return False

    def _voice_write_loop(self, v: subprocess.Popen, q: queue.Queue):
        """The only writer on one voice process's stdin; ends with the process or at a None."""
        while v.poll() is None:
            try:
                msg = q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                if msg is None:
                    v.stdin.close()
                    return
                v.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
                v.stdin.flush()
            except (OSError, ValueError, AttributeError):
                return

    def _voice_summarizer(self):
        """The model that writes summaries now, or None: Gemma when one is loaded (the live translator's
        member or a spare). voice_ai.py puts Claude in front of it in AI mode."""
        import translate
        pool = list(translate.members(self.tr, self.lc).values()) if getattr(self, "tr", None) is not None else []
        try:
            pool += list((getattr(self, "_spare_tr", None) or {}).values())
        except RuntimeError:
            pass
        tr = next((t for t in pool if type(t).__name__ == "MLXLLMTranslator" and hasattr(t, "model")), None)
        return summarize.GemmaSummarizer(tr) if tr is not None else None

    def _voice_full_state(self) -> dict:
        self._voice_seq += 1
        return {"type": "state", "seq": self._voice_seq,
                "paused": bool(getattr(self, "paused", False)), "song": bool(getattr(self, "song_on", False)),
                "held": bool(getattr(self, "held", False)), "running": bool(getattr(self, "running", False)),
                "blank_n": self._voice_blank_n, "profile": getattr(self, "profile", None),
                "summarizer": self._voice_can_summarize()}

    def _voice_can_summarize(self) -> bool:
        try:
            return self._voice_summarizer() is not None
        except Exception:                               # noqa: BLE001
            return False

    def _voice_state_now(self, blank: bool = False):
        """The whole state, now (an operator action) and every second (so a restarted voice is in step)."""
        with self._voice_state_lock:                    # built and queued in one step: states arrive in order
            if blank:
                self._voice_blank_n += 1
            self._voice_send(self._voice_full_state())

    def voice_test(self, lang: str = "ro") -> bool:
        """Settings → Test voice: one sentence into the stream."""
        text = {"ro": "Aceasta este o probă a traducerii audio. Dacă auziți aceste cuvinte, totul funcționează.",
                "en": "This is a test of the audio translation. If you can hear these words, everything works."}
        lang = lang if lang in text else "ro"
        return self._voice_send({"type": "say", "lang": lang, "text": text[lang]})

    # --- from the voice ---------------------------------------------------------------------------
    def _voice_reader(self, v: subprocess.Popen):
        for line in v.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict) or v is not self.voice:
                continue
            try:
                self._voice_on(ev)
            except Exception:                           # noqa: BLE001
                self._state_failed(self._voice_on)
        rc = v.wait()
        if v is not self.voice or self._voice_stopping or getattr(self, "_shutting_down", False):
            return
        self._voice_ready = False
        self._voice_restart(f"exit {rc}", v)

    def _voice_on(self, ev: dict):
        t = ev.get("type")
        if t == "status":
            self._voice_status, self._voice_status_t = ev, time.time()
            for lang, st in (ev.get("langs") or {}).items():
                self._voice_peak[lang] = max(self._voice_peak.get(lang, 0), int(st.get("listeners") or 0))
        elif t == "ready":
            self._voice_ready, self._voice_port = True, ev.get("port")
            self._voice_status_t = time.time()
            self._voice_error = None
            self._voice_send({"type": "config", "cfg": self._voice_cfg_msg()})
            self._voice_state_now()
            self._broadcast()
        elif t == "item":
            if ev.get("kind") != "test":
                self._voice_items.append(ev)
                self._voice_event({"event": "voice_item", **{k: v for k, v in ev.items() if k != "type"}})
        elif t == "summarize":
            threading.Thread(target=self._voice_summarize, args=(ev,), daemon=True, name="voice-summary").start()
        elif t == "error":
            self._voice_error = f"{ev.get('where', 'voice')}: {ev.get('msg', '')}"
            self._voice_event({"event": "voice_error", "where": ev.get("where"), "msg": ev.get("msg")})
        elif t == "warn":
            self._voice_event({"event": "voice_warn", "msg": ev.get("msg")})

    def _voice_summarize(self, ev: dict):
        """A catch-up summary on the orchestrator's model. Captions come first: it runs between two
        phrases under `_proc_lock`, the way the keep-warm ping does, and lets a waiting phrase go first.
        The voice gives up by itself after summary_timeout_s."""
        req, lang = ev.get("req"), ev.get("lang") if ev.get("lang") in ("ro", "en") else "ro"
        texts = [str(t) for t in (ev.get("texts") or []) if str(t).strip()][:40]
        timeout = float(self.voice_cfg().get("summary_timeout_s", 4.0))
        t0, res = time.time(), None
        holy = getattr(self, "_speech_holy", None) or {}
        try:
            if any(holy.get(sid) for sid in ev.get("sids") or []):
                # Scripture is never condensed: the orchestrator checks its own record of the sentences
                # it published, whatever the voice asked for
                res = {"ok": False, "reason": "scripture", "text": "", "gemma_s": 0.0, "engine": None}
            else:
                res = self._voice_summary(texts, lang, t0 + timeout)
                res = self._voice_summary_safe(res)
        except Exception as e:                          # noqa: BLE001
            res = {"ok": False, "reason": f"error ({type(e).__name__})", "text": "", "gemma_s": 0.0}
        self._voice_counts["summaries_ok" if res.get("ok") else "summaries_refused"] += 1
        self._voice_send({"type": "summary", "req": req, **res})
        self._voice_event({"event": "voice_summary", "req": req, "lang": lang, "sids": ev.get("sids"),
                           "in": texts, "took_s": round(time.time() - t0, 3), **res})

    def _voice_summary(self, texts: list[str], lang: str, deadline: float) -> dict:
        """{ok, text, reason, gemma_s, engine}; overridden in AI mode (voice_ai.py)."""
        return self._voice_summary_local(texts, lang, deadline)

    def _voice_summary_safe(self, res: dict) -> dict:
        """What a summary may never bring in: a word the blocklist hides on screen. The model writes new
        text from speech in the room; anything it adds that the screen would mask refuses the summary."""
        if res.get("ok") and hasattr(self, "_blocklist"):
            try:
                import pipeline
                if pipeline.mask(res.get("text") or "", self._blocklist())[1]:
                    return {**res, "ok": False, "reason": "blocklist"}
            except Exception:                           # noqa: BLE001
                return {**res, "ok": False, "reason": "blocklist_check_failed"}
        return res

    def _voice_summary_local(self, texts: list[str], lang: str, deadline: float) -> dict:
        why = summarize.refuse(texts, False)
        if why:
            return {"ok": False, "reason": why, "text": "", "gemma_s": 0.0, "engine": None}
        with self._voice_summ_lock:
            # It starts only while enough time is left for the voice to still be waiting (it gives up at
            # the deadline): a summary nobody takes would hold the translator for nothing.
            while time.time() < deadline - summarize.MIN_TIME_LEFT_S:
                idle = (getattr(self, "_current", None) is None and getattr(self, "_pending", None) is None
                        and self._work.empty())
                if idle and self._proc_lock.acquire(timeout=0.05):
                    try:
                        gen = self._voice_summarizer()
                        if gen is None:
                            return summarize.summarize(None, texts, lang)
                        with self._spare_lock:          # the keep-warm ping must not run on this model now
                            self._tr_used[id(gen.tr)] = time.time()
                            res = summarize.summarize(gen, texts, lang)
                            self._tr_used[id(gen.tr)] = time.time()
                        return res
                    finally:
                        self._proc_lock.release()
                time.sleep(0.05)
        return {"ok": False, "reason": "busy", "text": "", "gemma_s": 0.0, "engine": None}

    # --- supervision ------------------------------------------------------------------------------
    def _voice_loop(self):
        """Every second: the whole state to the voice; restart it when it hangs (no status for HANG_S
        once it was ready, or no `ready` within START_GRACE_S)."""
        while not getattr(self, "_shutting_down", False):
            time.sleep(STATE_EVERY_S)
            v = self.voice
            if v is None or self._voice_stopping:
                continue
            try:
                self._voice_state_now()
                now = time.time()
                if v.poll() is None:
                    age = self._voice_status.get("clock_age_s")
                    if self._voice_ready and now - self._voice_status_t > HANG_S:
                        self._voice_restart("no status", v)
                    elif self._voice_ready and isinstance(age, (int, float)) and age > HANG_S:
                        self._voice_restart("the play clock stopped", v)   # it reports, but plays nothing
                    elif not self._voice_ready and now - self._voice_started > START_GRACE_S:
                        self._voice_restart("did not become ready", v)
            except Exception:                           # noqa: BLE001
                self._state_failed(self._voice_loop)

    # --- what the operator sees ---------------------------------------------------------------------
    def voice_url(self) -> str | None:
        """The address phones open: this Mac's address on the network and the voice port."""
        t, ip = self._voice_lan
        if time.time() - t > 30:
            ip = lan_address()
            self._voice_lan = (time.time(), ip)
        port = self._voice_port or int(self.voice_cfg().get("port", 8767))
        return f"http://{ip}:{port}/" if ip else None

    def _voice_retry_when_model_arrives(self):
        """Live Voice is on but its model was missing: once it is downloaded, start (looked at most every 5 s)."""
        now = time.time()
        if now - getattr(self, "_voice_model_look", 0.0) < 5.0:
            return
        self._voice_model_look = now
        if self.voice is None and self._voice_dead and "not downloaded" in self._voice_dead and self.voice_model_ready():
            self._voice_dead = None
            threading.Thread(target=self._voice_apply, args=(False, True), daemon=True, name="voice-start").start()

    def _voice_light(self) -> tuple[str, float | None]:
        """(off | starting | ready | speaking | behind | stopped, seconds behind)."""
        if not self.voice_enabled():
            return "off", None
        v = self.voice
        if self._voice_dead or v is None or v.poll() is not None:
            return "stopped", None
        if not self._voice_ready:
            return "starting", None
        st = self._voice_status
        behind = st.get("behind_s")
        if isinstance(behind, (int, float)) and behind > BEHIND_S:
            return "behind", round(float(behind), 1)
        return ("speaking" if st.get("playing_sid") is not None or st.get("queue_n") else "ready"), \
            (round(float(behind), 1) if isinstance(behind, (int, float)) else None)

    def voice_problem(self) -> str | None:
        """voice_stopped / voice_behind for the banner (lifecycle._problem_code)."""
        light, _ = self._voice_light()
        if light == "stopped":
            return "voice_stopped"
        if light == "behind" and getattr(self, "running", False):
            return "voice_behind"
        return None

    def _voice_state(self) -> dict:
        if self.voice_enabled():
            self._voice_retry_when_model_arrives()
        light, behind = self._voice_light()
        langs = (self._voice_status.get("langs") or {}) if light not in ("off", "stopped") else {}
        en_on = self.voice_lang_enabled("en")
        return {"voice": {
            "enabled": self.voice_enabled(), "state": light, "behind_s": behind,
            "listeners": {"ro": int((langs.get("ro") or {}).get("listeners") or 0),
                          "en": int((langs.get("en") or {}).get("listeners") or 0)},
            "en": en_on, "url": self.voice_url() if self.voice_enabled() else None,
            "port": self._voice_port or int(self.voice_cfg().get("port", 8767)),
            "model": self.voice_model_ready() if self.voice_enabled() else None,
            "summarizer": self._voice_can_summarize() if self.voice_enabled() else False,
            "error": (self._voice_dead or self._voice_error) if light == "stopped" else None,
            "session": self.voice_summary()}}

    def voice_summary(self, items: list[dict] | None = None) -> dict:
        """The voice's numbers for After (§5): median delay, share within 10 s, summaries, peak listeners."""
        items = self._voice_items if items is None else items
        played = [i for i in items if i.get("kind") in ("sentence", "summary") and i.get("delay_after_end_s") is not None]
        d = [float(i["delay_after_end_s"]) for i in played]
        return {"items": len(played),
                "delay_median_s": round(statistics.median(d), 1) if d else None,
                "within_10s_pct": round(100 * sum(x <= 10 for x in d) / len(d)) if d else None,
                "summaries": sum(1 for i in played if i.get("kind") == "summary"),
                "summaries_refused": int(self._voice_counts.get("summaries_refused", 0)),
                "cut": sum(1 for i in played if i.get("cut")),
                "peak_listeners": {"ro": int(self._voice_peak.get("ro", 0)), "en": int(self._voice_peak.get("en", 0))}}

    def _voice_end_run(self):
        """A service ended (lifecycle.stop): its peak listeners go into its log, for After."""
        if self.voice_enabled() and getattr(self, "log_path", None) and self._voice_peak:
            try:
                self._log_event({"event": "voice_listeners", "ro": int(self._voice_peak.get("ro", 0)),
                                 "en": int(self._voice_peak.get("en", 0))})
            except Exception:                           # noqa: BLE001
                pass

    def _voice_new_run(self):
        """A new service: its own numbers."""
        self._voice_items, self._voice_counts = [], collections.Counter()
        self._voice_peak = {lang: int(st.get("listeners") or 0)          # who is listening already
                            for lang, st in (self._voice_status.get("langs") or {}).items()}

    def _voice_settings_changed(self, changed: dict):
        keys = [k for k in changed if k.startswith("voice.")]
        if not keys:
            return
        restart = any(k in ("voice.port", "voice.threads", "voice.en.enabled") or k.startswith("voice.voices")
                      for k in keys)
        fresh = "voice.enabled" in keys                 # off and on again: the operator's way out of voice_stopped
        threading.Thread(target=self._voice_apply, args=(restart, fresh), daemon=True, name="voice-settings").start()

    def _voice_apply(self, restart: bool, fresh: bool):
        """Bring the voice process in line with the settings as they are now. One at a time, and the
        settings are read here, not when the change was made: off-then-on in quick succession ends on."""
        with self._voice_cfg_lock:
            if not self.voice_enabled():
                self.voice_stop()
            else:
                running = self.voice is not None and self.voice.poll() is None and not self._voice_stopping
                if running and restart:
                    self.voice_stop()
                    running = False
                if not running:
                    if fresh:
                        self._voice_restarts.clear()
                        self._voice_dead = None
                    self._voice_stopping = False
                    self.voice_start()
                    if hasattr(self, "_spares") and getattr(self, "tr", None) is not None:
                        sp = getattr(self, "_spares_thread", None)      # Gemma for the summaries, in Quickest
                        if sp is None or not sp.is_alive():
                            self._spares_thread = threading.Thread(target=self._spares, daemon=True,
                                                                   name="spare-translators")
                            self._spares_thread.start()
                else:
                    self._voice_send({"type": "config", "cfg": self._voice_cfg_msg()})
        self._broadcast()
