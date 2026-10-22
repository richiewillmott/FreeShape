"""Sketch editing operations: fillet, offset, slot, mirror and patterns.

Pure geometry on a Sketch (no Qt), so they can be tested headlessly and reused by scripts.
Each raises ValueError with a user-readable message when it can't be done.
"""
from __future__ import annotations

import math

import numpy as np

from .sketch import Sketch, ORIGIN

TOL = 1e-7


def _ang(c, p):
    return math.atan2(p[1] - c[1], p[0] - c[0])


def _ccw_contains(c, p1, p2, p3):
    a1 = _ang(c, p1)
    a2 = (_ang(c, p2) - a1) % (2 * math.pi)
    a3 = (_ang(c, p3) - a1) % (2 * math.pi)
    return a3 < a2


def _users(sk: Sketch, pid):
    return [eid for eid, e in sk.entities.items() if pid in sk.entity_points(eid) and e["type"] != "point"]


# --------------------------------------------------------------------------- fillet

def fillet_corner(sk: Sketch, pid: str, r: float) -> str:
    """Round the corner where exactly two lines meet at point `pid`. Returns the new arc id."""
    if r <= 0:
        raise ValueError("Radius must be positive")
    users = _users(sk, pid)
    lines = [u for u in users if sk.entities[u]["type"] == "line"]
    if len(users) != 2 or len(lines) != 2:
        raise ValueError("Click a corner where two lines meet")
    P = sk.P(pid)
    ends = []
    for lid in lines:
        e = sk.entities[lid]
        other = e["p2"] if e["p1"] == pid else e["p1"]
        ends.append((lid, "p1" if e["p1"] == pid else "p2", sk.P(other)))
    (l1, k1, A), (l2, k2, B) = ends
    la, lb = np.linalg.norm(A - P), np.linalg.norm(B - P)
    if la < TOL or lb < TOL:
        raise ValueError("Lines are too short")
    u1, u2 = (A - P) / la, (B - P) / lb
    phi = math.acos(float(np.clip(u1 @ u2, -1, 1)))
    if phi < 1e-3 or phi > math.pi - 1e-3:
        raise ValueError("The two lines are in line with each other")
    theta = phi / 2
    dist = r / math.tan(theta)
    if dist >= la - 1e-9 or dist >= lb - 1e-9:
        raise ValueError("Radius is too large for these lines")
    T1, T2 = P + u1 * dist, P + u2 * dist
    bis = (u1 + u2) / np.linalg.norm(u1 + u2)
    C = P + bis * (r / math.sin(theta))
    t1, t2, c = sk.add_point(*T1), sk.add_point(*T2), sk.add_point(*C)
    sk.entities[l1][k1] = t1
    sk.entities[l2][k2] = t2
    construction = sk.entities[l1].get("construction", False) and sk.entities[l2].get("construction", False)
    M = C + (P - C) / np.linalg.norm(P - C) * r
    aid = sk.add_arc(c, t1, t2, construction) if _ccw_contains(C, T1, T2, M) else sk.add_arc(c, t2, t1, construction)
    # Keep the old corner as a "virtual sharp": it stays on both lines, and any length
    # dimensions on those lines now measure to it — so a dimensioned shape keeps its size.
    far = {l1: sk.entities[l1]["p2" if k1 == "p1" else "p1"], l2: sk.entities[l2]["p2" if k2 == "p1" else "p1"]}
    for k in sk.constraints:
        if k["type"] in ("distance", "hdistance", "vdistance") and len(k["refs"]) == 1 and k["refs"][0] in far:
            k["refs"] = [far[k["refs"][0]], pid]
    if not any(e["type"] == "point" and e["p"] == pid for e in sk.entities.values()):
        vs = sk.add_point_entity(pid)
        sk.entities[vs]["virtual"] = True
    sk.add_constraint("point_on", pid, l1)
    sk.add_constraint("point_on", pid, l2)
    sk.add_constraint("tangent", aid, l1)
    sk.add_constraint("tangent", aid, l2)
    sk.add_constraint("normal_at", aid, l1, t1)
    sk.add_constraint("normal_at", aid, l2, t2)
    rc = sk.add_constraint("radius", aid, value=r)
    rc["pos"] = (M + (M - C) / np.linalg.norm(M - C) * max(r, 1.0) * 1.2).tolist()
    sk.solve()
    return aid


