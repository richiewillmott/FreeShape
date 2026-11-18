"""Measure value: a #variable measured from the model, re-measured on every rebuild."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import features as F, kernel as K, measure_value as MV  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _box(depth=5.0, hole=False):
    """20 × 10 × depth box from the origin; optionally a Ø4 hole through its middle."""
    doc = Document()
    s = Sketch()
    a, b, c, d = s.add_point(0, 0), s.add_point(20, 0), s.add_point(20, 10), s.add_point(0, 10)
    for x, y in ((a, b), (b, c), (c, d), (d, a)):
        s.add_line(x, y)
    if hole:
        s.add_circle(s.add_point(10, 5), 2.0)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    sr = doc.state.sketches[sk["id"]]
    region = max(sr.regions, key=lambda r: K.face_info(r)["area"]) if hole else sr.regions[0]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(doc.state, sk["id"], region)], depth=depth, op="new")
    doc.add_feature(e)
    pid = next(iter(doc.state.parts))
    return doc, e, pid


def _measure(doc, **kw):
    f = doc.make_feature("measure_value")
    f["params"].update(kw)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f


def _vref(doc, pid, pt):
    sh = doc.state.parts[pid].shape
    return F.make_vertex_ref(pid, min(K.vertex_points(sh), key=lambda q: float(sum(abs(q - pt)))), sh)


def _face(doc, pid, key):
    sh = doc.state.parts[pid].shape
    f = max(K.faces(sh), key=key)
    return F.make_ref(pid, f, "face", sh)


def test_distance_between_vertices_follows_the_model():
    doc, e, pid = _box()
    f = _measure(doc, name="d", mtype="distance",
                 entities=[["vertex", _vref(doc, pid, (0, 0, 0))], ["vertex", _vref(doc, pid, (20, 10, 5))]])
    assert abs(doc.state.vars["d"] - math.sqrt(525)) < 1e-9
    e = dict(e, params=dict(e["params"], depth=8.0))
    doc.replace_feature(e)
    assert abs(doc.state.vars["d"] - math.sqrt(564)) < 1e-9
    assert "#d = 23.749 mm" in MV.label(doc.feature(f["id"]), doc.state)


def test_distance_components_methods_and_rounding():
    doc, _e, pid = _box()
    ents = [["vertex", _vref(doc, pid, (0, 0, 0))], ["vertex", _vref(doc, pid, (20, 10, 5))]]
    _measure(doc, name="dz", mtype="distance", component="z", entities=ents)
    _measure(doc, name="r", mtype="distance", entities=ents, round_to=0.5)
    assert doc.state.vars["dz"] == pytest.approx(5)
    assert doc.state.vars["r"] == pytest.approx(23.0)
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    bottom = _face(doc, pid, lambda f: -K.interior_point(f)[2])
    _measure(doc, name="t", mtype="distance", entities=[["face", top], ["face", bottom]])
    _measure(doc, name="far", mtype="distance", method="max", entities=[["face", top], ["face", bottom]])
    assert doc.state.vars["t"] == pytest.approx(5)
    assert doc.state.vars["far"] == pytest.approx(math.sqrt(525))


def test_area_volume_length_size_centroid_count():
    doc, _e, pid = _box()
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    _measure(doc, name="a", mtype="area", entities=[["face", top]])
    _measure(doc, name="v", mtype="volume", entities=[["part", pid]])
    _measure(doc, name="per", mtype="length", entities=[["face", top]])
    _measure(doc, name="sx", mtype="size", axis_c="x", entities=[["part", pid]])
    _measure(doc, name="cy", mtype="centroid", axis_c="y", entities=[["part", pid]])
    _measure(doc, name="n", mtype="count", count_mode="picks", entities=[["part", pid], ["face", top]])
    _measure(doc, name="z", mtype="coordinate", axis_c="z", entities=[["vertex", _vref(doc, pid, (20, 10, 5))]])
    v = doc.state.vars
    assert (v["a"], v["v"], v["per"], v["sx"], v["cy"], v["n"], v["z"]) == pytest.approx(
        (200, 1000, 60, 20, 5, 2, 5))


def test_diameter_angle_and_centre_distance():
    doc, _e, pid = _box(hole=True)
    sh = doc.state.parts[pid].shape
    cyl = next(f for f in K.faces(sh) if MV._geom("face", f)["type"] == "cylinder")
    cref = F.make_ref(pid, cyl, "face", sh)
    side = _face(doc, pid, lambda f: -K.interior_point(f)[0])           # the x = 0 side
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    _measure(doc, name="dia", mtype="diameter", entities=[["face", cref]])
    _measure(doc, name="ang", mtype="angle", entities=[["face", top], ["face", side]])
    _measure(doc, name="cc", mtype="distance", method="center", entities=[["face", cref], ["face", side]])
    v = doc.state.vars
    assert (v["dia"], v["ang"], v["cc"]) == pytest.approx((4, 90, 10))


def test_measured_variable_drives_a_later_feature():
    doc, _e, pid = _box()
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    bottom = _face(doc, pid, lambda f: -K.interior_point(f)[2])
    _measure(doc, name="t", mtype="distance", entities=[["face", top], ["face", bottom]])
    s = Sketch()
    s.add_circle(s.add_point(40, 0), 3.0)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                       depth="#t * 2", op="new")
    doc.add_feature(e)
    assert not doc.errors
    new = next(p for i, p in doc.state.parts.items() if i.startswith(e["id"]))
    assert K.volume(new.shape) == pytest.approx(math.pi * 9 * 10, rel=1e-6)


def test_bad_name_and_missing_picks_are_errors():
    doc, _e, _pid = _box()
    f = doc.make_feature("measure_value")
    f["params"].update(name="2x", mtype="volume", entities=[])
    doc.add_feature(f)
    assert "Variable names" in doc.errors.get(f["id"], "")


def test_prefill_from_measure_panel():
    assert MV.prefill([("vertex", {}), ("edge", {})])["mtype"] == "distance"
    assert MV.prefill([("face", {})])["mtype"] == "area"
    assert MV.prefill([("part", "p1")])["mtype"] == "volume"
    assert MV.prefill([("edge", {}), ("edge", {})])["mtype"] == "distance"
    assert MV.prefill([("edge", {})])["mtype"] == "length"


def _sketch_id(doc):
    return next(f["id"] for f in doc.features if f["type"] == "sketch")


def test_sketch_points_and_curves_drive_a_variable():
    """Vertices may be sketch points (line ends, centres) and the variable follows an edit of the sketch."""
    import copy
    doc, _e, _pid = _box()
    sid = _sketch_id(doc)
    sr = doc.state.sketches[sid]
    a = next(k for k, q in sr.sketch.points.items() if q == [0.0, 0.0] and k != "origin")
    c = next(k for k, q in sr.sketch.points.items() if q == [20.0, 10.0])
    line = next(k for k, e in sr.sketch.entities.items() if e["type"] == "line"
                and {sr.sketch.points[e["p1"]][1], sr.sketch.points[e["p2"]][1]} == {0.0})
    _measure(doc, name="diag", mtype="distance",
             entities=[["vertex", F.sketch_point_ref(sr, sid, a)], ["vertex", F.sketch_point_ref(sr, sid, c)]])
    _measure(doc, name="bottom", mtype="length", entities=[["sketch_line", {"sketch": sid, "entity": line}]])
    assert doc.state.vars["diag"] == pytest.approx(math.sqrt(500))
    assert doc.state.vars["bottom"] == pytest.approx(20)
    skf = copy.deepcopy(doc.feature(sid))
    s = Sketch.from_dict(skf["params"]["sketch"])
    for k, q in s.points.items():
        if q[0] == 20.0:
            s.points[k] = [30.0, q[1]]
    skf["params"]["sketch"] = s.to_dict()
    doc.replace_feature(skf)
    assert not doc.errors, doc.errors
    assert doc.state.vars["diag"] == pytest.approx(math.sqrt(1000))
    assert doc.state.vars["bottom"] == pytest.approx(30)


def test_prefill_with_the_model_knows_surfaces_and_round_edges():
    doc, _e, pid = _box(hole=True)
    sh = doc.state.parts[pid].shape
    circ = next(e for e in K.edges(sh) if MV._geom("edge", e)["type"] == "circle")
    assert MV.prefill([("edge", F.make_ref(pid, circ, "edge", sh))], doc.state)["mtype"] == "diameter"
    assert MV.prefill([("part", pid)], doc.state)["mtype"] == "volume"
    s = Sketch()
    line = s.add_line(s.add_point(0, 30), s.add_point(20, 30))
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    ex = doc.make_feature("extrude")
    ex["params"].update(creation="surface", curves=[{"kind": "sketch_chain", "sketch": sk["id"], "entity": line}],
                        depth=7, op="new")
    doc.add_feature(ex)
    surf = next(k for k in doc.state.parts if k.startswith(ex["id"]))
    p = MV.prefill([("part", surf)], doc.state)
    assert p["mtype"] == "area"                      # a surface body: its area, not a volume of 0
    assert MV.headline(doc.state, [("part", surf)])[:2] == ("Area", "140 mm²")
    assert MV.prefill([("sketch_line", {"sketch": sk["id"], "entity": line})], doc.state)["mtype"] == "length"


def test_measure_bar_creates_the_variable_from_two_vertices():
    """As in Onshape: pick two vertices, the corner bar shows 'Min dist', and its (x) button opens Measure value
    with Distance and the two picks filled in."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from test_ui_fixes import _window
    from freeshape.ui.viewport import Pick
    doc, _e, pid = _box()
    app, w = _window(doc)
    vp = w.viewport
    assert vp._allowed("vertex") and vp._allowed("sketch_point")           # on by default, as in Onshape
    pts = K.vertex_points(doc.state.parts[pid].shape)
    near = lambda q: min(range(len(pts)), key=lambda i: float(sum(abs(pts[i] - q))))  # noqa: E731
    sid = _sketch_id(doc)
    far = next(k for k, q in doc.state.sketches[sid].sketch.points.items() if q == [20.0, 10.0])
    vp.selection = [Pick("vertex", (pid, near((0, 0, 5)))), Pick("sketch_point", (sid, far))]
    vp.selectionChanged.emit()
    app.processEvents()
    assert not w.measure.isHidden() and not w.measure.create.isHidden()
    assert "Min dist:" in w.measure.value.text() and "22.913 mm" in w.measure.value.text()
    w.measure.create.click()
    app.processEvents()
    p = w.dialog.feature["params"]
    assert (p["mtype"], p["method"], p["component"]) == ("distance", "min", "total")
    assert [k for k, _ in p["entities"]] == ["vertex", "vertex"]
    w.dialog.accept()
    app.processEvents()
    assert doc.state.vars["d"] == pytest.approx(math.sqrt(525))


