"""Variables made on the fly: type an unknown #name in a feature or sketch-dimension box and FreeShape offers to
create it just before that feature, with the value the box had; # drops down the variables there are. Also: the
view faces a sketch when it's opened for editing (Orient normal to sketch on edit), as in Onshape."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QEvent  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402


def _extrude_dialog():
    from test_nav import _window
    from test_auto_op import _second_sketch, _dialog_with
    app, w, vp = _window()
    fid, sr = _second_sketch(w)
    far = max(sr.regions, key=lambda r: np.mean(K.bbox(r)[0][0]))
    return app, w, _dialog_with(w, app, fid, far)


def _type(ed, text):
    ed.setText(text)
    ed.textEdited.emit(text)


def _var_features(doc):
    return [f for f in doc.features if f["type"] == "measure_value"]


def test_unknown_name_offers_create_with_the_old_value():
    app, w, d = _extrude_dialog()
    d._set("depth", 25.0)
    ed = d.widgets["depth"]
    _type(ed, "#depth")
    assert d.create_btn.isVisibleTo(d) and "#depth = 25" in d.create_btn.text()
    n_before, idx_before = len(w.doc.features), d.index
    d.create_btn.click()
    app.processEvents()
    (var,) = _var_features(w.doc)
    assert var["params"]["name"] == "depth" and var["params"]["mtype"] == "fixed_length"
    assert w.doc.features.index(var) == idx_before and d.index == idx_before + 1 == len(w.doc.features)
    assert len(w.doc.features) == n_before + 1
    assert d.feature["params"]["depth"] == "#depth" and not d.create_btn.isVisibleTo(d)
    assert "depth" not in d._field_errors
    d.accept()
    app.processEvents()
    ex = w.doc.features[-1]
    assert ex["type"] == "extrude" and w.doc.features.index(var) < w.doc.features.index(ex)
    vols = sorted(round(K.volume(p.shape), 3) for p in w.doc.state.parts.values())
    assert 40 * 30 * 25 in [round(v) for v in vols]
    var["params"]["expr"] = "40"                               # the variable drives the feature
    w.doc.replace_feature(var)
    vols = sorted(round(K.volume(p.shape)) for p in w.doc.state.parts.values())
    assert 40 * 30 * 40 in vols


def test_enter_creates_instead_of_accepting():
    app, w, d = _extrude_dialog()
    _type(d.widgets["depth"], "#h * 2")
    assert d.create_btn.isVisibleTo(d)
    d.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    app.processEvents()
    assert w.dialog is d and w.mode == "dialog"                # still open
    assert [f["params"]["name"] for f in _var_features(w.doc)] == ["h"]
    assert d.feature["params"]["depth"] == "#h * 2"
    w.cancel_dialog()


def test_angle_box_makes_an_angle_and_bad_names_get_no_offer():
    from test_nav import _window
    from test_auto_op import _second_sketch
    app, w, vp = _window()
    fid, sr = _second_sketch(w)
    w.start_feature("revolve")
    app.processEvents()
    d = w.dialog
    d._set("end", "one")
    d._sync_visibility()
    _type(d.widgets["angle"], "#turn")
    assert d.create_btn.isVisibleTo(d) and "°" in d.create_btn.text()
    d.create_btn.click()
    (var,) = _var_features(w.doc)
    assert var["params"]["mtype"] == "fixed_angle"
    for bad in ("#a + #b", "#3d", "#turn + 5 + #"):
        _type(d.widgets["angle"], bad)
        assert not d.create_btn.isVisibleTo(d), bad
    w.cancel_dialog()


def test_editing_an_earlier_feature_puts_the_variable_just_before_it():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    ex = next(f for f in doc.features if f["type"] == "extrude")
    doc.rollback = None
    w.edit_feature(ex["id"])
    app.processEvents()
    d = w.dialog
    i0 = doc.features.index(ex)
    _type(d.widgets["depth"], "#thick")
    d.create_btn.click()
    var = _var_features(doc)[0]
    assert doc.features.index(var) == i0 and doc.features.index(ex) == i0 + 1 and doc.rollback is None
    d.accept()
    app.processEvents()
    assert doc.features[i0 + 1]["params"]["depth"] == "#thick" and not doc.errors


def test_sketch_dimension_box_offers_it_too(monkeypatch):
    from PySide6.QtWidgets import QMessageBox, QApplication
    from freeshape.ui.viewport import Pick
    from test_nav import _window
    app, w, vp = _window()
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    vp.set_selection([Pick("plane", ("std", "Top"))])
    w.start_sketch()
    s = w.sketcher
    sk = s.sk
    a, b = sk.add_point(0, 0), sk.add_point(30, 0)
    line = sk.add_line(a, b)
    c = sk.add_constraint("distance", line, value=30.0)
    s.edit_dimension(vp, c)
    ed = s._editor
    ed.setText("#len")
    ed.returnPressed.emit()
    QApplication.processEvents()
    assert c["expr"] == "#len" and s.variables.get("len") == 30.0
    (var,) = _var_features(w.doc)
    idx = w.sk_ctx["index"]
    assert w.doc.features.index(var) == idx - 1
    w.finish_sketch()
    sketch = next(f for f in w.doc.features if f["type"] == "sketch" and f is not w.doc.features[0])
    assert w.doc.features.index(var) < w.doc.features.index(sketch) and not w.doc.errors


def test_hash_drops_down_the_variables():
    from PySide6.QtWidgets import QApplication, QLineEdit
    from freeshape.ui.varcomplete import attach, missing_variable
    QApplication.instance() or QApplication([])
    ed = QLineEdit()
    c = attach(ed, lambda: {"wall": 3.0, "width": 40.0, "gap": 1.0}, lambda: {"wall": "length"})
    ed.setText("2 * #w")
    ed.setCursorPosition(len(ed.text()))
    c._update()
    assert c.completer.completionCount() == 2                  # #wall, #width
    c._insert("#width")
    assert ed.text() == "2 * #width"
    ed.setText("10")
    c._update()
    assert not c.completer.popup().isVisible()
    assert missing_variable("#a * 2", {}) == "a" and missing_variable("#a + #b", {}) is None
    assert missing_variable("#a + 1", {"a": 1}) is None


def test_a_new_tab_view_never_cancels_a_sketch_turning_to_face_you():
    """The tab's first view comes a moment after it opens; a sketch opened before then keeps its view."""
    from test_nav import _window
    app, w, vp = _window()
    sk = next(f for f in w.doc.features if f["type"] == "sketch")
    w.set_display("orient_on_sketch", True, save=False)
    w.edit_feature(sk["id"])
    turning_to = vp._anim[1][1].copy()
    w._fresh_view()                                            # the delayed first view arrives late
    assert vp._anim is not None and np.allclose(vp._anim[1][1], turning_to)
    w.cancel_sketch()


def test_editing_a_sketch_faces_it_and_switching_off_goes_back():
    from freeshape.ui.viewport import Pick
    from test_nav import _window
    app, w, vp = _window()
    sk = next(f for f in w.doc.features if f["type"] == "sketch")
    iso = vp.cam.state()
    w.set_display("orient_on_sketch", True, save=False)
    w.edit_feature(sk["id"])
    app.processEvents()
    target_R = vp._anim[1][1] if vp._anim else vp.cam.R
    assert abs(abs(float(np.asarray(target_R)[2] @ np.array([0, 0, 1.0]))) - 1) < 1e-6   # looking along Top's normal
    w.set_display("orient_on_sketch", False, save=False)      # off while sketching: back to the view you had
    back = vp._anim[1] if vp._anim else vp.cam.state()
    assert np.allclose(back[1], iso[1])
    w.cancel_sketch()
    vp._anim = None                                            # (the animation back, which offscreen never ends)
    vp.cam.set_state(iso)
    w.edit_feature(sk["id"])                                   # off: editing leaves the view alone
    app.processEvents()
    assert vp._anim is None and np.allclose(vp.cam.R, iso[1])
    w.cancel_sketch()
    w.set_display("orient_on_sketch", True, save=False)
