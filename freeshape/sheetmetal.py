"""Sheet metal: flat panels joined by bends, folded up in 3D and unfolded into an exact flat pattern.

A sheet metal part keeps a small model next to its solid:

* panels — flat pieces of sheet. Each has its outline as a planar face in its own 2D coordinates (material
  from z = 0 to z = thickness), and two placements: folded (where it is in the 3D part) and flat (where it
  lands in the flat pattern, which lies in the base panel's plane);
* bends — each joins a panel's straight edge to a new panel, with an angle, an inside radius, and a direction
  (up = towards the material side, down = away from it).

Bend allowance (the length of sheet a bend uses up) = angle × (radius + K × thickness), with the K-factor
saying where the neutral line sits in the thickness (0.5 = the middle; ~0.42–0.45 suits most steel and
aluminium bent in a press brake).

A flange is added to the existing solid (a bend + a new panel), so holes cut earlier stay. The flat pattern is
built from the finished solid: each panel's face on its z = 0 side is moved into the flat, and each bend becomes
a strip as long as its bend allowance. So holes and cut-outs made with normal features (extrude cut, hole …)
appear in the flat pattern too, as long as they are in the flat panels and not across a bend.
"""
from __future__ import annotations

import copy
import math

import numpy as np

from . import kernel as K


# --------------------------------------------------------------------------- small geometry helpers
def frame(o, x, z):
    """4x4 matrix mapping local (x, y, z) to world, from an origin and x / z axes."""
    z = np.asarray(z, float)
    z = z / np.linalg.norm(z)
    x = np.asarray(x, float)
    x = x - z * (x @ z)
    x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, y, z, np.asarray(o, float)
    return M


def rot_about(point, axis, ang):
    """4x4 rotation by ang (radians) about the line through point along axis."""
    a = np.asarray(axis, float)
    a = a / np.linalg.norm(a)
    Kx = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    R = np.eye(3) + math.sin(ang) * Kx + (1 - math.cos(ang)) * (Kx @ Kx)
    M = np.eye(4)
    M[:3, :3] = R
    p = np.asarray(point, float)
    M[:3, 3] = p - R @ p
    return M


def apply(M, p):
    return (np.asarray(M, float) @ np.append(np.asarray(p, float), 1.0))[:3]


def _polygon_face(pts3):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    mp = BRepBuilderAPI_MakePolygon()
    for q in pts3:
        mp.Add(K._pnt(q))
    mp.Close()
    return BRepBuilderAPI_MakeFace(mp.Wire(), True).Face()


def _prism(face, vec):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    return BRepPrimAPI_MakePrism(face, K._vec(vec)).Shape()


def _moved(shape, M):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    return BRepBuilderAPI_Transform(shape, K.trsf_from_matrix(M), True).Shape()


def _unify(shape):
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
    u.Build()
    return u.Shape()


def _inside_point(face):
    """A point certainly on a face (the middle of one of its mesh triangles) — its centroid can be in a hole."""
    m = K.tessellate(face)
    if len(m.tris):
        return np.asarray(m.verts, float)[np.asarray(m.tris[0])].mean(axis=0)
    return np.asarray(K.face_info(face)["c"], float)


def _face_contains(face, pt2, tol=1e-6):
    """Is the 2D point inside (or on) a planar face lying in z = 0?"""
    from OCP.BRepClass import BRepClass_FaceClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON
    from OCP.gp import gp_Pnt2d
    from OCP.BRep import BRep_Tool
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    surf = BRep_Tool.Surface_s(face)
    pr = GeomAPI_ProjectPointOnSurf(K._pnt((pt2[0], pt2[1], 0.0)), surf)
    if pr.NbPoints() < 1:
        return False
    u, v = pr.LowerDistanceParameters()
    c = BRepClass_FaceClassifier(face, gp_Pnt2d(u, v), tol)
    return c.State() in (TopAbs_IN, TopAbs_ON)


