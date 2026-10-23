"""Before the sermon: the preflight checklist and the rehearsal run. Owned by S9.

`run(live)` returns {checks: [{id, ok, detail, code?}], ok, t}; each check says in plain words
what it found. Sound and readiness are read from the state fields S1 adds (`audio`, `phase`)
and are skipped while those fields do not exist yet."""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import paths

AUDIO_EXT = {".wav", ".flac", ".mp3", ".aiff", ".aif", ".ogg", ".m4a"}


def _check(cid: str, ok: bool, detail: str, code: str | None = None) -> dict:
    c = {"id": cid, "ok": bool(ok), "detail": detail}
    if code and not ok:
        c["code"] = code                       # a problem.code the UI already has words for
    return c


def _get(base: str, path: str, timeout: float):
    """(status, parsed JSON or None, error text or None) for one GET."""
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path), timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw) if raw else None, None
            except json.JSONDecodeError:
                return r.status, None, None
    except urllib.error.HTTPError as e:
        return e.code, None, f"HTTP {e.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        return 0, None, f"{type(reason).__name__}: {reason}"


def propresenter_checks(pp_cfg: dict) -> list[dict]:
    """Reach ProPresenter, then find the configured message in it."""
    host, port = pp_cfg.get("host", "127.0.0.1"), int(pp_cfg.get("port", 50001))
    mid, timeout = str(pp_cfg.get("message_id", "Live Translation")), float(pp_cfg.get("timeout_s", 1.5))
    base = f"http://{host}:{port}"
    code, ver, err = _get(base, "/version", timeout)
    if code != 200:
        return [_check("propresenter", False,
                       f"Cannot reach ProPresenter at {host}:{port} ({err or f'HTTP {code}'}). Is ProPresenter "
                       f"open, with Network turned on in its settings, on port {port}?", "pp_unreachable")]
    name = (ver or {}).get("name") or "ProPresenter"
    out = [_check("propresenter", True, f"{name} answered at {host}:{port}")]
    code, msgs, err = _get(base, "/v1/messages", timeout)
    if code != 200 or not isinstance(msgs, list):
        out.append(_check("pp_message", False, f"Could not list ProPresenter messages ({err or f'HTTP {code}'})",
                          "pp_message_missing"))
        return out
    names, ids = [], set()
    for m in msgs:
        i = (m or {}).get("id") or {}
        names.append(str(i.get("name", "")))
        ids |= {str(i.get("name")), str(i.get("uuid")), str(i.get("index"))}
    if mid in ids:
        out.append(_check("pp_message", True, f"Message \"{mid}\" found"))
    else:
        have = ", ".join(f'"{n}"' for n in names if n) or "none"
        out.append(_check("pp_message", False, f"ProPresenter has no message named \"{mid}\" (it has: {have}). "
                                               f"Create it under Messages, with a text token.", "pp_message_missing"))
    return out


def _existing(p: pathlib.Path) -> pathlib.Path:
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def local_checks(live) -> list[dict]:
    lc, root = live.lc, live.root
    out = []
    services = paths.services_dir(lc["transcript_log_dir"], root)
    free_gb = shutil.disk_usage(_existing(services)).free / 1e9
    need = float(lc.get("preflight_min_free_gb", 2.0))
    out.append(_check("disk", free_gb >= need,
                      f"{free_gb:.1f} GB free" + ("" if free_gb >= need else f"; at least {need:g} GB is needed. "
                                                  "Empty the Trash or delete old files."), "disk_full"))
    try:
        services.mkdir(parents=True, exist_ok=True)
        probe = services / ".preflight"
        probe.write_text("ok")
        probe.unlink()
        out.append(_check("logs", True, f"Transcripts are saved in {services}"))
    except OSError as e:
        out.append(_check("logs", False, f"Cannot save transcripts in {services}: {e}"))
    py = paths.engine_python(lc["stt_venv"], root)
    ok = py.is_file() and os.access(py, os.X_OK)
    out.append(_check("engine", ok, "Speech recognition is installed" if ok else
                      f"Speech recognition is not installed (missing {py})", None if ok else "models_missing"))
    gl = paths.resource(live.cfg["paths"]["glossary"], root)
    out.append(_check("glossary", gl.is_file(), "Glossary found" if gl.is_file() else f"Glossary missing: {gl}"))
    return out


def state_checks(st: dict) -> list[dict]:
    """Health the other sessions publish in the state (S1 `phase`/`audio`)."""
    out = []
    phase = st.get("phase")
    if phase is not None:
        if phase == "needs_setup":
            out.append(_check("models", False, "The translation models are not installed yet. Open Setup.",
                              "models_missing"))
        elif phase == "error":
            out.append(_check("models", False, f"The app reported an error: {st.get('status', '')}"))
        else:
            out.append(_check("models", True, f"Models: {phase}"))
    audio = st.get("audio")
    if isinstance(audio, dict):
        dev = audio.get("device") or "the sound input"
        if audio.get("ok"):
            heard = audio.get("speech") or (audio.get("rms_db") is not None and audio["rms_db"] > -60)
            out.append(_check("sound", True, f"Sound is coming in from {dev}" if heard
                              else f"{dev} is connected; no sound heard yet"))
        else:
            out.append(_check("sound", False, f"No sound from {dev}. Check the cable and the mixer.", "no_sound"))
    return out


def run(live) -> dict:
    """The checklist; also stored as the `preflight` state field."""
    checks = local_checks(live) + propresenter_checks(live.cfg.get("propresenter") or {})
    try:
        checks += state_checks(live.state())
    except Exception as e:                    # noqa: BLE001
        checks.append(_check("state", False, f"Could not read the app state: {e}"))
    res = {"checks": checks, "ok": all(c["ok"] for c in checks), "t": round(time.time(), 3)}
    live._preflight = res
    return res


# --- rehearsal -------------------------------------------------------------------------------
def samples(root) -> list[pathlib.Path]:
    d = paths.samples_dir(root)
    return sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXT) if d.is_dir() else []


def find_sample(root, name: str | None) -> pathlib.Path | None:
    """A rehearsal recording by file name or stem (only from the samples folder); default: the first."""
    have = samples(root)
    if not name:
        return have[0] if have else None
    return next((p for p in have if name in (p.name, p.stem)), None)


def rehearse(live, sample: pathlib.Path, timeout_s: float | None = None) -> threading.Thread:
    """Play a recording through the whole chain as if it were the live input, then stop.
    The file is used for this run only: the next Start listens to the sound input again."""
    prev = getattr(live.args, "file", None)
    live.args.file = str(sample)
    try:
        live.start()
    finally:
        live.args.file = prev                  # the engine already has the file; Start uses the mic
    live._log_event({"event": "rehearsal", "sample": sample.name})
    if timeout_s is None:
        try:
            import soundfile as sf
            timeout_s = sf.info(str(sample)).duration + 180
        except Exception:                      # noqa: BLE001
            timeout_s = 3 * 3600

    log = live.log_path

    def ours():                                # a later Start owns the service: leave it alone
        return live.running and live.log_path == log

    def finish():
        deadline = time.time() + timeout_s
        while ours() and not live.eof.is_set() and time.time() < deadline:
            time.sleep(0.25)
        if ours() and live.eof.is_set():
            time.sleep(float(live.lc.get("hold_s", 1.2)) + 2.0)    # the last clause is shown
        if ours():
            live.stop()
    th = threading.Thread(target=finish, daemon=True, name="rehearsal")
    th.start()
    return th
