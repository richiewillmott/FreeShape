"""Selecting like Onshape between the view and the feature list: picking a face or edge marks the feature that made
it; a feature selected in the list shows its geometry exactly as selected geometry; a click on empty space (or Esc)
deselects the features too."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.ui.panels import ROLE_ID, ROLE_KIND  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def _filleted():
    """The test box (20 x 20 x 10, centred) with one vertical edge filleted."""
    from test_select_other_keys import _win
    app, w, vp = _win()
    part = next(iter(w.doc.state.parts.values()))
    edge = next(e for e in K.edges(part.shape) if (ln := K.edge_line(e)) is not None and abs(ln[1][2]) > 0.9
                and np.allclose(K.edge_info(e)["c"][:2], [10, 10]))
    fi = w.doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, edge, "edge", part.shape)], radius=3.0)
    w.doc.add_feature(fi)
    app.processEvents()
    assert not w.doc.errors
    return app, w, vp, fi["id"]


def _face_index(w, test):
    part = next(iter(w.doc.state.parts.values()))
    return part.id, next(i for i, f in enumerate(K.faces(part.shape)) if test(K.face_info(f)))


def _marked(w):
    return set(w.left.features._related)


def _row(w, fid):
    lst = w.left.features
    return next(i for i in range(lst.count()) if lst.item(i).data(ROLE_KIND) == "feature"
                and lst.item(i).data(ROLE_ID) == fid)


def test_picking_a_face_marks_the_feature_that_made_it():
    app, w, vp, fillet = _filleted()
    extrude = next(f["id"] for f in w.doc.features if f["type"] == "extrude")
    pid, round_face = _face_index(w, lambda i: "r" in i and abs(i["r"] - 3) < 1e-6)
    vp.set_selection([Pick("face", (pid, round_face))])
    app.processEvents()
    assert _marked(w) == {fillet}
    pid, top = _face_index(w, lambda i: i.get("n") is not None and i["n"][2] > 0.9)
    vp.set_selection([Pick("face", (pid, top))])                 # the top: still the extrude's (the fillet
    app.processEvents()                                          # only trimmed it)
    assert _marked(w) == {extrude}
    vp.clear_selection()
    app.processEvents()
    assert _marked(w) == set()
    assert not w.left.features.selectedItems()                   # marked, never selected


def test_picking_a_fillets_edge_marks_the_fillet():
    app, w, vp, fillet = _filleted()
    part = next(iter(w.doc.state.parts.values()))
    ei = next(i for i, e in enumerate(K.edges(part.shape))
              if any("r" in K.face_info(f) and abs(K.face_info(f)["r"] - 3) < 1e-6
                     for f in K._faces_at_edge(part.shape, e)))
    vp.set_selection([Pick("edge", (part.id, ei))])
    app.processEvents()
    assert _marked(w) == {fillet}


def test_a_feature_selected_in_the_list_is_shown_as_selected_geometry():
    app, w, vp, fillet = _filleted()
    lst = w.left.features
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, lst.visualItemRect(lst.item(_row(w, fillet))).center())
    app.processEvents()
    pid, round_face = _face_index(w, lambda i: "r" in i and abs(i["r"] - 3) < 1e-6)
    assert vp.feature_sel is not None and round_face in vp.feature_sel["faces"][pid]
    assert vp.feature_hl is None or not vp.feature_hl.get("faces", {}).get(pid, set()) & {round_face}


def test_a_click_on_empty_space_deselects_the_feature_too():
    app, w, vp, fillet = _filleted()
    lst = w.left.features
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, lst.visualItemRect(lst.item(_row(w, fillet))).center())
    app.processEvents()
    assert lst.selectedItems() and not vp.selection              # a feature selected, nothing in the view
    vp.clear_selection()                                         # what a click on empty space (or Esc) does
    app.processEvents()
    assert not lst.selectedItems() and vp.feature_sel is None


def test_the_mark_is_actually_drawn_on_the_row():
    """The list's style sheet styles rows itself, so the mark has to be painted by the row's own painter — the
    first version set a row colour that never showed."""
    app, w, vp, fillet = _filleted()
    lst = w.left.features
    rect = lst.visualItemRect(lst.item(_row(w, fillet)))

    def colour():
        return lst.viewport().grab().toImage().pixelColor(rect.left() + 3, rect.center().y()).name()
    plain = colour()
    pid, round_face = _face_index(w, lambda i: "r" in i and abs(i["r"] - 3) < 1e-6)
    vp.set_selection([Pick("face", (pid, round_face))])
    app.processEvents()
    assert colour() != plain
    vp.clear_selection()
    app.processEvents()
    assert colour() == plain


def test_the_feature_being_edited_is_shaded():
    app, w, vp, fillet = _filleted()
    lst = w.left.features
    rect = lst.visualItemRect(lst.item(_row(w, fillet)))
    plain = lst.viewport().grab().toImage().pixelColor(rect.left() + 3, rect.center().y()).name()
    w.edit_feature(fillet)
    app.processEvents()
    rect = lst.visualItemRect(lst.item(_row(w, fillet)))
    assert lst.viewport().grab().toImage().pixelColor(rect.left() + 3, rect.center().y()).name() != plain


def _row_colour(w, kind, ident):
    lst = w.left.features
    i = next(i for i in range(lst.count()) if lst.item(i).data(ROLE_KIND) == kind and lst.item(i).data(ROLE_ID) == ident)
    r = lst.visualItemRect(lst.item(i))
    return lst.viewport().grab().toImage().pixelColor(r.left() + 3, r.center().y()).name()


def test_picking_a_default_plane_marks_it_in_the_list():
    app, w, vp, _fillet = _filleted()
    plain = _row_colour(w, "std_plane", "Front")
    vp.set_selection([Pick("plane", ("std", "Front"))])
    app.processEvents()
    assert "std:Front" in _marked(w) and _row_colour(w, "std_plane", "Front") != plain
    assert _row_colour(w, "std_plane", "Top") == plain                  # only the one picked
    vp.clear_selection()
    app.processEvents()
    assert _row_colour(w, "std_plane", "Front") == plain


def test_picking_a_plane_you_made_marks_its_feature():
    app, w, vp, _fillet = _filleted()
    pf = w.doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=30.0)
    w.doc.add_feature(pf)
    app.processEvents()
    plain = _row_colour(w, "feature", pf["id"])
    vp.set_selection([Pick("plane", ("plane", pf["id"]))])
    app.processEvents()
    assert _marked(w) == {pf["id"]} and _row_colour(w, "feature", pf["id"]) != plain