# --------------------------------------------------------------------------- the model
class SheetModel:
    """Panels and bends of one sheet metal part (see the module docstring)."""

    def __init__(self, thickness, radius, k, relief="rect", relief_scale=1.5):
        self.thickness = float(thickness)
        self.radius = float(radius)
        self.k = float(k)
        self.relief = relief                  # corner relief where two bends meet: "rect" or "none"
        self.relief_scale = float(relief_scale)
        self.reliefs = []         # dicts: panel, c (local 2D corner), o1, o2 (the bends' outward dirs), e1, e2 (how
        #                                 far the notch reaches past each bend line, i.e. its bend allowance), s (size)
        self.panels = []          # dicts: id, face (local, z=0), fold (4x4), flat (4x4), parent, bend
        self.bends = []           # dicts: id, panel (parent id), child, a, b (local 2D of the parent), o (outward),
        #                                 angle (deg), radius, up, ba

    def copy(self):
        m = SheetModel(self.thickness, self.radius, self.k, self.relief, self.relief_scale)
        m.panels = [dict(p) for p in self.panels]
        m.bends = [dict(b) for b in self.bends]
        m.reliefs = [dict(r) for r in self.reliefs]
        return m

    def panel(self, pid):
        return next((p for p in self.panels if p["id"] == pid), None)

    def bend_allowance(self, angle_deg, radius):
        return math.radians(angle_deg) * (radius + self.k * self.thickness)

    # ------------------------------------------------------------------ edges a flange can go on
    def panel_edges(self, panel):
        """Straight outline edges of a panel that aren't bent yet: [(a2, b2, outward2)] in its local 2D."""
        out = []
        face = panel["face"]
        used = [(np.asarray(b["a"]), np.asarray(b["b"])) for b in self.bends if b["panel"] == panel["id"]]
        # the edge this panel hangs from (its bend) isn't free either
        for e in K.edges(face):
            info = K.edge_info(e)
            if "p0" not in info:
                continue
            a, b = np.array(info["p0"][:2]), np.array(info["p1"][:2])
            if np.linalg.norm(b - a) < 1e-6:
                continue
            if panel.get("bend") is not None and abs(a[1]) < 1e-7 and abs(b[1]) < 1e-7:
                continue                       # its root edge (v = 0), where its own bend joins
            if any(_same_seg(a, b, ua, ub) for ua, ub in used):
                continue
            d = (b - a) / np.linalg.norm(b - a)
            n = np.array([-d[1], d[0]])
            mid = (a + b) / 2
            eps = max(1e-4, 1e-4 * np.linalg.norm(b - a))
            o = n if not _face_contains(face, mid + n * eps) else -n
            out.append((a, b, o))
        return out

    def find_edge(self, ref, scale):
        """The panel edge best matching a picked 3D edge (its centre / direction / length)."""
        c = np.asarray(ref.get("c"), float)
        d = np.asarray(ref.get("d") or [1, 0, 0], float)
        L = float(ref.get("len", 0))
        t = self.thickness
        best, best_s = None, 1e9
        for p in self.panels:
            for a, b, o in self.panel_edges(p):
                for z in (0.0, t):
                    A, B = apply(p["fold"], (a[0], a[1], z)), apply(p["fold"], (b[0], b[1], z))
                    m = (A + B) / 2
                    dd = (B - A) / max(np.linalg.norm(B - A), 1e-12)
                    s = np.linalg.norm(c - m) / scale + (1 - abs(float(dd @ d))) * 5
                    s += abs(np.linalg.norm(B - A) - L) / max(L, np.linalg.norm(B - A), 1e-9)
                    if s < best_s:
                        best, best_s = (p, a, b, o), s
        if best is None or best_s > 0.4:
            raise K.KernelError("Pick a straight edge along the side of a sheet metal panel")
        return best


def _same_seg(a, b, c, d, tol=1e-6):
    return (np.linalg.norm(a - c) < tol and np.linalg.norm(b - d) < tol) or \
        (np.linalg.norm(a - d) < tol and np.linalg.norm(b - c) < tol)


# --------------------------------------------------------------------------- building
def make_base(region_face, plane: K.Plane, thickness, radius, k, flip=False, relief="rect", relief_scale=1.5):
    """A flat sheet from a sketch region: returns (solid, model)."""
    z = -plane.normal if flip else plane.normal
    M = frame(plane.origin, plane.xdir, z)
    Minv = np.linalg.inv(M)
    local = _moved(region_face, Minv)                       # the region in the panel's own 2D coordinates
    local = K.faces(local)[0]
    solid = _moved(_prism(local, (0, 0, thickness)), M)
    m = SheetModel(thickness, radius, k, relief, relief_scale)
    m.panels.append({"id": "P0", "face": local, "fold": M, "flat": M, "parent": None, "bend": None})
    return K.solids(solid)[0] if K.solids(solid) else solid, m


