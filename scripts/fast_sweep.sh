#!/bin/bash
cd "$(dirname "$0")/.."
A=work/e2e/sermon_live.wav
run() { name=$1; shift; .venv-mt/bin/python src/live/e2e.py --audio "$A" --mode auto --profile fast --name "$name" "$@" > "logs/e2e_$name.log" 2>&1; echo "$name exit=$?"; }
c=(--set min_pause_ms=250 --set hold_min_words=4)
run v5_nllb_c3.0 "${c[@]}" --set max_clause_s=3.0 --set max_chunk_s=3.0
run v5_nllb_c2.5 "${c[@]}" --set max_clause_s=2.5 --set max_chunk_s=2.5
echo FAST-DONE
