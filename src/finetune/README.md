# Fine-tuning on corrected sermon data

Built and checked, **not run** (no training happens in this iteration).
The training data is our own corrected sermon audio only. The Bible audio is evaluation-only:
`build_manifests.py` refuses any row that points at it.

## Pipeline

```bash
# 1. Transcribe the whole archive with the chosen local model (resumable)
.venv-nemo/bin/python src/batch_transcribe.py --model jackrabbit-ctc-lm-b8

# 2. (optional) Google reference transcripts for the archive, within the 600-min cap
.venv/bin/python src/google_stt.py submit Audio/*.mp3 --model chirp_3 --tag sermons
.venv/bin/python src/google_stt.py poll

# 3. Correct transcripts in the browser: http://127.0.0.1:8766
.venv-mt/bin/python src/correct_tool.py --model jackrabbit-ctc-lm-b8 --google chirp_3
#    then click "Export manifest" -> data/finetune/manifest_google.jsonl (or manifest_clean.jsonl with --google none)

# 4. Train/holdout split by sermon
.venv/bin/python src/finetune/build_manifests.py

# 5. Validate the setup without training, then train
.venv-nemo/bin/python src/finetune/finetune.py --check
.venv-nemo/bin/python src/finetune/finetune.py --epochs 20 --freeze-encoder-epochs 3

# 6. Holdout evaluation (same scoring code as Phase 1)
.venv-nemo/bin/python src/finetune/eval_holdout.py --registry jackrabbit-tdt
.venv-nemo/bin/python src/finetune/eval_holdout.py --model models/finetuned/jackrabbit-<date>.nemo
```

## Data format

NeMo ASR manifests, one JSON object per line:

```json
{"audio_filepath": "/abs/path/work/archive/<sermon>/0001.wav", "duration": 12.3, "text": "Frați și surori, ..."}
```

`manifest_all.jsonl` carries extra provenance fields (`sermon`, `source`, `human_edited`,
`pending_terms_check`). `build_manifests.py` drops them.

## Google-derived text: not for training

Google Cloud Service Specific Terms §17(b) ("Use Restrictions for AI/ML Services";
Speech-to-Text is an AI/ML Service) say customers will not use AI/ML Service output "to …
create or improve models similar to a Google Model". Training a Romanian speech recognizer
on Chirp transcripts falls squarely under that. Therefore every segment whose starting text
came from Google is flagged `"pending_terms_check": true`, **even after human edits**, and
`build_manifests.py` excludes it. Such segments remain fine as an evaluation reference.

For training data, correct segments that start from the local model's text
(`correct_tool.py --google none`; highlights come from Whisper). Highlights can come from a second local model
instead of Google.

## Recipe notes

- Base: `surogate/jackrabbit-110m-ro` (CC-BY-NC-4.0: fine for internal church use, not for
  commercial use). Alternative: `--base sped` (Apache-2.0).
- The tokenizer is unchanged, so the 4-gram LM and phrase boosting keep working.
- How much data: expect gains from about 3–5 h of corrected speech. With less, use
  `--freeze-encoder-epochs` and a low LR (5e-5 is the default).
- Hardware: Apple GPU (MPS) at fp32. A 110M model with batch 8 × accumulation 4 fits easily
  in 36 GB of RAM. Expect hours per epoch-hour of audio, not minutes.
- Always compare against the baseline row in `results/holdout_eval.csv` before deploying.
  Also re-run the Bible benchmark (`src/score_bible.py`) to check for regressions on read speech.
