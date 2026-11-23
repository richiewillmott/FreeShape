"""Selection helpers on a part's faces and edges (indices in kernel.faces() / kernel.edges() order), for the
right-click selection tools: tangent-connected faces or edges, an edge's loop, a face's edges, and faces or edges
of the same size (every hole of one size, say)."""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K

_CACHE: dict = {}


class Topology:
    """Which faces each edge bounds, which edges each face has, and which edges meet at each vertex."""

    def __init__(self, shape):
        from OCP.TopExp import TopExp
        TopTools_IndexedMapOfShape = K.TopTools_IndexedMapOfShape
        from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX, TopAbs_WIRE
        from OCP.TopExp import TopExp_Explorer
        self.shape = shape
        self.faces = K.faces(shape)
        self.edges = K.edges(shape)
        emap, vmap = TopTools_IndexedMapOfShape(), TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(shape, TopAbs_EDGE, emap)
        TopExp.MapShapes_s(shape, TopAbs_VERTEX, vmap)
        self.face_edges: list[list[int]] = []
        self.face_wires: list[list[list[int]]] = []
        self.edge_faces: list[list[int]] = [[] for _ in self.edges]
        for fi, f in enumerate(self.faces):
            wires = []
            exp = TopExp_Explorer(f, TopAbs_WIRE)
            while exp.More():
                ids = []
                ex2 = TopExp_Explorer(exp.Current(), TopAbs_EDGE)
                while ex2.More():
                    k = emap.FindIndex(ex2.Current()) - 1
                    if k >= 0 and k not in ids:
                        ids.append(k)
                    ex2.Next()
                wires.append(ids)
                exp.Next()
            self.face_wires.append(wires)
            es = sorted({k for w in wires for k in w})
            self.face_edges.append(es)
            for k in es:
                if fi not in self.edge_faces[k]:
                    self.edge_faces[k].append(fi)
        self.edge_verts: list[list[int]] = []
        self.vert_edges: dict[int, list[int]] = {}
        for ei, e in enumerate(self.edges):
            vs = []
            exp = TopExp_Explorer(e, TopAbs_VERTEX)
            while exp.More():
                k = vmap.FindIndex(exp.Current()) - 1
                if k >= 0 and k not in vs:
                    vs.append(k)
                exp.Next()
            self.edge_verts.append(vs)
            for v in vs:
                self.vert_edges.setdefault(v, []).append(ei)
        self.vertices = [K._as_vertex(vmap.FindKey(i)) for i in range(1, vmap.Extent() + 1)]
        self._classes = None

    @property
    def classes(self):
        if self._classes is None:
            self._classes = K.edge_classes(self.shape)
        return self._classes


def topology(shape) -> Topology:
    key = id(shape)
    hit = _CACHE.get(key)
    if hit is None or hit.shape is not shape:
        if len(_CACHE) > 32:
            _CACHE.clear()
        hit = _CACHE[key] = Topology(shape)
    return hit


def _flood(start, neighbours):
    seen, todo = {start}, [start]
    while todo:
        cur = todo.pop()
        for n in neighbours(cur):
            if n not in seen:
                seen.add(n)
                todo.append(n)
    return sorted(seen)


def tangent_faces(shape, fi: int) -> list[int]:
    """The face and every face joined to it smoothly, and on through their smooth joins (a fillet chain)."""
    t = topology(shape)
    cls = t.classes

    def nb(f):
        for e in t.face_edges[f]:
            if cls[e] == "tangent":
                yield from (g for g in t.edge_faces[e] if g != f)
    return _flood(fi, nb)


def connected_faces(shape, fi: int) -> list[int]:
    """The face and the faces that share an edge with it."""
    t = topology(shape)
    return sorted({fi} | {g for e in t.face_edges[fi] for g in t.edge_faces[e]})


def face_edges(shape, fi: int) -> list[int]:
    return list(topology(shape).face_edges[fi])


def _tangent_at(edge, vertex):
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec
    ad = BRepAdaptor_Curve(edge)
    p, v = gp_Pnt(), gp_Vec()
    ad.D1(BRep_Tool.Parameter_s(vertex, edge), p, v)
    d = np.array([v.X(), v.Y(), v.Z()])
    n = np.linalg.norm(d)
    return d / n if n > 1e-12 else None


def tangent_edges(shape, ei: int, ang_deg: float = 1.0) -> list[int]:
    """The edge and every edge that carries on smoothly from it at a shared end (round a fillet, say)."""
    t = topology(shape)
    cos_tol = math.cos(math.radians(ang_deg))

    def nb(e):
        for v in t.edge_verts[e]:
            a = _tangent_at(t.edges[e], t.vertices[v])
            if a is None:
                continue
            for o in t.vert_edges.get(v, []):
                if o == e:
                    continue
                b = _tangent_at(t.edges[o], t.vertices[v])
                if b is not None and abs(float(a @ b)) >= cos_tol:
                    yield o
    return _flood(ei, nb)


def edge_loop(shape, ei: int) -> list[int]:
    """The closed loop of edges the edge is part of, round one of the faces it bounds: a flat face's loop first
    (a hole's rim, the outline of a top face), else the shortest loop."""
    t = topology(shape)
    best = None
    for fi in t.edge_faces[ei]:
        flat = K.face_info(t.faces[fi]).get("type") == int(K.GeomAbs_Plane)
        for w in t.face_wires[fi]:
            if ei in w:
                key = (0 if flat else 1, len(w))
                if best is None or key < best[0]:
                    best = (key, w)
    return sorted(best[1]) if best else [ei]


def _close(a, b, rel=1e-6):
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


def same_faces(shape, fi: int) -> list[int]:
    """Faces like this one: the same kind of surface and size (a cylinder's radius and length, a flat face's
    area) — every hole of one size, every face of one size."""
    t = topology(shape)
    ref = K.face_info(t.faces[fi])
    out = []
    for gi, g in enumerate(t.faces):
        info = K.face_info(g)
        if info.get("type") != ref.get("type"):
            continue
        if "r" in ref and not _close(info.get("r", -1), ref["r"]):
            continue
        if not _close(info["area"], ref["area"], 1e-5):
            continue
        out.append(gi)
    return out


def same_edges(shape, ei: int) -> list[int]:
    """Edges like this one: the same kind of curve and length (and radius, for circles and arcs)."""
    t = topology(shape)
    ref = K.edge_info(t.edges[ei])
    out = []
    for k, e in enumerate(t.edges):
        info = K.edge_info(e)
        if info.get("type") != ref.get("type"):
            continue
        if "r" in ref and not _close(info.get("r", -1), ref["r"]):
            continue
        if not _close(info["len"], ref["len"], 1e-5):
            continue
        out.append(k)
    return out
