"""The view cube menu's display options, as in Onshape: dimetric / trimetric views, previous view, zoom to window,
named views, perspective, render modes, hidden / tangent edges, high quality and boundary edges."""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.ui.camera import Camera, STANDARD_VIEWS, basis  # noqa: E402


def test_edge_classes_tangent_and_boundary():
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    box = BRepPrimAPI_MakeBox(20, 10, 5).Shape()
    mk = BRepFilletAPI_MakeFillet(box)
    mk.Add(2.0, K.edges(box)[0])
    cls = K.edge_classes(mk.Shape())
    assert len(cls) == len(K.edges(mk.Shape()))
    assert cls.count("tangent") == 2 and "boundary" not in cls       # the fillet's two edges meet smoothly
    assert K.edge_classes(K.faces(box)[0]) == ["boundary"] * 4      # a lone face: all its edges are open


@pytest.mark.parametrize("persp", [False, True])
def test_screen_point_and_ray_agree(persp):
    """A world point drawn at (sx, sy) lies on the pick ray through (sx, sy), in both projections."""
    c = Camera()
    c.w, c.h, c.scale = 800, 600, 0.2
    c.R = basis(np.array(STANDARD_VIEWS["Trimetric"][0], float), np.array([0, 0, 1.0]))
    c.target = np.array([5.0, -3.0, 2.0])
    c.perspective = persp
    for p in (np.array([10.0, 4.0, 7.0]), np.array([-20.0, 15.0, -4.0])):
        sx, sy, z = c.to_screen(p)
        o, d = c.ray(sx, sy)
        t = float((p - o) @ d)
        assert np.linalg.norm(o + t * d - p) < 1e-6
        assert c.view_z_of_t(t, d) == pytest.approx(z, abs=1e-6)
    if persp:          # things nearer the eye look bigger
        near, far = c.target + c.R[2] * 20, c.target - c.R[2] * 20
        off = c.R[0] * 10
        w_near = c.to_screen(near + off)[0] - c.to_screen(near)[0]
        w_far = c.to_screen(far + off)[0] - c.to_screen(far)[0]
        assert w_near > w_far


def _window():
    from test_ui_fixes import _cross_doc, _window as win
    doc, _ = _cross_doc()
    app, w = win(doc)
    return app, w, doc


def test_cube_menu_has_the_onshape_options():
    from PySide6.QtWidgets import QMenu
    app, w, _doc = _window()
    m = QMenu()
    w._fill_cube_menu(m)
    texts = [a.text() for a in m.actions() if a.text()]
    for t in ("Isometric", "Dimetric", "Trimetric", "Graphics preferences…", "Named views…", "Previous view",
              "Zoom to fit", "Zoom to window", "Perspective view", "Orient normal to sketch on edit",
              "Shaded with edges", "Hidden edges removed", "Tangent edges visible", "View in high quality",
              "Highlight boundary edges", "Section view…"):
        assert t in texts, t
    render = next(a.menu() for a in m.actions() if a.text() == "Shaded with edges")
    assert [a.text() for a in render.actions()] == ["Shaded with edges", "Shaded without edges", "Unshaded",
                                                    "Translucent"]
    w.set_display("render_mode", "translucent", save=False)
    m2 = QMenu()
    w._fill_cube_menu(m2)
    texts2 = [a.text() for a in m2.actions()]
    assert "Translucent" in texts2 and "Hidden edges removed" not in texts2      # as Onshape: no hidden edges
    w.set_display("render_mode", "shaded_edges", save=False)


def test_display_options_set_the_viewport_and_regroup_edges():
    app, w, _doc = _window()
    vp = w.viewport
    for key, val in (("render_mode", "unshaded"), ("hidden_edges", "visible"), ("tangent_edges", "phantom"),
                     ("highlight_boundary", True)):
        w.set_display(key, val, save=False)
        assert getattr(vp, key) == val
    b = vp._batch
    assert b["nedge"] > 0 and {"Et", "Eb", "nEt", "nEb"} <= set(b)
    w.set_display("perspective", True, save=False)
    assert vp.cam.perspective
    w.set_display("orient_on_sketch", False, save=False)
    assert w.orient_on_sketch is False
    n0 = vp._batch["ntri"]
    w.set_display("high_quality", True, save=False)
    assert vp._batch["ntri"] >= n0
    for key, val in w.DISPLAY_DEFAULTS.items():
        w.set_display(key, val, save=False)


def test_previous_view_and_zoom_to_window():
    import time
    app, w, _doc = _window()
    vp = w.viewport

    def settle():
        for _ in range(200):
            app.processEvents()
            if vp._anim is None:
                return
            time.sleep(0.01)
    end = time.time() + 0.3                                    # let the window's start-up view settle
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    vp.set_standard_view("Front", animate=False)
    front = vp.cam.state()
    vp.set_standard_view("Top", animate=False)
    top = vp.cam.state()
    vp.zoom_to_rect(100, 100, 300, 250, animate=False)
    assert vp.cam.scale < top[2]                               # zoomed in on the box
    assert vp.previous_view()
    settle()
    assert np.allclose(vp.cam.R, top[1]) and vp.cam.scale == pytest.approx(top[2])
    assert vp.previous_view()
    settle()
    assert np.allclose(vp.cam.R, front[1])


def test_named_views_are_saved_in_the_file():
    doc = Document()
    doc.named_views["Detail"] = {"target": [1.0, 2.0, 3.0], "R": np.eye(3).tolist(), "scale": 0.1,
                                 "perspective": True}
    path = os.path.join(tempfile.mkdtemp(), "nv.fshape")
    doc.save(path)
    assert Document.load(path).named_views == doc.named_views
