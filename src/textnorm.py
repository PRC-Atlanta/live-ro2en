"""Romanian text normalisation: Moldovan Cyrillic -> Latin, phonetic scoring key,
diacritic folding, spoken-header stripping."""
from __future__ import annotations

import re
import unicodedata

# --- Moldovan Cyrillic -> Latin -------------------------------------------------

_SIMPLE = {
    "а": "a", "б": "b", "в": "v", "д": "d", "е": "e", "ж": "j", "з": "z", "и": "i",
    "й": "i", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ț", "ш": "ș", "щ": "șt", "ь": "i",
    "э": "ă", "ю": "iu", "ъ": "", "ё": "io",
}
_CYR_VOWELS = set("аеиоуыэюяё")
_FRONT = set("еиь")


def _translit_word(w: str) -> str:
    out = []
    n = len(w)
    for i, ch in enumerate(w):
        lo = ch.lower()
        nxt = w[i + 1].lower() if i + 1 < n else ""
        prev = w[i - 1].lower() if i > 0 else ""
        if lo == "к":
            s = "ch" if nxt in _FRONT else "c"
        elif lo == "г":
            s = "gh" if nxt in _FRONT else "g"
        elif lo in ("ч", "ӂ"):
            base = "c" if lo == "ч" else "g"
            if nxt in _FRONT:
                s = base
            elif nxt in ("а", "я"):
                s = base + "e"          # ча -> cea, ӂа -> gea
            elif nxt == "ю":
                s = base                # чю -> c + 'iu' from ю
            elif nxt in ("о", "у"):
                s = base + "i"          # чо -> cio, чу -> ciu
            else:
                s = base + "i"          # final / before consonant: чинч -> cinci
            if nxt == "я":
                s = base                # чя -> c + 'ea' from я below
        elif lo == "я":
            if prev == "и":
                s = "a"                 # ия -> ia (credincioșia), not "iia"
            else:
                s = "ia" if (i == 0 or prev in _CYR_VOWELS or prev == "ь") else "ea"
            if prev in ("ч", "ӂ"):
                s = "ea"
        elif lo == "ы":
            s = "î" if (i == 0 or i == n - 1 or not w[i + 1].isalpha()) else "â"
        elif lo in _SIMPLE:
            s = _SIMPLE[lo]
        else:
            s = ch
            out.append(s)
            continue
        if ch.isupper() and s:
            s = s[0].upper() + s[1:]
        out.append(s)
    return "".join(out)


_CYR_RE = re.compile(r"[Ѐ-ӿ]+")


def cyr_to_latin(text: str) -> str:
    """Transliterate Moldovan Cyrillic to Latin Romanian (best effort; ambiguous
    spellings such as ea/ia and â/î are neutralised later by `phon_key`)."""
    return _CYR_RE.sub(lambda m: _translit_word(m.group(0)), text)


# --- Latin normalisation ---------------------------------------------------------

_CEDILLA = str.maketrans({"ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț"})
_FOLD = str.maketrans({"ă": "a", "â": "a", "î": "i", "ș": "s", "ț": "t"})
_TOKEN_RE = re.compile(r"[0-9a-zăâîșț]+")


def fix_cedilla(text: str) -> str:
    return unicodedata.normalize("NFC", text).translate(_CEDILLA)


def tokens(text: str) -> list[str]:
    """Lower-case word tokens; hyphens and apostrophes split words (într-o -> într o)."""
    return _TOKEN_RE.findall(fix_cedilla(text).lower())


def fold_diacritics(word: str) -> str:
    return word.translate(_FOLD)


_OLD_FORMS = {"sînt": "sunt", "sîntem": "suntem", "sînteți": "sunteți", "sîntează": "suntează",
              # Orthodox / older spelling of the name; same word for scoring and highlights
              "iisus": "isus", "iisuse": "isuse", "iisusului": "isusului",
              "filadelfia": "philadelphia", "filadelfiei": "philadelphiei"}


def phon_key(word: str) -> str:
    """Phonetic key for one lower-case token. Merges â/î, ea/ia, ie/e, ch/c, gh/g and
    word-final -ci/-gi, so orthographic/script variants compare equal. Keeps ă/ș/ț."""
    w = _OLD_FORMS.get(word, word)
    w = w.replace("â", "î")
    w = w.replace("ea", "ia").replace("ie", "e")
    w = w.replace("ch", "c").replace("gh", "g")
    w = re.sub(r"([cg])i$", r"\1", w)
    return w


# --- digits -> Romanian words (scoring only) --------------------------------------

_UNITS = "zero unu doi trei patru cinci șase șapte opt nouă".split()
_TEENS = ("zece unsprezece doisprezece treisprezece paisprezece cincisprezece șaisprezece "
          "șaptesprezece optsprezece nouăsprezece").split()
_TENS = {2: "douăzeci", 3: "treizeci", 4: "patruzeci", 5: "cincizeci", 6: "șaizeci",
         7: "șaptezeci", 8: "optzeci", 9: "nouăzeci"}


