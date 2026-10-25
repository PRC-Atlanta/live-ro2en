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


def _sounds_ratio(heard: str, name: str) -> float:
    return difflib.SequenceMatcher(None, skeleton(heard), skeleton(name)).ratio()


def candidates(names: list[str]) -> list[str]:
    """Full names, and each word of a person's name ('Dragoș Puha' → 'Dragoș', 'Puha');
    a place with a small word in it ('Vicovu de Sus') only as a whole."""
    out = []
    for n in names:
        out.append(n)
        words = n.split()
        if len(words) > 1 and all(w[:1].isupper() for w in words):
            out += [w for w in words if len(w) >= 3 and fold(w) not in RO_CUES]
    return list(dict.fromkeys(out))


def map_back(text: str, names: list[str], known: set[str] = frozenset()) -> str:
    """Replace the English recogniser's spellings of today's names with the listed form.
    `known` holds folded dictionary words: a heard word in it is a real word, not a misheard
    name ('Bethany' is not 'Botoșani', 'Sceva' is not 'Suceava'), and is only kept as is."""
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
                # a dictionary word ('Bethany', 'Sceva', 'Christian') only when short and sounding
                # nearly the same as the name: 'Pua' (a Bible name too) is still Puha, 'Such Ava'
                # still Suceava
                strict = all(fold(w.group(0)) in known for w in win)
                if strict and any(len(w.group(0)) > 4 for w in win):
                    continue                            # 'Christian' is a word, not Cristian
                hit = next((c for c in cands if len(skeleton(heard)) >= 3 and _close(heard, c)
                            and not (strict and _sounds_ratio(heard, c) < 0.85)), None)
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
    """The listed names any of whose own words (capitalised, 3+ letters, not "Fratele", "Sora",
    "Familia") occur in the text. "fratele" alone used to bring in every saved "Fratele …"
    name, and Gemma answered with "Fratele Emanuel și Dumnezeu" in Romanian (2026-10-25)."""
    seen = set(fold(w) for w in _WORD_RE.findall(text))
    return [n for n in names if any(len(w) >= 3 and w[:1].isupper() and fold(w) not in RO_CUES
                                    and fold(w) in seen for w in n.split())]


# --- the Romanian recogniser's near-misses (idea 2) ---------------------------------------

def ro_names(gloss) -> list[str]:
    """Today's and the saved names as Romanian speech has them: the capitalised Romanian side
    of the session and user rows ('Dragoș Puha', 'Petru' even where it reads 'Peter' in
    English) and the glossary's kept names. Boosted in the Romanian recogniser and repaired
    before translating."""
    rows = list(getattr(gloss, "session", None) or []) + list(getattr(gloss, "user", None) or [])
    out = [r["ro"].strip() for r in rows if r.get("ro", "").strip()[:1].isupper()]
    extra = list(getattr(gloss, "extra_names", None) or [])     # Settings → Names & words
    return list(dict.fromkeys(out + extra + list(getattr(gloss, "names", None) or [])))


# a word after one of these is a person's name more often than not ("familia Pha", "fratele Drago")
RO_CUES = {"frate", "fratele", "fratelui", "fratilor", "sora", "sorei", "sura", "surorii", "familia",
           "familiei", "familie", "familiile", "tata", "tatal", "tatei", "mama", "mamei", "fiul", "fiica",
           "pastor", "pastorul", "pastorului"}
# endings of a name's case forms: 'Paraschivei', 'Dragoșului' are the name, not a near-miss
_RO_ENDINGS = ("ului", "ul", "ei", "ii", "i", "a", "u", "e")


@functools.lru_cache(maxsize=4096)
def ro_key(text: str) -> str:
    """How a Romanian word sounds, voicing ignored: 'Buha' and 'Puha', 'Drago' and 'Dragoș',
    'Parascheva' and 'Paraschiva' come out close."""
    w = fold(text)
    w = re.sub(r"[^a-z]", "", w)
    w = w.replace("sch", "sk").replace("chi", "ki").replace("che", "ke").replace("ghi", "gi").replace("ghe", "ge")
    w = w.replace("q", "k").replace("w", "v").replace("y", "i").replace("x", "ks")
    return w.translate(str.maketrans("bdgvzc", "ptkfsk"))


