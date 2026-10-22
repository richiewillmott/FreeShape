"""Drawing tab interface: the sheet canvas, the list of views and annotations, and the tools —
inserting base / projected / isometric views, dimensions, notes, sheet setup, and DXF / PDF export."""
from __future__ import annotations

import math
import os

import numpy as np
from PySide6.QtCore import QMarginsF, QPointF, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter,
                           QPainterPath, QPdfWriter, QPen, QPolygonF, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                               QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import drawing as DW
from .. import kernel as K
from ..drawing import DIRECTIONS, LAYER_STYLE, SCALES, SHEETS, TITLE_FIELDS, scale_label
from .icons import icon

ROLE_ID = Qt.UserRole + 1
ACCENT = QColor(31, 111, 209)
HOVER = QColor(240, 138, 36)
SNAP_PX = 9

LAYER_COLOR = {
    "TRIM": QColor(190, 194, 200), "BORDER": QColor(20, 22, 26), "BORDER_THIN": QColor(20, 22, 26),
    "BORDER_TEXT": QColor(40, 44, 50), "TITLE": QColor(20, 22, 26), "TITLE_THIN": QColor(40, 44, 50),
    "TITLE_LABEL": QColor(120, 126, 134), "TITLE_TEXT": QColor(20, 22, 26), "VISIBLE": QColor(14, 16, 20),
    "HIDDEN": QColor(70, 76, 86), "TANGENT": QColor(140, 146, 154), "CENTER": QColor(70, 76, 86),
    "DIM": QColor(14, 16, 20), "DIM_LOST": QColor(217, 48, 37), "NOTE": QColor(14, 16, 20),
    "BEND": QColor(200, 40, 40),
}


# =========================================================================== painting the sheet
class _Group:
    """One owner's items (a view, a dimension, the border …) turned into painter paths, per layer."""

    def __init__(self):
        self.paths = {}     # layer -> QPainterPath (strokes)
        self.fills = {}     # layer -> QPainterPath (filled triangles)
        self.texts = []     # (layer, pos, text, h, angle, ha, va)
        self.box = None     # paper bounds [x0, y0, x1, y1]

    def grow(self, pts):
        a = np.asarray(pts, float).reshape(-1, 2)
        lo, hi = a.min(axis=0), a.max(axis=0)
        if self.box is None:
            self.box = [lo[0], lo[1], hi[0], hi[1]]
        else:
            self.box = [min(self.box[0], lo[0]), min(self.box[1], lo[1]), max(self.box[2], hi[0]),
                        max(self.box[3], hi[1])]


