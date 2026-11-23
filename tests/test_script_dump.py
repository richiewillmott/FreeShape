"""Export as Python script / Run Python script: a project written out as a script builds the same geometry again,
its typed variables come first as plain values that drive the model, and the three parts (CAD model, assembly,
drawing) link to each other inside the script."""
import os
import re
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F, kernel as K, script as S  # noqa: E402
from freeshape.assembly import AssemblyDoc  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.drawing import DrawingDoc  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def _sig(doc):
    return sorted((round(K.volume(p.shape), 4), len(K.faces(p.shape)), len(K.edges(p.shape)))
                  for p in doc.state.parts.values())


def _roundtrip(docs, tmp_path, name="m.py"):
    text = S.dump(docs)
    compile(text, name, "exec")                              # it is valid Python
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return text, S.run_file(str(path))


def test_example_bracket_builds_the_same(tmp_path):
    doc = Document.load(os.path.join(EX, "bracket.fshape"))
    text, ws = _roundtrip([doc], tmp_path)
    (again,) = ws.documents()
    assert _sig(again) == _sig(doc) and not ws.errors()
    assert again.name == doc.name and again.uid == doc.uid
    assert "sk_F1.entity('l7', 'line'" in text                # sketches are written one curve per line
    assert again.hidden_features == doc.hidden_features


def _plate():
    doc = Document()
    doc.name = "Plate"
    for name, expr, mtype in (("w", "40", "fixed_length"), ("h", "10", "fixed_length"), ("n", "3", "fixed_number")):
        v = doc.make_feature("measure_value")
        v["params"].update(name=name, mtype=mtype, expr=expr)
        doc.add_feature(v)
    old = doc.make_feature("variable")
    old["params"].update(name="gap", expr="2.5")
    doc.add_feature(old)
    derived = doc.make_feature("measure_value")                    # uses another variable: stays in the model
    derived["params"].update(name="half", mtype="fixed_length", expr="#w / 2")
    doc.add_feature(derived)
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 30), s.add_point(0, 30)]
    ls = [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
    for t, line in zip(("horizontal", "vertical", "horizontal", "vertical"), ls):
        s.add_constraint(t, line)
    s.add_constraint("distance", ls[0], value=40.0)["expr"] = "#w"
    s.add_constraint("distance", ls[1], value=30.0)
    s.add_constraint("coincident", p[0], "origin")
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    sr = doc.state.sketches[sk["id"]]
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(sr, sk["id"], sr.regions[0])], depth="#h", op="new")
    doc.add_feature(ex)
    assert not doc.errors
    return doc


def test_typed_variables_come_first_and_drive_the_model(tmp_path):
    doc = _plate()
    text, ws = _roundtrip([doc], tmp_path)
    top = text[:text.index("def build")]
    assert re.search(r"(?m)^w = 40 .*# #w \(mm\)", top) and re.search(r"(?m)^h = 10 ", top)
    assert re.search(r"(?m)^n = 3 ", top) and re.search(r"(?m)^gap = 2\.5 ", top)
    assert "half" not in top                                       # an expression of other variables isn't lifted
    assert "'expr': str(w)" in text
    assert _sig(ws.documents()[0]) == _sig(doc)
    path = tmp_path / "changed.py"
    path.write_text(re.sub(r"(?m)^w = 40", "w = 60", text).replace("\nh = 10", "\nh = 5"), encoding="utf-8")
    ws2 = S.run_file(str(path))
    (part,) = ws2.documents()[0].state.parts.values()
    assert abs(K.volume(part.shape) - 60 * 30 * 5) < 1e-6 and not ws2.errors()


def _hinge():
    studio = Document.load(os.path.join(EX, "hinge_parts.fshape"))

    def res(src):
        return studio.state_for(src.get("config")).parts[src["part"]]
    asm = AssemblyDoc.load(os.path.join(EX, "hinge.fasm"), resolver=res)
    dwg = DrawingDoc()
    dwg.name = "Hinge drawing"
    dwg.source = {"kind": "studio", "uid": studio.uid, "part": "*", "studio_name": studio.name}
    dwg.resolver = lambda src: K.compound([p.shape for p in studio.state.parts.values()])
    dwg.refresh(notify=False)
    dwg.add_view((0, -1, 0), (0, 0, 1), (100.0, 100.0), checkpoint=False)
    return studio, asm, dwg


def test_model_assembly_and_drawing_link_inside_the_script(tmp_path):
    studio, asm, dwg = _hinge()
    text, ws = _roundtrip([studio, asm, dwg], tmp_path)
    for heading in ("1. CAD model", "2. Assembly", "3. Drawing"):
        assert heading in text
    assert "'uid': ps1.uid" in text and "'path'" not in text.split("2. Assembly")[1].split("3. Drawing")[0]
    s2, a2, d2 = ws.documents()
    assert _sig(s2) == _sig(studio) and not ws.errors()
    place = {i["name"]: np.round(np.array(a2.display_T(i["id"])), 6).tolist() for i in a2.instances}
    assert place == {i["name"]: np.round(np.array(asm.display_T(i["id"])), 6).tolist() for i in asm.instances}
    assert len(a2.parts) == len(asm.parts) and not a2.errors
    assert d2.shape is not None and d2.error is None and len(d2.views) == 1