# --------------------------------------------------------------------------- offset

def _chain(sk: Sketch, eid: str):
    """Ordered list of (eid, forward) for the chain of connected lines/arcs containing eid."""
    ents = sk.entities
    adj = {}
    for k, e in ents.items():
        if e["type"] in ("line", "arc"):
            for p in (e["p1"], e["p2"]):
                adj.setdefault(p, []).append(k)
    chain = [(eid, True)]
    seen = {eid}
    closed = False
    cur, fwd = eid, True
    while True:
        e = ents[cur]
        end = e["p2"] if fwd else e["p1"]
        nb = [x for x in adj.get(end, []) if x != cur]
        if len(adj.get(end, [])) != 2 or not nb:
            break
        nxt = nb[0]
        if nxt == eid:
            closed = True
            break
        if nxt in seen:
            break
        seen.add(nxt)
        nf = ents[nxt]["p1"] == end
        chain.append((nxt, nf))
        cur, fwd = nxt, nf
    if not closed:
        cur = eid
        while True:
            start = ents[cur]["p1"] if chain[0][1] else ents[cur]["p2"]
            nb = [x for x in adj.get(start, []) if x != cur]
            if len(adj.get(start, [])) != 2 or not nb or nb[0] in seen:
                break
            prv = nb[0]
            seen.add(prv)
            pf = ents[prv]["p2"] == start
            chain.insert(0, (prv, pf))
            cur = prv
    return chain, closed


def _left_normal(v):
    n = np.array([-v[1], v[0]])
    return n / max(np.linalg.norm(n), 1e-12)


def _intersections(c1, c2):
    """Intersections of two curves given as ('line', P, D) or ('circle', C, r)."""
    if c1[0] == "line" and c2[0] == "line":
        P, D = c1[1], c1[2]
        Q, E = c2[1], c2[2]
        den = D[0] * E[1] - D[1] * E[0]
        if abs(den) < 1e-12:
            return []
        w = Q - P
        t = (w[0] * E[1] - w[1] * E[0]) / den
        return [P + t * D]
    if c1[0] == "circle" and c2[0] == "line":
        c1, c2 = c2, c1
    if c1[0] == "line" and c2[0] == "circle":
        P, D = c1[1], c1[2] / np.linalg.norm(c1[2])
        C, r = c2[1], c2[2]
        f = P - C
        b = f @ D
        disc = b * b - (f @ f - r * r)
        if disc < -1e-9:
            return []
        s = math.sqrt(max(disc, 0))
        return [P + (-b - s) * D, P + (-b + s) * D]
    C1, r1, C2, r2 = c1[1], c1[2], c2[1], c2[2]
    d = np.linalg.norm(C2 - C1)
    if d < 1e-12 or d > r1 + r2 + 1e-9 or d < abs(r1 - r2) - 1e-9:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h = math.sqrt(max(r1 * r1 - a * a, 0))
    m = C1 + a * (C2 - C1) / d
    perp = np.array([-(C2 - C1)[1], (C2 - C1)[0]]) / d
    return [m + h * perp, m - h * perp]


