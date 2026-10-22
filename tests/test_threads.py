"""Modelled ISO threads: every result is compared with the exact volume worked out by hand
(Pappus' theorem on the ISO 68-1 profile).  Run:  python -m pytest tests  or  python tests/test_threads.py"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from freeshape import kernel as K
from freeshape import features as F
from freeshape.document import Document
from freeshape.sketch import Sketch


def rod_volume(rmin, rmaj, P, L):
    """Exact volume of an ISO basic threaded rod of length L."""
    h = rmaj - rmin
    A = (0.75 + 0.125) / 2 * P * h
    rc = rmin + h * (0.75 + 2 * 0.125) / (3 * (0.75 + 0.125))
    return math.pi * rmin ** 2 * L + A * 2 * math.pi * rc * (L / P)


def _close(got, exp, rel=1e-4):
    assert abs(got / exp - 1) < rel, (got, exp)


def test_tapped_through_and_blind():
    for size, L in (("M3", 12.0), ("M4", 12.0), ("M8", 9.0)):
        D = float(size[1:]); P = K.ISO_PITCH[size]; rmaj = D / 2; rmin = rmaj - K.THREAD_H * P
        blk = BRepPrimAPI_MakeBox(gp_Pnt(-2 * D, -2 * D, 0), 4 * D, 4 * D, L).Shape()
        nut = K.cut_internal_thread(blk, (0, 0, 0), (0, 0, 1), D, P, 0, L)
        assert K.is_valid(nut) and len(K.solids(nut)) == 1
        _close(K.volume(blk) - K.volume(nut), rod_volume(rmin, rmaj, P, L))
    blk = BRepPrimAPI_MakeBox(gp_Pnt(-10, -10, 0), 20, 20, 12).Shape()
    nut = K.cut_internal_thread(blk, (0, 0, 12), (0, 0, -1), 6, 1.0, 0, 9, extend=(True, False))
    _close(K.volume(blk) - K.volume(nut), rod_volume(3 - K.THREAD_H, 3, 1.0, 9))
    return "through and blind tapped holes exact"


def test_bolt():
    D, P, Lt = 6.0, 1.0, 12.0
    rmaj = D / 2; rmin = rmaj - K.THREAD_H * P
    bolt = K.fuse(K._local_cylinder(rmaj, 0, Lt + 2), K._local_cylinder(5.0, Lt + 2, Lt + 6))
    out = K.external_thread(bolt, (0, 0, 0), (0, 0, 1), rmaj, P, 0, Lt)
    assert K.is_valid(out) and len(K.solids(out)) == 1
    _close(K.volume(out), K.volume(bolt) - math.pi * rmaj ** 2 * Lt + rod_volume(rmin, rmaj, P, Lt))
    return "M6 bolt exact"


def test_left_hand_and_clearance():
    blk = BRepPrimAPI_MakeBox(gp_Pnt(-8, -8, 0), 16, 16, 10).Shape()
    rh = K.cut_internal_thread(blk, (0, 0, 0), (0, 0, 1), 6, 1.0, 0, 10)
    lh = K.cut_internal_thread(blk, (0, 0, 0), (0, 0, 1), 6, 1.0, 0, 10, left=True)
    loose = K.cut_internal_thread(blk, (0, 0, 0), (0, 0, 1), 6, 1.0, 0, 10, clearance=0.15)
    assert K.is_valid(lh) and abs(K.volume(lh) - K.volume(rh)) < 1e-3
    assert K.volume(loose) < K.volume(rh)
    return "left-hand mirrors right-hand; clearance removes more"


def _sketch(doc, plane, build):
    s = Sketch(); build(s); s.solve()
    f = doc.make_feature("sketch"); f["params"].update(plane=plane, sketch=s.to_dict()); doc.add_feature(f)
    return f["id"]


def test_hole_and_thread_features():
    doc = Document()
    def rect(s):
        p = [s.add_point(0, 0), s.add_point(30, 0), s.add_point(30, 20), s.add_point(0, 20)]
        for a, b in zip(p, p[1:] + p[:1]): s.add_line(a, b)
    sk = _sketch(doc, {"kind": "std", "name": "Top"}, rect)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=[F.region_ref(doc.state, sk, doc.state.sketches[sk].regions[0])], depth=8.0, op="new")
    doc.add_feature(ex)
    pid = next(iter(doc.state.parts))
    v0 = K.volume(doc.state.parts[pid].shape)
    hs = _sketch(doc, {"kind": "std", "name": "Top"}, lambda s: s.add_point_entity(s.add_point(10, 10)))
    h = doc.make_feature("hole")
    h["params"].update(sketch=hs, standard="tapped", size="M5", end="through", model_thread=True)
    doc.add_feature(h)
    assert not doc.errors, doc.errors
    _close(v0 - K.volume(doc.state.parts[pid].shape), rod_volume(2.5 - K.THREAD_H * 0.8, 2.5, 0.8, 8.0))
    # a plain Ø5 hole, then the Thread feature recognises it as M6 and taps it
    hs2 = _sketch(doc, {"kind": "std", "name": "Top"}, lambda s: s.add_point_entity(s.add_point(22, 10)))
    h2 = doc.make_feature("hole"); h2["params"].update(sketch=hs2, standard="custom", diameter=5.0, end="through")
    doc.add_feature(h2)
    shape = doc.state.parts[pid].shape
    hole = next(f for f in K.faces(shape) if abs(K.face_info(f).get("r", 0) - 2.5) < 1e-6)
    assert F.thread_spec(doc.state, {"size": "auto"}, hole)["size"] == "M6"
    th = doc.make_feature("thread"); th["params"]["faces"] = [F.make_ref(pid, hole, "face", shape)]
    doc.add_feature(th)
    assert not doc.errors, doc.errors
    return "Hole (modelled M5) and Thread on a Ø5 hole (auto M6)"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            print("ok  ", name, "-", fn())


def test_thread_whole_length_of_plain_rod():
    """Threading the whole face of a plain rod (nothing else left of the part) used to fail."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
    import numpy as np
    from freeshape import kernel as K
    rod = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)), 3, 20).Shape()
    out = K.external_thread(rod, np.zeros(3), np.array([0, 1.0, 0]), 3.0, 1.0, 0.0, 20.0)
    assert K.is_valid(out) and 400 < K.volume(out) < 0.85 * K.volume(rod)
