"""Interactive sketch editing: drawing tools, snapping, inference, dimensions, constraints, dragging."""
from __future__ import annotations

import math

import numpy as np
from OpenGL import GL
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPen, QPainterPath, QPolygonF, QFontMetricsF
from PySide6.QtWidgets import QLineEdit

from .. import kernel as K
from ..sketch import Sketch, ORIGIN, DIMENSIONS, CONSTRAINT_LABELS
from . import theme
from . import boxsel
from .icons import CONSTRAINT_GLYPHS
from .numparse import parse_number, fmt, has_vars
from .. import sketch_ops as OPS

DRAW_TOOLS = {"line", "rect", "center_rect", "circle", "arc3", "arc_center", "point", "spline", "ellipse", "text"}

# constraint tool -> (description, list of acceptable selection signatures)
CONSTRAINT_TOOLS = {
    "coincident": "Select two points, or a point and a curve",
    "horizontal": "Select a line or two points",
    "vertical": "Select a line or two points",
    "parallel": "Select two lines",
    "perpendicular": "Select two lines",
    "tangent": "Select a line and an arc/circle, or two arcs/circles",
    "equal": "Select two lines or two arcs/circles",
    "midpoint": "Select a point and a line",
    "concentric": "Select two arcs/circles",
    "symmetric": "Select two points (or two circles/lines) and the line they're symmetric about",
    "fix": "Select points or curves to fix in place",
}

TOOL_HINTS = {
    "select": "Click to select; drag points or curves to move them. Double-click a dimension to edit it.",
    "line": "Click to start a line, click again to finish it. Lines chain — press Esc to stop.",
    "rect": "Click two opposite corners.",
    "center_rect": "Click the centre, then a corner.",
    "circle": "Click the centre, then click to set the radius.",
    "arc3": "Click the start point, the end point, then a point on the arc.",
    "arc_center": "Click the centre, the start point, then sweep to the end point.",
    "point": "Click to place a point (used by Hole).",
    "spline": "Click points for the curve to pass through. Double-click or press Enter to finish; click the first "
              "point again to close it into a loop.",
    "ellipse": "Click the centre, then the end of the long axis, then click to set the width.",
    "text": "Click where the text should start (its bottom-left), then type the text.",
    "dimension": "Click a line, circle or two items, then click to place the dimension.",
    "use": "Click part edges to project them into the sketch.",
    "trim": "Click a segment of a line to remove it up to the nearest intersections.",
    "sk_fillet": "Click a corner where two lines meet, then type the radius.",
    "offset": "Click a line, arc or circle to offset its whole chain; type the distance (negative = other side).",
    "slot": "Click the two end centres of the slot, then type its width.",
    "sk_mirror": "Select geometry, then click the line to mirror it about.",
    "sk_lpattern": "Select geometry, then click a line for the direction; type the count and spacing.",
    "sk_cpattern": "Select geometry, then click the centre point; type the count and angle.",
}
PICK_FIRST_TOOLS = {"sk_mirror", "sk_lpattern", "sk_cpattern"}   # keep the selection when chosen


