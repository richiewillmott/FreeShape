"""Geometry kernel: thin, robust wrappers around OpenCascade (OCP).

Everything that touches B-rep geometry lives here so the rest of the app
works with plain Python/numpy data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax1, gp_Ax2, gp_Ax3, gp_Pln, gp_Circ, gp_Trsf
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform,
    BRepBuilderAPI_MakeWire,
)
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism, BRepPrimAPI_MakeRevol
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse, BRepAlgoAPI_Cut, BRepAlgoAPI_Common, BRepAlgoAPI_Splitter
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet, BRepFilletAPI_MakeChamfer
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GC import GC_MakeArcOfCircle
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Line, GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_C1
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_SOLID, TopAbs_REVERSED, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopoDS import TopoDS, TopoDS_Compound
try:
    from OCP.TopTools import TopTools_ListOfShape, TopTools_IndexedMapOfShape
except ImportError:  # OCP >= 8 exposes NCollection templates directly
    from OCP.OCP.collections import (List_TopoDS_Shape as TopTools_ListOfShape,
                                     IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as TopTools_IndexedMapOfShape)
from OCP.TopLoc import TopLoc_Location
from OCP.BRep import BRep_Builder
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.STEPControl import STEPControl_Writer, STEPControl_Reader, STEPControl_AsIs
from OCP.StlAPI import StlAPI_Writer
from OCP.IFSelect import IFSelect_RetDone
from OCP.BRepCheck import BRepCheck_Analyzer


# OCP renamed the static casts between releases (TopoDS.Face_s -> TopoDS.Face); support both.
def _caster(name):
    fn = getattr(TopoDS, name + "_s", None) or getattr(TopoDS, name)
    return fn


_as_face = _caster("Face")
_as_edge = _caster("Edge")
_as_solid = _caster("Solid")
_as_vertex = _caster("Vertex")


class KernelError(Exception):
    """A geometry operation failed (shown to the user as a red feature)."""


# --------------------------------------------------------------------------- planes

@dataclass
class Plane:
    """A sketch / datum plane: origin plus orthonormal x, y, normal axes."""
    origin: np.ndarray
    xdir: np.ndarray
    normal: np.ndarray

    @property
    def ydir(self) -> np.ndarray:
        return np.cross(self.normal, self.xdir)

    def to_world(self, u, v) -> np.ndarray:
        return self.origin + u * self.xdir + v * self.ydir

    def to_local(self, p) -> tuple[float, float]:
        d = np.asarray(p, float) - self.origin
        return float(d @ self.xdir), float(d @ self.ydir)

    def gp_ax3(self) -> gp_Ax3:
        return gp_Ax3(_pnt(self.origin), _dir(self.normal), _dir(self.xdir))

    def offset(self, dist: float, flip: bool = False) -> "Plane":
        n = -self.normal if flip else self.normal
        xd = self.xdir if not flip else self.xdir
        return Plane(self.origin + dist * self.normal, xd.copy(), n.copy())

    def to_dict(self):
        return {"o": self.origin.tolist(), "x": self.xdir.tolist(), "n": self.normal.tolist()}

    @staticmethod
    def from_dict(d):
        return Plane(np.array(d["o"], float), np.array(d["x"], float), np.array(d["n"], float))


def std_plane(name: str) -> Plane:
    """Onshape's default planes. Top = XY, Front = XZ, Right = YZ (Z up)."""
    o = np.zeros(3)
    if name == "Top":
        return Plane(o, np.array([1.0, 0, 0]), np.array([0, 0, 1.0]))
    if name == "Front":
        return Plane(o, np.array([1.0, 0, 0]), np.array([0, -1.0, 0]))
    if name == "Right":
        return Plane(o, np.array([0, 1.0, 0]), np.array([1.0, 0, 0]))
    raise KeyError(name)


def _pnt(p) -> gp_Pnt:
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _dir(v) -> gp_Dir:
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


def _vec(v) -> gp_Vec:
    return gp_Vec(float(v[0]), float(v[1]), float(v[2]))


def _np(p) -> np.ndarray:
    return np.array([p.X(), p.Y(), p.Z()])


# --------------------------------------------------------------------------- topology helpers

def faces(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, m)
    return [_as_face(m.FindKey(i)) for i in range(1, m.Size() + 1)]


def edges(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    return [_as_edge(m.FindKey(i)) for i in range(1, m.Size() + 1)]


def solids(shape):
    out = []
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        out.append(_as_solid(exp.Current()))
        exp.Next()
    return out


def compound(shapes):
    b = BRep_Builder()
    c = TopoDS_Compound()
    b.MakeCompound(c)
    for s in shapes:
        b.Add(c, s)
    return c


def _list(shapes) -> TopTools_ListOfShape:
    lst = TopTools_ListOfShape()
    for s in shapes:
        lst.Append(s)
    return lst


def bbox(shape):
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b, False)
    if b.IsVoid():
        return np.zeros(3), np.zeros(3)
    return _np(b.CornerMin()), _np(b.CornerMax())


def is_valid(shape) -> bool:
    try:
        return BRepCheck_Analyzer(shape).IsValid()
    except Exception:
        return False


def clean(shape):
    """Merge coplanar faces / collinear edges left behind by booleans."""
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        u.Build()
        return u.Shape()
    except Exception:
        return shape


# --------------------------------------------------------------------------- measurements / signatures

_VOLUME_CACHE = {}


def _has_freeform(shape) -> bool:
    from OCP.GeomAbs import GeomAbs_BSplineSurface, GeomAbs_BezierSurface, GeomAbs_SurfaceOfExtrusion
    for f in faces(shape):
        if BRepAdaptor_Surface(f).GetType() in (GeomAbs_BSplineSurface, GeomAbs_BezierSurface):
            return True
    return False


def volume(shape) -> float:
    """Exact volume. Uses the adaptive (Gauss-Kronrod) integrator, which is right on swept and ruled
    surfaces such as threads where the quick method can be badly wrong. Free-form (e.g. thread)
    surfaces use a relative accuracy of 1e-4 — still 4+ significant figures — because the full
    accuracy is slow there. Results are cached per shape."""
    key = hash(shape)
    hit = _VOLUME_CACHE.get(key)
    if hit is not None and hit[0].IsSame(shape) and hit[0].IsEqual(shape):
        return hit[1]
    g = GProp_GProps()
    BRepGProp.VolumePropertiesGK_s(shape, g, 1e-4 if _has_freeform(shape) else 1e-6)
    v = g.Mass()
    if len(_VOLUME_CACHE) > 2000:
        _VOLUME_CACHE.clear()
    _VOLUME_CACHE[key] = (shape, v)
    return v


def rough_volume(shape) -> float:
    """Volume to about 1% — fast even on thread surfaces. For sanity checks only."""
    g = GProp_GProps()
    BRepGProp.VolumePropertiesGK_s(shape, g, 0.1)
    return g.Mass()


def mass_props(shape):
    g = GProp_GProps()
    # CGFlag: the adaptive integrator only computes the centre of mass when asked to
    BRepGProp.VolumePropertiesGK_s(shape, g, 1e-4 if _has_freeform(shape) else 1e-6, False, False, True)
    sa = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, sa)
    return {"volume": g.Mass(), "area": sa.Mass(), "centroid": _np(g.CentreOfMass())}


def face_info(face) -> dict:
    """A geometric signature of a face, used to re-find it after a rebuild."""
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    ad = BRepAdaptor_Surface(face)
    st = ad.GetType()
    info = {"kind": "face", "area": g.Mass(), "c": _np(g.CentreOfMass()).tolist(), "type": int(st)}
    if st == GeomAbs_Plane:
        pl = ad.Plane()
        n = _np(pl.Axis().Direction())
        if face.Orientation() == TopAbs_REVERSED:
            n = -n
        info["n"] = n.tolist()
    elif st == GeomAbs_Cylinder:
        cy = ad.Cylinder()
        info["r"] = cy.Radius()
        info["axis"] = _np(cy.Axis().Direction()).tolist()
        info["axis_p"] = _np(cy.Axis().Location()).tolist()
    return info


def face_loops(face) -> int:
    from OCP.TopAbs import TopAbs_WIRE
    n = 0
    exp = TopExp_Explorer(face, TopAbs_WIRE)
    while exp.More():
        n += 1
        exp.Next()
    return n


