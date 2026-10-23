"""Where the live app reads and writes. Dev (LIVE_HOME unset): the repo, as before. Packaged
app: LIVE_HOME (~/Library/Application Support/Live RO2EN) and the LIVE_* overrides below.

  LIVE_HOME               user data root (settings, session logs, models via HF_HOME)
  LIVE_LOG_DIR            logs (engine.log, app_errors.log, services/)
  LIVE_CACHE_DIR          caches
  LIVE_ENGINE_PYTHON      interpreter for engine.py (default <root>/<stt_venv>/bin/python)
  LIVE_ENGINE_PYTHONPATH  PYTHONPATH for that interpreter (embedded runtime site dirs)

Every function takes the repo root, so tests can point a Live at a temporary one."""
from __future__ import annotations

import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def _env(name: str) -> pathlib.Path | None:
    v = os.environ.get(name)
    return pathlib.Path(v).expanduser() if v else None


def home(root: pathlib.Path = REPO) -> pathlib.Path:
    return _env("LIVE_HOME") or root


def log_dir(root: pathlib.Path = REPO) -> pathlib.Path:
    return _env("LIVE_LOG_DIR") or home(root) / "logs"


def services_dir(rel: str, root: pathlib.Path = REPO) -> pathlib.Path:
    """Per-run clause logs: config `transcript_log_dir` in dev, <log dir>/services otherwise."""
    if _env("LIVE_HOME") or _env("LIVE_LOG_DIR"):
        return log_dir(root) / "services"
    return root / rel


def cache_dir(root: pathlib.Path = REPO) -> pathlib.Path:
    return _env("LIVE_CACHE_DIR") or home(root) / ".cache"


def resource(rel: str, root: pathlib.Path = REPO) -> pathlib.Path:
    """A file shipped with the app (glossary, control page): always under the code root."""
    return root / rel


def engine_python(stt_venv: str, root: pathlib.Path = REPO) -> pathlib.Path:
    return _env("LIVE_ENGINE_PYTHON") or root / stt_venv / "bin" / "python"


def engine_env() -> dict | None:
    """Environment for the engine child; None (inherit) unless the runtime sets a PYTHONPATH."""
    pp = os.environ.get("LIVE_ENGINE_PYTHONPATH")
    return {**os.environ, "PYTHONPATH": pp} if pp else None
