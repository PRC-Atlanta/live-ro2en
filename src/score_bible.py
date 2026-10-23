"""Score every local model + Google on the Bible test set.

  .venv/bin/python src/score_bible.py
Writes results/bible_scores.csv, results/bible_by_excerpt.csv, results/bible_terms.csv.
WER: phonetic-key tokens (script variants neutral), diacritics kept ('wer') and folded
('wer_nodiac'). Term accuracy: per terms.txt occurrence inside the aligned reference span,
'hit' = exact spelling (case-insensitive, diacritics required), 'near' = same word with
wrong/missing diacritics or a close misspelling, else 'miss'. terms.txt is also the phrase-boost
list (stt_run boost models, Google chirp_2 phrase set), so term_set/term_acc_boosted say how
much of the term figure is measured on boosted terms. Digits are spelled out before scoring.
"""
from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter
import pathlib
import sys
from collections import defaultdict

import pandas as pd
from rapidfuzz import fuzz

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from scoring import align, corpus_score, score                            # noqa: E402
from textnorm import cyr_to_latin, fold_diacritics, key_tokens, phon_key, strip_headers, tokens  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
RES = ROOT / "results"


def _latin_df() -> pd.DataFrame:
    cache = ROOT / "data" / "ref" / "cornilescu_latin.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    d = pd.read_parquet(ROOT / "data/ref/cornilescu/data/data.parquet")
    d = d.sort_values(["book_number", "chapter", "verse_number"])
    d["latin"] = d["text"].map(cyr_to_latin)
    d = d[["book_number", "chapter", "verse_number", "latin"]].reset_index(drop=True)
    d.to_parquet(cache)
    return d


def book_verses(book_numbers: set[int]) -> dict[int, list[tuple[int, int, str]]]:
    """book -> [(chapter, verse, latin_text)] with glued words repaired
    ('darvoi' -> 'dar voi': the dataset lost some spaces)."""
    d = _latin_df()
    freq = Counter(t for x in d["latin"] for t in tokens(x))

    def unglue(m):
        w = m.group(0); lw = w.lower()
        if freq[lw] > 2 or len(lw) < 5:
            return w
        best = max(((min(freq[lw[:i]], freq[lw[i:]]), i) for i in range(2, len(lw) - 1)), default=(0, 0))
        return w[:best[1]] + " " + w[best[1]:] if best[0] > 20 else w
    out = {}
    for b, g in d[d.book_number.isin(book_numbers)].groupby("book_number"):
        g = g.sort_values(["chapter", "verse_number"])
        out[b] = [(int(c), int(v), re.sub(r"[^\W\d_]+", unglue, t))
                  for c, v, t in zip(g.chapter, g.verse_number, g.latin)]
    return out


def book_texts(book_numbers: set[int]) -> dict[int, str]:
    return {b: " ".join(t for _, _, t in vs) for b, vs in book_verses(book_numbers).items()}


def load_terms(path: pathlib.Path | None = None) -> list[list[str]]:
    out = []
    for l in (path or ROOT / "terms.txt").read_text().splitlines():
        if l.strip() and not l.startswith("#"):
            out.append(tokens(l))
    return out


def boost_terms() -> set[str]:
    """The phrase-boost list as decoders see it (stt_run._terms, GoogleSTT.phrases: terms.txt)."""
    return {" ".join(t) for t in load_terms(ROOT / "terms.txt")}


def uses_boost(model: str) -> bool:
    """Decodes with the boost list: stt_run *-boost entries and Google chirp_2's phrase set
    (chirp_3 runs without biasing, see DECISIONS.md)."""
    return model.endswith("-boost") or model == "google-chirp_2"


def split_terms(tagg: dict[str, list[int]], boosted: set[str]) -> dict:
    """Term accuracy split by boost-list membership. If every scored term is on the boost
    list there is nothing to split: say so instead (term_set = 'boost list')."""
    pct = lambda vs: round(100 * sum(v[1] for v in vs) / max(sum(v[0] for v in vs), 1), 1)
    on = [v for k, v in tagg.items() if k in boosted]
    off = [v for k, v in tagg.items() if k not in boosted]
    if not off:
        return {"term_set": "boost list"}
    return {"term_set": "mixed", "term_acc_boosted": pct(on), "term_acc_unboosted": pct(off),
            "term_n_unboosted": sum(v[0] for v in off)}


