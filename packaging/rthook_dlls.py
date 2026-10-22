# Runs first inside the packaged app: let Windows find OpenCascade's and VTK's DLLs next to the exe.
import os
import sys

base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
for sub in ("cadquery_ocp.libs", "vtkmodules", "OCP", ""):
    p = os.path.join(base, sub)
    if os.path.isdir(p):
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(p)
            except OSError:
                pass
        os.environ["PATH"] = p + os.pathsep + os.environ.get("PATH", "")
