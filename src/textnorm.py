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


def key_tokens(text: str, fold: bool = False) -> list[str]:
    out = [phon_key(t) for t in tokens(text)]
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


def _is_num(k: str) -> bool:
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
                if _is_num(kj):
                    j += 1
                elif kj in _SI and j + 1 < n and _is_num(keys[j + 1]):
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
