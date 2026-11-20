"""Document units (inches and others): lengths shown and typed in the document's unit, the model kept in mm."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from freeshape import units as U  # noqa: E402
from freeshape.expr import parse_length, with_unit  # noqa: E402


@pytest.fixture(autouse=True)
def back_to_mm():
    yield
    U.set_unit("mm")


def test_with_unit_and_parse_length():
    v, k = {"w": 10.0, "n": 2.0}, {"w": "length", "n": "number"}
    assert with_unit("2", "in") == "(2 in)" and parse_length("2", "in") == pytest.approx(50.8)
    assert parse_length("1/2", "in") == pytest.approx(12.7)
    assert with_unit("#w + 1", "in", k) == "#w + (1 in)" and parse_length("#w + 1", "in", v, k) == pytest.approx(35.4)
    assert with_unit("#w * 2", "in", k) == "#w * 2"                        # already a length
    assert parse_length("25 mm", "in") == pytest.approx(25)                 # its own unit wins
    assert parse_length("1 + 2 mm", "in") == pytest.approx(27.4)
    assert with_unit("#w + 1", "mm", k) == "#w + 1"                        # millimetres: unchanged
    assert parse_length("3", "cm") == pytest.approx(30)


def test_unit_texts():
    U.set_unit("in")
    assert U.length(25.4) == "1 in" and U.num(12.7) == "0.5"
    assert U.area(645.16) == "1 in²" and U.volume(16387.064) == "1 in³"
    U.set_unit("m")
    assert U.length(1500) == "1.5 m"


def test_inch_document_in_the_window(tmp_path):
    from test_nav import _window
    from freeshape.document import Document
    app, w, vp = _window()
    doc = w.doc
    w.set_doc_units("in")
    assert doc.units == "in" and U.unit() == "in" and "inch" in w.units_label.text()
    ext = next(f for f in doc.features if f["type"] == "extrude")
    depth_mm = ext["params"]["depth"]
    w.edit_feature(ext["id"])
    d = w.dialog
    ed = d.widgets["depth"]
    assert ed.text() == U.num(depth_mm)                                    # shown in inches
    ed.setText("2")
    ed.textEdited.emit("2")
    assert d.feature["params"]["depth"] == pytest.approx(50.8)             # a bare 2 is 2 inches, kept in mm
    ed.setText("10 mm")
    ed.textEdited.emit("10 mm")
    assert d.feature["params"]["depth"] == pytest.approx(10)
    w.cancel_dialog() if hasattr(w, "cancel_dialog") else d.reject()
    app.processEvents()
    path = str(tmp_path / "inch.fshape")
    doc.save(path)
    assert Document.load(path).units == "in"
    # a new Variable starts as a Length in inches
    w.start_feature("measure_value")
    assert w.dialog.feature["params"]["unit"] == "in"
    w.dialog.reject()
    w.set_doc_units("mm")
    assert U.unit() == "mm"


def test_measurements_follow_the_unit():
    from freeshape import api as fs, measure_value as MV
    U.set_unit("in")
    assert MV.quantity_text("length", 50.8) == "2 in"
    assert MV.quantity_text("area", 645.16 * 3) == "3 in²"
    assert MV.quantity_text("angle", 30) == "30 °"
    p = {"mtype": "fixed_length", "unit": "mm"}
    assert MV.value_text(p, 25.4) == "25.4 mm"                             # a Length keeps its own unit
    del fs
