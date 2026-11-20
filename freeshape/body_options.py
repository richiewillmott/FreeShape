"""Options shared by the features that make a body by moving a profile (Extrude, Revolve, Sweep — and Loft later).

Issue #27: rather than adding an option to one feature when someone asks, and again (perhaps differently) to the
next, each option lives here once and every feature that has it uses the same code. FEATURE_PARITY.md (made from
freeshape/parity.py) lists which features have which option, and which are still to do.

The options:

* Creation type — Solid (closed regions / faces), Surface (curves, edges or region outlines swept into open
  surfaces) or Thin (curves or outlines thickened into a wall, then swept into a solid).
* Thin wall — thickness and which side of the curve it goes.
* Draft (taper) — the sides of an extrusion lean in or out by an angle (uses the Draft feature's code).
* Result — Solid / Thin bodies merge with the model by New / Add / Remove / Intersect (features.apply_body);
  surfaces are always new surface bodies.
"""
from __future__ import annotations

import numpy as np

from . import kernel as K

CREATION_TYPES = [("solid", "Solid"), ("surface", "Surface"), ("thin", "Thin")]
THIN_SIDES = [("mid", "Both sides equally"), ("one", "One side"), ("other", "Other side"),
              ("two", "Two thicknesses")]


def creation(p) -> str:
    c = p.get("creation", "solid")
    return c if c in ("solid", "surface", "thin") else "solid"


# ---------------------------------------------------------------------- what gets moved
def curve_wires(state, p):
    """The sketch curves / edges picked for a Surface or Thin body, one wire per picked chain; an outline
    (of a region or face) gives all its loops, holes included. A sketch curve picked as a chain whose chain
    branches (a +, a T …) takes every curve connected to it, branches included — as loose edges, which
    split_network() sorts out."""
    from . import features as F
    out = []
    loose = []                                       # single edges (of parts or sketch regions): joined up below
    for ref in p.get("curves", []) or []:
        if ref.get("kind") == "outline":
            out += outline_wires([F.outline_face(state, ref)])
            continue
        if ref.get("kind") == "region_edge":
            loose.append(F.region_edge(state, ref))
            continue
        if ref.get("kind") == "edge":
            loose.append(F.resolve(state, ref["edge"], "edge")[1])
            continue
        net = _branching_sketch_network(state, ref)
        if net is not None:
            out += net
        else:
            out.append(F._path_wire(state, [ref]))
    return out + join_edges(loose)


