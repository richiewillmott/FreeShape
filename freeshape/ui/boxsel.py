"""Box (rubber-band) selection helpers shared by the 3D viewport and the sketcher.

Drag left-to-right: a blue solid box that selects only what is fully enclosed.
Drag right-to-left: a green dashed box that selects everything fully enclosed OR partly inside (touching).
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPen

WINDOW_COLOR = (31, 111, 209)       # fully enclosed
CROSSING_COLOR = (46, 160, 67)      # enclosed or partially inside


def is_crossing(x0, y0, x1, y1) -> bool:
    """A box dragged right-to-left selects everything it touches."""
    return x1 < x0


def norm_rect(x0, y0, x1, y1):
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def draw_box(p, box):
    """Paint the rubber-band box (x0, y0, x1, y1) with a QPainter."""
    x0, y0, x1, y1 = box
    r, g, b = CROSSING_COLOR if is_crossing(x0, y0, x1, y1) else WINDOW_COLOR
    col = QColor(r, g, b)
    fill = QColor(r, g, b, 38)
    pen = QPen(col, 1.3)
    if is_crossing(x0, y0, x1, y1):
        pen.setStyle(Qt.DashLine)
    p.setPen(pen)
    p.setBrush(fill)
    a, b_, c, d = norm_rect(x0, y0, x1, y1)
    p.drawRect(QRectF(a, b_, c - a, d - b_))


def points_in_rect(S, rect):
    """Boolean mask of the (n, 2) screen points inside rect."""
    x0, y0, x1, y1 = rect
    return (S[:, 0] >= x0) & (S[:, 0] <= x1) & (S[:, 1] >= y0) & (S[:, 1] <= y1)


def segs_hit_rect(A, B, rect):
    """Boolean mask: which segments A[i]->B[i] (n, 2 arrays) touch rect."""
    x0, y0, x1, y1 = rect
    lo, hi = np.minimum(A, B), np.maximum(A, B)
    ok = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1)
    d = B - A
    cs = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    cr = d[:, None, 0] * (cs[None, :, 1] - A[:, None, 1]) - d[:, None, 1] * (cs[None, :, 0] - A[:, None, 0])
    return ok & (cr.min(1) <= 0) & (cr.max(1) >= 0)       # the box's corners are not all on one side


def tris_hit_rect(P, rect):
    """Boolean mask: which screen triangles P (n, 3, 2) overlap rect (separating-axis test)."""
    x0, y0, x1, y1 = rect
    lo, hi = P.min(1), P.max(1)
    ok = (hi[:, 0] >= x0) & (lo[:, 0] <= x1) & (hi[:, 1] >= y0) & (lo[:, 1] <= y1)
    cs = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
    for i in range(3):
        a, b = P[:, i], P[:, (i + 1) % 3]
        e = b - a
        n = np.stack([-e[:, 1], e[:, 0]], 1)
        pt = (P * n[:, None, :]).sum(2)
        pc = (cs[None] * n[:, None, :]).sum(2)
        ok &= ~((pt.max(1) < pc.min(1)) | (pt.min(1) > pc.max(1)))
    return ok


def polyline_selected(S, rect, crossing) -> bool:
    """A screen polyline (k, 2): fully inside the box, or (crossing) touching it."""
    if len(S) == 0:
        return False
    inside = points_in_rect(S, rect)
    if not crossing:
        return bool(inside.all())
    if inside.any():
        return True
    if len(S) < 2:
        return False
    return bool(segs_hit_rect(S[:-1], S[1:], rect).any())


def mesh_part_selected(S, tris, rect, crossing, inside_v=None) -> bool:
    """A set of triangles (indices into the screen vertices S): fully inside the box, or (crossing) touching it."""
    if len(tris) == 0:
        return False
    if inside_v is None:
        inside_v = points_in_rect(S, rect)
    if not crossing:
        return bool(inside_v[tris].all())
    if inside_v[tris].any():
        return True
    return bool(tris_hit_rect(S[tris], rect).any())
