"""QA pass (2.5.0): document model — save/load fidelity, undo/redo symmetry, non-finite expressions.
Tests marked xfail(strict=True) reproduce open defects from QA_REPORT.md; they start passing (and so fail the
run, as a reminder to drop the mark) once the defect is fixed."""
import json
import math
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from freeshape import expr, kernel as K, variables as V
from freeshape.document import Document
from test_core import _plate_doc

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")


def _state(doc):
    """Everything an undo step is meant to put back."""
    return json.loads(doc._snapshot())


def _volumes(doc):
    return sorted(round(K.volume(p.shape), 4) for p in doc.state.parts.values())


# ------------------------------------------------------------------------------------------ save / load
@pytest.mark.parametrize("name", ["bracket.fshape", "hinge_parts.fshape"])
def test_save_load_save_is_identical(name, tmp_path):
    doc = Document.load(os.path.join(EXAMPLES, name))
    p1, p2 = tmp_path / "a.fshape", tmp_path / "b.fshape"
    doc.save(str(p1), version=False)
    again = Document.load(str(p1))
    again.save(str(p2), version=False)
    assert json.loads(p1.read_text()) == json.loads(p2.read_text())
    assert _volumes(doc) == _volumes(again)
    assert doc.errors == again.errors


def test_failed_save_keeps_the_old_file(tmp_path):
    doc, _, _ = _plate_doc()
    path = tmp_path / "keep.fshape"
    doc.save(str(path), version=False)
    before = path.read_text()
    doc.part_overrides["broken"] = {"x": {1, 2}}          # anything json can't write
    with pytest.raises(TypeError):
        doc.save(str(path), version=False)
    assert path.read_text() == before


# ------------------------------------------------------------------------------------------ undo / redo
def _cmd_add(doc):
    doc.add_feature(doc.make_feature("chamfer"))


def _cmd_suppress(doc):
    doc.set_suppressed(doc.features[1]["id"], True)


def _cmd_delete(doc):
    doc.delete_features({doc.features[1]["id"]})


def _cmd_rename(doc):
    doc.rename_feature(doc.features[1]["id"], "Renamed")


def _cmd_move(doc):
    v = V.add(doc, "w", "5")
    doc.move_feature(v["id"], 0)


def _cmd_replace(doc):
    f = doc.features[1]
    doc.replace_feature(dict(f, params=dict(f["params"], depth=30.0)))


def _cmd_color(doc):
    doc.set_part_colors({next(iter(doc.state.parts)): (1, 0, 0)})


def _cmd_folder(doc):
    doc.make_folder([f["id"] for f in doc.features])


def _cmd_configs(doc):
    doc.set_configs([{"name": "A", "vars": {}, "suppress": []}, {"name": "B", "vars": {}, "suppress": []}], 1)


def _cmd_var_rename(doc):
    V.add(doc, "w", "15", index=0)
    f = doc.features[2]
    doc.replace_feature(dict(f, params=dict(f["params"], depth="#w")))
    V.rename(doc, "w", "wall")


def _cmd_paste(doc):
    doc.insert_features([doc.make_feature("chamfer"), doc.make_feature("chamfer")], 1)


@pytest.mark.parametrize("cmd", [_cmd_add, _cmd_suppress, _cmd_delete, _cmd_rename, _cmd_move, _cmd_replace,
                                 _cmd_color, _cmd_folder, _cmd_configs, _cmd_var_rename, _cmd_paste])
def test_undo_redo_symmetry(cmd):
    doc, _, _ = _plate_doc()
    before, vol_before = _state(doc), _volumes(doc)
    n0 = len(doc._undo)
    cmd(doc)
    after, vol_after = _state(doc), _volumes(doc)
    for _ in range(len(doc._undo) - n0):
        doc.undo()
    assert _state(doc) == before
    assert _volumes(doc) == vol_before
    while doc.can_redo():
        doc.redo()
    assert _state(doc) == after
    assert _volumes(doc) == vol_after


# ------------------------------------------------------------------------------------------ expressions
@pytest.mark.parametrize("text", ["1e999", "1e999 - 1e999", "1e308 * 10"])
def test_expressions_never_give_non_finite_numbers(text):
    assert expr.parse_number(text) is None


@pytest.mark.parametrize("depth", ["1e999", "1e999 - 1e999"])
def test_non_finite_depth_is_an_error(depth):
    doc, _, _ = _plate_doc()
    ex = doc.features[1]
    doc.replace_feature(dict(ex, params=dict(ex["params"], depth=depth)))
    # (K.volume on the resulting part overflows the C stack and kills the process, so only look at the error)
    assert ex["id"] in doc.errors


def test_expression_edge_cases_fail_cleanly():
    for t in ["10/0", "5 % 0", "2**5000", "9**9**9", "(" * 500 + "1" + ")" * 500, "__import__('os')",
              "().__class__", "sqrt(-1)", "min()", "1 +", "#nope * 2"]:
        assert expr.parse_number(t) is None, t


# ------------------------------------------------------------------------------------------ copy / paste
@pytest.mark.xfail(strict=True, reason="QA-05: pasting a variable gives two variables with the same name")
def test_pasted_variable_gets_a_unique_name():
    from freeshape import feature_clipboard as C
    doc, _, _ = _plate_doc()
    v = V.add(doc, "w", "15", index=0)
    C.paste(doc, C.copy_features(doc, [v["id"]]))
    names = [x["name"] for x in V.list_variables(doc)]
    assert len(names) == len(set(names)), names
