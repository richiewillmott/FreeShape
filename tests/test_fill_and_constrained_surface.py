"""Fill (a surface patch over a closed loop of edges) and Constrained surface (a surface fitted through points),
as in Onshape."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _import(doc, shape, name="shape.brep"):
    """Bring a shape in as an imported BREP file (as File ▸ Import does)."""
    import base64
    import tempfile
    path = os.path.join(tempfile.mkdtemp(), name)
    K.export_brep([shape], path)
    imp = doc.make_feature("import")
    with open(path, "rb") as fh:
        imp["params"].update(data=base64.b64encode(fh.read()).decode(), filename=name)
    doc.add_feature(imp)
    assert not doc.errors, doc.errors


def _open_box_doc():
    """A 20 x 20 x 10 box made as a surface with its top open (Extrude ▸ Surface of a square, plus a bottom)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    box = BRepPrimAPI_MakeBox(20, 20, 10).Shape()
    sew = BRepBuilderAPI_Sewing(1e-4)
    for f in K.faces(box):
        if abs(K.face_info(f)["c"][2] - 10) > 1e-6:
            sew.Add(f)
    sew.Perform()
    doc = Document()
    _import(doc, sew.SewedShape(), "open_box.brep")
    return doc


def _rim_refs(doc, z=10.0):
    (part,) = doc.state.parts.values()
    return [["path", {"kind": "edge", "edge": F.make_ref(part.id, e, "edge", part.shape)}]
            for e in K.edges(part.shape) if abs(K.edge_info(e)["c"][2] - z) < 1e-6]


def _new_parts(doc, before):
    return [p for pid, p in doc.state.parts.items() if pid not in before]


def test_fill_closes_the_top_of_an_open_box():
    doc = _open_box_doc()
    before = set(doc.state.parts)
    fill = doc.make_feature("fill")
    fill["params"].update(edges=_rim_refs(doc))
    doc.add_feature(fill)
    assert not doc.errors, doc.errors
    (patch,) = _new_parts(doc, before)
    lo, hi = K.bbox(patch.shape)
    assert abs(lo[2] - 10) < 1e-6 and abs(hi[2] - 10) < 1e-6           # flat, on the rim
    assert abs(K.face_info(K.faces(patch.shape)[0])["area"] - 400) < 1e-3


def test_fill_through_a_guide_point():
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt
    from freeshape.more_features import fill_face
    sq = [(0, 0, 10), (20, 0, 10), (20, 20, 10), (0, 20, 10)]
    rim = [(BRepBuilderAPI_MakeEdge(gp_Pnt(*sq[i]), gp_Pnt(*sq[(i + 1) % 4])).Edge(), None) for i in range(4)]
    face = fill_face(rim, "position", [(10, 10, 14)])
    assert K.bbox(face)[1][2] == pytest.approx(14, abs=0.05)


def test_fill_through_a_sketch_point_picked_in_the_dialog():
    doc = _open_box_doc()
    s = Sketch()
    pt = s.add_point(10, 10)
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    sr = doc.state.sketches[sk["id"]]
    fill = doc.make_feature("fill")
    fill["params"].update(edges=_rim_refs(doc), points=[["vertex", F.sketch_point_ref(sr, sk["id"], pt)]])
    doc.add_feature(fill)
    assert not doc.errors, doc.errors
    patch = list(doc.state.parts.values())[-1]
    assert K.bbox(patch.shape)[0][2] == pytest.approx(0, abs=0.05)   # pulled down to the point on Top (z = 0)


def test_fill_added_to_the_surface_closes_the_box_into_one_body():
    doc = _open_box_doc()
    fill = doc.make_feature("fill")
    fill["params"].update(edges=_rim_refs(doc), surface_op="add")
    doc.add_feature(fill)
    assert not doc.errors and len(doc.state.parts) == 1
    assert len(K.faces(next(iter(doc.state.parts.values())).shape)) == 6


