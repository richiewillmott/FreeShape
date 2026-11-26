"""Qt Linguist catalogs, UI translations and stable command metadata."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication
from freeshape.i18n import catalog_path, current_language, initialize, tr

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def language(monkeypatch):
    app = QApplication.instance() or QApplication([])
    def select(value):
        monkeypatch.setenv("FREESHAPE_LANG", value)
        initialize(app)
        return app
    yield select
    select("en")


def test_russian(language):
    language("ru_RU.UTF-8")
    assert current_language() == "ru"
    assert tr("Save") == "Сохранить"
    assert tr("Unknown string") == "Unknown string"


def test_english(language):
    language("ru")
    language("en_US.UTF-8")
    assert current_language() == "en"
    assert tr("Save") == "Save"


def test_ru_catalog(language):
    language("en")
    translator = QTranslator()
    assert translator.load(str(catalog_path("ru")))
    assert not translator.isEmpty()
    messages = ET.parse(ROOT / "freeshape/locale/ru.ts").findall(".//message")
    active = [m for m in messages if m.find("translation").get("type") not in ("vanished", "obsolete")]
    assert len(active) >= 800
    for m in active:
        assert m.findtext("source")
        assert m.findtext("translation")
        assert m.find("translation").get("type") != "unfinished"
        assert translator.translate("FreeShape", m.findtext("source")) == m.findtext("translation")


def test_cad_vocabulary(language):
    language("ru")
    for source in ("Thickness", "Chamfer type", "Mates", "Show constraints",
                   "Bend radius (inside)", "Dimension", "Features",
                   "Both sides equally", "Translate by XYZ"):
        assert tr(source) != source


def test_russian_widgets(language):
    language("ru")
    from freeshape.ui.dialogs import SelectionField
    from freeshape.ui.shortcuts_help import ShortcutsDialog
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


def test_russian_s_menu_metadata(language, tmp_path):
    language("ru")
    from PySide6.QtCore import QSettings
    from freeshape.ui import shortcut_menu as S
    from test_nav import _window
    app, w, vp = _window()
    w.settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    try:
        commands = {c["id"]: c for c in S.commands(w, "sketch")}
        assert S.chosen(w, "sketch") == S.DEFAULTS["sketch"]
        finish = commands["finish_sketch"]
        assert "Завершить" in finish["label"]
        assert not finish["icon"].isNull()
        fillet = commands["sk_fillet"]
        assert fillet["key"] == "Shift+F"
        # A translator is free to change the tooltip format.
        for action, icon_name in w._menu_actions:
            if icon_name == "sk_fillet":
                action.setToolTip("Произвольная подсказка без клавиш")
        assert next(c for c in S.commands(w, "sketch") if c["id"] == "sk_fillet")["key"] == "Shift+F"
    finally:
        w.close()


def test_catalog_extraction_is_current():
    import shutil
    import subprocess
    import sys
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join((str(Path(sys.executable).parent), env.get("PATH", "")))
    if not shutil.which("pyside6-lupdate", path=env["PATH"]):
        pytest.skip("Qt Linguist extraction tool is not installed")
    subprocess.run([sys.executable, "tools/update_translations.py", "--check"],
                   cwd=ROOT, env=env, check=True)
