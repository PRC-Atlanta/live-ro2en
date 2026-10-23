#!/bin/bash
# Everything that needs sermon recordings, in order. Safe to re-run (each step resumes).
#   scripts/run_sermon_pipeline.sh            # local models + scoring + review packet
#   GOOGLE=chirp_3 scripts/run_sermon_pipeline.sh   # also send sermon excerpts to Google
set -uo pipefail
cd "$(dirname "$0")/.."
shopt -s nullglob
AUDIO=(Audio/*)
[ ${#AUDIO[@]} -gt 0 ] || { echo "No files in ./Audio — add sermon recordings first."; exit 1; }
.venv/bin/python src/sermon_set.py --minutes 25 || exit 1
M=work/sermon/manifest.csv; S=work/sermon/segments
if [ -n "${GOOGLE:-}" ]; then      # dev-only reference transcripts, counted against the cap
  RO=(); for f in "${AUDIO[@]}"; do [[ $f == Audio/EN* ]] || RO+=("$f"); done      # English recordings are not sermons here
  .venv/bin/python src/google_stt.py submit ${RO[@]+"${RO[@]}"} --model "$GOOGLE" --tag sermons   # whole recordings, cut later by word timings
fi
FAIL=0
scripts/run_stt_queue.sh .venv-nemo "$M" "$S" sermon jackrabbit-tdt jackrabbit-tdt-boost jackrabbit-ctc-lm jackrabbit-ctc-lm-boost sped-tdt canary-asr || FAIL=1
scripts/run_stt_queue.sh .venv-mlx "$M" "$S" sermon parakeet-mlx whisper-large-v3-mlx || FAIL=1
scripts/run_stt_queue.sh .venv-moondream "$M" "$S" sermon parakeet-redux || FAIL=1
SILVER=(); [ -n "${GOOGLE:-}" ] && SILVER=(--silver "google-$GOOGLE")   # ${a[@]+...} below: bash 3.2 + set -u rejects empty arrays
if [ -n "${GOOGLE:-}" ]; then
  .venv/bin/python src/google_stt.py poll || FAIL=1    # waits for Google (Dynamic Batch: minutes to hours)
  .venv/bin/python src/google_excerpts.py --set sermon --model "$GOOGLE" || FAIL=1
fi
.venv/bin/python src/score_sermons.py --set sermon ${SILVER[@]+"${SILVER[@]}"} --exclude jackrabbit-tdt,jackrabbit-tdt-boost,jackrabbit-ctc-lm-boost,parakeet-redux || FAIL=1   # one voter per model family
REF=${GOOGLE:+google-$GOOGLE}; REF=${REF:-jackrabbit-ctc-lm-boost}
.venv/bin/python src/review_packet.py --set sermon --ref "$REF" --local jackrabbit-tdt-boost --minutes 10 || FAIL=1
if [ "$FAIL" -ne 0 ]; then
  echo "SERMON PIPELINE INCOMPLETE: a step failed (see above); results may be missing or stale"; exit 1
fi
echo "Sermon pipeline done: results/sermon_noref_scores.csv, results/sermon_side_by_side.md, review_packet/"