def edge_info(edge) -> dict:
    g = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, g)
    ad = BRepAdaptor_Curve(edge)
    ct = ad.GetType()
    info = {"kind": "edge", "len": g.Mass(), "c": _np(g.CentreOfMass()).tolist(), "type": int(ct)}
    if ct == GeomAbs_Line:
        info["d"] = _np(ad.Line().Direction()).tolist()
        info["p0"] = _np(ad.Value(ad.FirstParameter())).tolist()
        info["p1"] = _np(ad.Value(ad.LastParameter())).tolist()
    elif ct == GeomAbs_Circle:
        ci = ad.Circle()
        info["r"] = ci.Radius()
        info["center"] = _np(ci.Location()).tolist()
        info["axis"] = _np(ci.Axis().Direction()).tolist()
    return info


def planar_face_plane(face) -> Plane | None:
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Plane:
        return None
    pl = ad.Plane()
    pos = pl.Position()
    n = _np(pos.Direction())
    x = _np(pos.XDirection())
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    c = _np(g.CentreOfMass())
    # Sketch frame sits on the face centroid; pick an x-axis aligned with the world if possible
    for cand in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        proj = cand - (cand @ n) * n
        if np.linalg.norm(proj) > 0.3:
            x = proj / np.linalg.norm(proj)
            break
    origin = c - ((c - _np(pos.Location())) @ n) * n
    return Plane(origin, x, n)


def edge_line(edge):
    """(point, direction) if the edge is straight, else None."""
    ad = BRepAdaptor_Curve(edge)
    if ad.GetType() != GeomAbs_Line:
        return None
    ln = ad.Line()
    return _np(ln.Location()), _np(ln.Direction())


# --------------------------------------------------------------------------- sketch → regions

def make_sketch_edges(plane: Plane, curves) -> list:
    """curves: list of dicts {'type': 'line'|'circle'|'arc', ...} in sketch (u, v) coordinates."""
    out = []
    ax3 = plane.gp_ax3()
    for c in curves:
        try:
            if c["type"] == "line":
                a = plane.to_world(*c["p1"])
                b = plane.to_world(*c["p2"])
                if np.linalg.norm(a - b) < 1e-7:
                    continue
                out.append(BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge())
            elif c["type"] == "circle":
                if c["r"] < 1e-7:
                    continue
                ax2 = gp_Ax2(_pnt(plane.to_world(*c["c"])), ax3.Direction(), ax3.XDirection())
                out.append(BRepBuilderAPI_MakeEdge(gp_Circ(ax2, float(c["r"]))).Edge())
            elif c["type"] == "arc":
                p1 = plane.to_world(*c["p1"])
                pm = plane.to_world(*c["pm"])
                p2 = plane.to_world(*c["p2"])
                if min(np.linalg.norm(p1 - pm), np.linalg.norm(p2 - pm), np.linalg.norm(p1 - p2)) < 1e-7:
                    continue
                arc = GC_MakeArcOfCircle(_pnt(p1), _pnt(pm), _pnt(p2)).Value()
                out.append(BRepBuilderAPI_MakeEdge(arc).Edge())
        except Exception:
            continue
    return out


def sketch_regions(plane: Plane, curves) -> list:
    """Split the sketch plane by all sketch curves and return every bounded region as a face.

    This mirrors Onshape: overlapping profiles give separate, individually selectable regions.
    """
    edges_ = make_sketch_edges(plane, curves)
    if not edges_:
        return []
    lo, hi = bbox(compound(edges_))
    pts = [plane.to_local(p) for p in (lo, hi, [lo[0], hi[1], lo[2]], [hi[0], lo[1], hi[2]],
                                       [lo[0], lo[1], hi[2]], [hi[0], hi[1], lo[2]])]
    us = [p[0] for p in pts]
    vs = [p[1] for p in pts]
    margin = max(max(us) - min(us), max(vs) - min(vs), 1.0) * 0.5 + 10
    u0, u1, v0, v1 = min(us) - margin, max(us) + margin, min(vs) - margin, max(vs) + margin
    big = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0, u1, v0, v1).Face()
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([big]))
    sp.SetTools(_list(edges_))
    sp.SetFuzzyValue(1e-6)
    sp.Build()
    if not sp.IsDone():
        return []
    regions = []
    tol = 1e-4 * margin
    for f in faces(sp.Shape()):
        flo, fhi = bbox(f)
        corners = [plane.to_local(p) for p in (flo, fhi)]
        fu = [c[0] for c in corners]
        fv = [c[1] for c in corners]
        # also project the other bbox corners: plane may be skewed in world space
        if (min(fu) <= u0 + tol or max(fu) >= u1 - tol or min(fv) <= v0 + tol or max(fv) >= v1 - tol):
            # verify against actual vertices to be safe
            touches = False
            for v in _vertices_np(f):
                u, w = plane.to_local(v)
                if abs(u - u0) < tol or abs(u - u1) < tol or abs(w - v0) < tol or abs(w - v1) < tol:
                    touches = True
                    break
            if touches:
                continue
        regions.append(f)
    return regions


def _vertices_np(shape):
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, m)
    return [_np(BRep_Tool.Pnt_s(_as_vertex(m.FindKey(i)))) for i in range(1, m.Size() + 1)]


# --------------------------------------------------------------------------- solid features

def extrude(profile_faces, direction: np.ndarray, depth: float, symmetric=False, depth2: float = 0.0):
    if not profile_faces:
        raise KernelError("Select sketch regions or a planar face to extrude")
    if abs(depth) < 1e-9 and abs(depth2) < 1e-9:
        raise KernelError("Depth must be non-zero")
    d = np.asarray(direction, float)
    d = d / np.linalg.norm(d)
    results = []
    for f in profile_faces:
        start = f
        if symmetric:
            total = depth
            start = translate(f, -d * total / 2)
            vec = d * total
        elif depth2:
            start = translate(f, -d * depth2)
            vec = d * (depth + depth2)
        else:
            vec = d * depth
        mk = BRepPrimAPI_MakePrism(start, _vec(vec))
        mk.Build()
        if not mk.IsDone():
            raise KernelError("Extrude failed")
        results.append(mk.Shape())
    return fuse_all(results)


def revolve(profile_faces, axis_p, axis_d, angle_deg: float, symmetric=False):
    if not profile_faces:
        raise KernelError("Select sketch regions to revolve")
    ax = gp_Ax1(_pnt(axis_p), _dir(axis_d))
    out = []
    ang = math.radians(angle_deg)
    for f in profile_faces:
        start = f
        if symmetric and angle_deg < 360:
            start = rotate(f, axis_p, axis_d, -angle_deg / 2)
        if abs(angle_deg) >= 360 - 1e-9:
            mk = BRepPrimAPI_MakeRevol(start, ax)
        else:
            mk = BRepPrimAPI_MakeRevol(start, ax, ang)
        mk.Build()
        if not mk.IsDone():
            raise KernelError("Revolve failed (does the profile cross the axis?)")
        out.append(mk.Shape())
    return fuse_all(out)


def translate(shape, v):
    t = gp_Trsf()
    t.SetTranslation(_vec(v))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def rotate(shape, p, d, angle_deg):
    t = gp_Trsf()
    t.SetRotation(gp_Ax1(_pnt(p), _dir(d)), math.radians(angle_deg))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def mirror(shape, plane: Plane):
    t = gp_Trsf()
    t.SetMirror(gp_Ax2(_pnt(plane.origin), _dir(plane.normal)))
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def _solid_volume(shape):
    try:
        return rough_volume(shape) if solids(shape) else None
    except Exception:
        return None


def _plausible(cls, a, b, res):
    """Rough volume sanity check on a boolean result (catches gross failures such as a cut that
    returned nothing or swelled the part)."""
    va, vb = _solid_volume(a), _solid_volume(b)
    if va is None or vb is None:
        return True                      # not solids: nothing to check against
    vr = _solid_volume(res)
    if vr is None:
        vr = 0.0                         # an empty result is only right if the volume really vanished
    tol = 0.03 * (va + vb) + 1e-9
    if cls is BRepAlgoAPI_Cut:
        return va - vb - tol <= vr <= va + tol
    if cls is BRepAlgoAPI_Fuse:
        return max(va, vb) - tol <= vr <= va + vb + tol
    return vr <= min(va, vb) + tol


def _repair(shape):
    """OpenCascade's shape healer; returns the fixed shape only if it is now valid."""
    from OCP.ShapeFix import ShapeFix_Shape
    try:
        fx = ShapeFix_Shape(shape)
        fx.SetPrecision(1e-7)
        fx.SetMaxTolerance(1e-4)
        fx.Perform()
        out = fx.Shape()
        return out if is_valid(out) else None
    except Exception:
        return None


