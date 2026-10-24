"""Confidence gate before translation (idea 17): a phrase the recogniser garbled must not reach
the translator, which turns non-words into fluent, confident, wrong captions ("slava ludum
nezukum tum" -> "glory to gambling"). A missing caption is better than an invented one.

The signal is the share of real words in the phrase's language, checked against offline word
lists (data/lexicon/*.txt, built by scripts/build_lexicon.py, plus the system dictionary for
English) and names (glossary, terms, today's names, capitalised words inside a sentence).
Words of the other language, single letters and glued scraps like "my.s" count against it.
The Romanian list is the Bible plus past services, so it misses modern words ("perfuzii"): an
unknown Romanian-looking word gets half credit there, a word with Romanian diacritics full
credit (the Romanian recogniser writes them only on words it is sure of). Short real phrases
("Amin!", "Aleluia", a list of names) pass."""
from __future__ import annotations

import functools
import pathlib
import re
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[2]
SYSTEM_WORDS = pathlib.Path("/usr/share/dict/words")
PROPER_NAMES = pathlib.Path("/usr/share/dict/propernames")

_FOLD = str.maketrans({"ă": "a", "â": "a", "î": "i", "ș": "s", "ț": "t", "ş": "s", "ţ": "t"})
_L = r"[^\W\d_]"
_TOKEN = re.compile(rf"{_L}+(?:[-'’]{_L}+)*")     # "n-aveți", "it's": one token, checked by parts
_GLUED = re.compile(rf"{_L}\.{_L}(?!\.)")           # "my.s" (not "U.S.", not "3.5")
_SENT_END = re.compile(r"[.!?…]\s*$")
_RO_MARKS = set("ăâîșțşţ")
SINGLE_OK = {"ro": {"a", "o", "e", "i", "ș", "î"}, "en": {"a", "i", "o"}}
CLITICS = {"ro": {"a", "o", "e", "i", "ș", "s", "n", "l", "m", "v", "î"}}
# endings on a stem the lists know: definite article / plural / case, English inflections
RO_SUFFIXES = ("ului", "elor", "ilor", "lor", "lui", "ul", "le", "ua", "ii", "ile", "a", "i", "e", "u")
EN_SUFFIXES = ("ies", "es", "s", "ed", "d", "ing", "ly", "er", "est")


def fold(word: str) -> str:
    return unicodedata.normalize("NFC", word).lower().translate(_FOLD)


def _read(path: pathlib.Path, min_len: int = 1) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {fold(w.strip()) for w in lines if len(w.strip()) >= min_len and not w.lstrip().startswith("#")}


@functools.lru_cache(maxsize=4)
def lexicon(root: str = str(ROOT)) -> dict[str, set[str]]:
    """{'ro', 'en', 'names'}: folded lower-case words. The system dictionary only adds words of
    4+ letters: its short entries ('tum', 'ers') would let garbage through, and the common
    short English words are in the built list anyway."""
    base = pathlib.Path(root)
    ro, en = _read(base / "data" / "lexicon" / "ro.txt"), _read(base / "data" / "lexicon" / "en.txt")
    en |= {w for w in _read(SYSTEM_WORDS, 4) if w.isalpha()}
    names = _read(PROPER_NAMES)
    for f in ("terms.txt", "keywords_en.txt"):
        for line in _read(base / f):
            names.update(fold(t) for t in re.findall(rf"{_L}+", line))
    return {"ro": ro, "en": en, "names": names}


def names_from(rows) -> set[str]:
    """Folded words of glossary rows ({ro, en}): today's names count as real words."""
    out: set[str] = set()
    for r in rows or ():
        for k in ("ro", "en"):
            out.update(fold(t) for t in re.findall(rf"{_L}+", r.get(k) or ""))
    return out


def _known_word(w: str, words: set[str], lang: str) -> bool:
    if w in words:
        return True
    for suf in (RO_SUFFIXES if lang == "ro" else EN_SUFFIXES):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[: -len(suf)]
            if stem in words or (lang == "en" and suf in ("ed", "ing", "er", "est") and stem + "e" in words):
                return True
    return False


