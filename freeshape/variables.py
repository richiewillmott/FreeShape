"""The Variables table: every variable in a Part Studio in one list — its value, kind and where it's used — with its
value editable in place and a rename that follows it everywhere (features, sketch dimensions, configurations).
Like Onshape's variable table."""
from __future__ import annotations

import re

from .expr import VAR_RE

VAR_TYPES = ("variable", "measure_value")
FIXED = ("fixed_length", "fixed_angle", "fixed_number", "expression")


def _strings(value):
    """Every string inside a feature's settings."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)


def list_variables(doc) -> list[dict]:
    """[{fid, name, value, kind, expr, typed, suppressed, error, unit}] in feature-list order. typed: a value you
    entered (editable here); otherwise measured from the model."""
    st = doc.state
    out = []
    for f in doc.features:
        if f.get("type") not in VAR_TYPES:
            continue
        p = f.get("params", {})
        name = str(p.get("name", "")).strip().lstrip("#")
        typed = f["type"] == "variable" or p.get("mtype", "fixed_length") in FIXED
        out.append({"fid": f["id"], "name": name, "value": st.vars.get(name),
                    "kind": getattr(st, "var_kinds", {}).get(name), "expr": p.get("expr", "") if typed else "",
                    "typed": typed, "mtype": p.get("mtype", "expression") if f["type"] != "variable" else "old",
                    "unit": p.get("unit", "mm"), "suppressed": bool(f.get("suppressed")),
                    "error": doc.errors.get(f["id"])})
    return out


def uses(doc, name) -> list[str]:
    """Ids of the features that use #name (in any box, sketch dimension or text), the variable's own aside."""
    pat = re.compile(r"#" + re.escape(name) + r"(?![A-Za-z0-9_])")
    out = []
    for f in doc.features:
        if f.get("type") in VAR_TYPES and str(f.get("params", {}).get("name", "")).lstrip("#") == name:
            continue
        if any(pat.search(s) for s in _strings(f.get("params", {}))):
            out.append(f["id"])
    return out


def set_expr(doc, fid, expr):
    """Change a typed variable's value (text as you'd type it: '25', '1 in', '#a * 2')."""
    f = doc.feature(fid)
    if f is None:
        raise KeyError(fid)
    g = dict(f, params=dict(f.get("params", {}), expr=str(expr)))
    doc.replace_feature(g)


def _rename_text(text, old, new):
    return re.sub(r"#" + re.escape(old) + r"(?![A-Za-z0-9_])", "#" + new, text)


def _rename_in(value, old, new):
    if isinstance(value, str):
        return _rename_text(value, old, new) if "#" in value else value
    if isinstance(value, list):
        return [_rename_in(v, old, new) for v in value]
    if isinstance(value, dict):
        return {k: _rename_in(v, old, new) for k, v in value.items()}
    return value


VALID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def rename(doc, old, new):
    """Rename #old to #new everywhere: the variable itself, every feature box and sketch dimension that uses it,
    and the configuration table. One undo step."""
    new = new.strip().lstrip("#")
    if not VALID.match(new):
        raise ValueError("Variable names use letters, digits and _ (not starting with a digit)")
    if new == old:
        return
    if any(v["name"] == new for v in list_variables(doc)):
        raise ValueError(f"There's already a #{new}")
    doc.checkpoint()
    for i, f in enumerate(doc.features):
        p = f.get("params", {})
        if f.get("type") in VAR_TYPES and str(p.get("name", "")).lstrip("#") == old:
            p = dict(p, name=new)
            name = f.get("name", "")
            f = dict(f, name=("#" + new) if name == "#" + old else name)
        doc.features[i] = dict(f, params=_rename_in(p, old, new))
    for row in doc.configs.get("rows", []):
        vars_ = row.get("vars", {})
        row["vars"] = {(new if k == old else k): (_rename_text(v, old, new) if isinstance(v, str) else v)
                       for k, v in vars_.items()}
    doc.rebuild()


def add(doc, name, expr="10", mtype="fixed_length", unit="mm", index=None):
    """A new typed variable (at the end, or at `index`). Returns its feature."""
    name = name.strip().lstrip("#")
    if not VALID.match(name):
        raise ValueError("Variable names use letters, digits and _ (not starting with a digit)")
    if any(v["name"] == name for v in list_variables(doc)):
        raise ValueError(f"There's already a #{name}")
    f = doc.make_feature("measure_value")
    f["params"].update(name=name, mtype=mtype, expr=str(expr), unit=unit, aunit="deg")
    f["name"] = f"#{name}"
    doc.insert_feature(f, doc.insert_index() if index is None else index)
    return f
