"""The Nest window: choose how many of each sheet-metal part to cut, the stock sheet, gaps and kerf; see the parts
laid out on sheets as you change things; export one DXF for the laser / plasma / waterjet."""
from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import Qt, QTimer, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QTableWidget, QTableWidgetItem, QSpinBox, QDoubleSpinBox,
    QComboBox, QCheckBox, QLabel, QPushButton, QWidget, QFileDialog, QMessageBox, QHeaderView, QSizePolicy,
)

from .. import nesting as N
from . import filedlg


class _Preview(QWidget):
    """The sheets side by side with the parts on them."""

    def __init__(self):
        super().__init__()
        self.res = None
        self.setMinimumSize(560, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#eef1f5"))
        res = self.res
        if res is None or not res.sheets:
            p.setPen(QColor("#6b7685"))
            p.drawText(self.rect(), Qt.AlignCenter, "Set a quantity for at least one part")
            return
        n = len(res.sheets)
        gap = N.SHEET_GAP
        total_w = n * res.sheet_w + (n - 1) * gap
        k = min((self.width() - 24) / total_w, (self.height() - 40) / res.sheet_h)
        ox, oy = 12.0, self.height() - 14.0
        T = lambda q: QPointF(ox + q[0] * k, oy - q[1] * k)          # noqa: E731
        for si, sheet in enumerate(res.sheets):
            sx = N.sheet_offset(res, si)
            r = QRectF(T((sx, res.sheet_h)), T((sx + res.sheet_w, 0)))
            p.setPen(QPen(QColor("#9aa3ae"), 1))
            p.setBrush(QColor("white"))
            p.drawRect(r)
            p.setPen(QColor("#4b5563"))
            p.drawText(QPointF(r.left(), r.top() - 4), f"Sheet {si + 1}")
            for pl in sheet:
                part = res.parts[pl.part]
                lines, circles, arcs, polys, bends = N.placed(part, pl, sx)
                path = QPainterPath()
                for a, b in lines:
                    path.moveTo(T(a))
                    path.lineTo(T(b))
                for c, rr in circles:
                    path.addEllipse(T(c), rr * k, rr * k)
                for c, rr, a0, a1 in arcs:
                    a1 = a1 if a1 > a0 else a1 + 360
                    ts = np.radians(np.linspace(a0, a1, 24))
                    pts = [T((c[0] + rr * math.cos(t), c[1] + rr * math.sin(t))) for t in ts]
                    path.moveTo(pts[0])
                    for q in pts[1:]:
                        path.lineTo(q)
                for pts in polys:
                    path.moveTo(T(pts[0]))
                    for q in pts[1:]:
                        path.lineTo(T(q))
                p.setPen(QPen(QColor("#1f6fd1"), 1.1))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                p.setPen(QPen(QColor("#d93025"), 0.8, Qt.DashLine))
                for a, b, _l in bends:
                    p.drawLine(T(a), T(b))


class NestDialog(QDialog):
    def __init__(self, main, doc):
        super().__init__(main)
        self.main, self.doc = main, doc
        self.setWindowTitle("Nest for cutting")
        self.resize(1080, 640)
        self.parts = []                 # (pid, name, thickness, flat pattern)
        state = doc.state
        errors = []
        for pid in getattr(state, "sheet", {}):
            if pid not in state.parts:
                continue
            try:
                fp = main.flat_pattern_of(state, pid)
                self.parts.append((pid, state.parts[pid].name, float(fp.thickness), fp))
            except Exception as e:
                errors.append(f"{state.parts[pid].name}: {e}")
        self._cache = {}
        self.res = None

        lay = QHBoxLayout(self)
        left = QVBoxLayout()
        lay.addLayout(left, 0)
        form = QFormLayout()
        self.thick = QComboBox()
        for t in sorted({t for _p, _n, t, _f in self.parts}):
            self.thick.addItem(f"{t:g} mm", t)
        form.addRow("Thickness:", self.thick)
        left.addLayout(form)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Part", "Quantity"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        left.addWidget(self.table, 1)

        form = QFormLayout()
        self.preset = QComboBox()
        for name, w, h in N.SHEET_PRESETS:
            self.preset.addItem(name, (w, h))
        self.preset.addItem("Custom", None)
        form.addRow("Sheet:", self.preset)
        self.sw, self.sh = self._mm(3000, 1, 100000), self._mm(1500, 1, 100000)
        row = QHBoxLayout()
        row.addWidget(self.sw)
        row.addWidget(QLabel("×"))
        row.addWidget(self.sh)
        form.addRow("Size:", row)
        self.gap = self._mm(5, 0, 1000)
        form.addRow("Gap between parts:", self.gap)
        self.margin = self._mm(10, 0, 1000)
        form.addRow("Margin from edge:", self.margin)
        self.kerf = self._mm(0.0, 0, 20, dp=2)
        self.kerf.setToolTip("Grows each outline by half this (holes shrink), so the cut lands on the line. "
                             "Leave at 0 if your cutting software already compensates.")
        form.addRow("Kerf compensation:", self.kerf)
        self.turn = QCheckBox("Allow turning parts 90°")
        self.turn.setChecked(True)
        form.addRow("", self.turn)
        self.mat = QComboBox()
        for name, rho in N.MATERIALS:
            self.mat.addItem(f"{name} ({rho:g} g/cm³)", rho)
        form.addRow("Material:", self.mat)
        self.price = QDoubleSpinBox()
        self.price.setRange(0, 10000)
        self.price.setDecimals(2)
        self.price.setSpecialValueText("—")
        self.price.setSuffix(" /kg")
        form.addRow("Sheet price:", self.price)
        left.addLayout(form)

        right = QVBoxLayout()
        lay.addLayout(right, 1)
        self.preview = _Preview()
        right.addWidget(self.preview, 1)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right.addWidget(self.info)
        if errors:
            warn = QLabel("⚠ Couldn't unfold: " + "; ".join(errors))
            warn.setStyleSheet("color:#d93025;")
            warn.setWordWrap(True)
            right.addWidget(warn)
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.export_btn = QPushButton("Export DXF…")
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self.export)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        btns.addWidget(self.export_btn)
        btns.addWidget(close)
        right.addLayout(btns)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.renest)
        for w in (self.sw, self.sh, self.gap, self.margin, self.kerf, self.price):
            w.valueChanged.connect(self.schedule)
        self.turn.toggled.connect(self.schedule)
        self.mat.currentIndexChanged.connect(self.schedule)
        self.preset.currentIndexChanged.connect(self._preset)
        self.thick.currentIndexChanged.connect(self._fill_table)
        self._fill_table()

    @staticmethod
    def _mm(v, lo, hi, dp=1):
        s = QDoubleSpinBox()
        s.setRange(lo, hi)
        s.setDecimals(dp)
        s.setValue(v)
        s.setSuffix(" mm")
        return s

    def _preset(self):
        wh = self.preset.currentData()
        if wh:
            for s, v in ((self.sw, wh[0]), (self.sh, wh[1])):
                s.blockSignals(True)
                s.setValue(v)
                s.blockSignals(False)
        self.schedule()

    def _fill_table(self):
        t = self.thick.currentData()
        rows = [x for x in self.parts if t is None or abs(x[2] - t) < 1e-9]
        self.table.setRowCount(len(rows))
        self._rows = rows
        for i, (_pid, name, _t, _fp) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(name))
            q = QSpinBox()
            q.setRange(0, 100000)
            q.setValue(1)
            q.valueChanged.connect(self.schedule)
            self.table.setCellWidget(i, 1, q)
        self.schedule()

    def schedule(self, *_):
        self._timer.start(150)

    def _nest_parts(self):
        kerf = round(self.kerf.value(), 4)
        out = []
        for pid, name, _t, fp in self._rows:
            key = (pid, kerf)
            if key not in self._cache:
                self._cache[key] = N.part_from_flat(fp, name, kerf)
            out.append(self._cache[key])
        return out

    def renest(self):
        qty = [self.table.cellWidget(i, 1).value() for i in range(self.table.rowCount())]
        try:
            parts = self._nest_parts()
            self.res = N.nest(parts, qty, self.sw.value(), self.sh.value(), self.gap.value(), self.margin.value(),
                              self.turn.isChecked())
        except Exception as e:
            self.res = None
            self.preview.res = None
            self.preview.update()
            self.info.setText(f"⚠ {e}")
            self.export_btn.setEnabled(False)
            return
        self.preview.res = self.res
        self.preview.update()
        s = N.summary(self.res, self.mat.currentData(), self.price.value() or None)
        txt = (f"<b>{s['parts']} part(s) on {s['sheets']} sheet(s)</b> of {self.sw.value():g} × {self.sh.value():g} × "
               f"{s['thickness']:g} mm · material used {s['use'] * 100:.0f}% · parts {s['part_kg']:.2f} kg · "
               f"sheets {s['sheet_kg']:.2f} kg")
        if "cost" in s:
            txt += f" · sheet cost {s['cost']:.2f}"
        lx, ly = s["last_used"]
        txt += (f"<br>The last sheet is used up to {lx:.0f} mm along and {ly:.0f} mm up "
                f"(offcut about {max(self.sw.value() - lx, 0):.0f} × {self.sh.value():g} mm).")
        self.info.setText(txt)
        self.export_btn.setEnabled(self.res.count > 0)

    def export(self):
        if self.res is None or not self.res.count:
            return
        name = (getattr(self.doc, "name", "") or "nest").replace(" ", "_") + "_nest.dxf"
        path, _ = filedlg.getSaveFileName(self, "Export nested sheets", name, "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            N.export_dxf(self.res, path)
        except Exception as e:
            QMessageBox.critical(self, "Nest for cutting", f"Couldn't export:\n{e}")
            return
        try:
            self.main.status.showMessage(f"Exported {self.res.count} parts on {len(self.res.sheets)} sheet(s): "
                                         f"{os.path.basename(path)}", 8000)
        except Exception:
            pass
