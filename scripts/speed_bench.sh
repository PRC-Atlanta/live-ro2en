#!/bin/bash
# Speed of the live app on this Mac (recognition + translation). Run on an idle machine with
# the app closed; compare the printed lines between Macs. Takes ~3-5 min.
cd "$(dirname "$0")/.."
APP="/Applications/Live RO2EN.app/Contents/Resources"
if [ -x .venv-nemo/bin/python ] && [ -x .venv-mt/bin/python ]; then
  NEMO=(.venv-nemo/bin/python); MT=(.venv-mt/bin/python)
else
  export PYTHONNOUSERSITE=1 BENCH_SRC="$APP/app/src" LIVE_HOME="$HOME/Library/Application Support/Live RO2EN"
  export HF_HOME="$LIVE_HOME/hf" HF_HUB_OFFLINE=1
  NEMO=(env PYTHONPATH="$APP/site/common:$APP/site/nemo" "$APP/runtime/bin/python3" -s)
  MT=(env PYTHONPATH="$APP/site/common:$APP/site/mt" "$APP/runtime/bin/python3" -s)
fi
for m in jackrabbit-ctc-lm-b8 parakeet-mlx; do "${NEMO[@]}" scripts/speed_bench.py stt "$m" 2>/dev/null | grep '^{'; done
for s in nllb:nllb-200-distilled-1.3B mlx:mlx-community/gemma-4-12B-it-qat-4bit; do
  "${MT[@]}" scripts/speed_bench.py mt "$s" 2>/dev/null | grep '^{'; done
