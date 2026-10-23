"""Pages, state, the /ws feed and start/stop. Owned by S1."""
import asyncio
import pathlib

from fastapi import APIRouter, Request, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.routing import WebSocketRoute

from routes import body, not_built, run
from security import request_allowed

HERE = pathlib.Path(__file__).resolve().parents[1]
PAGE = (HERE / "control.html").read_text()


def router(live, hosts: set[str], port: int) -> APIRouter:
    r = APIRouter()

    @r.get("/")
    def index():                         # becomes the new UI (/ui/) when S5 is done
        return HTMLResponse(PAGE)

    @r.get("/legacy")
    def legacy():
        return HTMLResponse(PAGE)

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

    for method, path in (("POST", "/api/preload"), ("POST", "/api/shutdown"), ("POST", "/api/unload"),
                         ("GET", "/api/health"), ("GET", "/api/readiness"), ("GET", "/api/permissions")):
        not_built(r, method, path, "S1")

    # a plain route: with `from __future__ import annotations` FastAPI cannot resolve a
    # `sock: WebSocket` parameter and closed every connection with 1008
    async def ws(sock):
        if not request_allowed(sock.headers, "WS", hosts, port):
            await sock.close(code=1008)          # policy violation: not our control page
            return
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
    r.routes.append(WebSocketRoute("/ws", ws))
    return r
