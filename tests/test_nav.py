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
