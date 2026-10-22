"""Run with:  python -m freeshape [file.fshape]"""
import os
import sys


def _deps_ok():
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("OCP", "PySide6", "OpenGL", "numpy", "ezdxf"))


def _self_test(out_path):
    """Quick check that the geometry engine and the display libraries load (used after building the .exe)."""
    try:
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from freeshape import kernel as K
        box = BRepPrimAPI_MakeBox(10, 20, 30).Shape()
        m = K.tessellate(box)
        from PySide6.QtWidgets import QApplication  # noqa: F401
        from PySide6 import QtOpenGLWidgets, QtSvg  # noqa: F401
        from OpenGL import GL  # noqa: F401
        from freeshape.ui import mainwindow, splash, drawing_ui  # noqa: F401
        from freeshape import drawing, dxf, hlr_worker, sheetmetal  # noqa: F401
        assets = os.path.join(os.path.dirname(os.path.abspath(K.__file__)), "assets", "freeshape.ico")
        ok = abs(K.volume(box) - 6000) < 1e-6 and len(m.tris) > 0 and os.path.exists(assets)
        msg = "OK" if ok else "FAILED: wrong result"
    except Exception as e:
        import traceback
        msg = "FAILED: " + "".join(traceback.format_exception(e))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(msg)
    return 0 if msg == "OK" else 1


def main():
    if len(sys.argv) >= 4 and sys.argv[1] == "--step-worker":
        # the packaged .exe runs its own STEP reader this way (there's no separate python.exe to use)
        from .step_worker import run
        run(sys.argv[2], sys.argv[3])
        return
    if len(sys.argv) >= 6 and sys.argv[1] == "--hlr-worker":
        from .hlr_worker import run as hlr_run
        hlr_run(*sys.argv[2:6])
        return
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        sys.exit(_self_test(sys.argv[2]))
    if not getattr(sys, "frozen", False) and not _deps_ok():
        if os.environ.get("FREESHAPE_BOOTSTRAPPED"):
            print("[FreeShape] Requirements are still missing after setup; delete the .venv folder and try again.")
            sys.exit(1)
        # missing requirements: set up the private environment and relaunch from there
        from .bootstrap import launch
        launch(sys.argv[1:])
        return
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QSurfaceFormat
    fmt = QSurfaceFormat()
    fmt.setSamples(4)
    fmt.setDepthBufferSize(24)
    QSurfaceFormat.setDefaultFormat(fmt)
    app = QApplication(sys.argv)
    app.setApplicationName("FreeShape")
    if sys.platform == "win32":
        # so Windows shows FreeShape's icon in the taskbar instead of Python's
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FreeShape.App")
        except Exception:
            pass
    # splash first: the geometry kernel takes a few seconds to load, so show something straight away
    splash = None
    if not os.environ.get("FREESHAPE_NO_SPLASH"):
        from .ui.splash import show_splash
        splash = show_splash()

    def step(text, frac):
        if splash is not None:
            splash.step(text, frac)
    step("Loading the geometry kernel…", 0.15)
    from . import kernel  # noqa: F401  (the slow import: OpenCascade)
    step("Loading the interface…", 0.6)
    from .ui.mainwindow import app_icon
    app.setWindowIcon(app_icon())
    from .ui.mainwindow import MainWindow
    path = sys.argv[1] if len(sys.argv) > 1 else None
    step(f"Opening {os.path.basename(path)}…" if path else "Getting the workspace ready…", 0.8)
    w = MainWindow(path)
    w.show()
    if splash is not None:
        splash.finish(w)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
