"""Standard parts, made on the spot from ISO / DIN size tables — no downloads, works offline.

Every part is described by a small dict (its "source" in an assembly), e.g.
    {"kind": "std", "family": "iso4762", "size": "M5", "length": 16}
and `build(spec)` makes its solid. Threads are shown as plain cylinders at the nominal diameter, as most CAD
libraries do; dimensions follow the standards' nominal values.

Placement, so a mate on the obvious face just works:
* screws and bolts: axis along Z, the underside of the head on z = 0, head above, shank going down (-Z);
* nuts, washers, bearings, inserts: axis along Z, sitting on z = 0, going up.
"""
from __future__ import annotations

import math

import numpy as np

from . import kernel as K

# --------------------------------------------------------------------------- size tables (mm)
# ISO 4762 socket head cap screw: d -> (head diameter dk, head height k, hex socket s, socket depth t)
ISO4762 = {"M2": (3.8, 2.0, 1.5, 1.0), "M2.5": (4.5, 2.5, 2.0, 1.1), "M3": (5.5, 3.0, 2.5, 1.3),
           "M4": (7.0, 4.0, 3.0, 2.0), "M5": (8.5, 5.0, 4.0, 2.5), "M6": (10.0, 6.0, 5.0, 3.0),
           "M8": (13.0, 8.0, 6.0, 4.0), "M10": (16.0, 10.0, 8.0, 5.0), "M12": (18.0, 12.0, 10.0, 6.0),
           "M16": (24.0, 16.0, 14.0, 8.0), "M20": (30.0, 20.0, 17.0, 10.0)}
# ISO 10642 countersunk socket screw: d -> (head diameter dk, socket s, socket depth t); 90° head
ISO10642 = {"M3": (6.72, 2.0, 1.1), "M4": (8.96, 2.5, 1.5), "M5": (11.2, 3.0, 1.9), "M6": (13.44, 4.0, 2.2),
            "M8": (17.92, 5.0, 3.0), "M10": (22.4, 6.0, 3.6), "M12": (26.88, 8.0, 4.3)}
# ISO 4017 hex head screw: d -> (across flats s, head height k)
ISO4017 = {"M3": (5.5, 2.0), "M4": (7.0, 2.8), "M5": (8.0, 3.5), "M6": (10.0, 4.0), "M8": (13.0, 5.3),
           "M10": (16.0, 6.4), "M12": (18.0, 7.5), "M16": (24.0, 10.0), "M20": (30.0, 12.5)}
# ISO 4032 hex nut: d -> (across flats s, height m)
ISO4032 = {"M2": (4.0, 1.6), "M2.5": (5.0, 2.0), "M3": (5.5, 2.4), "M4": (7.0, 3.2), "M5": (8.0, 4.7),
           "M6": (10.0, 5.2), "M8": (13.0, 6.8), "M10": (16.0, 8.4), "M12": (18.0, 10.8), "M16": (24.0, 14.8),
           "M20": (30.0, 18.0)}
# ISO 7089 plain washer: d -> (inner d1, outer d2, thickness h)
ISO7089 = {"M2": (2.2, 5.0, 0.3), "M2.5": (2.7, 6.0, 0.5), "M3": (3.2, 7.0, 0.5), "M4": (4.3, 9.0, 0.8),
           "M5": (5.3, 10.0, 1.0), "M6": (6.4, 12.0, 1.6), "M8": (8.4, 16.0, 1.6), "M10": (10.5, 20.0, 2.0),
           "M12": (13.0, 24.0, 2.5), "M16": (17.0, 30.0, 3.0), "M20": (21.0, 37.0, 3.0)}
