"""Announced Bible references: find "Evrei, capitolul 6, versetul 10" / "Hebrews thirteen seven"
in a chunk and keep a hint for the next few chunks, so the translator renders the reference
exactly (Hebrews 13:7 = Evrei 13:7) and follows the published wording without running ahead.

Book names come from data/bible_books.csv (66 books, RO and EN, with spoken variants). A book
counts only when a chapter number follows it ("Romani 14", not "romani" the people; "Hebrews
13", not "in Hebrew"). A reference whose book was not understood ("34 cu 16 din cartea Poc")
keeps the numbers only: a wrong book on screen is worse than none."""
from __future__ import annotations

import csv
import dataclasses
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from textnorm import fix_cedilla, fold_diacritics   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOKS_CSV = ROOT / "data" / "bible_books.csv"
CHUNKS = 6              # a reading announced once is read in ~5-6 chunks

_WORD_RE = re.compile(r"\d+|[^\W\d_]+")
_ORD = {"1": 1, "2": 2, "3": 3, "i": 1, "ii": 2, "iii": 3,
        "intai": 1, "intaia": 1, "prima": 1, "first": 1, "1st": 1,
        "doua": 2, "second": 2, "2nd": 2, "treia": 3, "third": 3, "3rd": 3}
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                     "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split(), 2)}
# words allowed between the book, chapter and verse numbers
_FILL = {"capitolul", "capitol", "cap", "chapter", "versetul", "versetele", "versetului", "verset",
         "verse", "verses", "v", "de", "la", "cu", "din", "from", "si", "and", "the", "in"}
_RANGE = {"pana", "to", "through", "thru"}
# book names that are also common words: a book only when written with a capital ("Job 3")
_COMMON = {"job", "numbers", "acts", "mark", "judges", "kings", "ruth", "tit", "romani"}


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(fold_diacritics(fix_cedilla(text).lower()))


@dataclasses.dataclass(frozen=True)
class Book:
    en: str
    ro: str
    keys: tuple            # ((ordinal or 0, (word, ...)), ...) every spoken form, RO and EN


def load_books(path: pathlib.Path | str = BOOKS_CSV) -> list[Book]:
    if not pathlib.Path(path).exists():
        return []
    books = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            names = [r["en"], r["ro"]] + [v for c in ("en_variants", "ro_variants")
                                          for v in (r.get(c) or "").split("|") if v.strip()]
            keys = []
            for n in names:
                w = _words(n)
                num = int(w[0]) if w and w[0].isdigit() else 0
                keys.append((num, tuple(w[1:] if num else w)))
            books.append(Book(r["en"].strip(), r["ro"].strip(), tuple(dict.fromkeys(keys))))
    return books


BOOKS = load_books()


@dataclasses.dataclass
class Ref:
    book: Book | None
    chapter: int
    verse: int | None = None
    verse_end: int | None = None

    def nums(self) -> str:
        if self.verse is None:
            return str(self.chapter)
        return f"{self.chapter}:{self.verse}" + (f"-{self.verse_end}" if self.verse_end else "")

    def name(self, lang: str) -> str:
        """'Hebrews 13:7' / 'Evrei 13:7'; only the numbers when the book was not understood."""
        if self.book is None:
            return self.nums()
        return f"{self.book.en if lang == 'en' else self.book.ro} {self.nums()}"


def _number(w: list[str], i: int) -> tuple[int | None, int]:
    """A number at w[i] (digits or English words up to 199): (value, next index)."""
    if i >= len(w):
        return None, i
    if w[i].isdigit():
        return int(w[i]), i + 1
    n, j = 0, i
    if w[j] in ("one", "a") and j + 1 < len(w) and w[j + 1] == "hundred":
        n, j = 100, j + 2
        if j < len(w) and w[j] == "and":
            j += 1
    if j < len(w) and w[j] in _TENS:
        n, j = n + _TENS[w[j]], j + 1
        if j < len(w) and w[j] in _UNITS and 0 < _UNITS[w[j]] < 10:
            n, j = n + _UNITS[w[j]], j + 1
    elif j < len(w) and w[j] in _UNITS:
        n, j = n + _UNITS[w[j]], j + 1
    return (n, j) if j > i and n > 0 else (None, i)


def _skip(w: list[str], i: int, words=_FILL, most: int = 3) -> int:
    k = i
    while k < len(w) and k - i < most and w[k] in words:
        k += 1
    return k


