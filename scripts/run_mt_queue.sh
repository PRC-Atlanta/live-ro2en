#!/bin/bash
# Run every translation system over data/mt/bible_verses.jsonl sequentially, then score.
cd "$(dirname "$0")/.."
FAILED=()
for s in nllb:nllb-200-distilled-1.3B nllb:nllb-200-3.3B mlx:mlx-community/gemma-4-12B-it-qat-4bit mlx:mlx-community/EuroLLM-22B-Instruct-2512-mlx-4bit; do
  echo "[$(date +%H:%M:%S)] START $s"
  .venv-mt/bin/python src/mt_bench.py run "$s" > "logs/mt_$(basename "${s#*:}").log" 2>&1
  rc=$?                                  # capture before $(date) below resets $?
  echo "[$(date +%H:%M:%S)] END $s exit=$rc"
  [ $rc -eq 0 ] || FAILED+=("$s")
done
.venv-mt/bin/python src/mt_bench.py score > logs/mt_score.log 2>&1 || { echo "MT scoring failed: logs/mt_score.log"; exit 1; }
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "MT-QUEUE-FAILED: ${FAILED[*]} (scored without them)"; exit 1
fi
echo MT-QUEUE-DONE