def _run_boolean(cls, a, b, fuzzy, preset):
    if preset:                     # settings applied before the operation runs
        op = cls()
        op.SetArguments(_list([a]))
        op.SetTools(_list([b]))
        op.SetFuzzyValue(fuzzy)
        op.Build()
    else:                          # the original one-step form (runs with its own defaults)
        op = cls(a, b)
        op.SetFuzzyValue(fuzzy)
        op.Build()
    return op.Shape() if op.IsDone() else None


def _boolean(cls, a, b, check=False, single=False, volume_check=False):
    """check=True: careful mode for delicate geometry such as threads — the result must be a valid
    shape (OpenCascade's healer is tried on tolerance blemishes) and not empty, and exact tolerances
    are tried first. single=True: the result must be one solid (the pieces were meant to merge).
    volume_check=True: also a rough volume sanity check."""
    if not check:
        res = _run_boolean(cls, a, b, 1e-6, False)
        if res is None:
            raise KernelError("Boolean operation failed")
        return clean(res)
    want_solid = bool(solids(a)) and cls is not BRepAlgoAPI_Common
    for fuzzy, preset in ((0.0, True), (1e-7, True), (1e-5, True), (1e-6, False)):
        r = _run_boolean(cls, a, b, fuzzy, preset)
        if r is None:
            continue
        if not is_valid(r):
            r = _repair(r)               # usually just edge tolerances at the new cut edges
            if r is None:
                continue
        n = len(solids(r))
        if (want_solid and n == 0) or (single and n != 1):
            continue
        if volume_check and not _plausible(cls, a, b, r):
            continue
        return r
    raise KernelError("Boolean operation failed")


def fuse(a, b, check=False, single=False, volume_check=False):
    return _boolean(BRepAlgoAPI_Fuse, a, b, check, single, volume_check)


def cut(a, b, check=False, volume_check=False):
    return _boolean(BRepAlgoAPI_Cut, a, b, check, False, volume_check)


def common(a, b, check=False, volume_check=False):
    return _boolean(BRepAlgoAPI_Common, a, b, check, False, volume_check)


def fuse_all(shapes):
    shapes = [s for s in shapes if s is not None]
    if not shapes:
        return None
    acc = shapes[0]
    for s in shapes[1:]:
        acc = fuse(acc, s)
    return acc


def intersects(a, b) -> bool:
    la, ha = bbox(a)
    lb, hb = bbox(b)
    if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
        return False
    try:
        c = common(a, b)
        return volume(c) > 1e-9
    except KernelError:
        return False


def touches_or_intersects(a, b) -> bool:
    la, ha = bbox(a)
    lb, hb = bbox(b)
    if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
        return False
    try:
        f = fuse(a, b)
        return len(solids(f)) < len(solids(a)) + len(solids(b))
    except KernelError:
        return False


def _blend_ok(before, after):
    """A fillet / chamfer result we can trust: a valid solid with no more pieces than before. OpenCascade sometimes
    'succeeds' with a too-big radius and returns overlapping, self-intersecting rounds instead of failing."""
    try:
        if after is None or after.IsNull() or not is_valid(after):
            return False
        if len(solids(after)) > max(1, len(solids(before))):
            return False
        v = volume(after)
        return v > 1e-9 and abs(v - volume(before)) < volume(before)
    except Exception:
        return False


def _try_blend(make, shape, edge_list, size):
    mk = make(shape)
    for e in edge_list:
        mk.Add(float(size), e)
    try:
        mk.Build()
    except Exception:
        return None
    if not mk.IsDone():
        return None
    out = mk.Shape()
    return out if _blend_ok(shape, out) else None


def _largest_blend(make, shape, edge_list, size):
    """The largest size below `size` that works (binary search), or None — for a helpful error message."""
    if len(edge_list) > 24:
        return None
    lo, hi = 0.0, float(size)
    for _ in range(9):
        mid = (lo + hi) / 2
        if _try_blend(make, shape, edge_list, mid) is not None:
            lo = mid
        else:
            hi = mid
    return lo if lo > 1e-6 else None


def _nice_down(x):
    """Round down to a tidy number for the message (e.g. 39.06 -> 39, 2.47 -> 2.4)."""
    step = 1.0 if x >= 10 else 0.1 if x >= 1 else 0.01
    return math.floor(x / step) * step


def fillet(shape, edge_list, radius: float):
    if not edge_list:
        raise KernelError("Select edges to fillet")
    if radius <= 0:
        raise KernelError("Radius must be positive")
    out = _try_blend(BRepFilletAPI_MakeFillet, shape, edge_list, radius)
    if out is None:
        best = _largest_blend(BRepFilletAPI_MakeFillet, shape, edge_list, radius)
        hint = f" — the largest that works here is about {_nice_down(best):g} mm" if best else ""
        raise KernelError(f"Fillet radius too large for these edges (the rounds would overlap or not fit){hint}")
    return out


def chamfer(shape, edge_list, dist: float):
    if not edge_list:
        raise KernelError("Select edges to chamfer")
    if dist <= 0:
        raise KernelError("Distance must be positive")
    out = _try_blend(BRepFilletAPI_MakeChamfer, shape, edge_list, dist)
    if out is None:
        best = _largest_blend(BRepFilletAPI_MakeChamfer, shape, edge_list, dist)
        hint = f" — the largest that works here is about {_nice_down(best):g} mm" if best else ""
        raise KernelError(f"Chamfer too large for these edges (the cuts would overlap or not fit){hint}")
    return out


def shell(shape, remove_faces, thickness: float, outward=False):
    if thickness <= 0:
        raise KernelError("Thickness must be positive")
    mk = BRepOffsetAPI_MakeThickSolid()
    off = thickness if outward else -thickness
    try:
        mk.MakeThickSolidByJoin(shape, _list(remove_faces), off, 1e-4)
        mk.Build()
    except Exception as exc:
        raise KernelError("Shell failed — try a smaller thickness") from exc
    if not mk.IsDone():
        raise KernelError("Shell failed — try a smaller thickness")
    return mk.Shape()


# --------------------------------------------------------------------------- tessellation

@dataclass
class Mesh:
    verts: np.ndarray        # (n,3) float32
    normals: np.ndarray      # (n,3) float32
    tris: np.ndarray         # (m,3) uint32
    tri_face: np.ndarray     # (m,) face index for each triangle
    face_ranges: list = field(default_factory=list)   # per face (tri_start, tri_end)
    edge_polys: list = field(default_factory=list)    # per edge (k,3) polyline


def tessellate(shape, lin_defl: float | None = None, ang_defl: float = 0.3) -> Mesh:
    if lin_defl is None:
        lo, hi = bbox(shape)
        lin_defl = max(np.linalg.norm(hi - lo) * 0.001, 1e-3)
    BRepMesh_IncrementalMesh(shape, lin_defl, False, ang_defl, True)
    V, N, T, TF, ranges = [], [], [], [], []
    base = 0
    for fi, f in enumerate(faces(shape)):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(f, loc)
        start = sum(len(t) for t in T)
        if tri is None:
            ranges.append((start, start))
            continue
        trsf = loc.Transformation()
        nb = tri.NbNodes()
        pts = np.empty((nb, 3))
        for i in range(1, nb + 1):
            p = tri.Node(i).Transformed(trsf)
            pts[i - 1] = (p.X(), p.Y(), p.Z())
        nt = tri.NbTriangles()
        idx = np.empty((nt, 3), dtype=np.int64)
        rev = f.Orientation() == TopAbs_REVERSED
        for i in range(1, nt + 1):
            a, b, c = tri.Triangle(i).Get()
            idx[i - 1] = (a - 1, c - 1, b - 1) if rev else (a - 1, b - 1, c - 1)
        # vertex normals from the surface if available, else from triangles
        nrm = np.zeros((nb, 3))
        if tri.HasNormals():
            for i in range(1, nb + 1):
                d = tri.Normal(i)
                nrm[i - 1] = (d.X(), d.Y(), d.Z())
            if not loc.IsIdentity():
                R = np.array([[trsf.Value(r, c) for c in range(1, 4)] for r in range(1, 4)])
                nrm = nrm @ R.T
            if rev:
                nrm = -nrm
        else:
            fn = np.cross(pts[idx[:, 1]] - pts[idx[:, 0]], pts[idx[:, 2]] - pts[idx[:, 0]])
            for k in range(3):
                np.add.at(nrm, idx[:, k], fn)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln == 0] = 1
        nrm /= ln
        V.append(pts)
        N.append(nrm)
        T.append(idx + base)
        TF.append(np.full(nt, fi, dtype=np.int32))
        ranges.append((start, start + nt))
        base += nb
    seams = seam_edge_indices(shape)
    polys = []
    for i, e in enumerate(edges(shape)):
        # seam edges (where a cylinder closes on itself) aren't shown or pickable, as in Onshape
        polys.append(np.zeros((0, 3), np.float32) if i in seams else discretize_edge(e, lin_defl))
    if V:
        mesh = Mesh(np.vstack(V).astype(np.float32), np.vstack(N).astype(np.float32),
                    np.vstack(T).astype(np.uint32), np.concatenate(TF), ranges, polys)
    else:
        mesh = Mesh(np.zeros((0, 3), np.float32), np.zeros((0, 3), np.float32),
                    np.zeros((0, 3), np.uint32), np.zeros(0, np.int32), ranges, polys)
    return mesh


