"""ProPresenter 7 HTTP API adapter (endpoints from openapi.propresenter.com).

  POST /v1/message/{id}/trigger  body [{"name": token, "text": {"text": ...}}]  -> show/update
  GET  /v1/message/{id}/clear                                                  -> hide
  GET  /v1/message/{id}                                                        -> does it exist
  GET  /v1/messages                                                            -> all messages
  GET  /v1/status/layers         {"messages": bool, ...}                       -> is one showing
  PUT  /v1/stage/message         body "<json string>"                          -> stage text
  DELETE /v1/stage/message                                                     -> hide stage
  GET  /version                                                                -> ping
Identical consecutive texts are not resent (avoids needless re-renders).

Robustness: the adapter remembers what *should* be on screen (`_want`) apart from what was
last confirmed sent (`_last`). `health()` pings, checks the message exists, and re-sends the
wanted text after an outage or after someone clears the message by hand in ProPresenter.
While ProPresenter is unreachable, sends are skipped until the next try (1, 2, 5, 5 … s), so
a dead host never stalls the caption pipeline on a timeout per caption."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


class ProPresenter:
    def __init__(self, host="127.0.0.1", port=50001, message_id="Live Translation",
                 token_name="Text", stage_message=False, timeout_s=1.5, backoff_s=(1, 2, 5), **_):
        self.base = f"http://{host}:{port}"
        self.message_id = message_id
        self.token_name = token_name
        self.stage = stage_message
        self.timeout = timeout_s
        self.backoff = tuple(backoff_s) or (0,)
        self._last: str | None = None              # last text confirmed on screen (None: unknown)
        self._want: str | None = None              # what should be on screen (None: nothing asked yet)
        self.last_error: str | None = None
        self.last_ms: float | None = None
        self.connected = False
        self.since: float | None = None            # when `connected` last changed
        self.error_code: str | None = None         # unreachable | auth | message_missing | None
        self.fails = 0                             # consecutive unreachable requests
        self._next_try = 0.0
        self._last_health = 0.0
        self.resends = 0                           # times health() put the text back
        self._lock = threading.RLock()             # one request at a time, in order

    # --- transport -----------------------------------------------------------------------
    def _req(self, method: str, path: str, body=None, want_json=False):
        """One request. Returns the status code (0 = unreachable), or (code, json) with
        want_json. Updates the connection state."""
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        t = time.perf_counter()
        payload = None
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                code = r.status
                if want_json:
                    try:
                        payload = json.loads(r.read() or b"null")
                    except ValueError:
                        payload = None
            self.last_error = None
        except urllib.error.HTTPError as e:
            code = e.code
            self.last_error = f"HTTP {e.code} {method} {path}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            code = 0
            self.last_error = f"{type(e).__name__}: {e}"
        self.last_ms = (time.perf_counter() - t) * 1000
        self._track(code, path)
        return (code, payload) if want_json else code

    def _track(self, code: int, path: str):
        now = time.time()
        up = code != 0
        if up != self.connected or self.since is None:
            self.connected, self.since = up, now
        if not up:
            self.fails += 1
            self._next_try = now + self.backoff[min(self.fails, len(self.backoff)) - 1]
            self.error_code = "unreachable"
            return
        self.fails, self._next_try = 0, 0.0
        on_message = path.startswith("/v1/message/")
        if code in (401, 403):
            self.error_code = "auth"
        elif code == 404 and on_message:
            self.error_code = "message_missing"
        elif 200 <= code < 300 and (on_message or self.error_code == "unreachable"):
            self.error_code = None

    def _mid(self) -> str:
        return urllib.parse.quote(str(self.message_id), safe="")

    def _backing_off(self) -> bool:
        return bool(self.fails) and time.time() < self._next_try

    # --- captions ------------------------------------------------------------------------
    def show(self, text: str) -> bool:
        with self._lock:
            self._want = text
            if text == self._last:
                return True
            if self._backing_off():            # unreachable: health() re-sends on recovery
                return False
            return self._send(text)

    def clear(self) -> bool:
        with self._lock:
            self._want = ""
            return self._send("")

    def _send(self, text: str) -> bool:
        """Put `text` on screen now ("" = hide), whatever was sent before. Under _lock."""
        if text:
            ok = self._req("POST", f"/v1/message/{self._mid()}/trigger",
                           [{"name": self.token_name, "text": {"text": text}}]) in (200, 204)
            self._also_stage("PUT", text)
        else:
            ok = self._req("GET", f"/v1/message/{self._mid()}/clear") in (200, 204)
            self._also_stage("DELETE")
        self._last = text if ok else None      # unknown after a failure: resend next time
        return ok

    def _also_stage(self, method: str, text=None) -> None:
        """Mirror to the stage message. Its result must not hide a failed main caption: the
        main error stays, and a stage failure is reported on its own."""
        if not self.stage:
            return
        main_err, main_code = self.last_error, self.error_code
        self._req(method, "/v1/stage/message", text)
        stage_err = self.last_error
        self.last_error = main_err or (f"stage: {stage_err}" if stage_err else None)
        if main_err:
            self.error_code = main_code

    def force_resend(self) -> bool:
        """Send the wanted text again even if it was sent before (a manual clear, a
        ProPresenter restart). Nothing asked yet: nothing to do."""
        with self._lock:
            if self._want is None:
                return True
            return self._send(self._want)

    # --- health --------------------------------------------------------------------------
    def ping(self) -> bool:
        return self._req("GET", "/version") == 200

    def list_messages(self) -> list[str] | None:
        """Names of the messages defined in ProPresenter, or None when it cannot be asked."""
        with self._lock:
            code, msgs = self._req("GET", "/v1/messages", want_json=True)
        if code != 200 or not isinstance(msgs, list):
            return None
        out = []
        for m in msgs:
            ident = m.get("id") if isinstance(m, dict) else None
            name = ident.get("name") if isinstance(ident, dict) else None
            if name:
                out.append(str(name))
        return out

    def _messages_layer_showing(self) -> bool | None:
        """Whether ProPresenter shows a message now (None: this version cannot say)."""
        code, layers = self._req("GET", "/v1/status/layers", want_json=True)
        if code != 200 or not isinstance(layers, dict) or "messages" not in layers:
            if code == 404:
                self.last_error = None             # an older ProPresenter: not an error
            return None
        return bool(layers["messages"])

    def health(self, resend: bool = True) -> dict:
        """Ping, check the message exists, and put the wanted text back if it is not on
        screen: after an outage (it was never confirmed) or after a manual clear in
        ProPresenter (the messages layer is off while we expect a caption)."""
        with self._lock:
            self._last_health = time.time()
            if self._req("GET", "/version") != 200:
                return self.status()
            if self._req("GET", f"/v1/message/{self._mid()}") != 200 or not resend:
                return self.status()
            if self._want is not None and self._want != self._last:
                if self._send(self._want):
                    self.resends += 1
            elif self._want and self._messages_layer_showing() is False:
                if self._send(self._want):
                    self.resends += 1
            return self.status()

    def health_due(self, every_s: float) -> bool:
        """Time for the next health check: every `every_s` while fine, on the backoff
        schedule while unreachable."""
        now = time.time()
        if self.fails:
            return now >= self._next_try
        return now - self._last_health >= every_s

    def reconnect(self) -> dict:
        """Operator pressed Reconnect: forget the backoff, check now and re-send."""
        with self._lock:
            self.fails, self._next_try = 0, 0.0
            st = self.health(resend=False)
            if self.connected and self.error_code is None:
                self.force_resend()
            return self.status() if self.connected else st

    def status(self) -> dict:
        return {"connected": bool(self.connected), "since": self.since,
                "last_ms": round(self.last_ms, 1) if self.last_ms is not None else None,
                "error_code": self.error_code}
