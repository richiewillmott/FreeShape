"""Variable (formerly Measure value): a #variable that is either a value you type — a length, an angle, a plain
number or any expression, with a unit — or measured from the model.

Fixed types: fixed_length | fixed_angle | fixed_number | expression, with `expr` (e.g. "25", "1 m", "#a * 2") and
`unit` (mm cm m in ft) / `aunit` (deg rad) for bare numbers. The variable remembers its kind (length, angle …) so
using it in a box that wants something else is an error, not a silent number.

Measured types (below):

Like a Variable feature, but its value comes from geometry — a distance, angle, diameter, length, area,
volume, count, coordinate, centre of mass or bounding-box size — and it is measured again on every rebuild,
so dimensions and features that use the variable follow the model.

Params:
    name        variable name (without #)
    mtype       distance | ray | angle | diameter | radius | length | area | volume | count | coordinate | centroid
                | size | inertia
    entities    [[kind, ref], ...] with kind in vertex / edge / face / part / region / plane / sketch_line
                (a vertex may be a part corner or a sketch point; sketch_line is one curve of a sketch)
    method      distance only: min | max | center | inf (infinite propagation: lines, circles and faces extended)
    component   distance: total | x | y | z  (total = straight-line distance, x/y/z = along that axis)
    axis_c      coordinate / centroid / size: x | y | z
    round_to    round the value to a multiple of this (0 = no rounding)
    ray         ray distance: [[axis, ref]] the ray (a sketch line or straight edge, from its start; a round edge or
                a cylinder, along its axis); ray_offset, ray_flip, ray_closest, ray_box, ray_index as in the
                Onshape feature (index counts hits from 0; negative counts from the far end)
    flip_angle  angle: 180° minus the angle
    count_mode  matching (faces / edges of the same part with the same geometry as each pick — e.g. all the holes
                the same size as the one picked) | picks (how many things are picked; files from 2.2.0)
    coord_type  coordinate: global | local (in the frame of the plane in `frame`) | along (along the direction of
                the plane normal or axis in `frame`)
    frame       [[plane | axis, ref]]: the frame for local coordinates, bounding boxes and inertia, or the
                direction for 'along'
    density     inertia: g/cm³ (the moment is in g·mm², about the frame's X / Y / Z axis through its origin)
    hide_preview  don't draw the measured line while editing

The options follow Konstantin Sh's "Measure value" FeatureScript for Onshape (CADSharp); FreeShape has no mate
connectors in a Part Studio, so a plane (origin and X / Y / Z directions) or an axis stands in for one.
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K

FIXED_TYPES = [("fixed_length", "Length"), ("fixed_angle", "Angle"), ("fixed_number", "Number"),
               ("expression", "Any expression")]
FIXED = {k for k, _ in FIXED_TYPES}
MEASURED_TYPES = [("distance", "Distance"), ("ray", "Ray distance"), ("angle", "Angle"), ("diameter", "Diameter"),
                  ("radius", "Radius"), ("length", "Perimeter / length"), ("area", "Area"), ("volume", "Volume"),
                  ("count", "Count"), ("coordinate", "Coordinate of a vertex"), ("centroid", "Centroid"),
                  ("size", "Bounding box size"), ("inertia", "Inertia")]
TYPES = FIXED_TYPES + [(k, "Measured " + lab[0].lower() + lab[1:]) for k, lab in MEASURED_TYPES]
LENGTH_UNITS = [("mm", "mm"), ("cm", "cm"), ("m", "m"), ("in", "in"), ("ft", "ft")]
ANGLE_UNITS = [("deg", "deg"), ("rad", "rad")]
# what kind of quantity each type makes (for unit checking where the variable is used)
KINDS = {"fixed_length": "length", "fixed_angle": "angle", "fixed_number": "number", "distance": "length",
         "ray": "length", "angle": "angle", "diameter": "length", "radius": "length", "length": "length",
         "area": "area", "volume": "volume", "count": "number", "coordinate": "length", "centroid": "length",
         "size": "length", "inertia": "any"}
METHODS = [("min", "Minimum"), ("max", "Maximum"), ("inf", "Infinite propagation"), ("center", "Centre distance")]
COUNT_MODES = [("matching", "Matching faces / edges"), ("picks", "Picked items")]
COORD_TYPES = [("global", "Global"), ("local", "Local (plane's frame)"), ("along", "Along direction")]
COMPONENTS = [("total", "Distance"), ("x", "X"), ("y", "Y"), ("z", "Z")]
AXIS_COMPONENTS = [("x", "X"), ("y", "Y"), ("z", "Z")]
UNITS = {"distance": "mm", "ray": "mm", "inertia": "g·mm²", "diameter": "mm", "radius": "mm", "length": "mm", "coordinate": "mm",
         "centroid": "mm", "size": "mm", "area": "mm²", "volume": "mm³", "angle": "°", "count": ""}
DEFAULTS = {"name": "v", "mtype": "fixed_length", "expr": "10", "unit": "mm", "aunit": "deg", "entities": [], "method": "min", "component": "total", "axis_c": "x",
            "round_to": 0.0, "ray": [], "ray_offset": 0.0, "ray_flip": False, "ray_closest": True, "ray_box": False,
            "ray_index": 0, "flip_angle": False, "count_mode": "matching", "coord_type": "global", "frame": [],
            "density": 1.0, "hide_preview": False}


# --------------------------------------------------------------------------- resolving the picks
def _vertex(pt):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.gp import gp_Pnt
    return BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(x) for x in pt])).Vertex()


def _plane_face(pl, half=1e4):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.gp import gp_Ax3, gp_Dir, gp_Pln, gp_Pnt
    ax = gp_Ax3(gp_Pnt(*pl.origin), gp_Dir(*pl.normal), gp_Dir(*pl.xdir))
    return BRepBuilderAPI_MakeFace(gp_Pln(ax), -half, half, -half, half).Face()


def resolve_items(state, entities):
    """[(kind, shape)] for the stored picks; a missing reference is an error, as in other features."""
    from . import features as F
    out = []
    for kind, ref in entities or []:
        if kind == "vertex":
            out.append(("vertex", _vertex(F.resolve_point(state, ref))))
        elif kind in ("edge", "face"):
            out.append((kind, F.resolve(state, ref, kind)[1]))
        elif kind == "part":
            if ref not in state.parts:
                raise K.KernelError("A measured part no longer exists")
            out.append(("part", state.parts[ref].shape))
        elif kind == "region":
            out.append(("region", F.resolve_regions(state, [ref])[0]))
        elif kind == "plane":
            out.append(("plane", _plane_face(F.resolve_plane(state, ref))))
        elif kind == "sketch_line":
            out.append(F.resolve_sketch_curve(state, ref))          # ("edge", …) or ("vertex", …) for a point
    return out


# --------------------------------------------------------------------------- geometry helpers
def _geom(kind, shape):
    from . import measure as M
    if kind == "plane":
        pl = K.planar_face_plane(shape)
        return {"type": "plane", "p": pl.origin, "n": pl.normal}
    if kind == "vertex":
        return {"type": "point", "p": _point(shape)}
    if kind == "edge" and len(K.edges(shape)) != 1:
        return {"type": "other"}                    # a sketch curve made of several edges
    if kind == "edge":
        shape = K.edges(shape)[0]
    g = M._geom("face" if kind == "region" else kind, shape)
    if g["type"] == "other" and kind == "face":
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Sphere
        ad = BRepAdaptor_Surface(shape)
        if ad.GetType() == GeomAbs_Sphere:
            sp = ad.Sphere()
            loc = sp.Location()
            return {"type": "sphere", "p": np.array([loc.X(), loc.Y(), loc.Z()]), "r": sp.Radius()}
    return g


def _point(v):
    from OCP.BRep import BRep_Tool
    p = BRep_Tool.Pnt_s(v)
    return np.array([p.X(), p.Y(), p.Z()])


def _centre(kind, shape):
    """The centre used by 'centre to centre': a point, or an axis (p, d) for cylinders."""
    g = _geom(kind, shape)
    if g["type"] == "point":
        return ("point", g["p"])
    if g["type"] in ("circle", "sphere"):
        return ("point", g["p"])
    if g["type"] == "cylinder":
        return ("axis", g["p"], g["d"] / np.linalg.norm(g["d"]))
    if g["type"] == "line":
        return ("axis", g["p"], g["d"] / np.linalg.norm(g["d"]))
    return ("point", _centroid(kind, shape))


def _centroid(kind, shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    if kind == "vertex":
        return _point(shape)
    if kind == "part" and K.solids(shape):
        return np.asarray(K.mass_props(shape)["centroid"], float)
    g = GProp_GProps()
    if kind in ("face", "region", "plane", "part"):
        BRepGProp.SurfaceProperties_s(shape, g)
    else:
        BRepGProp.LinearProperties_s(shape, g)
    c = g.CentreOfMass()
    return np.array([c.X(), c.Y(), c.Z()])


def _samples(kind, shape):
    """Points covering a shape, for the maximum distance (vertices, edge points and face mesh points)."""
    if kind == "vertex":
        return np.array([_point(shape)])
    pts = [np.asarray(K.discretize_edge(e), float) for e in K.edges(shape)]
    if kind in ("face", "region", "part"):
        try:
            m = K.tessellate(shape)
            if len(m.verts):
                pts.append(np.asarray(m.verts, float))
        except Exception:
            pass
    pts = [p for p in pts if len(p)]
    if not pts:
        return np.array([_centroid(kind, shape)])
    return np.concatenate(pts)


def _vector(items, method):
    """(from, to) points of the distance between the first pick and the second."""
    from . import measure as M
    (ka, a), (kb, b) = items[0], items[1]
    if method == "max":
        pa, pb = _samples(ka, a), _samples(kb, b)
        best, pair = -1.0, None
        for i in range(0, len(pa), 512):           # chunks keep the distance matrix small
            d = np.linalg.norm(pa[i:i + 512, None, :] - pb[None, :, :], axis=2)
            j = np.unravel_index(int(np.argmax(d)), d.shape)
            if d[j] > best:
                best, pair = float(d[j]), (pa[i + j[0]], pb[j[1]])
        return pair
    if method == "center":
        ca, cb = _centre(ka, a), _centre(kb, b)
        if ca[0] == "point" and cb[0] == "point":
            return ca[1], cb[1]
        if ca[0] == "axis" and cb[0] == "point":
            return _foot(ca, cb[1]), cb[1]
        if ca[0] == "point" and cb[0] == "axis":
            return ca[1], _foot(cb, ca[1])
        # two axes: the closest points of the two lines (parallel axes: perpendicular distance)
        p1, d1, p2, d2 = ca[1], ca[2], cb[1], cb[2]
        w = p1 - p2
        b_ = d1 @ d2
        den = 1 - b_ * b_
        if den < 1e-12:
            return p1, _foot(cb, p1)
        s = (b_ * (d2 @ w) - (d1 @ w)) / den
        t = ((d2 @ w) - b_ * (d1 @ w)) / den
        return p1 + s * d1, p2 + t * d2
    md = M._min_dist(a, b)
    if md is None:
        raise K.KernelError("Couldn't measure the distance between these")
    return md[1], md[2]


def _foot(axis, q):
    _, p, d = axis
    return p + ((q - p) @ d) * d


def _direction(kind, shape):
    g = _geom(kind, shape)
    for key, t in (("n", "plane"), ("d", "line"), ("d", "cylinder"), ("d", "circle")):
        if g["type"] == t:
            v = np.asarray(g[key], float)
            return t, v / np.linalg.norm(v)
    raise K.KernelError("Angle needs straight edges, flat faces, planes or round edges / faces (their axes)")


# --------------------------------------------------------------------------- infinite propagation
BIG = 1e5


def _extended(kind, shape):
    """The shape grown to its whole underlying geometry: a straight edge → a (very long) line, an arc → its full
    circle, a face → its whole surface (planes and cylinders very large, spheres whole). Others are unchanged."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane, GeomAbs_Sphere
    try:
        if kind == "edge" and len(K.edges(shape)) == 1:
            e = K.edges(shape)[0]
            ad = BRepAdaptor_Curve(e)
            if ad.GetType() == GeomAbs_Line:
                return BRepBuilderAPI_MakeEdge(ad.Line(), -BIG, BIG).Edge()
            if ad.GetType() == GeomAbs_Circle:
                return BRepBuilderAPI_MakeEdge(ad.Circle()).Edge()
            return e
        if kind in ("face", "region"):
            ad = BRepAdaptor_Surface(shape)
            t = ad.GetType()
            if t == GeomAbs_Plane:
                return BRepBuilderAPI_MakeFace(ad.Plane(), -BIG, BIG, -BIG, BIG).Face()
            if t == GeomAbs_Cylinder:
                return BRepBuilderAPI_MakeFace(ad.Cylinder(), 0, 2 * math.pi, -BIG, BIG).Face()
            if t == GeomAbs_Sphere:
                return BRepBuilderAPI_MakeFace(ad.Sphere()).Face()
            return BRepBuilderAPI_MakeFace(BRep_Tool.Surface_s(shape), 1e-7).Face()
    except Exception:
        pass
    return shape


