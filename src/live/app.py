"""Live translation orchestrator + local web control page.

  .venv-mt/bin/python src/live/app.py                     # mic (config.yaml live.input_device)
  .venv-mt/bin/python src/live/app.py --file x.wav --autostart --exit-on-eof   # file as live input
Open http://127.0.0.1:8765 for the control page.

Spawns the ASR engine (src/live/engine.py) in the STT venv, translates committed clauses
with glossary enforcement, pushes rolling captions to ProPresenter, and logs every clause
(timestamped source + translation + latencies) to logs/services/<date>_<time>.jsonl.
Never imports google_stt."""
from __future__ import annotations

import argparse
import collections
import json
import os
import pathlib
import queue
import signal
import subprocess
import sys
import threading
import time

import yaml

# Fully offline at runtime: no model hub lookups, no telemetry. Models are fetched once by
# setup.sh. Set LIVE_ALLOW_NETWORK=1 only for setup/updates.
if os.environ.get("LIVE_ALLOW_NETWORK") != "1":
    for _k, _v in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("HF_DATASETS_OFFLINE", "1"),
                   ("DO_NOT_TRACK", "1")):
        os.environ[_k] = _v
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import netaudit  # noqa: E402
netaudit.install()                 # no-op unless NET_AUDIT_LOG is set (offline tests)

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import awake                                         # noqa: E402
import paths                                         # noqa: E402
if os.environ.get("LIVE_HOME"):        # loaders and the model downloader share one cache
    os.environ.setdefault("HF_HOME", str(paths.hf_home()))
import security                                      # noqa: E402
from captions import RollingCaptions                 # noqa: E402
from langid import LangTracker                       # noqa: E402
from lifecycle import LifecycleMixin                 # noqa: E402
from output import OutputMixin                       # noqa: E402
from display import DisplayMixin                     # noqa: E402
from remote import RemoteMixin                       # noqa: E402
from pipeline import PipelineMixin                   # noqa: E402
from propresenter import ProPresenter, make as make_pp   # noqa: E402,F401
import settings                                      # noqa: E402
from settings import SettingsMixin                   # noqa: E402
from report import SessionMixin                      # noqa: E402
from state import StateMixin                         # noqa: E402
from translate import Glossary                       # noqa: E402
# re-exported: tests and tools import these from live.app
from pipeline import is_echo, merge_clauses, pack_parts            # noqa: E402,F401
from security import LOOPBACK, allowed_hosts, request_allowed      # noqa: E402,F401


class Live(LifecycleMixin, PipelineMixin, OutputMixin, DisplayMixin, RemoteMixin, SessionMixin, StateMixin, SettingsMixin):
    """One service: the engine child, the translator, ProPresenter and the control state.
    The behaviour lives in the mixins (lifecycle, pipeline, output, state); this is the data."""

    def __init__(self, cfg: dict, args):
        self.cfg = cfg
        self.profile = cfg["live"].get("profile", "fast")
        self.lc = self._profile_cfg(self.profile)
        self.args = args
        self.pp = make_pp(cfg["propresenter"])          # ProPresenterOff when propresenter.enabled is false
        self.caps = RollingCaptions(cfg["propresenter"]["max_chars_per_line"], cfg["propresenter"]["max_lines"],
                                    min_line_s=float(cfg["live"].get("caption_min_line_s", 0) or 0),
                                    reveal=bool(cfg["live"].get("caption_reveal", True)),
                                    reveal_wps=float(cfg["live"].get("caption_reveal_wps", 3) or 3))
        self.lang = LangTracker(self.lc.get("language_mode", "auto"))
        self.gloss = Glossary(paths.resource(cfg["paths"]["glossary"], ROOT))
        self.tr = None
        self.engine: subprocess.Popen | None = None
        self.running = False
        self.history: list[dict] = []
        self.lat: list[float] = []
        self.status = "idle"
        self.partial = ""
        self.lock = threading.Lock()
        self.listeners: set = set()
        self._state_fns: list = []                     # register_state(): extra state fields
        self.log_path = None
        self.eof = threading.Event()
        self._pending: dict | None = None
        self._timer: threading.Timer | None = None
        self._proc_lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._stopping = False
        self.warnings: list[str] = []                  # engine degradations, shown until restart
        self.counts: collections.Counter = collections.Counter()   # clause outcomes this run
        self.audio_fault: str | None = None            # engine watchdog: dead/unplugged/muted input
        self.stt_behind: str | None = None             # engine: recognition lags the audio
        self.behind_s: float | None = None             # set while captions lag more than lag_warn_s
        # clauses are translated and shown by ONE worker in arrival order (a held fragment and
        # the clause after it can never overtake each other)
        self._work: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._control = threading.RLock()              # start / stop / profile never overlap
        self._hold_gen = 0                             # a hold timer from an older fragment is stale
        for cls in type(self).__mro__:                 # each mixin's own fields and register_state()
            if "_mixin_init" in vars(cls):
                vars(cls)["_mixin_init"](self)

    @property
    def root(self) -> pathlib.Path:
        """Code/data root for paths.*: the repo in dev (read late, so a test can repoint it)."""
        return ROOT


