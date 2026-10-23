"""HTTP routes, one router per work stream. Each module exposes `router(live) -> APIRouter`.
Endpoints that are planned but not built answer 501 with NOT_BUILT's shape (contract.json)."""
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


async def body(request: Request) -> dict:
    """The JSON object posted, or {} when there is none."""
    if not await request.body():
        return {}
    b = await request.json()
    return b if isinstance(b, dict) else {}


async def run(fn, *args):
    """Blocking Live calls (start, stop, profile, clear) run off the event loop."""
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def not_built(r: APIRouter, method: str, path: str, owner: str):
    async def handler():
        return JSONResponse({"error": "not_implemented", "endpoint": f"{method} {path}", "owner": owner},
                            status_code=501)
    r.add_api_route(path, handler, methods=[method], name=f"{owner}:{method} {path}")
