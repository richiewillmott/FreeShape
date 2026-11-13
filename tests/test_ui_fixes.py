"""Regressions: context menus act on the whole selection, hiding a part while a feature dialog is open takes it out
of the view, small holes are drawn round, and the show / hide eyes stay visible in a narrow side panel."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _cross_doc():
    """A 40 x 40 square divided in four by a cross: one 10 mm tall block over all of it, two 20 mm tall blocks
    over two quarters each, and a plane half-way up the first block."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 40), s.add_point(0, 40)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    m = [s.add_point(20, 0), s.add_point(20, 40), s.add_point(0, 20), s.add_point(40, 20)]
    s.add_line(m[0], m[1])
    s.add_line(m[2], m[3])
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sid = f["id"]
    for idx, depth in (([0, 1, 2, 3], 10), ([0, 1], 20), ([2, 3], 20)):
        sr = doc.state.sketches[sid]
        e = doc.make_feature("extrude")
        e["params"].update(regions=[F.region_ref(sr, sid, sr.regions[i]) for i in idx], depth=depth, op="new")
        doc.add_feature(e)
    pl = doc.make_feature("plane")
    pl["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=5)
    doc.add_feature(pl)
    return doc, pl["id"]


def _window(doc):
    app = _app()
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.resize(1200, 800)
    w._add_tab(doc)
    app.processEvents()
    return app, w


def test_small_circle_is_drawn_round_on_a_big_part():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle
    outer = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 22, 12).Shape()
    hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, -1), gp_Dir(0, 0, 1)), 2.5, 14).Shape()
    shape = BRepAlgoAPI_Cut(outer, hole).Shape()
    m = K.tessellate(shape)
    small = [len(m.edge_polys[i]) - 1 for i, e in enumerate(K.edges(shape))
             if BRepAdaptor_Curve(e).GetType() == GeomAbs_Circle and BRepAdaptor_Curve(e).Circle().Radius() < 3]
    assert small and min(small) >= 40, small          # was 14: a visible polygon


def test_set_parts_hidden():
    doc, _ = _cross_doc()
    ids = list(doc.state.parts)
    doc.set_parts_hidden(ids[:2], True)
    assert doc.hidden_parts == set(ids[:2])
    doc.set_parts_hidden(ids, False)
    assert doc.hidden_parts == set()


def test_context_menu_hides_every_selected_part():
    from PySide6.QtWidgets import QMenu
    from freeshape.ui.viewport import Pick
    doc, _ = _cross_doc()
    app, w = _window(doc)
    ids = list(doc.state.parts)
    # selected in the graphics area, right-clicked on one of them: the menu acts on all
    w.viewport.set_selection([Pick("part", (i,)) for i in ids])
    assert w._menu_parts(ids[1]) == ids
    m = QMenu()
    w._add_hide_actions(m, w._menu_parts(ids[1]))
    m.actions()[0].trigger()
    assert doc.hidden_parts == set(ids)
    # right-clicking a part that is not selected acts on that part only
    doc.set_parts_hidden(ids, False)
    w.viewport.set_selection([Pick("part", (ids[0],))])
    assert w._menu_parts(ids[2]) == [ids[2]]
    # selected in the parts list
    w.viewport.clear_selection()
    for i in range(w.left.parts.count()):
        w.left.parts.item(i).setSelected(i < 2)
    assert w._menu_parts(ids[0], w.left.parts.selected_part_ids()) == ids[:2]


def test_hiding_a_part_during_a_feature_dialog_updates_the_view():
    from PySide6.QtTest import QTest
    from freeshape.ui.viewport import Pick
    doc, plane = _cross_doc()
    app, w = _window(doc)
    big = list(doc.state.parts)[0]
    w.start_feature("split")
    d = w.dialog
    d.activate(d.fields["parts"])
    d.handle_pick(Pick("part", (big,)))
    d.activate(d.fields["tools"])
    d.handle_pick(Pick("plane", ("plane", plane)))
    QTest.qWait(250)
    d.fields["tools"].items.clear()
    d._fields_changed()
    QTest.qWait(250)
    assert big in [pid for pid, _m, _c in w.viewport.parts]
    doc.toggle_part_hidden(big)
    QTest.qWait(250)
    shown = [pid for pid, _m, _c in w.viewport.parts]
    assert big not in shown and len(shown) == 2          # was: still shown, catching clicks
    w.cancel_dialog()


def test_eye_stays_inside_a_narrow_list():
    from freeshape.ui.panels import eye_rect
    from PySide6.QtCore import QRect
    row = QRect(0, 0, 500, 24)                            # a long name makes the row wider than the list
    assert eye_rect(row, 199).right() <= 199
    assert eye_rect(row).right() > 400                    # unchanged without a visible edge
