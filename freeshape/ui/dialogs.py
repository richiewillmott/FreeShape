"""Floating, Onshape-style feature dialogs (green check / red X), built from small schemas."""
from __future__ import annotations

import copy

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
    if k == "edge" and "path" in want:
        pid, ei = pick.key
        shape = vp.part_shapes.get(pid)
        if shape is not None:
            return ("path", {"kind": "edge", "edge": F.make_ref(pid, K.edges(shape)[ei], "edge", shape)})
    if k == "sketch_line" and "axis" in want:
        fid, eid = pick.key
        return ("axis", {"kind": "sketch_line", "sketch": fid, "line": eid})
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
    if kind == "part":
        p = doc.state.parts.get(ref)
        return p.name if p else ref
    if kind == "plane":
        if ref["kind"] == "std":
            return f"{ref['name']} plane"
        if ref["kind"] == "plane":
            f = doc.feature(ref["fid"])
            return f["name"] if f else "Plane"
        return "Planar face"
    if kind == "path":
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
        if kind == "part":
            p = state.parts.get(ref)
            return ("mesh", K.tessellate(p.shape)) if p else None
        if kind == "plane":
            pl = F.resolve_plane(state, ref)
            h = plane_half
            return ("poly", np.array([pl.to_world(u, v) for u, v in ((-h, -h), (h, -h), (h, h), (-h, h), (-h, -h))]))
        if kind == "path":
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
        self.list.setFixedHeight(min(6, n) * 20 + 4)

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
        _ops(),
        _sel("profiles", "Faces and sketch regions to extrude", {"region", "face"}),
        _choice("end", "End type", [("blind", "Blind"), ("symmetric", "Symmetric"), ("through", "Through all")]),
        _num("depth", "Depth", visible=lambda p: p.get("end") != "through", minimum=1e-6),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") != "symmetric"),
        _check("second", "Second end position", visible=lambda p: p.get("end") == "blind"),
        _num("depth2", "Second depth", visible=lambda p: p.get("end") == "blind" and p.get("second")),
        _check("both", "Both directions", visible=lambda p: p.get("end") == "through"),
    ],
    "revolve": [
        _ops(),
        _sel("profiles", "Faces and sketch regions to revolve", {"region", "face"}),
        _sel("axis_sel", "Revolve axis (sketch line or edge)", {"axis"}, multi=False),
        _choice("axis_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _choice("end", "Revolve type", [("full", "Full"), ("one", "One direction"), ("symmetric", "Symmetric")]),
        _num("angle", "Revolve angle", unit="deg", visible=lambda p: p.get("end") != "full"),
        _check("flip", "Opposite direction", visible=lambda p: p.get("end") == "one"),
    ],
    "fillet": [
        _sel("edges", "Edges or faces to fillet", {"edge"}),
        _num("radius", "Radius", minimum=1e-6),
    ],
    "chamfer": [
        _sel("edges", "Edges or faces to chamfer", {"edge"}),
        _num("distance", "Distance", minimum=1e-6),
    ],
    "shell": [
        _sel("faces", "Faces to remove", {"face"}),
        _num("thickness", "Thickness", minimum=1e-6),
        _check("outward", "Shell outside"),
    ],
    "plane": [
        _sel("ref_sel", "Plane or planar face", {"plane"}, multi=False),
        _num("offset", "Offset distance"),
        _check("flip", "Flip direction"),
    ],
    "mirror": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to mirror", {"part"}),
        _sel("plane_sel", "Mirror plane", {"plane"}, multi=False),
    ],
    "linear_pattern": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to pattern", {"part"}),
        _sel("dir_sel", "Direction (edge or sketch line)", {"axis"}, multi=False),
        _choice("dir_std", "…or world axis", [("", "—"), ("X", "X axis"), ("Y", "Y axis"), ("Z", "Z axis")]),
        _num("spacing", "Distance"),
        _num("count", "Instance count", unit=""),
        _check("flip", "Opposite direction"),
    ],
    "circular_pattern": [
        _ops([("new", "New"), ("add", "Add")]),
        _sel("parts", "Parts to pattern", {"part"}),
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
        _ops(),
        _sel("profiles", "Faces and sketch regions to sweep", {"region", "face"}),
        _sel("path", "Sweep path (sketch curves or edges)", {"path"}),
    ],
    "loft": [
        _ops(),
        _sel("profiles", "Profiles — pick them in order", {"region", "face"}),
        _check("ruled", "Straight sides (ruled)"),
    ],
    "draft": [
        _sel("faces", "Faces to draft", {"face"}),
        _sel("neutral_sel", "Neutral plane", {"plane"}, multi=False),
        _num("angle", "Draft angle", unit="deg"),
        _check("flip", "Opposite direction"),
    ],
    "split": [
        _sel("parts", "Parts to split", {"part"}),
        _sel("plane_sel", "Split with plane or planar face", {"plane"}, multi=False),
        _choice("keep", "Keep", [("both", "Both sides"), ("front", "Front side only"), ("back", "Back side only")]),
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
        _choice("end", "Termination", [("through", "Through all"), ("blind", "Blind")]),
        _num("depth", "Hole depth", visible=lambda p: p.get("end") == "blind"),
        _check("tip", "Drill point (118°)", visible=lambda p: p.get("end") == "blind"),
        {"t": "info", "key": "_info", "text": lambda p: _hole_info(p)},
    ],
}


