"""2D drawings, like Onshape's Drawing tab.

A drawing shows a part (or a whole Part Studio, or an assembly) on a sheet of paper:

* views worked out from the 3D model with hidden-line removal — a base view, views projected from it
  (first-angle / ISO or third-angle / ANSI), and isometric views; visible edges thick, hidden edges dashed,
  centre marks on holes,
* dimensions that measure the model (lengths, horizontal / vertical / aligned distances, point-to-line
  distances, diameters and radii) and follow it when the part changes,
* notes, a border with grid zones, and a title block,
* export to DXF (opens in AutoCAD) and PDF.

Everything on the sheet is built once into a list of simple paper-space items (lines, arcs, polylines, filled
triangles and text, in millimetres from the sheet's bottom-left corner). The screen, the PDF and the DXF all draw
that same list, so what you export is exactly what you see.

Views keep their model in the source's own coordinates; a view maps model point P to paper as
    paper = o + scale * (P · right, P · up)
where o is the paper position of the model origin. Dimension ends are stored as 3D model points (a vertex, or a
circle's centre / radius) and re-found on the current model each time, so they stay attached after edits.
"""
from __future__ import annotations

import base64
import copy
import json
import math
import os
import time
import uuid

import numpy as np

from . import kernel as K

FILE_FORMAT = "freeshape-drawing"
FILE_VERSION = 1

SHEETS = {"A4": (297.0, 210.0), "A3": (420.0, 297.0), "A2": (594.0, 420.0), "A1": (841.0, 594.0),
          "A0": (1189.0, 841.0)}
SCALES = [10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01]
DIRECTIONS = {
    # name: (direction from the part towards the viewer, up)
    "Front": ((0, -1, 0), (0, 0, 1)),
    "Back": ((0, 1, 0), (0, 0, 1)),
    "Top": ((0, 0, 1), (0, 1, 0)),
    "Bottom": ((0, 0, -1), (0, -1, 0)),
    "Right": ((1, 0, 0), (0, 0, 1)),
    "Left": ((-1, 0, 0), (0, 0, 1)),
    "Isometric": ((1, -1, 1), (0, 0, 1)),
}
MARGIN = 10.0              # border inset from the sheet edge
TB_W, TB_H = 180.0, 36.0   # title block
TEXT_H = 3.5               # dimension / note text height (mm on paper), ISO 129
BALLOON_R = 4.5            # balloon circle radius (mm on paper)
ARROW_L, ARROW_W = 3.0, 0.9
GAP = 1.0                  # gap between a part and its extension line
OVERSHOOT = 2.0            # extension lines run past the dimension line by this much

TITLE_FIELDS = [("title", "Title"), ("number", "Drawing number"), ("rev", "Revision"), ("drawn", "Drawn by"),
                ("date", "Date"), ("company", "Company"), ("material", "Material"),
                ("tolerance", "General tolerances"), ("sheet", "Sheet")]


def _ray_hits(o, d, T):
    """Distance along the ray (o, d) to the nearest of the triangles T (n, 3, 3), or None (Möller–Trumbore)."""
    if not len(T):
        return None
    o, d = np.asarray(o, float), np.asarray(d, float)
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    pv = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, pv)
    ok = np.abs(det) > 1e-14
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = o - T[:, 0]
    uu = np.einsum("ij,ij->i", tv, pv) * inv
    qv = np.cross(tv, e1)
    vv = (qv @ d) * inv
    t = np.einsum("ij,ij->i", e2, qv) * inv
    hit = ok & (uu >= -1e-9) & (vv >= -1e-9) & (uu + vv <= 1 + 1e-9) & (t > 1e-9)
    return float(t[hit].min()) if hit.any() else None


def _curve_points(c):
    """A view curve as a list of 2D points (arcs sampled finely)."""
    if c[0] == "line":
        return [np.asarray(c[1], float), np.asarray(c[2], float)]
    if c[0] == "circle":
        n = max(12, int(abs(c[4] - c[3]) / (2 * math.pi) * 96))
        ts = np.linspace(c[3], c[4], n + 1)
        return list(np.stack([c[1][0] + c[2] * np.cos(ts), c[1][1] + c[2] * np.sin(ts)], axis=1))
    return [np.asarray(q, float) for q in c[1]]


def _clip_to_circle(pts, c, r):
    """The parts of a polyline that lie inside a circle (for detail views)."""
    out, cur = [], []
    c = np.asarray(c, float)

    def inside(q):
        return float(np.linalg.norm(q - c)) <= r + 1e-12

    def cross_at(a, b):
        d = b - a
        f = a - c
        A, B, C = d @ d, 2 * f @ d, f @ f - r * r
        disc = B * B - 4 * A * C
        if A < 1e-24 or disc < 0:
            return []
        sq = math.sqrt(disc)
        return sorted(t for t in ((-B - sq) / (2 * A), (-B + sq) / (2 * A)) if 0 <= t <= 1)
    for i in range(len(pts) - 1):
        a, b = np.asarray(pts[i], float), np.asarray(pts[i + 1], float)
        ts = cross_at(a, b)
        ia = inside(a)
        if ia and not cur:
            cur = [a]
        state = ia
        for t in ts:
            q = a + (b - a) * t
            if state:
                cur.append(q)
                out.append(cur)
                cur = []
            else:
                cur = [q]
            state = not state
        if state:
            cur.append(b)
    if len(cur) >= 2:
        out.append(cur)
    return [p for p in out if len(p) >= 2]


def scale_label(s):
    if s >= 1:
        return f"{s:g}:1"
    return f"1:{1 / s:g}"


def fmt_value(v, dp=None):
    if dp is None:
        txt = f"{v:.2f}".rstrip("0").rstrip(".")
    else:
        txt = f"{v:.{int(dp)}f}"
    return "0" if txt in ("-0", "") else txt


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


