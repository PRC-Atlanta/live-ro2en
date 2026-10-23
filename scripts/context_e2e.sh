#!/bin/bash
cd "$(dirname "$0")/.."
.venv-mt/bin/python src/live/e2e.py --audio work/e2e/sermon_live.wav --mode auto --profile quality --name v6_ro_ctx6_gemma > logs/e2e_v6_ro.log 2>&1; echo "ro $?"
.venv-mt/bin/python scripts/test_en_sermon.py data/samples/EN_Alex_T_10min.wav --recognizer parakeet --name EN_Alex_T_10min_parakeet_ctx6; echo "en $?"
echo CTX-DONE
