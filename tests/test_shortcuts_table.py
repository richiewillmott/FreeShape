"""The keyboard shortcuts window is a searchable table that matches the keys the sketcher really uses, and a plane
picked in a dialog is outlined at the size it is drawn (resized or not)."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_search_filters_the_table():
    from freeshape.ui.shortcuts_help import ShortcutsDialog
    _app()
    d = ShortcutsDialog()
    everything = d.visible_keys()
    assert len(everything) > 60
    d.search.setText("trim")
    assert d.visible_keys() == ["M"]
    d.search.setText("shift+x")
    assert d.visible_keys() == ["Shift+X", "Shift+X"]           # section view, and the sketch's hexagon
    d.search.setText("planes off")
    assert d.visible_keys() == ["P"]
    d.search.setText("nothing like this")
    assert d.visible_keys() == [] and not d.none.isHidden()
    d.search.setText("")
    assert d.visible_keys() == everything and d.none.isHidden()


def test_sketch_section_matches_the_sketcher_keys():
    from freeshape.ui.shortcuts_help import SHORTCUTS
    from freeshape.ui.sketcher import Sketcher
    sketch = dict(SHORTCUTS)["Sketch"]
    listed = {}
    for keys, action in sketch:
        for k in keys.split(", "):
            listed[k] = action
    for letter, tool in Sketcher.KEYS.items():
        assert letter in listed, f"{letter} ({tool}) missing from the shortcuts table"
    for letter, tool in Sketcher.SHIFT_KEYS.items():
        assert f"Shift+{letter}" in listed, f"Shift+{letter} ({tool}) missing from the shortcuts table"


def test_question_mark_opens_it():
    from PySide6.QtWidgets import QDialog
    from test_nav import _window
    app, w, vp = _window()
    opened = []
    import freeshape.ui.shortcuts_help as SH
    real = SH.ShortcutsDialog.exec
    SH.ShortcutsDialog.exec = lambda self: opened.append(self) or QDialog.Accepted
    try:
        w.show_shortcuts()
    finally:
        SH.ShortcutsDialog.exec = real
    assert opened and opened[0].table.rowCount() > 60


def test_picked_plane_outline_follows_its_resized_size():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    doc.set_plane_rect(("std", "Top"), (-20.0, 50.0, -10.0, 30.0))
    from freeshape.ui.viewport import Pick
    vp.set_selection([Pick("plane", ("std", "Top"))])
    w.start_feature("plane")
    app.processEvents()
    d = w.dialog
    polys = [g for (key, _i), (kind, g) in d.overlays() if kind == "poly"]
    assert polys, "the picked Top plane should be outlined"
    pts = np.asarray(polys[0])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    assert np.allclose([lo[0], hi[0], lo[1], hi[1]], [-20, 50, -10, 30])    # its resized size, not ±75
    w.cancel_dialog()
    doc.set_plane_rect(("std", "Top"), None)
    vp.set_selection([Pick("plane", ("std", "Top"))])
    w.start_feature("plane")
    app.processEvents()
    pts = np.asarray([g for (_k, _i), (kind, g) in w.dialog.overlays() if kind == "poly"][0])
    h = vp.plane_half()
    assert np.allclose([pts[:, 0].min(), pts[:, 0].max()], [-h, h])
    w.cancel_dialog()
