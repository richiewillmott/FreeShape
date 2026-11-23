"""The S menu: a customisable grid of tools at the mouse, one menu each for sketch, Part Studio, assembly and
drawing, kept in the settings."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402
from PySide6.QtCore import Qt, QSettings  # noqa: E402

from freeshape.ui import shortcut_menu as S  # noqa: E402


@pytest.fixture
def win(tmp_path):
    from test_nav import _window
    app, w, vp = _window()
    w.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)    # don't touch the real settings
    return w


def test_each_place_has_its_tools_and_defaults(win):
    for ctx in ("sketch", "studio", "assembly", "drawing"):
        ids = [c["id"] for c in S.commands(win, ctx)]
        assert len(ids) == len(set(ids)) > 5
        assert S.chosen(win, ctx) == S.DEFAULTS[ctx]                    # every default is a real tool
    sk = [c["id"] for c in S.commands(win, "sketch")]
    assert {"line_mid", "polygon_out", "sk_extend", "insert_dxf", "finish_sketch", "select"} <= set(sk)
    assert "extrude" in [c["id"] for c in S.commands(win, "studio")]


def test_choices_are_kept_per_place(win):
    S.save(win, "sketch", ["circle", "trim", "no_such_tool"])
    assert S.chosen(win, "sketch") == ["circle", "trim"]                 # unknown ids dropped
    assert S.chosen(win, "studio") == S.DEFAULTS["studio"]              # other places untouched
    S.reset(win, "sketch")
    assert S.chosen(win, "sketch") == S.DEFAULTS["sketch"]


def test_popup_runs_the_tool_and_number_keys_work(win):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    S.save(win, "studio", ["fillet", "extrude"])
    started = []
    win.start_feature = lambda t, *a: started.append(t)
    # the toolbar buttons were connected to the old start_feature: rebuild what they call through the window
    pop = S.ShortcutPopup(win, "studio")
    assert [c["id"] for c in pop.items] == ["fillet", "extrude"]
    assert pop.buttons[0].text() == "Fillet"
    ran = []
    pop.items[1]["run"] = lambda: ran.append("extrude")
    pop.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_2, Qt.NoModifier))
    QApplication.processEvents()
    assert ran == ["extrude"]


def test_popup_clicks_a_real_sketch_tool(win):
    from PySide6.QtWidgets import QApplication
    from freeshape.ui.viewport import Pick
    win.viewport.set_selection([Pick("plane", ("std", "Top"))])
    win.start_sketch()
    assert win.mode == "sketch" and S.current_context(win) == "sketch"
    pop = S.ShortcutPopup(win, "sketch")
    i = [c["id"] for c in pop.items].index("circle")
    pop.buttons[i].click()
    QApplication.processEvents()
    assert win.sketcher.tool == "circle"
    win.cancel_sketch()


def test_customize_dialog_adds_orders_and_saves(win):
    d = S.CustomizeDialog(win, "drawing")
    assert d.ids() == S.DEFAULTS["drawing"]
    d.mine.setCurrentRow(0)
    d.remove_selected()
    assert "base_view" not in d.ids()
    row = next(i for i in range(d.avail.count()) if d.avail.item(i).data(Qt.UserRole) == "pdf")
    d.avail.setCurrentRow(row)
    d.avail.item(row).setSelected(True)
    d.add_selected()
    d.mine.setCurrentRow(d.mine.count() - 1)
    d.move_selected(-1)
    assert d.ids()[-2] == "pdf"
    d.ctx_box.setCurrentIndex(d.ctx_box.findData("sketch"))           # switching keeps the drawing edits
    d.ctx_box.setCurrentIndex(d.ctx_box.findData("drawing"))
    assert d.ids()[-2] == "pdf"
    d.accept()
    assert S.chosen(win, "drawing")[-2] == "pdf" and "base_view" not in S.chosen(win, "drawing")
    d2 = S.CustomizeDialog(win, "drawing")
    d2.reset_current()
    d2.accept()
    assert S.chosen(win, "drawing") == S.DEFAULTS["drawing"]


def test_s_in_a_sketch_no_longer_picks_select():
    from freeshape.sketch import Sketch
    from freeshape import kernel as K
    from freeshape.ui.sketcher import Sketcher
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    s = Sketcher(K.std_plane("Top"), Sketch(), "f1", lambda *a: None)
    s.set_tool("line")
    assert not s.key_press(None, QKeyEvent(QEvent.KeyPress, Qt.Key_S, Qt.NoModifier))
    assert s.tool == "line"
