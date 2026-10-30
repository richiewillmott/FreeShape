"""Plane types: offset, plane + point, line + angle, point normal, three points, mid plane, curve point, tangent,
and working out the type from what was selected."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _doc_with_block(circle=False):
    """A 40 x 20 x 10 block (corner at the origin) as one part; optionally a cylinder (r=6, h=10) on top."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 20), s.add_point(0, 20)]
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
    if circle:
        s2 = Sketch()
        s2.add_circle(s2.add_point(20, 10), 6)
        s2.solve()
        f2 = doc.make_feature("sketch")
        f2["params"]["sketch"] = s2.to_dict()
        f2["params"]["plane"] = {"kind": "std", "name": "Top"}
        doc.add_feature(f2)
        sr2 = doc.state.sketches[f2["id"]]
        e2 = doc.make_feature("extrude")
        e2["params"].update(regions=[F.region_ref(sr2, f2["id"], sr2.regions[0])], depth=10, op="new")
        doc.add_feature(e2)
    return doc


def _vref(doc, pos):
    part = next(iter(doc.state.parts.values()))
    return {"k": "vertex", "ref": F.make_vertex_ref(part.id, pos, part.shape)}


def _eref(doc, index_of):
    part = next(iter(doc.state.parts.values()))
    for e in K.edges(part.shape):
        info = K.edge_info(e)
        if index_of(info):
            return {"k": "edge", "ref": F.make_ref(part.id, e, "edge", part.shape)}
    raise AssertionError("edge not found")


def _fref(doc, index_of):
    part = next(iter(doc.state.parts.values()))
    for f in K.faces(part.shape):
        info = K.face_info(f)
        if index_of(info):
            return {"k": "face", "ref": F.make_ref(part.id, f, "face", part.shape)}
    raise AssertionError("face not found")


def _plane(doc, **params):
    f = doc.make_feature("plane")
    f["params"].update(params)
    st, err = doc.preview(f, doc.insert_index())
    assert err is None, err
    return st.planes[f["id"]]


def _is_plane(pl, origin, normal):
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    return abs(abs(float(pl.normal @ n)) - 1) < 1e-6 and abs(float((np.asarray(origin, float) - pl.origin) @ n)) < 1e-6


def test_old_offset_planes_still_work():
    doc = _doc_with_block()
    f = doc.make_feature("plane")
    f["params"] = {"ref": {"kind": "std", "name": "Top"}, "offset": 25.0, "flip": False}     # saved by 1.8.x
    st, err = doc.preview(f, doc.insert_index())
    assert err is None and _is_plane(st.planes[f["id"]], (0, 0, 25), (0, 0, 1))
    assert F.plane_type(f["params"]) == "offset"


def test_three_points_are_detected_and_used():
    doc = _doc_with_block()
    refs = [_vref(doc, v) for v in ((0, 0, 10), (40, 0, 10), (40, 20, 10))]
    assert F.plane_type({"type": "auto", "refs": refs}) == "three_point"
    assert _is_plane(_plane(doc, refs=refs), (0, 0, 10), (0, 0, 1))
    line = [_vref(doc, v) for v in ((0, 0, 0), (40, 0, 0), (0, 0, 10))]            # a vertical plane, y = 0
    assert _is_plane(_plane(doc, refs=line), (0, 0, 0), (0, 1, 0))
    f = doc.make_feature("plane")
    f["params"].update(refs=[_vref(doc, v) for v in ((0, 0, 0), (10, 0, 0), (30, 0, 0))])
    assert doc.preview(f, doc.insert_index())[1] == "The three points are in a straight line"


