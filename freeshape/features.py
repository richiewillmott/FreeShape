"""Parametric features and the regeneration engine.

A feature is a plain dict: {"id", "type", "name", "suppressed", "params": {...}}.
Rebuilding replays features in order into a ModelState. Geometry references
(faces, edges, sketch regions) are stored as geometric signatures and re-found
after every rebuild, so editing an earlier feature doesn't break later ones
in the common cases.
"""
from __future__ import annotations

import base64
import math
import os
import tempfile
from dataclasses import dataclass, field

import numpy as np

from . import kernel as K
from .sketch import Sketch
from .expr import parse_number, var_names

DEFAULT_COLOR = (0.60, 0.74, 0.85)      # Onshape-style steel blue

FEATURE_LABELS = {
    "sketch": "Sketch", "extrude": "Extrude", "revolve": "Revolve", "fillet": "Fillet",
    "chamfer": "Chamfer", "shell": "Shell", "plane": "Plane", "mirror": "Mirror",
    "linear_pattern": "Linear pattern", "circular_pattern": "Circular pattern",
    "curve_pattern": "Curve pattern", "point_pattern": "Point pattern", "mate_connector": "Mate connector",
    "boolean": "Boolean", "import": "Import", "hole": "Hole", "variable": "Variable", "measure_value": "Variable", "derived": "Derived",
    "sweep": "Sweep", "loft": "Loft", "draft": "Draft", "split": "Split", "move": "Transform", "thread": "Thread",
    "sm_base": "Sheet metal", "sm_flange": "Flange", "sm_hem": "Hem", "sm_jog": "Jog", "sm_tab": "Tab",
    "sm_convert": "Convert to sheet metal", "delete_face": "Delete face", "move_face": "Move face", "offset_surface": "Offset surface",
}

# ISO metric screw data (coarse threads).  clearance: close / normal / loose (ISO 273)
HOLE_SIZES = ["M2", "M2.5", "M3", "M4", "M5", "M6", "M8", "M10", "M12"]
CLEARANCE = {"M2": (2.2, 2.4, 2.6), "M2.5": (2.7, 2.9, 3.1), "M3": (3.2, 3.4, 3.6), "M4": (4.3, 4.5, 4.8),
             "M5": (5.3, 5.5, 5.8), "M6": (6.4, 6.6, 7.0), "M8": (8.4, 9.0, 10.0), "M10": (10.5, 11.0, 12.0),
             "M12": (13.0, 13.5, 14.5)}
TAP_DRILL = {"M2": 1.6, "M2.5": 2.05, "M3": 2.5, "M4": 3.3, "M5": 4.2, "M6": 5.0, "M8": 6.8, "M10": 8.5,
             "M12": 10.2}
COUNTERBORE = {"M2": (4.4, 2.2), "M2.5": (5.5, 2.7), "M3": (6.5, 3.3), "M4": (8.0, 4.4), "M5": (10.0, 5.4),
               "M6": (11.0, 6.4), "M8": (15.0, 8.6), "M10": (18.0, 10.8), "M12": (20.0, 13.0)}  # socket head
COUNTERSINK = {"M2": 4.4, "M2.5": 5.5, "M3": 6.3, "M4": 9.4, "M5": 10.4, "M6": 12.6, "M8": 17.3, "M10": 20.0,
               "M12": 24.0}  # 90° flat head


def hole_centres(sketch):
    """Hole positions in a sketch: its points and the centres of its circles.
    Returns ([(u, v)], [circle diameter or None])."""
    centres, sizes = [], []
    for eid, e in sketch.entities.items():
        if e["type"] == "point" and not e.get("virtual"):
            centres.append(tuple(sketch.points[e["p"]]))
            sizes.append(None)
        elif e["type"] == "circle":
            centres.append(tuple(sketch.points[e["c"]]))
            sizes.append(None if e.get("construction") else 2 * float(e["r"]))
    return centres, sizes


def hole_dims(p):
    """(drill diameter, counterbore (dia, depth) or None, countersink dia or None, label)."""
    std = p.get("standard", "custom")
    if std == "sketch":
        style = p.get("style", "simple")
        cb = (float(p.get("counterbore_dia", 9)), float(p.get("counterbore_depth", 3))) if style == "counterbore" else None
        cs = float(p.get("countersink_dia", 10)) if style == "countersink" else None
        return 1.0, cb, cs, "Size of the sketch circles"
    size = p.get("size", "M4")
    style = p.get("style", "simple")
    if std == "custom" or size not in TAP_DRILL:
        cb = (float(p.get("counterbore_dia", 9)), float(p.get("counterbore_depth", 3))) if style == "counterbore" else None
        cs = float(p.get("countersink_dia", 10)) if style == "countersink" else None
        return float(p.get("diameter", 5)), cb, cs, f"Ø{float(p.get('diameter', 5)):g}"
    if std == "tapped":
        d = TAP_DRILL[size]
        label = f"{size} tapped"
    else:
        d = CLEARANCE[size][{"close": 0, "normal": 1, "loose": 2}.get(p.get("fit", "normal"), 1)]
        label = f"{size} clearance"
    cb = COUNTERBORE[size] if style == "counterbore" else None
    cs = COUNTERSINK[size] if style == "countersink" else None
    return d, cb, cs, label


@dataclass
class Part:
    id: str
    name: str
    shape: object
    color: tuple = DEFAULT_COLOR
    face_colors: dict = None      # face index -> (r, g, b), e.g. from a coloured STEP file


def _surface_key_match(a, b, tol):
    """Do two faces lie on the same underlying surface (same plane, or same cylinder)?"""
    if a["type"] != b["type"]:
        return False
    if "n" in a and "n" in b:
        na, nb = np.array(a["n"]), np.array(b["n"])
        if na @ nb < 1 - 1e-6:
            return False
        return abs(na @ np.array(a["c"]) - nb @ np.array(b["c"])) < tol
    if "r" in a and "r" in b:
        if abs(a["r"] - b["r"]) > tol or abs(abs(np.dot(a["axis"], b["axis"])) - 1) > 1e-6:
            return False
        d = np.array(b["axis_p"]) - np.array(a["axis_p"])
        ax = np.array(a["axis"])
        return np.linalg.norm(d - (d @ ax) * ax) < tol
    return False


def carry_face_colors(old: Part, new_shape):
    """After a feature changes a part, keep the colours of faces that are still there —
    including faces that were trimmed (they stay on the same plane or cylinder)."""
    if not old.face_colors:
        return None
    old_faces = K.faces(old.shape)
    lo, hi = K.bbox(old.shape)
    tol = 1e-6 * (1 + float(np.linalg.norm(hi - lo)))
    sigs = []
    for i, col in old.face_colors.items():
        if i < len(old_faces):
            f = old_faces[i]
            flo, fhi = K.bbox(f)
            sigs.append((K.face_info(f), flo - tol * 10, fhi + tol * 10, col))
    out = {}
    for j, f in enumerate(K.faces(new_shape)):
        inf = K.face_info(f)
        c = np.array(inf["c"])
        # 1) unchanged face: same centre and area
        for oi, flo, fhi, col in sigs:
            if abs(oi["area"] - inf["area"]) <= 1e-6 * max(oi["area"], 1) and np.linalg.norm(np.array(oi["c"]) - c) < tol * 10:
                out[j] = col
                break
        else:
            # 2) trimmed face: same surface, and it sits within the old face's extent
            nlo, nhi = K.bbox(f)
            for oi, flo, fhi, col in sigs:
                if _surface_key_match(oi, inf, tol * 10) and np.all(nlo >= flo) and np.all(nhi <= fhi):
                    out[j] = col
                    break
    return out or None


@dataclass
class SketchResult:
    plane: K.Plane
    sketch: Sketch
    regions: list            # TopoDS_Face list
    region_info: list        # signature per region (see region_signature)
    bb: tuple = None         # (lo, hi) of all regions, for position-relative matching


@dataclass
class ModelState:
    parts: dict = field(default_factory=dict)        # id -> Part (ordered)
    sketches: dict = field(default_factory=dict)     # feature id -> SketchResult
    planes: dict = field(default_factory=dict)       # feature id -> Plane
    part_counter: int = 0
    vars: dict = field(default_factory=dict)        # variable name -> value (mm / degrees / plain)
    sheet: dict = field(default_factory=dict)       # part id -> sheetmetal.SheetModel (sheet metal parts)
    history: dict = field(default_factory=dict)     # feature id -> (state before it, state after it)
    measured: dict = field(default_factory=dict)    # Measure value feature id -> its value
    var_kinds: dict = field(default_factory=dict)   # variable name -> length | angle | area | volume | number | any
    connectors: dict = field(default_factory=dict)  # Mate connector feature id -> its frame {o, x, y, z, name}
    # parts made so far by each feature: feature id -> count. A part's id is "<feature id>:p<n>", n counting only
    # that feature's parts, so suppressing or deleting an earlier feature never renumbers it (references to it,
    # its name / colour and assemblies using it stay on the same body).
    part_seq: dict = field(default_factory=dict)
    # Reading a file saved before part ids were per feature: ids are made the old way ("<feature id>:<n>", n
    # counting every part in the model), and each old id -> the id the part has now is noted in legacy_map.
    legacy_map: dict | None = None

    def copy(self):
        return ModelState(dict(self.parts), dict(self.sketches), dict(self.planes), self.part_counter,
                          dict(self.vars), dict(self.sheet), dict(self.history), dict(self.measured),
                          dict(self.var_kinds), dict(self.connectors), dict(self.part_seq), self.legacy_map)

    def new_part(self, shape, fid, color=DEFAULT_COLOR):
        self.part_counter += 1
        k = self.part_seq[fid] = self.part_seq.get(fid, 0) + 1
        pid = f"{fid}:p{k}"
        if self.legacy_map is not None:
            old = f"{fid}:{self.part_counter}"
            self.legacy_map[old] = pid
            pid = old
        p = Part(pid, f"Part {self.part_counter}", shape, color)
        self.parts[pid] = p
        return p


# --------------------------------------------------------------------------- reference resolution

def make_ref(part_id, sub, kind, part_shape=None):
    info = K.face_info(sub) if kind == "face" else K.edge_info(sub)
    ref = {"part": part_id, **info}
    if part_shape is not None:
        lo, hi = K.bbox(part_shape)
        ref["bb"] = [lo.tolist(), hi.tolist()]
        try:                                   # where it sits in the part (see kernel.edge_context)
            ref["ctx"] = K.face_context(part_shape, sub) if kind == "face" else K.edge_context(part_shape, sub)
        except Exception:
            pass
    return ref


# how much each unit of (1 - cos) between a stored edge's face normals and a candidate's adds to its score: faces
# turned right round add 0.6 each (no match); turned 45° (an edited rotation upstream), about 0.18 in all
_ADJ_WEIGHT = 0.3


def context_penalty(shape, sub, kind, ref) -> float:
    """What a candidate face / edge adds to its match score for not sitting in the part the way the picked one did:
    0 when it agrees (or the reference is from before references kept their context), 1e9 when it can't be the same
    one — an outside corner for an inside one, a pocket floor for a top face, a face pointing the other way."""
    ctx = ref.get("ctx")
    if kind == "face":
        if "n" in ref:
            info = K.face_info(sub)
            if "n" in info and float(np.dot(info["n"], ref["n"])) < -0.5:
                return 1e9
        if not ctx or ctx.get("outline") not in ("convex", "concave"):
            return 0.0
        got = K.face_context(shape, sub).get("outline")
        return 1e9 if got in ("convex", "concave") and got != ctx["outline"] else 0.0
    if not ctx:
        return 0.0
    got = K.edge_context(shape, sub)
    if ctx.get("convex") is not None and got["convex"] is not None and got["convex"] != ctx["convex"]:
        return 1e9
    pen = 0.0
    if ctx.get("adj") and got["adj"]:
        for n in ctx["adj"]:
            best = max(float(np.dot(n, m)) for m in got["adj"])
            pen += (1.0 - best) * _ADJ_WEIGHT
    return pen


def _norm_pos(c, bb):
    lo, hi = np.array(bb[0]), np.array(bb[1])
    span = np.maximum(hi - lo, 1e-6)
    return (np.array(c) - lo) / span


def _score(info, ref, scale, bb=None):
    if info["type"] != ref["type"]:
        return 1e9
    s = np.linalg.norm(np.array(info["c"]) - np.array(ref["c"])) / scale
    if bb is not None and "bb" in ref:
        # position relative to the part's bounding box survives resizing
        s = min(s, 0.5 * np.linalg.norm(_norm_pos(info["c"], bb) - _norm_pos(ref["c"], ref["bb"])))
    size_key = "area" if ref["kind"] == "face" else "len"
    a, b = info[size_key], ref[size_key]
    s += abs(a - b) / max(a, b, 1e-9)
    for key in ("n", "d", "axis"):
        if key in ref and key in info:
            dot = abs(float(np.dot(info[key], ref[key])))
            s += (1 - dot) * 5
    if "r" in ref and "r" in info:
        s += abs(info["r"] - ref["r"]) / max(ref["r"], 1e-9)
    return s


