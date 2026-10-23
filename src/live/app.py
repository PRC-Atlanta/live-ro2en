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
import asyncio
import datetime as dt
import json
import os
import pathlib
import statistics
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
from captions import RollingCaptions                 # noqa: E402
from langid import LangTracker                       # noqa: E402
from propresenter import ProPresenter                # noqa: E402
from segmenter import split_clauses                  # noqa: E402
from translate import Glossary, make_translator      # noqa: E402
from textnorm import apply_spellings                 # noqa: E402


class Live:
    def __init__(self, cfg: dict, args):
        self.cfg = cfg
        self.profile = cfg["live"].get("profile", "fast")
        self.lc = self._profile_cfg(self.profile)
        self.args = args
        self.pp = ProPresenter(**cfg["propresenter"])
        self.caps = RollingCaptions(cfg["propresenter"]["max_chars_per_line"], cfg["propresenter"]["max_lines"])
        self.lang = LangTracker(self.lc.get("language_mode", "auto"))
        self.gloss = Glossary(ROOT / cfg["paths"]["glossary"])
        self.tr = None
        self.engine: subprocess.Popen | None = None
        self.running = False
        self.history: list[dict] = []
        self.lat: list[float] = []
        self.status = "idle"
        self.partial = ""
        self.lock = threading.Lock()
        self.listeners: set = set()
        self.log_path = None
        self.eof = threading.Event()
        self._pending: dict | None = None
        self._timer: threading.Timer | None = None
        self._proc_lock = threading.Lock()

    def _profile_cfg(self, name: str) -> dict:
        """config.yaml `live` settings with the chosen profile's overrides on top."""
        return {**self.cfg["live"], **(self.cfg.get("profiles", {}).get(name) or {})}

    def set_profile(self, name: str):
        """Switch fast <-> quality: restarts the engine and reloads the translator if needed."""
        if name not in self.cfg.get("profiles", {}) or name == self.profile:
            return
        was_running = self.running
        self.stop()
        new = self._profile_cfg(name)
        if new["translator"] != self.lc["translator"]:
            self.tr = None
        self.profile, self.lc = name, new
        if was_running:
            self.start()
        self._broadcast()

    # --- control ---------------------------------------------------------------------
    def load_translator(self):
        if self.tr is None:
            self.status = "loading translator"
            self.tr = make_translator(self.lc["translator"])
            if hasattr(self.tr, "context_clauses"):
                self.tr.context_clauses = int(self.lc.get("context_clauses", 2))
            self.tr.translate("Bună dimineața.", "ro", "en", self.gloss)    # warm-up

    def start(self, device=None):
        if self.running:
            return
        self.load_translator()
        logdir = ROOT / self.lc["transcript_log_dir"]
        logdir.mkdir(parents=True, exist_ok=True)
        self.log_path = logdir / f"{dt.datetime.now():%Y-%m-%d_%H%M%S}.jsonl"
        py = ROOT / self.lc["stt_venv"] / "bin" / "python"
        cmd = [str(py), str(HERE / "engine.py"), "--stt", self.lc["stt_model"], "--mode", self.lang.mode,
               "--cfg", json.dumps({**{k: self.lc[k] for k in ("vad_threshold", "min_pause_ms", "max_clause_s",
                                                                "partial_every_s", "min_commit_s", "max_commit_s",
                                                                "en_recognizer", "context_s", "lookahead_words") if k in self.lc},
                                   "whistle": {**(self.cfg.get("whistle") or {}),   # whistle_<key> in live/profile overrides
                                               **{k[len("whistle_"):]: v for k, v in self.lc.items()
                                                  if k.startswith("whistle_")}}})]
        if getattr(self.args, "file", None):
            cmd += ["--file", self.args.file, "--realtime"]
        elif device or self.lc.get("input_device") is not None:
            cmd += ["--device", str(device or self.lc["input_device"])]
        self.status = "starting engine"
        self.engine = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=open(ROOT / "logs" / "engine.log", "a"), text=True, bufsize=1)
        self.running = True
        threading.Thread(target=self._reader, daemon=True).start()

    def stop(self):
        if self.engine and self.engine.poll() is None:
            try:
                self.engine.stdin.write(json.dumps({"cmd": "stop"}) + "\n"); self.engine.stdin.flush()
                self.engine.wait(5)
            except Exception:
                self.engine.kill()
        self.running = False
        self.status = "stopped"
        self._broadcast()

    def set_mode(self, mode: str):
        self.lang.set_mode(mode)
        if self.engine and self.engine.poll() is None:
            self.engine.stdin.write(json.dumps({"cmd": "mode", "mode": mode}) + "\n"); self.engine.stdin.flush()
        self._broadcast()

    def clear(self):
        self.caps.clear()
        self.pp.clear()
        self._broadcast()

    # --- clause pipeline ---------------------------------------------------------------
    def _reader(self):
        for line in self.engine.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev["type"] == "ready":
                self.status = f"listening ({ev['source']})"
            elif ev["type"] == "partial":
                self.partial = ev["text"]
            elif ev["type"] == "clause":
                self._on_clause(ev)
            elif ev["type"] == "eof":
                self._flush()
                self.status = "input ended"
                self.eof.set()
            self._broadcast()
        self.running = False

    def _on_clause(self, ev: dict):
        """Hold very short fragments ('Dar acum.') briefly and merge them with the next
        clause: translating 1-3 words alone is poor. Sentence-final fragments go now."""
        with self.lock:
            if self._timer:
                self._timer.cancel()
            p = self._pending
            cap = self.lc.get("max_chunk_s", 0)
            if p and cap and ev["t_end"] - p.get("t_speech", p["t_start"]) > cap:
                # merging would make the first word wait too long: send the fragment on its own
                self._pending = None
                threading.Thread(target=self._handle, args=(p,), daemon=True).start()
                p = None
            if p:
                ev = {**ev, "ro": f"{p['ro']} {ev['ro']}".strip(), "multi": f"{p['multi']} {ev['multi']}".strip(),
                      "t_start": p["t_start"], "t_speech": p.get("t_speech", p["t_start"]), "stt_s": p["stt_s"] + ev["stt_s"],
                      "audio_s": p["audio_s"] + ev["audio_s"]}
            self._pending = None
            text = ev.get("multi") if self.lang.current == "en" else ev["ro"]
            text = text or ev["ro"] or ""
            short = len(text.split()) < self.lc.get("hold_min_words", 4) and not text.rstrip().endswith(("?", "!"))
            if short and not (cap and ev["t_end"] - ev.get("t_speech", ev["t_start"]) > cap):
                self._pending = ev
                self._timer = threading.Timer(self.lc.get("hold_s", 1.2), self._flush)
                self._timer.daemon = True
                self._timer.start()
                return
        self._handle(ev)

    def _flush(self):
        with self.lock:
            ev, self._pending = self._pending, None
        if ev:
            self._handle(ev)
            self._broadcast()

    def _handle(self, ev: dict):
        """Never let one bad chunk stop the service: log it and carry on."""
        with self._proc_lock:
            try:
                self._handle_locked(ev)
            except Exception as e:                       # noqa: BLE001
                import traceback
                self.status = f"error on one chunk ({type(e).__name__}); still listening"
                with open(ROOT / "logs" / "app_errors.log", "a") as f:
                    f.write(f"{dt.datetime.now().isoformat()} {ev.get('ro', '')[:200]!r}\n{traceback.format_exc()}\n")

    def _handle_locked(self, ev: dict):
        # language: in auto mode decide from the multilingual transcript
        probe = ev.get("multi") or ev.get("ro") or ""
        lang = self.lang.update(probe)
        # English text: Whistle when configured (ev["en"]), else the Parakeet text (A/B switch)
        src_text = ev["ro"] if lang == "ro" else (ev.get("en") or ev.get("multi") or ev["ro"])
        src_text = apply_spellings(src_text, self.cfg.get("spellings") or {})
        if not src_text.strip():
            return
        tgt = "en" if lang == "ro" else "ro"
        clauses, tail = split_clauses(src_text)
        parts = clauses + ([tail] if tail else [])        # at a pause everything is committed
        t0 = time.time()
        out = []
        for p in parts:
            out.append(self.tr.translate(p, lang, tgt, self.gloss, use_context=True)
                       if hasattr(self.tr, "context") else self.tr.translate(p, lang, tgt, self.gloss))
        translation = " ".join(o for o in out if o)
        t_tr = time.time() - t0
        shown = self.caps.add(translation)
        t1 = time.time()
        ok = self.pp.show(shown)
        t_disp = time.time()
        latency = t_disp - ev["t_end"]
        n_g, ok_g = self.gloss.adherence(src_text, translation, lang)
        rec = {"id": ev["id"], "t_start": ev["t_start"], "t_end": ev["t_end"], "t_display": t_disp,
               "lang": lang, "source": src_text,
               "en_recognizer": (ev.get("source") or ("whistle" if ev.get("en") else "parakeet")) if lang == "en" else None, "translation": translation, "latency_s": round(latency, 3),
               # average delay per word, approximating words as evenly spread over the commit
               "word_lag_s": round(latency + (ev["t_end"] - ev["t_start"]) / 2, 3), "profile": self.profile,
               "t_speech": ev.get("t_speech", ev["t_start"]),
               "first_word_s": round(t_disp - ev.get("t_speech", ev["t_start"]), 3),  # what a bilingual listener notices
               "stt_s": ev["stt_s"], "vad_wait_s": round(ev["t_emit"] - ev["t_end"] - ev["stt_s"], 3),
               "mt_s": round(t_tr, 3), "pp_s": round(t_disp - t1, 4), "pp_ok": ok,
               "pp_error": self.pp.last_error, "audio_s": ev["audio_s"], "glossary": [n_g, ok_g],
               "time": dt.datetime.now().isoformat(timespec="milliseconds")}
        with self.lock:
            self.history = (self.history + [rec])[-10:]
            self.lat.append(latency)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # --- UI state ------------------------------------------------------------------------
    def state(self) -> dict:
        with self.lock:
            lat = self.lat[-30:]
            return {"running": self.running, "status": self.status, "mode": self.lang.mode, "profile": self.profile,
                    "profiles": list(self.cfg.get("profiles", {})),
                    "lang": self.lang.current, "partial": self.partial,
                    "latency_last": round(lat[-1], 2) if lat else None,
                    "latency_median": round(statistics.median(lat), 2) if lat else None,
                    "lines": [{"src": h["source"], "dst": h["translation"], "lat": h["latency_s"],
                               "lang": h["lang"]} for h in self.history],
                    "screen": self.caps.text, "pp_error": self.pp.last_error,
                    "translator": self.lc["translator"], "stt": self.lc["stt_model"]}

    def _broadcast(self):
        for q in list(self.listeners):
            try:
                q.put_nowait(self.state())
            except Exception:
                pass


