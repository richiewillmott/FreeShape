"""QA-16: a constraint that can't be met somewhere in a sketch must not freeze the rest of it — drags and dimension
edits are judged on whether they make something fail that didn't before."""
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


def _screen(uv):
    return 400 + uv[0] * 10, 300 - uv[1] * 10


def _sketch_with_a_clash():
    """Line A (y = 0) carries two lengths that can't both hold; line B (y = 10) is free and unrelated."""
    sk = Sketch()
    a1, a2 = sk.add_point(0, 0), sk.add_point(10, 0)
    la = sk.add_line(a1, a2)
    sk.add_constraint("horizontal", la)
    sk.add_constraint("fix", a1, value=[0, 0])
    k10 = sk.add_constraint("distance", la, value=10.0)
    k20 = sk.add_constraint("distance", la, value=20.0)     # the clash
    b1, b2 = sk.add_point(0, 10), sk.add_point(10, 10)
    lb = sk.add_line(b1, b2)
    sk.solve()
    s = Sketcher(K.std_plane("Top"), sk, "f1", lambda *a: None)
    s._offer_dims = lambda *a, **k: None
    s._flash = lambda *a, **k: None
    return s, dict(a1=a1, a2=a2, la=la, k10=k10["id"], k20=k20["id"], b1=b1, b2=b2, lb=lb)


def _drag(s, frm, to):
    s.set_tool("select")
    s.mouse_press(VP, *_screen(frm), _Ev())
    s.mouse_drag(VP, *_screen(to), _Ev())
    s.mouse_release(VP, *_screen(to), _Ev(), True)


def test_a_clash_elsewhere_doesnt_stop_a_drag():
    s, ids = _sketch_with_a_clash()
    a_before = [list(s.sk.points[ids["a1"]]), list(s.sk.points[ids["a2"]])]
    _drag(s, (10, 10), (14, 13))
    assert np.allclose(s.sk.points[ids["b2"]], [14, 13], atol=1e-3)          # it moved where it was dragged
    assert [list(s.sk.points[ids["a1"]]), list(s.sk.points[ids["a2"]])] == a_before   # the clash didn't move
    assert {ids["k10"], ids["k20"]} & s.sk.status["bad"]                    # and is still shown as failing


def test_a_new_dimension_can_be_added_next_to_a_clash():
    s, ids = _sketch_with_a_clash()
    n = len(s.sk.constraints)
    s._create_dimension(ids["lb"], None, (5, 12))
    assert len(s.sk.constraints) == n + 1                                    # it was kept
    c = s.sk.constraints[-1]
    assert c["type"] == "distance" and c["refs"] == [ids["lb"]]


def test_a_dimension_that_would_clash_is_still_refused():
    s, ids = _sketch_with_a_clash()
    s._create_dimension(ids["lb"], None, (5, 12))
    n = len(s.sk.constraints)
    lb_len = s.sk.constraints[-1]
    s.sk.add_constraint("fix", ids["b1"], value=[0, 10])
    s.sk.add_constraint("fix", ids["b2"], value=[10, 10])
    s.sk.solve(ignore=s._failing_now())
    before = s._failing_now()
    lb_len["value"] = 15.0                                                   # what the value box would do
    assert not s._solve_without_new_failures(before, keep={lb_len["id"]})
    assert n + 2 == len(s.sk.constraints)


def test_editing_the_clashing_dimension_to_a_value_that_fits_clears_it():
    s, ids = _sketch_with_a_clash()
    before = s._failing_now()
    k20 = next(c for c in s.sk.constraints if c["id"] == ids["k20"])
    k20["value"] = 10.0
    assert s._solve_without_new_failures(before, keep={ids["k20"]})
    assert not s.sk.status["bad"]
