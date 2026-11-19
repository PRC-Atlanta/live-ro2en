#!/bin/bash
# Write the Sparkle appcasts for a release (docs/release.md "Updates"):
#   DIST/appcast.xml          Live RO2EN   -> LiveRO2EN-<version>.dmg
#   DIST/appcast-remote.xml   Live Remote  -> LiveRemote-<version>.dmg
# Each lists one item, the newest version. Upload both, with the DMGs, to the GitHub release:
# the apps read https://github.com/PRC-Atlanta/live-ro2en/releases/latest/download/appcast*.xml.
#
#   packaging/make_appcast.sh [DIST]                 default build/dist
#
# The DMGs must be the final ones (notarized and stapled): the EdDSA signature covers their bytes.
# The script refuses a DMG that is not: `stapler validate`, Gatekeeper on the DMG
# (spctl -t open, primary signature) and a Developer ID signature on the app inside. Only a
# test feed on this Mac (APPCAST_URL_PREFIX http://127.0.0.1:… or http://localhost:…) skips that.
# Signing uses the private key in the login keychain (Sparkle generate_keys, account live-ro2en);
# the script never reads or prints it.
#   APPCAST_URL_PREFIX   where the DMGs will be downloaded from
#                        (default https://github.com/PRC-Atlanta/live-ro2en/releases/download/v<version>/)
#   SPARKLE_ACCOUNT      keychain account of the key (default live-ro2en)
#   APPCAST_ONLY         "ro2en" or "remote": write only that one
#
# sign_update (which reads the private key) is the Sparkle copy SwiftPM fetched for the app
# build, in macos/.build/artifacts: run `swift package resolve` in macos/ first.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DIST="${1:-$REPO/build/dist}"
# sign_update reads the private signing key, so it must be the real Sparkle tool. Use the copy
# SwiftPM fetched for the app build: macos/Package.swift pins Sparkle exactly (2.10.1) and
# SwiftPM checks the binary artifact against the checksum in Sparkle's own manifest before it
# unpacks it. Nothing is downloaded here.
TOOLS="$REPO/macos/.build/artifacts/sparkle/Sparkle/bin"
ACCOUNT="${SPARKLE_ACCOUNT:-live-ro2en}"
die() { printf '\nmake_appcast: %s\n\n' "$*" >&2; exit 1; }

[ -x "$TOOLS/sign_update" ] || die "no Sparkle sign_update at $TOOLS.
Run \`swift package resolve\` (or \`swift build\`) in macos/ first: SwiftPM fetches the pinned,
checksum-verified Sparkle there."

# The DMG being checked is mounted here; detached and removed on every exit, failures included.
MNT=""
MOUNTED=0
unmount() {
  if [ "$MOUNTED" = 1 ]; then
    hdiutil detach -quiet "$MNT" 2>/dev/null || hdiutil detach -quiet -force "$MNT" 2>/dev/null || true
    MOUNTED=0
  fi
  if [ -n "$MNT" ]; then rmdir "$MNT" 2>/dev/null || true; MNT=""; fi
}
trap unmount EXIT

# a test feed served from this Mac may use an ad hoc DMG; a published one never
case "${APPCAST_URL_PREFIX:-}" in
  http://127.0.0.1:*|http://localhost:*) LOCAL_TEST=1 ;;
  *) LOCAL_TEST=0 ;;
esac

# require_notarized DMG: stapled, accepted by Gatekeeper, with a Developer ID app inside ($2)
require_notarized() {
  local dmg="$1" app="$2"
  if [ "$LOCAL_TEST" = 1 ]; then
    echo "make_appcast: local test feed: notarization of $(basename "$dmg") not checked"; return 0
  fi
  xcrun stapler validate -q "$dmg" >/dev/null 2>&1 ||
    die "$dmg has no stapled notarization ticket (notarize and staple it first; docs/release.md)"
  spctl -a -t open --context context:primary-signature "$dmg" 2>/dev/null ||
    die "Gatekeeper rejects $dmg (spctl -a -t open --context context:primary-signature -vv \"$dmg\")"
  # not grep -q: it exits at the first match and codesign, still writing, fails the pipe (pipefail)
  codesign -dvv "$app" 2>&1 | grep '^Authority=Developer ID Application: ' >/dev/null ||
    die "$(basename "$app") in $dmg is not signed with a Developer ID Application certificate"
}

plist() { /usr/libexec/PlistBuddy -c "Print :$2" "$1"; }

# appcast TITLE PLIST DMG_PREFIX OUT
appcast() {
  local title="$1" pl="$2" prefix="$3" out="$4"
  local ver build minos dmg url sig pub
  ver=$(plist "$pl" CFBundleShortVersionString); build=$(plist "$pl" CFBundleVersion)
  minos=$(plist "$pl" LSMinimumSystemVersion)
  dmg="$DIST/$prefix-$ver.dmg"
  [ -f "$dmg" ] || { echo "make_appcast: no $dmg"; exit 1; }
  # the app inside must be the version the appcast announces
  MNT=$(mktemp -d)
  hdiutil attach -quiet -nobrowse -readonly -mountpoint "$MNT" "$dmg" ||
    die "could not mount $dmg"
  MOUNTED=1
  local inside; inside=$(find "$MNT" -maxdepth 1 -name '*.app' | head -1)
  [ -n "$inside" ] || die "no .app inside $dmg"
  local ib; ib=$(plist "$inside/Contents/Info.plist" CFBundleVersion) ||
    die "cannot read CFBundleVersion of $(basename "$inside") in $dmg"
  local feed; feed=$(plist "$inside/Contents/Info.plist" SUFeedURL 2>/dev/null || true)
  require_notarized "$dmg" "$inside"       # dies with the reason; the EXIT trap detaches
  unmount
  [ "$ib" = "$build" ] || { echo "make_appcast: $dmg holds build $ib, $pl says $build"; exit 1; }
  [ -n "$feed" ] || { echo "make_appcast: the app in $dmg has no SUFeedURL"; exit 1; }
  url="${APPCAST_URL_PREFIX:-https://github.com/PRC-Atlanta/live-ro2en/releases/download/v$ver/}$(basename "$dmg")"
  sig=$("$TOOLS/sign_update" --account "$ACCOUNT" "$dmg")      # sparkle:edSignature="…" length="…"
  pub=$(LC_ALL=C date -u "+%a, %d %b %Y %H:%M:%S +0000")
  cat > "$out" <<XML
<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>$title</title>
    <item>
      <title>$title $ver</title>
      <pubDate>$pub</pubDate>
      <link>https://github.com/PRC-Atlanta/live-ro2en/releases/tag/v$ver</link>
      <sparkle:version>$build</sparkle:version>
      <sparkle:shortVersionString>$ver</sparkle:shortVersionString>
      <sparkle:minimumSystemVersion>$minos</sparkle:minimumSystemVersion>
      <enclosure url="$url" type="application/octet-stream" $sig/>
    </item>
  </channel>
</rss>
XML
  echo "appcast: $out ($title $ver, build $build) -> $url"
}

[ "${APPCAST_ONLY:-ro2en}" != ro2en ] || appcast "Live RO2EN"  "$REPO/macos/Info.plist"  LiveRO2EN  "$DIST/appcast.xml"
[ "${APPCAST_ONLY:-remote}" != remote ] || appcast "Live Remote" "$REPO/remote/Info.plist" LiveRemote "$DIST/appcast-remote.xml"
