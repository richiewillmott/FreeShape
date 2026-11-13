"""Mouse styles for the 3D view: which buttons and keys orbit, pan and zoom. Every style is one entry in STYLES.

A rule is (buttons held, modifier key, action):
  buttons   "L", "M", "R" or a chord such as "LM" (both held)
  modifier  "", "ctrl", "shift" or "alt" (the only modifier held)
  action    "orbit", "pan" or "zoom" (drag up / down to zoom)
The first matching rule wins. A left click that doesn't move still selects in every style; with no rule for a plain
left drag it draws a selection box. "wheel" is what the wheel (or two-finger scroll) does without Ctrl:
"zoom", or "pan" for touchpads (then Ctrl + scroll and pinching zoom). Inside a sketch the left button always
belongs to the sketch.
"""
from __future__ import annotations

from PySide6.QtCore import Qt

STYLES = {
    "onshape": {"label": "Onshape", "wheel": "zoom", "rules": [
        ("R", "", "orbit"), ("M", "", "pan"), ("R", "ctrl", "pan"), ("M", "ctrl", "pan")]},
    "touchpad": {"label": "Touchpad", "wheel": "pan", "rules": [
        ("L", "alt", "orbit"), ("L", "shift", "pan"), ("R", "", "orbit"), ("M", "", "pan"), ("R", "ctrl", "pan")]},
    "cad": {"label": "CAD (FreeCAD)", "wheel": "zoom", "rules": [
        ("LM", "", "orbit"), ("MR", "", "orbit"), ("M", "", "pan"), ("M", "ctrl", "orbit")]},
    "gesture": {"label": "Gesture (FreeCAD)", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("R", "", "pan"), ("R", "ctrl", "pan"), ("M", "", "pan"), ("LR", "", "zoom")]},
    "blender": {"label": "Blender", "wheel": "zoom", "rules": [
        ("M", "", "orbit"), ("M", "shift", "pan"), ("M", "ctrl", "zoom"), ("R", "", "orbit")]},
    "solidworks": {"label": "SolidWorks", "wheel": "zoom_reverse", "rules": [
        ("M", "", "orbit"), ("M", "ctrl", "pan"), ("M", "shift", "zoom"), ("R", "", "orbit")]},
    "openinventor": {"label": "OpenInventor (FreeCAD)", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("M", "", "pan"), ("LM", "", "zoom"), ("R", "", "orbit")]},
    "revit": {"label": "Revit", "wheel": "zoom", "rules": [
        ("M", "", "pan"), ("M", "shift", "orbit"), ("R", "", "orbit")]},
    "openscad": {"label": "OpenSCAD", "wheel": "zoom", "rules": [
        ("L", "", "orbit"), ("R", "", "pan"), ("M", "", "zoom"), ("R", "shift", "zoom")]},
}
DEFAULT = "onshape"
ZOOM_SPEEDS = [("slow", "Slow", 0.5), ("normal", "Normal", 1.0), ("fast", "Fast", 1.8)]

_BTN = {"L": Qt.LeftButton, "M": Qt.MiddleButton, "R": Qt.RightButton}


def _held(buttons) -> str:
    """Qt buttons held -> 'L', 'LM', 'MR' …"""
    return "".join(k for k in "LMR" if buttons & _BTN[k])


def _modifier(mods) -> str | None:
    """The single modifier held ('' for none), or None for combinations no rule uses."""
    names = [n for n, flag in (("ctrl", Qt.ControlModifier), ("shift", Qt.ShiftModifier), ("alt", Qt.AltModifier))
             if mods & flag]
    if not names:
        return ""
    return names[0] if len(names) == 1 else None


def style(name):
    return STYLES.get(name) or STYLES[DEFAULT]


def drag_action(name, buttons, mods):
    """What a drag with these buttons and keys does in this style: 'orbit', 'pan', 'zoom', or None."""
    held, mod = _held(buttons), _modifier(mods)
    if not held or mod is None:
        return None
    for b, m, action in style(name)["rules"]:
        if b == held and m == mod:
            return action
    return None


def wheel_action(name, mods):
    """What the wheel / two-finger scroll does: 'zoom', 'zoom_reverse' or 'pan'. Ctrl always zooms."""
    w = style(name)["wheel"]
    if mods & Qt.ControlModifier and w == "pan":
        return "zoom"
    return w
