"""Extrude with a second end position: a second arrow, the other way to the first, drags the Second depth."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import numpy as np  # noqa: E402


def _open(second, end2="blind", flip=False):
    from test_nav import _window
    app, w, vp = _window()
    e = w.doc.features[1]
    w.edit_feature(e["id"])
    app.processEvents()
    d = w.dialog
    d.feature["params"].update(second=second, end2=end2, depth2=7.0, flip=flip)
    return app, w, d


def test_second_end_has_its_own_arrow_the_other_way():
    app, w, d = _open(True)
    arrows = d.manipulators()
    assert len(arrows) == 2
    first, second = arrows
    assert np.allclose(np.asarray(second["dir"]), -np.asarray(first["dir"]))   # opposite ways
    assert second["value"] == 7.0 and second.get("drag", True)
    second["on_drag"](12.0)                                    # what a drag of the arrow calls
    assert d.feature["params"]["depth2"] == 12.0 and d.feature["params"]["depth"] == 10
    assert second["label"].startswith("12")
    first["on_drag"](-10.0)                                    # flipping the first end turns the second round too
    assert d.feature["params"]["flip"] and np.allclose(np.asarray(second["dir"]), np.asarray(first["dir"]))
    w.cancel_dialog()


def test_no_second_arrow_without_a_blind_second_end():
    app, w, d = _open(False)
    assert len(d.manipulators()) == 1
    w.cancel_dialog()
    app, w, d = _open(True, end2="through")
    assert len(d.manipulators()) == 1
    w.cancel_dialog()
    app, w, d = _open(True, flip=True)                         # flipped first end: the second still goes opposite
    first, second = d.manipulators()
    assert first["value"] < 0
    tip1 = np.asarray(first["dir"]) * first["value"]
    tip2 = np.asarray(second["dir"]) * second["value"]
    assert float(tip1 @ tip2) < 0
    w.cancel_dialog()