def add_flange(shape, model: SheetModel, panel, a, b, o, length, angle, radius=None, up=True,
               position="outside", fid="F"):
    """Bend a new flange off panel edge a→b (local 2D, o = outward). length is measured from the outside of
    the bend (the virtual sharp). position: 'outside' = the bend starts at the edge (the part grows);
    'inside' = the flange's outer face lines up with the old edge (the outline stays the same size).
    Returns (new shape, new model)."""
    m = model.copy()
    panel = m.panel(panel["id"])
    t = m.thickness
    R = float(radius if radius is not None else m.radius)
    th = math.radians(float(angle))
    if not (0 < angle <= 180):
        raise K.KernelError("The bend angle must be between 0 and 180°")
    a, b, o = (np.asarray(x, float) for x in (a, b, o))
    setback = (R + t) * math.tan(th / 2) if angle < 179.999 else (R + t)
    flat_len = float(length) - setback
    if flat_len <= 1e-6:
        raise K.KernelError(f"The flange is too short for this bend — make it longer than {setback:.2f} mm")
    Mf = panel["fold"]
    if position == "inside":
        # trim the panel back so the flange's outer face sits where the edge was
        strip2 = [a, b, b - o * setback, a - o * setback]
        panel["face"] = _trim_face(panel["face"], strip2)
        box = _prism(_polygon_face([(q[0], q[1], -1e-3) for q in strip2]), (0, 0, t + 2e-3))
        shape = K.cut(shape, _moved(box, Mf))
        a, b = a - o * setback, b - o * setback
    # run along the edge so that "left" of it is outward: then the new panel's local y points away from the bend
    dd = (b - a) / np.linalg.norm(b - a)
    if float(np.array([-dd[1], dd[0]]) @ o) < 0:
        a, b = b, a
    d = (b - a) / np.linalg.norm(b - a)
    w = float(np.linalg.norm(b - a))
    zv = np.array([0.0, 0.0, 1.0])
    o3 = np.array([o[0], o[1], 0.0])
    if up:
        k_ax = np.cross(o3, zv)
        Q = np.array([a[0], a[1], t + R])
    else:
        k_ax = np.cross(zv, o3)
        Q = np.array([a[0], a[1], -R])
    Rot = rot_about(Q, k_ax, th)
    a3 = np.array([a[0], a[1], 0.0])
    d3 = np.array([d[0], d[1], 0.0])
    # bend: the edge's cross-section swept about the bend axis (in the panel's local coordinates)
    sect = _polygon_face([a3, a3 + d3 * w, a3 + d3 * w + zv * t, a3 + zv * t])
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1
    bend_solid = BRepPrimAPI_MakeRevol(sect, gp_Ax1(K._pnt(Q), K._dir(k_ax)), th).Shape()
    # the new panel: local u along the edge, v away from the bend, same z side as its parent
    L_fold = frame(apply(Rot, a3), Rot[:3, :3] @ d3, Rot[:3, :3] @ zv)      # local y = the rotated outward
    ba = m.bend_allowance(angle, R)
    L_flat = frame(a3 + o3 * ba, d3, zv)
    child_face = _polygon_face([(0, 0, 0), (w, 0, 0), (w, flat_len, 0), (0, flat_len, 0)])
    slab = _moved(_prism(child_face, (0, 0, t)), L_fold)
    new = K.fuse(K.fuse(shape, _moved(bend_solid, Mf)), _moved(slab, Mf))
    cid = f"{fid}:{len(m.panels)}"
    m.panels.append({"id": cid, "face": child_face, "fold": Mf @ L_fold, "flat": panel["flat"] @ L_flat,
                     "parent": panel["id"], "bend": len(m.bends)})
    bend = {"id": f"B{len(m.bends) + 1}", "panel": panel["id"], "child": cid, "a": a.tolist(),
            "b": b.tolist(), "o": o.tolist(), "angle": float(angle), "radius": R, "up": bool(up),
            "ba": ba, "Q": Q.tolist(), "axis": k_ax.tolist(), "theta": th}
    m.bends.append(bend)
    # corner reliefs where this bend meets another one on the same panel
    if m.relief != "none":
        for other in m.bends[:-1]:
            if other["panel"] != panel["id"]:
                continue
            rel = _corner(m, bend, other, setback)
            if rel is not None:
                new = _cut_relief(new, m, panel, rel, (bend, other))
                m.reliefs.append(rel)
    sols = K.solids(new)
    return (_unify(sols[0]) if len(sols) == 1 else new), m


