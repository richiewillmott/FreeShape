"""Type # in a value box and a list of the variables you can use drops down (with their values); pick one with the
mouse or the arrow keys and Enter. Works part-way through an expression too: `#wall * 2 + #g` offers #gap …"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QObject
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter

_TOKEN = re.compile(r"#(\w*)$")
NAME_ROLE = Qt.UserRole + 1


def _value_text(v, kind):
    from .numparse import fmt
    from .. import units as U
    if isinstance(v, str):
        return f'"{v}"'
    try:
        if kind == "length":
            return U.length(float(v))
        if kind == "angle":
            return f"{fmt(float(v))}°"
        return fmt(float(v))
    except (TypeError, ValueError):
        return str(v)


class VarCompleter(QObject):
    """Attach to a QLineEdit: get_vars() -> {name: value}, get_kinds() -> {name: kind} (both looked up each time,
    so a variable made a moment ago is offered too)."""

    def __init__(self, edit, get_vars, get_kinds=lambda: {}):
        super().__init__(edit)
        self.edit, self.get_vars, self.get_kinds = edit, get_vars, get_kinds
        self.model = QStandardItemModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(edit)
        self.completer.setCompletionRole(NAME_ROLE)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.activated[str].connect(self._insert)
        edit.textEdited.connect(self._update)

    def _token(self):
        text, pos = self.edit.text(), self.edit.cursorPosition()
        return _TOKEN.search(text[:pos])

    def _fill(self):
        self.model.clear()
        kinds = self.get_kinds() or {}
        for name, v in sorted((self.get_vars() or {}).items(), key=lambda kv: kv[0].lower()):
            it = QStandardItem(f"#{name}   = {_value_text(v, kinds.get(name))}")
            it.setData(f"#{name}", NAME_ROLE)
            it.setEditable(False)
            self.model.appendRow(it)

    def _update(self, _text=None):
        m = self._token()
        if m is None:
            self.completer.popup().hide()
            return
        self._fill()
        self.completer.setCompletionPrefix("#" + m.group(1))
        if self.completer.completionCount() == 0:
            self.completer.popup().hide()
            return
        r = self.edit.cursorRect()
        r.setWidth(max(220, self.completer.popup().sizeHintForColumn(0) + 30))
        self.completer.complete(r)

    def _insert(self, name):
        m = self._token()
        text, pos = self.edit.text(), self.edit.cursorPosition()
        start = m.start() if m else pos
        new = text[:start] + name + text[pos:]
        self.edit.setText(new)
        self.edit.setCursorPosition(start + len(name))
        self.edit.textEdited.emit(new)                  # so the box re-checks what's in it


def attach(edit, get_vars, get_kinds=lambda: {}):
    """Give a value box the # drop-down; returns the helper (kept alive by the box)."""
    return VarCompleter(edit, get_vars, get_kinds)


VALID_NAME = re.compile(r"^[A-Za-z_]\w*$")


def missing_variable(text, variables):
    """The one variable `text` uses that doesn't exist yet (so it could be made on the fly), or None."""
    from ..expr import var_names
    missing = [n for n in var_names(text) if n not in variables]
    names = sorted(set(missing))
    if len(names) == 1 and VALID_NAME.match(names[0]):
        return names[0]
    return None