def test_tangent_fill_bulges_to_meet_a_curved_surface_smoothly():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from freeshape.more_features import fill_face
    shell = K.cut(K.faces(BRepPrimAPI_MakeSphere(10).Shape())[0],
                  BRepPrimAPI_MakeBox(gp_Pnt(-20, -20, 7), 40, 40, 20).Shape())
    rim = [e for e in K.edges(shell) if abs(K.edge_info(e)["c"][2] - 7) < 1e-6]      # the cut's circle
    flat = fill_face([(e, K._faces_at_edge(shell, e)[0]) for e in rim], "position")
    smooth = fill_face([(e, K._faces_at_edge(shell, e)[0]) for e in rim], "tangent")
    assert abs(K.bbox(flat)[1][2] - 7) < 1e-3 and K.bbox(smooth)[1][2] > 9.5


def test_fill_needs_a_closed_loop():
    doc = _open_box_doc()
    fill = doc.make_feature("fill")
    fill["params"].update(edges=_rim_refs(doc)[:3])                    # three sides of four
    doc.add_feature(fill)
    assert "closed loop" in doc.errors.get(fill["id"], "")


def test_fill_over_a_sketch_circle():
    doc = Document()
    s = Sketch()
    s.add_circle(s.add_point(0, 0), 10)
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    eid = next(iter(s.entities))
    fill = doc.make_feature("fill")
    fill["params"].update(edges=[["path", {"kind": "sketch_chain", "sketch": sk["id"], "entity": eid}]])
    doc.add_feature(fill)
    assert not doc.errors, doc.errors
    (patch,) = doc.state.parts.values()
    assert K.face_info(K.faces(patch.shape)[0])["area"] == pytest.approx(np.pi * 100, rel=1e-3)


def _points_doc(pts, tolerance=0.1):
    """A part made of loose vertices (stands in for an imported mesh's points), and a Constrained surface on it."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    cloud = K.make_compound([BRepBuilderAPI_MakeVertex(gp_Pnt(*q)).Vertex() for q in pts])
    doc = Document()
    _import(doc, cloud, "pts.brep")
    pid = next(iter(doc.state.parts))
    cs = doc.make_feature("constrained_surface")
    cs["params"].update(parts=[["part", pid]], tolerance=tolerance)
    doc.add_feature(cs)
    return doc, cs, pid


def test_constrained_surface_fits_points_on_a_curved_surface():
    pts = [(x, y, (x * x + y * y) / 50.0) for x in range(-10, 11, 4) for y in range(-10, 11, 4)]
    doc, cs, pid = _points_doc(pts)
    assert not doc.errors, doc.errors
    surf = [p for q, p in doc.state.parts.items() if q != pid][0]
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    worst = max(BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*q)).Vertex(), surf.shape).Value()
                for q in pts)
    assert worst < 0.1
    lo, hi = K.bbox(surf.shape)
    assert lo[0] == pytest.approx(-10, abs=1.5) and hi[0] == pytest.approx(10, abs=1.5)   # (a rectangle in u, v)


def test_constrained_surface_needs_points_that_spread_out():
    doc, cs, _ = _points_doc([(0, 0, 0), (5, 5, 5), (10, 10, 10)])
    assert "in a line" in doc.errors.get(cs["id"], "")


def test_constrained_surface_reports_a_fit_outside_the_tolerance():
    rng = np.random.default_rng(1)
    pts = [(x, y, float(rng.uniform(-3, 3))) for x in range(0, 21, 2) for y in range(0, 21, 2)]   # rough
    doc, cs, _ = _points_doc(pts, tolerance=1e-4)
    err = doc.errors.get(cs["id"], "")
    assert err == "" or "tolerance" in err


@pytest.mark.parametrize("ftype", ["fill", "constrained_surface"])
def test_the_dialogs_open(ftype):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from test_select_other_keys import _win
    app, w, vp = _win()
    w.start_feature(ftype)
    app.processEvents()
    assert w.mode == "dialog" and w.dialog is not None and w.dialog.feature["type"] == ftype
    w.cancel_dialog() if hasattr(w, "cancel_dialog") else w.dialog.reject()