class Sketcher:
    def __init__(self, plane: K.Plane, sketch: Sketch, fid: str, on_change, on_tool_changed=None):
        self.plane = plane
        self.sk = sketch
        self.fid = fid
        self.on_change = on_change
        self.on_tool_changed = on_tool_changed
        self.tool = "select"
        self.construction = False
        self.selection: list = []          # ids of points / entities / constraints
        self.hover = None
        self.show_regions = True
        self.show_constraints = True
        self.variables = {}          # #name -> value, from the features before this sketch
        self.undo_stack: list = []
        self.redo_stack: list = []
        self._reset_tool_state()
        self._dim_rects = {}
        self._glyph_rects = []
        self._editor: QLineEdit | None = None
        self._drag = None
        self._box_start = None      # rubber-band selection: press point (screen) while dragging on empty space
        self._box = None
        self.cursor_uv = None
        self.snap_info = None
        self.infer = None
        self.infer_tan = None
        self.sk.solve()

    # ================================================================== state
    def _reset_tool_state(self):
        self.temp = []          # clicked positions / ids for the active tool
        self.chain_anchor = None
        self.arc_sweep = 0.0
        self._last_ang = None
        self.cons_picks = []
        self.dim_first = None
        self.dim_second = None
        self._infer_src = []    # points / line midpoints hovered recently (alignment sources)
        self._first_cons = []   # inference constraints of the first click of a two-click tool
        self.infer_hits = []
        self._infer_cons = []

    def set_tool(self, tool):
        self._end_editor()
        if tool in CONSTRAINT_TOOLS and self.selection and self._apply_constraint(tool, self.selection):
            # selection already satisfied the constraint: apply it and stay in select mode
            tool = "select"
            keep = []
        else:
            keep = list(self.selection) if (tool in CONSTRAINT_TOOLS or tool in PICK_FIRST_TOOLS) else []
        self.tool = tool
        self._reset_tool_state()
        self.cons_picks = keep if tool in CONSTRAINT_TOOLS else []
        self.selection = keep
        if self.on_tool_changed:
            self.on_tool_changed(self.tool)

    def hint(self):
        if getattr(self, "_prompt_label", None):
            return self._prompt_label
        if self.tool in CONSTRAINT_TOOLS:
            return CONSTRAINT_TOOLS[self.tool]
        if self.tool == "dimension" and getattr(self, "dim_first", None) is not None:
            if self.dim_second is not None:
                return "Click where the dimension should go."
            return "Click a second item (or a part edge) to measure to — or click empty space to place the dimension."
        return TOOL_HINTS.get(self.tool, "")

    def push_undo(self):
        self.undo_stack.append(self.sk.to_dict())
        self.redo_stack.clear()
        if len(self.undo_stack) > 300:
            self.undo_stack.pop(0)

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(self.sk.to_dict())
            self._replace(Sketch.from_dict(self.undo_stack.pop()))

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(self.sk.to_dict())
            self._replace(Sketch.from_dict(self.redo_stack.pop()))

    def _replace(self, sk):
        self.sk = sk
        self.selection = []
        self._reset_tool_state()
        self.sk.solve()
        self.on_change(True)

    def changed(self, final=True):
        self.sk.solve()
        self.on_change(final)

    # ================================================================== coordinates & hits
    def uv(self, vp, x, y):
        w = vp.ray_plane(x, y, self.plane)
        if w is None:
            return None
        return np.array(self.plane.to_local(w))

    def scr(self, vp, uv):
        s = vp.cam.to_screen(self.plane.to_world(float(uv[0]), float(uv[1])))
        return np.array([s[0], s[1]])

    def hit(self, vp, x, y, kinds=("point", "entity", "constraint"), exclude=()):
        if "constraint" in kinds:
            for cid, r in self._dim_rects.items():
                if r.contains(QPointF(x, y)) and cid not in exclude:
                    return ("constraint", cid)
            for r, cid in self._glyph_rects:
                if r.contains(QPointF(x, y)) and cid not in exclude:
                    return ("constraint", cid)
        if "point" in kinds:
            best = (8.0, None)
            for pid, p in self.sk.points.items():
                if pid in exclude:
                    continue
                s = self.scr(vp, p)
                d = math.hypot(s[0] - x, s[1] - y)
                if d < best[0]:
                    best = (d, pid)
            if best[1]:
                return ("point", best[1])
        if "entity" in kinds:
            best = (7.0, None)
            for eid, ent in self.sk.entities.items():
                if eid in exclude:
                    continue
                if ent["type"] == "text":            # text: anywhere inside its box
                    box = np.array([self.scr(vp, p) for p in self.sk.text_box(eid)])
                    if _inside_poly(box[:-1], (x, y)):
                        if 2.0 < best[0]:
                            best = (2.0, eid)
                        continue
                for pts in self.sk.polylines(eid, 64):
                    if len(pts) < 2:
                        continue
                    S = np.array([self.scr(vp, p) for p in pts])
                    A, B = S[:-1], S[1:]
                    ab = B - A
                    L2 = np.maximum((ab ** 2).sum(1), 1e-12)
                    t = np.clip(((x - A[:, 0]) * ab[:, 0] + (y - A[:, 1]) * ab[:, 1]) / L2, 0, 1)
                    d = np.hypot(A[:, 0] + t * ab[:, 0] - x, A[:, 1] + t * ab[:, 1] - y).min()
                    if d < best[0]:
                        best = (d, eid)
            if best[1]:
                return ("entity", best[1])
        return None

    def _id_of(self, h):
        return h[1] if h else None

    def snap(self, vp, x, y, anchor_uv=None, exclude=()):
        """Returns (uv, snap) where snap is ('point', pid) / ('on', eid) / None.
        Also sets self.infer to 'H'/'V' when the rubber band is near horizontal/vertical."""
        uv = self.uv(vp, x, y)
        self.infer = None
        self.infer_hits = []        # [(source uv, "H"/"V")] drawn as yellow dotted guides
        self._infer_cons = []       # [("horizontal"/"vertical", source ref)] applied to the new point on click
        self.infer_tan = None       # circle / arc the line being drawn is tangent to
        if uv is None:
            return None, None
        h = self.hit(vp, x, y, kinds=("point",), exclude=exclude)
        if h:
            self._acquire_source(h[1], anchor_uv)
            return np.array(self.sk.points[h[1]]), h
        mid = self._hit_midpoint(vp, x, y, exclude)
        if mid:
            self._acquire_source(("mid", mid[0]), anchor_uv)
            return mid[1], ("mid", mid[0])
        if self.tool == "line" and anchor_uv is not None:
            tan = self._tangent_infer(vp, x, y, uv, np.asarray(anchor_uv, float), exclude)
            if tan:
                t_uv, eid, kind = tan
                self.infer_tan = eid
                self.infer_hits.append((np.asarray(anchor_uv, float), "T"))
                return t_uv, (("tan", eid) if kind == "on" else None)
        # candidate alignment sources: the line's start point first, then points/midpoints hovered earlier
        cands = []
        if anchor_uv is not None:
            cands.append((None, np.asarray(anchor_uv, float)))
        for ref in self._infer_src:
            s_uv = self._src_uv(ref)
            if s_uv is not None:
                cands.append((ref, s_uv))
        pick = {}
        for ref, s_uv in cands:
            axis = self._axis_hit(vp, s_uv, x, y)
            if axis and axis not in pick:
                pick[axis] = (ref, s_uv)
        if pick:
            u, v = uv
            if "H" in pick:
                v = pick["H"][1][1]
            if "V" in pick:
                u = pick["V"][1][0]
            uv = np.array([u, v])
            for axis, (ref, s_uv) in pick.items():
                self.infer_hits.append((s_uv, axis))
                if ref is None:
                    self.infer = axis       # the line being drawn gets its own horizontal/vertical constraint
                else:
                    self._infer_cons.append(("horizontal" if axis == "H" else "vertical", ref))
        h = self.hit(vp, x, y, kinds=("entity",), exclude=exclude)
        if h and self.sk.entities[h[1]]["type"] != "point" and not pick:
            uv = self._project_on(h[1], uv)
            return uv, ("on", h[1])
        return uv, None

    def _tangent_infer(self, vp, x, y, uv, A, exclude=()):
        """Tangent inference for the line being drawn from A: (uv, circle/arc id, "dir"|"on") or None.
        - starting at an arc's end, moving along the arc's tangent there snaps onto that tangent;
        - hovering near the point of a circle / arc where a line from A would touch it snaps to that point."""
        sk = self.sk
        sa = self.scr(vp, A)
        m = np.array([x - sa[0], y - sa[1]])
        nm = np.linalg.norm(m)
        start = self.chain_anchor if isinstance(self.chain_anchor, str) else None
        if start in sk.points and nm > 12:
            for eid, e in sk.entities.items():
                if e["type"] != "arc" or eid in exclude or start not in (e["p1"], e["p2"]):
                    continue
                rv = A - sk.P(e["c"])
                n = np.linalg.norm(rv)
                if n < 1e-9:
                    continue
                t = np.array([-rv[1], rv[0]]) / n
                st = self.scr(vp, A + t) - sa
                if np.linalg.norm(st) > 1e-9 and abs(_cross2(st / np.linalg.norm(st), m / nm)) < 0.05:
                    return A + t * float((uv - A) @ t), eid, "dir"
        best = (18.0, None)
        for eid, e in sk.entities.items():
            if e["type"] not in ("circle", "arc") or eid in exclude:
                continue
            c = sk.P(e["c"])
            r = sk.radius(eid)
            d = np.linalg.norm(A - c)
            if d <= r * 1.001 or r < 1e-9:
                continue
            base = math.atan2(A[1] - c[1], A[0] - c[0])
            ang = math.acos(r / d)
            for sgn in (1, -1):
                T = c + r * np.array([math.cos(base + sgn * ang), math.sin(base + sgn * ang)])
                sT = self.scr(vp, T)
                dist = math.hypot(sT[0] - x, sT[1] - y)
                if dist < best[0]:
                    best = (dist, (T, eid))
        if best[1]:
            return best[1][0], best[1][1], "on"
        return None

    # ---- inference sources: points / line midpoints the cursor has recently hovered over
    def _acquire_source(self, ref, anchor_uv=None):
        if anchor_uv is not None and not isinstance(ref, tuple) \
                and np.linalg.norm(self.sk.P(ref) - np.asarray(anchor_uv, float)) < 1e-9:
            return          # the line's own start point is already an inference source
        if ref in self._infer_src:
            self._infer_src.remove(ref)
        self._infer_src.append(ref)
        del self._infer_src[:-3]

    def _src_uv(self, ref):
        sk = self.sk
        if isinstance(ref, tuple):
            e = sk.entities.get(ref[1])
            if not e or e["type"] != "line":
                return None
            return (sk.P(e["p1"]) + sk.P(e["p2"])) / 2
        return sk.P(ref) if ref in sk.points else None

    def _axis_hit(self, vp, src_uv, x, y):
        """'H' / 'V' when the cursor is (on screen) nearly horizontal / vertical from src_uv."""
        sa = self.scr(vp, src_uv)
        m = np.array([x - sa[0], y - sa[1]])
        nm = np.linalg.norm(m)
        if nm <= 12:
            return None
        su = self.scr(vp, src_uv + np.array([1.0, 0])) - sa
        sv = self.scr(vp, src_uv + np.array([0, 1.0])) - sa
        if abs(_cross2(su / np.linalg.norm(su), m / nm)) < 0.06:
            return "H"
        if abs(_cross2(sv / np.linalg.norm(sv), m / nm)) < 0.06:
            return "V"
        return None

    def _hit_midpoint(self, vp, x, y, exclude=()):
        best = (8.0, None)
        for eid, e in self.sk.entities.items():
            if eid in exclude or e["type"] != "line":
                continue
            m = (self.sk.P(e["p1"]) + self.sk.P(e["p2"])) / 2
            s = self.scr(vp, m)
            d = math.hypot(s[0] - x, s[1] - y)
            if d < best[0]:
                best = (d, (eid, m))
        return best[1]

    def _source_point(self, ref):
        """The sketch point behind an inference source; a line midpoint gets a construction point."""
        sk = self.sk
        if isinstance(ref, tuple):
            eid = ref[1]
            e = sk.entities.get(eid)
            if not e:
                return None
            for c in sk.constraints:
                if c["type"] == "midpoint" and c["refs"][1] == eid:
                    return c["refs"][0]
            m = sk.add_point(*((sk.P(e["p1"]) + sk.P(e["p2"])) / 2))
            sk.add_constraint("midpoint", m, eid)
            sk.add_point_entity(m)
            return m
        return ref if ref in sk.points else None

    def _project_on(self, eid, uv):
        e = self.sk.entities[eid]
        if e["type"] == "line":
            a, b = self.sk.P(e["p1"]), self.sk.P(e["p2"])
            d = b - a
            t = np.clip(((uv - a) @ d) / max(d @ d, 1e-12), 0, 1)
            return a + t * d
        if e["type"] in ("circle", "arc"):
            c = self.sk.P(e["c"])
            r = self.sk.radius(eid)
            v = uv - c
            n = np.linalg.norm(v)
            return c + (v / n * r if n > 1e-12 else np.array([r, 0]))
        return uv

    def _point_at(self, uv, snap, cons=None):
        """Create (or reuse) a point for a click; returns pid. `cons` are the horizontal/vertical
        inferences (from snap) to apply to a newly created point."""
        if snap and snap[0] == "point":
            return snap[1]
        pid = self.sk.add_point(*uv)
        if snap and snap[0] in ("on", "tan"):
            self.sk.add_constraint("point_on", pid, snap[1])
        elif snap and snap[0] == "mid":
            self.sk.add_constraint("midpoint", pid, snap[1])
        for ctype, ref in (cons or []):
            src = self._source_point(ref)
            if src and src != pid:
                self.sk.add_constraint(ctype, src, pid)
        return pid

    # ================================================================== mouse
    def mouse_press(self, vp, x, y, e) -> bool:
        self._end_editor()
        if getattr(self, "_flashing", False):
            self._flashing = False                 # a warning stays up until the next click
            self._prompt_label = None
        t = self.tool
        mods = e.modifiers()
        if t == "select":
            h = self.hit(vp, x, y)
            if h is None:
                if not (mods & (Qt.ShiftModifier | Qt.ControlModifier)):
                    self.selection = []
                self._drag = None
                self._box_start = (x, y)
                self._box = None
                return True
            hid = h[1]
            if hid not in self.selection:
                self.selection.append(hid)
                self._drag = {"id": hid, "kind": h[0], "start": self.uv(vp, x, y), "pushed": False, "toggle": False}
            else:
                self._drag = {"id": hid, "kind": h[0], "start": self.uv(vp, x, y), "pushed": False, "toggle": True}
            return True
        if t in CONSTRAINT_TOOLS:
            h = self.hit(vp, x, y, kinds=("point", "entity"))
            if h:
                if h[1] in self.cons_picks:
                    self.cons_picks.remove(h[1])
                else:
                    self.cons_picks.append(h[1])
                self.selection = list(self.cons_picks)
                if self._apply_constraint(t, self.cons_picks):
                    self.cons_picks = []
                    self.selection = []
            return True
        if t == "dimension":
            return self._dimension_click(vp, x, y)
        if t == "use":
            pk = vp.pick(x, y, kinds={"edge"})
            if pk:
                self._use_edge(vp, pk)
            return True
        if t == "trim":
            h = self.hit(vp, x, y, kinds=("entity",))
            if h:
                self._trim(h[1], self.uv(vp, x, y))
            return True
        if t in DRAW_TOOLS:
            return self._draw_click(vp, x, y)
        if t in ("sk_fillet", "offset", "slot", "sk_mirror", "sk_lpattern", "sk_cpattern"):
            return self._ops_click(vp, x, y, e)
        return False

    def mouse_drag(self, vp, x, y, e):
        if self.tool in DRAW_TOOLS:
            self.mouse_move(vp, x, y, e)
            return
        if self._box_start is not None and self.tool == "select":
            if self._box is not None or abs(x - self._box_start[0]) + abs(y - self._box_start[1]) > 3:
                self._box = (self._box_start[0], self._box_start[1], x, y)
            return
        d = self._drag
        if not d or self.tool != "select":
            return
        uv = self.uv(vp, x, y)
        if uv is None:
            return
        if not d["pushed"]:
            self.push_undo()
            d["pushed"] = True
            d["orig"] = self.sk.to_dict()
        sk = self.sk
        kind, i = d["kind"], d["id"]
        delta = uv - d["start"]
        if kind == "constraint":
            for c in sk.constraints:
                if c["id"] == i:
                    c["pos"] = uv.tolist()
            self.on_change(False)
            return
        # restore original then apply the drag delta (avoids drift)
        base = Sketch.from_dict(d["orig"])
        sk.points = base.points
        for eid, ent in base.entities.items():
            if eid in sk.entities and "r" in ent:
                sk.entities[eid]["r"] = ent["r"]
        weights = {}
        if kind == "point":
            if i == ORIGIN:
                return
            sk.points[i] = (np.array(base.points[i]) + delta).tolist()
            weights = {(i, 0): 1e3, (i, 1): 1e3}
        elif kind == "entity":
            ent = sk.entities[i]
            if ent["type"] == "circle":
                c = np.array(base.points[ent["c"]])
                ent["r"] = float(max(np.linalg.norm(uv - c), 1e-3))
                weights = {(i, "r"): 1e3}
            else:
                for pid in sk.entity_points(i):
                    if pid == ORIGIN:
                        continue
                    sk.points[pid] = (np.array(base.points[pid]) + delta).tolist()
                    weights[(pid, 0)] = 1e3
                    weights[(pid, 1)] = 1e3
        sk.solve(weights=weights)
        if not sk.status.get("ok", True):
            # couldn't satisfy constraints with the dragged geometry there: fall back to the original
            sk.points = Sketch.from_dict(d["orig"]).points
            sk.solve()
        self.on_change(False)

    def _box_pick(self, vp, box, additive):
        """Select the curves and loose points inside a dragged box: left-to-right takes what is fully enclosed,
        right-to-left also what the box touches."""
        rect = boxsel.norm_rect(*box)
        crossing = boxsel.is_crossing(*box)
        sk = self.sk
        ids, used = [], set()
        for eid, ent in sk.entities.items():
            if ent["type"] == "point":
                continue
            used.update(sk.entity_points(eid))
            pts = sk.polyline(eid, 64)
            if len(pts) < 2:
                continue
            S = np.array([self.scr(vp, q) for q in pts])
            if boxsel.polyline_selected(S, rect, crossing):
                ids.append(eid)
        for pid, q in sk.points.items():
            if pid in used:
                continue
            sx, sy = self.scr(vp, q)
            if rect[0] <= sx <= rect[2] and rect[1] <= sy <= rect[3]:
                ids.append(pid)
        if not additive:
            self.selection = []
        for i in ids:
            if i not in self.selection:
                self.selection.append(i)

    def mouse_release(self, vp, x, y, e, moved):
        if self._box_start is not None:
            box, self._box, self._box_start = self._box, None, None
            if box is not None and self.tool == "select":
                self._box_pick(vp, box, bool(e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)))
            return
        d = self._drag
        self._drag = None
        if self.tool == "select" and d:
            if d.get("pushed"):
                self.changed(True)
            elif d.get("toggle") and not moved:
                if d["id"] in self.selection:
                    self.selection.remove(d["id"])
        if self.tool in DRAW_TOOLS and moved and self.temp and self.tool in ("circle", "rect", "center_rect", "line"):
            # click-drag-release drawing
            self._draw_click(vp, x, y)

    def mouse_move(self, vp, x, y, e):
        anchor = None
        if self.tool == "line" and self.chain_anchor is not None:
            anchor = self.sk.P(self.chain_anchor) if isinstance(self.chain_anchor, str) else self.chain_anchor
        if self.tool == "slot" and self.temp:
            anchor = self.temp[0][0]
        uv, snap = (self.snap(vp, x, y, anchor) if self.tool in DRAW_TOOLS or self.tool in ("dimension", "slot")
                    else (self.uv(vp, x, y), None))
        self.cursor_uv = uv
        self.snap_info = snap
        if self.tool == "arc_center" and len(self.temp) == 2 and uv is not None:
            c = self.temp[0][0]
            ang = math.atan2(uv[1] - c[1], uv[0] - c[0])
            if self._last_ang is not None:
                da = (ang - self._last_ang + math.pi) % (2 * math.pi) - math.pi
                self.arc_sweep += da
            self._last_ang = ang
        if self.tool == "select" or self.tool in CONSTRAINT_TOOLS or self.tool in (
                "sk_fillet", "offset", "sk_mirror", "sk_lpattern", "sk_cpattern"):
            kinds = ("point", "entity", "constraint") if self.tool == "select" else (
                ("point",) if self.tool == "sk_fillet" else ("entity",) if self.tool == "offset" else ("point", "entity"))
            self.hover = self._id_of(self.hit(vp, x, y, kinds))
        else:
            self.hover = None
        if self.tool in ("dimension", "use"):
            # show which part edge a click would pick up
            pk = None
            sh = self.hit(vp, x, y, kinds=("point", "entity")) if self.tool == "dimension" else None
            if self.tool == "use" or (sh is None and (self.tool != "dimension" or self.dim_second is None)):
                pk = vp.pick(x, y, kinds={"edge"})
            vp.hover = pk
            # the dimension follows the mouse once its items are picked, so the next click clearly places it
            self._dim_over_item = sh is not None or pk is not None

    def double_click(self, vp, x, y, e):
        if self.tool == "spline" and self.temp:
            self._finish_spline(vp)
            return
        h = self.hit(vp, x, y, kinds=("entity",)) if self.tool == "select" else None
        if h and self.sk.entities.get(h[1], {}).get("type") == "text":
            self.edit_text(vp, h[1])
            return
        h = self.hit(vp, x, y, kinds=("constraint",))
        if h:
            c = self._constraint(h[1])
            if c and c["type"] in DIMENSIONS:
                self.edit_dimension(vp, c)
                return
        if self.tool == "line":
            self.chain_anchor = None
            self.temp = []

    def escape(self):
        had_editor = self._editor is not None
        self._end_editor()
        if self.tool == "spline" and len(self.temp) >= 2:
            self._finish_spline()             # Esc keeps the curve drawn so far, like the line chain
            return
        if self.temp or self.chain_anchor is not None or self.dim_first or self.cons_picks:
            self._reset_tool_state()
            self.selection = []
        elif had_editor:
            pass                      # first Esc just dismisses the dimension box
        elif self.tool != "select":
            self.set_tool("select")
        else:
            self.selection = []

    def key_press(self, vp, e) -> bool:
        k = e.key()
        if k == Qt.Key_Escape:
            self.escape()
            return True
        if k in (Qt.Key_Return, Qt.Key_Enter) and self.tool == "spline" and self.temp:
            self._finish_spline(vp)
            return True
        if k in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_selection()
            return True
        if k == Qt.Key_Space and not e.modifiers():
            self.selection = []                  # Space deselects everything
            self.cons_picks = []
            return True
        if e.modifiers() & Qt.ControlModifier:
            if k == Qt.Key_Z:
                self.undo()
                return True
            if k == Qt.Key_Y:
                self.redo()
                return True
            if k == Qt.Key_A:
                self.selection = list(self.sk.entities)
                return True
            return False
        shortcuts = {Qt.Key_L: "line", Qt.Key_R: "rect", Qt.Key_C: "circle", Qt.Key_A: "arc3",
                     Qt.Key_D: "dimension", Qt.Key_I: "coincident", Qt.Key_H: "horizontal",
                     Qt.Key_V: "vertical", Qt.Key_E: "equal", Qt.Key_T: "tangent", Qt.Key_S: "select",
                     Qt.Key_P: "point", Qt.Key_U: "use", Qt.Key_O: "offset"}
        if k == Qt.Key_Q:
            self.toggle_construction()
            return True
        if k in shortcuts and not e.modifiers():
            self.set_tool(shortcuts[k])
            return True
        return False

    # ================================================================== editing commands
    def delete_selection(self):
        if not self.selection:
            return
        self.push_undo()
        self.sk.delete(self.selection)
        self.selection = []
        self.changed(True)

    def toggle_construction(self):
        ents = [i for i in self.selection if i in self.sk.entities]
        if ents:
            self.push_undo()
            val = not all(self.sk.entities[i].get("construction") for i in ents)
            for i in ents:
                self.sk.entities[i]["construction"] = val
            self.changed(True)
        else:
            self.construction = not self.construction
        if self.on_tool_changed:
            self.on_tool_changed(self.tool)

    def _constraint(self, cid):
        return next((c for c in self.sk.constraints if c["id"] == cid), None)

    def _etype(self, i):
        if i in self.sk.points:
            return "point"
        if i in self.sk.entities:
            t = self.sk.entities[i]["type"]
            return "point" if t == "point" else t
        return None

    def _as_point(self, i):
        if i in self.sk.entities and self.sk.entities[i]["type"] == "point":
            return self.sk.entities[i]["p"]
        return i

    def _apply_constraint(self, tool, picks) -> bool:
        picks = [p for p in picks if p in self.sk.points or p in self.sk.entities]
        types = [self._etype(p) for p in picks]
        sk = self.sk
        curves = ("circle", "arc")
        made = []
        if tool == "fix" and picks:
            for p in picks:
                pts = [self._as_point(p)] if types[picks.index(p)] == "point" else sk.entity_points(p)
                for pid in pts:
                    if pid != ORIGIN:
                        made.append(("fix", [pid], list(sk.points[pid])))
                if p in sk.entities and sk.entities[p]["type"] == "circle":
                    made.append(("radius", [p], sk.entities[p]["r"]))
        elif len(picks) == 1 and tool in ("horizontal", "vertical") and types[0] == "line":
            made.append((tool, [picks[0]], None))
        elif len(picks) == 2:
            a, b = picks
            ta, tb = types
            if tool == "coincident":
                if ta == "point" and tb == "point":
                    made.append(("coincident", [self._as_point(a), self._as_point(b)], None))
                elif ta == "point" and tb in ("line",) + curves:
                    made.append(("point_on", [self._as_point(a), b], None))
                elif tb == "point" and ta in ("line",) + curves:
                    made.append(("point_on", [self._as_point(b), a], None))
                elif ta in curves and tb in curves:
                    made.append(("concentric", [a, b], None))
            elif tool in ("horizontal", "vertical") and ta == "point" and tb == "point":
                made.append((tool, [self._as_point(a), self._as_point(b)], None))
            elif tool in ("parallel", "perpendicular") and ta == "line" and tb == "line":
                made.append((tool, [a, b], None))
            elif tool == "equal" and ((ta == tb == "line") or (ta in curves and tb in curves)):
                made.append(("equal", [a, b], None))
            elif tool == "concentric" and ta in curves + ("point",) and tb in curves + ("point",):
                made.append(("concentric", [a if ta in curves else self._as_point(a),
                                            b if tb in curves else self._as_point(b)], None))
            elif tool == "midpoint":
                if ta == "point" and tb == "line":
                    made.append(("midpoint", [self._as_point(a), b], None))
                elif tb == "point" and ta == "line":
                    made.append(("midpoint", [self._as_point(b), a], None))
            elif tool == "tangent":
                if (ta == "line" and tb in curves) or (tb == "line" and ta in curves) or (ta in curves and tb in curves):
                    val = None
                    if ta in curves and tb in curves:
                        d = np.linalg.norm(sk.P(sk.entities[a]["c"]) - sk.P(sk.entities[b]["c"]))
                        internal = d < max(sk.radius(a), sk.radius(b))
                        val = -1.0 if internal else 1.0
                    made.append(("tangent", [a, b], val))
        if tool == "symmetric" and len(picks) == 3:
            lines_ = [p for p, t in zip(picks, types) if t == "line"]
            axis = picks[2] if types[2] == "line" else (lines_[-1] if lines_ else None)
            others = [(p, t) for p, t in zip(picks, types) if p != axis]
            if axis and len(others) == 2:
                (a, ta), (b, tb) = others
                if ta == "point" and tb == "point":
                    made.append(("symmetric", [self._as_point(a), self._as_point(b), axis], None))
                elif ta in curves and tb in curves:
                    made.append(("symmetric", [sk.entities[a]["c"], sk.entities[b]["c"], axis], None))
                    made.append(("equal", [a, b], None))
                elif ta == "line" and tb == "line":
                    ea, eb = sk.entities[a], sk.entities[b]
                    A0, A1 = sk.P(ax0 := sk.entities[axis]["p1"]), sk.P(sk.entities[axis]["p2"])
                    d = (A1 - A0) / max(np.linalg.norm(A1 - A0), 1e-12)
                    refl = lambda q: A0 + 2 * ((q - A0) @ d) * d - (q - A0)
                    straight = np.linalg.norm(refl(sk.P(ea["p1"])) - sk.P(eb["p1"]))
                    crossed = np.linalg.norm(refl(sk.P(ea["p1"])) - sk.P(eb["p2"]))
                    q1, q2 = (eb["p1"], eb["p2"]) if straight <= crossed else (eb["p2"], eb["p1"])
                    made.append(("symmetric", [ea["p1"], q1, axis], None))
                    made.append(("symmetric", [ea["p2"], q2, axis], None))
        if not made:
            return False
        self.push_undo()
        for t, refs, val in made:
            sk.add_constraint(t, *refs, value=val)
        self.changed(True)
        return True

    # ------------------------------------------------------------------ drawing tools
    def _draw_click(self, vp, x, y):
        t = self.tool
        anchor = None
        if t == "line" and self.chain_anchor is not None:
            anchor = self.sk.P(self.chain_anchor)
        uv, snap = self.snap(vp, x, y, anchor)
        if uv is None:
            return True
        icons = list(self._infer_cons)      # alignment inferences for the point placed by this click
        self._infer_src = []
        sk = self.sk
        cons = self.construction
        if t == "point":
            self.push_undo()
            pid = self._point_at(uv, snap, icons)
            sk.add_point_entity(pid)
            self.changed(True)
            return True
        if t == "line":
            if self.chain_anchor is None:
                self.push_undo()
                self.chain_anchor = self._point_at(uv, snap, icons)
                self._pending_line_start_new = snap is None
                self.temp = ["started"]
                return True
            a = self.chain_anchor
            if np.linalg.norm(uv - sk.P(a)) < 1e-9:
                return True
            if not self.temp:
                self.push_undo()
            b = self._point_at(uv, snap, icons)
            if a == b:
                return True
            lid = sk.add_line(a, b, cons)
            if self.infer_tan and self.infer_tan in sk.entities:
                sk.add_constraint("tangent", lid, self.infer_tan)
            if self.infer == "H":
                sk.add_constraint("horizontal", lid)
            elif self.infer == "V":
                sk.add_constraint("vertical", lid)
            self.changed(True)
            pa, pb = sk.P(a), sk.P(b)
            d = pb - pa
            n = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-12)
            self._offer_dims(vp, [("distance", [lid], float(np.linalg.norm(d)),
                                   (pa + pb) / 2 + n * 14 * vp.cam.scale)])
            if snap and snap[0] == "point":
                self.chain_anchor = None    # closed onto existing geometry: end chain
                self.temp = []
            else:
                self.chain_anchor = b
                self.temp = []
            return True
        if t in ("rect", "center_rect"):
            if not self.temp:
                self.temp = [(uv, snap)]
                self._first_cons = icons
                return True
            (u0, s0) = self.temp[0]
            if np.linalg.norm(uv - u0) < 1e-9:
                return True
            self.push_undo()
            if t == "rect":
                a = self._point_at(u0, s0, self._first_cons)
                c = self._point_at(uv, snap, icons)
                b = sk.add_point(uv[0], u0[1])
                d = sk.add_point(u0[0], uv[1])
            else:
                ctr = self._point_at(u0, s0, self._first_cons)
                half = uv - u0
                a = sk.add_point(*(u0 - half))
                c = self._point_at(uv, snap, icons)
                b = sk.add_point(u0[0] + half[0], u0[1] - half[1])
                d = sk.add_point(u0[0] - half[0], u0[1] + half[1])
            l1 = sk.add_line(a, b, cons)
            l2 = sk.add_line(b, c, cons)
            l3 = sk.add_line(c, d, cons)
            l4 = sk.add_line(d, a, cons)
            sk.add_constraint("horizontal", l1)
            sk.add_constraint("horizontal", l3)
            sk.add_constraint("vertical", l2)
            sk.add_constraint("vertical", l4)
            if t == "center_rect":
                diag = sk.add_line(a, c, True)
                sk.add_constraint("midpoint", ctr, diag)
            self.temp = []
            self.changed(True)
            pa, pc = sk.P(a), sk.P(c)
            lo, hi = np.minimum(pa, pc), np.maximum(pa, pc)
            gap = 14 * vp.cam.scale
            self._offer_dims(vp, [
                ("distance", [l1], float(hi[0] - lo[0]), np.array([(lo[0] + hi[0]) / 2, hi[1] + gap])),
                ("distance", [l2], float(hi[1] - lo[1]), np.array([hi[0] + gap * 2, (lo[1] + hi[1]) / 2])),
            ])
            return True
        if t == "circle":
            if not self.temp:
                self.temp = [(uv, snap)]
                self._first_cons = icons
                return True
            c_uv, cs = self.temp[0]
            r = float(np.linalg.norm(uv - c_uv))
            if r < 1e-6:
                return True
            self.push_undo()
            c = self._point_at(c_uv, cs, self._first_cons)
            cid = sk.add_circle(c, r, cons)
            self.temp = []
            self.changed(True)
            ctr = sk.P(c)
            self._offer_dims(vp, [("diameter", [cid], 2 * sk.radius(cid), ctr + np.array([r, r]) * 0.9)])
            return True
        if t == "arc3":
            self.temp.append((uv, snap))
            if len(self.temp) < 3:
                return True
            (p1, s1), (p2, s2), (p3, s3) = self.temp
            ctr = _circumcenter(p1, p2, p3)
            self.temp = []
            if ctr is None:
                return True
            self.push_undo()
            if not _ccw_contains(ctr, p1, p2, p3):
                p1, s1, p2, s2 = p2, s2, p1, s1
            c = sk.add_point(*ctr)
            a = self._point_at(p1, s1)
            b = self._point_at(p2, s2)
            aid = sk.add_arc(c, a, b, cons)
            # keep the third point: a real point on the arc, so it can be dragged, attached to other geometry
            # or dimensioned, and the arc follows (the arc is defined by its three points, not just two + radius)
            if not (s3 and s3[0] == "point" and s3[1] in (a, b)):
                m = self._point_at(p3, s3)
                if m not in (a, b, c):
                    sk.add_constraint("point_on", m, aid)
                    if m not in sk.points or not any(m in sk.entity_points(e) for e in sk.entities):
                        sk.add_point_entity(m)
            self.changed(True)           # no radius prompt: the arc stays defined by its three points
            return True
        if t == "spline":
            if self.temp:
                first = self.scr(vp, self.temp[0][0])
                if len(self.temp) >= 3 and math.hypot(first[0] - x, first[1] - y) < 9:
                    self._finish_spline(vp, closed=True)          # back on the first point: a loop
                    return True
                last = self.scr(vp, self.temp[-1][0])
                if math.hypot(last[0] - x, last[1] - y) < 4:
                    self._finish_spline(vp)                       # the last point clicked again: done
                    return True
            self.temp.append((uv, snap))
            return True
        if t == "ellipse":
            self.temp.append((uv, snap))
            if len(self.temp) == 1:
                self._first_cons = icons
            if len(self.temp) < 3:
                return True
            (cu, cs), (au, as_), _ = self.temp
            self.temp = []
            ax = au - cu
            A = float(np.linalg.norm(ax))
            if A < 1e-9:
                return True
            nrm = np.array([-ax[1], ax[0]]) / A
            r = abs(float((uv - cu) @ nrm))
            if r < 1e-9:
                return True
            self.push_undo()
            c = self._point_at(cu, cs, self._first_cons)
            a = self._point_at(au, as_)
            eid = sk.add_ellipse(c, a, r, cons)
            sk.add_line(c, a, True)                 # the long axis, as construction: dimension or align it
            self.changed(True)
            self._offer_dims(vp, [("radius", [eid], r, sk.P(c) + nrm * r * 1.15)])
            return True
        if t == "text":
            vals = self._text_dialog(vp)
            if vals:
                self.push_undo()
                pid = self._point_at(uv, snap, icons)
                sk.add_text(pid, **vals)
                self.changed(True)
            return True
        if t == "arc_center":
            self.temp.append((uv, snap))
            if len(self.temp) == 2:
                c = self.temp[0][0]
                self._last_ang = math.atan2(uv[1] - c[1], uv[0] - c[0])
                self.arc_sweep = 0.0
            if len(self.temp) < 3:
                return True
            (cu, cs), (p1, s1), (p2, s2) = self.temp
            r = np.linalg.norm(p1 - cu)
            v = p2 - cu
            if np.linalg.norm(v) > 1e-9:
                p2 = cu + v / np.linalg.norm(v) * r
            self.temp = []
            if r < 1e-9:
                return True
            self.push_undo()
            c = self._point_at(cu, cs)
            a = self._point_at(p1, s1)
            b = sk.add_point(*p2) if not (s2 and s2[0] == "point") else s2[1]
            if self.arc_sweep < 0:
                a, b = b, a
            aid = sk.add_arc(c, a, b, cons)
            self.changed(True)
            self._offer_arc(vp, aid)
            return True
        return True

    def _finish_spline(self, vp=None, closed=False):
        """Make the spline from the points clicked so far (at least two; three for a loop)."""
        pts = self.temp
        self.temp = []
        if len(pts) < (3 if closed else 2):
            return False
        self.push_undo()
        ids = []
        for uv, snap in pts:
            pid = self._point_at(uv, snap)
            if not ids or ids[-1] != pid:
                ids.append(pid)
        if len(ids) < (3 if closed else 2):
            self.undo()
            return False
        self.sk.add_spline(ids, closed, self.construction)
        self.changed(True)
        return True

    def _text_dialog(self, vp, e=None):
        """Ask for the text and its look. e: an existing text entity to edit. Returns the values, or None."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QFontComboBox, QDoubleSpinBox,
                                       QCheckBox, QDialogButtonBox, QHBoxLayout)
        from PySide6.QtGui import QFont as _QF
        e = e or {}
        dlg = QDialog(vp.window() if hasattr(vp, "window") else None)
        dlg.setWindowTitle("Edit text" if e else "Text")
        form = QFormLayout(dlg)
        txt = QLineEdit(e.get("text", ""))
        txt.setPlaceholderText("Type the text")
        form.addRow("Text:", txt)
        font = QFontComboBox()
        font.setCurrentFont(_QF(e.get("font", "Arial")))
        form.addRow("Font:", font)
        size = QDoubleSpinBox()
        size.setRange(0.1, 10000)
        size.setDecimals(2)
        size.setSuffix(" mm")
        size.setValue(float(e.get("size", getattr(self, "_last_text_size", 10.0))))
        form.addRow("Size:", size)
        style = QHBoxLayout()
        bold, italic = QCheckBox("Bold"), QCheckBox("Italic")
        bold.setChecked(bool(e.get("bold")))
        italic.setChecked(bool(e.get("italic")))
        style.addWidget(bold)
        style.addWidget(italic)
        style.addStretch(1)
        form.addRow("Style:", style)
        ang = QDoubleSpinBox()
        ang.setRange(-360, 360)
        ang.setSuffix("°")
        ang.setValue(float(e.get("angle", 0.0)))
        form.addRow("Angle:", ang)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        txt.setFocus()
        if dlg.exec() != QDialog.Accepted or not txt.text().strip():
            return None
        self._last_text_size = size.value()
        return {"text": txt.text(), "font": font.currentFont().family(), "size": size.value(),
                "bold": bold.isChecked(), "italic": italic.isChecked(), "angle": ang.value()}

    def edit_text(self, vp, eid):
        e = self.sk.entities.get(eid)
        if e is None or e["type"] != "text":
            return
        vals = self._text_dialog(vp, e)
        if vals:
            self.push_undo()
            e.update(vals)
            self.changed(True)

    # ------------------------------------------------------------------ dimensions
    def _dimension_click(self, vp, x, y):
        h = self.hit(vp, x, y, kinds=("point", "entity"))
        uv = self.uv(vp, x, y)
        if h is None and self.dim_second is None:
            # like Onshape: a part's edge (or corner) can be dimensioned to as well — it is brought into the
            # sketch as fixed construction geometry first
            pk = vp.pick(x, y, kinds={"edge"})
            if pk is not None:
                eid = self._use_edge(vp, pk, construction=True, click=(x, y))
                if eid is not None:
                    h = ("point" if eid in self.sk.points else "entity", eid)
        if self.dim_first is None:
            if h:
                self.dim_first = h[1]
                self.selection = [h[1]]
            vp.status_text = self.hint()
            return True
        if h and h[1] != self.dim_first and self.dim_second is None:
            self.dim_second = h[1]
            self.selection = [self.dim_first, h[1]]
            vp.status_text = self.hint()
            return True
        # placement click
        c = self._create_dimension(self.dim_first, self.dim_second, uv, vp)
        self.dim_first = self.dim_second = None
        self.selection = []
        if not getattr(self, "_flashing", False):
            vp.status_text = self.hint()
        if c:
            self.edit_dimension(vp, c)
        return True

    def _create_dimension(self, a, b, pos, vp=None):
        spec = self._dim_spec(a, b, pos)
        if spec is None:
            return None
        ctype, refs, val = spec
        sk = self.sk
        sk.solve()
        dof_before = sk.status.get("dof", 0)
        saved = sk.to_dict()
        self.push_undo()
        c = sk.add_constraint(ctype, *refs, value=float(val))
        if pos is not None:
            c["pos"] = [float(pos[0]), float(pos[1])]
        sk.solve()
        if not sk.status.get("ok", True) or sk.status.get("dof", 0) >= dof_before:
            # it would fight the other dimensions (or just repeat them): leave the sketch as it was and say so
            self._restore_geometry(saved)
            sk.constraints.remove(c)
            sk.solve()
            self.undo_stack.pop()
            if vp is not None:
                self._flash(vp, "Can't add that dimension — this is already set by other dimensions or "
                                "constraints (it would over-define the sketch)")
            self.on_change(True)
            return None
        self.on_change(True)
        return c

    def _restore_geometry(self, saved):
        base = Sketch.from_dict(saved)
        self.sk.points = base.points
        for eid, ent in base.entities.items():
            if eid in self.sk.entities and "r" in ent:
                self.sk.entities[eid]["r"] = ent["r"]

    def _dim_spec(self, a, b, pos):
        """(type, refs, current value) of the dimension between picks a and b, or None."""
        sk = self.sk
        ta = self._etype(a)
        tb = self._etype(b) if b else None
        ctype, refs, val = None, None, None
        if b is None:
            if ta == "line":
                ctype, refs = "distance", [a]
                e = sk.entities[a]
                val = float(np.linalg.norm(sk.P(e["p2"]) - sk.P(e["p1"])))
            elif ta == "circle":
                ctype, refs, val = "diameter", [a], 2 * sk.radius(a)
            elif ta == "arc":
                ctype, refs, val = "radius", [a], sk.radius(a)
            elif ta == "ellipse":           # half its width; the length is set with its dashed axis line
                ctype, refs, val = "radius", [a], sk.radius(a)
            else:
                return None
        else:
            def ptof(i, t):
                if t == "point":
                    return self._as_point(i)
                if t in ("circle", "arc", "ellipse"):
                    return sk.entities[i]["c"]
                if t == "text":
                    return sk.entities[i]["p"]          # where the text starts
                return None
            if ta == "line" and tb == "line":
                e1, e2 = sk.entities[a], sk.entities[b]
                d1 = sk.P(e1["p2"]) - sk.P(e1["p1"])
                d2 = sk.P(e2["p2"]) - sk.P(e2["p1"])
                cr = abs(_cross2(d1 / np.linalg.norm(d1), d2 / np.linalg.norm(d2)))
                if cr < 1e-3:
                    ctype, refs = "distance", [e2["p1"], a]
                    val = _pt_line_dist(sk.P(e2["p1"]), sk.P(e1["p1"]), sk.P(e1["p2"]))
                else:
                    ctype, refs = "angle", [a, b]
                    cosv = abs(d1 @ d2) / (np.linalg.norm(d1) * np.linalg.norm(d2))
                    dt = (d1 @ d2) / (np.linalg.norm(d1) * np.linalg.norm(d2))
                    val = math.degrees(math.atan2(cr, dt))
            elif "line" in (ta, tb):
                line, other, to = (a, b, tb) if ta == "line" else (b, a, ta)
                p = ptof(other, to)
                if p is None:
                    return None
                e = sk.entities[line]
                ctype, refs = "distance", [p, line]
                val = _pt_line_dist(sk.P(p), sk.P(e["p1"]), sk.P(e["p2"]))
            else:
                pa, pb = ptof(a, ta), ptof(b, tb)
                if pa is None or pb is None:
                    return None
                A, B = sk.P(pa), sk.P(pb)
                lo, hi = np.minimum(A, B), np.maximum(A, B)
                ctype = "distance"
                if pos is not None and abs(A[0] - B[0]) > 1e-6 and abs(A[1] - B[1]) > 1e-6:
                    if lo[0] <= pos[0] <= hi[0] and not (lo[1] <= pos[1] <= hi[1]):
                        ctype = "hdistance"
                    elif lo[1] <= pos[1] <= hi[1] and not (lo[0] <= pos[0] <= hi[0]):
                        ctype = "vdistance"
                refs = [pa, pb]
                val = {"distance": np.linalg.norm(A - B), "hdistance": abs(A[0] - B[0]),
                       "vdistance": abs(A[1] - B[1])}[ctype]
        if ctype is None:
            return None
        return ctype, refs, val

    # ------------------------------------------------------------------ fillet / offset / slot / mirror / patterns
    def _nice(self, vp, px=40):
        raw = vp.cam.scale * px
        mag = 10 ** math.floor(math.log10(max(raw, 1e-9)))
        for m in (1, 2, 5, 10):
            if m * mag >= raw:
                return float(m * mag)
        return float(10 * mag)

    def _ops_click(self, vp, x, y, e):
        t = self.tool
        sk = self.sk
        uv = self.uv(vp, x, y)
        if uv is None:
            return True
        if t == "sk_fillet":
            h = self.hit(vp, x, y, kinds=("point",))
            if not h:
                return True
            pid = h[1]
            lines = [k for k in OPS._users(sk, pid) if sk.entities[k]["type"] == "line"]
            short = min((np.linalg.norm(sk.P(sk.entities[k]["p1"]) - sk.P(sk.entities[k]["p2"])) for k in lines),
                        default=10.0)
            r0 = min(self._nice(vp, 30), short * 0.3)
            self._prompt(vp, sk.P(pid), "Fillet radius", r0, lambda r: OPS.fillet_corner(self.sk, pid, r),
                         expr_target=lambda: self._last_radius_constraint())
            return True
        if t == "offset":
            h = self.hit(vp, x, y, kinds=("entity",))
            if not h or sk.entities[h[1]]["type"] == "point":
                return True
            eid = h[1]
            self._prompt(vp, uv, "Offset distance (negative = other side)", self._nice(vp, 30),
                         lambda d: OPS.offset(self.sk, eid, d), allow_negative=True)
            return True
        if t == "slot":
            _uv, snap = self.snap(vp, x, y, self.temp[0][0] if self.temp else None)
            if not self.temp:
                self.temp = [(_uv, snap)]
                return True
            (a, sa), b = self.temp[0], _uv
            self.temp = []
            if np.linalg.norm(b - a) < 1e-9:
                return True
            cons = self.construction

            def make(width, a=a, b=b, sa=sa, sb=snap):
                pa = self._point_at(a, sa) if sa else None
                pb = self._point_at(b, sb) if sb else None
                OPS.slot(self.sk, a, b, width, cons, pa, pb)
            self._prompt(vp, (a + b) / 2, "Slot width", round(max(np.linalg.norm(b - a) / 3, 1e-3), 3), make,
                         expr_target=lambda: self._last_radius_constraint(), expr_scale=0.5)
            return True
        # mirror / patterns: click geometry to (de)select it, then the line / centre
        sel = [i for i in self.selection if i in sk.entities]
        h = self.hit(vp, x, y, kinds=("point", "entity"))
        if t in ("sk_mirror", "sk_lpattern"):
            if h and h[0] == "entity" and sk.entities[h[1]]["type"] == "line" and sel and h[1] not in sel:
                axis = h[1]
                if t == "sk_mirror":
                    self.push_undo()
                    try:
                        OPS.mirror(self.sk, sel, axis)
                    except ValueError as err:
                        self.undo_stack.pop()
                        self._flash(vp, str(err))
                        return True
                    self.selection = []
                    self.changed(True)
                    self.set_tool("select")
                    return True
                e_ = sk.entities[axis]
                d = sk.P(e_["p2"]) - sk.P(e_["p1"])
                # copies go towards the side you clicked on (away from the selection), not the
                # way the line happened to be drawn
                pts = np.vstack([sk.polyline(i, 16) for i in sel])
                if d @ (uv - pts.mean(axis=0)) < 0:
                    d = -d
                vals = {"count": 3, "spacing": round(max(OPS.extent_along(sk, sel, d) * 1.25, self._nice(vp, 40)), 3)}
                run = lambda: OPS.linear_pattern(self.sk, sel, d, vals["count"], vals["spacing"])
                anchor = sk.P(e_["p2"])
                self._prompt(vp, anchor, "Number of instances", 3, lambda v: (vals.update(count=int(round(v))), run()),
                             minimum=2, integer=True,
                             then=lambda base: self._prompt(vp, anchor, "Spacing", vals["spacing"],
                                                            lambda v: (vals.update(spacing=v), run()),
                                                            base=base, allow_negative=True,
                                                            done=lambda: self._finish_pick_tool()))
                return True
        if t == "sk_cpattern" and sel:
            if h and h[0] == "point" or (h is None) or (h and h[1] not in sel):
                centre = sk.P(h[1]) if h and h[0] == "point" else uv
                vals = {"count": 6, "angle": 360.0}
                run = lambda: OPS.circular_pattern(self.sk, sel, centre, vals["count"], vals["angle"])
                self._prompt(vp, centre, "Number of instances", 6, lambda v: (vals.update(count=int(round(v))), run()),
                             minimum=2, integer=True,
                             then=lambda base: self._prompt(vp, centre, "Total angle (degrees)", 360,
                                                            lambda v: (vals.update(angle=v), run()), base=base,
                                                            allow_negative=True,
                                                            done=lambda: self._finish_pick_tool()))
                return True
        if h and h[0] == "entity":
            if h[1] in self.selection:
                self.selection.remove(h[1])
            else:
                self.selection.append(h[1])
        return True

    def _finish_pick_tool(self):
        self.selection = []
        self.set_tool("select")

    def _last_radius_constraint(self):
        rs = [c for c in self.sk.constraints if c["type"] == "radius"]
        return rs[-1] if rs else None

    def _flash(self, vp, msg):
        self._flashing = True
        self._prompt_label = "⚠ " + msg
        vp.status_text = self._prompt_label
        vp.update()

    def _prompt(self, vp, uv_pos, label, initial, apply, base=None, then=None, done=None,
                allow_negative=False, minimum=None, integer=False, expr_target=None, expr_scale=1.0):
        """Run `apply(value)` on a fresh copy of the sketch while the user types the value — the
        geometry updates live. Enter keeps it (then runs `then(base)` for a follow-up value)."""
        push = base is None
        if base is None:
            base = self.sk.to_dict()
            self.push_undo()
        self._prompt_label = label + " — type a value and press Enter"
        state = {"ok": False}

        def run(v):
            self.sk = Sketch.from_dict(base)
            try:
                apply(v)
                state["ok"] = True
                self._prompt_label = label + " — type a value and press Enter"
            except ValueError as err:
                self.sk = Sketch.from_dict(base)
                self.sk.solve()
                state["ok"] = False
                self._prompt_label = f"⚠ {err}"
            vp.status_text = self._prompt_label
            self.on_change(False)
            vp.update()

        def valid(v):
            if v is None:
                return False
            if minimum is not None and v < minimum:
                return False
            if not allow_negative and v <= 0:
                return False
            return True

        run(initial)
        s = self.scr(vp, uv_pos)
        ed = self._make_editor(vp, s[0] + 70, s[1] - 26, fmt(initial))
        ed.setToolTip(label)
        ed.setPlaceholderText(label)

        def edited(_t):
            v = parse_number(ed.text(), variables=self.variables)
            if valid(v):
                run(int(round(v)) if integer else v)

        def finish(accept_next=True):
            if self._editor is not ed:
                return
            txt = ed.text().strip()
            self._close_editor()
            if not state["ok"]:
                self.sk = Sketch.from_dict(base)
                if push and self.undo_stack:
                    self.undo_stack.pop()
            elif has_vars(txt) and expr_target:
                c = expr_target()
                if c is not None:
                    c["expr"] = txt if expr_scale == 1.0 else f"({txt})*{expr_scale}"
            self._prompt_label = None
            self.changed(True)
            vp.status_text = self.hint()
            vp.update()
            vp.setFocus()
            if accept_next and state["ok"] and then:
                then(base)
            elif done:
                done()

        ed.textEdited.connect(edited)
        ed.returnPressed.connect(lambda: finish(True))
        ed.editingFinished.connect(lambda: finish(False))
        self._editor = ed
        self._editor_finish = lambda: finish(False)
        ed.show()
        ed.setFocus()
        ed.selectAll()

    def _offer_arc(self, vp, aid):
        sk = self.sk
        a1, a2 = sk.arc_angles(aid)
        am = (a1 + a2) / 2
        r = sk.radius(aid)
        pos = sk.P(sk.entities[aid]["c"]) + np.array([math.cos(am), math.sin(am)]) * r * 1.3
        self._offer_dims(vp, [("radius", [aid], r, pos)])

    def _offer_dims(self, vp, offers):
        """Like Onshape: right after drawing, pop up a dimension box. Typing a value and pressing
        Enter adds that dimension and the geometry snaps to it; carrying on drawing (or Esc) just
        dismisses it. Several offers (e.g. rectangle width then height) are shown one after another."""
        if not offers:
            return
        ctype, refs, value, pos = offers[0]
        rest = offers[1:]
        s = self.scr(vp, pos)
        ed = self._make_editor(vp, s[0], s[1], fmt(value))
        ed.setToolTip("Type a value and press Enter to dimension it — or just keep drawing")

        def commit():
            if self._editor is not ed:
                return
            txt = ed.text().strip()
            v = parse_number(txt, variables=self.variables)
            self._close_editor()
            if v is not None and v > 0 and all(r in self.sk.entities for r in refs):
                self.push_undo()
                c = self.sk.add_constraint(ctype, *refs, value=float(v))
                c["pos"] = [float(pos[0]), float(pos[1])]
                if has_vars(txt):
                    c["expr"] = txt
                self.sk.solve()
                if not self.sk.status.get("ok", True):
                    self.sk.constraints.remove(c)      # would over-constrain: drop it quietly
                    self.sk.solve()
                    self.undo_stack.pop()
                self.on_change(True)
            vp.update()
            vp.setFocus()
            if rest:
                self._offer_dims(vp, rest)

        def dismiss():
            if self._editor is ed:        # focus left the box without Enter: no dimension
                self._close_editor()
                vp.update()

        ed.returnPressed.connect(commit)
        ed.editingFinished.connect(dismiss)
        self._editor = ed
        ed.show()
        ed.setFocus()
        ed.selectAll()

    def _make_editor(self, vp, cx, cy, text):
        ed = QLineEdit(vp)
        ed.setFixedWidth(110)
        ed.setText(text)
        ed.move(int(cx - 55), int(cy - 12))
        ed.setStyleSheet("QLineEdit { border: 2px solid %s; border-radius: 4px; padding: 2px 4px; font-weight: 600; }"
                         % theme.ACCENT)
        return ed

    def edit_dimension(self, vp, c):
        self._close_editor()
        ed = QLineEdit(vp)
        ed.setFixedWidth(110)
        unit = "deg" if c["type"] == "angle" else "mm"
        ed.setText(c.get("expr") or fmt(c["value"]))
        ed.selectAll()
        r = self._dim_rects.get(c["id"])
        if r is None:
            pos = c.get("pos")
            s = self.scr(vp, pos) if pos else np.array([vp.width() / 2, vp.height() / 2])
            r = QRectF(s[0] - 40, s[1] - 10, 80, 20)
        ed.move(int(r.center().x() - 55), int(r.center().y() - 12))
        ed.setStyleSheet("QLineEdit { border: 2px solid %s; border-radius: 4px; padding: 2px 4px; font-weight: 600; }"
                         % theme.ACCENT)
        cid = c["id"]

        def commit():
            if self._editor is not ed:
                return
            txt = ed.text().strip()
            v = parse_number(txt, angle=(unit == "deg"), variables=self.variables)
            self._close_editor()
            if v is None or (c["type"] != "angle" and v <= 0):
                return
            cc = self._constraint(cid)
            if cc is None:
                return
            self.push_undo()
            old = cc["value"]
            saved = self.sk.to_dict()
            cc["value"] = float(v)
            if has_vars(txt):
                cc["expr"] = txt
            else:
                cc.pop("expr", None)
            self.sk.solve()
            if not self.sk.status.get("ok", True):
                # can't be done with the other dimensions: put everything back exactly and say why
                cc["value"] = old
                self._restore_geometry(saved)
                self.sk.solve()
                self.undo_stack.pop()
                self._flash(vp, f"Can't make that {fmt(v)} — it doesn't fit with the other dimensions and "
                                "constraints")
            self.on_change(True)
            vp.update()

        ed.returnPressed.connect(commit)
        ed.editingFinished.connect(commit)
        self._editor = ed
        ed.show()
        ed.setFocus()

    def _end_editor(self):
        """Close any value box; a live prompt keeps the value it's showing."""
        fin = getattr(self, "_editor_finish", None)
        self._editor_finish = None
        if fin is not None and self._editor is not None:
            fin()
        else:
            self._close_editor()

    def _close_editor(self):
        ed = self._editor
        if ed is not None:
            self._editor = None
            ed.hide()
            ed.deleteLater()

    # ------------------------------------------------------------------ use / trim
    def _use_edge(self, vp, pk, construction=None, click=None):
        """Project a part edge into the sketch as fixed geometry. Returns the new entity (or point) id to use, or
        None. Straight edges become lines, circles facing the sketch become circles / arcs, and any edge that is
        seen edge-on (like the rim of a cylinder from the side) becomes the straight line it looks like.
        With `click` (screen x, y) a click near either end of the result gives that end point instead."""
        pid, ei = pk.key
        shape = vp.part_shapes.get(pid)
        if shape is None:
            return None
        edge = K.edges(shape)[ei]
        info = K.edge_info(edge)
        sk = self.sk
        cons = self.construction if construction is None else construction
        n = np.asarray(self.plane.normal, float)
        poly = np.asarray(K.discretize_edge(edge), float)
        uvs = np.array([self.plane.to_local(q) for q in poly]) if len(poly) else np.zeros((0, 2))
        self.push_undo()
        out = None
        if "r" in info and abs(abs(np.asarray(info["axis"]) @ n) - 1) < 1e-6:
            c = np.array(self.plane.to_local(info["center"]))
            pc = sk.add_point(*c)
            sk.add_constraint("fix", pc, value=c.tolist())
            if len(poly) > 1 and np.linalg.norm(poly[0] - poly[-1]) > 1e-6:
                # partial circular edge: an arc
                p0, p1, mid = uvs[0], uvs[-1], uvs[len(uvs) // 2]
                a, b = sk.add_point(*p0), sk.add_point(*p1)
                if not _ccw_contains(c, p0, p1, mid):
                    a, b = b, a
                out = sk.add_arc(pc, a, b, cons)
                for p_ in (a, b):
                    sk.add_constraint("fix", p_, value=list(sk.P(p_)))
            else:
                out = sk.add_circle(pc, info["r"], cons)
                sk.add_constraint("radius", out, value=info["r"])
        elif len(uvs) >= 2:
            # anything whose outline on the sketch plane is straight: a line between its two extreme points
            ctr = uvs.mean(axis=0)
            _u, sv, vt = np.linalg.svd(uvs - ctr)
            d = vt[0]
            off = np.abs((uvs - ctr) @ np.array([-d[1], d[0]]))
            size = max(float(np.ptp(uvs, axis=0).max()), 1e-9)
            if float(off.max()) > 1e-4 * max(size, 1.0) + 1e-7:
                self.undo_stack.pop()
                self._flash(vp, "Only straight or circular edges can be used here")
                return None
            t = (uvs - ctr) @ d
            a, b = ctr + d * t.min(), ctr + d * t.max()
            if np.linalg.norm(b - a) < 1e-7:
                out = sk.add_point(*a)                     # the edge points straight at you: a point
                sk.add_constraint("fix", out, value=a.tolist())
            else:
                pa, pb = sk.add_point(*a), sk.add_point(*b)
                out = sk.add_line(pa, pb, cons)
                sk.add_constraint("fix", pa, value=a.tolist())
                sk.add_constraint("fix", pb, value=b.tolist())
                if click is not None:
                    for pp in (pa, pb):
                        sp = self.scr(vp, sk.P(pp))
                        if np.hypot(sp[0] - click[0], sp[1] - click[1]) < 10:
                            out = pp                       # clicked at the end: dimension to the corner
        else:
            self.undo_stack.pop()
            return None
        self.changed(True)
        return out

    def _trim(self, eid, uv):
        """Trim a line segment back to its neighbouring intersections with other curves."""
        sk = self.sk
        e = sk.entities[eid]
        if e["type"] != "line":
            # whole circle / arc: just delete it
            self.push_undo()
            sk.delete([eid])
            self.changed(True)
            return
        a, b = sk.P(e["p1"]), sk.P(e["p2"])
        d = b - a
        ts = []
        for oid, o in sk.entities.items():
            if oid == eid or o["type"] == "point":
                continue
            for t in _intersections_with_line(sk, oid, a, b):
                if 1e-6 < t < 1 - 1e-6:
                    ts.append(t)
        t_click = float(np.clip(((uv - a) @ d) / max(d @ d, 1e-12), 0, 1))
        lo = max([t for t in ts if t < t_click], default=0.0)
        hi = min([t for t in ts if t > t_click], default=1.0)
        self.push_undo()
        p1, p2 = e["p1"], e["p2"]
        cons = e.get("construction", False)
        sk.delete([eid])
        if lo > 1e-6:
            q = sk.add_point(*(a + lo * d))
            if p1 not in sk.points:
                p1 = sk.add_point(*a)
            sk.add_line(p1, q, cons)
        if hi < 1 - 1e-6:
            q = sk.add_point(*(a + hi * d))
            if p2 not in sk.points:
                p2 = sk.add_point(*b)
            sk.add_line(q, p2, cons)
        self.changed(True)

    # ================================================================== drawing
    def _color(self, i):
        st = self.sk.status
        if i in self.selection:
            return theme.SELECT
        if i == self.hover:
            return theme.HOVER
        bad_refs = set()
        for c in self.sk.constraints:
            if c["id"] in st.get("bad", ()):
                bad_refs.update(c["refs"])
        if i in bad_refs:
            return theme.SKETCH_BAD
        if i in self.sk.entities:
            e = self.sk.entities[i]
            if e.get("construction") and e["type"] != "point":
                return theme.SKETCH_CONSTRUCTION
            return theme.SKETCH_FREE if self.sk.is_entity_free(i) else theme.SKETCH_DEFINED
        if i in self.sk.points:
            return theme.SKETCH_FREE if i in st.get("free", set()) else theme.SKETCH_DEFINED
        return theme.SKETCH_FREE

    def draw_gl(self, vp):
        pl = self.plane
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_LIGHTING)
        # faint sketch plane grid
        s = vp.cam.scale
        span = 60 * s * 20
        step = 10 ** math.floor(math.log10(max(s * 60, 1e-6)))
        cu, cv = pl.to_local(vp.cam.target)
        GL.glLineWidth(1)
        GL.glColor4f(0.3, 0.45, 0.7, 0.10)
        GL.glBegin(GL.GL_LINES)
        n = int(span / step)
        if n < 400:
            u0 = math.floor((cu - span / 2) / step) * step
            v0 = math.floor((cv - span / 2) / step) * step
            for k in range(n + 2):
                u = u0 + k * step
                GL.glVertex3f(*pl.to_world(u, v0))
                GL.glVertex3f(*pl.to_world(u, v0 + span + step))
                v = v0 + k * step
                GL.glVertex3f(*pl.to_world(u0, v))
                GL.glVertex3f(*pl.to_world(u0 + span + step, v))
        GL.glEnd()
        sk = self.sk
        for eid, e in sk.entities.items():
            if e["type"] == "point":
                continue
            col = self._color(eid)
            if e.get("construction"):
                GL.glEnable(GL.GL_LINE_STIPPLE)
                GL.glLineStipple(3, 0x0F0F)
            GL.glLineWidth(3.0 if col in (theme.SELECT, theme.HOVER) else 2.0)
            GL.glColor3f(*col)
            for poly in sk.polylines(eid, 96):
                GL.glBegin(GL.GL_LINE_STRIP)
                for u, v in poly:
                    GL.glVertex3f(*pl.to_world(u, v))
                GL.glEnd()
            if e["type"] == "text" and (eid == self.hover or eid in self.selection):
                GL.glLineWidth(1.0)            # the text's box, while hovered or selected
                GL.glBegin(GL.GL_LINE_STRIP)
                for u, v in sk.text_box(eid):
                    GL.glVertex3f(*pl.to_world(u, v))
                GL.glEnd()
            GL.glDisable(GL.GL_LINE_STIPPLE)
        # points (endpoints, centres, sketch points)
        virtual = {e["p"] for e in sk.entities.values() if e["type"] == "point" and e.get("virtual")}
        if virtual:
            GL.glLineWidth(1.2)
            GL.glColor3f(*theme.SKETCH_CONSTRUCTION)
            k = vp.cam.scale * 4
            GL.glBegin(GL.GL_LINES)
            for pid in virtual:
                u, v = sk.points[pid]
                for du, dv in ((k, k), (k, -k)):
                    GL.glVertex3f(*pl.to_world(u - du, v - dv))
                    GL.glVertex3f(*pl.to_world(u + du, v + dv))
            GL.glEnd()
        for pid, p in sk.points.items():
            if pid in virtual:
                continue
            col = self._color(pid)
            ent_pt = any(e["type"] == "point" and e["p"] == pid for e in sk.entities.values())
            GL.glPointSize(9 if pid in (self.hover,) or pid in self.selection else (7 if ent_pt else 5))
            GL.glColor3f(*col)
            GL.glBegin(GL.GL_POINTS)
            GL.glVertex3f(*pl.to_world(*p))
            GL.glEnd()
        self._draw_preview_gl(vp)
        GL.glEnable(GL.GL_DEPTH_TEST)

    def _draw_preview_gl(self, vp):
        pl = self.plane
        cur = self.cursor_uv
        if cur is None:
            return
        GL.glColor3f(*theme.SKETCH_FREE)
        GL.glLineWidth(1.6)
        if self.construction:
            GL.glEnable(GL.GL_LINE_STIPPLE)
            GL.glLineStipple(3, 0x0F0F)

        def strip(pts, closed=False):
            GL.glBegin(GL.GL_LINE_LOOP if closed else GL.GL_LINE_STRIP)
            for u, v in pts:
                GL.glVertex3f(*pl.to_world(u, v))
            GL.glEnd()
        t = self.tool
        if t == "line" and self.chain_anchor is not None:
            strip([self.sk.P(self.chain_anchor), cur])
        elif t == "rect" and self.temp:
            a = self.temp[0][0]
            strip([a, (cur[0], a[1]), cur, (a[0], cur[1])], True)
        elif t == "center_rect" and self.temp:
            c = self.temp[0][0]
            h = cur - c
            strip([c - h, (c[0] + h[0], c[1] - h[1]), c + h, (c[0] - h[0], c[1] + h[1])], True)
        elif t == "circle" and self.temp:
            c = self.temp[0][0]
            r = np.linalg.norm(cur - c)
            a = np.linspace(0, 2 * math.pi, 97)
            strip(c + r * np.c_[np.cos(a), np.sin(a)])
        elif t == "slot" and self.temp:
            a = self.temp[0][0]
            strip([a, cur])
        elif t == "spline" and self.temp:
            pts = [q for q, _s in self.temp] + [cur]
            strip(K.spline_sample(pts, False, 48) if len(pts) >= 2 else pts)
        elif t == "ellipse" and self.temp:
            c = self.temp[0][0]
            if len(self.temp) == 1:
                strip([c, cur])
            else:
                ax = self.temp[1][0] - c
                A = max(np.linalg.norm(ax), 1e-12)
                nrm = np.array([-ax[1], ax[0]]) / A
                r = abs(float((cur - c) @ nrm))
                a = np.linspace(0, 2 * math.pi, 97)
                strip(c + np.outer(np.cos(a), ax) + np.outer(np.sin(a), nrm * r))
        elif t == "arc3" and self.temp:
            if len(self.temp) == 1:
                strip([self.temp[0][0], cur])
            else:
                p1, p2 = self.temp[0][0], self.temp[1][0]
                ctr = _circumcenter(p1, p2, cur)
                if ctr is not None:
                    if not _ccw_contains(ctr, p1, p2, cur):
                        p1, p2 = p2, p1
                    strip(_arc_pts(ctr, p1, p2))
                else:
                    strip([p1, p2])
        elif t == "arc_center" and self.temp:
            c = self.temp[0][0]
            if len(self.temp) == 1:
                strip([c, cur])
            else:
                p1 = self.temp[1][0]
                r = np.linalg.norm(p1 - c)
                v = cur - c
                p2 = c + v / max(np.linalg.norm(v), 1e-12) * r
                a, b = (p1, p2) if self.arc_sweep >= 0 else (p2, p1)
                strip(_arc_pts(c, a, b))
        GL.glDisable(GL.GL_LINE_STIPPLE)
        # yellow dotted alignment guides (horizontal / vertical inference from a hovered point or midpoint)
        if self.infer_hits and (self.tool in DRAW_TOOLS or self.tool == "slot"):
            GL.glColor3f(0.96, 0.78, 0.08)
            GL.glLineWidth(1.4)
            GL.glEnable(GL.GL_LINE_STIPPLE)
            GL.glLineStipple(2, 0x3333)
            for s_uv, _axis in self.infer_hits:
                GL.glBegin(GL.GL_LINES)
                GL.glVertex3f(*pl.to_world(float(s_uv[0]), float(s_uv[1])))
                GL.glVertex3f(*pl.to_world(float(cur[0]), float(cur[1])))
                GL.glEnd()
            GL.glDisable(GL.GL_LINE_STIPPLE)
        # snap marker
        if self.tool in DRAW_TOOLS or self.tool in ("dimension", "slot"):
            GL.glPointSize(8)
            GL.glColor3f(*(theme.HOVER if self.snap_info else theme.SKETCH_FREE))
            GL.glBegin(GL.GL_POINTS)
            GL.glVertex3f(*pl.to_world(*cur))
            GL.glEnd()

    def draw_overlay(self, vp, p):
        self._dim_rects = {}
        self._glyph_rects = []
        sk = self.sk
        if self._box is not None:
            boxsel.draw_box(p, self._box)
        f = QFont()
        f.setPixelSize(12)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetricsF(f)
        # ---- dimensions
        for c in sk.constraints:
            if c["type"] not in DIMENSIONS:
                continue
            try:
                self._draw_dimension(vp, p, c, fm)
            except (KeyError, IndexError, ValueError):
                continue
        # ---- the dimension being placed, following the mouse
        if self.tool == "dimension" and self.dim_first is not None and self.cursor_uv is not None \
                and self._editor is None and (self.dim_second is not None or not getattr(self, "_dim_over_item", False)):
            try:
                spec = self._dim_spec(self.dim_first, self.dim_second, self.cursor_uv)
                if spec is not None:
                    pc = {"id": "__preview", "type": spec[0], "refs": spec[1], "value": spec[2],
                          "pos": [float(self.cursor_uv[0]), float(self.cursor_uv[1])]}
                    self._draw_dimension(vp, p, pc, fm)
                    self._dim_rects.pop("__preview", None)
            except (KeyError, IndexError, ValueError, TypeError):
                pass
        # ---- constraint glyphs
        if self.show_constraints:
            f2 = QFont()
            f2.setPixelSize(11)
            f2.setBold(True)
            p.setFont(f2)
            slots = {}
            for c in sk.constraints:
                t = c["type"]
                if t in DIMENSIONS or t in ("coincident", "point_on", "normal_at"):
                    continue
                for ref in c["refs"][:2]:
                    anchor = self._anchor_screen(vp, ref)
                    if anchor is None:
                        continue
                    k = slots.get(ref, 0)
                    slots[ref] = k + 1
                    r = QRectF(anchor[0] + 8 + 17 * k, anchor[1] + 6, 16, 16)
                    sel = c["id"] in self.selection or c["id"] == self.hover
                    bad = c["id"] in sk.status.get("bad", ())
                    p.setPen(QPen(QColor(theme.ERROR if bad else ("#d98a1e" if sel else "#9aa4b1")), 1))
                    p.setBrush(QColor("#fff4dc" if sel else "white"))
                    p.drawRoundedRect(r, 3, 3)
                    p.setPen(QColor(theme.ERROR if bad else "#374151"))
                    p.drawText(r, Qt.AlignCenter, CONSTRAINT_GLYPHS.get(t, "?"))
                    self._glyph_rects.append((r, c["id"]))
        # ---- inference marker near cursor
        if self.infer_hits and self.cursor_uv is not None and self.tool in DRAW_TOOLS | {"slot"}:
            s = self.scr(vp, self.cursor_uv)
            label = "".join(sorted({a for _u, a in self.infer_hits}))
            r = QRectF(s[0] + 12, s[1] + 10, 16 * len(label), 16)
            p.setPen(QPen(QColor(theme.ACCENT), 1))
            p.setBrush(QColor("#eaf2fd"))
            p.drawRoundedRect(r, 3, 3)
            p.drawText(r, Qt.AlignCenter, label)
        # ---- live length readout while drawing
        if self.cursor_uv is not None:
            txt = None
            if self.tool == "line" and self.chain_anchor is not None:
                txt = fmt(np.linalg.norm(self.cursor_uv - self.sk.P(self.chain_anchor)))
            elif self.tool in ("circle",) and self.temp:
                txt = "Ø" + fmt(2 * np.linalg.norm(self.cursor_uv - self.temp[0][0]))
            elif self.tool in ("rect", "center_rect") and self.temp:
                d = np.abs(self.cursor_uv - self.temp[0][0]) * (2 if self.tool == "center_rect" else 1)
                txt = f"{fmt(d[0])} × {fmt(d[1])}"
            if txt:
                s = self.scr(vp, self.cursor_uv)
                f3 = QFont()
                f3.setPixelSize(11)
                p.setFont(f3)
                p.setPen(QColor(theme.TEXT_MUTED))
                p.drawText(QPointF(s[0] + 14, s[1] - 10), txt)

    def _anchor_screen(self, vp, ref):
        sk = self.sk
        if ref in sk.entities:
            e = sk.entities[ref]
            if e["type"] == "line":
                m = (sk.P(e["p1"]) + sk.P(e["p2"])) / 2
            elif e["type"] == "circle":
                m = sk.P(e["c"]) + np.array([e["r"] * 0.707, e["r"] * 0.707])
            elif e["type"] == "arc":
                a1, a2 = sk.arc_angles(ref)
                am = (a1 + a2) / 2
                m = sk.P(e["c"]) + sk.radius(ref) * np.array([math.cos(am), math.sin(am)])
            else:
                m = sk.P(e["p"])
            return self.scr(vp, m)
        if ref in sk.points:
            return self.scr(vp, sk.P(ref))
        return None

    @staticmethod
    def _dim_label_at(S, A, B, pos):
        """Where a distance's number goes: the middle of its dimension line (like Onshape), unless it was
        placed off past one end — then out there, in line with the dimension."""
        mid = (A + B) / 2
        if pos is None:
            return S(mid)
        d = B - A
        L = float(np.linalg.norm(d))
        if L < 1e-12:
            return S(mid)
        u = d / L
        t = float((pos - A) @ u)
        if 0 <= t <= L:
            return S(mid)
        return S(A + u * t)

    def _draw_dimension(self, vp, p, c, fm):
        sk = self.sk
        t = c["type"]
        R = c["refs"]
        sel = c["id"] in self.selection or c["id"] == self.hover
        bad = c["id"] in sk.status.get("bad", ())
        col = QColor(theme.ERROR if bad else ("#d98a1e" if sel else "#1f2937"))
        pen = QPen(col, 1)
        p.setPen(pen)
        S = lambda uv: QPointF(*self.scr(vp, uv))
        val = c["value"]
        if t == "angle":
            text = fmt(val) + "°"
        elif t == "diameter":
            text = "Ø" + fmt(val)
        elif t == "radius":
            text = "R" + fmt(val)
        else:
            text = fmt(val)
        if c.get("expr"):
            text = f"{c['expr']} ({text})"
        pos = np.array(c["pos"]) if c.get("pos") else None

        def arrow(tip: QPointF, frm: QPointF):
            d = np.array([tip.x() - frm.x(), tip.y() - frm.y()])
            n = np.linalg.norm(d)
            if n < 1e-6:
                return
            d /= n
            q = np.array([-d[1], d[0]])
            a = np.array([tip.x(), tip.y()])
            pts = [a, a - d * 8 + q * 3, a - d * 8 - q * 3]
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(*x) for x in pts]))
            p.setBrush(Qt.NoBrush)

        label_at = None
        if t in ("distance", "hdistance", "vdistance"):
            refs = list(R)
            if len(refs) == 1:
                e = sk.entities[refs[0]]
                refs = [e["p1"], e["p2"]]
            a_id, b_id = refs
            if b_id in sk.entities and sk.entities[b_id]["type"] == "line":
                # point to line
                P = sk.P(self._as_point(a_id))
                e = sk.entities[b_id]
                A, B = sk.P(e["p1"]), sk.P(e["p2"])
                d = B - A
                F = A + ((P - A) @ d) / max(d @ d, 1e-12) * d
                # the dimension line runs from the point square to the line, slid sideways to where it was placed
                du = d / max(np.linalg.norm(d), 1e-12)
                off = float((pos - P) @ du) if pos is not None else 0.0
                P2, F2 = P + du * off, F + du * off
                sp, sf = S(P2), S(F2)
                p.setPen(QPen(col, 1, Qt.DashLine))
                # extension of the reference line out to the dimension, and from the point if it was slid
                ends = [A, B]
                near = min(ends, key=lambda q: np.linalg.norm(q - F2))
                if not (min(A @ du, B @ du) - 1e-9 <= F2 @ du <= max(A @ du, B @ du) + 1e-9):
                    p.drawLine(S(near), sf)
                p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 150), 1))
                if abs(off) > 1e-9:
                    p.drawLine(S(P), sp)
                p.setPen(pen)
                p.drawLine(sp, sf)
                arrow(sp, sf)
                arrow(sf, sp)
                label_at = self._dim_label_at(S, P2, F2, pos)
            else:
                def ptof(i):
                    if i in sk.entities:
                        e = sk.entities[i]
                        return sk.P(e.get("c", e.get("p")))
                    return sk.P(i)
                A, B = ptof(a_id), ptof(b_id)
                if pos is None:
                    mid = (A + B) / 2
                    d = B - A
                    nrm = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-12)
                    pos = mid + nrm * max(np.linalg.norm(d) * 0.15, vp.cam.scale * 20)
                if t == "hdistance":
                    A2, B2 = np.array([A[0], pos[1]]), np.array([B[0], pos[1]])
                elif t == "vdistance":
                    A2, B2 = np.array([pos[0], A[1]]), np.array([pos[0], B[1]])
                else:
                    d = B - A
                    nrm = np.array([-d[1], d[0]]) / max(np.linalg.norm(d), 1e-12)
                    off = (pos - A) @ nrm
                    A2, B2 = A + nrm * off, B + nrm * off
                p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 150), 1))
                p.drawLine(S(A), S(A2))
                p.drawLine(S(B), S(B2))
                p.setPen(pen)
                p.drawLine(S(A2), S(B2))
                arrow(S(A2), S(B2))
                arrow(S(B2), S(A2))
                label_at = self._dim_label_at(S, A2, B2, pos)
        elif t in ("radius", "diameter"):
            e = sk.entities[R[0]]
            C = sk.P(e["c"])
            r = sk.radius(R[0])
            if pos is None:
                pos = C + np.array([r * 0.9, r * 0.9])
            v = pos - C
            n = np.linalg.norm(v)
            u = v / n if n > 1e-12 else np.array([1.0, 0])
            edge = C + u * r
            start = C - u * r if t == "diameter" else C
            p.drawLine(S(start), S(edge))
            if n > r:
                p.drawLine(S(edge), S(pos))
            arrow(S(edge), S(C))
            if t == "diameter":
                arrow(S(start), S(C))
            label_at = S(pos)
        elif t == "angle":
            l1, l2 = sk.entities[R[0]], sk.entities[R[1]]
            m1 = (sk.P(l1["p1"]) + sk.P(l1["p2"])) / 2
            m2 = (sk.P(l2["p1"]) + sk.P(l2["p2"])) / 2
            if pos is None:
                pos = (m1 + m2) / 2
            p.setPen(QPen(col, 1, Qt.DashLine))
            p.drawLine(S(m1), S(pos))
            p.drawLine(S(m2), S(pos))
            p.setPen(pen)
            label_at = S(pos)
        if label_at is None:
            return
        w = fm.horizontalAdvance(text) + 10
        r = QRectF(label_at.x() - w / 2, label_at.y() - 10, w, 20)
        p.setPen(QPen(col, 1))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(r, 3, 3)
        p.setPen(col)
        p.drawText(r, Qt.AlignCenter, text)
        p.setBrush(Qt.NoBrush)
        self._dim_rects[c["id"]] = r


