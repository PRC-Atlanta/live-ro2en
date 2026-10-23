"""What reaches the screen: clear, language mode, profile; hide/retract/ProPresenter tests
to come. Owned by S2 (`profile` calls Live.set_profile, owned by S4)."""
from fastapi import APIRouter, Request

from routes import body, not_built, run


def router(live) -> APIRouter:
    r = APIRouter()

    @r.post("/api/clear")
    async def clear():
        await run(live.clear)
        return live.state()

    @r.post("/api/mode")
    async def mode(request: Request):
        live.set_mode((await body(request))["mode"])
        return live.state()

    @r.post("/api/profile")
    async def profile(request: Request):
        await run(live.set_profile, (await body(request))["profile"])
        return live.state()

    for path in ("pause", "blank", "hold", "retract", "correct", "layout", "pp/test", "pp/test_caption",
                 "reconnect"):
        not_built(r, "POST", f"/api/{path}", "S2")
    return r