def test_plane_through_a_point_and_mid_plane():
    doc = _doc_with_block()
    top = _fref(doc, lambda i: i.get("n") and abs(i["n"][2] - 1) < 1e-6)
    bottom = _fref(doc, lambda i: i.get("n") and abs(i["n"][2] + 1) < 1e-6)
    left = _fref(doc, lambda i: i.get("n") and abs(i["n"][0] + 1) < 1e-6)
    front = _fref(doc, lambda i: i.get("n") and abs(i["n"][1] + 1) < 1e-6)
    v = _vref(doc, (40, 20, 4.0 + 6.0))                 # (40, 20, 10): top corner
    assert F.plane_type({"type": "auto", "refs": [top, v]}) == "plane_point"
    assert _is_plane(_plane(doc, refs=[bottom, v]), (0, 0, 10), (0, 0, 1))
    assert F.plane_type({"type": "auto", "refs": [top, bottom]}) == "mid_plane"
    assert _is_plane(_plane(doc, refs=[top, bottom]), (0, 0, 5), (0, 0, 1))
    bis = _plane(doc, refs=[left, front])                # two perpendicular faces: the plane between them
    assert abs(abs(float(bis.normal[2]))) < 1e-9 and abs(float(abs(bis.normal[0]) - abs(bis.normal[1]))) < 1e-9


def test_point_normal_line_angle_and_selection_detection():
    doc = _doc_with_block()
    edge_x = _eref(doc, lambda i: i["type"] == 0 and abs(abs(i["d"][0]) - 1) < 1e-6 and i["c"][1] < 1 and i["c"][2] < 1)
    v = _vref(doc, (40, 20, 10))
    assert F.plane_type({"type": "auto", "refs": [edge_x, v]}) == "point_normal"
    assert _is_plane(_plane(doc, refs=[edge_x, v]), (40, 20, 10), (1, 0, 0))
    assert F.plane_type({"type": "auto", "refs": [edge_x]}) == "line_angle"
    flat = _plane(doc, refs=[edge_x], angle=0.0)                 # 0 degrees: the plane holding the edge and world Z
    assert _is_plane(flat, (0, 0, 0), (0, 1, 0))
    tilt = _plane(doc, refs=[edge_x], angle=90.0)
    assert _is_plane(tilt, (0, 0, 0), (0, 0, 1))
    # an explicit type overrides auto-detection; unfit selections are reported, not guessed
    assert F.plane_type({"type": "three_point", "refs": []}) == "three_point"
    f = doc.make_feature("plane")
    f["params"].update(refs=[edge_x, edge_x, v, v])
    assert doc.preview(f, doc.insert_index())[1]


def test_curve_point_and_tangent():
    doc = _doc_with_block(circle=True)
    part = list(doc.state.parts.values())
    assert len(part) == 2
    cyl = part[1]
    face = next(f for f in K.faces(cyl.shape) if K.face_info(f)["type"] == int(K.GeomAbs_Cylinder))
    fref = {"k": "face", "ref": F.make_ref(cyl.id, face, "face", cyl.shape)}
    assert F.plane_type({"type": "auto", "refs": [fref]}) == "tangent"
    pt = {"k": "vertex", "ref": F.make_vertex_ref(cyl.id, (26, 10, 5), cyl.shape)}   # on the +x side of the cylinder
    tan = _plane(doc, refs=[fref, pt])
    assert _is_plane(tan, (26, 10, 5), (1, 0, 0))
    circ = next(e for e in K.edges(cyl.shape) if K.edge_info(e)["type"] == int(K.GeomAbs_Circle))
    eref = {"k": "edge", "ref": F.make_ref(cyl.id, circ, "edge", cyl.shape)}
    cp = {"k": "vertex", "ref": F.make_vertex_ref(cyl.id, (26, 10, 10), cyl.shape)}
    assert F.plane_type({"type": "auto", "refs": [eref, cp]}) == "curve_point"
    pl = _plane(doc, refs=[eref, cp])                      # normal to the circle at (26, 10, 10): along y
    assert _is_plane(pl, (26, 10, 10), (0, 1, 0))


def test_flip_reverses_the_normal():
    doc = _doc_with_block()
    refs = [_vref(doc, v) for v in ((0, 0, 10), (40, 0, 10), (40, 20, 10))]
    a, b = _plane(doc, refs=refs, flip=False), _plane(doc, refs=refs, flip=True)
    assert float(a.normal @ b.normal) < -0.999
