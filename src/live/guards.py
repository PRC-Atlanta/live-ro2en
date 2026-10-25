"""Numbers and negations guard (translation idea 5).

The translator garbles number *words* in both directions ("cei paisprezece copii" -> "fifteen
children", "October fifteenth" -> "cincizeci de octombrie") and now and then invents or drops a
"not" ("între cele două ne mișcăm" -> "we do not move between the two"). These are the errors
that change a fact on screen, and both are cheap to check:

  numbers(text, lang)      the values (>= 2) said in a phrase, number words and digits alike
  to_digits(text, lang)    number words rewritten as digits (English before EN->RO, and the
                           source of a retry), so the translator copies them instead
  negated(text, lang)      whether a phrase says "not" (tag questions like "nu-i așa?" ignored)
  check(tr, src, out, ...) compare source and translation; on a mismatch translate once more
                           and keep the version that matches; returns the fields for the log
"""
from __future__ import annotations

import re
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from textnorm import fix_cedilla, fold_diacritics   # noqa: E402

# --- number words ----------------------------------------------------------------------------
# kind: "unit" 0-9, "teen" 10-19, "ten" 20-90, "scale" 100/1000/10**6
_EN = {w: (i, "unit") for i, w in enumerate("zero one two three four five six seven eight nine".split())}
_EN.update({w: (10 + i, "teen") for i, w in enumerate(
    "ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())})
_EN.update({w: (20 + 10 * i, "ten") for i, w in enumerate(
    "twenty thirty forty fifty sixty seventy eighty ninety".split())})
_EN.update({"hundred": (100, "scale"), "thousand": (1000, "scale"), "million": (10 ** 6, "scale")})
_EN_ORD = {w: (i + 1, "unit") for i, w in enumerate(
    "first second third fourth fifth sixth seventh eighth ninth".split())}
_EN_ORD.update({w: (10 + i, "teen") for i, w in enumerate(
    "tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
    "nineteenth".split())})
_EN_ORD.update({w: (20 + 10 * i, "ten") for i, w in enumerate(
    "twentieth thirtieth fortieth fiftieth sixtieth seventieth eightieth ninetieth".split())})
_EN_ORD.update({"hundredth": (100, "scale"), "thousandth": (1000, "scale")})

_RO = {"zero": (0, "unit"), "unu": (1, "unit"), "una": (1, "unit"), "doi": (2, "unit"),
       "două": (2, "unit"), "trei": (3, "unit"), "patru": (4, "unit"), "cinci": (5, "unit"),
       "șase": (6, "unit"), "șapte": (7, "unit"), "opt": (8, "unit"), "nouă": (9, "unit"),
       "zece": (10, "teen"), "sută": (100, "scale"), "sute": (100, "scale"),
       "mie": (1000, "scale"), "mii": (1000, "scale"), "milion": (10 ** 6, "scale"),
       "milioane": (10 ** 6, "scale")}
for _n, _forms in {11: "unsprezece unșpe", 12: "doisprezece douăsprezece doișpe douășpe",
                   13: "treisprezece treișpe", 14: "paisprezece patrusprezece paișpe",
                   15: "cincisprezece cinsprezece cinșpe", 16: "șaisprezece șasesprezece șaișpe",
                   17: "șaptesprezece șapteșpe", 18: "optsprezece optișpe optșpe",
                   19: "nouăsprezece nouășpe"}.items():
    _RO.update({f: (_n, "teen") for f in _forms.split()})
for _n, _forms in {20: "douăzeci", 30: "treizeci", 40: "patruzeci", 50: "cincizeci",
                   60: "șaizeci șasezeci", 70: "șaptezeci", 80: "optzeci", 90: "nouăzeci"}.items():
    _RO.update({f: (_n, "ten") for f in _forms.split()})
# recognised text sometimes lacks diacritics; "noua" stays out ("the new", not nine)
_RO.update({fold_diacritics(k): v for k, v in list(_RO.items()) if fold_diacritics(k) not in _RO
            and fold_diacritics(k) != "noua"})
# Romanian ordinals: "al paisprezecelea" / "a paisprezecea"; 2-10 feminine forms are irregular
_RO_ORD_FEM = {"doua": 2, "treia": 3, "patra": 4, "cincea": 5, "șasea": 6, "șaptea": 7, "opta": 8,
               "noua": 9, "zecea": 10}

_MONTHS = set("january february march april june july august september october november "
              "december".split())                     # "May" only capitalised (see _month)
_BOTH = {"en": {"both"}, "ro": {"amândoi", "amândouă", "ambii", "ambele", "amandoi", "amandoua"}}
_TOK = re.compile(r"\d+(?:[.,:]\d+)*|[^\W\d_]+(?:'[^\W\d_]+)?|[^\w\s]")
_ROFUNC = set("că ce și să de la ne ni nu a ai au am este e sunt era îi îl le lor pe în cu din "
              "pentru prin dar iar sau".split())


def _toks(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOK.finditer(fix_cedilla(text))]


def _ro_ordinal(w: str, prev: str) -> tuple[int, str] | None:
    if w.endswith("lea") and prev == "al":
        stem = w[:-3]
        for s in (stem, stem[:-1] if stem.endswith("u") else stem):   # optulea -> opt
            if s in _RO:
                return _RO[s]
    if prev == "a":
        if w in _RO_ORD_FEM:
            n = _RO_ORD_FEM[w]
            return (n, "teen" if n == 10 else "unit")
        if w.endswith("zecea"):                        # a paisprezecea, a douăzecea
            for s in (w[:-1], w[:-2] + "i"):
                if s in _RO and _RO[s][1] in ("teen", "ten"):
                    return _RO[s]
    return None


def _word(toks, i, lang) -> tuple[int, str, bool] | None:
    """(value, kind, is_ordinal) of token i, or None; the context decides the ambiguous ones."""
    raw = toks[i][0]
    w = raw.lower()
    prev = toks[i - 1][0].lower() if i else ""
    nxt = toks[i + 1][0].lower() if i + 1 < len(toks) else ""
    if w[0].isdigit():
        return 0, "digit", False                        # digits are read in _runs
    if lang == "en":
        if w in _EN:
            return (*_EN[w], False)
        if w in _EN_ORD:
            if w == "second" and prev != "the" and not _month(toks, i - 1):
                return None                             # "wait a second", "a second time"
            return (*_EN_ORD[w], True)
        if w in ("a", "an") and nxt in ("hundred", "thousand", "million"):
            return 1, "unit", False
        if w == "dozen":
            return 12, "teen", False
        return None
    if w in ("o", "un") and nxt in ("sută", "suta", "mie", "milion") and prev != "-":
        return 1, "unit", False                         # not "spune-o mie": "say it to me"
    ordn = _ro_ordinal(w, prev)
    if ordn:
        return (*ordn, True)
    if w == "nouă" and (not nxt or not nxt[0].isalpha() or nxt in _ROFUNC or prev in ("ne", "-")):
        return None                                     # "ne-a spus nouă că": "to us", not nine
    if w == "nouă" and prev in ("o", "una", "cea", "această", "aceasta", "acea", "mai", "foarte", "noua"):
        return None                                     # "o nouă viață": a new life, not nine
    if w in _RO:
        return (*_RO[w], False)
    return None


# "twenty thirty years ago", "fifteen twenty people": a range of counts, not a year
_EN_COUNTED = set("""years months weeks days hours minutes seconds times people men women children
kids families brothers sisters members pastors churches dollars miles pages chapters verses""".split())


def _a_range(toks, i: int, k: int, first: int, second: int) -> bool:
    """"twenty thirty years ago", "fifteen twenty people": two counts, the second the larger,
    before a counted noun. Not "in nineteen eighty times were hard" (a year after "in")."""
    nxt = toks[k + 1][0].lower() if k + 1 < len(toks) else ""
    prev = toks[i - 1][0].lower() if i else ""
    if nxt not in _EN_COUNTED or second <= first:
        return False
    # "in nineteen eighty", "since twenty ten": a year can only start 18, 19 or 20
    return not (first in (18, 19, 20) and prev in ("in", "since", "from", "by", "until"))


def _month(toks, i) -> bool:
    if i < 0:
        return False
    w = toks[i][0]
    return w.lower() in _MONTHS or w == "May"


def _runs(text: str, lang: str) -> list[dict]:
    """Number runs in reading order: {start, end, value, ordinal, year, after_month, words}."""
    toks, runs, i = _toks(text), [], 0
    while i < len(toks):
        first = _word(toks, i, lang)
        if first is None or (first[1] == "scale" and first[0] != 1):
            i += 1                                      # "sute de oameni": hundreds, not a number
            continue
        v, kind, ordinal = first
        total, cur, year, j = 0, v, False, i + 1
        if kind == "digit":
            # 2,000 / 2.000 are one number; 1:21 (a verse) and 3.5 are two
            tok, after = toks[i][0], _month(toks, i - 1)
            vals = ([int(re.sub(r"[.,]", "", tok))] if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", tok)
                    else [int(x) for x in re.split(r"[.,:]", tok)])
            runs += [{"start": toks[i][1], "end": toks[i][2], "value": x, "ordinal": False,
                      "year": False, "after_month": after, "words": False} for x in vals]
            i += 1
            continue
        end = toks[i][2]
        while j < len(toks) and not ordinal:
            k, conn = j, toks[j][0].lower()
            if conn in ("-", "and", "și", "si"):
                k = j + 1
            nxt = _word(toks, k, lang) if k < len(toks) else None
            if nxt is None or nxt[1] == "digit":
                if lang == "en" and conn == "oh" and kind in ("teen", "ten") and cur < 100:
                    after = _word(toks, j + 1, lang) if j + 1 < len(toks) else None
                    if after and after[1] == "unit":            # nineteen oh five
                        cur, year, kind, ordinal = cur * 100 + after[0], True, "unit", after[2]
                        end, j = toks[j + 1][2], j + 2
                        continue
                break
            nv, nk, no = nxt
            if k != j and not ((conn in ("-", "și", "si") and kind == "ten" and nk == "unit")
                               or (conn == "and" and kind == "scale" and nk != "scale")):
                break
            if nk == "scale":
                if nv == 100 and cur and cur < 100:
                    cur *= 100
                elif nv >= 1000 and (cur or total):
                    total, cur = total + (cur or 1) * nv, 0
                else:
                    break
            elif kind == "ten" and nk == "unit" and (k != j or lang == "en"):
                cur += nv                               # twenty-six / douăzeci și șase
            elif kind == "scale" or (kind != "scale" and total and cur == 0):
                cur += nv                               # a hundred and twenty / două mii doisprezece
            elif (lang == "en" and not year and kind in ("teen", "ten") and 10 <= cur < 100
                  and nk in ("teen", "ten") and k == j
                  and not _a_range(toks, i, k, cur, nv)):
                cur, year = cur * 100 + nv, True        # nineteen sixty / twenty twenty
            elif year and kind == "ten" and cur % 10 == 0 and nk == "unit":
                cur += nv                               # nineteen sixty three
            else:
                break
            kind, ordinal, end, j = nk, no, toks[k][2], k + 1
        runs.append({"start": toks[i][1], "end": end, "value": total + cur, "ordinal": ordinal,
                     "year": year, "after_month": _month(toks, i - 1), "words": True})
        i = j
    return runs


def numbers(text: str, lang: str) -> set[int]:
    """The values >= 2 said in a phrase: 1 is left out ("one of", "o casă", "someone")."""
    vals = {r["value"] for r in _runs(text, lang) if r["value"] >= 2}
    low = fix_cedilla(text).lower()
    if any(re.search(rf"\b{w}\b", low) for w in _BOTH[lang]):
        vals.add(2)                                     # "both" / "amândoi" = two
    return vals


def _ordinal_en(n: int) -> str:
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def to_digits(text: str, lang: str, min_value: int = 11) -> str:
    """Number words as digits: dates ("May nineteenth, nineteen sixty-three" -> "May 19, 1963"),
    years and numbers >= `min_value`. Small numbers stay words: the translator gets them right,
    and "the two of us" reads better than "the 2 of us"."""
    out, last = [], 0
    for r in _runs(text, lang):
        if not r["words"] or not (r["year"] or r["after_month"] or r["value"] >= min_value):
            continue
        if r["ordinal"] and not r["after_month"]:
            if lang != "en":
                continue                                # "al paisprezecelea": leave Romanian ordinals
            rep = _ordinal_en(r["value"])
        else:
            rep = str(r["value"])
        out += [text[last:r["start"]], rep]
        last = r["end"]
    return "".join(out) + text[last:]


# --- negations --------------------------------------------------------------------------------
_NEG = {"ro": {"nu", "n", "nici", "niciodată", "nimeni", "nimic", "nicăieri", "niciun", "nicio",
               "niciunul", "niciuna", "nicidecum"},
        "en": {"not", "no", "never", "none", "nobody", "nothing", "nowhere", "neither", "nor",
               "cannot", "unless", "noone", "dont", "doesnt", "didnt", "cant", "wont", "isnt",
               "arent", "wasnt", "werent", "havent", "hasnt", "hadnt", "couldnt", "wouldnt",
               "shouldnt", "aint"}}
_NEG["ro"] |= {fold_diacritics(w) for w in _NEG["ro"]}
_TAGS = {"ro": [re.compile(r"\bnu-i a[șs]a\b\s*\??", re.I),                    # nu-i așa (că)?
                re.compile(r"\bnu(?: e| este)? a[șs]a\s*\?", re.I),                # nu e așa?
                re.compile(r",\s*nu\s*\?", re.I),                              # e bine, nu?
                re.compile(r"\bnu\b(?=(?:\W+\w+){0,4}?\W+dec[âa]t\b)", re.I)],  # nu ... decât = only
         "en": [re.compile(r",?\s*\b(?:is|are|was|were|do|does|did|have|has|had|can|could|will|wo|"
                           r"would|should|must|ca|ai)n'?t\s+(?:it|he|she|they|we|you|i|there|that|"
                           r"this)\s*\?", re.I)]}                              # ..., isn't it?


def negated(text: str, lang: str) -> bool:
    """Does the phrase say "not"? Tag questions are not negations ("nu-i așa?", "isn't it?")."""
    t = fix_cedilla(text).replace("\u2019", "'")
    for pat in _TAGS[lang]:
        t = pat.sub(" ", t)
    for w in re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", t.lower()):
        if w in _NEG[lang] or (lang == "en" and w.endswith("n't")):
            return True
    return False


# --- the guard ---------------------------------------------------------------------------------
def mismatches(src: str, out: str, lang: str, tgt: str) -> dict:
    """What differs between a source phrase and its translation (empty: nothing)."""
    issues = {}
    a, b = numbers(src, lang), numbers(out, tgt)
    if a != b:
        issues["numbers"] = {"source": sorted(a), "translation": sorted(b)}
    na, nb = negated(src, lang), negated(out, tgt)
    if na != nb:
        issues["negation"] = {"source": na, "translation": nb}
    return issues


def _retranslate(tr, text: str, lang: str, tgt: str, gloss) -> str:
    """One more translation without the previous sentences (they may be the cause); the
    translator's context is left as it was."""
    if not hasattr(tr, "context"):
        return tr.translate(text, lang, tgt, gloss)
    saved, truncated = list(tr.context), getattr(tr, "last_truncated", False)
    try:
        return tr.translate(text, lang, tgt, gloss, use_context=False)
    finally:
        tr.context, tr.last_truncated = saved, truncated


def check(tr, src: str, out: str, lang: str, tgt: str, gloss=None) -> tuple[str, dict]:
    """Compare numbers and negations of `src` and its translation `out`. On a mismatch,
    translate once more (the number words of the source as digits, no context) and keep the
    version that matches; if neither does, keep the first. Returns (translation, log fields):
    {} when all agreed, else {"guard": {"issues", "kept": "first"|"retry", "first"|"retry"}}."""
    if not out.strip() or re.findall(r"\w+", src.lower()) == re.findall(r"\w+", out.lower()):
        return out, {}                                  # nothing, or the source echoed (a quote)
    issues = mismatches(src, out, lang, tgt)
    if not issues:
        return out, {}
    rec = {"issues": issues, "kept": "first"}
    text = to_digits(src, lang, min_value=2) if "numbers" in issues else src
    # a translator without context (NLLB) gives the same answer to the same text: no point asking
    if text == src and not hasattr(tr, "context"):
        rec["retry"] = None
        return out, {"guard": rec}
    try:
        again = (_retranslate(tr, text, lang, tgt, gloss) or "").strip()
    except Exception as e:                              # a failed retry never loses the caption
        rec["error"] = f"{type(e).__name__}: {e}"
        return out, {"guard": rec}
    if again and not mismatches(src, again, lang, tgt):
        rec.update(kept="retry", first=out)
        return again, {"guard": rec}
    rec["retry"] = again
    return out, {"guard": rec}
