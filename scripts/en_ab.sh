#!/bin/bash
# A/B: Parakeet (default) vs Whistle on the same English recording, offline, with both translators.
cd "$(dirname "$0")/.."
A=${1:-data/samples/en_sermon_sample.wav}
B=$(basename "$A" .wav)
.venv-mt/bin/python scripts/test_en_sermon.py "$A" --recognizer whistle --name "${B}_whistle"
.venv-mt/bin/python scripts/test_en_sermon.py "$A" --recognizer parakeet --name "${B}_parakeet"
.venv-mt/bin/python scripts/test_en_sermon.py "$A" --recognizer whistle --name "${B}_whistle_nllb" --translator nllb:nllb-200-distilled-1.3B
.venv-mt/bin/python scripts/test_en_sermon.py "$A" --recognizer parakeet --name "${B}_parakeet_nllb" --translator nllb:nllb-200-distilled-1.3B
echo EN-AB-DONE
