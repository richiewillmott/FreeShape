"""Floating, Onshape-style feature dialogs (green check / red X), built from small schemas."""
from __future__ import annotations

import copy
import json

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QLineEdit, QComboBox, QCheckBox,
    QListWidget, QListWidgetItem, QWidget, QButtonGroup, QSizePolicy, QGraphicsDropShadowEffect,
)

from .. import kernel as K
from .. import features as F
from .icons import icon
from . import theme
from .numparse import parse_number, fmt, has_vars
from .viewport import Pick

OP_LABELS = [("new", "New"), ("add", "Add"), ("remove", "Remove"), ("intersect", "Intersect")]


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


def item_geometry(state, item, plane_half=40.0):
    """World geometry used to highlight a selected item: ('mesh', Mesh) or ('poly', ndarray)."""
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
            return ("poly", np.array([pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]))
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
        self.title = QLabel(label)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("color:#4b5563;")
        top.addWidget(self.title, 1)
        self.clear_btn = QToolButton()
        self.clear_btn.setText("✕")
        self.clear_btn.setToolTip("Clear selections")
        self.clear_btn.clicked.connect(self.clear)
        top.addWidget(self.clear_btn)
        lay.addLayout(top)
        self.list = QListWidget()
        self.list.setFixedHeight(22)
        self.extra = 0              # extra height (px) the dialog has been dragged taller by
        self._base_h = 22
        self.list.setStyleSheet("QListWidget { background: transparent; }")
        self.list.itemDoubleClicked.connect(self._remove_item)
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
            it = QListWidgetItem("Click to select" if self.property("active") == "true" else "—")
            it.setForeground(QColor("#8a94a3"))
            self.list.addItem(it)
        for i, item in enumerate(self.items):
            it = QListWidgetItem(item_label(doc, item))
            if i in missing:
                it.setForeground(QColor("#d93025"))
                it.setToolTip("Reference could not be found — double-click to remove")
            else:
                it.setToolTip("Double-click to remove")
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
        row = self.list.row(it)
        if 0 <= row < len(self.items):
            self.items.pop(row)
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


def _sel(key, label, want, multi=True, visible=None):
    return {"t": "sel", "key": key, "label": label, "want": want, "multi": multi, "visible": visible}


def _ops(options=None):
    return {"t": "ops", "key": "op", "options": options or OP_LABELS}


