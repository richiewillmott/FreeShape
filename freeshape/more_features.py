"""Whole features from Onshape's Part Studio menu that FreeShape didn't have yet:

    Thicken      surfaces or faces into solids (one side, the other, both equally or two thicknesses)
    Enclose      a solid from the space surfaces / faces / planes close off between them
    Rib          a wall from sketch lines out to the part (gussets, stiffeners)
    Delete part  remove parts from the model from this point on
    Helix        a helix curve around an axis, a circle or a cylinder (a path for springs and threads)
    Bounding box a box part around parts (with optional clearance), for stock size or packaging
    Compound     several solids as one part that share their touching faces (SALOME's Glue faces / Partition)
    Explode      a part split into its solids — or its faces as surfaces, or its edges as curves (SALOME's Explode)

Selections are stored as [[kind, ref], ...] per box (see ui/dialogs.PAIR_FEATURES).
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K

THICKEN_SIDES = [("one", "One side"), ("other", "Other side"), ("mid", "Both sides equally"),
                 ("two", "Two thicknesses")]
HELIX_MODES = [("turns_pitch", "Turns and pitch"), ("height_turns", "Height and turns"),
               ("height_pitch", "Height and pitch")]
RIB_DIRECTIONS = [("parallel", "Parallel to the sketch plane"), ("normal", "Normal to the sketch plane")]

DEFAULTS = {
    "thicken": {"items": [], "thickness": 2.0, "side": "one", "thickness2": 1.0, "op": "new", "scope": None},
    "enclose": {"items": [], "keep_tools": False, "op": "new", "scope": None},
    "rib": {"curves": [], "parts": [], "thickness": 2.0, "direction": "parallel", "flip": False},
    "delete_part": {"parts": []},
    "helix": {"base": [], "axis_std": "Z", "radius": 10.0, "mode": "turns_pitch", "turns": 5.0, "pitch": 3.0,
              "height": 15.0, "start_angle": 0.0, "left_hand": False, "flip": False},
    "bounding_box": {"parts": [], "clearance": 0.0, "op": "new"},
    "compound": {"parts": [], "glue": True, "tolerance": 0.0},
    "explode_part": {"parts": [], "what": "solid", "keep": False},
}
EXPLODE_KINDS = [("solid", "Solids"), ("face", "Faces (as surfaces)"), ("edge", "Edges (as curves)")]


def _pairs(p, key):
    return [(k, r) for k, r in (p.get(key) or [])]


def _orient(solid):
    """OpenCascade sometimes hands back a solid turned inside out (negative volume); turn it the right way."""
    try:
        if K.volume(solid) < 0:
            return solid.Reversed()
    except Exception:
        pass
    return solid


# --------------------------------------------------------------------------- Thicken
def _thick(shape, t):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    mk = BRepOffsetAPI_MakeThickSolid()
    mk.MakeThickSolidBySimple(shape, t)
    mk.Build()
    if not mk.IsDone():
        raise K.KernelError("Couldn't thicken this (try a smaller thickness)")
    return _orient(mk.Shape())


def _offset(shape, d):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape
    mo = BRepOffsetAPI_MakeOffsetShape()
    mo.PerformBySimple(shape, d)
    if not mo.IsDone():
        raise K.KernelError("Couldn't offset this surface")
    return mo.Shape()


def thicken_shape(shape, side, t, t2=0.0):
    """A solid from a face or surface: along its normal (one), against it (other), centred (mid) or t one
    way and t2 the other (two)."""
    if t <= 0:
        raise K.KernelError("Thickness must be more than 0")
    if side == "one":
        return _thick(shape, t)
    if side == "other":
        return _thick(shape, -t)
    if side == "mid":
        return _thick(_offset(shape, -t / 2), t)
    if t2 < 0:
        raise K.KernelError("Second thickness can't be negative")
    return _thick(_offset(shape, -t2), t + t2) if t2 > 0 else _thick(shape, t)


def _surfaces(state, items):
    """The shapes picked for Thicken / Enclose: surface parts whole, single faces, planes (as big faces)."""
    from . import features as F
    from .measure_value import _plane_face
    out = []
    for kind, ref in items:
        if kind == "part":
            if ref not in state.parts:
                raise K.KernelError("A picked surface no longer exists")
            out.append(("part", ref, state.parts[ref].shape))
        elif kind == "face":
            pid, face = F.resolve(state, ref, "face")
            out.append(("face", pid, face))
        elif kind == "plane":
            out.append(("plane", None, _plane_face(F.resolve_plane(state, ref), 1e3)))
    return out


def _grown(face, d):
    """A flat face grown outwards by d all round (None for curved faces)."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.BRepTools import BRepTools
    from OCP.GeomAbs import GeomAbs_Intersection
    if K.planar_face_plane(face) is None:
        return None
    area = K.face_info(face)["area"]
    for sign in (1, -1):
        try:
            mo = BRepOffsetAPI_MakeOffset(BRepTools.OuterWire_s(face), GeomAbs_Intersection)
            mo.Perform(sign * d)
            g = _face_on(face, mo.Shape())
            if K.face_info(g)["area"] > area:
                return g
        except Exception:
            continue
    return None


