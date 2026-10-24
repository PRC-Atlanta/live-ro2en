#!/usr/bin/env python3
"""Download the public-domain Bible texts the verse matcher (src/live/verses.py) captions from,
check them against pinned sha256 sums and write data/bible/verses.tsv.gz, plus
data/bible/lexicon.tsv.gz: Romanian/English word pairs that occur in the same verses (Dice
coefficient), which the matcher uses to find where a part of a verse is in the other text.

  scripts/fetch_bible.py [--cache DIR] [--out FILE] [--offline]

Sources (licences in data/bible/README.md):
  - World English Bible (eBible.org "engwebp": Protestant canon, "LORD" for the Name): the
    English the app captions for Romanian speech. The VPL text puts a psalm's title in
    verse 1 ("A Psalm by David. The LORD is my shepherd"), Cornilescu does not number titles:
    they are cut, using the titles the USFM edition marks (\\d).
  - King James Version (eBible.org "engkjvcpb"): only for recognising English speech (preachers
    quote KJV/NKJV wording); never shown.
  - Cornilescu 1924, Romanian: eBible.org "ron1924" is the public-domain text but only in
    Moldovan Cyrillic, and ambiguous to transliterate (я = ea or ia). The Latin text is taken
    from thiagobodruk/bible ro_vdc.json (pinned commit) verse by verse, only where it is the
    same text as ron1924 (phonetic comparison); its inlined notes "(Fiul durerii mele)" are
    dropped. A verse that differs is transliterated from ron1924 instead. John 11 is missing
    from ron1924, so those verses come from ro_vdc unchecked.
The downloads stay in --cache (git-ignored data/ref/bible); the output is deterministic and
committed (the app runs offline and bundles only tracked files). Re-run after changing a pin;
eBible.org regenerates its zips now and then, so a checksum failure means: look at the new
file, then update the pin here."""
from __future__ import annotations

import argparse
import collections
import difflib
import gzip
import hashlib
import io
import json
import pathlib
import re
import sys
import unicodedata
import urllib.request
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from textnorm import cyr_to_latin, fix_cedilla, phon_key, tokens   # noqa: E402
sys.path.insert(0, str(ROOT / "src" / "live"))
import verses   # noqa: E402

SOURCES = {
    "web": ("https://ebible.org/Scriptures/engwebp_vpl.zip",
            "d681320dbaec25ba2b6ad7151cd71737c10efc24a6545ffe1aa50b5ef0ca4b8d", "engwebp_vpl.txt"),
    "usfm": ("https://ebible.org/Scriptures/engwebp_usfm.zip",
             "d2a7c9640820003d8ed475ce6d92cc990b0447fe96ce72fd0df17c0dac9d95fe", "20-PSAengwebp.usfm"),
    "kjv": ("https://ebible.org/Scriptures/engkjvcpb_vpl.zip",
            "b82a0651fe6a0f070c18c8c045b71891aa92de6f5c57e600c4ee427b0dcf4036", "engkjvcpb_vpl.txt"),
    "ron": ("https://ebible.org/Scriptures/ron1924_vpl.zip",
            "0a0ab371ef5bdab914afbe5fa0dd6bd14a3a41b5c836d9c694a76da06bf5dccb", "ron1924_vpl.txt"),
    "vdc": ("https://raw.githubusercontent.com/thiagobodruk/bible/"
            "df0860fe0921b5b899d1b554041900ab072cd4c4/json/ro_vdc.json",
            "d1dfeb5fc5ad868b354fb69712f240b492fc099ac6f51a69041c97d0a7c628d1", None),
}
LEXICON_MIN = 0.15      # Dice coefficient kept in the lexicon ...
LEXICON_TOP = 6         # ... for the best few partners of each word
SAME_TEXT = 0.9          # phonetic similarity above which ro_vdc's verse is ron1924's verse
_VPL = re.compile(r"(\w+) (\d+):(\d+) ?(.*)")


