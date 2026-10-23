"""After the sermon: summary stats, rating, transcript export (txt/srt/docx), the per-service
index (logs/services/index.csv) and the diagnostics zip. Owned by S9.

Everything here reads the jsonl the pipeline already writes (one record per clause, with an
`outcome`, plus `event` records), so a report can be rebuilt from any old log.
`SessionMixin` adds the `elapsed_s`, `first_word_ok_pct` and `preflight` state fields."""
from __future__ import annotations

import collections
import csv
import datetime as dt
import fnmatch
import io
import json
import os
import pathlib
import platform
import re
import shutil
import statistics
import sys
import threading
import time
import types
import zipfile
from xml.sax.saxutils import escape

import paths

FIRST_WORD_OK_S = 6.0                 # the rehearsal gate's threshold (e2e.py `gate`)
INDEX_FIELDS = ["log", "date", "kind", "elapsed_s", "phrases", "failed", "filtered", "first_word_ok_pct",
                "latency_median", "pp_errors", "corrections", "score", "tags", "note"]
LOG_NAME = re.compile(r"^[\w.-]+\.jsonl$")


# --- reading a service log -------------------------------------------------------------------
def load(path) -> list[dict]:
    """Every record of a jsonl log; a torn last line (the app was killed) is skipped."""
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict):
                out.append(r)
    return out


