"""First-run model download and hardware checks (S6).

The orchestrator never touches the network. `Setup` (used by routes/setup.py) spawns this file
as a separate child with LIVE_ALLOW_NETWORK=1; the child downloads the pinned files of one
profile into the Hugging Face cache and prints JSON progress lines on stdout:

  {"type": "progress", "done": <bytes>, "total": <bytes>, "item": <repo>, "file": <name>}
  {"type": "item", "repo": <repo>, "state": "present|done|not_published"}
  {"type": "error", "code": "disk_full|not_published|network|checksum|network_not_allowed", "msg": ...}
  {"type": "done", "ok": true}

What to fetch comes from requirements/models.lock.txt (model_lock.subset): every repo pinned by
commit, with its file list and size. Files go into the normal HF cache layout
(blobs/<sha256 or git oid> + snapshots/<commit>/<file> symlinks + refs/main), so the existing
offline loaders find them unchanged. A partial file stays as blobs/<id>.incomplete and the next
run resumes it with an HTTP Range request; every finished file is checked against its LFS
sha256 (or git blob id) before it is linked in. Cancel = terminate the child.

  python src/live/model_setup.py status                    # offline: present, disk, RAM, profile
  LIVE_ALLOW_NETWORK=1 python src/live/model_setup.py download --profile fast
  python src/live/model_setup.py verify --profile quality  # offline: re-hash what is present

Standard library only, so it runs in any of the app's interpreters."""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import os
import pathlib
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import model_lock  # noqa: E402
import paths       # noqa: E402

GB = 1000 ** 3
GiB = 1024 ** 3
MIN_RAM = 16 * GiB           # Fast: Jackrabbit + Parakeet + NLLB with room for macOS
QUALITY_RAM = 24 * GiB       # Fast + Best: both translators stay loaded (hot switch, APP_PLAN S4)
DISK_MARGIN = 2 * GB         # keep this much free after the download
RETRIES = (1, 2, 5, 10, 20)  # seconds between attempts at one file (flaky church Wi-Fi)
CHUNK = 1 << 20
OFFLINE_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


# --- what is on disk -----------------------------------------------------------------------
def repo_dir(item: dict, cache: pathlib.Path) -> pathlib.Path:
    kind = "datasets" if item["kind"] == "dataset" else "models"
    return pathlib.Path(cache) / f"{kind}--{item['repo'].replace('/', '--')}"


def present(item: dict, cache: pathlib.Path, root: pathlib.Path = paths.REPO) -> bool:
    """Every listed file of the item exists at its pinned commit (or its dev dir= build does)."""
    if item.get("dir") and (paths.resource(item["dir"], root) / "model.bin").is_file():
        return True
    if item["rev"] == model_lock.PLACEHOLDER:
        return False
    snap = repo_dir(item, cache) / "snapshots" / item["rev"]
    if not item["files"]:
        return snap.is_dir() and any(snap.iterdir())
    return all((snap / f).is_file() for f in item["files"])


def missing(profile: str = "fast", root: pathlib.Path = paths.REPO, lock=None) -> list[str]:
    """Repos the profile still needs (empty = ready). For the needs_setup phase / models_missing."""
    cache = paths.hf_cache(root)
    return [i["repo"] for i in model_lock.subset(profile, lock) if not present(i, cache, root)]


def ct2_dir(name: str, root: pathlib.Path = paths.REPO, lock=None) -> pathlib.Path:
    """The directory CTranslate2 should load for NLLB `name`: the dev conversion in
    models/ct2/<name> if it exists, else the pinned church build in the HF cache."""
    local = paths.resource(f"models/ct2/{name}", root)
    if (local / "model.bin").is_file():
        return local
    for i in model_lock.items(lock):
        if i.get("dir", "") and i["dir"].rstrip("/").endswith(f"/{name}"):
            return repo_dir(i, paths.hf_cache(root)) / "snapshots" / i["rev"]
    return local


def free_bytes(path: pathlib.Path) -> int:
    p = pathlib.Path(path)
    while not p.exists() and p != p.parent:     # the cache may not exist yet on a fresh install
        p = p.parent
    return shutil.disk_usage(p).free