def resolve(state: ModelState, ref, kind):
    """Find the face/edge in the current model best matching a stored signature.
    Returns (part_id, subshape) or raises KernelError."""
    parts = [state.parts[ref["part"]]] if ref.get("part") in state.parts else list(state.parts.values())
    if not parts:
        raise K.KernelError("Missing reference: no parts")
    best, best_s = None, 1e9
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for p in parts:
        a, b = K.bbox(p.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    for p in parts:
        lo_p, hi_p = K.bbox(p.shape)
        bb = [lo_p, hi_p]
        subs = K.faces(p.shape) if kind == "face" else K.edges(p.shape)
        for s in subs:
            info = K.face_info(s) if kind == "face" else K.edge_info(s)
            sc = _score(info, ref, scale, bb)
            if sc < min(best_s, 0.6):
                sc += context_penalty(p.shape, s, kind, ref)      # only worked out for close candidates
            if sc < best_s:
                best, best_s = (p.id, s), sc
    if best is None or best_s > 0.6:
        raise K.KernelError(f"Missing {kind} reference")
    return best


def resolve_plane(state: ModelState, ref) -> K.Plane:
    kind = ref.get("kind")
    if kind == "std":
        return K.std_plane(ref["name"])
    if kind == "plane":
        if ref["fid"] not in state.planes:
            raise K.KernelError("Missing plane reference")
        return state.planes[ref["fid"]]
    if kind == "face":
        _, f = resolve(state, ref["face"], "face")
        pl = K.planar_face_plane(f)
        if pl is None:
            raise K.KernelError("Face is not planar")
        # keep the stored sketch origin/x so sketches don't jump when the face changes size
        if "origin" in ref:
            o = np.array(ref["origin"], float)
            o = o - ((o - pl.origin) @ pl.normal) * pl.normal
            x = np.array(ref["xdir"], float)
            x = x - (x @ pl.normal) * pl.normal
            if np.linalg.norm(x) > 1e-6:
                pl = K.Plane(o, x / np.linalg.norm(x), pl.normal)
        return pl
    raise K.KernelError("Unknown plane reference")


def make_vertex_ref(part_id, pos, part_shape=None):
    ref = {"kind": "vertex", "part": part_id, "c": [float(x) for x in pos]}
    if part_shape is not None:
        lo, hi = K.bbox(part_shape)
        ref["bb"] = [lo.tolist(), hi.tolist()]
    return ref


def sketch_point_ref(sr, fid, point_id) -> dict:
    """A vertex reference to a point of a sketch (a line end, a circle centre, a sketch point). It follows the
    sketch point by its id; "c" is where it was when picked."""
    return {"kind": "sketch_point", "sketch": fid, "point": point_id,
            "c": [float(x) for x in sr.plane.to_world(*sr.sketch.points[point_id])]}


def resolve_sketch_curve(state: ModelState, ref):
    """("edge", TopoDS_Edge or compound) for one curve of a sketch, or ("vertex", TopoDS_Vertex) for a sketch
    point entity. ref = {"sketch": fid, "entity": eid}."""
    sr = state.sketches.get(ref.get("sketch"))
    if sr is None or ref.get("entity") not in sr.sketch.entities:
        raise K.KernelError("A measured sketch curve no longer exists")
    e = sr.sketch.entities[ref["entity"]]
    if e["type"] == "point":
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
        from OCP.gp import gp_Pnt
        p = sr.plane.to_world(*sr.sketch.points[e["p"]])
        return "vertex", BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(x) for x in p])).Vertex()
    tmp = Sketch.from_dict(sr.sketch.to_dict())
    tmp.entities = {ref["entity"]: dict(e, construction=False)}
    edges = K.make_sketch_edges(sr.plane, tmp.curves())
    if not edges:
        raise K.KernelError("Couldn't make an edge from this sketch curve")
    if len(edges) == 1:
        return "edge", edges[0]
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound
    comp, b = TopoDS_Compound(), BRep_Builder()
    b.MakeCompound(comp)
    for x in edges:
        b.Add(comp, x)
    return "edge", comp


def resolve_point(state: ModelState, ref) -> np.ndarray:
    """The position of the model vertex best matching a stored vertex reference."""
    if ref.get("kind") == "sketch_point":
        sr = state.sketches.get(ref.get("sketch"))
        if sr is None or ref.get("point") not in sr.sketch.points:
            raise K.KernelError("Missing vertex reference: the sketch point is gone")
        return np.asarray(sr.plane.to_world(*sr.sketch.points[ref["point"]]), float)
    parts =[state.parts[ref["part"]]] if ref.get("part") in state.parts else list(state.parts.values())
    if not parts:
        raise K.KernelError("Missing vertex reference: no parts")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for p in parts:
        a, b = K.bbox(p.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    c0 = np.array(ref["c"], float)
    best, best_s = None, 1e9
    for p in parts:
        a, b = K.bbox(p.shape)
        for v in K.vertex_points(p.shape):
            sc = float(np.linalg.norm(v - c0)) / scale
            if "bb" in ref:
                sc = min(sc, 0.5 * float(np.linalg.norm(_norm_pos(v, [a, b]) - _norm_pos(c0, ref["bb"]))))
            if sc < best_s:
                best, best_s = v, sc
    if best is None or best_s > 0.6:
        raise K.KernelError("Missing vertex reference")
    return np.asarray(best, float)


# --------------------------------------------------------------------------- planes
# A plane feature takes any mix of planes / planar faces, curved faces, edges and vertices; the plane type is
# either chosen by the user or worked out from what was selected ("auto"), as in Onshape.

PLANE_TYPES = [
    ("auto", "Automatic"), ("offset", "Offset"), ("plane_point", "Plane + point"), ("line_angle", "Line + angle"),
    ("point_normal", "Point + edge normal"), ("three_point", "Three points"), ("mid_plane", "Mid plane"),
    ("curve_point", "Curve + point"), ("tangent", "Tangent"),
]
PLANE_TYPE_NAMES = dict(PLANE_TYPES)


def plane_items(p):
    """[(kind, ref)] a plane feature was built from: kind is plane | face | edge | vertex."""
    refs = p.get("refs")
    if refs:
        return [(r["k"], r["ref"]) for r in refs]
    if p.get("ref"):
        return [("plane", p["ref"])]
    return []


def plane_role(kind, ref):
    if kind == "vertex":
        return "point"
    if kind == "plane":
        return "plane"
    if kind == "face":
        return "plane" if ref.get("type") == int(K.GeomAbs_Plane) else "surface"
    if kind == "edge":
        return "line" if ref.get("type") == int(K.GeomAbs_Line) else "curve"
    return "?"


def detect_plane_type(roles):
    """The plane type that fits a selection (a list of roles from plane_role), or None."""
    n = {r: roles.count(r) for r in ("plane", "surface", "line", "curve", "point", "?")}
    total = len(roles)
    if total == 0:
        return "offset"
    if n["?"]:
        return None
    if n["point"] == 3 and total == 3:
        return "three_point"
    if n["point"] == 1 and n["plane"] == 1 and total == 2:
        return "plane_point"
    if n["point"] == 1 and n["line"] == 1 and total == 2:
        return "point_normal"
    if n["point"] == 1 and n["curve"] == 1 and total == 2:
        return "curve_point"
    if n["plane"] == 2 and total == 2:
        return "mid_plane"
    if n["line"] == 1 and total - n["line"] - n["plane"] == 0 and n["plane"] <= 1:
        return "line_angle"
    if n["surface"] == 1 and n["point"] <= 1 and total == 1 + n["point"]:
        return "tangent"
    if n["plane"] == 1 and total == 1:
        return "offset"
    return None


def plane_type(p):
    """The effective type of a plane feature's params (resolves 'auto'); None when the selection fits nothing."""
    t = p.get("type") or "auto"
    if t == "auto":
        return detect_plane_type([plane_role(k, r) for k, r in plane_items(p)])
    return t


def _frame(origin, normal, xhint=None):
    n = np.asarray(normal, float)
    n = n / np.linalg.norm(n)
    o = np.asarray(origin, float)
    if xhint is not None:
        x = np.asarray(xhint, float)
        x = x - (x @ n) * n
        if np.linalg.norm(x) > 1e-6:
            return K.Plane(o, x / np.linalg.norm(x), n)
    for c in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        x = c - (c @ n) * n
        if np.linalg.norm(x) > 0.3:
            return K.Plane(o, x / np.linalg.norm(x), n)
    raise K.KernelError("Can't orient that plane")


def build_plane(state, p):
    """The datum plane a plane feature describes."""
    items = plane_items(p)
    typ = plane_type(p)
    if typ is None:
        raise K.KernelError("That selection doesn't fit a plane type — pick a plane or face, an edge, or points")
    flip = bool(p.get("flip"))
    by = {"plane": [], "surface": [], "line": [], "curve": [], "point": []}
    for k, ref in items:
        by.setdefault(plane_role(k, ref), []).append((k, ref))

    def face_plane(k, ref):
        return resolve_plane(state, ref if k == "plane" else {"kind": "face", "face": ref})

    def one(role, what, n=1):
        got = by[role] if isinstance(role, str) else [x for r in role for x in by[r]]
        if len(got) < n:
            raise K.KernelError(f"Select {what}")
        return got[:n]

    def done(pl):
        return K.Plane(pl.origin, pl.xdir, -pl.normal) if flip and typ != "offset" else pl

    if typ == "offset":
        base = face_plane(*by["plane"][0]) if by["plane"] else K.std_plane("Top")
        return base.offset(float(p.get("offset", 10)) * (-1 if flip else 1))
    if typ == "plane_point":
        base = face_plane(*one("plane", "a plane or planar face")[0])
        pt = resolve_point(state, one("point", "a vertex for the plane to pass through")[0][1])
        return done(K.Plane(pt, base.xdir, base.normal))
    if typ in ("point_normal", "curve_point"):
        _, eref = one(("line", "curve"), "an edge")[0]
        pt = resolve_point(state, one("point", "a vertex")[0][1])
        _, e = resolve(state, eref, "edge")
        if typ == "point_normal" and eref.get("type") == int(K.GeomAbs_Line):
            n = np.array(K.edge_info(e)["d"], float)          # perpendicular to a straight edge, through the point
            return done(_frame(pt, n))
        on, n = K.edge_closest(e, pt)                          # perpendicular to the curve where the point lies on it
        return done(_frame(on, n))
    if typ == "three_point":
        pts = [resolve_point(state, r) for _, r in one("point", "three vertices", 3)]
        n = np.cross(pts[1] - pts[0], pts[2] - pts[0])
        if np.linalg.norm(n) < 1e-9 * max(1.0, float(np.linalg.norm(pts[1] - pts[0]))):
            raise K.KernelError("The three points are in a straight line")
        x = pts[1] - pts[0]
        return done(_frame(pts[0], n / np.linalg.norm(n), x / np.linalg.norm(x)))
    if typ == "mid_plane":
        a, b = [face_plane(*it) for it in one("plane", "two planes or planar faces", 2)]
        na, nb = a.normal, b.normal
        if abs(float(na @ nb)) > 1 - 1e-9:                    # parallel: halfway between them
            d = float(na @ (b.origin - a.origin)) / 2
            return done(_frame(a.origin + d * na, na, a.xdir))
        nm = na + nb if float(na @ nb) >= 0 else na - nb      # not parallel: the plane bisecting them
        nm = nm / np.linalg.norm(nm)
        M = np.array([na, nb, np.cross(na, nb)])
        o = np.linalg.solve(M, np.array([na @ a.origin, nb @ b.origin, 0.0]))
        return done(_frame(o, nm))
    if typ == "line_angle":
        _, eref = one("line", "a straight edge")[0]
        _, e = resolve(state, eref, "edge")
        info = K.edge_info(e)
        a, d = np.array(info["p0"], float), np.array(info["d"], float)
        d = d / np.linalg.norm(d)
        u0 = None
        if by["plane"]:                                       # zero degrees: parallel to the chosen plane
            u0 = np.cross(face_plane(*by["plane"][0]).normal, d)
        if u0 is None or np.linalg.norm(u0) < 1e-6:           # otherwise: the plane holding the edge and world Z
            for c in (np.array([0, 0, 1.0]), np.array([1.0, 0, 0])):
                u0 = c - (c @ d) * d
                if np.linalg.norm(u0) > 1e-6:
                    break
        u0 = u0 / np.linalg.norm(u0)
        ang = math.radians(float(p.get("angle", 45)))
        u = math.cos(ang) * u0 + math.sin(ang) * np.cross(d, u0)
        return done(K.Plane(a, d, np.cross(d, u)))
    if typ == "tangent":
        faces_ = by["surface"] + [it for it in by["plane"] if it[0] == "face"]
        if not faces_:
            raise K.KernelError("Select a curved face")
        face = resolve(state, faces_[0][1], "face")[1]
        pt = resolve_point(state, by["point"][0][1]) if by["point"] else None
        on, n = K.surface_point_normal(face, pt)
        return done(_frame(on, n))
    raise K.KernelError("Unknown plane type")


def region_signature(face):
    info = K.face_info(face)
    return {"c": info["c"], "area": info["area"], "loops": K.face_loops(face), "edges": len(K.edges(face))}


def _region_score(info, r, bb):
    scale = math.sqrt(max(info["area"], r["area"], 1e-9))
    s = np.linalg.norm(np.array(info["c"]) - np.array(r["c"])) / scale
    if bb is not None and r.get("bb"):
        s = min(s, np.linalg.norm(_norm_pos(info["c"], bb) - _norm_pos(r["c"], r["bb"])))
    s += abs(info["area"] - r["area"]) / max(info["area"], r["area"], 1e-9)
    if "loops" in r:
        s += abs(info["loops"] - r["loops"]) * 1.0
        s += min(abs(info["edges"] - r["edges"]), 4) * 0.15
    return s


def resolve_regions(state: ModelState, refs):
    out = []
    for r in refs:
        sk = state.sketches.get(r["sketch"])
        if sk is None:
            raise K.KernelError("Missing sketch")
        best, best_s = None, 1e9
        for f, info in zip(sk.regions, sk.region_info):
            s = _region_score(info, r, sk.bb)
            if s < best_s:
                best, best_s = f, s
        if best is None or best_s > 1.5:
            raise K.KernelError("Missing sketch region")
        out.append(best)
    return out


def region_ref(state_or_result, sketch_fid, face):
    """Signature of a sketch region. Accepts a ModelState or the SketchResult itself."""
    sr = state_or_result
    if isinstance(sr, ModelState):
        sr = sr.sketches.get(sketch_fid)
    ref = {"sketch": sketch_fid, **region_signature(face)}
    if sr is not None and sr.bb is not None:
        ref["bb"] = [list(map(float, sr.bb[0])), list(map(float, sr.bb[1]))]
    return ref


def resolve_axis(state: ModelState, ref):
    kind = ref.get("kind")
    if kind == "std":
        d = {"X": [1, 0, 0], "Y": [0, 1, 0], "Z": [0, 0, 1]}[ref["name"]]
        return np.zeros(3), np.array(d, float)
    if kind == "sketch_line":
        sk = state.sketches.get(ref["sketch"])
        if sk is None or ref["line"] not in sk.sketch.entities:
            raise K.KernelError("Missing axis")
        e = sk.sketch.entities[ref["line"]]
        a = sk.plane.to_world(*sk.sketch.points[e["p1"]])
        b = sk.plane.to_world(*sk.sketch.points[e["p2"]])
        d = b - a
        if np.linalg.norm(d) < 1e-9:
            raise K.KernelError("Axis has zero length")
        return a, d / np.linalg.norm(d)
    if kind == "edge":
        _, e = resolve(state, ref["edge"], "edge")
        ln = K.edge_line(e)
        if ln is None:
            info = K.edge_info(e)
            if "axis" in info:
                return np.array(info["center"]), np.array(info["axis"])
            raise K.KernelError("Axis edge must be straight or circular")
        return ln
    raise K.KernelError("Select an axis")


# --------------------------------------------------------------------------- applying a body to the part set

def is_compound_part(part) -> bool:
    """A compound of solids (from the Compound feature): one part made of several solids that share their
    touching faces. Every other part is a single solid."""
    return part is not None and len(K.solids(part.shape)) > 1


def set_part_shape(state: ModelState, part: Part, shape):
    """Store a new shape for a part; disjoint solids become separate parts (like Onshape) — except a compound,
    which stays one part and is glued again so its solids keep sharing their touching faces."""
    pieces = K.solids(shape)
    if is_compound_part(state.parts.get(part.id)) and len(pieces) > 1:
        try:
            glued = K.glue_solids(pieces)
        except K.KernelError:
            glued = K.make_compound(pieces)
        state.parts[part.id] = Part(part.id, part.name, glued, part.color)
        return
    if len(pieces) <= 1:
        state.parts[part.id] = Part(part.id, part.name, pieces[0] if pieces else shape, part.color)
        return
    state.parts[part.id] = Part(part.id, part.name, pieces[0], part.color)
    for i, extra in enumerate(pieces[1:], 1):
        pid = f"{part.id}.{i}"
        state.parts[pid] = Part(pid, f"{part.name} ({i + 1})", extra, part.color)


def apply_body(state: ModelState, body, op: str, fid: str, scope=None):
    """Merge a tool body into the model according to op: new / add / remove / intersect."""
    if body is None:
        raise K.KernelError("Feature produced no geometry")
    sols = K.solids(body)
    if not sols:
        raise K.KernelError("Feature produced no solid")
    # curves (helixes) aren't material: never merge into them
    targets = [state.parts[p] for p in (scope or state.parts) if p in state.parts and K.faces(state.parts[p].shape)]
    if op == "add":
        # a compound keeps its separate solids: Add would fuse them into one, so new material isn't merged into it
        targets = [p for p in targets if not is_compound_part(p)]
    if op == "new" or (op == "add" and not targets):
        for s in sols:
            state.new_part(s, fid)
        return
    if op == "add":
        merged_into = []
        remaining = []
        for s in sols:
            hit = [p for p in targets if K.touches_or_intersects(p.shape, s)]
            if not hit:
                remaining.append(s)
                continue
            keep = hit[0]
            shape = keep.shape
            for p in hit[1:]:
                shape = K.fuse(shape, p.shape)
                state.parts.pop(p.id, None)
                targets.remove(p)
            shape = K.fuse(shape, s)
            state.parts[keep.id] = Part(keep.id, keep.name, shape, keep.color)
            targets[targets.index(keep)] = state.parts[keep.id]
            merged_into.append(keep.id)
        for s in remaining:
            state.new_part(s, fid)
        return
    hit_any = False
    for p in list(targets):
        if not K.intersects(p.shape, body):
            if op == "intersect":
                continue
            continue
        hit_any = True
        res = K.cut(p.shape, body) if op == "remove" else K.common(p.shape, body)
        if not K.solids(res):
            state.parts.pop(p.id)
            continue
        set_part_shape(state, p, res)
    if not hit_any:
        raise K.KernelError("Feature does not intersect any part")


# --------------------------------------------------------------------------- feature implementations

# --------------------------------------------------------------------------- variables & expressions

# params that hold numbers — these may also be expressions like "#wall * 2"
NUMERIC_KEYS = {"depth", "depth2", "angle", "radius", "distance", "thickness", "offset", "count", "spacing",
                "diameter", "counterbore_dia", "counterbore_depth", "dx", "dy", "dz", "rotate",
                "draft", "pitch", "twist", "scale_end", "major", "length", "clearance", "thread_clearance",
                "countersink_dia", "k", "relief_scale",
                # second-direction / other-side boxes, transform scale and helix: every number box a feature dialog
                # has must be listed here, or an expression typed in it reaches the geometry as text
                "thickness2", "offset2", "draft_angle", "draft_angle2", "angle2", "radius2", "distance2", "spacing2",
                "count2", "scale", "turns", "height", "start_angle"}

# number boxes that hold a whole count
WHOLE_KEYS = {"count", "count2"}


# what each numeric param holds, for unit checking: angles, plain numbers; the rest are lengths
ANGLE_KEYS = {"angle", "rotate", "draft", "twist", "angle2", "draft2", "start_angle", "pressure_angle",
              "draft_angle", "draft_angle2"}
NUMBER_KEYS = {"count", "count2", "k", "relief_scale", "scale_end", "turns", "ray_index", "teeth", "scale"}
ANY_KEYS = {"round_to", "density", "module"}


def param_kind(key) -> str:
    if key in ANGLE_KEYS:
        return "angle"
    if key in NUMBER_KEYS:
        return "number"
    if key in ANY_KEYS:
        return "any"
    return "length"


# numeric params of plugin features: feature type -> set of keys (filled in by freeshape.plugins)
EXTRA_NUMERIC: dict = {"measure_value": {"round_to", "ray_offset", "ray_index", "density"}, "compound": {"tolerance"}}


def prepare(f, state):
    """A copy of feature f with every expression evaluated against the variables defined so far."""
    p = f.get("params", {})
    keys = NUMERIC_KEYS | EXTRA_NUMERIC.get(f.get("type"), set())
    if not any(isinstance(p.get(k), str) for k in keys):
        return f
    p2 = dict(p)
    for k in keys:
        v = p2.get(k)
        if isinstance(v, str):
            want = param_kind(k) if k in NUMERIC_KEYS else "any"      # plugin fields: not checked
            from .expr import text_problem
            why = text_problem(v, state.vars)
            if why:
                raise K.KernelError(f"{k.replace('_', ' ').capitalize()} '{v}': {why}")
            val = parse_number(v, angle=(want == "angle"), variables=state.vars)
            if val is None:
                bad = [n for n in var_names(v) if n not in state.vars]
                raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{v}'")
            from .expr import check_units
            why = check_units(v, want, state.var_kinds)
            if why:
                raise K.KernelError(f"{k.replace('_', ' ').capitalize()} '{v}': {why}")
            p2[k] = int(round(val)) if k in WHOLE_KEYS else val
    return dict(f, params=p2)


def run_variable(state, f):
    p = f["params"]
    name = str(p.get("name", "")).strip().lstrip("#")
    if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
        raise K.KernelError("Variable names use letters, digits and _ (not starting with a digit)")
    val = parse_number(p.get("expr", ""), variables=state.vars)
    if val is None:
        bad = [n for n in var_names(p.get("expr", "")) if n not in state.vars]
        raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else "Enter a value or expression")
    state.vars[name] = val
    state.var_kinds[name] = "any"          # (the old Variable feature has no unit: never checked)


