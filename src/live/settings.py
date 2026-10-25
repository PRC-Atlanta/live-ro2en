"""Layered settings, the user's word lists and today's names (session terms). Owned by S3.

  base       config.yaml in dev, the bundled defaults.yaml in the app (paths.base_config)
  user       <LIVE_HOME>/settings.yaml: schema_version + the exposed keys the user changed
             (only those) + `spellings:` additions. Written atomically, migrated on load.
  lists      bundled glossary.csv / keywords_en.txt / config `spellings`, plus the user's
             additions files next to settings.yaml (paths.user_glossary, paths.user_keywords)
  session    today's names and terms (POST /api/terms): glossary, spellings and the names
             the Romanian recogniser boosts, applied live; kept for this run unless saved.

Only the keys in FIELDS can be read or written through the API ("hidden keys are rejected"):
model ids, venvs, chunk internals, `paths`, `google`, `bible_testset` are never exposed.
Mixed into live.app.Live as SettingsMixin."""
from __future__ import annotations

import copy
import csv
import io
import json
import os
import pathlib
import re
import tempfile
import threading
import time

import yaml

import names as namelist
import paths
from textnorm import merge_spellings, name_variants
from translate import read_glossary

SCHEMA_VERSION = 1

# apply: now = used from the next phrase; start = from the next Start (restart_required while
# live); launch = a default for the next app launch (the live switch is its own control).
_HOST = r"[A-Za-z0-9][A-Za-z0-9.\-]*"          # a name or IPv4; the port is its own field
FIELDS: dict[str, dict] = {
    # Basic
    "live.input_device":       {"group": "basic", "type": "device", "apply": "start"},
    "live.input_gain_db":      {"group": "basic", "type": "num", "min": -12, "max": 30, "apply": "now", "default": 0},
    "live.input_gain_auto":    {"group": "basic", "type": "bool", "apply": "now", "default": True},
    "live.language_mode":      {"group": "basic", "type": "enum", "choices": ["auto", "ro-en", "en-ro"], "apply": "launch"},
    "live.profile":            {"group": "basic", "type": "enum", "choices": "@profiles", "apply": "launch"},
    "live.autostart":          {"group": "basic", "type": "bool", "apply": "launch", "default": False},
    # ProPresenter
    "propresenter.enabled":    {"group": "propresenter", "type": "bool", "apply": "now"},
    "propresenter.host":       {"group": "propresenter", "type": "str", "pattern": _HOST, "apply": "now"},
    "propresenter.port":       {"group": "propresenter", "type": "int", "min": 1, "max": 65535, "apply": "now"},
    "propresenter.message_id": {"group": "propresenter", "type": "str", "apply": "now"},
    "propresenter.token_name": {"group": "propresenter", "type": "str", "apply": "now"},
    "propresenter.max_lines":  {"group": "propresenter", "type": "int", "min": 1, "max": 3, "apply": "now"},
    "propresenter.max_chars_per_line": {"group": "propresenter", "type": "int", "min": 20, "max": 80, "apply": "now"},
    "propresenter.stage_message": {"group": "propresenter", "type": "bool", "apply": "now"},
    "propresenter.health_every_s": {"group": "propresenter", "type": "int", "min": 0, "max": 60, "apply": "launch"},
    # the captions display (a window, and optionally a view-only page on the local network)
    "display.size":            {"group": "propresenter", "type": "enum", "choices": ["medium", "large", "x-large"], "apply": "now"},
    "display.lines":           {"group": "propresenter", "type": "int", "min": 1, "max": 3, "apply": "now"},
    "display.colors":          {"group": "propresenter", "type": "enum", "choices": ["white_on_black", "yellow_on_black", "black_on_white"], "apply": "now"},
    "display.network":         {"group": "propresenter", "type": "bool", "apply": "now"},
    "display.show_hidden":     {"group": "propresenter", "type": "bool", "apply": "now"},
    "display.remote":          {"group": "propresenter", "type": "bool", "apply": "now"},
    # Advanced
    "live.input_gain_target_db": {"group": "advanced", "type": "num", "min": -30, "max": -10, "apply": "now", "default": -20},
    "live.vad_threshold":      {"group": "advanced", "type": "num", "min": 0.1, "max": 0.95, "apply": "start"},
    "live.hold_min_words":     {"group": "advanced", "type": "int", "min": 0, "max": 12, "apply": "now"},
    "live.hold_s":             {"group": "advanced", "type": "num", "min": 0, "max": 5, "apply": "now"},
    "live.phrase_min_words":   {"group": "advanced", "type": "int", "min": 0, "max": 12, "apply": "now"},
    "live.hold_dangling":      {"group": "advanced", "type": "bool", "apply": "now"},
    "live.hold_slow_s":        {"group": "advanced", "type": "num", "min": 0, "max": 10, "apply": "now"},
    "live.overlap_trim":       {"group": "advanced", "type": "bool", "apply": "now"},
    "live.song_detect":        {"group": "advanced", "type": "bool", "apply": "now", "default": False},
    "live.song_symbol":        {"group": "advanced", "type": "enum", "choices": ["♪", ""], "apply": "now", "default": "♪"},
    "live.sentence_pause_s":   {"group": "advanced", "type": "num", "min": 0, "max": 3, "apply": "now"},
    "live.caption_min_line_s": {"group": "advanced", "type": "num", "min": 0, "max": 8, "apply": "now"},
    "live.caption_reveal":     {"group": "advanced", "type": "bool", "apply": "now"},
    "live.caption_reveal_wps": {"group": "advanced", "type": "num", "min": 1, "max": 8, "apply": "now"},
    "live.partial_every_s":    {"group": "advanced", "type": "num", "min": 0, "max": 5, "apply": "start"},
    "live.lag_warn_s":         {"group": "advanced", "type": "num", "min": 2, "max": 30, "apply": "now"},
    "live.gate_min_real":      {"group": "advanced", "type": "num", "min": 0, "max": 0.9, "apply": "now"},
    "live.keep_both_translators": {"group": "advanced", "type": "enum", "choices": ["auto", True, False], "apply": "now"},
    "live.engine_restarts":    {"group": "advanced", "type": "int", "min": 0, "max": 5, "apply": "now"},
    "ui.show_latency":         {"group": "advanced", "type": "bool", "apply": "now", "default": False},
    "ui.show_counts":          {"group": "advanced", "type": "bool", "apply": "now", "default": False},
}
MAX_ITEMS, MAX_LEN = 500, 120              # per list, per word / term
MAX_GLOSSARY = 2000                         # the user's saved glossary rows