def _line_hit(a1, d1, a2, d2):
    """Where two 2D lines meet (None if parallel)."""
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        return None
    t = ((a2[0] - a1[0]) * d2[1] - (a2[1] - a1[1]) * d2[0]) / den
    return a1 + d1 * t


def _corner(m, b1, b2, setback):
    """If bends b1 and b2 meet at a corner of their panel, the corner relief for them (else None)."""
    a1, e1 = np.array(b1["a"]), np.array(b1["b"])
    a2, e2 = np.array(b2["a"]), np.array(b2["b"])
    d1 = (e1 - a1) / np.linalg.norm(e1 - a1)
    d2 = (e2 - a2) / np.linalg.norm(e2 - a2)
    c = _line_hit(a1, d1, a2, d2)
    if c is None:
        return None
    s = m.relief_scale * m.thickness
    near = setback + max(b1["ba"], b2["ba"]) + s + 1e-6
    if min(np.linalg.norm(c - a1), np.linalg.norm(c - e1)) > near or \
            min(np.linalg.norm(c - a2), np.linalg.norm(c - e2)) > near:
        return None
    return {"panel": b1["panel"], "c": c.tolist(), "o1": list(b1["o"]), "o2": list(b2["o"]),
            "e1": float(b1["ba"]), "e2": float(b2["ba"]), "s": float(s), "bends": [b1["id"], b2["id"]]}


def _relief_square(rel, z0, z1):
    """The notch in its panel's 2D coordinates: from s inside each bend line out to the far side of each bend."""
    c, o1, o2 = np.array(rel["c"]), np.array(rel["o1"]), np.array(rel["o2"])
    s = rel["s"]
    pts = [c + o1 * x + o2 * y for x, y in ((-s, -s), (rel["e1"], -s), (rel["e1"], rel["e2"]), (-s, rel["e2"]))]
    return _prism(_polygon_face([(q[0], q[1], z0) for q in pts]), (0, 0, z1 - z0))


