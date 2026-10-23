#!/bin/bash
# Build the embedded Python runtime for the Mac app (APP_PLAN §1.1, bundle layout in
# docs/app_contract.md). Build time only: downloads python-build-standalone and wheels.
#
#   packaging/build_runtime.sh [OUT]        default OUT = build/runtime
#
# OUT/
#   runtime/            python-build-standalone 3.12 (bin/python3.12), pruned
#   site/common/        packages pinned to the same version in nemo.txt and mt.txt (torch, mlx, numpy…)
#   site/nemo/          the rest of requirements/nemo.txt   (engine.py)
#   site/mt/            the rest of requirements/mt.txt     (app.py, translators, web UI)
#   reqs/               the split requirement files actually installed
#   env.sh              the environment the shell sets (sourced by the relocation test)
#   size_report.txt     sizes before/after pruning, biggest packages
#
# Everything is wheels only (except the pure-Python sdists in SDIST_OK below, built here and
# checked to be py3-none-any), installed with `uv pip install --target --no-deps` from the
# pinned lists, pruned per packaging/prune.txt, and precompiled to unchecked-hash .pyc, so the
# folder can be copied anywhere and run read-only with `python3.12 -s -B`.
# Run packaging/audit_macho.sh OUT afterwards (this script does, unless NO_AUDIT=1).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
OUT="${1:-$REPO/build/runtime}"
mkdir -p "$(dirname "$OUT")"; OUT="$(cd "$(dirname "$OUT")" && pwd)/$(basename "$OUT")"
CACHE="${RUNTIME_CACHE:-$REPO/build/cache}"

# python-build-standalone: pinned release + sha256 (update both together)
PBS_TAG=20261013
PBS_PY=3.12.15
PBS_FILE="cpython-${PBS_PY}+${PBS_TAG}-aarch64-apple-darwin-install_only_stripped.tar.gz"
PBS_SHA256=9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_FILE//+/%2B}"

# sdist-only packages in the pins (PyPI has no wheel). Each is built here and must come out as
# pure Python (checked below). Everything else is wheels only.
SDIST_OK="antlr4-python3-runtime"

# Oldest macOS the app supports (Info.plist LSMinimumSystemVersion, Package.swift). uv picks
# wheels for this target, not for the build Mac's own version, and the audit fails any Mach-O
# that needs newer.
export MACOS_MIN="${MACOS_MIN:-14.0}" MACOSX_DEPLOYMENT_TARGET="${MACOS_MIN:-14.0}"

command -v uv >/dev/null || { echo "uv is required (brew install uv)"; exit 1; }
[ "$(uname -m)" = arm64 ] || { echo "build on Apple Silicon"; exit 1; }
say() { printf '\n== %s\n' "$*"; }

# --- 1. interpreter ----------------------------------------------------------------------
say "python-build-standalone $PBS_PY ($PBS_TAG)"
mkdir -p "$CACHE"
TARBALL="$CACHE/$PBS_FILE"
if [ ! -f "$TARBALL" ] || [ "$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)" != "$PBS_SHA256" ]; then
  curl -fL --retry 3 -o "$TARBALL.partial" "$PBS_URL"
  mv "$TARBALL.partial" "$TARBALL"
fi
got=$(shasum -a 256 "$TARBALL" | cut -d' ' -f1)
[ "$got" = "$PBS_SHA256" ] || { echo "CHECKSUM MISMATCH: $TARBALL ($got)"; exit 1; }
rm -rf "$OUT"; mkdir -p "$OUT/reqs" "$OUT/site"
tar xzf "$TARBALL" -C "$OUT"            # extracts python/
mv "$OUT/python" "$OUT/runtime"
PY="$OUT/runtime/bin/python3.12"
"$PY" -I -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version'
# The engine is spawned without -s, so switch the user site off in the runtime itself.
# (The shell also sets PYTHONNOUSERSITE=1; see env.sh.)
sed -i '' 's/^ENABLE_USER_SITE = None$/ENABLE_USER_SITE = False/' "$OUT/runtime/lib/python3.12/site.py"
grep -q '^ENABLE_USER_SITE = False$' "$OUT/runtime/lib/python3.12/site.py" || { echo "site.py patch failed"; exit 1; }

