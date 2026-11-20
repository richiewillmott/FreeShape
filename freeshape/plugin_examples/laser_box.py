"""Laser-cut box: six flat panels with finger joints that slot together, from the outside size and the sheet
thickness — plus a command that writes flat panels to a DXF for the laser cutter.

Every edge where two panels meet is split into an odd number of fingers, taken in turn by one panel then the
other, so both ends of every edge belong to the same panel. Corners belong to the bottom and top (or to the front
and back when the box is open).
"""
PANELS = ("bottom", "top", "front", "back", "left", "right")
NAMES = {"bottom": "Bottom", "top": "Lid", "front": "Front", "back": "Back", "left": "Left side",
         "right": "Right side"}
RANK = {"bottom": 0, "top": 0, "front": 1, "back": 1, "left": 2, "right": 2}


def slab(name, W, D, H, t):
    """(min corner, max corner) of a full-size panel."""
    return {"bottom": ((0, 0, 0), (W, D, t)), "top": ((0, 0, H - t), (W, D, H)),
            "front": ((0, 0, 0), (W, t, H)), "back": ((0, D - t, 0), (W, D, H)),
            "left": ((0, 0, 0), (t, D, H)), "right": ((W - t, 0, 0), (W, D, H))}[name]


def _overlap(a, b):
    lo = tuple(max(a[0][i], b[0][i]) for i in range(3))
    hi = tuple(min(a[1][i], b[1][i]) for i in range(3))
    return (lo, hi) if all(hi[i] - lo[i] > 1e-9 for i in range(3)) else None


def panels(fs, W, D, H, t, finger, lid=True):
    """{panel name: solid}, assembled in place."""
    names = [p for p in PANELS if lid or p != "top"]
    box = {p: slab(p, W, D, H, t) for p in names}
    cut = {p: [] for p in names}                       # boxes each panel gives up
    # corners: the t × t × t cubes where three panels meet; owned by the highest-ranked panel there
    for x in (0, W - t):
        for y in (0, D - t):
            for z in (0, H - t):
                cube = ((x, y, z), (x + t, y + t, z + t))
                there = [p for p in names if _overlap(box[p], cube)]
                owner = min(there, key=lambda p: (RANK[p], PANELS.index(p)))
                for p in there:
                    if p != owner:
                        cut[p].append(cube)
    # edges: the bar where two panels overlap, less its corner cubes, split into fingers
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            bar = _overlap(box[a], box[b])
            if bar is None:
                continue
            lo, hi = bar
            ax = max(range(3), key=lambda k: hi[k] - lo[k])          # the edge's direction
            s0, s1 = lo[ax] + t, hi[ax] - t
            L = s1 - s0
            if L <= 1e-6:
                continue
            n = max(3, int(round(L / finger)))
            if n % 2 == 0:
                n += 1
            first, second = sorted((a, b), key=lambda p: (RANK[p], PANELS.index(p)))
            for k in range(n):
                q0, q1 = list(lo), list(hi)
                q0[ax], q1[ax] = s0 + L * k / n, s0 + L * (k + 1) / n
                loser = second if k % 2 == 0 else first
                cut[loser].append((tuple(q0), tuple(q1)))
    out = {}
    for p in names:
        (x0, y0, z0), (x1, y1, z1) = box[p]
        solid = fs.box(x1 - x0, y1 - y0, z1 - z0, at=(x0, y0, z0))
        tools = []
        for (q0, q1) in cut[p]:
            # grow each cut a hair outwards where it meets the outside of the panel, so faces don't coincide
            g0 = [q0[k] - (1e-4 if abs(q0[k] - box[p][0][k]) < 1e-9 else 0) for k in range(3)]
            g1 = [q1[k] + (1e-4 if abs(q1[k] - box[p][1][k]) < 1e-9 else 0) for k in range(3)]
            tools.append(fs.box(g1[0] - g0[0], g1[1] - g0[1], g1[2] - g0[2], at=g0))
        if tools:
            from freeshape import kernel as K
            solid = fs.subtract(solid, K.compound(tools))
        out[p] = solid
    return out


