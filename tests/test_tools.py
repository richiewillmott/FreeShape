"""Headless tests for the sketch tools (fillet, offset, slot, mirror, patterns), variables and the
part features (sweep, loft, draft, holes, split, move).  Run:  python -m pytest tests  or  python tests/test_tools.py"""
import math
import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np

from freeshape.document import Document
from freeshape.sketch import Sketch, ORIGIN
from freeshape import sketch_ops as OPS, features as F, kernel as K

pl = K.std_plane('Top')
def areas(s):
    return sorted(round(K.face_info(f)['area'], 3) for f in K.sketch_regions(pl, s.curves()))
def rect(s, w=40, h=30, dims=True):
    a, b, c, d = s.add_point(0, 0), s.add_point(w, 0), s.add_point(w, h), s.add_point(0, h)
    L = [s.add_line(a, b), s.add_line(b, c), s.add_line(c, d), s.add_line(d, a)]
    for l, t in zip(L, ['horizontal', 'vertical', 'horizontal', 'vertical']): s.add_constraint(t, l)
    s.merge_points(ORIGIN, a)
    if dims:
        s.add_constraint('distance', L[0], value=w); s.add_constraint('distance', L[1], value=h)
    s.solve(); return [ORIGIN, b, c, d], L
def test_sketch_fillet():
    s = Sketch(); P, L = rect(s)
    OPS.fillet_corner(s, P[2], 5)
    ok = s.status['ok']; a = areas(s)
    exp = 1200 - (1 - math.pi / 4) * 25
    assert ok and len(a) == 1 and abs(a[0] - exp) < 0.01, (ok, a, exp)
    assert s.status['dof'] == 0, s.status['dof']
    # change a dimension afterwards: rectangle stays rectangular with the fillet
    for c in s.constraints:
        if c['type'] == 'distance' and c.get('value') == 40: c['value'] = 60
    s.solve(); a = areas(s)
    assert s.status['ok'] and abs(a[0] - (1800 - (1 - math.pi / 4) * 25)) < 0.01, a
    return f'area {a[0]}, fully defined, survives resize'
def test_sketch_fillet_all4():
    s = Sketch(); P, L = rect(s)
    for p in P[1:]: OPS.fillet_corner(s, p, 4)
    OPS.fillet_corner(s, ORIGIN, 4)
    a = areas(s); exp = 1200 - 4 * (4 - math.pi) * 16 / 4 * 4 / 4
    exp = 1200 - (4 - math.pi) * 16
    assert abs(a[0] - exp) < 0.01, (a, exp); return f'area {a[0]}'
def test_sketch_offset():
    s = Sketch(); P, L = rect(s)
    OPS.offset(s, L[0], 5)
    a = areas(s)
    # outward offset of a 40x30 rectangle by 5 with rounded corners: 50*40 - 4 corners*(25 - pi*25/4)
    outer = 50 * 40     # sharp corners, like Onshape
    assert abs(sum(a) - outer) < 0.05, (a, outer)
    return f'regions {a} (outer total {round(sum(a),2)} vs expected {round(outer,2)})'
def test_sketch_offset_in():
    s = Sketch(); P, L = rect(s)
    OPS.offset(s, L[0], -5)
    a = areas(s); assert abs(min(a) - 30 * 20) < 0.01, a; return f'inner {min(a)}'
def test_sketch_offset_circle():
    s = Sketch(); c = s.add_point(0, 0); ci = s.add_circle(c, 10); s.solve()
    OPS.offset(s, ci, 3); a = areas(s)
    assert abs(sum(a) - math.pi * 169) < 0.05, a; return f'{a}'
def test_sketch_slot():
    s = Sketch(); OPS.slot(s, (0, 0), (30, 0), 10); s.solve(); a = areas(s)
    exp = 30 * 10 + math.pi * 25
    assert len(a) == 1 and abs(a[0] - exp) < 0.01, (a, exp); return f'area {a[0]}, ok={s.status["ok"]}'
def test_sketch_mirror():
    s = Sketch(); c = s.add_point(10, 5); ci = s.add_circle(c, 3)
    p1, p2 = s.add_point(0, -20), s.add_point(0, 20); ax = s.add_line(p1, p2, True); s.add_constraint('vertical', ax)
    s.add_constraint('point_on', ORIGIN, ax)
    s.solve(); new = OPS.mirror(s, [ci], ax)
    circles = [e for e in s.entities.values() if e['type'] == 'circle']
    cs = sorted(tuple(np.round(s.P(e['c']), 4)) for e in circles)
    assert cs == [(-10.0, 5.0), (10.0, 5.0)], cs
    # move the original: mirror follows
    s.points[c] = [15, 8]; s.solve(weights={(c, 0): 1e3, (c, 1): 1e3})
    cs = sorted(tuple(np.round(s.P(e['c']), 3)) for e in [e for e in s.entities.values() if e['type'] == 'circle'])
    assert cs == [(-15.0, 8.0), (15.0, 8.0)], cs
    return f'mirrored & linked {cs}'