def join_edges(edges, tol=1e-6):
    """Edges picked one by one, as wires: each run of edges that meet end to end becomes one wire (so the four
    edges of a square make one closed outline, and three of them a C). Edges that branch are left as their own
    wires, for split_network() to sort out."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire
    left = list(edges)
    out = []
    while left:
        mk = BRepBuilderAPI_MakeWire()
        mk.Add(left.pop(0))
        grown = True
        while grown and left:
            grown = False
            for i, e in enumerate(left):
                trial = BRepBuilderAPI_MakeWire(mk.Wire())
                trial.Add(e)
                if trial.IsDone():
                    mk = trial
                    left.pop(i)
                    grown = True
                    break
        out.append(mk.Wire())
    return out


def _branching_sketch_network(state, ref):
    """Edges of every curve connected to a picked sketch curve (or of exactly the curves in "only"), if they
    branch somewhere (a point with three or more curves); None when they don't (the chain is then used as
    before)."""
    if ref.get("kind") != "sketch_chain":
        return None
    sr = state.sketches.get(ref.get("sketch"))
    if sr is None or ref.get("entity") not in sr.sketch.entities:
        return None
    from . import sketch_ops
    from .sketch import Sketch
    sk = sr.sketch
    ends = {k: sketch_ops._ends(e, True) for k, e in sk.entities.items()
            if not e.get("construction") and not e.get("helper")}
    ends = {k: v for k, v in ends.items() if v}
    if ref["entity"] not in ends:
        return None
    at = {}
    for k, (a, b) in ends.items():
        for pt in (a, b):
            at.setdefault(pt, set()).add(k)
    if ref.get("only"):                                # exactly these curves (picked one by one)
        comp = {k for k in ref["only"] if k in ends}
    else:
        comp, todo = set(), [ref["entity"]]
        while todo:
            k = todo.pop()
            if k in comp:
                continue
            comp.add(k)
            for pt in ends[k]:
                todo += [x for x in at.get(pt, ()) if x not in comp]
    if not any(len(at[pt] & comp) > 2 for k in comp for pt in ends[k]):
        return None
    tmp = Sketch.from_dict(sk.to_dict())
    tmp.entities = {k: dict(v, construction=False) for k, v in tmp.entities.items() if k in comp}
    return K.make_sketch_edges(sr.plane, tmp.curves())


# ---------------------------------------------------------------------- non-conformal curve networks
def split_network(wires):
    """Curves for a surface that cross or branch: split them where they cross and cut the network at every
    junction (a point where three or more curves meet), giving one wire per run between junctions — so a +
    gives four. Curves that neither cross nor branch come back unchanged (the same wires)."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_BuilderAlgo
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_VERTEX
    from OCP.TopExp import TopExp
    edges = [e for w in wires for e in K.edges(w)]
    if len(edges) < 2:
        return wires
    args = K.TopTools_ListOfShape()
    for e in edges:
        args.Append(e)
    g = BRepAlgoAPI_BuilderAlgo()
    g.SetArguments(args)
    g.Build()
    if not g.IsDone():
        return wires
    res = g.Shape()
    try:
        from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape as _DM
    except ImportError:
        from OCP.OCP.collections import IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher as _DM
    vmap = _DM()
    TopExp.MapShapesAndAncestors_s(res, TopAbs_VERTEX, TopAbs_EDGE, vmap)
    emap = K.TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(res, TopAbs_EDGE, emap)
    v_edges = {}                                       # vertex index -> set of edge indices
    for i in range(1, vmap.Extent() + 1):
        ids = {emap.FindIndex(e) for e in vmap.FindFromIndex(i)}
        v_edges[i] = ids
    if emap.Extent() == len(edges) and max(len(v) for v in v_edges.values()) <= 2:
        return wires                                   # nothing crosses or branches: as before
    e_verts = {}
    for vi, es in v_edges.items():
        for ei in es:
            e_verts.setdefault(ei, []).append(vi)
    seen, out = set(), []
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire

    def walk(ei, from_v):
        """Edges from ei onwards, through vertices where just two edges meet."""
        run, cur, v = [ei], ei, from_v
        while True:
            nxt_v = [x for x in e_verts.get(cur, []) if x != v] or [v]
            v = nxt_v[0]
            nb = [x for x in v_edges[v] if x != cur]
            if len(v_edges[v]) != 2 or not nb or nb[0] in seen or nb[0] in run:
                return run
            cur = nb[0]
            seen.add(cur)
            run.append(cur)
    for ei in range(1, emap.Extent() + 1):
        if ei in seen:
            continue
        seen.add(ei)
        vs = e_verts.get(ei, [])
        if len(vs) < 2:                                # a closed edge (a circle) on its own
            run = [ei]
        else:
            fwd = walk(ei, vs[0])
            back = walk(ei, vs[1])
            run = list(reversed(back[1:])) + fwd
        mk = BRepBuilderAPI_MakeWire()
        for k in run:
            mk.Add(K._as_edge(emap.FindKey(k)))
        if mk.IsDone():
            out.append(mk.Wire())
    return out or wires


def outline_ref(kind, ref):
    """A curve item standing for the boundary of a region / face (kind 'region' or 'face')."""
    return {"kind": "outline", "of": kind, "ref": ref}


def outline_wires(faces):
    """Every boundary (outer and holes) of the given faces, as wires."""
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    out = []
    for f in faces:
        exp = TopExp_Explorer(f, TopAbs_WIRE)
        while exp.More():
            out.append(K._caster("Wire")(exp.Current()))
            exp.Next()
    return out


def wire_plane(wire, fallback_normal=None):
    """The plane a (flat) wire lies in: (origin, normal). Straight single lines have no plane of their own —
    fallback_normal (the sketch's) is used then."""
    from OCP.BRepLib import BRepLib_FindSurface
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    fs = BRepLib_FindSurface(wire, 1e-6, True)
    lo, hi = K.bbox(wire)
    if fs.Found():
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
            face = BRepBuilderAPI_MakeFace(fs.Surface(), 1e-6).Face()
            ad = BRepAdaptor_Surface(face)
            if ad.GetType() == GeomAbs_Plane:
                pl = ad.Plane()
                return (lo + hi) / 2, K._np(pl.Axis().Direction())
        except Exception:
            pass
    if fallback_normal is None:
        raise K.KernelError("The curves must lie flat (in one plane)")
    return (lo + hi) / 2, np.asarray(fallback_normal, float)


