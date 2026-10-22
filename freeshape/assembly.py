"""Assemblies: instances of parts placed in space and connected by mates, like Onshape's Assembly tab.

* An **instance** is a part from a Part Studio tab (live-linked: edit the part and the assembly updates),
  a saved .fshape file, or an embedded STEP file, plus a 4x4 placement. A fixed instance never moves.
* A **mate** joins a mate connector (a frame: origin + Z + X axes) on one instance to one on another.
  Connectors come from what you click: a flat face, a round face (its axis, at the end nearest the click),
  a circular edge (its centre and axis), or a straight edge.
* The solver moves the free instances, by as little as possible, until every mate holds. While a part is
  dragged, the mates have priority and the drag follows as closely as they allow — so a revolute spins,
  a slider slides and a fastened part just comes along.
"""
from __future__ import annotations

import base64
import copy
import json
import os
import uuid

import numpy as np

from . import kernel as K

MATE_TYPES = {
    "fastened": ("Fastened", "No motion — the parts are locked together"),
    "revolute": ("Revolute", "Rotates about the connector's Z axis, like a hinge or an axle"),
    "slider": ("Slider", "Slides along the Z axis without rotating"),
    "cylindrical": ("Cylindrical", "Slides along and rotates about the Z axis, like a shaft in a bearing"),
    "planar": ("Planar", "Slides and rotates on a plane, like a block on a table"),
    "ball": ("Ball", "Rotates in every direction about a point"),
}
MATE_DOF = {"fastened": 0, "revolute": 1, "slider": 1, "cylindrical": 2, "planar": 3, "ball": 3}
FILE_FORMAT = "freeshape-assembly"
FILE_VERSION = 1
INSTANCE_COLORS = [(0.60, 0.74, 0.85), (0.72, 0.74, 0.78), (0.86, 0.66, 0.40), (0.56, 0.78, 0.56),
                   (0.85, 0.55, 0.55), (0.70, 0.60, 0.85), (0.50, 0.78, 0.78), (0.85, 0.78, 0.45)]


# =========================================================================== small rigid-body maths

def unit(v):
    v = np.asarray(v, float)
    n = np.linalg.norm(v)
    return v / n if n > 1e-15 else v


def perp(z):
    """A deterministic unit vector perpendicular to z (global X if possible, else global Y)."""
    z = unit(z)
    for a in (np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])):
        x = a - (a @ z) * z
        if np.linalg.norm(x) > 0.3:
            return unit(x)
    return unit(np.cross(z, [1.0, 0, 0]))


def rodrigues(w):
    """Rotation matrix for a rotation vector (axis * angle in radians)."""
    th = float(np.linalg.norm(w))
    if th < 1e-14:
        return np.eye(3)
    k = w / th
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * Kx + (1 - np.cos(th)) * (Kx @ Kx)


def rot_about(axis, angle_rad, point=(0, 0, 0)):
    """4x4 rotation about an axis through a point."""
    M = np.eye(4)
    R = rodrigues(unit(axis) * angle_rad)
    p = np.asarray(point, float)
    M[:3, :3] = R
    M[:3, 3] = p - R @ p
    return M


def translation(v):
    M = np.eye(4)
    M[:3, 3] = np.asarray(v, float)
    return M


def orthonormalize(M):
    """Remove numerical drift from a rigid transform."""
    M = np.array(M, float)
    U, _s, Vt = np.linalg.svd(M[:3, :3])
    R = U @ Vt
    if np.linalg.det(R) < 0:
        U[:, -1] *= -1
        R = U @ Vt
    M[:3, :3] = R
    M[3] = [0, 0, 0, 1]
    return M


def frame_matrix(fr):
    """Connector dict {o, z, x} -> 4x4 whose columns are x, y, z, origin."""
    z = unit(fr["z"])
    x = unit(np.asarray(fr["x"], float) - (np.asarray(fr["x"], float) @ z) * z)
    M = np.eye(4)
    M[:3, 0], M[:3, 1], M[:3, 2], M[:3, 3] = x, np.cross(z, x), z, fr["o"]
    return M


def transform_frame(M, fr):
    M = np.asarray(M, float)
    return {"o": (M[:3, :3] @ fr["o"] + M[:3, 3]), "z": M[:3, :3] @ fr["z"], "x": M[:3, :3] @ fr["x"]}


# =========================================================================== mate connectors from geometry

def _adjacent_faces(shape, edge):
    out = []
    for f in K.faces(shape):
        if any(e.IsSame(edge) for e in K.edges(f)):
            out.append(f)
    return out