# --- 2. split the pins: common / nemo / mt -----------------------------------------------
say "split requirements/nemo.txt and requirements/mt.txt"
norm() {   # name==ver per line, PEP 503-normalised name, sorted
  grep -Ev '^[[:space:]]*(#|$)' "$1" | sed 's/[[:space:]]*#.*//' |
    awk -F'==' '{n=tolower($1); gsub(/[-_.]+/, "-", n); print n "==" $2}' | sort -u
}
norm "$REPO/requirements/nemo.txt" > "$OUT/reqs/nemo.all.txt"
norm "$REPO/requirements/mt.txt"   > "$OUT/reqs/mt.all.txt"
comm -12 "$OUT/reqs/nemo.all.txt" "$OUT/reqs/mt.all.txt" > "$OUT/reqs/common.txt"
comm -23 "$OUT/reqs/nemo.all.txt" "$OUT/reqs/mt.all.txt" > "$OUT/reqs/nemo.txt"
comm -13 "$OUT/reqs/nemo.all.txt" "$OUT/reqs/mt.all.txt" > "$OUT/reqs/mt.txt"
# a name in both lists at different versions must not be in common (it is in each env's own dir)
cut -d= -f1 "$OUT/reqs/nemo.txt" | sort > "$OUT/reqs/.n"; cut -d= -f1 "$OUT/reqs/mt.txt" | sort > "$OUT/reqs/.m"
cnt() { wc -l < "$1" | tr -d ' '; }
echo "common $(cnt "$OUT/reqs/common.txt") · nemo-only $(cnt "$OUT/reqs/nemo.txt") · mt-only $(cnt "$OUT/reqs/mt.txt")"
echo "different versions per env: $(comm -12 "$OUT/reqs/.n" "$OUT/reqs/.m" | tr '\n' ' ')"
rm -f "$OUT/reqs/.n" "$OUT/reqs/.m"

# --- 3. install (no dependency resolution: the pins are the full closure) ------------------
# Wheels only: every pin gets --only-binary except the sdist-only ones in SDIST_OK, which
# uv builds here and which are then checked to be pure Python (Root-Is-Purelib, no .so).
for name in common nemo mt; do
  say "install site/$name ($(cnt "$OUT/reqs/$name.txt") packages)"
  args=()
  for p in $(cut -d= -f1 "$OUT/reqs/$name.txt"); do
    case " $(echo $SDIST_OK) " in *" $p "*) ;; *) args+=(--only-binary "$p");; esac
  done
  uv pip install -q --python "$PY" --target "$OUT/site/$name" --no-deps --link-mode copy \
     --python-platform aarch64-apple-darwin \
     "${args[@]}" -r "$OUT/reqs/$name.txt"
done
say "check the sdist-built packages are pure Python"
for w in "$OUT"/site/*/*.dist-info/WHEEL; do
  if grep -qi '^Root-Is-Purelib: false' "$w"; then
    pkg=$(basename "$(dirname "$w")" | sed 's/-[^-]*\.dist-info$//' | tr 'A-Z_.' 'a-z--')
    case " $(echo $SDIST_OK) " in *" $pkg "*) echo "NOT PURE PYTHON: $pkg (built from sdist)"; exit 1;; esac
  fi
done
# console scripts carry absolute shebangs into this build folder; the app never uses them
rm -rf "$OUT"/site/*/bin
# .pth files are NOT processed for PYTHONPATH dirs: list them so a needed one is not missed
say ".pth files (ignored on PYTHONPATH)"
find "$OUT/site" -maxdepth 2 -name '*.pth' -print | sed "s|$OUT/||" || true

