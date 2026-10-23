#!/bin/bash
# Audit every Mach-O file under a runtime folder (or an .app) for load paths that would only
# work on the build machine.
#
#   packaging/audit_macho.sh [DIR]          default DIR = build/runtime
#
# Mach-O files are found with `file`, not by extension (APP_PLAN §1.1). For each one, the
# load commands (`otool -l`: LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_RPATH,
# LC_ID_DYLIB) must not mention /opt/homebrew, /usr/local or /Users, every @rpath/@loader_path
# dependency must resolve to a file in DIR, every absolute
# dependency must be a system one (/usr/lib, /System), none may need a macOS newer than
# MACOS_MIN (default 14.0, LC_BUILD_VERSION minos), and every file must have an arm64 slice.
# Exit 0 = pass, 1 = fail. Writes DIR/macho_audit.txt.
set -euo pipefail
export LC_ALL=C          # `file` output can hold non-UTF-8 bytes; a UTF-8 awk stops on them
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-$(dirname "$HERE")/build/runtime}"
DIR="$(cd "$DIR" && pwd)"
REPORT="$DIR/macho_audit.txt"
BAD='/opt/homebrew|/usr/local|/Users/'
MACOS_MIN="${MACOS_MIN:-14.0}"
EXE_DIR="$DIR/runtime/bin"; [ -d "$DIR/Contents/MacOS" ] && EXE_DIR="$DIR/Contents/MacOS"   # @executable_path
newer() {   # version $1 > version $2, compared part by part (14 = 14.0 = 14.0.0)
  awk -v a="$1" -v b="$2" 'BEGIN { na = split(a, x, "."); nb = split(b, y, ".")
    for (i = 1; i <= (na > nb ? na : nb); i++) { if (x[i] + 0 > y[i] + 0) exit 0; if (x[i] + 0 < y[i] + 0) exit 1 }
    exit 1 }'
}

list=$(mktemp); trap 'rm -f "$list"' EXIT
# `file` over every regular file (batched); keep Mach-O ones
find "$DIR" -type f ! -name '*.py' ! -name '*.pyc' ! -name '*.txt' ! -name '*.json' -print0 |
  xargs -0 file --no-pad -F $'\t' 2>/dev/null | awk -F'\t' '$2 ~ /Mach-O/ && $1 !~ / \(for architecture / {print $1}' | sort -u > "$list"
n=$(wc -l < "$list" | tr -d ' ')

fail=0; nbad=0; nabs=0; narch=0; nos=0; nmiss=0
{
  echo "Mach-O audit of $DIR ($(date '+%Y-%m-%d %H:%M'))"
  echo "files: $n"
  while IFS= read -r f; do
    rel="${f#$DIR/}"
    # paths from the load commands that name a library or a search path
    paths=$(otool -l "$f" 2>/dev/null | awk '
      /cmd LC_(LOAD_DYLIB|LOAD_WEAK_DYLIB|REEXPORT_DYLIB|LOAD_UPWARD_DYLIB|RPATH|ID_DYLIB)$/ {want=$2; next}
      want && ($1 == "name" || $1 == "path") {print want "\t" $2; want=""}')
    hits=$(printf '%s\n' "$paths" | grep -E "$BAD" || true)
    if [ -n "$hits" ]; then
      fail=1; nbad=$((nbad + 1))
      printf 'BAD   %s\n' "$rel"; printf '%s\n' "$hits" | sed 's/^/        /'
    fi
    # absolute dependencies must be system libraries
    abs=$(printf '%s\n' "$paths" | awk -F'\t' '$1 ~ /LOAD|REEXPORT|RPATH/ && $2 ~ /^\// && $2 !~ /^\/(usr\/lib|System)\// {print $2}')
    if [ -n "$abs" ]; then
      fail=1; nabs=$((nabs + 1))
      printf 'ABS   %s\n' "$rel"; printf '%s\n' "$abs" | sed 's/^/        /'
    fi
    # every @rpath / @loader_path / @executable_path dependency (weak ones aside) must resolve
    # to a file in the tree: a lazily imported extension is not caught by the smoke imports
    dir=$(dirname "$f"); miss=""
    rpaths=$(printf '%s\n' "$paths" | awk -F'\t' '$1 == "LC_RPATH" {print $2}')
    while IFS=$'\t' read -r kind dep; do
      case "$kind" in LC_LOAD_DYLIB|LC_REEXPORT_DYLIB) ;; *) continue;; esac
      case "$dep" in @*) ;; *) continue;; esac
      found=""
      case "$dep" in
        @loader_path/*)     [ -e "$dir/${dep#@loader_path/}" ] && found=1 ;;
        @executable_path/*) [ -e "$EXE_DIR/${dep#@executable_path/}" ] && found=1 ;;
        @rpath/*)
          while IFS= read -r rp; do
            [ -n "$rp" ] || continue
            rp="${rp/#@loader_path/$dir}"; rp="${rp/#@executable_path/$EXE_DIR}"
            [ -e "$rp/${dep#@rpath/}" ] && { found=1; break; }
          done <<< "$rpaths"
          # no search path of its own: dyld uses its loader's, so the library just has to be here
          [ -z "$found" ] && [ -z "$rpaths" ] && grep -q "/${dep#@rpath/}\$" "$list" && found=1 ;;
      esac
      [ -n "$found" ] || miss+="$dep"$'\n'
    done <<< "$paths"
    if [ -n "$miss" ]; then
      fail=1; nmiss=$((nmiss + 1))
      printf 'MISS  %s\n' "$rel"; printf '%s' "$miss" | sed 's/^/        /'
    fi
    # the newest macOS any slice needs (LC_BUILD_VERSION minos, or the older LC_VERSION_MIN_MACOSX)
    minos=$(otool -l "$f" 2>/dev/null | awk '
      /cmd LC_BUILD_VERSION$/ {k="minos"; next}  /cmd LC_VERSION_MIN_MACOSX$/ {k="version"; next}
      k && $1 == k {print $2; k=""}' | sort -t. -k1,1n -k2,2n -k3,3n | tail -1)
    if [ -n "$minos" ] && newer "$minos" "$MACOS_MIN"; then
      fail=1; nos=$((nos + 1))
      printf 'MINOS %s (needs macOS %s, target %s)\n' "$rel" "$minos" "$MACOS_MIN"
    fi
    archs=$(lipo -archs "$f" 2>/dev/null || echo "?")
    case " $archs " in *" arm64 "*) ;; *) fail=1; narch=$((narch + 1)); printf 'ARCH  %s (%s)\n' "$rel" "$archs";; esac
  done < "$list"
  echo
  echo "build-machine paths: $nbad files · non-system absolute deps: $nabs files · unresolved @rpath deps: $nmiss files · newer than macOS $MACOS_MIN: $nos files · without arm64: $narch files"
  if [ "$fail" = 0 ]; then echo "AUDIT PASS"; else echo "AUDIT FAIL"; fi
} | tee "$REPORT" || true
grep -q '^AUDIT PASS$' "$REPORT"
