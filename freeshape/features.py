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
    "boolean": "Boolean", "import": "Import", "hole": "Hole", "variable": "Variable",
    "sweep": "Sweep", "loft": "Loft", "draft": "Draft", "split": "Split", "move": "Move", "thread": "Thread",
    "sm_base": "Sheet metal", "sm_flange": "Flange",
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

    def copy(self):
        return ModelState(dict(self.parts), dict(self.sketches), dict(self.planes), self.part_counter,
                          dict(self.vars), dict(self.sheet))

    def new_part(self, shape, fid, color=DEFAULT_COLOR):
        self.part_counter += 1
        pid = f"{fid}:{self.part_counter}"
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
    return ref


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

def set_part_shape(state: ModelState, part: Part, shape):
    """Store a new shape for a part; disjoint solids become separate parts (like Onshape)."""
    pieces = K.solids(shape)
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
    targets = [state.parts[p] for p in (scope or state.parts) if p in state.parts]
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
                "countersink_dia", "k", "relief_scale"}


def prepare(f, state):
    """A copy of feature f with every expression evaluated against the variables defined so far."""
    p = f.get("params", {})
    if not any(isinstance(p.get(k), str) for k in NUMERIC_KEYS):
        return f
    p2 = dict(p)
    for k in NUMERIC_KEYS:
        v = p2.get(k)
        if isinstance(v, str):
            val = parse_number(v, angle=(k in ("angle", "rotate", "draft", "twist")), variables=state.vars)
            if val is None:
                bad = [n for n in var_names(v) if n not in state.vars]
                raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{v}'")
            p2[k] = int(round(val)) if k == "count" else val
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


def run_sketch(state, f):
    p = f["params"]
    plane = resolve_plane(state, p["plane"])
    sk = Sketch.from_dict(p["sketch"])
    # dimensions driven by variables are re-evaluated and the sketch re-solved
    driven = [c for c in sk.constraints if isinstance(c.get("expr"), str)]
    for c in driven:
        val = parse_number(c["expr"], angle=(c["type"] == "angle"), variables=state.vars)
        if val is None:
            bad = [n for n in var_names(c["expr"]) if n not in state.vars]
            raise K.KernelError(f"Unknown variable #{bad[0]}" if bad else f"Can't read '{c['expr']}'")
        c["value"] = val
    if driven:
        sk.solve()
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
    p = f["params"]
    profs = _profiles(state, p)
    if not profs:
        raise K.KernelError("Select sketch regions or a planar face")
    n = _profile_normal(state, p, profs)
    op = p.get("op", "add")
    flip = p.get("flip", False)
    end = p.get("end", "blind")
    depth = float(p.get("depth", 10))
    symmetric = end == "symmetric"
    cutting = op in ("remove", "intersect")
    if cutting and not p.get("regions"):
        # cutting from a face goes into the part by default
        flip = not flip

    def make(direction):
        if end == "through":
            lo, hi = np.full(3, 1e18), np.full(3, -1e18)
            for part in state.parts.values():
                a, b = K.bbox(part.shape)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
            dd = float(np.linalg.norm(hi - lo)) * 2 + 10 if state.parts else depth
            both = p.get("both", False) or (cutting and p.get("regions"))
            return K.extrude(profs, direction, dd, depth2=dd if both else 0.0)
        depth2 = float(p.get("depth2", 0)) if p.get("second", False) and not symmetric else 0.0
        return K.extrude(profs, direction, depth, symmetric=symmetric, depth2=depth2)

    d = -n if flip else n
    body = make(d)
    if cutting and end == "blind" and not p.get("second") and state.parts:
        # like Onshape, a cut that misses everything is tried in the other direction
        targets = [state.parts[i] for i in (p.get("scope") or state.parts) if i in state.parts]
        if not any(K.intersects(t.shape, body) for t in targets):
            alt = make(-d)
            if any(K.intersects(t.shape, alt) for t in targets):
                body = alt
    apply_body(state, body, op, f["id"], p.get("scope"))


def run_revolve(state, f):
    p = f["params"]
    profs = _profiles(state, p)
    a, d = resolve_axis(state, p.get("axis", {}))
    if p.get("flip"):
        d = -d
    end = p.get("end", "full")
    angle = 360.0 if end == "full" else float(p.get("angle", 180))
    body = K.revolve(profs, a, d, angle, symmetric=(end == "symmetric"))
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"))


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
        state.parts[pid] = Part(pid, part.name, K.fillet(part.shape, es, float(p.get("radius", 1))), part.color)


