"""Geometry for plugins and scripts: a small, stable set of functions for making and combining solids.

    from freeshape import api as fs
    body = fs.box(40, 20, 10)
    body = fs.subtract(body, fs.cylinder(4, 10, at=(20, 10, 0)))

Lengths are millimetres, angles degrees. Shapes are OpenCascade (OCP) shapes, so anything in OCP works on them
too — but the functions here are the ones FreeShape promises to keep working between versions.
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K
from .kernel import KernelError, Plane  # noqa: F401  (re-exported for plugins)

__all__ = [
    "KernelError", "Plane", "plane", "box", "cylinder", "cone", "sphere", "polygon", "outline", "circle", "extrude",
    "revolve",
    "union", "subtract", "intersect", "move", "rotate", "mirror", "fillet", "chamfer", "shell", "faces", "edges",
    "solids", "volume", "bbox", "centre_of_mass", "is_valid", "save_step", "save_stl",
]


def _v(p):
    return np.asarray(p, float).reshape(3)


def plane(name_or_origin="Top", normal=None, xdir=None) -> Plane:
    """A plane: plane("Top") / "Front" / "Right", or plane(origin, normal, xdir)."""
    if isinstance(name_or_origin, str):
        return K.std_plane(name_or_origin)
    n = _v(normal if normal is not None else (0, 0, 1))
    n = n / np.linalg.norm(n)
    if xdir is None:
        xdir = (1, 0, 0) if abs(n[0]) < 0.9 else (0, 1, 0)
    x = _v(xdir) - n * (_v(xdir) @ n)
    return Plane(_v(name_or_origin), x / np.linalg.norm(x), n)


# ------------------------------------------------------------------ solids
def box(x, y, z, at=(0, 0, 0), centred=False):
    """A box x × y × z from the corner `at` (or centred on it)."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    if min(x, y, z) <= 0:
        raise KernelError("A box needs positive sizes")
    o = _v(at) - (np.array([x, y, z]) / 2 if centred else 0)
    return BRepPrimAPI_MakeBox(K._pnt(o), float(x), float(y), float(z)).Shape()


def cylinder(radius, height, at=(0, 0, 0), axis=(0, 0, 1)):
    if radius <= 0 or height <= 0:
        raise KernelError("A cylinder needs a positive radius and height")
    return K.cylinder(_v(at), _v(axis), radius, height)


def cone(r1, r2, height, at=(0, 0, 0), axis=(0, 0, 1)):
    return K.cone(_v(at), _v(axis), r1, r2, height)


def sphere(radius, at=(0, 0, 0)):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    return BRepPrimAPI_MakeSphere(K._pnt(_v(at)), float(radius)).Shape()


# ------------------------------------------------------------------ flat profiles
def polygon(points, on=None):
    """A flat face from 2D points (u, v) on a plane (Top by default), or from 3D points when `on` is None and the
    points have three coordinates."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    pts = [np.asarray(p, float) for p in points]
    if len(pts) < 3:
        raise KernelError("A polygon needs at least three points")
    if len(pts[0]) == 2:
        pl = on if isinstance(on, Plane) else K.std_plane(on or "Top")
        pts = [pl.to_world(*p) for p in pts]
    mp = BRepBuilderAPI_MakePolygon()
    for p in pts:
        mp.Add(K._pnt(p))
    mp.Close()
    mf = BRepBuilderAPI_MakeFace(mp.Wire(), True)
    if not mf.IsDone():
        raise KernelError("The polygon isn't flat or crosses itself")
    return mf.Face()


def outline(segments, on=None):
    """A flat face from a closed chain of segments, each a list of 2D points on a plane (Top by default): two
    points make a straight line, more make a smooth curve through them. Each segment starts where the last
    one ended; the chain closes back to the first point.

        fs.outline([[(0, 0), (40, 0)], [(40, 0), (50, 10), (40, 20)], [(40, 20), (0, 20)]])
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
    from OCP.GeomAPI import GeomAPI_PointsToBSpline
    try:
        from OCP.TColgp import TColgp_Array1OfPnt
    except ImportError:  # OCP >= 8 exposes NCollection templates directly
        from OCP.OCP.collections import Array1_gp_Pnt as TColgp_Array1OfPnt
    pl = on if isinstance(on, Plane) else K.std_plane(on or "Top")
    segs = [[np.asarray(p, float) for p in s] for s in segments if len(s) >= 2]
    if not segs:
        raise KernelError("An outline needs at least one segment")
    first = segs[0][0]
    if np.linalg.norm(segs[-1][-1] - first) > 1e-9:
        segs.append([segs[-1][-1], first])
    mw = BRepBuilderAPI_MakeWire()
    for s in segs:
        w = [pl.to_world(*p) for p in s]
        if len(w) == 2:
            if np.linalg.norm(w[1] - w[0]) < 1e-9:
                continue
            e = BRepBuilderAPI_MakeEdge(K._pnt(w[0]), K._pnt(w[1])).Edge()
        else:
            arr = TColgp_Array1OfPnt(1, len(w))
            for i, q in enumerate(w, 1):
                arr.SetValue(i, K._pnt(q))
            e = BRepBuilderAPI_MakeEdge(GeomAPI_PointsToBSpline(arr, 3, 8).Curve()).Edge()
        mw.Add(e)
    if not mw.IsDone():
        raise KernelError("The outline's segments don't join up end to end")
    mf = BRepBuilderAPI_MakeFace(mw.Wire(), True)
    if not mf.IsDone():
        raise KernelError("The outline isn't flat or crosses itself")
    return mf.Face()


