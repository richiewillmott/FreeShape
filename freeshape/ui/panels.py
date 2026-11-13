"""Left-hand panels: the feature list (with rollback bar) and the parts list."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal, QSize, QRect, QPointF
from PySide6.QtGui import QColor, QIcon, QPixmap, QPainter, QFont, QBrush, QPainterPath, QPen
from PySide6.QtWidgets import (
    QTabBar, QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QFrame, QAbstractItemView,
    QMenu, QHBoxLayout, QToolButton, QStyledItemDelegate, QStyleOptionViewItem, QStyle,
)

from .icons import icon
from . import theme

ROLE_KIND = Qt.UserRole
ROLE_ID = Qt.UserRole + 1
ROLE_EYE = Qt.UserRole + 2        # None (no eye), "shown" or "hidden"
ROLE_INDENT = Qt.UserRole + 3     # True for a feature shown inside a folder
ROLE_OPEN = Qt.UserRole + 4       # a folder row: True if expanded
CHEVRON_W = 16                    # px: the expand / collapse arrow at the left of a folder row
INDENT_W = 20                     # px: how far a folder's features are indented
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


EYE_SPACE = 28        # px kept clear at the right of a row for its eye


def eye_rect(row_rect, right=None):
    """Where a row's show / hide eye sits: at its right-hand end, like Onshape. `right` is the visible right edge
    of the list, so the eye stays in view even when a long name makes the row wider than the panel."""
    r = row_rect.right() if right is None else min(row_rect.right(), right)
    return QRect(r - 24, row_rect.center().y() - 8, 18, 16)


def paint_eye(p, r, hidden, hover):
    """An eye (shown) or a crossed-out eye (hidden)."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor("#1f6fd1") if hover else QColor("#5f6b7a")
    if hidden and not hover:
        col = QColor("#a3acb8")
    pen = QPen(col, 1.6)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    cx, cy, w, h = r.center().x() + 0.5, r.center().y() + 0.5, r.width() - 2, r.height() * 0.55
    path = QPainterPath(QPointF(cx - w / 2, cy))
    path.quadTo(QPointF(cx, cy - h), QPointF(cx + w / 2, cy))
    path.quadTo(QPointF(cx, cy + h), QPointF(cx - w / 2, cy))
    p.drawPath(path)
    p.setBrush(col)
    p.drawEllipse(QPointF(cx, cy), 2.4, 2.4)
    if hidden:
        p.drawLine(QPointF(cx - w / 2 + 1, cy + h - 0.5), QPointF(cx + w / 2 - 1, cy - h + 0.5))
    p.restore()


def chevron_rect(row_rect):
    return QRect(row_rect.left() + 2, row_rect.center().y() - 6, CHEVRON_W - 4, 12)


