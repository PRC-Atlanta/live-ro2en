"""Text-based RO/EN language detection with hysteresis (switch only after `confirm`
consecutive clauses agree). Works on STT output, so it needs no extra model."""
from __future__ import annotations

import re

RO = set("""și să nu de la în că pe care cu este a o un una lui ce mai ne ei ea el noi voi
din pentru dar am ai au fi fost sunt va vor iar ca dacă când cum unde tot toate toți așa
foarte acum aici acolo isus dumnezeu domnul hristos frați fraților biserica""".split())
EN = set("""the and of to a in is that it for you he was on are with as his they be at one
have this from or had by not but what all were we when your can said there an which she do
their if will up other about out many then them these so some her would make like him into
time has look two more go see no way could my than been who its now people did get come
jesus god lord christ brothers sisters church amen""".split())
_TOK = re.compile(r"[a-zăâîșțşţ]+")


def score(text: str) -> float:
    """>0 means Romanian-like, <0 English-like, magnitude ~ confidence."""
    toks = _TOK.findall(text.lower())
    if not toks:
        return 0.0
    ro = sum(t in RO for t in toks) + 2 * sum(any(c in t for c in "ăâîșțşţ") for t in toks)
    en = sum(t in EN for t in toks)
    return (ro - en) / len(toks)


class LangTracker:
    def __init__(self, mode: str = "auto", initial: str = "ro", confirm: int = 2, margin: float = 0.15,
                 strong: float = 0.35):
        self.mode = mode          # auto | ro-en | en-ro
        self.current = initial
        self.confirm = confirm
        self.margin = margin
        self.strong = strong      # a clause this clearly in the other language switches at once
        self._streak = 0
        self.set_mode(mode)       # a lock given at start-up must set the language too

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "ro-en":
            self.current = "ro"
        elif mode == "en-ro":
            self.current = "en"
        self._streak = 0

    def update(self, text: str) -> str:
        if self.mode != "auto":
            return self.current
        s = score(text)
        guess = "ro" if s > self.margin else "en" if s < -self.margin else None
        if guess is None or guess == self.current:
            self._streak = 0
        else:
            self._streak += 1
            if self._streak >= self.confirm or abs(s) >= self.strong:
                self.current, self._streak = guess, 0
        return self.current