def test_saving_writes_the_three_files(tmp_path):
    studio, asm, dwg = _hinge()
    _text, ws = _roundtrip([studio, asm, dwg], tmp_path)
    paths = ws.save(str(tmp_path / "out"))
    assert [os.path.splitext(p)[1] for p in paths] == [".fshape", ".fasm", ".fdwg"]
    again = Document.load(paths[0])
    assert _sig(again) == _sig(studio)


def test_a_part_studio_not_in_the_script_keeps_a_copy(tmp_path):
    studio, asm, _dwg = _hinge()
    text, ws = _roundtrip([asm], tmp_path)                    # the assembly alone
    assert "DATA = {" in text and "_brep=DATA[" in text
    (a2,) = ws.documents()
    assert len(a2.parts) == len(asm.parts)


def test_new_sketch_curves_and_pictures_survive(tmp_path):
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QImage, QColor
    QApplication.instance() or QApplication([])
    from freeshape import sketch_ops as OPS
    doc = Document()
    s = Sketch()
    c, ax = s.add_point(0, 0), s.add_point(10, 0)
    p1, p2 = s.add_point(10, 0), s.add_point(-10, 0)
    s.add_ellipse_arc(c, ax, 5, p1, p2)
    s.add_line(p2, p1)
    OPS.conic(s, (20, 0), (30, 0), (25, 5), 0.4)
    img = QImage(400, 300, QImage.Format_RGB32)
    img.fill(QColor("orange"))
    import base64
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    buf = QByteArray()
    dev = QBuffer(buf)
    dev.open(QIODevice.WriteOnly)
    img.save(dev, "PNG")
    s.add_image(s.add_point(0, 20), s.add_point(40, 20), base64.b64encode(bytes(buf.data())).decode() * 3, 0.75)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    text, ws = _roundtrip([doc], tmp_path)
    (again,) = ws.documents()
    assert again.features[0]["params"]["sketch"] == doc.features[0]["params"]["sketch"]
    assert len(again.state.sketches[sk["id"]].regions) == len(doc.state.sketches[sk["id"]].regions)


def test_linked_picks_the_documents_that_belong_together():
    studio, asm, dwg = _hinge()
    other = Document()
    other.name = "Unrelated"
    assert [d.name for d in S.linked([other, dwg, asm, studio], asm)] == [studio.name, asm.name, dwg.name]
    assert [d.name for d in S.linked([other, dwg, asm, studio], other)] == ["Unrelated"]


def test_require_refuses_a_newer_script():
    S.require(S.SCRIPT_VERSION)
    with pytest.raises(RuntimeError):
        S.require(S.SCRIPT_VERSION + 1)


def test_menu_export_and_run_open_new_tabs(tmp_path, monkeypatch):
    from freeshape.ui import filedlg
    from PySide6.QtWidgets import QMessageBox
    from test_nav import _window
    app, w, vp = _window()
    out = str(tmp_path / "exported.py")
    monkeypatch.setattr(filedlg, "getSaveFileName", lambda *a, **k: (out, ""))
    w.export_script()
    assert os.path.exists(out)
    n = w.tabs.count()
    monkeypatch.setattr(filedlg, "getOpenFileName", lambda *a, **k: (out, ""))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    original = w.doc
    docs = w.run_script()
    app.processEvents()
    assert docs and w.tabs.count() == n + 1
    assert docs[0].uid != original.uid                        # the original is open: the copy gets its own id
    assert _sig(docs[0]) == _sig(original)


def test_every_pick_says_what_it_is(tmp_path):
    doc = Document.load(os.path.join(EX, "bracket.fshape"))
    text = S.dump([doc])
    assert "# area of Bracket profile — 594 mm², 6 edges" in text
    assert "# edge of Bracket — line 40 mm from (6, 20, 6) to (6, -20, 6)" in text
    plate = _plate()
    (part_id,) = plate.state.parts
    shape = plate.state.parts[part_id].shape
    top = next(f for f in K.faces(shape) if (K.face_info(f).get("n") or [0, 0, 0])[2] > 0.99)
    on_top = plate.make_feature("sketch")
    on_top["params"]["plane"] = {"kind": "face", "face": F.make_ref(part_id, top, "face", shape)}
    s = Sketch()
    s.add_circle(s.add_point(10, 10), 3)
    on_top["params"]["sketch"] = s.to_dict()
    plate.add_feature(on_top)
    text, ws = _roundtrip([plate], tmp_path)
    name = plate.state.parts[part_id].name
    assert f"# face of {name} — flat, facing +Z, 1200 mm², centre (20, 15, 10)" in text
    assert _sig(ws.documents()[0]) == _sig(plate) and not ws.errors()     # comments change nothing
    studio, asm, dwg = _hinge()
    text = S.dump([studio, asm, dwg])
    assert "        # Base plate <1>: edge — circle Ø8 round (15, 20, 0)\n        a={" in text
    assert "# area of Pin — 47.8 mm², 1 edge\n" in text
    compile(text, "hinge.py", "exec")