def picked_normal(state, p):
    """Sketch normal of the first picked sketch curve (for straight lines, which have no plane of their own)."""
    for ref in p.get("curves", []) or []:
        if ref.get("kind") == "sketch_chain" and ref.get("sketch") in state.sketches:
            return np.asarray(state.sketches[ref["sketch"]].plane.normal, float)
        if ref.get("kind") == "outline" and ref.get("of") == "region":
            sk = state.sketches.get(ref["ref"].get("sketch"))
            if sk is not None:
                return np.asarray(sk.plane.normal, float)
        if ref.get("kind") == "region_edge":
            sk = state.sketches.get(ref["region"].get("sketch"))
            if sk is not None:
                return np.asarray(sk.plane.normal, float)
    return None


def _ends(wire):
    """First and last points of a wire (in its edge order)."""
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS_Vertex
    from OCP.BRep import BRep_Tool
    a, b = TopoDS_Vertex(), TopoDS_Vertex()
    TopExp.Vertices_s(K._caster("Wire")(wire), a, b)
    return K._np(BRep_Tool.Pnt_s(a)), K._np(BRep_Tool.Pnt_s(b))


def _offset_wire(wire, normal, d, open_):
    """The wire moved sideways by d in its plane (d=0: itself)."""
    if abs(d) < 1e-12:
        return wire
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    line = _straight_line(wire) if open_ else None
    if line is not None:
        # a single straight line has no plane of its own: shift it sideways within the sketch plane
        if normal is None:
            raise K.KernelError("Pick the line from a sketch to thicken it")
        a, b = line
        side = np.cross(np.asarray(normal, float), b - a)
        side /= np.linalg.norm(side)
        return K.translate(wire, -side * float(d))
    mk = BRepOffsetAPI_MakeOffset(wire, GeomAbs_Intersection, bool(open_))
    try:
        mk.Perform(float(d))
    except Exception:
        raise K.KernelError("Couldn't thicken that curve — try a smaller thickness") from None
    if not mk.IsDone():
        raise K.KernelError("Couldn't thicken that curve — try a smaller thickness")
    ws = []
    exp = TopExp_Explorer(mk.Shape(), TopAbs_WIRE)
    while exp.More():
        ws.append(K._caster("Wire")(exp.Current()))
        exp.Next()
    if len(ws) != 1:
        raise K.KernelError("Couldn't thicken that curve — try a smaller thickness")
    return ws[0]


