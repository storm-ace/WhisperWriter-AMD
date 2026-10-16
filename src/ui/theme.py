"""
Design tokens, global stylesheet and small widget helpers for the WhisperWriter UI.

Two palettes (dark "graphite" and light "paper") and four accents. The active values live
in this module's globals (BG, TEXT, ACCENT, ...) and are swapped by `set_theme()`; code
always reads them as `theme.X` at the time it builds a widget, so rebuilding the windows
after `set_theme()` restyles everything. Widgets opt into styles through the `role`
dynamic property (e.g. role="muted", role="primary") instead of per-widget CSS.
"""

import os
import tempfile

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton, QWidget

from utils import APP_ROOT

THEMES = ('dark', 'light', 'system')

ACCENTS = {
    'orange': '#FF7A45',
    'amber': '#E9A23B',
    'blue': '#4C8DF6',
    'violet': '#9B7BF7',
}

_PALETTES = {
    'dark': {
        'BG': '#0F1012', 'PANEL': '#131417', 'SURFACE': '#17181B', 'SURFACE_2': '#1F2024',
        'HOVER': '#26282D', 'BORDER': '#2A2B30', 'BORDER_2': '#2E3035', 'DIVIDER': '#222328',
        'TEXT': '#ECEDEF', 'TEXT_2': '#DADCE0', 'MUTED': '#A3A5AC', 'FAINT': '#8E9098',
        'CHIP_BG': '#202227', 'CHIP_TEXT': '#B4B6BD',
        'KBD_BG': '#1A1B1F', 'KBD_BORDER': '#34363C', 'KBD_TEXT': '#C9CBD1',
        'TRACK_OFF': '#34363C', 'SCROLL': '#2E3035', 'SCROLL_HOVER': '#3A3C42',
        'WAVE_IDLE': '#3A3C42', 'SELECTION': '#3A4A60',
        'INFO': '#6FB1FF', 'INFO_TEXT': '#9CC7FF', 'INFO_BG': '#17222E',
        'INFOBOX_BG': '#111A24', 'INFOBOX_BORDER': '#1F3247', 'INFOBOX_TEXT': '#8EA9C6',
        'WARN_BG': '#2A2210', 'WARN_TEXT': '#F2C66B',
        'DANGER': '#FF8A80', 'DANGER_BG': '#2A1515', 'DANGER_BORDER': '#4A2222',
        'SHADOW': '#000000', 'KNOB': '#FFFFFF',
    },
    'light': {
        'BG': '#F6F6F7', 'PANEL': '#FFFFFF', 'SURFACE': '#FFFFFF', 'SURFACE_2': '#EFF0F2',
        'HOVER': '#E6E7EA', 'BORDER': '#E1E2E6', 'BORDER_2': '#D3D5DA', 'DIVIDER': '#E6E7EA',
        'TEXT': '#16171A', 'TEXT_2': '#2B2D32', 'MUTED': '#555861', 'FAINT': '#696C75',
        'CHIP_BG': '#EEEFF2', 'CHIP_TEXT': '#454850',
        'KBD_BG': '#FFFFFF', 'KBD_BORDER': '#CDD0D6', 'KBD_TEXT': '#33363C',
        'TRACK_OFF': '#C9CCD2', 'SCROLL': '#D3D5DA', 'SCROLL_HOVER': '#BFC2C8',
        'WAVE_IDLE': '#CDD0D6', 'SELECTION': '#BFD6F6',
        'INFO': '#2F74D8', 'INFO_TEXT': '#1C5DB8', 'INFO_BG': '#E7F0FD',
        'INFOBOX_BG': '#F1F6FD', 'INFOBOX_BORDER': '#CFE0F7', 'INFOBOX_TEXT': '#40618C',
        'WARN_BG': '#FCF2DC', 'WARN_TEXT': '#8A5A00',
        'DANGER': '#C62828', 'DANGER_BG': '#FDECEC', 'DANGER_BORDER': '#F2C4C4',
        'SHADOW': '#5A5F6A', 'KNOB': '#FFFFFF',
    },
}

