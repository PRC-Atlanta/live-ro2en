"""Live Voice streams (docs/live_voice_plan.md §4.2, §14): one continuous MP3 per language, served to
phones on the church Wi-Fi.

  Channel   the ring of the last 30 s of MP3 bytes of one language
  Server    a small HTTP/1.1 server (asyncio, one thread): GET / (listener page), GET /listen,
            /listen/ro, /listen/en (audio/mpeg, close-delimited, no length), and POST /stats only in
            rehearsal mode. Nothing else: no control route, no files.

The server answers only private, link-local and loopback clients, reads at most MAX_HEAD bytes of
request within HEAD_TIMEOUT_S, serves at most `max_clients` listeners (and a few per address beyond
which a new one is refused), and never queues more than SLOW_S of audio for a slow client: such a
client is moved to the live edge."""
from __future__ import annotations

import asyncio
import collections
import ipaddress
import json
import pathlib
import threading
import time

FRAME = 1152                       # samples per MP3 frame at 44.1 kHz (26 ms)
SR = 44100
RING_S = 30.0
SLOW_S = 10.0                      # a client this far behind is moved to the live edge
MAX_HEAD = 4096
HEAD_TIMEOUT_S = 5.0
MAX_STATS_BODY = 2048
WRITE_HIGH = 64 * 1024             # the most we let the kernel + asyncio hold for one client
PAGE = pathlib.Path(__file__).resolve().parent / "page.html"
CSP = ("Content-Security-Policy: default-src 'none'; media-src 'self'; connect-src 'self'; "
       "style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\r\n"
       "Referrer-Policy: no-referrer\r\n")
ROUTES = {"/listen": "ro", "/listen/ro": "ro", "/listen/en": "en"}


