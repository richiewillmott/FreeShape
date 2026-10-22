"""Progress window for long imports, and running the STEP reader in a background process."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QProcess, QEventLoop, QTimer
from PySide6.QtWidgets import QProgressDialog, QApplication

from .. import kernel as K

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if getattr(sys, "frozen", False):
    ROOT = os.path.dirname(sys.executable)
WORKER_MIN_BYTES = 1_000_000        # smaller files read in a blink; not worth starting a second process


class ImportProgress:
    """A small progress window. stage() shows a moving 'busy' bar, step() a real percentage."""

    def __init__(self, parent, title):
        self.title = title
        self.d = QProgressDialog(title, "Cancel", 0, 0, parent)
        self.d.setWindowTitle("FreeShape")
        self.d.setWindowModality(Qt.WindowModal)
        self.d.setMinimumDuration(0)
        self.d.setMinimumWidth(420)
        self.d.setAutoClose(False)
        self.d.setAutoReset(False)
        self.d.setCancelButton(None)          # only the background read can actually be cancelled
        self.cancelled = False
        self._last = 0.0
        self.d.show()
        self._pump(force=True)

    def _pump(self, force=False):
        now = time.time()
        if force or now - self._last > 0.05:
            self._last = now
            QApplication.processEvents()

    def stage(self, text, cancellable=False):
        self.d.setRange(0, 0)
        self.d.setLabelText(f"{self.title}\n\n{text}")
        if cancellable:
            self.d.setCancelButtonText("Cancel")
        else:
            self.d.setCancelButton(None)
        self._pump(force=True)

    def step(self, i, n, text):
        if self.d.maximum() != n:
            self.d.setRange(0, max(n, 1))
            self.d.setLabelText(f"{self.title}\n\n{text}")
        self.d.setValue(i)
        self._pump()

    def close(self):
        self.d.reset()
        self.d.hide()
        self.d.deleteLater()


def read_step_in_background(data: bytes, prog: ImportProgress, parent=None) -> bool:
    """Read STEP bytes in a separate process (so the window keeps drawing and Cancel works), leaving the result
    in the kernel's cache for the import that follows. Returns False only if the user cancelled.
    If the background read can't run, it quietly returns True and the import reads the file itself."""
    key = K.step_cache_key(data)
    if key in K._STEP_CACHE:
        return True
    if len(data) < WORKER_MIN_BYTES or os.environ.get("FREESHAPE_NO_STEP_WORKER"):
        prog.stage("Reading the STEP file…")
        return True
    tmp = tempfile.mkdtemp(prefix="freeshape_step_")
    src, prefix = os.path.join(tmp, "in.step"), os.path.join(tmp, "out")
    try:
        with open(src, "wb") as fh:
            fh.write(data)
        proc = QProcess(parent)
        proc.setWorkingDirectory(ROOT)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        if sys.platform == "win32":
            try:                                       # no console window popping up for the helper
                def no_window(args):
                    args.flags |= 0x08000000           # CREATE_NO_WINDOW
                proc.setCreateProcessArgumentsModifier(no_window)
            except Exception:
                pass
        state = {"text": "Starting the STEP reader", "done": False, "cancelled": False, "buf": ""}
        t0 = time.time()
        loop = QEventLoop()

        def show():
            if prog.d.wasCanceled() and not state["cancelled"]:
                cancel()
                return
            try:                                      # progress file (works for the windowed .exe too)
                with open(prefix + ".progress", encoding="utf-8") as fh:
                    lines = [ln.strip() for ln in fh if ln.strip()]
                for ln in lines:
                    if ln == "DONE":
                        state["done"] = True
                    else:
                        state["text"] = ln
            except OSError:
                pass
            mb = len(data) / 1e6
            prog.d.setLabelText(f"{prog.title}\n\n{state['text']} …  ({mb:.1f} MB, {int(time.time() - t0)} s)")

        def read():
            state["buf"] += bytes(proc.readAllStandardOutput()).decode(errors="ignore")
            *lines, state["buf"] = state["buf"].split("\n")
            for ln in lines:
                if ln.startswith("@@FS "):
                    msg = ln[5:].strip()
                    if msg == "DONE":
                        state["done"] = True
                    else:
                        state["text"] = msg
            show()

        def cancel():
            state["cancelled"] = True
            proc.kill()
        prog.stage("Starting the STEP reader…", cancellable=True)
        prog.d.canceled.connect(cancel)
        proc.readyReadStandardOutput.connect(read)
        proc.finished.connect(loop.quit)
        tick = QTimer()
        tick.timeout.connect(show)
        tick.start(250)
        if getattr(sys, "frozen", False):
            proc.start(sys.executable, ["--step-worker", src, prefix])
        else:
            proc.start(sys.executable, ["-m", "freeshape.step_worker", src, prefix])
        if not proc.waitForStarted(10000):
            tick.stop()
            prog.stage("Reading the STEP file…")
            return True
        loop.exec()
        tick.stop()
        try:
            prog.d.canceled.disconnect(cancel)
        except Exception:
            pass
        if state["cancelled"]:
            proc.waitForFinished(3000)
            return False
        read()
        show()
        if state["done"] and os.path.exists(prefix + ".brep") and os.path.exists(prefix + ".json"):
            prog.stage("Loading the parts…")
            try:
                K.remember_step(key, K.load_step_result(prefix))
            except Exception:
                pass                       # the import will read the file itself
        return True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
