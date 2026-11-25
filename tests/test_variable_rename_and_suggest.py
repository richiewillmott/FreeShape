"""Renaming a variable — in its dialog or in the feature list — renames it everywhere it's used, and value boxes
offer #variables as you type a name, even without the #."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import variables as V  # noqa: E402
from test_core import _plate_doc  # noqa: E402


def _doc_using_w():
    doc, _sk, _ = _plate_doc()
    v = V.add(doc, "w", "15", index=0)
    ex = doc.features[2]
    doc.replace_feature(dict(ex, params=dict(ex["params"], depth="#w * 2")))
    doc.set_configs([{"name": "Big", "vars": {"w": "30"}, "suppress": []}], 0)
    assert not doc.errors
    return doc, v["id"], ex["id"]


def test_renaming_a_variable_in_its_dialog_renames_its_uses():
    doc, vid, exid = _doc_using_w()
    f = doc.feature(vid)
    doc.replace_feature(dict(f, params=dict(f["params"], name="width")))    # what the Variable dialog does
    assert not doc.errors
    assert doc.feature(exid)["params"]["depth"] == "#width * 2"
    assert doc.feature(vid)["name"] == "#width"                                # its label in the list too
    assert doc.configs["rows"][0]["vars"] == {"width": "30"}                  # and the configuration table
    doc.undo()                                                                 # one undo puts it all back
    assert doc.feature(exid)["params"]["depth"] == "#w * 2" and doc.feature(vid)["params"]["name"] == "w"


def test_renaming_a_variable_in_the_feature_list_renames_the_variable():
    doc, vid, exid = _doc_using_w()
    doc.rename_feature(vid, "#wall")
    assert not doc.errors
    assert doc.feature(vid)["params"]["name"] == "wall" and doc.feature(vid)["name"] == "#wall"
    assert doc.feature(exid)["params"]["depth"] == "#wall * 2"


def test_a_name_another_variable_has_is_not_taken_over():
    doc, vid, exid = _doc_using_w()
    V.add(doc, "h", "5", index=0)
    f = doc.feature(vid)
    doc.replace_feature(dict(f, params=dict(f["params"], name="h")))           # clashes: uses aren't switched
    assert doc.feature(exid)["params"]["depth"] == "#w * 2"


def _box():
    from PySide6.QtWidgets import QApplication, QLineEdit
    from freeshape.ui.varcomplete import attach
    app = QApplication.instance() or QApplication([])
    ed = QLineEdit()
    vc = attach(ed, lambda: {"wall": 2.0, "width": 60.0, "mass": 1.0}, lambda: {"wall": "length", "width": "length"})
    ed.show()
    return app, ed, vc


def _type(app, ed, text):
    from PySide6.QtTest import QTest
    ed.setFocus()
    QTest.keyClicks(ed, text)
    app.processEvents()


def _offered(vc):
    return [vc.completer.completionModel().index(i, 0).data(vc.completer.completionRole())
            for i in range(vc.completer.completionCount())] if vc.completer.popup().isVisible() else []


def test_typing_a_name_without_the_hash_offers_variables():
    app, ed, vc = _box()
    _type(app, ed, "w")
    assert _offered(vc) == ["#wall", "#width"]
    vc._insert("#width")
    assert ed.text() == "#width"


def test_part_way_through_an_expression():
    app, ed, vc = _box()
    _type(app, ed, "10 + wa")
    assert _offered(vc) == ["#wall"]
    vc._insert("#wall")
    assert ed.text() == "10 + #wall"


def test_a_unit_after_a_number_is_not_offered_variables():
    app, ed, vc = _box()
    _type(app, ed, "25 m")
    assert _offered(vc) == []                        # "m" is metres here, not the start of #mass
    ed.clear()
    _type(app, ed, "2m")
    assert _offered(vc) == []


def test_the_hash_still_works():
    app, ed, vc = _box()
    _type(app, ed, "#m")
    assert _offered(vc) == ["#mass"]
