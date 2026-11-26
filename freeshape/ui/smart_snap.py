"""Analytic smart snap candidates in sketch coordinates; tolerance in screen pixels."""
from __future__ import annotations

import math
from itertools import combinations
import numpy as np
from ..sketch_ops import _intersections

CAPTURE_PX = 9.0
GUIDE_PX = 7.0


def arc_midpoint(c, a, b):
    d = a - c
    start = math.atan2(d[1], d[0])
    stop = math.atan2(*(b - c)[::-1])
    t = start + ((stop - start) % (2 * math.pi)) / 2
    return c + np.linalg.norm(d) * np.array([math.cos(t), math.sin(t)])


def curve(sk, eid):
    e = sk.entities.get(eid, {})
    if e.get("type") == "line":
        a, b = sk.P(e["p1"]), sk.P(e["p2"])
        if np.linalg.norm(b - a) > 1e-9:
            return ("line", a, b - a)
    if e.get("type") in ("circle", "arc"):
        r = sk.radius(eid)
        if r > 1e-9:
            return ("circle", sk.P(e["c"]), r)
    return None


def contains(sk, eid, q):
    e = sk.entities[eid]
    if e["type"] == "line":
        a, b = sk.P(e["p1"]), sk.P(e["p2"])
        d = b - a
        t = (q - a) @ d / max(d @ d, 1e-20)
        return -1e-7 <= t <= 1 + 1e-7
    if e["type"] == "arc":
        c = sk.P(e["c"])
        def angle(p):
            v = p - c
            return math.atan2(v[1], v[0])
        a, b = angle(sk.P(e["p1"])), angle(sk.P(e["p2"]))
        t = (angle(q) - a) % (2 * math.pi)
        return t <= (b - a) % (2 * math.pi) + 1e-7 or t >= 2 * math.pi - 1e-7
    return True


def crossings(sk, a, b):
    ca, cb = curve(sk, a), curve(sk, b)
    if ca is None or cb is None:
        return []
    return [q for q in _intersections(ca, cb)
            if contains(sk, a, q) and contains(sk, b, q)]


def guide_crossings(sk, eid, uv, axis):
    c = curve(sk, eid)
    if c is None:
        return []
    guide = ("line", np.asarray(uv), np.array([1., 0.] if axis == "H" else [0., 1.]))
    return [q for q in _intersections(c, guide) if contains(sk, eid, q)]


def rectangle_centres(sk):
    """Closed four-edge rectangles. No helper geometry is added during hover."""
    adj = {}
    for eid, e in sk.entities.items():
        if e["type"] != "line" or e.get("helper"):
            continue
        a, b = e["p1"], e["p2"]
        adj.setdefault(a, []).append((b, eid))
        adj.setdefault(b, []).append((a, eid))
    seen = set()
    for a, neighbours in adj.items():
        for b, ab in neighbours:
            for c, bc in adj.get(b, ()):
                if c == a:
                    continue
                for d, cd in adj.get(c, ()):
                    if d in (a, b):
                        continue
                    for end, da in adj.get(d, ()):
                        if end != a:
                            continue
                        ids = tuple(sorted((ab, bc, cd, da)))
                        if ids in seen:
                            continue
                        seen.add(ids)
                        p = [sk.P(i) for i in (a, b, c, d)]
                        v = [p[(i+1)%4] - p[i] for i in range(4)]
                        lengths = [np.linalg.norm(w) for w in v]
                        if min(lengths) < 1e-9:
                            continue
                        if any(abs(v[i] @ v[(i+1)%4]) > 1e-5 * lengths[i] * lengths[(i+1)%4]
                               for i in range(4)):
                            continue
                        if np.linalg.norm(p[0]+p[2]-p[1]-p[3]) > 1e-5 * max(lengths):
                            continue
                        yield (p[0]+p[2])/2, (a,c), ids


