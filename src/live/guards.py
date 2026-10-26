"""Numbers and negations guard (translation idea 5).

The translator garbles number *words* in both directions ("cei paisprezece copii" -> "fifteen
children", "October fifteenth" -> "cincizeci de octombrie") and now and then invents or drops a
"not" ("între cele două ne mișcăm" -> "we do not move between the two"). These are the errors
that change a fact on screen, and both are cheap to check:

  numbers(text, lang)      the values (>= 2) said in a phrase, number words and digits alike
  to_digits(text, lang)    number words rewritten as digits (English before EN->RO, and the
                           source of a retry), so the translator copies them instead
  negated(text, lang)      whether a phrase says "not" (tag questions like "nu-i așa?" ignored)
  check(tr, src, out, ...) compare source and translation; on a mismatch translate again
                           (without context, then with a note naming the problem, then with
                           the fallback translator) and keep the first version that matches;
                           returns the fields for the log
  unglue(text)             "4567.000" (the recogniser's "patru, cinci, șase, șapte mii") -> "4.000-7.000"
  longer_numbers(ro, alt)  "doi" -> "12" when the other recogniser heard 12 (doisprezece ⊃ doi)
  odd_figures(text)        figures no speaker says ("1.271701 milion", 271.700.000.000)
  hide_odd(src, out)       such figures in a translation replaced by "[amount unclear]"
  notes(text, lang)        warnings for the translator before it starts: a bare "mai" (more,
                           again), "nu-i așa că …?" (a question, not a negation)

2026-10-25: four reversed meanings reached the screen because the retry repeated the first
answer ("Ne-ai mai dat o zi" -> "You have not given us", "ne mai trebuie fonduri" -> "we don't
need any more funds"), and two false alarms cost 9-10 s ("a doua oară" = 2 against "a second
time"; "nemântuite" with no "nu" against "not saved").
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
            if w == "second" and (prev not in ("the", "-") or nxt in ("time", "one")) and not _month(toks, i - 1):
                return None                             # "wait a second", "(for) the second time"
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
        if nxt in ("oară", "oara", "m", "l", "i", "ne", "te", "v", "s") and ordn[0] < 11:
            return None                                 # "a doua oară" / "a doua m-a întrebat": "again"
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
               "cannot", "unless", "lest", "noone", "dont", "doesnt", "didnt", "cant", "wont", "isnt",
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


# Romanian adjectives and participles negated by "ne-": "suflete nemântuite" is "souls that are
# not saved" in English, a "not" with no "nu". The recogniser also splits them ("ne mântuite").
_NE_STEMS = ("mantui", "credincio", "pocai", "ascultat", "ascultato", "cunoscu", "vazu", "putincio",
             "drept", "fericit", "impac", "iertat", "botezat", "nascut", "facut", "spus", "stiut",
             "lucrat", "atins", "intors", "convertit", "multumit", "maritat", "casatorit", "dorit")
# the attached form ("nemântuite"), or the recogniser's split one with a participle/adjective
# ending ("ne mântuite") and not after "să" ("să ne mântuiască", "Dumnezeu ne mântuiește" are
# the pronoun "us": a "not" in their translation is a real reversal)
_NE_ENDS = r"(?:i?t|i?ta|i?te|i?ti|at|ata|ate|ati|ut|uta|ute|uti|os|oasa|oase|osi)"   # right after the stem
_NE_RE = re.compile(r"\bne(?:" + "|".join(_NE_STEMS) + r")\w*|(?<!\bsa )\bne\s+(?:"
                    + "|".join(_NE_STEMS) + r")" + _NE_ENDS + r"\b", re.I)


def ambiguous_negation(text: str, lang: str) -> bool:
    """A Romanian "ne-" word: English may say it with "not" or with "un-"/"in-", so a negation
    mismatch on this phrase proves nothing."""
    return lang == "ro" and bool(_NE_RE.search(fold_diacritics(fix_cedilla(text).lower())))


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
    if na != nb and not ambiguous_negation(src if lang == "ro" else out, "ro"):
        issues["negation"] = {"source": na, "translation": nb}
    return issues


# --- warnings for the translator ---------------------------------------------------------------
_MAI = re.compile(r"\bmai\b", re.I)
_MAI_NEG = re.compile(r"\b(?:nu|n|nici|niciodat[ăa])\b[\s-]*(?:\w+[\s-]+){0,2}?mai\b", re.I)
_NUI_ASA = re.compile(r"\bnu-i a[șs]a\s+c[ăa]\b|\bnu\s+e(?:ste)?\s+a[șs]a\s+c[ăa]\b", re.I)


def notes(text: str, lang: str) -> list[str]:
    """Short warnings that go into the translator's prompt for this piece (RO→EN): Gemma read a
    bare "mai" as "nu mai" (no longer) twice on 2026-10-25, and "nu-i așa că …?" as a negation."""
    if lang != "ro":
        return []
    t = fix_cedilla(text)
    out = []
    if _MAI.search(t) and not _MAI_NEG.search(t) and not negated(t, "ro"):
        out.append("«mai» here means more / again / another / still. There is no negation in this "
                   "piece: do not write not, no longer or any more.")
    if _NUI_ASA.search(t):
        out.append("«nu-i așa că …?» means «isn't it true that …?»: keep it a question, it is not a "
                   "negation.")
    return out


def _retranslate(tr, text: str, lang: str, tgt: str, gloss, note: str = "") -> str:
    """One more translation without the previous sentences (they may be the cause); the
    translator's context is left as it was. `note` goes into the prompt when the translator
    takes one (Gemma)."""
    kw = {"note": note} if note and _takes_note(tr, lang, tgt) else {}
    if not hasattr(tr, "context"):
        return tr.translate(text, lang, tgt, gloss, **kw)
    saved, truncated = list(tr.context), getattr(tr, "last_truncated", False)
    try:
        return tr.translate(text, lang, tgt, gloss, use_context=False, **kw)
    finally:
        tr.context, tr.last_truncated = saved, truncated


def _takes_note(tr, lang: str, tgt: str) -> bool:
    member = getattr(tr, "trs", {}).get((lang, tgt), tr)    # ByDirection: the member that would run
    return bool(getattr(member, "takes_note", False))


def _note(issues: dict, src: str, lang: str) -> str:
    """What to tell the translator on the second retry."""
    parts = []
    neg = issues.get("negation")
    if neg is not None:
        parts.append("The source has NO negation: the translation must not contain not, no, never "
                     "or no longer." if not neg["source"] else
                     "The source IS negative: keep the negation (not / no / never) in the translation.")
    if "numbers" in issues:
        want = issues["numbers"]["source"]
        parts.append("Numbers in the source: " + (", ".join(map(str, want)) if want else "none")
                     + ". Write exactly these, as digits.")
    return " ".join(parts)


def _invented_not(issues: dict) -> bool:
    """The translation says "not" and the source doesn't: the reversal Gemma makes ("Ne-ai mai
    dat o zi" -> "You have not given us", 2026-10-25)."""
    neg = issues.get("negation")
    return bool(neg) and not neg["source"] and neg["translation"]


