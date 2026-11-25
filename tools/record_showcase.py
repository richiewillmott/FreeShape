"""Records a FreeShape showcase video: a phone stand built in the real app, driven by a script, with a drawn mouse
pointer. Usage:  python record_showcase.py OUT.mp4        (on the desktop: the window takes over the screen)
                 python record_showcase.py --dry           (headless check of the steps, no video)"""
import math
import os
import subprocess
import sys
import tempfile
import time

REPO = r"C:\Users\Richardw\Documents\GitHub\FreeShape"
sys.path.insert(0, REPO)
DRY = "--dry" in sys.argv
OUT = next((a for a in sys.argv[1:] if not a.startswith("--")), None)
if DRY:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ["FREESHAPE_NO_PLUGINS"] = "1"
os.environ["FREESHAPE_AUTOSAVE_DIR"] = tempfile.mkdtemp(prefix="freeshape-showcase-")

import numpy as np  # noqa: E402
from PySide6.QtCore import Qt, QPoint, QPointF, QRectF, QEvent  # noqa: E402
from PySide6.QtGui import (QSurfaceFormat, QImage, QPainter, QColor, QPen, QPolygonF, QFont,  # noqa: E402
                           QMouseEvent, QPainterPath)
from PySide6.QtWidgets import QApplication, QToolButton  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

W, H, FPS = 1920, 1080, 30
fmt = QSurfaceFormat()
fmt.setSamples(4)
fmt.setDepthBufferSize(24)
QSurfaceFormat.setDefaultFormat(fmt)
app = QApplication(sys.argv)
app.setApplicationName("FreeShape")

from freeshape import kernel as K, features as F  # noqa: E402
from freeshape.document import Document  # noqa: E402
from freeshape.sketch import ORIGIN  # noqa: E402
from freeshape.ui.mainwindow import MainWindow, app_icon  # noqa: E402
from freeshape.ui.viewport import Pick, basis  # noqa: E402
from freeshape.ui.panels import ROLE_ID, ROLE_KIND  # noqa: E402

app.setWindowIcon(app_icon())
w = MainWindow()
w.setWindowFlag(Qt.FramelessWindowHint)
w.setWindowFlag(Qt.WindowStaysOnTopHint)
w.setGeometry(0, 0, W, H)
w.show()
doc = Document()
doc.name = "Phone stand"
w._add_tab(doc)
for i in range(w.tabs.count() - 1, -1, -1):       # close the empty "Part Studio 1" the window starts with
    other = w.docs.get(w.tabs.tabData(i))
    if other is not None and other is not doc:
        other.dirty = False
        w.close_tab(i)
w.tabs.setCurrentIndex(w._tab_index(doc))
for _ in range(20):
    app.processEvents()
vp = w.viewport
if not DRY:
    from PySide6.QtGui import QCursor
    QCursor.setPos(W - 2, H + 100)                  # the real pointer out of the way (it isn't recorded)
    w.raise_()
    w.activateWindow()