def _chain_edges(wire):
    """A wire's edges in order along it (as oriented in the wire)."""
    from OCP.BRepTools import BRepTools_WireExplorer
    out = []
    ex = BRepTools_WireExplorer(K._caster("Wire")(wire))
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def _straight_line(wire):
    """(start, end) if the wire is one straight line, else None."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    es = K.edges(wire)
    if len(es) != 1 or BRepAdaptor_Curve(es[0]).GetType() != GeomAbs_Line:
        return None
    return _ends(wire)


def _wire_face(wire, normal):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Pln
    o, n = wire_plane(wire, normal)
    mk = BRepBuilderAPI_MakeFace(gp_Pln(K._pnt(o), K._dir(n)), wire, True)
    if not mk.IsDone():
        raise K.KernelError("Couldn't make a flat face from that curve")
    f = mk.Face()
    from OCP.ShapeFix import ShapeFix_Face
    fix = ShapeFix_Face(f)
    fix.Perform()
    return fix.Face()


def thin_outlines(wire, thickness, side="mid", normal=None, thickness2=None):
    """The outline(s) of a thickened wire: (outer, inner) for a closed wire (a ring), (band, None) for an open one
    (a band with square ends). side: 'mid', 'one', 'other' or 'two' (thickness / thickness2 either side)."""
    t = float(thickness)
    t2 = float(thickness if thickness2 is None else thickness2)
    if t <= 0 or (side == "two" and (t2 < 0 or t + t2 <= 0)):
        raise K.KernelError("Thickness must be positive")
    a, b = {"mid": (t / 2, -t / 2), "one": (t, 0.0), "other": (0.0, -t),
            "two": (t, -t2)}.get(side, (t / 2, -t / 2))
    closed = _is_closed(wire)
    wa = _offset_wire(wire, normal, a, not closed)
    wb = _offset_wire(wire, normal, b, not closed)
    if closed:
        fa, fb = _wire_face(wa, normal), _wire_face(wb, normal)
        return (wa, wb) if K.face_info(fa)["area"] >= K.face_info(fb)["area"] else (wb, wa)
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeEdge
    a0, a1 = _ends(wa)
    b0, b1 = _ends(wb)
    # round the band: along one side, across the end, back along the other side, across the start
    mw = BRepBuilderAPI_MakeWire()
    for e in _chain_edges(wa):
        mw.Add(e)
    mw.Add(BRepBuilderAPI_MakeEdge(K._pnt(a1), K._pnt(b1)).Edge())
    for e in reversed(_chain_edges(wb)):
        mw.Add(K._caster("Edge")(e.Reversed()))
    mw.Add(BRepBuilderAPI_MakeEdge(K._pnt(b0), K._pnt(a0)).Edge())
    if not mw.IsDone():
        raise K.KernelError("Couldn't thicken that curve")
    return mw.Wire(), None


def thin_faces(wires, thickness, side="mid", normal=None, thickness2=None):
    """Flat faces that thicken each wire into a wall: for a closed outline a ring, for an open curve a band with
    square ends. side: 'mid' (thickness split equally), 'one' or 'other' (all of it on one side), or 'two'
    (thickness on one side, thickness2 on the other)."""
    out = []
    for w in wires:
        outer, inner = thin_outlines(w, thickness, side, normal, thickness2)
        if inner is None:
            out.append(_wire_face(outer, normal))
        else:
            out += K.faces(K.cut(_wire_face(outer, normal), _wire_face(inner, normal)))
    if not out:
        raise K.KernelError("Select sketch curves or edges to thicken")
    return out


def _is_closed(wire) -> bool:
    from OCP.BRep import BRep_Tool
    if BRep_Tool.IsClosed_s(wire):
        return True
    a, b = _ends(wire)
    return bool(np.linalg.norm(a - b) < 1e-7)


def profiles(state, p, region_faces):
    """What a body feature moves, for its creation type: faces (Solid, Thin) or wires (Surface).
    region_faces: the picked sketch regions / faces (features._profiles)."""
    c = creation(p)
    if c == "solid":
        return region_faces
    wires = curve_wires(state, p) + outline_wires(region_faces)
    if not wires:
        raise K.KernelError("Select sketch curves, edges or regions")
    split = split_network(wires)
    if c == "surface":
        return split
    faces = thin_faces(split, p.get("thickness", 2.0), p.get("thin_side", "mid"), picked_normal(state, p),
                       p.get("thickness2", p.get("thickness", 2.0)))
    if split is not wires and len(faces) > 1:
        faces = _merge_flat(faces)                     # the walls of a + or T are one wall where they meet
    return faces


def _merge_flat(faces):
    """Overlapping flat wall outlines fused into one region per connected group."""
    shape = faces[0]
    for f in faces[1:]:
        try:
            shape = K.fuse(shape, f)
        except Exception:
            return faces
    try:
        from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
        u.Build()
        shape = u.Shape()
    except Exception:
        pass
    return K.faces(shape) or faces


def profile_normal(state, p, profs):
    """Direction an extrusion goes by default: the sketch's normal, or the plane of the profile."""
    if p.get("regions"):
        return np.asarray(state.sketches[p["regions"][0]["sketch"]].plane.normal, float)
    sk_n = picked_normal(state, p)
    if sk_n is not None:
        return sk_n
    first = profs[0]
    if first.ShapeType() == 4:          # TopAbs_FACE
        pl = K.planar_face_plane(first)
        if pl is None:
            raise K.KernelError("Profile face must be planar")
        return pl.normal
    return wire_plane(first)[1]


# ---------------------------------------------------------------------- draft (taper)
def taper(body, profile_plane_origin, profile_normal_, direction, angle_deg):
    """Lean the sides of an extruded body by angle_deg (positive: narrowing away from the sketch). Uses the
    Draft feature's code on the faces that run along the extrusion; the sketch plane stays put."""
    ang = float(angle_deg)
    if abs(ang) < 1e-9:
        return body
    d = np.asarray(direction, float)
    d /= np.linalg.norm(d)
    sides = []
    for f in K.faces(body):
        info = K.face_info(f)
        n = info.get("n")
        if n is not None and abs(float(np.dot(n, d))) > 1 - 1e-6:
            continue                    # a cap
        sides.append(f)
    if not sides:
        return body
    neutral = K.Plane(np.asarray(profile_plane_origin, float), K._any_perp(d), d)
    return K.draft(body, sides, d, ang, neutral)