# --------------------------------------------------------------------------- options of the Onshape feature
def _plane_at(doc, z):
    pl = doc.make_feature("plane")
    pl["params"].update(type="offset", refs=[{"k": "plane", "ref": {"kind": "std", "name": "Top"}}], offset=z)
    doc.add_feature(pl)
    return pl["id"]


def _sketch_on(doc, plane_ref, build):
    s = Sketch()
    out = build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=plane_ref, sketch=s.to_dict())
    doc.add_feature(f)
    return f["id"], out


def test_infinite_propagation_extends_faces():
    doc, _e, pid = _box()
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    sid, pt = _sketch_on(doc, {"kind": "std", "name": "Top"}, lambda s: s.add_point(50, 5))
    sr = doc.state.sketches[sid]
    ents = [["face", top], ["vertex", F.sketch_point_ref(sr, sid, pt)]]
    _measure(doc, name="near", mtype="distance", method="min", entities=ents)
    _measure(doc, name="inf", mtype="distance", method="inf", entities=ents)
    assert doc.state.vars["near"] == pytest.approx(math.hypot(30, 5))
    assert doc.state.vars["inf"] == pytest.approx(5)          # to the top face's plane, extended


def test_ray_distance_closest_index_box_and_offset():
    doc, _e, pid = _box(hole=True)
    plid = _plane_at(doc, 2.5)
    sid, line = _sketch_on(doc, {"kind": "plane", "fid": plid},
                           lambda s: s.add_line(s.add_point(-10, 5), s.add_point(-5, 5)))
    ray = [["axis", {"kind": "sketch_line", "sketch": sid, "line": line}]]
    tgt = [["part", pid]]
    _measure(doc, name="r0", mtype="ray", ray=ray, entities=tgt)
    _measure(doc, name="r2", mtype="ray", ray=ray, entities=tgt, ray_closest=False, ray_index=2)
    _measure(doc, name="rl", mtype="ray", ray=ray, entities=tgt, ray_closest=False, ray_index=-1)
    _measure(doc, name="rb", mtype="ray", ray=ray, entities=tgt, ray_closest=False, ray_box=True, ray_index=0)
    _measure(doc, name="ro", mtype="ray", ray=ray, entities=tgt, ray_offset=3)
    v = doc.state.vars
    assert (v["r0"], v["r2"], v["rl"], v["rb"], v["ro"]) == pytest.approx((10, 22, 30, 10, 10))


