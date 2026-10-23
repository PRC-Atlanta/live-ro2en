"""Holdout evaluation with the Phase 1 scoring code (same normalisation and WER/CER).

  .venv-nemo/bin/python src/finetune/eval_holdout.py --model models/finetuned/jackrabbit-2026-11-01.nemo
  .venv-nemo/bin/python src/finetune/eval_holdout.py --model ft.nemo --decoder tdt-greedy
  .venv-nemo/bin/python src/finetune/eval_holdout.py --registry jackrabbit-ctc-lm-b8   # baseline, same decoding as the default
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

# --decoder -> stt_run.set_decoding args; mirrors the jackrabbit-* registry entries
DECODERS = {"tdt-greedy": ("rnnt", None, 32),                  # = jackrabbit-tdt
            "ctc-greedy": ("ctc", None, 32),                   # = jackrabbit-ctc
            "ctc-lm-b8": ("ctc", "lm-4gram-ro.nemo", 8),       # = jackrabbit-ctc-lm-b8 (live default)
            "ctc-lm-b32": ("ctc", "lm-4gram-ro.nemo", 32)}     # = jackrabbit-ctc-lm
LIVE_DECODER = "ctc-lm-b8"


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--model", help=".nemo file (fine-tuned)")
    g.add_argument("--registry", help="name from src/stt_run.py, e.g. jackrabbit-ctc-lm-b8")
    ap.add_argument("--decoder", choices=sorted(DECODERS), default=LIVE_DECODER,
                    help="decoding for --model; compare against the --registry entry that decodes the same way")
    ap.add_argument("--manifest", default=str(ROOT / "data/finetune/holdout.jsonl"))
    return ap.parse_args(argv)


def main() -> None:
    a = parse_args()
    rows = [json.loads(l) for l in open(a.manifest) if l.strip()]
    if a.registry:
        import stt_run
        fn = stt_run.MODELS[a.registry]()
        name = a.registry
        print("decoder: as registered in stt_run for", a.registry, flush=True)
    else:
        from nemo.collections.asr.models import ASRModel
        import stt_run
        m = ASRModel.restore_from(a.model, map_location="cpu")
        m.eval()
        stt_run.set_decoding(m, *DECODERS[a.decoder])
        fn = lambda p: stt_run._nemo_text(m.transcribe([p], batch_size=1, verbose=False))
        name = f"{pathlib.Path(a.model).name} [{a.decoder}]"
        print("decoder:", a.decoder, DECODERS[a.decoder], flush=True)
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
