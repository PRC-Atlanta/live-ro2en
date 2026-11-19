"""What reaches the screen: clear/blank, hide (pause), hold, song, remove last (retract), fix last
(correct), box size (layout), language mode, profile, and the ProPresenter test, test
caption and reconnect. Owned by S2 (`profile` calls Live.set_profile, owned by S4)."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from output import StaleCorrection
from routes import body, run

MODES = ("auto", "ro-en", "en-ro")


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
        b = await body(request)
        text, based_on = b.get("text"), b.get("based_on")
        if not isinstance(text, str) or not text.strip():
            return _bad("text is required")
        if based_on is not None and not isinstance(based_on, str):
            return _bad("based_on must be text")
        seq = b.get("based_on_seq")            # the number of the caption being fixed (state lines[].seq)
        if seq is not None and (not isinstance(seq, int) or isinstance(seq, bool)):
            return _bad("based_on_seq must be a whole number")
        try:
            done = await run(live.correct, text, based_on, seq)
        except StaleCorrection:                # the operator typed over a caption that is gone now
            return JSONResponse({"error": "stale", "detail": "The last caption changed, so nothing was changed."}, 409)
        if not done:
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
        mode = (await body(request)).get("mode")
        if mode not in MODES:
            return _bad("mode must be auto, ro-en or en-ro")
        await run(live.set_mode, mode)
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

    @r.post("/api/engine")
    async def engine(request: Request):
        code, out = await run(live.set_engine, (await body(request)).get("engine"))
        return out if code == 200 else JSONResponse(out, status_code=code)

    @r.post("/api/ai/key")
    async def ai_key(request: Request):
        b = await body(request)
        if "key" not in b or not (b["key"] is None or isinstance(b["key"], str)):
            return _bad("key must be a string, or null to remove it")
        code, out = await run(live.set_ai_key, b["key"])
        return out if code == 200 else JSONResponse(out, status_code=code)

    @r.post("/api/voice/test")
    async def voice_test(request: Request):
        lang = (await body(request)).get("lang", "ro")
        if lang not in ("ro", "en"):
            return _bad("lang must be ro or en")
        return {"ok": bool(await run(live.voice_test, lang))}

    @r.post("/api/reconnect")
    async def reconnect():
        await run(live.reconnect)
        return live.state()

    return r
