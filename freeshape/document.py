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
        # feature-list folders: id -> {"name": str, "open": bool}; a feature in one has f["folder"] = that id.
        # A folder groups a run of features that sit next to each other in the list (like Onshape).
        self.folders: dict = {}
        # configurations: named rows that set variables (by name) and suppress features (by id), like Onshape's
        # configuration table: {"rows": [{"name", "vars": {name: expr}, "suppress": [fid]}], "active": index}
        self.configs: dict = {"rows": [], "active": 0}
        self.derived_fallback: dict = {}          # derived feature id -> saved copy of its parts (from the file)
        self.plugin_fallback: dict = {}           # plugin feature id -> saved copy of what it made (from the file)
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
                           "hidden_features": sorted(self.hidden_features), "folders": self.folders,
                           "configs": self.configs})

    def _restore(self, snap: str):
        d = json.loads(snap)
        self.features = d["features"]
        self.rollback = d["rollback"]
        self.part_overrides = d["overrides"]
        self.hidden_parts = set(d["hidden"])
        self.hidden_features = set(d.get("hidden_features", []))
        self.folders = d.get("folders", {})
        self.configs = d.get("configs", {"rows": [], "active": 0})

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

    @staticmethod
    def _sketches_used(feat):
        """Ids of the sketches a feature uses (its regions, profiles, path, hole positions …)."""
        out = set()

        def walk(x):
            if isinstance(x, dict):
                for k, v in x.items():
                    if k == "sketch" and isinstance(v, str):
                        out.add(v)
                    else:
                        walk(v)
            elif isinstance(x, (list, tuple)):
                for v in x:
                    walk(v)
        if feat.get("type") != "sketch":
            walk(feat.get("params", {}))
        return out

    def delete_features(self, ids):
        self.checkpoint()
        idx = [i for i, f in enumerate(self.features) if f["id"] in ids]
        freed = set()
        for f in self.features:
            if f["id"] in ids:
                freed |= self._sketches_used(f)
        self.features = [f for f in self.features if f["id"] not in ids]
        # sketches were hidden when a feature used them: show them again once nothing uses them any more
        still = set()
        for f in self.features:
            still |= self._sketches_used(f)
        self.hidden_features -= (freed - still)
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

    def set_part_colors(self, colors: dict):
        """Give several parts new colours in one undo step. colors: part id -> (r, g, b) in 0..1."""
        if not colors:
            return
        self.checkpoint()
        for pid, c in colors.items():
            self.part_overrides.setdefault(pid, {})["color"] = [float(x) for x in c]
        self._apply_overrides(self.state)
        self.notify()

    def toggle_part_hidden(self, pid):
        self.hidden_parts ^= {pid}
        self.notify()

    def toggle_feature_hidden(self, fid):
        self.hidden_features ^= {fid}
        self.notify()

    def set_parts_hidden(self, pids, hidden: bool):
        """Hide (or show) several parts at once — one redraw for the whole selection."""
        pids = set(pids)
        self.hidden_parts = (self.hidden_parts | pids) if hidden else (self.hidden_parts - pids)
        self.notify()

    def set_features_hidden(self, fids, hidden: bool):
        """Hide (or show) several sketches / planes at once."""
        fids = set(fids)
        self.hidden_features = (self.hidden_features | fids) if hidden else (self.hidden_features - fids)
        self.notify()

    # ------------------------------------------------------------------ rebuild
    @staticmethod
    def _key(prefix_json: str) -> str:
        return hashlib.sha1(prefix_json.encode()).hexdigest()

    # ------------------------------------------------------------------ configurations
    def config_names(self):
        return [r["name"] for r in self.configs.get("rows", [])]

    def active_config(self):
        rows = self.configs.get("rows", [])
        if not rows:
            return None
        return rows[min(max(int(self.configs.get("active", 0)), 0), len(rows) - 1)]["name"]

    def _config_row(self, name=None):
        rows = self.configs.get("rows", [])
        if not rows:
            return None
        if name is None:
            return rows[min(max(int(self.configs.get("active", 0)), 0), len(rows) - 1)]
        return next((r for r in rows if r["name"] == name), None)

    def _configured(self, f, row):
        """Feature f as the configuration `row` has it: its variable value and whether it's suppressed."""
        if row is None:
            return f
        if f["id"] in row.get("suppress", []) and not f.get("suppressed"):
            f = dict(f, suppressed=True)
        if f.get("type") == "variable":
            name = str(f["params"].get("name", "")).strip().lstrip("#")
            val = row.get("vars", {}).get(name)
            if val not in (None, ""):
                f = dict(f, params=dict(f["params"], expr=str(val)))
        return f

    def set_configs(self, rows, active=0):
        """Replace the configuration table (one undo step) and rebuild in the active configuration."""
        self.checkpoint()
        self.configs = {"rows": [dict(r) for r in rows], "active": int(active) if rows else 0}
        self.rebuild()

    def set_active_config(self, name_or_index):
        rows = self.configs.get("rows", [])
        i = name_or_index if isinstance(name_or_index, int) else next(
            (k for k, r in enumerate(rows) if r["name"] == name_or_index), None)
        if i is None or not (0 <= i < len(rows)) or i == self.configs.get("active", 0):
            return
        self.configs["active"] = i
        self.dirty = True
        self.rebuild()

    def state_for(self, config=None):
        """The model in another configuration (for assemblies, drawings and derived parts), without changing
        this tab. None or unknown: the active one."""
        if not config or self._config_row(config) is None or config == self.active_config():
            return self.state
        st = self.compute(self.features, self.active_count(), None, config=config).copy()
        self._apply_overrides(st)
        return st

    def compute(self, features, upto=None, errors=None, config=None):
        """Replay features (with caching). Returns the resulting ModelState."""
        state = ModelState()
        from . import plugins as _P
        _P.CURRENT_DOC[0] = self.uid
        h = hashlib.sha1()
        n = len(features) if upto is None else upto
        new_cache = {}
        row = self._config_row(config)
        for f in features[:n]:
            f = self._configured(f, row)
            h.update(json.dumps({k: v for k, v in f.items() if k != "folder"}, sort_keys=True).encode())
            if f.get("type") == "derived":
                from . import features as _F
                h.update(json.dumps(_F.link_stamp(f)).encode())       # rebuild when the source parts change
                if f["id"] in self.derived_fallback:
                    f = dict(f, _fallback=self.derived_fallback[f["id"]])
            elif str(f.get("type", "")).startswith("plugin:"):
                from . import plugins as _P
                h.update(b"P" if f["type"] in _P.FEATURES else b"-")    # rebuild once the plugin is (un)installed
                f = dict(f, _raw_stamp=_P.stamp(f))
                if f["id"] in self.plugin_fallback:
                    fb = self.plugin_fallback[f["id"]]
                    f = dict(f, _fallback=fb)
                    if f["type"] not in _P.FEATURES:      # still work out its expressions without the plugin
                        from . import features as _F
                        _F.EXTRA_NUMERIC.setdefault(f["type"], set(fb.get("numeric", [])))
            key = h.hexdigest()
            if key in self._cache:
                state, err = self._cache[key]
            else:
                prev = state
                state = state.copy()
                err = None
                if not f.get("suppressed"):
                    before = dict(state.parts)
                    try:
                        run = RUNNERS.get(f["type"])
                        if run is None and str(f["type"]).startswith("plugin:"):
                            from .plugins import run_feature as run
                        if run is None:
                            raise K.KernelError(f"Unknown feature type '{f['type']}' (made by a newer FreeShape?)")
                        run(state, prepare(f, state))
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
                    if err is None:
                        # remembered so later features (feature patterns, mirror) can repeat what this one did
                        state.history[f["id"]] = (prev, state)
            new_cache[key] = (state, err)
            if errors is not None and err:
                errors[f["id"]] = err
        self._cache.update(new_cache)
        if len(self._cache) > 400:
            self._cache = new_cache
        return state

    # ------------------------------------------------------------------ folders
    def folder_members(self, folder_id):
        return [f["id"] for f in self.features if f.get("folder") == folder_id]

    def is_run(self, fids):
        """True if the features sit next to each other in the list (a folder holds one unbroken run)."""
        idx = sorted(i for i, f in enumerate(self.features) if f["id"] in set(fids))
        return bool(idx) and idx[-1] - idx[0] + 1 == len(idx)

    def make_folder(self, fids, name=None):
        """Put a run of features into a new folder (taking them out of any folder they were in).
        Returns the folder id, or raises ValueError if the features aren't next to each other."""
        fids = [f["id"] for f in self.features if f["id"] in set(fids)]
        if not fids:
            raise ValueError("Select the features to put in the folder")
        if not self.is_run(fids):
            raise ValueError("A folder holds features that are next to each other in the list. "
                             "Drag them together first, or select a run of features.")
        self.checkpoint()
        n = 1
        while f"D{n}" in self.folders:
            n += 1
        fo = f"D{n}"
        names = {v["name"] for v in self.folders.values()}
        k = 1
        while name is None or name in names:
            name, k = f"Folder {k}", k + 1
        self.folders[fo] = {"name": name, "open": True}
        for f in self.features:
            if f["id"] in fids:
                f["folder"] = fo
        self._prune_folders()
        self.notify()
        return fo

    def rename_folder(self, fo, name):
        if fo in self.folders and name:
            self.checkpoint()
            self.folders[fo]["name"] = name
            self.notify()

    def set_folder_open(self, fo, open_):
        """Expand / collapse a folder in the list (a view setting: no undo step)."""
        if fo in self.folders and self.folders[fo].get("open", True) != open_:
            self.folders[fo]["open"] = bool(open_)
            self.dirty = True
            self.notify()

    def ungroup_folder(self, fo):
        """Remove a folder, keeping its features where they are."""
        if fo not in self.folders:
            return
        self.checkpoint()
        for f in self.features:
            if f.get("folder") == fo:
                f.pop("folder", None)
        self.folders.pop(fo, None)
        self.notify()

    def remove_from_folder(self, fids):
        """Take features out of their folder. Ones at either end just leave it; ones from the middle move to
        just after the folder, so it stays one unbroken run. If that move would break the model (a later member
        uses a moved feature), nothing changes and ValueError is raised."""
        fids = set(fids)
        if not any(f["id"] in fids and f.get("folder") for f in self.features):
            return
        snap = self._snapshot()
        errs_before = set(self.errors)
        self.checkpoint()
        for fo in {f["folder"] for f in self.features if f["id"] in fids and f.get("folder")}:
            run = [i for i, f in enumerate(self.features) if f.get("folder") == fo]
            span = self.features[run[0]:run[-1] + 1]
            out = [f["id"] in fids for f in span]
            # leading and trailing features just leave the folder
            lo, hi = 0, len(span)
            while lo < hi and out[lo]:
                lo += 1
            while hi > lo and out[hi - 1]:
                hi -= 1
            for f in span:
                if f["id"] in fids:
                    f.pop("folder", None)
            middle = [f for f in span[lo:hi] if f["id"] in fids]
            if middle:
                inner = [f for f in span[lo:hi] if f["id"] not in fids]
                self.features[run[0]:run[-1] + 1] = span[:lo] + inner + middle + span[hi:]
        self._prune_folders()
        self.rebuild()
        if set(self.errors) - errs_before:
            self._undo.pop()
            self._restore(snap)
            self.rebuild()
            raise ValueError("Those features are used by later features in the folder, so they can't be moved "
                             "out of its middle. Take them out from the top or bottom of the folder instead.")

    def set_folder_suppressed(self, fo, value):
        self.checkpoint()
        for f in self.features:
            if f.get("folder") == fo:
                f["suppressed"] = bool(value)
        self.rebuild()

    def _prune_folders(self):
        """Drop empty folders and forget folders features point at that no longer exist."""
        used = {f.get("folder") for f in self.features}
        for fo in [k for k in self.folders if k not in used]:
            del self.folders[fo]
        for f in self.features:
            if f.get("folder") and f["folder"] not in self.folders:
                f.pop("folder", None)

    def rebuild(self):
        self._prune_folders()
        errors = {}
        self.state = self.compute(self.features, self.active_count(), errors).copy()
        self.errors = errors
        self._apply_overrides(self.state)
        self.notify()

    def preview(self, feat, index, extra=()):
        """Model state with `feat` inserted (or replacing its old self) at index. `extra` are not-yet-saved
        features (e.g. a plane made for a new sketch) that go in just before it."""
        feats = [f for f in self.features if f["id"] != feat["id"]]
        index = min(index, len(feats))
        feats = feats[:index] + list(extra) + [feat]
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
    def _derived_copies(self):
        """A copy of each derived feature's parts, saved in the file so it still opens without the source."""
        out = {}
        for f in self.features:
            if f.get("type") != "derived":
                continue
            items = []
            for pid, p in self.state.parts.items():
                if pid.startswith(f["id"] + ":"):
                    try:
                        import base64
                        items.append({"name": p.name, "color": list(p.color),
                                      "brep": base64.b64encode(K.shape_to_brep(p.shape)).decode()})
                    except Exception:
                        pass
            if items:
                out[f["id"]] = items
            elif f["id"] in self.derived_fallback:
                out[f["id"]] = self.derived_fallback[f["id"]]
        return out

    def _plugin_copies(self):
        from . import plugins
        out = plugins.saved_copies(self.features, self.uid)
        for f in self.features:                  # keep copies of features whose plugin isn't here, untouched
            if f["id"] not in out and f["id"] in self.plugin_fallback:
                out[f["id"]] = self.plugin_fallback[f["id"]]
        return out

    def to_json(self):
        return json.dumps({"format": "freeshape", "version": FILE_VERSION, "name": self.name, "uid": self.uid,
                           "derived_copies": self._derived_copies(),
                           "plugin_copies": self._plugin_copies(),
                           "features": self.features, "rollback": self.rollback,
                           "overrides": self.part_overrides, "hidden": sorted(self.hidden_parts),
                           "hidden_features": sorted(self.hidden_features), "folders": self.folders,
                           "configs": self.configs}, indent=1)

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
        doc.folders = d.get("folders", {})
        doc.configs = d.get("configs", {"rows": [], "active": 0})
        doc.derived_fallback = d.get("derived_copies", {})
        doc.plugin_fallback = d.get("plugin_copies", {})
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
        elif str(path).lower().endswith((".brep", ".brp")):
            K.export_brep(shapes, path)
        else:
            K.export_step(shapes, path)
