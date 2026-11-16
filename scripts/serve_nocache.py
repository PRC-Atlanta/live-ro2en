#!/usr/bin/env python3
"""A static file server for local bench pages that tells the browser never to cache (so a rebuilt
player.html is always the one shown). Usage: serve_nocache.py PORT [DIR] [BIND]; binds 127.0.0.1
unless BIND is given (e.g. this Mac's Tailscale address, to reach it from your own devices only)."""
import functools
import http.server
import sys


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


port = int(sys.argv[1])
handler = functools.partial(NoCache, directory=sys.argv[2] if len(sys.argv) > 2 else ".")
bind = sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"
http.server.ThreadingHTTPServer((bind, port), handler).serve_forever()