class DrawingDoc:
    kind = "drawing"

    def __init__(self):
        self.uid = uuid.uuid4().hex
        self.name = "Drawing 1"
        self.path = None
        self.dirty = False
        self.listeners = []
        self.resolver = None                 # callable(source) -> TopoDS shape (set by the main window)
        self.bom_resolver = None             # callable(source) -> [{"name", "qty", "mass", "shapes"}] (parts list)
        self.bom: list[dict] = []            # parts list rows: {"item", "name", "qty", "mass"} (saved in the file)
        self._bom_shapes: list = []          # each row's shapes, placed as in the model
        self.source = None                   # what the drawing shows (see the main window's resolver)
        self.sheet = {"size": "A3", "portrait": False, "scale": 1.0, "projection": "first"}
        self.title = {"title": "", "number": "", "rev": "A", "drawn": "", "date": time.strftime("%d/%m/%Y"),
                      "company": "", "material": "", "tolerance": "ISO 2768-m", "sheet": "1/1"}
        self.views: list[dict] = []
        self.annots: list[dict] = []
        self.shape = None
        self.error = None
        self._fallback = None                # the shape saved in the file (used if the source can't be found)
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._counter = 0
        self._items = None                   # cached paper-space items
        self.version = 0                     # bumps whenever the picture changes
        self.hlr_async = None                # callable(doc, view): work out the exact view in the background
        self._saved_views = None             # exact views stored in the file (used if the model is unchanged)
        # attributes some generic window code looks at
        from .features import ModelState
        self.features = []
        self.hidden_parts = set()
        self.hidden_features = set()
        self.state = ModelState()

    # ------------------------------------------------------------------ ids, undo
    def new_id(self, prefix):
        self._counter += 1
        used = {v["id"] for v in self.views} | {a["id"] for a in self.annots}
        while f"{prefix}{self._counter}" in used:
            self._counter += 1
        return f"{prefix}{self._counter}"

    def view(self, vid):
        return next((v for v in self.views if v["id"] == vid), None)

    def annot(self, aid):
        return next((a for a in self.annots if a["id"] == aid), None)

    def is_empty(self):
        return not self.views and not self.annots

    def _snapshot(self):
        return json.dumps({"sheet": self.sheet, "title": self.title, "views": self.views, "annots": self.annots,
                           "source": self.source})

    def _restore(self, snap):
        d = json.loads(snap)
        self.sheet, self.title, self.views, self.annots = d["sheet"], d["title"], d["views"], d["annots"]
        if d.get("source") != self.source:
            self.source = d.get("source")
            self.refresh(notify=False)

    def checkpoint(self):
        self._undo.append(self._snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self.dirty = True
            self.changed()

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self.dirty = True
            self.changed()

    def notify(self):
        for cb in list(self.listeners):
            cb()

    def changed(self, notify=True):
        """Something on the sheet changed: rebuild the picture."""
        self._items = None
        self._by_owner = None
        self.version += 1
        if notify:
            self.notify()

    # ------------------------------------------------------------------ the model being drawn
    def refresh(self, notify=True):
        """Fetch the current model (picking up edits in its Part Studio / Assembly)."""
        old = self.shape
        self.error = None
        shape = None
        if self.source is not None and self.resolver is not None:
            try:
                shape = self.resolver(self.source)
            except Exception as e:
                self.error = str(e)
        if shape is None and self._fallback is not None:
            shape = self._fallback
            if self.error is None and self.resolver is not None and self.source is not None:
                self.error = "Linked model not found — showing the copy saved in this drawing"
            elif self.error:
                self.error += " — showing the copy saved in this drawing"
        if shape is None and old is not None:
            shape = old
        self.shape = shape
        if shape is not old:
            self._snaps = {}
            if shape is not None:
                self._use_saved_views()
                self._reattach()
            self._update_bom()
        self.changed(notify)

    # ------------------------------------------------------------------ parts list (BOM) and balloons
    def _update_bom(self):
        """Refresh the parts list from the model (keeps the saved one if the model can't be reached)."""
        self._bom_meshes = None
        if self.bom_resolver is None or self.source is None:
            return
        try:
            rows = self.bom_resolver(self.source) or []
        except Exception:
            return
        self.bom = [{"item": i + 1, "name": r.get("name", "?"), "qty": int(r.get("qty", 1)),
                     "mass": r.get("mass")} for i, r in enumerate(rows)]
        self._bom_shapes = [list(r.get("shapes", [])) for r in rows]

    def add_bom_table(self, pos=None, checkpoint=True):
        """The parts list, sitting on the title block (or at `pos`, its bottom-right corner on the paper)."""
        if not self.bom:
            raise ValueError("This drawing's model has no parts list (make a drawing of an assembly or of all the "
                             "parts in a Part Studio)")
        for a in self.annots:
            if a["type"] == "bom":
                return a
        W, _H = self.sheet_size()
        a = {"type": "bom", "pos": list(pos) if pos is not None else [W - MARGIN, MARGIN + TB_H]}
        return self.add_annot(a) if checkpoint else self._add_annot_quiet(a)

    def _add_annot_quiet(self, a):
        a["id"] = self.new_id("A")
        self.annots.append(a)
        self.changed()
        return a

    def _meshes(self):
        """Per BOM row, the triangle meshes of its parts (for finding which part is where in a view)."""
        if getattr(self, "_bom_meshes", None) is None:
            out = []
            for shapes in self._bom_shapes:
                tris = []
                for shp in shapes:
                    m = K.tessellate(shp)
                    if len(m.tris):
                        tris.append(m.verts[m.tris].astype(float))
                out.append(np.vstack(tris) if tris else np.zeros((0, 3, 3)))
            self._bom_meshes = out
        return self._bom_meshes

    def _first_hit(self, origin, direction):
        """(row index, point) of the first part a ray meets, or None."""
        best = None
        for ri, T in enumerate(self._meshes()):
            t = _ray_hits(origin, direction, T)
            if t is not None and (best is None or t < best[0]):
                best = (t, ri)
        if best is None:
            return None
        return best[1], np.asarray(origin, float) + np.asarray(direction, float) * best[0]

    def part_at(self, v, xy):
        """Which parts-list row is under paper point xy in view v, and the 3D point there."""
        if not self.bom or not self._bom_shapes:
            return None
        r, u, d = self.axes(v)
        uv = self.from_paper(v, xy)
        lo, hi = self._bbox()
        far = float(np.linalg.norm(hi - lo)) * 2 + 10
        centre = (lo + hi) / 2
        o = r * uv[0] + u * uv[1] + d * (float(centre @ d) + far)
        return self._first_hit(o, -d)

    def _visible_point(self, v, ri):
        """A point on row ri's part that can be seen in view v (so its balloon doesn't point at something
        hidden): of many points spread over the part, the visible one nearest its middle. Falls back to any
        point on it when none is visible."""
        shapes = self._bom_shapes[ri] if ri < len(self._bom_shapes) else []
        if not shapes:
            return None
        _r, _u, d = self.axes(v)
        d = np.asarray(d, float)
        lo, hi = self._bbox()
        far = float(np.linalg.norm(hi - lo)) * 2 + 10
        best = None
        for shp in shapes:                                   # the first copy that shows anywhere will do
            m = K.tessellate(shp)
            if not len(m.tris):
                continue
            tri = m.verts[m.tris].astype(float)
            nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
            area = np.linalg.norm(nrm, axis=1)
            facing = (nrm @ d) > 1e-9 * (area + 1e-30)       # triangles turned towards the viewer
            idx = np.nonzero(facing)[0]
            if not len(idx):
                continue
            idx = idx[np.argsort(-area[idx])][:80]
            cands = tri[idx].mean(1)
            mid = self.project(v, cands.mean(0))
            order = np.argsort(np.linalg.norm(self.project(v, cands) - mid, axis=1))
            for q in cands[order]:
                hit = self._first_hit(q + d * far, -d)
                if hit is not None and hit[0] == ri and np.linalg.norm(hit[1] - q) < 1e-6 * far + 1e-6:
                    return q
            if best is None:
                best = cands[order[0]]
        return best

    def add_balloon(self, v, item, anchor, pos, checkpoint=True):
        a = {"type": "balloon", "view": v["id"], "item": int(item), "anchor": [float(x) for x in anchor],
             "pos": [float(x) for x in pos]}
        return self.add_annot(a) if checkpoint else self._add_annot_quiet(a)

    def auto_balloons(self, v):
        """A balloon for every parts-list row seen in view v that doesn't have one there yet, fanned out round
        the view. Returns how many were added."""
        if not self.bom:
            raise ValueError("This drawing's model has no parts list")
        have = {a["item"] for a in self.annots if a["type"] == "balloon" and a.get("view") == v["id"]}
        x0, y0, x1, y1 = self.view_box(v)
        C = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
        placed = [np.asarray(a["pos"], float) for a in self.annots if a["type"] == "balloon"]
        todo = []
        for ri, row in enumerate(self.bom):
            if row["item"] in have:
                continue
            q = self._visible_point(v, ri)
            if q is not None:
                todo.append((row["item"], q))
        if not todo:
            return 0
        self.checkpoint()
        for item, q in todo:
            A = self.to_paper(v, self.project(v, q))
            d = A - C
            ang = math.atan2(d[1], d[0]) if np.linalg.norm(d) > 1e-6 else math.pi / 4
            for k in range(36):                              # turn round until it is clear of the other balloons
                a = ang + (k // 2 + 1) * 0.17 * (1 if k % 2 else -1) if k else ang
                dirv = np.array([math.cos(a), math.sin(a)])
                ts = [t for t in ((x0 - C[0]) / dirv[0] if dirv[0] < -1e-9 else None,
                                  (x1 - C[0]) / dirv[0] if dirv[0] > 1e-9 else None,
                                  (y0 - C[1]) / dirv[1] if dirv[1] < -1e-9 else None,
                                  (y1 - C[1]) / dirv[1] if dirv[1] > 1e-9 else None) if t is not None]
                P = C + dirv * (min(ts) + 12.0)
                if all(np.linalg.norm(P - o) > 2 * BALLOON_R + 2 for o in placed):
                    break
            placed.append(P)
            self.annots.append({"id": self.new_id("A"), "type": "balloon", "view": v["id"], "item": item,
                                "anchor": [float(x) for x in q], "pos": [float(x) for x in P]})
        self.changed()
        return len(todo)

    def _bom_items(self, out, a):
        """The parts list: a table with ITEM / PART / QTY (and MASS when known), its bottom-right corner at pos."""
        rows = self.bom
        show_mass = any(r.get("mass") for r in rows)
        h = 7.0
        name_w = max([len(str(r["name"])) for r in rows] + [10]) * TEXT_H * 0.62 + 6
        cols = [("ITEM", 14.0, "center"), ("PART", min(max(name_w, 50.0), 140.0), "left"), ("QTY", 14.0, "center")]
        if show_mass:
            cols.append(("MASS (g)", 24.0, "right"))
        W = sum(c[1] for c in cols)
        x1, y0 = a["pos"]
        x0 = x1 - W
        n = len(rows) + 1
        y1 = y0 + n * h
        owner = a["id"]
        L = lambda p, q, lay="BOM": out.append(("line", lay, owner, p, q))       # noqa: E731
        L((x0, y0), (x1, y0), "BOM_THICK")
        L((x0, y1), (x1, y1), "BOM_THICK")
        L((x0, y0), (x0, y1), "BOM_THICK")
        L((x1, y0), (x1, y1), "BOM_THICK")
        L((x0, y1 - h), (x1, y1 - h), "BOM_THICK")
        for k in range(1, n - 1):
            L((x0, y0 + k * h), (x1, y0 + k * h))
        x = x0
        for _c, w, _al in cols[:-1]:
            x += w
            L((x, y0), (x, y1))

        def cell(ci, row_from_top, text):
            cx0 = x0 + sum(c[1] for c in cols[:ci])
            w, al = cols[ci][1], cols[ci][2]
            yb = y1 - (row_from_top + 1) * h
            px = cx0 + w / 2 if al == "center" else (cx0 + 2 if al == "left" else cx0 + w - 2)
            out.append(("text", "NOTE", owner, (px, yb + h / 2), str(text), TEXT_H * 0.9, 0.0, al, "middle"))
        for ci, (title, _w, _al) in enumerate(cols):
            cell(ci, 0, title)
        for k, r in enumerate(rows):
            cell(0, k + 1, r["item"])
            cell(1, k + 1, r["name"])
            cell(2, k + 1, r["qty"])
            if show_mass:
                cell(3, k + 1, fmt_value(r["mass"], 1) if r.get("mass") else "")

    def _balloon_items(self, out, a):
        v = self.view(a.get("view"))
        if v is None:
            return
        P = np.asarray(a["pos"], float)
        A = self.to_paper(v, self.project(v, a["anchor"]))
        owner = a["id"]
        out.append(("circle", "BALLOON", owner, tuple(P), BALLOON_R))
        out.append(("text", "NOTE", owner, tuple(P), str(a["item"]), TEXT_H, 0.0, "center", "middle"))
        d = A - P
        L = float(np.linalg.norm(d))
        if L > BALLOON_R + 0.5:
            u = d / L
            out.append(("line", "BALLOON", owner, tuple(P + u * BALLOON_R), tuple(A)))
            s = 0.7                                          # a dot where the leader touches the part
            out.append(("tri", "BALLOON", owner, (A[0] - s, A[1]), (A[0], A[1] + s), (A[0] + s, A[1])))
            out.append(("tri", "BALLOON", owner, (A[0] - s, A[1]), (A[0], A[1] - s), (A[0] + s, A[1])))

    def source_label(self):
        s = self.source or {}
        if s.get("kind") == "assembly":
            return f"Assembly “{s.get('name', '?')}”"
        if s.get("kind") == "studio":
            what = s.get("part_name") if s.get("part") not in (None, "*") else "all parts"
            if s.get("flat"):
                what += " (flat pattern)"
            return f"{s.get('studio_name', 'Part Studio')} · {what}"
        return "—"

    # ------------------------------------------------------------------ sheet geometry
    def sheet_size(self):
        w, h = SHEETS.get(self.sheet.get("size", "A3"), SHEETS["A3"])
        return (h, w) if self.sheet.get("portrait") else (w, h)

    def view_scale(self, v):
        return float(v.get("scale") or self.sheet.get("scale") or 1.0)

    def axes(self, v):
        return K.view_axes(v["d"], v["up"])

    def project(self, v, P):
        """3D model point(s) -> 2D view coordinates (model units)."""
        r, u, _d = self.axes(v)
        P = np.asarray(P, float)
        return np.stack([P @ r, P @ u], axis=-1)

    def to_paper(self, v, uv):
        return np.asarray(v["o"], float) + self.view_scale(v) * np.asarray(uv, float)

    def from_paper(self, v, xy):
        return (np.asarray(xy, float) - np.asarray(v["o"], float)) / self.view_scale(v)

    def model_extent(self, d, up):
        """2D bounds (model units) of the model seen from direction d (from its bounding box corners)."""
        if self.shape is None:
            return np.zeros(2), np.zeros(2)
        lo, hi = self._bbox()
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        r, u, _ = K.view_axes(d, up)
        uv = np.stack([corners @ r, corners @ u], axis=-1)
        return uv.min(axis=0), uv.max(axis=0)

    def view_shape(self, v):
        """What a view shows: the model, or for a section view the model with the part in front of the cut
        removed (cached until the model or the cut changes). A detail view shows what its parent shows."""
        if self.shape is None:
            return None
        if v.get("type") == "detail":
            p = self.view(v.get("parent"))
            return self.view_shape(p) if p is not None else self.shape
        if v.get("type") != "section" or not v.get("cut"):
            return self.shape
        key = (id(self.shape), tuple(v["cut"]["p0"]), tuple(v["d"]))
        cache = self.__dict__.setdefault("_cut_cache", {})
        hit = cache.get(v["id"])
        if hit is not None and hit[0] == key and hit[2] is self.shape:
            return hit[1]
        try:
            shp = K.section_cut(self.shape, v["cut"]["p0"], v["d"])
        except Exception:
            shp = self.shape
        cache[v["id"]] = (key, shp, self.shape)
        return shp

    def curves(self, v):
        """The view's edges. Exact straight away for simple parts; for complex ones (with a window to do the
        work in the background) a quick mesh-based picture first, replaced when the exact one is ready."""
        empty = {"visible": [], "hidden": [], "tangent": []}
        shp = self.view_shape(v)
        if shp is None:
            return empty
        try:
            ex = K.hlr_cached(shp, v["d"], v["up"])
            if ex is not None:
                return ex
            if self.hlr_async is not None and not K.hlr_is_quick(shp):
                self.hlr_async(self, v)
                return K.hlr_project(shp, v["d"], v["up"], fast=True)
            return K.hlr_project(shp, v["d"], v["up"])
        except Exception:
            return empty

    def exact_ready(self):
        return self.shape is None or all(K.hlr_cached(self.view_shape(v), v["d"], v["up"]) is not None
                                         for v in self.views)

    def view_box(self, v):
        """Paper rectangle (x0, y0, x1, y1) around a view."""
        if v.get("type") == "detail" and v.get("detail"):
            c, r = np.asarray(v["detail"]["c"], float), float(v["detail"]["r"])
            a, b = self.to_paper(v, c - r), self.to_paper(v, c + r)
            return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))
        lo, hi = self.model_extent(v["d"], v["up"])
        a, b = self.to_paper(v, lo), self.to_paper(v, hi)
        return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))

    def drawing_area(self):
        """Inside the border, above the title block band."""
        W, H = self.sheet_size()
        return (MARGIN + 5, MARGIN + TB_H + 6, W - MARGIN - 5, H - MARGIN - 5)

    # ------------------------------------------------------------------ views
    def add_view(self, d, up, centre, kind="base", parent=None, align=None, checkpoint=True):
        if checkpoint:
            self.checkpoint()
        v = {"id": self.new_id("V"), "type": kind, "d": [float(x) for x in _unit(d)], "up": [float(x) for x in up],
             "o": [0.0, 0.0], "scale": None, "hidden": kind != "iso", "tangent": kind == "iso",
             "centre_marks": kind != "iso", "parent": parent, "align": align, "label": ""}
        lo, hi = self.model_extent(v["d"], v["up"])
        mid = (lo + hi) / 2
        v["o"] = (np.asarray(centre, float) - self.view_scale(v) * mid).tolist()
        if v["hidden"]:
            v["hidden"] = self.hidden_is_readable(v)
        self.views.append(v)
        self.changed()
        return v

    def _sheet_normal(self):
        """The flat pattern's normal (its biggest flat face's)."""
        best = None
        for f in K.faces(self.shape):
            pl = K.planar_face_plane(f)
            if pl is None:
                continue
            a = K.face_info(f).get("area", 0)
            if best is None or a > best[0]:
                best = (a, pl.normal)
        return None if best is None else np.asarray(best[1], float)

    def hidden_is_readable(self, v):
        """Hidden edges help on simple parts but bury a gear or a PCB in dashes: show them by default only
        when there aren't many (they can always be switched on per view)."""
        cv = self.curves(v)
        weight = sum(1 if c[0] != "poly" else 3 for c in cv.get("hidden", []))
        return weight <= 60

    def projected_dir(self, parent, side):
        """Direction and up of a view placed on `side` (unit 2D vector on paper) of `parent`, by the standard
        (first angle: the view from the left goes on the right; third angle: the view from the right)."""
        r, u, d = self.axes(parent)
        horiz = abs(side[0]) >= abs(side[1])
        s3 = r * np.sign(side[0]) if horiz else u * np.sign(side[1])
        first = self.sheet.get("projection", "first") == "first"
        nd = -s3 if first else s3
        if horiz:
            nup = u
        else:
            su = float(np.sign(side[1]))
            nup = (su * d) if first else (-su * d)
        return nd, nup, ("h" if horiz else "v")

    def add_projected(self, parent, paper_pt):
        """A view projected from `parent`, on the side of `paper_pt` (kept lined up with it)."""
        box = self.view_box(parent)
        c = np.array([(box[0] + box[2]) / 2, (box[1] + box[3]) / 2])
        side = np.asarray(paper_pt, float) - c
        if np.linalg.norm(side) < 1e-6:
            return None
        diag = min(abs(side[0]), abs(side[1])) > 0.5 * max(abs(side[0]), abs(side[1]))
        self.checkpoint()
        if diag:
            # off the corner: an isometric view of that corner, like Onshape
            r, u, d = self.axes(parent)
            nd = d + r * np.sign(side[0]) + u * np.sign(side[1])
            v = self.add_view(nd, u, paper_pt, kind="iso", parent=None, checkpoint=False)
            v["scale"] = parent.get("scale")
            self.changed()
            return v
        nd, nup, align = self.projected_dir(parent, side)
        v = self.add_view(nd, nup, paper_pt, kind="projected", parent=parent["id"], align=align, checkpoint=False)
        v["scale"] = parent.get("scale")
        self._line_up(v)
        self.changed()
        return v

    # ------------------------------------------------------------------ section and detail views
    def next_letter(self):
        used = {v.get("letter") for v in self.views}
        for ch in "ABCDEFGHJKLMNPQRSTUVWXYZ":           # I and O are skipped, as is usual on drawings
            if ch not in used:
                return ch
        return "Z"

    def add_section(self, parent, a, b, place, checkpoint=True):
        """A section view cut along the line a-b (paper points) drawn on `parent`, placed at `place` (paper).
        You look at the cut from the side the view is placed on; the cut faces are hatched."""
        r, u, pd = self.axes(parent)
        ua, ub = self.from_paper(parent, a), self.from_paper(parent, b)
        line = r * (ub[0] - ua[0]) + u * (ub[1] - ua[1])
        if np.linalg.norm(line) < 1e-9:
            raise ValueError("Draw the cutting line by clicking two different points")
        line = _unit(line)
        n = _unit(np.cross(line, pd))
        mid = (np.asarray(a, float) + np.asarray(b, float)) / 2
        w = np.asarray(place, float) - mid
        if float(np.asarray([n @ r, n @ u]) @ w) < 0:
            n = -n                                       # the viewer is on the side the view is placed on
        p0 = r * ua[0] + u * ua[1]
        up = np.asarray(parent["up"], float)
        up = up - (up @ n) * n
        if np.linalg.norm(up) < 1e-6:
            up = np.asarray(pd, float)
        if checkpoint:
            self.checkpoint()
        v = self.add_view(n, _unit(up), place, kind="section", parent=parent["id"], checkpoint=False)
        v["cut"] = {"p0": [float(x) for x in p0]}
        v["letter"] = self.next_letter()
        v["hidden"] = False
        v["scale"] = parent.get("scale")
        v["o"] = (np.asarray(place, float) - self.view_scale(v) * np.mean(self.model_extent(v["d"], v["up"]),
                                                                            axis=0)).tolist()
        self.changed()
        return v

    def add_detail(self, parent, c, radius, place, factor=2.0, checkpoint=True):
        """An enlarged view of the circle (centre c, radius in paper mm) on `parent`, placed at `place`."""
        if radius <= 0:
            raise ValueError("Drag out a circle round the area to enlarge")
        if checkpoint:
            self.checkpoint()
        cuv = self.from_paper(parent, c)
        rm = float(radius) / self.view_scale(parent)
        v = {"id": self.new_id("V"), "type": "detail", "d": list(parent["d"]), "up": list(parent["up"]),
             "o": [0.0, 0.0], "scale": self.view_scale(parent) * float(factor), "hidden": parent.get("hidden", False),
             "tangent": parent.get("tangent", False), "centre_marks": True, "parent": parent["id"], "align": None,
             "label": "", "letter": self.next_letter(), "detail": {"c": [float(x) for x in cuv], "r": rm}}
        v["o"] = (np.asarray(place, float) - self.view_scale(v) * cuv).tolist()
        self.views.append(v)
        self.changed()
        return v

    def view_title(self, v):
        if v.get("type") == "section" and v.get("letter"):
            return f"SECTION {v['letter']}-{v['letter']}"
        if v.get("type") == "detail" and v.get("letter"):
            return f"DETAIL {v['letter']} ({scale_label(self.view_scale(v))})"
        return v.get("label", "")

    def _line_up(self, v):
        """Keep a projected view in line with its parent (same height for side views, same x for top views)."""
        p = self.view(v.get("parent")) if v.get("parent") else None
        if p is None:
            return
        # a model point that is in both views must line up: the origin works because the axes are shared
        if v["align"] == "h":
            v["o"][1] = p["o"][1] + 0.0
        else:
            v["o"][0] = p["o"][0] + 0.0

    def move_view(self, v, delta):
        """Move a view on the paper. A projected view only slides along its line with its parent; the views
        projected from this one come along, so everything stays lined up."""
        delta = np.asarray(delta, float)
        if v.get("parent") and v.get("align"):
            delta = np.array([delta[0], 0.0]) if v["align"] == "h" else np.array([0.0, delta[1]])

        def shift(view):
            view["o"] = (np.asarray(view["o"], float) + delta).tolist()
            for a in self.annots:
                if a.get("view") == view["id"] and a.get("pos") is not None:
                    a["pos"] = (np.asarray(a["pos"], float) + delta).tolist()    # its dimensions come too
            for ch in self.views:
                if ch.get("parent") == view["id"]:
                    shift(ch)
        shift(v)
        self.changed()

    def delete(self, ids):
        ids = set(ids)
        # deleting a view takes its projected views and its dimensions with it
        grow = True
        while grow:
            grow = False
            for v in self.views:
                if v.get("parent") in ids and v["id"] not in ids:
                    ids.add(v["id"])
                    grow = True
        self.checkpoint()
        self.views = [v for v in self.views if v["id"] not in ids]
        self.annots = [a for a in self.annots if a["id"] not in ids and a.get("view") not in ids]
        self.changed()

    def auto_layout(self, which=("Front", "Top", "Right", "Isometric")):
        """Standard views (front, plan, side, isometric) laid out evenly on the sheet, at the biggest standard
        scale that fits."""
        self.views = []
        self.annots = []
        if self.shape is None:
            return
        if (self.source or {}).get("flat"):
            self._flat_layout()
            return
        first = self.sheet.get("projection", "first") == "first"
        fd, fu = DIRECTIONS["Front"]
        flo, fhi = self.model_extent(fd, fu)
        fw, fh = fhi - flo
        lo, hi = K.bbox(self.shape)
        depth = float(hi[1] - lo[1])
        ilo, ihi = self.model_extent(*DIRECTIONS["Isometric"])
        iw, ih = ihi - ilo
        want_top, want_side, want_iso = "Top" in which, "Right" in which, "Isometric" in which
        x0, y0, x1, y1 = self.drawing_area()
        AW, AH = x1 - x0, y1 - y0
        min_gap = 12.0

        def sizes(sc):
            c0 = sc * fw
            c1 = max(sc * depth if want_side else 0, sc * iw if want_iso else 0)
            r_front = sc * fh
            r_plan = max(sc * depth if want_top else 0, sc * ih if want_iso else 0)
            return c0, c1, r_front, r_plan
        scale = SCALES[-1]
        for sc in SCALES:
            c0, c1, rf, rp = sizes(sc)
            ncol = 2 if c1 > 0 else 1
            nrow = 2 if rp > 0 else 1
            if c0 + c1 + (ncol + 1) * min_gap <= AW and rf + rp + (nrow + 1) * min_gap <= AH:
                scale = sc
                break
        self.sheet["scale"] = scale
        c0, c1, rf, rp = sizes(scale)
        ncol = 2 if c1 > 0 else 1
        nrow = 2 if rp > 0 else 1
        gx = (AW - c0 - c1) / (ncol + 1)
        gy = (AH - rf - rp) / (nrow + 1)
        col0 = x0 + gx + c0 / 2
        col1 = x0 + 2 * gx + c0 + c1 / 2
        # first angle: front on the top row, plan below it; third angle: plan on top, front below
        r_top_h, r_bot_h = (rf, rp) if first else (rp, rf)
        row_top = y1 - gy - r_top_h / 2
        row_bot = y1 - 2 * gy - r_top_h - r_bot_h / 2
        front_y = row_top if first else row_bot
        plan_y = row_bot if first else row_top
        front = self.add_view(fd, fu, (col0, front_y), checkpoint=False)
        if want_top:
            nd, nup, al = self.projected_dir(front, np.array([0.0, -1.0 if first else 1.0]))
            v = self.add_view(nd, nup, (col0, plan_y), kind="projected", parent=front["id"], align=al,
                              checkpoint=False)
            self._line_up(v)
        if want_side:
            nd, nup, al = self.projected_dir(front, np.array([1.0, 0.0]))
            v = self.add_view(nd, nup, (col1, front_y), kind="projected", parent=front["id"], align=al,
                              checkpoint=False)
            self._line_up(v)
        if want_iso:
            self.add_view(*DIRECTIONS["Isometric"], (col1, plan_y), kind="iso", checkpoint=False)
        self.changed()

    def _flat_layout(self):
        """A flat pattern: one view looking straight at the sheet, as big as fits."""
        n = self._sheet_normal()
        if n is None:
            n = np.array([0.0, 0.0, 1.0])
        if n[2] < -1e-9 or (abs(n[2]) < 1e-9 and (n[1] < -1e-9 or (abs(n[1]) < 1e-9 and n[0] < 0))):
            n = -n
        up = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.9 else np.array([0.0, 0.0, 1.0])
        lo, hi = self.model_extent(n, up)
        w, h = hi - lo
        x0, y0, x1, y1 = self.drawing_area()
        scale = SCALES[-1]
        for sc in SCALES:
            if sc * w + 40 <= x1 - x0 and sc * h + 40 <= y1 - y0:
                scale = sc
                break
        self.sheet["scale"] = scale
        v = self.add_view(n, up, ((x0 + x1) / 2, (y0 + y1) / 2), checkpoint=False)
        v["hidden"] = False
        self.changed()

    # ------------------------------------------------------------------ snapping & dimension ends
    def _snapdata(self):
        if self.shape is None:
            return np.zeros((0, 3)), []
        s = self.__dict__.setdefault("_snaps", {})
        key = id(self.shape)
        if key not in s:
            s.clear()
            s[key] = K.snap_features(self.shape)
        return s[key]

    def _bbox(self):
        bb = getattr(self, "_bb", None)
        if bb is None or bb[0] is not self.shape:
            lo, hi = K.bbox(self.shape)
            bb = self._bb = (self.shape, lo, hi)
        return bb[1], bb[2]

    def _size(self):
        if self.shape is None:
            return 1.0
        lo, hi = self._bbox()
        return float(np.linalg.norm(hi - lo)) or 1.0

    def bend_lines(self, v):
        """Bend lines of a flat-pattern drawing in this view's 2D coordinates (model units): [(uvA, uvB)].
        Only where the sheet is seen face-on, like where they're drawn."""
        bends = (self.source or {}).get("bends") if (self.source or {}).get("flat") else None
        if not bends or self.shape is None:
            return []
        n = self._sheet_normal()
        _r, _u, dv = self.axes(v)
        if n is None or abs(abs(float(n @ dv)) - 1) > 1e-6:
            return []
        return [(np.asarray(self.project(v, p0), float), np.asarray(self.project(v, p1), float))
                for p0, p1, _lab in bends]

    def snap_data(self, v):
        """Everything the Dimension tool hit-tests in a view, as arrays in paper coordinates (cached until the
        view or the model changes)."""
        cv = self.curves(v)
        key = (id(self.shape), id(cv), tuple(v["d"]), tuple(v["up"]), tuple(v["o"]), self.view_scale(v),
               bool(v.get("hidden")), bool(v.get("tangent")),
               json.dumps((self.source or {}).get("bends")) if (self.source or {}).get("flat") else "")
        cache = self.__dict__.setdefault("_snapview", {})
        hit = cache.get(v["id"])
        if hit is not None and hit[0] == key:
            return hit[1]
        pts = self.snap_points(v)
        o, s = np.asarray(v["o"], float), self.view_scale(v)
        la, lb, hc = [], [], []
        sa, sb = [], []                     # every drawn piece of line, to check something is really there
        shown = ("visible", "hidden") if v.get("hidden") else ("visible",)
        if v.get("tangent"):
            shown = shown + ("tangent",)
        for k in shown:
            for c in cv.get(k, []):
                if c[0] == "line":
                    la.append(c[1])
                    lb.append(c[2])
                    sa.append(c[1])
                    sb.append(c[2])
                elif c[0] == "circle":
                    hc.append((o + s * np.asarray(c[1], float), c[2] * s, c[3], c[4]))
                    n = max(8, int(abs(c[4] - c[3]) / (2 * math.pi) * 72))
                    ts = np.linspace(c[3], c[4], n + 1)
                    q = np.stack([c[1][0] + c[2] * np.cos(ts), c[1][1] + c[2] * np.sin(ts)], axis=1)
                    sa.extend(q[:-1])
                    sb.extend(q[1:])
                else:
                    q = np.asarray(c[1], float)
                    if len(q) >= 2:
                        sa.extend(q[:-1])
                        sb.extend(q[1:])
        # flat patterns: the bend lines can be dimensioned to, like edges
        for A, B in self.bend_lines(v):
            la.append(A)
            lb.append(B)
            sa.append(A)
            sb.append(B)
        circ = [(P3, r, self.to_paper(v, uv), ci) for P3, _ax, r, uv, ci in self.circles_in_view(v)]
        data = {
            "items": pts,
            "xy": np.array([q[2] for q in pts], float).reshape(-1, 2),
            "centre": np.array([q[0] == "centre" for q in pts], bool),
            "LA": o + s * np.array(la, float).reshape(-1, 2), "LB": o + s * np.array(lb, float).reshape(-1, 2),
            "hc": hc,
            "circ": circ,
            "cC": np.array([c[2] for c in circ], float).reshape(-1, 2),
            "cR": np.array([c[1] * s for c in circ], float),
            "SA": o + s * np.array(sa, float).reshape(-1, 2), "SB": o + s * np.array(sb, float).reshape(-1, 2),
        }
        cache[v["id"]] = (key, data)
        return data

    def snap_points(self, v):
        """[(kind, 3D point, paper xy, extra)] — vertices and circle centres a dimension can attach to."""
        verts, circles = self._snapdata()
        out = []
        nv, nc = len(verts), len(circles)
        if nv:
            pp = self.to_paper(v, self.project(v, np.nan_to_num(verts)))
            for i, (P, xy) in enumerate(zip(verts, pp)):
                if not np.isnan(P[0]):
                    out.append(("vertex", P, xy, {"i": i, "n": nv}))
        _r, _u, d = self.axes(v)
        for i, (c, ax, r, _full, from_face) in enumerate(circles):
            facing = abs(abs(float(np.dot(_unit(ax), d))) - 1) < 1e-6
            if from_face and not facing:
                continue                      # a cylinder's axis point only means something seen end-on
            xy = self.to_paper(v, self.project(v, c))
            out.append(("centre", c, xy, {"r": r, "axis": ax, "facing": facing, "i": i, "n": nc}))
        return out

    def circles_in_view(self, v, full_only=False):
        """Circles that look round in this view (their axis points at the viewer)."""
        _verts, circles = self._snapdata()
        _r, _u, d = self.axes(v)
        out, seen = [], set()
        for ci, (c, ax, r, full, _ff) in enumerate(circles):
            if full_only and not full:
                continue
            if abs(abs(float(np.dot(_unit(ax), d))) - 1) > 1e-6:
                continue
            uv = self.project(v, c)
            k = (round(float(uv[0]), 5), round(float(uv[1]), 5), round(r, 5))
            if k in seen:
                continue
            seen.add(k)
            out.append((c, ax, r, uv, ci))
        return out

    def resolve(self, a):
        """Where a stored dimension end is on the current model: (3D point, radius or None), or None if gone."""
        verts, circles = self._snapdata()
        P = np.asarray(a["p"], float)
        tol = 0.02 * self._size() + 1e-6
        i = a.get("i")
        if a["kind"] == "vertex":
            if not len(verts):
                return None
            # same topology as when it was picked (e.g. only a dimension changed): the same vertex by number
            if i is not None and a.get("n") == len(verts) and 0 <= i < len(verts) and not np.isnan(verts[i][0]):
                return verts[i], None, i, len(verts)
            d = np.linalg.norm(np.nan_to_num(verts, nan=1e30) - P, axis=1)
            j = int(np.argmin(d))
            return (verts[j], None, j, len(verts)) if d[j] <= tol else None
        if i is not None and a.get("n") == len(circles) and 0 <= i < len(circles):
            return circles[i][0], circles[i][2], i, len(circles)
        best = None
        for j, (c, _ax, r, _full, _ff) in enumerate(circles):
            dc = float(np.linalg.norm(c - P))
            if dc > tol:
                continue
            score = dc + 0.5 * abs(r - float(a.get("r", r)))
            if best is None or score < best[0]:
                best = (score, c, r, j)
        return None if best is None else (best[1], best[2], best[3], len(circles))

    def line_ends(self, v):
        """End points (view coordinates) of the straight edges drawn in a view — where outline edges of round
        faces (a cylinder seen from the side) can be dimensioned from."""
        cv = self.curves(v)
        pts = [c[i] for key in ("visible", "hidden") for c in cv.get(key, []) if c[0] == "line" for i in (1, 2)]
        pts += [q for A, B in self.bend_lines(v) for q in (A, B)]
        return np.array(pts, float) if pts else np.zeros((0, 2))

    def _reattach(self):
        """After the model changed: move each dimension end onto its feature in the new model."""
        for a in self.annots:
            for k in ("a", "b", "b2"):
                end = a.get(k)
                if not isinstance(end, dict):
                    continue
                if end.get("kind") == "uv":
                    v = self.view(a.get("view"))
                    ends = self.line_ends(v) if v is not None else np.zeros((0, 2))
                    if len(ends):
                        dd = np.linalg.norm(ends - np.asarray(end["uv"], float), axis=1)
                        j = int(np.argmin(dd))
                        if dd[j] <= 0.02 * self._size() + 1e-6:
                            end["uv"] = [float(x) for x in ends[j]]
                            end.pop("lost", None)
                            continue
                    end["lost"] = True
                    continue
                hit = self.resolve(end)
                if hit is not None:
                    end["p"] = [float(x) for x in hit[0]]
                    if hit[1] is not None:
                        end["r"] = float(hit[1])
                    end["i"], end["n"] = int(hit[2]), int(hit[3])
                    end.pop("lost", None)
                else:
                    end["lost"] = True

    # ------------------------------------------------------------------ dimensions & notes
    def add_annot(self, a):
        self.checkpoint()
        a["id"] = self.new_id("D" if a["type"] != "note" else "N")
        self.annots.append(a)
        self.changed()
        return a

    def end_uv(self, v, end):
        """A dimension end in the view's 2D coordinates (model units)."""
        if end.get("kind") == "uv":
            return np.asarray(end["uv"], float)
        return self.project(v, end["p"])

    def dim_geometry(self, a):
        """Paper-space anchor points and the measured value of a dimension: (kind, data, value) or None."""
        v = self.view(a.get("view"))
        if v is None:
            return None
        P = lambda end: self.to_paper(v, self.end_uv(v, end))
        t = a["type"]
        if t == "lin":
            A, B = P(a["a"]), P(a["b"])
            uvA, uvB = self.end_uv(v, a["a"]), self.end_uv(v, a["b"])
            o = a.get("orient", "a")
            val = abs(uvB[0] - uvA[0]) if o == "h" else abs(uvB[1] - uvA[1]) if o == "v" else \
                float(np.linalg.norm(uvB - uvA))
            return ("lin", (A, B, o), val)
        if t == "pl":
            A = P(a["a"])
            L1, L2 = P(a["b"]), P(a["b2"])
            uvA = self.end_uv(v, a["a"])
            u1, u2 = self.end_uv(v, a["b"]), self.end_uv(v, a["b2"])
            dd = _unit(u2 - u1)
            n = np.array([-dd[1], dd[0]])
            val = abs(float((uvA - u1) @ n))
            # the foot of the perpendicular, on paper
            pd = _unit(L2 - L1)
            F = L1 + pd * float((A - L1) @ pd)
            return ("lin", (A, F, "perp"), val)
        if t in ("dia", "rad"):
            C = P(a["a"])
            r = float(a["a"].get("r", 1.0))
            return (t, (C, r * self.view_scale(v)), (2 * r if t == "dia" else r))
        return None

    def dim_text(self, a, value):
        if a.get("text"):
            return a["text"]
        pre = {"dia": "Ø", "rad": "R"}.get(a["type"], "")
        return f"{a.get('prefix', '')}{pre}{fmt_value(value, a.get('dp'))}{a.get('suffix', '')}"

    # ------------------------------------------------------------------ building the picture
    def items(self):
        if self._items is None:
            self._items = [it for lst in self.items_by_owner().values() for it in lst]
        return self._items

    def items_by_owner(self):
        """Items grouped by what they belong to (None = border and title block, then each view, each
        annotation). A view's list is reused as long as nothing about that view changed, so moving one view
        or adding a dimension doesn't redo every view."""
        if getattr(self, "_by_owner", None) is not None:
            return self._by_owner
        cache = self.__dict__.setdefault("_owner_cache", {})
        by = {}
        W, H = self.sheet_size()
        sk = ("sheet", W, H, json.dumps(self.sheet, sort_keys=True), json.dumps(self.title, sort_keys=True), self.name)
        hit = cache.get(None)
        if hit is None or hit[0] != sk:
            lst = []
            self._border(lst, W, H)
            self._title_block(lst, W, H)
            hit = (sk, lst)
        by[None] = hit[1]
        new_cache = {None: hit}
        for v in self.views:
            vk = self._view_key(v)
            hit = cache.get(v["id"])
            if hit is None or hit[0] != vk:
                lst = []
                self._view_items(lst, v)
                hit = (vk, lst)
            by[v["id"]] = hit[1]
            new_cache[v["id"]] = hit
        for a in self.annots:
            lst = []
            try:
                if a["type"] == "note":
                    self._note_items(lst, a)
                elif a["type"] == "bom":
                    if self.bom:
                        self._bom_items(lst, a)
                elif a["type"] == "balloon":
                    self._balloon_items(lst, a)
                else:
                    self._dim_items(lst, a)
            except Exception:
                lst = []
            by[a["id"]] = lst
        self._owner_cache = new_cache
        self._by_owner = by
        return by

    def _view_key(self, v):
        cv = self.curves(v)
        bends = (self.source or {}).get("bends") if (self.source or {}).get("flat") else None
        kids = [(c["type"], c.get("letter"), c.get("cut"), c["d"], c.get("detail"))
                for c in self.views if c.get("parent") == v["id"] and c.get("type") in ("section", "detail")]
        return (id(self.view_shape(v)), id(cv), tuple(v["d"]), tuple(v["up"]), tuple(v["o"]), self.view_scale(v),
                bool(v.get("hidden")), bool(v.get("tangent")), bool(v.get("centre_marks", True)), self.view_title(v),
                json.dumps(bends) if bends else "", json.dumps(kids), json.dumps(v.get("detail")),
                json.dumps(v.get("cut")))

    # items: (kind, layer, owner, ...) — kinds: line (p0, p1) · arc (c, r, a0deg, a1deg) · circle (c, r) ·
    #        poly (pts) · tri (p0, p1, p2) filled · text (pos, text, height, angle_deg, halign, valign)
    def _border(self, out, W, H):
        L = lambda a, b, layer="BORDER": out.append(("line", layer, None, a, b))
        L((0, 0), (W, 0), "TRIM")
        L((W, 0), (W, H), "TRIM")
        L((W, H), (0, H), "TRIM")
        L((0, H), (0, 0), "TRIM")
        m = MARGIN
        L((m, m), (W - m, m))
        L((W - m, m), (W - m, H - m))
        L((W - m, H - m), (m, H - m))
        L((m, H - m), (m, m))
        # grid zones: numbers along the top and bottom, letters up the sides
        nx = max(2, int(round((W - 2 * m) / 50)))
        ny = max(2, int(round((H - 2 * m) / 50)))
        for i in range(1, nx):
            x = m + (W - 2 * m) * i / nx
            L((x, 0.0 + m * 0.5), (x, m), "BORDER_THIN")
            L((x, H - m), (x, H - m * 0.5), "BORDER_THIN")
        for i in range(nx):
            x = m + (W - 2 * m) * (i + 0.5) / nx
            out.append(("text", "BORDER_TEXT", None, (x, m * 0.5), str(i + 1), 2.5, 0.0, "center", "middle"))
            out.append(("text", "BORDER_TEXT", None, (x, H - m * 0.5), str(i + 1), 2.5, 0.0, "center", "middle"))
        letters = "ABCDEFGHJKLMNP"
        for j in range(1, ny):
            y = m + (H - 2 * m) * j / ny
            L((m * 0.5, y), (m, y), "BORDER_THIN")
            L((W - m, y), (W - m * 0.5, y), "BORDER_THIN")
        for j in range(ny):
            y = m + (H - 2 * m) * (j + 0.5) / ny
            ch = letters[(ny - 1 - j) % len(letters)]
            out.append(("text", "BORDER_TEXT", None, (m * 0.5, y), ch, 2.5, 0.0, "center", "middle"))
            out.append(("text", "BORDER_TEXT", None, (W - m * 0.5, y), ch, 2.5, 0.0, "center", "middle"))

    def title_cells(self):
        """(x, y, w, h, key, label, text height) of the title block cells, x/y from the block's corner."""
        return [
            (0, 24, 120, 12, "title", "Title", 5.0), (120, 24, 60, 12, "number", "Drawing number", 3.5),
            (0, 12, 40, 12, "drawn", "Drawn by", 3.0), (40, 12, 30, 12, "date", "Date", 3.0),
            (70, 12, 25, 12, "scale", "Scale", 3.5), (95, 12, 25, 12, "size", "Size", 3.5),
            (120, 12, 40, 12, "projection", "Projection", 0), (160, 12, 20, 12, "rev", "Rev", 3.5),
            (0, 0, 60, 12, "company", "Company", 3.0), (60, 0, 60, 12, "material", "Material", 3.0),
            (120, 0, 40, 12, "tolerance", "General tolerances", 2.5), (160, 0, 20, 12, "sheet", "Sheet", 3.0),
        ]

    def title_value(self, key):
        if key == "scale":
            return scale_label(float(self.sheet.get("scale", 1.0)))
        if key == "size":
            return self.sheet.get("size", "A3")
        if key == "title" and not self.title.get("title"):
            return self.name
        return str(self.title.get(key, ""))

    def _title_block(self, out, W, H):
        x0, y0 = W - MARGIN - TB_W, MARGIN
        L = lambda a, b, layer="TITLE": out.append(("line", layer, None, a, b))
        L((x0, y0), (x0 + TB_W, y0))
        L((x0 + TB_W, y0), (x0 + TB_W, y0 + TB_H))
        L((x0 + TB_W, y0 + TB_H), (x0, y0 + TB_H))
        L((x0, y0 + TB_H), (x0, y0))
        for (cx, cy, cw, ch, key, label, th) in self.title_cells():
            X, Y = x0 + cx, y0 + cy
            L((X, Y), (X + cw, Y), "TITLE_THIN")
            L((X, Y), (X, Y + ch), "TITLE_THIN")
            out.append(("text", "TITLE_LABEL", None, (X + 1.2, Y + ch - 1.2), label, 1.8, 0.0, "left", "top"))
            if key == "projection":
                self._projection_symbol(out, X + cw / 2, Y + ch * 0.42)
                continue
            val = self.title_value(key)
            if val:
                # shrink long text to fit its box
                h = th
                while h > 1.8 and len(val) * h * 0.62 > cw - 3:
                    h -= 0.25
                out.append(("text", "TITLE_TEXT", None, (X + 1.5, Y + 2.2), val, h, 0.0, "left", "baseline"))

    def _projection_symbol(self, out, cx, cy):
        """ISO first- or third-angle projection symbol."""
        first = self.sheet.get("projection", "first") == "first"
        s = 1.0
        d_small, d_big, L_ = 3.2 * s, 6.4 * s, 7.0 * s
        cone_x = cx - 6 if first else cx + 6
        circ_x = cx + 6 if first else cx - 6
        # truncated cone side view, small end on the left
        a0, a1 = cone_x - L_ / 2, cone_x + L_ / 2
        pts = [(a0, cy - d_small / 2), (a1, cy - d_big / 2), (a1, cy + d_big / 2), (a0, cy + d_small / 2)]
        for i in range(4):
            out.append(("line", "TITLE_THIN", None, pts[i], pts[(i + 1) % 4]))
        out.append(("circle", "TITLE_THIN", None, (circ_x, cy), d_big / 2))
        out.append(("circle", "TITLE_THIN", None, (circ_x, cy), d_small / 2))
        for (x, w) in ((cone_x, L_ + 3), (circ_x, d_big + 3)):
            out.append(("line", "CENTER", None, (x - w / 2, cy), (x + w / 2, cy)))
        out.append(("line", "CENTER", None, (circ_x, cy - d_big / 2 - 1.5), (circ_x, cy + d_big / 2 + 1.5)))

    def _view_items(self, out, v):
        if v.get("type") == "detail" and v.get("detail"):
            self._detail_items(out, v)
            return
        cv = self.curves(v)
        layers = [("visible", "VISIBLE")]
        if v.get("hidden"):
            layers.append(("hidden", "HIDDEN"))
        if v.get("tangent"):
            layers.append(("tangent", "TANGENT"))
        o = np.asarray(v["o"], float)
        s = self.view_scale(v)
        T = lambda q: (float(o[0] + s * q[0]), float(o[1] + s * q[1]))
        for key, layer in layers:
            for c in cv.get(key, []):
                if c[0] == "line":
                    out.append(("line", layer, v["id"], T(c[1]), T(c[2])))
                elif c[0] == "circle":
                    a0, a1 = math.degrees(c[3]), math.degrees(c[4])
                    if a1 - a0 >= 359.999:
                        out.append(("circle", layer, v["id"], T(c[1]), c[2] * s))
                    else:
                        out.append(("arc", layer, v["id"], T(c[1]), c[2] * s, a0, a1))
                else:
                    out.append(("poly", layer, v["id"], [T(q) for q in c[1]]))
        if v.get("centre_marks", True):
            marks = {}
            for _c, _ax, r, uv, _i in self.circles_in_view(v, full_only=True):
                k = (round(float(uv[0]), 4), round(float(uv[1]), 4))
                if k not in marks or r > marks[k][0]:
                    marks[k] = (r, uv)
            for r, uv in marks.values():
                C = T(uv)
                ext = r * s + 2.0
                out.append(("line", "CENTER", v["id"], (C[0] - ext, C[1]), (C[0] + ext, C[1])))
                out.append(("line", "CENTER", v["id"], (C[0], C[1] - ext), (C[0], C[1] + ext)))
        bends = (self.source or {}).get("bends") if (self.source or {}).get("flat") else None
        if bends and self.shape is not None:
            # flat pattern: bend lines with their direction / angle / radius, where the sheet is seen face-on
            n = self._sheet_normal()
            _r, _u, dv = self.axes(v)
            if n is not None and abs(abs(float(n @ dv)) - 1) < 1e-6:
                for p0, p1, lab in bends:
                    A, B = T(self.project(v, p0)), T(self.project(v, p1))
                    out.append(("line", "BEND", v["id"], A, B))
                    d = np.subtract(B, A)
                    ang = self._reading_angle(d) if np.linalg.norm(d) > 1e-9 else 0.0
                    rd = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
                    mid = (np.asarray(A) + np.asarray(B)) / 2 + np.array([-rd[1], rd[0]]) * 0.8
                    out.append(("text", "BEND", v["id"], tuple(mid), lab, 2.5, ang, "center", "bottom"))
        if v.get("type") == "section" and v.get("cut"):
            self._hatch_items(out, v, T)
        for ch in self.views:
            if ch.get("parent") != v["id"]:
                continue
            if ch.get("type") == "section" and ch.get("cut"):
                self._cut_line_items(out, v, ch)
            elif ch.get("type") == "detail" and ch.get("detail"):
                c = T(ch["detail"]["c"])
                rr = ch["detail"]["r"] * s
                out.append(("circle", "DETAIL", v["id"], c, rr))
                q = (c[0] + rr * 0.75 + 1.5, c[1] + rr * 0.75 + 1.5)
                out.append(("text", "NOTE", v["id"], q, ch["letter"], TEXT_H * 1.4, 0.0, "left", "bottom"))
        title = self.view_title(v)
        if title:
            x0, y0, x1, _y1 = self.view_box(v)
            out.append(("text", "NOTE", v["id"], ((x0 + x1) / 2, y0 - 6), title, TEXT_H, 0.0, "center", "top"))

    def _hatch_items(self, out, v, T):
        """Hatch the cut faces of a section view: 45° lines, alternating direction from one part to the next."""
        r, u, d = self.axes(v)
        spacing = 2.5 / self.view_scale(v)                   # 2.5 mm apart on the paper, whatever the scale
        p0 = v["cut"]["p0"]
        try:
            groups = K.section_faces(self.view_shape(v), p0, d)
        except Exception:
            return
        for i, fs in enumerate(groups):
            along = _unit(r + u) if i % 2 == 0 else _unit(r - u)
            across = _unit(np.cross(d, along))
            off = spacing * (0.5 if i % 3 == 2 else 0.0)
            for f in fs:
                for P, Q in K.hatch_segments(f, np.asarray(p0) + across * off, along, across, spacing):
                    out.append(("line", "HATCH", v["id"], T(self.project(v, P)), T(self.project(v, Q))))

    def _cut_line_items(self, out, parent, sec):
        """The cutting line on the parent view: chain line with thick ends, arrows showing which way you look,
        and the section's letter at each end."""
        pd_uv = self.project(parent, np.asarray(sec["d"], float))          # towards the section's viewer
        if np.linalg.norm(pd_uv) < 1e-9:
            return
        nd = _unit(pd_uv)
        ld = np.array([-nd[1], nd[0]])
        P0 = self.to_paper(parent, self.project(parent, sec["cut"]["p0"]))
        x0, y0, x1, y1 = self.view_box(parent)
        corners = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]) - P0
        ts = corners @ ld
        t0, t1 = float(ts.min()) - 5.0, float(ts.max()) + 5.0
        A, B = P0 + ld * t0, P0 + ld * t1
        owner = parent["id"]
        out.append(("line", "CUT", owner, tuple(A), tuple(B)))
        for E, inward in ((A, ld), (B, -ld)):
            out.append(("line", "CUT_THICK", owner, tuple(E), tuple(E + inward * 6.0)))
            tail = E + nd * 8.0                         # arrow from the viewer's side onto the line
            out.append(("line", "CUT_THICK", owner, tuple(tail), tuple(E + nd * ARROW_L)))
            self._arrow(out, owner, tuple(E), -nd)
            out.append(("text", "NOTE", owner, tuple(tail + nd * 1.5), sec["letter"], TEXT_H * 1.4, 0.0,
                        "center", "bottom" if nd[1] >= 0 else "top"))

    def _detail_items(self, out, v):
        """A detail view: everything the parent view shows inside the circle (edges, hatching, centre lines),
        enlarged, with the circle as its boundary and its title underneath."""
        parent = self.view(v.get("parent"))
        if parent is None:
            return
        c, rad = np.asarray(v["detail"]["c"], float), float(v["detail"]["r"])
        o, s = np.asarray(v["o"], float), self.view_scale(v)
        po, ps = np.asarray(parent["o"], float), self.view_scale(parent)
        back = lambda q: (np.asarray(q, float) - po) / ps                    # parent paper -> model view coords
        T = lambda q: (float(o[0] + s * q[0]), float(o[1] + s * q[1]))
        for it in self._parent_picture(parent):
            kind, layer = it[0], it[1]
            if kind == "line":
                pts = [back(it[3]), back(it[4])]
            elif kind == "poly":
                pts = [back(q) for q in it[3]]
            elif kind in ("circle", "arc"):
                cc, rr = back(it[3]), it[4] / ps
                a0, a1 = (0.0, 360.0) if kind == "circle" else (it[5], it[6])
                ts = np.radians(np.linspace(a0, a1, max(12, int(abs(a1 - a0) / 360 * 96)) + 1))
                pts = list(np.stack([cc[0] + rr * np.cos(ts), cc[1] + rr * np.sin(ts)], axis=1))
            else:
                continue
            for piece in _clip_to_circle(pts, c, rad):
                if len(piece) == 2:
                    out.append(("line", layer, v["id"], T(piece[0]), T(piece[1])))
                else:
                    out.append(("poly", layer, v["id"], [T(q) for q in piece]))
        out.append(("circle", "DETAIL", v["id"], T(c), rad * s))
        x0, y0, x1, _y1 = self.view_box(v)
        out.append(("text", "NOTE", v["id"], ((x0 + x1) / 2, y0 - 6), self.view_title(v), TEXT_H, 0.0,
                    "center", "top"))

    def _parent_picture(self, parent):
        """The drawing of a view's model (edges, hatching, centre lines — not its labels or the markers of other
        views), cached while that view is unchanged. Detail views are cut out of it."""
        cache = self.__dict__.setdefault("_picture_cache", {})
        key = self._view_key(parent)
        hit = cache.get(parent["id"])
        if hit is not None and hit[0] == key:
            return hit[1]
        lst = []
        self._view_items(lst, parent)
        keep = {"VISIBLE", "HIDDEN", "TANGENT", "HATCH", "CENTER", "BEND"}
        pic = [it for it in lst if it[1] in keep and it[0] in ("line", "poly", "circle", "arc")]
        cache[parent["id"]] = (key, pic)
        return pic

    def _note_items(self, out, a):
        lines = str(a.get("text", "")).split("\n")
        h = float(a.get("height", TEXT_H))
        x, y = a["pos"]
        for i, ln in enumerate(lines):
            out.append(("text", "NOTE", a["id"], (x, y - i * h * 1.6), ln, h, 0.0, "left", "top"))

    def _arrow(self, out, owner, tip, direction):
        """Filled arrowhead with its tip at `tip`, pointing along `direction`."""
        d = _unit(direction)
        n = np.array([-d[1], d[0]])
        tip = np.asarray(tip, float)
        base = tip - d * ARROW_L
        out.append(("tri", "DIM", owner, tuple(tip), tuple(base + n * ARROW_W), tuple(base - n * ARROW_W)))

    @staticmethod
    def _reading_angle(d):
        ang = math.degrees(math.atan2(d[1], d[0]))
        while ang > 90.0001:
            ang -= 180
        while ang <= -89.9999:
            ang += 180
        return ang

    def _dim_items(self, out, a):
        g = self.dim_geometry(a)
        if g is None:
            return
        kind, data, value = g
        lost = any(isinstance(a.get(k), dict) and a[k].get("lost") for k in ("a", "b", "b2"))
        layer = "DIM_LOST" if lost else "DIM"
        own = a["id"]
        text = self.dim_text(a, value)
        pos = np.asarray(a.get("pos", [0, 0]), float)
        if kind == "lin":
            A, B, o = data
            A, B = np.asarray(A, float), np.asarray(B, float)
            if o == "h":
                u = np.array([1.0, 0.0])
            elif o == "v":
                u = np.array([0.0, 1.0])
            else:
                u = _unit(B - A)
                if np.linalg.norm(B - A) < 1e-9:
                    u = np.array([1.0, 0.0])
            n = np.array([-u[1], u[0]])
            A2 = A + n * float((pos - A) @ n)
            B2 = B + n * float((pos - B) @ n)
            # extension lines (with a small gap at the part, running a little past the dimension line)
            for P0, P1 in ((A, A2), (B, B2)):
                e = P1 - P0
                L = float(np.linalg.norm(e))
                if L > GAP + 0.2:
                    ed = e / L
                    out.append(("line", layer, own, tuple(P0 + ed * GAP), tuple(P1 + ed * OVERSHOOT)))
            span = B2 - A2
            Ls = float(np.linalg.norm(span))
            sd = span / Ls if Ls > 1e-9 else u
            if Ls >= 2 * ARROW_L + 2:
                out.append(("line", layer, own, tuple(A2), tuple(B2)))
                self._arrow(out, own, A2, -sd)
                self._arrow(out, own, B2, sd)
            else:
                # too tight: arrows outside, pointing in
                out.append(("line", layer, own, tuple(A2 - sd * (ARROW_L + 4)), tuple(B2 + sd * (ARROW_L + 4))))
                self._arrow(out, own, A2, sd)
                self._arrow(out, own, B2, -sd)
            ang = self._reading_angle(sd)
            rd = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
            up = np.array([-rd[1], rd[0]])
            t = float((pos - A2) @ sd)
            if 0 <= t <= Ls:
                at = (A2 + B2) / 2
            else:
                at = A2 + sd * t
            at = at + up * 0.8
            out.append(("text", layer, own, tuple(at), text, TEXT_H, ang, "center", "bottom"))
            return
        C, R = data
        C = np.asarray(C, float)
        dv = pos - C
        dist = float(np.linalg.norm(dv))
        u = dv / dist if dist > 1e-9 else np.array([0.7071, 0.7071])
        E = C + u * R
        tw = len(text) * TEXT_H * 0.62
        if dist > R + 0.5:
            # leader from outside: arrow touching the circle, a line out to the text, and a short shoulder
            if kind == "dia":
                E2 = C - u * R
                out.append(("line", layer, own, tuple(E2), tuple(pos)))
                self._arrow(out, own, E, u)
                self._arrow(out, own, E2, -u)
            else:
                out.append(("line", layer, own, tuple(C), tuple(pos)))
                self._arrow(out, own, E, u)
            right = 1.0 if u[0] >= 0 else -1.0
            sh_end = pos + np.array([right * (tw + 2), 0.0])
            out.append(("line", layer, own, tuple(pos), tuple(sh_end)))
            out.append(("text", layer, own, (pos[0] + right * (tw / 2 + 1), pos[1] + 0.8), text, TEXT_H, 0.0,
                        "center", "bottom"))
        else:
            if kind == "dia":
                E2 = C - u * R
                out.append(("line", layer, own, tuple(E2), tuple(E)))
                self._arrow(out, own, E, u)
                self._arrow(out, own, E2, -u)
            else:
                out.append(("line", layer, own, tuple(C), tuple(E)))
                self._arrow(out, own, E, u)
            ang = self._reading_angle(u)
            rd = np.array([math.cos(math.radians(ang)), math.sin(math.radians(ang))])
            up = np.array([-rd[1], rd[0]])
            out.append(("text", layer, own, tuple(pos + up * 0.8), text, TEXT_H, ang, "center", "bottom"))

    # ------------------------------------------------------------------ files
    def to_json(self, embed=True):
        d = {"format": FILE_FORMAT, "version": FILE_VERSION, "name": self.name, "uid": self.uid,
             "source": copy.deepcopy(self.source), "sheet": self.sheet, "title": self.title,
             "views": self.views, "annots": self.annots, "bom": self.bom}
        if embed and self.shape is not None:
            try:
                raw = K.shape_to_brep(self.shape)
                d["_brep"] = base64.b64encode(raw).decode()
                # the exact views, so reopening doesn't have to work them out again
                import hashlib
                saved = []
                for v in self.views:
                    ex = K.hlr_cached(self.shape, v["d"], v["up"])
                    if ex is not None:
                        saved.append({"d": v["d"], "up": v["up"], "curves": ex})
                if saved:
                    d["_views"] = {"model": hashlib.sha1(raw).hexdigest(), "views": saved}
            except Exception:
                pass
        if self.path and d["source"] and d["source"].get("path"):
            try:
                d["source"]["relpath"] = os.path.relpath(d["source"]["path"],
                                                         os.path.dirname(os.path.abspath(self.path)))
            except ValueError:
                pass
        return json.dumps(d, indent=1)

    def save(self, path=None):
        self.path = path or self.path
        txt = self.to_json()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(txt)
        os.replace(tmp, self.path)
        self.dirty = False

    @staticmethod
    def load(path, resolver=None, hlr_async=None):
        with open(path) as fh:
            d = json.load(fh)
        if d.get("format") != FILE_FORMAT:
            raise K.KernelError("Not a FreeShape drawing")
        g = DrawingDoc()
        g.name = d.get("name", "Drawing 1")
        g.uid = d.get("uid") or g.uid
        g.source = d.get("source")
        g.sheet.update(d.get("sheet", {}))
        g.title.update(d.get("title", {}))
        g.views = d.get("views", [])
        g.annots = d.get("annots", [])
        g.bom = d.get("bom", [])
        g.path = path
        src = g.source or {}
        if src.get("relpath") and not (src.get("path") and os.path.exists(src["path"])):
            cand = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(path)), src["relpath"]))
            if os.path.exists(cand):
                src["path"] = cand
        if d.get("_brep"):
            try:
                g._fallback = K.shape_from_brep(base64.b64decode(d["_brep"]))
            except Exception:
                g._fallback = None
        nums = [int("".join(c for c in x["id"] if c.isdigit()) or 0) for x in g.views + g.annots]
        g._counter = max(nums + [0])
        g._saved_views = d.get("_views")
        g.resolver = resolver
        g.hlr_async = hlr_async
        g.refresh(notify=False)
        return g

    def _use_saved_views(self):
        """Reuse the exact views stored in the file when the model is the same as when it was saved."""
        sv, self._saved_views = self._saved_views, None
        if not sv or self.shape is None:
            return
        try:
            import hashlib
            if hashlib.sha1(K.shape_to_brep(self.shape)).hexdigest() != sv.get("model"):
                return
            for item in sv.get("views", []):
                K.hlr_store(self.shape, item["d"], item["up"], item["curves"])
        except Exception:
            pass


