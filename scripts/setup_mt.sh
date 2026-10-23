#!/bin/bash
# Translation runtimes: CTranslate2 (NLLB) + MLX-LM (local LLMs). Logs to logs/setup_mt.log
set -e
cd "$(dirname "$0")/.."
uv venv -q -p 3.12 .venv-mt
uv pip install -p .venv-mt ctranslate2 "transformers<5" sentencepiece torch sacrebleu mlx-lm pandas pyyaml rapidfuzz huggingface_hub protobuf
mkdir -p models/ct2
for m in nllb-200-distilled-1.3B nllb-200-3.3B; do
  if [ ! -f models/ct2/$m/model.bin ]; then
    .venv-mt/bin/ct2-transformers-converter --model facebook/$m --output_dir models/ct2/$m --quantization int8_float16 --force
  fi
done
.venv-mt/bin/python -c "
from huggingface_hub import snapshot_download
for r in ['mlx-community/gemma-4-12B-it-qat-4bit','mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit']:
    print(snapshot_download(r))"
echo SETUP-MT-DONE