# ============================================================================ recorder
class Recorder:
    def __init__(self, path):
        self.t0 = time.perf_counter()
        self.frame = 0
        self.keys = [(0.0, W * 0.55, H * 0.55)]       # cursor (t, x, y) in window pixels
        self.clicks = []                               # (t, x, y)
        self.captions = []                             # [t0, t1, text]
        self.proc = None
        self.vmap = [(0.0, 0.0)]                       # (real time, video time): stalls are squeezed out
        if path and not DRY:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
            self.proc = subprocess.Popen(
                [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                 "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "slow", "-crf", "18",
                 "-pix_fmt", "yuv420p", "-movflags", "+faststart", path], stdin=subprocess.PIPE)

    def now(self):
        return time.perf_counter() - self.t0

    # -- cursor
    def cursor_at(self, t):
        ks = self.keys
        if t <= ks[0][0]:
            return ks[0][1:]
        for (ta, xa, ya), (tb, xb, yb) in zip(ks, ks[1:]):
            if ta <= t <= tb:
                f = (t - ta) / max(tb - ta, 1e-9)
                return xa + (xb - xa) * f, ya + (yb - ya) * f
        return ks[-1][1:]

    def put(self, x, y):
        self.keys.append((self.now(), float(x), float(y)))

    # -- frames
    CAP = 0.06                                         # longest a gap between captures lasts in the video

    def _real_of(self, vt):
        vm = self.vmap
        for (ra, va), (rb, vb) in zip(vm, vm[1:]):
            if va <= vt <= vb:
                return ra + (rb - ra) * ((vt - va) / max(vb - va, 1e-9))
        return vm[-1][0]

    def tick(self):
        app.processEvents()
        if self.proc is None:
            return
        img = app.primaryScreen().grabWindow(0, 0, 0, W, H).toImage().convertToFormat(QImage.Format_RGB888)
        rt = self.now()
        r0, v0 = self.vmap[-1]
        self.vmap.append((rt, v0 + min(rt - r0, self.CAP)))
        vt = self.vmap[-1][1]
        while self.frame / FPS <= vt:
            self._write(img, self._real_of(self.frame / FPS))
            self.frame += 1

    def _write(self, base, t):
        img = base.copy()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        for (tc, xc, yc) in self.clicks:                 # click ripples
            a = t - tc
            if 0 <= a < 0.45:
                r = 8 + a * 70
                c = QColor(31, 111, 209, int(200 * (1 - a / 0.45)))
                p.setPen(QPen(c, 3))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(xc, yc), r, r)
        for t0, t1, text in self.captions:              # caption bar
            if t0 <= t <= t1:
                fade = min(1.0, (t - t0) / 0.25, (t1 - t) / 0.25)
                f = QFont("Segoe UI")
                f.setPixelSize(34)
                f.setWeight(QFont.DemiBold)
                p.setFont(f)
                tw = p.fontMetrics().horizontalAdvance(text)
                box = QRectF(W / 2 - tw / 2 - 28, H - 128, tw + 56, 64)
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(20, 24, 31, int(215 * fade)))
                p.drawRoundedRect(box, 14, 14)
                p.setPen(QColor(255, 255, 255, int(255 * fade)))
                p.drawText(box, Qt.AlignCenter, text)
        x, y = self.cursor_at(t)                         # the pointer
        s = 1.45
        pts = [(0, 0), (0, 17), (4.2, 13.2), (7, 19.6), (9.6, 18.4), (6.9, 12.2), (12.2, 12.2)]
        poly = QPolygonF([QPointF(x + px * s, y + py * s) for px, py in pts])
        p.setPen(QPen(QColor(0, 0, 0), 1.6))
        p.setBrush(QColor(255, 255, 255))
        p.drawPolygon(poly)
        p.end()
        self.proc.stdin.write(bytes(img.constBits())[: W * H * 3])

    def close(self):
        if self.proc is not None:
            self.proc.stdin.close()
            self.proc.wait()


rec = Recorder(OUT)


def wait(sec):
    end = rec.now() + sec
    while rec.now() < end:
        rec.tick()
        if DRY:
            time.sleep(0.005)


def caption(text, sec):
    rec.captions.append([rec.now(), rec.now() + sec, text])


def win_pos(widget, pt):
    q = widget.mapTo(w, QPoint(int(pt[0]), int(pt[1])))
    return q.x(), q.y()


def _send(widget, kind, local, button=Qt.NoButton, buttons=Qt.NoButton, mods=Qt.NoModifier):
    gl = widget.mapToGlobal(QPoint(int(local[0]), int(local[1])))
    ev = QMouseEvent(kind, QPointF(*local), QPointF(gl), button, buttons, mods)
    QApplication.sendEvent(widget, ev)


def move_to(x, y, dur=0.55, hover_widget=None):
    """Glide the pointer to window point (x, y); hover_widget gets mouse-move events on the way (for previews)."""
    x0, y0 = rec.cursor_at(rec.now())
    start = rec.now()
    while True:
        f = min(1.0, (rec.now() - start) / dur)
        e = f * f * (3 - 2 * f)
        cx, cy = x0 + (x - x0) * e, y0 + (y - y0) * e
        rec.put(cx, cy)
        if hover_widget is not None:
            loc = hover_widget.mapFrom(w, QPoint(int(cx), int(cy)))
            _send(hover_widget, QEvent.MouseMove, (loc.x(), loc.y()))
        rec.tick()
        if f >= 1.0:
            break


