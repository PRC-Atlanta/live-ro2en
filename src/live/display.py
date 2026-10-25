"""The captions display: a captions-only page (ui/display.html) for a second screen or
projector, opened from the toolbar in its own window. With `display.network: true` the same
page is also served to other devices on the local network by a second, VIEW-ONLY server
(`display.port`, default 8766): it answers GET / (the page) and /ws (the caption box, Hide
and Hold, the display settings), and nothing else, so turning it on cannot let anyone on
the church Wi-Fi control the service. Mixed into live.app.Live."""

import asyncio
import json
import pathlib
import socket
import subprocess
import sys
import threading

PAGE = pathlib.Path(__file__).resolve().parent / "ui" / "display.html"
DEFAULTS = {"size": "large", "lines": 2, "colors": "white_on_black", "network": False, "port": 8766,
            "show_hidden": False}


def local_name() -> str:
    """This Mac's Bonjour name (church-mac.local), else its host name."""
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["/usr/sbin/scutil", "--get", "LocalHostName"], capture_output=True,
                                 text=True, timeout=2).stdout.strip()
            if out:
                return f"{out}.local"
        except (OSError, subprocess.SubprocessError):
            pass
    return socket.gethostname()


class DisplayMixin:
    def _mixin_init(self):
        self._net_server = None                        # uvicorn.Server of the view-only page
        self._net_thread: threading.Thread | None = None
        self._net_error: str | None = None
        self._net_host: str | None = None
        self.register_state(lambda: {"display": self._display_state()})

    def _display_cfg(self) -> dict:
        return {**DEFAULTS, **(self.cfg.get("display") or {})}

    def _display_state(self) -> dict:
        d = self._display_cfg()
        on = self._net_server is not None and self._net_error is None
        return {"size": d["size"], "lines": int(d["lines"]), "colors": d["colors"],
                "network": bool(d["network"]), "show_hidden": bool(d["show_hidden"]),
                "url": f"http://{self._net_host}:{d['port']}/" if on and self._net_host else None,
                "network_error": self._net_error}

    def display_feed(self) -> dict:
        """What the view-only page gets: the caption box and how to show it, nothing else.
        With `display.show_hidden` Hide is ProPresenter's alone, so the page is told it is off."""
        paused = getattr(self, "paused", False) and not self._display_cfg()["show_hidden"]
        return {"screen": self.caps.text, "paused": paused,
                "held": getattr(self, "held", False), "running": self.running,
                "display": self._display_state()}

    # --- the view-only network server ---------------------------------------------------
    def start_display_server(self):
        """Start or stop the network page to match `display.network` (idempotent)."""
        want = bool(self._display_cfg()["network"])
        if want and self._net_server is None:
            self._net_start()
        elif not want and self._net_server is not None:
            self._net_stop()

    def _net_start(self):
        port = int(self._display_cfg()["port"])
        self._net_error = None
        try:                                           # fail now, visibly, if the port is taken
            probe = socket.socket()
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("0.0.0.0", port))
            probe.close()
        except OSError as e:
            self._net_error = f"port {port} is in use ({e.strerror or e})"
            return
        import uvicorn
        server = uvicorn.Server(uvicorn.Config(network_app(self), host="0.0.0.0", port=port,
                                               log_level="warning", lifespan="off"))
        server.install_signal_handlers = lambda: None  # the main server owns SIGTERM/SIGINT
        self._net_server, self._net_host = server, local_name()
        self._net_thread = threading.Thread(target=server.run, daemon=True, name="display-net")
        self._net_thread.start()

    def _net_stop(self):
        server, self._net_server = self._net_server, None
        if server is not None:
            server.should_exit = True
        th, self._net_thread = self._net_thread, None
        if th is not None and th is not threading.current_thread():
            th.join(5)

    def _display_changed(self, changed: dict):
        if "display.network" in changed:
            self.start_display_server()
        self._broadcast()


def network_app(live):
    """The view-only server: the page and a read-only feed. No other route exists."""
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import HTMLResponse
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def page():
        return HTMLResponse(PAGE.read_text(), headers={"Cache-Control": "no-cache"})

    @app.websocket("/ws")
    async def feed(ws: WebSocket):
        await ws.accept()
        last = None
        try:
            while True:
                cur = json.dumps(live.display_feed(), ensure_ascii=False)
                if cur != last:
                    await ws.send_text(cur)
                    last = cur
                await asyncio.sleep(0.25)
        except (WebSocketDisconnect, RuntimeError, OSError):
            return

    return app