def grow(fs, panel, d):
    """The panel with its outline pushed out by d all round (sharp corners kept), same thickness: kerf
    compensation, so that after the laser burns away d on each side the parts are the drawn size."""
    import numpy as np
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Intersection
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from freeshape import kernel as K
    if d <= 0:
        return panel
    lo, hi = map(np.asarray, fs.bbox(panel))
    thin = int(np.argmin(hi - lo))
    n = np.zeros(3)
    n[thin] = 1.0
    faces = [f for f in K.faces(panel) if (pl := K.planar_face_plane(f)) is not None
             and abs(abs(pl.normal @ n) - 1) < 1e-9 and abs(fs.bbox(f)[0][thin] - lo[thin]) < 1e-6]
    if not faces:
        return panel
    mo = BRepOffsetAPI_MakeOffset(faces[0], GeomAbs_Intersection)
    mo.Perform(float(d))
    ex = TopExp_Explorer(mo.Shape(), TopAbs_WIRE)
    wires = []
    while ex.More():
        wires.append(K._as_wire(ex.Current()))
        ex.Next()
    if not wires:
        return panel
    outer = max((BRepBuilderAPI_MakeFace(w, True).Face() for w in wires), key=lambda f: fs.volume(fs.extrude(f, 1, n)))
    return fs.extrude(outer, float(hi[thin] - lo[thin]), direction=n)


def lay_flat(fs, parts, t, gap=5.0):
    """The panels laid flat on the Top plane in two rows, for cutting."""
    import numpy as np
    flat = {}
    cursor, row_y, row_h, count = 0.0, 0.0, 0.0, 0
    for p, s in parts.items():
        lo, hi = map(np.asarray, fs.bbox(s))
        thin = int(np.argmin(hi - lo))
        if thin == 1:
            s = fs.rotate(s, 90, axis=(1, 0, 0))
        elif thin == 0:
            s = fs.rotate(s, -90, axis=(0, 1, 0))
        lo, hi = map(np.asarray, fs.bbox(s))
        if count == 3:
            cursor, row_y, row_h = 0.0, row_y + row_h + gap, 0.0
        s = fs.move(s, cursor - lo[0], row_y - lo[1], -lo[2])
        cursor += hi[0] - lo[0] + gap
        row_h = max(row_h, hi[1] - lo[1])
        flat[p] = s
        count += 1
    return flat


def write_dxf(shapes, path, gap=5.0):
    """Each shape's biggest flat face as outlines in a DXF (curves as fine polylines), laid side by side."""
    import numpy as np
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Line
    from freeshape import kernel as K
    from freeshape.dxf import DxfWriter

    def area(f):
        from OCP.BRepGProp import BRepGProp
        from OCP.GProp import GProp_GProps
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, g)
        return abs(g.Mass())

    dw = DxfWriter()
    dw.layer("CUT", 7)
    x_at = 0.0
    lo_all, hi_all = [0.0, 0.0], [0.0, 0.0]
    for shape in shapes:
        flats = [(f, K.planar_face_plane(f)) for f in K.faces(shape)]
        flats = [(f, pl) for f, pl in flats if pl is not None]
        if not flats:
            continue
        face, pl = max(flats, key=lambda fp: area(fp[0]))
        lines = []
        for e in K.edges(face):
            c = BRepAdaptor_Curve(e)
            if c.GetType() == GeomAbs_Line:
                ts = [c.FirstParameter(), c.LastParameter()]
            else:
                ts = [c.FirstParameter() + (c.LastParameter() - c.FirstParameter()) * i / 32 for i in range(33)]
            pts = []
            for u in ts:
                p = c.Value(u)
                q = pl.to_local(np.array([p.X(), p.Y(), p.Z()]))
                pts.append((float(q[0]), float(q[1])))
            lines.append(pts)
        allp = [q for ln in lines for q in ln]
        mx, my = min(q[0] for q in allp), min(q[1] for q in allp)
        Mx = max(q[0] for q in allp)
        for ln in lines:
            moved = [(q[0] - mx + x_at, q[1] - my) for q in ln]
            if len(moved) == 2:
                dw.line("CUT", *moved)
            else:
                dw.polyline("CUT", moved)
            for q in moved:
                lo_all = [min(lo_all[0], q[0]), min(lo_all[1], q[1])]
                hi_all = [max(hi_all[0], q[0]), max(hi_all[1], q[1])]
        x_at += Mx - mx + gap
    dw.save(path, lo_all, hi_all)