# =========================================================================== export
LAYER_STYLE = {
    # layer: (pen width mm, dash pattern in mm or None, DXF colour, DXF linetype)
    "TRIM": (0.1, None, 8, "CONTINUOUS"),
    "BORDER": (0.7, None, 7, "CONTINUOUS"),
    "BORDER_THIN": (0.35, None, 7, "CONTINUOUS"),
    "BORDER_TEXT": (0.25, None, 7, "CONTINUOUS"),
    "TITLE": (0.5, None, 7, "CONTINUOUS"),
    "TITLE_THIN": (0.25, None, 7, "CONTINUOUS"),
    "TITLE_LABEL": (0.18, None, 8, "CONTINUOUS"),
    "TITLE_TEXT": (0.25, None, 7, "CONTINUOUS"),
    "VISIBLE": (0.5, None, 7, "CONTINUOUS"),
    "HIDDEN": (0.25, (3.0, 1.5), 8, "DASHED"),
    "TANGENT": (0.18, None, 8, "CONTINUOUS"),
    "CENTER": (0.18, (8.0, 1.5, 1.5, 1.5), 1, "CENTER"),
    "DIM": (0.18, None, 3, "CONTINUOUS"),
    "DIM_LOST": (0.18, None, 1, "CONTINUOUS"),
    "NOTE": (0.25, None, 7, "CONTINUOUS"),
    "BEND": (0.18, (6.0, 1.5, 1.5, 1.5), 1, "CENTER"),
    "HATCH": (0.18, None, 8, "CONTINUOUS"),
    "CUT": (0.25, (8.0, 1.5, 1.5, 1.5), 7, "CENTER"),
    "CUT_THICK": (0.7, None, 7, "CONTINUOUS"),
    "DETAIL": (0.25, None, 7, "CONTINUOUS"),
    "BOM": (0.25, None, 7, "CONTINUOUS"),
    "BOM_THICK": (0.5, None, 7, "CONTINUOUS"),
    "BALLOON": (0.25, None, 7, "CONTINUOUS"),
}