def paint_chevron(p, r, open_):
    """The folder row's arrow: pointing right when closed, down when open."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor("#5f6b7a"), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    c = QPointF(r.center().x() + 0.5, r.center().y() + 0.5)
    if open_:
        pts = [QPointF(c.x() - 4, c.y() - 2), QPointF(c.x(), c.y() + 2), QPointF(c.x() + 4, c.y() - 2)]
    else:
        pts = [QPointF(c.x() - 2, c.y() - 4), QPointF(c.x() + 2, c.y()), QPointF(c.x() - 2, c.y() + 4)]
    p.drawPolyline(pts)
    p.restore()


class _EyeDelegate(QStyledItemDelegate):
    """Draws the row as usual, plus Onshape's eye at the right: on the row under the mouse, and always (faint and
    crossed out) on rows that are hidden."""

    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def paint(self, p, opt, index):
        state = index.data(ROLE_EYE)
        right = self.view.viewport().rect().right()
        # the row's background across its full (visible) width, then the icon and text in the space left of the
        # eye column, so a long name is shortened with "…" instead of running under (or pushing out) the eye
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, index)
        o.rect = QRect(opt.rect.left(), opt.rect.top(), min(opt.rect.right(), right) - opt.rect.left() + 1,
                       opt.rect.height())
        style = self.view.style()
        style.drawPrimitive(QStyle.PE_PanelItemViewItem, o, p, self.view)
        is_folder = index.data(ROLE_KIND) == "folder"
        if is_folder:
            paint_chevron(p, chevron_rect(opt.rect), bool(index.data(ROLE_OPEN)))
            o.rect = o.rect.adjusted(CHEVRON_W, 0, 0, 0)
        elif index.data(ROLE_INDENT):
            o.rect = o.rect.adjusted(CHEVRON_W + INDENT_W, 0, 0, 0)
        if state:
            o.rect = o.rect.adjusted(0, 0, -EYE_SPACE, 0)
        o.textElideMode = Qt.ElideRight
        style.drawControl(QStyle.CE_ItemViewItem, o, p, self.view)
        if not state:
            return
        hover_row = getattr(self.view, "_eye_hover_row", -1) == index.row()
        if hover_row or state == "hidden":
            over = hover_row and getattr(self.view, "_eye_over", False)
            paint_eye(p, eye_rect(opt.rect, right), state == "hidden", over)


class _EyeMixin:
    """Gives a list the show / hide eye: hovering a row shows it, clicking it toggles that item's visibility."""

    def _eye_setup(self):
        self._eye_hover_row = -1
        self._eye_over = False
        self.setMouseTracking(True)
        self.setItemDelegate(_EyeDelegate(self))
        # names are shortened to fit rather than scrolled sideways, which would carry the eyes out of view
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideRight)

    def _eye_hit(self, pos):
        it = self.itemAt(pos)
        if it is None or not it.data(ROLE_EYE):
            return None
        r = eye_rect(self.visualItemRect(it), self.viewport().rect().right())
        return it if r.adjusted(-3, -3, 3, 3).contains(pos) else None

    def _eye_track(self, pos):
        it = self.itemAt(pos)
        row = self.row(it) if it is not None else -1
        over = self._eye_hit(pos) is not None
        if row != self._eye_hover_row or over != self._eye_over:
            self._eye_hover_row, self._eye_over = row, over
            self.viewport().update()
        if over:
            it = self.itemAt(pos)
            name = it.text()
            self.viewport().setToolTip(("Show " if it.data(ROLE_EYE) == "hidden" else "Hide ") + name)
            self.viewport().setCursor(Qt.PointingHandCursor)
        else:
            # a name shortened with "…" shows in full on hover
            it = self.itemAt(pos)
            self.viewport().setToolTip(it.toolTip() or it.text() if it is not None else "")
            self.viewport().unsetCursor()

    def _eye_leave(self):
        if self._eye_hover_row != -1:
            self._eye_hover_row, self._eye_over = -1, False
            self.viewport().update()


