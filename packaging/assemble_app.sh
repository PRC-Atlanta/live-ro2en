#!/bin/bash
# Assemble the distributable "Live RO2EN.app" (unsigned contents; packaging/sign.sh signs it).
#
#   packaging/assemble_app.sh [RUNTIME] [APP]
#       RUNTIME  packaging/build_runtime.sh output   default build/runtime
#       APP      the .app to (re)create              default "build/dist/Live RO2EN.app"
#
# Layout (docs/app_contract.md "Bundle layout", what LaunchConfig.resolve() looks for):
#   Contents/MacOS/Live RO2EN                 the Swift shell (macos/build.sh)
#   Contents/Resources/runtime/bin/python3.12 python-build-standalone (from RUNTIME)
#   Contents/Resources/site/{common,mt,nemo}  packages (from RUNTIME)
#   Contents/Resources/app/                   the code root (paths.REPO = app/):
#       src/live/…, the src/*.py modules it imports, defaults.yaml, glossary.csv,
#       keywords_en.txt, terms.txt, requirements/models.lock.txt   (packaging/app_files.py)
#       models/vad/silero_vad.onnx, models/whistle/{libneedle.dylib,whistle.cact}  (bundled models)
#       data/samples/<BUNDLE_SAMPLES>         rehearsal recordings
#
# Inputs that are not in git:
#   MODELS_DIR       where the three bundled model files are; default: <repo>/models, then the
#                    main checkout's models/ (for a git worktree), else they are downloaded at
#                    their pins (packaging/fetch_bundled_models.sh). Always checked against the
#                    sha256 lines in requirements/models.lock.txt.
#   SAMPLES_DIR      where the rehearsal recordings are (default: same search as MODELS_DIR, data/samples)
#   BUNDLE_SAMPLES   which of them to ship (default "en_sermon_sample.wav", a synthetic reading).
#                    Never add a church recording here: the app is distributed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
RUNTIME="${1:-$REPO/build/runtime}"
APP="${2:-$REPO/build/dist/Live RO2EN.app}"
BUNDLE_SAMPLES="${BUNDLE_SAMPLES-en_sermon_sample.wav}"
LOCK="$REPO/requirements/models.lock.txt"
say() { printf '\n== %s\n' "$*"; }
die() { echo "assemble_app: $*" >&2; exit 1; }

[ -x "$RUNTIME/runtime/bin/python3.12" ] || die "no runtime at $RUNTIME (run: make runtime)"
for s in common mt nemo; do [ -d "$RUNTIME/site/$s" ] || die "missing $RUNTIME/site/$s"; done
RUNTIME="$(cd "$RUNTIME" && pwd)"
PY="$RUNTIME/runtime/bin/python3.12"
mkdir -p "$(dirname "$APP")"; APP="$(cd "$(dirname "$APP")" && pwd)/$(basename "$APP")"
case "$APP" in *.app) ;; *) die "APP must end in .app: $APP";; esac
RES="$APP/Contents/Resources"

# --- 1. the Swift shell ----------------------------------------------------------------------
say "Swift shell (macos/build.sh)"
rm -rf "$APP"
APP_OUT="$APP" "$REPO/macos/build.sh" release

# --- 2. runtime + site packages ----------------------------------------------------------------
say "runtime and site packages"
ditto "$RUNTIME/runtime" "$RES/runtime"
ditto "$RUNTIME/site" "$RES/site"
# build-only leftovers that must not be sealed into the bundle
find "$RES/runtime" "$RES/site" -name '.DS_Store' -delete
find "$RES/runtime" "$RES/site" -type d -name __pycache__ -empty -delete 2>/dev/null || true

# --- 3. the app code -----------------------------------------------------------------------------
say "app code (packaging/app_files.py)"
LIST="$(mktemp)"; trap 'rm -f "$LIST"' EXIT
"$PY" -I "$HERE/app_files.py" "$REPO" > "$LIST"
mkdir -p "$RES/app"
rsync -a --files-from="$LIST" "$REPO/" "$RES/app/"
echo "  $(wc -l < "$LIST" | tr -d ' ') files; src/*.py: $(grep -E '^src/[^/]+\.py$' "$LIST" | sed 's|src/||' | tr '\n' ' ')"