# ---------------------------------------------------------------------- geometry helpers

def _inside_poly(poly, pt):
    """Is the 2D point inside the polygon (screen or sketch coordinates)?"""
    x, y = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def _cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _pt_line_dist(p, a, b):
    d = b - a
    n = np.linalg.norm(d)
    if n < 1e-12:
        return float(np.linalg.norm(p - a))
    return float(abs(d[0] * (p - a)[1] - d[1] * (p - a)[0]) / n)


def _circumcenter(a, b, c):
    a, b, c = map(np.asarray, (a, b, c))
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    ux = ((a @ a) * (b[1] - c[1]) + (b @ b) * (c[1] - a[1]) + (c @ c) * (a[1] - b[1])) / d
    uy = ((a @ a) * (c[0] - b[0]) + (b @ b) * (a[0] - c[0]) + (c @ c) * (b[0] - a[0])) / d
    return np.array([ux, uy])


def _ang(c, p):
    return math.atan2(p[1] - c[1], p[0] - c[0])


def _ccw_contains(c, p1, p2, p3):
    """Does the CCW arc from p1 to p2 around c pass through p3?"""
    a1 = _ang(c, p1)
    a2 = (_ang(c, p2) - a1) % (2 * math.pi)
    a3 = (_ang(c, p3) - a1) % (2 * math.pi)
    return a3 < a2


