"""ProPresenter 7 HTTP API adapter (endpoints from openapi.propresenter.com).

  POST /v1/message/{id}/trigger  body [{"name": token, "text": {"text": ...}}]  -> show/update
  GET  /v1/message/{id}/clear                                                  -> hide
  PUT  /v1/stage/message         body "<json string>"                          -> stage text
  DELETE /v1/stage/message                                                     -> hide stage
Identical consecutive texts are not resent (avoids needless re-renders)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class ProPresenter:
    def __init__(self, host="127.0.0.1", port=50001, message_id="Live Translation",
                 token_name="Text", stage_message=False, timeout_s=1.5, **_):
        self.base = f"http://{host}:{port}"
        self.message_id = message_id
        self.token_name = token_name
        self.stage = stage_message
        self.timeout = timeout_s
        self._last = None
        self.last_error: str | None = None
        self.last_ms: float | None = None

    def _req(self, method: str, path: str, body=None) -> int:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
        t = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                code = r.status
            self.last_error = None
        except urllib.error.HTTPError as e:
            code = e.code
            self.last_error = f"HTTP {e.code} {method} {path}"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            code = 0
            self.last_error = f"{type(e).__name__}: {e}"
        self.last_ms = (time.perf_counter() - t) * 1000
        return code

    def _mid(self) -> str:
        return urllib.parse.quote(str(self.message_id), safe="")

    def show(self, text: str) -> bool:
        if text == self._last:
            return True
        ok = self._req("POST", f"/v1/message/{self._mid()}/trigger",
                       [{"name": self.token_name, "text": {"text": text}}]) in (200, 204)
        self._also_stage("PUT", text)
        if ok:
            self._last = text
        return ok

    def clear(self) -> bool:
        self._last = ""
        ok = self._req("GET", f"/v1/message/{self._mid()}/clear") in (200, 204)
        self._also_stage("DELETE")
        return ok

    def _also_stage(self, method: str, text=None) -> None:
        """Mirror to the stage message. Its result must not hide a failed main caption: the
        main error stays, and a stage failure is reported on its own."""
        if not self.stage:
            return
        main_err = self.last_error
        self._req(method, "/v1/stage/message", text)
        stage_err = self.last_error
        self.last_error = main_err or (f"stage: {stage_err}" if stage_err else None)

    def ping(self) -> bool:
        return self._req("GET", "/version") == 200
