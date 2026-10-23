#!/bin/bash
# Build "Live RO2EN.app" (the Swift shell) into macos/build/, ad-hoc signed.
#
#   macos/build.sh            release build
#   macos/build.sh debug      debug build
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
APP="$HERE/build/Live RO2EN.app"

swift build --package-path "$HERE" -c "$CONF"
BIN="$(swift build --package-path "$HERE" -c "$CONF" --show-bin-path)/LiveRO2EN"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/Live RO2EN"
cp "$HERE/Info.plist" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"

codesign --force --sign - --options runtime --entitlements "$HERE/LiveRO2EN.entitlements" \
  --timestamp=none "$APP"
codesign --verify --strict "$APP"
echo "built: $APP"
