#!/bin/bash
# Everything that needs sermon recordings, in order. Safe to re-run (each step resumes).
#   scripts/run_sermon_pipeline.sh            # local models + scoring + review packet
#   GOOGLE=chirp_3 scripts/run_sermon_pipeline.sh   # also send sermon excerpts to Google
set -uo pipefail
cd "$(dirname "$0")/.."
ls Audio/* >/dev/null 2>&1 || { echo "No files in ./Audio — add sermon recordings first."; exit 1; }
.venv/bin/python src/sermon_set.py --minutes 25 || exit 1
M=work/sermon/manifest.csv; S=work/sermon/segments
if [ -n "${GOOGLE:-}" ]; then      # dev-only reference transcripts, counted against the cap
  .venv/bin/python src/google_stt.py submit $(ls Audio/* | grep -v "^Audio/EN") --model "$GOOGLE" --tag sermons   # whole recordings, cut later by word timings
fi
scripts/run_stt_queue.sh .venv-nemo $M $S sermon jackrabbit-tdt jackrabbit-tdt-boost jackrabbit-ctc-lm jackrabbit-ctc-lm-boost sped-tdt canary-asr
scripts/run_stt_queue.sh .venv-mlx $M $S sermon parakeet-mlx whisper-large-v3-mlx
scripts/run_stt_queue.sh .venv-moondream $M $S sermon parakeet-redux
SILVER=""; [ -n "${GOOGLE:-}" ] && SILVER="--silver google-$GOOGLE"
[ -n "${GOOGLE:-}" ] && .venv/bin/python src/google_stt.py poll    # waits for Google (Dynamic Batch: minutes to hours)
[ -n "${GOOGLE:-}" ] && .venv/bin/python src/google_excerpts.py --set sermon --model "$GOOGLE"
.venv/bin/python src/score_sermons.py --set sermon $SILVER --exclude jackrabbit-tdt,jackrabbit-tdt-boost,jackrabbit-ctc-lm-boost,parakeet-redux   # one voter per model family
REF=${GOOGLE:+google-$GOOGLE}; REF=${REF:-jackrabbit-ctc-lm-boost}
.venv/bin/python src/review_packet.py --set sermon --ref "$REF" --local jackrabbit-tdt-boost --minutes 10
echo "Sermon pipeline done: results/sermon_noref_scores.csv, results/sermon_side_by_side.md, review_packet/"
