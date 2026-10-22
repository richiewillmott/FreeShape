"""Builds the hinge example with the Python API: a Part Studio (examples/hinge_parts.fshape) with a base plate,
an arm and a pin, and an assembly (examples/hinge.fasm) that puts them together with a fastened and a
revolute mate. Open hinge.fasm and drag the arm — it swings about the pin.

    python examples/make_hinge.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.assembly import AssemblyDoc
from freeshape.document import Document
from freeshape.sketch import Sketch

HERE = os.path.dirname(os.path.abspath(__file__))


def rect(s, x0, y0, w, h):
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)


def sketch(doc, name, build):
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["name"] = name
    f["params"].update(plane={"kind": "std", "name": "Top"}, sketch=s.to_dict())
    doc.add_feature(f)
    return f["id"]


def extrude(doc, sk, depth, biggest=True):
    sr = doc.state.sketches[sk]
    areas = [K.mass_props(r)["area"] for r in sr.regions]
    i = int(np.argmax(areas)) if biggest else int(np.argmin(areas))
    f = doc.make_feature("extrude")
    f["params"].update(regions=[F.region_ref(sr, sk, sr.regions[i])], op="new", depth=depth)
    doc.add_feature(f)
    return list(doc.state.parts)[-1]


def main():
    studio = Document()
    studio.name = "Hinge parts"
    plate = extrude(studio, sketch(studio, "Plate", lambda s: (rect(s, 0, 0, 60, 40),
                                                                s.add_circle(s.add_point(15, 20), 4))), 10)
    arm = extrude(studio, sketch(studio, "Arm", lambda s: (rect(s, 0, 60, 50, 12),
                                                            s.add_circle(s.add_point(6, 66), 4))), 6)
    pin = extrude(studio, sketch(studio, "Pin", lambda s: s.add_circle(s.add_point(80, 20), 3.9)), 30)
    for pid, name, col in ((plate, "Base plate", (0.62, 0.66, 0.72)), (arm, "Arm", (0.36, 0.56, 0.86)),
                           (pin, "Pin", (0.86, 0.62, 0.30))):
        studio.set_part_override(pid, name=name, color=list(col))
    studio.save(os.path.join(HERE, "hinge_parts.fshape"))

    asm = AssemblyDoc()
    asm.name = "Hinge"
    asm.resolver = lambda src: studio.state.parts[src["part"]]
    src = lambda pid: {"kind": "studio", "uid": studio.uid, "part": pid, "path": studio.path,
                       "studio_name": studio.name}
    i_plate, i_arm, i_pin = asm.insert(src(plate)), asm.insert(src(arm)), asm.insert(src(pin))

    def circle_edge(iid, r, z):
        for i, e in enumerate(K.edges(asm.parts[iid].shape)):
            info = K.edge_info(e)
            if abs(info.get("r", 0) - r) < 1e-6 and abs(info["center"][2] - z) < 1e-6:
                return asm.connector(iid, "edge", i)
        raise RuntimeError("edge not found")

    # the pin goes up through the plate's hole (both bottom edges together, Z flipped)
    m = asm.make_mate("fastened")
    m["a"], m["b"], m["flip"] = circle_edge(i_plate, 4, 0), circle_edge(i_pin, 3.9, 0), True
    asm.add_mate(m)
    # the arm sits on the plate and turns about the hole
    m = asm.make_mate("revolute")
    m["a"], m["b"] = circle_edge(i_plate, 4, 10), circle_edge(i_arm, 4, 0)
    asm.add_mate(m)
    asm.save(os.path.join(HERE, "hinge.fasm"))
    print("wrote hinge_parts.fshape and hinge.fasm —", asm.dof, "degree of freedom left (the hinge)")


if __name__ == "__main__":
    main()