# ---------------------------------------------------------------------- result
def apply(state, body, p, fid):
    """Put a body feature's result into the model: surfaces become new surface bodies; solids merge by p['op']."""
    from . import features as F
    if creation(p) == "surface":
        if body is None or not K.faces(body):
            raise K.KernelError("Feature produced no surface")
        shells = _surface_pieces(body)
        if p.get("surface_op", "new") == "add":
            shells = _sew_into_surfaces(state, shells)
        for s in shells:
            state.new_part(s, fid)
        return
    F.apply_body(state, body, p.get("op", "add"), fid, p.get("scope"))


def _sew(shapes, tol=1e-4):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    sew = BRepBuilderAPI_Sewing(tol)
    for sh in shapes:
        for f in K.faces(sh):
            sew.Add(f)
    sew.Perform()
    return sew.SewedShape()


def _shells(shape):
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    out, exp = [], TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def _sew_into_surfaces(state, pieces):
    """Surface 'Add': each new sheet is sewn into the surface bodies whose edges it meets (Onshape's Add for
    surfaces). Returns the sheets that met nothing — they become new surface bodies."""
    from .features import Part
    left = []
    for piece in pieces:
        lo, hi = K.bbox(piece)
        hit = []
        for part in list(state.parts.values()):
            if K.solids(part.shape) or not K.faces(part.shape):
                continue
            a, b = K.bbox(part.shape)
            if (a > hi + 1e-3).any() or (b < lo - 1e-3).any():
                continue
            if len(_shells(_sew([part.shape, piece]))) == 1:
                hit.append(part)
        if not hit:
            left.append(piece)
            continue
        sewn = _sew([h.shape for h in hit] + [piece])
        sh = _shells(sewn)
        keep = hit[0]
        state.parts[keep.id] = Part(keep.id, keep.name, sh[0] if len(sh) == 1 else sewn, keep.color)
        for h in hit[1:]:
            state.parts.pop(h.id, None)
    return left


def _edge_key(e, q=1e-5):
    """A position key for an edge (its ends and middle), so the same edge of two separate faces matches."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    ad = BRepAdaptor_Curve(e)
    pts = [ad.Value(t) for t in (ad.FirstParameter(), ad.LastParameter())]
    m = ad.Value((ad.FirstParameter() + ad.LastParameter()) / 2)
    ends = sorted(tuple(round(c / q) for c in (x.X(), x.Y(), x.Z())) for x in pts)
    return tuple(ends) + (tuple(round(c / q) for c in (m.X(), m.Y(), m.Z())),)


def _surface_pieces(body):
    """Each connected sheet of a surface result as its own body. Sheets that meet edge to edge — like the two
    ends of a two-sided extrude, or the runs of a chain — are sewn into one; where three or more faces meet
    along an edge (the middle of a +, a T) the sheets stay separate bodies, so a non-conformal network still
    builds (non-manifold sheets can't be one surface)."""
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
    fs = K.faces(body)
    if len(fs) <= 1:
        return [body]
    by_edge = {}
    for i, f in enumerate(fs):
        for e in K.edges(f):
            try:
                by_edge.setdefault(_edge_key(e), set()).add(i)
            except Exception:
                pass
    parent = list(range(len(fs)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for owners in by_edge.values():
        if len(owners) == 2:                           # an edge two faces share: join them
            a, b = owners
            parent[root(a)] = root(b)
    groups = {}
    for i in range(len(fs)):
        groups.setdefault(root(i), []).append(fs[i])
    out = []
    for grp in groups.values():
        if len(grp) == 1:
            out.append(grp[0])
            continue
        sew = BRepBuilderAPI_Sewing(1e-6)
        for f in grp:
            sew.Add(f)
        sew.Perform()
        sewn = sew.SewedShape()
        exp, shells = TopExp_Explorer(sewn, TopAbs_SHELL), []
        while exp.More():
            shells.append(exp.Current())
            exp.Next()
        out += shells or [sewn]
    return out