def _epoch(r: dict) -> float | None:
    """When a record happened: the caption's display time, else its end, else `time`."""
    for k in ("t_display", "t_end"):
        if isinstance(r.get(k), (int, float)):
            return float(r[k])
    try:
        return dt.datetime.fromisoformat(r["time"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def log_start(path) -> float | None:
    """The run's start, from the log's name (`%Y-%m-%d_%H%M%S[_n].jsonl`, set by the app at start)."""
    try:
        return dt.datetime.strptime(pathlib.Path(path).stem[:17], "%Y-%m-%d_%H%M%S").timestamp()
    except ValueError:
        return None


def captions(recs: list[dict]) -> list[dict]:
    """The captions that were shown, in speech order."""
    ok = [r for r in recs if r.get("outcome") == "ok" and "event" not in r and r.get("translation")]
    return sorted(ok, key=lambda r: r.get("t_start") or 0)


# outcomes that are not failures: the operator's Hide/Hold, and output the safety filter dropped
# on purpose (a translator loop or an overlong answer; counted as `filtered`)
OPERATOR = ("paused", "held")
FILTERED = ("mt_loop", "mt_overlong")


def summary(recs: list[dict], start: float | None = None, first_word_ok_s: float = FIRST_WORD_OK_S) -> dict:
    """Duration, phrases, delays, failures, ProPresenter errors, corrections and the rating."""
    caps = captions(recs)
    outcomes = collections.Counter(r["outcome"] for r in recs if "outcome" in r and "event" not in r)
    times = [t for t in map(_epoch, recs) if t is not None]
    if start is None:
        starts = [r["t_start"] for r in recs if isinstance(r.get("t_start"), (int, float))]
        start = min(starts) if starts else (min(times) if times else None)
    lat = [r["latency_s"] for r in caps if isinstance(r.get("latency_s"), (int, float))]
    fw = [r["first_word_s"] for r in caps if isinstance(r.get("first_word_s"), (int, float))]
    events = collections.Counter(r["event"] for r in recs if "event" in r)
    rating = next((r for r in reversed(recs) if r.get("event") == "rating"), None)
    return {
        "elapsed_s": round(max(times) - start, 1) if times and start is not None else None,
        "phrases": len(caps),
        "outcomes": dict(outcomes),
        "failed": sum(v for k, v in outcomes.items() if k not in ("ok", *OPERATOR, *FILTERED)),
        "filtered": sum(outcomes[k] for k in FILTERED),
        "latency_median": round(statistics.median(lat), 2) if lat else None,
        "first_word_median": round(statistics.median(fw), 2) if fw else None,
        "first_word_ok_pct": round(100 * sum(x <= first_word_ok_s for x in fw) / len(fw), 1) if fw else None,
        "pp_errors": sum(1 for r in caps if r.get("pp_ok") is False),
        "corrections": events["correct"] + events["retract"],
        "warnings": events["warn"],
        "engine_exits": events["engine_exit"],
        "kind": "rehearsal" if events["rehearsal"] else "service",
        "rating": {"score": rating.get("score"), "tags": rating.get("tags") or [], "note": rating.get("note") or ""}
                  if rating else {"score": None, "tags": [], "note": ""},
    }


# --- exports ---------------------------------------------------------------------------------
def _clock(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _srt_time(s: float) -> str:
    ms = max(0, int(round(s * 1000)))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def _origin(recs: list[dict], start: float | None) -> float:
    caps = captions(recs)
    if start is not None:
        return start
    return min((r.get("t_start") or 0) for r in caps) if caps else 0.0


def _pairs(recs, start):
    """(offset from start, source language, source, translation) per shown caption."""
    t0 = _origin(recs, start)
    for r in captions(recs):
        src = r.get("lang", "ro")
        yield (r.get("t_start", t0) - t0, src, r.get("source", ""), r["translation"])


LANG = {"ro": "RO", "en": "EN"}


def to_txt(recs: list[dict], start: float | None = None, title: str = "") -> str:
    """Bilingual transcript: a timestamp, the words spoken, then the caption shown."""
    out = [title or "Live RO2EN transcript", ""]
    for off, src, s, t in _pairs(recs, start):
        dst = "en" if src == "ro" else "ro"
        out += [f"[{_clock(off)}] {LANG.get(src, src.upper())}: {s}", f"           {LANG[dst]}: {t}", ""]
    return "\n".join(out).rstrip() + "\n"


def to_srt(recs: list[dict], start: float | None = None) -> str:
    """The captions as shown, timed from the start of the service (each lasts until the next
    caption, at most 7 s and at least 1 s)."""
    t0 = _origin(recs, start)
    caps = captions(recs)
    shown = [((r.get("t_display") or r.get("t_end") or t0) - t0, r["translation"]) for r in caps]
    blocks = []
    for i, (on, text) in enumerate(shown):
        nxt = shown[i + 1][0] if i + 1 < len(shown) else on + 4.0
        off = max(on + 1.0, min(nxt, on + 7.0))
        blocks.append(f"{i + 1}\n{_srt_time(on)} --> {_srt_time(off)}\n{text.strip()}\n")
    return "\n".join(blocks)


XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def _xml_ok(text: str) -> str:
    """Drop the characters XML 1.0 forbids (a stray control character makes Word refuse the file)."""
    return XML_BAD.sub("", str(text))


def _cell(text: str) -> str:
    """Typed text in index.csv: a leading = + - @ would run as a formula in Excel or Numbers."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def to_docx(recs: list[dict], start: float | None = None, title: str = "") -> bytes:
    """A minimal Word document (stdlib only): a heading, then one timestamped source line and
    its caption per phrase."""
    def para(text, bold=False, grey=False):
        rpr = ("<w:b/>" if bold else "") + ('<w:color w:val="555555"/>' if grey else "")
        rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        return f'<w:p><w:r>{rpr}<w:t xml:space="preserve">{escape(_xml_ok(text))}</w:t></w:r></w:p>'
    body = [para(title or "Live RO2EN transcript", bold=True), para("")]
    for off, src, s, t in _pairs(recs, start):
        body += [para(f"[{_clock(off)}] {s}", grey=True), para(t), para("")]
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f'<w:body>{"".join(body)}</w:body></w:document>')
    types_ = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
              '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
              '<Default Extension="xml" ContentType="application/xml"/>'
              '<Override PartName="/word/document.xml" ContentType="application/'
              'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types_)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _stamp(log) -> str:
    st = log_start(log)
    return dt.datetime.fromtimestamp(st).strftime("%Y-%m-%d %H%M") if st else pathlib.Path(log).stem


def _unique(path: pathlib.Path) -> pathlib.Path:
    n, p = 2, path
    while p.exists():
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        n += 1
    return p


def export(log, fmt: str, out_dir) -> pathlib.Path:
    """Write the transcript of one service log as txt, srt or docx into out_dir."""
    if fmt not in ("txt", "srt", "docx"):
        raise ValueError(f"unknown format {fmt!r}")
    recs, start = load(log), log_start(log)
    title = f"Live RO2EN transcript, {_stamp(log)}"
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _unique(out_dir / f"Live RO2EN transcript {_stamp(log)}.{fmt}")
    if fmt == "docx":
        path.write_bytes(to_docx(recs, start, title))
    else:
        path.write_text(to_txt(recs, start, title) if fmt == "txt" else to_srt(recs, start), encoding="utf-8")
    return path


# --- rating and the per-service index -------------------------------------------------------
TAGS_MAX, NOTE_MAX = 10, 2000


def check_rating(score, tags, note) -> tuple[int, list[str], str]:
    """Validate a rating from the page: score 1-5, a few short tags, a bounded note."""
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
        raise ValueError("score must be a whole number from 1 to 5")
    tags = tags or []
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise ValueError("tags must be a list of strings")
    note = note or ""
    if not isinstance(note, str):
        raise ValueError("note must be a string")
    return score, [t.strip()[:40] for t in tags if t.strip()][:TAGS_MAX], note.strip()[:NOTE_MAX]


def rate(log, index, score, tags=None, note="") -> dict:
    """Record a rating in the service's jsonl and its index.csv row; returns the summary."""
    score, tags, note = check_rating(score, tags, note)
    rec = {"event": "rating", "score": score, "tags": tags, "note": note,
           "time": dt.datetime.now().isoformat(timespec="milliseconds")}
    with open(log, "a+b") as f:
        f.seek(0, os.SEEK_END)
        torn = f.tell() > 0 and (f.seek(-1, os.SEEK_END), f.read(1))[1] != b"\n"
        # a torn last line (the app was killed mid-write) must not swallow the rating
        f.write((b"\n" if torn else b"") + (json.dumps(rec, ensure_ascii=False) + "\n").encode())
    return update_index(index, log)


_index_lock = threading.Lock()


def update_index(index, log) -> dict:
    """Insert or replace this log's row in index.csv (atomic rewrite); returns its summary."""
    log, index = pathlib.Path(log), pathlib.Path(index)
    s = summary(load(log), log_start(log))
    r = s["rating"] or {}
    st = log_start(log)
    row = {"log": log.name,
           "date": dt.datetime.fromtimestamp(st).isoformat(timespec="minutes") if st else "",
           "kind": s["kind"], "elapsed_s": s["elapsed_s"], "phrases": s["phrases"], "failed": s["failed"], "filtered": s["filtered"],
           "first_word_ok_pct": s["first_word_ok_pct"], "latency_median": s["latency_median"],
           "pp_errors": s["pp_errors"], "corrections": s["corrections"], "score": r.get("score"),
           "tags": _cell(";".join(r.get("tags") or [])), "note": _cell(r.get("note") or "")}
    row = {k: "" if v is None else v for k, v in row.items()}
    with _index_lock:
        rows = []
        if index.exists():
            with open(index, newline="", encoding="utf-8") as f:
                rows = [x for x in csv.DictReader(f) if x.get("log") != log.name]
        rows.append(row)
        rows.sort(key=lambda x: x.get("log", ""))
        index.parent.mkdir(parents=True, exist_ok=True)
        tmp = index.with_suffix(".csv.tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=INDEX_FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        os.replace(tmp, index)
    return s


def find_log(services: pathlib.Path, name: str | None, current=None) -> pathlib.Path | None:
    """The log a request means: a named one in the services folder (a bare file name, so a
    request cannot reach outside it), else the current run's, else the newest."""
    if name:
        if not LOG_NAME.match(name):
            return None
        p = services / name
        return p if p.is_file() else None
    if current and pathlib.Path(current).is_file():
        return pathlib.Path(current)
    logs = sorted(services.glob("*.jsonl")) if services.is_dir() else []
    return logs[-1] if logs else None


# --- diagnostics -----------------------------------------------------------------------------
# never in the zip: environment files, keys, certificates, cloud credentials
DENY = (".env", ".env.*", "*.env", "*.key", "*.json.key", "*.pem", "*.p12", "*.pfx", "*.crt", "*.cer",
        "*service-account*", "*service_account*", "*cred*", "*secret*", "*token*", "id_rsa*", "id_ed25519*", "*.keychain*",
        "*.netrc", ".netrc")
SECRET_KEY = re.compile(r"^(\s*-?\s*[\"']?[\w.-]*(?:api[_-]?key|apikey|secret|password|passwd|credential|"
                        r"private[_-]?key|access[_-]?token|auth[_-]?token|bearer)[\w.-]*[\"']?\s*[:=]\s*)(.+)$",
                        re.IGNORECASE | re.MULTILINE)
LOG_TAIL = 5_000_000                   # bytes kept from each big log
SERVICE_LOGS = 5                       # most recent service jsonl files


def is_secret_file(path: pathlib.Path) -> bool:
    name = path.name.lower()
    if any(fnmatch.fnmatch(name, pat) for pat in DENY):
        return True
    try:
        with open(path, "rb") as f:                         # the head only: logs can be large
            head = f.read(65536)
    except OSError:
        return True
    return (b"-----BEGIN" in head and b"PRIVATE KEY" in head) or b'"private_key"' in head


def redact(text: str) -> str:
    """Blank the value of any setting whose name looks like a key, password or token."""
    return SECRET_KEY.sub(lambda m: m.group(1) + "<redacted>", text)


def _tail(path: pathlib.Path, n: int = LOG_TAIL) -> bytes:
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(max(0, size - n))
        return f.read()


def diagnostics(out_dir, logs: pathlib.Path, services: pathlib.Path, settings: list[pathlib.Path],
                state: dict | None = None, extra: dict | None = None) -> pathlib.Path:
    """Zip the logs, the settings (secrets redacted) and a system summary for support.
    Never includes .env, key or credential files."""
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = _unique(out_dir / f"Live RO2EN diagnostics {dt.datetime.now():%Y-%m-%d %H%M}.zip")
    files: dict[str, pathlib.Path] = {}
    if logs.is_dir():
        for p in sorted(logs.iterdir()):
            if p.is_file() and p.suffix == ".log":           # engine.log, app_errors.log
                files[f"logs/{p.name}"] = p
    if services.is_dir():
        for p in sorted(services.glob("*.jsonl"))[-SERVICE_LOGS:]:
            files[f"logs/services/{p.name}"] = p
        if (services / "index.csv").is_file():
            files["logs/services/index.csv"] = services / "index.csv"
    for p in settings:
        if p.is_file():
            files[f"settings/{p.name}"] = p
    du = shutil.disk_usage(out_dir)
    system = {"time": dt.datetime.now().isoformat(timespec="seconds"), "platform": platform.platform(),
              "mac": platform.mac_ver()[0], "machine": platform.machine(), "python": sys.version.split()[0],
              "disk_free_gb": round(du.free / 1e9, 1), **(extra or {})}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for arc, p in files.items():
            if is_secret_file(p):
                continue
            z.writestr(arc, redact(_tail(p).decode("utf-8", "replace")))
        z.writestr("system.json", json.dumps(system, indent=1, default=str))
        if state is not None:
            z.writestr("state.json", redact(json.dumps(state, indent=1, ensure_ascii=False, default=str)))
    return path


# --- live state fields -----------------------------------------------------------------------
class _Tracker:
    """Reads the current run's log incrementally (new lines only) for the live state fields."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset(None)

    def reset(self, path):
        self.path, self.pos, self.start, self.first, self.last = path, 0, log_start(path) if path else None, None, None
        self.fw_n = self.fw_ok = 0

    def update(self, path, ok_s: float):
        if path != self.path:
            self.reset(path)
        try:
            with open(path, "rb") as f:
                f.seek(self.pos)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(b"\n") + 1               # a half-written last line waits for the next call
        self.pos += end
        for line in chunk[:end].splitlines():
            try:
                r = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(r, dict):
                continue
            if isinstance(r.get("t_start"), (int, float)) and self.first is None:
                self.first = r["t_start"]
            t = _epoch(r)
            if t is not None:
                self.last = t if self.last is None else max(self.last, t)
            if r.get("outcome") == "ok" and "event" not in r and isinstance(r.get("first_word_s"), (int, float)):
                self.fw_n += 1
                self.fw_ok += r["first_word_s"] <= ok_s


class SessionMixin:
    """Mixed into live.app.Live (S11 adds it to the bases). Until then routes/session.py
    attaches it to the running Live, so the fields are served either way."""

    def _mixin_init(self):
        if getattr(self, "_session", None) is not None:
            return
        self._session = types.SimpleNamespace(tracker=_Tracker(), was_running=False, indexed=None)
        self._preflight = None
        self.register_state(self._session_state)

    def _session_state(self) -> dict:
        s, path = self._session, self.log_path
        out = {"elapsed_s": None, "first_word_ok_pct": None, "preflight": self._preflight,
               "rehearsal": bool(self.running and path and path == getattr(self, "_rehearsal_log", None))}
        if path:
            ok_s = float(self.lc.get("first_word_ok_s", FIRST_WORD_OK_S))
            with s.tracker.lock:
                tr = s.tracker
                tr.update(path, ok_s)
                start = tr.start or tr.first
                if start is not None:
                    end = time.time() if self.running else (tr.last or start)
                    out["elapsed_s"] = round(max(0.0, end - start), 1)
                if tr.fw_n:
                    out["first_word_ok_pct"] = round(100 * tr.fw_ok / tr.fw_n, 1)
        running = bool(self.running)
        if s.was_running and not running and path and s.indexed != (path, "stopped"):
            s.indexed = (path, "stopped")       # a run just ended: its row goes into index.csv
            try:
                update_index(paths.services_index(self.lc["transcript_log_dir"], self.root), path)
            except Exception:                   # noqa: BLE001 - a report must never break the state feed
                pass
        s.was_running = running
        return out


def attach(live):
    """Give a Live that was built without SessionMixin its state fields (idempotent)."""
    if not isinstance(live, SessionMixin):
        live._session_state = SessionMixin._session_state.__get__(live)
    SessionMixin._mixin_init(live)
    return live
