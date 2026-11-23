"""FreeShape scripts: a whole project — Part Studios, assemblies and drawings — as a Python file that builds it again.

Document → Export as Python script… writes one; Document → Run Python script… (or plain `python my_model.py`) runs
it. The script has three parts — CAD model, assembly, drawing — and the model's fixed variables come first, as
plain Python values: change one and run the script again.

A script is the feature list, written out: every Part Studio is rebuilt by FreeShape's own rebuild, exactly as when
a file is opened, so a script keeps working for as long as a saved file would (both go through the same loader).

    from freeshape import script

    def build(ws):
        ps = ws.part_studio("Bracket")
        ...
        return ws

    if __name__ == "__main__":
        script.main(build)          # saves Bracket.fshape … next to the script (or in a folder you name)
"""
from __future__ import annotations

import keyword
import math
import os
import re
import sys

SCRIPT_VERSION = 1


# =========================================================================== running a script
class Sketch:
    """A sketch, built up one point / curve / constraint at a time (with the ids later features refer to)."""

    def __init__(self):
        self.points = {"origin": [0.0, 0.0]}
        self.entities = {}
        self.constraints = []

    def point(self, pid, x, y):
        self.points[pid] = [float(x), float(y)]
        return pid

    def entity(self, eid, kind, **keys):
        self.entities[eid] = {"type": kind, **keys}
        return eid

    def constraint(self, cid, kind, refs, **keys):
        self.constraints.append({"id": cid, "type": kind, "refs": list(refs), **keys})
        return cid

    def to_dict(self):
        return {"points": {k: list(v) for k, v in self.points.items()},
                "entities": {k: dict(v) for k, v in self.entities.items()},
                "constraints": [dict(c) for c in self.constraints]}


def _plain(v):
    """Script values to file values: Sketch objects become their dicts, everything else as it is."""
    if isinstance(v, Sketch):
        return v.to_dict()
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class _Builder:
    kind = ""

    def __init__(self, ws, name, uid=None, **settings):
        import uuid
        self.ws = ws
        self.name = name
        self.uid = uid or uuid.uuid4().hex
        self.settings_ = dict(settings)
        self._doc = None

    def settings(self, **kw):
        self.settings_.update(kw)
        self._doc = None
        return self

    @property
    def document(self):
        if self._doc is None:
            self._doc = self._make()
        return self._doc


class PartStudio(_Builder):
    """A Part Studio: features in order, then the view settings; built by FreeShape's own file loader."""
    kind = "studio"

    def __init__(self, ws, name, uid=None, **settings):
        super().__init__(ws, name, uid, **settings)
        self.features = []

    def feature(self, ftype, id, name, params=None, suppressed=False, **extra):
        """Add a feature: its type, id (later features and assemblies refer to it), name and parameters."""
        f = {"id": id, "type": ftype, "name": name, "suppressed": bool(suppressed),
             "params": _plain(params or {})}
        f.update(_plain(extra))
        self.features.append(f)
        self._doc = None
        return id

    def to_dict(self):
        from .document import FILE_VERSION
        s = self.settings_
        return {"format": "freeshape", "version": FILE_VERSION, "name": self.name, "uid": self.uid,
                "features": self.features, "rollback": s.get("rollback"),
                "overrides": s.get("overrides", {}), "hidden": s.get("hidden_parts", []),
                "hidden_features": s.get("hidden_features", []), "folders": s.get("folders", {}),
                "configs": s.get("configs", {"rows": [], "active": 0}), "plane_sizes": s.get("plane_sizes", {}),
                "named_views": s.get("named_views", {}), "units": s.get("units", "mm"),
                "derived_copies": s.get("derived_copies", {}), "plugin_copies": s.get("plugin_copies", {})}

    def _make(self):
        from .document import Document
        doc = Document.from_dict(self.to_dict())
        doc.dirty = True
        return doc

    @property
    def errors(self):
        """{feature id: message} for features that didn't build."""
        return dict(self.document.errors)


