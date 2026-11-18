"""Options added to finish the parity list (issue #27): chamfer and fillet types, revolve two directions, sweep
profile control, loft surface / thin, hole up-to ends, pattern second direction and merge types."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _sketch(doc, build, plane=None):
    s = Sketch()
    ids = build(s)
    s.solve()
    sk = doc.make_feature("sketch")
    if plane:
        sk["params"]["plane"] = plane
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    return sk["id"], ids


def _rect(x0, y0, x1, y1):
    def build(s):
        a, b, c, d = s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)
        return [s.add_line(u, v) for u, v in ((a, b), (b, c), (c, d), (d, a))]
    return build


def _add(doc, ftype, **kw):
    f = doc.make_feature(ftype)
    f["params"].update(kw)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f


def _region(doc, sid, i=0):
    return F.region_ref(doc.state, sid, doc.state.sketches[sid].regions[i])


def _box(doc, w=20, h=10, d=5):
    sid, _ = _sketch(doc, _rect(0, 0, w, h))
    _add(doc, "extrude", regions=[_region(doc, sid)], depth=float(d), op="new")
    return next(iter(doc.state.parts.values()))


def _edge_along(part, axis, at):
    """Ref of the box edge running along `axis` (0 x, 1 y, 2 z) through point `at` (other coords)."""
    for e in K.edges(part.shape):
        lo, hi = K.bbox(e)
        if hi[axis] - lo[axis] > 1 and all(abs(lo[k] - at[k]) < 1e-6 for k in range(3) if k != axis):
            return F.make_ref(part.id, e, "edge", part.shape)
    raise AssertionError("edge not found")


def _vol(doc):
    return sum(K.volume(p.shape) for p in doc.state.parts.values() if K.solids(p.shape))


# ------------------------------------------------------------------ chamfer / fillet
@pytest.mark.parametrize("kw,cut", [
    (dict(type="two", distance=1.0, distance2=3.0), 1.0 * 3.0 / 2 * 20),
    (dict(type="angle", distance=2.0, angle=45.0), 2.0 * 2.0 / 2 * 20),
    (dict(type="angle", distance=2.0, angle=30.0), 2.0 * 2.0 * math.tan(math.radians(30)) / 2 * 20),
])
def test_chamfer_types(kw, cut):
    doc = Document()
    part = _box(doc)
    _add(doc, "chamfer", edges=[_edge_along(part, 0, (0, 0, 5))], **kw)
    assert abs(_vol(doc) - (1000 - cut)) < 1e-6


def test_chamfer_flip_swaps_sides():
    tops = []
    for flip in (False, True):
        doc = Document()
        part = _box(doc)
        _add(doc, "chamfer", edges=[_edge_along(part, 0, (0, 0, 5))], type="two", distance=1.0, distance2=3.0,
             flip=flip)
        p = next(iter(doc.state.parts.values()))
        top = [K.face_info(f)["area"] for f in K.faces(p.shape)
               if K.face_info(f).get("n") is not None and abs(K.face_info(f)["n"][2] - 1) < 1e-9]
        tops.append(round(top[0], 6))
    assert sorted(tops) == [20 * 7, 20 * 9]          # the 3 mm and the 1 mm side swap over


def test_variable_fillet():
    doc = Document()
    part = _box(doc)
    _add(doc, "fillet", edges=[_edge_along(part, 0, (0, 0, 5))], radius=1.0, variable=True, radius2=3.0)
    removed = 1000 - _vol(doc)
    corner = 1 - math.pi / 4
    assert corner * 1 * 20 < removed < corner * 9 * 20          # between an all-1 and an all-3 fillet
    assert K.is_valid(next(iter(doc.state.parts.values())).shape)


# ------------------------------------------------------------------ revolve / sweep / loft
def test_revolve_two_directions():
    doc = Document()
    sid, _ = _sketch(doc, _rect(10, 0, 12, 5))
    _add(doc, "revolve", regions=[_region(doc, sid)], axis={"kind": "std", "name": "Y"}, end="two",
         angle=90.0, angle2=45.0)
    ring = math.pi * (12 ** 2 - 10 ** 2) * 5
    assert abs(_vol(doc) - ring * 135 / 360) < 1e-6
    p = next(iter(doc.state.parts.values()))
    lo, hi = K.bbox(p.shape)
    assert lo[2] < -1 and hi[2] > 1          # it goes both ways from the sketch plane


def _bent_path(doc):
    """On the Front plane: straight up 20, then 45° up and across (20, 20) — one connected chain."""
    def build(s):
        a, b, c = s.add_point(0, 0), s.add_point(0, 20), s.add_point(20, 40)
        return [s.add_line(a, b), s.add_line(b, c)]
    return _sketch(doc, build, plane={"kind": "std", "name": "Front"})


@pytest.mark.parametrize("control,vol", [("none", 4 * 20 * (1 + math.sqrt(2))), ("lock_direction", 4 * 20 * (1 + math.sqrt(2))),
                                         ("keep", 4 * 40)])
def test_sweep_profile_control(control, vol):
    doc = Document()
    sid, _ = _sketch(doc, _rect(-1, -1, 1, 1))
    pid, pids = _bent_path(doc)
    _add(doc, "sweep", regions=[_region(doc, sid)], path=[{"kind": "sketch_chain", "sketch": pid, "entity": pids[0]}],
         control=control, lock_std="Y")
    p = next(iter(doc.state.parts.values()))
    assert K.is_valid(p.shape)
    # turning to follow the path, the 2×2 square sweeps its full length; kept flat, only its height counts
    assert abs(_vol(doc) - vol) < 0.02 * vol


def test_loft_surface_thin_and_path():
    doc = Document()
    a, _ = _sketch(doc, _rect(0, 0, 10, 10))
    pf = doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=20.0)
    doc.add_feature(pf)
    b, _ = _sketch(doc, _rect(2, 2, 8, 8), plane={"kind": "plane", "fid": pf["id"]})
    profiles = [{"kind": "region", "ref": _region(doc, a)}, {"kind": "region", "ref": _region(doc, b)}]
    _add(doc, "loft", creation="surface", profiles=profiles)
    sheet = [p for p in doc.state.parts.values() if not K.solids(p.shape)]
    assert len(sheet) == 1 and len(K.faces(sheet[0].shape)) == 4          # four sloping sides, open ends
    doc.features.pop()
    doc.rebuild()
    _add(doc, "loft", creation="thin", profiles=profiles, thickness=1.0, thin_side="other", op="new")
    solid = [p for p in doc.state.parts.values() if K.solids(p.shape)]
    full = (10 * 10 + 6 * 6 + math.sqrt(100 * 36)) / 3 * 20                 # frustum of the outer outlines
    inner = (8 * 8 + 4 * 4 + math.sqrt(64 * 16)) / 3 * 20
    assert len(solid) == 1 and abs(K.volume(solid[0].shape) - (full - inner)) < 1e-3


@pytest.mark.parametrize("control", ["guide", "lock_faces"])
def test_sweep_guide_and_lock_faces(control):
    doc = Document()
    wall = _box(doc, 30, 10, 30)                                       # a box whose front face the path runs on
    front = next(f for f in K.faces(wall.shape) if K.face_info(f).get("n") is not None
                 and abs(K.face_info(f)["n"][1] + 1) < 1e-9)
    sid, _ = _sketch(doc, _rect(4, -2, 6, 0))                        # 2×2 square touching the front face
    pid, pids = _sketch(doc, lambda s: [s.add_line(s.add_point(5, 0), s.add_point(5, 20))],
                        plane={"kind": "std", "name": "Front"})       # straight up the front face
    gid, gids = _sketch(doc, lambda s: [s.add_line(s.add_point(6, 0), s.add_point(6, 20))],
                        plane={"kind": "std", "name": "Front"})
    extra = dict(guide=[{"kind": "sketch_chain", "sketch": gid, "entity": gids[0]}]) if control == "guide" else \
        dict(support=[F.make_ref(wall.id, front, "face", wall.shape)])
    _add(doc, "sweep", regions=[_region(doc, sid)], path=[{"kind": "sketch_chain", "sketch": pid, "entity": pids[0]}],
         control=control, op="new", **extra)
    swept = [p for p in doc.state.parts.values() if p.id != wall.id]
    assert len(swept) == 1 and abs(K.volume(swept[0].shape) - 4 * 20) < 0.01      # guide mode approximates


# ------------------------------------------------------------------ hole / patterns / mirror
def _plate_with_holes_sketch(doc, ends_at):
    part = _box(doc, 40, 20, 10)
    sid, _ = _sketch(doc, lambda s: [s.add_point(10, 10), s.add_point(30, 10)],
                     plane={"kind": "std", "name": "Top"})
    return part, sid


def test_hole_up_to_next_and_face():
    doc = Document()
    part = _box(doc, 40, 20, 10)
    # a sketch on the plate's top face with two hole centres
    top = next(f for f in K.faces(part.shape) if K.face_info(f).get("n") is not None
               and abs(K.face_info(f)["n"][2] - 1) < 1e-9)
    ref = F.make_ref(part.id, top, "face", part.shape)
    sid, _ = _sketch(doc, lambda s: [s.add_point_entity(s.add_point(-10, 0)), s.add_point_entity(s.add_point(10, 0))],
                     plane={"kind": "face", "face": ref})
    _add(doc, "hole", sketch=sid, standard="custom", diameter=4.0, end="up_to_next", tip=False)
    hole = math.pi * 4 * 10
    assert abs(_vol(doc) - (40 * 20 * 10 - 2 * hole)) < 1e-3          # right through the 10 mm plate
    doc.features.pop()
    doc.rebuild()
    pf = doc.make_feature("plane")
    pf["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=4.0)
    doc.add_feature(pf)
    _add(doc, "hole", sketch=sid, standard="custom", diameter=4.0, end="up_to_face", tip=False,
         up_to={"kind": "plane", "ref": {"kind": "plane", "fid": pf["id"]}}, offset=1.0)
    assert abs(_vol(doc) - (40 * 20 * 10 - 2 * math.pi * 4 * 5)) < 1e-3    # down to z=4, stopping 1 short


def test_linear_pattern_grid_and_remove():
    doc = Document()
    part = _box(doc, 5, 5, 5)
    _add(doc, "linear_pattern", parts=[part.id], count=3, spacing=10.0, second=True, count2=2, spacing2=10.0,
         op="new")
    assert len(doc.state.parts) == 6 and abs(_vol(doc) - 6 * 125) < 1e-6
    doc = Document()
    plate = _box(doc, 60, 10, 4)
    sid, _ = _sketch(doc, _rect(2, 2, 6, 6))
    _add(doc, "extrude", regions=[_region(doc, sid)], end="symmetric", depth=40.0, op="new")
    cutter = [p for p in doc.state.parts.values() if p.id != plate.id][0]
    _add(doc, "linear_pattern", parts=[cutter.id], count=4, spacing=12.0, op="remove", scope=[plate.id])
    vols = sorted(K.volume(p.shape) for p in doc.state.parts.values())
    assert abs(vols[-1] - (60 * 10 * 4 - 3 * 16 * 4)) < 1e-6          # three copies punched through the plate


def _plate_with_boss_and_pocket(doc):
    plate = _box(doc, 80, 30, 5)
    top = next(f for f in K.faces(plate.shape) if K.face_info(f).get("n") is not None
               and abs(K.face_info(f)["n"][2] - 1) < 1e-9)
    sid, _ = _sketch(doc, lambda s: [s.add_circle(s.add_point(-30, 0), 4.0)],
                     plane={"kind": "face", "face": F.make_ref(plate.id, top, "face", plate.shape)})
    _add(doc, "extrude", regions=[_region(doc, sid)], depth=6.0, op="add")
    plate = next(iter(doc.state.parts.values()))
    boss = [F.make_ref(plate.id, f, "face", plate.shape) for f in K.faces(plate.shape)
            if K.face_info(f)["c"][0] < 15 and K.face_info(f)["c"][2] > 5 - 1e-6 and K.face_info(f)["area"] < 200]
    return plate, boss


def test_pattern_and_mirror_faces():
    doc = Document()
    plate, boss = _plate_with_boss_and_pocket(doc)
    assert len(boss) == 2                                              # the boss's side and top
    before = _vol(doc)
    _add(doc, "linear_pattern", type="faces", faces=boss, count=3, spacing=20.0)
    assert abs(_vol(doc) - (before + 2 * math.pi * 16 * 6)) < 1e-3 and len(doc.state.parts) == 1
    doc.features.pop()
    doc.rebuild()
    _add(doc, "mirror", type="faces", faces=boss, plane={"kind": "std", "name": "Right"})
    assert abs(_vol(doc) - (before + math.pi * 16 * 6)) < 1e-3