def seam_edge_indices(shape) -> set:
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    out = set()
    for f in faces(shape):
        exp = TopExp_Explorer(f, TopAbs_EDGE)
        while exp.More():
            e = _as_edge(exp.Current())
            try:
                if BRep_Tool.IsClosed_s(e, f):
                    out.add(m.FindIndex(e) - 1)
            except Exception:
                pass
            exp.Next()
    return out


def discretize_edge(e, defl=0.01) -> np.ndarray:
    try:
        ad = BRepAdaptor_Curve(e)
        if ad.GetType() == GeomAbs_Line:
            return np.array([_np(ad.Value(ad.FirstParameter())), _np(ad.Value(ad.LastParameter()))], np.float32)
        d = GCPnts_QuasiUniformDeflection(ad, defl)
        if d.IsDone() and d.NbPoints() >= 2:
            return np.array([_np(d.Value(i)) for i in range(1, d.NbPoints() + 1)], np.float32)
        a, b = ad.FirstParameter(), ad.LastParameter()
        return np.array([_np(ad.Value(a + (b - a) * t)) for t in np.linspace(0, 1, 32)], np.float32)
    except Exception:
        return np.zeros((0, 3), np.float32)


# --------------------------------------------------------------------------- import / export

def export_step(shapes, path):
    w = STEPControl_Writer()
    for s in shapes:
        w.Transfer(s, STEPControl_AsIs)
    if w.Write(str(path)) != IFSelect_RetDone:
        raise KernelError("STEP export failed")


def export_stl(shapes, path, defl=0.05):
    shp = compound(shapes) if len(shapes) != 1 else shapes[0]
    BRepMesh_IncrementalMesh(shp, defl, False, 0.3, True)
    w = StlAPI_Writer()
    if not w.Write(shp, str(path)):
        raise KernelError("STL export failed")


_STEP_CACHE: dict = {}      # sha1 of a STEP file's bytes -> import result (filled by the background reader too)


def step_cache_key(data: bytes) -> str:
    import hashlib
    return hashlib.sha1(data).hexdigest()


def remember_step(key, result):
    if len(_STEP_CACHE) > 6:
        _STEP_CACHE.pop(next(iter(_STEP_CACHE)))
    _STEP_CACHE[key] = result


def import_step_with_colors(path, progress=None):
    """Read a STEP file keeping part names and colours.
    Returns a list of (solid, name or None, (r, g, b) or None, {face index: (r, g, b)}).
    progress(stage_text) is called between the slow stages, if given."""
    with open(path, "rb") as fh:
        key = step_cache_key(fh.read())
    hit = _STEP_CACHE.get(key)
    if hit is not None:
        return list(hit)
    out = _import_step_with_colors(path, progress)
    remember_step(key, out)
    return list(out)


def _import_step_with_colors(path, progress=None):
    progress = progress or (lambda _t: None)
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.XCAFDoc import (XCAFDoc_DocumentTool, XCAFDoc_ColorTool, XCAFDoc_ShapeTool,
                             XCAFDoc_ColorGen, XCAFDoc_ColorSurf)
    from OCP.TDF import TDF_Label
    from OCP.TDataStd import TDataStd_Name
    from OCP.Quantity import Quantity_Color
    from OCP.TopLoc import TopLoc_Location
    try:
        from OCP.TDF import TDF_LabelSequence
    except ImportError:
        from OCP.OCP.collections import Sequence_TDF_Label as TDF_LabelSequence

    doc = TDocStd_Document(TCollection_ExtendedString("freeshape"))
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    progress("Reading the STEP file")
    if reader.ReadFile(str(path)) != IFSelect_RetDone:
        raise KernelError(f"Could not read STEP file {path}")
    progress("Converting it into solids")
    if not reader.Transfer(doc):
        raise KernelError("STEP transfer failed")
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    def static(cls, name):
        return getattr(cls, name + "_s", None) or getattr(cls, name)

    is_ref = static(XCAFDoc_ShapeTool, "IsReference")
    is_asm = static(XCAFDoc_ShapeTool, "IsAssembly")
    get_ref = static(XCAFDoc_ShapeTool, "GetReferredShape")
    get_comps = static(XCAFDoc_ShapeTool, "GetComponents")
    get_shape = static(XCAFDoc_ShapeTool, "GetShape")
    get_loc = static(XCAFDoc_ShapeTool, "GetLocation")
    get_subs = static(XCAFDoc_ShapeTool, "GetSubShapes")
    label_color = static(XCAFDoc_ColorTool, "GetColor")

    def to_srgb(x):
        # OpenCascade keeps colours linear; the screen (and the original CAD app) shows sRGB
        x = min(max(float(x), 0.0), 1.0)
        return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055

    def color_of(label):
        for t in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
            c = Quantity_Color()
            try:
                if label_color(label, t, c):
                    return (to_srgb(c.Red()), to_srgb(c.Green()), to_srgb(c.Blue()))
            except Exception:
                pass
        return None

    def name_of(label):
        attr = TDataStd_Name()
        try:
            if label.FindAttribute(TDataStd_Name.GetID_s(), attr):
                n = attr.Get().ToExtString().strip()
                # exporters often write placeholders like "NONE" or "SOLID" — treat those as no name
                if not n or n.upper() in ("NONE", "UNNAMED", "NO NAME", "SOLID", "BODY", "SHAPE", "COMPOUND") \
                        or n.upper().startswith(("=>[", "OPEN CASCADE")):
                    return None
                return n
        except Exception:
            return None
        return None

    out = []

    def dominant_sub_color(label, sub_shape):
        """Colour covering the most area among coloured faces/solids under a label."""
        seq = TDF_LabelSequence()
        get_subs(label, seq)
        weights = {}
        for i in range(1, seq.Length() + 1):
            sl = seq.Value(i)
            c = color_of(sl)
            if c is None:
                continue
            sh = get_shape(sl)
            for f in faces(sh):
                g = GProp_GProps()
                BRepGProp.SurfaceProperties_s(f, g)
                weights[c] = weights.get(c, 0.0) + g.Mass()
        return max(weights, key=weights.get) if weights else None

    def walk(label, loc, color, name, depth=0):
        if depth > 64:
            return
        if is_ref(label):
            ref = TDF_Label()
            get_ref(label, ref)
            l2 = loc.Multiplied(get_loc(label))
            walk(ref, l2, color_of(label) or color, name_of(label) or name, depth + 1)
            return
        own_name = name_of(label) or name
        own_color = color_of(label) or color
        if is_asm(label):
            comps = TDF_LabelSequence()
            get_comps(label, comps, False)
            for i in range(1, comps.Length() + 1):
                walk(comps.Value(i), loc, own_color, own_name, depth + 1)
            return
        shape = get_shape(label)
        if shape.IsNull():
            return
        sols = solids(shape) or [shape]
        sub_color = None if own_color else dominant_sub_color(label, shape)
        for k, sol in enumerate(sols):
            c = own_color
            if c is None:
                sl = TDF_Label()
                try:
                    if st.FindSubShape(label, sol, sl):
                        c = color_of(sl)
                except Exception:
                    pass
            nm = own_name if len(sols) == 1 or not own_name else f"{own_name} ({k + 1})"
            # per-face colours (only kept when they differ from the part's main colour)
            fcols = {}
            sol_faces = faces(sol)
            raw = {}
            for fi, fc in enumerate(sol_faces):
                col = None
                for t in (XCAFDoc_ColorSurf, XCAFDoc_ColorGen):
                    q = Quantity_Color()
                    try:
                        if ct.GetColor(fc, t, q):
                            col = (to_srgb(q.Red()), to_srgb(q.Green()), to_srgb(q.Blue()))
                            break
                    except Exception:
                        pass
                if col is not None:
                    raw[fi] = col
            if c is None and raw and len(raw) == len(sol_faces) and len(set(raw.values())) == 1:
                c = next(iter(raw.values()))          # every face the same colour: that's the part colour
            elif c is None and not raw:
                c = sub_color
            # colour only on some faces: the rest keep the default grey, like in the original CAD app
            for fi, col in raw.items():
                if c is None or max(abs(a - b) for a, b in zip(col, c)) > 0.02:
                    fcols[fi] = col
            out.append((sol.Moved(loc) if not loc.IsIdentity() else sol, nm, c, fcols))

    progress("Collecting parts, names and colours")
    free = TDF_LabelSequence()
    st.GetFreeShapes(free)
    for i in range(1, free.Length() + 1):
        walk(free.Value(i), TopLoc_Location(), None, None)
    if not out:
        raise KernelError("No solids found in STEP file")
    return out