def register(api):
    api.info(name="Laser-cut box", version="1.0",
             description="Finger-jointed boxes from flat sheet, and flat panels to DXF for laser cutting")
    fs = api.geom

    def build(ctx):
        W, D, H, t, finger = ctx["width"], ctx["depth"], ctx["height"], ctx["thickness"], ctx["finger"]
        if t <= 0 or min(W, D, H) <= 4 * t:
            ctx.fail("Each side must be more than 4 × the sheet thickness")
        if finger < t:
            ctx.fail("Fingers should be at least as wide as the sheet is thick")
        if ctx["inside"]:
            W, D, H = W + 2 * t, D + 2 * t, H + (2 if ctx["lid"] else 1) * t
        ps = panels(fs, W, D, H, t, finger, ctx["lid"])
        kerf = ctx.get("kerf", 0.0) or 0.0
        if kerf < 0 or kerf > t:
            ctx.fail("The kerf should be between 0 and the sheet thickness")
        if kerf > 0:
            ps = {p: grow(fs, s, kerf / 2) for p, s in ps.items()}
        if ctx["layout"] == "flat":
            ps = lay_flat(fs, ps, t)
        for p, s in ps.items():
            ctx.add_part(s, NAMES[p])
        return None

    api.add_feature("laser_box", "Laser-cut box", build, boolean=False, fields=[
        api.number("width", "Width (X)", 120.0, minimum=1),
        api.number("depth", "Depth (Y)", 80.0, minimum=1),
        api.number("height", "Height (Z)", 60.0, minimum=1),
        api.check("inside", "These are inside sizes", False),
        api.number("thickness", "Sheet thickness", 3.0, minimum=0.1),
        api.number("finger", "Finger width (about)", 12.0, minimum=0.1),
        api.check("lid", "Lid", True),
        api.number("kerf", "Laser kerf (0 for none)", 0.0, minimum=0),
        api.choice("layout", "Show", [("assembled", "Assembled"), ("flat", "Flat, ready to cut")], "assembled"),
        api.note("Choose Flat to lay the panels out, then Plugins ▸ Export flat panels as DXF… writes them for "
                 "the laser cutter. The finger count on each edge is rounded to an odd number. Kerf is the width the "
                 "laser burns away (often 0.1–0.2 mm): each outline is drawn half a kerf bigger all round, so the "
                 "cut parts come out the right size and the joints are snug."),
    ])

    def export_dxf(ctx):
        st = ctx.state
        if st is None or not st.parts:
            ctx.message("There are no parts in this Part Studio.")
            return
        from PySide6.QtWidgets import QFileDialog
        parts = ctx.selected_parts() or list(st.parts.values())
        path, _ = QFileDialog.getSaveFileName(ctx.main, "Export flat panels as DXF", "panels.dxf", "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        write_dxf([p.shape for p in parts], path)
        ctx.message(f"Wrote {len(parts)} panel{'s' if len(parts) != 1 else ''} to<br><b>{path}</b><br><br>"
                    "Each part's biggest flat face is drawn, so lay the panels flat (or pick the ones you want) "
                    "first.", title="Export flat panels")

    api.add_command("Export flat panels as DXF…", export_dxf)