# Active tokens (overwritten by set_theme). Defaults = dark + orange.
NAME = 'dark'
BG = PANEL = SURFACE = SURFACE_2 = HOVER = BORDER = BORDER_2 = DIVIDER = ''
TEXT = TEXT_2 = MUTED = FAINT = CHIP_BG = CHIP_TEXT = KBD_BG = KBD_BORDER = KBD_TEXT = ''
TRACK_OFF = SCROLL = SCROLL_HOVER = WAVE_IDLE = SELECTION = ''
INFO = INFO_TEXT = INFO_BG = INFOBOX_BG = INFOBOX_BORDER = INFOBOX_TEXT = ''
WARN_BG = WARN_TEXT = DANGER = DANGER_BG = DANGER_BORDER = SHADOW = KNOB = ''
ACCENT = ACCENT_HOVER = ACCENT_INK = ACCENT_SOFT = WARM_BORDER = WARM_TEXT = WARM_HOVER = ''


def _mix(a, b, t):
    """Blend two #RRGGBB colours: t=0 gives a, t=1 gives b."""
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t)).name().upper()


def system_prefers_dark():
    try:
        return QGuiApplication.styleHints().colorScheme() != Qt.ColorScheme.Light
    except Exception:
        return True


def set_theme(name='dark', accent='orange'):
    """Activate a palette ('dark' | 'light' | 'system') and an accent from ACCENTS."""
    resolved = name if name in _PALETTES else ('dark' if system_prefers_dark() else 'light')
    g = globals()
    g['NAME'] = resolved
    g.update(_PALETTES[resolved])
    acc = ACCENTS.get(accent, ACCENTS['orange'])
    dark = resolved == 'dark'
    g['ACCENT'] = acc
    g['ACCENT_HOVER'] = _mix(acc, '#FFFFFF', 0.15) if dark else _mix(acc, '#000000', 0.1)
    g['ACCENT_INK'] = '#160C06' if dark or accent in ('orange', 'amber') else '#FFFFFF'
    g['ACCENT_SOFT'] = _mix(g['BG'], acc, 0.12)
    g['WARM_BORDER'] = _mix(g['BG'], acc, 0.28)
    g['WARM_HOVER'] = _mix(g['BG'], acc, 0.2)
    g['WARM_TEXT'] = _mix(acc, '#FFFFFF', 0.6) if dark else _mix(acc, '#000000', 0.45)
    _icon_cache.clear()


def _pick_family(candidates, fallback):
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return fallback


def ui_font_family():
    return _pick_family(['IBM Plex Sans', 'Segoe UI Variable Text', 'Segoe UI', 'SF Pro Text',
                         'Cantarell', 'Noto Sans'], 'Sans Serif')


def mono_font_family():
    return _pick_family(['JetBrains Mono', 'Cascadia Mono', 'Cascadia Code', 'Consolas',
                         'SF Mono', 'DejaVu Sans Mono'], 'Monospace')


def _chevron_file():
    """QSS can only load images from files, so the combo arrow is written once per colour."""
    path = os.path.join(tempfile.gettempdir(), f'whisperwriter-chevron-{MUTED[1:]}.svg')
    if not os.path.isfile(path):
        with open(path, 'w', encoding='utf-8') as f:
            f.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                    f'stroke="{MUTED}" stroke-width="2" stroke-linecap="round" '
                    f'stroke-linejoin="round">{ICONS["chevron"]}</svg>')
    return path.replace(os.sep, '/')