def private(addr: str) -> bool:
    """Loopback, private (RFC 1918 / ULA) or link-local: someone on this network, never the internet."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local


class Channel:
    """The last RING_S of one language's MP3 bytes, in chunks numbered from 0. `pos` values are byte
    offsets since the stream began."""

    def __init__(self, lang: str, bitrate_kbps: int = 64):
        self.lang = lang
        self.byte_rate = bitrate_kbps * 1000 // 8
        self.chunks: collections.deque = collections.deque()     # (start byte, bytes)
        self.end = 0                                              # bytes produced so far (the live edge)
        self.samples = 0                                          # samples encoded so far
        self.lock = threading.Lock()
        self.listeners = 0
        self.peak = 0

    def put(self, data: bytes, samples: int = 0):
        with self.lock:
            self.samples += samples
            if not data:
                return
            self.chunks.append((self.end, data))
            self.end += len(data)
            keep = self.end - int(RING_S * self.byte_rate)
            while self.chunks and self.chunks[0][0] + len(self.chunks[0][1]) <= keep:
                self.chunks.popleft()

    def start_pos(self, preroll_s: float) -> int:
        """Where a new client starts: the live edge minus the preroll (never before the ring)."""
        with self.lock:
            first = self.chunks[0][0] if self.chunks else self.end
            return max(first, self.end - int(preroll_s * self.byte_rate))

    def read(self, pos: int, preroll_s: float = 0.0) -> tuple[bytes, int, bool]:
        """(bytes from `pos` to the live edge, the new pos, jumped). A `pos` that fell out of the ring or
        more than SLOW_S behind is moved to the live edge (minus the preroll)."""
        with self.lock:
            first = self.chunks[0][0] if self.chunks else self.end
            jumped = False
            if pos < first or self.end - pos > int(SLOW_S * self.byte_rate):
                pos, jumped = max(first, self.end - int(preroll_s * self.byte_rate)), True
            out = []
            for start, data in self.chunks:
                if start + len(data) <= pos:
                    continue
                out.append(data[pos - start:] if start < pos else data)
            return b"".join(out), self.end, jumped


class Server:
    def __init__(self, channels: dict[str, Channel], host: str = "0.0.0.0", port: int = 8767,
                 preroll_s: float = 1.0, max_clients: int = 300, per_addr: int = 40, rehearsal: bool = False,
                 log=None, page: bytes | None = None):
        self.channels, self.host, self.port = channels, host, int(port)
        self.preroll_s, self.max_clients, self.per_addr = float(preroll_s), int(max_clients), int(per_addr)
        self.rehearsal = bool(rehearsal)
        self.log = log or (lambda rec: None)
        self.page = page if page is not None else PAGE.read_bytes()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._server = None
        self._wake: dict[str, asyncio.Event] = {}
        self._by_addr: collections.Counter = collections.Counter()
        self._clients = 0
        self._ready = threading.Event()
        self.error: str | None = None

    # --- lifecycle --------------------------------------------------------------------------
    def start(self, timeout: float = 5.0) -> bool:
        self.thread = threading.Thread(target=self._run, daemon=True, name="voice-http")
        self.thread.start()
        self._ready.wait(timeout)
        return self.error is None and self._server is not None

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        try:
            self._wake = {lang: asyncio.Event() for lang in self.channels}
            self._server = self.loop.run_until_complete(
                asyncio.start_server(self._client, self.host, self.port, limit=MAX_HEAD, backlog=256))
            if self.port == 0:
                self.port = self._server.sockets[0].getsockname()[1]
        except OSError as e:
            self.error = f"{type(e).__name__}: {e}"
            self._ready.set()
            return
        self._ready.set()
        try:
            self.loop.run_forever()
        finally:
            self._server.close()
            for task in asyncio.all_tasks(self.loop):       # listeners still connected when we stop
                task.cancel()
            try:
                self.loop.run_until_complete(asyncio.sleep(0))
                self.loop.close()
            except Exception:                              # noqa: BLE001
                pass

    def stop(self):
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

    def notify(self, lang: str):
        """New audio in `lang`'s channel (called from the play clock thread)."""
        loop, ev = self.loop, self._wake.get(lang)
        if loop is not None and ev is not None and loop.is_running():
            loop.call_soon_threadsafe(ev.set)

    def _who(self, addr: str) -> dict:
        """A listener's address goes into the voice log only during a rehearsal (§4.2): on an ordinary
        Sunday the log counts listeners, it does not name their phones."""
        return {"addr": addr} if self.rehearsal else {}

    # --- one connection ------------------------------------------------------------------------
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername") or ("", 0)
        addr = str(peer[0])
        try:
            if not private(addr):
                return await self._reply(writer, 403, b"forbidden\n")
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
            except (asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
                return await self._reply(writer, 400, b"bad request\n")
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
                return await self._reply(writer, 400, b"bad request\n")
            method, target = parts[0], parts[1].split("?", 1)[0]
            headers = {k.strip().lower(): v.strip() for k, _, v in (l.partition(":") for l in lines[1:] if l)}
            if method in ("GET", "HEAD") and target == "/":
                return await self._reply(writer, 200, b"" if method == "HEAD" else self.page,
                                         "text/html; charset=utf-8", length=len(self.page))
            if method in ("GET", "HEAD") and target in ROUTES and ROUTES[target] in self.channels:
                if method == "HEAD":
                    return await self._reply(writer, 200, b"", "audio/mpeg", length=None,
                                             extra="X-Voice-Rehearsal: 1\r\n" if self.rehearsal else "")
                return await self._listen(reader, writer, addr, ROUTES[target])
            if method == "POST" and target == "/stats" and self.rehearsal:
                return await self._stats(reader, writer, addr, headers)
            if target == "/" or ROUTES.get(target) in self.channels or (target == "/stats" and self.rehearsal):
                return await self._reply(writer, 405, b"method not allowed\n")
            return await self._reply(writer, 404, b"not found\n")
        except (ConnectionError, asyncio.CancelledError, OSError):
            pass
        finally:
            try:
                writer.close()
            except Exception:                              # noqa: BLE001
                pass

    async def _reply(self, writer, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8",
                     length: int | None = -1, extra: str = ""):
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
                  405: "Method Not Allowed", 413: "Payload Too Large", 503: "Service Unavailable"}.get(code, "OK")
        n = len(body) if length == -1 else length
        head = (f"HTTP/1.1 {code} {reason}\r\nContent-Type: {ctype}\r\n"
                + (f"Content-Length: {n}\r\n" if n is not None else "")
                + "Cache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n"
                + (CSP if ctype.startswith("text/html") else "") + extra + "\r\n")
        writer.write(head.encode("latin-1") + body)
        try:
            await asyncio.wait_for(writer.drain(), 5.0)
        except (asyncio.TimeoutError, ConnectionError):
            pass

    async def _stats(self, reader, writer, addr: str, headers: dict):
        try:
            n = int(headers.get("content-length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_STATS_BODY:
            return await self._reply(writer, 413, b"too large\n")
        try:
            body = await asyncio.wait_for(reader.readexactly(n), HEAD_TIMEOUT_S)
            data = json.loads(body.decode("utf-8"))
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ValueError, UnicodeDecodeError):
            return await self._reply(writer, 400, b"bad request\n")
        if isinstance(data, dict):
            keep = {k: data[k] for k in ("lang", "buffer_s", "stalls", "pos", "t", "ua") if k in data
                    and isinstance(data[k], (int, float, str)) and len(str(data[k])) <= 200}
            self.log({"event": "client_stats", "addr": addr, **keep})
        return await self._reply(writer, 204, b"")

    async def _listen(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, addr: str, lang: str):
        ch = self.channels[lang]
        local = addr.startswith("127.") or addr == "::1"       # this Mac itself (tests, the bench's recorder)
        if self._clients >= self.max_clients or (not local and self._by_addr[addr] >= self.per_addr):
            self.log({"event": "client_refused", **self._who(addr), "lang": lang, "clients": self._clients})
            return await self._reply(writer, 503, b"busy\n", extra="Retry-After: 5\r\n")
        self._clients += 1
        self._by_addr[addr] += 1
        with ch.lock:
            ch.listeners += 1
            ch.peak = max(ch.peak, ch.listeners)
        t0, sent, stalls = time.time(), 0, 0
        pos = ch.start_pos(self.preroll_s)
        self.log({"event": "client_connect", **self._who(addr), "lang": lang, "listeners": ch.listeners})
        try:
            sock = writer.get_extra_info("socket")
            writer.transport.set_write_buffer_limits(high=WRITE_HIGH, low=WRITE_HIGH // 4)
            head = ("HTTP/1.1 200 OK\r\nContent-Type: audio/mpeg\r\nCache-Control: no-store\r\n"
                    "X-Content-Type-Options: nosniff\r\nConnection: close\r\n"
                    + "\r\n")
            writer.write(head.encode("latin-1"))
            ev = self._wake[lang]
            while True:
                data, pos, jumped = ch.read(pos, 0.0)
                if jumped:
                    stalls += 1
                if data:
                    writer.write(data)
                    sent += len(data)
                    try:                                   # a client that cannot take it within SLOW_S
                        await asyncio.wait_for(writer.drain(), SLOW_S)   # is gone, not just slow
                    except asyncio.TimeoutError:
                        break
                else:
                    ev.clear()
                    try:
                        await asyncio.wait_for(ev.wait(), 1.0)
                    except asyncio.TimeoutError:
                        pass
                if writer.is_closing() or reader.at_eof() or (sock is not None and sock.fileno() < 0):
                    break                                  # the phone went away
        except (ConnectionError, OSError):
            pass
        finally:
            self._clients -= 1
            self._by_addr[addr] -= 1
            if self._by_addr[addr] <= 0:
                del self._by_addr[addr]
            with ch.lock:
                ch.listeners -= 1
            self.log({"event": "client_disconnect", **self._who(addr), "lang": lang, "s": round(time.time() - t0, 1),
                      "bytes": sent, "stalls": stalls, "listeners": ch.listeners})


class Encoder:
    """LAME, CBR mono 44.1 kHz. float32 in, MP3 bytes out (LAME keeps a little back between calls)."""

    def __init__(self, bitrate_kbps: int = 64):
        import lameenc
        e = lameenc.Encoder()
        e.set_bit_rate(int(bitrate_kbps))
        e.set_in_sample_rate(SR)
        e.set_channels(1)
        e.set_quality(5)
        try:
            e.silence()
        except Exception:                                  # noqa: BLE001
            pass
        self.enc = e

    def encode(self, pcm) -> bytes:
        import numpy as np
        x = np.clip(np.asarray(pcm, dtype=np.float32), -1.0, 1.0)
        return bytes(self.enc.encode((x * 32767.0).astype("<i2").tobytes()))
