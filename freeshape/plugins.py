"""Python plugins.

A plugin is a .py file in the plugins folder (Document ▸ Plugins… ▸ Open plugins folder) with a function
`register(api)`. Through `api` it can add

  * **features** — parametric, with their own dialog; they rebuild with the model like built-in ones, and
  * **commands** — run once from the Plugins menu, with the open document and the selection.

    def register(api):
        api.info(name="Washer", version="1.0", description="Plain washers")

        def build(ctx):
            fs = api.geom
            outer = fs.cylinder(ctx["od"] / 2, ctx["t"])
            return fs.subtract(outer, fs.cylinder(ctx["id"] / 2, ctx["t"]))

        api.add_feature("washer", "Washer", build, fields=[
            api.number("od", "Outside diameter", 20),
            api.number("id", "Hole diameter", 10.5),
            api.number("t", "Thickness", 2),
        ])

See PLUGINS.md for the whole API. Plugin features save a copy of what they made in the document, so a file
still opens (unchanged) on a computer without the plugin.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import sys
import traceback
from dataclasses import dataclass, field

from . import api as geom
from . import kernel as K

API_VERSION = 1
PREFIX = "plugin:"

FEATURES: dict = {}        # feature type ("plugin:<id>") -> FeatureSpec
COMMANDS: list = []        # Command
PLUGINS: list = []         # PluginInfo, in load order
LAST_BODIES: dict = {}     # (document uid, feature id, raw stamp) -> (full stamp, [shape] or saved copy)
CURRENT_DOC = [""]         # uid of the document being rebuilt (set by Document.compute)
_listeners: list = []      # called after (re)loading, e.g. to rebuild menus


@dataclass
class FeatureSpec:
    ftype: str
    label: str
    build: object
    fields: list
    plugin: str
    boolean: bool = True
    help: str = ""

    def defaults(self, has_parts=False):
        d = {"_plugin": self.plugin}
        for f in self.fields:
            if "default" in f:
                d[f["key"]] = f["default"]
            elif f["t"] == "sel":
                d[f["key"]] = []
        if self.boolean:
            d["op"] = "add" if has_parts else "new"
        return d

    def numeric_keys(self):
        return {f["key"] for f in self.fields if f["t"] == "num"}


@dataclass
class Command:
    label: str
    run: object
    plugin: str
    shortcut: str | None = None


@dataclass
class PluginInfo:
    name: str
    path: str
    title: str = ""
    version: str = ""
    description: str = ""
    error: str = ""
    features: list = field(default_factory=list)
    commands: list = field(default_factory=list)


# ---------------------------------------------------------------------- folders
def user_dir() -> str:
    """Where your plugins live: $FREESHAPE_PLUGINS, or a 'plugins' folder in FreeShape's data folder."""
    env = os.environ.get("FREESHAPE_PLUGINS")
    if env:
        return env
    base = None
    try:
        from PySide6.QtCore import QStandardPaths
        g = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        base = os.path.join(g, "FreeShape") if g else None
    except Exception:
        pass
    return os.path.normpath(os.path.join(base or os.path.expanduser("~/.freeshape"), "plugins"))


def examples_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "plugin_examples"), os.path.join(os.path.dirname(here), "examples", "plugins")):
        if os.path.isdir(cand):
            return cand
    return os.path.join(here, "plugin_examples")


