#!/bin/bash
# Usage: scripts/run_stt_queue.sh <venv> <manifest> <segdir> <set> model1 model2 ...
# Runs models sequentially; each writes transcripts/local/<model>/<set>.jsonl (resumable).
cd "$(dirname "$0")/.."
FAILED=()
LOGS=${LOGS:-logs}                       # per-model logs (tests point this elsewhere)
VENV=$1; MAN=$2; SEG=$3; SET=$4; shift 4
for m in "$@"; do
  echo "[$(date +%H:%M:%S)] START $m"
  "$VENV/bin/python" src/stt_run.py "$m" "$MAN" "$SEG" "$SET" > "$LOGS/stt_${SET}_${m}.log" 2>&1
  rc=$?                                  # capture before $(date) below resets $?
  echo "[$(date +%H:%M:%S)] END $m exit=$rc  $(tail -n 1 "$LOGS/stt_${SET}_${m}.log")"
  [ $rc -eq 0 ] || FAILED+=("$m")
done
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "QUEUE-FAILED: ${FAILED[*]} (see $LOGS/stt_${SET}_<model>.log)"; exit 1
fi
echo QUEUE-DONE
