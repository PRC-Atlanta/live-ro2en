"""Live Voice stream soak (docs/live_voice_plan.md §9): N simulated listeners on localhost for some minutes.
Starts the voice process with a synth that needs no model (the real play clocks, encoders and HTTP server),
speaks a sentence every few seconds, and reports per client the bytes received against the stream's rate,
how many fell behind (were moved to the live edge), and the voice process's CPU share of one core.

  .venv-voice/bin/python scripts/voice_soak.py --clients 100 --minutes 10"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]


def cpu_seconds(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    parts = [float(x) for x in out.replace(",", ".").split(":")] if out else [0.0]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


async def client(port: int, lang: str, until: float, got: list):
    r, w = await asyncio.open_connection("127.0.0.1", port)
    w.write(f"GET /listen/{lang} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    await r.readuntil(b"\r\n\r\n")
    n = 0
    while time.time() < until:
        try:
            data = await asyncio.wait_for(r.read(4096), 5)
        except asyncio.TimeoutError:
            break
        if not data:
            break
        n += len(data)
    got.append(n)
    w.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=100)
    ap.add_argument("--minutes", type=float, default=10.0)
    a = ap.parse_args()
    log = pathlib.Path(tempfile.mkdtemp()) / "voice.jsonl"
    p = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fake_voice_synth.py"), "--cfg",
                          json.dumps({"port": 0, "host": "127.0.0.1"}), "--log", str(log)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
    events = []
    threading.Thread(target=lambda: [events.append(json.loads(l)) for l in p.stdout], daemon=True).start()
    while not any(e["type"] == "ready" for e in events):
        time.sleep(0.05)
    port = next(e for e in events if e["type"] == "ready")["port"]
    p.stdin.write(json.dumps({"type": "state", "running": True, "blank_n": 0}) + "\n")
    secs = a.minutes * 60
    until = time.time() + secs

    def talk():
        sid = 0
        while time.time() < until:
            sid += 1
            lang = "ro" if (sid // 10) % 2 == 0 else "en"
            p.stdin.write(json.dumps({"type": "sentence", "sid": sid, "lang": lang, "text": "unu doi trei patru cinci sase sapte opt noua zece",
                                      "spk_end": time.time() - 1, "scripture": False}) + "\n")
            p.stdin.flush()
            time.sleep(4)
    threading.Thread(target=talk, daemon=True).start()
    cpu0, t0, got = cpu_seconds(p.pid), time.time(), []

    async def run():
        await asyncio.gather(*(client(port, "ro" if i % 2 else "en", until, got) for i in range(a.clients)))
    asyncio.run(run())
    wall = time.time() - t0
    cpu = cpu_seconds(p.pid) - cpu0
    p.stdin.close()
    p.wait(10)
    recs = [json.loads(l) for l in open(log)]
    stalls = sum(r.get("stalls", 0) for r in recs if r.get("event") == "client_disconnect")
    want = 8000 * wall
    print(json.dumps({"clients": a.clients, "minutes": round(wall / 60, 2), "connected": len(got),
                      "bytes_min_pct": round(100 * min(got) / want, 1), "bytes_max_pct": round(100 * max(got) / want, 1),
                      "moved_to_live_edge": stalls, "voice_cpu_pct_of_one_core": round(100 * cpu / wall, 1),
                      "items_played": sum(1 for r in recs if r.get("event") == "item")}))


if __name__ == "__main__":
    main()
