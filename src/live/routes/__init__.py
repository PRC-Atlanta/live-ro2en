"""HTTP routes, one router per work stream. Each module exposes `router(live) -> APIRouter`.
Endpoints that are planned but not built answer 501 with NOT_BUILT's shape (contract.json)."""
import asyncio

import json

from fastapi import APIRouter, HTTPException, Request, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles


class NoCache(StaticFiles):
    """The operator UI's files: an app update must never run against yesterday's cached UI."""
    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


async def state_feed(live, sock, still_ok=lambda: True):
    """The /ws feed on an accepted socket: every state as it changes, and at least once a second.
    Ends when the page goes, or when `still_ok()` turns false (checked at least once a second)."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    loop = asyncio.get_running_loop()

    class Bridge:
        def put_nowait(self, s):
            loop.call_soon_threadsafe(lambda: q.full() or q.put_nowait(s))
    b = Bridge()
    live.listeners.add(b)
    try:
        await sock.send_json(live.state())
        while still_ok():
            try:
                s = await asyncio.wait_for(q.get(), 1.0)
            except asyncio.TimeoutError:
                s = live.state()
            await sock.send_json(s)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        live.listeners.discard(b)


async def body(request: Request) -> dict:
    """The JSON object posted, or {} when there is none. Anything else is refused before the
    handler runs: a text/plain or form POST is one a foreign web page can send without asking."""
    raw = await request.body()
    if not raw:
        return {}
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/json":
        raise HTTPException(415, "send JSON (Content-Type: application/json)")
    try:
        b = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "the body is not valid JSON") from None
    if not isinstance(b, dict):
        raise HTTPException(400, "the body must be a JSON object")
    return b


async def run(fn, *args):
    """Blocking Live calls (start, stop, profile, clear) run off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def not_built(r: APIRouter, method: str, path: str, owner: str):
    async def handler():
        return JSONResponse({"error": "not_implemented", "endpoint": f"{method} {path}", "owner": owner},
                            status_code=501)
    r.add_api_route(path, handler, methods=[method], name=f"{owner}:{method} {path}")
