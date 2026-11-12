"""Shift+E / Shift+W while sketching, and where file dialogs open."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402
from freeshape.ui.filedirs import remember_dir, start_dir  # noqa: E402

TOP = {"kind": "std", "name": "Top"}


def rect_lines(s, x0, y0, w, h):
    """Four unconstrained lines forming a w x h rectangle with its corner at (x0, y0)."""
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    return [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]


def sketch_feature(doc, build):
    """Add a sketch feature on the Top plane, built by build(sketch); returns the feature id."""
    s = Sketch()
    build(s)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"].update(plane=TOP, sketch=s.to_dict())
    doc.add_feature(f)
    assert not doc.errors.get(f["id"])
    return f["id"]


class _Settings(dict):
    def value(self, k, default=None):
        return self.get(k, default)

    def setValue(self, k, v):
        self[k] = v


def test_regions_of_sketch_lists_every_region():
    from freeshape.ui.mainwindow import regions_of_sketch
    doc = Document()
    sid = sketch_feature(doc, lambda s: (rect_lines(s, 0, 0, 10, 10), rect_lines(s, 20, 0, 5, 5)))
    picks = regions_of_sketch(doc.state, sid)
    assert [p.key for p in picks] == [(sid, 0), (sid, 1)] and all(p.kind == "region" for p in picks)
    assert regions_of_sketch(doc.state, "nope") == []


def _window():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from freeshape.ui.mainwindow import MainWindow
    w = MainWindow()
    w.settings = _Settings()
    app.processEvents()
    return w




def test_finish_and_start_extrude_preselects_regions():
    w = _window()
    w.start_sketch()
    ctx = w.sk_ctx
    ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher.sk
    rect_lines(s, 0, 0, 10, 10)
    rect_lines(s, 20, 0, 5, 5)
    s.solve()
    fid = ctx["feature"]["id"]
    w.finish_sketch_and_start("extrude")
    assert w.mode == "dialog" and w.dialog.ftype == "extrude"
    regs = w.dialog.feature["params"]["regions"]
    assert len(regs) == 2 and {r["sketch"] for r in regs} == {fid}
    w.cancel_dialog()


def test_finish_and_start_revolve_and_empty_sketch():
    w = _window()
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    rect_lines(w.sketcher.sk, 0, 5, 10, 10)
    w.sketcher.sk.solve()
    w.finish_sketch_and_start("revolve")
    assert w.dialog.ftype == "revolve" and len(w.dialog.feature["params"]["regions"]) == 1
    assert not w.dialog.feature["params"].get("axis")
    w.cancel_dialog()
    # open lines only: no closed region, Extrude opens with nothing selected
    w.start_sketch()
    w.sk_ctx["feature"]["params"]["plane"] = TOP
    w._enter_sketcher(animate=False)
    s = w.sketcher.sk
    s.add_line(s.add_point(0, 0), s.add_point(5, 0))
    w.finish_sketch_and_start("extrude")
    assert w.dialog.ftype == "extrude" and w.dialog.feature["params"]["regions"] == []


def test_dialog_dir_remembered_then_document_then_desktop(tmp_path):
    s = _Settings()
    desk = tmp_path / "desk"
    desk.mkdir()
    docdir = tmp_path / "docs"
    docdir.mkdir()
    last = tmp_path / "last"
    last.mkdir()
    assert start_dir(s, "doc", None, desktop=str(desk)) == str(desk)
    assert start_dir(s, "doc", str(docdir / "a.fshape"), desktop=str(desk)) == str(docdir)
    remember_dir(s, "doc", str(last / "x.fshape"))
    assert start_dir(s, "doc", str(docdir / "a.fshape"), desktop=str(desk)) == str(last)
    assert start_dir(s, "export", str(docdir / "a.fshape"), desktop=str(desk)) == str(docdir)   # per kind
    last.rmdir()                                                   # a vanished folder is ignored
    assert start_dir(s, "doc", None, desktop=str(desk)) == str(desk)
    assert start_dir(s, "doc", None, desktop=str(tmp_path / "gone")) == os.path.expanduser("~")
