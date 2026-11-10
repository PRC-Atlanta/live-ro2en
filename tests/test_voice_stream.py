"""Live Voice M2: the MP3 ring, the listener HTTP server and the voice process's protocol
(docs/live_voice_plan.md §3, §4.2, §9). The process tests run src/voice/voice.py in .venv-voice with a
synth that needs no model (tests/fake_voice_synth.py)."""
import http.client
import json
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from voice import stream                                  # noqa: E402

VOICE_PY = ROOT / ".venv-voice" / "bin" / "python"
needs_voice_env = pytest.mark.skipif(not VOICE_PY.exists(), reason=".venv-voice is not set up (setup.sh)")


# --- the ring -------------------------------------------------------------------------------------
def test_the_ring_keeps_thirty_seconds_and_a_new_client_starts_at_the_live_edge():
    ch = stream.Channel("ro", 64)
    assert ch.byte_rate == 8000
    for _ in range(400):                                   # 40 s of audio in 0.1 s chunks
        ch.put(b"x" * 800, 4410)
    first = ch.chunks[0][0]
    assert ch.end == 320_000 and ch.end - first <= 30 * 8000 and ch.end - first > 29 * 8000
    pos = ch.start_pos(1.0)
    assert pos == ch.end - 8000                            # the live edge minus the preroll
    data, pos2, jumped = ch.read(pos)
    assert len(data) == 8000 and pos2 == ch.end and not jumped
    assert ch.read(pos2) == (b"", ch.end, False)           # nothing new yet


def test_a_slow_client_is_moved_to_the_live_edge():
    ch = stream.Channel("ro", 64)
    for _ in range(50):
        ch.put(b"x" * 800)
    pos = ch.end
    for _ in range(200):                                   # 20 s pass while the client reads nothing
        ch.put(b"y" * 800)
    data, new, jumped = ch.read(pos)
    assert jumped and new == ch.end and data == b""        # more than 10 s behind: jumped, nothing replayed
    for _ in range(50):                                    # 5 s behind is still served in full
        ch.put(b"z" * 800)
    data, new, jumped = ch.read(new)
    assert not jumped and len(data) == 40_000


def test_only_private_addresses_are_answered():
    for ok in ("127.0.0.1", "10.1.2.3", "192.168.1.20", "172.16.5.5", "169.254.10.1", "::1", "fe80::1%en0",
               "fd12:3456::1", "::ffff:192.168.1.9"):
        assert stream.private(ok), ok
    for bad in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "::ffff:8.8.8.8", "not an address", ""):
        assert not stream.private(bad), bad


# --- the server -------------------------------------------------------------------------------------
class Feed:
    """A server on a free port with fake MP3 bytes arriving in real time (8000 bytes a second)."""

    def __init__(self, **kw):
        self.channels = {"ro": stream.Channel("ro"), "en": stream.Channel("en")}
        self.logs = []
        self.server = stream.Server(self.channels, host="127.0.0.1", port=0, log=self.logs.append,
                                    page=b"<html>page</html>", **kw)
        assert self.server.start()
        self.port = self.server.port
        self.stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        t0, n = time.time(), 0
        while not self.stop.is_set():
            due = t0 + n * 0.05
            if time.time() < due:
                time.sleep(0.01)
                continue
            for lang, ch in self.channels.items():
                ch.put((b"R" if lang == "ro" else b"E") * 400)
                self.server.notify(lang)
            n += 1

    def close(self):
        self.stop.set()
        self.server.stop()

    def get(self, path, method="GET", body=None, headers=None, timeout=3):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        c.request(method, path, body=body, headers=headers or {})
        return c, c.getresponse()


@pytest.fixture
def feed():
    f = Feed()
    yield f
    f.close()


def test_the_page_and_the_two_streams(feed):
    c, r = feed.get("/")
    assert r.status == 200 and r.read() == b"<html>page</html>" and "text/html" in r.getheader("Content-Type")
    assert "default-src 'none'" in r.getheader("Content-Security-Policy") and r.getheader("Cache-Control") == "no-store"
    for path, byte in (("/listen", b"R"), ("/listen/ro", b"R"), ("/listen/en", b"E")):
        c, r = feed.get(path)
        assert r.status == 200 and r.getheader("Content-Type") == "audio/mpeg"
        assert r.getheader("Content-Length") is None and r.getheader("Cache-Control") == "no-store"
        t0, got = time.time(), b""
        while time.time() - t0 < 1.5:
            got += r.read(400)
        assert set(got) == set(byte) and 8000 <= len(got) <= 24000        # the preroll, then real time
        c.close()