def _face_on(face, wire):
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.TopoDS import TopoDS
    if wire.ShapeType().name == "TopAbs_WIRE":
        w = TopoDS.Wire(wire) if hasattr(TopoDS, "Wire") else TopoDS.Wire_s(wire)
    else:
        w = K.make_wire(K.edges(wire))
    mk = BRepBuilderAPI_MakeFace(BRep_Tool.Surface_s(face), w)
    g = mk.Face()
    from OCP.ShapeFix import ShapeFix_Face
    fx = ShapeFix_Face(g)
    fx.Perform()
    g = fx.Face()
    if face.Orientation() != g.Orientation():
        r = g.Reversed()
        g = TopoDS.Face(r) if hasattr(TopoDS, "Face") else TopoDS.Face_s(r)
    return g


def _corner_fills(faces, side, t, t2):
    """The blocks that fill outside corners between walls thickened from neighbouring flat faces: where the
    walls of the faces, each grown a little past its edges, overlap."""
    grow = (t + t2) * 1.5
    grown = []
    for fc in faces:
        g = _grown(fc, grow)
        grown.append(thicken_shape(g, side, t, t2) if g is not None else None)
    out = []
    for i in range(len(faces)):
        for j in range(i + 1, len(faces)):
            if grown[i] is None or grown[j] is None:
                continue
            shared = [e for e in K.edges(faces[i]) if any(e.IsSame(x) for x in K.edges(faces[j]))]
            if len(shared) != 1:                          # only faces that meet along one edge
                continue
            pts = np.asarray(K.discretize_edge(shared[0]), float)
            a, b = pts[0], pts[-1]
            if np.linalg.norm(b - a) < 1e-9:
                continue
            c = K.common(grown[i], grown[j])
            if c is None or not K.solids(c):
                continue
            # only along the shared edge, not past its ends
            u = (b - a) / np.linalg.norm(b - a)
            v = np.cross(u, [0, 0, 1] if abs(u[2]) < 0.9 else [1, 0, 0])
            v /= np.linalg.norm(v)
            w = np.cross(u, v)
            r = (t + t2) * 10 + 10
            cap = _prism(_quad([a + r * (-v - w), a + r * (v - w), a + r * (v + w), a + r * (-v + w)]), u,
                         float(np.linalg.norm(b - a)))
            c = K.common(c, cap)
            if c is not None and K.solids(c):
                out.append(c)
    return out


def run_thicken(state, f):
    from . import features as F
    p = f["params"]
    picks = _surfaces(state, _pairs(p, "items"))
    if not picks:
        raise K.KernelError("Select surfaces or faces to thicken")
    from .body_options import _shells
    t, t2 = float(p.get("thickness", 2)), float(p.get("thickness2", 1))
    sheets, by_part = [], {}
    for kind, pid, shape in picks:
        if kind == "part" and K.solids(shape):
            raise K.KernelError("Thicken works on surfaces and faces — pick a face of a solid part")
        if kind == "part":
            sheets += _shells(shape) or [shape]
        elif kind == "face":
            by_part.setdefault(pid, []).append(shape)
    bodies = [thicken_shape(sh, p.get("side", "one"), t, t2) for sh in sheets]
    for faces in by_part.values():
        # each face is thickened on its own and the walls are joined (an outside corner between two faces
        # isn't filled in — OpenCascade can't offset an open set of faces as one sheet reliably)
        walls = [thicken_shape(fc, p.get("side", "one"), t, t2) for fc in faces]
        if len(walls) > 1:
            walls += _corner_fills(faces, p.get("side", "one"), t, t2)
        bodies.append(K.fuse_all(walls) if len(walls) > 1 else walls[0])
    F.apply_body(state, K.combine(bodies), p.get("op", "new"), f["id"], p.get("scope"))


