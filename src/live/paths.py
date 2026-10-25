"""Where the live app reads and writes. Dev (LIVE_HOME unset): the repo, as before. Packaged
app: LIVE_HOME (~/Library/Application Support/Live RO2EN) and the LIVE_* overrides below.

  LIVE_HOME               user data root (settings, session logs, models via HF_HOME)
  LIVE_LOG_DIR            logs (engine.log, app_errors.log, services/)
  LIVE_USER_DIR           the user's own files (settings, names & words); default LIVE_HOME
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
    """Environment for the engine child; None (inherit) unless the runtime sets a PYTHONPATH.
    In the app the engine also gets its own HOME under the caches: onnxruntime and lhotse
    create folders in HOME on import (~/Library/Application Support/Microsoft, ~/.lhotse),
    which must not appear in the volunteer's home folder. The orchestrator keeps the real
    HOME (exports go to the real Desktop)."""
    pp = os.environ.get("LIVE_ENGINE_PYTHONPATH")
    if not pp:
        return None
    env = {**os.environ, "PYTHONPATH": pp}
    if _env("LIVE_CACHE_DIR"):
        home = cache_dir() / "engine-home"
        home.mkdir(parents=True, exist_ok=True)
        env["HOME"] = str(home)
    return env


# --- S9: session report, exports, diagnostics, rehearsal samples -----------------------------
def services_index(rel: str, root: pathlib.Path = REPO) -> pathlib.Path:
    """One row per service (summary and rating) for tuning: <services dir>/index.csv."""
    return services_dir(rel, root) / "index.csv"


def desktop() -> pathlib.Path:
    """Where exports and the diagnostics zip go: the user's Desktop (tests repoint this)."""
    return pathlib.Path.home() / "Desktop"


def samples_dir(root: pathlib.Path = REPO) -> pathlib.Path:
    """Rehearsal recordings shipped with the app."""
    return resource("data/samples", root)


# --- models (S6) ---------------------------------------------------------------------------
def hf_home(root: pathlib.Path = REPO) -> pathlib.Path:
    """Model cache root: HF_HOME if set, <LIVE_HOME>/hf in the app, else the user's HF cache.
    The app must export HF_HOME=hf_home() so the model loaders and the downloader agree."""
    if _env("HF_HOME"):
        return _env("HF_HOME")
    if _env("LIVE_HOME"):
        return _env("LIVE_HOME") / "hf"
    return pathlib.Path("~/.cache/huggingface").expanduser()


def hf_cache(root: pathlib.Path = REPO) -> pathlib.Path:
    """Where Hugging Face snapshots live (HF_HUB_CACHE, default <hf_home>/hub)."""
    return _env("HF_HUB_CACHE") or hf_home(root) / "hub"


# --- settings and the user's word lists (S3) -------------------------------------------
def user_dir(root: pathlib.Path = REPO) -> pathlib.Path:
    """The user's own files (settings.yaml and the additions to the bundled lists): LIVE_HOME,
    or <repo>/user in dev, so the bundled glossary.csv / keywords_en.txt are never overwritten.
    LIVE_USER_DIR overrides both (a bench run: no names typed in Settings, only --terms)."""
    return _env("LIVE_USER_DIR") or _env("LIVE_HOME") or root / "user"


def settings_file(root: pathlib.Path = REPO) -> pathlib.Path:
    """The user's overrides of the bundled defaults (schema_version + changed keys only)."""
    return user_dir(root) / "settings.yaml"


def remote_pin_file(root: pathlib.Path = REPO) -> pathlib.Path:
    """Live Remote's PIN (remote.py), next to settings.yaml but never exported with it."""
    return user_dir(root) / "remote_pin"


def user_keywords(root: pathlib.Path = REPO) -> pathlib.Path:
    """Names & words the user added to the bundled keywords_en.txt."""
    return user_dir(root) / "keywords_en.txt"


def user_glossary(root: pathlib.Path = REPO) -> pathlib.Path:
    """Glossary rows (ro,en,note) the user added to the bundled glossary.csv."""
    return user_dir(root) / "glossary.csv"


def base_config(root: pathlib.Path = REPO) -> pathlib.Path:
    """The layer under settings.yaml: config.yaml in dev (LIVE_HOME unset, file present),
    the bundled defaults.yaml otherwise."""
    dev = root / "config.yaml"
    return dev if not _env("LIVE_HOME") and dev.exists() else resource("defaults.yaml", root)