def run_measure_value(state, f):
    from . import measure_value as MV
    MV.run(state, f)


def run_sketch(state, f):
    p = f["params"]
    plane = resolve_plane(state, p["plane"])
    sk = Sketch.from_dict(p["sketch"])
    # dimensions driven by variables are re-evaluated and the sketch re-solved
    driven = [c for c in sk.constraints if isinstance(c.get("expr"), str)]
    from .expr import eval_text, text_problem
    for c in driven:
        why = text_problem(c["expr"], state.vars)
        if why:
            raise K.KernelError(f"Dimension '{c['expr']}': {why}")
        val = parse_number(c["expr"], angle=(c["type"] == "angle"), variables=state.vars)
        if val is None:
            bad = [n for n in var_names(c["expr"]) if n not in state.vars]
            raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{c['expr']}'")
        c["value"] = val
    if driven and not sk.solve():
        # a variable gave a dimension a value the sketch can't take (it would break its other constraints): say so,
        # rather than building regions from geometry that doesn't match its own dimensions. (A clash the sketch
        # already had, among dimensions no variable drives, is left as it was before.)
        failing = [c for c in driven if c["id"] in sk.status.get("bad", ())]
        if failing:
            dims = ", ".join(f"'{c['expr']}'" for c in failing[:3]) + (" …" if len(failing) > 3 else "")
            raise K.KernelError(f"The sketch can't be solved with these variable values — check dimension {dims}")
    for e in sk.entities.values():             # text that comes from variables: "PN-" + #part
        if e["type"] == "text" and e.get("expr"):
            try:
                e["text"] = eval_text(e["expr"], state.vars)
            except ValueError as err:
                raise K.KernelError(f"Sketch text {e['expr']}: {err}")
            if not e["text"].strip():
                raise K.KernelError(f"Sketch text {e['expr']} is empty")
    regions = K.sketch_regions(plane, sk.curves())
    bb = K.bbox(K.compound(regions)) if regions else None
    state.sketches[f["id"]] = SketchResult(plane, sk, regions, [region_signature(r) for r in regions], bb)


def _profiles(state, p):
    profs = resolve_regions(state, p.get("regions", []))
    for fr in p.get("faces", []):
        _, face = resolve(state, fr, "face")
        profs.append(face)
    return profs


def _profile_normal(state, p, profs):
    if p.get("regions"):
        return state.sketches[p["regions"][0]["sketch"]].plane.normal
    pl = K.planar_face_plane(profs[0])
    if pl is None:
        raise K.KernelError("Profile face must be planar")
    return pl.normal


def run_extrude(state, f):
    from . import body_options as BO
    p = f["params"]
    profs = BO.profiles(state, p, _profiles(state, p))
    if not profs:
        raise K.KernelError("Select sketch regions or a planar face")
    n = BO.profile_normal(state, p, profs)
    surface = BO.creation(p) == "surface"
    op = p.get("op", "add") if not surface else "new"
    flip = p.get("flip", False)
    end = p.get("end", "blind")
    depth = float(p.get("depth", 10))
    symmetric = end == "symmetric"
    cutting = op in ("remove", "intersect")
    if cutting and not p.get("regions"):
        # cutting from a face goes into the part by default
        flip = not flip
    # (files from before per-end options only had a Blind second end, and only after a Blind first end)
    second = bool(p.get("second")) and end not in ("symmetric", "through") and (end == "blind" or "end2" in p)
    end2 = p.get("end2", "blind") if second else None
    draft_on = bool(p.get("draft")) and not surface
    a1 = float(p.get("draft_angle", 0)) if draft_on and end not in UP_TO else 0.0
    a2 = float(p.get("draft_angle2", 0)) if draft_on and p.get("draft2") and end2 not in UP_TO else 0.0
    through_both = end == "through" and (p.get("both", False) or (cutting and p.get("regions")))

    def through_len():
        lo, hi = np.full(3, 1e18), np.full(3, -1e18)
        for part in state.parts.values():
            a, b = K.bbox(part.shape)
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        return float(np.linalg.norm(hi - lo)) * 2 + 10 if state.parts else depth

    def one_side(direction, end_type, length, up_to, offset, angle):
        """One end of the extrusion, from the sketch outwards: blind, through all or up to something — the
        same code for the first and the second end."""
        if end_type in UP_TO:
            q = dict(p, end=end_type, up_to=up_to, offset=offset)
            body = K.combine([_sweep_up_to(state, q, prof, "extrude", direction) for prof in profs])
        else:
            body = K.extrude(profs, direction, through_len() if end_type == "through" else length)
        if angle:
            body = BO.taper(body, _profile_origin(state, p, profs), n, direction, angle)
        return body

    mirror_end = second and end2 == "symmetric"

    def mirror_plane():
        nn = np.asarray(n, float) / np.linalg.norm(n)
        x = np.cross(nn, [0, 0, 1] if abs(nn[2]) < 0.9 else [1, 0, 0])
        return K.Plane(_profile_origin(state, p, profs), x / np.linalg.norm(x), nn)

    def make(direction):
        if mirror_end:
            # second end "Symmetric": whatever the first end is (up to a vertex, a draft…), the other side is its
            # mirror image over the sketch plane — like extruding one way then Mirror → Add
            body = one_side(direction, end, depth, p.get("up_to"), p.get("offset", 0.0), a1)
            return K.combine([body, K.mirror(body, mirror_plane())])
        side1 = ("blind" if symmetric else end, depth / 2 if symmetric else depth, p.get("up_to"),
                 p.get("offset", 0.0), a1)
        if symmetric:
            side2 = ("blind", depth / 2, None, 0.0, a1)
        elif through_both:
            side2 = ("through", 0.0, None, 0.0, a1)
        elif second:
            side2 = (end2, float(p.get("depth2", 0)), p.get("up_to2"), p.get("offset2", 0.0), a2)
        else:
            side2 = None
        plain = not (a1 or a2) and end not in UP_TO and (side2 is None or side2[0] in ("blind", "through"))
        if plain:
            # one prism from end to end (as files made before per-end options always did)
            if end == "through":
                dd = through_len()
                return K.extrude(profs, direction, dd, depth2=dd if through_both else 0.0)
            depth2 = float(p.get("depth2", 0)) if second else 0.0
            return K.extrude(profs, direction, depth, symmetric=symmetric, depth2=depth2)
        body = one_side(direction, *side1)
        start = crossing_start(direction)
        if start is not None:
            # the second end's vertex is on the first end's side (Onshape's trick: both ends up to the same
            # vertex, the second stopping short by X → a slab X thick ending at the vertex): the extrusion
            # starts out there instead of at the sketch
            if start > 0:
                body = K.cut(body, K.extrude(profs, direction, start))
            elif start < 0:
                body = K.combine([body, K.extrude(profs, -direction, -start)])
            if body is None or (all(f.ShapeType() == 4 for f in profs) and K.volume(body) <= 1e-9):
                raise K.KernelError("The second end is past the first end — make it stop short by less")
            return body
        if side2 is not None:
            body = K.combine([body, one_side(-direction, *side2)])
        return body

    def crossing_start(direction):
        """Where the extrusion starts (along `direction`, from the sketch) when the second end is up to a vertex
        that lies on the first end's side; None when it doesn't (the usual case)."""
        if not (second and end2 == "up_to_vertex" and (p.get("up_to2") or {}).get("kind") == "vertex"):
            return None
        q = resolve_point(state, p["up_to2"]["ref"])
        dn = np.asarray(direction, float) / np.linalg.norm(direction)
        reach = float((q - _profile_origin(state, p, profs)) @ dn)      # how far along the first end's way
        if reach <= 1e-9:
            return None
        return reach - float(p.get("offset2", 0.0) or 0.0)    # stopping short: back towards the sketch

    d = -n if flip else n
    body = make(d)
    if cutting and end == "blind" and not second and state.parts:
        # like Onshape, a cut that misses everything is tried in the other direction
        targets = [state.parts[i] for i in (p.get("scope") or state.parts) if i in state.parts]
        if not any(K.intersects(t.shape, body) for t in targets):
            alt = make(-d)
            if any(K.intersects(t.shape, alt) for t in targets):
                body = alt
    BO.apply(state, body, dict(p, op=op), f["id"])