def num_words(n: int) -> str:
    """0-999999 as spoken Romanian, masculine/counting forms (12 -> doisprezece, 21 -> douăzeci
    și unu, 200 -> două sute, 1260 -> o mie două sute șaizeci). Feminine agreement is not
    guessed here; key_tokens(numbers=True) folds both genders to one form instead."""
    if not 0 <= n <= 999999:
        raise ValueError(n)
    if n >= 1000:
        th, r = divmod(n, 1000)
        head = "o mie" if th == 1 else ("două" if th == 2 else num_words(th)) + (" de mii" if th % 100 >= 20 or th >= 100 and th % 100 == 0 else " mii")
        return head + (" " + num_words(r) if r else "")
    h, r = divmod(n, 100)
    if h:
        head = "o sută" if h == 1 else ("două" if h == 2 else _UNITS[h]) + " sute"
        return head + (" " + num_words(r) if r else "")
    if r < 10:
        return _UNITS[r]
    if r < 20:
        return _TEENS[r - 10]
    t, u = divmod(r, 10)
    return _TENS[t] + (f" și {_UNITS[u]}" if u else "")


# one form per number word, applied to both sides: '42 de luni' is spoken 'patruzeci și două'
_GENDER = {"două": "doi", "douăsprezece": "doisprezece", "una": "unu"}


def expand_numbers(toks: list[str]) -> list[str]:
    """Spell out standalone numbers up to 6 digits (no leading zero) so '12' and 'doisprezece'
    compare equal, and fold feminine number forms (două -> doi) on both sides.
    Mixed tokens ('2a') are left alone."""
    out: list[str] = []
    for t in toks:
        if t.isdigit() and len(t) <= 6 and (t == "0" or t[0] != "0"):
            out += num_words(int(t)).split()
        else:
            out.append(t)
    return [_GENDER.get(t, t) for t in out]


def key_tokens(text: str, fold: bool = False, numbers: bool = False) -> list[str]:
    """Phonetic-key tokens. numbers=True spells out digits first (see expand_numbers)."""
    toks = tokens(text)
    out = [phon_key(t) for t in (expand_numbers(toks) if numbers else toks)]
    if fold:
        out = [fold_diacritics(t) for t in out]
    return out


# --- spoken header stripping -----------------------------------------------------

_NUM_WORDS = """unu una un doi două trei patru cinci șase șapte opt nouă zece unsprezece
doisprezece douăsprezece treisprezece paisprezece patrusprezece cincisprezece
șaisprezece șaptesprezece optsprezece nouăsprezece douăzeci treizeci patruzeci
cincizeci șaizeci șaptezeci optzeci nouăzeci o sută suta sute zecea întâi""".split()
_NUM_KEYS = {phon_key(w) for w in _NUM_WORDS} | {fold_diacritics(phon_key(w)) for w in _NUM_WORDS}
_HEADER_KEYS = {phon_key(w) for w in ("capitolul", "psalmul", "capitol")}
_SI = {phon_key("și"), "si"}
# also articles ('o femeie', 'un om'): a number only when another number word follows ('o sută')
_AMBIG_KEYS = {phon_key(w) for w in ("o", "un", "una")}


def _is_num(k: str, nxt: str | None = None) -> bool:
    if k in _AMBIG_KEYS:
        return nxt is not None and nxt not in _AMBIG_KEYS and _is_num(nxt)
    return k.isdigit() or k in _NUM_KEYS


def strip_headers(keys: list[str]) -> list[str]:
    """Remove 'Cartea X' at the very start and every 'Capitolul/Psalmul <number>'."""
    return [keys[i] for i in strip_headers_idx(keys)]


def strip_headers_idx(keys: list[str]) -> list[int]:
    """Indices of the tokens kept by strip_headers."""
    out: list[int] = []
    i = 0
    if len(keys) >= 2 and fold_diacritics(keys[0]) == fold_diacritics(phon_key("cartea")):
        i = 2
    n = len(keys)
    while i < n:
        k = fold_diacritics(keys[i])
        if keys[i] in _HEADER_KEYS or k in {fold_diacritics(h) for h in _HEADER_KEYS}:
            j = i + 1
            while j < n and j - i <= 6:
                kj = keys[j]
                nxt = keys[j + 1] if j + 1 < n else None
                if _is_num(kj, nxt):
                    j += 1
                elif kj in _SI and nxt is not None and _is_num(nxt, keys[j + 2] if j + 2 < n else None):
                    j += 1
                else:
                    break
            if j > i + 1:
                i = j
                continue
        out.append(i)
        i += 1
    return out


def diacritic_stats(hyp_words: list[str]) -> dict:
    """Counts of diacritic characters and cedilla (wrong) forms in raw hypothesis text."""
    text = " ".join(hyp_words)
    return {
        "comma_below": sum(text.count(c) for c in "șțȘȚ"),
        "cedilla": sum(text.count(c) for c in "şţŞŢ"),
        "breve_circ": sum(text.count(c) for c in "ăâîĂÂÎ"),
    }


def load_spellings(path=None) -> dict[str, str]:
    """Preferred spellings from config.yaml (`spellings:` variant -> preferred)."""
    import pathlib
    import yaml
    path = path or pathlib.Path(__file__).resolve().parents[1] / "config.yaml"
    return dict(yaml.safe_load(open(path)).get("spellings") or {})


def apply_spellings(text: str, spellings: dict[str, str]) -> str:
    """Replace whole-word variants with the preferred form, keeping a leading capital
    (Filadelfia -> Philadelphia, filadelfia -> philadelphia)."""
    for a, b in spellings.items():
        def sub(m, b=b):
            w = m.group(0)
            return b[0].upper() + b[1:] if w[0].isupper() else b[0].lower() + b[1:]
        text = re.sub(rf"(?<![\w-]){re.escape(a)}(?![\w-])", sub, text, flags=re.IGNORECASE)
    return text
