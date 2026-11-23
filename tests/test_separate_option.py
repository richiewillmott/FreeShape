"""'Separate' on Extrude / Revolve / Sweep: areas that touch in the sketch (a square cut into four by a cross) give
one part each instead of one fused part. Off by default; only for New solids."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _cross_square(doc, x0=0.0, plane=None):
    """A 25 × 25 square cut into four by a cross."""
    s = Sketch()
    P = {(x, y): s.add_point(x0 + x, y) for x in (0, 12.5, 25) for y in (0, 12.5, 25)}
    for a, b in (((0, 0), (25, 0)), ((25, 0), (25, 25)), ((25, 25), (0, 25)), ((0, 25), (0, 0)),
                 ((12.5, 0), (12.5, 25)), ((0, 12.5), (25, 12.5))):
        s.add_line(P[a], P[b])
    s.solve()
    sk = doc.make_feature("sketch")
    if plane:
        sk["params"]["plane"] = plane
    sk["params"]["sketch"] = s.to_dict()
    doc.add_feature(sk)
    sr = doc.state.sketches[sk["id"]]
    assert len(sr.regions) == 4
    return sk["id"], [F.region_ref(sr, sk["id"], r) for r in sr.regions]


def _parts(doc, f):
    doc.add_feature(f)
    assert not doc.errors, doc.errors
    return sorted(round(K.volume(p.shape), 4) for p in doc.state.parts.values())


def test_extrude_separate_gives_four_parts_and_is_off_by_default():
    doc = Document()
    _sid, regions = _cross_square(doc)
    ex = doc.make_feature("extrude")
    assert not ex["params"].get("separate")
    ex["params"].update(regions=regions, depth=25.0, op="new")
    assert _parts(doc, ex) == [15625.0]                               # as before: one solid
    doc.delete_features([ex["id"]])
    ex2 = doc.make_feature("extrude")
    ex2["params"].update(regions=regions, depth=25.0, op="new", separate=True)
    assert _parts(doc, ex2) == [3906.25] * 4                          # 12.5 × 12.5 × 25 each
    names = sorted(p.name for p in doc.state.parts.values())
    assert len(set(names)) == 4


def test_separate_only_for_new():
    doc = Document()
    _sid, regions = _cross_square(doc)
    base = doc.make_feature("extrude")
    base["params"].update(regions=regions[:1], depth=5.0, op="new")
    doc.add_feature(base)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=regions, depth=25.0, op="add", separate=True)
    vols = _parts(doc, ex)
    assert len(vols) == 1                                             # Add still merges everything it touches


def test_revolve_and_sweep_separate():
    doc = Document()
    _sid, regions = _cross_square(doc, x0=10.0, plane={"kind": "std", "name": "Front"})
    rv = doc.make_feature("revolve")
    rv["params"].update(regions=regions, axis={"kind": "std", "name": "Z"}, end="one", angle=90.0, op="new",
                        separate=True)
    assert len(_parts(doc, rv)) == 4
    doc2 = Document()
    _sid2, regions2 = _cross_square(doc2)
    s = Sketch()
    s.add_line(s.add_point(0, 0), s.add_point(0, 40))
    s.solve()
    path = doc2.make_feature("sketch")
    path["params"]["plane"] = {"kind": "std", "name": "Front"}
    path["params"]["sketch"] = s.to_dict()
    doc2.add_feature(path)
    sr = doc2.state.sketches[path["id"]]
    eid = next(iter(sr.sketch.entities))
    sw = doc2.make_feature("sweep")
    sw["params"].update(regions=regions2, path=[{"kind": "sketch_chain", "sketch": path["id"], "entity": eid}],
                        op="new", separate=True)
    vols = _parts(doc2, sw)
    assert vols == [12.5 * 12.5 * 40] * 4                             # four bars along the 40 mm path


def test_ticking_separate_in_the_dialog_switches_to_new():
    from test_nav import _window
    from test_auto_op import _second_sketch, _dialog_with
    app, w, vp = _window()
    fid, sr = _second_sketch(w)
    import numpy as np
    near = min(sr.regions, key=lambda r: np.mean(F.K.bbox(r)[0][0]))
    d = _dialog_with(w, app, fid, near)
    assert d.feature["params"]["op"] == "add"                         # it touches the box: Add, automatically
    cb = d.widgets["separate"]
    assert not cb.isChecked() and cb.isVisibleTo(d)
    cb.setChecked(True)
    assert d.feature["params"]["op"] == "new" and d._op_chosen
    d._set("creation", "surface")
    d._sync_visibility()
    assert not cb.isVisibleTo(d)                                      # surfaces are separate sheets anyway
    w.cancel_dialog()


def test_colour_each_part_differently():
    doc = Document()
    _sid, regions = _cross_square(doc)
    ex = doc.make_feature("extrude")
    ex["params"].update(regions=regions, depth=25.0, op="new", separate=True)
    doc.add_feature(ex)
    assert {p.color for p in doc.state.parts.values()} == {F.DEFAULT_COLOR}      # off: the usual colour
    ex["params"]["separate_colors"] = True
    doc.replace_feature(ex)
    cols = [p.color for p in doc.state.parts.values()]
    assert len(cols) == 4 and len(set(cols)) == 4 and F.DEFAULT_COLOR not in cols
    assert cols == F.auto_colors(4)                                    # the Auto colour palette, in order


def test_colour_option_shows_only_with_separate():
    from test_nav import _window
    from test_auto_op import _second_sketch, _dialog_with
    app, w, vp = _window()
    fid, sr = _second_sketch(w)
    d = _dialog_with(w, app, fid, sr.regions[0])
    col = d.widgets["separate_colors"]
    assert not col.isVisibleTo(d)
    d.widgets["separate"].setChecked(True)
    assert col.isVisibleTo(d)
    w.cancel_dialog()
