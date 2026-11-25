"""Issue #52: clicks in the feature list and the parts list add to the selection, as in Onshape — click Part 1,
then Part 2, and both are picked (for a Boolean, say) — and the parts list and the view pick the same parts."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt, QPoint  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from freeshape.ui.panels import ROLE_ID, ROLE_KIND  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def _two_parts():
    from test_select_other_keys import _win
    app, w, vp = _win()
    pid = next(iter(w.doc.state.parts))
    mv = w.doc.make_feature("move")
    mv["params"].update(parts=[pid], type="xyz", dx=40.0, copy=True)
    w.doc.add_feature(mv)
    app.processEvents()
    assert len(w.doc.state.parts) == 2
    return app, w, vp


def _click_row(app, lst, row, mods=Qt.NoModifier):
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, mods, lst.visualItemRect(lst.item(row)).center())
    app.processEvents()


def _picked_parts(vp):
    return {pk.key[0] for pk in vp.selection if pk.kind == "part"}


def test_clicking_parts_in_the_list_adds_them_to_the_selection():
    app, w, vp = _two_parts()
    parts = w.left.parts
    a, b = parts.item(0).data(ROLE_ID), parts.item(1).data(ROLE_ID)
    _click_row(app, parts, 0)
    _click_row(app, parts, 1)
    assert set(parts.selected_part_ids()) == {a, b}            # both kept, not swapped
    assert _picked_parts(vp) == {a, b}                         # and picked in the view too
    _click_row(app, parts, 0)                                  # clicking a picked one drops it
    assert parts.selected_part_ids() == [b] and _picked_parts(vp) == {b}


def test_the_view_and_the_parts_list_agree():
    app, w, vp = _two_parts()
    parts = w.left.parts
    a = parts.item(0).data(ROLE_ID)
    vp.set_selection([Pick("part", (a,))])                     # picked in the view …
    app.processEvents()
    assert parts.selected_part_ids() == [a]                    # … shows in the list
    vp.set_selection([])                                       # Esc / click on empty space
    app.processEvents()
    assert parts.selected_part_ids() == []


def test_clicking_planes_and_features_in_the_feature_list_adds_them():
    app, w, vp = _win_features()
    lst = w.left.features
    rows = {lst.item(i).data(ROLE_ID): i for i in range(lst.count())
            if lst.item(i).data(ROLE_KIND) in ("std_plane", "feature")}
    feats = [f["id"] for f in w.doc.features]
    for key in ("Top", "Front", feats[0], feats[-1]):
        _click_row(app, lst, rows[key])
    picked = {it.data(ROLE_ID) for it in lst.selectedItems()}
    assert picked == {"Top", "Front", feats[0], feats[-1]}
    planes = {pk.key[1] for pk in vp.selection if pk.kind == "plane"}
    assert planes == {"Top", "Front"}                          # the planes are picked in the view as well
    _click_row(app, lst, rows["Top"])                          # and a second click drops one
    assert "Top" not in {it.data(ROLE_ID) for it in lst.selectedItems()}
    assert {pk.key[1] for pk in vp.selection if pk.kind == "plane"} == {"Front"}
    vp.set_selection([])                                       # Esc clears the list too
    app.processEvents()
    assert not lst.selectedItems()


def test_shift_click_still_picks_a_run_and_empty_space_clears():
    app, w, vp = _win_features()
    lst = w.left.features
    rows = [i for i in range(lst.count()) if lst.item(i).data(ROLE_KIND) == "feature"]
    _click_row(app, lst, rows[0])
    _click_row(app, lst, rows[-1], Qt.ShiftModifier)
    assert len([it for it in lst.selectedItems() if it.data(ROLE_KIND) == "feature"]) == len(rows)
    below = lst.visualItemRect(lst.item(lst.count() - 1)).bottomLeft() + QPoint(5, 40)
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, below)
    app.processEvents()
    assert not lst.selectedItems()


def _win_features():
    from test_select_other_keys import _win
    app, w, vp = _win()
    w.left.features.resize(260, 600)
    app.processEvents()
    return app, w, vp
