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
    if name.startswith("plugin:"):
        name = "plugin"
    if name == "undo" or name == "redo":
        if name == "redo":
            p.translate(32, 0)
            p.scale(-1, 1)
        # a hooked arrow, as in most apps: from the lower right, up and round, pointing back to the left
        path = QPainterPath()
        path.moveTo(12, 24)
        path.lineTo(19, 24)
        path.cubicTo(24.5, 24, 27, 20.5, 27, 16.75)
        path.cubicTo(27, 13, 24.5, 9.5, 19, 9.5)
        path.lineTo(10, 9.5)
        p.setPen(_pen(INK, 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setBrush(INK)
        p.setPen(_pen(INK, 1.0))
        p.drawPolygon(QPolygonF([QPointF(4, 9.5), QPointF(11, 4.5), QPointF(11, 14.5)]))
    elif name == "sketch":
        # a sketch plane seen at an angle, a curve drawn on it, and the pencil drawing it
        p.setPen(_pen(BLUE, 1.3))
        p.setBrush(QColor(156, 195, 240, 110))
        p.drawPolygon(QPolygonF([QPointF(2, 25), QPointF(10, 16), QPointF(30, 16), QPointF(22, 25)]))
        path = QPainterPath()
        path.moveTo(7, 22.5)
        path.cubicTo(10, 18.5, 13, 23.5, 16.5, 20.5)
        p.setPen(_pen(INK, 1.7))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        _dot(p, 7, 22.5, INK, 1.5)
        p.save()                                    # the pencil, tip at the end of the curve
        p.translate(16.5, 20.5)
        p.rotate(-50)
        p.setPen(_pen(INK, 1.1))
        p.setBrush(QColor("#f5c542"))               # body
        p.drawRect(QRectF(4.5, -2.4, 13, 4.8))
        p.setBrush(QColor("#e88a9a"))               # eraser
        p.drawRect(QRectF(17.5, -2.4, 3, 4.8))
        p.setBrush(QColor("#f1d9b5"))               # sharpened wood
        p.drawPolygon(QPolygonF([QPointF(4.5, -2.4), QPointF(0, 0), QPointF(4.5, 2.4)]))
        p.setPen(Qt.NoPen)
        p.setBrush(INK)                             # graphite point
        p.drawPolygon(QPolygonF([QPointF(1.6, -0.85), QPointF(0, 0), QPointF(1.6, 0.85)]))
        p.restore()
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
    elif name == "delete_face":
        _box(p, 4, 12, 18, 14, 6)
        p.setPen(_pen(RED, 2.6))
        p.drawLine(QPointF(9, 17), QPointF(17, 25))
        p.drawLine(QPointF(17, 17), QPointF(9, 25))
    elif name == "move_face":
        _box(p, 4, 14, 16, 13, 6)
        _arrow(p, 12, 20, 25, 7, ORANGE, 2.4)
    elif name == "offset_surface":
        _box(p, 4, 14, 16, 13, 6)
        p.setPen(_pen(ORANGE, 2.2))
        p.drawLine(QPointF(8, 9), QPointF(22, 9))
        p.drawLine(QPointF(22, 9), QPointF(27, 6))
    elif name == "search":
        p.setPen(_pen(INK, 2.2))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(14, 14), 8, 8)
        p.drawLine(QPointF(20, 20), QPointF(27, 27))
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
    elif name == "mate_connector":
        p.setPen(_pen(QColor(214, 58, 58), 2.0))
        p.drawLine(QPointF(14, 18), QPointF(27, 18))
        p.setPen(_pen(QColor(46, 160, 70), 2.0))
        p.drawLine(QPointF(14, 18), QPointF(8, 27))
        p.setPen(_pen(BLUE, 2.0))
        p.drawLine(QPointF(14, 18), QPointF(14, 4))
        _dot(p, 14, 18, ORANGE, 3.0)
    elif name == "curve_pattern":
        path = QPainterPath(QPointF(4, 26))
        path.cubicTo(QPointF(10, 4), QPointF(22, 4), QPointF(28, 24))
        p.setPen(_pen(BLUE, 1.2))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        for k, (x, y) in enumerate(((5.5, 22), (12, 11), (21, 11), (27, 21))):
            p.setPen(_pen(INK if k == 0 else ORANGE, 1.2))
            p.setBrush(FACE if k == 0 else QColor(240, 138, 36, 80))
            p.drawRect(QRectF(x - 2.6, y - 2.6, 5.2, 5.2))
    elif name == "point_pattern":
        for k, (x, y) in enumerate(((7, 8), (24, 9), (9, 24), (22, 23))):
            p.setPen(_pen(INK if k == 0 else ORANGE, 1.2))
            p.setBrush(FACE if k == 0 else QColor(240, 138, 36, 80))
            p.drawRect(QRectF(x - 3.5, y - 3.5, 7, 7))
            _dot(p, x, y, BLUE, 1.3)
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
    elif name == "sk_chamfer":
        p.setPen(_pen(INK, 2))
        p.drawPolyline(QPolygonF([QPointF(5, 27), QPointF(5, 14), QPointF(14, 5), QPointF(27, 5)]))
        p.setPen(_pen(ORANGE, 1.4, Qt.DashLine))
        p.drawLine(QPointF(5, 5), QPointF(5, 12))
        p.drawLine(QPointF(5, 5), QPointF(12, 5))
    elif name == "sk_split":
        p.setPen(_pen(INK, 2))
        p.drawLine(QPointF(4, 22), QPointF(14, 14))
        p.drawLine(QPointF(18, 11), QPointF(28, 4))
        _dot(p, 16, 12.5)
        p.setPen(_pen(RED, 1.5))
        p.drawLine(QPointF(12, 6), QPointF(20, 19))
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
    elif name in ("sm_hem", "sm_jog", "sm_tab", "sm_convert"):
        p.setPen(_pen(INK, 1.4))
        p.setBrush(FACE)
        if name == "sm_hem":
            p.drawRect(QRectF(3, 20, 24, 4))
            path = QPainterPath(QPointF(27, 20))
            path.cubicTo(QPointF(31, 20), QPointF(31, 13), QPointF(27, 13))
            p.setBrush(Qt.NoBrush)
            p.setPen(_pen(BLUE, 1.8))
            p.drawPath(path)
            p.drawLine(QPointF(27, 13), QPointF(15, 13))
        elif name == "sm_jog":
            p.drawPolygon(QPolygonF([QPointF(2, 24), QPointF(12, 24), QPointF(18, 12), QPointF(30, 12),
                                     QPointF(30, 15), QPointF(20, 15), QPointF(14, 27), QPointF(2, 27)]))
        elif name == "sm_tab":
            p.drawRect(QRectF(3, 14, 20, 12))
            p.setBrush(BLUE_FILL)
            p.setPen(_pen(BLUE, 1.4))
            p.drawRect(QRectF(23, 17, 6, 6))
        else:
            p.drawRect(QRectF(4, 12, 22, 6))
            _arrow(p, 15, 26, 15, 21, BLUE, 1.6)
            p.setPen(_pen(BLUE, 1.6))
            p.drawLine(QPointF(26, 12), QPointF(30, 5))
    elif name == "plugin":
        # a jigsaw piece
        path = QPainterPath()
        path.moveTo(6, 10)
        path.lineTo(12, 10)
        path.cubicTo(10, 4, 20, 4, 18, 10)
        path.lineTo(24, 10)
        path.lineTo(24, 15)
        path.cubicTo(30, 13, 30, 23, 24, 21)
        path.lineTo(24, 27)
        path.lineTo(6, 27)
        path.closeSubpath()
        p.setPen(_pen(INK, 1.3))
        p.setBrush(BLUE_FILL)
        p.drawPath(path)
    elif name == "history":
        p.setPen(_pen(INK, 1.5))
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(17, 17), 11, 11)
        p.setPen(_pen(BLUE, 1.8))
        p.drawLine(QPointF(17, 17), QPointF(17, 10))
        p.drawLine(QPointF(17, 17), QPointF(22, 20))
        _arrow(p, 9, 9, 5, 5, INK, 1.4)
    elif name == "derived":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawRect(QRectF(3, 4, 10, 10))
        p.setBrush(BLUE_FILL)
        p.drawRect(QRectF(18, 17, 11, 11))
        _arrow(p, 12, 13, 19, 18, BLUE, 1.6)
    elif name == "configs":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(4, 6, 24, 20))
        p.setPen(_pen(INK, 1.0))
        for y in (12, 19):
            p.drawLine(QPointF(4, y), QPointF(28, y))
        p.drawLine(QPointF(13, 6), QPointF(13, 26))
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        p.drawRect(QRectF(13.5, 12.5, 14, 6))
    elif name == "variable":
        p.setPen(_pen(INK, 1.8))
        p.drawArc(QRectF(3, 5, 10, 22), 100 * 16, 160 * 16)
        p.drawArc(QRectF(19, 5, 10, 22), -80 * 16, 160 * 16)
        _text(p, "x", BLUE, 15, True, QRectF(6, 4, 20, 22))
    elif name == "variables_table":
        # a little table: a # column and values
        p.setPen(_pen(INK, 1.4))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(4, 5, 24, 22))
        p.setBrush(QColor("#dbe8f8"))
        p.drawRect(QRectF(4, 5, 24, 6))
        p.setBrush(Qt.NoBrush)
        for y in (16.5, 22):
            p.drawLine(QPointF(4, y), QPointF(28, y))
        p.drawLine(QPointF(13, 5), QPointF(13, 27))
        _text(p, "#", BLUE, 9, True, QRectF(4, 11, 9, 16))
    elif name == "measure":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("#f5d77a"))
        p.translate(16, 16)
        p.rotate(-35)
        p.drawRect(QRectF(-13, -4, 26, 8))
        for i in range(-10, 12, 4):
            p.drawLine(QPointF(i, -4), QPointF(i, -1 if i % 8 else 1))
        p.resetTransform()
    elif name == "thicken":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawPolygon(QPolygonF([QPointF(3, 20), QPointF(13, 14), QPointF(29, 14), QPointF(19, 20)]))
        p.setBrush(FACE_DARK)
        p.drawPolygon(QPolygonF([QPointF(3, 20), QPointF(19, 20), QPointF(19, 26), QPointF(3, 26)]))
        p.drawPolygon(QPolygonF([QPointF(19, 20), QPointF(29, 14), QPointF(29, 20), QPointF(19, 26)]))
        p.setPen(_pen(BLUE, 1.8))
        p.drawLine(QPointF(16, 4), QPointF(16, 12))
        p.drawLine(QPointF(13, 7), QPointF(16, 4))
        p.drawLine(QPointF(19, 7), QPointF(16, 4))
    elif name == "enclose":
        p.setPen(_pen(BLUE, 1.8))
        p.drawLine(QPointF(3, 8), QPointF(29, 8))
        p.drawLine(QPointF(3, 25), QPointF(29, 25))
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawEllipse(QRectF(8, 8, 16, 17))
    elif name == "fill":
        # a patch filling a loop: the loop's edges in blue, the patch shaded inside them
        path = QPainterPath(QPointF(5, 20))
        path.cubicTo(QPointF(8, 6), QPointF(24, 4), QPointF(27, 14))
        path.cubicTo(QPointF(29, 24), QPointF(12, 30), QPointF(5, 20))
        p.setPen(Qt.NoPen)
        p.setBrush(FACE)
        p.drawPath(path)
        p.setPen(_pen(INK, 1.0))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(8, 10, 18, 10), 20 * 16, 140 * 16)
        p.setPen(_pen(BLUE, 2.0))
        p.drawPath(path)
    elif name == "constrained_surface":
        # a curved surface drawn through scattered points
        path = QPainterPath(QPointF(3, 22))
        path.cubicTo(QPointF(10, 10), QPointF(20, 26), QPointF(29, 12))
        path.lineTo(QPointF(29, 18))
        path.cubicTo(QPointF(20, 32), QPointF(10, 16), QPointF(3, 28))
        path.closeSubpath()
        p.setPen(_pen(INK, 1.2))
        p.setBrush(FACE)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        for x, y in ((6, 19), (12, 16), (18, 20), (24, 17), (28, 13), (9, 24), (21, 25)):
            p.drawEllipse(QPointF(x, y), 1.8, 1.8)
    elif name == "rib":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE_DARK)
        p.drawPolygon(QPolygonF([QPointF(4, 4), QPointF(9, 4), QPointF(9, 23), QPointF(28, 23), QPointF(28, 28),
                                 QPointF(4, 28)]))
        p.setBrush(BLUE_FILL)
        p.drawPolygon(QPolygonF([QPointF(9, 10), QPointF(22, 23), QPointF(9, 23)]))
    elif name == "delete_part":
        _box(p, 5, 12, 16, 13, 6)
        p.setPen(_pen(RED, 2.6))
        p.drawLine(QPointF(17, 4), QPointF(28, 15))
        p.drawLine(QPointF(28, 4), QPointF(17, 15))
    elif name == "helix":
        p.setPen(_pen(BLUE, 1.8))
        for i in range(4):
            y = 5 + i * 6
            p.drawArc(QRectF(6, y, 20, 7), 0, 180 * 16)
            p.drawArc(QRectF(6, y + 3, 20, 7), 180 * 16, 180 * 16)
    elif name == "compound":
        # two blocks side by side sharing one face (drawn heavier)
        p.setPen(_pen(INK, 1.2))
        p.setBrush(FACE)
        p.drawRect(QRectF(3, 9, 13, 16))
        p.setBrush(QColor("#f5d77a"))
        p.drawRect(QRectF(16, 9, 13, 16))
        p.setPen(_pen(BLUE, 2.6))
        p.drawLine(QPointF(16, 8), QPointF(16, 26))
    elif name == "explode_part":
        # two blocks pulled apart, with arrows
        p.setPen(_pen(INK, 1.2))
        p.setBrush(FACE)
        p.drawRect(QRectF(2, 10, 11, 14))
        p.setBrush(QColor("#f5d77a"))
        p.drawRect(QRectF(19, 10, 11, 14))
        p.setPen(_pen(BLUE, 1.6))
        p.drawLine(QPointF(14, 17), QPointF(18, 17))
        for x, d in ((14, 1), (18, -1)):
            p.drawLine(QPointF(x, 17), QPointF(x + 2.5 * d, 14.5))
            p.drawLine(QPointF(x, 17), QPointF(x + 2.5 * d, 19.5))
    elif name == "bounding_box":
        p.setPen(_pen(INK, 1.0, Qt.DashLine))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(3, 6, 23, 21))
        p.setPen(_pen(INK, 1.3))
        p.setBrush(FACE)
        p.drawEllipse(QRectF(8, 10, 13, 13))
    elif name == "measure_value":
        p.setPen(_pen(INK, 1.2))
        p.setBrush(QColor("#f5d77a"))
        p.drawRect(QRectF(3, 20, 26, 8))
        for i in range(6, 28, 4):
            p.drawLine(QPointF(i, 20), QPointF(i, 23 if i % 8 else 25))
        _text(p, "#", BLUE, 14, True, QRectF(4, 1, 24, 18))
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
    elif name == "hexagon":
        p.setPen(_pen(INK, 2))
        pts = [QPointF(16 + 11 * math.cos(math.radians(a)), 16 + 11 * math.sin(math.radians(a)))
               for a in range(0, 360, 60)]
        p.drawPolygon(QPolygonF(pts))
        _dot(p, 16, 16)
        _dot(p, 27, 16)
    elif name in ("polygon", "polygon_out"):
        R = 11 if name == "polygon" else 12.5
        pts = [QPointF(16 + R * math.cos(math.radians(a)), 16.5 - R * math.sin(math.radians(a)))
               for a in range(90, 450, 72)]
        p.setPen(_pen(INK, 1, Qt.DashLine))
        r = R if name == "polygon" else R * math.cos(math.pi / 5)
        p.drawEllipse(QPointF(16, 16.5), r, r)
        p.setPen(_pen(INK, 2))
        p.drawPolygon(QPolygonF(pts))
        _dot(p, 16, 16.5)
        if name == "polygon":
            _dot(p, pts[0].x(), pts[0].y())
        else:
            m = (pts[0] + pts[1]) / 2
            _dot(p, m.x(), m.y())
    elif name == "line_mid":
        p.setPen(_pen(INK, 2))
        p.drawLine(QPointF(5, 26), QPointF(27, 6))
        _dot(p, 27, 6)
        _dot(p, 16, 16, ORANGE, 3)
    elif name == "rect3":
        pts = [QPointF(4, 19), QPointF(17, 6), QPointF(28, 17), QPointF(15, 30)]
        p.setPen(_pen(INK, 2))
        p.drawPolygon(QPolygonF([QPointF(q.x(), q.y() - 2) for q in pts]))
        for q in pts[:3]:
            _dot(p, q.x(), q.y() - 2)
    elif name == "circle":
        p.setPen(_pen(INK, 2))
        p.drawEllipse(QPointF(16, 16), 10, 10)
        _dot(p, 16, 16)
    elif name == "spline":
        p.setPen(_pen(INK, 2))
        path = QPainterPath(QPointF(4, 24))
        path.cubicTo(QPointF(10, 2), QPointF(18, 30), QPointF(28, 8))
        p.drawPath(path)
        for x, y in ((4, 24), (12, 13), (20, 19), (28, 8)):
            _dot(p, x, y)
    elif name == "ellipse":
        p.setPen(_pen(INK, 2))
        p.drawEllipse(QPointF(16, 16), 13, 8)
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawLine(QPointF(16, 16), QPointF(29, 16))
        _dot(p, 16, 16)
    elif name == "sk_text":
        f = QFont("Times New Roman")
        f.setPixelSize(24)
        f.setBold(True)
        p.setFont(f)
        p.setPen(INK)
        p.drawText(QRectF(0, 0, 32, 30), Qt.AlignCenter, "A")
        p.setPen(_pen(BLUE, 1.6))
        p.drawLine(QPointF(5, 28), QPointF(27, 28))
    elif name == "arc3":
        p.setPen(_pen(INK, 2))
        p.drawArc(QRectF(5, 8, 22, 22), 20 * 16, 140 * 16)
        _dot(p, 26.3, 15.2)
        _dot(p, 5.7, 15.2)
        _dot(p, 16, 8)
    elif name == "arc_tan":
        p.setPen(_pen(INK, 2))
        p.drawLine(QPointF(3, 24), QPointF(14, 24))
        p.drawArc(QRectF(4, 6, 20, 20), 270 * 16, 180 * 16)
        _dot(p, 14, 24, ORANGE, 2.8)
        _dot(p, 14, 6)
    elif name == "ellipse_arc":
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawEllipse(QRectF(3, 9, 26, 14))
        p.setPen(_pen(INK, 2))
        p.drawArc(QRectF(3, 9, 26, 14), 0, 180 * 16)
        _dot(p, 29, 16)
        _dot(p, 3, 16)
    elif name == "conic":
        p.setPen(_pen(INK, 1, Qt.DashLine))
        p.drawPolyline(QPolygonF([QPointF(4, 26), QPointF(16, 4), QPointF(28, 26)]))
        p.setPen(_pen(INK, 2))
        path = QPainterPath(QPointF(4, 26))
        path.quadTo(QPointF(16, 2), QPointF(28, 26))
        p.drawPath(path)
        _dot(p, 4, 26)
        _dot(p, 28, 26)
        _dot(p, 16, 4, ORANGE)
    elif name == "spline_cp":
        p.setPen(_pen(INK, 1, Qt.DashLine))
        ctrl = [QPointF(4, 26), QPointF(10, 4), QPointF(22, 28), QPointF(28, 6)]
        p.drawPolyline(QPolygonF(ctrl))
        p.setPen(_pen(INK, 2))
        path = QPainterPath(ctrl[0])
        path.cubicTo(ctrl[1], ctrl[2], ctrl[3])
        p.drawPath(path)
        for q in ctrl:
            p.setPen(_pen(BLUE, 1.4))
            p.setBrush(QColor("white"))
            p.drawRect(QRectF(q.x() - 2.2, q.y() - 2.2, 4.4, 4.4))
        p.setBrush(Qt.NoBrush)
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
    elif name == "use_intersect":
        _box(p, 5, 14, 15, 12, 6)
        p.setPen(_pen(BLUE, 1.2, Qt.DashLine))
        p.drawLine(QPointF(2, 20), QPointF(30, 20))
        p.setPen(_pen(ORANGE, 2.4))
        p.drawLine(QPointF(5, 20), QPointF(20, 20))
        p.drawLine(QPointF(20, 20), QPointF(23.5, 16.5))
    elif name == "sk_extend":
        p.setPen(_pen(INK, 2))
        p.drawLine(QPointF(4, 18), QPointF(15, 18))
        p.drawLine(QPointF(26, 5), QPointF(26, 28))
        p.setPen(_pen(ORANGE, 1.6, Qt.DashLine))
        p.drawLine(QPointF(15, 18), QPointF(25, 18))
        _dot(p, 15, 18)
    elif name in ("sk_transform", "sk_copy"):
        p.setPen(_pen(INK, 1.4, Qt.DashLine) if name == "sk_transform" else _pen(INK, 2))
        p.drawRect(QRectF(4, 14, 12, 12))
        p.setPen(_pen(INK, 2))
        p.save()
        p.translate(21, 11)
        p.rotate(-20)
        p.drawRect(QRectF(-6, -6, 12, 12))
        p.restore()
        p.setPen(_pen(ORANGE, 1.6))
        p.drawLine(QPointF(12, 17), QPointF(17, 13))
        p.drawLine(QPointF(17, 13), QPointF(14, 13))
        p.drawLine(QPointF(17, 13), QPointF(17, 16))
    elif name in ("insert_dxf", "insert_image"):
        p.setPen(_pen(INK, 1.6))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(5, 4, 19, 24))
        if name == "insert_dxf":
            _text(p, "DXF", BLUE, 7, True, QRectF(5, 7, 19, 10))
            p.setPen(_pen(INK, 1.4))
            p.drawLine(QPointF(8, 23), QPointF(20, 23))
            p.drawArc(QRectF(8, 17, 8, 8), 0, 180 * 16)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#9cc3f0"))
            p.drawPolygon(QPolygonF([QPointF(7, 25), QPointF(13, 15), QPointF(17, 21), QPointF(19, 18),
                                     QPointF(22, 25)]))
            p.setBrush(ORANGE)
            p.drawEllipse(QPointF(18, 10), 2.4, 2.4)
        p.setBrush(Qt.NoBrush)
        p.setPen(_pen(ORANGE, 2.2))
        p.drawLine(QPointF(27, 18), QPointF(27, 28))
        p.drawLine(QPointF(22, 23), QPointF(32, 23))
    elif name in ("dir_normal", "dir_flipped"):
        # the direction arrow on Extrude, Revolve …: up normally, down when flipped (as Onshape's)
        p.setPen(_pen(INK, 1.2))
        p.setBrush(QColor("#e8eef7"))
        p.drawPolygon(QPolygonF([QPointF(6, 26), QPointF(26, 26), QPointF(22, 21), QPointF(10, 21)]))
        up = name == "dir_normal"
        tip, tail = (4.0, 19.0) if up else (19.0, 4.0)
        p.setPen(_pen(BLUE, 2.6))
        p.drawLine(QPointF(16, tail), QPointF(16, tip + (3 if up else -3)))
        p.setPen(Qt.NoPen)
        p.setBrush(BLUE)
        d = 1 if up else -1
        p.drawPolygon(QPolygonF([QPointF(16, tip), QPointF(10.5, tip + 7 * d), QPointF(21.5, tip + 7 * d)]))
        p.setBrush(Qt.NoBrush)
    elif name == "finish_sketch":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1e8e3e"))
        p.drawEllipse(QPointF(16, 16), 12, 12)
        p.setPen(_pen(QColor("white"), 3))
        p.drawPolyline(QPolygonF([QPointF(10, 16.5), QPointF(14.5, 21), QPointF(22.5, 11.5)]))
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
    elif name == "relation":
        p.setPen(_pen(INK, 1.5))
        p.setBrush(FACE)
        for cx, cy, r, n in ((11, 13, 7, 8), (23, 21, 5, 6)):
            p.drawEllipse(QPointF(cx, cy), r, r)
            for k in range(n):
                a = 2 * math.pi * k / n
                p.drawLine(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)),
                           QPointF(cx + (r + 2.2) * math.cos(a), cy + (r + 2.2) * math.sin(a)))
        _dot(p, 11, 13)
        _dot(p, 23, 21)
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
    elif name in ("balloon", "auto_balloon"):
        p.setPen(_pen(BLUE, 1.6))
        p.setBrush(QColor("white"))
        p.drawEllipse(QPointF(19, 12), 8, 8)
        p.drawLine(QPointF(13.5, 17.5), QPointF(5, 27))
        _text(p, "1", BLUE, 10, True, QRectF(11, 4, 16, 16))
        if name == "auto_balloon":
            p.setPen(_pen(INK, 1.2))
            p.drawEllipse(QPointF(7, 7), 4, 4)
    elif name == "section_view":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(5, 9, 22, 14))
        p.save()
        p.setClipRect(QRectF(5, 9, 22, 14))
        p.setPen(_pen(INK, 0.9))
        for k in range(-16, 30, 4):
            p.drawLine(QPointF(5 + k, 23), QPointF(19 + k, 9))
        p.restore()
        p.setPen(_pen(BLUE, 1.8))
        p.drawLine(QPointF(3, 4), QPointF(29, 4))
        _arrow(p, 4, 4, 4, 8, BLUE, 1.4)
        _arrow(p, 28, 4, 28, 8, BLUE, 1.4)
    elif name == "detail_view":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(3, 12, 14, 14))
        p.setPen(_pen(BLUE, 1.4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(11, 19), 3.5, 3.5)
        p.drawEllipse(QPointF(23, 11), 7.5, 7.5)
        p.drawLine(QPointF(13.5, 16.5), QPointF(17.5, 14))
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
    elif name == "nest":
        p.setPen(_pen(INK, 1.3))
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(3, 5, 26, 22))
        p.setPen(_pen(BLUE, 1.5))
        p.setBrush(BLUE_FILL)
        p.drawRect(QRectF(6, 8, 9, 7))
        p.drawRect(QRectF(17, 8, 9, 7))
        p.drawRect(QRectF(6, 17, 13, 7))
        p.drawPolygon(QPolygonF([QPointF(21, 17), QPointF(26, 17), QPointF(26, 24)]))
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
    elif name == "folder":
        p.setPen(_pen(INK, 1.6))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath()
        path.moveTo(4, 9)
        path.lineTo(12, 9)
        path.lineTo(15, 12)
        path.lineTo(28, 12)
        path.lineTo(28, 25)
        path.lineTo(4, 25)
        path.closeSubpath()
        p.drawPath(path)
    elif name == "new_folder":
        draw("folder", p)
        p.setPen(_pen(BLUE, 2.2))
        p.drawLine(QPointF(16, 15), QPointF(16, 23))
        p.drawLine(QPointF(12, 19), QPointF(20, 19))
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
    "symmetric": "⋈", "normal": "⊾",
}


