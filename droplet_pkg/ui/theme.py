"""Application-wide look: colour tokens, Qt palette and stylesheet.

``apply_theme(app, "bright" | "dark")`` restyles every window at once, so the
Display menu's Dark mode darkens the whole interface and not only the plot.
Widgets can opt into a few named styles through ``objectName`` or the
``role`` property (see the stylesheet below):

    objectName "Sidebar"      the left panel of the main window
    objectName "Card"         a rounded group inside the sidebar / peaks window
    objectName "CardTitle"    the small caps title of a card
    objectName "MainToolbar"  the main window's icon toolbar
    role "muted"              secondary text
    role "chip"               a small rounded tag (file mode, dt, …)
"""

import os

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui


ACCENT = "#2f7de1"      # Dropli blue, shared with the Dropli chat and tour

THEMES = {
    "bright": {
        "bg":          "#f3f4f7",
        "surface":     "#ffffff",
        "surface2":    "#f7f8fa",
        "hover":       "#e9edf3",
        "pressed":     "#dde3ec",
        "border":      "#d8dce3",
        "border_soft": "#e6e9ee",
        "text":        "#1d2330",
        "muted":       "#6b7383",
        "accent":      ACCENT,
        "accent_text": "#ffffff",
        "accent_soft": "#dce9fb",
        "accent_line": "#9cc2f2",
        "icon":        "#3b4352",
        "pos":         "#d6472b",
        "neg":         "#2f7de1",
        "plot_bg":     "w",
    },
    "dark": {
        "bg":          "#15181e",
        "surface":     "#1d2129",
        "surface2":    "#232832",
        "hover":       "#2b313c",
        "pressed":     "#343b48",
        "border":      "#353c49",
        "border_soft": "#2a303a",
        "text":        "#e3e7ee",
        "muted":       "#939cad",
        "accent":      "#4b95ec",
        "accent_text": "#ffffff",
        "accent_soft": "#203552",
        "accent_line": "#36598a",
        "icon":        "#c9d0dc",
        "pos":         "#f07a5f",
        "neg":         "#6aaaf3",
        "plot_bg":     "k",
    },
}

_current = ["bright"]
_style_set = [False]
# Application-wide event filters (Python) to pause while re-styling: each one
# would otherwise see the ~30 events re-styling sends to every widget.
_paused_filters = []


def pause_during_restyle(event_filter):
    _paused_filters.append(event_filter)
_listeners = []


def current_mode():
    return _current[0]


def tokens(mode=None):
    """Colour tokens of ``mode`` (default: the active theme)."""
    return THEMES.get(mode or _current[0], THEMES["bright"])


def on_theme_changed(fn):
    """Call ``fn(tokens)`` after every theme change (e.g. to recolour icons)."""
    _listeners.append(fn)


def off_theme_changed(fn):
    """Stop calling ``fn`` (e.g. when the window it updates is deleted)."""
    if fn in _listeners:
        _listeners.remove(fn)


def _palette(t):
    p = QtGui.QPalette()
    C = QtGui.QColor
    R = QtGui.QPalette.ColorRole
    G = QtGui.QPalette.ColorGroup
    p.setColor(R.Window,          C(t["bg"]))
    p.setColor(R.WindowText,      C(t["text"]))
    p.setColor(R.Base,            C(t["surface"]))
    p.setColor(R.AlternateBase,   C(t["surface2"]))
    p.setColor(R.ToolTipBase,     C(t["surface"]))
    p.setColor(R.ToolTipText,     C(t["text"]))
    p.setColor(R.PlaceholderText, C(t["muted"]))
    p.setColor(R.Text,            C(t["text"]))
    p.setColor(R.Button,          C(t["surface"]))
    p.setColor(R.ButtonText,      C(t["text"]))
    p.setColor(R.BrightText,      C("#ffffff"))
    p.setColor(R.Highlight,       C(t["accent"]))
    p.setColor(R.HighlightedText, C(t["accent_text"]))
    p.setColor(R.Link,            C(t["accent"]))
    p.setColor(R.Light,           C(t["surface"]))
    p.setColor(R.Midlight,        C(t["border_soft"]))
    p.setColor(R.Mid,             C(t["border"]))
    p.setColor(R.Dark,            C(t["pressed"]))
    p.setColor(R.Shadow,          C("#000000"))
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(G.Disabled, role, C(t["muted"]))
    p.setColor(G.Disabled, R.Base, C(t["surface2"]))
    p.setColor(G.Disabled, R.Button, C(t["surface2"]))
    return p


