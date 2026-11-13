"""Nesting: lay sheet-metal flat patterns out on stock sheets for laser, plasma or waterjet cutting.

Each part is first turned to the angle where the rectangle round it is smallest; the rectangles are then packed
onto as few sheets as possible (MaxRects, best short-side fit, with optional 90° turns), keeping a gap between
parts and a margin round the sheet edge. This is rectangle nesting: parts don't tuck into each other's notches,
which keeps it fast and predictable — good for brackets, plates and enclosure panels.

Kerf compensation grows each outline by half the kerf (holes shrink by the same), so the cutter's centre line
runs where the material edge should be. Leave it at 0 if your cutting software compensates itself.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import kernel as K

SHEET_PRESETS = [("3000 × 1500", 3000.0, 1500.0), ("2500 × 1250", 2500.0, 1250.0), ("2000 × 1000", 2000.0, 1000.0),
                 ("1220 × 2440 (8 × 4 ft)", 2440.0, 1220.0), ("1000 × 500", 1000.0, 500.0)]
MATERIALS = [("Mild steel", 7.85), ("Stainless steel", 8.0), ("Aluminium", 2.70), ("Copper", 8.96),
             ("Brass", 8.50), ("Zinc-coated steel", 7.85)]


@dataclass
class NestPart:
    name: str
    lines: list
    circles: list
    arcs: list
    polys: list
    bends: list
    area: float                     # material area of one part (mm², before kerf)
    thickness: float
    w: float = 0.0                  # size once turned to its best angle
    h: float = 0.0
    turn: float = 0.0               # that angle (degrees)
    shift: tuple = (0.0, 0.0)       # moves the turned part so its box starts at (0, 0)


@dataclass
class Placement:
    part: int                       # index into the part list
    copy: int                       # 1, 2, … for parts made more than once
    angle: float                    # total turn (degrees) of the part's own coordinates
    tx: float                       # then this move puts it on the sheet
    ty: float
    w: float
    h: float


@dataclass
class NestResult:
    parts: list
    sheet_w: float
    sheet_h: float
    sheets: list = field(default_factory=list)      # [[Placement]]

    @property
    def count(self):
        return sum(len(s) for s in self.sheets)


# --------------------------------------------------------------------------- parts
def part_from_flat(fp, name, kerf=0.0) -> NestPart:
    """A flat pattern as a nesting part. kerf (mm) grows the outline by half of it (holes shrink)."""
    from . import sheetmetal as SM
    face = fp.face
    if kerf > 1e-9:
        face = _grow_face(face, kerf / 2.0)
    lines, circles, arcs, polys, bends = SM.flat_entities(fp, face)
    p = NestPart(name, lines, circles, arcs, polys, bends, float(K.face_info(fp.face)["area"]), float(fp.thickness))
    _best_turn(p)
    return p


def _grow_face(face, d):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    mk = BRepOffsetAPI_MakeOffset(face, GeomAbs_Arc)
    mk.Perform(float(d))
    if not mk.IsDone():
        raise K.KernelError("Couldn't apply the kerf to this outline")
    wires = [w for w in _wires(mk.Shape())]
    if not wires:
        raise K.KernelError("Couldn't apply the kerf to this outline")
    # the outer wire is the one with the biggest box; the rest are holes
    def box_size(w):
        lo, hi = K.bbox(w)
        return float(np.linalg.norm(hi - lo))
    wires.sort(key=lambda w: -box_size(w))
    mf = BRepBuilderAPI_MakeFace(wires[0], True)
    for w in wires[1:]:
        mf.Add(w)
    return mf.Face() if mf.IsDone() else face


def _wires(shape):
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE
    out = []
    exp = TopExp_Explorer(shape, TopAbs_WIRE)
    while exp.More():
        out.append(K._as_wire(exp.Current()))
        exp.Next()
    return out


def part_points(p: NestPart) -> np.ndarray:
    """Points round the part's outline (for its box and best angle)."""
    pts = []
    for a, b in p.lines:
        pts += [a, b]
    for c, r in p.circles:
        ts = np.linspace(0, 2 * math.pi, 49)
        pts += list(np.stack([c[0] + r * np.cos(ts), c[1] + r * np.sin(ts)], axis=1))
    for c, r, a0, a1 in p.arcs:
        a1 = a1 if a1 > a0 else a1 + 360
        ts = np.radians(np.linspace(a0, a1, 25))
        pts += list(np.stack([c[0] + r * np.cos(ts), c[1] + r * np.sin(ts)], axis=1))
    for pl in p.polys:
        pts += list(pl)
    return np.asarray(pts, float).reshape(-1, 2)


