"""Navigation: mouse styles, wheel zoom, Ctrl+Shift+arrow pan, the N key, Shift+E from a sketch and dialogs that
remember the last folder."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint, QPointF  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui import navstyles as NS  # noqa: E402


def test_style_table():
    L, M, R = Qt.LeftButton, Qt.MiddleButton, Qt.RightButton
    none, shift, ctrl = Qt.NoModifier, Qt.ShiftModifier, Qt.ControlModifier
    assert NS.drag_action("onshape", R, none) == "orbit"
    assert NS.drag_action("onshape", M, none) == "pan"
    assert NS.drag_action("onshape", R, ctrl) == "pan"
    assert NS.drag_action("onshape", L, none) is None              # left drag still box-selects
    assert NS.drag_action("cad", L | M, none) == "orbit"           # a chord
    assert NS.drag_action("blender", M, shift) == "pan"
    assert NS.drag_action("openinventor", L, none) == "orbit"
    assert NS.drag_action("onshape", R, ctrl | shift) is None      # combinations no rule uses
    assert NS.wheel_action("touchpad", none) == "pan" and NS.wheel_action("touchpad", ctrl) == "zoom"
    assert NS.wheel_action("solidworks", none) == "zoom_reverse"
    assert NS.drag_action("nonsense", R, none) == "orbit"          # unknown style: Onshape
    for st in NS.STYLES.values():
        assert st["label"] and st["rules"]


def _window():
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    os.environ["FREESHAPE_NO_PLUGINS"] = "1"          # your own installed plugins stay out of the tests
    from freeshape.ui.mainwindow import MainWindow
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 30), s.add_point(0, 30)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=10, op="new")
    doc.add_feature(e)
    doc.hidden_features |= {"Top", "Front", "Right"}
    w = MainWindow()
    w.resize(1200, 800)
    w._add_tab(doc)
    app.processEvents()
    vp = w.viewport
    vp.set_standard_view("Isometric", animate=False)
    vp.fit(animate=False)
    return app, w, vp


def test_n_key_rules():
    app, w, vp = _window()
    asked = []
    real = vp.look_along
    vp.look_along = lambda normal=None, anchor=None, animate=True, axes=(): asked.append(normal)
    s = vp.cam.to_screen(np.array([[20.0, 15.0, 10.0]]))[0]
    vp._cursor_xy = (float(s[0]), float(s[1]))                 # the mouse over the top face
    w._normal_key()
    assert np.allclose(np.abs(asked[-1]), [0, 0, 1])
    vp._cursor_xy = (2.0, 2.0)                                 # over empty space, nothing selected
    vp.clear_selection()
    w._normal_key()
    assert asked[-1] is None                                   # -> look from the other side
    vp.look_along = real
    vp.look_along(np.array([0, 0, 1.0]), animate=False)
    assert np.allclose(vp.cam.R[2], [0, 0, 1], atol=1e-9)      # from the nearer side
    vp.look_along(np.array([0, 0, 1.0]), animate=False)
    assert np.allclose(vp.cam.R[2], [0, 0, -1], atol=1e-9)     # already square to it: the other side
    vp.set_standard_view("Right", animate=False)
    vp.look_along(None, animate=False)
    assert np.allclose(vp.cam.R[2], [-1, 0, 0], atol=1e-9)     # Right becomes Left


def test_wheel_is_capped_and_ctrl_shift_arrow_pans():
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    app, w, vp = _window()

    def wheel(dy):
        vp._zoom_left = 0.0
        ev = QWheelEvent(QPointF(300, 300), QPointF(300, 300), QPoint(0, 0), QPoint(0, dy), Qt.NoButton,
                         Qt.NoModifier, Qt.NoScrollPhase, False)
        app.sendEvent(vp, ev)
        return vp._zoom_left
    one = wheel(120)
    assert abs(wheel(600) - one) < 1e-12                      # a burst counts as one notch at most
    assert abs(wheel(15) - one / 8) < 1e-12                    # small steps scale with the amount
    vp.zoom_speed = 2.0
    assert abs(wheel(120) - 2 * one) < 1e-12
    vp._zoom_timer.stop()
    t0 = vp.cam.target.copy()
    QTest.keyClick(vp, Qt.Key_Right, Qt.ControlModifier | Qt.ShiftModifier)
    assert not np.allclose(t0, vp.cam.target)


def test_shift_e_from_a_sketch_opens_extrude_with_its_area():
    from freeshape.ui.viewport import Pick
    app, w, vp = _window()
    vp.set_selection([Pick("plane", ("std", "Front"))])
    w.start_sketch()
    sk = w.sketcher
    p = [sk.sk.add_point(x, y) for x, y in [(0, 0), (10, 0), (10, 10), (0, 10)]]
    for a, b in zip(p, p[1:] + p[:1]):
        sk.sk.add_line(a, b)
    sk.changed(True)
    w._feature_from_sketch("extrude")
    assert w.mode == "dialog" and w.dialog.ftype == "extrude"
    assert len(w.dialog.fields["profiles"].items) == 1
    w.cancel_dialog()


def test_dialogs_start_in_the_last_folder(tmp_path):
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui import filedlg
    before = filedlg._settings().value(filedlg._KEY, "")
    try:
        os.makedirs(tmp_path / "a", exist_ok=True)
        filedlg.remember(str(tmp_path / "a" / "drawing.dxf"))
        assert filedlg.start_path("part.step") == os.path.join(str(tmp_path / "a"), "part.step")
        full = str(tmp_path / "elsewhere.fshape")
        assert filedlg.start_path(full) == full                # a full path (Save as an existing file) wins
    finally:
        filedlg._settings().setValue(filedlg._KEY, before)      # leave the user's setting as it was


def test_n_squares_the_view_to_the_face_edges():
    app, w, vp = _window()
    vp.set_standard_view("Isometric", animate=False)
    vp.look_along(np.array([0, 0, 1.0]), animate=False, axes=[np.array([0.866, 0.5, 0])])
    R = vp.cam.R
    on_screen = np.array([0.866, 0.5, 0]) @ R[0], np.array([0.866, 0.5, 0]) @ R[1]
    assert min(abs(on_screen[0]), abs(on_screen[1])) < 1e-3          # the edge runs level or upright
    vp.set_standard_view("Isometric", animate=False)
    vp.look_along(np.array([0, 0, 1.0]), animate=False)             # no edges given: lines up with X / Y
    assert min(abs(vp.cam.R[0] @ [1, 0, 0]), abs(vp.cam.R[1] @ [1, 0, 0])) < 1e-9
