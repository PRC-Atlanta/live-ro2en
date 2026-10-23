"""Before and after the sermon: preflight, rating, export, diagnostics. Owned by S9."""
from fastapi import APIRouter

from routes import not_built


def router(live) -> APIRouter:
    r = APIRouter()
    for path in ("preflight", "rate", "export", "diagnostics", "rehearsal"):
        not_built(r, "POST", f"/api/{path}", "S9")
    return r
