"""2.0 stage 2: section and detail views, nesting flat patterns for cutting, parts lists and balloons."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K, sheetmetal as SM, nesting as N  # noqa: E402
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf, _clip_to_circle  # noqa: E402


def _part():
    """80 × 40 × 16 block with a counterbored hole and a plain hole, both on y = 20."""
    s = K.cut(BRepPrimAPI_MakeBox(80, 40, 16).Shape(), K.cylinder((25, 20, -1), (0, 0, 1), 5, 18))
    s = K.cut(s, K.cylinder((25, 20, 10), (0, 0, 1), 9, 10))
    return K.cut(s, K.cylinder((60, 20, -1), (0, 0, 1), 4, 18))


def _drawing(shape, bom=None):
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda src: shape
    if bom is not None:
        g.bom_resolver = lambda src: bom
    g.sheet.update(size="A3", scale=2.0)
    g.refresh(notify=False)
    return g


# ------------------------------------------------------------------ section and detail views
def test_section_cut_faces_and_hatching():
    part = _part()
    cut = K.section_cut(part, [20, 20, 0], [0, -1, 0])          # keep the half behind the y = 20 cut
    assert abs(K.volume(cut) - K.volume(part) / 2) < 1e-3
    faces = K.section_faces(cut, [20, 20, 0], [0, -1, 0])
    assert len(faces) == 1 and len(faces[0]) == 3                # material either side of the two holes
    segs = [s_ for f in faces[0] for s_ in K.hatch_segments(f, [0, 20, 0], [1, 0, 1], [1, 0, -1], 2.0)]
    area = sum(K.face_info(f)["area"] for f in faces[0])
    assert abs(sum(np.linalg.norm(q - p) for p, q in segs) - area / 2.0) < area * 0.02


def test_section_and_detail_views_on_the_sheet_and_in_dxf(tmp_path):
    g = _drawing(_part())
    top = g.add_view(*DIRECTIONS["Top"], (110, 190), checkpoint=False)
    a = g.to_paper(top, g.project(top, [-5, 20, 0]))
    b = g.to_paper(top, g.project(top, [85, 20, 0]))
    sec = g.add_section(top, a, b, (110, 90))
    assert g.view_title(sec) == "SECTION A-A"
    assert np.allclose(sec["d"], [0, -1, 0])                     # placed below: looking from the front
    c = g.to_paper(sec, g.project(sec, [25, 20, 13]))
    det = g.add_detail(sec, c, 14, (320, 90))
    assert g.view_title(det) == "DETAIL B (4:1)"
    layers = {it[1] for it in g.items()}
    assert {"HATCH", "CUT", "CUT_THICK", "DETAIL"} <= layers
    by = g.items_by_owner()
    assert any(it[1] == "HATCH" for it in by[det["id"]])          # the detail of a section is hatched too
    export_dxf(g, str(tmp_path / "s.dxf"))
    import ezdxf
    got = {e.dxf.layer for e in ezdxf.readfile(str(tmp_path / "s.dxf")).modelspace()}
    assert {"HATCH", "CUT", "DETAIL"} <= got
    g.save(str(tmp_path / "s.fdwg"))
    g2 = DrawingDoc.load(str(tmp_path / "s.fdwg"), resolver=lambda src: _part())
    assert [v.get("type") for v in g2.views] == ["base", "section", "detail"]
    g.delete([top["id"]])
    assert g.views == []                                          # its section and detail go with it


def test_clip_to_circle():
    pieces = _clip_to_circle([np.array([-10.0, 0]), np.array([10.0, 0])], np.array([0.0, 0]), 5.0)
    assert len(pieces) == 1 and np.allclose(pieces[0][0], [-5, 0]) and np.allclose(pieces[0][-1], [5, 0])
    assert _clip_to_circle([np.array([-10.0, 9]), np.array([10.0, 9])], np.array([0.0, 0]), 5.0) == []


# ------------------------------------------------------------------ nesting
T_, R_, KF = 2.0, 2.0, 0.44


def _flat(points):
    face = SM._polygon_face([(x, y, 0) for x, y in points])
    shape, m = SM.make_base(face, K.std_plane("Top"), T_, R_, KF)
    return SM.flat_pattern(shape, m)


def test_nest_fits_parts_with_gaps_margin_kerf_and_pairs(tmp_path):
    rect = N.part_from_flat(_flat([(0, 0), (140, 0), (140, 70), (0, 70)]), "Plate", kerf=0.2)
    tri = N.part_from_flat(_flat([(0, 0), (80, 0), (0, 80)]), "Gusset")
    assert abs(rect.w - 140.2) < 1e-6 and abs(rect.h - 70.2) < 1e-6                 # kerf grew the outline
    assert abs(tri.w - 80 * math.sqrt(2)) < 1e-6                                    # turned onto its long side
    assert N._pair(tri, 5.0) is not None and N._pair(rect, 5.0) is None             # triangles pair up
    res = N.nest([rect, tri], [9, 14], 1000, 500, spacing=5, margin=10)
    assert len(res.sheets) == 1 and res.count == 23
    polys = []
    for pl in res.sheets[0]:
        pts = N._hull(N.part_points(res.parts[pl.part])) @ N._rot(pl.angle).T + np.array([pl.tx, pl.ty])
        assert pts.min() >= 10 - 1e-6 and pts[:, 0].max() <= 990 + 1e-6 and pts[:, 1].max() <= 490 + 1e-6
        polys.append(pts)
    assert all(N._separated(polys[i], polys[j], 5 - 1e-6) for i in range(len(polys)) for j in range(i))
    s = N.summary(res, 7.85)
    area = 9 * 140 * 70 + 14 * 80 * 80 / 2
    assert abs(s["part_kg"] - area * 2 * 7.85 / 1e6) < 1e-6
    N.export_dxf(res, str(tmp_path / "n.dxf"))
    import ezdxf
    layers = [e.dxf.layer for e in ezdxf.readfile(str(tmp_path / "n.dxf")).modelspace()]
    assert layers.count("SHEET") == 1 and layers.count("CUT") >= 23 * 3


def test_nest_spills_onto_more_sheets_and_refuses_oversize():
    big = N.part_from_flat(_flat([(0, 0), (400, 0), (400, 300), (0, 300)]), "Big")
    res = N.nest([big], [5], 1000, 500, spacing=5, margin=10)
    assert len(res.sheets) == 2 and res.count == 5            # turned upright, three fit across a sheet
    assert [len(sh) for sh in res.sheets] == [3, 2]
    try:
        N.nest([big], [1], 300, 200)
        assert False, "should not fit"
    except ValueError as e:
        assert "doesn't fit" in str(e)


# ------------------------------------------------------------------ parts list and balloons
def _peg_board():
    plate = K.cut(BRepPrimAPI_MakeBox(80, 40, 8).Shape(), K.cylinder((20, 20, -1), (0, 0, 1), 4, 10))
    pegs = [K.cylinder((x, 20, -5), (0, 0, 1), 4, 30) for x in (20, 40, 60)]
    rows = [{"name": "Base plate", "qty": 1, "shapes": [plate]}, {"name": "Peg", "qty": 3, "shapes": pegs}]
    return K.compound([plate] + pegs), rows


def test_parts_list_and_balloons(tmp_path):
    shape, rows = _peg_board()
    g = _drawing(shape, rows)
    assert [(r["item"], r["name"], r["qty"]) for r in g.bom] == [(1, "Base plate", 1), (2, "Peg", 3)]
    front = g.add_view(*DIRECTIONS["Front"], (110, 180), checkpoint=False)
    g.add_bom_table()
    texts = [it[4] for it in g.items() if it[0] == "text"]
    assert "Base plate" in texts and "Peg" in texts and "QTY" in texts
    assert g.auto_balloons(front) == 2 and g.auto_balloons(front) == 0           # not twice
    for a in g.annots:
        if a["type"] == "balloon":
            q = np.asarray(a["anchor"], float)
            _r, _u, d = g.axes(front)
            hit = g._first_hit(q + np.asarray(d) * 500, -np.asarray(d, float))
            assert hit is not None and hit[0] == a["item"] - 1                   # it points at something visible
            if a["item"] == 2:
                assert q[2] > 8                                                  # a peg above the plate, not behind
    P = g.to_paper(front, g.project(front, [40, 20, 15]))
    assert g.part_at(front, P)[0] == 1                                           # clicking a peg finds the pegs
    g.save(str(tmp_path / "b.fdwg"))
    g2 = DrawingDoc.load(str(tmp_path / "b.fdwg"))
    assert len(g2.bom) == 2 and sum(a["type"] == "balloon" for a in g2.annots) == 2
