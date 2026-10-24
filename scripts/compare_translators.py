#!/usr/bin/env python3
"""English → Romanian side by side (idea 13): the English phrases of a service, translated
again by each candidate translator, next to what the service showed, with simple checks.

  scripts/compare_translators.py extract [--date 2026-10-24]
      English phrases of that day's service logs (lang "en", with what the app showed) and a
      clean Romanian word list (Bible, glossary, idioms; not the logs: Gemma's "onze",
      "batizat" and "zelo" went into data/lexicon/ro.txt that way) into logs/bench/.
  .venv-mt/bin/python scripts/compare_translators.py run SPEC [SPEC ...] [--limit N]
      SPEC: nllb:nllb-200-distilled-1.3B, mlx:mlx-community/gemma-4-12B-it-qat-4bit (loads it
      on the GPU: one bench job at a time). Each phrase goes through what the pipeline does
      (clauses, the translator with context, the numbers guard). Writes
      logs/bench/en_ro_<date>_side.jsonl and .md (one column per translator plus "logged").
  scripts/compare_translators.py check [SIDE.jsonl]
      The checks again on a side-by-side file (no translator needed).

Checks per output: words outside the Romanian list (foreign or invented: "onze", "botezit"),
a list of known wrong-language words, agreement / pronoun patterns ("tata meu", "cele
paisprezece copii", "un încurajare", "l-iubea"), numbers of the source missing as digits or
words (guards.numbers), empty or echoed output, words added to a short fragment ("Amen." ->
"Amintește-mi.", "Thank you." -> "- Mulțumesc, domnule.": NLLB's weakness)."""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "src" / "live"))


def main_repo() -> pathlib.Path:
    """Logs and the Bible texts are git-ignored: in the main checkout, not a worktree."""
    for p in (ROOT, *ROOT.parents):
        if (p / ".git").is_dir():
            return p
    return ROOT


