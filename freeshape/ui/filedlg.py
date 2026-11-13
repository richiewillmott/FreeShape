"""File dialogs that start in the folder used last (Open, Save as, Import, Export …), remembered between runs.
Same arguments as QFileDialog's, so call sites read the same."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QFileDialog

_KEY = "last_folder"


def _settings():
    return QSettings("FreeShape", "FreeShape")


def last_folder():
    d = _settings().value(_KEY, "")
    return d if d and os.path.isdir(d) else ""


def start_path(path=""):
    """Where a dialog opens: a full path as given, otherwise the name (if any) in the last folder used."""
    path = path or ""
    if os.path.isabs(path) and os.path.isdir(os.path.dirname(path)):
        return path
    d = last_folder()
    return os.path.join(d, path) if d else path


def remember(path):
    if path:
        _settings().setValue(_KEY, os.path.dirname(os.path.abspath(path)))


def getOpenFileName(parent, caption="", path="", filt="", selected=""):
    res = QFileDialog.getOpenFileName(parent, caption, start_path(path), filt, selected)
    remember(res[0])
    return res


def getSaveFileName(parent, caption="", path="", filt="", selected=""):
    res = QFileDialog.getSaveFileName(parent, caption, start_path(path), filt, selected)
    remember(res[0])
    return res
