"""The keyboard shortcuts window (Document → Keyboard shortcuts, or ?): every key in a two-column table, grouped,
with a box to search it."""
from __future__ import annotations

from PySide6.QtCore import QCoreApplication
from ..i18n import QT_TRANSLATE_NOOP

from ..i18n import tr

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QDialogButtonBox, QLabel)

SHORTCUTS = [
    (QT_TRANSLATE_NOOP("FreeShape", "Navigation"), [
        ("Right-drag", QT_TRANSLATE_NOOP("FreeShape", "Rotate the view")),
        ("Middle-drag, Ctrl+right-drag", QT_TRANSLATE_NOOP("FreeShape", "Pan")),
        ("Wheel", QT_TRANSLATE_NOOP("FreeShape", "Zoom at the mouse")),
        ("Double-click the wheel", "Zoom to fit"),
        ("F", "Zoom to fit"),
        ("Z", QT_TRANSLATE_NOOP("FreeShape", "Zoom out")),
        ("W, Shift+Z", QT_TRANSLATE_NOOP("FreeShape", "Zoom to window (drag a box; Esc cancels)")),
        ("Arrow keys", QT_TRANSLATE_NOOP("FreeShape", "Turn the view 15°")),
        ("Shift+arrow", QT_TRANSLATE_NOOP("FreeShape", "Turn the view 90°")),
        ("Ctrl+arrow", QT_TRANSLATE_NOOP("FreeShape", "Turn the view 5°")),
        ("Ctrl+Shift+arrow", QT_TRANSLATE_NOOP("FreeShape", "Pan")),
        ("Shift+1 … Shift+7", QT_TRANSLATE_NOOP("FreeShape", "Front, Back, Left, Right, Top, Bottom, Isometric view")),
        ("N", QT_TRANSLATE_NOOP("FreeShape", "Normal to the face / plane / sketch (again: from the other side)")),
        ("Alt+Left", "Previous view"),
        ("Shift+V", "Named views"),
    ]),
    (QT_TRANSLATE_NOOP("FreeShape", "Showing"), [
        ("P", QT_TRANSLATE_NOOP("FreeShape", "Default planes on / off")),
        ("Shift+P", QT_TRANSLATE_NOOP("FreeShape", "Hide (or show) all planes and sketches")),
        ("Shift+H", QT_TRANSLATE_NOOP("FreeShape", "Hide (or show) sketches")),
        ("Y", QT_TRANSLATE_NOOP("FreeShape", "Hide the selected part (or the one under the mouse)")),
        ("Shift+Y", QT_TRANSLATE_NOOP("FreeShape", "Show hidden parts")),
        ("Shift+I", QT_TRANSLATE_NOOP("FreeShape", "Isolate the selected part (or the one under the mouse)")),
        ("Shift+X", "Section view"),
        ("Shift+R", QT_TRANSLATE_NOOP("FreeShape", "View in high quality")),
    ]),
    (QT_TRANSLATE_NOOP("FreeShape", "Selecting"), [
        ("Click, Shift / Ctrl+click", QT_TRANSLATE_NOOP("FreeShape", "Select, add to the selection")),
        ("Drag a box", QT_TRANSLATE_NOOP("FreeShape", "Left to right: what is fully inside · right to left: also what it touches")),
        ("Space", QT_TRANSLATE_NOOP("FreeShape", "Deselect all")),
        ("Tab / Shift+Tab", QT_TRANSLATE_NOOP("FreeShape", "Step through everything under the mouse; click selects the one shown")),
        ("` (back quote)", QT_TRANSLATE_NOOP("FreeShape", "Select other…: list everything under the mouse")),
        ("Right-click", "Select other…, tangent-connected, loop, same size …"),
    ]),
    (QT_TRANSLATE_NOOP("FreeShape", "Features and tools"), [
        ("S", QT_TRANSLATE_NOOP("FreeShape", "Your tools menu at the mouse (Customize… to choose them)")),
        ("Alt+C, Ctrl+Space", QT_TRANSLATE_NOOP("FreeShape", "Search tools")),
        ("Shift+S", "New sketch"),
        ("Shift+E", QT_TRANSLATE_NOOP("FreeShape", "Extrude (from a sketch: finish it and extrude)")),
        ("Shift+W", QT_TRANSLATE_NOOP("FreeShape", "Revolve (from a sketch: finish it and revolve)")),
        ("Shift+F", QT_TRANSLATE_NOOP("FreeShape", "Fillet")),
        ("Enter", QT_TRANSLATE_NOOP("FreeShape", "Accept the dialog")),
        ("Shift+Enter", QT_TRANSLATE_NOOP("FreeShape", "Accept and start another of the same")),
        ("Esc", QT_TRANSLATE_NOOP("FreeShape", "Cancel / stop the tool")),
        ("Delete", QT_TRANSLATE_NOOP("FreeShape", "Delete what's selected")),
        ("Shift+N", QT_TRANSLATE_NOOP("FreeShape", "Rename the selected feature or part")),
        ("Ctrl+C, Ctrl+V", QT_TRANSLATE_NOOP("FreeShape", "Copy / paste the features selected in the feature list (pasted at the rollback bar)")),
        ("Ctrl+Z, Ctrl+Y", QT_TRANSLATE_NOOP("FreeShape", "Undo, redo")),
        ("?", QT_TRANSLATE_NOOP("FreeShape", "This list")),
    ]),
    ("Sketch", [
        ("L", QT_TRANSLATE_NOOP("FreeShape", "Line")), ("R", QT_TRANSLATE_NOOP("FreeShape", "Corner rectangle")), ("C", QT_TRANSLATE_NOOP("FreeShape", "Circle")), ("A", QT_TRANSLATE_NOOP("FreeShape", "3-point arc")),
        ("Shift+A", QT_TRANSLATE_NOOP("FreeShape", "Tangent arc")), ("P, Shift+S", QT_TRANSLATE_NOOP("FreeShape", "Point")), ("D", "Dimension"), ("Q", QT_TRANSLATE_NOOP("FreeShape", "Construction")),
        ("U", QT_TRANSLATE_NOOP("FreeShape", "Use (project) part edges")), ("O", QT_TRANSLATE_NOOP("FreeShape", "Offset")), ("M", QT_TRANSLATE_NOOP("FreeShape", "Trim")), ("X", QT_TRANSLATE_NOOP("FreeShape", "Extend")),
        ("Shift+X", QT_TRANSLATE_NOOP("FreeShape", "Polygon (a hexagon to start)")), ("Shift+F", QT_TRANSLATE_NOOP("FreeShape", "Sketch fillet")),
        ("I", QT_TRANSLATE_NOOP("FreeShape", "Coincident")), ("H", QT_TRANSLATE_NOOP("FreeShape", "Horizontal")), ("V", QT_TRANSLATE_NOOP("FreeShape", "Vertical")), ("E", QT_TRANSLATE_NOOP("FreeShape", "Equal")), ("T", QT_TRANSLATE_NOOP("FreeShape", "Tangent")),
        ("B", QT_TRANSLATE_NOOP("FreeShape", "Parallel")), ("Shift+L", QT_TRANSLATE_NOOP("FreeShape", "Perpendicular")), ("Shift+O", QT_TRANSLATE_NOOP("FreeShape", "Concentric")), ("Shift+M", QT_TRANSLATE_NOOP("FreeShape", "Midpoint")),
        ("Shift+Q", QT_TRANSLATE_NOOP("FreeShape", "Symmetric")), ("Shift+J", QT_TRANSLATE_NOOP("FreeShape", "Fix")),
        ("Delete", QT_TRANSLATE_NOOP("FreeShape", "Delete what's selected")), ("Esc", QT_TRANSLATE_NOOP("FreeShape", "Stop the tool")),
    ]),
    (QT_TRANSLATE_NOOP("FreeShape", "Assembly"), [
        ("I", "Insert parts"), ("M, Shift+F", QT_TRANSLATE_NOOP("FreeShape", "Fastened mate")), ("Shift+R", QT_TRANSLATE_NOOP("FreeShape", "Revolute mate")),
        ("Shift+L", QT_TRANSLATE_NOOP("FreeShape", "Slider mate")), ("Left-drag a part", QT_TRANSLATE_NOOP("FreeShape", "Move it")), ("Delete", QT_TRANSLATE_NOOP("FreeShape", "Delete what's selected")),
    ]),
    ("Drawing", [
        ("P", QT_TRANSLATE_NOOP("FreeShape", "Projected view")), ("N", QT_TRANSLATE_NOOP("FreeShape", "Note")),
    ]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(QCoreApplication.translate("FreeShape", "Keyboard shortcuts"))
        self.resize(620, 720)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(QCoreApplication.translate("FreeShape", "Search keys or actions — e.g. trim, Shift+X, planes"))
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([QCoreApplication.translate("FreeShape", "Keys"), QCoreApplication.translate("FreeShape", "What it does")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        bold = QFont()
        bold.setBold(True)
        self.rows = []                                     # (row, section row, text to search) per shortcut
        for section, items in SHORTCUTS:
            r = self.table.rowCount()
            self.table.insertRow(r)
            head = QTableWidgetItem(tr(section))
            head.setFont(bold)
            head.setBackground(QBrush(QColor(31, 111, 209, 28)))        # a light band marks each section
            head.setForeground(QBrush(QColor(31, 111, 209)))
            self.table.setItem(r, 0, head)
            self.table.setSpan(r, 0, 1, 2)
            sec_row = r
            for keys, action in items:
                r = self.table.rowCount()
                self.table.insertRow(r)
                k = QTableWidgetItem(keys)
                k.setFont(bold)
                self.table.setItem(r, 0, k)
                self.table.setItem(r, 1, QTableWidgetItem(tr(action)))
                self.rows.append((r, sec_row, f"{section} {tr(section)} {keys} {action} {tr(action)}".lower()))
        self.table.resizeRowsToContents()
        lay.addWidget(self.table, 1)
        self.none = QLabel(QCoreApplication.translate("FreeShape", "No shortcut matches that."))
        self.none.setStyleSheet("color:#8a94a3;")
        self.none.hide()
        lay.addWidget(self.none)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        bb.accepted.connect(self.accept)
        lay.addWidget(bb)
        self.search.textChanged.connect(self.filter)
        self.search.setFocus()

    def filter(self, text=""):
        words = text.lower().split()
        shown_sections = set()
        for r, sec, hay in self.rows:
            ok = all(w in hay for w in words)
            self.table.setRowHidden(r, not ok)
            if ok:
                shown_sections.add(sec)
        for r, sec, _hay in self.rows:
            self.table.setRowHidden(sec, sec not in shown_sections)
        self.none.setVisible(not shown_sections)

    def visible_keys(self):
        return [self.table.item(r, 0).text() for r, _s, _h in self.rows if not self.table.isRowHidden(r)]
