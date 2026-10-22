"""Headless tests for the kernel, solver and parametric engine.  Run:  python -m pytest tests  (or python tests/test_core.py)"""
import base64
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.document import Document
from freeshape.sketch import Sketch, ORIGIN


def _plate_doc(width=60.0):
    doc = Document()
    s = Sketch()
    a, b, c, d = s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)
    L = [s.add_line(a, b), s.add_line(b, c), s.add_line(c, d), s.add_line(d, a)]
    for l, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, l)
    s.add_constraint("coincident", a, ORIGIN)
    dim = s.add_constraint("distance", L[0], value=width)
    s.add_constraint("distance", L[1], value=40)
    cc = s.add_point(30, 20)
    ci = s.add_circle(cc, 8)
    s.add_constraint("diameter", ci, value=16)
    assert s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    ring = max(doc.state.sketches[sk["id"]].regions, key=lambda f: K.face_info(f)["area"])
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], ring)], depth=15.0)
    doc.add_feature(ex)
    return doc, sk, dim


def test_solver_rectangle_fully_defined():
    doc, sk, _ = _plate_doc()
    s = Sketch.from_dict(sk["params"]["sketch"])
    s.solve()
    assert s.status["ok"] and s.status["dof"] == 2   # circle centre is still free


def test_extrude_volume():
    doc, _, _ = _plate_doc()
    part = next(iter(doc.state.parts.values()))
    assert abs(K.volume(part.shape) - (60 * 40 - math.pi * 64) * 15) < 1e-3


def test_references_survive_upstream_edit():
    doc, sk, dim = _plate_doc()
    part = next(iter(doc.state.parts.values()))
    seams = K.seam_edge_indices(part.shape)
    vert = [e for i, e in enumerate(K.edges(part.shape))
            if i not in seams and (ln := K.edge_line(e)) is not None and abs(ln[1][2]) > 0.9]
    assert len(vert) == 4
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, e, "edge", part.shape) for e in vert], radius=5)
    doc.add_feature(fi)
    assert not doc.errors
    s = Sketch.from_dict(sk["params"]["sketch"])
    for c in s.constraints:
        if c["id"] == dim["id"]:
            c["value"] = 120.0
    s.solve()
    f0 = dict(doc.features[0], params=dict(doc.features[0]["params"], sketch=s.to_dict()))
    doc.replace_feature(f0)
    assert not doc.errors
    lo, hi = K.bbox(doc.state.parts[part.id].shape)
    assert abs(hi[0] - lo[0] - 120) < 1e-4


def test_undo_redo_and_rollback():
    doc, _, _ = _plate_doc()
    doc.set_rollback(1)
    assert not doc.state.parts
    doc.set_rollback(None)
    assert doc.state.parts
    doc.undo()
    assert len(doc.features) == 1
    doc.redo()
    assert len(doc.features) == 2


def test_cut_finds_material_direction():
    doc, _, _ = _plate_doc()
    s = Sketch()
    c = s.add_point(10, 10)
    s.add_circle(c, 3)
    sk = doc.make_feature("sketch")
    sk["params"].update(plane={"kind": "std", "name": "Top"}, sketch=s.to_dict())
    doc.add_feature(sk)
    reg = doc.state.sketches[sk["id"]].regions[0]
    cut = doc.make_feature("extrude")
    cut["params"].update(regions=[F.region_ref(doc.state, sk["id"], reg)], depth=5.0, op="remove", flip=True)
    doc.add_feature(cut)
    assert not doc.errors


def test_save_load_export_import():
    doc, _, _ = _plate_doc()
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "plate.fshape")
    doc.save(path)
    d2 = Document.load(path)
    assert not d2.errors and len(d2.state.parts) == 1
    step = os.path.join(tmp, "plate.step")
    d2.export(step)
    d2.export(os.path.join(tmp, "plate.stl"))
    imp = d2.make_feature("import")
    with open(step, "rb") as fh:
        imp["params"] = {"data": base64.b64encode(fh.read()).decode(), "filename": "plate.step"}
    d2.add_feature(imp)
    assert not d2.errors and len(d2.state.parts) == 2


def test_revolve_and_patterns():
    doc = Document()
    s = Sketch()
    pts = [s.add_point(*p) for p in [(20, 0), (30, 0), (30, 10), (20, 10)]]
    for a, b in zip(pts, pts[1:] + pts[:1]):
        s.add_line(a, b)
    sk = doc.make_feature("sketch")
    sk["params"].update(plane={"kind": "std", "name": "Front"}, sketch=s.to_dict())
    doc.add_feature(sk)
    rv = doc.make_feature("revolve")
    rv["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                        axis={"kind": "std", "name": "Z"}, end="full")
    doc.add_feature(rv)
    v = K.volume(next(iter(doc.state.parts.values())).shape)
    assert abs(v - math.pi * (30 ** 2 - 20 ** 2) * 10) < 1e-2
    pid = next(iter(doc.state.parts))
    lp = doc.make_feature("linear_pattern")
    lp["params"].update(parts=[pid], count=3, spacing=100.0)
    doc.add_feature(lp)
    assert not doc.errors and len(doc.state.parts) == 3


def test_number_parser():
    from freeshape.ui.numparse import parse_number
    assert parse_number("25/2") == 12.5
    assert abs(parse_number("1/2 in") - 12.7) < 1e-9
    assert parse_number("1in + 5mm") == 30.4
    assert parse_number("nonsense") is None


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
