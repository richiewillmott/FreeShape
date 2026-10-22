"""Home page: start something new, open a file, or pick up a recent project (cards with thumbnails)."""
from __future__ import annotations

import hashlib
import os
import time

from PySide6.QtCore import Qt, Signal, QSize, QRectF, QPointF
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont, QFontMetrics, QPen, QPainterPath
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame, QGridLayout, QMenu,
    QSizePolicy, QLineEdit, QToolButton,
)

from .icons import icon

CARD_W, THUMB_H = 236, 148
KINDS = {".fshape": "Part Studio", ".fcad": "Part Studio", ".fasm": "Assembly", ".fdwg": "Drawing", ".step": "STEP file",
         ".stp": "STEP file"}


def thumbs_dir():
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation) or os.path.expanduser("~")
    d = os.path.join(base, "FreeShape", "thumbnails")
    os.makedirs(d, exist_ok=True)
    return d


def thumb_path(path):
    key = hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode()).hexdigest()[:20]
    return os.path.join(thumbs_dir(), key + ".png")


def ago(t):
    if not t:
        return ""
    s = max(0, time.time() - t)
    for unit, n in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if s >= n:
            k = int(s // n)
            return f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


def size_text(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


class Card(QFrame):
    clicked = Signal(str)
    menuRequested = Signal(str, object)

    def __init__(self, path, opened_at):
        super().__init__()
        self.path = path
        self.setObjectName("homeCard")
        self.setFixedWidth(CARD_W)
        self.setCursor(Qt.PointingHandCursor)
        exists = os.path.exists(path)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 10)
        lay.setSpacing(3)
        self.thumb = QLabel()
        self.thumb.setObjectName("homeThumb")
        self.thumb.setFixedSize(CARD_W - 16, THUMB_H)
        self.thumb.setAlignment(Qt.AlignCenter)
        tp = thumb_path(path)
        ext = os.path.splitext(path)[1].lower()
        if exists and os.path.exists(tp):
            pm = QPixmap(tp).scaled(CARD_W - 16, THUMB_H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self.thumb.setPixmap(pm.copy((pm.width() - (CARD_W - 16)) // 2, (pm.height() - THUMB_H) // 2,
                                         CARD_W - 16, THUMB_H))
        else:
            name = "assembly" if ext == ".fasm" else "drawing" if ext == ".fdwg" else \
                "import" if ext in (".step", ".stp") else "part"
            self.thumb.setPixmap(icon(name, 64).pixmap(64, 64))
        lay.addWidget(self.thumb)
        title = QLabel(os.path.basename(path))
        title.setObjectName("homeCardTitle")
        fm = QFontMetrics(title.font())
        title.setText(fm.elidedText(os.path.basename(path), Qt.ElideMiddle, CARD_W - 20))
        lay.addWidget(title)
        folder = QLabel()
        folder.setObjectName("homeMuted")
        folder.setText(QFontMetrics(folder.font()).elidedText(os.path.dirname(path), Qt.ElideLeft, CARD_W - 20))
        lay.addWidget(folder)
        meta = [KINDS.get(ext, "File")]
        if exists:
            meta.append(ago(opened_at))
            meta.append(size_text(os.path.getsize(path)))
        else:
            meta.append("file not found")
        m = QLabel("  ·  ".join(x for x in meta if x))
        m.setObjectName("homeMuted" if exists else "homeMissing")
        lay.addWidget(m)
        self.setToolTip(path if exists else f"{path}\n(the file has been moved or deleted)")
        if not exists:
            self.setProperty("missing", "true")

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.path)

    def contextMenuEvent(self, e):
        self.menuRequested.emit(self.path, e.globalPos())


class HomePage(QWidget):
    openPath = Signal(str)
    newStudio = Signal()
    newAssembly = Signal()
    openDialog = Signal()
    importStep = Signal()
    backToWork = Signal()
    removeRecent = Signal(str)
    clearRecent = Signal()

    STYLE = """
    QWidget#home { background: #f5f6f8; }
    QLabel#homeTitle { font-size: 26px; font-weight: 600; color: #1f2328; }
    QLabel#homeSub, QLabel#homeMuted { color: #6a737d; font-size: 12px; }
    QLabel#homeMissing { color: #d93025; font-size: 12px; }
    QLabel#homeSection { font-size: 15px; font-weight: 600; color: #1f2328; }
    QLabel#homeCardTitle { font-size: 14px; font-weight: 600; color: #1f2328; }
    QFrame#homeDrop { background: #ffffff; border: 2px dashed #d5d9de; border-radius: 10px; }
    QFrame#homeDrop[hot="true"] { border-color: #1f6fd1; background: #f2f8ff; }
    QFrame#homeCard { background: #ffffff; border: 1px solid #e3e6ea; border-radius: 8px; }
    QFrame#homeCard:hover { border-color: #1f6fd1; background: #f2f8ff; }
    QFrame#homeCard[missing="true"] QLabel#homeThumb { background: #eef0f3; }
    QLabel#homeThumb { background: #eef0f3; border-radius: 5px; }
    QPushButton#homePrimary { background: #1f6fd1; color: white; border: none; border-radius: 5px;
                              padding: 8px 16px; font-weight: 600; }
    QPushButton#homePrimary:hover { background: #155bb0; }
    QPushButton#homeButton { background: #ffffff; border: 1px solid #d5d9de; border-radius: 5px; padding: 8px 14px; }
    QPushButton#homeButton:hover { border-color: #1f6fd1; background: #f2f8ff; }
    QToolButton#homeLink { border: none; color: #1f6fd1; background: transparent; }
    QToolButton#homeLink:hover { text-decoration: underline; }
    QLineEdit#homeSearch { border: 1px solid #d5d9de; border-radius: 5px; padding: 5px 8px; background: #ffffff; }
    """

    def __init__(self):
        super().__init__()
        self.setObjectName("home")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(self.STYLE)
        self.setAcceptDrops(True)
        self._recent = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("home")
        scroll.setWidget(body)
        row = QHBoxLayout(body)
        row.addStretch(1)
        col = QWidget()
        col.setMaximumWidth(1040)
        col.setMinimumWidth(520)
        col.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addWidget(col, 10)
        row.addStretch(1)
        v = QVBoxLayout(col)
        v.setContentsMargins(24, 36, 24, 36)
        v.setSpacing(10)

        t = QLabel("Start a project")
        t.setObjectName("homeTitle")
        v.addWidget(t)
        sub = QLabel("Make something new, open a FreeShape file, or bring in a STEP model. "
                     "You can also drag files anywhere into this window.")
        sub.setObjectName("homeSub")
        sub.setWordWrap(True)
        v.addWidget(sub)
        v.addSpacing(6)

        drop = QFrame()
        drop.setObjectName("homeDrop")
        self.drop = drop
        dl = QVBoxLayout(drop)
        dl.setContentsMargins(20, 22, 20, 22)
        dl.setSpacing(12)
        ic = QLabel()
        ic.setPixmap(icon("part", 40).pixmap(40, 40))
        ic.setAlignment(Qt.AlignCenter)
        dl.addWidget(ic)
        dt = QLabel("Drop a .fshape, .fasm, .fdwg or STEP file here")
        dt.setAlignment(Qt.AlignCenter)
        dt.setStyleSheet("font-size: 14px;")
        dl.addWidget(dt)
        br = QHBoxLayout()
        br.addStretch(1)
        for text, sig, primary, ic_name in (("New Part Studio", self.newStudio, True, "part"),
                                            ("New Assembly", self.newAssembly, False, "assembly"),
                                            ("Open…", self.openDialog, False, None),
                                            ("Import STEP…", self.importStep, False, "import")):
            b = QPushButton(text)
            b.setObjectName("homePrimary" if primary else "homeButton")
            if ic_name and not primary:
                b.setIcon(icon(ic_name, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(sig.emit)
            br.addWidget(b)
        br.addStretch(1)
        dl.addLayout(br)
        self.back = QToolButton()
        self.back.setObjectName("homeLink")
        self.back.setCursor(Qt.PointingHandCursor)
        self.back.clicked.connect(self.backToWork.emit)
        self.back.hide()
        bl = QHBoxLayout()
        bl.addStretch(1)
        bl.addWidget(self.back)
        bl.addStretch(1)
        dl.addLayout(bl)
        v.addWidget(drop)
        v.addSpacing(14)

        head = QHBoxLayout()
        rt = QLabel("Recent")
        rt.setObjectName("homeSection")
        head.addWidget(rt)
        head.addStretch(1)
        self.search = QLineEdit()
        self.search.setObjectName("homeSearch")
        self.search.setPlaceholderText("Search recent")
        self.search.setFixedWidth(200)
        self.search.textChanged.connect(lambda _t: self._fill())
        head.addWidget(self.search)
        clear = QToolButton()
        clear.setObjectName("homeLink")
        clear.setText("Clear list")
        clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self.clearRecent.emit)
        head.addWidget(clear)
        v.addLayout(head)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 4, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(14)
        self.grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        v.addWidget(self.grid_host)
        self.empty = QLabel("Files you open, save or import will appear here.")
        self.empty.setObjectName("homeSub")
        v.addWidget(self.empty)
        v.addStretch(1)
        self._cols = 0

    # ------------------------------------------------------------------ content
    def set_recent(self, items, back_name=None):
        """items: [(path, opened_at)] newest first. back_name: the open tab to offer going back to."""
        self._recent = list(items)
        if back_name:
            self.back.setText(f"← Back to “{back_name}”")
            self.back.show()
        else:
            self.back.hide()
        self._cols = 0
        self._fill()

    def _columns(self):
        w = self.grid_host.width() or self.width() - 80
        return max(1, (w + 14) // (CARD_W + 14))

    def _fill(self):
        while self.grid.count():
            it = self.grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        q = self.search.text().strip().lower()
        items = [(p, t) for p, t in self._recent if not q or q in os.path.basename(p).lower()]
        cols = self._columns()
        self._cols = cols
        for i, (path, t) in enumerate(items):
            c = Card(path, t)
            c.clicked.connect(self.openPath.emit)
            c.menuRequested.connect(self._card_menu)
            self.grid.addWidget(c, i // cols, i % cols)
        self.empty.setVisible(not items)
        self.empty.setText("Files you open, save or import will appear here." if not q else "No recent files match.")

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._recent and self._columns() != self._cols:
            self._fill()

    def showEvent(self, e):
        super().showEvent(e)
        if self._recent and self._columns() != self._cols:
            self._fill()

    def _card_menu(self, path, gpos):
        m = QMenu(self)
        if os.path.exists(path):
            m.addAction("Open", lambda: self.openPath.emit(path))
            m.addAction("Show in folder", lambda: self._show_in_folder(path))
        m.addAction("Remove from list", lambda: self.removeRecent.emit(path))
        m.exec(gpos)

    @staticmethod
    def _show_in_folder(path):
        import subprocess
        import sys
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(path)])
        except OSError:
            pass

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop.setProperty("hot", "true")
            self.drop.style().unpolish(self.drop)
            self.drop.style().polish(self.drop)

    def dragLeaveEvent(self, e):
        self.drop.setProperty("hot", "false")
        self.drop.style().unpolish(self.drop)
        self.drop.style().polish(self.drop)

    def dropEvent(self, e):
        self.dragLeaveEvent(e)
        for url in e.mimeData().urls():
            p = url.toLocalFile()
            if p:
                self.openPath.emit(p)