def import_step(path):
    r = STEPControl_Reader()
    if r.ReadFile(str(path)) != IFSelect_RetDone:
        raise KernelError(f"Could not read STEP file {path}")
    r.TransferRoots()
    shp = r.OneShape()
    sols = solids(shp)
    return sols if sols else [shp]


# --------------------------------------------------------------------------- sweep / loft / draft / split / move

def make_wire(edge_list):
    """Join edges into one wire, ordering them end-to-end (they may be picked in any order)."""
    if not edge_list:
        raise KernelError("Select a path")
    def ends(e):
        p = discretize_edge(e)
        return p[0].astype(float), p[-1].astype(float)
    remaining = list(edge_list)
    chain = [remaining.pop(0)]
    a, b = ends(chain[0])
    head, tail = a, b
    tol = max(1e-4, 1e-6 * float(np.linalg.norm(b - a)))
    changed = True
    while remaining and changed:
        changed = False
        for e in list(remaining):
            p, q = ends(e)
            if np.linalg.norm(p - tail) < tol * 100 or np.linalg.norm(q - tail) < tol * 100:
                chain.append(e)
                tail = q if np.linalg.norm(p - tail) < np.linalg.norm(q - tail) else p
            elif np.linalg.norm(p - head) < tol * 100 or np.linalg.norm(q - head) < tol * 100:
                chain.insert(0, e)
                head = q if np.linalg.norm(p - head) < np.linalg.norm(q - head) else p
            else:
                continue
            remaining.remove(e)
            changed = True
    if remaining:
        raise KernelError("Path edges must connect end to end")
    mk = BRepBuilderAPI_MakeWire()
    for e in chain:
        mk.Add(e)
    if not mk.IsDone():
        raise KernelError("Path edges must connect end to end")
    return mk.Wire()


def _pipe_shell(path_wire, wire):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell
    from OCP.BRepBuilderAPI import BRepBuilderAPI_RightCorner
    mk = BRepOffsetAPI_MakePipeShell(path_wire)
    mk.SetMode(False)                       # corrected Frenet: keeps the profile upright along the path
    mk.SetTransitionMode(BRepBuilderAPI_RightCorner)   # clean mitred joins at sharp corners
    mk.Add(wire, False, False)
    mk.Build()
    if not mk.IsDone():
        raise KernelError("Sweep failed — the path may be too tight for the profile")
    mk.MakeSolid()
    return mk.Shape()


def sweep(profile_faces, path_wire):
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_WIRE
    if not profile_faces:
        raise KernelError("Select a profile to sweep")
    out = []
    for f in profile_faces:
        outer = BRepTools.OuterWire_s(f)
        try:
            body = _pipe_shell(path_wire, outer)
            exp = TopExp_Explorer(f, TopAbs_WIRE)
            while exp.More():                 # holes in the profile become tunnels
                w = TopoDS.Wire(exp.Current()) if hasattr(TopoDS, "Wire") else exp.Current()
                if not w.IsSame(outer):
                    body = cut(body, _pipe_shell(path_wire, w))
                exp.Next()
        except KernelError:
            raise
        except Exception as exc:
            raise KernelError("Sweep failed — the path may be too tight for the profile") from exc
        out.append(body)
    res = fuse_all(out)
    if not solids(res):
        raise KernelError("Sweep didn't make a solid — the profile should be a closed region")
    if volume(res) < 0:
        res = res.Reversed()
    return res


def loft(profile_faces, ruled=False):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    from OCP.BRepTools import BRepTools
    if len(profile_faces) < 2:
        raise KernelError("Select at least two profiles, in order")
    mk = BRepOffsetAPI_ThruSections(True, bool(ruled), 1e-6)
    for f in profile_faces:
        mk.AddWire(BRepTools.OuterWire_s(f))
    try:
        mk.Build()
    except Exception as exc:
        raise KernelError("Loft failed — try profiles with similar shapes") from exc
    if not mk.IsDone():
        raise KernelError("Loft failed — try profiles with similar shapes")
    return mk.Shape()


def draft(shape, face_list, pull_dir, angle_deg, neutral: Plane):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
    if not face_list:
        raise KernelError("Select faces to draft")
    mk = BRepOffsetAPI_DraftAngle(shape)
    pln = gp_Pln(_pnt(neutral.origin), _dir(neutral.normal))
    for f in face_list:
        mk.Add(f, _dir(pull_dir), math.radians(angle_deg), pln, True)
        if not mk.AddDone():
            raise KernelError("Can't draft one of the faces (drafting works on flat or cylindrical side faces)")
    try:
        mk.Build()
    except Exception as exc:
        raise KernelError("Draft failed — try a smaller angle") from exc
    if not mk.IsDone():
        raise KernelError("Draft failed — try a smaller angle")
    return mk.Shape()


def split_by_plane(shape, plane: Plane):
    lo, hi = bbox(shape)
    size = float(np.linalg.norm(hi - lo)) * 2 + 10
    c = plane.origin + ((lo + hi) / 2 - plane.origin) - (((lo + hi) / 2 - plane.origin) @ plane.normal) * plane.normal
    u0, v0 = plane.to_local(c)
    tool = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0 - size, u0 + size, v0 - size, v0 + size).Face()
    sp = BRepAlgoAPI_Splitter()
    sp.SetArguments(_list([shape]))
    sp.SetTools(_list([tool]))
    sp.SetFuzzyValue(1e-6)
    sp.Build()
    if not sp.IsDone():
        raise KernelError("Split failed")
    pieces = solids(sp.Shape())
    if len(pieces) < 2:
        raise KernelError("The plane doesn't cut through the part")
    return pieces


def transform(shape, translate_v=(0, 0, 0), axis_p=(0, 0, 0), axis_d=(0, 0, 1), angle_deg=0.0):
    t = gp_Trsf()
    if abs(angle_deg) > 1e-12:
        t.SetRotation(gp_Ax1(_pnt(axis_p), _dir(axis_d)), math.radians(angle_deg))
    m = gp_Trsf()
    m.SetTranslation(_vec(translate_v))
    t = m.Multiplied(t)
    return BRepBuilderAPI_Transform(shape, t, True).Shape()


def cylinder(base, direction, radius, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    ax = gp_Ax2(_pnt(base), _dir(direction))
    return BRepPrimAPI_MakeCylinder(ax, float(radius), float(height)).Shape()


def cone(base, direction, r1, r2, height):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    ax = gp_Ax2(_pnt(base), _dir(direction))
    return BRepPrimAPI_MakeCone(ax, float(r1), float(r2), float(height)).Shape()


# --------------------------------------------------------------------------- modelled threads

# ISO 261 coarse pitches
ISO_PITCH = {"M1.6": 0.35, "M2": 0.4, "M2.5": 0.45, "M3": 0.5, "M4": 0.7, "M5": 0.8, "M6": 1.0, "M8": 1.25,
             "M10": 1.5, "M12": 1.75, "M14": 2.0, "M16": 2.0, "M20": 2.5, "M24": 3.0}
THREAD_H = 0.5413   # thread depth (5H/8) as a fraction of the pitch, ISO 68-1


def _frame(origin, axis):
    """Transform from the local Z-up thread frame to world (origin, axis)."""
    t = gp_Trsf()
    t.SetTransformation(gp_Ax3(_pnt(origin), _dir(axis)), gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)))
    return t


def _uv_segment(surf, a, b):
    """An edge on `surf` that is a straight line in its (angle, height) parameters — on a
    cylinder that's an exact helix (or a straight vertical line)."""
    from OCP.Geom2d import Geom2d_Line
    from OCP.gp import gp_Pnt2d, gp_Dir2d
    from OCP.BRepLib import BRepLib
    dx, dy = b[0] - a[0], b[1] - a[1]
    L = math.hypot(dx, dy)
    e = BRepBuilderAPI_MakeEdge(Geom2d_Line(gp_Pnt2d(*a), gp_Dir2d(dx, dy)), surf, 0.0, L).Edge()
    # long helices need enough approximation segments (about 8 per turn) to stay accurate
    turns = abs(dx) / (2 * math.pi)
    BRepLib.BuildCurves3d_s(e, 1e-7, GeomAbs_C1, 14, max(30, int(math.ceil(turns * 8))))
    return e


