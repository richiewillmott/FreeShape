"""Normal: a line at right angles to a circle, arc or ellipse where it meets it (also made by Perpendicular when
a line and a curve are picked)."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _angle_to_radius(sk, line, centre):
    l = sk.entities[line]
    d = sk.P(l["p2"]) - sk.P(l["p1"])
    w = sk.P(centre) - sk.P(l["p1"])
    return abs(d[0] * w[1] - d[1] * w[0]) / (np.linalg.norm(d) * np.linalg.norm(w))


def test_line_normal_to_a_circle_runs_through_its_centre():
    sk = Sketch()
    c = sk.add_point(0, 0)
    circ = sk.add_circle(c, 10)
    a, b = sk.add_point(10, 0), sk.add_point(18, 6)              # starts on the circle, leans off the radius
    line = sk.add_line(a, b)
    sk.add_constraint("point_on", a, circ)
    sk.add_constraint("normal", line, circ)
    assert sk.solve()
    assert _angle_to_radius(sk, line, c) < 1e-6
    assert abs(np.linalg.norm(sk.P(a) - sk.P(c)) - sk.radius(circ)) < 1e-6


def test_line_normal_to_an_ellipse_at_its_foot():
    sk = Sketch()
    c, ax = sk.add_point(0, 0), sk.add_point(20, 0)
    el = sk.add_ellipse(c, ax, 10)                                # 20 x 10
    q = (20 * math.cos(0.7), 10 * math.sin(0.7))
    a, b = sk.add_point(*q), sk.add_point(q[0] + 8, q[1] + 1)
    line = sk.add_line(a, b)
    sk.add_constraint("point_on", a, el)
    sk.add_constraint("normal", line, el, a)
    assert sk.solve()
    c0, ax0 = sk.P(c), sk.P(ax)
    A, B = np.linalg.norm(ax0 - c0), abs(sk.entities[el]["r"])
    u = (ax0 - c0) / A
    v = np.array([-u[1], u[0]])
    w = sk.P(a) - c0
    grad = u * (w @ u) / A ** 2 + v * (w @ v) / B ** 2            # the ellipse's normal at the foot
    d = sk.P(b) - sk.P(a)
    assert abs(d[0] * grad[1] - d[1] * grad[0]) / (np.linalg.norm(d) * np.linalg.norm(grad)) < 1e-6


def test_perpendicular_tool_on_a_line_and_an_arc_makes_normal():
    from freeshape.ui.sketcher import Sketcher
    sk = Sketch()
    c = sk.add_point(0, 0)
    circ = sk.add_circle(c, 10)
    a, b = sk.add_point(10, 0), sk.add_point(18, 6)
    line = sk.add_line(a, b)
    s = Sketcher(K.std_plane("Top"), sk, "f1", lambda *x: None)
    s.selection = [line, circ]
    s.set_tool("perpendicular")
    assert any(k["type"] == "normal" for k in s.sk.constraints)
    assert _angle_to_radius(s.sk, line, c) < 1e-6
    s.sk.constraints.clear()
    s.selection = [circ, line]
    s.set_tool("normal")
    assert [k["type"] for k in s.sk.constraints] == ["normal"]