def export_dxf(doc: DrawingDoc, path, full_size=False):
    """Write the sheet as a DXF file for AutoCAD.
    full_size: scale everything so the views are at true size (1:1 model units) instead of paper size.
    With ezdxf installed (normally it is): AutoCAD 2010 DXF with real, editable AutoCAD dimensions and an Arial
    text style. Without it: a plain R12 DXF with the dimensions drawn as lines and text."""
    try:
        import ezdxf  # noqa: F401
    except ImportError:
        return _export_dxf_r12(doc, path, full_size)
    return _export_dxf_ezdxf(doc, path, full_size)


def _export_dxf_ezdxf(doc: DrawingDoc, path, full_size=False):
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    sc = float(doc.sheet.get("scale", 1.0))
    k = 1.0 / sc if full_size else 1.0
    S = lambda p: (float(p[0]) * k, float(p[1]) * k)
    out = ezdxf.new("R2010", setup=False)
    out.units = ezdxf.units.MM
    out.header["$MEASUREMENT"] = 1
    out.header["$LUNITS"] = 2
    out.header["$LTSCALE"] = k
    out.header["$PSLTSCALE"] = 0
    W, H = doc.sheet_size()
    out.header["$LIMMIN"] = (0, 0)
    out.header["$LIMMAX"] = (W * k, H * k)
    from .dxf import LINETYPES
    for name, (desc, pat) in LINETYPES.items():
        if pat and name not in out.linetypes:
            out.linetypes.add(name, pattern=[sum(abs(x) for x in pat)] + list(pat), description=desc)
    std = out.styles.get("Standard")
    std.dxf.font = "arial.ttf"
    for name, (_wd, _dash, col, lt) in LAYER_STYLE.items():
        if name == "TRIM":
            continue
        out.layers.add(name, color=col, linetype=lt if lt in out.linetypes else "Continuous")
    msp = out.modelspace()
    dim_ids = {a["id"] for a in doc.annots if a["type"] != "note"}
    align = {("left", "baseline"): TextEntityAlignment.LEFT, ("left", "bottom"): TextEntityAlignment.BOTTOM_LEFT,
             ("left", "middle"): TextEntityAlignment.MIDDLE_LEFT, ("left", "top"): TextEntityAlignment.TOP_LEFT,
             ("center", "baseline"): TextEntityAlignment.CENTER,
             ("center", "bottom"): TextEntityAlignment.BOTTOM_CENTER,
             ("center", "middle"): TextEntityAlignment.MIDDLE_CENTER,
             ("center", "top"): TextEntityAlignment.TOP_CENTER, ("right", "baseline"): TextEntityAlignment.RIGHT,
             ("right", "bottom"): TextEntityAlignment.BOTTOM_RIGHT,
             ("right", "middle"): TextEntityAlignment.MIDDLE_RIGHT, ("right", "top"): TextEntityAlignment.TOP_RIGHT}
    for it in doc.items():
        kind, layer, owner = it[0], it[1], it[2]
        if layer == "TRIM" or owner in dim_ids:
            continue
        at = {"layer": layer}
        if kind == "line":
            msp.add_line(S(it[3]), S(it[4]), dxfattribs=at)
        elif kind == "circle":
            msp.add_circle(S(it[3]), it[4] * k, dxfattribs=at)
        elif kind == "arc":
            msp.add_arc(S(it[3]), it[4] * k, it[5], it[6], dxfattribs=at)
        elif kind == "poly":
            msp.add_lwpolyline([S(q) for q in it[3]], dxfattribs=at)
        elif kind == "tri":
            msp.add_solid([S(it[3]), S(it[4]), S(it[5])], dxfattribs=at)
        elif kind == "text":
            _k, _l, _o, pos, txt, h, ang, ha, va = it
            t = msp.add_text(txt, height=h * k, rotation=ang, dxfattribs={**at, "style": "Standard"})
            t.set_placement(S(pos), align=align.get((ha, va), TextEntityAlignment.LEFT))
    # real AutoCAD dimensions: they measure the paper, so DIMLFAC turns that back into model sizes
    for a in doc.annots:
        if a["type"] == "note":
            continue
        try:
            _ezdxf_dim(doc, msp, a, k, sc, full_size)
        except Exception:
            # if AutoCAD-style can't be made for this one, draw it as lines and text like on screen
            items = []
            doc._dim_items(items, a)
            for it in items:
                at = {"layer": it[1]}
                if it[0] == "line":
                    msp.add_line(S(it[3]), S(it[4]), dxfattribs=at)
                elif it[0] == "tri":
                    msp.add_solid([S(it[3]), S(it[4]), S(it[5])], dxfattribs=at)
                elif it[0] == "text":
                    t = msp.add_text(it[4], height=it[5] * k, rotation=it[6], dxfattribs={**at, "style": "Standard"})
                    t.set_placement(S(it[3]), align=align.get((it[7], it[8]), TextEntityAlignment.LEFT))
    out.saveas(path)