# --------------------------------------------------------------------------- Enclose
def enclose_solids(shapes):
    """The closed-off volumes between surfaces that meet or cross each other."""
    from OCP.BOPAlgo import BOPAlgo_MakerVolume
    mv = BOPAlgo_MakerVolume()
    mv.SetArguments(K._list(shapes))
    mv.SetIntersect(True)
    mv.SetRunParallel(True)
    mv.Perform()
    if mv.HasErrors():
        raise K.KernelError("Couldn't work out the enclosed space")
    sols = [_orient(s) for s in K.solids(mv.Shape())]
    sols = [s for s in sols if K.volume(s) > 1e-9]
    if not sols:
        raise K.KernelError("These surfaces don't close off any space")
    return sols


def run_enclose(state, f):
    from . import features as F
    p = f["params"]
    picks = _surfaces(state, _pairs(p, "items"))
    if len(picks) < 1:
        raise K.KernelError("Select the surfaces, faces or planes that close off a space")
    sols = enclose_solids([s for _k, _pid, s in picks])
    if not p.get("keep_tools"):
        for kind, pid, shape in picks:
            if kind == "part" and not K.solids(shape):
                state.parts.pop(pid, None)            # the surfaces are used up, as in Onshape
    F.apply_body(state, K.combine(sols), p.get("op", "new"), f["id"], p.get("scope"))


# --------------------------------------------------------------------------- Rib
def _line_segments(state, refs):
    """Straight segments (a, b, sketch plane) of the picked sketch curves."""
    from . import features as F
    segs = []
    for ref in refs:
        if ref.get("kind") != "sketch_chain" or ref.get("sketch") not in state.sketches:
            raise K.KernelError("Rib needs sketch lines")
        pl = state.sketches[ref["sketch"]].plane
        w = F._path_wire(state, [ref])
        for e in K.edges(w):
            from .measure import _geom
            if _geom("edge", e)["type"] != "line":
                raise K.KernelError("Rib profiles must be straight sketch lines")
            pts = np.asarray(K.discretize_edge(e), float)
            segs.append((pts[0], pts[-1], pl))
    return segs


def _prism(face, d, length):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    v = np.asarray(d, float) * length
    return BRepPrimAPI_MakePrism(face, _gpvec(v)).Shape()


def _gpvec(v):
    from OCP.gp import gp_Vec
    return gp_Vec(*[float(x) for x in v])


def _quad(pts):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    from OCP.gp import gp_Pnt
    poly = BRepBuilderAPI_MakePolygon()
    for q in pts:
        poly.Add(gp_Pnt(*[float(x) for x in q]))
    poly.Close()
    return BRepBuilderAPI_MakeFace(poly.Wire()).Face()


def rib_body(a, b, normal, thickness, direction, targets, flip=False):
    """One straight rib: a wall `thickness` thick along the line a–b, grown (parallel to the sketch plane, or
    along its normal) until it meets the target parts; only the piece between the line and the parts is kept."""
    u = (b - a) / np.linalg.norm(b - a)
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    h = thickness / 2
    if direction == "normal":
        side = np.cross(n, u)                         # the wall's width lies in the sketch plane
        start = _quad([a - side * h, b - side * h, b + side * h, a + side * h])
        dirs = [n, -n]
    else:
        start = _quad([a - n * h, b - n * h, b + n * h, a + n * h])
        perp = np.cross(n, u)
        dirs = [perp, -perp]
    if flip:
        dirs = dirs[::-1]
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for t in targets:
        x, y = K.bbox(t)
        lo, hi = np.minimum(lo, x), np.maximum(hi, y)
    reach = float(np.linalg.norm(hi - lo)) * 2 + float(np.linalg.norm(b - a)) + 10
    target = K.combine(targets)
    from .measure import _min_dist
    for d in dirs:
        slab = _prism(start, d, reach)
        if not K.intersects(target, slab):
            continue
        pieces = [s for s in K.solids(K.cut(slab, target))
                  if (_min_dist(s, start) or (1e9,))[0] < 1e-6]
        # the piece that starts at the line must actually reach the part (not run off into space)
        pieces = [s for s in pieces if (_min_dist(s, target) or (1e9,))[0] < 1e-6]
        if pieces:
            return K.combine(pieces)
    raise K.KernelError("The rib doesn't reach the part (try Opposite direction or the other rib direction)")


