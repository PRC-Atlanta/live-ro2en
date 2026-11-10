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
QUOTES = {ord(q): None for q in "„”“«»\"‚‹›"}
ORD = {"ro": {"1": "Întâi", "2": "A doua", "3": "A treia"}, "en": {"1": "First", "2": "Second", "3": "Third"}}
# (verse, verses, to, and, chapter, words that put a number before a name without it being a book number)
WORDS = {"ro": ("versetul", "versetele", "până la", "și", "capitolul",
                ("capitolul", "capitolele", "versetul", "versetele", "ora", "anul", "pagina")),
         "en": ("verse", "verses", "to", "and", "chapter",
                ("chapter", "chapters", "verse", "verses", "page", "number"))}


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
    and_, chapter = WORDS[lang][3], WORDS[lang][4]
    alt = "|".join(map(re.escape, names))
    v = r"[1-9]\d{0,2}"
    end = r"(?=\s*(?:[.;:!?)]|$))"
    # Book N:M, then at most one of  -N2:M2 | -K | ,K[,K…] . Never half a reference: what follows may
    # not be more digits, a colon-digit, another range or another list item. A following "și/and N:M"
    # is left alone: it is as often a clock time.
    ref = re.compile(rf"\b({alt})(\s+)({v}):({v})"
                     rf"(?:\s*[-–]\s*({v}):({v})|\s*[-–]\s*({v})|((?:,\s?{v})+){end}(?!,\s?\d))?"
                     rf"(?!\d|:\d|,\d|\s*[-–]\s*\d)")
    num = None
    if numbered:
        nb = "|".join(map(re.escape, numbered))
        # "2 Timotei 3", "1 Corinthians, chapter 4", "din 2 Petru." but not "chapter 3 John writes"
        num = re.compile(rf"(?<![\w:,.])([123])\s+(?=(?:{nb})(?:\s+\d|\s*[,.;:!?]|\s*$|,?\s+{chapter}\b))")
    return ref, num


_CLOCK_EN = re.compile(r"\s*(?:am|pm|a\.m|p\.m)\b", re.IGNORECASE)


def book_numbers(text: str, lang: str) -> str:
    """"2 Timotei 3" -> "A doua Timotei 3", "1 Corinthians" -> "First Corinthians": numbered books only,
    and only where a chapter, punctuation or the end follows the book ("chapter 3 John writes" stays)."""
    num = _patterns(lang)[1]
    if not num:
        return text
    before = WORDS[lang][5]

    def sub(m):
        prev = text[:m.start()].rstrip().rsplit(" ", 1)[-1].lower().strip(",.;:")
        return m[0] if prev in before else ORD[lang][m[1]] + " "
    return num.sub(sub, text)


def references(text: str, lang: str) -> str:
    one, many, to, and_, chapter, _ = WORDS[lang]

    def sub(m):
        tail = m[5] or m[7] or m[8]
        if lang == "en" and not tail and _CLOCK_EN.match(m.string, m.end()):
            return m[0]                            # "Mark 10:30 am": a clock time ("am" is a Romanian verb)
        out = f"{m[1]}{m[2]}{m[3]}, "
        if m[5]:                                   # 3:16-4:2
            out += f"{one} {m[4]} {to} {chapter} {m[5]}, {one} {m[6]}"
        elif m[7]:                                 # 13:4-7
            out += f"{many} {m[4]} {to} {m[7]}"
        elif m[8]:                                 # 3:16,17[,18]
            vs = [m[4]] + re.findall(r"\d+", m[8])
            out += f"{many} {', '.join(vs[:-1])} {and_} {vs[-1]}"
        else:
            out += f"{one} {m[4]}"
        return out
    return _patterns(lang)[0].sub(sub, text)


def speakable(text: str, lang: str = "ro") -> str:
    text = re.sub(r"\*{2,}", " ", text.translate(QUOTES))     # a masked word (the blocklist's ***) is not read
    text = references(book_numbers(text, lang), lang)
    return re.sub(r"\s+", " ", text).strip()