# --------------------------------------------------------------------------- rays, frames, matching
def _axis(state, item):
    """(origin, unit direction) of a ray / direction pick: a sketch line or straight edge from its start point, a
    round edge or cylinder along its axis from its centre, a world axis from the origin."""
    from . import features as F
    kind, ref = item
    if kind == "axis" and ref.get("kind") == "edge":
        _, e = F.resolve(state, ref["edge"], "edge")
        info = K.edge_info(e)
        if K.edge_line(e) is not None:
            a, b = np.array(info["p0"], float), np.array(info["p1"], float)
            return a, (b - a) / np.linalg.norm(b - a)
    if kind == "axis":
        a, d = F.resolve_axis(state, ref)
        return np.asarray(a, float), np.asarray(d, float) / np.linalg.norm(d)
    if kind in ("face", "plane"):
        if kind == "face":
            g = _geom("face", F.resolve(state, ref, "face")[1])
        else:
            pl = F.resolve_plane(state, ref)
            g = {"type": "plane", "p": pl.origin, "n": pl.normal}
        if g["type"] == "plane":
            return np.asarray(g["p"], float), np.asarray(g["n"], float)
        if g["type"] == "cylinder":
            return np.asarray(g["p"], float), np.asarray(g["d"], float) / np.linalg.norm(g["d"])
    raise K.KernelError("Pick an axis: a sketch line, an edge, a cylinder or a plane (its normal)")


