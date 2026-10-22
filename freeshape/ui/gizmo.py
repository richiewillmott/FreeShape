"""The move / rotate triad for assemblies, like Onshape's.

Click a part and a triad appears where you clicked, lined up with the part's own axes:

* an arrow per axis — drag it to slide the part along that axis,
* a small ring at the end of each arrow — drag it to turn the part about that axis (an arc and the angle
  show while you drag),
* a small square between each pair of arrows — drag it to slide the part in that plane,
* the ball in the middle — drag it to move the part freely in the screen plane.

After a drag, the distance or angle stays on screen; click it and type an exact value.
The triad lives in the part's own coordinates, so it follows the part through undo, mates and so on.
Everything is drawn with QPainter on top of the model, so it is never hidden behind a part.
"""
import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QLineEdit

LEN_PX = 82          # arrow length on screen
SHAFT = 0.70         # where the shaft ends and the cone starts (fraction of the arrow)
CONE_END = 0.90
RING_AT = 1.0        # the rotate ring sits just past the cone
RING_R = 0.11       # ring radius (fraction of the arrow)
SQ0, SQ1 = 0.22, 0.40  # the plane squares

AXIS_COLORS = ((214, 60, 52), (64, 160, 72), (52, 102, 214))
ACTIVE = QColor(35, 120, 230)


def _rot(axis, ang):
    a = np.asarray(axis, float)
    a = a / (np.linalg.norm(a) or 1.0)
    K_ = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
    return np.eye(3) + math.sin(ang) * K_ + (1 - math.cos(ang)) * (K_ @ K_)


def motion(R=None, about=None, t=None):
    """4x4 world motion: rotate by R about the point `about`, then translate by t."""
    M = np.eye(4)
    if R is not None:
        c = np.zeros(3) if about is None else np.asarray(about, float)
        M[:3, :3] = R
        M[:3, 3] = c - R @ c
    if t is not None:
        M[:3, 3] += np.asarray(t, float)
    return M


def _ray_plane(o, d, p0, n):
    den = float(d @ n)
    if abs(den) < 1e-9:
        return None
    return o + d * (float((p0 - o) @ n) / den)


def _seg_dist(p, a, b):
    ab = b - a
    L2 = float(ab @ ab)
    t = 0.0 if L2 < 1e-12 else float(np.clip((p - a) @ ab / L2, 0, 1))
    return float(np.linalg.norm(p - (a + ab * t))), t


