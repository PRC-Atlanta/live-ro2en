#!/bin/bash
cd "$(dirname "$0")/.."
for s in nllb:nllb-200-distilled-1.3B nllb:nllb-200-3.3B mlx:mlx-community/gemma-4-12B-it-qat-4bit mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit; do
  .venv-mt/bin/python src/mt_bench.py sermon $s > logs/mtsermon_$(basename ${s#*:}).log 2>&1; echo "mt $s $?"; done
for s in nllb:nllb-200-distilled-1.3B mlx:mlx-community/gemma-4-12B-it-qat-4bit; do
  n=$(basename ${s#*:})
  .venv-mt/bin/python src/live/e2e.py --audio work/e2e/sermon_live.wav --mode auto --translator $s --name sermon_auto_$n > logs/e2e_sermon_$n.log 2>&1; echo "e2e $n $?"; done
echo SERMON-MT-E2E-DONE