def draw_constraint(name, p: QPainter, rect: QRectF):
    s = CONSTRAINT_GLYPHS.get(name, "?")
    f = QFont()
    f.setPixelSize(int(rect.height() * 0.62))
    f.setBold(True)
    p.setFont(f)
    p.setPen(INK)
    p.drawText(rect, Qt.AlignCenter, s)


def user_icons_dir() -> str:
    """Your own icons: $FREESHAPE_ICONS, or an 'icons' folder in FreeShape's data folder (next to 'plugins')."""
    import os
    env = os.environ.get("FREESHAPE_ICONS")
    if env:
        return env
    base = None
    try:
        from PySide6.QtCore import QStandardPaths
        g = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        base = os.path.join(g, "FreeShape") if g else None
    except Exception:
        pass
    return os.path.normpath(os.path.join(base or os.path.expanduser("~/.freeshape"), "icons"))


def file_stem(name: str) -> str:
    """The file name for an icon: 'extrude', 'plugin_gridfinity' (for 'plugin:gridfinity') …"""
    return name.replace(":", "_")


def user_icon_path(name: str):
    """Your icon for `name` (an .svg or .png in the icons folder), or None."""
    import os
    d = user_icons_dir()
    for ext in (".svg", ".png"):
        path = os.path.join(d, file_stem(name) + ext)
        if os.path.isfile(path):
            return path
    return None


