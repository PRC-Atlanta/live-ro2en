"""Offline translators (RO<->EN) shared by the Phase 2 benchmark and the live app.

  NLLBTranslator   CTranslate2 int8 NLLB-200 (models/ct2/<name> or the HF cache), CPU
  MLXLLMTranslator local LLM via mlx-lm, glossary injected per sentence
Glossary: glossary.csv (ro,en,note). `Glossary.relevant(src, src_lang)` returns only the
entries whose source term occurs in the sentence, so prompts stay short; the same for the
idioms (data/idioms.csv) and the names kept as written (names.py)."""
from __future__ import annotations

import csv
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))      # guards
from textnorm import fix_cedilla, fold_diacritics, phon_key, tokens   # noqa: E402
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import gate   # noqa: E402
import names as namelist   # noqa: E402
import scripture   # noqa: E402
import verses   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
NLLB_CODES = {"ro": "ron_Latn", "en": "eng_Latn"}
# Post-edits for the target language (whole words). NLLB writes the Orthodox "Iisus";
# Romanian evangelical usage (and Cornilescu) is "Isus".
POSTEDIT = {"ro": {"Iisus": "Isus", "Iisuse": "Isuse", "lui Iisus": "lui Isus"}, "en": {}}
# Archaic English the translator falls back on for biblical register: modern forms only
# (case kept). Verb endings like "knoweth" are left to the prompt.
MODERN_EN = {"thee": "you", "thou": "you", "ye": "you", "thy": "your", "thine": "your",
             "thyself": "yourself", "hath": "has", "hast": "have", "doth": "does", "dost": "do",
             "art thou": "are you", "thou art": "you are", "shalt": "shall",
             "unto": "to", "saith": "says", "spake": "spoke"}
_MODERN_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, MODERN_EN), key=len, reverse=True)) + r")\b",
                        re.IGNORECASE)


def modernize(text: str) -> str:
    def sub(m):
        w = m.group(0)
        new = MODERN_EN[w.lower()]
        return new[0].upper() + new[1:] if w[0].isupper() else new
    return _MODERN_RE.sub(sub, text)


def postedit(text: str, tgt: str) -> str:
    for a, b in POSTEDIT.get(tgt, {}).items():
        text = re.sub(rf"\b{re.escape(a)}\b", b, text)
    return fix_cedilla(text) if tgt == "ro" else modernize(text)


# Chat/control tokens: Gemma 4 (<image|>, <|channel>, <turn|>, <|"|>), Gemma 2/3 (<start_of_turn>)
_CTRL_RE = re.compile(r"(?:<\|[^<>\s]{1,24}>|<[^<>\s|]{1,24}\|>|<(?:start|end)_of_[a-z_]+>"
                      r"|<(?:eos|bos|pad|unk|mask)>)+")
# Gemma's notes to the reader: "*(Note: The source text ...)*", "Note: ...", "Translation: ..."
_NOTE_RE = re.compile(r"[*_\s]*[(\[][*_\s]*(?:note|notă|nota|n\.b\.)\b.*", re.I | re.S)
_LABEL_RE = re.compile(r"(?<![^\W\d_])(note|notă|explanation|explicație|translation|traducere)"
                       r"[*_]*\s*:[*_]*", re.I)
# short words: a token before one of these stood where a space belongs, not inside a word
_SHORT_WORDS = set("a o e i ai al am au ca că ce cu da de din el ea ei eu fi în la le lor lui mai "
                   "ne nu pe sa să se și ta tu un va vă zi bun rău tot cum cât sau are era fie "
                   "an and are as at be but by do for go he her him his if in is it me my no "
                   "not of on or our out so the to up us was we who you all can god".split())
# two consonants that start Romanian or English words; any other pair starts a word piece ("ctori")
_ONSETS = set("bl br ch cl cr dr fl fr gl gr kn ph pl pr ps sc sh sk sl sm sn sp st sv sw th tr "
              "tw wh wr șt șc șp șm șn șl șv".split())


