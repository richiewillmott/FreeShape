"""STL export writes binary STL (much smaller than text STL for curved parts), readable back with the same facets."""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import kernel as K  # noqa: E402


def test_stl_is_binary_and_reads_back(tmp_path):
    shape = K.cylinder((0, 0, 0), (0, 0, 1), 10, 20)
    path = str(tmp_path / "c.stl")
    K.export_stl([shape], path)
    raw = open(path, "rb").read()
    (n,) = struct.unpack("<I", raw[80:84])
    assert n > 0 and len(raw) == 84 + 50 * n               # binary layout: 80-byte header, count, 50 bytes a facet
    assert not raw[:5].lower().startswith(b"solid") or b"facet normal" not in raw[:400]
    from OCP.StlAPI import StlAPI_Reader
    from OCP.TopoDS import TopoDS_Shape
    back = TopoDS_Shape()
    assert StlAPI_Reader().Read(back, path)
    assert len(K.faces(back)) == n
