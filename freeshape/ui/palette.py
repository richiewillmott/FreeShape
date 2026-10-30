"""Command search: type a few letters, pick a tool, press Enter (like Onshape's "Search tools…" and ParaView's
Ctrl+Space). Opens over the 3D view; Esc closes it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel,
                               QApplication, QGraphicsDropShadowEffect)
from PySide6.QtGui import QColor

from .icons import icon


@dataclass
class Command:
    key: str                         # also the icon name
    name: str
    description: str
    action: Callable[[], None]
    keywords: str = ""               # extra words that should find it ("round" finds Fillet)
    group: str = ""


def search_commands(commands, text):
    """Commands matching `text`, best first: every typed word must appear in the name, keywords or description;
    names that start with the text come first, then names that contain it, then the rest."""
    words = text.lower().split()
    if not words:
        return list(commands)
    scored = []
    for i, c in enumerate(commands):
        name = c.name.lower()
        hay = f"{name} {c.keywords.lower()} {c.description.lower()} {c.group.lower()}"
        if not all(w in hay for w in words):
            continue
        if name.startswith(words[0]):
            rank = 0
        elif all(w in name for w in words):
            rank = 1
        elif all(w in f"{name} {c.keywords.lower()}" for w in words):
            rank = 2
        else:
            rank = 3
        scored.append((rank, i, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [c for _r, _i, c in scored]


class CommandPalette(QFrame):
    def __init__(self, parent, get_commands: Callable[[], list]):
        super().__init__(parent)
        self.setObjectName("featureDialog")
        self._get = get_commands
        self._shown = []
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(18)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 70))
        self.setGraphicsEffect(sh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Search tools…")
        self.edit.setClearButtonEnabled(True)
        self.edit.textChanged.connect(self._refill)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)
        body = QHBoxLayout()
        self.list = QListWidget()
        self.list.setFixedWidth(230)
        self.list.setMinimumHeight(220)
        self.list.itemActivated.connect(lambda _it: self.run_current())
        self.list.itemClicked.connect(lambda _it: self.run_current())
        self.list.currentRowChanged.connect(self._describe)
        body.addWidget(self.list)
        right = QVBoxLayout()
        self.title = QLabel("")
        self.title.setStyleSheet("font-size: 15px; font-weight: 700;")
        self.desc = QLabel("")
        self.desc.setWordWrap(True)
        self.desc.setStyleSheet("color:#4b5563;")
        self.desc.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        right.addWidget(self.title)
        right.addWidget(self.desc, 1)
        body.addLayout(right, 1)
        lay.addLayout(body)
        foot = QLabel("Enter: start the tool  ·  ↑ ↓: choose  ·  Esc: close")
        foot.setStyleSheet("color:#8a94a3; font-size: 11px;")
        lay.addWidget(foot)
        self.setFixedSize(520, 330)
        self.hide()
        QApplication.instance().focusChanged.connect(self._focus_changed)

    # ------------------------------------------------------------------ open / close
    def open(self):
        par = self.parentWidget()
        if par is not None:
            self.move(max(8, par.width() - self.width() - 16), 12)
        self.edit.clear()
        self._refill("")
        self.show()
        self.raise_()
        self.edit.setFocus()

    def close_palette(self):
        if self.isVisible():
            self.hide()
            par = self.parentWidget()
            if par is not None:
                par.setFocus()

    def _focus_changed(self, _old, new):
        if self.isVisible() and new is not None and not (new is self or self.isAncestorOf(new)):
            self.hide()

    # ------------------------------------------------------------------ content
    def _refill(self, text):
        self._shown = search_commands(self._get(), text)
        self.list.clear()
        for c in self._shown:
            it = QListWidgetItem(icon(c.key), c.name)
            self.list.addItem(it)
        if self._shown:
            self.list.setCurrentRow(0)
        else:
            self.title.setText("No tool found")
            self.desc.setText("Try another word, for example “round” for Fillet or “hole”.")

    def _describe(self, row):
        if 0 <= row < len(self._shown):
            c = self._shown[row]
            self.title.setText(c.name)
            self.desc.setText(c.description)

    def run_current(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self._shown)):
            return
        cmd = self._shown[row]
        self.close_palette()
        QTimer.singleShot(0, cmd.action)         # after the palette has gone and focus is back on the view

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() == QEvent.KeyPress:
            k = ev.key()
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self.run_current()
                return True
            if k == Qt.Key_Escape:
                self.close_palette()
                return True
            if k in (Qt.Key_Down, Qt.Key_Up):
                n = self.list.count()
                if n:
                    self.list.setCurrentRow((self.list.currentRow() + (1 if k == Qt.Key_Down else -1)) % n)
                return True
        return super().eventFilter(obj, ev)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_palette()
            return
        super().keyPressEvent(e)