def _ctrl_sub(m: re.Match) -> str:
    """A control token stands where a space or a word piece belongs ("în<image|>eternitate",
    "năde<image|>jde"): between words keep a space, inside a word join the pieces. Inside means
    a right-hand piece of 1-3 letters that is not a word ("jde"), a 1-2 letter left piece that
    is not a word, or a right-hand piece starting with two consonants no word starts with."""
    s = m.string
    left = re.search(r"[^\W\d_]*$", s[:m.start()]).group(0)
    right = re.match(r"[^\W\d_]*", s[m.end():]).group(0)
    before = s[m.start() - 1] if m.start() else ""
    if not left or not right:
        return " " if before in ",.;:!?)" and right else ""
    if right[0].isupper():
        return " "
    r2 = right[:2].lower()
    if (len(right) <= 3 and right.lower() not in _SHORT_WORDS) or \
            (len(left) <= 2 and left.lower() not in _SHORT_WORDS) or \
            (len(r2) == 2 and not set(r2) & set("aeiouăâîy") and r2 not in _ONSETS):
        return ""
    return " "


def clean_output(out: str) -> str:
    """What reaches the screen from an LLM: the answer, without thinking, control tokens or
    notes to the reader. Gemma 4 sometimes reopened its thinking channel mid-answer
    ("...ale vo<image|><|channel>thought\\n<channel|>în urma ...") and then gave the whole
    answer again: the text after the last <channel|> is that answer."""
    out = re.sub(r"<think>.*?</think>", "", out, flags=re.S)
    if "<channel|>" in out:
        after = out.rsplit("<channel|>", 1)[1]
        out = after if re.search(r"[^\W\d_]", _CTRL_RE.sub("", after)) else out.split("<|channel>")[0]
    elif "<|channel>" in out:                           # a thought that never closed
        out = out.split("<|channel>")[0]
    out = _NOTE_RE.sub("", out)
    # a note: a label that opens the output, a line or a sentence, or is set apart ("*Note:",
    # "Note:" capitalised after an unfinished phrase), not "here is the explanation: God
    # loves us" inside a sentence
    m = next((m for m in _LABEL_RE.finditer(out)
              if m.group(1)[0].isupper() or re.search(r"[*_]\s*$", out[:m.start()])
              or re.search(r"(?:^|[.!?)\]\n\"”>])[\s(\[]*$", out[:m.start()])), None)
    if m:
        lead = not out[:m.start()].strip(" *_\"\n")
        if lead and m.group(1).lower() in ("translation", "traducere"):
            out = out[m.end():]                         # "Translation: ..." -> the translation
        else:
            out = out[:m.start()].rstrip(" *_([")       # a note, alone or after the translation
    out = _CTRL_RE.sub(_ctrl_sub, out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip().strip('"').strip()
    return out.split("\n\n")[0].strip()


def tiny(text: str) -> bool:
    """A 1-2 character fragment ("T."): with context, Gemma translated the context instead."""
    return sum(c.isalnum() for c in text) <= 2


def _fk(text: str) -> list[str]:
    return [fold_diacritics(phon_key(t)) for t in tokens(text)]


def _en_alts(en: str) -> list[list[str]]:
    """English renderings of a glossary cell: 'Passover / Easter' is two alternatives, and a
    leading article is optional ('the Holy Spirit' matches 'Holy Spirit')."""
    return [tokens(re.sub(r"^\s*the\s+", "", alt, flags=re.I)) for alt in en.split("/") if alt.strip()]


def read_idioms(path: pathlib.Path | str | None) -> list[tuple[list[str], dict]]:
    """Rows of data/idioms.csv (ro,en,match) as (match key, row): 'match' lists the spoken
    forms that bring the idiom into the prompt ('a găsi cu cale' comes in on 'cu cale')."""
    if not path or not pathlib.Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = [{"ro": r["ro"].strip(), "en": r["en"].strip(), "match": (r.get("match") or r["ro"])}
                for r in csv.DictReader(f) if (r.get("ro") or "").strip() and (r.get("en") or "").strip()]
    return [(_fk(m), r) for r in rows for m in r["match"].split("|") if m.strip()]


def read_glossary(path: pathlib.Path | str | None) -> list[dict]:
    """Rows of a glossary csv (ro,en,note); [] when there is no file."""
    if not path or not pathlib.Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return [{"ro": r["ro"].strip(), "en": r["en"].strip(), "note": (r.get("note") or "").strip()}
                for r in csv.DictReader(f) if (r.get("ro") or "").strip() and (r.get("en") or "").strip()]


class Glossary:
    """Three layers, the later winning for the same Romanian term: the bundled glossary.csv,
    the user's additions file, and today's names (session terms, set live with set_session).
    Readers never see a half-built index: each change swaps the whole index at once."""

    def __init__(self, path: pathlib.Path | str = ROOT / "glossary.csv", user: pathlib.Path | str | None = None,
                 idioms: pathlib.Path | str | None = ROOT / "data" / "idioms.csv"):
        self.base = read_glossary(path)
        self.user = read_glossary(user)
        self.session: list[dict] = []
        self._idioms = read_idioms(idioms)
        self._index()

    def _index(self):
        merged: dict[tuple, dict] = {}
        for later, layer in ((False, self.base), (True, self.user), (True, self.session)):
            for r in layer:
                k = tuple(_fk(r["ro"]))
                if later and k in merged and r["ro"].casefold() == r["en"].casefold():
                    continue        # a bare name ('Pavel', today's speaker) never undoes 'Pavel = Paul'
                merged.pop(k, None)                      # re-inserted last: a later layer wins
                merged[k] = r
        rows = list(merged.values())
        # names kept as written: a capitalised row that is the same in both languages (today's
        # people, the church, places), today's even where the bundled glossary has a Bible name
        keep = [r["ro"] for layer in (self.session, self.user, self.base) for r in layer
                if r["ro"][:1].isupper() and r["ro"].casefold() == r["en"].casefold()]
        self.rows, self._ro, self._en, self.names = (
            rows, [(_fk(r["ro"]), r) for r in rows],
            [(alt, r) for r in rows for alt in _en_alts(r["en"])
             if not r["note"].startswith("recogniser spelling")],
            list({fold_diacritics(n.casefold()): n for n in reversed(keep)}.values())[::-1])

    def load_user(self, path: pathlib.Path | str | None):
        """(Re)read the user's additions file."""
        self.user = read_glossary(path)
        self._index()

    def set_session(self, rows: list[dict]):
        """Today's names and terms ({ro, en}); replaces the previous session terms, live."""
        self.session = [{"ro": r["ro"].strip(), "en": r["en"].strip(), "note": r.get("note") or "session"}
                        for r in rows if r.get("ro", "").strip() and r.get("en", "").strip()]
        self._index()

    @staticmethod
    def _contains(seq: list[str], pat: list[str]) -> bool:
        n = len(pat)
        return n > 0 and any(seq[i:i + n] == pat for i in range(len(seq) - n + 1))

    def relevant(self, src: str, src_lang: str = "ro") -> list[dict]:
        if src_lang == "ro":
            seq = _fk(src)
            hits = [r for k, r in self._ro if self._contains(seq, k)]
        else:
            seq = tokens(src)
            hits = {}
            for k, r in self._en:           # one Romanian form per English term: the shortest
                if self._contains(seq, k):
                    e = tuple(k)
                    if e not in hits or len(r["ro"]) < len(hits[e]["ro"]):
                        hits[e] = r
            hits = list(hits.values())
        # prefer longer matches (Duhul Sfânt over Duhul)
        return sorted(hits, key=lambda r: -len(r["ro"]))

    def idioms(self, src: str, src_lang: str = "ro") -> list[dict]:
        """Romanian idioms said in the text (RO→EN only)."""
        if src_lang != "ro":
            return []
        seq = _fk(src)
        return list({id(r): r for k, r in self._idioms if self._contains(seq, k)}.values())

    def keep_names(self, src: str) -> list[str]:
        """The kept names mentioned in the text."""
        return namelist.mentioned(src, self.names)

    def adherence(self, src: str, out: str, src_lang: str = "ro") -> tuple[int, int]:
        """(n_terms_in_source, n_rendered_as_glossary_prefers)."""
        rel = self.relevant(src, src_lang)
        if src_lang == "ro":
            o = tokens(out)
            ok = sum(any(self._contains(o, alt) for alt in _en_alts(r["en"])) for r in rel)
        else:
            o = _fk(out)
            ok = sum(self._contains(o, _fk(r["ro"])) for r in rel)
        return len(rel), ok


def ct2_dir(name: str) -> pathlib.Path:
    """Where the CTranslate2 build of NLLB `name` is: the dev conversion in models/ct2/<name>,
    else the pinned church build in the Hugging Face cache (model_setup, S6)."""
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import model_setup
    return model_setup.ct2_dir(name, ROOT)


class NLLBTranslator:
    def __init__(self, name: str = "nllb-200-distilled-1.3B", device: str = "cpu",
                 compute_type: str = "int8", beam: int = 2, threads: int = 8):
        import ctranslate2
        import transformers
        self.name = name
        self.tr = ctranslate2.Translator(str(ct2_dir(name)), device=device,
                                         compute_type=compute_type, intra_threads=threads)
        self.tok = transformers.AutoTokenizer.from_pretrained(f"facebook/{name}")
        self.beam = beam
        self.last_truncated = False               # the last output hit the decoding limit

    def reset_context(self):
        """NLLB translates each clause on its own: there is no context to forget."""

    def translate(self, text: str, src: str = "ro", tgt: str = "en", glossary=None) -> str:
        self.tok.src_lang = NLLB_CODES[src]
        pieces = self.tok.convert_ids_to_tokens(self.tok.encode(text))
        res = self.tr.translate_batch([pieces], target_prefix=[[NLLB_CODES[tgt]]],
                                      beam_size=self.beam, max_decoding_length=256)
        self.last_truncated = len(res[0].hypotheses[0]) >= 256
        out = res[0].hypotheses[0][1:]
        out = self.tok.decode(self.tok.convert_tokens_to_ids(out), skip_special_tokens=True).strip()
        return postedit(out, tgt)


_PIECES = ("The speech comes in short pieces, often cut mid-sentence: if the previous piece ended "
           "mid-sentence, continue that sentence; never repeat what was already translated; if this "
           "piece stops mid-sentence, stop there too, never complete it with words that are not in it. ")
SYSTEM = {
    ("ro", "en"): ("You are a live interpreter for a Romanian evangelical church service. Translate the "
                   "Romanian speech into natural, faithful, modern English, as a preacher speaks today. "
                   + _PIECES +
                   "For Bible quotations use NKJV-style wording. Never use archaic English: no thee, "
                   "thou, thy, ye, hath, doth, unto, or -eth/-est endings. Capitalise You/Your/He/His/Him "
                   "only for God; for people use lower case (his footsteps, your father). Bible figures "
                   "keep their English Bible names; other people's names stay exactly as written. "
                   "Output only the English translation, nothing else."),
    ("en", "ro"): ("You are a live interpreter for a Romanian evangelical church service. Translate the "
                   "English speech into natural Romanian with correct diacritics (ă â î ș ț). "
                   + _PIECES +
                   "Write only Romanian words: never Spanish, Portuguese or Italian ones (unsprezece, "
                   "not onze; botezat, not batizat; zel, not zelo). Make every adjective, article, "
                   "numeral and possessive agree with its noun in gender, number and case (în aceste "
                   "ultime zile, cei paisprezece copii, tot sufletul, rugăciunile voastre, tatăl meu); "
                   "use the right pronoun forms (îl iubea, l-a iubit, îi mulțumea lui Dumnezeu) and the "
                   "infinitive after poate (poate răsuna). Translate idioms by their meaning, not word "
                   "for word. Write dates, years and numbers above ten as digits (19 mai 1963). "
                   "For Bible quotations and terms use Cornilescu-style wording. Write Tu/Te/Tău/El/Lui "
                   "with a capital only for God. People's names stay exactly as written. Output only "
                   "the Romanian translation, nothing else."),
}
LANG = {"ro": "Romanian", "en": "English"}


class MLXLLMTranslator:
    def __init__(self, repo: str, max_tokens: int = 200, context_clauses: int = 2):
        from mlx_lm import load
        self.repo = repo
        self.model, self.tok = load(repo)
        self.max_tokens = max_tokens
        self.context: list[tuple[str, str, str]] = []    # (source, translation, source language)
        self.context_clauses = context_clauses
        self.last_truncated = False               # the last output hit max_tokens (cut off)
        self.reading = scripture.Reading()        # a Bible reference announced a few chunks back
        self.verses = verses.Matcher()            # a verse recognised by its words: the published text
        self.last_verse = None                    # the last piece's verse match (log record)

    def reset_context(self):
        """Forget the previous sentences (after Show, a profile switch or a new service)."""
        self.context = []
        if getattr(self, "reading", None) is not None:
            self.reading.reset()
        if getattr(self, "verses", None) is not None:
            self.verses.reset()

    def _prompt(self, text, src, tgt, glossary, hint: str = ""):
        lines = []
        if glossary is not None:
            keep = glossary.keep_names(text)
            kept = {fold_diacritics(n.casefold()) for n in keep}
            rel = glossary.relevant(text, src)
            rel += glossary.idioms(text, src)
            pairs = []
            for r in rel[:14]:
                if r["ro"].casefold() == r["en"].casefold() and fold_diacritics(r["ro"].casefold()) in kept:
                    continue                              # a name: on the Names line
                a, b = (r["ro"], r["en"]) if src == "ro" else (r["en"], r["ro"])
                if src == "ro" and fold_diacritics(r["ro"].casefold()) in kept:
                    b += " (only for the Bible figure)"   # 'Pavel = Paul' with a Pavel here today
                pairs.append((a, b + (f" ({r['note']})" if "=" in r.get("note", "") else "")))
            if pairs:
                lines.append("Glossary (use these renderings):\n" + "\n".join(f"- {a} = {b}" for a, b in pairs))
            if keep:
                lines.append("Names (keep exactly as written): " + ", ".join(keep))
        if hint:
            lines.append("Scripture: " + hint)
        n = max(self.context_clauses, 0)
        ctx = [c for c in self.context[-n:] if c[1].strip()] if n else []
        if ctx:
            lines.append("Previous pieces (already translated, context only):\n" +
                         "\n".join(f"{LANG[l]}: {s}\n{LANG['en' if l == 'ro' else 'ro']}: {t}" for s, t, l in ctx))
        lines.append(f"Translate the next piece into {LANG[tgt]}:\n{text}")
        msgs = [{"role": "system", "content": SYSTEM[(src, tgt)]},
                {"role": "user", "content": "\n\n".join(lines)}]
        try:
            return self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                                enable_thinking=False)
        except Exception:      # templates without a system role
            msgs = [{"role": "user", "content": SYSTEM[(src, tgt)] + "\n\n" + msgs[1]["content"]}]
            return self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)

    def translate(self, text: str, src: str = "ro", tgt: str = "en", glossary=None,
                  use_context: bool = False) -> str:
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler
        import guards
        if not use_context:
            self.context = []
        elif self.context and self.context[-1][2] != src:
            self.context = self.context[-2:]      # a language switch keeps only the last two pieces
        if src == "en":
            text = guards.to_digits(text, "en")   # "October fifteenth" came back as "fifty"
            if glossary is not None:
                text = namelist.map_back(text, glossary.names, gate.lexicon()["en"])   # Dragosh → Dragoș, Souchava → Suceava
        reading = getattr(self, "reading", None)
        hint = ""
        if use_context and reading is not None:
            hint = reading.hint(reading.update(text), src, tgt)
        verse = self.verses.match(text, src, tgt, reading.ref) \
            if use_context and reading is not None and getattr(self, "verses", None) is not None else None
        if verse is not None:
            return self._verse(verse, text, src, tgt, glossary)
        self.last_verse = None
        small, ctx = tiny(text), self.context
        if small:
            self.context, hint = [], ""          # translated alone, the context kept for later
        try:
            out = generate(self.model, self.tok, prompt=self._prompt(text, src, tgt, glossary, hint),
                           max_tokens=self.max_tokens, sampler=make_sampler(temp=0.0), verbose=False,
                           logits_processors=self._suppress())
        finally:
            self.context = ctx
        self.last_truncated = len(self.tok.encode(out)) >= self.max_tokens - 1
        out = clean_output(out)
        if small and len(out.split()) > 3:
            out = ""                              # "T." -> two sentences: not a translation of it
        out = postedit(out, tgt)
        if use_context and not small:
            self.context = (self.context + [(text, out, src)])[-max(self.context_clauses, 1):]
        return out

    def _verse(self, verse, text: str, src: str, tgt: str, glossary) -> str:
        """A piece that is part of a verse: the published text, the words around it translated alone."""
        ctx = self.context
        around = [self.translate(t, src, tgt, glossary) if t else "" for t in (verse.before, verse.after)]
        out = " ".join(x for x in (around[0], verse.caption, around[1]) if x)
        self.context = (ctx + [(text, out, src)])[-max(self.context_clauses, 1):]
        self.last_truncated, self.last_verse = False, verse.log()
        return out

    def _suppress(self) -> list:
        """Logits processors that forbid the tokenizer's special tokens other than end-of-turn.
        Gemma 4's generation_config.json lists <image|> and <audio|> in `suppress_tokens`, but
        mlx-lm (0.32) reads only `eos_token_id` from it, so greedy decoding picked <image|>
        where a space or a word piece belonged ("năde<image|>jde"), and <|channel> reopened
        the thinking channel mid-answer. Nothing a translation needs is a special token."""
        if getattr(self, "_suppressors", None) is None:
            ids = banned_ids(self.tok)
            self._suppressors = [_ban_processor(ids)] if ids else []
        return self._suppressors