def ripple():
    x, y = rec.cursor_at(rec.now())
    rec.clicks.append((rec.now(), x, y))


def click_button(btn, dur=0.55):
    c = btn.rect().center()
    move_to(*win_pos(btn, (c.x(), c.y())), dur)
    ripple()
    btn.click()
    wait(0.25)


def vp_screen(world):
    s = vp.cam.to_screen(np.asarray(world, float))
    return float(s[0]), float(s[1])


def vp_move(local, dur=0.45):
    move_to(*win_pos(vp, local), dur, hover_widget=vp)


def vp_click(local, dur=0.45):
    vp_move(local, dur)
    ripple()
    _send(vp, QEvent.MouseButtonPress, local, Qt.LeftButton, Qt.LeftButton)
    rec.tick()
    _send(vp, QEvent.MouseButtonRelease, local, Qt.LeftButton, Qt.NoButton)
    wait(0.12)


def vp_drag(a, b, dur=1.0, pause_at=None):
    """Press at a, drag to b (each point a (x, y) in the 3D view), release."""
    vp_move(a, 0.4)
    ripple()
    _send(vp, QEvent.MouseButtonPress, a, Qt.LeftButton, Qt.LeftButton)
    start = rec.now()
    while True:
        f = min(1.0, (rec.now() - start) / dur)
        e = f * f * (3 - 2 * f)
        loc = (a[0] + (b[0] - a[0]) * e, a[1] + (b[1] - a[1]) * e)
        rec.put(*win_pos(vp, loc))
        _send(vp, QEvent.MouseMove, loc, Qt.NoButton, Qt.LeftButton)
        rec.tick()
        if f >= 1.0:
            break
    _send(vp, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton)
    wait(0.15)


def tool_button(name):
    for b in w.findChildren(QToolButton):
        if b.property("icon_name") == name and b.isVisible():
            return b
    raise RuntimeError(f"no button {name}")


def finish_button():
    for b in w.findChildren(QToolButton):
        if "Finish sketch" in (b.text() or "") and b.isVisible():
            return b
    raise RuntimeError("no Finish sketch button")


def dialog_ok():
    return w.dialog.findChild(QToolButton, "okButton")


def list_row(kind, ident):
    lst = w.left.features
    for i in range(lst.count()):
        it = lst.item(i)
        if it.data(ROLE_KIND) == kind and it.data(ROLE_ID) == ident:
            r = lst.visualItemRect(it)
            return lst, (r.left() + 60, r.center().y())
    raise RuntimeError(f"no row {kind} {ident}")


def frame_view(target, R, scale, dur=0.6):
    vp.animate_to((np.asarray(target, float), R, scale), duration=dur)
    wait(dur + 0.05)


# ============================================================================ the phone stand
FRONT = K.std_plane("Front")
T = 6.0                                     # wall thickness
ANG = 65.0                                  # back angle
# side profile (u along the base, v up), drawn corner by corner
c, s_ = math.cos(math.radians(ANG)), math.sin(math.radians(ANG))
P = [(0, 0), (100, 0), (100, 16), (94, 16), (94, 6), (50, 6)]
P7 = (50 - 100 * c, 6 + 100 * s_)
P8 = (P7[0] - T * s_, P7[1] - T * c)
P9 = (P8[0] + (P8[1] - 6) / math.tan(math.radians(ANG)), 6)
P += [P7, P8, P9, (0, 6)]


def world(u, v):
    return FRONT.to_world(u, v)


def stand_view(zoom=1.0, target=None):
    """Front-right-above, so the lip, the inside corner and the leaning back all show; framed for the finished
    stand (100 long, 80 wide, about 97 tall)."""
    R3 = basis(np.array([1.0, 1.25, 0.75]) * np.array([1, -float(np.sign(FRONT.normal[1]) or 1), 1]),
               np.array([0.0, 0.0, 1.0]))
    corners = [world(u, v) + FRONT.normal * d for u in (-15, 105) for v in (-2, 100) for d in (0, 80)]
    lo, hi = np.min(corners, axis=0), np.max(corners, axis=0)
    tgt, sc = vp.cam.fit_target(lo, hi, R3)
    return (tgt if target is None else np.asarray(target, float)), R3, sc * zoom


