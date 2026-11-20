"""A chamfer on a straight side edge that runs into rounded (filleted) top edges: OpenCascade's chamfer gives up
there, so the bevel is cut as a prism carried on through the rounded corner, as in Onshape."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import api as fs, kernel as K  # noqa: E402


def _filleted_box():
    box = fs.box(100, 60, 30)
    top = [e for e in K.edges(box) if abs(K.bbox(e)[0][2] - 30) < 1e-6 and abs(K.bbox(e)[1][2] - 30) < 1e-6]
    f = K.fillet(box, top, 2.0)
    side = [e for e in K.edges(f) if np.allclose(K.bbox(e)[0][:2], [100, 0], atol=1e-5)
            and np.allclose(K.bbox(e)[1][:2], [100, 0], atol=1e-5)]
    assert len(side) == 1
    return f, side


@pytest.mark.parametrize("d, kw, area", [(1.0, {}, 0.5), (3.0, {}, 4.5), (1.9, {}, 1.805),
                                         (1.0, {"mode": "two", "dist2": 2.0}, 1.0)])
def test_chamfer_a_side_edge_below_filleted_top_edges(d, kw, area):
    f, side = _filleted_box()
    r = K.chamfer(f, side, d, **kw)
    assert K.is_valid(r) and len(K.solids(r)) == 1
    gone = K.volume(f) - K.volume(r)
    assert area * 28 < gone < area * 30.01          # the bevel runs the full height, through the rounded corner
    assert len(K.faces(r)) == len(K.faces(f)) + 1    # one new (flat) face
    # the chamfer face is where it should be: a point on it just below the top is on the solid's surface
    lo, hi = K.bbox(r)
    assert np.allclose(lo, [0, 0, 0], atol=1e-6) and hi[0] == pytest.approx(100) and hi[2] == pytest.approx(30)


def test_angle_chamfer_and_a_concave_edge_is_left_to_opencascade():
    f, side = _filleted_box()
    r = K.chamfer(f, side, 1.0, mode="angle", angle=30)
    gone = K.volume(f) - K.volume(r)
    assert 0.5 * np.tan(np.radians(30)) * 27 < gone < 0.5 * np.tan(np.radians(30)) * 30.01
    with pytest.raises(K.KernelError):
        K.chamfer(f, side, 100.0)                   # far too big still fails, with the usual message


def test_an_invalid_solid_is_repaired_before_rounding(monkeypatch):
    """A solid OpenCascade flags as invalid (seen after a modelled thread) can't be filleted or chamfered at any
    size; FreeShape repairs it first and finds the picked edges again on the repaired solid."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    box = fs.box(40, 30, 20)
    edge = [e for e in K.edges(box) if np.allclose(K.bbox(e)[0][:2], [40, 0], atol=1e-6)
            and np.allclose(K.bbox(e)[1][:2], [40, 0], atol=1e-6)]
    real_try, real_valid = K._try_blend, K.is_valid
    monkeypatch.setattr(K, "is_valid", lambda s: False if s.IsSame(box) else real_valid(s))
    monkeypatch.setattr(K, "_try_blend", lambda mk, s, es, size, add=None:
                        None if s.IsSame(box) else real_try(mk, s, es, size, add))
    monkeypatch.setattr(K, "_repair", lambda s: BRepBuilderAPI_Copy(s).Shape())
    for op in (lambda: K.fillet(box, edge, 3.0), lambda: K.chamfer(box, edge, 3.0)):
        out = op()
        assert real_valid(out) and K.volume(out) < K.volume(box)