def _fallback_may_fix(issues: dict) -> bool:
    """NLLB is asked only for numbers or an invented "not". A "not" in the source that the
    translation lacks is most often the recogniser's stray «nu» («Vă nu salută») or a "lest" said
    otherwise; NLLB put it back as "It does not greet you" and "Holy shit, let's not…" (bench on
    the corrected sermons, 2026-10-26), so Gemma's answer stays."""
    return "numbers" in issues or _invented_not(issues)


def check(tr, src: str, out: str, lang: str, tgt: str, gloss=None, fallback=None) -> tuple[str, dict]:
    """Compare numbers and negations of `src` and its translation `out`. On a mismatch,
    translate again: (1) without context, number words as digits; (2) the same with a note
    naming the problem (Gemma); (3) with `fallback` (NLLB: literal), only for numbers or a
    "not" the translation invented. The first version that matches is kept. If none does, an
    invented "not" takes the fallback's version (never the reversed meaning of the first),
    anything else the first. Returns (translation, log fields): {} when all agreed, else {"guard": {"issues",
    "kept": "first"|"retry"|"note"|"fallback", "first"|"retry"|"tries"}}."""
    if not out.strip() or re.findall(r"\w+", src.lower()) == re.findall(r"\w+", out.lower()):
        return out, {}                                  # nothing, or the source echoed (a quote)
    issues = mismatches(src, out, lang, tgt)
    if not issues:
        return out, {}
    rec = {"issues": issues, "kept": "first"}
    text = to_digits(src, lang, min_value=2) if "numbers" in issues else src
    tries = {}

    def attempt(name, fn):
        try:
            again = (fn() or "").strip()
        except Exception as e:                          # a failed retry never loses the caption
            rec.setdefault("errors", {})[name] = f"{type(e).__name__}: {e}"
            rec.setdefault("error", rec["errors"][name])
            return None
        tries[name] = again
        return again if again and not mismatches(src, again, lang, tgt) else None

    # a translator without context (NLLB) gives the same answer to the same text: no point asking
    plain = not (text == src and not hasattr(tr, "context"))
    got = attempt("retry", lambda: _retranslate(tr, text, lang, tgt, gloss)) if plain else None
    if got is None and "negation" in issues and _takes_note(tr, lang, tgt):   # numbers alone: often a false alarm
        got = attempt("note", lambda: _retranslate(tr, text, lang, tgt, gloss, note=_note(issues, src, lang)))
    if got is None and fallback is not None and fallback is not getattr(tr, "trs", {}).get((lang, tgt), tr) \
            and _fallback_may_fix(issues):
        got = attempt("fallback", lambda: fallback.translate(text, lang, tgt, gloss))
    if got is not None:
        name = next(k for k, v in tries.items() if v == got)
        rec.update(kept=name, first=out)
        if len(tries) > 1:
            rec["tries"] = tries
        return got, {"guard": rec}
    if _invented_not(issues) and tries.get("fallback") and \
            "negation" not in mismatches(src, tries["fallback"], lang, tgt):
        rec.update(kept="fallback", first=out, tries=tries, unresolved=True)
        return tries["fallback"], {"guard": rec}
    if not plain:
        rec["retry"] = None
    elif len(tries) == 1:
        rec["retry"] = tries.get("retry")
    else:
        rec["tries"] = tries
    return out, {"guard": rec}


