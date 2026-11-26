"""Smart snapping: preview, parametric commit, and bypass."""
import numpy as np
from PySide6.QtCore import Qt
from freeshape.sketch import Sketch
from freeshape.ui import smart_snap as SS
from test_sketch_inference import VP, _Ev, _sketcher, _click, _cons


def _at(s, q, dx=0, dy=0):
    x,y = s.scr(VP,q)
    return float(x+dx),float(y+dy)


def test_intersection_commit_and_undo():
    sk=Sketch()
    a,b,c,d=[sk.add_point(*q) for q in [(10,10),(30,30),(10,30),(30,10)]]
    l1,l2=sk.add_line(a,b),sk.add_line(c,d)
    s=_sketcher(sk)
    s.set_tool("point")
    x,y=_at(s,[20,20],2,2)
    before=sk.to_dict()
    s.mouse_move(VP,x,y,_Ev())
    assert s.snap_info[0]=="intersection"
    assert sk.to_dict()==before
    s.mouse_press(VP,x,y,_Ev())
    pts=[p for p in sk.points if p not in before["points"]]
    assert len(pts)==1
    assert {r[1] for t,r in _cons(s) if t=="point_on" and r[0]==pts[0]}=={l1,l2}
    assert np.allclose(sk.P(pts[0]),[20,20])
    s.undo()
    assert s.sk.to_dict()==before


def test_finite_intersections_and_arc_midpoint():
    sk=Sketch()
    a,b,c,d=[sk.add_point(*q) for q in [(10,10),(20,10),(30,0),(30,20)]]
    l1,l2=sk.add_line(a,b),sk.add_line(c,d)
    assert SS.crossings(sk,l1,l2)==[]
    center=sk.add_point(40,40)
    p1,p2=sk.add_point(50,40),sk.add_point(40,50)
    arc=sk.add_arc(center,p1,p2)
    s=_sketcher(sk)
    s.set_tool("point")
    target=np.array([40,40])+10/np.sqrt(2)
    _click(s,*_at(s,target,1,1))
    matches=[r[0] for t,r in _cons(s) if t=="midpoint" and r[1]==arc]
    assert len(matches)==1
    assert np.allclose(sk.P(matches[0]),target,atol=1e-5)


def test_rectangle_center_is_parametric():
    sk=Sketch()
    ids=[sk.add_point(*q) for q in [(10,10),(30,10),(30,26),(10,26)]]
    for a,b in zip(ids,ids[1:]+ids[:1]):
        sk.add_line(a,b)
    s=_sketcher(sk)
    s.set_tool("point")
    before=sk.to_dict()
    x,y=_at(s,[20,18],1,2)
    s.mouse_move(VP,x,y,_Ev())
    assert s.snap_info[0]=="rect_center"
    assert sk.to_dict()==before
    s.mouse_press(VP,x,y,_Ev())
    mid=[c for c in sk.constraints if c["type"]=="midpoint"][0]
    pid,diag=mid["refs"]
    assert sk.entities[diag]["helper"]
    assert np.allclose(sk.P(pid),[20,18])
    # Constrain the corners to resized positions; the centre must follow.
    for p,q in zip(ids,[(10,10),(50,10),(50,26),(10,26)]):
        sk.add_constraint("fix",p,value=q)
    assert sk.solve()
    assert np.allclose(sk.P(pid),[30,18],atol=1e-5)
    restored=Sketch.from_dict(sk.to_dict())
    assert restored.solve()
    assert np.allclose(restored.P(pid),[30,18],atol=1e-5)


def test_quadrant_and_shift_bypass():
    sk=Sketch()
    c=sk.add_point(20,20)
    cid=sk.add_circle(c,10)
    s=_sketcher(sk)
    s.set_tool("point")
    x,y=_at(s,[30,20],1,1)
    s.mouse_move(VP,x,y,_Ev())
    assert s.snap_info[0]=="quadrant"

    class Shift:
        def modifiers(self):
            return Qt.ShiftModifier
    s.mouse_move(VP,x,y,Shift())
    assert s.snap_info is None
    assert not s.infer_hits
    s.mouse_press(VP,x,y,Shift())
    assert not sk.constraints
    # Move away from the unconstrained point and test the opposite quadrant.
    _click(s,*_at(s,[10,20],1,1))
    assert any(t=="point_on" and r[1]==cid for t,r in _cons(s))
    assert any(t=="horizontal" and r[0]==c for t,r in _cons(s))


def test_parallel_direction_uses_awakened_line():
    sk=Sketch()
    a,b=sk.add_point(10,10),sk.add_point(30,20)
    ref=sk.add_line(a,b)
    s=_sketcher(sk)
    s.set_tool("line")
    s.mouse_move(VP,*_at(s,[15,12.5]),_Ev())
    assert ref in s._wake_entities
    _click(s,*_at(s,[10,40]))
    x,y=_at(s,[26,48],0,2)
    s.mouse_move(VP,x,y,_Ev())
    assert s._direction_infer==("parallel",ref)
    s.mouse_press(VP,x,y,_Ev())
    assert any(t=="parallel" and r[1]==ref for t,r in _cons(s))


def test_screen_pixel_tolerance():
    src=np.array([0.,0.])
    screen=lambda q:np.asarray(q)*10
    assert SS.axis_distance(screen,src,[100,5],"H")==5
    assert SS.axis_distance(screen,src,[1000,5],"H")==5


def test_helper_diagonal_circle_intersection_line_start():
    sk=Sketch()
    a,b=sk.add_point(10,10),sk.add_point(50,50)
    diag=sk.add_line(a,b,True)
    sk.entities[diag]["helper"]=True
    c=sk.add_point(30,30)
    sk.add_constraint("midpoint",c,diag)
    cid=sk.add_circle(c,10)
    sk.add_constraint("fix",a,value=[10,10])
    sk.add_constraint("fix",b,value=[50,50])
    radius=sk.add_constraint("radius",cid,value=10)
    s=_sketcher(sk)
    s.set_tool("line")
    q=np.array([30.,30.])+10/np.sqrt(2)
    before=sk.to_dict()
    x,y=_at(s,q,2,-1)
    s.mouse_move(VP,x,y,_Ev())
    assert s.snap_info[0]=="intersection"
    assert set(s.snap_info[1])=={diag,cid}
    assert set(s._snap_sources)=={diag,cid}
    assert sk.to_dict()==before
    s.mouse_press(VP,x,y,_Ev())
    p=s.chain_anchor
    assert p is not None
    assert {r[1] for t,r in _cons(s) if t=="point_on" and r[0]==p}=={diag,cid}
    assert np.allclose(sk.P(p),q)
    _click(s,*_at(s,[50,65]))
    radius["value"]=15
    assert sk.solve()
    expected=np.array([30.,30.])+15/np.sqrt(2)
    assert np.allclose(sk.P(p),expected,atol=1e-5)
    assert Sketch.from_dict(sk.to_dict()).solve()


def test_helper_diagonal_arc_intersection_rejects_extended_arc():
    sk=Sketch()
    a,b=sk.add_point(10,10),sk.add_point(50,50)
    diag=sk.add_line(a,b,True)
    sk.entities[diag]["helper"]=True
    c=sk.add_point(30,30)
    p1,p2=sk.add_point(40,30),sk.add_point(30,40)
    aid=sk.add_arc(c,p1,p2)
    crossings=SS.crossings(sk,diag,aid)
    assert len(crossings)==1
    assert np.allclose(crossings[0],np.array([30.,30.])+10/np.sqrt(2))