def _known(tok: str, words: set[str], lang: str) -> bool:
    """A hyphen/apostrophe compound is known when its parts are ('m-a', 'vieții-cheie', 'it's');
    one-letter clitics ('n-', '-l', ''s') are ignored."""
    parts = re.split(r"[-'’]", tok)
    long = [fold(p) for p in parts if len(p) > 1]
    if not long:
        return len(parts) > 1 and all(p.lower() in CLITICS.get(lang, ()) for p in parts)   # "s-a", "l-a"
    return all(_known_word(p, words, lang) for p in long)


def features(text: str, lang: str, lex: dict, names: set[str] = frozenset()) -> dict:
    """Word counts behind the decision (also written to the log record). `real` may be
    fractional: an unknown Romanian-looking word counts half."""
    text = unicodedata.normalize("NFC", text or "")
    other = "en" if lang == "ro" else "ro"
    n = name = frag = foreign = neutral = 0
    real = 0.0
    caps = 0
    for m in _TOKEN.finditer(text):
        t = m.group()
        w = fold(t)
        n += 1
        initial = not text[: m.start()].strip() or bool(_SENT_END.search(text[: m.start()]))
        cap = t[0].isupper()
        caps += cap
        if len(t) == 1:
            if t.lower() in SINGLE_OK[lang]:
                real += 1
            else:
                frag += 1
        elif _known(t, lex[lang], lang):
            real += 1
        elif cap and (w in names or w in lex["names"]):
            real += 1
            name += 1
        elif cap and len(t) <= 2:
            frag += 1                                  # "Ts", "GS": letters, not a name
        elif cap and not initial:
            if w in lex[other]:
                neutral += 1                           # "Romania", "Millions": can't tell
            else:
                real += 0.5                            # "Puha" a name, or "Ludum" a garble
                name += 1
        elif w in lex[other]:
            if lang == "ro" and len(t) >= 5:
                real += 0.5                            # "permanent", "tonic": likely a shared word
            else:
                foreign += 1
        elif lang == "ro" and any(c in _RO_MARKS for c in t.lower()):
            real += 1
        elif lang == "ro" and len(t) >= 4:
            real += 0.5
    frag += len(_GLUED.findall(text))
    counted = n - neutral
    return {"words": n, "real": real, "names": name, "frag": frag, "foreign": foreign,
            "all_caps": bool(n) and caps == n,
            "real_share": round(real / counted, 2) if counted > 0 else 1.0}


def check(text: str, lang: str, lex: dict, names: set[str] = frozenset(),
          min_real: float = 0.6, max_frag: float = 0.25) -> tuple[bool, str, dict]:
    """(ok, reason, features). Rejects: a lone scrap ('F.'), a phrase made largely of single
    letters / glued scraps, or one with too few real words in its language. A phrase of
    capitalised words only is a list of names (idea 15) and passes."""
    f = features(text, lang, lex, names)
    n = f["words"]
    if not n:
        return True, "", f
    if n <= 2 and f["frag"] >= n or n == 1 and len(text.strip(" .,;:!?…")) == 1:
        return False, "fragment", f
    if n >= 3 and f["frag"] / n > max_frag:
        return False, "fragments", f
    if f["all_caps"] and not f["frag"]:
        return True, "", f
    if f["real_share"] < min_real and (n >= 2 or f["foreign"] == 0 and f["real"] == 0):
        return False, "non_words", f
    return True, "", f


_SENTENCES = re.compile(r"(?<=[.!?…])\s+")


def screen(text: str, lang: str, lex: dict, names: set[str] = frozenset(),
           min_real: float = 0.6, max_frag: float = 0.25) -> tuple[str, str, list[str]]:
    """(text to translate, reason if all of it was rejected, sentences cut). A garbled sentence
    of 3+ words is cut on its own ("nezurkamai sum tsoom. It's okay to cry..." keeps the second
    sentence); then what is left is checked as a whole. Returns "" when nothing is left."""
    kept, cut = [], []
    parts = [p for p in _SENTENCES.split(text.strip()) if p]
    if len(parts) > 1:
        for p in parts:
            if len(_TOKEN.findall(p)) >= 3 and not check(p, lang, lex, names, min_real, max_frag)[0]:
                cut.append(p)
            else:
                kept.append(p)
    else:
        kept = parts
    rest = " ".join(kept)
    if not rest:
        return "", "non_words", cut
    ok, why, _ = check(rest, lang, lex, names, min_real, max_frag)
    return (rest, "", cut) if ok else ("", why, cut + [rest])
