"""Live Remote: the toolbar app on the ProPresenter Mac (remote/). With `display.remote: true`
the network server of the captions display (display.py, `display.port`) also answers
/remote/*: Hide/Show, Hold, Song, Clear, Remove last and today's names & words, plus a
feed of what the toolbar shows. Start, stop and settings stay on this Mac.

Every /remote request carries the PIN (`X-Live-Pin`), shown in Settings → ProPresenter and
kept in <user dir>/remote_pin. A new PIN drops every connected remote. Too many wrong PINs
from one address lock that address out for a minute, and 50 in an hour from anywhere lock out
every address but the remote that last knew the PIN (a request with no PIN is refused but not
counted, so nobody can lock the real remote out). Browsers are refused outright: any
request with an Origin header, or for a Host that is not this Mac (DNS rebinding); Live
Remote sends neither. The server is advertised over Bonjour (_liveroen._tcp), so the remote
finds this Mac by itself. Mixed into live.app.Live."""
import asyncio
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

import paths

PIN_HEADER = "x-live-pin"
SERVICE = "_liveroen._tcp"
MAX_FAILS, FAIL_WINDOW_S = 10, 60.0     # wrong PINs from one address within the window: locked out
GLOBAL_FAILS, GLOBAL_WINDOW_S = 50, 3600.0   # from all addresses in an hour: every address but the
                                             # last one that knew the PIN is locked out (many-IP guessing)
FEED_EVERY_S = 0.25
# The Bonjour advert ($1 name, $2 port, $3 this process): restarts dns-sd if it ends
# (mDNSResponder restarted) and ends it when this process goes, even on a crash.
WATCH = ('trap \'kill $c 2>/dev/null; exit\' TERM; c=; '
         'while kill -0 "$3" 2>/dev/null; do '
         'if [ -z "$c" ] || ! kill -0 $c 2>/dev/null; then '
         'dns-sd -R "$1" ' + SERVICE + ' local "$2" v=1 >/dev/null 2>&1 & c=$!; fi; '
         'sleep ${LIVE_BONJOUR_TICK_S:-2}; done; kill $c 2>/dev/null')


def new_pin() -> str:
    return f"{secrets.randbelow(10**6):06d}"