def _cut_relief(shape, m, panel, rel, bends):
    """Cut the notch out of the folded part: the panel's corner, and the end of each bend next to it."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
    from OCP.gp import gp_Ax1
    t = m.thickness
    eps = 1e-3 * max(t, 1.0)
    tools = [_relief_square(rel, -eps, t + eps)]
    c = np.array(rel["c"])
    for bd in bends:
        other_o = np.array(rel["o2"] if bd["id"] == rel["bends"][0] else rel["o1"])
        a, b = np.array(bd["a"]), np.array(bd["b"])
        L = float(np.linalg.norm(b - a))
        d = (b - a) / L
        # the part of this bend's edge within s of the other bend's line (on the panel side) and beyond it
        k0 = float(d @ other_o)
        if abs(k0) < 1e-9:
            continue
        t_at = float((c - a) @ other_o) / k0 - rel["s"] / k0       # where (p - c)·o_other = -s
        lo_, hi_ = (t_at, L + 1.0) if k0 > 0 else (-1.0, t_at)
        lo_, hi_ = max(lo_, -1.0), min(hi_, L + 1.0)
        if hi_ - lo_ <= 1e-9:
            continue
        p0, p1 = a + d * lo_, a + d * hi_
        sect = _polygon_face([(p0[0], p0[1], -eps), (p1[0], p1[1], -eps), (p1[0], p1[1], t + eps),
                              (p0[0], p0[1], t + eps)])
        piece = BRepPrimAPI_MakeRevol(sect, gp_Ax1(K._pnt(bd["Q"]), K._dir(bd["axis"])),
                                      bd["theta"] + 1e-6).Shape()
        tools.append(piece)
    for tool in tools:
        shape = K.cut(shape, _moved(tool, panel["fold"]))
    return shape


def _trim_face(face, strip2):
    """A panel outline with a strip cut off along one edge."""
    tool = _prism(_polygon_face([(q[0], q[1], -1.0) for q in strip2]), (0, 0, 2.0))
    return _single_face(K.cut(_prism(face, (0, 0, 0.5)), tool), bottom=True)


def _single_face(shape, bottom=False):
    """The z = 0 face of a thin slab (or the only face of a face-shaped result)."""
    fs = K.faces(shape)
    if bottom:
        for f in fs:
            pl = K.planar_face_plane(f)
            if pl is not None and abs(abs(pl.normal[2]) - 1) < 1e-9 and abs(pl.origin[2]) < 1e-9:
                return f
    return fs[0]


# --------------------------------------------------------------------------- flat pattern
class FlatPattern:
    def __init__(self, face, bend_lines, M_base, thickness):
        self.face = face                  # the flat sheet (in world space, in the base panel's plane)
        self.bend_lines = bend_lines      # [(p0_world, p1_world, label)]
        self.M_base = M_base              # base panel frame: world -> 2D via its inverse
        self.thickness = thickness

    def solid(self):
        if getattr(self, "_solid", None) is not None:
            return self._solid
        z = self.M_base[:3, 2] * self.thickness
        return _prism(self.face, z)

    def to2d(self, p):
        q = apply(np.linalg.inv(self.M_base), p)
        return float(q[0]), float(q[1])


def flat_pattern(shape, m: SheetModel):
    """Unfold: each panel's face on its z = 0 side goes to its flat place; each bend becomes a strip of its
    bend allowance. Cuts made in panels come along."""
    faces = []
    tol = 1e-5 * (1 + float(np.linalg.norm(K.bbox(shape)[1] - K.bbox(shape)[0])))
    all_faces = K.faces(shape)
    for p in m.panels:
        Minv = np.linalg.inv(p["fold"])
        to_flat = p["flat"] @ Minv
        for f in all_faces:
            pl = K.planar_face_plane(f)
            if pl is None:
                continue
            # on this panel's z = 0 plane?
            o_loc = apply(Minv, pl.origin)
            n_loc = Minv[:3, :3] @ pl.normal
            if abs(abs(n_loc[2]) - 1) > 1e-7 or abs(o_loc[2]) > tol:
                continue
            c = apply(Minv, _inside_point(f))
            if not _face_contains(p["face"], (c[0], c[1]), tol=max(tol, 1e-4)):
                continue
            faces.append(_moved(f, to_flat))
    bend_lines = []
    for bd in m.bends:
        parent = m.panel(bd["panel"])
        a, b, o = np.array(bd["a"]), np.array(bd["b"]), np.array(bd["o"])
        ba = bd["ba"]
        strip = [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0] + o[0] * ba, b[1] + o[1] * ba, 0.0),
                 (a[0] + o[0] * ba, a[1] + o[1] * ba, 0.0)]
        faces.append(_moved(_polygon_face(strip), parent["flat"]))
        mid_a = (a + o * ba / 2)
        mid_b = (b + o * ba / 2)
        label = f"{'UP' if bd['up'] else 'DOWN'} {bd['angle']:g}° R{bd['radius']:g}"
        bend_lines.append((apply(parent["flat"], (mid_a[0], mid_a[1], 0)),
                           apply(parent["flat"], (mid_b[0], mid_b[1], 0)), label))
    if not faces:
        raise K.KernelError("Couldn't unfold this part")
    # join everything as thin slabs (solids merge far more reliably than loose faces), then keep the bottom
    base = m.panels[0]
    zdir = base["flat"][:3, 2] * m.thickness
    slab = K.fuse_all([_prism(f, zdir) for f in faces])
    for rel in m.reliefs:
        parent = m.panel(rel["panel"])
        tool = _relief_square(rel, -1.0, 1.0 + m.thickness)
        slab = K.cut(slab, _moved(tool, parent["flat"]))
    slab = _unify(slab)
    Binv = np.linalg.inv(base["flat"])
    bottom = []
    for f in K.faces(slab):
        pl = K.planar_face_plane(f)
        if pl is None:
            continue
        o_loc = apply(Binv, pl.origin)
        n_loc = Binv[:3, :3] @ pl.normal
        if abs(abs(n_loc[2]) - 1) < 1e-7 and abs(o_loc[2]) < 1e-6:
            bottom.append(f)
    if not bottom:
        raise K.KernelError("Couldn't unfold this part")
    sheet = bottom[0] if len(bottom) == 1 else K.compound(bottom)
    fp = FlatPattern(sheet, bend_lines, base["flat"], m.thickness)
    # a clean solid straight from the outline (no seams left over from joining the strips)
    fp._solid = K.fuse_all([_prism(f, zdir) for f in bottom]) if len(bottom) > 1 else _prism(bottom[0], zdir)
    return fp


def flat_dxf(fp: FlatPattern, path, name=""):
    """The flat pattern as a DXF for laser / plasma / waterjet cutting: the outline (layer CUT), bend lines
    (layer BEND, dashed) with their direction, angle and radius."""
    lines, circles, arcs, polys = [], [], [], []
    for e in K.edges(fp.face):
        info = K.edge_info(e)
        if "p0" in info:
            lines.append((fp.to2d(info["p0"]), fp.to2d(info["p1"])))
            continue
        pts = K.discretize_edge(e, 0.005)
        if "r" in info and len(pts) >= 2:
            c = fp.to2d(info["center"])
            p0, p1 = fp.to2d(pts[0]), fp.to2d(pts[-1])
            if np.linalg.norm(np.subtract(p0, p1)) < 1e-6:
                circles.append((c, info["r"]))
                continue
            mid = fp.to2d(pts[len(pts) // 2])
            a0 = math.degrees(math.atan2(p0[1] - c[1], p0[0] - c[0]))
            a1 = math.degrees(math.atan2(p1[1] - c[1], p1[0] - c[0]))
            am = math.degrees(math.atan2(mid[1] - c[1], mid[0] - c[0]))
            # counter-clockwise from a0 to a1 must pass through the middle point
            if not ((am - a0) % 360 < (a1 - a0) % 360):
                a0, a1 = a1, a0
            arcs.append((c, info["r"], a0, a1))
            continue
        polys.append([fp.to2d(q) for q in pts])
    bends = [(fp.to2d(p0), fp.to2d(p1), lab) for p0, p1, lab in fp.bend_lines]
    try:
        import ezdxf
        doc = ezdxf.new("R2010", setup=False)
        doc.units = ezdxf.units.MM
        doc.header["$MEASUREMENT"] = 1
        doc.linetypes.add("DASHED", pattern=[4.5, 3.0, -1.5], description="Dashed __ __ __")
        doc.styles.get("Standard").dxf.font = "arial.ttf"
        doc.layers.add("CUT", color=7)
        doc.layers.add("BEND", color=1, linetype="DASHED")
        doc.layers.add("NOTES", color=3)
        msp = doc.modelspace()
        for p, q in lines:
            msp.add_line(p, q, dxfattribs={"layer": "CUT"})
        for c, r in circles:
            msp.add_circle(c, r, dxfattribs={"layer": "CUT"})
        for c, r, a0, a1 in arcs:
            msp.add_arc(c, r, a0, a1, dxfattribs={"layer": "CUT"})
        for pts in polys:
            msp.add_lwpolyline(pts, dxfattribs={"layer": "CUT"})
        from ezdxf.enums import TextEntityAlignment
        for p, q, lab in bends:
            msp.add_line(p, q, dxfattribs={"layer": "BEND"})
            ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            if ang > 90 or ang <= -90:
                ang -= 180 if ang > 0 else -180
            t = msp.add_text(lab, height=2.5, rotation=ang, dxfattribs={"layer": "NOTES"})
            t.set_placement(((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), align=TextEntityAlignment.BOTTOM_CENTER)
        doc.saveas(path)
    except ImportError:
        from .dxf import DxfWriter
        w = DxfWriter()
        w.layer("CUT", 7)
        w.layer("BEND", 1, "DASHED")
        w.layer("NOTES", 3)
        for p, q in lines:
            w.line("CUT", p, q)
        for c, r in circles:
            w.circle("CUT", c, r)
        for c, r, a0, a1 in arcs:
            w.arc("CUT", c, r, a0, a1)
        for pts in polys:
            w.polyline("CUT", pts)
        for p, q, lab in bends:
            w.line("BEND", p, q)
            ang = math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))
            w.text("NOTES", ((p[0] + q[0]) / 2, (p[1] + q[1]) / 2), lab, 2.5, ang, "center", "bottom")
        lo, hi = K.bbox(fp.face)
        w.save(path, fp.to2d(lo), fp.to2d(hi))
    return len(lines) + len(circles) + len(arcs) + len(polys), len(bends)
