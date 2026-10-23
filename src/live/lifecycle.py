"""Start, stop and switch the service: the translator, the engine child and its events.
Mixed into live.app.Live (owned by S1; `set_profile` by S4).

Lifecycle (one `phase` field in the state, docs/app_contract.md):
  needs_setup -> loading -> ready -> starting -> listening|captioning -> stopping -> ready ...
  -> unloading, plus error.
- preload(): the translator loads here while the engine child loads its models in `--hold`
  mode (input open for the level meter only, nothing transcribed). Start then only sends
  {"cmd":"listen","on":true}: live in well under a second. Stop sends listen off: the engine
  commits what it still holds and stays warm for the next service.
- A file run (e2e, rehearsal, --file without --hold) spawns its own engine, as before.
- The engine is restarted automatically after a crash (2 per 60 s), with the same input and
  mode while the translator stays loaded; after that the operator is asked (engine_stopped).
- Nothing is orphaned: the engine exits on stdin EOF and on parent death (engine.py), the app
  stops it on SIGTERM / shutdown, and pidfiles let the next launch clean up after a crash."""
from __future__ import annotations

import collections
import datetime as dt
import json
import os
import pathlib
import signal
import subprocess
import threading
import time

import paths
from translate import make_translator

HERE = pathlib.Path(__file__).resolve().parent
ENGINE_SCRIPT = HERE / "engine.py"       # tests point this at an engine without models
ENGINE_CFG_KEYS = ("vad_threshold", "min_pause_ms", "max_clause_s", "partial_every_s", "min_commit_s",
                   "max_commit_s", "en_recognizer", "context_s", "lookahead_words", "audio_watchdog_s",
                   "digital_silence_s", "stt_backlog_s", "catchup_max_s", "level_hz")
RESTARTS, RESTART_WINDOW_S = 2, 600.0    # automatic engine restarts allowed per window (long
                                         # enough to catch a crash on every model load, ~30 s)
RESTART_WAIT_S = 180.0                   # a crash while a switch/Start holds _control waits this long
ENGINE_LOG_MAX = 20_000_000              # bytes: engine.log becomes engine.old.log at the next spawn
LEVEL_STALE_S = 1.5                      # no level event for this long: the meter shows nothing
LISTEN_ACK_S = 2.0                       # Start waits this long for the engine to go live
# problem.code -> (severity, action id, action label); the UI has the words (strings.en.js)
PROBLEMS = {
    "models_missing": ("error", "setup", "Open Setup"),
    "engine_stopped": ("error", "start", "Start again"),
    "mic_denied": ("error", "help", "Show me how"),
    "device_lost": ("error", "settings", "Choose another input"),
    "no_sound": ("warn", "help", "What to check"),
    "pp_unreachable": ("error", "reconnect", "Try again"),
    "pp_message_missing": ("error", "settings", "Open Settings"),
    "disk_full": ("error", "help", "What to do"),
    "recognition_behind": ("warn", "quickest", "Switch to Quickest"),
    "captions_behind": ("warn", "quickest", "Switch to Quickest"),
    "english_unavailable": ("info", "lock_ro", "Romanian speaker only"),
}


def _alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:              # exists, but not ours
        return True
    return True


def _command(pid: int) -> str:
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _terminate(pid: int, wait_s: float = 2.0) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    end = time.time() + wait_s
    while time.time() < end and _alive(pid):
        time.sleep(0.05)
    if _alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


