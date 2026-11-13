"""STL export writes the binary format."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _box():
    """A 10 mm cube from one sketch and one extrude."""
    doc = Document()
    s = Sketch()
    p = [s.add_point(0, 0), s.add_point(10, 0), s.add_point(10, 10), s.add_point(0, 10)]
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
    assert not doc.errors
    return next(iter(doc.state.parts.values())).shape


def test_stl_export_is_binary(tmp_path):
    path = str(tmp_path / "box.stl")
    K.export_stl([_box()], path)
    data = open(path, "rb").read()
    # binary STL: 80-byte header, uint32 triangle count, then 50 bytes per triangle
    n = struct.unpack("<I", data[80:84])[0]
    assert n >= 12
    assert len(data) == 84 + 50 * n
    assert not data.lstrip().startswith(b"solid")


def test_binary_stl_reads_back(tmp_path):
    from OCP.StlAPI import StlAPI_Reader
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shape

    path = str(tmp_path / "box.stl")
    K.export_stl([_box()], path)
    n = struct.unpack("<I", open(path, "rb").read()[80:84])[0]
    back = TopoDS_Shape()
    assert StlAPI_Reader().Read(back, path)
    ex, faces = TopExp_Explorer(back, TopAbs_FACE), 0
    while ex.More():
        faces += 1
        ex.Next()
    assert faces == n