def _tooth(r_in, r_out, w_in, w_out, pitch, z0, turns, left=False):
    """One continuous helical thread tooth about +Z, built face by face: its base and crest are exact
    cylinder faces and its flanks are ruled between exact helices. Base width w_in at r_in, crest
    width w_out at r_out.

    Every face covers at most one turn (the tooth is split at angle 0 on each turn). A single face
    that wraps the cylinder many times is legal in OpenCascade, but its boolean operations then
    produce subtly wrong results on some lengths; one turn per face keeps them exact."""
    from OCP.Geom import Geom_CylindricalSurface
    from OCP.BRepFill import BRepFill
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeSolid
    sgn = -1.0 if left else 1.0
    S_in = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r_in)
    S_out = Geom_CylindricalSurface(gp_Ax3(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), r_out)
    n = max(1, int(round(turns)))
    faces_ = []

    def band(S, w, k):
        u0, u1 = sgn * 2 * math.pi * k, sgn * 2 * math.pi * (k + 1)
        h0, h1 = z0 + k * pitch, z0 + (k + 1) * pitch
        pts = [(u0, h0 - w / 2), (u1, h1 - w / 2), (u1, h1 + w / 2), (u0, h0 + w / 2)]
        es = [_uv_segment(S, pts[i], pts[(i + 1) % 4]) for i in range(4)]
        mw = BRepBuilderAPI_MakeWire()
        for e in es:
            mw.Add(e)
        return BRepBuilderAPI_MakeFace(S, mw.Wire(), True).Face(), es

    for k in range(n):
        f_in, e_in = band(S_in, w_in, k)
        f_out, e_out = band(S_out, w_out, k)
        faces_ += [f_in, f_out, BRepFill.Face_s(e_in[0], e_out[0]), BRepFill.Face_s(e_in[2], e_out[2])]
        if k == 0:
            faces_.append(BRepFill.Face_s(e_in[3], e_out[3]))     # start wall
        if k == n - 1:
            faces_.append(BRepFill.Face_s(e_in[1], e_out[1]))     # end wall
    sew = BRepBuilderAPI_Sewing(1e-6)
    for f in faces_:
        sew.Add(f)
    sew.Perform()
    shell = _caster("Shell")(sew.SewedShape())
    sol = BRepBuilderAPI_MakeSolid(shell).Solid()
    if volume(sol) < 0:
        sol.Reverse()
    return sol


def _local_cylinder(r, z0, z1):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), float(r), float(z1 - z0)).Shape()


def _threaded_rod(rmin, rmaj, pitch, a, b, left=False):
    """An ISO threaded rod about +Z from z=a to z=b (core at rmin, crests at rmaj).
    The tooth's base sits a little inside the core (flanks extended at the ISO 30° angle) — with the
    exact-helix tooth that joins reliably and about 3× faster than a base lying exactly on the core."""
    P = float(pitch)
    if rmaj - rmin < 1e-3:
        raise KernelError("Thread is too shallow")
    if b - a <= 1e-6:
        raise KernelError("Thread length must be positive")
    eps = 0.05 * P
    w_in = 0.75 * P + 2 * eps * math.tan(math.radians(30))
    start = a - 2 * P
    turns = int(math.ceil((b + 2 * P - start) / P))
    tooth = _tooth(rmin - eps, rmaj, w_in, 0.125 * P, P, start, turns, left)
    # the core must reach well past both ends of the tooth (its end turns are about a pitch wide):
    # a tooth end poking out of the core's flat end makes the join subtly broken
    rod = fuse(_local_cylinder(rmin, start - 1.5 * P, start + turns * P + 1.5 * P), tooth, check=True, single=True)
    return common(rod, _local_cylinder(rmaj + 0.1 * P, a, b), check=True)


def threaded_plugs(origin, axis, major, pitch, z0, z1, core_r=None, extend=(True, True), left=False, clearance=0.0):
    """Solids that, cut from a part, leave an ISO internal (tapped) thread along `axis` between
    distances z0 and z1 from `origin`. `extend` says whether each end opens into air (the thread then
    runs out cleanly past it). core_r: radius of an existing hole the thread is cut into.
    clearance: extra radial room (mm), e.g. for 3D-printed threads."""
    P = float(pitch)
    rmaj = major / 2 + clearance
    rmin = max(rmaj - THREAD_H * P, core_r or 0.0)
    a = z0 - (2 * P if extend[0] else 0.0)
    b = z1 + (2 * P if extend[1] else 0.0)
    return [BRepBuilderAPI_Transform(_threaded_rod(rmin, rmaj, P, a, b, left), _frame(origin, axis), True).Shape()]


def cut_internal_thread(shape, origin, axis, major, pitch, z0, z1, **kw):
    """Tap a thread into `shape`: cut the threaded plug sections out of it, one at a time."""
    n0 = len(faces(shape))
    for plug in threaded_plugs(origin, axis, major, pitch, z0, z1, **kw):
        shape = cut(shape, plug, check=True)
    if len(faces(shape)) <= n0:
        raise KernelError("The thread didn't cut into any material here")
    return shape


def external_thread(shape, origin, axis, shaft_r, pitch, z0, z1, left=False, clearance=0.0):
    """Thread a round shaft: the plain section between z0 and z1 is replaced by an ISO threaded rod
    whose crests sit on the shaft's surface (less any clearance)."""
    P = float(pitch)
    rmaj = shaft_r - clearance
    T = _frame(origin, axis)
    sleeve = BRepBuilderAPI_Transform(_local_cylinder(shaft_r + 0.1 * P, z0, z1), T, True).Shape()
    n0 = len(faces(shape))
    rod = BRepBuilderAPI_Transform(_threaded_rod(rmaj - THREAD_H * P, rmaj, P, z0, z1, left), T, True).Shape()
    lo, hi = bbox(shape)
    slo, shi = bbox(sleeve)
    if np.all(lo >= slo - 1e-6) and np.all(hi <= shi + 1e-6):
        # the thread runs the whole length of a plain rod: nothing is left of the original, it *is* the rod
        return rod
    shape = cut(shape, sleeve, check=True)
    out = fuse(shape, rod, check=True, single=True)
    if len(faces(out)) <= n0:
        raise KernelError("Couldn't thread this shaft")
    return out


def material_at(shape, point) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN
    c = BRepClass3d_SolidClassifier(shape, _pnt(point), 1e-7)
    return c.State() == TopAbs_IN


def cylinder_face_frame(face):
    """For a cylindrical face: (axis point, unit axis, radius, z-min, z-max along the axis, is_hole)."""
    from OCP.BRepLProp import BRepLProp_SLProps
    ad = BRepAdaptor_Surface(face)
    if ad.GetType() != GeomAbs_Cylinder:
        raise KernelError("Pick a round (cylindrical) face")
    cy = ad.Cylinder()
    p0 = _np(cy.Axis().Location())
    d = _np(cy.Axis().Direction())
    r = cy.Radius()
    zs = [float((v - p0) @ d) for v in _vertices_np(face)]
    poly = [np.asarray(q, float) for e in edges(face) for q in discretize_edge(e, r * 0.01)]
    zs += [float((q - p0) @ d) for q in poly]
    u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
    v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
    props = BRepLProp_SLProps(ad, u, v, 1, 1e-7)
    n = _np(props.Normal())
    if face.Orientation() == TopAbs_REVERSED:
        n = -n
    pt = _np(props.Value())
    radial = pt - p0 - ((pt - p0) @ d) * d
    is_hole = float(n @ radial) < 0          # normal points at the axis: material is outside → a hole
    return p0, d, r, min(zs), max(zs), is_hole


# --------------------------------------------------------------------------- section view

def section_caps(shape, plane: Plane):
    """Faces where `plane` cuts `shape` (the solid caps shown in a section view).
    Material on the +normal side is removed."""
    lo, hi = bbox(shape)
    c = (lo + hi) / 2
    size = float(np.linalg.norm(hi - lo)) + 10
    d0 = float((c - plane.origin) @ plane.normal)
    if d0 - size > 0 or d0 + size < 0:
        return []                                  # plane misses the part entirely
    u0, v0 = plane.to_local(c)
    big = BRepBuilderAPI_MakeFace(gp_Pln(plane.gp_ax3()), u0 - size, u0 + size, v0 - size, v0 + size).Face()
    try:
        common_ = BRepAlgoAPI_Common(shape, big)
        common_.Build()
        if not common_.IsDone():
            return []
        return [f for f in faces(common_.Shape())]
    except Exception:
        return []