def _glyph_files(mode, t):
    """Draw the little images the stylesheet needs (check mark, arrows) into
    a cache folder and return their paths, keyed by name."""
    folder = os.path.join(QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.TempLocation), "droplet_theme", mode)
    os.makedirs(folder, exist_ok=True)

    def draw(name, color, fn, w=12, h=12):
        path = os.path.join(folder, name + ".png")
        pm = QtGui.QPixmap(w * 2, h * 2)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.scale(2, 2)
        pen = QtGui.QPen(QtGui.QColor(color), 1.6)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        fn(p)
        p.end()
        pm.save(path)
        return path.replace("\\", "/")

    P = QtCore.QPointF
    poly = lambda *pts: (lambda p: p.drawPolyline(QtGui.QPolygonF([P(*q) for q in pts])))
    return {
        "check":      draw("check", t["accent_text"], poly((2.5, 6.2), (5, 8.6), (9.5, 3.4))),
        "partial":    draw("partial", t["accent_text"], poly((3, 6), (9, 6))),
        "down":       draw("down", t["muted"], poly((3, 4.5), (6, 7.5), (9, 4.5))),
        "up":         draw("up", t["muted"], poly((3, 7.5), (6, 4.5), (9, 7.5))),
        "down_off":   draw("down_off", t["border"], poly((3, 4.5), (6, 7.5), (9, 4.5))),
        "up_off":     draw("up_off", t["border"], poly((3, 7.5), (6, 4.5), (9, 7.5))),
    }