class Assembly(_Builder):
    kind = "assembly"

    def __init__(self, ws, name, uid=None, **settings):
        super().__init__(ws, name, uid, **settings)
        self.instances, self.mates = [], []

    def instance(self, **inst):
        self.instances.append(_plain(inst))
        self._doc = None
        return inst.get("id")

    def mate(self, **mate):
        self.mates.append(_plain(mate))
        self._doc = None
        return mate.get("id")

    def to_dict(self):
        from .assembly import FILE_FORMAT, FILE_VERSION
        s = self.settings_
        return {"format": FILE_FORMAT, "version": FILE_VERSION, "name": self.name, "uid": self.uid,
                "instances": self.instances, "mates": self.mates,
                "explode": s.get("explode", {"steps": [], "amount": 1.0}), "relations": s.get("relations", []),
                "patterns": s.get("patterns", []), "subs": s.get("subs", []),
                "named_views": s.get("named_views", {}), "units": s.get("units", "mm"), "blobs": s.get("blobs", {})}

    def _make(self, refresh=True):
        from .assembly import AssemblyDoc
        a = AssemblyDoc.from_dict(self.to_dict(), None, self.ws.resolve_part, refresh=False)
        a.asm_resolver = self.ws.resolve_assembly
        if refresh:
            a.refresh(solve=True, notify=False)
        a.dirty = True
        return a


class Drawing(_Builder):
    kind = "drawing"

    def __init__(self, ws, name, uid=None, **settings):
        super().__init__(ws, name, uid, **settings)

    def to_dict(self):
        from .drawing import FILE_FORMAT, FILE_VERSION
        s = self.settings_
        d = {"format": FILE_FORMAT, "version": FILE_VERSION, "name": self.name, "uid": self.uid,
             "source": s.get("source"), "sheet": s.get("sheet", {}), "title": s.get("title", {}),
             "views": s.get("views", []), "annots": s.get("annots", []), "bom": s.get("bom", [])}
        if s.get("_brep"):
            d["_brep"] = s["_brep"]
        return d

    def _make(self, refresh=True):
        from .drawing import DrawingDoc
        g = DrawingDoc.from_dict(self.to_dict(), None, self.ws.resolve_drawing, refresh=refresh)
        g.dirty = True
        return g


class Workspace:
    """Everything a script builds, and how its documents find each other (by uid, or by file)."""

    def __init__(self, folder=None):
        self.items: list[_Builder] = []
        self.folder = folder
        self._files = {}

    def part_studio(self, name, uid=None, **settings) -> PartStudio:
        b = PartStudio(self, name, uid, **settings)
        self.items.append(b)
        return b

    def assembly(self, name, uid=None, **settings) -> Assembly:
        b = Assembly(self, name, uid, **settings)
        self.items.append(b)
        return b

    def drawing(self, name, uid=None, **settings) -> Drawing:
        b = Drawing(self, name, uid, **settings)
        self.items.append(b)
        return b

    # ---- finding sources
    def _by_uid(self, kind, uid):
        return next((b for b in self.items if b.kind == kind and b.uid == uid), None)

    def _file(self, path, kind):
        from .assembly import AssemblyDoc
        from .document import Document
        ap = os.path.abspath(path)
        if ap not in self._files:
            self._files[ap] = AssemblyDoc.load(ap, resolver=self.resolve_part) if kind == "assembly" \
                else Document.load(ap)
        return self._files[ap]

    def studio(self, src):
        from . import kernel as K
        b = self._by_uid("studio", src.get("uid"))
        if b is not None:
            return b.document
        path = src.get("path")
        if path and not os.path.isabs(path) and self.folder:
            path = os.path.join(self.folder, path)
        if path and os.path.exists(path):
            return self._file(path, "studio")
        raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't in the script")

    def resolve_part(self, src):
        from . import kernel as K
        studio = self.studio(src)
        st = studio.state_for(src.get("config"))
        p = st.parts.get(src.get("part"))
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        return p

    def resolve_assembly(self, src):
        b = self._by_uid("assembly", src.get("uid"))
        if b is not None:
            return b.document
        if src.get("path") and os.path.exists(src["path"]):
            return self._file(src["path"], "assembly")
        return None

    def resolve_drawing(self, src):
        """The shape a drawing shows (as the app's resolver: a part, all parts, a flat pattern or an assembly)."""
        from . import kernel as K
        if src.get("kind") == "assembly":
            asm = self.resolve_assembly(src)
            if asm is None:
                raise K.KernelError(f"Assembly “{src.get('name', '?')}” isn't in the script")
            ex = bool(src.get("exploded"))
            return K.compound([K.located(asm.parts[i["id"]].shape, asm.display_T(i["id"], exploded=ex, amount=1.0))
                               for i in asm.visible_instances() if i["id"] in asm.parts])
        studio = self.studio(src)
        st = studio.state_for(src.get("config"))
        if src.get("part") in (None, "*"):
            parts = [p for p in st.parts.values() if p.id not in studio.hidden_parts]
            if not parts:
                raise K.KernelError(f"“{studio.name}” has no parts")
            return parts[0].shape if len(parts) == 1 else K.compound([p.shape for p in parts])
        p = st.parts.get(src["part"])
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        if src.get("flat"):
            from . import sheetmetal as SM
            return SM.flat_pattern(p.shape, st.sheet[p.id]).solid()
        return p.shape

    # ---- results
    def documents(self):
        """Every document, built: Part Studios first, then assemblies, then drawings."""
        order = {"studio": 0, "assembly": 1, "drawing": 2}
        return [b.document for b in sorted(self.items, key=lambda b: order[b.kind])]

    def errors(self):
        """{document name: {feature id: message}} for whatever didn't build."""
        out = {}
        for b in self.items:
            e = getattr(b.document, "errors", None) or {}
            if e:
                out[b.name] = dict(e)
        return out

    def save(self, folder):
        """Write every document as a file in `folder` (Name.fshape / .fasm / .fdwg); returns the paths."""
        os.makedirs(folder, exist_ok=True)
        ext = {"studio": ".fshape", "assembly": ".fasm", "drawing": ".fdwg"}
        paths = []
        for b in sorted(self.items, key=lambda b: {"studio": 0, "assembly": 1, "drawing": 2}[b.kind]):
            safe = re.sub(r'[\\/:*?"<>|]+', "_", b.name).strip() or b.kind
            path = os.path.join(folder, safe + ext[b.kind])
            doc = b.document
            doc.save(path) if b.kind != "studio" else doc.save(path, version=False)
            paths.append(path)
        return paths


