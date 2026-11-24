"""Copy and paste features (Ctrl+C / Ctrl+V), and the Variables table (values and names edited in place, a rename
followed everywhere)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from freeshape import feature_clipboard as FC, kernel as K, variables as V  # noqa: E402
from freeshape.document import Document  # noqa: E402

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def _vols(doc):
    return sorted(round(K.volume(p.shape), 3) for p in doc.state.parts.values())


# --------------------------------------------------------------------------- copy / paste

def test_copied_sketch_and_extrude_paste_linked_to_each_other():
    doc = Document.load(os.path.join(EX, "hinge_parts.fshape"))
    before = _vols(doc)
    sketch, extrude = doc.features[0], doc.features[1]
    payload = FC.from_text(FC.to_text(FC.copy_features(doc, [extrude["id"], sketch["id"]])))
    assert [f["id"] for f in payload["features"]] == [sketch["id"], extrude["id"]]   # list order kept
    new = FC.paste(doc, payload)
    assert len(new) == 2 and not doc.errors
    s2, e2 = new
    assert s2["id"] != sketch["id"] and e2["params"]["regions"][0]["sketch"] == s2["id"]   # uses the copy
    assert s2["name"] == sketch["name"] + " (2)"
    assert len(_vols(doc)) == len(before) + 1
    doc.undo()                                                         # one step
    assert _vols(doc) == before and len(doc.features) == 6


def test_paste_into_another_part_studio_and_at_a_place():
    src = Document.load(os.path.join(EX, "hinge_parts.fshape"))
    payload = FC.copy_features(src, [f["id"] for f in src.features[:2]])
    other = Document()
    FC.paste(other, payload)
    assert len(other.state.parts) == 1 and not other.errors
    FC.paste(other, payload, index=0)                                  # in front of what's there
    assert other.features[0]["type"] == "sketch" and other.features[0]["name"].endswith("(2)")   # at the front
    assert len(other.state.parts) == 2 and not other.errors
    assert FC.from_text("just some text") is None and FC.from_text("") is None


def test_ctrl_c_ctrl_v_in_the_window():
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    n = len(w.doc.features)
    w.copy_features([f["id"] for f in w.doc.features])
    assert FC.from_text(QApplication.clipboard().text()) is not None
    w.paste_features()
    assert len(w.doc.features) == 2 * n and len(w.doc.state.parts) == 2 and not w.doc.errors
    w.mode = "dialog"                                                  # not while a feature is open
    w.paste_features()
    assert len(w.doc.features) == 2 * n
    w.mode = "idle"


# --------------------------------------------------------------------------- variables

def _plate():
    from test_script_dump import _plate as make
    return make()


def test_list_uses_rename_and_set():
    doc = _plate()
    rows = {v["name"]: v for v in V.list_variables(doc)}
    assert set(rows) == {"w", "h", "n", "gap", "half"}
    assert rows["w"]["value"] == 40.0 and rows["w"]["kind"] == "length" and rows["w"]["typed"]
    assert len(V.uses(doc, "w")) == 2                                  # the sketch dimension and #half
    V.rename(doc, "w", "width")
    assert not doc.errors and "width" in doc.state.vars and "w" not in doc.state.vars
    texts = str([f["params"] for f in doc.features])
    assert "#w " not in texts and "#w'" not in texts and "#width" in texts
    with pytest.raises(ValueError):
        V.rename(doc, "width", "h")                                    # taken
    with pytest.raises(ValueError):
        V.rename(doc, "width", "2wide")                                # not a name
    hid = next(v["fid"] for v in V.list_variables(doc) if v["name"] == "h")
    V.set_expr(doc, hid, "5")
    assert _vols(doc) == [round(40 * 30 * 5, 3)]
    doc.undo()
    assert _vols(doc) == [round(40 * 30 * 10, 3)]


def test_rename_reaches_configurations():
    doc = _plate()
    doc.configs = {"rows": [{"name": "Big", "vars": {"w": "60", "h": "#w / 4"}, "suppress": []}], "active": 0}
    V.rename(doc, "w", "width")
    row = doc.configs["rows"][0]
    assert row["vars"] == {"width": "60", "h": "#width / 4"}


def test_table_window_edits_in_place():
    from PySide6.QtWidgets import QApplication
    from test_nav import _window
    app, w, vp = _window()
    V.add(w.doc, "wall", "3")
    w.show_variables()
    t = w._variables_table
    assert t.table.rowCount() == 1 and t.table.item(0, 0).text() == "#wall"
    t.table.item(0, 3).setText("4.5")                                  # value
    QApplication.processEvents()
    assert w.doc.state.vars["wall"] == 4.5 and t.table.item(0, 1).text().startswith("4.5")
    t.table.item(0, 0).setText("#thick")                               # name
    assert "thick" in w.doc.state.vars and t.table.item(0, 0).text() == "#thick"
    V.add(w.doc, "gap", "1")                                           # follows the model
    assert t.table.rowCount() == 2
    t.close()
    assert w._variables_table is None