def run_rib(state, f):
    from . import features as F
    from .features import Part
    p = f["params"]
    segs = _line_segments(state, [r for _k, r in _pairs(p, "curves")])
    if not segs:
        raise K.KernelError("Select sketch lines for the rib")
    ids = [r for _k, r in _pairs(p, "parts") if r in state.parts] or \
        [i for i, pt in state.parts.items() if K.solids(pt.shape)]
    if not ids:
        raise K.KernelError("A rib needs a part to grow out to")
    t = float(p.get("thickness", 2))
    if t <= 0:
        raise K.KernelError("Thickness must be more than 0")
    targets = [state.parts[i].shape for i in ids]
    ribs = [rib_body(a, b, pl.normal, t, p.get("direction", "parallel"), targets, bool(p.get("flip")))
            for a, b, pl in segs]
    rib = K.combine(ribs)
    # join the rib to every target part it touches
    for pid in ids:
        part = state.parts[pid]
        if K.touches_or_intersects(part.shape, rib):
            state.parts[pid] = Part(pid, part.name, K.fuse(part.shape, rib), part.color)
            return
    F.apply_body(state, rib, "new", f["id"])


# --------------------------------------------------------------------------- Delete part
def run_delete_part(state, f):
    ids = [r for _k, r in _pairs(f["params"], "parts")]
    if not ids:
        raise K.KernelError("Select parts to delete")
    for pid in ids:
        state.parts.pop(pid, None)
        state.sheet.pop(pid, None)


# --------------------------------------------------------------------------- Helix
def helix_numbers(p):
    """(turns, pitch, height) from whichever two the mode gives."""
    mode = p.get("mode", "turns_pitch")
    turns, pitch, height = float(p.get("turns", 5)), float(p.get("pitch", 3)), float(p.get("height", 15))
    if mode == "height_turns":
        if turns <= 0:
            raise K.KernelError("Turns must be more than 0")
        pitch = height / turns
    elif mode == "height_pitch":
        if pitch == 0:
            raise K.KernelError("Pitch can't be 0")
        turns = height / pitch
    else:
        height = turns * pitch
    if turns <= 0 or pitch <= 0 or height <= 0:
        raise K.KernelError("Turns, pitch and height must be more than 0")
    return turns, pitch, height


