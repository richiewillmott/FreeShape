"""CAD key bindings are independent of UI language and Linux keyboard layout."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QWidget, QLineEdit
from freeshape.ui.keyboard import normalized_key, portable_sequence, ShortcutRouter
from test_sketch_inference import VP, _sketcher


class Key:
    def __init__(self, key, scan=0, mods=Qt.ControlModifier):
        self.k, self.scan, self.mods = key, scan, mods
    def key(self):
        return self.k
    def nativeScanCode(self):
        return self.scan
    def modifiers(self):
        return self.mods


@pytest.mark.parametrize("platform", ["xcb", "wayland", "wayland-egl"])
@pytest.mark.parametrize("logical", ["Z", "Я", "Ζ"])
def test_physical_z_is_z_on_linux(platform, logical):
    assert normalized_key(Key(ord(logical),52),platform)==ord("Z")


@pytest.mark.parametrize("platform", ["xcb", "wayland", "wayland-egl"])
@pytest.mark.parametrize("logical,scan", [("Z",29), ("A",24), ("Y",52), ("Q",38), ("W",52)])
def test_latin_layout_preserves_logical_letter(platform, logical, scan):
    assert normalized_key(Key(ord(logical),scan),platform)==ord(logical)


@pytest.mark.parametrize("ru,en", [("Я","Z"),("Н","Y"),("С","C"),("М","V"),("Д","L"),("Ф","A")])
def test_russian_logical_fallback(ru,en):
    assert normalized_key(Key(ord(ru)), "offscreen")==ord(en)


def test_portable_sequences():
    for text in ("Ctrl+Z","Ctrl+Y","Ctrl+Shift+Z","Ctrl+C","Ctrl+V","Shift+S"):
        assert portable_sequence(text).toString(QKeySequence.SequenceFormat.PortableText)==text


def test_direct_sketch_undo_redo(monkeypatch):
    s=_sketcher()
    called=[]
    s.undo=lambda: called.append("undo")
    s.redo=lambda: called.append("redo")
    for letter in ("Z","Я"):
        assert s.key_press(VP,Key(ord(letter)))
        assert s.key_press(VP,Key(ord(letter),mods=Qt.ControlModifier|Qt.ShiftModifier))
    assert called==["undo","redo","undo","redo"]


def test_router_dispatches_once_and_leaves_text_input():
    app=QApplication.instance() or QApplication([])
    window=QWidget()
    edit=QLineEdit(window)
    router=ShortcutRouter(window)
    called=[]
    router.bind("Ctrl+Z",lambda:called.append("undo"))
    router.bind("Ctrl+Shift+Z",lambda:called.append("redo"))
    def ev(kind,key,mods=Qt.ControlModifier):
        return QKeyEvent(kind,ord(key),mods)
    try:
        for ch in ("Z","Я"):
            assert router.eventFilter(window,ev(QEvent.ShortcutOverride,ch))
            assert router.eventFilter(window,ev(QEvent.KeyPress,ch))
            assert router.eventFilter(window,ev(QEvent.KeyPress,ch,Qt.ControlModifier|Qt.ShiftModifier))
        assert called==["undo","redo","undo","redo"]
        assert not router.eventFilter(edit,ev(QEvent.ShortcutOverride,"Я"))
        assert not router.eventFilter(edit,ev(QEvent.KeyPress,"Я"))
        assert len(called)==4
    finally:
        window.close()


def test_router_scoped_to_window_and_viewport():
    app=QApplication.instance() or QApplication([])
    first, second=QWidget(), QWidget()
    first.viewport=QWidget(first)
    second.viewport=QWidget(second)
    a,b=ShortcutRouter(first),ShortcutRouter(second)
    a.attach_viewport(first.viewport)
    b.attach_viewport(second.viewport)
    called=[]
    a.bind("Ctrl+Z",lambda:called.append("first"))
    b.bind("Ctrl+Z",lambda:called.append("second"))
    def ev(kind):
        return QKeyEvent(kind,ord("Я"),Qt.ControlModifier)
    try:
        for widget in (first.viewport, second.viewport, first):
            app.sendEvent(widget,ev(QEvent.ShortcutOverride))
            app.sendEvent(widget,ev(QEvent.KeyPress))
        assert called==["first","second","first"]
        assert not a.eventFilter(second.viewport,ev(QEvent.KeyPress))
    finally:
        first.close()
        second.close()


def test_actual_qshortcut_and_text_focus():
    from PySide6.QtGui import QShortcut
    from PySide6.QtTest import QTest

    app=QApplication.instance() or QApplication([])
    window=QWidget()
    window.viewport=QWidget(window)
    window.viewport.setFocusPolicy(Qt.StrongFocus)
    edit=QLineEdit(window)
    router=ShortcutRouter(window)
    router.attach_viewport(window.viewport)
    called=[]
    sequence=router.bind("Ctrl+Z",lambda:called.append("undo"))
    shortcut=QShortcut(sequence,window)
    shortcut.activated.connect(lambda:called.append("qt-undo"))
    try:
        window.show()
        window.activateWindow()
        window.viewport.setFocus()
        app.processEvents()
        QTest.keyClick(window.viewport,Qt.Key_Z,Qt.ControlModifier)
        assert called==["undo"]
        edit.setFocus()
        app.processEvents()
        QTest.keyClicks(edit,"abc")
        QTest.keyClick(edit,Qt.Key_Z,Qt.ControlModifier)
        assert edit.text()==""
        assert called==["undo"]
    finally:
        window.close()
