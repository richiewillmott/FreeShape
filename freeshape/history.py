"""Version history of a Part Studio: named versions saved in the file, and a readable "what changed" between any
two (features added / removed / changed, setting by setting; sketch edits; variables; part volumes) plus the
material added and removed, for showing in 3D."""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json

from . import kernel as K
from .sketch import DIMENSIONS, CONSTRAINT_LABELS

MAX_AUTO = 50              # automatic versions (made on save) kept; named ones are always kept
BLOB_MIN = 2000            # imported geometry bigger than this is stored once, shared by every version


# ---------------------------------------------------------------------- snapshots
def _blobify(features, blobs):
    out = copy.deepcopy(features)
    for f in out:
        p = f.get("params", {})
        d = p.get("data")
        if isinstance(d, str) and len(d) > BLOB_MIN:
            h = hashlib.sha1(d.encode()).hexdigest()
            blobs.setdefault(h, d)
            p["data"] = "@blob:" + h
    return out


def _unblob(features, blobs):
    out = copy.deepcopy(features)
    for f in out:
        p = f.get("params", {})
        d = p.get("data")
        if isinstance(d, str) and d.startswith("@blob:"):
            p["data"] = blobs.get(d[6:], "")
    return out


def snapshot(doc) -> dict:
    """The parts of a document a version keeps (not the view: hidden parts, camera …)."""
    return {"features": _blobify(doc.features, doc.version_blobs), "configs": copy.deepcopy(doc.configs),
            "folders": copy.deepcopy(doc.folders), "overrides": copy.deepcopy(doc.part_overrides),
            "rollback": doc.rollback}


def _same(a, b) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def now_iso():
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def nice_time(iso) -> str:
    try:
        t = _dt.datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return str(iso or "")
    today = _dt.date.today()
    if t.date() == today:
        return "Today " + t.strftime("%H:%M")
    if t.date() == today - _dt.timedelta(days=1):
        return "Yesterday " + t.strftime("%H:%M")
    return t.strftime("%d %b %Y %H:%M").lstrip("0")


def create(doc, name=None, note="", auto=False):
    """Add a version of the document as it is now. Returns it (or None for an automatic one when nothing changed
    since the last version)."""
    snap = snapshot(doc)
    if auto and doc.versions and _same(doc.versions[-1]["snapshot"], snap):
        return None
    n = 1 + max([int(v["id"][1:]) for v in doc.versions if v["id"][1:].isdigit()] + [0])
    named = sum(1 for v in doc.versions if not v.get("auto")) + 1
    v = {"id": f"V{n}", "name": name or ("Saved" if auto else f"Version {named}"), "note": note, "auto": auto,
         "time": now_iso(), "snapshot": snap}
    doc.versions.append(v)
    autos = [x for x in doc.versions if x.get("auto")]
    for old in autos[:-MAX_AUTO] if len(autos) > MAX_AUTO else []:
        doc.versions.remove(old)
    _prune_blobs(doc)
    return v


def _prune_blobs(doc):
    used = set()
    for v in doc.versions:
        for f in v["snapshot"]["features"]:
            d = f.get("params", {}).get("data")
            if isinstance(d, str) and d.startswith("@blob:"):
                used.add(d[6:])
    for h in list(doc.version_blobs):
        if h not in used:
            del doc.version_blobs[h]


def version(doc, vid):
    return next((v for v in doc.versions if v["id"] == vid), None)


def features_of(doc, v):
    return _unblob(v["snapshot"]["features"], doc.version_blobs)


def current(doc) -> dict:
    return {"features": copy.deepcopy(doc.features), "configs": doc.configs, "folders": doc.folders,
            "overrides": doc.part_overrides, "rollback": doc.rollback}


def expanded(doc, v) -> dict:
    """A version's snapshot with its imported geometry put back."""
    s = dict(v["snapshot"])
    s["features"] = features_of(doc, v)
    return s