def _count(seq: list[str], pat: list[str]) -> int:
    n = len(pat)
    return sum(1 for i in range(len(seq) - n + 1) if seq[i:i + n] == pat)


def term_stats(ref_toks: list[str], hyp_toks: list[str], terms: list[list[str]]) -> dict:
    fk = lambda ts: [fold_diacritics(phon_key(t)) for t in ts]
    ref_f, hyp_f = fk(ref_toks), fk(hyp_toks)
    out = {}
    for t in terms:
        n_ref = _count(ref_f, fk(t))
        if not n_ref:
            continue
        exact = _count(hyp_toks, t)
        loose = _count(hyp_f, fk(t))
        # close misspellings: windows of the same length that fuzzy-match the term
        joined = " ".join(t)
        n = len(t)
        fuzzy = sum(1 for i in range(len(hyp_toks) - n + 1)
                    if hyp_f[i:i + n] != fk(t) and fuzz.ratio(" ".join(hyp_toks[i:i + n]), joined) >= 85)
        hit = min(n_ref, exact)
        near = min(n_ref - hit, max(loose - exact, 0) + fuzzy)
        out[" ".join(t)] = (n_ref, hit, near)
    return out


def load_hyps(manifest: pd.DataFrame) -> dict[str, dict[str, str]]:
    """model -> excerpt -> concatenated text."""
    hyps: dict[str, dict[str, str]] = defaultdict(dict)
    order = manifest.groupby("excerpt")["segment"].apply(list).to_dict()
    for f in sorted((ROOT / "transcripts" / "local").glob("*/bible.jsonl")):
        if f.parent.name.startswith("canary-ast"):          # English output, scored in Phase 2
            continue
        rows = {j["segment"]: j for j in map(json.loads, f.read_text().splitlines()) if j}
        for ex, segs in order.items():
            if all(s in rows for s in segs):
                hyps[f.parent.name][ex] = " ".join(rows[s]["text"] for s in segs)
    for d in sorted((ROOT / "transcripts" / "google").glob("*/")):
        for ex in order:
            p = d / f"{ex}.txt"
            if p.exists():
                hyps[f"google-{d.name}"][ex] = p.read_text()
    return hyps


def speed(model: str) -> float | None:
    f = ROOT / "transcripts" / "local" / model / "bible.jsonl"
    if not f.exists():
        return None
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return sum(r["infer_s"] for r in rows) / max(sum(r["audio_s"] for r in rows), 1e-9)


def excerpt_spans(hk_of: dict[tuple[str, str], list[str]], ex_book: dict[str, int],
                  ref_key: dict[int, list[str]]) -> dict[str, tuple[int, int]]:
    """One reference span per excerpt: where the read passage sits in the whole book.
    Each non-empty hypothesis locates it with a free-ends alignment; the median start and
    end over all models is the span every model is then scored on."""
    starts, ends = defaultdict(list), defaultdict(list)
    for (m, ex), hk in hk_of.items():
        if hk:
            a = align(ref_key[ex_book[ex]], hk, free_ends=True)
            starts[ex].append(a.ref_start); ends[ex].append(a.ref_end)
    return {ex: (int(statistics.median_low(starts[ex])), int(statistics.median_high(ends[ex])))
            for ex in starts}


def excerpt_chapters(man: pd.DataFrame | None = None) -> dict[str, tuple[int, int, int]]:
    """excerpt -> (book, first chapter, last chapter) of the passage it reads, located the
    way main() scores it (median alignment span of every model's transcript)."""
    man = pd.read_csv(ROOT / "work" / "bible" / "manifest.csv") if man is None else man
    ex_book = man.groupby("excerpt")["book_number"].first().to_dict()
    ref_key, key_ch = {}, {}
    for b, vs in book_verses(set(ex_book.values())).items():
        ks = [(c, key_tokens(t, numbers=True)) for c, _, t in vs]
        ref_key[b] = [k for _, kk in ks for k in kk]
        key_ch[b] = [c for c, kk in ks for _ in kk]
    hk_of = {(m, ex): strip_headers(key_tokens(t, numbers=True))
             for m, exs in load_hyps(man).items() for ex, t in exs.items()}
    span = excerpt_spans(hk_of, ex_book, ref_key)
    if set(ex_book) - set(span):
        raise SystemExit(f"no transcripts to locate excerpts {sorted(set(ex_book) - set(span))}")
    return {ex: (ex_book[ex], key_ch[ex_book[ex]][lo], key_ch[ex_book[ex]][hi - 1]) for ex, (lo, hi) in span.items()}


