"""Sub-assemblies: an assembly inserted into another as one rigid group whose parts can be mated to."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeshape import assembly as A, kernel as K  # noqa: E402
from test_assembly import edge, lib, make, mate, near, src  # noqa: E402,F401


def _pinned_plate(lib):
    """A sub-assembly: a plate with a pin fastened in its hole."""
    s = make(lib)
    s.name = "Pinned plate"
    ip, ipin = s.insert(src("plate")), s.insert(src("pin"))
    hole_bot = s.connector(ip, "edge", edge(s, ip, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    pin_bot = s.connector(ipin, "edge", edge(s, ipin, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 0)))
    mate(s, "fastened", hole_bot, pin_bot, flip=True)
    return s, ip, ipin


def _relative(a, i, j):
    return np.linalg.inv(np.array(a.instance(i)["T"])) @ np.array(a.instance(j)["T"])


def _members(a, sid):
    return [i["id"] for i in a.instances if i.get("sub") == sid]


def test_insert_keeps_the_layout_and_counts_as_one_body(lib):
    s, _ip, _ipin = _pinned_plate(lib)
    a = make(lib)
    ground = a.insert(src("ground"))
    sid = a.insert_assembly(s)
    plate, pin = _members(a, sid)
    assert a.sub(sid)["name"] == "Pinned plate <1>"
    assert a.instance(plate)["name"] == "Pinned plate <1> / plate <1>"
    rel0 = _relative(s, *[i["id"] for i in s.instances])
    assert np.allclose(_relative(a, plate, pin), rel0)                  # placed as in the sub-assembly
    assert a.dof == 6                                                   # one free body, not two
    assert a.instance_dof(plate) == 6
    a.move_instance(pin, A.rot_about([0, 0, 1], 0.7, [5, 5, 0]) @ A.translation([30, 0, 0]))
    assert np.allclose(_relative(a, plate, pin), rel0)                  # moving one part moves the lot
    a.set_fixed(pin, True)
    assert all(a.instance(i)["fixed"] for i in (plate, pin)) and a.dof == 0
    del ground


def test_mating_to_a_sub_assembly_part_moves_the_whole_group(lib):
    s, *_ = _pinned_plate(lib)
    a = make(lib)
    iarm = a.insert(src("arm"))                                         # fixed (first)
    sid = a.insert_assembly(s)
    plate, pin = _members(a, sid)
    rel0 = _relative(a, plate, pin)
    arm_hole = a.connector(iarm, "edge", edge(a, iarm, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 6)))
    pin_top = a.connector(pin, "edge", edge(a, pin, lambda e: near(e.get("r", 0), 4) and near(e["center"][2], 30)))
    mate(a, "revolute", arm_hole, pin_top, flip=True)
    assert not a.errors
    assert np.allclose(_relative(a, plate, pin), rel0, atol=1e-6)       # still rigid after solving
    T = np.array(a.instance(pin)["T"])
    top = T[:3, :3] @ np.array([0, 0, 30.0]) + T[:3, 3]
    assert np.allclose(top, [6, 6, 6], atol=1e-5)                       # pin top in the arm's hole
    assert a.dof == 1                                                   # the group turns about the pin
    a.turn_mate(a.mates[0]["id"], degrees=40)
    assert np.allclose(_relative(a, plate, pin), rel0, atol=1e-6) and not a.errors


def test_cycles_save_load_delete_and_follow_edits(lib, tmp_path):
    s, ip, ipin = _pinned_plate(lib)
    a = make(lib)
    a.name = "Top"
    sid = a.insert_assembly(s)
    with pytest.raises(K.KernelError, match="can't contain itself"):
        s.insert_assembly(a)
    with pytest.raises(K.KernelError, match="can't contain itself"):
        a.insert_assembly(a)
    # save and load (the sub-assembly's parts come back from the saved copy)
    path = str(tmp_path / "top.fasm")
    a.save(path)
    b = A.AssemblyDoc.load(path, resolver=a.resolver)
    assert [x["id"] for x in b.subs] == [sid] and len(_members(b, sid)) == 2 and b.dof == 0     # first: fixed
    # the sub-assembly changes in its own tab: a part is added and one moves
    b.asm_resolver = lambda source: s if source.get("uid") == s.uid else None
    blk = s.insert(src("block"), T=A.translation([100, 0, 0]))
    s.move_instance(ipin, A.translation([0, 0, 5]), solve=False)
    b.refresh()
    plate, pin, block = _members(b, sid)
    assert b.instance(block)["sub_inst"] == blk
    assert np.allclose(_relative(b, plate, block), _relative(s, ip, blk))
    assert np.allclose(_relative(b, plate, pin), _relative(s, ip, ipin))
    s.delete_instances([blk])
    b.refresh()
    assert len(_members(b, sid)) == 2
    # deleting one of its parts deletes the whole sub-assembly
    b.delete_instances([pin])
    assert not b.subs and not b.instances
    b.undo()
    assert b.subs and len(b.instances) == 2


def test_sub_assembly_inside_a_sub_assembly(lib):
    s, *_ = _pinned_plate(lib)
    mid = make(lib)
    mid.name = "Middle"
    mid.insert(src("ground"))
    mid.insert_assembly(s)
    top = make(lib)
    top.insert(src("block"))
    sid = top.insert_assembly(mid)
    assert len(_members(top, sid)) == 3 and top.dof == 6                 # everything inside moves as one
    with pytest.raises(K.KernelError, match="can't contain itself"):
        s.insert_assembly(top)
    assert {s.uid, mid.uid} <= top.contained_uids()


def test_insert_a_sub_assembly_from_the_insert_panel():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from test_nav import _window
    app, w, vp = _window()
    studio = w.doc
    w.new_assembly()
    first = w.doc
    w.asm.insert_studio_parts(studio.uid)
    app.processEvents()
    assert len(first.instances) == 1
    w.new_assembly()
    second = w.doc
    w.asm.close_insert()
    w.asm.toggle_insert()
    panel = w.asm.insert_panel
    tops = [panel.tree.topLevelItem(i) for i in range(panel.tree.topLevelItemCount())]
    item = next(t for t in tops if t.text(0).startswith(first.name))
    panel._insert_item(item)
    app.processEvents()
    assert len(second.subs) == 1 and len(second.instances) == 1 and second.instances[0]["sub"]
    w.asm.panel.populate(second)
    assert "sub-assembly" in w.asm.panel.instances.item(0).toolTip()
    w.asm.close_insert()
    # the first assembly changes in its own tab: the second follows when it refreshes
    first.move_instance(first.instances[0]["id"], A.translation([0, 0, 0]))
    first.insert({"kind": "studio", "uid": studio.uid, "part": next(iter(studio.state.parts)),
                  "path": None, "studio_name": studio.name}, T=A.translation([80, 0, 0]))
    second.refresh()
    assert len(second.instances) == 2
