"""Holdout evaluation with the Phase 1 scoring code (same normalisation and WER/CER).

  .venv-nemo/bin/python src/finetune/eval_holdout.py --model models/finetuned/jackrabbit-2026-11-01.nemo
  .venv-nemo/bin/python src/finetune/eval_holdout.py --registry jackrabbit-tdt   # baseline from stt_run
Writes/updates results/holdout_eval.csv (one row per evaluated model)."""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from scoring import corpus_score, score     # noqa: E402
from textnorm import key_tokens             # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help=".nemo file (fine-tuned)")
    ap.add_argument("--registry", help="name from src/stt_run.py, e.g. jackrabbit-tdt")
    ap.add_argument("--manifest", default=str(ROOT / "data/finetune/holdout.jsonl"))
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.manifest) if l.strip()]
    if a.registry:
        import stt_run
        fn = stt_run.MODELS[a.registry]()
        name = a.registry
    else:
        from nemo.collections.asr.models import ASRModel
        m = ASRModel.restore_from(a.model, map_location="cpu")
        m.change_decoding_strategy(decoder_type="rnnt")
        import stt_run
        fn = lambda p: stt_run._nemo_text(m.transcribe([p], batch_size=1, verbose=False))
        name = pathlib.Path(a.model).name
    items = [score(key_tokens(r["text"]), key_tokens(fn(r["audio_filepath"])), free_ends=False) for r in rows]
    c = corpus_score(items)
    out = ROOT / "results" / "holdout_eval.csv"
    new = not out.exists()
    with open(out, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["model", "wer", "cer", "n_words", "n_utts"])
        if new:
            w.writeheader()
        w.writerow({"model": name, "wer": round(100 * c["wer"], 2), "cer": round(100 * c["cer"], 2),
                    "n_words": c["n_ref"], "n_utts": len(rows)})
    print(name, round(100 * c["wer"], 2), round(100 * c["cer"], 2))


if __name__ == "__main__":
    main()