class RemoteMixin:
    def _mixin_init(self):
        self._remote_pin: str | None = None
        self._remote_fails: dict[str, list[float]] = {}     # per client address
        self._remote_global: list[float] = []               # every wrong PIN, for the hourly cap
        self._remote_trusted: str | None = None             # the address that last sent the right PIN
        self._remote_lock = threading.Lock()
        self._remote_clients = 0
        self._bonjour: subprocess.Popen | None = None
        self._bonjour_lock = threading.Lock()
        self.register_state(lambda: {"remote": self._remote_state()})

    # --- settings and the PIN ----------------------------------------------------------
    def remote_enabled(self) -> bool:
        return bool((self.cfg.get("display") or {}).get("remote", False))

    def remote_pin(self) -> str:
        """The PIN, made (and saved) the first time it is needed."""
        with self._remote_lock:
            if self._remote_pin is None:
                f = paths.remote_pin_file()
                try:
                    pin = f.read_text().strip()
                except OSError:
                    pin = ""
                if not (len(pin) == 6 and pin.isdigit()):
                    pin = self._write_pin(new_pin())
                self._remote_pin = pin
            return self._remote_pin

    def _write_pin(self, pin: str) -> str:
        from settings import atomic_write
        f = paths.remote_pin_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(f, pin + "\n")
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
        return pin

    def renew_remote_pin(self) -> str:
        """Settings → New PIN: every connected remote is dropped and must type the new one."""
        with self._remote_lock:
            self._remote_pin = self._write_pin(new_pin())
            self._remote_fails.clear()             # a new PIN also ends every lockout
            self._remote_global.clear()
        self._log_event({"event": "remote_pin"})
        self._broadcast()
        return self._remote_pin

    def _remote_state(self) -> dict:
        on = self.remote_enabled()
        return {"enabled": on, "pin": self.remote_pin() if on else None,
                "clients": self._remote_clients if on else 0}

    # --- checking a request ------------------------------------------------------------
    def remote_hosts(self) -> set[str]:
        """Host names this Mac answers to (besides IP literals): a rebinding name is not one."""
        names = {"localhost", socket.gethostname().lower()}
        net = (getattr(self, "_net_host", None) or "").lower()
        if net:
            names |= {net, net.removesuffix(".local")}
        names |= {n + ".local" for n in list(names) if not n.endswith(".local")}
        return names

    def remote_check(self, pin: str | None, client: str = "", host: str | None = None,
                     origin: str | None = None) -> int:
        """200 for the right PIN; 404 with the remote off; 403 for a browser (Origin) or a
        foreign Host; 429 while this client is locked out; 401 otherwise. Only a wrong PIN
        that was actually sent counts towards the lockout."""
        if not self.remote_enabled():
            return 404
        if origin is not None or not self._host_ok(host):
            return 403
        right = bool(pin) and hmac.compare_digest(pin.encode(), self.remote_pin().encode())
        now = time.monotonic()
        with self._remote_lock:                    # one section: a New PIN can't be undone half-way
            for c in [c for c, ts in self._remote_fails.items() if not ts or now - ts[-1] >= FAIL_WINDOW_S]:
                del self._remote_fails[c]
            self._remote_global = [t for t in self._remote_global if now - t < GLOBAL_WINDOW_S]
            fails = self._remote_fails.get(client, [])
            if len(fails) >= MAX_FAILS:
                return 429
            if len(self._remote_global) >= GLOBAL_FAILS and client != self._remote_trusted:
                return 429
            if not pin:
                return 401
            if right:
                self._remote_trusted = client
                return 200
            self._remote_fails[client] = fails + [now]
            self._remote_global.append(now)
        return 401

    def _host_ok(self, host: str | None) -> bool:
        if not host:
            return False
        h = host.strip().lower()
        if h.startswith("["):                       # [v6]:port
            h = h[1:].split("]", 1)[0]
        elif h.count(":") == 1:
            h = h.split(":", 1)[0]
        h = h.split("%", 1)[0].rstrip(".")
        try:
            ipaddress.ip_address(h)
            return True
        except ValueError:
            return h in self.remote_hosts()

    def remote_feed(self) -> dict:
        """What the toolbar shows: the status light, the caption line, the Names badge."""
        st = self.state()
        pp = st.get("pp") or {}
        return {"name": getattr(self, "_net_host", None) or "", "phase": st.get("phase"),
                "running": bool(st.get("running")), "paused": bool(st.get("paused")),
                "held": bool(st.get("held")), "song": bool(st.get("song")),
                "screen": st.get("screen") or "", "lag_s": st.get("lag_s"),
                "terms_n": int(st.get("session_terms_n") or 0),
                "pp": {"enabled": bool(pp.get("enabled", True)), "connected": bool(pp.get("connected"))}}

    # --- Bonjour -----------------------------------------------------------------------
    def _bonjour_sync(self):
        """Advertise _liveroen._tcp while the remote is on and the server runs (macOS only),
        through WATCH."""
        with self._bonjour_lock:
            self._bonjour_sync_locked()

    def _bonjour_sync_locked(self):
        want = self.remote_enabled() and getattr(self, "_net_server", None) is not None \
            and not getattr(self, "_net_error", None)
        if self._bonjour is not None and self._bonjour.poll() is not None:
            self._bonjour = None                   # dns-sd ended by itself: advertise again
        if want and self._bonjour is None and sys.platform == "darwin" and shutil.which("dns-sd"):
            port = int(self._display_cfg()["port"])
            name = f"Live RO2EN ({(self._net_host or 'Mac').removesuffix('.local')})"
            try:
                self._bonjour = subprocess.Popen(["/bin/sh", "-c", WATCH, "sh", name, str(port), str(os.getpid())],
                                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                self._bonjour = None
        elif not want and self._bonjour is not None:
            self._bonjour_stop_locked()

    def _bonjour_stop(self):
        with self._bonjour_lock:
            self._bonjour_stop_locked()

    def _bonjour_stop_locked(self):
        p, self._bonjour = self._bonjour, None
        if p is None:
            return
        try:
            os.killpg(p.pid, 15)                   # the shell and its dns-sd
        except OSError:
            p.terminate()
        try:
            p.wait(2)
        except subprocess.TimeoutExpired:
            p.kill()


def add_routes(app, live):
    """/remote/* on the network server. Each route answers 404 while the remote is off."""
    from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse

    from routes import body, run

    def check(conn) -> int:
        return live.remote_check(conn.headers.get(PIN_HEADER), conn.client.host if conn.client else "",
                                 conn.headers.get("host"), conn.headers.get("origin"))

    def guard(request: Request):
        code = check(request)
        if code != 200:
            raise HTTPException(code, {404: "not found", 403: "forbidden", 401: "wrong PIN",
                                       429: "too many wrong PINs: try again in a minute"}[code])

    async def flag(request: Request) -> bool:
        on = (await body(request)).get("on", True)
        if not isinstance(on, bool):
            raise HTTPException(400, "on must be true or false")
        return on

    @app.get("/remote/state")
    async def state(request: Request):
        guard(request)
        return live.remote_feed()

    for name in ("pause", "hold", "song"):
        def make(fn_name):
            async def toggle(request: Request):
                guard(request)
                await run(getattr(live, fn_name), await flag(request))
                return live.remote_feed()
            return toggle
        app.add_api_route(f"/remote/{name}", make(name), methods=["POST"])

    @app.post("/remote/blank")
    async def blank(request: Request):
        guard(request)
        await run(live.blank)
        return live.remote_feed()

    @app.post("/remote/retract")
    async def retract(request: Request):
        guard(request)
        if not await run(live.retract):
            return JSONResponse({"error": "nothing_to_change", "detail": "there is no caption to remove"}, 409)
        return live.remote_feed()

    @app.get("/remote/terms")
    async def get_terms(request: Request):
        guard(request)
        return live.terms_view()

    @app.post("/remote/terms")
    async def post_terms(request: Request):
        guard(request)
        b = await body(request)
        code, payload = await run(live.set_terms, {"lines": b.get("lines", []), "save": b.get("save") is True})
        if code != 200:
            return JSONResponse(payload, status_code=code)
        return live.terms_view()

    @app.websocket("/remote/ws")
    async def feed(ws: WebSocket):
        pin = ws.headers.get(PIN_HEADER)
        code = check(ws)
        await ws.accept()                          # then closed with a reason the remote can show
        if code != 200:
            await ws.close(code=4000 + code)       # 4401 wrong PIN, 4403 browser, 4404 off, 4429 locked out
            return
        with live._remote_lock:
            live._remote_clients += 1
        live._broadcast()
        last = None
        try:
            while True:
                if not live.remote_enabled() or not hmac.compare_digest(pin.encode(), live.remote_pin().encode()):
                    await ws.close(code=4401 if live.remote_enabled() else 4404)
                    return
                cur = json.dumps(live.remote_feed(), ensure_ascii=False)
                if cur != last:
                    await ws.send_text(cur)
                    last = cur
                await asyncio.sleep(FEED_EVERY_S)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return
        finally:
            with live._remote_lock:
                live._remote_clients -= 1
            live._broadcast()
