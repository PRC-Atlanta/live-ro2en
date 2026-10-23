"""First run: input devices, model download, hardware checks. Owned by S6.

The download runs in a separate child (model_setup.py) with LIVE_ALLOW_NETWORK=1, so this
process never gets network access. The wizard polls GET /api/setup for `download` progress."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import model_setup
from routes import body, run


def router(live) -> APIRouter:
    r = APIRouter()
    def ready():                          # the models are in: warm up now, so the wizard's sound
        live._models_cache = (-1e9, [])   # step gets a level and Start is instant
        if getattr(live, "_keep_warm", False):
            live.preload()

    setup = model_setup.Setup(root=lambda: live.root, on_done=ready)
    live.model_setup = setup              # for the needs_setup phase / models_missing problem (S1, S11)

    @r.get("/api/devices")
    def devices():
        try:
            import sounddevice as sd
            return [{"index": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
                    if d["max_input_channels"] > 0]
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @r.get("/api/setup")
    def get_setup():
        return setup.status()

    @r.post("/api/setup/download")
    async def download(request: Request):
        b = await body(request)
        if getattr(live, "running", False):        # never during a service
            return JSONResponse({"error": "live", "msg": "stop captions before downloading models"},
                                status_code=409)
        profile = b.get("profile") or (await run(setup.status))["recommended_profile"]
        try:
            await run(setup.start, profile)
        except ValueError as e:
            return JSONResponse({"error": "bad_profile", "msg": str(e)}, status_code=400)
        return await run(setup.status)

    @r.post("/api/setup/cancel")
    async def cancel():
        await run(setup.cancel)
        return await run(setup.status)

    return r
