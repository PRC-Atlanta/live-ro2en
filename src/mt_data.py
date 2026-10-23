"""Build the verse-aligned translation test set from the Bible excerpts.

  .venv/bin/python src/mt_data.py [stt_model]      (default: best local model by Bible WER)
Writes data/mt/bible_verses.jsonl: excerpt, ref (book/chapter/verse), ro_ref (clean
Cornilescu), ro_stt (the STT model's words aligned to that verse), en_ref (KJV)."""
from __future__ import annotations

import json
import pathlib
import sys

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from score_bible import book_verses, load_hyps            # noqa: E402
from scoring import align                                  # noqa: E402
from textnorm import key_tokens, strip_headers_idx, tokens  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
KJV_BOOKS = {1: "Genesis", 19: "Psalms", 40: "Matthew", 43: "John", 49: "Ephesians",
             59: "James", 60: "I Peter", 66: "Revelation of John"}


def main(model: str | None = None) -> None:
    if model is None:
        sc = pd.read_csv(ROOT / "results" / "bible_scores.csv")
        sc = sc[~sc.model.str.startswith("google") & (sc.excerpts.str.split("/").str[0] == sc.excerpts.str.split("/").str[1])]
        model = sc.sort_values("wer").iloc[0]["model"]
    man = pd.read_csv(ROOT / "work" / "bible" / "manifest.csv")
    ex_book = man.groupby("excerpt")["book_number"].first().to_dict()
    verses = book_verses(set(ex_book.values()))
    kjv = pd.read_csv(ROOT / "data" / "ref" / "en" / "KJV.csv")
    kjv_map = {(r.Book, r.Chapter, r.Verse): r.Text for r in kjv.itertuples()}
    missing_books = {b for b in KJV_BOOKS.values() if b not in set(kjv.Book)}
    if missing_books:
        raise SystemExit(f"KJV book names not found: {missing_books}; have e.g. {sorted(set(kjv.Book))[:8]}")
    hyps = load_hyps(man)[model]
    out = []
    for ex, text in sorted(hyps.items()):
        b = ex_book[ex]
        vs = verses[b]
        ref_tok, ref_vid = [], []
        for i, (_, _, t) in enumerate(vs):
            k = tokens(t)
            ref_tok += k
            ref_vid += [i] * len(k)
        # map every token to the whitespace word it came from, so verses can be rebuilt
        # from the model's own words (case + punctuation kept)
        words = text.split()
        tok_word = [wi for wi, w in enumerate(words) for _ in tokens(w)]
        hk_all = key_tokens(text)
        keep = strip_headers_idx(hk_all)
        hk = [hk_all[i] for i in keep]
        a = align(key_tokens(" ".join(t for _, _, t in vs)), hk)
        by_verse: dict[int, list[int]] = {}
        j = a.ref_start
        hi = 0
        last_v = ref_vid[a.ref_start] if a.ref_start < len(ref_vid) else 0
        for r, h in a.pairs:
            if r is not None:
                last_v = ref_vid[j]; j += 1
            if h is not None:
                wi = tok_word[keep[hi]]; hi += 1
                lst = by_verse.setdefault(last_v, [])
                if wi not in lst:
                    lst.append(wi)
        v0, v1 = ref_vid[a.ref_start], ref_vid[max(a.ref_end - 1, a.ref_start)]
        # trim partial first/last verses (excerpts start/end mid-verse)
        for vi in range(v0 + 1, v1):
            c, v, t = vs[vi]
            en = kjv_map.get((KJV_BOOKS[b], c, v))
            if en is None or vi not in by_verse:
                continue
            out.append({"excerpt": ex, "ref": f"{KJV_BOOKS[b]} {c}:{v}", "ro_ref": t,
                        "ro_stt": " ".join(words[w] for w in by_verse[vi]), "en_ref": en})
    dst = ROOT / "data" / "mt"
    dst.mkdir(parents=True, exist_ok=True)
    with open(dst / "bible_verses.jsonl", "w") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"model={model} verses={len(out)} excerpts={len(set(r['excerpt'] for r in out))}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
