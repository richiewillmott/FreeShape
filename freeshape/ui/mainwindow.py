"""Main window: ties the document, viewport, panels, toolbars and dialogs together."""
from __future__ import annotations

import base64
import copy
import os
import sys

import numpy as np
from PySide6.QtCore import Qt, QSize, QTimer, QPoint
from PySide6.QtGui import QAction, QKeySequence, QShortcut, QColor
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolButton, QLabel, QFrame, QSplitter, QMenu,
    QStackedWidget, QFileDialog, QMessageBox, QInputDialog, QColorDialog, QTabBar, QSizePolicy, QApplication, QDialog,
    QComboBox, QPushButton,
)

from .. import kernel as K
from ..i18n import tr
from .. import units as U
from .. import features as F
from ..document import Document
from ..assembly import AssemblyDoc
from ..drawing import DrawingDoc
from ..sketch import Sketch
from . import theme
from . import filedlg
from .icons import icon
from .menus import submenu
from .viewport import Viewport, Pick, plane_corners
from .panels import LeftPanel, STD_PLANES, DocTabBar, ROLE_ID, ROLE_KIND
from .dialogs import FeatureDialog, SketchDialog, pick_to_item
from .section import SectionPanel
from .sketcher import Sketcher, CONSTRAINT_TOOLS
from .numparse import fmt
from .assembly_ui import AssemblyController

APP_NAME = "FreeShape"
ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets")


def app_icon():
    from PySide6.QtGui import QIcon
    ic = QIcon()
    for size in (16, 32, 48, 64, 128, 256, 512):
        path = os.path.join(ASSETS, f"logo_{size}.png")
        if os.path.exists(path):
            ic.addFile(path)
    return ic


def _sep():
    f = QFrame()
    f.setObjectName("sep")
    f.setFixedWidth(1)
    f.setFixedHeight(26)
    return f


