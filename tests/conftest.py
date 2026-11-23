"""Test-wide setup: the windows the tests open must never autosave into (or offer to recover from) your own
FreeShape folder — a test run used to leave "Part Studio 1" documents there for FreeShape to offer next time."""
import os
import tempfile

_AUTOSAVE = tempfile.mkdtemp(prefix="freeshape-test-autosave-")
os.environ["FREESHAPE_AUTOSAVE_DIR"] = _AUTOSAVE
os.environ["FREESHAPE_NO_RECOVERY"] = "1"
os.environ.setdefault("FREESHAPE_NO_PLUGINS", "1")
