"""The Variables table window (toolbar Variable ▾ → Variables table): every variable of the Part Studio with its
value, kind and what uses it. Change a value or a name right in the table; a rename follows the variable everywhere.
Stays open while you work and follows the model."""
from __future__ import annotations

from ..i18n import tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QHeaderView,
                               QPushButton, QLabel, QAbstractItemView, QInputDialog, QMessageBox)

from .. import units as U
from .. import variables as V
from .numparse import fmt

COLS = ["Name", "Value", "Kind", "Set to", "Used by"]
KIND_NAMES = {"length": "Length", "angle": "Angle", "number": "Number", "area": "Area", "volume": "Volume",
              "text": "Text", "any": "Any"}


def value_text(v, kind):
    if v is None:
        return "—"
    if isinstance(v, str):
        return f'"{v}"'
    try:
        if kind == "length":
            return U.length(float(v))
        if kind == "angle":
            return f"{fmt(float(v))}°"
        if kind == "area":
            return f"{fmt(float(v))} mm²"
        if kind == "volume":
            return f"{fmt(float(v))} mm³"
        return fmt(float(v))
    except (TypeError, ValueError):
        return str(v)


class VariablesTable(QDialog):
    def __init__(self, main):
        super().__init__(main, Qt.Tool)
        self.main = main
        self.doc = main.doc
        self.setWindowTitle(f"Variables — {self.doc.name}")
        self.resize(640, 380)
        lay = QVBoxLayout(self)
        hint = QLabel("Change a value in <b>Set to</b>, or a name, right here — a new name follows the variable "
                      "into every feature, sketch dimension and configuration that uses it. Measured variables "
                      "are set by the model.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5f6b7a;")
        lay.addWidget(hint)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        hh = self.table.horizontalHeader()
        for i in range(len(COLS)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(len(COLS) - 1, QHeaderView.Stretch)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.b_new = QPushButton(tr("+ New variable"))
        self.b_new.clicked.connect(self.new_variable)
        self.b_show = QPushButton(tr("Edit…"))
        self.b_show.setToolTip("Open the selected variable's feature (to change its type or what it measures)")
        self.b_show.clicked.connect(self.show_feature)
        row.addWidget(self.b_new)
        row.addWidget(self.b_show)
        row.addStretch(1)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.close)
        row.addWidget(close)
        lay.addLayout(row)
        self._filling = False
        self.rows = []
        self._listener = lambda: self.refresh()
        self.doc.listeners.append(self._listener)
        self.refresh()

    # ---- contents
    def refresh(self):
        self._filling = True
        try:
            self.rows = V.list_variables(self.doc)
            self.table.setRowCount(len(self.rows))
            for r, v in enumerate(self.rows):
                users = V.uses(self.doc, v["name"]) if v["name"] else []
                used = ", ".join((self.doc.feature(f) or {}).get("name", f) for f in users) or "—"
                cells = [f"#{v['name']}", value_text(v["value"], v["kind"]),
                         KIND_NAMES.get(v["kind"], "") + ("" if v["typed"] else " (measured)"),
                         v["expr"] if v["typed"] else "measured from the model", used]
                for c, text in enumerate(cells):
                    it = QTableWidgetItem(text)
                    editable = c == 0 or (c == 3 and v["typed"])
                    flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled | (Qt.ItemIsEditable if editable else Qt.NoItemFlags)
                    it.setFlags(flags)
                    if v["error"]:
                        it.setToolTip("⚠ " + v["error"])
                        if c == 1:
                            it.setText("⚠ " + v["error"][:40])
                    elif c == 3 and v["typed"] and v.get("mtype") == "fixed_length" and v["expr"] \
                            and v["expr"].replace(".", "", 1).replace("-", "", 1).isdigit():
                        it.setToolTip(f"In {v['unit']}")
                    if v["suppressed"]:
                        it.setForeground(Qt.gray)
                    self.table.setItem(r, c, it)
        finally:
            self._filling = False

    def _edited(self, item):
        if self._filling:
            return
        r, c = item.row(), item.column()
        if r >= len(self.rows):
            return
        v = self.rows[r]
        if self.main.mode != "idle":
            QMessageBox.information(self, "Variables", "Finish or cancel the feature or sketch you're editing first.")
            self.refresh()
            return
        try:
            if c == 0:
                V.rename(self.doc, v["name"], item.text())
            elif c == 3 and v["typed"]:
                V.set_expr(self.doc, v["fid"], item.text().strip())
        except ValueError as e:
            QMessageBox.warning(self, "Variables", str(e))
        self.refresh()

    def new_variable(self):
        if self.main.mode != "idle":
            QMessageBox.information(self, "Variables", "Finish or cancel the feature or sketch you're editing first.")
            return
        name, ok = QInputDialog.getText(self, "New variable", "Name (without #):")
        if not ok or not name.strip():
            return
        try:
            V.add(self.doc, name, expr="10", mtype="fixed_length", unit=U.unit())
        except ValueError as e:
            QMessageBox.warning(self, "Variables", str(e))
            return
        self.refresh()
        r = next((i for i, v in enumerate(self.rows) if v["name"] == name.strip().lstrip("#")), None)
        if r is not None:
            self.table.setCurrentCell(r, 3)
            self.table.editItem(self.table.item(r, 3))

    def show_feature(self):
        r = self.table.currentRow()
        if 0 <= r < len(self.rows):
            self.main.edit_feature(self.rows[r]["fid"])

    def closeEvent(self, e):
        try:
            self.doc.listeners.remove(self._listener)
        except ValueError:
            pass
        if getattr(self.main, "_variables_table", None) is self:
            self.main._variables_table = None
        super().closeEvent(e)