class _ToolStrip(QWidget):
    """A toolbar row that never makes the window wider than the screen: when its buttons don't fit, it scrolls
    sideways (mouse wheel over it, or the ‹ › buttons at its ends)."""

    def __init__(self, inner):
        super().__init__()
        from PySide6.QtWidgets import QScrollArea, QFrame as _QF
        self.inner = inner
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.area = QScrollArea()
        self.area.setFrameShape(_QF.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.area.setWidgetResizable(True)
        self.area.setWidget(inner)
        self.area.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }")
        self.left_btn, self.right_btn = QToolButton(), QToolButton()
        for b, txt, step in ((self.left_btn, "‹", -1), (self.right_btn, "›", 1)):
            b.setText(txt)
            b.setAutoRaise(True)
            b.setFixedWidth(18)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda _=False, st=step: self.scroll_by(st * 160))
            b.hide()
        lay.addWidget(self.left_btn)
        lay.addWidget(self.area, 1)
        lay.addWidget(self.right_btn)
        self.area.horizontalScrollBar().valueChanged.connect(lambda _v: self._arrows())
        self.area.horizontalScrollBar().rangeChanged.connect(lambda _a, _b: self._arrows())

    def scroll_by(self, dx):
        sb = self.area.horizontalScrollBar()
        sb.setValue(sb.value() + dx)

    def _arrows(self):
        sb = self.area.horizontalScrollBar()
        over = sb.maximum() > 0
        self.left_btn.setVisible(over)
        self.right_btn.setVisible(over)
        self.left_btn.setEnabled(sb.value() > 0)
        self.right_btn.setEnabled(sb.value() < sb.maximum())

    def wheelEvent(self, e):
        d = e.angleDelta()
        self.scroll_by(-(d.y() or d.x()) // 2)
        e.accept()

    def minimumSizeHint(self):
        return QSize(200, self.inner.minimumSizeHint().height())

    def sizeHint(self):
        return self.inner.sizeHint()


class MainWindow(QMainWindow):
    def __init__(self, path=None):
        super().__init__()
        self.setStyleSheet(theme.STYLESHEET)
        self.setWindowIcon(app_icon())
        scr = QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr is not None else None
        if avail is not None and avail.width() > 400:      # fit a small or scaled-up screen
            self.resize(min(1440, avail.width() - 40), min(900, avail.height() - 60))
        else:
            self.resize(1440, 900)
        self.doc = None             # the document in the current tab
        self.docs = {}              # tab key -> Document
        self._views = {}            # tab key -> saved camera state
        self._studio_counter = 0
        self.mode = "idle"          # idle | dialog | sketch
        self.dialog = None
        self.sketcher = None
        self.sk_ctx = None
        self.sketch_waiting_for_plane = False
        self._sketch_buttons = {}
        self._menu_actions = []               # (menu action, icon name) for the ▾ menus
        self.section_panel = None
        self.history_panel = None
        from PySide6.QtCore import QSettings
        self.settings = QSettings("FreeShape", "FreeShape")
        F.LINKER = self._derived_link              # derived parts find their source studios through the window
        F.LINK_SOURCE_STAMP = self._derived_source_stamp
        if not os.environ.get("FREESHAPE_NO_PLUGINS"):
            from .. import plugins as _plugins
            _plugins.load_all()
        self._build_ui()
        self._bind_shortcuts()
        self.set_theme(str(self.settings.value("theme", "light")), save=False)
        self.set_orbit_mode(str(self.settings.value("orbit", "cursor")), save=False)
        self.set_nav_style(str(self.settings.value("nav_style", "onshape")), save=False)
        self.set_zoom_speed(str(self.settings.value("zoom_speed", "normal")), save=False)
        self.set_shiny(str(self.settings.value("shiny", "true")).lower() != "false", save=False)
        self._load_display_settings()
        self._add_tab(self._new_document())
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self.autosave)
        self._autosave_timer.start(int(os.environ.get("FREESHAPE_AUTOSAVE_MS", "60000")))
        if not os.environ.get("FREESHAPE_NO_RECOVERY"):
            QTimer.singleShot(500, self.offer_recovery)
        if path:
            self.open_any(path)
        elif not os.environ.get("FREESHAPE_NO_HOME"):
            self.show_home()
        QTimer.singleShot(50, self._fresh_view)

    # ================================================================== UI construction
    def _build_ui(self):
        self.asm = AssemblyController(self)
        from .drawing_ui import DrawingController
        self.dwg = DrawingController(self)
        central = QWidget()
        central.setObjectName("central")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_topbar())
        self.main_stack = QStackedWidget()
        work = QWidget()
        root_outer = root
        root = QVBoxLayout(work)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.tool_stack = QStackedWidget()
        self.tool_stack.setObjectName("toolbar")
        for build in (self._build_feature_toolbar, self._build_sketch_toolbar, self._build_assembly_toolbar,
                      self._build_drawing_toolbar):
            self.tool_stack.addWidget(_ToolStrip(build()))     # scrolls sideways on a narrow screen
        self.tool_stack.setFixedHeight(44)
        root.addWidget(self.tool_stack)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(1)
        self.left = LeftPanel()
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self.left)
        self.left_stack.addWidget(self.asm.panel)
        self.left_stack.addWidget(self.dwg.panel)
        split.addWidget(self.left_stack)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        self.viewport = Viewport()
        self.viewport.cube_menu_hook = self._fill_cube_menu
        self.view_stack = QStackedWidget()       # the 3D view, or the drawing sheet
        self.view_stack.addWidget(self.viewport)
        self.view_stack.addWidget(self.dwg.canvas)
        cl.addWidget(self.view_stack, 1)
        self.tabs = DocTabBar()
        self.tabs.currentChanged.connect(self._switch_tab)
        self.tabs.middleClicked.connect(self.close_tab)
        self.tabs.tabBarDoubleClicked.connect(self._rename_tab)
        self.tabs.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.customContextMenuRequested.connect(self._tab_context)
        tabwrap = QWidget()
        tabwrap.setObjectName("tabstrip")
        tabwrap.setStyleSheet("QWidget#tabstrip { background: #eef0f3; border-top: 1px solid %s; }" % theme.BORDER)
        tl = QHBoxLayout(tabwrap)
        tl.setContentsMargins(0, 0, 8, 0)
        tl.setSpacing(0)
        plus = QToolButton()
        plus.setText("+")
        plus.setToolTip(tr("Create a Part Studio, an Assembly or a Drawing"))
        plus.setFixedSize(40, 32)
        plus.setStyleSheet("QToolButton { font-size: 20px; color: #3b4250; border: none; border-right: 1px solid #d5d9de;"
                           " border-radius: 0; background: #eef0f3; }"
                           "QToolButton:hover { background: #e3e8ef; }")
        plus.setPopupMode(QToolButton.InstantPopup)
        pm = QMenu(plus)
        pm.addAction(icon("part", 16), tr("Create Part Studio"), self.new_doc)
        pm.addAction(icon("assembly", 16), tr("Create Assembly"), self.new_assembly)
        pm.addAction(icon("drawing", 16), tr("Create Drawing…"), lambda: self.new_drawing())
        plus.setMenu(pm)
        tl.addWidget(plus)
        tl.addWidget(self.tabs, 1)
        tl.addStretch(0)
        self.units_label = QToolButton()               # Units: millimetre ▾ — this document's length unit
        self.units_label.setObjectName("unitsButton")
        self.units_label.setText(tr("Units: millimetre ▾"))
        self.units_label.setToolTip("The unit lengths are shown and typed in, for this document. A bare number you "
                                    "type is in this unit; '25 mm' or '1 in' always work.")
        self.units_label.setPopupMode(QToolButton.InstantPopup)
        um = QMenu(self.units_label)
        um.aboutToShow.connect(lambda m=um: self._fill_units_menu(m))
        self.units_label.setMenu(um)
        tl.addWidget(self.units_label)
        self._tabwrap = tabwrap
        split.addWidget(center)
        split.setStretchFactor(1, 1)
        split.setSizes([270, 1170])
        root.addWidget(split, 1)
        root.addWidget(self._tabwrap)
        from .home import HomePage
        self.home = HomePage()
        self.main_stack.addWidget(work)
        self.main_stack.addWidget(self.home)
        root_outer.addWidget(self.main_stack, 1)
        hp = self.home
        hp.openPath.connect(self.open_any)
        hp.newStudio.connect(self._home_new_studio)
        hp.newAssembly.connect(self.new_assembly)
        hp.openDialog.connect(self.open_dialog)
        hp.importStep.connect(self.import_step)
        hp.backToWork.connect(self.show_workspace)
        hp.removeRecent.connect(self._remove_recent)
        hp.clearRecent.connect(self._clear_recent)
        self.setAcceptDrops(True)
        self.setCentralWidget(central)

        self.measure = _MeasurePanel(self.viewport, lambda: self.start_feature("measure_value"))
        self.measure.hide()
        self.status = self.statusBar()
        self.status.showMessage(tr("Ready"))

        lp = self.left
        lp.features.editRequested.connect(self.edit_feature)
        lp.features.contextRequested.connect(self._tree_context)
        lp.features.orderChanged.connect(self._tree_reordered)
        lp.features.planeClicked.connect(self._tree_plane_clicked)
        lp.features.featureHovered.connect(self._feature_hovered)
        lp.features.itemSelectionChanged.connect(self._update_feature_hl)
        self._hl_hover = None
        self._hl_cache = {}
        lp.filter.textChanged.connect(lambda t: (lp.features.set_filter(t), self._populate_lists()))
        lp.features.eyeClicked.connect(self._tree_eye)
        lp.features.folderToggled.connect(self._toggle_folder)
        lp.new_folder_btn.clicked.connect(lambda: self._new_folder(self.left.features.selected_feature_ids()))
        lp.parts.eyeClicked.connect(lambda pid: self.doc.toggle_part_hidden(pid) if self.doc is not None else None)
        lp.parts.partClicked.connect(self._part_clicked)
        lp.parts.itemSelectionChanged.connect(self._parts_list_selection)
        lp.parts.contextRequested.connect(self._part_context)
        self.viewport.selectionChanged.connect(self._selection_changed)
        self.viewport.contextRequested.connect(self._viewport_context)
        self.viewport.on_plane_resized = self._plane_resized
        self.viewport.can_resize_planes = lambda: (self.mode == "idle" and not self.is_asm and not self.is_dwg
                                                  and hasattr(self.doc, "set_plane_rect"))
        self.viewport.doubleClicked.connect(self._viewport_double)
        self.viewport.hoverChanged.connect(self._hover_changed)

    def _build_topbar(self):
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(44)
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 0, 12, 0)
        mark = QLabel()
        mark.setPixmap(app_icon().pixmap(28, 28))
        h.addWidget(mark)
        h.addSpacing(6)
        logo = QLabel("freeshape")
        logo.setObjectName("logo")
        h.addWidget(logo)
        h.addSpacing(10)
        home_btn = QToolButton()
        home_btn.setObjectName("menuButton")
        home_btn.setText(tr("⌂  Home"))
        home_btn.setToolTip(tr("Home — new projects and recent files"))
        home_btn.clicked.connect(lambda: self.show_home() if self.main_stack.currentIndex() == 0 else self.show_workspace())
        self.home_btn = home_btn
        h.addWidget(home_btn)
        menu_btn = QToolButton()
        menu_btn.setObjectName("menuButton")
        menu_btn.setText(tr("☰  Document"))
        menu_btn.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(menu_btn)
        self._add_action(m, "New Part Studio", self.new_doc, "Ctrl+N")
        self._add_action(m, "New Assembly", self.new_assembly, "Ctrl+Shift+N")
        self._add_action(m, "New Drawing…", lambda: self.new_drawing())
        self._add_action(m, "Open…", self.open_dialog, "Ctrl+O")
        self.recent_menu = submenu(m, "Open recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._add_action(m, "Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        m.addSeparator()
        self._add_action(m, "Save", self.save, "Ctrl+S")
        self._add_action(m, "Save as…", self.save_as, "Ctrl+Shift+S")
        m.addSeparator()
        self._add_action(m, "Import STEP / BREP (new tab)…", self.import_step)
        self._add_action(m, "Import STEP / BREP into this tab…", lambda: self.import_step(into_current=True))
        self._add_action(m, "Export STEP…", lambda: self.export(None, "step"))
        self._add_action(m, "Export STL…", lambda: self.export(None, "stl"))
        self._add_action(m, "Export BREP…", lambda: self.export(None, "brep"))
        self._add_action(m, "Nest sheet metal for cutting…", self.nest_for_cutting)
        m.addSeparator()
        self._add_action(m, "Export as Python script…", self.export_script)
        self._add_action(m, "Run Python script…", self.run_script)
        m.addSeparator()
        self._add_action(m, "Create version…", self.create_version)
        self._add_action(m, "Version history…", self.show_history, "Ctrl+Alt+H")
        plm = submenu(m, "Plugins")
        from . import plugin_ui as _pui
        plm.aboutToShow.connect(lambda mm=plm: _pui.fill_menu(mm, self))
        m.addSeparator()
        self._add_action(m, "Keyboard shortcuts", self.show_shortcuts)
        self._add_action(m, "About FreeShape", self.show_about)
        self._add_action(m, "Quit", self.close, "Ctrl+Q")
        menu_btn.setMenu(m)
        h.addWidget(menu_btn)
        view_btn = QToolButton()
        view_btn.setObjectName("menuButton")
        view_btn.setText(tr("View"))
        view_btn.setPopupMode(QToolButton.InstantPopup)
        vm = QMenu(view_btn)
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"), 1):
            self._add_action(vm, name, lambda _=False, n=name: self.viewport.set_standard_view(n), f"Shift+{i}")
        for name in ("Dimetric", "Trimetric"):
            self._add_action(vm, name, lambda _=False, n=name: self.viewport.set_standard_view(n))
        vm.addSeparator()
        self.act_prev_view = self._add_action(vm, "Previous view", lambda: self.viewport.previous_view(), "Alt+Left")
        self._add_action(vm, "Zoom to window", lambda: self.viewport.start_zoom_window(), "Shift+Z")
        self._add_action(vm, "Named views…", self.named_views_dialog)
        dm = submenu(vm, "Display")
        dm.aboutToShow.connect(lambda: self._fill_display_menu(dm))
        vm.aboutToShow.connect(lambda: self.act_prev_view.setEnabled(self.viewport.has_previous_view()))
        vm.addSeparator()
        self.act_section = self._add_action(vm, "Section view", self.toggle_section, "Ctrl+Shift+X")
        self.act_section.setCheckable(True)
        self._add_action(vm, "Zoom to fit", lambda: self.dwg.canvas.fit() if self.is_dwg else self.viewport.fit(), "F")
        self.act_planes = self._add_action(vm, "Show default planes", self._toggle_planes)
        self.act_planes.setCheckable(True)
        self.act_planes.setChecked(True)
        self._add_action(vm, "Show all parts", self._show_all_parts)
        self.act_shiny = self._add_action(vm, "Shiny surfaces", lambda on: self.set_shiny(on))
        self.act_shiny.setCheckable(True)
        rm = submenu(vm, "Rotate around")
        from PySide6.QtGui import QActionGroup as _AG
        rgrp = _AG(self)
        self.orbit_actions = {}
        for mode, label in (("cursor", "The point under the mouse (model centre over empty space)"),
                            ("centre", "The centre of the model"), ("origin", "The origin")):
            a = QAction(tr(label), self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_orbit_mode(md))
            rgrp.addAction(a)
            rm.addAction(a)
            self.orbit_actions[mode] = a
        from . import navstyles as NS
        mm = submenu(vm, "Mouse style")
        mgrp = _AG(self)
        self.nav_actions = {}
        for key, st in NS.STYLES.items():
            a = QAction(tr(st["label"]), self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_nav_style(k))
            mgrp.addAction(a)
            mm.addAction(a)
            self.nav_actions[key] = a
        zm = submenu(vm, "Zoom speed")
        zgrp = _AG(self)
        self.zoom_actions = {}
        for key, label, _v in NS.ZOOM_SPEEDS:
            a = QAction(tr(label), self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, k=key: self.set_zoom_speed(k))
            zgrp.addAction(a)
            zm.addAction(a)
            self.zoom_actions[key] = a
        vm.addSeparator()
        tm = submenu(vm, "Theme")
        from PySide6.QtGui import QActionGroup
        grp = QActionGroup(self)
        self.theme_actions = {}
        for mode, label in (("light", "Light"), ("dark", "Dark")):
            a = QAction(tr(label), self)
            a.setCheckable(True)
            a.triggered.connect(lambda _=False, md=mode: self.set_theme(md))
            grp.addAction(a)
            tm.addAction(a)
            self.theme_actions[mode] = a
        vm.addSeparator()
        lang_menu = submenu(vm, "Language")
        lang_group = QActionGroup(self)
        selected_language = str(self.settings.value("language", "system"))
        for code, label in (("system", tr("System language")), ("en", "English"), ("ru", "Русский")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(code == selected_language)
            action.triggered.connect(lambda _=False, lang=code: self._set_language_preference(lang))
            lang_group.addAction(action)
            lang_menu.addAction(action)
        vm.addSeparator()
        self._add_action(vm, "Customize the S menu…", self.customize_s_menu)
        im = submenu(vm, "Icons")
        im.addAction(tr("Open my icons folder…"), self.open_icons_folder)
        im.addAction(tr("Save the built-in icons there (to start from)"), self.export_icons)
        im.addAction(tr("Reload icons"), self.reload_icons)
        view_btn.setMenu(vm)
        h.addWidget(view_btn)
        # ---- what a click / box selection may pick: vertices, edges, faces, solids, sketches, planes
        self.sel_btn = QToolButton()
        self.sel_btn.setObjectName("menuButton")
        self.sel_btn.setText(tr("Select"))
        self.sel_btn.setToolTip(tr("Choose which kinds of things can be selected (vertices, edges, faces …)"))
        self.sel_btn.setPopupMode(QToolButton.InstantPopup)
        selm = QMenu(self.sel_btn)
        self.sel_actions = {}
        for key, label, kinds, on in (
                ("vertex", "Vertices", ("vertex", "sketch_point"), True), ("edge", "Edges", ("edge",), True),
                ("face", "Faces", ("face",), True), ("part", "Solids (whole parts)", ("part",), False),
                ("sketch", "Sketches", ("sketch_line", "region"), True), ("plane", "Planes", ("plane",), True)):
            a = QAction(tr(label), self)
            a.setCheckable(True)
            a.setChecked(on)
            a.setData(kinds)
            a.toggled.connect(self._selection_filter_changed)
            selm.addAction(a)
            self.sel_actions[key] = a
        selm.addSeparator()
        ra = QAction(tr("Reset to default"), self)
        ra.triggered.connect(self._reset_selection_filter)
        selm.addAction(ra)
        self.sel_btn.setMenu(selm)
        h.addWidget(self.sel_btn)
        search_btn = QToolButton()
        search_btn.setObjectName("menuButton")
        search_btn.setText(tr("Search tools…"))
        search_btn.setIcon(icon("search"))
        search_btn.setToolTip(tr("Find a tool by name (Alt+C or Ctrl+Space)"))
        search_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        search_btn.clicked.connect(self.open_command_search)
        h.addWidget(search_btn)
        h.addSpacing(16)
        self.doc_label = QLabel("Untitled document")
        self.doc_label.setObjectName("docname")
        h.addWidget(self.doc_label)
        h.addStretch(1)
        hint = QLabel(tr("Right-drag: rotate · Middle-drag: pan · Wheel: zoom"))
        hint.setObjectName("muted")
        h.addWidget(hint)
        return bar

    # ------------------------------------------------------------------ command search
    def _palette_commands(self):
        from .palette import Command
        F_ = self.start_feature
        C = lambda key, name, desc, fn, kw="", grp="Feature": Command(key, name, desc, fn, kw, grp)
        cmds = [
            C("sketch", "Sketch", "Draw 2D profiles on a plane or a flat face. With vertices, edges or faces selected "
              "that define a plane, the plane is made for you.", lambda: self.start_sketch(), "draw 2d profile"),
            C("extrude", "Extrude", "Add or remove material by extending sketch regions or flat faces. Blind, "
              "symmetric or through all.", lambda: F_("extrude"), "boss cut pad pocket"),
            C("revolve", "Revolve", "Spin a sketch region around an axis to make round parts.",
              lambda: F_("revolve"), "lathe turn"),
            C("sweep", "Sweep", "Move a profile along a path to make a pipe or rail.", lambda: F_("sweep"), "pipe path"),
            C("loft", "Loft", "Blend smoothly between two or more profiles.", lambda: F_("loft"), "blend transition"),
            C("fillet", "Fillet", "Round off edges or faces with a radius.", lambda: F_("fillet"), "round blend corner"),
            C("chamfer", "Chamfer", "Create beveled edges or faces with a distance.", lambda: F_("chamfer"),
              "bevel"),
            C("draft", "Draft", "Angle side faces so a part releases from a mould.", lambda: F_("draft"), "taper"),
            C("shell", "Shell", "Hollow a part, leaving walls of a thickness; chosen faces are left open.",
              lambda: F_("shell"), "hollow"),
            C("thicken", "Thicken", "Turn surfaces or faces into solids of a thickness (one side, the other, both "
              "or two thicknesses).", lambda: F_("thicken"), "surface solid wall skin"),
            C("enclose", "Enclose", "A solid from the space that surfaces, faces and planes close off between them.",
              lambda: F_("enclose"), "surface solid close fill volume"),
            C("fill", "Fill", "A surface patch over a closed loop of edges or sketch curves, just meeting or "
              "tangent to the surfaces around it, through any points you pick.", lambda: F_("fill"),
              "surface patch cap boundary hole"),
            C("constrained_surface", "Constrained surface", "A smooth surface fitted through points, or through a "
              "part's vertices (a point cloud or scan), within a tolerance.", lambda: F_("constrained_surface"),
              "surface fit points cloud scan"),
            C("rib", "Rib", "A wall of a thickness from sketch lines, grown out until it meets the part.",
              lambda: F_("rib"), "gusset stiffener web"),
            C("delete_part", "Delete part", "Remove parts from the model from this point on.",
              lambda: F_("delete_part"), "remove body"),
            C("helix", "Helix", "A helix curve around an axis, a circle or a cylinder — sweep a profile along it "
              "for springs.", lambda: F_("helix"), "spiral spring coil thread curve"),
            C("compound", "Compound", "Several solids as one part, sharing the faces where they touch (glue "
              "faces, as in SALOME) — a conformal assembly for meshing.", lambda: F_("compound"),
              "glue faces partition salome conformal shared mesh fea"),
            C("explode_part", "Explode", "Split a part into its solids (a compound back apart), or into its faces "
              "(surfaces) or edges (curves).", lambda: F_("explode_part"), "split separate solids faces edges salome"),
            C("bounding_box", "Bounding box", "A box part around parts, with optional clearance (stock size, "
              "packaging).", lambda: F_("bounding_box"), "stock envelope size"),
            C("hole", "Hole", "Custom holes or ISO clearance, tapped, counterbore and countersink holes at sketch "
              "points.", lambda: F_("hole"), "drill tap counterbore countersink screw"),
            C("thread", "Thread", "Model real ISO metric threads on holes and shafts.", lambda: F_("thread"),
              "screw bolt"),
            C("delete_face", "Delete face", "Remove faces from a part. Heal closes the gap by extending the "
              "surrounding faces, Cap covers it with a flat face, or leave the void open. Useful for imported "
              "parts: removes fillets, chamfers, bosses and holes.", lambda: F_("delete_face"),
              "defeature remove fillet chamfer protrusion step import heal cap"),
            C("move_face", "Move face", "Push or pull flat faces along their normal to resize a part.",
              lambda: F_("move_face"), "offset face resize push pull direct edit"),
            C("offset_surface", "Offset surface", "Make a new surface a set distance from selected faces (flat or "
              "curved). The result is a surface body you can use as a reference or delete faces from.",
              lambda: F_("offset_surface"), "offset face surface copy shell skin"),
            C("sm_base", "Sheet metal", "A flat sheet from a sketch with thickness, bend radius and K-factor.",
              lambda: F_("sm_base"), "bracket enclosure"),
            C("sm_flange", "Flange", "Bend the sheet up along edges.", lambda: F_("sm_flange"), "sheet metal bend"),
            C("sm_hem", "Hem", "Fold sheet metal edges right back (180°).", lambda: F_("sm_hem"), "sheet metal fold"),
            C("sm_jog", "Jog", "Step sheet metal up or down from an edge.", lambda: F_("sm_jog"),
              "sheet metal joggle offset step"),
            C("sm_tab", "Tab", "Add sheet in a panel's plane from a sketch.", lambda: F_("sm_tab"), "sheet metal extend"),
            C("sm_convert", "Convert to sheet metal", "Make a flat plate into sheet metal.", lambda: F_("sm_convert"),
              "sheet metal plate import"),
            C("plane", "Plane", "Make a datum plane: offset, through a point, line and angle, three points, mid "
              "plane, normal to a curve or tangent to a surface.", lambda: F_("plane"), "datum"),
            C("mirror", "Mirror", "Copy parts across a plane.", lambda: F_("mirror"), "symmetry"),
            C("linear_pattern", "Linear pattern", "Repeat parts along a direction.", lambda: F_("linear_pattern"),
              "array copies"),
            C("circular_pattern", "Circular pattern", "Repeat parts around an axis.",
              lambda: F_("circular_pattern"), "array copies polar"),
            C("curve_pattern", "Curve pattern", "Repeat parts, features or faces along a path, turning with it.",
              lambda: F_("curve_pattern"), "array copies path along curve"),
            C("point_pattern", "Point pattern", "A copy of parts, features or faces at each picked point.",
              lambda: F_("point_pattern"), "array copies points vertices locations"),
            C("boolean", "Boolean", "Add, subtract or intersect parts.", lambda: F_("boolean"),
              "union subtract intersect combine merge"),
            C("split", "Split", "Cut parts with planes, faces (flat or curved) or sketch curves; keep both sides or one.", lambda: F_("split"), "cut"),
            C("move", "Transform", "Move, rotate, scale or copy parts: by XYZ, along a line, a distance, point to "
              "point, or one mate connector onto another.", lambda: F_("move"), "translate move rotate scale copy"),
            C("mate_connector", "Mate connector", "A frame (origin and X / Y / Z) on a face, edge or vertex; Shift "
              "moves it between the points of the face.", lambda: F_("mate_connector"), "frame origin connector"),
            C("variable", "Variable", "A #name to use in any dimension: a length, angle, number or expression "
              "with its unit, or measured from the model (distance, area, volume…) so it follows the model.",
              lambda: F_("measure_value"), "parameter measure value distance area volume angle unit"),
            C("derived", "Derived", "Bring parts from another Part Studio, kept linked.", lambda: F_("derived"),
              "link reference copy other studio"),
            C("configs", "Configurations", "Sizes or versions of this Part Studio from a table of variable values.",
              self.edit_configs, "configuration family table sizes variants"),
            C("import", "Import STEP", "Open a STEP file in a new tab.", lambda: self.import_step(), "open file",
              "File"),
            C("export", "Export", "Export the model as STEP or STL.", lambda: self.export(None, None),
              "save stl step", "File"),
            C("undo", "Undo", "Undo the last change (Ctrl+Z).", self.undo, "", "Edit"),
            C("redo", "Redo", "Redo what was undone (Ctrl+Y).", self.redo, "", "Edit"),
            C("section", "Section view", "Cut the model open with a plane to see inside (Ctrl+Shift+X).",
              self.toggle_section, "cross", "View"),
            C("fit", "Zoom to fit", "Fit the whole model in the view (F).", lambda: self.viewport.fit(), "", "View"),
            C("iso", "Isometric view", "Look at the model from the isometric corner (Shift+7).",
              lambda: self.viewport.set_standard_view("Isometric"), "", "View"),
        ]
        for i, name in enumerate(("Front", "Back", "Left", "Right", "Top", "Bottom"), 1):
            cmds.append(C("iso", f"{name} view", f"Look at the model from the {name.lower()} (Shift+{i}).",
                          lambda n=name: self.viewport.set_standard_view(n), "", "View"))
        for name in ("Dimetric", "Trimetric"):
            cmds.append(C("iso", f"{name} view", f"Look at the model from the {name.lower()} corner.",
                          lambda n=name: self.viewport.set_standard_view(n), "", "View"))
        cmds += [
            C("iso", "Previous view", "Go back to the view before (Alt+Left).", lambda: self.viewport.previous_view(),
              "back undo view", "View"),
            C("fit", "Zoom to window", "Drag a box around the area to fill the view with (Shift+Z).",
              lambda: self.viewport.start_zoom_window(), "zoom box area", "View"),
            C("iso", "Named views", "Save the view under a name, or go back to a saved one.",
              self.named_views_dialog, "saved views camera", "View"),
            C("iso", "Perspective view", "Switch between perspective and parallel projection.",
              lambda: self.set_display("perspective", not self.viewport.cam.perspective), "projection", "View"),
            C("iso", "Graphics preferences", "Translucency, mesh quality and other display settings.",
              self.graphics_preferences, "display settings render", "View"),
        ]
        for key, label in RENDER_MODES:
            cmds.append(C("iso", label, f"Draw the parts {label.lower()}.",
                          lambda k=key: self.set_display("render_mode", k), "render display mode", "View"))
        return cmds

    def open_command_search(self):
        if self.is_asm or self.is_dwg or self.mode != "idle":
            self.status.showMessage("Search tools works in a Part Studio while no tool is open — finish or cancel "
                                    "the current one first.", 5000)
            return
        if getattr(self, "_palette", None) is None:
            from .palette import CommandPalette
            self._palette = CommandPalette(self.viewport, self._palette_commands)
        self._palette.open()

    def open_s_menu(self):
        """S: a grid of your favourite tools for where you are (sketch, Part Studio, assembly, drawing) at the
        mouse."""
        if self.main_stack.currentWidget() is getattr(self, "home", None):
            return                                         # on the Home page: no tools to offer
        from .shortcut_menu import ShortcutPopup, current_context
        self._s_popup = ShortcutPopup(self, current_context(self))
        self._s_popup.popup_at()

    def customize_s_menu(self):
        from .shortcut_menu import CustomizeDialog, current_context
        CustomizeDialog(self, current_context(self)).exec()

    SELECT_DEFAULTS = {"vertex": True, "edge": True, "face": True, "part": False, "sketch": True, "plane": True}

    def _selection_filter_changed(self, _checked=None):
        kinds = set()
        for a in self.sel_actions.values():
            if a.isChecked():
                kinds.update(a.data())
        vp = self.viewport
        is_default = all(a.isChecked() == self.SELECT_DEFAULTS[k] for k, a in self.sel_actions.items())
        vp.user_filter = None if is_default else kinds
        self.sel_btn.setText(tr("Select") if is_default else "Select •")
        # drop anything already selected that can no longer be selected
        managed = {k for a in self.sel_actions.values() for k in a.data()}
        keep = [pk for pk in vp.selection if pk.kind not in managed or vp._allowed(pk.kind)]
        if len(keep) != len(vp.selection):
            vp.set_selection(keep)
        vp.hover = None
        vp.update()

    def _reset_selection_filter(self):
        for k, a in self.sel_actions.items():
            a.blockSignals(True)
            a.setChecked(self.SELECT_DEFAULTS[k])
            a.blockSignals(False)
        self._selection_filter_changed()

    def _set_language_preference(self, language):
        self.settings.setValue("language", language)
        self.settings.sync()
        message = ("FREESHAPE_LANG overrides the saved language preference."
                   if os.environ.get("FREESHAPE_LANG") else
                   "Restart FreeShape to apply the selected language.")
        QMessageBox.information(self, tr("Language"), tr(message))

    def _add_action(self, menu, text, slot, shortcut=None):
        a = QAction(tr(text), self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
            self.addAction(a)
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    def _tb(self, layout, name, tip, slot, checkable=False):
        b = QToolButton()
        b.setIcon(icon(name))
        b.setProperty("icon_name", name)
        b.setIconSize(QSize(26, 26))
        b.setToolTip(tr(tip))
        b.setCheckable(checkable)
        b.setAutoRaise(True)
        b.clicked.connect(slot)
        layout.addWidget(b)
        return b

    def _tb_group(self, layout, items):
        """A toolbar button with a ▾ menu, like Onshape's: the button runs the first tool, the arrow lists them all.
        items: (icon name, tooltip, slot). Returns the button."""
        name0, tip0, slot0 = items[0]
        b = self._tb(layout, name0, tip0, slot0)
        b.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(b)
        for name, tip, slot in items:
            label = tr(tip).split(" — ")[0]
            a = m.addAction(icon(name, 20), label)
            a.setToolTip(tr(tip))
            a.setProperty("icon_name", name)
            a.triggered.connect(lambda _=False, sl=slot: sl())
            self._menu_actions.append((a, name))
        m.setToolTipsVisible(True)
        b.setMenu(m)
        b.setProperty("group", [n for n, _t, _s in items])
        return b

    def _build_feature_toolbar(self):
        """Part Studio tools in Onshape's order: Sketch first (with its name), then features in groups, with
        related tools under a ▾ (patterns, direct edits, sheet metal …)."""
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        self._menu_actions = getattr(self, "_menu_actions", [])
        F_ = lambda n: (lambda: self.start_feature(n))      # noqa: E731
        self.btn_undo = self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self.btn_redo = self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        sk = self._tb(h, "sketch", "Sketch (Shift+S) — select a plane or flat face first, or pick one after",
                      lambda: self.start_sketch())
        sk.setText(tr("Sketch"))
        sk.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        sk.setObjectName("sketchButton")
        h.addWidget(_sep())
        # make solids and surfaces
        self._tb(h, "extrude", "Extrude (Shift+E)", F_("extrude"))
        self._tb(h, "revolve", "Revolve (Shift+W)", F_("revolve"))
        self._tb(h, "sweep", "Sweep — a profile along a path", F_("sweep"))
        self._tb(h, "loft", "Loft — blend between profiles", F_("loft"))
        self._tb_group(h, [("thicken", "Thicken — surfaces or faces into solids", F_("thicken")),
                           ("enclose", "Enclose — a solid from the space surfaces and planes close off",
                            F_("enclose"))])
        h.addWidget(_sep())
        # change them
        self._tb(h, "fillet", "Fillet (Shift+F)", F_("fillet"))
        self._tb(h, "chamfer", "Chamfer", F_("chamfer"))
        self._tb(h, "draft", "Draft — angle side faces", F_("draft"))
        self._tb(h, "rib", "Rib — a wall from sketch lines out to the part", F_("rib"))
        self._tb(h, "shell", "Shell", F_("shell"))
        self._tb_group(h, [("hole", "Hole — custom or ISO clearance / tapped", F_("hole")),
                           ("thread", "Thread — real ISO threads on holes and shafts", F_("thread"))])
        h.addWidget(_sep())
        # copy them
        self._tb_group(h, [("linear_pattern", "Linear pattern", F_("linear_pattern")),
                           ("circular_pattern", "Circular pattern", F_("circular_pattern")),
                           ("curve_pattern", "Curve pattern — copies along a path, turning with it",
                            F_("curve_pattern")),
                           ("point_pattern", "Point pattern — a copy at each picked point", F_("point_pattern"))])
        self._tb(h, "mirror", "Mirror", F_("mirror"))
        h.addWidget(_sep())
        # combine, cut up and move parts
        self._tb(h, "boolean", "Boolean — union, subtract or intersect parts", F_("boolean"))
        self._tb(h, "split", "Split — parts with planes, faces or sketch curves", F_("split"))
        self._tb_group(h, [("move", "Transform — by XYZ, a line, a distance, point to point, rotate, mate connectors "
                                    "or scale; move or copy parts", F_("move")),
                           ("mate_connector", "Mate connector — a frame on a face, edge or vertex (Shift: the next "
                                              "point on the face)", F_("mate_connector"))])
        self._tb(h, "delete_part", "Delete part — remove parts from here on", F_("delete_part"))
        self._tb_group(h, [("compound", "Compound — solids as one part that share the faces where they touch "
                                        "(glue faces)", F_("compound")),
                           ("explode_part", "Explode — a part into its solids (a compound back apart), faces or "
                                            "edges", F_("explode_part"))])
        self._tb_group(h, [("delete_face", "Delete face — remove fillets, chamfers, bosses and holes from imported "
                                           "parts (heal, cap or leave open)", F_("delete_face")),
                           ("move_face", "Move face — push or pull flat faces", F_("move_face"))])
        # surfaces, as Onshape groups them: Offset surface on the button, the rest under its ▾
        self._tb_group(h, [("offset_surface", "Offset surface — a new surface a set distance from faces (0: a copy)",
                            F_("offset_surface")),
                           ("fill", "Fill — a surface patch over a closed loop of edges or sketch curves",
                            F_("fill")),
                           ("constrained_surface", "Constrained surface — a surface fitted through points",
                            F_("constrained_surface"))])
        h.addWidget(_sep())
        # construction
        self._tb_group(h, [("plane", "Plane", F_("plane")),
                           ("helix", "Helix — a helix curve (a path for springs and threads)", F_("helix"))])
        h.addWidget(_sep())
        # sheet metal
        self._tb_group(h, [
            ("sm_base", "Sheet metal — a flat sheet from a sketch (thickness, bend radius, K-factor)", F_("sm_base")),
            ("sm_flange", "Flange — bend the sheet up along edges", F_("sm_flange")),
            ("sm_hem", "Hem — fold edges right back over the sheet", F_("sm_hem")),
            ("sm_jog", "Jog — step the sheet up or down from an edge and carry on", F_("sm_jog")),
            ("sm_tab", "Tab — more sheet in a panel's plane, from a sketch on its face", F_("sm_tab")),
            ("sm_convert", "Convert — make a flat plate (an imported one, say) into sheet metal", F_("sm_convert")),
            ("flat_dxf", "Export flat pattern DXF — for laser / plasma cutting", lambda: self.export_flat_dxf()),
            ("nest", "Nest for cutting — lay several flat patterns out on stock sheets, one DXF",
             self.nest_for_cutting)])
        self.btn_flat = self._tb(h, "flat", "Flat pattern — show sheet metal parts unfolded", self.toggle_flat,
                                 checkable=True)
        h.addWidget(_sep())
        # the Part Studio itself: variables, configurations, linked parts, history, plugins
        self._tb_group(h, [("variable", "Variable — a #name for any dimension: a length, angle, number or text, or "
                            "measured from the model (it follows the model)", F_("measure_value")),
                           ("variables_table", "Variables table — every variable, its value and what uses it; "
                            "change values and names in place", self.show_variables)])
        self._tb(h, "configs", "Configurations — sizes / versions of this part from a table of variable values",
                 self.edit_configs)
        self._tb(h, "derived", "Derived — bring parts from another Part Studio, kept linked", F_("derived"))
        self._tb(h, "history", "Version history — versions of this Part Studio and what changed between them",
                 self.show_history)
        self.plugin_btn = self._tb(h, "plugin", "Plugins — features and commands added by Python plugins",
                                   lambda: None)
        self.plugin_btn.setPopupMode(QToolButton.InstantPopup)
        pm = QMenu(self.plugin_btn)
        from . import plugin_ui
        pm.aboutToShow.connect(lambda m=pm: plugin_ui.fill_menu(m, self))
        self.plugin_btn.setMenu(pm)
        self.config_box = QComboBox()
        self.config_box.setToolTip(tr("The configuration shown"))
        self.config_box.setMinimumWidth(110)
        self.config_box.activated.connect(lambda i: self.doc.set_active_config(i) if self.doc is not None and
                                          hasattr(self.doc, "set_active_config") else None)
        self.config_box.hide()
        h.addWidget(self.config_box)
        h.addWidget(_sep())
        self._tb(h, "import", "Import STEP (opens in a new tab)", lambda: self.import_step())
        self._tb(h, "export", "Export STEP / STL / BREP", lambda: self.export(None, None))
        h.addStretch(1)
        self.btn_section = self._tb(h, "section", "Section view (Ctrl+Shift+X) — select a face or plane first to cut there",
                                    self.toggle_section, checkable=True)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: self.viewport.fit())
        self._tb(h, "iso", "Isometric (Shift+7)", lambda: self.viewport.set_standard_view("Isometric"))
        return w

    def _build_assembly_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        a = self.asm
        self.btn_aundo = self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self.btn_aredo = self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        self._tb(h, "insert", "Insert — add parts from your Part Studios or a file (I)", a.toggle_insert)
        h.addWidget(_sep())
        from ..assembly import MATE_TYPES
        for t, (label, tip) in MATE_TYPES.items():
            key = {"fastened": " (Shift+F)", "revolute": " (Shift+R)", "slider": " (Shift+L)"}.get(t, "")
            self._tb(h, "m_" + t, f"{label} mate{key} — {tip}", lambda _=False, tt=t: a.start_mate(tt))
        h.addWidget(_sep())
        self._tb(h, "move", "Move / rotate the selected parts by exact amounts", lambda: a.start_move())
        self._tb(h, "fix", "Fix / unfix the selected parts", self._asm_toggle_fix)
        h.addWidget(_sep())
        self.btn_interf = self._tb(h, "interference", "Interference check — show where parts overlap",
                                   lambda: a.toggle_interference(), checkable=True)
        self._tb(h, "relation", "Relation — gear, rack and pinion, or screw: link mates' motions", a.relation_dialog)
        self._tb(h, "linear_pattern", "Linear pattern of the selected parts", lambda: a.pattern_dialog("linear"))
        self._tb(h, "circular_pattern", "Circular pattern of the selected parts", lambda: a.pattern_dialog("circular"))
        self._tb(h, "bom", "Bill of materials", a.show_bom)
        self._tb(h, "explode", "Exploded view — slide the parts apart for pictures and drawings", a.toggle_explode)
        self._tb(h, "export", "Export the assembly (STEP keeps the parts, names and colours)",
                 lambda: self.export(None, None))
        h.addStretch(1)
        self.btn_asection = self._tb(h, "section", "Section view (Ctrl+Shift+X)", self.toggle_section, checkable=True)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: self.viewport.fit())
        self._tb(h, "iso", "Isometric (Shift+7)", lambda: self.viewport.set_standard_view("Isometric"))
        return w

    def _build_drawing_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        d = self.dwg
        self._tb(h, "undo", "Undo (Ctrl+Z)", self.undo)
        self._tb(h, "redo", "Redo (Ctrl+Y)", self.redo)
        h.addWidget(_sep())
        self._tb(h, "base_view", "Insert view — front, top, side, isometric …", d.insert_view)
        self._dwg_buttons = {}
        self._dwg_buttons["projected"] = self._tb(h, "proj_view", "Projected view — pick a view, then click beside "
                                                  "it (off a corner for an isometric)", lambda: d.set_tool("projected"),
                                                  checkable=True)
        self._dwg_buttons["section"] = self._tb(h, "section_view", "Section view — draw the cutting line across a "
                                                "view, then place the section", lambda: d.set_tool("section"),
                                                checkable=True)
        self._dwg_buttons["detail"] = self._tb(h, "detail_view", "Detail view — circle an area to show it larger",
                                               lambda: d.set_tool("detail"), checkable=True)
        h.addWidget(_sep())
        self._dwg_buttons["dim"] = self._tb(h, "dim", "Dimension (D) — click an edge, a circle, or two points, then "
                                            "where it goes", lambda: d.set_tool("dim"), checkable=True)
        self._dwg_buttons["note"] = self._tb(h, "note", "Note (N)", lambda: d.set_tool("note"), checkable=True)
        h.addWidget(_sep())
        self._tb(h, "bom", "Parts list — the bill of materials as a table on the sheet", d.add_parts_list)
        self._dwg_buttons["balloon"] = self._tb(h, "balloon", "Balloon — click a part, then where its number goes",
                                                lambda: d.set_tool("balloon"), checkable=True)
        self._tb(h, "auto_balloon", "Auto balloon — number every part in the selected view", lambda: d.auto_balloon())
        h.addWidget(_sep())
        self._tb(h, "sheet", "Sheet size, scale, projection and title block", d.sheet_setup)
        h.addWidget(_sep())
        self._tb(h, "dxf", "Export DXF — opens in AutoCAD", lambda: d.export("dxf"))
        self._tb(h, "pdf", "Export PDF", lambda: d.export("pdf"))
        h.addStretch(1)
        self._tb(h, "fit", "Zoom to fit (F)", lambda: d.canvas.fit())
        return w

    def dwg_tool_changed(self, tool):
        for name, b in getattr(self, "_dwg_buttons", {}).items():
            b.setChecked(name == tool)

    @property
    def is_dwg(self):
        return getattr(self.doc, "kind", "studio") == "drawing"

    def _asm_toggle_fix(self):
        ids = self.asm.selected_instance_ids()
        if not ids:
            self.status.showMessage(tr("Select a part first (in the list or the graphics area)"), 4000)
            return
        fix = not all(self.doc.instance(i).get("fixed") for i in ids)
        self.doc.checkpoint()
        for i in ids:
            self.doc.instance(i)["fixed"] = fix
        self.doc.solve()
        self.doc.notify()

    @property
    def is_asm(self):
        return getattr(self.doc, "kind", "studio") == "assembly"

    def _build_sketch_toolbar(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(8, 2, 8, 2)
        h.setSpacing(2)
        self._tb(h, "undo", "Undo (Ctrl+Z)", lambda: self._sk("undo"))
        self._tb(h, "redo", "Redo (Ctrl+Y)", lambda: self._sk("redo"))
        h.addWidget(_sep())
        b = self._tb(h, "point", "Select (Esc)", lambda: self._sk_tool("select"), checkable=True)
        b.setIcon(self._arrow_icon())
        b.setProperty("icon_name", "__arrow")
        self._sketch_buttons["select"] = b
        # like Onshape, variants of a tool share a button: it shows (and repeats) the one used last, ▾ lists them
        self._sk_group(h, [("line", "Line (L)"),
                           ("line_mid", "Midpoint line — click the middle, then one end")])
        self._sk_group(h, [("rect", "Corner rectangle (R)"), ("center_rect", "Center rectangle"),
                           ("rect3", "Aligned rectangle — two corners for one side, then the width")])
        self._sk_group(h, [("polygon", "Inscribed polygon (Shift+X) — corners on a circle; type the number of "
                                       "sides (6 to start: a hexagon)"),
                           ("polygon_out", "Circumscribed polygon — sides touch a circle (size across flats); "
                                           "type the number of sides")])
        self._sk_group(h, [("circle", "Circle (C)"), ("ellipse", "Ellipse")])
        self._sk_group(h, [("arc3", "3-point arc (A)"), ("arc_center", "Center-point arc"),
                           ("arc_tan", "Tangent arc (Shift+A) — carry on smoothly from the end of a line or arc"),
                           ("ellipse_arc", "Elliptical arc — centre, long axis, width, then start and end"),
                           ("conic", "Conic — start, end, shoulder point, then rho")])
        self._sk_group(h, [("spline", "Spline — through the points you click; double-click or Enter to finish"),
                           ("spline_cp", "Control-point spline — the points pull the curve (Bézier style)")])
        self._sketch_buttons["point"] = self._tb(h, "point", "Point (P or Shift+S)", lambda: self._sk_tool("point"),
                                                 checkable=True)
        self._sketch_buttons["text"] = self._tb(h, "sk_text", "Text", lambda: self._sk_tool("text"), checkable=True)
        h.addWidget(_sep())
        b = self._tb(h, "construction", "Construction (Q)", lambda: self._sk("toggle_construction"), checkable=True)
        self._sketch_buttons["construction"] = b
        b = self._tb(h, "dimension", "Dimension (D)", lambda: self._sk_tool("dimension"), checkable=True)
        self._sketch_buttons["dimension"] = b
        h.addWidget(_sep())
        for name in ("coincident", "horizontal", "vertical", "parallel", "perpendicular", "tangent",
                     "equal", "midpoint", "concentric", "symmetric", "fix"):
            label = name.capitalize()
            keys = {"coincident": " (I)", "horizontal": " (H)", "vertical": " (V)", "equal": " (E)", "tangent": " (T)",
                    "parallel": " (B)", "perpendicular": " (Shift+L)", "concentric": " (Shift+O)",
                    "midpoint": " (Shift+M)", "symmetric": " (Shift+Q)", "fix": " (Shift+J)"}
            b = self._tb(h, name, tr(label) + keys.get(name, "") + " — " + tr(CONSTRAINT_TOOLS[name]),
                         lambda _=False, n=name: self._sk_tool(n), checkable=True)
            self._sketch_buttons[name] = b
        h.addWidget(_sep())
        self._sk_group(h, [("sk_fillet", "Sketch fillet (Shift+F) — round a corner"),
                           ("sk_chamfer", "Sketch chamfer — cut a corner straight")])
        self._sk_group(h, [("offset", "Offset (O)"), ("slot", "Slot — two end centres, then the width")])
        self._sketch_buttons["sk_mirror"] = self._tb(h, "sk_mirror", "Mirror — select geometry, then the mirror line",
                                                     lambda: self._sk_tool("sk_mirror"), checkable=True)
        self._sk_group(h, [("sk_transform", "Transform — select geometry, then move and turn it"),
                           ("sk_copy", "Copy — select geometry, then place a moved / turned copy")])
        self._sk_group(h, [("sk_lpattern", "Linear pattern — select geometry, then a direction line"),
                           ("sk_cpattern", "Circular pattern — select geometry, then the centre point")])
        h.addWidget(_sep())
        self._sk_group(h, [("use", "Use (project) part edges (U)"),
                           ("use_intersect", "Intersect — where part faces cross the sketch plane")])
        self._sk_group(h, [("trim", "Trim (M)"), ("sk_split", "Split — cut a line or arc in two where you click"),
                           ("sk_extend", "Extend (X) — lengthen a line or arc to the next curve")])
        self._tb(h, "delete", "Delete selected (Del)", lambda: self._sk("delete_selection"))
        self._tb_group(h, [("insert_dxf", "Insert DXF — bring a drawing's lines, arcs and curves into the sketch",
                            self._sketch_insert_dxf),
                           ("insert_image", "Insert image — a picture to trace over", self._sketch_insert_image)])
        h.addStretch(1)
        self._tb(h, "normal_to", "Normal to sketch (N)", self._normal_to_sketch)
        self._tb(h, "fit", "Zoom to fit (F, or double-click the middle button)", lambda: self.viewport.fit())
        h.addWidget(_sep())
        # straight on to 3D: finish the sketch and start the feature with its regions already picked
        self._tb_group(h, [(f, tip, lambda _=False, f=f: self._feature_from_sketch(f)) for f, tip in (
            ("extrude", "Extrude this sketch (Shift+E)"), ("revolve", "Revolve this sketch (Shift+W)"),
            ("sweep", "Sweep this sketch — then pick the path"))])
        done = QToolButton()
        done.setText(tr("✓  Finish sketch"))
        done.setStyleSheet("QToolButton { background:%s; color:white; font-weight:600; padding:5px 12px; border-radius:4px; }"
                           "QToolButton:hover { background:%s; }" % (theme.ACCENT, theme.ACCENT_DARK))
        done.clicked.connect(self.finish_sketch)
        h.addWidget(done)
        return w

    def _sketch_insert_dxf(self):
        if not self.sketcher:
            return
        path, _ = filedlg.getOpenFileName(self, "Insert DXF into the sketch", "", "DXF drawings (*.dxf)")
        if not path:
            return
        try:
            rep = self.sketcher.insert_dxf(path)
        except Exception as err:
            QMessageBox.warning(self, APP_NAME, f"Couldn't read that DXF: {err}")
            return
        skipped = ", ".join(f"{n} {t.lower()}" for t, n in sorted(rep["skipped"].items()))
        self.status.showMessage(f"Inserted {rep['added']} item(s) from {os.path.basename(path)}"
                                + (f" (skipped {skipped})" if skipped else ""), 8000)
        self.viewport.fit()

    def _sketch_insert_image(self):
        if not self.sketcher:
            return
        path, _ = filedlg.getOpenFileName(self, "Insert image into the sketch", "",
                                          "Pictures (*.png *.jpg *.jpeg *.bmp *.gif *.webp)")
        if not path:
            return
        try:
            self.sketcher.insert_image(path)
        except ValueError as err:
            QMessageBox.warning(self, APP_NAME, str(err))
            return
        self.status.showMessage("Image inserted, 100 mm wide: dimension between its bottom corners to set its "
                                "size", 8000)
        self.viewport.fit()

    def _sk_group(self, layout, items):
        """Sketch tools that share one button (rectangle / centre rectangle / hexagon …): the button shows the
        variant used last and clicking it starts that one again; ▾ lists them all."""
        icon_of = {"text": "sk_text"}
        b = self._tb(layout, icon_of.get(items[0][0], items[0][0]), items[0][1],
                     lambda: self._sk_tool(b.property("current")), checkable=True)
        b.setProperty("current", items[0][0])
        b.setPopupMode(QToolButton.MenuButtonPopup)
        m = QMenu(b)
        for name, tip in items:
            a = m.addAction(icon(icon_of.get(name, name), 20), tr(tip).split(" — ")[0])
            a.setToolTip(tr(tip))
            a.triggered.connect(lambda _=False, n=name: self._sk_tool(n))
            self._menu_actions.append((a, icon_of.get(name, name)))
            self._sketch_buttons[name] = b
        m.setToolTipsVisible(True)
        b.setMenu(m)
        b.setProperty("tips", {n: t for n, t in items})
        return b

    def _arrow_icon(self):
        from PySide6.QtGui import QPixmap, QPainter, QPolygonF, QIcon
        from PySide6.QtCore import QPointF
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(2, 2)
        p.setBrush(QColor(theme.ICON_INK))
        p.setPen(Qt.NoPen)
        p.drawPolygon(QPolygonF([QPointF(10, 5), QPointF(10, 25), QPointF(15, 20), QPointF(19, 28),
                                 QPointF(22, 26.5), QPointF(18, 19), QPointF(24, 19)]))
        p.end()
        pm.setDevicePixelRatio(2)
        return QIcon(pm)

    def _bind_shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
            return s
        sc("Alt+C", self.open_command_search)
        sc("Ctrl+Space", self.open_command_search)
        sc("Alt+`", self.open_command_search)
        sc("Ctrl+Z", self.undo)
        sc("Ctrl+C", self.copy_features)
        sc("Ctrl+V", self.paste_features)
        sc("Ctrl+Y", self.redo)
        sc("Ctrl+Shift+Z", self.redo)
        sc("Shift+S", lambda: self._sk_key("Shift+S") or (
            self.start_sketch() if self.mode == "idle" and not self.is_dwg else None))
        sc("Shift+E", lambda: self._feature_from_sketch("extrude"))
        sc("Shift+W", lambda: self._feature_from_sketch("revolve"))
        sc("Shift+F", lambda: self._sk_key("Shift+F") or (
            self.asm.start_mate("fastened") if self.is_asm else self.start_feature("fillet")))
        # ---- Onshape's 3D view keys (in a sketch, letters the sketch uses go to its tools instead)
        sc("P", lambda: self._sk_key("P") or (self.dwg.set_tool("projected") if self.is_dwg
                                               else self.act_planes.trigger() if not self.is_asm else None))
        sc("Shift+P", lambda: self._sk_key("Shift+P") or self.toggle_construction_geometry())
        sc("Shift+H", lambda: self._sk_key("Shift+H") or self.toggle_sketches())
        sc("Y", lambda: self._sk_key("Y") or self.hide_selected_parts())
        sc("Shift+Y", lambda: None if self.is_dwg or self.is_asm else self._show_all_parts())
        sc("Shift+I", lambda: self._sk_key("Shift+I") or self.isolate_selected_parts())
        sc("Shift+V", self.named_views_dialog)
        sc("Shift+X", lambda: self._sk_key("Shift+X") or (
            self.toggle_section() if not self.is_dwg and not self.is_asm else None))
        sc("Shift+R", lambda: (self.asm.start_mate("revolute") if self.mode == "idle" else None) if self.is_asm
           else self.set_display("high_quality", not self.viewport.high_quality))
        sc("Z", lambda: self.viewport.zoom_step(1.4))
        sc("W", lambda: self.viewport.start_zoom_window())
        sc("Shift+N", self.rename_selected)
        sc("?", self.show_shortcuts)
        sc("Shift+?", self.show_shortcuts)
        sc("`", self.select_other_at_cursor)
        sc("M", lambda: self._sk_key("M") or (self.asm.start_mate("fastened") if self.is_asm else None))
        sc("B", lambda: self._sk_key("B"))
        sc("X", lambda: self._sk_key("X"))

        sc("N", lambda: self.dwg.set_tool("note") if self.is_dwg else self._normal_key())
        sc("S", self.open_s_menu)
        sc("Delete", self._delete_key)
        sc("Escape", self._escape)

    # ------------------------------------------------------------------ keys (Onshape's set)
    def _sk_key(self, seq: str) -> bool:
        """In a sketch, a key the sketch uses picks its tool (P point, M trim, Shift+F fillet …). True if it did."""
        if self.mode != "sketch" or not self.sketcher:
            return False
        from .sketcher import Sketcher
        mods = Qt.ShiftModifier if seq.startswith("Shift+") else Qt.NoModifier
        tool = Sketcher.key_tool(ord(seq[-1]), mods)
        if tool:
            self._sk_tool(tool)
        return True                                      # in a sketch, never the 3D meaning

    def _target_parts(self):
        """Parts a key acts on: the selected ones, else the one under the mouse."""
        vp = self.viewport
        ids = []
        for pk in vp.selection:
            if pk.kind in ("face", "edge", "part", "vertex") and pk.key[0] in self.doc.state.parts \
                    and pk.key[0] not in ids:
                ids.append(pk.key[0])
        if not ids and vp.hover is not None and vp.hover.kind in ("face", "edge", "part", "vertex") \
                and vp.hover.key[0] in self.doc.state.parts:
            ids = [vp.hover.key[0]]
        return ids

    def hide_selected_parts(self):
        """Y: hide the selected parts (or the one under the mouse)."""
        if self.is_dwg or self.is_asm or self.mode == "sketch":
            return
        ids = self._target_parts()
        if ids:
            self.viewport.clear_selection()
            self.doc.set_parts_hidden(ids, True)

    def isolate_selected_parts(self):
        """Shift+I: show only the selected parts (or the one under the mouse); Shift+Y shows the rest again."""
        if self.is_dwg or self.is_asm or self.mode == "sketch":
            return
        ids = self._target_parts()
        if ids:
            self._isolate(ids)

    def _construction_ids(self, kinds=("sketch", "plane")):
        if self.is_dwg or self.is_asm or self.doc is None:
            return []
        return [f["id"] for f in self.doc.features if f.get("type") in kinds]

    def toggle_sketches(self):
        """Shift+H: hide every sketch, or show them all again if they're all hidden."""
        fids = self._construction_ids(("sketch",))
        if fids:
            shown = any(f not in self.doc.hidden_features for f in fids)
            self.doc.set_features_hidden(fids, shown)

    def toggle_construction_geometry(self):
        """Shift+P: hide all construction geometry — the default planes, your planes and sketches — or show it all
        again if it's all hidden."""
        if self.is_dwg or self.is_asm:
            return
        fids = list(STD_PLANES) + self._construction_ids()
        shown = any(f not in self.doc.hidden_features for f in fids)
        self.doc.set_features_hidden(fids, shown)

    def rename_selected(self):
        """Shift+N: rename the feature selected in the list, else the selected part."""
        if self.is_dwg or self.is_asm or self.mode != "idle":
            return
        fids = self.left.features.selected_feature_ids()
        if len(fids) == 1:
            self._rename_feature(fids[0])
            return
        ids = self._target_parts()
        if len(ids) == 1:
            self._rename_part(ids[0])

    # ------------------------------------------------------------------ Select other…, selection tools
    def _pick_label(self, pk) -> str:
        """A readable name for anything that can be picked, for Select other…"""
        st = self.doc.state if hasattr(self.doc, "state") else None
        vp = self.viewport

        def part_name(pid):
            p = st.parts.get(pid) if st else None
            return p.name if p else str(pid)
        try:
            if pk.kind in ("face", "edge", "vertex"):
                shape = vp.part_shapes.get(pk.key[0])
                where = part_name(pk.key[0])
                if pk.kind == "face":
                    info = K.face_info(K.faces(shape)[pk.key[1]])
                    kind = {0: "flat", 1: f"cylinder Ø{U.num(2 * info.get('r', 0))}", 2: "cone", 3: "sphere",
                            4: "torus"}.get(info.get("type"), "curved")
                    n = info.get("n")
                    if n is not None:                    # which way a flat face looks, to tell them apart
                        n = np.asarray(n, float)
                        i = int(np.argmax(np.abs(n)))
                        kind += (f", facing {'-' if n[i] < 0 else '+'}{'XYZ'[i]}" if abs(n[i]) > 0.999
                                 else f", facing ({n[0]:.2f}, {n[1]:.2f}, {n[2]:.2f})")
                    return f"Face — {kind} — {where}"
                if pk.kind == "edge":
                    info = K.edge_info(K.edges(shape)[pk.key[1]])
                    kind = {0: f"line {U.length(info['len'])}", 1: f"circle Ø{U.num(2 * info.get('r', 0))}"}.get(
                        info.get("type"), "curve")
                    return f"Edge — {kind} — {where}"
                return f"Vertex — {where}"
            if pk.kind == "part":
                return f"Part — {part_name(pk.key[0])}"
            if pk.kind == "plane":
                if pk.key[0] == "std":
                    return f"Plane — {pk.key[1]}"
                f = self.doc.feature(pk.key[1])
                return f"Plane — {f['name'] if f else pk.key[1]}"
            if pk.kind in ("region", "sketch_line", "sketch_point"):
                f = self.doc.feature(pk.key[0])
                what = {"region": "area", "sketch_line": "curve", "sketch_point": "point"}[pk.kind]
                return f"{f['name'] if f else 'Sketch'} — {what}"
        except Exception:
            pass
        return pk.label()

    def _select_other_menu(self, x, y, parent=None):
        """The menu of everything under (x, y); hovering an entry highlights it, choosing it adds it to the
        selection. None if there is nothing (or only one thing) there."""
        vp = self.viewport
        items = vp.pick_all(x, y)
        if not items:
            return None
        m = QMenu(parent or self)
        m.setTitle(tr("Select other…"))
        acts = {}
        for pk in items:
            a = m.addAction(self._pick_label(pk))
            acts[id(a)] = pk

        def hovered(a):
            pk = acts.get(id(a))
            if pk is not None and pk != vp.hover:
                vp.hover = pk
                vp.hover_xray = True
                vp.hoverChanged.emit(pk)
                vp.update()

        def chosen(a):
            pk = acts.get(id(a))
            if pk is not None and pk not in vp.selection:
                vp.set_selection(list(vp.selection) + [pk])
        m.hovered.connect(hovered)
        m.triggered.connect(chosen)
        m.aboutToHide.connect(vp.set_hover_none)
        return m

    def select_other_at_cursor(self):
        """` (back quote), as Onshape: list everything selectable under the mouse."""
        if self.mode == "sketch" or self.is_dwg:
            return
        xy = getattr(self.viewport, "_cursor_xy", None)
        if xy is None:
            return
        m = self._select_other_menu(*xy)
        if m is not None:
            m.exec(self.viewport.mapToGlobal(QPoint(int(xy[0]), int(xy[1]))))

    def _add_selection_tools(self, m, pk):
        """Right-click selection tools on a face or edge, as in Onshape: tangent-connected, loops, same size."""
        from .. import selection_tools as ST
        shape = self.viewport.part_shapes.get(pk.key[0])
        if shape is None:
            return
        pid, i = pk.key

        def add(kind, ids):
            vp = self.viewport
            sel = list(vp.selection)
            for k in ids:
                q = Pick(kind, (pid, int(k)))
                if q not in sel:
                    sel.append(q)
            vp.set_selection(sel)
        if pk.kind == "face":
            m.addAction(tr("Select tangent-connected faces"), lambda: add("face", ST.tangent_faces(shape, i)))
            m.addAction(tr("Select adjacent faces"), lambda: add("face", ST.connected_faces(shape, i)))
            m.addAction(tr("Select faces of the same size"), lambda: add("face", ST.same_faces(shape, i)))
            m.addAction(tr("Select edges of this face"), lambda: add("edge", ST.face_edges(shape, i)))
        elif pk.kind == "edge":
            m.addAction(tr("Select tangent-connected edges"), lambda: add("edge", ST.tangent_edges(shape, i)))
            m.addAction(tr("Select loop"), lambda: add("edge", ST.edge_loop(shape, i)))
            m.addAction(tr("Select edges of the same size"), lambda: add("edge", ST.same_edges(shape, i)))

    # ================================================================== doc → view
    def _on_doc_changed(self):
        if self.is_dwg:
            self.dwg.on_changed()
            name = self.doc.name
            self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
            self.setWindowTitle(f"{name} — {APP_NAME}")
            self._refresh_tab_title(self.doc)
            return
        if self.is_asm:
            self.asm.on_changed()
            self.btn_aundo.setEnabled(self.doc.can_undo())
            self.btn_aredo.setEnabled(self.doc.can_redo())
            name = self.doc.name
            self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
            self.setWindowTitle(f"{name} — {APP_NAME}")
            self._refresh_tab_title(self.doc)
            return
        self._populate_lists()
        shown = getattr(self, "_interference_state", None)
        if shown is not None and shown is not self.doc.state:
            # the model changed since Check interference: its red overlaps no longer describe it
            self._interference_state = None
            self.viewport.ghosts = []
        if self.mode == "idle":
            self._show_state(self.doc.state)
        elif self.mode == "dialog" and self.dialog is not None:
            # e.g. a part hidden from the parts list while a feature dialog is open: redraw the preview so the
            # hidden part leaves the view (and stops catching clicks meant for what is behind it)
            self.dialog.schedule_preview()
        elif self.mode == "sketch" and self.sk_ctx and self.sketcher:
            self._sketch_changed(True)
        self._update_feature_hl()
        self.btn_undo.setEnabled(self.doc.can_undo())
        self.btn_redo.setEnabled(self.doc.can_redo())
        name = self.doc.name
        self.doc_label.setText(name + ("  •" if self.doc.dirty else ""))
        self.setWindowTitle(f"{name} — {APP_NAME}")
        self._refresh_tab_title(self.doc)
        n = len(self.doc.state.parts)
        self.left.parts_header.setText(f"Parts ({n})")
        self._refresh_config_box()
        self.left.feat_header.setText(f"Features ({len(self.doc.features)})")
        errs = len(self.doc.errors)
        if errs:
            self.status.showMessage(f"{errs} feature(s) failed to regenerate — hover them in the feature list for details")

    def _populate_lists(self):
        if self.is_dwg:
            self.dwg.on_changed()
            return
        if self.is_asm:
            self.asm.panel.populate(self.doc)
            return
        editing = None
        if self.mode == "dialog" and self.dialog and not self.dialog.is_new:
            editing = self.dialog.feature["id"]
        if self.mode == "sketch" and self.sk_ctx and not self.sk_ctx["is_new"]:
            editing = self.sk_ctx["feature"]["id"]
        self.left.features.populate(self.doc, editing)
        self.left.parts.populate(self.doc)
        self._sync_planes_action()

    def _planes_for(self, state):
        half = self.viewport.plane_half()
        planes = []
        for name in STD_PLANES:
            if name not in self.doc.hidden_features:
                planes.append((("std", name), K.std_plane(name), name,
                               self.doc.plane_rect(("std", name), half)))
        for fid, pl in state.planes.items():
            if fid not in self.doc.hidden_features:
                f = self.doc.feature(fid)
                planes.append((("plane", fid), pl, f["name"] if f else "",
                               self.doc.plane_rect(("plane", fid), half * 0.6)))
        return planes

    def _plane_resized(self, key, rect):
        """A plane's corner handle was dragged: keep its new size in the document (undoable, saved)."""
        if self.doc is not None and hasattr(self.doc, "set_plane_rect"):
            self.doc.set_plane_rect(key, rect)

    def _show_state(self, state, active_sketch=None, also_show=()):
        if self.is_dwg:
            return
        if self.is_asm:
            self.asm.show_scene()
            return
        parts = [(p.id, self._flat_or_folded(state, p), p.color,
                  None if self._flat_on(state, p) else p.face_colors)
                 for p in state.parts.values() if p.id not in self.doc.hidden_parts]
        sketches = []
        for fid, sr in state.sketches.items():
            if fid in self.doc.hidden_features and fid != active_sketch and fid not in also_show:
                continue
            sketches.append((fid, sr))
        self.viewport.set_scene(parts, self._planes_for(state), sketches)
        # mate connectors: small X / Y / Z triads (hidden ones and those past the rollback bar left out)
        self.viewport.triads = [dict(o=fr["o"], x=fr["x"], y=fr["y"], z=fr["z"], size_px=26, width=2.2)
                                for fid, fr in getattr(state, "connectors", {}).items()
                                if fid not in self.doc.hidden_features]

    # ================================================================== feature dialogs
    def start_feature(self, ftype, edit_fid=None):
        if self.is_asm or self.is_dwg:
            return
        if ftype == "derived":
            self._derived_dialog(edit_fid)
            return
        if self.mode != "idle":
            if self.mode == "dialog" and self.dialog and self.dialog.is_new and self.dialog.ftype != ftype \
                    and edit_fid is None:
                self.cancel_dialog()
            else:
                return
        if edit_fid:
            feat = copy.deepcopy(self.doc.feature(edit_fid))
            index = self.doc.feature_index(edit_fid)
            is_new = False
            pre = []
        else:
            feat = self.doc.make_feature(ftype)
            index = self.doc.insert_index()
            is_new = True
            pre = list(self.viewport.selection)
        self.mode = "dialog"
        self.viewport.feature_hl = self.viewport.feature_sel = None
        self.dialog = FeatureDialog(self, feat, is_new, index, pre)
        self._adapt_tree(self.dialog)
        self.dialog.accepted.connect(self.commit_dialog)
        self.dialog.rejected.connect(self.cancel_dialog)
        self.viewport.pick_handler = self.dialog.handle_pick
        self.measure.hide()
        self._populate_lists()

    def show_preview(self, feature, index, overlays):
        st, err = self.doc.preview(feature, index)
        p = feature["params"]
        # sketches a feature uses stay visible while it is being edited
        show = {r["sketch"] for r in p.get("regions", [])}
        if p.get("sketch") and isinstance(p.get("sketch"), str):
            show.add(p["sketch"])
        ax = p.get("axis") or {}
        if ax.get("kind") == "sketch_line":
            show.add(ax["sketch"])
        self._show_state(st, also_show=show)
        self.viewport.overlays = overlays
        self.viewport.update()
        return err

    def commit_dialog(self):
        d = self.dialog
        if d is None:
            return
        f = d.feature
        self._close_dialog()
        if d.is_new:
            # like Onshape, sketches consumed by a new feature are hidden to keep the view clean
            used = {r["sketch"] for r in f["params"].get("regions", [])}
            if f["type"] == "hole" and f["params"].get("sketch"):
                used.add(f["params"]["sketch"])
            for item in f["params"].get("profiles", []) if f["type"] == "loft" else []:
                if item.get("kind") == "region":
                    used.add(item["ref"]["sketch"])
            for r in f["params"].get("path", []) if f["type"] == "sweep" else []:
                if r.get("kind") == "sketch_chain":
                    used.add(r["sketch"])
            self.doc.hidden_features |= used
            self.doc.add_feature(f, d.index)
        else:
            self.doc.replace_feature(f)
        err = self.doc.errors.get(f["id"])
        self.status.showMessage(f"{f['name']}: {err}" if err else f"{f['name']} created" if d.is_new
                                else f"{f['name']} updated", 6000)

    def cancel_dialog(self):
        self._close_dialog()
        self._show_state(self.doc.state)

    def _close_dialog(self):
        if self.dialog:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.dialog = None
        self.mode = "idle"
        vp = self.viewport
        vp.pick_handler = None
        vp.select_filter = None
        vp.overlays = []
        vp.outlines = []
        vp.manipulators = []
        vp._manip_drag = None
        vp.clear_selection()
        self._populate_lists()

    def _history_ok(self):
        if self.is_asm or self.is_dwg or self.doc is None or not hasattr(self.doc, "versions"):
            QMessageBox.information(self, APP_NAME, "Version history is kept for Part Studios.")
            return False
        return True

    def show_history(self):
        p = getattr(self, "history_panel", None)
        if p is not None:
            p.close_panel()
            return
        if not self._history_ok():
            return
        if self.mode != "idle":
            self.status.showMessage("Finish the feature or sketch you're editing first", 5000)
            return
        from .history_ui import HistoryPanel
        self.history_panel = HistoryPanel(self)

    def create_version(self):
        if not self._history_ok():
            return
        from .history_ui import version_dialog
        from .. import history as H
        n = sum(1 for v in self.doc.versions if not v.get("auto")) + 1
        got = version_dialog(self, name=f"Version {n}")
        if got is None:
            return
        H.create(self.doc, got[0] or None, got[1])
        self.doc.dirty = True
        self._populate_lists()
        p = getattr(self, "history_panel", None)
        if p is not None:
            p.fill()
        self.status.showMessage(f"Version “{self.doc.versions[-1]['name']}” created — Version history shows "
                                "what changed since", 6000)

    def _after_plugin(self, rebuild=False):
        """After a plugin command / script ran, or plugins were reloaded: show what changed."""
        doc = self.doc
        if doc is None:
            return
        if rebuild and hasattr(doc, "features"):
            doc._cache.clear()
            doc.rebuild()
        if hasattr(doc, "notify"):
            doc.notify()
        self._populate_lists()

    def edit_feature(self, fid):
        f = self.doc.feature(fid)
        if f is None or self.mode != "idle":
            return
        from .. import plugins as _plugins
        if _plugins.is_plugin_type(f["type"]) and f["type"] not in _plugins.FEATURES:
            QMessageBox.information(self, APP_NAME, f"“{f['name']}” was made with the "
                                    f"“{_plugins.plugin_of(f)}” plugin, which isn't installed. It keeps the shape "
                                    "saved in the file; install the plugin (Document ▸ Plugins) to change it.")
            return
        if f["type"] == "sketch":
            self.start_sketch(fid)
        elif f["type"] == "import":
            QMessageBox.information(self, APP_NAME, "Imported geometry has no editable parameters.")
        else:
            self.start_feature(f["type"], fid)

    # ================================================================== sketch mode
    def start_sketch(self, edit_fid=None):
        if self.mode != "idle" or self.is_asm or self.is_dwg:
            return
        extra, base = [], None
        if edit_fid:
            feat = copy.deepcopy(self.doc.feature(edit_fid))
            index = self.doc.feature_index(edit_fid)
            is_new = False
            has_plane = True
        else:
            feat = self.doc.make_feature("sketch")
            index = self.doc.insert_index()
            is_new = True
            has_plane = False
            auto_plane = self._plane_for_selection(index)
            if auto_plane is not None:
                # e.g. three vertices are selected: make the plane they define and sketch on it
                pf, base = auto_plane
                extra = [pf]
                feat["params"]["plane"] = {"kind": "plane", "fid": pf["id"]}
                has_plane = True
            else:
                for pk in self.viewport.selection:
                    item = pick_to_item(self.viewport, pk, {"plane"})
                    if item:
                        feat["params"]["plane"] = item[1]
                        has_plane = True
                        break
        self.mode = "sketch"
        self.viewport.feature_hl = self.viewport.feature_sel = None
        self.sk_ctx = {"feature": feat, "index": index, "is_new": is_new,
                       "extra": extra, "base": base if base is not None else self.doc.state_before(index)}
        self.viewport.clear_selection()
        self.measure.hide()
        self._show_state(self.sk_ctx["base"])
        self.dialog = SketchDialog(self, feat, has_plane)
        self._adapt_tree(self.dialog)
        self.dialog.accepted.connect(self.finish_sketch)
        self.dialog.rejected.connect(self.cancel_sketch)
        self.dialog.planePicked.connect(self._sketch_plane_picked)
        self.dialog.show_cons.toggled.connect(self._sketch_display_toggles)
        self.dialog.show_regions.toggled.connect(self._sketch_display_toggles)
        self.viewport.pick_handler = self.dialog.handle_pick
        if has_plane:
            self._enter_sketcher(animate=True)            # new or edited: face it (if that option is on)
        else:
            self.viewport.status_text = "Select a plane or planar face to sketch on"
            self.dialog.set_status("Select a plane or a planar face in the graphics area.")
        self._populate_lists()

    # selections that define a plane by themselves, so Sketch can make the plane for you
    AUTO_SKETCH_PLANES = {"three_point", "plane_point", "point_normal", "curve_point", "mid_plane", "tangent"}

    def _plane_for_selection(self, index):
        """When what is selected defines a plane (three vertices, a face and a vertex, two faces …), a new plane
        feature for it and the model state with that plane, so a sketch can start on it. None otherwise —
        including for a single plane or face, which is simply sketched on."""
        vp = self.viewport
        want = {"plane", "face", "edge", "vertex"}
        items = [it for it in (pick_to_item(vp, pk, want) for pk in vp.selection) if it]
        if len(items) < 2:
            return None
        roles = [F.plane_role(k, r) for k, r in items]
        typ = F.detect_plane_type(roles)
        if typ not in self.AUTO_SKETCH_PLANES or (typ == "tangent" and "point" not in roles):
            return None
        pf = self.doc.make_feature("plane")
        pf["params"].update(type="auto", refs=[{"k": k, "ref": r} for k, r in items])
        st, err = self.doc.preview(pf, index)
        if err or pf["id"] not in st.planes:
            return None
        return pf, st

    def _sketch_plane_picked(self, ref):
        self.sk_ctx["feature"]["params"]["plane"] = ref
        self._enter_sketcher(animate=True)

    def _enter_sketcher(self, animate=True):
        ctx = self.sk_ctx
        feat = ctx["feature"]
        try:
            plane = F.resolve_plane(ctx["base"], feat["params"]["plane"])
        except K.KernelError as e:
            self.dialog.set_status(f"Can't use that plane: {e}")
            return
        sk = Sketch.from_dict(feat["params"]["sketch"])
        self.sketcher = Sketcher(plane, sk, feat["id"], self._sketch_changed, self._sketch_tool_changed)
        self.sketcher.variables = dict(ctx["base"].vars)
        self.sketcher.variable_kinds = dict(getattr(ctx["base"], "var_kinds", {}))
        self.sketcher.on_new_variable = self._sketch_new_variable
        self._sketch_display_toggles()
        vp = self.viewport
        vp.sketcher = self.sketcher
        vp.pick_handler = None
        vp.select_filter = None
        self.sketch_waiting_for_plane = False
        self.tool_stack.setCurrentIndex(1)
        self._sketch_tool_changed("select")
        if animate and self.orient_on_sketch:
            self._view_before_sketch = vp.cam.state()   # (turning the option off goes back to this)
            vp.normal_to(plane)
        self._sketch_changed(True)
        vp.setFocus()
        if ctx["is_new"]:
            self._sk_tool("line")

    def _sketch_display_toggles(self):
        if self.sketcher and self.dialog:
            self.sketcher.show_constraints = self.dialog.show_cons.isChecked()
            self.sketcher.show_regions = self.dialog.show_regions.isChecked()
            self.viewport.update()

    def _sketch_changed(self, final):
        ctx = self.sk_ctx
        if not ctx or not self.sketcher:
            return
        ctx["feature"]["params"]["sketch"] = self.sketcher.sk.to_dict()
        if final:
            st, err = self.doc.preview(ctx["feature"], ctx["index"], ctx.get("extra", ()))
            self._show_state(st, active_sketch=ctx["feature"]["id"])
        st = self.sketcher.sk.status
        if not st.get("ok", True):
            msg, ok = "Conflicting constraints (shown in red)", False
        elif not self.sketcher.sk.entities:
            msg, ok = "", False
        elif st.get("dof", 0) == 0:
            msg, ok = "Sketch is fully defined", True
        else:
            msg, ok = f"Under-defined — {st.get('dof', 0)} degree(s) of freedom (blue geometry can move)", False
        if self.dialog and isinstance(self.dialog, SketchDialog):
            self.dialog.set_status(msg, ok)
        self.viewport.status_text = self.sketcher.hint()
        self.viewport.update()

    def _sketch_tool_changed(self, tool):
        on = {}
        for name, b in self._sketch_buttons.items():
            if name == "construction":
                b.setChecked(bool(self.sketcher and self.sketcher.construction))
                continue
            on.setdefault(id(b), [b, False])
            if name == tool:
                on[id(b)][1] = True
                tips = b.property("tips")
                if tips:                                   # a shared button: show the variant now in use
                    b.setProperty("current", name)
                    b.setProperty("icon_name", name)
                    b.setIcon(icon(name))
                    b.setToolTip(tr(tips[name]))
        for b, checked in on.values():
            b.setChecked(checked)
        if self.sketcher:
            self.viewport.status_text = self.sketcher.hint()
            self.viewport.update()

    def _sk(self, method):
        if self.sketcher:
            getattr(self.sketcher, method)()
            self.viewport.update()
            self.viewport.setFocus()

    def _sk_tool(self, tool):
        if self.sketcher:
            self.sketcher.set_tool(tool)
            self.viewport.update()
            self.viewport.setFocus()

    def finish_sketch(self):
        if self.mode != "sketch":
            return
        ctx = self.sk_ctx
        feat = ctx["feature"]
        if self.sketcher:
            self.sketcher._close_editor()
            feat["params"]["sketch"] = self.sketcher.sk.to_dict()
        if isinstance(self.dialog, SketchDialog):
            feat["name"] = self.dialog.title.text().strip() or feat["name"]
        self._exit_sketch()
        if self.sketcher is None and ctx["is_new"] and not feat["params"]["sketch"]["entities"]:
            self._show_state(self.doc.state)
            return
        if ctx["is_new"]:
            extra = ctx.get("extra", [])
            for k, ex in enumerate(extra):          # the plane made for this sketch goes in first
                self.doc.add_feature(ex, ctx["index"] + k)
            self.doc.add_feature(feat, ctx["index"] + len(extra))
        else:
            self.doc.replace_feature(feat)
        self.status.showMessage(f"{feat['name']} saved", 5000)

    def cancel_sketch(self):
        self._exit_sketch()
        self._show_state(self.doc.state)

    def _exit_sketch(self):
        if self.sketcher:
            self.sketcher._close_editor()
        self.sketcher = None
        vp = self.viewport
        vp.sketcher = None
        vp.pick_handler = None
        vp.select_filter = None
        vp.status_text = ""
        if self.dialog:
            self.dialog.hide()
            self.dialog.deleteLater()
        self.dialog = None
        self.mode = "idle"
        self.tool_stack.setCurrentIndex(0)
        self._populate_lists()

    def edit_configs(self):
        if self.doc is None or self.is_asm or self.is_dwg:
            return
        self._leave_modes()
        from .config_ui import ConfigDialog
        ConfigDialog(self, self.doc).exec()

    def _refresh_config_box(self):
        box = getattr(self, "config_box", None)
        if box is None:
            return
        names = self.doc.config_names() if hasattr(self.doc, "config_names") else []
        box.blockSignals(True)
        box.clear()
        box.addItems(names)
        if names:
            box.setCurrentIndex(names.index(self.doc.active_config()))
        box.setVisible(bool(names))
        box.blockSignals(False)

    def _feature_from_sketch(self, ftype):
        """Shift+E / Shift+W and the Extrude / Revolve / Sweep buttons on the sketch toolbar: in a sketch, finish
        it and open the feature with its areas picked; otherwise just open the feature."""
        if self.mode == "sketch" and self.sk_ctx:
            fid = self.sk_ctx["feature"]["id"]
            self.finish_sketch()
            if self.mode != "idle" or self.doc.feature(fid) is None:
                return
            self.start_feature(ftype)
            if self.dialog is not None:
                self._tree_sketch_to_dialog(fid)
            return
        self.start_feature(ftype)

    def _normal_key(self):
        """N: inside a sketch, look straight at it (as before). In the 3D view: look straight at the flat face,
        plane or sketch under the mouse (or else the selected one) from the nearer side — again for the other
        side; with nothing flat there, look from the opposite side. A curved face under the mouse: nothing."""
        if self.mode == "sketch" and self.sketcher:
            self._normal_to_sketch()
            return
        vp = self.viewport
        kinds = {"face", "plane", "region", "sketch_line"}
        pk = None
        xy = getattr(vp, "_cursor_xy", None)
        if xy is not None:
            pk = vp.pick(xy[0], xy[1], kinds=kinds)
        if pk is None:
            pk = next((q for q in vp.selection if q.kind in kinds), None)
        if pk is None:
            vp.look_along(None)
            return
        try:
            plane = self._flat_plane_of(pk)
        except Exception:
            plane = None
        if plane is None:
            return                                         # curved: nothing to square up to
        vp.look_along(plane[0], plane[1], axes=plane[2] if len(plane) > 2 else ())

    def _flat_plane_of(self, pk):
        """(normal, a point on it) for a flat face, a plane or a sketch; None if it isn't flat."""
        if pk.kind == "face":
            shape = self.viewport.part_shapes.get(pk.key[0])
            if shape is None:
                return None
            face = K.faces(shape)[pk.key[1]]
            pl = K.planar_face_plane(face)
            if pl is None:
                return None
            # the face's straight edges, longest first: the view lines up with them
            dirs = []
            for e in K.edges(face):
                info = K.edge_info(e)
                if "p0" in info and "p1" in info:
                    d = np.subtract(info["p1"], info["p0"])
                    if np.linalg.norm(d) > 1e-9:
                        dirs.append((float(np.linalg.norm(d)), d / np.linalg.norm(d)))
            dirs.sort(key=lambda t: -t[0])
            return pl.normal, K.interior_point(face), [d for _l, d in dirs[:1]]
        if pk.kind in ("region", "sketch_line"):
            sr = self.doc.state.sketches.get(pk.key[0]) if not self.is_asm else None
            return None if sr is None else (sr.plane.normal, None, [sr.plane.xdir])
        if pk.kind == "plane":
            item = pick_to_item(self.viewport, pk, {"plane"})
            if item:
                pl = F.resolve_plane(self.doc.state, item[1])
                return pl.normal, None, [pl.xdir]
        return None

    def _normal_to_sketch(self):
        if self.sketcher:
            self.viewport.normal_to(self.sketcher.plane)
            return
        for pk in self.viewport.selection:
            item = pick_to_item(self.viewport, pk, {"plane"})
            if item:
                try:
                    self.viewport.normal_to(F.resolve_plane(self.doc.state, item[1]))
                except K.KernelError:
                    pass
                return

    # ------------------------------------------------------------------ display options (view cube menu)
    DISPLAY_DEFAULTS = {"render_mode": "shaded_edges", "hidden_edges": "removed", "tangent_edges": "visible",
                        "highlight_boundary": False, "high_quality": False, "perspective": False,
                        "orient_on_sketch": True, "translucency": 0.3}

    def _load_display_settings(self):
        self.orient_on_sketch = True
        for k, dflt in self.DISPLAY_DEFAULTS.items():
            v = self.settings.value("display/" + k, dflt)
            if isinstance(dflt, bool):
                v = str(v).lower() in ("true", "1")
            elif isinstance(dflt, float):
                try:
                    v = float(v)
                except (TypeError, ValueError):
                    v = dflt
            else:
                v = str(v)
            self.set_display(k, v, save=False)

    def set_display(self, key, value, save=True):
        """Change a display option (render mode, hidden / tangent edges, perspective …) and remember it."""
        vp = self.viewport
        if key == "perspective":
            vp.set_perspective(value)
        elif key == "orient_on_sketch":
            was = self.orient_on_sketch
            self.orient_on_sketch = bool(value)
            if self.mode == "sketch" and self.sketcher is not None and was != self.orient_on_sketch:
                # as Onshape: switching it on faces the sketch now; off goes back to the view you had
                if self.orient_on_sketch:
                    self._view_before_sketch = vp.cam.state()
                    vp.normal_to(self.sketcher.plane)
                elif getattr(self, "_view_before_sketch", None) is not None:
                    vp.animate_to(self._view_before_sketch)
        elif key == "translucency":
            vp.translucency = float(min(max(value, 0.05), 0.95))
            vp.update()
        else:
            old = getattr(vp, key)
            setattr(vp, key, value)
            if old != value:
                if key == "high_quality":
                    vp.refresh_display(remesh=True)
                elif key in ("tangent_edges", "highlight_boundary"):
                    vp.refresh_display()
                else:
                    vp.update()
        if save:
            self.settings.setValue("display/" + key, value)

    def _fill_cube_menu(self, m):
        """The view cube's menu, as Onshape's: views, preferences, named / previous views, zoom, projection,
        render mode, hidden and tangent edges, quality, boundary edges and the section view."""
        vp = self.viewport
        for name in ("Isometric", "Dimetric", "Trimetric"):
            m.addAction(name, lambda _=False, n=name: vp.set_standard_view(n))
        m.addSeparator()
        m.addAction(tr("Graphics preferences…"), self.graphics_preferences)
        m.addSeparator()
        m.addAction(tr("Named views…"), self.named_views_dialog)
        a = m.addAction(tr("Previous view"), lambda: vp.previous_view())
        a.setEnabled(vp.has_previous_view())
        m.addSeparator()
        m.addAction(tr("Zoom to fit"), lambda: vp.fit())
        m.addAction(tr("Zoom to window"), lambda: vp.start_zoom_window())
        m.addSeparator()
        self._fill_display_menu(m, clear=False)
        if not self.is_asm and not self.is_dwg:
            m.addSeparator()
            m.addAction(icon("section"), "Section view…", self.toggle_section)

    def _fill_display_menu(self, m, clear=True):
        """Projection, sketch orientation, render mode, hidden / tangent edges, quality and boundary edges."""
        vp = self.viewport
        if clear:
            m.clear()
            for old in m.findChildren(QMenu, options=Qt.FindDirectChildrenOnly):
                old.deleteLater()          # submenus from the last fill (they're owned by m, so clear() keeps them)

        def check(menu, text, on, slot):
            a = menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(bool(on))
            a.triggered.connect(lambda _=False: slot())
            return a
        check(m, "Perspective view", vp.cam.perspective,
              lambda: self.set_display("perspective", not vp.cam.perspective))
        check(m, "Orient normal to sketch on edit", self.orient_on_sketch,
              lambda: self.set_display("orient_on_sketch", not self.orient_on_sketch))
        m.addSeparator()
        rm = submenu(m, dict(RENDER_MODES)[vp.render_mode])
        for key, label in RENDER_MODES:
            check(rm, label, vp.render_mode == key, lambda k=key: self.set_display("render_mode", k))
        if vp.render_mode != "translucent":          # (see-through already shows every edge)
            hm = submenu(m, dict(HIDDEN_EDGES)[vp.hidden_edges])
            for key, label in HIDDEN_EDGES:
                check(hm, label, vp.hidden_edges == key, lambda k=key: self.set_display("hidden_edges", k))
        tm = submenu(m, dict(TANGENT_EDGES)[vp.tangent_edges])
        for key, label in TANGENT_EDGES:
            check(tm, label, vp.tangent_edges == key, lambda k=key: self.set_display("tangent_edges", k))
        check(m, "View in high quality", vp.high_quality,
              lambda: self.set_display("high_quality", not vp.high_quality))
        check(m, "Highlight boundary edges", vp.highlight_boundary,
              lambda: self.set_display("highlight_boundary", not vp.highlight_boundary))

    def named_views_dialog(self):
        """Save the current view under a name, go back to a saved one, or delete one. Saved in the file."""
        doc = self.doc
        if self.is_dwg or doc is None:
            return
        if not hasattr(doc, "named_views"):
            doc.named_views = {}
        vp = self.viewport
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Named views"))
        lay = QVBoxLayout(dlg)
        from PySide6.QtWidgets import QListWidget
        lst = QListWidget()
        lay.addWidget(lst)

        def fill():
            lst.clear()
            for name in doc.named_views:
                lst.addItem(name)
        fill()

        def go(item=None):
            item = item or lst.currentItem()
            if item is None:
                return
            v = doc.named_views.get(item.text())
            if v:
                self.set_display("perspective", bool(v.get("perspective", False)))
                vp.animate_to((np.array(v["target"], float), np.array(v["R"], float), float(v["scale"])))

        def add():
            name, ok = QInputDialog.getText(dlg, "Named views", "Name for the current view:",
                                            text=f"View {len(doc.named_views) + 1}")
            name = name.strip()
            if not ok or not name:
                return
            t, R, sc = vp.cam.state()
            doc.named_views[name] = {"target": [float(x) for x in t], "R": [[float(x) for x in r] for r in R],
                                     "scale": float(sc), "perspective": bool(vp.cam.perspective)}
            doc.dirty = True
            fill()

        def delete():
            item = lst.currentItem()
            if item is not None and item.text() in doc.named_views:
                del doc.named_views[item.text()]
                doc.dirty = True
                fill()
        lst.itemDoubleClicked.connect(go)
        row = QHBoxLayout()
        for text, fn in (("Save current view…", add), ("Go to", go), ("Delete", delete)):
            b = QPushButton(tr(text))
            b.clicked.connect(lambda _=False, f=fn: f())
            row.addWidget(b)
        close = QPushButton(tr("Close"))
        close.clicked.connect(dlg.accept)
        row.addWidget(close)
        lay.addLayout(row)
        dlg.resize(380, 300)
        dlg.exec()

    def graphics_preferences(self):
        """Display settings in one place: render mode, edges, translucency, quality, projection, shine."""
        vp = self.viewport
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Graphics preferences"))
        from PySide6.QtWidgets import QCheckBox, QFormLayout, QSlider
        form = QFormLayout(dlg)

        def combo(options, current, key):
            cb = QComboBox()
            for k, label in options:
                cb.addItem(tr(label), k)
            cb.setCurrentIndex(max(0, cb.findData(current)))
            cb.currentIndexChanged.connect(lambda _i: self.set_display(key, cb.currentData()))
            return cb
        form.addRow(tr("Render mode"), combo(RENDER_MODES, vp.render_mode, "render_mode"))
        form.addRow(tr("Hidden edges"), combo(HIDDEN_EDGES, vp.hidden_edges, "hidden_edges"))
        form.addRow(tr("Tangent edges"), combo(TANGENT_EDGES, vp.tangent_edges, "tangent_edges"))
        sl = QSlider(Qt.Horizontal)
        sl.setRange(5, 95)
        sl.setValue(int(round(vp.translucency * 100)))
        sl.valueChanged.connect(lambda v: self.set_display("translucency", v / 100.0))
        form.addRow(tr("Translucent opacity"), sl)
        for label, key, cur in (("View in high quality", "high_quality", vp.high_quality),
                                ("Highlight boundary edges", "highlight_boundary", vp.highlight_boundary),
                                ("Perspective view", "perspective", vp.cam.perspective),
                                ("Orient normal to sketch on edit", "orient_on_sketch", self.orient_on_sketch)):
            c = QCheckBox(tr(label))
            c.setChecked(bool(cur))
            c.toggled.connect(lambda on, k=key: self.set_display(k, bool(on)))
            form.addRow("", c)
        sh = QCheckBox(tr("Shiny surfaces"))
        sh.setChecked(vp.shiny)
        sh.toggled.connect(lambda on: self.set_shiny(on))
        form.addRow("", sh)
        close = QPushButton(tr("Close"))
        close.clicked.connect(dlg.accept)
        form.addRow("", close)
        dlg.exec()

    def set_shiny(self, on, save=True):
        self.viewport.shiny = bool(on)
        self.act_shiny.blockSignals(True)
        self.act_shiny.setChecked(bool(on))
        self.act_shiny.blockSignals(False)
        self.viewport.update()
        if save:
            self.settings.setValue("shiny", "true" if on else "false")

    def set_nav_style(self, key, save=True):
        from . import navstyles as NS
        key = key if key in NS.STYLES else NS.DEFAULT
        self.viewport.nav_style = key
        for k, a in getattr(self, "nav_actions", {}).items():
            a.setChecked(k == key)
        if save:
            self.settings.setValue("nav_style", key)
            self.status.showMessage(f"Mouse style: {NS.STYLES[key]['label']}", 4000)

    def set_zoom_speed(self, key, save=True):
        from . import navstyles as NS
        speeds = {k: v for k, _l, v in NS.ZOOM_SPEEDS}
        key = key if key in speeds else "normal"
        self.viewport.zoom_speed = speeds[key]
        for k, a in getattr(self, "zoom_actions", {}).items():
            a.setChecked(k == key)
        if save:
            self.settings.setValue("zoom_speed", key)

    def set_orbit_mode(self, mode, save=True):
        mode = mode if mode in ("cursor", "centre", "origin") else "cursor"
        self.viewport.orbit_mode = mode
        for m_, a in self.orbit_actions.items():
            a.setChecked(m_ == mode)
        if save:
            self.settings.setValue("orbit", mode)

    # ================================================================== your own icons
    def icon_names(self):
        """Every icon name the toolbars use, plus every feature type (for the feature list)."""
        names = {b.property("icon_name") for b in self.findChildren(QToolButton)}
        names |= {nm for _a, nm in getattr(self, "_menu_actions", [])}
        names |= set(F.FEATURE_LABELS)
        names |= {"part", "assembly", "drawing", "error", "folder", "new_folder", "origin", "search", "iso_view"}
        try:
            from .. import plugins as P
            names |= {"plugin:" + k.split(":", 1)[-1] for k in P.FEATURES}
        except Exception:
            pass
        return sorted(n for n in names if n and not str(n).startswith("__"))

    def open_icons_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from .icons import user_icons_dir
        d = user_icons_dir()
        os.makedirs(d, exist_ok=True)
        readme = os.path.join(d, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as fh:
                fh.write("Your own FreeShape icons\n\nPut an .svg or .png here named like a tool to use it instead of the "
                         "built-in icon: extrude.svg, fillet.png, sketch.svg ... A plugin feature's icon is "
                         "plugin_<id>, e.g. plugin_gridfinity.svg.\n\nView > Icons > Save the built-in icons there writes "
                         "every built-in icon as a PNG with the right name, to see the names or to start from.\n"
                         "Then View > Icons > Reload icons (or restart). Delete a file to get the built-in icon back.\n")
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def export_icons(self):
        from .icons import export_builtin, user_icons_dir
        d = os.path.join(user_icons_dir(), "built-in")
        n = export_builtin(self.icon_names(), d)
        QMessageBox.information(self, APP_NAME, f"Saved {n} built-in icons (64 × 64 PNG) to<br><b>{d}</b><br><br>"
                                "To change one, copy it up into the icons folder (or make an .svg with the same "
                                "name there), edit it, then View ▸ Icons ▸ Reload icons.")

    def reload_icons(self):
        from . import icons as ICONS
        ICONS.icon.cache_clear()
        self.set_theme(theme.MODE, save=False)
        self.status.showMessage(tr("Icons reloaded"), 4000)

    # ================================================================== theme
    def set_theme(self, mode, save=True):
        from . import icons as ICONS
        theme.apply(mode)
        ICONS.set_ink(theme.ICON_INK)
        self.setStyleSheet(theme.stylesheet())
        self._adapt_tree(self, include_root=False)
        for b in self.findChildren(QToolButton):
            nm = b.property("icon_name")
            if nm == "__arrow":
                b.setIcon(self._arrow_icon())
            elif nm:
                b.setIcon(icon(nm))
        for a, nm in getattr(self, "_menu_actions", []):
            a.setIcon(icon(nm, 20))
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            self.tabs.setTabIcon(i, icon("assembly" if getattr(d, "kind", "") == "assembly" else "part", 16))
        for mode_, a in getattr(self, "theme_actions", {}).items():
            a.setChecked(mode_ == theme.MODE)
        if self.doc is not None:
            self._populate_lists()
        self.viewport.update()
        if save:
            self.settings.setValue("theme", theme.MODE)

    def _adapt_tree(self, root, include_root=True):
        """Re-colour inline widget styles (written with light colours) for the current theme."""
        widgets = ([root] if include_root else []) + root.findChildren(QWidget)
        for w_ in widgets:
            css = w_.property("light_css")
            if css is None:
                css = w_.styleSheet()
                if not css:
                    continue
                w_.setProperty("light_css", css)
            w_.setStyleSheet(theme.adapt(css))

    # ================================================================== recent files
    def _recent(self):
        v = self.settings.value("recent", [])
        if isinstance(v, str):
            v = [v]
        return [x for x in (v or []) if isinstance(x, str)]

    def _recent_times(self):
        import json
        try:
            return json.loads(str(self.settings.value("recent_times", "{}")))
        except Exception:
            return {}

    def _add_recent(self, path):
        import json, time
        if not path:
            return
        path = os.path.abspath(path)
        lst = [path] + [x for x in self._recent() if os.path.abspath(x) != path]
        lst = lst[:30]
        self.settings.setValue("recent", lst)
        times = self._recent_times()
        times[path] = time.time()
        self.settings.setValue("recent_times", json.dumps({k: v for k, v in times.items() if k in lst}))
        if self.main_stack.currentIndex() == 1:
            self._refresh_home()

    def _remove_recent(self, path):
        self.settings.setValue("recent", [x for x in self._recent() if os.path.abspath(x) != os.path.abspath(path)])
        self._refresh_home()

    def _clear_recent(self):
        if QMessageBox.question(self, APP_NAME, "Clear the list of recent files? (The files themselves are kept.)") \
                == QMessageBox.Yes:
            self.settings.setValue("recent", [])
            self._refresh_home()

    # ------------------------------------------------------------------ home page
    def _refresh_home(self):
        times = self._recent_times()
        items = [(p, times.get(os.path.abspath(p), 0)) for p in self._recent()]
        back = None
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is not None and (self._has_content(d) or d.path):
                back = self.doc.name if self.doc is not None and (self._has_content(self.doc) or self.doc.path) \
                    else d.name
                break
        self.home.set_recent(items, back)

    def show_home(self):
        if self.main_stack.currentIndex() == 0:
            self._leave_modes()
            self._update_thumbnail()
        self._refresh_home()
        self.main_stack.setCurrentIndex(1)
        self.doc_label.setText(tr("Home"))
        self.setWindowTitle(APP_NAME)

    def show_workspace(self):
        if self.main_stack.currentIndex() != 0:
            self.main_stack.setCurrentIndex(0)
            if self.doc is not None:
                self.setWindowTitle(f"{self.doc.name} — {APP_NAME}")
                self.doc_label.setText(self.doc.name + ("  •" if self.doc.dirty else ""))
                self.viewport.update()

    def _home_new_studio(self):
        if self._is_pristine(self.doc) and self.doc.kind == "studio":
            self.show_workspace()
        else:
            self.new_doc()

    def open_any(self, path):
        """Open a FreeShape document, or import a STEP file — whatever the file is."""
        if not os.path.exists(path):
            QMessageBox.information(self, APP_NAME, f"“{os.path.basename(path)}” can't be found — it may have "
                                    "been moved or deleted.")
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in (".step", ".stp", ".brep", ".brp"):
            if self.mode != "idle":
                self._leave_modes()
            self.import_step_file(path)
        elif ext in (".fshape", ".fcad", ".fasm", ".fdwg"):
            self.open_file(path)
        else:
            QMessageBox.information(self, APP_NAME, "FreeShape opens .fshape, .fasm and .fdwg documents, STEP files "
                                    "and BREP files.")
            return
        self.show_workspace()

    def _thumb_key(self, doc):
        return doc.path or getattr(doc, "_thumb_for", None)

    def _update_thumbnail(self, doc=None):
        """Save a picture of the current model for its card on the home page."""
        from .home import thumb_path
        doc = doc or self.doc
        if doc is None or doc is not self.doc or self.mode != "idle" or self.main_stack.currentIndex() != 0:
            return
        key = self._thumb_key(doc)
        if not key:
            return
        try:
            img = self.dwg.thumbnail() if doc.kind == "drawing" else self.viewport.render_thumbnail()
            if img is not None:
                img.save(thumb_path(key))
        except Exception:
            pass

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            if url.toLocalFile():
                self.open_any(url.toLocalFile())

    def _fill_recent(self):
        m = self.recent_menu
        m.clear()
        lst = [x for x in self._recent() if os.path.exists(x)]
        if not lst:
            a = m.addAction(tr("No recent documents"))
            a.setEnabled(False)
            return
        for path in lst:
            a = m.addAction(os.path.basename(path), lambda p=path: self.open_any(p))
            a.setToolTip(path)
        m.addSeparator()
        m.addAction(tr("Clear list"), self._clear_recent)

    # ================================================================== autosave & recovery
    @staticmethod
    def autosave_root():
        from PySide6.QtCore import QStandardPaths
        # a fixed location (not tied to the app name Qt was given) so every start finds the same folder;
        # FREESHAPE_AUTOSAVE_DIR moves it (the tests use their own, so they never touch yours)
        d = os.environ.get("FREESHAPE_AUTOSAVE_DIR")
        if d:
            os.makedirs(d, exist_ok=True)
            return d
        generic = QStandardPaths.writableLocation(QStandardPaths.GenericDataLocation)
        base = os.path.join(generic, "FreeShape") if generic else os.path.expanduser("~/.freeshape")
        d = os.path.join(base, "autosave")
        os.makedirs(d, exist_ok=True)
        return d

    @classmethod
    def autosave_dir(cls):
        """This running copy's own folder, so two FreeShape windows never touch each other's autosaves."""
        d = os.path.join(cls.autosave_root(), str(os.getpid()))
        os.makedirs(d, exist_ok=True)
        return d

    @staticmethod
    def _process_alive(pid):
        if pid == os.getpid():
            return True
        if sys.platform == "win32":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == 259                       # STILL_ACTIVE
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _orphaned_autosaves(self):
        """Autosave files left behind by FreeShape windows that are no longer running (i.e. crashed)."""
        root = self.autosave_root()
        out = []
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if name.endswith((".fshape", ".fasm", ".fdwg")) and os.path.isfile(full):
                out.append(full)                                          # older single-folder layout
            elif os.path.isdir(full) and name.isdigit() and not self._process_alive(int(name)):
                out += [os.path.join(full, f) for f in os.listdir(full) if f.endswith((".fshape", ".fasm", ".fdwg"))]
                if not os.listdir(full):
                    try:
                        os.rmdir(full)
                    except OSError:
                        pass
        return sorted(out)

    def _autosave_path(self, doc):
        import uuid
        if not getattr(doc, "_autosave_id", None):
            doc._autosave_id = uuid.uuid4().hex
        ext = {"assembly": ".fasm", "drawing": ".fdwg"}.get(doc.kind, ".fshape")
        return os.path.join(self.autosave_dir(), doc._autosave_id + ext)

    def autosave(self):
        import json, time
        for doc in list(self.docs.values()):
            if not (doc.dirty and self._has_content(doc)):
                continue
            try:
                if doc.kind == "assembly":
                    self._update_link_paths(doc)
                d = json.loads(doc.to_json())
                d["autosave"] = {"path": doc.path, "name": doc.name, "time": time.time()}
                tmp = self._autosave_path(doc) + ".tmp"
                with open(tmp, "w") as fh:
                    json.dump(d, fh)
                os.replace(tmp, self._autosave_path(doc))
            except Exception:
                pass

    def _drop_autosave(self, doc):
        if doc is not None and getattr(doc, "_autosave_id", None):
            try:
                os.remove(self._autosave_path(doc))
            except OSError:
                pass

    def offer_recovery(self):
        import json, time
        files = self._orphaned_autosaves()
        if not files:
            return
        names = []
        for f in files:
            try:
                with open(f) as fh:
                    meta = json.load(fh).get("autosave", {})
                names.append(f"• {meta.get('name', 'Untitled')}  —  saved {time.strftime('%d %b %H:%M', time.localtime(meta.get('time', 0)))}")
            except Exception:
                names.append("• (unreadable)")
        r = QMessageBox.question(
            self, APP_NAME, "FreeShape didn't close properly last time. Recover these unsaved documents?\n\n"
            + "\n".join(names), QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        for f in files:
            if r == QMessageBox.Yes:
                try:
                    with open(f) as fh:
                        meta = json.load(fh).get("autosave", {})
                    doc = AssemblyDoc.load(f, resolver=self._resolve_source) if f.endswith(".fasm") \
                        else DrawingDoc.load(f, resolver=self._resolve_drawing_source,
                                                      hlr_async=self.dwg.request_exact) if f.endswith(".fdwg") \
                        else Document.load(f)
                    doc.path = meta.get("path")
                    doc.name = (meta.get("name") or "Recovered") + " (recovered)"
                    doc.dirty = True
                    self._open_in_tab(doc)
                except Exception as e:
                    QMessageBox.warning(self, APP_NAME, f"Couldn't recover {os.path.basename(f)}:\n{e}")
            try:
                os.remove(f)
                folder = os.path.dirname(f)
                if folder != self.autosave_root() and not os.listdir(folder):
                    os.rmdir(folder)
            except OSError:
                pass

    # ================================================================== section view
    def toggle_section(self, *_):
        if self.is_dwg and self.section_panel is None:
            return
        if self.section_panel is None:
            self.section_panel = SectionPanel(self)
            self._adapt_tree(self.section_panel)
            self.section_panel.closed.connect(self.toggle_section)
            chosen = None
            for pk in self.viewport.selection if self.is_asm else ():
                if pk.kind == "face":
                    shp = self.viewport.part_shapes.get(pk.key[0])
                    if shp is not None:
                        chosen = K.planar_face_plane(K.faces(shp)[pk.key[1]])
                        if chosen is not None:
                            break
            for pk in self.viewport.selection if not self.is_asm else ():
                item = pick_to_item(self.viewport, pk, {"plane"})
                if item:
                    try:
                        chosen = F.resolve_plane(self.doc.state, item[1])
                    except K.KernelError:
                        chosen = None
                    break
            if chosen is not None:
                self.section_panel.use_selection(chosen)
            else:
                self.section_panel._plane_changed()
            self.section_panel.place()
        else:
            self.section_panel.hide()
            self.section_panel.deleteLater()
            self.section_panel = None
            self.viewport.set_section(None)
        on = self.section_panel is not None
        for w_ in (self.act_section, self.btn_section, self.btn_asection):
            w_.blockSignals(True)
            w_.setChecked(on)
            w_.blockSignals(False)

    # ================================================================== sheet metal
    def _flat_on(self, state, p):
        return getattr(self, "_flat_view", False) and p.id in getattr(state, "sheet", {})

    def flat_pattern_of(self, state, pid):
        """The flat pattern of a sheet metal part (cached while the part is unchanged)."""
        from .. import sheetmetal as SM
        part = state.parts[pid]
        cache = self.__dict__.setdefault("_flat_cache", {})
        hit = cache.get(pid)
        if hit is not None and hit[0] is part.shape:
            return hit[1]
        fp = SM.flat_pattern(part.shape, state.sheet[pid])
        if len(cache) > 32:
            cache.clear()
        cache[pid] = (part.shape, fp)
        return fp

    def _flat_or_folded(self, state, p):
        if not self._flat_on(state, p):
            return p.shape
        try:
            return self.flat_pattern_of(state, p.id).solid()
        except Exception as e:
            self.status.showMessage(f"Couldn't unfold {p.name}: {e}", 6000)
            return p.shape

    def toggle_flat(self, *_):
        self._flat_view = not getattr(self, "_flat_view", False)
        self.btn_flat.setChecked(self._flat_view)
        if self.is_asm or self.is_dwg:
            return
        if self.mode == "idle":
            self._show_state(self.doc.state)
        if self._flat_view and not getattr(self.doc.state, "sheet", None):
            self.status.showMessage(tr("No sheet metal parts here yet — make one with Sheet metal first"), 5000)
        else:
            self.status.showMessage("Showing sheet metal parts flat" if self._flat_view else
                                    "Showing parts folded", 3000)

    def nest_for_cutting(self):
        if self.is_asm or self.is_dwg or self.doc is None:
            return
        state = self.doc.state
        if not [i for i in getattr(state, "sheet", {}) if i in state.parts]:
            QMessageBox.information(self, APP_NAME, "There are no sheet metal parts to nest — make one with the Sheet "
                                    "metal tool first.")
            return
        from .nest_ui import NestDialog
        self._leave_modes()
        NestDialog(self, self.doc).exec()

    def export_flat_dxf(self, pid=None):
        from .. import sheetmetal as SM
        if self.is_asm or self.is_dwg:
            return
        state = self.doc.state
        ids = [i for i in getattr(state, "sheet", {}) if i in state.parts]
        if not ids:
            QMessageBox.information(self, APP_NAME, "There are no sheet metal parts — make one with the Sheet metal "
                                    "tool first.")
            return
        if pid is None:
            sel = [pk.key[0] for pk in self.viewport.selection if pk.kind in ("face", "edge", "part")]
            sel = [i for i in sel if i in ids]
            pid = sel[0] if sel else (ids[0] if len(ids) == 1 else None)
        if pid is None:
            names = [state.parts[i].name for i in ids]
            name, ok = QInputDialog.getItem(self, "Export flat pattern", "Which part?", names, 0, False)
            if not ok:
                return
            pid = ids[names.index(name)]
        part = state.parts[pid]
        path, _ = filedlg.getSaveFileName(self, "Export flat pattern", part.name.replace(" ", "_") + "_flat.dxf",
                                              "DXF (*.dxf)")
        if not path:
            return
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        try:
            fp = self.flat_pattern_of(state, pid)
            _n, nb = SM.flat_dxf(fp, path, part.name)
            self.status.showMessage(f"Exported the flat pattern of {part.name} ({nb} bend line(s)): {path}", 8000)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Couldn't export the flat pattern:\n{e}")

    # ================================================================== undo / misc
    def undo(self):
        if self.mode == "sketch":
            self._sk("undo")
        elif self.mode == "idle":
            self.doc.undo()

    def redo(self):
        if self.mode == "sketch":
            self._sk("redo")
        elif self.mode == "idle":
            self.doc.redo()

    def _escape(self):
        if self.is_dwg:
            self.dwg.key(_FakeKey(Qt.Key_Escape))
            return
        if self.is_asm:
            self.asm.escape()
            return
        if self.mode == "sketch":
            if self.sketcher:
                self.sketcher.escape()
                self.viewport.update()
            return
        if self.mode == "dialog":
            self.cancel_dialog()
        elif self.mode == "idle":
            self.viewport.clear_selection()

    def _delete_key(self):
        if self.is_dwg:
            if self.dwg.selection:
                self.doc.delete(self.dwg.selection)
                self.dwg.selection = []
            return
        if self.mode == "sketch":
            self._sk("delete_selection")
            return
        if self.mode != "idle":
            return
        if self.is_asm:
            self.asm.delete_selected()
            return
        ids = self.left.features.selected_feature_ids()
        if ids:
            self._delete_features(ids)

    def _delete_features(self, ids):
        names = ", ".join(self.doc.feature(i)["name"] for i in ids)
        if len(ids) > 4:
            names = f"these {len(ids)} features"
        if QMessageBox.question(self, APP_NAME, f"Delete {names}?") == QMessageBox.Yes:
            self.doc.delete_features(ids)

    # ================================================================== tree interactions
    def _tree_reordered(self, ids, rollback, folders=None):
        if self.mode != "idle":
            self._populate_lists()
            return
        folders = folders or {}
        order = {fid: i for i, fid in enumerate(ids)}
        new = sorted(self.doc.features, key=lambda f: order.get(f["id"], 1e9))
        moved_folders = any(f.get("folder") != folders[f["id"]] for f in new if f["id"] in folders)
        if [f["id"] for f in new] != [f["id"] for f in self.doc.features] or rollback != self.doc.active_count() \
                or moved_folders:
            self.doc.checkpoint()
            for f in new:
                if f["id"] in folders:
                    if folders[f["id"]]:
                        f["folder"] = folders[f["id"]]
                    else:
                        f.pop("folder", None)
            self.doc.features = new
            self.doc.set_rollback(rollback if rollback < len(new) else None)
            self.doc.rebuild()
        else:
            self._populate_lists()

    # ------------------------------------------------------------------ feature-list folders
    def _toggle_folder(self, fo):
        info = self.doc.folders.get(fo) if self.doc is not None else None
        if info is not None:
            self.doc.set_folder_open(fo, not info.get("open", True))

    def _new_folder(self, ids):
        if self.doc is None or self.is_asm or self.is_dwg or self.mode != "idle":
            return
        if not ids:
            self.status.showMessage("Select the features to put in a folder first (Shift- or Ctrl-click to "
                                    "select several), then click New folder.", 8000)
            return
        try:
            fo = self.doc.make_folder(ids)
        except ValueError as e:
            QMessageBox.information(self, APP_NAME, str(e))
            return
        self._rename_folder(fo, first=True)

    def _rename_folder(self, fo, first=False):
        info = self.doc.folders.get(fo)
        if info is None:
            return
        name, ok = QInputDialog.getText(self, "Name folder" if first else "Rename folder", "Name:",
                                        text=info["name"])
        if ok and name.strip() and name.strip() != info["name"]:
            if first:                    # naming a new folder is part of making it: one undo step
                info["name"] = name.strip()
                self.doc.notify()
            else:
                self.doc.rename_folder(fo, name.strip())

    def _remove_from_folder(self, ids):
        try:
            self.doc.remove_from_folder(ids)
        except ValueError as e:
            QMessageBox.information(self, APP_NAME, str(e))

    def _folder_context(self, fo, gpos):
        info = self.doc.folders.get(fo)
        if info is None or self.mode != "idle":
            return
        members = self.doc.folder_members(fo)
        m = QMenu(self)
        m.addAction("Collapse" if info.get("open", True) else "Expand", lambda: self._toggle_folder(fo))
        m.addAction(tr("Rename…"), lambda: self._rename_folder(fo))
        m.addSeparator()
        all_sup = all((self.doc.feature(i) or {}).get("suppressed") for i in members)
        m.addAction("Unsuppress all" if all_sup else "Suppress all",
                    lambda: self.doc.set_folder_suppressed(fo, not all_sup))
        m.addAction(tr("Ungroup (keep the features)"), lambda: self.doc.ungroup_folder(fo))
        m.addSeparator()
        m.addAction(f"Delete folder and its {len(members)} feature(s)", lambda: self._delete_features(members))
        m.exec(gpos)

    # ------------------------------------------------------------------ feature-list highlighting (like Onshape)
    def _std_plane_shown(self, name):
        return self.doc is not None and name not in self.doc.hidden_features

    def _feature_hovered(self, fid):
        self._hl_hover = fid
        vp = self.viewport
        # a default plane hovered in the list lights up in the view just as when the mouse is over it there
        mine = getattr(self, "_list_plane_hover", None)
        if mine is not None and vp.hover == mine:
            vp.hover = None
        self._list_plane_hover = None
        if fid and str(fid).startswith("std:") and self._std_plane_shown(fid[4:]):
            self._list_plane_hover = vp.hover = Pick("plane", ("std", fid[4:]))
        self._update_feature_hl()

    def _update_feature_hl(self):
        """Show what the features made: the ones selected in the list exactly as selected faces are drawn (as in
        Onshape), the one under the mouse tinted (its faces, or a sketch's curves)."""
        vp = self.viewport
        if self.is_asm or self.mode != "idle":
            if vp.feature_hl is not None or vp.feature_sel is not None:
                vp.feature_hl = vp.feature_sel = None
                vp.update()
            return

        def geometry(fids):
            faces, polys = {}, []
            for fid in fids:
                try:
                    f_, p_ = self._feature_geometry(fid)
                except Exception:
                    continue
                for pid, fis in f_.items():
                    faces.setdefault(pid, set()).update(fis)
                polys += p_
            return faces, polys
        sel = list(self.left.features.selected_feature_ids())
        faces, polys = geometry(sel)
        vp.feature_sel = {"faces": faces, "polys": polys} if (faces or polys) else None
        hover = [self._hl_hover] if self._hl_hover and self._hl_hover not in sel else []
        faces, polys = geometry(hover)
        edges = {}
        many = sum(len(v) for v in faces.values()) > 3000      # huge imports: tint the faces, skip outlines
        for pid, fis in ({} if many else faces).items():
            shp = vp.part_shapes.get(pid)
            if shp is not None:
                edges[pid] = K.face_edge_indices(shp, fis)
        vp.feature_hl = {"faces": faces, "edges": edges, "polys": polys} if (faces or polys) else None
        vp.update()

    def _feature_of_pick(self, pk):
        """The feature that made what's picked in the view — a face, an edge, a part, a sketch's curve or area, a
        plane — or None. A face belongs to the last feature that made or remade it; an edge to the last feature
        that made either face it borders (so a fillet's edge is the fillet's)."""
        doc = self.doc
        kind = pk.kind
        if kind in ("region", "sketch_line", "sketch_point"):
            return pk.key[0]
        if kind == "plane":                              # a plane feature, or a default plane ("std:Front")
            return pk.key[1] if pk.key[0] == "plane" else f"std:{pk.key[1]}"
        if kind not in ("face", "edge", "part", "vertex"):
            return None
        pid = pk.key[0]
        if kind == "part":
            fid = str(pid).split(":", 1)[0]
            return fid if doc.feature(fid) is not None else None
        shape = self.viewport.part_shapes.get(pid)
        if shape is None:
            return None
        if kind == "face":
            wanted = {pk.key[1]}
        elif kind == "edge":
            edge = K.edges(shape)[pk.key[1]]
            all_faces = K.faces(shape)
            wanted = {i for i, f in enumerate(all_faces)
                      if any(f.IsSame(g) for g in K._faces_at_edge(shape, edge))}
        else:
            return None
        found = None
        for f in doc.features[:doc.active_count()]:
            if f.get("suppressed") or f["type"] in ("sketch", "plane", "variable", "measure_value"):
                continue
            try:
                faces, _p = self._feature_geometry(f["id"])
            except Exception:
                continue
            if wanted & set(faces.get(pid, ())):
                found = f["id"]                          # the last one wins
        return found

    def _feature_geometry(self, fid):
        """(faces by part in the model shown now, world polylines) for one feature — cached per model state."""
        doc = self.doc
        key = (fid, id(doc.state), doc.active_count(), tuple(getattr(doc, "plane_sizes", {}).get(fid, ())))
        hit = self._hl_cache.get(key)
        if hit is not None:
            return hit
        if len(self._hl_cache) > 200:
            self._hl_cache.clear()
        faces, polys = {}, []
        if fid == "origin" or str(fid).startswith("std:"):
            h = self.viewport.plane_half()
            if fid == "origin":                         # a small ring round the origin in each default plane
                r = h * 0.06
                a = np.linspace(0, 2 * np.pi, 33)
                for name in ("Top", "Front", "Right"):
                    pl = K.std_plane(name)
                    polys.append(np.array([pl.to_world(r * np.cos(t), r * np.sin(t)) for t in a]))
            elif self._std_plane_shown(fid[4:]):
                pass                                    # shown: it lights up like a hovered plane (above)
            else:                                       # hidden: just its outline
                pl = K.std_plane(fid[4:])
                cs = plane_corners(doc.plane_rect(("std", fid[4:]), h))
                polys.append(np.array([pl.to_world(u, v) for u, v in cs + cs[:1]]))
            return faces, polys                          # (not cached: it depends on the plane being shown)
        f = doc.feature(fid)
        idx = doc.feature_index(fid)
        if f is None or f.get("suppressed") or idx is None or idx >= doc.active_count():
            pass
        elif f["type"] == "sketch":
            sr = doc.state.sketches.get(fid)
            if sr is not None:
                for eid, e in sr.sketch.entities.items():
                    if e["type"] == "point":
                        continue
                    for poly in sr.sketch.polylines(eid, 48):
                        polys.append(np.array([sr.plane.to_world(u, v) for u, v in poly]))
        elif f["type"] == "plane":
            pl = doc.state.planes.get(fid)
            if pl is not None:
                h = self.viewport.plane_half() * 0.6
                cs = plane_corners(doc.plane_rect(("plane", fid), h))
                polys.append(np.array([pl.to_world(u, v) for u, v in cs + cs[:1]]))
        elif f["type"] not in ("variable", "measure_value"):
            before = doc.state_before(idx)
            after = doc.state_before(idx + 1)
            new = F.feature_new_faces(before, after)
            faces = F.map_faces_to(after, new, doc.state) if idx + 1 < doc.active_count() else new
            faces = {pid: fis for pid, fis in faces.items() if pid not in doc.hidden_parts}
        self._hl_cache[key] = (faces, polys)
        return faces, polys

    def _tree_plane_clicked(self, key):
        """A plane clicked in the feature list acts like clicking it in the graphics area."""
        kind, ident = key.split(":", 1)
        if kind == "std":
            pick = Pick("plane", ("std", ident))
        else:
            f = self.doc.feature(ident)
            if f is not None and self._tree_feature_to_dialog(ident):
                return
            if f is not None and f["type"] == "sketch" and self._tree_sketch_to_dialog(ident):
                return
            if f is None or f["type"] != "plane" or ident not in self.doc.state.planes:
                return
            pick = Pick("plane", ("plane", ident))
        if self.mode == "idle":
            # as in Onshape, a click in the list adds the plane to what's picked (or takes it out again)
            row_selected = any(it.data(ROLE_KIND) in ("std_plane", "feature") and
                               it.data(ROLE_ID) == ident and it.isSelected()
                               for it in self.left.features.selectedItems())
            others = [pk for pk in self.viewport.selection if pk != pick]
            self.viewport.set_selection(others + [pick] if row_selected else others)
        elif self.viewport.pick_handler is not None:
            # an open dialog (e.g. a new sketch waiting for its plane) takes the plane
            self.viewport.pick_handler(pick)

    def _tree_feature_to_dialog(self, fid):
        """A feature clicked in the list while a dialog wants features (feature pattern / mirror): add it, or
        take it out again. Returns True if the click was used."""
        d = self.dialog
        fld = getattr(d, "active_field", None) if d is not None else None
        if fld is None or "feature" not in fld.want or self.mode != "dialog":
            return False
        if fid == d.feature.get("id"):
            return True
        idx = self.doc.feature_index(fid)
        if idx is None or idx >= d.index:
            self.status.showMessage(tr("Pick a feature from above this one in the list."), 6000)
            return True
        fld.toggle(("feature", fid))
        return True

    def _tree_sketch_to_dialog(self, fid):
        """A sketch clicked in the feature list while a feature dialog wants sketch areas (Extrude, Loft …): use the
        sketch's closed areas, like Onshape. A loft profile is one area, so for Loft the sketch's biggest area (its
        outline, with any holes in it) is used. Returns True if the click was used."""
        d = self.dialog
        fld = getattr(d, "active_field", None) if d is not None else None
        if fld is None or "region" not in fld.want:
            return False
        sr = self.doc.state.sketches.get(fid)
        if sr is None or not sr.regions:
            self.status.showMessage("That sketch isn't a closed shape, so it has no area to use. Close its outline "
                                    "(the inside turns blue) and pick it again.", 8000)
            return True
        idx = list(range(len(sr.regions)))
        if d.feature.get("type") == "loft" and len(idx) > 1:
            idx = [max(idx, key=lambda i: K.face_info(sr.regions[i]).get("area", 0.0))]
        for i in idx:
            item = ("region", F.region_ref(sr, fid, sr.regions[i]))
            if not any(ex[0] == "region" and ex[1].get("sketch") == fid and ex[1] == item[1] for ex in fld.items):
                fld.toggle(item)
        return True

    def _tree_eye(self, key):
        """The eye in the feature list: show / hide a sketch or plane."""
        _kind, fid = key.split(":", 1)
        if self.doc is not None and hasattr(self.doc, "toggle_feature_hidden"):
            self.doc.toggle_feature_hidden(fid)

    def _tree_context(self, key, gpos):
        kind, fid = key.split(":", 1)
        if kind == "folder":
            self._folder_context(fid, gpos)
            return
        m = QMenu(self)
        if kind == "std_plane":
            m.addAction(tr("New sketch"), lambda: (self.viewport.set_selection([Pick("plane", ("std", fid))]),
                                               self.start_sketch()))
            m.addAction(tr("Normal to"), lambda: self.viewport.normal_to(K.std_plane(fid)))
            m.addAction(tr("Show") if fid in self.doc.hidden_features else "Hide",
                        lambda: self.doc.toggle_feature_hidden(fid))
            m.exec(gpos)
            return
        f = self.doc.feature(fid)
        if f is None or self.mode != "idle":
            return
        m.addAction(tr("Edit…"), lambda: self.edit_feature(fid))
        m.addAction(tr("Rename…"), lambda: self._rename_feature(fid))
        sel_ids = self.left.features.selected_feature_ids()
        copy_ids = sel_ids if fid in sel_ids else [fid]
        m.addAction(f"Copy{' ' + str(len(copy_ids)) + ' features' if len(copy_ids) > 1 else ''}\tCtrl+C",
                    lambda: self.copy_features(copy_ids))
        if self._clipboard_features() is not None:
            idx_after = self.doc.feature_index(fid) + 1
            m.addAction(tr("Paste after this\tCtrl+V"), lambda: self.paste_features(index=idx_after))
        if f["type"] in ("sketch", "plane"):
            sel = [i for i in self.left.features.selected_feature_ids()
                   if (self.doc.feature(i) or {}).get("type") in ("sketch", "plane")]
            vis = sel if fid in sel and len(sel) > 1 else [fid]
            hide = any(i not in self.doc.hidden_features for i in vis)
            n = f" {len(vis)} items" if len(vis) > 1 else ""
            m.addAction(("Hide" if hide else "Show") + n, lambda: self.doc.set_features_hidden(vis, hide))
        if f["type"] == "plane":
            m.addAction(tr("New sketch on plane"), lambda: (self.viewport.set_selection([Pick("plane", ("plane", fid))]),
                                                        self.start_sketch()))
        m.addSeparator()
        m.addAction("Unsuppress" if f.get("suppressed") else "Suppress",
                    lambda: self.doc.set_suppressed(fid, not f.get("suppressed")))
        idx = self.doc.feature_index(fid)
        m.addAction(tr("Roll to here"), lambda: (self.doc.checkpoint(), self.doc.set_rollback(idx + 1)))
        if self.doc.rollback is not None:
            m.addAction(tr("Roll to end"), lambda: (self.doc.checkpoint(), self.doc.set_rollback(None)))
        m.addSeparator()
        ids = self.left.features.selected_feature_ids() or [fid]
        if fid not in ids:
            ids = [fid]
        n = f" {len(ids)} features" if len(ids) > 1 else ""
        m.addAction(icon("new_folder", 16), f"Add{n} to new folder", lambda: self._new_folder(ids))
        if any((self.doc.feature(i) or {}).get("folder") for i in ids):
            m.addAction(f"Remove{n} from folder", lambda: self._remove_from_folder(ids))
        m.addSeparator()
        m.addAction(tr("Delete"), lambda: self._delete_features(ids))
        err = self.doc.errors.get(fid)
        if err:
            m.addSeparator()
            a = m.addAction("⚠ " + err)
            a.setEnabled(False)
        m.exec(gpos)

    def _rename_feature(self, fid):
        f = self.doc.feature(fid)
        name, ok = QInputDialog.getText(self, "Rename feature", "Name:", text=f["name"])
        if ok and name.strip():
            self.doc.rename_feature(fid, name.strip())

    # ================================================================== parts interactions
    def _part_clicked(self, pid, _):
        # (idle: the parts list's selection is the view's — see _parts_list_selection)
        if self.mode == "dialog" and self.dialog:
            self.dialog.handle_pick(Pick("part", (pid,)))

    def _parts_list_selection(self):
        """The parts list and the view pick the same parts, as in Onshape: a click in the list adds the part to (or
        takes it out of) the view's selection; other picks — faces, edges, planes — stay picked."""
        if self.mode != "idle" or self.is_dwg or self.is_asm:
            return
        pids = self.left.parts.selected_part_ids()
        keep = [pk for pk in self.viewport.selection if pk.kind != "part"]
        self.viewport.set_selection(keep + [Pick("part", (pid,)) for pid in pids])

    def _part_context(self, pid, gpos):
        p = self.doc.state.parts.get(pid)
        if p is None:
            return
        m = QMenu(self)
        ids = self._menu_parts(pid, self.left.parts.selected_part_ids())
        if len(ids) > 1:
            # several parts selected and one of them right-clicked: the menu acts on all of them, like Onshape
            self._add_hide_actions(m, ids)
            m.addAction(f"Isolate {len(ids)} parts", lambda: self._isolate(ids))
            m.addAction(f"Auto colour {len(ids)} parts", lambda: self._auto_color(ids))
            m.addAction(tr("Check interference"), lambda: self.check_interference(ids))
            m.addSeparator()
            m.addAction(f"Export {len(ids)} parts as STEP…", lambda: self.export(ids, "step"))
            m.addAction(f"Export {len(ids)} parts as STL…", lambda: self.export(ids, "stl"))
            m.addAction(f"Export {len(ids)} parts as BREP…", lambda: self.export(ids, "brep"))
            m.exec(gpos)
            return
        m.addAction(tr("Rename…"), lambda: self._rename_part(pid))
        m.addAction(tr("Change colour…"), lambda: self._recolor_part(pid))
        m.addAction(tr("Show") if pid in self.doc.hidden_parts else "Hide", lambda: self.doc.toggle_part_hidden(pid))
        m.addAction(tr("Isolate"), lambda: self._isolate(pid))
        m.addSeparator()
        m.addAction(tr("Mass properties"), lambda: self._mass_props(pid))
        m.addAction(tr("Shape info…"), lambda: self._shape_info(pid))
        if F.is_compound_part(self.doc.state.parts.get(pid)):
            m.addAction(icon("explode_part", 16), "Explode into solids", lambda: self._explode_part(pid))
        m.addAction(icon("drawing", 16), "Create drawing…", lambda: self.new_drawing(
            {"kind": "studio", "uid": self.doc.uid, "part": pid}))
        if pid in getattr(self.doc.state, "sheet", {}):
            m.addAction(icon("flat_dxf", 16), "Export flat pattern (DXF)…", lambda: self.export_flat_dxf(pid))
            m.addAction(icon("nest", 16), "Nest for cutting…", self.nest_for_cutting)
            m.addAction(icon("drawing", 16), "Create drawing of the flat pattern…", lambda: self.new_drawing(
                {"kind": "studio", "uid": self.doc.uid, "part": pid, "flat": True}))
        m.addAction(tr("Export STEP…"), lambda: self.export([pid], "step"))
        m.addAction(tr("Export STL…"), lambda: self.export([pid], "stl"))
        m.addAction(tr("Export BREP…"), lambda: self.export([pid], "brep"))
        m.exec(gpos)

    def _rename_part(self, pid):
        p = self.doc.state.parts[pid]
        name, ok = QInputDialog.getText(self, "Rename part", "Name:", text=p.name)
        if ok and name.strip():
            self.doc.set_part_override(pid, name=name.strip())

    def _recolor_part(self, pid):
        p = self.doc.state.parts[pid]
        c = QColorDialog.getColor(QColor.fromRgbF(*p.color), self, "Part colour")
        if c.isValid():
            self.doc.set_part_override(pid, color=[c.redF(), c.greenF(), c.blueF()])

    def _auto_color(self, pids):
        """Give each part a different colour. Doing it again moves along the palette, so a second go gives a
        new set of colours."""
        pids = [p for p in pids if p in self.doc.state.parts]
        if not pids:
            return
        self._auto_color_turn = getattr(self, "_auto_color_turn", -1) + 1
        cols = F.auto_colors(len(pids), shift=self._auto_color_turn * 5)
        self.doc.set_part_colors(dict(zip(pids, cols)))
        self.status.showMessage(f"Coloured {len(pids)} parts — choose Auto colour again for a different "
                                "set, or Undo to go back", 6000)

    def _isolate(self, pids):
        keep = {pids} if isinstance(pids, str) else set(pids)
        self.doc.hidden_parts = {p for p in self.doc.state.parts if p not in keep}
        self.doc.notify()

    def _menu_parts(self, pid, list_sel=()):
        """The parts a context menu opened on part `pid` should act on: the whole selection (in the parts list
        or the graphics area) when pid is part of it, otherwise just pid."""
        list_sel = [p for p in list_sel if p in self.doc.state.parts]
        if pid in list_sel and len(list_sel) > 1:
            return list_sel
        view_sel = []
        for pk in self.viewport.selection:
            if pk.kind in ("face", "edge", "part", "vertex") and pk.key[0] not in view_sel \
                    and pk.key[0] in self.doc.state.parts:
                view_sel.append(pk.key[0])
        return view_sel if pid in view_sel else [pid]

    def _menu_features(self, fid, kinds):
        """Sketches / planes a viewport context menu on `fid` acts on: all the selected ones if fid is selected."""
        sel = []
        for pk in self.viewport.selection:
            if pk.kind in kinds:
                f = pk.key[1] if pk.kind == "plane" else pk.key[0]
                if f not in sel:
                    sel.append(f)
        return sel if fid in sel else [fid]

    def check_interference(self, pids):
        """Right-click ▸ Check interference on several parts: where any two of them overlap, shown in red in the
        view (until the model next changes), with each pair and its overlap volume listed."""
        from ..interference import overlaps
        from PySide6.QtWidgets import QProgressDialog, QApplication
        parts = self.doc.state.parts
        pids = [p for p in pids if p in parts]
        if len(pids) < 2:
            return []
        prog = QProgressDialog("Checking for interference…", None, 0, 0, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(400)

        def step(n, total):
            prog.setMaximum(max(total, 1))
            prog.setValue(n)
            QApplication.processEvents()
        try:
            res = overlaps({p: parts[p].shape for p in pids}, step)
        finally:
            prog.close()
        ghosts = []
        for _a, _b, _v, ov in res:
            try:
                ghosts.append((K.tessellate(ov), (0.92, 0.15, 0.12, 0.55)))
            except Exception:
                pass
        self.viewport.ghosts = ghosts
        self._interference_state = self.doc.state if ghosts else None
        self.viewport.update()
        n = len(pids)
        if not res:
            self.status.showMessage(f"No interference between the {n} parts.", 6000)
            QMessageBox.information(self, tr("Check interference"), f"No interference — none of the {n} parts overlap.")
            return res
        lines = [f"• {parts[a].name}  ×  {parts[b].name}:  {fmt(v, 2)} mm³" for a, b, v, _ov in res]
        self.status.showMessage(f"{len(res)} interference(s) found — shown in red until the model changes.", 8000)
        QMessageBox.information(self, tr("Check interference"), f"{len(res)} pair(s) of parts overlap (shown in red):\n\n"
                                + "\n".join(lines[:30]))
        return res

    def _add_hide_actions(self, m, pids):
        """Hide / show for several parts: Hide if any of them is showing, Show if they are all hidden."""
        n = len(pids)
        what = f"{n} parts" if n > 1 else "part"
        if any(p not in self.doc.hidden_parts for p in pids):
            m.addAction(f"Hide {what}", lambda: self.doc.set_parts_hidden(pids, True))
        else:
            m.addAction(f"Show {what}", lambda: self.doc.set_parts_hidden(pids, False))

    def _show_all_parts(self):
        self.doc.hidden_parts = set()
        self.doc.notify()

    def _toggle_planes(self):
        """P / View ▸ Show default planes: hide the Top, Front and Right planes, or show them all again. It sets the
        same per-plane state as their eyes in the feature list, so the list, the eyes and the view always agree (the
        tick, already flipped by the time this runs, says which way to go)."""
        if self.doc is None or self.is_dwg or self.is_asm or not hasattr(self.doc, "set_features_hidden"):
            self.act_planes.setChecked(not self.act_planes.isChecked())    # nothing to hide here: undo the tick
            return
        self.doc.set_features_hidden(STD_PLANES, not self.act_planes.isChecked())

    def _sync_planes_action(self):
        """The Show default planes tick follows the planes: on while any of them is shown."""
        if self.doc is not None and hasattr(self.doc, "hidden_features"):
            self.act_planes.setChecked(any(n not in self.doc.hidden_features for n in STD_PLANES))

    def _explode_part(self, pid):
        """Right-click ▸ Explode into solids: open Explode with this part picked."""
        self.viewport.set_selection([Pick("part", (pid,))])
        self.start_feature("explode_part")

    def _shape_info(self, pid):
        """How the part is built — vertices, edges, wires, faces, shells, solids (shared ones counted once) — as
        SALOME's 'What is'. Shows whether the solids of a compound share their touching faces."""
        p = self.doc.state.parts[pid]
        n = K.topology_counts(p.shape)
        kind = "Compound of solids" if n["SOLID"] > 1 else "Solid" if n["SOLID"] else \
            "Surface" if n["FACE"] else "Curve"
        rows = "".join(f"<tr><td>{k}</td><td align=right>&nbsp;&nbsp;<b>{v}</b></td></tr>" for k, v in n.items())
        QMessageBox.information(self, tr("Shape info"), f"<b>{p.name}</b> — {kind}<br><br><table>{rows}</table>")

    def _mass_props(self, pid):
        p = self.doc.state.parts[pid]
        self._mass_props_shape(p.name, p.shape)

    def _mass_props_shape(self, name, shape):
        mp = K.mass_props(shape)
        lo, hi = K.bbox(shape)
        c = mp["centroid"]
        QMessageBox.information(
            self, tr("Mass properties"),
            f"<b>{name}</b><br><br>Volume: {U.volume(mp['volume'], 3)}<br>"
            f"Surface area: {U.area(mp['area'], 3)}<br>"
            f"Mass (PLA, 1.24 g/cm³): {fmt(mp['volume'] * 1.24e-3, 2)} g<br>"
            f"Mass (aluminium, 2.7 g/cm³): {fmt(mp['volume'] * 2.7e-3, 2)} g<br>"
            f"Centre of mass: ({U.num(c[0], 3)}, {U.num(c[1], 3)}, {U.num(c[2], 3)}) {U.unit()}<br>"
            f"Bounding box: {U.num(hi[0] - lo[0], 3)} × {U.num(hi[1] - lo[1], 3)} × {U.num(hi[2] - lo[2], 3)} "
            f"{U.unit()}")

    # ================================================================== viewport interactions
    def _hover_changed(self, pk):
        if self.mode == "idle":
            self.status.showMessage(pk.label() if pk else "", 0)

    def _viewport_double(self, pk):
        if self.mode != "idle" or pk is None or self.is_asm:
            return
        if pk.kind in ("region", "sketch_line"):
            self.edit_feature(pk.key[0])
        elif pk.kind == "plane" and pk.key[0] == "plane":
            self.edit_feature(pk.key[1])

    def _viewport_context(self, pk, gpos):
        if self.is_asm:
            if self.mode == "idle":
                self.asm.viewport_menu(pk, gpos)
            return
        m = QMenu(self)
        if self.mode == "sketch":
            m.addAction(tr("Normal to sketch"), self._normal_to_sketch)
            m.addAction(tr("Finish sketch"), self.finish_sketch)
            m.addAction(tr("Cancel sketch"), self.cancel_sketch)
            m.exec(gpos)
            return
        if self.mode != "idle":
            return
        if pk is not None:
            lp = self.viewport.mapFromGlobal(gpos)
            other = self._select_other_menu(lp.x(), lp.y(), m)
            if other is not None:
                m.addMenu(other)
            if pk.kind in ("face", "edge") and pk.key[0] in self.doc.state.parts:
                self._add_selection_tools(m, pk)
            m.addSeparator()
            if pk.kind in ("plane", "face"):
                item = pick_to_item(self.viewport, pk, {"plane"})
                if item:
                    m.addAction(tr("New sketch"), lambda: (self.viewport.set_selection([pk]), self.start_sketch()))

                    def nt(item=item):
                        try:
                            self.viewport.normal_to(F.resolve_plane(self.doc.state, item[1]))
                        except K.KernelError:
                            pass
                    m.addAction(tr("Normal to"), nt)
                if pk.kind == "plane" and self.doc.plane_sizes.get(self.doc.plane_key(pk.key)):
                    m.addAction(tr("Reset plane size"), lambda: self.doc.set_plane_rect(pk.key, None))
            if pk.kind in ("face", "edge", "part", "vertex"):
                pid = pk.key[0]
                ids = self._menu_parts(pid)
                self._add_hide_actions(m, ids)
                if len(ids) > 1:
                    m.addAction(f"Isolate {len(ids)} parts", lambda: self._isolate(ids))
                    m.addAction(f"Auto colour {len(ids)} parts", lambda: self._auto_color(ids))
                    m.addAction(tr("Check interference"), lambda: self.check_interference(ids))
                else:
                    m.addAction(tr("Isolate part"), lambda: self._isolate(pid))
                    m.addAction(tr("Select part"), lambda: self.viewport.set_selection([Pick("part", (pid,))]))
                    m.addAction(tr("Mass properties"), lambda: self._mass_props(pid))
                    m.addAction(tr("Shape info…"), lambda: self._shape_info(pid))
            if pk.kind in ("region", "sketch_line"):
                fid = pk.key[0]
                m.addAction(tr("Edit sketch"), lambda: self.edit_feature(fid))
                fids = self._menu_features(fid, ("region", "sketch_line"))
                m.addAction(f"Hide {len(fids)} sketches" if len(fids) > 1 else "Hide sketch",
                            lambda: self.doc.set_features_hidden(fids, True))
            if pk.kind == "plane":
                hid = pk.key[1]
                fids = self._menu_features(hid, ("plane",))
                m.addAction(f"Hide {len(fids)} planes" if len(fids) > 1 else "Hide plane",
                            lambda: self.doc.set_features_hidden(fids, True))
            m.addSeparator()
        m.addAction(tr("Zoom to fit"), lambda: self.viewport.fit())
        vm = submenu(m, "Views")
        for name in ("Front", "Back", "Left", "Right", "Top", "Bottom", "Isometric"):
            vm.addAction(name, lambda _=False, n=name: self.viewport.set_standard_view(n))
        if self.doc.hidden_parts:
            m.addAction(tr("Show all parts"), self._show_all_parts)
        if len(self.doc.state.parts) > 1:
            m.addAction(tr("Auto colour all parts"), lambda: self._auto_color(list(self.doc.state.parts)))
        m.exec(gpos)

    def _selection_changed(self):
        if self.mode != "idle":
            return
        sel = self.viewport.selection
        if not self.is_asm and not self.is_dwg:
            # the parts list shows the parts picked in the view; nothing picked (Esc, a click on empty space)
            # clears the feature list's selection too, as in Onshape
            self.left.parts.select_parts(pk.key[0] for pk in sel if pk.kind == "part")
            if not sel:
                self.left.features.blockSignals(True)
                self.left.features.clearSelection()
                self.left.features.blockSignals(False)
                self.left.features.viewport().update()
                self._update_feature_hl()
            # and, as Onshape does, the feature that made what's picked is marked in the feature list
            made_by = set()
            for pk in sel[:50]:
                try:
                    fid = self._feature_of_pick(pk)
                except Exception:
                    fid = None
                if fid:
                    made_by.add(fid)
            self.left.features.mark_related(made_by)
        if self.is_asm:
            self.asm.panel.select_instances({pk.key[0] for pk in sel if pk.kind in ("face", "edge", "part")})
        text = self._measure_text(sel)
        if not sel:
            self.viewport.measure_line = None
            self.viewport.update()
        if text:
            head = None if self.is_asm else self._measure_headline(sel)
            self.measure.set_measure(head, text)
            self.measure.move(self.viewport.width() - self.measure.width() - 14,
                              self.viewport.height() - self.measure.height() - 14)
            self.measure.show()
            self.measure.raise_()
        else:
            self.measure.hide()

    def _measure_headline(self, sel):
        """(label, value) for the corner bar — the measurement its (x) button turns into a variable — or None."""
        from .. import measure_value as MV
        from .dialogs import pick_to_item
        want = {"vertex", "edge", "face", "part", "region", "sketch_line"}
        items = []
        try:
            for pk in sel:
                it = pick_to_item(self.viewport, pk, want)
                if it is None:
                    return None
                items.append(it)
            h = MV.headline(self.doc.state, items)
        except Exception:
            return None
        return (h[0], h[1]) if h else None

    def _sub(self, pk):
        if pk.kind in ("sketch_point", "sketch_line"):
            for fid, sr, _ in self.viewport.sketches:
                if fid != pk.key[0]:
                    continue
                if pk.kind == "sketch_point":
                    from ..measure_value import _vertex
                    return _vertex(sr.plane.to_world(*sr.sketch.points[pk.key[1]])) \
                        if pk.key[1] in sr.sketch.points else None
                from types import SimpleNamespace
                try:
                    return F.resolve_sketch_curve(SimpleNamespace(sketches={fid: sr}),
                                                  {"sketch": fid, "entity": pk.key[1]})[1]
                except K.KernelError:
                    return None
            return None
        shape = self.viewport.part_shapes.get(pk.key[0])
        if shape is None:
            return None
        if pk.kind == "vertex":
            from ..measure_value import _vertex
            pts = K.vertex_points(shape)
            return _vertex(pts[pk.key[1]]) if pk.key[1] < len(pts) else None
        if pk.kind == "face":
            return K.faces(shape)[pk.key[1]]
        if pk.kind == "edge":
            return K.edges(shape)[pk.key[1]]
        if pk.kind == "part":
            return shape
        return None

    def _measure_text(self, sel):
        from .. import measure as MEAS
        items, names = [], []
        for pk in sel:
            if pk.kind in ("sketch_point", "sketch_line"):
                shp = self._sub(pk)
                if shp is not None:
                    from OCP.TopAbs import TopAbs_VERTEX
                    items.append(("vertex" if shp.ShapeType() == TopAbs_VERTEX else "edge", shp))
                    names.append(None)
            elif pk.kind in ("face", "edge", "part", "vertex"):
                items.append((pk.kind, self._sub(pk)))
                p = self.doc.state.parts.get(pk.key[0])
                if self.is_asm and pk.kind == "part":
                    inst = self.doc.instance(pk.key[0])
                    names.append(inst["name"] if inst else None)
                else:
                    names.append(p.name if p and pk.kind == "part" else None)
            elif pk.kind == "region":
                for fid, sr, _ in self.viewport.sketches:
                    if fid == pk.key[0] and pk.key[1] < len(sr.regions):
                        items.append(("region", sr.regions[pk.key[1]]))
                        names.append(None)
        if not items:
            self.viewport.measure_line = None
            return f"{len(sel)} items selected" if sel else ""
        try:
            m = MEAS.measure(items, names)
        except Exception:
            self.viewport.measure_line = None
            return ""
        self.viewport.measure_line = m.get("line") if m else None
        self.viewport.update()
        return MEAS.to_html(m)

    # ================================================================== files
    @staticmethod
    def _has_content(doc):
        return bool(doc.features) or bool(getattr(doc, "instances", None)) or bool(getattr(doc, "views", None)) \
            or bool(getattr(doc, "annots", None))

    def _confirm_discard(self, doc=None):
        doc = doc or self.doc
        if not doc.dirty or not self._has_content(doc):
            return True
        r = QMessageBox.question(self, APP_NAME, f"Save changes to “{doc.name}” first?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    def _leave_modes(self):
        if self.is_dwg:
            self.dwg.leave()
            self.mode = "idle"
            return
        if self.is_asm:
            self.asm.leave()
            self.mode = "idle"
            return
        if self.mode == "dialog":
            self.cancel_dialog()
        elif self.mode == "sketch":
            self.cancel_sketch()

    # ------------------------------------------------------------------ tabs
    # ------------------------------------------------------------------ units
    def default_units(self):
        from .. import units as U
        u = str(self.settings.value("default_units", "mm"))
        return u if u in U.FACTORS else "mm"

    def _apply_units(self):
        """Lengths are shown and typed in the unit of the document on screen."""
        from .. import units as U
        u = getattr(self.doc, "units", "mm") if self.doc is not None and self.doc.kind != "drawing" else "mm"
        U.set_unit(u)
        self.units_label.setText(tr("Units: {unit} ▾").format(unit=tr(U.NAMES[U.unit()])))
        self.units_label.setEnabled(self.doc is not None and self.doc.kind != "drawing")

    def _fill_units_menu(self, m):
        from PySide6.QtGui import QActionGroup
        from .. import units as U
        m.clear()
        m.addSection(tr("This document"))
        grp = QActionGroup(m)
        cur = getattr(self.doc, "units", "mm")
        for u in U.ORDER:
            a = m.addAction(f"{tr(U.NAMES[u]).capitalize()} ({u})")
            a.setCheckable(True)
            a.setChecked(u == cur)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, uu=u: self.set_doc_units(uu))
        m.addSection(tr("New documents"))
        dm = submenu(m, tr("Default: {unit}").format(unit=tr(U.NAMES[self.default_units()])))
        for u in U.ORDER:
            a = dm.addAction(tr(U.NAMES[u]).capitalize())
            a.setCheckable(True)
            a.setChecked(u == self.default_units())
            a.triggered.connect(lambda _=False, uu=u: self.settings.setValue("default_units", uu))

    def set_doc_units(self, u):
        """Change the unit this document shows and takes lengths in. The model doesn't change: a 25.4 mm block is
        a 1 in block."""
        if self.doc is None or self.doc.kind == "drawing" or getattr(self.doc, "units", "mm") == u:
            return
        self._leave_modes()
        self.doc.units = u
        self.doc.dirty = True
        self._apply_units()
        if self.doc.kind == "studio":
            self._populate_lists()
        self._refresh_tab_title(self.doc)
        self.measure.hide()
        self.viewport.update()

    def _new_document(self, name=None):
        self._studio_counter += 1
        doc = Document()
        doc.units = self.default_units()
        doc.name = name or f"Part Studio {self._studio_counter}"
        doc.rebuild()
        return doc

    def _tab_index(self, doc):
        for i in range(self.tabs.count()):
            if self.docs.get(self.tabs.tabData(i)) is doc:
                return i
        return -1

    def _refresh_tab_title(self, doc):
        i = self._tab_index(doc)
        if i >= 0:
            self.tabs.setTabText(i, doc.name + (" •" if doc.dirty else ""))
            self.tabs.setTabToolTip(i, (doc.path or doc.name) + "\nDouble-click to rename · right-click or middle-click to close")

    def _add_tab(self, doc, switch=True):
        if switch and hasattr(self, "main_stack"):
            self.show_workspace()
        key = id(doc)
        self.docs[key] = doc
        doc.listeners.append(lambda d=doc: self._doc_changed_from(d))
        if doc.kind == "assembly":
            doc.resolver = self._resolve_source
            doc.asm_resolver = self._resolve_assembly
        if doc.kind == "drawing":
            doc.resolver = self._resolve_drawing_source
            doc.hlr_async = self.dwg.request_exact
            doc.bom_resolver = self._drawing_bom
            doc._update_bom()
            doc.changed(notify=False)
        i = self.tabs.addTab(icon({"assembly": "assembly", "drawing": "drawing"}.get(doc.kind, "part"), 16), doc.name)
        self.tabs.setTabData(i, key)
        if not switch:
            self._refresh_tab_title(doc)
            return
        if self.tabs.currentIndex() == i:
            self._switch_tab(i)
        else:
            self.tabs.setCurrentIndex(i)
        self._refresh_tab_title(doc)

    def _doc_changed_from(self, doc):
        if doc is self.doc:
            self._on_doc_changed()
        else:
            self._refresh_tab_title(doc)
        self._update_derived_from(doc)

    # ------------------------------------------------------------------ derived parts
    def _derived_link(self, params):
        """The parts a derived feature brings in: [(name, shape, colour, face colours)]."""
        studio = self.studio_by_uid(params.get("uid"), params.get("path"), load=True)
        if studio is None:
            raise K.KernelError(f"Part Studio “{params.get('studio_name') or '?'}” isn't open")
        if getattr(self, "_deriving", None) and studio.uid in self._deriving:
            raise K.KernelError("A Part Studio can't derive from itself (directly or round a loop)")
        st = studio.state_for(params.get("config"))
        want = [studio.part_id(w) if hasattr(studio, "part_id") else w for w in (params.get("parts") or ["*"])]
        parts = [p for p in st.parts.values() if "*" in want or p.id in want]
        return [(p.name, p.shape, p.color, p.face_colors) for p in parts]

    def _derived_source_stamp(self, params, for_save=False):
        """Identifies the saved source file of a derived feature (path + modification time), so a document can
        use the copy of the parts saved in it rather than load and rebuild the whole source when nothing changed.
        None when the saved copy can't be trusted: the source is open in a tab (live or with unsaved edits)."""
        uid, path = params.get("uid"), params.get("path")
        studios = self.studio_docs()
        doc = next((d for d in studios if uid and d.uid == uid), None)
        if doc is None and path:
            ap = os.path.abspath(path)
            doc = next((d for d in studios if d.path and os.path.abspath(d.path) == ap), None)
        if doc is not None:
            if not for_save or doc.dirty or not doc.path or not os.path.exists(doc.path):
                return None
            ap = os.path.abspath(doc.path)
            return f"{ap}|{os.path.getmtime(ap)!r}"
        if not path or not os.path.exists(path):
            return None
        ap = os.path.abspath(path)
        if for_save:
            hit = self.__dict__.get("_linked_docs", {}).get(ap)
            if hit is not None:                       # the parts came from this load of the file
                return f"{ap}|{hit[0]!r}"
        return f"{ap}|{os.path.getmtime(ap)!r}"

    def _update_derived_from(self, src_doc):
        """A Part Studio changed: rebuild the ones that derive parts from it."""
        if getattr(src_doc, "kind", None) != "studio" or getattr(self, "_derive_busy", False):
            return
        self._derive_busy = True
        try:
            for d in self.studio_docs():
                if d is src_doc:
                    continue
                if any(f.get("type") == "derived" and f["params"].get("uid") == src_doc.uid for f in d.features):
                    self._deriving = {d.uid}
                    try:
                        d.rebuild()
                    finally:
                        self._deriving = None
        finally:
            self._derive_busy = False

    def _derived_dialog(self, edit_fid=None):
        from PySide6.QtWidgets import QDialog, QFormLayout, QDialogButtonBox, QListWidget, QListWidgetItem
        doc = self.doc
        if doc is None or self.mode != "idle":
            return
        others = [d for d in self.studio_docs() if d is not doc and d.state.parts]
        f = doc.feature(edit_fid) if edit_fid else None
        if not others and f is None:
            QMessageBox.information(self, APP_NAME, "Open (or make) the other Part Studio first — derived parts come "
                                    "from another Part Studio tab or file.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Derived parts"))
        form = QFormLayout(dlg)
        studio = QComboBox()
        for d in others:
            studio.addItem(d.name, d.uid)
        cfg = QComboBox()
        parts = QListWidget()
        parts.setSelectionMode(QListWidget.ExtendedSelection)
        form.addRow(tr("From:"), studio)
        cfg_label = QLabel(tr("Configuration:"))
        form.addRow(cfg_label, cfg)
        form.addRow(tr("Parts (none picked = all):"), parts)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        def fill():
            d = self.studio_by_uid(studio.currentData())
            cfg.blockSignals(True)
            cfg.clear()
            names = d.config_names() if d else []
            for n in names:
                cfg.addItem(n, n)
            if names:
                cfg.setCurrentIndex(names.index(d.active_config()))
            cfg.setVisible(bool(names))
            cfg_label.setVisible(bool(names))
            cfg.blockSignals(False)
            fill_parts()

        def fill_parts():
            parts.clear()
            d = self.studio_by_uid(studio.currentData())
            if d is None:
                return
            for p in d.state_for(cfg.currentData()).parts.values():
                it = QListWidgetItem(p.name)
                it.setData(Qt.UserRole, p.id)
                parts.addItem(it)
        studio.currentIndexChanged.connect(fill)
        cfg.currentIndexChanged.connect(fill_parts)
        fill()
        if f is not None:
            i = studio.findData(f["params"].get("uid"))
            if i >= 0:
                studio.setCurrentIndex(i)
                fill()
            if f["params"].get("config"):
                cfg.setCurrentIndex(max(0, cfg.findData(f["params"]["config"])))
            for k in range(parts.count()):
                parts.item(k).setSelected(parts.item(k).data(Qt.UserRole) in f["params"].get("parts", []))
        if dlg.exec() != QDialog.Accepted or studio.currentData() is None:
            return
        src = self.studio_by_uid(studio.currentData())
        picked = [it.data(Qt.UserRole) for it in parts.selectedItems()] or ["*"]
        params = {"uid": src.uid, "path": src.path or "", "studio_name": src.name, "parts": picked,
                  "config": cfg.currentData() if cfg.count() else None}
        if f is None:
            feat = doc.make_feature("derived")
            feat["params"] = params
            feat["name"] = "Derived from " + src.name + (f" ({params['config']})" if params["config"] else "")
            self._deriving = {doc.uid}
            try:
                doc.add_feature(feat)
            finally:
                self._deriving = None
            err = doc.errors.get(feat["id"])
        else:
            feat = dict(f, params=params)
            doc.replace_feature(feat)
            err = doc.errors.get(feat["id"])
        if err:
            QMessageBox.warning(self, APP_NAME, err)

    def _is_pristine(self, doc):
        return doc is not None and not self._has_content(doc) and not doc.path and not doc.dirty

    def _fresh_view(self):
        """The first view of a document: isometric, fitted, with nothing for Previous view to go back to. It runs
        a moment after a tab opens; if the view is already on its way somewhere (a sketch opened straight away
        turns to face it) or a sketch is open, it leaves the view alone rather than snapping it back."""
        if self.viewport._anim is not None or self.mode == "sketch":
            return
        self.viewport.set_standard_view("Isometric", animate=False)
        self.viewport.reset_view_history()

    def _switch_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        if doc is None or doc is self.doc:
            return
        self.viewport.reset_view_history()            # Previous view stays within one document
        if getattr(self, "history_panel", None) is not None:
            self.history_panel.close_panel()
        self._leave_modes()
        if self.section_panel is not None:
            self.toggle_section()
        if self.doc is not None:
            self._update_thumbnail()
            if self.doc.kind != "drawing":
                self._views[id(self.doc)] = self.viewport.cam.state()
        self.doc = doc
        self._apply_units()
        self.viewport.clear_selection()
        self.measure.hide()
        asm = doc.kind == "assembly"
        if doc.kind == "drawing":
            self.tool_stack.setCurrentIndex(3)
            self.left_stack.setCurrentIndex(2)
            self.view_stack.setCurrentIndex(1)
            self.viewport.drag_handler = None
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                doc.refresh(notify=False)          # pick up edits made to the model meanwhile
            finally:
                QApplication.restoreOverrideCursor()
            self.dwg.activate()
            self._on_doc_changed()
            return
        self.view_stack.setCurrentIndex(0)
        self.tool_stack.setCurrentIndex(2 if asm else 0)
        self.left_stack.setCurrentIndex(1 if asm else 0)
        self.viewport.drag_handler = self.asm if asm else None
        self.viewport.triads = []
        self.viewport.ghosts = []
        if asm:
            doc.refresh(notify=False)          # pick up edits made in the Part Studios meanwhile
        self._on_doc_changed()
        saved = self._views.get(id(doc))
        if saved is not None:
            self.viewport.cam.set_state(saved)
            self.viewport.update()
        else:
            QTimer.singleShot(0, self._fresh_view)

    def close_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        if doc is None:
            return
        if doc is not self.doc:
            self.tabs.setCurrentIndex(i)
        self._leave_modes()
        if not self._confirm_discard(doc):
            return
        if self.tabs.count() == 1:
            self._add_tab(self._new_document())        # always keep one tab open
            i = self._tab_index(doc)
        key = self.tabs.tabData(i)
        self.tabs.removeTab(i)
        self._drop_autosave(self.docs.get(key))
        self.docs.pop(key, None)
        self._views.pop(key, None)

    def _rename_tab(self, i):
        if i < 0:
            return
        doc = self.docs.get(self.tabs.tabData(i))
        name, ok = QInputDialog.getText(self, "Rename tab", "Name:", text=doc.name)
        if ok and name.strip():
            doc.name = name.strip()
            doc.dirty = True
            doc.notify()

    def _tab_context(self, pos):
        i = self.tabs.tabAt(pos)
        if i < 0:
            return
        m = QMenu(self)
        m.addAction(tr("Rename…"), lambda: self._rename_tab(i))
        m.addAction(tr("Duplicate"), lambda: self._duplicate_tab(i))
        m.addSeparator()
        m.addAction(tr("Close"), lambda: self.close_tab(i))
        m.exec(self.tabs.mapToGlobal(pos))

    def _duplicate_tab(self, i):
        src = self.docs.get(self.tabs.tabData(i))
        if src is None:
            return
        import json
        if src.kind == "drawing":
            g = DrawingDoc()
            d = json.loads(src.to_json(embed=False))
            g.source, g.sheet, g.title = d["source"], d["sheet"], d["title"]
            g.views, g.annots = d["views"], d["annots"]
            g._counter = src._counter
            g._fallback = src._fallback
            g.name = src.name + " (copy)"
            g.resolver = self._resolve_drawing_source
            g.refresh(notify=False)
            g.dirty = True
            self._add_tab(g)
            return
        if src.kind == "assembly":
            d = json.loads(src.to_json(embed=False))
            a = AssemblyDoc()
            a.instances, a.mates, a.blobs = d["instances"], d["mates"], dict(src.blobs)
            a.subs = d.get("subs", [])
            a.relations, a.patterns = d.get("relations", []), d.get("patterns", [])
            a._counter = src._counter
            a.name = src.name + " (copy)"
            a.resolver = self._resolve_source
            a.refresh(solve=False, notify=False)
            a.dirty = True
            self._add_tab(a)
            return
        doc = Document()
        d = json.loads(src.to_json())
        doc.features, doc.rollback = d["features"], d["rollback"]
        doc.part_overrides, doc.hidden_parts = d["overrides"], set(d["hidden"])
        doc.hidden_features = set(d.get("hidden_features", []))
        doc._counter = src._counter
        doc.name = src.name + " (copy)"
        doc.rebuild()
        doc.dirty = True
        self._add_tab(doc)

    def new_doc(self):
        self._leave_modes()
        self._add_tab(self._new_document())

    def new_assembly(self):
        self._leave_modes()
        self._asm_counter = getattr(self, "_asm_counter", 0) + 1
        a = AssemblyDoc()
        a.name = f"Assembly {self._asm_counter}"
        a.units = self.default_units()
        a.resolver = self._resolve_source
        self._add_tab(a)
        QTimer.singleShot(0, self._fresh_view)
        if self.studio_docs() and any(d.state.parts for d in self.studio_docs()):
            QTimer.singleShot(50, self.asm.toggle_insert)

    def drawing_sources(self):
        """Everything a drawing can show: each open Part Studio (all parts, or one part) and each Assembly."""
        out = []
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is None:
                continue
            if d.kind == "studio" and d.state.parts:
                for cfg in (d.config_names() or [None]):
                    st = d.state_for(cfg)
                    base = {"kind": "studio", "uid": d.uid, "path": d.path, "studio_name": d.name}
                    if cfg:
                        base["config"] = cfg
                    entries = []
                    if len(st.parts) > 1:
                        entries.append(("All parts", {**base, "part": "*"}))
                    for p in st.parts.values():
                        entries.append((p.name, {**base, "part": p.id, "part_name": p.name}))
                        if p.id in getattr(st, "sheet", {}):
                            entries.append((p.name + " — flat pattern", {**base, "part": p.id, "part_name": p.name,
                                                                          "flat": True}))
                    out.append((d.name + (f" · {cfg}" if cfg else ""), "part", entries))
            elif d.kind == "assembly" and d.instances:
                out.append((d.name, "assembly", [("Whole assembly", {"kind": "assembly", "uid": d.uid,
                                                                      "path": d.path, "name": d.name})]))
        return out

    def new_drawing(self, preselect=None):
        """Create a Drawing tab of a part, a Part Studio or an Assembly."""
        self._leave_modes()
        from .drawing_ui import NewDrawingDialog
        sources = self.drawing_sources()
        if not sources:
            QMessageBox.information(self, APP_NAME, "Make (or open) a part or an assembly first — a drawing shows "
                                    "one of your open Part Studios or Assemblies.")
            return
        if preselect is None and self.doc is not None and self.doc.kind in ("studio", "assembly"):
            preselect = {"kind": self.doc.kind, "uid": self.doc.uid, "part": "*"}
            if self.doc.kind == "studio" and len(self.doc.state.parts) == 1:
                preselect["part"] = next(iter(self.doc.state.parts))
        dlg = NewDrawingDialog(self, sources, preselect, self.settings)
        if dlg.exec() != QDialog.Accepted:
            return
        src = dlg.chosen()
        if src is None:
            return
        if preselect and preselect.get("exploded") and src.get("kind") == "assembly":
            src = dict(src, exploded=True)
        g = DrawingDoc()
        self._dwg_counter = getattr(self, "_dwg_counter", 0) + 1
        g.name = (src.get("part_name") or src.get("name") or src.get("studio_name") or f"Drawing {self._dwg_counter}")
        if src.get("exploded"):
            g.name += " exploded"
        if src.get("flat"):
            g.name += " flat pattern"
        g.source = dict(src)
        g.sheet.update(size=dlg.size.currentData(), portrait=bool(dlg.orient.currentData()),
                       projection=dlg.proj.currentData())
        g.title.update(drawn=dlg.drawn.text().strip(), company=dlg.company.text().strip(), title=g.name)
        for k, v in (("dwg_size", g.sheet["size"]), ("dwg_proj", g.sheet["projection"]),
                     ("dwg_drawn", g.title["drawn"]), ("dwg_company", g.title["company"])):
            self.settings.setValue(k, v)
        g.resolver = self._resolve_drawing_source
        g.hlr_async = self.dwg.request_exact
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            g.refresh(notify=False)
            if dlg.views.isChecked():
                g.auto_layout()
        finally:
            QApplication.restoreOverrideCursor()
        g.dirty = True
        self._add_tab(g)

    def _drawing_assembly(self, src):
        asm = next((d for d in self.docs.values() if d.kind == "assembly" and d.uid == src.get("uid")), None)
        if asm is None and src.get("path"):
            ap = os.path.abspath(src["path"])
            asm = next((d for d in self.docs.values() if d.kind == "assembly" and d.path
                        and os.path.abspath(d.path) == ap), None)
            if asm is None and os.path.exists(ap):
                asm = AssemblyDoc.load(ap, resolver=self._resolve_source)
        if asm is None:
            raise K.KernelError(f"Assembly “{src.get('name', '?')}” isn't open")
        return asm

    def _drawing_bom(self, src):
        """A drawing's parts list: one row per different part with how many are used, and where they are."""
        if src.get("kind") == "assembly":
            asm = self._drawing_assembly(src)
            rows, order = {}, []
            for inst in asm.visible_instances():
                if inst["id"] not in asm.parts:
                    continue
                key = asm._source_key(inst["source"])
                if key not in rows:
                    nm = inst.get("part_name") or inst["name"]
                    if inst["source"].get("config"):
                        nm += f" ({inst['source']['config']})"
                    rows[key] = {"name": nm, "qty": 0, "shapes": []}
                    order.append(key)
                rows[key]["qty"] += 1
                rows[key]["shapes"].append(K.located(asm.parts[inst["id"]].shape,
                                                     asm.display_T(inst["id"], exploded=bool(src.get("exploded")),
                                                                   amount=1.0)))
            return [rows[k] for k in order]
        studio = self.studio_by_uid(src.get("uid"), src.get("path"), load=True)
        if studio is None or src.get("flat"):
            return []
        st = studio.state_for(src.get("config"))
        if src.get("part") in (None, "*"):
            parts = [p for p in st.parts.values() if p.id not in studio.hidden_parts]
        else:
            pid = studio.part_id(src["part"]) if hasattr(studio, "part_id") else src["part"]
            parts = [st.parts[pid]] if pid in st.parts else []
        return [{"name": p.name, "qty": 1, "shapes": [p.shape]} for p in parts]

    def _resolve_drawing_source(self, src):
        """The shape a drawing shows (kept as the same object while the model is unchanged, so views aren't
        worked out again for nothing)."""
        cache = self.__dict__.setdefault("_dwg_shapes", {})
        if src.get("kind") == "assembly":
            asm = self._drawing_assembly(src)
            src["name"] = asm.name
            if asm.path:
                src["path"] = os.path.abspath(asm.path)
            ex = bool(src.get("exploded"))
            items = [(id(asm.parts[i["id"]].shape),
                      tuple(np.round(asm.display_T(i["id"], exploded=ex, amount=1.0).ravel(), 9)))
                     for i in asm.visible_instances() if i["id"] in asm.parts]
            key = ("asm", src.get("uid"), ex, tuple(items))
            if key not in cache:
                cache[key] = K.compound([K.located(asm.parts[i["id"]].shape,
                                                   asm.display_T(i["id"], exploded=ex, amount=1.0))
                                         for i in asm.visible_instances() if i["id"] in asm.parts])
            return cache[key]
        studio = self.studio_by_uid(src.get("uid"), src.get("path"), load=True)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        src["studio_name"] = studio.name
        src["uid"] = studio.uid
        if studio.path:
            src["path"] = os.path.abspath(studio.path)
        if src.get("part") in (None, "*"):
            parts = [p for p in studio.state_for(src.get("config")).parts.values() if p.id not in studio.hidden_parts]
            if not parts:
                raise K.KernelError(f"“{studio.name}” has no parts")
            if len(parts) == 1:
                return parts[0].shape
            key = ("studio", studio.uid, src.get("config"), tuple(id(p.shape) for p in parts))
            if key not in cache:
                cache[key] = K.compound([p.shape for p in parts])
            return cache[key]
        src["part"] = studio.part_id(src["part"])          # (an id from before part ids were per feature)
        p = studio.state_for(src.get("config")).parts.get(src["part"])
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        src["part_name"] = p.name
        if src.get("flat"):
            if p.id not in getattr(studio.state_for(src.get("config")), "sheet", {}):
                raise K.KernelError(f"“{p.name}” isn't sheet metal any more")
            fp = self.flat_pattern_of(studio.state_for(src.get("config")), p.id)
            # bend lines go on the drawing too (kept in the source, so they're there when reopened)
            src["bends"] = [[list(map(float, a)), list(map(float, b)), lab] for a, b, lab in fp.bend_lines]
            key = ("flat", studio.uid, src.get("config"), p.id, id(p.shape))
            if key not in cache:
                cache[key] = fp.solid()
            return cache[key]
        return p.shape

    # ------------------------------------------------------------------ links between tabs
    def assembly_docs(self):
        return [d for d in (self.docs.get(self.tabs.tabData(i)) for i in range(self.tabs.count()))
                if d is not None and d.kind == "assembly"]

    def _resolve_assembly(self, source):
        """The assembly a sub-assembly comes from: its open tab (by id, then by file), else its file (loaded once
        and kept while the file is unchanged). None if it can't be found."""
        uid, path = source.get("uid"), source.get("path")
        open_ = self.assembly_docs()
        doc = next((d for d in open_ if uid and d.uid == uid), None)
        if doc is None and path:
            ap = os.path.abspath(path)
            doc = next((d for d in open_ if d.path and os.path.abspath(d.path) == ap), None)
            if doc is None and os.path.exists(ap):
                cache = self.__dict__.setdefault("_asm_file_cache", {})
                stamp = os.path.getmtime(ap)
                hit = cache.get(ap)
                if hit is not None and hit[0] == stamp:
                    return hit[1]
                depth = self.__dict__.get("_asm_load_depth", 0)
                if depth > 8:
                    return None                       # (a file loop: give up)
                self._asm_load_depth = depth + 1
                try:
                    doc = AssemblyDoc.load(ap, resolver=self._resolve_source)
                    doc.asm_resolver = self._resolve_assembly
                    doc.refresh(solve=False, notify=False)
                finally:
                    self._asm_load_depth = depth
                cache[ap] = (stamp, doc)
        return doc

    def studio_docs(self):
        out = []
        for i in range(self.tabs.count()):
            d = self.docs.get(self.tabs.tabData(i))
            if d is not None and d.kind == "studio":
                out.append(d)
        return out

    def studio_by_uid(self, uid, path=None, load=False, show=False):
        """The Part Studio an assembly instance links to: an open tab (by id, then by file), else the file."""
        studios = self.studio_docs()
        doc = next((d for d in studios if uid and d.uid == uid), None)
        if doc is None and path:
            ap = os.path.abspath(path)
            doc = next((d for d in studios if d.path and os.path.abspath(d.path) == ap), None)
        if doc is not None:
            if show:
                self.tabs.setCurrentIndex(self._tab_index(doc))
            return doc
        if not (load and path and os.path.exists(path)):
            return None
        if show:
            self.open_file(path)
            return self.studio_by_uid(uid, path)
        cache = self.__dict__.setdefault("_linked_docs", {})
        ap = os.path.abspath(path)
        mtime = os.path.getmtime(ap)
        hit = cache.get(ap)
        if hit is None or hit[0] != mtime:
            try:
                hit = (mtime, Document.load(ap))
            except Exception:
                return None
            cache[ap] = hit
        return hit[1]

    def _resolve_source(self, src):
        studio = self.studio_by_uid(src.get("uid"), src.get("path"), load=True)
        if studio is None:
            raise K.KernelError(f"Part Studio “{src.get('studio_name', '?')}” isn't open")
        if studio.path and src.get("path") != studio.path:
            src["path"] = studio.path
        src["studio_name"] = studio.name
        if studio.uid != src.get("uid"):
            src["uid"] = studio.uid
        state = studio.state_for(src.get("config")) if hasattr(studio, "state_for") else studio.state
        if hasattr(studio, "part_id") and src.get("part"):
            src["part"] = studio.part_id(src["part"])      # (an id from before part ids were per feature)
        p = state.parts.get(src.get("part"))
        if p is None:
            raise K.KernelError(f"The part no longer exists in “{studio.name}”")
        return p

    def _update_link_paths(self, asm):
        """Record where each linked Part Studio is saved now, so the assembly finds it next time."""
        for inst in asm.instances:
            src = inst["source"]
            if src.get("kind") != "studio":
                continue
            st = next((d for d in self.studio_docs() if d.uid == src.get("uid")), None)
            if st is not None:
                src["studio_name"] = st.name
                if st.path:
                    src["path"] = os.path.abspath(st.path)

    def open_studio_for_link(self, path):
        """Open a Part Studio file in a background tab (keeping the current tab), for inserting from it."""
        for d in self.studio_docs():
            if d.path and os.path.abspath(d.path) == os.path.abspath(path):
                return d
        try:
            doc = Document.load(path)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
            return None
        doc.name = os.path.splitext(os.path.basename(path))[0]
        self._add_tab(doc, switch=False)
        self._add_recent(path)
        return doc

    def open_dialog(self):
        self._leave_modes()
        path, _ = filedlg.getOpenFileName(
            self, "Open", "", "FreeShape documents, STEP and BREP files (*.fshape *.fasm *.fdwg *.fcad *.step *.stp *.brep *.brp);;"
            "Part Studios (*.fshape *.fcad);;Assemblies (*.fasm);;Drawings (*.fdwg);;STEP files (*.step *.stp);;BREP files (*.brep *.brp);;"
            "All files (*)")
        if path:
            self.open_any(path)

    def open_file(self, path):
        # already open? just switch to it
        for key, d in self.docs.items():
            if d.path and os.path.abspath(d.path) == os.path.abspath(path):
                self.tabs.setCurrentIndex(self._tab_index(d))
                return
        from .progress import ImportProgress
        prog = ImportProgress(self, f"Opening {os.path.basename(path)}")
        try:
            if not path.lower().endswith((".fasm", ".fdwg")) and not self._prefetch_imports(path, prog):
                self.status.showMessage(tr("Opening cancelled"), 5000)
                return
            prog.stage("Rebuilding the model…")
            self.viewport.progress_cb = prog.step
            from .. import document as _D
            _D.PROGRESS = prog.step
            try:
                if path.lower().endswith(".fasm"):
                    doc = AssemblyDoc.load(path, resolver=self._resolve_source)
                elif path.lower().endswith(".fdwg"):
                    prog.stage("Working out the views…")
                    doc = DrawingDoc.load(path, resolver=self._resolve_drawing_source,
                                          hlr_async=self.dwg.request_exact)
                else:
                    doc = Document.load(path)
            except Exception as e:
                prog.close()
                prog = None
                QMessageBox.critical(self, APP_NAME, f"Could not open {path}:\n{e}")
                return
            doc.name = os.path.splitext(os.path.basename(path))[0]
            self._open_in_tab(doc)
            QTimer.singleShot(400, lambda d=doc: self._update_thumbnail(d))
        finally:
            self.viewport.progress_cb = None
            from .. import document as _D
            _D.PROGRESS = None
            if prog is not None:
                prog.close()
        self._add_recent(path)

    def _open_in_tab(self, doc):
        """New tab — or reuse the current one if it's still empty."""
        old = self.doc
        if self._is_pristine(old):
            old_key = id(old)
            self._add_tab(doc)
            self.tabs.removeTab(self._tab_index(old))
            self.docs.pop(old_key, None)
            self._views.pop(old_key, None)
        else:
            self._add_tab(doc)
        QTimer.singleShot(60, self._fresh_view)

    def save(self):
        if not self.doc.path:
            return self.save_as()
        if self.is_asm:
            self._update_link_paths(self.doc)
            unsaved = sorted({s.name for s in self.studio_docs() if not s.path and any(
                i["source"].get("uid") == s.uid for i in self.doc.instances)})
            if unsaved:
                self.status.showMessage("Note: " + ", ".join(unsaved) + " isn't saved yet — the assembly keeps a "
                                        "copy of its parts, but save the Part Studio too so it stays linked", 10000)
        try:
            self.doc.save()
        except OSError as e:
            QMessageBox.critical(self, APP_NAME, f"Could not save:\n{e}")
            return False
        self._on_doc_changed()
        self._update_thumbnail()
        self._add_recent(self.doc.path)
        self._drop_autosave(self.doc)
        self.status.showMessage(f"Saved {self.doc.path}", 4000)
        return True

    def save_as(self):
        ext = ".fdwg" if self.is_dwg else ".fasm" if self.is_asm else ".fshape"
        filt = "FreeShape drawings (*.fdwg)" if self.is_dwg else "FreeShape assemblies (*.fasm)" if self.is_asm \
            else "FreeShape documents (*.fshape)"
        path, _ = filedlg.getSaveFileName(self, "Save as", self.doc.path or f"{self.doc.name}{ext}", filt)
        if not path:
            return False
        if not path.lower().endswith(ext):
            path += ext
        self.doc.path = path
        return self.save()

    def import_step(self, into_current=False):
        if self.mode != "idle":
            self._leave_modes()
        path, _ = filedlg.getOpenFileName(self, "Import", "", "STEP or BREP files (*.step *.stp *.brep *.brp);;"
                                              "STEP files (*.step *.stp);;BREP files (*.brep *.brp);;All files (*)")
        if path:
            self.import_step_file(path, into_current)

    def import_step_file(self, path, into_current=False):
        from .progress import ImportProgress, read_step_in_background
        if into_current and self.is_asm:
            n = self.asm.insert_step(path)
            self.status.showMessage(f"Inserted {n} part(s) from {os.path.basename(path)}", 5000)
            return
        fname = os.path.basename(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        prog = ImportProgress(self, f"Importing {fname}")
        try:
            if not F.is_brep_name(fname) and not read_step_in_background(raw, prog, self):
                self.status.showMessage(tr("Import cancelled"), 5000)
                return
            params = {"data": base64.b64encode(raw).decode(), "filename": fname}
            if not F.is_brep_name(fname):
                # keep the parts as exact geometry, so opening the file never translates the STEP again
                try:
                    items = K._STEP_CACHE.get(K.step_cache_key(raw))
                    if items is None:
                        import tempfile
                        with tempfile.NamedTemporaryFile(suffix=".step", delete=False) as tmp:
                            tmp.write(raw)
                        try:
                            items = K.import_step_with_colors(tmp.name)
                        finally:
                            os.unlink(tmp.name)
                    b64, meta = K.pack_import(items)
                    K.adopt_import(K.step_cache_key(raw), b64)
                    params = {"brep": b64, "meta": meta, "filename": fname}
                except Exception:
                    pass                       # keep the STEP itself; it still opens, just more slowly
            if into_current:
                doc = self.doc
            else:
                doc = Document()
                doc.name = os.path.splitext(fname)[0]
            f = doc.make_feature("import")
            f["name"] = "Import " + fname
            f["params"] = params
            prog.stage("Building the parts…")
            self.viewport.progress_cb = prog.step
            if not into_current:
                doc._thumb_for = os.path.abspath(path)
            doc.add_feature(f)
            if not into_current:
                self._open_in_tab(doc)
        finally:
            self.viewport.progress_cb = None
            prog.close()
        if doc.errors.get(f["id"]):
            QMessageBox.warning(self, APP_NAME, doc.errors[f["id"]])
        QTimer.singleShot(80, self._fresh_view)
        n = len(doc.state.parts)
        self.status.showMessage(f"Imported {fname} — {n} part{'s' if n != 1 else ''}", 5000)
        if not doc.errors.get(f["id"]):
            self._add_recent(path)
            QTimer.singleShot(500, lambda d=doc: self._update_thumbnail(d))

    def _prefetch_imports(self, path, prog):
        """Read the STEP files embedded in a .fshape in the background first (big ones only). False = cancelled."""
        import json
        from .progress import read_step_in_background
        try:
            with open(path) as fh:
                feats = json.load(fh).get("features", [])
        except Exception:
            return True
        from .progress import read_saved_import_in_background
        for f in feats:
            p = f.get("params", {})
            if f.get("type") == "import" and not f.get("suppressed") and p.get("brep"):
                if not read_saved_import_in_background(p["brep"], p.get("meta") or [], prog, self):
                    return False
                continue
            if f.get("type") == "import" and not f.get("suppressed") and f.get("params", {}).get("data") \
                    and not F.is_brep_name(f["params"].get("filename", "")):
                try:
                    raw = base64.b64decode(f["params"]["data"])
                except Exception:
                    continue
                if not read_step_in_background(raw, prog, self):
                    return False
        return True

    EXPORT_FORMATS = (("step", "STEP (*.step *.stp)", ".step"), ("stl", "STL (*.stl)", ".stl"),
                      ("brep", "BREP — OpenCascade, exact (*.brep *.brp)", ".brep"))

    def _export_path(self, title, base, fmt_):
        """Ask where to save; returns a path with the chosen format's extension, or None."""
        fmt_ = fmt_ if fmt_ in ("step", "stl", "brep") else "step"
        filters = ";;".join(f for _k, f, _e in self.EXPORT_FORMATS)
        sel = next(f for k, f, _e in self.EXPORT_FORMATS if k == fmt_)
        ext = next(e for k, _f, e in self.EXPORT_FORMATS if k == fmt_)
        path, chosen = filedlg.getSaveFileName(self, title, base + ext, filters, sel)
        if not path:
            return None
        if not os.path.splitext(path)[1]:
            path += next((e for _k, f, e in self.EXPORT_FORMATS if f == chosen), ".step")
        return path

    def _stl_options(self, path):
        """For an .stl path: ask for the resolution (Coarse / Medium / Fine / Custom, remembered). Returns
        (deviation, angle), None for a file that isn't STL, or False if cancelled."""
        if not str(path).lower().endswith(".stl"):
            return None
        from PySide6.QtWidgets import QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("STL resolution"))
        form = QFormLayout(dlg)
        combo = QComboBox()
        for key, label in (("coarse", "Coarse — small files, visible facets"),
                           ("medium", "Medium — fine for most 3D printing"),
                           ("fine", "Fine — smooth curves, bigger files"), ("custom", "Custom")):
            combo.addItem(tr(label), key)
        dev, ang = QDoubleSpinBox(), QDoubleSpinBox()
        dev.setDecimals(3)
        dev.setRange(0.001, 10.0)
        dev.setSingleStep(0.01)
        dev.setSuffix(" mm")
        ang.setDecimals(1)
        ang.setRange(0.5, 90.0)
        ang.setSuffix(" °")
        form.addRow(tr("Resolution"), combo)
        form.addRow(tr("Deviation (distance from the true surface)"), dev)
        form.addRow(tr("Angle (between neighbouring facets)"), ang)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)

        def pick():
            key = combo.currentData()
            custom = key == "custom"
            dev.setEnabled(custom)
            ang.setEnabled(custom)
            if not custom:
                d, a = K.STL_RESOLUTIONS[key]
                dev.setValue(d)
                ang.setValue(a)
        combo.currentIndexChanged.connect(lambda _i: pick())
        key = str(self.settings.value("stl/resolution", "medium"))
        dev.setValue(float(self.settings.value("stl/deviation", 0.05)))
        ang.setValue(float(self.settings.value("stl/angle", 17.0)))
        combo.setCurrentIndex(max(0, combo.findData(key)))
        pick()
        if dlg.exec() != QDialog.Accepted:
            return False
        self.settings.setValue("stl/resolution", combo.currentData())
        self.settings.setValue("stl/deviation", dev.value())
        self.settings.setValue("stl/angle", ang.value())
        return dev.value(), ang.value()

    def export(self, part_ids, fmt_=None):
        if self.is_dwg:
            self.dwg.export(fmt_ if fmt_ in ("dxf", "pdf") else None)
            return
        if self.is_asm:
            if not self.doc.instances:
                QMessageBox.information(self, APP_NAME, "Insert some parts first.")
                return
            path = self._export_path("Export assembly", self.doc.name.replace(" ", "_"), fmt_)
            if not path:
                return
            stl = self._stl_options(path)
            if stl is False:
                return
            try:
                self.doc.export(path, stl=stl)
                self.status.showMessage(f"Exported {path}", 5000)
            except Exception as e:
                QMessageBox.critical(self, APP_NAME, f"Export failed:\n{e}")
            return
        if not self.doc.state.parts:
            QMessageBox.information(self, APP_NAME, "There are no parts to export yet.")
            return
        base = "part"
        if part_ids and len(part_ids) == 1:
            base = self.doc.state.parts[part_ids[0]].name.replace(" ", "_")
        path = self._export_path("Export", base, fmt_)
        if not path:
            return
        stl = self._stl_options(path)
        if stl is False:
            return
        try:
            self.doc.export(path, part_ids, stl=stl)
            self.status.showMessage(f"Exported {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Export failed:\n{e}")

    def show_about(self):
        from .. import __version__
        QMessageBox.about(self, "About FreeShape", (
            f"<h3>FreeShape {__version__}</h3>"
            "<p>Free, offline, Onshape-style parametric CAD — parts, assemblies, drawings and sheet metal.</p>"
            "<p>Copyright © 2026 Richard Willmott</p>"
            "<p>This program is free software: you can redistribute it and/or modify it under the terms of the "
            "GNU General Public License, version 3 or any later version. It comes with ABSOLUTELY NO WARRANTY. "
            "See the LICENSE file for details.</p>"
            "<p>Built on open-source libraries including the Open CASCADE Technology geometry kernel (LGPL 2.1) "
            "and Qt / PySide6 (LGPL 3) — see THIRD-PARTY-NOTICES.txt.</p>"
            "<p><a href='https://github.com/richiewillmott/FreeShape'>github.com/richiewillmott/FreeShape</a></p>"))

    # ------------------------------------------------------------------ variables table
    def show_variables(self):
        """Variable ▾ → Variables table: every variable in this Part Studio, editable in place."""
        if self.is_asm or self.is_dwg or self.doc is None:
            return
        from .variables_ui import VariablesTable
        t = getattr(self, "_variables_table", None)
        if t is not None and t.doc is not self.doc:
            t.close()
            t = None
        if t is None:
            t = self._variables_table = VariablesTable(self)
        t.show()
        t.raise_()
        t.activateWindow()

    # ------------------------------------------------------------------ copy / paste features
    def copy_features(self, fids=None):
        """Ctrl+C: the features selected in the feature list go on the clipboard (the system one too, so another
        FreeShape window can paste them)."""
        from .. import feature_clipboard as FC
        if self.is_asm or self.is_dwg or self.mode != "idle" or self.doc is None:
            return
        fids = list(fids) if fids else self.left.features.selected_feature_ids()
        if not fids:
            self.status.showMessage(tr("Select features in the feature list to copy them"), 4000)
            return
        payload = FC.copy_features(self.doc, fids)
        self._feature_clip = payload
        QApplication.clipboard().setText(FC.to_text(payload))
        n = len(payload["features"])
        self.status.showMessage(f"Copied {n} feature{'s' if n != 1 else ''} — Ctrl+V pastes them at the rollback "
                                "bar (here or in another Part Studio)", 5000)

    def _clipboard_features(self):
        from .. import feature_clipboard as FC
        got = FC.from_text(QApplication.clipboard().text())
        return got if got is not None else getattr(self, "_feature_clip", None)

    def paste_features(self, index=None):
        """Ctrl+V: copies of the copied features, at the rollback bar (or `index`), one step to undo."""
        from .. import feature_clipboard as FC
        if self.is_asm or self.is_dwg or self.mode != "idle" or self.doc is None:
            return
        payload = self._clipboard_features()
        if payload is None:
            return
        new = FC.paste(self.doc, payload, index)
        if not new:
            return
        bad = [f["name"] for f in new if self.doc.errors.get(f["id"])]
        msg = f"Pasted {len(new)} feature{'s' if len(new) != 1 else ''}"
        if bad:
            msg += f" — {len(bad)} need attention (red in the feature list: pick what they use again)"
        self.status.showMessage(msg, 8000)

    # ------------------------------------------------------------------ variables made on the fly
    def create_variable_before(self, index, name, kind, value):
        """Type an unknown #name in a box → this makes it: a Variable feature inserted at `index` (just before the
        feature being edited), holding `value` (mm / degrees / a number, or an expression text). kind: length,
        angle, number or any. Returns the new feature."""
        from .numparse import fmt
        f = self.doc.make_feature("measure_value")
        mtype = {"length": "fixed_length", "angle": "fixed_angle", "number": "fixed_number"}.get(kind, "expression")
        if isinstance(value, str):
            expr = value
        elif mtype == "fixed_length":
            expr = U.num(float(value))                   # in the document's unit (the variable's unit below)
        else:
            expr = fmt(float(value))
        f["params"].update(name=name, mtype=mtype, expr=expr, unit=U.unit(), aunit="deg")
        f["name"] = f"#{name}"
        self.doc.insert_feature(f, index)
        self.status.showMessage(f"Variable #{name} = {expr}{(' ' + U.unit()) if mtype == 'fixed_length' else ''} "
                                "added before this feature — edit it in the feature list", 8000)
        return f

    def _sketch_new_variable(self, name, kind, value):
        """A sketch dimension used an unknown #name and you said make it: insert it before this sketch."""
        ctx = self.sk_ctx
        if not ctx or self.sketcher is None:
            return None
        self.create_variable_before(ctx["index"], name, kind, value)
        ctx["index"] += 1                                # the sketch now comes one later
        ctx["base"] = self.doc.state_before(ctx["index"])
        self.sketcher.variable_kinds = dict(getattr(ctx["base"], "var_kinds", {}))
        return dict(ctx["base"].vars)

    # ------------------------------------------------------------------ Python scripts
    def _open_docs(self):
        return [d for d in (self.docs.get(self.tabs.tabData(i)) for i in range(self.tabs.count())) if d is not None]

    def export_script(self):
        """Document → Export as Python script…: this tab and the tabs linked to it (the Part Studios its assembly
        uses, its drawings …) as one script that builds them all again, variables first."""
        from .. import script as S
        if self.doc is None:
            return
        if self.mode == "sketch":
            self.finish_sketch()
        elif self.mode == "dialog":
            self.status.showMessage("Finish or cancel the feature you're editing first", 5000)
            return
        docs = S.linked(self._open_docs(), self.doc)
        base = self.doc.name.replace(" ", "_") + ".py"
        if getattr(self.doc, "path", None):
            base = os.path.join(os.path.dirname(self.doc.path), os.path.splitext(os.path.basename(self.doc.path))[0]
                                + ".py")
        path, _ = filedlg.getSaveFileName(self, "Export as Python script", base, "Python script (*.py)")
        if not path:
            return
        if not path.lower().endswith(".py"):
            path += ".py"
        try:
            text = S.dump(docs)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as e:
            QMessageBox.critical(self, APP_NAME, f"Couldn't write the script:\n{e}")
            return
        names = ", ".join(d.name for d in docs)
        self.status.showMessage(f"Script saved: {os.path.basename(path)} ({names})", 8000)

    def run_script(self, path=None):
        """Document → Run Python script…: run a FreeShape script and open what it builds as new tabs."""
        from .. import script as S
        if not path:
            path, _ = filedlg.getOpenFileName(self, "Run Python script", "", "Python script (*.py)")
            if not path:
                return
            if not getattr(self, "_script_ok", False):
                ok = QMessageBox.question(
                    self, APP_NAME, "A script is a Python program: it can do anything a program on your computer can. "
                    "Only run scripts you trust (ones you exported yourself, say).\n\nRun it?")
                if ok != QMessageBox.Yes:
                    return
                self._script_ok = True
        from .plugin_ui import is_project_script
        if not is_project_script(path):
            # an older-style script that works on the open Part Studio (see PLUGINS.md): run it that way
            from .. import plugins as P
            try:
                P.run_script(path, self)
                self.status.showMessage(f"Ran {os.path.basename(path)}", 5000)
            except Exception as e:
                QMessageBox.warning(self, APP_NAME, f"{os.path.basename(path)} failed:\n\n{type(e).__name__}: {e}")
            self._after_plugin()
            return
        self.status.showMessage(f"Running {os.path.basename(path)}…")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ws = S.run_file(path)
            docs = ws.documents()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            import traceback
            tb = traceback.format_exc(limit=6)
            QMessageBox.critical(self, APP_NAME, f"The script stopped with an error:\n{e}\n\n{tb[-1500:]}")
            self.status.clearMessage()
            return
        finally:
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
        self._fresh_uids(docs)
        for d in docs:
            self._add_tab(d)
            if getattr(d, "kind", "") in ("assembly", "drawing"):
                d.refresh()                              # now linked through the app's tabs
        errs = ws.errors()
        msg = f"Script built {len(docs)} document(s): " + ", ".join(d.name for d in docs)
        if errs:
            msg += " — some features failed: " + "; ".join(f"{n}: {len(e)}" for n, e in errs.items())
        self.status.showMessage(msg, 10000)
        return docs

    def _fresh_uids(self, docs):
        """Documents a script built get new ids if the originals are open too, so links stay inside the script's
        set (an assembly links to the Part Studio the script made, not the one already open)."""
        import uuid
        open_uids = {getattr(d, "uid", None) for d in self._open_docs()}
        remap = {}
        for d in docs:
            if getattr(d, "uid", None) in open_uids:
                remap[d.uid] = uuid.uuid4().hex
                d.uid = remap[d.uid]
        if not remap:
            return
        for d in docs:
            kind = getattr(d, "kind", "")
            if kind == "assembly":
                for inst in d.instances:
                    src = inst.get("source", {})
                    if src.get("uid") in remap:
                        src["uid"] = remap[src["uid"]]
                for sub in d.subs:
                    src = sub.get("source", {})
                    if src.get("uid") in remap:
                        src["uid"] = remap[src["uid"]]
            elif kind == "drawing" and d.source and d.source.get("uid") in remap:
                d.source["uid"] = remap[d.source["uid"]]

    def show_shortcuts(self):
        """Document → Keyboard shortcuts (or ?): every key, in a table you can search."""
        from .shortcuts_help import ShortcutsDialog
        ShortcutsDialog(self).exec()

    def closeEvent(self, e):
        self._leave_modes()
        self._update_thumbnail()
        for i in range(self.tabs.count()):
            doc = self.docs.get(self.tabs.tabData(i))
            if doc is not None and doc.dirty and self._has_content(doc):
                self.tabs.setCurrentIndex(i)
                if not self._confirm_discard(doc):
                    e.ignore()
                    return
        for doc in self.docs.values():
            self._drop_autosave(doc)
        try:
            os.rmdir(self.autosave_dir())        # only succeeds if it's empty
        except OSError:
            pass
        e.accept()

    def keyPressEvent(self, e):
        if self.viewport.zoom_window_mode and e.key() == Qt.Key_Escape:
            self.viewport._end_zoom_window()
            return
        # assembly keys (plain shortcuts would steal the same letters from the sketcher)
        if self.is_asm and self.mode == "idle":
            k, mods = e.key(), e.modifiers()
            if k == Qt.Key_I and not mods:
                self.asm.toggle_insert()
                return
            gz = self.asm.gizmo
            txt = e.text()
            if gz.active() and gz.last is not None and gz.editor is None and txt and txt in "0123456789-.+":
                gz.open_editor(initial=txt)          # typing a number after a drag sets it, no click needed
                return
            if k == Qt.Key_Escape and self.asm.gizmo.inst is not None:
                self.asm.gizmo.clear()
                return
            if mods == Qt.ShiftModifier and k in (Qt.Key_R, Qt.Key_L):
                self.asm.start_mate("revolute" if k == Qt.Key_R else "slider")
                return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.section_panel is not None:
            self.section_panel.place()
        if self.measure.isVisible():
            self._selection_changed()


RENDER_MODES = [("shaded_edges", "Shaded with edges"), ("shaded", "Shaded without edges"), ("unshaded", "Unshaded"),
                ("translucent", "Translucent")]
HIDDEN_EDGES = [("removed", "Hidden edges removed"), ("visible", "Hidden edges visible")]
TANGENT_EDGES = [("visible", "Tangent edges visible"), ("hidden", "Tangent edges hidden"),
                 ("phantom", "Tangent edges as phantom lines")]


class _MeasurePanel(QFrame):
    """The measure bar in the bottom-right corner of the graphics area, as in Onshape: the main measurement
    ("Min dist: 107.521 mm") and an (x) button that turns it into a Measure value feature — a #variable driven by
    the picked vertices, edges, faces, parts or sketch entities, re-measured on every rebuild. Clicking the value
    shows every measurement of the selection (angle, ΔX·ΔY·ΔZ, centre to centre …)."""

    def __init__(self, parent, on_create):
        super().__init__(parent)
        self.setObjectName("measurePanel")
        self.setStyleSheet(
            "#measurePanel { background: rgba(255,255,255,235); border: 1px solid %s; border-radius: 5px; }"
            " QLabel { color: %s; }"
            " QToolButton#measureVar { border: 1px solid transparent; border-radius: 4px; padding: 0 4px;"
            "   font-style: italic; font-weight: 600; color: %s; }"
            " QToolButton#measureVar:hover { border-color: %s; background: rgba(0,0,0,18); }"
            % (theme.BORDER, theme.TEXT, theme.TEXT, theme.BORDER))
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 4, 6, 4)
        v.setSpacing(3)
        self.label = QLabel()                # every measurement of the selection (shown on demand)
        v.addWidget(self.label)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.value = QLabel()
        self.value.setCursor(Qt.PointingHandCursor)
        self.value.setToolTip(tr("Click to show all the measurements of the selection"))
        self.value.mousePressEvent = lambda _e: self._toggle()
        row.addWidget(self.value)
        self.create = QToolButton()
        self.create.setObjectName("measureVar")
        self.create.setText("(x)")
        self.create.setToolTip("Create a variable from this measurement — a Measure value feature, driven by the "
                               "selection, that updates when the model changes")
        self.create.setCursor(Qt.PointingHandCursor)
        self.create.clicked.connect(on_create)
        row.addWidget(self.create)
        v.addLayout(row)
        self.expanded = False
        self._has_head = False

    def enterEvent(self, e):
        # the selection-count badge follows the pointer over the 3D view; it would sit on top of (x) here
        badge = getattr(self.parentWidget(), "count_badge", None)
        if badge is not None:
            badge.hide()
        super().enterEvent(e)

    def _toggle(self):
        self.expanded = not self.expanded
        self.label.setVisible(self.expanded or not self._has_head)
        self._fit()
        p = self.parentWidget()
        if p is not None:
            self.move(p.width() - self.width() - 14, p.height() - self.height() - 14)

    def set_measure(self, head, details_html):
        """head = (label, value text) of the main measurement, or None (then only the details are shown)."""
        self._has_head = head is not None
        self.label.setText(details_html)
        self.label.setVisible(self.expanded or not self._has_head)
        self.value.setVisible(self._has_head)
        self.create.setVisible(self._has_head)
        if head:
            self.value.setText(f"<span style='color:#6a737d'>{head[0]}:</span> <b>{head[1]}</b>")
        self._fit()

    def _fit(self):
        """Resize to the new contents now: the layout is otherwise recalculated later, so a longer value
        (after a shorter one) would be cut off."""
        for wdg in (self.label, self.value, self.create):
            wdg.updateGeometry()
        lay = self.layout()
        lay.invalidate()
        lay.activate()
        self.adjustSize()

    def setText(self, text):
        self.set_measure(None, text)

class _FakeKey:
    """Lets window shortcuts hand a key to the drawing tools."""

    def __init__(self, key):
        self._k = key

    def key(self):
        return self._k

    def modifiers(self):
        return Qt.NoModifier
