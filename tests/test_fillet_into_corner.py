"""A fillet on an edge that runs into a corner an earlier fillet rounded, with a radius as big as that fillet's or
bigger: OpenCascade can't add it on its own (Onshape's kernel lets it overflow into the round), so it's redone
together with the earlier fillet — which OpenCascade can — while the two stay separate features."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from test_qa_geometry import _rect, _sketch, _extrude  # noqa: E402


def _edge_at(shape, c):
    return next(e for e in K.edges(shape) if np.allclose(K.edge_info(e)["c"], c, atol=1e-6))


def _box_with_top_fillet(r_top=2.0):
    """100 x 60 x 15, the top front and top right edges rounded (Fillet 1) — they meet at the corner above the
    vertical edge at x = 100, y = 0."""
    doc = Document()
    _extrude(doc, _sketch(doc, _rect(0, 0, 100, 60, fix_origin=True)), depth=15.0)
    part = next(iter(doc.state.parts.values()))
    f1 = doc.make_feature("fillet")
    f1["params"].update(edges=[F.make_ref(part.id, _edge_at(part.shape, c), "edge", part.shape)
                               for c in ([50, 0, 15], [100, 30, 15])], radius=r_top)
    doc.add_feature(f1)
    assert not doc.errors
    return doc, part.id, f1


def _fillet_corner_edge(doc, pid, r):
    part = doc.state.parts[pid]
    vert = next(e for e in K.edges(part.shape) if (ln := K.edge_line(e)) is not None and abs(ln[1][2]) > 0.9
                and np.allclose(K.edge_info(e)["c"][:2], [100, 0]))
    f2 = doc.make_feature("fillet")
    f2["params"].update(edges=[F.make_ref(pid, vert, "edge", part.shape)], radius=r)
    doc.add_feature(f2)
    return f2


def _all_at_once(r_top, r_vert):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    box = BRepPrimAPI_MakeBox(100, 60, 15).Shape()
    return K.fillet_specs(box, [(_edge_at(box, [50, 0, 15]), r_top, None), (_edge_at(box, [100, 30, 15]), r_top, None),
                                (_edge_at(box, [100, 0, 7.5]), r_vert, None)])


@pytest.mark.parametrize("r", [2.0, 3.0, 10.0])
def test_a_fillet_into_a_rounded_corner_works_at_or_above_the_earlier_radius(r):
    doc, pid, _f1 = _box_with_top_fillet(2.0)
    f2 = _fillet_corner_edge(doc, pid, r)
    assert not doc.errors, doc.errors
    shape = doc.state.parts[pid].shape
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    assert K.volume(shape) == pytest.approx(K.volume(_all_at_once(2.0, r)), rel=1e-9)   # the same corner
    assert [f["type"] for f in doc.features[-2:]] == ["fillet", "fillet"]               # two features still


def test_smaller_radii_are_done_as_before():
    doc, pid, _f1 = _box_with_top_fillet(2.0)
    before = doc.state.parts[pid].shape
    _fillet_corner_edge(doc, pid, 1.0)
    assert not doc.errors
    assert doc.state.blends[pid]["base"].IsSame(before)         # added on its own, not redone with Fillet 1


def test_editing_the_first_fillet_keeps_the_second_working():
    doc, pid, f1 = _box_with_top_fillet(2.0)
    _fillet_corner_edge(doc, pid, 3.0)
    assert not doc.errors
    f = doc.feature(f1["id"])
    doc.replace_feature(dict(f, params=dict(f["params"], radius=4.0)))
    assert not doc.errors, doc.errors
    # (3 mm is now under Fillet 1's 4 mm, so Fillet 2 goes on by itself: a corner very nearly the one-go shape)
    assert K.volume(doc.state.parts[pid].shape) == pytest.approx(K.volume(_all_at_once(4.0, 3.0)), rel=1e-4)
    doc.undo()
    assert K.volume(doc.state.parts[pid].shape) == pytest.approx(K.volume(_all_at_once(2.0, 3.0)), rel=1e-9)


def test_with_another_change_in_between_the_old_message_stays():
    doc, pid, _f1 = _box_with_top_fillet(2.0)
    # something else changes the part between the two fillets: a small pocket far from the corner
    _extrude(doc, _sketch(doc, _rect(10, 10, 5, 5)), depth=3.0, op="remove", flip=True)
    assert not doc.errors
    f2 = _fillet_corner_edge(doc, pid, 3.0)
    assert "largest that works" in doc.errors.get(f2["id"], "")
