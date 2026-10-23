"""First run: input devices, model download, hardware checks. Owned by S6."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from routes import not_built


def router(live) -> APIRouter:
    r = APIRouter()

    @r.get("/api/devices")
    def devices():
        try:
            import sounddevice as sd
            return [{"index": i, "name": d["name"]} for i, d in enumerate(sd.query_devices())
                    if d["max_input_channels"] > 0]
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    for method, path in (("GET", "/api/setup"), ("POST", "/api/setup/download"), ("POST", "/api/setup/cancel")):
        not_built(r, method, path, "S6")
    return r