BASE = main_repo()
BENCH = BASE / "logs" / "bench"
_WORD = re.compile(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*")
_FOLD = str.maketrans({"ă": "a", "â": "a", "î": "i", "ș": "s", "ț": "t", "ş": "s", "ţ": "t"})
# letters Romanian never writes (Spanish, Portuguese, Italian, French accents)
_FOREIGN_CHARS = set("ñçãõáéíóúàèìòùêôûëïü")
# wrong-language or invented forms seen from Gemma (idea 13), folded
FOREIGN = {"onze", "doze", "treze", "catorze", "quinze", "botezit", "batizat", "batizado", "bautizado",
           "zelo", "reunionit", "temearea", "douaspreze", "familia's", "nascu", "muito", "tambem",
           "pero", "porque", "entonces", "siempre", "hermano", "hermanos", "irmao", "irmaos", "senhor",
           "fratello", "sempre", "anche", "molto", "perche", "allora", "nostro", "vostro", "corazon",
           "coracao", "cuore", "iglesia", "igreja", "chiesa", "gracias", "obrigado", "grazie"}
RO_SUFFIXES = ("urilor", "ului", "elor", "ilor", "lor", "lui", "ul", "le", "ua", "ii", "ile", "uri",
               "ul", "a", "i", "e", "u", "ă", "ăm", "ați", "ește", "esc", "ește", "ează", "ăm", "au", "ând",
               "ind", "it", "ită", "iți", "ite", "at", "ată", "ați", "ate", "ut", "ută", "uți", "ute")
# (name, pattern) on the Romanian output; each is a known Gemma slip (translation_ideas.md 13)
AGREEMENT = [
    ("tata meu/mea", r"\btata (?:meu|mea)\b"),
    ("cele + numeral + copii/frați", r"\bcele (?:\w+ )?(?:copii|frați|fii|băieți|nepoți)\b"),
    ("un + feminine noun", r"\bun (?:\w+(?:are|ere|ire|ție|țiune|iune|tate|ură))\b"),
    ("o + masculine/neuter noun", r"\bo (?:copil|frate|tată|suflet|cuvânt|lucru|timp|an|om)\b"),
    ("acești + feminine / câteva", r"\bacești (?:\w+ )?(?:câteva|zile|clipe|vremuri)\b"),
    ("toată + masculine/neuter", r"\btoată (?:sufletul|cugetul|timpul|neamul)\b"),
    ("ale + voastră/noastră", r"\bale \w+ (?:voastră|noastră)\b|\b\w+i ale (?:voastră|noastră)\b"),
    ("l-iubea / i-a iubit pe", r"\bl-(?:iubea|iubeau)\b|\bi-a iubit pe\b"),
    ("mulțumea pe Dumnezeu", r"\bmulțume\w* pe Dumnezeu\b"),
    ("poate + indicative", r"\bpoate (?:răsună|cântă|spună|vină)\b"),
    ("aceasta a fost + masculine", r"\baceasta a fost (?:timpul|planul|momentul)\b"),
    ("către + parents (born to)", r"\bnăscut\w* (?:\w+ ){0,2}către\b"),
    ("decât cineva", r"\bdecât cineva\b"),
]
_AGREE = [(n, re.compile(p, re.I)) for n, p in AGREEMENT]


def fold(w: str) -> str:
    return w.lower().translate(_FOLD)


# --- extract --------------------------------------------------------------------------------
def extract(date: str, out: pathlib.Path) -> tuple[int, int]:
    rows = []
    for f in sorted((BASE / "logs" / "services").glob(f"{date}_*.jsonl")):
        recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        rehearsal = any(r.get("event") == "rehearsal" for r in recs)
        for r in recs:
            if r.get("lang") == "en" and (r.get("source") or "").strip():
                rows.append({"file": f.name, "id": r.get("id"), "time": r.get("time"), "rehearsal": rehearsal,
                             "profile": r.get("profile"), "outcome": r.get("outcome"),
                             "source": r["source"], "logged": r.get("translation") or "",
                             "mt_s": r.get("mt_s")})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    words = clean_ro_words()
    (out.parent / "ro_words_clean.txt").write_text("\n".join(sorted(words)) + "\n", encoding="utf-8")
    return len(rows), len(words)


def clean_ro_words() -> set[str]:
    """Romanian words from sources no translator wrote: the Cornilescu Bible, the reference
    verses, the glossary, idioms and terms."""
    import csv
    ro: set[str] = set()
    bible = BASE / "data" / "ref" / "cornilescu_latin.parquet"
    if bible.is_file():
        import pandas as pd
        for t in pd.read_parquet(bible)["latin"]:
            ro.update(fold(w) for w in _WORD.findall(t or ""))
    verses = BASE / "data" / "mt" / "bible_verses.jsonl"
    if verses.is_file():
        for line in verses.read_text(encoding="utf-8").splitlines():
            ro.update(fold(w) for w in _WORD.findall(json.loads(line).get("ro_ref", "")))
    for name, col in ((ROOT / "glossary.csv", "ro"), (ROOT / "data" / "idioms.csv", "ro")):
        if name.is_file():
            with open(name, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    ro.update(fold(w) for w in _WORD.findall(row.get(col) or ""))
    ro.update(fold(w) for w in _WORD.findall((ROOT / "terms.txt").read_text(encoding="utf-8")))
    seen = collections.Counter()                  # modern words ("vineri", "ceea"): what the Romanian
    for f in (BASE / "logs" / "services").glob("*.jsonl"):   # recogniser wrote in 3+ phrases
        for line in f.read_text(encoding="utf-8").splitlines():
            if '"lang": "ro"' in line:
                r = json.loads(line)
                seen.update({fold(w) for w in _WORD.findall(r.get("source") or "") if w.islower()})
    ro.update(w for w, n in seen.items() if n >= 3)
    return {w for w in ro if w not in FOREIGN}


# --- checks ---------------------------------------------------------------------------------
def _known(w: str, words: set[str]) -> bool:
    if w in words:
        return True
    for part in re.split(r"[-'’]", w):           # "n-aveți", "s-a": by parts
        if part and part not in words and not any(
                part.endswith(fold(s)) and len(part) - len(fold(s)) >= 3 and part[: -len(fold(s))] in words
                for s in RO_SUFFIXES) and len(part) > 2:
            return False
    return True


def checks(src: str, out: str, words: set[str]) -> dict:
    import guards
    src_words = {fold(w) for w in _WORD.findall(src)}
    toks = _WORD.findall(out)
    unknown = []
    for i, t in enumerate(toks):
        f = fold(t)
        if f in src_words or (t[:1].isupper() and i and not out[:out.find(t)].rstrip().endswith((".", "!", "?"))):
            continue                                  # a name or a word kept from the English
        if not _known(f, words):
            unknown.append(t)
    foreign = [t for t in toks if fold(t) in FOREIGN or set(t.lower()) & _FOREIGN_CHARS]
    agree = [n for n, p in _AGREE if p.search(out)]
    a, b = guards.numbers(src, "en"), guards.numbers(out, "ro")
    digits_src = set(re.findall(r"\d+", guards.to_digits(src, "en")))
    return {"unknown": unknown, "foreign": foreign, "agreement": agree,
            "numbers_missing": sorted(a - b), "digits_kept": sorted(digits_src & set(re.findall(r"\d+", out))),
            "digits_src": sorted(digits_src), "empty": not out.strip(),
            # NLLB on fragments: "Amen." -> "Amintește-mi.", "Thank you." -> "- Mulțumesc, domnule."
            "added": bool(re.match(r"\s*[-–—]\s", out) or re.search(r"[.!?]\s+[-–—]\s", out)
                          or (len(src.split()) <= 4 and len(toks) > 2 * len(src.split()) + 1)
                          or (fold(src.strip(" .!")) == "amen" and fold(out.strip(" .!")) != "amin")),
            "echo": [w.lower() for w in _WORD.findall(src)] == [w.lower() for w in toks] and len(toks) > 2}


def summarize(rows: list[dict], cols: list[str], words: set[str]) -> dict:
    summ = {}
    for c in cols:
        n = collections.Counter()
        pats = collections.Counter()
        for r in rows:
            k = checks(r["source"], r.get(c) or "", words)
            r.setdefault("checks", {})[c] = k
            n["phrases"] += 1
            n["with_unknown_word"] += bool(k["unknown"])
            n["unknown_words"] += len(k["unknown"])
            n["foreign_words"] += len(k["foreign"])
            n["agreement_hits"] += len(k["agreement"])
            n["numbers_missing"] += len(k["numbers_missing"])
            n["src_digit_numbers"] += len(k["digits_src"])
            n["digits_kept"] += len(k["digits_kept"])
            n["empty"] += k["empty"]
            n["echo"] += k["echo"]
            n["added"] += k["added"]
            pats.update(k["agreement"])
            pats.update(f"foreign:{fold(w)}" for w in k["foreign"])
        summ[c] = {**n, "patterns": dict(pats.most_common())}
    return summ


# --- run ------------------------------------------------------------------------------------
def load(spec: str, threads: int):
    import translate
    kind, _, name = spec.partition(":")
    if kind == "nllb":
        return translate.NLLBTranslator(name, threads=threads)
    return translate.make_translator(spec)


def translate_all(spec: str, rows: list[dict], threads: int, context_clauses: int) -> tuple[list[str], list[float]]:
    """Each source as the pipeline translates it: clauses, context, the numbers guard."""
    import guards
    import translate
    from segmenter import split_clauses
    tr = load(spec, threads)
    if hasattr(tr, "context_clauses"):
        tr.context_clauses = context_clauses
    gloss = translate.Glossary()
    tr.translate(translate.WARMUP[("en", "ro")], "en", "ro", gloss)
    if hasattr(tr, "reset_context"):
        tr.reset_context()
    outs, secs, prev = [], [], None
    for r in rows:
        if prev is not None and (r["file"] != prev["file"] or (r.get("id") or 0) - (prev.get("id") or 0) > 3):
            tr.reset_context()                        # another service, or Romanian in between
        prev = r
        t0 = time.perf_counter()
        clauses, tail = split_clauses(r["source"])
        out = []
        for p in clauses + ([tail] if tail else []):
            out.append(tr.translate(p, "en", "ro", gloss, use_context=True) if hasattr(tr, "context")
                       else tr.translate(p, "en", "ro", gloss))
        text = " ".join(o for o in out if o)
        text, _ = guards.check(tr, r["source"], text, "en", "ro", gloss)
        outs.append(text)
        secs.append(round(time.perf_counter() - t0, 3))
    return outs, secs


def col_name(spec: str) -> str:
    return spec.split(":", 1)[0] if spec.count(":") else spec


def write_side(rows: list[dict], cols: list[str], summ: dict, path: pathlib.Path):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    md = [f"# English → Romanian side by side ({len(rows)} phrases)", "",
          "| check | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    keys = ["with_unknown_word", "unknown_words", "foreign_words", "agreement_hits", "numbers_missing",
            "src_digit_numbers", "digits_kept", "empty", "echo", "added"]
    for k in keys:
        md.append(f"| {k} | " + " | ".join(str(summ[c].get(k, 0)) for c in cols) + " |")
    for c in cols:
        md += ["", f"**{c} patterns:** " + (", ".join(f"{p} ×{n}" for p, n in summ[c]["patterns"].items()) or "none")]
    md += ["", "| time | English | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
    for r in rows:
        cells = []
        for c in cols:
            k = r["checks"][c]
            flag = k["foreign"] + k["agreement"] + [f"?{w}" for w in k["unknown"][:3]] + \
                [f"#{n}" for n in k["numbers_missing"]] + (["added?"] if k["added"] else [])
            cells.append((r.get(c) or "").replace("|", "/") + (f" ⚠ {', '.join(map(str, flag))}" if flag else ""))
        md.append(f"| {(r.get('time') or '')[11:19]} | {r['source'].replace('|', '/')} | " + " | ".join(cells) + " |")
    path.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--date", default="2026-10-24")
    r = sub.add_parser("run")
    r.add_argument("specs", nargs="+")
    r.add_argument("--date", default="2026-10-24")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--threads", type=int, default=4, help="NLLB CPU threads (leave room for a replay)")
    r.add_argument("--context-clauses", type=int, default=4)
    r.add_argument("--no-rehearsal", action="store_true", help="skip the rehearsal file's phrases")
    c = sub.add_parser("check")
    c.add_argument("side", nargs="?")
    c.add_argument("--date", default="2026-10-24")
    a = ap.parse_args()
    src = BENCH / f"en_ro_{a.date}.jsonl"
    side = BENCH / f"en_ro_{a.date}_side.jsonl"
    if a.cmd == "extract":
        n, w = extract(a.date, src)
        print(f"{n} English phrases -> {src}; {w} clean Romanian words -> {BENCH / 'ro_words_clean.txt'}")
        return 0
    words = set((BENCH / "ro_words_clean.txt").read_text(encoding="utf-8").split())
    if a.cmd == "check":
        side = pathlib.Path(a.side) if a.side else side
        rows = [json.loads(l) for l in side.read_text(encoding="utf-8").splitlines() if l.strip()]
        cols = [k for k in rows[0] if k == "logged" or (k.startswith(("nllb", "mlx")) and not k.endswith(" s"))]
    else:
        rows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
        if a.no_rehearsal:
            rows = [x for x in rows if not x.get("rehearsal")]
        rows = rows[: a.limit] if a.limit else rows
        cols = ["logged"]
        for spec in a.specs:
            name = spec if spec not in cols else spec + "#2"
            t0 = time.time()
            outs, secs = translate_all(spec, rows, a.threads, a.context_clauses)
            for x, o, s in zip(rows, outs, secs):
                x[name], x[name + " s"] = o, s
            cols.append(name)
            print(f"{spec}: {len(rows)} phrases in {time.time() - t0:.0f} s", flush=True)
    for x in rows:
        x.pop("checks", None)
    summ = summarize(rows, cols, words)
    write_side(rows, cols, summ, side)
    print(json.dumps({c: {k: v for k, v in s.items() if k != "patterns"} for c, s in summ.items()}, indent=1))
    for c in cols:
        print(c, "patterns:", summ[c]["patterns"])
    print(f"-> {side} and {side.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
