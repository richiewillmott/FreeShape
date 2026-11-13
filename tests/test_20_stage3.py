"""2.0 stage 3: standard parts, exploded views, gear / rack and pinion / screw relations, instance patterns."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K, assembly as A, stdparts as SP  # noqa: E402
from freeshape.features import Part  # noqa: E402


def T(x, y, z):
    M = np.eye(4)
    M[:3, 3] = (x, y, z)
    return M


# ------------------------------------------------------------------ standard parts
def test_every_standard_part_builds_as_one_valid_solid():
    n = 0
    for fam, (_label, sizes, _lens, _col) in SP.FAMILIES.items():
        for size in sizes:
            lens = SP.lengths_for(fam, size) or [None]
            for L in {lens[0], lens[-1]}:
                shp = SP.build({"family": fam, "size": size, "length": L})
                assert K.is_valid(shp) and len(K.solids(shp)) == 1, (fam, size, L)
                n += 1
    assert n > 100


def test_standard_part_sizes_and_placement():
    lo, hi = K.bbox(SP.build({"family": "iso4762", "size": "M5", "length": 16}))
    assert abs(lo[2] + 16) < 1e-5 and abs(hi[2] - 5) < 1e-5 and abs(hi[0] - 4.25) < 1e-5    # head Ø8.5 × 5
    d, D, B = SP.BEARINGS["608"]
    lo, hi = K.bbox(SP.build({"family": "bearing", "size": "608"}))
    assert abs(hi[0] - D / 2) < 1e-5 and abs(hi[2] - B) < 1e-5
    nut = SP.build({"family": "iso4032", "size": "M8"})
    lo, hi = K.bbox(nut)
    assert abs((hi[1] - lo[1]) - 13.0) < 1e-6                     # 13 across the flats
    assert SP.name({"family": "iso4762", "size": "M5", "length": 16}) == "ISO 4762 M5 × 16"
    assert all(1.5 * 3 <= L <= 36 for L in SP.lengths_for("iso4762", "M3"))


def test_standard_parts_in_an_assembly_save_and_bom(tmp_path):
    a = A.AssemblyDoc()
    for spec, n in (({"kind": "std", "family": "iso4762", "size": "M5", "length": 16}, 2),
                    ({"kind": "std", "family": "iso4032", "size": "M5"}, 2),
                    ({"kind": "std", "family": "bearing", "size": "608"}, 1)):
        for _ in range(n):
            a.insert(dict(spec), checkpoint=False)
    assert not a.errors
    assert [(r["name"], r["qty"]) for r in a.bom()] == [("ISO 4762 M5 × 16", 2), ("ISO 4032 M5", 2),
                                                         ("Bearing 608 (8×22×7)", 1)]
    a.save(str(tmp_path / "s.fasm"))
    b = A.AssemblyDoc.load(str(tmp_path / "s.fasm"))
    assert len(b.parts) == 5 and not b.errors


# ------------------------------------------------------------------ exploded view
def _bolted_plate():
    plate = K.cut(BRepPrimAPI_MakeBox(60, 40, 8).Shape(), K.cylinder((30, 20, -1), (0, 0, 1), 3.2, 10))
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", "Plate", plate, (0.6, 0.7, 0.8), None)
    p = a.insert({"kind": "studio", "uid": "u", "part": "plate"}, T(0, 0, 0), checkpoint=False)
    s = a.insert({"kind": "std", "family": "iso4762", "size": "M3", "length": 16}, T(30, 20, 8), checkpoint=False)
    w = a.insert({"kind": "std", "family": "iso7089", "size": "M3"}, T(30, 20, -0.5), checkpoint=False)
    n = a.insert({"kind": "std", "family": "iso4032", "size": "M3"}, T(30, 20, -2.9), checkpoint=False)
    for iid, z in ((w, -0.5), (n, -2.9)):
        a.instance(iid)["T"] = T(30, 20, z).tolist()
    return a, p, s, w, n


def test_auto_explode_separates_a_bolted_stack(tmp_path):
    a, p, s, w, n = _bolted_plate()
    assert a.auto_explode() == 3
    def box(iid):
        return K.bbox(K.located(a.parts[iid].shape, a.display_T(iid, exploded=True)))
    plate_lo, plate_hi = box(p)
    assert box(s)[0][2] > plate_hi[2]                           # the screw comes right out of its hole, upwards
    assert box(w)[1][2] < plate_lo[2]                           # washer below the plate
    assert box(n)[1][2] < box(w)[0][2]                          # nut below the washer
    assert np.allclose(a.T(s)[:3, 3], [30, 20, 8])              # real positions untouched
    half = a.display_T(s, exploded=True, amount=0.5)[2, 3]
    assert abs(half - (8 + a.explode_offsets()[s][2] / 2)) < 1e-9
    a.save(str(tmp_path / "e.fasm"))
    assert len(A.AssemblyDoc.load(str(tmp_path / "e.fasm")).explode["steps"]) == 3
    a.delete_instances([n])
    assert all(n not in st["ids"] for st in a.explode["steps"])


# ------------------------------------------------------------------ relations
def _gearbox():
    lib = {"base": BRepPrimAPI_MakeBox(200, 80, 5).Shape(), "g20": K.cylinder((0, 0, 0), (0, 0, 1), 20, 8),
           "g40": K.cylinder((0, 0, 0), (0, 0, 1), 40, 8), "rack": BRepPrimAPI_MakeBox(120, 10, 8).Shape()}
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", src["part"], lib[src["part"]], (0.7, 0.7, 0.7), None)
    b = a.insert({"kind": "studio", "uid": "u", "part": "base"}, T(0, 0, 0), checkpoint=False)
    g1 = a.insert({"kind": "studio", "uid": "u", "part": "g20"}, T(40, 40, 5), checkpoint=False)
    g2 = a.insert({"kind": "studio", "uid": "u", "part": "g40"}, T(100, 40, 5), checkpoint=False)

    def mate(t, ia, oa, za, ib, zb, x=(1.0, 0, 0)):
        m = a.make_mate(t)
        m["a"] = {"inst": ia, "frame": {"o": list(map(float, oa)), "z": list(za), "x": list(x)}}
        m["b"] = {"inst": ib, "frame": {"o": [0.0, 0, 0], "z": list(zb), "x": list(x)}}
        a.add_mate(m, checkpoint=False)
        return m["id"]
    m1 = mate("revolute", b, (40, 40, 5), (0, 0, 1.0), g1, (0, 0, -1.0))
    m2 = mate("revolute", b, (100, 40, 5), (0, 0, 1.0), g2, (0, 0, -1.0))
    return a, lib, b, g1, g2, m1, m2, mate


def _angle(a, iid):
    R = a.T(iid)[:3, :3]
    return math.degrees(math.atan2(R[1, 0], R[0, 0]))


def test_gear_relation_turns_by_the_tooth_ratio_past_half_a_turn():
    a, lib, b, g1, g2, m1, m2, _mate = _gearbox()
    dof = a.dof
    rel = a.add_relation("gear", m1, m2, ratio=20 / 40, reverse=True)
    assert a.dof == dof - 1
    a.turn_mate(m1, degrees=90)
    assert abs(_angle(a, g1) - 90) < 0.05 and abs(_angle(a, g2) + 45) < 0.05
    a.turn_mate(m1, degrees=270)                                   # a full turn in all
    assert abs(math.degrees(rel["acc"][1] - rel["base"][1]) + 180) < 0.1


def test_rack_and_pinion_and_screw():
    a, lib, b, g1, g2, m1, m2, mate = _gearbox()
    r = a.insert({"kind": "studio", "uid": "u", "part": "rack"}, T(20, 10, 5), checkpoint=False)
    ms = mate("slider", b, (0, 10, 5), (1.0, 0, 0), r, (-1.0, 0, 0), x=(0, 1.0, 0))
    a.add_relation("rack", m1, ms, radius=20, reverse=False)
    x0 = a.T(r)[0, 3]
    a.turn_mate(m1, degrees=90)
    assert abs(abs(a.T(r)[0, 3] - x0) - 2 * math.pi * 20 / 4) < 0.01

    lib["rod"], lib["nut"] = K.cylinder((0, 0, 0), (0, 0, 1), 3, 100), K.cylinder((0, 0, 0), (0, 0, 1), 6, 10)
    rod = a.insert({"kind": "studio", "uid": "u", "part": "rod"}, T(170, 40, 5), checkpoint=False)
    a.set_fixed(rod, True)
    nut = a.insert({"kind": "studio", "uid": "u", "part": "nut"}, T(170, 40, 30), checkpoint=False)
    mc = mate("cylindrical", rod, (0, 0, 0), (0, 0, 1.0), nut, (0, 0, -1.0))
    a.add_relation("screw", mc, pitch=2.0)
    z0 = a.T(nut)[2, 3]
    a.turn_mate(mc, degrees=720)
    assert abs(abs(a.T(nut)[2, 3] - z0) - 4.0) < 0.01


def test_relation_type_checks():
    a, lib, b, g1, g2, m1, m2, _mate = _gearbox()
    for args in (("rack", m1, m2), ("screw", m1, None), ("gear", m1, m1)):
        try:
            a.add_relation(*args)
            assert False, args
        except K.KernelError:
            pass


# ------------------------------------------------------------------ patterns
def test_instance_patterns_follow_their_seed_and_edit(tmp_path):
    lib = {"base": BRepPrimAPI_MakeBox(100, 100, 5).Shape(), "nut": K.cylinder((0, 0, 0), (0, 0, 1), 6, 10)}
    c = A.AssemblyDoc()
    c.resolver = lambda src: Part("x", src["part"], lib[src["part"]], (0.7, 0.7, 0.7), None)
    c.insert({"kind": "studio", "uid": "u", "part": "base"}, T(0, 0, 0), checkpoint=False)
    bolt = c.insert({"kind": "studio", "uid": "u", "part": "nut"}, T(30, 0, 5), checkpoint=False)
    pat = c.add_pattern("circular", [bolt], 6, angle=360, axis={"kind": "world", "name": "Z"})
    copies = [i for row in pat["copies"] for i in row]
    assert len(copies) == 5 and all(c.instance(i)["fixed"] for i in copies)
    assert all(abs(np.hypot(c.T(i)[0, 3], c.T(i)[1, 3]) - 30) < 1e-9 for i in copies)
    c.move_instance(bolt, T(10, 0, 0))
    assert all(abs(np.hypot(c.T(i)[0, 3], c.T(i)[1, 3]) - 40) < 1e-9 for i in copies)
    c.set_fixed(copies[0], False)
    assert c.instance(copies[0])["fixed"]                         # a copy stays placed by its pattern
    c.edit_pattern(pat["id"], count=4)
    assert len(c.instances) == 2 + 3
    lp = c.add_pattern("linear", [bolt], 3, spacing=15, axis={"kind": "world", "name": "Y"})
    assert np.allclose(c.T(lp["copies"][1][0])[:3, 3] - c.T(bolt)[:3, 3], [0, 30, 0])
    c.save(str(tmp_path / "p.fasm"))
    d = A.AssemblyDoc.load(str(tmp_path / "p.fasm"))
    assert len(d.patterns) == 2 and len(d.instances) == len(c.instances)
    c.delete_instances([bolt])                                    # the seed takes its copies with it
    assert len(c.instances) == 1 and not c.patterns
