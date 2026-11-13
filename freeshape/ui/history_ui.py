"""The Version history panel: versions saved in the document, what changed between any two (or since one), shown
in words and in 3D (added material green, removed red), and restoring or opening an old version."""
from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QTextBrowser, QCheckBox,
    QAbstractItemView, QDialog, QLineEdit, QPlainTextEdit, QDialogButtonBox, QMenu, QMessageBox, QApplication,
)

from .. import history as H
from .. import kernel as K
from .assembly_ui import _floating, _head, _hline

ADDED = (0.18, 0.72, 0.30, 0.55)
REMOVED = (0.90, 0.20, 0.15, 0.55)
NOW = "__now__"


def version_dialog(parent, title="Create version", name="", note=""):
    """(name, note) or None."""
    d = QDialog(parent)
    d.setWindowTitle(title)
    lay = QVBoxLayout(d)
    lay.addWidget(QLabel("Name"))
    ne = QLineEdit(name)
    ne.setPlaceholderText("e.g. Sent for quote")
    lay.addWidget(ne)
    lay.addWidget(QLabel("Description (optional)"))
    te = QPlainTextEdit(note)
    te.setFixedHeight(70)
    lay.addWidget(te)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    lay.addWidget(bb)
    ne.selectAll()
    if d.exec() != QDialog.Accepted:
        return None
    return ne.text().strip(), te.toPlainText().strip()


