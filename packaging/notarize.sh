#!/bin/bash
# Notarize and staple the Developer ID signed app and DMG (docs/release.md).
#
#   packaging/notarize.sh [APP] [OUT_DIR]     defaults "build/dist/Live RO2EN.app", build/dist
#
# 1. zip the app, submit it, wait, staple the ticket to the app (so it opens offline after it
#    is copied to /Applications: a church Mac may have no network on Sunday);
# 2. rebuild the DMG around the stapled app (packaging/make_dmg.sh), submit it, wait, staple it.
#
# Credentials: a notarytool keychain profile, NOTARY_PROFILE (default "live-ro2en"), created
# once with `xcrun notarytool store-credentials live-ro2en --apple-id … --team-id …`. This
# script never reads, prints or stores a password: notarytool takes them from the keychain.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
APP="${1:-$REPO/build/dist/Live RO2EN.app}"
OUT_DIR="${2:-$REPO/build/dist}"
PROFILE="${NOTARY_PROFILE:-live-ro2en}"
die() { printf '\nnotarize: %s\n\n' "$*" >&2; exit 1; }

[ -d "$APP/Contents/MacOS" ] || die "no app at $APP (run: make dmg)"
codesign --verify --strict "$APP" || die "the app's signature does not verify"
AUTH=$(codesign -dvv "$APP" 2>&1 | awk -F= '/^Authority=Developer ID Application: / && !f {print $2; f = 1}')
[ -n "$AUTH" ] || die "the app is not signed with a Developer ID Application certificate, so it
cannot be notarized. Install one (Xcode -> Settings -> Accounts -> Manage Certificates ->
+ -> Developer ID Application), then rebuild with: make dist   (docs/release.md)"
codesign -dvv "$APP" 2>&1 | grep 'flags=.*runtime' >/dev/null || die "the app is not signed with the hardened runtime"
xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1 || die "no usable notarytool keychain profile '$PROFILE'.
Create it once (it asks for an app-specific password from appleid.apple.com and keeps it in
your keychain):
  xcrun notarytool store-credentials $PROFILE --apple-id <your Apple ID> --team-id <TEAMID>
or set NOTARY_PROFILE to the name you used."
echo "identity: $AUTH · profile: $PROFILE"

submit() {   # $1 = file; waits, prints the log on failure
  local out id status
  out=$(xcrun notarytool submit "$1" --keychain-profile "$PROFILE" --wait --output-format plist) ||
    true
  id=$(/usr/libexec/PlistBuddy -c 'Print :id' /dev/stdin <<< "$out" 2>/dev/null || true)
  status=$(/usr/libexec/PlistBuddy -c 'Print :status' /dev/stdin <<< "$out" 2>/dev/null || true)
  echo "  submission ${id:-?}: ${status:-unknown}"
  if [ "$status" != Accepted ]; then
    [ -n "$id" ] && xcrun notarytool log "$id" --keychain-profile "$PROFILE" || echo "$out"
    die "notarization of $(basename "$1") was not accepted (log above)"
  fi
}

TMP="$(mktemp -d "${TMPDIR:-/tmp}/live-notary.XXXXXX")"; trap 'rm -rf "$TMP"' EXIT
echo "== 1. notarize the app"
ditto -c -k --keepParent "$APP" "$TMP/app.zip"
submit "$TMP/app.zip"
xcrun stapler staple "$APP"
xcrun stapler validate "$APP"

echo "== 2. rebuild the DMG around the stapled app, notarize it"
DMG=$("$HERE/make_dmg.sh" "$APP" "$OUT_DIR" | awk '/^dmg: / {sub(/^dmg: /, ""); sub(/ \([^)]*\)$/, ""); print}')
[ -f "$DMG" ] || die "make_dmg.sh did not produce a DMG"
submit "$DMG"
xcrun stapler staple "$DMG"
xcrun stapler validate "$DMG"
spctl -a -vvv -t open --context context:primary-signature "$DMG" || true
spctl -a -vvv "$APP"
echo "notarized and stapled: $DMG"
