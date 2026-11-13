"""Assembly tab interface: instance & mate lists, the Insert panel, the Mate dialog, dragging parts
through their mates, precise moves, interference check and the bill of materials."""
from __future__ import annotations

import copy
import json
import os

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QSize
from PySide6.QtGui import QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QLineEdit, QListWidget, QListWidgetItem, QWidget,
    QGraphicsDropShadowEffect, QTreeWidget, QTreeWidgetItem, QPushButton, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QAbstractItemView, QComboBox, QFileDialog, QMessageBox, QCheckBox,
)

from .. import kernel as K
from .. import assembly as A
from . import filedlg
from .icons import icon
from .numparse import parse_number, fmt
from .panels import swatch
from .viewport import Pick

ROLE_ID = Qt.UserRole + 1
ROLE_KIND = Qt.UserRole


def _floating(frame: QFrame):
    frame.setObjectName("featureDialog")
    sh = QGraphicsDropShadowEffect(frame)
    sh.setBlurRadius(18)
    sh.setOffset(0, 3)
    sh.setColor(QColor(0, 0, 0, 60))
    frame.setGraphicsEffect(sh)


def _head(frame, title, ic, on_ok, on_cancel, ok_tip="Accept (Enter)", cancel_tip="Cancel (Esc)"):
    head = QHBoxLayout()
    lab = QLabel()
    lab.setPixmap(icon(ic).pixmap(20, 20))
    head.addWidget(lab)
    t = QLineEdit(title)
    t.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                    "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
    head.addWidget(t, 1)
    if on_ok is not None:
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip(ok_tip)
        ok.clicked.connect(on_ok)
        head.addWidget(ok)
    cancel = QToolButton()
    cancel.setObjectName("cancelButton")
    cancel.setText("✕")
    cancel.setToolTip(cancel_tip)
    cancel.clicked.connect(on_cancel)
    head.addWidget(cancel)
    return head, t


def _hline():
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("color:#e3e6ea;")
    return line


# =========================================================================== left panel