def offset(sk: Sketch, eid: str, dist: float):
    """Offset the chain containing `eid`. Positive distance = outward for closed chains."""
    if abs(dist) < TOL:
        raise ValueError("Offset distance can't be zero")
    e0 = sk.entities.get(eid)
    if e0 is None or e0["type"] == "point":
        raise ValueError("Click a line, arc or circle")
    cons = e0.get("construction", False)
    if e0["type"] == "circle":
        r = e0["r"] + dist
        if r <= TOL:
            raise ValueError("Offset is larger than the circle")
        return [sk.add_circle(e0["c"], r, cons)]
    chain, closed = _chain(sk, eid)
    # orientation: positive offset = outward on a closed chain
    s = dist
    if closed:
        pts = []
        for k, fwd in chain:
            pl = sk.polyline(k, 32)
            pts.extend(pl if fwd else pl[::-1])
        pts = np.array(pts)
        area = 0.5 * np.sum(pts[:, 0] * np.roll(pts[:, 1], -1) - np.roll(pts[:, 0], -1) * pts[:, 1])
        s = -dist if area > 0 else dist          # CCW: outward is to the right
    items = []
    for k, fwd in chain:
        e = sk.entities[k]
        if e["type"] == "line":
            S, E = (sk.P(e["p1"]), sk.P(e["p2"])) if fwd else (sk.P(e["p2"]), sk.P(e["p1"]))
            n = _left_normal(E - S)
            items.append({"eid": k, "fwd": fwd, "type": "line", "S": S + s * n, "E": E + s * n,
                          "curve": ("line", S + s * n, E - S)})
        else:
            C = sk.P(e["c"])
            r = sk.radius(k)
            r2 = r - s if fwd else r + s
            if r2 <= TOL:
                raise ValueError("Offset is larger than an arc's radius")
            S, E = (sk.P(e["p1"]), sk.P(e["p2"])) if fwd else (sk.P(e["p2"]), sk.P(e["p1"]))
            items.append({"eid": k, "fwd": fwd, "type": "arc", "C": e["c"],
                          "S": C + (S - C) / np.linalg.norm(S - C) * r2,
                          "E": C + (E - C) / np.linalg.norm(E - C) * r2,
                          "curve": ("circle", C, r2)})
    n = len(items)
    joints = []          # new vertex positions; joint i is between item i-1 and item i
    for i in range(n):
        if i == 0 and not closed:
            joints.append(items[0]["S"])
            continue
        a, b = items[i - 1], items[i]
        guess = (a["E"] + b["S"]) / 2
        if np.linalg.norm(a["E"] - b["S"]) < 1e-6:
            joints.append(guess)
            continue
        cand = _intersections(a["curve"], b["curve"])
        joints.append(min(cand, key=lambda p: np.linalg.norm(p - guess)) if cand else guess)
    if not closed:
        joints.append(items[-1]["E"])
    pids = [sk.add_point(*p) for p in joints]
    new = []
    for i, it in enumerate(items):
        a = pids[i]
        b = pids[(i + 1) % len(pids)] if closed else pids[i + 1]
        if it["type"] == "line":
            lid = sk.add_line(a, b, cons)
            sk.add_constraint("parallel", lid, it["eid"])
            new.append(lid)
        else:
            new.append(sk.add_arc(it["C"], a, b, cons) if it["fwd"] else sk.add_arc(it["C"], b, a, cons))
    sk.solve()
    return new


# --------------------------------------------------------------------------- slot