@functools.lru_cache(maxsize=1)
def hardware() -> dict:
    try:
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        ram = 0
    chip = platform.processor()
    if sys.platform == "darwin":
        for key, conv in (("hw.memsize", int), ("machdep.cpu.brand_string", str)):
            try:
                v = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True, timeout=2).stdout.strip()
                if key == "hw.memsize":
                    ram = conv(v) or ram
                else:
                    chip = v or chip
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
    return {"ram_bytes": int(ram), "arch": platform.machine(), "chip": chip or "",
            "apple_silicon": sys.platform == "darwin" and platform.machine() == "arm64"}


def status(root: pathlib.Path = paths.REPO, lock=None) -> dict:
    """Everything the setup wizard needs, offline: models present, disk, RAM, the recommended
    profile and the checks behind it."""
    cache = paths.hf_cache(root)
    models, profiles = [], {}
    for it in model_lock.items(lock):
        if it["profile"] in model_lock.TIERS:
            models.append({"id": it["repo"], "profile": it["profile"], "size_bytes": it["size"],
                           "present": present(it, cache, root),
                           "published": it["rev"] != model_lock.PLACEHOLDER})
    for p in model_lock.TIERS:
        want = model_lock.TIERS[:model_lock.TIERS.index(p) + 1]
        ms = [m for m in models if m["profile"] in want]
        profiles[p] = {"size_bytes": sum(m["size_bytes"] for m in ms),
                       "missing_bytes": sum(m["size_bytes"] for m in ms if not m["present"]),
                       "ready": all(m["present"] for m in ms),
                       "downloadable": all(m["present"] or m["published"] for m in ms)}
    hw, free = hardware(), free_bytes(cache)
    fast, best = profiles["fast"], profiles["quality"]
    fits = lambda p: free >= p["missing_bytes"] + (DISK_MARGIN if p["missing_bytes"] else 0)  # noqa: E731
    rec = "quality" if hw["ram_bytes"] >= QUALITY_RAM and (best["ready"] or fits(best)) else "fast"
    checks = [
        {"id": "apple_silicon", "ok": hw["apple_silicon"], "detail": hw["chip"] or hw["arch"]},
        {"id": "ram", "ok": hw["ram_bytes"] >= MIN_RAM,
         "detail": f"{hw['ram_bytes'] / GiB:.0f} GB memory ({MIN_RAM // GiB} GB needed, "
                   f"{QUALITY_RAM // GiB} GB for Fast + Best)"},
        {"id": "disk", "ok": fast["ready"] or fits(fast),
         "detail": f"{free / GB:.1f} GB free, {profiles[rec]['missing_bytes'] / GB:.1f} GB to download"},
        {"id": "models", "ok": fast["ready"],
         "detail": "ready" if fast["ready"] else f"{fast['missing_bytes'] / GB:.1f} GB still to download"},
    ]
    return {"profiles": profiles, "models": models, "recommended_profile": rec,
            "disk": {"path": str(cache), "free_bytes": free}, "hardware": hw, "checks": checks,
            "needs_setup": not fast["ready"], "problem": None if fast["ready"] else "models_missing"}


# --- integrity (offline) -------------------------------------------------------------------
def blob_hash(path: pathlib.Path, blob_id: str) -> str:
    """sha256 for LFS blobs (64 hex), git blob sha1 for small files (40 hex)."""
    if len(blob_id) == 64:
        h = hashlib.sha256()
    else:
        h = hashlib.sha1()
        h.update(b"blob %d\0" % path.stat().st_size)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(profile: str, root: pathlib.Path = paths.REPO, lock=None) -> list[dict]:
    """Re-hash every present file of the profile against its blob name. [{file, ok, detail}]."""
    cache, out = paths.hf_cache(root), []
    for it in model_lock.subset(profile, lock):
        if it.get("dir") and (paths.resource(it["dir"], root) / "model.bin").is_file():
            out.append({"file": it["dir"], "ok": True, "detail": "local build"})
            continue
        snap = repo_dir(it, cache) / "snapshots" / it["rev"]
        for f in it["files"] or sorted(str(p.relative_to(snap)) for p in snap.rglob("*") if p.is_file()):
            p = snap / f
            if not p.is_file():
                out.append({"file": f"{it['repo']}/{f}", "ok": False, "detail": "missing"})
                continue
            blob = p.resolve()
            if not p.is_symlink() or len(blob.name) not in (40, 64):
                out.append({"file": f"{it['repo']}/{f}", "ok": True, "detail": "not content-addressed"})
                continue
            ok = blob_hash(blob, blob.name) == blob.name
            out.append({"file": f"{it['repo']}/{f}", "ok": ok, "detail": "ok" if ok else "checksum mismatch"})
    return out


