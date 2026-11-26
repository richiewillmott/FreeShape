"""Layout-independent CAD shortcuts, separate from translated UI strings.

Qt's logical Latin letters take precedence (including QWERTZ, AZERTY,
Dvorak and Colemak). For non-Latin keys, Linux XKB scan codes use standard
PC key positions; Russian ЙЦУКЕН aliases cover platforms without native
scan codes. Text input is left to Qt.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QLineEdit, QTextEdit, QPlainTextEdit,
    QAbstractSpinBox, QComboBox,
)

_XKB_LETTERS = {
    code: ord(letter)
    for start, row in ((24, "QWERTYUIOP"), (38, "ASDFGHJKL"), (52, "ZXCVBNM"))
    for code, letter in enumerate(row, start)
}
_RUSSIAN = {ord(ru): ord(en) for ru, en in zip(
    "ЙЦУКЕНГШЩЗФЫВАПРОЛДЯЧСМИТЬ", "QWERTYUIOPASDFGHJKLZXCVBNM"
)}
_RUSSIAN.update({ord(chr(k).lower()): v for k, v in tuple(_RUSSIAN.items())})
_MODS = Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier


def portable_sequence(text):
    return QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)


def normalized_key(event, platform=None):
    """Return the shortcut key, never alter the actual text in the Qt event."""
    key = int(event.key())
    if ord("A") <= key <= ord("Z"):
        return key
    if platform is None:
        platform = QGuiApplication.platformName() if QGuiApplication.instance() else ""
    scan = event.nativeScanCode() if hasattr(event, "nativeScanCode") else 0
    if (platform == "xcb" or platform.startswith("wayland")) and scan in _XKB_LETTERS:
        return _XKB_LETTERS[scan]
    return _RUSSIAN.get(key, key)


def _signature(key, modifiers):
    return int(key), (modifiers & _MODS).value


def _is_text_input(widget):
    while widget is not None:
        if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
            return True
        if isinstance(widget, QComboBox) and widget.isEditable():
            return True
        widget = widget.parentWidget()
    return False


class ShortcutRouter(QObject):
    """Intercept only registered letter shortcuts in our main window.

    ShortcutOverride precedes Qt's own QShortcut matching. Accepting it makes
    the following KeyPress reach this router, so the command executes once
    even if Qt also recognizes the current logical key. Non-letter shortcuts,
    other windows, dialogs and all text-entry widgets keep their Qt behavior.
    """
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.bindings = {}
        window.installEventFilter(self)

    def attach_viewport(self, viewport):
        """Also catch keys handled directly by the graphics viewport."""
        viewport.installEventFilter(self)

    def bind(self, text, callback):
        seq = portable_sequence(text)
        if seq.count() == 1 and not seq.isEmpty():
            comb = seq[0]
            if ord("A") <= int(comb.key()) <= ord("Z"):
                self.bindings[_signature(comb.key(), comb.keyboardModifiers())] = callback
        return seq

    def _command(self, event):
        key = normalized_key(event)
        if not ord("A") <= key <= ord("Z"):
            return None
        command = self.bindings.get(_signature(key, event.modifiers()))
        if command:
            return command
        # Local sketch shortcuts that have no window-wide QShortcut.
        sk = getattr(self.window, "sketcher", None)
        vp = getattr(self.window, "viewport", None)
        if getattr(self.window, "mode", None) != "sketch" or sk is None or vp is None:
            return None
        mods = event.modifiers() & _MODS
        if (sk.key_tool(key, mods) or
                (key == ord("Q") and not mods) or
                (key == ord("A") and mods == Qt.ControlModifier)):
            return lambda: (sk.key_press(vp, event), vp.update())
        return None

    def eventFilter(self, watched, event):
        if event.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        if watched is not self.window and watched is not getattr(self.window, "viewport", None):
            return False
        focus = QApplication.focusWidget()
        if _is_text_input(watched) or (focus is not None and _is_text_input(focus)):
            return False
        command = self._command(event)
        if command is None:
            return False
        event.accept()
        if event.type() == QEvent.KeyPress:
            command()
        return True
