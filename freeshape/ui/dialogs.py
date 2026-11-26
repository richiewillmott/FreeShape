"""Floating, Onshape-style feature dialogs (green check / red X), built from small schemas."""
from __future__ import annotations

from ..i18n import tr

import copy
import json
import math

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal, QSize, QEvent
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QWidget, QButtonGroup, QSizePolicy, QGraphicsDropShadowEffect, QApplication,
    QPushButton, QStyledItemDelegate, QStyleOptionViewItem,
)

from .. import kernel as K
from .. import features as F
from .icons import icon, dialog_button
from . import theme
from .numparse import parse_number, fmt, has_vars
from .viewport import Pick

OP_LABELS = [("new", "New"), ("add", "Add"), ("remove", "Remove"), ("intersect", "Intersect")]


from .. import units as U  # noqa: E402
from ..expr import with_unit  # noqa: E402


def _show(v):
    """Text for a number field: expressions stay as typed, numbers are tidied."""
    return v if isinstance(v, str) else fmt(v)


# ---------------------------------------------------------------------- pick <-> ref conversion

def region_touching(sr, eid):
    """Index of the sketch region whose outline runs along sketch entity `eid` (the smallest such region, so a
    hole's circle picks the hole rather than the plate around it), or None."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    try:
        pts = sr.sketch.polyline(eid, 16)
    except Exception:
        return None
    if not len(pts):
        return None
    u, v = pts[len(pts) // 2]
    P = sr.plane.to_world(u, v)
    vert = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, P))).Vertex()
    best = None
    for ri, face in enumerate(sr.regions):
        dd = None
        for e in K.edges(face):
            ext = BRepExtrema_DistShapeShape(vert, e)
            if ext.IsDone():
                d = ext.Value()
                dd = d if dd is None else min(dd, d)
        if dd is not None and dd < 1e-5 * max(1.0, float(np.linalg.norm(P))) + 1e-6:
            area = K.face_info(face).get("area", 0.0)
            if best is None or area < best[0]:
                best = (area, ri)
    return None if best is None else best[1]


def pick_to_item(vp, pick: Pick, want: set):
    """Convert a viewport pick into a (kind, ref) item for a selection field."""
    k = pick.kind
    if k == "sketch_line" and "sketch_line" in want:
        # one sketch curve on its own (Measure value) — before 'a sketch line picks the region it bounds'
        fid, eid = pick.key
        return ("sketch_line", {"sketch": fid, "entity": eid})
    if k == "sketch_point" and "vertex" in want:
        fid, ptid = pick.key
        for sfid, sr, _ in vp.sketches:
            if sfid == fid and ptid in sr.sketch.points:
                return ("vertex", F.sketch_point_ref(sr, fid, ptid))
        return None
    if k == "region" and "region" in want:
        fid, ri = pick.key
        for sfid, sr, _ in vp.sketches:
            if sfid == fid and ri < len(sr.regions):
                return ("region", F.region_ref(sr, fid, sr.regions[ri]))
    if k == "face":
        pid, fi = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        face = K.faces(shape)[fi]
        if "face" in want:
            return ("face", F.make_ref(pid, face, "face", shape))
        if "plane" in want:
            pl = K.planar_face_plane(face)
            if pl is not None:
                return ("plane", {"kind": "face", "face": F.make_ref(pid, face, "face", shape),
                                  "origin": pl.origin.tolist(), "xdir": pl.xdir.tolist()})
        if "edge" in want:          # selecting a face selects all of its edges (fillet/chamfer)
            return ("edges", [F.make_ref(pid, e, "edge", shape) for e in K.edges(face)])
        if "part" in want:
            return ("part", pid)
    if k == "part" and "part" in want:
        return ("part", pick.key[0])
    if k == "edge":
        pid, ei = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        edge = K.edges(shape)[ei]
        if "edge" in want:
            return ("edge", F.make_ref(pid, edge, "edge", shape))
        if "axis" in want:
            return ("axis", {"kind": "edge", "edge": F.make_ref(pid, edge, "edge", shape)})
    if k == "sketch_line" and "region" in want:
        # clicking a sketch's line (or circle) picks the closed area it bounds
        fid, eid = pick.key
        for sfid, sr, _ in vp.sketches:
            if sfid != fid or not sr.regions:
                continue
            ri = region_touching(sr, eid)
            if ri is not None:
                return ("region", F.region_ref(sr, fid, sr.regions[ri]))
        return None
    if k == "sketch_line" and "path" in want:
        fid, eid = pick.key
        return ("path", {"kind": "sketch_chain", "sketch": fid, "entity": eid})
    if "outline" in want and k in ("region", "face"):
        # Surface / Thin: a region or flat face stands for its boundary
        from .. import body_options as BO
        if k == "region":
            got = pick_to_item(vp, pick, {"region"})
            return ("path", BO.outline_ref("region", got[1])) if got else None
        got = pick_to_item(vp, pick, {"face"})
        if got:
            pid, fi = pick.key
            if K.planar_face_plane(K.faces(vp.part_shapes[pid])[fi]) is not None:
                return ("path", BO.outline_ref("face", got[1]))
        return None
    if k == "edge" and "path" in want:
        pid, ei = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is not None:
            return ("path", {"kind": "edge", "edge": F.make_ref(pid, K.edges(shape)[ei], "edge", shape)})
    if k == "sketch_line" and "axis" in want:
        fid, eid = pick.key
        return ("axis", {"kind": "sketch_line", "sketch": fid, "line": eid})
    if k == "vertex" and "vertex" in want:
        pid, vi = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        pts = K.vertex_points(shape)
        if vi >= len(pts):
            return None
        return ("vertex", F.make_vertex_ref(pid, pts[vi], shape))
    if k == "plane" and "plane" in want:
        key = pick.key
        if key[0] == "std":
            return ("plane", {"kind": "std", "name": key[1]})
        return ("plane", {"kind": "plane", "fid": key[1]})
    return None


def item_label(doc, item):
    kind, ref = item
    if kind == "region":
        return "Sketch region"
    if kind == "face":
        return "Face"
    if kind == "edge":
        return "Edge"
    if kind == "vertex":
        return "Sketch point" if ref.get("kind") == "sketch_point" else "Vertex"
    if kind == "sketch_line":
        return "Sketch curve"
    if kind == "part":
        p = doc.state.parts.get(ref)
        return p.name if p else ref
    if kind == "feature":
        f = doc.feature(ref)
        return f["name"] if f else ref
    if kind == "plane":
        if ref["kind"] == "std":
            return f"{ref['name']} plane"
        if ref["kind"] == "plane":
            f = doc.feature(ref["fid"])
            return f["name"] if f else "Plane"
        return "Planar face"
    if kind == "path":
        if ref["kind"] == "outline":
            return "Outline of sketch region" if ref.get("of") == "region" else "Outline of face"
        if ref["kind"] == "region_edge":
            return "Edge of sketch region"
        if ref["kind"] == "sketch_chain" and ref.get("only"):
            return "Sketch curve" if len(ref["only"]) == 1 else f"{len(ref['only'])} sketch curves"
        return "Sketch curves" if ref["kind"] == "sketch_chain" else "Edge"
    if kind == "axis":
        if ref["kind"] == "std":
            return f"{ref['name']} axis"
        if ref["kind"] == "sketch_line":
            return "Sketch line"
        return "Edge"
    return kind


def item_geometry(state, item, plane_half=40.0, plane_rect=None):
    """World geometry used to highlight a selected item: ('mesh', Mesh) or ('poly', ndarray). plane_rect(ref):
    the (u0, u1, v0, v1) a picked plane is drawn over (its resized size), or None for ±plane_half."""
    kind, ref = item
    try:
        if kind == "region":
            face = F.resolve_regions(state, [ref])[0]
            return ("mesh", K.tessellate(face))
        if kind == "face":
            _, face = F.resolve(state, ref, "face")
            return ("mesh", K.tessellate(face))
        if kind == "edge":
            _, e = F.resolve(state, ref, "edge")
            return ("poly", K.discretize_edge(e))
        if kind == "vertex":
            c = F.resolve_point(state, ref)
            s = plane_half * 0.06
            seg = [np.array(a) for a in ((s, 0, 0), (0, s, 0), (0, 0, s))]
            pts = []
            for d in seg:
                pts += [c - d, c + d, c]
            return ("poly", np.array(pts))
        if kind == "part":
            p = state.parts.get(ref)
            return ("mesh", K.tessellate(p.shape)) if p else None
        if kind == "sketch_line":
            k2, shp = F.resolve_sketch_curve(state, ref)
            if k2 == "vertex":
                return item_geometry(state, ("vertex", {"kind": "sketch_point", "sketch": ref["sketch"],
                                                        "point": state.sketches[ref["sketch"]].sketch.entities[
                                                            ref["entity"]]["p"]}), plane_half)
            m = K.Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32),
                       np.zeros(0, np.int32), [], [K.discretize_edge(e) for e in K.edges(shp)])
            return ("mesh", m)
        if kind == "plane":
            pl = F.resolve_plane(state, ref)
            h = plane_half
            r = plane_rect(ref) if plane_rect is not None else None
            u0, u1, v0, v1 = r if r else (-h, h, -h, h)
            return ("poly", np.array([pl.to_world(u, v) for u, v in ((u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0))]))
        if kind == "path":
            if ref.get("kind") == "outline":
                from .. import body_options as BO
                es = [e for w in BO.outline_wires([F.outline_face(state, ref)]) for e in K.edges(w)]
                m = K.Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32),
                           np.zeros(0, np.int32), [], [K.discretize_edge(e) for e in es])
                return ("mesh", m)
            w = F._path_wire(state, [ref])
            m = K.Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32), np.zeros((0, 3), np.uint32),
                       np.zeros(0, np.int32), [], [K.discretize_edge(e) for e in K.edges(w)])
            return ("mesh", m)
        if kind == "axis":
            a, d = F.resolve_axis(state, ref)
            return ("poly", np.array([a - d * plane_half * 1.5, a + d * plane_half * 1.5]))
    except K.KernelError:
        return None
    return None


# ---------------------------------------------------------------------- selection field widget

class _PickList(QListWidget):
    """The picks in a selection box, one per row, each with a × at its right end to take it out again (as in
    Onshape): faint while the mouse is elsewhere, red under the mouse. Delete removes the highlighted row, and a
    double-click still works."""
    removeRequested = Signal(int)
    X_W = 20

    def __init__(self):
        super().__init__()
        self.setMouseTracking(True)
        self._hover = -1
        self.setItemDelegate(_PickRowDelegate(self))

    def _x_rect(self, row):
        r = self.visualItemRect(self.item(row))
        right = min(r.right(), self.viewport().rect().right())
        from PySide6.QtCore import QRect
        return QRect(right - self.X_W + 1, r.top(), self.X_W, r.height())

    def removable(self, row):
        it = self.item(row)
        return it is not None and it.data(Qt.UserRole) is not None

    def _x_at(self, pos):
        it = self.itemAt(pos)
        if it is None:
            return -1
        row = self.row(it)
        return row if self.removable(row) and self._x_rect(row).contains(pos) else -1

    def mouseMoveEvent(self, e):
        super().mouseMoveEvent(e)
        it = self.itemAt(e.position().toPoint())
        row = self.row(it) if it is not None else -1
        if row != self._hover:
            self._hover = row
            self.viewport().update()
        over = self._x_at(e.position().toPoint()) >= 0
        self.viewport().setCursor(Qt.PointingHandCursor if over else Qt.ArrowCursor)

    def leaveEvent(self, e):
        super().leaveEvent(e)
        self._hover = -1
        self.viewport().update()

    def mousePressEvent(self, e):
        row = self._x_at(e.position().toPoint()) if e.button() == Qt.LeftButton else -1
        if row >= 0:
            self.removeRequested.emit(int(self.item(row).data(Qt.UserRole)))
            e.accept()
            return
        super().mousePressEvent(e)

    def event(self, e):
        # Delete / Backspace on a pick belong to this list, not the window's Delete shortcut
        if e.type() == QEvent.ShortcutOverride and e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            it = self.currentItem()
            if it is not None and it.data(Qt.UserRole) is not None:
                e.accept()
                return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            it = self.currentItem()
            if it is not None and it.data(Qt.UserRole) is not None:
                self.removeRequested.emit(int(it.data(Qt.UserRole)))
                return
        super().keyPressEvent(e)


class _PickRowDelegate(QStyledItemDelegate):
    def paint(self, p, opt, index):
        o = QStyleOptionViewItem(opt)
        o.rect = opt.rect.adjusted(0, 0, -_PickList.X_W, 0)      # the name stops short of the ×
        super().paint(p, o, index)
        view = self.parent()
        if index.data(Qt.UserRole) is None:
            return
        hover = view._hover == index.row()
        r = view._x_rect(index.row())
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPen
        c = QColor(theme.ERROR) if hover else QColor("#9aa3ae")
        if not hover:
            c.setAlpha(150)
        pen = QPen(c, 1.6)
        pen.setCapStyle(Qt.RoundCap)
        p.save()
        p.setRenderHint(p.RenderHint.Antialiasing)
        p.setPen(pen)
        cx, cy, h = r.center().x() + 0.5, r.center().y() + 0.5, 3.6
        p.drawLine(QPointF(cx - h, cy - h), QPointF(cx + h, cy + h))
        p.drawLine(QPointF(cx + h, cy - h), QPointF(cx - h, cy + h))
        p.restore()


class SelectionField(QFrame):
    activated = Signal(object)
    changed = Signal()

    def __init__(self, key, label, want, multi=True, parent=None):
        super().__init__(parent)
        self.setObjectName("selField")
        self.key, self.want, self.multi = key, set(want), multi
        self.items: list = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        lay.setSpacing(2)
        top = QHBoxLayout()
        self.title = QLabel(tr(label))
        self.title.setWordWrap(True)
        self.title.setStyleSheet("color:#4b5563;")
        top.addWidget(self.title, 1)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip(tr("Clear selections"))
        self.clear_btn.clicked.connect(self.clear)
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.list = _PickList()
        self.list.setFixedHeight(22)
        self.extra = 0              # extra height (px) the dialog has been dragged taller by
        self._base_h = 22
        self.list.setStyleSheet("QListWidget { background: transparent; }")
        self.list.itemDoubleClicked.connect(self._remove_item)
        self.list.removeRequested.connect(self.remove_at)
        lay.addWidget(self.list)
        self.set_active(False)

    def mousePressEvent(self, e):
        self.activated.emit(self)

    def set_active(self, a):
        self.setProperty("active", "true" if a else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def refresh(self, doc, missing=()):
        self.list.clear()
        if not self.items:
            it = QListWidgetItem(tr("Click to select") if self.property("active") == "true" else "—")
            it.setForeground(QColor("#8a94a3"))
            self.list.addItem(it)
        for i, item in enumerate(self.items):
            label = item_label(doc, item)
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, i)                          # which pick the row is (its × removes it)
            if i in missing:
                it.setForeground(QColor("#d93025"))
                it.setToolTip(f"{label} — its reference could not be found. Click × to remove it")
            else:
                it.setToolTip(f"Click × to remove {label}")
            self.list.addItem(it)
        n = max(1, len(self.items))
        self._base_h = min(6, n) * 20 + 4
        self.list.setFixedHeight(self._base_h + self.extra)

    def set_extra(self, px):
        self.extra = max(0, int(px))
        self.list.setFixedHeight(self._base_h + self.extra)
        self.layout().invalidate()          # Qt re-lays nested widgets lazily; the dialog needs the new size now
        self.updateGeometry()

    def _remove_item(self, it):
        i = it.data(Qt.UserRole)
        if i is not None:
            self.remove_at(int(i))

    def remove_at(self, i):
        """Take one pick out of the box (its row's ×, Delete, or a double-click)."""
        if 0 <= i < len(self.items):
            self.items.pop(i)
            self.changed.emit()

    def clear(self):
        self.items = []
        self.changed.emit()

    def toggle(self, item):
        kind, ref = item
        if kind == "edges":
            for r in ref:
                self.toggle(("edge", r))
            return
        for i, ex in enumerate(self.items):
            if _same(ex, item):
                self.items.pop(i)
                self.changed.emit()
                return
        if not self.multi:
            self.items = []
        self.items.append(item)
        self.changed.emit()


def _same(a, b):
    if a[0] != b[0]:
        return False
    ra, rb = a[1], b[1]
    if isinstance(ra, str) or isinstance(rb, str):
        return ra == rb
    if "c" in ra and "c" in rb:
        return np.linalg.norm(np.array(ra["c"]) - np.array(rb["c"])) < 1e-6 and ra.get("sketch") == rb.get("sketch")
    return ra == rb


# ---------------------------------------------------------------------- schemas

def _isnum(v):
    try:
        return abs(float(v)) > 1e-12
    except (TypeError, ValueError):
        return True if isinstance(v, str) and v.strip() else False


def _plane_info(p):
    """What the plane dialog says about the current selection."""
    items = F.plane_items(p)
    typ = F.plane_type(p)
    if typ is None:
        return "⚠ That selection doesn't fit a plane type. Try a plane or face, an edge, or vertices."
    name = F.PLANE_TYPE_NAMES.get(typ, typ)
    if (p.get("type") or "auto") == "auto":
        if not items:
            return "Select a plane or face, an edge, or vertices — the plane type is picked from what you select."
        return f"Type: {name}"
    return ""


def _hole_info(p):
    if p.get("standard") == "sketch":
        return "Each hole is the size of the circle you drew"
    try:
        d, cb, cs, label = F.hole_dims(p)
    except Exception:
        return ""
    t = f"{label}: drill Ø{fmt(d)}"
    if cb:
        t += f", counterbore Ø{fmt(cb[0])} × {fmt(cb[1])} deep"
    if cs:
        t += f", countersink Ø{fmt(cs)} × 90°"
    return t


def _num(key, label, unit="mm", visible=None, minimum=None):
    return {"t": "num", "key": key, "label": label, "unit": unit, "visible": visible, "min": minimum}


def _choice(key, label, options, visible=None):
    return {"t": "choice", "key": key, "label": label, "options": options, "visible": visible}


def _check(key, label, visible=None):
    return {"t": "check", "key": key, "label": label, "visible": visible}


def _flip_look(b):
    """An arrow button's icon and tip for its state."""
    on = b.isChecked()
    b.setIcon(icon("dir_flipped" if on else "dir_normal"))
    b.setToolTip(f"{b.property('flip_label')}: {'flipped' if on else 'not flipped'} — click to flip "
                 "(or click the arrow in the view)")


def _flip(key, label, inline=None, visible=None):
    """An Onshape-style direction arrow button (instead of an "Opposite direction" tick box). inline: the key of
    the row it sits at the right of (the end type, the distance …); without one it gets a row of its own."""
    return {"t": "flip", "key": key, "label": label, "inline": inline, "visible": visible}


def _sel(key, label, want, multi=True, visible=None):
    return {"t": "sel", "key": key, "label": label, "want": want, "multi": multi, "visible": visible}


def _ops(options=None):
    return {"t": "ops", "key": "op", "options": options or OP_LABELS}


SURFACE_OPS = [("new", "New"), ("add", "Add")]
# features whose selection boxes are stored as they are: [[kind, ref], ...] per box
PAIR_FEATURES = ("measure_value", "thicken", "enclose", "rib", "delete_part", "helix", "bounding_box", "compound",
                 "explode_part", "fill", "constrained_surface")


def _segments(key, options, default=None):
    """A row of push buttons for a short choice (like the New / Add / Remove buttons)."""
    return {"t": "ops", "key": key, "options": options, "default": default}


class _SegmentRow(QWidget):
    """Button row with set_value, so code can change the choice like a combo box."""

    def set_value(self, value):
        for b in self.buttons:
            b.setChecked(b.property("value") == value)


def _creation_fields(verb):
    """Creation type (Solid / Surface / Thin) and the thin-wall settings — shared by Extrude, Revolve and Sweep
    (freeshape/body_options.py), so they look and behave the same in each."""
    from .. import body_options as BO
    thin = lambda p: p.get("creation") == "thin"
    curvy = lambda p: p.get("creation") in ("surface", "thin")
    return [
        _segments("creation", BO.CREATION_TYPES, "solid"),
        {**_ops(), "visible": lambda p: p.get("creation", "solid") != "surface"},
        {**_segments("surface_op", SURFACE_OPS, "new"), "visible": lambda p: p.get("creation", "solid") == "surface"},
        _sel("profiles", f"Faces and sketch regions to {verb}", {"region", "face"},
             visible=lambda p: p.get("creation", "solid") == "solid"),
        _check("separate", "Separate: one part per area",
               visible=lambda p: p.get("creation", "solid") == "solid"),
        _check("separate_colors", "Colour each part differently",
               visible=lambda p: p.get("creation", "solid") == "solid" and p.get("separate")),
        _sel("curves", "Sketch curves and edges", {"path"}, visible=curvy),
        _num("thickness", "Thickness", visible=thin, minimum=1e-6),
        _choice("thin_side", "Wall goes", BO.THIN_SIDES, visible=thin),
        _num("thickness2", "Second thickness (other side)", visible=lambda p: thin(p) and p.get("thin_side") == "two",
             minimum=0),
        _scope_field(lambda p: p.get("creation", "solid") != "surface"),
    ]


def _scope_field(also=lambda p: True):
    """Merge scope: which parts Add / Remove / Intersect act on (none picked: all of them). Shared by the
    features that merge a body into the model."""
    return _sel("scope_sel", "Merge scope — parts to act on (none: all)", {"part"},
                visible=lambda p: p.get("op", "new") != "new" and also(p))


def _mt(p):
    return p.get("type", "xyz")


def _connector_options(dlg):
    """The Part Studio's mate connectors (made before this feature) to choose from."""
    out = [("", "— choose —")]
    for fid in getattr(dlg.base_state, "connectors", {}):
        f = dlg.doc.feature(fid)
        out.append((fid, f["name"] if f else fid))
    return out


def _mc_info(p, dlg):
    if not p.get("on"):
        return "Pick where it goes. With nothing picked it sits at the origin."
    names = [c["name"] for c in dlg._mc_candidates()] if dlg is not None else []
    if not names:
        return ""
    k = int(p.get("point", 0) or 0) % len(names)
    more = " — press Shift for the next point" if len(names) > 1 else ""
    return f"At: {names[k]}  ({k + 1} of {len(names)}){more}"


def _pattern_of(what):
    """The 'Parts / Features' switch and feature picker shared by Mirror and the patterns."""
    return [
        _choice("type", f"What to {what}", [("parts", "Parts"), ("features", "Features"), ("faces", "Faces")]),
        _sel("features", "Features (click them in the feature list)", {"feature"},
             visible=lambda p: p.get("type") == "features"),
        _sel("faces", "Faces — all the faces of a boss, rib or pocket", {"face"},
             visible=lambda p: p.get("type") == "faces"),
    ]


def _mf():
    from .. import more_features
    return more_features


def _mv():
    from .. import measure_value
    return measure_value


def _creation_types():
    from .. import body_options as BO
    return BO.CREATION_TYPES


def _thin_sides():
    from .. import body_options as BO
    return BO.THIN_SIDES


def _second(p):
    return bool(p.get("second")) and p.get("end") not in ("symmetric", "through")


_UP_TO_FIELDS = [
    _sel("upto_face", "Face or plane to go up to", {"face", "plane"}, multi=False,
         visible=lambda p: p.get("end") == "up_to_face"),
    _sel("upto_part", "Part to go up to", {"part"}, multi=False, visible=lambda p: p.get("end") == "up_to_part"),
    _sel("upto_vertex", "Vertex to go up to", {"vertex"}, multi=False,
         visible=lambda p: p.get("end") == "up_to_vertex"),
]


SCHEMAS = {
    "sm_base": [
        _sel("profiles", "Sketch region(s) — the sheet's outline", {"region"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _num("radius", "Bend radius (inside)", minimum=0),
        _num("k", "K-factor (0.44 suits most steel / aluminium)", unit="", minimum=1e-6),
        _check("flip", "Thickness on the other side"),
        _check("relief", "Corner reliefs where two bends meet"),
        _num("relief_scale", "Relief size (× thickness)", unit="", visible=lambda p: p.get("relief", True),
             minimum=0.1),
    ],
    "sm_hem": [
        _sel("edges", "Edges to hem", {"edge"}),
        _num("length", "Hem length (to the outside of the fold)", minimum=1e-6),
        _check("down", "Fold the other way"),
        _check("own_radius", "Choose the fold radius (else half the thickness)"),
        _num("radius", "Fold radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0.01),
    ],
    "sm_jog": [
        _sel("edges", "Edges to jog from", {"edge"}),
        _num("offset", "Offset (the step)", minimum=1e-6),
        _num("length", "Length after the step (to the outside)", minimum=1e-6),
        _check("down", "Step the other way"),
        _check("own_radius", "Use a different bend radius"),
        _num("radius", "Bend radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0),
    ],
    "sm_tab": [
        _sel("profiles", "Sketch region(s) on a panel's face, touching it", {"region"}),
    ],
    "sm_convert": [
        _sel("faces", "One of the plate's big flat faces", {"face"}, multi=False),
        _num("radius", "Bend radius for flanges added later", minimum=0),
        _num("k", "K-factor (0.44 suits most steel / aluminium)", unit="", minimum=1e-6),
        _check("relief", "Corner reliefs where two bends meet"),
    ],
    "sm_flange": [
        _sel("edges", "Edges to bend a flange from", {"edge"}),
        _num("length", "Flange length (to the outside)", minimum=1e-6),
        _num("angle", "Bend angle", unit="deg", minimum=1e-6),
        _choice("position", "Where the bend goes", [("outside", "Outside the edge (the part grows)"),
                                                    ("inside", "Inside the edge (keeps the outline size)")]),
        _check("down", "Bend the other way"),
        _check("own_radius", "Use a different bend radius"),
        _num("radius", "Bend radius (inside)", visible=lambda p: p.get("own_radius"), minimum=0),
    ],
    "extrude": [
        *_creation_fields("extrude"),
        _choice("end", "End type", [("blind", "Blind"), ("symmetric", "Symmetric"), ("through", "Through all")]
                + F.UP_TO_LABELS),
        _num("depth", "Depth", visible=lambda p: p.get("end") not in ("through",) + F.UP_TO, minimum=1e-6),
        *_UP_TO_FIELDS,
        _num("offset", "Stop short by",
             visible=lambda p: p.get("end") in ("up_to_face", "up_to_vertex")),
        _flip("flip", "Opposite direction", inline="end", visible=lambda p: p.get("end") != "symmetric"),
        _check("second", "Second end position", visible=lambda p: p.get("end") not in ("symmetric", "through")),
        _choice("end2", "Second end type", [("blind", "Blind"), ("symmetric", "Symmetric (mirror of the first end)"),
                                            ("through", "Through all")] + F.UP_TO_LABELS,
                visible=_second),
        _num("depth2", "Second depth", visible=lambda p: _second(p) and p.get("end2", "blind") == "blind",
             minimum=1e-6),
        _sel("upto2_face", "Second end: face or plane to go up to", {"face", "plane"}, multi=False,
             visible=lambda p: _second(p) and p.get("end2") == "up_to_face"),
        _sel("upto2_part", "Second end: part to go up to", {"part"}, multi=False,
             visible=lambda p: _second(p) and p.get("end2") == "up_to_part"),
        _sel("upto2_vertex", "Second end: vertex to go up to", {"vertex"}, multi=False,
             visible=lambda p: _second(p) and p.get("end2") == "up_to_vertex"),
        _num("offset2", "Second end: stop short by",
             visible=lambda p: _second(p) and p.get("end2") in ("up_to_face", "up_to_vertex")),
        _check("both", "Both directions", visible=lambda p: p.get("end") == "through"),
        _check("draft", "Draft (taper the sides: + narrows, − widens)",
               visible=lambda p: p.get("creation", "solid") != "surface" and p.get("end") not in F.UP_TO),
        _num("draft_angle", "Draft angle", unit="deg",
             visible=lambda p: p.get("draft") and p.get("creation", "solid") != "surface"
             and p.get("end") not in F.UP_TO),
        _check("draft2", "Draft the second end",
               visible=lambda p: p.get("draft") and _second(p) and p.get("creation", "solid") != "surface"
               and p.get("end2", "blind") not in F.UP_TO + ("symmetric",)),
        _num("draft_angle2", "Second draft angle", unit="deg",
             visible=lambda p: p.get("draft") and p.get("draft2") and _second(p)
             and p.get("creation", "solid") != "surface" and p.get("end2", "blind") not in F.UP_TO + ("symmetric",)),
    ],
    "revolve": [
        *_creation_fields("revolve"),
        _sel("axis_sel", "Revolve axis (sketch line or edge)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _choice("end", "Revolve type", [("full", "Full"), ("one", "One direction"), ("symmetric", "Symmetric"),
                                        ("two", "Two directions")] + F.UP_TO_LABELS),
        _num("angle", "Revolve angle", unit="deg", visible=lambda p: p.get("end") in ("one", "symmetric", "two")),
        _num("angle2", "Second angle (other way)", unit="deg", visible=lambda p: p.get("end") == "two"),
        *_UP_TO_FIELDS,
        _num("offset", "Stop short by",
             visible=lambda p: p.get("end") == "up_to_face"),
        _check("mirror_end", "Symmetric (mirror it the other way too)", visible=lambda p: p.get("end") in F.UP_TO),
        _flip("flip", "Opposite direction", inline="end",
              visible=lambda p: p.get("end") in ("one", "two") or p.get("end") in F.UP_TO),
    ],
    "fillet": [
        _sel("edges", "Edges or faces to fillet", {"edge"}),
        _num("radius", "Radius", minimum=1e-6),
        _check("variable", "Variable radius (changes along the edge)"),
        _num("radius2", "Radius at the other end", visible=lambda p: p.get("variable"), minimum=1e-6),
    ],
    "chamfer": [
        _sel("edges", "Edges or faces to chamfer", {"edge"}),
        _choice("type", "Chamfer type", [("equal", "Equal distance"), ("two", "Two distances"),
                                         ("angle", "Distance and angle")]),
        _num("distance", "Distance", minimum=1e-6),
        _num("distance2", "Second distance", visible=lambda p: p.get("type") == "two", minimum=1e-6),
        _num("angle", "Angle", unit="deg", visible=lambda p: p.get("type") == "angle", minimum=1e-6),
        _check("flip", "Flip sides", visible=lambda p: p.get("type") in ("two", "angle")),
        _check("tangent", "Tangent propagation (carry on round smooth corners)"),
    ],
    "shell": [
        _sel("faces", "Faces to remove", {"face"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _check("outward", "Shell outside"),
    ],
    "delete_face": [
        _sel("faces", "Faces to delete", {"face"}),
        _choice("mode", "After deleting", [("heal", "Heal (extend the surrounding faces)"),
                                           ("cap", "Cap (close with a flat face)"),
                                           ("open", "Leave the void open")]),
        {"t": "info", "key": "_info", "text": lambda p: {
            "heal": "Removes fillets, chamfers, bosses and holes from an imported part.",
            "cap": "Covers the opening with a flat face.",
            "open": "The part becomes an open shell."}.get(p.get("mode", "heal"), "")},
    ],
    "move_face": [
        _sel("faces", "Flat faces to move", {"face"}),
        _num("distance", "Distance (outwards)"),
        _flip("flip", "Flip direction", inline="distance"),
    ],
    "offset_surface": [
        _sel("faces", "Faces to offset", {"face"}),
        _num("distance", "Distance (outwards)"),
        _flip("flip", "Flip direction", inline="distance"),
    ],
    "plane": [
        _choice("type", "Plane type", F.PLANE_TYPES),
        _sel("refs_sel", "Planes, faces, edges and vertices", {"plane", "face", "edge", "vertex"}),
        {"t": "info", "key": "_info", "text": lambda p: _plane_info(p)},
        _num("offset", "Offset distance", visible=lambda p: F.plane_type(p) == "offset"),
        _num("angle", "Angle", unit="deg", visible=lambda p: F.plane_type(p) == "line_angle"),
        _flip("flip", "Flip direction", inline="type", visible=lambda p: F.plane_type(p) is not None),
    ],
    "mirror": [
        *_pattern_of("mirror"),
        {**_ops(), "visible": lambda p: p.get("type", "parts") == "parts"},
        _scope_field(lambda p: p.get("type", "parts") == "parts"),
        _sel("parts", "Parts to mirror", {"part"}, visible=lambda p: p.get("type", "parts") == "parts"),
        _sel("plane_sel", "Mirror plane", {"plane"}, multi=False),
    ],
    "linear_pattern": [
        *_pattern_of("pattern"),
        {**_ops(), "visible": lambda p: p.get("type", "parts") == "parts"},
        _scope_field(lambda p: p.get("type", "parts") == "parts"),
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type", "parts") == "parts"),
        _sel("dir_sel", "Direction (edge or sketch line)", {"axis"}, multi=False),
        _choice("dir_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("spacing", "Distance"),
        _num("count", "Instance count", unit=""),
        _flip("flip", "Opposite direction", inline="spacing"),
        _check("second", "Second direction"),
        _sel("dir2_sel", "Second direction (edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: p.get("second")),
        _choice("dir2_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: p.get("second")),
        _num("spacing2", "Second distance", visible=lambda p: p.get("second")),
        _num("count2", "Second instance count", unit="", visible=lambda p: p.get("second")),
        _flip("flip2", "Opposite second direction", inline="spacing2", visible=lambda p: p.get("second")),
    ],
    "circular_pattern": [
        *_pattern_of("pattern"),
        {**_ops(), "visible": lambda p: p.get("type", "parts") == "parts"},
        _scope_field(lambda p: p.get("type", "parts") == "parts"),
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type", "parts") == "parts"),
        _sel("axis_sel", "Axis of pattern (edge or sketch line)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("angle", "Angle", unit="deg"),
        _num("count", "Instance count", unit=""),
    ],
    "curve_pattern": [
        *_pattern_of("pattern"),
        {**_ops(), "visible": lambda p: p.get("type", "parts") == "parts"},
        _scope_field(lambda p: p.get("type", "parts") == "parts"),
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type", "parts") == "parts"),
        _sel("path", "Path to pattern along (sketch curves or edges)", {"path"}),
        _num("count", "Instance count", unit=""),
        _choice("spacing_mode", "Spacing", [("fit", "Spread along the whole path"), ("distance", "Distance apart")]),
        _num("spacing", "Distance", visible=lambda p: p.get("spacing_mode") == "distance"),
        _flip("flip", "Start from the other end", inline="spacing_mode"),
        _check("keep_orientation", "Keep orientation (move only, don't turn with the path)"),
    ],
    "point_pattern": [
        *_pattern_of("pattern"),
        {**_ops(), "visible": lambda p: p.get("type", "parts") == "parts"},
        _scope_field(lambda p: p.get("type", "parts") == "parts"),
        _sel("parts", "Parts to pattern", {"part"}, visible=lambda p: p.get("type", "parts") == "parts"),
        _sel("locations", "Points to copy to (vertices or sketch points)", {"vertex"}),
        _sel("reference_sel", "Reference point (optional — else the middle of what's patterned)", {"vertex"},
             multi=False),
    ],
    "boolean": [
        {"t": "ops", "key": "op", "options": [("union", "Union"), ("subtract", "Subtract"), ("intersect", "Intersect")]},
        _sel("tools", "Tools", {"part"}),
        _sel("targets", "Targets", {"part"}, visible=lambda p: p.get("op") != "union"),
        _check("keep_tools", "Keep tools", visible=lambda p: p.get("op") != "union"),
    ],
    "sweep": [
        *_creation_fields("sweep"),
        _sel("path", "Sweep path (sketch curves or edges)", {"path"}),
        _choice("control", "Profile control", K.SWEEP_CONTROLS),
        _choice("lock_std", "Direction", [("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: p.get("control") == "lock_direction"),
        _sel("support_sel", "Faces the path runs on", {"face"}, visible=lambda p: p.get("control") == "lock_faces"),
        _sel("guide_sel", "Guide curve", {"path"}, multi=False, visible=lambda p: p.get("control") == "guide"),
    ],
    "loft": [
        _segments("creation", _creation_types(), "solid"),
        {**_ops(), "visible": lambda p: p.get("creation", "solid") != "surface"},
        {**_segments("surface_op", SURFACE_OPS, "new"), "visible": lambda p: p.get("creation", "solid") == "surface"},
        _scope_field(lambda p: p.get("creation", "solid") != "surface"),
        _sel("profiles", "Profiles — pick them in order (regions, faces or curves)", {"region", "face", "path"}),
        _num("thickness", "Thickness", visible=lambda p: p.get("creation") == "thin", minimum=1e-6),
        _choice("thin_side", "Wall goes", _thin_sides(), visible=lambda p: p.get("creation") == "thin"),
        _num("thickness2", "Second thickness (other side)",
             visible=lambda p: p.get("creation") == "thin" and p.get("thin_side") == "two", minimum=0),
        _check("ruled", "Straight sides (ruled)"),
        _sel("path", "Path to loft along (optional)", {"path"}),
    ],
    "draft": [
        _sel("faces", "Faces to draft", {"face"}),
        _sel("neutral_sel", "Neutral plane", {"plane"}, multi=False),
        _num("angle", "Draft angle", unit="deg"),
        _flip("flip", "Opposite direction", inline="angle"),
    ],
    "split": [
        _sel("parts", "Parts to split", {"part"}),
        _sel("tools", "Split with — planes, faces (flat or curved) or sketch curves", {"plane", "face", "path"}),
        {"t": "split_tools", "key": "_split_tools"},
        {"t": "info", "key": "_info", "text": lambda p: "" if F.split_tools(p) else
         "Pick one or more: each cuts right through. Untick “Keep both sides” to keep only the side its arrow "
         "points to."},
    ],
    "move": [
        _choice("type", "Type", F.MOVE_TYPES),
        _sel("parts", "Parts to transform", {"part"}),
        _num("dx", "Move X", visible=lambda p: _mt(p) == "xyz"),
        _num("dy", "Move Y", visible=lambda p: _mt(p) == "xyz"),
        _num("dz", "Move Z", visible=lambda p: _mt(p) == "xyz"),
        _sel("axis_sel", "Line, direction or axis (edge or sketch line) — a line moves it by its own length",
             {"axis"}, multi=False, visible=lambda p: _mt(p) in ("line", "distance", "rotate")
             or (_mt(p) == "xyz" and _isnum(p.get("rotate")) is not False)),
        _num("distance", "Distance", visible=lambda p: _mt(p) == "distance"),
        _sel("from_sel", "Point to move from (vertex or sketch point)", {"vertex"}, multi=False,
             visible=lambda p: _mt(p) == "points"),
        _sel("to_sel", "Point to move to", {"vertex"}, multi=False, visible=lambda p: _mt(p) == "points"),
        _num("rotate", "Rotate", unit="deg",
             visible=lambda p: _mt(p) == "rotate" or (_mt(p) == "xyz" and _isnum(p.get("rotate")) is not False)),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: _mt(p) in ("rotate", "distance")
                or (_mt(p) == "xyz" and _isnum(p.get("rotate")) is not False)),
        _choice("from_mc", "Mate connector to move from", _connector_options, visible=lambda p: _mt(p) == "mates"),
        _choice("to_mc", "Mate connector to move to", _connector_options, visible=lambda p: _mt(p) == "mates"),
        _num("scale", "Scale", unit="", visible=lambda p: _mt(p) == "scale", minimum=1e-9),
        _sel("about_sel", "About this point (none: the origin)", {"vertex"}, multi=False,
             visible=lambda p: _mt(p) == "scale"),
        _flip("flip", "Opposite direction", inline="type", visible=lambda p: _mt(p) in ("line", "distance", "rotate",
                                                                                         "mates")),
        _check("copy", "Keep the original (make a copy)", visible=lambda p: _mt(p) != "copy"),
    ],
    "mate_connector": [
        _sel("on_sel", "On a face, edge or vertex (none: the origin)", {"face", "edge", "vertex"}, multi=False),
        {"t": "info", "key": "_info", "text": lambda p, dlg=None: _mc_info(p, dlg), "dialog": True},
        {"t": "button", "key": "_next_point", "label": "Next point  (Shift)", "on": "_mc_next_point",
         "visible": lambda p: bool(p.get("on"))},
        _flip("flip", "Flip the Z axis", inline="_next_point"),
        _num("rotate", "Turn about Z", unit="deg"),
        _num("dx", "Move along X"),
        _num("dy", "Move along Y"),
        _num("dz", "Move along Z"),
    ],
    "thread": [
        _sel("faces", "Round faces to thread (holes or shafts)", {"face"}),
        _choice("size", "Size", [("auto", "Automatic (from the diameter)")] + [(x, x) for x in K.ISO_PITCH]
                + [("custom", "Custom")]),
        _num("major", "Major diameter", visible=lambda p: p.get("size") == "custom", minimum=1e-3),
        _num("pitch", "Pitch", visible=lambda p: p.get("size") == "custom", minimum=1e-3),
        _choice("length_mode", "Length", [("full", "Whole face"), ("length", "Set length")]),
        _num("length", "Thread length", visible=lambda p: p.get("length_mode") == "length", minimum=1e-3),
        _check("left_hand", "Left-hand thread"),
        _num("clearance", "Print clearance"),
        {"t": "info", "key": "_info", "text": lambda p: "Real ISO metric thread geometry. Holes get an internal "
                                                       "thread, shafts an external one. For 3D printing, add "
                                                       "about 0.1–0.2 mm clearance."},
    ],
    "thicken": [
        _sel("items", "Surfaces and faces to thicken", {"face", "part"}),
        _choice("side", "Thickness goes", _mf().THICKEN_SIDES),
        _num("thickness", "Thickness", minimum=1e-6),
        _num("thickness2", "Second thickness (other side)", visible=lambda p: p.get("side") == "two", minimum=0),
        _ops(),
        _scope_field(),
    ],
    "enclose": [
        _sel("items", "Surfaces, faces and planes that close off a space", {"face", "part", "plane"}),
        _check("keep_tools", "Keep the surfaces"),
        _ops(),
        _scope_field(),
    ],
    "fill": [
        _sel("edges", "Edges and sketch curves around the area (a closed loop)", {"path"}),
        _choice("continuity", "Meets the surfaces around it", _mf().FILL_CONTINUITY),
        _sel("points", "Points it must pass through (optional)", {"vertex"}),
        _segments("surface_op", SURFACE_OPS, "new"),
        {"t": "info", "key": "_info", "text": lambda p: "Tangent: edges of a surface body are met smoothly; sketch "
                                                       "curves and edges between two faces are just met."},
    ],
    "constrained_surface": [
        _sel("points", "Points it goes through", {"vertex"}),
        _sel("parts", "…or parts (all their vertices — a point cloud or scan)", {"part"}),
        _num("tolerance", "Tolerance (furthest a point may be)", minimum=1e-6),
        _segments("surface_op", SURFACE_OPS, "new"),
    ],
    "rib": [
        _sel("curves", "Sketch lines for the rib", {"path"}),
        _sel("parts", "Parts to grow out to (none: all)", {"part"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _choice("direction", "Rib grows", _mf().RIB_DIRECTIONS),
        _flip("flip", "Opposite direction", inline="direction"),
    ],
    "delete_part": [
        _sel("parts", "Parts to delete", {"part"}),
    ],
    "helix": [
        _sel("base", "Circle edge, cylinder or axis (optional)", {"edge", "face", "axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: not p.get("base")),
        _num("radius", "Radius", minimum=1e-6,
             visible=lambda p: not p.get("base") or p["base"][0][0] == "axis"),
        _choice("mode", "Size by", _mf().HELIX_MODES),
        _num("turns", "Turns", unit="", minimum=1e-6, visible=lambda p: p.get("mode", "turns_pitch") != "height_pitch"),
        _num("pitch", "Pitch (rise per turn)", minimum=1e-6,
             visible=lambda p: p.get("mode", "turns_pitch") != "height_turns"),
        _num("height", "Height", minimum=1e-6, visible=lambda p: p.get("mode", "turns_pitch") != "turns_pitch"),
        _num("start_angle", "Start angle", unit="deg"),
        _check("left_hand", "Left-handed (turns anticlockwise)"),
        _flip("flip", "Direction"),
    ],
    "compound": [
        _sel("parts", "Solid parts (none: all)", {"part"}),
        _check("glue", "Glue faces — touching solids share their faces"),
        _num("tolerance", "Glue tolerance (0 = exact)", visible=lambda p: p.get("glue", True), minimum=0),
        {"t": "info", "key": "_info", "text": lambda p: (
            "One part whose solids share the faces where they touch (a conformal mesh across them). "
            "Right-click the part ▸ Shape info to see the counts. Export as BREP to keep the shared faces "
            "(FreeShape's STEP export doesn't keep them yet).") if p.get("glue", True) else
            "One part holding the solids as they are; touching faces are not shared."},
    ],
    "explode_part": [
        _sel("parts", "Parts to explode", {"part"}),
        _choice("what", "Explode into", [("solid", "Solids"), ("face", "Faces (as surfaces)"),
                                         ("edge", "Edges (as curves)")]),
        _check("keep", "Keep the original part"),
        {"t": "info", "key": "_info", "text": lambda p: {
            "solid": "Each solid becomes a part of its own — e.g. a compound back into its solids.",
            "face": "Each face becomes a surface part.",
            "edge": "Each edge becomes a curve part."}.get(p.get("what", "solid"), "")},
    ],
    "bounding_box": [
        _sel("parts", "Parts to box (none: all)", {"part"}),
        _num("clearance", "Clearance all round", minimum=0),
    ],
    "measure_value": [
        {"t": "text", "key": "name", "label": "Name   #", "placeholder": "e.g. gap"},
        _choice("mtype", "Type", _mv().TYPES),
        {"t": "text", "key": "expr", "label": "Value", "placeholder": "e.g. 25, 1 m or #a * 2", "expr": True,
         "visible": lambda p: p.get("mtype") in _mv().FIXED},
        _choice("unit", "Unit", _mv().LENGTH_UNITS, visible=lambda p: p.get("mtype") == "fixed_length"),
        _choice("aunit", "Unit", _mv().ANGLE_UNITS, visible=lambda p: p.get("mtype") == "fixed_angle"),
        _sel("ray", "Ray (sketch line or edge from its start, round edge, cylinder)", {"axis", "face"},
             multi=False, visible=lambda p: p.get("mtype") == "ray"),
        _sel("entities", "Entities", {"vertex", "edge", "face", "part", "plane", "region", "sketch_line"},
             visible=lambda p: p.get("mtype") not in _mv().FIXED),
        _choice("method", "Measure method", _mv().METHODS, visible=lambda p: p.get("mtype", "distance") == "distance"),
        _choice("component", "Value", _mv().COMPONENTS, visible=lambda p: p.get("mtype", "distance") == "distance"),
        _num("ray_offset", "Ray origin offset", visible=lambda p: p.get("mtype") == "ray"),
        _flip("ray_flip", "Opposite direction", inline="ray_offset", visible=lambda p: p.get("mtype") == "ray"),
        _check("ray_closest", "Closest", visible=lambda p: p.get("mtype") == "ray"),
        _check("ray_box", "Box intersection",
               visible=lambda p: p.get("mtype") == "ray" and not p.get("ray_closest", True)),
        _num("ray_index", "Intersection index (negative: from the far end)", unit="",
             visible=lambda p: p.get("mtype") == "ray" and not p.get("ray_closest", True)),
        _check("flip_angle", "Flip angle", visible=lambda p: p.get("mtype") == "angle"),
        _choice("count_mode", "Count", _mv().COUNT_MODES, visible=lambda p: p.get("mtype") == "count"),
        _choice("coord_type", "Coordinate type", _mv().COORD_TYPES, visible=lambda p: p.get("mtype") == "coordinate"),
        _sel("frame", "Coordinate system (a plane) / direction", {"plane", "axis"}, multi=False,
             visible=lambda p: (p.get("mtype") == "coordinate" and p.get("coord_type", "global") != "global")
             or p.get("mtype") in ("size", "inertia")),
        _choice("axis_c", "Axis", _mv().AXIS_COMPONENTS,
                visible=lambda p: p.get("mtype") in ("centroid", "size", "inertia")
                or (p.get("mtype") == "coordinate" and p.get("coord_type", "global") != "along")),
        _num("density", "Density", unit="g/cm³", visible=lambda p: p.get("mtype") == "inertia", minimum=0),
        {**_num("round_to", "Round to a multiple of (0 = don't)", minimum=0,
                visible=lambda p: p.get("mtype") not in _mv().FIXED), "dims": "any"},
        _check("hide_preview", "Hide preview", visible=lambda p: p.get("mtype") not in _mv().FIXED),
        {"t": "info", "key": "_info", "text": lambda p: (
            "Use #" + str(p.get("name") or "name") + " in any box that takes a "
            + {"fixed_length": "length", "fixed_angle": "angle", "fixed_number": "number"}.get(p.get("mtype"), "value")
            + ". A bare number is in the unit chosen here.") if p.get("mtype") in ("fixed_length", "fixed_angle",
                                                                                   "fixed_number") else
            ("Any expression: a number, a quantity with units (2 * 3 mm), or text in quotes (\"Rev B\", "
             "\"PN-\" + #n) for sketch text.") if p.get("mtype") == "expression" else
            "Measured again on every rebuild, so whatever uses #" + str(p.get("name") or "name")
            + " follows the model."},
    ],
    "variable": [
        {"t": "text", "key": "name", "label": "Name   #", "placeholder": "e.g. wall"},
        {"t": "text", "key": "expr", "label": "Value", "placeholder": "e.g. 3 or #wall * 2", "expr": True},
    ],
    "hole": [
        {"t": "sketch_choice", "key": "sketch", "label": "Sketch with hole centres"},
        _choice("standard", "Standard", [("clearance", "ISO clearance"), ("tapped", "ISO tapped"),
                                         ("custom", "Custom size"), ("sketch", "Size of the sketch circles")]),
        _choice("size", "Size", [(x, x) for x in F.HOLE_SIZES],
                visible=lambda p: p.get("standard") not in ("custom", "sketch")),
        _choice("fit", "Fit", [("close", "Close"), ("normal", "Normal"), ("loose", "Loose")],
                visible=lambda p: p.get("standard") == "clearance"),
        _num("diameter", "Hole diameter", minimum=1e-6, visible=lambda p: p.get("standard") == "custom"),
        _choice("style", "Hole style", [("simple", "Simple"), ("counterbore", "Counterbore"),
                                        ("countersink", "Countersink")]),
        _num("counterbore_dia", "Counterbore diameter",
             visible=lambda p: p.get("style") == "counterbore" and p.get("standard") in ("custom", "sketch")),
        _num("counterbore_depth", "Counterbore depth",
             visible=lambda p: p.get("style") == "counterbore" and p.get("standard") in ("custom", "sketch")),
        _num("countersink_dia", "Countersink diameter",
             visible=lambda p: p.get("style") == "countersink" and p.get("standard") in ("custom", "sketch")),
        _check("model_thread", "Model the thread (real geometry — slower)", visible=lambda p: p.get("standard") == "tapped"),
        _num("thread_clearance", "Print clearance", visible=lambda p: p.get("standard") == "tapped" and p.get("model_thread")),
        _check("left_hand", "Left-hand thread", visible=lambda p: p.get("standard") == "tapped" and p.get("model_thread")),
        _choice("end", "Termination", [("through", "Through all"), ("blind", "Blind"), ("up_to_next", "Up to next"),
                                       ("up_to_face", "Up to face"), ("up_to_vertex", "Up to vertex")]),
        _num("depth", "Hole depth", visible=lambda p: p.get("end") == "blind"),
        _sel("upto_face", "Face or plane to go up to", {"face", "plane"}, multi=False,
             visible=lambda p: p.get("end") == "up_to_face"),
        _sel("upto_vertex", "Vertex to go up to", {"vertex"}, multi=False,
             visible=lambda p: p.get("end") == "up_to_vertex"),
        _num("offset", "Stop short by", visible=lambda p: p.get("end") in ("up_to_face", "up_to_vertex")),
        _check("tip", "Drill point (118°)", visible=lambda p: p.get("end") != "through"),
        {"t": "info", "key": "_info", "text": lambda p: _hole_info(p)},
    ],
}


def params_to_fields(ftype, p):
    """Selection-field contents for a feature's params."""
    f = {}
    if ftype.startswith("plugin:"):
        from .. import plugins
        for spec in plugins.dialog_schema(ftype):
            if spec["t"] == "sel":
                f[spec["key"]] = [tuple(it) for it in p.get(spec["key"]) or []]
        return f
    if ftype in PAIR_FEATURES:
        for spec in SCHEMAS.get(ftype, []):
            if spec["t"] == "sel" and spec["key"] != "scope_sel":
                f[spec["key"]] = [tuple(it) for it in p.get(spec["key"]) or []]
        f["scope_sel"] = [("part", i) for i in p.get("scope") or []]
    if ftype in ("mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
        f["features"] = [("feature", i) for i in p.get("features", [])]
        f["faces"] = [("face", r) for r in p.get("faces", []) or []]
    if ftype in ("extrude", "revolve", "hole"):
        for pre, key in (("upto", "up_to"), ("upto2", "up_to2")):
            u = p.get(key) or {}
            f[pre + "_face"] = [(u["kind"], u["ref"])] if u.get("kind") in ("face", "plane") else []
            f[pre + "_vertex"] = [("vertex", u["ref"])] if u.get("kind") == "vertex" else []
            f[pre + "_part"] = [("part", u["id"])] if u.get("kind") == "part" else []
    if ftype in ("extrude", "revolve", "sweep", "loft", "mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
        f["scope_sel"] = [("part", i) for i in p.get("scope") or []]
    if ftype in ("extrude", "revolve", "sweep"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])] + [("face", r) for r in p.get("faces", [])]
        f["curves"] = [("path", r) for r in p.get("curves", []) or []]
    if ftype in ("sm_base", "sm_tab"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])]
    if ftype in ("sm_flange", "sm_hem", "sm_jog"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype == "sm_convert":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype in ("sweep", "loft"):
        f["path"] = [("path", r) for r in p.get("path", []) or []]
    if ftype == "sweep":
        f["support_sel"] = [("face", r) for r in p.get("support", []) or []]
        f["guide_sel"] = [("path", r) for r in p.get("guide", []) or []]
    if ftype == "loft":
        f["profiles"] = [(it["kind"], it["ref"]) for it in p.get("profiles", [])]
    if ftype == "draft":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
        f["neutral_sel"] = [("plane", p["neutral"])] if p.get("neutral") else []
    if ftype in ("split", "move"):
        f["parts"] = [("part", i) for i in p.get("parts", [])]
    if ftype == "split":
        f["tools"] = [(t["kind"], t["ref"]) for t in F.split_tools(p)]
    if ftype == "move":
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
        for key, fk in (("from_point", "from_sel"), ("to_point", "to_sel"), ("about", "about_sel")):
            f[fk] = [("vertex", p[key])] if p.get(key) else []
    if ftype == "mate_connector":
        on = p.get("on") or {}
        f["on_sel"] = [(on["kind"], on["ref"])] if on.get("kind") else []
    if ftype == "revolve":
        ax = p.get("axis") or {}
        f["axis_sel"] = [("axis", ax)] if ax and ax.get("kind") != "std" else []
    if ftype in ("fillet", "chamfer"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype == "plane":
        f["refs_sel"] = [(k, r) for k, r in F.plane_items(p)]
    if ftype == "mirror":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "linear_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        d = p.get("direction") or {}
        f["dir_sel"] = [("axis", d)] if d and d.get("kind") != "std" else []
        d2 = p.get("direction2") or {}
        f["dir2_sel"] = [("axis", d2)] if d2 and d2.get("kind") != "std" else []
    if ftype in ("curve_pattern", "point_pattern"):
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        f["path"] = [("path", r) for r in p.get("path", []) or []]
        f["locations"] = [("vertex", r) for r in p.get("locations", []) or []]
        f["reference_sel"] = [("vertex", p["reference"])] if p.get("reference") else []
    if ftype == "circular_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "boolean":
        f["tools"] = [("part", i) for i in p.get("tools", [])]
        f["targets"] = [("part", i) for i in p.get("targets", [])]
    return f


def fields_to_params(ftype, fields, p):
    if ftype.startswith("plugin:"):
        from .. import plugins
        for spec in plugins.dialog_schema(ftype):
            if spec["t"] == "sel":
                p[spec["key"]] = [[k, r] for k, r in fields.get(spec["key"], [])]
        return
    if ftype in PAIR_FEATURES:
        for spec in SCHEMAS.get(ftype, []):
            if spec["t"] == "sel" and spec["key"] != "scope_sel":
                p[spec["key"]] = [[k, r] for k, r in fields.get(spec["key"], [])]
        if "scope_sel" in fields:
            p["scope"] = [r for k, r in fields.get("scope_sel", []) if k == "part"] or None
    if ftype in ("sm_base", "sm_tab"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
    if ftype in ("sm_flange", "sm_hem", "sm_jog"):
        p["edges"] = [r for k, r in fields.get("edges", []) if k == "edge"]
    if ftype == "sm_convert":
        p["faces"] = [r for k, r in fields.get("faces", []) if k == "face"]
    if ftype in ("mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
        p["features"] = [r for k, r in fields.get("features", []) if k == "feature"]
        p["faces"] = [r for k, r in fields.get("faces", []) if k == "face"]
    if ftype in ("extrude", "revolve", "hole"):
        for pre, endkey, key in (("upto", "end", "up_to"), ("upto2", "end2", "up_to2")):
            fk = {"up_to_face": pre + "_face", "up_to_vertex": pre + "_vertex",
                  "up_to_part": pre + "_part"}.get(p.get(endkey))
            sel = fields.get(fk, []) if fk else []
            if sel:
                k, r = sel[0]
                p[key] = {"kind": "part", "id": r} if k == "part" else {"kind": k, "ref": r}
            elif fk:
                p.pop(key, None)
    if ftype in ("extrude", "revolve", "sweep", "loft", "mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
        if "scope_sel" in fields:
            p["scope"] = [r for k, r in fields.get("scope_sel", []) if k == "part"] or None
    if ftype in ("extrude", "revolve", "sweep"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
        p["faces"] = [r for k, r in fields.get("profiles", []) if k == "face"]
        p["curves"] = [r for k, r in fields.get("curves", []) if k == "path"]
    if ftype in ("sweep", "loft"):
        p["path"] = [r for k, r in fields.get("path", [])]
    if ftype == "sweep":
        p["support"] = [r for k, r in fields.get("support_sel", [])]
        p["guide"] = [r for k, r in fields.get("guide_sel", [])]
    if ftype == "loft":
        p["profiles"] = [{"kind": k, "ref": r} for k, r in fields.get("profiles", [])]
    if ftype == "draft":
        p["faces"] = [r for k, r in fields.get("faces", [])]
        sel = fields.get("neutral_sel", [])
        p["neutral"] = sel[0][1] if sel else {"kind": "std", "name": "Top"}
    if ftype in ("split", "move"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "split":
        old = list(F.split_tools(p))
        tools = []
        for k, r in fields.get("tools", []):
            prev = next((t for t in old if t["kind"] == k and _same(t["ref"], r)), None)
            if prev is not None:
                old.remove(prev)
            tools.append({"kind": k, "ref": r, "both": prev.get("both", True) if prev else True,
                          "flip": prev.get("flip", False) if prev else False})
        p["tools"] = tools
        p.pop("plane", None)                      # an older split's single plane is now its first tool
        p.pop("keep", None)
    if ftype == "move":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        else:
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or ("X" if p.get("type") == "distance" else "Z")}
        for key, fk in (("from_point", "from_sel"), ("to_point", "to_sel"), ("about", "about_sel")):
            if fk in fields:
                s_ = fields.get(fk) or []
                p[key] = s_[0][1] if s_ else None
    if ftype == "mate_connector":
        sel = fields.get("on_sel", [])
        new_on = {"kind": sel[0][0], "ref": sel[0][1]} if sel else None
        if new_on != p.get("on"):
            p["point"] = 0                           # a new face: start at its first point
        p["on"] = new_on
    if ftype == "revolve":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        elif p.get("axis_std"):
            p["axis"] = {"kind": "std", "name": p["axis_std"]}
        else:
            p["axis"] = {}
    if ftype in ("fillet", "chamfer"):
        p["edges"] = [r for k, r in fields.get("edges", [])]
    if ftype in ("shell", "thread", "delete_face", "move_face", "offset_surface"):
        p["faces"] = [r for k, r in fields.get("faces", [])]
    if ftype == "plane":
        p["refs"] = [{"k": k, "ref": r} for k, r in fields.get("refs_sel", [])]
        p.pop("ref", None)
    if ftype in ("mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "mirror":
        sel = fields.get("plane_sel", [])
        p["plane"] = sel[0][1] if sel else {"kind": "std", "name": "Right"}
    if ftype == "linear_pattern":
        sel = fields.get("dir_sel", [])
        if sel:
            p["direction"] = sel[0][1]
            p["dir_std"] = ""
        else:
            p["direction"] = {"kind": "std", "name": p.get("dir_std") or "X"}
        sel2 = fields.get("dir2_sel", [])
        if sel2:
            p["direction2"] = sel2[0][1]
            p["dir2_std"] = ""
        else:
            p["direction2"] = {"kind": "std", "name": p.get("dir2_std") or "Y"}
    if ftype == "circular_pattern":
        sel = fields.get("axis_sel", [])
        if sel:
            p["axis"] = sel[0][1]
            p["axis_std"] = ""
        else:
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or "Z"}
    if ftype == "curve_pattern":
        p["path"] = [r for k, r in fields.get("path", [])]
    if ftype == "point_pattern":
        p["locations"] = [r for k, r in fields.get("locations", []) if k == "vertex"]
        sel = fields.get("reference_sel", [])
        p["reference"] = sel[0][1] if sel else None
    if ftype == "boolean":
        p["tools"] = [r for k, r in fields.get("tools", [])]
        p["targets"] = [r for k, r in fields.get("targets", [])]


# ---------------------------------------------------------------------- the dialog

class _ResizableFrame(QFrame):
    """A floating panel over the viewport that can be resized by dragging its right edge, bottom edge or
    bottom-right corner. Extra height goes to the selection lists (see adjustSize); the width is remembered
    for the next dialog."""
    GRIP = 9                    # px inside the edge that grabs the resize
    _remembered_width: dict = {}

    def _init_resizable(self, default_width, min_width=240):
        self._w = self._remembered_width.get(type(self).__name__, default_width)
        self._min_w = min_width
        self._user_h = 0
        self._rz = None
        self.setMouseTracking(True)
        self.setFixedWidth(self._w)

    def _fit(self):
        lay = self.layout()
        if lay is not None:
            lay.invalidate()
            lay.activate()
        QFrame.adjustSize(self)

    def adjustSize(self):
        fields = getattr(self, "fields", {})
        for f in fields.values():
            f.set_extra(0)
        self._fit()
        if self._user_h > self.height():
            vis = [f for f in fields.values() if f.isVisibleTo(self)]
            if vis:
                extra = (self._user_h - self.height()) // len(vis)
                for f in vis:
                    f.set_extra(extra)
                self._fit()

    def _grip_at(self, pos):
        return pos.x() >= self.width() - self.GRIP, pos.y() >= self.height() - self.GRIP

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            right, bottom = self._grip_at(e.position())
            if right or bottom:
                self._rz = (right, bottom, e.globalPosition(), self.width(), self.height())
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._rz is not None:
            right, bottom, g0, w0, h0 = self._rz
            d = e.globalPosition() - g0
            par = self.parentWidget()
            if right:
                maxw = max(self._min_w, (par.width() - self.x() - 12) if par else 4000)
                self._w = int(max(self._min_w, min(maxw, w0 + d.x())))
                self.setFixedWidth(self._w)
            if bottom:
                maxh = max(120, (par.height() - self.y() - 12) if par else 4000)
                self._user_h = int(max(0, min(maxh, h0 + d.y())))
            self.adjustSize()
            e.accept()
            return
        right, bottom = self._grip_at(e.position())
        self.setCursor(Qt.SizeFDiagCursor if (right and bottom) else Qt.SizeHorCursor if right
                       else Qt.SizeVerCursor if bottom else Qt.ArrowCursor)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._rz is not None:
            self._rz = None
            self._remembered_width[type(self).__name__] = self._w
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def leaveEvent(self, e):
        if self._rz is None:
            self.unsetCursor()
        super().leaveEvent(e)


class FeatureDialog(_ResizableFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, main, feature, is_new, index, preselection=()):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self._init_resizable(300)
        self.fields: dict = {}
        self.main = main
        self.doc = main.doc
        self.feature = copy.deepcopy(feature)
        self.ftype = feature["type"]
        self.is_new = is_new
        self.index = index
        self.base_state = self.doc.state_before(index)
        # settings added in later versions get their defaults when an older feature is edited
        try:
            from ..features import default_params
            for k_, v_ in default_params(self.ftype, self.base_state).items():
                self.feature["params"].setdefault(k_, v_)
        except Exception:
            pass
        self.schema = SCHEMAS.get(self.ftype, [])
        if self.ftype.startswith("plugin:"):
            from .. import plugins
            self.schema = plugins.dialog_schema(self.ftype)
        self.fields: dict[str, SelectionField] = {}
        self.widgets = {}
        self._field_errors = {}          # param key -> why the number typed in its box can't be used
        self._info_rows = []
        self.rows = []
        self.active_field = None
        if self.ftype == "mate_connector":                # Shift steps to the next point on the face
            QApplication.instance().installEventFilter(self)
        self._op_chosen = not is_new     # New / Add follow what the shape touches until you pick one yourself
        self._auto_busy = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._do_preview)
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(18)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        self._build()
        # initial field contents from params, then from the preselection
        for k, items in params_to_fields(self.ftype, self.feature["params"]).items():
            if k in self.fields:
                self.fields[k].items = list(items)
        if is_new and preselection:
            self._apply_preselection(preselection)
            if self.ftype == "measure_value":
                self._prefill_measure()
        if is_new and self.ftype == "measure_value":
            pr = self.feature["params"]
            if pr.get("mtype") == "fixed_length" and pr.get("unit", "mm") == "mm" and U.unit() != "mm":
                pr["unit"] = U.unit()                   # a new Length is in the document's unit
                self._show_value("unit", pr["unit"])
            base, n, taken = pr.get("name") or "v", 1, set(self.base_state.vars)
            while pr.get("name") in taken:                  # v, v2, v3 … — never overwrite a #variable
                n += 1
                pr["name"] = f"{base}{n}"
            self._show_value("name", pr["name"])
        if self.ftype in ("extrude", "revolve", "sweep") and \
                self.feature["params"].get("creation", "solid") != "solid":
            # a Thin / Surface feature (e.g. made in 2.1) whose regions sit in the hidden faces box: show them as
            # outlines in the curves box instead — the same geometry
            self._move_profiles_for("solid", self.feature["params"]["creation"])
            fields_to_params(self.ftype, {k: f.items for k, f in self.fields.items()}, self.feature["params"])
        def shown(f):                      # only boxes the current choices show (not hidden "Up to" / feature ones)
            spec = next((sp for sp, _w in self.rows if sp.get("key") == f.key), None)
            vis = spec.get("visible") if spec else None
            return vis is None or bool(vis(self.feature["params"]))
        cands = [f for f in self.fields.values() if shown(f)]
        first = next((f for f in cands if not f.items), None) or next(iter(cands), None)
        if first:
            self.activate(first)
        self._sync_visibility()
        self.schedule_preview()
        self.adjustSize()
        self.move(12, 12)
        self.show()

    # ------------------------------------------------------------------ ui
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon(self.ftype).pixmap(20, 20))
        head.addWidget(ic)
        self.title = QLineEdit(self.feature["name"])
        self.title.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                                 "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
        head.addWidget(self.title, 1)
        ok = dialog_button(QToolButton(), "ok")
        ok.setToolTip(tr("Accept (Enter)"))
        ok.clicked.connect(self.accept)
        cancel = dialog_button(QToolButton(), "cancel")
        cancel.setToolTip(tr("Cancel (Esc)"))
        cancel.clicked.connect(self.reject)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#e3e6ea;")
        lay.addWidget(line)
        p = self.feature["params"]
        self._inline = []                                 # (flip spec, its arrow button) sitting in another row
        inline = {s["inline"]: s for s in self.schema if s["t"] == "flip" and s.get("inline")}
        for spec in self.schema:
            if spec["t"] == "flip" and spec.get("inline"):
                continue                                  # made with the row it sits in
            w = self._make_row(spec, p)
            if w is not None:
                lay.addWidget(w)
                self.rows.append((spec, w))
                fs = inline.get(spec.get("key"))
                if fs is not None and w.layout() is not None:
                    btn = self._flip_button(fs, p)
                    w.layout().addWidget(btn)
                    self._inline.append((fs, btn))
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)
        # "Create #name = …": shown when a box uses a variable that doesn't exist yet (Enter does it too)
        self._create_offer = None
        self.create_btn = QToolButton()
        self.create_btn.setObjectName("createVariable")
        self.create_btn.setCursor(Qt.PointingHandCursor)
        self.create_btn.setStyleSheet("QToolButton#createVariable { color: #1f6fd1; font-weight: 600; border: 1px "
                                      "solid #9cc3f0; border-radius: 4px; padding: 3px 8px; background: #eef5fd; }")
        self.create_btn.clicked.connect(self._do_create)
        self.create_btn.hide()
        lay.addWidget(self.create_btn)

    def _make_row(self, spec, p):
        t = spec["t"]
        if t == "ops":
            w = _SegmentRow()
            w.buttons = []
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            grp = QButtonGroup(w)
            cur = p.get(spec["key"], spec.get("default"))
            for val, lab in spec["options"]:
                b = QToolButton()
                b.setObjectName("segment")
                b.setText(tr(lab))
                b.setProperty("value", val)
                b.setCheckable(True)
                b.setChecked(cur == val)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, v=val: self._set(spec["key"], v))
                grp.addButton(b)
                h.addWidget(b)
                w.buttons.append(b)
            if spec["key"] != "op":
                self.widgets[spec["key"]] = w
            return w
        if t == "split_tools":
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(2)
            self._split_box, self._split_sig = box, None
            return box
        if t == "button":
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            b = QPushButton(tr(spec["label"]))
            b.clicked.connect(lambda _=False, fn=spec["on"]: getattr(self, fn)())
            h.addWidget(b)
            h.addStretch(1)
            self.widgets[spec["key"]] = b
            return row
        if t == "info":
            lab = QLabel("")
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.widgets[spec["key"]] = lab
            self._info_rows.append((spec, lab))
            return lab
        if t == "sel":
            f = SelectionField(spec["key"], tr(spec["label"]), spec["want"], spec["multi"])
            f.activated.connect(self.activate)
            f.changed.connect(self._fields_changed)
            self.fields[spec["key"]] = f
            return f
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if t == "flip":                                   # an arrow button on a row of its own
            lab = QLabel(tr(spec["label"]))
            lab.setStyleSheet("color:#4b5563;")
            h.addWidget(lab, 1)
            h.addWidget(self._flip_button(spec, p))
            return row
        if t == "check":
            cb = QCheckBox(tr(spec["label"]))
            cb.setChecked(bool(p.get(spec["key"])))
            cb.toggled.connect(lambda v, k=spec["key"]: self._set(k, v))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        lab = QLabel(tr(spec["label"]))
        lab.setStyleSheet("color:#4b5563;")
        h.addWidget(lab, 1)
        if t == "num":
            unit = spec["unit"]
            if unit == "mm" and not spec.get("dims"):      # a length: shown and typed in the document's unit
                self.__dict__.setdefault("_len_keys", set()).add(spec["key"])
                unit = U.unit()
            ed = QLineEdit(self._num_text(spec["key"], p.get(spec["key"], 0)))
            ed.setFixedWidth(110)
            ed.setAlignment(Qt.AlignRight)
            if unit:
                ed.setPlaceholderText(unit)
                ed.setToolTip(f"Value in {unit}. Expressions, units and variables work: '25/2', '1 in', '25 mm', "
                              "'#wall * 2'.")

            want = spec.get("dims") or ("length" if spec["key"] in self.__dict__.get("_len_keys", ()) else
                                        {"deg": "angle", "": "number"}.get(unit, "any"))

            def done(ed=ed, k=spec["key"], unit=unit, mn=spec.get("min"), whole=bool(spec.get("int")),
                     label=spec.get("label", ""), want=want):
                txt = ed.text()
                if k in self.__dict__.get("_len_keys", ()):     # bare numbers are in the document's unit
                    txt = with_unit(txt, U.unit(), getattr(self.base_state, "var_kinds", {}))
                why = self._number_problem(txt, want, unit, mn)
                ed.setProperty("invalid", "true" if why else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                ed.setToolTip(why or "")
                if why:
                    # keep what was typed and say why — never quietly fall back to the old value
                    self._field_errors[k] = f"{label.split('(')[0].strip()}: {why}"
                    self._show_field_errors()
                    # an unknown #name: offer to make it, just before this feature, with the value the box had
                    from .varcomplete import missing_variable
                    name = missing_variable(txt, self.base_state.vars)
                    kind = want if want in ("length", "angle", "number") else "any"
                    self._offer_create(k, name, kind, ed, done) if name else self._offer_create(k, None)
                    return
                self._field_errors.pop(k, None)
                self._offer_create(k, None)
                v = parse_number(txt, angle=(unit == "deg"), variables=self.base_state.vars)
                if has_vars(txt):
                    self._set(k, txt.strip())        # keep the expression so it follows the variable
                else:
                    self._set(k, int(round(v)) if k == "count" or whole else v)
            self.widgets[spec["key"]] = ed
            from .varcomplete import attach
            attach(ed, lambda: self.base_state.vars, lambda: getattr(self.base_state, "var_kinds", {}))
            ed.textEdited.connect(lambda _t, f=done: f())
            ed.editingFinished.connect(
                lambda ed=ed, k=spec["key"]: None if k in self._field_errors
                else ed.setText(self._num_text(k, self.feature["params"].get(k, 0))))
            h.addWidget(ed)
            if unit:
                u = QLabel(unit)
                u.setStyleSheet("color:#8a94a3;")
                u.setFixedWidth(24)
                h.addWidget(u)
            return row
        if t == "text":
            ed = QLineEdit(str(p.get(spec["key"], "")))
            ed.setPlaceholderText(tr(spec.get("placeholder", "")))
            ed.setFixedWidth(150)
            self.widgets[spec["key"]] = ed
            if spec.get("expr"):
                from .varcomplete import attach
                attach(ed, lambda: self.base_state.vars, lambda: getattr(self.base_state, "var_kinds", {}))

            def text_done(_t=None, ed=ed, k=spec["key"], is_expr=spec.get("expr")):
                txt = ed.text().strip()
                bad = False
                if is_expr:
                    bad = parse_number(txt, variables=self.base_state.vars) is None
                ed.setProperty("invalid", "true" if bad else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                self._set(k, txt)
            ed.textEdited.connect(text_done)
            h.addWidget(ed)
            return row
        if t == "choice":
            cb = QComboBox()
            opts = spec["options"](self) if callable(spec["options"]) else spec["options"]
            for val, lab2 in opts:
                cb.addItem(tr(lab2), val)
            idx = max(0, cb.findData(p.get(spec["key"], opts[0][0])))
            cb.setCurrentIndex(idx)
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._set(k, cb.itemData(i)))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        if t == "sketch_choice":
            cb = QComboBox()
            cb.addItem(tr("— select —"), None)
            for fid, sr in self.base_state.sketches.items():
                f = self.doc.feature(fid)
                if f and any(e["type"] in ("point", "circle") for e in sr.sketch.entities.values()):
                    cb.addItem(f["name"], fid)
            if p.get(spec["key"]) is None and cb.count() > 1:
                p[spec["key"]] = cb.itemData(cb.count() - 1)
                self._default_hole_size(p)
            cb.setCurrentIndex(max(0, cb.findData(p.get(spec["key"]))))
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._choose_sketch(cb.itemData(i)))
            cb.setToolTip("Holes go at this sketch's points and circle centres. You can also just click the sketch.")
            self._sketch_cb = cb
            h.addWidget(cb)
            return row
        return None

    def _flip_button(self, spec, p):
        """The direction arrow: points up normally, down when flipped. Click it (or the arrow in the view)."""
        b = QToolButton()
        b.setObjectName("flipButton")
        b.setCheckable(True)
        b.setAutoRaise(True)
        b.setIconSize(QSize(20, 20))
        b.setChecked(bool(p.get(spec["key"])))
        b.setProperty("flip_label", tr(spec["label"]))
        _flip_look(b)
        b.toggled.connect(lambda v, b=b, k=spec["key"]: (_flip_look(b), self._set(k, v)))
        self.widgets[spec["key"]] = b
        return b

    def _split_rows(self):
        """One row per Split tool: its name, Keep both sides, and Flip (which side stays) when not."""
        box = getattr(self, "_split_box", None)
        if box is None:
            return
        tools = F.split_tools(self.feature["params"])
        sig = tuple((t["kind"], bool(t.get("both", True)), json.dumps(t["ref"], sort_keys=True, default=str))
                    for t in tools)
        if sig == self._split_sig:
            return
        self._split_sig = sig
        lay = box.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for i, t in enumerate(tools):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(4, 0, 0, 0)
            name = QLabel(f"{i + 1}. {item_label(self.doc, (t['kind'], t['ref']))}")
            name.setStyleSheet("color:#4b5563;")
            h.addWidget(name, 1)
            cb = QCheckBox(tr("Keep both sides"))
            cb.setChecked(bool(t.get("both", True)))
            cb.toggled.connect(lambda on, i=i: self._split_set(i, "both", on))
            h.addWidget(cb)
            fl = QToolButton()
            fl.setText(tr("⇅ Flip"))
            fl.setToolTip(tr("Keep the other side (or drag the arrow across)"))
            fl.setVisible(not t.get("both", True))
            fl.clicked.connect(lambda _=False, i=i: self._split_set(i, "flip", None))
            h.addWidget(fl)
            lay.addWidget(row)

    def _split_set(self, i, key, value):
        tools = F.split_tools(self.feature["params"])
        if i >= len(tools):
            return
        tools = [dict(t) for t in tools]
        tools[i][key] = (not tools[i].get("flip")) if key == "flip" else bool(value)
        p = self.feature["params"]
        p["tools"] = tools
        p.pop("plane", None)
        p.pop("keep", None)
        QTimer.singleShot(0, self._sync_visibility)
        self.schedule_preview()

    def _sync_visibility(self):
        p = self.feature["params"]
        if self.ftype == "split":
            self._split_rows()
        for spec, lab in self._info_rows:
            txt = spec["text"](p, self) if spec.get("dialog") else spec["text"](p)
            lab.setText(tr(txt))
            w_ = max(min(lab.width() if lab.width() > 50 else 10 ** 6, self.width() - 24), 200)
            lab.setMinimumHeight(lab.heightForWidth(w_) if txt else 0)     # wrapped text gets all its lines
            if spec["key"] == "_info":           # warnings in red, hints in green
                lab.setStyleSheet("color:#d93025; font-weight:600;" if txt.startswith("⚠")
                                  else "color:#2e9e44; font-weight:600;")
        for spec, w in self.rows + getattr(self, "_inline", []):
            vis = spec.get("visible")
            w.setVisible(True if vis is None else bool(vis(p)))
        for f in self.fields.values():
            f.refresh(self.doc)
        self.adjustSize()

    # ------------------------------------------------------------------ variables made on the fly
    def _offer_create(self, key, name, kind=None, ed=None, redo=None):
        """Show (or, with name None, hide) the 'Create #name' button for the box `key`."""
        if name is None:
            if self._create_offer and self._create_offer["key"] == key:
                self._create_offer = None
                self.create_btn.hide()
                self.adjustSize()
            return
        value = self.feature["params"].get(key)
        if value is None or (not isinstance(value, str) and kind == "length" and float(value) <= 0):
            value = 10.0 if kind != "angle" else 90.0
        if isinstance(value, (int, float)):
            shown = U.length(float(value)) if kind == "length" else (f"{fmt(float(value))}°" if kind == "angle"
                                                                       else fmt(float(value)))
        else:
            shown = str(value)
        self._create_offer = {"key": key, "name": name, "kind": kind, "value": value, "redo": redo, "ed": ed}
        self.create_btn.setText(f"+  Create #{name} = {shown}   (Enter)")
        self.create_btn.setToolTip(f"Add a Variable #{name} just before this feature, set to {shown} (what this "
                                   "box had) — change its value later in the feature list")
        self.create_btn.show()
        self.adjustSize()

    def _do_create(self):
        """Make the offered variable before this feature, then use it in the box."""
        offer, self._create_offer = self._create_offer, None
        self.create_btn.hide()
        if not offer:
            return
        self.main.create_variable_before(self.index, offer["name"], offer["kind"], offer["value"])
        self.index += 1                                  # this feature now comes one later
        self.base_state = self.doc.state_before(self.index)
        if offer.get("redo"):
            offer["redo"]()                              # the box's text is fine now: use it
        self.schedule_preview()
        if offer.get("ed") is not None:
            offer["ed"].setFocus()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and self._create_offer is not None:
            self._do_create()                            # Enter on "#newname": make it, don't accept yet
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if e.modifiers() & Qt.ShiftModifier and self.is_new:
                # Shift+Enter, as Onshape: keep this one and start another of the same kind straight away
                main, ftype = self.main, self.ftype
                self.accept()
                QTimer.singleShot(0, lambda: main.start_feature(ftype))
            else:
                self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ selection
    def activate(self, field):
        prev = self.active_field
        if self.active_field is not None:
            self.active_field.set_active(False)
        self.active_field = field
        field.set_active(True)
        want = field.want
        kinds = set()
        if "region" in want:
            kinds |= {"region", "sketch_line"}
        if "face" in want or "plane" in want:
            kinds.add("face")
        if "edge" in want:
            kinds |= {"edge", "face"}
        if "axis" in want or "path" in want:
            kinds |= {"edge", "sketch_line"}
        if "plane" in want:
            kinds.add("plane")
        if "part" in want:
            kinds.add("part")
        if "vertex" in want:
            kinds |= {"vertex", "sketch_point"}
        if "sketch_line" in want:
            kinds.add("sketch_line")
        if "outline" in want:
            kinds |= {"region", "face"}
        self.main.viewport.select_filter = kinds
        for f in self.fields.values():
            f.refresh(self.doc)
        if prev is not field and any(k == "part" for f in self.fields.values() for k, _r in f.items):
            self.schedule_preview()                  # part highlights follow the active box
        self.main.viewport.update()

    def _default_hole_size(self, p):
        """A new hole on a sketch that has circles but no points: take the circles' size — what you drew."""
        if not self.is_new or self.ftype != "hole":
            return
        sr = self.base_state.sketches.get(p.get("sketch"))
        if sr is None:
            return
        _c, sizes = F.hole_centres(sr.sketch)
        if sizes and all(sizes):
            p["standard"] = "sketch"

    def _choose_sketch(self, fid):
        p = self.feature["params"]
        p["sketch"] = fid
        self._default_hole_size(p)
        w = self.widgets.get("standard") if hasattr(self, "widgets") else None
        if w is not None and hasattr(w, "findData"):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(p.get("standard"))))
            w.blockSignals(False)
        self._sync_visibility()
        self.schedule_preview()

    def _pick_sketch(self, pick):
        """Clicking a sketch (its region or a curve) picks it for features that take a whole sketch."""
        cb = getattr(self, "_sketch_cb", None)
        if cb is None or pick.kind not in ("region", "sketch_line"):
            return False
        i = cb.findData(pick.key[0])
        if i < 0:
            return False
        cb.setCurrentIndex(i)          # -> _choose_sketch
        return True

    def handle_pick(self, pick: Pick):
        """Called by the main window when the user clicks in the viewport."""
        if self._pick_sketch(pick):
            return
        if pick.kind == "overlay":
            key, i = pick.key
            if key == "_mc_point":                       # a click on one of the mate connector's places: go there
                self.feature["params"]["point"] = i
                self._sync_visibility()
                self.schedule_preview()
                return
            fld = self.fields.get(key)
            if fld and i < len(fld.items):
                fld.items.pop(i)
                self._fields_changed()
            return
        fld = self.active_field
        if fld is not None and not self._field_shown(fld):
            fld = self._first_visible_field()            # (e.g. Surface chosen while the faces box was active)
            if fld is not None:
                self.activate(fld)
        if fld is None:
            return
        item = pick_to_item(self.main.viewport, pick, fld.want)
        if item is None:
            if pick.kind == "sketch_line" and "region" in fld.want:
                try:
                    self.main.status.showMessage(
                        "That sketch isn't a closed shape, so it has no area to use. Close its outline "
                        "(the inside turns blue) and pick it again.", 8000)
                except Exception:
                    pass
            return
        fld.toggle(item)

    def _field_shown(self, f):
        """Does the dialog show this selection box for the current choices?"""
        spec = next((sp for sp, _w in self.rows if sp.get("key") == f.key), None)
        vis = spec.get("visible") if spec else None
        return vis is None or bool(vis(self.feature["params"]))

    def _first_visible_field(self):
        return next((f for f in self.fields.values() if self._field_shown(f)), None)

    def _apply_preselection(self, picks):
        for pk in picks:
            if self._pick_sketch(pk):
                break
        used = set()
        fields = self.fields
        if (self.ftype in ("extrude", "revolve", "sweep") and "curves" in fields and picks
                and all(pk.kind in ("edge", "sketch_line") for pk in picks)
                and self.feature["params"].get("creation", "solid") == "solid"):
            # only edges / sketch curves picked: they are the curves of a surface, not region boundaries
            fields = {"curves": fields["curves"]}
        for key, fld in fields.items():
            for i, pk in enumerate(picks):
                if i in used or (not fld.multi and fld.items):
                    continue
                item = pick_to_item(self.main.viewport, pk, fld.want)
                if item is None:
                    continue
                used.add(i)
                fld.toggle(item)
        self.main.viewport.clear_selection()
        prof, curves = self.fields.get("profiles"), self.fields.get("curves")
        if curves is not None and self.ftype in ("extrude", "revolve", "sweep"):
            self._exact_sketch_curves(picks, curves)
        if (self.ftype in ("extrude", "revolve", "sweep") and prof is not None and curves is not None
                and not prof.items and curves.items
                and self.feature["params"].get("creation", "solid") == "solid"):
            # edges or sketch curves picked before opening: they make a surface (like Onshape)
            self._set_creation_widget("surface")
            self._set("creation", "surface")

    def _exact_sketch_curves(self, picks, curves):
        """Sketch lines picked before opening the feature mean exactly those lines (a click in the dialog
        takes the whole chain): group them into connected runs, one curve item per run."""
        from .. import sketch_ops
        lines = {}
        for pk in picks:
            if pk.kind == "sketch_line":
                lines.setdefault(pk.key[0], []).append(pk.key[1])
        if not lines:
            return
        keep = [(k, r) for k, r in curves.items if not (r.get("kind") == "sketch_chain" and r.get("sketch") in lines)]
        for fid, eids in lines.items():
            sr = self.base_state.sketches.get(fid)
            if sr is None:
                continue
            parent = {e: e for e in eids}

            def root(e):
                while parent[e] != e:
                    e = parent[e]
                return e
            ends = {e: sketch_ops._ends(sr.sketch.entities[e]) if e in sr.sketch.entities else None for e in eids}
            for i, a in enumerate(eids):
                for b in eids[i + 1:]:
                    if ends[a] and ends[b] and set(ends[a]) & set(ends[b]):
                        parent[root(b)] = root(a)
            groups = {}
            for e in eids:
                groups.setdefault(root(e), []).append(e)
            for g in groups.values():
                keep.append(("path", {"kind": "sketch_chain", "sketch": fid, "entity": g[0], "only": g}))
        curves.items = keep
        curves.refresh(self.doc)
        fields_to_params(self.ftype, {k: f.items for k, f in self.fields.items()}, self.feature["params"])

    def _prefill_measure(self):
        """Measure value opened with a selection: choose what to measure from what was picked."""
        from .. import measure_value as MV
        p = self.feature["params"]
        items = self.fields["entities"].items
        if not items:
            return
        pre = MV.prefill(items, self.base_state)
        taken = set(self.base_state.vars)
        base, n = pre["name"], 1
        while pre["name"] in taken:                     # don't overwrite an existing #variable by accident
            n += 1
            pre["name"] = f"{base}{n}"
        for k in ("name", "mtype", "method", "component", "axis_c"):
            p[k] = pre[k]
            self._show_value(k, pre[k])
        self._sync_visibility()

    def _show_value(self, key, value):
        w = self.widgets.get(key)
        if isinstance(w, QComboBox):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.blockSignals(False)
        elif isinstance(w, QLineEdit):
            w.setText(str(value))

    def _set_creation_widget(self, value):
        w = self.widgets.get("creation") if hasattr(self, "widgets") else None
        if w is None:
            return
        if hasattr(w, "findData"):
            w.blockSignals(True)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.blockSignals(False)
        elif hasattr(w, "set_value"):
            w.set_value(value)

    def _fields_changed(self):
        if self._auto_busy:
            return
        if self.sender() is self.fields.get("scope_sel"):
            self._op_chosen = True                       # you set the merge scope yourself: leave it alone
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, self.feature["params"])
        self._auto_op()
        snd = self.sender()
        for which, keys in ((1, ("upto_face", "upto_vertex", "upto_part")),
                            (2, ("upto2_face", "upto2_vertex", "upto2_part"))):
            if any(snd is self.fields.get(k) for k in keys):
                self._auto_direction(which)                # a face / vertex / part to go up to: turn towards it
        self._sync_visibility()
        self.schedule_preview()

    def _auto_direction(self, which=1):
        """Like Onshape: for an "Up to …" end, point the arrow the way that reaches what it goes up to (for the
        first end and, unlike Onshape, the second too). You can still flip it back with the arrow."""
        if self.ftype not in ("extrude", "revolve"):
            return
        try:
            pp = F.prepare(self.feature, self.base_state)["params"]
        except K.KernelError:
            return
        side = F.up_to_side(self.base_state, self.ftype, pp, which)
        if not side:
            return
        p = self.feature["params"]
        cur = -1 if p.get("flip") else 1
        if which == 1:
            want = side != cur
        else:
            # the second end runs opposite the first: flip both if its target is on the first end's side,
            # unless that would turn the first end away from its own target
            first = F.up_to_side(self.base_state, self.ftype, pp, 1) if pp.get("end") in F.UP_TO else 0
            want = side == cur and first != cur
        if want:
            self._toggle_flip()

    # ------------------------------------------------------------------ mate connectors
    def _mc_candidates(self):
        """The points the mate connector being edited can go at (cached per picked face / edge / vertex)."""
        on = self.feature["params"].get("on")
        if not on:
            return []
        key = json.dumps(on, sort_keys=True, default=str)
        cache = self.__dict__.setdefault("_mc_cache", {})
        if key not in cache:
            from .. import mate_connectors as MC
            try:
                cache[key] = MC.candidates(self.base_state, on)
            except (K.KernelError, KeyError, IndexError):
                cache[key] = []
        return cache[key]

    def _mc_next_point(self):
        """Shift (or Next point): the next place on the same face / edge for the mate connector."""
        n = len(self._mc_candidates())
        if n < 2:
            return
        p = self.feature["params"]
        p["point"] = (int(p.get("point", 0) or 0) + 1) % n
        self._sync_visibility()
        self.schedule_preview()

    def eventFilter(self, obj, ev):
        # Onshape moves a mate connector between the points of a face with Shift: a tap of Shift (not while
        # typing in a box) steps to the next one
        if self.ftype == "mate_connector" and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Shift \
                and not ev.isAutoRepeat():
            fw = QApplication.focusWidget()
            if not isinstance(fw, (QLineEdit, QComboBox)) and not self.__dict__.get("_shift_seen"):
                # the same key press reaches every parent widget in turn: count it once
                self._shift_seen = True
                QTimer.singleShot(0, lambda: self.__dict__.update(_shift_seen=False))
                self._mc_next_point()
        return super().eventFilter(obj, ev)

    def _toggle_flip(self, key="flip"):
        """Flip the direction (the arrow button, or a click on the arrow in the view)."""
        w = self.widgets.get(key)
        if isinstance(w, QToolButton) and w.isCheckable():
            w.setChecked(not w.isChecked())              # its toggled signal sets the param and previews
        else:
            self._set(key, not self.feature["params"].get(key, False))

    def _show_op(self, op):
        """Show `op` as the chosen New / Add / Remove / Intersect button."""
        for spec, w in self.rows:
            if spec.get("t") == "ops" and spec.get("key") == "op":
                for b in getattr(w, "buttons", []):
                    b.setChecked(b.property("value") == op)

    AUTO_OP_TYPES = ("extrude", "revolve", "sweep")

    def _auto_op(self):
        """For a new Extrude / Revolve / Sweep, like Onshape: New when the shape touches no part, otherwise Add
        with the parts it touches as the merge scope. Stops once you choose New/Add/Remove/… or a scope."""
        from .. import body_options as BO
        if self._op_chosen or self.ftype not in self.AUTO_OP_TYPES or "scope_sel" not in self.fields:
            return
        p = self.feature["params"]
        if BO.creation(p) == "surface" or p.get("op", "new") not in ("new", "add"):
            return
        parts = {pid: pt for pid, pt in self.base_state.parts.items() if K.faces(pt.shape)}
        key = json.dumps({k: v for k, v in p.items() if k not in ("op", "scope")}, sort_keys=True, default=str)
        if key == getattr(self, "_auto_key", None):
            return                                       # nothing that moves the shape has changed
        touching = []
        if parts:
            tools = self._tool_shapes()
            if tools is None:
                return                                   # nothing to extrude yet (or it fails): leave as is
            touching = [pid for pid, pt in parts.items() if any(K.touches_or_intersects(pt.shape, t) for t in tools)]
        op = "add" if touching else "new"
        self._auto_key = key
        self._auto_busy = True
        try:
            p["op"] = op
            p["scope"] = touching or None
            fld = self.fields["scope_sel"]
            fld.items = [("part", pid) for pid in touching]
            fld.refresh(self.doc)
            for spec, w in self.rows:
                if spec.get("t") == "ops" and spec.get("key") == "op":
                    for b in getattr(w, "buttons", []):
                        b.setChecked(b.property("value") == op)
        finally:
            self._auto_busy = False

    def _tool_shapes(self):
        """The solids this feature would make on its own (as New), or None if it can't be made yet."""
        st = copy.copy(self.base_state)
        st.parts = dict(self.base_state.parts)
        before = set(st.parts)
        f = copy.deepcopy(self.feature)
        f["params"]["op"] = "new"
        f["params"].pop("scope", None)
        try:
            f = F.prepare(f, self.base_state)
            F.RUNNERS[self.ftype](st, f)
        except Exception:
            return None
        made = [pt.shape for pid, pt in st.parts.items() if pid not in before]
        return made or None

    def _move_profiles_for(self, old, new):
        """Switching Solid <-> Thin / Surface keeps the selection: each face or region becomes its edges, one item
        each, in the curves box (so you can take one away: a square's four edges, less one, make a C), and back
        again. The box for the chosen type is always the active one, so a click can't land in a hidden box."""
        prof, curves = self.fields.get("profiles"), self.fields.get("curves")
        if prof is None or curves is None:
            return
        if (old == "solid") != (new == "solid"):
            if new != "solid":
                add = []
                for k, r in prof.items:
                    add += self._edge_items(k, r)
                curves.items = list(curves.items) + add
                prof.items = []
            else:
                back, groups, legacy = [], {}, []
                for k, r in curves.items:
                    src = r.get("from")
                    if r.get("kind") == "outline":
                        legacy.append((r["of"], r["ref"]))
                    elif src:
                        groups.setdefault(json.dumps(src, sort_keys=True), []).append(r)
                    else:
                        back.append((k, r))
                restored, dropped = list(legacy), 0
                for key, rs in groups.items():
                    src = json.loads(key)
                    if len(rs) >= src.get("n", 0):
                        restored.append((src["of"], src["ref"]))      # all its edges still there: the area again
                    else:
                        dropped += 1
                curves.items = back
                prof.items = prof.items + restored
                if dropped:
                    try:
                        self.main.status.showMessage(
                            f"{dropped} area(s) had edges taken away, so they can't make a solid — pick the "
                            "faces or regions again", 8000)
                    except Exception:
                        pass
            for f in (prof, curves):
                f.refresh(self.doc)
        target = curves if new != "solid" else prof
        QTimer.singleShot(0, lambda t=target: self.activate(t))

    def _edge_items(self, kind, ref):
        """A region or face as curve items, one per edge of its boundary (holes included), each remembering
        where it came from. Falls back to one 'outline' item if the edges can't be listed."""
        from .. import body_options as BO
        src = {"of": kind, "ref": copy.deepcopy(ref)}
        out = []
        try:
            if kind == "region":
                face = F.resolve_regions(self.base_state, [ref])[0]
                es = K.edges(face)
                src["n"] = len(es)
                for e in es:
                    out.append(("path", {"kind": "region_edge", "region": copy.deepcopy(ref),
                                         "c": [float(x) for x in K.edge_info(e)["c"]], "from": src}))
            elif kind == "face":
                pid, face = F.resolve(self.base_state, ref, "face")
                shape = self.base_state.parts[pid].shape
                es = K.edges(face)
                src["n"] = len(es)
                for e in es:
                    out.append(("path", {"kind": "edge", "edge": F.make_ref(pid, e, "edge", shape), "from": src}))
        except (K.KernelError, KeyError, IndexError):
            out = []
        return out or [("path", BO.outline_ref(kind, ref))]

    def _set(self, key, value):
        p = self.feature["params"]
        if key == "op":
            self._op_chosen = True
        if key == "creation" and self.ftype in ("extrude", "revolve", "sweep"):
            self._move_profiles_for(p.get("creation", "solid"), value)
        p[key] = value
        if key == "separate" and value and p.get("op", "new") != "new":
            # separate parts only makes sense for New: switch to it (and stop choosing the op automatically)
            p["op"] = "new"
            p.pop("scope", None)
            self._op_chosen = True
            self._show_op("new")
        if key == "type" and self.ftype in ("mirror", "linear_pattern", "circular_pattern", "curve_pattern", "point_pattern"):
            fk = value if value in ("features", "faces") else "parts"
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
        if key == "end" and self.ftype in ("extrude", "revolve", "hole"):
            # choosing an "Up to …" end makes its pick box the active one, so the next click fills it
            fk = {"up_to_face": "upto_face", "up_to_vertex": "upto_vertex", "up_to_part": "upto_part"}.get(value)
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
            if value in F.UP_TO:
                QTimer.singleShot(0, lambda: self._auto_direction(1))     # up to next, or one already picked
        if key == "end2" and self.ftype == "extrude":
            fk = {"up_to_face": "upto2_face", "up_to_vertex": "upto2_vertex", "up_to_part": "upto2_part"}.get(value)
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
            if value in F.UP_TO:
                QTimer.singleShot(0, lambda: self._auto_direction(2))
        if key in ("axis_std", "dir_std", "dir2_std") and value:
            # choosing a world axis replaces a picked axis
            fk = {"axis_std": "axis_sel", "dir_std": "dir_sel", "dir2_std": "dir2_sel"}[key]
            if fk in self.fields:
                self.fields[fk].items = []
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, p)
        self._sync_visibility()
        self.schedule_preview()

    def overlays(self):
        """Geometry of the current selections, in the pre-feature model, for highlighting."""
        out = []
        half = self.main.viewport.plane_half()

        def rect(ref):
            """A picked plane's outline matches the plane as drawn: its resized size, else its default."""
            doc = self.doc
            if not hasattr(doc, "plane_rect"):
                return None
            if ref.get("kind") == "std":
                return doc.plane_rect(("std", ref.get("name")), half)
            if ref.get("kind") == "plane":
                return doc.plane_rect(("plane", ref.get("fid")), half * 0.6)
            return None
        if self.ftype == "mate_connector":
            # the places it can go on the picked face: small crosses, the chosen one larger
            cur = int(self.feature["params"].get("point", 0) or 0)
            cands = self._mc_candidates()
            for k, c in enumerate(cands):
                s = half * (0.05 if k == cur % max(len(cands), 1) else 0.025)
                o = np.asarray(c["o"], float)
                pts = []
                for d_ in (np.array([s, 0, 0]), np.array([0, s, 0]), np.array([0, 0, s])):
                    pts += [o - d_, o + d_, o]
                out.append((("_mc_point", k), ("poly", np.array(pts))))
        params = self.feature["params"]
        hidden = {spec.get("key") for spec, _w in self.rows + getattr(self, "_inline", [])
                  if spec.get("visible") is not None and not spec["visible"](params)}
        for key, fld in self.fields.items():
            if key in hidden:
                continue                  # a box that's switched off (Second end unticked …): its picks don't count
            if self.ftype == "split" and key == "parts" and self.fields.get("tools") and self.fields["tools"].items:
                continue                  # the preview shows what's kept; a highlight of the whole part would hide it
            for i, item in enumerate(fld.items):
                if item[0] == "part" and fld is not self.active_field:
                    continue              # a whole part (merge scope, parts to act on) lights up only while its box
                    #                       is the one you're picking in — otherwise it hides the preview, as Onshape
                g = item_geometry(self.base_state, item, half, rect)
                if g is not None:
                    out.append(((key, i), g))
        return out

    # ------------------------------------------------------------------ drag arrows
    def _set_quiet(self, key, value):
        """Set a param from a drag and update its widget without re-triggering edits."""
        self.feature["params"][key] = value
        w = self.widgets.get(key)
        if isinstance(w, QLineEdit):
            w.setText(self._num_text(key, value))
        elif isinstance(w, (QCheckBox, QToolButton)) and w.isCheckable():
            w.blockSignals(True)
            w.setChecked(bool(value))
            w.blockSignals(False)
            if w.property("flip_label"):
                _flip_look(w)

    def _snap_step(self):
        raw = self.main.viewport.cam.scale * 6        # roughly 6 px per step
        mag = 10 ** np.floor(np.log10(max(raw, 1e-6)))
        for m in (1, 2, 5, 10):
            if m * mag >= raw:
                return float(m * mag)
        return float(mag * 10)

    def _split_arrows(self, p):
        """An arrow on each Split tool that keeps one side, pointing at the side that stays; drag it across to
        keep the other side."""
        st = self.base_state
        ids = [i for i in p.get("parts", []) if i in st.parts]
        if not ids:
            return []
        lo, hi = np.full(3, 1e18), np.full(3, -1e18)
        for pid in ids:
            a, b = K.bbox(st.parts[pid].shape)
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        size = float(np.linalg.norm(hi - lo)) * 2 + 10
        centre = (lo + hi) / 2
        length = max(float(np.linalg.norm(hi - lo)) * 0.25, 1.0)
        out = []
        for i, t in enumerate(F.split_tools(p)):
            if t.get("both", True):
                continue
            try:
                got = K._tool_normal_at(centre, F.split_tool_shape(st, t, centre, size))
            except Exception:
                got = None
            if got is None:
                continue
            _d, q, n = got
            flip0 = bool(t.get("flip"))

            def on_drag(v, i=i, flip0=flip0):
                want = flip0 if v >= 0 else not flip0
                tools = F.split_tools(self.feature["params"])
                if i < len(tools) and bool(tools[i].get("flip")) != want:
                    self._split_set(i, "flip", None)
            out.append({"origin": q, "dir": -n if flip0 else n, "value": length, "on_drag": on_drag,
                        "on_done": self.schedule_preview, "label": f"Keep {i + 1}"})
        return out

    def manipulators(self):
        """Onshape-style arrows: drag to set extrude depth / plane offset."""
        try:
            p = F.prepare(self.feature, self.base_state)["params"]      # expressions -> numbers
        except K.KernelError:
            return []
        try:
            if self.ftype == "fillet":
                return self._fillet_arrow(p)
            if self.ftype == "split":
                return self._split_arrows(p)
            if self.ftype == "revolve":
                return self._revolve_arrow(p)
            if self.ftype == "extrude":
                if p.get("end") == "through":
                    return []
                profs = F._profiles(self.base_state, p)
                if not profs:
                    return []
                n = np.asarray(F._profile_normal(self.base_state, p, profs), float)
                if p.get("op") in ("remove", "intersect") and not p.get("regions"):
                    n = -n                   # cuts from a face go into the part
                infos = [K.face_info(f) for f in profs]
                area = sum(i["area"] for i in infos) or 1.0
                origin = sum(np.array(i["c"]) * i["area"] for i in infos) / area
                symmetric = p.get("end") == "symmetric"
                depth = float(p.get("depth", 10))
                value = depth / 2 if symmetric else (-depth if p.get("flip") else depth)
                second = self._second_arrow(p, origin, n)
                if p.get("end") in F.UP_TO:
                    # up to something: the arrow only shows (and flips) the way it goes; click it to flip
                    reach = self.main.viewport.cam.scale * 70
                    return [{"origin": origin, "dir": n, "value": -reach if p.get("flip") else reach,
                             "drag": False, "on_drag": lambda v: None, "on_done": self.schedule_preview,
                             "on_click": self._toggle_flip, "label": dict(F.UP_TO_LABELS)[p["end"]]}] + second

                first = {"origin": origin, "dir": n, "value": value, "on_done": self.schedule_preview,
                         "snap": self._snap_step(), "on_click": None if symmetric else self._toggle_flip,
                         "min": 1e-3 if symmetric else None, "label": U.length(depth, 4)}

                def on_drag(v, symmetric=symmetric, first=first, second=second):
                    if symmetric:
                        self._set_quiet("depth", round(abs(v) * 2, 6))
                    else:
                        self._set_quiet("depth", round(abs(v), 6))
                        self._set_quiet("flip", v < 0)
                        for m in second:           # the second end always goes the other way to the first
                            m["dir"] = n if v < 0 else -n
                    first["label"] = U.length(self.feature["params"]["depth"], 4)
                    self.schedule_preview()
                first["on_drag"] = on_drag
                return [first] + second
            if self.ftype == "plane":
                if F.plane_type(p) != "offset":
                    return []
                planes_ = [(k, r) for k, r in F.plane_items(p) if F.plane_role(k, r) == "plane"]
                if planes_:
                    k_, r_ = planes_[0]
                    base = F.resolve_plane(self.base_state, r_ if k_ == "plane" else {"kind": "face", "face": r_})
                else:
                    base = K.std_plane("Top")
                off = float(p.get("offset", 0))
                value = -off if p.get("flip") else off

                def on_drag(v):
                    self._set_quiet("offset", round(abs(v), 6))
                    self._set_quiet("flip", v < 0)
                    self.main.viewport.manipulators[0]["label"] = U.length(abs(v), 4)
                    self.schedule_preview()
                return [{"origin": base.origin, "dir": base.normal, "value": value, "on_drag": on_drag,
                         "on_done": self.schedule_preview, "snap": self._snap_step(), "on_click": self._toggle_flip,
                         "label": U.length(off, 4)}]
        except (K.KernelError, KeyError, IndexError):
            return []
        return []

    def _first_fillet_edge(self, p):
        """(part shape, edge) of the first edge picked for a fillet, in the model before it; or None."""
        for ref in p.get("edges", []):
            try:
                pid, e = F.resolve(self.base_state, ref, "edge")
            except K.KernelError:
                continue
            return self.base_state.parts[pid].shape, e
        return None

    def _fillet_arrow(self, p):
        """An arrow on the round at the first picked edge, as Onshape's: its tip sits on the middle of the round
        and points into the corner it cuts away, so dragging it in makes the radius bigger."""
        got = self._first_fillet_edge(p)
        if got is None:
            return []
        fr = K.fillet_frame(*got)
        if fr is None:
            return []
        pt, _t, n1, n2, convex = fr
        inward = -(n1 + n2) if convex else (n1 + n2)       # from the edge towards the ball's centre
        if np.linalg.norm(inward) < 1e-9:
            return []
        inward = inward / np.linalg.norm(inward)
        # the middle of a round of radius r is r * (1 / sin(half the angle between the faces) - 1) in from the edge
        half = 0.5 * (math.pi - math.acos(max(-1.0, min(1.0, float(np.dot(n1, n2))))))
        k = 1.0 / max(math.sin(half), 1e-6) - 1.0
        if k < 1e-6:
            return []                                      # (faces almost flat to each other: no corner to show)
        r = float(p.get("radius", 1) or 1)
        arrow = {"origin": pt, "dir": inward, "value": r * k, "on_done": self.schedule_preview,
                 "snap": self._snap_step() * k, "min": 1e-3 * k, "on_click": None, "label": U.length(r, 4)}

        def on_drag(v):
            radius = round(abs(v) / k, 6)
            self._set_quiet("radius", radius)
            arrow["label"] = U.length(radius, 4)
            self.schedule_preview()
        arrow["on_drag"] = on_drag
        return [arrow]

    def fillet_failure_outlines(self):
        """When the fillet can't be made: where its rounds would run, in purple, and its edges in red (Onshape)."""
        try:
            p = F.prepare(self.feature, self.base_state)["params"]
        except K.KernelError:
            return []
        r = float(p.get("radius", 1) or 1)
        purple, red = (0.78, 0.18, 0.86), (0.86, 0.15, 0.13)
        out = []
        for ref in p.get("edges", [])[:40]:
            try:
                pid, e = F.resolve(self.base_state, ref, "edge")
            except K.KernelError:
                continue
            shape = self.base_state.parts[pid].shape
            out += [(poly, purple, 2.5) for poly in K.fillet_outline(shape, e, r)]
            out.append((np.asarray(K.discretize_edge(e), float), red, 3.5))
        return out

    def _second_arrow(self, p, origin, n):
        """Extrude with a second end position at a Blind depth: a second arrow, the other way to the first, that
        drags the Second depth (it can't cross over — that's the first end's job)."""
        if not (p.get("second") and p.get("end") not in ("symmetric", "through")
                and p.get("end2", "blind") == "blind"):
            return []
        depth2 = float(p.get("depth2", 0) or 0)
        arrow = {"origin": origin, "dir": n if p.get("flip") else -n, "value": max(depth2, 1e-3),
                 "on_done": self.schedule_preview, "snap": self._snap_step(), "min": 1e-3,
                 "label": U.length(depth2, 4)}

        def on_drag(v, arrow=arrow):
            self._set_quiet("depth2", round(abs(v), 6))
            arrow["label"] = U.length(self.feature["params"]["depth2"], 4)
            self.schedule_preview()
        arrow["on_drag"] = on_drag
        return [arrow]

    def _revolve_arrow(self, p):
        """A curved-way arrow on a Revolve that turns one way (one direction, two directions, up to …): it points
        the way the profile turns; click it to turn the other way."""
        end = p.get("end", "full")
        if not (end in ("one", "two") or end in F.UP_TO):
            return []
        profs = [f for f in F._profiles(self.base_state, p) if f.ShapeType() == 4]
        if not profs:
            return []
        a, d = F.resolve_axis(self.base_state, p.get("axis", {}))
        d = np.asarray(d, float) / np.linalg.norm(d)
        if p.get("flip"):
            d = -d
        infos = [K.face_info(f) for f in profs]
        area = sum(i["area"] for i in infos) or 1.0
        c = sum(np.array(i["c"]) * i["area"] for i in infos) / area
        r = c - np.asarray(a, float)
        r = r - (r @ d) * d
        if np.linalg.norm(r) < 1e-9:
            return []
        way = np.cross(d, r / np.linalg.norm(r))           # the way the profile sets off turning
        label = dict(F.UP_TO_LABELS).get(end) or f"{fmt(float(p.get('angle', 180)))}°"
        return [{"origin": c, "dir": way, "value": self.main.viewport.cam.scale * 70, "drag": False,
                 "on_drag": lambda v: None, "on_done": self.schedule_preview, "on_click": self._toggle_flip,
                 "label": label}]

    # ------------------------------------------------------------------ preview / commit
    def schedule_preview(self):
        self._timer.start(90)

    def _num_text(self, key, v):
        """A number box's text: a length in the document's unit; expressions as typed."""
        if key in self.__dict__.get("_len_keys", ()) and not isinstance(v, str):
            try:
                return U.num(v)
            except (TypeError, ValueError):
                return _show(v)
        return _show(v)

    def _number_problem(self, txt, want, unit, mn):
        """Why a number box's text can't be used (None if it can): unreadable, an undefined variable, the wrong
        kind of quantity (an angle in a length box), or below the minimum."""
        from ..expr import check_units, var_names
        if not str(txt).strip():
            return "enter a value"
        from ..expr import text_problem
        tp = text_problem(txt, self.base_state.vars)
        if tp:
            return tp
        v = parse_number(txt, angle=(unit == "deg"), variables=self.base_state.vars)
        if v is None:
            missing = [n for n in var_names(txt) if n not in self.base_state.vars]
            if missing:
                return f"#{missing[0]} isn't defined before this feature"
            return f"can't read '{txt.strip()}'"
        why = check_units(txt, want, getattr(self.base_state, "var_kinds", {}))
        if why:
            return why
        if mn is not None and v < mn:
            return f"must be at least {U.length(mn) if want == 'length' else fmt(mn)}"
        return None

    def _show_field_errors(self):
        if self._field_errors:
            self.err.setText("⚠ " + "\n⚠ ".join(self._field_errors.values()))
            self.err.setStyleSheet("color:#d93025; font-weight:600;")
            self.err.show()
            self.adjustSize()

    def _do_preview(self):
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        vp = self.main.viewport
        if not self._op_chosen and vp._manip_drag is None:
            self._auto_op()                     # a longer extrude may reach a part now (or no longer)
            self._sync_visibility()
        if vp._manip_drag is None:          # don't rebuild the arrow while it's being dragged
            vp.manipulators = self.manipulators()
        err = self.main.show_preview(self.feature, self.index, self.overlays())
        # a fillet too big to make: show where it would run (purple) and its edges (red), as Onshape does
        vp.outlines = self.fillet_failure_outlines() if (err and self.ftype == "fillet") else []
        vp.update()
        if self._field_errors:
            self._show_field_errors()
        elif err:
            self.err.setText("⚠ " + err)
            self.err.setStyleSheet("")
            self.err.show()
        elif self.ftype == "measure_value":
            from .. import measure_value as MV
            p = self.feature["params"]
            try:
                pp = F.prepare(self.feature, self.base_state)["params"]
                val, line = MV.measure(self.base_state, pp)
                if pp.get("mtype") not in MV.FIXED:
                    val = MV.rounded(val, pp.get("round_to", 0))
                self.main.viewport.measure_line = None if pp.get("hide_preview") else line
                kind = MV.kind_of(self.base_state, pp)
                self.err.setText(f"#{str(p.get('name', '')).lstrip('#')} = {MV.value_text(pp, val, kind)}")
            except K.KernelError as ex:
                val = None
                self.main.viewport.measure_line = None
                self.err.setText(str(ex))
            self.err.setStyleSheet("color:#2e9e44; font-weight:600;" if val is not None else "color:#6a737d;")
            self.err.setVisible(True)
        elif self.ftype == "variable":
            p = self.feature["params"]
            val = parse_number(p.get("expr", ""), variables=self.base_state.vars)
            self.err.setText(f"#{str(p.get('name', '')).lstrip('#')} = {fmt(val)}" if val is not None else "")
            self.err.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.err.setVisible(val is not None)
        else:
            self.err.hide()
        self.adjustSize()

    def accept(self):
        if self._field_errors:
            self._show_field_errors()                 # a box can't be used: say so rather than accept it
            return
        self._timer.stop()
        self._stop_shift_filter()
        if self.ftype == "measure_value":
            self.main.viewport.measure_line = None
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        if self.ftype == "hole":
            import re as _re
            try:
                label = F.hole_dims(self.feature["params"])[3]
                base = _re.sub(r"\s*\(.*\)$", "", self.feature["name"])
                self.feature["name"] = f"{base} ({label})"
            except Exception:
                pass
        if self.ftype == "measure_value":
            p = self.feature["params"]
            p["name"] = str(p.get("name", "")).strip().lstrip("#")
        if self.ftype == "variable":
            p = self.feature["params"]
            name = str(p.get("name", "")).strip().lstrip("#")
            p["name"] = name
            if self.feature["name"].startswith(("Variable", "#")):
                self.feature["name"] = f"#{name} = {p.get('expr', '')}"
        self.accepted.emit()

    def reject(self):
        self._timer.stop()
        self._stop_shift_filter()
        if self.ftype == "measure_value":
            self.main.viewport.measure_line = None
        self.rejected.emit()

    def _stop_shift_filter(self):
        if self.ftype == "mate_connector":
            try:
                QApplication.instance().removeEventFilter(self)
            except Exception:
                pass


class SketchDialog(_ResizableFrame):
    """The small Sketch panel: plane selection and display toggles."""
    accepted = Signal()
    rejected = Signal()
    planePicked = Signal(object)

    def __init__(self, main, feature, has_plane):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
        self._init_resizable(260)
        self.main = main
        self.feature = feature
        sh = QGraphicsDropShadowEffect(self)
        sh.setBlurRadius(18)
        sh.setOffset(0, 3)
        sh.setColor(QColor(0, 0, 0, 60))
        self.setGraphicsEffect(sh)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 10)
        lay.setSpacing(6)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(icon("sketch").pixmap(20, 20))
        head.addWidget(ic)
        self.title = QLineEdit(feature["name"])
        self.title.setStyleSheet("QLineEdit { border: none; font-weight: 700; background: transparent; }"
                                 "QLineEdit:focus { border: 1px solid #1f6fd1; background: white; }")
        head.addWidget(self.title, 1)
        ok = dialog_button(QToolButton(), "ok")
        ok.setToolTip(tr("Finish sketch"))
        ok.clicked.connect(self.accepted.emit)
        cancel = dialog_button(QToolButton(), "cancel")
        cancel.setToolTip(tr("Cancel sketch"))
        cancel.clicked.connect(self.rejected.emit)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        self.plane_field = SelectionField("plane", "Sketch plane", {"plane"}, multi=False)
        self.plane_field.activated.connect(self._activate)
        lay.addWidget(self.plane_field)
        self.show_cons = QCheckBox(tr("Show constraints"))
        self.show_cons.setChecked(True)
        self.show_regions = QCheckBox(tr("Show sketch regions"))
        self.show_regions.setChecked(True)
        lay.addWidget(self.show_cons)
        lay.addWidget(self.show_regions)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#4b5563;")
        lay.addWidget(self.status)
        self.plane_field.items = [("plane", feature["params"]["plane"])] if has_plane else []
        self.plane_field.refresh(main.doc)
        if not has_plane:
            self._activate(self.plane_field)
        self.adjustSize()
        self.move(12, 12)
        self.show()

    def _activate(self, f):
        f.set_active(True)
        f.refresh(self.main.doc)
        self.main.viewport.select_filter = {"plane", "face"}
        self.main.viewport.sketcher = None
        self.main.sketch_waiting_for_plane = True

    def handle_pick(self, pick):
        item = pick_to_item(self.main.viewport, pick, {"plane"})
        if item:
            self.plane_field.items = [item]
            self.plane_field.set_active(False)
            self.plane_field.refresh(self.main.doc)
            self.planePicked.emit(item[1])

    def set_status(self, text, ok=False):
        self.status.setText(text)
        self.status.setStyleSheet("color:%s;" % (theme.OK_GREEN if ok else theme.TEXT_MUTED))