def circle(radius, centre=(0, 0), on=None):
    """A flat round face on a plane (Top by default)."""
    from OCP.gp import gp_Ax2, gp_Circ
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
    pl = on if isinstance(on, Plane) else K.std_plane(on or "Top")
    c = pl.to_world(*centre)
    circ = gp_Circ(gp_Ax2(K._pnt(c), K._dir(pl.normal), K._dir(pl.xdir)), float(radius))
    w = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(circ).Edge()).Wire()
    return BRepBuilderAPI_MakeFace(w, True).Face()


def extrude(face, height, direction=None):
    """A solid from a flat face (or a list of them), pushed along its normal (or `direction`) by height."""
    fs_ = face if isinstance(face, (list, tuple)) else [face]
    if direction is None:
        n = K.planar_face_plane(fs_[0])
        if n is None:
            raise KernelError("Only flat faces can be extruded without a direction")
        direction = n.normal
    d = _v(direction)
    return K.extrude(list(fs_), d / np.linalg.norm(d), float(height))


def revolve(face, angle=360.0, axis=(0, 0, 1), about=(0, 0, 0)):
    fs_ = face if isinstance(face, (list, tuple)) else [face]
    return K.revolve(list(fs_), _v(about), _v(axis), float(angle))


# ------------------------------------------------------------------ combining and moving
def union(*shapes):
    shapes = [s for s in (shapes[0] if len(shapes) == 1 and isinstance(shapes[0], (list, tuple)) else shapes)]
    return K.fuse_all(shapes)


def subtract(shape, *tools):
    for t in tools:
        shape = K.cut(shape, t)
    return shape


def intersect(a, b):
    return K.common(a, b)


def move(shape, dx=0.0, dy=0.0, dz=0.0):
    return K.translate(shape, (dx, dy, dz))


def rotate(shape, angle, axis=(0, 0, 1), about=(0, 0, 0)):
    return K.rotate(shape, _v(about), _v(axis), float(angle))


def mirror(shape, about="Right"):
    return K.mirror(shape, about if isinstance(about, Plane) else K.std_plane(about))


def fillet(shape, edge_list, radius):
    return K.fillet(shape, list(edge_list), float(radius))


def chamfer(shape, edge_list, distance):
    return K.chamfer(shape, list(edge_list), float(distance))


def shell(shape, open_faces, thickness, outward=False):
    return K.shell(shape, list(open_faces), float(thickness), outward)


# ------------------------------------------------------------------ looking at shapes
def faces(shape):
    return K.faces(shape)


def edges(shape):
    return K.edges(shape)


def solids(shape):
    return K.solids(shape)


def volume(shape) -> float:
    return K.volume(shape)


def bbox(shape):
    """((xmin, ymin, zmin), (xmax, ymax, zmax))"""
    lo, hi = K.bbox(shape)
    return tuple(map(float, lo)), tuple(map(float, hi))


def centre_of_mass(shape):
    return tuple(map(float, K.mass_props(shape)["centroid"]))


def is_valid(shape) -> bool:
    return K.is_valid(shape)


def save_step(shapes, path):
    K.export_step(list(shapes) if isinstance(shapes, (list, tuple)) else [shapes], path)


def save_stl(shapes, path):
    K.export_stl(list(shapes) if isinstance(shapes, (list, tuple)) else [shapes], path)


# a few handy numbers for gear / pattern plugins
TAU = 2 * math.pi