def _profile_origin(state, p, profs):
    """A point on the plane the profiles lie in (where an extrusion starts)."""
    if p.get("regions"):
        return np.asarray(state.sketches[p["regions"][0]["sketch"]].plane.origin, float)
    lo, hi = K.bbox(K.compound(profs))
    return (lo + hi) / 2


UP_TO = ("up_to_face", "up_to_vertex", "up_to_part", "up_to_next")
UP_TO_LABELS = [("up_to_next", "Up to next"), ("up_to_face", "Up to face"), ("up_to_part", "Up to part"),
                ("up_to_vertex", "Up to vertex")]


def _model_size(state, extra=()):
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for shp in [pt.shape for pt in state.parts.values()] + list(extra):
        a, b = K.bbox(shp)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    return float(np.linalg.norm(hi - lo)) if np.all(hi >= lo) else 100.0


def _sweep_up_to(state, p, face, kind, how):
    """One profile swept (extrude: along direction `how`; revolve: about axis `how` = (point, dir)) until it
    meets what p["up_to"] names: the next part in the way, a face (or plane), a part, or a vertex."""
    end = p.get("end")
    target = p.get("up_to") or {}
    offset = float(p.get("offset", 0.0) or 0.0)
    sheet = face.ShapeType() != 4             # a curve (wire) making a surface, not a face making a solid
    start = _first_point(face) if sheet else K.interior_point(face)     # a point on the profile
    size = _model_size(state, [face]) * 2 + 10

    if kind == "extrude":
        d = np.asarray(how, float) / np.linalg.norm(how)

        def sweep_long():
            return K.extrude([face], d, size)
        seed = start + d * max(size * 1e-5, 1e-4)
    else:
        ap, ad = np.asarray(how[0], float), np.asarray(how[1], float)

        def sweep_long():
            return K.revolve([face], ap, ad, 359.0)
        step = 0.05 if np.linalg.norm(np.cross(start - ap, ad)) > 1e-9 else 0.0
        seed = K.rotate_point(start, ap, ad, step)

    if end == "up_to_vertex":
        if target.get("kind") != "vertex":
            raise K.KernelError("Pick the vertex to go up to")
        q = resolve_point(state, target["ref"])
        if kind == "extrude":
            depth = float((q - start) @ d) - offset
            if depth <= 1e-9:
                raise K.KernelError("That vertex is behind the profile — click the direction arrow to flip it")
            return K.extrude([face], d, depth)
        ang = K.angle_about(start, q, ap, ad)
        if ang <= 1e-6:
            raise K.KernelError("That vertex is behind the profile — click the direction arrow to flip it")
        return K.revolve([face], ap, ad, ang)

    if end == "up_to_face":
        if target.get("kind") == "face":
            _pid, tf = resolve(state, target["ref"], "face")
            tool = K.termination_face(tf, offset, start)
        elif target.get("kind") == "plane":
            tool = K.termination_plane(resolve_plane(state, target["ref"]), offset, start)
        else:
            raise K.KernelError("Pick the face or plane to go up to")
    elif end == "up_to_part":
        pid = target.get("id") if target.get("kind") == "part" else None
        if pid not in state.parts:
            raise K.KernelError("Pick the part to go up to")
        tool = state.parts[pid].shape
    else:                                                   # up to next: whatever part is in the way first
        if not state.parts:
            raise K.KernelError("There are no parts for it to reach")
        tool = K.compound([pt.shape for pt in state.parts.values()])
    if sheet:
        return K.keep_sheet_from(sweep_long(), tool, face)
    return K.keep_piece_from(sweep_long(), tool, seed)


def up_to_side(state, ftype, p, which=1) -> int:
    """Which way an Extrude / Revolve "Up to …" end has to go to reach its target: +1 the way it points without
    Opposite direction, -1 the other way, 0 if that can't be told (or either way works). which=2: the extrude's
    second end (its +1 is still the first end's way, as the second end always runs opposite the first). Used by
    the dialog to turn the arrow towards the face / vertex / part picked, as Onshape does."""
    from . import body_options as BO
    end = p.get("end") if which == 1 else p.get("end2")
    target = (p.get("up_to") if which == 1 else p.get("up_to2")) or {}
    if end not in UP_TO:
        return 0
    try:
        profs = BO.profiles(state, p, _profiles(state, p))
        if not profs:
            return 0
        solid_profs = [f for f in profs if f.ShapeType() == 4]

        def target_point():
            if end == "up_to_vertex" and target.get("kind") == "vertex":
                return resolve_point(state, target["ref"])
            if end == "up_to_face" and target.get("kind") == "face":
                return np.asarray(K.face_info(resolve(state, target["ref"], "face")[1])["c"], float)
            if end == "up_to_part" and target.get("kind") == "part" and target.get("id") in state.parts:
                lo, hi = K.bbox(state.parts[target["id"]].shape)
                return (np.asarray(lo, float) + np.asarray(hi, float)) / 2
            return None
        if ftype == "revolve":
            if end == "up_to_next" or not solid_profs:
                return 0
            a, d = resolve_axis(state, p.get("axis", {}))
            q = target_point()
            if q is None and end == "up_to_face" and target.get("kind") == "plane":
                return 0
            if q is None:
                return 0
            ang = K.angle_about(K.interior_point(solid_profs[0]), q, a, d)
            if ang < 1e-6 or abs(ang - 180) < 1e-6:
                return 0
            return 1 if ang < 180 else -1
        n = np.asarray(BO.profile_normal(state, p, profs), float)
        n = n / np.linalg.norm(n)
        cutting = p.get("op", "add") in ("remove", "intersect")
        if cutting and not p.get("regions"):
            n = -n                                         # (as run_extrude: cuts from a face go into the part)
        o = _profile_origin(state, p, profs)
        if end == "up_to_face" and target.get("kind") == "plane":
            pl = resolve_plane(state, target["ref"])
            pn = np.asarray(pl.normal, float)
            den = float(n @ pn)
            if abs(den) < 1e-6:
                return 0
            t = float((np.asarray(pl.origin, float) - o) @ pn) / den
        elif end in ("up_to_face", "up_to_vertex"):
            q = target_point()
            if q is None:
                return 0
            t = float((q - o) @ n)
        else:                                              # up to next / up to a part: which way meets it
            if not solid_profs:
                return 0
            pid = target.get("id") if end == "up_to_part" and target.get("kind") == "part" else None
            if end == "up_to_part" and pid not in state.parts:
                return 0
            parts = [state.parts[pid]] if pid else list(state.parts.values())
            size = _model_size(state, solid_profs) * 2 + 10
            start = K.interior_point(solid_profs[0])
            eps = max(size * 1e-6, 1e-5)

            def meets(direction):
                sweep = K.extrude(solid_profs, direction, size)
                for pt in parts:
                    # building onto a part from its own face: that part isn't "the next one" that way
                    if not cutting and K._point_in(pt.shape, start + direction * eps):
                        continue
                    if K.intersects(pt.shape, sweep):
                        return True
                return False
            fwd, back = meets(n), meets(-n)
            return 0 if fwd == back else (1 if fwd else -1)
        if abs(t) < 1e-7:
            return 0
        return 1 if t > 0 else -1
    except (K.KernelError, KeyError, IndexError, ValueError, TypeError):
        return 0


def _first_point(wire):
    from . import body_options as BO
    return BO._ends(wire)[0]


def run_revolve(state, f):
    from . import body_options as BO
    p = f["params"]
    profs = BO.profiles(state, p, _profiles(state, p))
    a, d = resolve_axis(state, p.get("axis", {}))
    if p.get("flip"):
        d = -d
    end = p.get("end", "full")
    if end in UP_TO:
        body = K.combine([_sweep_up_to(state, p, prof, "revolve", (a, d)) for prof in profs])
        if p.get("mirror_end"):
            # symmetric: the same revolve mirrored over the sketch plane (the plane of the profile and axis)
            nn = BO.profile_normal(state, p, profs)
            nn = np.asarray(nn, float) / np.linalg.norm(nn)
            x = np.cross(nn, [0, 0, 1] if abs(nn[2]) < 0.9 else [1, 0, 0])
            pl = K.Plane(_profile_origin(state, p, profs), x / np.linalg.norm(x), nn)
            body = K.combine([body, K.mirror(body, pl)])
        BO.apply(state, body, p, f["id"])
        return
    angle = 360.0 if end == "full" else float(p.get("angle", 180))
    back = 0.0
    if end == "two":                                   # two directions: angle one way, angle2 the other
        back = float(p.get("angle2", 90))
        if angle + back > 360 + 1e-9:
            raise K.KernelError("The two angles add up to more than 360°")
        angle += back
    body = K.revolve(profs, a, d, angle, symmetric=(end == "symmetric"), back=back)
    BO.apply(state, body, p, f["id"])


def _group_by_part(state, refs, kind):
    groups = {}
    for r in refs:
        pid, sub = resolve(state, r, kind)
        groups.setdefault(pid, []).append(sub)
    return groups


def run_fillet(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("edges", []), "edge")
    if not groups:
        raise K.KernelError("Select edges to fillet")
    for pid, es in groups.items():
        part = state.parts[pid]
        r2 = float(p.get("radius2", 1)) if p.get("variable") else None
        state.parts[pid] = Part(pid, part.name, K.fillet(part.shape, es, float(p.get("radius", 1)), r2),
                                part.color)


def run_chamfer(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("edges", []), "edge")
    if not groups:
        raise K.KernelError("Select edges to chamfer")
    for pid, es in groups.items():
        part = state.parts[pid]
        mode = p.get("type", "equal")
        shape = K.chamfer(part.shape, es, float(p.get("distance", 1)), mode, float(p.get("distance2", 1)),
                          float(p.get("angle", 45)), bool(p.get("flip")))
        state.parts[pid] = Part(pid, part.name, shape, part.color)


