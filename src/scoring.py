"""WER/CER with semi-global alignment (free reference end-gaps), term accuracy,
consensus agreement. Shared by Phase 1 benchmarks, the review rescoring script and
the Phase 4 holdout evaluation."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
from rapidfuzz.distance import Levenshtein


@dataclass
class Align:
    sub: int
    dele: int
    ins: int
    n_ref: int
    ref_start: int      # aligned span in the reference token list
    ref_end: int
    pairs: list         # (ref_tok|None, hyp_tok|None)

    @property
    def errors(self) -> int:
        return self.sub + self.dele + self.ins

    @property
    def wer(self) -> float:
        return self.errors / max(self.n_ref, 1)


def _vocab_ids(ref: list[str], hyp: list[str]):
    vocab: dict[str, int] = {}
    r = np.array([vocab.setdefault(t, len(vocab)) for t in ref], dtype=np.int32)
    h = np.array([vocab.setdefault(t, len(vocab)) for t in hyp], dtype=np.int32)
    return r, h


def _dp_rows(r: np.ndarray, h: np.ndarray, free_ends: bool, keep: bool):
    """Edit-distance DP over hyp rows, vectorised along the reference axis.
    D[i][j] = min(D[i-1][j-1]+sub, D[i-1][j]+1, D[i][j-1]+1)."""
    m = len(r)
    idx = np.arange(m + 1, dtype=np.int64)
    prev = np.zeros(m + 1, dtype=np.int64) if free_ends else idx.copy()
    rows = [prev] if keep else None
    for i in range(1, len(h) + 1):
        t = np.empty(m + 1, dtype=np.int64)
        t[0] = prev[0] + 1
        t[1:] = np.minimum(prev[:-1] + (r != h[i - 1]), prev[1:] + 1)
        cur = idx + np.minimum.accumulate(t - idx)      # ref-deletion chain
        if keep:
            rows.append(cur)
        prev = cur
    return prev, rows


def align(ref: list[str], hyp: list[str], free_ends: bool = True) -> Align:
    """Align hyp to ref. With free_ends, unmatched reference prefix/suffix cost nothing,
    so a partial transcript can be scored against a whole book."""
    if not hyp:
        return Align(0, 0 if free_ends else len(ref), 0, 0 if free_ends else len(ref), 0, 0, [])
    r, h = _vocab_ids(ref, hyp)
    if free_ends and len(r) > 4 * len(h) + 400:
        # locate the end column cheaply, then redo full DP on a window
        last, _ = _dp_rows(r, h, True, False)
        end = int(np.argmin(last))
        lo = max(0, end - 3 * len(h) - 200)
        sub = align(ref[lo:end + 50], hyp, True)
        sub.ref_start += lo
        sub.ref_end += lo
        return sub
    last, rows = _dp_rows(r, h, free_ends, True)
    j = int(np.argmin(last)) if free_ends else len(r)
    end_j = j
    i = len(h)
    S = D = I = 0
    pairs = []
    while i > 0:
        cur = rows[i]
        if j > 0 and cur[j] == rows[i - 1][j - 1] + (r[j - 1] != h[i - 1]):
            if r[j - 1] != h[i - 1]:
                S += 1
            pairs.append((ref[j - 1], hyp[i - 1]))
            i, j = i - 1, j - 1
        elif cur[j] == rows[i - 1][j] + 1:
            I += 1
            pairs.append((None, hyp[i - 1]))
            i -= 1
        else:
            D += 1
            pairs.append((ref[j - 1], None))
            j -= 1
    if not free_ends:
        while j > 0:
            D += 1
            pairs.append((ref[j - 1], None))
            j -= 1
    pairs.reverse()
    start_j = j
    n_ref = end_j - start_j
    return Align(S, D, I, n_ref, start_j, end_j, pairs)


def cer(ref_text: str, hyp_text: str) -> float:
    return Levenshtein.distance(ref_text, hyp_text) / max(len(ref_text), 1)


def score(ref_keys: list[str], hyp_keys: list[str], free_ends: bool = True) -> dict:
    a = align(ref_keys, hyp_keys, free_ends)
    ref_span = " ".join(ref_keys[a.ref_start:a.ref_end])
    return {
        "wer": a.wer, "sub": a.sub, "del": a.dele, "ins": a.ins, "n_ref": a.n_ref,
        "cer": cer(ref_span, " ".join(hyp_keys)), "ref_start": a.ref_start, "ref_end": a.ref_end,
    }


def corpus_score(items: list[dict]) -> dict:
    """Pool per-item score() dicts into corpus WER/CER (weighted by reference size)."""
    n = sum(x["n_ref"] for x in items)
    e = sum(x["sub"] + x["del"] + x["ins"] for x in items)
    c = sum(x["cer"] * x["n_ref"] for x in items)
    return {"wer": e / max(n, 1), "cer": c / max(n, 1), "n_ref": n}


MIN_VOTERS = 3     # rover() of 1-2 hypotheses is just the first one, not a consensus


def rover(hyps: list[list[str]]) -> list[str]:
    """ROVER-style consensus: the hypothesis with the lowest mean WER to the others is the
    backbone; every other hypothesis is aligned to it and each backbone slot (plus the gap
    before it) takes the majority token. Ties keep the backbone. Fewer than MIN_VOTERS
    non-empty hypotheses: returns the first one unchanged."""
    hyps = [h for h in hyps if h]
    if not hyps:
        return []
    if len(hyps) <= 2:
        return list(hyps[0])
    def mean_wer(i):
        return sum(align(hyps[j], hyps[i], free_ends=False).errors / max(len(hyps[j]), 1)
                   for j in range(len(hyps)) if j != i)
    b = min(range(len(hyps)), key=mean_wer)
    back = hyps[b]
    slots = [[t] for t in back]                 # votes per backbone position
    gaps = [[] for _ in range(len(back) + 1)]   # inserted tokens before position k
    for j, h in enumerate(hyps):
        if j == b:
            continue
        a = align(back, h, free_ends=False)
        k = 0
        ins: list[str] = []
        for r, t in a.pairs:
            if r is None:
                ins.append(t)
                continue
            if ins:
                gaps[k].append(" ".join(ins)); ins = []
            slots[k].append(t)                   # t may be None (deletion vote)
            k += 1
        if ins:
            gaps[k].append(" ".join(ins))
    n = len(hyps)
    out: list[str] = []
    for k in range(len(back) + 1):
        if gaps[k]:
            cand = Counter(gaps[k]).most_common(1)[0]
            if cand[1] > n / 2:
                out += cand[0].split()
        if k < len(back):
            c = Counter(slots[k])
            best, cnt = c.most_common(1)[0]
            if cnt == c[back[k]]:
                best = back[k]
            if best is not None:
                out.append(best)
    return out