def params_to_fields(ftype, p):
    """Selection-field contents for a feature's params."""
    f = {}
    if ftype in ("extrude", "revolve", "sweep"):
        f["profiles"] = [("region", r) for r in p.get("regions", [])] + [("face", r) for r in p.get("faces", [])]
    if ftype == "sm_base":
        f["profiles"] = [("region", r) for r in p.get("regions", [])]
    if ftype == "sm_flange":
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype == "sweep":
        f["path"] = [("path", r) for r in p.get("path", [])]
    if ftype == "loft":
        f["profiles"] = [(it["kind"], it["ref"]) for it in p.get("profiles", [])]
    if ftype == "draft":
        f["faces"] = [("face", r) for r in p.get("faces", [])]
        f["neutral_sel"] = [("plane", p["neutral"])] if p.get("neutral") else []
    if ftype in ("split", "move"):
        f["parts"] = [("part", i) for i in p.get("parts", [])]
    if ftype == "split":
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "move":
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "revolve":
        ax = p.get("axis") or {}
        f["axis_sel"] = [("axis", ax)] if ax and ax.get("kind") != "std" else []
    if ftype in ("fillet", "chamfer"):
        f["edges"] = [("edge", r) for r in p.get("edges", [])]
    if ftype in ("shell", "thread"):
        f["faces"] = [("face", r) for r in p.get("faces", [])]
    if ftype == "plane":
        f["ref_sel"] = [("plane", p["ref"])] if p.get("ref") else []
    if ftype == "mirror":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        f["plane_sel"] = [("plane", p["plane"])] if p.get("plane") else []
    if ftype == "linear_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        d = p.get("direction") or {}
        f["dir_sel"] = [("axis", d)] if d and d.get("kind") != "std" else []
    if ftype == "circular_pattern":
        f["parts"] = [("part", i) for i in p.get("parts", [])]
        a = p.get("axis") or {}
        f["axis_sel"] = [("axis", a)] if a and a.get("kind") != "std" else []
    if ftype == "boolean":
        f["tools"] = [("part", i) for i in p.get("tools", [])]
        f["targets"] = [("part", i) for i in p.get("targets", [])]
    return f


