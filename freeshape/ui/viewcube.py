"""Onshape-style view cube, drawn with QPainter in the top-right corner of the viewport.

* A cube with bevelled edges and corners: click a face for a standard view, an edge for a view halfway between
  two faces, or a corner for an isometric-style view (26 views in all).
* Face names are printed on the faces themselves and turn with the cube.
* Four arrows around it turn the view 90° up / down / left / right; the two curved arrows roll it 90°.
* An X / Y / Z triad sits on the cube's corner, and a small menu below has the named views.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPolygonF, QPen, QFont, QTransform, QPainterPath, QBrush

from . import theme

C = 0.30                     # bevel size (cube is -1..1)
AREA = 168                   # size of the whole widget in pixels
SCALE = 27.0                 # pixels per cube unit
LABELS = {  # face normal -> (name, text-right vector, text-up vector)
    (0, 0, 1): ("Top", (1, 0, 0), (0, 1, 0)),
    (0, 0, -1): ("Bottom", (1, 0, 0), (0, -1, 0)),
    (0, -1, 0): ("Front", (1, 0, 0), (0, 0, 1)),
    (0, 1, 0): ("Back", (-1, 0, 0), (0, 0, 1)),
    (1, 0, 0): ("Right", (0, 1, 0), (0, 0, 1)),
    (-1, 0, 0): ("Left", (0, -1, 0), (0, 0, 1)),
}


def _elements():
    """The cube's polygons: 6 faces, 12 bevelled edges, 8 corners. [(key, normal, [3D points])]."""
    out = []
    k = 1 - C
    for n in LABELS:
        n = np.array(n, float)
        ax = int(np.argmax(np.abs(n)))
        a, b = [i for i in range(3) if i != ax]
        pts = []
        for s1, s2 in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            p = n.copy()
            p[a], p[b] = s1 * k, s2 * k
            pts.append(p)
        out.append(("face", n, pts))
    for i in range(3):
        for j in range(i + 1, 3):
            free = 3 - i - j
            for si in (-1, 1):
                for sj in (-1, 1):
                    n = np.zeros(3)
                    n[i], n[j] = si, sj
                    pts = []
                    for sf, first in ((-1, True), (1, True), (1, False), (-1, False)):
                        p = np.zeros(3)
                        if first:
                            p[i], p[j] = si, sj * k
                        else:
                            p[i], p[j] = si * k, sj
                        p[free] = sf * k
                        pts.append(p)
                    out.append(("edge", n / np.linalg.norm(n), pts))
    for sx in (-1, 1):
        for sy in (-1, 1):
            for sz in (-1, 1):
                s = np.array([sx, sy, sz], float)
                pts = [np.array([sx, sy * k, sz * k]), np.array([sx * k, sy, sz * k]), np.array([sx * k, sy * k, sz])]
                out.append(("corner", s / np.linalg.norm(s), pts))
    return out


ELEMENTS = _elements()

ROUND = 0.34                 # corner/edge rounding radius (cube is -1..1)
PANEL = 0.70                 # half-size of the white panel on each face; beyond it is an edge / corner zone
BLOB = 0.50                  # how rounded the overall shape is
SHELL = 1.12                 # half-size of the rounded outer shell (the face panels sit at 1.0)
CORNER_R = 0.19              # radius of the white disc on each corner


def _cells():
    """A rounded cube as small patches: [(region key normal, patch normal, [4 points])]. Each face is split
    into a flat middle and rounded borders; a patch belongs to the face, edge or corner it sits on."""
    K = 1 - ROUND
    ts = list(np.linspace(-1, -K, 4)) + list(np.linspace(K, 1, 4))
    ts = sorted(set([round(t, 9) for t in ts]))
    out = []

    def rounded(q):
        c_ = np.clip(q, -K, K)
        d = q - c_
        nd = np.linalg.norm(d)
        return (c_ + ROUND * d / nd, d / nd) if nd > 1e-12 else (q, q / np.linalg.norm(q))
    for n in LABELS:
        n = np.array(n, float)
        ax = int(np.argmax(np.abs(n)))
        a, b = [i for i in range(3) if i != ax]
        for i in range(len(ts) - 1):
            for j in range(len(ts) - 1):
                quad = []
                for u, v in ((ts[i], ts[j]), (ts[i + 1], ts[j]), (ts[i + 1], ts[j + 1]), (ts[i], ts[j + 1])):
                    q = n.copy()
                    q[a], q[b] = u, v
                    quad.append(rounded(q)[0])
                cu, cv = (ts[i] + ts[i + 1]) / 2, (ts[j] + ts[j + 1]) / 2
                q = n.copy()
                q[a], q[b] = cu, cv
                _pt, normal = rounded(q)
                region = n.copy()
                if abs(cu) > K:
                    region[a] = math.copysign(1.0, cu)
                if abs(cv) > K:
                    region[b] = math.copysign(1.0, cv)
                out.append((region / np.linalg.norm(region), normal, quad))
    return out