def banned_ids(tok) -> list[int]:
    """The special-token ids a translation must never contain: all of them except end-of-turn."""
    try:
        special = set(tok.all_special_ids)
    except Exception:                             # a tokenizer without special-token lists
        return []
    return sorted(special - set(getattr(tok, "eos_token_ids", None) or []))


def _ban_processor(ids: list[int]):
    import mlx.core as mx
    import numpy as np
    masks = {}

    def ban(tokens, logits):
        n = logits.shape[-1]
        if n not in masks:
            m = np.zeros(n, np.float32)
            m[[i for i in ids if i < n]] = -np.inf
            masks[n] = mx.array(m)
        return logits + masks[n].astype(logits.dtype)
    return ban


def make_translator(spec: str):
    """'nllb:<ct2 dir name>' or 'mlx:<hf repo>'."""
    kind, _, name = spec.partition(":")
    if kind == "nllb":
        return NLLBTranslator(name)
    if kind == "mlx":
        return MLXLLMTranslator(name)
    raise ValueError(spec)


# --- a translator per direction (idea 13) ---------------------------------------------------
DIRECTIONS = (("ro", "en"), ("en", "ro"))
WARMUP = {("ro", "en"): "Bună dimineața.", ("en", "ro"): "Good morning."}


def direction_specs(lc: dict) -> dict[tuple[str, str], str]:
    """The translator spec for each direction of profile settings `lc`: `translator`, and
    `translator_en_ro` for English → Romanian when set (Gemma's Romanian slips into Spanish
    and Portuguese words; NLLB's grammar is clean)."""
    return {("ro", "en"): lc["translator"], ("en", "ro"): lc.get("translator_en_ro") or lc["translator"]}


