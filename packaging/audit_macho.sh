#!/bin/bash
# Audit every Mach-O file under a runtime folder (or an .app) for load paths that would only
# work on the build machine.
#
#   packaging/audit_macho.sh [DIR]          default DIR = build/runtime
#
# Mach-O files are found with `file`, not by extension (APP_PLAN §1.1). For each one, the
# load commands (`otool -l`: LC_LOAD_DYLIB, LC_LOAD_WEAK_DYLIB, LC_REEXPORT_DYLIB, LC_RPATH,
# LC_ID_DYLIB) must not mention /opt/homebrew, /usr/local or /Users, and every absolute
# dependency must be a system one (/usr/lib, /System). Non-arm64 slices are reported.
# Exit 0 = pass, 1 = fail. Writes DIR/macho_audit.txt.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${1:-$(dirname "$HERE")/build/runtime}"
DIR="$(cd "$DIR" && pwd)"
REPORT="$DIR/macho_audit.txt"
BAD='/opt/homebrew|/usr/local|/Users/'

list=$(mktemp); trap 'rm -f "$list"' EXIT
# `file` over every regular file (batched); keep Mach-O ones
find "$DIR" -type f ! -name '*.py' ! -name '*.pyc' ! -name '*.txt' ! -name '*.json' -print0 |
  xargs -0 file --no-pad -F $'\t' 2>/dev/null | awk -F'\t' '$2 ~ /Mach-O/ && $1 !~ / \(for architecture / {print $1}' | sort -u > "$list"
n=$(wc -l < "$list" | tr -d ' ')

fail=0; nbad=0; nabs=0; narch=0
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
    abs=$(printf '%s\n' "$paths" | awk -F'\t' '$1 ~ /LOAD|REEXPORT/ && $2 ~ /^\// && $2 !~ /^\/(usr\/lib|System)\// {print $2}')
    if [ -n "$abs" ]; then
      fail=1; nabs=$((nabs + 1))
      printf 'ABS   %s\n' "$rel"; printf '%s\n' "$abs" | sed 's/^/        /'
    fi
    archs=$(lipo -archs "$f" 2>/dev/null || echo "?")
    case " $archs " in *" arm64 "*) ;; *) narch=$((narch + 1)); printf 'ARCH  %s (%s)\n' "$rel" "$archs";; esac
  done < "$list"
  echo
  echo "build-machine paths: $nbad files · non-system absolute deps: $nabs files · without arm64: $narch files"
  if [ "$fail" = 0 ]; then echo "AUDIT PASS"; else echo "AUDIT FAIL"; fi
} | tee "$REPORT" | grep -Ev '^ARCH ' || true
grep -q '^AUDIT PASS$' "$REPORT"