def restore(doc, vid):
    """Make the document what it was at that version (undoable)."""
    v = version(doc, vid)
    if v is None:
        raise KeyError(vid)
    s = expanded(doc, v)
    doc.checkpoint()
    doc.features = s["features"]
    doc.configs = copy.deepcopy(s.get("configs") or {"rows": [], "active": 0})
    doc.folders = copy.deepcopy(s.get("folders") or {})
    doc.part_overrides = copy.deepcopy(s.get("overrides") or {})
    doc.rollback = s.get("rollback")
    nums = [int("".join(c for c in f["id"] if c.isdigit()) or 0) for f in doc.features]
    doc._counter = max([doc._counter] + nums)
    doc.rebuild()


def state_of(doc, snap):
    """The model a snapshot builds (a scratch document, so the open one isn't touched)."""
    from .document import Document
    d = Document()
    d.uid = doc.uid
    d.features = copy.deepcopy(snap["features"])
    d.configs = copy.deepcopy(snap.get("configs") or {"rows": [], "active": 0})
    d.folders = copy.deepcopy(snap.get("folders") or {})
    d.part_overrides = copy.deepcopy(snap.get("overrides") or {})
    d.rollback = snap.get("rollback")
    d.derived_fallback = doc.derived_fallback
    d.plugin_fallback = doc.plugin_fallback
    d._cache = doc._cache                     # unchanged features aren't rebuilt
    d.rebuild()
    return d.state, d.errors


# ---------------------------------------------------------------------- describing settings
LENGTH_KEYS = {"depth", "depth2", "radius", "distance", "thickness", "offset", "spacing", "diameter",
               "counterbore_dia", "counterbore_depth", "countersink_dia", "dx", "dy", "dz", "major", "pitch",
               "length", "clearance", "thread_clearance"}
ANGLE_KEYS = {"angle", "rotate", "draft", "twist"}
LABELS = {
    "depth": "Depth", "depth2": "Second depth", "end": "End", "op": "Result", "angle": "Angle",
    "radius": "Radius", "distance": "Distance", "thickness": "Thickness", "offset": "Offset", "count": "Count",
    "spacing": "Spacing", "flip": "Flip", "second": "Second direction", "regions": "Sketch regions",
    "faces": "Faces", "edges": "Edges", "parts": "Parts", "tools": "Tool parts", "targets": "Target parts",
    "plane": "Plane", "axis": "Axis", "direction": "Direction", "diameter": "Diameter", "size": "Size",
    "standard": "Standard", "style": "Style", "fit": "Fit", "outward": "Outward", "keep": "Keep",
    "copy": "Make a copy", "dx": "Move X", "dy": "Move Y", "dz": "Move Z", "rotate": "Rotate", "k": "K-factor",
    "length": "Length", "pitch": "Pitch", "major": "Major diameter", "left_hand": "Left-hand", "type": "Type",
    "profiles": "Profiles", "path": "Path", "neutral": "Neutral plane", "up_to": "Up to", "features": "Features",
    "mode": "Mode", "position": "Position", "down": "Other way", "relief": "Corner reliefs", "config": "Configuration",
    "sketch": "Sketch", "expr": "Value", "name": "Name", "keep_tools": "Keep tool parts", "ruled": "Ruled",
}
VALUE_NAMES = {"new": "New", "add": "Add", "remove": "Remove", "intersect": "Intersect", "union": "Union",
               "subtract": "Subtract", "blind": "Blind", "through": "Through all", "full": "Full turn",
               "up_to_face": "Up to face", "up_to_part": "Up to part", "up_to_vertex": "Up to vertex",
               "symmetric": "Symmetric"}


def _label(ftype, key):
    if str(ftype).startswith("plugin:"):
        from . import plugins
        spec = plugins.FEATURES.get(ftype)
        for f in spec.fields if spec else []:
            if f["key"] == key:
                return f["label"]
    return LABELS.get(key, key.replace("_", " ").capitalize())


def _num(v, key):
    if isinstance(v, str):
        return v                                 # an expression, e.g. "#wall * 2"
    if isinstance(v, bool):
        return "on" if v else "off"
    if isinstance(v, int) and key not in LENGTH_KEYS | ANGLE_KEYS:
        return str(v)
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s + (" mm" if key in LENGTH_KEYS else "°" if key in ANGLE_KEYS else "")