class LifecycleMixin:
    def _mixin_init(self):
        self.phase_hint: str | None = None       # starting / stopping while a control runs
        self._unloading = False
        self._shutting_down = False
        self._keep_warm = False                  # preload() asked for a warm engine between services
        self._preload_thread: threading.Thread | None = None
        self._tr_thread: threading.Thread | None = None   # reloads the translator after an idle switch
        self._spares_thread: threading.Thread | None = None
        self._engine_hold = False                # the current engine was started with --hold
        self._engine_ready = False               # ... and has sent `ready`
        self._engine_src: str | None = None      # the file it plays (None: the sound input)
        self._engine_device = None               # the input it was told to use
        self._engine_spec: tuple | None = None   # (stt model, English recognizer) it loaded
        self._engine_cfg_sent: dict | None = None
        self._ready_evt = threading.Event()
        self._listening = False                  # the engine is transcribing (live)
        self._listen_ack = threading.Event()
        self._eng_load: tuple[str, float] | None = None   # (label, pct) while the engine loads
        self._restarts: collections.deque = collections.deque()
        self._engine_dead: str | None = None     # the engine stopped for good: engine_stopped
        self._start_error: str | None = None
        self._english_unavailable = False
        self._audio_kind: str | None = None      # no_frames | digital_silence | mic_denied | device_lost
        self._level: tuple[float, dict] | None = None
        self._last_voice_t: float | None = None
        self._last_level_push = 0.0
        self.input_name: str | None = None       # the input the engine opened (its name)
        self._run_src: str | None = None         # what this service listens to (for a restart)
        self._run_device = None
        self._ran = False                        # a service ran since launch: idle = stopped
        self._models_cache: tuple[float, list] = (-1e9, [])
        self._engine_warnings: list[str] = []    # from loading: kept across services
        self._pid_dir: pathlib.Path | None = None
        self._t_launch = time.time()
        self.exit_process = lambda: os.kill(os.getpid(), signal.SIGTERM)   # /api/shutdown; tests replace it
        self.register_state(self._lifecycle_state)

    def _profile_cfg(self, name: str) -> dict:
        """config.yaml `live` settings with the chosen profile's overrides on top."""
        return {**self.cfg["live"], **(self.cfg.get("profiles", {}).get(name) or {})}

    def set_profile(self, name: str):
        """Switch fast <-> quality with no gap (S4): the engine keeps running and gets the
        profile's pause settings with {"cmd":"cfg"}; the translator is swapped between two
        clauses (PipelineMixin._swap_profile: kept loaded side by side when RAM allows, else a
        warm reload). Only a different speech model needs an engine restart."""
        with self._control:
            if name not in self.cfg.get("profiles", {}) or name == self.profile:
                return
            new, old = self._profile_cfg(name), self.profile
            engine_up = self.engine is not None and self.engine.poll() is None   # also in hold mode
            keys = self.RESTART_KEYS + tuple(k for k in {*new, *self.lc} if k.startswith("whistle_"))
            restart = self.running and engine_up and any(new.get(k) != self.lc.get(k) for k in keys)
            if restart:
                self.stop()
            try:
                info = self._swap_profile(name, new)
            except Exception as e:
                if self.running and self.tr is None:   # neither translator would load back
                    threading.Thread(target=self._translator_lost, args=(e,), daemon=True).start()
                raise
            sent = False
            if engine_up and not restart:
                cfg = {k: new[k] for k in ENGINE_CFG_KEYS if k in new and k not in self.RESTART_KEYS}
                try:
                    self.engine.stdin.write(json.dumps({"cmd": "cfg", "cfg": cfg}) + "\n")
                    self.engine.stdin.flush()
                    sent = True
                except Exception as e:                 # noqa: BLE001 - the engine reader reports a death
                    self._warn(f"could not send the new pause settings to the engine: {e}")
            self._log_event({"event": "profile", "from": old, "to": name,
                             "hot": self.running and not restart, "engine_cfg": sent, **info})
            if restart:
                self.start()
            self._broadcast()

    def _translator_lost(self, e: Exception):
        """No translator is loaded mid-service: end it so the operator sees it (engine_stopped,
        Start again loads the translator) instead of a service that logs an error per clause."""
        keep, self._keep_warm = self._keep_warm, False    # no re-preload: it would clear the error
        try:
            self.stop()
        finally:
            self._keep_warm = keep
        self._start_error = self.status = f"translator failed to load: {type(e).__name__}: {e}"
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

    def preload(self, device=None, wait: bool = False):
        """Load everything now and keep it warm, so Start is instant: the translator here and
        the engine in hold mode (input open for the level meter only). Runs in the background;
        idempotent. Nothing happens while the models are missing (phase needs_setup)."""
        self._keep_warm = True
        with self._control:
            busy = self._preload_thread is not None and self._preload_thread.is_alive()
            if busy or self.running or self._shutting_down or self._warm_alive():
                th = self._preload_thread if busy else None
            elif self._models_missing():
                self.status = "models missing: open Setup"
                self._broadcast()
                return
            else:
                self._engine_dead = self._start_error = None
                self.phase_hint = "loading"
                th = self._preload_thread = threading.Thread(target=self._preload_run, args=(device,),
                                                             daemon=True, name="preload")
                th.start()
        if wait and th is not None:
            th.join()

    def _preload_run(self, device):
        try:
            with self._control:
                if self.running or self._shutting_down or self._warm_alive():
                    return
                self._spawn_engine(device, hold=True, src=getattr(self.args, "file", None))
            self._broadcast()
            try:
                self.load_translator()                 # meanwhile the engine loads in its own process
            except Exception as e:                     # noqa: BLE001  Start retries and shows it
                self._start_error = f"translator failed to load: {type(e).__name__}: {e}"
                self.status = self._start_error
            eng = self.engine
            while not self._ready_evt.wait(0.2):       # the engine is loading its models
                if eng is None or eng.poll() is not None or eng is not self.engine or self._shutting_down:
                    break
            if self._ready_evt.is_set() and self.tr is not None and hasattr(self, "preload_translators"):
                self._spares_thread = threading.Thread(target=self._spares, daemon=True, name="spare-translators")
                self._spares_thread.start()
        finally:
            if self.phase_hint == "loading":
                self.phase_hint = None
            self._broadcast()

    def _spares(self):
        """The other profile's translator too (S4, only when RAM allows): the first switch is instant."""
        try:
            self.preload_translators()
        except Exception as e:                         # noqa: BLE001  a spare is a nicety
            self._warn(f"could not preload the other translator: {type(e).__name__}: {e}")

    def _await_preload(self, timeout: float = 600):
        th = self._preload_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout)

    def _pause_download(self) -> bool:
        """End a model download (its partial files stay; Setup resumes it)."""
        ms = getattr(self, "model_setup", None)
        if ms is None or not ms.progress().get("running"):
            return False
        ms.cancel()
        return True

    def start(self, device=None):
        paused = self._pause_download()                # the service gets the network and the CPU
        self._await_preload()                          # Start pressed while still loading: wait
        with self._control:
            if self.running:
                return
            self.running = True                        # Start is disabled while loading
            self.status = "starting"
            self.phase_hint = "starting"
            self._engine_dead = self._start_error = None
            self._broadcast()
            try:
                self._drain()                          # a previous run's clauses finish first
                src = getattr(self.args, "file", None)
                if self._warm_usable(src):
                    self._go_live(device)
                else:
                    self._retire_engine()              # warm on another input or model: replace it
                    self._await_spare()
                    self.load_translator()
                    self._start_engine(device)
                if paused:                             # after _new_run, which resets the warnings
                    self._warn("the model download was paused for the service; resume it in Setup")
            except Exception as e:
                self.running = False
                self.phase_hint = None
                self.status = f"start failed: {type(e).__name__}: {e}"
                self._start_error = self.status
                self._broadcast()
                raise

    def _await_spare(self):
        """A spare that is loading may be this profile's translator: take it, not a second copy."""
        sp = self._spares_thread
        if self.tr is None and sp is not None and sp.is_alive() and sp is not threading.current_thread():
            sp.join()

    def _go_live(self, device=None):
        """Start on the warm engine: a new service log, then {"cmd":"listen","on":true}."""
        self._await_spare()
        self.load_translator()                         # loaded by preload; reloaded after an unload
        want = device if device is not None else self.lc.get("input_device")
        self._new_run()
        self._run_src, self._run_device = self._engine_src, want
        if self._engine_src is None and want != self._engine_device:
            self._send({"cmd": "device", "device": want})
            self._engine_device = want
        cfg = self._engine_cfg()
        if cfg != self._engine_cfg_sent:               # e.g. another profile's pause settings
            self._send({"cmd": "cfg", "cfg": cfg})
            self._engine_cfg_sent = cfg
        self._listen_ack.clear()
        self._send({"cmd": "listen", "on": True})
        if self._listen_ack.wait(LISTEN_ACK_S):
            self.status = f"listening ({self.input_name or self._engine_src or 'default'})"
        self._broadcast()

    def _new_run(self):
        """A new service: its own log file, fresh counters and warnings."""
        logdir = paths.services_dir(self.lc["transcript_log_dir"], self.root)
        logdir.mkdir(parents=True, exist_ok=True)
        stem = f"{dt.datetime.now():%Y-%m-%d_%H%M%S}"
        path, n = logdir / f"{stem}.jsonl", 1
        while path.exists() or path == self.log_path:   # a second service in the same second
            n += 1
            path = logdir / f"{stem}_{n}.jsonl"           # sorts after the first one
        self.log_path = path
        self.warnings, self.counts = list(self._engine_warnings), collections.Counter()
        self.behind_s = self.stt_behind = None
        self.eof.clear()
        self._stopping = False
        self._ran = True

    def _engine_cfg(self) -> dict:
        return {**{k: self.lc[k] for k in ENGINE_CFG_KEYS if k in self.lc},
                "parakeet_stream": self.cfg.get("parakeet_stream") or {},
                "whistle": {**(self.cfg.get("whistle") or {}),   # whistle_<key> in live/profile overrides
                            **{k[len("whistle_"):]: v for k, v in self.lc.items() if k.startswith("whistle_")}}}

    def _start_engine(self, device=None):
        """A service on a new engine (file runs, or nothing warm): live as soon as it is ready."""
        self._new_run()
        self.audio_fault = None
        self._audio_kind = None
        want = device or self.lc.get("input_device")
        self._run_src, self._run_device = getattr(self.args, "file", None), want
        self._spawn_engine(want, hold=False, src=self._run_src)

    def _spawn_engine(self, device=None, hold: bool = False, src: str | None = None):
        py = paths.engine_python(self.lc["stt_venv"], self.root)
        cfg = self._engine_cfg()
        cmd = [str(py), str(ENGINE_SCRIPT), "--stt", self.lc["stt_model"], "--mode", self.lang.mode,
               "--cfg", json.dumps(cfg)]
        if hold:
            cmd += ["--hold"]
        if src:
            cmd += ["--file", src, "--realtime"]
        elif device is not None:
            cmd += ["--device", str(device)]
        self.status = "loading speech recognition" if hold else "starting engine"
        self.audio_fault = None
        self._audio_kind = self._level = None
        self._engine_hold, self._engine_ready, self._listening = hold, False, False
        self._engine_src, self._engine_device = src, device
        self._engine_spec = self._spec()
        self._engine_cfg_sent = cfg
        self._eng_load = ("Starting speech recognition", 0.0)
        self._ready_evt.clear()
        self._listen_ack.clear()
        self._english_unavailable = False
        self._engine_warnings = []
        self._stopping = False
        logs = paths.log_dir(self.root)
        logs.mkdir(parents=True, exist_ok=True)
        elog = logs / "engine.log"
        try:                                           # one older copy kept, so it never grows unbounded
            if elog.stat().st_size > ENGINE_LOG_MAX:
                elog.replace(logs / "engine.old.log")
        except OSError:
            pass
        with open(elog, "a") as err:                   # the child keeps its own copy
            self.engine = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=err, text=True, bufsize=1, env=paths.engine_env())
        self._write_engine_pid(self.engine)
        self._reader_thread = threading.Thread(target=self._reader, args=(self.engine,), daemon=True)
        self._reader_thread.start()

    def _send(self, cmd: dict) -> bool:
        eng = self.engine
        if eng is None or eng.poll() is not None:
            return False
        try:
            eng.stdin.write(json.dumps(cmd) + "\n")
            eng.stdin.flush()
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def _engine_alive(self) -> bool:
        eng = self.engine
        return eng is not None and eng.poll() is None

    def _warm_alive(self) -> bool:
        return self._engine_alive() and self._engine_hold and not self._listening

    def _spec(self) -> tuple:
        """What a running engine cannot change live (S4's RESTART_KEYS and whistle_* options)."""
        keys = tuple(getattr(self, "RESTART_KEYS", ("stt_model", "stt_venv", "en_recognizer")))
        return tuple(self.lc.get(k) for k in keys) + tuple(sorted(
            (k, json.dumps(v, sort_keys=True, default=str)) for k, v in self.lc.items() if k.startswith("whistle_")))

    def _swap_profile(self, name: str, new: dict) -> dict:
        """S4 swaps the profile (pipeline.py); a warm engine that cannot run the new profile
        (another speech model) is replaced in the background, so the next Start is instant."""
        info = super()._swap_profile(name, new)
        if not self.running and self._warm_alive() and self._engine_spec != self._spec():
            threading.Thread(target=self._rewarm, daemon=True, name="rewarm").start()
        elif not self.running and self.tr is None and self._keep_warm and not self._shutting_down:
            # switched while idle with no spare: load the new translator now, not at Start
            self._tr_thread = threading.Thread(target=self._reload_translator, daemon=True, name="translator")
            self._tr_thread.start()
        return info

    def _reload_translator(self):
        self._broadcast()                              # phase loading
        try:
            self._await_spare()
            self.load_translator()
            if not self.running:                       # a Start meanwhile owns the status
                self.status = "ready"
        except Exception as e:                         # noqa: BLE001  Start retries and shows it
            self._start_error = f"translator failed to load: {type(e).__name__}: {e}"
            self.status = self._start_error
        finally:
            self._broadcast()

    def _rewarm(self):
        with self._control:
            if self.running or not self._warm_alive() or self._engine_spec == self._spec():
                return
            self._retire_engine()
        self.preload()

    def _warm_usable(self, src) -> bool:
        """The warm engine can take this service: same source and the same models."""
        return (self._warm_alive() and self._engine_ready and self._engine_src == src
                and self._engine_spec == self._spec())

    def stop(self):
        with self._control:
            eng = self.engine
            if not self.running and self._warm_alive() and not self._shutting_down:
                self.status = "ready"                 # nothing live: keep the warm engine
                self._broadcast()
                return
            self._stopping = True
            self.phase_hint = "stopping"
            self._broadcast()
            warm = False
            if (eng is not None and eng.poll() is None and self._engine_hold and self._engine_src is None
                    and self._listening and not self._shutting_down):
                self._listen_ack.clear()               # commit what it holds, stay loaded
                warm = self._send({"cmd": "listen", "on": False}) and self._listen_ack.wait(20)
                warm = warm and eng.poll() is None    # a dead engine also sets the ack
            if not warm and eng is not None and eng.poll() is None:
                try:
                    eng.stdin.write(json.dumps({"cmd": "stop"}) + "\n"); eng.stdin.flush()
                    eng.wait(20)     # it transcribes the speech it still holds, then emits eof
                except Exception:
                    eng.kill()
            if not warm and self._reader_thread and self._reader_thread is not threading.current_thread():
                self._reader_thread.join(30)  # let the final clause be translated, shown and logged
            self._flush()                    # a held fragment is shown before the service ends
            self._drain()                    # nothing from this run may land in the next one
            self.running = False
            self.phase_hint = None
            if warm:
                self._listening = False
                self._stopping = False       # the engine lives on: a crash from now is a crash
                self.status = "ready"
            else:
                self.status = "stopped"
            self._broadcast()
        if not warm and self._keep_warm and not self._shutting_down and not getattr(self.args, "file", None):
            threading.Thread(target=self.preload, daemon=True, name="re-preload").start()

    def _retire_engine(self):
        """End the current engine (warm or not) and wait for its reader."""
        with self._control:
            eng = self.engine
            if eng is None or eng.poll() is not None:
                return
            self._stopping = True
            try:
                eng.stdin.write(json.dumps({"cmd": "stop"}) + "\n"); eng.stdin.flush()
                eng.wait(10)
            except Exception:
                eng.kill()
            if self._reader_thread and self._reader_thread is not threading.current_thread():
                self._reader_thread.join(15)
            self._engine_ready = self._listening = False

    def unload(self):
        """Free the memory (models and engine) until the next preload or Start."""
        self._keep_warm = False
        with self._control:
            if self.running:
                self.stop()
            self._unloading = True
            self.status = "freeing memory"
            self._broadcast()
            try:
                self._retire_engine()
                with self._proc_lock:
                    self.tr = None
                import gc
                gc.collect()
            finally:
                self._unloading = False
                self._ran = True
                self.status = "unloaded"
                self._broadcast()

    def shutdown(self):
        """App quit (SIGTERM, the FastAPI shutdown hook, POST /api/shutdown): stop the service,
        end the engine, drop the pidfiles. Idempotent."""
        if self._shutting_down:
            return
        self._shutting_down = True
        self._keep_warm = False
        try:
            self._pause_download()
            if self.running:
                self.stop()
            self._retire_engine()
        finally:
            if hasattr(self, "stop_pp_monitor"):
                self.stop_pp_monitor()
            self._drop_pidfiles()

    def set_mode(self, mode: str):
        self.lang.set_mode(mode)
        self._send({"cmd": "mode", "mode": mode})
        self._broadcast()

    def _follow_input(self):
        """The chosen input changed (Settings, Setup wizard): a warm engine switches to it now,
        so the level meter shows the new input; a live service switches at the next Start."""
        want = self.lc.get("input_device")
        if self.running or not self._warm_alive() or self._engine_src is not None or want == self._engine_device:
            return
        if self._send({"cmd": "device", "device": want}):
            self._engine_device = want
            self.input_name, self._level, self._last_voice_t = None, None, None
            self.audio_fault = self._audio_kind = None
            self._broadcast()

    def _apply_settings(self, changed: dict):
        """S3 applies the settings (settings.py); the input device is followed here."""
        sup = getattr(super(), "_apply_settings", None)
        if sup is not None:
            sup(changed)
        if "live.input_device" in changed:
            self._follow_input()
        if "live.language_mode" in changed and not self.running:
            self._send({"cmd": "mode", "mode": self.lang.mode})   # a warm engine listens for it at Start
        if not self.running and self._warm_alive() and self._engine_spec != self._spec():
            # e.g. the English recogniser changed: the warm engine can't take it live, replace it now
            threading.Thread(target=self._rewarm, daemon=True, name="rewarm").start()

    # --- pidfiles: the next launch cleans up after a crash -------------------------------
    def claim_pidfiles(self, run_dir: pathlib.Path | None = None):
        """Called once at launch (main): end engines an earlier app instance left behind
        (its app pid is gone, and the process really is this copy's engine.py), then record
        our own pid. Pidfiles live in <log dir>/run."""
        d = pathlib.Path(run_dir) if run_dir else paths.log_dir(self.root) / "run"
        d.mkdir(parents=True, exist_ok=True)
        ours = str(ENGINE_SCRIPT)
        for f in d.glob("engine-*.pid"):
            try:
                epid, apid = (int(x) for x in f.read_text().split()[:2])
            except (OSError, ValueError):
                f.unlink(missing_ok=True)
                continue
            if apid != os.getpid() and not _alive(apid):
                if _alive(epid) and ours in _command(epid):
                    _terminate(epid)
                f.unlink(missing_ok=True)
        (d / "app.pid").write_text(f"{os.getpid()}\n")
        self._pid_dir = d

    def _write_engine_pid(self, eng):
        if self._pid_dir is None or not getattr(eng, "pid", None):
            return
        try:
            (self._pid_dir / f"engine-{os.getpid()}.pid").write_text(f"{eng.pid} {os.getpid()}\n")
        except OSError:
            pass

    def _drop_engine_pid(self, eng):
        if self._pid_dir is None:
            return
        f = self._pid_dir / f"engine-{os.getpid()}.pid"
        try:
            if f.read_text().split()[0] == str(getattr(eng, "pid", "")):
                f.unlink()
        except (OSError, IndexError):
            pass

    def _drop_pidfiles(self):
        if self._pid_dir is None:
            return
        for f in (self._pid_dir / f"engine-{os.getpid()}.pid", self._pid_dir / "app.pid"):
            try:
                if f.name != "app.pid" or f.read_text().strip() == str(os.getpid()):
                    f.unlink()
            except OSError:
                pass

    # --- engine events -------------------------------------------------------------------
    def _elog(self, rec: dict):
        """Engine events go into the service log only while a service runs (a warm engine's
        events between services belong to no service)."""
        if self.running:
            self._log_event(rec)

    def _reader(self, engine: subprocess.Popen):
        played_out = False                   # this engine said eof (self.eof outlives it: a warm engine
        for line in engine.stdout:           # started after a rehearsal must still count as crashing)
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = ev.get("type")
            current = engine is self.engine
            if t == "level":
                if current:
                    self._on_level(ev)
                continue
            if t == "loading":
                if current:
                    self._eng_load = (str(ev.get("label", "")), float(ev.get("pct", 0)))
            elif t == "input":
                if current:
                    self.input_name = ev.get("device")
            elif t == "listen":
                if current:
                    self._listening = bool(ev.get("on"))
                    if self.phase_hint == "starting" and self._listening:
                        self.phase_hint = None
                    self._listen_ack.set()
            elif t == "ready":
                if current:
                    self._engine_ready = True
                    self._eng_load = None
                    if ev.get("hold"):
                        self.status = f"ready ({ev.get('source', 'default')})"
                    else:
                        self._listening = True
                        if self.phase_hint == "starting":
                            self.phase_hint = None
                        self.status = f"listening ({ev['source']})"
                    self._ready_evt.set()
            elif t == "warn":
                # e.g. the multilingual model failed to load: auto-detect degrades to RO only
                if ev["msg"] == "audio input reopened":
                    self.audio_fault = None              # the device is back
                    self._audio_kind = None
                else:
                    self._warn(ev["msg"])
                    if not self._engine_ready:            # a loading problem: kept for every service
                        self._engine_warnings = (self._engine_warnings + [ev["msg"]])[-20:]
                    if ev["msg"].startswith("multilingual model unavailable"):
                        self._english_unavailable = True
                self._elog({"event": "warn", "msg": ev["msg"]})
            elif t == "error":
                self._on_engine_error(ev)
            elif t == "mode":
                # the engine could not run the requested mode (no multilingual model)
                self.lang.set_mode(ev["mode"])
                self._warn(ev["msg"])
                self._english_unavailable = True
                self._elog({"event": "mode", "mode": ev["mode"], "msg": ev["msg"]})
            elif t == "backlog":
                self.stt_behind = None if ev["ok"] else ev["msg"]
                self._elog({"event": "backlog", "ok": ev["ok"], "s": ev["s"]})
            elif t == "audio":
                self.audio_fault = None if ev["ok"] else ev["msg"]
                self._audio_kind = None if ev["ok"] else (ev.get("fault") or "no_frames")
                self._elog({"event": "audio", "ok": ev["ok"], "msg": ev["msg"]})
            elif t == "partial":
                self.partial = ev["text"]
            elif t == "clause":
                self._on_clause(ev)
            elif t == "eof":
                self._flush()
                self._drain()                # the last clause is shown and logged before eof
                self.status = "input ended"
                played_out = True
                self.eof.set()
            self._broadcast()
        rc = engine.wait()
        self._drop_engine_pid(engine)
        if engine is not self.engine:        # an older engine; a newer run owns the state
            return
        self._flush()                        # a held fragment must not die with the engine
        self._drain()
        if engine is not self.engine:        # a new run started while this one drained
            return
        self._engine_ready = self._listening = False
        self._listen_ack.set()               # nobody waits for an answer from a dead engine
        ended = played_out and not self._stopping and not self._shutting_down
        if not played_out and not self._stopping and not self._shutting_down:
            self._elog({"event": "engine_exit", "rc": rc})
            if self._restart_after_crash(rc, engine):
                return
            self.status = f"ENGINE STOPPED (exit {rc}): captions halted, see logs/engine.log"
            self._engine_dead = self.status
        self.running = False
        self.phase_hint = None
        self._broadcast()
        if ended and self._keep_warm and not getattr(self.args, "file", None):
            # a rehearsal file played out: warm the sound input again for the next Start
            threading.Thread(target=self.preload, daemon=True, name="re-preload").start()

    def _restart_after_crash(self, rc, dead=None) -> bool:
        """The engine died on its own: start it again with the same input, file and mode (the
        translator stays loaded). At most RESTARTS times per RESTART_WINDOW_S; then the
        operator decides (engine_stopped)."""
        limit = int(self.lc.get("engine_restarts", RESTARTS))
        now = time.monotonic()
        while self._restarts and now - self._restarts[0] > RESTART_WINDOW_S:
            self._restarts.popleft()
        if len(self._restarts) >= limit or not (self.running or self._keep_warm):
            return False
        if self._stopping or self._shutting_down:
            return False
        if not self._control.acquire(blocking=False):
            # a profile reload or a Start holds it (a Stop sets _stopping): restart once it is done
            threading.Thread(target=self._restart_when_free, args=(rc, dead, now), daemon=True,
                             name="engine-restart").start()
            return True
        try:
            return self._restart_locked(rc, now)
        finally:
            self._control.release()

    def _restart_when_free(self, rc, dead, now):
        if not self._control.acquire(timeout=RESTART_WAIT_S):
            # still busy after RESTART_WAIT_S: give up, unless the holder moved on without us
            if self.engine is dead and not (self._stopping or self._shutting_down):
                self.status = f"ENGINE STOPPED (exit {rc}): captions halted, see logs/engine.log"
                self._engine_dead = self.status
                self.running, self.phase_hint = False, None
                self._broadcast()
            return
        try:
            if self.engine is not dead:              # the holder started a new engine meanwhile
                return
            if not self._restart_locked(rc, now) and not (self._stopping or self._shutting_down):
                self.status = f"ENGINE STOPPED (exit {rc}): captions halted, see logs/engine.log"
                self._engine_dead = self.status
                self.running, self.phase_hint = False, None
                self._broadcast()
        finally:
            self._control.release()

    def _restart_locked(self, rc, now) -> bool:
        try:
            if self._shutting_down or self._stopping or not (self.running or self._keep_warm):
                return False
            self._restarts.append(now)
            msg = f"the speech engine stopped (exit {rc}) and was restarted"
            self._warn(msg)
            self._elog({"event": "engine_restart", "rc": rc})
            if self.running:
                self._spawn_engine(self._run_device, hold=False, src=self._run_src)
                self.phase_hint = "starting"
            else:
                self._spawn_engine(self._engine_device, hold=True, src=self._engine_src)
            self.status = "speech engine restarting"
            self._broadcast()
            return True
        except Exception as e:                           # noqa: BLE001  could not even spawn
            self._warn(f"engine restart failed: {type(e).__name__}: {e}")
            return False

    def _on_engine_error(self, ev: dict):
        """The engine survived a failure (one chunk, the English stream, the audio device):
        count and log it, and keep it visible."""
        where = ev.get("where", "engine")
        if where == "transcribe":
            self.status = f"speech recognition failed on one chunk ({ev['msg'][:60]}); still listening"
            self._log_failure(ev, "stt_error", error=ev["msg"])
        elif where == "audio input":                   # retried every few seconds: one live alert
            self.audio_fault = f"input device: {ev['msg']}"
            self._audio_kind = "device_lost"
            self._elog({"event": "error", "where": where, "msg": ev["msg"]})
        else:
            self._warn(f"{where}: {ev['msg']}")
            self._elog({"event": "error", "where": where, "msg": ev["msg"]})

    def _on_level(self, ev: dict):
        now = time.time()
        self._level = (now, ev)
        if ev.get("speech"):
            self._last_voice_t = now
        if now - self._last_level_push >= 0.05:        # the meter moves at the engine's rate (≤ 20 Hz)
            self._last_level_push = now
            self._broadcast()

    def _warn(self, msg: str, keep: int = 20):
        """Shown until the next start; a repeated message is listed once, and the list is
        bounded so a recurring fault cannot flood the control page."""
        if msg not in self.warnings:
            self.warnings = (self.warnings + [msg])[-keep:]

    # --- state: phase, progress, audio, problem ------------------------------------------
    def _models_missing(self) -> list:
        """Models the Fast profile still needs (S6), re-checked at most every 5 s."""
        t, val = self._models_cache
        if time.monotonic() - t > 5:
            try:
                import model_setup
                val = model_setup.missing("fast", self.root)
            except Exception:                              # noqa: BLE001  unknown: do not block Start
                val = []
            self._models_cache = (time.monotonic(), val)
        return val

    def _models_missing_for(self, profile: str) -> list:
        """Models `profile` still needs (not cached: preflight asks once)."""
        import model_setup
        return model_setup.missing(profile, self.root)

    def _phase(self) -> str:
        if self._unloading:
            return "unloading"
        if self.running:
            if self.phase_hint == "stopping":
                return "stopping"
            if not self._listening or self.phase_hint == "starting":
                return "starting"
            return "captioning" if self.counts.get("ok") and not getattr(self, "paused", False) else "listening"
        if (self.phase_hint == "loading" or any(t is not None and t.is_alive()
                                                 for t in (self._preload_thread, self._tr_thread))):
            return "loading"
        if self._engine_dead or self._start_error:
            return "error"
        if self._warm_alive():
            return "ready" if self._engine_ready else "loading"
        if self._models_missing():
            return "needs_setup"
        return "stopped" if self._ran else "ready"

    def _progress(self, phase: str) -> dict | None:
        if phase != "loading":
            return None
        label, eng = self._eng_load if self._eng_load else ("Speech recognition ready", 100.0)
        tr = 100.0 if self.tr is not None else 0.0
        if eng >= 100 and self.tr is None:
            label = "Loading the translator"
        return {"label": label, "pct": round(0.7 * eng + 0.3 * tr, 1)}

    def _audio_state(self) -> dict:
        now = time.time()
        alive = self._engine_alive()
        lv = self._level[1] if self._level and alive and now - self._level[0] < LEVEL_STALE_S else {}
        dev = self.input_name
        if dev is None and isinstance(self.lc.get("input_device"), str):
            dev = self.lc["input_device"]
        return {"device": dev, "rms_db": lv.get("rms_db"), "peak_db": lv.get("peak_db"),
                "speech": bool(lv.get("speech")),
                "last_voice_s": round(now - self._last_voice_t, 1) if self._last_voice_t else None,
                "ok": not alive or (self._audio_kind is None and self.audio_fault is None),   # no known fault
                "engine": alive}                        # false: nothing listens, so no level is no news

    def _problem_code(self, phase: str) -> str | None:
        """The one problem the banner shows, most urgent first (docs/app_contract.md)."""
        if phase == "needs_setup":
            return "models_missing"
        if phase == "error":
            return "engine_stopped"
        if self._engine_alive():
            kind = self._audio_kind
            if kind == "mic_denied":
                return "mic_denied"
            if kind in ("no_frames", "device_lost"):
                return "device_lost"
            if kind == "digital_silence":
                return "no_sound"
        pp = getattr(self, "pp", None)
        code = pp.status().get("error_code") if hasattr(pp, "status") else None
        if code in ("unreachable", "auth"):
            return "pp_unreachable"
        if code == "message_missing":
            return "pp_message_missing"
        pre = getattr(self, "_preflight", None) or {}
        if any(c.get("code") == "disk_full" and not c.get("ok") for c in pre.get("checks") or []):
            return "disk_full"
        if self.running and self.stt_behind:
            return "recognition_behind"
        if self.running:
            pace = self._pipeline_state().get("pace") if hasattr(self, "_pipeline_state") else None
            if (pace and pace.get("state") != "ok" and pace.get("behind_s")) or self.behind_s:
                return "captions_behind"            # S4's pace: captions lag the speech (not recognition)
        if self._english_unavailable and self._engine_alive() and self.lang.mode != "ro-en":
            return "english_unavailable"
        return None

    def _lifecycle_state(self) -> dict:
        phase = self._phase()
        code = self._problem_code(phase)
        problem = None
        if code:
            sev, aid, label = PROBLEMS[code]
            problem = {"code": code, "severity": sev, "action": {"id": aid, "label": label}}
        return {"phase": phase, "progress": self._progress(phase), "audio": self._audio_state(), "problem": problem}

    def readiness(self) -> dict:
        """GET /api/readiness: what the Mac shell shows while the app launches."""
        st = self._lifecycle_state()
        return {"phase": st["phase"], "ready": st["phase"] in ("ready", "listening", "captioning"),
                "progress": st["progress"], "problem": st["problem"], "status": self.status}

    def health(self) -> dict:
        """GET /api/health: the orchestrator answers, and whether the engine lives."""
        eng = self.engine
        return {"ok": True, "pid": os.getpid(), "phase": self._phase(),
                "engine": {"alive": self._engine_alive(), "pid": getattr(eng, "pid", None) if self._engine_alive() else None,
                           "hold": self._engine_hold, "listening": self._listening},
                "uptime_s": round(time.time() - self._t_launch, 1), "restarts": len(self._restarts)}
