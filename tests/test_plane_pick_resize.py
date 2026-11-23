"""Planes are picked only where there's nothing else (as in Onshape), and a selected plane can be resized by
dragging its corner handles; the size is kept in the file and undoable."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint  # noqa: E402

from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.viewport import Pick, plane_rect  # noqa: E402


def _square(h, at=(0.0, 0.0)):
    s = Sketch()
    x, y = at
    p = [s.add_point(x - h, y - h), s.add_point(x + h, y - h), s.add_point(x + h, y + h), s.add_point(x - h, y + h)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    return s


def _window_with_centred_box():
    """A 20 × 20 × 10 box centred on the origin (so Front and Right run through it); its sketch left showing."""
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.No)
    os.environ["FREESHAPE_NO_PLUGINS"] = "1"
    from freeshape.ui.mainwindow import MainWindow
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = _square(10).to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=10, op="new")
    doc.add_feature(e)
    w = MainWindow()
    w.resize(1200, 800)
    w._add_tab(doc)
    app.processEvents()
    vp = w.viewport
    vp.set_standard_view("Isometric", animate=False)
    vp.fit(animate=False)
    app.processEvents()
    return app, w, vp


def _at(vp, pt):
    sx, sy, _ = vp.cam.to_screen(np.asarray(pt, float))
    return sx, sy


def test_faces_win_over_planes_running_through_the_part():
    app, w, vp = _window_with_centred_box()
    for pt in [(5, 5, 10), (0, 0, 10), (10, 3, 5), (-10, -3, 5), (3, -10, 5)]:
        pk = vp.pick(*_at(vp, pt))
        assert pk is not None and pk.kind == "face", (pt, pk)
    pk = vp.pick(*_at(vp, (60, 60, 0)))                     # out on the Top plane, nothing else there
    assert pk == Pick("plane", ("std", "Top"))


def test_sketch_lines_hidden_behind_a_face_are_not_picked():
    app, w, vp = _window_with_centred_box()
    # the box's own sketch sits on its bottom; from above, its edges are behind the box
    assert vp.pick(*_at(vp, (-5, -10, 5))).kind == "face"
    vp.set_standard_view("Bottom", animate=False)
    app.processEvents()
    pk = vp.pick(*_at(vp, (3, 3, 0)))                        # from below the sketch is in front: its region
    assert pk is not None and pk.kind == "region"


def test_a_sketch_on_a_face_is_still_picked():
    app, w, vp = _window_with_centred_box()
    doc = w.doc
    pid = next(iter(doc.state.parts))
    shape = doc.state.parts[pid].shape
    top = next(f for f in F.K.faces(shape) if abs(F.K.face_info(f)["c"][2] - 10) < 1e-6)
    g = doc.make_feature("sketch")
    g["params"]["plane"] = {"kind": "face", "face": F.make_ref(pid, top, "face", shape)}
    g["params"]["sketch"] = _square(3).to_dict()
    doc.add_feature(g)
    app.processEvents()
    pk = vp.pick(*_at(vp, (0, 0, 10)))
    assert pk is not None and pk.kind in ("region", "sketch_line", "sketch_point") and pk.key[0] == g["id"]


def _select_top(w, vp, app):
    vp.set_standard_view("Top", animate=False)
    vp.fit(animate=False)
    vp.set_selection([Pick("plane", ("std", "Top"))])
    app.processEvents()


def test_every_selected_plane_shows_its_four_handles_when_idle():
    app, w, vp = _window_with_centred_box()
    assert vp._plane_handles() == []
    _select_top(w, vp, app)
    assert len(vp._plane_handles()) == 4
    three = [Pick("plane", ("std", n)) for n in ("Top", "Front", "Right")]
    vp.set_selection(three)
    hs = vp._plane_handles()
    assert len(hs) == 12 and {i for i, *_ in hs} == {0, 1, 2}             # like Onshape: all three, at once
    pid = next(iter(w.doc.state.parts))
    vp.set_selection([Pick("face", (pid, 0)), Pick("plane", ("std", "Front"))])
    assert len(vp._plane_handles()) == 4                                   # a face selected too: still Front's
    vp.set_selection([Pick("plane", ("std", "Top"))])
    w.mode = "dialog"
    assert vp._plane_handles() == []
    w.mode = "idle"


def test_with_several_selected_each_plane_resizes_on_its_own():
    from PySide6.QtTest import QTest
    app, w, vp = _window_with_centred_box()
    doc = w.doc
    vp.set_standard_view("Top", animate=False)
    vp.fit(animate=False)
    vp.set_selection([Pick("plane", ("std", "Top")), Pick("plane", ("std", "Front"))])
    app.processEvents()
    h = vp.plane_half()
    top_i = next(i for i, (k, *_r) in enumerate(vp.planes) if k == ("std", "Top"))
    i, c, sx, sy = next(hd for hd in vp._plane_handles() if hd[0] == top_i and hd[1] == 2)    # Top's (u1, v1)
    at = QPoint(int(round(sx)), int(round(sy)))
    target = QPoint(*(int(round(q)) for q in _at(vp, (30, 20, 0))))
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseMove(vp, (at + target) / 2)
    QTest.mouseMove(vp, target)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, target)
    app.processEvents()
    u0, u1, v0, v1 = doc.plane_rect(("std", "Top"), h)
    assert abs(u1 - 30) < 2 and abs(v1 - 20) < 2
    assert doc.plane_rect(("std", "Front"), h) == (-h, h, -h, h)          # the other one is untouched
    assert len(vp.selection) == 2 and len(vp._plane_handles()) == 8      # both still selected, with handles


def test_dragging_a_corner_resizes_the_plane_keeps_it_and_undoes():
    from PySide6.QtTest import QTest
    app, w, vp = _window_with_centred_box()
    doc = w.doc
    _select_top(w, vp, app)
    h = vp.plane_half()
    hs = vp._plane_handles()
    _i, _c, sx, sy = next(hd for hd in hs if hd[2] > vp.width() / 2 and hd[3] < vp.height() / 2)   # u1, v1
    at = QPoint(int(round(sx)), int(round(sy)))
    target = QPoint(*(int(round(c)) for c in _at(vp, (30, 20, 0))))
    QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, at)
    QTest.mouseMove(vp, (at + target) / 2)
    QTest.mouseMove(vp, target)
    QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, target)
    app.processEvents()
    u0, u1, v0, v1 = doc.plane_rect(("std", "Top"), h)
    assert (u0, v0) == (-h, -h)                              # the opposite corner stays
    assert abs(u1 - 30) < 2 and abs(v1 - 20) < 2
    assert vp.selection == [Pick("plane", ("std", "Top"))]   # still selected
    assert vp.pick(*_at(vp, (60, 60, 0))) is None            # outside its new size: not the plane any more
    assert vp.pick(*_at(vp, (25, 15, 0))) == Pick("plane", ("std", "Top"))
    key_rect = next(r for k, _pl, _l, r in vp.planes if k == ("std", "Top"))
    assert plane_rect(key_rect) == (u0, u1, v0, v1)
    doc.undo()
    app.processEvents()
    assert doc.plane_rect(("std", "Top"), h) == (-h, h, -h, h)
    doc.redo()
    assert doc.plane_rect(("std", "Top"), h) == (u0, u1, v0, v1)


def test_plane_size_saves_and_resets(tmp_path):
    doc = Document()
    doc.set_plane_rect(("std", "Front"), (-10, 40, -5, 25))
    pf = doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=5.0)
    doc.add_feature(pf)
    doc.set_plane_rect(("plane", pf["id"]), (0, 10, 0, 10))
    path = str(tmp_path / "p.fshape")
    doc.save(path)
    again = Document.load(path)
    assert again.plane_rect(("std", "Front"), 75) == (-10, 40, -5, 25)
    assert again.plane_rect(("plane", pf["id"]), 45) == (0, 10, 0, 10)
    assert again.plane_rect(("std", "Top"), 75) == (-75, 75, -75, 75)
    again.set_plane_rect(("std", "Front"), None)
    assert again.plane_rect(("std", "Front"), 75) == (-75, 75, -75, 75)
