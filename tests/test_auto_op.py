"""A new Extrude picks New when it touches no part and Add (merge scope: the parts it touches) when it does."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import features as F  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def _second_sketch(w):
    """A sketch with the first square again (on the box's bottom) and a second square far away."""
    doc = w.doc
    s = Sketch()
    for x0 in (0, 100):
        p = [s.add_point(x0, 0), s.add_point(x0 + 40, 0), s.add_point(x0 + 40, 30), s.add_point(x0, 30)]
        for a, b in zip(p, p[1:] + p[:1]):
            s.add_line(a, b)
    s.solve()
    f = doc.make_feature("sketch")
    f["params"]["sketch"] = s.to_dict()
    doc.add_feature(f)
    w._refresh_all() if hasattr(w, "_refresh_all") else None
    return f["id"], doc.state.sketches[f["id"]]


def _dialog_with(w, app, fid, region):
    w.start_feature("extrude")
    app.processEvents()
    d = w.dialog
    fld = d.fields["profiles"]
    fld.items = [("region", F.region_ref(d.base_state, fid, region))]
    fld.refresh(w.doc)
    fld.changed.emit()
    app.processEvents()
    return d


def test_far_region_is_new_touching_region_adds_to_that_part():
    from test_nav import _window
    app, w, vp = _window()
    fid, sr = _second_sketch(w)
    import numpy as np
    far = max(sr.regions, key=lambda r: np.mean(F.K.bbox(r)[0][0]))
    near = min(sr.regions, key=lambda r: np.mean(F.K.bbox(r)[0][0]))
    d = _dialog_with(w, app, fid, far)
    assert d.feature["params"]["op"] == "new" and not d.fields["scope_sel"].items
    w.cancel_dialog()
    d = _dialog_with(w, app, fid, near)
    p = d.feature["params"]
    box = [pid for pid in d.base_state.parts]
    assert p["op"] == "add" and [r for _k, r in d.fields["scope_sel"].items] == box and p["scope"] == box
    # once you pick an op yourself it stays
    d._set("op", "new")
    fld = d.fields["profiles"]
    fld.items = [("region", F.region_ref(d.base_state, fid, far))]
    fld.changed.emit()
    assert d.feature["params"]["op"] == "new"
    w.cancel_dialog()
