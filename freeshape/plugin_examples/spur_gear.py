"""Spur gear: an involute gear from its module and number of teeth, as a parametric feature."""
import math


def gear_segments(module, teeth, pressure_angle=20.0, steps=10):
    """The outline of an involute spur gear, centred on (0, 0): segments for freeshape.api.outline — smooth
    involute flanks, and arcs (as smooth curves) across the tips and roots."""
    m, z = float(module), int(teeth)
    a = math.radians(pressure_angle)
    rp = m * z / 2                    # pitch radius
    rb = rp * math.cos(a)             # base circle: the involute starts here
    ra = rp + m                       # tip
    rf = rp - 1.25 * m                # root
    r0 = max(rb, rf)

    def inv(r):
        t = math.acos(min(1.0, rb / r))
        return math.tan(t) - t

    half = math.pi / (2 * z) + inv(rp)          # the tooth's half angle at radius r is half - inv(r)

    def psi(r):
        return half - inv(r)

    def xy(r, t):
        return (r * math.cos(t), r * math.sin(t))

    def arc(r, t0, t1, n=4):
        return [xy(r, t0 + (t1 - t0) * i / n) for i in range(n + 1)]

    radii = [r0 + (ra - r0) * (i / steps) ** 1.5 for i in range(steps + 1)]   # finer near the base
    segs = []
    for k in range(z):
        c = 2 * math.pi * k / z
        if rf < r0:
            segs.append([xy(rf, c - psi(r0)), xy(r0, c - psi(r0))])
        segs.append([xy(r, c - psi(r)) for r in radii])                 # rising flank
        segs.append(arc(ra, c - psi(ra), c + psi(ra)))                  # tip
        segs.append([xy(r, c + psi(r)) for r in reversed(radii)])       # falling flank
        if rf < r0:
            segs.append([xy(r0, c + psi(r0)), xy(rf, c + psi(r0))])
        segs.append(arc(rf, c + psi(r0), c + 2 * math.pi / z - psi(r0)))   # root, to the next tooth
    return segs


def register(api):
    api.info(name="Spur gear", version="1.1",
             description="Involute spur gears from the module and the number of teeth")
    fs = api.geom

    def build(ctx):
        m, z, t = ctx["module"], ctx["teeth"], ctx["thickness"]
        if m <= 0 or t <= 0:
            ctx.fail("The module and thickness must be more than 0")
        if z < 6:
            ctx.fail("A gear needs at least 6 teeth")
        plane = ctx.plane("plane")
        gear = fs.extrude(fs.outline(gear_segments(m, z, ctx["pressure_angle"]), on=plane), t)
        if ctx["bore"] > 0:
            if ctx["bore"] >= m * z - 2.5 * m:
                ctx.fail("The bore is bigger than the gear's root")
            gear = fs.subtract(gear, fs.extrude(fs.circle(ctx["bore"] / 2, on=plane), t))
        return gear

    api.add_feature("spur_gear", "Spur gear", build, fields=[
        api.select("plane", "Plane (Top if none)", kinds="plane", multi=False),
        api.number("module", "Module", 2.0, minimum=0.1),
        api.integer("teeth", "Teeth", 20, minimum=6),
        api.number("thickness", "Thickness", 8.0, minimum=0.01),
        api.number("bore", "Bore (0 for none)", 8.0, minimum=0),
        api.angle("pressure_angle", "Pressure angle", 20.0),
        api.note("Module = pitch diameter ÷ teeth. Two gears mesh when they have the same module; put their "
                 "centres (teeth₁ + teeth₂) × module ÷ 2 apart."),
    ])
