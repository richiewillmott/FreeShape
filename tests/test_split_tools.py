"""Split with many tools — planes, curved faces (extended to cut right through), sketch curves — each keeping
both sides or only the side its arrow points to. Older single-plane splits still work."""
import base64
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K, features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.features import ModelState  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

RIGHT, FRONT = {"kind": "std", "name": "Right"}, {"kind": "std", "name": "Front"}


def _cube():
    st = ModelState()
    pid = st.new_part(BRepPrimAPI_MakeBox(K._pnt((-10, -10, -10)), 20, 20, 20).Shape(), "F0").id
    return st, pid


def _split(st, pid, tools):
    F.run_split(st, {"id": "F9", "params": {"parts": [pid], "tools": tools}})
    return [p for p in st.parts.values() if p.id.split(".")[0] == pid]


def test_two_crossing_planes_make_four_or_keep_one_quarter():
    st, pid = _cube()
    pieces = _split(st, pid, [{"kind": "plane", "ref": RIGHT, "both": True},
                              {"kind": "plane", "ref": FRONT, "both": True}])
    assert sorted(round(K.volume(p.shape)) for p in pieces) == [2000] * 4
    for fr, ff in ((False, False), (True, False), (False, True), (True, True)):
        st, pid = _cube()
        (q,) = _split(st, pid, [{"kind": "plane", "ref": RIGHT, "both": False, "flip": fr},
                                {"kind": "plane", "ref": FRONT, "both": False, "flip": ff}])
        c = K.mass_props(q.shape)["centroid"]
        assert abs(K.volume(q.shape) - 2000) < 1e-6
        assert (c[0] > 0) != fr                                 # Right's normal is +X
    st, pid = _cube()                                           # keep both on one, one side on the other
    assert sorted(round(K.volume(p.shape)) for p in _split(
        st, pid, [{"kind": "plane", "ref": RIGHT, "both": True},
                  {"kind": "plane", "ref": FRONT, "both": False}])) == [2000, 2000]


def test_curved_face_is_extended_and_sides_are_exact():
    for flip, want in ((False, 8000 - math.pi * 36 * 20), (True, math.pi * 36 * 20)):
        st, pid = _cube()
        cyl = st.new_part(K.cylinder((0, 0, 30), (0, 0, 1), 6, 5), "F0")      # short and above: extended
        face = next(f for f in K.faces(cyl.shape) if "r" in K.face_info(f))
        (q,) = _split(st, pid, [{"kind": "face", "ref": F.make_ref(cyl.id, face, "face", cyl.shape),
                                 "both": False, "flip": flip}])
        assert abs(K.volume(q.shape) - want) < 1e-6


def test_old_single_plane_splits_still_work():
    st, pid = _cube()
    F.run_split(st, {"id": "F9", "params": {"parts": [pid], "plane": RIGHT, "keep": "back"}})
    (q,) = st.parts.values()
    assert K.mass_props(q.shape)["centroid"][0] < 0


def _doc_with_cube(tmp):
    doc = Document()
    path = os.path.join(tmp, "c.brep")
    K.export_brep([BRepPrimAPI_MakeBox(K._pnt((-10, -10, -10)), 20, 20, 20).Shape()], path)
    im = doc.make_feature("import")
    with open(path, "rb") as fh:
        im["params"].update(data=base64.b64encode(fh.read()).decode(), filename="c.brep")
    doc.add_feature(im)
    return doc


def test_split_along_a_sketch_spline_both_ways(tmp_path):
    doc = _doc_with_cube(str(tmp_path))
    s = Sketch()
    a, b, c, d = s.add_point(-20, -20), s.add_point(0, 5), s.add_point(20, -20), s.add_point(30, -20)
    sp = s.add_spline([a, b, c])
    s.add_line(c, d)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(sketch=s.to_dict(), plane={"kind": "std", "name": "Top"})
    doc.add_feature(f, 0)
    pid = next(iter(doc.state.parts))
    split = doc.make_feature("split")
    split["params"].update(parts=[pid], tools=[{"kind": "path", "both": False,
                                                 "ref": {"kind": "sketch_chain", "sketch": f["id"], "entity": sp}}])
    doc.add_feature(split)
    v1 = sum(K.volume(p.shape) for p in doc.state.parts.values())
    t = split["params"]["tools"][0]
    doc.replace_feature(dict(split, params=dict(split["params"], tools=[dict(t, flip=True)])))
    v2 = sum(K.volume(p.shape) for p in doc.state.parts.values())
    assert not doc.errors and 0 < v1 < 8000 and abs(v1 + v2 - 8000) < 1e-6


def test_split_dialog_rows_arrows_and_old_features(tmp_path):
    from test_nav import _window
    app, w, vp = _window()
    w._add_tab(_doc_with_cube(str(tmp_path)))
    app.processEvents()
    pid = next(iter(w.doc.state.parts))
    w.start_feature("split")
    d = w.dialog
    d.fields["parts"].items = [("part", pid)]
    d.fields["tools"].items = [("plane", {"kind": "std", "name": "Right"}), ("plane", {"kind": "std", "name": "Top"})]
    d._fields_changed()
    rows = d._split_box.layout()
    assert rows.count() == 2
    d._split_set(0, "both", False)
    d._split_set(1, "both", False)
    app.processEvents()
    arrows = d.manipulators()
    assert [m["label"] for m in arrows] == ["Keep 1", "Keep 2"]
    arrows[0]["on_drag"](-5)                                 # dragged across: keep the other side
    assert d.feature["params"]["tools"][0]["flip"] is True
    w.commit_dialog()
    (q,) = [p for p in w.doc.state.parts.values()]
    c = K.mass_props(q.shape)["centroid"]
    assert not w.doc.errors and c[0] < 0 and c[2] > 0          # the -X, upper quarter of the cube
    # an older split opens with its plane as the first tool
    old = w.doc.make_feature("split")
    old["params"] = {"parts": [], "plane": {"kind": "std", "name": "Front"}, "keep": "front"}
    from freeshape.ui.dialogs import params_to_fields
    assert params_to_fields("split", old["params"])["tools"] == [("plane", {"kind": "std", "name": "Front"})]
