"""The N key in the 3D view: normal to the hovered / selected flat thing, or flip the view (issue #13)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _window():
    """A window on a 10 mm cube (one sketch, one extrude); camera moves land at once instead of animating."""
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.mainwindow import MainWindow
    app = QApplication.instance() or QApplication([])
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 10), s.add_point(0, 10)]
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
    w = MainWindow()
    w.resize(1200, 800)
    w._add_tab(doc)
    app.processEvents()
    vp = w.viewport
    vp.animate_to = lambda st, *a, **k: vp.cam.set_state(st)
    return w, vp, f["id"]


def _face(vp, normal):
    """A pick of the cube's face whose outward normal is `normal`."""
    from freeshape.ui.viewport import Pick
    pid, shape = next(iter(vp.part_shapes.items()))
    for i, face in enumerate(K.faces(shape)):
        pl = K.planar_face_plane(face)
        if pl is not None and np.allclose(pl.normal, normal, atol=1e-6):
            return Pick("face", (pid, i))
    raise AssertionError(f"no face with normal {normal}")


def _curved_face(vp):
    """Add a cylinder to the view and return a pick of its curved face."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from freeshape.ui.viewport import Pick
    cyl = BRepPrimAPI_MakeCylinder(5, 10).Shape()
    vp.part_shapes["cyl"] = cyl
    i = next(i for i, face in enumerate(K.faces(cyl)) if K.planar_face_plane(face) is None)
    return Pick("face", ("cyl", i))


def _back(vp):
    return vp.cam.R[2]          # from the target towards the eye


def test_n_goes_normal_to_the_hovered_flat_face():
    w, vp, _ = _window()
    vp.set_standard_view("Isometric", animate=False)
    vp.hover = _face(vp, (1, 0, 0))
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (1, 0, 0), atol=1e-9)


def test_n_goes_normal_to_the_selected_face_and_hover_wins():
    w, vp, _ = _window()
    vp.set_standard_view("Isometric", animate=False)
    vp.set_selection([_face(vp, (0, 0, 1))])
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (0, 0, 1), atol=1e-9)
    vp.set_standard_view("Isometric", animate=False)
    vp.hover = _face(vp, (1, 0, 0))                     # under the mouse beats the selection
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (1, 0, 0), atol=1e-9)


def test_n_flips_when_already_normal():
    w, vp, _ = _window()
    vp.set_standard_view("Isometric", animate=False)
    vp.hover = _face(vp, (1, 0, 0))
    w._normal_to_sketch()
    target, R, scale = vp.cam.state()
    w._normal_to_sketch()                               # second tap: the other side, same up, zoom and target
    assert np.allclose(_back(vp), (-1, 0, 0), atol=1e-9)
    assert np.allclose(vp.cam.R[1], R[1], atol=1e-9)
    assert np.isclose(vp.cam.scale, scale) and np.allclose(vp.cam.target, target)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (1, 0, 0), atol=1e-9)
    # a view only nearly normal (5° off) is squared up, not flipped
    a = np.radians(5)
    vp.set_view_dir(np.array([np.cos(a), np.sin(a), 0]), np.array([0, 0, 1.0]), animate=False)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (1, 0, 0), atol=1e-9)


def test_n_flips_the_view_with_nothing_hovered_or_selected():
    from freeshape.ui.viewport import Pick
    w, vp, _ = _window()
    vp.set_standard_view("Right", animate=False)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (-1, 0, 0), atol=1e-9)           # Right becomes Left
    assert np.allclose(vp.cam.R[1], (0, 0, 1), atol=1e-9)
    vp.set_standard_view("Isometric", animate=False)                # any view, not only the named ones
    back = _back(vp).copy()
    vp.set_selection([Pick("edge", (next(iter(vp.part_shapes)), 0))])   # an edge has no normal: still a flip
    w._normal_to_sketch()
    assert np.allclose(_back(vp), -back, atol=1e-9)


def test_n_does_nothing_over_a_curved_face():
    w, vp, _ = _window()
    vp.set_standard_view("Isometric", animate=False)
    R = vp.cam.R.copy()
    vp.hover = _curved_face(vp)
    w._normal_to_sketch()
    assert np.allclose(vp.cam.R, R)
    vp.set_selection([_face(vp, (0, 0, 1))])            # ...unless something flat is selected
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (0, 0, 1), atol=1e-9)


def test_n_takes_the_shorter_way_round():
    w, vp, _ = _window()
    vp.set_view_dir(np.array([-1.0, -1, 1]), np.array([0, 0, 1.0]), animate=False)     # looking at the -x side
    vp.hover = _face(vp, (1, 0, 0))                     # the +x face (hit through the part, say)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (-1, 0, 0), atol=1e-9)
    vp.set_view_dir(np.array([1.0, -1, 1]), np.array([0, 0, 1.0]), animate=False)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (1, 0, 0), atol=1e-9)


def test_n_goes_normal_to_a_plane_and_a_sketch():
    from freeshape.ui.viewport import Pick
    w, vp, sid = _window()
    vp.set_standard_view("Isometric", animate=False)
    key, pl = next((key, pl) for key, pl, *_ in vp.planes if abs(pl.normal[1]) > 0.5)
    vp.hover = Pick("plane", key)
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (0, -1, 0), atol=1e-9)           # the near side of the front plane
    vp.set_standard_view("Isometric", animate=False)
    vp.hover = Pick("region", (sid, 0))
    w._normal_to_sketch()
    assert np.allclose(_back(vp), (0, 0, 1), atol=1e-9)


def test_n_in_a_sketch_still_goes_normal_to_the_sketch():
    from freeshape.ui.viewport import Pick
    w, vp, sid = _window()
    w.start_sketch(edit_fid=sid)
    assert w.sketcher is not None
    side = next(key for key, pl, *_ in vp.planes if abs(pl.normal[0]) > 0.5)
    for _ in range(2):                                  # never a flip, and whatever is under the mouse
        vp.set_standard_view("Right", animate=False)
        vp.hover = Pick("plane", side)
        w._normal_to_sketch()
        assert np.allclose(_back(vp), w.sketcher.plane.normal, atol=1e-9)
        w._normal_to_sketch()
        assert np.allclose(_back(vp), w.sketcher.plane.normal, atol=1e-9)
