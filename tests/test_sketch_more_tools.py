"""The rest of the sketch-toolbar request: tangent arc, extend, transform / copy, control-point spline, conic,
elliptical arc, intersect (project a section), DXF and image insertion."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K, sketch_ops as OPS  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_sketch_quick_tools import VP, _clicks, _len, _real_lines, _sketcher, _type  # noqa: E402

TOP = K.std_plane("Top")


def _areas(sk):
    return sorted(round(K.face_info(f)["area"], 4) for f in K.sketch_regions(TOP, sk.curves()))


def _line(sk, a, b):
    return sk.add_line(sk.add_point(*a), sk.add_point(*b))


# --------------------------------------------------------------------------- tangent arc

def test_tangent_arc_from_a_line_is_tangent_and_stays_so():
    sk = Sketch()
    l = _line(sk, (0, 0), (10, 0))
    b = sk.entities[l]["p2"]
    aid = OPS.tangent_arc(sk, b, (10, 10))
    C, r = sk.P(sk.entities[aid]["c"]), sk.radius(aid)
    assert np.allclose(C, (10, 5)) and abs(r - 5) < 1e-9
    sk.points[sk.entities[l]["p1"]] = [0.0, -3.0]            # tip the line: the arc follows it round
    sk.solve()
    e = sk.entities[l]
    d = sk.P(e["p2"]) - sk.P(e["p1"])
    rad = sk.P(b) - sk.P(sk.entities[aid]["c"])
    assert abs(d @ rad) < 1e-6                                # radius square to the line where they meet


def test_tangent_arc_chains_off_an_arc():
    sk = Sketch()
    l = _line(sk, (0, 0), (10, 0))
    a1 = OPS.tangent_arc(sk, sk.entities[l]["p2"], (10, 10))
    e = sk.entities[a1]
    end = e["p2"] if e["p1"] == sk.entities[l]["p2"] else e["p1"]
    a2 = OPS.tangent_arc(sk, end, (0, 12))
    assert sk.status.get("ok", True)
    c1, c2 = sk.P(e["c"]), sk.P(sk.entities[a2]["c"])
    r1, r2 = sk.radius(a1), sk.radius(a2)
    d = np.linalg.norm(c1 - c2)
    assert min(abs(d - abs(r1 - r2)), abs(d - (r1 + r2))) < 1e-6
    with pytest.raises(ValueError):
        OPS.tangent_arc(sk, sk.entities[l]["p1"], (-10, 0))   # straight on: that's a line
    lone = Sketch()
    with pytest.raises(ValueError):
        OPS.tangent_arc(lone, lone.add_point(0, 0), (1, 1))


def test_tangent_arc_tool():
    offers = []
    s = _sketcher(offers)
    _line(s.sk, (0, 0), (10, 0))
    _clicks(s, "arc_tan", (400, 250))                         # not the end of anything: refused
    assert not s.temp and "end of a line or arc" in s._prompt_label
    _clicks(s, "arc_tan", (500, 300), (500, 200))             # from (10, 0) to (10, 10)
    arcs = [k for k, e in s.sk.entities.items() if e["type"] == "arc"]
    assert len(arcs) == 1 and abs(s.sk.radius(arcs[0]) - 5) < 1e-6
    assert offers and offers[-1][0] == "radius"


# --------------------------------------------------------------------------- extend

def test_extend_line_to_a_line_and_to_a_circle():
    sk = Sketch()
    l = _line(sk, (0, 0), (4, 0))
    wall = _line(sk, (10, -5), (10, 5))
    OPS.extend(sk, l, (3.9, 0.1))
    assert np.allclose(sk.P(sk.entities[l]["p2"]), (10, 0))
    assert any(c["type"] == "point_on" and c["refs"] == [sk.entities[l]["p2"], wall] for c in sk.constraints)
    circ = sk.add_circle(sk.add_point(-10, 0), 3)
    OPS.extend(sk, l, (0.1, 0))                               # the other end, back to the circle
    assert np.allclose(sk.P(sk.entities[l]["p1"]), (-7, 0), atol=1e-6)
    assert any(c["refs"] == [sk.entities[l]["p1"], circ] for c in sk.constraints)


def test_extend_arc_round_to_a_line_and_refusals():
    sk = Sketch()
    c = sk.add_point(0, 0)
    arc = sk.add_arc(c, sk.add_point(5, 0), sk.add_point(0, 5))
    _line(sk, (-3, -10), (-3, 10))
    OPS.extend(sk, arc, (0.2, 5))                             # the p2 end carries on anticlockwise
    assert np.allclose(sk.P(sk.entities[arc]["p2"]), (-3, 4), atol=1e-6)
    lone = Sketch()
    l = _line(lone, (0, 0), (1, 0))
    with pytest.raises(ValueError):
        OPS.extend(lone, l, (1, 0))                           # nothing to reach
    with pytest.raises(ValueError):
        OPS.extend(lone, lone.add_circle(lone.add_point(5, 5), 1), (6, 5))


def test_extend_keeps_a_shared_corner():
    sk = Sketch()
    a, b, c = sk.add_point(0, 0), sk.add_point(4, 0), sk.add_point(4, 3)
    l1, l2 = sk.add_line(a, b), sk.add_line(b, c)
    _line(sk, (10, -5), (10, 5))
    OPS.extend(sk, l1, (4, 0))
    assert sk.entities[l1]["p2"] != b and np.allclose(sk.P(b), (4, 0))   # the corner of l2 stays put


def test_extend_tool():
    s = _sketcher()
    l = _line(s.sk, (0, 0), (4, 0))
    _line(s.sk, (10, -5), (10, 5))
    _clicks(s, "sk_extend", (438, 300))
    assert abs(_len(s.sk, l) - 10) < 1e-6


# --------------------------------------------------------------------------- transform / copy

def test_transform_moves_and_turns_and_copy_keeps_the_original():
    sk = Sketch()
    l = _line(sk, (1, 0), (2, 0))
    OPS.transform(sk, [l], 0, 0, 90, (0, 0))
    e = sk.entities[l]
    assert np.allclose(sk.P(e["p1"]), (0, 1)) and np.allclose(sk.P(e["p2"]), (0, 2))
    new = OPS.transform(sk, [l], 5, 0, 0, copy=True)
    assert len(new) == 1 and new[0] != l
    assert np.allclose(sk.P(sk.entities[new[0]]["p1"]), (5, 1))
    assert np.allclose(sk.P(e["p1"]), (0, 1))


def test_transform_tool_prompts_for_x_y_and_angle():
    s = _sketcher()
    l = _line(s.sk, (0, 0), (10, 0))
    s.selection = [l]
    s.set_tool("sk_transform")
    assert s.selection == [l]                                 # picked first: kept
    s.mouse_move(VP, 400, 300, None)
    s.mouse_press(VP, 300, 100, _Ev())                        # empty space: start, turning about (-10, 20)
    _type(s, "0")
    _type(s, "0")
    _type(s, "180")
    e = s.sk.entities[l]
    assert np.allclose(s.sk.P(e["p2"]), (-30, 40), atol=1e-6)
    assert s.tool == "select"


def test_copy_tool_makes_a_moved_copy():
    s = _sketcher()
    l = _line(s.sk, (0, 0), (10, 0))
    s.selection = [l]
    s.set_tool("sk_copy")
    s.mouse_press(VP, 300, 100, _Ev())
    _type(s, "5")
    _type(s, "2")
    _type(s, "0")
    lines = _real_lines(s.sk)
    assert len(lines) == 2
    other = next(k for k in lines if k != l)
    assert np.allclose(s.sk.P(s.sk.entities[other]["p1"]), (5, 2))


class _Ev:
    def modifiers(self):
        from PySide6.QtCore import Qt
        return Qt.NoModifier


# --------------------------------------------------------------------------- splines, conic

def test_control_point_spline_tool_and_region():
    s = _sketcher()
    _clicks(s, "spline_cp", (400, 300), (450, 200), (550, 200), (600, 300))
    s.key_press(VP, _Key())
    (sp,) = [e for e in s.sk.entities.values() if e["type"] == "spline"]
    assert sp["poles"] and len(sp["pts"]) == 4
    poly = s.sk.polyline(next(k for k, e in s.sk.entities.items() if e is sp))
    assert np.allclose(poly[0], (0, 0)) and np.allclose(poly[-1], (20, 0))   # starts / ends on the end poles
    assert poly[:, 1].max() < 10 - 1e-3                       # pulled towards the middle poles, not through them
    a, b = sp["pts"][0], sp["pts"][-1]
    s.sk.add_line(a, b)
    assert len(_areas(s.sk)) == 1


class _Key:
    def key(self):
        from PySide6.QtCore import Qt
        return Qt.Key_Return

    def modifiers(self):
        from PySide6.QtCore import Qt
        return Qt.NoModifier


def test_conic_is_exact_and_rho_changes_it():
    sk = Sketch()
    eid = OPS.conic(sk, (0, 0), (10, 0), (5, 5), 0.5)          # rho 0.5: a parabola
    e = sk.entities[eid]
    sk.add_line(e["pts"][2], e["pts"][0])
    assert abs(_areas(sk)[0] - 2 / 3 * 10 * 2.5) < 1e-4        # a parabolic segment: 2/3 base × height
    e["weights"] = [1.0, OPS.conic_weight(0.25), 1.0]          # flatter
    assert _areas(sk)[0] < 2 / 3 * 10 * 2.5
    with pytest.raises(ValueError):
        OPS.conic_weight(1.0)


def test_conic_tool_asks_for_rho():
    s = _sketcher()
    _clicks(s, "conic", (400, 300), (500, 300), (450, 250))
    _type(s, "0.7")
    (e,) = [e for e in s.sk.entities.values() if e["type"] == "spline"]
    assert e["poles"] and abs(e["rho"] - 0.7) < 1e-12 and abs(e["weights"][1] - 0.7 / 0.3) < 1e-9


# --------------------------------------------------------------------------- elliptical arc

def test_ellipse_arc_closes_a_region_and_stays_on_its_ellipse():
    sk = Sketch()
    c, ax = sk.add_point(0, 0), sk.add_point(10, 0)
    p1, p2 = sk.add_point(10, 0), sk.add_point(-10, 0)
    ea = sk.add_ellipse_arc(c, ax, 5, p1, p2)
    sk.add_line(p2, p1)
    assert sk.solve()
    assert abs(_areas(sk)[0] - math.pi * 10 * 5 / 2) < 1e-4
    sk.add_constraint("radius", ea, value=3.0)               # narrower: the ends stay on it
    assert sk.solve()
    ctr, axv, r = sk.ellipse_axes(ea)
    for p in (p1, p2):
        w = sk.P(p) - ctr
        u = axv / np.linalg.norm(axv)
        x, y = w @ u, w[0] * -u[1] + w[1] * u[0]
        assert abs((x / np.linalg.norm(axv)) ** 2 + (y / r) ** 2 - 1) < 1e-6


def test_ellipse_arc_tool_and_copy():
    offers = []
    s = _sketcher(offers)
    _clicks(s, "ellipse_arc", (400, 300), (500, 300), (400, 250), (500, 300), (300, 300))
    (eid,) = [k for k, e in s.sk.entities.items() if e["type"] == "ellipse_arc"]
    t1, t2 = s.sk.ellipse_arc_params(eid)
    assert abs((t2 - t1) - math.pi) < 1e-6                    # the top half
    assert offers[-1][0] == "radius" and abs(offers[-1][2] - 5) < 1e-6
    new = OPS.transform(s.sk, [eid], 0, -20, copy=True)
    assert s.sk.entities[new[0]]["type"] == "ellipse_arc"


# --------------------------------------------------------------------------- intersect

class _PartVP:
    def __init__(self, shape):
        self.part_shapes = {"P1": shape}
        self.status_text = ""

    def update(self):
        pass


def test_intersect_brings_in_a_section():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    from freeshape.ui.sketcher import Sketcher
    from freeshape.ui.viewport import Pick
    sphere = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 5.0).Shape()
    s = Sketcher(TOP, Sketch(), "f1", lambda *a: None)
    vp = _PartVP(sphere)
    fi = next(i for i, f in enumerate(K.faces(sphere)))
    assert s._intersect_face(vp, Pick("face", ("P1", fi))) == 1
    (circ,) = [e for e in s.sk.entities.values() if e["type"] == "circle"]
    assert abs(circ["r"] - 5) < 1e-6 and circ["construction"]
    assert s.sk.solve() and s.sk.status.get("dof") == 0       # fixed in place
    box = BRepPrimAPI_MakeBox(gp_Pnt(-2, -3, -1), 4.0, 6.0, 2.0).Shape()
    s2 = Sketcher(TOP, Sketch(), "f1", lambda *a: None)
    assert s2._intersect_face(_PartVP(box), Pick("part", ("P1",))) == 4   # the box cut through: 4 lines
    above = Sketcher(K.Plane(np.array([0, 0, 50.0]), np.array([1.0, 0, 0]), np.array([0, 0, 1.0])), Sketch(),
                     "f1", lambda *a: None)
    vpa = _PartVP(box)
    assert above._intersect_face(vpa, Pick("part", ("P1",))) == 0
    assert "cross" in vpa.status_text


# --------------------------------------------------------------------------- DXF, image

def _dxf(tmp_path):
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 1                               # inches
    m = doc.modelspace()
    m.add_lwpolyline([(0, 0, 0), (2, 0, 0.4142), (2, 1, 0), (0, 1, 0)], format="xyb", close=True)
    m.add_circle((1, 0.5), 0.2)
    m.add_ellipse((10, 0), (2, 0, 0), 0.5, 0, math.pi)
    m.add_line((8, 0), (12, 0))
    m.add_spline([(0, 3), (1, 4), (2, 3), (3, 4)])
    blk = doc.blocks.new("B")
    blk.add_line((0, 0), (1, 0))
    m.add_blockref("B", (20, 20))
    m.add_text("skip me")
    p = str(tmp_path / "t.dxf")
    doc.saveas(p)
    return p


def test_dxf_import_joins_ends_scales_units_and_makes_regions(tmp_path):
    from freeshape.dxf_import import import_dxf
    sk = Sketch()
    rep = import_dxf(sk, _dxf(tmp_path))
    assert rep["scale"] == 25.4 and rep["skipped"] == {"TEXT": 1}
    kinds = [e["type"] for e in sk.entities.values() if not e.get("construction")]
    assert kinds.count("arc") == 1 and kinds.count("ellipse_arc") == 1 and kinds.count("spline") == 1
    areas = _areas(sk)
    half_ellipse = math.pi * 50.8 * 25.4 / 2
    assert any(abs(a - half_ellipse) < 1e-3 for a in areas)
    assert any(abs(a - math.pi * (0.2 * 25.4) ** 2) < 1e-3 for a in areas)
    blk = [e for e in sk.entities.values() if e["type"] == "line" and
           np.allclose(sk.P(e["p1"]), (20 * 25.4, 20 * 25.4))]
    assert len(blk) == 1                                      # the block was exploded into place


def test_insert_dxf_is_one_undo_step(tmp_path):
    s = _sketcher()
    s.insert_dxf(_dxf(tmp_path), scale=1.0)
    assert len(s.sk.entities) > 5
    s.undo()
    assert not s.sk.entities


def test_insert_image_traces_and_sizes_by_its_corners(tmp_path):
    from PySide6.QtGui import QImage, QColor
    img = QImage(200, 100, QImage.Format_RGB32)
    img.fill(QColor("red"))
    path = str(tmp_path / "pic.png")
    img.save(path)
    s = _sketcher()
    eid = s.insert_image(path)
    e = s.sk.entities[eid]
    assert e["type"] == "image" and abs(e["aspect"] - 0.5) < 1e-9 and e["data"]
    box = s.sk.image_box(eid)
    assert np.allclose(box[2], (100, 50))
    s.sk.add_constraint("distance", e["p1"], e["p2"], value=40.0)   # size it: 40 mm wide
    s.sk.solve()
    assert np.allclose(np.ptp(s.sk.image_box(eid), axis=0), (40, 20), atol=1e-6)
    assert not s.sk.curves()                                  # never part of a region
    again = Sketch.from_dict(s.sk.to_dict())
    assert again.entities[eid]["data"] == e["data"]
    with pytest.raises(ValueError):
        s.insert_image(str(tmp_path / "missing.png"))
