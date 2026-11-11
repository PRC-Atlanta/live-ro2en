"""Live Voice never opens a connection (docs/live_voice_plan.md §7): the real voice process, with the
real model, under the outbound-denying sandbox profile and the network audit, loads, serves a listener
and speaks, and the audit log stays empty. Skipped where .venv-voice or the model is not set up."""
import http.client
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice import model                                  # noqa: E402

VOICE_PY = ROOT / ".venv-voice" / "bin" / "python"
ready = VOICE_PY.exists() and model.model_dir() is not None


def test_the_audit_hook_sees_an_outbound_connection(tmp_path):
    """The audit itself works: a connect or a lookup to anything but this Mac is written down."""
    log = tmp_path / "net.log"
    code = ("import sys, socket; sys.path.insert(0, 'src/live'); import netaudit; netaudit.install()\n"
            "s = socket.socket(); s.setblocking(False)\n"
            "try: s.connect(('192.0.2.1', 9))\n"
            "except OSError: pass\n"
            "t = socket.socket(); t.setblocking(False)\n"
            "try: t.connect(('127.0.0.1', 9))\n"
            "except OSError: pass\n")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**os.environ, "NET_AUDIT_LOG": str(log)}, check=True)
    lines = log.read_text().splitlines()
    assert len(lines) == 1 and "192.0.2.1" in lines[0] and "socket.connect" in lines[0]


@pytest.mark.skipif(not ready, reason="needs .venv-voice and the Supertonic 3 files (setup.sh)")
@pytest.mark.parametrize("sandbox", [False, True])
def test_the_voice_process_opens_no_connection(tmp_path, sandbox):
    if sandbox and not shutil.which("sandbox-exec"):
        pytest.skip("no sandbox-exec")
    log = tmp_path / "net.log"
    cmd = [str(VOICE_PY), str(ROOT / "src" / "voice" / "voice.py"), "--cfg",
           json.dumps({"port": 0, "host": "127.0.0.1", "threads": 2}), "--log", str(tmp_path / "voice.jsonl")]
    if sandbox:
        cmd = ["sandbox-exec", "-f", str(ROOT / "scripts" / "offline_voice.sb")] + cmd
    env = {k: v for k, v in os.environ.items() if k != "LIVE_ALLOW_NETWORK"}
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                         env={**env, "NET_AUDIT_LOG": str(log)}, cwd=ROOT)
    events = []
    threading.Thread(target=lambda: [events.append(json.loads(l)) for l in p.stdout], daemon=True).start()
    try:
        end = time.time() + 60
        while time.time() < end and not any(e["type"] in ("ready", "error") for e in events) and p.poll() is None:
            time.sleep(0.1)
        ev = next((e for e in events if e["type"] in ("ready", "error")), None)
        assert ev and ev["type"] == "ready", (ev, p.poll(), p.stderr.read()[-800:] if p.poll() is not None else "")
        c = http.client.HTTPConnection("127.0.0.1", ev["port"], timeout=5)
        c.request("GET", "/listen/ro")
        r = c.getresponse()
        p.stdin.write(json.dumps({"type": "state", "running": True, "blank_n": 0}) + "\n")
        p.stdin.write(json.dumps({"type": "sentence", "sid": 1, "lang": "ro", "text": "Deschideți la Romani 8:28.",
                                  "spk_end": time.time() - 1, "scripture": False}) + "\n")
        p.stdin.write(json.dumps({"type": "sentence", "sid": 2, "lang": "en", "text": "Open your Bibles to John 3:16.",
                                  "spk_end": time.time() - 1, "scripture": False}) + "\n")
        p.stdin.flush()
        got, end = 0, time.time() + 20
        while time.time() < end and len([e for e in events if e["type"] == "item"]) < 2:
            got += len(r.read(2000))
        items = [e for e in events if e["type"] == "item"]
        assert {i["lang"] for i in items} == {"ro", "en"} and got > 8000
        assert not [e for e in events if e["type"] == "error"]
    finally:
        try:
            p.stdin.close()
        except OSError:
            pass
        p.wait(10)
    assert p.returncode == 0
    assert not log.exists() or log.read_text() == "", log.read_text()
    env_set = (ROOT / "src" / "voice" / "voice.py").read_text()
    assert 'HF_HUB_OFFLINE' in env_set and "netaudit.install()" in env_set
