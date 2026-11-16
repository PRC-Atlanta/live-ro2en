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
# Signing uses the private key in the login keychain (Sparkle generate_keys, account live-ro2en);
# the script never reads or prints it.
#   APPCAST_URL_PREFIX   where the DMGs will be downloaded from
#                        (default https://github.com/PRC-Atlanta/live-ro2en/releases/download/v<version>/)
#   SPARKLE_ACCOUNT      keychain account of the key (default live-ro2en)
#   APPCAST_ONLY         "ro2en" or "remote": write only that one
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$HERE")"
DIST="${1:-$REPO/build/dist}"
SPARKLE_VER=2.10.1
TOOLS="$REPO/build/cache/sparkle-$SPARKLE_VER/bin"
ACCOUNT="${SPARKLE_ACCOUNT:-live-ro2en}"

if [ ! -x "$TOOLS/sign_update" ]; then
  echo "== Sparkle $SPARKLE_VER tools (sign_update) -> build/cache"
  mkdir -p "$REPO/build/cache"
  curl -fsSL -o "$REPO/build/cache/Sparkle-$SPARKLE_VER.tar.xz" \
    "https://github.com/sparkle-project/Sparkle/releases/download/$SPARKLE_VER/Sparkle-$SPARKLE_VER.tar.xz"
  mkdir -p "$REPO/build/cache/sparkle-$SPARKLE_VER"
  tar -xJf "$REPO/build/cache/Sparkle-$SPARKLE_VER.tar.xz" -C "$REPO/build/cache/sparkle-$SPARKLE_VER"
fi

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
  hdiutil detach -quiet "$mnt"; rmdir "$mnt"
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
