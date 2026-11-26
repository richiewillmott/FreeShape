"""Menu helpers."""
from __future__ import annotations

from PySide6.QtWidgets import QMenu

from ..i18n import tr


def submenu(parent: QMenu, title: str) -> QMenu:
    """A submenu owned by `parent`.

    Use this instead of `parent.addMenu(title)`: from PySide6 6.11 the menu that call makes is deleted as soon as
    Python drops its reference, leaving the parent with an empty, broken entry. A QMenu created with a parent
    lives as long as the parent does."""
    m = QMenu(tr(title), parent)
    parent.addMenu(m)
    return m
