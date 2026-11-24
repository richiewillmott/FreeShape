"""Mate connectors in a Part Studio (Onshape's Mate connector feature): a frame — an origin with X, Y and Z
directions — placed on a face, edge or vertex of a part.

Each kind of geometry offers several points to put it at (a flat face: its middle, its corners, the middles of its
edges and the centres of its round edges; a round face: its axis at the middle or at either end; …). In the dialog,
Shift steps through them without changing the face. The frame can then be flipped, turned about its Z axis and
moved along its own axes.

Mate connectors are drawn as small X / Y / Z triads, and other features use them: Transform (move one mate connector
onto another) and Point pattern (a copy at each).
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K


def _unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def _perp(z):
    """An X direction square to z: along world X if it can be, else world Y."""
    z = _unit(z)
    for cand in ((1.0, 0, 0), (0, 1.0, 0)):
        c = np.array(cand)
        x = c - (c @ z) * z
        if np.linalg.norm(x) > 1e-6:
            return _unit(x)
    return np.array([1.0, 0, 0])


def _index(subs, sub):
    for i, s in enumerate(subs):
        if s.IsSame(sub):
            return i
    return None


def candidates(state, item):
    """The points a mate connector can go at on the picked face / edge / vertex: [{"name", "o", "z", "x"}] (first
    one: the default). `item` is {"kind": "face" | "edge" | "vertex", "ref": …}."""
    from . import assembly as A
    from . import features as F
    kind, ref = item.get("kind"), item.get("ref")
    if kind == "vertex":
        q = F.resolve_point(state, ref)
        return [{"name": "The vertex", "o": q, "z": np.array([0, 0, 1.0]), "x": np.array([1.0, 0, 0])}]
    if kind not in ("face", "edge"):
        raise K.KernelError("Pick a face, edge or vertex for the mate connector")
    pid, sub = F.resolve(state, ref, kind)
    shape = state.parts[pid].shape
    idx = _index(K.faces(shape) if kind == "face" else K.edges(shape), sub)
    if idx is None:
        raise K.KernelError("Missing mate connector reference")
    base = A.connector_frame(shape, kind, idx)
    z0, x0 = _unit(base["z"]), _unit(base["x"])
    out = []

    def add(name, o, z=None, x=None):
        z = z0 if z is None else _unit(z)
        x = x0 if x is None else _unit(x)
        x = x - (x @ z) * z
        x = _unit(x) if np.linalg.norm(x) > 1e-6 else _perp(z)
        o = np.asarray(o, float)
        if any(np.linalg.norm(c["o"] - o) < 1e-6 and np.allclose(c["z"], z) for c in out):
            return
        out.append({"name": name, "o": o, "z": z, "x": x})

    if kind == "face":
        what = base.get("what", "")
        if what.startswith("round face"):
            # a cylinder or cone: its axis at the middle, then at each end (the centres of its round edges),
            # pointing out of that end
            mid = np.asarray(base["o"], float)
            add("Axis, middle", mid)
            ends = []
            for e in K.edges(sub):
                info = K.edge_info(e)
                if "center" in info and "axis" in info:
                    ends.append(np.asarray(info["center"], float))
            for k, c in enumerate(sorted(ends, key=lambda c: float((c - mid) @ z0))):
                way = 1.0 if float((c - mid) @ z0) >= 0 else -1.0
                add(f"Axis, end {k + 1}", c, z0 * way)
        else:
            add("Middle of the face", base["o"])
            for k, e in enumerate(K.edges(sub)):
                info = K.edge_info(e)
                if "center" in info and "axis" in info:
                    add(f"Centre of round edge {k + 1}", info["center"])
            for k, e in enumerate(K.edges(sub)):
                info = K.edge_info(e)
                if "p0" in info and "p1" in info and "center" not in info:
                    add(f"Middle of edge {k + 1}", (np.asarray(info["p0"]) + np.asarray(info["p1"])) / 2)
            for k, v in enumerate(K._vertices_np(sub)):
                add(f"Corner {k + 1}", v)
        return out
    # an edge
    info = K.edge_info(sub)
    if "center" in info and "axis" in info:
        add("Centre of the round edge", info["center"])
    elif "p0" in info and "p1" in info:
        a, b = np.asarray(info["p0"], float), np.asarray(info["p1"], float)
        add("Middle of the edge", (a + b) / 2)
        add("Start of the edge", a)
        add("End of the edge", b)
    else:
        add("On the edge", base["o"])
    return out


def build(state, p):
    """The world frame a Mate connector feature makes: {"o", "x", "y", "z", "name"}."""
    on = p.get("on")
    if on:
        cands = candidates(state, on)
        if not cands:
            raise K.KernelError("No place for a mate connector there")
        c = cands[int(p.get("point", 0) or 0) % len(cands)]
        o, z, x, name = c["o"], c["z"], c["x"], c["name"]
    else:
        o, z, x, name = np.zeros(3), np.array([0, 0, 1.0]), np.array([1.0, 0, 0]), "The origin"
    if p.get("flip"):                                 # flip the Z axis over (about X)
        z = -z
    ang = math.radians(float(p.get("rotate", 0) or 0))
    if abs(ang) > 1e-12:
        y = np.cross(z, x)
        x = x * math.cos(ang) + y * math.sin(ang)
    y = np.cross(z, x)
    o = (np.asarray(o, float) + x * float(p.get("dx", 0) or 0) + y * float(p.get("dy", 0) or 0)
         + z * float(p.get("dz", 0) or 0))
    return {"o": o, "x": _unit(x), "y": _unit(y), "z": _unit(z), "name": name}


def frame_matrix(fr):
    """A 4x4 matrix taking the frame's own coordinates to world coordinates."""
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = fr["x"], fr["y"], fr["z"], fr["o"]
    return M


def move_between(a, b):
    """The rigid move taking mate connector frame `a` onto frame `b` (origins together, axes lined up), as a
    function shape -> moved shape."""
    M = frame_matrix(b) @ np.linalg.inv(frame_matrix(a))
    return lambda s_: K.transform_matrix(s_, M)