def fetch(name: str, cache: pathlib.Path, offline: bool) -> bytes:
    url, sha, _ = SOURCES[name]
    f = cache / url.rsplit("/", 1)[1]
    if not f.exists():
        if offline:
            sys.exit(f"fetch_bible: {f} is not downloaded (run without --offline)")
        print(f"downloading {url}", file=sys.stderr)
        with urllib.request.urlopen(url, timeout=120) as r:
            data = r.read()
        cache.mkdir(parents=True, exist_ok=True)
        f.write_bytes(data)
    data = f.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != sha:
        sys.exit(f"fetch_bible: {f.name}: sha256 {got}, expected {sha} (changed upstream? check it, "
                 f"then update SOURCES)")
    return data


def vpl(data: bytes, member: str) -> dict[tuple[str, int, int], str]:
    """A VPL text from its zip, read in memory: {(book code, chapter, verse): text}."""
    out = {}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for line in z.read(member).decode("utf-8-sig").splitlines():
            m = _VPL.match(line.strip())
            if m and m[4].strip():
                out[(m[1], int(m[2]), int(m[3]))] = m[4].strip()
    return out


def psalm_titles(data: bytes, member: str) -> dict[tuple[int, int], str]:
    """{(chapter, verse): title} from the USFM Psalms: the \\d line before that verse."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        usfm = z.read(member).decode("utf-8-sig")
    out, ch, title = {}, 0, None
    for line in usfm.splitlines():
        if m := re.match(r"\\c (\d+)", line):
            ch = int(m[1])
        elif line.startswith("\\d "):
            t = re.sub(r"\\([fx]) .*?\\\1\*", "", line[3:])            # footnotes, cross-references
            t = re.sub(r"\|[^\\]*?\\w\*", "", t)                      # \w word|strong="H1"\w*
            title = re.sub(r"\s+", " ", re.sub(r"\\\+?[a-z]+\d*\*?", "", t)).strip()
        elif (m := re.match(r"\\v (\d+)", line)) and title:
            out[(ch, int(m[1]))], title = title, None
    return out


def tidy_kjv(text: str) -> str:
    """The KJV for matching: no [italics] brackets, no (א) acrostic letters."""
    text = re.sub(r"\([\u0590-\u05ff]+\)\s*", "", text.replace("[", "").replace("]", ""))
    return re.sub(r"\s+", " ", text).strip()


def tidy_ro(text: str) -> str:
    """ro_vdc's verse as printed: no inlined notes, no space before punctuation, closing quotes."""
    text = re.sub(r"\s*\([^()]*\)", "", fix_cedilla(text))
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"„([^„\"”]*)\"", r"„\1”", text)
    return re.sub(r"\s+", " ", text).strip()


def _key(text: str) -> str:
    return " ".join(re.sub("[aeiouăîâ]", "", phon_key(t)) for t in tokens(text))


def same_text(latin: str, cyrillic: str) -> bool:
    a, b = _key(latin), _key(cyr_to_latin(cyrillic))
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio() >= SAME_TEXT


def build(cache: pathlib.Path, offline: bool) -> tuple[list[tuple], dict]:
    web = vpl(fetch("web", cache, offline), SOURCES["web"][2])
    codes = list(dict.fromkeys(k[0] for k in web))
    if len(codes) != 66:
        sys.exit(f"fetch_bible: WEB has {len(codes)} books, expected 66")
    titles = psalm_titles(fetch("usfm", cache, offline), SOURCES["usfm"][2])
    for (c, v), t in titles.items():
        k = (codes[18], c, v)
        if web.get(k, "").startswith(t) and web[k][len(t):].strip():
            web[k] = web[k][len(t):].strip()
    kjv = {k: tidy_kjv(v) for k, v in vpl(fetch("kjv", cache, offline), SOURCES["kjv"][2]).items() if k[0] in codes}
    ron = vpl(fetch("ron", cache, offline), SOURCES["ron"][2])
    vdc = {}
    books = json.loads(fetch("vdc", cache, offline).decode("utf-8-sig"))
    if len(books) != 66:
        sys.exit(f"fetch_bible: ro_vdc has {len(books)} books, expected 66")
    for code, b in zip(codes, books):
        for c, vs in enumerate(b["chapters"], 1):
            for v, t in enumerate(vs, 1):
                if t.strip():
                    vdc[(code, c, v)] = tidy_ro(t)
    stats = {"checked": 0, "transliterated": 0, "unchecked": 0}
    ro = {}
    for k in sorted(set(vdc) | set(ron), key=lambda k: (codes.index(k[0]), k[1], k[2])):
        if k in ron and k in vdc and same_text(vdc[k], ron[k]):
            ro[k], stats["checked"] = vdc[k], stats["checked"] + 1
        elif k in ron:
            ro[k], stats["transliterated"] = cyr_to_latin(ron[k]), stats["transliterated"] + 1
        else:
            ro[k], stats["unchecked"] = vdc[k], stats["unchecked"] + 1
    keys = sorted(set(web) | set(ro), key=lambda k: (codes.index(k[0]), k[1], k[2]))
    rows = [(codes.index(k[0]) + 1, k[1], k[2], ro.get(k, ""), web.get(k, ""), kjv.get(k, "")) for k in keys]
    stats.update(verses=len(rows), web=len(web), kjv=len(kjv), ro=len(ro),
                 both=sum(1 for r in rows if r[3] and r[4]))
    return rows, stats


