#!/bin/bash
# Build "Live Remote.app" (the toolbar for the ProPresenter Mac) into remote/build/, and a
# small .dmg next to it.
#
#   remote/build.sh            release build, signed ad hoc (works on this Mac, or after
#                              "Open Anyway" on another)
#   SIGN_IDENTITY="Developer ID Application: …" remote/build.sh
#                              Developer ID signature with a secure timestamp, for notarizing
#                              (xcrun notarytool submit remote/build/Live\ Remote.dmg …)
#
# Run it:  open "remote/build/Live Remote.app"
# It needs Live Remote switched on in the captions Mac's Settings → ProPresenter.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
APP="$HERE/build/Live Remote.app"
DMG="$HERE/build/Live Remote.dmg"
ID="${SIGN_IDENTITY:--}"

swift build --package-path "$HERE" -c release
BIN="$(swift build --package-path "$HERE" -c release --show-bin-path)/LiveRemote"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Live Remote"
# drop SwiftPM's search paths into the build Mac's toolchain (as macos/build.sh does)
otool -l "$APP/Contents/MacOS/Live Remote" | awk '/cmd LC_RPATH$/ {r=1; next} r && $1 == "path" {print $2; r=0}' |
  { grep -Ev '^(/usr/lib/|/System/|@)' || true; } | while IFS= read -r rp; do
    install_name_tool -delete_rpath "$rp" "$APP/Contents/MacOS/Live Remote"
  done
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

if [ "$ID" = "-" ]; then TS=--timestamp=none; else TS=--timestamp; fi
codesign --force --sign "$ID" --options runtime --entitlements "$HERE/LiveRemote.entitlements" $TS "$APP"
codesign --verify --strict "$APP"

rm -f "$DMG"
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -quiet -volname "Live Remote" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"
[ "$ID" = "-" ] || codesign --force --sign "$ID" $TS "$DMG"
echo "built: $APP ($(du -sh "$APP" | cut -f1)), $DMG ($(du -h "$DMG" | cut -f1))"