# --- nested dict helpers ------------------------------------------------------------------
def get(cfg: dict, key: str, default=None):
    cur = cfg
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return FIELDS.get(key, {}).get("default", default)
        cur = cur[part]
    return cur


def put(cfg: dict, key: str, value):
    *head, last = key.split(".")
    cur = cfg
    for part in head:
        cur = cur.setdefault(part, {})
    cur[last] = value


def drop(cfg: dict, key: str):
    """Remove key and any section it leaves empty."""
    *head, last = key.split(".")
    chain, cur = [], cfg
    for part in head:
        if not isinstance(cur.get(part), dict):
            return
        chain.append((cur, part))
        cur = cur[part]
    cur.pop(last, None)
    for parent, part in reversed(chain):
        if not parent[part]:
            del parent[part]


def deep_merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in (b or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else copy.deepcopy(v)
    return out


# --- files --------------------------------------------------------------------------------
def atomic_write(path: pathlib.Path, text: str):
    """Write via a temp file in the same folder + fsync + rename: a crash or a full disk
    leaves the old file, never half a file."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_lines(path) -> list[str]:
    """A word list: one entry per line, blank lines and # comments skipped."""
    p = pathlib.Path(path) if path else None
    if p is None or not p.exists():
        return []
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]


def write_lines(path, words: list[str], header: str):
    atomic_write(path, "".join(f"# {h}\n" for h in header.splitlines()) + "".join(f"{w}\n" for w in words))


def write_glossary(path, rows: list[dict]):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=["ro", "en", "note"], lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({"ro": r["ro"], "en": r["en"], "note": r.get("note") or ""})
    atomic_write(path, buf.getvalue())


