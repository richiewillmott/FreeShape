"""The Fillet dialog, like Onshape's: an arrow at the edge to drag the radius, and when the radius is too big to
make, where the round would run drawn in purple with its edges in red (not just a message)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402

PURPLE, RED = (0.78, 0.18, 0.86), (0.86, 0.15, 0.13)


def _fillet_dialog_on_top_edge():
    """The test box (20 x 20 x 10, centred), Fillet on one of its top edges."""
    from test_select_other_keys import _win
    app, w, vp = _win()
    pid = next(iter(w.doc.state.parts))
    shape = w.doc.state.parts[pid].shape
    ei = next(i for i, e in enumerate(K.edges(shape)) if (ln := K.edge_line(e)) is not None
              and abs(ln[1][2]) < 1e-6 and abs(K.edge_info(e)["c"][2] - K.bbox(shape)[1][2]) < 1e-6)
    w.start_feature("fillet")
    app.processEvents()
    d = w.dialog
    d.handle_pick(Pick("edge", (pid, ei)))
    d._do_preview()
    app.processEvents()
    return app, w, vp, d, shape, K.edges(shape)[ei]


def _set_radius(app, d, r):
    d.feature["params"]["radius"] = r
    d._do_preview()
    app.processEvents()


def test_the_arrow_sits_on_the_round_and_points_into_the_corner():
    """As Onshape's: its tip is on the middle of the round and it points into the corner the round cuts away, so
    dragging it in makes the radius bigger."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    app, w, vp, d, shape, edge = _fillet_dialog_on_top_edge()
    (arrow,) = vp.manipulators
    r = float(d.feature["params"]["radius"])
    mid = np.asarray(K.edge_info(edge)["c"], float)
    assert np.allclose(arrow["origin"], mid, atol=1e-6)
    inward = np.asarray(arrow["dir"], float)
    assert inward[2] < -0.5                                         # a top edge: down into the part
    tip = mid + inward * arrow["value"]
    rounded = K.fillet(shape, [edge], r)
    gap = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(K._pnt(tip)).Vertex(), rounded).Value()
    assert gap < 1e-3                                               # the tip is on the round
    k = arrow["value"] / r
    arrow["on_drag"](4.0 * k)                                       # dragged further in …
    assert abs(d.feature["params"]["radius"] - 4.0) < 1e-6 and "4" in arrow["label"]   # … a bigger radius


def test_a_fillet_that_fits_has_no_purple():
    app, w, vp, d, *_ = _fillet_dialog_on_top_edge()
    _set_radius(app, d, 2.0)
    assert not vp.outlines


def test_too_big_shows_the_round_in_purple_and_the_edge_in_red():
    app, w, vp, d, shape, edge = _fillet_dialog_on_top_edge()
    _set_radius(app, d, 50.0)                                       # far more than a 10 mm tall box allows
    assert d.err.isVisible()                                        # (the message is still there too)
    colours = [c for _p, c, _w in vp.outlines]
    assert PURPLE in colours and RED in colours
    purple = [np.asarray(p) for p, c, _w in vp.outlines if c == PURPLE]
    assert any(np.allclose(np.linalg.norm(p - p.mean(axis=0), axis=1).max(), 50.0, rtol=0.5) for p in purple)
    _set_radius(app, d, 2.0)                                        # back to one that fits: the purple goes
    assert not vp.outlines


def test_the_purple_goes_when_the_dialog_closes():
    app, w, vp, d, *_ = _fillet_dialog_on_top_edge()
    _set_radius(app, d, 50.0)
    assert vp.outlines
    w.cancel_dialog()
    app.processEvents()
    assert vp.outlines == []
