"""OpenGL 3D viewport: rendering, picking, navigation, view cube."""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

import numpy as np
from OpenGL import GL
from PySide6.QtCore import Qt, Signal, QTimer, QPointF, QRectF, QPoint
from PySide6.QtGui import QPainter, QColor, QFont, QPen, QPolygonF, QSurfaceFormat, QCursor
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from .. import kernel as K
from . import theme
from .camera import Camera, STANDARD_VIEWS, basis

PLANE_HALF = 75.0          # the default planes are 150 x 150 mm


@dataclass(frozen=True)
class Pick:
    kind: str           # face | edge | region | plane | sketch_line | part | sketch_point
    key: tuple          # identifying tuple, e.g. (part_id, face_index)

    def label(self):
        names = {"face": "Face", "edge": "Edge", "region": "Sketch region", "plane": "Plane",
                 "sketch_line": "Sketch line", "part": "Part", "sketch_point": "Sketch point"}
        return names.get(self.kind, self.kind)


def _ray_tris(o, d, V, T):
    """Möller–Trumbore for many triangles. Returns (t, tri_index) of the nearest hit or (inf, -1)."""
    if len(T) == 0:
        return np.inf, -1
    v0 = V[T[:, 0]].astype(np.float64)
    e1 = V[T[:, 1]] - v0
    e2 = V[T[:, 2]] - v0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - v0
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 0)
    if not hit.any():
        return np.inf, -1
    idx = np.where(hit)[0]
    k = idx[np.argmin(t[idx])]
    return float(t[k]), int(k)


def _ray_tris_kept(o, d, V, T, kept):
    """Nearest ray hit whose point survives the section cut."""
    if len(T) == 0:
        return np.inf, -1
    v0 = V[T[:, 0]].astype(np.float64)
    e1 = V[T[:, 1]] - v0
    e2 = V[T[:, 2]] - v0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-12
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    s = o - v0
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ d) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hit = ok & (u >= -1e-9) & (v >= -1e-9) & (u + v <= 1 + 1e-9) & (t > 0)
    idx = np.where(hit)[0]
    if not len(idx):
        return np.inf, -1
    pts = o + np.outer(t[idx], d)
    good = idx[kept(pts)]
    if not len(good):
        return np.inf, -1
    k = good[np.argmin(t[good])]
    return float(t[k]), int(k)


def _seg_dist(px, py, A, B):
    """Distance from a screen point to many screen segments (n,2). Returns (dist, param)."""
    ab = B - A
    L2 = np.einsum("ij,ij->i", ab, ab)
    L2[L2 == 0] = 1e-12
    t = np.clip(((px - A[:, 0]) * ab[:, 0] + (py - A[:, 1]) * ab[:, 1]) / L2, 0, 1)
    cx = A[:, 0] + t * ab[:, 0]
    cy = A[:, 1] + t * ab[:, 1]
    return np.hypot(cx - px, cy - py), t


