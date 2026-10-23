#!/bin/bash
# Find settings that keep the first word of every caption within ~6 s (sermon audio, Auto mode).
cd "$(dirname "$0")/.."
A=work/e2e/sermon_live.wav
G=mlx:mlx-community/gemma-4-12B-it-qat-4bit; E=mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit; N=nllb:nllb-200-distilled-1.3B
run() { name=$1; shift; .venv-mt/bin/python src/live/e2e.py --audio "$A" --mode auto --profile quality --name "$name" "$@" > "logs/e2e_$name.log" 2>&1; echo "$name exit=$?"; }
common=(--stt jackrabbit-ctc-lm-b8 --set min_commit_s=0 --set max_commit_s=0 --set hold_min_words=4 --set context_clauses=4)
run v3_gemma_c3.5 --translator "$G" "${common[@]}" --set max_clause_s=3.5 --set max_chunk_s=3.5
run v3_gemma_c4.5 --translator "$G" "${common[@]}" --set max_clause_s=4.5 --set max_chunk_s=4.5
run v3_eurollm_c3.5 --translator "$E" "${common[@]}" --set max_clause_s=3.5 --set max_chunk_s=3.5
run v3_nllb_c4.5 --translator "$N" "${common[@]}" --set max_clause_s=4.5 --set max_chunk_s=4.5
echo SWEEP-DONE
