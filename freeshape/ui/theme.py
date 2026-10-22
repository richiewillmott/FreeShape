"""Colours and Qt stylesheets for the light (Onshape-like) and dark themes.

Other modules read colours as attributes of this module at draw time (theme.ACCENT, theme.BG_TOP …),
so apply("dark") / apply("light") switches everything in place.
"""

LIGHT = {
    "ACCENT": "#1f6fd1", "ACCENT_DARK": "#155bb0", "ACCENT_LIGHT": "#e3eefb", "ACCENT_SOFT": "#d3e4f8",
    "ACCENT_BORDER": "#c4d9f3", "TEXT": "#1f2328", "TEXT_MUTED": "#6a737d", "BORDER": "#d5d9de",
    "PANEL": "#ffffff", "PANEL_ALT": "#f5f6f8", "INPUT": "#ffffff", "ERROR": "#d93025", "OK_GREEN": "#2e9e44",
    "SELECTED_ROW": "#ffe9b8", "INVALID_BG": "#fdecea", "ACTIVE_FIELD": "#f2f8ff", "LOGO_TEXT": "#3b4250",
    "BG_TOP": (1.0, 1.0, 1.0), "BG_BOTTOM": (1.0, 1.0, 1.0), "EDGE": (0.10, 0.11, 0.13),
    "PLANE": (0.55, 0.70, 0.90), "SKETCH_FREE": (0.12, 0.42, 0.85), "SKETCH_DEFINED": (0.08, 0.08, 0.10),
    "SKETCH_CONSTRUCTION": (0.45, 0.50, 0.58), "REGION": (0.40, 0.62, 0.95), "OVERLAY_TEXT": "#3c424c",
    "PLANE_LABEL": "#28508f", "ICON_INK": "#3b4250", "SKETCH_REMOTE": (0.15, 0.35, 0.75),
}

DARK = {
    "ACCENT": "#4c8ff0", "ACCENT_DARK": "#3a7be0", "ACCENT_LIGHT": "#2a3a52", "ACCENT_SOFT": "#2f4566",
    "ACCENT_BORDER": "#3d5a85", "TEXT": "#e6e8eb", "TEXT_MUTED": "#9aa3ae", "BORDER": "#3a3f46",
    "PANEL": "#23262b", "PANEL_ALT": "#1b1d21", "INPUT": "#2b2f35", "ERROR": "#ff6b5e", "OK_GREEN": "#4cc66a",
    "SELECTED_ROW": "#5a4720", "INVALID_BG": "#4a2522", "ACTIVE_FIELD": "#22324a", "LOGO_TEXT": "#e6e8eb",
    "BG_TOP": (0.22, 0.235, 0.26), "BG_BOTTOM": (0.11, 0.12, 0.135), "EDGE": (0.04, 0.04, 0.05),
    "PLANE": (0.45, 0.62, 0.90), "SKETCH_FREE": (0.42, 0.68, 1.0), "SKETCH_DEFINED": (0.93, 0.94, 0.96),
    "SKETCH_CONSTRUCTION": (0.62, 0.67, 0.74), "REGION": (0.40, 0.62, 0.95), "OVERLAY_TEXT": "#d6dae0",
    "PLANE_LABEL": "#9cc2f5", "ICON_INK": "#d3d8df", "SKETCH_REMOTE": (0.50, 0.72, 1.0),
}

# colours used in inline widget styles: light value -> dark value
INLINE_MAP = {
    "#ffffff": "#23262b", "white": "#23262b", "#eef0f3": "#1f2226", "#e3e8ef": "#2c3138", "#d5d9de": "#3a3f46",
    "#3b4250": "#c9ced6", "#1f2328": "#e6e8eb", "#4b5563": "#b5bcc6", "#f5f6f8": "#1b1d21", "#e3e6ea": "#3a3f46",
    "#6a737d": "#9aa3ae", "#1f6fd1": "#4c8ff0", "#f2f8ff": "#22324a", "#e3eefb": "#2a3a52", "#d3e4f8": "#2f4566",
    "#c4d9f3": "#3d5a85", "#fdecea": "#4a2522", "#ffe9b8": "#5a4720", "#fff4dc": "#4d3d1d",
    "rgba(255,255,255,235)": "rgba(35,38,43,235)", "#155bb0": "#3a7be0", "#2e9e44": "#4cc66a",
}

HOVER = (1.0, 0.62, 0.20)
SELECT = (1.0, 0.78, 0.25)
SKETCH_BAD = (0.85, 0.15, 0.12)
MODE = "light"


def apply(mode: str):
    """Switch the module-level colours to the light or dark palette."""
    global MODE
    MODE = "dark" if mode == "dark" else "light"
    globals().update(DARK if MODE == "dark" else LIGHT)


def adapt(css: str) -> str:
    """Translate an inline stylesheet written with light colours to the current theme."""
    if MODE != "dark" or not css:
        return css
    out = css
    # longest keys first so e.g. "#ffffff" wins over "white"
    for k in sorted(INLINE_MAP, key=len, reverse=True):
        out = out.replace(k, INLINE_MAP[k]).replace(k.upper(), INLINE_MAP[k])
    return out


import os as _os
CHECK_ICON = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                           "assets", "check.svg").replace("\\", "/")


