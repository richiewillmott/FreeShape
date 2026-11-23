"""Bring a DXF drawing into a sketch: lines, circles, arcs, polylines (with their bulges), ellipses, splines and
points. Blocks are exploded. Ends that meet in the file are joined in the sketch so closed outlines make
regions. Geometry comes in unconstrained, in mm, with the file's origin on the sketch origin."""
from __future__ import annotations

import math

import numpy as np

from .sketch import Sketch

# $INSUNITS -> mm
_UNITS = {1: 25.4, 2: 304.8, 3: 1609344.0, 4: 1.0, 5: 10.0, 6: 1000.0, 8: 0.0000254, 9: 0.0254, 10: 914.4,
          14: 100.0, 15: 10000.0}


def units_scale(doc) -> float:
    """mm per drawing unit, from the file's $INSUNITS (unitless files are taken as mm)."""
    try:
        return _UNITS.get(int(doc.header.get("$INSUNITS", 0)), 1.0)
    except (TypeError, ValueError):
        return 1.0


class _Points:
    """Sketch points shared by ends that land in the same place."""

    def __init__(self, sk, tol):
        self.sk, self.tol, self.cells = sk, tol, {}

    def get(self, xy):
        x, y = float(xy[0]), float(xy[1])
        key = (round(x / self.tol), round(y / self.tol))
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                pid = self.cells.get((key[0] + dx, key[1] + dy))
                if pid is not None and math.hypot(*(np.subtract(self.sk.points[pid], (x, y)))) <= self.tol:
                    return pid
        pid = self.sk.add_point(x, y)
        self.cells[key] = pid
        return pid


def _flat(entities, depth=0):
    """Explode blocks and polylines into simple entities."""
    for e in entities:
        t = e.dxftype()
        if t in ("INSERT", "LWPOLYLINE", "POLYLINE") and depth < 8:
            try:
                yield from _flat(e.virtual_entities(), depth + 1)
            except Exception:
                continue
        else:
            yield e


def import_dxf(sk: Sketch, path: str, scale: float | None = None, construction=False) -> dict:
    """Add the drawing's geometry to `sk`. scale: mm per drawing unit (default: from the file's units).
    Returns {"added": count, "skipped": {dxf type: count}, "scale": scale used}."""
    import ezdxf
    doc = ezdxf.readfile(path)
    k = units_scale(doc) if scale is None else float(scale)
    pts = _Points(sk, 1e-6 * max(k, 1e-9) * 1000)

    def P(v):
        return pts.get((v[0] * k, v[1] * k))

    added, skipped = 0, {}
    for e in _flat(doc.modelspace()):
        t = e.dxftype()
        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                if math.hypot(b[0] - a[0], b[1] - a[1]) * k < 1e-9:
                    continue
                pa, pb = P(a), P(b)
                if pa != pb:
                    sk.add_line(pa, pb, construction)
                    added += 1
            elif t == "CIRCLE":
                c = e.ocs().to_wcs(e.dxf.center)
                sk.add_circle(sk.add_point(c[0] * k, c[1] * k), e.dxf.radius * k, construction)
                added += 1
            elif t == "ARC":
                c = e.ocs().to_wcs(e.dxf.center)
                s, f = e.start_point, e.end_point
                if e.dxf.extrusion[2] < 0:            # seen from below: it runs clockwise, so swap its ends
                    s, f = f, s
                sk.add_arc(sk.add_point(c[0] * k, c[1] * k), P(s), P(f), construction)
                added += 1
            elif t == "ELLIPSE":
                c, ax = e.dxf.center, e.dxf.major_axis
                ctr = sk.add_point(c[0] * k, c[1] * k)
                tip = sk.add_point((c[0] + ax[0]) * k, (c[1] + ax[1]) * k)
                r = math.hypot(ax[0], ax[1]) * e.dxf.ratio * k
                span = (e.dxf.end_param - e.dxf.start_param) % (2 * math.pi)
                if span < 1e-9 or abs(span - 2 * math.pi) < 1e-9:
                    sk.add_ellipse(ctr, tip, r, construction)
                else:
                    s, f = e.start_point, e.end_point
                    if e.dxf.extrusion[2] < 0:
                        s, f = f, s
                    sk.add_ellipse_arc(ctr, tip, r, P(s), P(f), construction)
                sk.add_line(ctr, tip, True)               # its long axis, as construction (as the Ellipse tool)
                added += 1
            elif t == "SPLINE":
                closed = bool(e.closed)
                fit = [tuple(q) for q in e.fit_points]
                if len(fit) >= 2:
                    ids = [P(q) for q in fit]
                    sk.add_spline(ids[:-1] if closed and ids[0] == ids[-1] else ids, closed, construction)
                else:                                     # control points: a curve through points along it
                    flat = [tuple(q) for q in e.construction_tool().flattening(0.01 / max(k, 1e-9))]
                    if len(flat) < 2:
                        continue
                    step = max(1, len(flat) // 40)
                    keep = flat[::step]
                    if keep[-1] != flat[-1]:
                        keep.append(flat[-1])
                    ids = []
                    for q in keep:
                        pid = P(q)
                        if not ids or ids[-1] != pid:
                            ids.append(pid)
                    if closed and len(ids) > 2 and ids[0] == ids[-1]:
                        ids.pop()
                    if len(ids) < 2:
                        continue
                    sk.add_spline(ids, closed and len(ids) > 2, construction)
                added += 1
            elif t == "POINT":
                sk.add_point_entity(P(e.dxf.location))
                added += 1
            else:
                skipped[t] = skipped.get(t, 0) + 1
        except Exception:
            skipped[t] = skipped.get(t, 0) + 1
    sk.solve()
    return {"added": added, "skipped": skipped, "scale": k}
