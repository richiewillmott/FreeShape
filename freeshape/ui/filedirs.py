"""Which folder a file dialog opens in: the last one used for that kind, else the document's, else Desktop/home."""
import os

from PySide6.QtCore import QStandardPaths


def _desktop():
    return QStandardPaths.writableLocation(QStandardPaths.DesktopLocation) or ""


def start_dir(settings, kind, doc_path=None, desktop=None):
    """Folder for a `kind` dialog ("doc" for open/save/import, "export" for exports)."""
    remembered = str(settings.value(f"last_dir/{kind}", "") or "") if settings is not None else ""
    if remembered and os.path.isdir(remembered):
        return remembered
    if doc_path and os.path.isdir(os.path.dirname(os.path.abspath(doc_path))):
        return os.path.dirname(os.path.abspath(doc_path))
    desk = _desktop() if desktop is None else desktop
    if desk and os.path.isdir(desk):
        return desk
    return os.path.expanduser("~")


def remember_dir(settings, kind, path):
    if settings is not None and path:
        settings.setValue(f"last_dir/{kind}", os.path.dirname(os.path.abspath(path)))
