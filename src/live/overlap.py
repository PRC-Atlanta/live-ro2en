"""Words repeated across chunk boundaries (translation idea 16).

The English recogniser re-reads the previous seconds of audio as context, and now and then
the end of one chunk comes back at the start of the next, often written another way:
"...May nineteenth, nineteen sixty-three." | "19, 1963, in Vicovisus..."; "In two thousand
twelve" | "2012 they began...". Shown as is, the captions say it twice.

  trim(prev, text, lang)   drop the leading span of `text` that repeats the end of `prev`

Conservative: words are compared case, punctuation and diacritics aside and numbers by value
(words or digits); a single repeated word is dropped only when it is a number.
"""
from __future__ import annotations

import re

import guards
from textnorm import fold_diacritics

_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*")


def _keys(text: str, lang: str) -> list[tuple[str, int, int, bool]]:
    """(key, start, end, is_number) per word; a number run ("nineteen sixty-three", "1963") is
    one item keyed by its value."""
    runs = [r for r in guards._runs(text, lang)]
    out, i = [], 0
    for m in _WORD.finditer(text):
        while i < len(runs) and runs[i]["end"] <= m.start():
            i += 1
        if i < len(runs) and runs[i]["start"] <= m.start() < runs[i]["end"]:
            r = runs[i]
            if not out or out[-1][1] != r["start"]:
                out.append((str(r["value"]), r["start"], r["end"], True))
            continue
        out.append((fold_diacritics(m.group().lower().replace("’", "'")), m.start(), m.end(), False))
    return out


def trim(prev: str, text: str, lang: str = "en", max_words: int = 8) -> tuple[str, str]:
    """(text without the repeated start, the dropped span or ""). The longest run of at most
    `max_words` items that ends `prev` and starts `text` is dropped, if it is at least two items
    long or a number."""
    if not prev or not text:
        return text, ""
    a, b = _keys(prev, lang), _keys(text, lang)
    for k in range(min(max_words, len(a), len(b)), 0, -1):
        if [x[0] for x in a[-k:]] != [x[0] for x in b[:k]]:
            continue
        if k < 2 and not b[0][3]:
            return text, ""
        cut = b[k - 1][2]
        rest = text[cut:].lstrip(" ,;:.-–—")
        return rest, text[:cut].strip()
    return text, ""
