"""QA pass (2.5.0): parametric regeneration — references, part ids, failed features, expression fields.
Tests marked xfail(strict=True) reproduce open defects from QA_REPORT.md; once a defect is fixed the test passes,
which fails the run as a reminder to drop the mark."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import features as F, kernel as K
from freeshape.document import Document
from freeshape.sketch import Sketch, ORIGIN


def _rect(x0, y0, w, h, fix_origin=False):
    s = Sketch()
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    L = [s.add_line(p[i], p[(i + 1) % 4]) for i in range(4)]
    for line, t in zip(L, ["horizontal", "vertical", "horizontal", "vertical"]):
        s.add_constraint(t, line)
    if fix_origin:
        s.add_constraint("coincident", p[0], ORIGIN)
    s.solve()
    return s


def _sketch(doc, s):
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    return sk


def _extrude(doc, sk, **kw):
    ex = doc.make_feature("extrude")
    regs = doc.state.sketches[sk["id"]].regions
    ex["params"].update(regions=[F.region_ref(doc.state, sk["id"], r) for r in regs], **kw)
    doc.add_feature(ex)
    return ex


def _vertical_edge_at(shape, x, y):
    for e in K.edges(shape):
        ln = K.edge_line(e)
        c = np.array(K.edge_info(e)["c"])
        if ln is not None and abs(ln[1][2]) > 0.9 and abs(c[0] - x) < 1e-6 and abs(c[1] - y) < 1e-6:
            return e
    raise AssertionError("edge not found")


def _round_centres(shape, r):
    return [np.round(K.edge_info(e)["center"], 2).tolist() for e in K.edges(shape)
            if "r" in K.edge_info(e) and abs(K.edge_info(e)["r"] - r) < 1e-6]


def _box(w=60.0, h=40.0, depth=15.0):
    doc = Document()
    sk = _sketch(doc, _rect(0, 0, w, h, fix_origin=True))
    _extrude(doc, sk, depth=depth)
    return doc


def test_fillet_on_a_suppressed_pocket_edge_errors_instead_of_moving():
    doc = _box()
    sk2 = _sketch(doc, _rect(25, 15, 10, 10))
    cut = _extrude(doc, sk2, end="through", op="remove", both=True)
    part = next(iter(doc.state.parts.values()))
    edge = _vertical_edge_at(part.shape, 25, 15)
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, edge, "edge", part.shape)], radius=2.0)
    doc.add_feature(fi)
    assert not doc.errors
    doc.set_suppressed(cut["id"], True)
    part = next(iter(doc.state.parts.values()))
    # the pocket's edge is gone: the fillet must report it, not round the box's outer corner
    assert fi["id"] in doc.errors or not _round_centres(part.shape, 2.0), _round_centres(part.shape, 2.0)


def test_suppressing_an_earlier_part_keeps_references_on_their_body():
    doc = Document()
    _extrude(doc, _sketch(doc, _rect(0, 0, 10, 10)), depth=5.0, op="new")
    first = doc.features[-1]
    s = Sketch()
    for x0 in (100.0, 200.0):
        pts = [s.add_point(x0, 0), s.add_point(x0 + 20, 0), s.add_point(x0 + 20, 20), s.add_point(x0, 20)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s.add_line(a, b)
    s.solve()
    ex2 = _extrude(doc, _sketch(doc, s), depth=10.0, op="new")
    left = min((p for pid, p in doc.state.parts.items() if pid.startswith(ex2["id"])),
               key=lambda p: K.bbox(p.shape)[0][0])
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(left.id, _vertical_edge_at(left.shape, 100, 0), "edge", left.shape)],
                        radius=3.0)
    doc.add_feature(fi)
    doc.set_suppressed(first["id"], True)
    rounded = [K.bbox(p.shape)[0][0] for p in doc.state.parts.values() if _round_centres(p.shape, 3.0)]
    assert [round(float(x)) for x in rounded] == [100]


def test_failed_fillet_leaves_the_model_unchanged():
    doc = _box()
    _extrude(doc, _sketch(doc, _rect(0, 0, 2, 2)), depth=2.0, op="new")
    pa, pb = list(doc.state.parts.values())
    vol_a = K.volume(pa.shape)
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(pa.id, _vertical_edge_at(pa.shape, 60, 0), "edge", pa.shape),
                               F.make_ref(pb.id, K.edges(pb.shape)[0], "edge", pb.shape)], radius=3.0)
    doc.add_feature(fi)
    assert fi["id"] in doc.errors
    assert abs(K.volume(doc.state.parts[pa.id].shape) - vol_a) < 1e-6


@pytest.mark.parametrize("ftype,params", [
    ("linear_pattern", dict(count=2, spacing=10.0, second=True, count2="#n", spacing2="#n * 4", op="new")),
    ("fillet", dict(radius=1.0, variable=True, radius2="#n / 3")),
    ("chamfer", dict(type="two", distance=1.0, distance2="#n / 3")),
])
def test_expressions_work_in_every_number_field(ftype, params):
    doc = _box(5, 5, 5)
    v = doc.make_feature("variable")
    v["params"].update(name="n", expr="3")
    doc.add_feature(v, 0)
    part = next(iter(doc.state.parts.values()))
    f = doc.make_feature(ftype)
    if ftype == "linear_pattern":
        f["params"].update(parts=[part.id], **params)
    else:
        f["params"].update(edges=[F.make_ref(part.id, _vertical_edge_at(part.shape, 5, 5), "edge", part.shape)],
                           **params)
    doc.add_feature(f)
    assert "ValueError" not in doc.errors.get(f["id"], ""), doc.errors


def test_circular_pattern_count_zero_is_a_clear_error():
    doc = _box(5, 5, 5)
    f = doc.make_feature("circular_pattern")
    f["params"].update(parts=list(doc.state.parts), count=0)
    doc.add_feature(f)
    assert "ZeroDivisionError" not in doc.errors.get(f["id"], "")


def test_dimension_edits_keep_the_fillet_on_its_edge():
    """Guard for what works today: upstream dimension edits re-find the right corner."""
    doc = Document()
    s = _rect(0, 0, 60, 40, fix_origin=True)
    line = next(k for k, e in s.entities.items() if e["type"] == "line")      # the bottom edge
    dim = s.add_constraint("distance", line, value=60)
    assert s.solve()
    sk = _sketch(doc, s)
    _extrude(doc, sk, depth=15.0)
    part = next(iter(doc.state.parts.values()))
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, _vertical_edge_at(part.shape, 60, 40), "edge", part.shape)],
                        radius=4.0)
    doc.add_feature(fi)
    for w in (120.0, 10.0):
        s = Sketch.from_dict(doc.feature(sk["id"])["params"]["sketch"])
        next(c for c in s.constraints if c["id"] == dim["id"])["value"] = w
        assert s.solve()
        cur = doc.feature(sk["id"])
        doc.replace_feature(dict(cur, params=dict(cur["params"], sketch=s.to_dict())))
        assert not doc.errors, doc.errors
        (part,) = doc.state.parts.values()
        assert sorted({tuple(c[:2]) for c in _round_centres(part.shape, 4.0)}) == [(w - 4.0, 36.0)]


def test_variable_that_breaks_the_sketch_is_an_error():
    """QA-19: a variable value the sketch can't satisfy is reported, not built from half-solved geometry."""
    from freeshape import variables as V
    doc = Document()
    var = V.add(doc, "w", "60")
    s = _rect(0, 0, 60, 40, fix_origin=True)
    bottom = next(k for k, e in s.entities.items() if e["type"] == "line")
    a, b = s.entities[bottom]["p1"], s.entities[bottom]["p2"]
    dim = s.add_constraint("distance", bottom, value=60)
    dim["expr"] = "#w"
    s.add_constraint("hdistance", a, b, value=60)          # a fixed dimension the variable can't argue with
    assert s.solve()
    sk = _sketch(doc, s)
    ex = _extrude(doc, sk, depth=10.0)
    assert not doc.errors
    V.set_expr(doc, var["id"], "80")
    assert "can't be solved" in doc.errors.get(sk["id"], ""), doc.errors
    assert ex["id"] in doc.errors                           # nothing downstream is built from the broken sketch
    V.set_expr(doc, var["id"], "60")                        # and it recovers once the value fits again
    assert not doc.errors


