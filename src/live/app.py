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
import subprocess
import sys
import threading
import time

import yaml

# Fully offline at runtime: no model hub lookups, no telemetry. Models are fetched once by
# setup.sh. Set LIVE_ALLOW_NETWORK=1 only for setup/updates.
if os.environ.get("LIVE_ALLOW_NETWORK") != "1":
    for _k, _v in (("HF_HUB_OFFLINE", "1"), ("TRANSFORMERS_OFFLINE", "1"), ("HF_DATASETS_OFFLINE", "1"),
                   ("NEEDLE_TELEMETRY", "0"), ("DO_NOT_TRACK", "1")):
        os.environ[_k] = _v
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import netaudit  # noqa: E402
netaudit.install()                 # no-op unless NET_AUDIT_LOG is set (offline tests)

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import paths                                         # noqa: E402
import security                                      # noqa: E402
from captions import RollingCaptions                 # noqa: E402
from langid import LangTracker                       # noqa: E402
from lifecycle import LifecycleMixin                 # noqa: E402
from output import OutputMixin                       # noqa: E402
from pipeline import PipelineMixin                   # noqa: E402
from propresenter import ProPresenter                # noqa: E402
from state import StateMixin                         # noqa: E402
from translate import Glossary                       # noqa: E402
# re-exported: tests and tools import these from live.app
from pipeline import is_echo, merge_clauses, pack_parts            # noqa: E402,F401
from security import LOOPBACK, allowed_hosts, request_allowed      # noqa: E402,F401


class Live(LifecycleMixin, PipelineMixin, OutputMixin, StateMixin):
    """One service: the engine child, the translator, ProPresenter and the control state.
    The behaviour lives in the mixins (lifecycle, pipeline, output, state); this is the data."""

    def __init__(self, cfg: dict, args):
        self.cfg = cfg
        self.profile = cfg["live"].get("profile", "fast")
        self.lc = self._profile_cfg(self.profile)
        self.args = args
        self.pp = ProPresenter(**cfg["propresenter"])
        self.caps = RollingCaptions(cfg["propresenter"]["max_chars_per_line"], cfg["propresenter"]["max_lines"])
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
    app.mount("/ui", StaticFiles(directory=HERE / "ui", html=True), name="ui")   # new UI (S5)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--file", help="play an audio file as live input (real-time paced)")
    ap.add_argument("--autostart", action="store_true")
    ap.add_argument("--exit-on-eof", action="store_true")
    ap.add_argument("--headless", action="store_true", help="no web server")
    ap.add_argument("--pp-port", type=int)
    ap.add_argument("--port", type=int, help="control page port (overrides live.web_port; the app passes a free one)")
    ap.add_argument("--mode")
    ap.add_argument("--profile", help="fast | quality (overrides live.profile)")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    if a.pp_port:
        cfg["propresenter"]["port"] = a.pp_port
    if a.port:
        cfg["live"]["web_port"] = a.port
    if a.mode:
        cfg["live"]["language_mode"] = a.mode
    if a.profile:
        cfg["live"]["profile"] = a.profile
    live = Live(cfg, a)
    if a.autostart:
        live.start()
    if a.headless:
        while not (a.exit_on_eof and live.eof.is_set()):
            time.sleep(0.2)
        time.sleep(3)          # let the last clause finish translating
        live.stop()
        print(json.dumps({"log": str(live.log_path)}))
        return
    import uvicorn
    if a.exit_on_eof:
        threading.Thread(target=lambda: (live.eof.wait(), time.sleep(3), live.stop(),
                                         __import__("os")._exit(0)), daemon=True).start()
    uvicorn.run(build_app(live), host=cfg["live"]["web_host"], port=cfg["live"]["web_port"], log_level="warning")


if __name__ == "__main__":
    main()
