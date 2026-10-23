"""HTTP routes, one router per work stream. Each module exposes `router(live) -> APIRouter`.
Endpoints that are planned but not built answer 501 with NOT_BUILT's shape (contract.json)."""
import asyncio

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


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
