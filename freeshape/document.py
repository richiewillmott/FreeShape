"""Document: the ordered feature list, rollback bar, undo/redo, save/load, and a cached rebuild."""
from __future__ import annotations

import copy
import hashlib
import json
import traceback

from . import kernel as K
from .features import RUNNERS, FEATURE_LABELS, ModelState, default_params, Part, prepare, carry_face_colors

FILE_VERSION = 1


class Document:
    kind = "studio"

    def __init__(self):
        import uuid
        self.uid = uuid.uuid4().hex                 # lets assemblies find this Part Studio again
        self.name = "Part Studio 1"
        self.features: list[dict] = []
        self.rollback: int | None = None          # number of active features; None = all
        self.part_overrides: dict = {}            # part id -> {"name":..., "color":...}
        self.hidden_parts: set = set()
        self.hidden_features: set = set()         # sketches / planes hidden in the view
        self.path = None
        self.dirty = False
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._cache: dict[str, ModelState] = {}
        self.errors: dict[str, str] = {}
        self.state = ModelState()
        self._counter = 0
        self.listeners = []

    # ------------------------------------------------------------------ ids / names
    def new_id(self, prefix="F"):
        self._counter += 1
        ids = {f["id"] for f in self.features}
        while f"{prefix}{self._counter}" in ids:
            self._counter += 1
        return f"{prefix}{self._counter}"

    def next_name(self, ftype):
        label = FEATURE_LABELS.get(ftype, ftype.title())
        n = 1
        names = {f["name"] for f in self.features}
        while f"{label} {n}" in names:
            n += 1
        return f"{label} {n}"

    def make_feature(self, ftype, state=None):
        return {"id": self.new_id(), "type": ftype, "name": self.next_name(ftype),
                "suppressed": False, "params": default_params(ftype, state or self.state)}

    # ------------------------------------------------------------------ undo / redo
    def _snapshot(self) -> str:
        return json.dumps({"features": self.features, "rollback": self.rollback,
                           "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                           "hidden_features": sorted(self.hidden_features)})

    def _restore(self, snap: str):
        d = json.loads(snap)
        self.features = d["features"]
        self.rollback = d["rollback"]
        self.part_overrides = d["overrides"]
        self.hidden_parts = set(d["hidden"])
        self.hidden_features = set(d.get("hidden_features", []))

    def checkpoint(self):
        """Call before any user-visible change."""
        self._undo.append(self._snapshot())
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def can_undo(self):
        return bool(self._undo)

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if self._undo:
            self._redo.append(self._snapshot())
            self._restore(self._undo.pop())
            self.dirty = True
            self.rebuild()

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self.dirty = True
            self.rebuild()

    # ------------------------------------------------------------------ editing
    def active_count(self):
        return len(self.features) if self.rollback is None else min(self.rollback, len(self.features))

    def insert_index(self):
        return self.active_count()

    def add_feature(self, feat, index=None):
        self.checkpoint()
        if index is None:
            index = self.insert_index()
        self.features.insert(index, feat)
        if self.rollback is not None:
            self.rollback = index + 1
            if self.rollback >= len(self.features):
                self.rollback = None
        self.rebuild()

    def replace_feature(self, feat):
        self.checkpoint()
        for i, f in enumerate(self.features):
            if f["id"] == feat["id"]:
                self.features[i] = feat
                break
        self.rebuild()

    def delete_features(self, ids):
        self.checkpoint()
        idx = [i for i, f in enumerate(self.features) if f["id"] in ids]
        self.features = [f for f in self.features if f["id"] not in ids]
        if self.rollback is not None:
            self.rollback -= sum(1 for i in idx if i < self.rollback)
            if self.rollback >= len(self.features):
                self.rollback = None
        self.rebuild()

    def set_suppressed(self, fid, value):
        self.checkpoint()
        for f in self.features:
            if f["id"] == fid:
                f["suppressed"] = value
        self.rebuild()

    def rename_feature(self, fid, name):
        self.checkpoint()
        for f in self.features:
            if f["id"] == fid:
                f["name"] = name
        self.notify()

    def move_feature(self, fid, new_index):
        self.checkpoint()
        i = next(i for i, f in enumerate(self.features) if f["id"] == fid)
        f = self.features.pop(i)
        self.features.insert(min(new_index, len(self.features)), f)
        self.rebuild()

    def set_rollback(self, n):
        n = None if n is None or n >= len(self.features) else max(0, n)
        if n != self.rollback:
            self.rollback = n
            self.rebuild()

    def feature(self, fid):
        return next((f for f in self.features if f["id"] == fid), None)

    def feature_index(self, fid):
        return next((i for i, f in enumerate(self.features) if f["id"] == fid), None)

    def set_part_override(self, pid, **kw):
        self.checkpoint()
        self.part_overrides.setdefault(pid, {}).update(kw)
        self._apply_overrides(self.state)
        self.notify()

    def toggle_part_hidden(self, pid):
        self.hidden_parts ^= {pid}
        self.notify()

    def toggle_feature_hidden(self, fid):
        self.hidden_features ^= {fid}
        self.notify()

    # ------------------------------------------------------------------ rebuild
    @staticmethod
    def _key(prefix_json: str) -> str:
        return hashlib.sha1(prefix_json.encode()).hexdigest()

    def compute(self, features, upto=None, errors=None):
        """Replay features (with caching). Returns the resulting ModelState."""
        state = ModelState()
        h = hashlib.sha1()
        n = len(features) if upto is None else upto
        new_cache = {}
        for f in features[:n]:
            h.update(json.dumps(f, sort_keys=True).encode())
            key = h.hexdigest()
            if key in self._cache:
                state, err = self._cache[key]
            else:
                state = state.copy()
                err = None
                if not f.get("suppressed"):
                    before = dict(state.parts)
                    try:
                        RUNNERS[f["type"]](state, prepare(f, state))
                        for pid, part in list(state.parts.items()):
                            old = before.get(pid) or before.get(pid.split(".")[0])
                            if old is not None and old.face_colors and part.shape is not old.shape \
                                    and part.face_colors is None:
                                state.parts[pid] = Part(part.id, part.name, part.shape, part.color,
                                                        carry_face_colors(old, part.shape))
                    except K.KernelError as e:
                        err = str(e)
                    except Exception as e:  # never let one feature kill the model
                        traceback.print_exc()
                        err = f"{type(e).__name__}: {e}"
            new_cache[key] = (state, err)
            if errors is not None and err:
                errors[f["id"]] = err
        self._cache.update(new_cache)
        if len(self._cache) > 400:
            self._cache = new_cache
        return state

    def rebuild(self):
        errors = {}
        self.state = self.compute(self.features, self.active_count(), errors).copy()
        self.errors = errors
        self._apply_overrides(self.state)
        self.notify()

    def preview(self, feat, index):
        """Model state with `feat` inserted (or replacing its old self) at index."""
        feats = [f for f in self.features if f["id"] != feat["id"]]
        index = min(index, len(feats))
        feats = feats[:index] + [feat]
        errors = {}
        st = self.compute(feats, None, errors).copy()
        self._apply_overrides(st)
        return st, errors.get(feat["id"])

    def state_before(self, index):
        st = self.compute(self.features, index).copy()
        self._apply_overrides(st)
        return st

    def _apply_overrides(self, state):
        for pid, ov in self.part_overrides.items():
            p = state.parts.get(pid)
            if p:
                state.parts[pid] = Part(p.id, ov.get("name", p.name), p.shape, tuple(ov.get("color", p.color)),
                                        None if "color" in ov else p.face_colors)

    def notify(self):
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ files
    def to_json(self):
        return json.dumps({"format": "freeshape", "version": FILE_VERSION, "name": self.name, "uid": self.uid,
                           "features": self.features, "rollback": self.rollback,
                           "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                           "hidden_features": sorted(self.hidden_features)}, indent=1)

    def save(self, path=None):
        path = path or self.path
        with open(path, "w") as fh:
            fh.write(self.to_json())
        self.path = path
        self.dirty = False

    @staticmethod
    def load(path) -> "Document":
        with open(path) as fh:
            d = json.load(fh)
        doc = Document()
        doc.name = d.get("name", "Part Studio 1")
        doc.uid = d.get("uid") or doc.uid
        doc.features = d["features"]
        doc.rollback = d.get("rollback")
        doc.part_overrides = d.get("overrides", {})
        doc.hidden_parts = set(d.get("hidden", []))
        doc.hidden_features = set(d.get("hidden_features", []))
        doc.path = path
        nums = [int("".join(c for c in f["id"] if c.isdigit()) or 0) for f in doc.features]
        doc._counter = max(nums + [0])
        doc.rebuild()
        return doc

    def visible_parts(self):
        return [p for p in self.state.parts.values() if p.id not in self.hidden_parts]

    def export(self, path, part_ids=None):
        parts = [p for p in self.state.parts.values() if part_ids is None or p.id in part_ids]
        if not parts:
            raise K.KernelError("Nothing to export")
        shapes = [p.shape for p in parts]
        if str(path).lower().endswith((".stl",)):
            K.export_stl(shapes, path)
        else:
            K.export_step(shapes, path)
