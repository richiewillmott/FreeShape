"""Curve pattern (copies along a path, turning with it) and Point pattern (a copy at each picked point)."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from test_20_stage1 import _extrude, _rect, _sketch  # noqa: E402


def _block(doc):
    """A 4 x 2 x 3 block whose middle-left sits on the point (30, 0): long side along X."""
    sid, sr = _sketch(doc, _rect(28, -1, 32, 1))
    _extrude(doc, sid, sr, depth=3, op="new")
    return next(iter(doc.state.parts))


def _path(doc, build):
    sid, sr = _sketch(doc, build)
    eid = next(e for e, v in sr.sketch.entities.items() if v["type"] in ("circle", "line", "arc"))
    return {"kind": "sketch_chain", "sketch": sid, "entity": eid}


def _centres(doc, skip=()):
    return sorted((tuple(np.round((np.asarray(K.bbox(p.shape)[0]) + K.bbox(p.shape)[1]) / 2, 4)), pid)
                  for pid, p in doc.state.parts.items() if pid not in skip)


def test_round_a_circle_copies_turn_with_it():
    doc = Document()
    seed = _block(doc)
    path = _path(doc, lambda s: s.add_circle(s.add_point(0, 0), 30))      # starts at (30, 0), going round
    f = doc.make_feature("curve_pattern")
    f["params"].update(parts=[seed], path=[path], count=4)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    assert len(doc.state.parts) == 4
    mids = [np.asarray(c) for c, _ in _centres(doc)]
    radii = sorted(round(float(np.hypot(m[0], m[1])), 3) for m in mids)
    assert radii == [30.0] * 4                                              # all on the circle
    for pid, part in doc.state.parts.items():
        lo, hi = K.bbox(part.shape)
        mid = (np.asarray(lo) + hi) / 2
        # turned with the circle: the long (4 mm) side stays across the path, along the radius
        radial = np.array([mid[0], mid[1], 0]) / 30
        size = np.asarray(hi) - lo
        assert abs(float(size[:2] @ np.abs(radial[:2])) - 4.0) < 1e-6


def test_keep_orientation_and_spacing_along_a_line():
    doc = Document()
    seed = _block(doc)
    path = _path(doc, lambda s: s.add_line(s.add_point(30, 0), s.add_point(30, 50)))
    f = doc.make_feature("curve_pattern")
    f["params"].update(parts=[seed], path=[path], count=3, spacing_mode="distance", spacing=10,
                       keep_orientation=True)
    doc.add_feature(f)
    assert not doc.errors
    ys = sorted(c[1] for c, _ in _centres(doc))
    assert ys == [0.0, 10.0, 20.0]
    f["params"]["spacing"] = 30                                             # 3 copies 30 apart: 60 > 50
    doc.replace_feature(f)
    assert doc.errors


def test_point_pattern_copies_to_each_point():
    doc = Document()
    seed = _block(doc)

    def pts(s):
        for x, y in ((0, 0), (60, 0), (0, 40)):
            s.add_point_entity(s.add_point(x, y))
    sid, sr = _sketch(doc, pts)
    locs = [{"kind": "sketch_point", "sketch": sid, "point": e["p"]}
            for e in sr.sketch.entities.values() if e["type"] == "point"]
    f = doc.make_feature("point_pattern")
    f["params"].update(parts=[seed], locations=locs, reference=locs[0])      # (0, 0) is where the seed "is"
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    xs = sorted((c[0], c[1]) for c, _ in _centres(doc))
    assert xs == [(30.0, 0.0), (30.0, 40.0), (90.0, 0.0)]
    f["params"]["reference"] = None                                         # no reference: the seed's middle
    doc.replace_feature(f)
    assert len(doc.state.parts) == 4 and not doc.errors


def test_curve_pattern_of_a_feature():
    doc = Document()
    sid, sr = _sketch(doc, _rect(-50, -50, 50, 50))
    _extrude(doc, sid, sr, depth=5, op="new")
    plate = next(iter(doc.state.parts))
    sid2, sr2 = _sketch(doc, lambda s: s.add_circle(s.add_point(30, 0), 2))
    hole = _extrude(doc, sid2, sr2, depth=5, op="remove")
    vol = K.volume(doc.state.parts[plate].shape)
    path = _path(doc, lambda s: s.add_circle(s.add_point(0, 0), 30))
    f = doc.make_feature("curve_pattern")
    f["params"].update(type="features", features=[hole["id"]], path=[path], count=6)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    assert abs(K.volume(doc.state.parts[plate].shape) - (vol - 5 * math.pi * 4 * 5)) < 1e-3


def test_dialogs_open_and_fill_in():
    from test_nav import _window
    app, w, vp = _window()
    seed = next(iter(w.doc.state.parts))
    sk = w.doc.features[0]
    for ftype in ("curve_pattern", "point_pattern"):
        w.start_feature(ftype)
        app.processEvents()
        d = w.dialog
        assert d is not None and d.ftype == ftype
        d.fields["parts"].items = [("part", seed)]
        if ftype == "curve_pattern":
            d.fields["path"].items = [("path", {"kind": "sketch_chain", "sketch": sk["id"],
                                                "entity": next(iter(w.doc.state.sketches[sk["id"]].sketch.entities))})]
        else:
            pts = list(w.doc.state.sketches[sk["id"]].sketch.points)
            d.fields["locations"].items = [("vertex", {"kind": "sketch_point", "sketch": sk["id"], "point": q})
                                           for q in pts[1:3]]
        d._fields_changed()
        p = d.feature["params"]
        assert p["parts"] == [seed]
        assert (p["path"] if ftype == "curve_pattern" else p["locations"])
        w.cancel_dialog()
