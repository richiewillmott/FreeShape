"""2D sketch model and geometric constraint solver.

Geometry:
  points    id -> [x, y]
  entities  id -> line   {p1, p2}
                  circle {c, r}
                  arc    {c, p1, p2}   counter-clockwise from p1 to p2
                  point  {p}
  Every entity may carry "construction": True (ignored when building regions).

Constraints are dicts with a "type" and entity/point references. Dimensions are
constraints with a "value". The solver is a damped Gauss-Newton on a
minimum-norm step, so under-constrained geometry moves as little as possible
(which is what makes dragging and editing dimensions feel natural).
"""
from __future__ import annotations

import copy
import itertools
import math

import numpy as np

ORIGIN = "origin"

# Constraint types that carry an editable value (i.e. dimensions)
DIMENSIONS = {"distance", "hdistance", "vdistance", "radius", "diameter", "angle"}

CONSTRAINT_LABELS = {
    "coincident": "Coincident", "point_on": "Coincident", "horizontal": "Horizontal",
    "vertical": "Vertical", "parallel": "Parallel", "perpendicular": "Perpendicular",
    "equal": "Equal", "tangent": "Tangent", "concentric": "Concentric", "midpoint": "Midpoint",
    "fix": "Fix", "symmetric": "Symmetric", "distance": "Distance", "hdistance": "Horizontal distance",
    "vdistance": "Vertical distance", "radius": "Radius", "diameter": "Diameter", "angle": "Angle",
}