def stylesheet():
    ui = ui_font_family()
    mono = mono_font_family()
    chevron = _chevron_file()
    return f'''
* {{ font-family: "{ui}"; font-size: 13px; color: {TEXT}; outline: 0; }}
QMainWindow, QDialog, #Root {{ background: {BG}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_2};
    padding: 6px 8px; border-radius: 6px; }}

#TopBar {{ background: {PANEL}; border-bottom: 1px solid {DIVIDER}; }}
#Sidebar, #ContextPanel, #SettingsNav {{ background: {PANEL}; }}
#Sidebar, #SettingsNav {{ border-right: 1px solid {DIVIDER}; }}
#ContextPanel {{ border-left: 1px solid {DIVIDER}; }}
#Center, #SettingsBody {{ background: {BG}; }}
#StorageFoot {{ background: {SURFACE}; border-radius: 8px; }}

QLabel[role="h1"] {{ font-size: 21px; font-weight: 600; }}
QLabel[role="h2"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="brand"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="strong"] {{ font-weight: 500; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="faint"] {{ color: {FAINT}; font-size: 12px; }}
QLabel[role="section"] {{ color: {FAINT}; font-size: 11px; font-weight: 600;
    letter-spacing: 1px; }}
QLabel[role="mono"] {{ font-family: "{mono}"; font-size: 12px; color: {MUTED}; }}
QLabel[role="value"] {{ font-family: "{mono}"; font-size: 12px; color: {TEXT}; }}
QLabel[role="info-mono"] {{ font-family: "{mono}"; font-size: 12px; color: {INFOBOX_TEXT}; }}
QLabel[role="body"] {{ color: {TEXT_2}; font-size: 14px; }}
QLabel[role="chip"] {{ background: {CHIP_BG}; color: {CHIP_TEXT}; border-radius: 6px;
    padding: 2px 8px; font-family: "{mono}"; font-size: 11px; }}
QLabel[role="chip-info"] {{ background: {INFO_BG}; color: {INFO_TEXT}; border-radius: 6px;
    padding: 2px 8px; font-family: "{mono}"; font-size: 11px; }}
QLabel[role="status"] {{ background: {INFO_BG}; color: {INFO_TEXT}; border-radius: 15px;
    padding: 5px 12px; font-size: 12px; }}
QLabel[role="status"][state="starting"] {{ background: {WARN_BG}; color: {WARN_TEXT}; }}
QLabel[role="status"][state="error"] {{ background: {DANGER_BG}; color: {DANGER}; }}
QLabel[role="status"][state="recording"] {{ background: {ACCENT_SOFT}; color: {WARM_TEXT}; }}
QLabel[role="kbd"] {{ font-family: "{mono}"; font-size: 11px; padding: 2px 6px;
    border-radius: 4px; border: 1px solid {KBD_BORDER}; background: {KBD_BG}; color: {KBD_TEXT}; }}
QLabel[role="summary"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 10px 12px; color: {TEXT_2}; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER_2}; border-radius: 8px;
    padding: 0 14px; min-height: 34px; font-weight: 500; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: {FAINT}; }}
QPushButton:focus {{ border-color: {INFO}; }}
QPushButton[role="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: {ACCENT_INK};
    font-weight: 600; }}
QPushButton[role="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[role="warm"] {{ background: {ACCENT_SOFT}; border-color: {WARM_BORDER}; color: {WARM_TEXT}; }}
QPushButton[role="warm"]:hover {{ background: {WARM_HOVER}; }}
QPushButton[role="ghost"] {{ background: transparent; border-color: transparent; color: {MUTED}; }}
QPushButton[role="ghost"]:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton[role="danger"] {{ background: {DANGER_BG}; border-color: {DANGER_BORDER}; color: {DANGER}; }}
QPushButton[role="nav"] {{ background: transparent; border: 0; text-align: left; padding: 0 12px;
    color: {MUTED}; font-weight: 400; min-height: 36px; }}
QPushButton[role="nav"]:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton[role="nav"]:checked {{ background: {SURFACE_2}; color: {TEXT}; }}
QPushButton[role="mode"] {{ background: transparent; border: 0; border-radius: 6px;
    min-height: 28px; padding: 0 12px; color: {MUTED}; font-size: 12px; font-weight: 400; }}
QPushButton[role="mode"]:checked {{ background: {HOVER}; color: {TEXT}; }}
QPushButton[role="record"] {{ background: {ACCENT}; border: 0; border-radius: 26px;
    min-width: 52px; max-width: 52px; min-height: 52px; max-height: 52px; padding: 0; }}
QPushButton[role="record"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[role="record"][recording="true"] {{ background: {WARM_TEXT}; }}
QPushButton[role="engine"], QPushButton[role="swatch"] {{ background: {BG};
    border: 1px solid {BORDER_2}; border-radius: 10px; text-align: left; padding: 12px 14px; }}
QPushButton[role="engine"] {{ min-height: 64px; }}
QPushButton[role="engine"]:checked, QPushButton[role="swatch"]:checked {{
    border: 2px solid {ACCENT}; background: {ACCENT_SOFT}; }}
QToolButton {{ background: transparent; border: 0; border-radius: 6px; padding: 4px; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton[role="pill-stop"] {{ background: {HOVER}; border-radius: 16px;
    min-width: 32px; min-height: 32px; }}

QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {BG}; border: 1px solid {BORDER_2}; border-radius: 8px;
    padding: 0 10px; min-height: 34px; selection-background-color: {SELECTION}; }}
QPlainTextEdit {{ padding: 8px 10px; font-family: "{mono}"; font-size: 12px;
    min-height: 76px; max-height: 76px; }}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{ border-color: {INFO}; }}
QLineEdit[role="search"] {{ background: {SURFACE}; border-color: {BORDER}; }}
QLineEdit[role="palette"] {{ background: transparent; border: 0; font-size: 15px;
    min-height: 52px; padding: 0 4px; }}
QLineEdit[role="title-edit"] {{ font-size: 21px; font-weight: 600; min-height: 36px; }}
QLineEdit[role="mono"] {{ font-family: "{mono}"; font-size: 12px; }}
QComboBox::drop-down {{ border: 0; width: 28px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 14px; height: 14px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER_2};
    selection-background-color: {HOVER}; padding: 4px; }}

QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 3px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}

QListWidget {{ background: transparent; border: 0; }}
QListWidget::item {{ border-radius: 8px; padding: 0; margin: 1px 0; }}
QListWidget::item:hover {{ background: {SURFACE_2}; }}
QListWidget::item:selected {{ background: {SURFACE_2}; }}

QMenu {{ background: {SURFACE}; border: 1px solid {BORDER_2}; border-radius: 8px; padding: 6px; }}
QMenu::item {{ padding: 7px 18px 7px 12px; border-radius: 6px; }}
QMenu::item:selected {{ background: {HOVER}; }}
QMenu::item:disabled {{ color: {FAINT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 4px; }}

#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
#InfoBox {{ background: {INFOBOX_BG}; border: 1px solid {INFOBOX_BORDER}; border-radius: 10px; }}
#Dock {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
#ModeGroup {{ background: {BG}; border: 1px solid {BORDER}; border-radius: 8px; }}
#Entry {{ border-top: 1px solid {DIVIDER}; }}
#Palette {{ background: {SURFACE}; border: 1px solid {BORDER_2}; border-radius: 14px; }}
#PaletteLine {{ background: {BORDER}; }}
#SaveBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; }}
#Toast {{ background: {SURFACE_2}; border: 1px solid {BORDER_2}; border-radius: 10px; }}
'''


