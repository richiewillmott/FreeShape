"""The example plugins that come with FreeShape: Gridfinity, timing pulley, Lighten, laser-cut box, batch export."""
import base64
import itertools
import math
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import api as fs, features as F, kernel as K, plugins as P  # noqa: E402
from freeshape.document import Document  # noqa: E402


@pytest.fixture(autouse=True)
def examples():
    P.load_all([P.examples_dir()])
    sys.path.insert(0, P.examples_dir())
    yield
    sys.path.remove(P.examples_dir())
    P._unload()


def _feature(ftype, **params):
    doc = Document()
    f = doc.make_feature("plugin:" + ftype)
    f["params"].update(params)
    doc.add_feature(f)
    return doc


def _bb(shape):
    lo, hi = K.bbox(shape)
    return [round(hi[i] - lo[i], 3) for i in range(3)]


# --------------------------------------------------------------------------- Gridfinity
def test_gridfinity_bin_fits_the_grid():
    doc = _feature("gridfinity", nx=2, ny=1, units=3, div_x=1, magnets=True)
    assert not doc.errors
    (part,) = doc.state.parts.values()
    assert K.is_valid(part.shape) and len(K.solids(part.shape)) == 1
    assert _bb(part.shape) == [83.5, 41.5, 21 + 4.4]          # 42 × units − 0.5, 7 mm × height + stacking lip


def test_gridfinity_bin_stacks_into_the_one_below():
    import gridfinity as G
    low = G.bin_solid(fs, 1, 1, 3)
    up = fs.move(G.bin_solid(fs, 1, 1, 3), 0, 0, 21)          # the next bin sits 7 × 3 mm higher
    assert K.volume(fs.intersect(low, up)) < 1.0             # its foot fits the lip without clashing


def test_gridfinity_baseplate():
    doc = _feature("gridfinity", kind="baseplate", nx=3, ny=2)
    (part,) = doc.state.parts.values()
    assert not doc.errors and K.is_valid(part.shape) and _bb(part.shape) == [126.0, 84.0, 4.65]


# --------------------------------------------------------------------------- timing pulley
@pytest.mark.parametrize("profile, teeth, od", [("gt2_2", 20, 12.224), ("gt2_2", 40, 24.955), ("htd_5", 30, 46.603)])
def test_timing_pulley_outside_diameter(profile, teeth, od):
    doc = _feature("timing_pulley", profile=profile, teeth=teeth, flange=0, hub_l=0, bore=0)
    (part,) = doc.state.parts.values()
    assert not doc.errors and K.is_valid(part.shape)
    w = _bb(part.shape)
    assert w[2] == pytest.approx(6.5)
    assert od - 0.05 < max(w[0], w[1]) <= od + 1e-3            # lands are on the outside diameter
    import timing_pulley as TP
    assert TP.sizes(profile, teeth)[0] == pytest.approx(teeth * TP.PROFILES[profile][0] / math.pi)


def test_timing_pulley_with_flanges_hub_and_bore():
    doc = _feature("timing_pulley", teeth=20)
    (part,) = doc.state.parts.values()
    assert not doc.errors and K.is_valid(part.shape)
    assert _bb(part.shape)[2] == pytest.approx(7 + 1 + 6.5 + 1)
    assert "between 10 and 200" in _feature("timing_pulley", teeth=5).errors.popitem()[1]


# --------------------------------------------------------------------------- Lighten
def _plate_doc():
    plate = fs.subtract(fs.box(120, 80, 5), fs.cylinder(6, 5, at=(20, 20, 0)))
    doc = Document()
    imp = doc.make_feature("import")
    fd, tmp = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    K.export_brep([plate], tmp)
    with open(tmp, "rb") as fh:
        imp["params"].update(data=base64.b64encode(fh.read()).decode(), filename="plate.brep")
    os.unlink(tmp)
    doc.add_feature(imp)
    pid = next(iter(doc.state.parts))
    sh = doc.state.parts[pid].shape
    top = max(fs.faces(sh), key=lambda f: fs.bbox(f)[0][2])
    return doc, pid, F.make_ref(pid, top, "face", sh), K.volume(plate)


