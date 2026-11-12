"""Mouse styles: which button / modifier chord orbits, pans, zooms or selects in the 3D view.

A style is data. Each binding is (buttons, mods, action[, ctx]):
  buttons  the mouse buttons held, as letters in "LMR" (left, middle, right); "" means none held (touchpad hover).
           Matched exactly: "R" does not match left+right held.
  mods     modifier spec over S (Shift), C (Ctrl), A (Alt): an upper-case letter must be held, a lower-case one must
           not be; letters left out are ignored. "" accepts any modifiers.
  action   orbit | pan | zoom | select (select = the button does its normal job: pick, box-select, sketch, drag a part)
  ctx      "" always; "view" only when no sketch or pick tool is active; "edit" only while one is.
The first matching binding wins, so list specific chords before general ones. `wheel` is "zoom" (wheel zooms at the
cursor) or "scroll" (a scroll pans, Ctrl+scroll zooms). `pivot` forces the orbit pivot (cursor | centre | origin);
None follows the user's Rotate-around setting.

The button / modifier mappings follow the ones documented by FreeCAD's navigation styles (Gui/Navigation).
"""
from __future__ import annotations

from dataclasses import dataclass

NAV_ACTIONS = ("orbit", "pan", "zoom")
_BUTTON_NAMES = {"L": "Left", "M": "Middle", "R": "Right"}
_MOD_NAMES = (("C", "Ctrl"), ("S", "Shift"), ("A", "Alt"))


def _mods_match(spec: str, held: str) -> bool:
    for ch in spec:
        if ch.isupper():
            if ch not in held:
                return False
        elif ch.upper() in held:
            return False
    return True


@dataclass(frozen=True)
class NavStyle:
    key: str
    label: str
    bindings: tuple
    wheel: str = "zoom"
    pivot: str | None = None

    def resolve(self, buttons: str, mods: str, editing: bool = False):
        """The action for the buttons ("LMR" letters) and modifiers ("SCA" letters) held, or None."""
        for b in self.bindings:
            bb, mm, action = b[0], b[1], b[2]
            ctx = b[3] if len(b) > 3 else ""
            if bb != buttons or (ctx == "view" and editing) or (ctx == "edit" and not editing):
                continue
            if _mods_match(mm, mods):
                return action
        return None

    def describe(self, action: str) -> str:
        """How to do `action`, from the table (e.g. "Ctrl+Right-drag / Middle-drag")."""
        out = []
        for b in self.bindings:
            if b[2] != action:
                continue
            ctx = b[3] if len(b) > 3 else ""
            held = [n for c, n in _MOD_NAMES if c in b[1]]
            if b[0]:
                what = "+".join(_BUTTON_NAMES[c] for c in b[0]) + "-drag"
            else:
                what = "move"
            s = "+".join(held + [what])
            if ctx == "edit":
                s += " when editing"
            if s not in out:
                out.append(s)
        if action == "zoom":
            out.insert(0, "Wheel" if self.wheel == "zoom" else "Ctrl+scroll")
        elif action == "pan" and self.wheel == "scroll":
            out.insert(0, "Scroll")
        return " / ".join(out) or "-"

    def help_line(self) -> str:
        return "Orbit: %s   Pan: %s   Zoom: %s" % (
            self.describe("orbit"), self.describe("pan"), self.describe("zoom"))


# left button as navigation: orbit in the plain view; with a sketch / pick tool active left keeps its job and Alt+left
# orbits (FreeCAD: "in edit modes, hold Alt in addition")
_LEFT_ORBIT = (("L", "A", "orbit", "edit"), ("L", "", "orbit", "view"), ("L", "", "select", "edit"))

_ONSHAPE = (("R", "C", "pan"), ("R", "c", "orbit"), ("M", "", "pan"), ("L", "", "select"))

STYLES = {s.key: s for s in (
    NavStyle("onshape", "Onshape", _ONSHAPE),
    NavStyle("touchpad", "Touchpad",
             _ONSHAPE + (("", "CSa", "zoom"), ("", "Sca", "pan"), ("", "Acs", "orbit")), wheel="scroll"),
    NavStyle("cad", "CAD", (
        ("R", "CS", "zoom"), ("R", "Cs", "pan"), ("R", "Sc", "orbit"),
        ("M", "cs", "pan"), ("MR", "", "orbit"), ("LM", "", "orbit"),
        ("L", "", "select"))),
    NavStyle("gesture", "Gesture", (("R", "", "pan"),) + _LEFT_ORBIT),
    NavStyle("blender", "Blender", (
        ("R", "CS", "zoom"), ("M", "Cs", "zoom"), ("M", "Sc", "pan"), ("M", "cs", "orbit"),
        ("LR", "", "pan"), ("L", "", "select"))),
    NavStyle("solidworks", "SolidWorks", (
        ("M", "Cs", "pan"), ("M", "Sc", "zoom"), ("M", "cs", "orbit"), ("L", "", "select"))),
    NavStyle("openinventor", "OpenInventor", (
        ("R", "CS", "zoom"), ("M", "Cs", "zoom"), ("M", "cs", "pan"), ("LM", "", "zoom"),
        ("L", "CS", "pan"), ("L", "C", "select"), ("L", "S", "select"),
        ("L", "A", "orbit", "edit"), ("L", "", "orbit", "view"), ("L", "", "select", "edit"))),
    NavStyle("revit", "Revit", (
        ("R", "CS", "zoom"), ("M", "Cs", "zoom"), ("M", "Sc", "orbit"), ("M", "cs", "pan"),
        ("LR", "", "pan"), ("L", "", "select"))),
    NavStyle("openscad", "OpenSCAD", (
        ("R", "S", "zoom"), ("R", "s", "pan"), ("M", "", "zoom")) + _LEFT_ORBIT),
)}

DEFAULT_STYLE = "onshape"


def get_style(key) -> NavStyle:
    return STYLES.get(str(key), STYLES[DEFAULT_STYLE])
