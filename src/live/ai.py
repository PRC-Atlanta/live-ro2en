"""The AI engine: Claude Haiku 5.5 through the Anthropic API, as the translation and summary engine
the operator may choose instead of the local models (the AI / Local knob).

Captions never wait for the network. The local models stay loaded as a hot spare; every phrase has a
time budget (`ai.timeout_s`, from the measured latency: median 0.9 s, p99 2.2 s on 2026-11-10), and on a
timeout, an error or no network the phrase is translated locally and the engine is marked down. While
it is down a small probe call every `ai.retry_s` finds out when the API works again, and the engine
comes back by itself. `state()` says which engine is really running.

The key: ANTHROPIC_API_KEY in the environment; in the app, the macOS Keychain (set from Settings); in
a dev checkout, `.env`. It is never logged, printed, put in a state message or written to a file here.
This is the only module that may reach the network at run time, and only api.anthropic.com
(netaudit.allow)."""
from __future__ import annotations

import collections
import hashlib
import json
import os
import pathlib
import re
import subprocess
import threading
import time

import netaudit

HOST = "api.anthropic.com"
BASE_URL = "https://" + HOST               # fixed: never from the environment (ANTHROPIC_BASE_URL is ignored)
ENV_IGNORED = ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_CONFIG_DIR")
MODEL = "claude-haiku-5-5"
DEFAULTS = {"enabled": False, "model": MODEL, "timeout_s": 2.5, "phrase_s": 4.0, "retry_s": 10.0,
            "summary_timeout_s": 3.0}
KEY_RE = re.compile(r"\Ask-ant-[A-Za-z0-9_\-]{20,300}\Z")     # \Z: no trailing newline slips through
SERVICE_RE = re.compile(r"\A[\w .\-]{1,64}\Z")
KEYCHAIN_SERVICE = "Live RO2EN Anthropic API key"
KEYCHAIN_ACCOUNT = "live-ro2en"
SECURITY = "/usr/bin/security"
PROBE = ("Translate into English: Amin.", 8)       # the smallest real request


class Unavailable(Exception):
    """The API did not answer in time or at all; `reason` is a short word for the log and the UI."""

    def __init__(self, reason: str, retryable: bool = True):
        super().__init__(reason)
        self.reason, self.retryable = reason, retryable


# --- the key ------------------------------------------------------------------------------------
def _keychain_service() -> str:
    name = os.environ.get("LIVE_KEYCHAIN_SERVICE") or KEYCHAIN_SERVICE
    return name if SERVICE_RE.match(name) else KEYCHAIN_SERVICE      # it goes inside a quoted command line