def _frame(state, p):
    """(origin, [X, Y, Z] unit axes) of the frame pick — a plane — or the world frame when none is picked."""
    from . import features as F
    fr = p.get("frame") or []
    if not fr:
        return np.zeros(3), [np.eye(3)[i] for i in range(3)]
    kind, ref = fr[0]
    if kind != "plane":
        raise K.KernelError("The coordinate system is a plane (its origin and X / Y / Z directions)")
    pl = F.resolve_plane(state, ref)
    x, z = np.asarray(pl.xdir, float), np.asarray(pl.normal, float)
    return np.asarray(pl.origin, float), [x, np.cross(z, x), z]


def _ray_hits(shape, o, d):
    """Distances along the ray (o, d) where it crosses the shape's faces, nearest first (t >= 0)."""
    from OCP.BRepIntCurveSurface import BRepIntCurveSurface_Inter
    from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
    it = BRepIntCurveSurface_Inter()
    it.Init(shape, gp_Lin(gp_Pnt(*map(float, o)), gp_Dir(*map(float, d))), 1e-7)
    out = []
    while it.More():
        if it.W() >= -1e-9:
            out.append(float(it.W()))
        it.Next()
    return out


def _matching(shape, kind, picked, tol=1e-6):
    """Faces or edges of `shape` with the same kind of geometry and size as the picked one."""
    if kind == "face":
        g0, a0 = _geom("face", picked), K.face_info(picked)["area"]
        out = []
        for f in K.faces(shape):
            g = _geom("face", f)
            if g["type"] != g0["type"] or abs(K.face_info(f)["area"] - a0) > tol * max(a0, 1) * 100:
                continue
            if "r" in g0 and abs(g["r"] - g0["r"]) > 1e-6 * max(g0["r"], 1):
                continue
            out.append(f)
        return out
    g0, l0 = _geom("edge", picked), K.edge_info(picked)["len"]
    out = []
    for e in K.edges(shape):
        g = _geom("edge", e)
        if g["type"] != g0["type"] or abs(K.edge_info(e)["len"] - l0) > tol * max(l0, 1) * 100:
            continue
        if "r" in g0 and abs(g["r"] - g0["r"]) > 1e-6 * max(g0["r"], 1):
            continue
        out.append(e)
    return out


