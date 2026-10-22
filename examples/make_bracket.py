"""Builds examples/bracket.fshape with the Python API — handy as a scripting example too.

    python examples/make_bracket.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.document import Document
from freeshape.sketch import Sketch, ORIGIN


def rect_profile_L(s: Sketch):
    """An L-shaped bracket profile, fully constrained."""
    pts = [s.add_point(*p) for p in [(0, 0), (60, 0), (60, 6), (6, 6), (6, 45), (0, 45)]]
    lines = [s.add_line(a, b) for a, b in zip(pts, pts[1:] + pts[:1])]
    s.points.pop(pts[0])
    s.merge_points(ORIGIN, pts[0])   # first corner sits on the origin
    for l, t in zip(lines, ["horizontal", "vertical", "horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, l)
    s.add_constraint("distance", lines[0], value=60).update(pos=[30, -8])
    s.add_constraint("distance", lines[1], value=6).update(pos=[68, 3])
    s.add_constraint("distance", lines[5], value=45).update(pos=[-8, 22])
    s.add_constraint("distance", lines[4], value=6).update(pos=[3, 52])
    s.solve()
    return lines


def main():
    doc = Document()
    doc.name = "Bracket"

    # 1. L profile on the Front plane, extruded symmetrically
    sk = doc.make_feature("sketch")
    sk["name"] = "Bracket profile"
    sk["params"]["plane"] = {"kind": "std", "name": "Front"}
    s = Sketch()
    rect_profile_L(s)
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    region = doc.state.sketches[sk["id"]].regions[0]
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], region)], end="symmetric", depth=40.0, op="new")
    doc.add_feature(ex)
    doc.hidden_features.add(sk["id"])
    part = next(iter(doc.state.parts.values()))

    # 2. Fillet the inside corner of the L
    inner = [e for e in K.edges(part.shape)
             if (ln := K.edge_line(e)) is not None and abs(abs(ln[1][1]) - 1) < 1e-6
             and abs(ln[0][0] - 6) < 1e-6 and abs(ln[0][2] - 6) < 1e-6]
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, e, "edge", part.shape) for e in inner], radius=5.0)
    doc.add_feature(fi)

    # 3. Two mounting holes through the base, placed with sketch points on the Top plane
    hs = doc.make_feature("sketch")
    hs["name"] = "Hole positions"
    hs["params"]["plane"] = {"kind": "std", "name": "Top"}
    s2 = Sketch()
    for y in (-12.0, 12.0):
        p = s2.add_point(40.0, y)
        s2.add_point_entity(p)
        s2.add_constraint("hdistance", p, ORIGIN, value=40.0)
        s2.add_constraint("vdistance", p, ORIGIN, value=12.0)
    s2.solve()
    hs["params"]["sketch"] = s2.to_dict()
    doc.add_feature(hs)
    ho = doc.make_feature("hole")
    ho["params"].update(sketch=hs["id"], diameter=6.5, end="through", style="simple")
    doc.add_feature(ho)
    doc.hidden_features.add(hs["id"])

    # 4. Slot in the upright, sketched on the Right-facing face of the upright... here on the Right plane
    ss = doc.make_feature("sketch")
    ss["name"] = "Slot"
    ss["params"]["plane"] = {"kind": "std", "name": "Right"}
    s3 = Sketch()
    a, b = s3.add_point(-8, 28), s3.add_point(8, 28)
    c, d = s3.add_point(8, 36), s3.add_point(-8, 36)
    l1, l2 = s3.add_line(a, b), s3.add_line(c, d)
    ca, cb = s3.add_point(8, 32), s3.add_point(-8, 32)
    s3.add_arc(ca, b, c)
    s3.add_arc(cb, d, a)
    s3.add_constraint("horizontal", l1)
    s3.add_constraint("horizontal", l2)
    s3.solve()
    ss["params"]["sketch"] = s3.to_dict()
    doc.add_feature(ss)
    regs = doc.state.sketches[ss["id"]].regions
    cutx = doc.make_feature("extrude")
    cutx["name"] = "Slot cut"
    cutx["params"].update(regions=[F.region_ref(doc.state, ss["id"], r) for r in regs], end="through", op="remove")
    doc.add_feature(cutx)
    doc.hidden_features.add(ss["id"])

    # 5. Chamfer the outer top edge of the upright
    part = next(iter(doc.state.parts.values()))
    top = [e for e in K.edges(part.shape)
           if (ln := K.edge_line(e)) is not None and abs(abs(ln[1][1]) - 1) < 1e-6
           and abs(ln[0][0]) < 1e-6 and abs(ln[0][2] - 45) < 1e-6]
    ch = doc.make_feature("chamfer")
    ch["params"].update(edges=[F.make_ref(part.id, e, "edge", part.shape) for e in top], distance=2.0)
    doc.add_feature(ch)

    doc.set_part_override(part.id, name="Bracket")
    out = os.path.join(os.path.dirname(__file__), "bracket.fshape")
    doc.save(out)
    print("errors:", doc.errors or "none")
    print("volume: %.1f mm^3" % K.volume(doc.state.parts[part.id].shape))
    print("saved", out)


if __name__ == "__main__":
    main()