# ------------------------------------------------------------------------------------------ QA-04: old files
def _two_bodies_doc(legacy):
    """The QA-04 model (a block, then two squares, a fillet on the left square), with part ids made the old way
    (numbered across the whole model, as files before format 2) when legacy is set."""
    doc = Document()
    if legacy:
        doc._legacy_map = {}
    _extrude(doc, _sketch(doc, _rect(0, 0, 10, 10)), depth=5.0, op="new")
    s = Sketch()
    for x0 in (100.0, 200.0):
        pts = [s.add_point(x0, 0), s.add_point(x0 + 20, 0), s.add_point(x0 + 20, 20), s.add_point(x0, 20)]
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s.add_line(a, b)
    s.solve()
    ex2 = _extrude(doc, _sketch(doc, s), depth=10.0, op="new")
    left = min((p for pid, p in doc.state.parts.items() if pid.startswith(ex2["id"])),
               key=lambda p: K.bbox(p.shape)[0][0])
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(left.id, _vertical_edge_at(left.shape, 100, 0), "edge", left.shape)],
                        radius=3.0)
    doc.add_feature(fi)
    doc.set_part_override(left.id, name="Left square")
    return doc, left.id


def _rounded_at(doc):
    return [round(float(K.bbox(p.shape)[0][0])) for p in doc.state.parts.values() if _round_centres(p.shape, 3.0)]