class Viewport(QOpenGLWidget):
    selectionChanged = Signal()
    hoverChanged = Signal(object)
    contextRequested = Signal(object, QPoint)       # Pick or None, global pos
    doubleClicked = Signal(object)
    pickFilterRejected = Signal(str)

    def __init__(self, parent=None):
        fmt = QSurfaceFormat()
        fmt.setSamples(4)
        fmt.setDepthBufferSize(24)
        super().__init__(parent)
        self.setFormat(fmt)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.cam = Camera()
        self.parts = []          # list of (pid, mesh, color)
        self.part_shapes = {}    # pid -> shape
        self.planes = []         # list of (key_tuple, Plane, label, half_size)
        self.sketches = []       # list of (fid, SketchResult, region_meshes)
        self.hidden_sketch_regions = False
        self.preview_tool = None  # mesh of a translucent tool body
        self.overlays = []        # [((field_key, index), ("mesh", Mesh) | ("poly", ndarray))] dialog selections
        self.face_colors = {}     # part id -> {face index: (r, g, b)}
        self.section = None       # K.Plane when the section view is on (material on the -normal side is kept)
        self._section_cache_key = None
        self._section_meshes = {}
        self.measure_line = None  # (p1, p2) world points of a measured distance
        # drag arrows: dicts {origin, dir, value, on_drag(value), on_done()} — like Onshape's manipulators
        self.manipulators = []
        self._manip_drag = None
        self._manip_hover = None
        self.selection: list[Pick] = []
        self.hover: Pick | None = None
        self.select_filter: set | None = None     # None = default selectable kinds
        self.sketcher = None                        # active sketch controller
        self.pick_handler = None                    # callable(Pick) while a feature dialog is open
        self._mesh_cache = {}
        self._placed_cache = {}
        self.progress_cb = None   # callable(i, n, text) while a long import fills the scene
        self.feature_hl = None    # {"faces": {pid: {i}}, "edges": {pid: {i}}, "polys": [pts]} from the feature list
        self._batch = None
        self._batch_version = 0
        self._gpu_version = -1
        self._vbos = {}
        self._use_vbo = True
        self._prog = None         # shiny shader (False if unavailable)
        self.shiny = True
        self._part_boxes = {}     # pid -> (lo, hi) of its mesh, to skip far-away parts when picking
        self._part_segs = {}      # pid -> (A, B, edge index) segment arrays for edge picking
        self.triads = []          # mate connectors: dicts {o, x, y, z, color, size_px}
        self.ghosts = []          # translucent highlight meshes: [(Mesh, (r, g, b, a))]
        self.drag_handler = None  # assemblies: object with press(x, y, e) -> bool, move(x, y, e), release(x, y, e)
        self._part_drag = False
        self.orbit_mode = "cursor"     # what right-drag rotates about: cursor | centre | origin
        self._thumb = False            # drawing a clean picture for the home page
        self._orbiting = False
        self.last_click = None
        self._press = None
        self._last = None
        self._moved = False
        self._pivot = None
        self._anim = None
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._step_anim)
        self._zoom_left = 0.0
        self._zoom_at = (0.0, 0.0)
        self._zoom_timer = QTimer(self)
        self._zoom_timer.timeout.connect(self._step_zoom)
        self._segs = None
        self.show_planes = True
        from .viewcube import ViewCube
        self.cube = ViewCube()
        self.status_text = ""

    # ================================================================== scene
    def mesh_for(self, shape, kind="part"):
        # The cache keeps a reference to the shape so its memory (and hash) can't be reused by a new shape.
        key = (kind, hash(shape))
        hit = self._mesh_cache.get(key)
        if hit is not None and hit[0].IsSame(shape) and hit[0].IsEqual(shape):
            return hit[1]
        m = K.tessellate(shape)
        if len(self._mesh_cache) > 600:
            self._mesh_cache.clear()
        self._mesh_cache[key] = (shape, m)
        return m

    def placed_mesh(self, local_shape, M):
        """Mesh of a part placed by a 4x4 transform (assemblies): the part is tessellated once in its own
        coordinates and only the vertices move, so dragging stays fast."""
        m = self.mesh_for(local_shape)
        M = np.asarray(M, float)
        if np.allclose(M, np.eye(4), atol=1e-12):
            return m
        key = (id(m), M.tobytes())
        hit = self._placed_cache.get(key)
        if hit is not None and hit[0] is m:
            return hit[1]
        R, t = M[:3, :3], M[:3, 3]
        out = K.Mesh((m.verts @ R.T + t).astype(np.float32), (m.normals @ R.T).astype(np.float32), m.tris,
                     m.tri_face, m.face_ranges, [(np.asarray(e) @ R.T + t) for e in m.edge_polys])
        if len(self._placed_cache) > 400:
            self._placed_cache.clear()
        self._placed_cache[key] = (m, out)
        return out

    def set_scene(self, parts, planes, sketches):
        """parts: [(pid, shape, color, face_colors, transform, local_shape)] (the last three optional);
        planes: [(key, Plane, label)]; sketches: [(fid, SketchResult)]."""
        parts = [tuple(x) + (None,) * (6 - len(x)) for x in parts]
        cb = self.progress_cb if len(parts) > 15 else None
        built = []
        for i, (pid, shape, color, _fc, M, local) in enumerate(parts):
            if cb is not None:
                cb(i, len(parts), "Preparing the 3D view")
            built.append((pid, self.mesh_for(shape) if M is None else self.placed_mesh(local, M), color))
        self.parts = built
        self.part_shapes = {pid: shape for pid, shape, *_ in parts}
        self.face_colors = {pid: fc for pid, _s, _c, fc, *_ in parts if fc}
        self._build_batch(parts)
        self._part_boxes = {pid: (m.verts.min(0).astype(np.float64), m.verts.max(0).astype(np.float64))
                            for pid, m, _ in self.parts if len(m.verts)}
        self._part_segs = {}
        self._section_cache_key = None
        self.planes = planes
        self.sketches = [(fid, sr, [self.mesh_for(f, "region") for f in sr.regions]) for fid, sr in sketches]
        self._segs = None
        valid = []
        for p in self.selection:
            if self._pick_exists(p):
                valid.append(p)
        if len(valid) != len(self.selection):
            self.selection = valid
            self.selectionChanged.emit()
        if self.hover and not self._pick_exists(self.hover):
            self.hover = None
        self.update()

    def _pick_exists(self, p: Pick):
        if p.kind in ("face", "edge", "part"):
            return any(pid == p.key[0] for pid, _, _ in self.parts)
        if p.kind == "region":
            return any(fid == p.key[0] and p.key[1] < len(sr.regions) for fid, sr, _ in self.sketches)
        if p.kind == "sketch_line":
            return any(fid == p.key[0] and p.key[1] in sr.sketch.entities for fid, sr, _ in self.sketches)
        if p.kind == "plane":
            return any(k == p.key for k, *_ in self.planes)
        return True

    def model_bounds(self):
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for _, m, _ in self.parts:
            if len(m.verts):
                lo = np.minimum(lo, m.verts.min(0))
                hi = np.maximum(hi, m.verts.max(0))
        for _, sr, meshes in self.sketches:
            for m in meshes:
                if len(m.verts):
                    lo = np.minimum(lo, m.verts.min(0))
                    hi = np.maximum(hi, m.verts.max(0))
            for eid in sr.sketch.entities:
                pts = sr.sketch.polyline(eid)
                for u, v in pts:
                    w = sr.plane.to_world(u, v)
                    lo, hi = np.minimum(lo, w), np.maximum(hi, w)
        if self.sketcher is not None:
            sk = self.sketcher
            # only real geometry counts (the lone origin point has no size)
            pts = [sk.plane.to_world(*c["pos"]) for c in sk.sk.constraints if c.get("pos")]
            for eid in sk.sk.entities:
                pts += [sk.plane.to_world(u, v) for u, v in sk.sk.polyline(eid, 16)]
            for w in pts:
                lo, hi = np.minimum(lo, w), np.maximum(hi, w)
        if not np.all(np.isfinite(lo)):
            # nothing drawn yet: a sensible starting view about 80 mm across
            return np.array([-PLANE_HALF] * 3), np.array([PLANE_HALF] * 3)
        # never zoom in tighter than a few millimetres (e.g. a single point)
        centre, half = (lo + hi) / 2, np.maximum((hi - lo) / 2, 0.0)
        if half.max() < 2.5:
            half = np.full(3, 2.5)
        return centre - half, centre + half

    def plane_half(self):
        """Half-size of the default planes: fixed at 75 mm, so they're 150 x 150 mm whatever the model size."""
        return PLANE_HALF

    # ================================================================== view control
    def animate_to(self, state, duration=0.35, pivot=None):
        """Animate the camera to `state`. With a pivot, that point stays put on screen while the view turns."""
        keep = None
        if pivot is not None:
            pivot = np.asarray(pivot, float)
            v = self.cam.R @ (pivot - self.cam.target)
            keep = (pivot, float(v[0]), float(v[1]))
        self._anim = (self.cam.state(), state, time.time(), duration, keep)
        self._anim_timer.start(15)

    def rotate_view_about(self, back, up, pivot, duration=0.4):
        """Turn the view to (back, up) about a point — the view cube's clicks and arrows use this."""
        R = basis(np.asarray(back, float), np.asarray(up, float))
        pivot = np.asarray(pivot, float)
        v = self.cam.R @ (pivot - self.cam.target)
        target = pivot - (v[0] * R[0] + v[1] * R[1])
        self.animate_to((target, R, self.cam.scale), duration, pivot=pivot)

    def _step_anim(self):
        if not self._anim:
            self._anim_timer.stop()
            return
        s0, s1, t0, dur, keep = self._anim
        t = min(1.0, (time.time() - t0) / dur)
        st = Camera.interpolate(s0, s1, t)
        if keep is not None:
            pv, vx, vy = keep
            Rt = st[1]
            st = (pv - (vx * Rt[0] + vy * Rt[1]), Rt, st[2])
        self.cam.set_state(st)
        if t >= 1:
            self._anim = None
            self._anim_timer.stop()
        self.update()

    def set_standard_view(self, name, fit=True, animate=True):
        back, up = STANDARD_VIEWS[name]
        self.set_view_dir(np.array(back, float), np.array(up, float), fit, animate)

    def set_view_dir(self, back, up, fit=True, animate=True):
        R = basis(back, up)
        if fit:
            lo, hi = self.model_bounds()
            target, scale = self.cam.fit_target(lo, hi, R)
        else:
            target, scale = self.cam.target, self.cam.scale
        st = (target, R, scale)
        if animate:
            self.animate_to(st)
        else:
            self.cam.set_state(st)
            self.update()

    def normal_to(self, plane: K.Plane, animate=True):
        R = basis(plane.normal, plane.ydir)
        lo, hi = self.model_bounds()
        target, scale = self.cam.fit_target(lo, hi, R)
        if animate:
            self.animate_to((target, R, scale))
        else:
            self.cam.set_state((target, R, scale))
            self.update()

    def fit(self, animate=True):
        lo, hi = self.model_bounds()
        target, scale = self.cam.fit_target(lo, hi)
        if animate:
            self.animate_to((target, self.cam.R.copy(), scale))
        else:
            self.cam.target, self.cam.scale = target, scale
            self.update()

    # ================================================================== GL
    def initializeGL(self):
        GL.glClearColor(1, 1, 1, 1)

    def resizeGL(self, w, h):
        self.cam.w, self.cam.h = max(w, 1), max(h, 1)

    def paintGL(self):
        dpr = self.devicePixelRatioF()
        W, H = int(self.width() * dpr), int(self.height() * dpr)
        self.cam.w, self.cam.h = self.width(), self.height()
        GL.glViewport(0, 0, W, H)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self._draw_background()
        self._setup_camera()
        self._draw_scene()
        if self._thumb:
            self._reset_gl()
            return
        self._reset_gl()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        self._draw_overlay(p)
        p.end()

    def _reset_gl(self):
        GL.glDisable(GL.GL_CLIP_PLANE0)
        GL.glBlendFunc(GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDisable(GL.GL_LINE_SMOOTH)
        GL.glDisable(GL.GL_LINE_STIPPLE)
        GL.glDisable(GL.GL_COLOR_MATERIAL)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glLineWidth(1.0)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDepthRange(0, 1)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()

    def _draw_background(self):
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDisable(GL.GL_LIGHTING)
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        GL.glBegin(GL.GL_QUADS)
        GL.glColor3f(*theme.BG_BOTTOM)
        GL.glVertex2f(-1, -1)
        GL.glVertex2f(1, -1)
        GL.glColor3f(*theme.BG_TOP)
        GL.glVertex2f(1, 1)
        GL.glVertex2f(-1, 1)
        GL.glEnd()

    def _setup_camera(self):
        c = self.cam
        hw, hh = c.w * c.scale / 2, c.h * c.scale / 2
        GL.glMatrixMode(GL.GL_PROJECTION)
        GL.glLoadIdentity()
        GL.glOrtho(-hw, hw, -hh, hh, -c.depth / 2, c.depth / 2)
        GL.glMatrixMode(GL.GL_MODELVIEW)
        GL.glLoadIdentity()
        # lights in eye space
        GL.glEnable(GL.GL_LIGHT0)
        GL.glEnable(GL.GL_LIGHT1)
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_POSITION, (0.35, 0.6, 1.0, 0.0))
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_DIFFUSE, (0.55, 0.55, 0.55, 1))
        GL.glLightfv(GL.GL_LIGHT0, GL.GL_SPECULAR, (0.25, 0.25, 0.25, 1))
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_POSITION, (-0.7, -0.2, 0.6, 0.0))
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_DIFFUSE, (0.30, 0.30, 0.32, 1))
        GL.glLightfv(GL.GL_LIGHT1, GL.GL_SPECULAR, (0, 0, 0, 1))
        GL.glLightModelfv(GL.GL_LIGHT_MODEL_AMBIENT, (0.45, 0.45, 0.47, 1))
        GL.glLightModeli(GL.GL_LIGHT_MODEL_TWO_SIDE, GL.GL_TRUE)
        GL.glLoadMatrixd(c.modelview().T.flatten())

    # ------------------------------------------------------------------ shiny part shading (per-pixel)
    _VERT = """
    #version 120
    varying vec3 N;
    varying vec4 C;
    void main() {
        vec4 ev = gl_ModelViewMatrix * gl_Vertex;
        N = gl_NormalMatrix * gl_Normal;
        C = gl_Color;
        gl_ClipVertex = ev;
        gl_Position = gl_ProjectionMatrix * ev;
    }
    """
    _FRAG = """
    #version 120
    uniform float shine;
    varying vec3 N;
    varying vec4 C;
    void main() {
        vec3 n = normalize(N);
        vec3 v = vec3(0.0, 0.0, 1.0);
        if (dot(n, v) < 0.0) n = -n;                    // always light the side we're looking at
        vec3 L1 = normalize(vec3(0.55, 0.12, 1.0));     // key light, near the viewer, from the right
        vec3 L2 = normalize(vec3(-0.6, 0.8, 0.3));      // soft top light
        float d1 = max(dot(n, L1), 0.0);
        float d2 = max(dot(n, L2), 0.0);
        vec3 base = C.rgb;
        vec3 col = base * (0.50 + 0.52 * d1 + 0.06 * d2);
        float s1 = pow(max(dot(n, normalize(L1 + v)), 0.0), 18.0);
        float s2 = pow(max(dot(n, normalize(L2 + v)), 0.0), 18.0);
        float rim = pow(1.0 - max(dot(n, v), 0.0), 4.0);
        col += shine * (0.36 * s1 + 0.08 * s2) * vec3(0.90, 0.96, 1.0) + shine * 0.05 * rim;
        gl_FragColor = vec4(min(col, vec3(1.0)), C.a);
    }
    """

    def _shader(self):
        """The per-pixel shiny shader (built once). None if this graphics driver can't do it."""
        if self._prog is False:
            return None
        if self._prog is None:
            try:
                from OpenGL.GL import shaders
                self._prog = shaders.compileProgram(shaders.compileShader(self._VERT, GL.GL_VERTEX_SHADER),
                                                    shaders.compileShader(self._FRAG, GL.GL_FRAGMENT_SHADER),
                                                    validate=False)
                self._u_shine = GL.glGetUniformLocation(self._prog, "shine")
            except Exception:
                self._prog = False
                return None
        return self._prog

    def _use_shiny(self, on):
        prog = self._shader() if on and self.shiny else None
        try:
            if prog:
                GL.glUseProgram(prog)
                GL.glUniform1f(self._u_shine, 1.0)
            else:
                GL.glUseProgram(0)
        except Exception:
            self._prog = False

    # ------------------------------------------------------------------ batched scene geometry
    def _build_batch(self, parts_full):
        """Concatenate every part's mesh into single vertex / normal / colour / index arrays (colours,
        including per-face STEP colours, baked in) plus one array of edge line segments."""
        V, N, C, T, E = [], [], [], [], []
        ranges = {}
        nv = nt = 0
        fcols = self.face_colors
        for pid, m, color in self.parts:
            if len(m.verts) == 0:
                continue
            col = np.empty((len(m.verts), 3), np.float32)
            col[:] = color[:3]
            fc = fcols.get(pid)
            if fc:
                for fi, c in fc.items():
                    if fi < len(m.face_ranges):
                        a, b = m.face_ranges[fi]
                        if b > a:
                            col[m.tris[a:b].ravel()] = c[:3]
            V.append(m.verts)
            N.append(m.normals)
            C.append(col)
            T.append(m.tris.astype(np.uint32) + nv)
            ranges[pid] = (nt, m, color)
            nv += len(m.verts)
            nt += len(m.tris)
            for poly in m.edge_polys:
                poly = np.asarray(poly, np.float32)
                if len(poly) >= 2:
                    seg = np.empty((2 * (len(poly) - 1), 3), np.float32)
                    seg[0::2] = poly[:-1]
                    seg[1::2] = poly[1:]
                    E.append(seg)
        cat = lambda a, shape: np.ascontiguousarray(np.vstack(a), np.float32) if a else np.zeros(shape, np.float32)
        self._batch = {"V": cat(V, (0, 3)), "N": cat(N, (0, 3)), "C": cat(C, (0, 3)),
                       "T": np.ascontiguousarray(np.vstack(T), np.uint32) if T else np.zeros((0, 3), np.uint32),
                       "E": cat(E, (0, 3)), "ranges": ranges, "ntri": nt, "nedge": sum(len(e) for e in E)}
        self._batch_version += 1

    def _upload_batch(self):
        """Put the batch in GPU buffers (once per scene change). Falls back to plain arrays if buffers fail."""
        if self._gpu_version == self._batch_version:
            return
        self._gpu_version = self._batch_version
        if self._vbos:
            try:
                GL.glDeleteBuffers(len(self._vbos), list(self._vbos.values()))
            except Exception:
                pass
            self._vbos = {}
        if not self._use_vbo or self._batch is None:
            return
        try:
            b = self._batch
            for key, target in (("V", GL.GL_ARRAY_BUFFER), ("N", GL.GL_ARRAY_BUFFER), ("C", GL.GL_ARRAY_BUFFER),
                                ("E", GL.GL_ARRAY_BUFFER), ("T", GL.GL_ELEMENT_ARRAY_BUFFER)):
                buf = int(GL.glGenBuffers(1))
                GL.glBindBuffer(target, buf)
                GL.glBufferData(target, b[key].nbytes, b[key] if b[key].size else None, GL.GL_STATIC_DRAW)
                self._vbos[key] = buf
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        except Exception:
            self._use_vbo = False
            self._vbos = {}

    def _bind_batch(self, colors=True):
        self._upload_batch()
        b = self._batch
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        if colors:
            GL.glEnableClientState(GL.GL_COLOR_ARRAY)
        if self._vbos:
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbos["V"])
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbos["N"])
            GL.glNormalPointer(GL.GL_FLOAT, 0, None)
            if colors:
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbos["C"])
                GL.glColorPointer(3, GL.GL_FLOAT, 0, None)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._vbos["T"])
        else:
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, b["V"])
            GL.glNormalPointer(GL.GL_FLOAT, 0, b["N"])
            if colors:
                GL.glColorPointer(3, GL.GL_FLOAT, 0, b["C"])

    def _idx_ptr(self, tri_start):
        """Index-data pointer for drawing from triangle `tri_start` (an offset into the GPU buffer, or a
        slice of the array when buffers aren't available)."""
        if self._vbos:
            import ctypes
            return ctypes.c_void_p(int(tri_start) * 12)
        return self._batch["T"][tri_start:]

    def _unbind_batch(self):
        if self._vbos:
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        GL.glDisableClientState(GL.GL_COLOR_ARRAY)
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

    def _draw_edge_batch(self):
        self._upload_batch()
        b = self._batch
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        if self._vbos:
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbos["E"])
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, None)
            GL.glDrawArrays(GL.GL_LINES, 0, b["nedge"])
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        else:
            GL.glVertexPointer(3, GL.GL_FLOAT, 0, b["E"])
            GL.glDrawArrays(GL.GL_LINES, 0, b["nedge"])
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

    def _draw_mesh(self, m, tris=None):
        if len(m.verts) == 0:
            return
        T = m.tris if tris is None else tris
        if len(T) == 0:
            return
        GL.glEnableClientState(GL.GL_VERTEX_ARRAY)
        GL.glEnableClientState(GL.GL_NORMAL_ARRAY)
        GL.glVertexPointer(3, GL.GL_FLOAT, 0, m.verts)
        GL.glNormalPointer(GL.GL_FLOAT, 0, m.normals)
        GL.glDrawElements(GL.GL_TRIANGLES, T.size, GL.GL_UNSIGNED_INT, np.ascontiguousarray(T))
        GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
        GL.glDisableClientState(GL.GL_VERTEX_ARRAY)

    def _draw_polyline(self, pts, closed=False):
        if len(pts) < 2:
            return
        GL.glBegin(GL.GL_LINE_LOOP if closed else GL.GL_LINE_STRIP)
        for p in pts:
            GL.glVertex3f(*map(float, p))
        GL.glEnd()

    def _sel_color(self, pick):
        if pick in self.selection:
            return theme.SELECT
        if pick == self.hover:
            return theme.HOVER
        return None

    def _draw_scene(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LEQUAL)
        GL.glEnable(GL.GL_MULTISAMPLE)
        GL.glEnable(GL.GL_LINE_SMOOTH)
        GL.glHint(GL.GL_LINE_SMOOTH_HINT, GL.GL_NICEST)
        GL.glEnable(GL.GL_BLEND)
        # keep destination alpha at 1 so the widget compositor doesn't see a translucent framebuffer
        GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA, GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)

        # ---- section view: clip away the material in front of the plane
        if self.section is not None:
            n = np.asarray(self.section.normal, float)
            o = np.asarray(self.section.origin, float)
            GL.glClipPlane(GL.GL_CLIP_PLANE0, (-n[0], -n[1], -n[2], float(n @ o)))
            GL.glEnable(GL.GL_CLIP_PLANE0)

        # ---- solid parts: the whole scene is one batch on the graphics card (fast even with hundreds of parts)
        GL.glEnable(GL.GL_LIGHTING)
        GL.glEnable(GL.GL_COLOR_MATERIAL)
        GL.glColorMaterial(GL.GL_FRONT_AND_BACK, GL.GL_AMBIENT_AND_DIFFUSE)
        GL.glMaterialfv(GL.GL_FRONT_AND_BACK, GL.GL_SPECULAR, (0.35, 0.35, 0.35, 1))
        GL.glMaterialf(GL.GL_FRONT_AND_BACK, GL.GL_SHININESS, 40)
        GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glPolygonOffset(1.0, 1.0)
        b = self._batch
        if b is not None and b["ntri"]:
            self._use_shiny(True)
            self._bind_batch(colors=True)
            GL.glDrawElements(GL.GL_TRIANGLES, b["ntri"] * 3, GL.GL_UNSIGNED_INT, self._idx_ptr(0))
            GL.glDisableClientState(GL.GL_COLOR_ARRAY)
            # selected / hovered parts and faces drawn again on top in their highlight colour
            GL.glPolygonOffset(0.5, 0.5)
            picks = list(self.selection) + ([self.hover] if self.hover is not None else [])
            for pk in picks:
                if pk.kind not in ("part", "face"):
                    continue
                rng = b["ranges"].get(pk.key[0])
                if rng is None:
                    continue
                base, m, color = rng
                if pk.kind == "part":
                    a_, b_ = 0, len(m.tris)
                    col = theme.SELECT if pk in self.selection else \
                        tuple(0.6 * x + 0.4 * y for x, y in zip(color, theme.HOVER))
                else:
                    if pk.key[1] >= len(m.face_ranges):
                        continue
                    a_, b_ = m.face_ranges[pk.key[1]]
                    col = self._sel_color(pk)
                if b_ > a_ and col:
                    GL.glColor4f(*col, 1.0)
                    GL.glDrawElements(GL.GL_TRIANGLES, (b_ - a_) * 3, GL.GL_UNSIGNED_INT, self._idx_ptr(base + a_))
            self._unbind_batch()
            self._use_shiny(False)
        GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glDisable(GL.GL_LIGHTING)

        # ---- edges (one draw call), then highlighted ones
        GL.glLineWidth(1.4)
        GL.glColor3f(*theme.EDGE)
        if b is not None and b["nedge"]:
            self._draw_edge_batch()
        hl = [pk for pk in list(self.selection) + ([self.hover] if self.hover is not None else []) if pk.kind == "edge"]
        if hl:
            meshes = {pid: m for pid, m, _ in self.parts}
            for pk in hl:
                m = meshes.get(pk.key[0])
                col = self._sel_color(pk)
                if m is None or not col or pk.key[1] >= len(m.edge_polys):
                    continue
                GL.glLineWidth(4.0)
                GL.glColor3f(*col)
                GL.glDepthRange(0, 0.99999)
                self._draw_polyline(m.edge_polys[pk.key[1]])
                GL.glDepthRange(0, 1)
        GL.glLineWidth(1.4)

        # ---- the feature picked in the feature list: its faces tinted, their edges and sketch curves outlined
        fh = self.feature_hl
        if fh:
            col = theme.HOVER
            b = self._batch
            if fh.get("faces") and b is not None and b["ntri"]:
                GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
                GL.glPolygonOffset(0.3, 0.3)
                GL.glDepthMask(GL.GL_FALSE)
                self._bind_batch(colors=False)
                GL.glDisableClientState(GL.GL_NORMAL_ARRAY)
                GL.glColor4f(*col, 0.35)
                for pid, fis in fh["faces"].items():
                    rng = b["ranges"].get(pid)
                    if rng is None:
                        continue
                    base, m, _c = rng
                    for fi in fis:
                        if fi < len(m.face_ranges):
                            a_, b_ = m.face_ranges[fi]
                            if b_ > a_:
                                GL.glDrawElements(GL.GL_TRIANGLES, (b_ - a_) * 3, GL.GL_UNSIGNED_INT,
                                                  self._idx_ptr(base + a_))
                self._unbind_batch()
                GL.glDepthMask(GL.GL_TRUE)
                GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            meshes = {pid: m for pid, m, _ in self.parts}
            GL.glLineWidth(3.0)
            GL.glColor3f(*col)
            GL.glDepthRange(0, 0.99999)
            for pid, eis in (fh.get("edges") or {}).items():
                m = meshes.get(pid)
                if m is None:
                    continue
                for ei in eis:
                    if ei < len(m.edge_polys):
                        self._draw_polyline(m.edge_polys[ei])
            GL.glDepthRange(0, 1)
            if fh.get("polys"):
                GL.glDisable(GL.GL_DEPTH_TEST)
                GL.glLineWidth(2.6)
                for poly in fh["polys"]:
                    self._draw_polyline(poly)
                GL.glEnable(GL.GL_DEPTH_TEST)
            GL.glLineWidth(1.4)

        # ---- section caps (solid faces where the plane cuts)
        if self.section is not None:
            GL.glDisable(GL.GL_CLIP_PLANE0)
            caps = self._section_cap_meshes()
            GL.glEnable(GL.GL_LIGHTING)
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glPolygonOffset(-1.0, -1.0)
            for pid, meshes in caps.items():
                base = next((c for p_, _m, c in self.parts if p_ == pid), (0.7, 0.7, 0.7))
                col = tuple(0.55 * x + 0.45 * y for x, y in zip(base, (0.95, 0.55, 0.20)))
                GL.glColor4f(*col, 1.0)
                for m in meshes:
                    self._draw_mesh(m)
            GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glDisable(GL.GL_LIGHTING)
            GL.glLineWidth(1.6)
            GL.glColor3f(*theme.EDGE)
            for meshes in caps.values():
                for m in meshes:
                    for poly in m.edge_polys:
                        self._draw_polyline(poly)
            GL.glLineWidth(1.4)

        # ---- sketches (not being edited)
        GL.glDepthRange(0, 0.9995)
        for fid, sr, meshes in self.sketches:
            if self.sketcher and self.sketcher.fid == fid:
                continue
            sk = sr.sketch
            for eid, e in sk.entities.items():
                pk = Pick("sketch_line", (fid, eid))
                col = self._sel_color(pk) or (theme.SKETCH_CONSTRUCTION if e.get("construction") else theme.SKETCH_REMOTE)
                if e.get("construction"):
                    GL.glEnable(GL.GL_LINE_STIPPLE)
                    GL.glLineStipple(2, 0x0F0F)
                GL.glLineWidth(3.5 if col in (theme.SELECT, theme.HOVER) else 1.6)
                GL.glColor3f(*col)
                pts = sk.polyline(eid)
                if e["type"] == "point":
                    GL.glPointSize(6)
                    GL.glBegin(GL.GL_POINTS)
                    GL.glVertex3f(*sr.plane.to_world(*pts[0]))
                    GL.glEnd()
                else:
                    self._draw_polyline([sr.plane.to_world(u, v) for u, v in pts])
                GL.glDisable(GL.GL_LINE_STIPPLE)
        GL.glDepthRange(0, 1)

        # ---- dialog selections (drawn on top of the preview)
        if self.overlays:
            GL.glDepthRange(0, 0.998)
            for key, (kind, g) in self.overlays:
                hov = self.hover is not None and self.hover.kind == "overlay" and self.hover.key == key
                col = theme.HOVER if hov else theme.SELECT
                if kind == "mesh":
                    GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
                    GL.glPolygonOffset(-1.0, -1.0)
                    GL.glColor4f(*col, 0.85)
                    self._draw_mesh(g)
                    GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
                    GL.glLineWidth(2.0)
                    GL.glColor3f(*col)
                    for poly in g.edge_polys:
                        self._draw_polyline(poly)
                else:
                    GL.glLineWidth(4.0)
                    GL.glColor3f(*col)
                    self._draw_polyline(g)
            GL.glDepthRange(0, 1)
            GL.glLineWidth(1.4)

        # ---- translucent things last
        GL.glDepthMask(GL.GL_FALSE)
        for fid, sr, meshes in self.sketches:
            if self.sketcher and self.sketcher.fid == fid and not self.sketcher.show_regions:
                continue
            for ri, m in enumerate(meshes):
                pk = Pick("region", (fid, ri))
                col = self._sel_color(pk)
                if col:
                    GL.glColor4f(*col, 0.75)
                else:
                    GL.glColor4f(*theme.REGION, 0.42 if (self.sketcher and self.sketcher.fid == fid) else 0.32)
                GL.glDepthRange(0, 0.9993)
                self._draw_mesh(m)
                GL.glDepthRange(0, 1)
        if self.preview_tool is not None:
            GL.glColor4f(0.9, 0.25, 0.2, 0.25)
            self._draw_mesh(self.preview_tool)
        if self.show_planes and self.sketcher is None:
            for key, pl, label, half in self.planes:
                pk = Pick("plane", key)
                col = self._sel_color(pk)
                corners = [pl.to_world(u, v) for u, v in ((-half, -half), (half, -half), (half, half), (-half, half))]
                GL.glColor4f(*(col or theme.PLANE), 0.45 if col else 0.14)
                GL.glBegin(GL.GL_QUADS)
                for c in corners:
                    GL.glVertex3f(*c)
                GL.glEnd()
                GL.glColor4f(*(col or (0.35, 0.55, 0.85)), 0.9)
                GL.glLineWidth(2.5 if col else 1.0)
                self._draw_polyline(corners, closed=True)
        GL.glDepthMask(GL.GL_TRUE)

        # ---- measured distance
        if self.measure_line is not None and self.sketcher is None:
            a, b = (np.asarray(x, float) for x in self.measure_line)
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glEnable(GL.GL_LINE_STIPPLE)
            GL.glLineStipple(2, 0x00FF)
            GL.glLineWidth(2.2)
            GL.glColor3f(0.93, 0.45, 0.10)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex3f(*a)
            GL.glVertex3f(*b)
            GL.glEnd()
            GL.glDisable(GL.GL_LINE_STIPPLE)
            GL.glPointSize(8)
            GL.glBegin(GL.GL_POINTS)
            GL.glVertex3f(*a)
            GL.glVertex3f(*b)
            GL.glEnd()
            GL.glEnable(GL.GL_DEPTH_TEST)

        # ---- highlight meshes (e.g. interference) and mate connectors, on top
        if self.ghosts:
            GL.glDisable(GL.GL_DEPTH_TEST)
            GL.glDepthMask(GL.GL_FALSE)
            for m, rgba in self.ghosts:
                GL.glColor4f(*rgba)
                self._draw_mesh(m)
                GL.glLineWidth(2.0)
                GL.glColor4f(rgba[0] * 0.7, rgba[1] * 0.7, rgba[2] * 0.7, 1.0)
                for poly in m.edge_polys:
                    self._draw_polyline(poly)
            GL.glDepthMask(GL.GL_TRUE)
            GL.glEnable(GL.GL_DEPTH_TEST)
        if self.triads:
            GL.glDisable(GL.GL_DEPTH_TEST)
            for tr in self.triads:
                L_ = self.cam.scale * tr.get("size_px", 34)
                o = np.asarray(tr["o"], float)
                GL.glLineWidth(tr.get("width", 2.6))
                for key, col in (("x", (0.85, 0.2, 0.2)), ("y", (0.2, 0.65, 0.25)), ("z", (0.2, 0.4, 0.95))):
                    if tr.get("color"):
                        col = tuple(0.5 * a + 0.5 * b for a, b in zip(col, tr["color"]))
                    GL.glColor3f(*col)
                    GL.glBegin(GL.GL_LINES)
                    GL.glVertex3f(*o)
                    GL.glVertex3f(*(o + np.asarray(tr[key], float) * L_ * (1.25 if key == "z" else 0.8)))
                    GL.glEnd()
                GL.glPointSize(9)
                GL.glColor3f(*(tr.get("color") or (0.98, 0.62, 0.15)))
                GL.glBegin(GL.GL_POINTS)
                GL.glVertex3f(*o)
                GL.glEnd()
            GL.glEnable(GL.GL_DEPTH_TEST)

        # ---- origin triad
        if self._thumb:
            return
        GL.glDisable(GL.GL_DEPTH_TEST)
        L = self.cam.scale * 28
        GL.glLineWidth(2)
        for d, col in (((1, 0, 0), (0.85, 0.2, 0.2)), ((0, 1, 0), (0.2, 0.65, 0.25)), ((0, 0, 1), (0.2, 0.4, 0.9))):
            GL.glColor3f(*col)
            GL.glBegin(GL.GL_LINES)
            GL.glVertex3f(0, 0, 0)
            GL.glVertex3f(*(np.array(d) * L))
            GL.glEnd()
        GL.glPointSize(5)
        GL.glColor3f(0.25, 0.27, 0.3)
        GL.glBegin(GL.GL_POINTS)
        GL.glVertex3f(0, 0, 0)
        GL.glEnd()

        # ---- active sketch
        if self.sketcher:
            self.sketcher.draw_gl(self)
        GL.glLineWidth(1)

    # ------------------------------------------------------------------ view cube (see viewcube.py)
    def _cube_pick(self, x, y):
        return self.cube.pick(self.cam, self.width(), x, y)

    def _cube_click(self, key):
        if not key:
            return
        if key == "menu":
            from PySide6.QtWidgets import QMenu
            m = QMenu(self)
            for name in ("Isometric", "Front", "Back", "Left", "Right", "Top", "Bottom"):
                m.addAction(name, lambda n=name: self.set_standard_view(n))
            m.addSeparator()
            m.addAction("Zoom to fit", lambda: self.fit())
            r = self.cube.menu_rect(self.width())
            m.exec(self.mapToGlobal(QPoint(int(r.left()), int(r.bottom()) + 2)))
            return
        tv = self.cube.target_view(self.cam, key)
        if tv is not None:
            # turn about the origin / model centre (per the Rotate-around setting), not the middle of the screen
            pivot = np.zeros(3) if self.orbit_mode == "origin" else self.orbit_pivot(-1e6, -1e6)
            self.rotate_view_about(tv[0], tv[1], pivot)

    # ================================================================== overlay
    def _draw_overlay(self, p: QPainter):
        f = QFont()
        f.setPixelSize(11)
        p.setFont(f)
        # rotation centre while orbiting
        if self._orbiting and self._pivot is not None:
            sp = self.cam.to_screen(np.asarray(self._pivot, float))
            c = QPointF(float(sp[0]), float(sp[1]))
            p.setPen(QPen(QColor(255, 255, 255, 220), 4))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(c, 7, 7)
            p.setPen(QPen(QColor(31, 111, 209), 2))
            p.drawEllipse(c, 7, 7)
            p.setBrush(QColor(31, 111, 209))
            p.drawEllipse(c, 2.2, 2.2)
        # plane labels
        if self.show_planes and self.sketcher is None:
            for key, pl, label, half in self.planes:
                if not label:
                    continue
                corner = pl.to_world(-half, half)
                s = self.cam.to_screen(corner)
                col = QColor(theme.PLANE_LABEL)
                hidden_cache = self.__dict__.setdefault("_label_hidden", {})
                moving = self._orbiting or self._anim is not None
                if moving and label in hidden_cache:
                    behind = hidden_cache[label]             # the view is moving: skip the ray test this frame
                else:
                    hit = self.surface_point(s[0] + 4, s[1] + 10) if self.parts else None
                    behind = hit is not None and self.cam.to_view(hit)[2] > self.cam.to_view(corner)[2] + 1e-6
                    hidden_cache[label] = behind
                if behind:
                    col.setAlpha(55)            # the label is behind a part: just a hint of it
                p.setPen(col)
                p.drawText(QPointF(s[0] + 4, s[1] + 14), label)
        # view cube
        self.cube.hover = getattr(self, "_cube_hover", None)
        self.cube.draw(p, self.cam, self.width())
        f.setBold(False)
        p.setFont(f)
        if self.sketcher:
            self.sketcher.draw_overlay(self, p)
        self._draw_manipulators(p)
        if self.drag_handler is not None and hasattr(self.drag_handler, "draw_overlay") and not self._thumb_on():
            self.drag_handler.draw_overlay(p)
        self._draw_scale_bar(p)
        if self.status_text:
            f.setPixelSize(12)
            p.setFont(f)
            warn = self.status_text.startswith("⚠")
            if warn:
                f.setBold(True)
                p.setFont(f)
            p.setPen(QColor(theme.ERROR) if warn else QColor(theme.TEXT_MUTED))
            p.drawText(QRectF(12, self.height() - 26, self.width() - 24, 18), Qt.AlignLeft | Qt.AlignVCenter, self.status_text)

    # ------------------------------------------------------------------ section view
    def set_section(self, plane):
        self.section = plane
        self._section_cache_key = None
        self._segs = None
        self.update()

    def _section_cap_meshes(self):
        pl = self.section
        key = (tuple(np.round(pl.origin, 9)), tuple(np.round(pl.normal, 9)),
               tuple((pid, id(self.part_shapes.get(pid))) for pid, _m, _c in self.parts))
        if key != self._section_cache_key:
            out = {}
            for pid, _m, _c in self.parts:
                shape = self.part_shapes.get(pid)
                if shape is None:
                    continue
                fs = K.section_caps(shape, pl)
                if fs:
                    out[pid] = [K.tessellate(f) for f in fs]
            self._section_meshes = out
            self._section_cache_key = key
        return self._section_meshes

    def _kept(self, pts):
        """Boolean mask: which world points survive the section cut."""
        if self.section is None:
            return np.ones(len(pts), bool)
        return (np.asarray(pts) - self.section.origin) @ self.section.normal <= 1e-9

    # ------------------------------------------------------------------ drag arrows
    def _manip_screen(self, m):
        o = np.asarray(m["origin"], float)
        d = np.asarray(m["dir"], float)
        base = self.cam.to_screen(o)[:2]
        tip = self.cam.to_screen(o + d * m["value"])[:2]
        sd = self.cam.to_screen(o + d)[:2] - base
        n = np.linalg.norm(sd)
        sd = sd / n if n > 1e-9 else np.array([0.0, -1.0])
        if m["value"] < 0:
            sd = -sd
        return base, tip, sd

    def _draw_manipulators(self, p: QPainter):
        for i, m in enumerate(self.manipulators):
            base, tip, sd = self._manip_screen(m)
            active = self._manip_hover == i or (self._manip_drag and self._manip_drag["i"] == i)
            col = QColor(theme.HOVER[0] * 255, theme.HOVER[1] * 255, theme.HOVER[2] * 255) if active \
                else QColor(theme.ACCENT)
            p.setPen(QPen(col, 2.5, Qt.DashLine))
            p.drawLine(QPointF(*base), QPointF(*tip))
            # arrow head pointing along the drag direction
            q = np.array([-sd[1], sd[0]])
            head = [tip + sd * 22, tip + q * 9, tip - q * 9]
            p.setPen(QPen(col.darker(130), 1))
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(*h) for h in head]))
            p.setBrush(Qt.NoBrush)
            if m.get("label"):
                f = QFont()
                f.setPixelSize(12)
                f.setBold(True)
                p.setFont(f)
                txt = m["label"]
                r = QRectF(tip[0] + sd[0] * 30 - 34, tip[1] + sd[1] * 30 - 10, 68, 20)
                p.setPen(QPen(col, 1))
                p.setBrush(QColor(255, 255, 255, 235))
                p.drawRoundedRect(r, 3, 3)
                p.setPen(QColor(theme.TEXT))
                p.drawText(r, Qt.AlignCenter, txt)
                p.setBrush(Qt.NoBrush)

    def _manip_hit(self, x, y):
        for i, m in enumerate(self.manipulators):
            base, tip, sd = self._manip_screen(m)
            centre = tip + sd * 10
            if np.hypot(centre[0] - x, centre[1] - y) < 16:
                return i
        return None

    def _manip_param(self, m, x, y):
        """Distance along the arrow's line closest to the mouse ray."""
        o, d = self.cam.ray(x, y)
        P = np.asarray(m["origin"], float)
        D = np.asarray(m["dir"], float)
        w0 = P - o
        a, b, c = D @ D, D @ d, d @ d
        den = a * c - b * b
        if abs(den) < 1e-9:
            return None
        return (b * (d @ w0) - c * (D @ w0)) / den

    def _draw_scale_bar(self, p: QPainter):
        """A map-style scale bar so sizes on screen are easy to judge."""
        target_px = 120
        raw = target_px * self.cam.scale
        mag = 10 ** np.floor(np.log10(raw))
        length = mag
        for m in (1, 2, 5, 10):
            if m * mag <= raw:
                length = m * mag
        px = length / self.cam.scale
        x0 = 16
        y0 = self.height() - (46 if self.status_text else 22)
        dark = QColor(theme.OVERLAY_TEXT)
        p.setPen(QPen(dark, 2))
        p.drawLine(QPointF(x0, y0), QPointF(x0 + px, y0))
        p.drawLine(QPointF(x0, y0 - 5), QPointF(x0, y0 + 1))
        p.drawLine(QPointF(x0 + px, y0 - 5), QPointF(x0 + px, y0 + 1))
        p.drawLine(QPointF(x0 + px / 2, y0 - 3), QPointF(x0 + px / 2, y0))
        f = QFont()
        f.setPixelSize(11)
        f.setBold(True)
        p.setFont(f)
        label = (f"{length / 1000:g} m" if length >= 1000 else
                 f"{length:g} mm" if length >= 1 else f"{length * 1000:g} µm")
        p.drawText(QPointF(x0 + px + 8, y0 + 4), label)

    # ================================================================== picking
    def _allowed(self, kind):
        if self.select_filter is None:
            return kind in ("face", "edge", "region", "plane", "sketch_line")
        return kind in self.select_filter

    def _ray_parts(self, o, d, margin=0.0):
        """Parts whose bounding box the pick ray passes through (or within `margin` of) — so picking only
        tests the triangles of parts actually under the cursor."""
        # all parts' boxes as two arrays (rebuilt when the scene changes): one vectorised slab test
        key = (id(self.parts), len(self.parts), id(self._part_boxes))
        bx = getattr(self, "_box_arrays", None)
        if bx is None or bx[0] != key:
            items = [(pid, m) for pid, m, _ in self.parts if pid in self._part_boxes]
            LO = np.array([self._part_boxes[pid][0] for pid, _m in items], float).reshape(-1, 3)
            HI = np.array([self._part_boxes[pid][1] for pid, _m in items], float).reshape(-1, 3)
            bx = self._box_arrays = (key, items, LO, HI)
        _k, items, LO, HI = bx
        if not items:
            return []
        with np.errstate(divide="ignore", invalid="ignore"):
            inv = 1.0 / d
            t1, t2 = (LO - margin - o) * inv, (HI + margin - o) * inv
            t1 = np.where(np.isnan(t1), -np.inf, t1)
            t2 = np.where(np.isnan(t2), np.inf, t2)
            tmin = np.max(np.minimum(t1, t2), axis=1)
            tmax = np.min(np.maximum(t1, t2), axis=1)
        hit = tmax >= np.maximum(tmin, 0.0)
        return [items[i] for i in np.nonzero(hit)[0]]

    def _segments_near(self, o, d):
        """Edge segments of the parts near the pick ray, plus all sketch segments."""
        A, B, owner = [], [], []
        margin = self.cam.scale * 10
        for pid, m in self._ray_parts(o, d, margin):
            segs = self._part_segs.get(pid)
            if segs is None:
                a_, b_, ow = [], [], []
                for ei, poly in enumerate(m.edge_polys):
                    if len(poly) >= 2:
                        a_.append(poly[:-1])
                        b_.append(poly[1:])
                        ow += [("edge", (pid, ei))] * (len(poly) - 1)
                segs = (np.vstack(a_).astype(np.float64), np.vstack(b_).astype(np.float64), ow) if a_ else None
                self._part_segs[pid] = segs
            if segs is not None:
                A.append(segs[0])
                B.append(segs[1])
                owner += segs[2]
        sA, sB, sO = self._segments()
        if len(sO):
            A.append(sA)
            B.append(sB)
            owner += sO
        if not A:
            return np.zeros((0, 3)), np.zeros((0, 3)), []
        return np.vstack(A), np.vstack(B), owner

    def _segments(self):
        """Sketch-curve segments (part edges are handled per part, see _segments_near)."""
        if self._segs is None:
            A, B, owner = [], [], []
            for fid, sr, _ in self.sketches:
                if self.sketcher and self.sketcher.fid == fid:
                    continue
                for eid, e in sr.sketch.entities.items():
                    if e["type"] == "point":
                        continue
                    pts = np.array([sr.plane.to_world(u, v) for u, v in sr.sketch.polyline(eid, 32)], np.float32)
                    A.append(pts[:-1])
                    B.append(pts[1:])
                    owner += [("sketch_line", (fid, eid))] * (len(pts) - 1)
            if A:
                self._segs = (np.vstack(A).astype(np.float64), np.vstack(B).astype(np.float64), owner)
            else:
                self._segs = (np.zeros((0, 3)), np.zeros((0, 3)), [])
        return self._segs

    def pick(self, x, y, kinds=None) -> Pick | None:
        allowed = (lambda k: k in kinds) if kinds is not None else self._allowed
        o, d = self.cam.ray(x, y)
        if self.overlays and kinds is None:
            hit = self._pick_overlay(x, y, o, d)
            if hit is not None:
                return hit
        best_face = (np.inf, None)
        part_ok = allowed("part")
        if allowed("face") or part_ok or allowed("edge"):
            for pid, m in self._ray_parts(o, d):
                t, ti = _ray_tris(o, d, m.verts, m.tris) if self.section is None else \
                    _ray_tris_kept(o, d, m.verts, m.tris, self._kept)
                if t < best_face[0]:
                    best_face = (t, Pick("part", (pid,)) if part_ok and not allowed("face")
                                 else Pick("face", (pid, int(m.tri_face[ti]))))
        face_z = self.cam.view_z_of_t(best_face[0]) if np.isfinite(best_face[0]) else -np.inf
        tol_z = self.cam.scale * 3 + 1e-6

        # edges & sketch lines (screen-space proximity)
        want_edge = allowed("edge")
        want_sl = allowed("sketch_line")
        if want_edge or want_sl:
            A, B, owner = self._segments_near(o, d)
            if len(owner):
                sa = self.cam.to_screen(A)
                sb = self.cam.to_screen(B)
                dist, t = _seg_dist(x, y, sa[:, :2], sb[:, :2])
                z = sa[:, 2] + t * (sb[:, 2] - sa[:, 2])
                order = np.argsort(dist)
                kept_a = self._kept(A) if self.section is not None else None
                for i in order[:40]:
                    if dist[i] > 7:
                        break
                    kind, key = owner[i]
                    if kept_a is not None and kind == "edge" and not kept_a[i]:
                        continue
                    if kind == "edge" and not want_edge:
                        continue
                    if kind == "sketch_line" and not want_sl:
                        continue
                    if kind == "edge" and z[i] < face_z - tol_z * 4:
                        continue      # hidden behind a face
                    return Pick(kind, key)

        # sketch regions (they lie on top of faces when sketching on a face)
        if allowed("region"):
            best = (np.inf, None)
            for fid, sr, meshes in self.sketches:
                if self.sketcher and self.sketcher.fid == fid:
                    continue
                for ri, m in enumerate(meshes):
                    tt, _ = _ray_tris(o, d, m.verts, m.tris)
                    if tt < best[0]:
                        best = (tt, Pick("region", (fid, ri)))
            if best[1] is not None and self.cam.view_z_of_t(best[0]) >= face_z - tol_z * 4:
                return best[1]

        # planes win when they're in front of, or level with, the nearest face
        plane_hit = (np.inf, None)
        if allowed("plane") and self.show_planes and self.sketcher is None:
            for key, pl, label, half in self.planes:
                denom = d @ pl.normal
                if abs(denom) < 1e-9:
                    continue
                tt = ((pl.origin - o) @ pl.normal) / denom
                if tt <= 0:
                    continue
                u, v = pl.to_local(o + tt * d)
                if abs(u) <= half and abs(v) <= half and tt < plane_hit[0]:
                    plane_hit = (tt, Pick("plane", key))
        if plane_hit[1] is not None and plane_hit[0] <= best_face[0] + tol_z * 4:
            return plane_hit[1]
        if best_face[1] is not None and (allowed("face") or part_ok):
            return best_face[1]
        return plane_hit[1]

    def _pick_overlay(self, x, y, o, d):
        best_poly = (6.0, None)
        best_mesh = (np.inf, None)
        for key, (kind, g) in self.overlays:
            if kind == "poly":
                if len(g) < 2:
                    continue
                S = self.cam.to_screen(np.asarray(g, float))
                dist, _ = _seg_dist(x, y, S[:-1, :2], S[1:, :2])
                if dist.min() < best_poly[0]:
                    best_poly = (float(dist.min()), key)
            else:
                t, _ = _ray_tris(o, d, g.verts, g.tris)
                if t < best_mesh[0]:
                    best_mesh = (t, key)
        if best_poly[1] is not None:
            return Pick("overlay", best_poly[1])
        if best_mesh[1] is not None:
            # only if nothing solid is clearly in front of it
            front = np.inf
            for _, m in self._ray_parts(o, d):
                t, _ = _ray_tris(o, d, m.verts, m.tris)
                front = min(front, t)
            if best_mesh[0] <= front + self.cam.scale * 4:
                return Pick("overlay", best_mesh[1])
        return None

    def surface_point(self, x, y):
        o, d = self.cam.ray(x, y)
        best = np.inf
        for _, m in self._ray_parts(o, d):
            t, _ = _ray_tris(o, d, m.verts, m.tris)
            best = min(best, t)
        return o + best * d if np.isfinite(best) else None

    def ray_plane(self, x, y, plane: K.Plane):
        o, d = self.cam.ray(x, y)
        denom = d @ plane.normal
        if abs(denom) < 1e-12:
            return None
        t = ((plane.origin - o) @ plane.normal) / denom
        return o + t * d

    # ================================================================== selection api
    def set_selection(self, picks):
        self.selection = list(picks)
        self.selectionChanged.emit()
        self.update()

    def clear_selection(self):
        if self.selection:
            self.selection = []
            self.selectionChanged.emit()
            self.update()

    def toggle(self, pick: Pick):
        if pick in self.selection:
            self.selection.remove(pick)
        else:
            self.selection.append(pick)
        self.selectionChanged.emit()
        self.update()

    # ================================================================== events
    def mousePressEvent(self, e):
        self._anim = None
        pos = e.position()
        self._press = (pos.x(), pos.y(), e.button())
        self._last = (pos.x(), pos.y())
        self._moved = False
        self._pivot = None
        if e.button() == Qt.RightButton:
            self._pivot = self.orbit_pivot(pos.x(), pos.y())
        if e.button() == Qt.LeftButton:
            cube = self._cube_pick(pos.x(), pos.y())
            if cube is not None:
                self._press = None
                self._cube_click(cube)
                return
            mi = self._manip_hit(pos.x(), pos.y()) if self.manipulators else None
            if mi is not None:
                m = self.manipulators[mi]
                t = self._manip_param(m, pos.x(), pos.y())
                self._manip_drag = {"i": mi, "offset": (t - m["value"]) if t is not None else 0.0}
                return
            if self.sketcher and self.sketcher.mouse_press(self, pos.x(), pos.y(), e):
                self.update()
                return
            self._part_drag = False
            if self.drag_handler is not None and self.pick_handler is None and \
                    not (e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)):
                self._part_drag = bool(self.drag_handler.press(pos.x(), pos.y(), e))

    def mouseMoveEvent(self, e):
        pos = e.position()
        x, y = pos.x(), pos.y()
        if self._last is not None and self._press is not None:
            dx, dy = x - self._last[0], y - self._last[1]
            if abs(x - self._press[0]) + abs(y - self._press[1]) > 3:
                self._moved = True
            btn = self._press[2]
            if btn == Qt.LeftButton and self._manip_drag is not None:
                md = self._manip_drag
                m = self.manipulators[md["i"]]
                t = self._manip_param(m, x, y)
                if t is not None:
                    v = t - md["offset"]
                    step = m.get("snap", 0.0)
                    if step and not (e.modifiers() & Qt.ShiftModifier):
                        v = round(v / step) * step
                    if m.get("min") is not None:
                        v = max(v, m["min"])
                    if abs(v) < 1e-6:
                        v = 1e-3 if m["value"] >= 0 else -1e-3
                    if v != m["value"]:
                        m["value"] = v
                        m["on_drag"](v)
                self._last = (x, y)
                self.update()
                return
            if btn == Qt.RightButton and not (e.modifiers() & Qt.ControlModifier):
                if self._moved:
                    self.cam.orbit(dx, dy, self._pivot)
                    self._orbiting = True
                    self.update()
            elif btn == Qt.MiddleButton or (btn == Qt.RightButton and e.modifiers() & Qt.ControlModifier):
                self.cam.pan(dx, dy)
                self.update()
            elif btn == Qt.LeftButton and self.sketcher:
                self.sketcher.mouse_drag(self, x, y, e)
                self.update()
            elif btn == Qt.LeftButton and self._part_drag and self._moved:
                self.setCursor(Qt.ClosedHandCursor)
                self.drag_handler.move(x, y, e)
                self.update()
            self._last = (x, y)
            return
        # hover
        cube = self._cube_pick(x, y)
        if cube != getattr(self, "_cube_hover", None):
            self._cube_hover = cube
            self.update()
        if cube is not None:
            self.setCursor(Qt.PointingHandCursor)
            return
        mh = self._manip_hit(x, y) if self.manipulators else None
        if mh != self._manip_hover:
            self._manip_hover = mh
            self.update()
        if mh is not None:
            self.setCursor(Qt.OpenHandCursor)
            return
        if self.drag_handler is not None and hasattr(self.drag_handler, "hover") and not self.sketcher \
                and self.drag_handler.hover(x, y):
            self.setCursor(Qt.OpenHandCursor)
            if self.hover is not None:
                self.hover = None
                self.hoverChanged.emit(None)
                self.update()
            return
        self.setCursor(Qt.ArrowCursor)
        if self.sketcher:
            self.sketcher.mouse_move(self, x, y, e)
            self.update()
            return
        # picking under the mouse can take a few ms on big models: do it once per event-loop turn with the latest
        # position, rather than for every mouse-move event in a burst
        self._hover_xy = (x, y)
        if not getattr(self, "_hover_pending", False):
            self._hover_pending = True
            QTimer.singleShot(0, self._do_hover)

    def _do_hover(self):
        self._hover_pending = False
        xy = getattr(self, "_hover_xy", None)
        if xy is None or self._press is not None or self.sketcher:
            return
        h = self.pick(*xy)
        if h != self.hover:
            self.hover = h
            self.hoverChanged.emit(h)
            self.update()

    def _thumb_on(self):
        return bool(getattr(self, "_thumb", False))

    def render_thumbnail(self, w=480, h=300):
        """A clean isometric picture of the model (no planes, cube, highlights or labels) for the home page.
        Returns a QImage, or None when there's nothing to show."""
        if not self.parts and not self.sketches:
            return None
        saved = (self.cam.state(), self.show_planes, self.selection, self.hover, self.feature_hl,
                 self.measure_line, self.triads, self.ghosts, self.overlays, self.manipulators, self.sketches)
        try:
            self._thumb = True
            self.show_planes = False
            self.selection, self.hover, self.feature_hl, self.measure_line = [], None, None, None
            self.triads, self.ghosts, self.overlays, self.manipulators = [], [], [], []
            if self.parts:
                self.sketches = []          # just the parts, like Onshape's thumbnails
            back, up = STANDARD_VIEWS["Isometric"]
            R = basis(np.array(back, float), np.array(up, float))
            lo, hi = self.model_bounds()
            # fit into the middle part of the view with the thumbnail's shape, leaving a little margin
            vw, vh = max(self.width(), 1), max(self.height(), 1)
            aspect = w / h
            cw, ch = (vh * aspect, vh) if vw / vh > aspect else (vw, vw / aspect)
            target, _ = self.cam.fit_target(lo, hi, R)
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            v = corners @ R.T
            scale = max(np.ptp(v[:, 0]) / (cw * 0.86), np.ptp(v[:, 1]) / (ch * 0.86), 1e-6)
            self.cam.set_state((target, R, scale))
            img = self.grabFramebuffer()
        finally:
            self._thumb = False
            (st, self.show_planes, self.selection, self.hover, self.feature_hl, self.measure_line, self.triads,
             self.ghosts, self.overlays, self.manipulators, self.sketches) = saved
            self.cam.set_state(st)
            self.update()
        dpr = img.devicePixelRatio() or 1.0
        iw, ih = img.width(), img.height()
        cw_px, ch_px = (int(ih * aspect), ih) if iw / ih > aspect else (iw, int(iw / aspect))
        img = img.copy((iw - cw_px) // 2, (ih - ch_px) // 2, cw_px, ch_px)
        return img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def orbit_pivot(self, x, y):
        """The point the view rotates about, by the 'Rotate around' setting:
        cursor = the model point under the mouse (Onshape-style), or the model's centre over empty space;
        centre = always the centre of the model; origin = always the origin."""
        if self.orbit_mode == "origin":
            return np.zeros(3)
        if self.orbit_mode == "cursor":
            p = self.surface_point(x, y)
            if p is not None:
                return p
        if self._part_boxes:
            lo = np.min([b[0] for b in self._part_boxes.values()], axis=0)
            hi = np.max([b[1] for b in self._part_boxes.values()], axis=0)
            return (lo + hi) / 2
        lo, hi = self.model_bounds()
        return (lo + hi) / 2 if (self.parts or self.sketches) else np.zeros(3)

    def mouseReleaseEvent(self, e):
        pos = e.position()
        press = self._press
        self._press = None
        self._last = None
        if self._orbiting:
            self._orbiting = False
            self.update()
        if press is None:
            return
        if self._manip_drag is not None:
            m = self.manipulators[self._manip_drag["i"]] if self._manip_drag["i"] < len(self.manipulators) else None
            self._manip_drag = None
            if m and m.get("on_done"):
                m["on_done"]()
            self.update()
            return
        if press[2] == Qt.RightButton and not self._moved:
            pk = self.pick(pos.x(), pos.y(), kinds={"face", "edge", "region", "plane", "sketch_line"}) \
                if not self.sketcher else None
            self.contextRequested.emit(pk, e.globalPosition().toPoint())
            return
        if press[2] != Qt.LeftButton:
            return
        if self.sketcher:
            self.sketcher.mouse_release(self, pos.x(), pos.y(), e, self._moved)
            self.update()
            return
        if self._part_drag:
            self._part_drag = False
            self.drag_handler.release(pos.x(), pos.y(), e, self._moved)
            self.setCursor(Qt.ArrowCursor)
            if self._moved:
                self.update()
                return
        if self._moved:
            return
        self.last_click = (pos.x(), pos.y())
        pk = self.pick(pos.x(), pos.y())
        if self.pick_handler is not None:
            if pk is not None:
                self.pick_handler(pk)
            self.update()
            return
        if pk is None:
            if not (e.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier)) and self.select_filter is None:
                self.clear_selection()
            return
        self.toggle(pk)

    def mouseDoubleClickEvent(self, e):
        pos = e.position()
        if self.sketcher:
            self.sketcher.double_click(self, pos.x(), pos.y(), e)
            self.update()
            return
        pk = self.pick(pos.x(), pos.y())
        self.doubleClicked.emit(pk)

    def wheelEvent(self, e):
        """Zoom at the cursor — glides over a few frames rather than jumping, like Onshape."""
        self._anim = None
        delta = e.angleDelta().y()
        if delta == 0:
            return
        pos = e.position()
        self._zoom_left += (delta / 120.0) * math.log(0.85)
        self._zoom_at = (pos.x(), pos.y())
        if not self._zoom_timer.isActive():
            self._zoom_timer.start(12)

    def _step_zoom(self):
        left = self._zoom_left
        if abs(left) < 1e-4:
            self._zoom_left = 0.0
            self._zoom_timer.stop()
            return
        step = left if abs(left) < 0.004 else left * 0.32
        self._zoom_left -= step
        self.cam.zoom(math.exp(step), *self._zoom_at)
        self.update()

    def keyPressEvent(self, e):
        if self.sketcher and self.sketcher.key_press(self, e):
            self.update()
            return
        super().keyPressEvent(e)

    def leaveEvent(self, e):
        if self.hover:
            self.hover = None
            self.hoverChanged.emit(None)
            self.update()
