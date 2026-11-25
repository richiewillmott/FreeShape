"""The toolbars, organised like Onshape's: Sketch first with its name, related tools under a ▾, and the window fits a
1920-pixel screen; the feature list's origin and planes fold away under "Default geometry"."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))


def _buttons(w, page):
    from PySide6.QtWidgets import QToolButton
    inner = w.tool_stack.widget(page).inner
    return [b for b in inner.findChildren(QToolButton) if b.property("icon_name")]


def test_sketch_comes_first_with_its_name_and_tools_are_grouped():
    from test_nav import _window
    app, w, vp = _window()
    names = [b.property("icon_name") for b in _buttons(w, 0)]
    assert names[:3] == ["undo", "redo", "sketch"]
    sk = _buttons(w, 0)[2]
    assert sk.text() == "Sketch"
    groups = {b.property("icon_name"): b.property("group") for b in _buttons(w, 0) if b.property("group")}
    assert groups["linear_pattern"] == ["linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"]
    assert groups["thicken"] == ["thicken", "enclose"] and "sm_flange" in groups["sm_base"]
    assert groups["offset_surface"] == ["offset_surface", "fill", "constrained_surface"]    # as Onshape groups them
    assert "offset_surface" not in groups["delete_face"]
    assert "circular_pattern" not in names and "enclose" not in names       # only in the ▾ menus
    acts = {a.property("icon_name"): a for b in _buttons(w, 0) if b.menu() for a in b.menu().actions()}
    started = []
    w.start_feature = lambda t: started.append(t)
    acts["circular_pattern"].trigger()
    assert started == ["circular_pattern"]
    assert {"circular_pattern", "enclose", "sm_hem"} <= set(w.icon_names())
    for page in range(w.tool_stack.count()):
        assert w.tool_stack.widget(page).inner.sizeHint().width() < 1900       # fits a 1920 screen


def test_sketch_variants_share_a_button_that_remembers_the_last_one():
    from test_nav import _window
    app, w, vp = _window()
    rect = w._sketch_buttons["rect"]
    assert w._sketch_buttons["center_rect"] is rect is w._sketch_buttons["rect3"]
    poly = w._sketch_buttons["polygon"]                       # polygons have their own button (no separate hexagon)
    assert "hexagon" not in w._sketch_buttons
    assert poly is not rect and w._sketch_buttons["polygon"] is poly is w._sketch_buttons["polygon_out"]
    assert w._sketch_buttons["line"] is w._sketch_buttons["line_mid"]
    assert w._sketch_buttons["sk_fillet"] is w._sketch_buttons["sk_chamfer"]
    assert w._sketch_buttons["trim"] is w._sketch_buttons["sk_split"]
    w._sketch_tool_changed("rect3")
    assert rect.isChecked() and rect.property("current") == "rect3" and "Aligned" in rect.toolTip()
    w._sketch_tool_changed("line")
    assert not rect.isChecked() and w._sketch_buttons["line"].isChecked()
    assert rect.property("current") == "rect3"                               # clicking it again: the same kind
    w._sketch_tool_changed("polygon_out")
    assert poly.isChecked() and "Circumscribed" in poly.toolTip()


def test_default_geometry_folds_away():
    from freeshape.ui.panels import ROLE_KIND
    from test_nav import _window
    app, w, vp = _window()
    fl = w.left.features
    rows = {fl.item(i).data(ROLE_KIND): fl.item(i) for i in range(fl.count())}
    assert fl.item(0).text() == "Default geometry"
    assert not rows["origin"].isHidden()
    fl.toggle_default_geometry()
    hidden = [fl.item(i).isHidden() for i in range(fl.count()) if fl.item(i).data(ROLE_KIND) in ("origin", "std_plane")]
    assert hidden == [True] * 4
    assert not any(fl.item(i).isHidden() for i in range(fl.count()) if fl.item(i).data(ROLE_KIND) == "feature")
    w._populate_lists()                                                      # stays folded after a rebuild
    assert fl.item(1).isHidden()
    fl.toggle_default_geometry()


def test_hovering_default_planes_and_origin_in_the_list_highlights_them():
    from freeshape.ui.panels import ROLE_KIND
    from test_nav import _window
    app, w, vp = _window()
    fl = w.left.features

    def row(kind, text):
        return next(fl.item(i) for i in range(fl.count()) if (fl.item(i).data(ROLE_KIND), fl.item(i).text()) == (kind, text))
    w.doc.hidden_features.discard("Top")                                   # Top shown, Front hidden
    w.doc.hidden_features.add("Front")
    w._populate_lists()
    fl._entered(row("std_plane", "Top"))
    assert vp.hover is not None and vp.hover.key == ("std", "Top")         # lit up like a hovered plane
    fl._entered(row("origin", "Origin"))
    assert vp.hover is None and vp.feature_hl and len(vp.feature_hl["polys"]) == 3   # rings round the origin
    fl._entered(row("std_plane", "Front"))                                 # a hidden plane: just its outline
    assert vp.hover is None and len(vp.feature_hl["polys"]) == 1
    fl._entered(None)
    assert vp.feature_hl is None
