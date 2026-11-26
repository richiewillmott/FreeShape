"""The document's length unit (Onshape's "Workspace units"): millimetre, centimetre, metre, inch or foot.

The model is always worked out in millimetres; this only changes what you see and what a bare number you type
means. The window sets CURRENT to the unit of the document on screen. No Qt here.
"""
from __future__ import annotations

from .i18n import QT_TRANSLATE_NOOP

FACTORS = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "ft": 304.8}
NAMES = {"mm": QT_TRANSLATE_NOOP("FreeShape", "millimetre"), "cm": QT_TRANSLATE_NOOP("FreeShape", "centimetre"), "m": QT_TRANSLATE_NOOP("FreeShape", "metre"), "in": QT_TRANSLATE_NOOP("FreeShape", "inch"), "ft": QT_TRANSLATE_NOOP("FreeShape", "foot")}
ORDER = ["mm", "cm", "m", "in", "ft"]
CURRENT = ["mm"]


def unit() -> str:
    return CURRENT[0]


def set_unit(u):
    CURRENT[0] = u if u in FACTORS else "mm"


def factor(u=None) -> float:
    return FACTORS.get(u or CURRENT[0], 1.0)


def to_unit(mm, u=None) -> float:
    """A length in mm, in the document's unit."""
    return float(mm) / factor(u)


def from_unit(v, u=None) -> float:
    """A length in the document's unit, in mm."""
    return float(v) * factor(u)


def num(mm, digits=4, u=None) -> str:
    """Just the number of a length, in the document's unit: 25.4 -> "1" in an inch document."""
    from .expr import fmt
    return fmt(to_unit(mm, u), digits)


def length(mm, digits=3, u=None) -> str:
    """A length with its unit: "107.521 mm", "4.233 in"."""
    return f"{num(mm, digits, u)} {u or CURRENT[0]}"


def area(mm2, digits=3, u=None) -> str:
    from .expr import fmt
    return f"{fmt(float(mm2) / factor(u) ** 2, digits)} {u or CURRENT[0]}²"


def volume(mm3, digits=2, u=None) -> str:
    from .expr import fmt
    return f"{fmt(float(mm3) / factor(u) ** 3, digits)} {u or CURRENT[0]}³"
