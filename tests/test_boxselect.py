"""Box selection: left-to-right selects only what is fully enclosed, right-to-left also what is touched."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape import features as F  # noqa: E402


def _viewport_with_box():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.viewport import Viewport
    from freeshape.ui.camera import basis
    doc = Document()
    s = Sketch()
    p = [s.add_point(-10, -10), s.add_point(10, -10), s.add_point(10, 10), s.add_point(-10, 10)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=10, op="new")
    doc.add_feature(e)
    part = next(iter(doc.state.parts.values()))
    vp = Viewport()
    mesh = K.tessellate(part.shape)
    vp.parts = [(part.id, mesh, None)]
    vp._part_boxes = {part.id: (mesh.verts.min(0), mesh.verts.max(0))}
    vp.cam.set_state((np.array([0.0, 0, 5]), basis((0, 0, 1), (0, 1, 0)), 0.1))   # looking straight down
    return vp


def test_window_vs_crossing():
    vp = _viewport_with_box()
    cx, cy = vp.cam.w / 2, vp.cam.h / 2
    half = 10 / vp.cam.scale                  # the 20 mm square is 200 px wide at this zoom
    vp.select_filter = None
    vp.user_filter = {"face"}
    # a box around the whole part encloses every face
    big = (cx - half - 20, cy - half - 20, cx + half + 20, cy + half + 20)
    assert len(vp._items_in_rect(big, False)) == 6
    # a small box in the middle touches the top and bottom faces but encloses none
    small = (cx - 10, cy - 10, cx + 10, cy + 10)
    assert vp._items_in_rect(small, False) == []
    assert {pk.kind for pk in vp._items_in_rect(small, True)} == {"face"}
    assert len(vp._items_in_rect(small, True)) == 2
    # edges only: the small box in the middle touches no edge, a box over one side wall edge pair does
    vp.user_filter = {"edge"}
    assert vp._items_in_rect(small, True) == []
    assert len(vp._items_in_rect(big, False)) == 12
    left = (cx - half - 5, cy - 20, cx - half + 5, cy + 20)       # crosses the left edges only
    assert vp._items_in_rect(left, False) == []
    assert len(vp._items_in_rect(left, True)) >= 1
    # whole-part selection when only solids may be picked
    vp.user_filter = {"part"}
    assert len(vp._items_in_rect(big, False)) == 1
    assert vp._items_in_rect(small, False) == []
    assert len(vp._items_in_rect(small, True)) == 1
    # filtered-out kinds are never selected
    vp.user_filter = set()
    assert vp._items_in_rect(big, True) == []


def test_vertices_are_selectable():
    vp = _viewport_with_box()
    cx, cy = vp.cam.w / 2, vp.cam.h / 2
    half = 10 / vp.cam.scale
    vp.user_filter = {"vertex"}
    pk = vp.pick(cx - half, cy - half)                    # a corner of the square seen from above
    assert pk is not None and pk.kind == "vertex"
    assert vp.pick(cx, cy) is None                         # nothing else is selectable in the middle
    big = (cx - half - 20, cy - half - 20, cx + half + 20, cy + half + 20)
    assert len(vp._items_in_rect(big, False)) == 8
    corner = (cx - half - 10, cy - half - 10, cx - half + 10, cy - half + 10)
    got = vp._items_in_rect(corner, False)
    assert len(got) == 2 and all(p.kind == "vertex" for p in got)      # top and bottom corner, in line from above
    vp.user_filter = {"edge"}
    assert vp.pick(cx - half, cy - half) is None or vp.pick(cx - half, cy - half).kind == "edge"


def test_space_deselects_all_and_arrows_turn_the_view_15_degrees():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from freeshape.ui.camera import STANDARD_VIEWS, basis
    from freeshape.ui.viewport import Pick
    vp = _viewport_with_box()
    vp.set_selection([Pick("face", ("p", 0)), Pick("edge", ("p", 1))])
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert vp.selection == []
    back, up = STANDARD_VIEWS["Right"]
    vp.cam.set_state((np.zeros(3), basis(back, up), 0.1))
    right = QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier)
    for _ in range(3):
        vp.keyPressEvent(right)                      # three taps: between Right and Front
    b = vp._anim[1][1][2]
    assert np.allclose(b, [np.cos(np.pi / 4), -np.sin(np.pi / 4), 0], atol=1e-6)
    for _ in range(3):
        vp.keyPressEvent(right)                      # six taps: Front
    assert np.allclose(vp._anim[1][1][2], [0, -1, 0], atol=1e-6)
    vp._anim = None
    vp.cam.set_state((np.zeros(3), basis(back, up), 0.1))
    vp.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Up, Qt.NoModifier))     # up/down tilt by 15 degrees too
    assert abs(float(vp._anim[1][1][2] @ np.array(back, float)) - np.cos(np.radians(15))) < 1e-6
