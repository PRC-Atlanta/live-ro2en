"""Phase 2 translation benchmark (run in .venv-mt).

  .venv-mt/bin/python src/mt_bench.py run nllb:nllb-200-distilled-1.3B [--limit N]
  .venv-mt/bin/python src/mt_bench.py run mlx:mlx-community/gemma-4-12B-it-qat-4bit
  .venv-mt/bin/python src/mt_bench.py score
For every verse in data/mt/bible_verses.jsonl it translates the clean Cornilescu text
(ro_ref) and the STT text (ro_stt) to English, and a subset of KJV verses EN->RO.
Outputs results/mt/<system>.jsonl (resumable) and results/mt_scores.csv with chrF++
vs KJV (verse level and per-excerpt document level), glossary adherence, latency.
Canary direct speech translation (transcripts/local/canary-ast-en) is scored at document
level against the KJV span of each excerpt."""
from __future__ import annotations

import csv
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
sys.path.insert(0, str(pathlib.Path(__file__).parent / "live"))
from live.translate import Glossary, make_translator, timed   # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "mt" / "bible_verses.jsonl"
OUT = ROOT / "results" / "mt"
EN_RO_N = 40


def slug(spec: str) -> str:
    return spec.split(":", 1)[1].split("/")[-1]


def run(spec: str, limit: int = 0) -> None:
    rows = [json.loads(l) for l in DATA.read_text().splitlines()]
    if limit:
        rows = rows[:limit]
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{slug(spec)}.jsonl"
    done = set()
    if path.exists():
        done = {(j["ref"], j["field"]) for j in map(json.loads, path.read_text().splitlines())}
    tr = make_translator(spec)
    gl = Glossary()
    tr.translate("Dumnezeu este bun.", "ro", "en", gl)          # warm-up
    jobs = [(r, f, "ro", "en") for r in rows for f in ("ro_ref", "ro_stt")]
    jobs += [(r, "en_ref", "en", "ro") for r in rows[:EN_RO_N]]
    with open(path, "a") as f:
        for r, field, s, t in jobs:
            if (r["ref"], field) in done:
                continue
            out, dt = timed(tr.translate, r[field], s, t, gl)
            n, ok = gl.adherence(r[field], out, s)
            f.write(json.dumps({"ref": r["ref"], "excerpt": r["excerpt"], "field": field, "src": r[field],
                                "out": out, "sec": round(dt, 4), "gl_n": n, "gl_ok": ok},
                               ensure_ascii=False) + "\n")
            f.flush()
    print("done", spec)