def test_new_part_ids_are_per_feature():
    doc, left = _two_bodies_doc(legacy=False)
    assert sorted(doc.state.parts) == ["F2:p1", "F4:p1", "F4:p2"] and left == "F4:p1"


def test_old_file_part_ids_are_converted_on_open(tmp_path):
    import json
    old, left_old = _two_bodies_doc(legacy=True)
    assert left_old == "F4:2"                               # the old numbering
    d = json.loads(old.to_json())
    d["version"] = 1
    d.pop("part_aliases", None)
    path = tmp_path / "old.fshape"
    path.write_text(json.dumps(d))
    doc = Document.load(str(path))
    assert not doc.errors and _rounded_at(doc) == [100]
    assert doc.part_aliases == {"F2:1": "F2:p1", "F4:2": "F4:p1", "F4:3": "F4:p2"}
    assert doc.state.parts["F4:p1"].name == "Left square"   # the name override followed the part
    assert doc.part_id("F4:2") == "F4:p1" and doc.part_id("F4:2.1") == "F4:p1.1"   # assemblies / drawings
    doc.set_suppressed(doc.features[1]["id"], True)
    assert not doc.errors and _rounded_at(doc) == [100]     # QA-04 is fixed for converted files too
    doc.save(str(path), version=False)                      # saved in the new format: not converted again
    again = Document.load(str(path))
    assert json.loads(path.read_text())["version"] >= 2 and again.part_aliases == doc.part_aliases
    assert _rounded_at(again) == [100]


def test_example_assembly_finds_its_parts_through_the_aliases():
    from freeshape.assembly import AssemblyDoc
    ex = os.path.join(os.path.dirname(__file__), "..", "examples")
    studio = Document.load(os.path.join(ex, "hinge_parts.fshape"))   # a format-1 file
    assert studio.part_aliases and all(":p" in pid for pid in studio.state.parts)
    asm = AssemblyDoc.load(os.path.join(ex, "hinge.fasm"),
                           resolver=lambda src: studio.state.parts[studio.part_id(src["part"])])
    assert len(asm.parts) == 3 and not asm.errors