def run_chamfer(state, f):
    p = f["params"]
    groups = _group_by_part(state, p.get("edges", []), "edge")
    if not groups:
        raise K.KernelError("Select edges to chamfer")
    for pid, es in groups.items():
        part = state.parts[pid]
        state.parts[pid] = Part(pid, part.name, K.chamfer(part.shape, es, float(p.get("distance", 1))), part.color)


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


def run_plane(state, f):
    p = f["params"]
    base = resolve_plane(state, p["ref"])
    pl = base.offset(float(p.get("offset", 10)) * (-1 if p.get("flip") else 1))
    state.planes[f["id"]] = pl


def _selected_parts(state, p):
    ids = [i for i in p.get("parts", []) if i in state.parts]
    if not ids:
        raise K.KernelError("Select parts")
    return ids


def run_mirror(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    plane = resolve_plane(state, p["plane"])
    bodies = [K.mirror(state.parts[i].shape, plane) for i in ids]
    body = K.compound(bodies)
    apply_body(state, body, p.get("op", "add"), f["id"], ids if p.get("op", "add") == "add" else None)


def run_linear_pattern(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    dref = p.get("direction", {"kind": "std", "name": "X"})
    _, d = resolve_axis(state, dref)
    if p.get("flip"):
        d = -d
    n = int(p.get("count", 3))
    sp = float(p.get("spacing", 20))
    copies = []
    for i in ids:
        for k in range(1, n):
            copies.append(K.translate(state.parts[i].shape, d * sp * k))
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"])


def run_circular_pattern(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    a, d = resolve_axis(state, p.get("axis", {"kind": "std", "name": "Z"}))
    n = int(p.get("count", 4))
    total = float(p.get("angle", 360))
    step = total / n if abs(total - 360) < 1e-9 else total / max(n - 1, 1)
    copies = []
    for i in ids:
        for k in range(1, n):
            copies.append(K.rotate(state.parts[i].shape, a, d, step * k))
    if not copies:
        raise K.KernelError("Count must be at least 2")
    apply_body(state, K.compound(copies), p.get("op", "add"), f["id"])


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


def run_import(state, f):
    p = f["params"]
    data = base64.b64decode(p["data"])
    with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
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
    through = p.get("end", "through") == "through"
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
    modelled = p.get("standard") == "tapped" and p.get("model_thread") and p.get("size") in K.ISO_PITCH
    if modelled:
        _model_hole_threads(state, f, p, sk, centres, d, through, depth, back, big)
    tools = []
    from_circles = p.get("standard") == "sketch"
    if from_circles and not any(sizes):
        raise K.KernelError("“Size of the sketch circles” needs circles in the sketch")
    for (u, v), circle_d in zip(centres, sizes):
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
                shape = K.cut_internal_thread(shape, p0, ax, sp["major"], P, z0, z1, core_r=core,
                                              extend=(open_lo, open_hi), left=left, clearance=clearance)
            else:
                if abs(sp["major"] / 2 - sp["r"]) > 0.25 * P:
                    raise K.KernelError(f"Shaft Ø{2 * sp['r']:.4g} doesn't suit {sp['size']} — pick the size or use Custom")
                shape = K.external_thread(shape, p0, ax, sp["r"], P, z0, z1, left=left, clearance=clearance)
        set_part_shape(state, part, shape)


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
                chain, _closed = sketch_ops._chain(sk, r["entity"])
                ids = {k for k, _ in chain}
                tmp = Sketch.from_dict(sk.to_dict())
                tmp.entities = {k: dict(v, construction=False) for k, v in tmp.entities.items() if k in ids}
                curves = tmp.curves()
            edges += K.make_sketch_edges(sr.plane, curves)
        elif r.get("kind") == "edge":
            _, e = resolve(state, r["edge"], "edge")
            edges.append(e)
    return K.make_wire(edges)


def run_sweep(state, f):
    p = f["params"]
    profs = _profiles(state, p)
    if not p.get("path"):
        raise K.KernelError("Select the path to sweep along")
    body = K.sweep(profs, _path_wire(state, p["path"]))
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"))


def run_loft(state, f):
    p = f["params"]
    profs = []
    for item in p.get("profiles", []):
        if item["kind"] == "region":
            profs += resolve_regions(state, [item["ref"]])
        else:
            profs.append(resolve(state, item["ref"], "face")[1])
    body = K.loft(profs, p.get("ruled", False))
    apply_body(state, body, p.get("op", "add"), f["id"], p.get("scope"))


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


def run_split(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    plane = resolve_plane(state, p.get("plane") or {"kind": "std", "name": "Right"})
    for pid in ids:
        part = state.parts[pid]
        pieces = K.split_by_plane(part.shape, plane)
        if p.get("keep") == "front":
            pieces = [s_ for s_ in pieces if (K.mass_props(s_)["centroid"] - plane.origin) @ plane.normal > 0]
        elif p.get("keep") == "back":
            pieces = [s_ for s_ in pieces if (K.mass_props(s_)["centroid"] - plane.origin) @ plane.normal < 0]
        if not pieces:
            raise K.KernelError("Nothing left on that side of the plane")
        set_part_shape(state, part, K.compound(pieces) if len(pieces) > 1 else pieces[0])


def run_move(state, f):
    p = f["params"]
    ids = _selected_parts(state, p)
    v = (float(p.get("dx", 0)), float(p.get("dy", 0)), float(p.get("dz", 0)))
    ang = float(p.get("rotate", 0))
    a, d = (np.zeros(3), np.array([0, 0, 1.0]))
    if abs(ang) > 1e-12:
        a, d = resolve_axis(state, p.get("axis") or {"kind": "std", "name": "Z"})
    for pid in ids:
        part = state.parts[pid]
        moved = K.transform(part.shape, v, a, d, ang)
        if p.get("copy"):
            np_ = state.new_part(moved, f["id"], part.color)
            state.parts[np_.id] = Part(np_.id, part.name + " (copy)", moved, part.color, part.face_colors)
        else:
            state.parts[pid] = Part(pid, part.name, moved, part.color, part.face_colors)


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


RUNNERS = {
    "sketch": run_sketch, "extrude": run_extrude, "revolve": run_revolve, "fillet": run_fillet,
    "chamfer": run_chamfer, "shell": run_shell, "plane": run_plane, "mirror": run_mirror,
    "linear_pattern": run_linear_pattern, "circular_pattern": run_circular_pattern,
    "boolean": run_boolean, "import": run_import, "hole": run_hole, "variable": run_variable,
    "sweep": run_sweep, "loft": run_loft, "draft": run_draft, "split": run_split, "move": run_move,
    "thread": run_thread, "sm_base": run_sm_base, "sm_flange": run_sm_flange,
}


def default_params(ftype: str, state: ModelState | None = None) -> dict:
    has_parts = bool(state and state.parts)
    op = "add" if has_parts else "new"
    return {
        "sketch": {"plane": {"kind": "std", "name": "Top"}, "sketch": Sketch().to_dict()},
        "extrude": {"regions": [], "faces": [], "end": "blind", "depth": 25.0, "depth2": 10.0,
                    "second": False, "flip": False, "op": op},
        "revolve": {"regions": [], "faces": [], "axis": {}, "end": "full", "angle": 180.0,
                    "flip": False, "op": op},
        "fillet": {"edges": [], "radius": 2.0},
        "chamfer": {"edges": [], "distance": 1.0},
        "shell": {"faces": [], "thickness": 2.0, "outward": False},
        "plane": {"ref": {"kind": "std", "name": "Top"}, "offset": 20.0, "flip": False},
        "mirror": {"parts": [], "plane": {"kind": "std", "name": "Right"}, "op": "add"},
        "linear_pattern": {"parts": [], "direction": {"kind": "std", "name": "X"}, "count": 3,
                           "spacing": 30.0, "flip": False, "op": "new"},
        "circular_pattern": {"parts": [], "axis": {"kind": "std", "name": "Z"}, "count": 6,
                             "angle": 360.0, "op": "new"},
        "boolean": {"op": "union", "tools": [], "targets": [], "keep_tools": False},
        "import": {"data": "", "filename": ""},
        "variable": {"name": "", "expr": "10"},
        "thread": {"faces": [], "size": "auto", "major": 6.0, "pitch": 1.0, "length_mode": "full", "length": 10.0,
                   "left_hand": False, "clearance": 0.0},
        "hole": {"sketch": None, "standard": "clearance", "size": "M4", "fit": "normal", "diameter": 5.0,
                 "model_thread": False, "thread_clearance": 0.0, "left_hand": False,
                 "depth": 10.0, "end": "through", "style": "simple", "tip": True,
                 "counterbore_dia": 9.0, "counterbore_depth": 3.0, "countersink_dia": 10.0},
        "sweep": {"regions": [], "faces": [], "path": [], "op": op},
        "loft": {"profiles": [], "ruled": False, "op": op},
        "draft": {"faces": [], "neutral": {"kind": "std", "name": "Top"}, "angle": 3.0, "flip": False},
        "split": {"parts": [], "plane": {"kind": "std", "name": "Right"}, "keep": "both"},
        "move": {"parts": [], "dx": 0.0, "dy": 0.0, "dz": 0.0, "axis": {"kind": "std", "name": "Z"},
                 "axis_std": "Z", "rotate": 0.0, "copy": False},
        "sm_base": {"regions": [], "thickness": 1.5, "radius": 1.5, "k": 0.44, "flip": False, "relief": True,
                    "relief_scale": 1.5},
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
