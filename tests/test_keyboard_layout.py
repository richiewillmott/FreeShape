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
@pytest.mark.parametrize("logical", ["Z", "Я", "Y", "Ζ"])
def test_physical_z_is_z_on_linux(platform, logical):
    assert normalized_key(Key(ord(logical),52),platform)==ord("Z")


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
        app.removeEventFilter(router)
        window.close()