def _in_poly(p, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if (a[1] > p[1]) != (b[1] > p[1]):
            x = a[0] + (p[1] - a[1]) * (b[0] - a[0]) / (b[1] - a[1])
            if x > p[0]:
                inside = not inside
    return inside


class Gizmo:
    """The triad's state, drawing, hit testing and drag maths. `ctl` is the AssemblyController; it applies the
    motions (so mates are respected) via ctl.apply_motion(iid, M, T0s)."""

    def __init__(self, ctl):
        self.ctl = ctl
        self.doc = None
        self.inst = None
        self.lo = None             # triad origin in part coordinates
        self.lR = np.eye(3)        # triad axes in part coordinates (columns)
        self.hover = None
        self.drag = None           # the drag in progress
        self.last = None           # the last drag (its value can be typed in afterwards)
        self.editor = None

    # ------------------------------------------------------------------ state
    @property
    def vp(self):
        return self.ctl.vp

    def active(self):
        doc = self.ctl.doc
        if self.inst is None or doc is not self.doc or self.ctl.w.mode != "idle":
            return False
        inst = doc.instance(self.inst)
        return inst is not None and not inst.get("hidden")

    def place(self, iid, world_point):
        doc = self.ctl.doc
        T = doc.T(iid)
        Ti = np.linalg.inv(T)
        self.doc, self.inst = doc, iid
        self.lo = Ti[:3, :3] @ np.asarray(world_point, float) + Ti[:3, 3]
        self.lR = np.eye(3)
        self.hover = self.drag = self.last = None
        self.close_editor()
        self.vp.update()

    def clear(self):
        if self.inst is None:
            return
        self.inst = self.drag = self.last = self.hover = None
        self.close_editor()
        self.vp.update()

    def frame(self):
        T = self.ctl.doc.T(self.inst)
        o = T[:3, :3] @ self.lo + T[:3, 3]
        R = T[:3, :3] @ self.lR
        return o, R

    # ------------------------------------------------------------------ geometry
    def _geom(self):
        cam = self.vp.cam
        o, R = self.frame()
        Lw = LEN_PX * cam.scale
        S = lambda p: cam.to_screen(p)[..., :2]
        g = {"o": o, "R": R, "Lw": Lw, "os": S(o), "axes": [], "squares": []}
        ang = np.linspace(0, 2 * np.pi, 33)
        for k in range(3):
            a = R[:, k]
            u, v = R[:, (k + 1) % 3], R[:, (k + 2) % 3]
            c = o + a * Lw * RING_AT
            ring = c + Lw * RING_R * (np.outer(np.cos(ang), u) + np.outer(np.sin(ang), v))
            # how much the arrow points at the viewer (to fade arrows seen end-on)
            facing = abs(float(a @ cam.R[2]))
            g["axes"].append({"a": a, "shaft_end": S(o + a * Lw * SHAFT), "cone_end": S(o + a * Lw * CONE_END),
                              "ring": S(ring), "ring_c": S(c), "facing": facing,
                              "cone_base": [S(o + a * Lw * SHAFT + Lw * 0.055 * (math.cos(t) * u + math.sin(t) * v))
                                            for t in np.linspace(0, 2 * np.pi, 13)]})
        for k in range(3):
            u, v = R[:, (k + 1) % 3], R[:, (k + 2) % 3]
            q = [o + Lw * (u * s + v * t) for s, t in ((SQ0, SQ0), (SQ1, SQ0), (SQ1, SQ1), (SQ0, SQ1))]
            g["squares"].append(S(np.array(q)))
        return g

    def label_rect(self):
        if self.last is None or self.last.get("text") is None:
            return None
        x, y = self.last["label_at"]
        return QRectF(x - 40, y - 11, 80, 22)

    # ------------------------------------------------------------------ hit test
    def hit(self, x, y):
        if not self.active():
            return None
        r = self.label_rect()
        if r is not None and r.contains(QPointF(x, y)):
            return ("label",)
        g = self._geom()
        p = np.array([x, y], float)
        best = None
        for k, ax in enumerate(g["axes"]):
            rc = ax["ring_c"]
            rr = max(float(np.max(np.linalg.norm(ax["ring"] - rc, axis=1))), 7.0)
            d = float(np.linalg.norm(p - rc))
            if d < rr + 5 and (best is None or d < best[0]):
                best = (d, ("rot", k))
        if best is not None:
            return best[1]
        if np.linalg.norm(p - g["os"]) < 9:
            return ("free",)
        for k, ax in enumerate(g["axes"]):
            d, t = _seg_dist(p, g["os"], ax["cone_end"])
            if d < 7 and t > 0.25 and (best is None or d < best[0]):
                best = (d, ("move", k))
        if best is not None:
            return best[1]
        for k, sq in enumerate(g["squares"]):
            if _in_poly(p, sq):
                return ("plane", k)
        return None

    # ------------------------------------------------------------------ dragging
    def _point_on(self, handle, x, y, o, R):
        """Where the mouse ray meets the handle's line / plane (start and during the drag)."""
        cam = self.vp.cam
        ro, rd = cam.ray(x, y)
        kind = handle[0]
        if kind == "move":
            D = R[:, handle[1]]
            w0 = o - ro
            a, b, c = D @ D, D @ rd, rd @ rd
            den = a * c - b * b
            if abs(den) < 1e-9:
                return None
            return (b * (rd @ w0) - c * (D @ w0)) / den
        if kind == "plane":
            return _ray_plane(ro, rd, o, R[:, handle[1]])
        if kind == "free":
            return _ray_plane(ro, rd, o, cam.R[2])
        return None

    def _rot_angle(self, d, x, y):
        """Angle (radians, unwrapped later) of the mouse around the rotate axis, measured from the drag's start."""
        cam = self.vp.cam
        k = d["handle"][1]
        o, a = d["o"], d["R"][:, k]
        if d["mode"] == "plane":
            ro, rd = cam.ray(x, y)
            hp = _ray_plane(ro, rd, o, a)
            if hp is None:
                return None
            w = hp - o
            u = d["ref_u"]
            return math.atan2(float(np.cross(u, w) @ a), float(u @ w))
        # the ring is seen edge-on: use the angle around the triad's centre on screen instead
        os_ = cam.to_screen(o)[:2]
        s = 1.0 if float(a @ cam.R[2]) >= 0 else -1.0
        return s * -math.atan2(y - os_[1], x - os_[0]) - d["screen0"]

    def start(self, handle, x, y):
        doc = self.ctl.doc
        o, R = self.frame()
        self.close_editor()
        d = {"handle": handle, "o": o, "R": R, "T0s": {i["id"]: np.array(i["T"], float) for i in doc.instances},
             "snap": doc._snapshot(), "value": 0.0, "moved": False, "x0": x, "y0": y}
        if handle[0] == "rot":
            a = R[:, handle[1]]
            cam = self.vp.cam
            d["mode"], d["screen0"], d["prev"] = "screen", 0.0, 0.0
            d["ref_u"] = R[:, (handle[1] + 1) % 3]
            if abs(float(a @ cam.R[2])) > 0.12:
                ro, rd = cam.ray(x, y)
                hp = _ray_plane(ro, rd, o, a)
                if hp is not None:
                    u = hp - o
                    u = u - a * (u @ a)
                    if np.linalg.norm(u) > 1e-9:
                        d["mode"], d["ref_u"] = "plane", u / np.linalg.norm(u)
            if d["mode"] == "screen":
                d["screen0"] = self._rot_angle(d, x, y)
        else:
            d["p0"] = self._point_on(handle, x, y, o, R)
            if d["p0"] is None:
                return False
        self.drag = d
        return True

    def motion_for(self, d, value=None, vec=None):
        """The world motion for drag `d` with the given value (mm or degrees) or vector."""
        kind, o, R = d["handle"][0], d["o"], d["R"]
        if kind == "move":
            return motion(t=R[:, d["handle"][1]] * value)
        if kind == "rot":
            return motion(R=_rot(R[:, d["handle"][1]], math.radians(value)), about=o)
        return motion(t=vec)

    def move(self, x, y):
        d = self.drag
        if d is None:
            return
        o, R = d["o"], d["R"]
        kind = d["handle"][0]
        if abs(x - d["x0"]) + abs(y - d["y0"]) > 2:
            d["moved"] = True
        if kind == "move":
            t = self._point_on(d["handle"], x, y, o, R)
            if t is None:
                return
            d["value"] = float(t - d["p0"])
            M = self.motion_for(d, d["value"])
        elif kind in ("plane", "free"):
            hp = self._point_on(d["handle"], x, y, o, R)
            if hp is None:
                return
            d["vec"] = hp - d["p0"]
            d["value"] = float(np.linalg.norm(d["vec"]))
            M = self.motion_for(d, vec=d["vec"])
        else:
            raw = self._rot_angle(d, x, y)
            if raw is None:
                return
            # unwrap so turning past 180° keeps counting
            prev = d["prev"]
            step = (raw - prev + math.pi) % (2 * math.pi) - math.pi
            d["prev"] = prev + step
            ang = math.degrees(d["prev"])
            if _shift_held():
                ang = round(ang / 15.0) * 15.0
            d["value"] = ang
            M = self.motion_for(d, ang)
        self.ctl.apply_motion(self.inst, M, d["T0s"], self._targets(d), turn=kind == "rot")
        self._set_label(d)
        self.vp.update()

    def _targets(self, d):
        """Part-space points (with world positions before the drag) that pin down the part's pose."""
        Lw = d.get("Lw") or LEN_PX * self.vp.cam.scale
        d["Lw"] = Lw
        return [self.lo, self.lo + self.lR[:, 0] * Lw, self.lo + self.lR[:, 1] * Lw]

    def _set_label(self, d):
        kind = d["handle"][0]
        g = self._geom()
        if kind == "rot":
            k = d["handle"][1]
            text = f"{d['value']:.2f} deg"
            at = g["axes"][k]["ring_c"] + np.array([30.0, -22.0])
        elif kind == "move":
            k = d["handle"][1]
            text = f"{d['value']:.2f} mm"
            at = g["axes"][k]["cone_end"] + np.array([34.0, -18.0])
        else:
            text = f"{d['value']:.2f} mm"
            at = g["os"] + np.array([-50.0, 34.0])
        self.last = {"handle": d["handle"], "o": d["o"], "R": d["R"], "T0s": d["T0s"], "value": d["value"],
                     "vec": d.get("vec"), "text": text, "label_at": (float(at[0]), float(at[1])),
                     "Lw": d.get("Lw"), "arc": d.get("prev") if kind == "rot" else None,
                     "ref_u": d.get("ref_u"), "prev": d.get("prev"), "mode": d.get("mode"),
                     "screen0": d.get("screen0"), "T_after": self.ctl.doc.T(self.inst).copy(),
                     "ref_local": (d["T0s"][self.inst][:3, :3].T @ d["ref_u"]) if d.get("ref_u") is not None
                     else None}

    def finish(self):
        d, self.drag = self.drag, None
        return d

    # ------------------------------------------------------------------ typing a value
    def open_editor(self, initial=None):
        """Show a typing box over the value label. Opened by itself after every drag with the value selected,
        so just typing a number (and Enter) sets it, like Onshape; `initial` starts it with that text instead."""
        if self.last is None or self.last["handle"][0] not in ("move", "rot", "plane", "free"):
            return
        r = self.label_rect()
        self.close_editor()
        ed = _ValueEdit(self.vp, self.close_editor)
        ed.setText(f"{self.last['value']:.3f}".rstrip("0").rstrip(".") if initial is None else initial)
        ed.setGeometry(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
        ed.setAlignment(Qt.AlignCenter)
        ed.setStyleSheet("QLineEdit { border: 1px solid #2378e6; border-radius: 3px; background: white;"
                         " color: #1d1d1f; font-weight: bold; }")
        if initial is None:
            ed.selectAll()
        ed.returnPressed.connect(self._typed)
        ed.editingFinished.connect(self.close_editor)
        ed.show()
        ed.setFocus()
        self.editor = ed

    def close_editor(self):
        ed, self.editor = self.editor, None
        if ed is not None:
            ed.hide()
            ed.deleteLater()

    def _typed(self):
        ed, last = self.editor, self.last
        if ed is None or last is None:
            return
        txt = ed.text().replace("deg", "").replace("mm", "").replace("°", "").strip()
        try:
            v = float(txt)
        except ValueError:
            return
        d = dict(last)
        if d["handle"][0] in ("plane", "free"):
            vec = d.get("vec")
            n = np.linalg.norm(vec) if vec is not None else 0.0
            if n < 1e-12:
                return
            d["vec"] = vec / n * v
        d["value"] = v
        if d["handle"][0] == "rot":
            d["prev"] = math.radians(v)
        M = self.motion_for(d, v, d.get("vec"))
        self.ctl.apply_motion(self.inst, M, d["T0s"], self._targets(d), exact=True, turn=d["handle"][0] == "rot")
        self._set_label(d)
        self.close_editor()
        self.ctl.typed_value_applied()
        self.vp.update()

    # ------------------------------------------------------------------ drawing
    def draw(self, p):
        if not self.active():
            return
        if self.last is not None and self.drag is None and \
                not np.allclose(self.ctl.doc.T(self.inst), self.last["T_after"], atol=1e-7):
            self.last = None                    # the part moved some other way: the old value means nothing now
            self.close_editor()
        g = self._geom()
        os_ = QPointF(*g["os"])
        dragging = self.drag["handle"] if self.drag is not None and self.drag["moved"] else None
        hot = dragging or self.hover
        p.save()
        p.setRenderHint(p.RenderHint.Antialiasing, True)
        outline = QColor(70, 74, 82)
        white = QColor(255, 255, 255, 235)

        def pen(active, w=1.4):
            return QPen(ACTIVE if active else outline, w)

        # plane squares (draw first, under the arrows)
        for k, sq in enumerate(g["squares"]):
            act = hot == ("plane", k)
            p.setPen(pen(act, 1.2))
            fill = QColor(ACTIVE) if act else QColor(255, 255, 255, 150)
            if act:
                fill.setAlpha(120)
            p.setBrush(fill)
            p.drawPolygon(QPolygonF([QPointF(*q) for q in sq]))
        # arrows, back to front so nearer ones sit on top
        order = sorted(range(3), key=lambda k: float(g["axes"][k]["a"] @ self.vp.cam.R[2]))
        for k in order:
            ax = g["axes"][k]
            act_m = hot == ("move", k)
            act_r = hot == ("rot", k)
            col = QColor(*AXIS_COLORS[k])
            # shaft: white with a dark edge, coloured tint so X / Y / Z are easy to tell apart
            a0, a1 = g["os"], ax["shaft_end"]
            p.setPen(QPen(ACTIVE if act_m else outline, 5.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(*a0), QPointF(*a1))
            p.setPen(QPen(QColor(ACTIVE).lighter(150) if act_m else col.lighter(160), 2.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(*a0), QPointF(*a1))
            # cone: hull of its base circle and the tip
            pts = np.array(ax["cone_base"] + [ax["cone_end"]])
            hull = _hull(pts)
            p.setPen(pen(act_m, 1.3))
            p.setBrush(QColor(ACTIVE) if act_m else white)
            p.drawPolygon(QPolygonF([QPointF(*q) for q in hull]))
            # rotate ring
            ring = QPolygonF([QPointF(*q) for q in ax["ring"]])
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ACTIVE if act_r else outline, 4.6))
            p.drawPolyline(ring)
            p.setPen(QPen(QColor(ACTIVE).lighter(140) if act_r else col.lighter(150), 2.2))
            p.drawPolyline(ring)
        # centre ball
        act = hot == ("free",)
        p.setPen(pen(act, 1.4))
        p.setBrush(QColor(ACTIVE) if act else white)
        p.drawEllipse(os_, 6.0, 6.0)
        # rotation arc while turning (and after, until something else happens)
        last = self.last
        if last is not None and last["handle"][0] == "rot" and last.get("ref_u") is not None \
                and last.get("arc") is not None and abs(last["arc"]) > 1e-4:
            k = last["handle"][1]
            # drawn where the triad is now: the part (and the triad with it) may have swung about a hinge
            a = g["R"][:, k]
            u = _rot(a, -last["arc"]) @ (self.ctl.doc.T(self.inst)[:3, :3] @ last["ref_local"])
            v = np.cross(a, u)
            r = g["Lw"] * RING_AT
            ts = np.linspace(0, last["arc"], max(8, int(abs(math.degrees(last["arc"])) / 3)))
            pts = self.vp.cam.to_screen(np.array([g["o"] + r * (math.cos(t) * u + math.sin(t) * v) for t in ts]))
            path = QPainterPath(QPointF(*pts[0][:2]))
            for q in pts[1:]:
                path.lineTo(QPointF(*q[:2]))
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(ACTIVE, 2.0))
            p.drawPath(path)
            s0 = g["os"]
            p.setPen(QPen(ACTIVE, 1.0, Qt.DashLine))
            p.drawLine(QPointF(*s0), QPointF(*pts[0][:2]))
            p.drawLine(QPointF(*s0), QPointF(*pts[-1][:2]))
        # value label (click it, or just type, to set a value)
        r = self.label_rect()
        if r is not None and self.editor is not None:
            self.editor.setGeometry(int(r.x()), int(r.y()), int(r.width()), int(r.height()))
        if r is not None and self.editor is None:
            f = QFont()
            f.setPixelSize(12)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QPen(ACTIVE, 1.2))
            p.setBrush(QColor(255, 255, 255, 240))
            p.drawRoundedRect(r, 3, 3)
            p.setPen(QColor(29, 29, 31))
            p.drawText(r, Qt.AlignCenter, last["text"])
        p.restore()


class _ValueEdit(QLineEdit):
    """The typing box: Esc closes it without changing anything."""

    def __init__(self, parent, on_cancel):
        super().__init__(parent)
        self._cancel = on_cancel

    def event(self, e):
        # Esc is also a window shortcut: claim it while typing here
        if e.type() == e.Type.ShortcutOverride and e.key() == Qt.Key_Escape:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(e)


def _hull(pts):
    pts = sorted(map(tuple, pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for q in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], q) <= 0:
            lower.pop()
        lower.append(q)
    for q in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], q) <= 0:
            upper.pop()
        upper.append(q)
    return lower[:-1] + upper[:-1]


def _shift_held():
    from PySide6.QtWidgets import QApplication
    return bool(QApplication.keyboardModifiers() & Qt.ShiftModifier)