def slot(sk: Sketch, a, b, width: float, construction=False, a_pid=None, b_pid=None):
    """Straight slot between centre points a and b (sketch coords). Returns the new entity ids."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    L = np.linalg.norm(b - a)
    if L < TOL:
        raise ValueError("Slot ends are in the same place")
    if width <= 0:
        raise ValueError("Width must be positive")
    w = width / 2
    d = (b - a) / L
    n = np.array([-d[1], d[0]])
    A = a_pid or sk.add_point(*a)
    B = b_pid or sk.add_point(*b)
    pa_l, pb_l = sk.add_point(*(a + n * w)), sk.add_point(*(b + n * w))
    pb_r, pa_r = sk.add_point(*(b - n * w)), sk.add_point(*(a - n * w))
    top = sk.add_line(pa_l, pb_l, construction)
    bot = sk.add_line(pb_r, pa_r, construction)
    end_b = sk.add_arc(B, pb_r, pb_l, construction)
    end_a = sk.add_arc(A, pa_l, pa_r, construction)
    centre = sk.add_line(A, B, True)
    for arc in (end_a, end_b):
        sk.add_constraint("tangent", arc, top)
        sk.add_constraint("tangent", arc, bot)
    sk.add_constraint("equal", end_a, end_b)
    rc = sk.add_constraint("radius", end_b, value=w)
    rc["pos"] = (b + d * w * 1.6 + n * w * 0.6).tolist()
    sk.solve()
    return [top, bot, end_a, end_b, centre]


# --------------------------------------------------------------------------- mirror & patterns

def _geometry(sk: Sketch, ids):
    ents = [i for i in ids if i in sk.entities]
    if not ents:
        raise ValueError("Select the sketch geometry first")
    return ents


def _copy(sk: Sketch, ents, transform, keep=frozenset(), orient_flip=False, copy_cons=()):
    """Copy entities with transform(uv)->uv. Points in `keep` map to themselves."""
    pmap = {}

    def P(pid):
        if pid in keep:
            return pid
        if pid not in pmap:
            pmap[pid] = sk.add_point(*transform(sk.P(pid)))
        return pmap[pid]

    emap = {}
    for eid in ents:
        e = sk.entities[eid]
        c = e.get("construction", False)
        t = e["type"]
        if t == "line":
            emap[eid] = sk.add_line(P(e["p1"]), P(e["p2"]), c)
        elif t == "circle":
            emap[eid] = sk.add_circle(P(e["c"]), e["r"], c)
        elif t == "arc":
            p1, p2 = P(e["p1"]), P(e["p2"])
            emap[eid] = sk.add_arc(P(e["c"]), p2, p1, c) if orient_flip else sk.add_arc(P(e["c"]), p1, p2, c)
        elif t == "point":
            emap[eid] = sk.add_point_entity(P(e["p"]))
    if copy_cons:
        allmap = {**pmap, **emap}
        inside = set(ents) | set(pmap)
        for k in list(sk.constraints):
            if k["type"] in copy_cons and all(r in inside or r in keep for r in k["refs"]) \
                    and any(r in inside for r in k["refs"]):
                sk.add_constraint(k["type"], *[allmap.get(r, r) for r in k["refs"]], value=k.get("value"))
    return pmap, emap


SHAPE_CONS_ROT = ("coincident", "point_on", "parallel", "perpendicular", "equal", "tangent", "concentric", "midpoint")
SHAPE_CONS_LIN = SHAPE_CONS_ROT + ("horizontal", "vertical")


def mirror(sk: Sketch, ids, axis_line: str):
    """Mirror selected geometry about a line, linked with symmetric constraints."""
    ents = [i for i in _geometry(sk, ids) if i != axis_line]
    ax = sk.entities.get(axis_line)
    if ax is None or ax["type"] != "line":
        raise ValueError("Pick a line to mirror about")
    A, B = sk.P(ax["p1"]), sk.P(ax["p2"])
    d = (B - A) / max(np.linalg.norm(B - A), 1e-12)

    def refl(p):
        v = p - A
        return A + 2 * (v @ d) * d - v

    pts = {p for e in ents for p in sk.entity_points(e)}
    on_axis = {p for p in pts if abs((sk.P(p) - A)[0] * d[1] - (sk.P(p) - A)[1] * d[0]) < 1e-7}
    pmap, emap = _copy(sk, ents, refl, keep=frozenset(on_axis), orient_flip=True)
    for old, new in pmap.items():
        sk.add_constraint("symmetric", old, new, axis_line)
    for old, new in emap.items():
        if sk.entities[new]["type"] == "circle":
            sk.add_constraint("equal", old, new)
    sk.solve()
    return list(emap.values())


def linear_pattern(sk: Sketch, ids, direction, count: int, spacing: float):
    ents = _geometry(sk, ids)
    count = int(count)
    if count < 2:
        raise ValueError("Count must be at least 2")
    d = np.asarray(direction, float)
    d = d / max(np.linalg.norm(d), 1e-12)
    new = []
    for k in range(1, count):
        off = d * spacing * k
        _, emap = _copy(sk, ents, lambda p, off=off: p + off, copy_cons=SHAPE_CONS_LIN)
        new += list(emap.values())
    sk.solve()
    return new


def circular_pattern(sk: Sketch, ids, centre, count: int, total_angle: float = 360.0):
    ents = _geometry(sk, ids)
    count = int(count)
    if count < 2:
        raise ValueError("Count must be at least 2")
    c = np.asarray(centre, float)
    full = abs(abs(total_angle) - 360) < 1e-9
    step = math.radians(total_angle) / (count if full else count - 1)
    new = []
    for k in range(1, count):
        a = step * k
        R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
        _, emap = _copy(sk, ents, lambda p, R=R: c + R @ (p - c), copy_cons=SHAPE_CONS_ROT)
        new += list(emap.values())
    sk.solve()
    return new


def extent_along(sk: Sketch, ids, d):
    d = np.asarray(d, float) / max(np.linalg.norm(d), 1e-12)
    vals = []
    for eid in ids:
        if eid in sk.entities:
            vals += [float(p @ d) for p in sk.polyline(eid, 24)]
    return (max(vals) - min(vals)) if vals else 0.0
