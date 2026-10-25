"""Runtime network audit (used by the offline tests). When NET_AUDIT_LOG is set, every
DNS lookup or socket connection to a non-loopback address is appended to that file.
Native code (e.g. a model library's own HTTP client) bypasses Python sockets; the offline test also
runs under `sandbox-exec` with outbound network denied to cover that."""
from __future__ import annotations

import os
import sys
import time

_LOCAL = ("127.0.0.1", "::1", "localhost", "0.0.0.0", None, "")


def install() -> None:
    path = os.environ.get("NET_AUDIT_LOG")
    if not path:
        return

    def hook(event, args):
        if event not in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "urllib.Request"):
            return
        target = args[1] if event == "socket.connect" else args[0]
        host = target[0] if isinstance(target, tuple) else target
        if event == "urllib.Request":
            from urllib.parse import urlparse
            host = urlparse(str(target)).hostname
        if isinstance(host, str) and (host in _LOCAL or host.startswith("127.") or host.startswith("/")):
            return
        if host in _LOCAL:
            return
        with open(path, "a") as f:
            f.write(f"{time.time():.3f} pid={os.getpid()} {event} {target!r}\n")
    sys.addaudithook(hook)
