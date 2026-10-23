"""Score local models on a set WITHOUT human references (sermons).

  .venv/bin/python src/score_sermons.py [--set sermon] [--silver google-chirp_2]
Per model, per excerpt:
  - silver WER/CER vs Google (only if --silver is given; use it only after Google passed
    the Bible validation, see BENCHMARK.md)
  - consensus WER: vs a ROVER consensus of the OTHER models (leave-one-out)
  - term hit rate: terms.txt terms present in the consensus, spelled exactly by the model;
    'near' = right word, wrong diacritics/close misspelling
  - diacritic accuracy: among the model's words that match a consensus word containing
    ă/â/î/ș/ț after folding, the share spelled with the correct diacritics; plus count of
    wrong cedilla forms (ş/ţ)
Writes results/<set>_noref_scores.csv and results/<set>_side_by_side.md (10 passages)."""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from collections import defaultdict

import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from score_bible import load_terms, term_stats                         # noqa: E402
from scoring import align, corpus_score, rover, score                  # noqa: E402
from textnorm import fold_diacritics, key_tokens, phon_key, tokens     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DIAC = set("ăâîșț")


def load_set(set_name: str) -> tuple[pd.DataFrame, dict[str, dict[str, str]]]:
    man = pd.read_csv(ROOT / "work" / set_name / "manifest.csv")
    order = man.groupby("excerpt")["segment"].apply(list).to_dict()
    hyps: dict[str, dict[str, str]] = defaultdict(dict)
    for f in sorted((ROOT / "transcripts" / "local").glob(f"*/{set_name}.jsonl")):
        if f.parent.name.startswith("canary-ast"):          # English output, not a RO transcript
            continue
        rows = {j["segment"]: j for j in map(json.loads, f.read_text().splitlines()) if j}
        for ex, segs in order.items():
            if all(s in rows for s in segs):
                hyps[f.parent.name][ex] = " ".join(rows[s]["text"] for s in segs)
    for d in sorted((ROOT / "transcripts" / "google").glob("*/")):
        for ex in order:
            p = d / f"{ex}.txt"
            if p.exists():
                hyps[f"google-{d.name}"][ex] = p.read_text().strip()
    return man, hyps


def diacritic_acc(hyp_text: str, cons_tokens: list[str]) -> tuple[int, int, int]:
    """(n_checked, n_correct, n_cedilla) — words whose consensus form has diacritics."""
    cons = {fold_diacritics(t): t for t in cons_tokens if DIAC & set(t)}
    toks = tokens(hyp_text)
    n = ok = 0
    for t in toks:
        f = fold_diacritics(t)
        if f in cons:
            n += 1
            ok += phon_key(t) == phon_key(cons[f])
    return n, ok, sum(hyp_text.count(c) for c in "şţŞŢ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", default="sermon")
    ap.add_argument("--silver", default=None, help="e.g. google-chirp_2")
    ap.add_argument("--exclude", default="", help="comma-separated models to leave out of the consensus")
    a = ap.parse_args()
    man, hyps = load_set(a.set)
    excerpts = sorted(man.excerpt.unique())
    excl = set(filter(None, a.exclude.split(",")))
    voters = [m for m in hyps if not m.startswith("google") and m not in excl
              and all(ex in hyps[m] for ex in excerpts)]
    terms = load_terms()
    rows = []
    for m in sorted(hyps):
        if not all(ex in hyps[m] for ex in excerpts):
            continue
        cons_items, silver_items = [], []
        tagg = [0, 0, 0]
        dn = dok = dced = 0
        for ex in excerpts:
            others = [key_tokens(hyps[o][ex]) for o in voters if o != m]
            others_raw = [tokens(hyps[o][ex]) for o in voters if o != m]
            hk = key_tokens(hyps[m][ex])
            cons_items.append(score(rover(others), hk, free_ends=False))
            cons_raw = rover(others_raw)
            for n_ref, hit, near in term_stats(cons_raw, tokens(hyps[m][ex]), terms).values():
                tagg[0] += n_ref; tagg[1] += hit; tagg[2] += near
            n, ok, ced = diacritic_acc(hyps[m][ex], cons_raw)
            dn += n; dok += ok; dced += ced
            if a.silver and a.silver != m and ex in hyps.get(a.silver, {}):
                silver_items.append(score(key_tokens(hyps[a.silver][ex]), hk, free_ends=False))
        c = corpus_score(cons_items)
        row = {"model": m, "consensus_wer": round(100 * c["wer"], 2), "consensus_cer": round(100 * c["cer"], 2),
               "term_hit": round(100 * tagg[1] / max(tagg[0], 1), 1), "term_near": round(100 * tagg[2] / max(tagg[0], 1), 1),
               "term_n": tagg[0], "diacritic_acc": round(100 * dok / max(dn, 1), 1), "cedilla_chars": dced}
        if silver_items:
            s = corpus_score(silver_items)
            row.update({"silver_wer": round(100 * s["wer"], 2), "silver_cer": round(100 * s["cer"], 2)})
        rows.append(row)
    rows.sort(key=lambda r: r.get("silver_wer", r["consensus_wer"]))
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "model", k))
    with open(out / f"{a.set}_noref_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys); w.writeheader(); w.writerows(rows)
    print(pd.DataFrame(rows).to_string(index=False))
    side_by_side(a.set, man, hyps, [r["model"] for r in rows][:4], a.silver)


def side_by_side(set_name, man, hyps, models, silver, n=10) -> None:
    """10 passages (one segment each, spread over the set) with each model's text."""
    segs = man.iloc[[int(i * (len(man) - 1) / max(n - 1, 1)) for i in range(n)]]
    seg_text = {}
    for m in models + ([silver] if silver and silver not in models else []):
        f = ROOT / "transcripts" / "local" / m / f"{set_name}.jsonl"
        if f.exists():
            seg_text[m] = {j["segment"]: j["text"] for j in map(json.loads, f.read_text().splitlines())}
    lines = [f"# Side-by-side passages ({set_name})\n",
             "Provisional: no human reference. Google text is per excerpt and therefore not split by segment.\n"]
    for _, r in segs.iterrows():
        lines.append(f"\n## {r.segment} ({r.dur_s:.0f} s at {r.abs_start_s:.0f} s of {r.book_stem})\n")
        for m, d in seg_text.items():
            lines.append(f"- **{m}**: {d.get(r.segment, '—')}")
    (ROOT / "results" / f"{set_name}_side_by_side.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
