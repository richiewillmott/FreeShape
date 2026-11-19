"""Lighten: cut a pattern of pockets (triangles, hexagons or circles) into a flat face, leaving ribs of a set width
and a solid border round the edges and every hole — the classic weight-saving pattern for robot, drone and
machine plates.

Pick one or more flat faces. Pockets go right through the part, or to a depth that leaves a floor. Pockets that
would be cut short by the border are kept if they're still big enough, and their corners are rounded.
"""
import math


def _plane_of(face):
    from freeshape import kernel as K
    pl = K.planar_face_plane(face)
    if pl is None:
        raise K.KernelError("Lighten needs flat faces")
    return pl


def _offset_face(face, dist):
    """The face grown (dist > 0) or shrunk (dist < 0) by dist — holes shrink or grow the other way. A list of
    faces (shrinking can split it); [] when nothing is left."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
    from OCP.GeomAbs import GeomAbs_Arc
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from freeshape import kernel as K
    if abs(dist) < 1e-9:
        return [face]
    mo = BRepOffsetAPI_MakeOffset(face, GeomAbs_Arc)
    try:
        mo.Perform(float(dist))
    except Exception:
        return []
    if not mo.IsDone():
        return []
    wires = []
    ex = TopExp_Explorer(mo.Shape(), TopAbs_WIRE)
    while ex.More():
        wires.append(K._as_wire(ex.Current()))
        ex.Next()
    faces = []
    for w in wires:
        mf = BRepBuilderAPI_MakeFace(w, True)
        if mf.IsDone():
            faces.append(mf.Face())
    return faces


def _area(shape):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, g)
    return abs(g.Mass())


def _faces(shape):
    from freeshape import kernel as K
    return K.faces(shape)


def allowed_region(face, margin):
    """Where pockets may go: the face shrunk by the margin from its outside edge and from every hole."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    pieces = _offset_face(face, -margin)
    if not pieces:
        return None
    pieces.sort(key=_area, reverse=True)
    region = pieces[0]                                  # the outside boundary, shrunk
    for hole in pieces[1:]:                             # the holes, grown
        region = BRepAlgoAPI_Cut(region, hole).Shape()
    return region


def cells(kind, size, rib, centre, extent):
    """2D polygons (or circles as (cx, cy, r)) covering a square of half-size `extent` round `centre`."""
    cx, cy = centre
    out = []
    if kind == "triangle":
        s = size                                       # side of the triangle grid
        h = s * math.sqrt(3) / 2
        inset = rib / 2 * 2 * math.sqrt(3)             # shrink the side so the ribs are `rib` wide
        s2 = s - inset
        if s2 <= 0:
            return []
        rows = int(extent / h) + 2
        cols = int(extent / (s / 2)) + 2
        for j in range(-rows, rows + 1):
            for i in range(-cols, cols + 1):
                up = (i + j) % 2 == 0
                x = cx + i * s / 2
                y0 = cy + j * h
                gy = y0 + (h / 3 if up else 2 * h / 3)  # centroid
                r = s2 / math.sqrt(3)                  # circumradius of the shrunk triangle
                a0 = math.pi / 2 if up else -math.pi / 2
                out.append([(x + r * math.cos(a0 + k * 2 * math.pi / 3), gy + r * math.sin(a0 + k * 2 * math.pi / 3))
                            for k in range(3)])
    elif kind == "hexagon":
        f = size                                       # across flats, centre to centre
        f2 = f - rib
        if f2 <= 0:
            return []
        R = f2 / math.sqrt(3)
        dy = f * math.sqrt(3) / 2
        rows = int(extent / dy) + 2
        cols = int(extent / f) + 2
        for j in range(-rows, rows + 1):
            for i in range(-cols, cols + 1):
                x = cx + i * f + (f / 2 if j % 2 else 0)
                y = cy + j * dy
                out.append([(x + R * math.cos(math.pi / 6 + k * math.pi / 3),
                             y + R * math.sin(math.pi / 6 + k * math.pi / 3)) for k in range(6)])
    else:                                              # circles on a hexagonal grid
        d2 = size - rib
        if d2 <= 0:
            return []
        dy = size * math.sqrt(3) / 2
        rows = int(extent / dy) + 2
        cols = int(extent / size) + 2
        for j in range(-rows, rows + 1):
            for i in range(-cols, cols + 1):
                out.append((cx + i * size + (size / 2 if j % 2 else 0), cy + j * dy, d2 / 2))
    return out


