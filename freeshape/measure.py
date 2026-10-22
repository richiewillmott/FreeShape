"""Measurements between selected geometry, like Onshape's measure panel.

measure(items) takes a list of (kind, shape) with kind in face / edge / part / region
and returns {"title", "rows": [(label, value)], "line": (p1, p2) or None}.
"""
from __future__ import annotations

import math

import numpy as np
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Cylinder, GeomAbs_Line, GeomAbs_Plane

from . import kernel as K
from .expr import fmt

PLA, ALU, STEEL = 1.24e-3, 2.70e-3, 7.85e-3     # g / mm³


def _v(p):
    return np.array([p.X(), p.Y(), p.Z()])


def _geom(kind, shape):
    """Simple geometric description: plane / line / circle / cylinder / other."""
    if kind in ("face", "region"):
        ad = BRepAdaptor_Surface(shape)
        t = ad.GetType()
        if t == GeomAbs_Plane:
            pl = K.planar_face_plane(shape)
            return {"type": "plane", "p": pl.origin, "n": pl.normal}
        if t == GeomAbs_Cylinder:
            cy = ad.Cylinder()
            return {"type": "cylinder", "p": _v(cy.Axis().Location()), "d": _v(cy.Axis().Direction()),
                    "r": cy.Radius()}
    if kind == "edge":
        ad = BRepAdaptor_Curve(shape)
        t = ad.GetType()
        if t == GeomAbs_Line:
            ln = ad.Line()
            return {"type": "line", "p": _v(ln.Location()), "d": _v(ln.Direction())}
        if t == GeomAbs_Circle:
            ci = ad.Circle()
            return {"type": "circle", "p": _v(ci.Location()), "d": _v(ci.Axis().Direction()), "r": ci.Radius()}
    return {"type": "other"}


def _angle(a, b, acute=True):
    c = float(np.clip(abs(a @ b) if acute else a @ b, -1, 1))
    return math.degrees(math.acos(c))


def _min_dist(a, b):
    d = BRepExtrema_DistShapeShape(a, b)
    d.Perform()
    if not d.IsDone() or d.NbSolution() == 0:
        return None
    return d.Value(), _v(d.PointOnShape1(1)), _v(d.PointOnShape2(1))


def _single(kind, shape, name=None):
    rows = []
    g = _geom(kind, shape)
    if kind in ("face", "region"):
        info = K.face_info(shape)
        rows.append(("Area", f"{fmt(info['area'], 3)} mm²"))
        if g["type"] == "cylinder":
            rows.append(("Diameter", f"{fmt(2 * g['r'], 3)} mm"))
            rows.append(("Radius", f"{fmt(g['r'], 3)} mm"))
        if g["type"] == "plane":
            n = g["n"]
            rows.append(("Normal", f"({fmt(n[0], 3)}, {fmt(n[1], 3)}, {fmt(n[2], 3)})"))
        return {"title": "Sketch region" if kind == "region" else "Face", "rows": rows, "line": None}
    if kind == "edge":
        info = K.edge_info(shape)
        rows.append(("Length", f"{fmt(info['len'], 3)} mm"))
        if g["type"] == "circle":
            rows.append(("Diameter", f"{fmt(2 * g['r'], 3)} mm"))
            rows.append(("Radius", f"{fmt(g['r'], 3)} mm"))
            c = g["p"]
            rows.append(("Centre", f"({fmt(c[0], 3)}, {fmt(c[1], 3)}, {fmt(c[2], 3)})"))
        if g["type"] == "line":
            p0, p1 = np.array(info["p0"]), np.array(info["p1"])
            dv = np.abs(p1 - p0)
            rows.append(("ΔX · ΔY · ΔZ", f"{fmt(dv[0], 3)} · {fmt(dv[1], 3)} · {fmt(dv[2], 3)}"))
        return {"title": "Edge", "rows": rows, "line": None}
    mp = K.mass_props(shape)
    lo, hi = K.bbox(shape)
    size = hi - lo
    v = mp["volume"]
    rows += [("Volume", f"{fmt(v, 2)} mm³"), ("Surface area", f"{fmt(mp['area'], 2)} mm²"),
             ("Size", f"{fmt(size[0], 2)} × {fmt(size[1], 2)} × {fmt(size[2], 2)} mm"),
             ("Mass", f"PLA {fmt(v * PLA, 1)} g · Al {fmt(v * ALU, 1)} g · steel {fmt(v * STEEL, 1)} g")]
    c = mp["centroid"]
    rows.append(("Centre of mass", f"({fmt(c[0], 2)}, {fmt(c[1], 2)}, {fmt(c[2], 2)})"))
    return {"title": name or "Part", "rows": rows, "line": None}