def test_angle_flip_and_matching_count():
    doc, _e, pid = _box(hole=True)
    sh = doc.state.parts[pid].shape
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    side = _face(doc, pid, lambda f: -K.interior_point(f)[0])
    cyl = next(f for f in K.faces(sh) if MV._geom("face", f)["type"] == "cylinder")
    circ = next(e for e in K.edges(sh) if MV._geom("edge", e)["type"] == "circle")
    _measure(doc, name="a", mtype="angle", flip_angle=True, entities=[["face", top], ["face", side]])
    _measure(doc, name="tops", mtype="count", entities=[["face", top]])
    _measure(doc, name="holes", mtype="count", entities=[["face", F.make_ref(pid, cyl, "face", sh)]])
    _measure(doc, name="rims", mtype="count", entities=[["edge", F.make_ref(pid, circ, "edge", sh)]])
    v = doc.state.vars
    assert (v["a"], v["tops"], v["holes"], v["rims"]) == pytest.approx((90, 2, 1, 2))


def test_local_and_along_coordinates_framed_box_and_inertia():
    doc, _e, pid = _box()
    plid = _plane_at(doc, 2.5)
    corner = [["vertex", _vref(doc, pid, (20, 10, 5))]]
    frame = [["plane", {"kind": "plane", "fid": plid}]]
    _measure(doc, name="lz", mtype="coordinate", coord_type="local", axis_c="z", frame=frame, entities=corner)
    _measure(doc, name="al", mtype="coordinate", coord_type="along", entities=corner,
             frame=[["axis", {"kind": "std", "name": "Y"}]])
    _measure(doc, name="bx", mtype="size", axis_c="x", entities=[["part", pid]],
             frame=[["plane", {"kind": "std", "name": "Front"}]])
    _measure(doc, name="iz", mtype="inertia", axis_c="z", density=1.0, entities=[["part", pid]])
    v = doc.state.vars
    # box 20 × 10 × 5 of 1 g/cm³ weighs 1 g; about the world Z axis: (20² + 10²) / 12 + (10² + 5²) g·mm²
    assert (v["lz"], v["al"], v["iz"]) == pytest.approx((2.5, 10, 500 / 12 + 125))
    assert v["bx"] == pytest.approx(20, abs=1e-6)


def test_old_count_without_mode_counts_picks():
    doc, _e, pid = _box()
    top = _face(doc, pid, lambda f: K.interior_point(f)[2])
    f = doc.make_feature("measure_value")
    p = f["params"]
    p.update(name="n", mtype="count", entities=[["face", top]])
    del p["count_mode"]                                   # as saved by 2.2.0
    doc.add_feature(f)
    assert doc.state.vars["n"] == 1
