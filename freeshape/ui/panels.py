"""Left-hand panels: the feature list (with rollback bar) and the parts list."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter, QFont, QBrush
from PySide6.QtWidgets import (
    QTabBar, QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QFrame, QAbstractItemView,
    QMenu, QHBoxLayout, QToolButton,
)

from .icons import icon
from . import theme

ROLE_KIND = Qt.UserRole
ROLE_ID = Qt.UserRole + 1
STD_PLANES = ("Top", "Front", "Right")


def swatch(color, size=14):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor.fromRgbF(*color))
    p.setPen(QColor(90, 100, 115))
    p.drawRoundedRect(1, 1, size - 2, size - 2, 3, 3)
    p.end()
    return QIcon(pm)


class RollbackBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.setStyleSheet("background: #1f6fd1; border-radius: 2px; margin: 2px 4px;")
        self.setToolTip("Rollback bar — drag to roll the model back")
        self.setCursor(Qt.SizeVerCursor)


class FeatureList(QListWidget):
    editRequested = Signal(str)
    contextRequested = Signal(str, object)   # kind+id, global pos
    orderChanged = Signal(list, int)         # feature ids in order, rollback index
    planeClicked = Signal(str)
    featureHovered = Signal(object)          # feature id under the mouse, or None

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self._hover_fid = None
        self.itemEntered.connect(self._entered)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(18, 18))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemDoubleClicked.connect(self._dbl)
        self.itemClicked.connect(self._clicked)
        self._filter = ""

    def populate(self, doc, editing_id=None):
        self.blockSignals(True)
        self.clear()
        bold = QFont()
        bold.setBold(True)
        it = QListWidgetItem(icon("origin"), "Origin")
        it.setData(ROLE_KIND, "origin")
        it.setFlags(Qt.ItemIsEnabled)
        self.addItem(it)
        for name in STD_PLANES:
            it = QListWidgetItem(icon("plane"), name)
            it.setData(ROLE_KIND, "std_plane")
            it.setData(ROLE_ID, name)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if name in doc.hidden_features:
                it.setForeground(QColor("#9aa3ae"))
            self.addItem(it)
        active = doc.active_count()
        for i, f in enumerate(doc.features):
            if i == active:
                self._add_rollback()
            if self._filter and self._filter.lower() not in f["name"].lower():
                continue
            it = QListWidgetItem(icon(f["type"]), f["name"])
            it.setData(ROLE_KIND, "feature")
            it.setData(ROLE_ID, f["id"])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            err = doc.errors.get(f["id"])
            if err:
                it.setForeground(QColor("#d93025"))
                it.setIcon(icon("error"))
                it.setToolTip(err)
            elif f.get("suppressed"):
                it.setForeground(QColor("#9aa3ae"))
                font = QFont()
                font.setStrikeOut(True)
                it.setFont(font)
                it.setToolTip("Suppressed")
            elif i >= active:
                it.setForeground(QColor("#9aa3ae"))
                it.setToolTip("Rolled back")
            elif f["id"] in doc.hidden_features:
                it.setForeground(QColor("#8a94a3"))
            if f["id"] == editing_id:
                it.setBackground(QColor(theme.SELECTED_ROW))
                it.setFont(bold)
            self.addItem(it)
        if active >= len(doc.features):
            self._add_rollback()
        self.blockSignals(False)

    def _entered(self, it):
        fid = it.data(ROLE_ID) if it is not None and it.data(ROLE_KIND) == "feature" else None
        if fid != self._hover_fid:
            self._hover_fid = fid
            self.featureHovered.emit(fid)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        if self.itemAt(e.position().toPoint()) is None and self._hover_fid is not None:
            self._hover_fid = None
            self.featureHovered.emit(None)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        if self._hover_fid is not None:
            self._hover_fid = None
            self.featureHovered.emit(None)

    def _add_rollback(self):
        it = QListWidgetItem()
        it.setData(ROLE_KIND, "rollback")
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsDragEnabled | Qt.ItemIsSelectable)
        it.setSizeHint(QSize(10, 12))
        self.addItem(it)
        self.setItemWidget(it, RollbackBar())

    def set_filter(self, text):
        self._filter = text

    def dropEvent(self, e):
        super().dropEvent(e)
        ids, rb, seen_bar = [], None, False
        for i in range(self.count()):
            it = self.item(i)
            k = it.data(ROLE_KIND)
            if k == "feature":
                ids.append(it.data(ROLE_ID))
            elif k == "rollback":
                rb = len(ids)
        if rb is None:
            rb = len(ids)
        self.orderChanged.emit(ids, rb)

    def _dbl(self, it):
        if it.data(ROLE_KIND) == "feature":
            self.editRequested.emit(it.data(ROLE_ID))

    def _clicked(self, it):
        if it.data(ROLE_KIND) == "std_plane":
            self.planeClicked.emit("std:" + it.data(ROLE_ID))
        elif it.data(ROLE_KIND) == "feature":
            self.planeClicked.emit("feature:" + it.data(ROLE_ID))

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return
        k = it.data(ROLE_KIND)
        if k in ("feature", "std_plane"):
            self.contextRequested.emit(f"{k}:{it.data(ROLE_ID)}", self.viewport().mapToGlobal(pos))

    def selected_feature_ids(self):
        return [it.data(ROLE_ID) for it in self.selectedItems() if it.data(ROLE_KIND) == "feature"]


class PartsList(QListWidget):
    partClicked = Signal(str, bool)
    contextRequested = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(14, 14))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemClicked.connect(lambda it: self.partClicked.emit(it.data(ROLE_ID), False))

    def populate(self, doc):
        self.blockSignals(True)
        self.clear()
        for p in doc.state.parts.values():
            it = QListWidgetItem(swatch(p.color), p.name)
            it.setData(ROLE_ID, p.id)
            if p.id in doc.hidden_parts:
                it.setForeground(QColor("#9aa3ae"))
                it.setToolTip("Hidden")
            self.addItem(it)
        self.blockSignals(False)

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it is not None:
            self.contextRequested.emit(it.data(ROLE_ID), self.viewport().mapToGlobal(pos))


class LeftPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("leftpanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.feat_header = QLabel("Features")
        self.feat_header.setObjectName("sectionHeader")
        lay.addWidget(self.feat_header)
        self.filter = QLineEdit()
        self.filter.setObjectName("filter")
        self.filter.setPlaceholderText("Filter by name")
        lay.addWidget(self.filter)
        self.features = FeatureList()
        lay.addWidget(self.features, 3)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background:#d5d9de;")
        lay.addWidget(sep)
        self.parts_header = QLabel("Parts")
        self.parts_header.setObjectName("sectionHeader")
        lay.addWidget(self.parts_header)
        self.parts = PartsList()
        lay.addWidget(self.parts, 2)
        self.setMinimumWidth(220)


class DocTabBar(QTabBar):
    """Bottom document tabs styled like Onshape's: flat, icon + name, blue underline on the active tab.
    No close buttons — close from the right-click menu, with a middle-click or with Ctrl+W."""
    middleClicked = Signal(int)

    STYLE = """
    QTabBar { background: transparent; }
    QTabBar::tab {
        background: #eef0f3; color: #3b4250;
        border: none; border-right: 1px solid #d5d9de; border-bottom: 3px solid transparent;
        padding: 6px 14px 4px 10px; margin: 0; min-width: 48px; max-width: 230px;
    }
    QTabBar::tab:hover:!selected { background: #e3e8ef; }
    QTabBar::tab:selected { background: #ffffff; color: #1f2328; border-bottom: 3px solid #1f6fd1; }
    QTabBar::scroller { width: 26px; }
    QTabBar QToolButton { background: #eef0f3; border: none; }
    """

    def __init__(self):
        super().__init__()
        self.setDrawBase(False)
        self.setMovable(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.ElideRight)
        self.setIconSize(QSize(16, 16))
        self.setStyleSheet(self.STYLE)
        self.setDocumentMode(True)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            i = self.tabAt(e.position().toPoint())
            if i >= 0:
                self.middleClicked.emit(i)
                return
        super().mouseReleaseEvent(e)