class Sketch:
    def __init__(self):
        self.points: dict[str, list] = {ORIGIN: [0.0, 0.0]}
        self.entities: dict[str, dict] = {}
        self.constraints: list[dict] = []
        self._n = itertools.count(1)
        self.status = {}          # results of the last solve (for colouring)

    # ------------------------------------------------------------------ ids / serialisation
    def _id(self, prefix):
        while True:
            i = f"{prefix}{next(self._n)}"
            if i not in self.points and i not in self.entities and not any(c.get("id") == i for c in self.constraints):
                return i

    def to_dict(self):
        return {"points": copy.deepcopy(self.points), "entities": copy.deepcopy(self.entities),
                "constraints": copy.deepcopy(self.constraints)}

    @staticmethod
    def from_dict(d) -> "Sketch":
        s = Sketch()
        s.points = copy.deepcopy(d.get("points", {}))
        s.points.setdefault(ORIGIN, [0.0, 0.0])
        s.entities = copy.deepcopy(d.get("entities", {}))
        s.constraints = copy.deepcopy(d.get("constraints", []))
        nums = [int("".join(ch for ch in k if ch.isdigit()) or 0)
                for k in list(s.points) + list(s.entities) + [c.get("id", "") for c in s.constraints]]
        s._n = itertools.count(max(nums + [0]) + 1)
        return s

    def copy(self) -> "Sketch":
        return Sketch.from_dict(self.to_dict())

    # ------------------------------------------------------------------ creation
    def add_point(self, x, y) -> str:
        pid = self._id("p")
        self.points[pid] = [float(x), float(y)]
        return pid

    def add_line(self, a, b, construction=False) -> str:
        eid = self._id("l")
        self.entities[eid] = {"type": "line", "p1": a, "p2": b, "construction": construction}
        return eid

    def add_circle(self, c, r, construction=False) -> str:
        eid = self._id("c")
        self.entities[eid] = {"type": "circle", "c": c, "r": float(r), "construction": construction}
        return eid

    def add_arc(self, c, p1, p2, construction=False) -> str:
        eid = self._id("a")
        self.entities[eid] = {"type": "arc", "c": c, "p1": p1, "p2": p2, "construction": construction}
        return eid

    def add_point_entity(self, p) -> str:
        eid = self._id("pt")
        self.entities[eid] = {"type": "point", "p": p, "construction": True}
        return eid

    def add_constraint(self, ctype, *refs, value=None) -> dict:
        c = {"id": self._id("k"), "type": ctype, "refs": list(refs)}
        if value is not None:
            c["value"] = [float(v) for v in value] if isinstance(value, (list, tuple)) else float(value)
        self.constraints.append(c)
        return c

    def entity_points(self, eid) -> list[str]:
        e = self.entities[eid]
        return [e[k] for k in ("p1", "p2", "c", "p") if k in e]

    def delete(self, ids):
        """Delete entities, points or constraints (and anything that depends on them)."""
        ids = set(ids)
        cons_ids = {c["id"] for c in self.constraints if c["id"] in ids}
        ent_ids = {i for i in ids if i in self.entities}
        pt_ids = {i for i in ids if i in self.points and i != ORIGIN}
        for eid, e in list(self.entities.items()):
            if any(p in pt_ids for p in self.entity_points(eid)):
                ent_ids.add(eid)
        for eid in ent_ids:
            self.entities.pop(eid, None)
        # remove now-orphaned points
        used = {ORIGIN}
        for eid in self.entities:
            used.update(self.entity_points(eid))
        for pid in list(self.points):
            if pid not in used:
                pt_ids.add(pid)
                self.points.pop(pid)
        gone = ent_ids | pt_ids | cons_ids
        self.constraints = [c for c in self.constraints
                            if c["id"] not in gone and not any(r in gone for r in c["refs"])]

    def merge_points(self, keep, drop):
        """Replace point `drop` with `keep` everywhere (used when snapping endpoints together)."""
        if keep == drop or drop == ORIGIN:
            return
        for e in self.entities.values():
            for k in ("p1", "p2", "c", "p"):
                if e.get(k) == drop:
                    e[k] = keep
        for c in self.constraints:
            c["refs"] = [keep if r == drop else r for r in c["refs"]]
        self.points.pop(drop, None)
        # drop now-degenerate constraints
        self.constraints = [c for c in self.constraints
                            if not (c["type"] == "coincident" and c["refs"][0] == c["refs"][1])]

    # ------------------------------------------------------------------ geometry queries
    def P(self, pid):
        return np.array(self.points[pid], float)

    def radius(self, eid):
        e = self.entities[eid]
        if e["type"] == "circle":
            return e["r"]
        if e["type"] == "arc":
            return float(np.linalg.norm(self.P(e["p1"]) - self.P(e["c"])))
        return 0.0

    def arc_angles(self, eid):
        e = self.entities[eid]
        c = self.P(e["c"])
        a1 = math.atan2(*(self.P(e["p1"]) - c)[::-1])
        a2 = math.atan2(*(self.P(e["p2"]) - c)[::-1])
        while a2 <= a1 + 1e-9:
            a2 += 2 * math.pi
        return a1, a2

    def polyline(self, eid, n=48) -> np.ndarray:
        """Points to draw an entity, in sketch coordinates."""
        e = self.entities[eid]
        t = e["type"]
        if t == "line":
            return np.array([self.P(e["p1"]), self.P(e["p2"])])
        if t == "circle":
            c = self.P(e["c"])
            a = np.linspace(0, 2 * math.pi, n + 1)
            return c + e["r"] * np.c_[np.cos(a), np.sin(a)]
        if t == "arc":
            c = self.P(e["c"])
            r = self.radius(eid)
            a1, a2 = self.arc_angles(eid)
            a = np.linspace(a1, a2, max(8, int(n * (a2 - a1) / (2 * math.pi)) + 2))
            return c + r * np.c_[np.cos(a), np.sin(a)]
        if t == "point":
            return np.array([self.P(e["p"])])
        return np.zeros((0, 2))

    def curves(self) -> list[dict]:
        """Non-construction geometry in the format kernel.sketch_regions expects."""
        out = []
        for eid, e in self.entities.items():
            if e.get("construction"):
                continue
            if e["type"] == "line":
                out.append({"type": "line", "p1": self.points[e["p1"]], "p2": self.points[e["p2"]]})
            elif e["type"] == "circle":
                out.append({"type": "circle", "c": self.points[e["c"]], "r": e["r"]})
            elif e["type"] == "arc":
                c = self.P(e["c"])
                r = self.radius(eid)
                a1, a2 = self.arc_angles(eid)
                am = (a1 + a2) / 2
                out.append({"type": "arc", "p1": self.points[e["p1"]],
                            "pm": (c + r * np.array([math.cos(am), math.sin(am)])).tolist(),
                            "p2": (c + r * np.array([math.cos(a2), math.sin(a2)])).tolist()})
        return out

    def lines(self):
        """All lines (including construction) as (eid, p0, p1) — used for revolve axes."""
        return [(eid, self.P(e["p1"]), self.P(e["p2"])) for eid, e in self.entities.items() if e["type"] == "line"]

    # ------------------------------------------------------------------ solver
    def _layout(self):
        """Map each free scalar to a column in the variable vector."""
        idx = {}
        n = 0
        for pid in self.points:
            if pid == ORIGIN:
                continue
            idx[(pid, 0)] = n
            idx[(pid, 1)] = n + 1
            n += 2
        for eid, e in self.entities.items():
            if e["type"] == "circle":
                idx[(eid, "r")] = n
                n += 1
        return idx, n

    def _get_x(self, idx, n):
        x = np.zeros(n)
        for (k, j), i in idx.items():
            x[i] = self.entities[k]["r"] if j == "r" else self.points[k][j]
        return x

    def _set_x(self, idx, x):
        for (k, j), i in idx.items():
            if j == "r":
                self.entities[k]["r"] = float(x[i])
            else:
                self.points[k][j] = float(x[i])

    def _equations(self):
        """Yield (constraint_id, residual_fn, var_keys). residual_fn(get) -> list of floats."""
        eqs = []
        ents = self.entities

        def pt(get, p):
            return np.array([get((p, 0)), get((p, 1))])

        def rad(get, eid):
            e = ents[eid]
            if e["type"] == "circle":
                return get((eid, "r"))
            return float(np.linalg.norm(pt(get, e["p1"]) - pt(get, e["c"])))

        def pkeys(*pids):
            return [(p, j) for p in pids for j in (0, 1)]

        def ekeys(eid):
            e = ents[eid]
            ks = pkeys(*[e[k] for k in ("p1", "p2", "c", "p") if k in e])
            if e["type"] == "circle":
                ks.append((eid, "r"))
            return ks

        def line_dir(get, eid):
            e = ents[eid]
            return pt(get, e["p2"]) - pt(get, e["p1"])

        # implicit arc constraint: both endpoints on the same radius
        for eid, e in ents.items():
            if e["type"] == "arc":
                def f(get, e=e):
                    c = pt(get, e["c"])
                    return [np.linalg.norm(pt(get, e["p1"]) - c) - np.linalg.norm(pt(get, e["p2"]) - c)]
                eqs.append((None, f, pkeys(e["c"], e["p1"], e["p2"])))

        for c in self.constraints:
            t, R = c["type"], c["refs"]
            v = c.get("value", 0.0)
            try:
                f, keys = self._make_eq(t, R, v, pt, rad, line_dir, pkeys, ekeys, ents)
            except (KeyError, IndexError):
                continue
            if f is not None:
                eqs.append((c["id"], f, keys))
        return eqs

    @staticmethod
    def _make_eq(t, R, v, pt, rad, line_dir, pkeys, ekeys, ents):
        def typ(i):
            return ents[R[i]]["type"] if R[i] in ents else "pointref"

        if t == "coincident":
            a, b = R
            return (lambda g: list(pt(g, a) - pt(g, b))), pkeys(a, b)
        if t == "point_on":
            p, e = R
            if typ(1) == "line":
                l1, l2 = ents[e]["p1"], ents[e]["p2"]

                def f(g):
                    d = pt(g, l2) - pt(g, l1)
                    L = np.linalg.norm(d) + 1e-12
                    w = pt(g, p) - pt(g, l1)
                    return [(d[0] * w[1] - d[1] * w[0]) / L]
                return f, pkeys(p) + ekeys(e)
            if typ(1) in ("circle", "arc"):
                cc = ents[e]["c"]
                return (lambda g: [np.linalg.norm(pt(g, p) - pt(g, cc)) - rad(g, e)]), pkeys(p) + ekeys(e)
            return None, []
        if t in ("horizontal", "vertical"):
            j = 1 if t == "horizontal" else 0
            if len(R) == 1:
                a, b = ents[R[0]]["p1"], ents[R[0]]["p2"]
            else:
                a, b = R
            return (lambda g: [pt(g, a)[j] - pt(g, b)[j]]), pkeys(a, b)
        if t in ("parallel", "perpendicular"):
            l1, l2 = R

            def f(g):
                d1 = line_dir(g, l1)
                d2 = line_dir(g, l2)
                n = (np.linalg.norm(d1) * np.linalg.norm(d2)) + 1e-12
                if t == "parallel":
                    return [(d1[0] * d2[1] - d1[1] * d2[0]) / n]
                return [(d1 @ d2) / n]
            return f, ekeys(l1) + ekeys(l2)
        if t == "equal":
            a, b = R
            if typ(0) == "line" and typ(1) == "line":
                return (lambda g: [np.linalg.norm(line_dir(g, a)) - np.linalg.norm(line_dir(g, b))]), ekeys(a) + ekeys(b)
            return (lambda g: [rad(g, a) - rad(g, b)]), ekeys(a) + ekeys(b)
        if t == "concentric":
            a, b = R
            ca = ents[a]["c"] if a in ents else a
            cb = ents[b]["c"] if b in ents else b
            return (lambda g: list(pt(g, ca) - pt(g, cb))), pkeys(ca, cb)
        if t == "normal_at":
            # arc/circle `a` meets line `l` at point `p` at right angles to the radius (i.e. tangent there).
            # Pins the tangent point first-order, so filleted corners solve as fully defined.
            a, l, p = R
            cc = ents[a]["c"]
            l1, l2 = ents[l]["p1"], ents[l]["p2"]

            def f(g):
                d = pt(g, l2) - pt(g, l1)
                return [float((pt(g, p) - pt(g, cc)) @ d) / (np.linalg.norm(d) + 1e-12)]
            return f, pkeys(p, cc, l1, l2)
        if t == "midpoint":
            p, l = R
            a, b = ents[l]["p1"], ents[l]["p2"]
            return (lambda g: list(pt(g, p) - (pt(g, a) + pt(g, b)) / 2)), pkeys(p, a, b)
        if t == "tangent":
            a, b = R
            if typ(0) != "line" and typ(1) == "line":
                a, b = b, a
            if typ(0) == "line":
                l1, l2 = ents[a]["p1"], ents[a]["p2"]
                cc = ents[b]["c"]

                def f(g):
                    d = pt(g, l2) - pt(g, l1)
                    L = np.linalg.norm(d) + 1e-12
                    w = pt(g, cc) - pt(g, l1)
                    return [abs(d[0] * w[1] - d[1] * w[0]) / L - rad(g, b)]
                return f, ekeys(a) + ekeys(b)
            ca, cb = ents[a]["c"], ents[b]["c"]
            internal = v < 0  # stored sign chooses internal/external tangency

            def f(g):
                dd = np.linalg.norm(pt(g, ca) - pt(g, cb))
                return [dd - (abs(rad(g, a) - rad(g, b)) if internal else rad(g, a) + rad(g, b))]
            return f, ekeys(a) + ekeys(b)
        if t == "symmetric":
            a, b, l = R
            l1, l2 = ents[l]["p1"], ents[l]["p2"]

            def f(g):
                d = pt(g, l2) - pt(g, l1)
                L = np.linalg.norm(d) + 1e-12
                m = (pt(g, a) + pt(g, b)) / 2 - pt(g, l1)
                w = pt(g, b) - pt(g, a)
                return [(d[0] * m[1] - d[1] * m[0]) / L, (w @ d) / L]
            return f, pkeys(a, b, l1, l2)
        if t == "fix":
            p = R[0]
            x0, y0 = v if isinstance(v, (list, tuple)) else (0, 0)
            return (lambda g: [pt(g, p)[0] - x0, pt(g, p)[1] - y0]), pkeys(p)
        if t in ("distance", "hdistance", "vdistance"):
            if len(R) == 1:      # line length
                a, b = ents[R[0]]["p1"], ents[R[0]]["p2"]
                R = [a, b]
            a, b = R
            if a in ents and ents[a]["type"] == "line":
                a, b = b, a
            if b in ents and ents[b]["type"] == "line":   # point-to-line distance
                l1, l2 = ents[b]["p1"], ents[b]["p2"]

                def f(g):
                    d = pt(g, l2) - pt(g, l1)
                    L = np.linalg.norm(d) + 1e-12
                    w = pt(g, a) - pt(g, l1)
                    return [abs(d[0] * w[1] - d[1] * w[0]) / L - v]
                return f, pkeys(a, l1, l2)
            if a in ents:
                a = ents[a].get("c", ents[a].get("p"))
            if b in ents:
                b = ents[b].get("c", ents[b].get("p"))
            if t == "hdistance":
                return (lambda g: [abs(pt(g, a)[0] - pt(g, b)[0]) - v]), pkeys(a, b)
            if t == "vdistance":
                return (lambda g: [abs(pt(g, a)[1] - pt(g, b)[1]) - v]), pkeys(a, b)
            return (lambda g: [np.linalg.norm(pt(g, a) - pt(g, b)) - v]), pkeys(a, b)
        if t in ("radius", "diameter"):
            e = R[0]
            k = 1.0 if t == "radius" else 2.0
            return (lambda g: [k * rad(g, e) - v]), ekeys(e)
        if t == "angle":
            l1, l2 = R
            th = math.radians(v)

            def f(g):
                d1 = line_dir(g, l1)
                d2 = line_dir(g, l2)
                n = (np.linalg.norm(d1) * np.linalg.norm(d2)) + 1e-12
                cr = (d1[0] * d2[1] - d1[1] * d2[0]) / n
                dt = (d1 @ d2) / n
                # unsigned angle between the lines (0..180)
                return [math.atan2(abs(cr), dt) - th]
            return f, ekeys(l1) + ekeys(l2)
        return None, []

    def solve(self, weights: dict | None = None, iters=60, tol=1e-9) -> bool:
        """Solve all constraints in place. `weights` raises the cost of moving specific
        variables (e.g. the point being dragged). Returns True if every constraint is met."""
        idx, n = self._layout()
        if n == 0:
            self.status = {"ok": True, "bad": set(), "free": set(), "dof": 0}
            return True
        eqs = self._equations()
        x = self._get_x(idx, n)
        w = np.ones(n)
        if weights:
            for k, val in weights.items():
                if k in idx:
                    w[idx[k]] = val

        def evaluate(x, want_jac):
            rows, J = [], []
            for cid, f, keys in eqs:
                def get(k, x=x):
                    if k[0] == ORIGIN:
                        return 0.0
                    return x[idx[k]]
                r = np.atleast_1d(np.asarray(f(get), float))
                rows.append(r)
                if want_jac:
                    jr = np.zeros((len(r), n))
                    for k in set(keys):
                        if k not in idx:
                            continue
                        i = idx[k]
                        h = 1e-7 * max(1.0, abs(x[i]))
                        xp = x.copy()
                        xp[i] += h

                        def getp(kk, xp=xp):
                            if kk[0] == ORIGIN:
                                return 0.0
                            return xp[idx[kk]]
                        jr[:, i] = (np.atleast_1d(np.asarray(f(getp), float)) - r) / h
                    J.append(jr)
            r = np.concatenate(rows) if rows else np.zeros(0)
            Jm = np.vstack(J) if (want_jac and J) else np.zeros((0, n))
            return r, Jm

        r, J = evaluate(x, True)
        for _ in range(iters):
            err = float(r @ r)
            if err < tol * tol:
                break
            Js = J / w           # weighted minimum-norm step
            step_y, *_ = np.linalg.lstsq(Js, -r, rcond=1e-10)
            step = step_y / w
            lam = 1.0
            improved = False
            for _ls in range(12):
                xn = x + lam * step
                rn, _ = evaluate(xn, False)
                if rn @ rn < err:
                    improved = True
                    break
                lam *= 0.5
            if not improved:
                break
            x = xn
            r, J = evaluate(x, True)

        self._set_x(idx, x)
        # ---- diagnostics: which constraints fail, which variables remain free
        bad = set()
        pos = 0
        for cid, f, keys in eqs:
            k = len(np.atleast_1d(f(lambda kk: 0.0 if kk[0] == ORIGIN else x[idx[kk]])))
            if cid and np.any(np.abs(r[pos:pos + k]) > 1e-6):
                bad.add(cid)
            pos += k
        free = set()
        dof = n
        if J.size:
            _, s, vt = np.linalg.svd(J, full_matrices=True)
            rank = int(np.sum(s > 1e-8 * max(1.0, s[0] if len(s) else 1)))
            dof = n - rank
            null = vt[rank:]
            if len(null):
                mag = np.linalg.norm(null, axis=0)
                for (kk, j), i in idx.items():
                    if mag[i] > 1e-6:
                        free.add(kk)
        else:
            free = {k for (k, _) in idx}
        self.status = {"ok": not bad, "bad": bad, "free": free, "dof": dof}
        return not bad

    def is_entity_free(self, eid) -> bool:
        free = self.status.get("free", set())
        if eid in free:
            return True
        return any(p in free for p in self.entity_points(eid))