# --- validation ---------------------------------------------------------------------------
def _choices(spec: dict, cfg: dict) -> list:
    c = spec.get("choices")
    return list(cfg.get("profiles") or {}) if c == "@profiles" else c


def check_value(key: str, value, cfg: dict):
    """(True, normalised value) or (False, reason)."""
    spec = FIELDS[key]
    t = spec["type"]
    isnum = isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "bool":
        return (True, value) if isinstance(value, bool) else (False, "must be true or false")
    if t == "enum":
        ch = _choices(spec, cfg)
        ok = any(value == c and type(value) is type(c) for c in ch)     # True is not 1 here
        return (True, value) if ok else (False, f"must be one of {ch}")
    if t == "device":
        if value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0):
            return True, value
        if isinstance(value, str) and value.strip() and len(value) <= 200:
            return True, value.strip()
        return False, "must be a device name, an index or null (system default)"
    if t in ("int", "num"):
        if not isnum or (t == "int" and (isinstance(value, float) and not value.is_integer())) or value != value:
            return False, f"must be a{'n integer' if t == 'int' else ' number'}"
        value = int(value) if t == "int" else float(value)
        if not spec["min"] <= value <= spec["max"]:
            return False, f"must be between {spec['min']} and {spec['max']}"
        return True, value
    if t == "str":
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            return False, "must be a non-empty text"
        value = value.strip()
        if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
            return False, "must be a host name or an IP address"
        return True, value
    raise AssertionError(t)


def clean_word(w) -> str | None:
    if not isinstance(w, str):
        return None
    w = " ".join(w.split())
    return w if w and len(w) <= MAX_LEN and not w.startswith("#") else None


# --- user layer ---------------------------------------------------------------------------
def _v0_to_v1(d: dict) -> dict:
    """Files written before schema_version existed (or a config.yaml copied in by hand):
    the same nested layout; `clean` keeps the exposed keys and the spellings."""
    return d


MIGRATIONS = {0: _v0_to_v1}


def migrate(d: dict) -> tuple[dict, int]:
    d = dict(d)
    v = d.pop("schema_version", 0)
    v = v if isinstance(v, int) and v >= 0 else 0
    while v < SCHEMA_VERSION:
        d = MIGRATIONS[v](d)
        v += 1
    return d, v


def clean(d: dict, cfg: dict) -> tuple[dict, list[str]]:
    """Only exposed keys with valid values, plus spellings (str -> str). Returns (user, notes).
    Keys no longer in FIELDS (e.g. a saved `live.en_recognizer: whistle` from before Whistle was
    removed) are dropped silently, and leave the file at the next save."""
    out, notes = {}, []
    for key in FIELDS:
        if not _has(d, key):
            continue
        ok, val = check_value(key, get(d, key), cfg)
        if ok:
            put(out, key, val)
        else:
            notes.append(f"{key}: {val}; using the default")
    sp = d.get("spellings")
    if isinstance(sp, dict):
        good = {clean_word(a): clean_word(b) for a, b in sp.items() if clean_word(a) and clean_word(b)}
        if good:
            out["spellings"] = good
    return out, notes


def _has(d: dict, key: str) -> bool:
    cur = d
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def read_user(path, cfg: dict) -> tuple[dict, list[str]]:
    """The user's overrides, migrated and cleaned. An unreadable file is moved aside (kept as
    settings.yaml.bad-<time>) so the app still starts, on the defaults."""
    path = pathlib.Path(path)
    if not path.exists():
        return {}, []
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(d, dict):
            raise ValueError("not a mapping")
    except (yaml.YAMLError, ValueError, UnicodeDecodeError) as e:
        bad = path.with_name(f"{path.name}.bad-{time.strftime('%Y%m%d-%H%M%S')}")
        os.replace(path, bad)
        return {}, [f"{path.name} could not be read ({type(e).__name__}); moved to {bad.name}, using the defaults"]
    newer = isinstance(d.get("schema_version"), int) and d["schema_version"] > SCHEMA_VERSION
    d, _ = migrate(d)
    user, notes = clean(d, cfg)
    if newer:
        notes.append(f"{path.name} is from a newer version; only the settings this version knows are used")
    return user, notes


