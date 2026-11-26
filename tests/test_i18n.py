"""Basic translation tests without a graphical session."""
import json
from pathlib import Path

from freeshape.i18n import tr, current_language


def test_russian(monkeypatch):
    monkeypatch.setenv("FREESHAPE_LANG", "ru_RU.UTF-8")
    assert current_language() == "ru"
    assert tr("Save") == "Сохранить"
    assert tr("Unknown string") == "Unknown string"


def test_english(monkeypatch):
    monkeypatch.setenv("FREESHAPE_LANG", "en_US.UTF-8")
    assert tr("Save") == "Save"


def test_ru_catalog():
    path = Path(__file__).resolve().parents[1] / "freeshape" / "locale" / "ru.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) >= 100
    assert all(isinstance(k, str) and isinstance(v, str) and k and v for k, v in data.items())


def test_cad_vocabulary(monkeypatch):
    monkeypatch.setenv("FREESHAPE_LANG", "ru")
    for source in ("Thickness", "Chamfer type", "Mates", "Show constraints",
                   "Bend radius (inside)", "Dimension", "Features"):
        assert tr(source) != source


def test_expanded_catalog():
    path = Path(__file__).resolve().parents[1] / "freeshape" / "locale" / "ru.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) >= 800
    assert all(key.strip() and value.strip() for key, value in data.items())


def test_russian_widgets(monkeypatch):
    import pytest
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("FREESHAPE_LANG", "ru")
    qt = pytest.importorskip("PySide6.QtWidgets")
    pytest.importorskip("OCP")
    from freeshape.ui.dialogs import SelectionField
    from freeshape.ui.shortcuts_help import ShortcutsDialog
    app = qt.QApplication.instance() or qt.QApplication([])
    field = SelectionField("edges", "Edges or faces to fillet", {"edge"})
    assert field.title.text() == "Рёбра или грани для скругления"
    assert field.clear_btn.toolTip() == "Очистить выделение"
    assert field.key == "edges" and field.want == {"edge"}
    dialog = ShortcutsDialog()
    assert dialog.windowTitle() == "Горячие клавиши"
    assert dialog.table.horizontalHeaderItem(0).text() == "Клавиши"
    assert dialog.table.horizontalHeaderItem(1).text() == "Действие"
    dialog.close()
    field.close()