class RollbackBar(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(8)
        self.setStyleSheet("background: #1f6fd1; border-radius: 2px; margin: 2px 4px;")
        self.setToolTip("Rollback bar — drag to roll the model back")
        self.setCursor(Qt.SizeVerCursor)


class FeatureList(_EyeMixin, QListWidget):
    eyeClicked = Signal(str)                 # "std_plane:Top" / "feature:F3"
    editRequested = Signal(str)
    contextRequested = Signal(str, object)   # kind+id, global pos
    orderChanged = Signal(list, int, dict)   # feature ids in order, rollback index, feature id -> folder id
    folderToggled = Signal(str)              # folder id: expand / collapse
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
        self._folder_of = {}
        self._eye_setup()

    def populate(self, doc, editing_id=None):
        self.blockSignals(True)
        self.clear()
        self._folder_of = {f["id"]: f.get("folder") for f in doc.features}
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
            it.setData(ROLE_EYE, "hidden" if name in doc.hidden_features else "shown")
            if name in doc.hidden_features:
                it.setForeground(QColor("#9aa3ae"))
            self.addItem(it)
        active = doc.active_count()
        feats = doc.features
        folders = getattr(doc, "folders", {}) or {}
        flt = self._filter.lower()
        # a folder is shown open when it is expanded, the filter is on, the rollback bar or the feature being
        # edited is inside it — so neither is ever tucked out of sight
        forced = set()
        if 0 < active < len(feats) and feats[active].get("folder") and \
                feats[active].get("folder") == feats[active - 1].get("folder"):
            forced.add(feats[active]["folder"])
        for f in feats:
            if f["id"] == editing_id and f.get("folder"):
                forced.add(f["folder"])
        prev_folder = None
        for i, f in enumerate(feats):
            if i == active:
                self._add_rollback()
            fo = f.get("folder") if f.get("folder") in folders else None
            name_hit = not flt or flt in f["name"].lower()
            folder_hit = bool(fo) and bool(flt) and flt in folders[fo]["name"].lower()
            if fo and fo != prev_folder:
                members = [g for g in feats if g.get("folder") == fo]
                if not flt or folder_hit or any(flt in g["name"].lower() for g in members):
                    self._add_folder(doc, fo, folders[fo], members, fo in forced or bool(flt))
            prev_folder = fo
            if not (name_hit or folder_hit):
                continue
            it = QListWidgetItem(icon(f["type"]), f["name"])
            it.setData(ROLE_KIND, "feature")
            it.setData(ROLE_ID, f["id"])
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
            if fo:
                it.setData(ROLE_INDENT, True)
            if f["type"] in ("sketch", "plane"):
                it.setData(ROLE_EYE, "hidden" if f["id"] in doc.hidden_features else "shown")
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
            if fo and not (folders[fo].get("open", True) or fo in forced or flt):
                it.setHidden(True)
        if active >= len(doc.features):
            self._add_rollback()
        self.blockSignals(False)

    def _add_folder(self, doc, fo, info, members, force_open):
        open_ = bool(info.get("open", True)) or force_open
        it = QListWidgetItem(icon("folder"), f"{info['name']} ({len(members)})")
        it.setData(ROLE_KIND, "folder")
        it.setData(ROLE_ID, fo)
        it.setData(ROLE_OPEN, open_)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDropEnabled)
        errs = [doc.errors[g["id"]] for g in members if g["id"] in doc.errors]
        if errs:
            it.setForeground(QColor("#d93025"))
            it.setIcon(icon("error"))
            it.setToolTip(f"{len(errs)} feature(s) in this folder failed:\n" + "\n".join(errs[:5]))
        elif members and all(g.get("suppressed") for g in members):
            it.setForeground(QColor("#9aa3ae"))
            font = QFont()
            font.setStrikeOut(True)
            it.setFont(font)
            it.setToolTip("All suppressed")
        self.addItem(it)

    def folder_layout(self):
        """Which folder each feature belongs to, read from the rows as they now stand (after a drag). A feature
        is in a folder when it sits right under the folder row, or between two of the folder's features;
        a feature dragged anywhere else leaves its folder."""
        rows = []
        for i in range(self.count()):
            it = self.item(i)
            k = it.data(ROLE_KIND)
            if k in ("feature", "folder"):
                rows.append((k, it.data(ROLE_ID)))
        old = self._folder_of
        out = {}
        for j, (k, ident) in enumerate(rows):
            if k != "feature":
                continue
            prev = rows[j - 1] if j > 0 else None
            nxt = next((r for r in rows[j + 1:] if r[0] == "feature"), None)
            prev_fo = None
            if prev is not None:
                prev_fo = prev[1] if prev[0] == "folder" else out.get(prev[1])
            mine = old.get(ident)
            if prev is not None and prev[0] == "folder":
                out[ident] = prev[1]
            elif prev_fo and nxt is not None and old.get(nxt[1]) == prev_fo:
                out[ident] = prev_fo                      # between two of the folder's features
            elif mine and mine == prev_fo:
                out[ident] = mine                         # still right after its own folder's features
            else:
                out[ident] = None
        return out

    def _entered(self, it):
        fid = it.data(ROLE_ID) if it is not None and it.data(ROLE_KIND) == "feature" else None
        if fid != self._hover_fid:
            self._hover_fid = fid
            self.featureHovered.emit(fid)

    def _chevron_hit(self, pos):
        it = self.itemAt(pos)
        if it is None or it.data(ROLE_KIND) != "folder":
            return None
        r = self.visualItemRect(it)
        return it if pos.x() < r.left() + CHEVRON_W + 2 else None

    def mousePressEvent(self, e):
        pos = e.position().toPoint()
        it = self._eye_hit(pos) if e.button() == Qt.LeftButton else None
        if it is not None:
            self.eyeClicked.emit(f"{it.data(ROLE_KIND)}:{it.data(ROLE_ID)}")
            e.accept()
            return
        it = self._chevron_hit(pos) if e.button() == Qt.LeftButton else None
        if it is not None:
            self.folderToggled.emit(it.data(ROLE_ID))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        pos = e.position().toPoint()
        if self._eye_hit(pos) is not None or self._chevron_hit(pos) is not None:
            e.accept()
            return
        it = self.itemAt(pos)
        if it is not None and it.data(ROLE_KIND) == "folder":
            self.folderToggled.emit(it.data(ROLE_ID))
            e.accept()
            return
        super().mouseDoubleClickEvent(e)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        self._eye_track(e.position().toPoint())
        if self.itemAt(e.position().toPoint()) is None and self._hover_fid is not None:
            self._hover_fid = None
            self.featureHovered.emit(None)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._eye_leave()
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
        self.orderChanged.emit(ids, rb, self.folder_layout())

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
        if k in ("feature", "std_plane", "folder"):
            self.contextRequested.emit(f"{k}:{it.data(ROLE_ID)}", self.viewport().mapToGlobal(pos))

    def selected_feature_ids(self):
        """Selected features, in list order; a selected folder row counts as all of its features."""
        folders = {it.data(ROLE_ID) for it in self.selectedItems() if it.data(ROLE_KIND) == "folder"}
        out = []
        for i in range(self.count()):
            it = self.item(i)
            if it.data(ROLE_KIND) != "feature":
                continue
            fid = it.data(ROLE_ID)
            if it.isSelected() or self._folder_of.get(fid) in folders:
                out.append(fid)
        return out

    def selected_folder_ids(self):
        return [it.data(ROLE_ID) for it in self.selectedItems() if it.data(ROLE_KIND) == "folder"]


