"""Select other… (` and right-click), Tab to step through what's under the mouse, the right-click selection tools
(tangent-connected, loop, same size …), and Onshape's keyboard shortcuts."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint, QEvent  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402

from freeshape import kernel as K, selection_tools as ST  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


# --------------------------------------------------------------------------- selection tools on a real shape

def _filleted_box_with_holes():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.gp import gp_Pnt, gp_Ax2, gp_Dir
    box = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 40, 30, 10).Shape()
    mk = BRepFilletAPI_MakeFillet(box)
    for e in K.edges(box):
        if abs(K.edge_info(e)["c"][2] - 10) < 1e-6:
            mk.Add(2.0, e)
    sh = mk.Shape()
    for x, y, r in ((10, 10, 3), (30, 10, 3), (20, 22, 2)):
        sh = K.cut(sh, BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), r, 20).Shape())
    return sh


def test_selection_tools():
    sh = _filleted_box_with_holes()
    fs, es = K.faces(sh), K.edges(sh)
    top = next(i for i, f in enumerate(fs) if K.face_info(f).get("n") and K.face_info(f)["n"][2] > 0.99)
    tan = ST.tangent_faces(sh, top)
    assert top in tan and len(tan) == 9                      # top, its 4 fillets and on round to the 4 sides
    assert not any(abs(K.face_info(fs[i]).get("r", 0) - 3) < 1e-9 for i in tan)       # not the holes
    holes6 = [i for i, f in enumerate(fs) if abs(K.face_info(f).get("r", 0) - 3) < 1e-9]
    assert len(holes6) == 2 and ST.same_faces(sh, holes6[0]) == holes6                 # every Ø6, not the Ø4
    rim = next(i for i, e in enumerate(es) if abs(K.edge_info(e).get("r", 0) - 3) < 1e-9)
    assert ST.edge_loop(sh, rim) == [rim]                     # a hole's rim is a loop on its own
    assert len(ST.same_edges(sh, rim)) == 4                   # both rims of both Ø6 holes
    straight = next(i for i, e in enumerate(es) if K.edge_info(e)["type"] == 0
                    and abs(K.edge_info(e)["c"][2] - 10) < 1e-6)
    assert len(ST.edge_loop(sh, straight)) == 4               # the top face's outline
    arc = next(i for i, e in enumerate(es) if K.edge_info(e)["type"] == 0
               and abs(K.edge_info(e)["c"][2] - 8) < 1e-6)   # where a fillet meets a side
    assert len(ST.tangent_edges(sh, arc)) >= 1
    assert set(ST.face_edges(sh, top)) >= set(ST.edge_loop(sh, straight))
    assert set(ST.connected_faces(sh, top)) >= {top}


# --------------------------------------------------------------------------- in the window

def _win():
    from test_plane_pick_resize import _window_with_centred_box
    app, w, vp = _window_with_centred_box()
    w.show()
    app.processEvents()
    return app, w, vp


def _at(vp, pt):
    sx, sy, _ = vp.cam.to_screen(np.asarray(pt, float))
    return float(sx), float(sy)


def test_pick_all_lists_front_and_hidden_faces_part_and_planes():
    app, w, vp = _win()
    x, y = _at(vp, (5, 5, 10))
    items = vp.pick_all(x, y)
    assert items[0] == vp.pick(x, y) and items[0].kind == "face"
    faces = [p for p in items if p.kind == "face"]
    assert len(faces) >= 2                                   # the top and, behind it, the bottom
    assert any(p.kind == "plane" for p in items)             # the planes running through are still listed
    assert len(items) == len(set(items))


def test_tab_steps_through_and_a_click_selects_the_one_shown():
    from PySide6.QtTest import QTest
    app, w, vp = _win()
    x, y = _at(vp, (5, 5, 10))
    vp._cursor_xy = (int(x), int(y))                         # (offscreen, a hover move isn't delivered)
    first = vp.pick(*vp._cursor_xy)
    vp.event(QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
    second = vp.hover
    assert second is not None and second != first and "Tab 2 of" in vp.status_text
    vp.event(QKeyEvent(QEvent.KeyPress, Qt.Key_Backtab, Qt.ShiftModifier))
    assert vp.hover == first                                  # Shift+Tab goes back
    vp.event(QKeyEvent(QEvent.KeyPress, Qt.Key_Tab, Qt.NoModifier))
    at = QPoint(int(vp._cursor_xy[0]), int(vp._cursor_xy[1]))
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, at)
    app.processEvents()
    assert vp.selection == [second] and vp._cycle is None
    vp._hover_xy = (at.x() + 40, at.y() + 40)                # moving away ends the cycling
    vp._do_hover()
    assert vp._cycle is None and "Tab" not in vp.status_text


def test_select_other_menu_highlights_and_adds():
    app, w, vp = _win()
    x, y = _at(vp, (5, 5, 10))
    m = w._select_other_menu(x, y)
    acts = m.actions()
    labels = [a.text() for a in acts]
    assert len(acts) >= 3 and labels[0].startswith("Face — flat, facing +Z — ")
    assert any(t.startswith("Plane — ") for t in labels)
    assert not any(t.startswith("Part — ") for t in labels)   # whole parts follow the Select filter (off)
    vp.user_filter = {"part", "face", "edge", "vertex", "plane"}
    assert any(w._pick_label(p).startswith("Part — ") for p in vp.pick_all(x, y))
    vp.user_filter = None
    vp.clear_selection()
    m.hovered.emit(acts[1])
    assert vp.hover is not None
    m.triggered.emit(acts[1])
    assert len(vp.selection) == 1
    m.triggered.emit(acts[0])
    assert len(vp.selection) == 2                              # adds, as Onshape


def test_right_click_selection_tools():
    from PySide6.QtWidgets import QMenu
    app, w, vp = _win()
    pid = next(iter(w.doc.state.parts))
    shape = vp.part_shapes[pid]
    top = next(i for i, f in enumerate(K.faces(shape)) if K.face_info(f).get("n") and K.face_info(f)["n"][2] > .99)
    m = QMenu()
    w._add_selection_tools(m, Pick("face", (pid, top)))
    acts = {a.text(): a for a in m.actions()}
    vp.clear_selection()
    acts["Select edges of this face"].trigger()
    assert len(vp.selection) == 4 and all(p.kind == "edge" for p in vp.selection)
    vp.clear_selection()
    acts["Select adjacent faces"].trigger()
    assert len(vp.selection) == 5                              # the top and its four sides


def _key(w, app, key, mods=Qt.NoModifier):
    from PySide6.QtTest import QTest
    w.activateWindow()
    w.viewport.setFocus()
    QTest.keyClick(w.viewport, key, mods)
    app.processEvents()


def test_plane_and_visibility_keys():
    app, w, vp = _win()
    assert w.act_planes.isChecked()
    _key(w, app, Qt.Key_P)
    assert not w.act_planes.isChecked() and not vp.planes      # P: planes off …
    _key(w, app, Qt.Key_P)
    assert w.act_planes.isChecked() and vp.planes             # … and on again
    sketches = [f["id"] for f in w.doc.features if f["type"] == "sketch"]
    _key(w, app, Qt.Key_H, Qt.ShiftModifier)
    assert set(sketches) <= w.doc.hidden_features             # Shift+H: sketches hidden
    _key(w, app, Qt.Key_H, Qt.ShiftModifier)
    assert not set(sketches) & w.doc.hidden_features
    _key(w, app, Qt.Key_P, Qt.ShiftModifier)                  # Shift+P: all construction geometry
    assert not w.act_planes.isChecked() and set(sketches) <= w.doc.hidden_features
    _key(w, app, Qt.Key_P, Qt.ShiftModifier)
    assert w.act_planes.isChecked() and not set(sketches) & w.doc.hidden_features
    pid = next(iter(w.doc.state.parts))
    vp.set_selection([Pick("part", (pid,))])
    _key(w, app, Qt.Key_Y)
    assert pid in w.doc.hidden_parts                          # Y: hide …
    _key(w, app, Qt.Key_Y, Qt.ShiftModifier)
    assert not w.doc.hidden_parts                             # … Shift+Y: show again
    s0 = vp.cam.scale
    _key(w, app, Qt.Key_Z)
    assert vp.cam.scale > s0                                   # Z: zoom out
    R0 = vp.cam.R.copy()
    _key(w, app, Qt.Key_Right, Qt.ShiftModifier)
    vp._anim = None if vp._anim is None else vp._anim
    assert vp._anim is not None or not np.allclose(vp.cam.R, R0)   # Shift+arrow turns the view


def test_sketch_letters_go_to_sketch_tools():
    from freeshape.ui.sketcher import Sketcher
    app, w, vp = _win()
    vp.set_selection([Pick("plane", ("std", "Top"))])
    w.start_sketch()
    assert w.mode == "sketch"
    planes_on = w.act_planes.isChecked()
    for key, mods, tool in ((Qt.Key_P, Qt.NoModifier, "point"), (Qt.Key_M, Qt.NoModifier, "trim"),
                            (Qt.Key_X, Qt.NoModifier, "sk_extend"), (Qt.Key_B, Qt.NoModifier, "parallel"),
                            (Qt.Key_X, Qt.ShiftModifier, "hexagon"), (Qt.Key_F, Qt.ShiftModifier, "sk_fillet"),
                            (Qt.Key_S, Qt.ShiftModifier, "point"), (Qt.Key_L, Qt.ShiftModifier, "perpendicular"),
                            (Qt.Key_A, Qt.ShiftModifier, "arc_tan")):
        w.sketcher.set_tool("select")
        _key(w, app, key, mods)
        assert w.sketcher.tool == tool, (key, mods, w.sketcher.tool)
    assert w.act_planes.isChecked() == planes_on               # P in a sketch didn't hide the planes
    assert w.mode == "sketch"                                  # and Shift+F didn't start a 3D fillet
    assert Sketcher.key_tool(ord("Q"), Qt.ShiftModifier) == "symmetric"
    assert Sketcher.key_tool(ord("L"), Qt.ControlModifier) is None
    w.cancel_sketch()


def test_shift_enter_keeps_the_feature_and_starts_another():
    from PySide6.QtWidgets import QApplication
    from test_direction_arrow import _above_box, _extrude_dialog
    app, w, vp = _win()
    d = _extrude_dialog(w, app, _above_box(w))
    n = len(w.doc.features)
    d.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ShiftModifier))
    QApplication.processEvents()
    assert len(w.doc.features) == n + 1 and w.doc.features[-1]["type"] == "extrude"
    assert w.mode == "dialog" and w.dialog is not None and w.dialog.ftype == "extrude" and w.dialog is not d
    w.cancel_dialog()