# --------------------------------------------------------------------------- assemblies: placement, storage, export

def trsf_from_matrix(M) -> gp_Trsf:
    """A 4x4 rigid transform (numpy) as an OpenCascade gp_Trsf."""
    M = np.asarray(M, float)
    t = gp_Trsf()
    t.SetValues(*(float(M[r, c]) for r in range(3) for c in range(4)))
    return t


def located(shape, M):
    """The shape placed by a 4x4 rigid transform. Shares geometry with the original (cheap)."""
    M = np.asarray(M, float)
    if np.allclose(M, np.eye(4), atol=1e-12):
        return shape
    return shape.Moved(TopLoc_Location(trsf_from_matrix(M)))


def shape_to_brep(shape) -> bytes:
    """Serialise a shape (OpenCascade's native BREP text), zlib-compressed."""
    import os, tempfile, zlib
    from OCP.BRepTools import BRepTools
    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        BRepTools.Write_s(shape, path)
        with open(path, "rb") as fh:
            return zlib.compress(fh.read(), 6)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def shape_from_brep(data: bytes):
    import os, tempfile, zlib
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape
    fd, path = tempfile.mkstemp(suffix=".brep")
    with os.fdopen(fd, "wb") as fh:
        fh.write(zlib.decompress(data))
    try:
        shp = TopoDS_Shape()
        BRepTools.Read_s(shp, path, BRep_Builder())
        if shp.IsNull():
            raise KernelError("Stored part copy is unreadable")
        return shp
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def export_step_named(items, path, assembly_name="Assembly"):
    """STEP export as a real assembly: one product per distinct part, placed once per instance, keeping
    names and colours. items: [(local shape, 4x4 transform, instance name, (r, g, b) or None)]."""
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Writer
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorGen
        from OCP.TDataStd import TDataStd_Name
        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
        doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
        st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
        ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
        top = st.NewShape()
        TDataStd_Name.Set_s(top, TCollection_ExtendedString(str(assembly_name)))
        protos = []                       # [(shape, label)] one product per distinct part
        for shape, M, name, color in items:
            lab = next((l for s, l in protos if s.IsSame(shape)), None)
            if lab is None:
                lab = st.AddShape(shape, False)
                base = str(name).rsplit(" <", 1)[0] if name else "Part"
                TDataStd_Name.Set_s(lab, TCollection_ExtendedString(base))
                if color is not None:
                    ct.SetColor(lab, Quantity_Color(float(color[0]), float(color[1]), float(color[2]),
                                                    Quantity_TOC_RGB), XCAFDoc_ColorGen)
                protos.append((shape, lab))
            comp = st.AddComponent(top, lab, TopLoc_Location(trsf_from_matrix(M)))
            if name:
                TDataStd_Name.Set_s(comp, TCollection_ExtendedString(str(name)))
        st.UpdateAssemblies()
        w = STEPCAFControl_Writer()
        w.SetNameMode(True)
        w.SetColorMode(True)
        if not w.Transfer(doc, STEPControl_AsIs):
            raise KernelError("transfer failed")
        if w.Write(str(path)) != IFSelect_RetDone:
            raise KernelError("write failed")
    except Exception:
        export_step([located(s, M) for s, M, _n, _c in items], path)


def save_step_result(result, prefix):
    """Write an import result as prefix.brep (all solids) + prefix.json (names, colours) — used to pass a STEP
    file read in a background process back to the app quickly."""
    import json as _json
    comp = compound([sol for sol, *_ in result])
    from OCP.BRepTools import BRepTools
    BRepTools.Write_s(comp, prefix + ".brep")
    meta = [{"name": nm, "color": list(c) if c else None, "faces": {str(k): list(v) for k, v in (fc or {}).items()}}
            for _s, nm, c, fc in result]
    with open(prefix + ".json", "w") as fh:
        _json.dump(meta, fh)


def load_step_result(prefix):
    import json as _json
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape, TopoDS_Iterator
    shp = TopoDS_Shape()
    BRepTools.Read_s(shp, prefix + ".brep", BRep_Builder())
    with open(prefix + ".json") as fh:
        meta = _json.load(fh)
    sols = []
    it = TopoDS_Iterator(shp)
    while it.More():
        sols.append(it.Value())
        it.Next()
    if len(sols) != len(meta):
        raise KernelError("Background STEP read returned mismatched data")
    return [(sol, m["name"], tuple(m["color"]) if m["color"] else None,
             {int(k): tuple(v) for k, v in m["faces"].items()}) for sol, m in zip(sols, meta)]


def face_edge_indices(shape, face_indices):
    """Indices (in edges(shape) order) of the edges bounding the given faces."""
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, m)
    fs = faces(shape)
    out = set()
    for fi in face_indices:
        if 0 <= fi < len(fs):
            for e in edges(fs[fi]):
                k = m.FindIndex(e)
                if k > 0:
                    out.add(k - 1)
    return out


# --------------------------------------------------------------------------- 2D drawing views (hidden-line removal)

_HLR_CACHE: dict = {}


def _hlr_curves(compound_shape, defl):
    """2D curves of an HLR result compound (it lies in the projection plane, z = 0)."""
    out = []
    if compound_shape is None or compound_shape.IsNull():
        return out
    for e in edges(compound_shape):
        try:
            ad = BRepAdaptor_Curve(e)
            ct = ad.GetType()
            a, b = ad.FirstParameter(), ad.LastParameter()
            if ct == GeomAbs_Line:
                p0, p1 = _np(ad.Value(a)), _np(ad.Value(b))
                if np.linalg.norm(p1[:2] - p0[:2]) > 1e-9:
                    out.append(("line", (float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))))
                continue
            if ct == GeomAbs_Circle:
                ci = ad.Circle()
                c = _np(ci.Location())
                r = ci.Radius()
                p0, p1 = _np(ad.Value(a)), _np(ad.Value(b))
                a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
                sweep = b - a
                if _np(ci.Axis().Direction())[2] < 0:     # runs clockwise on the paper: flip to counter-clockwise
                    a0 = math.atan2(p1[1] - c[1], p1[0] - c[0])
                out.append(("circle", (float(c[0]), float(c[1])), float(r), float(a0), float(a0 + sweep)))
                continue
            pts = discretize_edge(e, defl)
            if len(pts) == 2:
                p0, p1 = pts[0], pts[1]
                if abs(p1[0] - p0[0]) + abs(p1[1] - p0[1]) > 1e-12:
                    out.append(("line", (float(p0[0]), float(p0[1])), (float(p1[0]), float(p1[1]))))
                continue
            if len(pts) >= 2:
                xy = np.asarray(pts, float)[:, :2]
                # a curve seen edge-on (e.g. a hole's rim from the side) is just a straight line
                ctr = xy.mean(axis=0)
                _u, _s, vt = np.linalg.svd(xy - ctr)
                dd = vt[0]
                if float(np.max(np.abs((xy - ctr) @ np.array([-dd[1], dd[0]])))) < max(defl, 1e-7):
                    t = (xy - ctr) @ dd
                    a2, b2 = ctr + dd * t.min(), ctr + dd * t.max()
                    if np.linalg.norm(b2 - a2) > 1e-9:
                        out.append(("line", (float(a2[0]), float(a2[1])), (float(b2[0]), float(b2[1]))))
                    continue
                out.append(("poly", [(float(q[0]), float(q[1])) for q in xy]))
        except Exception:
            continue
    return out


