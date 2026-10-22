"""Orthographic orbit camera with smooth view transitions."""
from __future__ import annotations

import numpy as np


def _normalize(v):
    n = np.linalg.norm(v)
    return v / n if n > 1e-12 else v


def basis(back, up):
    back = _normalize(np.asarray(back, float))
    up = np.asarray(up, float)
    right = np.cross(up, back)
    if np.linalg.norm(right) < 1e-6:
        right = np.cross(np.array([0, 1.0, 0]), back)
        if np.linalg.norm(right) < 1e-6:
            right = np.array([1.0, 0, 0])
    right = _normalize(right)
    up = np.cross(back, right)
    return np.array([right, up, back])


def rot_axis(axis, ang):
    axis = _normalize(np.asarray(axis, float))
    x, y, z = axis
    c, s = np.cos(ang), np.sin(ang)
    C = 1 - c
    return np.array([[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
                     [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
                     [z * x * C - y * s, z * y * C + x * s, c + z * z * C]])


def mat_to_quat(m):
    t = np.trace(m)
    if t > 0:
        s = np.sqrt(t + 1.0) * 2
        return np.array([0.25 * s, (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s])
    i = int(np.argmax(np.diag(m)))
    if i == 0:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        return np.array([(m[2, 1] - m[1, 2]) / s, 0.25 * s, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s])
    if i == 1:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        return np.array([(m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, 0.25 * s, (m[1, 2] + m[2, 1]) / s])
    s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
    return np.array([(m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, 0.25 * s])


def quat_to_mat(q):
    w, x, y, z = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def slerp(q0, q1, t):
    d = float(np.dot(q0, q1))
    if d < 0:
        q1, d = -q1, -d
    if d > 0.9995:
        q = q0 + t * (q1 - q0)
        return q / np.linalg.norm(q)
    th = np.arccos(d)
    return (np.sin((1 - t) * th) * q0 + np.sin(t * th) * q1) / np.sin(th)


STANDARD_VIEWS = {
    # name: (back = direction from target towards the eye, up)
    "Front": ((0, -1, 0), (0, 0, 1)),
    "Back": ((0, 1, 0), (0, 0, 1)),
    "Top": ((0, 0, 1), (0, 1, 0)),
    "Bottom": ((0, 0, -1), (0, -1, 0)),
    "Right": ((1, 0, 0), (0, 0, 1)),
    "Left": ((-1, 0, 0), (0, 0, 1)),
    "Isometric": ((1, -1, 1), (0, 0, 1)),
}


class Camera:
    def __init__(self):
        self.target = np.zeros(3)
        self.R = basis(*STANDARD_VIEWS["Isometric"])   # rows: right, up, back (world -> view)
        self.scale = 0.25                              # world units per pixel
        self.w = 800
        self.h = 600
        self.depth = 1e5

    # --------------------------------------------------------------- transforms
    @property
    def right(self):
        return self.R[0]

    @property
    def up(self):
        return self.R[1]

    @property
    def back(self):
        return self.R[2]

    def to_view(self, p):
        return (np.asarray(p, float) - self.target) @ self.R.T

    def to_screen(self, p):
        """World point(s) -> (sx, sy, view_z). Works on (3,) or (n,3)."""
        v = self.to_view(p)
        sx = v[..., 0] / self.scale + self.w / 2
        sy = self.h / 2 - v[..., 1] / self.scale
        return np.stack([sx, sy, v[..., 2]], axis=-1)

    def ray(self, sx, sy):
        x = (sx - self.w / 2) * self.scale
        y = (self.h / 2 - sy) * self.scale
        origin = self.target + self.R[0] * x + self.R[1] * y + self.R[2] * self.depth * 0.5
        return origin, -self.R[2]

    def view_z_of_t(self, t):
        return self.depth * 0.5 - t

    def modelview(self):
        M = np.eye(4)
        M[:3, :3] = self.R
        M[:3, 3] = -self.R @ self.target
        return M

    # --------------------------------------------------------------- manipulation
    def orbit(self, dx, dy, pivot=None):
        """Rotate by a mouse delta (pixels) about screen axes through `pivot`."""
        pivot = self.target if pivot is None else np.asarray(pivot, float)
        a1 = -dx * 0.008
        a2 = -dy * 0.008
        Rw = rot_axis(self.up, a1) @ rot_axis(self.right, a2)
        # world-space rotation of the camera frame
        self.R = self.R @ Rw.T
        # re-orthonormalise
        u, _, vt = np.linalg.svd(self.R)
        self.R = u @ vt
        self.target = pivot + Rw @ (self.target - pivot)

    def pan(self, dx, dy):
        self.target = self.target - self.R[0] * dx * self.scale + self.R[1] * dy * self.scale

    def zoom(self, factor, sx=None, sy=None):
        if sx is None:
            self.scale *= factor
            return
        x = (sx - self.w / 2) * self.scale
        y = (self.h / 2 - sy) * self.scale
        before = self.target + self.R[0] * x + self.R[1] * y
        self.scale = float(np.clip(self.scale * factor, 1e-5, 1e4))
        x2 = (sx - self.w / 2) * self.scale
        y2 = (self.h / 2 - sy) * self.scale
        after = self.target + self.R[0] * x2 + self.R[1] * y2
        self.target = self.target + (before - after)

    def fit_target(self, lo, hi, R=None):
        """(target, scale) that frames the box in view R."""
        R = self.R if R is None else R
        lo, hi = np.asarray(lo, float), np.asarray(hi, float)
        corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        c = (lo + hi) / 2
        v = (corners - c) @ R.T
        wext = max(np.ptp(v[:, 0]), 1e-3)
        hext = max(np.ptp(v[:, 1]), 1e-3)
        mid = (v[:, 0].max() + v[:, 0].min()) / 2 * R[0] + (v[:, 1].max() + v[:, 1].min()) / 2 * R[1]
        scale = max(wext / max(self.w - 120, 50), hext / max(self.h - 120, 50))
        return c + mid, scale

    def state(self):
        return (self.target.copy(), self.R.copy(), self.scale)

    def set_state(self, s):
        self.target, self.R, self.scale = s[0].copy(), s[1].copy(), s[2]

    @staticmethod
    def interpolate(s0, s1, t):
        t = t * t * (3 - 2 * t)
        q = slerp(mat_to_quat(s0[1]), mat_to_quat(s1[1]), t)
        R = quat_to_mat(q)
        target = s0[0] * (1 - t) + s1[0] * t
        scale = float(np.exp(np.log(s0[2]) * (1 - t) + np.log(s1[2]) * t))
        return (target, R, scale)