def translator_key(lc: dict) -> str:
    """One string for what `lc` translates with: the spec, or both when they differ. Two
    profiles with the same key share their loaded translator(s)."""
    s = direction_specs(lc)
    a, b = s[("ro", "en")], s[("en", "ro")]
    return a if a == b else f"{a} | en_ro={b}"


class ByDirection:
    """Two translators behind one: each phrase goes to its direction's. The pipeline holds
    it as its translator; the members are loaded, kept warm and kept as spares one by one
    (a member may also serve another profile)."""

    def __init__(self, trs: dict[tuple[str, str], object], specs: dict[tuple[str, str], str]):
        self.trs, self.specs = dict(trs), dict(specs)
        self.last = self.trs[("ro", "en")]       # the member that ran last (truncation, guard retry)
        self.used: dict[int, float] = {}         # id(member) -> when it last translated (keep-warm)
        # for a member without its own (NLLB): announced references, verses recognised by their
        # words, and the English pre-steps the Gemma member does itself (numbers, name spellings)
        self.reading = scripture.Reading()
        self.verses = verses.Matcher()
        self.last_verse = None

    def members(self) -> dict[str, object]:
        """spec -> translator, each loaded translator once."""
        return {self.specs[d]: self.trs[d] for d in DIRECTIONS}

    # The pipeline and guards.check read and restore these between calls: those of the member
    # that ran last. `context` always exists, so a phrase for the Gemma member keeps its context
    # after one for NLLB (which has none: [] here, and setting it does nothing).
    @property
    def context(self) -> list:
        return getattr(self.last, "context", [])

    @context.setter
    def context(self, value):
        if hasattr(self.last, "context"):
            self.last.context = value

    @property
    def last_truncated(self) -> bool:
        return bool(getattr(self.last, "last_truncated", False))

    @last_truncated.setter
    def last_truncated(self, value):
        self.last.last_truncated = value

    @property
    def context_clauses(self) -> int:
        return max((tr.context_clauses for tr in self.members().values() if hasattr(tr, "context_clauses")),
                   default=0)

    @context_clauses.setter
    def context_clauses(self, value):            # a profile setting: every member that has it
        for tr in self.members().values():
            if hasattr(tr, "context_clauses"):
                tr.context_clauses = value

    def translate(self, text: str, src: str = "ro", tgt: str = "en", glossary=None, **kw) -> str:
        tr = self.last = self.trs[(src, tgt)]
        live = bool(kw.get("use_context"))
        if "use_context" in kw and not hasattr(tr, "context"):
            kw.pop("use_context")                # NLLB has no context
        try:
            if getattr(tr, "verses", None) is None and live:
                return self._plain(tr, text, src, tgt, glossary)
            out = tr.translate(text, src, tgt, glossary, **kw)
            self.last_verse = getattr(tr, "last_verse", None)
            return out
        finally:
            self.used[id(tr)] = time.time()

    def _plain(self, tr, text: str, src: str, tgt: str, glossary) -> str:
        """A live phrase for a member with no verse matching of its own: the same steps the Gemma
        member takes before translating, so Philippians 1:21 still comes out as Cornilescu's
        "a muri este un câștig" when NLLB serves English → Romanian."""
        import guards
        if src == "en":
            text = guards.to_digits(text, "en")
            if glossary is not None:
                text = namelist.map_back(text, glossary.names, gate.lexicon()["en"])
        self.reading.update(text)
        verse = self.verses.match(text, src, tgt, self.reading.ref)
        if verse is None:
            self.last_verse = None
            return tr.translate(text, src, tgt, glossary)
        around = [tr.translate(t, src, tgt, glossary) if t else "" for t in (verse.before, verse.after)]
        self.last_verse = verse.log()
        return " ".join(x for x in (around[0], verse.caption, around[1]) if x)

    def reset_context(self):
        self.reading.reset()
        self.verses.reset()
        self.last_verse = None
        for tr in self.members().values():
            if hasattr(tr, "reset_context"):
                tr.reset_context()
            elif hasattr(tr, "context"):
                tr.context = []


def members(tr, lc: dict) -> dict[str, object]:
    """spec -> loaded translator for the translator `tr` that serves profile settings `lc`."""
    if tr is None:
        return {}
    if isinstance(tr, ByDirection):
        return tr.members()
    return {lc["translator"]: tr}


def assemble(lc: dict, have: dict, load) -> object:
    """The translator for profile settings `lc`: one translator when both directions use the
    same spec, else a ByDirection. Specs in `have` (spec -> loaded) are reused; the others
    are loaded with load(spec, direction) and added to `have`."""
    specs = direction_specs(lc)
    for d in DIRECTIONS:
        if specs[d] not in have:
            have[specs[d]] = load(specs[d], d)
    trs = {d: have[specs[d]] for d in DIRECTIONS}
    if trs[("ro", "en")] is trs[("en", "ro")]:
        return trs[("ro", "en")]
    return ByDirection(trs, specs)


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t