def run(build, folder=None) -> Workspace:
    """Run a script's build(ws) and return the workspace with everything it made."""
    ws = Workspace(folder)
    out = build(ws)
    return out if isinstance(out, Workspace) else ws


def run_file(path) -> Workspace:
    """Run a FreeShape script file (its build function) and return what it made. The script is ordinary Python,
    so only run scripts you trust — as with plugins."""
    import runpy
    ns = runpy.run_path(path, run_name="freeshape_script")
    build = ns.get("build")
    if not callable(build):
        raise ValueError("That script has no build(ws) function — is it a FreeShape script?")
    return run(build, os.path.dirname(os.path.abspath(path)))


def main(build, argv=None):
    """`python script.py [folder]`: build everything and save the files (next to the script by default)."""
    argv = sys.argv[1:] if argv is None else argv
    here = os.path.dirname(os.path.abspath(sys.modules["__main__"].__file__)) \
        if getattr(sys.modules.get("__main__"), "__file__", None) else os.getcwd()
    folder = argv[0] if argv else here
    ws = run(build, here)
    for name, errs in ws.errors().items():
        for fid, msg in errs.items():
            print(f"{name}: {fid}: {msg}")
    for p in ws.save(folder):
        print("saved", p)
    return ws


# =========================================================================== writing a script
_BIG = 300                            # strings longer than this go in the DATA section at the end


class _Raw(str):
    """Python code written into the script as itself (a variable's name, ps1.uid …), not as a quoted string."""


def _num(v, digits=3):
    """A short number: 40, 12.5, 0.333."""
    v = round(float(v), digits)
    return f"{v:.{digits}f}".rstrip("0").rstrip(".") if v != int(v) else str(int(v))


def _xyz(c):
    return "(" + ", ".join(_num(x, 2) for x in c) + ")"


def _facing(n):
    import numpy as np
    n = np.asarray(n, float)
    i = int(np.argmax(np.abs(n)))
    if abs(n[i]) > 0.999:
        return f"facing {'-' if n[i] < 0 else '+'}{'XYZ'[i]}"
    return "facing (" + ", ".join(_num(x, 2) for x in n) + ")"


_FACE_KIND = {0: "flat", 1: "cylinder", 2: "cone", 3: "sphere", 4: "torus"}
_EDGE_KIND = {0: "line", 1: "circle", 2: "ellipse"}


def _noted(key, text, ind):
    """`key` + a value; a value that starts with its "# what this is" comment gets the comment on its own line
    above the key, not squeezed in after it."""
    if text.startswith("# "):
        note, rest = text.split("\n", 1)
        return f"{note}\n{' ' * ind}{key}{rest.lstrip()}"
    return key + text


