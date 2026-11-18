"""Shared body options (issue #27): Solid / Surface / Thin and draft, the same code in Extrude, Revolve and Sweep."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _doc(build):
    doc = Document()
    s = Sketch()
    ids = build(s)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    return doc, sk["id"], ids


def _rect(s):
    a, b, c, d = s.add_point(0, 0), s.add_point(20, 0), s.add_point(20, 10), s.add_point(0, 10)
    return [s.add_line(x, y) for x, y in ((a, b), (b, c), (c, d), (d, a))]


def _ell(s):
    a, b, c = s.add_point(0, 0), s.add_point(20, 0), s.add_point(20, 10)
    return [s.add_line(a, b), s.add_line(b, c)]


def _arc_curve(s):
    c, p1, p2 = s.add_point(0, 0), s.add_point(10, 0), s.add_point(0, 10)
    return [s.add_arc(c, p1, p2)]


def _add(doc, ftype, **kw):
    f = doc.make_feature(ftype)
    f["params"].update(kw)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return list(doc.state.parts.values())


def _region(doc, sid):
    return F.region_ref(doc.state, sid, doc.state.sketches[sid].regions[0])


def _chain(sid, eid):
    return {"kind": "sketch_chain", "sketch": sid, "entity": eid}


def test_old_extrude_unchanged():
    doc, sid, _ = _doc(_rect)
    f = doc.make_feature("extrude")
    for k in ("creation", "curves", "thickness", "thin_side", "draft", "draft_angle"):
        f["params"].pop(k)                                  # a file saved before these options existed
    f["params"].update(regions=[_region(doc, sid)], depth=5.0)
    doc.add_feature(f)
    assert abs(K.volume(next(iter(doc.state.parts.values())).shape) - 1000) < 1e-6


@pytest.mark.parametrize("side,area", [("mid", 30.0), ("one", 31.0), ("other", 29.0)])
def test_thin_open_curve_each_side(side, area):
    doc, sid, ids = _doc(_ell)
    parts = _add(doc, "extrude", creation="thin", curves=[_chain(sid, ids[0])], thickness=1.0, thin_side=side,
                 depth=5.0)
    assert len(parts) == 1 and abs(K.volume(parts[0].shape) - area * 5) < 1e-6


def test_thin_closed_outline_and_arc():
    doc, sid, _ = _doc(_rect)
    parts = _add(doc, "extrude", creation="thin", regions=[_region(doc, sid)], thickness=1.0, depth=5.0)
    assert abs(K.volume(parts[0].shape) - (21 * 11 - 19 * 9) * 5) < 1e-6
    doc, sid, ids = _doc(_arc_curve)
    parts = _add(doc, "extrude", creation="thin", curves=[_chain(sid, ids[0])], thickness=2.0, depth=4.0)
    want = (math.pi / 4) * (11 ** 2 - 9 ** 2) * 4               # quarter ring, radius 9..11
    assert abs(K.volume(parts[0].shape) - want) < 1e-3


def test_surface_from_curves_and_outline():
    doc, sid, ids = _doc(_ell)
    parts = _add(doc, "extrude", creation="surface", curves=[_chain(sid, ids[0])], depth=5.0, op="add")
    assert len(parts) == 1 and not K.solids(parts[0].shape)
    assert abs(sum(K.face_info(x)["area"] for x in K.faces(parts[0].shape)) - 150) < 1e-6
    doc, sid, _ = _doc(_rect)
    parts = _add(doc, "extrude", creation="surface", regions=[_region(doc, sid)], depth=5.0)
    assert len(K.faces(parts[0].shape)) == 4          # an open tube: no caps


def test_draft_narrows_and_widens():
    vols = {}
    for a in (5.0, -5.0):
        doc, sid, _ = _doc(_rect)
        parts = _add(doc, "extrude", regions=[_region(doc, sid)], depth=5.0, draft=True, draft_angle=a)
        assert K.is_valid(parts[0].shape)
        vols[a] = K.volume(parts[0].shape)
    assert vols[5.0] < 1000 < vols[-5.0]


def test_revolve_thin_and_surface():
    doc, sid, ids = _doc(lambda s: [s.add_line(s.add_point(10, 0), s.add_point(10, 20))])
    axis = {"kind": "std", "name": "Y"}
    parts = _add(doc, "revolve", creation="thin", curves=[_chain(sid, ids[0])], thickness=2.0, thin_side="mid",
                 axis_std="Y", axis=axis, end="full")
    want = math.pi * (11 ** 2 - 9 ** 2) * 20                    # a tube: radius 9..11, 20 long
    assert abs(K.volume(parts[0].shape) - want) < 1e-3
    doc, sid, ids = _doc(lambda s: [s.add_line(s.add_point(10, 0), s.add_point(10, 20))])
    parts = _add(doc, "revolve", creation="surface", curves=[_chain(sid, ids[0])], axis=axis, end="full")
    assert not K.solids(parts[0].shape)
    assert abs(sum(K.face_info(x)["area"] for x in K.faces(parts[0].shape)) - 2 * math.pi * 10 * 20) < 1e-3


def test_thin_two_thicknesses():
    doc, sid, ids = _doc(_ell)
    parts = _add(doc, "extrude", creation="thin", curves=[_chain(sid, ids[0])], thin_side="two",
                 thickness=1.0, thickness2=0.5, depth=5.0)
    # 1 mm on the outside of the L (31 mm²) plus 0.5 mm inside (20·0.5 + 9.5·0.5 = 14.75 mm²)
    assert abs(K.volume(parts[0].shape) - (31 + 14.75) * 5) < 1e-6


def _plane_below(doc, z):
    pf = doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=z)
    doc.add_feature(pf)
    return {"kind": "plane", "ref": {"kind": "plane", "fid": pf["id"]}}


@pytest.mark.parametrize("creation", ["solid", "surface"])
def test_second_end_up_to_a_plane(creation):
    doc, sid, ids = _doc(_rect)
    target = _plane_below(doc, -7.0)
    kw = dict(regions=[_region(doc, sid)]) if creation == "solid" else dict(curves=[_chain(sid, ids[0])])
    parts = _add(doc, "extrude", creation=creation, depth=5.0, second=True, end2="up_to_face", up_to2=target, **kw)
    lo, hi = K.bbox(parts[0].shape)
    assert abs(lo[2] + 7) < 1e-6 and abs(hi[2] - 5) < 1e-6
    if creation == "solid":
        assert abs(K.volume(parts[0].shape) - 200 * 12) < 1e-6
    else:
        assert len(parts) == 1 and abs(sum(K.face_info(x)["area"] for x in K.faces(parts[0].shape)) - 60 * 12) < 1e-6   # the whole outline: picking a line picks its chain


def test_surface_up_to_a_plane():
    doc, sid, ids = _doc(_ell)
    target = _plane_below(doc, 6.0)
    parts = _add(doc, "extrude", creation="surface", curves=[_chain(sid, ids[0])], end="up_to_face", up_to=target)
    assert abs(sum(K.face_info(x)["area"] for x in K.faces(parts[0].shape)) - 30 * 6) < 1e-6


def test_symmetric_and_second_end_draft():
    doc, sid, _ = _doc(_rect)
    parts = _add(doc, "extrude", regions=[_region(doc, sid)], end="symmetric", depth=10.0, draft=True, draft_angle=5.0)
    vol_sym = K.volume(parts[0].shape)
    assert vol_sym < 2000                       # narrows both ways (it used to widen one way)
    lo, hi = K.bbox(parts[0].shape)
    assert abs(lo[2] + 5) < 1e-6 and abs(hi[2] - 5) < 1e-6
    doc, sid, _ = _doc(_rect)
    parts = _add(doc, "extrude", regions=[_region(doc, sid)], depth=5.0, second=True, end2="blind", depth2=5.0,
                 draft=True, draft_angle=5.0, draft2=True, draft_angle2=5.0)
    assert abs(K.volume(parts[0].shape) - vol_sym) < 1e-6 and len(K.solids(parts[0].shape)) == 1
    doc, sid, _ = _doc(_rect)
    parts = _add(doc, "extrude", regions=[_region(doc, sid)], depth=5.0, second=True, end2="blind", depth2=5.0,
                 draft=True, draft_angle=5.0, draft2=False)
    assert vol_sym < K.volume(parts[0].shape) < 2000            # only the first end is drafted


def test_old_second_end_behind_up_to_is_ignored():
    """Files from before per-end options could hold a ticked (but hidden) second end under an up-to end."""
    doc, sid, _ = _doc(_rect)
    target = _plane_below(doc, 6.0)
    f = doc.make_feature("extrude")
    f["params"].pop("end2")
    f["params"].update(regions=[_region(doc, sid)], end="up_to_face", up_to=target, second=True, depth2=4.0)
    doc.add_feature(f)
    assert abs(K.volume(next(iter(doc.state.parts.values())).shape) - 200 * 6) < 1e-6


def test_merge_scope():
    doc, sid, _ = _doc(_rect)
    a = _add(doc, "extrude", regions=[_region(doc, sid)], depth=5.0, op="new")[0]
    b = _add(doc, "extrude", regions=[_region(doc, sid)], depth=5.0, flip=True, op="new")
    assert len(b) == 2
    s2 = Sketch()
    c, = [s2.add_point(10, 5)]
    s2.add_circle(c, 2.0)
    s2.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s2.to_dict()
    doc.add_feature(sk)
    hole = F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])
    _add(doc, "extrude", regions=[hole], end="symmetric", depth=40.0, op="remove", scope=[a.id])
    vols = sorted(K.volume(p.shape) for p in doc.state.parts.values())
    assert abs(vols[0] - (1000 - math.pi * 4 * 5)) < 1e-6 and abs(vols[1] - 1000) < 1e-6   # only `a` drilled


def test_revolve_surface_up_to_a_plane():
    doc, sid, ids = _doc(lambda s: [s.add_line(s.add_point(10, 0), s.add_point(10, 20))])
    parts = _add(doc, "revolve", creation="surface", curves=[_chain(sid, ids[0])], axis={"kind": "std", "name": "Y"},
                 end="up_to_face", up_to={"kind": "plane", "ref": {"kind": "std", "name": "Right"}})
    area = sum(K.face_info(x)["area"] for x in K.faces(parts[0].shape))
    assert abs(area - 2 * math.pi * 10 * 20 / 4) < 1e-3 or abs(area - 2 * math.pi * 10 * 20 * 3 / 4) < 1e-3
