"""STL export writes binary STL (much smaller than text STL for curved parts), readable back with the same facets."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

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


def test_stl_resolution_changes_the_triangle_count(tmp_path):
    from freeshape.document import Document
    import struct
    doc = Document()
    from freeshape import api as fs
    shape = fs.cylinder(50, 20)
    counts = {}
    for key in ("coarse", "medium", "fine"):
        p = str(tmp_path / f"{key}.stl")
        K.export_stl([shape], p, *K.STL_RESOLUTIONS[key])
        with open(p, "rb") as fh:
            fh.seek(80)
            counts[key] = struct.unpack("<I", fh.read(4))[0]
    assert counts["coarse"] < counts["medium"] < counts["fine"]
    p = str(tmp_path / "custom.stl")
    K.export_stl([shape], p, 0.5, 45)
    with open(p, "rb") as fh:
        fh.seek(80)
        assert struct.unpack("<I", fh.read(4))[0] < counts["coarse"]
    with pytest.raises(K.KernelError):
        K.export_stl([shape], p, 0, 10)
    del doc


def test_stl_dialog_remembers_the_choice(tmp_path):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QDialog
    from test_nav import _window
    app, w, vp = _window()
    assert w._stl_options("a.step") is None

    def choose(key, accept=True):
        def go():
            d = QApplication.activeModalWidget()
            c = d.findChild(QComboBox)
            c.setCurrentIndex(c.findData(key))
            d.accept() if accept else d.reject()
        QTimer.singleShot(50, go)
    choose("fine")
    assert w._stl_options("a.stl") == pytest.approx(K.STL_RESOLUTIONS["fine"])
    choose("fine", accept=False)
    assert w._stl_options("a.stl") is False
    QTimer.singleShot(50, lambda: QApplication.activeModalWidget().accept())
    assert w._stl_options("a.stl") == pytest.approx(K.STL_RESOLUTIONS["fine"])     # remembered
    w.settings.setValue("stl/resolution", "medium")
