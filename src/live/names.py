"""Names that stay names: today's people and places (Settings → Names & words) and the
glossary's own names, kept exactly as written by the translator, and the English
recogniser's phonetic spellings mapped back to them before translating
(Dragosh / Ragosh → Dragoș, Souchava → Suceava, Vicovisus → Vicovu de Sus).

The match is conservative: a capitalised word (or 2-3 words run together), close in sound
to a listed name; anything uncertain is left as the recogniser wrote it."""
from __future__ import annotations

import difflib
import functools
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from textnorm import fix_cedilla, fold_diacritics   # noqa: E402

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


def fold(text: str) -> str:
    return fold_diacritics(fix_cedilla(text).lower())


@functools.lru_cache(maxsize=4096)
def skeleton(text: str) -> str:
    """How a name sounds, in plain letters, the same for 'Dragoș' and 'Dragosh', 'Suceava' and
    'Souchava', 'Paraschiva' and 'Paraskiva'."""
    w = fix_cedilla(text).lower()
    w = w.replace("ș", "sh").replace("ț", "ts")
    w = fold_diacritics(w).replace(" ", "").replace("-", "")
    w = re.sub(r"[^a-z]", "", w)
    w = w.replace("ph", "f").replace("ck", "k").replace("ou", "u").replace("w", "v").replace("y", "i")
    w = w.replace("che", "ke").replace("chi", "ki").replace("ghe", "ge").replace("ghi", "gi")
    w = re.sub(r"c(?=[ei])", "ch", w)                     # Romanian ce/ci: Suceava → suchava
    w = re.sub(r"(?<!c)ch(?![eiaou])", "k", w)            # sch: Paraschiva → paraskiva
    w = re.sub(r"(?<=ch)e(?=[aou])", "", w)               # cea → cha
    w = re.sub(r"c(?!h)", "k", w)
    return w


def _close(heard: str, name: str) -> bool:
    a, b = skeleton(heard), skeleton(name)
    if not a or not b or a == b:
        return a == b and bool(a)
    r = difflib.SequenceMatcher(None, a, b).ratio()
    if len(b) <= 4:                                       # short names: nearly the same and same start
        return r >= 0.85 and a[0] == b[0]
    return r >= 0.9 or (r >= 0.75 and a[0] == b[0] and a[-1] == b[-1])


def candidates(names: list[str]) -> list[str]:
    """Full names, and each word of a person's name ('Dragoș Puha' → 'Dragoș', 'Puha');
    a place with a small word in it ('Vicovu de Sus') only as a whole."""
    out = []
    for n in names:
        out.append(n)
        words = n.split()
        if len(words) > 1 and all(w[:1].isupper() for w in words):
            out += [w for w in words if len(w) >= 3]
    return list(dict.fromkeys(out))


def map_back(text: str, names: list[str]) -> str:
    """Replace the English recogniser's spellings of today's names with the listed form."""
    if not names or not text:
        return text
    cands = candidates(names)
    exact = {fold(c) for c in cands}
    words = list(_WORD_RE.finditer(text))
    out, last, i = [], 0, 0
    while i < len(words):
        best = None
        if words[i].group(0)[:1].isupper():
            for n in (3, 2, 1):
                win = words[i:i + n]
                if len(win) < n or (n > 1 and not all(w.group(0)[:1].isupper() for w in win)):
                    continue
                heard = " ".join(w.group(0) for w in win)
                if fold(heard) in exact:
                    best = (n, None)                     # already right: leave it
                    break
                hit = next((c for c in cands if len(skeleton(heard)) >= 3 and _close(heard, c)), None)
                if hit:
                    best = (n, hit)
                    break
        if best and best[1]:
            n, hit = best
            out.append(text[last:words[i].start()] + hit)
            last = words[i + n - 1].end()
            i += n
        else:
            i += best[0] if best else 1
    return "".join(out) + text[last:]


def mentioned(text: str, names: list[str]) -> list[str]:
    """The listed names any of whose words (3+ letters) occur in the text."""
    seen = set(fold(w) for w in _WORD_RE.findall(text))
    return [n for n in names if any(len(w) >= 3 and fold(w) in seen for w in n.split())]