def _arc_pts(c, p1, p2, n=64):
    r = np.linalg.norm(np.asarray(p1) - c)
    a1 = _ang(c, p1)
    a2 = _ang(c, p2)
    while a2 <= a1:
        a2 += 2 * math.pi
    a = np.linspace(a1, a2, n)
    return c + r * np.c_[np.cos(a), np.sin(a)]


def _intersections_with_line(sk, oid, a, b):
    """Parameters t in [0,1] along segment a->b where entity oid crosses it."""
    o = sk.entities[oid]
    d = b - a
    out = []
    if o["type"] == "line":
        c, e = sk.P(o["p1"]), sk.P(o["p2"])
        f = e - c
        den = d[0] * f[1] - d[1] * f[0]
        if abs(den) < 1e-12:
            return out
        w = c - a
        t = (w[0] * f[1] - w[1] * f[0]) / den
        s = (w[0] * d[1] - w[1] * d[0]) / den
        if -1e-9 <= s <= 1 + 1e-9:
            out.append(t)
    elif o["type"] in ("circle", "arc"):
        c = sk.P(o["c"])
        r = sk.radius(oid)
        f = a - c
        A = d @ d
        B = 2 * f @ d
        C = f @ f - r * r
        disc = B * B - 4 * A * C
        if disc < 0 or A < 1e-12:
            return out
        for sgn in (-1, 1):
            t = (-B + sgn * math.sqrt(disc)) / (2 * A)
            pt = a + t * d
            if o["type"] == "arc":
                a1, a2 = sk.arc_angles(oid)
                ang = math.atan2(pt[1] - c[1], pt[0] - c[0])
                while ang < a1:
                    ang += 2 * math.pi
                if ang > a2:
                    continue
            out.append(t)
    return out