def _hull(pts):
    pts = sorted(set(map(tuple, np.round(pts, 9))))
    if len(pts) < 3:
        return np.asarray(pts, float)

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for q in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], q) <= 0:
            lo.pop()
        lo.append(q)
    for q in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], q) <= 0:
            up.pop()
        up.append(q)
    return np.asarray(lo[:-1] + up[:-1], float)


def _rot(deg):
    a = math.radians(deg)
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def _best_turn(p: NestPart):
    """Turn the part to where its bounding rectangle is smallest (lying long side along X)."""
    pts = part_points(p)
    if not len(pts):
        p.w = p.h = 0.0
        return
    hull = _hull(pts)
    cands = {0.0}
    for i in range(len(hull)):
        d = hull[(i + 1) % len(hull)] - hull[i]
        if np.linalg.norm(d) > 1e-9:
            cands.add(round(-math.degrees(math.atan2(d[1], d[0])), 9))
    best = None
    for ang in cands:
        q = hull @ _rot(ang).T
        lo, hi = q.min(0), q.max(0)
        wh = hi - lo
        key = (round(wh[0] * wh[1], 6), -wh[0])
        if best is None or key < best[0]:
            best = (key, ang, lo, wh)
    _k, ang, lo, wh = best
    if wh[1] > wh[0]:                         # long side along X
        ang += 90.0
        q = hull @ _rot(ang).T
        lo, wh = q.min(0), q.max(0) - q.min(0)
    p.turn, p.w, p.h = ang, float(wh[0]), float(wh[1])
    p.shift = (float(-lo[0]), float(-lo[1]))


# --------------------------------------------------------------------------- packing
class _MaxRects:
    def __init__(self, W, H):
        self.free = [(0.0, 0.0, W, H)]

    def find(self, w, h, allow_turn):
        best = None
        for (x, y, fw, fh) in self.free:
            for ww, hh, turned in ((w, h, False), (h, w, True)) if allow_turn else ((w, h, False),):
                if ww <= fw + 1e-9 and hh <= fh + 1e-9:
                    short, long_ = min(fw - ww, fh - hh), max(fw - ww, fh - hh)
                    key = (short, long_, y, x)
                    if best is None or key < best[0]:
                        best = (key, x, y, ww, hh, turned)
        return best

    def place(self, x, y, w, h):
        new = []
        for (fx, fy, fw, fh) in self.free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                new.append((fx, y + h, fw, fy + fh - y - h))
        # drop free rectangles inside others
        self.free = [r for i, r in enumerate(new) if r[2] > 1e-9 and r[3] > 1e-9 and not any(
            j != i and o[0] <= r[0] + 1e-9 and o[1] <= r[1] + 1e-9 and o[0] + o[2] >= r[0] + r[2] - 1e-9 and
            o[1] + o[3] >= r[1] + r[3] - 1e-9 and (o != r or j < i) for j, o in enumerate(new))]


def _separated(A, B, gap):
    """Are two convex polygons at least `gap` apart? (Separating-axis test: a gap of `gap` between their shadows
    on some edge normal means they are at least that far apart.)"""
    for P in (A, B):
        n = len(P)
        for i in range(n):
            e = P[(i + 1) % n] - P[i]
            L = np.linalg.norm(e)
            if L < 1e-12:
                continue
            ax = np.array([-e[1], e[0]]) / L
            a, b = A @ ax, B @ ax
            if a.max() + gap <= b.min() + 1e-9 or b.max() + gap <= a.min() + 1e-9:
                return True
    return False


def _pair(p: NestPart, gap):
    """Two copies, the second turned 180° and slid against the first (triangles make a rectangle, L shapes
    interlock). Returns (members, w, h) when that packs tighter than two separate copies, else None.
    members: [(angle, (tx, ty))] putting each copy inside a box from (0, 0) to (w, h)."""
    hull = _hull(part_points(p))
    if len(hull) < 3:
        return None
    A = hull @ _rot(p.turn).T + np.array(p.shift)
    B0 = hull @ _rot(p.turn + 180).T
    B0 = B0 - B0.min(0)                                   # same box as A: (0, 0) - (w, h)
    best = None
    for axis in (0, 1):
        span = (p.w if axis == 0 else p.h) + gap
        lo, hi = 0.0, span
        step = np.zeros(2)
        step[axis] = 1.0
        if not _separated(A, B0 + step * hi, gap):
            continue
        for _ in range(40):                               # smallest slide that keeps the gap
            mid = (lo + hi) / 2
            if _separated(A, B0 + step * mid, gap):
                hi = mid
            else:
                lo = mid
        Bt = B0 + step * hi
        allp = np.vstack([A, Bt])
        lo2, hi2 = allp.min(0), allp.max(0)
        wh = hi2 - lo2
        cost = (wh[0] + gap) * (wh[1] + gap)
        if best is None or cost < best[0]:
            tB = -(hull @ _rot(p.turn + 180).T).min(0) + step * hi - lo2
            best = (cost, wh, [(p.turn, tuple(np.array(p.shift) - lo2)), (p.turn + 180.0, tuple(tB))])
    if best is None or best[0] >= 2 * (p.w + gap) * (p.h + gap) * 0.95:
        return None
    return best[2], float(best[1][0]), float(best[1][1])


