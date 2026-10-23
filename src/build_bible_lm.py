"""Build a token-level 4-gram ARPA LM from the Cornilescu text for Jackrabbit CTC beam search
(NeMo NGPU-LM format: each SentencePiece id is written as chr(id + 100)).

  .venv-nemo/bin/python src/build_bible_lm.py
Held-out: every (book, chapter) touched by the Bible test excerpts, +/-1 chapter, is
excluded, so the Bible WER of jackrabbit-ctc-biblelm is not inflated by memorised text.
Estimator: interpolated absolute discounting (D = 0.75) written as a backoff ARPA, so
seen n-grams carry the interpolated probability and gamma(h) = D * N1+(h.) / c(h.) is
the backoff weight. Output: models/lm/lm-4gram-ro-bible.arpa"""
from __future__ import annotations

import json
import math
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
OFFSET, ORDER, D = 100, 4, 0.75
BOS, EOS, UNK = "<s>", "</s>", "<unk>"
KJV2NUM = {"Genesis": 1, "Psalms": 19, "Matthew": 40, "John": 43, "Ephesians": 49, "James": 59,
           "I Peter": 60, "Revelation of John": 66}


def held_out() -> set[tuple[int, int]]:
    out = set()
    for l in (ROOT / "data/mt/bible_verses.jsonl").read_text().splitlines():
        r = json.loads(l)
        book, cv = r["ref"].rsplit(" ", 1)
        b, c = KJV2NUM[book], int(cv.split(":")[0])
        out |= {(b, c - 1), (b, c), (b, c + 1)}
    return out


def main() -> None:
    from score_bible import book_verses
    from stt_run import _nemo_restore
    m = _nemo_restore("surogate/jackrabbit-110m-ro", "jackrabbit-110m-ro.nemo")
    tok = m.tokenizer
    V = tok.vocab_size
    excl = held_out()
    verses = book_verses(set(range(1, 67)))
    sents, n_ex = [], 0
    for b, vs in verses.items():
        for c, v, t in vs:
            if (b, c) in excl:
                n_ex += 1; continue
            sents.append([BOS] + [chr(i + OFFSET) for i in tok.text_to_ids(t)] + [EOS])
    print(f"verses used={len(sents)} excluded={n_ex} vocab={V}", flush=True)
    counts = [Counter() for _ in range(ORDER + 1)]
    for s in sents:
        for n in range(1, ORDER + 1):
            for i in range(len(s) - n + 1):
                g = tuple(s[i:i + n])
                if n == 1 and g[0] == BOS:
                    continue
                counts[n][g] += 1
    # history statistics: c(h.) and N1+(h.)
    hist_tot = [defaultdict(int) for _ in range(ORDER + 1)]
    hist_types = [defaultdict(int) for _ in range(ORDER + 1)]
    for n in range(2, ORDER + 1):
        for g, c in counts[n].items():
            hist_tot[n][g[:-1]] += c
            hist_types[n][g[:-1]] += 1
    vocab = [chr(i + OFFSET) for i in range(V)] + [EOS]
    N = sum(counts[1].values())
    seen1 = len(counts[1])
    uni_floor = D * seen1 / N / (len(vocab) + 1)          # +1 for <unk>
    prob: list[dict] = [dict() for _ in range(ORDER + 1)]
    for w in vocab:
        prob[1][(w,)] = max(counts[1].get((w,), 0) - D, 0) / N + uni_floor
    prob[1][(UNK,)] = uni_floor

    def p_lower(g: tuple) -> float:
        n = len(g)
        if n == 1:
            return prob[1].get(g, uni_floor)
        if g in prob[n]:
            return prob[n][g]
        h = g[:-1]
        gamma = D * hist_types[n][h] / hist_tot[n][h] if hist_tot[n].get(h) else 1.0
        return gamma * p_lower(g[1:])

    for n in range(2, ORDER + 1):
        for g, c in counts[n].items():
            h = g[:-1]
            ct = hist_tot[n][h]
            gamma = D * hist_types[n][h] / ct
            prob[n][g] = max(c - D, 0) / ct + gamma * p_lower(g[1:])
    out = ROOT / "models" / "lm" / "lm-4gram-ro-bible.arpa"
    out.parent.mkdir(parents=True, exist_ok=True)

    def bow(g: tuple) -> str:
        n = len(g) + 1
        if n > ORDER or g not in hist_tot[n]:
            return ""
        return f"\t{math.log10(D * hist_types[n][g] / hist_tot[n][g]):.6f}"
    with open(out, "w", encoding="utf-8") as f:
        sizes = [len(prob[1]) + 1] + [len(prob[n]) for n in range(2, ORDER + 1)]
        f.write("\\data\\\n" + "".join(f"ngram {i + 1}={s}\n" for i, s in enumerate(sizes)) + "\n")
        f.write("\\1-grams:\n")
        f.write(f"-99\t{BOS}{bow((BOS,))}\n")
        for g, p in prob[1].items():
            f.write(f"{math.log10(p):.6f}\t{g[0]}{bow(g)}\n")
        for n in range(2, ORDER + 1):
            f.write(f"\n\\{n}-grams:\n")
            for g, p in prob[n].items():
                f.write(f"{math.log10(p):.6f}\t{' '.join(g)}{bow(g)}\n")
        f.write("\n\\end\\\n")
    print("wrote", out, "ngrams:", sizes)


if __name__ == "__main__":
    main()