def _ezdxf_dim(doc, msp, a, k, sc, full_size):
    g = doc.dim_geometry(a)
    if g is None:
        return
    kind, data, value = g
    lost = any(isinstance(a.get(x), dict) and a[x].get("lost") for x in ("a", "b", "b2"))
    S = lambda p: (float(p[0]) * k, float(p[1]) * k)
    ov = {"dimlfac": 1.0 if full_size else 1.0 / sc, "dimtxt": TEXT_H * k, "dimasz": ARROW_L * k,
          "dimexo": GAP * k, "dimexe": OVERSHOOT * k, "dimgap": 0.8 * k, "dimtad": 1, "dimtih": 0, "dimtoh": 0,
          "dimtxsty": "Standard", "dimclrd": 0, "dimclre": 0, "dimclrt": 0, "dimdsep": ord("."),
          "dimtmove": 1}
    if a.get("dp") is None:
        ov.update(dimdec=3, dimzin=8)            # up to 3 places, no trailing zeros (like on screen)
    else:
        ov.update(dimdec=int(a["dp"]), dimzin=0)
    if a.get("text"):
        text = a["text"]
    else:
        text = f"{a.get('prefix', '')}<>{a.get('suffix', '')}"
    at = {"layer": "DIM_LOST" if lost else "DIM"}
    pos = np.asarray(a.get("pos", [0, 0]), float)
    if kind == "lin":
        A, B, o = (np.asarray(x, float) if not isinstance(x, str) else x for x in data)
        if o in ("h", "v"):
            d = msp.add_linear_dim(base=S(pos), p1=S(A), p2=S(B), angle=0 if o == "h" else 90, text=text,
                                   dimstyle="Standard", override=ov, dxfattribs=at)
        else:
            if (B[0] < A[0] - 1e-9) or (abs(B[0] - A[0]) <= 1e-9 and B[1] < A[1]):
                A, B = B, A                      # run left to right (or upwards) so the text reads the right way
            u = _unit(B - A)
            n = np.array([-u[1], u[0]])
            dist = float((pos - A) @ n) * k
            d = msp.add_aligned_dim(p1=S(A), p2=S(B), distance=dist, text=text, dimstyle="Standard",
                                    override=ov, dxfattribs=at)
    else:
        C, R = data
        C = np.asarray(C, float)
        v = pos - C
        ang = math.degrees(math.atan2(v[1], v[0])) if np.linalg.norm(v) > 1e-9 else 45.0
        if kind == "dia":
            d = msp.add_diameter_dim(center=S(C), radius=R * k, angle=ang, location=S(pos), text=text,
                                     dimstyle="Standard", override=ov, dxfattribs=at)
        else:
            if not a.get("text"):
                text = f"{a.get('prefix', '')}R<>{a.get('suffix', '')}"
            d = msp.add_radius_dim(center=S(C), radius=R * k, angle=ang, location=S(pos), text=text,
                                   dimstyle="Standard", override=ov, dxfattribs=at)
    d.render()


