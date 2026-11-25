"""Chamfer's Tangent propagation, as Onshape's. A vertical edge that runs up into a rounded top edge carries on
smoothly round the round's end and along the top: on, the chamfer follows that chain (or says it can't); off, just
the picked edge, at any size. Chamfers from before the option keep working as they did."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from freeshape import features as F, kernel as K  # noqa: E402
from freeshape.document import Document  # noqa: E402
from test_qa_geometry import _rect, _sketch, _extrude  # noqa: E402


def _box_with_rounded_top_edge():
    """60 x 100 x 15, the top front edge rounded at 2 mm; the chamfer goes on the vertical edge at x = 60, y = 0,
    which runs up into that round."""
    doc = Document()
    _extrude(doc, _sketch(doc, _rect(0, 0, 60, 100, fix_origin=True)), depth=15.0)
    part = next(iter(doc.state.parts.values()))
    top = next(e for e in K.edges(part.shape) if np.allclose(K.edge_info(e)["c"], [30, 0, 15]))
    fi = doc.make_feature("fillet")
    fi["params"].update(edges=[F.make_ref(part.id, top, "edge", part.shape)], radius=2.0)
    doc.add_feature(fi)
    assert not doc.errors
    return doc, part.id


def _chamfer(doc, pid, d, **extra):
    shape = doc.state.parts[pid].shape
    vert = next(e for e in K.edges(shape) if (ln := K.edge_line(e)) is not None and abs(ln[1][2]) > 0.9
                and np.allclose(K.edge_info(e)["c"][:2], [60, 0]))
    ch = doc.make_feature("chamfer")
    ch["params"].update(edges=[F.make_ref(pid, vert, "edge", shape)], distance=d, **extra)
    for k in [k for k, v in extra.items() if v is _REMOVE]:
        ch["params"].pop(k)
    before = K.volume(shape)
    doc.add_feature(ch)
    return ch, before


_REMOVE = object()


def _removed(doc, pid, before):
    return before - K.volume(doc.state.parts[pid].shape)


def test_new_chamfers_have_it_on():
    assert F.default_params("chamfer", F.ModelState())["tangent"] is True


def test_on_follows_the_whole_smooth_chain():
    doc, pid = _box_with_rounded_top_edge()
    ch, before = _chamfer(doc, pid, 1.0, tangent=True)
    assert not doc.errors
    assert _removed(doc, pid, before) > 40              # the 100 mm top edge too, not just the 13 mm vertical one


def test_on_and_too_big_for_the_chain_says_so():
    doc, pid = _box_with_rounded_top_edge()
    ch, _ = _chamfer(doc, pid, 5.0, tangent=True)
    err = doc.errors.get(ch["id"], "")
    assert "Tangent propagation" in err and "largest that works" in err


@pytest.mark.parametrize("d", [1.0, 5.0, 10.0])
def test_off_bevels_just_the_picked_edge_at_any_size(d):
    doc, pid = _box_with_rounded_top_edge()
    ch, before = _chamfer(doc, pid, d, tangent=False)
    assert not doc.errors, doc.errors
    shape = doc.state.parts[pid].shape
    assert K.is_valid(shape) and len(K.solids(shape)) == 1
    assert _removed(doc, pid, before) == pytest.approx(d * d / 2 * 15, rel=0.1)   # the vertical edge's triangle


def test_chamfers_from_before_the_option_work_as_they_did():
    doc, pid = _box_with_rounded_top_edge()
    ch, before = _chamfer(doc, pid, 5.0, tangent=_REMOVE)     # no setting at all, as in an older file
    assert "tangent" not in doc.feature(ch["id"])["params"]
    assert not doc.errors
    assert _removed(doc, pid, before) == pytest.approx(5 * 5 / 2 * 15, rel=0.1)


def test_an_edge_with_no_smooth_neighbours_is_the_same_either_way():
    results = []
    for tp in (True, False):
        doc = Document()
        _extrude(doc, _sketch(doc, _rect(0, 0, 60, 100, fix_origin=True)), depth=15.0)
        part = next(iter(doc.state.parts.values()))
        e = next(x for x in K.edges(part.shape) if np.allclose(K.edge_info(x)["c"], [30, 0, 15]))
        ch = doc.make_feature("chamfer")
        ch["params"].update(edges=[F.make_ref(part.id, e, "edge", part.shape)], distance=3.0, tangent=tp)
        doc.add_feature(ch)
        assert not doc.errors
        results.append(K.volume(doc.state.parts[part.id].shape))
    assert results[0] == pytest.approx(results[1], rel=1e-9)


def test_the_dialog_has_the_checkbox():
    from test_select_other_keys import _win
    app, w, vp = _win()
    w.start_feature("chamfer")
    app.processEvents()
    box = w.dialog.widgets.get("tangent")
    assert box is not None and box.isChecked()