def helix_wire(origin, axis, radius, turns, pitch, start_dir=None, start_angle=0.0, left=False):
    """A helix starting at origin + radius·start_dir (turned by start_angle°), climbing along axis."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.BRepLib import BRepLib
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.Geom2d import Geom2d_Line
    from OCP.gp import gp_Ax3, gp_Dir, gp_Dir2d, gp_Pnt, gp_Pnt2d
    if radius <= 0:
        raise K.KernelError("Radius must be more than 0")
    z = np.asarray(axis, float) / np.linalg.norm(axis)
    x = np.asarray(start_dir, float) if start_dir is not None else np.cross(z, [0, 0, 1] if abs(z[2]) < .9 else [1, 0, 0])
    x = x - (x @ z) * z
    x /= np.linalg.norm(x)
    ax = gp_Ax3(gp_Pnt(*[float(c) for c in origin]), gp_Dir(*z), gp_Dir(*x))
    surf = Geom_CylindricalSurface(ax, float(radius))
    a0 = math.radians(start_angle)
    du = -2 * math.pi if left else 2 * math.pi
    line = Geom2d_Line(gp_Pnt2d(a0, 0.0), gp_Dir2d(du, pitch))
    length = turns * math.hypot(2 * math.pi, pitch)          # in the (u, v) parameter plane
    e = BRepBuilderAPI_MakeEdge(line, surf, 0.0, length).Edge()
    BRepLib.BuildCurves3d_s(e)
    return K.make_wire([e])


def helix_base(state, p):
    """(origin, axis, radius, start direction) from a circle edge, a cylinder face or a world axis."""
    from . import features as F
    from .measure_value import _geom
    base = _pairs(p, "base")
    if base:
        kind, ref = base[0]
        if kind == "axis":
            a, d = F.resolve_axis(state, ref)
            return np.asarray(a, float), np.asarray(d, float), float(p.get("radius", 10)), None
        _pid, sub = F.resolve(state, ref, kind)
        g = _geom(kind, sub)
        if g["type"] == "circle":
            c, d = np.asarray(g["p"], float), np.asarray(g["d"], float)
            start = np.asarray(K.discretize_edge(sub), float)[0] - c
            return c, d, g["r"], start if np.linalg.norm(start) > 1e-9 else None
        if g["type"] == "cylinder":
            d = np.asarray(g["d"], float) / np.linalg.norm(g["d"])
            pts = np.asarray(K.tessellate(sub).verts, float)
            s = (pts - g["p"]) @ d
            return np.asarray(g["p"], float) + d * float(s.min()), d, g["r"], None
        raise K.KernelError("Pick a circle edge, a cylindrical face or an axis")
    std = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[p.get("axis_std") or "Z"]
    return np.zeros(3), np.array(std, float), float(p.get("radius", 10)), None


def run_helix(state, f):
    p = f["params"]
    origin, axis, radius, start = helix_base(state, p)
    if p.get("flip"):
        axis = -axis
    turns, pitch, _h = helix_numbers(p)
    w = helix_wire(origin, axis, radius, turns, pitch, start, float(p.get("start_angle", 0)),
                   bool(p.get("left_hand")))
    part = state.new_part(w, f["id"])
    part.name = f"Helix {state.part_counter}"


# --------------------------------------------------------------------------- Bounding box
def run_bounding_box(state, f):
    from . import features as F
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt
    p = f["params"]
    ids = [r for _k, r in _pairs(p, "parts") if r in state.parts] or list(state.parts)
    if not ids:
        raise K.KernelError("There are no parts to box")
    lo, hi = np.full(3, 1e18), np.full(3, -1e18)
    for i in ids:
        a, b = K.bbox(state.parts[i].shape)
        lo, hi = np.minimum(lo, a), np.maximum(hi, b)
    c = float(p.get("clearance", 0))
    lo, hi = lo - c, hi + c
    if (hi - lo <= 1e-9).any():
        raise K.KernelError("The box would be flat — add some clearance")
    box = BRepPrimAPI_MakeBox(gp_Pnt(*lo), gp_Pnt(*hi)).Shape()
    F.apply_body(state, box, p.get("op", "new"), f["id"])


# --------------------------------------------------------------------------- Compound
def run_compound(state, f):
    """Several solid parts become one part, a compound of solids. With Glue, faces where the solids touch are
    merged into one shared face (as are their edges and vertices) — two cubes side by side then have 11 faces,
    not 12 — so the solids are joined conformally (for meshing / FEA) but stay separate solids. Without it the
    solids are just gathered into one part. Unlike a composite part, the solids really share the faces."""
    p = f["params"]
    ids = [r for _k, r in _pairs(p, "parts") if r in state.parts]
    if not ids:
        ids = [pid for pid, part in state.parts.items() if K.solids(part.shape)]
    sols = [s for pid in ids for s in K.solids(state.parts[pid].shape)]
    if len(sols) < 2:
        raise K.KernelError("Select at least two solid parts")
    tol = float(p.get("tolerance", 0) or 0)
    shape = K.glue_solids(sols, tol) if p.get("glue", True) else K.make_compound(sols)
    first = state.parts[ids[0]]
    for pid in ids:
        state.parts.pop(pid, None)
        state.sheet.pop(pid, None)
    part = state.new_part(shape, f["id"], first.color)
    part.name = f"Compound {state.part_counter}"


# --------------------------------------------------------------------------- Explode
def run_explode(state, f):
    """Split parts into their pieces, as SALOME's Explode: a compound into its solids (each its own part again),
    or any part into its faces (surface parts) or edges (curve parts). The part is replaced by the pieces unless
    Keep is ticked."""
    p = f["params"]
    ids = [r for _k, r in _pairs(p, "parts") if r in state.parts]
    if not ids:
        raise K.KernelError("Select the parts to explode")
    what = p.get("what", "solid")
    pick = {"solid": K.solids, "face": K.faces, "edge": K.edges}.get(what, K.solids)
    made = 0
    for pid in ids:
        part = state.parts[pid]
        pieces = pick(part.shape)
        if what == "solid" and len(pieces) < 2:
            continue                                # a single solid: nothing to split
        if not p.get("keep"):
            state.parts.pop(pid, None)
            state.sheet.pop(pid, None)
        word = {"solid": "solid", "face": "face", "edge": "edge"}[what]
        for i, piece in enumerate(pieces, 1):
            new = state.new_part(piece, f["id"], part.color)
            new.name = f"{part.name} — {word} {i}"
            made += 1
    if not made:
        raise K.KernelError("Nothing to explode: these parts are single solids (explode into faces or edges "
                            "instead)" if what == "solid" else "These parts have no " + what + "s")


RUNNERS = {"thicken": run_thicken, "enclose": run_enclose, "rib": run_rib, "delete_part": run_delete_part,
           "helix": run_helix, "bounding_box": run_bounding_box, "compound": run_compound,
           "explode_part": run_explode}
LABELS = {"thicken": "Thicken", "enclose": "Enclose", "rib": "Rib", "delete_part": "Delete part",
          "helix": "Helix", "bounding_box": "Bounding box", "compound": "Compound",
          "explode_part": "Explode"}