def apply_app_theme(app, name='dark', accent='orange'):
    set_theme(name, accent)
    app.setStyle('Fusion')
    font = QFont(ui_font_family())
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(stylesheet())


def set_role(widget, role, **props):
    widget.setProperty('role', role)
    for key, value in props.items():
        widget.setProperty(key, value)
    return widget


def restyle(widget):
    """Re-evaluate the stylesheet after a dynamic property changed."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def label(text='', role=None, wrap=False, selectable=False):
    w = QLabel(text)
    if role:
        w.setProperty('role', role)
    w.setWordWrap(wrap)
    if selectable:
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def button(text='', role=None, icon_name=None, tooltip=None, icon_color=None):
    b = QPushButton(text)
    if role:
        b.setProperty('role', role)
    if icon_name:
        default = {'primary': ACCENT_INK, 'warm': WARM_TEXT, 'nav': MUTED}.get(role, TEXT)
        b.setIcon(icon(icon_name, icon_color or default))
        b.setIconSize(QSize(15, 15))
    if tooltip:
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def tool_button(icon_name, tooltip, color=None, size=16):
    b = QToolButton()
    b.setIcon(icon(icon_name, color or MUTED))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tooltip)
    b.setAccessibleName(tooltip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def dot(color, size=8):
    w = QWidget()
    w.setFixedSize(size, size)
    set_dot_color(w, color)
    return w


def set_dot_color(widget, color):
    widget.setStyleSheet(f'background: {color}; border-radius: {widget.width() // 2}px;')


# Stroke icons (24x24 viewBox, Lucide-style paths drawn in-house).
ICONS = {
    'logo': '<path d="M4 10v4M8 7v10M12 4v16M16 7v10M20 10v4"/>',
    'plus': '<path d="M12 5v14M5 12h14"/>',
    'search': '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
    'settings': '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    'chevron': '<path d="M6 9l6 6 6-6"/>',
    'mic': '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    'x': '<path d="M6 6l12 12M18 6L6 18"/>',
    'copy': '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    'terminal': '<path d="M4 17l6-6-6-6M12 19h8"/>',
    'send': '<path d="M4 12l16-8-6 16-2-7z"/><path d="M12 13l8-9"/>',
    'more': '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    'db': '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/>',
    'back': '<path d="M15 18l-6-6 6-6"/>',
    'chip': '<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 1v4M15 1v4M9 19v4M15 19v4M1 9h4M1 15h4M19 9h4M19 15h4"/>',
    'keyboard': '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>',
    'type': '<path d="M4 7V4h16v3M9 20h6M12 4v16"/>',
    'sparkle': '<path d="M12 3l2.5 5.5L20 11l-5.5 2.5L12 19l-2.5-5.5L4 11l5.5-2.5z"/>',
    'palette': '<circle cx="12" cy="12" r="9"/><circle cx="8" cy="10" r="1.2"/><circle cx="12" cy="7.5" r="1.2"/><circle cx="16" cy="10" r="1.2"/><path d="M12 21a3 3 0 0 1 0-6h1.5a2.5 2.5 0 0 0 0-5"/>',
    'list': '<path d="M4 6h16M4 12h16M4 18h10"/>',
    'check': '<path d="M5 12l5 5 9-10"/>',
    'trash': '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    'edit': '<path d="M4 20h4L19 9l-4-4L4 16v4z"/>',
    'download': '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    'play': '<path d="M7 5l12 7-12 7z"/>',
    'folder': '<path d="M3 6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1z"/>',
}

_icon_cache = {}


def _render_svg(svg, size):
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def icon(name, color=None, size=48):
    color = color or TEXT
    key = (name, color, size)
    if key not in _icon_cache:
        body = ICONS[name].replace('currentColor', color)
        _icon_cache[key] = _render_svg(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</svg>', size)
    return _icon_cache[key]


APP_ICON_FILE = os.path.join(APP_ROOT, 'assets', 'whisperwriter.ico')


def app_icon():
    """Window, taskbar and tray icon (multi-size .ico; drawn on the fly if it is missing)."""
    if os.path.isfile(APP_ICON_FILE):
        return QIcon(APP_ICON_FILE)
    return _render_svg(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<rect x="0.5" y="0.5" width="23" height="23" rx="6" fill="#131417"/>'
        f'<g fill="none" stroke="{ACCENT}" stroke-width="2.2" stroke-linecap="round">'
        f'{ICONS["logo"]}</g></svg>', 64)


set_theme()
