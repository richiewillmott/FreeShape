# PyInstaller recipe for FreeShape.   Build with:  build_exe.bat   (or:  pyinstaller packaging/freeshape.spec)
# On Linux, packaging/linux/build_appimage.sh uses this same recipe to make FreeShape-x86_64.AppImage.
# Produces dist/FreeShape/FreeShape.exe plus its support files (a folder build starts much faster than a
# single-file exe, which would unpack ~500 MB to a temp folder on every launch).
import glob
import os
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import OCP  # noqa: E402  (only to find where it's installed)

site = os.path.dirname(os.path.dirname(os.path.abspath(OCP.__file__)))
binaries, datas = [], []
# let PyInstaller find OpenCascade's and VTK's DLLs (done here rather than in the .bat, which trips over spaces in paths)
os.environ["PATH"] = os.pathsep.join([os.path.join(site, "cadquery_ocp.libs"), os.path.join(site, "vtkmodules"),
                                      os.path.join(site, "OCP"), os.environ.get("PATH", "")])

# OpenCascade (OCP) and the parts of VTK it links to. Normally PyInstaller works out exactly which libraries
# are needed (their folders are on the search path below). FREESHAPE_FULL=1 copies all of them instead — bigger,
# but a sure thing; build_exe.bat falls back to it automatically if the quick self-test of the exe fails.
binaries += [(b, d) for b, d in collect_dynamic_libs("OCP")]
if os.environ.get("FREESHAPE_FULL"):
    for lib in glob.glob(os.path.join(site, "cadquery_ocp.libs", "*")):
        binaries.append((lib, "cadquery_ocp.libs"))
    for b, d in collect_dynamic_libs("vtkmodules"):
        name = os.path.basename(b).lower()
        if name.endswith(".dll") or name.startswith("libvtk"):      # C++ libraries, not vtk*.pyd wrappers
            binaries.append((b, d))

# FreeShape's own files: icons, logo, examples
datas.append((os.path.join(ROOT, "freeshape", "assets"), os.path.join("freeshape", "assets")))
datas.append((os.path.join(ROOT, "examples"), "examples"))
datas.append((os.path.join(ROOT, "README.md"), "."))
datas.append((os.path.join(ROOT, "CHANGES.md"), "."))
datas.append((os.path.join(ROOT, "LICENSE"), "."))
datas.append((os.path.join(ROOT, "THIRD-PARTY-NOTICES.txt"), "."))
# ezdxf (DXF export with real AutoCAD dimensions) ships a few data files
try:
    from PyInstaller.utils.hooks import collect_data_files
    datas += collect_data_files("ezdxf")
except Exception:
    pass

hidden = collect_submodules("freeshape")
if sys.platform.startswith("linux"):
    hidden += ["OpenGL.platform.egl", "OpenGL.raw.GLX._types"]
hidden = hidden + ["ezdxf", "ezdxf.render.dimension", "OCP", "OCP.OCP", "PySide6.QtSvg", "PySide6.QtOpenGL",
                                            "PySide6.QtOpenGLWidgets", "OpenGL.GL", "OpenGL.GL.shaders",
                                            "OpenGL.platform.win32" if sys.platform == "win32" else "OpenGL.platform.glx",
                                            "OpenGL.arrays.numpymodule",
                                            "OpenGL.arrays.ctypesarrays", "OpenGL.arrays.numbers"]

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[ROOT, os.path.join(site, "cadquery_ocp.libs"), os.path.join(site, "vtkmodules")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    runtime_hooks=[os.path.join(HERE, "rthook_dlls.py")],
    excludes=["tkinter", "matplotlib", "vtk", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.QtMultimedia", "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization",
              "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(a.pure)
icon = os.path.join(ROOT, "freeshape", "assets", "freeshape.ico")
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="FreeShape",
    icon=icon if sys.platform == "win32" else None,
    version=os.path.join(HERE, "version_info.txt") if sys.platform == "win32" else None,
    console=False,          # a normal windowed app: no black console window
    disable_windowed_traceback=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="FreeShape")
