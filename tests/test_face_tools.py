"""Delete face (heal / cap / leave open) and Move face, for cleaning up imported parts."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _block_doc(fillet=False, boss=False):
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(40, 0), s.add_point(40, 20), s.add_point(0, 20)]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sr = doc.state.sketches[f["id"]]
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, f["id"], sr.regions[0])], depth=10, op="new")
    doc.add_feature(e)
    if boss:                                              # a 5 mm high cylinder on top
        s2 = Sketch()
        s2.add_circle(s2.add_point(20, 10), 5)
        s2.solve()
        f2 = doc.make_feature("sketch")
        f2["params"]["sketch"] = s2.to_dict()
        f2["params"]["plane"] = {"kind": "std", "name": "Top"}
        doc.add_feature(f2)
        sr2 = doc.state.sketches[f2["id"]]
        # sketch plane is z = 0: make the boss go from z=0 up to 15, fused with the block
        e2 = doc.make_feature("extrude")
        e2["params"].update(regions=[F.region_ref(sr2, f2["id"], sr2.regions[0])], depth=15, op="add")
        doc.add_feature(e2)
    return doc


def _face_ref(doc, pred):
    part = next(iter(doc.state.parts.values()))
    for f in K.faces(part.shape):
        info = K.face_info(f)
        if pred(info):
            return F.make_ref(part.id, f, "face", part.shape)
    raise AssertionError("face not found")


def _run(doc, ftype, **params):
    f = doc.make_feature(ftype)
    f["params"].update(params)
    st, err = doc.preview(f, doc.insert_index())
    return st, err


def _volume(shape):
    from OCP.GProp import GProp_GProps
    from OCP.BRepGProp import BRepGProp
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def test_move_face_grows_and_shrinks_the_block():
    doc = _block_doc()
    top = _face_ref(doc, lambda i: i.get("n") and i["n"][2] > 0.99)
    st, err = _run(doc, "move_face", faces=[top], distance=5.0)
    assert err is None, err
    assert abs(_volume(next(iter(st.parts.values())).shape) - 40 * 20 * 15) < 1e-3
    st, err = _run(doc, "move_face", faces=[top], distance=3.0, flip=True)       # flipped: pulled into the part
    assert err is None, err
    assert abs(_volume(next(iter(st.parts.values())).shape) - 40 * 20 * 7) < 1e-3
    side = _face_ref(doc, lambda i: i.get("n") and i["n"][0] > 0.99)
    st, err = _run(doc, "move_face", faces=[side], distance=10.0)
    assert err is None and abs(_volume(next(iter(st.parts.values())).shape) - 50 * 20 * 10) < 1e-3


def test_delete_face_heal_removes_a_boss():
    doc = _block_doc(boss=True)
    part = next(iter(doc.state.parts.values()))
    assert abs(_volume(part.shape) - (40 * 20 * 10 + np.pi * 25 * 5)) < 1e-2
    cyl = _face_ref(doc, lambda i: i["type"] == int(K.GeomAbs_Cylinder))
    top = _face_ref(doc, lambda i: i.get("n") and i["n"][2] > 0.99 and i["area"] < 100)    # the boss's top face
    st, err = _run(doc, "delete_face", faces=[cyl, top], mode="heal")
    assert err is None, err
    assert abs(_volume(next(iter(st.parts.values())).shape) - 40 * 20 * 10) < 1e-2


def test_delete_face_open_and_cap():
    doc = _block_doc()
    top = _face_ref(doc, lambda i: i.get("n") and i["n"][2] > 0.99)
    nfaces = len(K.faces(next(iter(doc.state.parts.values())).shape))
    st, err = _run(doc, "delete_face", faces=[top], mode="open")
    assert err is None, err
    shape = next(iter(st.parts.values())).shape
    assert len(K.faces(shape)) == nfaces - 1 and not K.solids(shape)       # an open shell
    st, err = _run(doc, "delete_face", faces=[top], mode="cap")             # the opening is closed again
    assert err is None, err
    shape = next(iter(st.parts.values())).shape
    assert len(K.faces(shape)) == nfaces and len(K.solids(shape)) == 1
    assert abs(_volume(shape) - 40 * 20 * 10) < 1e-3


def test_delete_face_needs_a_selection():
    doc = _block_doc()
    st, err = _run(doc, "delete_face", faces=[], mode="heal")
    assert err and "Select faces" in err


def _bbox(shape):
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    b = Bnd_Box()
    BRepBndLib.Add_s(shape, b)
    return (b.CornerMin().X(), b.CornerMin().Y(), b.CornerMin().Z(),
            b.CornerMax().X(), b.CornerMax().Y(), b.CornerMax().Z())


def test_offset_surface_flat_and_curved():
    doc = _block_doc()
    top = _face_ref(doc, lambda i: i.get("n") and i["n"][2] > 0.99)
    st, err = _run(doc, "offset_surface", faces=[top], distance=4.0)
    assert err is None, err
    assert len(st.parts) == 2                                      # the block + a new surface body
    surf = [p for p in st.parts.values() if not K.solids(p.shape)][0]
    assert abs(_bbox(surf.shape)[2] - 14.0) < 1e-6 and abs(_bbox(surf.shape)[5] - 14.0) < 1e-6
    st, err = _run(doc, "offset_surface", faces=[top], distance=4.0, flip=True)
    surf = [p for p in st.parts.values() if not K.solids(p.shape)][0]
    assert abs(_bbox(surf.shape)[2] - 6.0) < 1e-6
    # a curved face (the boss' cylinder wall) grows by the offset
    doc = _block_doc(boss=True)
    cyl = _face_ref(doc, lambda i: i.get("type") == "cylinder" or i.get("r"))
    st, err = _run(doc, "offset_surface", faces=[cyl], distance=2.0)
    assert err is None, err
    surf = [p for p in st.parts.values() if not K.solids(p.shape)][0]
    b = _bbox(surf.shape)
    assert abs((b[3] - b[0]) - 14.0) < 0.05                       # diameter 10 -> 14


def test_offset_surface_needs_a_selection():
    st, err = _run(_block_doc(), "offset_surface", faces=[], distance=2.0)
    assert err