def build_groups(items):
    groups = {}
    for it in items:
        kind, layer, owner = it[0], it[1], it[2]
        g = groups.setdefault(owner, _Group())
        if kind == "text":
            _k, _l, _o, pos, txt, h, ang, ha, va = it
            g.texts.append((layer, pos, txt, h, ang, ha, va))
            w = max(len(txt), 1) * h * 0.62
            g.grow([(pos[0] - w, pos[1] - h * 1.4), (pos[0] + w, pos[1] + h * 1.4)])
            continue
        if kind == "tri":
            path = g.fills.setdefault(layer, QPainterPath())
            path.addPolygon(QPolygonF([QPointF(*it[3]), QPointF(*it[4]), QPointF(*it[5])]))
            path.closeSubpath()
            g.grow([it[3], it[4], it[5]])
            continue
        path = g.paths.setdefault(layer, QPainterPath())
        if kind == "line":
            path.moveTo(QPointF(*it[3]))
            path.lineTo(QPointF(*it[4]))
            g.grow([it[3], it[4]])
        elif kind in ("circle", "arc"):
            c, r = it[3], it[4]
            a0, a1 = (0.0, 360.0) if kind == "circle" else (it[5], it[6])
            if a1 < a0:
                a1 += 360.0
            n = max(12, int((a1 - a0) / 4))
            pts = [(c[0] + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
                    c[1] + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]
            path.moveTo(QPointF(*pts[0]))
            for q in pts[1:]:
                path.lineTo(QPointF(*q))
            g.grow([(c[0] - r, c[1] - r), (c[0] + r, c[1] + r)])
        elif kind == "poly":
            pts = it[3]
            path.moveTo(QPointF(*pts[0]))
            for q in pts[1:]:
                path.lineTo(QPointF(*q))
            g.grow(pts)
    return groups


_FONT = None


def _font():
    global _FONT
    if _FONT is None:
        f = QFont("Arial")
        f.setPixelSize(100)
        _FONT = (f, QFontMetricsF(f))
    return _FONT


def draw_text(p, pos, txt, h, ang, ha, va, color):
    """Text in paper space (y up), `h` = capital height in mm, like DXF text."""
    f, fm = _font()
    cap = fm.capHeight() or 70.0
    k = h / cap
    w = fm.horizontalAdvance(txt)
    dx = {"left": 0.0, "center": -w / 2, "right": -w}.get(ha, 0.0)
    dy = {"baseline": 0.0, "bottom": -fm.descent(), "middle": cap / 2, "top": cap}.get(va, 0.0)
    p.save()
    p.translate(pos[0], pos[1])
    p.rotate(ang)
    p.scale(k, -k)
    p.setFont(f)
    p.setPen(color)
    p.drawText(QPointF(dx, dy), txt)
    p.restore()


def paint_groups(p, groups, px_per_mm, colors=None, only=None, override=None, print_mode=False):
    """Draw cached groups with the painter already mapping paper mm (y up) to the device.
    override: QColor to draw everything in (highlights)."""
    for owner, g in groups.items():
        if only is not None and owner not in only:
            continue
        for layer, path in g.paths.items():
            if layer == "TRIM" and print_mode:
                continue
            wd, dash, _c, _lt = LAYER_STYLE.get(layer, (0.25, None, 7, ""))
            col = override or (colors or LAYER_COLOR).get(layer, QColor(0, 0, 0))
            min_w = 0.0 if print_mode else 1.0 / px_per_mm
            w = max(wd, min_w) if override is None else max(wd, min_w) + 1.6 / px_per_mm
            pen = QPen(col, w)
            pen.setCapStyle(Qt.FlatCap if dash else Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            if dash:
                pen.setDashPattern([max(x / w, 0.5) for x in dash])
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
        for layer, path in g.fills.items():
            col = override or (colors or LAYER_COLOR).get(layer, QColor(0, 0, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPath(path)
        for (layer, pos, txt, h, ang, ha, va) in g.texts:
            col = override or (colors or LAYER_COLOR).get(layer, QColor(0, 0, 0))
            draw_text(p, pos, txt, h, ang, ha, va, col)


def render_sheet_image(doc, w=480, h=300, groups=None):
    """A picture of the sheet (for the home page)."""
    groups = groups or build_groups(doc.items())
    W, H = doc.sheet_size()
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor(236, 238, 241))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    k = min((w - 20) / W, (h - 20) / H)
    ox, oy = (w - W * k) / 2, (h + H * k) / 2
    p.setTransform(QTransform(k, 0, 0, -k, ox, oy))
    p.fillRect(QRectF(0, 0, W, H), QColor("white"))
    paint_groups(p, groups, k)
    p.end()
    return img


def export_pdf(doc, path, groups=None):
    groups = groups or build_groups(doc.items())
    W, H = doc.sheet_size()
    pdf = QPdfWriter(path)
    pdf.setResolution(1200)
    pdf.setPageLayout(QPageLayout(QPageSize(QSizeF(W, H), QPageSize.Millimeter, doc.sheet.get("size", "")),
                                  QPageLayout.Portrait, QMarginsF(0, 0, 0, 0)))
    pdf.setTitle(doc.title_value("title"))
    pdf.setCreator("FreeShape")
    p = QPainter(pdf)
    p.setRenderHint(QPainter.Antialiasing, True)
    dpmm = pdf.resolution() / 25.4
    p.setTransform(QTransform(dpmm, 0, 0, -dpmm, 0, H * dpmm))
    paint_groups(p, groups, dpmm, print_mode=True)
    p.end()


# =========================================================================== the canvas
class DrawingCanvas(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.z = 2.0                 # pixels per mm
        self.off = QPointF(40, 600)  # screen position of the paper origin
        self._groups = None
        self._groups_for = None
        self._press = None
        self._fitted_for = None
        self._zoom_timer = None
        self._pic = None

    # ------------------------------------------------------------------ coordinates
    def T(self):
        return QTransform(self.z, 0, 0, -self.z, self.off.x(), self.off.y())

    def to_screen(self, xy):
        return QPointF(self.off.x() + xy[0] * self.z, self.off.y() - xy[1] * self.z)

    def to_paper(self, x, y):
        return np.array([(x - self.off.x()) / self.z, (self.off.y() - y) / self.z])

    def fit(self):
        doc = self.ctl.doc
        if doc is None:
            return
        W, H = doc.sheet_size()
        k = min((self.width() - 40) / W, (self.height() - 40) / H)
        self.z = max(k, 0.05)
        self.off = QPointF((self.width() - W * self.z) / 2, (self.height() + H * self.z) / 2)
        self.update()

    def groups(self):
        """Painter paths per owner — rebuilt only for owners whose items changed."""
        doc = self.ctl.doc
        key = (id(doc), doc.version)
        if self._groups_for != key:
            old = getattr(self, "_gcache", {}) if getattr(self, "_gcache_doc", None) is doc else {}
            new, groups = {}, {}
            for owner, lst in doc.items_by_owner().items():
                hit = old.get(owner)
                if hit is not None and hit[0] is lst:
                    grp = hit[1]
                else:
                    grp = build_groups(lst).get(owner, _Group())
                new[owner] = (lst, grp)
                groups[owner] = grp
            self._gcache, self._gcache_doc = new, doc
            self._groups = groups
            self._groups_for = key
        return self._groups

    # ------------------------------------------------------------------ painting
    def _sheet_picture(self, doc, moving):
        """The sheet (everything but what's being dragged) drawn once into a picture, redrawn only when
        something changes or the zoom settles. Panning just slides it."""
        from PySide6.QtGui import QPixmap
        dpr = self.devicePixelRatioF()
        W, H = doc.sheet_size()
        key = (id(doc), doc.version, round(self.z, 9), tuple(sorted(map(str, moving))), self.width(), self.height(),
               dpr)
        pic = getattr(self, "_pic", None)
        if pic is not None and pic["key"] == key:
            # same picture; usable as long as the visible area is inside what it covers
            dx, dy = self.off.x() - pic["off"].x(), self.off.y() - pic["off"].y()
            if abs(dx) <= pic["mx"] and abs(dy) <= pic["my"]:
                return pic
        mx, my = self.width() * 0.5, self.height() * 0.5             # margin so panning doesn't redraw at once
        pw, ph = int((self.width() + 2 * mx) * dpr), int((self.height() + 2 * my) * dpr)
        pm = QPixmap(max(pw, 1), max(ph, 1))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setTransform(QTransform(self.z, 0, 0, -self.z, self.off.x() + mx, self.off.y() + my))
        p.fillRect(QRectF(0, 0, W, H), QColor("white"))
        groups = self.groups()
        if moving:
            paint_groups(p, {k: g for k, g in groups.items() if k not in moving}, self.z)
        else:
            paint_groups(p, groups, self.z)
        p.end()
        self._pic = {"key": key, "pm": pm, "off": QPointF(self.off), "mx": mx, "my": my, "z": self.z}
        return self._pic

    def paintEvent(self, _e):
        doc = self.ctl.doc
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(226, 229, 233))
        if doc is None or getattr(doc, "kind", "") != "drawing":
            p.end()
            return
        if self._fitted_for != id(doc):
            self._fitted_for = id(doc)
            self.fit()
        p.setRenderHint(QPainter.Antialiasing, True)
        W, H = doc.sheet_size()
        r = QRectF(self.to_screen((0, H)), self.to_screen((W, 0)))
        for i, a in enumerate((26, 16, 8)):
            p.fillRect(r.adjusted(-1 + i, 2 + i * 1.5, 3 + i * 1.5, 4 + i * 2), QColor(0, 0, 0, a))
        ctl = self.ctl
        moving, delta = ctl.moving()
        pic = getattr(self, "_pic", None)
        zooming = self._zoom_timer is not None and self._zoom_timer.isActive() and pic is not None \
            and pic["key"][0] == id(doc) and pic["key"][1] == doc.version
        if zooming and abs(pic["z"] - self.z) > 1e-12:
            # mid-zoom: stretch the last picture; a sharp one is drawn when the wheel stops
            k = self.z / pic["z"]
            p.save()
            p.translate(self.off.x(), self.off.y())
            p.scale(k, k)
            p.translate(-pic["off"].x() - pic["mx"], -pic["off"].y() - pic["my"])
            p.drawPixmap(QPointF(0, 0), pic["pm"])
            p.restore()
        else:
            pic = self._sheet_picture(doc, moving)
            p.drawPixmap(QPointF(self.off.x() - pic["off"].x() - pic["mx"], self.off.y() - pic["off"].y() - pic["my"]),
                         pic["pm"])
        p.setTransform(self.T())
        groups = self.groups()
        if moving:
            p.save()
            p.translate(delta[0], delta[1])
            paint_groups(p, {k: g for k, g in groups.items() if k in moving}, self.z)
            p.restore()
        # highlights: hovered / selected views and annotations
        hl = {}
        for oid in ctl.selection:
            hl[oid] = ACCENT
        if ctl.hover_id and ctl.hover_id not in hl:
            hl[ctl.hover_id] = HOVER
        for oid, col in hl.items():
            off_ = delta if oid in moving else (0.0, 0.0)
            if doc.annot(oid) is not None:
                p.save()
                p.translate(*off_)
                paint_groups(p, groups, self.z, only={oid}, override=QColor(col))
                p.restore()
            v = doc.view(oid)
            if v is not None:
                x0, y0, x1, y1 = doc.view_box(v)
                p.setPen(QPen(col, 1.2 / self.z, Qt.DashLine))
                p.setBrush(QColor(col.red(), col.green(), col.blue(), 14))
                p.drawRect(QRectF(x0 - 3 + off_[0], y0 - 3 + off_[1], x1 - x0 + 6, y1 - y0 + 6))
        ctl.draw_tool_overlay(p, self)
        p.resetTransform()
        if ctl.hint:
            f = QFont()
            f.setPixelSize(12)
            p.setFont(f)
            warn = ctl.hint.startswith("⚠")
            p.setPen(QColor(217, 48, 37) if warn else QColor(90, 96, 106))
            p.drawText(QRectF(12, self.height() - 26, self.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       ctl.hint)
        top = "⚠ " + doc.error if doc.error else ("Working out the exact views…" if ctl.busy() else "")
        if top:
            f = QFont()
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor(217, 48, 37) if doc.error else QColor(90, 96, 106))
            p.drawText(QRectF(12, 8, self.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter, top)
        p.end()

    # ------------------------------------------------------------------ input
    def wheelEvent(self, e):
        f = 1.0015 ** e.angleDelta().y()
        pos = e.position()
        before = self.to_paper(pos.x(), pos.y())
        self.z = float(np.clip(self.z * f, 0.05, 200))
        self.off = QPointF(pos.x() - before[0] * self.z, pos.y() + before[1] * self.z)
        if self._zoom_timer is None:
            from PySide6.QtCore import QTimer
            self._zoom_timer = QTimer(self)
            self._zoom_timer.setSingleShot(True)
            self._zoom_timer.timeout.connect(self.update)
        self._zoom_timer.start(140)
        self.update()

    def mousePressEvent(self, e):
        pos = e.position()
        self._press = (pos.x(), pos.y(), e.button())
        self._last = (pos.x(), pos.y())
        self._moved = False
        if e.button() == Qt.LeftButton:
            self.ctl.press(pos.x(), pos.y(), e)
        self.setFocus()

    def mouseMoveEvent(self, e):
        pos = e.position()
        x, y = pos.x(), pos.y()
        if self._press is not None:
            if abs(x - self._press[0]) + abs(y - self._press[1]) > 3:
                self._moved = True
            if self._press[2] in (Qt.MiddleButton, Qt.RightButton) and self._moved:
                self.off += QPointF(x - self._last[0], y - self._last[1])
                self._last = (x, y)
                self.setCursor(Qt.ClosedHandCursor)
                self.update()
                return
            if self._press[2] == Qt.LeftButton:
                self.ctl.drag(x, y, e, self._moved)
            self._last = (x, y)
            return
        self.ctl.hover(x, y)

    def mouseReleaseEvent(self, e):
        pos = e.position()
        press, self._press = self._press, None
        self.setCursor(Qt.ArrowCursor)
        if press is None:
            return
        if press[2] == Qt.RightButton and not self._moved:
            self.ctl.context(pos.x(), pos.y(), e.globalPosition().toPoint())
            return
        if press[2] == Qt.LeftButton:
            self.ctl.release(pos.x(), pos.y(), e, self._moved)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.ctl.double_click(e.position().x(), e.position().y())

    def keyPressEvent(self, e):
        if not self.ctl.key(e):
            super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.ctl.doc is not None and getattr(self.ctl.doc, "kind", "") == "drawing" and \
                self._fitted_for == id(self.ctl.doc) and e.oldSize().width() > 0:
            # keep the sheet centred
            dx = (e.size().width() - e.oldSize().width()) / 2
            dy = (e.size().height() - e.oldSize().height()) / 2
            self.off += QPointF(dx, dy)


# =========================================================================== left panel
class DrawingPanel(QWidget):
    itemClicked = Signal(str)
    itemDouble = Signal(str)
    itemContext = Signal(str, object)

    def __init__(self):
        super().__init__()
        self.setObjectName("leftpanel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        h = QLabel("Drawing")
        h.setObjectName("sectionHeader")
        lay.addWidget(h)
        self.source = QLabel("")
        self.source.setWordWrap(True)
        self.source.setObjectName("muted")
        self.source.setContentsMargins(10, 4, 10, 6)
        lay.addWidget(self.source)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._ctx)
        self.tree.itemClicked.connect(lambda it: self.itemClicked.emit(it.data(0, ROLE_ID) or ""))
        self.tree.itemDoubleClicked.connect(lambda it: self.itemDouble.emit(it.data(0, ROLE_ID) or ""))
        lay.addWidget(self.tree, 1)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setContentsMargins(10, 6, 10, 8)
        lay.addWidget(self.status)
        self.setMinimumWidth(220)

    def _ctx(self, pos):
        it = self.tree.itemAt(pos)
        if it is not None and it.data(0, ROLE_ID):
            self.itemContext.emit(it.data(0, ROLE_ID), self.tree.viewport().mapToGlobal(pos))

    def populate(self, doc, selection=()):
        self.tree.blockSignals(True)
        self.tree.clear()
        self.source.setText("Shows: " + doc.source_label())
        sheet = QTreeWidgetItem([f"Sheet  {doc.sheet.get('size')} · {scale_label(doc.sheet.get('scale', 1))}"
                                 f" · {'first' if doc.sheet.get('projection') == 'first' else 'third'} angle"])
        sheet.setIcon(0, icon("sheet", 16))
        sheet.setData(0, ROLE_ID, "__sheet")
        self.tree.addTopLevelItem(sheet)
        vh = QTreeWidgetItem([f"Views ({len(doc.views)})"])
        vh.setFlags(Qt.ItemIsEnabled)
        self.tree.addTopLevelItem(vh)
        names = {k: n for n, (d, _u) in DIRECTIONS.items() for k in [tuple(np.round(DW._unit(d), 6))]}
        for v in doc.views:
            nm = names.get(tuple(np.round(DW._unit(v["d"]), 6)))
            kind = {"base": "Base view", "projected": "Projected view", "iso": "Isometric view"}.get(v["type"], "View")
            label = f"{kind}" + (f" — {nm}" if nm and nm != "Isometric" else "")
            if v.get("scale"):
                label += f"  ({scale_label(v['scale'])})"
            it = QTreeWidgetItem([label])
            it.setIcon(0, icon("iso_view" if v["type"] == "iso" else "proj_view" if v["type"] == "projected"
                               else "base_view", 16))
            it.setData(0, ROLE_ID, v["id"])
            vh.addChild(it)
            it.setSelected(v["id"] in selection)
        ah = QTreeWidgetItem([f"Dimensions & notes ({len(doc.annots)})"])
        ah.setFlags(Qt.ItemIsEnabled)
        self.tree.addTopLevelItem(ah)
        for a in doc.annots:
            if a["type"] == "note":
                label = "Note: " + str(a.get("text", "")).split("\n")[0][:30]
                ic = "note"
            else:
                g = doc.dim_geometry(a)
                label = doc.dim_text(a, g[2]) if g else "?"
                if any(isinstance(a.get(k), dict) and a[k].get("lost") for k in ("a", "b", "b2")):
                    label += "  (lost its edge)"
                ic = "dim"
            it = QTreeWidgetItem([label])
            it.setIcon(0, icon(ic, 16))
            it.setData(0, ROLE_ID, a["id"])
            ah.addChild(it)
            it.setSelected(a["id"] in selection)
        self.tree.expandAll()
        self.tree.blockSignals(False)
        st = []
        if doc.error:
            st.append("⚠ " + doc.error)
        if not doc.exact_ready():
            st.append("Working out the exact views in the background — round edges look a little faceted "
                      "until then.")
        self.status.setText("\n".join(st) or "Drag views to move them · right-click for options · "
                                             "D dimension · N note · F fit")


# =========================================================================== dialogs
def _combo(items, current=None):
    c = QComboBox()
    for label, data in items:
        c.addItem(label, data)
    if current is not None:
        i = c.findData(current)
        if i >= 0:
            c.setCurrentIndex(i)
    return c


def _scale_items(include_sheet=False):
    out = [("Same as the sheet", None)] if include_sheet else []
    return out + [(scale_label(s), s) for s in SCALES]


class NewDrawingDialog(QDialog):
    """Pick what to draw, the sheet size and the projection standard."""

    def __init__(self, parent, sources, preselect=None, settings=None):
        super().__init__(parent)
        self.setWindowTitle("New drawing")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("<b>What should the drawing show?</b>"))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(220)
        lay.addWidget(self.tree)
        self._sources = []
        first = None
        for group_label, ic, entries in sources:
            top = QTreeWidgetItem([group_label])
            top.setIcon(0, icon(ic, 16))
            self.tree.addTopLevelItem(top)
            for label, src in entries:
                it = QTreeWidgetItem([label])
                it.setIcon(0, icon("part" if src.get("kind") == "studio" else "assembly", 16))
                it.setData(0, ROLE_ID, len(self._sources))
                self._sources.append(src)
                top.addChild(it)
                if first is None or (preselect is not None and _same_source(src, preselect)):
                    first = it
        self.tree.expandAll()
        if first is not None:
            self.tree.setCurrentItem(first)
        f = QFormLayout()
        s = settings
        self.size = _combo([(f"{k}  ({int(w)} × {int(h)} mm)", k) for k, (w, h) in SHEETS.items()],
                           (s.value("dwg_size", "A3") if s else "A3"))
        self.orient = _combo([("Landscape", False), ("Portrait", True)])
        self.proj = _combo([("First angle (ISO / BS 8888 — UK & Europe)", "first"),
                            ("Third angle (ANSI — USA)", "third")], (s.value("dwg_proj", "first") if s else "first"))
        self.views = QCheckBox("Add the standard views (front, plan, side and isometric) at a scale that fits")
        self.views.setChecked(True)
        self.drawn = QLineEdit(s.value("dwg_drawn", "") if s else "")
        self.drawn.setPlaceholderText("Your name or initials")
        self.company = QLineEdit(s.value("dwg_company", "") if s else "")
        f.addRow("Sheet", self.size)
        f.addRow("", self.orient)
        f.addRow("Projection", self.proj)
        f.addRow("Drawn by", self.drawn)
        f.addRow("Company", self.company)
        lay.addLayout(f)
        lay.addWidget(self.views)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Create drawing")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.tree.itemDoubleClicked.connect(lambda it: self.accept() if it.data(0, ROLE_ID) is not None else None)

    def chosen(self):
        it = self.tree.currentItem()
        if it is None or it.data(0, ROLE_ID) is None:
            return None
        return self._sources[int(it.data(0, ROLE_ID))]


def _same_source(a, b):
    if a.get("kind") != b.get("kind"):
        return False
    if a.get("kind") == "studio":
        return a.get("uid") == b.get("uid") and a.get("part") == b.get("part") and \
            bool(a.get("flat")) == bool(b.get("flat"))
    return a.get("uid") == b.get("uid")


class SheetDialog(QDialog):
    def __init__(self, parent, doc):
        super().__init__(parent)
        self.setWindowTitle("Sheet and title block")
        lay = QVBoxLayout(self)
        g = QGridLayout()
        self.size = _combo([(k, k) for k in SHEETS], doc.sheet.get("size"))
        self.orient = _combo([("Landscape", False), ("Portrait", True)], bool(doc.sheet.get("portrait")))
        self.scale = _combo(_scale_items(), float(doc.sheet.get("scale", 1.0)))
        self.proj = _combo([("First angle (ISO)", "first"), ("Third angle (ANSI)", "third")],
                           doc.sheet.get("projection", "first"))
        g.addWidget(QLabel("Size"), 0, 0)
        g.addWidget(self.size, 0, 1)
        g.addWidget(self.orient, 0, 2)
        g.addWidget(QLabel("Scale"), 1, 0)
        g.addWidget(self.scale, 1, 1)
        g.addWidget(QLabel("Projection"), 2, 0)
        g.addWidget(self.proj, 2, 1, 1, 2)
        lay.addLayout(g)
        lay.addWidget(QLabel("<b>Title block</b>"))
        f = QFormLayout()
        self.fields = {}
        for key, label in TITLE_FIELDS:
            e = QLineEdit(str(doc.title.get(key, "")))
            if key == "title":
                e.setPlaceholderText(doc.name)
            self.fields[key] = e
            f.addRow(label, e)
        lay.addLayout(f)
        note = QLabel("Changing the projection re-points the projected views. Changing the scale resizes every view "
                      "that uses the sheet scale.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class ViewDialog(QDialog):
    def __init__(self, parent, doc, v, new=False):
        super().__init__(parent)
        self.setWindowTitle("Insert a view" if new else "View properties")
        lay = QVBoxLayout(self)
        f = QFormLayout()
        cur = None
        for n, (d, _u) in DIRECTIONS.items():
            if v is not None and np.allclose(DW._unit(d), DW._unit(v["d"]), atol=1e-6):
                cur = n
        self.dir = _combo([(n, n) for n in DIRECTIONS], cur or "Front")
        self.dir.setEnabled(new or (v is not None and v["type"] != "projected"))
        if v is not None and cur is None:
            self.dir.insertItem(0, "(as projected)", None)
            self.dir.setCurrentIndex(0)
        self.scale = _combo(_scale_items(True), v.get("scale") if v else None)
        self.hidden = QCheckBox("Show hidden edges (dashed)")
        self.hidden.setChecked(bool(v.get("hidden", True)) if v else True)
        self.tangent = QCheckBox("Show tangent edges (where rounded faces meet)")
        self.tangent.setChecked(bool(v.get("tangent", False)) if v else False)
        self.marks = QCheckBox("Centre marks on holes and round edges")
        self.marks.setChecked(bool(v.get("centre_marks", True)) if v else True)
        self.label = QLineEdit(v.get("label", "") if v else "")
        self.label.setPlaceholderText("e.g. VIEW A  (optional)")
        f.addRow("Looking from", self.dir)
        f.addRow("Scale", self.scale)
        f.addRow("Label", self.label)
        lay.addLayout(f)
        for w in (self.hidden, self.tangent, self.marks):
            lay.addWidget(w)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        if new:
            bb.button(QDialogButtonBox.Ok).setText("Place on the sheet")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class DimDialog(QDialog):
    def __init__(self, parent, doc, a):
        super().__init__(parent)
        self.setWindowTitle("Dimension")
        g = doc.dim_geometry(a)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"Measured: <b>{DW.fmt_value(g[2], 4) if g else '?'}</b> mm"))
        f = QFormLayout()
        self.dp = _combo([("Automatic", None)] + [(str(i), i) for i in range(0, 5)], a.get("dp"))
        self.prefix = QLineEdit(a.get("prefix", ""))
        self.prefix.setPlaceholderText("e.g. 4×  ")
        self.suffix = QLineEdit(a.get("suffix", ""))
        self.suffix.setPlaceholderText("e.g.  ±0.1  or  THRU")
        self.text = QLineEdit(a.get("text", ""))
        self.text.setPlaceholderText("Leave empty to show the measured value")
        f.addRow("Decimal places", self.dp)
        f.addRow("Before the value", self.prefix)
        f.addRow("After the value", self.suffix)
        f.addRow("Replace the text", self.text)
        if a["type"] == "lin":
            self.orient = _combo([("Aligned with the points", "a"), ("Horizontal", "h"), ("Vertical", "v")],
                                 a.get("orient", "a"))
            f.addRow("Direction", self.orient)
        lay.addLayout(f)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# =========================================================================== controller
class DrawingController:
    """Everything the main window does while a Drawing tab is current."""

    def __init__(self, w):
        self.w = w
        self.panel = DrawingPanel()
        self.canvas = DrawingCanvas(self)
        self.selection = []
        self.hover_id = None
        self.tool = "select"
        self.hint = ""
        self._drag = None
        self._snap = None          # current snap target under the mouse (dimension tool)
        self._picks = []           # dimension picks so far
        self._place = None         # view waiting to be placed: {"kind": "base"/"projected", ...}
        self._cursor = None        # paper position of the mouse
        self._jobs = {}            # exact views being worked out: key -> (QProcess, shape, d, up, out path)
        self._queue = []
        self._breps = {}           # id(shape) -> (shape, temp .brep file) for the workers
        self._tmp = None
        p = self.panel
        p.itemClicked.connect(self._panel_clicked)
        p.itemDouble.connect(self._panel_double)
        p.itemContext.connect(lambda oid, gpos: self._menu_for(oid, gpos))

    @property
    def doc(self):
        d = self.w.doc
        return d if getattr(d, "kind", "") == "drawing" else None

    # ------------------------------------------------------------------ exact views in the background
    def request_exact(self, doc, v):
        shape = doc.shape
        if shape is None:
            return
        key = K._hlr_key(shape, v["d"], v["up"])
        if key in self._jobs or any(q[0] == key for q in self._queue):
            return
        self._queue.append((key, shape, [float(x) for x in v["d"]], [float(x) for x in v["up"]]))
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._pump_jobs)

    def busy(self):
        return len(self._jobs) + len(self._queue)

    def _brep_file(self, shape):
        import tempfile
        hit = self._breps.get(id(shape))
        if hit is not None and hit[0] is shape:
            return hit[1]
        if self._tmp is None:
            self._tmp = tempfile.mkdtemp(prefix="freeshape_views_")
        path = os.path.join(self._tmp, f"m{len(self._breps)}_{id(shape)}.brep")
        with open(path, "wb") as fh:
            fh.write(K.shape_to_brep(shape))
        self._breps[id(shape)] = (shape, path)
        return path

    def _pump_jobs(self):
        import sys
        from PySide6.QtCore import QProcess
        from .progress import ROOT
        limit = max(1, min(4, (os.cpu_count() or 2) - 1))
        while self._queue and len(self._jobs) < limit:
            key, shape, d, up = self._queue.pop(0)
            if K.hlr_cached(shape, d, up) is not None:
                continue
            try:
                brep = self._brep_file(shape)
            except Exception:
                K.hlr_store(shape, d, up, K.hlr_project(shape, d, up, fast=True))
                continue
            out = os.path.join(self._tmp, f"v{abs(hash(key))}.json")
            proc = QProcess(self.canvas)
            proc.setWorkingDirectory(ROOT)
            if sys.platform == "win32":
                try:
                    def no_window(args):
                        args.flags |= 0x08000000           # CREATE_NO_WINDOW
                    proc.setCreateProcessArgumentsModifier(no_window)
                except Exception:
                    pass
            args = [brep, ",".join(map(repr, d)), ",".join(map(repr, up)), out]
            proc.finished.connect(lambda *_a, k=key: self._job_done(k))
            proc.errorOccurred.connect(lambda *_a, k=key: self._job_done(k))
            self._jobs[key] = (proc, shape, d, up, out)
            if getattr(sys, "frozen", False):
                proc.start(sys.executable, ["--hlr-worker"] + args)
            else:
                proc.start(sys.executable, ["-m", "freeshape.hlr_worker"] + args)
        self._show_busy()

    def _job_done(self, key):
        job = self._jobs.pop(key, None)
        if job is None:
            return
        _proc, shape, d, up, out = job
        try:
            import json
            with open(out) as fh:
                res = json.load(fh)
            os.remove(out)
        except Exception:
            # the helper couldn't do it: work it out here instead (slower, but it gets done)
            try:
                res = K.hlr_project(shape, d, up)
            except Exception:
                res = K.hlr_project(shape, d, up, fast=True)
        K.hlr_store(shape, d, up, res)
        for doc in list(self.w.docs.values()):
            if getattr(doc, "kind", "") == "drawing" and doc.shape is shape:
                doc.changed(notify=doc is self.doc)
        self._pump_jobs()

    def _show_busy(self):
        n = self.busy()
        if self.doc is not None:
            self.panel.populate(self.doc, self.selection)
        self.canvas.update()
        return n

    def wait_exact(self):
        """Before exporting: make sure every view is the exact one (waits for the background work)."""
        doc = self.doc
        if doc is None or doc.shape is None:
            return True
        for v in doc.views:
            doc.curves(v)
        if doc.exact_ready():
            return True
        from PySide6.QtWidgets import QApplication, QProgressDialog
        import time
        dlg = QProgressDialog("Working out the exact views…", "Cancel", 0, 0, self.w)
        dlg.setWindowTitle("FreeShape")
        dlg.setMinimumDuration(0)
        dlg.show()
        while not doc.exact_ready():
            QApplication.processEvents()
            if dlg.wasCanceled():
                dlg.close()
                return False
            time.sleep(0.03)
            if not self._jobs and not self._queue:
                for v in doc.views:                 # nothing running: finish here
                    if K.hlr_cached(doc.shape, v["d"], v["up"]) is None:
                        K.hlr_store(doc.shape, v["d"], v["up"], K.hlr_project(doc.shape, v["d"], v["up"]))
        dlg.close()
        doc.changed()
        return True

    # ------------------------------------------------------------------ refresh
    def on_changed(self):
        doc = self.doc
        if doc is None:
            return
        self.selection = [s for s in self.selection if doc.view(s) or doc.annot(s)]
        self.panel.populate(doc, self.selection)
        self.canvas.update()

    def activate(self):
        self.set_tool("select")
        self.canvas._fitted_for = None
        self.on_changed()

    def leave(self):
        self.set_tool("select")

    # ------------------------------------------------------------------ tools
    def set_tool(self, tool, **kw):
        self.tool = tool
        self._picks = []
        self._snap = None
        self._place = kw.get("place")
        self.hint = {
            "select": "",
            "dim": "Dimension: click an edge, a circle, or two points (corners / hole centres) — then click "
                   "where it should go. Esc to stop.",
            "note": "Note: click where the note should go.",
            "projected": "Projected view: click the view to project from.",
            "place": "Click on the sheet to place the view. Esc to cancel.",
        }.get(tool, "")
        if tool == "projected" and self.selection and self.doc and self.doc.view(self.selection[0]):
            self._place = {"kind": "projected", "parent": self.selection[0]}
            self.hint = "Move to the side of the view and click to place the projected view (off a corner gives " \
                        "an isometric view). Esc to cancel."
        self.canvas.setCursor(Qt.CrossCursor if tool != "select" else Qt.ArrowCursor)
        self.canvas.update()
        self.w.dwg_tool_changed(tool)

    def flash(self, msg):
        self.hint = "⚠ " + msg
        self.canvas.update()

    def insert_view(self):
        doc = self.doc
        if doc is None:
            return
        dlg = ViewDialog(self.w, doc, None, new=True)
        if dlg.exec() != QDialog.Accepted:
            return
        name = dlg.dir.currentData() or "Front"
        d, up = DIRECTIONS[name]
        self.set_tool("place", place={"kind": "base", "d": d, "up": up, "iso": name == "Isometric",
                                      "scale": dlg.scale.currentData(), "hidden": dlg.hidden.isChecked(),
                                      "tangent": dlg.tangent.isChecked(), "marks": dlg.marks.isChecked(),
                                      "label": dlg.label.text().strip()})

    def sheet_setup(self):
        doc = self.doc
        if doc is None:
            return
        dlg = SheetDialog(self.w, doc)
        if dlg.exec() != QDialog.Accepted:
            return
        doc.checkpoint()
        old_proj = doc.sheet.get("projection")
        old_scale = float(doc.sheet.get("scale", 1.0))
        doc.sheet["size"] = dlg.size.currentData()
        doc.sheet["portrait"] = bool(dlg.orient.currentData())
        doc.sheet["projection"] = dlg.proj.currentData()
        new_scale = float(dlg.scale.currentData())
        if abs(new_scale - old_scale) > 1e-12:
            # resize views about their centres
            for v in doc.views:
                if v.get("scale"):
                    continue
                x0, y0, x1, y1 = doc.view_box(v)
                c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
                o = np.asarray(v["o"], float)
                v["o"] = (c + (o - c) * new_scale / old_scale).tolist()
            doc.sheet["scale"] = new_scale
            for v in doc.views:
                doc._line_up(v)
        if old_proj != doc.sheet["projection"]:
            self._reproject()
        for key, e in dlg.fields.items():
            doc.title[key] = e.text().strip()
        s = self.w.settings
        if doc.title.get("drawn"):
            s.setValue("dwg_drawn", doc.title["drawn"])
        if doc.title.get("company"):
            s.setValue("dwg_company", doc.title["company"])
        doc.changed()

    def _reproject(self):
        """The projection standard changed: projected views now show the opposite side."""
        doc = self.doc
        for v in doc.views:
            p = doc.view(v.get("parent")) if v.get("parent") else None
            if p is None:
                continue
            pb, vb = doc.view_box(p), doc.view_box(v)
            side = np.array([(vb[0] + vb[2]) / 2 - (pb[0] + pb[2]) / 2, (vb[1] + vb[3]) / 2 - (pb[1] + pb[3]) / 2])
            nd, nup, al = doc.projected_dir(p, side)
            c = np.array([(vb[0] + vb[2]) / 2, (vb[1] + vb[3]) / 2])
            v["d"], v["up"], v["align"] = [float(x) for x in nd], [float(x) for x in nup], al
            lo, hi = doc.model_extent(v["d"], v["up"])
            v["o"] = (c - doc.view_scale(v) * (lo + hi) / 2).tolist()
            doc._line_up(v)

    def add_note(self, at=None):
        doc = self.doc
        if doc is None:
            return
        if at is None:
            self.set_tool("note")
            return
        txt, ok = QInputDialog.getMultiLineText(self.w, "Note", "Text:")
        if ok and txt.strip():
            doc.add_annot({"type": "note", "text": txt.strip(), "pos": [float(at[0]), float(at[1])], "height": DW.TEXT_H})

    # ------------------------------------------------------------------ hit testing
    def _hit(self, x, y):
        """What's under the mouse: an annotation id, else a view id, else None."""
        doc = self.doc
        P = self.canvas.to_paper(x, y)
        tol = 4.0 / self.canvas.z
        groups = self.canvas.groups()
        best = None
        for a in reversed(doc.annots):
            g = groups.get(a["id"])
            if g is None or g.box is None:
                continue
            x0, y0, x1, y1 = g.box
            if x0 - tol <= P[0] <= x1 + tol and y0 - tol <= P[1] <= y1 + tol:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[0]:
                    best = (area, a["id"])
        if best:
            return best[1]
        for v in reversed(doc.views):
            x0, y0, x1, y1 = doc.view_box(v)
            if x0 - 3 <= P[0] <= x1 + 3 and y0 - 3 <= P[1] <= y1 + 3:
                return v["id"]
        return None

    def _view_at(self, P, pad=8.0):
        doc = self.doc
        for v in reversed(doc.views):
            x0, y0, x1, y1 = doc.view_box(v)
            if x0 - pad <= P[0] <= x1 + pad and y0 - pad <= P[1] <= y1 + pad:
                return v
        return None

    @staticmethod
    def _drawn_near(sd, P, tol):
        """The nearest point on the view's drawn lines, if one is within tol of P (else None)."""
        A, B = sd["SA"], sd["SB"]
        if not len(A):
            return None
        AB = B - A
        L2 = np.maximum((AB * AB).sum(axis=1), 1e-24)
        t = np.clip(((P - A) * AB).sum(axis=1) / L2, 0, 1)
        Q = A + AB * t[:, None]
        dd = np.linalg.norm(Q - P, axis=1)
        i = int(np.argmin(dd))
        return (Q[i], A[i], B[i]) if dd[i] <= tol else None

    def _find_snap(self, x, y):
        """Dimension tool: the point / edge / circle under the mouse, in the view under it (vectorised over
        cached per-view arrays, so it stays quick on big models)."""
        doc = self.doc
        P = self.canvas.to_paper(x, y)
        v = self._view_at(P)
        if v is None:
            return None
        tol = SNAP_PX / self.canvas.z
        sd = doc.snap_data(v)
        s = doc.view_scale(v)
        nc = len(doc._snapdata()[1])
        # corners and hole centres
        if len(sd["xy"]):
            dd = np.linalg.norm(sd["xy"] - P, axis=1) - sd["centre"] * 1e-9      # a centre wins a tie
            i = int(np.argmin(dd))
            if dd[i] <= tol:
                kind, P3, xy, extra = sd["items"][i]
                return {"type": "point", "view": v["id"], "kind": kind, "p": P3, "xy": np.asarray(xy, float),
                        "r": extra.get("r"), "i": extra["i"], "n": extra["n"]}
        # round edges and holes seen end-on (also holes drilled across a round part, whose rims aren't flat)
        best_c = None
        near = self._drawn_near(sd, P, tol) if len(sd["cC"]) else None
        if near is not None:
            # a round edge / hole from the model — but only where it's actually drawn under the mouse, and the
            # drawn piece there runs along that circle (not just crosses it): a gear's tooth flanks are arcs, and
            # their full circles mustn't be clickable all the way round
            Q, SA_, SB_ = near
            on = np.ones(len(sd["cC"]), bool)
            # how close "on the circle" is: the drawing's own accuracy, not the mouse's (curves that merely run
            # near a circle — like a gear's tooth outlines past its flank arcs — don't count)
            tight = 0.02 + 2e-3 * sd["cR"]
            for q in (Q, SA_, SB_, (SA_ + SB_) / 2):
                on &= np.abs(np.linalg.norm(sd["cC"] - q, axis=1) - sd["cR"]) <= tight
            dd = np.where(on, np.abs(np.linalg.norm(sd["cC"] - P, axis=1) - sd["cR"]), np.inf)
            i = int(np.argmin(dd))
            if dd[i] <= tol:
                P3, r, C, ci = sd["circ"][i]
                best_c = (float(dd[i]), {"type": "circle", "view": v["id"], "c": C, "R": r * s, "p": P3, "r": r,
                                         "full": doc._snapdata()[1][ci][3], "i": ci, "n": nc})
        # straight edges
        cand = None
        A, B = sd["LA"], sd["LB"]
        if len(A):
            AB = B - A
            L2 = np.maximum((AB * AB).sum(axis=1), 1e-24)
            t = np.clip(((P - A) * AB).sum(axis=1) / L2, 0, 1)
            dd = np.linalg.norm(A + AB * t[:, None] - P, axis=1)
            i = int(np.argmin(dd))
            if dd[i] <= tol:
                cand = (float(dd[i]), ("line", A[i], B[i]))
        for C, R, a0, a1 in sd["hc"]:
            dd = abs(float(np.linalg.norm(P - C)) - R)
            if dd > tol or (cand is not None and dd >= cand[0]):
                continue
            full = a1 - a0 >= 2 * math.pi - 1e-6
            if not full and (math.atan2(P[1] - C[1], P[0] - C[0]) - a0) % (2 * math.pi) > (a1 - a0) + 1e-6:
                continue
            cand = (dd, ("circle", C, R, full))
        if best_c is not None and (cand is None or best_c[0] <= cand[0] + 1e-9 or cand[1][0] == "circle"):
            return best_c[1]
        if cand is None:
            return None
        geo = cand[1]
        if geo[0] == "line":
            ends = []
            vx = [(i, q) for i, q in enumerate(sd["items"]) if q[0] == "vertex"]
            vxy = np.array([q[2] for _i, q in vx], float).reshape(-1, 2)
            for q in (geo[1], geo[2]):
                m = None
                if len(vxy):
                    dd = np.linalg.norm(vxy - q, axis=1)
                    j = int(np.argmin(dd))
                    if dd[j] < 1e-3 * max(s, 1e-9) + 1e-6:
                        _k, P3, _xy, extra = vx[j][1]
                        m = {"kind": "vertex", "p": [float(c) for c in P3], "i": extra["i"], "n": extra["n"]}
                if m is None:
                    # the outline of a round face (e.g. a cylinder from the side): no corner there, so keep the
                    # point where the edge ends in this view
                    m = {"kind": "uv", "uv": [float(c) for c in doc.from_paper(v, q)]}
                ends.append(m)
            return {"type": "line", "view": v["id"], "a": geo[1], "b": geo[2], "p3": ends}
        C, R = geo[1], geo[2]
        for P3, r, xy, ci in sd["circ"]:
            if np.linalg.norm(xy - C) < 1e-3 + 1e-4 * R and abs(r * s - R) < 1e-3 + 1e-4 * R:
                return {"type": "circle", "view": v["id"], "c": C, "R": R, "p": P3, "r": r, "full": geo[3],
                        "i": ci, "n": nc}
        return {"type": "circle", "view": v["id"], "c": C, "R": R, "p": None, "r": R / s, "full": geo[3]}

    # ------------------------------------------------------------------ mouse
    def press(self, x, y, e):
        doc = self.doc
        if doc is None:
            return
        if self.hint.startswith("⚠"):
            self.hint = ""
        P = self.canvas.to_paper(x, y)
        if self.tool == "place" or (self.tool == "projected" and self._place):
            self._place_view(P)
            return
        if self.tool == "projected":
            v = self._view_at(P, pad=2)
            if v is not None:
                self.selection = [v["id"]]
                self._place = {"kind": "projected", "parent": v["id"]}
                self.hint = "Move to the side of the view and click to place it (off a corner gives an isometric " \
                            "view). Esc to cancel."
                self.on_changed()
            return
        if self.tool == "note":
            self.set_tool("select")
            self.add_note(P)
            return
        if self.tool == "dim":
            self._dim_click(x, y, P)
            return
        hit = self._hit(x, y)
        mods = e.modifiers()
        if hit is None:
            if not (mods & (Qt.ShiftModifier | Qt.ControlModifier)):
                self.selection = []
            self.on_changed()
            self._drag = None
            return
        if mods & (Qt.ShiftModifier | Qt.ControlModifier):
            if hit in self.selection:
                self.selection.remove(hit)
            else:
                self.selection.append(hit)
        elif hit not in self.selection:
            self.selection = [hit]
        self._drag = {"id": hit, "start": P, "delta": np.zeros(2), "snap": doc._snapshot(), "moved": False}
        self.on_changed()

    def drag(self, x, y, e, moved):
        d = self._drag
        doc = self.doc
        if d is None or doc is None or not moved:
            return
        P = self.canvas.to_paper(x, y)
        delta = P - d["start"]
        v = doc.view(d["id"])
        if v is not None and v.get("parent") and v.get("align"):
            delta = np.array([delta[0], 0.0]) if v["align"] == "h" else np.array([0.0, delta[1]])
        d["delta"] = delta
        d["moved"] = True
        self.canvas.update()

    def moving(self):
        """(ids being dragged, paper offset) — drawn moved on top of the cached sheet until the mouse is let go."""
        d = self._drag
        doc = self.doc
        if d is None or doc is None or not d.get("moved"):
            return set(), (0.0, 0.0)
        ids = {d["id"]}
        if doc.view(d["id"]) is not None:
            grow = True
            while grow:                              # its projected views come along
                grow = False
                for v in doc.views:
                    if v.get("parent") in ids and v["id"] not in ids:
                        ids.add(v["id"])
                        grow = True
            ids |= {a["id"] for a in doc.annots if a.get("view") in ids}
        return ids, (float(d["delta"][0]), float(d["delta"][1]))

    def release(self, x, y, e, moved):
        d, self._drag = self._drag, None
        doc = self.doc
        if d is None or doc is None:
            return
        if d.get("moved") and np.linalg.norm(d["delta"]) > 0:
            doc._undo.append(d["snap"])
            doc._redo.clear()
            doc.dirty = True
            v = doc.view(d["id"])
            if v is not None:
                doc.move_view(v, d["delta"])
            else:
                a = doc.annot(d["id"])
                if a is not None:
                    a["pos"] = (np.asarray(a["pos"], float) + d["delta"]).tolist()
                    doc.changed(notify=False)
            doc.notify()
        self.canvas.update()

    def hover(self, x, y):
        doc = self.doc
        if doc is None:
            return
        self._cursor = self.canvas.to_paper(x, y)
        if self.tool == "dim":
            self._snap = self._find_snap(x, y)
            self.canvas.update()
            return
        if self.tool in ("place", "projected", "note"):
            self.canvas.update()
            return
        h = self._hit(x, y)
        if h != self.hover_id:
            self.hover_id = h
            self.canvas.update()
        self.canvas.setCursor(Qt.SizeAllCursor if h else Qt.ArrowCursor)

    def double_click(self, x, y):
        if self.tool != "select":
            return
        h = self._hit(x, y)
        if h:
            self.edit(h)

    def context(self, x, y, gpos):
        if self.tool != "select":
            self.set_tool("select")
            return
        h = self._hit(x, y)
        if h and h not in self.selection:
            self.selection = [h]
            self.on_changed()
        self._menu_for(h or "__sheet", gpos, at=self.canvas.to_paper(x, y))

    def key(self, e):
        k = e.key()
        if k == Qt.Key_Escape:
            if self.tool != "select":
                self.set_tool("select")
            else:
                self.selection = []
                self.on_changed()
            return True
        if k in (Qt.Key_Delete, Qt.Key_Backspace) and self.selection and self.tool == "select":
            self.doc.delete(self.selection)
            self.selection = []
            return True
        if k == Qt.Key_F and not e.modifiers():
            self.canvas.fit()
            return True
        if k == Qt.Key_D and not e.modifiers():
            self.set_tool("dim")
            return True
        if k == Qt.Key_N and not e.modifiers():
            self.set_tool("note")
            return True
        return False

    # ------------------------------------------------------------------ menus & editing
    def _menu_for(self, oid, gpos, at=None):
        doc = self.doc
        if doc is None:
            return
        m = QMenu(self.w)
        v, a = doc.view(oid), doc.annot(oid)
        if v is not None:
            m.addAction("View properties…", lambda: self.edit(oid))
            m.addAction("Projected view from this…", lambda: (setattr(self, "selection", [oid]),
                                                                self.set_tool("projected")))
            act = m.addAction("Hidden edges")
            act.setCheckable(True)
            act.setChecked(bool(v.get("hidden")))
            act.triggered.connect(lambda on: self._set_view(v, "hidden", on))
            act = m.addAction("Centre marks")
            act.setCheckable(True)
            act.setChecked(bool(v.get("centre_marks", True)))
            act.triggered.connect(lambda on: self._set_view(v, "centre_marks", on))
            m.addSeparator()
            m.addAction("Delete view", lambda: doc.delete([oid]))
        elif a is not None:
            m.addAction("Edit…", lambda: self.edit(oid))
            m.addAction("Delete", lambda: doc.delete([oid]))
        else:
            m.addAction(icon("base_view", 16), "Insert view…", self.insert_view)
            m.addAction(icon("dim", 16), "Dimension (D)", lambda: self.set_tool("dim"))
            if at is not None:
                m.addAction(icon("note", 16), "Note here…", lambda: self.add_note(at))
            m.addSeparator()
            m.addAction(icon("sheet", 16), "Sheet and title block…", self.sheet_setup)
            m.addAction("Zoom to fit (F)", self.canvas.fit)
        m.exec(gpos)

    def _set_view(self, v, key, value):
        self.doc.checkpoint()
        v[key] = bool(value)
        self.doc.changed()

    def edit(self, oid):
        doc = self.doc
        if oid == "__sheet":
            self.sheet_setup()
            return
        v, a = doc.view(oid), doc.annot(oid)
        if v is not None:
            dlg = ViewDialog(self.w, doc, v)
            if dlg.exec() != QDialog.Accepted:
                return
            doc.checkpoint()
            x0, y0, x1, y1 = doc.view_box(v)
            c = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
            name = dlg.dir.currentData()
            if name and dlg.dir.isEnabled():
                d, up = DIRECTIONS[name]
                v["d"], v["up"] = [float(x) for x in DW._unit(d)], list(up)
                v["type"] = "iso" if name == "Isometric" else ("base" if v["type"] == "iso" else v["type"])
            v["scale"] = dlg.scale.currentData()
            v["hidden"] = dlg.hidden.isChecked()
            v["tangent"] = dlg.tangent.isChecked()
            v["centre_marks"] = dlg.marks.isChecked()
            v["label"] = dlg.label.text().strip()
            lo, hi = doc.model_extent(v["d"], v["up"])
            v["o"] = (c - doc.view_scale(v) * (lo + hi) / 2).tolist()
            for ch in doc.views:
                if ch.get("parent") == v["id"]:
                    if not ch.get("scale") or ch.get("scale") == v.get("scale"):
                        ch["scale"] = v["scale"]
                    doc._line_up(ch)
            doc._line_up(v)
            doc.changed()
            return
        if a is None:
            return
        if a["type"] == "note":
            txt, ok = QInputDialog.getMultiLineText(self.w, "Note", "Text:", a.get("text", ""))
            if ok:
                doc.checkpoint()
                a["text"] = txt.strip()
                doc.changed()
            return
        dlg = DimDialog(self.w, doc, a)
        if dlg.exec() != QDialog.Accepted:
            return
        doc.checkpoint()
        a["dp"] = dlg.dp.currentData()
        for k in ("prefix", "suffix", "text"):
            val = getattr(dlg, k).text()
            if val.strip():
                a[k] = val
            else:
                a.pop(k, None)
        if a["type"] == "lin":
            a["orient"] = dlg.orient.currentData()
        doc.changed()

    def _panel_clicked(self, oid):
        if not oid:
            return
        if oid == "__sheet":
            self.selection = []
        else:
            self.selection = [oid]
        self.canvas.update()

    def _panel_double(self, oid):
        if oid:
            self.edit(oid)

    # ------------------------------------------------------------------ placing views
    def _ghost(self, P):
        """(paper box, label) of the view that a click at P would place."""
        doc = self.doc
        pl = self._place
        if pl is None:
            return None
        if pl["kind"] == "base":
            sc = pl.get("scale") or doc.sheet.get("scale", 1.0)
            lo, hi = doc.model_extent(pl["d"], pl["up"])
            w, h = (hi - lo) * sc
            return (P[0] - w / 2, P[1] - h / 2, P[0] + w / 2, P[1] + h / 2), ""
        parent = doc.view(pl["parent"])
        if parent is None:
            return None
        box = doc.view_box(parent)
        c = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])
        side = P - c
        if np.linalg.norm(side) < 1e-6:
            return None
        diag = min(abs(side[0]), abs(side[1])) > 0.5 * max(abs(side[0]), abs(side[1]))
        sc = doc.view_scale(parent)
        if diag:
            r, u, d = doc.axes(parent)
            nd = d + r * np.sign(side[0]) + u * np.sign(side[1])
            lo, hi = doc.model_extent(nd, u)
            w, h = (hi - lo) * sc
            return (P[0] - w / 2, P[1] - h / 2, P[0] + w / 2, P[1] + h / 2), "Isometric"
        nd, nup, al = doc.projected_dir(parent, side)
        lo, hi = doc.model_extent(nd, nup)
        w, h = (hi - lo) * sc
        cx, cy = (P[0], c[1]) if al == "h" else (c[0], P[1])
        name = next((n for n, (dd, _u) in DIRECTIONS.items() if np.allclose(DW._unit(dd), nd, atol=1e-6)), "")
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), name

    def _place_view(self, P):
        doc = self.doc
        pl = self._place
        if pl is None:
            return
        if pl["kind"] == "base":
            doc.checkpoint()
            v = doc.add_view(pl["d"], pl["up"], P, kind="iso" if pl.get("iso") else "base", checkpoint=False)
            v["scale"] = pl.get("scale")
            v["hidden"], v["tangent"], v["centre_marks"] = pl["hidden"], pl["tangent"], pl["marks"]
            v["label"] = pl.get("label", "")
            lo, hi = doc.model_extent(v["d"], v["up"])
            v["o"] = (np.asarray(P, float) - doc.view_scale(v) * (lo + hi) / 2).tolist()
            doc.changed()
            self.selection = [v["id"]]
            self.set_tool("select")
            return
        parent = doc.view(pl["parent"])
        if parent is None:
            self.set_tool("select")
            return
        v = doc.add_projected(parent, P)
        if v is not None:
            self.selection = [v["id"]]
        self._place = {"kind": "projected", "parent": pl["parent"]}   # keep placing more from the same view
        self.on_changed()

    # ------------------------------------------------------------------ dimensions
    def _dim_click(self, x, y, P):
        s = self._find_snap(x, y)
        picks = self._picks
        if picks and picks[-1].get("ready"):
            self._create_dim(P)                 # placement click
            return
        if s is None:
            if len(picks) == 1 and picks[0]["type"] == "line":
                self._create_dim(P)             # one edge, then empty space: its length
            return
        if picks and s.get("view") != picks[0].get("view"):
            self.flash("Both ends of a dimension have to be in the same view")
            return
        place = "Click where the dimension should go."
        if s["type"] == "circle":
            if s["p"] is None:
                self.flash("That curve isn't a circle facing this view — pick its centre or edges instead")
                return
            if not picks:
                picks.append({**s, "ready": True})
            else:
                picks.append({"type": "point", "view": s["view"], "kind": "centre", "p": s["p"], "xy": s["c"],
                              "r": s["r"], "i": s["i"], "n": s["n"], "ready": True})
            self.hint = place
        elif s["type"] == "line":
            if not picks:
                picks.append(s)
                self.hint = "Click where the length should go — or click another edge (distance between them) " \
                            "or a point."
            else:
                if picks[0]["type"] == "line":
                    d1 = DW._unit(np.asarray(picks[0]["b"]) - picks[0]["a"])
                    d2 = DW._unit(np.asarray(s["b"]) - s["a"])
                    if abs(d1[0] * d2[1] - d1[1] * d2[0]) > 1e-3:
                        self.flash("Those edges aren't parallel — pick parallel edges (angles aren't supported yet)")
                        return
                picks.append({**s, "ready": True})
                self.hint = place
        else:   # a point
            if not picks:
                picks.append(s)
                self.hint = "Now click a second point or an edge."
            else:
                if picks[0]["type"] == "point" and np.linalg.norm(np.asarray(s["p"]) - np.asarray(picks[0]["p"])) < 1e-9:
                    return
                picks.append({**s, "ready": True})
                self.hint = place
        self.canvas.update()

    def _spec(self, P):
        """The annotation the current picks would make if placed at P."""
        picks = self._picks
        if not picks:
            return None
        if not picks[-1].get("ready") and not (len(picks) == 1 and picks[0]["type"] == "line"):
            return None
        v = self.doc.view(picks[0]["view"])
        end = lambda pk: {"kind": pk["kind"], "p": [float(c) for c in pk["p"]], "i": pk.get("i"), "n": pk.get("n"),
                          **({"r": float(pk["r"])} if pk.get("kind") == "centre" and pk.get("r") else {})}
        a = {"view": v["id"], "pos": [float(P[0]), float(P[1])]}
        first = picks[0]
        if len(picks) == 1 and first["type"] == "circle":
            a.update(type="dia" if first.get("full", True) else "rad",
                     a={"kind": "centre", "p": [float(c) for c in first["p"]], "r": float(first["r"]),
                        "i": first.get("i"), "n": first.get("n")})
            return a
        if len(picks) == 1 and first["type"] == "line":
            A, B = dict(first["p3"][0]), dict(first["p3"][1])
            xa, xb = np.asarray(first["a"]), np.asarray(first["b"])
            a.update(type="lin", a=A, b=B, orient=self._orient(xa, xb, P))
            return a
        second = picks[1]
        if "line" in (first["type"], second["type"]):
            # a point (or the end of the other edge) and an edge: the square distance to that edge
            line, other = (second, first) if second["type"] == "line" else (first, second)
            pt = dict(other["p3"][0]) if other["type"] == "line" else end(other)
            a.update(type="pl", a=pt, b=dict(line["p3"][0]), b2=dict(line["p3"][1]))
            if other["type"] == "line" and self._is_cylinder_width(v, other, line):
                a["prefix"] = "Ø"            # the two outline edges of a round part: it's a diameter
            return a
        a.update(type="lin", a=end(first), b=end(second),
                 orient=self._orient(np.asarray(first["xy"]), np.asarray(second["xy"]), P))
        return a

    def _is_cylinder_width(self, v, l1, l2):
        """Are two parallel edges in a view the two sides of the same cylinder (seen from the side)?"""
        doc = self.doc
        a1, b1 = doc.from_paper(v, l1["a"]), doc.from_paper(v, l1["b"])
        a2 = doc.from_paper(v, l2["a"])
        d = DW._unit(b1 - a1)
        n = np.array([-d[1], d[0]])
        gap = abs(float((a2 - a1) @ n))
        if gap < 1e-9:
            return False
        mid = float((a1 @ n + a2 @ n) / 2)
        tol = 1e-4 * doc._size() + 1e-6
        _r, _u, vd = doc.axes(v)
        for c, ax, r, _full, _ff in doc._snapdata()[1]:
            if abs(2 * r - gap) > tol or abs(float(np.dot(DW._unit(ax), vd))) > 1e-6:
                continue
            uv = doc.project(v, c)
            if abs(float(uv @ n) - mid) < tol and abs(float(np.dot(DW._unit(doc.project(v, np.asarray(c) + ax)
                                                                         - uv), n))) < 1e-6:
                return True
        return False

    @staticmethod
    def _orient(A, B, P):
        if abs(A[1] - B[1]) < 1e-6:
            return "h"
        if abs(A[0] - B[0]) < 1e-6:
            return "v"
        lo, hi = np.minimum(A, B), np.maximum(A, B)
        if lo[0] <= P[0] <= hi[0] and not (lo[1] <= P[1] <= hi[1]):
            return "h"
        if lo[1] <= P[1] <= hi[1] and not (lo[0] <= P[0] <= hi[0]):
            return "v"
        return "a"

    def _create_dim(self, P):
        a = self._spec(P)
        self._picks = []
        if a is None:
            return
        self.doc.add_annot(a)
        self.hint = "Dimension added. Click the next edge or point — Esc to stop."
        self.canvas.update()

    # ------------------------------------------------------------------ overlay (tool feedback)
    def draw_tool_overlay(self, p, canvas):
        doc = self.doc
        z = canvas.z
        P = self._cursor
        if self.tool == "dim":
            for pk in self._picks:
                self._draw_pick(p, pk, z, ACCENT)
            if self._snap is not None and not (self._picks and self._picks[-1].get("ready")):  # what a click picks
                self._draw_pick(p, self._snap, z, HOVER)
            placing = self._picks and (self._picks[-1].get("ready") or (
                len(self._picks) == 1 and self._picks[0]["type"] == "line" and self._snap is None))
            if P is not None and placing:
                a = self._spec(P)
                if a is not None:
                    a["id"] = "__preview"
                    items = []
                    try:
                        doc._dim_items(items, a)
                    except Exception:
                        items = []
                    paint_groups(p, build_groups(items), z, override=QColor(ACCENT))
        if P is not None and (self.tool == "place" or (self.tool == "projected" and self._place)):
            gh = self._ghost(P)
            if gh is not None:
                (x0, y0, x1, y1), name = gh
                p.setPen(QPen(ACCENT, 1.4 / z, Qt.DashLine))
                p.setBrush(QColor(31, 111, 209, 22))
                p.drawRect(QRectF(x0, y0, x1 - x0, y1 - y0))
                if name:
                    draw_text(p, ((x0 + x1) / 2, y1 + 2.5), name, 3.0, 0.0, "center", "bottom", ACCENT)
        if self.tool == "note" and P is not None:
            draw_text(p, (P[0] + 2, P[1] - 2), "Note", 3.5, 0.0, "left", "top", QColor(31, 111, 209, 140))

    def _draw_pick(self, p, pk, z, col):
        r = 4.5 / z
        if pk["type"] == "point":
            xy = pk["xy"]
            p.setPen(QPen(col, 2.0 / z))
            p.setBrush(QColor(col.red(), col.green(), col.blue(), 60))
            if pk.get("kind") == "centre":
                p.drawEllipse(QPointF(*xy), r, r)
                p.drawLine(QPointF(xy[0] - r * 1.6, xy[1]), QPointF(xy[0] + r * 1.6, xy[1]))
                p.drawLine(QPointF(xy[0], xy[1] - r * 1.6), QPointF(xy[0], xy[1] + r * 1.6))
            else:
                p.drawRect(QRectF(xy[0] - r, xy[1] - r, 2 * r, 2 * r))
        elif pk["type"] == "line":
            p.setPen(QPen(col, 3.0 / z, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(*pk["a"]), QPointF(*pk["b"]))
        elif pk["type"] == "circle":
            p.setPen(QPen(col, 3.0 / z, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            if pk.get("full", True):
                p.drawEllipse(QPointF(*pk["c"]), pk["R"], pk["R"])
            else:
                # an arc: light up only the drawn pieces that lie on it
                v = self.doc.view(pk["view"])
                sd = self.doc.snap_data(v) if v is not None else None
                if sd is not None and len(sd["SA"]):
                    C, R = np.asarray(pk["c"], float), float(pk["R"])
                    tol = 0.02 + 2e-3 * R
                    ok = (np.abs(np.linalg.norm(sd["SA"] - C, axis=1) - R) <= tol) & \
                         (np.abs(np.linalg.norm(sd["SB"] - C, axis=1) - R) <= tol)
                    for a_, b_ in zip(sd["SA"][ok], sd["SB"][ok]):
                        p.drawLine(QPointF(*a_), QPointF(*b_))

    # ------------------------------------------------------------------ export
    def export(self, fmt_=None):
        doc = self.doc
        if doc is None:
            return
        base = (doc.title.get("number") or doc.name).replace(" ", "_")
        if fmt_ is None:
            m = QMenu(self.w)
            m.addAction(icon("dxf", 16), "DXF (AutoCAD)…", lambda: self.export("dxf"))
            m.addAction(icon("pdf", 16), "PDF…", lambda: self.export("pdf"))
            from PySide6.QtGui import QCursor
            m.exec(QCursor.pos())
            return
        if not self.wait_exact():
            return
        if fmt_ == "pdf":
            path, _ = QFileDialog.getSaveFileName(self.w, "Export PDF", base + ".pdf", "PDF (*.pdf)")
            if not path:
                return
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            try:
                export_pdf(doc, path, self.canvas.groups())
                self.w.status.showMessage(f"Exported {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self.w, "FreeShape", f"Export failed:\n{e}")
            return
        dlg = QDialog(self.w)
        dlg.setWindowTitle("Export DXF")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("How should the drawing be sized in the DXF?"))
        c1 = QCheckBox(f"Views at true size (1:1) — everything scaled by {DW.fmt_value(1 / doc.sheet.get('scale', 1))}×")
        c1.setToolTip("Best for working on the geometry in AutoCAD: a 50 mm edge measures 50 mm.\n"
                      "Leave it off to get the sheet exactly as it prints (paper millimetres).")
        sc = float(doc.sheet.get("scale", 1.0))
        c1.setChecked(False)
        c1.setEnabled(abs(sc - 1.0) > 1e-12)
        lay.addWidget(c1)
        info = QLabel("AutoCAD 2010 DXF: real AutoCAD dimensions (grip-edit them, change their style), Arial text, "
                      "and layers VISIBLE, HIDDEN (dashed), CENTER, DIM, NOTE, BORDER, TITLE… Opens in AutoCAD / "
                      "AutoCAD LT, DraftSight, LibreCAD and laser-cutter software.")
        info.setWordWrap(True)
        info.setObjectName("muted")
        lay.addWidget(info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        lay.addWidget(bb)
        if abs(sc - 1.0) > 1e-12 and dlg.exec() != QDialog.Accepted:
            return
        path, _ = QFileDialog.getSaveFileName(self.w, "Export DXF", base + ".dxf", "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            DW.export_dxf(doc, path, full_size=c1.isChecked())
            self.w.status.showMessage(f"Exported {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self.w, "FreeShape", f"Export failed:\n{e}")

    def thumbnail(self):
        doc = self.doc
        if doc is None:
            return None
        return render_sheet_image(doc, groups=self.canvas.groups())