def score() -> None:
    from sacrebleu.metrics import CHRF
    chrf = CHRF(word_order=2)
    verses = {json.loads(l)["ref"]: json.loads(l) for l in DATA.read_text().splitlines()}
    rows = []
    for p in sorted(OUT.glob("*.jsonl")):
        res = [json.loads(l) for l in p.read_text().splitlines()]
        row = {"system": p.stem}
        for field, refk in (("ro_ref", "en_ref"), ("ro_stt", "en_ref"), ("en_ref", "ro_ref")):
            rs = [r for r in res if r["field"] == field and r["ref"] in verses]
            if not rs:
                continue
            hyp = [r["out"] for r in rs]
            ref = [verses[r["ref"]][refk] for r in rs]
            tag = {"ro_ref": "clean", "ro_stt": "stt", "en_ref": "en2ro"}[field]
            row[f"chrf_{tag}"] = round(chrf.corpus_score(hyp, [ref]).score, 2)
            secs = sorted(r["sec"] for r in rs)
            row[f"lat_med_{tag}"] = round(statistics.median(secs), 3)
            row[f"lat_p90_{tag}"] = round(secs[int(0.9 * (len(secs) - 1))], 3)
            n = sum(r["gl_n"] for r in rs)
            row[f"gloss_{tag}"] = round(100 * sum(r["gl_ok"] for r in rs) / max(n, 1), 1)
            if tag == "stt":     # document level, comparable with Canary
                docs = {}
                for r in rs:
                    docs.setdefault(r["excerpt"], [[], []])
                    docs[r["excerpt"]][0].append(r["out"]); docs[r["excerpt"]][1].append(verses[r["ref"]]["en_ref"])
                row["chrf_doc_stt"] = round(chrf.corpus_score([" ".join(h) for h, _ in docs.values()],
                                                              [[" ".join(r) for _, r in docs.values()]]).score, 2)
            row[f"n_{tag}"] = len(rs)
        rows.append(row)
    # Canary direct speech translation: document level per excerpt
    can = ROOT / "transcripts" / "local" / "canary-ast-en" / "bible.jsonl"
    if can.exists():
        import pandas as pd
        man = pd.read_csv(ROOT / "work" / "bible" / "manifest.csv")
        segs = {j["segment"]: j for j in map(json.loads, can.read_text().splitlines())}
        docs_h, docs_r = [], []
        for ex, g in man.groupby("excerpt"):
            refs = [v["en_ref"] for v in verses.values() if v["excerpt"] == ex]
            if not refs or not all(s in segs for s in g.segment):
                continue
            docs_h.append(" ".join(segs[s]["text"] for s in g.segment))
            docs_r.append(" ".join(refs))
        if docs_h:
            lat = sorted(j["infer_s"] for j in segs.values())
            rows.append({"system": "canary-1b-v2-ast (speech->EN)",
                         "chrf_doc_stt": round(chrf.corpus_score(docs_h, [docs_r]).score, 2),
                         "lat_med_stt": round(statistics.median(lat), 3), "n_stt": len(docs_h)})
    keys = sorted({k for r in rows for k in r}, key=lambda k: (k != "system", k))
    with open(ROOT / "results" / "mt_scores.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader(); w.writerows(rows)
    for r in rows:
        print(r)


def sermon_clauses(stt_model: str = "jackrabbit-ctc-lm-b8", n: int = 60) -> list[dict]:
    """~n clauses spread over the sermon set, split the way the live app splits them."""
    from live.segmenter import split_clauses
    f = ROOT / "transcripts" / "local" / stt_model / "sermon.jsonl"
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    cl = []
    for r in rows:
        c, tail = split_clauses(r["text"])
        for x in c + ([tail] if tail else []):
            if len(x.split()) >= 4:
                cl.append({"segment": r["segment"], "src": x})
    step = max(1, len(cl) // n)
    return cl[::step][:n]


def sermon(spec: str) -> None:
    """Sermon clauses: RO->EN, then EN->RO back-translation with the same system."""
    from sacrebleu.metrics import CHRF
    chrf = CHRF(word_order=2)
    tr = make_translator(spec)
    gl = Glossary()
    tr.translate("Dumnezeu este bun.", "ro", "en", gl)
    out = []
    for c in sermon_clauses():
        en, dt = timed(tr.translate, c["src"], "ro", "en", gl)
        back = tr.translate(en, "en", "ro", gl)
        n, ok = gl.adherence(c["src"], en, "ro")
        out.append({**c, "en": en, "back": back, "sec": round(dt, 3), "gl_n": n, "gl_ok": ok,
                    "back_chrf": round(chrf.sentence_score(back, [c["src"]]).score, 1)})
    (OUT / f"sermon_{slug(spec)}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    secs = sorted(r["sec"] for r in out)
    row = {"system": slug(spec), "clauses": len(out),
           "backtrans_chrf": round(chrf.corpus_score([r["back"] for r in out], [[r["src"] for r in out]]).score, 2),
           "glossary_pct": round(100 * sum(r["gl_ok"] for r in out) / max(sum(r["gl_n"] for r in out), 1), 1),
           "glossary_n": sum(r["gl_n"] for r in out),
           "lat_med_s": round(statistics.median(secs), 3), "lat_p90_s": round(secs[int(0.9 * (len(secs) - 1))], 3)}
    f = ROOT / "results" / "mt_sermon.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(row)


def latency(spec: str, n: int = 30) -> None:
    """Quiet-machine per-clause latency on n STT verses (sequential, glossary on)."""
    rows = [json.loads(l) for l in DATA.read_text().splitlines()][:: 5][:n]
    tr = make_translator(spec)
    gl = Glossary()
    tr.translate("Dumnezeu este bun.", "ro", "en", gl)
    ts = sorted(timed(tr.translate, r["ro_stt"], "ro", "en", gl)[1] for r in rows)
    words = sum(len(r["ro_stt"].split()) for r in rows) / len(rows)
    row = {"system": slug(spec), "n": len(ts), "avg_src_words": round(words, 1),
           "median_s": round(statistics.median(ts), 3), "p90_s": round(ts[int(0.9 * (len(ts) - 1))], 3)}
    f = ROOT / "results" / "mt_latency.csv"
    new = not f.exists()
    with open(f, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)
    print(row)


if __name__ == "__main__":
    if sys.argv[1] == "latency":
        latency(sys.argv[2])
    elif sys.argv[1] == "sermon":
        sermon(sys.argv[2])
    elif sys.argv[1] == "run":
        lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else 0
        run(sys.argv[2], lim)
    else:
        score()
