"""Sketch tools added for the toolbar request: polygons with any number of sides (inscribed / circumscribed),
midpoint line, aligned 3-point rectangle, sketch chamfer and split."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from freeshape import features as F, kernel as K, sketch_ops as OPS  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher  # noqa: E402


class _Cam:
    scale = 1.0

    def to_screen(self, w):                      # 10 px per mm, origin at (400, 300), y up
        w = np.asarray(w, float)
        return np.stack([w[..., 0] * 10 + 400, 300 - w[..., 1] * 10, np.zeros(w.shape[:-1])], axis=-1)


class _VP:
    cam = _Cam()

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)

    def update(self):
        pass

    def setFocus(self):
        pass


VP = _VP()


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _sketcher(offers=None):
    QApplication.instance() or QApplication([])
    s = Sketcher(K.std_plane("Top"), Sketch(), "f1", lambda *a: None)
    s._make_editor = lambda _vp, x, y, text: QLineEdit(text)
    s._offer_dims = (lambda vp, o: offers.extend(o)) if offers is not None else (lambda *a, **k: None)
    return s


def _clicks(s, tool, *pts):
    s.set_tool(tool)
    for px, py in pts:
        s.mouse_move(VP, px, py, _Ev())
        s.mouse_press(VP, px, py, _Ev())


def _type(s, text):
    ed = s._editor
    assert ed is not None
    ed.setText(text)
    ed.textEdited.emit(text)
    ed.returnPressed.emit()
    QApplication.processEvents()


def _real_lines(sk):
    return [k for k, e in sk.entities.items() if e["type"] == "line" and not e.get("construction")]


def _len(sk, lid):
    e = sk.entities[lid]
    return float(np.linalg.norm(sk.P(e["p2"]) - sk.P(e["p1"])))


# --------------------------------------------------------------------------- polygon

@pytest.mark.parametrize("inscribed", [True, False])
@pytest.mark.parametrize("n", [3, 4, 5, 8])
def test_polygon_op_is_regular_and_only_needs_centre_and_size(n, inscribed):
    sk = Sketch()
    ang, level = OPS.polygon_angle((0.2, 10.0), n, inscribed)          # mouse nearly straight up: snaps level
    made = OPS.polygon(sk, (0, 0), ang, 10.0, n, inscribed, level=level)
    assert sk.status.get("ok", True) and sk.status.get("dof") == 3      # centre x, y and size
    sides = [_len(sk, i) for i in made["sides"]]
    assert np.ptp(sides) < 1e-6
    pts = np.array([sk.P(p) for p in made["points"]])
    R = np.linalg.norm(pts, axis=1)
    assert np.ptp(R) < 1e-6
    assert abs(sk.radius(made["circle"]) - 10.0) < 1e-6
    assert abs(R[0] - (10.0 if inscribed else 10.0 / math.cos(math.pi / n))) < 1e-6
    assert level is not None and any(c["type"] in ("horizontal", "vertical") for c in sk.constraints)


def test_polygon_angle_snaps_a_level_side_or_a_corner_on_top():
    assert OPS.polygon_angle((1.0, 0.03), 6)[1] == "flat"              # as the hexagon tool: corner at 0°
    assert OPS.polygon_angle((0.03, 1.0), 6)[1] == "point"
    assert OPS.polygon_angle((1.0, 0.4), 6)[1] is None
    assert OPS.polygon_angle((0.0, 1.0), 4, inscribed=False)[1] == "flat"   # square, mouse on the top side


def test_polygon_tool_asks_for_the_sides_then_offers_the_diameter():
    offers = []
    s = _sketcher(offers)
    _clicks(s, "polygon", (400, 300), (400, 200))                       # centre at origin, corner 10 mm up
    _type(s, "5")
    sk = s.sk
    assert len(_real_lines(sk)) == 5 and s.poly_sides == 5
    assert sk.status.get("ok", True)
    assert offers and offers[0][0] == "diameter" and abs(offers[0][2] - 20.0) < 1e-6


def test_circumscribed_hexagon_sized_across_flats_extrudes_a_nut_pocket():
    offers = []
    s = _sketcher(offers)
    _clicks(s, "polygon_out", (400, 300), (400, 235))                   # side midpoint 6.5 mm up
    _type(s, "6")
    ctype, refs, value, _pos = offers[0]
    assert ctype == "diameter" and abs(value - 13.0) < 1e-6            # M8 nut: 13 mm across flats
    sk = s.sk
    sk.add_constraint(ctype, *refs, value=13.0)
    sk.add_constraint("coincident", sk.entities[refs[0]]["c"], "origin")
    assert sk.solve() and sk.status.get("dof", 0) == 0
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = sk.to_dict()
    doc.add_feature(f)
    regions = doc.state.sketches[f["id"]].regions
    assert len(regions) == 1                                            # construction circles make no area
    r = 6.5 / math.cos(math.pi / 6)
    area = K.face_info(regions[0])["area"]
    assert abs(area - 1.5 * math.sqrt(3) * r * r) < 1e-6
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, f["id"], regions[0])], depth=6.5)
    doc.add_feature(ex)
    part = next(iter(doc.state.parts.values()))
    assert abs(K.volume(part.shape) - area * 6.5) < 1e-6


# --------------------------------------------------------------------------- midpoint line, 3-point rectangle

def test_midpoint_line_is_centred_on_the_first_click():
    offers = []
    s = _sketcher(offers)
    _clicks(s, "line_mid", (400, 300), (480, 300))                     # on the origin, 8 mm out, level
    sk = s.sk
    (lid,) = _real_lines(sk)
    e = sk.entities[lid]
    assert np.allclose(sk.P(e["p1"]), (-8, 0)) and np.allclose(sk.P(e["p2"]), (8, 0))
    types = {c["type"] for c in sk.constraints}
    assert {"midpoint", "horizontal"} <= types
    assert offers[0][0] == "distance" and abs(offers[0][2] - 16.0) < 1e-6
    sk.points[e["p2"]] = [10.0, 0.0]                                    # move one end: still centred on the origin
    sk.solve()
    assert _len(sk, lid) > 16.5 and np.allclose(sk.P(e["p1"]), -sk.P(e["p2"]), atol=1e-6)


def test_three_point_rectangle_at_an_angle_stays_square_cornered():
    offers = []
    s = _sketcher(offers)
    _clicks(s, "rect3", (400, 300), (480, 240), (450, 175))             # first side (0,0)-(8,6), 10 mm long
    sk = s.sk
    lines = _real_lines(sk)
    assert len(lines) == 4 and sk.status.get("ok", True)
    d = [sk.P(sk.entities[l]["p2"]) - sk.P(sk.entities[l]["p1"]) for l in lines]
    for k in range(4):
        assert abs(d[k] @ d[(k + 1) % 4]) < 1e-6                         # every corner square
    assert abs(_len(sk, lines[0]) - 10.0) < 1e-6
    assert [o[0] for o in offers] == ["distance", "distance"]
    sk.points[sk.entities[lines[1]]["p2"]] = [3.0, 12.0]               # pull a corner: still a rectangle
    sk.solve()
    d = [sk.P(sk.entities[l]["p2"]) - sk.P(sk.entities[l]["p1"]) for l in lines]
    assert all(abs(d[k] @ d[(k + 1) % 4]) < 1e-6 for k in range(4))


# --------------------------------------------------------------------------- chamfer, split

def _corner():
    sk = Sketch()
    a, b, c = sk.add_point(0, 0), sk.add_point(10, 0), sk.add_point(10, 8)
    l1, l2 = sk.add_line(a, b), sk.add_line(b, c)
    sk.add_constraint("horizontal", l1)
    sk.add_constraint("vertical", l2)
    sk.add_constraint("distance", l1, value=10.0)
    return sk, a, b, c, l1, l2


def test_chamfer_cuts_the_corner_and_keeps_the_length():
    sk, a, b, c, l1, l2 = _corner()
    cid = OPS.chamfer_corner(sk, b, 2.0)
    assert sk.status.get("ok", True)
    e = sk.entities[cid]
    ends = sorted(map(tuple, np.round([sk.P(e["p1"]), sk.P(e["p2"])], 6)))
    assert ends == [(8.0, 0.0), (10.0, 2.0)]
    dims = [k for k in sk.constraints if k["type"] == "distance"]
    assert any(k["refs"] == [a, b] for k in dims)                        # the 10 mm still runs to the sharp corner
    dim = next(k for k in dims if k["refs"] != [a, b])
    dim["value"] = 3.0                                                  # change the chamfer: both sides follow
    sk.solve()
    P = sk.P(b)
    assert sorted(round(float(np.linalg.norm(sk.P(e[k]) - P)), 6) for k in ("p1", "p2")) == [3.0, 3.0]
    assert abs(np.linalg.norm(sk.P(a) - P) - 10.0) < 1e-6
    assert len(_real_lines(sk)) == 3                                    # the hidden legs are construction


def test_chamfer_refuses_what_it_cannot_do():
    sk, a, b, c, l1, l2 = _corner()
    with pytest.raises(ValueError):
        OPS.chamfer_corner(sk, b, 9.0)                                  # longer than the 8 mm side
    with pytest.raises(ValueError):
        OPS.chamfer_corner(sk, a, 1.0)                                  # an open end, not a corner


def test_chamfer_tool_prompts_for_the_distance():
    s = _sketcher()
    s.sk, a, b, c, l1, l2 = _corner()
    _clicks(s, "sk_chamfer", (500, 300))                                # the corner at (10, 0)
    _type(s, "1.5")
    assert len(_real_lines(s.sk)) == 3
    assert any(k["type"] == "distance" and k["value"] == 1.5 for k in s.sk.constraints)


def test_split_line_keeps_it_straight_and_its_length():
    sk = Sketch()
    a, b = sk.add_point(0, 0), sk.add_point(10, 0)
    lid = sk.add_line(a, b)
    sk.add_constraint("horizontal", lid)
    sk.add_constraint("distance", lid, value=10.0)
    first, second = OPS.split(sk, lid, (4, 0.3))
    assert np.allclose(sk.P(sk.entities[first]["p2"]), (4, 0))
    assert sk.entities[first]["p2"] == sk.entities[second]["p1"]
    sk.points[sk.entities[first]["p2"]] = [6.0, 2.0]                    # move the cut point: stays in line
    sk.solve()
    ys = [sk.P(p)[1] for p in (a, sk.entities[first]["p2"], b)]
    assert np.ptp(ys) < 1e-6
    assert abs(_len(sk, first) + _len(sk, second) - 10.0) < 1e-6        # overall 10 mm kept


def test_split_arc_and_errors():
    sk = Sketch()
    c, p1, p2 = sk.add_point(0, 0), sk.add_point(5, 0), sk.add_point(0, 5)
    aid = sk.add_arc(c, p1, p2)
    first, second = OPS.split(sk, aid, (3, 3))
    q = sk.P(sk.entities[first]["p2"])
    assert np.allclose(q, (5 / math.sqrt(2), 5 / math.sqrt(2)))
    assert sk.entities[second]["c"] == c
    with pytest.raises(ValueError):
        OPS.split(sk, first, (5, 0))                                    # at an end
    circ = sk.add_circle(c, 3.0)
    with pytest.raises(ValueError):
        OPS.split(sk, circ, (3, 0))


def test_split_tool_click():
    s = _sketcher()
    a, b = s.sk.add_point(0, 0), s.sk.add_point(10, 0)
    s.sk.add_line(a, b)
    _clicks(s, "sk_split", (440, 300))
    assert len(_real_lines(s.sk)) == 2