def run_shell(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to remove")
    for pid, fs in groups.items():
        part = state.parts[pid]
        state.parts[pid] = Part(pid, part.name,
                                K.shell(part.shape, fs, float(p.get("thickness", 1)), p.get("outward", False)),
                                part.color)


def run_delete_face(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to delete")
    for pid, fs in groups.items():
        part = state.parts[pid]
        state.parts[pid] = Part(pid, part.name, K.delete_faces(part.shape, fs, p.get("mode", "heal")), part.color)


def run_move_face(state, f):
    p = f["params"]
    refs = p.get("faces", [])
    if not refs:
        raise K.KernelError("Select flat faces to move")
    dist = float(p.get("distance", 1)) * (-1 if p.get("flip") else 1)
    for r in refs:                     # one at a time: each move changes the part the next face is found in
        pid, face = resolve(state, r, "face")
        part = state.parts[pid]
        state.parts[pid] = Part(pid, part.name, K.move_face(part.shape, face, dist), part.color)


def run_offset_surface(state, f):
    p = f["params"]
    refs = p.get("faces", [])
    if not refs:
        raise K.KernelError("Select faces to offset")
    dist = float(p.get("distance", 1)) * (-1 if p.get("flip") else 1)
    for r in refs:                     # each face becomes its own new surface body
        _pid, face = resolve(state, r, "face")
        state.new_part(K.offset_surface(face, dist), f["id"])


def run_plane(state, f):
    state.planes[f["id"]] = build_plane(state, f["params"])


def _selected_parts(state, p):
    ids = [i for i in p.get("parts", []) if i in state.parts]
    if not ids:
        raise K.KernelError("Select parts")
    return ids


# --------------------------------------------------------------------------- feature patterns
def _feature_changes(state, fids):
    """What each feature did to the model, part by part: [(part id, material added, material removed)].
    Used to repeat features (a hole, a boss, a pocket) in a pattern or mirror."""
    out = []
    for fid in fids:
        h = state.history.get(fid)
        if h is None:
            raise K.KernelError("Pick features that come before this one (and aren't suppressed)")
        before, after = h
        for pid, part in after.parts.items():
            old = before.parts.get(pid)
            if old is None:
                out.append((pid, part.shape, None))
                continue
            if old.shape is part.shape:
                continue
            add, rem = K.cut(part.shape, old.shape), K.cut(old.shape, part.shape)
            tiny = 1e-9 * max(K.volume(old.shape), 1.0)
            out.append((pid, add if K.volume(add) > tiny else None, rem if K.volume(rem) > tiny else None))
    if not out:
        raise K.KernelError("Those features don't change any part, so there's nothing to repeat")
    return out


def _repeat_features(state, f, transforms):
    """Apply what the chosen features did again at each transform (a function shape -> moved shape)."""
    changes = _feature_changes(state, f["params"].get("features", []))
    adds, rems = {}, {}
    for T in transforms:
        for pid, add, rem in changes:
            if add is not None:
                adds.setdefault(pid, []).append(T(add))
            if rem is not None:
                rems.setdefault(pid, []).append(T(rem))
    for pid in list(adds) + [q for q in rems if q not in adds]:
        part = state.parts.get(pid)
        if part is None:                      # the feature made a part that has since gone: new parts
            for s_ in adds.get(pid, []):
                for sol in K.solids(s_):
                    state.new_part(sol, f["id"])
            continue
        shape = part.shape
        if adds.get(pid):
            shape = K.fuse(shape, K.fuse_all(adds[pid]))
        if rems.get(pid):
            shape = K.cut(shape, K.fuse_all(rems[pid]))
        set_part_shape(state, part, shape)


def _features_mode(p):
    return p.get("type") == "features"


def face_feature_bodies(state, refs):
    """For 'pattern / mirror faces': the material the picked faces make — a boss, rib or pocket — found by healing
    those faces away (as Delete face does) and comparing. {part id: (added material, removed material)}."""
    out = {}
    for pid, fs in _group_by_part(state, refs, "face").items():
        part = state.parts[pid]
        healed = K.delete_faces(part.shape, fs, "heal")
        added = K.cut(part.shape, healed)
        removed = K.cut(healed, part.shape)
        add_s, rem_s = K.solids(added), K.solids(removed)
        if not add_s and not rem_s:
            raise K.KernelError("Those faces don't make a boss or pocket that can be repeated — pick all the "
                                "faces of one (e.g. a boss's side and top)")
        out[pid] = (K.compound(add_s) if add_s else None, K.compound(rem_s) if rem_s else None)
    if not out:
        raise K.KernelError("Select the faces to repeat")
    return out


def repeat_faces(state, p, moves):
    """Copy the material made by the picked faces with each of `moves` (shape -> moved shape), into its part."""
    for pid, (added, removed) in face_feature_bodies(state, p.get("faces", [])).items():
        part = state.parts[pid]
        shape = part.shape
        for mv in moves:
            if added is not None:
                shape = K.fuse(shape, mv(added))
        for mv in moves:
            if removed is not None:
                shape = K.cut(shape, mv(removed))
        set_part_shape(state, part, shape)


def _faces_mode(p):
    return p.get("type") == "faces"


def run_mirror(state, f):
    p = f["params"]
    if _features_mode(p):
        plane = resolve_plane(state, p["plane"])
        _repeat_features(state, f, [lambda s_: K.mirror(s_, plane)])
        return
    if _faces_mode(p):
        plane = resolve_plane(state, p["plane"])
        repeat_faces(state, p, [lambda s_: K.mirror(s_, plane)])
        return
    ids = _selected_parts(state, p)
    plane = resolve_plane(state, p["plane"])
    bodies = [K.mirror(state.parts[i].shape, plane) for i in ids]
    body = K.compound(bodies)
    op = p.get("op", "add")
    apply_body(state, body, op, f["id"], p.get("scope") or (ids if op == "add" else None))


def run_linear_pattern(state, f):
    p = f["params"]
    ids = [] if _features_mode(p) or _faces_mode(p) else _selected_parts(state, p)
    dref = p.get("direction", {"kind": "std", "name": "X"})
    _, d = resolve_axis(state, dref)
    if p.get("flip"):
        d = -d
    n = int(p.get("count", 3))
    sp = float(p.get("spacing", 20))
    # a second direction makes a grid: every step one way combined with every step the other way
    n2, d2, sp2 = 1, np.zeros(3), 0.0
    if p.get("second"):
        _, d2 = resolve_axis(state, p.get("direction2") or {"kind": "std", "name": "Y"})
        if p.get("flip2"):
            d2 = -d2
        n2, sp2 = int(p.get("count2", 2)), float(p.get("spacing2", 20))
        if n2 < 1:
            raise K.KernelError("Second count must be at least 1")
    offsets = [d * sp * i + d2 * sp2 * j for j in range(n2) for i in range(n) if (i, j) != (0, 0)]
    if _features_mode(p) or _faces_mode(p):
        if not offsets:
            raise K.KernelError("Count must be at least 2")
        moves = [lambda s_, v=v: K.translate(s_, v) for v in offsets]
        if _faces_mode(p):
            repeat_faces(state, p, moves)
        else:
            _repeat_features(state, f, moves)
        return
    copies = [K.translate(state.parts[i].shape, v) for i in ids for v in offsets]
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"], p.get("scope"))


def run_circular_pattern(state, f):
    p = f["params"]
    ids = [] if _features_mode(p) or _faces_mode(p) else _selected_parts(state, p)
    a, d = resolve_axis(state, p.get("axis", {"kind": "std", "name": "Z"}))
    n = int(p.get("count", 4))
    if n < 2:                                  # (before the step: a count of 0 would divide by zero)
        raise K.KernelError("Count must be at least 2")
    total = float(p.get("angle", 360))
    step = total / n if abs(total - 360) < 1e-9 else total / max(n - 1, 1)
    if _features_mode(p):
        if n < 2:
            raise K.KernelError("Count must be at least 2")
        _repeat_features(state, f, [lambda s_, k=k: K.rotate(s_, a, d, step * k) for k in range(1, n)])
        return
    if _faces_mode(p):
        if n < 2:
            raise K.KernelError("Count must be at least 2")
        repeat_faces(state, p, [lambda s_, k=k: K.rotate(s_, a, d, step * k) for k in range(1, n)])
        return
    copies = []
    for i in ids:
        for k in range(1, n):
            copies.append(K.rotate(state.parts[i].shape, a, d, step * k))
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"], p.get("scope"))


def _pattern_moves(state, f, ids, moves, empty_msg="Count must be at least 2"):
    """Copy the parts / features / faces of a pattern with each of `moves` (shape -> moved shape)."""
    p = f["params"]
    if not moves:
        raise K.KernelError(empty_msg)
    if _faces_mode(p):
        repeat_faces(state, p, moves)
        return
    if _features_mode(p):
        _repeat_features(state, f, moves)
        return
    copies = [mv(state.parts[i].shape) for i in ids for mv in moves]
    if not copies:
        raise K.KernelError("Select the parts to pattern")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"], p.get("scope"))


def _pattern_seed_box(state, f, ids):
    """The middle of what a pattern copies (its parts, the material its features or faces make)."""
    p = f["params"]
    if _faces_mode(p):
        shapes = [s_ for a, r in face_feature_bodies(state, p.get("faces", [])).values() for s_ in (a, r) if s_]
    elif _features_mode(p):
        shapes = [s_ for _pid, a, r in _feature_changes(state, p.get("features", [])) for s_ in (a, r) if s_]
    else:
        shapes = [state.parts[i].shape for i in ids]
    if not shapes:
        raise K.KernelError("Select what to pattern")
    lo, hi = K.bbox(K.compound(shapes))
    return (np.asarray(lo, float) + np.asarray(hi, float)) / 2


def run_curve_pattern(state, f):
    """Copies spaced along a path (sketch curves or edges), turning with it as Onshape's Curve pattern does (or
    keeping their orientation). The first copy is the seed where it is; the others move as the path's start
    would move to each place along it."""
    p = f["params"]
    ids = [] if _features_mode(p) or _faces_mode(p) else _selected_parts(state, p)
    if not p.get("path"):
        raise K.KernelError("Pick the path to pattern along")
    wire = _path_wire(state, p["path"])
    n = int(p.get("count", 4))
    if n < 2:
        raise K.KernelError("Count must be at least 2")
    _f, length, closed = K.wire_frames(wire, [0.0])
    if p.get("spacing_mode", "fit") == "distance":
        step = float(p.get("spacing", 20))
        if step <= 0:
            raise K.KernelError("Spacing must be more than 0")
        if step * (n - 1) > length + 1e-6:
            raise K.KernelError(f"{n} copies {step:g} mm apart need a path {step * (n - 1):g} mm long; "
                                f"this one is {length:.4g} mm")
    else:
        step = length / n if closed else length / (n - 1)       # spread along it all (round a closed one)
    frames, _l, _c = K.wire_frames(wire, [step * k for k in range(n)], bool(p.get("flip")))
    (p0, t0), rest = frames[0], frames[1:]
    normal = None                                       # a flat path turns everything about its plane's normal
    samples, _l, _c = K.wire_frames(wire, np.linspace(0, length, 24))
    sp = np.array([q for q, _t in samples])
    sp = sp - sp.mean(axis=0)
    _u, sv, vt = np.linalg.svd(sp)
    if sv[0] > 1e-9 and sv[-1] < 1e-6 * sv[0]:
        normal = vt[-1]
    moves = []
    for q, t in rest:
        ang, axis = 0.0, None
        if not p.get("keep_orientation"):
            if normal is not None:
                ang = math.degrees(math.atan2(float(np.cross(t0, t) @ normal), float(t0 @ t)))
                axis = normal
            else:
                cr = np.cross(t0, t)
                if np.linalg.norm(cr) > 1e-9:
                    axis = cr / np.linalg.norm(cr)
                    ang = math.degrees(math.atan2(np.linalg.norm(cr), float(t0 @ t)))
        if axis is None or abs(ang) < 1e-9:
            moves.append(lambda s_, v=q - p0: K.translate(s_, v))
        else:
            moves.append(lambda s_, v=q - p0, a=ang, ax=axis: K.transform(s_, v, p0, ax, a))
    _pattern_moves(state, f, ids, moves)


def run_point_pattern(state, f):
    """A copy at each picked point (vertices or sketch points): moved by how far each point is from the reference
    point — or, with none picked, from the middle of what's copied."""
    p = f["params"]
    ids = [] if _features_mode(p) or _faces_mode(p) else _selected_parts(state, p)
    locs = p.get("locations") or []
    if not locs:
        raise K.KernelError("Pick the points to copy to")
    ref = p.get("reference")
    origin = resolve_point(state, ref) if ref else _pattern_seed_box(state, f, ids)
    moves = []
    for r in locs:
        v = resolve_point(state, r) - origin
        if np.linalg.norm(v) > 1e-9:                    # the seed's own place: it's already there
            moves.append(lambda s_, v=v: K.translate(s_, v))
    _pattern_moves(state, f, ids, moves, "Pick points away from the reference point")


def run_boolean(state, f):
    p = f["params"]
    op = p.get("op", "union")
    tools = [i for i in p.get("tools", []) if i in state.parts]
    targets = [i for i in p.get("targets", []) if i in state.parts]
    if op == "union":
        allp = tools + targets
        if len(allp) < 2:
            raise K.KernelError("Select at least two parts")
        keep = state.parts[allp[0]]
        shape = keep.shape
        for i in allp[1:]:
            shape = K.fuse(shape, state.parts.pop(i).shape)
        set_part_shape(state, keep, shape)
        return
    if not tools or not targets:
        raise K.KernelError("Select tool and target parts")
    body = K.compound([state.parts[i].shape for i in tools])
    if not p.get("keep_tools"):
        for i in tools:
            state.parts.pop(i)
    apply_body(state, body, "remove" if op == "subtract" else "intersect", f["id"], targets)


# Auto colour: distinct colours that read well under the 3D view's lighting, on light and dark backgrounds.
# Oranges and yellows are left out: those are the selection and hover highlight colours.
AUTO_COLORS = ["#4e79a7", "#59a14f", "#e15759", "#76b7b2", "#b07aa1", "#6b5b95", "#2a9d8f", "#ff9da7",
               "#c06c84", "#86bcb6", "#d37295", "#8cd17d", "#a0cbe8", "#5c7cfa", "#bab0ac", "#499894"]


def auto_colors(n, shift=0):
    """n clearly different colours as (r, g, b) in 0..1. Up to 16 come from the palette above (starting `shift`
    places along it, so asking again gives a fresh set); beyond that, evenly spread extra hues are added."""
    import colorsys
    k = len(AUTO_COLORS)
    out = []
    for i in range(n):
        if i < k:
            h = AUTO_COLORS[(i + shift) % k].lstrip("#")
            out.append(tuple(int(h[j:j + 2], 16) / 255 for j in (0, 2, 4)))
        else:
            hue = ((i - k) * 0.618033988749895 + 0.11) % 1.0      # golden-ratio steps
            if 0.04 < hue < 0.18:                                  # skip the highlight oranges / yellows
                hue += 0.16
            out.append(colorsys.hsv_to_rgb(hue, 0.55, 0.82))
    return out


# --------------------------------------------------------------------------- derived parts
# Set by the main window: callable(params) -> [(name, shape, colour, face colours)] — the parts of another Part
# Studio (in a configuration). Raises KernelError if it can't be reached.
LINKER = None
# Also set by the main window: callable(params, for_save=False) -> a string that identifies the saved source file
# as it is now (for_save: as the parts in this document came from it), or None when that can't be trusted (the
# source is open in a tab, missing, ...). Lets a document use the copy of the parts saved in it, instead of
# loading and rebuilding the whole source file, when the source hasn't changed since.
LINK_SOURCE_STAMP = None


def saved_copy_is_current(f) -> bool:
    fb = f.get("_fallback")
    if not fb or LINK_SOURCE_STAMP is None:
        return False
    try:
        now = LINK_SOURCE_STAMP(f["params"])
    except Exception:
        return False
    return now is not None and all(it.get("src") == now for it in fb)


def link_stamp(f):
    """Something that changes whenever a derived feature's source parts change (part of the rebuild cache key)."""
    if saved_copy_is_current(f):
        return ["saved", f["_fallback"][0]["src"]]
    if LINKER is None:
        return "nolink"
    try:
        return [id(sh) for _n, sh, _c, _fc in LINKER(f["params"])]
    except Exception as e:
        return f"missing:{e}"


def run_derived(state, f):
    """Parts from another Part Studio, kept linked: edit them there and they update here. If the source can't be
    found, the copy saved with this document is used."""
    p = f["params"]
    items = None
    err = None
    if saved_copy_is_current(f):
        pass                      # source unchanged since this file was saved: use the saved copy (below)
    elif LINKER is not None:
        try:
            items = LINKER(p)
        except K.KernelError as e:
            err = str(e)
    if items is None:
        fb = f.get("_fallback")
        if not fb:
            raise K.KernelError(err or "The Part Studio to derive from isn't open")
        items = [(it["name"], K.shape_from_brep(base64.b64decode(it["brep"])), tuple(it["color"]), None)
                 for it in fb]
    if not items:
        raise K.KernelError("There are no parts to derive (pick parts from the other Part Studio)")
    for name, shape, color, fcols in items:
        part = state.new_part(shape, f["id"], tuple(color) if color else DEFAULT_COLOR)
        state.parts[part.id] = Part(part.id, name or part.name, shape, part.color, fcols or None)


def is_brep_name(name) -> bool:
    return str(name).lower().endswith((".brep", ".brp"))


def run_import(state, f):
    p = f["params"]
    if p.get("brep"):              # saved as exact geometry: no STEP to translate
        for shape, name, color, fcols in K.unpack_import(p["brep"], p.get("meta") or []):
            part = state.new_part(shape, f["id"], tuple(color) if color else DEFAULT_COLOR)
            state.parts[part.id] = Part(part.id, name or part.name, part.shape, part.color, fcols or None)
        return
    data = base64.b64decode(p["data"])
    brep = is_brep_name(p.get("filename", ""))
    with tempfile.NamedTemporaryFile(suffix=".brep" if brep else ".step", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        if brep:
            items = [(s, None, None, {}) for s in K.import_brep(path)]     # BREP holds no names or colours
        else:
            try:
                items = K.import_step_with_colors(path)
            except Exception:
                items = [(s, None, None, {}) for s in K.import_step(path)]   # plain geometry as a fallback
        for shape, name, color, fcols in items:
            part = state.new_part(shape, f["id"], tuple(color) if color else DEFAULT_COLOR)
            state.parts[part.id] = Part(part.id, name or part.name, part.shape, part.color, fcols or None)
    finally:
        os.unlink(path)


def run_hole(state, f):
    """Holes at sketch points: simple, counterbored or countersunk; custom size or ISO metric."""
    p = f["params"]
    sk = state.sketches.get(p.get("sketch"))
    if sk is None:
        raise K.KernelError("Select a sketch with points or circles for the hole centres (click it, or pick it above)")
    centres, sizes = hole_centres(sk.sketch)
    if not centres:
        raise K.KernelError("Add sketch points or circles to place holes")
    if not state.parts:
        raise K.KernelError("There's no part to put holes in")
    dia, cb, cs, _label = hole_dims(p)
    if dia <= 0:
        raise K.KernelError("Hole diameter must be positive")
    end = p.get("end", "through")
    through = end == "through"
    depth = float(p.get("depth", 10))
    n = np.asarray(sk.plane.normal, float)
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for part in state.parts.values():
        a_, b_ = K.bbox(part.shape)
        lo, hi = np.minimum(lo, a_), np.maximum(hi, b_)
    big = float(np.linalg.norm(hi - lo)) * 2 + 10
    shapes = [pt.shape for pt in state.parts.values()]
    back = max(dia * 0.02, 1e-3)      # start a hair above the surface for clean cuts

    def material_on(direction):
        probe = K.cylinder(sk.plane.to_world(*centres[0]), direction, dia / 2, min(big, max(depth, 1.0)))
        return sum(K.volume(K.common(sh, probe)) for sh in shapes if K.intersects(sh, probe))

    d = -n if material_on(-n) >= material_on(n) else n
    depths = [depth] * len(centres)
    if end in ("up_to_next", "up_to_face", "up_to_vertex"):
        # the shared up-to ends: each hole goes as deep as its own centre needs
        depths = [_hole_depth_up_to(state, p, np.asarray(sk.plane.to_world(u, v), float), d, shapes)
                  for (u, v) in centres]
        depth = depths[0]
    modelled = p.get("standard") == "tapped" and p.get("model_thread") and p.get("size") in K.ISO_PITCH
    if modelled:
        _model_hole_threads(state, f, p, sk, centres, d, through, depth, back, big)
    tools = []
    from_circles = p.get("standard") == "sketch"
    if from_circles and not any(sizes):
        raise K.KernelError("“Size of the sketch circles” needs circles in the sketch")
    for (u, v), circle_d, depth in zip(centres, sizes, depths):
        if from_circles:
            if not circle_d:
                continue                              # a plain point: no size to take
            dia = circle_d
        c = np.asarray(sk.plane.to_world(u, v), float)
        start = c - d * back
        if modelled:
            pass                                  # the threaded bore itself was cut above
        elif through:
            tools.append(K.cylinder(c - d * big, d, dia / 2, big * 2))
        else:
            tools.append(K.cylinder(start, d, dia / 2, depth + back))
        if not through and p.get("tip", True):
            # 118° drill point at the bottom of a blind hole
            tip_h = (dia / 2) / math.tan(math.radians(59))
            tools.append(K.cone(c + d * depth, d, dia / 2, 0.0, tip_h))
        if cb:
            cdia, cdep = cb
            if cdia <= dia:
                raise K.KernelError("Counterbore must be wider than the hole")
            tools.append(K.cylinder(start, d, cdia / 2, cdep + back))
        if cs:
            if cs <= dia:
                raise K.KernelError("Countersink must be wider than the hole")
            h = (cs - dia) / 2                       # 90° countersink
            tools.append(K.cone(c - d * back, d, cs / 2 + back, dia / 2, h + back))
    if tools:
        apply_body(state, K.fuse_all(tools), "remove", f["id"])


def _hole_depth_up_to(state, p, c, d, shapes):
    """How deep a hole at c (going along d) goes for an up-to end: to where it next comes out of the material,
    or to a face / plane / vertex — less any 'stop short by' offset."""
    end = p.get("end")
    target = p.get("up_to") or {}
    offset = float(p.get("offset", 0.0) or 0.0)
    if end == "up_to_next":
        hits = sorted({round(t, 9) for sh in shapes for t in K.ray_hits(sh, c, d) if t > 1e-6})
        for t in hits:
            if not any(K.material_at(sh, c + d * (t + 1e-4)) for sh in shapes):
                return t - offset
        raise K.KernelError("The hole doesn't come out of the part — use Through all or Blind")
    if end == "up_to_vertex":
        if target.get("kind") != "vertex":
            raise K.KernelError("Pick the vertex the hole goes up to")
        t = float((resolve_point(state, target["ref"]) - c) @ d)
    else:
        if target.get("kind") == "face":
            _pid, face = resolve(state, target["ref"], "face")
            surf = K.termination_face(face, 0.0, c)
        elif target.get("kind") == "plane":
            surf = K.termination_plane(resolve_plane(state, target["ref"]), 0.0, c)
        else:
            raise K.KernelError("Pick the face or plane the hole goes up to")
        hits = [t for t in K.ray_hits(surf, c, d) if t > 1e-6]
        if not hits:
            raise K.KernelError("The hole never reaches that face — pick another")
        t = hits[0]
    t -= offset
    if t <= 1e-6:
        raise K.KernelError("That is behind the start of the hole")
    return t


def _material_range(shapes, origin, axis, radius, big):
    """Where along `axis` (distances from `origin`) the parts have material within `radius`."""
    probe = K.cylinder(np.asarray(origin) - np.asarray(axis) * big, axis, radius, big * 2)
    zs = []
    for sh in shapes:
        if not K.intersects(sh, probe):
            continue
        com = K.common(sh, probe)
        pts = [np.asarray(q, float) for e in K.edges(com) for q in K.discretize_edge(e, radius * 0.05)]
        zs += [float((q - origin) @ axis) for q in pts]
    return (min(zs), max(zs)) if zs else None


def _model_hole_threads(state, f, p, sk, centres, d, through, depth, back, big):
    """Tapped holes with real ISO thread geometry, cut straight into the parts."""
    size = p.get("size")
    major, pitch = float(size[1:]), K.ISO_PITCH[size]
    clearance = float(p.get("thread_clearance", 0) or 0)
    for (u, v) in centres:
        c = np.asarray(sk.plane.to_world(u, v), float)
        rng = _material_range([pt.shape for pt in state.parts.values()], c, d, major / 2 + pitch, big)
        if rng is None:
            continue
        z0, z1 = (rng[0], rng[1]) if through else (max(rng[0], -back), min(rng[1], depth))
        if z1 - z0 < 1e-6:
            continue
        for part in list(state.parts.values()):
            probe = K.cylinder(c + d * z0, d, major / 2 + pitch, z1 - z0)
            if not K.intersects(part.shape, probe):
                continue
            new = K.cut_internal_thread(part.shape, c, d, major, pitch, z0, z1,
                                        extend=(True, through), clearance=clearance, left=bool(p.get("left_hand")))
            set_part_shape(state, part, new)


def _nearest_size(diameter, internal):
    """ISO size for a round face: holes match the tap drill, shafts match the major diameter."""
    if internal:
        return min(TAP_DRILL, key=lambda k: abs(TAP_DRILL[k] - diameter))
    return min((k for k in K.ISO_PITCH if k in TAP_DRILL or k in ("M14", "M16", "M20", "M24")),
               key=lambda k: abs(float(k[1:]) - diameter))


def thread_spec(state, p, face):
    """What a Thread feature will do to one round face: dict with kind, size, major, pitch, z0, z1 …"""
    p0, ax, r, zmin, zmax, is_hole = K.cylinder_face_frame(face)
    size = p.get("size", "auto")
    if size == "custom":
        major, pitch, label = float(p.get("major", 2 * r)), float(p.get("pitch", 1.0)), "custom"
    else:
        if size == "auto" or size not in K.ISO_PITCH:
            size = _nearest_size(2 * r, is_hole)
        major, pitch, label = float(size[1:]), K.ISO_PITCH[size], size
    if pitch <= 0:
        raise K.KernelError("Pitch must be positive")
    return {"p0": p0, "axis": ax, "r": r, "zmin": zmin, "zmax": zmax, "internal": is_hole,
            "major": major, "pitch": pitch, "size": label}


def run_thread(state, f):
    """Real (modelled) ISO threads on round faces: tap a hole, or thread a shaft."""
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select round faces (holes or shafts) to thread")
    left = bool(p.get("left_hand"))
    clearance = float(p.get("clearance", 0) or 0)
    for pid, fs in groups.items():
        specs = [thread_spec(state, p, face) for face in fs]
        part = state.parts[pid]
        shape = part.shape
        taps = []                  # internal threads, cut together at the end
        for sp in specs:
            P, ax, p0 = sp["pitch"], sp["axis"], sp["p0"]
            z0, z1 = sp["zmin"], sp["zmax"]
            # which ends open into air? (probe just past each end, inside the thread's depth)
            radial = np.cross(ax, [1.0, 0, 0] if abs(ax[0]) < 0.9 else [0, 1.0, 0])
            radial /= np.linalg.norm(radial)
            rp = sp["r"] + (0.3 * P if sp["internal"] else -0.3 * P)
            open_lo = not K.material_at(shape, p0 + ax * (z0 - 0.3 * P) + radial * rp)
            open_hi = not K.material_at(shape, p0 + ax * (z1 + 0.3 * P) + radial * rp)
            if p.get("length_mode") == "length":
                L = float(p.get("length", z1 - z0))
                if L <= 0:
                    raise K.KernelError("Thread length must be positive")
                if open_lo or not open_hi:
                    z1 = min(z1, z0 + L)          # measured from the open (lower) end
                    open_hi = False
                else:
                    z0 = max(z0, z1 - L)
                    open_lo = False
            if sp["internal"]:
                core = sp["r"] + 0.002 if abs(sp["r"] - (sp["major"] / 2 - K.THREAD_H * P)) < 0.01 * P else sp["r"]
                taps.append((p0, ax, sp["major"], P, z0, z1,
                             dict(core_r=core, extend=(open_lo, open_hi), left=left, clearance=clearance)))
            else:
                if abs(sp["major"] / 2 - sp["r"]) > 0.25 * P:
                    raise K.KernelError(f"Shaft Ø{2 * sp['r']:.4g} doesn't suit {sp['size']} — pick the size or use Custom")
                shape = K.external_thread(shape, p0, ax, sp["r"], P, z0, z1, left=left, clearance=clearance)
        if taps:
            shape = K.cut_internal_threads(shape, taps)
        # threads can leave a solid OpenCascade flags as invalid (later fillets would then fail at any size):
        # check and repair each solid on its own
        pieces = [K.heal(sol) for sol in K.solids(shape)] or [shape]
        set_part_shape(state, part, pieces[0] if len(pieces) == 1 else K.compound(pieces))


# --------------------------------------------------------------------------- sweep / loft / draft / split / move

def _path_wire(state, refs):
    from . import sketch_ops
    edges = []
    for r in refs:
        if r.get("kind") == "sketch_chain":
            sr = state.sketches.get(r["sketch"])
            if sr is None or r["entity"] not in sr.sketch.entities:
                raise K.KernelError("Missing sweep path")
            sk = sr.sketch
            e0 = sk.entities[r["entity"]]
            if e0["type"] == "circle":
                curves = [{"type": "circle", "c": sk.points[e0["c"]], "r": e0["r"]}]
            else:
                if sketch_ops._ends(e0) is None:          # a closed curve on its own (ellipse, closed spline)
                    ids = {r["entity"]}
                else:
                    chain, _closed = sketch_ops._chain(sk, r["entity"], splines=True)
                    ids = {k for k, _ in chain}
                    if r.get("only"):                     # just these curves of the chain (picked one by one)
                        ids &= set(r["only"])
                tmp = Sketch.from_dict(sk.to_dict())
                tmp.entities = {k: dict(v, construction=False) for k, v in tmp.entities.items() if k in ids}
                curves = tmp.curves()
            edges += K.make_sketch_edges(sr.plane, curves)
        elif r.get("kind") == "edge":
            _, e = resolve(state, r["edge"], "edge")
            edges.append(e)
        elif r.get("kind") == "region_edge":
            edges.append(region_edge(state, r))
        elif r.get("kind") == "outline":
            # the boundary of a region / flat face (its outer loop here; body_options takes the holes too)
            from OCP.BRepTools import BRepTools
            if len(refs) == 1:
                return BRepTools.OuterWire_s(outline_face(state, r))
            edges += K.edges(BRepTools.OuterWire_s(outline_face(state, r)))
    return K.make_wire(edges)


def region_edge(state, ref):
    """One edge of a sketch region's boundary ({"kind": "region_edge", "region": region ref, "c": its middle}):
    the region's edge nearest that middle point."""
    face = resolve_regions(state, [ref["region"]])[0]
    c = np.asarray(ref["c"], float)
    best, best_d = None, 1e18
    for e in K.edges(face):
        d = float(np.linalg.norm(np.asarray(K.edge_info(e)["c"]) - c))
        if d < best_d:
            best, best_d = e, d
    lo, hi = K.bbox(face)
    if best is None or best_d > 0.05 * float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) + 1e-6:
        raise K.KernelError("An edge of the sketch region is missing")
    return best


def outline_face(state, ref):
    """The region or face an 'outline' curve ref is the boundary of."""
    if ref.get("of") == "region":
        return resolve_regions(state, [ref["ref"]])[0]
    return resolve(state, ref["ref"], "face")[1]


def run_sweep(state, f):
    from . import body_options as BO
    p = f["params"]
    profs = BO.profiles(state, p, _profiles(state, p))
    if not p.get("path"):
        raise K.KernelError("Select the path to sweep along")
    path = _path_wire(state, p["path"])
    control = sweep_control(state, p)
    body = K.sweep_curves(profs, path, control) if BO.creation(p) == "surface" else K.sweep(profs, path, control)
    BO.apply(state, body, p, f["id"])


def sweep_control(state, p):
    """How a swept profile turns along the path (Sweep's 'Profile control')."""
    mode = p.get("control", "none")
    if mode == "lock_direction":
        ref = p.get("lock_dir") or {"kind": "std", "name": p.get("lock_std") or "Z"}
        return {"mode": mode, "dir": resolve_axis(state, ref)[1]}
    if mode == "lock_faces":
        fs = [resolve(state, r, "face")[1] for r in p.get("support", [])]
        if not fs:
            raise K.KernelError("Select the faces the path runs on")
        return {"mode": mode, "support": K.compound(fs)}
    if mode == "guide":
        if not p.get("guide"):
            raise K.KernelError("Select the guide curve")
        return {"mode": mode, "guide": _path_wire(state, p["guide"])}
    return {"mode": mode}


def run_loft(state, f):
    from . import body_options as BO
    from OCP.BRepTools import BRepTools
    p = f["params"]
    profs, normals = [], []                           # faces (regions / faces) or wires (curves), in order
    for item in p.get("profiles", []):
        ref = item["ref"]
        if item["kind"] == "region":
            got = resolve_regions(state, [ref])
        elif item["kind"] == "path":
            got = [_path_wire(state, [ref])]
        else:
            got = [resolve(state, ref, "face")[1]]
        sk = state.sketches.get(ref.get("sketch")) if isinstance(ref, dict) else None
        profs += got
        normals += [np.asarray(sk.plane.normal, float) if sk else None] * len(got)
    c = BO.creation(p)
    path = _path_wire(state, p["path"]) if p.get("path") else None
    ruled = p.get("ruled", False)
    wires = [BRepTools.OuterWire_s(x) if x.ShapeType() == 4 else x for x in profs]
    if c == "solid" and path is None and all(x.ShapeType() == 4 for x in profs):
        body = K.loft(profs, ruled)                       # as before
    elif c == "solid":
        body = K.loft_wires(wires, True, ruled, path)
    elif c == "surface":
        body = K.loft_wires(wires, False, ruled, path)
    else:                                             # thin: loft the wall's outer outlines, less the inner ones
        rings = [BO.thin_outlines(w, p.get("thickness", 2.0), p.get("thin_side", "mid"), nrm,
                                  p.get("thickness2", p.get("thickness", 2.0))) for w, nrm in zip(wires, normals)]
        if len({inner is None for _o, inner in rings}) > 1:
            raise K.KernelError("Thin loft profiles must be all closed or all open")
        body = K.loft_wires([o for o, _i in rings], True, ruled, path)
        if rings[0][1] is not None:
            body = K.cut(body, K.loft_wires([i for _o, i in rings], True, ruled, path))
    BO.apply(state, body, p, f["id"])


def run_draft(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("faces", []), "face")
    if not groups:
        raise K.KernelError("Select faces to draft")
    neutral = resolve_plane(state, p.get("neutral") or {"kind": "std", "name": "Top"})
    pull = -neutral.normal if p.get("flip") else neutral.normal
    for pid, fs in groups.items():
        part = state.parts[pid]
        state.parts[pid] = Part(pid, part.name, K.draft(part.shape, fs, pull, float(p.get("angle", 3)), neutral),
                                part.color)


def split_tools(p):
    """The Split feature's cutting tools: [{"kind": plane|face|path, "ref", "both", "flip"}]. Splits made before
    2.0 had one plane and a "keep" choice; they read as one tool."""
    tools = p.get("tools")
    if tools:
        return tools
    if p.get("plane") is not None:
        keep = p.get("keep", "both")
        return [{"kind": "plane", "ref": p["plane"], "both": keep == "both", "flip": keep == "back"}]
    return []


def split_tool_shape(state, tool, around, size):
    """The surface a Split tool cuts with, made big enough to go right through."""
    kind, ref = tool["kind"], tool["ref"]
    if kind == "plane":
        return K.plane_face(resolve_plane(state, ref), around, size)
    if kind == "face":
        return K.extended_face(resolve(state, ref, "face")[1], size)
    if kind == "path":
        if ref.get("kind") != "sketch_chain":
            raise K.KernelError("To split along a curve, pick a sketch curve (it is pushed out both ways "
                                "into a cutting surface)")
        from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
        wire = _path_wire(state, [ref])
        n = np.asarray(state.sketches[ref["sketch"]].plane.normal, float)
        wire = K.translate(wire, -n * size)
        return BRepPrimAPI_MakePrism(wire, K._vec(n * 2 * size)).Shape()
    raise K.KernelError("Pick planes, faces or sketch curves to split with")


def run_split(state, f):
    """Cut parts with any number of planes, faces (flat or curved — extended to cut right through) or sketch
    curves (pushed out into a surface). Each tool keeps both sides, or only the side its arrow points to."""
    p = f["params"]
    ids = _selected_parts(state, p)
    tools = split_tools(p)
    if not tools:
        raise K.KernelError("Pick planes, faces or sketch curves to split with")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for pid in ids:
        a, b = K.bbox(state.parts[pid].shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    around = (lo + hi) / 2
    shapes = [split_tool_shape(state, t, around, size) for t in tools]
    for pid in ids:
        part = state.parts[pid]
        pieces = K.split_with(part.shape, shapes)
        if len(pieces) < 2 and all(t.get("both", True) for t in tools):
            raise K.KernelError("The splitting surfaces don't cut through the part")
        for t, sh in zip(tools, shapes):
            if t.get("both", True):
                continue
            want = -1.0 if t.get("flip") else 1.0
            pieces = [pc for pc in pieces if K.piece_side(pc, sh) * want > 0]
        if not pieces:
            raise K.KernelError("Nothing is left — every piece is on a side that isn't kept (flip an arrow)")
        set_part_shape(state, part, K.compound(pieces) if len(pieces) > 1 else pieces[0])


def run_mate_connector(state, f):
    from . import mate_connectors as MC
    state.connectors[f["id"]] = MC.build(state, f["params"])


MOVE_TYPES = [("line", "Translate by line"), ("distance", "Translate by distance"), ("xyz", "Translate by XYZ"),
              ("mates", "Transform by mate connectors"), ("rotate", "Rotate"), ("copy", "Copy in place"),
              ("scale", "Scale"), ("points", "Translate from point to point")]


def _axis_segment(state, ref):
    """The two ends of a straight edge or sketch line picked as an axis (None for a world axis or a round edge)."""
    kind = (ref or {}).get("kind")
    if kind == "sketch_line":
        sk = state.sketches.get(ref["sketch"])
        if sk is None or ref["line"] not in sk.sketch.entities:
            raise K.KernelError("Missing line")
        e = sk.sketch.entities[ref["line"]]
        return (np.asarray(sk.plane.to_world(*sk.sketch.points[e["p1"]]), float),
                np.asarray(sk.plane.to_world(*sk.sketch.points[e["p2"]]), float))
    if kind == "edge":
        _, e = resolve(state, ref["edge"], "edge")
        info = K.edge_info(e)
        if "p0" in info:
            return np.asarray(info["p0"], float), np.asarray(info["p1"], float)
    return None


def move_function(state, p):
    """What a Transform (Move) feature does to each part, as a function shape -> moved shape. Its types follow
    Onshape's Transform: by XYZ (and an optional turn, as files before 2.5 had), by a line's length and direction,
    by a distance along a direction, from one point to another, a rotation, one mate connector onto another, or a
    uniform scale."""
    t = p.get("type", "xyz")
    flip = -1.0 if p.get("flip") else 1.0
    if t == "line":
        seg = _axis_segment(state, p.get("axis"))
        if seg is None:
            raise K.KernelError("Pick a straight edge or sketch line to move along (it moves by its length)")
        v = (seg[1] - seg[0]) * flip
        return lambda s_: K.translate(s_, v)
    if t == "distance":
        _a, d = resolve_axis(state, p.get("axis") or {"kind": "std", "name": "X"})
        v = np.asarray(d, float) * float(p.get("distance", 0) or 0) * flip
        return lambda s_: K.translate(s_, v)
    if t == "points":
        a, b = p.get("from_point"), p.get("to_point")
        if not a or not b:
            raise K.KernelError("Pick the point to move from and the point to move to")
        v = resolve_point(state, b) - resolve_point(state, a)
        return lambda s_: K.translate(s_, v)
    if t == "rotate":
        a, d = resolve_axis(state, p.get("axis") or {"kind": "std", "name": "Z"})
        ang = float(p.get("rotate", 0) or 0) * flip
        return lambda s_: K.transform(s_, (0, 0, 0), a, d, ang)
    if t == "mates":
        from . import mate_connectors as MC
        fa, fb = state.connectors.get(p.get("from_mc")), state.connectors.get(p.get("to_mc"))
        if fa is None or fb is None:
            raise K.KernelError("Choose the mate connector to move from and the one to move to (add them with "
                                "Mate connector first)")
        if p.get("flip"):                          # land the other way up: Z against Z
            fb = dict(fb, z=-fb["z"], y=-fb["y"])
        return MC.move_between(fa, fb)
    if t == "copy":
        return lambda s_: s_
    if t == "scale":
        k = float(p.get("scale", 1) or 0)
        if k <= 0:
            raise K.KernelError("The scale must be more than 0")
        pt = p.get("about")
        return lambda s_: K.scale(s_, resolve_point(state, pt) if pt else np.zeros(3), k)
    v = (float(p.get("dx", 0)), float(p.get("dy", 0)), float(p.get("dz", 0)))
    ang = float(p.get("rotate", 0))
    a, d = (np.zeros(3), np.array([0, 0, 1.0]))
    if abs(ang) > 1e-12:
        a, d = resolve_axis(state, p.get("axis") or {"kind": "std", "name": "Z"})
    return lambda s_: K.transform(s_, v, a, d, ang)


def _rigid_matrix(move):
    """The 4x4 rigid motion a shape function makes (found by moving four points through it), or None if it isn't
    one (a scale)."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    got = []
    for q in ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)):
        pts = K.vertex_points(move(BRepBuilderAPI_MakeVertex(gp_Pnt(*q)).Vertex()))
        if len(pts) != 1:
            return None
        got.append(np.asarray(pts[0], float))
    o = got[0]
    R = np.column_stack([got[1] - o, got[2] - o, got[3] - o])
    if not np.allclose(R.T @ R, np.eye(3), atol=1e-9) or np.linalg.det(R) < 0:
        return None
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, o
    return M


def _moved_sheet(model, M):
    """A sheet metal part's model after the part is moved rigidly by M: its panels (and where its flat pattern
    lies) move with it. None for a motion that isn't rigid — the part is then plain solid geometry, and sheet
    metal features on it say so instead of building from where the sheet used to be."""
    if model is None or M is None:
        return None
    m = model.copy()
    for panel in m.panels:
        panel["fold"] = M @ np.asarray(panel["fold"], float)
        panel["flat"] = M @ np.asarray(panel["flat"], float)
    return m


def run_move(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    move = move_function(state, p)
    M = _rigid_matrix(move) if any(pid in state.sheet for pid in ids) else None
    for pid in ids:
        part = state.parts[pid]
        moved = move(part.shape)
        sheet = _moved_sheet(state.sheet.get(pid), M)
        if p.get("copy") or p.get("type") == "copy":
            np_ = state.new_part(moved, f["id"], part.color)
            state.parts[np_.id] = Part(np_.id, part.name + " (copy)", moved, part.color, part.face_colors)
            if sheet is not None:
                state.sheet[np_.id] = sheet          # a copy of a sheet metal part is sheet metal too
        else:
            state.parts[pid] = Part(pid, part.name, moved, part.color, part.face_colors)
            if sheet is not None:
                state.sheet[pid] = sheet
            else:
                state.sheet.pop(pid, None)


def run_sm_base(state, f):
    """Sheet metal from sketch regions: a flat sheet of the given thickness (one part per region)."""
    from . import sheetmetal as SM
    p = f["params"]
    regions = resolve_regions(state, p.get("regions", []))
    if not regions:
        raise K.KernelError("Select the sketch region(s) for the sheet")
    t, r, k = float(p.get("thickness", 1.5)), float(p.get("radius", 1.5)), float(p.get("k", 0.44))
    if t <= 0:
        raise K.KernelError("The thickness must be more than 0")
    if not (0 < k <= 1):
        raise K.KernelError("The K-factor is between 0 and 1 (0.44 suits most steel and aluminium)")
    plane = state.sketches[p["regions"][0]["sketch"]].plane
    for face in regions:
        solid, model = SM.make_base(face, plane, t, r, k, bool(p.get("flip")),
                                    "rect" if p.get("relief", True) else "none", float(p.get("relief_scale", 1.5)))
        part = state.new_part(solid, f["id"])
        state.sheet[part.id] = model


def _sheet_edges(state, refs, what):
    """For each picked edge: (part id, its sheet model, panel, a, b, outward) — the panel edge it is."""
    if not refs:
        raise K.KernelError(f"Pick the edges to put {what} on")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for part in state.parts.values():
        a, b = K.bbox(part.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    for ref in refs:
        pid = ref.get("part")
        if pid not in state.sheet:
            pid = next(iter(state.sheet), None) if len(state.sheet) == 1 else pid
        model = state.sheet.get(pid)
        if model is None or pid not in state.parts:
            raise K.KernelError(f"{what.capitalize()} go on sheet metal parts — make the part with Sheet metal first")
        panel, a, b, o = model.find_edge(ref, scale)
        yield pid, model, panel, a, b, o


def run_sm_hem(state, f):
    """Hems: fold the chosen edges right back over the sheet (180°)."""
    from . import sheetmetal as SM
    p = f["params"]
    for pid, model, panel, a, b, o in list(_sheet_edges(state, p.get("edges", []), "hems")):
        part = state.parts[pid]
        shape, model = SM.add_hem(part.shape, state.sheet[pid], panel, a, b, o, float(p.get("length", 10)),
                                  float(p["radius"]) if p.get("own_radius") else None, bool(p.get("down")), f["id"])
        state.parts[pid] = Part(pid, part.name, shape, part.color)
        state.sheet[pid] = model


def run_sm_jog(state, f):
    """Jogs: from the chosen edges the sheet steps up (or down) and carries on parallel."""
    from . import sheetmetal as SM
    p = f["params"]
    for pid, model, panel, a, b, o in list(_sheet_edges(state, p.get("edges", []), "jogs")):
        part = state.parts[pid]
        shape, model = SM.add_jog(part.shape, state.sheet[pid], panel, a, b, o, float(p.get("offset", 10)),
                                  float(p.get("length", 20)), float(p["radius"]) if p.get("own_radius") else None,
                                  bool(p.get("down")), f["id"])
        state.parts[pid] = Part(pid, part.name, shape, part.color)
        state.sheet[pid] = model


def run_sm_tab(state, f):
    """Tabs: more sheet in a panel's plane, from sketch regions drawn on its face."""
    from . import sheetmetal as SM
    p = f["params"]
    regions = resolve_regions(state, p.get("regions", []))
    if not regions:
        raise K.KernelError("Pick the sketch region(s) for the tab, drawn on a sheet metal face")
    for face in regions:
        done = False
        for pid, model in list(state.sheet.items()):
            try:
                shape, model = SM.add_tab(state.parts[pid].shape, model, face, f["id"])
            except K.KernelError:
                continue
            part = state.parts[pid]
            state.parts[pid] = Part(pid, part.name, shape, part.color)
            state.sheet[pid] = model
            done = True
            break
        if not done:
            raise K.KernelError("Draw the tab on the face of a sheet metal panel, touching its edge")


def run_sm_convert(state, f):
    """Make a flat plate (an imported one, say) into sheet metal, so it can get flanges and be unfolded."""
    from . import sheetmetal as SM
    p = f["params"]
    refs = p.get("faces", [])
    if not refs:
        raise K.KernelError("Pick one of the plate's big flat faces")
    pid, face = resolve(state, refs[0], "face")
    part = state.parts[pid]
    shape, model = SM.convert_plate(part.shape, face, float(p.get("radius", 1.5)), float(p.get("k", 0.44)),
                                    "rect" if p.get("relief", True) else "none", float(p.get("relief_scale", 1.5)))
    state.parts[pid] = Part(pid, part.name, shape, part.color)
    state.sheet[pid] = model


def run_sm_flange(state, f):
    """Flanges: bend the sheet up (or down) along the chosen edges."""
    from . import sheetmetal as SM
    p = f["params"]
    refs = p.get("edges", [])
    if not refs:
        raise K.KernelError("Pick the edges to put flanges on")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for part in state.parts.values():
        a, b = K.bbox(part.shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    for ref in refs:
        pid = ref.get("part")
        if pid not in state.sheet:
            # the ref may name a part that has since been renumbered: try every sheet metal part
            pid = next(iter(state.sheet), None) if len(state.sheet) == 1 else pid
        model = state.sheet.get(pid)
        part = state.parts.get(pid)
        if model is None or part is None:
            raise K.KernelError("Flanges go on sheet metal parts — make the part with Sheet metal first")
        panel, a, b, o = model.find_edge(ref, scale)
        radius = float(p["radius"]) if p.get("own_radius") else None
        shape, model = SM.add_flange(part.shape, model, panel, a, b, o, float(p.get("length", 20)),
                                     float(p.get("angle", 90)), radius, not p.get("down"),
                                     p.get("position", "outside"), f["id"])
        state.parts[pid] = Part(pid, part.name, shape, part.color)
        state.sheet[pid] = model


def _separately(run):
    """'Separate' on Extrude / Revolve / Sweep (New, Solid): each picked area or face is made on its own, so areas
    that touch — a square cut into four by a cross — give one part each instead of one fused part (as SALOME does,
    and Onshape's Sweep Separate custom feature)."""
    def go(state, f):
        from . import body_options as BO
        p = f["params"]
        items = [("regions", r) for r in p.get("regions", [])] + [("faces", x) for x in p.get("faces", [])]
        if not p.get("separate") or p.get("op", "add") != "new" or BO.creation(p) != "solid" or len(items) < 2:
            return run(state, f)
        before = set(state.parts)
        for kind, item in items:
            q = dict(p, regions=[item] if kind == "regions" else [], faces=[item] if kind == "faces" else [],
                     separate=False)
            run(state, dict(f, params=q))
        if p.get("separate_colors"):
            # each new part its own colour, to tell them apart (the Auto colour palette: no highlight oranges)
            new = [pid for pid in state.parts if pid not in before]
            for pid, col in zip(new, auto_colors(len(new))):
                old = state.parts[pid]
                state.parts[pid] = Part(old.id, old.name, old.shape, tuple(col), old.face_colors)
    go.__name__ = run.__name__
    go.__doc__ = run.__doc__
    return go


RUNNERS = {
    "sketch": run_sketch, "extrude": run_extrude, "revolve": run_revolve, "fillet": run_fillet,
    "chamfer": run_chamfer, "shell": run_shell, "plane": run_plane, "mirror": run_mirror,
    "linear_pattern": run_linear_pattern, "circular_pattern": run_circular_pattern,
    "curve_pattern": run_curve_pattern, "point_pattern": run_point_pattern,
    "mate_connector": run_mate_connector,
    "boolean": run_boolean, "import": run_import, "hole": run_hole, "variable": run_variable, "measure_value": run_measure_value,
    "derived": run_derived,
    "sweep": run_sweep, "loft": run_loft, "draft": run_draft, "split": run_split, "move": run_move,
    "thread": run_thread, "sm_base": run_sm_base, "sm_flange": run_sm_flange,
    "sm_hem": run_sm_hem, "sm_jog": run_sm_jog, "sm_tab": run_sm_tab, "sm_convert": run_sm_convert,
    "delete_face": run_delete_face, "move_face": run_move_face, "offset_surface": run_offset_surface,
}
for _k in ("extrude", "revolve", "sweep"):
    RUNNERS[_k] = _separately(RUNNERS[_k])


def _more_features():
    from . import more_features as MF
    for k, fn in MF.RUNNERS.items():
        RUNNERS.setdefault(k, fn)
        FEATURE_LABELS.setdefault(k, MF.LABELS[k])


_more_features()


def _MF_DEFAULTS():
    import copy as _copy
    from . import more_features as MF
    return _copy.deepcopy(MF.DEFAULTS)


def default_params(ftype: str, state: ModelState | None = None) -> dict:
    has_parts = bool(state and state.parts)
    op = "add" if has_parts else "new"
    from . import measure_value as _MV
    if ftype.startswith("plugin:"):
        from . import plugins
        return plugins.default_params(ftype, has_parts)
    return {
        "sketch": {"plane": {"kind": "std", "name": "Top"}, "sketch": Sketch().to_dict()},
        "extrude": {"creation": "solid", "regions": [], "faces": [], "curves": [], "thickness": 2.0,
                    "thickness2": 1.0, "thin_side": "mid", "end": "blind", "depth": 25.0, "depth2": 10.0,
                    "second": False, "end2": "blind", "flip": False, "draft": False, "draft_angle": 3.0,
                    "draft2": False, "draft_angle2": 3.0, "op": op},
        "revolve": {"creation": "solid", "regions": [], "faces": [], "curves": [], "thickness": 2.0,
                    "thickness2": 1.0, "thin_side": "mid", "axis": {}, "end": "full", "angle": 180.0, "angle2": 90.0, "flip": False,
                    "op": op},
        "fillet": {"edges": [], "radius": 2.0, "variable": False, "radius2": 1.0},
        "chamfer": {"edges": [], "type": "equal", "distance": 1.0, "distance2": 2.0, "angle": 45.0, "flip": False},
        "shell": {"faces": [], "thickness": 2.0, "outward": False},
        "delete_face": {"faces": [], "mode": "heal"},
        "move_face": {"faces": [], "distance": 2.0, "flip": False},
        "offset_surface": {"faces": [], "distance": 2.0, "flip": False},
        **{k: dict(v, **({"op": op} if k == "thicken" else {})) for k, v in _MF_DEFAULTS().items()},
        "plane": {"type": "auto", "refs": [], "offset": 20.0, "angle": 45.0, "flip": False},
        "mirror": {"parts": [], "plane": {"kind": "std", "name": "Right"}, "op": "add"},
        "linear_pattern": {"parts": [], "direction": {"kind": "std", "name": "X"}, "count": 3,
                           "spacing": 30.0, "flip": False, "second": False,
                           "direction2": {"kind": "std", "name": "Y"}, "dir2_std": "Y", "count2": 2,
                           "spacing2": 30.0, "flip2": False, "op": "new"},
        "circular_pattern": {"parts": [], "axis": {"kind": "std", "name": "Z"}, "count": 6,
                             "angle": 360.0, "op": "new"},
        "curve_pattern": {"parts": [], "path": [], "count": 4, "spacing_mode": "fit", "spacing": 20.0,
                          "flip": False, "keep_orientation": False, "op": "new"},
        "point_pattern": {"parts": [], "locations": [], "reference": None, "op": "new"},
        "mate_connector": {"on": None, "point": 0, "flip": False, "rotate": 0.0, "dx": 0.0, "dy": 0.0, "dz": 0.0},
        "boolean": {"op": "union", "tools": [], "targets": [], "keep_tools": False},
        "import": {"data": "", "filename": ""},
        "variable": {"name": "", "expr": "10"},
        "measure_value": dict(_MV.DEFAULTS, entities=[]),
        "derived": {"uid": "", "path": "", "studio_name": "", "parts": ["*"], "config": None},
        "thread": {"faces": [], "size": "auto", "major": 6.0, "pitch": 1.0, "length_mode": "full", "length": 10.0,
                   "left_hand": False, "clearance": 0.0},
        "hole": {"sketch": None, "standard": "clearance", "size": "M4", "fit": "normal", "diameter": 5.0,
                 "model_thread": False, "thread_clearance": 0.0, "left_hand": False,
                 "depth": 10.0, "end": "through", "style": "simple", "tip": True,
                 "counterbore_dia": 9.0, "counterbore_depth": 3.0, "countersink_dia": 10.0},
        "sweep": {"creation": "solid", "regions": [], "faces": [], "curves": [], "thickness": 2.0,
                  "thickness2": 1.0, "thin_side": "mid", "path": [], "control": "none", "lock_std": "Z",
                  "support": [], "guide": [], "op": op},
        "loft": {"creation": "solid", "profiles": [], "thickness": 2.0, "thickness2": 1.0, "thin_side": "mid",
                 "ruled": False, "path": [], "op": op},
        "draft": {"faces": [], "neutral": {"kind": "std", "name": "Top"}, "angle": 3.0, "flip": False},
        "split": {"parts": [], "tools": []},
        "move": {"type": "xyz", "parts": [], "dx": 0.0, "dy": 0.0, "dz": 0.0, "axis": {"kind": "std", "name": "Z"},
                 "axis_std": "Z", "rotate": 0.0, "distance": 10.0, "flip": False, "from_point": None,
                 "to_point": None, "from_mc": "", "to_mc": "", "scale": 1.0, "about": None, "copy": False},
        "sm_base": {"regions": [], "thickness": 1.5, "radius": 1.5, "k": 0.44, "flip": False, "relief": True,
                    "relief_scale": 1.5},
        "sm_hem": {"edges": [], "length": 10.0, "down": False, "own_radius": False, "radius": 0.75},
        "sm_jog": {"edges": [], "offset": 10.0, "length": 20.0, "down": False, "own_radius": False, "radius": 1.5},
        "sm_tab": {"regions": []},
        "sm_convert": {"faces": [], "radius": 1.5, "k": 0.44, "relief": True, "relief_scale": 1.5},
        "sm_flange": {"edges": [], "length": 20.0, "angle": 90.0, "position": "outside", "down": False,
                      "own_radius": False, "radius": 1.5},
    }[ftype]


def _matched_faces(src_shape, src_faces, dst_shape):
    """Faces of dst_shape that are the given faces of src_shape — unchanged, or trimmed by a later feature."""
    if src_shape.IsSame(dst_shape):
        return set(src_faces)
    fake = Part("x", "x", src_shape, DEFAULT_COLOR, {i: (1, 1, 1) for i in src_faces})
    return set((carry_face_colors(fake, dst_shape) or {}).keys())


def feature_new_faces(before: ModelState, after: ModelState):
    """{part id: set of face indices} that a feature created (faces of `after` with no counterpart in `before`)."""
    out = {}
    for pid, part in after.parts.items():
        old = before.parts.get(pid) or before.parts.get(pid.split(".")[0])
        n = len(K.faces(part.shape))
        if old is None:
            out[pid] = set(range(n))
            continue
        if old.shape.IsSame(part.shape):
            continue
        existing = _matched_faces(old.shape, range(len(K.faces(old.shape))), part.shape)
        new = set(range(n)) - existing
        if new:
            out[pid] = new
    return out


def map_faces_to(state_from: ModelState, faces_by_part, state_to: ModelState):
    """Follow faces from one model state to a later one (the faces the user sees now)."""
    out = {}
    for pid, fis in faces_by_part.items():
        src = state_from.parts.get(pid)
        if src is None:
            continue
        targets = [p for q, p in state_to.parts.items() if q == pid or q.split(".")[0] == pid]
        for p in targets:
            got = _matched_faces(src.shape, fis, p.shape)
            if got:
                out[p.id] = got
    return out
