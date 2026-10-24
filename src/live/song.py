"""Songs: don't caption garbled lyrics (translation idea 18).

Sung lyrics are recognised badly and the translator turns them into confident nonsense
("îmbete" -> "drunk", "spre rai străneie" -> "to a strange heaven"). The operator's Song key
holds the captions (output.py); this module is the optional automatic detection
(`live.song_detect`, off by default), from what every phrase already carries:

  loud     the input level (phrase level minus the gain in use, so the gain knob and the
           automatic gain do not move it) well above the recent speech: singers and the band
           on the pulpit microphone (2026-10-24: +8..+15 dB over the speakers around them)
  slow     few words per second: sung syllables are long (songs 0.3-2.0 words/s, speech 2-4)
  steady   a low crest (peak over the speech level): held notes and the band, not the bursts and
           gaps of speech (songs ~4.5 dB, speech ~16, a loud close-mic preacher ~12)
  repeat   a line said already a little earlier: a chorus ("va fi o dimineață")

A phrase is song-like when it is steady and slow and either loud or a repeated line.
Song mode starts when `need` of the last `window` phrases were song-like and ends at the first
phrase that is clearly speech (quiet or quick) or two that are not song-like. Very short
phrases (< min_s) are no evidence either way."""
from __future__ import annotations

import collections
import re
import statistics

from textnorm import fold_diacritics


def _line(text: str) -> str:
    return " ".join(fold_diacritics(w) for w in re.findall(r"[^\W\d_]+", text.lower()))


class SongDetector:
    def __init__(self, loud_db: float = 6.0, max_wps: float = 1.8, max_crest_db: float = 8.0, window: int = 4, need: int = 3,
                 min_s: float = 1.5, repeat_s: float = 240.0, base_n: int = 60, base_min: int = 8):
        self.loud_db, self.max_wps, self.window, self.need = loud_db, max_wps, window, need
        self.max_crest_db = max_crest_db
        self.min_s, self.repeat_s, self.base_min = min_s, repeat_s, base_min
        self.base: collections.deque = collections.deque(maxlen=base_n)   # input levels of speech
        self.recent: collections.deque = collections.deque(maxlen=window)  # song-like? (evidence only)
        self.lines: collections.deque = collections.deque(maxlen=80)      # (t, line)
        self.on = False
        self._off_run = 0

    def reset(self):
        self.recent.clear()
        self.on, self._off_run = False, 0

    def update(self, text: str, t_start: float, t_end: float, level: dict | None,
               gain_db: float | None) -> tuple[bool, dict]:
        """Feed one phrase; returns (song mode now, the features for the log)."""
        dur = max(0.0, t_end - t_start)
        words = len(re.findall(r"[^\W_]+", text or ""))
        speech_db, peak_db = (level or {}).get("speech_db"), (level or {}).get("peak_db")
        inp = None if speech_db is None else speech_db - float(gain_db or 0)
        line = _line(text or "")
        repeat = len(line.split()) >= 3 and any(l == line and t_start - t <= self.repeat_s
                                               for t, l in self.lines)
        if line:
            self.lines.append((t_start, line))
        base = statistics.median(self.base) if len(self.base) >= self.base_min else None
        f = {"wps": round(words / dur, 2) if dur else None,
             "over_db": None if inp is None or base is None else round(inp - base, 1),
             "crest_db": None if inp is None or peak_db is None else round(peak_db - speech_db, 1), "repeat": repeat}
        if dur < self.min_s or inp is None:
            f["like"] = None
            return self.on, f
        loud = f["over_db"] is not None and f["over_db"] >= self.loud_db
        slow = f["wps"] <= self.max_wps
        steady = f["crest_db"] is not None and f["crest_db"] <= self.max_crest_db
        like = steady and slow and (loud or repeat)
        f["like"] = like
        self.recent.append(like)
        if not like and not self.on:
            self.base.append(inp)                      # the speech level, from speech only
        if self.on:
            clear_speech = (f["over_db"] is not None and f["over_db"] < self.loud_db / 2) or f["wps"] > 2 * self.max_wps \
                or (f["crest_db"] is not None and f["crest_db"] > self.max_crest_db + 4)
            self._off_run = 0 if like else self._off_run + 1
            if clear_speech or self._off_run >= 2:
                self.on, self._off_run = False, 0
                self.recent.clear()
        elif sum(bool(x) for x in self.recent) >= self.need:
            self.on, self._off_run = True, 0
        return self.on, f