def write(rows: list[tuple], out: pathlib.Path) -> None:
    clean = lambda s: unicodedata.normalize("NFC", s.replace("\t", " "))   # noqa: E731
    body = "book\tchapter\tverse\tro\tweb\tkjv\n" + "".join(
        f"{b}\t{c}\t{v}\t{clean(r)}\t{clean(w)}\t{clean(k)}\n" for b, c, v, r, w, k in rows)
    write_gz(out, body)


def lexicon(rows: list[tuple]) -> list[tuple[str, str, float]]:
    """(ro key, en key, Dice) for words found together in the verses (RO with WEB)."""
    ids: dict[str, int] = {}
    df, co = collections.Counter(), collections.Counter()
    for _, _, _, ro, web, _ in rows:
        if not (ro and web):
            continue
        a = {ids.setdefault("r" + k, len(ids)) for k, *_ in verses.words(ro, "ro") if verses.content(k, "ro")}
        b = {ids.setdefault("e" + k, len(ids)) for k, *_ in verses.words(web, "en") if verses.content(k, "en")}
        df.update(a)
        df.update(b)
        co.update(x << 20 | y for x in a for y in b)
    names = {i: k for k, i in ids.items()}
    best = collections.defaultdict(list)
    for p, c in co.items():
        x, y = p >> 20, p & 0xFFFFF
        d = 2 * c / (df[x] + df[y])
        if c >= 2 and d >= LEXICON_MIN:
            best[x].append((d, y))
            best[y].append((d, x))
    keep = set()
    for x, ys in best.items():
        for d, y in sorted(ys, key=lambda t: (-t[0], names[t[1]]))[:LEXICON_TOP]:
            keep.add((min(x, y), max(x, y), round(d, 2)))
    out = []
    for x, y, d in keep:
        a, b = sorted((names[x], names[y]), key=lambda k: k[0] != "r")      # ro first
        out.append((a[1:], b[1:], d))
    return sorted(out)


def write_gz(out: pathlib.Path, body: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
        g.write(body.encode("utf-8"))      # mtime 0, no name: the same input gives the same bytes


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache", type=pathlib.Path, default=ROOT / "data" / "ref" / "bible")
    ap.add_argument("--out", type=pathlib.Path, default=ROOT / "data" / "bible" / "verses.tsv.gz")
    ap.add_argument("--offline", action="store_true", help="use only the files already in --cache")
    a = ap.parse_args(argv)
    rows, stats = build(a.cache, a.offline)
    write(rows, a.out)
    print(f"{a.out}: {a.out.stat().st_size / 1e6:.1f} MB, " + ", ".join(f"{k} {v}" for k, v in stats.items()))
    lex = a.out.with_name("lexicon.tsv.gz")
    pairs = lexicon(rows)
    write_gz(lex, "ro\ten\tdice\n" + "".join(f"{r}\t{e}\t{d}\n" for r, e, d in pairs))
    print(f"{lex}: {lex.stat().st_size / 1e6:.2f} MB, {len(pairs)} word pairs")


if __name__ == "__main__":
    main()
