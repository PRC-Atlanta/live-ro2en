#!/bin/bash
cd "$(dirname "$0")/.."
A=work/e2e/sermon_live.wav
G=mlx:mlx-community/gemma-4-12B-it-qat-4bit; N=nllb:nllb-200-distilled-1.3B
run() { name=$1; shift; .venv-mt/bin/python src/live/e2e.py --audio "$A" --mode auto --profile quality --name "$name" "$@" > "logs/e2e_$name.log" 2>&1; echo "$name exit=$?"; }
c=(--stt jackrabbit-ctc-lm-b8 --set min_commit_s=0 --set max_commit_s=0 --set hold_min_words=4 --set context_clauses=4 --set min_pause_ms=250)
run v4_gemma_c3.0 --translator "$G" "${c[@]}" --set max_clause_s=3.0 --set max_chunk_s=3.0
run v4_gemma_c2.5 --translator "$G" "${c[@]}" --set max_clause_s=2.5 --set max_chunk_s=2.5
run v4_nllb_c3.5 --translator "$N" "${c[@]}" --set max_clause_s=3.5 --set max_chunk_s=3.5
echo SWEEP2-DONE
