"""Toolbar / tree icons drawn with QPainter, so the app needs no image assets."""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF, QBrush, QFont

INK = QColor("#3b4250")


def set_ink(hex_color):
    """Recolour line work for the current theme (then clear the icon cache)."""
    INK.setNamedColor(hex_color) if hasattr(INK, "setNamedColor") else INK.setRgb(QColor(hex_color).rgb())
    icon.cache_clear()
BLUE = QColor("#1f6fd1")
BLUE_FILL = QColor("#9cc3f0")
FACE = QColor("#c9d6e6")
FACE_DARK = QColor("#9fb2c9")
ORANGE = QColor("#f08a24")
GREEN = QColor("#2e9e44")
RED = QColor("#d93025")


def _pen(c=INK, w=1.7, style=Qt.SolidLine):
    p = QPen(c, w, style)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


def _box(p: QPainter, x, y, w, h, d, top=FACE, side=FACE_DARK, front=None):
    """Simple oblique box."""
    front = front or QColor("#dfe7f1")
    p.setPen(_pen(INK, 1.3))
    p.setBrush(front)
    p.drawRect(QRectF(x, y, w, h))
    p.setBrush(top)
    p.drawPolygon(QPolygonF([QPointF(x, y), QPointF(x + d, y - d), QPointF(x + w + d, y - d), QPointF(x + w, y)]))
    p.setBrush(side)
    p.drawPolygon(QPolygonF([QPointF(x + w, y), QPointF(x + w + d, y - d), QPointF(x + w + d, y + h - d), QPointF(x + w, y + h)]))


def _arrow(p, x1, y1, x2, y2, c=BLUE, w=1.8):
    p.setPen(_pen(c, w))
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    a = math.atan2(y2 - y1, x2 - x1)
    s = 4.5
    pts = [QPointF(x2, y2), QPointF(x2 - s * math.cos(a - 0.5), y2 - s * math.sin(a - 0.5)),
           QPointF(x2 - s * math.cos(a + 0.5), y2 - s * math.sin(a + 0.5))]
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawPolygon(QPolygonF(pts))


def _dot(p, x, y, c=BLUE, r=2.4):
    p.setPen(Qt.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(x, y), r, r)


def _text(p, s, c=INK, size=12, bold=True, rect=QRectF(0, 0, 32, 32)):
    f = QFont()
    f.setPixelSize(size)
    f.setBold(bold)
    p.setFont(f)
    p.setPen(c)
    p.drawText(rect, Qt.AlignCenter, s)


