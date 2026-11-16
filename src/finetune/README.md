# Fine-tuning Jackrabbit on the church's own sermon audio

Prepared and smoke-tested, **not run**. Self-training: the recogniser labels the archive's
pause-cut segments, a second, different recogniser (Whisper large-v3) checks each label, and
only close agreements (plus hand-corrected segments) are trained on, gently.
Google-derived text and the Bible audio never enter training; `luigi-mitoi` is the held-out test.

## Pipeline

```bash
export HF_HUB_OFFLINE=1
# 1. Segment + label the archive with the live decoding (resumable; writes work/archive/<stem>/NNNN.wav)
.venv-nemo/bin/python src/batch_transcribe.py --model jackrabbit-ctc-lm-b8
#    segment list for the other recognisers
#    (data/finetune/archive_segments.csv: segment,audio,start,end from the step-1 jsonl files)
# 2. Pseudo-labels with the most accurate offline decoding (CTC + LM beam 32 + terms.txt boost), resumable
.venv-nemo/bin/python src/stt_run.py jackrabbit-ctc-lm-boost data/finetune/archive_segments.csv work/archive archive
# 3. Second opinion (resumable)
.venv-mlx/bin/python src/alt_transcribe.py --model whisper-large-v3-mlx
# 4. Per-segment agreement (no threshold here) -> data/finetune/agreement.jsonl
.venv/bin/python src/finetune/agreement.py
# 5. Manifests: threshold, hold-outs, corrected text first, overlap refusal -> data/finetune/{train,dev,holdout}.jsonl
.venv/bin/python src/finetune/build_manifests.py --max-disagreement 0.10 [--holdout br-random-1 nelu-suciu]
# 6. Check, then train (checkpoints and the final .nemo under models/finetuned/<run>/)
.venv-nemo/bin/python src/finetune/finetune.py --check
.venv-nemo/bin/python src/finetune/finetune.py --out-dir models/finetuned/jackrabbit-st1
.venv-nemo/bin/python src/finetune/finetune.py --out-dir models/finetuned/jackrabbit-st1 --resume   # after a stop
# 7. Evaluate with the live decoding (CTC + 4-gram LM, beam 8, church phrase boost)
.venv-nemo/bin/python src/finetune/eval_checkpoint.py --tag base                     # unmodified model
.venv-nemo/bin/python src/finetune/eval_checkpoint.py --tag st1 \
    --ckpt models/finetuned/jackrabbit-st1/jackrabbit-st1.nemo
```

`eval_checkpoint.py` runs `scripts/stt_replay.py`'s own `offline` and `wer` on each sermon
(output `logs/bench/stt/<tag>-<sermon>-offline.jsonl`, so `scripts/stt_replay.py wer <tag>-luigi-mitoi-offline`
rescores it), then transcribes the Bible set and scores it on the same reference spans as
`results/bible_scores.csv`. Summary rows: `logs/finetune_eval/finetune_eval.csv`.

## Training recipe (finetune.py defaults)

- Tokenizer unchanged, so the 4-gram LM and phrase boosting keep working.
- Encoder: subsampling and the lower 13 of 17 conformer layers frozen and kept in eval mode
  (BatchNorm statistics untouched); top 4 layers train (`--train-top-layers`). Epoch 0 trains
  the decoders only (`--freeze-encoder-epochs 1`). TDT decoder + joint and CTC decoder train.
- Both losses: TDT + CTC (CTC weight 0.3 as in the model; `--ctc-weight`).
- AdamW, peak LR 5e-5, 10 % warm-up, cosine to 5e-6; batch 4 x accumulation 8 (effective 32); 10 epochs; seed 1234.
- Checkpoints every epoch in `<out-dir>/ckpt/` (`last.ckpt` for `--resume`, two best by dev CTC WER);
  per-step seconds and memory in `<out-dir>/steps.jsonl`.
- Apple GPU (MPS): NeMo's TDT loss is CUDA-only, so `tdt_loss.py` supplies a vectorised PyTorch
  TDT loss (checked against NeMo's reference recursion, values and gradients to 1e-6, on CPU and
  MPS); `PYTORCH_ENABLE_MPS_FALLBACK=1` runs the CTC loss on CPU.

## Data rules

- Corrected text (`data/finetune/corrections_clean/`, sources `local` / `local-alt`) takes
  precedence over a pseudo-label for the same segment.
- Google-started corrections (`data/finetune/corrections/`, source `google`) are skipped:
  Google Cloud Service Specific Terms §17(b), see DECISIONS.md 2026-10-22.
- Held-out sermons are excluded by their whole source recording, and every training segment
  of >= 5 s is checked by audio envelope against the held-out recordings (refused on a match).
- Bible, e2e, podcast and live-service recordings are refused outright (evaluation only).
- The Phase 1 sermon excerpts (work/sermon) are reported, not excluded; `--exclude-eval-excerpts`
  drops overlapping segments.

## Licence

Base `surogate/jackrabbit-110m-ro` is CC-BY-NC-4.0: fine for internal church use, not commercial.
