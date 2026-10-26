#!/bin/bash
# Build "Live RO2EN.app" (the Swift shell) into macos/build/, ad-hoc signed.
#
#   macos/build.sh            release build
#   macos/build.sh debug      debug build
#   APP_OUT=/path/X.app macos/build.sh    build the .app somewhere else (packaging/assemble_app.sh)
#
# Run it:  open "macos/build/Live RO2EN.app"
# In dev the shell starts <repo>/.venv-mt/bin/python <repo>/src/live/app.py --port <free>,
# finding <repo> by walking up from the .app. Override (env var or user default):
#   LIVE_REPO / LiveRepoPath, LIVE_PYTHON / LivePythonPath, LIVE_ENGINE_PYTHON / LiveEnginePythonPath
#   e.g. open "macos/build/Live RO2EN.app" --args -LivePythonPath /path/to/.venv-mt/bin/python
# Logs: ~/Library/Logs/Live RO2EN/{shell,orchestrator}.log
#
# The packaged app (S7/S10) puts runtime/, site/ and app/ under Contents/Resources; the shell
# then uses those instead (docs/app_contract.md "Bundle layout"). Signing here is ad-hoc only;
# S10 does Developer ID, hardened runtime and notarization.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONF="${1:-release}"
APP="${APP_OUT:-$HERE/build/Live RO2EN.app}"

swift build --package-path "$HERE" -c "$CONF"
BIN="$(swift build --package-path "$HERE" -c "$CONF" --show-bin-path)/LiveRO2EN"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Live RO2EN"
# SwiftPM adds an LC_RPATH into the build Mac's Xcode toolchain (back-deployment libraries this
# macOS 14+ binary never loads: its Swift runtime is /usr/lib/swift). It points nowhere on a
# user's Mac, so drop every absolute search path outside the system (packaging/audit_macho.sh).
otool -l "$APP/Contents/MacOS/Live RO2EN" | awk '/cmd LC_RPATH$/ {r=1; next} r && $1 == "path" {print $2; r=0}' |
  { grep -Ev '^(/usr/lib/|/System/|@)' || true; } | while IFS= read -r rp; do
    install_name_tool -delete_rpath "$rp" "$APP/Contents/MacOS/Live RO2EN"
  done
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
cp "$HERE/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"   # packaging/icons/make_icons.swift
printf 'APPL????' > "$APP/Contents/PkgInfo"

codesign --force --sign - --options runtime --entitlements "$HERE/LiveRO2EN.entitlements" \
  --timestamp=none "$APP"
codesign --verify --strict "$APP"
echo "built: $APP"
