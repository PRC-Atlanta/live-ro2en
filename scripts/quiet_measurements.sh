#!/bin/bash
# Timing runs that need an otherwise idle machine. Sequential on purpose.
cd "$(dirname "$0")/.."
for m in jackrabbit-ctc-lm-b8 jackrabbit-ctc-lm jackrabbit-ctc-lm-boost jackrabbit-tdt jackrabbit-tdt-boost jackrabbit-stream-tdt sped-tdt canary-asr whisper-turbo-ro-hf; do
  .venv-nemo/bin/python src/stt_latency.py "$m" > "logs/lat_$m.log" 2>&1; echo "stt $m $?"; done
for m in parakeet-mlx whisper-large-v3-mlx; do .venv-mlx/bin/python src/stt_latency.py "$m" > "logs/lat_$m.log" 2>&1; echo "stt $m $?"; done
.venv-moondream/bin/python src/stt_latency.py parakeet-redux > logs/lat_redux.log 2>&1; echo "stt redux $?"
for s in nllb:nllb-200-distilled-1.3B nllb:nllb-200-3.3B mlx:mlx-community/gemma-4-12B-it-qat-4bit mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit; do
  .venv-mt/bin/python src/mt_bench.py latency "$s" > logs/mtlat_$(basename "${s#*:}").log 2>&1; echo "mt $s $?"; done
for s in mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit mlx:mlx-community/gemma-4-12B-it-qat-4bit nllb:nllb-200-distilled-1.3B; do
  n=$(basename "${s#*:}")
  .venv-mt/bin/python src/live/e2e.py --audio work/bible/excerpts/49-Efes_00.flac --mode ro-en --translator "$s" --name "efes_roen_$n" > "logs/e2e_$n.log" 2>&1; echo "e2e efes $n $?"
  .venv-mt/bin/python src/live/e2e.py --mixed --translator "$s" --name "mixed_auto_$n" > "logs/e2e_mixed_$n.log" 2>&1; echo "e2e mixed $n $?"
done
echo QUIET-DONE
