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
#   SPARKLE_SHA256       sha256 of Sparkle-$SPARKLE_VER.tar.xz (default: the pin below)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DIST="${1:-$REPO/build/dist}"
SPARKLE_VER=2.10.1
# sha256 of the official Sparkle-$SPARKLE_VER.tar.xz (the GitHub release asset). sign_update
# reads the private signing key, so the tarball it comes from must be the real one.
# TODO(release): fill in from the Sparkle release page (or a verified download) and keep it
# with SPARKLE_VER; the script refuses to run while it is empty.
SPARKLE_SHA256_PIN=""
SPARKLE_SHA256="${SPARKLE_SHA256:-$SPARKLE_SHA256_PIN}"
CACHE="$REPO/build/cache"
TARBALL="$CACHE/Sparkle-$SPARKLE_VER.tar.xz"
TOOLS="$CACHE/sparkle-$SPARKLE_VER/bin"
ACCOUNT="${SPARKLE_ACCOUNT:-live-ro2en}"
die() { printf '\nmake_appcast: %s\n\n' "$*" >&2; exit 1; }

[ -n "$SPARKLE_SHA256" ] || die "no sha256 pinned for Sparkle-$SPARKLE_VER.tar.xz.
Set SPARKLE_SHA256_PIN in packaging/make_appcast.sh (or SPARKLE_SHA256 in the environment) to the
official tarball's sha256 before signing an appcast: sign_update handles the private key."
sha_ok() { [ "$(shasum -a 256 "$1" | awk '{print $1}')" = "$SPARKLE_SHA256" ]; }

# Checked every run, cached or not; the tools are unpacked again from the checked tarball.
mkdir -p "$CACHE"
if [ ! -f "$TARBALL" ]; then
  echo "== Sparkle $SPARKLE_VER tools (sign_update) -> build/cache"
  curl -fsSL -o "$TARBALL.partial" \
    "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VER/Sparkle-$SPARKLE_VER.tar.xz"
  sha_ok "$TARBALL.partial" || { rm -f "$TARBALL.partial"; die "the downloaded Sparkle-$SPARKLE_VER.tar.xz does not match its sha256 pin (deleted)"; }
  mv "$TARBALL.partial" "$TARBALL"
fi
sha_ok "$TARBALL" || die "$TARBALL does not match its sha256 pin: delete it and run again"
rm -rf "$CACHE/sparkle-$SPARKLE_VER"
mkdir -p "$CACHE/sparkle-$SPARKLE_VER"
tar -xJf "$TARBALL" -C "$CACHE/sparkle-$SPARKLE_VER"
[ -x "$TOOLS/sign_update" ] || die "no bin/sign_update in Sparkle-$SPARKLE_VER.tar.xz"

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
  codesign -dvv "$app" 2>&1 | grep -q '^Authority=Developer ID Application: ' ||
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
  local mnt; mnt=$(mktemp -d)
  hdiutil attach -quiet -nobrowse -readonly -mountpoint "$mnt" "$dmg"
  local inside; inside=$(find "$mnt" -maxdepth 1 -name '*.app' | head -1)
  local ib; ib=$(plist "$inside/Contents/Info.plist" CFBundleVersion)
  local feed; feed=$(plist "$inside/Contents/Info.plist" SUFeedURL 2>/dev/null || true)
  local checked=0
  ( require_notarized "$dmg" "$inside" ) && checked=1     # subshell: detach even when it fails
  hdiutil detach -quiet "$mnt"; rmdir "$mnt"
  [ "$checked" = 1 ] || exit 1                            # the reason is printed above
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
