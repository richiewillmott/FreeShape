"""Sketching on a part's face: the face's corners, edge midpoints and edges are snap targets (like Onshape), and the
points they give are tied to the part so they can be dimensioned from."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from test_nav import _window  # noqa: E402


def _sketch_on_top():
    from freeshape.ui.viewport import Pick
    app, w, vp = _window()
    pid = next(iter(w.doc.state.parts))
    shape = w.doc.state.parts[pid].shape
    fi = max((i for i, f in enumerate(K.faces(shape)) if K.planar_face_plane(f) is not None),
             key=lambda i: K.face_info(K.faces(shape)[i])["c"][2])
    vp.set_selection([Pick("face", (pid, fi))])
    w.start_sketch()
    sk = w.sketcher
    vp.set_standard_view("Top", animate=False)
    vp.fit(animate=False)
    app.processEvents()
    return app, w, vp, sk


def _scr(vp, sk, u, v):
    s = vp.cam.to_screen(sk.plane.to_world(u, v))
    return float(s[0]), float(s[1])


def _uv_of(sk, x, y, z=10):
    return np.array(sk.plane.to_local(np.array([x, y, z], float)))


def test_line_from_a_face_corner_along_an_edge():
    app, w, vp, sk = _sketch_on_top()
    sk.set_tool("line") if hasattr(sk, "set_tool") else setattr(sk, "tool", "line")
    corner = _uv_of(sk, 40, 30)
    x, y = _scr(vp, sk, *(corner + 0.3))           # a little off the corner: it still snaps
    uv, snap = sk.snap(vp, x, y)
    assert snap and snap[0] == "mpt" and np.allclose(uv, corner)
    sk.mouse_move(vp, x, y, None)
    assert sk.model_hover is not None
    sk._draw_click(vp, x, y)
    a = sk.chain_anchor
    assert np.allclose(sk.sk.P(a), corner)
    assert any(c["type"] == "fix" and c["refs"][0] == a for c in sk.sk.constraints)
    # the end goes on the face's front edge (y = 0), somewhere along it
    on = _uv_of(sk, 13, 0)
    x, y = _scr(vp, sk, *on)
    uv, snap = sk.snap(vp, x, y, sk.sk.P(a))
    assert snap and snap[0] in ("medge", "on")
    sk._draw_click(vp, x, y)
    sk.sk.solve()
    lines = [e for e in sk.sk.entities.values() if e["type"] == "line" and not e.get("construction")]
    assert len(lines) == 1
    b = lines[0]["p2"]
    pb = sk.sk.P(b)
    edge_dir = _uv_of(sk, 40, 0) - _uv_of(sk, 0, 0)
    off = pb - _uv_of(sk, 0, 0)
    assert abs(off[0] * edge_dir[1] - off[1] * edge_dir[0]) / np.linalg.norm(edge_dir) < 1e-6   # on the edge
    assert any(c["type"] == "point_on" and c["refs"][0] == b for c in sk.sk.constraints)
    # dragging the solver can't pull it off the edge: a construction copy of the edge holds it
    held = [c for c in sk.sk.constraints if c["type"] == "point_on" and c["refs"][0] == b][0]["refs"][1]
    assert sk.sk.entities[held]["construction"]


def test_edge_midpoint_and_dimension_from_a_corner():
    app, w, vp, sk = _sketch_on_top()
    sk.tool = "point"
    mid = _uv_of(sk, 20, 30)
    x, y = _scr(vp, sk, *mid)
    uv, snap = sk.snap(vp, x, y)
    assert snap and snap[0] == "mpt" and snap[2][2] == "mid" and np.allclose(uv, mid)
    # a free point, then dimension it from the face's corner
    sk.tool = "point"
    free = _uv_of(sk, 25, 12)
    sk._draw_click(vp, *_scr(vp, sk, *free))
    p = [k for k in sk.sk.points if np.allclose(sk.sk.P(k), free)][0]
    sk.tool = "dimension"
    sk._dimension_click(vp, *_scr(vp, sk, *_uv_of(sk, 0, 0)))
    assert sk.dim_first is not None and np.allclose(sk.sk.P(sk.dim_first), _uv_of(sk, 0, 0))
    first = sk.dim_first
    sk._dimension_click(vp, *_scr(vp, sk, *free))
    assert sk.dim_second == p
    sk._dimension_click(vp, *_scr(vp, sk, *_uv_of(sk, 10, 20)))
    assert any(c["type"] in ("distance", "hdistance", "vdistance") and set(c["refs"][:2]) == {first, p}
               for c in sk.sk.constraints)


def test_snap_ignores_edges_off_the_sketch_plane():
    app, w, vp, sk = _sketch_on_top()
    keys = {(pid, ei) for pid, ei, _u, _p in sk._model_geo(vp)}
    assert len(keys) == 4                         # only the top face's four edges, not the bottom or sides


def test_horizontal_line_ends_on_the_edge_it_reaches():
    app, w, vp, sk = _sketch_on_top()
    sk.set_tool("line")
    start = _uv_of(sk, 5, 9)
    sk._draw_click(vp, *_scr(vp, sk, *start))
    a = sk.chain_anchor
    # near the right edge (x = 40), slightly off level and slightly short of the edge
    end = _uv_of(sk, 39.7, 9.3)
    x, y = _scr(vp, sk, *end)
    uv, snap = sk.snap(vp, x, y, sk.sk.P(a))
    assert sk.infer in ("H", "V") and snap and snap[0] == "medge"
    edge_x = _uv_of(sk, 40, 9)
    assert np.allclose(uv, edge_x, atol=1e-6)                   # level with the start AND on the edge
    sk._draw_click(vp, x, y)
    sk.sk.solve()
    line = [e for e in sk.sk.entities.values() if e["type"] == "line" and not e.get("construction")][0]
    assert np.allclose(sk.sk.P(line["p2"]), edge_x, atol=1e-6)


def test_helper_lines_are_hidden_outside_the_sketch():
    from freeshape.sketch import Sketch
    s = Sketch()
    p = [s.add_point(x, y) for x, y in [(0, 0), (10, 0), (10, 6), (0, 6)]]
    for a, b in zip(p, p[1:] + p[:1]):
        s.add_line(a, b)
    diag = s.add_line(p[0], p[2], True)                         # as an older centre rectangle saved it
    s.add_constraint("midpoint", s.add_point(5, 3), diag)
    axis = s.add_line(s.add_point(-5, 0), s.add_point(-5, 10), True)   # a construction line you drew
    assert s.helper_ids() == {diag}
    assert axis not in s.helper_ids()
