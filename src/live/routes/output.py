"""What reaches the screen: clear/blank, hide (pause), hold, song, remove last (retract), fix last
(correct), box size (layout), language mode, profile, and the ProPresenter test, test
caption and reconnect. Owned by S2 (`profile` calls Live.set_profile, owned by S4)."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from routes import body, run


def _bad(msg: str) -> JSONResponse:
    return JSONResponse({"error": "bad_request", "detail": msg}, status_code=400)


def _int_or_none(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
        raise ValueError("lines and chars must be whole numbers")
    return int(v)


def router(live) -> APIRouter:
    r = APIRouter()
    live.start_pp_monitor()                    # health pings from launch, before Start

    @r.post("/api/clear")
    async def clear():
        await run(live.clear)
        return live.state()

    @r.post("/api/blank")
    async def blank():
        await run(live.blank)
        return live.state()

    @r.post("/api/pause")
    async def pause(request: Request):
        on = (await body(request)).get("on", True)
        if not isinstance(on, bool):
            return _bad("on must be true or false")
        await run(live.pause, on)
        return live.state()

    @r.post("/api/hold")
    async def hold(request: Request):
        on = (await body(request)).get("on", True)
        if not isinstance(on, bool):
            return _bad("on must be true or false")
        await run(live.hold, on)
        return live.state()

    @r.post("/api/song")
    async def song(request: Request):
        on = (await body(request)).get("on", True)
        if not isinstance(on, bool):
            return _bad("on must be true or false")
        await run(live.song, on)
        return live.state()

    @r.post("/api/retract")
    async def retract():
        if not await run(live.retract):
            return JSONResponse({"error": "nothing_to_change", "detail": "there is no caption to remove"}, 409)
        return live.state()

    @r.post("/api/correct")
    async def correct(request: Request):
        text = (await body(request)).get("text")
        if not isinstance(text, str) or not text.strip():
            return _bad("text is required")
        if not await run(live.correct, text):
            return JSONResponse({"error": "nothing_to_change", "detail": "there is no caption to fix"}, 409)
        return live.state()

    @r.post("/api/layout")
    async def layout(request: Request):
        b = await body(request)
        try:
            await run(live.layout, _int_or_none(b.get("lines")), _int_or_none(b.get("chars")))
        except ValueError as e:
            return _bad(str(e))
        return live.state()

    @r.post("/api/mode")
    async def mode(request: Request):
        live.set_mode((await body(request))["mode"])
        return live.state()

    @r.post("/api/profile")
    async def profile(request: Request):
        name = (await body(request)).get("profile")
        if not isinstance(name, str) or name not in (live.cfg.get("profiles") or {}):
            return _bad("unknown profile")
        await run(live.set_profile, name)
        return live.state()

    @r.post("/api/pp/test")
    async def pp_test():
        return await run(live.pp_test)

    @r.post("/api/pp/test_caption")
    async def pp_test_caption():
        return await run(live.pp_test_caption)

    @r.post("/api/remote/pin")
    async def remote_pin():
        await run(live.renew_remote_pin)
        return live.state()

    @r.post("/api/reconnect")
    async def reconnect():
        await run(live.reconnect)
        return live.state()

    return r
