"""Bible verses recognised by their words: when a piece is part of a known verse, caption the
matching part of the published text (World English Bible for RO→EN, Cornilescu for EN→RO)
instead of the translator's guess. Psalm 71:3 "Tu ai hotărât să mă scapi" came out as "You
have decided to cast me off", Philippians 1:21 "a muri este un câștig" as "She died. | it is
a gain", Psalm 103:14 "suntem țărână" as "we are a nation".

The recent pieces (a window of WINDOW words, the current piece last) are aligned word by word
(fuzzy: "scap" ~ "scapi"; diacritics, â/î and ea/ia folded) against the verses:
  - of a reference announced a few pieces back (scripture.Reading: "Psalmul 71, versetul 3")
    or of the passage matched just before (a reading goes on for several pieces): a few
    matching words are enough (NEAR_*);
  - of the whole Bible otherwise, found through an index of word pairs: only long, clean and
    rare matches (GLOBAL_*), so "în numele lui Isus Hristos" or "slava lui Dumnezeu" (in
    hundreds of verses) never count.
Only the words of the current piece that matched are captioned, located in the target verse
through a word lexicon learned from the two texts (data/bible/lexicon.tsv.gz) and snapped to
punctuation or a conjunction: the published text does not run ahead of the speaker, and what
was captioned already is not repeated. Three or more words of the piece around the verse are
translated as usual (Match.before / after), and so are 1-2 words unless they are the verse's
own words split differently ("prea iubiților" = "Preaiubiților"). Unsure means no match: the translator then works
as before, with the reference hint of scripture.py.

The texts are data/bible/verses.tsv.gz (scripts/fetch_bible.py; licences in data/bible/
README.md). The index takes a few seconds to build, in a background thread started with the
matcher; until it is ready nothing matches."""
from __future__ import annotations

import array
import bisect
import collections
import csv
import dataclasses
import functools
import gzip
import math
import pathlib
import re
import sys
import threading

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from textnorm import fix_cedilla, fold_diacritics, phon_key   # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import scripture   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "bible" / "verses.tsv.gz"

WINDOW = 28             # words of the previous pieces aligned together with the current one
GLOBAL_WORDS = 6        # with no reference: matching words in the window ...
GLOBAL_OWN = 3          # ... of them in the current piece
GLOBAL_QUALITY = 0.75   # ... matching words / aligned span
SURE_WORDS = 10         # ... a quote this long may be in several verses (parallels)
GLOBAL_IDF = 15.0       # ... rarity: summed log(verses / verses with the word) of the content words
NEAR_WORDS = 3          # with an announced reference or right after a matched verse
NEAR_OWN = 2
NEAR_QUALITY = 0.6
NEAR_IDF = 4.0          # summed rarity of the current piece's matching content words
KEEP_LOCK = 4           # pieces a matched passage stays "near" after its last match
NEAR_VERSES = 4         # verses after the reference (or the last match) searched as "near"
MIN_SIM = 0.7           # word similarity that counts as a match
MIN_DICE = 0.15         # lexicon strength that anchors a source word to a target word
CANDIDATES = 6
WIDEN = 2               # target words a caption may start earlier to take in a reordered word
VOCAB_MAX = 1 << 24        # word-pair code = id * VOCAB_MAX + next id

_STOP = {
    "ro": set("a ai al ale am ar are as au ca cand care ce cel cea cei cele cu da dar de din dupa "
              "e ea ei el eu fi fie fost i iar il in insa la le li lor lui m ma mai mi ne nici nu o "
              "pe pentru prin s sa sau se si sunt suntem te ti tu un una unei unui va vom vor voi "
              "este noi iti isi ii".split()),
    "en": set("a an and are as at be been but by do does for from had has have he her him his i if "
              "in is it its me my no not of on or our shall she so that the their them they this "
              "to us was we were what when which who will with you your all".split()),
}
# where a caption piece may start or end in the target text: before these words
_CONJ = {"ro": {"si", "dar", "ci", "caci", "iar", "ca", "sa", "pentru", "care", "cand"},
         "en": {"and", "but", "for", "that", "who", "when", "because", "so", "to"}}
