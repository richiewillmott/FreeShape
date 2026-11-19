"""Gridfinity: storage bins and baseplates for the Gridfinity system (42 mm grid, 7 mm height units).

The profiles follow the published Gridfinity dimensions: a bin's foot is 4.75 mm tall (0.8 at 45°, 1.8 straight,
2.15 at 45°), the baseplate's pocket is the same shape 4.65 mm deep, and the stacking lip on top of a bin takes the
foot of the bin above it.
"""
import math

GRID = 42.0          # one grid unit
UNIT_H = 7.0         # one height unit
GAP = 0.5            # a bin is 0.5 mm smaller than its grid cells
R_OUT = 3.75         # outer corner radius of a bin
FOOT = [(0.0, 0.8 + 2.15), (0.8, 2.15), (2.6, 2.15), (4.75, 0.0)]      # (height, inset from the outside)
FOOT_H = 4.75
LIP = [(0.0, 2.6), (1.9, 0.7), (3.7, 0.7), (4.4, 0.0)]                  # (height above the lip's base, inset)
LIP_H = 4.4
PLATE = [(0.0, 2.85), (2.15, 0.7), (3.95, 0.7), (4.65, 0.0)]           # baseplate pocket, from its bottom
PLATE_H = 4.65
MAGNET_D, MAGNET_DEPTH, MAGNET_AT = 6.5, 2.4, 13.0


