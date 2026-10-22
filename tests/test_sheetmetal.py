"""Sheet metal: base, flanges (up / down, inside / outside, any angle), flanges on flanges, and exact flat
patterns (bend allowance from the K-factor), with holes cut by normal features carried into the flat."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K, sheetmetal as SM  # noqa: E402

T, R, KF = 2.0, 2.0, 0.44


def base(w=100, h=60):
    face = SM._polygon_face([(0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0)])
    return SM.make_base(face, K.std_plane("Top"), T, R, KF)


def edge(m, panel, pred):
    return next(e for e in m.panel_edges(panel) if pred(e[0], e[1]))


def ba(angle, r=R):
    return math.radians(angle) * (r + KF * T)


def test_base_is_a_flat_sheet():
    shape, m = base()
    assert abs(K.volume(shape) - 100 * 60 * T) < 1e-6
    assert len(m.panel_edges(m.panels[0])) == 4


@pytest.mark.parametrize("position", ["outside", "inside"])
def test_flange_and_flat_length(position):
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=25, angle=90, position=position)
    lo, hi = K.bbox(shape)
    assert abs(hi[2] - 25) < 1e-6                              # 25 to the outside of the bend
    if position == "outside":
        assert abs(lo[1] - (-(R + T))) < 1e-6                 # the part grew by the bend
    else:
        assert abs(lo[1]) < 1e-6                               # outline kept
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    fp = SM.flat_pattern(shape, m)
    flo, fhi = K.bbox(fp.face)
    flange_flat = 25 - (R + T)
    trimmed = R + T if position == "inside" else 0.0
    assert abs((fhi[1] - flo[1]) - (60 - trimmed + ba(90) + flange_flat)) < 1e-6
    assert len(K.faces(fp.face)) == 1 and len(fp.bend_lines) == 1


def test_flange_angles_and_down():
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[0] - 100) < 1e-9 and abs(b[0] - 100) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=45, up=False)
    lo, hi = K.bbox(shape)
    assert lo[2] < -5 and hi[2] <= T + 1e-6                    # bent downwards
    fp = SM.flat_pattern(shape, m)
    flo, fhi = K.bbox(fp.face)
    setback = (R + T) * math.tan(math.radians(22.5))
    assert abs((fhi[0] - flo[0]) - (100 + ba(45) + 20 - setback)) < 1e-6
    assert fp.bend_lines[0][2].startswith("DOWN 45°")


def test_flange_on_a_flange_makes_a_channel_and_unfolds():
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=90)
    child = m.panels[1]
    # the free end of the new flange (its far edge, v = its flat length)
    far = max(m.panel_edges(child), key=lambda t: t[0][1] + t[1][1])
    shape, m = SM.add_flange(shape, m, child, *far, length=15, angle=90)
    assert len(m.bends) == 2 and K.is_valid(shape)
    fp = SM.flat_pattern(shape, m)
    flo, fhi = K.bbox(fp.face)
    want = 60 + ba(90) + (20 - (R + T)) + ba(90) + (15 - (R + T))
    assert abs((fhi[1] - flo[1]) - want) < 1e-6


def test_holes_cut_later_show_in_the_flat():
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=25, angle=90)
    shape = K.cut(shape, K.cylinder((30, 30, -5), (0, 0, 1), 4, 20))          # in the base
    shape = K.cut(shape, K.cylinder((50, -20, 15), (0, 1, 0), 3, 30))         # in the flange
    fp = SM.flat_pattern(shape, m)
    radii = sorted(round(K.edge_info(e)["r"], 6) for e in K.edges(fp.face) if "r" in K.edge_info(e))
    assert radii == [3.0, 4.0]


def test_too_short_flange_is_refused():
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    with pytest.raises(K.KernelError):
        SM.add_flange(shape, m, m.panels[0], *e, length=3, angle=90)


def test_flat_dxf(tmp_path):
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=25, angle=90)
    shape = K.cut(shape, K.cylinder((30, 30, -5), (0, 0, 1), 4, 20))
    path = str(tmp_path / "flat.dxf")
    n, nb = SM.flat_dxf(SM.flat_pattern(shape, m), path)
    assert nb == 1 and n >= 5
    try:
        import ezdxf
    except ImportError:
        return
    doc = ezdxf.readfile(path)
    layers = {e.dxf.layer for e in doc.modelspace()}
    assert {"CUT", "BEND", "NOTES"} <= layers


def test_sheet_metal_features_regenerate():
    """Through the document: base + flange as features; changing the thickness rebuilds everything."""
    from freeshape.document import Document
    from freeshape.sketch import Sketch
    from freeshape import features as F
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(80, 0), s.add_point(80, 50), s.add_point(0, 50)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane={"kind": "std", "name": "Top"}, sketch=s.to_dict())
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    b = doc.make_feature("sm_base")
    b["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], thickness=1.5, radius=1.5, k=0.44)
    doc.add_feature(b)
    pid = next(iter(doc.state.parts))
    shp = doc.state.parts[pid].shape
    top_edge = next(e for e in K.edges(shp) if "p0" in K.edge_info(e)
                    and abs(K.edge_info(e)["p0"][1]) < 1e-9 and abs(K.edge_info(e)["p1"][1]) < 1e-9
                    and abs(K.edge_info(e)["p0"][2] - 1.5) < 1e-9)
    fl = doc.make_feature("sm_flange")
    fl["params"].update(edges=[F.make_ref(pid, top_edge, "edge", shp)], length=20)
    doc.add_feature(fl)
    assert not doc.errors and len(doc.state.sheet[pid].bends) == 1
    import copy
    b2 = copy.deepcopy(doc.feature(b["id"]))
    b2["params"]["thickness"] = 3.0
    doc.replace_feature(b2)
    assert not doc.errors and len(doc.state.sheet[pid].bends) == 1
    lo, hi = K.bbox(doc.state.parts[pid].shape)
    assert abs(lo[1] + 4.5) < 1e-6 and abs(hi[2] - 20) < 1e-6


@pytest.mark.parametrize("position", ["outside", "inside"])
def test_corner_relief_where_two_bends_meet(position):
    shape, m = base()
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=90)
    e = edge(m, m.panels[0], lambda a, b: abs(a[0] - b[0]) < 1e-9 and a[0] > 50)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=90, position=position)
    assert len(m.reliefs) == 1 and K.is_valid(shape) and len(K.solids(shape)) == 1
    fp = SM.flat_pattern(shape, m)
    assert len(K.faces(fp.face)) == 1
    # the notch: s = 1.5 × thickness inside the bend lines; the corner of the base is gone
    c = np.array(m.reliefs[0]["c"])
    s = 1.5 * T
    inner = c + np.array(m.reliefs[0]["o1"]) * (-s / 2) + np.array(m.reliefs[0]["o2"]) * (-s / 2)
    assert not K.material_at(shape, (inner[0], inner[1], T / 2))
    assert K.material_at(shape, (inner[0] - 10 * np.sign(m.reliefs[0]["o2"][0] or 1), inner[1] + 10, T / 2))


def test_no_relief_when_switched_off():
    face = SM._polygon_face([(0, 0, 0), (100, 0, 0), (100, 60, 0), (0, 60, 0)])
    shape, m = SM.make_base(face, K.std_plane("Top"), T, R, KF, relief="none")
    e = edge(m, m.panels[0], lambda a, b: abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=90)
    e = edge(m, m.panels[0], lambda a, b: abs(a[0] - b[0]) < 1e-9 and a[0] > 50)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=20, angle=90)
    assert not m.reliefs