def fields_to_params(ftype, fields, p):
    if ftype == "sm_base":
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
    if ftype == "sm_flange":
        p["edges"] = [r for k, r in fields.get("edges", []) if k == "edge"]
    if ftype in ("extrude", "revolve", "sweep"):
        p["regions"] = [r for k, r in fields.get("profiles", []) if k == "region"]
        p["faces"] = [r for k, r in fields.get("profiles", []) if k == "face"]
    if ftype == "sweep":
        p["path"] = [r for k, r in fields.get("path", [])]
    if ftype == "loft":
        p["profiles"] = [{"kind": k, "ref": r} for k, r in fields.get("profiles", [])]
    if ftype == "draft":
        p["faces"] = [r for k, r in fields.get("faces", [])]
        sel = fields.get("neutral_sel", [])
        p["neutral"] = sel[0][1] if sel else {"kind": "std", "name": "Top"}
    if ftype in ("split", "move"):
        p["parts"] = [r for k, r in fields.get("parts", [])]
    if ftype == "split":
        sel = fields.get("plane_sel", [])
        p["plane"] = sel[0][1] if sel else {"kind": "std", "name": "Right"}
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
    if ftype in ("shell", "thread"):
        p["faces"] = [r for k, r in fields.get("faces", [])]
    if ftype == "plane":
        sel = fields.get("ref_sel", [])
        p["ref"] = sel[0][1] if sel else {"kind": "std", "name": "Top"}
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

class FeatureDialog(QFrame):
    accepted = Signal()
    rejected = Signal()

    def __init__(self, main, feature, is_new, index, preselection=()):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
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
        first = next((f for f in self.fields.values() if not f.items), None) or next(iter(self.fields.values()), None)
        if first:
            self.activate(first)
        self._sync_visibility()
        self.schedule_preview()
        self.setFixedWidth(300)
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
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(0)
            grp = QButtonGroup(w)
            for val, lab in spec["options"]:
                b = QToolButton()
                b.setObjectName("segment")
                b.setText(lab)
                b.setCheckable(True)
                b.setChecked(p.get(spec["key"]) == val)
                b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                b.clicked.connect(lambda _=False, v=val: self._set(spec["key"], v))
                grp.addButton(b)
                h.addWidget(b)
            return w
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

            def done(ed=ed, k=spec["key"], unit=unit, mn=spec.get("min")):
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
                        self._set(k, int(round(v)) if k == "count" else v)
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

    def _sync_visibility(self):
        p = self.feature["params"]
        for spec, lab in self._info_rows:
            lab.setText(spec["text"](p))
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
        for key, fld in self.fields.items():
            for i, pk in enumerate(picks):
                if i in used or (not fld.multi and fld.items):
                    continue
                item = pick_to_item(self.main.viewport, pk, fld.want)
                if item is None:
                    continue
                used.add(i)
                fld.toggle(item)
        self.main.viewport.clear_selection()

    def _fields_changed(self):
        fields = {k: f.items for k, f in self.fields.items()}
        fields_to_params(self.ftype, fields, self.feature["params"])
        self._sync_visibility()
        self.schedule_preview()

    def _set(self, key, value):
        p = self.feature["params"]
        p[key] = value
        if key in ("axis_std", "dir_std") and value:
            # choosing a world axis replaces a picked axis
            fk = "axis_sel" if key == "axis_std" else "dir_sel"
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

    def manipulators(self):
        """Onshape-style arrows: drag to set extrude depth / plane offset."""
        try:
            p = F.prepare(self.feature, self.base_state)["params"]      # expressions -> numbers
        except K.KernelError:
            return []
        try:
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
                base = F.resolve_plane(self.base_state, p["ref"])
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
        self.feature["name"] = self.title.text().strip() or self.feature["name"]
        if self.ftype == "hole":
            import re as _re
            try:
                label = F.hole_dims(self.feature["params"])[3]
                base = _re.sub(r"\s*\(.*\)$", "", self.feature["name"])
                self.feature["name"] = f"{base} ({label})"
            except Exception:
                pass
        if self.ftype == "variable":
            p = self.feature["params"]
            name = str(p.get("name", "")).strip().lstrip("#")
            p["name"] = name
            if self.feature["name"].startswith(("Variable", "#")):
                self.feature["name"] = f"#{name} = {p.get('expr', '')}"
        self.accepted.emit()

    def reject(self):
        self._timer.stop()
        self.rejected.emit()


class SketchDialog(QFrame):
    """The small Sketch panel: plane selection and display toggles."""
    accepted = Signal()
    rejected = Signal()
    planePicked = Signal(object)

    def __init__(self, main, feature, has_plane):
        super().__init__(main.viewport)
        self.setObjectName("featureDialog")
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
        self.setFixedWidth(260)
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
