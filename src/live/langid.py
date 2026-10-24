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


def looks_romanian(ro_text: str) -> bool:
    """The Romanian recogniser's own transcript reads as Romanian: Romanian-leaning words and
    no sign of garbling (gate). Romanian speech the multilingual model wrote as English still
    passes this; English speech makes the Romanian model write scraps like "my.s Ts"."""
    if not (ro_text or "").strip() or score(ro_text) <= 0.15:
        return False
    try:                                              # lazy: the word lists load once, on first use
        import gate
    except ImportError:                               # imported as live.langid
        from live import gate
    return gate.check(ro_text, "ro", gate.lexicon())[0]


class LangTracker:
    """`initial` is the home language (the church's usual one). Leaving it takes more than
    coming back: a garbled home-language phrase that the multilingual model writes as the
    other language must not flip the captions. Away: `confirm_away` clauses in a row, or one
    clause of at least `strong_words` words that is clearly (`strong_away`) the other language;
    when the Romanian recogniser's transcript of the same audio (`ro`) is given and still reads
    as Romanian, the clause does not vote to leave (idea 12). An empty `ro` is a vote to leave.
    Back home: `confirm` clauses, or one clause of `strong` with at least `home_words` words
    ("pasă de." alone must not end an English reading); clauses under `min_vote_words` words
    neither vote for home nor break an away streak."""

    def __init__(self, mode: str = "auto", initial: str = "ro", confirm: int = 2, margin: float = 0.15,
                 strong: float = 0.35, confirm_away: int = 3, strong_away: float = 0.5, strong_words: int = 10,
                 home_words: int = 4, min_vote_words: int = 3):
        self.mode = mode          # auto | ro-en | en-ro
        self.current = initial
        self.home = initial
        self.confirm = confirm
        self.margin = margin
        self.strong = strong      # a clause this clearly in the other language switches at once
        self.confirm_away = confirm_away
        self.strong_away = strong_away
        self.strong_words = strong_words
        self.home_words = home_words
        self.min_vote_words = min_vote_words
        self._streak = 0
        self.set_mode(mode)       # a lock given at start-up must set the language too

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        if mode == "ro-en":
            self.current = "ro"
        elif mode == "en-ro":
            self.current = "en"
        self._streak = 0

    def update(self, text: str, ro: str | None = None) -> str:
        """`text`: the multilingual transcript (the probe); `ro`: the Romanian recogniser's
        transcript of the same audio, when there is one."""
        if self.mode != "auto":
            return self.current
        s = score(text)
        guess = "ro" if s > self.margin else "en" if s < -self.margin else None
        n_words = len(_TOK.findall(text.lower()))
        if guess == self.home != self.current and n_words < self.min_vote_words:
            return self.current                       # "cu ce, și." mid-English: no vote either way
        if guess and guess != self.home and self.home == "ro" and ro is not None and looks_romanian(ro):
            guess = None                              # garbled Romanian, not English (11:42 prayer)
        if guess is None or guess == self.current:
            self._streak = 0
            return self.current
        self._streak += 1
        if guess == self.home:
            switch = self._streak >= self.confirm or (abs(s) >= self.strong and n_words >= self.home_words)
        else:
            switch = self._streak >= self.confirm_away or (
                abs(s) >= self.strong_away and n_words >= self.strong_words)
        if switch:
            self.current, self._streak = guess, 0
        return self.current