def dump_user(user: dict) -> str:
    return ("# Your changes to the Live RO2EN defaults. Written by the app (Settings); only\n"
            "# the settings you changed are listed.\n" +
            yaml.safe_dump({"schema_version": SCHEMA_VERSION, **user}, allow_unicode=True, sort_keys=False))


def apply_user(base: dict, user: dict) -> dict:
    """The effective config: base with the user's values on top. A user value for a `live`
    key also replaces that key in every profile, so the user's choice wins over a profile."""
    cfg = deep_merge(base, {k: v for k, v in user.items() if k != "spellings"})
    for key in FIELDS:
        if key.startswith("live.") and _has(user, key):
            for prof in (cfg.get("profiles") or {}).values():
                if isinstance(prof, dict) and key[5:] in prof:
                    prof[key[5:]] = get(user, key)
    cfg["spellings"] = merge_spellings(base.get("spellings"), user.get("spellings"))
    return cfg


def load_config(base_path=None, user_path=None, root: pathlib.Path = paths.REPO) -> tuple[dict, dict, list[str]]:
    """(effective cfg, base cfg, notes) for app.py main and for tests."""
    base = yaml.safe_load(open(base_path or paths.base_config(root), encoding="utf-8"))
    user, notes = read_user(user_path or paths.settings_file(root), base)
    return apply_user(base, user), base, notes


# --- session terms ------------------------------------------------------------------------
def parse_terms(lines) -> list[dict]:
    """'ro = en' pairs or bare names (kept as they are in both languages), one per line."""
    if isinstance(lines, str):
        lines = [lines]
    rows: dict[str, dict] = {}
    for line in (l for chunk in lines if isinstance(chunk, str) for l in chunk.splitlines()):
        line = " ".join(line.split())
        if not line or line.startswith("#"):
            continue
        ro, _, en = (s.strip() for s in line.partition("=")) if "=" in line else (line, "", line)
        ro, en = ro or en, en or ro
        if ro and clean_word(ro) and clean_word(en):          # '#...' would read back as a comment
            rows[ro.casefold()] = {"ro": ro, "en": en}
    return list(rows.values())[:MAX_ITEMS]


def term_spellings(rows: list[dict]) -> dict[str, str]:
    """Spellings for today's names: only capitalised terms (names, not common words), so
    'Ionut Pop' -> 'Ionuț Pop' but a lower-case 'mântuire = salvation' adds none."""
    return merge_spellings(*(name_variants(s) for r in rows for s in dict.fromkeys((r["ro"], r["en"]))
                             if s[:1].isupper()))


