"""Issue #31: units are checked (an angle in a length box is an error, not a silent number), a box that can't be
used says why instead of quietly going back to the old value, and the one Variable feature takes a Length,
Angle, Number or Any expression with its unit, as well as the measured types."""
import copy
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from freeshape import kernel as K, measure_value as MV  # noqa: E402
from freeshape.expr import check_units  # noqa: E402
from test_measure_value import _box  # noqa: E402


def _var(doc, before, **params):
    """A Variable feature inserted before feature `before` (so later features can use it)."""
    f = doc.make_feature("measure_value")
    f["params"].update(params)
    doc.insert_feature(f, doc.feature_index(before["id"])) if hasattr(doc, "insert_feature") else \
        doc.features.insert(doc.feature_index(before["id"]), f)
    doc.rebuild()
    return f


def _depth(doc, e, value):
    e2 = copy.deepcopy(doc.feature(e["id"]))
    e2["params"]["depth"] = value
    doc.replace_feature(e2)
    return doc.errors.get(e["id"])


def test_check_units():
    kinds = {"w": "length", "a": "angle", "s": "area", "n": "number", "old": "any"}
    assert check_units("90 deg", "length") and check_units("10 mm", "angle")
    assert check_units("#a", "length", kinds) and check_units("10 mm + 5 deg", "length")
    assert check_units("3 mm", "number")
    for ok, want in (("10", "length"), ("1 m", "length"), ("#w * 2", "length"), ("2 rad", "angle"),
                     ("sin(30 deg) * #w", "length"), ("sqrt(#s)", "length"), ("#n * 3", "length"),
                     ("#old", "length"), ("1/2 in", "length"), ("(10 + 5) mm", "length")):
        assert check_units(ok, want, kinds) is None, ok


@pytest.mark.parametrize("mtype, expr, unit, value, shown", [
    ("fixed_length", "1", "m", 1000.0, "1 m"),
    ("fixed_length", "25.4", "mm", 25.4, "25.4 mm"),
    ("fixed_length", "2 in", "mm", 50.8, "50.8 mm"),          # its own unit wins
    ("fixed_angle", "0.5", "rad", math.degrees(0.5), "0.5 rad"),
    ("fixed_number", "6", "", 6.0, "6"),
    ("expression", "2 * 3 mm", "", 6.0, "6 mm"),
])
def test_typed_variable_values_and_units(mtype, expr, unit, value, shown):
    doc, e, _pid = _box()
    params = {"name": "v", "mtype": mtype, "expr": expr}
    if mtype == "fixed_angle":
        params["aunit"] = unit
    elif unit:
        params["unit"] = unit
    f = _var(doc, e, **params)
    assert not doc.errors, doc.errors
    assert doc.state.vars["v"] == pytest.approx(value)
    assert MV.label(doc.feature(f["id"]), doc.state).endswith(f"#v = {shown}")


def test_a_length_variable_given_an_angle_is_an_error():
    doc, e, _pid = _box()
    f = _var(doc, e, name="v", mtype="fixed_length", expr="30 deg")
    assert "angle" in doc.errors.get(f["id"], "")


def test_using_a_variable_of_the_wrong_kind_fails_the_feature():
    doc, e, _pid = _box()
    _var(doc, e, name="a", mtype="fixed_angle", expr="30")
    _var(doc, e, name="w", mtype="fixed_length", expr="0.012", unit="m")
    err = _depth(doc, e, "#a")
    assert err and "angle" in err and "length" in err          # not a silent 30 mm
    assert _depth(doc, e, "#w") is None
    part = next(iter(doc.state.parts.values()))
    assert K.volume(part.shape) == pytest.approx(20 * 10 * 12)     # 0.012 m = 12 mm deep
    assert "angle" in (_depth(doc, e, "90 deg") or "")


def test_measured_area_cannot_be_a_depth_but_an_old_variable_can():
    doc, e, pid = _box()
    v = doc.make_feature("variable")                            # the old Variable feature: no unit, never checked
    v["params"].update(name="old", expr="7")
    doc.features.insert(doc.feature_index(e["id"]), v)
    doc.rebuild()
    assert _depth(doc, e, "#old") is None
    s = Document_area(doc)
    assert s


def Document_area(doc):
    """A measured area used as a depth is an error."""
    pid = next(iter(doc.state.parts))
    f = doc.make_feature("measure_value")
    f["params"].update(name="ar", mtype="area", entities=[["part", pid]])
    doc.add_feature(f)
    e = doc.make_feature("extrude")
    first = next(x for x in doc.features if x["type"] == "extrude")
    e["params"] = copy.deepcopy(first["params"])
    e["params"]["depth"] = "#ar"
    doc.add_feature(e)
    return "area" in doc.errors.get(e["id"], "")


# --------------------------------------------------------------------------- the dialog
def _dialog(ftype="extrude"):
    from test_ui_fixes import _window
    doc, e, pid = _box()
    app, w = _window(doc)
    w.edit_feature(e["id"]) if ftype == "extrude" else w.start_feature(ftype)
    app.processEvents()
    return app, w, doc, e


def test_a_bad_number_box_keeps_the_text_says_why_and_blocks_accept():
    app, w, doc, e = _dialog()
    d = w.dialog
    ed = d.widgets["depth"]
    ed.setText("30 deg")
    ed.textEdited.emit("30 deg")
    ed.editingFinished.emit()
    app.processEvents()
    assert ed.text() == "30 deg"                                # not quietly put back to the old value
    assert "angle" in d.err.text() and not d.err.isHidden()
    d.accept()
    app.processEvents()
    assert w.dialog is d                                        # still open: nothing was accepted
    for txt, word in (("#nope", "isn't defined"), ("12 kg", "can't read")):
        ed.setText(txt)
        ed.textEdited.emit(txt)
        assert word in d.err.text()
    ed.setText("12")
    ed.textEdited.emit("12")
    assert not d._field_errors
    d.accept()
    app.processEvents()
    assert doc.feature(e["id"])["params"]["depth"] == 12


def test_toolbar_variable_starts_a_length_with_a_free_name():
    app, w, doc, e = _dialog(ftype="measure_value")
    d = w.dialog
    p = d.feature["params"]
    assert d.ftype == "measure_value" and p["mtype"] == "fixed_length" and p["name"] == "v"
    d.accept()
    app.processEvents()
    w.start_feature("measure_value")
    app.processEvents()
    assert w.dialog.feature["params"]["name"] == "v2"
    assert w.dialog.feature["name"].startswith("Variable")
