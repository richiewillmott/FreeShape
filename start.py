"""Start FreeShape. On the first run this installs everything it needs automatically.

    python start.py                 (or double-click it on Windows)
    python start.py my_part.fshape
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from freeshape.bootstrap import launch

if __name__ == "__main__":
    launch()