class HistoryPanel(QFrame):
    def __init__(self, main):
        super().__init__(main.viewport)
        _floating(self)
        self.main = main
        self.doc = main.doc
        self.setMinimumWidth(340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head, t = _head(self, "Version history", "history", None, self.close_panel, cancel_tip="Close (Esc)")
        t.setReadOnly(True)
        lay.addLayout(head)
        lay.addWidget(_hline())
        row = QHBoxLayout()
        mk = QPushButton("Create version…")
        mk.setToolTip("Keep the model as it is now as a named version (saved in the file)")
        mk.clicked.connect(self.create_version)
        row.addWidget(mk)
        row.addStretch(1)
        lay.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.list.itemDoubleClicked.connect(lambda it: self.rename(it.data(Qt.UserRole)))
        self.list.setMinimumHeight(150)
        lay.addWidget(self.list, 2)
        self.hint = QLabel("Click a version to see what has changed since. Ctrl+click two to compare them.")
        self.hint.setWordWrap(True)
        self.hint.setObjectName("muted")
        lay.addWidget(self.hint)
        self.text = QTextBrowser()
        self.text.setMinimumHeight(170)
        lay.addWidget(self.text, 3)
        self.show3d = QCheckBox("Show in 3D  (added material green, removed red)")
        self.show3d.setChecked(True)
        self.show3d.toggled.connect(lambda _v: self._ghosts())
        lay.addWidget(self.show3d)
        row = QHBoxLayout()
        self.btn_restore = QPushButton("Restore")
        self.btn_restore.setToolTip("Go back to this version (you can undo it)")
        self.btn_restore.clicked.connect(self.restore)
        self.btn_open = QPushButton("Open in a new tab")
        self.btn_open.clicked.connect(self.open_copy)
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        lay.addLayout(row)
        self._res = None
        self._pair = None
        self._listener = lambda: QTimer.singleShot(0, self._doc_changed)
        self.doc.listeners.append(self._listener)
        self.fill()
        self.resize(380, min(720, max(520, main.viewport.height() - 24)))
        self.move(12, 12)
        self.show()
        self.raise_()

    # ------------------------------------------------------------------ list
    def fill(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        changed = not self.doc.versions or not H._same(self.doc.versions[-1]["snapshot"], H.snapshot(self.doc))
        it = QListWidgetItem("● Now" + ("  — changed since the last version" if changed and self.doc.versions
                                         else ""))
        it.setData(Qt.UserRole, NOW)
        f = it.font()
        f.setBold(True)
        it.setFont(f)
        self.list.addItem(it)
        for v in reversed(self.doc.versions):
            label = f"{v['name']}   ·   {H.nice_time(v.get('time'))}"
            if v.get("note"):
                label += "\n" + v["note"]
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, v["id"])
            it.setToolTip(v.get("note") or ("Saved automatically" if v.get("auto") else v["name"]))
            if v.get("auto"):
                it.setForeground(QColor("#6b7280"))
            else:
                ff = it.font()
                ff.setBold(True)
                it.setFont(ff)
            self.list.addItem(it)
        self.list.blockSignals(False)
        if select:
            for i in range(self.list.count()):
                if self.list.item(i).data(Qt.UserRole) in select:
                    self.list.item(i).setSelected(True)
        if not self.doc.versions:
            self.text.setHtml("<p style='color:#888'>No versions yet. Click <b>Create version…</b> to keep the "
                              "model as it is now, or just save: each save keeps a version when something "
                              "changed.</p>")
        self._selection_changed()

    def _selected(self):
        ids = [it.data(Qt.UserRole) for it in self.list.selectedItems()]
        order = [NOW] + [v["id"] for v in reversed(self.doc.versions)]
        return sorted(ids, key=order.index)              # newest first

    def _selection_changed(self):
        ids = self._selected()
        one = [i for i in ids if i != NOW]
        self.btn_restore.setEnabled(len(ids) == 1 and bool(one))
        self.btn_open.setEnabled(len(ids) == 1 and bool(one))
        if not ids or ids == [NOW]:
            self._pair = None
            self._res = None
            self._ghosts()
            if self.doc.versions and ids == [NOW]:
                self.text.setHtml("<p style='color:#888'>Pick a version to compare with now.</p>")
            return
        if len(ids) > 2:
            for it in self.list.selectedItems()[2:]:
                it.setSelected(False)
            return
        new, old = (ids[0], ids[1]) if len(ids) == 2 else (NOW, ids[0])
        self._pair = (old, None if new == NOW else new)
        self.text.setHtml("<p style='color:#888'>Working out what changed…</p>")
        QTimer.singleShot(10, self._compare)

    def _label(self, vid):
        if vid is None:
            return "now"
        v = H.version(self.doc, vid)
        return v["name"] if v else vid

    def _compare(self):
        if self._pair is None:
            return
        old, new = self._pair
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = H.compare(self.doc, old, new, geometry=True)
        except Exception as e:
            res = None
            self.text.setHtml(f"<p style='color:#d93025'>Couldn't compare: {e}</p>")
        finally:
            QApplication.restoreOverrideCursor()
        self._res = res
        if res is not None:
            head = f"<p style='color:#555'>From <b>{H._esc(self._label(old))}</b> to " \
                   f"<b>{H._esc(self._label(new))}</b>:</p>"
            self.text.setHtml(head + H.to_html(res, self._label(old), self._label(new)))
        self._ghosts()

    def _ghosts(self):
        vp = self.main.viewport
        g = (self._res or {}).get("geometry") if self.show3d.isChecked() else None
        ghosts = []
        for shapes, col in ((g or {}).get("added", []), ADDED), ((g or {}).get("removed", []), REMOVED):
            for sh in shapes:
                try:
                    ghosts.append((K.tessellate(sh), col))
                except Exception:
                    pass
        vp.ghosts = ghosts
        vp.update()

    def _doc_changed(self):
        if self.main.doc is not self.doc:
            self.close_panel()
            return
        keep = self._selected()
        self.fill(select=keep)

    # ------------------------------------------------------------------ actions
    def create_version(self):
        got = version_dialog(self, name=f"Version {sum(1 for v in self.doc.versions if not v.get('auto')) + 1}")
        if got is None:
            return
        v = H.create(self.doc, got[0] or None, got[1])
        self.doc.dirty = True
        self.main._populate_lists()
        self.fill(select=[v["id"]])

    def rename(self, vid):
        v = H.version(self.doc, vid)
        if v is None:
            return
        got = version_dialog(self, "Rename version", v["name"], v.get("note", ""))
        if got is None:
            return
        v["name"] = got[0] or v["name"]
        v["note"] = got[1]
        v["auto"] = False                     # a renamed automatic version is kept like a named one
        self.doc.dirty = True
        self.fill(select=[vid])

    def delete(self, vid):
        v = H.version(self.doc, vid)
        if v is None:
            return
        self.doc.versions.remove(v)
        H._prune_blobs(self.doc)
        self.doc.dirty = True
        self.fill()

    def restore(self):
        ids = [i for i in self._selected() if i != NOW]
        if len(ids) != 1:
            return
        v = H.version(self.doc, ids[0])
        if QMessageBox.question(self, "Restore version",
                                f"Go back to “{v['name']}”? The model changes to what it was then; Undo brings it "
                                "back, and saving keeps a version of how it is now first.") != QMessageBox.Yes:
            return
        if self.doc.versions and not H._same(self.doc.versions[-1]["snapshot"], H.snapshot(self.doc)):
            H.create(self.doc, f"Before restoring {v['name']}", auto=False)
        H.restore(self.doc, v["id"])
        self.main._after_plugin()
        self.fill()

    def open_copy(self):
        ids = [i for i in self._selected() if i != NOW]
        if len(ids) != 1:
            return
        from ..document import Document
        v = H.version(self.doc, ids[0])
        s = H.expanded(self.doc, v)
        d = Document()
        d.name = f"{self.doc.name} ({v['name']})"
        d.features = s["features"]
        d.configs = copy.deepcopy(s.get("configs") or {"rows": [], "active": 0})
        d.folders = copy.deepcopy(s.get("folders") or {})
        d.part_overrides = copy.deepcopy(s.get("overrides") or {})
        d.rollback = s.get("rollback")
        d.derived_fallback = self.doc.derived_fallback
        d.plugin_fallback = self.doc.plugin_fallback
        nums = [int("".join(c for c in f["id"] if c.isdigit()) or 0) for f in d.features]
        d._counter = max(nums + [0])
        d.rebuild()
        self.close_panel()
        self.main._add_tab(d)

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        vid = it.data(Qt.UserRole) if it else None
        if not vid or vid == NOW:
            return
        m = QMenu(self)
        m.addAction("Rename / describe…").triggered.connect(lambda: self.rename(vid))
        m.addAction("Delete version").triggered.connect(lambda: self.delete(vid))
        m.exec(self.list.mapToGlobal(pos))

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close_panel()
        else:
            super().keyPressEvent(e)

    def close_panel(self):
        if self._listener in self.doc.listeners:
            self.doc.listeners.remove(self._listener)
        self.main.viewport.ghosts = []
        self.main.viewport.update()
        if getattr(self.main, "history_panel", None) is self:
            self.main.history_panel = None
        self.hide()
        self.deleteLater()
