#!/bin/bash
# A/B: Parakeet (default) vs Whistle on the same English recording, offline, with both translators.
cd "$(dirname "$0")/.."
A=${1:-data/samples/en_sermon_sample.wav}
.venv-mt/bin/python scripts/test_en_sermon.py $A --recognizer whistle --name $(basename $A .wav)_whistle
.venv-mt/bin/python scripts/test_en_sermon.py $A --recognizer parakeet --name $(basename $A .wav)_parakeet
.venv-mt/bin/python scripts/test_en_sermon.py $A --recognizer whistle --name $(basename $A .wav)_whistle_nllb --translator nllb:nllb-200-distilled-1.3B
.venv-mt/bin/python scripts/test_en_sermon.py $A --recognizer parakeet --name $(basename $A .wav)_parakeet_nllb --translator nllb:nllb-200-distilled-1.3B
echo EN-AB-DONE
