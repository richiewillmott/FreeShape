# Translating FreeShape

English is the source language. Russian is the first optional Qt Linguist
catalog. Under View -> Language, choose System language, English, or Русский.
Restart FreeShape to apply the selection. The environment variable
`FREESHAPE_LANG=ru` overrides the saved choice.

## Adding a UI string

Use `QCoreApplication.translate("FreeShape", "English text")` for strings
translated at display time. For data tables and helper arguments that must
remain English internally, mark the literal with
`QT_TRANSLATE_NOOP("FreeShape", "English text")` (from `freeshape.i18n`)
and use `tr(source)` at the display boundary. Never translate document IDs,
command IDs, shortcut bindings, model parameters or user-supplied values.
For dynamic messages, translate the format string before substituting values.

The Qt translation tools detect marked strings automatically. From an
environment with PySide6 on PATH:

    python tools/update_translations.py
    pyside6-linguist freeshape/locale/ru.ts
    python tools/update_translations.py
    python tools/update_translations.py --check
    python -m pytest tests/test_i18n.py

The TS file is the editable source (also usable in Weblate). The generated QM
is committed so source checkouts work without a translation build step; both
Python packages and PyInstaller include it. Refresh both files after changes.
The check reports unfinished and vanished messages and fails if the source
catalog no longer matches the extracted strings. Untranslated messages fall
back to English. Qt's standard dialogs use Qt's own catalog when available.

Coverage remains incremental: some dynamic messages and advanced controls
still need translation markers and translated text. Add a marker at the
actual source rather than maintaining a separate manual list of English keys.