def stylesheet() -> str:
    g = globals()
    return f"""
* {{ font-family: "Inter", "Segoe UI", "Helvetica Neue", "Noto Sans", Arial, sans-serif; font-size: 13px; color: {g['TEXT']}; }}
QMainWindow, QWidget#central {{ background: {g['PANEL_ALT']}; }}
QWidget#topbar {{ background: {g['PANEL']}; border-bottom: 1px solid {g['BORDER']}; }}
QWidget#toolbar {{ background: {g['PANEL']}; border-bottom: 1px solid {g['BORDER']}; }}
QStackedWidget#toolbar > QWidget {{ background: {g['PANEL']}; }}
QLabel#logo {{ font-weight: 600; font-size: 19px; color: {g['LOGO_TEXT']}; letter-spacing: 0.2px; }}
QLabel#docname {{ font-size: 14px; font-weight: 600; }}
QLabel#muted {{ color: {g['TEXT_MUTED']}; }}
QToolButton {{ border: 1px solid transparent; border-radius: 4px; padding: 3px; background: transparent; }}
QToolButton:hover {{ background: {g['ACCENT_LIGHT']}; border-color: {g['ACCENT_BORDER']}; }}
QToolButton:checked {{ background: {g['ACCENT_SOFT']}; border-color: {g['ACCENT']}; }}
QToolButton:disabled {{ color: {g['TEXT_MUTED']}; }}
QToolButton#menuButton {{ padding: 4px 8px; font-weight: 600; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QFrame#sep {{ background: {g['BORDER']}; max-width: 1px; min-width: 1px; margin: 6px 4px; }}

QWidget#leftpanel {{ background: {g['PANEL']}; border-right: 1px solid {g['BORDER']}; }}
QLabel#sectionHeader {{ font-weight: 700; padding: 8px 10px 4px 10px; color: {g['TEXT']}; }}
QLineEdit#filter {{ border: 1px solid {g['BORDER']}; border-radius: 4px; padding: 4px 6px; margin: 0 8px 4px 8px; background: {g['PANEL_ALT']}; }}
QListWidget {{ border: none; background: {g['PANEL']}; outline: 0; }}
QListWidget::item {{ padding: 3px 6px; border-radius: 3px; }}
QListWidget::item:hover {{ background: {g['ACCENT_LIGHT']}; }}
QListWidget::item:selected {{ background: {g['SELECTED_ROW']}; color: {g['TEXT']}; }}
QSplitter::handle {{ background: {g['BORDER']}; }}
QScrollBar:vertical {{ background: {g['PANEL']}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {g['BORDER']}; border-radius: 4px; min-height: 24px; }}
QScrollBar:horizontal {{ background: {g['PANEL']}; height: 10px; }}
QScrollBar::handle:horizontal {{ background: {g['BORDER']}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

QFrame#featureDialog {{ background: {g['PANEL']}; border: 1px solid {g['BORDER']}; border-radius: 6px; }}
QLabel#dialogTitle {{ font-weight: 700; font-size: 13px; }}
QLabel#dialogError {{ color: {g['ERROR']}; }}
QToolButton#okButton {{ color: {g['OK_GREEN']}; font-size: 18px; font-weight: 800; padding: 0 6px; }}
QToolButton#cancelButton {{ color: {g['ERROR']}; font-size: 18px; font-weight: 800; padding: 0 6px; }}
QToolButton#segment {{ border: 1px solid {g['BORDER']}; border-radius: 0; padding: 4px 8px; background: {g['PANEL']}; }}
QToolButton#segment:checked {{ background: {g['ACCENT']}; color: white; border-color: {g['ACCENT']}; }}
QLineEdit, QComboBox, QDoubleSpinBox {{ border: 1px solid {g['BORDER']}; border-radius: 4px; padding: 3px 6px; background: {g['INPUT']}; }}
QComboBox QAbstractItemView {{ background: {g['INPUT']}; border: 1px solid {g['BORDER']}; selection-background-color: {g['ACCENT_LIGHT']}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {g['ACCENT']}; }}
QLineEdit[invalid="true"] {{ border-color: {g['ERROR']}; background: {g['INVALID_BG']}; }}
QFrame#selField {{ border: 1px solid {g['BORDER']}; border-radius: 4px; background: {g['PANEL_ALT']}; }}
QFrame#selField[active="true"] {{ border: 2px solid {g['ACCENT']}; background: {g['ACTIVE_FIELD']}; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {g['BORDER']}; border-radius: 3px; background: {g['INPUT']}; }}
QCheckBox::indicator:checked {{ background: {g['ACCENT']}; border-color: {g['ACCENT']}; image: url({CHECK_ICON}); }}
QSlider::groove:horizontal {{ height: 4px; background: {g['BORDER']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {g['ACCENT']}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {g['PANEL']}; border: 1px solid {g['TEXT_MUTED']}; }}
QMenu {{ background: {g['INPUT']}; border: 1px solid {g['BORDER']}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px 5px 12px; border-radius: 3px; }}
QMenu::item:selected {{ background: {g['ACCENT_LIGHT']}; }}
QMenu::item:disabled {{ color: {g['TEXT_MUTED']}; }}
QMenu::separator {{ height: 1px; background: {g['BORDER']}; margin: 4px 6px; }}
QMenu::indicator:checked {{ width: 10px; height: 10px; background: {g['ACCENT']}; border-radius: 2px; margin-left: 4px; }}
QStatusBar {{ background: {g['PANEL']}; border-top: 1px solid {g['BORDER']}; }}
QMessageBox, QInputDialog, QDialog {{ background: {g['PANEL']}; }}
QPushButton {{ border: 1px solid {g['BORDER']}; border-radius: 4px; padding: 4px 14px; background: {g['INPUT']}; }}
QPushButton:hover {{ border-color: {g['ACCENT']}; }}
QPushButton:default {{ border-color: {g['ACCENT']}; }}
QToolTip {{ background: #2b2f36; color: white; border: none; padding: 4px 6px; }}
"""


apply("light")
STYLESHEET = stylesheet()   # kept for older imports
