# Translating FreeShape

English is the source language. Russian is the first optional catalog.
Translations are incremental: the home screen, main menus, CAD operation parameters, sketch hints,
assembly/drawing controls, history, configuration and shortcuts are translated. Some dynamic messages
and advanced controls remain in English.

Under View -> Language, choose System language, English, or Русский. Restart
FreeShape to apply the selection. The default is the desktop's language.
For manual testing the environment variable FREESHAPE_LANG=ru takes precedence.

To contribute, add an English-string key and Russian translation to
freeshape/locale/ru.json, then wrap its source UI label with tr(...) imported
from freeshape.i18n. The main menu helper and MainWindow._add_action
translate automatically. Preserve shortcut labels and placeholders.

Untranslated labels fall back to English. The JSON files are included in the
normal Python package and in the PyInstaller bundle used by Linux packages.
Run: python -m pytest tests/test_i18n.py