SURFACE_OPS = [("new", "New"), ("add", "Add")]
# features whose selection boxes are stored as they are: [[kind, ref], ...] per box
PAIR_FEATURES = ("measure_value", "thicken", "enclose", "rib", "delete_part", "helix", "bounding_box", "compound", "explode_part")


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
        _sel("curves", "Sketch curves, edges and outlines", {"path", "outline"}, visible=curvy),
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
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") != "symmetric"),
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
        _check("flip", "Opposite direction",
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
        _check("flip", "Flip direction"),
    ],
    "offset_surface": [
        _sel("faces", "Faces to offset", {"face"}),
        _num("distance", "Distance (outwards)"),
        _check("flip", "Flip direction"),
    ],
    "plane": [
        _choice("type", "Plane type", F.PLANE_TYPES),
        _sel("refs_sel", "Planes, faces, edges and vertices", {"plane", "face", "edge", "vertex"}),
        {"t": "info", "key": "_info", "text": lambda p: _plane_info(p)},
        _num("offset", "Offset distance", visible=lambda p: F.plane_type(p) == "offset"),
        _num("angle", "Angle", unit="deg", visible=lambda p: F.plane_type(p) == "line_angle"),
        _check("flip", "Flip direction", visible=lambda p: F.plane_type(p) is not None),
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
        _check("flip", "Opposite direction"),
        _check("second", "Second direction"),
        _sel("dir2_sel", "Second direction (edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: p.get("second")),
        _choice("dir2_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: p.get("second")),
        _num("spacing2", "Second distance", visible=lambda p: p.get("second")),
        _num("count2", "Second instance count", unit="", visible=lambda p: p.get("second")),
        _check("flip2", "Opposite second direction", visible=lambda p: p.get("second")),
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
        _check("flip", "Opposite direction"),
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
        _sel("parts", "Parts to move", {"part"}),
        _num("dx", "Move X"),
        _num("dy", "Move Y"),
        _num("dz", "Move Z"),
        _num("rotate", "Rotate", unit="deg"),
        _sel("axis_sel", "Rotate about (edge or sketch line)", {"axis"}, multi=False,
             visible=lambda p: _isnum(p.get("rotate")) is not False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")],
                visible=lambda p: _isnum(p.get("rotate")) is not False),
        _check("copy", "Keep the original (make a copy)"),
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
    "rib": [
        _sel("curves", "Sketch lines for the rib", {"path"}),
        _sel("parts", "Parts to grow out to (none: all)", {"part"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _choice("direction", "Rib grows", _mf().RIB_DIRECTIONS),
        _check("flip", "Opposite direction"),
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
        _check("flip", "Opposite direction"),
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
        _choice("mtype", "Measure type", _mv().TYPES),
        _sel("ray", "Ray (sketch line or edge from its start, round edge, cylinder)", {"axis", "face"},
             multi=False, visible=lambda p: p.get("mtype") == "ray"),
        _sel("entities", "Entities", {"vertex", "edge", "face", "part", "plane", "region", "sketch_line"}),
        _choice("method", "Measure method", _mv().METHODS, visible=lambda p: p.get("mtype", "distance") == "distance"),
        _choice("component", "Value", _mv().COMPONENTS, visible=lambda p: p.get("mtype", "distance") == "distance"),
        _num("ray_offset", "Ray origin offset", visible=lambda p: p.get("mtype") == "ray"),
        _check("ray_flip", "Opposite direction", visible=lambda p: p.get("mtype") == "ray"),
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
        _num("round_to", "Round to a multiple of (0 = don't)", minimum=0),
        _check("hide_preview", "Hide preview"),
        {"t": "info", "key": "_info", "text": lambda p: "Measured again on every rebuild, so whatever uses "
                                                       "#" + str(p.get("name") or "name") + " follows the model."},
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
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
        f["features"] = [("feature", i) for i in p.get("features", [])]
        f["faces"] = [("face", r) for r in p.get("faces", []) or []]
    if ftype in ("extrude", "revolve", "hole"):
        for pre, key in (("upto", "up_to"), ("upto2", "up_to2")):
            u = p.get(key) or {}
            f[pre + "_face"] = [(u["kind"], u["ref"])] if u.get("kind") in ("face", "plane") else []
            f[pre + "_vertex"] = [("vertex", u["ref"])] if u.get("kind") == "vertex" else []
            f[pre + "_part"] = [("part", u["id"])] if u.get("kind") == "part" else []
    if ftype in ("extrude", "revolve", "sweep", "loft", "mirror", "linear_pattern", "circular_pattern"):
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
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
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
    if ftype in ("extrude", "revolve", "sweep", "loft", "mirror", "linear_pattern", "circular_pattern"):
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
            p["axis"] = {"kind": "std", "name": p.get("axis_std") or "Z"}
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
    if ftype in ("mirror", "linear_pattern", "circular_pattern"):
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
        self._info_rows = []
        self.rows = []
        self.active_field = None
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
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip("Accept (Enter)")
        ok.clicked.connect(self.accept)
        cancel = QToolButton()
        cancel.setObjectName("cancelButton")
        cancel.setText("✕")
        cancel.setToolTip("Cancel (Esc)")
        cancel.clicked.connect(self.reject)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#e3e6ea;")
        lay.addWidget(line)
        p = self.feature["params"]
        for spec in self.schema:
            w = self._make_row(spec, p)
            if w is not None:
                lay.addWidget(w)
                self.rows.append((spec, w))
        self.err = QLabel("")
        self.err.setObjectName("dialogError")
        self.err.setWordWrap(True)
        self.err.hide()
        lay.addWidget(self.err)

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
                b.setText(lab)
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
        if t == "info":
            lab = QLabel("")
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#2e9e44; font-weight:600;")
            self.widgets[spec["key"]] = lab
            self._info_rows.append((spec, lab))
            return lab
        if t == "sel":
            f = SelectionField(spec["key"], spec["label"], spec["want"], spec["multi"])
            f.activated.connect(self.activate)
            f.changed.connect(self._fields_changed)
            self.fields[spec["key"]] = f
            return f
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        if t == "check":
            cb = QCheckBox(spec["label"])
            cb.setChecked(bool(p.get(spec["key"])))
            cb.toggled.connect(lambda v, k=spec["key"]: self._set(k, v))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        lab = QLabel(spec["label"])
        lab.setStyleSheet("color:#4b5563;")
        h.addWidget(lab, 1)
        if t == "num":
            ed = QLineEdit(_show(p.get(spec["key"], 0)))
            ed.setFixedWidth(110)
            ed.setAlignment(Qt.AlignRight)
            unit = spec["unit"]
            if unit:
                ed.setPlaceholderText(unit)
                ed.setToolTip(f"Value in {unit}. Expressions, units and variables work: '25/2', '1 in', '#wall * 2'.")

            def done(ed=ed, k=spec["key"], unit=unit, mn=spec.get("min"), whole=bool(spec.get("int"))):
                txt = ed.text()
                v = parse_number(txt, angle=(unit == "deg"), variables=self.base_state.vars)
                bad = v is None or (mn is not None and v < mn)
                ed.setProperty("invalid", "true" if bad else "false")
                ed.style().unpolish(ed)
                ed.style().polish(ed)
                if not bad:
                    if has_vars(txt):
                        self._set(k, txt.strip())        # keep the expression so it follows the variable
                    else:
                        self._set(k, int(round(v)) if k == "count" or whole else v)
            self.widgets[spec["key"]] = ed
            ed.textEdited.connect(lambda _t, f=done: f())
            ed.editingFinished.connect(lambda ed=ed, k=spec["key"]: ed.setText(_show(self.feature["params"].get(k, 0))))
            h.addWidget(ed)
            if unit:
                u = QLabel(unit)
                u.setStyleSheet("color:#8a94a3;")
                u.setFixedWidth(24)
                h.addWidget(u)
            return row
        if t == "text":
            ed = QLineEdit(str(p.get(spec["key"], "")))
            ed.setPlaceholderText(spec.get("placeholder", ""))
            ed.setFixedWidth(150)
            self.widgets[spec["key"]] = ed

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
            for val, lab2 in spec["options"]:
                cb.addItem(lab2, val)
            idx = max(0, cb.findData(p.get(spec["key"], spec["options"][0][0])))
            cb.setCurrentIndex(idx)
            cb.currentIndexChanged.connect(lambda i, cb=cb, k=spec["key"]: self._set(k, cb.itemData(i)))
            h.addWidget(cb)
            self.widgets[spec["key"]] = cb
            return row
        if t == "sketch_choice":
            cb = QComboBox()
            cb.addItem("— select —", None)
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
            cb = QCheckBox("Keep both sides")
            cb.setChecked(bool(t.get("both", True)))
            cb.toggled.connect(lambda on, i=i: self._split_set(i, "both", on))
            h.addWidget(cb)
            fl = QToolButton()
            fl.setText("⇅ Flip")
            fl.setToolTip("Keep the other side (or drag the arrow across)")
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
            txt = spec["text"](p)
            lab.setText(txt)
            w_ = max(min(lab.width() if lab.width() > 50 else 10 ** 6, self.width() - 24), 200)
            lab.setMinimumHeight(lab.heightForWidth(w_) if txt else 0)     # wrapped text gets all its lines
            if spec["key"] == "_info":           # warnings in red, hints in green
                lab.setStyleSheet("color:#d93025; font-weight:600;" if txt.startswith("⚠")
                                  else "color:#2e9e44; font-weight:600;")
        for spec, w in self.rows:
            vis = spec.get("visible")
            w.setVisible(True if vis is None else bool(vis(p)))
        for f in self.fields.values():
            f.refresh(self.doc)
        self.adjustSize()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept()
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)

    # ------------------------------------------------------------------ selection
    def activate(self, field):
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
            fld = self.fields.get(key)
            if fld and i < len(fld.items):
                fld.items.pop(i)
                self._fields_changed()
            return
        fld = self.active_field
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
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, self.feature["params"])
        self._sync_visibility()
        self.schedule_preview()

    def _move_profiles_for(self, old, new):
        """Switching Solid <-> Thin / Surface keeps the selection: faces and regions become their outlines in the
        curves box (and back again), so the same shape is made into a wall or surface."""
        from .. import body_options as BO
        prof, curves = self.fields.get("profiles"), self.fields.get("curves")
        if prof is None or curves is None or (old == "solid") == (new == "solid"):
            return
        if new != "solid":
            curves.items = [it for it in curves.items] + [("path", BO.outline_ref(k, r)) for k, r in prof.items]
            prof.items = []
        else:
            back = [(r["of"], r["ref"]) for k, r in curves.items if r.get("kind") == "outline"]
            curves.items = [(k, r) for k, r in curves.items if r.get("kind") != "outline"]
            prof.items = prof.items + back
        for f in (prof, curves):
            f.refresh(self.doc)
        if new != "solid" and curves.items:
            QTimer.singleShot(0, lambda: self.activate(curves))
        elif new == "solid":
            QTimer.singleShot(0, lambda: self.activate(prof))

    def _set(self, key, value):
        p = self.feature["params"]
        if key == "creation" and self.ftype in ("extrude", "revolve", "sweep"):
            self._move_profiles_for(p.get("creation", "solid"), value)
        p[key] = value
        if key == "type" and self.ftype in ("mirror", "linear_pattern", "circular_pattern"):
            fk = value if value in ("features", "faces") else "parts"
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
        if key == "end" and self.ftype in ("extrude", "revolve", "hole"):
            # choosing an "Up to …" end makes its pick box the active one, so the next click fills it
            fk = {"up_to_face": "upto_face", "up_to_vertex": "upto_vertex", "up_to_part": "upto_part"}.get(value)
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
        if key == "end2" and self.ftype == "extrude":
            fk = {"up_to_face": "upto2_face", "up_to_vertex": "upto2_vertex", "up_to_part": "upto2_part"}.get(value)
            if fk in self.fields:
                QTimer.singleShot(0, lambda fk=fk: self.activate(self.fields[fk]))
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
        for key, fld in self.fields.items():
            if self.ftype == "split" and key == "parts" and self.fields.get("tools") and self.fields["tools"].items:
                continue                  # the preview shows what's kept; a highlight of the whole part would hide it
            for i, item in enumerate(fld.items):
                g = item_geometry(self.base_state, item, half)
                if g is not None:
                    out.append(((key, i), g))
        return out

    # ------------------------------------------------------------------ drag arrows
    def _set_quiet(self, key, value):
        """Set a param from a drag and update its widget without re-triggering edits."""
        self.feature["params"][key] = value
        w = self.widgets.get(key)
        if isinstance(w, QLineEdit):
            w.setText(fmt(value))
        elif isinstance(w, QCheckBox):
            w.blockSignals(True)
            w.setChecked(bool(value))
            w.blockSignals(False)

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
            if self.ftype == "split":
                return self._split_arrows(p)
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

                def on_drag(v, symmetric=symmetric):
                    if symmetric:
                        self._set_quiet("depth", round(abs(v) * 2, 6))
                    else:
                        self._set_quiet("depth", round(abs(v), 6))
                        self._set_quiet("flip", v < 0)
                    self.main.viewport.manipulators[0]["label"] = fmt(self.feature["params"]["depth"]) + " mm"
                    self.schedule_preview()
                return [{"origin": origin, "dir": n, "value": value, "on_drag": on_drag,
                         "on_done": self.schedule_preview, "snap": self._snap_step(),
                         "min": 1e-3 if symmetric else None, "label": fmt(depth) + " mm"}]
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
                    self.main.viewport.manipulators[0]["label"] = fmt(abs(v)) + " mm"
                    self.schedule_preview()
                return [{"origin": base.origin, "dir": base.normal, "value": value, "on_drag": on_drag,
                         "on_done": self.schedule_preview, "snap": self._snap_step(), "label": fmt(off) + " mm"}]
        except (K.KernelError, KeyError, IndexError):
            return []
        return []

    # ------------------------------------------------------------------ preview / commit
    def schedule_preview(self):
        self._timer.start(90)

    def _do_preview(self):
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        vp = self.main.viewport
        if vp._manip_drag is None:          # don't rebuild the arrow while it's being dragged
            vp.manipulators = self.manipulators()
        err = self.main.show_preview(self.feature, self.index, self.overlays())
        if err:
            self.err.setText("⚠ " + err)
            self.err.setStyleSheet("")
            self.err.show()
        elif self.ftype == "measure_value":
            from .. import measure_value as MV
            p = self.feature["params"]
            try:
                pp = F.prepare(self.feature, self.base_state)["params"]
                val, line = MV.measure(self.base_state, pp)
                val = MV.rounded(val, pp.get("round_to", 0))
                self.main.viewport.measure_line = None if pp.get("hide_preview") else line
                unit = MV.UNITS.get(p.get("mtype", "distance"), "")
                self.err.setText(f"#{str(p.get('name', '')).lstrip('#')} = {fmt(val)} {unit}".rstrip())
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
        self._timer.stop()
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
        if self.ftype == "measure_value":
            self.main.viewport.measure_line = None
        self.rejected.emit()


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
        ok = QToolButton()
        ok.setObjectName("okButton")
        ok.setText("✓")
        ok.setToolTip("Finish sketch")
        ok.clicked.connect(self.accepted.emit)
        cancel = QToolButton()
        cancel.setObjectName("cancelButton")
        cancel.setText("✕")
        cancel.setToolTip("Cancel sketch")
        cancel.clicked.connect(self.rejected.emit)
        head.addWidget(ok)
        head.addWidget(cancel)
        lay.addLayout(head)
        self.plane_field = SelectionField("plane", "Sketch plane", {"plane"}, multi=False)
        self.plane_field.activated.connect(self._activate)
        lay.addWidget(self.plane_field)
        self.show_cons = QCheckBox("Show constraints")
        self.show_cons.setChecked(True)
        self.show_regions = QCheckBox("Show sketch regions")
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