class PartsList(_EyeMixin, QListWidget):
    eyeClicked = Signal(str)                 # part id
    partClicked = Signal(str, bool)
    contextRequested = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(14, 14))
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.itemClicked.connect(lambda it: self.partClicked.emit(it.data(ROLE_ID), False))
        self._eye_setup()

    def mousePressEvent(self, e):
        it = self._eye_hit(e.position().toPoint()) if e.button() == Qt.LeftButton else None
        if it is not None:
            self.eyeClicked.emit(it.data(ROLE_ID))
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        self._eye_track(e.position().toPoint())

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._eye_leave()

    def populate(self, doc):
        self.blockSignals(True)
        self.clear()
        for p in doc.state.parts.values():
            it = QListWidgetItem(swatch(p.color), p.name)
            it.setData(ROLE_ID, p.id)
            it.setData(ROLE_EYE, "hidden" if p.id in doc.hidden_parts else "shown")
            if p.id in doc.hidden_parts:
                it.setForeground(QColor("#9aa3ae"))
                it.setToolTip("Hidden")
            self.addItem(it)
        self.blockSignals(False)

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it is not None:
            self.contextRequested.emit(it.data(ROLE_ID), self.viewport().mapToGlobal(pos))

    def selected_part_ids(self):
        return [it.data(ROLE_ID) for it in self.selectedItems()]


class LeftPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("leftpanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.feat_header = QLabel("Features")
        self.feat_header.setObjectName("sectionHeader")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(0)
        row.addWidget(self.feat_header, 1)
        self.new_folder_btn = QToolButton()
        self.new_folder_btn.setIcon(icon("new_folder", 18))
        self.new_folder_btn.setAutoRaise(True)
        self.new_folder_btn.setToolTip("New folder from the selected features")
        row.addWidget(self.new_folder_btn)
        lay.addLayout(row)
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