def nest(parts, quantities, sheet_w, sheet_h, spacing=5.0, margin=10.0, allow_turn=True, pair=True) -> NestResult:
    """Pack `quantities[i]` copies of each part onto as few sheets as needed."""
    res = NestResult(list(parts), float(sheet_w), float(sheet_h))
    W = sheet_w - 2 * margin + spacing
    H = sheet_h - 2 * margin + spacing
    if W <= 0 or H <= 0:
        raise ValueError("The margin leaves no room on the sheet")
    jobs = []
    for i, (p, q) in enumerate(zip(parts, quantities)):
        fits = (p.w + spacing <= W + 1e-9 and p.h + spacing <= H + 1e-9) or \
               (allow_turn and p.h + spacing <= W + 1e-9 and p.w + spacing <= H + 1e-9)
        if q > 0 and not fits:
            raise ValueError(f"“{p.name}” ({p.w:.0f} × {p.h:.0f} mm) doesn't fit on a {sheet_w:.0f} × {sheet_h:.0f} "
                             "sheet with this margin")
        single = ([(p.turn, p.shift)], p.w, p.h)
        pr = _pair(p, spacing) if pair and q >= 2 else None
        if pr is not None and not (pr[1] + spacing <= W + 1e-9 and pr[2] + spacing <= H + 1e-9 or
                                   allow_turn and pr[2] + spacing <= W + 1e-9 and pr[1] + spacing <= H + 1e-9):
            pr = None                                        # a pair too big for the sheet: place them singly
        n_pairs = int(q) // 2 if pr is not None else 0
        copy = 1
        for _ in range(n_pairs):
            jobs.append((i, (copy, copy + 1), pr))
            copy += 2
        while copy <= int(q):
            jobs.append((i, (copy,), single))
            copy += 1
    jobs.sort(key=lambda j: (-max(j[2][1], j[2][2]), -j[2][1] * j[2][2]))
    bins = []
    for i, copies, (members, cw, ch) in jobs:
        p = parts[i]
        w, h = cw + spacing, ch + spacing
        spot = None
        for b, mr in enumerate(bins):
            f = mr.find(w, h, allow_turn)
            if f is not None:
                spot = (b, f)
                break
        if spot is None:
            bins.append(_MaxRects(W, H))
            res.sheets.append([])
            spot = (len(bins) - 1, bins[-1].find(w, h, allow_turn))
        b, (_key, x, y, ww, hh, turned) = spot
        bins[b].place(x, y, ww, hh)
        # each copy: turned and moved into the group's box, the group turned 90° if that fitted better, then
        # moved onto the sheet (inside the margin)
        G = _rot(90.0 if turned else 0.0)
        hull = _hull(part_points(p))
        group_pts = np.vstack([(hull @ _rot(a).T + np.array(t)) @ G.T for a, t in members]) if len(hull) else \
            np.zeros((1, 2))
        glo = group_pts.min(0)
        for (a, t), k in zip(members, copies):
            ang = a + (90.0 if turned else 0.0)
            off = G @ np.array(t, float) - glo + np.array([margin + x, margin + y])
            res.sheets[b].append(Placement(i, k, ang, float(off[0]), float(off[1]), ww - spacing, hh - spacing))
    return res


# --------------------------------------------------------------------------- output
def placed(p: NestPart, pl: Placement, dx=0.0):
    """The part's entities moved to its place: (lines, circles, arcs, polys, bends)."""
    R = _rot(pl.angle)
    t = np.array([pl.tx + dx, pl.ty])
    T = lambda q: tuple(float(x) for x in (R @ np.asarray(q, float) + t))   # noqa: E731
    lines = [(T(a), T(b)) for a, b in p.lines]
    circles = [(T(c), r) for c, r in p.circles]
    arcs = [(T(c), r, a0 + pl.angle, a1 + pl.angle) for c, r, a0, a1 in p.arcs]
    polys = [[T(q) for q in pts] for pts in p.polys]
    bends = [(T(a), T(b), lab) for a, b, lab in p.bends]
    return lines, circles, arcs, polys, bends