def measure(items, names=None):
    items = [(k, s) for k, s in items if s is not None]
    if not items:
        return None
    if len(items) == 1:
        return _single(items[0][0], items[0][1], (names or [None])[0])
    if len(items) == 2:
        (ka, a), (kb, b) = items
        ga, gb = _geom(ka, a), _geom(kb, b)
        rows = []
        line = None
        md = _min_dist(a, b)
        if md:
            d, p1, p2 = md
            dv = np.abs(p2 - p1)
            rows.append(("Minimum distance", f"{fmt(d, 3)} mm"))
            rows.append(("ΔX · ΔY · ΔZ", f"{fmt(dv[0], 3)} · {fmt(dv[1], 3)} · {fmt(dv[2], 3)}"))
            if d > 1e-9:
                line = (p1, p2)
        # centres of circles / cylinders
        ca = ga["p"] if ga["type"] in ("circle",) else None
        cb = gb["p"] if gb["type"] in ("circle",) else None
        if ca is not None and cb is not None:
            rows.append(("Centre to centre", f"{fmt(float(np.linalg.norm(cb - ca)), 3)} mm"))
            line = (ca, cb)
        if ga["type"] == "cylinder" and gb["type"] == "cylinder" and _angle(ga["d"], gb["d"]) < 1e-3:
            w = gb["p"] - ga["p"]
            perp = w - (w @ ga["d"]) * ga["d"]
            rows.append(("Axis to axis", f"{fmt(float(np.linalg.norm(perp)), 3)} mm"))
        # angles and parallel distances
        dirs = {"plane": "n", "line": "d", "cylinder": "d", "circle": "d"}
        if ga["type"] in dirs and gb["type"] in dirs:
            da, db = ga[dirs[ga["type"]]], gb[dirs[gb["type"]]]
            plane_line = {ga["type"], gb["type"]} == {"plane", "line"}
            ang = 90 - _angle(da, db) if plane_line else _angle(da, db)
            rows.append(("Angle", f"{fmt(ang, 3)}°"))
            if ga["type"] == gb["type"] == "plane" and _angle(da, db) < 1e-4:
                rows.append(("Plane to plane", f"{fmt(abs(float((gb['p'] - ga['p']) @ da)), 3)} mm"))
        return {"title": "Measurement", "rows": rows, "line": line}
    kinds = {k for k, _ in items}
    if kinds <= {"face", "region"}:
        tot = sum(K.face_info(s)["area"] for _, s in items)
        return {"title": f"{len(items)} faces", "rows": [("Total area", f"{fmt(tot, 3)} mm²")], "line": None}
    if kinds == {"edge"}:
        tot = sum(K.edge_info(s)["len"] for _, s in items)
        return {"title": f"{len(items)} edges", "rows": [("Total length", f"{fmt(tot, 3)} mm")], "line": None}
    if kinds == {"part"}:
        tot = sum(K.volume(s) for _, s in items)
        return {"title": f"{len(items)} parts", "rows": [("Total volume", f"{fmt(tot, 2)} mm³"),
                                                         ("Mass (PLA)", f"{fmt(tot * PLA, 1)} g")], "line": None}
    return {"title": f"{len(items)} items selected", "rows": [], "line": None}


def to_html(m):
    if not m:
        return ""
    rows = "".join(f"<tr><td style='color:#6a737d; padding-right:10px'>{a}</td><td><b>{b}</b></td></tr>"
                   for a, b in m["rows"])
    return f"<b>{m['title']}</b><table cellspacing=0 cellpadding=1>{rows}</table>"