class _Plain(dict):
    """A reference written without its comment (its owner already says what it is)."""


class _Writer:
    def __init__(self, part_names=None, sketch_names=None, inst_names=None):
        self.data = {}
        self.part_names = part_names or {}           # part id -> name, to say whose face / edge a reference is
        self.sketch_names = sketch_names or {}       # sketch feature id -> name
        self.inst_names = inst_names or {}           # assembly instance id -> name

    def describe(self, v):
        """A plain-English line for a picked face / edge / corner / sketch area / sketch curve (what the
        fingerprint below it means), or None if `v` isn't one."""
        if not isinstance(v, dict) or isinstance(v, _Plain):
            return None
        kind = v.get("kind")
        whose = self.part_names.get(v.get("part"), v.get("part"))
        of = f" of {whose}" if whose else ""
        try:
            if kind == "face" and "area" in v:
                bits = [_FACE_KIND.get(v.get("type"), "curved") + (f" Ø{_num(2 * v['r'])}" if "r" in v else "")]
                if "n" in v:
                    bits.append(_facing(v["n"]))
                bits.append(f"{_num(v['area'], 1)} mm²")
                if "c" in v:
                    bits.append(f"centre {_xyz(v['c'])}")
                return f"face{of} — " + ", ".join(bits)
            if kind == "edge" and "len" in v:
                t = v.get("type")
                if t == 1 and "r" in v:
                    what = f"circle Ø{_num(2 * v['r'])}" + (f" round {_xyz(v['center'])}" if "center" in v else "")
                elif t == 0 and "p0" in v:
                    what = f"line {_num(v['len'])} mm from {_xyz(v['p0'])} to {_xyz(v['p1'])}"
                else:
                    what = f"{_EDGE_KIND.get(t, 'curve')} {_num(v['len'])} mm long" + \
                           (f", middle {_xyz(v['c'])}" if "c" in v else "")
                return f"edge{of} — {what}"
            if kind == "vertex" and "c" in v:
                return f"corner{of} at {_xyz(v['c'])}"
            if kind == "sketch_point" and "sketch" in v:
                return f"point {v.get('point')} of {self.sketch_names.get(v['sketch'], v['sketch'])}"
            if "sketch" in v and "area" in v and "loops" in v:
                holes = int(v.get("loops", 1)) - 1
                extra = f", with {holes} hole{'s' if holes > 1 else ''}" if holes > 0 else ""
                return (f"area of {self.sketch_names.get(v['sketch'], v['sketch'])} — {_num(v['area'], 1)} mm², "
                        f"{v.get('edges', '?')} edge{'' if v.get('edges') == 1 else 's'}{extra}")
            if "sketch" in v and "entity" in v:
                return f"curve {v['entity']} of {self.sketch_names.get(v['sketch'], v['sketch'])}"
            if "inst" in v and kind in ("face", "edge", "vertex") and isinstance(v.get("ref"), dict):
                inner = self.describe(v["ref"]) or kind
                return f"{self.inst_names.get(v['inst'], v['inst'])}: {inner}"
        except (KeyError, TypeError, ValueError):
            return None
        return None

    def lit(self, v, ind=0, width=110):
        """A Python literal for a file value: nested dicts / lists broken over lines when long; very long strings
        (embedded geometry, pictures) replaced by DATA["…"]."""
        if isinstance(v, _Raw):
            return str(v)
        if isinstance(v, str):
            if len(v) > _BIG:
                key = f"d{len(self.data) + 1}"
                self.data[key] = v
                return f'DATA["{key}"]'
            return repr(v)
        if isinstance(v, bool) or v is None:
            return repr(v)
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return f'float("{v}")'
            return repr(v)
        if isinstance(v, int):
            return repr(v)
        if isinstance(v, (list, tuple)):
            items = [self.lit(x, ind + 4, width) for x in v]
            one = "[" + ", ".join(items) + "]"
            if len(one) + ind <= width and "\n" not in one:
                return one
            pad = " " * (ind + 4)
            return "[\n" + "".join(f"{pad}{x},\n" for x in items) + " " * ind + "]"
        if isinstance(v, dict):
            note = self.describe(v)
            if note and "inst" in v:
                v = {k: (_Plain(x) if k == "ref" else x) for k, x in v.items()}    # said once, on the outer one
            items = [_noted(f"{self.lit(str(k))}: ", self.lit(x, ind + 4, width), ind + 4) for k, x in v.items()]
            one = "{" + ", ".join(items) + "}"
            if len(one) + ind <= width and "\n" not in one:
                body = one
            else:
                pad = " " * (ind + 4)
                body = "{\n" + "".join(f"{pad}{x},\n" for x in items) + " " * ind + "}"
            # what a picked face / edge / area is, in words, on the line above its fingerprint
            return f"# {note}\n{' ' * ind}{body}" if note else body
        return repr(v)

    def kwargs(self, d, ind, width=110):
        """keyword=value, … for a dict whose keys are names; one per line when it doesn't fit."""
        if any(not str(k).isidentifier() or keyword.iskeyword(str(k)) for k in d):
            return "**" + self.lit(dict(d), ind, width)
        parts = [_noted(f"{k}=", self.lit(v, ind + 4, width), ind + 4) for k, v in d.items()]
        one = ", ".join(parts)
        if len(one) + ind <= width - 20 and "\n" not in one:
            return one
        pad = " " * (ind + 4)
        return "\n" + "".join(f"{pad}{p},\n" for p in parts) + " " * ind