def _stylesheet(t, g):
    return f"""
* {{ outline: none; }}
QWidget {{ font-size: 10pt; }}
QToolTip {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 6px; padding: 6px 8px;
}}

/* ── Buttons ─────────────────────────────────────────── */
QPushButton {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 6px;
    padding: 3px 10px; min-height: 18px;
}}
QPushButton:hover    {{ background: {t['hover']}; }}
QPushButton:pressed  {{ background: {t['pressed']}; }}
QPushButton:checked  {{ background: {t['accent_soft']}; border-color: {t['accent_line']}; color: {t['text']}; }}
QPushButton:disabled {{ color: {t['muted']}; background: {t['surface2']}; border-color: {t['border_soft']}; }}
QPushButton:flat     {{ background: transparent; border: none; }}
QPushButton:flat:hover {{ background: {t['hover']}; }}
QPushButton[compact="true"] {{ padding: 0px; }}
QPushButton[role="primary"] {{
    background: {t['accent']}; color: {t['accent_text']}; border-color: {t['accent']};
}}
QPushButton[role="primary"]:hover {{ background: {t['accent_line']}; }}

QToolButton {{
    background: transparent; color: {t['text']};
    border: 1px solid transparent; border-radius: 6px; padding: 3px;
}}
QToolButton:hover   {{ background: {t['hover']}; }}
QToolButton:pressed {{ background: {t['pressed']}; }}
QToolButton:checked {{ background: {t['accent_soft']}; border-color: {t['accent_line']}; }}
QToolButton::menu-indicator {{ image: none; width: 0px; }}

/* ── Inputs ──────────────────────────────────────────── */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 6px;
    selection-background-color: {t['accent']}; selection-color: {t['accent_text']};
}}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ padding: 2px 6px; min-height: 18px; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t['accent']}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {t['muted']}; background: {t['surface2']}; border-color: {t['border_soft']};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {t['accent_line']}; }}
QComboBox {{ padding-right: 18px; }}
QComboBox::drop-down {{ border: none; width: 18px; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({g['down']}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({g['down_off']}); }}
QComboBox[dense="true"] {{ padding: 1px 3px; padding-right: 14px; }}
QComboBox[dense="true"]::drop-down {{ width: 14px; }}
/* A plain list (not the menu-style popup), as tall as its items: padding on
   the view itself is not counted by Qt when it sizes the popup, which hid
   the last values behind scroll arrows. */
QComboBox {{ combobox-popup: 0; }}
QComboBox QAbstractItemView {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border']}; padding: 0px; outline: none;
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 16px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    border: none; width: 16px; margin-top: 1px; margin-right: 1px;
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    border: none; width: 16px; margin-bottom: 1px; margin-right: 1px;
}}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {t['hover']}; border-radius: 3px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({g['up']}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({g['down']}); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{ image: url({g['up_off']}); }}
QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{ image: url({g['down_off']}); }}

QCheckBox, QRadioButton {{ spacing: 6px; color: {t['text']}; background: transparent; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {t['muted']}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border-radius: 4px;
    border: 1px solid {t['border']}; background: {t['surface']};
}}
QCheckBox::indicator:hover {{ border-color: {t['accent']}; }}
QCheckBox::indicator:checked {{
    background: {t['accent']}; border-color: {t['accent']}; image: url({g['check']});
}}
QCheckBox::indicator:indeterminate {{
    background: {t['accent']}; border-color: {t['accent']}; image: url({g['partial']});
}}
QCheckBox::indicator:disabled {{ background: {t['surface2']}; border-color: {t['border_soft']}; }}
QCheckBox::indicator:checked:disabled {{ background: {t['accent_line']}; border-color: {t['accent_line']}; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border-radius: 8px;
    border: 1px solid {t['border']}; background: {t['surface']};
}}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {t['accent_text']}, stop:0.35 {t['accent_text']}, stop:0.45 {t['accent']}, stop:1 {t['accent']});
    border-color: {t['accent']};
}}

/* ── Sliders ─────────────────────────────────────────── */
QSlider::groove:horizontal {{ height: 4px; background: {t['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
QSlider::sub-page:horizontal:disabled {{ background: {t['border']}; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0; border-radius: 8px;
    background: {t['surface']}; border: 1px solid {t['accent_line']};
}}
QSlider::handle:horizontal:hover {{ border-color: {t['accent']}; }}
QSlider::handle:horizontal:disabled {{ border-color: {t['border']}; }}

/* ── Menus ───────────────────────────────────────────── */
QMenuBar {{ background: {t['bg']}; color: {t['text']}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 4px 9px; border-radius: 5px; }}
QMenuBar::item:selected {{ background: {t['hover']}; }}
QMenuBar::item:pressed  {{ background: {t['pressed']}; }}
QMenu {{
    background: {t['surface']}; color: {t['text']};
    border: 1px solid {t['border']}; border-radius: 8px; padding: 5px;
}}
QMenu::item {{ padding: 5px 22px 5px 24px; border-radius: 5px; }}
QMenu::item:selected {{ background: {t['accent_soft']}; color: {t['text']}; }}
QMenu::item:disabled {{ color: {t['muted']}; }}
QMenu::separator {{ height: 1px; background: {t['border_soft']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 6px; }}

/* ── Scroll bars ─────────────────────────────────────── */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {t['border']}; border-radius: 3px; min-height: 24px; min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {t['muted']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollArea {{ background: transparent; border: none; }}

/* ── Containers ──────────────────────────────────────── */
QGroupBox {{
    border: 1px solid {t['border_soft']}; border-radius: 8px;
    margin-top: 14px; padding: 8px 6px 6px 6px; background: {t['surface']};
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: {t['muted']}; font-weight: 600;
}}
QTabWidget::pane {{ border: 1px solid {t['border_soft']}; border-radius: 8px; top: -1px; background: {t['surface']}; }}
QTabBar::tab {{
    background: transparent; color: {t['muted']}; padding: 6px 12px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {t['text']}; border-bottom-color: {t['accent']}; }}
QTabBar::tab:hover    {{ color: {t['text']}; }}
QHeaderView::section {{
    background: {t['surface2']}; color: {t['muted']}; border: none;
    border-bottom: 1px solid {t['border_soft']}; padding: 4px 6px; font-weight: 600;
}}
QTableView, QTreeView, QListView, QTableWidget, QTreeWidget, QListWidget {{
    background: {t['surface']}; alternate-background-color: {t['surface2']};
    border: 1px solid {t['border_soft']}; border-radius: 6px;
    gridline-color: {t['border_soft']};
    selection-background-color: {t['accent_soft']}; selection-color: {t['text']};
}}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {t['accent_line']}; }}
QSplitter::handle:horizontal {{ width: 5px; }}
QSplitter#Sidebar::handle:vertical {{ height: 10px; background: transparent; }}
QSplitter#Sidebar::handle:vertical:hover {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 transparent,
        stop:0.4 transparent, stop:0.41 {t['accent_line']}, stop:0.59 {t['accent_line']},
        stop:0.6 transparent, stop:1 transparent);
}}
QSplitter::handle:vertical {{ height: 5px; }}
QStatusBar {{ background: {t['bg']}; color: {t['muted']}; border-top: 1px solid {t['border_soft']}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{
    background: {t['surface2']}; border: 1px solid {t['border']}; border-radius: 6px;
    text-align: center; color: {t['text']}; min-height: 14px;
}}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 5px; }}

/* ── Named parts of the main window ─────────────────── */
QToolBar#MainToolbar {{
    background: {t['bg']}; border: none;
    border-bottom: 1px solid {t['border_soft']}; padding: 3px 6px; spacing: 2px;
}}
QToolBar#MainToolbar::separator {{ width: 1px; background: {t['border']}; margin: 5px 6px; }}

QWidget#Sidebar {{ background: {t['bg']}; }}
QFrame#Card {{
    background: {t['surface']}; border: 1px solid {t['border_soft']}; border-radius: 10px;
}}
QToolButton#CardTitleButton {{
    color: {t['muted']}; font-size: 8pt; font-weight: 700; letter-spacing: 1px;
    border: none; padding: 2px 2px; background: transparent;
}}
QToolButton#CardTitleButton:hover {{ color: {t['text']}; }}
QToolButton#CardTitleButton:checked {{ background: transparent; border: none; }}
QLabel#CardTitle {{
    color: {t['muted']}; font-size: 8pt; font-weight: 700; letter-spacing: 1px;
}}
QLabel#FolderName {{ font-size: 11pt; font-weight: 700; color: {t['text']}; }}
QLabel[role="muted"] {{ color: {t['muted']}; }}
QLabel#SelHandle {{
    min-width: 12px; max-width: 12px; margin: 1px;
    border: 1px solid {t['border']}; border-radius: 3px; background: transparent;
}}
QLabel#SelHandle[selected="true"] {{ background: {t['accent']}; border-color: {t['accent']}; }}
QLineEdit[unticked="true"] {{ color: {t['muted']}; font-style: italic; }}
QFrame#ResLegendEntry {{
    background: {t['surface2']}; border: 1px solid {t['border_soft']}; border-radius: 6px;
}}
QFrame#ResLegendEntry:hover {{ border-color: {t['accent_line']}; background: {t['accent_soft']}; }}
QLabel#DragHandle {{ color: {t['muted']}; font-size: 14px; }}
QPushButton[lstate="0"] {{ border: 1px solid {t['border']}; color: {t['muted']}; padding: 0px; border-radius: 4px; }}
QPushButton[lstate="1"] {{ border: 1px solid #2d6e3e; background: #2d6e3e; color: white; padding: 0px; border-radius: 4px; }}
QPushButton[lstate="2"] {{ border: 1px solid #2d4a8a; background: #2d4a8a; color: white; padding: 0px; border-radius: 4px; }}
QPushButton[lstate="3"] {{ border: 1px solid #8a5c2d; background: #8a5c2d; color: white; padding: 0px; border-radius: 4px; }}
QLabel[role="chip"] {{
    background: {t['accent_soft']}; color: {t['text']};
    border-radius: 8px; padding: 1px 8px; font-size: 8pt;
}}
QFrame#OverlayCard {{
    background: {t['surface2']}; border: 1px solid {t['border_soft']}; border-radius: 8px;
}}
QListView#FileList {{ border: none; background: transparent; padding: 0px; }}
QListView#FileList::item {{ border-radius: 6px; }}
QWidget#PlotFrame {{
    background: {t['surface']}; border: 1px solid {t['border_soft']}; border-radius: 10px;
}}
QWidget#TitleBar {{ background: transparent; }}
QPushButton#SidebarToggle {{ border: none; background: transparent; padding: 0px; }}
QLabel#StatusCursor {{ color: {t['text']}; font-family: monospace; padding: 0 8px; }}
QLabel#StatusMode {{
    color: {t['accent']}; font-weight: 600; padding: 0 8px;
}}
"""