try:
    caption("FreeShape 2.6 — free, offline, Onshape-style CAD", 2.6)
    vp.set_standard_view("Isometric", animate=False)
    wait(1.2)

    print("T %5.1f  # ---- 1. sketch on Front" % rec.now(), flush=True)
    # ---- 1. sketch on Front
    lst, pt = list_row("std_plane", "Front")
    move_to(*win_pos(lst.viewport(), pt))
    ripple()
    QTest.mouseClick(lst.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(*map(int, pt)))
    wait(0.3)
    click_button(tool_button("sketch"))
    caption("Sketch the side profile", 6.5)
    R_front = basis(FRONT.normal, FRONT.ydir)
    frame_view(world(45, 46), R_front, 0.135, 0.6)
    QTest.keyClick(vp, Qt.Key_L)
    wait(0.2)
    for (u, v) in P + [P[0]]:
        vp_click(vp_screen(world(u, v)), 0.21)
    QTest.keyClick(vp, Qt.Key_Escape)
    wait(0.2)

    print("T %5.1f  # constraints and dimensions" % rec.now(), flush=True)
    # constraints and dimensions (the drawn shape, made exact and driven by its sizes)
    sk = w.sketcher.sk
    lines = [eid for eid, e in sk.entities.items() if e["type"] == "line"]
    assert len(lines) == 10, f"expected 10 lines, got {len(lines)}"
    have = {(cc["type"], tuple(cc["refs"])) for cc in sk.constraints}

    def con(t, *refs, value=None, pos=None):
        if value is None and (t, tuple(refs)) in have:
            return None
        cc = sk.add_constraint(t, *refs, value=value)
        if pos is not None:
            cc["pos"] = [float(pos[0]), float(pos[1])]
        return cc
    L = lines
    p1 = sk.entities[L[0]]["p1"]
    if not any(cc["type"] == "coincident" and ORIGIN in cc["refs"] for cc in sk.constraints):
        con("coincident", p1, ORIGIN)
    for i in (0, 2, 4, 8):
        con("horizontal", L[i])
    for i in (1, 3, 9):
        con("vertical", L[i])
    con("distance", L[0], value=100, pos=(50, -9))
    con("distance", L[1], value=16, pos=(110, 8))
    con("distance", L[2], value=6, pos=(97, 24))
    con("distance", L[3], value=10, pos=(86, 12))
    con("distance", L[4], value=44, pos=(72, 13))
    angle = con("angle", L[5], L[4], value=ANG, pos=(60, 24))
    con("distance", L[5], value=100, pos=(15, 60))
    con("perpendicular", L[5], L[6])
    con("distance", L[6], value=T, pos=(P7[0] - 10, P7[1] + 8))
    con("parallel", L[5], L[7])
    con("distance", L[9], value=6, pos=(-9, 3))
    assert sk.solve(), sk.status
    w.sketcher.changed(True)
    caption("Dimension it — the back leans at 65°", 2.4)
    wait(1.8)
    click_button(finish_button())
    sk_fid = doc.features[-1]["id"]
    wait(0.3)

    print("T %5.1f  # ---- 2. extrude" % rec.now(), flush=True)
    # ---- 2. extrude
    caption("Extrude — drag the arrow", 4.6)
    frame_view(*stand_view(), 0.8)
    move_to(*win_pos(vp, vp_screen(world(70, 3))))
    ripple()
    vp.set_selection([Pick("region", (sk_fid, 0))])
    wait(0.3)
    click_button(tool_button("extrude"))
    wait(0.6)
    m = vp.manipulators[0]
    base, tip, sd = vp._manip_screen(m)
    grab = (tip[0] + sd[0] * 10, tip[1] + sd[1] * 10)
    goal_world = np.asarray(m["origin"], float) + np.asarray(m["dir"], float) * 80.0
    gs = vp_screen(goal_world)
    vp_drag(grab, (gs[0] + sd[0] * 10, gs[1] + sd[1] * 10), dur=1.1)
    d = w.dialog
    d.feature["params"]["depth"] = 80.0                   # (exactly 80: the drag snaps near it)
    d.schedule_preview()
    wait(0.6)
    click_button(dialog_ok())
    wait(0.5)
    pid = next(iter(doc.state.parts))

    def edge_at(u, v):
        # the edge on the model the open feature starts from (what a click on it picks)
        shape = (w.dialog.base_state if w.dialog is not None else doc.state).parts[pid].shape
        best = None
        for i, e in enumerate(K.edges(shape)):
            ln = K.edge_line(e)
            if ln is None or abs(float(np.dot(ln[1], FRONT.normal))) < 0.99:
                continue
            cu, cv = FRONT.to_local(np.asarray(K.edge_info(e)["c"], float))
            dd = math.hypot(cu - u, cv - v)
            if best is None or dd < best[0]:
                best = (dd, i, e)
        return best[1], best[2]

    print("T %5.1f  # ---- 3. the inside corner" % rec.now(), flush=True)
    def pick_edge(e):
        d_ = w.dialog
        shape = d_.base_state.parts[pid].shape
        d_.active_field.toggle(("edge", F.make_ref(pid, e, "edge", shape)))

    # ---- 3. the inside corner: fillet with the arrow, past the limit (purple) and back
    caption("Fillet — drag the radius (purple = too big to fit)", 7.0)
    frame_view(*stand_view(0.86, world(50, 35) + FRONT.normal * 40), 0.6)
    click_button(tool_button("fillet"))
    ei, e_in = edge_at(50, 6)
    mid = np.asarray(K.edge_info(e_in)["c"], float)
    move_to(*win_pos(vp, vp_screen(mid)))
    ripple()
    pick_edge(e_in)
    w.dialog._do_preview()
    wait(0.6)
    m = vp.manipulators[0]
    k = m["value"] / float(w.dialog.feature["params"]["radius"])
    base, tip, sd = vp._manip_screen(m)
    grab = (tip[0] + sd[0] * 10, tip[1] + sd[1] * 10)

    def arrow_screen(r):
        t = vp_screen(np.asarray(m["origin"], float) + np.asarray(m["dir"], float) * r * k)
        return (t[0] + sd[0] * 10, t[1] + sd[1] * 10)
    # too big, hold, then back to one that fits
    _send(vp, QEvent.MouseButtonPress, grab, Qt.LeftButton, Qt.LeftButton)
    vp_move(grab, 0.3)
    for (r_to, dur) in ((100.0, 1.3), (25.0, 1.0)):
        if r_to == 25.0:
            wait(1.5)                                     # past the limit: the round in purple, its edge red
            print("outlines while too big:", len(vp.outlines), "radius", w.dialog.feature["params"].get("radius"),
                  "err shown:", w.dialog.err.isVisible(),
                  "manip value", vp.manipulators[0]["value"] if vp.manipulators else None, flush=True)
        a = rec.cursor_at(rec.now())
        a = vp.mapFrom(w, QPoint(int(a[0]), int(a[1])))
        a = (a.x(), a.y())
        b = arrow_screen(r_to)
        start = rec.now()
        while True:
            f = min(1.0, (rec.now() - start) / dur)
            e_ = f * f * (3 - 2 * f)
            loc = (a[0] + (b[0] - a[0]) * e_, a[1] + (b[1] - a[1]) * e_)
            rec.put(*win_pos(vp, loc))
            _send(vp, QEvent.MouseMove, loc, Qt.NoButton, Qt.LeftButton)
            rec.tick()
            if f >= 1.0:
                break
    _send(vp, QEvent.MouseButtonRelease, b, Qt.LeftButton, Qt.NoButton)
    w.dialog.feature["params"]["radius"] = 25.0
    w.dialog.schedule_preview()
    wait(0.7)
    click_button(dialog_ok())
    wait(0.4)

    print("T %5.1f  # ---- 4. round the top edges" % rec.now(), flush=True)
    # ---- 4. round the top edges
    caption("Round the top edges", 2.6)
    frame_view(*stand_view(0.85), 0.5)
    click_button(tool_button("fillet"))
    for (u, v) in (P7, P8):
        ei, e_ = edge_at(u, v)
        mid = np.asarray(K.edge_info(e_)["c"], float)
        move_to(*win_pos(vp, vp_screen(mid)), 0.35)
        ripple()
        pick_edge(e_)
        wait(0.1)
    w.dialog.feature["params"]["radius"] = 2.0
    w.dialog._do_preview()
    wait(0.5)
    click_button(dialog_ok())
    wait(0.5)

    print("T %5.1f  # ---- 5. change the angle" % rec.now(), flush=True)
    # ---- 5. change the angle: everything rebuilds
    caption("Change one angle…", 3.6)
    lst, pt = list_row("feature", sk_fid)
    move_to(*win_pos(lst.viewport(), pt))
    ripple()
    w.edit_feature(sk_fid)
    wait(0.9)
    sketcher = w.sketcher
    ang = next(cc for cc in sketcher.sk.constraints if cc["type"] == "angle")
    r = sketcher._dim_rects.get(ang["id"])
    spot = (r.center().x(), r.center().y()) if r is not None else vp_screen(world(*ang["pos"]))
    vp_move(spot, 0.5)
    ripple()
    sketcher.edit_dimension(vp, ang)
    wait(0.35)
    ed = sketcher._editor
    if ed is not None:
        ed.selectAll()
        for ch in "50":
            QTest.keyClicks(ed, ch)
            wait(0.12)
        wait(0.3)
        QTest.keyClick(ed, Qt.Key_Return)
    wait(0.9)
    print("T %5.1f  rebuild" % rec.now(), flush=True)
    caption("…and everything rebuilds", 4.6)
    click_button(finish_button())
    wait(0.4)
    frame_view(*stand_view(), 0.7)
    pivot = vp.cam.state()[0]
    start = rec.now()
    last = 0.0
    while rec.now() - start < 3.0:                    # a slow quarter-turn-ish look round, about its centre
        f = (rec.now() - start) / 3.0
        e = f * f * (3 - 2 * f)
        vp.cam.orbit(-(e - last) * 65.0, 0.0, pivot)          # 65 px = about 30°
        last = e
        vp.update()
        rec.put(*rec.cursor_at(rec.now()))
        rec.tick()
    from freeshape.sketch import Sketch as _S
    print("angle in model:", [cc["value"] for cc in _S.from_dict(doc.feature(sk_fid)["params"]["sketch"]).constraints if cc["type"] == "angle"], flush=True)
    if doc.errors:
        f4 = doc.features[-1]
        before = doc.state_before(len(doc.features) - 1)
        part = before.parts[pid]
        lo, hi = K.bbox(part.shape)
        scale = float(np.linalg.norm(hi - lo))
        for ref in f4["params"]["edges"]:
            scores = []
            for e in K.edges(part.shape):
                info = K.edge_info(e)
                sc = F._score(info, ref, scale, [lo, hi])
                pen = F.context_penalty(part.shape, e, "edge", ref) if sc < 0.6 else None
                scores.append((round(sc, 3), pen, np.round(info["c"], 1).tolist()))
            scores.sort(key=lambda x: x[0])
            print("ref c", np.round(ref["c"], 1).tolist(), "best:", scores[:3], flush=True)
    assert not doc.errors, doc.errors
    caption("FreeShape — free & open source · github.com/richiewillmott/FreeShape", 3.0)
    move_to(W * 0.93, H * 0.35, 0.8)
    wait(2.4)
    print("angle now", [cc["value"] for cc in Sk_after.constraints if cc["type"] == "angle"] if False else "", flush=True)
    print("OK", "errors:", doc.errors, "features:", [f["type"] for f in doc.features],
          "wall %.1fs, video %.1fs" % (rec.now(), rec.vmap[-1][1]))
finally:
    rec.close()
    doc.dirty = False
    w.close()