# --- the download child --------------------------------------------------------------------
class SetupError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__(msg)
        self.code = code


def _open(url: str, headers: dict | None = None, timeout: float = 60):
    req = urllib.request.Request(url, headers={"User-Agent": "live-ro2en-setup/1", **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)   # follows the CDN redirect, keeps Range


class Downloader:
    def __init__(self, plan: list[dict], root: pathlib.Path, cache: pathlib.Path, endpoint: str, out=None):
        self.plan, self.root, self.cache = plan, root, pathlib.Path(cache)
        self.endpoint = endpoint.rstrip("/")
        self.out = out or (lambda ev: print(json.dumps(ev), flush=True))
        self.total = sum(i["size"] for i in plan)
        self.done = 0
        self._last = 0.0
        self.item = self.file = None

    def _progress(self, force: bool = False):
        now = time.monotonic()
        if force or now - self._last >= 0.25:
            self._last = now
            self.out({"type": "progress", "done": self.done, "total": max(self.total, self.done),
                      "item": self.item, "file": self.file})

    def _url(self, it: dict, f: str | None = None) -> str:
        repo, rev = it["repo"], it["rev"]
        if f is None:
            kind = "datasets" if it["kind"] == "dataset" else "models"
            return f"{self.endpoint}/api/{kind}/{repo}/revision/{rev}?blobs=true"
        prefix = "datasets/" if it["kind"] == "dataset" else ""
        return f"{self.endpoint}/{prefix}{repo}/resolve/{rev}/{urllib.parse.quote(f)}"

    def _meta(self, it: dict) -> dict:
        """{file: {size, id}} at the pinned commit; id = LFS sha256 or git blob oid."""
        err = None
        for wait in (0,) + RETRIES:
            time.sleep(wait)
            try:
                with _open(self._url(it), timeout=30) as r:
                    d = json.load(r)
                break
            except (OSError, ValueError) as e:
                err = e
        else:
            raise SetupError("network", f"could not reach the model server for {it['repo']}: {err}")
        if d.get("sha") not in (None, it["rev"]):
            raise SetupError("network", f"{it['repo']}: server answered for {d.get('sha')}, not {it['rev']}")
        out = {}
        for s in d.get("siblings", []):
            lfs = s.get("lfs") or {}
            out[s["rfilename"]] = {"size": int(lfs.get("size", s.get("size") or 0)),
                                   "id": lfs.get("sha256") or s.get("blobId")}
        return out

    def _stream(self, url: str, part: pathlib.Path, size: int):
        have = part.stat().st_size if part.exists() else 0
        with _open(url, {"Range": f"bytes={have}-"} if have else None) as r:
            if have and r.status != 206:                 # the server ignored Range: start over
                self.done -= have
                have = 0
            with open(part, "ab" if have else "wb") as fh:
                while True:
                    chunk = r.read(CHUNK)
                    if not chunk:
                        break
                    fh.write(chunk)
                    self.done += len(chunk)
                    self._progress()
        if part.stat().st_size < size:
            raise OSError(f"connection closed at {part.stat().st_size} of {size} bytes")

    def _fetch_file(self, it: dict, f: str, m: dict):
        rd = repo_dir(it, self.cache)
        blob, snap = rd / "blobs" / m["id"], rd / "snapshots" / it["rev"] / f
        self.file = f
        if not blob.is_file():
            blob.parent.mkdir(parents=True, exist_ok=True)
            part = blob.with_name(m["id"] + ".incomplete")
            self.done += part.stat().st_size if part.exists() else 0     # resumed from last time
            err = None
            for wait in (0,) + RETRIES:
                time.sleep(wait)
                have = part.stat().st_size if part.exists() else 0
                if have > m["size"]:
                    part.unlink()
                    self.done -= have
                try:
                    if not part.exists() or part.stat().st_size < m["size"]:
                        self._stream(self._url(it, f), part, m["size"])
                except (OSError, urllib.error.URLError) as e:
                    err = e
                    continue
                if blob_hash(part, m["id"]) == m["id"]:
                    part.rename(blob)
                    break
                self.done -= part.stat().st_size
                part.unlink()
                err = SetupError("checksum", f"{it['repo']}/{f} did not match its checksum")
            else:
                if isinstance(err, SetupError):
                    raise err
                raise SetupError("network", f"download of {it['repo']}/{f} failed: {err}")
        else:
            self.done += m["size"]
        snap.parent.mkdir(parents=True, exist_ok=True)
        if snap.is_symlink() or snap.exists():
            snap.unlink()
        os.symlink(os.path.relpath(blob, snap.parent), snap)
        self._progress(force=True)

    def run(self) -> bool:
        need = [i for i in self.plan if not present(i, self.cache, self.root)]
        want = sum(i["size"] for i in need) - sum(
            p.stat().st_size for i in need for p in (repo_dir(i, self.cache) / "blobs").glob("*.incomplete")
            if (repo_dir(i, self.cache) / "blobs").is_dir())
        free = free_bytes(self.cache)
        if need and free < want + DISK_MARGIN:
            raise SetupError("disk_full", f"{free / GB:.1f} GB free, {(want + DISK_MARGIN) / GB:.1f} GB needed")
        unpublished = []
        for it in self.plan:
            self.item = it["repo"]
            if it not in need:
                self.done += it["size"]
                self.out({"type": "item", "repo": it["repo"], "state": "present"})
                continue
            if it["rev"] == model_lock.PLACEHOLDER:
                unpublished.append(it["repo"])
                self.out({"type": "item", "repo": it["repo"], "state": "not_published"})
                continue
            meta = self._meta(it)
            files = it["files"] or sorted(meta)
            gone = [f for f in files if f not in meta]
            if gone:
                raise SetupError("network", f"{it['repo']}@{it['rev'][:8]} has no {', '.join(gone)}")
            self.total += sum(meta[f]["size"] for f in files) - it["size"]
            self._progress(force=True)
            for f in files:
                self._fetch_file(it, f, meta[f])
            model_lock.pin_ref(it["repo"], it["rev"], self.cache, kind=it["kind"] if it["kind"] == "dataset" else "model")
            self.out({"type": "item", "repo": it["repo"], "state": "done"})
        self.file = None
        self._progress(force=True)
        if unpublished:
            raise SetupError("not_published", "not published yet (placeholder pin in models.lock.txt): "
                             + ", ".join(unpublished))
        return True


def download(profile: str, root: pathlib.Path = paths.REPO, lock=None, out=None) -> int:
    """The child's main: 0 when every file of the profile is present and verified."""
    emit = out or (lambda ev: print(json.dumps(ev), flush=True))
    if os.environ.get("LIVE_ALLOW_NETWORK") != "1":
        emit({"type": "error", "code": "network_not_allowed", "msg": "set LIVE_ALLOW_NETWORK=1 to download"})
        return 2
    endpoint = os.environ.get("HF_ENDPOINT") or "https://huggingface.co"
    try:
        Downloader(model_lock.subset(profile, lock), root, paths.hf_cache(root), endpoint, emit).run()
    except SetupError as e:
        emit({"type": "error", "code": e.code, "msg": str(e)})
        return 1
    emit({"type": "done", "ok": True})
    return 0


# --- the orchestrator side -----------------------------------------------------------------
def child_env(root: pathlib.Path = paths.REPO) -> dict:
    """Network on for the download child only; same model cache as the app's loaders."""
    env = {k: v for k, v in os.environ.items() if k not in OFFLINE_VARS}
    env.update(LIVE_ALLOW_NETWORK="1", HF_HOME=str(paths.hf_home(root)), HF_HUB_CACHE=str(paths.hf_cache(root)))
    return env


class Setup:
    """One download child at a time, its progress, and cancel (routes/setup.py)."""

    def __init__(self, root=lambda: paths.REPO, lock: pathlib.Path | None = None, python: str | None = None):
        self._root = root if callable(root) else (lambda: root)
        self.lock, self.python = lock, python or sys.executable
        self.proc: subprocess.Popen | None = None
        self._mu = threading.Lock()
        self._cancelled = False
        self.prog = self._idle()

    @staticmethod
    def _idle() -> dict:
        return {"state": "idle", "running": False, "profile": None, "done_bytes": 0, "total_bytes": 0,
                "pct": 0.0, "rate_bps": None, "eta_s": None, "item": None, "file": None,
                "error": None, "error_code": None}

    def progress(self) -> dict:
        with self._mu:
            return dict(self.prog)

    def status(self) -> dict:
        d = status(self._root(), self.lock)
        d["download"] = self.progress()
        return d

    def start(self, profile: str) -> dict:
        model_lock.subset(profile, self.lock)                 # ValueError on an unknown profile
        root = self._root()
        with self._mu:
            if self.proc and self.proc.poll() is None:
                return dict(self.prog)
            cmd = [self.python, str(HERE / "model_setup.py"), "download", "--profile", profile, "--json",
                   "--root", str(root)] + (["--lock", str(self.lock)] if self.lock else [])
            logd = paths.log_dir(root)
            logd.mkdir(parents=True, exist_ok=True)
            with open(logd / "setup.log", "a") as log:
                self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, stdin=subprocess.DEVNULL,
                                             text=True, env=child_env(root), cwd=str(root))
            self._cancelled = False
            self.prog = {**self._idle(), "state": "running", "running": True, "profile": profile}
            self._t0 = None
            threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()
            return dict(self.prog)

    def _reader(self, proc: subprocess.Popen):
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            with self._mu:
                p = self.prog
                if ev.get("type") == "progress":
                    now = time.monotonic()
                    if self._t0 is None:
                        self._t0 = (now, ev["done"])
                    p.update(done_bytes=ev["done"], total_bytes=ev["total"], item=ev.get("item"), file=ev.get("file"),
                             pct=round(100.0 * ev["done"] / ev["total"], 1) if ev["total"] else 100.0)
                    dt, db = now - self._t0[0], ev["done"] - self._t0[1]
                    if dt >= 1 and db > 0:
                        p["rate_bps"] = db / dt
                        p["eta_s"] = round(max(0, ev["total"] - ev["done"]) / p["rate_bps"], 1)
                elif ev.get("type") == "error":
                    p.update(error=ev.get("msg"), error_code=ev.get("code"))
                elif ev.get("type") == "done":
                    p.update(state="done", pct=100.0, eta_s=0)
        rc = proc.wait()
        with self._mu:
            p = self.prog
            p["running"] = False
            if self._cancelled:
                p.update(state="cancelled", eta_s=None)
            elif p["state"] != "done":
                p["state"] = "error"
                if not p["error"]:
                    p.update(error=f"the download stopped (exit {rc})", error_code="network")

    def cancel(self) -> dict:
        """Stop the child; the partial file stays and the next download resumes it."""
        with self._mu:
            proc = self.proc
            if not proc or proc.poll() is not None:
                return dict(self.prog)
            self._cancelled = True
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        for _ in range(50):                                    # let the reader record the end
            if not self.progress()["running"]:
                break
            time.sleep(0.02)
        return self.progress()


