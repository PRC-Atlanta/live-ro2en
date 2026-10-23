#!/bin/bash
# One-time setup on an Apple Silicon Mac. Creates five pinned venvs (separate because NeMo,
# MLX-LM and Moondream pin conflicting dependencies), downloads/convert models, and runs tests.
#   ./setup.sh            everything (~35 GB of models)
#   ./setup.sh live       only what the live app needs (STT + translator + VAD)
set -euo pipefail
cd "$(dirname "$0")"
command -v brew >/dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v uv >/dev/null || brew install uv
mkdir -p logs models/vad models/ct2
LOCK=requirements/models.lock.txt          # exact model versions + checksums
lock() { awk -v k="$1" -v r="$2" '$1==k && $2==r {print $3}' "$LOCK"; }
check() {                                  # refuse a file that differs from the recorded one
  want=$(lock sha256 "$1"); got=$(shasum -a 256 "$1" | cut -d' ' -f1)
  [ "$got" = "$want" ] || { echo "CHECKSUM MISMATCH: $1 (got $got, expected $want; see $LOCK)"; exit 1; }
}

mk() { [ -d "$1" ] || uv venv -q -p 3.12 "$1"; uv pip install -q -p "$1" -r "$2"; }
mk .venv          requirements.txt           # core: scoring, Google dev tools, tests
mk .venv-nemo     requirements/nemo.txt      # Jackrabbit / SpeD / Canary + live engine
mk .venv-mt       requirements/mt.txt        # NLLB (CTranslate2), MLX-LM, live orchestrator, web UI
if [ "${1:-}" != "live" ]; then
  mk .venv-mlx       requirements/mlx.txt        # Parakeet / Whisper (MLX) benchmarks
  mk .venv-moondream requirements/moondream.txt  # Parakeet Redux benchmark
fi

[ -f models/vad/silero_vad.onnx ] || curl -sfL -o models/vad/silero_vad.onnx "$(lock url models/vad/silero_vad.onnx)"
check models/vad/silero_vad.onnx
NLLB="nllb-200-distilled-1.3B"; [ "${1:-}" != "live" ] && NLLB="$NLLB nllb-200-3.3B"   # 3.3B: benchmarks only
for m in $NLLB; do
  [ -f models/ct2/$m/model.bin ] || .venv-mt/bin/ct2-transformers-converter --model facebook/$m \
      --revision "$(lock hf facebook/$m)" --output_dir models/ct2/$m --quantization int8_float16 --force
done
.venv-mt/bin/python - <<'PY'
import sys, yaml
from huggingface_hub import hf_hub_download, snapshot_download
rev = {l.split()[1]: l.split()[2] for l in open("requirements/models.lock.txt") if l.startswith("hf ")}
def pinned(repo):                        # never "latest": a new upload must not change the service
    if repo not in rev:
        sys.exit(f"{repo} is not pinned in requirements/models.lock.txt; add its commit first")
    return rev[repo]
cfg = yaml.safe_load(open("config.yaml"))
specs = {cfg["live"].get("translator", "")} | {p.get("translator", "") for p in (cfg.get("profiles") or {}).values()}
for spec in specs:                       # every translator a profile can switch to
    if spec.startswith("mlx:"):
        repo = spec.split(":", 1)[1]
        snapshot_download(repo, revision=pinned(repo))
# speech models (+ NLLB tokenizer, needed offline at runtime)
for repo, files in [("surogate/jackrabbit-110m-ro", ["jackrabbit-110m-ro.nemo", "lm-4gram-ro.nemo"])]:
    for f in files:
        hf_hub_download(repo, f, revision=pinned(repo))
snapshot_download("mlx-community/parakeet-tdt-0.6b-v3", revision=pinned("mlx-community/parakeet-tdt-0.6b-v3"))
from transformers import AutoTokenizer
AutoTokenizer.from_pretrained("facebook/nllb-200-distilled-1.3B", revision=pinned("facebook/nllb-200-distilled-1.3B"))
PY
# Whistle (English STT): fetch the engine + weights ONCE, vendored in models/whistle so the
# runtime never downloads anything (telemetry off for this step too).
mkdir -p models/whistle
if [ ! -f models/whistle/whistle.cact ] || [ ! -f models/whistle/libneedle.dylib ]; then
  export NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1
  .venv-nemo/bin/needle fetch
  .venv-nemo/bin/needle download whistle --out models/whistle
  LIB=$(.venv-nemo/bin/python -c "import os; os.environ['NEEDLE_TELEMETRY']='0'; import needle; print(needle._library_path(3))")
  cp "$LIB" models/whistle/libneedle.dylib
fi
check models/whistle/whistle.cact; check models/whistle/libneedle.dylib   # native code: verify before use
[ "${1:-}" = "live" ] || [ -f data/ref/cornilescu/data/data.parquet ] || .venv/bin/python -c "
from huggingface_hub import hf_hub_download
hf_hub_download('k-mktr/romanian_cornilescu_ro','data/data.parquet',repo_type='dataset',local_dir='data/ref/cornilescu',
                revision='$(lock dataset k-mktr/romanian_cornilescu_ro)')"
mkdir -p logs/services
.venv/bin/python -m pytest -q tests
echo "Setup complete. Next: cp .env.example .env (only for dev Google tools), then see README.md"