def keychain_get() -> str | None:
    if os.environ.get("LIVE_AI_NO_KEYCHAIN") == "1":
        return None
    try:
        r = subprocess.run([SECURITY, "find-generic-password", "-s", _keychain_service(), "-a", KEYCHAIN_ACCOUNT, "-w"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    key = r.stdout.strip() if r.returncode == 0 else ""
    return key if KEY_RE.match(key) else None


def keychain_set(key: str) -> bool:
    """Store the key in the login keychain. The key travels on the `security` tool's stdin (its
    interactive mode), never on a command line, where any process of this user could read it."""
    if not KEY_RE.match(key or "") or os.environ.get("LIVE_AI_NO_KEYCHAIN") == "1":
        return False
    cmd = f'add-generic-password -U -s "{_keychain_service()}" -a "{KEYCHAIN_ACCOUNT}" -w "{key}"\n'
    try:
        r = subprocess.run([SECURITY, "-i"], input=cmd, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and keychain_get() == key


def keychain_delete() -> bool:
    if os.environ.get("LIVE_AI_NO_KEYCHAIN") == "1":
        return False
    try:
        r = subprocess.run([SECURITY, "delete-generic-password", "-s", _keychain_service(), "-a", KEYCHAIN_ACCOUNT],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def dotenv_key(root: pathlib.Path) -> str | None:
    """ANTHROPIC_API_KEY from <root>/.env (a dev checkout only)."""
    try:
        for line in (root / ".env").read_text().splitlines():
            m = re.match(r"^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=\s*['\"]?([^'\"\s#]+)", line)
            if m and KEY_RE.match(m[1]):
                return m[1]
    except OSError:
        pass
    return None


def find_key(root: pathlib.Path | None = None) -> tuple[str | None, str | None]:
    """(key, where it came from: keychain | env | dotenv), or (None, None). The Keychain first: it is what
    Settings shows and changes, so a key saved there is the key in use."""
    if os.environ.get("LIVE_AI_NO_KEYCHAIN") != "1":
        k = keychain_get()
        if k:
            return k, "keychain"
    env = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if KEY_RE.match(env):
        return env, "env"
    if root is not None and not os.environ.get("LIVE_HOME") and os.environ.get("LIVE_AI_NO_DOTENV") != "1":
        k = dotenv_key(root)
        if k:
            return k, "dotenv"
    return None, None


# --- the engine -------------------------------------------------------------------------------------
class Engine:
    """One per app. `call()` is the only thing that touches the network."""

    def __init__(self, cfg: dict | None = None, root: pathlib.Path | None = None, log=None, client=None):
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.root = root
        self.log = log or (lambda rec: None)
        self._client = client                    # tests pass a fake
        self._key: str | None = None
        self._key_source: str | None = "test" if client is not None else None
        self._key_checked = client is not None
        self._pool = None                        # one worker: a call is waited for only as long as its budget
        self._lock = threading.RLock()
        self.up = True                           # False: falling back to local until a probe succeeds
        self.down_reason: str | None = None
        self.down_since: float | None = None
        self._probe: threading.Thread | None = None
        self._closed = False
        self.counts: collections.Counter = collections.Counter()
        self.lat: collections.deque = collections.deque(maxlen=200)
        self._cache_path = pathlib.Path(os.environ["LIVE_AI_CACHE"]) if os.environ.get("LIVE_AI_CACHE") else None
        self._cache: dict[str, dict] | None = None
        self._usage_log = (pathlib.Path(os.environ["LIVE_LOG_DIR"]) / "claude_usage.jsonl"
                           if os.environ.get("LIVE_LOG_DIR") else None)

    # --- settings and key ----------------------------------------------------------------------
    @property
    def model(self) -> str:
        return str(self.cfg.get("model") or MODEL)

    def configure(self, cfg: dict | None):
        self.cfg = {**DEFAULTS, **(cfg or {})}

    def reload_key(self):
        """Look for the key again (it was set or removed in Settings)."""
        with self._lock:
            if self._key_source == "test":
                return
            self._client, self._key, self._key_source, self._key_checked = None, None, None, False
            self.up, self.down_reason, self.down_since = True, None, None

    def _lookup(self):
        """Find the key once (the Keychain tool may take a moment). No client, no SDK, no network."""
        with self._lock:
            if not self._key_checked:
                self._key, self._key_source = find_key(self.root)
                self._key_checked = True

    def key_present(self, wait: bool = True) -> bool:
        """Is there a key? Only looks; builds nothing and allows no host (Local mode asks this too).
        `wait=False`: never blocks on the Keychain (the state feed): the lookup runs on a thread and
        the answer is False until it is in."""
        if self._client is not None:
            return True
        if not self._key_checked:
            if wait:
                self._lookup()
            else:
                th = getattr(self, "_lookup_thread", None)
                if th is None or not th.is_alive():
                    self._lookup_thread = threading.Thread(target=self._lookup, daemon=True, name="ai-key")
                    self._lookup_thread.start()
                return False
        return self._key is not None

    def client(self):
        """The API client, built on first use in AI mode only: this is where the SDK is imported and
        the one host is let through the audit. Everything that could point it elsewhere is shut:
        a fixed base URL, no proxy or other setting from the environment, no redirects."""
        with self._lock:
            if self._client is None:
                self._lookup()
                if self._key:
                    try:
                        import anthropic
                        import httpx2
                    except ImportError:                 # the SDK is not in this environment: as with no key
                        return None
                    for name in ENV_IGNORED:            # never a gateway, a profile or another credential
                        os.environ.pop(name, None)
                    netaudit.allow(HOST)
                    # no SDK retries: a late answer is worth nothing to a caption; the local model answers
                    # Our own transport: without one the SDK reads HTTPS_PROXY / ALL_PROXY itself (checked
                    # against 2.7.0: trust_env=False alone still went through the proxy).
                    self._client = anthropic.Anthropic(
                        api_key=self._key, base_url=BASE_URL, max_retries=0, timeout=float(self.cfg["timeout_s"]),
                        http_client=anthropic.DefaultHttpxClient(
                            transport=httpx2.HTTPTransport(trust_env=False), trust_env=False, follow_redirects=False))
            return self._client

    def has_key(self) -> bool:
        return self.key_present()

    def key_source(self) -> str | None:
        return self._key_source if self._key_checked or self._client is not None else None

    def usable(self) -> bool:
        return self.up and not self._closed and self.client() is not None

    # --- one request -----------------------------------------------------------------------------
    def call(self, system: str, messages: list[dict], max_tokens: int = 200, timeout: float | None = None,
             kind: str = "translate", soft: bool = False) -> tuple[str, str]:
        """(text, stop_reason) from one Messages API call with thinking off, within `timeout` seconds
        of wall clock in all: DNS, connecting, sending and every byte of the answer (the request runs on
        a worker thread and is waited for exactly that long; the HTTP client's own timeout only bounds
        each of its steps). Raises Unavailable; on a failure that may pass (timeout, network, overload,
        rate limit) the engine goes down and probes until the API answers again. `soft`: a failure of
        this call's timeout says nothing about captions (a summary with a short deadline), so the engine
        stays up; a network or API error still takes it down."""
        client = self.client()
        if client is None:
            raise Unavailable("no_key", retryable=False)
        key = None
        if self._cache_path is not None:
            key = hashlib.sha256(json.dumps([self.model, system, messages, max_tokens], ensure_ascii=False,
                                            sort_keys=True).encode()).hexdigest()
            hit = self._cached(key)
            if hit is not None:
                self.counts["cache_hits"] += 1
                return hit["text"], hit["stop"]
        import concurrent.futures
        import anthropic
        budget = max(0.05, float(timeout if timeout is not None else self.cfg["timeout_s"]))
        late = self._soft if soft else self._fail          # a soft call's timeout is its own short deadline
        fail = self._fail                                   # everything else is the API or the network
        t0 = time.perf_counter()
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="ai-call")
        fut = self._pool.submit(lambda: client.with_options(timeout=budget, max_retries=0).messages.create(
            model=self.model, max_tokens=max_tokens, system=system, messages=messages, thinking={"type": "disabled"}))
        try:
            r = fut.result(timeout=budget)
        except concurrent.futures.TimeoutError:
            fut.cancel()                                    # its answer, if it ever comes, is dropped
            late("timeout")
        except anthropic.APITimeoutError:
            late("timeout")
        except anthropic.APIConnectionError:
            fail("network")
        except anthropic.RateLimitError:
            fail("rate_limit")
        except anthropic.AuthenticationError:
            fail("key_rejected")
        except anthropic.PermissionDeniedError:
            fail("key_not_allowed")
        except anthropic.APIStatusError as e:
            fail("overloaded" if e.status_code >= 500 else f"http_{e.status_code}")
        except Exception as e:                              # noqa: BLE001  anything else is also "not now"
            fail(type(e).__name__)
        took = time.perf_counter() - t0
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
        stop = str(getattr(r, "stop_reason", "") or "")
        self.counts["calls"] += 1
        self.lat.append(took)
        self._usage(kind, took, r)
        if stop == "refusal":                               # declined: the local model translates this one
            self.counts["refusals"] += 1
            raise Unavailable("refusal", retryable=False)
        if key is not None:
            self._store(key, {"text": text, "stop": stop})
        return text, stop

    def _soft(self, reason: str):
        self.counts["soft_failures"] += 1
        raise Unavailable(reason)

    def _usage(self, kind: str, took: float, r):
        if self._usage_log is None:
            return
        try:
            u = r.usage
            with open(self._usage_log, "a") as f:
                f.write(json.dumps({"t": round(time.time(), 3), "kind": kind, "model": self.model,
                                    "s": round(took, 3), "in": u.input_tokens, "out": u.output_tokens,
                                    "stop": r.stop_reason, "request_id": getattr(r, "_request_id", None)}) + "\n")
        except Exception:                                   # noqa: BLE001
            pass

    def _fail(self, reason: str):
        with self._lock:
            self.counts["failures"] += 1
            self.counts["fail_" + reason] += 1
            if self.up:
                self.up, self.down_reason, self.down_since = False, reason, time.time()
                self.log({"event": "engine_down", "reason": reason})
                self._start_probe()
        raise Unavailable(reason)

    # --- coming back ---------------------------------------------------------------------------------
    def _start_probe(self):
        if self._probe is not None and self._probe.is_alive():
            return
        self._probe = threading.Thread(target=self._probe_loop, daemon=True, name="ai-probe")
        self._probe.start()

    def _probe_loop(self):
        import anthropic
        while not self._closed and not self.up:
            time.sleep(float(self.cfg["retry_s"]))
            client = self._client
            if self._closed or self.up or client is None:
                return
            try:
                client.with_options(timeout=float(self.cfg["timeout_s"]) * 2, max_retries=0).messages.create(
                    model=self.model, max_tokens=PROBE[1], messages=[{"role": "user", "content": PROBE[0]}],
                    thinking={"type": "disabled"})
            except anthropic.AuthenticationError:
                self.down_reason = "key_rejected"
                continue
            except Exception:                               # noqa: BLE001  still down
                continue
            with self._lock:
                down_s = round(time.time() - (self.down_since or time.time()), 1)
                self.up, self.down_reason, self.down_since = True, None, None
            self.log({"event": "engine_up", "down_s": down_s})

    def close(self):
        self._closed = True

    # --- bench cache (LIVE_AI_CACHE): the same input is never paid for twice ----------------------------
    def _cached(self, key: str) -> dict | None:
        if self._cache is None:
            self._cache = {}
            try:
                for line in open(self._cache_path):
                    rec = json.loads(line)
                    self._cache[rec["k"]] = rec
            except (OSError, ValueError):
                pass
        return self._cache.get(key)

    def _store(self, key: str, val: dict):
        self._cache[key] = {"k": key, **val}
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._cache_path, "a") as f:
                f.write(json.dumps({"k": key, **val}, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def latency(self) -> dict:
        xs = sorted(self.lat)
        if not xs:
            return {"n": 0, "median_s": None, "p90_s": None}
        return {"n": len(xs), "median_s": round(xs[len(xs) // 2], 2), "p90_s": round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 2)}


class ClaudeSummarizer:
    """summarize.py's `gen` for the AI engine: the same prompt and guards as Gemma's."""
    name = "ai"

    def __init__(self, engine: Engine, timeout: float | None = None):
        self.engine, self.timeout = engine, timeout

    def generate(self, texts: list[str], lang: str) -> str:
        import summarize
        msgs = summarize.messages(texts, lang)
        text, _ = self.engine.call(msgs[0]["content"], msgs[1:], max_tokens=summarize.MAX_TOKENS,
                                   timeout=self.timeout, kind="summary", soft=True)
        return text