def builtin_icon(name: str, size: int = 32) -> QIcon:
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


@lru_cache(maxsize=None)
def icon(name: str, size: int = 32) -> QIcon:
    """The icon for a tool: yours from the icons folder if there is one, otherwise the built-in drawing. A plugin
    feature without its own icon gets the jigsaw piece."""
    path = user_icon_path(name)
    if path:
        ic = QIcon(path)
        if not ic.isNull():
            return ic
    return builtin_icon(name, size)


def export_builtin(names, folder, size=64) -> int:
    """Write the built-in icons as PNGs (a starting point for your own); returns how many were written."""
    import os
    os.makedirs(folder, exist_ok=True)
    n = 0
    for name in sorted(set(names)):
        pm = builtin_icon(name, size).pixmap(size, size)
        if pm.save(os.path.join(folder, file_stem(name) + ".png")):
            n += 1
    with open(os.path.join(folder, "names.txt"), "w", encoding="utf-8") as fh:
        fh.write("Icon file names (add .svg or .png):\n\n" + "\n".join(file_stem(x) for x in sorted(set(names))) + "\n")
    return n



# ---------------------------------------------------------------------- a dialog's ✓ and ✕ (drawn, not font glyphs)
def _dialog_mark(kind: str, size: int) -> QIcon:
    """A clean tick (green) or cross (red) for a dialog's Accept / Cancel button, in the theme's colours: one
    smooth stroke with round ends, the same weight on every screen and font (the ✓ / ✕ characters weren't)."""
    from . import theme
    col = QColor(theme.OK_GREEN if kind == "ok" else theme.ERROR)
    ic = QIcon()
    for scale in (1, 2, 3):
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size * scale / 24, size * scale / 24)
        pen = QPen(col, 2.6 if kind == "ok" else 2.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if kind == "ok":
            path = QPainterPath(QPointF(5.0, 12.6))
            path.lineTo(QPointF(9.8, 17.2))
            path.lineTo(QPointF(19.0, 7.0))
            p.drawPath(path)
        else:
            p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
            p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))
        p.end()
        pm.setDevicePixelRatio(scale)
        ic.addPixmap(pm)
    return ic


def dialog_button(btn, kind: str, size: int = 18):
    """Make a QToolButton a dialog's Accept ("ok") or Cancel ("cancel") button: the drawn tick / cross, a round
    tinted hover (see theme: #okButton / #cancelButton)."""
    from PySide6.QtCore import QSize
    btn.setObjectName("okButton" if kind == "ok" else "cancelButton")
    btn.setText("")
    btn.setIcon(_dialog_mark(kind, size))
    btn.setIconSize(QSize(size, size))
    btn.setFixedSize(size + 12, size + 12)
    btn.setAutoRaise(True)
    btn.setCursor(Qt.PointingHandCursor)
    return btn
