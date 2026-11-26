#!/usr/bin/env bash
# Validate package metadata and desktop integration without installing as root.
set -euo pipefail
pkg="${1:?Usage: bash packaging/linux/test_deb.sh dist/freeshape_*_amd64.deb}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
dpkg-deb --extract "$pkg" "$tmp"
test "$(dpkg-deb --field "$pkg" Package)" = freeshape
test "$(dpkg-deb --field "$pkg" Architecture)" = amd64
test -x "$tmp/usr/bin/freeshape"
test -x "$tmp/opt/freeshape/FreeShape"
test -f "$tmp/usr/share/doc/freeshape/copyright"
test -f "$tmp/usr/share/doc/freeshape/LICENSE"
test -f "$tmp/usr/share/doc/freeshape/THIRD-PARTY-NOTICES.txt"
test -f "$tmp/usr/share/icons/hicolor/256x256/apps/freeshape.png"
grep -qx 'Exec=freeshape %F' "$tmp/usr/share/applications/freeshape.desktop"
for kind in part assembly drawing; do
  grep -q "application/x-freeshape-$kind" "$tmp/usr/share/mime/packages/freeshape.xml"
done
echo "[FreeShape] DEB structure OK"