# words that lead into what follows: a caption does not end on them ("… our secret sins in")
_LEAD = {"ro": set("si de la in pe cu din ca sa ca care dar ci iar pentru prin un o al ale ai cel cea "
                   "cei cele spre dupa catre".split()),
         "en": set("the a an of in on to and or if for by with from at as that which who but so into "
                   "upon than".split())}
# archaic KJV forms, matched as the modern words a preacher says
_KJV = {"thee": "you", "thou": "you", "ye": "you", "thy": "your", "thine": "your", "hath": "has",
        "hast": "have", "doth": "does", "dost": "do", "art": "are", "shalt": "shall", "wilt": "will",
        "unto": "to", "saith": "says", "spake": "spoke", "yea": "yes"}
_TOK = {"ro": re.compile(r"[0-9a-zăâîșț]+"), "en": re.compile(r"[0-9a-z]+")}
_BREAK = re.compile(r"[,;:.!?…—–]")
_END = re.compile(r"[;:.!?…][\"”’»]*$")             # a word that ends a sentence


@functools.lru_cache(maxsize=200_000)
def key(word: str, lang: str) -> str:
    """The matching key of one lower-case word."""
    if lang == "ro":
        return fold_diacritics(phon_key(word))
    return _KJV.get(word, word)


def content(k: str, lang: str) -> bool:
    return len(k) >= 3 and k not in _STOP[lang]


def _lower(text: str, lang: str) -> str:
    t = fix_cedilla(text).lower().replace("’", "'")
    return re.sub(r"'(s|t|ll|re|ve|d|m)\b", r"\1", t) if lang == "en" else t   # don't → dont


def words(text: str, lang: str) -> list[tuple[str, int, int]]:
    """(key, start, end) for each word of `text`: positions in `text` itself, also after
    "don't" → "dont" made the lowered text shorter (the caption cuts `text` at them)."""
    low = _lower(text, lang)
    if len(low) == len(text):
        return [(key(m.group(0), lang), m.start(), m.end()) for m in _TOK[lang].finditer(low)]
    at, j = [], 0                                       # at[i]: where low[i] came from in text
    plain = fix_cedilla(text).lower().replace("’", "'")
    for i, ch in enumerate(low):
        while j < len(plain) and plain[j] != ch:
            j += 1                                      # an apostrophe the rewrite dropped
        at.append(j)
        j += 1
    at.append(len(text))
    return [(key(m.group(0), lang), at[m.start()], at[m.end() - 1] + 1) for m in _TOK[lang].finditer(low)]


@functools.lru_cache(maxsize=500_000)
def sim(a: str, b: str) -> float:
    """Similarity of two keys: 1 equal, ~0.8 for an inflection or a recognition slip."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    short, long_ = min(la, lb), max(la, lb)
    if short < 3 or a[0] != b[0]:
        return 0.0
    if (a.startswith(b) or b.startswith(a)) and short >= max(3, long_ // 2):
        return 0.8                                         # scap ~ scapi, believ ~ believeth
    if long_ - short > 3:
        return 0.0
    prev = list(range(lb + 1))                             # Levenshtein distance
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    r = 1 - prev[lb] / long_
    return round(0.9 * r, 3) if r >= 0.75 else 0.0


class Keys:
    """A stream's words, kept as vocabulary ids (a few bytes a word); reads as strings."""

    def __init__(self, ids: np.ndarray, names: list[str]):
        self.ids, self.names = ids, names

    def __len__(self):
        return len(self.ids)

    def __getitem__(self, i):
        if isinstance(i, slice):
            return [self.names[x] for x in self.ids[i].tolist()]
        return self.names[self.ids[i]]


