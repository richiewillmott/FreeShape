"""2.0 stage 4: configurations, derived parts, and sheet metal hems, jogs, tabs and converting plates."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402

from freeshape import kernel as K, features as F, sheetmetal as SM  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402

TOP = {"kind": "std", "name": "Top"}


def _sketch(doc, pts=None, circle=None):
    s = Sketch()
    if pts:
        p = [s.add_point(x, y) for x, y in pts]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
    if circle:
        s.add_circle(s.add_point(*circle[:2]), circle[2])
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    f["params"]["plane"] = TOP
    doc.add_feature(f)
    return f["id"], doc.state.sketches[f["id"]]


def _extrude(doc, sid, sr, **kw):
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, sid, r) for r in sr.regions], **kw)
    doc.add_feature(e)
    return e


def _vol(state):
    return sum(K.volume(p.shape) for p in state.parts.values())


def _spacer():
    doc = Document()
    v = doc.make_feature("variable")
    v["params"].update(name="len", expr="40")
    doc.add_feature(v)
    sid, sr = _sketch(doc, [(0, 0), (20, 0), (20, 10), (0, 10)])
    _extrude(doc, sid, sr, depth="#len", op="new")
    sid2, sr2 = _sketch(doc, circle=(10, 5, 3))
    hole = _extrude(doc, sid2, sr2, end="through", op="remove")
    return doc, hole


# ------------------------------------------------------------------ configurations
def test_configurations_set_variables_and_suppress(tmp_path):
    doc, hole = _spacer()
    H = math.pi * 9
    doc.set_configs([{"name": "Short", "vars": {"len": "10"}},
                     {"name": "Long", "vars": {"len": "40 * 2"}, "suppress": [hole["id"]]}], active=0)
    assert doc.active_config() == "Short" and abs(_vol(doc.state) - (200 - H) * 10) < 1e-3
    assert abs(_vol(doc.state_for("Long")) - 200 * 80) < 1e-3                  # another one, without switching
    assert doc.active_config() == "Short"
    doc.set_active_config("Long")
    assert abs(_vol(doc.state) - 200 * 80) < 1e-3
    doc.save(str(tmp_path / "c.fshape"))
    d2 = Document.load(str(tmp_path / "c.fshape"))
    assert d2.config_names() == ["Short", "Long"] and d2.active_config() == "Long"
    assert abs(_vol(d2.state) - 200 * 80) < 1e-3


def test_assembly_instances_keep_their_configuration():
    from freeshape import assembly as A
    doc, _hole = _spacer()
    doc.set_configs([{"name": "Short", "vars": {"len": "10"}}, {"name": "Long", "vars": {"len": "80"}}])
    a = A.AssemblyDoc()
    a.resolver = lambda src: doc.state_for(src.get("config")).parts[src["part"]]
    pid = next(iter(doc.state.parts))
    for cfg in ("Short", "Long", "Long"):
        a.insert({"kind": "studio", "uid": doc.uid, "part": pid, "config": cfg, "part_name": "Spacer"},
                 checkpoint=False)
    vols = sorted(round(K.volume(a.parts[i["id"]].shape)) for i in a.instances)
    assert vols[0] < vols[1] == vols[2]
    assert sorted((r["name"], r["qty"]) for r in a.bom()) == [("Part 1 (Long)", 2), ("Part 1 (Short)", 1)]


# ------------------------------------------------------------------ derived parts
def test_derived_parts_follow_the_source_and_keep_a_copy(tmp_path):
    src, _hole = _spacer()
    src.set_configs([{"name": "Short", "vars": {"len": "10"}}, {"name": "Long", "vars": {"len": "80"}}])
    old = F.LINKER

    def link(p):
        st = src.state_for(p.get("config"))
        return [(q.name, q.shape, q.color, None) for q in st.parts.values()]
    F.LINKER = link
    try:
        dst = Document()
        d = dst.make_feature("derived")
        d["params"].update(uid=src.uid, parts=["*"], config="Long")
        dst.add_feature(d)
        H = math.pi * 9
        assert not dst.errors and abs(_vol(dst.state) - (200 - H) * 80) < 1e-3
        src.set_configs([{"name": "Short", "vars": {"len": "10"}}, {"name": "Long", "vars": {"len": "60"}}])
        dst.rebuild()
        assert abs(_vol(dst.state) - (200 - H) * 60) < 1e-3                     # the link follows the source
        dst.save(str(tmp_path / "d.fshape"))
    finally:
        F.LINKER = old
    F.LINKER = None
    try:
        d2 = Document.load(str(tmp_path / "d.fshape"))                         # source not available
        assert not d2.errors and abs(_vol(d2.state) - (200 - math.pi * 9) * 60) < 1e-3
    finally:
        F.LINKER = old


# ------------------------------------------------------------------ sheet metal
T_, R_, KF = 2.0, 2.0, 0.44


def _base(w=100, h=60):
    face = SM._polygon_face([(0, 0, 0), (w, 0, 0), (w, h, 0), (0, h, 0)])
    return SM.make_base(face, K.std_plane("Top"), T_, R_, KF)


def _edge(m, pred, panel=0):
    return next(e for e in m.panel_edges(m.panels[panel]) if pred(e[0], e[1]))


def _ba(ang, r):
    return math.radians(ang) * (r + KF * T_)


def _front(a, b):
    return abs(a[1]) < 1e-9 and abs(b[1]) < 1e-9


def test_hem_folds_back_and_unfolds_exactly():
    shape, m = _base()
    shape, m = SM.add_hem(shape, m, m.panels[0], *_edge(m, _front), length=10)
    assert K.is_valid(shape)
    flo, fhi = K.bbox(SM.flat_pattern(shape, m).face)
    Rh = T_ / 2
    assert abs((fhi[1] - flo[1]) - (60 + _ba(180, Rh) + 10 - (Rh + T_))) < 1e-6
    last = m.panels[-1]
    assert abs(last["fold"][2, 2] + 1) < 1e-9                                  # lying back over the sheet


def test_jog_steps_parallel_by_the_offset():
    shape, m = _base()
    shape, m = SM.add_jog(shape, m, m.panels[0], *_edge(m, _front), offset=15, length=25)
    last = m.panels[-1]
    assert np.allclose(np.abs(last["fold"][:3, 2]), [0, 0, 1])
    assert abs(last["fold"][2, 3] - 15) < 1e-9
    assert len(SM.flat_pattern(shape, m).bend_lines) == 2
    try:
        SM.add_jog(*_base(), m.panels[0], *_edge(_base()[1], _front), offset=3, length=25)
        assert False, "too small a jog should be refused"
    except K.KernelError:
        pass


def test_tab_grows_the_panel_and_takes_a_flange():
    shape, m = _base()
    tab = SM._polygon_face([(30, 60, 0), (70, 60, 0), (70, 80, 0), (30, 80, 0)])
    shape, m = SM.add_tab(shape, m, tab)
    assert abs(K.volume(shape) - (6000 + 800) * T_) < 1e-6
    e = _edge(m, lambda a, b: abs(a[1] - 80) < 1e-9 and abs(b[1] - 80) < 1e-9)
    shape, m = SM.add_flange(shape, m, m.panels[0], *e, length=15, angle=90)
    flo, fhi = K.bbox(SM.flat_pattern(shape, m).face)
    assert abs((fhi[1] - flo[1]) - (80 + _ba(90, R_) + 15 - (R_ + T_))) < 1e-6
    try:
        SM.add_tab(shape, m, SM._polygon_face([(300, 300, 0), (310, 300, 0), (310, 310, 0)]))
        assert False, "a tab that doesn't touch the sheet should be refused"
    except K.KernelError:
        pass


def test_convert_a_plate_keeps_holes_and_refuses_non_plates():
    plate = K.cut(BRepPrimAPI_MakeBox(80, 50, 3).Shape(), K.cylinder((40, 25, -1), (0, 0, 1), 6, 6))
    top = max((f for f in K.faces(plate) if K.planar_face_plane(f) is not None),
              key=lambda f: K.face_info(f)["c"][2])
    shape, m = SM.convert_plate(plate, top, 2.0, KF)
    assert m.thickness == 3.0 and abs(K.volume(shape) - K.volume(plate)) < 1e-6
    shape, m = SM.add_flange(shape, m, m.panels[0], *m.panel_edges(m.panels[0])[0], length=20, angle=90)
    flat = SM.flat_pattern(shape, m).face
    assert any(abs(K.edge_info(e).get("r", 0) - 6) < 1e-6 for e in K.edges(flat))      # the hole comes along
    step = K.cut(BRepPrimAPI_MakeBox(80, 50, 10).Shape(), BRepPrimAPI_MakeBox(10, 10, 20).Shape())
    stop = max((f for f in K.faces(step) if K.planar_face_plane(f) is not None), key=lambda f: K.face_info(f)["c"][2])
    try:
        SM.convert_plate(K.fuse(step, BRepPrimAPI_MakeBox(5, 5, 30).Shape()), stop, 2.0, KF)
        assert False
    except K.KernelError:
        pass


def test_sheet_features_in_a_document():
    doc = Document()
    sid, sr = _sketch(doc, [(0, 0), (100, 0), (100, 60), (0, 60)])
    b = doc.make_feature("sm_base")
    b["params"].update(regions=[F.region_ref(sr, sid, sr.regions[0])], thickness=2.0, radius=2.0)
    doc.add_feature(b)
    pid = next(iter(doc.state.parts))

    def ref(pred):
        sh = doc.state.parts[pid].shape
        e = next(e for e in K.edges(sh) if "p0" in K.edge_info(e)
                 and pred(np.array(K.edge_info(e)["p0"]), np.array(K.edge_info(e)["p1"])))
        return F.make_ref(pid, e, "edge", sh)
    on_top = lambda y: (lambda a, b: abs(a[1] - y) < 1e-6 and abs(b[1] - y) < 1e-6  # noqa: E731
                        and abs(a[2] - 2) < 1e-6 and abs(b[2] - 2) < 1e-6)
    h = doc.make_feature("sm_hem")
    h["params"].update(edges=[ref(on_top(0))], length=8)
    doc.add_feature(h)
    j = doc.make_feature("sm_jog")
    j["params"].update(edges=[ref(on_top(60))], offset=12, length=20)
    doc.add_feature(j)
    sid2, sr2 = _sketch(doc, [(100, 20), (125, 20), (125, 40), (100, 40)])
    t = doc.make_feature("sm_tab")
    t["params"].update(regions=[F.region_ref(sr2, sid2, r) for r in sr2.regions])
    doc.add_feature(t)
    assert not doc.errors and K.is_valid(doc.state.parts[pid].shape)
    assert len(SM.flat_pattern(doc.state.parts[pid].shape, doc.state.sheet[pid]).bend_lines) == 3


def test_tab_inside_the_sheet_is_refused_with_a_reason():
    shape, m = _base()
    try:
        SM.add_tab(shape, m, SM._polygon_face([(30, 20, 0), (70, 20, 0), (70, 40, 0), (30, 40, 0)]))
        assert False, "a tab inside the sheet adds nothing and should say so"
    except K.KernelError as e:
        assert "inside the sheet" in str(e)
