#!/bin/bash
# Builds dist/freeshape_<rev>_amd64.deb for Debian, Ubuntu and their relatives.
#   bash packaging/linux/build_deb.sh [SOURCE_DIR]
# SOURCE_DIR: the FreeShape source to package (default: this checkout) — so CI can package an older release tag
#   with this script.
# Needs: Python 3.10-3.13 with FreeShape's requirements and PyInstaller (python3 -m pip install -r requirements.txt
#   pyinstaller), and dpkg-deb. SKIP_PYINSTALLER=1 reuses SOURCE_DIR/build/linux-dist (made by build_appimage.sh).
# Like the AppImage, the package uses the system's own OpenGL / X11 / Wayland libraries (listed in Depends), and
# runs on systems at least as new as the one it was built on.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "${1:-$HERE/../..}" && pwd)"
cd "$SRC"
PY="${PYTHON:-python3}"
REV="$("$PY" -c 'import freeshape; print(freeshape.__version__)')"
OUT="dist/freeshape_${REV}_amd64.deb"
if [ "$(dpkg --print-architecture)" != "amd64" ]; then
  echo "[FreeShape] The DEB build currently supports amd64 only." >&2
  exit 1
fi

if [ "${SKIP_PYINSTALLER:-0}" != 1 ] || [ ! -x build/linux-dist/FreeShape/FreeShape ]; then
  echo "[FreeShape] Packing FreeShape $REV with PyInstaller (a few minutes)..."
  "$PY" -m PyInstaller packaging/freeshape.spec --noconfirm --clean --distpath build/linux-dist --workpath build/linux-work
fi

echo "[FreeShape] Checking the packed app..."
rm -f build/selftest.txt
if ! build/linux-dist/FreeShape/FreeShape --self-test "$SRC/build/selftest.txt" || ! grep -qx OK build/selftest.txt; then
  cat build/selftest.txt 2>/dev/null || true
  echo "[FreeShape] The packed app failed its self-test." >&2
  exit 1
fi

echo "[FreeShape] Laying out the package..."
PKG="build/deb/freeshape_${REV}_amd64"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" "$PKG/opt/freeshape" "$PKG/usr/bin" "$PKG/usr/share/applications" \
         "$PKG/usr/share/mime/packages" "$PKG/usr/share/doc/freeshape"
cp -a build/linux-dist/FreeShape/. "$PKG/opt/freeshape/"
# never ship the graphics driver stack or the C++ runtime the drivers use (as the AppImage): the system's own are
# used, or Mesa can fail to load
find "$PKG/opt/freeshape" \( -name 'libGL.so*' -o -name 'libGLX*.so*' -o -name 'libEGL.so*' \
     -o -name 'libGLdispatch.so*' -o -name 'libdrm.so*' -o -name 'libgbm.so*' -o -name 'libxcb.so*' \
     -o -name 'libX11.so*' -o -name 'libwayland-client.so*' -o -name 'libstdc++.so*' -o -name 'libgcc_s.so*' \) -delete

# the command: picks the display / OpenGL the same way the AppImage's AppRun does
cat > "$PKG/usr/bin/freeshape" <<'EOF'
#!/bin/sh
# Starts FreeShape (installed in /opt/freeshape).
if [ -z "${QT_QPA_PLATFORM:-}" ]; then
  if [ -n "${DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=xcb
    export PYOPENGL_PLATFORM="${PYOPENGL_PLATFORM:-glx}"
  elif [ -n "${WAYLAND_DISPLAY:-}" ]; then
    export QT_QPA_PLATFORM=wayland
    export PYOPENGL_PLATFORM="${PYOPENGL_PLATFORM:-egl}"
  fi
fi
exec /opt/freeshape/FreeShape "$@"
EOF

# menu entry, icons and the file types it opens
sed 's/^Exec=.*/Exec=freeshape %F/' "$HERE/freeshape.desktop" > "$PKG/usr/share/applications/freeshape.desktop"
for s in 16 32 48 64 128 256 512; do
  if [ -f "freeshape/assets/logo_${s}.png" ]; then
    mkdir -p "$PKG/usr/share/icons/hicolor/${s}x${s}/apps"
    cp "freeshape/assets/logo_${s}.png" "$PKG/usr/share/icons/hicolor/${s}x${s}/apps/freeshape.png"
  fi
done
cat > "$PKG/usr/share/mime/packages/freeshape.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-freeshape-part">
    <comment>FreeShape Part Studio</comment>
    <glob pattern="*.fshape"/>
    <icon name="freeshape"/>
  </mime-type>
  <mime-type type="application/x-freeshape-assembly">
    <comment>FreeShape assembly</comment>
    <glob pattern="*.fasm"/>
    <icon name="freeshape"/>
  </mime-type>
  <mime-type type="application/x-freeshape-drawing">
    <comment>FreeShape drawing</comment>
    <glob pattern="*.fdwg"/>
    <icon name="freeshape"/>
  </mime-type>
</mime-info>
EOF
cat > "$PKG/usr/share/doc/freeshape/copyright" <<EOF
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: FreeShape
Source: https://github.com/richiewillmott/FreeShape

Files: *
Copyright: 2026 Richard Willmott
License: GPL-3+
 FreeShape is free software under the GNU General Public License, version 3 or later.
 On Debian systems the full text is in /usr/share/common-licenses/GPL-3.
 Third-party notices are in /opt/freeshape (THIRD-PARTY-NOTICES.txt, where included).
EOF

for notice in LICENSE THIRD-PARTY-NOTICES.txt; do
  if [ -f "$notice" ]; then
    install -m 0644 "$notice" "$PKG/usr/share/doc/freeshape/$notice"
  fi
done

SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: freeshape
Version: $REV
Section: graphics
Priority: optional
Architecture: amd64
Installed-Size: $SIZE
Maintainer: Richard Willmott <richiewillmott@users.noreply.github.com>
Homepage: https://github.com/richiewillmott/FreeShape
Depends: libgl1, libegl1, libopengl0, libxkbcommon0, libxkbcommon-x11-0, libxcb-cursor0, libxcb-icccm4,
 libxcb-keysyms1, libxcb-randr0, libxcb-render-util0, libxcb-shape0, libxcb-xinerama0, libfontconfig1, libdbus-1-3,
 libstdc++6
Description: free, offline, Onshape-style parametric CAD
 FreeShape is parametric CAD that works like Onshape but runs on your own
 computer: constrained sketches, extrude, revolve, sweep and loft, fillets and
 chamfers, assemblies with mates, 2D drawings, sheet metal, variables and
 configurations, version history, and STEP / STL export. Built on OpenCascade.
EOF

# ownership and modes the way dpkg expects
find "$PKG" -type d -exec chmod 755 {} +
find "$PKG" -type f -exec chmod 644 {} +
chmod 755 "$PKG/usr/bin/freeshape" "$PKG/opt/freeshape/FreeShape"
find "$PKG/opt/freeshape" -type f \( -name '*.so' -o -name '*.so.*' \) -exec chmod 755 {} +

mkdir -p dist
rm -f "$OUT"
echo "[FreeShape] Building $OUT..."
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUT"
dpkg-deb --info "$OUT"
echo "[FreeShape] Done: $OUT"