class AssemblyPanel(QWidget):
    instanceClicked = Signal(str)
    instanceDouble = Signal(str)
    instanceContext = Signal(str, object)
    mateClicked = Signal(str)
    mateDouble = Signal(str)
    mateContext = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("leftpanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.inst_header = QLabel("Instances")
        self.inst_header.setObjectName("sectionHeader")
        lay.addWidget(self.inst_header)
        self.instances = QListWidget()
        self.instances.setIconSize(QSize(14, 14))
        self.instances.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.instances.setContextMenuPolicy(Qt.CustomContextMenu)
        self.instances.customContextMenuRequested.connect(
            lambda pos: self._ctx(self.instances, pos, self.instanceContext))
        self.instances.itemClicked.connect(lambda it: self.instanceClicked.emit(it.data(ROLE_ID)))
        self.instances.itemDoubleClicked.connect(lambda it: self.instanceDouble.emit(it.data(ROLE_ID)))
        lay.addWidget(self.instances, 3)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#d5d9de;")
        lay.addWidget(sep)
        self.mate_header = QLabel("Mates")
        self.mate_header.setObjectName("sectionHeader")
        lay.addWidget(self.mate_header)
        self.mates = QListWidget()
        self.mates.setIconSize(QSize(18, 18))
        self.mates.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.mates.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mates.customContextMenuRequested.connect(lambda pos: self._ctx(self.mates, pos, self.mateContext))
        self.mates.itemClicked.connect(lambda it: self.mateClicked.emit(it.data(ROLE_ID)))
        self.mates.itemDoubleClicked.connect(lambda it: self.mateDouble.emit(it.data(ROLE_ID)))
        lay.addWidget(self.mates, 2)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(10, 6, 10, 8)
        lay.addWidget(self.status)
        self.setMinimumWidth(220)

    def _ctx(self, lst, pos, sig):
        it = lst.itemAt(pos)
        if it is not None:
            sig.emit(it.data(ROLE_ID), lst.viewport().mapToGlobal(pos))

    def populate(self, doc, editing=None):
        for lst in (self.instances, self.mates):
            lst.blockSignals(True)
        sel_i = set(self.selected_instances())
        sel_m = set(self.selected_mates())
        self.instances.clear()
        for inst in doc.instances:
            label = inst["name"] + ("   ⚓" if inst.get("fixed") else "")
            it = QListWidgetItem(swatch(doc.instance_color(inst)), label)
            it.setData(ROLE_ID, inst["id"])
            tips = []
            if inst.get("fixed"):
                tips.append("Fixed — it won't move (right-click → Unfix to let it move)")
            err = doc.errors.get(inst["id"])
            if err:
                it.setForeground(QColor("#d93025") if inst["id"] not in doc.parts else QColor("#b26a00"))
                tips.append(err)
            elif inst.get("hidden"):
                it.setForeground(QColor("#9aa3ae"))
                tips.append("Hidden")
            src = inst["source"]
            tips.append("From " + (src.get("filename") or "STEP") if src.get("kind") == "step" else
                        "From Part Studio “%s”" % (src.get("studio_name") or "?"))
            it.setToolTip("\n".join(tips))
            self.instances.addItem(it)
            if inst["id"] in sel_i:
                it.setSelected(True)
        self.mates.clear()
        for m in doc.mates:
            it = QListWidgetItem(icon("m_" + m["type"]), m["name"])
            it.setData(ROLE_ID, m["id"])
            names = []
            for side in ("a", "b"):
                c = m.get(side)
                inst = doc.instance(c["inst"]) if c else None
                names.append(inst["name"] if inst else "?")
            tip = f"{A.MATE_TYPES[m['type']][0]}: {names[0]}  ↔  {names[1]}"
            err = doc.errors.get(m["id"])
            if m.get("suppressed"):
                it.setForeground(QColor("#9aa3ae"))
                f = QFont()
                f.setStrikeOut(True)
                it.setFont(f)
                tip += "\nSuppressed"
            elif err:
                it.setForeground(QColor("#d93025"))
                it.setIcon(icon("error"))
                tip += "\n" + err
            if m["id"] == editing:
                it.setBackground(QColor("#ffe9b8"))
            it.setToolTip(tip)
            self.mates.addItem(it)
            if m["id"] in sel_m:
                it.setSelected(True)
        mname = {m["id"]: m["name"] for m in doc.mates}
        for r in getattr(doc, "relations", []):
            it = QListWidgetItem(icon("relation"), r["name"])
            it.setData(ROLE_ID, r["id"])
            what = {"gear": f"ratio {r.get('ratio', 1):g}", "rack": f"pinion radius {r.get('radius', 0):g} mm",
                    "screw": f"pitch {r.get('pitch', 0):g} mm"}[r["type"]]
            it.setToolTip(f"{A.RELATION_TYPES[r['type']][0]}: {mname.get(r['a'], '?')}"
                          + (f" ↔ {mname.get(r['b'], '?')}" if r.get("b") else "") + f" ({what})")
            self.mates.addItem(it)
            if r["id"] in sel_m:
                it.setSelected(True)
        for pt in getattr(doc, "patterns", []):
            n = sum(1 for row in pt["copies"] for c in row if c)
            it = QListWidgetItem(icon("linear_pattern" if pt["type"] == "linear" else "circular_pattern"),
                                 f"{pt['name']} (×{pt['count']})")
            it.setData(ROLE_ID, pt["id"])
            err = doc.errors.get(pt["id"])
            it.setToolTip((f"⚠ {err}\n" if err else "") + f"{n} copies of "
                          + ", ".join((doc.instance(sd) or {}).get("name", "?") for sd in pt["seeds"])
                          + "\nDouble-click to change the count or spacing")
            self.mates.addItem(it)
            if pt["id"] in sel_m:
                it.setSelected(True)
        self.inst_header.setText(f"Instances ({len(doc.instances)})")
        self.mate_header.setText(f"Mates ({len(doc.mates)})")
        if not doc.instances:
            self.status.setText("Insert parts from your Part Studios (or a STEP file) with the Insert button.")
        else:
            free = sum(1 for i in doc.instances if not i.get("fixed"))
            bad = sum(1 for m in doc.mates if m["id"] in doc.errors and not m.get("suppressed"))
            txt = f"{free} free instance(s) · {doc.dof} degree(s) of freedom left"
            if bad:
                txt += f" · {bad} mate(s) failing"
            self.status.setText(txt + "\nDrag parts to move them through their mates.")
        for lst in (self.instances, self.mates):
            lst.blockSignals(False)

    def selected_instances(self):
        return [it.data(ROLE_ID) for it in self.instances.selectedItems()]

    def selected_mates(self):
        return [it.data(ROLE_ID) for it in self.mates.selectedItems()]

    def select_instances(self, ids):
        self.instances.blockSignals(True)
        for i in range(self.instances.count()):
            it = self.instances.item(i)
            it.setSelected(it.data(ROLE_ID) in ids)
        self.instances.blockSignals(False)


# =========================================================================== insert panel

class InsertPanel(QFrame):
    """Onshape-style Insert panel: pick a part (or a whole Part Studio) and it's added to the assembly.
    It stays open so you can insert several things."""
    closed = Signal()

    def __init__(self, ctl):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, "Insert parts", "insert", None, self.close_panel, cancel_tip="Close (Esc)")
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        hint = QLabel("Double-click a part to insert it. Pick a Part Studio to insert all its parts as they "
                      "sit there. Inserted parts stay linked: edit them in their tab and the assembly updates.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setMinimumHeight(220)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self._insert_item(it))
        lay.addWidget(self.tree)
        row = QHBoxLayout()
        b = QPushButton("Insert")
        b.setDefault(True)
        b.clicked.connect(lambda: self._insert_item(self.tree.currentItem()))
        row.addWidget(b)
        f = QPushButton("From file…")
        f.setToolTip("A FreeShape document (.fshape) or a STEP file")
        f.clicked.connect(self._from_file)
        row.addWidget(f)
        lay.addLayout(row)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        lay.addWidget(self.msg)
        lay.addWidget(_hline())
        lay.addWidget(self._std_box())
        self.fill()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def _std_box(self):
        """Standard parts: family, size and length, made from ISO size tables (no download needed)."""
        from .. import stdparts as SP
        from PySide6.QtWidgets import QComboBox, QFormLayout, QSpinBox, QWidget as _W
        box = _W()
        form = QFormLayout(box)
        form.setContentsMargins(0, 0, 0, 0)
        title = QLabel("<b>Standard parts</b>")
        form.addRow(title)
        self.std_family = QComboBox()
        for key, (label, *_rest) in SP.FAMILIES.items():
            self.std_family.addItem(label, key)
        self.std_size = QComboBox()
        self.std_len = QComboBox()
        form.addRow("Type:", self.std_family)
        form.addRow("Size:", self.std_size)
        self.std_len_label = QLabel("Length:")
        form.addRow(self.std_len_label, self.std_len)
        row = QHBoxLayout()
        self.std_qty = QSpinBox()
        self.std_qty.setRange(1, 200)
        self.std_qty.setPrefix("× ")
        row.addWidget(self.std_qty)
        b = QPushButton("Insert standard part")
        b.clicked.connect(self._insert_std)
        row.addWidget(b, 1)
        form.addRow(row)

        def sizes():
            fam = self.std_family.currentData()
            self.std_size.blockSignals(True)
            self.std_size.clear()
            self.std_size.addItems(SP.FAMILIES[fam][1])
            pick = {"bearing": "608", "iso8734": "4"}.get(fam, "M3")
            if self.std_size.findText(pick) >= 0:
                self.std_size.setCurrentText(pick)
            self.std_size.blockSignals(False)
            lengths()

        def lengths():
            fam, size = self.std_family.currentData(), self.std_size.currentText()
            ls = SP.lengths_for(fam, size) if size else None
            self.std_len.clear()
            self.std_len.setVisible(ls is not None)
            self.std_len_label.setVisible(ls is not None)
            for L in ls or []:
                self.std_len.addItem(f"{L:g} mm", L)
            if ls:
                self.std_len.setCurrentIndex(min(len(ls) - 1, 3))
        self.std_family.currentIndexChanged.connect(sizes)
        self.std_size.currentIndexChanged.connect(lengths)
        sizes()
        return box

    def _insert_std(self):
        from .. import stdparts as SP
        spec = {"kind": "std", "family": self.std_family.currentData(), "size": self.std_size.currentText()}
        if self.std_len.isVisible() and self.std_len.currentData():
            spec["length"] = float(self.std_len.currentData())
        n = self.ctl.insert_std(spec, self.std_qty.value())
        if n:
            self.msg.setText(f"Inserted {n} × {SP.name(spec)}")

    def fill(self):
        self.tree.clear()
        for doc in self.ctl.w.studio_docs():
            top = QTreeWidgetItem([doc.name])
            top.setIcon(0, icon("part", 16))
            top.setData(0, ROLE_KIND, "studio")
            top.setData(0, ROLE_ID, doc.uid)
            for p in doc.state.parts.values():
                it = QTreeWidgetItem([p.name])
                it.setIcon(0, swatch(p.color))
                it.setData(0, ROLE_KIND, "part")
                it.setData(0, ROLE_ID, (doc.uid, p.id))
                top.addChild(it)
            if not doc.state.parts:
                it = QTreeWidgetItem(["(no parts yet)"])
                it.setDisabled(True)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        if self.tree.topLevelItemCount() == 0:
            it = QTreeWidgetItem(["No Part Studios open — use “From file…”"])
            it.setDisabled(True)
            self.tree.addTopLevelItem(it)

    def _insert_item(self, it):
        if it is None or it.isDisabled():
            return
        kind = it.data(0, ROLE_KIND)
        if kind == "part":
            uid, pid = it.data(0, ROLE_ID)
            self.ctl.insert_studio_parts(uid, [pid])
        elif kind == "studio":
            self.ctl.insert_studio_parts(it.data(0, ROLE_ID), None)
        self.msg.setText(f"Inserted {it.text(0)}")

    def _from_file(self):
        path, _ = filedlg.getOpenFileName(self, "Insert from file", "",
                                              "Parts (*.fshape *.fcad *.step *.stp);;All files (*)")
        if not path:
            return
        if path.lower().endswith((".step", ".stp")):
            n = self.ctl.insert_step(path)
            self.msg.setText(f"Inserted {n} part(s) from {os.path.basename(path)}")
        else:
            doc = self.ctl.w.open_studio_for_link(path)
            if doc is not None:
                self.fill()
                self.ctl.insert_studio_parts(doc.uid, None)
                self.msg.setText(f"Inserted the parts of {doc.name}")

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self.closed.emit()


# =========================================================================== exploded view

class ExplodePanel(QFrame):
    """The exploded view: parts slid apart along directions, step by step (shown only while this is open; the
    real positions and mates don't change)."""
    closed = Signal()

    def __init__(self, ctl):
        super().__init__(ctl.w.viewport)
        from PySide6.QtWidgets import QSlider, QDoubleSpinBox
        _floating(self)
        self.ctl = ctl
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, "Exploded view", "explode", None, self.close_panel, cancel_tip="Close (Esc)")
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        hint = QLabel("Parts are only moved apart for showing (and drawings); their real positions and mates "
                      "don't change.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        auto = QPushButton("Auto explode")
        auto.setToolTip("Move every part out from the fixed one along its nearest main axis")
        auto.clicked.connect(self._auto)
        lay.addWidget(auto)
        lay.addWidget(QLabel("<b>Add a step</b> — select parts, then:"))
        row = QHBoxLayout()
        self.dir = QComboBox()
        for lab, v in (("+Z", (0, 0, 1)), ("−Z", (0, 0, -1)), ("+X", (1, 0, 0)), ("−X", (-1, 0, 0)),
                       ("+Y", (0, 1, 0)), ("−Y", (0, -1, 0))):
            self.dir.addItem(lab, v)
        row.addWidget(self.dir)
        self.dist = QDoubleSpinBox()
        self.dist.setRange(-100000, 100000)
        self.dist.setValue(30)
        self.dist.setSuffix(" mm")
        row.addWidget(self.dist, 1)
        add = QPushButton("Add")
        add.clicked.connect(self._add)
        row.addWidget(add)
        lay.addLayout(row)
        self.steps = QListWidget()
        self.steps.setMinimumHeight(110)
        lay.addWidget(self.steps)
        row = QHBoxLayout()
        rm = QPushButton("Remove step")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        clr = QPushButton("Clear all")
        clr.clicked.connect(lambda: (self.ctl.doc.clear_explode(), self.fill()))
        row.addWidget(clr)
        lay.addLayout(row)
        lay.addWidget(QLabel("Assembled ↔ exploded"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(int(round(100 * float(self.ctl.doc.explode.get("amount", 1.0)))))
        self.slider.valueChanged.connect(self._amount)
        lay.addWidget(self.slider)
        dwg = QPushButton("Drawing of this exploded view…")
        dwg.clicked.connect(lambda: self.ctl.w.new_drawing({"kind": "assembly", "uid": self.ctl.doc.uid,
                                                            "part": "*", "exploded": True}))
        lay.addWidget(dwg)
        self.msg = QLabel("")
        self.msg.setWordWrap(True)
        self.msg.setObjectName("muted")
        lay.addWidget(self.msg)
        self.fill()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def fill(self):
        doc = self.ctl.doc
        self.steps.clear()
        for st in doc.explode.get("steps", []):
            names = ", ".join((doc.instance(i) or {}).get("name", "?") for i in st["ids"])
            d = st["dir"]
            ax = next((lab for lab, v in (("+X", (1, 0, 0)), ("−X", (-1, 0, 0)), ("+Y", (0, 1, 0)),
                                          ("−Y", (0, -1, 0)), ("+Z", (0, 0, 1)), ("−Z", (0, 0, -1)))
                       if np.allclose(d, v, atol=1e-6)), "along " + ", ".join(f"{x:.2f}" for x in d))
            self.steps.addItem(f"{names}: {ax} {st['dist']:.1f} mm")
        self.ctl.show_scene()

    def _auto(self):
        try:
            n = self.ctl.doc.auto_explode()
            self.slider.setValue(100)
            self.msg.setText(f"{n} part(s) moved apart — adjust or add steps below")
        except K.KernelError as e:
            self.msg.setText(str(e))
        self.fill()

    def _add(self):
        ids = self.ctl.panel.selected_instances() or list({pk.key[0] for pk in self.ctl.vp.selection
                                                           if pk.kind in ("face", "edge", "part")})
        try:
            self.ctl.doc.add_explode_step(ids, self.dir.currentData(), self.dist.value())
            self.msg.setText(f"Moved {len(ids)} part(s)")
        except K.KernelError as e:
            self.msg.setText(str(e))
        self.fill()

    def _remove(self):
        r = self.steps.currentRow()
        doc = self.ctl.doc
        if 0 <= r < len(doc.explode.get("steps", [])):
            doc.checkpoint()
            doc.explode["steps"].pop(r)
            doc.notify()
            self.fill()

    def _amount(self, v):
        self.ctl.doc.explode["amount"] = v / 100.0
        self.ctl.doc.dirty = True
        self.ctl.show_scene()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        self.closed.emit()


# =========================================================================== mate dialog

class ConnectorField(QFrame):
    activated = Signal(object)

    def __init__(self, label):
        super().__init__()
        self.setObjectName("selField")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        t = QLabel(label)
        t.setStyleSheet("color:#4b5563;")
        top.addWidget(t, 1)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip("Clear")
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.text = QLabel("—")
        self.text.setWordWrap(True)
        lay.addWidget(self.text)
        self.set_active(False)

    def mousePressEvent(self, e):
        self.activated.emit(self)

    def set_active(self, a):
        self.setProperty("active", "true" if a else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class MateDialog(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, ctl, mate, is_new, preselection=()):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl = ctl
        self.doc = ctl.doc
        self.mate = copy.deepcopy(mate)
        self.is_new = is_new
        self._backup = copy.deepcopy(self.doc.instances)
        self._backup_mates = copy.deepcopy(self.doc.mates)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._preview)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, self.title = _head(self, self.mate["name"], "m_" + self.mate["type"], self.accept, self.reject)
        self.head_icon = head.itemAt(0).widget()
        lay.addLayout(head)
        lay.addWidget(_hline())
        # mate type, as a row of icon buttons (like Onshape's mate dialog)
        trow = QHBoxLayout()
        trow.setSpacing(2)
        self.type_buttons = {}
        for t, (label, tip) in A.MATE_TYPES.items():
            b = QToolButton()
            b.setIcon(icon("m_" + t))
            b.setIconSize(QSize(22, 22))
            b.setCheckable(True)
            b.setToolTip(f"{label} — {tip}")
            b.clicked.connect(lambda _=False, tt=t: self.set_type(tt))
            trow.addWidget(b)
            self.type_buttons[t] = b
        trow.addStretch(1)
        lay.addLayout(trow)
        self.type_label = QLabel("")
        self.type_label.setObjectName("muted")
        self.type_label.setWordWrap(True)
        lay.addWidget(self.type_label)
        self.fa = ConnectorField("First mate connector")
        self.fb = ConnectorField("Second mate connector")
        for f, side in ((self.fa, "a"), (self.fb, "b")):
            f.activated.connect(self.activate)
            f.clear_btn.clicked.connect(lambda _=False, s=side: self._clear(s))
            lay.addWidget(f)
        hint = QLabel("Click a flat face, a round face (picks the end nearest the click), a circular edge "
                      "or a straight edge on each part.")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        # options
        orow = QHBoxLayout()
        self.flip = QToolButton()
        self.flip.setText("⇅ Flip")
        self.flip.setCheckable(True)
        self.flip.setToolTip("Flip the primary (Z) axis — e.g. put a pin in the other way round")
        self.flip.clicked.connect(lambda c: self._set("flip", bool(c)))
        orow.addWidget(self.flip)
        self.rot = QToolButton()
        self.rot.setText("⟳ 90°")
        self.rot.setToolTip("Turn the second part 90° about the Z axis")
        self.rot.clicked.connect(self._rotate90)
        orow.addWidget(self.rot)
        orow.addStretch(1)
        lay.addLayout(orow)
        self.offset_row, self.offset = self._num_row("Offset", self.mate.get("offset", 0.0), "offset", "mm")
        lay.addWidget(self.offset_row)
        self.angle_row, self.angle = self._num_row("Angle", self.mate.get("angle", 0.0), "angle", "°")
        lay.addWidget(self.angle_row)
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)
        self.active = None
        self.set_type(self.mate["type"], preview=False)
        self.flip.setChecked(bool(self.mate.get("flip")))
        self._apply_preselection(preselection)
        self.activate(self.fa if not self.mate.get("a") else self.fb if not self.mate.get("b") else self.fa)
        self._refresh_fields()
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()
        self.schedule()

    def _num_row(self, label, value, key, unit):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(label)
        lab.setFixedWidth(60)
        h.addWidget(lab)
        ed = QLineEdit(fmt(float(value or 0)))
        ed.setProperty("unit", unit)

        def done(ed=ed, key=key):
            try:
                v = parse_number(ed.text(), angle=(key == "angle"))
                if v is None:
                    raise ValueError
            except Exception:
                ed.setProperty("invalid", "true")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                return
            ed.setProperty("invalid", "false")
            ed.style().unpolish(ed)
            ed.style().polish(ed)
            self._set(key, float(v))
        ed.editingFinished.connect(done)
        ed.returnPressed.connect(done)
        h.addWidget(ed, 1)
        h.addWidget(QLabel(unit))
        return w, ed

    # ------------------------------------------------------------------ state
    def set_type(self, t, preview=True):
        self.mate["type"] = t
        for k, b in self.type_buttons.items():
            b.setChecked(k == t)
        label, tip = A.MATE_TYPES[t]
        self.type_label.setText(f"<b>{label}</b> — {tip}")
        self.head_icon.setPixmap(icon("m_" + t).pixmap(20, 20))
        base = self.title.text().rsplit(" ", 1)
        if self.is_new and len(base) == 2 and base[0] in [v[0] for v in A.MATE_TYPES.values()]:
            self.title.setText(self.doc.make_mate(t)["name"] if self.is_new else self.title.text())
        self.angle_row.setVisible(t in ("fastened", "slider"))
        self.offset_row.setVisible(t in ("fastened", "revolute", "planar", "ball"))
        self.rot.setVisible(t in ("fastened", "slider"))
        self.flip.setVisible(t != "ball")
        self.adjustSize()
        if preview:
            self.schedule()

    def _set(self, key, value):
        self.mate[key] = value
        self.schedule()

    def _rotate90(self):
        a = (float(self.mate.get("angle", 0.0) or 0.0) + 90.0) % 360.0
        self.mate["angle"] = a
        self.angle.setText(fmt(a))
        self.schedule()

    def _clear(self, side):
        self.mate[side] = None
        self.activate(self.fa if side == "a" else self.fb)
        self._refresh_fields()
        self.schedule()

    def activate(self, field):
        if self.active is not None:
            self.active.set_active(False)
        self.active = field
        field.set_active(True)
        self.ctl.w.viewport.select_filter = {"face", "edge"}
        self._refresh_fields()

    def _refresh_fields(self):
        for f, side in ((self.fa, "a"), (self.fb, "b")):
            c = self.mate.get(side)
            if not c:
                f.text.setText("Click to select" if f is self.active else "—")
                f.text.setStyleSheet("color:#8a94a3;")
            else:
                inst = self.doc.instance(c["inst"])
                f.text.setText(f"{inst['name'] if inst else '?'} — {c['frame'].get('what', c['kind'])}")
                f.text.setStyleSheet("")
        self.ctl.update_triads()

    def handle_pick(self, pick: Pick):
        if pick.kind not in ("face", "edge"):
            return
        iid, idx = pick.key
        vp = self.ctl.w.viewport
        click = vp.surface_point(*vp.last_click) if vp.last_click else None
        if pick.kind == "edge" and vp.last_click:
            click = None
        try:
            c = self.doc.connector(iid, pick.kind, idx, click)
        except Exception as e:
            self._error(f"Can't use that: {e}")
            return
        side = "a" if self.active is self.fa else "b"
        other = self.mate.get("b" if side == "a" else "a")
        if other and other["inst"] == iid:
            self._error("Pick the second connector on a different part.")
            return
        self.mate[side] = c
        if side == "a" and not self.mate.get("b"):
            self.activate(self.fb)
        elif side == "b" and not self.mate.get("a"):
            self.activate(self.fa)
        self._refresh_fields()
        self.schedule()

    def _apply_preselection(self, picks):
        for pk in picks:
            if pk.kind not in ("face", "edge"):
                continue
            side = "a" if not self.mate.get("a") else "b" if not self.mate.get("b") else None
            if side is None:
                break
            iid, idx = pk.key
            if side == "b" and self.mate["a"]["inst"] == iid:
                continue
            try:
                self.mate[side] = self.doc.connector(iid, pk.kind, idx, None)
            except Exception:
                pass
        self.ctl.w.viewport.clear_selection()

    def _error(self, text):
        self.err.setText(text)
        self.err.setVisible(bool(text))
        self.adjustSize()

    # ------------------------------------------------------------------ preview
    def schedule(self):
        self._timer.start(30)

    def _restore(self):
        self.doc.instances = copy.deepcopy(self._backup)
        self.doc.mates = copy.deepcopy(self._backup_mates)

    def _preview(self):
        self._restore()
        m = self.mate
        if m.get("a") and m.get("b"):
            mates = [x for x in self.doc.mates if x["id"] != m["id"]]
            mates.append(m)
            self.doc.mates = mates
            self.doc.place_for_mate(m)
            self.doc.solve()
            err = self.doc.errors.get(m["id"])
            self._error(err or "")
        else:
            self.doc.solve()
            self._error("")
        self.ctl.show_scene()
        self.ctl.update_triads()

    def accept(self):
        self._timer.stop()
        self.mate["name"] = self.title.text().strip() or self.mate["name"]
        if not (self.mate.get("a") and self.mate.get("b")):
            self._error("Pick a mate connector on each of the two parts first.")
            return
        self._restore()
        self.accepted.emit()

    def reject(self):
        self._timer.stop()
        self._restore()
        self.doc.solve()
        self.rejected.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# =========================================================================== precise move

class MoveDialog(QFrame):
    """Move or rotate the selected instances by exact amounts (also works on fixed parts)."""
    accepted = Signal()
    rejected = Signal()

    def __init__(self, ctl, ids):
        super().__init__(ctl.w.viewport)
        _floating(self)
        self.ctl, self.doc, self.ids = ctl, ctl.doc, list(ids)
        self._backup = copy.deepcopy(self.doc.instances)
        self.vals = {"dx": 0.0, "dy": 0.0, "dz": 0.0, "angle": 0.0}
        self.axis = "Z"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        names = ", ".join(self.doc.instance(i)["name"] for i in self.ids)
        head, self.title = _head(self, "Move", "move", self.accept, self.reject)
        self.title.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        lab = QLabel(names)
        lab.setWordWrap(True)
        lab.setObjectName("muted")
        lay.addWidget(lab)
        self.eds = {}
        for key, label, unit in (("dx", "Move X", "mm"), ("dy", "Move Y", "mm"), ("dz", "Move Z", "mm"),
                                 ("angle", "Rotate", "°")):
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            l_ = QLabel(label)
            l_.setFixedWidth(60)
            h.addWidget(l_)
            ed = QLineEdit("0")
            ed.editingFinished.connect(lambda k=key, e=ed: self._num(k, e))
            ed.returnPressed.connect(lambda k=key, e=ed: self._num(k, e))
            h.addWidget(ed, 1)
            h.addWidget(QLabel(unit))
            if key == "angle":
                cb = QComboBox()
                cb.addItems(["about X", "about Y", "about Z"])
                cb.setCurrentIndex(2)
                cb.currentIndexChanged.connect(lambda i: (setattr(self, "axis", "XYZ"[i]), self._preview()))
                h.addWidget(cb)
            self.eds[key] = ed
            lay.addWidget(w)
        note = QLabel("Rotation is about the part's centre. Mated parts follow their mates afterwards.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.setFixedWidth(300)
        self.adjustSize()
        self.move(12, 12)
        self.show()
        self.eds["dx"].setFocus()

    def _num(self, key, ed):
        try:
            v = parse_number(ed.text(), angle=(key == "angle"))
            if v is None:
                return
        except Exception:
            return
        self.vals[key] = float(v)
        self._preview()

    def matrix(self):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for iid in self.ids:
            s = self.doc.world_shape(iid)
            if s is not None:
                a, b = K.bbox(s)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        c = (lo + hi) / 2 if np.all(np.isfinite(lo)) else np.zeros(3)
        ax = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[self.axis]
        R = A.rot_about(ax, np.radians(self.vals["angle"]), c)
        return A.translation([self.vals["dx"], self.vals["dy"], self.vals["dz"]]) @ R

    def _preview(self):
        self.doc.instances = copy.deepcopy(self._backup)
        M = self.matrix()
        for iid in self.ids:
            inst = self.doc.instance(iid)
            inst["T"] = A.orthonormalize(M @ np.array(inst["T"], float)).tolist()
        self.ctl.show_scene()

    def accept(self):
        for key, ed in self.eds.items():
            self._num(key, ed)
        M = self.matrix()
        self.doc.instances = copy.deepcopy(self._backup)
        self.accepted.emit()
        self.doc.checkpoint()
        for iid in self.ids:
            self.doc.move_instance(iid, M, checkpoint=False, solve=False)
        self.doc.solve(keep=self.ids[0] if len(self.ids) == 1 else None)
        self.doc.notify()

    def reject(self):
        self.doc.instances = copy.deepcopy(self._backup)
        self.rejected.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# =========================================================================== bill of materials

class BomDialog(QDialog):
    def __init__(self, parent, doc):
        super().__init__(parent)
        self.setWindowTitle(f"Bill of materials — {doc.name}")
        self.resize(720, 380)
        self.rows = doc.bom()
        lay = QVBoxLayout(self)
        t = QTableWidget(len(self.rows), 6)
        t.setHorizontalHeaderLabels(["Item", "Part", "Quantity", "From", "Volume (mm³)", "Mass PLA (g)"])
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for r, row in enumerate(self.rows):
            vals = [str(r + 1), row["name"], str(row["qty"]), row["source"],
                    fmt(row["volume"], 1) if row["volume"] is not None else "—",
                    fmt(row["volume"] * 1.24e-3, 2) if row["volume"] is not None else "—"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (0, 2, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                it.setToolTip(", ".join(row["instances"]))
                t.setItem(r, c, it)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(t)
        total = sum(r["qty"] for r in self.rows)
        lay.addWidget(QLabel(f"{len(self.rows)} different part(s), {total} in total"))
        row = QHBoxLayout()
        row.addStretch(1)
        cp = QPushButton("Copy")
        cp.setToolTip("Copy as a table you can paste into a spreadsheet")
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.csv("\t")))
        row.addWidget(cp)
        ex = QPushButton("Export CSV…")
        ex.clicked.connect(self._export)
        row.addWidget(ex)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)

    def csv(self, sep=","):
        out = [sep.join(["Item", "Part", "Quantity", "From", "Volume (mm3)", "Mass PLA (g)"])]
        for r, row in enumerate(self.rows):
            v = row["volume"]
            cells = [str(r + 1), row["name"], str(row["qty"]), row["source"],
                     f"{v:.1f}" if v is not None else "", f"{v * 1.24e-3:.2f}" if v is not None else ""]
            if sep == ",":
                cells = ['"%s"' % c.replace('"', '""') if ("," in c or '"' in c) else c for c in cells]
            out.append(sep.join(cells))
        return "\n".join(out) + "\n"

    def _export(self):
        path, _ = filedlg.getSaveFileName(self, "Export bill of materials", "bom.csv", "CSV (*.csv)")
        if path:
            if not path.lower().endswith(".csv"):
                path += ".csv"
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.csv())


# =========================================================================== controller

class AssemblyController:
    """Everything the main window does differently while an Assembly tab is current."""

    def __init__(self, w):
        self.w = w
        self.panel = AssemblyPanel()
        self.insert_panel = None
        self.explode_panel = None
        self.mate_dialog = None
        self.move_dialog = None
        self._drag = None
        self._interf = None          # last interference result (ghost meshes)
        from .gizmo import Gizmo
        self.gizmo = Gizmo(self)     # Onshape-style move / rotate triad on the clicked part
        p = self.panel
        p.instanceClicked.connect(self._instance_clicked)
        p.instanceDouble.connect(self.open_source)
        p.instanceContext.connect(self.instance_menu)
        p.mateClicked.connect(lambda _mid: self.update_triads())
        p.mateDouble.connect(self.edit_mate)
        p.mateContext.connect(self.mate_menu)

    @property
    def doc(self):
        return self.w.doc

    @property
    def vp(self):
        return self.w.viewport

    # ------------------------------------------------------------------ view
    def show_scene(self):
        doc = self.doc
        parts = []
        exploding = self.explode_panel is not None
        for inst in doc.visible_instances():
            p = doc.parts[inst["id"]]
            T = doc.display_T(inst["id"], exploded=exploding)
            fc = p.face_colors if not inst.get("color") else None
            parts.append((inst["id"], K.located(p.shape, T), doc.instance_color(inst), fc, T, p.shape))
        self.vp.set_scene(parts, [], [])
        self.vp.ghosts = self._interf or []
        self.update_triads()

    def update_triads(self):
        doc = self.doc
        tri = []
        conns = []
        if self.mate_dialog is not None:
            m = self.mate_dialog.mate
            conns = [m.get("a"), m.get("b")]
        else:
            for mid in self.panel.selected_mates():
                m = doc.mate(mid)
                if m:
                    conns += [m.get("a"), m.get("b")]
        for c in conns:
            if not c or doc.instance(c["inst"]) is None:
                continue
            fr = doc.world_frame(c)
            z = A.unit(fr["z"])
            x = A.unit(fr["x"])
            tri.append({"o": fr["o"], "x": x, "y": np.cross(z, x), "z": z})
        self.vp.triads = tri
        self.vp.update()

    def on_changed(self):
        if self._interf is not None:
            # parts may have moved: the old overlap display would be wrong
            self._interf = None
            self.vp.ghosts = []
            if hasattr(self.w, "btn_interf"):
                self.w.btn_interf.setChecked(False)
        editing = self.mate_dialog.mate["id"] if self.mate_dialog is not None and not self.mate_dialog.is_new \
            else None
        self.panel.populate(self.doc, editing)
        if self.w.mode == "idle":
            self.show_scene()

    # ------------------------------------------------------------------ inserting
    def toggle_explode(self):
        if self.explode_panel is not None:
            self.close_explode()
            return
        if self.w.mode != "idle":
            return
        self.close_insert()
        self.explode_panel = ExplodePanel(self)
        self.explode_panel.closed.connect(self.close_explode)
        self.show_scene()

    def close_explode(self):
        if self.explode_panel is not None:
            self.explode_panel.hide()
            self.explode_panel.deleteLater()
            self.explode_panel = None
            self.show_scene()

    def toggle_insert(self):
        if self.insert_panel is not None:
            self.close_insert()
            return
        if self.w.mode != "idle":
            return
        self.insert_panel = InsertPanel(self)
        self.w._adapt_tree(self.insert_panel)
        self.insert_panel.closed.connect(self.close_insert)

    def close_insert(self):
        if self.insert_panel is not None:
            self.insert_panel.hide()
            self.insert_panel.deleteLater()
            self.insert_panel = None

    def insert_studio_parts(self, uid, part_ids=None):
        studio = self.w.studio_by_uid(uid)
        if studio is None:
            return
        doc = self.doc
        pids = part_ids or list(studio.state.parts)
        if not pids:
            return
        doc.checkpoint()
        first_new = []
        # a whole studio keeps its parts where they sit relative to each other
        shift = None
        if part_ids is None and doc.instances:
            lo, hi = doc.bounds()
            slo, shi = np.full(3, np.inf), np.full(3, -np.inf)
            for pid in pids:
                a, b = K.bbox(studio.state.parts[pid].shape)
                slo, shi = np.minimum(slo, a), np.maximum(shi, b)
            gap = max(0.15 * float(np.linalg.norm(shi - slo)), 5.0)
            shift = A.translation([hi[0] + gap - slo[0], 0, lo[2] - slo[2]])
        for pid in pids:
            src = {"kind": "studio", "uid": studio.uid, "part": pid, "path": studio.path,
                   "studio_name": studio.name}
            try:
                T = shift if part_ids is None and doc.instances and shift is not None else None
                if part_ids is None and not doc.instances:
                    T = np.eye(4)
                iid = doc.insert(src, T=T, checkpoint=False)
                first_new.append(iid)
            except K.KernelError as e:
                QMessageBox.warning(self.w, "Insert", str(e))
        if part_ids is None and len(first_new) > 1 and not any(
                doc.instance(i).get("fixed") for i in first_new[1:]):
            pass
        self.w.status.showMessage(f"Inserted {len(first_new)} instance(s)", 4000)
        QTimer.singleShot(0, lambda: self.vp.fit())

    def insert_std(self, spec, qty=1):
        """Insert copies of a standard part, side by side next to what's there (ready to mate)."""
        doc = self.doc
        doc.checkpoint()
        made = []
        for _ in range(int(qty)):
            try:
                made.append(doc.insert(dict(spec), checkpoint=False))
            except K.KernelError as e:
                QMessageBox.warning(self.w, "Insert", str(e))
                break
        self.w.status.showMessage(f"Inserted {len(made)} standard part(s)", 4000)
        QTimer.singleShot(0, lambda: self.vp.fit())
        return len(made)

    def insert_step(self, path):
        import base64
        from .progress import ImportProgress, read_step_in_background
        with open(path, "rb") as fh:
            raw = fh.read()
        data = base64.b64encode(raw).decode()
        doc = self.doc
        key = doc.add_step_blob(data)
        prog = ImportProgress(self.w, f"Inserting {os.path.basename(path)}")
        try:
            if not read_step_in_background(raw, prog, self.w):
                return 0
            prog.stage("Building the parts…")
            sols = doc._step_parts({"blob": key})
        except Exception as e:
            prog.close()
            prog = None
            QMessageBox.warning(self.w, "Insert", f"Couldn't read {os.path.basename(path)}:\n{e}")
            return 0
        finally:
            if prog is not None:
                prog.close()
        doc.checkpoint()
        empty = not doc.instances
        shift = None
        if not empty:
            lo, hi = doc.bounds()
            slo, shi = np.full(3, np.inf), np.full(3, -np.inf)
            for s, *_ in sols:
                a, b = K.bbox(s)
                slo, shi = np.minimum(slo, a), np.maximum(shi, b)
            gap = max(0.15 * float(np.linalg.norm(shi - slo)), 5.0)
            shift = A.translation([hi[0] + gap - slo[0], 0, lo[2] - slo[2]])
        n = 0
        for i in range(len(sols)):
            src = {"kind": "step", "blob": key, "index": i, "filename": os.path.basename(path)}
            doc.insert(src, T=np.eye(4) if empty else shift, checkpoint=False)
            n += 1
        QTimer.singleShot(0, lambda: self.vp.fit())
        return n

    # ------------------------------------------------------------------ mates
    def start_mate(self, mtype, edit_mid=None):
        w = self.w
        if w.mode != "idle":
            if self.mate_dialog is not None and self.mate_dialog.is_new and edit_mid is None:
                self.mate_dialog.set_type(mtype)
            return
        if len([i for i in self.doc.instances if i["id"] in self.doc.parts]) < 2:
            w.status.showMessage("Insert at least two parts before adding a mate", 5000)
            return
        self.close_insert()
        if edit_mid:
            m = self.doc.mate(edit_mid)
            is_new, pre = False, []
        else:
            m = self.doc.make_mate(mtype)
            is_new, pre = True, list(self.vp.selection)
        w.mode = "dialog"
        self.mate_dialog = MateDialog(self, m, is_new, pre)
        w._adapt_tree(self.mate_dialog)
        self.mate_dialog.accepted.connect(self._mate_accepted)
        self.mate_dialog.rejected.connect(self._mate_rejected)
        self.vp.pick_handler = self.mate_dialog.handle_pick
        self.panel.populate(self.doc, None if is_new else m["id"])

    def edit_mate(self, mid):
        if self.doc.relation(mid) is not None:
            self.relation_dialog(mid)
            return
        if any(p_["id"] == mid for p_ in self.doc.patterns):
            self.pattern_dialog(edit=mid)
            return
        m = self.doc.mate(mid)
        if m is not None and self.w.mode == "idle":
            self.start_mate(m["type"], mid)

    # ------------------------------------------------------------------ relations and patterns
    def relation_dialog(self, edit=None):
        """Gear, rack and pinion, or screw: link mates so their motions go together."""
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QSpinBox
        doc = self.doc
        rel = doc.relation(edit) if edit else None
        dlg = QDialog(self.w)
        dlg.setWindowTitle("Edit relation" if rel else "Relation")
        form = QFormLayout(dlg)
        typ = QComboBox()
        for k, (lab, _tip) in A.RELATION_TYPES.items():
            typ.addItem(lab, k)
        form.addRow("Type:", typ)
        ma, mb = QComboBox(), QComboBox()
        form.addRow("Driving mate:", ma)
        mb_label = QLabel("Driven mate:")
        form.addRow(mb_label, mb)
        t1, t2 = QDoubleSpinBox(), QDoubleSpinBox()
        for t in (t1, t2):
            t.setRange(1, 10000)
            t.setDecimals(0)
        t1.setValue(20)
        t2.setValue(20)
        t1_label, t2_label = QLabel("Teeth (driving):"), QLabel("Teeth (driven):")
        form.addRow(t1_label, t1)
        form.addRow(t2_label, t2)
        num = QDoubleSpinBox()
        num.setRange(0.001, 100000)
        num.setDecimals(3)
        num.setSuffix(" mm")
        num_label = QLabel("")
        form.addRow(num_label, num)
        rev = QCheckBox("Opposite direction")
        form.addRow("", rev)
        tip = QLabel("")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        form.addRow(tip)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        def refill():
            k = typ.currentData()
            want_a = A.TURNING if k != "screw" else ("cylindrical",)
            want_b = A.TURNING if k == "gear" else A.SLIDING
            for box, want in ((ma, want_a), (mb, want_b)):
                cur = box.currentData()
                box.clear()
                for m in doc.mates:
                    if m["type"] in want:
                        box.addItem(f"{m['name']} ({A.MATE_TYPES[m['type']][0]})", m["id"])
                if cur is not None and box.findData(cur) >= 0:
                    box.setCurrentIndex(box.findData(cur))
            mb.setVisible(k != "screw")
            mb_label.setVisible(k != "screw")
            for w_ in (t1, t2, t1_label, t2_label):
                w_.setVisible(k == "gear")
            num.setVisible(k != "gear")
            num_label.setVisible(k != "gear")
            num_label.setText("Pinion radius:" if k == "rack" else "Pitch (per turn):")
            rev.setText("Same direction (inside gear)" if k == "gear" else "Opposite direction")
            tip.setText(A.RELATION_TYPES[k][1] + ". The parts keep the position they have now as the start.")
            if k == "gear" and mb.count() > 1 and mb.currentData() == ma.currentData():
                mb.setCurrentIndex(1)
        typ.currentIndexChanged.connect(refill)
        refill()
        if rel:
            typ.setCurrentIndex(typ.findData(rel["type"]))
            typ.setEnabled(False)
            refill()
            ma.setCurrentIndex(max(0, ma.findData(rel["a"])))
            if rel.get("b"):
                mb.setCurrentIndex(max(0, mb.findData(rel["b"])))
            t1.setValue(20)
            t2.setValue(20 / max(rel.get("ratio", 1.0), 1e-9))
            num.setValue(rel.get("radius", 10.0) if rel["type"] == "rack" else rel.get("pitch", 1.0))
            rev.setChecked(not rel.get("reverse", True) if rel["type"] == "gear" else bool(rel.get("reverse")))
        else:
            num.setValue(10.0)
        if ma.count() == 0:
            QMessageBox.information(self.w, "Relation", "Make the mates first: revolute mates for gears, a revolute and "
                                    "a slider for rack and pinion, a cylindrical mate for a screw.")
            return
        if dlg.exec() != QDialog.Accepted:
            return
        k = typ.currentData()
        kw = {"ratio": t1.value() / t2.value(), "radius": num.value(), "pitch": num.value(),
              "reverse": (not rev.isChecked()) if k == "gear" else rev.isChecked()}
        try:
            if rel:
                doc.checkpoint()
                rel.update({x: kw[x] for x in ("ratio", "radius", "pitch", "reverse")})
                doc.solve()
                doc.notify()
            else:
                doc.add_relation(k, ma.currentData(), mb.currentData() if k != "screw" else None, **kw)
        except K.KernelError as e:
            QMessageBox.warning(self.w, "Relation", str(e))

    def pattern_dialog(self, ptype="linear", edit=None):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QDoubleSpinBox, QSpinBox
        doc = self.doc
        pat = next((p_ for p_ in doc.patterns if p_["id"] == edit), None) if edit else None
        seeds = pat["seeds"] if pat else self.selected_instance_ids()
        seeds = [s_ for s_ in seeds if doc.instance(s_) is not None and not doc.instance(s_).get("pattern")]
        if not seeds:
            QMessageBox.information(self.w, "Pattern", "Select the parts to pattern first (in the list or the view).")
            return
        ptype = pat["type"] if pat else ptype
        dlg = QDialog(self.w)
        dlg.setWindowTitle(("Linear" if ptype == "linear" else "Circular") + " pattern")
        form = QFormLayout(dlg)
        form.addRow(QLabel(", ".join(doc.instance(s_)["name"] for s_ in seeds)))
        axis = QComboBox()
        for lab, ax in (("X axis", {"kind": "world", "name": "X"}), ("Y axis", {"kind": "world", "name": "Y"}),
                        ("Z axis", {"kind": "world", "name": "Z"})):
            axis.addItem(lab, ax)
        for m in doc.mates:
            if m["type"] in ("revolute", "cylindrical", "slider"):
                axis.addItem(f"Axis of {m['name']}", {"kind": "mate", "mate": m["id"]})
        form.addRow("Direction:" if ptype == "linear" else "Round:", axis)
        flip = QCheckBox("Opposite direction")
        form.addRow("", flip)
        count = QSpinBox()
        count.setRange(2, 1000)
        count.setValue(pat["count"] if pat else (3 if ptype == "linear" else 6))
        form.addRow("Count (with the original):", count)
        amt = QDoubleSpinBox()
        amt.setRange(-100000, 100000)
        amt.setDecimals(3)
        if ptype == "linear":
            amt.setSuffix(" mm")
            amt.setValue(pat["spacing"] if pat else 20.0)
            form.addRow("Spacing:", amt)
        else:
            amt.setSuffix("°")
            amt.setValue(pat["angle"] if pat else 360.0)
            form.addRow("Angle (360 = all the way round):", amt)
        if pat:
            for i in range(axis.count()):
                if axis.itemData(i) == {k_: v for k_, v in pat["axis"].items() if k_ != "flip"}:
                    axis.setCurrentIndex(i)
            flip.setChecked(bool(pat["axis"].get("flip")))
        elif ptype == "circular":
            axis.setCurrentIndex(2)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if dlg.exec() != QDialog.Accepted:
            return
        ax = dict(axis.currentData())
        if flip.isChecked():
            ax["flip"] = True
        kw = {"count": count.value(), "axis": ax}
        kw["spacing" if ptype == "linear" else "angle"] = amt.value()
        try:
            if pat:
                doc.edit_pattern(pat["id"], **kw)
            else:
                doc.add_pattern(ptype, seeds, **kw)
        except K.KernelError as e:
            QMessageBox.warning(self.w, "Pattern", str(e))

    def _close_mate(self):
        d = self.mate_dialog
        if d is not None:
            d.hide()
            d.deleteLater()
        self.mate_dialog = None
        self.w.mode = "idle"
        vp = self.vp
        vp.pick_handler = None
        vp.select_filter = None
        vp.clear_selection()
        return d

    def _mate_accepted(self):
        d = self._close_mate()
        m = d.mate
        if d.is_new:
            self.doc.add_mate(m)
        else:
            self.doc.replace_mate(m)
        err = self.doc.errors.get(m["id"])
        self.w.status.showMessage(f"{m['name']}: {err}" if err else f"{m['name']} added" if d.is_new
                                  else f"{m['name']} updated", 6000)

    def _mate_rejected(self):
        self._close_mate()
        self.on_changed()

    # ------------------------------------------------------------------ moving
    def start_move(self, ids=None):
        ids = ids or self.selected_instance_ids()
        if not ids:
            self.w.status.showMessage("Select a part (in the list or the graphics area) to move it", 5000)
            return
        if self.w.mode != "idle":
            return
        self.close_insert()
        self.w.mode = "dialog"
        self.move_dialog = MoveDialog(self, ids)
        self.w._adapt_tree(self.move_dialog)
        self.move_dialog.accepted.connect(self._close_move)
        self.move_dialog.rejected.connect(lambda: (self._close_move(), self.on_changed()))

    def _close_move(self):
        if self.move_dialog is not None:
            self.move_dialog.hide()
            self.move_dialog.deleteLater()
        self.move_dialog = None
        self.w.mode = "idle"

    def selected_instance_ids(self):
        ids = list(self.panel.selected_instances())
        for pk in self.vp.selection:
            if pk.kind in ("face", "edge", "part") and pk.key[0] not in ids:
                ids.append(pk.key[0])
        return [i for i in ids if self.doc.instance(i) is not None]

    # dragging (the viewport calls these) ---------------------------------
    def press(self, x, y, e):
        if self.w.mode != "idle" or self.explode_panel is not None:
            return False                      # (parts aren't dragged while the exploded view is shown)
        gz = self.gizmo
        h = gz.hit(x, y)
        if h is not None:
            inst = self.doc.instance(gz.inst)
            if h[0] != "label" and inst.get("fixed"):
                self.w.status.showMessage("This part is fixed — right-click it and choose Unfix to move it", 4000)
                self._drag = {"gizmo": "blocked", "moved": False}
                return True
            if h[0] == "label":
                self._drag = {"gizmo": "label", "moved": False}
                return True
            if gz.start(h, x, y):
                self._drag = {"gizmo": h, "moved": False, "snap": gz.drag["snap"]}
                return True
            return False
        pk = self.vp.pick(x, y, kinds={"face"})
        if pk is None:
            gz.clear()
            return False
        iid = pk.key[0]
        inst = self.doc.instance(iid)
        if inst is None:
            return False
        p = self.vp.surface_point(x, y)
        if p is None:
            return False
        T = np.array(inst["T"], float)
        Ti = np.linalg.inv(T)
        cam = self.vp.cam
        self._drag = {"inst": iid, "local": Ti[:3, :3] @ p + Ti[:3, 3], "plane_o": p, "n": cam.back.copy(),
                      "P": np.array([cam.right, cam.up]), "snap": self.doc._snapshot(), "moved": False,
                      "fixed": bool(inst.get("fixed")), "start_T": T}
        return True

    def hover(self, x, y):
        """Mouse over the triad? (highlights the handle; the viewport then skips its own hover)."""
        gz = self.gizmo
        h = gz.hit(x, y) if self.w.mode == "idle" else None
        if h != gz.hover:
            gz.hover = h
            self.vp.update()
        return h is not None

    def draw_overlay(self, p):
        self.gizmo.draw(p)

    def _group_has_mates(self, iid):
        return any(not m.get("suppressed") and m.get("a") and m.get("b")
                   and iid in (m["a"]["inst"], m["b"]["inst"]) for m in self.doc.mates)

    def apply_motion(self, iid, M, T0s, local_pts, exact=False, turn=False):
        """Move instance `iid` by the world motion M (from the poses T0s), as far as its mates allow:
        a part with no mates, or a mated group with nothing fixed, moves exactly (the whole group together);
        otherwise the solver moves it within its mates' freedom."""
        doc = self.doc
        group = doc._groups()[iid] if self._group_has_mates(iid) else {iid}
        if not any(doc.instance(i).get("fixed") for i in group):
            for i in group:
                doc.instance(i)["T"] = A.orthonormalize(M @ T0s[i]).tolist()
        else:
            T0 = T0s[iid]
            if exact:
                for i in doc.instances:
                    i["T"] = T0s[i["id"]].tolist()
            o = np.asarray(local_pts[0], float)
            if turn:
                # turning: aim for the new orientation and let the mates decide where the part sits
                # (a hinged part turns about its hinge, wherever the triad is)
                dirs = [(lp - o, (M @ T0)[:3, :3] @ (lp - o)) for lp in map(np.asarray, local_pts[1:])]
                drag = {"inst": iid, "dirs": dirs}
            else:
                drag = {"inst": iid, "points": [(o, (M @ T0 @ np.append(o, 1.0))[:3])]}
            doc.solve(drag=drag, iters=40 if exact else 10)
        self.show_scene()

    def typed_value_applied(self):
        doc = self.doc
        doc.solve()
        doc.dirty = True
        doc.notify()

    def move(self, x, y, e):
        d = self._drag
        if d is None:
            return
        if "gizmo" in d:
            if d["gizmo"] not in ("blocked", "label"):
                d["moved"] = True
                self.gizmo.move(x, y)
            return
        if d["fixed"]:
            self.w.status.showMessage("This part is fixed — right-click it and choose Unfix to drag it", 4000)
            return
        o, dr = self.vp.cam.ray(x, y)
        denom = dr @ d["n"]
        if abs(denom) < 1e-12:
            return
        t = ((d["plane_o"] - o) @ d["n"]) / denom
        target = o + t * dr
        d["moved"] = True
        doc = self.doc
        group = doc._groups()[d["inst"]] if self._group_has_mates(d["inst"]) else {d["inst"]}
        if not any(doc.instance(i).get("fixed") for i in group):
            # nothing in this group is fixed: slide the whole group together, like Onshape — the mates
            # between them stay exactly as they are and nothing spins round
            T = np.array(doc.instance(d["inst"])["T"], float)
            delta = target - (T[:3, :3] @ d["local"] + T[:3, 3])
            for i in group:
                Ti = np.array(doc.instance(i)["T"], float)
                Ti[:3, 3] += delta
                doc.instance(i)["T"] = Ti.tolist()
        else:
            doc.solve(drag={"inst": d["inst"], "local": d["local"], "target": target, "P": d["P"]}, iters=8)
        self.show_scene()

    def release(self, x, y, e, moved):
        d, self._drag = self._drag, None
        if d is None:
            return
        doc = self.doc
        if "gizmo" in d:
            gd = self.gizmo.finish()
            if d["gizmo"] == "label" and not moved:
                self.gizmo.open_editor()
            if gd is not None and d["moved"] and gd["moved"]:
                doc.solve()
                doc._undo.append(d["snap"])
                doc._redo.clear()
                doc.dirty = True
                doc.notify()
                # ready for typing: the value is selected, so a number + Enter sets it exactly
                self.gizmo.open_editor()
            self.vp.update()
            return
        if not moved or not d["moved"]:
            # a plain click on a part: put the move / rotate triad where it was clicked
            self.gizmo.place(d["inst"], d["plane_o"])
            return
        doc.solve()
        doc._undo.append(d["snap"])
        doc._redo.clear()
        doc.dirty = True
        doc.notify()

    # ------------------------------------------------------------------ menus
    def _instance_clicked(self, iid):
        if self.w.mode == "idle":
            self.vp.clear_selection()
            self.update_triads()
            if self.doc.instance(iid) is not None and not self.doc.instance(iid).get("hidden"):
                lo, hi = K.bbox(self.doc.world_shape(iid))
                self.gizmo.place(iid, (lo + hi) / 2)

    def instance_menu(self, iid, gpos):
        from PySide6.QtWidgets import QMenu
        doc = self.doc
        inst = doc.instance(iid)
        if inst is None or self.w.mode != "idle":
            return
        ids = self.selected_instance_ids()
        if iid not in ids:
            ids = [iid]
        m = QMenu(self.w)
        m.addAction("Rename…", lambda: self.rename(iid))
        m.addAction("Unfix" if inst.get("fixed") else "Fix", lambda: doc.set_fixed(iid, not inst.get("fixed")))
        m.addAction("Show" if inst.get("hidden") else "Hide", lambda: doc.set_hidden(iid, not inst.get("hidden")))
        m.addAction("Isolate", lambda: self.isolate(iid))
        m.addAction("Change colour…", lambda: self.recolor(iid))
        if inst.get("color"):
            m.addAction("Use the part's colour", lambda: doc.set_color(iid, None))
        m.addSeparator()
        m.addAction("Move / rotate…", lambda: self.start_move(ids))
        m.addAction("Duplicate", lambda: self.duplicate(ids))
        if inst["source"].get("kind") == "studio":
            m.addAction("Open Part Studio", lambda: self.open_source(iid))
        m.addAction("Mass properties", lambda: self.w._mass_props_shape(inst["name"], doc.world_shape(iid)))
        dof = doc.instance_dof(iid)
        a = m.addAction(f"Degrees of freedom: {dof}" + (" (fixed)" if inst.get("fixed") else ""))
        a.setEnabled(False)
        m.addSeparator()
        m.addAction("Delete", lambda: self.delete_instances(ids))
        m.exec(gpos)

    def mate_menu(self, mid, gpos):
        from PySide6.QtWidgets import QMenu
        doc = self.doc
        mt = doc.mate(mid)
        if mt is None and self.w.mode == "idle" and (doc.relation(mid) or any(p_["id"] == mid for p_ in doc.patterns)):
            m = QMenu(self.w)
            m.addAction("Edit…", lambda: self.edit_mate(mid))
            m.addAction("Rename…", lambda: self.rename(mid))
            m.addSeparator()
            m.addAction("Delete", lambda: doc.delete_mates([mid]))
            m.exec(gpos)
            return
        if mt is None or self.w.mode != "idle":
            return
        ids = self.panel.selected_mates()
        if mid not in ids:
            ids = [mid]
        m = QMenu(self.w)
        m.addAction("Edit…", lambda: self.edit_mate(mid))
        m.addAction("Rename…", lambda: self.rename(mid))
        if mt.get("type") in A.TURNING + A.SLIDING:
            m.addAction(icon("relation", 16), "Gear / rack / screw relation…", lambda: self.relation_dialog())
        m.addAction("Unsuppress" if mt.get("suppressed") else "Suppress",
                    lambda: doc.set_suppressed(mid, not mt.get("suppressed")))
        m.addSeparator()
        m.addAction("Delete", lambda: doc.delete_mates(ids))
        err = doc.errors.get(mid)
        if err:
            m.addSeparator()
            a = m.addAction("⚠ " + err)
            a.setEnabled(False)
        m.exec(gpos)

    def viewport_menu(self, pk, gpos):
        from PySide6.QtWidgets import QMenu
        if pk is not None and pk.kind in ("face", "edge") and self.doc.instance(pk.key[0]) is not None:
            self.panel.select_instances({pk.key[0]})
            self.instance_menu(pk.key[0], gpos)
            return
        m = QMenu(self.w)
        m.addAction("Insert parts…", self.toggle_insert)
        m.addAction("Zoom to fit", lambda: self.vp.fit())
        vm = m.addMenu("Views")
        for name in ("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"):
            vm.addAction(name, lambda n=name: self.vp.set_standard_view(n))
        if any(i.get("hidden") for i in self.doc.instances):
            m.addAction("Show all", self.show_all)
        m.exec(gpos)

    def rename(self, obj_id):
        from PySide6.QtWidgets import QInputDialog
        o = self.doc.instance(obj_id) or self.doc.mate(obj_id)
        name, ok = QInputDialog.getText(self.w, "Rename", "Name:", text=o["name"])
        if ok and name.strip():
            self.doc.rename(obj_id, name.strip())

    def recolor(self, iid):
        from PySide6.QtWidgets import QColorDialog
        inst = self.doc.instance(iid)
        c = QColorDialog.getColor(QColor.fromRgbF(*self.doc.instance_color(inst)), self.w, "Instance colour")
        if c.isValid():
            self.doc.set_color(iid, [c.redF(), c.greenF(), c.blueF()])

    def isolate(self, iid):
        for i in self.doc.instances:
            i["hidden"] = i["id"] != iid
        self.doc.notify()

    def show_all(self):
        for i in self.doc.instances:
            i["hidden"] = False
        self.doc.notify()

    def duplicate(self, ids):
        doc = self.doc
        doc.checkpoint()
        for iid in ids:
            inst = doc.instance(iid)
            lo, hi = K.bbox(doc.world_shape(iid))
            T = A.translation([0, (hi[1] - lo[1]) * 1.2 + 5, 0]) @ np.array(inst["T"], float)
            new = doc.insert(inst["source"], T=T, name=inst.get("part_name"), checkpoint=False)
            if inst.get("color"):
                doc.instance(new)["color"] = inst["color"]

    def delete_instances(self, ids):
        names = ", ".join(self.doc.instance(i)["name"] for i in ids)
        nm = sum(1 for m in self.doc.mates if m["a"]["inst"] in ids or m["b"]["inst"] in ids)
        extra = f"\n\n{nm} mate(s) on them will be deleted too." if nm else ""
        if QMessageBox.question(self.w, "Delete", f"Delete {names}?{extra}") == QMessageBox.Yes:
            self.doc.delete_instances(ids)

    def delete_selected(self):
        mids = self.panel.selected_mates()
        if mids:
            self.doc.delete_mates(mids)
            return
        ids = self.selected_instance_ids()
        if ids:
            self.delete_instances(ids)

    def open_source(self, iid):
        inst = self.doc.instance(iid)
        if inst is None:
            return
        src = inst["source"]
        if src.get("kind") != "studio":
            self.w.status.showMessage("This part came from a STEP file — there's no Part Studio to open", 5000)
            return
        studio = self.w.studio_by_uid(src.get("uid"), src.get("path"), load=True, show=True)
        if studio is None:
            QMessageBox.information(self.w, "Open Part Studio", "The Part Studio for this part isn't open and its "
                                    "file can't be found.")

    # ------------------------------------------------------------------ analysis
    def toggle_interference(self, on=None):
        btn = self.w.btn_interf
        if on is None:
            on = btn.isChecked()
        if not on:
            self._interf = None
            self.vp.ghosts = []
            self.vp.update()
            btn.setChecked(False)
            return
        from PySide6.QtWidgets import QProgressDialog, QApplication
        prog = QProgressDialog("Checking for interference…", None, 0, 0, self.w)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)

        def step(n, total):
            prog.setMaximum(max(total, 1))
            prog.setValue(n)
            QApplication.processEvents()
        try:
            res = self.doc.interferences(step)
        finally:
            prog.close()
        doc = self.doc
        if not res:
            self._interf = None
            btn.setChecked(False)
            self.vp.ghosts = []
            self.vp.update()
            QMessageBox.information(self.w, "Interference", "No interference — no parts overlap.")
            return
        ghosts = []
        lines = []
        for a, b, v, ov in res:
            try:
                ghosts.append((K.tessellate(ov), (0.92, 0.15, 0.12, 0.55)))
            except Exception:
                pass
            lines.append(f"• {doc.instance(a)['name']}  ×  {doc.instance(b)['name']}:  {fmt(v, 2)} mm³")
        self._interf = ghosts
        btn.setChecked(True)
        self.vp.ghosts = ghosts
        self.vp.update()
        self.w.status.showMessage(f"{len(res)} interference(s) found — shown in red. Click the button again "
                                  "to clear.", 8000)
        QMessageBox.information(self.w, "Interference", f"{len(res)} pair(s) of parts overlap "
                                "(shown in red):\n\n" + "\n".join(lines[:30]))

    def show_bom(self):
        if not self.doc.instances:
            QMessageBox.information(self.w, "Bill of materials", "Insert some parts first.")
            return
        d = BomDialog(self.w, self.doc)
        self.w._adapt_tree(d)
        d.exec()

    def escape(self):
        if self.mate_dialog is not None:
            self.mate_dialog.reject()
        elif self.move_dialog is not None:
            self.move_dialog.reject()
        elif self.insert_panel is not None:
            self.close_insert()
        else:
            self.vp.clear_selection()
            self.panel.select_instances(set())
            self.panel.mates.clearSelection()
            self.update_triads()

    def leave(self):
        """Close any open assembly dialog (switching tabs etc.)."""
        if self.mate_dialog is not None:
            self.mate_dialog.reject()
        if self.move_dialog is not None:
            self.move_dialog.reject()
        self.close_insert()
        self._drag = None
        self.vp.triads = []
        self.vp.ghosts = []
        self._interf = None
        if hasattr(self.w, "btn_interf"):
            self.w.btn_interf.setChecked(False)
