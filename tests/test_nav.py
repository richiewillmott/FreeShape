"""Navigation: wheel zoom scaling, Ctrl+Shift+arrow pan, Touchpad navigation style."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent, QWheelEvent  # noqa: E402


@pytest.fixture
def vp():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    v = Viewport()
    v.cam.w, v.cam.h, v.cam.scale = 800, 600, 0.1
    v.orbit_mode = "centre"
    return v


def _wheel(vp, angle=0, pixel=0, mods=Qt.NoModifier, ax=0, px=0):
    e = QWheelEvent(QPointF(400, 300), QPointF(400, 300), QPoint(px, pixel), QPoint(ax, angle),
                    Qt.NoButton, mods, Qt.NoScrollPhase, False)
    vp.wheelEvent(e)


def _settle(vp):
    while vp._zoom_timer.isActive():
        vp._step_zoom()


def _zoom_for(vp, deltas):
    vp.cam.scale = 0.1
    for d in deltas:
        _wheel(vp, angle=d)
    _settle(vp)
    return vp.cam.scale / 0.1


def test_one_notch_is_10_to_15_percent(vp):
    f = _zoom_for(vp, [120])
    assert 0.85 <= f <= 0.90
    assert _zoom_for(vp, [-120]) == pytest.approx(1 / f, rel=1e-6)


def test_highres_deltas_equal_one_notch(vp):
    one = _zoom_for(vp, [120])
    assert _zoom_for(vp, [15] * 8) == pytest.approx(one, rel=1e-6)


def test_huge_delta_capped(vp):
    assert _zoom_for(vp, [1200]) == pytest.approx(_zoom_for(vp, [120]), rel=1e-6)


def test_zoom_speed(vp):
    vp.zoom_speed = 2.0
    assert _zoom_for(vp, [120]) == pytest.approx(0.88 ** 2, rel=1e-6)
    vp.zoom_speed = 0.5
    assert _zoom_for(vp, [120]) == pytest.approx(0.88 ** 0.5, rel=1e-6)


def _key(vp, key, mods):
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods))


def test_ctrl_shift_arrow_pans(vp):
    t0 = vp.cam.target.copy()
    R0 = vp.cam.R.copy()
    _key(vp, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
    expect = R0[0] * 0.1 * 800 * 0.1
    assert np.allclose(vp.cam.target - t0, expect)
    assert np.allclose(vp.cam.R, R0)
    t1 = vp.cam.target.copy()
    _key(vp, Qt.Key_Up, Qt.ControlModifier | Qt.ShiftModifier)
    assert np.allclose(vp.cam.target - t1, R0[1] * 0.1 * 600 * 0.1)


def test_plain_arrow_still_rotates(vp):
    t0 = vp.cam.target.copy()
    _key(vp, Qt.Key_Right, Qt.NoModifier)
    assert vp._anim is not None
    assert np.allclose(vp.cam.target, t0)


def _move(vp, x, y, mods):
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x, y), QPointF(x, y), Qt.NoButton, Qt.NoButton, mods))


def test_touchpad_shift_pans(vp):
    vp.nav_style = "touchpad"
    _move(vp, 100, 100, Qt.ShiftModifier)
    t0 = vp.cam.target.copy()
    _move(vp, 130, 110, Qt.ShiftModifier)
    assert not np.allclose(vp.cam.target, t0)
    assert np.allclose(vp.cam.target - t0, -vp.cam.R[0] * 30 * 0.1 + vp.cam.R[1] * 10 * 0.1)


def test_touchpad_alt_orbits(vp):
    vp.nav_style = "touchpad"
    _move(vp, 100, 100, Qt.AltModifier)
    R0 = vp.cam.R.copy()
    _move(vp, 140, 100, Qt.AltModifier)
    assert not np.allclose(vp.cam.R, R0)


def test_touchpad_ctrl_shift_zooms(vp):
    vp.nav_style = "touchpad"
    mods = Qt.ControlModifier | Qt.ShiftModifier
    _move(vp, 400, 300, mods)
    _move(vp, 400, 320, mods)
    assert vp.cam.scale != 0.1


def test_onshape_style_ignores_modifier_moves(vp):
    t0, R0, s0 = vp.cam.target.copy(), vp.cam.R.copy(), vp.cam.scale
    for m in (Qt.ShiftModifier, Qt.AltModifier, Qt.ControlModifier | Qt.ShiftModifier):
        _move(vp, 100, 100, m)
        _move(vp, 150, 130, m)
    assert np.allclose(vp.cam.target, t0) and np.allclose(vp.cam.R, R0) and vp.cam.scale == s0


def test_touchpad_scroll_pans_and_ctrl_scroll_zooms(vp):
    vp.nav_style = "touchpad"
    t0 = vp.cam.target.copy()
    _wheel(vp, angle=120, pixel=20)
    assert not np.allclose(vp.cam.target, t0)
    assert np.allclose(vp.cam.target - t0, vp.cam.R[1] * 20 * 0.1)
    _wheel(vp, angle=120, mods=Qt.ControlModifier)
    _settle(vp)
    assert vp.cam.scale < 0.1


# ---------------------------------------------------------------- mouse styles (table driven)
from PySide6.QtCore import Qt as _Qt  # noqa: E402

_B = {"L": _Qt.LeftButton, "M": _Qt.MiddleButton, "R": _Qt.RightButton}
_M = {"S": _Qt.ShiftModifier, "C": _Qt.ControlModifier, "A": _Qt.AltModifier}


def _bm(buttons, mods):
    b, m = _Qt.NoButton, _Qt.NoModifier
    for c in buttons:
        b |= _B[c]
    for c in mods:
        m |= _M[c]
    return b, m


def _drag(vp, buttons, mods, x0=300, y0=300, x1=340, y1=320):
    """Press `buttons` (in order) with `mods`, move, release. Returns (target, R, scale) deltas as a tuple of bools."""
    t0, R0, s0 = vp.cam.target.copy(), vp.cam.R.copy(), vp.cam.scale
    held = _Qt.NoButton
    for c in buttons:
        held |= _B[c]
        _, m = _bm("", mods)
        vp.mousePressEvent(QMouseEvent(QEvent.MouseButtonPress, QPointF(x0, y0), QPointF(x0, y0), _B[c], held, m))
    _, m = _bm("", mods)
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x0 + 10, y0 + 5), QPointF(x0 + 10, y0 + 5),
                                  _Qt.NoButton, held, m))
    vp.mouseMoveEvent(QMouseEvent(QEvent.MouseMove, QPointF(x1, y1), QPointF(x1, y1), _Qt.NoButton, held, m))
    for c in reversed(buttons):
        held &= ~_B[c]
        vp.mouseReleaseEvent(QMouseEvent(QEvent.MouseButtonRelease, QPointF(x1, y1), QPointF(x1, y1),
                                         _B[c], held, m))
    zoomed = vp.cam.scale != s0
    return (not np.allclose(vp.cam.R, R0), not zoomed and not np.allclose(vp.cam.target, t0), zoomed)


ORBIT, PAN, ZOOM = (True, False, False), (False, True, False), (False, False, True)
NONE = (False, False, False)

# style -> [(buttons, mods, expected)]: one orbit, one pan, one zoom (plus the quirks) per FreeCAD style
STYLE_CASES = {
    "cad": [("R", "S", ORBIT), ("M", "", PAN), ("R", "C", PAN), ("R", "CS", ZOOM), ("MR", "", ORBIT),
            ("LM", "", ORBIT), ("R", "", NONE)],
    "gesture": [("L", "", ORBIT), ("R", "", PAN), ("M", "", NONE)],
    "blender": [("M", "", ORBIT), ("M", "S", PAN), ("M", "C", ZOOM), ("LR", "", PAN), ("R", "CS", ZOOM)],
    "solidworks": [("M", "", ORBIT), ("M", "C", PAN), ("M", "S", ZOOM), ("R", "", NONE)],
    "openinventor": [("L", "", ORBIT), ("M", "", PAN), ("L", "CS", PAN), ("LM", "", ZOOM), ("M", "C", ZOOM)],
    "revit": [("M", "S", ORBIT), ("M", "", PAN), ("LR", "", PAN), ("M", "C", ZOOM), ("R", "CS", ZOOM)],
    "openscad": [("L", "", ORBIT), ("R", "", PAN), ("M", "", ZOOM), ("R", "S", ZOOM)],
    "onshape": [("R", "", ORBIT), ("M", "", PAN), ("R", "C", PAN)],
}


@pytest.mark.parametrize("style,buttons,mods,expect", [(s, b, m, e) for s, cs in STYLE_CASES.items()
                                                       for b, m, e in cs])
def test_mouse_style_mapping(vp, style, buttons, mods, expect):
    vp.nav_style = style
    assert _drag(vp, buttons, mods) == expect


@pytest.mark.parametrize("style", ["cad", "gesture", "blender", "solidworks", "openinventor", "revit", "openscad"])
def test_mouse_style_wheel_zooms(vp, style):
    vp.nav_style = style
    assert _zoom_for(vp, [120]) == pytest.approx(0.88, rel=1e-6)


@pytest.mark.parametrize("style", ["cad", "blender", "solidworks", "revit"])
def test_left_drag_selects_not_navigates(vp, style):
    vp.nav_style = style
    assert _drag(vp, "L", "") == NONE


def test_left_orbit_styles_keep_left_for_sketch(vp):
    class Sk:
        dragged = 0
        sk = type("S", (), {"constraints": [], "entities": {}})()
        plane = None

        def mouse_press(self, *a):
            return True

        def mouse_drag(self, *a):
            Sk.dragged += 1

        def mouse_release(self, *a):
            pass

    vp.sketcher = Sk()
    for style in ("gesture", "openinventor", "openscad"):
        vp.nav_style = style
        assert _drag(vp, "L", "") == NONE
        assert _drag(vp, "L", "A") == ORBIT       # Alt+left still orbits while a sketch is active


def test_left_orbit_style_drag_part_wins(vp):
    class DH:
        def press(self, *a):
            return True

        def move(self, *a):
            pass

        def release(self, *a):
            pass

    vp.drag_handler = DH()
    vp.nav_style = "openinventor"
    assert _drag(vp, "L", "") == NONE


def test_every_style_has_help_line_and_orbit_pan_zoom():
    from freeshape.ui import navstyles
    assert set(navstyles.STYLES) >= {"onshape", "touchpad", "cad", "gesture", "blender", "solidworks",
                                     "openinventor", "revit", "openscad"}
    for st in navstyles.STYLES.values():
        h = st.help_line()
        assert h.startswith("Orbit: ") and "Pan: " in h and "Zoom: " in h


def test_old_nav_style_setting_loads(tmp_path):
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui import mainwindow
    from freeshape.ui.viewport import Viewport

    class W:
        set_nav_style = mainwindow.MainWindow.set_nav_style

    w = W()
    w.viewport = Viewport()
    w.nav_actions = {}
    w.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    w.settings.setValue("nav_style", "touchpad")
    mode = str(w.settings.value("mouse_style", w.settings.value("nav_style", "onshape")))
    w.set_nav_style(mode, save=False)
    assert w.viewport.nav_style == "touchpad"
    w.set_nav_style("bogus", save=False)
    assert w.viewport.nav_style == "onshape"
    w.set_nav_style("cad")
    assert w.settings.value("mouse_style") == "cad"
