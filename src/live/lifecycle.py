"""Start, stop and switch the service: the translator, the engine child and its events.
Mixed into live.app.Live (owned by S1; `set_profile` by S4)."""
from __future__ import annotations

import collections
import datetime as dt
import json
import pathlib
import subprocess
import threading

import paths
from translate import make_translator

HERE = pathlib.Path(__file__).resolve().parent
ENGINE_CFG_KEYS = ("vad_threshold", "min_pause_ms", "max_clause_s", "partial_every_s", "min_commit_s",
                   "max_commit_s", "en_recognizer", "context_s", "lookahead_words", "audio_watchdog_s",
                   "digital_silence_s", "stt_backlog_s", "catchup_max_s")


class LifecycleMixin:
    def _profile_cfg(self, name: str) -> dict:
        """config.yaml `live` settings with the chosen profile's overrides on top."""
        return {**self.cfg["live"], **(self.cfg.get("profiles", {}).get(name) or {})}

    def set_profile(self, name: str):
        """Switch fast <-> quality: restarts the engine and reloads the translator if needed."""
        with self._control:
            if name not in self.cfg.get("profiles", {}) or name == self.profile:
                return
            was_running = self.running
            self.stop()
            new = self._profile_cfg(name)
            if new["translator"] != self.lc["translator"]:
                with self._proc_lock:                  # never pulled from under a translation
                    self.tr = None
            self.profile, self.lc = name, new
            if was_running:
                self.start()
            self._broadcast()

    # --- control ---------------------------------------------------------------------
    def load_translator(self):
        with self._proc_lock:                          # the warm-up uses the model too
            if self.tr is None:
                self.status = "loading translator"
                tr = make_translator(self.lc["translator"])
                if hasattr(tr, "context_clauses"):
                    tr.context_clauses = int(self.lc.get("context_clauses", 2))
                tr.translate("Bună dimineața.", "ro", "en", self.gloss)    # warm-up
                self.tr = tr

    def start(self, device=None):
        with self._control:
            if self.running:
                return
            self.running = True                        # Start is disabled while loading
            self.status = "starting"
            self._broadcast()
            try:
                self._drain()                          # a previous run's clauses finish first
                self.load_translator()
                self._start_engine(device)
            except Exception as e:
                self.running = False
                self.status = f"start failed: {type(e).__name__}: {e}"
                self._broadcast()
                raise

    def _start_engine(self, device=None):
        logdir = paths.services_dir(self.lc["transcript_log_dir"], self.root)
        logdir.mkdir(parents=True, exist_ok=True)
        self.log_path = logdir / f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
        py = paths.engine_python(self.lc["stt_venv"], self.root)
        cmd = [str(py), str(HERE / "engine.py"), "--stt", self.lc["stt_model"], "--mode", self.lang.mode,
               "--cfg", json.dumps({**{k: self.lc[k] for k in ENGINE_CFG_KEYS if k in self.lc},
                                   "parakeet_stream": self.cfg.get("parakeet_stream") or {},
                                   "whistle": {**(self.cfg.get("whistle") or {}),   # whistle_<key> in live/profile overrides
                                               **{k[len("whistle_"):]: v for k, v in self.lc.items()
                                                  if k.startswith("whistle_")}}})]
        if getattr(self.args, "file", None):
            cmd += ["--file", self.args.file, "--realtime"]
        elif device or self.lc.get("input_device") is not None:
            cmd += ["--device", str(device or self.lc["input_device"])]
        self.status = "starting engine"
        self.warnings, self.counts = [], collections.Counter()
        self.audio_fault = self.behind_s = self.stt_behind = None
        self.eof.clear()
        self._stopping = False
        logs = paths.log_dir(self.root)
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / "engine.log", "a") as err:     # the child keeps its own copy
            self.engine = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=err, text=True, bufsize=1, env=paths.engine_env())
        self._reader_thread = threading.Thread(target=self._reader, args=(self.engine,), daemon=True)
        self._reader_thread.start()

    def stop(self):
        with self._control:
            self._stopping = True
            if self.engine and self.engine.poll() is None:
                try:
                    self.engine.stdin.write(json.dumps({"cmd": "stop"}) + "\n"); self.engine.stdin.flush()
                    self.engine.wait(20)     # it transcribes the speech it still holds, then emits eof
                except Exception:
                    self.engine.kill()
            if self._reader_thread and self._reader_thread is not threading.current_thread():
                self._reader_thread.join(30)  # let the final clause be translated, shown and logged
            self._drain()                    # nothing from this run may land in the next one
            self.running = False
            self.status = "stopped"
            self._broadcast()

    def set_mode(self, mode: str):
        self.lang.set_mode(mode)
        if self.engine and self.engine.poll() is None:
            self.engine.stdin.write(json.dumps({"cmd": "mode", "mode": mode}) + "\n"); self.engine.stdin.flush()
        self._broadcast()

    # --- engine events -------------------------------------------------------------------
    def _reader(self, engine: subprocess.Popen):
        for line in engine.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev["type"] == "ready":
                self.status = f"listening ({ev['source']})"
            elif ev["type"] == "warn":
                # e.g. the multilingual model failed to load: auto-detect degrades to RO only
                if ev["msg"] == "audio input reopened":
                    self.audio_fault = None              # the device is back
                else:
                    self._warn(ev["msg"])
                self._log_event({"event": "warn", "msg": ev["msg"]})
            elif ev["type"] == "error":
                self._on_engine_error(ev)
            elif ev["type"] == "mode":
                # the engine could not run the requested mode (no multilingual model)
                self.lang.set_mode(ev["mode"])
                self._warn(ev["msg"])
                self._log_event({"event": "mode", "mode": ev["mode"], "msg": ev["msg"]})
            elif ev["type"] == "backlog":
                self.stt_behind = None if ev["ok"] else ev["msg"]
                self._log_event({"event": "backlog", "ok": ev["ok"], "s": ev["s"]})
            elif ev["type"] == "audio":
                self.audio_fault = None if ev["ok"] else ev["msg"]
                self._log_event({"event": "audio", "ok": ev["ok"], "msg": ev["msg"]})
            elif ev["type"] == "partial":
                self.partial = ev["text"]
            elif ev["type"] == "clause":
                self._on_clause(ev)
            elif ev["type"] == "eof":
                self._flush()
                self._drain()                # the last clause is shown and logged before eof
                self.status = "input ended"
                self.eof.set()
            self._broadcast()
        rc = engine.wait()
        if engine is not self.engine:        # an older engine; a newer run owns the state
            return
        self._flush()                        # a held fragment must not die with the engine
        self._drain()
        if engine is not self.engine:        # a new run started while this one drained
            return
        if not self.eof.is_set() and not self._stopping:
            self.status = f"ENGINE STOPPED (exit {rc}): captions halted, see logs/engine.log"
            self._log_event({"event": "engine_exit", "rc": rc})
        self.running = False
        self._broadcast()

    def _on_engine_error(self, ev: dict):
        """The engine survived a failure (one chunk, the English stream, the audio device):
        count and log it, and keep it visible."""
        where = ev.get("where", "engine")
        if where == "transcribe":
            self.status = f"speech recognition failed on one chunk ({ev['msg'][:60]}); still listening"
            self._log_failure(ev, "stt_error", error=ev["msg"])
        elif where == "audio input":                   # retried every few seconds: one live alert
            self.audio_fault = f"input device: {ev['msg']}"
            self._log_event({"event": "error", "where": where, "msg": ev["msg"]})
        else:
            self._warn(f"{where}: {ev['msg']}")
            self._log_event({"event": "error", "where": where, "msg": ev["msg"]})

    def _warn(self, msg: str, keep: int = 20):
        """Shown until the next start; a repeated message is listed once, and the list is
        bounded so a recurring fault cannot flood the control page."""
        if msg not in self.warnings:
            self.warnings = (self.warnings + [msg])[-keep:]
