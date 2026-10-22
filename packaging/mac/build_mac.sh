#!/bin/bash
# Builds dist/FreeShape.app and dist/FreeShape-<rev>-mac-<arch>.dmg on a Mac.
#   bash packaging/mac/build_mac.sh
# Needs Python 3.10-3.13 with FreeShape's requirements and PyInstaller:
#   python3 -m pip install -r requirements.txt pyinstaller
# GitHub Actions runs this automatically (.github/workflows/mac.yml), so you don't need a Mac yourself.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
REV="$("$PY" -c 'import freeshape; print(freeshape.__revision__)')"
ARCH="$(uname -m)"; [ "$ARCH" = "arm64" ] || ARCH="intel"
mkdir -p build dist

echo "[FreeShape] Making the app icon..."
ICONSET=build/FreeShape.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
SRC=freeshape/assets/logo_512.png
for s in 16 32 128 256 512; do
  sips -z $s $s "$SRC" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  d=$((s * 2)); [ $d -le 512 ] && sips -z $d $d "$SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
cp "$SRC" "$ICONSET/icon_512x512@2x.png"
iconutil -c icns "$ICONSET" -o build/FreeShape.icns

echo "[FreeShape] Packing FreeShape with PyInstaller (a few minutes)..."
"$PY" -m PyInstaller packaging/freeshape.spec --noconfirm --clean --distpath dist --workpath build/mac-work

echo "[FreeShape] Checking the packed app..."
rm -f build/selftest.txt
dist/FreeShape.app/Contents/MacOS/FreeShape --self-test "$ROOT/build/selftest.txt" || true
if ! grep -q '^OK' build/selftest.txt 2>/dev/null; then
  cat build/selftest.txt 2>/dev/null || true
  echo "[FreeShape] The packed app failed its self-test." >&2
  exit 1
fi

# not signed with an Apple Developer ID: sign it "ad hoc" so Apple Silicon Macs will run it at all
codesign --force --deep --sign - dist/FreeShape.app

echo "[FreeShape] Making the .dmg..."
DMG="dist/FreeShape-$REV-mac-$ARCH.dmg"
STAGE=build/dmg
rm -rf "$STAGE" "$DMG"; mkdir -p "$STAGE"
cp -R dist/FreeShape.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "FreeShape" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "[FreeShape] Done: $DMG"