def _line_param(p, q):
    """(direction angle, offset from the origin, t0, t1) of the infinite line through p, q, with t measured along
    a fixed direction of that line — so collinear segments can be compared as intervals."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    if n < 1e-15:
        return None
    dx, dy = dx / n, dy / n
    if dx < 0 or (dx == 0 and dy < 0):
        dx, dy = -dx, -dy
    t0, t1 = p[0] * dx + p[1] * dy, q[0] * dx + q[1] * dy
    return math.atan2(dy, dx), p[0] * dy - p[1] * dx, min(t0, t1), max(t0, t1)


def _drop_covered(hidden, visible, tol):
    """Hidden lines lying exactly under visible ones (e.g. a back edge right behind a front edge) are left out.
    Visible lines are grouped by the infinite line they're on and sorted along it, so finding whether a hidden
    line is covered is a binary search — fast even with tens of thousands of edges (big STEP models)."""
    import bisect
    ang_q, off_q = 1e-6, max(tol * 10, 1e-9)
    groups = {}
    for c in visible:
        if c[0] != "line":
            continue
        lp = _line_param(c[1], c[2])
        if lp is not None:
            k = (int(round(lp[0] / ang_q)), int(round(lp[1] / off_q)))
            groups.setdefault(k, []).append((lp[2], lp[3]))
    index = {}
    for k, iv in groups.items():
        iv.sort()
        starts = [a for a, _b in iv]
        best, run = [], -1e300
        for _a, b in iv:
            run = max(run, b)
            best.append(run)                 # furthest reach of any interval starting at or before this one
        index[k] = (starts, best)
    circ = {}
    for c in visible:
        if c[0] == "circle":
            circ.setdefault((round(c[1][0] / off_q), round(c[1][1] / off_q)), []).append(c[2])
    out = []
    for c in hidden:
        if c[0] == "line":
            lp = _line_param(c[1], c[2])
            if lp is not None:
                k0, k1 = int(round(lp[0] / ang_q)), int(round(lp[1] / off_q))
                covered = False
                for da in (-1, 0, 1):
                    for do in (-1, 0, 1):
                        ix = index.get((k0 + da, k1 + do))
                        if ix is None:
                            continue
                        j = bisect.bisect_right(ix[0], lp[2] + tol) - 1
                        if j >= 0 and ix[1][j] >= lp[3] - tol:
                            covered = True
                            break
                    if covered:
                        break
                if covered:
                    continue
        elif c[0] == "circle":
            kx, ky = round(c[1][0] / off_q), round(c[1][1] / off_q)
            if any(abs(r - c[2]) < tol for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                   for r in circ.get((kx + dx, ky + dy), ())):
                continue
        out.append(c)
    return out


def view_axes(d, up):
    """Right / up / towards-the-viewer unit vectors of a drawing view."""
    d = np.asarray(d, float)
    d = d / np.linalg.norm(d)
    up = np.asarray(up, float)
    up = up - d * (up @ d)
    if np.linalg.norm(up) < 1e-9:
        up = np.array([0.0, 0, 1]) if abs(d[2]) < 0.9 else np.array([0.0, 1, 0])
        up = up - d * (up @ d)
    up = up / np.linalg.norm(up)
    right = np.cross(up, d)
    return right, up, d


def _hlr_key(shape, d, up, fast=False):
    _r, upv, dv = view_axes(d, up)
    return (id(shape), tuple(np.round(dv, 9)), tuple(np.round(upv, 9)), bool(fast))


def hlr_cached(shape, d, up):
    """The exact view if it has been worked out already, else None."""
    hit = _HLR_CACHE.get(_hlr_key(shape, d, up))
    return hit[1] if hit is not None and hit[0] is shape else None


def hlr_store(shape, d, up, res, fast=False):
    if len(_HLR_CACHE) > 96:
        _HLR_CACHE.clear()
    _HLR_CACHE[_hlr_key(shape, d, up, fast)] = (shape, res)


def hlr_project(shape, d, up, defl=None, fast=False):
    """Hidden-line views for 2D drawings, like Onshape's. d = direction from the part towards the viewer.
    Returns {"visible", "hidden", "tangent"}: lists of ("line", p0, p1) / ("circle", centre, r, a0, a1)
    / ("poly", points) in the view's 2D coordinates (model units; x = right, y = up, origin = world origin).
    fast=True works from the part's mesh: near-instant, but round edges come out as short straight pieces
    (used for a first look while the exact view is worked out)."""
    from OCP.HLRAlgo import HLRAlgo_Projector
    right, upv, dv = view_axes(d, up)
    key = _hlr_key(shape, d, up, fast)
    hit = _HLR_CACHE.get(key)
    if hit is not None and hit[0] is shape:
        return hit[1]
    if fast:
        ex = hlr_cached(shape, d, up)
        if ex is not None:
            return ex
    lo, hi = bbox(shape)
    size = float(np.linalg.norm(hi - lo)) or 1.0
    defl = defl or max(size * 2e-4, 1e-3)
    ax = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*dv), gp_Dir(*right))
    if fast:
        from OCP.HLRBRep import HLRBRep_PolyAlgo, HLRBRep_PolyHLRToShape
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        BRepMesh_IncrementalMesh(shape, size * 1e-3, False, 0.15, True)
        algo = HLRBRep_PolyAlgo()
        algo.Load(shape)
        algo.Projector(HLRAlgo_Projector(ax))
        algo.Update()
        h = HLRBRep_PolyHLRToShape()
        h.Update(algo)
    else:
        from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
        algo = HLRBRep_Algo()
        algo.Add(shape)
        algo.Projector(HLRAlgo_Projector(ax))
        algo.Update()
        algo.Hide()
        h = HLRBRep_HLRToShape(algo)
    vis = _hlr_curves(h.VCompound(), defl) + _hlr_curves(h.OutLineVCompound(), defl)
    hid = _hlr_curves(h.HCompound(), defl) + _hlr_curves(h.OutLineHCompound(), defl)
    tan = _hlr_curves(h.Rg1LineVCompound(), defl)
    res = {"visible": vis, "hidden": _drop_covered(hid, vis, size * 1e-6 + 1e-6), "tangent": tan}
    hlr_store(shape, d, up, res, fast)
    return res


def hlr_is_quick(shape):
    """Simple parts are projected exactly straight away; anything bigger gets a quick first look."""
    return len(edges(shape)) <= 60


def snap_features(shape):
    """3D points a drawing dimension can attach to: (vertices Nx3, [(centre, axis, radius)]).
    The seam point on a full circle (where a hole's edge starts and ends) isn't a real corner, so it's left out.
    Both lists come in the shape's own order, so the same feature keeps its index while the model's topology
    stays the same (e.g. when only a dimension changes)."""
    vm = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vm)
    real = set()                         # vertices on at least one edge that isn't a full circle or a seam
    seams = seam_edge_indices(shape)
    for ei, e in enumerate(edges(shape)):
        if ei in seams:
            continue
        try:
            ad = BRepAdaptor_Curve(e)
            closed = ad.GetType() == GeomAbs_Circle and abs((ad.LastParameter() - ad.FirstParameter())
                                                             - 2 * math.pi) < 1e-7
        except Exception:
            closed = False
        fv, lv = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        if not fv.IsNull() and fv.IsSame(lv):
            closed = True                    # any loop (e.g. a cross hole's rim on a round hub) has no real corner
        if closed or BRep_Tool.Degenerated_s(e):
            continue
        for v in (fv, lv):
            if not v.IsNull():
                real.add(vm.FindIndex(v))
    verts = []
    for i in range(1, vm.Size() + 1):
        verts.append(_np(BRep_Tool.Pnt_s(_as_vertex(vm.FindKey(i)))) if i in real else None)
    circles = []
    seen = {}
    for e in edges(shape):
        try:
            ad = BRepAdaptor_Curve(e)
            if ad.GetType() != GeomAbs_Circle:
                continue
            ci = ad.Circle()
            c = _np(ci.Location())
            ax = _np(ci.Axis().Direction())
            r = ci.Radius()
            full = abs((ad.LastParameter() - ad.FirstParameter()) - 2 * math.pi) < 1e-7
            k = (tuple(np.round(c, 6)), round(r, 6))
            if k in seen:
                if full:
                    j = seen[k]
                    circles[j] = (circles[j][0], circles[j][1], circles[j][2], True, False)
                continue
            seen[k] = len(circles)
            circles.append((c, ax, r, full, False))
        except Exception:
            continue
    # holes and shafts by their round faces too: a hole drilled across a round hub has no flat circular edge,
    # but its cylinder still looks like a circle when seen along its axis
    for f in faces(shape):
        try:
            ad = BRepAdaptor_Surface(f)
            if ad.GetType() != GeomAbs_Cylinder:
                continue
            cy = ad.Cylinder()
            ax = _np(cy.Axis().Direction())
            p0 = _np(cy.Axis().Location())
            c = p0 - ax * float(p0 @ ax)
            r = cy.Radius()
            full = abs((ad.LastUParameter() - ad.FirstUParameter()) - 2 * math.pi) < 1e-7
            k = ("cyl", tuple(np.round(c, 6)), tuple(np.round(np.abs(ax), 6)), round(r, 6))
            if k in seen:
                continue
            seen[k] = len(circles)
            circles.append((c, ax, r, full, True))
        except Exception:
            continue
    nan = np.full(3, np.nan)
    arr = np.array([nan if q is None else q for q in verts]) if verts else np.zeros((0, 3))
    return arr, circles
