"""Each pick in a feature dialog's selection box has its own × to take it out again, as in Onshape (it could only be
done with a hidden double-click before), and Delete removes the highlighted one."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.ui.viewport import Pick  # noqa: E402


def _fillet_with_three_edges():
    from test_select_other_keys import _win
    app, w, vp = _win()
    pid = next(iter(w.doc.state.parts))
    w.start_feature("fillet")
    app.processEvents()
    d = w.dialog
    for ei in (0, 1, 2):
        d.handle_pick(Pick("edge", (pid, ei)))
    app.processEvents()
    fld = d.active_field
    assert len(fld.items) == 3
    return app, w, d, fld


def test_the_x_on_a_row_removes_just_that_pick():
    app, w, d, fld = _fillet_with_three_edges()
    second = fld.items[1]
    lst = fld.list
    x = lst._x_rect(1).center()
    QTest.mouseMove(lst.viewport(), x)
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, x)
    app.processEvents()
    assert len(fld.items) == 2 and second not in fld.items
    assert lst.count() == 2                                     # the box shows what's left


def test_clicking_the_name_does_not_remove_it():
    app, w, d, fld = _fillet_with_three_edges()
    lst = fld.list
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, lst.visualItemRect(lst.item(0)).center())
    app.processEvents()
    assert len(fld.items) == 3


def test_delete_removes_the_highlighted_pick():
    app, w, d, fld = _fillet_with_three_edges()
    first = fld.items[0]
    lst = fld.list
    lst.setCurrentRow(0)
    lst.setFocus()
    QTest.keyClick(lst, Qt.Key_Delete)
    app.processEvents()
    assert len(fld.items) == 2 and first not in fld.items


def test_the_placeholder_row_has_no_x():
    app, w, d, fld = _fillet_with_three_edges()
    fld.clear()
    app.processEvents()
    assert fld.list.count() == 1 and not fld.list.removable(0)
