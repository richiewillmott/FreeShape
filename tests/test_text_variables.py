"""Text variables (#31 follow-up): a Variable of type Any can hold text, sketch text can come from variables, and a
text variable in a number box is a clear error."""
import copy
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

from freeshape import measure_value as MV  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.expr import eval_text, is_text_expr  # noqa: E402
from freeshape.sketch import Sketch  # noqa: E402


def test_eval_text():
    v = {"rev": "B", "n": 12.0, "w": 2.5}
    assert eval_text('"Rev " + #rev', v) == "Rev B"
    assert eval_text("#rev", v) == "B"
    assert eval_text('"PN-" + #n + "-" + (#w * 2)', v) == "PN-12-5"
    assert eval_text("'a+b' + \"c\"", v) == "a+bc"
    for bad, why in (("#nope", "isn't defined"), ('"open', "isn't closed"), ("Rev", "text goes in quotes"),
                     ('"a" +', "missing")):
        with pytest.raises(ValueError, match=why):
            eval_text(bad, v)
    assert is_text_expr('"x"') and is_text_expr("#rev", v) and not is_text_expr("#n * 2", v)


def _doc_with_text_var(value='"Rev B"'):
    doc = Document()
    f = doc.make_feature("measure_value")
    f["params"].update(name="rev", mtype="expression", expr=value)
    doc.add_feature(f)
    return doc, f


def test_text_variable_and_its_label():
    doc, f = _doc_with_text_var()
    assert not doc.errors and doc.state.vars["rev"] == "Rev B" and doc.state.var_kinds["rev"] == "text"
    assert MV.label(doc.feature(f["id"]), doc.state).endswith('#rev = "Rev B"')


def test_text_variable_in_a_number_box_is_an_error():
    doc, f = _doc_with_text_var()
    sk = Sketch()
    a, b, c, d = (sk.add_point(x, y) for x, y in ((0, 0), (20, 0), (20, 10), (0, 10)))
    for p, q in ((a, b), (b, c), (c, d), (d, a)):
        sk.add_line(p, q)
    s = doc.make_feature("sketch")
    s["params"]["sketch"] = sk.to_dict()
    doc.add_feature(s)
    e = doc.make_feature("extrude")
    e["params"].update(regions=[], depth="#rev")
    doc.add_feature(e)
    assert "#rev is text" in doc.errors[e["id"]]
    v = doc.make_feature("measure_value")
    v["params"].update(name="d", mtype="fixed_length", expr="#rev * 2")
    doc.add_feature(v)
    assert "#rev is text" in doc.errors[v["id"]]


def test_sketch_text_follows_a_text_variable():
    doc, f = _doc_with_text_var('"A"')
    sk = Sketch()
    p = sk.add_point(0, 0)
    tid = sk.add_text(p, "?", size=10, expr='"PN-" + #rev')
    s = doc.make_feature("sketch")
    s["params"]["sketch"] = sk.to_dict()
    doc.add_feature(s)
    assert not doc.errors
    res = doc.state.sketches[s["id"]]
    assert res.sketch.entities[tid]["text"] == "PN-A"
    n_a = len(res.regions)
    f2 = copy.deepcopy(doc.feature(f["id"]))
    f2["params"]["expr"] = '"WXYZ"'
    doc.replace_feature(f2)
    res = doc.state.sketches[s["id"]]
    assert not doc.errors and res.sketch.entities[tid]["text"] == "PN-WXYZ" and len(res.regions) > n_a
    f3 = copy.deepcopy(f2)
    f3["params"]["name"] = "other"
    doc.replace_feature(f3)                     # renaming the variable: the sketch text follows it
    assert not doc.errors
    assert doc.state.sketches[s["id"]].sketch.entities[tid]["text"] == "PN-WXYZ"
    assert Sketch.from_dict(doc.feature(s["id"])["params"]["sketch"]).entities[tid]["expr"] == '"PN-" + #other'


def test_number_variables_still_work_with_text_ones():
    doc, f = _doc_with_text_var()
    v = doc.make_feature("measure_value")
    v["params"].update(name="w", mtype="expression", expr="3 mm * 2")
    doc.add_feature(v)
    assert not doc.errors and doc.state.vars["w"] == pytest.approx(6) and doc.state.var_kinds["w"] == "length"