# --- the mixin ----------------------------------------------------------------------------
class SettingsMixin:
    def _mixin_init(self):
        self._settings_lock = threading.RLock()
        self.settings_path = pathlib.Path(getattr(self.args, "settings", None) or paths.settings_file(self.root))
        self._base_cfg = copy.deepcopy(getattr(self.args, "settings_base", None) or self.cfg)
        self._user, notes = read_user(self.settings_path, self._base_cfg)
        self.settings_notes = list(getattr(self.args, "settings_notes", None) or []) + notes
        self.session_terms: list[dict] = []
        self._session_lines: list[str] = []
        self.gloss.load_user(paths.user_glossary(self.root))
        self._apply_lists()
        self.register_state(lambda: {"session_terms_n": len(self.session_terms)})

    # --- settings -------------------------------------------------------------------------
    def settings_view(self, changed: dict | None = None) -> dict:
        changed = changed or {}
        fields = {k: {**{a: b for a, b in s.items() if a != "pattern"},
                      **({"choices": _choices(s, self.cfg)} if "choices" in s else {})} for k, s in FIELDS.items()}
        return {"schema_version": SCHEMA_VERSION,
                "values": {k: get(self.cfg, k) for k in FIELDS},
                "defaults": {k: get(self._base_cfg, k) for k in FIELDS},
                "overrides": [k for k in FIELDS if _has(self._user, k)],
                "fields": fields,
                "applies": {k: FIELDS[k]["apply"] for k in changed},
                "restart_required": bool(self.running and any(FIELDS[k]["apply"] == "start" for k in changed)),
                "notes": list(self.settings_notes)}

    def put_settings(self, body: dict, by_hand: bool = True) -> tuple[int, dict]:
        """{"values": {key: value}} (or the keys at the top level), or {"reset": true}.
        `by_hand`: a person changed these (not a reset or an imported file), so a gain set by
        hand switches Auto gain off as the knob does."""
        if body.get("reset") is True:
            vals = {k: get(self._base_cfg, k) for k in FIELDS if _has(self._user, k)}
        else:
            vals = body.get("values", {k: v for k, v in body.items() if k != "reset"})
            if not isinstance(vals, dict):
                return 400, {"error": "invalid", "detail": {"values": "must be an object"}}
        hidden = sorted(k for k in vals if k not in FIELDS)
        if hidden:
            return 400, {"error": "rejected", "keys": hidden,
                         "detail": "these settings cannot be changed here"}
        bad, ok = {}, {}
        for k, v in vals.items():
            good, val = check_value(k, v, self.cfg)
            if good:
                ok[k] = val
            else:
                bad[k] = val
        if bad:
            return 400, {"error": "invalid", "detail": bad}
        with self._settings_lock:
            if by_hand and body.get("reset") is not True and get(self.cfg, "live.input_gain_auto"):
                eng = getattr(self, "_eng_gain", None)
                if ok.get("live.input_gain_auto") is False and "live.input_gain_db" not in ok \
                        and isinstance(eng, (int, float)):
                    good, val = check_value("live.input_gain_db", round(eng), self.cfg)
                    if good:                            # Auto gain off keeps the gain it reached
                        ok["live.input_gain_db"] = val
                elif "live.input_gain_db" in ok and "live.input_gain_auto" not in ok:
                    ok["live.input_gain_auto"] = False  # a gain set by hand switches Auto gain off
            changed = {k: v for k, v in ok.items() if get(self.cfg, k) != v}
            user = copy.deepcopy(self._user)
            for k, v in changed.items():
                if v == get(self._base_cfg, k):
                    drop(user, k)                     # back to the default: no override kept
                else:
                    put(user, k, v)
            if user != self._user:
                atomic_write(self.settings_path, dump_user(user))
                self._user = user
            self._apply_settings(changed)
        self._broadcast()
        return 200, self.settings_view(changed)

    def _apply_settings(self, changed: dict):
        """Put the new values into the running service (cfg, profile cfg, ProPresenter)."""
        eff = apply_user(self._base_cfg, self._user)
        for k in changed:
            put(self.cfg, k, get(eff, k))
            if k.startswith("live."):
                sub = k[5:]
                for name, prof in (self.cfg.get("profiles") or {}).items():
                    if isinstance(prof, dict) and sub in prof:
                        prof[sub] = ((eff.get("profiles") or {}).get(name) or {}).get(sub, get(eff, k))
                prof = (eff.get("profiles") or {}).get(self.profile) or {}
                self.lc[sub] = prof[sub] if sub in prof else get(eff, k)
        if "live.language_mode" in changed and not self.running:
            self.lang.set_mode(get(self.cfg, "live.language_mode"))
        if "live.caption_min_line_s" in changed and hasattr(self.caps, "min_line_s"):
            self.caps.min_line_s = float(get(self.cfg, "live.caption_min_line_s") or 0)
        if "live.caption_reveal" in changed and hasattr(self.caps, "reveal"):
            self.caps.reveal = bool(get(self.cfg, "live.caption_reveal"))   # from the next phrase
        if "live.caption_reveal_wps" in changed and hasattr(self.caps, "reveal_wps"):
            self.caps.reveal_wps = float(get(self.cfg, "live.caption_reveal_wps") or 3)
        if any(k.startswith("display.") for k in changed) and hasattr(self, "_display_changed"):
            self._display_changed(changed)
        if "propresenter.enabled" in changed and hasattr(self, "_set_pp_enabled"):
            self._set_pp_enabled(bool(self.cfg["propresenter"].get("enabled", True)))
        if any(k.startswith("propresenter.") for k in changed):
            pc = self.cfg["propresenter"]
            for attr, val in (("base", f"http://{pc['host']}:{pc['port']}"), ("message_id", pc["message_id"]),
                              ("token_name", pc["token_name"]), ("stage", pc.get("stage_message", False)),
                              ("_last", None)):
                if hasattr(self.pp, attr):
                    setattr(self.pp, attr, val)
            for attr, val in (("max_chars", pc["max_chars_per_line"]), ("max_lines", pc["max_lines"])):
                if hasattr(self.caps, attr):
                    setattr(self.caps, attr, val)

    def import_config(self, body: dict) -> tuple[int, dict]:
        """Take the exposed settings and the spellings from an old config.yaml: {"text": "..."}
        (the file's contents) or {"path": "/…/config.yaml"}."""
        text = body.get("text")
        if text is None and isinstance(body.get("path"), str):
            p = pathlib.Path(body["path"]).expanduser()
            if p.suffix not in (".yaml", ".yml") or not p.is_file() or p.stat().st_size > 1_000_000:
                return 400, {"error": "invalid", "detail": {"path": "must be a .yaml file under 1 MB"}}
            text = p.read_text(encoding="utf-8")
        if not isinstance(text, str):
            return 400, {"error": "invalid", "detail": {"text": "send the config.yaml text or its path"}}
        try:
            d = yaml.safe_load(text) or {}
            if not isinstance(d, dict):
                raise ValueError
        except (yaml.YAMLError, ValueError):
            return 400, {"error": "invalid", "detail": {"text": "not a YAML settings file"}}
        d, _ = migrate(d)
        found, notes = clean(d, self.cfg)
        vals = {k: get(found, k) for k in FIELDS if _has(found, k) and get(found, k) != get(self.cfg, k)}
        code, out = self.put_settings({"values": vals}, by_hand=False) if vals else (200, self.settings_view())
        if code != 200:
            return code, out
        base_sp = self._base_cfg.get("spellings") or {}
        new_sp = {a: b for a, b in (found.get("spellings") or {}).items() if base_sp.get(a) != b}
        if new_sp:
            sp_code, sp_out = self.put_spellings({"user": {**self.user_spellings(), **new_sp}})
            if sp_code != 200:
                notes.append(f"spellings not imported: {sp_out['detail']['user']}")
                new_sp = {}
        return 200, {**self.settings_view(vals), "imported": sorted(vals), "spellings_added": len(new_sp),
                     "ignored": notes}

    # --- word lists ---------------------------------------------------------------------
    def _bundled_keywords(self) -> list[str]:
        return read_lines(paths.resource("keywords_en.txt", self.root))

    def user_keywords(self) -> list[str]:
        return read_lines(paths.user_keywords(self.root))

    def user_spellings(self) -> dict:
        return dict(self._user.get("spellings") or {})

    def keywords_view(self) -> dict:
        return {"bundled": self._bundled_keywords(), "user": self.user_keywords(),
                "session": self._session_words()}

    def spellings_view(self) -> dict:
        return {"bundled": dict(self._base_cfg.get("spellings") or {}), "user": self.user_spellings(),
                "session": term_spellings(self.session_terms)}

    def put_keywords(self, body: dict) -> tuple[int, dict]:
        """{"user": [...]} replaces the user's names & words (the bundled list stays)."""
        if "user" not in body and "words" not in body:
            return 200, self.keywords_view()
        words = body.get("user", body.get("words"))
        if not isinstance(words, list) or len(words) > MAX_ITEMS:
            return 400, {"error": "invalid", "detail": {"user": f"must be a list of at most {MAX_ITEMS} words"}}
        bad = [w for w in words if clean_word(w) is None]
        if bad:
            return 400, {"error": "invalid", "detail": {"user": f"not a word or phrase (max {MAX_LEN} characters): {bad[:5]}"}}
        with self._settings_lock:
            write_lines(paths.user_keywords(self.root), list(dict.fromkeys(clean_word(w) for w in words)),
                        "Names & words you added. Edited in Settings → Names & Spellings.")
            self._apply_lists()
        return 200, self.keywords_view()

    def put_spellings(self, body: dict) -> tuple[int, dict]:
        """{"user": {variant: preferred}} replaces the user's preferred spellings."""
        if "user" not in body:
            return 200, self.spellings_view()
        sp = body["user"]
        if not isinstance(sp, dict) or len(sp) > MAX_ITEMS:
            return 400, {"error": "invalid", "detail": {"user": f"must be an object of at most {MAX_ITEMS} entries"}}
        bad = [a for a, b in sp.items() if clean_word(a) is None or clean_word(b) is None]
        if bad:
            return 400, {"error": "invalid", "detail": {"user": f"empty or too long: {bad[:5]}"}}
        with self._settings_lock:
            user = copy.deepcopy(self._user)
            good = {clean_word(a): clean_word(b) for a, b in sp.items() if clean_word(a) != clean_word(b)}
            if good:
                user["spellings"] = good
            else:
                user.pop("spellings", None)
            if user != self._user:
                atomic_write(self.settings_path, dump_user(user))
                self._user = user
            self._apply_lists()
        return 200, self.spellings_view()

    # --- today's names & terms ----------------------------------------------------------
    def set_terms(self, body: dict) -> tuple[int, dict]:
        """{"lines": [...], "save": bool}: replaces today's terms, live; save also adds them to
        the user's glossary, names & words and spellings for next time."""
        lines = body.get("lines", [])
        if isinstance(lines, str):
            lines = [lines]
        if not isinstance(lines, list) or not all(isinstance(l, str) for l in lines):
            return 400, {"error": "invalid", "detail": {"lines": "must be a list of lines"}}
        rows = parse_terms(lines)
        with self._settings_lock:
            self.session_terms = rows
            self._session_lines = [f"{r['ro']} = {r['en']}" if r["ro"] != r["en"] else r["ro"] for r in rows]
            if body.get("save") is True and rows:
                self._save_terms(rows)
            self._apply_lists()
        self._broadcast()
        return 200, self.state()

    def terms_view(self) -> dict:
        return {"lines": list(self._session_lines), "n": len(self.session_terms)}

    def _save_terms(self, rows: list[dict]):
        gp = paths.user_glossary(self.root)
        merged = {r["ro"].casefold(): r for r in read_glossary(gp)}
        merged.update({r["ro"].casefold(): {**r, "note": "saved name"} for r in rows})
        write_glossary(gp, list(merged.values())[-MAX_GLOSSARY:])
        self.gloss.load_user(gp)
        new = {r["en"] for r in rows}
        words = [w for w in self.user_keywords() if w not in new] + [r["en"] for r in rows]
        write_lines(paths.user_keywords(self.root), list(dict.fromkeys(words))[-MAX_ITEMS:],
                    "Names & words you added. Edited in Settings → Names & Spellings.")
        sp = term_spellings(rows)
        if sp:                                     # newest kept when a list is full (stays editable)
            user = copy.deepcopy(self._user)
            old = {a: b for a, b in (user.get("spellings") or {}).items() if a not in sp}
            user["spellings"] = dict(list({**old, **sp}.items())[-MAX_ITEMS:])
            atomic_write(self.settings_path, dump_user(user))
            self._user = user

    def _session_words(self) -> list[str]:
        return list(dict.fromkeys(r["en"] for r in self.session_terms))

    def _apply_lists(self):
        """Push the merged lists into the running service: glossary session layer, the
        spellings the pipeline applies and the names the Romanian recogniser boosts (next
        engine start via ro_names, the running engine via the `keywords` command)."""
        self.gloss.set_session(self.session_terms)
        # the names typed in Settings → Names & words (Whistle used to be their only reader):
        # boosted and repaired like today's names
        self.gloss.extra_names = [w.strip() for w in self.user_keywords() if w.strip()[:1].isupper()]
        self.cfg["spellings"] = merge_spellings(self._base_cfg.get("spellings"), self._user.get("spellings"),
                                                term_spellings(self.session_terms))
        words = list(dict.fromkeys(self.user_keywords() + self._session_words()))
        ro = self.cfg["ro_names"] = namelist.ro_names(self.gloss)
        eng = getattr(self, "engine", None)
        if eng is not None and eng.poll() is None:
            try:
                eng.stdin.write(json.dumps({"cmd": "keywords", "words": words, "ro": ro}) + "\n")
                eng.stdin.flush()
            except (OSError, ValueError):
                pass                               # the engine is exiting; the next start has them
