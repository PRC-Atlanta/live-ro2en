"""S6: first-run model download (src/live/model_setup.py, /api/setup*). Everything runs against a
local fake Hugging Face server on 127.0.0.1: no real network, no real models."""
import hashlib
import http.server
import json
import os
import pathlib
import re
import sys
import threading
import time
import types

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))
import model_lock   # noqa: E402
import model_setup  # noqa: E402

REV_FAST, REV_BEST = "1" * 40, "2" * 40
BIG = os.urandom(3 * 1024 * 1024 + 123)        # LFS file, served slowly through a CDN redirect
SMALL = b'{"model_type": "fake"}'               # plain git file (git blob sha1)
BEST = os.urandom(200_000)


def git_oid(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


REPOS = {
    ("org/fast-model", REV_FAST): {"weights.bin": (BIG, True), "config.json": (SMALL, False)},
    ("org/best-model", REV_BEST): {"model.safetensors": (BEST, True)},
}


class FakeHub(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Handler)
        self.log, self.slow, self.corrupt = [], 0.0, False
        self.blobs = {hashlib.sha256(d).hexdigest(): d for files in REPOS.values() for d, lfs in files.values() if lfs}

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, data: bytes, status=200, headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        step = 64 * 1024
        for i in range(0, len(data), step):
            try:
                self.wfile.write(data[i:i + step])
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            if self.server.slow:
                time.sleep(self.server.slow)

    def do_GET(self):
        srv, path = self.server, self.path.split("?")[0]
        srv.log.append((path, self.headers.get("Range")))
        m = re.fullmatch(r"/api/models/(.+)/revision/([0-9a-f]{40})", path)
        if m and (m[1], m[2]) in REPOS:
            sib = []
            for name, (d, lfs) in REPOS[(m[1], m[2])].items():
                s = {"rfilename": name, "blobId": git_oid(d), "size": len(d)}
                if lfs:
                    s["lfs"] = {"sha256": hashlib.sha256(d).hexdigest(), "size": len(d), "pointerSize": 134}
                sib.append(s)
            return self._send(json.dumps({"id": m[1], "sha": m[2], "siblings": sib}).encode(),
                              headers={"Content-Type": "application/json"})
        m = re.fullmatch(r"/(.+)/resolve/([0-9a-f]{40})/(.+)", path)
        if m and (m[1], m[2]) in REPOS and m[3] in REPOS[(m[1], m[2])]:
            d, lfs = REPOS[(m[1], m[2])][m[3]]
            if lfs:                                       # like HF: LFS files redirect to the CDN
                self.send_response(302)
                self.send_header("Location", f"/cdn/{hashlib.sha256(d).hexdigest()}")
                self.send_header("Content-Length", "0")
                return self.end_headers()
            return self._send(d)
        m = re.fullmatch(r"/cdn/([0-9a-f]{64})", path)
        if m and m[1] in srv.blobs:
            d = srv.blobs[m[1]]
            if srv.corrupt:
                d = bytes(len(d))
            rng = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range") or "")
            if rng:
                start = int(rng[1])
                return self._send(d[start:], 206, {"Content-Range": f"bytes {start}-{len(d) - 1}/{len(d)}"})
            return self._send(d)
        self._send(b"not found", 404)


@pytest.fixture
def hub():
    srv = FakeHub()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def home(tmp_path, monkeypatch, hub):
    """An empty LIVE_HOME, the fake hub as HF_ENDPOINT, and a lock with one Fast and one Best repo."""
    monkeypatch.setenv("LIVE_HOME", str(tmp_path / "home"))
    for v in ("HF_HOME", "HF_HUB_CACHE", "LIVE_LOG_DIR", "LIVE_ALLOW_NETWORK"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("HF_ENDPOINT", hub.url)
    monkeypatch.setattr(model_setup, "RETRIES", (0,))
    lock = tmp_path / "models.lock.txt"
    lock.write_text(f"hf org/fast-model {REV_FAST} size={len(BIG) + len(SMALL)} profile=fast "
                    f"files=weights.bin,config.json\n"
                    f"hf org/best-model {REV_BEST} size={len(BEST)} profile=quality files=model.safetensors\n"
                    f"hf org/bench-model {'3' * 40} size=99 profile=bench\n")
    return types.SimpleNamespace(root=tmp_path / "home", lock=lock, cache=tmp_path / "home" / "hf" / "hub")


def _wait(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def test_lock_has_sizes_and_profiles_for_every_app_download():
    fast = {i["repo"] for i in model_lock.subset("fast")}
    best = {i["repo"] for i in model_lock.subset("quality")}
    assert {"surogate/jackrabbit-110m-ro", "mlx-community/parakeet-tdt-0.6b-v3",
            "facebook/nllb-200-distilled-1.3B"} <= fast < best
    assert "mlx-community/gemma-4-12B-it-qat-4bit" in best - fast
    assert not any(i["profile"] in ("bench", "bundled") for i in model_lock.subset("quality"))
    for i in model_lock.subset("quality"):
        assert i["size"] > 0 and i["files"] and len(i["rev"]) == 40, i["repo"]
    cfg = yaml.safe_load(open(ROOT / "config.yaml"))     # every translator a profile can switch to
    mlx = {p["translator"].split(":", 1)[1] for p in cfg["profiles"].values() if p["translator"].startswith("mlx:")}
    assert mlx <= best
    ct2 = [i for i in model_lock.subset("fast") if i["dir"]]
    assert ct2 and ct2[0]["dir"] == "models/ct2/nllb-200-distilled-1.3B"
    assert model_lock.entries("hf")["surogate/jackrabbit-110m-ro"] == "ad5437c325ed7cec485bd478e3de2dfc91df719e"


def test_ct2_placeholder_pin_is_marked():
    lines = (ROOT / "requirements" / "models.lock.txt").read_text()
    ph = [i for i in model_lock.items() if i["rev"] == model_lock.PLACEHOLDER]
    assert all("PLACEHOLDER" in i["repo"] for i in ph) and "PLACEHOLDER PIN" in lines
    assert "publish_models.py" in lines and (ROOT / "scripts" / "publish_models.py").is_file()


def test_setup_sh_live_uses_the_app_downloader():
    setup = (ROOT / "setup.sh").read_text()
    assert "src/live/model_setup.py download" in setup and "LIVE_ALLOW_NETWORK=1" in setup


def test_status_on_an_empty_home(home):
    st = model_setup.status(home.root, home.lock)
    assert st["needs_setup"] and st["problem"] == "models_missing"
    assert st["profiles"]["fast"]["missing_bytes"] == len(BIG) + len(SMALL)
    assert st["profiles"]["quality"]["size_bytes"] == len(BIG) + len(SMALL) + len(BEST)
    assert [m["id"] for m in st["models"]] == ["org/fast-model", "org/best-model"]    # no bench
    assert st["disk"]["path"] == str(home.cache) and st["disk"]["free_bytes"] > 0
    assert st["recommended_profile"] in ("fast", "quality")
    assert {c["id"] for c in st["checks"]} == {"apple_silicon", "ram", "disk", "models"}
    assert not home.root.exists()                     # looking does not create anything
    assert model_setup.missing("fast", home.root, home.lock) == ["org/fast-model"]


def test_recommendation_follows_ram_and_disk(home, monkeypatch):
    hw = dict(model_setup.hardware())
    monkeypatch.setattr(model_setup, "hardware", lambda: {**hw, "ram_bytes": 16 * model_setup.GiB})
    assert model_setup.status(home.root, home.lock)["recommended_profile"] == "fast"
    monkeypatch.setattr(model_setup, "hardware", lambda: {**hw, "ram_bytes": 64 * model_setup.GiB})
    assert model_setup.status(home.root, home.lock)["recommended_profile"] == "quality"
    monkeypatch.setattr(model_setup, "free_bytes", lambda p: 10)
    st = model_setup.status(home.root, home.lock)
    assert st["recommended_profile"] == "fast" and not next(c for c in st["checks"] if c["id"] == "disk")["ok"]


def test_download_needs_the_network_flag(home):
    out = []
    assert model_setup.download("fast", home.root, home.lock, out.append) == 2
    assert out[-1]["code"] == "network_not_allowed" and not home.cache.exists()


def test_download_refuses_when_the_disk_is_too_small(home, monkeypatch, hub):
    monkeypatch.setenv("LIVE_ALLOW_NETWORK", "1")
    monkeypatch.setattr(model_setup, "free_bytes", lambda p: 1000)
    out = []
    assert model_setup.download("fast", home.root, home.lock, out.append) == 1
    assert out[-1]["code"] == "disk_full" and hub.log == []


def test_a_corrupt_file_is_never_linked_in(home, monkeypatch, hub):
    monkeypatch.setenv("LIVE_ALLOW_NETWORK", "1")
    hub.corrupt = True
    out = []
    assert model_setup.download("fast", home.root, home.lock, out.append) == 1
    assert out[-1]["code"] == "checksum"
    snap = home.cache / "models--org--fast-model" / "snapshots" / REV_FAST
    assert not (snap / "weights.bin").exists() and not list(home.cache.rglob("*.incomplete"))


def test_unpublished_placeholder_is_reported_and_the_rest_downloads(home, monkeypatch, hub):
    monkeypatch.setenv("LIVE_ALLOW_NETWORK", "1")
    home.lock.write_text(home.lock.read_text() +
                         f"hf CHURCH-ORG-PLACEHOLDER/nllb-ct2 {model_lock.PLACEHOLDER} size=5 profile=fast files=model.bin "
                         f"dir=models/ct2/fake-nllb\n")
    out = []
    assert model_setup.download("fast", home.root, home.lock, out.append) == 1
    assert out[-1]["code"] == "not_published"
    assert {"type": "item", "repo": "org/fast-model", "state": "done"} in out
    # a dev install that converted the model locally counts as present (setup.sh path)
    (home.root / "models" / "ct2" / "fake-nllb").mkdir(parents=True)
    (home.root / "models" / "ct2" / "fake-nllb" / "model.bin").write_bytes(b"x")
    assert model_setup.missing("fast", home.root, home.lock) == []
    assert model_setup.ct2_dir("fake-nllb", home.root, home.lock) == home.root / "models" / "ct2" / "fake-nllb"


def test_download_interrupt_resume_then_offline(home, monkeypatch, hub):
    """Empty LIVE_HOME: pick Fast -> download -> cancel midway -> resume (Range) -> all checks OK
    -> the files load offline through refs/main. Driven through the HTTP API."""
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import Live, build_app
    real = model_setup.Setup
    monkeypatch.setattr(model_setup, "Setup", lambda root: real(root=root, lock=home.lock))
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    from test_contract import C, check

    st = c.get("/api/setup").json()
    check(st, C["types"]["setup"], "setup")
    assert st["needs_setup"] and st["download"]["state"] == "idle"
    assert "LIVE_ALLOW_NETWORK" not in os.environ                  # the orchestrator stays offline

    hub.slow = 0.03                                                # ~1.5 s for the big file
    r = c.post("/api/setup/download", json={"profile": "fast"}, headers=me)
    assert r.status_code == 200 and r.json()["download"]["running"]
    _wait(lambda: c.get("/api/setup").json()["download"]["done_bytes"] > 512 * 1024)
    st = c.post("/api/setup/cancel", json={}, headers=me).json()
    assert st["download"]["state"] == "cancelled" and not st["download"]["running"]
    check(st, C["types"]["setup"], "setup")
    parts = list(home.cache.rglob("*.incomplete"))
    assert len(parts) == 1 and 0 < parts[0].stat().st_size < len(BIG)
    have = parts[0].stat().st_size
    assert c.get("/api/setup").json()["needs_setup"]

    hub.slow, hub.log[:] = 0.0, []
    c.post("/api/setup/download", json={"profile": "fast"}, headers=me)
    st = _wait(lambda: (s := c.get("/api/setup").json())["download"]["state"] != "running" and s)
    assert st["download"]["state"] == "done", st["download"]
    assert st["download"]["done_bytes"] == st["download"]["total_bytes"] == len(BIG) + len(SMALL)
    assert st["download"]["pct"] == 100.0
    ranges = [rg for p, rg in hub.log if p.startswith("/cdn/")]
    assert ranges == [f"bytes={have}-"]                            # resumed, not restarted
    assert not st["needs_setup"] and st["problem"] is None and st["profiles"]["fast"]["ready"]
    assert not st["profiles"]["quality"]["ready"]                  # Best was not asked for
    check(st, C["types"]["setup"], "setup")

    res = model_setup.verify("fast", home.root, home.lock)          # every file re-hashed: OK
    assert len(res) == 2 and all(r["ok"] and r["detail"] == "ok" for r in res)
    from huggingface_hub import try_to_load_from_cache
    snap = home.cache / "models--org--fast-model" / "snapshots" / REV_FAST
    assert (home.cache / "models--org--fast-model" / "refs" / "main").read_text() == REV_FAST
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    got = try_to_load_from_cache("org/fast-model", "weights.bin", cache_dir=home.cache)
    assert got == str(snap / "weights.bin") and pathlib.Path(got).read_bytes() == BIG
    assert (home.root / "logs" / "setup.log").exists() and not (ROOT / "hf").exists()


def test_download_is_refused_while_live(home, monkeypatch):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from live.app import Live, build_app
    live = Live(yaml.safe_load(open(ROOT / "config.yaml")), types.SimpleNamespace(file=None))
    c = TestClient(build_app(live), base_url="http://127.0.0.1:8765")
    me = {"origin": "http://127.0.0.1:8765"}
    assert c.post("/api/setup/download", json={"profile": "nope"}, headers=me).status_code == 400
    live.running = True
    assert c.post("/api/setup/download", json={"profile": "fast"}, headers=me).status_code == 409
    assert c.post("/api/setup/download", json={}, headers={"origin": "https://evil.example"}).status_code == 403


def test_publish_script_writes_the_pin_over_the_placeholder(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("publish_models", ROOT / "scripts" / "publish_models.py")
    pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pm)
    build = tmp_path / "build"
    build.mkdir()
    (build / "model.bin").write_bytes(b"x" * 10)
    (build / "config.json").write_bytes(b"{}")
    (build / "README.md").write_text("card")
    line = pm.lock_line("church/nllb-ct2", "c" * 40, build)
    assert line == ("hf church/nllb-ct2 " + "c" * 40 + " size=12 profile=fast files=config.json,model.bin "
                    "dir=models/ct2/nllb-200-distilled-1.3B")
    lock = tmp_path / "lock.txt"
    lock.write_text((ROOT / "requirements" / "models.lock.txt").read_text())
    pm.write_lock(line, lock)
    it = [i for i in model_lock.items(lock) if i["dir"]]
    assert len(it) == 1 and it[0]["repo"] == "church/nllb-ct2" and it[0]["rev"] == "c" * 40
    assert model_lock.PLACEHOLDER not in lock.read_text()