def _value(v, key):
    if v is None:
        return "—"
    if isinstance(v, (bool, int, float)) or (isinstance(v, str) and key in LENGTH_KEYS | ANGLE_KEYS):
        return _num(v, key)
    if isinstance(v, str):
        return VALUE_NAMES.get(v, v if len(v) < 40 else v[:37] + "…")
    if isinstance(v, dict):
        if v.get("kind") == "std" and v.get("name"):
            return v["name"]
        return "a selection"
    return str(v)


def _plural(n, word):
    irregular = {"vertex": "vertices", "axis": "axes"}
    return f"{n} {word if n == 1 else irregular.get(word, word + 's')}"


def describe_params(ftype, old, new):
    """Readable lines for what changed in a feature's settings."""
    out = []
    if ftype == "sketch":
        out += sketch_changes(old.get("sketch") or {}, new.get("sketch") or {})
        if not _same(old.get("plane"), new.get("plane")):
            out.append(f"Moved to another plane ({_value(old.get('plane'), 'plane')} → "
                       f"{_value(new.get('plane'), 'plane')})")
        return out
    if ftype == "variable":
        if old.get("name") != new.get("name"):
            out.append(f"Renamed #{old.get('name')} → #{new.get('name')}")
        if str(old.get("expr")) != str(new.get("expr")):
            out.append(f"#{new.get('name')}: {old.get('expr')} → {new.get('expr')}")
        return out
    if ftype == "import":
        if old.get("data") != new.get("data"):
            out.append(f"Geometry replaced ({new.get('filename') or 'new file'})")
        return out
    for key in list(old) + [k for k in new if k not in old]:
        if key.startswith("_") or _same(old.get(key), new.get(key)):
            continue
        a, b = old.get(key), new.get(key)
        lab = _label(ftype, key)
        if isinstance(a, list) or isinstance(b, list):
            na, nb = len(a or []), len(b or [])
            word = lab.lower()
            out.append(f"{lab}: {na} → {nb} selected" if na != nb else f"{lab}: a different selection"
                       if word else lab)
        else:
            out.append(f"{lab}: {_value(a, key)} → {_value(b, key)}")
    return out


_ENTITY_WORDS = {"line": "line", "circle": "circle", "arc": "arc", "point": "point", "spline": "spline",
                 "ellipse": "ellipse", "text": "text"}


def sketch_changes(old, new):
    out = []
    oe, ne = old.get("entities", {}), new.get("entities", {})
    for label, ids, src in (("Added ", [k for k in ne if k not in oe], ne),
                            ("Removed ", [k for k in oe if k not in ne], oe)):
        counts = {}
        for k in ids:
            e = src[k]
            word = ("construction " if e.get("construction") else "") + _ENTITY_WORDS.get(e["type"], e["type"])
            counts[word] = counts.get(word, 0) + 1
        if counts:
            out.append(label + ", ".join(_plural(n, w) for w, n in counts.items()))
    for k in ne:
        if k in oe and oe[k].get("type") == "text" and oe[k].get("text") != ne[k].get("text"):
            out.append(f"Text “{oe[k].get('text')}” → “{ne[k].get('text')}”")
        elif k in oe and oe[k].get("construction") != ne[k].get("construction"):
            out.append(f"A {_ENTITY_WORDS.get(ne[k]['type'], 'curve')} made "
                       + ("construction" if ne[k].get("construction") else "normal geometry"))
    oc = {c["id"]: c for c in old.get("constraints", []) if "id" in c}
    nc = {c["id"]: c for c in new.get("constraints", []) if "id" in c}

    def dim(c):
        v = c.get("value")
        if isinstance(v, str):
            return v
        if v is None:
            return "?"
        return f"{float(v):.4f}".rstrip("0").rstrip(".") + ("°" if c["type"] == "angle" else " mm")

    def name(c):
        return CONSTRAINT_LABELS.get(c["type"], c["type"]).lower()
    for i, c in nc.items():
        if c["type"] in DIMENSIONS:
            if i not in oc:
                out.append(f"Added a {name(c)} dimension ({dim(c)})")
            elif not _same(oc[i].get("value"), c.get("value")):
                out.append(f"{name(c).capitalize()} dimension {dim(oc[i])} → {dim(c)}")
    for i, c in oc.items():
        if c["type"] in DIMENSIONS and i not in nc:
            out.append(f"Removed a {name(c)} dimension ({dim(c)})")
    for label, a, b in (("Added ", nc, oc), ("Removed ", oc, nc)):
        kinds = {}
        for i, c in a.items():
            if i not in b and c["type"] not in DIMENSIONS:
                kinds[name(c)] = kinds.get(name(c), 0) + 1
        if kinds:
            n = sum(kinds.values())
            out.append(f"{label}{_plural(n, 'constraint')} ({', '.join(sorted(kinds))})")
    op, npt = old.get("points", {}), new.get("points", {})
    moved = sum(1 for k, p in npt.items() if k in op and max(abs(p[0] - op[k][0]), abs(p[1] - op[k][1])) > 1e-6)
    if moved and not out:
        out.append(f"{_plural(moved, 'point')} moved")
    return out


