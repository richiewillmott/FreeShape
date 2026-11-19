"""Timing pulley: GT2 and HTD belt pulleys from the number of teeth, with flanges, a hub, a bore and a grub screw.

Pitch diameter = teeth × pitch ÷ π. The outside diameter is smaller than that by twice the belt's pitch line
offset, and each groove is a round-bottomed slot as deep as the belt's tooth, so a belt sits right on the pitch line.
"""
import math

# pitch, pitch line offset, tooth depth, groove radius (the belt tooth's rounded profile)
PROFILES = {
    "gt2_2": (2.0, 0.254, 0.75, 0.555),
    "gt2_3": (3.0, 0.381, 1.14, 0.85),
    "htd_3": (3.0, 0.381, 1.17, 0.86),
    "htd_5": (5.0, 0.5715, 2.06, 1.49),
}
NAMES = [("gt2_2", "GT2 2 mm"), ("gt2_3", "GT2 3 mm"), ("htd_3", "HTD 3M"), ("htd_5", "HTD 5M")]


def sizes(profile, teeth):
    p, pld, depth, rg = PROFILES[profile]
    dp = teeth * p / math.pi
    return dp, dp - 2 * pld


def pulley_arcs(profile, teeth, clearance=0.0):
    """The toothed outline as exact circular arcs, each (start, middle, end) in 2D, centred on (0, 0): an arc
    of the outside diameter between grooves, and a round-bottomed groove for each tooth."""
    p, pld, depth, rg = PROFILES[profile]
    z = int(teeth)
    dp = z * p / math.pi
    ro = dp / 2 - pld
    rg = rg + clearance
    cr = ro - depth - clearance + rg                 # groove centre radius: groove bottom at ro - depth
    cos_a = (ro * ro + cr * cr - rg * rg) / (2 * ro * cr)
    if cos_a >= 1 or cr <= 0:
        raise ValueError("Too few teeth for this belt")
    half = math.acos(cos_a)                         # where the groove crosses the outside circle
    pitch_ang = 2 * math.pi / z
    if 2 * half >= pitch_ang * 0.9:
        raise ValueError("Too few teeth for this belt")
    P = lambda r, a: (r * math.cos(a), r * math.sin(a))  # noqa: E731
    arcs = []
    for k in range(z):
        c = pitch_ang * k
        a0, a1 = c + half, c + pitch_ang - half
        arcs.append((P(ro, a0), P(ro, (a0 + a1) / 2), P(ro, a1)))                 # land
        gc = c + pitch_ang
        arcs.append((P(ro, a1), P(cr - rg, gc), P(ro, gc + half)))               # groove, through its bottom
    return arcs


def arcs_face(arcs, plane):
    """A flat face on `plane` from a closed chain of 3-point arcs."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Pnt
    pt = lambda q: gp_Pnt(*map(float, plane.to_world(*q)))  # noqa: E731
    mw = BRepBuilderAPI_MakeWire()
    first = pt(arcs[0][0])
    prev = first
    for i, (a, m, b) in enumerate(arcs):
        end = first if i == len(arcs) - 1 else pt(b)
        mw.Add(BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(prev, pt(m), end).Value()).Edge())
        prev = end
    return BRepBuilderAPI_MakeFace(mw.Wire(), True).Face()


def register(api):
    api.info(name="Timing pulley", version="1.0",
             description="GT2 and HTD timing belt pulleys from the number of teeth")
    fs = api.geom

    def build(ctx):
        prof, z = ctx["profile"], int(ctx["teeth"])
        width, fl, hub_d, hub_l, bore = ctx["width"], ctx["flange"], ctx["hub_d"], ctx["hub_l"], ctx["bore"]
        if z < 10 or z > 200:
            ctx.fail("Use between 10 and 200 teeth")
        if width <= 0:
            ctx.fail("The belt width must be more than 0")
        dp, od = sizes(prof, z)
        try:
            arcs = pulley_arcs(prof, z, ctx["clearance"])
        except ValueError as e:
            ctx.fail(str(e))
        plane = ctx.plane("plane")
        n = plane.normal
        z0 = 0.0
        parts = []
        if hub_l > 0:
            if hub_d <= bore:
                ctx.fail("The hub must be bigger than the bore")
            parts.append(fs.extrude(fs.circle(hub_d / 2, on=plane), hub_l))
            z0 = hub_l
        flange_d = od + 2 * max(1.0, PROFILES[prof][2] + 0.8)
        lift = lambda s, dz: fs.move(s, *(n * dz))      # noqa: E731
        if fl > 0:
            parts.append(lift(fs.extrude(fs.circle(flange_d / 2, on=plane), fl), z0))
            z0 += fl
        parts.append(lift(fs.extrude(arcs_face(arcs, plane), width), z0))
        z0 += width
        if fl > 0:
            parts.append(lift(fs.extrude(fs.circle(flange_d / 2, on=plane), fl), z0))
            z0 += fl
        body = fs.union(parts) if len(parts) > 1 else parts[0]
        if bore > 0:
            if bore >= od - 2 * PROFILES[prof][2] - 1:
                ctx.fail("The bore is too big for this pulley")
            body = fs.subtract(body, lift(fs.extrude(fs.circle(bore / 2, on=plane), z0 + 2), -1))
        if ctx["grub"] and hub_l > 0 and bore > 0:
            c = plane.origin + n * (hub_l / 2)
            body = fs.subtract(body, fs.cylinder(1.5, hub_d, at=c, axis=plane.xdir))   # M3 grub screw hole
        return body

    api.add_feature("timing_pulley", "Timing pulley", build, fields=[
        api.select("plane", "Plane (Top if none)", kinds="plane", multi=False),
        api.choice("profile", "Belt", NAMES, "gt2_2"),
        api.integer("teeth", "Teeth", 20, minimum=10),
        api.number("width", "Belt width", 6.5, minimum=0.1),
        api.number("flange", "Flange thickness (0 for none)", 1.0, minimum=0),
        api.number("hub_d", "Hub diameter", 16.0, minimum=0),
        api.number("hub_l", "Hub length (0 for none)", 7.0, minimum=0),
        api.number("bore", "Bore (0 for none)", 5.0, minimum=0),
        api.check("grub", "Grub screw hole in the hub (M3)", True),
        api.number("clearance", "Print clearance (groove)", 0.0, minimum=0),
        api.note("Belt widths: GT2 6 or 9 mm (6.5 / 9.5 leaves room), HTD 9 or 15 mm. Two pulleys on one belt: "
                 "centre distance from the belt length with any belt calculator. 0.05–0.1 mm print clearance "
                 "helps 3D-printed pulleys."),
    ])
