#!/bin/bash
# Download the small model file the app bundles (APP_PLAN §1.1 "Small model files") at its
# pin, for a build machine without a dev install (CI). assemble_app.sh calls this only when
# no checked local copy exists; it then checks the file's sha256 against
# requirements/models.lock.txt, so a changed upstream file stops the build.
#
#   packaging/fetch_bundled_models.sh OUT      -> OUT/models/vad/silero_vad.onnx
#
# Same source as setup.sh: silero_vad.onnx from its pinned tag (url line).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
OUT="$1"
LOCK="$REPO/requirements/models.lock.txt"
lock() { awk -v k="$1" -v r="$2" '$1 == k && $2 == r {print $3}' "$LOCK"; }
mkdir -p "$OUT/models/vad"
echo "fetching the bundled models at their pins into $OUT/models"

f="$OUT/models/vad/silero_vad.onnx"
[ -f "$f" ] || { curl -fsSL --retry 3 -o "$f.partial" "$(lock url models/vad/silero_vad.onnx)" && mv "$f.partial" "$f"; }

for p in models/vad/silero_vad.onnx; do
  got=$(shasum -a 256 "$OUT/$p" | cut -d' ' -f1)
  [ "$got" = "$(lock sha256 "$p")" ] || { echo "CHECKSUM MISMATCH: $p (got $got; see $LOCK)"; exit 1; }
done
echo "bundled models ok"
