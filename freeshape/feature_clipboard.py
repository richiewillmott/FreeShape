"""Copy and paste features (Ctrl+C / Ctrl+V in the feature list), within a Part Studio or between Part Studios.

Copied features keep their settings; pasted ones get new ids, and wherever one pasted feature uses another that
was copied with it (an extrude of a copied sketch, a part made by a copied feature), it uses the new copy.
References to features that weren't copied are left as they are: pasted into the same Part Studio they still find
them; pasted elsewhere they may need picking again, as in Onshape."""
from __future__ import annotations

import copy
import json

MARK = "freeshape-features"
VERSION = 1


def copy_features(doc, fids) -> dict:
    """What goes on the clipboard for these features (in feature-list order)."""
    order = [f for f in doc.features if f["id"] in set(fids)]
    return {"format": MARK, "version": VERSION, "source": getattr(doc, "uid", None),
            "features": [copy.deepcopy(f) for f in order]}


def to_text(payload) -> str:
    return json.dumps(payload)


def from_text(text):
    """The copied features in clipboard text, or None if it isn't FreeShape features."""
    if not text or MARK not in text[:200]:
        return None
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("format") != MARK or not isinstance(d.get("features"), list):
        return None
    return d


def _remap(value, ids):
    """Every id of a copied feature in `value` (or a part id it made, 'F2:1') swapped for the pasted one's."""
    if isinstance(value, str):
        if value in ids:
            return ids[value]
        head, sep, tail = value.partition(":")
        if sep and head in ids:
            return ids[head] + sep + tail
        return value
    if isinstance(value, list):
        return [_remap(v, ids) for v in value]
    if isinstance(value, dict):
        return {k: _remap(v, ids) for k, v in value.items()}
    return value


def paste(doc, payload, index=None) -> list:
    """Put copies of the copied features in at `index` (default: where new features go, at the rollback bar).
    Returns the new features."""
    feats = [copy.deepcopy(f) for f in payload.get("features", []) if isinstance(f, dict) and f.get("type")]
    if not feats:
        return []
    if index is None:
        index = doc.insert_index()
    ids = {f["id"]: doc.new_id() for f in feats}       # (new_id never repeats, so these are all different)
    names = {f["name"] for f in doc.features}
    out = []
    for f in feats:
        g = {k: v for k, v in f.items() if k not in ("folder",)}
        g["id"] = ids[f["id"]]
        g["params"] = _remap(f.get("params", {}), ids)
        name = f.get("name") or doc.next_name(f["type"])
        if name in names:
            k = 2
            while f"{name} ({k})" in names:
                k += 1
            name = f"{name} ({k})"
        names.add(name)
        g["name"] = name
        out.append(g)
    doc.insert_features(out, index)
    return out
