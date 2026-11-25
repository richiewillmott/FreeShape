"""Check interference in a Part Studio: right-click several parts ▸ Check interference shows where they overlap."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.interference import overlaps  # noqa: E402


def _box(x, y, z, dx, dy, dz):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    return BRepPrimAPI_MakeBox(gp_Pnt(x, y, z), dx, dy, dz).Shape()


def test_overlaps_finds_each_overlapping_pair_and_its_volume():
    shapes = {"a": _box(0, 0, 0, 10, 10, 10), "b": _box(5, 0, 0, 10, 10, 10),       # 5 x 10 x 10 overlap
              "c": _box(15, 0, 0, 10, 10, 10),                                        # touches b only
              "d": _box(100, 0, 0, 1, 1, 1)}                                          # far away
    res = overlaps(shapes)
    assert [(a, b) for a, b, _v, _s in res] == [("a", "b")]
    assert abs(res[0][2] - 500.0) < 1e-6


@pytest.fixture
def window(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a[2])))
    from test_list_selection import _two_parts
    app, w, vp = _two_parts()
    return app, w, vp, shown


def test_check_interference_on_two_parts_that_overlap(window):
    app, w, vp, shown = window
    pids = list(w.doc.state.parts)
    mv = w.doc.features[-1]                                  # the copy: move it so it overlaps the original
    lo, hi = K.bbox(w.doc.state.parts[pids[0]].shape)
    w.doc.replace_feature(dict(mv, params=dict(mv["params"], dx=float(hi[0] - lo[0]) / 2)))
    app.processEvents()
    res = w.check_interference(list(w.doc.state.parts))
    assert len(res) == 1 and res[0][2] > 0
    assert vp.ghosts and "overlap" in shown[-1]
    w.doc.replace_feature(dict(mv, params=dict(mv["params"], dx=500.0)))     # the model changes …
    app.processEvents()
    assert vp.ghosts == []                                   # … and the old red overlap goes


def test_check_interference_when_nothing_overlaps(window):
    app, w, vp, shown = window
    assert w.check_interference(list(w.doc.state.parts)) == []
    assert not vp.ghosts and "No interference" in shown[-1]


def test_the_menu_offers_it_for_several_parts(window, monkeypatch):
    from PySide6.QtWidgets import QMenu
    from freeshape.ui import mainwindow as MW
    app, w, vp, _shown = window
    pids = list(w.doc.state.parts)
    w.left.parts.select_parts(pids)
    seen = []

    class _Menu(QMenu):                                        # shows nothing: notes what the menu offers
        def exec(self, *a):
            seen.extend(x.text() for x in self.actions())
    monkeypatch.setattr(MW, "QMenu", _Menu)
    w._part_context(pids[0], None)
    assert "Check interference" in seen
