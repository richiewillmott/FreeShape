#!/bin/bash
# Builds dist/FreeShape-<rev>-x86_64.AppImage on Linux.
#   bash packaging/linux/build_appimage.sh
# Needs: Python 3.10-3.13 with FreeShape's requirements and PyInstaller installed
#   (python3 -m pip install -r requirements.txt pyinstaller), and internet once to fetch appimagetool.
# An AppImage runs on Linux systems at least as new as the one it was built on (glibc), so build on the
# oldest distro you want to support.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
REV="$("$PY" -c 'import freeshape; print(freeshape.__revision__)')"
OUT="dist/FreeShape-$REV-x86_64.AppImage"

echo "[FreeShape] Packing FreeShape with PyInstaller (a few minutes)..."
"$PY" -m PyInstaller packaging/freeshape.spec --noconfirm --clean --distpath build/linux-dist --workpath build/linux-work

echo "[FreeShape] Checking the packed app..."
rm -f build/selftest.txt
build/linux-dist/FreeShape/FreeShape --self-test "$ROOT/build/selftest.txt" || true
if ! grep -q '^OK' build/selftest.txt 2>/dev/null; then
  cat build/selftest.txt 2>/dev/null || true
  echo "[FreeShape] The packed app failed its self-test." >&2
  exit 1
fi

echo "[FreeShape] Making the AppDir..."
APPDIR=build/FreeShape.AppDir
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp -a build/linux-dist/FreeShape "$APPDIR/usr/lib/freeshape"
# never ship the graphics driver stack (or the C++ runtime the drivers use): the user's own OpenGL / X11 /
# Wayland / libstdc++ libraries must be used, or Mesa can fail to load on newer distros
find "$APPDIR/usr/lib/freeshape" \( -name 'libGL.so*' -o -name 'libGLX*.so*' -o -name 'libEGL.so*' \
     -o -name 'libGLdispatch.so*' -o -name 'libdrm.so*' -o -name 'libgbm.so*' -o -name 'libxcb.so*' \
     -o -name 'libX11.so*' -o -name 'libwayland-client.so*' -o -name 'libstdc++.so*' -o -name 'libgcc_s.so*' \) -delete
cp packaging/linux/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/linux/freeshape.desktop "$APPDIR/freeshape.desktop"
cp packaging/linux/freeshape.desktop "$APPDIR/usr/share/applications/"
cp freeshape/assets/logo_256.png "$APPDIR/freeshape.png"
cp freeshape/assets/logo_256.png "$APPDIR/usr/share/icons/hicolor/256x256/apps/freeshape.png"
ln -sf freeshape.png "$APPDIR/.DirIcon"

TOOL=build/appimagetool-x86_64.AppImage
if [ ! -x "$TOOL" ]; then
  echo "[FreeShape] Downloading appimagetool..."
  curl -fL -o "$TOOL" https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
  chmod +x "$TOOL"
fi
echo "[FreeShape] Building $OUT..."
mkdir -p dist
rm -f "$OUT"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$TOOL" --comp zstd "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "[FreeShape] Done: $OUT"
