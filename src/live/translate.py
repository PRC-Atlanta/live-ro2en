"""Offline translators (RO<->EN) shared by the Phase 2 benchmark and the live app.

  NLLBTranslator   CTranslate2 int8 NLLB-200 (models/ct2/<name>), CPU
  MLXLLMTranslator local LLM via mlx-lm, glossary injected per sentence
Glossary: glossary.csv (ro,en,note). `Glossary.relevant(src, src_lang)` returns only the
entries whose source term occurs in the sentence, so prompts stay short."""
from __future__ import annotations

import csv
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from textnorm import fix_cedilla, fold_diacritics, phon_key, tokens   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
NLLB_CODES = {"ro": "ron_Latn", "en": "eng_Latn"}
# Post-edits for the target language (whole words). NLLB writes the Orthodox "Iisus";
# Romanian evangelical usage (and Cornilescu) is "Isus".
POSTEDIT = {"ro": {"Iisus": "Isus", "Iisuse": "Isuse", "lui Iisus": "lui Isus"}, "en": {}}


def postedit(text: str, tgt: str) -> str:
    for a, b in POSTEDIT.get(tgt, {}).items():
        text = re.sub(rf"\b{re.escape(a)}\b", b, text)
    return fix_cedilla(text) if tgt == "ro" else text


def _fk(text: str) -> list[str]:
    return [fold_diacritics(phon_key(t)) for t in tokens(text)]


class Glossary:
    def __init__(self, path: pathlib.Path | str = ROOT / "glossary.csv"):
        self.rows = [r for r in csv.DictReader(open(path)) if r.get("ro") and r.get("en")]
        self._ro = [(_fk(r["ro"]), r) for r in self.rows]
        self._en = [(tokens(r["en"].replace("the ", "")), r) for r in self.rows]

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
            hits = [r for k, r in self._en if self._contains(seq, k)]
        # prefer longer matches (Duhul Sfânt over Duhul)
        return sorted(hits, key=lambda r: -len(r["ro"]))

    def adherence(self, src: str, out: str, src_lang: str = "ro") -> tuple[int, int]:
        """(n_terms_in_source, n_rendered_as_glossary_prefers)."""
        rel = self.relevant(src, src_lang)
        if src_lang == "ro":
            o = " " + " ".join(tokens(out)) + " "
            ok = sum(any(" " + " ".join(tokens(alt.replace("the ", ""))) + " " in o
                         for alt in r["en"].split("/")) for r in rel)
        else:
            o = _fk(out)
            ok = sum(self._contains(o, _fk(r["ro"])) for r in rel)
        return len(rel), ok


class NLLBTranslator:
    def __init__(self, name: str = "nllb-200-distilled-1.3B", device: str = "cpu",
                 compute_type: str = "int8", beam: int = 2, threads: int = 8):
        import ctranslate2
        import transformers
        self.name = name
        self.tr = ctranslate2.Translator(str(ROOT / "models" / "ct2" / name), device=device,
                                         compute_type=compute_type, intra_threads=threads)
        self.tok = transformers.AutoTokenizer.from_pretrained(f"facebook/{name}")
        self.beam = beam

    def translate(self, text: str, src: str = "ro", tgt: str = "en", glossary=None) -> str:
        self.tok.src_lang = NLLB_CODES[src]
        pieces = self.tok.convert_ids_to_tokens(self.tok.encode(text))
        res = self.tr.translate_batch([pieces], target_prefix=[[NLLB_CODES[tgt]]],
                                      beam_size=self.beam, max_decoding_length=256)
        out = res[0].hypotheses[0][1:]
        out = self.tok.decode(self.tok.convert_tokens_to_ids(out), skip_special_tokens=True).strip()
        return postedit(out, tgt)


SYSTEM = {
    ("ro", "en"): ("You are a live interpreter for a Romanian evangelical church service. Translate the "
                   "Romanian text into natural, faithful English. For Bible quotations and biblical "
                   "terms use traditional KJV/NKJV-style wording. Keep names in their standard English "
                   "Bible form. Output only the English translation, nothing else."),
    ("en", "ro"): ("You are a live interpreter for a Romanian evangelical church service. Translate the "
                   "English text into natural Romanian with correct diacritics (ă â î ș ț). For Bible "
                   "quotations and terms use Cornilescu-style wording. Output only the Romanian "
                   "translation, nothing else."),
}


class MLXLLMTranslator:
    def __init__(self, repo: str, max_tokens: int = 200, context_clauses: int = 2):
        from mlx_lm import load
        self.repo = repo
        self.model, self.tok = load(repo)
        self.max_tokens = max_tokens
        self.context: list[tuple[str, str]] = []
        self.context_clauses = context_clauses

    def _prompt(self, text, src, tgt, glossary):
        lines = []
        if glossary is not None:
            rel = glossary.relevant(text, src)
            if rel:
                pairs = [(r["ro"], r["en"]) if src == "ro" else (r["en"], r["ro"]) for r in rel[:12]]
                lines.append("Glossary (use these renderings):\n" + "\n".join(f"- {a} = {b}" for a, b in pairs))
        if self.context:
            lines.append("Previous sentences (context only, do not translate again):\n" +
                         "\n".join(s for s, _ in self.context[-self.context_clauses:]))
        lines.append(f"Translate:\n{text}")
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
        if not use_context:
            self.context = []
        out = generate(self.model, self.tok, prompt=self._prompt(text, src, tgt, glossary),
                       max_tokens=self.max_tokens, sampler=make_sampler(temp=0.0), verbose=False)
        out = re.sub(r"<think>.*?</think>", "", out, flags=re.S).strip().strip('"').strip()
        out = out.split("\n\n")[0].strip()
        out = postedit(out, tgt)
        if use_context:
            self.context.append((text, out))
        return out


def make_translator(spec: str):
    """'nllb:<ct2 dir name>' or 'mlx:<hf repo>'."""
    kind, _, name = spec.partition(":")
    if kind == "nllb":
        return NLLBTranslator(name)
    if kind == "mlx":
        return MLXLLMTranslator(name)
    raise ValueError(spec)


def timed(fn, *a, **k):
    t = time.perf_counter()
    r = fn(*a, **k)
    return r, time.perf_counter() - t