def test_a_query_string_is_ignored_and_nothing_else_is_served(feed):
    c, r = feed.get("/listen/ro?t=123")
    assert r.status == 200
    c.close()
    for path in ("/x", "/listen/fr", "/listen/ro/../..", "/stats", "/../etc/passwd", "/ui/", "/api/state"):
        c, r = feed.get(path)
        assert r.status == 404, path
    c, r = feed.get("/listen", method="POST", body=b"{}")
    assert r.status == 405
    c, r = feed.get("/", method="DELETE")
    assert r.status == 405
    c, r = feed.get("/stats", method="POST", body=b"{}")              # only in rehearsal mode
    assert r.status == 404
    c, r = feed.get("/listen/en", method="HEAD")
    assert r.status == 200 and r.getheader("X-Voice-Rehearsal") is None


def test_bad_and_slow_requests_are_refused(feed):
    s = socket.create_connection(("127.0.0.1", feed.port), timeout=3)
    s.sendall(b"GET / HTTP/1.1\r\nX-Big: " + b"a" * 8000 + b"\r\n\r\n")   # a head over 4 KB
    assert s.recv(200).startswith(b"HTTP/1.1 400")
    s.close()
    s = socket.create_connection(("127.0.0.1", feed.port), timeout=3)
    s.sendall(b"NONSENSE\r\n\r\n")
    assert s.recv(200).startswith(b"HTTP/1.1 400")
    s.close()
    old, stream.HEAD_TIMEOUT_S = stream.HEAD_TIMEOUT_S, 0.3
    try:
        s = socket.create_connection(("127.0.0.1", feed.port), timeout=3)
        s.sendall(b"GET / HTTP/1.1\r\n")                                # never finishes its request
        assert s.recv(200).startswith(b"HTTP/1.1 400")
        s.close()
    finally:
        stream.HEAD_TIMEOUT_S = old


def test_listener_limits():
    f = Feed(max_clients=3)
    try:
        held = [f.get("/listen/ro") for _ in range(3)]
        assert all(r.status == 200 for _, r in held)
        time.sleep(0.2)
        assert f.channels["ro"].listeners == 3
        c, r = f.get("/listen/ro")
        assert r.status == 503 and r.getheader("Retry-After") == "5"
        for c, r in held:
            r.close()
            c.close()
        deadline = time.time() + 5                         # the places are freed when the clients go
        while f.channels["ro"].listeners and time.time() < deadline:
            time.sleep(0.1)
        assert f.channels["ro"].listeners == 0 and f.channels["ro"].peak == 3
        c, r = f.get("/listen/ro")
        assert r.status == 200
    finally:
        f.close()


def test_rehearsal_mode_takes_small_stats_only():
    f = Feed(rehearsal=True)
    try:
        c, r = f.get("/stats", method="POST", body=json.dumps({"lang": "ro", "buffer_s": 2.5, "stalls": 1, "evil": {"a": 1}}),
                     headers={"Content-Type": "application/json"})
        assert r.status == 204
        rec = next(x for x in f.logs if x["event"] == "client_stats")
        assert rec["buffer_s"] == 2.5 and rec["lang"] == "ro" and "evil" not in rec
        c, r = f.get("/stats", method="POST", body=b"x" * 5000)
        assert r.status == 413
        c, r = f.get("/stats", method="POST", body=b"not json")
        assert r.status == 400
        c, r = f.get("/listen", method="HEAD")
        assert r.getheader("X-Voice-Rehearsal") == "1"
    finally:
        f.close()