def _numbers(w: list[str], i: int) -> tuple[int | None, int | None, int | None, int]:
    """chapter, verse, last verse after a book name (or after 'capitolul')."""
    ch, i = _number(w, _skip(w, i))
    if ch is None:
        return None, None, None, i
    verse, j = _number(w, _skip(w, i))
    if verse is None:
        return ch, None, None, i
    i, end = j, None
    k = _skip(w, i, _FILL | {","})
    if k < len(w) and w[k] in _RANGE:
        end, k2 = _number(w, _skip(w, _skip(w, k + 1, _RANGE | _FILL)))
        if end is not None and end > verse:
            i = k2
        else:
            end = None
    return ch, verse, end, i


def _match_book(w: list[str], raw: list[str], i: int, books) -> tuple[Book | None, int]:
    """The longest book name starting at w[i] (an ordinal may come first: '1', 'întâi', 'first')."""
    best, best_j = None, i
    for b in books:
        for num, key in b.keys:
            j = i
            if num:
                if j >= len(w) or _ORD.get(w[j]) != num:
                    if not (j + 1 < len(w) and w[j] == "a" and _ORD.get(w[j + 1]) == num):
                        continue
                    j += 1
                j += 1
            if key and tuple(w[j:j + len(key)]) == key and j + len(key) > best_j \
                    and not (len(key) == 1 and key[0] in _COMMON and not raw[j][:1].isupper()):
                best, best_j = b, j + len(key)
    return best, best_j


def find(text: str, books=None) -> Ref | None:
    """The last Bible reference announced in the text, or None."""
    books = BOOKS if books is None else books
    w, raw, found, i = _words(text), _WORD_RE.findall(fold_diacritics(fix_cedilla(text))), None, 0
    while i < len(w):
        b, j = _match_book(w, raw, i, books)
        if b is not None:
            ch, v, end, k = _numbers(w, j)
            if ch is not None and ch <= 150:
                found, i = Ref(b, ch, v, end), k
                continue
        if w[i] in ("capitolul", "chapter"):           # a chapter with no book we know
            ch, v, end, k = _numbers(w, i + 1)
            if ch is not None:
                found, i = Ref(None, ch, v, end), k
                continue
        if w[i].isdigit() and i + 3 < len(w) and w[i + 1] == "cu" and w[i + 2].isdigit() \
                and w[i + 3] == "din":                   # '34 cu 16 din cartea …'
            found, i = Ref(None, int(w[i]), int(w[i + 2])), i + 4
            continue
        i += 1
    return found


def verse_only(text: str) -> tuple[int, int | None] | None:
    """'versetul 8' / 'verse eight' with no book: the next verse of the reading."""
    w = _words(text)
    for i, t in enumerate(w):
        if t in ("versetul", "verse", "versetele", "verses"):
            v, j = _number(w, i + 1)
            if v is not None:
                end = None
                k = _skip(w, j, _RANGE | _FILL)
                if k > j and any(x in _RANGE for x in w[j:k]):
                    end, _ = _number(w, k)
                return v, end if end and end > v else None
    return None


class Reading:
    """The reference being read, carried over the next CHUNKS chunks."""

    def __init__(self, chunks: int = CHUNKS, books=None):
        self.chunks, self.books = chunks, books
        self.ref: Ref | None = None
        self.left = 0
        self.announced = False

    def reset(self):
        self.ref, self.left, self.announced = None, 0, False

    def update(self, text: str) -> Ref | None:
        """Read one chunk; the reference in force for it (announced here or a few chunks back)."""
        ref = find(text, self.books)
        if ref is not None:
            if ref.book is None and self.ref is not None and self.ref.book is not None and self.left > 0 \
                    and ref.chapter == self.ref.chapter:
                ref = dataclasses.replace(ref, book=self.ref.book)     # 'capitolul 14, versetul 7' of Romani
            vo = verse_only(text) if ref.verse is None else None
            if vo is not None:                                          # 'Romani 14 … de la versetul 7'
                ref = dataclasses.replace(ref, verse=vo[0], verse_end=vo[1])
            self.ref, self.left, self.announced = ref, self.chunks, True
            return ref
        self.announced = False
        if self.ref is None or self.left <= 0:
            self.ref = None
            return None
        self.left -= 1
        vo = verse_only(text)
        if vo is not None:
            self.ref = dataclasses.replace(self.ref, verse=vo[0], verse_end=vo[1])
            self.announced = True
        return self.ref

    def hint(self, ref: Ref | None, src: str, tgt: str) -> str:
        if ref is None:
            return ""
        if ref.book is None:
            return (f"The speaker announced a Bible reference ({ref.nums()}) but the book name was not clear: "
                    f"write only the numbers, never name a book.")
        say = f"Write the reference as \"{ref.name(tgt)}\". " if self.announced else ""
        wording = "the NKJV wording" if tgt == "en" else "the Cornilescu wording"
        return (f"{say}The speaker is reading {ref.name('en')} ({ref.name('ro')}): where this piece is part "
                f"of the verse, use {wording}; translate only the words in this piece, do not run ahead.")
