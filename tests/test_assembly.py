"""Assemblies: mate connectors, the mate solver, dragging, links, files and export (headless)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir

from freeshape import kernel as K
from freeshape import assembly as A
from freeshape.features import Part


def box(x, y, z, at=(0, 0, 0)):
    b = BRepPrimAPI_MakeBox(gp_Pnt(*at), x, y, z).Shape()
    return b


def cyl(r, h, at=(0, 0, 0)):
    return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(*at), gp_Dir(0, 0, 1)), r, h).Shape()


def bar_with_holes(length, width=10, thick=5, r=3):
    b = box(length, width, thick)
    for x in (width / 2, length - width / 2):
        b = K.cut(b, cyl(r, thick + 2, (x, width / 2, -1)))
    return b


@pytest.fixture
def lib():
    plate = K.cut(box(60, 40, 10), cyl(4, 12, (15, 20, -1)))
    return {"plate": plate, "pin": cyl(4, 30), "arm": K.cut(box(50, 12, 6), cyl(4, 8, (6, 6, -1))),
            "block": box(20, 10, 8), "ground": box(120, 20, 5), "bar60": bar_with_holes(60),
            "bar100": bar_with_holes(100), "bar80": bar_with_holes(80)}


def make(lib):
    a = A.AssemblyDoc()
    a.resolver = lambda src: Part("x", src["part"], lib[src["part"]], (0.73, 0.76, 0.80), None)
    return a


def src(name):
    return {"kind": "studio", "uid": "u1", "part": name}


def edge(a, iid, pred):
    for i, e in enumerate(K.edges(a.parts[iid].shape)):
        if pred(K.edge_info(e)):
            return i
    raise AssertionError("edge not found")


def face(a, iid, pred):
    for i, f in enumerate(K.faces(a.parts[iid].shape)):
        if pred(K.face_info(f)):
            return i
    raise AssertionError("face not found")


def near(a, b, tol=1e-6):
    return abs(a - b) < tol


def mate(a, mtype, ca, cb, **kw):
    m = a.make_mate(mtype)
    m["a"], m["b"] = ca, cb
    m.update(kw)
    a.add_mate(m)
    return m


def test_first_instance_fixed_and_others_placed_beside(lib):
    a = make(lib)
    i1 = a.insert(src("plate"))
    i2 = a.insert(src("pin"))
    assert a.instance(i1)["fixed"] and not a.instance(i2)["fixed"]
    lo1, hi1 = K.bbox(a.world_shape(i1))
    lo2, _ = K.bbox(a.world_shape(i2))
    assert lo2[0] > hi1[0]                       # no overlap with what's there
    assert a.instances[1]["name"] == "pin <1>"


def test_connector_frames(lib):
    a = make(lib)
    ip = a.insert(src("plate"))
    top = face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9)
    c = a.connector(ip, "face", top)
    assert np.allclose(c["frame"]["z"], [0, 0, 1]) and np.allclose(c["frame"]["o"], [30, 20, 10], atol=1e-6) is False
    # circular edge of the hole on the top face: centre + outward normal
    e = edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 10))
    c = a.connector(ip, "edge", e)
    assert np.allclose(c["frame"]["o"], [15, 20, 10]) and np.allclose(c["frame"]["z"], [0, 0, 1])
    e = edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0))
    assert np.allclose(a.connector(ip, "edge", e)["frame"]["z"], [0, 0, -1])
    # round face: the end nearest the click
    hole = face(a, ip, lambda f: near(f.get("r", 0), 4))
    c = a.connector(ip, "face", hole, click_world=[19, 20, 9])
    assert np.allclose(c["frame"]["o"], [15, 20, 10]) and np.allclose(c["frame"]["z"], [0, 0, 1])
    c = a.connector(ip, "face", hole, click_world=[19, 20, 1])
    assert np.allclose(c["frame"]["o"], [15, 20, 0]) and np.allclose(c["frame"]["z"], [0, 0, -1])


def test_fastened_pin_in_hole_and_revolute_arm(lib):
    a = make(lib)
    ip, ipin, iarm = a.insert(src("plate")), a.insert(src("pin")), a.insert(src("arm"))
    hole_bot = a.connector(ip, "edge", edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    pin_bot = a.connector(ipin, "edge", edge(a, ipin, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    mate(a, "fastened", hole_bot, pin_bot, flip=True)
    lo, hi = K.bbox(a.world_shape(ipin))
    assert np.allclose(lo, [11, 16, 0], atol=1e-5) and np.allclose(hi, [19, 24, 30], atol=1e-5)
    assert a.instance_dof(ipin) == 0
    hole_top = a.connector(ip, "edge", edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 10)))
    arm_bot = a.connector(iarm, "edge", edge(a, iarm, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    mate(a, "revolute", hole_top, arm_bot)
    lo, hi = K.bbox(a.world_shape(iarm))
    assert near(lo[2], 10, 1e-5) and near(hi[2], 16, 1e-5)
    assert not a.errors and a.dof == 1 and a.instance_dof(iarm) == 1


def test_dragging_through_a_revolute(lib):
    a = make(lib)
    ip, iarm = a.insert(src("plate")), a.insert(src("arm"))
    hole_top = a.connector(ip, "edge", edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 10)))
    arm_bot = a.connector(iarm, "edge", edge(a, iarm, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    mate(a, "revolute", hole_top, arm_bot)
    local = np.array([48, 6, 6.0])
    P = np.array([[1, 0, 0], [0, 1, 0.0]])
    for k in range(1, 10):
        ang = np.radians(10 * k)
        tgt = np.array([15, 20, 16]) + 60 * np.array([np.cos(ang), np.sin(ang), 0])
        a.solve(drag={"inst": iarm, "local": local, "target": tgt, "P": P}, iters=8)
    T = np.array(a.instance(iarm)["T"])
    end = T[:3, :3] @ local + T[:3, 3]
    assert near(np.degrees(np.arctan2(end[1] - 20, end[0] - 15)), 90, 2.0)
    assert near(end[2], 16, 1e-6)                              # still on the plate
    a.solve()
    assert not a.errors


def test_slider_planar_cylindrical_ball_dof(lib):
    expected = {"slider": 1, "planar": 3, "cylindrical": 2, "ball": 3, "fastened": 0, "revolute": 1}
    for t, dof in expected.items():
        a = make(lib)
        ig, ib = a.insert(src("plate")), a.insert(src("block"))
        top = a.connector(ig, "face", face(a, ig, lambda f: f.get("n") and f["n"][2] > 0.9))
        bot = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][2] < -0.9))
        mate(a, t, top, bot)
        assert not a.errors, t
        assert a.dof == dof, (t, a.dof)
        if t in ("planar", "fastened"):
            lo, _ = K.bbox(a.world_shape(ib))
            assert near(lo[2], 10, 1e-5), t                  # sitting on the plate


def test_planar_offset_and_moving_the_unfixed_side(lib):
    a = make(lib)
    ib = a.insert(src("block"))                              # fixed
    ig = a.insert(src("plate"))
    top = a.connector(ig, "face", face(a, ig, lambda f: f.get("n") and f["n"][2] > 0.9))
    bot = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "planar", top, bot, offset=2.0)                  # first connector on the free part
    lo_b, _ = K.bbox(a.world_shape(ib))
    _, hi_g = K.bbox(a.world_shape(ig))
    assert near(lo_b[2], 0) and near(hi_g[2], -2, 1e-5)      # the free plate moved under the block, 2 mm gap


def test_four_bar_closed_loop(lib):
    a = make(lib)
    g = a.insert(src("ground"))
    crank, coupler, rocker = a.insert(src("bar60")), a.insert(src("bar100")), a.insert(src("bar80"))
    # holes in the ground for the two pivots
    a.parts[g] = Part(g, "ground", K.cut(K.cut(lib["ground"], cyl(3, 7, (10, 10, -1))), cyl(3, 7, (110, 10, -1))),
                      (0.7, 0.7, 0.7))

    def hole(iid, x, top):
        z = K.bbox(a.parts[iid].shape)[1][2] if top else 0.0
        return a.connector(iid, "edge", edge(a, iid, lambda e: near(e.get("r", 0), 3) and near(e["center"][0], x)
                                             and near(e["center"][2], z)))
    mate(a, "revolute", hole(g, 10, True), hole(crank, 5, False))
    mate(a, "revolute", hole(crank, 55, True), hole(coupler, 5, False))
    mate(a, "revolute", hole(coupler, 95, True), hole(rocker, 75, False), flip=False)
    # closing the loop: rocker's other end to the ground (it sits 3 layers up, so allow it via offset)
    mate(a, "cylindrical", hole(g, 110, True), hole(rocker, 5, False))
    assert not a.errors, a.errors
    assert a.dof == 1, a.dof                                # a four-bar linkage has one degree of freedom


def test_part_edit_updates_connectors(lib):
    a = make(lib)
    ip, iarm = a.insert(src("plate")), a.insert(src("arm"))
    hole_top = a.connector(ip, "edge", edge(a, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 10)))
    arm_bot = a.connector(iarm, "edge", edge(a, iarm, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    mate(a, "revolute", hole_top, arm_bot)
    lib["plate"] = K.cut(box(60, 40, 14), cyl(4, 16, (15, 20, -1)))     # the plate gets thicker
    a.refresh()
    lo, hi = K.bbox(a.world_shape(iarm))
    assert near(lo[2], 14, 1e-5) and not a.errors


def test_undo_redo_and_delete(lib):
    a = make(lib)
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, pb)
    a.delete_instances([ipin])
    assert len(a.instances) == 1 and not a.mates
    a.undo()
    assert len(a.instances) == 2 and len(a.mates) == 1
    a.undo()
    assert not a.mates
    a.redo()
    assert len(a.mates) == 1


def test_save_load_with_embedded_copy_and_export(lib, tmp_path):
    a = make(lib)
    ip, ipin = a.insert(src("plate")), a.insert(src("pin"))
    a.insert(src("pin"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    pb = a.connector(ipin, "face", face(a, ipin, lambda f: f.get("n") and f["n"][2] < -0.9))
    mate(a, "fastened", top, pb)
    path = str(tmp_path / "t.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path)                            # no Part Studios available at all
    assert len(b.parts) == 3 and len(b.mates) == 1
    assert all("saved in this assembly" in v for v in b.errors.values())
    assert np.allclose(K.bbox(b.world_shape(ipin))[0], K.bbox(a.world_shape(ipin))[0], atol=1e-6)
    bom = {r["name"]: r["qty"] for r in a.bom()}
    assert bom == {"plate": 1, "pin": 2}
    step = str(tmp_path / "t.step")
    a.export(step)
    got = K.import_step_with_colors(step)
    assert len(got) == 3 and sorted(n for _s, n, _c, _f in got) == ["pin", "pin", "plate"]


def test_interference(lib):
    a = make(lib)
    ip, ib = a.insert(src("plate")), a.insert(src("block"))
    assert a.interferences() == []
    a.move_instance(ib, A.translation(-np.array(a.instance(ib)["T"])[:3, 3] + [5, 5, 5]))
    res = a.interferences()
    assert len(res) == 1 and near(res[0][2], 20 * 10 * 5, 1e-3)


def test_step_instances(lib, tmp_path):
    import base64
    p = str(tmp_path / "two.step")
    K.export_step([box(10, 10, 10), box(5, 5, 5, (20, 0, 0))], p)
    a = A.AssemblyDoc()
    key = a.add_step_blob(base64.b64encode(open(p, "rb").read()).decode())
    for i in range(2):
        a.insert({"kind": "step", "blob": key, "index": i, "filename": "two.step"}, T=np.eye(4))
    assert len(a.parts) == 2 and not a.errors
    path = str(tmp_path / "s.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path)
    assert len(b.parts) == 2 and not b.errors


def test_conflicting_mate_is_left_out(lib):
    a = make(lib)
    ip, ib = a.insert(src("plate")), a.insert(src("block"))
    top = a.connector(ip, "face", face(a, ip, lambda f: f.get("n") and f["n"][2] > 0.9))
    bot = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][2] < -0.9))
    side = a.connector(ib, "face", face(a, ib, lambda f: f.get("n") and f["n"][0] > 0.9))
    m1 = mate(a, "fastened", top, bot)
    m2 = mate(a, "fastened", top, side)                 # can't also hold: only this one turns red
    assert m2["id"] in a.errors and m1["id"] not in a.errors
    assert near(K.bbox(a.world_shape(ib))[0][2], 10, 1e-5)
    a.set_suppressed(m2["id"], True)
    assert not a.errors