@pytest.mark.parametrize("kind", ["triangle", "hexagon", "circle"])
def test_lighten_removes_material_but_keeps_the_border(kind):
    doc, pid, ref, v0 = _plate_doc()
    f = doc.make_feature("plugin:lighten")
    f["params"].update(faces=[["face", ref]], kind=kind, margin=4.0)
    doc.add_feature(f)
    sh = doc.state.parts[pid].shape
    assert not doc.errors and K.is_valid(sh)
    assert 0.3 * v0 < K.volume(sh) < 0.75 * v0
    border = fs.subtract(fs.box(120, 80, 5), fs.box(112, 72, 5, at=(4, 4, 0)))
    assert K.volume(fs.intersect(sh, border)) == pytest.approx(K.volume(border), rel=1e-6)   # edges untouched
    ring = fs.subtract(fs.cylinder(10, 5, at=(20, 20, 0)), fs.cylinder(6, 5, at=(20, 20, 0)))
    assert K.volume(fs.intersect(sh, ring)) == pytest.approx(K.volume(ring), rel=1e-6)       # and round the hole


def test_lighten_pocket_leaves_a_floor():
    doc, pid, ref, v0 = _plate_doc()
    f = doc.make_feature("plugin:lighten")
    f["params"].update(faces=[["face", ref]], through=False, depth=3.0)
    doc.add_feature(f)
    sh = doc.state.parts[pid].shape
    assert not doc.errors and K.is_valid(sh)
    assert K.volume(fs.intersect(sh, fs.box(120, 80, 2))) == pytest.approx(120 * 80 * 2 - math.pi * 36 * 2, rel=1e-6)


# --------------------------------------------------------------------------- laser-cut box
@pytest.mark.parametrize("lid", [True, False])
def test_laser_box_panels_fill_the_walls_without_overlapping(lid):
    import laser_box as LB
    ps = LB.panels(fs, 120, 80, 60, 3, 12, lid)
    assert all(K.is_valid(s) for s in ps.values())
    inside = 114 * 74 * (54 if lid else 57)
    assert sum(K.volume(s) for s in ps.values()) == pytest.approx(120 * 80 * 60 - inside)
    for a, b in itertools.combinations(ps.values(), 2):
        assert K.volume(fs.intersect(a, b)) < 1e-6


def test_laser_box_feature_names_the_panels_and_lays_them_flat(tmp_path):
    doc = _feature("laser_box", layout="flat", inside=True, width=100, depth=60, height=40)
    assert not doc.errors
    names = sorted(p.name for p in doc.state.parts.values())
    assert names == sorted(["Bottom", "Lid", "Front", "Back", "Left side", "Right side"])
    for p in doc.state.parts.values():
        lo, hi = K.bbox(p.shape)
        assert lo[2] == pytest.approx(0, abs=1e-6) and hi[2] == pytest.approx(3)        # flat, one sheet thick
    import laser_box as LB
    path = str(tmp_path / "box.dxf")
    LB.write_dxf([p.shape for p in doc.state.parts.values()], path)
    txt = open(path).read()
    assert txt.startswith("  0\nSECTION") and txt.count("\nLINE\n") > 100


# --------------------------------------------------------------------------- batch export
def test_batch_export_names_files_after_parts(tmp_path):
    import batch_export as BE
    doc = _feature("laser_box")
    parts = list(doc.state.parts.values())
    written = BE.export_parts(fs, parts, str(tmp_path), "both")
    files = sorted(os.listdir(tmp_path))
    assert len(written) == 12 and "Bottom.stl" in files and "Lid.step" in files
    assert BE.file_name('a/b:c*', set()) == "a_b_c_"
    used = set()
    assert [BE.file_name("Part", used) for _ in range(3)] == ["Part", "Part (2)", "Part (3)"]


def test_laser_box_kerf_grows_every_outline_by_half_a_kerf():
    doc0 = _feature("laser_box", layout="flat")
    doc1 = _feature("laser_box", layout="flat", kerf=0.2)
    assert not doc1.errors
    for a, b in zip(doc0.state.parts.values(), doc1.state.parts.values()):
        wa, wb = _bb(a.shape), _bb(b.shape)
        assert wb[0] == pytest.approx(wa[0] + 0.2) and wb[1] == pytest.approx(wa[1] + 0.2) and wb[2] == wa[2]
        assert len(K.faces(a.shape)) == len(K.faces(b.shape))          # same fingers, just bigger
    assert "kerf" in _feature("laser_box", kerf=5).errors.popitem()[1]
