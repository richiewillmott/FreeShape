"""Compound: several solids as one part that share the faces where they touch (SALOME's Glue faces), unlike a
composite part, whose solids keep their own faces."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _block(doc, x0, x1, y0=0.0, y1=10.0, depth=10.0):
    s = Sketch()
    p = [s.add_point(x0, y0), s.add_point(x1, y0), s.add_point(x1, y1), s.add_point(x0, y1)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    e = doc.make_feature("extrude")
    sr = doc.state.sketches[sk["id"]]
    e["params"].update(regions=[F.region_ref(sr, sk["id"], sr.regions[0])], depth=depth, op="new")
    doc.add_feature(e)
    return e


def _two_cubes():
    doc = Document()
    _block(doc, 0, 10)
    _block(doc, 10, 20)
    assert len(doc.state.parts) == 2, "the second cube must stay a separate part (op = new)"
    return doc


def _compound(doc, **kw):
    f = doc.make_feature("compound")
    f["params"].update(kw)
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return f


def test_glued_cubes_share_their_face():
    doc = _two_cubes()
    _compound(doc)
    (part,) = doc.state.parts.values()
    assert part.name.startswith("Compound")
    assert K.topology_counts(part.shape) == {"VERTEX": 12, "EDGE": 20, "WIRE": 11, "FACE": 11, "SHELL": 2,
                                             "SOLID": 2}
    assert K.volume(part.shape) == pytest.approx(2000)


def test_without_glue_the_faces_stay_separate():
    doc = _two_cubes()
    _compound(doc, glue=False)
    (part,) = doc.state.parts.values()
    assert K.topology_counts(part.shape) == {"VERTEX": 16, "EDGE": 24, "WIRE": 12, "FACE": 12, "SHELL": 2,
                                             "SOLID": 2}


def test_partly_touching_faces_are_split_so_the_patch_is_shared():
    doc = Document()
    _block(doc, 0, 10)
    _block(doc, 10, 20, y0=5, y1=15)          # touches the first cube on half of its side
    _compound(doc)
    (part,) = doc.state.parts.values()
    n = K.topology_counts(part.shape)
    assert n["SOLID"] == 2
    assert n["FACE"] == 7 + 7 - 1              # each touching side split in two; the shared half counted once


def test_compound_stays_one_part_through_later_features():
    doc = _two_cubes()
    _compound(doc)
    # new material with Add is not fused into the compound
    _block(doc, 20, 30)
    assert len(doc.state.parts) == 2
    # cutting through both solids keeps them one glued part
    s = Sketch()
    s.add_circle(s.add_point(10, 5), 2.0)
    s.solve()
    sk = doc.make_feature("sketch")
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    cut = doc.make_feature("extrude")
    sr = doc.state.sketches[sk["id"]]
    cut["params"].update(regions=[F.region_ref(sr, sk["id"], sr.regions[0])], depth=20, op="remove")
    doc.add_feature(cut)
    assert not doc.errors, doc.errors
    comp = next(p for p in doc.state.parts.values() if p.name.startswith("Compound"))
    n = K.topology_counts(comp.shape)
    assert n["SOLID"] == 2 and n["SHELL"] == 2
    # each solid gets a half-cylinder hole face, and the full-height hole cuts the shared face in two
    assert n["FACE"] == 11 + 2 + 1
    assert len(doc.state.parts) == 2


def test_compound_survives_save_and_reload():
    doc = _two_cubes()
    _compound(doc)
    path = os.path.join(tempfile.mkdtemp(), "c.fshape")
    doc.save(path)
    d2 = Document.load(path)
    (part,) = d2.state.parts.values()
    assert K.topology_counts(part.shape)["FACE"] == 11


def test_needs_two_solids():
    doc = Document()
    _block(doc, 0, 10)
    f = doc.make_feature("compound")
    doc.add_feature(f)
    assert "at least two" in doc.errors.get(f["id"], "")


# --------------------------------------------------------------------------- Explode
def _explode(doc, ids, **kw):
    f = doc.make_feature("explode_part")
    f["params"].update(parts=[["part", i] for i in ids], **kw)
    doc.add_feature(f)
    return f


def test_explode_a_compound_back_into_its_solids():
    doc = _two_cubes()
    _compound(doc)
    (cid,) = doc.state.parts
    _explode(doc, [cid])
    assert not doc.errors, doc.errors
    parts = list(doc.state.parts.values())
    assert len(parts) == 2 and all(len(K.solids(p.shape)) == 1 for p in parts)
    assert [K.volume(p.shape) for p in parts] == pytest.approx([1000, 1000])
    assert all(p.name.startswith("Compound") and "solid" in p.name for p in parts)
    # each is an ordinary closed solid again: 6 faces, 8 vertices
    assert all(K.topology_counts(p.shape)["FACE"] == 6 for p in parts)


def test_explode_into_faces_or_edges_and_keep():
    doc = Document()
    _block(doc, 0, 10)
    (pid,) = doc.state.parts
    _explode(doc, [pid], what="face", keep=True)
    assert not doc.errors, doc.errors
    assert len(doc.state.parts) == 1 + 6                          # the solid kept, plus 6 surface parts
    surfs = [p for p in doc.state.parts.values() if not K.solids(p.shape)]
    assert len(surfs) == 6 and sum(K.face_info(p.shape)["area"] for p in surfs) == pytest.approx(600)
    doc2 = Document()
    _block(doc2, 0, 10)
    (pid2,) = doc2.state.parts
    _explode(doc2, [pid2], what="edge")
    assert len(doc2.state.parts) == 12 and all(not K.faces(p.shape) for p in doc2.state.parts.values())


def test_explode_a_single_solid_into_solids_says_why():
    doc = Document()
    _block(doc, 0, 10)
    (pid,) = doc.state.parts
    f = _explode(doc, [pid])
    assert "single solids" in doc.errors.get(f["id"], "")