def apply_theme(app, mode="bright"):
    """Restyle the whole application in ``mode`` ("bright" or "dark")."""
    # Re-styling has to visit every widget of every window, so it is the slow
    # part (seconds with hundreds of peak lists): do it once, without repaints
    # or our Show watcher in between, under the wait cursor.
    mode = "dark" if mode == "dark" else "bright"
    _current[0] = mode
    t = tokens(mode)
    windows = [w for w in app.topLevelWidgets() if w.isVisible()]
    busy = bool(windows)
    if busy:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.CursorShape.WaitCursor)
    for w in windows:
        w.setUpdatesEnabled(False)
    paused = [f for f in [_watcher] + _paused_filters if f is not None]
    for f in paused:
        app.removeEventFilter(f)
    try:
        if not _style_set[0]:                 # once: app.style() is a stylesheet
            app.setStyle("Fusion")            # wrapper afterwards, never "Fusion"
            _style_set[0] = True
        app.setPalette(_palette(t))
        app.setStyleSheet(_stylesheet(t, _glyph_files(mode, t)))
        for fn in list(_listeners):
            try:
                fn(t)
            except Exception:
                pass
    finally:
        for f in paused:
            app.installEventFilter(f)
        for w in windows:
            try:
                w.setUpdatesEnabled(True)
            except RuntimeError:
                pass
        if busy:
            QtWidgets.QApplication.restoreOverrideCursor()