def main() -> None:
    RES.mkdir(exist_ok=True)
    man = pd.read_csv(ROOT / "work" / "bible" / "manifest.csv")
    ex_book = man.groupby("excerpt")["book_number"].first().to_dict()
    books = book_texts(set(ex_book.values()))
    ref_tok = {b: tokens(t) for b, t in books.items()}
    ref_key = {b: key_tokens(t, numbers=True) for b, t in books.items()}
    ref_keyf = {b: [fold_diacritics(k) for k in ks] for b, ks in ref_key.items()}
    terms = load_terms()
    boosted = boost_terms()
    hyps = load_hyps(man)
    # an excerpt a model has no transcript for counts as empty output (all deletions), so a
    # partial run is scored on the same reference words as the others, not on fewer
    hk_of = {(m, ex): strip_headers(key_tokens(exs.get(ex, ""), numbers=True)) for m, exs in hyps.items() for ex in ex_book}
    span = excerpt_spans(hk_of, ex_book, ref_key)
    if set(ex_book) - set(span):
        print(f"WARNING: no transcript at all for {sorted(set(ex_book) - set(span))}; not scored", file=sys.stderr)
    per_ex, summary, term_rows = [], [], []
    n_ex = len(ex_book)
    for m, exs in sorted(hyps.items()):
        strict, folded, tagg = [], [], defaultdict(lambda: [0, 0, 0])
        for ex in sorted(ex_book):
            if ex not in span:                   # no model has text for it: cannot be located
                continue
            text = exs.get(ex, "")
            b = ex_book[ex]
            lo, hi = span[ex]
            hk = hk_of[(m, ex)]
            # every model is scored on the same reference words: empty or truncated output
            # counts as deletions instead of shrinking its own denominator
            s = score(ref_key[b][lo:hi], hk, free_ends=False)
            sf_ = score(ref_keyf[b][lo:hi], [fold_diacritics(k) for k in hk], free_ends=False)
            strict.append(s); folded.append(sf_)
            ts = term_stats(ref_tok[b][lo:hi], tokens(text), terms)
            for k, v in ts.items():
                for i in range(3):
                    tagg[k][i] += v[i]
            per_ex.append({"model": m, "excerpt": ex, "wer": round(s["wer"], 4),
                           "wer_nodiac": round(sf_["wer"], 4), "cer": round(s["cer"], 4), "n_ref": s["n_ref"]})
        cs, cf = corpus_score(strict), corpus_score(folded)
        n_t = sum(v[0] for v in tagg.values())
        rtf = speed(m)
        summary.append({"model": m, "excerpts": f"{len(exs)}/{n_ex}", "wer": round(cs["wer"] * 100, 2),
                        "wer_nodiac": round(cf["wer"] * 100, 2), "cer": round(cs["cer"] * 100, 2),
                        "term_acc": round(100 * sum(v[1] for v in tagg.values()) / max(n_t, 1), 1),
                        "term_near": round(100 * sum(v[2] for v in tagg.values()) / max(n_t, 1), 1),
                        "term_n": n_t, "boost_decoding": "yes" if uses_boost(m) else "no",
                        **split_terms(tagg, boosted), "rtf": round(rtf, 4) if rtf is not None else "",
                        "n_ref_words": cs["n_ref"]})
        for k, (n, h, nr) in sorted(tagg.items()):
            term_rows.append({"model": m, "term": k, "n_ref": n, "hit": h, "near": nr, "miss": n - h - nr})
    summary.sort(key=lambda r: r["wer"])
    for name, rows in [("bible_scores.csv", summary), ("bible_by_excerpt.csv", per_ex), ("bible_terms.csv", term_rows)]:
        if rows:
            with open(RES / name, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
                w.writeheader(); w.writerows(rows)
    print(pd.DataFrame(summary).to_string(index=False))
    if summary and summary[0].get("term_set") == "boost list":
        print("NOTE: term accuracy is measured on terms.txt, which is also the phrase-boost list; "
              "for boost_decoding=yes models it is in-sample.")


if __name__ == "__main__":
    main()