def _tangent_at_closest(ea, eb):
    """Unit tangents of two edges at their closest points (for the angle between curved edges)."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt, gp_Vec
    d = BRepExtrema_DistShapeShape(ea, eb)
    d.Perform()
    if not d.IsDone() or d.NbSolution() == 0:
        raise K.KernelError("Couldn't find where these edges are closest")
    out = []
    for i, e in ((1, ea), (2, eb)):
        try:
            t = d.ParOnEdgeS1(1) if i == 1 else d.ParOnEdgeS2(1)
        except Exception:                      # the closest point is an end vertex
            ad0 = BRepAdaptor_Curve(e)
            q = d.PointOnShape1(1) if i == 1 else d.PointOnShape2(1)
            t = min((ad0.FirstParameter(), ad0.LastParameter()), key=lambda u: ad0.Value(u).Distance(q))
        ad = BRepAdaptor_Curve(e)
        pnt, v = gp_Pnt(), gp_Vec()
        ad.D1(t, pnt, v)
        w = np.array([v.X(), v.Y(), v.Z()])
        out.append(w / np.linalg.norm(w))
    return out


def _gprops(kind, shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    if kind == "part" and K.solids(shape):
        BRepGProp.VolumeProperties_s(shape, g)
    elif kind in ("face", "region", "part"):
        BRepGProp.SurfaceProperties_s(shape, g)
    else:
        BRepGProp.LinearProperties_s(shape, g)
    return g


# --------------------------------------------------------------------------- the value
def compute(state, p):
    """The measured value (mm, mm², mm³, degrees, g·mm² or a count), before rounding."""
    return measure(state, p)[0]


def measure(state, p):
    """(value, line): the value before rounding and the (from, to) points of the measured line for the preview
    (None when there is no line)."""
    mtype = p.get("mtype", "distance")
    if mtype in FIXED:
        return fixed_value(state, p)[0], None
    items = resolve_items(state, p.get("entities"))
    if mtype == "ray":
        return _ray(state, p, items)
    if mtype == "count":
        if p.get("count_mode", "picks") != "matching":
            return float(len(items)), None
        n = 0
        for (kind, sh), (_k, ref) in zip(items, p.get("entities") or []):
            if kind in ("face", "edge") and isinstance(ref, dict) and ref.get("part") in state.parts:
                n += len(_matching(state.parts[ref["part"]].shape, kind, sh))
            else:
                n += 1
        return float(n), None
    if not items:
        raise K.KernelError("Select what to measure")
    comp = p.get("component", "total")
    if mtype == "distance":
        if len(items) != 2:
            raise K.KernelError("Distance needs two picks")
        method = p.get("method", "min")
        if method == "inf":
            items = [(k, _extended(k, sh)) for k, sh in items]
            method = "min"
        a, b = _vector(items, method)
        v = np.asarray(b, float) - np.asarray(a, float)
        val = float(np.linalg.norm(v)) if comp == "total" else abs(float(v["xyz".index(comp)]))
        return val, (np.asarray(a, float), np.asarray(b, float))
    if mtype == "angle":
        if len(items) != 2:
            raise K.KernelError("Angle needs two picks")
        try:
            (ta, da), (tb, db) = _direction(*items[0]), _direction(*items[1])
        except K.KernelError:
            if not all(k == "edge" for k, _ in items):
                raise K.KernelError("Two things with a direction, or two edges, are needed")
            (da, db), ta, tb = _tangent_at_closest(items[0][1], items[1][1]), "line", "line"
        ang = math.degrees(math.acos(float(np.clip(abs(da @ db), 0, 1))))
        if {ta, tb} == {"plane", "line"} or {ta, tb} == {"plane", "cylinder"}:
            ang = 90 - ang                      # a line's angle to a plane, not to its normal
        return (180 - ang if p.get("flip_angle") else ang), None
    if mtype in ("diameter", "radius"):
        g = _geom(*items[0])
        if g["type"] not in ("circle", "cylinder", "sphere"):
            raise K.KernelError("Pick a round edge, a cylinder or a sphere")
        return g["r"] * (2 if mtype == "diameter" else 1), None
    if mtype == "length":
        tot = 0.0
        for kind, s in items:
            if kind == "edge":
                tot += sum(K.edge_info(e)["len"] for e in K.edges(s))
            elif kind in ("face", "region"):
                tot += sum(K.edge_info(e)["len"] for e in K.edges(s))       # its perimeter
            elif kind == "part" and not K.faces(s):
                tot += sum(K.edge_info(e)["len"] for e in K.edges(s))       # a curve part (a helix …)
            else:
                raise K.KernelError("Perimeter needs edges, faces (their outline) or curve parts")
        return tot, None
    if mtype == "area":
        tot = 0.0
        for kind, s in items:
            if kind in ("face", "region"):
                tot += K.face_info(s)["area"]
            elif kind == "part":
                tot += sum(K.face_info(f)["area"] for f in K.faces(s))       # its surface area
            else:
                raise K.KernelError("Area needs faces or parts")
        return tot, None
    if mtype == "volume":
        if any(k != "part" for k, _ in items):
            raise K.KernelError("Volume needs parts")
        return sum(K.volume(s) for _, s in items), None
    i = "xyz".index(p.get("axis_c", "x")) if p.get("axis_c", "x") in ("x", "y", "z") else 0
    if mtype == "coordinate":
        if items[0][0] != "vertex":
            raise K.KernelError("Coordinate needs a vertex")
        pt = _point(items[0][1])
        ct = p.get("coord_type", "global")
        if ct == "local":
            o, axes = _frame(state, p)
            return float((pt - o) @ axes[i]), None
        if ct == "along":
            fr = p.get("frame") or []
            if not fr:
                raise K.KernelError("Pick the direction: an axis, an edge or a plane")
            _o, d = _axis(state, fr[0])
            return float(pt @ d), None
        return float(pt[i]), None
    if mtype == "centroid":
        if len(items) == 1:
            return float(_centroid(*items[0])[i]), None
        vols = [K.volume(s) if k == "part" and K.solids(s) else 1.0 for k, s in items]
        cs = [_centroid(k, s) for k, s in items]
        return float(sum(v * c[i] for v, c in zip(vols, cs)) / sum(vols)), None
    if mtype == "size":
        o, axes = _frame(state, p)
        lo, hi = np.full(3, 1e18), np.full(3, -1e18)
        for k, s in items:
            pts = _samples(k, s) @ np.array(axes).T if p.get("frame") else None
            if pts is not None:
                a, b = pts.min(0), pts.max(0)
            else:
                a, b = K.bbox(s)
            lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        return float((hi - lo)[i]), None
    if mtype == "inertia":
        from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt
        o, axes = _frame(state, p)
        ax = gp_Ax1(gp_Pnt(*map(float, o)), gp_Dir(*map(float, axes[i])))
        dens = float(p.get("density", 1.0) or 0) / 1000.0              # g/cm³ → g/mm³
        return sum(_gprops(k, s).MomentOfInertia(ax) for k, s in items) * dens, None
    raise K.KernelError(f"Unknown measurement '{mtype}'")


def _ray(state, p, items):
    """Ray distance, as the Onshape feature: the distance along the ray to where it meets the targets."""
    ray = p.get("ray") or []
    if not ray:
        raise K.KernelError("Pick the ray: a sketch line or edge (from its start), or a round edge / cylinder")
    if not items:
        raise K.KernelError("Pick the target entities")
    o0, d = _axis(state, ray[0])
    if p.get("ray_flip"):
        d = -d
    off = float(p.get("ray_offset", 0) or 0)
    o = o0 + d * off
    closest = p.get("ray_closest", True)
    if not closest and p.get("ray_box"):
        pts = np.concatenate([_samples(k, s) for k, s in items])
        t = (pts - o) @ d
        dists = [float(t.min()), float(t.max())]
    else:
        dists = sorted(t for k, s in items if k in ("face", "region", "part", "plane") for t in _ray_hits(s, o, d))
    if not dists:
        # no hit: where the targets come closest to the ray's line
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
        from OCP.gp import gp_Dir, gp_Lin, gp_Pnt
        from . import measure as M
        line = BRepBuilderAPI_MakeEdge(gp_Lin(gp_Pnt(*map(float, o)), gp_Dir(*map(float, d))), -BIG, BIG).Edge()
        best = min((M._min_dist(s, line) for _k, s in items), key=lambda m: m[0] if m else 1e18)
        if best is None:
            raise K.KernelError("The ray doesn't reach the targets")
        val = float((best[1] - o) @ d)
    elif closest:
        val = dists[0]
    else:
        n = int(p.get("ray_index", 0) or 0)
        n = len(dists) + n if n < 0 else n
        if not 0 <= n < len(dists):
            raise K.KernelError(f"Intersection index is out of range [0, {len(dists) - 1}]")
        val = dists[n]
    val += off
    return val, (o0, o0 + d * val)


def fixed_value(state, p):
    """(value in mm / degrees / plain, kind) of a typed Length / Angle / Number / Any expression. A bare number
    is in the chosen unit; an expression with its own units ("1 m", "#a * 2") is converted; the wrong kind of
    quantity ("30 deg" for a Length) is an error."""
    from .expr import KIND_DIMS, UnitError, _UNITS, dims_name, expr_dims, parse_number, var_names
    mtype = p.get("mtype", "fixed_length")
    text = str(p.get("expr", "")).strip()
    if not text:
        raise K.KernelError("Enter a value or expression")
    want = {"fixed_length": "length", "fixed_angle": "angle", "fixed_number": "number"}.get(mtype, "any")
    from .expr import eval_text, is_text_expr, text_problem
    if want == "any" and is_text_expr(text, state.vars):         # text: "Rev B", "PN-" + #n
        try:
            return eval_text(text, state.vars), "text"
        except ValueError as e:
            raise K.KernelError(f"'{text}': {e}")
    why = text_problem(text, state.vars)
    if why:
        raise K.KernelError(f"'{text}': {why}")
    val = parse_number(text, angle=(want == "angle"), variables=state.vars)
    if val is None:
        missing = [n for n in var_names(text) if n not in state.vars]
        raise K.KernelError(f"#{missing[0]} isn't defined before this feature" if missing
                            else f"Can't read '{text}'")
    kinds = getattr(state, "var_kinds", {})
    try:
        d = expr_dims(text, kinds)
    except UnitError as e:
        raise K.KernelError(f"'{text}': {e}")
    if want != "any":
        if d not in (None, (0, 0), KIND_DIMS[want]):
            raise K.KernelError(f"'{text}' is {dims_name(d)} — this variable is {dims_name(KIND_DIMS[want])}")
        if d == (0, 0) and want == "length":
            val *= _UNITS.get(p.get("unit", "mm"), 1.0)
        elif d == (0, 0) and want == "angle":
            val *= _UNITS.get(p.get("aunit", "deg"), 1.0)
        return float(val), want
    kind = {(1, 0): "length", (0, 1): "angle", (2, 0): "area", (3, 0): "volume", (0, 0): "number"}.get(d, "any")
    return float(val), kind


def kind_of(state, p):
    mtype = p.get("mtype", "distance")
    if mtype in FIXED:
        return fixed_value(state, p)[1]
    return KINDS.get(mtype, "any")


def value_text(p, val, kind=None):
    """How a variable's value is shown: in the unit chosen for a typed Length / Angle, else mm, °, mm², mm³."""
    from .expr import _UNITS, fmt
    mtype = p.get("mtype", "distance")
    if mtype == "fixed_length":
        u = p.get("unit", "mm")
        return f"{fmt(val / _UNITS.get(u, 1.0), 4)} {u}"
    if mtype == "fixed_angle":
        u = p.get("aunit", "deg")
        return f"{fmt(val / _UNITS.get(u, 1.0), 4)} {'°' if u == 'deg' else u}"
    if mtype in ("fixed_number",):
        return fmt(val, 4)
    if isinstance(val, str):
        return '"' + val + '"'
    if mtype == "expression":
        return quantity_text(kind or "", val, 4)
    return quantity_text(KINDS.get(mtype, "any"), val, 3, mtype)


