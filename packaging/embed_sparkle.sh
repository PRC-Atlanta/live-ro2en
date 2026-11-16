#!/bin/bash
# Put Sparkle.framework (from the SwiftPM build) into an app's Contents/Frameworks and sign it
# from the inside out with the app's identity, so the hardened runtime loads it (library
# validation wants one team). Called by macos/build.sh and remote/build.sh before they sign the
# app; packaging/sign.sh signs Contents/Frameworks again with the release identity.
#
#   packaging/embed_sparkle.sh APP BIN_DIR [IDENTITY]
#       APP       the .app being assembled
#       BIN_DIR   `swift build --show-bin-path` (SwiftPM copies Sparkle.framework there)
#       IDENTITY  codesign identity, default "-" (ad hoc)
set -euo pipefail
APP="$1"; BIN="$2"; ID="${3:--}"
SRC="$BIN/Sparkle.framework"
[ -d "$SRC" ] || { echo "embed_sparkle: no Sparkle.framework in $BIN (swift build first)"; exit 1; }
FW="$APP/Contents/Frameworks"
rm -rf "$FW/Sparkle.framework"
mkdir -p "$FW"
ditto "$SRC" "$FW/Sparkle.framework"
if [ "$ID" = "-" ]; then TS=--timestamp=none; else TS=--timestamp; fi
cs() { codesign --force --sign "$ID" --options runtime $TS --preserve-metadata=entitlements "$@" 2>&1 |
         { grep -v ': replacing existing signature$' || true; }; }
V="$FW/Sparkle.framework/Versions/B"
# Sparkle's documented order: XPC services, Autoupdate, Updater.app, then the framework
for x in "$V"/XPCServices/*.xpc; do [ -e "$x" ] && cs "$x"; done
cs "$V/Autoupdate"
cs "$V/Updater.app"
cs "$FW/Sparkle.framework"
codesign --verify --strict "$FW/Sparkle.framework"
echo "embedded: Sparkle $(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$V/Resources/Info.plist")"
