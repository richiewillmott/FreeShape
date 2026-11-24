"""Transform with Onshape's types (by line, distance, XYZ, mate connectors, rotate, copy in place, scale, point to
point) and Part Studio mate connectors (points on a face to choose from, flip, turn, offset)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402

from freeshape import features as F, kernel as K, mate_connectors as MC  # noqa: E402
from freeshape.document import Document  # noqa: E402
from test_20_stage1 import _circ, _extrude, _rect, _sketch  # noqa: E402


def _box(doc, x0=0, y0=0, x1=20, y1=10, h=5):
    sid, sr = _sketch(doc, _rect(x0, y0, x1, y1))
    e = _extrude(doc, sid, sr, depth=h, op="new")
    return next(p for p in doc.state.parts if p.startswith(e["id"])), sid


def _bbox(doc, pid):
    lo, hi = K.bbox(doc.state.parts[pid].shape)
    return np.round(np.asarray(lo), 6), np.round(np.asarray(hi), 6)


def _move(doc, pid, **kw):
    f = doc.make_feature("move")
    f["params"].update(parts=[pid], **kw)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f


def test_translate_by_line_distance_xyz_and_points():
    doc = Document()
    pid, sid = _box(doc)
    sk = doc.state.sketches[sid].sketch
    line = next(e for e, v in sk.entities.items() if v["type"] == "line"
                and abs(sk.points[v["p1"]][1]) < 1e-9 and abs(sk.points[v["p2"]][1]) < 1e-9)   # the 20 mm bottom side
    f = _move(doc, pid, type="line", axis={"kind": "sketch_line", "sketch": sid, "line": line})
    lo, _hi = _bbox(doc, pid)
    assert abs(abs(lo[0]) - 20) < 1e-6                                  # moved by the line's length
    doc.delete_features([f["id"]])
    f = _move(doc, pid, type="distance", axis={"kind": "std", "name": "Y"}, distance=7, flip=True)
    assert _bbox(doc, pid)[0][1] == -7
    doc.delete_features([f["id"]])
    _move(doc, pid, type="xyz", dx=1, dy=2, dz=3)
    assert list(_bbox(doc, pid)[0]) == [1, 2, 3]
    at = lambda x, y: next(q for q, v in sk.points.items() if np.allclose(v, (x, y)) and q != "origin")
    a = {"kind": "sketch_point", "sketch": sid, "point": at(0, 0)}
    b = {"kind": "sketch_point", "sketch": sid, "point": at(20, 10)}
    _move(doc, pid, type="points", from_point=a, to_point=b)
    lo, _hi = _bbox(doc, pid)
    assert np.allclose(lo, [1 + 20, 2 + 10, 3])


def test_rotate_copy_in_place_and_scale():
    doc = Document()
    pid, _sid = _box(doc)
    _move(doc, pid, type="rotate", axis={"kind": "std", "name": "Z"}, rotate=90)
    lo, hi = _bbox(doc, pid)
    assert np.allclose([lo[0], hi[0], lo[1], hi[1]], [-10, 0, 0, 20])
    _move(doc, pid, type="copy")
    assert len(doc.state.parts) == 2
    _move(doc, pid, type="scale", scale=2)
    lo, hi = _bbox(doc, pid)
    assert np.allclose(hi - lo, [20, 40, 10])
    assert abs(K.volume(doc.state.parts[pid].shape) - 1000 * 8) < 1e-6


def test_mate_connector_points_on_a_flat_face_and_shift_through_them():
    doc = Document()
    pid, _sid = _box(doc)
    shape = doc.state.parts[pid].shape
    top = max(K.faces(shape), key=lambda f_: K.face_info(f_)["c"][2])
    ref = F.make_ref(pid, top, "face", shape)
    cands = MC.candidates(doc.state, {"kind": "face", "ref": ref})
    names = [c["name"] for c in cands]
    assert names[0] == "Middle of the face" and sum(n.startswith("Corner") for n in names) == 4
    assert sum(n.startswith("Middle of edge") for n in names) == 4
    assert np.allclose(cands[0]["o"], [10, 5, 5]) and np.allclose(cands[0]["z"], [0, 0, 1])
    mc = doc.make_feature("mate_connector")
    mc["params"].update(on={"kind": "face", "ref": ref}, point=0)
    doc.add_feature(mc)
    fr = doc.state.connectors[mc["id"]]
    assert np.allclose(fr["o"], [10, 5, 5])
    corner = next(i for i, n in enumerate(names) if n.startswith("Corner"))
    mc["params"].update(point=corner, flip=True, dz=2)
    doc.replace_feature(mc)
    fr = doc.state.connectors[mc["id"]]
    assert np.allclose(fr["z"], [0, 0, -1]) and abs(fr["o"][2] - 3) < 1e-9   # flipped, then 2 along its Z
    assert np.allclose(np.cross(fr["z"], fr["x"]), fr["y"])                    # still a proper frame


def test_mate_connector_on_a_round_face_offers_its_axis_ends():
    doc = Document()
    sid, sr = _sketch(doc, _circ(0, 0, 5))
    e = _extrude(doc, sid, sr, depth=30, op="new")
    pid = next(iter(doc.state.parts))
    shape = doc.state.parts[pid].shape
    side = next(f_ for f_ in K.faces(shape) if "n" not in K.face_info(f_))
    cands = MC.candidates(doc.state, {"kind": "face", "ref": F.make_ref(pid, side, "face", shape)})
    os_ = sorted(round(float(c["o"][2]), 6) for c in cands)
    assert os_ == [0.0, 15.0, 30.0]                                       # middle and both end centres
    for c in cands:
        assert abs(c["o"][0]) < 1e-6 and abs(c["o"][1]) < 1e-6           # all on the axis
    bottom = next(c for c in cands if abs(c["o"][2]) < 1e-6)
    assert np.allclose(bottom["z"], [0, 0, -1])                           # pointing out of that end


def test_transform_by_mate_connectors_puts_one_onto_the_other():
    doc = Document()
    pid, _sid = _box(doc)
    other, _s2 = _box(doc, 50, 0, 60, 10, 8)
    s1, s2 = doc.state.parts[pid].shape, doc.state.parts[other].shape
    bottom = min(K.faces(s1), key=lambda f_: K.face_info(f_)["c"][2])
    top2 = max(K.faces(s2), key=lambda f_: K.face_info(f_)["c"][2])
    a = doc.make_feature("mate_connector")
    a["params"]["on"] = {"kind": "face", "ref": F.make_ref(pid, bottom, "face", s1)}
    doc.add_feature(a)
    b = doc.make_feature("mate_connector")
    b["params"]["on"] = {"kind": "face", "ref": F.make_ref(other, top2, "face", s2)}
    doc.add_feature(b)
    _move(doc, pid, type="mates", from_mc=a["id"], to_mc=b["id"], flip=True)   # bottom face onto the top face
    lo, hi = _bbox(doc, pid)
    assert abs(lo[2] - 8) < 1e-6 and abs(hi[2] - 13) < 1e-6              # sitting on the other box
    assert np.allclose([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2], [55, 5])


def test_dialogs_shift_steps_through_points_and_triads_show():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    pid = next(iter(w.doc.state.parts))
    shape = w.doc.state.parts[pid].shape
    top = max(K.faces(shape), key=lambda f_: K.face_info(f_)["c"][2])
    w.start_feature("mate_connector")
    app.processEvents()
    d = w.dialog
    d.fields["on_sel"].items = [("face", F.make_ref(pid, top, "face", shape))]
    d._fields_changed()
    assert d.feature["params"]["point"] == 0
    QApplication.sendEvent(vp, QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.ShiftModifier))
    assert d.feature["params"]["point"] == 1
    app.processEvents()
    QApplication.sendEvent(vp, QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.ShiftModifier))
    assert d.feature["params"]["point"] == 2
    assert any(k[0] == "_mc_point" for (k, _g) in d.overlays())
    w.commit_dialog()
    app.processEvents()
    assert len(w.doc.state.connectors) == 1, list(w.doc.state.connectors)
    assert len(vp.triads) == 1, vp.triads
    w.start_feature("move")
    app.processEvents()
    t = w.dialog
    assert t.widgets["from_mc"].count() == 2                              # "— choose —" and the connector
    w.cancel_dialog()


def test_new_features_survive_a_python_script_round_trip(tmp_path):
    from test_script_dump import _roundtrip, _sig
    doc = Document()
    pid, sid = _box(doc)
    shape = doc.state.parts[pid].shape
    top = max(K.faces(shape), key=lambda f_: K.face_info(f_)["c"][2])
    mc = doc.make_feature("mate_connector")
    mc["params"].update(on={"kind": "face", "ref": F.make_ref(pid, top, "face", shape)}, point=2, rotate=30)
    doc.add_feature(mc)
    _move(doc, pid, type="copy")
    path_sid, psr = _sketch(doc, lambda s: s.add_line(s.add_point(0, 20), s.add_point(60, 20)))
    cp = doc.make_feature("curve_pattern")
    cp["params"].update(parts=[pid], path=[{"kind": "sketch_chain", "sketch": path_sid,
                                            "entity": next(iter(psr.sketch.entities))}], count=3)
    doc.add_feature(cp)
    assert not doc.errors, doc.errors
    _text, ws = _roundtrip([doc], tmp_path)
    again = ws.documents()[0]
    assert _sig(again) == _sig(doc) and set(again.state.connectors) == set(doc.state.connectors)
