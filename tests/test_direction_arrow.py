"""Extrude / Revolve direction, Onshape style: the arrow turns towards an "Up to …" target by itself (both ends),
a click on the arrow in the view flips it, and the dialog has an arrow button instead of an Opposite direction
tick box."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtWidgets import QToolButton, QCheckBox  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _square(x0, y0, x1, y1):
    s = Sketch()
    p = [s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    return s


def _sketch(doc, s, plane=None):
    f = doc.make_feature("sketch")
    if plane:
        f["params"]["plane"] = plane
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    return f["id"]


def _above_box(w):
    """The test box is 40 × 30 × 10 from the origin up. A square sketched on a plane 30 above it, over the box."""
    doc = w.doc
    pf = doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=30.0)
    doc.add_feature(pf)
    return _sketch(doc, _square(10, 10, 20, 20), {"kind": "plane", "fid": pf["id"]})


def _extrude_dialog(w, app, fid):
    w.start_feature("extrude")
    app.processEvents()
    d = w.dialog
    fld = d.fields["profiles"]
    fld.items = [("region", F.region_ref(d.base_state, fid, d.base_state.sketches[fid].regions[0]))]
    fld.refresh(w.doc)
    fld.changed.emit()
    app.processEvents()
    return d


def _pick(d, app, key, item):
    fld = d.fields[key]
    fld.items = [item]
    fld.refresh(d.main.doc)
    fld.changed.emit()
    app.processEvents()


def _box(d):
    pid = next(iter(d.base_state.parts))
    return pid, d.base_state.parts[pid].shape


def _face_at_z(shape, z):
    return next(f for f in K.faces(shape) if abs(K.face_info(f)["c"][2] - z) < 1e-6)


def test_flip_is_an_arrow_button_next_to_the_end_type():
    from test_nav import _window
    app, w, vp = _window()
    d = _extrude_dialog(w, app, _above_box(w))
    b = d.widgets["flip"]
    assert isinstance(b, QToolButton) and b.isCheckable() and not isinstance(b, QCheckBox)
    assert b.parent() is d.widgets["end"].parent()                  # in the End type row
    assert not any(isinstance(x, QCheckBox) and "Opposite" in x.text() for x in d.findChildren(QCheckBox))
    key0 = b.icon().cacheKey()
    b.click()
    assert d.feature["params"]["flip"] is True and b.icon().cacheKey() != key0 and "flipped" in b.toolTip()
    d._set("end", "symmetric")
    d._sync_visibility()
    assert b.isHidden()                                              # nothing to flip when symmetric
    w.cancel_dialog()


def test_up_to_next_and_up_to_face_turn_towards_the_target():
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    d = _extrude_dialog(w, app, _above_box(w))
    p = d.feature["params"]
    assert not p.get("flip")                                         # Top's normal: up, away from the box
    d.widgets["end"].setCurrentIndex(d.widgets["end"].findData("up_to_next"))
    QApplication.processEvents()
    assert p["flip"] is True and d.widgets["flip"].isChecked()       # the box is below: turned down to it
    d.widgets["flip"].click()                                        # you can still turn it back yourself
    assert p["flip"] is False
    d.widgets["end"].setCurrentIndex(d.widgets["end"].findData("up_to_face"))
    QApplication.processEvents()
    pid, shape = _box(d)
    _pick(d, app, "upto_face", ("face", F.make_ref(pid, _face_at_z(shape, 10.0), "face", shape)))
    assert p["flip"] is True                                         # the box's top is below the sketch
    d.accept()
    app.processEvents()
    f = w.doc.features[-1]
    assert f["type"] == "extrude" and not w.doc.errors.get(f["id"])
    zs = [K.bbox(pt.shape)[0][2] for pt in w.doc.state.parts.values()]
    assert any(abs(z - 10.0) < 1e-6 for z in zs) or len(w.doc.state.parts) == 1   # reaches down to z = 10


def test_second_end_up_to_face_turns_the_first_end_away():
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    d = _extrude_dialog(w, app, _above_box(w))
    p = d.feature["params"]
    d._set("flip", True)                                             # first end down …
    d.widgets["flip"].setChecked(True)
    d._set("second", True)
    d.widgets["end2"].setCurrentIndex(d.widgets["end2"].findData("up_to_face"))
    QApplication.processEvents()
    pid, shape = _box(d)
    _pick(d, app, "upto2_face", ("face", F.make_ref(pid, _face_at_z(shape, 10.0), "face", shape)))
    assert p["flip"] is False                                        # … so the second end, opposite, can reach it
    w.cancel_dialog()


def test_up_to_side_rules():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    fid = _above_box(w)
    st = doc.state
    pid = next(iter(st.parts))
    shape = st.parts[pid].shape
    base = {"regions": [F.region_ref(st, fid, st.sketches[fid].regions[0])], "op": "add"}
    low = next(q for q in K.vertex_points(shape) if abs(q[2]) < 1e-6)
    vref = {"kind": "vertex", "ref": F.make_vertex_ref(pid, low, shape)}
    assert F.up_to_side(st, "extrude", dict(base, end="up_to_vertex", up_to=vref)) == -1
    assert F.up_to_side(st, "extrude", dict(base, end="up_to_next")) == -1
    assert F.up_to_side(st, "extrude", dict(base, end="up_to_part", up_to={"kind": "part", "id": pid})) == -1
    assert F.up_to_side(st, "extrude", dict(base, end="blind")) == 0
    top = {"kind": "plane", "ref": {"kind": "std", "name": "Top"}}
    assert F.up_to_side(st, "extrude", dict(base, end="up_to_face", up_to=top)) == -1
    # sketched on the box's own top face, adding: the box isn't "the next part" downwards
    on_top = _sketch(doc, _square(5, 5, 15, 15), {"kind": "face", "face": F.make_ref(
        pid, _face_at_z(shape, 10.0), "face", shape)})
    reg = [F.region_ref(doc.state, on_top, doc.state.sketches[on_top].regions[0])]
    assert F.up_to_side(doc.state, "extrude", {"regions": reg, "op": "add", "end": "up_to_next"}) == 0
    assert F.up_to_side(doc.state, "extrude", {"regions": reg, "op": "remove", "end": "up_to_next"}) == -1


def test_revolve_side_follows_the_shorter_turn():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    st0 = doc.state
    pid = next(iter(st0.parts))
    shape = st0.parts[pid].shape
    q = next(v for v in K.vertex_points(shape) if np.allclose(v, (0, 30, 0)))
    vref = {"kind": "vertex", "ref": F.make_vertex_ref(pid, q, shape)}
    front = K.std_plane("Front")
    sides = {}
    for x in (25.0, -25.0):
        u = float((np.array([x, 0, 5.0]) - front.origin) @ front.xdir)
        v = float((np.array([x, 0, 5.0]) - front.origin) @ front.ydir)
        fid = _sketch(doc, _square(u - 5, v - 5, u + 5, v + 5), {"kind": "std", "name": "Front"})
        st = doc.state
        p = {"regions": [F.region_ref(st, fid, st.sketches[fid].regions[0])], "axis": {"kind": "std", "name": "Z"},
             "end": "up_to_vertex", "up_to": vref}
        sides[x] = F.up_to_side(st, "revolve", p)
    assert sides[25.0] == 1 and sides[-25.0] == -1                   # (25,0) → (0,30) is a quarter turn about +Z


def test_clicking_the_arrow_in_the_view_flips_and_dragging_does_not():
    from PySide6.QtTest import QTest
    from test_nav import _window
    app, w, vp = _window()
    d = _extrude_dialog(w, app, _above_box(w))
    d._do_preview()
    app.processEvents()
    (m,) = vp.manipulators
    assert m["on_click"] is not None
    base, tip, sd = vp._manip_screen(m)
    at = QPoint(int(round(tip[0] + sd[0] * 10)), int(round(tip[1] + sd[1] * 10)))
    assert vp._manip_hit(at.x(), at.y()) == 0
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, at)
    app.processEvents()
    assert d.feature["params"]["flip"] is True and d.widgets["flip"].isChecked()
    d._do_preview()
    (m,) = vp.manipulators
    base, tip, sd = vp._manip_screen(m)
    at = QPoint(int(round(tip[0] + sd[0] * 10)), int(round(tip[1] + sd[1] * 10)))
    depth0 = d.feature["params"]["depth"]
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseMove(vp, at + QPoint(int(sd[0] * 40), int(sd[1] * 40)))
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, at + QPoint(int(sd[0] * 40), int(sd[1] * 40)))
    app.processEvents()
    assert d.feature["params"]["flip"] is True                       # a drag stretches, it doesn't flip
    assert d.feature["params"]["depth"] != depth0
    w.cancel_dialog()


def test_up_to_arrow_cannot_be_stretched_and_revolve_has_an_arrow():
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    d = _extrude_dialog(w, app, _above_box(w))
    d.widgets["end"].setCurrentIndex(d.widgets["end"].findData("up_to_next"))
    QApplication.processEvents()
    d._do_preview()
    (m,) = vp.manipulators
    assert m["drag"] is False and m["label"] == "Up to next"
    flip = d.feature["params"]["flip"]
    m["on_click"]()
    assert d.feature["params"]["flip"] is (not flip)
    w.cancel_dialog()
    fid = _sketch(w.doc, _square(50, 0, 60, 10))
    w.start_feature("revolve")
    app.processEvents()
    r = w.dialog
    fld = r.fields["profiles"]
    fld.items = [("region", F.region_ref(r.base_state, fid, r.base_state.sketches[fid].regions[0]))]
    fld.changed.emit()
    r._set("axis_std", "Y")
    r.widgets["end"].setCurrentIndex(r.widgets["end"].findData("one"))
    QApplication.processEvents()
    arrows = r.manipulators()
    assert len(arrows) == 1 and arrows[0]["drag"] is False
    assert isinstance(r.widgets["flip"], QToolButton)
    arrows[0]["on_click"]()
    assert r.feature["params"]["flip"] is True and r.widgets["flip"].isChecked()
    w.cancel_dialog()
