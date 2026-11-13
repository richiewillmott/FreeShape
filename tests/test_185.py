"""1.8.5: feature-list folders, BREP export / import, hover outlines, the selection count and Auto colour."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from freeshape import kernel as K  # noqa: E402
from freeshape import features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _doc(nvars=6, nparts=1):
    """A sketch, `nparts` extruded blocks, then `nvars` variables (cheap features to group into folders)."""
    doc = Document()
    s = Sketch()
    for k in range(nparts):
        x = k * 14
        p = [s.add_point(x, 0), s.add_point(x + 10, 0), s.add_point(x + 10, 10), s.add_point(x, 10)]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    sid = f["id"]
    for i in range(nparts):
        sr = doc.state.sketches[sid]
        e = doc.make_feature("extrude")
        e["params"].update(regions=[F.region_ref(sr, sid, sr.regions[i])], depth=10, op="new")
        doc.add_feature(e)
    ids = []
    for k in range(nvars):
        v = doc.make_feature("variable")
        v["params"].update(name=f"v{k}", expr=str(k))
        doc.add_feature(v)
        ids.append(v["id"])
    return doc, ids


# ------------------------------------------------------------------ folders
def test_make_folder_needs_a_run_and_does_not_rebuild():
    doc, v = _doc()
    state = doc.state
    fo = doc.make_folder(v[1:4], "Group")
    assert doc.state is state                         # grouping never rebuilds the model
    assert doc.folder_members(fo) == v[1:4]
    try:
        doc.make_folder([v[0], v[5]])
        assert False, "a non-adjacent selection should be refused"
    except ValueError:
        pass


def test_folder_undo_save_load_and_ungroup(tmp_path):
    doc, v = _doc()
    fo = doc.make_folder(v[:3], "Group")
    doc.set_folder_open(fo, False)
    path = str(tmp_path / "f.fshape")
    doc.save(path)
    d2 = Document.load(path)
    assert d2.folders[fo] == {"name": "Group", "open": False} and d2.folder_members(fo) == v[:3]
    doc.ungroup_folder(fo)
    assert not doc.folders and not any(f.get("folder") for f in doc.features)
    doc.undo()
    assert doc.folder_members(fo) == v[:3]


def test_remove_from_folder_keeps_it_one_run():
    doc, v = _doc()
    fo = doc.make_folder(v[:4], "Group")
    doc.remove_from_folder([v[1]])                    # from the middle: moved to just after the folder
    order = [f["id"] for f in doc.features]
    assert doc.folder_members(fo) == [v[0], v[2], v[3]]
    assert order.index(v[1]) == order.index(v[3]) + 1
    doc.remove_from_folder([v[0]])                    # from the top: just leaves, stays put
    assert doc.folder_members(fo) == [v[2], v[3]]


def test_folder_suppress_and_delete_prunes():
    doc, v = _doc()
    fo = doc.make_folder(v[:2], "Group")
    doc.set_folder_suppressed(fo, True)
    assert all(doc.feature(i)["suppressed"] for i in v[:2])
    doc.delete_features(v[:2])
    assert fo not in doc.folders


def test_feature_list_shows_folders_and_drag_rules():
    _app()
    from freeshape.ui.panels import FeatureList, ROLE_KIND, ROLE_ID
    doc, v = _doc()
    fo = doc.make_folder(v[1:4], "Group")
    fl = FeatureList()
    fl.populate(doc)
    rows = [(fl.item(i).data(ROLE_KIND), fl.item(i).data(ROLE_ID)) for i in range(fl.count())]
    assert ("folder", fo) in rows
    assert rows.index(("folder", fo)) == rows.index(("feature", v[1])) - 1

    def row(ident):
        return next(i for i in range(fl.count()) if fl.item(i).data(ROLE_ID) == ident)

    def move(ident, to):
        fl.insertItem(to, fl.takeItem(row(ident)))
        return fl.folder_layout()

    assert move(v[0], row(v[2]))[v[0]] == fo            # dropped between two members: joins
    fl.populate(doc)
    lay = move(v[3], row(v[0]))                          # last member dragged out above the folder: leaves
    assert lay[v[3]] is None and lay[v[1]] == fo
    fl.populate(doc)
    for i in range(fl.count()):                          # selecting the folder row selects its features
        fl.item(i).setSelected(fl.item(i).data(ROLE_KIND) == "folder")
    assert fl.selected_feature_ids() == v[1:4]
    doc.set_folder_open(fo, False)
    fl.populate(doc)
    assert fl.item(row(v[2])).isHidden()


# ------------------------------------------------------------------ BREP
def test_brep_round_trip_and_import_feature(tmp_path):
    import base64
    doc, _ = _doc(nvars=0, nparts=3)
    path = str(tmp_path / "parts.brep")
    doc.export(path)
    back = K.import_brep(path)
    assert len(back) == 3
    assert abs(sum(K.mass_props(s)["volume"] for s in back) - 3000) < 1e-6
    d2 = Document()
    f = d2.make_feature("import")
    f["params"] = {"data": base64.b64encode(open(path, "rb").read()).decode(), "filename": "parts.brep"}
    d2.add_feature(f)
    assert not d2.errors and len(d2.state.parts) == 3


# ------------------------------------------------------------------ hover outline, count, auto colour
def test_faces_know_their_edges():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    m = K.tessellate(BRepPrimAPI_MakeBox(10, 20, 30).Shape())
    assert [len(x) for x in m.face_edges] == [4] * 6


def test_hover_outline_and_count_badge():
    _app()
    from freeshape.ui.viewport import Viewport, Pick
    doc, _ = _doc(nvars=0)
    part = next(iter(doc.state.parts.values()))
    vp = Viewport()
    vp.parts = [(part.id, K.tessellate(part.shape), (0.6, 0.7, 0.8))]
    vp.hover = Pick("face", (part.id, 0))
    assert len(vp._hover_outline()) == 4                 # the hovered face: its four edges, not a fill
    vp.hover = None
    assert vp._hover_outline() is None
    vp._cursor_xy = (50, 50)
    vp.set_selection([Pick("face", (part.id, 0)), Pick("face", (part.id, 1))])
    assert vp.count_badge.text() == "2"
    vp.clear_selection()
    assert vp.count_badge.isHidden()


def test_auto_colors_distinct_and_one_undo_step():
    cols = F.auto_colors(40)
    assert len({tuple(round(c, 3) for c in x) for x in cols}) == 40
    for r, g, b in cols:                                  # nothing orange / yellow like the highlights
        import colorsys
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        assert not (0.04 < h < 0.18 and s > 0.35)
    doc, _ = _doc(nvars=0, nparts=4)
    ids = list(doc.state.parts)
    n_undo = len(doc._undo)
    doc.set_part_colors(dict(zip(ids, F.auto_colors(len(ids)))))
    assert len({doc.state.parts[i].color for i in ids}) == 4
    assert len(doc._undo) == n_undo + 1