# ---------------------------------------------------------------------- the api handed to register()
class Api:
    version = API_VERSION
    geom = geom                    # freeshape.api: box, cylinder, extrude, union, subtract …

    def __init__(self, info: PluginInfo):
        self._info = info

    # -- about the plugin
    def info(self, name=None, version=None, description=None):
        if name:
            self._info.title = str(name)
        if version:
            self._info.version = str(version)
        if description:
            self._info.description = str(description)

    # -- dialog fields
    @staticmethod
    def number(key, label, default=10.0, unit="mm", minimum=None):
        """A number (typed values can be expressions like '#wall * 2'). unit: 'mm', 'deg' or ''."""
        return {"t": "num", "key": key, "label": label, "unit": unit, "min": minimum, "default": default}

    @staticmethod
    def integer(key, label, default=1, minimum=1):
        return {"t": "num", "key": key, "label": label, "unit": "", "min": minimum, "default": int(default),
                "int": True}

    @staticmethod
    def angle(key, label, default=0.0):
        return {"t": "num", "key": key, "label": label, "unit": "deg", "min": None, "default": default}

    @staticmethod
    def choice(key, label, options, default=None):
        """options: a list of values, or of (value, label) pairs."""
        opts = [o if isinstance(o, (list, tuple)) else (o, str(o)) for o in options]
        return {"t": "choice", "key": key, "label": label, "options": [tuple(o) for o in opts],
                "default": opts[0][0] if default is None else default}

    @staticmethod
    def check(key, label, default=False):
        return {"t": "check", "key": key, "label": label, "default": bool(default)}

    @staticmethod
    def text(key, label, default="", placeholder=""):
        return {"t": "text", "key": key, "label": label, "default": default, "placeholder": placeholder}

    @staticmethod
    def select(key, label, kinds=("face",), multi=True):
        """Things to click in the model: kinds from 'face', 'edge', 'vertex', 'part', 'plane', 'axis', 'region'."""
        kinds = {kinds} if isinstance(kinds, str) else set(kinds)
        bad = kinds - {"face", "edge", "vertex", "part", "plane", "axis", "region"}
        if bad:
            raise ValueError(f"Unknown selection kinds: {', '.join(sorted(bad))}")
        return {"t": "sel", "key": key, "label": label, "want": kinds, "multi": bool(multi)}

    @staticmethod
    def note(text):
        """A line of help text in the dialog."""
        return {"t": "info", "key": "_note_%x" % (hash(text) & 0xffffff), "text": (lambda p, s=text: s)}

    # -- registering
    def add_feature(self, id, label, build, fields=(), boolean=True, help=""):
        """A parametric feature. build(ctx) returns a solid (or a list of them); with boolean=True the dialog
        offers New / Add / Remove / Intersect, like Extrude. Return None if build changed parts itself
        (ctx.replace_part / ctx.add_part)."""
        ftype = PREFIX + str(id)
        if ftype in FEATURES and FEATURES[ftype].plugin != self._info.name:
            raise ValueError(f"Another plugin ({FEATURES[ftype].plugin}) already adds a feature called '{id}'")
        spec = FeatureSpec(ftype, str(label), build, list(fields), self._info.name, bool(boolean), help)
        FEATURES[ftype] = spec
        self._info.features.append(ftype)
        _register_feature_type(spec)
        return ftype

    def add_command(self, label, run, shortcut=None):
        """A command in the Plugins menu: run(ctx) with ctx.doc, ctx.selection, ctx.message() …"""
        c = Command(str(label), run, self._info.name, shortcut)
        COMMANDS.append(c)
        self._info.commands.append(c)
        return c


def _register_feature_type(spec: FeatureSpec):
    from . import features as F
    F.FEATURE_LABELS[spec.ftype] = spec.label
    F.RUNNERS[spec.ftype] = run_feature
    F.EXTRA_NUMERIC[spec.ftype] = spec.numeric_keys()


# ---------------------------------------------------------------------- loading
def _unload():
    from . import features as F
    for ftype in list(FEATURES):
        F.FEATURE_LABELS.pop(ftype, None)
        F.RUNNERS.pop(ftype, None)
        F.EXTRA_NUMERIC.pop(ftype, None)
    FEATURES.clear()
    COMMANDS.clear()
    for info in PLUGINS:
        sys.modules.pop("freeshape_plugin_" + info.name, None)
    PLUGINS.clear()


