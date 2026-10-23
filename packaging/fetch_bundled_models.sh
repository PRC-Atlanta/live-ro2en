#!/bin/bash
# Download the three small model files the app bundles (APP_PLAN §1.1 "Small model files") at
# their pins, for a build machine without a dev install (CI). assemble_app.sh calls this only
# when no checked local copy exists; it then checks every file's sha256 against
# requirements/models.lock.txt, so a changed upstream file stops the build.
#
#   packaging/fetch_bundled_models.sh RUNTIME OUT      -> OUT/models/{vad,whistle}/…
#
# Same sources as setup.sh: silero_vad.onnx from its pinned tag (url line), whistle.cact from
# Hugging Face at the pinned commit (hf line), libneedle.dylib = the Needle engine library the
# pinned cactus-needle package fetches (telemetry off).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
RUNTIME="$1"; OUT="$2"
LOCK="$REPO/requirements/models.lock.txt"
lock() { awk -v k="$1" -v r="$2" '$1 == k && $2 == r {print $3}' "$LOCK"; }
mkdir -p "$OUT/models/vad" "$OUT/models/whistle"
echo "fetching the bundled models at their pins into $OUT/models"

f="$OUT/models/vad/silero_vad.onnx"
[ -f "$f" ] || { curl -fsSL --retry 3 -o "$f.partial" "$(lock url models/vad/silero_vad.onnx)" && mv "$f.partial" "$f"; }

f="$OUT/models/whistle/whistle.cact"
rev="$(lock hf Cactus-Compute/whistle)"; [ -n "$rev" ] || { echo "Cactus-Compute/whistle is not pinned"; exit 1; }
[ -f "$f" ] || { curl -fsSL --retry 3 -o "$f.partial" "https://huggingface.co/Cactus-Compute/whistle/resolve/$rev/whistle.cact" && mv "$f.partial" "$f"; }

f="$OUT/models/whistle/libneedle.dylib"
if [ ! -f "$f" ]; then
  tmp="$(mktemp -d)"
  NEEDLE_TELEMETRY=0 DO_NOT_TRACK=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH="$RUNTIME/site/common:$RUNTIME/site/nemo" "$RUNTIME/runtime/bin/python3.12" -s -B -c '
import sys
from needle.agent import fetch
print(fetch.fetch_library(fetch.engine_version(3), sys.argv[1], generation=3))' "$tmp" > "$tmp/path.txt"
  cp "$(tail -1 "$tmp/path.txt")" "$f"
  rm -rf "$tmp"
fi
for p in models/vad/silero_vad.onnx models/whistle/whistle.cact models/whistle/libneedle.dylib; do
  got=$(shasum -a 256 "$OUT/$p" | cut -d' ' -f1)
  [ "$got" = "$(lock sha256 "$p")" ] || { echo "CHECKSUM MISMATCH: $p (got $got; see $LOCK)"; exit 1; }
done
echo "bundled models ok"
