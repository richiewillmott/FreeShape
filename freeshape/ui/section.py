"""The floating Section view panel."""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QComboBox, QSlider, QLineEdit, QCheckBox,
                               QToolButton, QGraphicsDropShadowEffect, QVBoxLayout)

from .. import kernel as K
from ..expr import parse_number, fmt
from .icons import icon


class SectionPanel(QFrame):
    closed = Signal()

    def __init__(self, main):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self.main = main
        self.custom = None          # plane picked from the selection
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(16)
        sh.setOffset(0, 2)
        sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon("section").pixmap(18, 18))
        top.addWidget(ic)
        t = QLabel("Section view")
        t.setObjectName("dialogTitle")
        top.addWidget(t)
        top.addStretch(1)
        self.plane = QComboBox()
        for name in ("Front", "Top", "Right"):
            self.plane.addItem(f"{name} plane", name)
        self.plane.addItem("Selected plane / face", "selected")
        top.addWidget(self.plane)
        x = QToolButton()
        x.setText("✕")
        x.setToolTip("Close section view")
        x.clicked.connect(self.closed.emit)
        top.addWidget(x)
        lay.addLayout(top)
        row = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(-1000, 1000)
        self.slider.setFixedWidth(220)
        row.addWidget(self.slider)
        self.offset = QLineEdit("0")
        self.offset.setFixedWidth(70)
        self.offset.setAlignment(Qt.AlignRight)
        self.offset.setToolTip("Offset from the plane, in mm")
        row.addWidget(self.offset)
        row.addWidget(QLabel("mm"))
        self.flip = QCheckBox("Flip")
        row.addWidget(self.flip)
        lay.addLayout(row)
        self.plane.currentIndexChanged.connect(self._plane_changed)
        self.slider.valueChanged.connect(self._slider)
        self.offset.editingFinished.connect(self._typed)
        self.flip.toggled.connect(lambda _: self.apply())
        self._range = 50.0
        self.adjustSize()
        self.show()

    def base_plane(self):
        key = self.plane.currentData()
        if key == "selected":
            return self.custom or K.std_plane("Front")
        return K.std_plane(key)

    def use_selection(self, plane):
        self.custom = plane
        self.plane.blockSignals(True)
        self.plane.setCurrentIndex(self.plane.findData("selected"))
        self.plane.blockSignals(False)
        self._plane_changed()

    def _plane_changed(self, *_):
        lo, hi = self.main.viewport.model_bounds()
        base = self.base_plane()
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        dist = (corners - base.origin) @ base.normal
        self._lo, self._hi = float(dist.min()), float(dist.max())
        mid = (self._lo + self._hi) / 2
        # a picked face/plane cuts exactly there (like Onshape); default planes cut through the middle
        # of the model if they don't pass through it
        self._set_offset(0.0 if (base is self.custom or self._lo <= 0 <= self._hi) else mid)

    def _set_offset(self, v):
        self.offset.setText(fmt(v, 3))
        span = max(self._hi - self._lo, 1e-6)
        self.slider.blockSignals(True)
        self.slider.setValue(int(round((v - self._lo) / span * 2000 - 1000)))
        self.slider.blockSignals(False)
        self.apply()

    def _slider(self, val):
        v = self._lo + (val + 1000) / 2000 * (self._hi - self._lo)
        self.offset.setText(fmt(v, 3))
        self.apply()

    def _typed(self):
        v = parse_number(self.offset.text(), variables=self.main.doc.state.vars)
        if v is not None:
            self._set_offset(v)

    def value(self):
        v = parse_number(self.offset.text(), variables=self.main.doc.state.vars)
        return 0.0 if v is None else v

    def apply(self):
        base = self.base_plane()
        n = -base.normal if self.flip.isChecked() else base.normal
        plane = K.Plane(base.origin + base.normal * self.value(), base.xdir, n)
        self.main.viewport.set_section(plane)

    def place(self):
        vp = self.main.viewport
        self.adjustSize()
        self.move(max(12, (vp.width() - self.width()) // 2), 12)