# --- 4. bundled models and rehearsal samples (git-ignored) -------------------------------------
say "bundled models"
lock_sha() { awk -v p="$1" '$1 == "sha256" && $2 == p {print $3}' "$LOCK"; }
MODEL_FILES="models/vad/silero_vad.onnx models/whistle/libneedle.dylib models/whistle/whistle.cact"
main_checkout() {   # the main working tree of a git worktree (its .venv, models/ and samples)
  local common; common=$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || return 0
  [ "$(basename "$common")" = .git ] && dirname "$common"
}
MAIN="$(main_checkout || true)"
models_ok() {   # $1 = a folder holding models/…: all three files present with the pinned sha256
  local f
  for f in $MODEL_FILES; do
    [ -f "$1/$f" ] && [ "$(shasum -a 256 "$1/$f" | cut -d' ' -f1)" = "$(lock_sha "$f")" ] || return 1
  done
}
SRC_ROOT=""
if [ -n "${MODELS_DIR:-}" ]; then
  # MODELS_DIR is the models/ folder itself
  models_ok "$(dirname "$MODELS_DIR")" && [ "$(basename "$MODELS_DIR")" = models ] && SRC_ROOT="$(dirname "$MODELS_DIR")" ||
    die "MODELS_DIR=$MODELS_DIR does not hold $MODEL_FILES with the sha256 in models.lock.txt"
else
  for c in "$REPO" "$MAIN" "$REPO/build/cache/bundled"; do
    [ -n "$c" ] && models_ok "$c" && { SRC_ROOT="$c"; break; }
  done
  if [ -z "$SRC_ROOT" ]; then
    "$HERE/fetch_bundled_models.sh" "$RUNTIME" "$REPO/build/cache/bundled"
    models_ok "$REPO/build/cache/bundled" || die "fetched models do not match models.lock.txt"
    SRC_ROOT="$REPO/build/cache/bundled"
  fi
fi
for f in $MODEL_FILES; do
  mkdir -p "$RES/app/$(dirname "$f")"; cp "$SRC_ROOT/$f" "$RES/app/$f"
done
echo "  from $SRC_ROOT/models (sha256 checked)"

say "rehearsal samples"
mkdir -p "$RES/app/data/samples"
for s in $BUNDLE_SAMPLES; do
  found=""
  for c in "${SAMPLES_DIR:-}" "$REPO/data/samples" "${MAIN:+$MAIN/data/samples}"; do
    [ -n "$c" ] && [ -f "$c/$s" ] && { cp "$c/$s" "$RES/app/data/samples/$s"; found="$c"; break; }
  done
  [ -n "$found" ] || die "rehearsal sample $s not found (set SAMPLES_DIR, or BUNDLE_SAMPLES= to ship none)"
  echo "  $s (from $found)"
done

# --- 5. precompile the app code (the processes run with -B: nothing is written at run time) ----
say "precompile app code"
"$PY" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$RES/app/src" >/dev/null

# --- 6. checks ------------------------------------------------------------------------------------
say "checks"
[ -x "$RES/runtime/bin/python3.12" ] && [ -f "$RES/app/src/live/app.py" ] || die "layout check failed"
# nothing in the bundle may point back at this checkout (text files only; .pyc embed paths only
# for tracebacks)
for p in "$REPO" ${MAIN:+"$MAIN"}; do
  leak=$(grep -rIl --exclude='*.pyc' -F "$p" "$RES" 2>/dev/null | head -5 || true)
  [ -z "$leak" ] || { echo "files mentioning $p:"; echo "$leak"; die "build path leaked into the bundle"; }
done
for bad in tests logs work results .venv .venv-mt .venv-nemo config.yaml .env; do
  [ ! -e "$RES/app/$bad" ] || die "$bad must not be bundled"
done
printf '  app: %s\n  size: %s (runtime %s, site %s, app %s)\n' "$APP" \
  "$(du -sh "$APP" | cut -f1)" "$(du -sh "$RES/runtime" | cut -f1)" "$(du -sh "$RES/site" | cut -f1)" \
  "$(du -sh "$RES/app" | cut -f1)"
echo "Assembled (not yet signed): $APP"
