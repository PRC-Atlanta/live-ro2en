"""Only the control page itself may drive or watch the service."""
from __future__ import annotations

from urllib.parse import urlsplit

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
FORBIDDEN = ("forbidden: only the control page may use this API. Opening it from another machine? "
             "Add that host name to live.web_allowed_hosts in config.yaml.")


def allowed_hosts(lc: dict) -> set[str]:
    """Host names the control page may be reached by (loopback + web_host + extras)."""
    return {"127.0.0.1", "localhost", "::1", str(lc.get("web_host", "127.0.0.1"))} | \
        set(lc.get("web_allowed_hosts") or [])


def request_allowed(headers, method: str, hosts: set[str], port: int) -> bool:
    """Host must be one of ours (stops DNS rebinding); anything that changes state, and the
    WebSocket, must come from our own origin (stops any other web page in a browser on this
    Mac from stopping or clearing the captions, or reading the transcript)."""
    if urlsplit("//" + (headers.get("host") or "")).hostname not in hosts:
        return False
    if method in ("GET", "HEAD"):
        return True
    origin = headers.get("origin")
    if origin is None:                   # non-browser client (curl, scripts): Host check is enough
        return method == "WS" or "application/json" in (headers.get("content-type") or "")
    o = urlsplit(origin)
    if o.hostname not in hosts or o.scheme not in ("http", "https"):
        return False
    # the page itself, or the same host behind a proxy listed in web_allowed_hosts
    return (o.port or {"http": 80, "https": 443}[o.scheme]) == port or o.hostname not in LOOPBACK


def install(app, hosts: set[str], port: int):
    """Reject every HTTP request that does not come from the control page."""
    from fastapi import Request
    from fastapi.responses import JSONResponse

    @app.middleware("http")
    async def same_origin_only(request: Request, call_next):
        if not request_allowed(request.headers, request.method, hosts, port):
            return JSONResponse({"error": FORBIDDEN}, status_code=403)
        return await call_next(request)