def test_old_script_converts_its_part_ids(tmp_path):
    from freeshape import script as S
    old, _ = _two_bodies_doc(legacy=True)
    text = S.dump([old]).replace(f"script.require({S.SCRIPT_VERSION})", "script.require(1)")
    p = tmp_path / "old.py"
    p.write_text(text, encoding="utf-8")
    (doc,) = S.run_file(str(p)).documents()
    assert not doc.errors and _rounded_at(doc) == [100] and "F4:p1" in doc.state.parts


def test_old_version_in_the_history_restores_with_new_ids(tmp_path):
    import json
    from freeshape import history as H
    old, _ = _two_bodies_doc(legacy=True)
    H.create(old, name="before")
    d = json.loads(old.to_json())
    d["version"] = 1
    for v in d["versions"]:
        v["snapshot"].pop("part_ids", None)                 # as a version saved before format 2
    path = tmp_path / "old.fshape"
    path.write_text(json.dumps(d))
    doc = Document.load(str(path))
    doc.delete_features({doc.features[-1]["id"]})           # drop the fillet, then bring it back from the version
    assert _rounded_at(doc) == []
    st, errors = H.state_of(doc, H.expanded(doc, doc.versions[0]))
    assert not errors and sorted(st.parts) == ["F2:p1", "F4:p1", "F4:p2"]
    H.restore(doc, doc.versions[0]["id"])
    assert not doc.errors and _rounded_at(doc) == [100] and "F4:p1" in doc.part_overrides


# ------------------------------------------------------------------------------------------ QA-03: lost references
def _pocket_with_fillet():
    doc = _box()
    sk2 = _sketch(doc, _rect(25, 15, 10, 10))
    cut = _extrude(doc, sk2, end="through", op="remove", both=True)
    part = next(iter(doc.state.parts.values()))
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, _vertical_edge_at(part.shape, 25, 15), "edge", part.shape)],
                        radius=2.0)
    doc.add_feature(fi)
    assert not doc.errors
    return doc, sk2, cut, fi


def test_fillet_on_a_deleted_pocket_edge_errors():
    doc, _sk2, cut, fi = _pocket_with_fillet()
    doc.delete_features({cut["id"]})
    assert "Missing edge reference" in doc.errors.get(fi["id"], "")
    assert not _round_centres(next(iter(doc.state.parts.values())).shape, 2.0)


def test_fillet_moved_above_its_pocket_errors():
    doc, _sk2, cut, fi = _pocket_with_fillet()
    doc.move_feature(fi["id"], doc.feature_index(cut["id"]))
    assert "Missing edge reference" in doc.errors.get(fi["id"], "")


def test_fillet_follows_its_pocket_corner_when_the_pocket_is_resized():
    doc, sk2, _cut, fi = _pocket_with_fillet()
    s = Sketch.from_dict(sk2["params"]["sketch"])
    for k, (x, y) in list(s.points.items()):
        if abs(x - 25) < 1e-9:
            s.points[k] = [20.0, y]                          # pocket corner moves from (25, 15) to (20, 15)
    assert s.solve()
    cur = doc.feature(sk2["id"])
    doc.replace_feature(dict(cur, params=dict(cur["params"], sketch=s.to_dict())))
    assert not doc.errors, doc.errors
    centres = {tuple(c[:2]) for c in _round_centres(next(iter(doc.state.parts.values())).shape, 2.0)}
    assert centres == {(22.0, 17.0)}                         # the round's centre, 2 into the pocket


def test_sketch_on_a_suppressed_pocket_floor_errors_instead_of_using_the_top_face():
    doc = _box()
    cut = _extrude(doc, _sketch(doc, _rect(25, 15, 10, 10)), depth=5.0, op="remove", flip=True)
    part = next(iter(doc.state.parts.values()))
    floor = next(f for f in K.faces(part.shape)                 # the pocket's 10 x 10 floor, at z = 5
                 if abs(K.face_info(f)["c"][2] - 5) < 1e-6 and abs(K.face_info(f)["area"] - 100) < 1e-6)
    sk = doc.make_feature("sketch")
    sk["params"]["plane"] = {"kind": "face", "face": F.make_ref(part.id, floor, "face", part.shape)}
    doc.add_feature(sk)
    assert not doc.errors
    doc.set_suppressed(cut["id"], True)
    assert "Missing face reference" in doc.errors.get(sk["id"], ""), doc.errors