# --- 4. prune ------------------------------------------------------------------------------
du_k() { du -sk "$1" | cut -f1; }
mb() { awk -v k="$1" 'BEGIN{printf "%.0f MB", k/1024}'; }
before_rt=$(du_k "$OUT/runtime"); before_c=$(du_k "$OUT/site/common")
before_n=$(du_k "$OUT/site/nemo"); before_m=$(du_k "$OUT/site/mt")
say "prune (packaging/prune.txt)"
while read -r kind pat _; do
  case "$kind" in ''|'#'*) continue;; esac
  case "$kind" in
    path)     find "$OUT" -path "$OUT/$pat" -prune -exec rm -rf {} + ;;
    dir)      find "$OUT/site" -type d -name "$pat" -prune -exec rm -rf {} + ;;
    file)     find "$OUT" -type f -name "$pat" -delete ;;
    *) echo "prune.txt: unknown kind '$kind'"; exit 1;;
  esac
done < "$HERE/prune.txt"
find "$OUT" -type d -name __pycache__ -prune -exec rm -rf {} +

# --- 4b. scrub build-machine paths left in upstream wheels ---------------------------------
# Some wheels carry their CI machine's search paths (e.g. sklearn: LC_RPATH
# /Users/runner/micromamba/envs/build/lib; scipy: /opt/homebrew/…/gcc). They point nowhere on
# a user's Mac, but they are dead weight and a dylib hijack risk, so delete them, give such a
# dylib an @rpath id, and re-sign ad hoc (arm64 needs a valid signature; S10 re-signs all).
say "scrub build-machine load paths"
macho_files() {   # every Mach-O under $1, found with `file` (not by extension)
  find "$1" -type f ! -name '*.py' ! -name '*.pyc' -print0 | xargs -0 file --no-pad -F $'\t' 2>/dev/null |
    awk -F'\t' '$2 ~ /Mach-O/ && $1 !~ / \(for architecture / {print $1}' | sort -u
}
BAD='^(/opt/homebrew|/usr/local|/Users/)'
nscrub=0
while IFS= read -r f; do
  lc=$(otool -l "$f" | awk '/cmd LC_(RPATH|ID_DYLIB)$/ {want=$2; next}
                            want && ($1 == "name" || $1 == "path") {print want "\t" $2; want=""}')
  hits=$(printf '%s\n' "$lc" | awk -F'\t' -v re="$BAD" '$2 ~ re')
  [ -n "$hits" ] || continue
  while IFS=$'\t' read -r kind p; do
    case "$kind" in
      LC_RPATH)    install_name_tool -delete_rpath "$p" "$f" 2>/dev/null ;;
      LC_ID_DYLIB) install_name_tool -id "@rpath/$(basename "$f")" "$f" 2>/dev/null ;;
    esac
  done <<< "$hits"
  codesign -f -s - "$f" 2>/dev/null
  nscrub=$((nscrub + 1))
done < <(macho_files "$OUT")
echo "  rewrote $nscrub files"
pr_rt=$(du_k "$OUT/runtime"); pr_c=$(du_k "$OUT/site/common"); pr_n=$(du_k "$OUT/site/nemo"); pr_m=$(du_k "$OUT/site/mt")

