"""First-run setup: create a private virtual environment next to the project and install the
requirements into it, then launch FreeShape from there. Standard library only, so it can run
before anything is installed.

Used by start.py (and the .bat/.sh launchers), and as a fallback by `python -m freeshape`.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV = os.path.join(ROOT, ".venv")
REQS = os.path.join(ROOT, "requirements.txt")
MARKER = os.path.join(VENV, ".freeshape-installed")
REQUIRED_MODULES = ("OCP", "PySide6", "OpenGL", "numpy", "ezdxf")
MIN_PY, MAX_PY = (3, 10), (3, 13)


def _say(msg):
    print(f"[FreeShape] {msg}", flush=True)


def _venv_python():
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def _running_in_venv():
    try:
        return os.path.samefile(sys.prefix, VENV)
    except OSError:
        return False


def _reqs_hash():
    with open(REQS, "rb") as fh:
        return hashlib.sha1(fh.read()).hexdigest()


def missing_modules(python=None):
    """Modules FreeShape needs that the given interpreter can't import."""
    python = python or sys.executable
    code = ("import importlib.util,sys;"
            f"print(','.join(m for m in {REQUIRED_MODULES!r} if importlib.util.find_spec(m) is None))")
    try:
        out = subprocess.run([python, "-c", code], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return list(REQUIRED_MODULES)
    if out.returncode != 0:
        return list(REQUIRED_MODULES)
    return [m for m in out.stdout.strip().split(",") if m]


def _check_python():
    v = sys.version_info[:2]
    if v < MIN_PY:
        _say(f"Python {v[0]}.{v[1]} is too old. Please install Python 3.10 – 3.13 from https://www.python.org")
        sys.exit(1)
    if v > MAX_PY:
        _say(f"Warning: Python {v[0]}.{v[1]} is newer than FreeShape has been tested on; "
             "if installation fails, install Python 3.12 or 3.13.")


def _create_venv():
    _say("First run: creating a private Python environment in .venv …")
    try:
        venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(VENV)
    except Exception as e:  # e.g. Debian/Ubuntu without python3-venv
        _say(f"Could not create the environment: {e}")
        if sys.platform.startswith("linux"):
            _say("On Debian/Ubuntu install it with:  sudo apt install python3-venv   then run again.")
        sys.exit(1)


def _install():
    py = _venv_python()
    _say("Installing requirements (OpenCascade, Qt, OpenGL, numpy). This downloads about 300 MB "
         "and takes a few minutes the first time …")
    r = subprocess.run([py, "-m", "pip", "install", "-r", REQS])
    if r.returncode != 0:
        _say("Installing the requirements failed — see the messages above. "
             "Check your internet connection, and that you're using 64-bit Python 3.10 – 3.13.")
        sys.exit(1)
    missing = missing_modules(py)
    if missing:
        _say(f"Installed, but these still can't be imported: {', '.join(missing)}")
        sys.exit(1)
    with open(MARKER, "w") as fh:
        fh.write(_reqs_hash())
    _say("Setup complete.")


def ensure_environment():
    """Make sure .venv exists and has the requirements. Returns the python to run FreeShape with."""
    _check_python()
    py = _venv_python()
    if not os.path.exists(py):
        _create_venv()
    missing = missing_modules(py)
    if not missing:
        # everything imports (e.g. a .venv copied over from another FreeShape folder): nothing to download
        if not os.path.exists(MARKER):
            try:
                with open(MARKER, "w") as fh:
                    fh.write(_reqs_hash())
            except OSError:
                pass
        return py
    _say("Missing: " + ", ".join(missing))
    _install()
    return py


def launch(args=None):
    """Set up if needed, then run FreeShape inside the private environment."""
    args = list(sys.argv[1:] if args is None else args)
    py = ensure_environment()
    _say("Starting FreeShape … (the first start after installing can take up to a minute)")
    if _running_in_venv():
        from freeshape.__main__ import main
        sys.argv = [sys.argv[0]] + args
        main()
        return
    cmd = [py, "-m", "freeshape"] + args
    env = dict(os.environ, FREESHAPE_BOOTSTRAPPED="1")
    sys.exit(subprocess.call(cmd, cwd=ROOT, env=env))
