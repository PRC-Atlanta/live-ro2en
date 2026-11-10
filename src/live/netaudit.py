"""Runtime network audit (used by the offline tests). When NET_AUDIT_LOG is set, every
DNS lookup or socket connection to a non-loopback address is appended to that file.
Native code (e.g. a model library's own HTTP client) bypasses Python sockets; the offline test also
runs under `sandbox-exec` with outbound network denied to cover that.

One exception, for the AI engine only (ai.py): `allow("api.anthropic.com")` names a host that may be
reached. A lookup of that host is not written down, and neither is a connection to an address it
resolved to; every other host still is. Local mode never calls `allow`, so its log must stay empty."""
from __future__ import annotations

import os
import sys
import threading
import time

_LOCAL = ("127.0.0.1", "::1", "localhost", "0.0.0.0", None, "")
_ALLOWED: set[str] = set()                 # host names
_ALLOWED_IPS: set[str] = set()             # what they resolved to
_BUSY = threading.local()


def allow(host: str) -> None:
    """Let `host` (a name, never a pattern) through the audit."""
    _ALLOWED.add(host.lower().rstrip("."))


def allowed() -> set[str]:
    return set(_ALLOWED)


def _learn(host: str) -> None:
    """The addresses an allowed host resolves to right now (our own lookup, not audited)."""
    import socket
    _BUSY.on = True
    try:
        for info in socket.getaddrinfo(host, None):
            _ALLOWED_IPS.add(info[4][0])
    except OSError:
        pass
    finally:
        _BUSY.on = False


def install() -> None:
    path = os.environ.get("NET_AUDIT_LOG")
    if not path:
        return

    def hook(event, args):
        if event not in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request"):
            return
        if getattr(_BUSY, "on", False):
            return
        target = args[1] if event == "socket.connect" else args[0]
        host = target[0] if isinstance(target, tuple) else target
        if event == "urllib.Request":
            from urllib.parse import urlparse
            host = urlparse(str(target)).hostname
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if isinstance(host, str) and (host in _LOCAL or host.startswith("127.") or host.startswith("/")):
            return
        if host in _LOCAL:
            return
        if isinstance(host, str) and _ALLOWED:
            name = host.lower().rstrip(".")
            if name in _ALLOWED:
                if event != "socket.connect":
                    _learn(name)
                return
            if event == "socket.connect" and host in _ALLOWED_IPS:
                return
        with open(path, "a") as f:
            f.write(f"{time.time():.3f} pid={os.getpid()} {event} {target!r}\n")
    sys.addaudithook(hook)