def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _variant(heard: str, name: str) -> bool:
    """Another real name or a case form, never a near-miss: Claudia/Claudiu, Daniel/Daniela,
    Paraschiv/Paraschiva, Paraschivei."""
    h, n = fold(heard), fold(name)
    if len(h) >= 4 and len(n) >= 4 and h[:-1] == n[:-1] and h[-1] in "aeiou" and n[-1] in "aeiou":
        return True
    if min(len(h), len(n)) >= 5 and (h == n + "a" or n == h + "a"):
        return True
    if abs(len(h) - len(n)) >= 2 and (h.startswith(n) or n.startswith(h)):
        return True                                     # Mariana/Maria, Cristi/Cristian
    stem = n[:-1] if n[-1] in "aeiou" else n
    return len(h) > len(n) - 1 and h.startswith(stem) and h[len(stem):] in _RO_ENDINGS


def _ro_match(heard: str, name: str, cued: bool) -> bool:
    a, b = ro_key(heard), ro_key(name)
    if len(a) < 2 or not b or _variant(heard, name):
        return False
    if a == b:
        return True
    if abs(len(a) - len(b)) > 2:
        return False
    r = _ratio(a, b)
    if r >= 0.7 and cued and a[0] == b[0] and abs(len(a) - len(b)) <= 1:              # 'familia Puia', 'Dragoș Puma'
        return True
    if len(b) <= 4:                                     # short names only with a cue or a capital
        return r >= 0.85 and a[0] == b[0] and cued
    return r >= 0.9 or (r >= 0.75 and a[0] == b[0] and a[-1] == b[-1])


def repair_ro(text: str, names: list[str], known: set[str] = frozenset()) -> tuple[str, list[tuple[str, str]]]:
    """Map the Romanian recogniser's near-misses of today's names back to the name, before
    translating: 'familia Pha' → 'familia Puha', 'Drago' → 'Dragoș', 'Parascheva' → 'Paraschiva',
    'paraschiva' → 'Paraschiva'. `known` holds folded dictionary words, which are left alone
    unless capitalised inside a sentence right after a cue ('familia Pua', 'Dragoș Puva').
    Returns (text, [(heard, name)])."""
    if not names or not text:
        return text, []
    single = [c for c in candidates(names) if " " not in c and (c in names or fold(c) not in known)]
    exact = {fold(c): c for c in single}
    glued = {fold(a) + fold(b): f"{a} {b}" for a in single for b in single if a != b}
    words = list(_WORD_RE.finditer(text))
    out, fixes, last = [], [], 0
    for i, m in enumerate(words):
        w = m.group(0)
        f = fold(w)
        if len(f) < 2:
            continue
        hit = None
        if f in glued and f not in known:
            hit = glued[f]                              # 'Sorincoban' → Sorin Coban
        elif f in exact:
            if exact[f] != w and w.islower() and f not in known:
                hit = exact[f]                          # 'sură paraschiva' → Paraschiva
        else:
            before = text[words[i - 1].end():m.start()] if i else ""
            prev = fold(words[i - 1].group(0)) if i else ""
            nxt = fold(words[i + 1].group(0)) if i + 1 < len(words) else ""
            gap_ok = i and not re.search(r"[.!?;:]", before)
            mid_cap = w[:1].isupper() and gap_ok
            cued = bool(gap_ok and (prev in RO_CUES or prev in exact)) or nxt in exact
            if f in known and not (cued and mid_cap):
                continue
            ok = [c for c in single if _ro_match(w, c, cued and (mid_cap or f not in known))]
            if ok:
                hit = max(ok, key=lambda c: _ratio(ro_key(w), ro_key(c)))
        if hit:
            out.append(text[last:m.start()] + hit)
            last = m.end()
            fixes.append((w, hit))
    return "".join(out) + text[last:], fixes