@dataclasses.dataclass
class Stream:
    """One text of one language as a flat word sequence."""
    lang: str
    keys: Keys
    vocab: dict                  # key -> id (shared by the streams of a language)
    verse: np.ndarray            # verse index of each word
    first: np.ndarray            # first word of each verse (len = verses + 1)
    pairs: np.ndarray            # sorted word-pair codes (id * vocabulary + next id) ...
    where: np.ndarray            # ... and the word positions they start at
    idf: dict                    # key -> log(verses / verses containing it)


def _stream(lang: str, texts: list[str], vocab: dict, names: list) -> Stream:
    ids, first, df = array.array("i"), [], collections.Counter()
    tok = _TOK[lang].findall
    for t in texts:
        first.append(len(ids))
        ks = []
        for w in tok(_lower(t, lang)):
            k = key(w, lang)
            if k not in vocab:
                vocab[k] = len(names)
                names.append(k)
            ks.append(vocab[k])
        ids.extend(ks)
        df.update(set(ks))
    first.append(len(ids))
    first_a = np.array(first, dtype=np.int64)
    ids_a = np.frombuffer(ids, dtype=np.int32).copy()
    verse = np.repeat(np.arange(len(texts), dtype=np.int32), np.diff(first_a))
    stop = np.zeros(len(names) + 1, dtype=bool)
    stop[[vocab[k] for k in _STOP[lang] if k in vocab]] = True
    a, b = ids_a[:-1], ids_a[1:]
    where = np.nonzero(~(stop[a] & stop[b]))[0].astype(np.int32)
    code = a[where].astype(np.int64) * VOCAB_MAX + b[where]
    order = np.argsort(code, kind="stable")
    n = max(len(texts), 1)
    return Stream(lang, Keys(ids_a, names), vocab, verse, first_a, code[order], where[order],
                  {names[i]: math.log(n / c) for i, c in df.items()})