def _ident(name, taken):
    """A Python name for a FreeShape variable (not a keyword, not one the script uses already)."""
    n = re.sub(r"\W", "_", str(name)) or "v"
    if n[0].isdigit():
        n = "v_" + n
    if keyword.iskeyword(n) or n in _RESERVED:
        n += "_"
    base, k = n, 2
    while n in taken:
        n, k = f"{base}_{k}", k + 1
    taken.add(n)
    return n


_RESERVED = {"script", "build", "ws", "DATA", "main", "Sketch", "sk", "ps", "asm", "dwg", "math", "os", "sys"}


def _number(text):
    try:
        return float(str(text).strip())
    except (TypeError, ValueError):
        return None


def _fixed_variables(features):
    """[(feature, current text, unit comment)] for variables you typed a plain number into (not measured, not
    using other variables) — these become the values at the top of the script."""
    out = []
    for f in features:
        p = f.get("params", {})
        if f.get("type") == "variable" and _number(p.get("expr")) is not None:
            out.append((f, "expr", ""))
        elif f.get("type") == "measure_value" and p.get("mtype") in ("fixed_length", "fixed_angle", "fixed_number") \
                and _number(p.get("expr")) is not None:
            unit = {"fixed_length": p.get("unit", "mm"), "fixed_angle": p.get("aunit", "deg")}.get(p["mtype"], "")
            out.append((f, "expr", unit))
    return out


def _sketch_lines(w, var, sk, ind):
    pad = " " * ind
    out = [f"{pad}{var} = script.Sketch()"]
    for pid, (x, y) in sk.get("points", {}).items():
        if pid == "origin":
            continue
        out.append(f"{pad}{var}.point({pid!r}, {w.lit(float(x))}, {w.lit(float(y))})")
    for eid, e in sk.get("entities", {}).items():
        keys = {k: v for k, v in e.items() if k != "type"}
        tail = (", " + w.kwargs(keys, ind)) if keys else ""
        out.append(f"{pad}{var}.entity({eid!r}, {e['type']!r}{tail})")
    for c in sk.get("constraints", []):
        keys = {k: v for k, v in c.items() if k not in ("id", "type", "refs")}
        tail = (", " + w.kwargs(keys, ind)) if keys else ""
        out.append(f"{pad}{var}.constraint({c['id']!r}, {c['type']!r}, {w.lit(c.get('refs', []))}{tail})")
    return out


