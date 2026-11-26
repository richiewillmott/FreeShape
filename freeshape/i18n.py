"""Qt Linguist catalogs and language selection, with English fallback."""
from __future__ import annotations

import os
from pathlib import Path


def _short_language(value):
    return str(value or "en").split(".")[0].replace("-", "_").split("_")[0].lower()


_active_language = None


def _preferred_language():
    """Use FREESHAPE_LANG, a stored preference, or the desktop's language."""
    override = os.environ.get("FREESHAPE_LANG", "").strip()
    if override:
        return _short_language(override)
    try:
        from PySide6.QtCore import QSettings, QLocale
        preference = str(QSettings("FreeShape", "FreeShape").value("language", "system"))
        if preference and preference != "system":
            return _short_language(preference)
        return _short_language(QLocale.system().name())
    except ImportError:
        return _short_language(os.environ.get("LANG", "en"))


def current_language():
    return _active_language if _active_language is not None else _preferred_language()


def QT_TRANSLATE_NOOP(context, source):
    """Mark deferred labels for lupdate without translating model data."""
    return source


def catalog_path(language):
    return Path(__file__).resolve().parent / "locale" / (language + ".qm")


def initialize(app):
    """Install the application and Qt catalogs; restart to change language."""
    global _active_language
    from PySide6.QtCore import QLibraryInfo, QTranslator
    for name in ("_freeshape_translator", "_freeshape_qt_translator"):
        previous = getattr(app, name, None)
        if previous is not None:
            app.removeTranslator(previous)
            delattr(app, name)
    _active_language = _preferred_language()
    if _active_language == "en":
        return
    translator = QTranslator(app)
    if not translator.load(str(catalog_path(_active_language))) or translator.isEmpty():
        return
    app.installTranslator(translator)
    app._freeshape_translator = translator
    directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    qt_translator = QTranslator(app)
    if qt_translator.load("qtbase_" + _active_language, directory):
        app.installTranslator(qt_translator)
        app._freeshape_qt_translator = qt_translator


def tr(source):
    """Translate a deferred UI label previously marked with QT_TRANSLATE_NOOP."""
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.translate("FreeShape", source)