def _export_dxf_r12(doc: DrawingDoc, path, full_size=False):
    """Plain AutoCAD R12 DXF (used if ezdxf isn't installed)."""
    from .dxf import DxfWriter
    k = 1.0 / float(doc.sheet.get("scale", 1.0)) if full_size else 1.0
    w = DxfWriter()
    for name, (_wd, _dash, col, lt) in LAYER_STYLE.items():
        if name == "TRIM":
            continue
        w.layer(name, col, lt)
    S = lambda p: (p[0] * k, p[1] * k)
    for it in doc.items():
        kind, layer = it[0], it[1]
        if layer == "TRIM":
            continue
        if kind == "line":
            w.line(layer, S(it[3]), S(it[4]))
        elif kind == "circle":
            w.circle(layer, S(it[3]), it[4] * k)
        elif kind == "arc":
            w.arc(layer, S(it[3]), it[4] * k, it[5], it[6])
        elif kind == "poly":
            w.polyline(layer, [S(q) for q in it[3]])
        elif kind == "tri":
            w.solid(layer, S(it[3]), S(it[4]), S(it[5]))
        elif kind == "text":
            _k, _l, _o, pos, txt, h, ang, ha, va = it
            w.text(layer, S(pos), txt, h * k, ang, ha, va)
    W, H = doc.sheet_size()
    w.save(path, (0, 0), (W * k, H * k), ltscale=k)
