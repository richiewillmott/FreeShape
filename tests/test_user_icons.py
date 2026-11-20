"""Your own icons (issue #30): an .svg / .png named like a tool in the icons folder replaces the built-in icon."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32">'
       '<rect width="32" height="32" fill="#ff0000"/></svg>')


def _colour(ic):
    img = ic.pixmap(32, 32).toImage()
    c = img.pixelColor(16, 16)
    return c.red(), c.green(), c.blue()


def test_icons_folder_overrides_and_reloads(tmp_path, monkeypatch):
    from test_nav import _window
    monkeypatch.setenv("FREESHAPE_ICONS", str(tmp_path))
    from freeshape.ui import icons as I
    app, w, vp = _window()
    w.reload_icons()
    before = _colour(I.icon("extrude"))
    (tmp_path / "extrude.svg").write_text(SVG)
    (tmp_path / "plugin_gridfinity.svg").write_text(SVG)
    assert _colour(I.icon("extrude")) == before                  # cached until reloaded
    w.reload_icons()
    assert _colour(I.icon("extrude")) == (255, 0, 0)
    assert _colour(I.icon("plugin:gridfinity")) == (255, 0, 0)
    btn = next(b for b in w.findChildren(type(w.btn_undo)) if b.property("icon_name") == "extrude")
    assert _colour(btn.icon()) == (255, 0, 0)                    # the toolbar button changed too
    os.remove(tmp_path / "extrude.svg")
    w.reload_icons()
    assert _colour(I.icon("extrude")) == before                  # delete it: the built-in one is back


def test_save_builtin_icons(tmp_path, monkeypatch):
    from test_nav import _window
    monkeypatch.setenv("FREESHAPE_ICONS", str(tmp_path))
    from freeshape.ui import icons as I
    app, w, vp = _window()
    names = w.icon_names()
    assert {"extrude", "fillet", "sketch", "measure_value"} <= set(names)
    n = I.export_builtin(names, str(tmp_path / "built-in"))
    files = os.listdir(tmp_path / "built-in")
    assert n == len(names) == len(files) - 1 and "extrude.png" in files and "names.txt" in files
