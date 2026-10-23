#!/bin/bash
# Build the DMG: the signed app plus an Applications shortcut to drag it onto.
#
#   packaging/make_dmg.sh [APP] [OUT_DIR]
#       APP      default "build/dist/Live RO2EN.app" (signed: run packaging/sign.sh first)
#       OUT_DIR  default build/dist
# Writes OUT_DIR/LiveRO2EN-<CFBundleShortVersionString>.dmg (compressed, read-only) and prints
# its path. With a Developer ID identity (the one the app is signed with) the DMG is signed too,
# so notarization covers it.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
APP="${1:-$REPO/build/dist/Live RO2EN.app}"
OUT_DIR="${2:-$REPO/build/dist}"
[ -d "$APP/Contents/MacOS" ] || { echo "make_dmg: no app at $APP (run: make app)"; exit 1; }
codesign --verify --strict "$APP" || { echo "make_dmg: the app is not signed (run: packaging/sign.sh)"; exit 1; }
VER=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$APP/Contents/Info.plist")
NAME="Live RO2EN"
mkdir -p "$OUT_DIR"
DMG="$OUT_DIR/LiveRO2EN-$VER.dmg"

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/live-dmg.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
ditto "$APP" "$STAGE/$NAME.app"           # ditto keeps the signature and extended attributes
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
echo "== hdiutil create $DMG"
hdiutil create -quiet -volname "$NAME $VER" -srcfolder "$STAGE" -fs APFS -format ULFO -ov "$DMG"

# sign the DMG with the app's Developer ID identity (an ad-hoc app leaves the DMG unsigned)
AUTH=$(codesign -dvv "$APP" 2>&1 | awk -F= '/^Authority=Developer ID Application: / && !f {print $2; f = 1}')
if [ -n "$AUTH" ]; then
  codesign --force --sign "$AUTH" --timestamp "$DMG"
  echo "DMG signed: $AUTH"
else
  echo "DMG left unsigned (the app is ad hoc signed)"
fi
hdiutil verify -quiet "$DMG"
echo "dmg: $DMG ($(du -h "$DMG" | cut -f1))"