# --- «ne» glued to the next word after a hyphen ---------------------------------------------------
_GLUED_NE = re.compile(r"(?<=[^\W\d_])-ne([a-zăâîșț]{3,})\b", re.I)


def split_ne(text: str, known: set[str] = frozenset()) -> str:
    """«mărește-ne credința» (increase our faith), written by the recogniser as «mărește-necredință»
    (increase our unbelief): after a verb's hyphen «ne» is the pronoun «us», never the negating
    prefix, so the rest goes back to its own word when it is a known word (folded word list) and
    the whole is not («ducă-ne» etc. stay). The old prompt fixed it by luck; since idea 27 Gemma
    translates what it is given (bench on the corrected sermons, 2026-10-26)."""
    def fix(m):
        rest = m.group(1)
        if known and fold_diacritics(rest.lower()) not in known:
            return m.group(0)
        return "-ne " + rest
    return _GLUED_NE.sub(fix, text)


# --- numbers the recogniser mangles ------------------------------------------------------------
_GLUED = re.compile(r"\b(\d{4,6})\.000\b|\b(\d{4,6})(?=\s+(?:de\s+)?mii\b)")   # 123.000 is a real amount


def _ascending(digits: str) -> tuple[int, int] | None:
    d = [int(c) for c in digits if c != "0"]
    if len(d) >= 3 and all(b == a + 1 for a, b in zip(d, d[1:])) and len(digits) - len(d) <= 2:
        return d[0], d[-1]
    return None


def unglue(text: str) -> str:
    """A spoken range written as one number: "vreo patru, cinci, șase, șapte mii" came out as
    "4567.000", "cinci, șase, șapte mii" as "50067.000" (2026-10-25). Ascending single digits
    before thousands become a range: "4.000-7.000"."""
    def fix(m):
        digits = m.group(1) or m.group(2)
        r = _ascending(digits)
        if r is None:
            return m.group(0)
        return f"{r[0]}.000-{r[1]}.000" if m.group(1) else f"{r[0]}-{r[1]}"
    return _GLUED.sub(fix, text)


# small number words that are the start of a bigger one: doi ⊂ doisprezece / douăzeci
_GROWS = {2: (12, 20), 3: (13, 30), 4: (14, 40), 5: (15, 50), 6: (16, 60), 7: (17, 70), 8: (18, 80),
          9: (19, 90)}
_RO_UNIT_WORDS = {v: [w for w, (n, k) in _RO.items() if n == v and k == "unit"] for v in _GROWS}


def longer_numbers(ro: str, alt: str) -> tuple[str, list]:
    """The Romanian recogniser heard "Iacov, tatăl celor doi fii" where the other recogniser of
    the same audio heard "celor 12 fii" (doisprezece): a number word that is the beginning of
    the other transcript's number is replaced by it. Returns (text, [(word, value)])."""
    if not alt or not ro:
        return ro, []
    have, other = numbers(ro, "ro"), numbers(alt, "ro") | {int(x) for x in re.findall(r"\b\d{1,2}\b", alt)}
    fixes = []
    for v, bigger in _GROWS.items():
        if v not in have or v in other:                 # the other recogniser heard it too: right
            continue
        for big in bigger:
            if big in other and big not in have:
                for w in _RO_UNIT_WORDS[v]:
                    pat = re.compile(rf"\b{w}\b(?!\s+(?:sute|mii|milioane)\b)", re.I)
                    if pat.search(ro):
                        ro = pat.sub(str(big), ro, count=1)
                        fixes.append((w, big))
                        break
                break
    return ro, fixes


_ODD = re.compile(r"\b\d{1,3}[.,]\d{4,}\b|\b\d{1,3}(?:[.,]\d{3}){3,}\b|\b\d{10,}\b")
_ODD_OUT = re.compile(r"\b\d{1,3}[.,]\d{4,}(?:\s+(?:million|billion|milion|milioane))?\b"
                      r"|\b\d{1,3}(?:[.,]\d{3}){2,}\b|\b\d{7,}\b")


def odd_figures(text: str) -> bool:
    """A figure no speaker says: a decimal with 4+ places ("1.271701 milion") or 10+ digits."""
    return bool(_ODD.search(text or ""))


def hide_odd(src: str, out: str) -> str:
    """When the source has a mangled figure, the translation's big or odd figures are replaced
    by "[amount unclear]": a church loan balance came out as "1.271701 million. 271,700,000."."""
    if not odd_figures(src):
        return out
    return _ODD_OUT.sub("[amount unclear]", out)