# ISO 8734 dowel pin: diameters (mm)
ISO8734 = ["2", "2.5", "3", "4", "5", "6", "8", "10", "12"]
# deep-groove ball bearings: designation -> (bore d, outside D, width B)
BEARINGS = {"623": (3, 10, 4), "624": (4, 13, 5), "625": (5, 16, 5), "626": (6, 19, 6), "688": (8, 16, 5),
            "608": (8, 22, 7), "6800": (10, 19, 5), "6000": (10, 26, 8), "6001": (12, 28, 8), "6002": (15, 32, 9),
            "6003": (17, 35, 10), "6200": (10, 30, 9), "6201": (12, 32, 10), "6202": (15, 35, 11),
            "6203": (17, 40, 12), "6204": (20, 47, 14), "6205": (25, 52, 15)}
# heat-set threaded inserts for plastic (common sizes): d -> (outer diameter, length)
INSERTS = {"M2": (3.6, 3.0), "M2.5": (4.0, 4.0), "M3": (4.6, 4.0), "M4": (5.6, 5.0), "M5": (6.4, 6.0),
           "M6": (8.0, 8.0)}

SCREW_LENGTHS = [3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 80, 90, 100, 110, 120]
PIN_LENGTHS = [4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 24, 30, 35, 40, 45, 50, 60]

FAMILIES = {
    # family: (label, sizes, lengths or None, colour)
    "iso4762": ("Socket head cap screw — ISO 4762", list(ISO4762), SCREW_LENGTHS, (0.25, 0.26, 0.28)),
    "iso10642": ("Countersunk socket screw — ISO 10642", list(ISO10642), SCREW_LENGTHS, (0.25, 0.26, 0.28)),
    "iso4017": ("Hex head screw — ISO 4017", list(ISO4017), SCREW_LENGTHS, (0.66, 0.68, 0.71)),
    "iso4032": ("Hex nut — ISO 4032", list(ISO4032), None, (0.66, 0.68, 0.71)),
    "iso7089": ("Plain washer — ISO 7089", list(ISO7089), None, (0.72, 0.74, 0.77)),
    "iso8734": ("Dowel pin — ISO 8734", ISO8734, PIN_LENGTHS, (0.70, 0.72, 0.75)),
    "bearing": ("Deep groove ball bearing", list(BEARINGS), None, (0.62, 0.64, 0.68)),
    "insert": ("Heat-set threaded insert", list(INSERTS), None, (0.80, 0.66, 0.36)),
}


def _d(size):
    return float(str(size).lstrip("M"))


def lengths_for(family, size):
    """Lengths that make sense for a screw / pin of this size (2× to 12× the diameter, within the standard
    range), or None for parts without a length."""
    lens = FAMILIES[family][2]
    if lens is None:
        return None
    d = _d(size)
    ok = [L for L in lens if 1.5 * d <= L <= max(12 * d, 20)]
    return ok or lens


def name(spec):
    fam, size = spec["family"], spec["size"]
    L = spec.get("length")
    if fam == "bearing":
        d, D, B = BEARINGS[size]
        return f"Bearing {size} ({d}×{D}×{B})"
    if fam == "insert":
        return f"Heat-set insert {size}"
    if fam == "iso8734":
        return f"ISO 8734 dowel Ø{size} × {L:g}"
    std = {"iso4762": "ISO 4762", "iso10642": "ISO 10642", "iso4017": "ISO 4017", "iso4032": "ISO 4032",
           "iso7089": "ISO 7089"}[fam]
    return f"{std} {size} × {L:g}" if L else f"{std} {size}"


def color(spec):
    return FAMILIES[spec["family"]][3]


def check(spec):
    fam = spec.get("family")
    if fam not in FAMILIES:
        raise K.KernelError(f"Unknown standard part family “{fam}”")
    if spec.get("size") not in FAMILIES[fam][1]:
        raise K.KernelError(f"{FAMILIES[fam][0]} has no size {spec.get('size')}")
    if FAMILIES[fam][2] is not None and not spec.get("length"):
        raise K.KernelError("Choose a length")


