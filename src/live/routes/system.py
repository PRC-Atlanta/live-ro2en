"""Pages, state, the /ws feed, start/stop, and the app lifecycle (preload, unload, shutdown,
health, readiness, permissions). Owned by S1."""
import threading

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.routing import WebSocketRoute

import permissions
from routes import body, run, state_feed
from security import request_allowed



def router(live, hosts: set[str], port: int) -> APIRouter:
    r = APIRouter()

    @r.get("/")
    def index():                         # the operator UI (S5); the old control page is retired
        return RedirectResponse("/ui/", status_code=307)

    @r.get("/api/state")
    def state():
        return JSONResponse(live.state())

    @r.post("/api/start")
    async def start(request: Request):
        await run(live.start, (await body(request)).get("device"))
        return live.state()

    @r.post("/api/stop")
    async def stop():
        await run(live.stop)
        return live.state()

    @r.post("/api/preload")
    async def preload(request: Request):
        """Load the models and keep a warm engine (non-blocking: watch `phase`/`progress`)."""
        await run(live.preload, (await body(request)).get("device"))
        return live.state()

    @r.post("/api/unload")
    async def unload():
        await run(live.unload)
        return live.state()

    @r.post("/api/shutdown")
    async def shutdown():
        """The Mac shell's Quit: stop cleanly, then the process exits (SIGTERM to ourselves,
        so uvicorn closes the server and the shutdown hook runs)."""
        await run(live.shutdown)
        t = threading.Timer(0.2, live.exit_process)      # after this answer has been sent
        t.daemon = True
        t.start()
        return {"ok": True}

    @r.get("/api/health")
    def health():
        return live.health()

    @r.get("/api/readiness")
    def readiness():
        return live.readiness()

    @r.get("/api/permissions")
    def perms():
        return permissions.status()

    r.on_shutdown.append(live.shutdown)          # uvicorn stopping (SIGTERM, Ctrl-C): no orphan engine

    # a plain route: with `from __future__ import annotations` FastAPI cannot resolve a
    # `sock: WebSocket` parameter and closed every connection with 1008
    async def ws(sock):
        if not request_allowed(sock.headers, "WS", hosts, port):
            await sock.close(code=1008)          # policy violation: not our control page
            return
        await sock.accept()
        await state_feed(live, sock)
    r.routes.append(WebSocketRoute("/ws", ws))
    return r