def test_sketch_lpattern():
    s = Sketch(); c = s.add_point(0, 0); ci = s.add_circle(c, 2); s.solve()
    OPS.linear_pattern(s, [ci], (1, 0), 4, 10)
    xs = sorted(round(s.P(e['c'])[0], 4) for e in s.entities.values() if e['type'] == 'circle')
    assert xs == [0, 10, 20, 30], xs; return f'{xs}'
def test_sketch_cpattern():
    s = Sketch(); c = s.add_point(20, 0); ci = s.add_circle(c, 2); s.solve()
    OPS.circular_pattern(s, [ci], (0, 0), 6, 360)
    angs = sorted(round(math.degrees(math.atan2(*s.P(e['c'])[::-1])) % 360, 3) for e in s.entities.values() if e['type'] == 'circle')
    assert angs == [0, 60, 120, 180, 240, 300], angs; return f'{angs}'
def test_sketch_pattern_rect():
    s = Sketch(); P, L = rect(s, 10, 6)
    OPS.linear_pattern(s, L, (0, 1), 3, 15); a = areas(s)
    assert a == [60.0, 60.0, 60.0], a; return f'{a}'

def sketch(doc, plane, build):
    s = Sketch(); build(s); s.solve()
    f = doc.make_feature('sketch'); f['params'].update(plane=plane, sketch=s.to_dict()); doc.add_feature(f)
    return f['id']
def rect_at(s, x0, y0, w, h):
    p = [s.add_point(x0, y0), s.add_point(x0 + w, y0), s.add_point(x0 + w, y0 + h), s.add_point(x0, y0 + h)]
    return [s.add_line(a, b) for a, b in zip(p, p[1:] + p[:1])]
def regions(doc, sid): return [F.region_ref(doc.state, sid, r) for r in doc.state.sketches[sid].regions]
def box(doc, w=40, d=30, h=20, op='new'):
    sid = sketch(doc, {'kind': 'std', 'name': 'Top'}, lambda s: rect_at(s, 0, 0, w, d))
    ex = doc.make_feature('extrude'); ex['params'].update(regions=regions(doc, sid), depth=h, op=op); doc.add_feature(ex)
    return sid, ex
def vol(doc): return round(sum(K.volume(p.shape) for p in doc.state.parts.values()), 2)
def add(doc, ftype, **params):
    f = doc.make_feature(ftype); f['params'].update(params); doc.add_feature(f)
    err = doc.errors.get(f['id'])
    if err: raise AssertionError(f'{ftype}: {err}')
    return f

def test_part_variables():
    doc = Document()
    add(doc, 'variable', name='wall', expr='3')
    add(doc, 'variable', name='height', expr='#wall * 5 + 1')
    sid = sketch(doc, {'kind': 'std', 'name': 'Top'}, lambda s: rect_at(s, 0, 0, 20, 10))
    ex = add(doc, 'extrude', regions=regions(doc, sid), depth='#height', op='new')
    v1 = vol(doc); assert abs(v1 - 200 * 16) < 1e-3, v1
    # change the variable -> model follows
    doc.features[0]['params']['expr'] = '5'; doc.rebuild()
    v2 = vol(doc); assert abs(v2 - 200 * 26) < 1e-3 and not doc.errors, (v2, doc.errors)
    # unknown variable gives a clear error
    doc.features[3]['params']['depth'] = '#nope'; doc.rebuild()
    assert 'Unknown variable #nope' in doc.errors.get(ex['id'], ''), doc.errors
    return f'depth=#height: {v1} -> {v2}; bad name -> "{doc.errors[ex["id"]]}"'

def test_part_sketch_dim_variable():
    doc = Document()
    add(doc, 'variable', name='w', expr='25')
    def build(s):
        L = rect_at(s, 0, 0, 10, 10)
        for l, t in zip(L, ['horizontal', 'vertical', 'horizontal', 'vertical']): s.add_constraint(t, l)
        c = s.add_constraint('distance', L[0], value=10); c['expr'] = '#w'
    sid = sketch(doc, {'kind': 'std', 'name': 'Top'}, build)
    a = K.face_info(doc.state.sketches[sid].regions[0])['area']
    doc.features[0]['params']['expr'] = '40'; doc.rebuild()
    a2 = K.face_info(doc.state.sketches[sid].regions[0])['area']
    assert abs(a - 250) < 1e-3 and abs(a2 - 400) < 1e-3, (a, a2)
    return f'sketch dimension #w: area {a} -> {a2}'

