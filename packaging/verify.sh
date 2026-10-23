#!/bin/bash
# Check a built app (and its DMG) before it is handed to anyone.
#
#   packaging/verify.sh [APP] [DMG]
#       APP  default "build/dist/Live RO2EN.app"
#       DMG  optional; when given, the app inside the DMG is the one checked (the shipped bytes)
#
# 1. codesign --verify --deep --strict on the app (and the DMG's signature, if it has one)
# 2. Gatekeeper: spctl -a -vvv, always reported; required only for a notarized build
#    (Developer ID signed and a stapled ticket), or when REQUIRE_NOTARIZED=1
# 3. packaging/audit_macho.sh on the app (no build-machine paths, nothing newer than MACOS_MIN)
# 4. smoke launch: a copy of the app in a temporary folder (so nothing under this repo can be
#    used), the bundled orchestrator started exactly as the shell starts it (LaunchConfig.swift:
#    same flags, same variables, with LIVE_HOME/logs/caches in the temporary folder), until
#    /api/state answers; then POST /api/shutdown, and no process may be left. Its open files
#    must not include anything under this repo.
# Exit 0 = everything required passed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
# the checkout(s) the app must not use at run time: this one and, for a git worktree, the main one
SRCROOT="$REPO"
common=$(git -C "$REPO" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)
[ "$(basename "${common:-x}")" = .git ] && SRCROOT="$(dirname "$common")"   # a prefix of REPO for a worktree
APP="${1:-$REPO/build/dist/Live RO2EN.app}"
DMG="${2:-}"
NAME="Live RO2EN"
fail=0
ok()   { printf '  PASS  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; fail=1; }
note() { printf '  info  %s\n' "$*"; }
say()  { printf '\n== %s\n' "$*"; }

T="$(mktemp -d "${TMPDIR:-/tmp}/live-verify.XXXXXX")"
MNT=""; PID=""
cleanup() {
  [ -n "$PID" ] && kill -TERM -- "-$PID" 2>/dev/null || true
  [ -n "$MNT" ] && hdiutil detach -quiet "$MNT" 2>/dev/null || true
  rm -rf "$T"
}
trap cleanup EXIT

# --- 0. the copy to check ---------------------------------------------------------------------
if [ -n "$DMG" ]; then
  say "DMG $(basename "$DMG")"
  [ -f "$DMG" ] || { echo "verify: no DMG at $DMG"; exit 1; }
  hdiutil verify -quiet "$DMG" && ok "hdiutil verify" || bad "hdiutil verify"
  MNT="$T/mnt"; mkdir -p "$MNT"
  hdiutil attach -quiet -nobrowse -readonly -noautoopen -mountpoint "$MNT" "$DMG"
  [ -d "$MNT/$NAME.app" ] && ok "contains $NAME.app" || bad "no $NAME.app in the DMG"
  [ "$(readlink "$MNT/Applications")" = /Applications ] && ok "Applications shortcut" || bad "no Applications shortcut"
  if codesign -dv "$DMG" >/dev/null 2>&1; then
    codesign --verify --strict "$DMG" && ok "DMG signature" || bad "DMG signature"
  else
    note "DMG is not signed (ad-hoc build)"
  fi
  ditto "$MNT/$NAME.app" "$T/$NAME.app"
  hdiutil detach -quiet "$MNT"; MNT=""
else
  [ -d "$APP/Contents/MacOS" ] || { echo "verify: no app at $APP"; exit 1; }
  ditto "$APP" "$T/$NAME.app"
fi
CHECK="$T/$NAME.app"
RES="$CHECK/Contents/Resources"

# --- 1. signature ------------------------------------------------------------------------------
say "code signature"
if out=$(codesign --verify --deep --strict --verbose=2 "$CHECK" 2>&1); then ok "codesign --verify --deep --strict"
else bad "codesign --verify --deep --strict"; printf '%s\n' "$out" | tail -20 | sed 's/^/        /'; fi
info=$(codesign -dvv "$CHECK" 2>&1 || true)
AUTH=$(printf '%s\n' "$info" | awk -F= '/^Authority=/ && !f {print $2; f = 1}')
note "signed by: ${AUTH:-ad hoc}"
printf '%s\n' "$info" | grep 'flags=.*runtime' >/dev/null && ok "hardened runtime (app)" || bad "app lacks the hardened runtime"
PY="$RES/runtime/bin/python3.12"
codesign -d --entitlements - --xml "$PY" 2>/dev/null | grep 'com.apple.security.cs.allow-jit' >/dev/null &&
  ok "python3.12 carries runtime.entitlements" || bad "python3.12 lacks runtime.entitlements"
codesign -dvv "$PY" 2>&1 | grep 'flags=.*runtime' >/dev/null && ok "hardened runtime (python3.12)" || bad "python3.12 lacks the hardened runtime"
codesign -d --entitlements - --xml "$CHECK" 2>/dev/null | grep 'com.apple.security.device.audio-input' >/dev/null &&
  ok "app carries LiveRO2EN.entitlements" || bad "app lacks LiveRO2EN.entitlements"

# --- 2. Gatekeeper -----------------------------------------------------------------------------
say "Gatekeeper"
notarized=0
case "$AUTH" in "Developer ID Application: "*)
  xcrun stapler validate -q "$CHECK" >/dev/null 2>&1 && notarized=1;; esac