PAGE = (HERE / "control.html").read_text()


def build_app(live: Live):
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse, JSONResponse
    app = FastAPI()

    @app.get("/")
    def index():
        return HTMLResponse(PAGE)

    @app.get("/api/state")
    def state():
        return JSONResponse(live.state())

    @app.get("/api/devices")
    def devices():
        try:
            import sounddevice as sd
            return [{"index": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
                    if d["max_input_channels"] > 0]
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.post("/api/{action}")
    async def act(action: str, body: dict | None = None):
        body = body or {}
        loop = asyncio.get_running_loop()
        if action == "start":
            await loop.run_in_executor(None, live.start, body.get("device"))
        elif action == "stop":
            await loop.run_in_executor(None, live.stop)
        elif action == "mode":
            live.set_mode(body["mode"])
        elif action == "profile":
            await loop.run_in_executor(None, live.set_profile, body["profile"])
        elif action == "clear":
            await loop.run_in_executor(None, live.clear)
        return live.state()

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        loop = asyncio.get_running_loop()

        class Bridge:
            def put_nowait(self, s):
                loop.call_soon_threadsafe(lambda: q.full() or q.put_nowait(s))
        b = Bridge()
        live.listeners.add(b)
        try:
            await sock.send_json(live.state())
            while True:
                try:
                    s = await asyncio.wait_for(q.get(), 1.0)
                except asyncio.TimeoutError:
                    s = live.state()
                await sock.send_json(s)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            live.listeners.discard(b)
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--file", help="play an audio file as live input (real-time paced)")
    ap.add_argument("--autostart", action="store_true")
    ap.add_argument("--exit-on-eof", action="store_true")
    ap.add_argument("--headless", action="store_true", help="no web server")
    ap.add_argument("--pp-port", type=int)
    ap.add_argument("--mode")
    ap.add_argument("--profile", help="fast | quality (overrides live.profile)")
    a = ap.parse_args()
    cfg = yaml.safe_load(open(a.config))
    if a.pp_port:
        cfg["propresenter"]["port"] = a.pp_port
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
