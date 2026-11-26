"""The S menu: press S over the model for a grid of the tools you use most, like Onshape's shortcut toolbar.

Each place has its own menu (Sketch, Part Studio, Assembly, Drawing), and each one is yours to fill: any tool on
that toolbar, its ▾ menus included (plugins too), in the order you choose. The choices are kept in the app's
settings, so they last between sessions."""
from __future__ import annotations

from ..i18n import tr

import json
import re

from PySide6.QtCore import Qt, QSize, QTimer, QPoint
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (QFrame, QGridLayout, QVBoxLayout, QHBoxLayout, QToolButton, QLabel, QDialog,
                               QListWidget, QListWidgetItem, QPushButton, QComboBox, QDialogButtonBox,
                               QAbstractItemView)

# (key, name shown, toolbar page)
CONTEXTS = [("sketch", "Sketch", 1), ("studio", "Part Studio", 0), ("assembly", "Assembly", 2),
            ("drawing", "Drawing", 3)]
PAGE = {k: page for k, _n, page in CONTEXTS}
NAMES = {k: n for k, n, _p in CONTEXTS}

DEFAULTS = {
    "sketch": ["line", "rect", "circle", "arc3", "dimension", "coincident", "construction", "sk_fillet", "trim",
               "offset", "use", "finish_sketch"],
    "studio": ["sketch", "extrude", "revolve", "fillet", "chamfer", "shell", "hole", "plane", "linear_pattern",
               "boolean"],
    "assembly": ["insert", "m_fastened", "m_revolute", "m_slider", "move", "explode"],
    "drawing": ["base_view", "proj_view", "section_view", "detail_view", "dim", "note"],
}
SETTINGS_KEY = "s_menu/"


def _label(text: str) -> str:
    """A tool's name from its tooltip: before any " — " explanation, without the "(Shift+F)" key hint."""
    name = (text or "").split(" — ")[0].strip()
    m = re.match(r"^(.*?)\s*\(([^()]{1,24})\)$", name)
    return m.group(1) if m and m.group(1) else name


def _key(text: str) -> str:
    """The key hint in a tooltip, e.g. "Shift+F" from "Fillet (Shift+F) — …", or ""."""
    m = re.match(r"^.*?\(([^()]{1,24})\)$", (text or "").split(" — ")[0].strip())
    return m.group(1) if m else ""


def commands(win, ctx: str) -> list[dict]:
    """Every tool on the toolbar for `ctx`, in toolbar order: [{"id", "label", "icon", "run", "enabled"}]. A ▾
    button gives each tool in its menu (not the button itself)."""
    page = win.tool_stack.widget(PAGE[ctx])
    inner = getattr(page, "inner", page)
    names = {id(a): n for a, n in getattr(win, "_menu_actions", [])}
    out, seen = [], set()

    def add(cid, label, ic, run, enabled, key=""):
        if cid and cid not in seen and label:
            seen.add(cid)
            out.append({"id": cid, "label": label, "icon": ic, "run": run, "enabled": enabled, "key": key})
    for b in inner.findChildren(QToolButton):
        menu = b.menu()
        if menu is not None:
            for a in menu.actions():
                if a.isSeparator() or a.menu() is not None:
                    continue
                cid = a.property("icon_name") or names.get(id(a))
                add(cid, _label(a.text()), a.icon(), a.trigger, a.isEnabled, _key(a.toolTip() or a.text()))
            continue
        cid = b.property("icon_name")
        if cid == "__arrow":
            cid = "select"
        label = _label(b.toolTip()) or b.text().replace("✓", "").strip()
        ic = b.icon()
        if not cid and b.text():                       # a text button, like "✓ Finish sketch"
            cid = "_".join(b.text().replace("✓", "").lower().split())
            from .icons import icon
            ic = icon(cid)
        add(cid, label, ic, b.click, b.isEnabled, _key(b.toolTip()))
    return out


def _settings(win):
    s = getattr(win, "settings", None)
    if s is None:
        from PySide6.QtCore import QSettings
        s = QSettings("FreeShape", "FreeShape")
    return s


