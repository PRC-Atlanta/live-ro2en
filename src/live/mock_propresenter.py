"""Mock ProPresenter 7 API server for tests and dry runs (stdlib only).

  python src/live/mock_propresenter.py --port 50001
Implements /version, /v1/messages, /v1/message/{id}, /v1/message/{id}/trigger,
/v1/message/{id}/clear, /v1/status/layers, /v1/stage/message. Every call is recorded with a
timestamp in `MockState.events`; GET /mock/events returns them as JSON (used by the
end-to-end latency test).

Fault injection (tests and dry runs; any method):
  /mock/fail {"n": 3}   the next n API calls answer 500
  /mock/down            API calls get the connection closed with no answer (unreachable)
  /mock/up              back to normal
  /mock/clear           what an operator's "clear" in ProPresenter does: hide every message"""
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockState:
    def __init__(self, messages=("Live Translation",), token="Text"):
        self.messages = {m: {"id": {"name": m, "uuid": f"uuid-{i}", "index": i},
                             "message": "{" + token + "}", "tokens": [{"name": token}]}
                         for i, m in enumerate(messages)}
        self.token = token
        self.visible: dict[str, str] = {}
        self.stage: str | None = None
        self.events: list[dict] = []
        self.fail_n = 0                       # /mock/fail: the next n API calls answer 500
        self.down = False                     # /mock/down: connections are dropped
        self.lock = threading.Lock()

    def find(self, mid: str):
        for name, m in self.messages.items():
            if mid in (name, m["id"]["uuid"], str(m["id"]["index"])):
                return name
        return None


def make_handler(state: MockState):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body=None):
            data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            if data:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n)) if n else None

        def _route(self, method):
            path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
            parts = path.strip("/").split("/")
            body = self._body() if method in ("POST", "PUT") else None
            with state.lock:
                ev = {"t": time.time(), "method": method, "path": path, "body": body}
                state.events.append(ev)
                if path == "/mock/fail":
                    state.fail_n = int((body or {}).get("n", 1)) if isinstance(body, dict) else 1
                    return self._send(200, {"fail_n": state.fail_n})
                if path in ("/mock/down", "/mock/up"):
                    state.down = path == "/mock/down"
                    return self._send(200, {"down": state.down})
                if path == "/mock/clear":
                    state.visible.clear()
                    state.stage = None
                    return self._send(200, {})
                if not path.startswith("/mock/"):
                    if state.down:
                        ev["fault"] = "down"
                        self.close_connection = True     # no answer at all: "unreachable"
                        return None
                    if state.fail_n > 0:
                        state.fail_n -= 1
                        ev["fault"] = "fail"
                        return self._send(500)
                if path == "/version":
                    return self._send(200, {"name": "Mock ProPresenter", "host_description": "mock",
                                            "api_version": "v1"})
                if path == "/mock/events":
                    return self._send(200, state.events)
                if path == "/v1/status/layers" and method == "GET":
                    return self._send(200, {"video_input": False, "media": False, "slide": False,
                                            "announcements": False, "props": False,
                                            "messages": bool(state.visible), "audio": False})
                if path == "/v1/messages" and method == "GET":
                    return self._send(200, list(state.messages.values()))
                if len(parts) >= 3 and parts[:2] == ["v1", "message"]:
                    name = state.find(parts[2])
                    if name is None:
                        return self._send(404)
                    if len(parts) == 4 and parts[3] == "trigger" and method == "POST":
                        if not isinstance(body, (list, type(None))):
                            return self._send(400)
                        text = next((t.get("text", {}).get("text", "") for t in (body or [])
                                     if t.get("name") == state.token), "")
                        state.visible[name] = text
                        return self._send(204)
                    if len(parts) == 4 and parts[3] == "clear" and method == "GET":
                        state.visible.pop(name, None)
                        return self._send(204)
                    if len(parts) == 3 and method == "GET":
                        return self._send(200, state.messages[name])
                if path == "/v1/stage/message":
                    if method == "PUT":
                        if not isinstance(body, str):
                            return self._send(400)
                        state.stage = body
                        return self._send(204)
                    if method == "DELETE":
                        state.stage = None
                        return self._send(204)
            return self._send(404)

        def do_GET(self):
            self._route("GET")

        def do_POST(self):
            self._route("POST")

        def do_PUT(self):
            self._route("PUT")

        def do_DELETE(self):
            self._route("DELETE")
    return H


def serve(port: int = 50001, host: str = "127.0.0.1", state: MockState | None = None):
    state = state or MockState()
    srv = ThreadingHTTPServer((host, port), make_handler(state))
    th = threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.05), daemon=True)
    th.start()
    return srv, state


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=50001)
    a = ap.parse_args()
    srv, st = serve(a.port)
    print(f"mock ProPresenter on http://127.0.0.1:{a.port}")
    try:
        while True:
            time.sleep(1)
            if st.visible:
                print("\r" + " | ".join(st.visible.values()).replace("\n", " / ")[:150], end="", flush=True)
    except KeyboardInterrupt:
        srv.shutdown()