def _studio_lines(w, doc, var, top, taken):
    """The lines that build one Part Studio; fixed variables go to `top` as name = value."""
    ind = 4
    pad = " " * ind
    lines = [f"{pad}# ---- Part Studio “{doc.name}”",
             f"{pad}{var} = ws.part_studio({doc.name!r}, uid={doc.uid!r}, units={getattr(doc, 'units', 'mm')!r})"]
    hoisted = {}
    fixed = _fixed_variables(doc.features)
    if fixed:
        top.append(f"# Part Studio “{doc.name}” — its variables. Change a value and run the script again.")
    for f, key, unit in fixed:
        p = f["params"]
        py = _ident(p.get("name", "v"), taken)
        val = _number(p[key])
        txt = repr(int(val)) if float(val).is_integer() and "." not in str(p[key]) else repr(val)
        note = f"   # #{p.get('name')}" + (f" ({unit})" if unit else "")
        top.append(f"{py} = {txt}{note}")
        hoisted[f["id"]] = py
    if fixed:
        top.append("")
    for f in doc.features:
        params = dict(f.get("params", {}))
        extra = {k: v for k, v in f.items() if k not in ("id", "type", "name", "suppressed", "params")}
        head = f"{pad}{var}.feature({f['type']!r}, id={f['id']!r}, name={f.get('name', '')!r}"
        if f.get("suppressed"):
            head += ", suppressed=True"
        if f["id"] in hoisted:
            params["expr"] = _Raw(f"str({hoisted[f['id']]})")      # the value from the top of the script
        if f["type"] == "sketch" and isinstance(params.get("sketch"), dict):
            sv = "sk_" + re.sub(r"\W", "_", f["id"])
            lines += _sketch_lines(w, sv, params["sketch"], ind)
            params["sketch"] = _Raw(sv)
        body = w.lit(params, ind + 4)
        tail = (", " + w.kwargs(extra, ind)) if extra else ""
        lines.append(f"{head}, params={body}{tail})")
    s = {}
    if doc.rollback is not None:
        s["rollback"] = doc.rollback
    for key, val, default in (("overrides", doc.part_overrides, {}), ("hidden_parts", sorted(doc.hidden_parts), []),
                              ("hidden_features", sorted(doc.hidden_features), []), ("folders", doc.folders, {}),
                              ("configs", doc.configs, {"rows": [], "active": 0}),
                              ("plane_sizes", getattr(doc, "plane_sizes", {}), {}),
                              ("named_views", doc.named_views, {}),
                              ("derived_copies", doc._derived_copies(), {}),
                              ("plugin_copies", doc._plugin_copies(), {})):
        if val != default:
            s[key] = val
    if s:
        lines.append(f"{pad}{var}.settings({w.kwargs(s, ind)})")
    lines.append("")
    return lines


def _assembly_lines(w, asm, var, names):
    pad = "    "
    lines = [f"{pad}# ---- Assembly “{asm.name}”",
             f"{pad}{var} = ws.assembly({asm.name!r}, uid={asm.uid!r}, units={getattr(asm, 'units', 'mm')!r})"]
    import base64
    from . import kernel as K
    for inst in asm.instances:
        d = {k: v for k, v in inst.items() if not k.startswith("_")}
        src = dict(d.get("source", {}))
        if src.get("kind") == "studio" and src.get("uid") in names:
            src.pop("path", None)
            src.pop("relpath", None)
            src["uid"] = _Raw(f"{names[src['uid']]}.uid")
        elif src.get("kind") == "studio":
            p = asm.parts.get(inst["id"])                  # not in the script: keep a copy of its shape
            if p is not None:
                try:
                    d["_brep"] = base64.b64encode(K.shape_to_brep(p.shape)).decode()
                except Exception:
                    pass
        d["source"] = src
        lines.append(f"{pad}{var}.instance({w.kwargs(d, 4)})")
    for m in asm.mates:
        lines.append(f"{pad}{var}.mate({w.kwargs(m, 4)})")
    s = {}
    for key, val, default in (("explode", asm.explode, {"steps": [], "amount": 1.0}), ("relations", asm.relations, []),
                              ("patterns", asm.patterns, []), ("subs", asm.subs, []),
                              ("named_views", asm.named_views, {})):
        if val != default:
            s[key] = val
    used = {i["source"]["blob"] for i in asm.instances if i["source"].get("kind") == "step"}
    if used:
        s["blobs"] = {k: v for k, v in asm.blobs.items() if k in used}
    if s:
        lines.append(f"{pad}{var}.settings({w.kwargs(s, 4)})")
    lines.append("")
    return lines


def _drawing_lines(w, dwg, var, names):
    pad = "    "
    import base64
    from . import kernel as K
    src = dict(dwg.source or {})
    s = {}
    if src.get("uid") in names:
        src.pop("path", None)
        src.pop("relpath", None)
        src["uid"] = _Raw(f"{names[src['uid']]}.uid")
    elif dwg.shape is not None:
        try:
            s["_brep"] = base64.b64encode(K.shape_to_brep(dwg.shape)).decode()
        except Exception:
            pass
    s.update(source=src, sheet=dwg.sheet, title=dwg.title, views=dwg.views, annots=dwg.annots, bom=dwg.bom)
    return [f"{pad}# ---- Drawing “{dwg.name}”",
            f"{pad}{var} = ws.drawing({dwg.name!r}, uid={dwg.uid!r}, {w.kwargs(s, 4)})", ""]


