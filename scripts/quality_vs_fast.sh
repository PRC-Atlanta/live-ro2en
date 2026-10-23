#!/bin/bash
# Same 5 min of real sermon audio through fast vs quality profiles (run on an idle machine).
cd "$(dirname "$0")/.."
A=work/e2e/sermon_live.wav
run() { .venv-mt/bin/python src/live/e2e.py --audio $A --mode auto --profile $1 ${2:+--translator $2} --name $3 > logs/e2e_$3.log 2>&1; echo "$3 exit=$?"; }
run fast "" v2_fast_nllb
run quality "" v2_quality_gemma
run quality mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit v2_quality_eurollm
run quality nllb:nllb-200-distilled-1.3B v2_quality_nllb
echo QVF-DONE
