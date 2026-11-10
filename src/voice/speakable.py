"""Caption text -> text the voice can say (plan §4.1, §14). The caption itself is never changed.

Measured 2026-11-10 (M0, scripts/voice_m0.py --raw + Whisper): Supertonic 3 reads plain numbers right
in both languages (1989, 2.500, 5,000, 47, 19 mai 1963, clock times), so no number spelling is
needed. It garbles chapter:verse after a book: "Romani 8:28" was heard as "828", "Apocalipsa 21:4"
as "24", "1 Corinteni 13:4-7" as "3, 47", "John 3:16" as "John 3". So after a Bible book:
  ro  N:M -> "N, versetul M"      N:M-K -> "N, versetele M până la K"
  en  N:M -> "N, verse M"         N:M-K -> "N, verses M to K"
and a leading 1/2/3 before a numbered book is spelled (Întâi / A doua / A treia, First / Second /
Third). A clock time ("la 3:40", "at 1:11") has no book before it and is left alone."""
from __future__ import annotations

import csv
import functools
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOOKS_CSV = ROOT / "data" / "bible_books.csv"
QUOTES = {ord(q): None for q in "„”“«»\""}
ORD = {"ro": {"1": "Întâi", "2": "A doua", "3": "A treia"}, "en": {"1": "First", "2": "Second", "3": "Third"}}
VERSE = {"ro": ("versetul", "versetele", "până la"), "en": ("verse", "verses", "to")}


@functools.lru_cache(maxsize=None)
def books(lang: str, path: pathlib.Path = BOOKS_CSV) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(every book name without its number, the names of the numbered books), longest first."""
    names, numbered = set(), set()
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            for n in [row[lang]] + [v for v in (row.get(f"{lang}_variants") or "").split("|") if v]:
                m = re.match(r"^([123]) (.+)$", n.strip())
                if m:
                    numbered.add(m[2])
                    names.add(m[2])
                elif n.strip():
                    names.add(n.strip())
    if lang == "ro":
        names |= {"Psalmul", "Psalmii", "Psalm"}
    else:
        names |= {"Psalm", "Psalms"}
    key = lambda s: (-len(s), s)        # noqa: E731
    return tuple(sorted(names, key=key)), tuple(sorted(numbered, key=key))


@functools.lru_cache(maxsize=None)
def _patterns(lang: str):
    names, numbered = books(lang)
    alt = "|".join(map(re.escape, names))
    ref = re.compile(rf"\b({alt})(\s+)(\d{{1,3}}):(\d{{1,3}})(?:\s*[-–]\s*(\d{{1,3}}))?(?!\d|:\d)")
    num = re.compile(rf"(?<![\w:,.])([123])\s+(?=(?:{'|'.join(map(re.escape, numbered))})\b)") if numbered else None
    return ref, num


def book_numbers(text: str, lang: str) -> str:
    """"2 Timotei" -> "A doua Timotei", "1 Corinthians" -> "First Corinthians" (numbered books only)."""
    num = _patterns(lang)[1]
    return num.sub(lambda m: ORD[lang][m[1]] + " ", text) if num else text


def references(text: str, lang: str) -> str:
    one, many, to = VERSE[lang]

    def sub(m):
        if m[5]:
            return f"{m[1]}{m[2]}{m[3]}, {many} {m[4]} {to} {m[5]}"
        return f"{m[1]}{m[2]}{m[3]}, {one} {m[4]}"
    return _patterns(lang)[0].sub(sub, text)


def speakable(text: str, lang: str = "ro") -> str:
    text = text.translate(QUOTES)
    text = references(book_numbers(text, lang), lang)
    return re.sub(r"\s+", " ", text).strip()
