"""2D drawings: hidden-line views, projection standards, dimensions that follow the model, DXF and files."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf  # noqa: E402


def plate(length=60.0, depth=40.0, height=10.0, hole_r=4.0):
    box = BRepPrimAPI_MakeBox(length, depth, height).Shape()
    return K.cut(box, K.cylinder((15, 20, -1), (0, 0, 1), hole_r, height + 2))


def drawing(shape_box, **sheet):
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape_box[0]
    g.sheet.update(sheet)
    g.refresh(notify=False)
    return g


def test_hidden_line_front_view():
    r = K.hlr_project(plate(), (0, -1, 0), (0, 0, 1))
    vis = [c for c in r["visible"] if c[0] == "line"]
    assert len(vis) == 4                                    # the outline of the plate
    xs = sorted(round(c[1][0], 6) for c in r["hidden"])
    assert xs == [11.0, 19.0]                               # the hole, dashed, and nothing doubled up


def test_top_view_has_the_hole_as_a_circle():
    r = K.hlr_project(plate(), (0, 0, 1), (0, 1, 0))
    circ = [c for c in r["visible"] if c[0] == "circle"]
    assert len(circ) == 1 and abs(circ[0][2] - 4) < 1e-9 and np.allclose(circ[0][1], (15, 20))


@pytest.mark.parametrize("proj", ["first", "third"])
def test_projected_views_follow_the_standard(proj):
    shape = [plate()]
    g = drawing(shape, projection=proj)
    front = g.add_view(*DIRECTIONS["Front"], (100, 150), checkpoint=False)
    right = g.add_projected(front, (220, 150))
    below = g.add_projected(front, (100, 60))
    # first angle: the view placed on the right is seen from the left, the one below is seen from above
    want_right = (-1, 0, 0) if proj == "first" else (1, 0, 0)
    want_below = (0, 0, 1) if proj == "first" else (0, 0, -1)
    assert np.allclose(right["d"], want_right) and np.allclose(below["d"], want_below)
    # lined up with the front view: same model point, same height / same x on the paper
    P = np.array([60.0, 0.0, 10.0])
    pf = g.to_paper(front, g.project(front, P))
    assert abs(g.to_paper(right, g.project(right, P))[1] - pf[1]) < 1e-9
    assert abs(g.to_paper(below, g.project(below, P))[0] - pf[0]) < 1e-9
    # moving the front view takes the others with it, and a projected view only slides along its line
    g.move_view(front, np.array([10.0, 5.0]))
    assert abs(g.to_paper(right, g.project(right, P))[1] - g.to_paper(front, g.project(front, P))[1]) < 1e-9
    before = list(right["o"])
    g.move_view(right, np.array([7.0, 30.0]))
    assert right["o"][1] == before[1] and right["o"][0] == before[0] + 7.0


def test_auto_layout_picks_a_standard_scale_that_fits():
    g = drawing([plate()], size="A4")
    g.auto_layout()
    assert len(g.views) == 4 and g.sheet["scale"] in (0.5, 1.0, 2.0)
    x0, y0, x1, y1 = g.drawing_area()
    for v in g.views:
        a, b, c, d = g.view_box(v)
        assert a >= x0 - 1e-6 and b >= y0 - 1e-6 and c <= x1 + 1e-6 and d <= y1 + 1e-6


def test_dimensions_measure_and_follow_the_model():
    shape = [plate()]
    g = drawing(shape)
    top = g.add_view(*DIRECTIONS["Top"], (150, 150), checkpoint=False)
    front = g.add_view(*DIRECTIONS["Front"], (150, 250), checkpoint=False)
    snaps = g.snap_points(front)
    verts = [(P, ex) for kind, P, _xy, ex in snaps if kind == "vertex"]
    lo = min(verts, key=lambda t: (t[0][0], t[0][2]))          # (0, *, 0)
    hi = max(verts, key=lambda t: (t[0][0], -t[0][2]))         # (60, *, 0)
    top_hi = max(verts, key=lambda t: (t[0][2], -t[0][0]))     # (0, *, 10)
    end = lambda t: {"kind": "vertex", "p": [float(c) for c in t[0]], "i": t[1]["i"], "n": t[1]["n"]}
    length = g.add_annot({"type": "lin", "view": front["id"], "a": end(lo), "b": end(hi), "orient": "h",
                          "pos": [150, 230]})
    height = g.add_annot({"type": "lin", "view": front["id"], "a": end(lo), "b": end(top_hi), "orient": "v",
                          "pos": [120, 250]})
    c, _ax, r, _uv, ci = g.circles_in_view(top)[0]
    dia = g.add_annot({"type": "dia", "view": top["id"], "pos": [180, 180],
                       "a": {"kind": "centre", "p": [float(x) for x in c], "r": r, "i": ci,
                             "n": len(g._snapdata()[1])}})
    val = lambda a: g.dim_geometry(a)[2]
    assert abs(val(length) - 60) < 1e-9 and abs(val(height) - 10) < 1e-9 and abs(val(dia) - 8) < 1e-9
    assert g.dim_text(dia, val(dia)) == "Ø8"
    # the part gets taller, longer and the hole bigger: the same dimensions now read the new sizes
    shape[0] = plate(length=75, height=25, hole_r=5)
    g.refresh(notify=False)
    assert abs(val(length) - 75) < 1e-9 and abs(val(height) - 25) < 1e-9 and abs(val(dia) - 10) < 1e-9
    assert not any(isinstance(a.get(k), dict) and a[k].get("lost") for a in g.annots for k in ("a", "b"))


def test_seam_points_are_not_snapped_to():
    verts, circles = K.snap_features(plate())
    assert int(np.isnan(verts[:, 0]).sum()) == 2
    # the hole's two rims, plus the hole itself (its cylinder face)
    assert len(circles) == 3 and sum(1 for c in circles if c[4]) == 1


def test_dxf_export(tmp_path):
    from freeshape import drawing as DW
    g = drawing([plate()])
    g.auto_layout()
    front = g.views[0]
    sp = [x for x in g.snap_points(front) if x[0] == "vertex"]
    E = lambda t: {"kind": "vertex", "p": [float(c) for c in t[1]], "i": t[3]["i"], "n": t[3]["n"]}
    lo = min(sp, key=lambda t: (t[1][0], t[1][2]))
    hi = max(sp, key=lambda t: (t[1][0], -t[1][2]))
    g.add_annot({"type": "lin", "view": front["id"], "a": E(lo), "b": E(hi), "orient": "h",
                 "pos": list(g.to_paper(front, (30, -15)))})
    # the plain R12 writer (used without ezdxf)
    p12 = str(tmp_path / "plate12.dxf")
    DW._export_dxf_r12(g, p12)
    txt = open(p12, encoding="ascii").read().replace("\r\n", "\n")
    assert "AC1009" in txt and "HIDDEN" in txt and "DASHED" in txt and "arial.ttf" in txt
    for ent in ("LINE", "CIRCLE", "TEXT"):
        assert f"\n{ent}\n" in txt
    try:
        import ezdxf
    except ImportError:
        return
    # with ezdxf: real AutoCAD dimensions that measure the model (DIMLFAC undoes the sheet scale)
    for full in (False, True):
        path = str(tmp_path / f"plate{int(full)}.dxf")
        export_dxf(g, path, full_size=full)
        doc = ezdxf.readfile(path)
        dims = [e for e in doc.modelspace() if e.dxftype() == "DIMENSION"]
        assert len(dims) == 1
        assert abs(float(dims[0].get_measurement()) * dims[0].override().get("dimlfac", 1.0) - 60) < 1e-6
        assert {"LINE", "CIRCLE", "TEXT"} <= {e.dxftype() for e in doc.modelspace()}
        assert doc.styles.get("Standard").dxf.font == "arial.ttf"


def test_save_and_reopen_without_the_part(tmp_path):
    shape = [plate()]
    g = drawing(shape)
    g.auto_layout()
    path = str(tmp_path / "plate.fdwg")
    g.save(path)
    g2 = DrawingDoc.load(path)                     # no Part Studio to link to: uses the copy in the file
    assert g2.shape is not None and len(g2.views) == 4
    assert abs(K.volume(g2.shape) - K.volume(shape[0])) < 1e-6


def test_undo_redo():
    g = drawing([plate()])
    g.add_view(*DIRECTIONS["Front"], (100, 100))
    g.add_view(*DIRECTIONS["Top"], (100, 40))
    assert len(g.views) == 2
    g.undo()
    assert len(g.views) == 1
    g.redo()
    assert len(g.views) == 2


def test_cylinder_outline_dimension_follows_the_model():
    """A cylinder seen from the side has no corners on its outline edges: those ends are kept as points in the
    view, and re-found on the new outline after the part changes."""
    shape = [K.cylinder((0, 0, 0), (0, 0, 1), 10, 30)]
    g = drawing(shape)
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    lines = [c for c in g.curves(front)["visible"] if c[0] == "line" and abs(c[1][0] - c[2][0]) < 1e-9]
    left, right = sorted(lines, key=lambda c: c[1][0])[0], sorted(lines, key=lambda c: c[1][0])[-1]
    uv = lambda q: {"kind": "uv", "uv": [float(q[0]), float(q[1])]}
    a = g.add_annot({"type": "pl", "view": front["id"], "a": uv(left[1]), "b": uv(right[1]), "b2": uv(right[2]),
                     "pos": [150, 180], "prefix": "Ø"})
    assert abs(g.dim_geometry(a)[2] - 20) < 1e-9 and g.dim_text(a, 20) == "Ø20"
    shape[0] = K.cylinder((0, 0, 0), (0, 0, 1), 10.3, 30)
    g.refresh(notify=False)
    assert abs(g.dim_geometry(a)[2] - 20.6) < 1e-9


def test_centre_marks_only_on_whole_circles():
    import math
    disc = K.cylinder((0, 0, 0), (0, 0, 1), 22, 8)
    for i in range(12):                                     # scallops: lots of partial arcs, no whole circles
        ang = 2 * math.pi * i / 12
        disc = K.cut(disc, K.cylinder((24 * math.cos(ang), 24 * math.sin(ang), -1), (0, 0, 1), 3, 10))
    disc = K.cut(disc, K.cylinder((0, 0, -1), (0, 0, 1), 3, 10))   # one bore
    g = drawing([disc])
    top = g.add_view(*DIRECTIONS["Top"], (150, 150), checkpoint=False)
    marks = [it for it in g.items() if it[1] == "CENTER" and it[2] == top["id"]]
    assert len(marks) == 2                                  # one cross, for the bore


def test_cross_hole_in_a_round_hub_can_be_dimensioned():
    """A hole drilled across a round hub has no flat circular edge, but seen along its axis it's a circle."""
    hub = K.cut(K.cylinder((0, 0, 0), (0, 0, 1), 11, 20), K.cylinder((0, -20, 10), (0, 1, 0), 2.5, 40))
    g = drawing([hub])
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    circ = [c for c in g.circles_in_view(front, full_only=True) if abs(c[2] - 2.5) < 1e-9]
    assert len(circ) == 1 and np.allclose(circ[0][3], (0, 10))
    marks = [it for it in g.items() if it[1] == "CENTER" and it[2] == front["id"]]
    assert len(marks) == 2
    a = g.add_annot({"type": "dia", "view": front["id"], "pos": [170, 170],
                     "a": {"kind": "centre", "p": [float(x) for x in circ[0][0]], "r": 2.5, "i": circ[0][4],
                           "n": len(g._snapdata()[1])}})
    assert g.dim_text(a, g.dim_geometry(a)[2]) == "Ø5"
