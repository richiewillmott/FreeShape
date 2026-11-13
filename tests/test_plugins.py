"""Python plugins: loading, plugin features (dialog, expressions, saved copies without the plugin), commands,
scripts and the geometry API."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from freeshape import api as fs, kernel as K, plugins as P  # noqa: E402
from freeshape.document import Document  # noqa: E402

WASHER = '''
"""Washers"""
def register(api):
    api.info(name="Washer", version="2.1")
    def build(ctx):
        fs = api.geom
        t = ctx["t"]
        return fs.subtract(fs.cylinder(ctx["od"] / 2, t), fs.cylinder(ctx["id"] / 2, t))
    api.add_feature("washer", "Washer", build, fields=[
        api.number("od", "Outside diameter", 20), api.number("id", "Hole", 10), api.number("t", "Thickness", 2)])
    api.add_command("Count parts", lambda ctx: ctx.message(len(ctx.state.parts)))
'''


@pytest.fixture()
def plugdir(tmp_path):
    (tmp_path / "washer.py").write_text(WASHER)
    (tmp_path / "broken.py").write_text("def register(api):\n    raise ValueError('nope')\n")
    (tmp_path / "_private.py").write_text("raise SystemExit")
    P.load_all([str(tmp_path)])
    yield tmp_path
    P._unload()


def _vol(doc):
    return sum(K.volume(p.shape) for p in doc.state.parts.values())


def test_loading_reports_errors_and_skips_underscored_files(plugdir):
    names = {i.name: i for i in P.PLUGINS}
    assert set(names) == {"washer", "broken"}
    assert names["washer"].title == "Washer" and names["washer"].version == "2.1"
    assert names["washer"].description == "Washers"
    assert "nope" in names["broken"].error and not names["broken"].features
    assert "plugin:washer" in P.FEATURES and [c.label for c in P.COMMANDS] == ["Count parts"]


def test_plugin_feature_rebuilds_with_variables_and_booleans(plugdir):
    import math
    doc = Document()
    v = doc.make_feature("variable")
    v["params"].update(name="t", expr="3")
    doc.add_feature(v)
    f = doc.make_feature("plugin:washer")
    assert f["name"] == "Washer 1" and f["params"]["op"] == "new"
    f["params"]["t"] = "#t"
    doc.add_feature(f)
    ring = math.pi * (100 - 25)
    assert not doc.errors and abs(_vol(doc) - ring * 3) < 1e-6
    doc.replace_feature(dict(doc.feature(v["id"]), params={"name": "t", "expr": "5"}))
    assert abs(_vol(doc) - ring * 5) < 1e-6
    g = doc.make_feature("plugin:washer")                    # a second one, cutting the first
    g["params"].update(od=30, id=15, op="remove")
    doc.add_feature(g)
    assert not doc.errors and _vol(doc) < ring * 5


def test_document_opens_without_the_plugin(plugdir, tmp_path):
    doc = Document()
    f = doc.make_feature("plugin:washer")
    doc.add_feature(f)
    v0 = _vol(doc)
    path = str(tmp_path / "w.fshape")
    doc.save(path)
    P._unload()
    d2 = Document.load(path)
    assert not d2.errors and abs(_vol(d2) - v0) < 1e-6           # the saved copy
    d2.replace_feature(dict(d2.feature(f["id"]), params=dict(f["params"], t=9)))
    assert "isn't installed" in d2.errors[f["id"]]
    d2.undo()
    d2.save(path)                                                 # copy kept on re-saving without the plugin
    assert abs(_vol(Document.load(path)) - v0) < 1e-6
    P.load_all([str(plugdir)])
    d3 = Document.load(path)
    d3.replace_feature(dict(d3.feature(f["id"]), params=dict(f["params"], t=9)))
    assert not d3.errors and abs(_vol(d3) - v0 * 4.5) < 1e-6


def test_example_gear_meshes_on_its_pitch_circle():
    P.load_all([P.examples_dir()])
    try:
        doc = Document()
        f = doc.make_feature("plugin:spur_gear")
        f["params"].update(module=2, teeth=20, thickness=5, bore=0)
        doc.add_feature(f)
        part = next(iter(doc.state.parts.values()))
        lo, hi = K.bbox(part.shape)
        assert not doc.errors and K.is_valid(part.shape)
        assert abs(hi[0] - 22) < 0.05 and abs(hi[2] - 5) < 1e-6      # tip radius m(z + 2) / 2
        import math
        v = K.volume(part.shape) / 5
        assert math.pi * 17.5 ** 2 < v < math.pi * 22 ** 2
    finally:
        P._unload()


def test_geometry_api():
    b = fs.box(10, 20, 30, centred=True)
    assert abs(fs.volume(b) - 6000) < 1e-6 and fs.bbox(b)[0] == pytest.approx((-5, -10, -15), abs=1e-5)
    c = fs.extrude(fs.circle(5), 10)
    assert abs(fs.volume(c) - 250 * 3.141592653589793) < 1e-6
    tri = fs.extrude(fs.polygon([(0, 0), (10, 0), (0, 10)], on="Front"), 2)
    assert abs(fs.volume(tri) - 100) < 1e-6
    u = fs.union(fs.box(10, 10, 10), fs.move(fs.box(10, 10, 10), dx=5))
    assert abs(fs.volume(u) - 1500) < 1e-6
    r = fs.rotate(fs.box(10, 1, 1), 90)
    assert abs(fs.bbox(r)[1][1] - 10) < 1e-5
    assert fs.centre_of_mass(fs.box(2, 2, 2)) == pytest.approx((1, 1, 1))
    with pytest.raises(fs.KernelError):
        fs.box(0, 1, 1)


def test_plugin_feature_dialog_and_command_in_the_window(plugdir):
    from PySide6.QtWidgets import QApplication, QMessageBox
    from test_nav import _window
    app, w, vp = _window()
    P.load_all([str(plugdir)])
    w.start_feature("plugin:washer")
    d = w.dialog
    assert d is not None and d.ftype == "plugin:washer"
    assert {"od", "id", "t", "op"} <= set(d.widgets) | {"op"}
    d.widgets["t"].setText("4")
    d.widgets["t"].textEdited.emit("4")
    d.feature["params"]["op"] = "new"
    w.commit_dialog()
    assert any(f["type"] == "plugin:washer" and f["params"]["t"] == 4 for f in w.doc.features)
    assert not w.doc.errors and len(w.doc.state.parts) == 2
    shown = []
    QMessageBox.information = staticmethod(lambda *a, **k: shown.append(a[2]))
    from freeshape.ui import plugin_ui
    plugin_ui.run_command(w, P.COMMANDS[0])
    assert shown == ["2"]


def test_script_adds_a_part(tmp_path):
    from freeshape.document import Document as D

    class Main:
        doc = D()
        viewport = None
    script = tmp_path / "plate.py"
    script.write_text("part = fs.subtract(fs.box(60, 40, 10), fs.cylinder(5, 10, at=(30, 20, 0)))\n"
                      "ctx.add_shape(part, 'Plate')\n")
    P.run_script(str(script), Main)
    import math
    assert not Main.doc.errors and Main.doc.features[-1]["name"] == "Plate"
    assert abs(_vol(Main.doc) - (24000 - math.pi * 250)) < 1e-6