def connector_frame(shape, kind, index, click=None):
    """A mate connector (in the part's own coordinates) for a face or edge of `shape`.
    `click` (a point near where the user clicked) chooses the end of a round face."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus, \
        GeomAbs_Line, GeomAbs_Circle
    centre = (np.add(*K.bbox(shape))) / 2
    if kind == "face":
        f = K.faces(shape)[index]
        info = K.face_info(f)
        ad = BRepAdaptor_Surface(f)
        st = ad.GetType()
        if st == GeomAbs_Plane:
            z = unit(info["n"])
            o = np.array(info["c"], float)
            # X along the longest straight edge, so square things line up squarely
            best, x = 0.0, None
            for e in K.edges(f):
                ei = K.edge_info(e)
                if "d" in ei and ei["len"] > best:
                    best, x = ei["len"], np.array(ei["d"], float)
            x = unit(x - (x @ z) * z) if x is not None else perp(z)
            return {"o": o, "z": z, "x": x, "what": "flat face"}
        if st in (GeomAbs_Cylinder, GeomAbs_Cone):
            if st == GeomAbs_Cylinder:
                p0, d, _r, zmin, zmax, _hole = K.cylinder_face_frame(f)
            else:
                ax = ad.Cone().Axis()
                p0, d = K._np(ax.Location()), unit(K._np(ax.Direction()))
                zs = [float((v - p0) @ d) for v in K._vertices_np(f)] or [0.0]
                poly = [np.asarray(q, float) for e in K.edges(f) for q in K.discretize_edge(e, 0.05)]
                zs += [float((q - p0) @ d) for q in poly]
                zmin, zmax = min(zs), max(zs)
            d = unit(d)
            zc = (zmin + zmax) / 2
            if click is not None:
                zk = float((np.asarray(click, float) - p0) @ d)
                cand = [(abs(zk - zmin), zmin, -1), (abs(zk - zc), zc, 0), (abs(zk - zmax), zmax, 1)]
                _dist, zz, end = min(cand)
            else:
                zz, end = zc, 0
            o = p0 + zz * d
            z = -d if end < 0 else d
            where = {-1: "end", 0: "middle", 1: "end"}[end]
            return {"o": o, "z": z, "x": perp(z), "what": f"round face ({where})"}
        if st == GeomAbs_Sphere:
            o = K._np(ad.Sphere().Location())
            return {"o": o, "z": np.array([0, 0, 1.0]), "x": np.array([1.0, 0, 0]), "what": "sphere centre"}
        if st == GeomAbs_Torus:
            ax = ad.Torus().Axis()
            z = unit(K._np(ax.Direction()))
            return {"o": K._np(ax.Location()), "z": z, "x": perp(z), "what": "torus axis"}
        # anything else: its centroid, normal at the middle
        from OCP.BRepLProp import BRepLProp_SLProps
        u = (ad.FirstUParameter() + ad.LastUParameter()) / 2
        v = (ad.FirstVParameter() + ad.LastVParameter()) / 2
        pr = BRepLProp_SLProps(ad, u, v, 1, 1e-7)
        z = unit(K._np(pr.Normal()))
        from OCP.TopAbs import TopAbs_REVERSED
        if f.Orientation() == TopAbs_REVERSED:
            z = -z
        return {"o": np.array(info["c"], float), "z": z, "x": perp(z), "what": "face"}

    e = K.edges(shape)[index]
    info = K.edge_info(e)
    ad = BRepAdaptor_Curve(e)
    ct = ad.GetType()
    if ct == GeomAbs_Circle:
        o = np.array(info["center"], float)
        z = unit(info["axis"])
        # point Z out of the material: along the outward normal of the flat face the circle sits on
        sign = None
        for f in _adjacent_faces(shape, e):
            fi = K.face_info(f)
            if "n" in fi and abs(float(np.dot(fi["n"], z))) > 0.99:
                sign = 1.0 if float(np.dot(fi["n"], z)) > 0 else -1.0
                break
        if sign is None:
            sign = 1.0 if float((o - centre) @ z) >= 0 else -1.0
        z = sign * z
        return {"o": o, "z": z, "x": perp(z), "what": "circular edge"}
    if ct == GeomAbs_Line:
        p0, p1 = np.array(info["p0"], float), np.array(info["p1"], float)
        z = unit(p1 - p0)
        return {"o": (p0 + p1) / 2, "z": z, "x": perp(z), "what": "straight edge"}
    t = (ad.FirstParameter() + ad.LastParameter()) / 2
    from OCP.gp import gp_Pnt, gp_Vec
    p, v = gp_Pnt(), gp_Vec()
    ad.D1(t, p, v)
    z = unit(np.array([v.X(), v.Y(), v.Z()]))
    return {"o": K._np(p), "z": z, "x": perp(z), "what": "edge"}


def frame_to_json(fr):
    return {"o": [float(a) for a in fr["o"]], "z": [float(a) for a in fr["z"]], "x": [float(a) for a in fr["x"]],
            "what": fr.get("what", "")}


def frame_from_json(d):
    return {"o": np.array(d["o"], float), "z": unit(d["z"]), "x": unit(d["x"]), "what": d.get("what", "")}


def _ref_signature(shape, kind, index):
    from .features import make_ref
    sub = K.faces(shape)[index] if kind == "face" else K.edges(shape)[index]
    ref = make_ref(None, sub, kind, shape)
    ref.pop("part", None)
    return json.loads(json.dumps(ref, default=lambda a: np.asarray(a).tolist()))


def _refind(shape, ref, kind):
    """Index of the face/edge of `shape` that best matches a stored signature (or None)."""
    from .features import _score
    lo, hi = K.bbox(shape)
    scale = max(float(np.linalg.norm(hi - lo)), 1e-6)
    subs = K.faces(shape) if kind == "face" else K.edges(shape)
    best, best_s = None, 1e9
    for i, s in enumerate(subs):
        info = K.face_info(s) if kind == "face" else K.edge_info(s)
        sc = _score(info, ref, scale, [lo, hi])
        if sc < best_s:
            best, best_s = i, sc
    return best if best_s <= 0.6 else None


# =========================================================================== mate residuals

def mate_residuals(m, FA, FB, L):
    """Residual vector of one mate given world connector frames A and B. Zero when the mate holds.
    Default alignment is Z axes opposed (faces touching / pin seated); `flip` aligns them instead."""
    oA, zA, xA = FA["o"], FA["z"], FA["x"]
    oB, zB, xB = FB["o"], FB["z"], FB["x"]
    t = m["type"]
    s = 1.0 if m.get("flip") else -1.0
    d = float(m.get("offset", 0.0) or 0.0)
    ang = np.radians(float(m.get("angle", 0.0) or 0.0))
    yA = np.cross(zA, xA)
    xt = np.cos(ang) * xA + np.sin(ang) * yA
    out = []
    if t in ("fastened", "revolute", "ball"):
        out.append(oB - (oA + d * zA))
    if t != "ball":
        out.append((zB - s * zA) * L)
    if t in ("fastened", "slider"):
        out.append((xB - xt) * L)
    if t in ("slider", "cylindrical"):
        dv = oB - oA
        out.append(dv - (dv @ zA) * zA)
    if t == "planar":
        out.append(np.array([(oB - oA) @ zA - d]))
    return np.concatenate(out) if out else np.zeros(0)


def snap_transform(m, FA, FB):
    """The rigid motion that carries connector B exactly onto its mated position against A
    (a configuration that satisfies every mate type)."""
    s = 1.0 if m.get("flip") else -1.0
    d = float(m.get("offset", 0.0) or 0.0)
    ang = np.radians(float(m.get("angle", 0.0) or 0.0))
    zA, xA = unit(FA["z"]), unit(FA["x"])
    yA = np.cross(zA, xA)
    xt = np.cos(ang) * xA + np.sin(ang) * yA
    zt = s * zA
    target = frame_matrix({"o": FA["o"] + d * zA, "z": zt, "x": xt})
    if m["type"] in ("slider", "cylindrical", "planar"):
        # keep B where it is along the free directions: only fix what the mate fixes
        dv = FB["o"] - FA["o"]
        if m["type"] == "planar":
            o = FB["o"] - ((dv @ zA) - d) * zA
        else:
            o = FA["o"] + (dv @ zA) * zA
        target[:3, 3] = o
    if m["type"] in ("revolute", "cylindrical", "planar", "ball"):
        # keep B's current spin about the axis instead of forcing X onto A's X
        xb = np.asarray(FB["x"], float)
        if m["type"] == "ball":
            return translation(FA["o"] + d * zA - FB["o"])
        xb = xb - (xb @ zt) * zt
        if np.linalg.norm(xb) > 1e-6:
            xb = unit(xb)
            M = frame_matrix({"o": target[:3, 3], "z": zt, "x": xb})
            # the frame's own z can't change sign by projecting, so this is a valid target
            target = M
    return target @ np.linalg.inv(frame_matrix(FB))


# =========================================================================== the document

class AssemblyDoc:
    kind = "assembly"

    def __init__(self):
        self.uid = uuid.uuid4().hex
        self.name = "Assembly 1"
        self.instances: list[dict] = []
        self.mates: list[dict] = []
        self.blobs: dict[str, str] = {}          # embedded STEP files (base64), keyed by content hash
        self.path = None
        self.dirty = False
        self.listeners = []
        self.resolver = None                     # callable(source) -> features.Part (set by the main window)
        self.parts: dict[str, object] = {}       # instance id -> Part in its own coordinates
        self._fallback: dict[str, object] = {}   # instance id -> shape stored in the file
        self._step_cache: dict[str, list] = {}
        self.errors: dict[str, str] = {}         # instance / mate id -> message
        self._conflicts: set = set()             # mates left out because they can't hold with the others
        self.dof = 0
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._counter = 0
        # Part-Studio-shaped attributes some generic window code looks at
        from .features import ModelState
        self.features = []
        self.hidden_parts = set()
        self.hidden_features = set()
        self.state = ModelState()

    # ------------------------------------------------------------------ ids
    def new_id(self, prefix):
        self._counter += 1
        used = {i["id"] for i in self.instances} | {m["id"] for m in self.mates}
        while f"{prefix}{self._counter}" in used:
            self._counter += 1
        return f"{prefix}{self._counter}"

    def instance(self, iid):
        return next((i for i in self.instances if i["id"] == iid), None)

    def mate(self, mid):
        return next((m for m in self.mates if m["id"] == mid), None)

    def is_empty(self):
        return not self.instances

    # ------------------------------------------------------------------ undo / redo
    def _snapshot(self):
        return json.dumps({"instances": self.instances, "mates": self.mates, "name": self.name})

    def _restore(self, snap):
        d = json.loads(snap)
        self.instances, self.mates = d["instances"], d["mates"]

    def checkpoint(self):
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
            self.refresh(solve=False)

    def redo(self):
        if self._redo:
            self._undo.append(self._snapshot())
            self._restore(self._redo.pop())
            self.dirty = True
            self.refresh(solve=False)

    def notify(self):
        for cb in list(self.listeners):
            cb()

    # ------------------------------------------------------------------ parts
    def _step_parts(self, src):
        key = src["blob"]
        if key not in self._step_cache:
            import tempfile
            data = base64.b64decode(self.blobs[key])
            fd, path = tempfile.mkstemp(suffix=".step")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            try:
                self._step_cache[key] = K.import_step_with_colors(path)
            finally:
                os.remove(path)
        return self._step_cache[key]

    def _resolve(self, inst):
        from .features import Part
        src = inst["source"]
        if src.get("kind") == "step":
            sols = self._step_parts(src)
            i = int(src.get("index", 0))
            if i >= len(sols):
                raise K.KernelError("The part is no longer in the STEP file")
            sol, nm, col, fcols = sols[i]
            from .features import DEFAULT_COLOR
            base = os.path.splitext(src.get("filename") or "Part")[0]
            return Part(inst["id"], nm or base, sol, col or DEFAULT_COLOR, fcols or None)
        if self.resolver is None:
            raise K.KernelError("Part Studio not open")
        return self.resolver(src)

    def refresh(self, solve=True, notify=True):
        """Re-read every instance's part (picking up Part Studio edits), update mates, re-solve."""
        from .features import Part
        self.errors = {}
        changed = set()
        for inst in self.instances:
            old = self.parts.get(inst["id"])
            try:
                p = self._resolve(inst)
            except Exception as e:
                if old is not None and inst["source"].get("kind") != "step":
                    # the Part Studio was closed or the part removed: keep showing the last version
                    self.parts[inst["id"]] = old
                    self.errors[inst["id"]] = (
                        "Linked Part Studio not found — showing the copy saved in this assembly"
                        if inst["id"] in self._fallback else f"{e} — showing the last version of the part")
                    continue
                shp = self._fallback.get(inst["id"])
                if shp is None and inst.get("_brep"):
                    try:
                        shp = K.shape_from_brep(base64.b64decode(inst["_brep"]))
                        self._fallback[inst["id"]] = shp
                    except Exception:
                        shp = None
                if shp is None:
                    self.errors[inst["id"]] = f"Part not found: {e}"
                    self.parts.pop(inst["id"], None)
                    continue
                p = Part(inst["id"], inst.get("part_name") or inst["name"], shp,
                         tuple(inst.get("color") or INSTANCE_COLORS[0]), None)
                self.errors[inst["id"]] = "Linked Part Studio not found — showing the copy saved in this assembly"
            if old is None or not old.shape.IsSame(p.shape):
                changed.add(inst["id"])
            self.parts[inst["id"]] = p
            inst["part_name"] = p.name
        for iid in list(self.parts):
            if self.instance(iid) is None:
                self.parts.pop(iid)
        # re-find mate connectors on parts whose geometry changed
        for m in self.mates:
            for side in ("a", "b"):
                c = m[side]
                if c["inst"] in changed and c.get("ref") and c["inst"] in self.parts:
                    shp = self.parts[c["inst"]].shape
                    idx = c.get("index")
                    subs = K.faces(shp) if c["kind"] == "face" else K.edges(shp)
                    ok_idx = None
                    if idx is not None and idx < len(subs):
                        if _refind(shp, c["ref"], c["kind"]) == idx:
                            ok_idx = idx
                    if ok_idx is None:
                        ok_idx = _refind(shp, c["ref"], c["kind"])
                    if ok_idx is None:
                        self.errors[m["id"]] = "Lost its connector after the part changed — edit the mate to re-pick"
                        continue
                    try:
                        fr = connector_frame(shp, c["kind"], ok_idx, c.get("click"))
                        c["frame"] = frame_to_json(fr)
                        c["index"] = ok_idx
                    except Exception:
                        pass
        if solve:
            self.solve()
        else:
            self._update_status()
        if notify:
            self.notify()

    # ------------------------------------------------------------------ geometry access
    def T(self, iid):
        inst = self.instance(iid)
        return np.array(inst["T"], float) if inst else np.eye(4)

    def world_shape(self, iid):
        p = self.parts.get(iid)
        return None if p is None else K.located(p.shape, self.T(iid))

    def visible_instances(self):
        return [i for i in self.instances if not i.get("hidden") and i["id"] in self.parts]

    def _source_key(self, src):
        if src.get("kind") == "step":
            return ("step", src.get("blob"), src.get("index", 0))
        return ("studio", src.get("uid"), src.get("part"))

    def instance_color(self, inst):
        """The instance's own colour, else its part's — except plain default-grey parts, which get a colour
        each (copies of the same part share one) so the assembly is easy to read."""
        from .features import DEFAULT_COLOR
        p = self.parts.get(inst["id"])
        if inst.get("color"):
            return tuple(inst["color"])
        if p is None:
            return INSTANCE_COLORS[0]
        if max(abs(a - b) for a, b in zip(p.color, DEFAULT_COLOR)) > 1e-3:
            return p.color
        keys = []
        for i in self.instances:
            q = self.parts.get(i["id"])
            if q is not None and not i.get("color") and \
                    max(abs(a - b) for a, b in zip(q.color, DEFAULT_COLOR)) <= 1e-3:
                k = self._source_key(i["source"])
                if k not in keys:
                    keys.append(k)
        k = self._source_key(inst["source"])
        return INSTANCE_COLORS[keys.index(k) % len(INSTANCE_COLORS)] if k in keys else INSTANCE_COLORS[0]

    def bounds(self):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for inst in self.instances:
            s = self.world_shape(inst["id"])
            if s is not None:
                a, b = K.bbox(s)
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
        if not np.all(np.isfinite(lo)):
            return np.zeros(3), np.zeros(3)
        return lo, hi

    def length_scale(self):
        lo, hi = self.bounds()
        return max(float(np.linalg.norm(hi - lo)) / 4, 5.0)

    # ------------------------------------------------------------------ editing
    def _unique_name(self, base):
        names = {i["name"] for i in self.instances}
        n = 1
        while f"{base} <{n}>" in names:
            n += 1
        return f"{base} <{n}>"

    def add_step_blob(self, data_b64):
        import hashlib
        key = hashlib.sha1(data_b64.encode()).hexdigest()[:16]
        self.blobs[key] = data_b64
        return key

    def insert(self, source, T=None, name=None, checkpoint=True):
        """Add an instance. Returns its id. Without a placement, it goes beside what's already there."""
        if checkpoint:
            self.checkpoint()
        inst = {"id": self.new_id("I"), "name": "", "source": copy.deepcopy(source), "T": np.eye(4).tolist(),
                "fixed": False, "hidden": False}
        self.instances.append(inst)
        try:
            p = self._resolve(inst)
        except Exception as e:
            self.instances.remove(inst)
            raise K.KernelError(f"Couldn't insert the part: {e}")
        self.parts[inst["id"]] = p
        inst["part_name"] = p.name
        inst["name"] = self._unique_name(name or p.name)
        if len(self.instances) == 1:
            inst["fixed"] = True                    # like Onshape: the first instance is fixed in place
        elif T is None:
            others = [i for i in self.instances if i is not inst and i["id"] in self.parts]
            lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
            for o in others:
                a, b = K.bbox(self.world_shape(o["id"]))
                lo, hi = np.minimum(lo, a), np.maximum(hi, b)
            plo, phi = K.bbox(p.shape)
            if np.all(np.isfinite(lo)):
                gap = max(0.15 * float(np.linalg.norm(phi - plo)), 5.0)
                v = np.array([hi[0] + gap - plo[0], (lo[1] + hi[1]) / 2 - (plo[1] + phi[1]) / 2,
                              lo[2] - plo[2]])
                T = translation(v)
        if T is not None:
            inst["T"] = np.asarray(T, float).tolist()
        self._update_status()
        self.notify()
        return inst["id"]

    def delete_instances(self, ids):
        self.checkpoint()
        ids = set(ids)
        self.instances = [i for i in self.instances if i["id"] not in ids]
        self.mates = [m for m in self.mates if m["a"]["inst"] not in ids and m["b"]["inst"] not in ids]
        for i in ids:
            self.parts.pop(i, None)
        self.solve()
        self.notify()

    def delete_mates(self, ids):
        self.checkpoint()
        self.mates = [m for m in self.mates if m["id"] not in set(ids)]
        self._update_status()
        self.notify()

    def set_fixed(self, iid, value):
        self.checkpoint()
        self.instance(iid)["fixed"] = bool(value)
        self.solve()
        self.notify()

    def set_hidden(self, iid, value):
        self.instance(iid)["hidden"] = bool(value)
        self.notify()

    def rename(self, obj_id, name):
        self.checkpoint()
        o = self.instance(obj_id) or self.mate(obj_id)
        if o is not None:
            o["name"] = name
        self.notify()

    def set_color(self, iid, color):
        self.checkpoint()
        self.instance(iid)["color"] = list(color) if color is not None else None
        self.notify()

    def set_suppressed(self, mid, value):
        self.checkpoint()
        self.mate(mid)["suppressed"] = bool(value)
        self.solve()
        self.notify()

    def move_instance(self, iid, M_world, checkpoint=True, solve=True):
        """Apply a world-space rigid motion to an instance (a fixed one too — this is the explicit move)."""
        if checkpoint:
            self.checkpoint()
        inst = self.instance(iid)
        inst["T"] = orthonormalize(np.asarray(M_world, float) @ np.array(inst["T"], float)).tolist()
        if solve:
            self.solve(keep=iid)
        self.notify()

    def connector(self, iid, kind, index, click_world=None):
        """Make a connector dict for a picked face/edge of an instance (stored in part coordinates)."""
        p = self.parts[iid]
        T = self.T(iid)
        click = None
        if click_world is not None:
            Ti = np.linalg.inv(T)
            click = (Ti[:3, :3] @ np.asarray(click_world, float) + Ti[:3, 3]).tolist()
        fr = connector_frame(p.shape, kind, index, click)
        return {"inst": iid, "kind": kind, "index": int(index), "click": click,
                "ref": _ref_signature(p.shape, kind, index), "frame": frame_to_json(fr)}

    def world_frame(self, c):
        return transform_frame(self.T(c["inst"]), frame_from_json(c["frame"]))

    def make_mate(self, mtype):
        n = 1
        label = MATE_TYPES[mtype][0]
        names = {m["name"] for m in self.mates}
        while f"{label} {n}" in names:
            n += 1
        return {"id": self.new_id("M"), "type": mtype, "name": f"{label} {n}", "a": None, "b": None,
                "flip": False, "offset": 0.0, "angle": 0.0, "suppressed": False}

    def add_mate(self, m, checkpoint=True):
        if checkpoint:
            self.checkpoint()
        self.mates.append(m)
        self.place_for_mate(m)
        self.solve()
        self.notify()

    def replace_mate(self, m):
        self.checkpoint()
        for i, old in enumerate(self.mates):
            if old["id"] == m["id"]:
                self.mates[i] = m
        self.place_for_mate(m)
        self.solve()
        self.notify()

    # ------------------------------------------------------------------ solving
    def _groups(self, exclude=None):
        """Connected groups of instances through (unsuppressed) mates, ignoring mate `exclude`."""
        parent = {i["id"]: i["id"] for i in self.instances}

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        for m in self.mates:
            if m is exclude or m.get("suppressed") or not m.get("a") or not m.get("b"):
                continue
            a, b = m["a"]["inst"], m["b"]["inst"]
            if a in parent and b in parent:
                parent[find(a)] = find(b)
        groups = {}
        for iid in parent:
            groups.setdefault(find(iid), set()).add(iid)
        return {iid: groups[find(iid)] for iid in parent}

    def place_for_mate(self, m):
        """Before solving, move one side's whole group straight into the mated position. Starting from an
        exactly aligned pose avoids the solver getting stuck on a part that needs flipping over."""
        if not m.get("a") or not m.get("b") or m.get("suppressed"):
            return
        a, b = m["a"]["inst"], m["b"]["inst"]
        if a not in self.parts or b not in self.parts or a == b:
            return
        groups = self._groups(exclude=m)
        ga, gb = groups[a], groups[b]
        if ga is gb or ga == gb:
            return                                  # already connected another way: let the solver sort it out
        fixed = {i["id"] for i in self.instances if i.get("fixed")}
        FA, FB = self.world_frame(m["a"]), self.world_frame(m["b"])
        if not (gb & fixed):
            M = snap_transform(m, FA, FB)
            move = gb
        elif not (ga & fixed):
            # move A's group onto B: swap roles (mirror the mate's definition)
            M = np.linalg.inv(snap_transform(m, FA, FB))
            move = ga
        else:
            return
        for iid in move:
            inst = self.instance(iid)
            inst["T"] = orthonormalize(M @ np.array(inst["T"], float)).tolist()

    def _active_mates(self):
        return [m for m in self.mates if not m.get("suppressed") and m.get("a") and m.get("b")
                and m["a"]["inst"] in self.parts and m["b"]["inst"] in self.parts
                and m["id"] not in self.errors]

    def _residuals(self, mates, Ts, frames, L):
        out = []
        for m in mates:
            FA = transform_frame(Ts[m["a"]["inst"]], frames[m["id"]][0])
            FB = transform_frame(Ts[m["b"]["inst"]], frames[m["id"]][1])
            out.append(mate_residuals(m, FA, FB, L))
        return np.concatenate(out) if out else np.zeros(0)

    def solve(self, drag=None, iters=40, keep=None):
        """Satisfy the mates by moving free instances as little as possible.
        drag: {"inst", "local" point, "target" world point, "P" 2x3 screen-plane projector} — the dragged part
        follows the target as far as the mates allow. keep: an instance that should not move if avoidable.
        Mates that can't hold together with the earlier ones are left out (and shown red), like Onshape."""
        for k in [k for k, v in self.errors.items() if v.startswith("Can't be solved")]:
            self.errors.pop(k)
        mates = self._active_mates()
        if drag is not None:
            self._solve_core([m for m in mates if m["id"] not in self._conflicts], drag, iters, keep)
            self._update_status()
            return True
        before = {i["id"]: list(i["T"]) for i in self.instances}
        bad = self._solve_core(mates, None, iters, keep)
        self._conflicts = set()
        if bad and len(mates) > 1:
            # find the culprits: add the mates one at a time, keeping each one that still solves
            for i in self.instances:
                i["T"] = before[i["id"]]
            good = []
            for m in mates:
                snap = {i["id"]: list(i["T"]) for i in self.instances}
                if self._solve_core(good + [m], None, iters, keep):
                    for i in self.instances:
                        i["T"] = snap[i["id"]]
                    self._conflicts.add(m["id"])
                else:
                    good.append(m)
        self._update_status()
        return not self._conflicts and not bad

    def _solve_core(self, mates, drag, iters, keep):
        """One least-squares solve over the given mates. Returns the ids of mates left unsatisfied."""
        fixed = {i["id"] for i in self.instances if i.get("fixed")}
        involved = set()
        for m in mates:
            involved |= {m["a"]["inst"], m["b"]["inst"]}
        if drag is not None:
            involved.add(drag["inst"])
        free = [i["id"] for i in self.instances if i["id"] in involved and i["id"] not in fixed
                and i["id"] in self.parts]
        L = self.length_scale()
        frames = {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"])) for m in mates}
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        if not free:
            return self._failing(mates, Ts, frames, L)
        pivots = {}
        for iid in free:
            a, b = K.bbox(self.world_shape(iid))
            pivots[iid] = (a + b) / 2
        n = 6 * len(free)
        # rotations are measured in mm of arc at the part's size, and cost a little more than sliding
        # (while dragging, much more: parts should slide with the mouse and only turn when a mate makes them)
        rot_cost = 1.5 if drag is None else 8.0
        # a part being kept still (e.g. one just moved by hand) is expensive to move
        cost = np.ones(n)
        for k, iid in enumerate(free):
            cost[6 * k:6 * k + 3] = rot_cost
            if keep is not None and iid == keep:
                cost[6 * k:6 * k + 6] *= 50.0

        def apply(Ts0, x):
            out = dict(Ts0)
            for k, iid in enumerate(free):
                w = x[6 * k:6 * k + 3] / L
                v = x[6 * k + 3:6 * k + 6]
                c = pivots[iid]
                R = rodrigues(w)
                D = np.eye(4)
                D[:3, :3] = R
                D[:3, 3] = c - R @ c + v
                out[iid] = D @ Ts0[iid]
            return out

        def drag_res(Ts_):
            if drag is None:
                return np.zeros(0)
            T = Ts_[drag["inst"]]
            if "points" in drag or "dirs" in drag:
                # the move / rotate triad: points to follow, and directions (lengths in mm) to turn towards
                out = [T[:3, :3] @ lp + T[:3, 3] - tp for lp, tp in drag.get("points", [])]
                out += [T[:3, :3] @ lv - tv for lv, tv in drag.get("dirs", [])]
                return np.concatenate(out)
            p = T[:3, :3] @ drag["local"] + T[:3, 3]
            return drag["P"] @ (p - drag["target"])

        def jac(fun, Ts0, r0):
            J = np.zeros((len(r0), n))
            h = 1e-6 * L
            for j in range(n):
                dx = np.zeros(n)
                dx[j] = h
                J[:, j] = (fun(apply(Ts0, dx)) - r0) / h
            return J

        mfun = lambda Ts_: self._residuals(mates, Ts_, frames, L)
        tol = 1e-9 * L
        S = np.diag(1.0 / cost)                     # variable scaling: min-norm in scaled space

        def clamp(dx):
            # keep each step modest so big moves don't fling parts around
            mx = float(np.max(np.abs(dx))) if len(dx) else 0.0
            return dx * (0.5 * L / mx) if mx > 0.5 * L else dx

        def correct(T0, steps):
            """Gauss-Newton on the mates alone (minimum-norm steps, so nothing moves needlessly)."""
            cur_ = T0
            for _ in range(steps):
                rm = mfun(cur_)
                em = float(np.linalg.norm(rm)) if len(rm) else 0.0
                if em < tol:
                    break
                Jm = jac(mfun, cur_, rm) @ S
                dx = clamp(S @ (-np.linalg.pinv(Jm, rcond=1e-6) @ rm))
                alpha, ok = 1.0, False
                for _ls in range(10):
                    trial = apply(cur_, alpha * dx)
                    r2 = mfun(trial)
                    if float(np.linalg.norm(r2)) < em:
                        cur_, ok = trial, True
                        break
                    alpha *= 0.5
                if not ok:
                    break
            return cur_

        cur = correct(Ts, iters if drag is None else 6)
        if drag is None and len(mates) and n <= 48:
            # stuck (e.g. a linkage starting out dead straight, where the first-order motion can't close a gap):
            # nudge the free parts a little and try again, keeping the best result
            def err(T_):
                r = mfun(T_)
                return float(np.linalg.norm(r)) if len(r) else 0.0
            best, best_e = cur, err(cur)
            rng = np.random.default_rng(12345)
            for attempt in range(6):
                if best_e < tol * 10:
                    break
                x = np.zeros(n)
                for k in range(len(free)):
                    x[6 * k:6 * k + 3] = rng.normal(0, 0.25 + 0.1 * attempt, 3) * L
                    x[6 * k + 3:6 * k + 6] = rng.normal(0, 0.05, 3) * L
                trial = correct(apply(best, x), min(iters, 25))
                e = err(trial)
                if e < best_e:
                    best, best_e = trial, e
            cur = best
        if drag is not None:
            for _it in range(iters):
                rd = drag_res(cur)
                ed = float(np.linalg.norm(rd))
                if ed < 1e-6 * L:
                    break
                rm = mfun(cur)
                if len(rm):
                    Jm = jac(mfun, cur, rm) @ S
                    N = np.eye(n) - np.linalg.pinv(Jm, rcond=1e-6) @ Jm
                else:
                    N = np.eye(n)
                Jd = jac(drag_res, cur, rd) @ S
                dx = clamp(S @ (N @ (np.linalg.pinv(Jd @ N, rcond=1e-6) @ (-rd))))
                alpha, ok = 1.0, False
                for _ls in range(6):
                    # step within what the mates allow, then pull back onto the mates exactly
                    trial = correct(apply(cur, alpha * dx), 4)
                    if float(np.linalg.norm(drag_res(trial))) < ed:
                        cur, ok = trial, True
                        break
                    alpha *= 0.5
                if not ok:
                    break
        for iid in free:
            self.instance(iid)["T"] = orthonormalize(cur[iid]).tolist()
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        return self._failing(mates, Ts, frames, L)

    def _failing(self, mates, Ts, frames, L):
        out = set()
        for m in mates:
            r = self._residuals([m], Ts, frames, L)
            if len(r) and float(np.linalg.norm(r)) > 1e-4 * max(L, 1):
                out.add(m["id"])
        return out

    def _update_status(self, Ts=None, mates=None, frames=None, L=None):
        """Per-mate 'holds or not' and the remaining degrees of freedom."""
        for k in [k for k, v in self.errors.items() if v.startswith("Can't be solved")]:
            self.errors.pop(k)
        mates = self._active_mates() if mates is None else mates
        L = self.length_scale() if L is None else L
        frames = frames or {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"]))
                            for m in mates}
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        for m in mates:
            r = self._residuals([m], Ts, frames, L)
            if len(r) and float(np.linalg.norm(r)) > 1e-4 * max(L, 1):
                self.errors[m["id"]] = "Can't be solved together with the other mates (shown red)"
        # degrees of freedom of the free instances
        fixed = {i["id"] for i in self.instances if i.get("fixed")}
        free = [i["id"] for i in self.instances if i["id"] not in fixed and i["id"] in self.parts]
        n = 6 * len(free)
        if n == 0:
            self.dof = 0
            return
        ok = [m for m in mates if m["id"] not in self.errors]
        if not ok:
            self.dof = n
            return
        idx = {iid: k for k, iid in enumerate(free)}
        pivots = {}
        for iid in free:
            s = self.world_shape(iid)
            a, b = K.bbox(s) if s is not None else (np.zeros(3), np.zeros(3))
            pivots[iid] = (a + b) / 2
        r0 = self._residuals(ok, Ts, frames, L)
        J = np.zeros((len(r0), n))
        h = 1e-6 * L
        for iid, k in idx.items():
            for j in range(6):
                T2 = dict(Ts)
                if j < 3:
                    w = np.zeros(3)
                    w[j] = h / L
                    R = rodrigues(w)
                    D = np.eye(4)
                    D[:3, :3] = R
                    D[:3, 3] = pivots[iid] - R @ pivots[iid]
                else:
                    D = np.eye(4)
                    D[j - 3, 3] = h
                T2[iid] = D @ Ts[iid]
                J[:, 6 * k + j] = (self._residuals(ok, T2, frames, L) - r0) / h
        sv = np.linalg.svd(J, compute_uv=False) if J.size else np.zeros(0)
        rank = int(np.sum(sv > 1e-6 * (sv[0] if len(sv) else 1)))
        self.dof = n - rank

    def instance_dof(self, iid):
        """Degrees of freedom left for one instance, holding every other instance still."""
        inst = self.instance(iid)
        if inst is None or inst.get("fixed"):
            return 0
        mates = [m for m in self._active_mates() if iid in (m["a"]["inst"], m["b"]["inst"])
                 and m["id"] not in self.errors]
        if not mates:
            return 6
        L = self.length_scale()
        frames = {m["id"]: (frame_from_json(m["a"]["frame"]), frame_from_json(m["b"]["frame"])) for m in mates}
        Ts = {i["id"]: np.array(i["T"], float) for i in self.instances}
        a, b = K.bbox(self.world_shape(iid))
        c = (a + b) / 2
        r0 = self._residuals(mates, Ts, frames, L)
        J = np.zeros((len(r0), 6))
        h = 1e-6 * L
        for j in range(6):
            T2 = dict(Ts)
            if j < 3:
                w = np.zeros(3)
                w[j] = h / L
                R = rodrigues(w)
                D = np.eye(4)
                D[:3, :3] = R
                D[:3, 3] = c - R @ c
            else:
                D = np.eye(4)
                D[j - 3, 3] = h
            T2[iid] = D @ Ts[iid]
            J[:, j] = (self._residuals(mates, T2, frames, L) - r0) / h
        sv = np.linalg.svd(J, compute_uv=False)
        return 6 - int(np.sum(sv > 1e-6 * (sv[0] if len(sv) else 1)))

    # ------------------------------------------------------------------ analysis
    def interferences(self, progress=None):
        """Pairs of visible instances whose solids overlap: [(iid_a, iid_b, volume, overlap shape)]."""
        vis = self.visible_instances()
        shapes = {i["id"]: self.world_shape(i["id"]) for i in vis}
        boxes = {k: K.bbox(s) for k, s in shapes.items()}
        out = []
        ids = [i["id"] for i in vis]
        pairs = [(a, b) for k, a in enumerate(ids) for b in ids[k + 1:]]
        for n, (a, b) in enumerate(pairs):
            if progress:
                progress(n, len(pairs))
            (la, ha), (lb, hb) = boxes[a], boxes[b]
            if np.any(ha < lb - 1e-6) or np.any(hb < la - 1e-6):
                continue
            try:
                ov = K.common(shapes[a], shapes[b])
                v = K.volume(ov) if not ov.IsNull() else 0.0
            except Exception:
                continue
            if v > 1e-3:
                out.append((a, b, v, ov))
        return out

    def bom(self):
        """Bill of materials: one row per distinct part, with how many times it is used."""
        rows = {}
        for inst in self.instances:
            src = inst["source"]
            key = self._source_key(src)
            if src.get("kind") == "step":
                where = src.get("filename", "STEP")
            else:
                where = src.get("studio_name") or os.path.basename(src.get("path") or "") or "Part Studio"
            p = self.parts.get(inst["id"])
            r = rows.setdefault(key, {"name": inst.get("part_name") or inst["name"], "source": where, "qty": 0,
                                      "volume": None, "instances": []})
            r["qty"] += 1
            r["instances"].append(inst["name"])
            if r["volume"] is None and p is not None:
                try:
                    r["volume"] = K.volume(p.shape)
                except Exception:
                    pass
        return list(rows.values())

    def export(self, path, ids=None):
        insts = [i for i in self.instances if i["id"] in self.parts and (ids is None or i["id"] in ids)
                 and not i.get("hidden")]
        if not insts:
            raise K.KernelError("Nothing to export")
        if str(path).lower().endswith(".stl"):
            K.export_stl([self.world_shape(i["id"]) for i in insts], path)
        else:
            K.export_step_named([(self.parts[i["id"]].shape, np.array(i["T"], float), i["name"],
                                  self.instance_color(i)) for i in insts], path, self.name)

    # ------------------------------------------------------------------ files
    def to_json(self, embed=True):
        insts = copy.deepcopy(self.instances)
        if embed:
            # a copy of each linked part goes in the file, so it still opens if the Part Studio is missing
            done = {}
            for inst in insts:
                if inst["source"].get("kind") == "step":
                    inst.pop("_brep", None)
                    continue
                p = self.parts.get(inst["id"])
                if p is None:
                    continue
                key = id(p.shape)
                if key not in done:
                    try:
                        done[key] = base64.b64encode(K.shape_to_brep(p.shape)).decode()
                    except Exception:
                        done[key] = None
                if done[key]:
                    inst["_brep"] = done[key]
                if self.path:
                    sp = inst["source"].get("path")
                    if sp:
                        try:
                            inst["source"]["relpath"] = os.path.relpath(sp, os.path.dirname(os.path.abspath(self.path)))
                        except ValueError:
                            pass
        used = {i["source"]["blob"] for i in self.instances if i["source"].get("kind") == "step"}
        return json.dumps({"format": FILE_FORMAT, "version": FILE_VERSION, "name": self.name, "uid": self.uid,
                           "instances": insts, "mates": self.mates,
                           "blobs": {k: v for k, v in self.blobs.items() if k in used}}, indent=1)

    def save(self, path=None):
        self.path = path or self.path
        txt = self.to_json()
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(txt)
        os.replace(tmp, self.path)
        self.dirty = False

    @staticmethod
    def load(path, resolver=None):
        with open(path) as fh:
            d = json.load(fh)
        if d.get("format") != FILE_FORMAT:
            raise K.KernelError("Not a FreeShape assembly")
        a = AssemblyDoc()
        a.name = d.get("name", "Assembly 1")
        a.uid = d.get("uid") or a.uid
        a.instances = d.get("instances", [])
        a.mates = d.get("mates", [])
        a.blobs = d.get("blobs", {})
        a.path = path
        base = os.path.dirname(os.path.abspath(path))
        for inst in a.instances:
            src = inst["source"]
            if src.get("relpath") and not (src.get("path") and os.path.exists(src["path"])):
                cand = os.path.normpath(os.path.join(base, src["relpath"]))
                if os.path.exists(cand):
                    src["path"] = cand
        nums = [int("".join(c for c in x["id"] if c.isdigit()) or 0) for x in a.instances + a.mates]
        a._counter = max(nums + [0])
        a.resolver = resolver
        a.refresh(solve=False, notify=False)
        return a
