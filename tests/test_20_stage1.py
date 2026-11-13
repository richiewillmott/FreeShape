"""2.0 stage 1: sketch splines, ellipses and text; Up-to end conditions; feature patterns and mirror."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

TOP = {"kind": "std", "name": "Top"}


def _sketch(doc, build, plane=TOP):
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    f["params"]["plane"] = plane
    doc.add_feature(f)
    return f["id"], doc.state.sketches[f["id"]]


def _rect(x0, y0, x1, y1):
    def b(s):
        p = [s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)]
        for a, c in zip(p, p[1:] + p[:1]):
            s.add_line(a, c)
    return b


def _circ(x, y, r):
    return lambda s: s.add_circle(s.add_point(x, y), r)


def _extrude(doc, sid, sr, **kw):
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, sid, r) for r in sr.regions], **kw)
    doc.add_feature(e)
    return e


def _vol(shape):
    return K.volume(shape)


# ------------------------------------------------------------------ sketch curves
def test_closed_spline_is_a_region_and_its_points_take_constraints():
    s = Sketch()
    ps = [s.add_point(x, y) for x, y in [(0, 0), (20, -5), (35, 10), (20, 25), (0, 18)]]
    s.add_spline(ps, closed=True)
    s.add_constraint("coincident", ps[0], "origin")
    s.add_constraint("hdistance", ps[0], ps[2], value=40)
    assert s.solve()
    assert abs(s.P(ps[2])[0] - 40) < 1e-6
    doc = Document()
    _sid, sr = _sketch(doc, lambda sk: sk.__dict__.update(Sketch.from_dict(s.to_dict()).__dict__))
    assert len(sr.regions) == 1 and K.face_info(sr.regions[0])["area"] > 500


def test_ellipse_area_minor_radius_dimension_and_point_on():
    s = Sketch()
    c, a = s.add_point(5, 5), s.add_point(25, 5)
    e = s.add_ellipse(c, a, 8)
    doc = Document()
    _sid, sr = _sketch(doc, lambda sk: sk.__dict__.update(Sketch.from_dict(s.to_dict()).__dict__))
    assert abs(K.face_info(sr.regions[0])["area"] - math.pi * 20 * 8) < 1e-3
    s.add_constraint("radius", e, value=6)
    q = s.add_point(5, 12)
    s.add_constraint("point_on", q, e)
    assert s.solve()
    assert abs(s.entities[e]["r"] - 6) < 1e-6
    ctr, ax, r = s.ellipse_axes(e)                               # (the solver may nudge the centre too)
    u = ax / np.linalg.norm(ax)
    w = s.P(q) - ctr
    x, y = w @ u, w[0] * -u[1] + w[1] * u[0]
    assert abs((x / np.linalg.norm(ax)) ** 2 + (y / r) ** 2 - 1) < 1e-6      # the point sits on the ellipse


def test_text_regions_are_letters_without_their_holes():
    def build(s):
        s.add_text(s.add_point(2, 3), "Hello 80", size=8, angle=10)
        _rect(-5, -5, 60, 30)(s)
    doc = Document()
    sid, sr = _sketch(doc, build, {"kind": "std", "name": "Front"})
    assert len(sr.regions) == 8              # H e l l o 8 0 + the plate around them (no letter-hole discs)
    plate = max(sr.regions, key=lambda f: K.face_info(f)["area"])
    letters = [r for r in sr.regions if r is not plate]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, sid, r) for r in letters], depth=1, op="new")
    doc.add_feature(e)
    assert not doc.errors and len(doc.state.parts) == 7


# ------------------------------------------------------------------ up to
def _slab_doc():
    doc = Document()
    sid, sr = _sketch(doc, _rect(0, 0, 40, 40))
    _extrude(doc, sid, sr, depth=5, op="new")
    mv = doc.make_feature("move")
    mv["params"].update(parts=list(doc.state.parts), dz=30)       # slab now at z 30..35
    doc.add_feature(mv)
    return doc, next(iter(doc.state.parts))


def _new_part_volume(doc, e):
    pid = next(p for p in doc.state.parts if p.startswith(e["id"]))
    return _vol(doc.state.parts[pid].shape)


def test_up_to_next_face_plane_vertex_and_part():
    A = math.pi * 25
    doc, slab = _slab_doc()
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_next", op="new")
    assert not doc.errors and abs(_new_part_volume(doc, e) - A * 30) < 1e-3

    doc, slab = _slab_doc()
    sh = doc.state.parts[slab].shape
    bottom = min(K.faces(sh), key=lambda f: K.interior_point(f)[2])
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_face", up_to={"kind": "face", "ref": F.make_ref(slab, bottom, "face", sh)},
                 offset=4, op="new")
    assert not doc.errors and abs(_new_part_volume(doc, e) - A * 26) < 1e-3          # stops 4 mm short

    doc, slab = _slab_doc()
    sh = doc.state.parts[slab].shape
    top = max(K.vertex_points(sh), key=lambda q: q[2])
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_vertex", up_to={"kind": "vertex", "ref": F.make_vertex_ref(slab, top, sh)},
                 op="new")
    assert abs(_new_part_volume(doc, e) - A * 35) < 1e-3

    doc, slab = _slab_doc()
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_part", up_to={"kind": "part", "id": slab}, op="new")
    assert abs(_new_part_volume(doc, e) - A * 30) < 1e-3


def test_up_to_curved_face_and_revolve_up_to_plane():
    doc = Document()
    sid, sr = _sketch(doc, _circ(0, 30, 6), {"kind": "std", "name": "Right"})
    _extrude(doc, sid, sr, end="symmetric", depth=60, op="new")
    cyl = next(iter(doc.state.parts.values()))
    cf = max(K.faces(cyl.shape), key=lambda f: K.face_info(f)["area"])
    sid, sr = _sketch(doc, _circ(0, 0, 3))
    e = _extrude(doc, sid, sr, end="up_to_face", up_to={"kind": "face", "ref": F.make_ref(cyl.id, cf, "face", cyl.shape)},
                 op="new")
    lo, hi = K.bbox(doc.state.parts[next(p for p in doc.state.parts if p.startswith(e["id"]))].shape)
    assert 24.0 < hi[2] < 24.9                                    # meets the round underside of the bar

    doc = Document()
    sid, sr = _sketch(doc, _rect(10, 0, 20, 10), {"kind": "std", "name": "Front"})
    r = doc.make_feature("revolve")
    r["params"].update(regions=[F.region_ref(sr, sid, x) for x in sr.regions], axis={"kind": "std", "name": "Z"},
                       end="up_to_face", up_to={"kind": "plane", "ref": {"kind": "std", "name": "Right"}}, op="new")
    doc.add_feature(r)
    assert abs(_vol(next(iter(doc.state.parts.values())).shape) - math.pi * (400 - 100) * 10 / 4) < 1e-2


def test_up_to_that_never_arrives_is_a_clear_error():
    doc, slab = _slab_doc()
    sid, sr = _sketch(doc, _circ(20, 20, 5))
    e = _extrude(doc, sid, sr, end="up_to_part", up_to={"kind": "part", "id": slab}, flip=True, op="new")
    assert "never reaches" in doc.errors.get(e["id"], "")


# ------------------------------------------------------------------ feature patterns
def _plate_with_hole(x=20, y=0):
    doc = Document()
    sid, sr = _sketch(doc, _rect(-30, -30, 30, 30))
    _extrude(doc, sid, sr, depth=5, op="new")
    sid, sr = _sketch(doc, _circ(x, y, 3))
    h = _extrude(doc, sid, sr, end="through", op="remove")
    return doc, next(iter(doc.state.parts)), h


def test_circular_and_linear_feature_patterns_and_mirror():
    P, H = 60 * 60 * 5, math.pi * 9 * 5
    doc, pid, h = _plate_with_hole()
    cp = doc.make_feature("circular_pattern")
    cp["params"].update(type="features", features=[h["id"]], axis={"kind": "std", "name": "Z"}, count=4, angle=360)
    doc.add_feature(cp)
    assert not doc.errors and abs(_vol(doc.state.parts[pid].shape) - (P - 4 * H)) < 1e-3

    doc, pid, h = _plate_with_hole(-20, -20)
    lp = doc.make_feature("linear_pattern")
    lp["params"].update(type="features", features=[h["id"]], direction={"kind": "std", "name": "X"}, spacing=10,
                        count=5)
    doc.add_feature(lp)
    assert not doc.errors and abs(_vol(doc.state.parts[pid].shape) - (P - 5 * H)) < 1e-3

    doc, pid, h = _plate_with_hole(20, 10)
    mf = doc.make_feature("mirror")
    mf["params"].update(type="features", features=[h["id"]], plane={"kind": "std", "name": "Right"})
    doc.add_feature(mf)
    assert not doc.errors and abs(_vol(doc.state.parts[pid].shape) - (P - 2 * H)) < 1e-3


def test_feature_pattern_follows_edits_to_the_original():
    doc, pid, h = _plate_with_hole()
    cp = doc.make_feature("circular_pattern")
    cp["params"].update(type="features", features=[h["id"]], axis={"kind": "std", "name": "Z"}, count=6, angle=360)
    doc.add_feature(cp)
    sk = doc.features[2]
    s = Sketch.from_dict(sk["params"]["sketch"])
    cid = next(k for k, e in s.entities.items() if e["type"] == "circle")
    s.entities[cid]["r"] = 2.0
    doc.replace_feature({**sk, "params": {**sk["params"], "sketch": s.to_dict()}})
    assert abs(_vol(doc.state.parts[pid].shape) - (60 * 60 * 5 - 6 * math.pi * 4 * 5)) < 1e-3


# ------------------------------------------------------------------ fixes
def test_horizontal_replaces_vertical_and_turns_the_line():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from freeshape.ui.sketcher import Sketcher
    sk = Sketch()
    a, b = sk.add_point(10, -20), sk.add_point(10, 0)
    l1 = sk.add_line(a, b)
    c, d = sk.add_point(30, 10), sk.add_point(30, 30)
    l2 = sk.add_line(c, d)
    S = Sketcher(K.std_plane("Top"), sk, "F1", lambda *a: None)
    S._apply_constraint("vertical", [l1])
    S._apply_constraint("vertical", [l2])
    S._apply_constraint("horizontal", [l1, l2])               # both lines selected, then H
    for line in (l1, l2):
        e = sk.entities[line]
        assert [k["type"] for k in sk.constraints if k["refs"] == [line]] == ["horizontal"]
        assert abs(np.linalg.norm(sk.P(e["p2"]) - sk.P(e["p1"])) - 20) < 1e-6      # turned, not squashed
    S._apply_constraint("horizontal", [l1])
    assert sum(k["type"] == "horizontal" for k in sk.constraints) == 2           # no duplicate