def pocket_faces(face, kind, size, rib, margin, radius, min_frac=0.25):
    """The flat pocket shapes on the face: pattern cells clipped to the allowed region, corners rounded."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from freeshape import api as fs
    pl = _plane_of(face)
    region = allowed_region(face, margin)
    if region is None:
        return [], pl
    # the face's extent in its own plane, and its middle
    import numpy as np
    (x0, y0, z0), (x1, y1, z1) = fs.bbox(face)
    corners = [pl.to_local(np.array([x, y, z])) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    us = [c[0] for c in corners]
    vs = [c[1] for c in corners]
    centre = ((min(us) + max(us)) / 2, (min(vs) + max(vs)) / 2)
    extent = max(max(us) - min(us), max(vs) - min(vs)) / 2 + size
    full = None
    out = []
    for c in cells(kind, size, rib, centre, extent):
        f = fs.circle(c[2], (c[0], c[1]), on=pl) if isinstance(c, tuple) else fs.polygon(c, on=pl)
        if full is None:
            full = _area(f)
        piece = BRepAlgoAPI_Common(f, region).Shape()
        for pf in _faces(piece):
            a = _area(pf)
            if a < full * min_frac:
                continue
            if radius > 0:                              # round the corners: shrink, then grow back
                inner = _offset_face(pf, -radius)
                rounded = []
                for q in inner:
                    rounded += _offset_face(q, radius)
                if not rounded:
                    continue
                pf = max(rounded, key=_area)
            out.append(pf)
    return out, pl


def register(api):
    api.info(name="Lighten", version="1.0",
             description="Weight-saving pockets (triangles, hexagons or circles) in flat faces")
    fs = api.geom

    def build(ctx):
        import numpy as np
        from freeshape import kernel as K
        faces = ctx.faces("faces")
        if not faces:
            ctx.fail("Pick one or more flat faces")
        size, rib, margin, radius = ctx["size"], ctx["rib"], ctx["margin"], ctx["radius"]
        if rib <= 0 or size <= rib * 2:
            ctx.fail("The cells must be more than twice the rib width")
        if radius * 2 >= size - rib:
            ctx.fail("The corner radius is too big for the cells")
        # each picked face's part (found before anything is cut, while the faces still belong to them)
        by_part = {}
        for face in faces:
            owner = None
            for part in ctx.parts():
                if any(face.IsSame(pf) for pf in fs.faces(part.shape)):
                    owner = part
                    break
            if owner is None:
                ctx.fail("A picked face doesn't belong to a part")
            by_part.setdefault(owner.id, (owner, []))[1].append(face)
        done = 0
        for owner, fl in by_part.values():
            tools = []
            for face in fl:
                pockets, pl = pocket_faces(face, ctx["kind"], size, rib, margin, radius,
                                       0.6 if ctx["kind"] == "circle" else 0.25)
                if not pockets:
                    continue
                # which way is into the material: towards the part's centre of mass
                com = np.asarray(fs.centre_of_mass(owner.shape))
                n = np.asarray(pl.normal, float)
                into = -n if (com - np.asarray(pl.origin)) @ n < 0 else n
                if ctx["through"]:
                    lo, hi = fs.bbox(owner.shape)
                    depth = float(np.linalg.norm(np.asarray(hi) - np.asarray(lo))) + 2
                else:
                    depth = ctx["depth"]
                    if depth <= 0:
                        ctx.fail("The pocket depth must be more than 0")
                tools += [fs.move(fs.extrude(p, depth + 0.01, direction=into), *(-into * 0.01)) for p in pockets]
                done += len(pockets)
            if tools:
                ctx.replace_part(owner.id, fs.subtract(owner.shape, K.compound(tools)))
        if not done:
            ctx.fail("No pockets fit: make the cells smaller or the margin narrower")
        return None

    api.add_feature("lighten", "Lighten (pockets)", build, boolean=False, fields=[
        api.select("faces", "Flat faces", kinds="face"),
        api.choice("kind", "Pattern", [("triangle", "Triangles (isogrid)"), ("hexagon", "Hexagons"),
                                       ("circle", "Circles")], "triangle"),
        api.number("size", "Cell size", 20.0, minimum=1),
        api.number("rib", "Rib width", 2.0, minimum=0.1),
        api.number("margin", "Border (edges and holes)", 4.0, minimum=0),
        api.number("radius", "Corner radius", 1.0, minimum=0),
        api.check("through", "Through the part", True),
        api.number("depth", "Depth (if not through)", 3.0, minimum=0.01),
        api.note("Cell size is the triangle side, or the distance between hexagon / circle centres. Pockets "
                 "trimmed by the border are kept when at least a quarter of a full cell is left."),
    ])