def test_part_sweep():
    doc = Document()
    pid = sketch(doc, {'kind': 'std', 'name': 'Top'}, lambda s: s.add_circle(s.add_point(0, 0), 3))
    def path(s):
        a, b, c = s.add_point(0, 0), s.add_point(0, 40), s.add_point(30, 40)
        s.add_line(a, b); s.add_line(b, c)
    path_id = sketch(doc, {'kind': 'std', 'name': 'Front'}, path)
    line = next(k for k, e in doc.state.sketches[path_id].sketch.entities.items() if e['type'] == 'line')
    add(doc, 'sweep', regions=regions(doc, pid), path=[{'kind': 'sketch_chain', 'sketch': path_id, 'entity': line}], op='new')
    v = vol(doc); exp = math.pi * 9 * 70
    assert abs(v - exp) / exp < 0.02, (v, exp); return f'pipe volume {v} (≈{round(exp,1)}), {len(doc.state.parts)} part'

def test_part_loft():
    doc = Document()
    a = sketch(doc, {'kind': 'std', 'name': 'Top'}, lambda s: rect_at(s, -10, -10, 20, 20))
    add(doc, 'plane', ref={'kind': 'std', 'name': 'Top'}, offset=30)
    pl = doc.features[-1]['id']
    b = sketch(doc, {'kind': 'plane', 'fid': pl}, lambda s: s.add_circle(s.add_point(0, 0), 6))
    add(doc, 'loft', profiles=[{'kind': 'region', 'ref': regions(doc, a)[0]}, {'kind': 'region', 'ref': regions(doc, b)[0]}], op='new')
    v = vol(doc); lo, hi = K.bbox(next(iter(doc.state.parts.values())).shape)
    assert 400 * 30 * 0.3 < v < 400 * 30 and abs(hi[2] - 30) < 1e-3, (v, hi)
    return f'square→circle loft, volume {v}, height {round(hi[2]-lo[2],3)}'

def test_part_draft():
    doc = Document(); box(doc)
    part = next(iter(doc.state.parts.values()))
    sides = [f for f in K.faces(part.shape) if (n := K.face_info(f).get('n')) is not None and abs(n[2]) < 0.1]
    add(doc, 'draft', faces=[F.make_ref(part.id, f, 'face', part.shape) for f in sides], neutral={'kind': 'std', 'name': 'Top'}, angle=5.0)
    v = vol(doc); assert v < 24000 and K.is_valid(next(iter(doc.state.parts.values())).shape), v
    return f'5° draft on 4 walls: volume 24000 -> {v}'

def test_part_tapped_hole():
    doc = Document(); box(doc, 40, 30, 10)
    def pts(s):
        for x in (10, 30): s.add_point_entity(s.add_point(x, 15))
    hs = sketch(doc, {'kind': 'std', 'name': 'Top'}, pts)
    add(doc, 'hole', sketch=hs, standard='tapped', size='M4', end='through', style='simple')
    removed = 12000 - vol(doc)
    d = math.sqrt(removed / 2 / 10 / math.pi) * 2
    assert abs(d - 3.3) < 0.05, d
    return f'M4 tapped → Ø{round(d,3)} tap drill (ISO 3.3)'

def test_part_clearance_cbore():
    doc = Document(); box(doc, 40, 30, 10)
    hs = sketch(doc, {'kind': 'std', 'name': 'Top'}, lambda s: s.add_point_entity(s.add_point(20, 15)))
    add(doc, 'hole', sketch=hs, standard='clearance', size='M5', fit='normal', end='through', style='counterbore')
    return f'M5 counterbored: removed {round(12000 - vol(doc), 1)} mm³'

def test_part_split():
    doc = Document(); box(doc, 40, 30, 20)
    add(doc, 'plane', ref={'kind': 'std', 'name': 'Right'}, offset=10)
    add(doc, 'split', parts=list(doc.state.parts), plane={'kind': 'plane', 'fid': doc.features[-1]['id']}, keep='both')
    vols = sorted(round(K.volume(p.shape), 2) for p in doc.state.parts.values())
    assert vols == [6000.0, 18000.0], vols; return f'split at x=10 → {vols}'

def test_part_move():
    doc = Document(); box(doc, 10, 10, 10)
    pid = next(iter(doc.state.parts))
    add(doc, 'move', parts=[pid], dx=50.0, dy=0.0, dz=5.0, rotate=0.0, copy=True)
    los = sorted(tuple(np.round(K.bbox(p.shape)[0], 3)) for p in doc.state.parts.values())
    assert los == [(0, 0, 0), (50, 0, 5)], los
    add(doc, 'move', parts=[pid], dx=0.0, dy=0.0, dz=0.0, axis_std='Z', axis={'kind': 'std', 'name': 'Z'}, rotate=90.0, copy=False)
    lo, hi = K.bbox(doc.state.parts[pid].shape)
    assert np.allclose(lo, [-10, 0, 0], atol=1e-4), lo
    return f'copy moved to (50,0,5); rotate 90° about Z → {np.round(lo,2)}..{np.round(hi,2)}'



if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                print("ok  ", name, "-", fn())
            except Exception:
                failed += 1
                print("FAIL", name)
                traceback.print_exc()
    sys.exit(1 if failed else 0)