def chosen(win, ctx: str, available=None) -> list[str]:
    """The tool ids in the S menu for `ctx`: yours if you've set them, else the defaults (only those that exist)."""
    raw = _settings(win).value(SETTINGS_KEY + ctx)
    ids = None
    if raw:
        try:
            ids = [str(i) for i in json.loads(raw)]
        except (TypeError, ValueError):
            ids = None
    if ids is None:
        ids = DEFAULTS.get(ctx, [])
    have = {c["id"] for c in (available if available is not None else commands(win, ctx))}
    return [i for i in ids if i in have]


def save(win, ctx: str, ids):
    _settings(win).setValue(SETTINGS_KEY + ctx, json.dumps(list(ids)))


def reset(win, ctx: str):
    _settings(win).remove(SETTINGS_KEY + ctx)


def current_context(win) -> str:
    if getattr(win, "mode", None) == "sketch":
        return "sketch"
    if getattr(win, "is_dwg", False):
        return "drawing"
    if getattr(win, "is_asm", False):
        return "assembly"
    return "studio"


class ShortcutPopup(QFrame):
    """The grid that S opens at the mouse. Click a tool (or press its number), Esc to close."""
    COLS = 6

    def __init__(self, win, ctx):
        super().__init__(win, Qt.Popup | Qt.FramelessWindowHint)
        self.win, self.ctx = win, ctx
        self.setObjectName("sMenu")
        self.setFrameShape(QFrame.StyledPanel)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        head = QHBoxLayout()
        title = QLabel(f"<b>{tr(NAMES[ctx])}</b>")
        head.addWidget(title)
        head.addStretch(1)
        cust = QToolButton()
        cust.setText(tr("Customize…"))
        cust.setAutoRaise(True)
        cust.clicked.connect(self._customize)
        head.addWidget(cust)
        lay.addLayout(head)
        cmds = {c["id"]: c for c in commands(win, ctx)}
        self.items = [cmds[i] for i in chosen(win, ctx, list(cmds.values()))]
        self.buttons = []
        if not self.items:
            lay.addWidget(QLabel(tr("Nothing here yet: click Customize… to choose your tools.")))
        grid = QGridLayout()
        grid.setSpacing(2)
        for k, c in enumerate(self.items):
            b = QToolButton()
            b.setIcon(c["icon"])
            b.setIconSize(QSize(26, 26))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            text = tr(c["label"])
            b.setText(text if len(text) <= 14 else text[:13] + "…")
            keys = [x for x in (str(k + 1) if k < 9 else "", c.get("key", "")) if x]
            b.setToolTip(tr(c["label"]) + (f"  ({', '.join(keys)})" if keys else ""))
            b.setFixedSize(92, 62)
            b.setAutoRaise(True)
            b.setEnabled(bool(c["enabled"]()))
            b.clicked.connect(lambda _=False, c=c: self.run(c))
            grid.addWidget(b, k // self.COLS, k % self.COLS)
            self.buttons.append(b)
        lay.addLayout(grid)

    def run(self, c):
        self.close()
        QTimer.singleShot(0, c["run"])

    def keyPressEvent(self, e):
        k = e.key()
        if Qt.Key_1 <= k <= Qt.Key_9 and not e.modifiers():
            i = k - Qt.Key_1
            if i < len(self.items) and self.buttons[i].isEnabled():
                self.run(self.items[i])
            return
        if k == Qt.Key_S and not e.modifiers():         # S again: close
            self.close()
            return
        super().keyPressEvent(e)

    def _customize(self):
        self.close()
        QTimer.singleShot(0, lambda: CustomizeDialog(self.win, self.ctx).exec())

    def popup_at(self, pos: QPoint | None = None):
        pos = pos or QCursor.pos()
        self.adjustSize()
        size = self.sizeHint()
        x, y = pos.x() - size.width() // 2, pos.y() - 20
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            x = max(g.left(), min(x, g.right() - size.width()))
            y = max(g.top(), min(y, g.bottom() - size.height()))
        self.move(x, y)
        self.show()
        self.setFocus()


class CustomizeDialog(QDialog):
    """Choose and order the tools in each S menu."""

    def __init__(self, win, ctx="sketch"):
        super().__init__(win)
        self.win = win
        self.setWindowTitle(tr("Customize the S menu"))
        self.resize(640, 460)
        self.pending = {}                                # ctx -> ids (edited, not saved yet)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Menu for:")))
        self.ctx_box = QComboBox()
        for k, n, _p in CONTEXTS:
            self.ctx_box.addItem(n, k)
        top.addWidget(self.ctx_box)
        top.addStretch(1)
        lay.addLayout(top)
        hint = QLabel("Press S over the model to open it. Double-click a tool to add or remove it; drag (or use "
                      "the arrows) to change the order.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        mid = QHBoxLayout()
        left = QVBoxLayout()
        left.addWidget(QLabel(tr("Tools")))
        self.avail = QListWidget()
        left.addWidget(self.avail)
        mid.addLayout(left, 1)
        btns = QVBoxLayout()
        btns.addStretch(1)
        self.b_add, self.b_rem = QPushButton(tr("Add →")), QPushButton(tr("← Remove"))
        self.b_up, self.b_down = QPushButton(tr("Up")), QPushButton(tr("Down"))
        for b in (self.b_add, self.b_rem, self.b_up, self.b_down):
            btns.addWidget(b)
        btns.addStretch(1)
        mid.addLayout(btns)
        right = QVBoxLayout()
        right.addWidget(QLabel(tr("In the S menu")))
        self.mine = QListWidget()
        self.mine.setDragDropMode(QAbstractItemView.InternalMove)
        right.addWidget(self.mine)
        mid.addLayout(right, 1)
        lay.addLayout(mid, 1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        bb.button(QDialogButtonBox.Reset).setText(tr("Reset to default"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Reset).clicked.connect(self.reset_current)
        lay.addWidget(bb)
        self.b_add.clicked.connect(self.add_selected)
        self.b_rem.clicked.connect(self.remove_selected)
        self.b_up.clicked.connect(lambda: self.move_selected(-1))
        self.b_down.clicked.connect(lambda: self.move_selected(1))
        self.avail.itemDoubleClicked.connect(lambda _i: self.add_selected())
        self.mine.itemDoubleClicked.connect(lambda _i: self.remove_selected())
        self.ctx_box.currentIndexChanged.connect(lambda _i: self._switch())
        self._ctx = None
        self.ctx_box.setCurrentIndex(max(0, self.ctx_box.findData(ctx)))
        self._switch()

    # ---- lists
    def _item(self, c):
        it = QListWidgetItem(c["icon"], tr(c["label"]))
        it.setData(Qt.UserRole, c["id"])
        return it

    def ids(self):
        return [self.mine.item(i).data(Qt.UserRole) for i in range(self.mine.count())]

    def _fill(self, ids):
        self.avail.clear()
        self.mine.clear()
        for i in ids:
            if i in self._cmds:
                self.mine.addItem(self._item(self._cmds[i]))
        for c in self._cmds.values():
            if c["id"] not in ids:
                self.avail.addItem(self._item(c))

    def _switch(self):
        if self._ctx is not None:
            self.pending[self._ctx] = self.ids()
        self._ctx = self.ctx_box.currentData()
        self._cmds = {c["id"]: c for c in commands(self.win, self._ctx)}
        ids = self.pending.get(self._ctx)
        if ids is None:
            ids = chosen(self.win, self._ctx, list(self._cmds.values()))
        self._fill(ids)

    def add_selected(self):
        for it in self.avail.selectedItems() or ([self.avail.currentItem()] if self.avail.currentItem() else []):
            self.mine.addItem(self._item(self._cmds[it.data(Qt.UserRole)]))
            self.avail.takeItem(self.avail.row(it))

    def remove_selected(self):
        ids = self.ids()
        for it in self.mine.selectedItems() or ([self.mine.currentItem()] if self.mine.currentItem() else []):
            ids.remove(it.data(Qt.UserRole))
        self._fill(ids)

    def move_selected(self, step):
        row = self.mine.currentRow()
        new = row + step
        if row < 0 or not (0 <= new < self.mine.count()):
            return
        it = self.mine.takeItem(row)
        self.mine.insertItem(new, it)
        self.mine.setCurrentRow(new)

    def reset_current(self):
        self.pending[self._ctx] = [i for i in DEFAULTS.get(self._ctx, []) if i in self._cmds]
        self._fill(self.pending[self._ctx])

    def accept(self):
        self.pending[self._ctx] = self.ids()
        for ctx, ids in self.pending.items():
            if ids == [i for i in DEFAULTS.get(ctx, []) if i in {c["id"] for c in commands(self.win, ctx)}]:
                reset(self.win, ctx)                   # same as the default: follow future defaults
            else:
                save(self.win, ctx, ids)
        super().accept()
