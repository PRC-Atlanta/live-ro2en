#!/bin/bash
# Sign the assembled app from the inside out (APP_PLAN §1.1 "Distribution"), never --deep:
#   1. every Mach-O under Contents/Resources (runtime/, site/, app/models), found with `file`;
#      (helper executables and libraries with no entitlements: only python3.12 needs them)
#   2. Contents/Resources/runtime/bin/python3.12 with packaging/runtime.entitlements
#   3. anything in Contents/Frameworks (Sparkle, once it is added), innermost first
#   4. the app itself with macos/LiveRO2EN.entitlements
# Always hardened runtime (--options runtime). With a Developer ID identity also a secure
# timestamp (--timestamp, needs the network), which notarization requires.
#
#   packaging/sign.sh [APP]                 default "build/dist/Live RO2EN.app"
#
# SIGN_IDENTITY   a codesign identity (name or SHA-1). Default: the first valid
#                 "Developer ID Application" identity in the keychain; if there is none, "-"
#                 (ad hoc), which only works on this Mac or after "Open Anyway".
#                 "Apple Development" / "Mac Developer" identities are refused: they are not
#                 for distribution (SIGN_ALLOW_ANY=1 overrides, for experiments only).
# Never asks for or prints a password or key: codesign uses the keychain as it is.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
APP="${1:-$REPO/build/dist/Live RO2EN.app}"
[ -d "$APP/Contents/MacOS" ] || { echo "sign: no app at $APP (run: make app)"; exit 1; }
RES="$APP/Contents/Resources"
RT_ENT="$HERE/runtime.entitlements"
APP_ENT="$REPO/macos/LiveRO2EN.entitlements"
JOBS="${SIGN_JOBS:-$(sysctl -n hw.ncpu)}"

ID="${SIGN_IDENTITY:-}"
if [ -z "$ID" ]; then
  ID=$(security find-identity -v -p codesigning 2>/dev/null |
       awk -F'"' '/"Developer ID Application: / && !f {print $2; f = 1}')
fi
if [ -z "$ID" ] || [ "$ID" = "-" ]; then
  ID="-"
  cat >&2 <<'MSG'

  ################################################################################
  #  NO "Developer ID Application" CERTIFICATE: SIGNING AD HOC.                  #
  #  This build runs only on this Mac, or elsewhere after System Settings ->      #
  #  Privacy & Security -> Open Anyway. It cannot be notarized. Microphone        #
  #  permission resets on every rebuild. See docs/release.md for the one-time     #
  #  certificate setup.                                                           #
  ################################################################################

MSG
  TS="--timestamp=none"
else
  case "$ID" in
    *"Apple Development"*|*"Mac Developer"*|*"Apple Distribution"*)
      if [ "${SIGN_ALLOW_ANY:-0}" != 1 ]; then
        echo "sign: '$ID' is not a Developer ID Application identity and cannot be used to distribute the app." >&2
        echo "      Create one in Xcode -> Settings -> Accounts -> Manage Certificates (docs/release.md)." >&2
        exit 1
      fi;;
  esac
  security find-identity -v -p codesigning | grep -F "$ID" >/dev/null ||
    { echo "sign: identity '$ID' is not in the keychain (security find-identity -v -p codesigning)" >&2; exit 1; }
  TS="--timestamp"
fi
echo "signing identity: $ID"

quiet() { "$@" 2> >(grep -v ': replacing existing signature$' >&2); }   # keep real errors
cs() { quiet codesign --force --sign "$ID" --options runtime $TS "$@"; }

# --- 1. every Mach-O in the resources (found with `file`, not by extension) ----------------------
list=$(mktemp -d); trap 'rm -rf "$list"' EXIT
find "$RES" -type f ! -name '*.py' ! -name '*.pyc' ! -name '*.txt' ! -name '*.json' -print0 |
  xargs -0 file --no-pad -F $'\t' 2>/dev/null |
  awk -F'\t' '$2 ~ /Mach-O/ && $1 !~ / \(for architecture / {print ($2 ~ /executable/ ? "exe" : "lib") "\t" $1}' |
  sort -u > "$list/all"
PY="$RES/runtime/bin/python3.12"
awk -F'\t' -v py="$PY" '$1 == "lib" && $2 != py {print $2}' "$list/all" | tr '\n' '\0' > "$list/libs"
awk -F'\t' -v py="$PY" '$1 == "exe" && $2 != py {print $2}' "$list/all" | tr '\n' '\0' > "$list/exes"
nlib=$(tr -cd '\0' < "$list/libs" | wc -c | tr -d ' '); nexe=$(tr -cd '\0' < "$list/exes" | wc -c | tr -d ' ')
echo "== 1. $nlib libraries and $nexe helper executables in Resources"
[ "$nlib" = 0 ] || quiet xargs -0 -n 64 -P "$JOBS" codesign --force --sign "$ID" --options runtime $TS < "$list/libs"
[ "$nexe" = 0 ] || { tr '\0' '\n' < "$list/exes" | sed "s|^$RES/|  |"
  quiet xargs -0 -n 16 -P "$JOBS" codesign --force --sign "$ID" --options runtime $TS < "$list/exes"; }   # no JIT etc.: only python3.12 needs it

# --- 2. the interpreter ------------------------------------------------------------------------
echo "== 2. runtime/bin/python3.12 (packaging/runtime.entitlements)"
[ -f "$PY" ] || { echo "sign: missing $PY"; exit 1; }
cs --entitlements "$RT_ENT" "$PY"

# --- 3. frameworks and nested bundles, innermost first --------------------------------------
if [ -d "$APP/Contents/Frameworks" ]; then
  echo "== 3. Contents/Frameworks"
  # bare executables and dylibs inside the frameworks (e.g. Sparkle's Autoupdate) first
  find "$APP/Contents/Frameworks" -type f -print0 | xargs -0 file --no-pad -F $'\t' 2>/dev/null |
    awk -F'\t' '$2 ~ /Mach-O/ && $1 !~ / \(for architecture / {print $1}' | sort -u |
    while IFS= read -r f; do cs --preserve-metadata=entitlements "$f"; done
  # deepest bundle paths first (XPC services and helper apps before their framework)
  find "$APP/Contents/Frameworks" -type d \( -name '*.xpc' -o -name '*.app' -o -name '*.framework' \) |
    awk '{print gsub("/", "/") "\t" $0}' | sort -rn | cut -f2- | while IFS= read -r b; do
      echo "  ${b#$APP/Contents/}"; cs --preserve-metadata=entitlements "$b"
    done
fi

# --- 4. the app ---------------------------------------------------------------------------------
echo "== 4. the app (macos/LiveRO2EN.entitlements)"
cs --entitlements "$APP_ENT" "$APP"
codesign --verify --strict "$APP"
echo "signed: $APP ($( [ "$ID" = - ] && echo "ad hoc" || echo "$ID"))"