def dump(docs) -> str:
    """A FreeShape script that builds these documents again (Part Studios, assemblies, drawings)."""
    from . import __version__
    studios = [d for d in docs if getattr(d, "kind", "studio") not in ("assembly", "drawing")]
    asms = [d for d in docs if getattr(d, "kind", "") == "assembly"]
    dwgs = [d for d in docs if getattr(d, "kind", "") == "drawing"]
    part_names = {pid: p.name for d in studios for pid, p in d.state.parts.items()}
    sketch_names = {f["id"]: f.get("name", f["id"]) for d in studios for f in d.features if f.get("type") == "sketch"}
    inst_names = {i["id"]: i.get("name", i["id"]) for a in asms for i in a.instances}
    w = _Writer(part_names, sketch_names, inst_names)
    taken = set(_RESERVED)
    names = {}
    top, body = [], []
    body.append("    # ======================================================== 1. CAD model")
    for k, d in enumerate(studios, 1):
        var = f"ps{k}"
        names[d.uid] = var
        body += _studio_lines(w, d, var, top, taken)
    if asms:
        body.append("    # ======================================================== 2. Assembly")
        for k, a in enumerate(asms, 1):
            var = f"asm{k}"
            names[a.uid] = var
            body += _assembly_lines(w, a, var, names)
    if dwgs:
        body.append("    # ======================================================== 3. Drawing")
        for k, g in enumerate(dwgs, 1):
            body += _drawing_lines(w, g, f"dwg{k}", names)
    title = ", ".join(d.name for d in docs)
    out = [f'"""FreeShape script: {title}.',
           "",
           f"Made by FreeShape {__version__} (Document → Export as Python script). Run it from FreeShape (Document →",
           "Run Python script…) to open it all as tabs, or with Python to save the files: python this_file.py [folder]",
           '"""',
           "from freeshape import script",
           "",
           f"script.require({SCRIPT_VERSION})",
           ""]
    out += top if top else ["# (no typed variables: add some in FreeShape with the Variable feature)", ""]
    out += ["", "def build(ws):"]
    out += body
    out += ["    return ws", "", ""]
    if w.data:
        out.append("# Embedded geometry and pictures the model needs (imported files, images, linked parts).")
        out.append("DATA = {")
        for key, val in w.data.items():
            chunks = [val[i:i + 100] for i in range(0, len(val), 100)]
            out.append(f"    {key!r}: (")
            out += [f"        {c!r}" for c in chunks]
            out.append("    ),")
        out.append("}")
        out += ["", ""]
    out += ['if __name__ == "__main__":', "    script.main(build)", ""]
    return "\n".join(out)


def require(version):
    """Stop with a clear message if the script is newer than this FreeShape."""
    if int(version) > SCRIPT_VERSION:
        raise RuntimeError(f"This script needs a newer FreeShape (script format {version}; this one reads "
                           f"{SCRIPT_VERSION}).")


def linked(docs, start):
    """The documents linked to `start` among `docs` (Part Studios its assemblies use, drawings of it, …), in the
    order Part Studios, assemblies, drawings — what Export as Python script writes out."""
    by_uid = {d.uid: d for d in docs if getattr(d, "uid", None)}
    edges = {d.uid: set() for d in by_uid.values()}

    def link(a, b):
        if a in edges and b in edges:
            edges[a].add(b)
            edges[b].add(a)
    for d in by_uid.values():
        kind = getattr(d, "kind", "studio")
        if kind == "assembly":
            for inst in d.instances:
                link(d.uid, inst.get("source", {}).get("uid"))
            for sub in getattr(d, "subs", []):
                link(d.uid, sub.get("source", {}).get("uid"))
        elif kind == "drawing" and d.source:
            link(d.uid, d.source.get("uid"))
    seen, todo = {start.uid}, [start.uid]
    while todo:
        for n in edges.get(todo.pop(), ()):
            if n not in seen:
                seen.add(n)
                todo.append(n)
    order = {"assembly": 1, "drawing": 2}
    return sorted((by_uid[u] for u in seen), key=lambda d: (order.get(getattr(d, "kind", "studio"), 0),
                                                            [x.uid for x in docs].index(d.uid)))