def _human(ev: dict, state={"t": 0.0}):   # noqa: B006 - progress for setup.sh, every 2 s
    if ev["type"] == "progress" and time.monotonic() - state["t"] >= 2:
        state["t"] = time.monotonic()
        print(f"  models: {ev['done'] / GB:.1f} / {ev['total'] / GB:.1f} GB  {ev.get('item') or ''}",
              file=sys.stderr, flush=True)
    elif ev["type"] == "item":
        print(f"  {ev['repo']}: {ev['state']}", file=sys.stderr, flush=True)
    elif ev["type"] == "error":
        print(f"MODEL DOWNLOAD FAILED ({ev['code']}): {ev['msg']}", file=sys.stderr, flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["status", "download", "verify"])
    ap.add_argument("--profile", default="fast", choices=model_lock.TIERS)
    ap.add_argument("--lock", type=pathlib.Path)
    ap.add_argument("--root", type=pathlib.Path, default=paths.REPO)
    ap.add_argument("--json", action="store_true", help="machine-readable progress lines on stdout")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(status(a.root, a.lock), indent=2))
        return 0
    if a.cmd == "verify":
        res = verify(a.profile, a.root, a.lock)
        for r in res:
            if not r["ok"]:
                print(f"BAD {r['file']}: {r['detail']}", file=sys.stderr)
        print(f"{sum(r['ok'] for r in res)}/{len(res)} files OK", file=sys.stderr)
        return 0 if all(r["ok"] for r in res) else 1
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # cancel: close the partial file cleanly
    return download(a.profile, a.root, a.lock, None if a.json else _human)


if __name__ == "__main__":
    sys.exit(main())
