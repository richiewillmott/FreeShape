"""Sketch hexagon tool: a regular hexagon (corners on a construction circle, equal sides), snapping level,
sized across flats, and usable as a region to extrude."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.sketcher import Sketcher, _hexagon_angle  # noqa: E402


class _Cam:
    scale = 1.0

    def to_screen(self, w):                      # 10 px per mm, origin at (400, 300), y up
        w = np.asarray(w, float)
        return np.stack([w[..., 0] * 10 + 400, 300 - w[..., 1] * 10, np.zeros(w.shape[:-1])], axis=-1)


class _ViewportStub:
    cam = _Cam()

    def ray_plane(self, x, y, plane):
        return ((x - 400) / 10, (300 - y) / 10, 0)


VP = _ViewportStub()


class _Ev:
    def modifiers(self):
        return Qt.NoModifier


def _draw_hexagon(cx, cy, x, y, offers=None):
    s = Sketcher(K.std_plane("Top"), Sketch(), "f1", lambda *a: None)
    s._offer_dims = (lambda vp, o: offers.extend(o)) if offers is not None else (lambda *a, **k: None)
    s.set_tool("hexagon")
    for px, py in ((cx, cy), (x, y)):
        s.mouse_move(VP, px, py, _Ev())
        s.mouse_press(VP, px, py, _Ev())
    return s


def _corners(sk):
    lines = [e for e in sk.entities.values() if e["type"] == "line" and not e.get("construction")]
    return lines, np.array([sk.points[e["p1"]] for e in lines])


def test_hexagon_is_regular_and_solves():
    s = _draw_hexagon(400, 300, 400 + 173, 300 - 100)          # corner at about 30°, 20 mm out: not snapped
    sk = s.sk
    lines, pts = _corners(sk)
    assert len(lines) == 6
    assert sk.solve() and sk.status.get("ok", True)
    lens = [np.linalg.norm(np.subtract(sk.points[e["p2"]], sk.points[e["p1"]])) for e in lines]
    assert max(lens) - min(lens) < 1e-6
    r = np.linalg.norm(pts, axis=1)
    assert np.ptp(r) < 1e-6 and abs(r[0] - 20.0) < 0.05
    circ = [e for e in sk.entities.values() if e["type"] == "circle"]
    assert len(circ) == 1 and circ[0]["construction"]


def test_hexagon_snaps_flat_top_and_point_top():
    assert _hexagon_angle(np.array([1.0, 0.03]))[1] == "flat_top"
    assert _hexagon_angle(np.array([0.03, 1.0]))[1] == "point_top"
    assert _hexagon_angle(np.array([1.0, 0.4]))[1] is None

    s = _draw_hexagon(400, 300, 600, 303)                       # nearly level: flat top and bottom
    sk = s.sk
    sk.solve()
    _lines, pts = _corners(sk)
    ys = np.round(pts[:, 1], 6)
    assert len(set(ys)) == 3 and ys.max() == -ys.min()          # level top / bottom rows of corners
    assert any(c["type"] == "horizontal" for c in sk.constraints)

    s = _draw_hexagon(400, 300, 402, 100)                       # nearly straight up: a corner on top
    sk = s.sk
    sk.solve()
    _lines, pts = _corners(sk)
    assert abs(pts[:, 0][np.argmax(pts[:, 1])]) < 1e-6
    assert any(c["type"] == "vertical" for c in sk.constraints)


def test_across_flats_dimension_sizes_it_and_extrudes():
    offers = []
    s = _draw_hexagon(400, 300, 500, 300, offers)               # 10 mm to a corner, flat top
    ctype, refs, value, _pos = offers[0]
    assert ctype == "distance" and abs(value - 10 * math.sqrt(3)) < 1e-6   # across flats
    sk = s.sk
    sk.add_constraint(ctype, *refs, value=13.0)                 # an M8 nut: 13 mm across flats
    sk.add_constraint("coincident", next(e["c"] for e in sk.entities.values() if e["type"] == "circle"), "origin")
    assert sk.solve()
    assert sk.status.get("dof", 0) == 0                         # fully defined: size, place and turn all fixed
    r = 13.0 / math.sqrt(3)
    doc = Document()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = sk.to_dict()
    doc.add_feature(f)
    regions = doc.state.sketches[f["id"]].regions
    assert len(regions) == 1
    area = K.face_info(regions[0])["area"]
    assert abs(area - 1.5 * math.sqrt(3) * r * r) < 1e-6
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, f["id"], regions[0])], depth=6.5)
    doc.add_feature(ex)
    part = next(iter(doc.state.parts.values()))
    assert abs(K.volume(part.shape) - area * 6.5) < 1e-6
    assert len(K.faces(part.shape)) == 8


def test_typing_in_the_popup_size_box_resizes_it():
    """The box that pops up after drawing: type 13, press Enter → 13 mm across flats."""
    from PySide6.QtWidgets import QApplication, QLineEdit
    app = QApplication.instance() or QApplication([])

    class _VP(_ViewportStub):
        def update(self):
            pass

        def setFocus(self):
            pass

    vp = _VP()
    s = Sketcher(K.std_plane("Top"), Sketch(), "f1", lambda *a: None)
    s._make_editor = lambda _vp, x, y, text: QLineEdit(text)
    s.set_tool("hexagon")
    for px, py in ((400, 300), (500, 300)):
        s.mouse_move(vp, px, py, _Ev())
        s.mouse_press(vp, px, py, _Ev())
    ed = s._editor
    assert ed is not None
    ed.setText("13")
    ed.returnPressed.emit()
    app.processEvents()
    dims = [c for c in s.sk.constraints if c["type"] == "distance"]
    assert len(dims) == 1 and dims[0]["value"] == 13.0
    _lines, pts = _corners(s.sk)
    r = np.linalg.norm(pts - pts.mean(0), axis=1)
    assert abs(r.mean() * math.sqrt(3) - 13.0) < 1e-6 and np.ptp(r) < 1e-6
