"""Thicken, Enclose, Rib, Delete part, Helix and Bounding box (whole features from Onshape's menu)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _sketch(doc, build, plane=None):
    s = Sketch()
    ids = build(s)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    if plane:
        sk["params"]["plane"] = {"kind": "std", "name": plane}
    doc.add_feature(sk)
    return sk["id"], ids


def _rect(x0, y0, x1, y1):
    def b(s):
        a, b_, c, d = s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)
        return [s.add_line(p, q) for p, q in ((a, b_), (b_, c), (c, d), (d, a))]
    return b


def _add(doc, ftype, **kw):
    f = doc.make_feature(ftype)
    f["params"].update(kw)
    doc.add_feature(f)
    return f


def _extrude(doc, sid, **kw):
    sr = doc.state.sketches[sid]
    return _add(doc, "extrude", regions=[F.region_ref(doc.state, sid, sr.regions[0])], **kw)


def _surface_rect(doc, w=20, h=10):
    sid, _ = _sketch(doc, _rect(0, 0, w, h))
    sr = doc.state.sketches[sid]
    from freeshape import body_options as BO
    _add(doc, "extrude", creation="surface", curves=[BO.outline_ref("region", F.region_ref(doc.state, sid, sr.regions[0]))],
         depth=5.0)
    return [i for i, p in doc.state.parts.items() if not K.solids(p.shape)]


@pytest.mark.parametrize("side,t2,want", [("one", 0, 2), ("other", 0, 2), ("mid", 0, 2), ("two", 1.0, 3)])
def test_thicken_a_flat_face(side, t2, want):
    doc = Document()
    sid, _ = _sketch(doc, _rect(0, 0, 20, 10))
    _extrude(doc, sid, depth=5.0, op="new")
    pid = next(iter(doc.state.parts))
    sh = doc.state.parts[pid].shape
    top = max(K.faces(sh), key=lambda f: K.interior_point(f)[2])
    f = _add(doc, "thicken", items=[["face", F.make_ref(pid, top, "face", sh)]], thickness=2.0, thickness2=t2,
             side=side, op="new")
    assert not doc.errors, doc.errors
    new = [p for i, p in doc.state.parts.items() if i.startswith(f["id"])][0]
    assert K.volume(new.shape) == pytest.approx(200 * want)
    lo, hi = K.bbox(new.shape)
    expect = {"one": (5, 7), "other": (3, 5), "mid": (4, 6), "two": (4, 7)}[side]
    assert (lo[2], hi[2]) == pytest.approx(expect, abs=1e-6)


def test_thicken_a_surface_tube_and_add():
    doc = Document()
    tube = _surface_rect(doc)
    _add(doc, "thicken", items=[["part", tube[0]]], thickness=1.0, side="other", op="new")
    assert not doc.errors, doc.errors
    sol = [p for p in doc.state.parts.values() if K.solids(p.shape)]
    assert len(sol) == 1 and K.volume(sol[0].shape) > 0


@pytest.mark.parametrize("keep", [False, True])
def test_enclose_tube_between_two_planes(keep):
    doc = Document()
    tube = _surface_rect(doc)            # an open 20×10 tube, z 0..5
    pl = _add(doc, "plane", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=3.0)
    assert not doc.errors, doc.errors
    _add(doc, "enclose", items=[["part", tube[0]], ["plane", {"kind": "std", "name": "Top"}],
                                ["plane", {"kind": "plane", "fid": pl["id"]}]], keep_tools=keep)
    assert not doc.errors, doc.errors
    sols = [p for p in doc.state.parts.values() if K.solids(p.shape)]
    assert len(sols) == 1 and K.volume(sols[0].shape) == pytest.approx(600)
    assert (tube[0] in doc.state.parts) == keep               # the surface is used up unless kept


def test_rib_gusset_in_an_l_bracket():
    doc = Document()
    # L bracket in the Front plane (x, z): a floor 40×5 and a wall 5×40, extruded 20 deep
    def ell(s):
        pts = [s.add_point(*q) for q in ((0, 0), (40, 0), (40, 5), (5, 5), (5, 40), (0, 40))]
        return [s.add_line(pts[i], pts[(i + 1) % 6]) for i in range(6)]
    sid, _ = _sketch(doc, ell, "Front")
    _extrude(doc, sid, depth=20.0, end="symmetric", op="new")
    vol0 = K.volume(next(iter(doc.state.parts.values())).shape)
    # a diagonal line in the Front plane across the inside corner
    sid2, ids = _sketch(doc, lambda s: [s.add_line(s.add_point(5, 25), s.add_point(25, 5))], "Front")
    _add(doc, "rib", curves=[["path", {"kind": "sketch_chain", "sketch": sid2, "entity": ids[0]}]], thickness=2.0)
    assert not doc.errors, doc.errors
    assert len(doc.state.parts) == 1
    added = K.volume(next(iter(doc.state.parts.values())).shape) - vol0
    assert added == pytest.approx(0.5 * 20 * 20 * 2, rel=1e-6)        # a 20×20 triangle, 2 thick


def test_delete_part():
    doc = Document()
    sid, _ = _sketch(doc, _rect(0, 0, 10, 10))
    _extrude(doc, sid, depth=5.0, op="new")
    pid = next(iter(doc.state.parts))
    _add(doc, "delete_part", parts=[["part", pid]])
    assert not doc.errors and not doc.state.parts


def test_helix_numbers_and_wire():
    doc = Document()
    f = _add(doc, "helix", radius=5.0, mode="turns_pitch", turns=3.0, pitch=2.0)
    assert not doc.errors, doc.errors
    part = next(iter(doc.state.parts.values()))
    assert not K.faces(part.shape)
    length = sum(K.edge_info(e)["len"] for e in K.edges(part.shape))
    assert length == pytest.approx(3 * math.hypot(2 * math.pi * 5, 2), rel=1e-6)
    lo, hi = K.bbox(part.shape)
    assert hi[2] - lo[2] == pytest.approx(6, abs=1e-3)
    f["params"].update(mode="height_turns", height=10.0, turns=4.0)
    doc.replace_feature(f)
    lo, hi = K.bbox(next(iter(doc.state.parts.values())).shape)
    assert hi[2] - lo[2] == pytest.approx(10, abs=1e-3)


def test_helix_on_a_cylinder_is_a_sweep_path():
    doc = Document()
    sid, _ = _sketch(doc, lambda s: [s.add_circle(s.add_point(0, 0), 5.0)])
    _extrude(doc, sid, depth=20.0, op="new")
    pid = next(iter(doc.state.parts))
    sh = doc.state.parts[pid].shape
    cyl = max(K.faces(sh), key=lambda f: K.face_info(f)["area"])
    h = _add(doc, "helix", base=[["face", F.make_ref(pid, cyl, "face", sh)]], mode="height_pitch", height=20.0,
             pitch=4.0)
    assert not doc.errors, doc.errors
    hid = next(i for i in doc.state.parts if i.startswith(h["id"]))
    import numpy as np
    pts = np.concatenate([K.discretize_edge(e) for e in K.edges(doc.state.parts[hid].shape)])
    assert (pts[:, 2].min(), pts[:, 2].max()) == pytest.approx((0, 20), abs=1e-3)
    assert np.hypot(pts[:, 0], pts[:, 1]) == pytest.approx(np.full(len(pts), 5.0), abs=1e-4)
    # an Add after it still merges into the cylinder, not the curve
    sid2, _ = _sketch(doc, _rect(-2, -2, 2, 2))
    _extrude(doc, sid2, depth=25.0, op="add")
    assert not doc.errors and len(doc.state.parts) == 2


def test_bounding_box_with_clearance():
    doc = Document()
    sid, _ = _sketch(doc, _rect(0, 0, 20, 10))
    _extrude(doc, sid, depth=5.0, op="new")
    f = _add(doc, "bounding_box", clearance=1.0)
    assert not doc.errors
    box = next(p for i, p in doc.state.parts.items() if i.startswith(f["id"]))
    assert K.volume(box.shape) == pytest.approx(22 * 12 * 7)


def test_thicken_neighbouring_faces_joined():
    doc = Document()
    sid, _ = _sketch(doc, _rect(0, 0, 20, 10))
    _extrude(doc, sid, depth=5.0, op="new")
    pid = next(iter(doc.state.parts))
    sh = doc.state.parts[pid].shape
    top = max(K.faces(sh), key=lambda f: K.interior_point(f)[2])
    side = max(K.faces(sh), key=lambda f: K.interior_point(f)[0])          # the x = 20 side, touching the top
    f = _add(doc, "thicken", items=[["face", F.make_ref(pid, top, "face", sh)], ["face", F.make_ref(pid, side, "face", sh)]],
             thickness=1.0, side="one", op="new")
    assert not doc.errors, doc.errors
    new = [p for i, p in doc.state.parts.items() if i.startswith(f["id"])]
    assert len(new) == 1 and K.is_valid(new[0].shape)
    assert K.volume(new[0].shape) == pytest.approx(200 + 50 + 10)   # both walls and the corner between them