SHEET_GAP = 100.0          # space between sheets in the DXF


def sheet_offset(res: NestResult, i):
    return i * (res.sheet_w + SHEET_GAP)


def export_dxf(res: NestResult, path, labels=True):
    """All sheets side by side in one DXF: sheet outlines (SHEET), cut paths (CUT), bend lines (BEND) and part
    names (NOTES, not for cutting)."""
    import ezdxf
    from ezdxf.enums import TextEntityAlignment
    doc = ezdxf.new("R2010", setup=False)
    doc.units = ezdxf.units.MM
    doc.header["$MEASUREMENT"] = 1
    doc.linetypes.add("DASHED", pattern=[4.5, 3.0, -1.5], description="Dashed __ __ __")
    doc.layers.add("SHEET", color=8)
    doc.layers.add("CUT", color=7)
    doc.layers.add("BEND", color=1, linetype="DASHED")
    doc.layers.add("NOTES", color=3)
    msp = doc.modelspace()
    W, H = res.sheet_w, res.sheet_h
    for si, sheet in enumerate(res.sheets):
        ox = sheet_offset(res, si)
        msp.add_lwpolyline([(ox, 0), (ox + W, 0), (ox + W, H), (ox, H)], close=True, dxfattribs={"layer": "SHEET"})
        t = msp.add_text(f"Sheet {si + 1} of {len(res.sheets)}", height=max(W, H) * 0.012,
                         dxfattribs={"layer": "NOTES"})
        t.set_placement((ox, H + max(W, H) * 0.01), align=TextEntityAlignment.BOTTOM_LEFT)
        for pl in sheet:
            p = res.parts[pl.part]
            lines, circles, arcs, polys, bends = placed(p, pl, ox)
            for a, b in lines:
                msp.add_line(a, b, dxfattribs={"layer": "CUT"})
            for c, r in circles:
                msp.add_circle(c, r, dxfattribs={"layer": "CUT"})
            for c, r, a0, a1 in arcs:
                msp.add_arc(c, r, a0, a1, dxfattribs={"layer": "CUT"})
            for pts in polys:
                msp.add_lwpolyline(pts, dxfattribs={"layer": "CUT"})
            for a, b, _lab in bends:
                msp.add_line(a, b, dxfattribs={"layer": "BEND"})
            if labels:
                h = max(2.0, min(pl.w, pl.h) * 0.08)
                t = msp.add_text(p.name, height=min(h, 12.0), dxfattribs={"layer": "NOTES"})
                c = np.array([ox, 0.0]) + _box_centre(p, pl)
                t.set_placement(tuple(c), align=TextEntityAlignment.MIDDLE_CENTER)
    doc.saveas(path)


def _box_centre(p, pl):
    pts = _hull(part_points(p)) @ _rot(pl.angle).T + np.array([pl.tx, pl.ty])
    return (pts.min(0) + pts.max(0)) / 2 if len(pts) else np.array([pl.tx, pl.ty])


def summary(res: NestResult, density=7.85, price_per_kg=None):
    """Sheets, material use and weight. density in g/cm³."""
    used = len(res.sheets)
    sheet_area = res.sheet_w * res.sheet_h * used
    part_area = sum(res.parts[pl.part].area for sh in res.sheets for pl in sh)
    thick = max((p.thickness for p in res.parts), default=0.0)
    part_mass = sum(res.parts[pl.part].area * res.parts[pl.part].thickness for sh in res.sheets for pl in sh) \
        * density / 1000.0                                       # mm³ × g/cm³ / 1000 = g
    sheet_mass = sheet_area * thick * density / 1000.0
    last = np.zeros(2)
    if res.sheets:
        for pl in res.sheets[-1]:
            pts = _hull(part_points(res.parts[pl.part])) @ _rot(pl.angle).T + np.array([pl.tx, pl.ty])
            if len(pts):
                last = np.maximum(last, pts.max(0))
    out = {"sheets": used, "parts": res.count, "use": (part_area / sheet_area) if sheet_area else 0.0,
           "last_used": (float(last[0]), float(last[1])),
           "part_kg": part_mass / 1000.0, "sheet_kg": sheet_mass / 1000.0, "thickness": thick}
    if price_per_kg:
        out["cost"] = out["sheet_kg"] * float(price_per_kg)
    return out