def _rrect(cx, cy, w, h, r, z):
    """A rounded rectangle wire in the plane at height z."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt
    r = max(0.05, min(r, w / 2 - 1e-3, h / 2 - 1e-3))
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    k = r * (1 - math.sqrt(0.5))
    P = lambda x, y: gp_Pnt(x, y, z)  # noqa: E731
    mw = BRepBuilderAPI_MakeWire()
    pts = [((x0 + r, y0), (x1 - r, y0)), ((x1, y0 + r), (x1, y1 - r)),
           ((x1 - r, y1), (x0 + r, y1)), ((x0, y1 - r), (x0, y0 + r))]
    mids = [(x1 - k, y0 + k), (x1 - k, y1 - k), (x0 + k, y1 - k), (x0 + k, y0 + k)]
    for i, (a, b) in enumerate(pts):
        mw.Add(BRepBuilderAPI_MakeEdge(P(*a), P(*b)).Edge())
        nxt = pts[(i + 1) % 4][0]
        arc = GC_MakeArcOfCircle(P(*b), P(*mids[i]), P(*nxt)).Value()
        mw.Add(BRepBuilderAPI_MakeEdge(arc).Edge())
    return mw.Wire()


def _loft(cx, cy, w, h, r, sections):
    """A solid through rounded rectangles: sections are (z, inset); straight (ruled) between them."""
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections
    ts = BRepOffsetAPI_ThruSections(True, True)
    for z, inset in sections:
        ts.AddWire(_rrect(cx, cy, w - 2 * inset, h - 2 * inset, r - inset, z))
    ts.Build()
    return ts.Shape()


def bin_solid(fs, nx, ny, units, wall=1.2, floor=1.0, div_x=0, div_y=0, lip=True, magnets=False):
    W, D = GRID * nx - GAP, GRID * ny - GAP
    cx, cy = GRID * nx / 2, GRID * ny / 2
    top = UNIT_H * units                                    # where the next bin's foot sits
    if top < FOOT_H + floor + 2:
        raise fs.KernelError("Too low: make it at least 2 height units")
    feet = []
    for i in range(nx):
        for j in range(ny):
            fx, fy = GRID * (i + 0.5), GRID * (j + 0.5)
            feet.append(_loft(fx, fy, GRID - GAP, GRID - GAP, R_OUT, FOOT))
    z_top = top + (LIP_H if lip else 0.0)
    body = _loft(cx, cy, W, D, R_OUT, [(FOOT_H - 0.01, 0.0), (z_top, 0.0)])
    solid = fs.union(feet + [body])
    # the inside: straight walls, then (with a lip) a 45° shelf under the lip and the lip's own profile
    z_floor = FOOT_H + floor
    if lip:
        base = top
        secs = [(z_floor, wall), (base - (2.6 - wall), wall), (base, 2.6)]
        secs += [(base + h, inset) for h, inset in LIP[1:]] + [(z_top + 1.0, -1.0)]
    else:
        secs = [(z_floor, wall), (z_top + 1.0, wall)]
    solid = fs.subtract(solid, _loft(cx, cy, W, D, R_OUT, secs))
    # dividers: walls across the inside, up to the bottom of the lip
    t = wall
    h_div = (top - (2.6 - wall) if lip else z_top) - z_floor
    walls = []
    for k in range(1, div_x + 1):
        x = cx - W / 2 + W * k / (div_x + 1)
        walls.append(fs.box(t, D - 2 * wall + 0.2, h_div, at=(x - t / 2, cy - D / 2 + wall - 0.1, z_floor)))
    for k in range(1, div_y + 1):
        y = cy - D / 2 + D * k / (div_y + 1)
        walls.append(fs.box(W - 2 * wall + 0.2, t, h_div, at=(cx - W / 2 + wall - 0.1, y - t / 2, z_floor)))
    if walls:
        solid = fs.union([solid] + walls)
    if magnets:
        holes = []
        for i in range(nx):
            for j in range(ny):
                fx, fy = GRID * (i + 0.5), GRID * (j + 0.5)
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        holes.append(fs.cylinder(MAGNET_D / 2, MAGNET_DEPTH,
                                                 at=(fx + sx * MAGNET_AT, fy + sy * MAGNET_AT, 0)))
        solid = fs.subtract(solid, fs.union(holes) if len(holes) > 1 else holes[0])
    return solid


def baseplate_solid(fs, nx, ny, magnets=False):
    W, D = GRID * nx, GRID * ny
    extra = MAGNET_DEPTH + 0.8 if magnets else 0.0          # a floor under the pockets to hold the magnets
    plate = _loft(W / 2, D / 2, W, D, 4.0, [(0.0, 0.0), (PLATE_H + extra, 0.0)])
    pockets = []
    for i in range(nx):
        for j in range(ny):
            fx, fy = GRID * (i + 0.5), GRID * (j + 0.5)
            secs = [(extra + h, inset) for h, inset in PLATE] + [(PLATE_H + extra + 1.0, -1.0)]
            if not magnets:
                secs = [(-1.0, PLATE[0][1])] + secs
            pockets.append(_loft(fx, fy, GRID, GRID, 4.0, secs))
            if magnets:
                for sx in (-1, 1):
                    for sy in (-1, 1):
                        pockets.append(fs.cylinder(MAGNET_D / 2, MAGNET_DEPTH + 0.01,
                                                   at=(fx + sx * MAGNET_AT, fy + sy * MAGNET_AT, extra - MAGNET_DEPTH)))
    return fs.subtract(plate, fs.union(pockets))


def register(api):
    api.info(name="Gridfinity", version="1.0",
             description="Gridfinity storage bins and baseplates (42 mm grid, 7 mm height units)")
    fs = api.geom

    def build(ctx):
        nx, ny = int(ctx["nx"]), int(ctx["ny"])
        if nx < 1 or ny < 1 or nx * ny > 64:
            ctx.fail("Use between 1 and 64 grid cells")
        if ctx["kind"] == "baseplate":
            return baseplate_solid(fs, nx, ny, ctx["magnets"])
        if ctx["wall"] < 0.4 or ctx["wall"] > 2.5:
            ctx.fail("Walls between 0.4 and 2.5 mm thick")
        return bin_solid(fs, nx, ny, int(ctx["units"]), ctx["wall"], 1.0, int(ctx["div_x"]), int(ctx["div_y"]),
                         ctx["lip"], ctx["magnets"])

    api.add_feature("gridfinity", "Gridfinity bin / baseplate", build, boolean=False, fields=[
        api.choice("kind", "Make", [("bin", "Bin"), ("baseplate", "Baseplate")], "bin"),
        api.integer("nx", "Width (grid units)", 2, minimum=1),
        api.integer("ny", "Depth (grid units)", 1, minimum=1),
        api.integer("units", "Height (7 mm units, bins)", 6, minimum=2),
        api.integer("div_x", "Dividers across", 0, minimum=0),
        api.integer("div_y", "Dividers along", 0, minimum=0),
        api.number("wall", "Wall thickness", 1.2, minimum=0.4),
        api.check("lip", "Stacking lip", True),
        api.check("magnets", "Magnet holes (6 × 2 mm)", False),
        api.note("One grid unit is 42 mm, one height unit 7 mm. Dividers and the lip are for bins; a baseplate "
                 "uses just the size and magnet holes."),
    ])