# --- 5. precompile -------------------------------------------------------------------------
# unchecked-hash: valid wherever the folder is copied (no mtime/path check) and never rewritten;
# the processes run with -B, so nothing is written into the (signed) bundle.
say "precompile"
for d in "$OUT/runtime/lib/python3.12" "$OUT"/site/*; do
  "$PY" -I -m compileall -q -j 0 --invalidation-mode unchecked-hash "$d" >"$OUT/.compile.log" 2>&1 ||
    echo "  compileall: $(grep -c 'Error' "$OUT/.compile.log" || true) files did not compile in ${d#$OUT/} (left as source)"
done
rm -f "$OUT/.compile.log"

# --- 6. smoke imports (each process's own PYTHONPATH, -s -B, no network) --------------------
say "smoke imports"
NEMO_PP="$OUT/site/common:$OUT/site/nemo"; MT_PP="$OUT/site/common:$OUT/site/mt"
PYTHONNOUSERSITE=1 PYTHONPATH="$NEMO_PP" HF_HUB_OFFLINE=1 "$PY" -s -B -c '
import torch, numpy, soundfile, sounddevice, onnxruntime, soxr, mlx.core, parakeet_mlx, needle
import nemo.collections.asr
print("  nemo ok: torch", torch.__version__, "numpy", numpy.__version__)'
PYTHONNOUSERSITE=1 PYTHONPATH="$MT_PP" HF_HUB_OFFLINE=1 "$PY" -s -B -c '
import fastapi, uvicorn, websockets, ctranslate2, transformers, mlx_lm, sentencepiece, yaml, torch
import mlx.core as mx                     # Metal JIT still works with the headers pruned
k = mx.fast.metal_kernel(name="twice", input_names=["a"], output_names=["b"],
                         source="uint i = thread_position_in_grid.x; b[i] = 2 * a[i];")
b = k(inputs=[mx.arange(4, dtype=mx.float32)], grid=(4, 1, 1), threadgroup=(4, 1, 1),
      output_shapes=[(4,)], output_dtypes=[mx.float32])[0]
assert b.tolist() == [0, 2, 4, 6], b
print("  mt ok: fastapi", fastapi.__version__, "ctranslate2", ctranslate2.__version__, "mlx kernel ok")'

# --- 7. env.sh: what the shell sets (paths relative to this folder) -------------------------
cat > "$OUT/env.sh" <<'SH'
# Environment for the embedded runtime. `source env.sh` from anywhere; R is this folder.
# The Swift shell sets the same variables (app_contract.md: bundle layout / environment).
R="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
export LIVE_PYTHON="$R/runtime/bin/python3.12"            # run app.py as: $LIVE_PYTHON -s -B app.py
export PYTHONPATH="$R/site/common:$R/site/mt"             # orchestrator (app.py)
export LIVE_ENGINE_PYTHON="$R/runtime/bin/python3.12"
export LIVE_ENGINE_PYTHONPATH="$R/site/common:$R/site/nemo"
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1       # the engine is spawned without -s -B
export NUMBA_CACHE_DIR="${TMPDIR:-/tmp}/live-numba"       # the shell: ~/Library/Caches/.../numba
unset PYTHONHOME PYTHONSTARTUP
SH

# --- 8. size report -------------------------------------------------------------------------
say "size report"
{
  echo "Embedded runtime size report ($(date '+%Y-%m-%d %H:%M'), python $PBS_PY / pbs $PBS_TAG)"
  echo
  printf '%-14s %12s %12s %12s\n' part installed pruned "with .pyc"
  for p in "runtime:$before_rt:$pr_rt" "site/common:$before_c:$pr_c" "site/nemo:$before_n:$pr_n" "site/mt:$before_m:$pr_m"; do
    IFS=: read -r d b q <<< "$p"
    printf '%-14s %12s %12s %12s\n' "$d" "$(mb "$b")" "$(mb "$q")" "$(mb "$(du_k "$OUT/$d")")"
  done
  printf '%-14s %12s %12s %12s\n' total "$(mb $((before_rt + before_c + before_n + before_m)))" \
    "$(mb $((pr_rt + pr_c + pr_n + pr_m)))" "$(mb "$(du_k "$OUT")")"
  echo
  echo "compressed estimate (tar | gzip -1): $(tar cf - -C "$OUT" runtime site | gzip -1 | wc -c | awk '{printf "%.0f MB", $1/1048576}')"
  for d in common nemo mt; do
    echo; echo "largest in site/$d:"
    du -sk "$OUT/site/$d"/* | sort -rn | head -12 |
      awk -F'\t' '{n = $2; sub(/.*\//, "", n); printf "  %7.0f MB  %s\n", $1 / 1024, n}'
  done
} | tee "$OUT/size_report.txt"

if [ "${NO_AUDIT:-0}" != 1 ]; then
  say "Mach-O audit"
  "$HERE/audit_macho.sh" "$OUT"
fi
echo; echo "Runtime ready: $OUT"
