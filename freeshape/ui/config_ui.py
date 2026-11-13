"""The Configurations table of a Part Studio: rows are configurations (e.g. "M5 × 20", "M6 × 30"), columns are
the studio's variables, plus any features you add a column for (ticked = suppressed in that configuration)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel, QComboBox,
    QDialogButtonBox, QHeaderView, QInputDialog, QMessageBox,
)


def variable_names(doc):
    out = []
    for f in doc.features:
        if f["type"] == "variable":
            n = str(f["params"].get("name", "")).strip().lstrip("#")
            if n and n not in out:
                out.append(n)
    return out


class ConfigDialog(QDialog):
    def __init__(self, parent, doc):
        super().__init__(parent)
        self.doc = doc
        self.setWindowTitle("Configurations")
        self.resize(720, 380)
        lay = QVBoxLayout(self)
        hint = QLabel("Each row is a configuration of this Part Studio: it sets the variables (type a value or an "
                      "expression; leave blank to keep the variable's own value) and can suppress features. "
                      "Assemblies, drawings and derived parts can use any configuration.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.vars = variable_names(doc)
        rows = doc.configs.get("rows", [])
        self.feat_cols = []
        for r in rows:
            for fid in r.get("suppress", []):
                if fid not in self.feat_cols and doc.feature(fid) is not None:
                    self.feat_cols.append(fid)
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        lay.addWidget(self.table, 1)
        self._build_columns()
        for r in rows:
            self._add_row(r)
        btns = QHBoxLayout()
        for text, fn in (("Add configuration", lambda: self._add_row(None)), ("Remove", self._remove_row),
                         ("Add feature column…", self._add_feature_col)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(QLabel("Active:"))
        self.active = QComboBox()
        btns.addWidget(self.active)
        lay.addLayout(btns)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.table.itemChanged.connect(lambda _it: self._refresh_active())
        if not rows:
            self._add_row(None)
        self._refresh_active(doc.configs.get("active", 0))
        if not self.vars:
            hint.setText(hint.text() + "\n\nThis studio has no variables yet — add some with the (x) tool and use "
                         "them in dimensions (#name), or add feature columns to switch features on and off.")

    def _build_columns(self):
        heads = ["Name"] + [f"#{v}" for v in self.vars] + [
            "Suppress " + (self.doc.feature(f) or {}).get("name", f) for f in self.feat_cols]
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)

    def _add_row(self, r):
        r = r or {"name": self._new_name(), "vars": {}, "suppress": []}
        row = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(r["name"]))
        for k, v in enumerate(self.vars):
            self.table.setItem(row, 1 + k, QTableWidgetItem(str(r.get("vars", {}).get(v, ""))))
        for k, fid in enumerate(self.feat_cols):
            it = QTableWidgetItem("")
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if fid in r.get("suppress", []) else Qt.Unchecked)
            self.table.setItem(row, 1 + len(self.vars) + k, it)
        self.table.blockSignals(False)
        self._refresh_active()

    def _new_name(self):
        names = {self.table.item(i, 0).text() for i in range(self.table.rowCount()) if self.table.item(i, 0)}
        n = 1
        while f"Configuration {n}" in names:
            n += 1
        return f"Configuration {n}"

    def _remove_row(self):
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._refresh_active()

    def _add_feature_col(self):
        feats = [f for f in self.doc.features if f["type"] not in ("variable",) and f["id"] not in self.feat_cols]
        if not feats:
            return
        name, ok = QInputDialog.getItem(self, "Feature column", "Feature to switch on and off:",
                                        [f["name"] for f in feats], 0, False)
        if not ok:
            return
        rows = self.rows()
        self.feat_cols.append(feats[[f["name"] for f in feats].index(name)]["id"])
        self.table.setRowCount(0)
        self._build_columns()
        for r in rows:
            self._add_row(r)

    def _refresh_active(self, want=None):
        cur = self.active.currentText() if want is None else None
        self.active.blockSignals(True)
        self.active.clear()
        names = [self.table.item(i, 0).text() if self.table.item(i, 0) else "" for i in range(self.table.rowCount())]
        self.active.addItems(names)
        if want is not None and 0 <= want < len(names):
            self.active.setCurrentIndex(want)
        elif cur in names:
            self.active.setCurrentIndex(names.index(cur))
        self.active.blockSignals(False)

    def rows(self):
        out = []
        for i in range(self.table.rowCount()):
            name = (self.table.item(i, 0).text() if self.table.item(i, 0) else "").strip()
            vals = {}
            for k, v in enumerate(self.vars):
                it = self.table.item(i, 1 + k)
                if it is not None and it.text().strip():
                    vals[v] = it.text().strip()
            sup = [fid for k, fid in enumerate(self.feat_cols)
                   if self.table.item(i, 1 + len(self.vars) + k) is not None
                   and self.table.item(i, 1 + len(self.vars) + k).checkState() == Qt.Checked]
            out.append({"name": name, "vars": vals, "suppress": sup})
        return out

    def _ok(self):
        rows = self.rows()
        names = [r["name"] for r in rows]
        if any(not n for n in names) or len(set(names)) != len(names):
            QMessageBox.information(self, "Configurations", "Give every configuration its own name.")
            return
        self.doc.set_configs(rows, max(self.active.currentIndex(), 0))
        self.accept()
