"""The start-up splash screen.

Shown straight after Qt starts — before the geometry kernel (the slow part) is loaded — so something appears
the moment FreeShape is opened, from Python or the packaged .exe. It only needs PySide6 and the logo picture.
The loading steps call step(text, fraction) to move the bar; the window closes itself once the main window is up.
"""
import os
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import QApplication, QWidget

W, H = 720, 400
BAR_H = 40
GREEN = QColor(46, 170, 92)
GREEN_HI = QColor(120, 230, 150)

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def _version():
    try:
        from .. import __revision__, __version__
        return f"{__version__}  ·  {__revision__}"
    except Exception:
        return ""


class Splash(QWidget):
    MIN_SECONDS = 1.2        # stay up at least this long, so it doesn't just flash

    def __init__(self):
        super().__init__(None, Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedSize(W, H)
        self.text = "Starting…"
        self.frac = 0.0
        self.t0 = time.monotonic()
        self._bg = self._make_background()
        scr = QGuiApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - W // 2, g.center().y() - H // 2)

    # ------------------------------------------------------------------ progress
    def step(self, text, frac):
        self.text = text
        self.frac = max(self.frac, min(1.0, frac))
        self.repaint()
        QApplication.processEvents()

    def finish(self, window):
        self.step("Ready", 1.0)
        left = self.MIN_SECONDS - (time.monotonic() - self.t0)
        end = time.monotonic() + max(0.0, left)
        while time.monotonic() < end:
            QApplication.processEvents()
            time.sleep(0.01)
        window.raise_()
        window.activateWindow()
        self.close()

    # ------------------------------------------------------------------ drawing
    def _make_background(self):
        dpr = self.devicePixelRatioF() if hasattr(self, "devicePixelRatioF") else 1.0
        pm = QPixmap(int(W * dpr), int(H * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, W, H), 8, 8)
        p.setClipPath(clip)
        # dark base
        g = QLinearGradient(0, 0, W, H)
        g.setColorAt(0.0, QColor(14, 16, 19))
        g.setColorAt(0.55, QColor(24, 27, 31))
        g.setColorAt(1.0, QColor(40, 44, 49))
        p.fillRect(QRectF(0, 0, W, H), g)
        # big soft glow behind the logo side
        rg = QRadialGradient(QPointF(W * 0.82, H * 0.25), W * 0.6)
        rg.setColorAt(0.0, QColor(46, 170, 92, 55))
        rg.setColorAt(1.0, QColor(46, 170, 92, 0))
        p.fillRect(QRectF(0, 0, W, H), rg)
        # sweeping light streaks, like light catching a machined edge
        for (x0, y0, x1, y1, w, a, col) in (
                (W * 0.30, -20, W + 40, H * 0.52, 2.2, 230, GREEN_HI),
                (W * 0.26, -20, W + 40, H * 0.60, 9.0, 60, GREEN),
                (-40, H * 0.86, W * 0.72, H * 0.60, 1.6, 170, QColor(210, 220, 228)),
                (-40, H * 0.92, W * 0.78, H * 0.64, 7.0, 38, QColor(210, 220, 228))):
            lg = QLinearGradient(x0, y0, x1, y1)
            c0, c1, c2 = QColor(col), QColor(col), QColor(col)
            c0.setAlpha(0)
            c1.setAlpha(a)
            c2.setAlpha(0)
            lg.setColorAt(0.0, c0)
            lg.setColorAt(0.65, c1)
            lg.setColorAt(1.0, c2)
            p.setPen(QPen(QBrush(lg), w, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(x0, y0), QPointF(x1, y1))
        # a darker facet across the top right, for depth
        facet = QPainterPath()
        facet.moveTo(W * 0.30, 0)
        facet.lineTo(W, 0)
        facet.lineTo(W, H * 0.52)
        facet.closeSubpath()
        fg = QLinearGradient(W * 0.6, 0, W, H * 0.4)
        fg.setColorAt(0, QColor(255, 255, 255, 10))
        fg.setColorAt(1, QColor(255, 255, 255, 22))
        p.fillPath(facet, fg)
        # faint wireframe cube, bottom right
        p.setPen(QPen(QColor(255, 255, 255, 26), 1.2))
        cx, cy, s = W * 0.80, H * 0.62, 70
        top = [QPointF(cx, cy - s), QPointF(cx + s * 0.87, cy - s * 0.5), QPointF(cx, cy), QPointF(cx - s * 0.87, cy - s * 0.5)]
        for i in range(4):
            p.drawLine(top[i], top[(i + 1) % 4])
        for q in (top[1], top[2], top[3]):
            p.drawLine(q, QPointF(q.x(), q.y() + s))
        p.drawLine(QPointF(top[1].x(), top[1].y() + s), QPointF(cx, cy + s))
        p.drawLine(QPointF(top[3].x(), top[3].y() + s), QPointF(cx, cy + s))
        # logo + name
        logo = QPixmap(os.path.join(ASSETS, "logo_256.png"))
        if not logo.isNull():
            p.drawPixmap(QRectF(56, 150, 92, 92), logo, QRectF(logo.rect()))
        f = QFont("Segoe UI")
        f.setPixelSize(52)
        f.setWeight(QFont.DemiBold)
        p.setFont(f)
        p.setPen(QColor(245, 247, 250))
        p.drawText(QRectF(168, 148, 460, 64), Qt.AlignLeft | Qt.AlignVCenter, "FreeShape")
        f2 = QFont("Segoe UI")
        f2.setPixelSize(15)
        p.setFont(f2)
        p.setPen(QColor(170, 180, 190))
        p.drawText(QRectF(171, 208, 460, 24), Qt.AlignLeft | Qt.AlignVCenter, "Parametric 3D CAD")
        v = _version()
        if v:
            f3 = QFont("Segoe UI")
            f3.setPixelSize(12)
            p.setFont(f3)
            p.setPen(QColor(130, 140, 150))
            p.drawText(QRectF(24, 18, W - 48, 18), Qt.AlignRight | Qt.AlignVCenter, v)
        # status bar strip
        p.fillRect(QRectF(0, H - BAR_H, W, BAR_H), QColor(30, 33, 37))
        p.setPen(QPen(QColor(255, 255, 255, 18), 1))
        p.drawLine(QPointF(0, H - BAR_H + 0.5), QPointF(W, H - BAR_H + 0.5))
        p.end()
        return pm

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, self._bg)
        # status text
        f = QFont("Segoe UI")
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(190, 198, 206))
        p.drawText(QRectF(22, H - BAR_H, 320, BAR_H), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        # progress bar
        bx, bw, bh = W - 22 - 300, 300, 6
        by = H - BAR_H / 2 - bh / 2
        track = QRectF(bx, by, bw, bh)
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.setBrush(QColor(12, 14, 16))
        p.drawRoundedRect(track, 3, 3)
        if self.frac > 0:
            fill = QRectF(bx, by, max(bh, bw * self.frac), bh)
            g = QLinearGradient(fill.left(), 0, fill.right(), 0)
            g.setColorAt(0, QColor(30, 120, 66))
            g.setColorAt(1, GREEN_HI)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawRoundedRect(fill, 3, 3)
            # a small bright glint at the leading edge
            gx = fill.right()
            rg = QRadialGradient(QPointF(gx - 2, by + bh / 2), 14)
            rg.setColorAt(0, QColor(220, 255, 230, 160))
            rg.setColorAt(1, QColor(220, 255, 230, 0))
            p.setBrush(rg)
            p.drawEllipse(QPointF(gx - 2, by + bh / 2), 14, 6)
        p.end()


def show_splash():
    """Create and show the splash (returns None if anything goes wrong — never stops FreeShape starting)."""
    try:
        s = Splash()
        s.show()
        s.step("Starting…", 0.05)
        return s
    except Exception:
        return None
