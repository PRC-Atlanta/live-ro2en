#!/usr/bin/env python3
"""Build the word lists the confidence gate (src/live/gate.py) checks phrases against.

  scripts/build_lexicon.py [--logs DIR] [--out DIR]

Writes data/lexicon/ro.txt and data/lexicon/en.txt: one lower-case, diacritics-folded word per
line. Sources, all offline:
  - Romanian: the Cornilescu Bible (data/ref/cornilescu_latin.parquet, if present), the
    Romanian STT reference verses (data/mt/bible_verses.jsonl), glossary.csv, terms.txt, and
    the service logs: the translator's Romanian output (EN→RO, clean words) plus words the
    Romanian recogniser wrote in at least MIN_SOURCE_PHRASES phrases (a one-off garble like
    "blăstă" stays out).
  - English: the KJV (data/ref/en/KJV.csv), keywords_en.txt, glossary.csv, the translator's
    English output in the logs and English recogniser words seen in MIN_SOURCE_PHRASES phrases.
    The system dictionary (/usr/share/dict/words) is read by the gate at run time, not copied.
Only words also written in lower case somewhere are taken from the logs, so people's names
from the services do not end up in the repository (names come from terms/glossary instead).
Re-run after a few services to widen the lists; the gate reloads them at start-up."""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from textnorm import fix_cedilla, fold_diacritics   # noqa: E402

MIN_SOURCE_PHRASES = 3
_WORD = re.compile(r"[A-Za-zĂÂÎȘȚăâîșțşţŞŢ]+")


def words(text: str, lower_only: bool = False) -> list[str]:
    toks = _WORD.findall(fix_cedilla(text or ""))
    if lower_only:
        toks = [t for t in toks if t.islower()]
    return [fold_diacritics(t.lower()) for t in toks]


def main_repo() -> pathlib.Path:
    """Data that is git-ignored (Bible texts, logs) lives in the main checkout, not a worktree."""
    for p in (ROOT, *ROOT.parents):
        if (p / ".git").is_dir():
            return p
    return ROOT


def main() -> int:
    ap = argparse.ArgumentParser()
    base = main_repo()
    ap.add_argument("--logs", default=str(base / "logs" / "services"))
    ap.add_argument("--data", default=str(base / "data"))
    ap.add_argument("--out", default=str(ROOT / "data" / "lexicon"))
    a = ap.parse_args()
    data = pathlib.Path(a.data)
    ro, en = set(), set()

    bible = data / "ref" / "cornilescu_latin.parquet"
    if bible.is_file():
        import pandas as pd
        for t in pd.read_parquet(bible)["latin"]:
            ro.update(words(t))
    verses = data / "mt" / "bible_verses.jsonl"
    if verses.is_file():
        for line in verses.read_text(encoding="utf-8").splitlines():
            v = json.loads(line)
            ro.update(words(v.get("ro_ref", "")))
            en.update(words(v.get("en_ref", "")))
    kjv = data / "ref" / "en" / "KJV.csv"
    if kjv.is_file():
        with open(kjv, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                en.update(words(row["Text"]))
    with open(ROOT / "glossary.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ro.update(words(row["ro"]))
            en.update(words(row["en"]))
    for name, dest in (("terms.txt", ro), ("keywords_en.txt", en)):
        for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                dest.update(words(line))

    src_count = {"ro": collections.Counter(), "en": collections.Counter()}
    for log in sorted(pathlib.Path(a.logs).glob("*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("outcome") != "ok" or r.get("lang") not in ("ro", "en"):
                continue
            out_set = en if r["lang"] == "ro" else ro
            out_set.update(words(r.get("translation", ""), lower_only=True))
            src_count[r["lang"]].update(set(words(r.get("source", ""), lower_only=True)))
    for lang, dest in (("ro", ro), ("en", en)):
        dest.update(w for w, n in src_count[lang].items() if n >= MIN_SOURCE_PHRASES)

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, s in (("ro", ro), ("en", en)):
        s = sorted(w for w in s if len(w) > 1 or w in ("a", "o", "e", "i", "s"))
        (out / f"{name}.txt").write_text("\n".join(s) + "\n", encoding="utf-8")
        print(f"{name}: {len(s)} words -> {out / (name + '.txt')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
