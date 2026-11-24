"""QA pass (2.5.0): the core modelling flow end to end, headless — sketch a rectangle, dimension it with a variable,
extrude, fillet one chosen corner, change the variable, check the fillet stayed on the same corner, export STEP and
import it back."""
import base64
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.gp import gp_Pnt
from OCP.TopAbs import TopAbs_IN

from freeshape import features as F, kernel as K, variables as V
from freeshape.document import Document
from freeshape.sketch import Sketch, ORIGIN

H, DEPTH, R = 40.0, 15.0, 6.0


def _inside(shape, x, y, z):
    c = BRepClass3d_SolidClassifier(shape, gp_Pnt(x, y, z), 1e-6)
    return c.State() == TopAbs_IN


def _model(width_expr="#w"):
    doc = Document()
    V.add(doc, "w", "60")
    s = Sketch()
    a, b, c, d = s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, H), s.add_point(0, H)
    L = [s.add_line(a, b), s.add_line(b, c), s.add_line(c, d), s.add_line(d, a)]
    for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    s.add_constraint("coincident", a, ORIGIN)
    dim = s.add_constraint("distance", L[0], value=60)
    dim["expr"] = width_expr
    s.add_constraint("distance", L[1], value=H)
    assert s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                        depth=DEPTH)
    doc.add_feature(ex)
    part = next(iter(doc.state.parts.values()))
    # the vertical edge at the far corner (x = w, y = H)
    corner = [e for e in K.edges(part.shape)
              if (ln := K.edge_line(e)) is not None and abs(ln[1][2]) > 0.9
              and abs(ln[0][0] - 60) < 1e-6 and abs(ln[0][1] - H) < 1e-6]
    assert len(corner) == 1
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, corner[0], "edge", part.shape)], radius=R)
    doc.add_feature(fi)
    assert not doc.errors
    return doc, part.id


def _check(doc, pid, w):
    shape = doc.state.parts[pid].shape
    want = (w * H - (4 - math.pi) * R * R / 4) * DEPTH
    assert abs(K.volume(shape) - want) < 1e-3, (K.volume(shape), want)
    z = DEPTH / 2
    assert not _inside(shape, w - 0.3, H - 0.3, z)          # the rounded corner: material gone
    for x, y in [(0.3, 0.3), (w - 0.3, 0.3), (0.3, H - 0.3)]:  # the other three: still sharp
        assert _inside(shape, x, y, z), (x, y)


def test_variable_driven_edit_keeps_the_fillet_on_its_corner():
    doc, pid = _model()
    _check(doc, pid, 60)
    var = V.list_variables(doc)[0]
    for w in (100, 35, 60):
        V.set_expr(doc, var["fid"], str(w))
        assert not doc.errors, doc.errors
        _check(doc, pid, w)


def test_undo_of_the_edit_brings_back_the_old_size():
    doc, pid = _model()
    V.set_expr(doc, V.list_variables(doc)[0]["fid"], "90")
    _check(doc, pid, 90)
    doc.undo()
    _check(doc, pid, 60)
    doc.redo()
    _check(doc, pid, 90)


def test_step_export_and_import_round_trip(tmp_path):
    doc, pid = _model()
    V.set_expr(doc, V.list_variables(doc)[0]["fid"], "80")
    step = tmp_path / "plate.step"
    doc.export(str(step))
    d2 = Document()
    imp = d2.make_feature("import")
    imp["params"] = {"data": base64.b64encode(step.read_bytes()).decode(), "filename": "plate.step"}
    d2.add_feature(imp)
    assert not d2.errors
    (got,) = d2.state.parts.values()
    assert abs(K.volume(got.shape) - K.volume(doc.state.parts[pid].shape)) < 1e-3
    lo, hi = K.bbox(got.shape)
    assert [round(v, 4) for v in hi - lo] == [80, H, DEPTH]


def test_save_and_reopen_keeps_the_variable_link(tmp_path):
    doc, pid = _model()
    path = tmp_path / "m.fshape"
    doc.save(str(path), version=False)
    d2 = Document.load(str(path))
    V.set_expr(d2, V.list_variables(d2)[0]["fid"], "70")
    assert not d2.errors
    _check(d2, pid, 70)