CELLS = _cells()


def _hull(points):
    pts = sorted(set((round(p.x(), 3), round(p.y(), 3)) for p in points))
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


class ViewCube:
    def __init__(self):
        self.hover = None

    # ------------------------------------------------------------------ layout
    @staticmethod
    def area(width):
        return QRectF(width - AREA - 4, 4, AREA, AREA)

    def _centre(self, width):
        r = self.area(width)
        return QPointF(r.center().x(), r.center().y() - 4)

    @staticmethod
    def _proj(R, c, p):
        v = R @ np.asarray(p, float)
        return QPointF(c.x() + v[0] * SCALE, c.y() - v[1] * SCALE), v[2]

    def _arrows(self, c):
        """Clickable arrows: [(key, polygon or path, is_path)]."""
        out = []
        d, s = 2.55 * SCALE, 8.5
        for key, (dx, dy) in (("up", (0, -1)), ("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0))):
            tip = QPointF(c.x() + dx * (d + s), c.y() + dy * (d + s))
            base = QPointF(c.x() + dx * d, c.y() + dy * d)
            px, py = -dy, dx
            out.append((key, QPolygonF([tip, QPointF(base.x() + px * s, base.y() + py * s),
                                        QPointF(base.x() - px * s, base.y() - py * s)])))
        # curved roll arrows, top-left (anticlockwise) and top-right (clockwise)
        for key, ang0, sweep in (("roll_ccw", 112, 40), ("roll_cw", 68, -40)):
            rr = 2.45 * SCALE
            path = QPainterPath()
            rect = QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr)
            path.arcMoveTo(rect, ang0)
            path.arcTo(rect, ang0, sweep)
            out.append((key, path))
        return out

    def menu_rect(self, width):
        c = self._centre(width)
        return QRectF(c.x() + 1.95 * SCALE, c.y() + 2.2 * SCALE, 26, 18)

    # ------------------------------------------------------------------ drawing
    def draw(self, p: QPainter, cam, width):
        """Draw the cube (from a cached picture when nothing about it has changed since the last frame)."""
        from PySide6.QtGui import QImage
        dpr = p.device().devicePixelRatioF() if hasattr(p.device(), "devicePixelRatioF") else 1.0
        key = (np.round(cam.R, 5).tobytes(), self.hover, theme.MODE, width, dpr)
        r = self.area(width).adjusted(-2, -2, 2, 12)
        if getattr(self, "_cache_key", None) != key:
            img = QImage(int(r.width() * dpr), int(r.height() * dpr), QImage.Format_ARGB32_Premultiplied)
            img.setDevicePixelRatio(dpr)
            img.fill(Qt.transparent)
            q = QPainter(img)
            q.translate(-r.x(), -r.y())
            self._draw(q, cam, width)
            q.end()
            self._cache_img, self._cache_key = img, key
        p.drawImage(r.topLeft(), self._cache_img)

    def _draw(self, p: QPainter, cam, width):
        R = cam.R
        c = self._centre(width)
        dark = theme.MODE == "dark"
        face_rgb = np.array([0.985, 0.99, 1.0]) if not dark else np.array([0.36, 0.39, 0.44])
        edge_col = QColor(150, 160, 176) if not dark else QColor(25, 27, 31)
        hov_col = QColor(130, 180, 240) if not dark else QColor(70, 120, 200)
        light = np.array([0.35, 0.55, 1.0])
        light /= np.linalg.norm(light)
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        # arrows first (behind the cube)
        arrow_col = QColor(214, 222, 232, 235) if not dark else QColor(100, 110, 124, 200)
        for key, shape in self._arrows(c):
            hot = self.hover == key
            col = QColor(31, 111, 209, 220) if hot else arrow_col
            if isinstance(shape, QPolygonF):
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawPolygon(shape)
            else:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, 5.5, Qt.SolidLine, Qt.FlatCap))
                p.drawPath(shape)
                # arrow head at the end of the arc
                end = shape.pointAtPercent(1.0)
                t = shape.angleAtPercent(1.0)
                a = math.radians(t)
                dx, dy = math.cos(a), -math.sin(a)
                px, py = -dy, dx
                hs = 8.0
                tip = QPointF(end.x() + dx * hs, end.y() + dy * hs)
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawPolygon(QPolygonF([tip, QPointF(end.x() + px * hs * 0.85, end.y() + py * hs * 0.85),
                                         QPointF(end.x() - px * hs * 0.85, end.y() - py * hs * 0.85)]))

        cx_, cy_ = c.x(), c.y()

        def poly_of(pts):
            v = np.asarray(pts, float) @ R.T
            return QPolygonF([QPointF(cx_ + a * SCALE, cy_ - b * SCALE) for a, b in zip(v[:, 0], v[:, 1])])

        # axis triad from the centre of the cube; drawn first, so it fades inside the glass and is strong outside
        o3 = np.zeros(3)                     # from the middle of the cube, out through the faces
        o, _ = self._proj(R, c, o3)
        f = QFont()
        f.setPixelSize(15)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)

        def axis(d, lab, col):
            end, _ = self._proj(R, c, o3 + np.array(d, float) * 2.4)
            p.setPen(QPen(col, 2.0))
            p.drawLine(o, end)
            lp, _ = self._proj(R, c, o3 + np.array(d, float) * 2.78)
            p.drawText(QRectF(lp.x() - 8, lp.y() - 9, 16, 18), Qt.AlignCenter, lab)
        axis((1, 0, 0), "X", QColor(220, 40, 40))
        axis((0, 1, 0), "Y", QColor(70, 175, 40))
        axis((0, 0, 1), "Z", QColor(50, 50, 230))
        # Onshape's cube: a light blue-grey translucent frame (the edges), a white rounded panel on every face and a
        # white disc on every corner — all see-through, so the far side shows faintly
        if not dark:
            frame = QColor(220, 228, 238, 215)
            white = QColor(255, 255, 255, 205)
            white_back = QColor(255, 255, 255, 70)
            line = QColor(255, 255, 255, 230)
        else:
            frame = QColor(78, 86, 98, 210)
            white = QColor(150, 158, 170, 225)
            white_back = QColor(150, 158, 170, 60)
            line = QColor(170, 178, 190, 200)
        hot = QColor(80, 150, 235, 215)
        hover_n = None
        if self.hover and self.hover.startswith("n:"):
            hover_n = np.array([float(v) for v in self.hover[2:].split(",")])

        def is_hot(region):
            return hover_n is not None and np.allclose(np.asarray(region, float) / np.linalg.norm(region), hover_n,
                                                       atol=2e-3)

        def face_axes(n):
            ax = int(np.argmax(np.abs(n)))
            a_, b_ = [i for i in range(3) if i != ax]
            return a_, b_

        def panel_path(n, half, rc, z=1.0):
            a_, b_ = face_axes(n)
            pts = []
            for cx, cy, a0 in ((half - rc, half - rc, 0), (-half + rc, half - rc, 90),
                               (-half + rc, -half + rc, 180), (half - rc, -half + rc, 270)):
                for k in range(7):
                    t = math.radians(a0 + 15 * k)
                    q3 = n * z
                    q3 = q3.copy()
                    q3[a_], q3[b_] = cx + rc * math.cos(t), cy + rc * math.sin(t)
                    pts.append(q3)
            path = QPainterPath()
            path.addPolygon(poly_of(pts))
            path.closeSubpath()
            return path

        def disc_path(sgn, rad):
            sgn = np.asarray(sgn, float)
            nn = sgn / np.linalg.norm(sgn)
            u = np.cross(nn, [0, 0, 1.0]) if abs(nn[2]) < 0.9 else np.cross(nn, [1.0, 0, 0])
            u /= np.linalg.norm(u)
            v = np.cross(nn, u)
            ctr = sgn * (SHELL - BLOB) + nn * BLOB   # on the rounded corner's surface
            path = QPainterPath()
            path.addPolygon(poly_of([ctr + rad * (math.cos(t) * u + math.sin(t) * v)
                                     for t in np.linspace(0, 2 * math.pi, 28)]))
            path.closeSubpath()
            return path

        faces = [np.array(n, float) for n in LABELS]
        corners = [np.array([sx, sy, sz], float) for sx in (-1, 1) for sy in (-1, 1) for sz in (-1, 1)]
        # far side first, faintly
        p.setPen(Qt.NoPen)
        for n in faces:
            if float(n @ cam.back) < -1e-6:
                p.setBrush(white_back)
                p.drawPath(panel_path(n, PANEL, 0.36))
        # the frame: the cube's outline, filled, with softly rounded corners
        # outline of a well-rounded box (corner spheres of radius BLOB), so it reads as a soft die, not a cube
        # (in an orthographic view that outline is the outline of the 8 sphere centres, widened by the radius)
        rr = BLOB
        hull = _hull([self._proj(R, c, cs * (SHELL - rr))[0] for cs in corners])
        if hull:
            fp = QPainterPath()
            fp.addPolygon(QPolygonF([QPointF(xx, yy) for xx, yy in hull]))
            fp.closeSubpath()
            p.setPen(QPen(frame, 2 * rr * SCALE, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(frame)
            p.drawPath(fp)
        # hovered edge: the band along it, on both faces
        if hover_n is not None and int(np.sum(np.abs(hover_n) > 1e-6)) == 2:
            idx = [i for i in range(3) if abs(hover_n[i]) > 1e-6]
            free = 3 - idx[0] - idx[1]
            sg = np.sign(hover_n)
            p.setPen(Qt.NoPen)
            p.setBrush(hot)
            for i, j in ((idx[0], idx[1]), (idx[1], idx[0])):
                quad = []
                for f_, w_ in ((-PANEL, PANEL), (PANEL, PANEL), (PANEL, 1.0), (-PANEL, 1.0)):
                    q3 = np.zeros(3)
                    q3[i] = sg[i]
                    q3[j] = sg[j] * w_
                    q3[free] = f_
                    quad.append(q3)
                p.drawPolygon(poly_of(quad))
        # white face panels
        p.setPen(Qt.NoPen)
        for n in faces:
            if float(n @ cam.back) > 1e-6:
                p.setBrush(hot if is_hot(n) else white)
                p.drawPath(panel_path(n, PANEL, 0.36))
        # white corner discs
        for s3 in corners:
            if float(s3 @ cam.back) > 0.05:
                p.setBrush(hot if is_hot(s3) else white)
                p.drawPath(disc_path(s3, CORNER_R))
        # face names, lying on the faces
        f = QFont()
        f.setPixelSize(12)
        f.setWeight(QFont.Medium)
        p.setFont(f)
        for n, (name, rv, uv) in LABELS.items():
            n = np.array(n, float)
            facing = float(n @ cam.back)
            if facing < 0.12:
                continue
            o, _ = self._proj(R, c, n)
            rx = R @ np.array(rv, float) * SCALE
            uy = R @ np.array(uv, float) * SCALE
            # unit text box (−1..1) -> projected face square: a pure affine map in an orthographic view
            tr = QTransform(rx[0] / 28, -rx[1] / 28, -uy[0] / 28, uy[1] / 28, o.x(), o.y())
            p.save()
            p.setTransform(tr, True)
            txt = QColor(30, 32, 36) if not dark else QColor(235, 238, 242)
            txt.setAlphaF(min(0.95, 0.2 + facing))
            p.setPen(txt)
            p.drawText(QRectF(-38, -12, 76, 24), Qt.AlignCenter, name)
            p.restore()
        # view menu button
        mr = self.menu_rect(width)
        hot = self.hover == "menu"
        if hot:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(31, 111, 209, 40))
            p.drawRoundedRect(mr, 3, 3)
        # a small shaded cube with a dark outline, and the drop-down triangle — like Onshape's
        from PySide6.QtGui import QLinearGradient
        bx, by, sz = mr.x() + 2, mr.y() + 1, 14.0
        top = [QPointF(bx + sz * 0.5, by), QPointF(bx + sz, by + sz * 0.25), QPointF(bx + sz * 0.5, by + sz * 0.5),
               QPointF(bx, by + sz * 0.25)]
        left = [QPointF(bx, by + sz * 0.25), QPointF(bx + sz * 0.5, by + sz * 0.5), QPointF(bx + sz * 0.5, by + sz),
                QPointF(bx, by + sz * 0.75)]
        right = [QPointF(bx + sz * 0.5, by + sz * 0.5), QPointF(bx + sz, by + sz * 0.25), QPointF(bx + sz, by + sz * 0.75),
                 QPointF(bx + sz * 0.5, by + sz)]
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(250, 250, 250))
        p.drawPolygon(QPolygonF(top))
        g = QLinearGradient(left[0], left[2])
        g.setColorAt(0, QColor(150, 150, 150))
        g.setColorAt(1, QColor(205, 205, 205))
        p.setBrush(QBrush(g))
        p.drawPolygon(QPolygonF(left))
        g2 = QLinearGradient(right[0], right[2])
        g2.setColorAt(0, QColor(235, 235, 235))
        g2.setColorAt(1, QColor(255, 255, 255))
        p.setBrush(QBrush(g2))
        p.drawPolygon(QPolygonF(right))
        outline = QColor(40, 40, 40) if not dark else QColor(20, 20, 22)
        p.setPen(QPen(outline, 1.3))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(QPolygonF([top[0], top[1], right[2], right[3], left[3], left[0]]))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(30, 30, 30) if not dark else QColor(215, 218, 224))
        tx, ty = mr.x() + 19, mr.y() + 7
        p.drawPolygon(QPolygonF([QPointF(tx, ty), QPointF(tx + 8, ty), QPointF(tx + 4, ty + 4.5)]))
        p.restore()

    @staticmethod
    def _key(n):
        return "n:" + ",".join(f"{v:+.3f}" for v in n)

    # ------------------------------------------------------------------ picking
    def pick(self, cam, width, x, y):
        """Key of what's under (x, y): 'n:..' cube part, 'up'/'down'/'left'/'right', 'roll_cw'/'roll_ccw',
        'menu'; '' inside the widget but on nothing; None outside it."""
        r = self.area(width)
        pt = QPointF(x, y)
        if not r.adjusted(-2, -2, 2, 24).contains(pt):
            return None
        if self.menu_rect(width).adjusted(-3, -3, 3, 3).contains(pt):
            return "menu"
        R = cam.R
        c = self._centre(width)
        region = self._hit_region(cam, c, x, y)
        if region is not None:
            return self._key(region)
        for key, shape in self._arrows(c):
            if isinstance(shape, QPolygonF):
                if shape.boundingRect().adjusted(-5, -5, 5, 5).contains(pt):
                    return key
            else:
                for i in range(11):
                    q = shape.pointAtPercent(i / 10)
                    if (q.x() - x) ** 2 + (q.y() - y) ** 2 < 8 ** 2:
                        return key
        return "" if r.contains(pt) else None

    @staticmethod
    def _hit_region(cam, c, x, y):
        """Where a screen point lands on the cube: the normal of the face, edge or corner zone it hits."""
        vx, vy = (x - c.x()) / SCALE, -(y - c.y()) / SCALE
        o = cam.right * vx + cam.up * vy + cam.back * 10.0
        d = -cam.back
        with np.errstate(divide="ignore", invalid="ignore"):
            t1, t2 = (-1 - o) / d, (1 - o) / d
        t1 = np.where(np.isnan(t1), -np.inf, t1)
        t2 = np.where(np.isnan(t2), np.inf, t2)
        tmin, tmax = np.max(np.minimum(t1, t2)), np.min(np.maximum(t1, t2))
        if tmax < tmin:
            return None
        h = o + d * tmin
        ax = int(np.argmax(np.abs(h)))
        region = np.zeros(3)
        region[ax] = math.copysign(1.0, h[ax])
        for i in range(3):
            if i != ax and abs(h[i]) > PANEL:
                region[i] = math.copysign(1.0, h[i])
        return region / np.linalg.norm(region)

    @staticmethod
    def target_view(cam, key):
        """(back, up) for a click on `key`, or None."""
        Rr, Ru, Rb = cam.R[0], cam.R[1], cam.R[2]
        if key.startswith("n:"):
            n = np.array([float(v) for v in key[2:].split(",")])
            n /= np.linalg.norm(n)
            t = tuple(int(round(v)) for v in n)
            if t in ((0, 0, 1), (0, 0, -1)):
                # straight down / up: keep the current screen orientation as far as possible
                up = Ru - (Ru @ n) * n
                if np.linalg.norm(up) < 1e-3:
                    up = np.array([0, 1.0, 0]) if t[2] > 0 else np.array([0, -1.0, 0])
                # snap to the nearest world axis so the view is square
                ax = np.zeros(3)
                i = int(np.argmax(np.abs(up[:2])))
                ax[i] = math.copysign(1.0, up[i])
                return n, ax
            return n, np.array([0, 0, 1.0])
        if key == "up":
            return Ru, -Rb
        if key == "down":
            return -Ru, Rb
        if key == "left":
            return -Rr, Ru
        if key == "right":
            return Rr, Ru
        if key == "roll_ccw":
            return Rb, Rr
        if key == "roll_cw":
            return Rb, -Rr
        return None