def build_app(live: Live):
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from routes import output, session, settings, setup, system
    app = FastAPI()
    hosts, port = allowed_hosts(live.lc), int(live.cfg["live"]["web_port"])
    security.install(app, hosts, port)
    app.include_router(system.router(live, hosts, port))
    for mod in (output, settings, setup, session):
        app.include_router(mod.router(live))
    class NoCache(StaticFiles):          # an app update must never run against yesterday's cached UI
        async def get_response(self, path, scope):
            r = await super().get_response(path, scope)
            r.headers["Cache-Control"] = "no-cache"
            return r
    app.mount("/ui", NoCache(directory=HERE / "ui", html=True), name="ui")   # the operator UI (S5)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", help="base settings (default: config.yaml in dev, defaults.yaml in the app)")
    ap.add_argument("--settings", help="the user's settings.yaml over --config (default: paths.settings_file)")
    ap.add_argument("--file", help="play an audio file as live input (real-time paced)")
    ap.add_argument("--autostart", action="store_true")
    ap.add_argument("--exit-on-eof", action="store_true")
    ap.add_argument("--headless", action="store_true", help="no web server")
    ap.add_argument("--pp-port", type=int)
    ap.add_argument("--port", type=int, help="control page port (overrides live.web_port; the app passes a free one)")
    ap.add_argument("--mode")
    ap.add_argument("--profile", help="fast | quality (overrides live.profile)")
    ap.add_argument("--hold", action="store_true",
                    help="preload and wait for Start (the default with the web page; with --file the file plays from Start)")
    a = ap.parse_args()
    awake.hold("Live RO2EN captions")      # no App Nap / timer throttling, also when run from a terminal
    cfg, a.settings_base, a.settings_notes = settings.load_config(a.config, a.settings, ROOT)
    if a.pp_port:
        cfg["propresenter"]["port"] = a.pp_port
    if a.port:
        cfg["live"]["web_port"] = a.port
    if a.mode:
        cfg["live"]["language_mode"] = a.mode
    if a.profile:
        cfg["live"]["profile"] = a.profile
    live = Live(cfg, a)
    terms = os.environ.get("LIVE_TERMS_FILE")       # test bench: today's names, as an operator types them
    if terms and pathlib.Path(terms).is_file():
        live.set_terms({"lines": [ln.strip() for ln in pathlib.Path(terms).read_text().splitlines() if ln.strip()]})
    live.claim_pidfiles()      # end an engine a crashed earlier launch left behind
    live.start_pp_monitor()
    if not a.headless:
        live.start_display_server()   # the view-only captions page, if display.network is on
    if a.headless:             # SIGTERM: stop the service and the engine, then exit
        signal.signal(signal.SIGTERM, lambda *_: threading.Thread(
            target=lambda: (live.shutdown(), os._exit(0)), daemon=True).start())
    if a.hold or (not a.headless and not a.file and cfg["live"].get("preload", True)):
        live.preload()         # models load now; Start is then instant
    if a.autostart or cfg["live"].get("autostart"):
        live.start()
    if a.headless:
        while not (a.exit_on_eof and live.eof.is_set()):
            time.sleep(0.2)
        time.sleep(3)          # let the last clause finish translating
        live.shutdown()
        print(json.dumps({"log": str(live.log_path)}))
        return
    import uvicorn
    if a.exit_on_eof:
        threading.Thread(target=lambda: (live.eof.wait(), time.sleep(3), live.shutdown(),
                                         __import__("os")._exit(0)), daemon=True).start()
    uvicorn.run(build_app(live), host=cfg["live"]["web_host"], port=cfg["live"]["web_port"], log_level="warning")


if __name__ == "__main__":
    main()
