"""The keyboard shortcuts window (Document → Keyboard shortcuts, or ?): every key in a two-column table, grouped,
with a box to search it."""
from __future__ import annotations

from ..i18n import tr

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor, QBrush
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLineEdit, QTableWidget, QTableWidgetItem, QHeaderView,
                               QAbstractItemView, QDialogButtonBox, QLabel)

SHORTCUTS = [
    ("Navigation", [
        ("Right-drag", "Rotate the view"),
        ("Middle-drag, Ctrl+right-drag", "Pan"),
        ("Wheel", "Zoom at the mouse"),
        ("Double-click the wheel", "Zoom to fit"),
        ("F", "Zoom to fit"),
        ("Z", "Zoom out"),
        ("W, Shift+Z", "Zoom to window (drag a box; Esc cancels)"),
        ("Arrow keys", "Turn the view 15°"),
        ("Shift+arrow", "Turn the view 90°"),
        ("Ctrl+arrow", "Turn the view 5°"),
        ("Ctrl+Shift+arrow", "Pan"),
        ("Shift+1 … Shift+7", "Front, Back, Left, Right, Top, Bottom, Isometric view"),
        ("N", "Normal to the face / plane / sketch (again: from the other side)"),
        ("Alt+Left", "Previous view"),
        ("Shift+V", "Named views"),
    ]),
    ("Showing", [
        ("P", "Default planes on / off"),
        ("Shift+P", "Hide (or show) all planes and sketches"),
        ("Shift+H", "Hide (or show) sketches"),
        ("Y", "Hide the selected part (or the one under the mouse)"),
        ("Shift+Y", "Show hidden parts"),
        ("Shift+I", "Isolate the selected part (or the one under the mouse)"),
        ("Shift+X", "Section view"),
        ("Shift+R", "View in high quality"),
    ]),
    ("Selecting", [
        ("Click, Shift / Ctrl+click", "Select, add to the selection"),
        ("Drag a box", "Left to right: what is fully inside · right to left: also what it touches"),
        ("Space", "Deselect all"),
        ("Tab / Shift+Tab", "Step through everything under the mouse; click selects the one shown"),
        ("` (back quote)", "Select other…: list everything under the mouse"),
        ("Right-click", "Select other…, tangent-connected, loop, same size …"),
    ]),
    ("Features and tools", [
        ("S", "Your tools menu at the mouse (Customize… to choose them)"),
        ("Alt+C, Ctrl+Space", "Search tools"),
        ("Shift+S", "New sketch"),
        ("Shift+E", "Extrude (from a sketch: finish it and extrude)"),
        ("Shift+W", "Revolve (from a sketch: finish it and revolve)"),
        ("Shift+F", "Fillet"),
        ("Enter", "Accept the dialog"),
        ("Shift+Enter", "Accept and start another of the same"),
        ("Esc", "Cancel / stop the tool"),
        ("Delete", "Delete what's selected"),
        ("Shift+N", "Rename the selected feature or part"),
        ("Ctrl+C, Ctrl+V", "Copy / paste the features selected in the feature list (pasted at the rollback bar)"),
        ("Ctrl+Z, Ctrl+Y", "Undo, redo"),
        ("?", "This list"),
    ]),
    ("Sketch", [
        ("L", "Line"), ("R", "Corner rectangle"), ("C", "Circle"), ("A", "3-point arc"),
        ("Shift+A", "Tangent arc"), ("P, Shift+S", "Point"), ("D", "Dimension"), ("Q", "Construction"),
        ("U", "Use (project) part edges"), ("O", "Offset"), ("M", "Trim"), ("X", "Extend"),
        ("Shift+X", "Polygon (a hexagon to start)"), ("Shift+F", "Sketch fillet"),
        ("I", "Coincident"), ("H", "Horizontal"), ("V", "Vertical"), ("E", "Equal"), ("T", "Tangent"),
        ("B", "Parallel"), ("Shift+L", "Perpendicular"), ("Shift+O", "Concentric"), ("Shift+M", "Midpoint"),
        ("Shift+Q", "Symmetric"), ("Shift+J", "Fix"),
        ("Delete", "Delete what's selected"), ("Esc", "Stop the tool"),
    ]),
    ("Assembly", [
        ("I", "Insert parts"), ("M, Shift+F", "Fastened mate"), ("Shift+R", "Revolute mate"),
        ("Shift+L", "Slider mate"), ("Left-drag a part", "Move it"), ("Delete", "Delete what's selected"),
    ]),
    ("Drawing", [
        ("P", "Projected view"), ("N", "Note"),
    ]),
]


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Keyboard shortcuts"))
        self.resize(620, 720)
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Search keys or actions — e.g. trim, Shift+X, planes"))
        self.search.setClearButtonEnabled(True)
        lay.addWidget(self.search)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Keys"), tr("What it does")])
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
        self.none = QLabel(tr("No shortcut matches that."))
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
