"""Sketching: hovering a point / midpoint, then moving level or plumb with it, snaps and adds the constraint."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher  # noqa: E402


class _Cam:
    scale = 1.0

    def to_screen(self, w):                      # 10 px per mm, origin at (400, 300), y up
        return (w[0] * 10 + 400, 300 - w[1] * 10, 0)


class _ViewportStub:
    cam = _Cam()

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)


VP = _ViewportStub()


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _sketcher(sk=None):
    s = Sketcher(K.std_plane("Top"), sk or Sketch(), "f1", lambda *a: None)
    s._offer_dims = lambda *a, **k: None          # no dimension editor in a headless test
    return s


def _click(s, x, y):
    s.mouse_move(VP, x, y, _Ev())
    s.mouse_press(VP, x, y, _Ev())


def _cons(s):
    return [(c["type"], tuple(c["refs"])) for c in s.sk.constraints]


def test_hover_origin_then_level_snaps_and_constrains():
    s = _sketcher()
    s.set_tool("line")
    s.mouse_move(VP, 400, 300, _Ev())            # rest on the origin
    s.mouse_move(VP, 600, 303, _Ev())            # a little off level
    assert s.infer_hits and s.infer_hits[0][1] == "H"
    assert abs(s.cursor_uv[1]) < 1e-9             # snapped onto the origin's level
    s.mouse_press(VP, 600, 303, _Ev())
    p1 = next(p for p in s.sk.points if p not in ("origin",))
    assert ("horizontal", ("origin", p1)) in _cons(s)
    assert s.sk.points[p1] == [20.0, 0.0]


def test_two_sources_give_both_axes():
    sk = Sketch()
    a = sk.add_point(-10, 5)
    b = sk.add_point(15, -8)
    sk.add_line(a, b)
    s = _sketcher(sk)
    s.set_tool("point")
    s.mouse_move(VP, 400 - 100, 300 - 50, _Ev())     # hover (-10, 5)
    s.mouse_move(VP, 400 + 150, 300 + 80, _Ev())     # hover (15, -8)
    s.mouse_move(VP, 400 - 98, 300 + 82, _Ev())      # level with (15, -8), plumb with (-10, 5)
    assert {ax for _u, ax in s.infer_hits} == {"H", "V"}
    assert np.allclose(s.cursor_uv, [-10, -8])
    s.mouse_press(VP, 400 - 98, 300 + 82, _Ev())
    kinds = sorted(t for t, _ in _cons(s))
    assert kinds == ["horizontal", "vertical"]


def test_midpoint_snap_adds_midpoint_constraint():
    sk = Sketch()
    a, b = sk.add_point(-10, 10), sk.add_point(10, 10)
    line = sk.add_line(a, b)
    s = _sketcher(sk)
    s.set_tool("point")
    s.mouse_move(VP, 402, 202, _Ev())                # near the middle of the line, (0, 10)
    assert s.snap_info == ("mid", line)
    s.mouse_press(VP, 402, 202, _Ev())
    assert any(t == "midpoint" and r[1] == line for t, r in _cons(s))


def test_no_inference_when_not_aligned():
    s = _sketcher()
    s.set_tool("line")
    s.mouse_move(VP, 400, 300, _Ev())
    s.mouse_move(VP, 600, 340, _Ev())                # clearly diagonal
    assert not s.infer_hits and s._infer_cons == []


def test_box_selection_window_and_crossing():
    sk = Sketch()
    a, b, c = sk.add_point(-5, -5), sk.add_point(5, -5), sk.add_point(5, 5)
    l1, l2 = sk.add_line(a, b), sk.add_line(b, c)
    s = _sketcher(sk)
    s.set_tool("select")

    def drag(x0, y0, x1, y1):
        s.selection = []
        s.mouse_press(VP, x0, y0, _Ev())
        s.mouse_drag(VP, x1, y1, _Ev())
        s.mouse_release(VP, x1, y1, _Ev(), True)
        return set(s.selection)

    assert drag(340, 340, 460, 360) == {l1}                  # left-to-right around the bottom line only
    assert drag(340, 240, 460, 360) == {l1, l2, "origin"}    # around everything
    assert drag(440, 300, 460, 360) == set()                 # only part of l2 inside: window selects nothing
    assert drag(460, 300, 440, 360) == {l1, l2}              # right-to-left: anything touched


def test_three_point_arc_keeps_its_third_point():
    s = _sketcher()
    s.set_tool("arc3")
    for x, y in ((300, 300), (500, 300), (400, 200)):        # (-10,0), (10,0), (0,10)
        _click(s, x, y)
    arcs = [e for e in s.sk.entities.values() if e["type"] == "arc"]
    assert len(arcs) == 1
    pts = [e for e in s.sk.entities.values() if e["type"] == "point"]
    assert len(pts) == 1                                       # the third point is kept ...
    third = pts[0]["p"]
    assert np.allclose(s.sk.points[third], [0, 10])
    assert any(c["type"] == "point_on" and c["refs"][0] == third for c in s.sk.constraints)    # ... on the arc
    aid = next(i for i, e in s.sk.entities.items() if e["type"] == "arc")
    r0 = s.sk.radius(aid)
    # dragging the third point reshapes the arc
    s.set_tool("select")
    s.mouse_press(VP, 400, 200, _Ev())
    s.mouse_drag(VP, 400, 150, _Ev())
    s.mouse_release(VP, 400, 150, _Ev(), True)
    assert s.sk.points[third][1] > 12 and abs(s.sk.radius(aid) - r0) > 0.5
    assert not [c for c in s.sk.constraints if c["type"] in ("radius", "diameter")]    # no radius was imposed


def test_line_tangent_to_a_circle_from_a_point():
    sk = Sketch()
    c = sk.add_point(20, 0)
    sk.add_circle(c, 5)
    s = _sketcher(sk)
    s.set_tool("line")
    _click(s, 400 - 100, 300)                     # start at (-10, 0)
    # tangent points of a line from (-10,0) to the circle (center 20,0, r 5): hover near one of them
    d = 30.0
    ang = np.arccos(5 / d)
    T = np.array([20, 0]) + 5 * np.array([np.cos(np.pi - ang), np.sin(np.pi - ang)])
    px, py = 400 + T[0] * 10 + 3, 300 - T[1] * 10 + 3          # a few pixels off
    s.mouse_move(VP, px, py, _Ev())
    assert s.infer_tan is not None and s.infer_hits[-1][1] == "T"
    assert np.allclose(s.cursor_uv, T, atol=1e-6)
    s.mouse_press(VP, px, py, _Ev())
    kinds = [c["type"] for c in s.sk.constraints]
    assert "tangent" in kinds and "point_on" in kinds
    s.sk.solve()
    lid = [k for k, e in s.sk.entities.items() if e["type"] == "line"][0]
    e = s.sk.entities[lid]
    a, b = s.sk.P(e["p1"]), s.sk.P(e["p2"])
    w = np.array([20, 0]) - a
    d = b - a
    dist = abs(d[0] * w[1] - d[1] * w[0]) / np.linalg.norm(d)
    assert abs(dist - 5) < 1e-5


def test_line_from_an_arc_end_snaps_to_its_tangent():
    sk = Sketch()
    c = sk.add_point(0, 0)
    a = sk.add_point(10, 0)
    b = sk.add_point(0, 10)
    sk.add_arc(c, a, b)
    s = _sketcher(sk)
    s.set_tool("line")
    _click(s, 400 + 100, 300)                     # start at the arc's end (10, 0); tangent there is vertical
    s.mouse_move(VP, 400 + 101, 300 - 150, _Ev())  # nearly straight up
    assert s.infer_tan is not None
    assert abs(s.cursor_uv[0] - 10) < 1e-9
    s.mouse_press(VP, 400 + 101, 300 - 150, _Ev())
    assert "tangent" in [c["type"] for c in s.sk.constraints]
    s.mouse_move(VP, 400 + 300, 300 - 300, _Ev())  # far from tangent: no inference
    assert s.infer_tan is None
