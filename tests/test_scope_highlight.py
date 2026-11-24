"""A part picked as merge scope (Extrude → Add, Part 1) lights up only while the Merge scope box is the one you're
picking in — otherwise the highlight covers the whole part (a big STEP import, say) and hides the preview."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import features as F  # noqa: E402


def test_merge_scope_part_highlighted_only_while_its_box_is_active():
    from test_nav import _window
    app, w, vp = _window()
    doc = w.doc
    sk = doc.features[0]
    sr = doc.state.sketches[sk["id"]]
    part = next(iter(doc.state.parts))
    e = doc.make_feature("extrude")
    e["params"].update(regions=[F.region_ref(sr, sk["id"], sr.regions[0])], depth=5, op="add", scope=[part])
    doc.add_feature(e)
    w.edit_feature(e["id"])
    app.processEvents()
    d = w.dialog
    assert d.fields["scope_sel"].items                                   # Part 1 is in the merge scope
    assert d.active_field is not d.fields["scope_sel"]
    keys = {k for (k, _i), _g in d.overlays()}
    assert "scope_sel" not in keys and "profiles" in keys                 # the region still shows; the part doesn't
    d.activate(d.fields["scope_sel"])
    keys = {k for (k, _i), _g in d.overlays()}
    assert "scope_sel" in keys                                           # picking in the box: it lights up
    w.cancel_dialog()