# ------------------------------------------------------------------------------------------ QA-13: moved sheet metal
def _sheet(**move):
    """A 100 x 60 x 2 sheet metal plate on Top, then a Transform with these settings."""
    doc = Document()
    sk = _sketch(doc, _rect(0, 0, 100, 60, fix_origin=True))
    sm = doc.make_feature("sm_base")
    sm["params"].update(regions=[F.region_ref(doc.state, sk["id"], doc.state.sketches[sk["id"]].regions[0])],
                        thickness=2.0, radius=2.0)
    doc.add_feature(sm)
    pid = next(iter(doc.state.parts))
    mv = doc.make_feature("move")
    mv["params"].update(parts=[pid], **move)
    doc.add_feature(mv)
    assert not doc.errors, doc.errors
    return doc, pid


def _flange_on_long_top_edge(doc, pid):
    """A 20 mm flange on the moved plate's long edge nearest -Y, on its top face."""
    part = doc.state.parts[pid]
    lo, hi = K.bbox(part.shape)
    top = hi[2]
    edge = min((e for e in K.edges(part.shape)
                if (ln := K.edge_line(e)) is not None and abs(abs(ln[1][2])) < 1e-6
                and abs(K.edge_info(e)["c"][2] - top) < 1e-6 and K.edge_info(e)["len"] > 90),
               key=lambda e: K.edge_info(e)["c"][1])
    fl = doc.make_feature("sm_flange")
    fl["params"].update(edges=[F.make_ref(pid, edge, "edge", part.shape)], length=20.0, angle=90.0)
    doc.add_feature(fl)
    return fl


def test_flange_on_a_moved_sheet_metal_part_is_built_where_the_part_is():
    doc, pid = _sheet(type="xyz", dz=30.0)
    fl = _flange_on_long_top_edge(doc, pid)
    assert not doc.errors, doc.errors
    shape = doc.state.parts[pid].shape
    assert len(K.solids(shape)) == 1                          # one piece, not a stray flange back at z = 0
    lo, hi = K.bbox(shape)
    assert lo[2] > 29.9 and hi[2] > 45


def test_flange_on_a_rotated_sheet_metal_part_and_its_flat_pattern():
    from freeshape import sheetmetal as SM
    doc, pid = _sheet(type="rotate", axis={"kind": "std", "name": "Z"}, rotate=90.0)
    _flange_on_long_top_edge(doc, pid)
    assert not doc.errors, doc.errors
    shape = doc.state.parts[pid].shape
    assert len(K.solids(shape)) == 1
    fp = SM.flat_pattern(shape, doc.state.sheet[pid])        # still unfolds
    assert fp.face is not None


def test_a_copy_of_a_sheet_metal_part_is_sheet_metal_too():
    doc, pid = _sheet(type="xyz", dx=150.0, copy=True)
    copy_id = next(q for q in doc.state.parts if q != pid)
    assert copy_id in doc.state.sheet
    _flange_on_long_top_edge(doc, copy_id)
    assert not doc.errors and len(K.solids(doc.state.parts[copy_id].shape)) == 1
    assert K.bbox(doc.state.parts[copy_id].shape)[0][0] > 149.9


def test_a_scaled_sheet_metal_part_is_no_longer_sheet_metal():
    doc, pid = _sheet(type="scale", scale=2.0)
    assert pid not in doc.state.sheet                         # a scaled sheet's thickness and bends aren't its own
    fl = _flange_on_long_top_edge(doc, pid)
    assert "sheet metal" in doc.errors.get(fl["id"], "").lower()