def quantity_text(kind, val, digits=3, mtype=None):
    """A value of a kind (length, area, volume …) for display: lengths, areas and volumes in the document's unit."""
    from . import units as U
    from .expr import fmt
    if kind == "length":
        return U.length(val, digits)
    if kind == "area":
        return U.area(val, digits)
    if kind == "volume":
        return U.volume(val, digits)
    if kind == "angle":
        return f"{fmt(val, digits)} °"
    unit = UNITS.get(mtype, "") if mtype else ""
    return f"{fmt(val, digits)}{(' ' + unit) if unit else ''}"


def rounded(value, step):
    step = float(step or 0)
    if step <= 0:
        return value
    return round(value / step) * step


def run(state, f):
    p = f["params"]
    name = str(p.get("name", "")).strip().lstrip("#")
    if not name or not name.replace("_", "a").isalnum() or name[0].isdigit():
        raise K.KernelError("Variable names use letters, digits and _ (not starting with a digit)")
    val = compute(state, p)
    if p.get("mtype", "distance") not in FIXED:
        val = rounded(val, p.get("round_to", 0))
    state.vars[name] = val
    state.measured[f["id"]] = val
    if hasattr(state, "var_kinds"):
        state.var_kinds[name] = kind_of(state, p)


def label(f, state):
    """The feature list text: '#name = value unit'."""
    p = f["params"]
    name = str(p.get("name", "")).strip().lstrip("#")
    val = state.measured.get(f["id"]) if state is not None else None
    if val is None:
        return f["name"]
    kind = getattr(state, "var_kinds", {}).get(name)
    return f"{f['name']}  ·  #{name} = {value_text(p, val, kind)}"