def _lcs_ids(a, b):
    """Ids in both lists that kept their order (longest common subsequence)."""
    n, m = len(a), len(b)
    L = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            L[i][j] = L[i + 1][j + 1] + 1 if a[i] == b[j] else max(L[i + 1][j], L[i][j + 1])
    out, i, j = set(), 0, 0
    while i < n and j < m:
        if a[i] == b[j]:
            out.add(a[i])
            i, j = i + 1, j + 1
        elif L[i + 1][j] >= L[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def feature_changes(old_snap, new_snap):
    """[{"kind": added|removed|changed, "name", "type", "lines": [..]}] in the newer version's order."""
    of = {f["id"]: f for f in old_snap["features"]}
    nf = {f["id"]: f for f in new_snap["features"]}
    kept_order = _lcs_ids([f["id"] for f in old_snap["features"] if f["id"] in nf],
                          [f["id"] for f in new_snap["features"] if f["id"] in of])
    out = []
    for f in new_snap["features"]:
        o = of.get(f["id"])
        if o is None:
            out.append({"kind": "added", "name": f["name"], "type": f["type"], "lines": []})
            continue
        lines = []
        if o.get("name") != f.get("name"):
            lines.append(f"Renamed from “{o.get('name')}”")
        if bool(o.get("suppressed")) != bool(f.get("suppressed")):
            lines.append("Suppressed" if f.get("suppressed") else "Unsuppressed")
        if f["id"] not in kept_order:
            lines.append("Moved in the feature list")
        if o.get("type") == f.get("type"):
            lines += describe_params(f["type"], o.get("params", {}), f.get("params", {}))
        else:
            lines.append("Changed type")
        if lines:
            out.append({"kind": "changed", "name": f["name"], "type": f["type"], "lines": lines})
    for f in old_snap["features"]:
        if f["id"] not in nf:
            out.append({"kind": "removed", "name": f["name"], "type": f["type"], "lines": []})
    return out


def other_changes(old_snap, new_snap):
    out = []
    oc, nc = old_snap.get("configs") or {}, new_snap.get("configs") or {}
    on = [r["name"] for r in oc.get("rows", [])]
    nn = [r["name"] for r in nc.get("rows", [])]
    for n in nn:
        if n not in on:
            out.append(f"Configuration “{n}” added")
    for n in on:
        if n not in nn:
            out.append(f"Configuration “{n}” removed")
    orow = {r["name"]: r for r in oc.get("rows", [])}
    for r in nc.get("rows", []):
        if r["name"] in orow and not _same(orow[r["name"]], r):
            out.append(f"Configuration “{r['name']}” changed")
    oo, no = old_snap.get("overrides") or {}, new_snap.get("overrides") or {}
    for pid in set(oo) | set(no):
        a, b = oo.get(pid, {}), no.get(pid, {})
        if a.get("name") != b.get("name") and b.get("name"):
            out.append(f"Part renamed {a.get('name') or pid} → {b['name']}")
        if a.get("color") != b.get("color") and b.get("color"):
            out.append(f"{b.get('name') or a.get('name') or 'A part'}'s colour changed")
    return out


# ---------------------------------------------------------------------- geometry
def geometry_changes(old_state, new_state, shapes=True):
    """{"rows": [(name, old volume or None, new volume or None)], "added": [shape], "removed": [shape]}.
    Volumes in mm³; shapes are the material added / removed (for showing in 3D)."""
    rows, added, removed = [], [], []
    for pid, p in new_state.parts.items():
        o = old_state.parts.get(pid)
        if o is None:
            rows.append((p.name, None, K.volume(p.shape)))
            if shapes:
                added.append(p.shape)
            continue
        if o.shape.IsSame(p.shape):
            continue
        vo, vn = K.volume(o.shape), K.volume(p.shape)
        rows.append((p.name, vo, vn))
        if shapes:
            for sh, into in ((K.cut(p.shape, o.shape), added), (K.cut(o.shape, p.shape), removed)):
                try:
                    if K.solids(sh) and K.volume(sh) > 1e-9 * max(vo, vn, 1.0):
                        into.append(sh)
                except Exception:
                    pass
    for pid, o in old_state.parts.items():
        if pid not in new_state.parts:
            rows.append((o.name, K.volume(o.shape), None))
            if shapes:
                removed.append(o.shape)
    return {"rows": rows, "added": added, "removed": removed}


def compare(doc, old_vid=None, new_vid=None, geometry=True):
    """What changed from version old_vid to version new_vid (None = the document as it is now)."""
    old_snap = current(doc) if old_vid is None else expanded(doc, version(doc, old_vid))
    new_snap = current(doc) if new_vid is None else expanded(doc, version(doc, new_vid))
    res = {"features": feature_changes(old_snap, new_snap), "other": other_changes(old_snap, new_snap)}
    if geometry:
        os_, _ = state_of(doc, old_snap)
        ns_ = doc.state if new_vid is None else state_of(doc, new_snap)[0]
        res["geometry"] = geometry_changes(os_, ns_)
    return res


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_html(res, old_label="before", new_label="now") -> str:
    """The comparison as simple HTML (for the History panel)."""
    h = []
    feats = res.get("features", [])
    marks = {"added": ("+", "#1e8e3e"), "removed": ("−", "#d93025"), "changed": ("~", "#1a73e8")}
    if feats:
        h.append("<p><b>Features</b></p><table cellspacing=0 cellpadding=1>")
        for c in feats:
            sym, col = marks[c["kind"]]
            what = {"added": "added", "removed": "removed", "changed": ""}[c["kind"]]
            h.append(f"<tr><td valign=top style='color:{col}; font-weight:bold; padding-right:6px'>{sym}</td>"
                     f"<td><b>{_esc(c['name'])}</b> <span style='color:#888'>{what}</span>")
            for ln in c["lines"]:
                h.append(f"<br>&nbsp;&nbsp;{_esc(ln)}")
            h.append("</td></tr>")
        h.append("</table>")
    if res.get("other"):
        h.append("<p><b>Other</b></p><ul style='margin-left:-20px'>" + "".join(
            f"<li>{_esc(x)}</li>" for x in res["other"]) + "</ul>")
    g = res.get("geometry")
    if g is not None:
        if g["rows"]:
            h.append("<p><b>Parts</b> <span style='color:#888'>(volume)</span></p><table cellspacing=0 cellpadding=1>")
            for name, a, b in g["rows"]:
                if a is None:
                    txt = f"new · {b / 1000:.2f} cm³"
                elif b is None:
                    txt = f"removed · was {a / 1000:.2f} cm³"
                else:
                    d = (b - a) / 1000
                    pct = f" ({d / (a / 1000) * 100:+.1f}%)" if a > 1e-9 else ""
                    txt = f"{a / 1000:.2f} → {b / 1000:.2f} cm³ <span style='color:{'#1e8e3e' if d >= 0 else '#d93025'}'>" \
                          f"{d:+.2f}{pct}</span>" if abs(d) > 5e-6 else "same volume, different shape"
                h.append(f"<tr><td style='padding-right:10px'>{_esc(name)}</td><td>{txt}</td></tr>")
            h.append("</table>")
        elif feats:
            h.append("<p style='color:#888'>The parts' shapes didn't change.</p>")
    if not h:
        h.append(f"<p style='color:#888'>No changes between {_esc(old_label)} and {_esc(new_label)}.</p>")
    return "".join(h)