class Bible:
    """The verse texts and their word indexes: "ro" (Cornilescu), "en" (WEB, and the KJV for
    recognising English quotes)."""

    def __init__(self, path: pathlib.Path | str = DATA):
        path = pathlib.Path(path)
        self.refs, ro, web, kjv = [], [], [], []
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                self.refs.append((int(r["book"]), int(r["chapter"]), int(r["verse"])))
                ro.append(r["ro"])
                web.append(r["web"])
                kjv.append(r["kjv"])
        self.text = {"ro": ro, "en": web}                  # what is shown
        vocab = {"ro": ({}, []), "en": ({}, [])}
        self.streams = {"ro": [_stream("ro", ro, *vocab["ro"])],
                        "en": [_stream("en", web, *vocab["en"]), _stream("en", kjv, *vocab["en"])]}
        self.index = {r: i for i, r in enumerate(self.refs)}
        self.lexicon: dict[tuple[str, str], float] = {}    # (ro key, en key) -> Dice
        lex = path.with_name("lexicon.tsv.gz")
        if lex.is_file():
            with gzip.open(lex, "rt", encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
                    self.lexicon[(r["ro"], r["en"])] = float(r["dice"])

    def name(self, a: int, b: int) -> str:
        """'Psalms 103:14' or 'Psalms 103:14-15' for verses a..b (same chapter)."""
        bk, ch, v = self.refs[a]
        book = scripture.BOOKS[bk - 1].en if len(scripture.BOOKS) == 66 else str(bk)
        return f"{book} {ch}:{v}" + (f"-{self.refs[b][2]}" if b > a else "")

    def chapter(self, book: int, ch: int) -> tuple[int, int] | None:
        a = self.index.get((book, ch, 1))
        if a is None:
            return None
        b = a
        while b + 1 < len(self.refs) and self.refs[b + 1][:2] == (book, ch):
            b += 1
        return a, b


@dataclasses.dataclass
class Match:
    ref: str
    score: float                 # matching words / aligned span (window)
    words: int                   # matching words in the window
    caption: str                 # the published text of the words just said
    before: str = ""             # the piece's words before / after the verse, to translate
    after: str = ""
    near: bool = False           # found through an announced reference or the previous match

    def log(self) -> dict:
        return {"ref": self.ref, "score": self.score, "words": self.words, "near": self.near}


def align(w: list[str], r: list[str]) -> list[tuple[int, int, float]]:
    """Local alignment of the window keys `w` with the verse keys `r`: [(i, j, similarity)]."""
    n, m = len(w), len(r)
    if not n or not m:
        return []
    H = np.zeros((n + 1, m + 1))
    for i in range(1, n + 1):
        wi, row, up = w[i - 1], H[i], H[i - 1]
        for j in range(1, m + 1):
            s = sim(wi, r[j - 1])
            row[j] = max(0.0, up[j - 1] + (2 * s if s >= MIN_SIM else -1.0), up[j] - 0.8, row[j - 1] - 0.8)
    i, j = np.unravel_index(int(np.argmax(H)), H.shape)
    pairs = []
    while i > 0 and j > 0 and H[i, j] > 0:
        s = sim(w[i - 1], r[j - 1])
        if abs(H[i, j] - (H[i - 1, j - 1] + (2 * s if s >= MIN_SIM else -1.0))) < 1e-9:
            if s >= MIN_SIM:
                pairs.append((i - 1, j - 1, s))
            i, j = i - 1, j - 1
        elif abs(H[i, j] - (H[i - 1, j] - 0.8)) < 1e-9:
            i -= 1
        else:
            j -= 1
    return pairs[::-1]


def _target(text: str, lang: str) -> tuple[list[str], list[set], list[int]]:
    """A target verse: its words as printed, their keys, and the positions a caption may start at."""
    ws = text.split()
    ks = [{k for k, *_ in words(x, lang)} for x in ws]
    conj = _CONJ[lang]
    breaks = [0] + [i for i in range(1, len(ws)) if _BREAK.search(ws[i - 1][-1:]) or ks[i] & conj] + [len(ws)]
    return ws, ks, breaks


def links(src: list[str], tgt: list[set], src_lang: str, lexicon: dict) -> list[tuple[int, int, float]]:
    """(source index, target index, Dice) for each source word's best partners (two at most)."""
    tl = "en" if src_lang == "ro" else "ro"
    cands = []
    for i, s in enumerate(src):
        if not content(s, src_lang):
            continue
        best = []
        for j, ks in enumerate(tgt):
            d = max((lexicon.get((s, t) if src_lang == "ro" else (t, s), 0.0) for t in ks if content(t, tl)),
                    default=0.0)
            if d >= MIN_DICE:
                best.append((d, j))
        cands += [(i, j, d) for d, j in sorted(best, reverse=True)[:2]]
    return cands


def anchors(cands: list[tuple[int, int, float]]) -> list[tuple[int, int]]:
    """The links in the same order in both texts (the heaviest such chain): (source, target)."""
    if not cands:
        return []
    score, back = [], []                                   # heaviest chain increasing in i and j
    for k, (i, j, d) in enumerate(cands):
        prev = [(score[q], q) for q in range(k) if cands[q][0] < i and cands[q][1] < j]
        s, q = max(prev, default=(0.0, -1))
        score.append(s + d)
        back.append(q)
    k, chain = int(np.argmax(score)), []
    while k >= 0:
        chain.append(cands[k][:2])
        k = back[k]
    return sorted(chain)


def project(p: int, n: int, m: int, breaks: list[int], words_: list[tuple[int, int]]) -> int:
    """The target word boundary for source word boundary p (0..n) of the same verse, between the
    boundaries of the corresponding words `words_`."""
    marks = sorted({b for i, j in words_ for b in ((i, j), (i + 1, j + 1))})
    if p <= 0 or n <= 0:
        return 0
    if p >= n:
        return m
    pts = [(0, 0)] + [(a, b) for a, b in marks if 0 < a < n and 0 < b < m] + [(n, m)]
    k = bisect.bisect_right([a for a, _ in pts], p) - 1
    (s0, t0), (s1, t1) = pts[k], pts[min(k + 1, len(pts) - 1)]
    t = t0 + (p - s0) * (t1 - t0) / max(s1 - s0, 1)
    if (p, round(t)) in marks:
        return round(t)
    near = min(breaks, key=lambda b: abs(b - t))
    return near if abs(near - t) <= 1.5 else int(round(t))


def display(text: str, lang: str) -> str:
    """The published text as captioned: the WEB's small-caps LORD/GOD written as the app's other
    captions write them."""
    if lang == "en":
        text = re.sub(r"\bLORD\b", "Lord", re.sub(r"\bGOD\b", "God", text))
    return text.strip()


_BIBLES: dict[pathlib.Path, Bible] = {}
_LOADING = threading.Lock()


class Matcher:
    """Finds the verse the speaker is reading; one per translator (it keeps the recent pieces)."""

    def __init__(self, path: pathlib.Path | str = DATA, background: bool = True):
        self.path, self.bible = pathlib.Path(path), None
        self.reset()
        if not self.path.is_file():
            return
        if background:
            threading.Thread(target=self._load, name="verses", daemon=True).start()
        else:
            self._load()

    def _load(self):
        try:
            with _LOADING:                                  # one copy for all translators (~90 MB)
                if self.path not in _BIBLES:
                    _BIBLES[self.path] = Bible(self.path)
            self.bible = _BIBLES[self.path]
        except Exception as e:                              # no verses: the translator as before
            print(f"verses: not loaded ({type(e).__name__}: {e})", file=sys.stderr)

    def reset(self):
        self.history: collections.deque = collections.deque()    # keys of the recent pieces
        self.lang = None
        self.lock = None          # (first verse, last verse) of the passage just matched
        self.lock_left = 0
        self.done = None          # (verse, target word) up to which the published text was shown

    def _near(self, ref) -> list[tuple[int, int]]:
        """Verse ranges (first, last) of the announced reference and of the passage just matched."""
        b, out = self.bible, []
        if ref is not None and ref.book is not None:
            bk = next((i + 1 for i, x in enumerate(scripture.BOOKS) if x is ref.book), None)
            ch = b.chapter(bk, ref.chapter) if bk else None
            if ch:
                if ref.verse:
                    a = b.index.get((bk, ref.chapter, ref.verse), ch[0])
                    z = b.index.get((bk, ref.chapter, ref.verse_end or ref.verse), a)
                    out.append((max(ch[0], a - 1), min(ch[1], z + NEAR_VERSES)))
                else:
                    out.append(ch)
        if self.lock is not None and self.lock_left > 0:
            a, z = self.lock
            out.append((max(0, a - 1), min(len(b.refs) - 1, z + NEAR_VERSES)))
        return out

    @staticmethod
    def _global(st: Stream, w: list[str]) -> list[int]:
        """Start positions in the stream of the regions sharing word pairs with the window."""
        stop = _STOP[st.lang]
        votes = collections.Counter()
        for i in range(len(w) - 1):
            if w[i] in stop and w[i + 1] in stop:
                continue
            a, b = st.vocab.get(w[i]), st.vocab.get(w[i + 1])
            if a is None or b is None:
                continue
            h = a * VOCAB_MAX + b
            lo, hi = np.searchsorted(st.pairs, [h, h + 1])
            if hi - lo > 3000:
                continue                                    # "a zis Domnul": everywhere
            for p in st.where[lo:hi]:
                votes[(int(p) - i) // 16] += 1
        return [d * 16 for d, c in votes.most_common(CANDIDATES) if c >= 2]

    def match(self, text: str, src: str = "ro", tgt: str = "en", ref=None) -> Match | None:
        """The published text for this piece if it is part of a verse, else None. Call once per
        piece, in order (the piece is remembered as context for the next ones)."""
        piece = words(text, src)
        if self.lang != src:
            self.reset()
            self.lang = src
        hist = list(self.history)[-max(WINDOW - len(piece), 0):]
        self.history.extend(k for k, *_ in piece)
        while len(self.history) > WINDOW:
            self.history.popleft()
        self.lock_left -= 1
        if self.lock_left <= 0:
            self.done = None
        if self.bible is None or len(piece) < NEAR_OWN or src not in self.bible.streams:
            return None
        w, h = hist + [k for k, *_ in piece], len(hist)
        found = []
        for st in self.bible.streams[src]:
            regions = [(int(st.first[a]), int(st.first[z + 1]), True) for a, z in self._near(ref)]
            regions += [(max(0, p - 8), p + len(w) + 24, False) for p in self._global(st, w)]
            for lo, hi, near in regions:
                cand = self._judge(st, w, h, lo, align(w, st.keys[lo:min(hi, len(st.keys))]), near)
                if cand is not None:
                    found.append(cand)
        if not found:
            return None
        best = max(found, key=lambda c: c["rank"])
        if not best["near"] and best["words"] < SURE_WORDS and \
                len({c["verse"] for c in found if c["rank"] >= best["rank"] - 1}) > 1:
            return None         # "enter into the kingdom of heaven": in several verses (a long quote is
        #                         the same text in each parallel: Deuteronomy 6:5 = Matthew 22:37)
        return self._caption(text, piece, h, best, src, tgt)

    @staticmethod
    def _judge(st: Stream, w: list[str], h: int, lo: int, pairs, near: bool) -> dict | None:
        """The features of one alignment, if they are enough to call it the verse."""
        stop = _STOP[st.lang]
        while len(pairs) > 1 and w[pairs[-1][0]] in stop and (pairs[-1][0] - pairs[-2][0] > 1 or
                                                               pairs[-1][1] - pairs[-2][1] > 1):
            pairs = pairs[:-1]                              # a stray "nu" two words on: not the verse
        while len(pairs) > 1 and w[pairs[0][0]] in stop and (pairs[1][0] - pairs[0][0] > 1 or
                                                              pairs[1][1] - pairs[0][1] > 1):
            pairs = pairs[1:]
        mine = [(i, j) for i, j, _ in pairs if i >= h]
        if len(mine) < NEAR_OWN:
            return None
        span = max(pairs[-1][0] - pairs[0][0], pairs[-1][1] - pairs[0][1]) + 1
        quality = sum(s for *_, s in pairs) / span
        rare = [st.idf.get(st.keys[lo + j], 0.0) for i, j, _ in pairs if content(w[i], st.lang)]
        own_rare = sum(st.idf.get(st.keys[lo + j], 0.0) for i, j in mine if content(w[i], st.lang))
        if len(mine) / (mine[-1][0] - mine[0][0] + 1) < NEAR_QUALITY:
            return None                                     # the piece's own words: scattered
        if near:
            ok = len(pairs) >= NEAR_WORDS and quality >= NEAR_QUALITY and own_rare >= NEAR_IDF
        else:
            ok = (len(pairs) >= GLOBAL_WORDS and len(mine) >= GLOBAL_OWN and quality >= GLOBAL_QUALITY
                  and sum(rare) >= GLOBAL_IDF)
        if not ok:
            return None
        return {"rank": len(pairs) * quality + (0.5 if near else 0), "st": st, "lo": lo, "mine": mine,
                "verse": int(st.verse[lo + mine[0][1]]),
                "quality": round(float(quality), 2), "words": len(pairs), "near": near, "idf": round(sum(rare), 1)}

    def _caption(self, text, piece, h, m: dict, src: str, tgt: str) -> Match | None:
        b, st, lo, mine = self.bible, m["st"], m["lo"], m["mine"]
        i0, i1 = mine[0][0] - h, mine[-1][0] - h               # piece word indexes of the verse part
        p0, p1 = lo + mine[0][1], lo + mine[-1][1]          # stream positions of those words
        va, vz = int(st.verse[p0]), int(st.verse[p1])
        vlo, vhi = int(st.first[va]), int(st.first[vz + 1])
        edge = lambda ws, a, z: [next((q for q in range(a, z) if sim(k, st.keys[q]) >= MIN_SIM), None)  # noqa: E731
                                 for k in ws]
        # 1-2 words of the piece left out of the alignment: the verse's own words when found next
        # to it ("prea iubiților" = "Preaiubiților"; the verse word mis-heard) are captioned with it
        lead = [k for k, *_ in piece[:i0]]
        if 0 < i0 < 3:
            if p0 > vlo and sim("".join(lead), st.keys[p0 - 1]) >= MIN_SIM:
                p0, i0 = p0 - 1, 0
            elif None not in (q := edge(lead, max(vlo, p0 - 4), p0)):
                p0, i0 = min(q), 0
        tail = [k for k, *_ in piece[i1 + 1:]]
        if 0 < len(tail) < 3 and None not in (q := edge(tail, p1 + 1, min(vhi, p1 + 5))):
            p1, i1 = max(q), len(piece) - 1
        out, done, ends = [], self.done, False
        for v in range(va, vz + 1):
            f, n = int(st.first[v]), int(st.first[v + 1] - st.first[v])
            s = p0 - f if v == va else 0
            e = p1 + 1 - f if v == vz else n
            ws, ks, breaks = _target(b.text[tgt][v], tgt)
            cands = links(st.keys[f:f + n], ks, src, b.lexicon)
            marks = anchors(cands)
            t0 = project(s, n, len(ws), breaks, marks)
            t1 = project(e if e < n - 1 else n, n, len(ws), breaks, marks)
            if t0 < len(ws) - 1 and _END.search(ws[t0]) and not any(s <= i < e and j == t0 for i, j, _ in cands):
                t0 += 1                                     # landed on the end of the sentence before
            floor = max([q for q in range(1, t0 + 1) if _END.search(ws[q - 1])], default=0)
            said = [j for i, j, _ in cands if s <= i < e and floor <= j < t0]
            if said:                                        # "le este rânduit" → "it is appointed for men"
                t0 = max(min(said), t0 - WIDEN)             # (never forward: that would run ahead)
            for _ in range(2):                              # "it is" before "appointed"
                if t0 > 0 and ks[t0 - 1] and ks[t0 - 1] <= _STOP[tgt] and not _BREAK.search(ws[t0 - 1][-1:]) \
                        and not (done is not None and done[0] == v and t0 <= done[1]):
                    t0 -= 1
            ends = t1 >= len(ws)
            for _ in range(2):                              # never end on "… In the"
                if not ends and t1 - 1 > t0 and ks[t1 - 1] and ks[t1 - 1] <= _LEAD[tgt] \
                        and not _BREAK.search(ws[t1 - 1][-1:]):
                    t1 -= 1
            if done is not None and done[0] == v:
                t0 = done[1] if t0 - done[1] <= 2 else t0  # go on where the last caption stopped
            if t1 > t0:
                out.append(" ".join(ws[t0:t1]))
                done = (v, t1)
        self.lock, self.lock_left = (va, vz), KEEP_LOCK
        caption = display(" ".join(out), tgt)
        n_words = len(caption.split())
        if n_words < 1 or n_words < 3 and not ends:
            return None                                     # "Your", "men": wait for more of the verse
        self.done = done
        before = text[:piece[i0][1]].strip(" ,;:-–—") if i0 else ""
        after = text[piece[i1][2]:].strip(" ,;:.-–—") if i1 < len(piece) - 1 else ""
        if 0 < len(tail) < 3 and after and None not in edge(tail, vlo, vhi):
            after = ""                                      # a verse word said in another order
        if 0 < i0 < 3 and before and None not in edge(lead, vlo, vhi):
            before = ""
        return Match(b.name(va, vz), m["quality"], m["words"], caption, before, after, m["near"])
