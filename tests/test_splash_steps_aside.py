"""The start-up splash stays on top, so it must step aside when a message box opens while FreeShape starts (a file
that needs a rebuild, a recovered autosave…) — otherwise the box hides under it and nothing can be clicked."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def test_a_message_box_while_starting_closes_the_splash():
    app = _app()
    from freeshape.ui.splash import Splash
    s = Splash()
    s.show()
    seen = {}

    def ask():
        box = QMessageBox(QMessageBox.Warning, "Rebuild", "This file needs a rebuild.")
        QTimer.singleShot(0, lambda: (seen.setdefault("splash_visible", s.isVisible()), box.accept()))
        box.exec()

    w = QMainWindow()
    w.show()
    assert s.isVisible()                       # the main window alone doesn't close it
    QTimer.singleShot(0, ask)
    s.finish(w)                                # the box opens during the splash's last moment
    assert seen == {"splash_visible": False}
    assert not s.isVisible()
    s.step("late", 1.0)                        # harmless once gone
    s.dismiss()
    w.close()


def test_without_a_dialog_the_splash_closes_normally():
    app = _app()
    from freeshape.ui.splash import Splash
    s = Splash()
    s.MIN_SECONDS = 0.05
    s.show()
    w = QMainWindow()
    w.show()
    s.finish(w)
    assert not s.isVisible() and s._gone
    w.close()
