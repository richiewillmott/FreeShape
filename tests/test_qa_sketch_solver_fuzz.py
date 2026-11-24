"""Randomised (seeded, reproducible) fuzz test for the FreeShape 2D constraint solver.

Each seed builds a random *satisfiable* sketch: geometry is first laid out in a known-good configuration,
constraints are derived from that configuration (so a solution is known to exist), then every free variable is
perturbed and Sketch.solve() is run. Every constraint residual is then checked with an evaluator written
independently of freeshape/sketch.py, and the solver's own status["ok"] is cross-checked.

Run from the repo root:
    python -m pytest <this file> -q
    python <this file> [n_seeds]          # prints a summary + timings
Reproduce one case:  python -m pytest <this file> -q -k "seed_17"
"""
from __future__ import annotations

import math
import os
import random
import sys
import time

import numpy as np
import pytest

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, REPO)
from freeshape.sketch import Sketch, ORIGIN  # noqa: E402

TOL = 1e-6          # residual tolerance (mm / rad)
PERTURB = 0.03      # perturbation as a fraction of each component's size


# ----------------------------------------------------------------------------- independent residuals
def _P(sk, p):
    return np.array(sk.points[p], float)


def _cross(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _ldir(sk, l):
    e = sk.entities[l]
    return _P(sk, e["p2"]) - _P(sk, e["p1"])


def _rad(sk, e):
    ent = sk.entities[e]
    if ent["type"] == "circle":
        return ent["r"]
    return float(np.linalg.norm(_P(sk, ent["p1"]) - _P(sk, ent["c"])))


def _pt_line_dist(sk, p, l):
    e = sk.entities[l]
    a = _P(sk, e["p1"])
    d = _ldir(sk, l)
    return abs(_cross(d, _P(sk, p) - a)) / np.linalg.norm(d)


def residual(sk, c) -> float:
    t, R, v = c["type"], c["refs"], c.get("value")
    if t == "coincident":
        return float(np.linalg.norm(_P(sk, R[0]) - _P(sk, R[1])))
    if t == "horizontal":
        e = sk.entities[R[0]]
        return abs(_P(sk, e["p1"])[1] - _P(sk, e["p2"])[1])
    if t == "vertical":
        e = sk.entities[R[0]]
        return abs(_P(sk, e["p1"])[0] - _P(sk, e["p2"])[0])
    if t in ("parallel", "perpendicular", "angle"):
        d1, d2 = _ldir(sk, R[0]), _ldir(sk, R[1])
        n1, n2 = np.linalg.norm(d1), np.linalg.norm(d2)
        if n1 < 1e-6 or n2 < 1e-6:
            return float("inf")          # a collapsed line does not count as satisfying a direction constraint
        ang = math.atan2(abs(_cross(d1, d2)), d1 @ d2)
        if t == "parallel":
            return min(ang, math.pi - ang)
        if t == "perpendicular":
            return abs(ang - math.pi / 2)
        return abs(ang - math.radians(v))
    if t == "equal":
        a, b = R
        if sk.entities[a]["type"] == "line":
            return abs(np.linalg.norm(_ldir(sk, a)) - np.linalg.norm(_ldir(sk, b)))
        return abs(_rad(sk, a) - _rad(sk, b))
    if t == "concentric":
        return float(np.linalg.norm(_P(sk, sk.entities[R[0]]["c"]) - _P(sk, sk.entities[R[1]]["c"])))
    if t == "midpoint":
        e = sk.entities[R[1]]
        return float(np.linalg.norm(_P(sk, R[0]) - (_P(sk, e["p1"]) + _P(sk, e["p2"])) / 2))
    if t == "point_on":
        p, e = R
        if sk.entities[e]["type"] == "line":
            return _pt_line_dist(sk, p, e)
        return abs(np.linalg.norm(_P(sk, p) - _P(sk, sk.entities[e]["c"])) - _rad(sk, e))
    if t == "tangent":
        a, b = R
        if sk.entities[a]["type"] == "line":
            return abs(_pt_line_dist(sk, sk.entities[b]["c"], a) - _rad(sk, b))
        dd = np.linalg.norm(_P(sk, sk.entities[a]["c"]) - _P(sk, sk.entities[b]["c"]))
        want = abs(_rad(sk, a) - _rad(sk, b)) if (v or 1) < 0 else _rad(sk, a) + _rad(sk, b)
        return abs(dd - want)
    if t == "normal":
        l, e = R[0], R[1]
        return _pt_line_dist(sk, sk.entities[e]["c"], l)
    if t == "symmetric":
        a, b, l = R
        e = sk.entities[l]
        A0 = _P(sk, e["p1"])
        d = _ldir(sk, l)
        d = d / np.linalg.norm(d)
        q = _P(sk, a) - A0
        refl = A0 + 2 * (q @ d) * d - q
        return float(np.linalg.norm(refl - _P(sk, b)))
    if t == "fix":
        return float(np.linalg.norm(_P(sk, R[0]) - np.array(v, float)))
    if t == "distance":
        if len(R) == 1:
            return abs(np.linalg.norm(_ldir(sk, R[0])) - v)
        a, b = R
        if b in sk.entities and sk.entities[b]["type"] == "line":
            return abs(_pt_line_dist(sk, a, b) - v)
        return abs(np.linalg.norm(_P(sk, a) - _P(sk, b)) - v)
    if t == "hdistance":
        return abs(abs(_P(sk, R[0])[0] - _P(sk, R[1])[0]) - v)
    if t == "vdistance":
        return abs(abs(_P(sk, R[0])[1] - _P(sk, R[1])[1]) - v)
    if t == "radius":
        return abs(_rad(sk, R[0]) - v)
    if t == "diameter":
        return abs(2 * _rad(sk, R[0]) - v)
    raise AssertionError(f"unchecked constraint type {t}")


def geometric_sanity(sk) -> list[str]:
    out = []
    for eid, e in sk.entities.items():
        if e["type"] == "circle" and not (e["r"] > 1e-6):
            out.append(f"circle {eid} has non-positive radius {e['r']}")
    for p, xy in sk.points.items():
        if not all(math.isfinite(v) for v in xy):
            out.append(f"point {p} not finite: {xy}")
    return out


# ----------------------------------------------------------------------------- random satisfiable sketches
def _rot(v, a):
    c, s = math.cos(a), math.sin(a)
    return np.array([c * v[0] - s * v[1], s * v[0] + c * v[1]])


class Gen:
    def __init__(self, seed, redundant=False):
        self.redundant = redundant        # add consistent-but-redundant constraints (known solver weakness)
        self.rng = random.Random(seed)
        self.sk = Sketch()
        self.slot = 0
        self.lines, self.circles = [], []

    def origin_for_next(self):
        self.slot += 1
        return np.array([(self.slot % 4) * 120.0, (self.slot // 4) * 120.0])

    def u(self, a, b):
        return self.rng.uniform(a, b)

    def rect(self):
        sk, o = self.sk, self.origin_for_next()
        w, h = self.u(5, 60), self.u(5, 60)
        P = [sk.add_point(*(o + d)) for d in ((0, 0), (w, 0), (w, h), (0, h))]
        L = [sk.add_line(P[i], P[(i + 1) % 4]) for i in range(4)]
        sk.add_constraint("horizontal", L[0]); sk.add_constraint("horizontal", L[2])
        sk.add_constraint("vertical", L[1]); sk.add_constraint("vertical", L[3])
        sk.add_constraint("fix", P[0], value=list(o))
        if self.rng.random() < 0.5:
            sk.add_constraint("distance", L[0], value=w)
        else:
            sk.add_constraint("hdistance", P[0], P[1], value=w)
        if self.rng.random() < 0.5:
            sk.add_constraint("distance", L[1], value=h)
        else:
            sk.add_constraint("vdistance", P[1], P[2], value=h)
        if self.rng.random() < 0.4:        # centre point via midpoint of a construction diagonal
            m = sk.add_point(*(o + (w / 2, h / 2)))
            dg = sk.add_line(P[0], P[2], construction=True)
            sk.add_constraint("midpoint", m, dg)
        self.lines += L
        return P, L

    def rotated_rect(self):
        sk, o = self.sk, self.origin_for_next()
        w, h, a = self.u(5, 60), self.u(5, 60), self.u(-1.4, 1.4)
        P = [sk.add_point(*(o + _rot(np.array(d), a))) for d in ((0, 0), (w, 0), (w, h), (0, h))]
        L = [sk.add_line(P[i], P[(i + 1) % 4]) for i in range(4)]
        ref = sk.add_point(*(o + (50, 0)))
        base = sk.add_line(P[0], ref, construction=True)
        sk.add_constraint("horizontal", base)
        sk.add_constraint("fix", P[0], value=list(o))
        sk.add_constraint("parallel", L[0], L[2]); sk.add_constraint("parallel", L[1], L[3])
        sk.add_constraint("perpendicular", L[0], L[1])
        sk.add_constraint("distance", L[0], value=w); sk.add_constraint("distance", L[1], value=h)
        sk.add_constraint("angle", base, L[0], value=abs(math.degrees(a)))
        if self.rng.random() < 0.5 and self.redundant:
            sk.add_constraint("equal", L[0], L[2])      # implied by the two parallels: redundant
        self.lines += L
        return P, L

    def triangle(self):
        sk, o = self.sk, self.origin_for_next()
        A = o
        B = o + np.array([self.u(10, 50), 0])
        C = o + np.array([self.u(-10, 50), self.u(8, 50)])
        P = [sk.add_point(*q) for q in (A, B, C)]
        L = [sk.add_line(P[i], P[(i + 1) % 3]) for i in range(3)]
        sk.add_constraint("fix", P[0], value=list(A))
        sk.add_constraint("horizontal", L[0])
        sk.add_constraint("distance", L[0], value=float(np.linalg.norm(B - A)))
        sk.add_constraint("distance", P[1], P[2], value=float(np.linalg.norm(C - B)))
        ang = math.degrees(math.atan2(abs(_cross(B - A, A - C)), (B - A) @ (A - C)))  # L[2] runs C->A
        sk.add_constraint("angle", L[0], L[2], value=ang)
        self.lines += L
        return P, L

    def circles_group(self):
        sk, o = self.sk, self.origin_for_next()
        r1 = self.u(3, 25)
        c1 = sk.add_point(*o)
        e1 = sk.add_circle(c1, r1)
        sk.add_constraint("fix", c1, value=list(o))
        sk.add_constraint("radius" if self.rng.random() < 0.5 else "diameter", e1,
                          value=r1 if self.rng.random() < 2 else 0)
        if sk.constraints[-1]["type"] == "diameter":
            sk.constraints[-1]["value"] = 2 * r1
        # external tangent circle
        r2, th = self.u(2, 20), self.u(0, 2 * math.pi)
        C2 = o + (r1 + r2) * np.array([math.cos(th), math.sin(th)])
        c2 = sk.add_point(*C2)
        e2 = sk.add_circle(c2, r2)
        sk.add_constraint("tangent", e1, e2, value=1.0)
        sk.add_constraint("radius", e2, value=r2)
        # internal tangent circle
        r3, th3 = self.u(0.5, r1 * 0.8), self.u(0, 2 * math.pi)
        C3 = o + (r1 - r3) * np.array([math.cos(th3), math.sin(th3)])
        c3 = sk.add_point(*C3)
        e3 = sk.add_circle(c3, r3)
        sk.add_constraint("tangent", e1, e3, value=-1.0)
        # concentric + equal
        c4 = sk.add_point(*o)
        e4 = sk.add_circle(c4, r2)
        sk.add_constraint("concentric", e1, e4)
        sk.add_constraint("equal", e4, e2)
        # tangent line above circle 1, and a normal line through its centre
        y = o[1] + r1
        a = sk.add_point(o[0] - 10, y); b = sk.add_point(o[0] + 10, y)
        lt = sk.add_line(a, b)
        sk.add_constraint("tangent", lt, e1)
        sk.add_constraint("horizontal", lt)
        phi = self.u(0, 2 * math.pi)
        dirv = np.array([math.cos(phi), math.sin(phi)])
        n1 = sk.add_point(*(o + 0.3 * r1 * dirv)); n2 = sk.add_point(*(o + 2 * r1 * dirv))
        ln = sk.add_line(n1, n2)
        sk.add_constraint("normal", ln, e1)
        # point on circle and on line
        q = sk.add_point(*(o + r1 * dirv))
        sk.add_constraint("point_on", q, e1)
        sk.add_constraint("point_on", q, ln)
        # arc concentric with the first circle, with a radius
        ra = self.u(r1 + 1, r1 + 15)
        t1, t2 = self.u(0, 3), self.u(3.3, 6)
        ac = sk.add_point(*o)
        ap1 = sk.add_point(*(o + ra * np.array([math.cos(t1), math.sin(t1)])))
        ap2 = sk.add_point(*(o + ra * np.array([math.cos(t2), math.sin(t2)])))
        arc = sk.add_arc(ac, ap1, ap2)
        sk.add_constraint("concentric", arc, e1)
        sk.add_constraint("radius", arc, value=ra)
        self.circles += [e1, e2, e3, e4]

    def symmetric_pair(self):
        sk, o = self.sk, self.origin_for_next()
        A0 = o; ang = self.u(0, math.pi)
        d = np.array([math.cos(ang), math.sin(ang)])
        A1 = o + 40 * d
        a0, a1 = sk.add_point(*A0), sk.add_point(*A1)
        ax = sk.add_line(a0, a1, construction=True)
        sk.add_constraint("fix", a0, value=list(A0)); sk.add_constraint("fix", a1, value=list(A1))
        Q = o + np.array([self.u(-20, 20), self.u(-20, 20)])
        q = Q - A0
        R = A0 + 2 * (q @ d) * d - q
        pa, pb = sk.add_point(*Q), sk.add_point(*R)
        sk.add_constraint("symmetric", pa, pb, ax)
        sk.add_constraint("distance", pa, ax, value=float(abs(_cross(d, q))))
        dup = sk.add_point(*Q)                          # coincident duplicate
        sk.add_constraint("coincident", dup, pa)
        sk.add_line(pa, pb)

    def build(self):
        makers = [self.rect, self.rotated_rect, self.triangle, self.circles_group, self.symmetric_pair]
        for _ in range(self.rng.randint(2, 6)):
            self.rng.choice(makers)()
        return self.sk


def perturb(sk, rng, scale):
    for p in list(sk.points):
        if p == ORIGIN:
            continue
        sk.points[p] = [sk.points[p][0] + rng.gauss(0, scale), sk.points[p][1] + rng.gauss(0, scale)]
    for e in sk.entities.values():
        if e["type"] == "circle":
            e["r"] = max(0.2, e["r"] * (1 + rng.gauss(0, PERTURB)))


def run_seed(seed, scale_frac=PERTURB, redundant=False):
    sk = Gen(seed, redundant).build()
    for c in sk.constraints:                          # the unperturbed sketch satisfies everything
        assert residual(sk, c) < 1e-6, (seed, c, residual(sk, c))
    rng = random.Random(seed * 7919 + 1)
    perturb(sk, rng, scale=scale_frac * 30)
    t0 = time.perf_counter()
    ok = sk.solve()
    dt = time.perf_counter() - t0
    bad = [(c["type"], c["id"], residual(sk, c)) for c in sk.constraints if residual(sk, c) > TOL]
    return sk, ok, bad, geometric_sanity(sk), dt


SEEDS = list(range(300))


@pytest.mark.parametrize("seed", SEEDS, ids=[f"seed_{s}" for s in SEEDS])
def test_solver_satisfies_random_satisfiable_sketch(seed):
    sk, ok, bad, sanity, _ = run_seed(seed)
    # if the solver says ok, every constraint must really hold
    if ok:
        assert not bad, f"solver reported ok but residuals violated: {bad}"
    assert ok, f"solver failed on a satisfiable sketch (small perturbation): {bad}"
    finite = [s for s in sanity if "finite" in s]
    assert not finite, finite


# ----------------------------------------------------------------------------- defects found by the QA pass
# (QA-15 negative radius, QA-14 redundant constraints, QA-02 infinite dimension), fixed since
def test_no_negative_radius_seed_236():
    sk, ok, bad, sanity, _ = run_seed(236)
    assert not sanity, sanity


@pytest.mark.parametrize("jitter", [0.01, 0.3])
def test_redundant_but_consistent_constraints_still_solve(jitter):
    sk = Sketch()
    P = [sk.add_point(x + jitter * ((i * 7) % 3 - 1), y + jitter * ((i * 5) % 3 - 1))
         for i, (x, y) in enumerate(((0, 0), (30, 0), (30, 10), (0, 10)))]
    L = [sk.add_line(P[i], P[(i + 1) % 4]) for i in range(4)]
    sk.add_constraint("parallel", L[0], L[2]); sk.add_constraint("parallel", L[1], L[3])
    sk.add_constraint("perpendicular", L[0], L[1])
    sk.add_constraint("distance", L[0], value=30); sk.add_constraint("distance", L[1], value=10)
    sk.add_constraint("equal", L[0], L[2])           # implied by the parallels: redundant, consistent
    assert sk.solve(), sk.status


def test_external_tangent_drag_inside_keeps_positive_radius():
    sk = Sketch(); c1 = sk.add_point(0, 0); c2 = sk.add_point(15, 0)
    e1 = sk.add_circle(c1, 10); e2 = sk.add_circle(c2, 5)
    sk.add_constraint("fix", c1, value=[0, 0]); sk.add_constraint("radius", e1, value=10)
    sk.add_constraint("tangent", e1, e2, value=1.0)
    sk.solve()
    sk.points[c2] = [3.0, 0.0]                        # what mouse_drag does, with the dragged point weighted
    sk.solve(weights={(c2, 0): 1e3, (c2, 1): 1e3})
    assert sk.entities[e2]["r"] > 0 or not sk.status["ok"], (sk.entities[e2]["r"], sk.status)


def test_infinite_dimension_is_reported_not_raised():
    sk = Sketch(); a = sk.add_point(0, 0); b = sk.add_point(10, 0); l = sk.add_line(a, b)
    sk.add_constraint("distance", l, value=float("inf"))
    assert sk.solve() is False


def test_solve_time_500_entities():
    """125 independent rectangles (500 lines, 875 constraints). Prints timing; asserts a loose bound only."""
    sk = Sketch()
    for i in range(125):
        x, y, w, h = (i % 12) * 30, (i // 12) * 30, 10 + i % 5, 5 + i % 3
        P = [sk.add_point(*q) for q in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
        L = [sk.add_line(P[k], P[(k + 1) % 4]) for k in range(4)]
        for t, k in (("horizontal", 0), ("horizontal", 2), ("vertical", 1), ("vertical", 3)):
            sk.add_constraint(t, L[k])
        sk.add_constraint("distance", L[0], value=w * 1.1); sk.add_constraint("distance", L[1], value=h * 0.9)
        sk.add_constraint("fix", P[0], value=[x + 0.3, y - 0.2])
    t0 = time.perf_counter(); assert sk.solve(); t1 = time.perf_counter() - t0
    t0 = time.perf_counter(); sk.solve(); t2 = time.perf_counter() - t0
    print(f"\n500 entities: first solve {t1:.2f}s, re-solve of solved sketch {t2:.2f}s")
    assert t1 < 60


@pytest.mark.parametrize("seed", SEEDS[:40], ids=[f"seed_{s}" for s in SEEDS[:40]])
def test_status_ok_is_honest_under_large_perturbation(seed):
    """Large perturbations may legitimately fail to converge, but status must never claim success falsely."""
    sk, ok, bad, sanity, _ = run_seed(seed, scale_frac=0.5)
    if ok:
        assert not bad, f"solver reported ok but residuals violated: {bad}"


def test_fd_jacobian_matches_central_difference():
    """The solver builds its Jacobian by forward differences; compare it to central differences at a random
    smooth (non-kink) configuration of a seed sketch."""
    sk = Gen(3).build()
    perturb(sk, random.Random(5), 1.0)
    idx, n = sk._layout()
    x = sk._get_x(idx, n)
    for cid, f, keys in sk._equations():
        def at(xx):
            return np.atleast_1d(np.asarray(f(lambda k: 0.0 if k[0] == ORIGIN else xx[idx[k]]), float))
        r = at(x)
        for k in set(keys):
            if k not in idx:
                continue
            i = idx[k]
            h = 1e-7 * max(1.0, abs(x[i]))
            xp = x.copy(); xp[i] += h
            fwd = (at(xp) - r) / h
            hc = 1e-5 * max(1.0, abs(x[i]))
            xp = x.copy(); xp[i] += hc
            xm = x.copy(); xm[i] -= hc
            cen = (at(xp) - at(xm)) / (2 * hc)
            assert np.allclose(fwd, cen, rtol=1e-3, atol=1e-5), (cid, k, fwd, cen)


if __name__ == "__main__":
    N = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    for frac in (PERTURB, 0.2, 0.5):
        fails, lies, insane, times = [], [], [], []
        for s in range(N):
            sk, ok, bad, sanity, dt = run_seed(s, frac)
            times.append(dt)
            if not ok:
                fails.append((s, bad[:3]))
            if ok and bad:
                lies.append((s, bad[:3]))
            if sanity:
                insane.append((s, sanity[:2]))
        print(f"perturb={frac}: seeds={N} not-converged={len(fails)} ok-but-violated={len(lies)} "
              f"insane-geometry={len(insane)} solve median={np.median(times)*1e3:.1f}ms max={max(times)*1e3:.1f}ms")
        for row in (fails[:4] + lies[:4] + insane[:4]):
            print("   ", row)


def test_three_point_rectangle_with_an_extra_parallel_can_be_dragged():
    """QA-14: the 3-point rectangle tool's perpendiculars plus a parallel the user adds (redundant) used to freeze
    every drag: the solve failed and the corner snapped back."""
    sk = Sketch()
    P = [sk.add_point(*q) for q in ((0, 0), (30, 0), (30, 10), (0, 10))]
    L = [sk.add_line(P[i], P[(i + 1) % 4]) for i in range(4)]
    for i in range(3):
        sk.add_constraint("perpendicular", L[i], L[i + 1])
    sk.add_constraint("parallel", L[1], L[3])
    assert sk.solve()
    sk.points[P[2]] = [31.5, 10.7]                   # what a drag does: move the corner, weight it, solve
    ok = sk.solve(weights={(P[2], 0): 1e3, (P[2], 1): 1e3})
    assert ok, sk.status
    assert np.allclose(sk.points[P[2]], [31.5, 10.7], atol=1e-3)


def test_negative_radius_dimension_is_reported():
    """QA-15: a radius of -4 used to solve "ok" with r = -4."""
    sk = Sketch(); c = sk.add_point(0, 0); e = sk.add_circle(c, 5)
    k = sk.add_constraint("radius", e, value=-4.0)
    assert sk.solve() is False and k["id"] in sk.status["bad"] and sk.entities[e]["r"] > 0


def test_a_circle_the_solve_shrinks_to_nothing_is_reported():
    """QA-15: a free circle internally tangent to a circle its centre sits on can only meet the tangency by
    shrinking to nothing; that's reported, not passed off as a solved sketch whose circle then vanishes."""
    sk = Sketch(); c1 = sk.add_point(0, 0); c2 = sk.add_point(10, 0)
    e1 = sk.add_circle(c1, 10); e2 = sk.add_circle(c2, 3)
    sk.add_constraint("fix", c1, value=[0, 0]); sk.add_constraint("fix", c2, value=[10, 0])
    sk.add_constraint("radius", e1, value=10)
    k = sk.add_constraint("tangent", e1, e2, value=-1.0)   # inside
    assert sk.solve() is False and k["id"] in sk.status["bad"]


@pytest.mark.parametrize("seed", SEEDS[:80], ids=[f"seed_{s}" for s in SEEDS[:80]])
def test_moderate_perturbation_converges_without_drifting(seed):
    """QA-31: at 20% perturbation 38 of 300 sketches used to stop unsolved, their points slid kilometres away
    (parallel / perpendicular were measured against the lines' current lengths, so stretching looked like
    progress). Now every one of the 300 converges, and nothing ends up far away."""
    sk, ok, bad, sanity, _ = run_seed(seed, scale_frac=0.2)
    assert ok and not bad and not sanity, (bad, sanity)
    pts = np.asarray(list(sk.points.values()), float)
    assert float(np.abs(pts).max()) < 1e4
