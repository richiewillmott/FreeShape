"""Version history: versions kept in the file, a readable "what changed" between versions, restoring, and the
History panel with added / removed material shown in 3D."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math  # noqa: E402

from freeshape import history as H, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from test_20_stage4 import _sketch, _extrude  # noqa: E402


def _block():
    doc = Document()
    sid, sr = _sketch(doc, [(0, 0), (40, 0), (40, 20), (0, 20)])
    e = _extrude(doc, sid, sr, depth=10, op="new")
    return doc, sid, e


def _vol(state):
    return sum(K.volume(p.shape) for p in state.parts.values())


def test_what_changed_reads_like_a_person_wrote_it():
    doc, sid, e = _block()
    v1 = H.create(doc, "Block", "first try")
    f = doc.feature(e["id"])
    doc.replace_feature(dict(f, name="Base", params=dict(f["params"], depth=15)))
    sid2, sr2 = _sketch(doc, circle=(20, 10, 4))
    _extrude(doc, sid2, sr2, end="through", op="remove")
    sk = doc.feature(sid)
    s = Sketch.from_dict(sk["params"]["sketch"])
    s.add_constraint("distance", next(k for k, x in s.entities.items() if x["type"] == "line"), value=40.0)
    s.add_line(s.add_point(0, 0), s.add_point(5, 5), construction=True)
    doc.replace_feature(dict(sk, params=dict(sk["params"], sketch=s.to_dict())))
    res = H.compare(doc, v1["id"])
    by = {c["name"]: c for c in res["features"]}
    assert by["Base"]["lines"] == ["Renamed from “Extrude 1”", "Depth: 10 mm → 15 mm"]
    assert "Added a distance dimension (40 mm)" in by["Sketch 1"]["lines"]
    assert "Added 1 construction line" in by["Sketch 1"]["lines"]
    assert by["Sketch 2"]["kind"] == "added" and by["Extrude 1"]["kind"] == "added"
    (name, a, b), = res["geometry"]["rows"]
    assert abs(a - 8000) < 1e-6 and abs(b - (12000 - math.pi * 16 * 15)) < 1e-6
    add, rem = res["geometry"]["added"], res["geometry"]["removed"]
    assert abs(sum(K.volume(x) for x in add) - (4000 - math.pi * 16 * 5)) < 1e-6        # the top 5 mm, less the hole
    assert abs(sum(K.volume(x) for x in rem) - math.pi * 16 * 10) < 1e-6                 # the hole in the old block
    html = H.to_html(res)
    assert "Depth: 10 mm → 15 mm" in html and "cm³" in html


def test_variables_suppression_and_reordering():
    doc, sid, e = _block()
    v = doc.make_feature("variable")
    v["params"].update(name="wall", expr="3")
    doc.add_feature(v, 0)
    v1 = H.create(doc)
    doc.replace_feature(dict(doc.feature(v["id"]), params={"name": "wall", "expr": "4"}))
    doc.set_suppressed(e["id"], True)
    doc.move_feature(v["id"], 2)
    res = H.compare(doc, v1["id"], geometry=False)
    lines = {c["name"]: c["lines"] for c in res["features"]}
    assert "#wall: 3 → 4" in lines["Variable 1"] and "Suppressed" in lines["Extrude 1"]
    assert any("Moved in the feature list" in x for x in lines.values() for x in x)


def test_versions_are_saved_in_the_file_and_restore_is_undoable(tmp_path):
    doc, sid, e = _block()
    H.create(doc, "Block")
    path = str(tmp_path / "h.fshape")
    doc.save(path)                                    # nothing changed since: no automatic version
    assert [v["name"] for v in doc.versions] == ["Block"]
    f = doc.feature(e["id"])
    doc.replace_feature(dict(f, params=dict(f["params"], depth=30)))
    doc.save(path)
    d2 = Document.load(path)
    assert [(v["name"], v["auto"]) for v in d2.versions] == [("Block", False), ("Saved", True)]
    assert abs(_vol(d2.state) - 24000) < 1e-6
    H.restore(d2, d2.versions[0]["id"])
    assert abs(_vol(d2.state) - 8000) < 1e-6
    d2.undo()
    assert abs(_vol(d2.state) - 24000) < 1e-6
    res = H.compare(d2, d2.versions[0]["id"], d2.versions[1]["id"], geometry=False)
    assert res["features"][0]["lines"] == ["Depth: 10 mm → 30 mm"]


def test_imported_geometry_is_stored_once(tmp_path, monkeypatch):
    import base64
    monkeypatch.setattr(H, "BLOB_MIN", 100)
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    doc = Document()
    f = doc.make_feature("import")
    path = str(tmp_path / "b.brep")
    K.export_brep([BRepPrimAPI_MakeBox(5, 5, 5).Shape()], path)
    with open(path, "rb") as fh:
        f["params"].update(data=base64.b64encode(fh.read()).decode(), filename="b.brep")
    doc.add_feature(f)
    assert not doc.errors
    for i in range(3):
        H.create(doc, f"v{i}")
        doc.rename_feature(f["id"], f"Import {i}")
    assert len(doc.version_blobs) == 1
    assert all(v["snapshot"]["features"][0]["params"]["data"].startswith("@blob:") for v in doc.versions)
    st, err = H.state_of(doc, H.expanded(doc, doc.versions[0]))
    assert not err and abs(_vol(st) - 125) < 1e-6


def test_history_panel_shows_changes_in_words_and_3d():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QMessageBox
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    H.create(doc, "Start")
    pid = next(iter(doc.state.parts))
    e = next(f for f in doc.features if f["type"] == "extrude")
    doc.replace_feature(dict(e, params=dict(e["params"], depth=25)))
    w.show_history()
    p = w.history_panel
    assert p is not None and p.list.count() == 2 and "changed since" in p.list.item(0).text()
    p.list.item(1).setSelected(True)
    p._compare()
    assert "Depth: 10 mm → 25 mm" in p.text.toPlainText()
    assert len(vp.ghosts) == 1 and vp.ghosts[0][1] == (0.18, 0.72, 0.30, 0.55)      # the added 15 mm, green
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    p.restore()
    assert abs(K.volume(doc.state.parts[pid].shape) - 40 * 30 * 10) < 1e-6
    assert [v["name"] for v in doc.versions] == ["Start", "Before restoring Start"]
    p.close_panel()
    assert w.history_panel is None and vp.ghosts == []
