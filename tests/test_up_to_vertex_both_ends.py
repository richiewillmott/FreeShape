"""Onshape's trick: Extrude up to a vertex, second end up to the same vertex stopping short by X → a part X thick
whose end is at the vertex."""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import features as F, kernel as K  # noqa: E402
from test_20_stage1 import _circ, _extrude, _sketch, _slab_doc  # noqa: E402

A = math.pi * 25


def _trick(offset2, end="up_to_vertex", **extra):
    doc, slab = _slab_doc()                                   # slab at z 30..35, its top corners at z 35
    sh = doc.state.parts[slab].shape
    top = max(K.vertex_points(sh), key=lambda q: q[2])
    vref = {"kind": "vertex", "ref": F.make_vertex_ref(slab, top, sh)}
    sid, sr = _sketch(doc, _circ(80, 20, 5))                  # off to the side, on Top (z 0)
    kw = dict(end=end, up_to=vref, second=True, end2="up_to_vertex", up_to2=vref, offset2=offset2, op="new")
    kw.update(extra)
    e = _extrude(doc, sid, sr, **kw)
    pid = next((p for p in doc.state.parts if p.startswith(e["id"])), None)
    return doc, (doc.state.parts[pid].shape if pid else None)


def test_same_vertex_both_ends_makes_a_slab_ending_at_the_vertex():
    doc, part = _trick(4)
    assert not doc.errors, doc.errors
    assert abs(K.volume(part) - A * 4) < 1e-3
    lo, hi = K.bbox(part)
    assert abs(lo[2] - 31) < 1e-6 and abs(hi[2] - 35) < 1e-6        # 4 mm thick, ending at the vertex


def test_the_first_end_can_be_blind_too():
    doc, part = _trick(10, end="blind", depth=35)
    assert not doc.errors and abs(K.volume(part) - A * 10) < 1e-3


def test_stopping_short_by_more_than_the_first_end_reaches_is_an_error():
    doc, part = _trick(4, end="blind", depth=20)                   # first end stops at 20, second starts at 31
    assert doc.errors


def test_a_second_vertex_on_its_own_side_still_works_as_before():
    doc, slab = _slab_doc()
    sh = doc.state.parts[slab].shape
    top = max(K.vertex_points(sh), key=lambda q: q[2])
    vref = {"kind": "vertex", "ref": F.make_vertex_ref(slab, top, sh)}
    sid, sr = _sketch(doc, _circ(80, 20, 5))
    e = _extrude(doc, sid, sr, end="blind", depth=5, flip=True, second=True, end2="up_to_vertex", up_to2=vref,
                 offset2=2, op="new")                               # first end down 5, second up to z 33
    pid = next(p for p in doc.state.parts if p.startswith(e["id"]))
    assert not doc.errors and abs(K.volume(doc.state.parts[pid].shape) - A * 38) < 1e-3


def test_unticking_the_second_end_drops_its_vertex_highlight():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    sk = doc.features[0]
    corner = next(iter(doc.state.sketches[sk["id"]].sketch.points))       # a corner of the sketch: in the model
    e = doc.features[1]                                                   # before this extrude
    e["params"].update(second=True, end2="up_to_vertex",
                       up_to2={"kind": "vertex", "ref": {"kind": "sketch_point", "sketch": sk["id"],
                                                         "point": corner}})
    w.edit_feature(e["id"])
    app.processEvents()
    d = w.dialog
    assert d.fields["upto2_vertex"].items
    assert "upto2_vertex" in {k for (k, _i), _g in d.overlays()}
    d.feature["params"]["second"] = False                            # untick Second end position
    d._sync_visibility()
    assert "upto2_vertex" not in {k for (k, _i), _g in d.overlays()}
    w.cancel_dialog()