def _mark_compact(b):
    if b.property("compact") is None and b.maximumWidth() <= 34:
        b.setProperty("compact", True)
        b.style().unpolish(b)
        b.style().polish(b)


def _widen_fixed_box(w):
    """Spin and combo boxes given a fixed width for the old, unpadded look
    would now cut their text ("10 p", "ne"): widen them to what they need."""
    if w.property("widened") is not None:
        return
    w.setProperty("widened", True)
    if w.minimumWidth() != w.maximumWidth():
        return
    need = w.sizeHint().width()
    if isinstance(w, QtWidgets.QComboBox):
        need = min(need, w.maximumWidth() + 40)    # never balloon a file list
    if need > w.maximumWidth():
        w.setFixedWidth(need)


class _CompactButtonWatcher(QtCore.QObject):
    """Adapts widgets sized for the old look as they are first shown, so rows
    and windows built later get the same treatment:
      - small fixed-size buttons (×, ↺, ?, colour swatches) get no padding, so
        their glyph is not squeezed out by the stylesheet's padding;
      - fixed-width spin / combo boxes are widened to fit their text."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Show:
            if isinstance(obj, QtWidgets.QPushButton):
                _mark_compact(obj)
            elif isinstance(obj, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
                _widen_fixed_box(obj)
                if isinstance(obj, QtWidgets.QComboBox) and obj.maxVisibleItems() < 30:
                    obj.setMaxVisibleItems(30)      # Qt shows 10 values by default
        return False


_watcher = None


def install_compact_button_watcher(app):
    global _watcher
    if _watcher is None:
        _watcher = _CompactButtonWatcher(app)
        app.installEventFilter(_watcher)


def follow_droplet_theme(app, owner):
    """For Droplet's helper programs (test suite, updater), which run in their
    own process: use the theme saved by Droplet, and follow it when it is
    switched in Droplet while the helper is open (checked every second)."""
    store = QtCore.QSettings("LILBID", "PeakViewer")

    def saved_mode():
        store.sync()
        return "dark" if store.value("display_mode", "bright") == "dark" else "bright"

    install_compact_button_watcher(app)
    apply_theme(app, saved_mode())
    timer = QtCore.QTimer(owner)
    timer.setInterval(1000)
    timer.timeout.connect(lambda: saved_mode() != current_mode() and apply_theme(app, saved_mode()))
    timer.start()
    return timer
