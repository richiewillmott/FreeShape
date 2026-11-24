"""QA pass (2.5.0): script export, drawing DXF export, drawing dimensions.
Tests marked xfail(strict=True) reproduce open defects from QA_REPORT.md."""
import os
import sys

import ezdxf
import numpy as np
import pytest
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from freeshape import script as S
from freeshape.document import Document
from freeshape.drawing import DIRECTIONS, DrawingDoc, export_dxf, _export_dxf_r12


# ------------------------------------------------------------------------------------------ script export
def _runs_payload(doc, tmp_path):
    """Export doc as a script, run it, and say whether the payload hidden in a name ran."""
    mark = tmp_path / "pwned.txt"
    payload = f"open({str(mark)!r},'w').write('pwned')"
    for f in doc.features:
        if f["type"] == "variable":
            f["params"]["name"] = f["params"]["name"].replace("PAYLOAD", payload)
    doc.name = doc.name.replace("PAYLOAD", payload)
    path = tmp_path / "m.py"
    path.write_text(S.dump([doc]), encoding="utf-8")
    try:
        S.run_file(str(path))
    except Exception:
        pass
    return mark.exists()


@pytest.mark.parametrize("name", ['A"""\nPAYLOAD\n"""', "A\nPAYLOAD\n#"])
def test_document_name_at_top_level_cannot_inject(name, tmp_path):
    d = Document()
    d.name = name
    assert not _runs_payload(d, tmp_path)


def test_indented_document_name_cannot_inject(tmp_path):
    d = Document()
    d.name = "A\n    PAYLOAD\n    #"
    p = tmp_path / "evil.fshape"
    d.save(str(p), version=False)
    assert not _runs_payload(Document.load(str(p)), tmp_path)


def test_variable_name_cannot_inject(tmp_path):
    d = Document()
    v = d.make_feature("variable")
    v["params"].update(name="w\nPAYLOAD\n#", expr="5")
    d.add_feature(v)
    assert not _runs_payload(d, tmp_path)


# ------------------------------------------------------------------------------------------ drawings
def _drawing():
    shape = BRepPrimAPI_MakeBox(60.0, 40.0, 10.0).Shape()
    g = DrawingDoc()
    g.source = {"kind": "studio"}
    g.resolver = lambda s: shape
    g.refresh(notify=False)
    return g


def _len_dim(g, v, P, Q, orient):
    snaps = [s for s in g.snap_points(v) if s[0] == "vertex"]

    def find(p):
        return min(snaps, key=lambda s: np.linalg.norm(np.asarray(s[1]) - np.asarray(p, float)))

    def E(s):
        return {"kind": "vertex", "p": [float(c) for c in s[1]], "i": s[3]["i"], "n": s[3]["n"]}
    return g.add_annot({"type": "lin", "view": v["id"], "a": E(find(P)), "b": E(find(Q)), "orient": orient,
                        "pos": list(g.to_paper(v, (30, -15)))})


def _dxf_dimension_values(g, path):
    export_dxf(g, str(path), full_size=False)
    doc = ezdxf.readfile(str(path))
    return [round(float(d.get_measurement()) * d.override().get("dimlfac", 1.0), 4)
            for d in doc.modelspace() if d.dxftype() == "DIMENSION"]


def test_dxf_dimension_matches_screen_at_sheet_scale(tmp_path):
    g = _drawing()
    g.sheet["scale"] = 1.0
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    a = _len_dim(g, front, (0, 0, 0), (60, 0, 0), "h")
    assert abs(g.dim_geometry(a)[2] - 60) < 1e-6
    assert _dxf_dimension_values(g, tmp_path / "d.dxf") == [60.0]


def test_dxf_dimension_on_a_view_with_its_own_scale(tmp_path):
    g = _drawing()
    g.sheet["scale"] = 1.0
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    front["scale"] = 2.0
    _len_dim(g, front, (0, 0, 0), (60, 0, 0), "h")
    assert _dxf_dimension_values(g, tmp_path / "d.dxf") == [60.0]


@pytest.mark.xfail(strict=True, reason="QA-10: aligned dimensions on iso views read the foreshortened length")
def test_iso_aligned_dimension_reads_the_true_length():
    g = _drawing()
    iso = g.add_view(*DIRECTIONS["Isometric"], (150, 150), kind="iso", checkpoint=False)
    a = _len_dim(g, iso, (0, 0, 0), (60, 0, 0), "a")
    assert abs(g.dim_geometry(a)[2] - 60) < 1e-6


_EVIL = "ok\n  0\nLINE\n  8\n0\n 10\n0\n 20\n0\n 11\n9999\n 21\n9999\n  0\nTEXT"


def _evil_drawing():
    g = _drawing()
    g.title["title"] = _EVIL
    f = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    _len_dim(g, f, (0, 0, 0), (60, 0, 0), "h")["text"] = _EVIL
    return g


def _far_lines(path):
    return [e for e in ezdxf.readfile(str(path)).modelspace()
            if e.dxftype() == "LINE" and max(abs(e.dxf.end[0]), abs(e.dxf.end[1])) > 9000]


def test_r12_dxf_escapes_newlines_in_text(tmp_path):
    _export_dxf_r12(_evil_drawing(), str(tmp_path / "b.dxf"))
    assert not _far_lines(tmp_path / "b.dxf")


@pytest.mark.xfail(strict=True, reason="QA-11: a newline in drawing text makes the ezdxf export fail")
def test_dxf_export_copes_with_newlines_in_text(tmp_path):
    export_dxf(_evil_drawing(), str(tmp_path / "a.dxf"))
    assert not _far_lines(tmp_path / "a.dxf")


def _dxf_lines_extent(path, layer_prefix="VISIBLE"):
    doc = ezdxf.readfile(str(path))
    xs = [x for e in doc.modelspace() if e.dxftype() == "LINE" and e.dxf.layer.startswith(layer_prefix)
          for x in (e.dxf.start[0], e.dxf.end[0])]
    return (max(xs) - min(xs)) if xs else None


@pytest.mark.parametrize("sheet_scale,view_scale", [(1.0, 2.0), (0.5, 0.5), (2.0, 0.25)])
def test_full_size_dxf_draws_and_dimensions_every_view_at_true_size(tmp_path, sheet_scale, view_scale):
    """QA-09: full size meant "divide by the sheet's scale", so a view with its own scale came out the wrong size
    and its dimension read wrong."""
    g = _drawing()
    g.sheet["scale"] = sheet_scale
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    front["scale"] = view_scale
    _len_dim(g, front, (0, 0, 0), (60, 0, 0), "h")
    path = tmp_path / "full.dxf"
    export_dxf(g, str(path), full_size=True)
    doc = ezdxf.readfile(str(path))
    dims = [round(float(d.get_measurement()) * d.override().get("dimlfac", 1.0), 4)
            for d in doc.modelspace() if d.dxftype() == "DIMENSION"]
    assert dims == [60.0]
    assert abs(_dxf_lines_extent(path, "VISIBLE") - 60.0) < 1e-6     # the view's visible edges: 60 mm wide


def test_paper_size_dxf_keeps_views_where_they_are(tmp_path):
    """On paper size nothing moves: a 2:1 view of the 60 mm box is 120 wide in the DXF, its dimension reads 60."""
    g = _drawing()
    g.sheet["scale"] = 1.0
    front = g.add_view(*DIRECTIONS["Front"], (150, 150), checkpoint=False)
    front["scale"] = 2.0
    _len_dim(g, front, (0, 0, 0), (60, 0, 0), "h")
    path = tmp_path / "paper.dxf"
    assert _dxf_dimension_values(g, path) == [60.0]