[ "${REQUIRE_NOTARIZED:-0}" = 1 ] && notarized=1
if out=$(spctl -a -vvv "$CHECK" 2>&1); then ok "spctl: $(printf '%s' "$out" | tr '\n' ' ' | sed "s|$T/||g")"
elif [ "$notarized" = 1 ]; then bad "spctl rejected a notarized build:"; printf '%s\n' "$out" | sed 's/^/        /'
else note "spctl (not required: not notarized): $(printf '%s' "$out" | tr '\n' ' ' | sed "s|$T/||g")"; fi

# --- 3. Mach-O audit ----------------------------------------------------------------------------
say "Mach-O audit (packaging/audit_macho.sh)"
if "$HERE/audit_macho.sh" "$CHECK" > "$T/audit.log" 2>&1; then ok "$(grep -E '^(files|build-machine)' "$T/audit.log" | tr '\n' ' ')"
else bad "audit_macho"; grep -vE '^(Mach-O audit|files:)' "$T/audit.log" | head -30 | sed 's/^/        /'; fi
rm -f "$CHECK/macho_audit.txt"

# --- 4a. the engine side imports from the bundle alone ------------------------------------------
# The smoke launch below only starts the orchestrator (needs_setup: no engine, no models). Import
# what the engine and the download child load, with the shell's environment and a fresh HOME,
# so a module or library missing from the bundle fails here, not on a volunteer's Mac.
say "engine and download child import from the bundle"
IH="$T/import-home"; mkdir -p "$IH"
imp() {   # $1 = HOME, $2 = site dirs, $3.. = modules
  local home="$1" sites="$2"; shift 2
  (cd "$RES/app/src/live" && env -i HOME="$home" PATH=/usr/bin:/bin TMPDIR="${TMPDIR:-/tmp}" LANG=en_US.UTF-8      PYTHONPATH="$RES/app/src/live:$RES/app/src:$sites" PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1      HF_HUB_OFFLINE=1 NUMBA_CACHE_DIR="$IH/numba"      "$PY" -s -B -c 'import importlib, sys
bad = []
for m in sys.argv[1:]:
    try:
        importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {e}")
print("\n".join(bad)); sys.exit(1 if bad else 0)' "$@") > "$T/imports.log" 2>&1
}
# the engine runs with HOME = <caches>/engine-home (paths.engine_env), as the app does
mkdir -p "$IH/caches/engine-home"
if imp "$IH/caches/engine-home" "$RES/site/common:$RES/site/nemo" engine vad context_stt whistle_en stt_run torch parakeet_mlx mlx.core needle \
       onnxruntime soxr soundfile sounddevice nemo.collections.asr; then ok "engine side (site/nemo)"
else bad "engine side (site/nemo):"; tail -15 "$T/imports.log" | sed 's/^/        /'; fi
if imp "$IH" "$RES/site/common:$RES/site/mt" app model_setup preflight report translate ctranslate2 mlx_lm transformers; then
  ok "orchestrator side (site/mt)"
else bad "orchestrator side (site/mt):"; tail -15 "$T/imports.log" | sed 's/^/        /'; fi
left=$(ls -A "$IH" | grep -vE '^(numba|caches)$' || true)
[ -z "$left" ] && ok "nothing written to the user's HOME (engine writes stay in its own)" ||
  bad "written to the user's HOME: $(printf '%s' "$left" | tr '\n' ' ')"

# --- 4. smoke launch ---------------------------------------------------------------------------
say "smoke launch of the bundled orchestrator"
U="$T/user"; mkdir -p "$U/support" "$U/logs" "$U/caches/numba" "$U/home"   # a fresh HOME: nothing of ours
PORT=$("$PY" -I -B -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')
S="$RES/site"
# the variables LaunchConfig.resolve() sets for the bundle, nothing inherited from this shell
(
  cd "$RES/app"
  exec env -i HOME="$U/home" USER="${USER:-}" PATH=/usr/bin:/bin:/usr/sbin:/sbin TMPDIR="${TMPDIR:-/tmp}" \
    LANG="${LANG:-en_US.UTF-8}" PYTHONUNBUFFERED=1 \
    PYTHONPATH="$S/common:$S/mt" LIVE_ENGINE_PYTHON="$PY" LIVE_ENGINE_PYTHONPATH="$S/common:$S/nemo" \
    LIVE_HOME="$U/support" LIVE_LOG_DIR="$U/logs" LIVE_CACHE_DIR="$U/caches" HF_HOME="$U/support/hf" \
    PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 NUMBA_CACHE_DIR="$U/caches/numba" \
    /usr/bin/perl -e 'setpgrp(0, 0); exec @ARGV or die' -- \
    "$PY" -s -B "$RES/app/src/live/app.py" --port "$PORT"
) > "$T/orchestrator.log" 2>&1 &
PID=$!
disown "$PID" 2>/dev/null || true      # no "Terminated" job notice when it exits on shutdown
t0=$(date +%s); state=""
while [ $(( $(date +%s) - t0 )) -lt "${SMOKE_TIMEOUT:-180}" ]; do
  kill -0 "$PID" 2>/dev/null || break
  state=$(curl -fsS -m 2 "http://127.0.0.1:$PORT/api/state" 2>/dev/null) && break
  state=""; sleep 1
done
if [ -n "$state" ]; then
  ok "/api/state answered after $(( $(date +%s) - t0 )) s: $(printf '%s' "$state" | "$PY" -I -B -c '
import json, sys; s = json.load(sys.stdin); print({k: s.get(k) for k in ("phase", "status", "profile", "running")})')"
  curl -fsS -m 2 "http://127.0.0.1:$PORT/ui/" -o /dev/null && ok "/ui/ served" || bad "/ui/ not served"
  # nothing under the repo may be open (code, data, venvs)
  pids=$(pgrep -g "$PID" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
  leaks=$(lsof -n -p "${pids:-$PID}" 2>/dev/null | awk '{print $NF}' | grep -F "$SRCROOT" | sort -u | head -5 || true)
  [ -z "$leaks" ] && ok "no file under $SRCROOT held open (cwd, mapped libraries)" || { bad "open files under the repo:"; echo "$leaks" | sed 's/^/        /'; }
  code=$(curl -s -m 10 -o /dev/null -w '%{http_code}' -X POST -H 'Content-Type: application/json' -d '{}' \
         "http://127.0.0.1:$PORT/api/shutdown" || true)
  [ "$code" = 200 ] && ok "POST /api/shutdown -> 200" || bad "POST /api/shutdown -> $code"
  for _ in $(seq 1 30); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
  kill -0 "$PID" 2>/dev/null && bad "orchestrator still running 15 s after shutdown" || ok "orchestrator exited"
else
  bad "/api/state did not answer within ${SMOKE_TIMEOUT:-180} s"
  tail -30 "$T/orchestrator.log" | sed 's/^/        /'
fi
sleep 1
left=$(pgrep -fl "$T/" 2>/dev/null || true)
if [ -z "$left" ]; then ok "no process left"; else bad "processes left:"; echo "$left" | sed 's/^/        /'; fi
PID=""
# the bundle must not have been written to (-B, PYTHONDONTWRITEBYTECODE)
codesign --verify --strict "$CHECK" 2>/dev/null && ok "bundle unchanged after the run (signature still valid)" ||
  bad "the run modified the bundle"
written=$(find "$U" -type f | sed "s|$U/||" | head -20 | tr '\n' ' ')
note "files written to the temporary user folders: ${written:-none}"

say "result"
if [ "$fail" = 0 ]; then echo "VERIFY PASS"; else echo "VERIFY FAIL"; fi
exit "$fail"