def draw(name: str, p: QPainter):
    p.setBrush(Qt.NoBrush)
    if name == "undo" or name == "redo":
        if name == "redo":
            p.translate(32, 0)
            p.scale(-1, 1)
        path = QPainterPath()
        path.moveTo(9, 13)
        path.cubicTo(14, 7, 26, 8, 25, 18)
        path.cubicTo(24, 24, 18, 25, 14, 24)
        p.setPen(_pen(INK, 2))
        p.drawPath(path)
        p.setBrush(INK)
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(5, 9), QPointF(13, 9), QPointF(7, 17)]))
    elif name == "sketch":
        p.setPen(_pen(INK, 1.2))
        p.setBrush(QColor("#eef3fa"))
        p.drawPolygon(QPolygonF([QPointF(3, 22), QPointF(11, 14), QPointF(29, 14), QPointF(21, 22)]))
        p.setPen(_pen(BLUE, 1.8))
        p.drawEllipse(QPointF(16, 18), 4.5, 2.2)
        p.setPen(_pen(INK, 1.6))
        p.drawLine(QPointF(20, 4), QPointF(27, 11))
        p.setBrush(ORANGE)
        p.drawPolygon(QPolygonF([QPointF(20, 4), QPointF(23, 3), QPointF(28, 8), QPointF(27, 11)]))
    elif name == "extrude":
        _box(p, 6, 16, 14, 10, 6)
        _arrow(p, 16, 12, 16, 3, ORANGE, 2)
    elif name == "revolve":
        p.setPen(_pen(INK, 1.3, Qt.DashLine))
        p.drawLine(QPointF(16, 3), QPointF(16, 29))
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawRect(QRectF(18, 9, 7, 14))
        path = QPainterPath()
        path.moveTo(25, 26)
        path.cubicTo(18, 31, 6, 29, 7, 22)
        p.setPen(_pen(ORANGE, 2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        _arrow(p, 8, 23, 7, 19, ORANGE)
    elif name == "fillet":
        path = QPainterPath()
        path.moveTo(5, 27)
        path.lineTo(5, 15)
        path.cubicTo(5, 9, 9, 5, 15, 5)
        path.lineTo(27, 5)
        path.lineTo(27, 27)
        path.closeSubpath()
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPath(path)
        arc = QPainterPath()
        arc.moveTo(5, 15)
        arc.cubicTo(5, 9, 9, 5, 15, 5)
        p.setPen(_pen(ORANGE, 2.6))
        p.setBrush(Qt.NoBrush)
        p.drawPath(arc)
    elif name == "chamfer":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(5, 27), QPointF(5, 14), QPointF(14, 5), QPointF(27, 5), QPointF(27, 27)]))
        p.setPen(_pen(ORANGE, 2.6))
        p.drawLine(QPointF(5, 14), QPointF(14, 5))
    elif name == "shell":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE_DARK)
        p.drawPolygon(QPolygonF([QPointF(4, 10), QPointF(10, 5), QPointF(28, 5), QPointF(28, 23), QPointF(22, 28), QPointF(4, 28)]))
        p.setBrush(QColor("#eef3fa"))
        p.drawPolygon(QPolygonF([QPointF(8, 10), QPointF(12, 7), QPointF(25, 7), QPointF(21, 10)]))
        p.setBrush(FACE)
        p.drawRect(QRectF(4, 10, 18, 18))
        p.setBrush(QColor("#7d93ad"))
        p.drawPolygon(QPolygonF([QPointF(7, 10), QPointF(10, 8), QPointF(23, 8), QPointF(19, 10)]))
    elif name == "hole":
        _box(p, 4, 12, 18, 14, 6)
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("#4a5566"))
        p.drawEllipse(QPointF(16, 9), 4.5, 2.2)
        p.setPen(_pen(ORANGE, 1.4, Qt.DashLine))
        p.drawLine(QPointF(14, 11), QPointF(14, 22))
        p.drawLine(QPointF(18, 11), QPointF(18, 22))
    elif name == "thread":
        # a bolt shank with slanted thread crests
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawRect(QRectF(10, 4, 12, 24))
        p.setPen(_pen(ORANGE, 1.8))
        for y in (8, 13, 18, 23):
            p.drawLine(QPointF(8, y + 2), QPointF(24, y - 2))
    elif name == "plane":
        p.setPen(_pen(BLUE, 1.4))
        p.setBrush(QColor(156, 195, 240, 140))
        p.drawPolygon(QPolygonF([QPointF(3, 23), QPointF(11, 12), QPointF(29, 12), QPointF(21, 23)]))
        p.setBrush(QColor(156, 195, 240, 220))
        p.drawPolygon(QPolygonF([QPointF(3, 15), QPointF(11, 4), QPointF(29, 4), QPointF(21, 15)]))
        _arrow(p, 16, 25, 16, 17, ORANGE)
    elif name == "mirror":
        p.setPen(_pen(INK, 1.3, Qt.DashLine))
        p.drawLine(QPointF(16, 2), QPointF(16, 30))
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(13, 8), QPointF(13, 25), QPointF(4, 25)]))
        p.setBrush(QColor(240, 138, 36, 90))
        p.setPen(_pen(ORANGE, 1.3))
        p.drawPolygon(QPolygonF([QPointF(19, 8), QPointF(19, 25), QPointF(28, 25)]))
    elif name == "linear_pattern":
        for i, x in enumerate((4, 13, 22)):
            p.setPen(_pen(INK if i == 0 else ORANGE, 1.3))
            p.setBrush(FACE if i == 0 else QColor(240, 138, 36, 80))
            p.drawRect(QRectF(x, 12, 6, 12))
        _arrow(p, 4, 7, 28, 7, BLUE, 1.4)
    elif name == "circular_pattern":
        for k in range(6):
            a = k * math.pi / 3
            x, y = 16 + 10 * math.cos(a), 16 + 10 * math.sin(a)
            p.setPen(_pen(INK if k == 0 else ORANGE, 1.2))
            p.setBrush(FACE if k == 0 else QColor(240, 138, 36, 80))
            p.drawEllipse(QPointF(x, y), 3.4, 3.4)
        _dot(p, 16, 16, INK, 1.6)
    elif name == "boolean":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawEllipse(QPointF(12, 16), 8, 8)
        p.setBrush(QColor(240, 138, 36, 120))
        p.drawEllipse(QPointF(20, 16), 8, 8)
    elif name in ("import", "export"):
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("#eef3fa"))
        p.drawPolygon(QPolygonF([QPointF(7, 4), QPointF(20, 4), QPointF(25, 9), QPointF(25, 28), QPointF(7, 28)]))
        if name == "import":
            _arrow(p, 16, 9, 16, 22, BLUE)
        else:
            _arrow(p, 16, 22, 16, 9, ORANGE)
    elif name == "section":
        _box(p, 6, 13, 16, 13, 6)
        p.setPen(_pen(ORANGE, 1.2))
        p.setBrush(QColor(240, 138, 36, 150))
        p.drawPolygon(QPolygonF([QPointF(14, 13), QPointF(20, 7), QPointF(20, 20), QPointF(14, 26)]))
        p.setPen(_pen(BLUE, 1.3))
        p.setBrush(QColor(156, 195, 240, 90))
        p.drawPolygon(QPolygonF([QPointF(12, 4), QPointF(22, 0.5), QPointF(22, 24), QPointF(12, 30)]))
    elif name == "sweep":
        path = QPainterPath()
        path.moveTo(6, 27)
        path.cubicTo(6, 12, 14, 8, 27, 8)
        p.setPen(QPen(FACE_DARK, 7, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        p.setPen(QPen(FACE, 4.5, Qt.SolidLine, Qt.RoundCap))
        p.drawPath(path)
        p.setPen(_pen(ORANGE, 1.4, Qt.DashLine))
        p.drawPath(path)
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("#eef3fa"))
        p.drawEllipse(QPointF(6, 27), 4, 2.2)
    elif name == "loft":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(5, 26), QPointF(13, 8), QPointF(21, 8), QPointF(27, 26)]))
        p.setBrush(QColor("#eef3fa"))
        p.drawEllipse(QPointF(17, 8), 4, 1.8)
        p.setPen(_pen(ORANGE, 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(5, 24, 22, 4))
    elif name == "draft":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(6, 27), QPointF(10, 7), QPointF(22, 7), QPointF(26, 27)]))
        p.setPen(_pen(ORANGE, 1.4, Qt.DashLine))
        p.drawLine(QPointF(6, 7), QPointF(6, 27))
        p.drawLine(QPointF(26, 7), QPointF(26, 27))
    elif name == "split":
        _box(p, 5, 13, 16, 13, 6)
        p.setPen(_pen(ORANGE, 2))
        p.drawLine(QPointF(13, 4), QPointF(13, 30))
        p.setPen(_pen(BLUE, 1.2))
        p.setBrush(QColor(156, 195, 240, 120))
        p.drawPolygon(QPolygonF([QPointF(11, 6), QPointF(15, 3), QPointF(15, 25), QPointF(11, 29)]))
    elif name == "move":
        _box(p, 4, 17, 11, 10, 4)
        _arrow(p, 14, 18, 26, 6, ORANGE, 1.8)
        p.setPen(_pen(BLUE, 1.4))
        p.drawArc(QRectF(15, 15, 13, 13), 200 * 16, 140 * 16)
    elif name == "sk_fillet":
        p.setPen(_pen(INK, 2))
        path = QPainterPath()
        path.moveTo(5, 27)
        path.lineTo(5, 16)
        path.cubicTo(5, 10, 10, 5, 16, 5)
        path.lineTo(27, 5)
        p.drawPath(path)
        p.setPen(_pen(ORANGE, 1.4, Qt.DashLine))
        p.drawLine(QPointF(5, 5), QPointF(5, 14))
        p.drawLine(QPointF(5, 5), QPointF(14, 5))
    elif name == "offset":
        p.setPen(_pen(INK, 2))
        p.drawRoundedRect(QRectF(10, 10, 12, 12), 2, 2)
        p.setPen(_pen(BLUE, 1.8))
        p.drawRoundedRect(QRectF(4, 4, 24, 24), 6, 6)
    elif name == "slot":
        p.setPen(_pen(INK, 2))
        p.drawRoundedRect(QRectF(3, 10, 26, 12), 6, 6)
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawLine(QPointF(9, 16), QPointF(23, 16))
        _dot(p, 9, 16)
        _dot(p, 23, 16)
    elif name == "sk_mirror":
        p.setPen(_pen(INK, 1.3, Qt.DashLine))
        p.drawLine(QPointF(16, 3), QPointF(16, 29))
        p.setPen(_pen(INK, 2))
        p.drawPolyline(QPolygonF([QPointF(13, 7), QPointF(5, 14), QPointF(13, 25)]))
        p.setPen(_pen(BLUE, 2))
        p.drawPolyline(QPolygonF([QPointF(19, 7), QPointF(27, 14), QPointF(19, 25)]))
    elif name == "sk_lpattern":
        for i, x in enumerate((6, 16, 26)):
            p.setPen(_pen(INK if i == 0 else BLUE, 1.8))
            p.drawEllipse(QPointF(x, 18), 3.5, 3.5)
        _arrow(p, 4, 8, 28, 8, ORANGE, 1.4)
    elif name == "sk_cpattern":
        for k in range(6):
            a = k * math.pi / 3
            p.setPen(_pen(INK if k == 0 else BLUE, 1.6))
            p.drawEllipse(QPointF(16 + 10 * math.cos(a), 16 + 10 * math.sin(a)), 3, 3)
        _dot(p, 16, 16, ORANGE, 1.8)
    elif name == "variable":
        p.setPen(_pen(INK, 1.8))
        p.drawArc(QRectF(3, 5, 10, 22), 100 * 16, 160 * 16)
        p.drawArc(QRectF(19, 5, 10, 22), -80 * 16, 160 * 16)
        _text(p, "x", BLUE, 15, True, QRectF(6, 4, 20, 22))
    elif name == "measure":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("#f5d77a"))
        p.translate(16, 16)
        p.rotate(-35)
        p.drawRect(QRectF(-13, -4, 26, 8))
        for i in range(-10, 12, 4):
            p.drawLine(QPointF(i, -4), QPointF(i, -1 if i % 8 else 1))
        p.resetTransform()
    # ----- sketch tools
    elif name == "line":
        p.setPen(_pen(INK, 2))
        p.drawLine(QPointF(6, 26), QPointF(26, 6))
        _dot(p, 6, 26)
        _dot(p, 26, 6)
    elif name == "rect":
        p.setPen(_pen(INK, 2))
        p.drawRect(QRectF(5, 9, 22, 15))
        _dot(p, 5, 24)
        _dot(p, 27, 9)
    elif name == "center_rect":
        p.setPen(_pen(INK, 2))
        p.drawRect(QRectF(5, 9, 22, 15))
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawLine(QPointF(5, 24), QPointF(27, 9))
        _dot(p, 16, 16.5)
        _dot(p, 27, 9)
    elif name == "circle":
        p.setPen(_pen(INK, 2))
        p.drawEllipse(QPointF(16, 16), 10, 10)
        _dot(p, 16, 16)
    elif name == "arc3":
        p.setPen(_pen(INK, 2))
        p.drawArc(QRectF(5, 8, 22, 22), 20 * 16, 140 * 16)
        _dot(p, 26.3, 15.2)
        _dot(p, 5.7, 15.2)
        _dot(p, 16, 8)
    elif name == "arc_center":
        p.setPen(_pen(INK, 2))
        p.drawArc(QRectF(5, 6, 22, 22), 0, 120 * 16)
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawLine(QPointF(16, 17), QPointF(27, 17))
        _dot(p, 16, 17)
        _dot(p, 27, 17)
    elif name == "point":
        _dot(p, 16, 16, INK, 3.5)
        p.setPen(_pen(INK, 1))
        p.drawLine(QPointF(9, 16), QPointF(23, 16))
        p.drawLine(QPointF(16, 9), QPointF(16, 23))
    elif name == "construction":
        p.setPen(_pen(INK, 2, Qt.DashLine))
        p.drawLine(QPointF(5, 26), QPointF(27, 6))
    elif name == "dimension":
        p.setPen(_pen(INK, 1.3))
        p.drawLine(QPointF(5, 8), QPointF(5, 26))
        p.drawLine(QPointF(27, 8), QPointF(27, 26))
        _arrow(p, 16, 20, 6, 20, INK, 1.3)
        _arrow(p, 16, 20, 26, 20, INK, 1.3)
        _text(p, "10", BLUE, 10, True, QRectF(0, 5, 32, 12))
    elif name == "use":
        _box(p, 5, 14, 15, 12, 6)
        p.setPen(_pen(ORANGE, 2.4))
        p.drawLine(QPointF(5, 14), QPointF(20, 14))
        p.drawLine(QPointF(20, 14), QPointF(26, 8))
    elif name == "trim":
        p.setPen(_pen(INK, 1.8))
        p.drawLine(QPointF(4, 16), QPointF(28, 16))
        p.drawLine(QPointF(16, 4), QPointF(16, 28))
        p.setPen(_pen(RED, 1.5))
        p.drawLine(QPointF(19, 11), QPointF(27, 21))
        p.drawLine(QPointF(27, 11), QPointF(19, 21))
    elif name == "delete":
        p.setPen(_pen(RED, 2.2))
        p.drawLine(QPointF(8, 8), QPointF(24, 24))
        p.drawLine(QPointF(24, 8), QPointF(8, 24))
    elif name in CONSTRAINT_GLYPHS:
        draw_constraint(name, p, QRectF(2, 2, 28, 28))
    # ----- tree items
    elif name == "part":
        _box(p, 6, 13, 14, 13, 6)
    elif name == "origin":
        _arrow(p, 10, 22, 26, 22, RED, 1.6)
        _arrow(p, 10, 22, 10, 6, BLUE, 1.6)
        _arrow(p, 10, 22, 19, 13, GREEN, 1.6)
    elif name == "fit":
        p.setPen(_pen(INK, 1.8))
        for (x, y, dx, dy) in ((4, 4, 1, 1), (28, 4, -1, 1), (4, 28, 1, -1), (28, 28, -1, -1)):
            p.drawLine(QPointF(x, y), QPointF(x + 7 * dx, y))
            p.drawLine(QPointF(x, y), QPointF(x, y + 7 * dy))
        _box(p, 11, 15, 8, 7, 4)
    elif name == "iso":
        _box(p, 7, 14, 13, 12, 6)
    elif name == "normal_to":
        p.setPen(_pen(BLUE, 1.4))
        p.setBrush(QColor(156, 195, 240, 160))
        p.drawRect(QRectF(6, 6, 20, 20))
        _dot(p, 16, 16, ORANGE, 3.5)
    elif name == "assembly":
        _box(p, 4, 17, 11, 10, 5)
        _box(p, 15, 12, 11, 10, 5, top=QColor("#b9d4b0"), side=QColor("#8fb486"), front=QColor("#d8e8d2"))
    elif name == "insert":
        _box(p, 5, 15, 13, 12, 6)
        p.setPen(_pen(GREEN, 2.4))
        p.drawLine(QPointF(24, 4), QPointF(24, 14))
        p.drawLine(QPointF(19, 9), QPointF(29, 9))
    elif name == "instance":
        _box(p, 7, 13, 13, 12, 6)
    elif name in ("m_fastened", "mate"):
        p.setPen(_pen(INK, 1.4))
        p.setBrush(FACE)
        p.drawRect(QRectF(4, 15, 24, 6))
        p.setBrush(FACE_DARK)
        p.drawRect(QRectF(4, 21, 24, 6))
        _dot(p, 16, 10, BLUE, 3.6)
        p.setPen(_pen(BLUE, 1.6))
        p.drawLine(QPointF(16, 10), QPointF(16, 18))
    elif name == "m_revolute":
        p.setPen(_pen(INK, 1.5))
        p.setBrush(FACE)
        p.drawEllipse(QRectF(7, 7, 18, 18))
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(13, 13, 6, 6))
        path = QPainterPath()
        path.arcMoveTo(QRectF(3, 3, 26, 26), 20)
        path.arcTo(QRectF(3, 3, 26, 26), 20, 140)
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(BLUE, 1.8))
        p.drawPath(path)
        _arrow(p, 7.2, 9.5, 5.6, 12.5, BLUE, 1.6)
    elif name == "m_slider":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(FACE_DARK)
        p.drawRect(QRectF(3, 19, 26, 5))
        p.setBrush(FACE)
        p.drawRect(QRectF(10, 11, 11, 8))
        _arrow(p, 15, 7, 27, 7, BLUE, 1.6)
        _arrow(p, 16, 7, 4, 7, BLUE, 1.6)
    elif name == "m_cylindrical":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(FACE_DARK)
        p.drawRect(QRectF(3, 14, 26, 5))
        p.setBrush(FACE)
        p.drawRoundedRect(QRectF(10, 10, 11, 13), 3, 3)
        _arrow(p, 15, 6, 27, 6, BLUE, 1.5)
        path = QPainterPath()
        path.arcMoveTo(QRectF(8, 21, 15, 8), 200)
        path.arcTo(QRectF(8, 21, 15, 8), 200, 140)
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(BLUE, 1.5))
        p.drawPath(path)
    elif name == "m_planar":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE_DARK)
        p.drawPolygon(QPolygonF([QPointF(2, 24), QPointF(10, 16), QPointF(30, 16), QPointF(22, 24)]))
        p.setBrush(FACE)
        p.drawRect(QRectF(12, 10, 9, 8))
        _arrow(p, 16, 6, 26, 6, BLUE, 1.4)
        _arrow(p, 16, 6, 10, 11, BLUE, 1.4)
    elif name == "m_ball":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(FACE_DARK)
        p.drawRect(QRectF(13, 18, 6, 11))
        p.setBrush(FACE)
        p.drawEllipse(QRectF(8, 6, 16, 16))
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(BLUE, 1.5))
        p.drawArc(QRectF(4, 2, 24, 24), 30 * 16, 120 * 16)
    elif name == "fix":
        p.setPen(_pen(INK, 1.6))
        p.drawLine(QPointF(16, 5), QPointF(16, 24))
        p.drawLine(QPointF(11, 9), QPointF(21, 9))
        path = QPainterPath()
        path.moveTo(7, 18)
        path.quadTo(8, 27, 16, 27)
        path.quadTo(24, 27, 25, 18)
        p.drawPath(path)
        _dot(p, 16, 5, INK, 2.4)
    elif name == "interference":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawRect(QRectF(4, 10, 15, 15))
        p.setBrush(QColor(156, 195, 240, 200))
        p.drawRect(QRectF(13, 5, 15, 15))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(217, 48, 37, 200))
        p.drawRect(QRectF(13.7, 10.7, 4.6, 8.6))
    elif name == "bom":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRoundedRect(QRectF(6, 4, 20, 24), 2, 2)
        for i, y in enumerate((10, 15, 20)):
            _dot(p, 10.5, y, BLUE, 1.6)
            p.setPen(_pen(INK, 1.4))
            p.drawLine(QPointF(14, y), QPointF(22, y))
    elif name == "explode":
        _box(p, 3, 20, 9, 8, 4)
        _box(p, 18, 9, 9, 8, 4)
        _arrow(p, 13, 18, 19, 13, BLUE, 1.5)
    elif name == "drawing":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(4, 6, 24, 20))
        p.setPen(_pen(INK, 1.0))
        p.drawRect(QRectF(17, 20, 11, 6))
        p.setPen(_pen(BLUE, 1.6))
        p.drawRect(QRectF(7, 9, 7, 6))
        p.drawRect(QRectF(16, 9, 4, 6))
        p.drawRect(QRectF(7, 17, 7, 3))
    elif name == "base_view":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(4, 5, 24, 22))
        p.setPen(_pen(BLUE, 2.0))
        p.setBrush(BLUE_FILL)
        p.drawRect(QRectF(10, 11, 12, 10))
    elif name == "proj_view":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(3, 5, 26, 22))
        p.setPen(_pen(INK, 1.5))
        p.setBrush(FACE)
        p.drawRect(QRectF(6, 9, 8, 8))
        p.setPen(_pen(BLUE, 1.8))
        p.setBrush(BLUE_FILL)
        p.drawRect(QRectF(19, 9, 6, 8))
        _arrow(p, 14.5, 13, 18, 13, BLUE, 1.4)
    elif name == "iso_view":
        _box(p, 7, 14, 13, 11, 6)
        p.setPen(_pen(INK, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(3, 4, 26, 24))
    elif name == "dim":
        p.setPen(_pen(INK, 1.3))
        p.drawLine(QPointF(5, 8), QPointF(5, 26))
        p.drawLine(QPointF(27, 8), QPointF(27, 26))
        p.setPen(_pen(BLUE, 1.6))
        p.drawLine(QPointF(7, 21), QPointF(25, 21))
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        p.drawPolygon(QPolygonF([QPointF(5.5, 21), QPointF(10, 19), QPointF(10, 23)]))
        p.drawPolygon(QPolygonF([QPointF(26.5, 21), QPointF(22, 19), QPointF(22, 23)]))
        _text(p, "25", INK, 9, True, QRectF(0, 7, 32, 12))
    elif name == "note":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("#fff7d6"))
        p.drawRoundedRect(QRectF(5, 6, 22, 20), 2, 2)
        _text(p, "A", INK, 13, True, QRectF(5, 6, 22, 20))
    elif name == "sheet":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(5, 4, 22, 24))
        p.setPen(_pen(INK, 1.0))
        p.drawRect(QRectF(13, 21, 14, 7))
        p.drawLine(QPointF(13, 24.5), QPointF(27, 24.5))
        p.drawLine(QPointF(20, 21), QPointF(20, 28))
    elif name == "pdf":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(6, 3, 20, 26))
        p.setPen(Qt.NoPen)
        p.setBrush(RED)
        p.drawRoundedRect(QRectF(3, 15, 22, 10), 2, 2)
        _text(p, "PDF", QColor("white"), 8, True, QRectF(3, 15, 22, 10))
    elif name == "dxf":
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(6, 3, 20, 26))
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        p.drawRoundedRect(QRectF(3, 15, 22, 10), 2, 2)
        _text(p, "DXF", QColor("white"), 8, True, QRectF(3, 15, 22, 10))
    elif name == "sm_base":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(4, 20), QPointF(14, 14), QPointF(28, 18), QPointF(18, 24)]))
        p.setBrush(FACE_DARK)
        p.drawPolygon(QPolygonF([QPointF(4, 20), QPointF(18, 24), QPointF(18, 26.5), QPointF(4, 22.5)]))
        p.drawPolygon(QPolygonF([QPointF(18, 24), QPointF(28, 18), QPointF(28, 20.5), QPointF(18, 26.5)]))
    elif name == "sm_flange":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(3, 22), QPointF(13, 16), QPointF(25, 19), QPointF(15, 25)]))
        p.setBrush(QColor("#b9d4b0"))
        p.drawPolygon(QPolygonF([QPointF(25, 19), QPointF(15, 25), QPointF(15, 9), QPointF(25, 3)]))
        p.setPen(_pen(GREEN, 2.0))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(11, 19, 8, 8), 90 * 16, 90 * 16)
    elif name == "flat":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawPolygon(QPolygonF([QPointF(4, 10), QPointF(28, 10), QPointF(28, 24), QPointF(4, 24)]))
        p.setPen(_pen(RED, 1.4, Qt.DashLine))
        p.drawLine(QPointF(10, 10), QPointF(10, 24))
        p.drawLine(QPointF(22, 10), QPointF(22, 24))
    elif name == "flat_dxf":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(5, 4, 22, 16))
        p.setPen(_pen(RED, 1.3, Qt.DashLine))
        p.drawLine(QPointF(11, 4), QPointF(11, 20))
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        p.drawRoundedRect(QRectF(3, 19, 22, 10), 2, 2)
        _text(p, "DXF", QColor("white"), 8, True, QRectF(3, 19, 22, 10))
    elif name == "error":
        p.setPen(Qt.NoPen)
        p.setBrush(RED)
        p.drawEllipse(QPointF(16, 16), 11, 11)
        _text(p, "!", QColor("white"), 16)
    else:
        _text(p, name[:2].upper())


CONSTRAINT_GLYPHS = {
    "coincident": "●", "horizontal": "H", "vertical": "V", "parallel": "∥", "perpendicular": "⊥",
    "tangent": "◡", "equal": "=", "midpoint": "M", "concentric": "◎", "fix": "⚓", "point_on": "●",
    "symmetric": "⋈",
}


def draw_constraint(name, p: QPainter, rect: QRectF):
    s = CONSTRAINT_GLYPHS.get(name, "?")
    f = QFont()
    f.setPixelSize(int(rect.height() * 0.62))
    f.setBold(True)
    p.setFont(f)
    p.setPen(INK)
    p.drawText(rect, Qt.AlignCenter, s)


@lru_cache(maxsize=None)
def icon(name: str, size: int = 32) -> QIcon:
    ic = QIcon()
    for scale in (1, 2):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size * scale / 32, size * scale / 32)
        draw(name, p)
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic
