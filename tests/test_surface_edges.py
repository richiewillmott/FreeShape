"""Extrude Solid -> Surface / Thin: the area becomes its separate edges (take one away for a C), a click on a face
while Surface is chosen doesn't make a solid behind your back, and switching back gives the area again."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.ui import dialogs as D  # noqa: E402
from freeshape.ui.dialogs import fields_to_params  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def _edit_extrude(w):
    ext = next(f for f in w.doc.features if f["type"] == "extrude")
    w.edit_feature(ext["id"])
    return ext, w.dialog


def test_square_becomes_four_edges_and_back():
    from test_nav import _window
    app, w, vp = _window()
    ext, d = _edit_extrude(w)
    d._set("creation", "surface")
    app.processEvents()
    items = d.fields["curves"].items
    assert len(items) == 4 and all(r["kind"] == "region_edge" for _k, r in items)
    assert d.fields["profiles"].items == []
    assert all(D.item_label(w.doc, it) == "Edge of sketch region" for it in items)
    assert d.active_field is d.fields["curves"]
    # back to Solid: all four still there, so the region comes back
    d._set("creation", "solid")
    app.processEvents()
    assert len(d.fields["profiles"].items) == 1 and d.fields["curves"].items == []
    assert d.active_field is d.fields["profiles"]
    w.cancel_dialog()


def test_three_edges_make_a_c_shaped_surface():
    from test_nav import _window
    app, w, vp = _window()
    ext, d = _edit_extrude(w)
    d._set("creation", "surface")
    app.processEvents()
    fld = d.fields["curves"]
    fld.items = fld.items[:3]                 # one edge taken away
    fld.refresh(w.doc)
    p = copy.deepcopy(d.feature["params"])
    fields_to_params("extrude", {k: f.items for k, f in d.fields.items()}, p)
    assert len(p["curves"]) == 3
    from freeshape import body_options as BO
    state = d.base_state
    wires = BO.curve_wires(state, p)
    assert len(wires) == 1 and len(K.edges(wires[0])) == 3
    a, b = BO._ends(wires[0])
    assert abs(a - b).max() > 1                # an open C, not a closed loop
    f = copy.deepcopy(ext)
    f["params"] = p
    w.doc.replace_feature(f)
    errs = {}
    st = w.doc.compute(w.doc.features, errors=errs)
    assert not errs, errs
    surf = [pt for pt in st.parts.values() if pt.created_by == ext["id"]] if hasattr(
        next(iter(st.parts.values())), "created_by") else list(st.parts.values())
    assert len(surf) == 1 and len(K.faces(surf[0].shape)) == 3 and not K.solids(surf[0].shape)
    # switching back with an edge missing can't give the region
    d._set("creation", "solid")
    app.processEvents()
    assert d.fields["profiles"].items == []
    w.cancel_dialog()


def test_face_click_while_surface_makes_no_solid():
    from test_nav import _window
    app, w, vp = _window()
    w.start_feature("extrude")
    app.processEvents()
    d = w.dialog
    d._set("creation", "surface")
    app.processEvents()
    pid = next(iter(vp.part_shapes))
    d.handle_pick(Pick("face", (pid, 0)))     # a face clicked by mistake
    app.processEvents()
    assert d.fields["profiles"].items == []
    assert d.fields["curves"].items == []     # Surface takes edges and sketch curves only
    d.handle_pick(Pick("edge", (pid, 0)))
    assert len(d.fields["curves"].items) == 1
    assert d.fields["profiles"].items == []
    w.cancel_dialog()
