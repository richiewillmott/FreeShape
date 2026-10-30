"""Command search."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape.ui.palette import Command, search_commands  # noqa: E402


def _cmds():
    nop = lambda: None
    return [Command("fillet", "Fillet", "Round off edges.", nop, "round blend"),
            Command("chamfer", "Chamfer", "Create beveled edges or faces.", nop, "bevel"),
            Command("delete_face", "Delete face", "Remove faces from a part.", nop, "defeature heal"),
            Command("move_face", "Move face", "Push or pull flat faces.", nop, "offset"),
            Command("shell", "Shell", "Hollow a part, leaving the chosen face open.", nop, "hollow"),
            Command("hole", "Hole", "Drill holes.", nop, "")]


def test_search_ranks_names_first_and_finds_keywords():
    c = _cmds()
    assert [x.name for x in search_commands(c, "cham")] == ["Chamfer"]
    assert search_commands(c, "round")[0].name == "Fillet"                  # a keyword
    assert [x.name for x in search_commands(c, "face")][:2] == ["Delete face", "Move face"]    # names before descriptions
    assert "Shell" in [x.name for x in search_commands(c, "face")]            # ... then ones that only mention it
    assert search_commands(c, "xyz") == []
    assert len(search_commands(c, "")) == len(c)
    assert [x.name for x in search_commands(c, "del face")] == ["Delete face"]   # every word must match


def test_palette_runs_the_chosen_command_on_enter():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.palette import CommandPalette
    ran = []
    cmds = [Command("fillet", "Fillet", "Round off edges.", lambda: ran.append("fillet")),
            Command("chamfer", "Chamfer", "Bevel edges.", lambda: ran.append("chamfer"))]
    host = QWidget()
    host.resize(900, 600)
    host.show()
    pal = CommandPalette(host, lambda: cmds)
    pal.open()
    QTest.keyClicks(pal.edit, "cham")
    assert pal.list.count() == 1 and pal.title.text() == "Chamfer"
    QTest.keyClick(pal.edit, Qt.Key_Return)
    app.processEvents()
    assert ran == ["chamfer"] and not pal.isVisible()
    pal.open()
    QTest.keyClick(pal.edit, Qt.Key_Escape)
    assert not pal.isVisible()
