#!/bin/bash
# Usage: scripts/run_stt_queue.sh <venv> <manifest> <segdir> <set> model1 model2 ...
# Runs models sequentially; each writes transcripts/local/<model>/<set>.jsonl (resumable).
cd "$(dirname "$0")/.."
VENV=$1; MAN=$2; SEG=$3; SET=$4; shift 4
for m in "$@"; do
  echo "[$(date +%H:%M:%S)] START $m"
  $VENV/bin/python src/stt_run.py "$m" "$MAN" "$SEG" "$SET" > "logs/stt_${SET}_${m}.log" 2>&1
  echo "[$(date +%H:%M:%S)] END $m exit=$?  $(tail -n 1 logs/stt_${SET}_${m}.log)"
done
echo QUEUE-DONE
