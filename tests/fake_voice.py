"""A stand-in for src/voice/voice.py (standard library only), for the orchestrator's side of the
protocol: says `ready`, sends `status` every 0.1 s, and writes every message it gets to FAKE_VOICE_OUT.
  FAKE_VOICE_DIE=<s>     exit(7) after that many seconds
  FAKE_VOICE_HANG=<s>    stop sending status after that many seconds (but keep running)
  FAKE_VOICE_NOT_READY=1 never say ready
A `sentence` whose text holds "SUMMARIZE" is answered with a `summarize` request for it; a `say` and
every other sentence is "played" at once (an `item` event)."""
import json
import os
import sys
import threading
import time

out = open(os.environ["FAKE_VOICE_OUT"], "a") if os.environ.get("FAKE_VOICE_OUT") else None
t0 = time.time()
lock = threading.Lock()


def emit(o):
    with lock:
        sys.stdout.write(json.dumps(o) + "\n")
        sys.stdout.flush()


def status():
    hang = float(os.environ.get("FAKE_VOICE_HANG") or 0)
    die = float(os.environ.get("FAKE_VOICE_DIE") or 0)
    while True:
        time.sleep(0.1)
        if die and time.time() - t0 > die:
            os._exit(7)
        if hang and time.time() - t0 > hang:
            continue
        emit({"type": "status", "listeners": 3, "queue_n": 0, "queue_s": 0, "behind_s": float(os.environ.get("FAKE_VOICE_BEHIND") or 0) or None,
              "playing_sid": None, "rtf_recent": 0.2, "errors": 0, "counts": {},
              "langs": {"ro": {"listeners": 2, "peak": 2, "queue_n": 0, "queue_s": 0, "behind_s": None, "playing_sid": None, "speed": 1.25},
                        "en": {"listeners": 1, "peak": 1, "queue_n": 0, "queue_s": 0, "behind_s": None, "playing_sid": None, "speed": 1.25}}})


if out:
    out.write(json.dumps({"type": "argv", "argv": sys.argv[1:]}) + "\n")
    out.flush()
if not os.environ.get("FAKE_VOICE_NOT_READY"):
    emit({"type": "ready", "port": 18767, "langs": ["ro", "en"]})
threading.Thread(target=status, daemon=True).start()
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if out:
        out.write(json.dumps(msg, ensure_ascii=False) + "\n")
        out.flush()
    if msg.get("type") == "stop":
        break
    if msg.get("type") == "sentence":
        if "SUMMARIZE" in msg.get("text", ""):
            emit({"type": "summarize", "req": msg["sid"], "lang": msg["lang"], "sids": [msg["sid"]],
                  "texts": [msg["text"].replace("SUMMARIZE ", "")]})
        else:
            emit({"type": "item", "lang": msg["lang"], "kind": "sentence", "sids": [msg["sid"]], "gen_s": 0.5,
                  "audio_s": 3.0, "play_start": time.time(), "delay_after_end_s": float(msg.get("delay", 4.0)),
                  "cut": False, "speed": 1.25, "summarize_s": 0.0, "words": 5, "pause_before": 0.25, "scripture": False})
    if msg.get("type") == "say":
        emit({"type": "item", "lang": msg["lang"], "kind": "test", "sids": [], "gen_s": 0.1, "audio_s": 1.0,
              "play_start": time.time(), "delay_after_end_s": None, "cut": False, "speed": 1.25, "summarize_s": 0.0,
              "words": 3, "pause_before": 0.25, "scripture": False})
