"""Picking a sketch's line where a closed area is wanted (extrude, loft …) uses the area it bounds."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def test_line_picks_the_region_it_bounds():
    from freeshape.ui.dialogs import region_touching
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(60, 0), s.add_point(60, 40), s.add_point(0, 40)]
    lines = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    hole = s.add_circle(s.add_point(20, 20), 5)
    open_line = s.add_line(s.add_point(80, 0), s.add_point(90, 10))
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    area = lambda ri: K.face_info(sr.regions[ri])["area"]
    outer = region_touching(sr, lines[0])
    inner = region_touching(sr, hole)
    assert outer is not None and inner is not None and outer != inner
    assert abs(area(outer) - (60 * 40 - 3.141592653589793 * 25)) < 1e-3     # the plate around the hole
    assert abs(area(inner) - 3.141592653589793 * 25) < 1e-3                 # the hole's own disc
    assert region_touching(sr, open_line) is None                            # not part of a closed shape