def test_a_hundred_listeners_stay_at_the_live_edge(feed):
    """100 clients on localhost: every one receives the stream at its rate (the 10-minute run with the
    CPU measurement is scripts/voice_soak.py, recorded in the plan)."""
    socks, got = [], []
    for i in range(100):
        s = socket.create_connection(("127.0.0.1", feed.port), timeout=5)
        s.sendall(b"GET /listen/" + (b"ro" if i % 2 else b"en") + b" HTTP/1.1\r\nHost: x\r\n\r\n")
        socks.append(s)
    time.sleep(3.0)
    for s in socks:
        s.setblocking(False)
        n = 0
        try:
            while True:
                b = s.recv(65536)
                if not b:
                    break
                n += len(b)
        except BlockingIOError:
            pass
        got.append(n)
        s.close()
    assert feed.channels["ro"].peak == 50 and feed.channels["en"].peak == 50
    assert min(got) > 8000 * 2.5 and max(got) < 8000 * 5.5, (min(got), max(got))


# --- the process -------------------------------------------------------------------------------------
class Proc:
    def __init__(self, tmp_path, cfg=None, env=None):
        self.log = tmp_path / "voice.jsonl"
        cfg = {"port": 0, "host": "127.0.0.1", **(cfg or {})}
        self.p = subprocess.Popen([str(VOICE_PY), str(ROOT / "tests" / "fake_voice_synth.py"), "--cfg", json.dumps(cfg),
                                   "--log", str(self.log)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, text=True, bufsize=1, env={**os.environ, **(env or {})})
        self.events = []
        threading.Thread(target=self._read, daemon=True).start()
        self.ready = self.wait("ready", 20)
        self.port = self.ready["port"]

    def _read(self):
        for line in self.p.stdout:
            try:
                self.events.append(json.loads(line))
            except ValueError:
                self.events.append({"type": "garbage", "line": line})

    def send(self, msg):
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def state(self, **kw):
        self.send({"type": "state", "paused": False, "song": False, "held": False, "running": True, "blank_n": 0,
                   "summarizer": False, **kw})

    def sentence(self, sid, text, lang="ro", behind=1.0, **kw):
        self.send({"type": "sentence", "sid": sid, "lang": lang, "text": text, "spk_end": time.time() - behind,
                   "scripture": False, **kw})

    def of(self, kind):
        return [e for e in self.events if e.get("type") == kind]

    def wait(self, kind, timeout=10, n=1):
        end = time.time() + timeout
        while time.time() < end:
            got = self.of(kind)
            if len(got) >= n:
                return got[n - 1]
            time.sleep(0.02)
        raise AssertionError(f"no {kind} #{n} within {timeout} s; got {[e.get('type') for e in self.events][-12:]}")

    def records(self, event=None):
        recs = [json.loads(l) for l in open(self.log)] if self.log.exists() else []
        return [r for r in recs if event is None or r.get("event") == event]

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        try:
            return self.p.wait(5)
        except subprocess.TimeoutExpired:
            self.p.kill()
            return None


@pytest.fixture
def proc(tmp_path):
    made = []

    def make(cfg=None, env=None):
        made.append(Proc(tmp_path, cfg, env))
        return made[-1]
    yield make
    for p in made:
        p.close()


@needs_voice_env
def test_ready_then_eof_on_stdin_ends_the_process(proc):
    v = proc()
    assert v.ready["langs"] == ["ro", "en"] and v.port > 0
    assert not v.of("garbage")                              # nothing but JSON lines on stdout
    assert v.close() == 0


@needs_voice_env
def test_stop_message_ends_it_and_the_english_stream_can_be_off(proc):
    v = proc({"en": {"enabled": False}})
    assert v.ready["langs"] == ["ro"]
    c = http.client.HTTPConnection("127.0.0.1", v.port, timeout=3)
    c.request("GET", "/listen/en")
    assert c.getresponse().status == 404
    v.send({"type": "stop"})
    assert v.p.wait(5) == 0


@needs_voice_env
def test_the_stream_keeps_its_byte_rate_through_silence_and_speech(proc):
    v = proc()
    c = http.client.HTTPConnection("127.0.0.1", v.port, timeout=5)
    c.request("GET", "/listen/ro")
    r = c.getresponse()
    v.state()
    counts, t0, got, last = [], time.time(), 0, time.time()
    said = False
    while time.time() - t0 < 6.0:
        got += len(r.read(500))
        if not said and time.time() - t0 > 2.0:            # 2 s of silence, then a sentence
            v.sentence(1, "una doua trei patru cinci sase sapte opt")
            said = True
        if time.time() - last >= 1.0:
            counts.append(got)
            got, last = 0, time.time()
    c.close()
    assert len(counts) >= 5 and all(6000 <= n <= 10500 for n in counts[1:]), counts    # 64 kbps = 8000 bytes/s
    item = v.wait("item")
    assert item["sids"] == [1] and item["kind"] == "sentence" and item["lang"] == "ro" and not item["cut"]
    assert 0 < item["delay_after_end_s"] < 3 and item["speed"] == 1.25 and item["audio_s"] > 1.5


@needs_voice_env
def test_sentences_play_in_order_each_language_in_its_own_stream(proc):
    v = proc()
    v.state()
    v.sentence(1, "unu doi trei patru")
    v.sentence(2, "one two three four", lang="en")
    v.sentence(3, "cinci sase sapte opt")
    v.wait("item", 10, n=3)
    items = v.of("item")
    ro = [i for i in items if i["lang"] == "ro"]
    assert [i["sids"] for i in ro] == [[1], [3]] and [i["sids"] for i in items if i["lang"] == "en"] == [[2]]
    assert ro[1]["play_start"] >= ro[0]["play_start"] + ro[0]["audio_s"] + 0.2      # gap_s between items
    en = next(i for i in items if i["lang"] == "en")
    assert en["play_start"] < ro[1]["play_start"]            # the English one did not wait for the Romanian queue
    st = v.wait("status", 5, n=len(v.of("status")) + 1)
    assert set(st["langs"]) == {"ro", "en"} and st["errors"] == 0 and st["rtf_recent"] is not None
    assert {"listeners", "queue_n", "queue_s", "behind_s", "playing_sid"} <= set(st) and st["queue_n"] == 0


@needs_voice_env
def test_hide_silences_within_the_fade_and_show_speaks_again(proc):
    v = proc()
    v.state()
    v.sentence(1, " ".join(["cuvant"] * 30))                 # about 7 s of audio
    v.sentence(2, "a doua propozitie care asteapta")
    time.sleep(1.5)
    t_hide = time.time()
    v.state(paused=True)
    item = v.wait("item", 3)
    assert item["sids"] == [1] and item["cut"] is True
    assert time.time() - t_hide < 1.0                        # faded out over 0.3 s, not played to its end
    v.sentence(3, "sosita in timpul ascunderii")             # arrives while hidden: dropped
    time.sleep(1.0)
    assert len(v.of("item")) == 1                            # the queued sentence was dropped too
    v.state(paused=False)
    v.sentence(4, "dupa afisare vorbeste iar")
    item = v.wait("item", 6, n=2)
    assert item["sids"] == [4] and not item["cut"]
    hush = v.records("hush")
    assert hush and 2 in hush[0]["dropped_sids"]


@needs_voice_env
def test_blank_is_one_shot_and_song_silences_too(proc):
    v = proc()
    v.state()
    v.sentence(1, " ".join(["cuvant"] * 30))
    time.sleep(1.2)
    v.state(blank_n=1)                                       # Blank: fade, drop, carry on
    assert v.wait("item", 3)["cut"] is True
    v.state(blank_n=1)                                       # the same state again every second: no new hush
    v.sentence(2, "merge mai departe dupa blank")
    assert v.wait("item", 6, n=2)["sids"] == [2]
    assert len(v.records("hush")) == 1
    v.sentence(3, " ".join(["cuvant"] * 30))
    time.sleep(1.2)
    v.state(blank_n=1, song=True)
    assert v.wait("item", 3, n=3)["cut"] is True


@needs_voice_env
def test_hold_and_stop_do_not_silence(proc):
    v = proc()
    v.state()
    v.sentence(1, " ".join(["cuvant"] * 12))
    time.sleep(0.8)
    v.state(held=True)
    v.state(running=False, held=True)                        # Stop: the queue is finished
    item = v.wait("item", 8)
    assert item["sids"] == [1] and not item["cut"]


@needs_voice_env
def test_retract_and_replace_before_during_and_after(proc):
    v = proc(env={"FAKE_GEN_S": "0.05"})
    v.state()
    v.sentence(1, " ".join(["lung"] * 24))                   # plays for ~6 s
    v.sentence(2, "aceasta va fi retrasa")
    v.sentence(3, "aceasta va fi inlocuita")
    time.sleep(2.5)                                          # 2 and 3 are generated and queued; 1 plays
    v.send({"type": "retract", "sid": 2})
    v.send({"type": "replace", "sid": 3, "text": "textul nou are cinci cuvinte aici"})
    v.send({"type": "replace", "sid": 1, "text": "prea tarziu"})          # playing: nothing
    v.wait("item", 12, n=2)
    time.sleep(0.5)
    items = v.of("item")
    assert [i["sids"] for i in items] == [[1], [3]]
    assert items[1]["words"] == 6                            # the new text was spoken
    where = {(r["event"], r["sid"]): r["where"] for r in v.records() if r.get("event") in ("retract", "replace")}
    assert where == {("retract", 2): "queued", ("replace", 3): "queued", ("replace", 1): "too_late"}
    v.send({"type": "retract", "sid": 1})                    # played: nothing, logged
    time.sleep(0.3)
    assert v.records("retract")[-1]["where"] == "too_late"


@needs_voice_env
def test_a_sentence_that_fails_does_not_stop_the_voice(proc):
    v = proc()
    v.state()
    v.sentence(1, "RAISE aici")
    v.sentence(2, "urmatoarea merge bine")
    assert v.wait("item", 6)["sids"] == [2]
    err = v.wait("error", 2)
    assert err["where"] == "generate" and "on purpose" in err["msg"]


@needs_voice_env
def test_test_voice_speaks_even_before_a_service(proc):
    v = proc()
    v.send({"type": "say", "lang": "en", "text": "This is a test."})
    item = v.wait("item", 6)
    assert item["kind"] == "test" and item["lang"] == "en" and item["sids"] == []


@needs_voice_env
def test_a_port_that_is_taken_is_reported(tmp_path, proc):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    p = subprocess.Popen([str(VOICE_PY), str(ROOT / "tests" / "fake_voice_synth.py"), "--cfg",
                          json.dumps({"port": port, "host": "127.0.0.1"})], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    out, _ = p.communicate(timeout=15)
    s.close()
    ev = [json.loads(l) for l in out.splitlines() if l.strip()]
    assert p.returncode == 4 and ev[-1]["type"] == "error" and ev[-1]["where"] == "listen"


@needs_voice_env
def test_the_real_encoder_makes_cbr_mp3():
    code = ("import sys; sys.path.insert(0, 'src'); import numpy as np; from voice import stream\n"
            "e = stream.Encoder(64); n = sum(len(e.encode(np.zeros(1152, np.float32))) for _ in range(383))\n"
            "print(n)")
    out = subprocess.run([str(VOICE_PY), "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=30)
    assert out.returncode == 0, out.stderr
    assert 76_000 <= int(out.stdout.strip()) <= 81_000        # 10 s of silence at 64 kbps = 80 000 bytes


@needs_voice_env
@pytest.mark.parametrize("how", ["paused", "song", "blank"])
def test_hide_song_and_blank_are_silent_within_the_fade(proc, how):
    """M3: from the operator's action to the last voiced sample is the 0.3 s fade (plus one 26 ms frame)."""
    v = proc()
    v.state()
    v.sentence(1, " ".join(["cuvant"] * 30))
    time.sleep(1.5)
    v.state(**({"blank_n": 1} if how == "blank" else {how: True}))
    v.wait("item", 3)
    hush = v.records("hush")[0]
    item = v.records("item")[0]
    assert item["cut"] and 0.0 <= item["t"] - hush["t"] <= voice_fade() + 0.12, item["t"] - hush["t"]
    assert item["played_s"] < item["audio_s"] - 2                # it did not play to its end
    # and the stream goes on: silence is still being encoded
    c = http.client.HTTPConnection("127.0.0.1", v.port, timeout=5)
    c.request("GET", "/listen/ro")
    r = c.getresponse()
    assert len(r.read(4000)) == 4000


def voice_fade():
    src = (ROOT / "src" / "voice" / "voice.py").read_text()
    import re
    return float(re.search(r"^FADE_S = ([\d.]+)", src, re.M).group(1))