def load_file(path) -> PluginInfo:
    path = os.path.normpath(path)
    name = os.path.splitext(os.path.basename(path))[0]
    info = PluginInfo(name=name, path=path, title=name.replace("_", " ").title())
    PLUGINS.append(info)
    try:
        spec = importlib.util.spec_from_file_location("freeshape_plugin_" + name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        reg = getattr(mod, "register", None)
        if reg is None:
            raise RuntimeError("The plugin has no register(api) function")
        doc = (mod.__doc__ or "").strip()
        if doc and not info.description:
            info.description = doc.splitlines()[0]
        reg(Api(info))
    except Exception as e:
        info.error = f"{type(e).__name__}: {e}"
        info.trace = traceback.format_exc()
        # a plugin that fails half way doesn't leave half its features behind
        from . import features as F
        for ftype in info.features:
            FEATURES.pop(ftype, None)
            F.RUNNERS.pop(ftype, None)
            F.FEATURE_LABELS.pop(ftype, None)
        for c in info.commands:
            if c in COMMANDS:
                COMMANDS.remove(c)
        info.features, info.commands = [], []
    return info


def load_all(folders=None):
    """(Re)load every plugin in the plugins folder(s). Files starting with '_' are skipped."""
    _unload()
    folders = [user_dir()] if folders is None else list(folders)
    for d in folders:
        if not d or not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            if fn.endswith(".py") and not fn.startswith("_"):
                load_file(os.path.join(d, fn))
    for cb in list(_listeners):
        try:
            cb()
        except Exception:
            traceback.print_exc()
    return list(PLUGINS)


def is_plugin_type(ftype) -> bool:
    return str(ftype).startswith(PREFIX)


def plugin_of(f) -> str:
    return (f.get("params") or {}).get("_plugin") or f["type"][len(PREFIX):]


def default_params(ftype, has_parts=False):
    spec = FEATURES.get(ftype)
    if spec is None:
        raise KeyError(ftype)
    return spec.defaults(has_parts)


def dialog_schema(ftype):
    """The FeatureDialog schema for a plugin feature."""
    spec = FEATURES.get(ftype)
    if spec is None:
        return []
    out = []
    if spec.boolean:
        out.append({"t": "ops", "key": "op",
                    "options": [("new", "New"), ("add", "Add"), ("remove", "Remove"), ("intersect", "Intersect")]})
    for f in spec.fields:
        g = dict(f)
        g.setdefault("visible", None)
        out.append(g)
    if spec.help:
        out.append({"t": "info", "key": "_help", "text": lambda p, s=spec.help: s})
    return out


# ---------------------------------------------------------------------- running a plugin feature
def stamp(f) -> str:
    p = {k: v for k, v in (f.get("params") or {}).items()}
    return hashlib.sha1(json.dumps([f["type"], p], sort_keys=True, default=str).encode()).hexdigest()[:16]


class FeatureContext:
    """What a plugin feature's build(ctx) gets: its values, and the model before it."""

    def __init__(self, state, f):
        self.state = state
        self.id = f["id"]
        self.name = f.get("name", "")
        self.params = f["params"]
        self.variables = dict(state.vars)
        self.geom = geom

    def __getitem__(self, key):
        return self.params[key]

    def get(self, key, default=None):
        return self.params.get(key, default)

    def fail(self, message):
        raise K.KernelError(str(message))

    # selections
    def _items(self, key):
        return [tuple(it) for it in self.params.get(key) or []]

    def faces(self, key):
        from . import features as F
        return [F.resolve(self.state, r, "face")[1] for k, r in self._items(key) if k == "face"]

    def edges(self, key):
        from . import features as F
        out = []
        for k, r in self._items(key):
            if k == "edge":
                out.append(F.resolve(self.state, r, "edge")[1])
            elif k == "edges":
                out += [F.resolve(self.state, e, "edge")[1] for e in r]
        return out

    def points(self, key):
        from . import features as F
        return [F.resolve_point(self.state, r) for k, r in self._items(key) if k == "vertex"]

    def parts(self, key=None):
        if key is None:
            return list(self.state.parts.values())
        return [self.state.parts[r] for k, r in self._items(key) if k == "part" and r in self.state.parts]

    def plane(self, key, default="Top"):
        from . import features as F
        items = [r for k, r in self._items(key) if k == "plane"]
        if not items:
            return K.std_plane(default) if default else None
        return F.resolve_plane(self.state, items[0])

    def axis(self, key, default="Z"):
        from . import features as F
        items = [r for k, r in self._items(key) if k == "axis"]
        if not items:
            return F.resolve_axis(self.state, {"kind": "std", "name": default}) if default else None
        return F.resolve_axis(self.state, items[0])

    def regions(self, key):
        from . import features as F
        refs = [r for k, r in self._items(key) if k == "region"]
        return F.resolve_regions(self.state, refs) if refs else []

    # changing parts directly (for features that don't just return a body)
    def add_part(self, shape, name=None, color=None):
        from . import features as F
        p = self.state.new_part(shape, self.id, tuple(color) if color else F.DEFAULT_COLOR)
        if name:
            self.state.parts[p.id] = F.Part(p.id, name, p.shape, p.color)
        return p.id

    def replace_part(self, part_id, shape):
        from . import features as F
        part = self.state.parts[part_id]
        F.set_part_shape(self.state, part, shape)

    def remove_part(self, part_id):
        self.state.parts.pop(part_id, None)


def _apply(state, f, bodies):
    from . import features as F
    sols = []
    for b in bodies:
        sols += K.solids(b)
    if not sols:
        raise K.KernelError("The plugin made no solid")
    spec = FEATURES.get(f["type"])
    boolean = spec.boolean if spec is not None else "op" in f["params"]
    op = f["params"].get("op", "new") if boolean else "new"
    F.apply_body(state, sols[0] if len(sols) == 1 else K.compound(sols), op, f["id"])


def run_feature(state, f):
    spec = FEATURES.get(f["type"])
    raw = f.get("_raw_stamp") or stamp(f)
    f = {k: v for k, v in f.items() if k != "_raw_stamp"}
    st = hashlib.sha1((raw + stamp(f)).encode()).hexdigest()[:16]   # includes the values variables gave
    key = (CURRENT_DOC[0], f["id"], raw)
    if spec is None:
        fb = f.get("_fallback")
        who = plugin_of(f)
        if fb and fb.get("stamp") == st:
            _apply(state, f, [K.shape_from_brep(base64.b64decode(b)) for b in fb["breps"]])
            LAST_BODIES[key] = (st, fb)
            return
        if fb:
            raise K.KernelError(f"This feature was changed, and the “{who}” plugin it needs isn't installed")
        raise K.KernelError(f"This feature needs the “{who}” plugin, which isn't installed (Document ▸ Plugins…)")
    ints = {fl["key"] for fl in spec.fields if fl.get("int")}
    if ints:
        p = dict(f["params"])
        for k in ints:
            if isinstance(p.get(k), (int, float)):
                p[k] = int(round(p[k]))
        f = dict(f, params=p)
    ctx = FeatureContext(state, f)
    try:
        out = spec.build(ctx)
    except K.KernelError:
        raise
    except Exception as e:
        traceback.print_exc()
        raise K.KernelError(f"Plugin “{spec.plugin}”: {type(e).__name__}: {e}")
    if out is None:
        LAST_BODIES.pop(key, None)
        return
    bodies = list(out) if isinstance(out, (list, tuple)) else [out]
    LAST_BODIES[key] = (st, bodies)
    _apply(state, f, bodies)


def saved_copies(features, doc_uid="") -> dict:
    """{feature id: {"stamp", "breps"}} for the document's plugin features, so it opens without the plugin."""
    out = {}
    for f in features:
        if not is_plugin_type(f.get("type")):
            continue
        got = LAST_BODIES.get((doc_uid, f["id"], stamp(f)))
        if got is None:
            continue
        st, bodies = got
        if isinstance(bodies, dict):
            out[f["id"]] = bodies
            continue
        try:
            spec = FEATURES.get(f["type"])
            out[f["id"]] = {"stamp": st, "plugin": plugin_of(f),
                            "numeric": sorted(spec.numeric_keys()) if spec else [],
                            "breps": [base64.b64encode(K.shape_to_brep(b)).decode() for b in bodies]}
        except Exception:
            pass
    return out


# ---------------------------------------------------------------------- commands
class CommandContext:
    """What a plugin command's run(ctx) gets."""

    def __init__(self, main):
        self.main = main
        self.geom = geom

    @property
    def doc(self):
        return getattr(self.main, "doc", None)

    @property
    def state(self):
        d = self.doc
        return d.state if d is not None and hasattr(d, "state") else None

    @property
    def selection(self):
        vp = getattr(self.main, "viewport", None)
        return list(vp.selection) if vp is not None else []

    def selected_parts(self):
        """Parts that are selected (or own a selected face / edge)."""
        st = self.state
        out = []
        for pk in self.selection:
            if pk.kind in ("part", "face", "edge", "vertex"):
                pid = pk.key[0]
                if st is not None and pid in st.parts and st.parts[pid] not in out:
                    out.append(st.parts[pid])
        return out

    def message(self, text, title=None):
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.information(self.main, title or "Plugin", str(text))

    def ask_number(self, label, value=10.0, title=None):
        from PySide6.QtWidgets import QInputDialog
        v, ok = QInputDialog.getDouble(self.main, title or "Plugin", label, float(value), -1e9, 1e9, 4)
        return v if ok else None

    def add_shape(self, shape, name="Plugin part"):
        """Put a solid into the Part Studio (as an imported feature, so it is undoable and saved)."""
        d = self.doc
        if d is None or not hasattr(d, "features"):
            raise RuntimeError("Open a Part Studio first")
        import tempfile
        f = d.make_feature("import")
        f["name"] = name
        fd, tmp = tempfile.mkstemp(suffix=".brep")
        os.close(fd)
        try:
            K.export_brep([shape], tmp)                 # the text BREP an imported .brep file holds
            with open(tmp, "rb") as fh:
                data = fh.read()
        finally:
            os.unlink(tmp)
        f["params"].update(data=base64.b64encode(data).decode(), filename=name + ".brep")
        d.add_feature(f)
        return f["id"]

    def add_feature(self, ftype, **params):
        """Add a feature (built-in or a plugin's) with these parameters; returns its id."""
        from . import features as F
        d = self.doc
        if ftype not in F.RUNNERS and PREFIX + ftype in FEATURES:
            ftype = PREFIX + ftype
        f = d.make_feature(ftype)
        f["params"].update(params)
        d.add_feature(f)
        return f["id"]


def run_command(cmd: Command, main):
    return cmd.run(CommandContext(main))


def run_script(path, main=None):
    """Run a .py file with `ctx` (a CommandContext, when the app is open) and `fs` (freeshape.api) defined."""
    g = {"__name__": "__freeshape_script__", "__file__": path, "fs": geom,
         "ctx": CommandContext(main) if main is not None else None}
    with open(path, encoding="utf-8") as fh:
        code = compile(fh.read(), path, "exec")
    exec(code, g)
    return g