# --------------------------------------------------------------------------- solids
def _prism(pts, z0, h):
    """A straight prism from a polygon in XY, from z0 up h."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Pnt, gp_Vec
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        poly.Add(gp_Pnt(float(x), float(y), float(z0)))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire(), True).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, float(h))).Shape()


def _hex_prism(af, h, z0=0.0, turn=0.0):
    """A hexagon prism, `af` across the flats, from z0 up h."""
    r = af / math.sqrt(3)                               # corner radius
    pts = [(r * math.cos(math.radians(60 * i + turn)), r * math.sin(math.radians(60 * i + turn)))
           for i in range(6)]
    return _prism(pts, z0, h)


def _cone(r0, r1, z0, h):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    return BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(0, 0, z0), gp_Dir(0, 0, 1)), r0, r1, h).Shape()


_CACHE: dict = {}


def build(spec):
    """The solid for a standard part spec (cached)."""
    check(spec)
    key = (spec["family"], spec["size"], float(spec.get("length") or 0))
    if key in _CACHE:
        return _CACHE[key]
    fam, size = spec["family"], spec["size"]
    L = float(spec.get("length") or 0)
    Z = (0, 0, 1)
    if fam == "iso4762":
        d = _d(size)
        dk, k, s, t = ISO4762[size]
        shape = K.fuse(K.cylinder((0, 0, 0), Z, dk / 2, k), K.cylinder((0, 0, -L), Z, d / 2, L))
        shape = K.cut(shape, _hex_prism(s, t + 0.01, k - t))
    elif fam == "iso10642":
        d = _d(size)
        dk, s, t = ISO10642[size]
        k = (dk - d) / 2                                # 90° head
        head = _cone(d / 2, dk / 2, -k, k)
        shape = K.fuse(head, K.cylinder((0, 0, -L), Z, d / 2, L - k))
        shape = K.cut(shape, _hex_prism(s, t + 0.01, -t))
        # countersunk screws sit with the top of the head on z = 0 (flush in the part)
    elif fam == "iso4017":
        d = _d(size)
        s, k = ISO4017[size]
        shape = K.fuse(_hex_prism(s, k), K.cylinder((0, 0, -L), Z, d / 2, L))
    elif fam == "iso4032":
        d = _d(size)
        s, m = ISO4032[size]
        shape = K.cut(_hex_prism(s, m), K.cylinder((0, 0, -1), Z, d / 2, m + 2))
    elif fam == "iso7089":
        d1, d2, h = ISO7089[size]
        shape = K.cut(K.cylinder((0, 0, 0), Z, d2 / 2, h), K.cylinder((0, 0, -1), Z, d1 / 2, h + 2))
    elif fam == "iso8734":
        d = float(size)
        shape = K.cylinder((0, 0, 0), Z, d / 2, L)
    elif fam == "bearing":
        d, D, B = BEARINGS[size]
        ring = K.cut(K.cylinder((0, 0, 0), Z, D / 2, B), K.cylinder((0, 0, -1), Z, d / 2, B + 2))
        # the shields between the rings, set in a little on both sides, so it reads as a bearing
        rin, rout = d / 2 + (D - d) * 0.2, D / 2 - (D - d) * 0.16
        dep = min(0.3, B * 0.05)
        for z0 in (-1.0, B - dep):
            groove = K.cut(K.cylinder((0, 0, z0), Z, rout, dep + 1), K.cylinder((0, 0, z0 - 1), Z, rin, dep + 3))
            ring = K.cut(ring, groove)
        shape = ring
    elif fam == "insert":
        d = _d(size)
        od, ln = INSERTS[size]
        body = K.cylinder((0, 0, 0), Z, od / 2, ln)
        for z0 in (ln * 0.25, ln * 0.6):                # knurl bands
            band = K.cut(K.cylinder((0, 0, z0), Z, od / 2 + 1, ln * 0.12),
                         K.cylinder((0, 0, z0 - 1), Z, od / 2 - 0.25, ln * 0.12 + 2))
            body = K.cut(body, band)
        shape = K.cut(body, K.cylinder((0, 0, -1), Z, d / 2, ln + 2))
    else:
        raise K.KernelError(f"Unknown standard part family “{fam}”")
    if len(_CACHE) > 200:
        _CACHE.clear()
    _CACHE[key] = shape
    return shape