def direct_targets(sk, screen, xy, exclude=(), tol=CAPTURE_PX, rectangles=None):
    xy = np.asarray(xy, float)
    out, nearby = [], []
    def offer(q, ref, rank):
        d = float(np.linalg.norm(screen(q)-xy))
        if d <= tol:
            out.append((rank, d, ref, np.asarray(q)))
    for eid, e in sk.entities.items():
        if eid in exclude:
            continue
        # A displayed helper diagonal is a valid finite curve for snapping.
        # Only rectangle discovery excludes helpers (to avoid false rectangles).
        t = e["type"]
        if t == "line":
            a,b = sk.P(e["p1"]),sk.P(e["p2"])
            if not e.get("helper"):
                offer((a+b)/2, ("mid", eid), 1)
            box = np.asarray([screen(a),screen(b)])
        elif t in ("circle","arc"):
            c,r = sk.P(e["c"]),sk.radius(eid)
            if r < 1e-9:
                continue
            if t == "arc":
                offer(arc_midpoint(c,sk.P(e["p1"]),sk.P(e["p2"])), ("mid",eid), 1)
            for axis, d in (("horizontal",[1,0]),("horizontal",[-1,0]),
                            ("vertical",[0,1]),("vertical",[0,-1])):
                q = c+r*np.asarray(d)
                if contains(sk,eid,q):
                    offer(q,("quadrant",eid,axis,tuple(d)),2)
            center = screen(c)
            ext = np.hypot(screen(c+np.array([r,0]))-center,
                           screen(c+np.array([0,r]))-center)
            box = np.asarray([center-ext,center+ext])
        else:
            continue
        if np.all(xy >= box.min(0)-tol) and np.all(xy <= box.max(0)+tol):
            nearby.append(eid)
    for a,b in combinations(nearby,2):
        for q in crossings(sk,a,b):
            offer(q,("intersection",(a,b),tuple(q)),0)
    for q, diagonal, ids in (rectangle_centres(sk) if rectangles is None else rectangles):
        if not any(i in exclude for i in ids):
            offer(q,("rect_center",diagonal,ids),1)
    if not out:
        return None
    _,_,ref,q = min(out,key=lambda item:(round(item[1]/2),item[0],item[1],str(item[2])))
    return q,ref


def ref_position(sk, ref):
    if isinstance(ref,str):
        return sk.P(ref) if ref in sk.points else None
    kind = ref[0]
    if kind == "rect_center":
        a,b = ref[1]
        return (sk.P(a)+sk.P(b))/2 if a in sk.points and b in sk.points else None
    if kind == "intersection":
        a,b = ref[1]
        qs = crossings(sk,a,b)
        return min(qs,key=lambda q:np.linalg.norm(q-np.asarray(ref[2]))) if qs else None
    e = sk.entities.get(ref[1])
    if not e:
        return None
    if kind == "mid":
        if e["type"] == "line":
            return (sk.P(e["p1"])+sk.P(e["p2"]))/2
        if e["type"] == "arc":
            return arc_midpoint(sk.P(e["c"]),sk.P(e["p1"]),sk.P(e["p2"]))
    if kind == "quadrant":
        return sk.P(e["c"])+sk.radius(ref[1])*np.asarray(ref[3])
    return None


def axis_distance(screen, src, xy, axis):
    a = screen(src)
    d = screen(src+np.array([1.,0.] if axis=="H" else [0.,1.]))-a
    v = np.asarray(xy)-a
    return abs(d[0]*v[1]-d[1]*v[0])/max(np.linalg.norm(d),1e-12)


def direction_target(sk, screen, xy, uv, anchor, refs, excluded=()):
    """Parallel/perpendicular/equal length using explicitly awakened lines."""
    v = uv-anchor
    length = float(np.linalg.norm(v))
    if length < 1e-9:
        return None
    options = []
    for eid in refs:
        e = sk.entities.get(eid,{})
        if eid in excluded or e.get("type") != "line":
            continue
        d = sk.P(e["p2"])-sk.P(e["p1"])
        L = np.linalg.norm(d)
        if L < 1e-9:
            continue
        unit = d/L
        for kind,vec in (("parallel",unit),("perpendicular",np.array([-unit[1],unit[0]]))):
            q = anchor+vec*(v@vec)
            dist = np.linalg.norm(screen(q)-xy)
            if dist <= GUIDE_PX and np.linalg.norm(q-anchor)>1e-9:
                options.append((dist,kind,eid,q))
        q = anchor+v/length*L
        dist = np.linalg.norm(screen(q)-xy)
        if dist <= GUIDE_PX:
            options.append((dist,"equal",eid,q))
    return min(options,key=lambda x:(x[0],x[1],x[2]))[1:] if options else None
