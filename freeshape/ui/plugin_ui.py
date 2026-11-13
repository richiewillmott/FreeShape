"""The Plugins menu and the Plugins window (what's installed, errors, install / reload / open the folder)."""
from __future__ import annotations

import os
import shutil
import traceback

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem, QTextBrowser,
    QMessageBox, QSplitter,
)

from .. import plugins as P
from .icons import icon


def fill_menu(menu, main):
    """Plugin features and commands, then script / manage entries."""
    menu.clear()
    feats = [(s.label, ft) for ft, s in P.FEATURES.items()]
    for label, ft in sorted(feats):
        a = menu.addAction(icon(ft), label + "…")
        a.triggered.connect(lambda _=False, ft=ft: main.start_feature(ft))
        a.setEnabled(not (main.is_asm or main.is_dwg))
    if feats and P.COMMANDS:
        menu.addSeparator()
    for c in P.COMMANDS:
        a = menu.addAction(c.label)
        a.triggered.connect(lambda _=False, c=c: run_command(main, c))
    if not feats and not P.COMMANDS:
        a = menu.addAction("No plugins installed")
        a.setEnabled(False)
    menu.addSeparator()
    menu.addAction("Run a Python script…").triggered.connect(lambda: run_script(main))
    menu.addAction("Manage plugins…").triggered.connect(lambda: PluginsDialog(main).exec())


def run_command(main, cmd):
    try:
        P.run_command(cmd, main)
    except Exception as e:
        traceback.print_exc()
        QMessageBox.warning(main, cmd.label, f"The “{cmd.plugin}” plugin failed:\n\n{type(e).__name__}: {e}")
    main._after_plugin()


def run_script(main):
    from . import filedlg
    path, _ = filedlg.getOpenFileName(main, "Run a Python script", "", "Python scripts (*.py)")
    if not path:
        return
    try:
        P.run_script(path, main)
        main.status.showMessage(f"Ran {os.path.basename(path)}", 5000)
    except Exception as e:
        traceback.print_exc()
        QMessageBox.warning(main, "Script", f"{os.path.basename(path)} failed:\n\n{type(e).__name__}: {e}")
    main._after_plugin()


class PluginsDialog(QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Plugins")
        self.resize(760, 460)
        lay = QVBoxLayout(self)
        top = QLabel(f"Plugins are Python files in <a href='folder'>{P.user_dir()}</a>. They can add parametric "
                     "features and commands (see PLUGINS.md). Only install plugins you trust — they run with "
                     "the same rights as FreeShape.")
        top.setWordWrap(True)
        top.setObjectName("muted")
        top.linkActivated.connect(lambda _l: self.open_folder())
        lay.addWidget(top)
        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.info = QTextBrowser()
        self.info.setOpenExternalLinks(False)
        split.addWidget(self.list)
        split.addWidget(self.info)
        split.setSizes([260, 480])
        lay.addWidget(split, 1)
        btns = QHBoxLayout()
        for text, fn in (("Install plugin file…", self.install), ("Add the examples", self.install_examples),
                         ("Open plugins folder", self.open_folder), ("Reload", self.reload)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            btns.addWidget(b)
        btns.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        lay.addLayout(btns)
        self.list.currentRowChanged.connect(self._show)
        self._fill()

    def _missing(self):
        """Plugins the open document uses that aren't installed."""
        doc = self.main.doc
        out = set()
        for f in getattr(doc, "features", []) or []:
            if P.is_plugin_type(f.get("type")) and f["type"] not in P.FEATURES:
                out.add(P.plugin_of(f))
        return sorted(out)

    def _fill(self):
        self.list.clear()
        for info in P.PLUGINS:
            it = QListWidgetItem(icon("plugin"), (info.title or info.name) + ("  ⚠" if info.error else ""))
            it.setData(Qt.UserRole, info)
            self.list.addItem(it)
        for name in self._missing():
            it = QListWidgetItem(f"{name}  (not installed)")
            it.setData(Qt.UserRole, name)
            it.setForeground(Qt.gray)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.info.setHtml("<p>No plugins installed yet.</p><p>Click <b>Add the examples</b> for a spur gear "
                              "feature and a part weights command to try, or <b>Install plugin file…</b>.</p>")

    def _show(self, row):
        it = self.list.item(row)
        if it is None:
            return
        info = it.data(Qt.UserRole)
        if isinstance(info, str):
            self.info.setHtml(f"<h3>{info}</h3><p>This document has features made with the “{info}” plugin, which "
                              "isn't installed. They keep the shape saved in the file, but can't be changed until "
                              "the plugin is installed.</p>")
            return
        h = [f"<h3>{info.title or info.name}" + (f" <span style='color:#888'>{info.version}</span>" if info.version
                                                 else "") + "</h3>"]
        if info.description:
            h.append(f"<p>{info.description}</p>")
        if info.error:
            tr = getattr(info, "trace", "").replace("&", "&amp;").replace("<", "&lt;")
            h.append(f"<p style='color:#d93025'><b>Didn't load:</b> {info.error}</p><pre style='font-size:9pt'>{tr}"
                     "</pre>")
        if info.features:
            h.append("<p><b>Features</b> (Plugins button on the toolbar):</p><ul>" + "".join(
                f"<li>{P.FEATURES[f].label}</li>" for f in info.features if f in P.FEATURES) + "</ul>")
        if info.commands:
            h.append("<p><b>Commands</b>:</p><ul>" + "".join(f"<li>{c.label}</li>" for c in info.commands) + "</ul>")
        h.append(f"<p style='color:#888'>{info.path}</p>")
        self.info.setHtml("".join(h))

    def open_folder(self):
        os.makedirs(P.user_dir(), exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(P.user_dir()))

    def install(self):
        from PySide6.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(self, "Install plugin", os.path.expanduser("~"),
                                                "Python plugins (*.py)")
        if paths:
            self._copy(paths)

    def install_examples(self):
        d = P.examples_dir()
        self._copy([os.path.join(d, f) for f in sorted(os.listdir(d)) if f.endswith(".py")] if os.path.isdir(d)
                   else [])

    def _copy(self, paths):
        os.makedirs(P.user_dir(), exist_ok=True)
        for p in paths:
            dst = os.path.join(P.user_dir(), os.path.basename(p))
            if os.path.abspath(p) != os.path.abspath(dst):
                shutil.copy2(p, dst)
        self.reload()

    def reload(self):
        P.load_all()
        self.main._after_plugin(rebuild=True)
        self._fill()
        bad = [i for i in P.PLUGINS if i.error]
        self.main.status.showMessage(f"{len(P.PLUGINS)} plugin(s) loaded" + (f", {len(bad)} with errors" if bad
                                                                               else ""), 6000)