SHORT = {"distance": "Min dist", "angle": "Angle", "diameter": "Diameter", "radius": "Radius",
         "length": "Length", "area": "Area", "volume": "Volume", "count": "Count", "coordinate": "X",
         "centroid": "Centre", "size": "Size", "ray": "Ray dist", "inertia": "Inertia"}


def _is_solid_part(state, ref):
    p = state.parts.get(ref) if state is not None else None
    return p is None or bool(K.solids(p.shape))


def prefill(items, state=None):
    """Picks [(kind, ref)] → params for a new feature, as Onshape's measure tool would read them:
    two picks → distance (minimum), a face or a surface body → area, a solid → volume, an edge or a sketch
    curve → length (a round one → diameter), a vertex → its X coordinate.
    With the model state, surface bodies and round edges are told apart; without it parts count as solids."""
    p = dict(DEFAULTS, mtype="distance", name="d", entities=[[k, r] for k, r in items])
    kinds = [k for k, _ in items]
    if state is not None and "sketch_line" in kinds:
        # a sketch curve that is just a point measures like a vertex
        try:
            kinds = [rk for rk, _ in resolve_items(state, [[k, r] for k, r in items])]
        except K.KernelError:
            pass
    if len(items) == 2:
        p.update(mtype="distance", method="min", component="total", name="d")
    elif kinds and all(k == "part" for k in kinds):
        if all(_is_solid_part(state, r) for _, r in items):
            p.update(mtype="volume", name="vol")
        else:
            p.update(mtype="area", name="area")         # surface bodies: their area
    elif kinds and all(k in ("face", "region") for k in kinds):
        p.update(mtype="area", name="area")
    elif kinds and all(k in ("edge", "sketch_line") for k in kinds):
        p.update(mtype="length", name="len")
        if len(items) == 1 and state is not None:
            try:
                if _geom(*resolve_items(state, [list(items[0])])[0])["type"] == "circle":
                    p.update(mtype="diameter", name="dia")
            except K.KernelError:
                pass
    elif kinds == ["vertex"]:
        p.update(mtype="coordinate", axis_c="x", name="x")
    return p


def headline(state, items):
    """What the measure panel shows in its corner bar, and what its (x) button turns into a variable:
    (label, value text, params) — or None when the picks make no single measurement."""
    if not items:
        return None
    p = prefill(items, state)
    try:
        val = compute(state, p)
    except Exception:
        return None
    mtype = p["mtype"]
    label = SHORT.get(mtype, mtype)
    if mtype in ("coordinate", "centroid", "size"):
        label = p.get("axis_c", "x").upper() if mtype == "coordinate" else f"{label} {p.get('axis_c', 'x').upper()}"
    return label, quantity_text(KINDS.get(mtype, "any"), val, 3, mtype), p
