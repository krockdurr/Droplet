# SetProcessDpiAwareness(2) has been intentionally removed.
# Calling it manually makes Windows report physical pixel dimensions to Qt,
# while QT_ENABLE_HIGHDPI_SCALING=0 and AA_Use96Dpi tell Qt to treat all
# coordinates as 96-DPI logical pixels. On any screen with scaling ≠ 100%
# these two systems give contradictory pixel counts, which causes pyqtgraph's
# QGraphicsScene to compute the wrong scene size - the axes appear too short
# and leave white space on the right. Qt6 registers the correct DPI awareness
# level automatically without this call, on Windows only, and does so in a
# way that is consistent with its own coordinate system.
# This block is safe to remove: ctypes.windll only exists on Windows so Mac
# and Linux were never affected by it in the first place.

import os
import sys
import json
import re
from pathlib import Path
import csv
import io
import concurrent.futures
import multiprocessing
# Start-up screen progress (does nothing unless Droplet.py showed the screen)
from droplet_pkg.ui.splash import (splash_step, finish_splash, splash_active,
                                   when_splash_finished)
splash_step(8, "Loading the plotting engine…")
import pyqtgraph as pg
splash_step(20, "Loading data tools…")
import pandas as pd
import numpy as np
# np.trapz was renamed np.trapezoid in numpy 2.0; support both
_trapezoid = getattr(np, "trapezoid", None) or np.trapz
from datetime import datetime
try:
    from PyQt6 import QtWidgets, QtCore, QtGui
    from PyQt6.QtGui import QAction, QActionGroup
    QtWidgets.QAction = QAction
    QtWidgets.QActionGroup = QActionGroup
    # QFrame flat enum shims (PyQt6 requires Shape./Shadow. prefix)
    QtWidgets.QFrame.HLine       = QtWidgets.QFrame.Shape.HLine
    QtWidgets.QFrame.VLine       = QtWidgets.QFrame.Shape.VLine
    QtWidgets.QFrame.NoFrame     = QtWidgets.QFrame.Shape.NoFrame
    QtWidgets.QFrame.StyledPanel = QtWidgets.QFrame.Shape.StyledPanel
    QtWidgets.QFrame.Sunken      = QtWidgets.QFrame.Shadow.Sunken
    QtWidgets.QFrame.Raised      = QtWidgets.QFrame.Shadow.Raised
    QtWidgets.QFrame.Plain       = QtWidgets.QFrame.Shadow.Plain
    # PySide-style names used in this code base (matplotlib's Qt backend used
    # to add them as a side effect of being imported)
    QtCore.Signal = QtCore.pyqtSignal
    QtCore.Slot   = QtCore.pyqtSlot
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
splash_step(32, "Loading signal-processing tools…")
from scipy.sparse import csc_matrix, eye, diags
from scipy.sparse.linalg import spsolve


from droplet_pkg.ui.windows.residuals_viewer import ResidualsViewerWindow
from droplet_pkg.ui.windows.cluster_detection import ClusterDetectionWindow
from droplet_pkg.ui.windows.peak_comparison import PeakComparisonWindow
from droplet_pkg.ui.windows.peak_area import PeakAreaWindow
from droplet_pkg.ui.windows.tutorial import TutorialOverlay
from droplet_pkg.processing.signal import get_tolerance, find_peak_bounds as _find_peak_bounds_orig



from droplet_pkg import APP_VERSION  # read from assets/about/VERSION


# ─────────────────────────────────────────────
#  Settings
# ─────────────────────────────────────────────
settings = QtCore.QSettings("LILBID", "PeakViewer")

def _get_dialog_dir(key):
    """Return the last-used directory for a named dialog context."""
    return settings.value(f"dialog_dir/{key}", "")

def _set_dialog_dir(key, path):
    """Save the directory of a file path for a named dialog context."""
    d = os.path.dirname(path) if os.path.isfile(path) else path
    if d: settings.setValue(f"dialog_dir/{key}", d)

# Bundled example spectra: opened when no data folder has been chosen yet
# (first launch) or the last one no longer exists.
EXAMPLE_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets", "example_spectra")

last_base_dir = settings.value("base_dir", "")
if last_base_dir and os.path.exists(last_base_dir):
    base_dir = last_base_dir
elif os.path.isdir(EXAMPLE_DATA_DIR):
    base_dir = EXAMPLE_DATA_DIR
else:
    base_dir = ""

VIRTUAL_FOLDER_PREFIX = "VIRTUAL FOLDER: "
MAX_RECENT = 10

# ─────────────────────────────────────────────
#  Recent folder / file helpers
# ─────────────────────────────────────────────
def load_recent_folders():
    raw = settings.value("recent_folders", [])
    if isinstance(raw, str): raw = [raw]
    return raw or []

def save_recent_folders(lst): settings.setValue("recent_folders", lst[:MAX_RECENT])

def add_recent_folder(path):
    lst = load_recent_folders()
    if path in lst: lst.remove(path)
    lst.insert(0, path); save_recent_folders(lst)

def load_recent_files():
    raw = settings.value("recent_files", [])
    if isinstance(raw, str): raw = [raw]
    return raw or []

def save_recent_files(lst): settings.setValue("recent_files", lst[:MAX_RECENT])

def add_recent_file(path):
    lst = load_recent_files()
    if path in lst: lst.remove(path)
    lst.insert(0, path); save_recent_files(lst)

# ─────────────────────────────────────────────
#  Virtual folder helpers
# ─────────────────────────────────────────────
def load_virtual_folders():
    raw = settings.value("virtual_folders", {})
    return raw if isinstance(raw, dict) else {}

def save_virtual_folders(vf): settings.setValue("virtual_folders", vf)

def add_virtual_folder(name, file_list):
    vf = load_virtual_folders(); vf[name] = file_list
    save_virtual_folders(vf); add_recent_folder(name)

# ─────────────────────────────────────────────
#  File listing
# ─────────────────────────────────────────────
virtual_file_list = []
is_virtual = False

def list_all_txt_files():
    if is_virtual:
        return list(virtual_file_list)
    if not base_dir or not os.path.exists(base_dir):
        return []
    files = sorted(
        os.path.join(base_dir, f) for f in os.listdir(base_dir)
        if f.lower().endswith((".txt", ".csv", ".tsv", ".dat", ".asc"))
    )
    return files

all_txt_files = list_all_txt_files()

def get_full_path(abs_path):
    return abs_path

def basename(path):
    return os.path.basename(path)

def get_txt_files(polarity):
    if polarity == "All":
        return list(all_txt_files)
    return [p for p in all_txt_files if polarity in os.path.basename(p)]

_DT_RE = re.compile(r'_dt(\d+)', re.IGNORECASE)

def _extract_dt(path):
    """Return the dt value string from a filename, or None if not found."""
    m = _DT_RE.search(os.path.basename(path))
    return m.group(1) if m else None

def get_available_dt_values(polarity=None):
    """
    Sorted list of unique dt values in files matching polarity.
    polarity=None or 'All' → scan all files.
    """
    if polarity and polarity != "All":
        candidates = [p for p in all_txt_files if polarity in os.path.basename(p)]
    else:
        candidates = all_txt_files
    vals = set()
    for p in candidates:
        v = _extract_dt(p)
        if v:
            vals.add(v)
    return sorted(vals, key=lambda x: int(x))

def get_txt_files_filtered(polarity, dt_value):
    """Filter files by both polarity and dt. 'All' means no filter for that dimension."""
    files = get_txt_files(polarity)
    if dt_value == "All":
        return files
    return [p for p in files if _extract_dt(p) == dt_value] 

def _auto_select_polarity():
    """Switch polarity_combo to pos if no neg files exist but pos files do.
    'auto' mode: always auto-detect.
    'neg'/'pos' mode: use that as preferred, but auto-switch if preferred has no files."""
    default_pol = settings.value("default_polarity", "neg")
    neg_files = [p for p in all_txt_files if "neg" in os.path.basename(p)]
    pos_files = [p for p in all_txt_files if "pos" in os.path.basename(p)]

    if default_pol == "auto":
        target = "neg" if neg_files else ("pos" if pos_files else "All")
    elif default_pol == "neg":
        # prefer neg, but switch to pos if neg has nothing
        target = "neg" if neg_files else ("pos" if pos_files else "All")
    elif default_pol == "pos":
        # prefer pos, but switch to neg if pos has nothing
        target = "pos" if pos_files else ("neg" if neg_files else "All")
    else:
        target = default_pol

    idx = polarity_combo.findText(target)
    if idx >= 0 and polarity_combo.currentIndex() != idx:
        polarity_combo.blockSignals(True)
        polarity_combo.setCurrentIndex(idx)
        polarity_combo.blockSignals(False)

def _refresh_dt_combo():
    """Repopulate the dt filter combo for the currently selected polarity."""
    cur = dt_combo.currentText()
    pol = polarity_combo.currentText() if 'polarity_combo' in dir() else "All"
    dt_combo.blockSignals(True)
    dt_combo.clear()
    dt_combo.addItem("All")
    for v in get_available_dt_values(polarity=pol):
        dt_combo.addItem(v)
    idx = dt_combo.findText(cur)
    dt_combo.setCurrentIndex(idx if idx >= 0 else 0)
    dt_combo.blockSignals(False)

# ─────────────────────────────────────────────
#  LILBID name parser
# ─────────────────────────────────────────────
def parse_lilbid_name(path):
    stem = os.path.splitext(os.path.basename(path))[0]
    stem = re.sub(r'^\d{4}[-_]?\d{2}[-_]?\d{2}[-_]?', '', stem)
    stem = stem.replace('_', ' ').replace('-', ' ').strip()
    return stem if stem else os.path.splitext(os.path.basename(path))[0]

# ─────────────────────────────────────────────
#  File format detection
# ─────────────────────────────────────────────
def detect_separator(first_lines):
    candidates = ['\t', ',', ';', ' ']
    scores = {c: 0 for c in candidates}
    for line in first_lines:
        if line.startswith('#'): continue
        for c in candidates:
            parts = line.split(c)
            if len(parts) >= 2: scores[c] += len(parts)
    return max(scores, key=scores.get)

def read_spectrum_file(path, sep=None):
    with open(path, 'r', errors='replace') as fh:
        sample = [fh.readline() for _ in range(20)]
    if sep is None:
        sep = detect_separator(sample)
    try:
        if sep == ' ':
            df = pd.read_csv(path, comment='#', sep=r'\s+', header=None,
                             engine='python', names=['mz', 'intensity'])
        else:
            df = pd.read_csv(path, comment='#', sep=sep, header=None,
                             names=['mz', 'intensity'])
    except Exception as e:
        raise ValueError(f"Could not parse file: {e}")
    df = df.dropna()
    try:
        df['mz']        = pd.to_numeric(df['mz'],        errors='raise')
        df['intensity'] = pd.to_numeric(df['intensity'], errors='raise')
    except Exception:
        df = df.iloc[1:].copy()
        try:
            df['mz']        = pd.to_numeric(df['mz'],        errors='raise')
            df['intensity'] = pd.to_numeric(df['intensity'], errors='raise')
        except Exception as e2:
            raise ValueError(f"File has unexpected columns or format: {e2}")
    if len(df) == 0:
        raise ValueError("File contains no usable data rows.")
    return df[['mz', 'intensity']].reset_index(drop=True)

def read_spectrum_headers(path):
    """Return all '#' comment lines from a spectrum file as a list of strings."""
    headers = []
    try:
        with open(path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith('#'):
                    headers.append(line.rstrip('\n'))
                else:
                    break
    except Exception:
        pass
    return headers

# ─────────────────────────────────────────────
#  Default peak colours
# ─────────────────────────────────────────────
default_peak_colors = [
    QtGui.QColor("#ff3b30"), QtGui.QColor("#34c759"),
    QtGui.QColor("#007aff"), QtGui.QColor("#ff9500"),
    QtGui.QColor("#af52de"), QtGui.QColor("#00c7be"),
]
next_color_index = 0

# Symbols available for peak-list legend entries (pyqtgraph symbol codes → display names)
MARKER_SYMBOL_NAMES = {
    "o":           "Circle ●",
    "s":           "Square ■",
    "t":           "Triangle ▼",
    "t1":          "Triangle ▲",
    "t2":          "Triangle ▶",
    "t3":          "Triangle ◀",
    "d":           "Diamond ◆",
    "+":           "Plus +",
    "x":           "Cross ×",
    "p":           "Pentagon ⬟",
    "h":           "Hexagon ⬢",
    "star":        "Star ★",
    "arrow_up":    "Arrow ↑",
    "arrow_down":  "Arrow ↓",
    "arrow_left":  "Arrow ←",
    "arrow_right": "Arrow →",
    "crosshair":   "Crosshair ⊕",
}
MARKER_SYMBOLS = list(MARKER_SYMBOL_NAMES.keys())
# Just the glyph of each symbol ("Circle ●" → "●"), for compact displays.
MARKER_SYMBOL_GLYPHS = {code: name.split()[-1] for code, name in MARKER_SYMBOL_NAMES.items()}

def _symbol_y(peak_int, n, offset, log_y):
    """Plot y of the n-th symbol stacked over a peak (n = 1 is the first).

    offset is a fraction of the peak height (the "Height offset" percentage):
    the first symbol sits at peak × (1 + offset) on either axis. Further
    symbols step up evenly as seen on the axis: peak × (1 + n·offset) on a
    linear axis, peak × (1 + offset)^n on a log axis. On a log axis the
    result is log10 of that, like every y value there."""
    if log_y:
        if peak_int <= 0:
            return 0
        return float(np.log10(peak_int) + n * np.log10(1.0 + offset))
    return peak_int * (1.0 + n * offset)

# ── Legend fields stored on each peak row ─────────────────────────────────────
# The peak rows are the only source of the peak-list legend. Each row carries:
#   row["legend_symbol"][0]  symbol code, "" = explicitly no symbol,
#                            None = not assigned yet (filled in automatically)
#   row["legend_col"][0]     legend column (1-based), None = column 1
# Whether a row is in the legend is simply its tick in the Peaks window.
# In files these are the optional keys "symbol" and "legend_col"; peak lists
# and projects without them (earlier Droplet versions) load with the defaults.
# ("legend_show", written briefly during development, is ignored.)

def _row_legend_fields(row) -> dict:
    """The row's legend settings as file keys (only those that are set)."""
    d = {}
    if row["legend_symbol"][0] is not None:
        d["symbol"] = row["legend_symbol"][0]
    if row["legend_col"][0] is not None:
        d["legend_col"] = row["legend_col"][0]
    return d

def _apply_row_legend_fields(row, item) -> None:
    """Apply legend keys read from a file; missing or invalid keys keep the defaults."""
    if not isinstance(item, dict):
        return
    sym = item.get("symbol")
    if isinstance(sym, str) and (sym == "" or sym in MARKER_SYMBOL_NAMES):
        row["legend_symbol"][0] = sym
    col = item.get("legend_col")
    if col is not None:
        try:
            row["legend_col"][0] = max(1, min(10, int(col)))
        except (TypeError, ValueError):
            pass

_legend_symbol_icon_cache: dict = {}

def _legend_symbol_icon(code, color):
    """16×16 icon of a legend symbol (None before the drawing code is defined)."""
    drawer = globals().get("LegendPreviewWidget")
    if drawer is None:
        return None
    key = (code, QtGui.QColor(color).rgba())
    icon = _legend_symbol_icon_cache.get(key)
    if icon is None:
        icon = _legend_symbol_icon_cache[key] = _draw_legend_symbol_icon(drawer, code, color)
    return icon

def _draw_legend_symbol_icon(drawer, code, color):
    pm = QtGui.QPixmap(16, 16)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    if code:
        drawer._draw_symbol(p, 8, 8, 5.5, code, QtGui.QColor(color))
    else:
        p.setPen(QtGui.QPen(QtGui.QColor("#999999"), 1.2))
        p.drawLine(4, 8, 12, 8)
    p.end()
    return QtGui.QIcon(pm)

def _copy_row_legend_fields(src, dst) -> None:
    for key in ("legend_symbol", "legend_col"):
        dst[key][0] = src[key][0]

# Peak-list legend entries, always derived from the peak rows by
# _auto_sync_legend_entries() (plus any legend-only labels imported from the
# saved-labels library). Each element:
#   {"label", "color": QColor, "symbol": str|None, "col": int,
#    "row": peak row dict or None, "checked": bool, "shown": bool}
_legend_entries: list = []

# Every legend line for the Legend parameters dialog: one per peak row (rows
# sharing a label each appear), then the legend-only labels.
_legend_row_entries: list = []

# Legend-only labels (imported saved labels without a matching peak row).
# Each element: {"label", "color": QColor, "symbol": str|None, "col": int}
_legend_extra_entries: list = []
_LEGEND_EXTRAS_FILE = Path.home() / ".droplet" / "legend_extra_entries.json"

# Before 3.2 symbols and columns lived in this file, keyed by label. It is only
# read now: rows without a symbol adopt the one saved there for their label.
_LEGEND_ENTRIES_FILE = Path.home() / ".droplet" / "legend_current_entries.json"
_legacy_legend_map: dict = {}   # label -> {"symbol": str|None, "col": int}

# Called (without arguments) after every legend sync, e.g. by the legend dialog.
_legend_listeners: list = []

# Persistent on-screen peak-list legend item (None when hidden)
_peak_legend = None

# Must be set before QApplication is created
# os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
# os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
# os.environ["QT_SCALE_FACTOR"] = "1"

# ─────────────────────────────────────────────
#  App + main window
# ─────────────────────────────────────────────
app = QtWidgets.QApplication.instance()
if app is None:
    app = QtWidgets.QApplication([])
    # Identify as "Droplet" (not "python3") so the desktop matches the window
    # to its launcher (Droplet.desktop) and gives it focus when it opens.
    # QSettings always use explicit names, so this does not move any settings.
    app.setApplicationName("Droplet")
    app.setDesktopFileName("Droplet")
    try:
        app.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass  # removed in PyQt6 6.0+
    # AA_Use96Dpi removed: it overrides Qt6's per-monitor DPI scaling,
    # forcing 96 DPI on all screens. Combined with QT_ENABLE_HIGHDPI_SCALING
    # being active (default in Qt6), this caused pyqtgraph's scene to be
    # the wrong size on any screen with scaling ≠ 100%, making axes appear
    # too short. Qt6 handles DPI correctly on its own without this attribute.

splash_step(45, "Preparing the main window…")
main_win = QtWidgets.QWidget()
main_win.setAcceptDrops(True)
main_layout = QtWidgets.QVBoxLayout()
main_layout.setSpacing(2)
main_layout.setContentsMargins(4, 4, 4, 4)
main_win.setLayout(main_layout)
quit_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), main_win)
quit_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
quit_shortcut.activated.connect(main_win.close)

def _on_screen_changed(screen):
    # On Windows, moving between screens with different DPI scaling causes
    # pyqtgraph's internal QGraphicsScene to retain its old dimensions.
    # updateGeometry()/update() are not enough - only a real resizeEvent
    # forces pyqtgraph to reflow the scene. The +1/-1 nudge triggers that
    # event without visibly changing the window size.
    app.processEvents()
    def _nudge():
        sz = main_win.size()
        main_win.resize(sz.width() + 1, sz.height())
        app.processEvents()
        main_win.resize(sz.width(), sz.height())
        plot_widget.updateGeometry()
        render_plot()
    QtCore.QTimer.singleShot(150, _nudge)
    # END screen-change nudge

def _on_main_win_close(event):
    peaks_win.close()
    if _manual_recal_win_ref is not None:
        try: _manual_recal_win_ref.close()
        except Exception: pass
    if _cluster_win_ref is not None:
        try: _cluster_win_ref.close()
        except Exception: pass
    if _comparison_win_ref is not None:
        try: _comparison_win_ref.close()
        except Exception: pass
    if _area_win_ref is not None:
        try: _area_win_ref.close()
        except Exception: pass
    event.accept()

quit_shortcut.activated.connect(main_win.close)

main_win.closeEvent = _on_main_win_close

# ─────────────────────────────────────────────
#  Peak undo/redo history  (session-only, text edits only)
# ─────────────────────────────────────────────
_peak_history   = []
_peak_redo      = []
_history_locked = False

# ─────────────────────────────────────────────
#  Peaks window
# ─────────────────────────────────────────────
peaks_win = QtWidgets.QWidget()
peaks_win.setWindowTitle("Peaks")
peaks_win.resize(740, 420)

# Root layout: menu bar at top, horizontal splitter below
_pw_root_layout = QtWidgets.QVBoxLayout(peaks_win)
_pw_root_layout.setContentsMargins(0, 0, 0, 0)
_pw_root_layout.setSpacing(0)

peaks_win_menu = QtWidgets.QMenuBar()
pw_file_menu  = peaks_win_menu.addMenu("File")
pw_peaks_menu = peaks_win_menu.addMenu("Peaks")
_pw_root_layout.setMenuBar(peaks_win_menu)

# Splitter: left = existing peaks content, right = confirmation panel
_pw_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
_pw_root_layout.addWidget(_pw_splitter)

_pw_left = QtWidgets.QWidget()
peaks_layout = QtWidgets.QVBoxLayout(_pw_left)
_pw_splitter.addWidget(_pw_left)
_pw_splitter.setCollapsible(0, False)

import_action   = QtWidgets.QAction("Import...",             peaks_win)
export_action   = QtWidgets.QAction("Export...",             peaks_win)
save_pl_action  = QtWidgets.QAction("Save",                  peaks_win)
save_pl_action.setShortcut(QtGui.QKeySequence("Ctrl+S"))
save_pl_action.setShortcutContext(QtCore.Qt.ShortcutContext.WindowShortcut)
pw_file_menu.addAction(import_action)
pw_file_menu.addAction(export_action)
pw_file_menu.addAction(save_pl_action)
pw_file_menu.addSeparator()
unmount_menu    = pw_file_menu.addMenu("Unmount imported file…")
# populated dynamically in _rebuild_unmount_menu()

def _rebuild_unmount_menu():
    unmount_menu.clear()
    saved = _get_saved_peak_files()
    if not saved:
        none_act = QtWidgets.QAction("(no saved files)", peaks_win)
        none_act.setEnabled(False)
        unmount_menu.addAction(none_act)
        return
    for path in saved:
        act = QtWidgets.QAction(os.path.basename(path), peaks_win)
        act.setToolTip(path)
        def _make_remove(p):
            def _do():
                lst = _get_saved_peak_files()
                if p in lst:
                    lst.remove(p)
                settings.setValue("saved_peak_files", json.dumps(lst))
                _rebuild_unmount_menu()
                _update_peak_nav_buttons()
            return _do
        act.triggered.connect(_make_remove(path))
        unmount_menu.addAction(act)

def _get_saved_peak_files():
    raw = settings.value("saved_peak_files", "[]")
    try:
        lst = json.loads(raw) if isinstance(raw, str) else []
        return [p for p in lst if os.path.exists(p)]
    except Exception:
        return []

def _add_to_saved_peak_files(path):
    lst = _get_saved_peak_files()
    if path not in lst:
        lst.append(path)
    settings.setValue("saved_peak_files", json.dumps(lst))
    _rebuild_unmount_menu()

# Mirrors pick_mode_action - defined here, linked after pick_mode_action is created
pw_pick_mode_action = QtWidgets.QAction("Pick Peaks Mode  [P]", peaks_win, checkable=True)

def open_peaks_window():
    peaks_win.show()
    peaks_win.adjustSize()
    peaks_win.raise_()
    peaks_win.activateWindow()

# ─────────────────────────────────────────────
#  Menu bar
# ─────────────────────────────────────────────
splash_step(52, "Building menus and tools…")
menu_bar = QtWidgets.QMenuBar()

# ── File ──────────────────────────────────────
file_menu = menu_bar.addMenu("File")
open_folder_action  = QtWidgets.QAction("Open Folder…",          main_win)
open_files_action   = QtWidgets.QAction("Open Individual Files…", main_win)
refresh_action      = QtWidgets.QAction("Refresh Current File",   main_win)
refresh_action.setShortcut(QtGui.QKeySequence("F5"))
file_menu.addAction(open_folder_action)
file_menu.addAction(open_files_action)
file_menu.addSeparator()
file_menu.addAction(refresh_action)
file_menu.addSeparator()
save_project_action    = QtWidgets.QAction("Save Project",     main_win)
save_project_as_action = QtWidgets.QAction("Save Project As…", main_win)
save_project_action.setShortcut(   QtGui.QKeySequence("Ctrl+S"))
save_project_as_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+S"))
open_project_action = QtWidgets.QAction("Open Project…", main_win)
open_project_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+O"))
file_menu.addAction(save_project_action)
file_menu.addAction(save_project_as_action)
file_menu.addAction(open_project_action)
file_menu.addSeparator()
recent_folders_menu = file_menu.addMenu("Recent Folders")
recent_files_menu   = file_menu.addMenu("Recent Files")

# ── View ──────────────────────────────────────
_stacked_mode    = settings.value("stacked_mode",       False, type=bool)
_log_y = settings.value("log_y", False, type=bool)
_stacked_mirror  = settings.value("stacked_mirror",     False, type=bool)
_stacked_mirror_odd = settings.value("stacked_mirror_odd", False, type=bool)
_sigma3_clip        = settings.value("sigma3_clip",        False, type=bool)
_sigma3_n_sigma     = settings.value("sigma3_n_sigma",     1.0,   type=float)
_stacked_fit_y      = settings.value("stacked_fit_y",      False, type=bool)
_stacked_lock_y     = settings.value("stacked_lock_y",     False, type=bool)
_stacked_lock_ymax  = settings.value("stacked_lock_ymax",  1.05,  type=float)
_stacked_dyn_scale  = settings.value("stacked_dyn_scale",  True,  type=bool)

view_menu = menu_bar.addMenu("View")

mouse_mode_group  = QtWidgets.QActionGroup(main_win)
pan_mode_action   = QtWidgets.QAction("Pan / Navigate",   main_win, checkable=True)
zoom_mode_action  = QtWidgets.QAction("Zoom Box  [Z]",    main_win, checkable=True)
pan_mode_action.setChecked(True)
mouse_mode_group.addAction(pan_mode_action)
mouse_mode_group.addAction(zoom_mode_action)
view_menu.addAction(pan_mode_action)
view_menu.addAction(zoom_mode_action)
view_menu.addSeparator()

crosshair_action    = QtWidgets.QAction("Cross-lines",            main_win, checkable=True)
dyn_scale_action    = QtWidgets.QAction("Dynamic Scale",          main_win, checkable=True)
sigma3_clip_action  = QtWidgets.QAction("Sigma-3 Noise Clipping", main_win, checkable=True)
sigma3_clip_action.setChecked(_sigma3_clip)
lock_axes_action    = QtWidgets.QAction("Lock Axes",              main_win, checkable=True)
grid_action         = QtWidgets.QAction("Show Grid",              main_win, checkable=True)
grid_action.setChecked(settings.value("main_grid", True, type=bool))
subtract_action     = QtWidgets.QAction("Subtract Overlay",       main_win, checkable=True)
subtract_dyn_action = QtWidgets.QAction("  Dynamic Subtraction",  main_win, checkable=True)
view_menu.addAction(crosshair_action)
view_menu.addAction(dyn_scale_action)
view_menu.addAction(sigma3_clip_action)
view_menu.addAction(lock_axes_action)
view_menu.addAction(grid_action)
view_menu.addSeparator()
view_menu.addAction(subtract_action)
view_menu.addAction(subtract_dyn_action)

view_menu.addSeparator()
stacked_mode_action = QtWidgets.QAction("Stacked Spectra Mode", main_win, checkable=True)
stacked_mode_action.setChecked(_stacked_mode)
view_menu.addAction(stacked_mode_action)
stacked_logy_action = QtWidgets.QAction("  Log Y axis", main_win, checkable=True)
stacked_logy_action.setChecked(_log_y)
stacked_logy_action.toggled.connect(lambda v: (
    globals().update({"_log_y": v}),
    settings.setValue("log_y", v),
    render_plot()))
view_menu.addAction(stacked_logy_action)
stacked_mirror_action = QtWidgets.QAction("  Stacked: Mirror every 2nd row", main_win, checkable=True)
stacked_mirror_action.setChecked(_stacked_mirror)
stacked_mirror_action.setToolTip(
    "Reverse the Y axis of every second row so paired spectra face each other.\n"
    "Use 'Mirror odd rows' to switch between even/odd pairing.")
stacked_mirror_action.toggled.connect(lambda v: (
    globals().update({"_stacked_mirror": v}),
    settings.setValue("stacked_mirror", v),
    render_plot()))
view_menu.addAction(stacked_mirror_action)
stacked_mirror_odd_action = QtWidgets.QAction("  Stacked: Mirror odd rows instead", main_win, checkable=True)
stacked_mirror_odd_action.setChecked(_stacked_mirror_odd)
stacked_mirror_odd_action.setToolTip(
    "Switch the mirrored set from even (0-based: 1,3,5…) to odd rows (0,2,4…).")
stacked_mirror_odd_action.toggled.connect(lambda v: (
    globals().update({"_stacked_mirror_odd": v}),
    settings.setValue("stacked_mirror_odd", v),
    render_plot()))
view_menu.addAction(stacked_mirror_odd_action)

stacked_fit_y_action = QtWidgets.QAction("  Stacked: Fit Y to spectra", main_win, checkable=True)
stacked_fit_y_action.setChecked(_stacked_fit_y)
stacked_fit_y_action.setToolTip(
    "Lock the Y range to [0, max intensity across all displayed spectra].")
view_menu.addAction(stacked_fit_y_action)

stacked_lock_y_action = QtWidgets.QAction("  Stacked: Lock Y to [0, x]", main_win, checkable=True)
stacked_lock_y_action.setChecked(_stacked_lock_y)
stacked_lock_y_action.setToolTip(
    "Hard-lock the Y viewing range to [0, x] regardless of zoom or pan.\n"
    "Set x with the spin box below.")
view_menu.addAction(stacked_lock_y_action)

_stacked_lock_ymax_container = QtWidgets.QWidget()
_stacked_lock_ymax_layout = QtWidgets.QHBoxLayout(_stacked_lock_ymax_container)
_stacked_lock_ymax_layout.setContentsMargins(28, 1, 8, 1)
_stacked_lock_ymax_layout.addWidget(QtWidgets.QLabel("Y max:"))
_stacked_lock_ymax_spin = QtWidgets.QDoubleSpinBox()
_stacked_lock_ymax_spin.setRange(0.01, 1000.0)
_stacked_lock_ymax_spin.setValue(_stacked_lock_ymax)
_stacked_lock_ymax_spin.setSingleStep(0.05)
_stacked_lock_ymax_spin.setDecimals(2)
_stacked_lock_ymax_spin.setFixedWidth(75)
_stacked_lock_ymax_spin.setEnabled(_stacked_lock_y)
_stacked_lock_ymax_spin.setToolTip("Upper Y bound for 'Lock Y to [0, x]'")
_stacked_lock_ymax_layout.addWidget(_stacked_lock_ymax_spin)
_stacked_lock_ymax_wa = QtWidgets.QWidgetAction(main_win)
_stacked_lock_ymax_wa.setDefaultWidget(_stacked_lock_ymax_container)
view_menu.addAction(_stacked_lock_ymax_wa)

mz_cursor_action = QtWidgets.QAction("Show m/z at Cursor", main_win, checkable=True)
mz_cursor_action.setChecked(settings.value("mz_cursor", False, type=bool))
view_menu.addAction(mz_cursor_action)
mz_cursor_action.toggled.connect(lambda v: settings.setValue("mz_cursor", v))

_mz_cursor_font_container = QtWidgets.QWidget()
_mz_cursor_font_layout = QtWidgets.QHBoxLayout(_mz_cursor_font_container)
_mz_cursor_font_layout.setContentsMargins(28, 1, 8, 1)
_mz_cursor_font_layout.addWidget(QtWidgets.QLabel("Font size:"))
_mz_cursor_font_spin = QtWidgets.QSpinBox()
_mz_cursor_font_spin.setRange(4, 72)
_mz_cursor_font_spin.setValue(int(settings.value("mz_cursor_font_pt", 11)))
_mz_cursor_font_spin.setSuffix(" pt")
_mz_cursor_font_spin.setFixedWidth(68)
_mz_cursor_font_spin.setToolTip("Font size for the floating m/z label shown near the cursor")
_mz_cursor_font_layout.addWidget(_mz_cursor_font_spin)
_mz_cursor_font_wa = QtWidgets.QWidgetAction(main_win)
_mz_cursor_font_wa.setDefaultWidget(_mz_cursor_font_container)
view_menu.addAction(_mz_cursor_font_wa)

view_menu.addSeparator()
label_overlap_action = QtWidgets.QAction(
    "Labels: Stack vertically (not comma)", main_win, checkable=True)
label_overlap_action.setChecked(settings.value("label_stack_vertical", False, type=bool))
label_overlap_action.setToolTip(
    "When multiple peak labels share the same m/z position:\n"
    "  Unchecked → labels joined by commas on one line\n"
    "  Checked   → labels stacked on separate lines with spacing")
label_overlap_action.toggled.connect(lambda v: (
    settings.setValue("label_stack_vertical", v), render_plot()))
view_menu.addAction(label_overlap_action)
view_menu.addSeparator()
plot_title_action = QtWidgets.QAction("Show Plot Title Bar", main_win, checkable=True)
plot_title_action.setChecked(settings.value("plot_title/show", True, type=bool))
plot_title_action.setToolTip(
    "Show the title bar below the tools row.\n"
    "Auto-populates date, mode and dt from the filename.")
view_menu.addAction(plot_title_action)

crosshair_action.setChecked(settings.value("crosshair", False, type=bool))

# ── Analysis ──────────────────────────────────
analysis_menu = menu_bar.addMenu("Processing")

tof_to_mass_action = QtWidgets.QAction("ToF → Mass Converter…", main_win)
analysis_menu.addAction(tof_to_mass_action)
analysis_menu.addSeparator()

baseline_submenu = analysis_menu.addMenu("Baseline Correction")
baseline_action       = QtWidgets.QAction("Apply to Current Spectrum", main_win, checkable=True)
baseline_batch_action = QtWidgets.QAction("Batch Process Folder…",     main_win)
baseline_submenu.addAction(baseline_action)
baseline_submenu.addAction(baseline_batch_action)
baseline_method_menu  = baseline_submenu.addMenu("Method")
baseline_method_group = QtWidgets.QActionGroup(main_win)
airpls_action = QtWidgets.QAction("airPLS  (LILBID standard)", main_win, checkable=True)
snip_action   = QtWidgets.QAction("SNIP    (atomic MS classic)", main_win, checkable=True)
airpls_action.setChecked(True)
baseline_method_group.addAction(airpls_action)
baseline_method_group.addAction(snip_action)
baseline_method_menu.addAction(airpls_action)
baseline_method_menu.addAction(snip_action)

recal_submenu             = analysis_menu.addMenu("Recalibration")
recal_action              = QtWidgets.QAction("Manual Recalibrate Current…",        main_win)
auto_recal_action         = QtWidgets.QAction("Auto-Recalibrate Current",            main_win)
manual_recal_batch_action = QtWidgets.QAction("Batch Manual Recalibrate…",           main_win)
recal_batch_action        = QtWidgets.QAction("Batch Auto-Recalibrate…",             main_win)
recal_submenu.addAction(recal_action)
recal_submenu.addAction(auto_recal_action)
recal_submenu.addSeparator()
recal_submenu.addAction(manual_recal_batch_action)
recal_submenu.addAction(recal_batch_action)

both_submenu        = analysis_menu.addMenu("Baseline + Recalibration")
both_current_action = QtWidgets.QAction("Apply Both to Current Spectrum", main_win)
both_batch_action   = QtWidgets.QAction("Batch Baseline + Recalibrate…",  main_win)
both_submenu.addAction(both_current_action)
both_submenu.addAction(both_batch_action)

analysis_menu.addSeparator()
view_residuals_action = QtWidgets.QAction("View Recalibration Residuals…", main_win)
analysis_menu.addAction(view_residuals_action)
analysis_menu.addSeparator()

normalize_submenu = analysis_menu.addMenu("Normalize Spectrum")
normalize_current_action = QtWidgets.QAction("Normalize Current to File…", main_win)
normalize_batch_action   = QtWidgets.QAction("Batch Normalize Folder…",    main_win)
normalize_submenu.addAction(normalize_current_action)
normalize_submenu.addAction(normalize_batch_action)

# ── Analysis (new menu, separate from Processing) ──────────────
new_analysis_menu        = menu_bar.addMenu("Analysis")
cluster_detect_action    = QtWidgets.QAction("Cluster Detection…", main_win)
new_analysis_menu.addAction(cluster_detect_action)
area_action = QtWidgets.QAction("Peak list area tools…", main_win)
new_analysis_menu.addAction(area_action)
peak_comparison_action = QtWidgets.QAction("Compare Common/Unique Peaks…", main_win)
new_analysis_menu.addAction(peak_comparison_action)
new_analysis_menu.addSeparator()
area_mode_action  = QtWidgets.QAction("Measure Peak Area   [A]", main_win, checkable=True)
# area_mode_action is now only accessible from the popup window, not directly in menu
ratio_mode_action = QtWidgets.QAction("Measure Peak Ratio  [R]…", main_win)
new_analysis_menu.addAction(ratio_mode_action)

# ── Peaks ─────────────────────────────────────
peaks_menu   = menu_bar.addMenu("Peaks")
peaks_action = QtWidgets.QAction("Open Peaks List", main_win)
peaks_action.setShortcut(QtGui.QKeySequence("Ctrl+P"))
peaks_action.triggered.connect(open_peaks_window)
peaks_menu.addAction(peaks_action)
peaks_menu.addSeparator()
pick_mode_action = QtWidgets.QAction("Pick Peaks Mode  [P]", main_win, checkable=True)
peaks_menu.addAction(pick_mode_action)

# ── Plot ──────────────────────────────────────
plot_menu = menu_bar.addMenu("Plot")

plot_menu.addSeparator()
# ──────────────────────────────────────────────

# ── Legend parameters (before export options) ──
legend_params_action = QtWidgets.QAction("Legend parameters…", main_win)
legend_params_action.setToolTip(
    "Configure the peak list legend: labels, symbols, font, box, columns.\n"
    "Changes are previewed live and applied to the plot and exports.")
plot_menu.addAction(legend_params_action)

_after_legend_sep = plot_menu.addSeparator()

export_png_action       = QtWidgets.QAction("Export as PNG…",            main_win)
export_svg_action       = QtWidgets.QAction("Export as SVG…",            main_win)
export_pdf_action       = QtWidgets.QAction("Export as PDF…",            main_win)
export_pgf_action       = QtWidgets.QAction("Export as PGF…",            main_win)
export_pgf_action.setToolTip(
    "PGF picture for LaTeX: \\usepackage{pgf} and \\input{plot.pgf}.\n"
    "Same view as the window, with native LaTeX text and vector lines.")
copy_plot_action        = QtWidgets.QAction("Copy Plot to Clipboard",     main_win)
copy_plot_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+C"))
export_peaks_csv_action = QtWidgets.QAction("Export Peak Data as CSV…",  main_win)
print_action            = QtWidgets.QAction("Print…",                    main_win)
print_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+P"))
plot_menu.addAction(export_png_action)
plot_menu.addAction(export_svg_action)
plot_menu.addAction(export_pdf_action)
plot_menu.addAction(export_pgf_action)
batch_export_plots_action = QtWidgets.QAction("Batch Export Plots…", main_win)
batch_export_plots_action.setToolTip(
    "Export one plot (PNG, PDF, SVG or PGF) per file visible under the\n"
    "mode / dt filters, each exactly as it looks in the window,\n"
    "keeping the current zoom and peak-list settings.")
plot_menu.addAction(batch_export_plots_action)
plot_menu.addSeparator()
plot_menu.addAction(copy_plot_action)
plot_menu.addSeparator()
plot_menu.addAction(print_action)
plot_menu.addSeparator()
# legend_font_spin removed — font is now set in the Legend parameters dialog

# ── Peak label font/angle widgets (added to the Peaks window, not here) ──
label_font_spin = QtWidgets.QSpinBox()
label_font_spin.setRange(4, 36)
label_font_spin.setValue(int(settings.value("label_font_pt", 10)))
label_font_spin.setSuffix(" pt")
label_font_spin.setFixedWidth(68)
label_font_spin.setToolTip("Font size for peak list and auto-peak labels on the plot.")

# ── Plot-menu: cursor info display font size ──
_cif_container = QtWidgets.QWidget()
_cif_layout = QtWidgets.QHBoxLayout(_cif_container)
_cif_layout.setContentsMargins(28, 1, 8, 1)
_cif_layout.addWidget(QtWidgets.QLabel("Cursor info font:"))
_cursor_info_font_spin = QtWidgets.QSpinBox()
_cursor_info_font_spin.setRange(4, 36)
_cursor_info_font_spin.setValue(int(settings.value("cursor_info_font_pt", 9)))
_cursor_info_font_spin.setSuffix(" pt")
_cursor_info_font_spin.setFixedWidth(68)
_cursor_info_font_spin.setToolTip(
    "Font size for the m/z and intensity readout shown in the corner while hovering.")
_cif_layout.addWidget(_cursor_info_font_spin)
_cif_layout.addStretch()
_cif_wa = QtWidgets.QWidgetAction(main_win)
_cif_wa.setDefaultWidget(_cif_container)
plot_menu.insertAction(_after_legend_sep, _cif_wa)

_cursor_info_font_spin.valueChanged.connect(
    lambda v: settings.setValue("cursor_info_font_pt", v))

_mz_cursor_font_spin.valueChanged.connect(
    lambda v: (settings.setValue("mz_cursor_font_pt", v), _mz_cursor_apply_font()))

label_font_spin.valueChanged.connect(
    lambda v: (settings.setValue("label_font_pt", v), _render_peaks_or_full()))

label_angle_spin = QtWidgets.QSpinBox()
label_angle_spin.setRange(0, 90)
label_angle_spin.setValue(int(settings.value("label_angle_deg", 60)))
label_angle_spin.setSuffix(" °")
label_angle_spin.setFixedWidth(60)
label_angle_spin.setToolTip("Rotation angle for peak labels (0 = horizontal, 90 = vertical).")
label_angle_spin.valueChanged.connect(lambda v: (settings.setValue("label_angle_deg", v), _render_peaks_or_full()))

label_altitude_spin = QtWidgets.QDoubleSpinBox()
label_altitude_spin.setRange(-0.1, 1.0)
label_altitude_spin.setSingleStep(0.02)
label_altitude_spin.setDecimals(2)
label_altitude_spin.setValue(float(settings.value("label_y_offset", 0.0)))
label_altitude_spin.setFixedWidth(68)
label_altitude_spin.setToolTip(
    "Extra altitude for peak labels above the peak tip.\n"
    "In log scale this is added to the log10 offset; in linear scale\n"
    "it multiplies the peak intensity (0.0 = default, 0.1 = 10% higher).")
label_altitude_spin.valueChanged.connect(
    lambda v: (settings.setValue("label_y_offset", v), _render_peaks_or_full()))

# ── Display ───────────────────────────────────
display_menu  = menu_bar.addMenu("Display")
dark_action   = QtWidgets.QAction("Dark",   main_win, checkable=True)
bright_action = QtWidgets.QAction("Bright", main_win, checkable=True)
display_group = QtWidgets.QActionGroup(main_win)
display_group.addAction(dark_action); display_group.addAction(bright_action)
display_menu.addAction(dark_action);  display_menu.addAction(bright_action)
bright_action.setChecked(True)

# ── Tests ─────────────────────────────────────
tests_menu = menu_bar.addMenu("Tests")
run_tests_action = QtWidgets.QAction("Run Test Suite…", main_win)
run_tests_action.setToolTip(
    "Check that Droplet works correctly on this computer; the report can be "
    "saved and sent along with a problem report")
tests_menu.addAction(run_tests_action)
tests_menu.setToolTipsVisible(True)

# ── Help ──────────────────────────────────────
help_menu = menu_bar.addMenu("Help")
tutorial_action       = QtWidgets.QAction("Tutorial (first steps)…", main_win)
help_files_action     = QtWidgets.QAction("Files & Overlays",         main_win)
help_view_action      = QtWidgets.QAction("View & Navigation",        main_win)
help_peaks_action     = QtWidgets.QAction("Peak Lists",               main_win)
help_analysis_action  = QtWidgets.QAction("Processing & Analysis",    main_win)
help_noise_action     = QtWidgets.QAction("Noise Clipping & Normalisation", main_win)
help_export_action    = QtWidgets.QAction("Export & Print",           main_win)
help_shortcuts_action = QtWidgets.QAction("Keyboard Shortcuts",       main_win)
about_action          = QtWidgets.QAction("About Droplet…",           main_win)
check_updates_action  = QtWidgets.QAction("Check for Updates…",       main_win)
previous_versions_action = QtWidgets.QAction("Previous Versions…",     main_win)
help_menu.addAction(tutorial_action)
help_menu.addSeparator()
help_menu.addAction(help_files_action)
help_menu.addAction(help_view_action)
help_menu.addAction(help_peaks_action)
help_menu.addAction(help_analysis_action)
help_menu.addAction(help_noise_action)
help_menu.addAction(help_export_action)
help_menu.addSeparator()
help_menu.addAction(about_action)
help_menu.addAction(help_shortcuts_action)
help_menu.addSeparator()
help_menu.addAction(check_updates_action)
help_menu.addAction(previous_versions_action)

main_layout.setMenuBar(menu_bar)

# ── Keep-open filter for menus with checkable items ──────────────────────────
# By default Qt closes a menu as soon as any action is triggered.  This filter
# intercepts the mouse-release event for checkable actions and consumes it so
# the menu stays open — the user can toggle several options in one visit.
class _KeepOpenFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
            if isinstance(obj, QtWidgets.QMenu):
                action = obj.activeAction()
                if action and action.isCheckable():
                    action.trigger()
                    return True   # eat event → menu stays open
        return super().eventFilter(obj, event)

_keep_open_filter = _KeepOpenFilter()
view_menu.installEventFilter(_keep_open_filter)
display_menu.installEventFilter(_keep_open_filter)

# ─────────────────────────────────────────────
#  Folder path label
# ─────────────────────────────────────────────
folder_path_label = QtWidgets.QLabel()
folder_path_label.setWordWrap(True)
folder_path_label.setStyleSheet("font-size: 10px; color: gray; padding: 1px 4px;")
main_layout.addWidget(folder_path_label)

def update_folder_label():
    if is_virtual:
        folder_path_label.setText("📂 Virtual folder")
    elif base_dir:
        folder_path_label.setText(f"📂 {base_dir}")
    else:
        folder_path_label.setText("📂 No folder selected")

update_folder_label()

# ─────────────────────────────────────────────
#  Collapsible section widget
# ─────────────────────────────────────────────
class CollapsibleSection(QtWidgets.QWidget):
    def __init__(self, title, parent=None, collapsed=False):
        super().__init__(parent)
        self._collapsed = collapsed
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._header = QtWidgets.QPushButton()
        self._header.setFlat(True)
        self._header.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet(
            "QPushButton { text-align: left; padding: 2px 6px; "
            "font-weight: bold; font-size: 11px; border: none; "
            "border-bottom: 1px solid palette(mid); }")
        self._header.clicked.connect(self.toggle)
        outer.addWidget(self._header)
        self._content = QtWidgets.QWidget()
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 2, 4, 2)
        self._content_layout.setSpacing(2)
        outer.addWidget(self._content)
        self._update_header(title)
        self._content.setVisible(not collapsed)

    def _update_header(self, title=None):
        if title: self._title = title
        arrow = "▸" if self._collapsed else "▾"
        self._header.setText(f"  {arrow}  {self._title}")

    def toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._update_header()

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)

# ─────────────────────────────────────────────
#  SECTION 1: Files & Overlays
# ─────────────────────────────────────────────
files_section = CollapsibleSection("Files && Overlays", collapsed=False)
main_layout.addWidget(files_section)

file_row = QtWidgets.QHBoxLayout()
file_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

polarity_combo = QtWidgets.QComboBox()
polarity_combo.addItems(["neg", "pos", "All"]); polarity_combo.setFixedWidth(60)

class ElidedComboBox(QtWidgets.QComboBox):
    """ComboBox that shows full text in both the collapsed widget and the
    drop-down popup (no middle-elision anywhere)."""

    def showPopup(self):
        # Widen the popup so long filenames are never cropped in the list
        fm = self.fontMetrics()
        max_w = max((fm.boundingRect(self.itemText(i)).width()
                     for i in range(self.count())), default=0)
        self.view().setMinimumWidth(max_w + 32)
        super().showPopup()

class _TrimmedDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox whose display strips unnecessary trailing zeros."""
    def textFromValue(self, value):
        return f"{value:.4f}".rstrip('0').rstrip('.')

combo = ElidedComboBox()
combo.setMinimumWidth(100); combo.setMaximumWidth(2000)
combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                    QtWidgets.QSizePolicy.Policy.Fixed)

refresh_btn = QtWidgets.QPushButton("↺")
refresh_btn.setFixedWidth(26); refresh_btn.setToolTip("Refresh / reload current file (F5)")

fmt_combo = QtWidgets.QComboBox()
fmt_combo.addItems(["Auto-detect", "Tab", "Comma", "Semicolon", "Space"])
fmt_combo.setFixedWidth(110); fmt_combo.setToolTip("Column separator")

splash_step(56)                      # keeps the start-up screen animating
main_toggle = QtWidgets.QCheckBox()
main_toggle.setChecked(True)
main_toggle.setToolTip("Show/hide the main spectrum")

# Color button for main spectrum
_main_spectrum_color = [QtGui.QColor(settings.value("main_spectrum_color", "#888888"))]
main_color_btn = QtWidgets.QPushButton()
main_color_btn.setFixedSize(22, 22)
main_color_btn.setToolTip("Choose colour for the main spectrum")
main_color_btn.setStyleSheet(
    f"background-color: {_main_spectrum_color[0].name()}; border: 1px solid gray; border-radius: 3px;")

def _pick_main_color():
    c = QtWidgets.QColorDialog.getColor(_main_spectrum_color[0], main_win, "Main spectrum colour")
    if c.isValid():
        _main_spectrum_color[0] = c
        settings.setValue("main_spectrum_color", c.name())
        main_color_btn.setStyleSheet(
            f"background-color: {c.name()}; border: 1px solid gray; border-radius: 3px;")
        _stacked_update_spectrum_colors()

main_color_btn.clicked.connect(_pick_main_color)

file_row.addWidget(main_toggle)
file_row.addWidget(QtWidgets.QLabel("Main file     "))
file_row.addWidget(main_color_btn)
file_row.addWidget(QtWidgets.QLabel(" Mode:"))
file_row.addWidget(polarity_combo)

dt_combo = QtWidgets.QComboBox()
dt_combo.setFixedWidth(80)
dt_combo.setToolTip("Filter files by dt value (from filename). 'All' shows all files.")
dt_combo.addItem("All")
file_row.addWidget(QtWidgets.QLabel("dt:"))
file_row.addWidget(dt_combo)

file_row.addWidget(combo)
file_row.addWidget(refresh_btn)
file_row.addSpacing(8)
file_row.addWidget(QtWidgets.QLabel("Sep:"))
file_row.addWidget(fmt_combo)
file_row.addStretch()
files_section.add_layout(file_row)

def _repopulate_all_overlays():
    for ov in overlay_list:
        pol_val = ov["polarity"].currentText()
        dt_val  = dt_combo.currentText()
        files = get_txt_files_filtered(pol_val, dt_val)
        ov_cb = ov["combo"]
        ov_cb.blockSignals(True); ov_cb.clear()
        if files:
            for p in files:
                ov_cb.addItem(os.path.basename(p), p)
        else:
            ov_cb.addItem(f'No file in "{pol_val}" mode / dt={dt_val}', None)
        ov_cb.blockSignals(False)

dt_combo.currentIndexChanged.connect(lambda _: _repopulate_all_overlays())
dt_combo.currentTextChanged.connect(lambda v: settings.setValue("dt_filter", v))

def get_sep_from_combo():
    return {"Tab": "\t", "Comma": ",", "Semicolon": ";", "Space": " "}.get(fmt_combo.currentText(), None)

overlay_rows_layout = QtWidgets.QVBoxLayout()
files_section.add_layout(overlay_rows_layout)
overlay_list = []
OVERLAY_COLORS = [
    '#e6194b',  # vivid red
    '#3cb44b',  # vivid green
    '#4363d8',  # strong blue
    '#f58231',  # orange
    '#911eb4',  # purple
    '#42d4f4',  # cyan
    '#f032e6',  # magenta
    '#bfef45',  # lime
    '#469990',  # teal
    '#fabed4',  # pink
]

def make_overlay_row(index=0, label=None, path=None, show_save=False):
    row_widget = QtWidgets.QWidget()
    row_layout = QtWidgets.QHBoxLayout()
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
    row_widget.setLayout(row_layout)

    chk_label = label if label else f"Overlay {index+1}"
    toggle  = QtWidgets.QCheckBox(chk_label); toggle.setChecked(False)
    pol     = QtWidgets.QComboBox(); pol.addItems(["neg","pos"]); pol.setFixedWidth(60); pol.setEnabled(False)

    ov_combo = ElidedComboBox()
    ov_combo.setEnabled(False)
    ov_combo.setMinimumWidth(100); ov_combo.setMaximumWidth(2000)
    ov_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    ov_combo.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Fixed)

    remove_ov_btn = QtWidgets.QPushButton("×"); remove_ov_btn.setFixedSize(22, 22)

    # Per-overlay color button
    ov_color_btn = QtWidgets.QPushButton()
    ov_color_btn.setFixedSize(22, 22)
    _ov_init_color = QtGui.QColor(OVERLAY_COLORS[index % len(OVERLAY_COLORS)])
    ov_color_btn.setStyleSheet(
        f"background-color: {_ov_init_color.name()}; border: 1px solid gray; border-radius: 3px;")
    ov_color_btn.setToolTip("Choose colour for this overlay")

    ov_dt_combo = QtWidgets.QComboBox()
    ov_dt_combo.setFixedWidth(70)
    ov_dt_combo.setToolTip("Filter overlay files by dt value. 'All' shows all.")
    ov_dt_combo.setEnabled(False)   # enabled only when overlay is toggled on

    row_layout.addWidget(toggle)
    row_layout.addWidget(ov_color_btn)
    row_layout.addWidget(QtWidgets.QLabel(" Mode:")); row_layout.addWidget(pol)
    row_layout.addWidget(QtWidgets.QLabel(" dt:")); row_layout.addWidget(ov_dt_combo)
    row_layout.addWidget(QtWidgets.QLabel(" File:")); row_layout.addWidget(ov_combo)
    row_layout.addWidget(remove_ov_btn)

    save_btn = None
    if show_save:
        save_btn = QtWidgets.QPushButton("💾 Save"); save_btn.setFixedWidth(70)
        row_layout.addWidget(save_btn)
    row_layout.addStretch()

    ov_data = {"toggle": toggle, "polarity": pol,
               "combo": ov_combo, "dt_combo": ov_dt_combo, "df": None, "widget": row_widget,
               "color": OVERLAY_COLORS[index % len(OVERLAY_COLORS)],
               "color_btn": ov_color_btn,
               "save_btn": save_btn, "fixed_path": path,
               "is_processed": False}

    def _pick_ov_color(_checked=False, od=ov_data):
        cur = QtGui.QColor(od["color"])
        c = QtWidgets.QColorDialog.getColor(cur, main_win, "Overlay colour")
        if c.isValid():
            od["color"] = c.name()
            od["color_btn"].setStyleSheet(
                f"background-color: {c.name()}; border: 1px solid gray; border-radius: 3px;")
            _stacked_update_spectrum_colors()

    ov_color_btn.clicked.connect(_pick_ov_color)

    def _populate_dt_combo():
        """Repopulate the per-overlay dt combo from all_txt_files."""
        cur = ov_dt_combo.currentText()
        ov_dt_combo.blockSignals(True)
        ov_dt_combo.clear()
        ov_dt_combo.addItem("All")
        for v in get_available_dt_values():
            ov_dt_combo.addItem(v)
        idx = ov_dt_combo.findText(cur)
        ov_dt_combo.setCurrentIndex(idx if idx >= 0 else 0)
        ov_dt_combo.blockSignals(False)

    def _populate_combo():
        _populate_dt_combo()
        cur_dt  = ov_dt_combo.currentText()
        cur_pol = pol.currentText()
        files = get_txt_files_filtered(cur_pol, cur_dt)
        # Auto-switch polarity if preferred has no files
        if not files:
            other = "pos" if cur_pol == "neg" else "neg"
            other_files = get_txt_files_filtered(other, cur_dt)
            if other_files:
                pol.blockSignals(True)
                pol.setCurrentText(other)
                pol.blockSignals(False)
                files = other_files
        ov_combo.blockSignals(True); ov_combo.clear()
        if files:
            for p in files:
                ov_combo.addItem(os.path.basename(p), p)
        else:
            pol_txt = pol.currentText()
            ov_combo.addItem(f'No file in "{pol_txt}" mode', None)
        ov_combo.blockSignals(False)

    _populate_combo()

    if path is not None:
        idx = ov_combo.findData(path)
        if idx < 0:
            ov_combo.addItem(os.path.basename(path), path)
            idx = ov_combo.count() - 1
        ov_combo.setCurrentIndex(idx)

    def on_toggle(state):
        en = toggle.isChecked()
        ov_combo.setEnabled(en); pol.setEnabled(en)
        if en: load_overlay_data(ov_data)
        else:  render_plot()

    def on_pol_changed():
        _populate_combo()
        if toggle.isChecked(): load_overlay_data(ov_data)

    def on_remove():
        overlay_list.remove(ov_data)
        overlay_rows_layout.removeWidget(row_widget); row_widget.deleteLater()
        _invalidate_overlay_curves()
        render_plot()

    def on_ov_dt_changed():
        _populate_combo()
        if toggle.isChecked(): load_overlay_data(ov_data)

    def on_toggle(state):
        en = toggle.isChecked()
        ov_combo.setEnabled(en); pol.setEnabled(en); ov_dt_combo.setEnabled(en)
        if en: load_overlay_data(ov_data)
        else:  render_plot()

    toggle.stateChanged.connect(on_toggle)
    pol.currentIndexChanged.connect(lambda _: on_pol_changed())
    ov_dt_combo.currentIndexChanged.connect(lambda _: on_ov_dt_changed())
    ov_combo.currentIndexChanged.connect(
        lambda _: load_overlay_data(ov_data) if toggle.isChecked() else None)
    remove_ov_btn.clicked.connect(on_remove)
    return ov_data, row_widget

def add_overlay_row(label=None, path=None, show_save=False, df_override=None):
    idx = len(overlay_list)
    ov_data, row_widget = make_overlay_row(idx, label=label, path=path, show_save=show_save)
    overlay_list.append(ov_data)
    overlay_rows_layout.addWidget(row_widget)
    if df_override is not None:
        ov_data["df"] = df_override
        ov_data["is_processed"] = True
    return ov_data

# add_overlay_row()

ov_btns_row = QtWidgets.QHBoxLayout()
add_overlay_btn = QtWidgets.QPushButton("+ Add Overlay")
add_overlay_btn.setFixedWidth(110); add_overlay_btn.clicked.connect(lambda: add_overlay_row())
ov_help_btn = QtWidgets.QPushButton("?"); ov_help_btn.setFixedWidth(24)
ov_btns_row.addWidget(add_overlay_btn); ov_btns_row.addWidget(ov_help_btn); ov_btns_row.addStretch()
files_section.add_layout(ov_btns_row)

# ─────────────────────────────────────────────
#  SECTION 2: Tools bar
# ─────────────────────────────────────────────
tools_row = QtWidgets.QHBoxLayout()
tools_row.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

# "Pick into:" - only visible when pick mode is active
pick_into_widget = QtWidgets.QWidget()
pick_into_layout = QtWidgets.QHBoxLayout(pick_into_widget)
pick_into_layout.setContentsMargins(0, 0, 0, 0)
pick_into_layout.setSpacing(4)
pick_into_layout.addWidget(QtWidgets.QLabel("Pick into:"))
pick_row_combo = QtWidgets.QComboBox()
pick_row_combo.setFixedWidth(50); pick_row_combo.setToolTip("Target peak row for click-to-pick")
pick_into_layout.addWidget(pick_row_combo)
pick_into_widget.setVisible(False)   # hidden until pick mode is on
tools_row.addWidget(pick_into_widget)

class JumpSlider(QtWidgets.QSlider):
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            val = QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                event.pos().x(), self.width())
            self.setValue(val)
        super().mousePressEvent(event)

tools_row.addSpacing(16)
tools_row.addWidget(QtWidgets.QLabel("Highlight intensity:"))
highlight_slider = JumpSlider(QtCore.Qt.Orientation.Horizontal)
highlight_slider.setMinimum(10); highlight_slider.setMaximum(255)
highlight_slider.setValue(255); highlight_slider.setFixedWidth(120)
highlight_slider.setToolTip(
    "Highlight colour intensity.\n"
    "At 255 the peak is shown in its full chosen colour.\n"
    "Reducing blends toward white (bright) or black (dark).")
tools_row.addWidget(highlight_slider)
tools_row.addSpacing(16)
tools_row.addWidget(QtWidgets.QLabel("Overlay opacity:"))
overlay_opacity_slider = JumpSlider(QtCore.Qt.Orientation.Horizontal)
overlay_opacity_slider.setMinimum(5); overlay_opacity_slider.setMaximum(100)
overlay_opacity_slider.setValue(80); overlay_opacity_slider.setFixedWidth(120)
overlay_opacity_slider.setToolTip(
    "Opacity of all overlay spectra (5–100 %).\n"
    "Lower values make overlays more transparent so the\n"
    "primary spectrum is easier to read through them.")
tools_row.addWidget(overlay_opacity_slider)
tools_row.addSpacing(16)

stacked_logy_chk = QtWidgets.QCheckBox("Log Y")
stacked_logy_chk.setChecked(_log_y)
stacked_logy_chk.setToolTip("Use logarithmic Y axis")
tools_row.addWidget(stacked_logy_chk)

stacked_mode_chk = QtWidgets.QCheckBox("Stacked")
stacked_mode_chk.setChecked(_stacked_mode)
stacked_mode_chk.setToolTip("Show spectra stacked in rows instead of overlapping")
tools_row.addWidget(stacked_mode_chk)

stacked_dyn_scale_chk = QtWidgets.QCheckBox("Dyn Scale")
stacked_dyn_scale_chk.setChecked(_stacked_dyn_scale)
stacked_dyn_scale_chk.setToolTip(
    "Stacked mode: ON = each spectrum normalised to 0–1 (equal heights).\n"
    "OFF = raw intensities, Y axis shared → heights are directly comparable.")
tools_row.addWidget(stacked_dyn_scale_chk)

sigma3_clip_chk = QtWidgets.QCheckBox("σ Clip")
sigma3_clip_chk.setChecked(_sigma3_clip)
sigma3_clip_chk.setToolTip(
    "Sigma noise clipping: suppress baseline noise below the\n"
    "N-sigma noise floor on the main plot (independent of Dynamic Scale).")
tools_row.addWidget(sigma3_clip_chk)

sigma3_slider = JumpSlider(QtCore.Qt.Orientation.Horizontal)
sigma3_slider.setMinimum(10)   # represents 1.0 σ
sigma3_slider.setMaximum(40)   # represents 4.0 σ
sigma3_slider.setValue(int(_sigma3_n_sigma * 10))
sigma3_slider.setFixedWidth(80)
sigma3_slider.setEnabled(_sigma3_clip)
sigma3_slider.setToolTip("Noise clipping threshold (1.0 – 4.0 σ)")

sigma3_sigma_label = QtWidgets.QLabel(f"{_sigma3_n_sigma:.1f}σ")
sigma3_sigma_label.setFixedWidth(30)
sigma3_sigma_label.setEnabled(_sigma3_clip)

tools_row.addWidget(sigma3_slider)
tools_row.addWidget(sigma3_sigma_label)

tools_row.addSpacing(16)
tools_row.addWidget(QtWidgets.QLabel("View All min m/z:"))
_view_mz_lower_spin = QtWidgets.QDoubleSpinBox()
_view_mz_lower_spin.setRange(0.0, 10000.0)
_view_mz_lower_spin.setDecimals(1)
_view_mz_lower_spin.setSingleStep(0.5)
_view_mz_lower_spin.setValue(settings.value("view_mz_lower", 10.9, type=float))
_view_mz_lower_spin.setFixedWidth(72)
_view_mz_lower_spin.setToolTip(
    "When using 'View All', ignore data below this m/z value.\n"
    "Prevents low-m/z noise from compressing the spectrum vertically.")
_view_mz_lower_spin.valueChanged.connect(
    lambda v: settings.setValue("view_mz_lower", v))
tools_row.addWidget(_view_mz_lower_spin)
tools_row.addStretch()
main_layout.addLayout(tools_row)

# ── Plot title bar ────────────────────────────────────────────────────────────
# Main title + auto-parsed filename tokens
# (date, mode, dt, sample) each editable.  Shown via View → Show Plot Title Bar.
_TITLE_HEADER_KEYS = ["date", "mode", "dt", "sample"]

_title_bar = QtWidgets.QWidget()
_title_bar.setMaximumHeight(28)
_tbl = QtWidgets.QHBoxLayout(_title_bar)
_tbl.setContentsMargins(4, 1, 4, 1)
_tbl.setSpacing(4)

_plot_title_edit = QtWidgets.QLineEdit()
_plot_title_edit.setPlaceholderText("Plot title…")
_plot_title_edit.setMinimumWidth(140)
_plot_title_edit.setToolTip("Main title displayed above the plot.")
_plot_title_edit.setText(settings.value("plot_title/text", ""))
_tbl.addWidget(_plot_title_edit)

_sep1 = QtWidgets.QFrame(); _sep1.setFrameShape(QtWidgets.QFrame.Shape.VLine)
_sep1.setStyleSheet("color: #ccc;"); _tbl.addWidget(_sep1)

_plot_header_edits: dict[str, QtWidgets.QLineEdit] = {}
_TITLE_KEY_WIDTHS = {"date": 88, "mode": 38, "dt": 38, "sample": 110}
for _hk in _TITLE_HEADER_KEYS:
    _hl = QtWidgets.QLabel(f"{_hk}:")
    _hl.setStyleSheet("color: gray; font-size: 10px;")
    _tbl.addWidget(_hl)
    _hed = QtWidgets.QLineEdit()
    _hed.setFixedWidth(_TITLE_KEY_WIDTHS[_hk])
    _hed.setPlaceholderText(_hk)
    _hed.setStyleSheet("font-size: 10px;")
    _hed.setToolTip(f"Auto-parsed from filename. Edit to override.")
    _tbl.addWidget(_hed)
    _plot_header_edits[_hk] = _hed

_sep2 = QtWidgets.QFrame(); _sep2.setFrameShape(QtWidgets.QFrame.Shape.VLine)
_sep2.setStyleSheet("color: #ccc;"); _tbl.addWidget(_sep2)

_tbl.addWidget(QtWidgets.QLabel("pt:"))
_plot_title_size = QtWidgets.QSpinBox()
_plot_title_size.setRange(6, 28)
_plot_title_size.setValue(settings.value("plot_title/size", 11, type=int))
_plot_title_size.setFixedWidth(46)
_tbl.addWidget(_plot_title_size)

_clear_title_btn = QtWidgets.QPushButton()
_clear_title_btn.setIcon(QtWidgets.QApplication.style().standardIcon(
    QtWidgets.QStyle.StandardPixmap.SP_TrashIcon))
_clear_title_btn.setFixedSize(22, 22)
_clear_title_btn.setFlat(True)
_clear_title_btn.setToolTip("Clear title text and all header fields")
_tbl.addWidget(_clear_title_btn)

_tbl.addStretch()

_hide_title_bar_btn = QtWidgets.QPushButton("✕")
_hide_title_bar_btn.setFixedSize(20, 20)
_hide_title_bar_btn.setFlat(True)
_hide_title_bar_btn.setToolTip("Hide the plot title bar\n(View → Show Plot Title Bar brings it back)")
_tbl.addWidget(_hide_title_bar_btn)
_title_bar.setVisible(settings.value("plot_title/show", True, type=bool))
main_layout.addWidget(_title_bar)

def _set_stacked_mode(val):
    global _stacked_mode
    _stacked_mode = val
    settings.setValue("stacked_mode", val)
    stacked_mode_action.blockSignals(True); stacked_mode_chk.blockSignals(True)
    stacked_mode_action.setChecked(val); stacked_mode_chk.setChecked(val)
    stacked_mode_action.blockSignals(False); stacked_mode_chk.blockSignals(False)
    try:
        plot.scene().sigMouseClicked.disconnect(plot_clicked)
    except Exception:
        pass
    if not val:
        plot.scene().sigMouseClicked.connect(plot_clicked)
    render_plot()

def _set_log_y(val):
    global _log_y
    _log_y = val
    settings.setValue("log_y", val)
    stacked_logy_action.blockSignals(True); stacked_logy_chk.blockSignals(True)
    stacked_logy_action.setChecked(val); stacked_logy_chk.setChecked(val)
    stacked_logy_action.blockSignals(False); stacked_logy_chk.blockSignals(False)
    render_plot()

def _set_stacked_dyn_scale(val):
    global _stacked_dyn_scale
    _stacked_dyn_scale = val
    settings.setValue("stacked_dyn_scale", val)
    stacked_dyn_scale_chk.blockSignals(True)
    stacked_dyn_scale_chk.setChecked(val)
    stacked_dyn_scale_chk.blockSignals(False)
    if _stacked_mode:
        render_plot()

stacked_mode_action.toggled.connect(_set_stacked_mode)
stacked_mode_chk.stateChanged.connect(lambda v: _set_stacked_mode(bool(v)))
stacked_dyn_scale_chk.stateChanged.connect(lambda v: _set_stacked_dyn_scale(bool(v)))

# disconnect old lambda on stacked_logy_action (it was set inline before)
stacked_logy_action.triggered.disconnect()
stacked_logy_action.toggled.connect(_set_log_y)
stacked_logy_chk.stateChanged.connect(lambda v: _set_log_y(bool(v)))

def _set_sigma3_clip(val):
    global _sigma3_clip
    _sigma3_clip = val
    settings.setValue("sigma3_clip", val)
    sigma3_clip_action.blockSignals(True); sigma3_clip_chk.blockSignals(True)
    sigma3_clip_action.setChecked(val);    sigma3_clip_chk.setChecked(val)
    sigma3_clip_action.blockSignals(False); sigma3_clip_chk.blockSignals(False)
    sigma3_slider.setEnabled(val)
    sigma3_sigma_label.setEnabled(val)
    render_plot()

def _set_sigma3_n_sigma(int_val):
    global _sigma3_n_sigma
    _sigma3_n_sigma = int_val / 10.0
    settings.setValue("sigma3_n_sigma", _sigma3_n_sigma)
    sigma3_sigma_label.setText(f"{_sigma3_n_sigma:.1f}σ")
    if _sigma3_clip:
        _sigma_render_timer.start()

sigma3_clip_action.toggled.connect(_set_sigma3_clip)
sigma3_clip_chk.stateChanged.connect(lambda v: _set_sigma3_clip(bool(v)))
sigma3_slider.valueChanged.connect(_set_sigma3_n_sigma)

# ─────────────────────────────────────────────
#  Plot widget
# ─────────────────────────────────────────────
plot_widget = pg.GraphicsLayoutWidget()
main_layout.addWidget(plot_widget)

status_bar = QtWidgets.QStatusBar()
status_bar.setSizeGripEnabled(False)
main_layout.addWidget(status_bar)

plot = plot_widget.addPlot()
plot.setLabel('bottom', 'm/z')
plot.setLabel('left',   'Intensity')

# ── Plot title logic (needs plot to exist) ────────────────────────────────────

def _apply_plot_title():
    """Rebuild the pyqtgraph plot title from the title bar widgets."""
    title  = _plot_title_edit.text().strip()
    pt     = _plot_title_size.value()
    pt_sub = max(6, pt - 2)
    settings.setValue("plot_title/text", title)
    settings.setValue("plot_title/size", pt)

    header_parts = [
        f"{k}={_plot_header_edits[k].text().strip()}"
        for k in _TITLE_HEADER_KEYS
        if _plot_header_edits[k].text().strip()
    ]
    subtitle = "  |  ".join(header_parts)

    if title and subtitle:
        html = (f"<span style='font-size:{pt}pt;font-weight:bold;'>{title}</span>"
                f"&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                f"<span style='font-size:{pt_sub}pt;color:gray;'>{subtitle}</span>")
    elif title:
        html = f"<span style='font-size:{pt}pt;font-weight:bold;'>{title}</span>"
    elif subtitle:
        html = f"<span style='font-size:{pt_sub}pt;color:gray;'>{subtitle}</span>"
    else:
        html = ""

    plot.setTitle(html or None)


def _auto_populate_title_vars(path: str):
    """Parse a spectrum file path and populate the header edit fields."""
    if not path:
        return
    name = os.path.splitext(os.path.basename(path))[0]
    vals: dict[str, str] = {}
    _parts = name.split("_")

    # date  YYYY-MM-DD
    _dm = re.match(r'(\d{4}-\d{2}-\d{2})', name)
    if _dm: vals["date"] = _dm.group(1)

    # mode
    if "_neg_" in name: vals["mode"] = "neg"
    elif "_pos_" in name: vals["mode"] = "pos"

    # dt
    _ddm = re.search(r'_dt(\d+)', name, re.IGNORECASE)
    dt_val = _ddm.group(1) if _ddm else ""
    if dt_val: vals["dt"] = dt_val

    # sample — from _parts[1] up to (but not including) the flow-rate token
    # (first part matching e.g. "0.22mlpmin").  Falls back to old logic if not found.
    if len(_parts) > 1:
        flow_idx = next(
            (j for j, p in enumerate(_parts)
             if re.match(r'\d+[\.,]\d+ml', p, re.IGNORECASE)),
            None)
        if flow_idx is not None and flow_idx > 1:
            sample_full = "_".join(_parts[1:flow_idx])
        else:
            sample_full = "_".join(_parts[1:max(2, len(_parts) - 4)])
        vals["sample"] = sample_full

    for k in _TITLE_HEADER_KEYS:
        ed = _plot_header_edits[k]
        ed.blockSignals(True)
        ed.setText(vals.get(k, ""))
        ed.blockSignals(False)

    # Main title: first token after the date, plus dt value
    main_title = _parts[1] if len(_parts) > 1 else ""
    if dt_val:
        main_title = f"{main_title}  -  dt{dt_val}"
    _plot_title_edit.blockSignals(True)
    _plot_title_edit.setText(main_title)
    _plot_title_edit.blockSignals(False)

    _apply_plot_title()


# Wire up all title-bar signals
_plot_title_edit.textChanged.connect(lambda _: _apply_plot_title())
_plot_title_size.valueChanged.connect(lambda _: _apply_plot_title())
for _hed in _plot_header_edits.values():
    _hed.textChanged.connect(lambda _: _apply_plot_title())

def _clear_plot_title():
    _plot_title_edit.blockSignals(True)
    _plot_title_edit.setText("")
    _plot_title_edit.blockSignals(False)
    for k in _TITLE_HEADER_KEYS:
        _plot_header_edits[k].blockSignals(True)
        _plot_header_edits[k].setText("")
        _plot_header_edits[k].blockSignals(False)
    _apply_plot_title()

_clear_title_btn.clicked.connect(_clear_plot_title)

# Auto-populate header fields when the file selection changes
combo.currentIndexChanged.connect(
    lambda: _auto_populate_title_vars(combo.currentData() or ""))

# View menu toggle wires up now that _title_bar exists
plot_title_action.toggled.connect(lambda v: (
    _title_bar.setVisible(v),
    settings.setValue("plot_title/show", v)))


class _MenuSpeechBubble(QtWidgets.QWidget):
    """A small speech bubble hanging from a menu-bar title, as if the menu
    itself were talking. Click it to dismiss; it also fades away on its own."""
    _ARROW = 9          # height of the pointer
    _RADIUS = 7

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 10 + self._ARROW, 12, 10)
        self._label = QtWidgets.QLabel()
        self._label.setWordWrap(True)
        self._label.setFixedWidth(330)
        self._label.setStyleSheet("color: #1a1a1a; background: transparent;")
        lay.addWidget(self._label)
        hint = QtWidgets.QLabel("Click on me to close me faster")
        hint.setStyleSheet("color: #8a7a3a; font-size: 9px; font-style: italic; background: transparent;")
        lay.addWidget(hint, 0, QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignBottom)
        self._arrow_x = 20
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def say(self, menu, text, msec=8000):
        """Show `text` in a bubble pointing at `menu`'s title in its menu bar."""
        self._label.setText(text)
        self.adjustSize()
        bar = menu.parentWidget() if isinstance(menu.parentWidget(), QtWidgets.QMenuBar) else menu_bar
        title = bar.actionGeometry(menu.menuAction())
        anchor = bar.mapTo(self.parentWidget(), title.bottomLeft() + QtCore.QPoint(title.width() // 2, 0))
        x = max(4, min(anchor.x() - 24, self.parentWidget().width() - self.width() - 4))
        self._arrow_x = anchor.x() - x
        self.move(x, anchor.y() + 2)
        self.raise_()
        self.show()
        self._timer.start(msec)

    def mousePressEvent(self, event):
        self.hide()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = QtCore.QRectF(self.rect()).adjusted(1, self._ARROW + 1, -1, -1)
        path = QtGui.QPainterPath()
        path.addRoundedRect(r, self._RADIUS, self._RADIUS)
        ax = float(self._arrow_x)
        tip = QtGui.QPainterPath()
        tip.moveTo(ax - self._ARROW, r.top() + 1)
        tip.lineTo(ax, 1)
        tip.lineTo(ax + self._ARROW, r.top() + 1)
        tip.closeSubpath()
        path = path.united(tip)
        p.setPen(QtGui.QPen(QtGui.QColor("#c9a227"), 1.2))
        p.setBrush(QtGui.QColor("#fff8d6"))
        p.drawPath(path)
        p.end()

_menu_bubble = _MenuSpeechBubble(main_win)

def _hide_title_bar_from_button():
    plot_title_action.setChecked(False)     # hides the bar and saves the choice
    _menu_bubble.say(view_menu,
        "The plot title bar is hidden. You can turn it back on (or off) "
        "here, with <b>View&nbsp;→ Show&nbsp;Plot&nbsp;Title&nbsp;Bar</b>.")

_hide_title_bar_btn.clicked.connect(_hide_title_bar_from_button)

# Apply saved title on startup
_apply_plot_title()
plot.setLogMode(x=False, y=_log_y)
_grid_on = settings.value("main_grid", True, type=bool)
plot.showGrid(x=_grid_on, y=_grid_on, alpha=0.3)
plot.vb.setMouseMode(pg.ViewBox.PanMode)

# ── Crosshair cursor over the plot area ──
class CrosshairCursorFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Enter:
            obj.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        elif event.type() == QtCore.QEvent.Type.Leave:
            obj.unsetCursor()
        return False

_crosshair_filter = CrosshairCursorFilter(plot_widget)
plot_widget.viewport().installEventFilter(_crosshair_filter)

hover_label = pg.LabelItem(justify='left')
hover_label.setParentItem(plot.vb)
hover_label.anchor(itemPos=(1,0), parentPos=(1,0), offset=(-10,10))
# Use a plain QLabel floating over the plot widget - avoids log-space coordinate issues
_mz_cursor_label = QtWidgets.QLabel(plot_widget)
def _mz_cursor_apply_font():
    pt = int(settings.value("mz_cursor_font_pt", 11))
    _mz_cursor_label.setStyleSheet(
        f"background: transparent; color: #3b9ddd; font-size: {pt}pt; font-weight: bold;")
_mz_cursor_apply_font()
_mz_cursor_label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
_mz_cursor_label.setVisible(False)
_mz_cursor_label.raise_()

v_line = pg.InfiniteLine(angle=90, movable=False,
    pen=pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
h_line = pg.InfiniteLine(angle=0,  movable=False,
    pen=pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
v_line.setVisible(False); h_line.setVisible(False)
plot.addItem(v_line, ignoreBounds=True)
plot.addItem(h_line, ignoreBounds=True)
splash_step(59)

# ── Minimap overlay ──────────────────────────────────────────────────────
class _MinimapOverlay(QtWidgets.QWidget):
    """
    A small thumbnail of the full spectrum drawn as a QWidget overlaid on
    plot_widget.  A red rectangle shows the currently visible viewport.
    Updates are throttled: the rect refreshes 2 s after the last pan/zoom.
    """
    _MARGIN   = 8          # distance from bottom-right corner
    _W, _H    = 200, 16    # minimap pixel size – slim horizontal bar

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._W, self._H)
        self._full_x  = None   # np array – full mz
        self._full_y  = None   # np array – full intensity (linear)
        self._view_rect = None  # QRectF in data coords
        self._delay_timer = QtCore.QTimer(self)
        self._delay_timer.setSingleShot(True)
        self._delay_timer.setInterval(1000)
        self._delay_timer.timeout.connect(self._refresh_rect)
        self._reposition(parent)

    def _reposition(self, parent=None):
        pw = parent or self.parent()
        if pw is None: return
        x = pw.width()  - self._W - self._MARGIN
        y = pw.height() - self._H - self._MARGIN
        self.move(x, y)

    def update_data(self, x_arr, y_arr):
        self._full_x = x_arr
        self._full_y = y_arr
        self._refresh_rect()

    def schedule_rect_update(self):
        self._delay_timer.start()

    def _refresh_rect(self):
        vr = plot.vb.viewRange()
        self._view_rect = QtCore.QRectF(
            vr[0][0], vr[1][0],
            vr[0][1] - vr[0][0],
            vr[1][1] - vr[1][0])
        self.update()

    def paintEvent(self, event):
        if self._full_x is None or len(self._full_x) == 0:
            return
        x = self._full_x
        x_min, x_max = float(x.min()), float(x.max())
        if x_max == x_min:
            return

        painter = QtGui.QPainter(self)
        pw, ph = float(self._W), float(self._H)
        mid_y  = ph / 2.0

        # Thin grey baseline
        painter.setPen(QtGui.QPen(QtGui.QColor(160, 160, 160, 180), 1))
        painter.drawLine(QtCore.QPointF(0, mid_y), QtCore.QPointF(pw, mid_y))

        if self._view_rect is not None:
            rx0 = (self._view_rect.left()  - x_min) / (x_max - x_min) * pw
            rx1 = (self._view_rect.right() - x_min) / (x_max - x_min) * pw
            rx0 = max(0.0, min(rx0, pw))
            rx1 = max(0.0, min(rx1, pw))
            # Filled zone on the baseline showing the current viewport span
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(255, 80, 80, 180))
            painter.drawRect(QtCore.QRectF(rx0, mid_y - 3, rx1 - rx0, 6))
            # Thin tick marks at the edges
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 80, 80, 230), 1))
            painter.drawLine(QtCore.QPointF(rx0, 0), QtCore.QPointF(rx0, ph))
            painter.drawLine(QtCore.QPointF(rx1, 0), QtCore.QPointF(rx1, ph))

        painter.end()

_minimap = _MinimapOverlay(plot_widget)
_minimap.raise_()
_minimap.show()

def _minimap_reposition():
    _minimap._reposition()
    _minimap.raise_()

_orig_plot_widget_resize = plot_widget.resizeEvent
def _pw_resize_event(event):
    _orig_plot_widget_resize(event)
    _minimap_reposition()
plot_widget.resizeEvent = _pw_resize_event

legend = plot.addLegend(offset=(10,10))
legend.setVisible(False)           # spectrum-names legend: not shown (option removed)

# ── Zoom history tracking ──
def _on_view_range_changed():
    """Record every manual zoom/pan so 'Go to last zoom' can restore it."""
    global _in_zoom_restore
    if _in_zoom_restore:
        return
    if lock_axes_action.isChecked():
        return
    xr = plot.vb.viewRange()[0]
    yr = plot.vb.viewRange()[1]
    # Only push if meaningfully different from the last saved range
    if _zoom_history:
        last_x, last_y = _zoom_history[-1]
        if abs(xr[0]-last_x[0]) < 1e-6 and abs(xr[1]-last_x[1]) < 1e-6:
            return
    _zoom_history.append((list(xr), list(yr)))
    # Keep history bounded
    if len(_zoom_history) > 50:
        _zoom_history.pop(0)

plot.vb.sigRangeChanged.connect(lambda vb, ranges: _on_view_range_changed())

# ─────────────────────────────────────────────
#  State
# ─────────────────────────────────────────────
# highlighted_ranges entries: (mz_min, mz_max, label, peak_mz, peak_int)
highlighted_ranges  = []
_label_text_items   = []
_peak_hl_items_by_row: dict = {}   # id(row_data) -> [PlotDataItem, ...] for fast per-row update
_hl_row_id          = [None]       # set before highlight_peaks call to tag items to a row
_peak_symbol_scatter_items = []   # ScatterPlotItems for "symbols on peaks" overlay
_auto_peak_scatter  = None
df           = None
df_raw       = None
recal_factor = 1.0
current_display = "bright"
locked_x_range  = None
locked_y_range  = None

# Zoom history for "Go to last zoom"
_zoom_history   = []   # list of (x_range, y_range) tuples
_in_zoom_restore = False  # guard against history re-entry

# Manual-recal overlay scatter items (inverted triangles shown during preselection)
_manual_recal_scatter_items = []

_cluster_scatter_items = []   # ScatterPlotItems drawn by cluster detection window

# ─────────────────────────────────────────────
#  Peak area measurement state
# ─────────────────────────────────────────────
_area_mode_active   = False   # True when the user is in area-pick mode
_area_line_lo       = None    # pg.InfiniteLine for lower bound
_area_line_hi       = None    # pg.InfiniteLine for upper bound
_area_pick_step     = 0       # 0 = waiting for first click, 1 = waiting for second
_area_result_label  = None    # pg.LabelItem showing the computed area
_AREA_BASELINE_FLOOR = 0.007  # intensity floor used by airPLS (the "true zero")

def _clear_cluster_scatter():
    global _cluster_scatter_items
    for item in _cluster_scatter_items:
        try:
            plot.removeItem(item)
        except Exception:
            pass
    _cluster_scatter_items.clear()

_CLUSTER_SYMBOLS = ['o', 's', 't', 'd', 'star', 'p', 'h', 't2', 't3', 'x']

# ─────────────────────────────────────────────
#  Peak area measurement
# ─────────────────────────────────────────────

def _area_clear_markers():
    """Remove both InfiniteLines and the result label from the plot."""
    global _area_line_lo, _area_line_hi, _area_result_label
    for line in (_area_line_lo, _area_line_hi):
        if line is not None:
            try:
                plot.removeItem(line)
            except Exception:
                pass
    _area_line_lo = None
    _area_line_hi = None
    if _area_result_label is not None:
        try:
            plot.vb.removeItem(_area_result_label)
        except Exception:
            pass
        _area_result_label = None


def _area_compute(mz_lo, mz_hi):
    """
    Integrate the active spectrum between mz_lo and mz_hi.
    Uses np.trapezoid on the raw intensity values, then subtracts the
    baseline floor contribution (_AREA_BASELINE_FLOOR * Δmz) so that
    a perfectly flat baseline contributes zero area.

    Returns (raw_area, corrected_area, n_points) or None if no data.
    """
    data = df  # the currently displayed (possibly baseline-corrected) spectrum
    if data is None or len(data) == 0:
        return None

    mask = (data['mz'] >= mz_lo) & (data['mz'] <= mz_hi)
    sub  = data[mask]
    if len(sub) < 2:
        return None

    mz_arr  = sub['mz'].values
    int_arr = sub['intensity'].values
    delta_mz = mz_arr[-1] - mz_arr[0]

    raw_area = float(_trapezoid(int_arr, mz_arr))

    # Noise floor: 3-sigma clip from the m/z >= 10.9 region (matches PeakAreaWindow).
    _MZ_THR       = 10.9
    _full_mz      = df['mz'].values
    _full_int     = df['intensity'].values
    _thr_mask     = _full_mz >= _MZ_THR
    noise_floor   = (
        _estimate_noise_floor(_full_int[_thr_mask], n_sigma=3.0)
        if _thr_mask.sum() >= 10 else _DYN_CLIP_FLOOR
    )
    floor_area     = noise_floor * delta_mz
    corrected_area = max(0.0, raw_area - floor_area)

    return raw_area, corrected_area, len(sub), noise_floor


def _area_show_result(mz_lo, mz_hi):
    """Compute area and display it as a label anchored to the top-left of the plot."""
    global _area_result_label

    result = _area_compute(mz_lo, mz_hi)

    # Remove old label
    if _area_result_label is not None:
        try:
            plot.vb.removeItem(_area_result_label)
        except Exception:
            pass
        _area_result_label = None

    if result is None:
        QtWidgets.QMessageBox.information(
            main_win, "Peak Area",
            f"No data points found between m/z {mz_lo:.4f} and {mz_hi:.4f}.")
        return

    raw_area, corrected_area, n_pts, noise_floor = result
    # Compute total corrected area for normalization (above 10.9 m/z threshold)
    full_mz  = df['mz'].values
    full_int = df['intensity'].values
    thr_mask = full_mz >= 10.9
    if thr_mask.sum() >= 2:
        full_mz_thr  = full_mz[thr_mask]
        full_int_thr = full_int[thr_mask]
        full_noise_floor = _estimate_noise_floor(full_int_thr, n_sigma=3.0)
        full_raw   = float(_trapezoid(full_int_thr, full_mz_thr))
        full_floor = full_noise_floor * (full_mz_thr[-1] - full_mz_thr[0])
        normalized_total_area_by_noise_floor = max(0.0, full_raw - full_floor)
    else:
        normalized_total_area_by_noise_floor = None
    if normalized_total_area_by_noise_floor and normalized_total_area_by_noise_floor > 0:
        norm_val = corrected_area / normalized_total_area_by_noise_floor
        norm_str = f"\n  Normalized by total corrected area: {norm_val:.6f}"
    else:
        norm_str = "\n  Normalized by total corrected area: N/A"
    text = (
        f"Area  [{mz_lo:.2f} – {mz_hi:.2f}]\n"
        f"  Raw:       {raw_area:.6f}\n"
        f"  Corrected to noise: {corrected_area:.6f}"
        f"{norm_str}\n"
        f"  ({n_pts} pts, 3σ floor={noise_floor:.4f})"
    )

    label_color = 'w' if current_display == 'dark' else 'k'
    _area_result_label = pg.LabelItem(text, color=label_color, size='9pt')
    _area_result_label.setParentItem(plot.vb)
    # Anchor: top-left corner of the viewport, with a small offset
    _area_result_label.anchor(itemPos=(0, 0), parentPos=(0, 0), offset=(8, 8))


def _area_on_plot_click(event):
    """
    Mouse-click handler injected while area mode is active.
    First click  → sets the lower-bound InfiniteLine.
    Second click → sets the upper-bound InfiniteLine and computes the area.
    Subsequent clicks reset and start over.
    """
    global _area_pick_step, _area_line_lo, _area_line_hi

    if not _area_mode_active:
        return
    if event.button() != QtCore.Qt.MouseButton.LeftButton:
        return

    pos = event.scenePos()
    if not plot.sceneBoundingRect().contains(pos):
        return

    # Prevent the normal pick-peaks handler from also firing
    event.accept()

    mz = plot.vb.mapSceneToView(pos).x()

    if _area_pick_step == 0:
        # ── First click: place the low-bound line ──
        _area_clear_markers()           # reset if there were leftover markers

        pen_lo = pg.mkPen('#3b9ddd', width=1, style=QtCore.Qt.PenStyle.DashLine)
        _area_line_lo = pg.InfiniteLine(pos=mz, angle=90, movable=False, pen=pen_lo,
                                        label=f'{mz:.2f}',
                                        labelOpts={'color': '#3b9ddd', 'position': 0.92})
        plot.addItem(_area_line_lo, ignoreBounds=True)
        _area_pick_step = 1

    else:
        # ── Second click: place the high-bound line and compute ──
        mz_lo_val = _area_line_lo.value()
        mz_hi_val = mz

        # Swap so lo < hi regardless of click order
        if mz_hi_val < mz_lo_val:
            mz_lo_val, mz_hi_val = mz_hi_val, mz_lo_val

        # Re-place the lo line in case we swapped
        plot.removeItem(_area_line_lo)
        pen_lo = pg.mkPen('#3b9ddd', width=1, style=QtCore.Qt.PenStyle.DashLine)
        _area_line_lo = pg.InfiniteLine(pos=mz_lo_val, angle=90, movable=False, pen=pen_lo,
                                        label=f'{mz_lo_val:.2f}',
                                        labelOpts={'color': '#3b9ddd', 'position': 0.92})
        plot.addItem(_area_line_lo, ignoreBounds=True)

        pen_hi = pg.mkPen('#e05c5c', width=1, style=QtCore.Qt.PenStyle.DashLine)
        _area_line_hi = pg.InfiniteLine(pos=mz_hi_val, angle=90, movable=False, pen=pen_hi,
                                        label=f'{mz_hi_val:.2f}',
                                        labelOpts={'color': '#e05c5c', 'position': 0.85})
        plot.addItem(_area_line_hi, ignoreBounds=True)

        _area_show_result(mz_lo_val, mz_hi_val)
        _area_pick_step = 0   # ready for a fresh pair of clicks


def _area_mode_set(active):
    """Enable or disable area-pick mode. Wires/unwires the click handler."""
    global _area_mode_active, _area_pick_step

    _area_mode_active = active
    _area_pick_step   = 0

    if active:
        # Show a brief tooltip-style hint in the hover label
        hover_label.setText(
            "<span style='color:#3b9ddd'>Area mode: click twice to define a range</span>")
        plot.scene().sigMouseClicked.connect(_area_on_plot_click)
    else:
        try:
            plot.scene().sigMouseClicked.disconnect(_area_on_plot_click)
        except Exception:
            pass
        _area_clear_markers()
        hover_label.setText("")

    # Keep menu action in sync (in case this was called programmatically)
    if area_mode_action.isChecked() != active:
        area_mode_action.blockSignals(True)
        area_mode_action.setChecked(active)
        area_mode_action.blockSignals(False)



def highlight_alpha(): return highlight_slider.value()
def overlay_alpha(): return int(overlay_opacity_slider.value() / 100.0 * 255)

# ─────────────────────────────────────────────
#  OPT: Persistent curve references
# ─────────────────────────────────────────────
_main_curve    = None           # PlotDataItem for the primary spectrum
_overlay_curves = {}            # id(ov_data) -> PlotDataItem
_stacked_sub_plots      = []   # list of PlotItem added in stacked mode
_stacked_mouse_handlers = []   # mouse-move callbacks for stacked sub-plots
_stacked_build_gen      = 0    # incremented each build; lets stale nudge timers self-cancel

def _invalidate_overlay_curves():
    """Call when overlays are added/removed so stale refs are dropped."""
    global _overlay_curves
    _overlay_curves = {}

# ─────────────────────────────────────────────
#  OPT: Normalisation cache
# ─────────────────────────────────────────────
_norm_cache: dict = {}

def _clear_norm_cache():
    _norm_cache.clear()


_DYN_CLIP_FLOOR = 2.5e-3  # fallback hard floor (used only when sigma-clipping can't run)

def _estimate_noise_floor(intensity_arr, n_sigma=3.0):
    """
    Estimate the noise baseline level via sigma-clipping.

    Algorithm:
      1. Work only on the bottom 50 % of positive values - these are the
         baseline / noise region, not real peaks.
      2. Do one pass of 3-sigma rejection on that region to remove any
         stray low-intensity peak tails that slipped in.
      3. Return  noise_median + n_sigma * noise_std.

    n_sigma=1  →  gentle display clipping (suppresses only flat baseline)
    n_sigma=2  →  moderate (previous behaviour)
    n_sigma=3  →  conservative area floor (classical "3-sigma detection limit")

    Falls back to _DYN_CLIP_FLOOR if there are too few points.
    """
    try:
        data = intensity_arr[intensity_arr > 0]
        if len(data) < 10:
            return _DYN_CLIP_FLOOR
        # Bottom 50 % = noise / baseline region
        threshold = np.percentile(data, 50)
        noise = data[data <= threshold]
        if len(noise) < 5:
            return _DYN_CLIP_FLOOR
        # Single-pass 3σ rejection to clean the noise region itself
        med = np.median(noise)
        std = np.std(noise)
        noise = noise[np.abs(noise - med) < 3.0 * std]
        if len(noise) < 3:
            return _DYN_CLIP_FLOOR
        floor = float(np.median(noise) + n_sigma * np.std(noise))
        # Safety cap: never clip more than 5 % of full scale
        return min(floor, 0.05)
    except Exception:
        return _DYN_CLIP_FLOOR


def normalise_cached(data_df, zero_floor=False):
    key = (id(data_df), zero_floor)
    if key not in _norm_cache:
        _norm_cache[key] = normalise(data_df, zero_floor)
    return _norm_cache[key]

# ─────────────────────────────────────────────
#  OPT: Auto-peak detection cache
# ─────────────────────────────────────────────
_auto_peaks_cache: dict = {}

def _clear_auto_peaks_cache():
    _auto_peaks_cache.clear()

def _get_auto_peaks(data_df, threshold_value, mode="pct"):
    """
    mode='pct'  → threshold_value is % of max intensity (0-100)
    mode='snr'  → threshold_value is a minimum signal-to-noise ratio
    """
    key = (id(data_df), threshold_value, mode)
    if key not in _auto_peaks_cache:
        from scipy.signal import find_peaks as _fp
        int_arr = data_df['intensity'].values
        mz_arr  = data_df['mz'].values
        mx = int_arr.max()
        if mx == 0:
            _auto_peaks_cache[key] = (np.array([]), np.array([]))
        else:
            if mode == "snr":
                # Pre-filter by a low absolute floor so find_peaks doesn't
                # waste time on pure-noise regions, then apply real SNR filter.
                rough_threshold = 0.001 * mx
                indices, _ = _fp(int_arr, distance=5, height=rough_threshold)
                if len(indices) == 0:
                    _auto_peaks_cache[key] = (np.array([]), np.array([]))
                else:
                    LOCAL = 5.0   # ±5 Da window for local noise
                    kept_mz  = []
                    kept_int = []
                    for i in indices:
                        mz = mz_arr[i]; intensity = int_arr[i]
                        if mz < _AR_MINIMUM_MASS:
                            continue
                        mask = (mz_arr >= mz - LOCAL) & (mz_arr <= mz + LOCAL)
                        noise = np.percentile(int_arr[mask], 25) if mask.sum() > 4 else 0.0
                        if noise <= 0:
                            noise = 1e-9
                        if intensity / noise >= threshold_value:
                            kept_mz.append(mz)
                            kept_int.append(intensity)
                    _auto_peaks_cache[key] = (np.array(kept_mz), np.array(kept_int))
            else:  # mode == "pct"
                threshold = (threshold_value / 100.0) * mx
                indices, _ = _fp(int_arr, distance=5, height=threshold)
                if len(indices) == 0:
                    _auto_peaks_cache[key] = (np.array([]), np.array([]))
                else:
                    mask = mz_arr[indices] >= _AR_MINIMUM_MASS
                    _auto_peaks_cache[key] = (mz_arr[indices][mask], int_arr[indices][mask])
    return _auto_peaks_cache[key]




def find_peak_bounds(mz_arr, int_arr, target_mz, *, noise_floor, coarse_tol, grace=3):
    """Drop-in wrapper around the original find_peak_bounds that tolerates up to
    `grace` consecutive rising points mid-descent before stopping the boundary walk.
    This prevents small noise blips from cutting a peak short prematurely.
    grace=0 reproduces the original behaviour exactly."""
    bounds = _find_peak_bounds_orig(mz_arr, int_arr, target_mz,
                                    noise_floor=noise_floor, coarse_tol=coarse_tol)
    if bounds is None or grace == 0:
        return bounds

    mz_lo, mz_hi, peak_mz, peak_int = bounds
    apex_idx = int(np.argmin(np.abs(mz_arr - peak_mz)))

    # ── Walk LEFT with grace ──────────────────────────────────────────────
    lo_idx = int(np.searchsorted(mz_arr, mz_lo))
    consecutive_rises = 0
    best_lo = lo_idx
    for i in range(lo_idx - 1, -1, -1):
        if int_arr[i] < noise_floor:
            break
        if int_arr[i] > int_arr[i + 1]:
            consecutive_rises += 1
            if consecutive_rises > grace:
                break
        else:
            consecutive_rises = 0
            best_lo = i
    mz_lo = float(mz_arr[best_lo])

    # ── Walk RIGHT with grace ─────────────────────────────────────────────
    hi_idx = int(np.searchsorted(mz_arr, mz_hi, side='right')) - 1
    consecutive_rises = 0
    best_hi = hi_idx
    for i in range(hi_idx + 1, len(mz_arr)):
        if int_arr[i] < noise_floor:
            break
        if int_arr[i] > int_arr[i - 1]:
            consecutive_rises += 1
            if consecutive_rises > grace:
                break
        else:
            consecutive_rises = 0
            best_hi = i
    mz_hi = float(mz_arr[best_hi])

    return mz_lo, mz_hi, peak_mz, peak_int


# ─────────────────────────────────────────────
#  OPT: Highlight-range cache
# ─────────────────────────────────────────────
_highlight_cache: dict = {}

def _clear_highlight_cache():
    _highlight_cache.clear()

def _get_highlight_geometry(data_df, peaks_flat):
    key = (id(data_df), tuple(peaks_flat))
    if key in _highlight_cache:
        return _highlight_cache[key]

    mz_full  = data_df["mz"].values
    int_full = data_df["intensity"].values

    # ── Use zero-floor normalised intensities for bound finding ──────────────
    # find_peak_bounds / peak_widths(rel_height=1.0) REQUIRES a baseline-clipped
    # signal (its own docstring: "int_arr already clipped to 0").
    # On raw data the surrounding valley sits at the (non-zero) baseline offset,
    # so scipy measures all the way out to where the signal drops back to that
    # high baseline — producing extremely wide bounds in linear-scale mode.
    # After zero-floor normalisation the minimum is ≈ 0, valley = 0, and the
    # measured width tightly follows the visible peak body in BOTH log and
    # linear display modes.
    # The raw int_full is still used for the curve y-values (so the drawn
    # highlight follows the actual displayed spectrum).
    norm_int = normalise_cached(data_df, zero_floor=True)["intensity"].values

    _thr = mz_full >= 10.9
    noise_floor = (
        _estimate_noise_floor(norm_int[_thr], n_sigma=3.0)
        if _thr.sum() >= 10 else _DYN_CLIP_FLOOR
    )

    results = []
    for p in peaks_flat:
        shifted_p = p + peak_shift(p)
        coarse    = get_tolerance(shifted_p)

        bounds = find_peak_bounds(mz_full, norm_int, shifted_p,
                                  noise_floor=noise_floor, coarse_tol=coarse, grace=3)

        if bounds is not None:
            mz_lo, mz_hi, _norm_peak_mz, _norm_peak_int = bounds
            # Keep the left boundary symmetric with the right: a left-side
            # shoulder can push mz_lo further from the apex than mz_hi is,
            # making the highlight look lop-sided and swallow adjacent peaks.
            mz_lo = max(mz_lo, _norm_peak_mz - (mz_hi - _norm_peak_mz))
            mask    = (mz_full >= mz_lo) & (mz_full <= mz_hi)
            mz_arr  = mz_full[mask]
            int_arr = int_full[mask]          # raw values — follow displayed curve
            if len(mz_arr) < 2:
                continue
            # Re-derive peak position from the raw slice (same m/z, raw intensity)
            _li     = int_arr.argmax()
            peak_mz  = float(mz_arr[_li])
            peak_int = float(int_arr[_li])
        else:
            # Fallback: narrow window + ±10-point slice
            backward  = 0.2
            idx_bool  = ((mz_full >= shifted_p - backward) &
                         (mz_full <= shifted_p + coarse))
            if not idx_bool.any():
                continue
            from scipy.signal import find_peaks as _fp
            cand_mz   = mz_full[idx_bool]
            cand_norm = norm_int[idx_bool]    # normalised — for peak detection
            cand_raw  = int_full[idx_bool]    # raw — for curve y-values

            local_peaks, _ = _fp(cand_norm,
                                  height=noise_floor, prominence=noise_floor)
            best = (local_peaks[cand_norm[local_peaks].argmax()]
                    if len(local_peaks) > 0 else int(cand_norm.argmax()))

            peak_mz  = float(cand_mz[best])
            peak_int = float(cand_raw[best])
            n        = 10
            start    = max(0, best - n)
            end      = min(len(cand_mz), best + n + 1)
            mz_arr   = cand_mz[start:end]
            int_arr  = cand_raw[start:end]
            mz_lo    = float(mz_arr.min())
            mz_hi    = float(mz_arr.max())

        results.append((mz_arr, int_arr, mz_lo, mz_hi, peak_mz, peak_int))

    _highlight_cache[key] = results
    return results

# ─────────────────────────────────────────────
#  OPT: Render debounce timer (80 ms)
# ─────────────────────────────────────────────
_render_timer = QtCore.QTimer()
_render_timer.setSingleShot(True)
_render_timer.setInterval(80)

# Debounce timer for the sigma-clip slider.
# The label refreshes on every tick; the plot render waits until dragging stops.
_sigma_render_timer = QtCore.QTimer()
_sigma_render_timer.setSingleShot(True)
_sigma_render_timer.setInterval(400)
_sigma_render_timer.timeout.connect(lambda: render_plot())

# Debounce timer for peak text inputs (values / labels).
# Fires after the user pauses typing rather than on every keystroke.
_peak_text_timer = QtCore.QTimer()
_peak_text_timer.setSingleShot(True)
_peak_text_timer.setInterval(300)
_peak_text_timer.timeout.connect(lambda: (
    _fast_update_one_row(_last_edited_row[0]),
    _auto_sync_legend_entries(),
    _rebuild_peak_legend_on_plot()))

def render_plot():
    """Schedule a render; coalesces rapid-fire calls into one."""
    _render_timer.start()
    # Any visual change: refresh the peak-list session snapshot soon after
    # (defined further down; not yet during startup).
    timer = globals().get("_peak_session_timer")
    if timer is not None:
        timer.start()

# ─────────────────────────────────────────────
#  Worker thread
# ─────────────────────────────────────────────
class _Worker(QtCore.QThread):
    finished = QtCore.Signal(object)
    error    = QtCore.Signal(str)

    def __init__(self, func, *args):
        super().__init__()
        self._func = func
        self._args = args
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            result = self._func(*self._args, stop_flag=self)
            if not self._stop_requested:
                self.finished.emit(result)
        except InterruptedError:
            pass
        except Exception as e:
            self.error.emit(str(e))


def _make_progress_dialog(title, message, cancellable=True):
    dlg = QtWidgets.QProgressDialog(message, "Stop" if cancellable else None, 0, 0, main_win)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(QtCore.Qt.WindowModality.NonModal)
    dlg.setMinimumDuration(0)
    dlg.setRange(0, 0); dlg.setValue(0)
    dlg.setMinimumWidth(260)
    dlg.setWindowFlags(
        QtCore.Qt.WindowType.Tool |
        QtCore.Qt.WindowType.FramelessWindowHint |
        QtCore.Qt.WindowType.WindowStaysOnTopHint)
    mw_geo  = main_win.geometry(); dlg_geo = dlg.sizeHint()
    dlg.move(mw_geo.right() - dlg_geo.width() - 12,
             mw_geo.bottom() - dlg_geo.height() - 12)
    dlg.show(); app.processEvents()
    return dlg

_active_worker = None

# ─────────────────────────────────────────────
#  Baseline algorithms
# ─────────────────────────────────────────────
def _whittaker_smooth(x, w, lambda_, differences=1):
    m = len(x)
    E = eye(m, format='csc'); D = E[1:] - E[:-1]
    W = diags(w, 0, shape=(m, m))
    A = csc_matrix(W + (lambda_ * D.T * D))
    B = csc_matrix(W * np.matrix(x).T)
    return np.array(spsolve(A, B))

# airPLS baseline correction adapted from LILBID_GUI by M. Umair
# https://github.com/mumair5393/LILBID_GUI/blob/main/baseline_correction.py
def airpls_baseline(intensity, lambda_=100, porder=1, itermax=20, stop_flag=None):
    x = np.array(intensity, dtype=float); m = x.shape[0]; w = np.ones(m)
    for i in range(1, itermax + 1):
        if stop_flag is not None and stop_flag._stop_requested:
            raise InterruptedError("airPLS cancelled")
        z = _whittaker_smooth(x, w, lambda_, porder)
        d = x - z; dssn = np.abs(d[d < 0].sum())
        if dssn < 0.001 * np.abs(x).sum(): break
        w[d >= 0] = 0
        w[d < 0]  = np.exp(i * np.abs(d[d < 0]) / dssn)
        w[0] = np.exp(i * d[d < 0].max() / dssn); w[-1] = w[0]
    return z

def apply_baseline(data_df, stop_flag=None):
    y = data_df['intensity'].values
    if snip_action.isChecked():
        baseline = snip_baseline(y, stop_flag=stop_flag)
        corrected = np.clip(y - baseline, 0, None)
    else:
        baseline = airpls_baseline(y, stop_flag=stop_flag)
        corrected = y - baseline
        corrected[corrected < 0] = 0.000001
        corrected = corrected + 0.007
    out = data_df.copy(); out['intensity'] = corrected
    return out

def snip_baseline(intensity, max_hwidth=40, smooth_iters=3, stop_flag=None):
    y = np.array(intensity, dtype=float); y = np.clip(y, 0, None)
    p = np.sqrt(np.sqrt(y + 1)); n = len(p)
    for hwidth in range(1, max_hwidth + 1):
        if stop_flag is not None and stop_flag._stop_requested:
            raise InterruptedError("SNIP cancelled")
        left  = np.roll(p,  hwidth); left[:hwidth]   = p[:hwidth]
        right = np.roll(p, -hwidth); right[-hwidth:]  = p[-hwidth:]
        p = np.minimum(p, (left + right) / 2.0)
    for _ in range(smooth_iters):
        left  = np.roll(p,  1); left[0]  = p[0]
        right = np.roll(p, -1); right[-1] = p[-1]
        p = (left + p + right) / 3.0
    return np.clip((p ** 2) ** 2 - 1, 0, None)

# ─────────────────────────────────────────────
#  Save processed spectrum
# ─────────────────────────────────────────────
def save_spectrum_df(data_df, path, src_path=None, process_tag=None, extra_headers=None):
    """Write spectrum to path, prepending original headers if src_path given.

    extra_headers: optional list of '#key=value' strings written after
    process_tag and before the original source-file headers.
    """
    with open(path, 'w', encoding='utf-8') as fh:
        if src_path and os.path.isfile(str(src_path)):
            if process_tag:
                fh.write(f"#processed={process_tag}\n")
            if extra_headers:
                for h in extra_headers:
                    fh.write(h + "\n")
            for hline in read_spectrum_headers(str(src_path)):
                fh.write(hline + "\n")
            fh.write("##########\n")
        for row in data_df.itertuples(index=False):
            fh.write(f"{row.mz}\t{row.intensity}\n")

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────
def normalise(data_df, zero_floor=False):
    out = data_df.copy()
    if zero_floor:
        mn = out['intensity'].min()
        out['intensity'] = out['intensity'] - mn + 1e-6
    mx = out['intensity'].max()
    if mx == 0: return out
    out['intensity'] = out['intensity'] / mx
    return out

def compute_subtraction(df_a, df_b, dynamic=False):
    if df_a is None or df_b is None: return None
    a = df_a.copy(); b = df_b.copy()
    if dynamic:
        mx_a = a['intensity'].max(); mx_b = b['intensity'].max()
        if mx_a > 0: a['intensity'] /= mx_a
        if mx_b > 0: b['intensity'] /= mx_b
    b_interp = np.interp(a['mz'].values, b['mz'].values, b['intensity'].values, left=0, right=0)
    result = a.copy(); result['intensity'] = a['intensity'].values - b_interp
    return result


def apply_alpha(color_str, alpha):
    c = pg.mkColor(color_str); factor = alpha / 255.0
    if current_display == "dark":
        r = int(c.red() * factor); g = int(c.green() * factor); b = int(c.blue() * factor)
    else:
        r = int(c.red()   + (255-c.red())   * (1.0-factor))
        g = int(c.green() + (255-c.green()) * (1.0-factor))
        b = int(c.blue()  + (255-c.blue())  * (1.0-factor))
    return QtGui.QColor(r, g, b, 255)

def overlay_pen(color_str, alpha):
    c = pg.mkColor(color_str)
    c.setAlpha(alpha)
    return pg.mkPen(c, width=1)

# def get_tolerance(mz): return min(0.1 + 0.004 * mz, 0.8)
def peak_shift(mz): return 0.0002 * mz
def spectrum_display_name(path): return parse_lilbid_name(path) if path else "-"

# ─────────────────────────────────────────────
#  Manual recalibration - peak pre-selection helper
#  Searches ±1.5 Da around each nominal m/z, returns
#  the local intensity-maximum as the detected peak.
# ─────────────────────────────────────────────
def _detect_peak_near(data_df, nominal_mz, window=1.5):
    """
    Find the highest-intensity point within [nominal_mz - window, nominal_mz + window].
    Returns (detected_mz, detected_intensity) or None if no data in window.
    """
    mask = ((data_df['mz'] >= max(nominal_mz - window, _AR_MINIMUM_MASS)) & 
            (data_df['mz'] <= nominal_mz + window))
    sub = data_df[mask]
    if sub.empty:
        return None
    idx = sub['intensity'].idxmax()
    return float(sub.loc[idx, 'mz']), float(sub.loc[idx, 'intensity'])


# ─────────────────────────────────────────────
#  Manual recalibration - TOF polynomial application
#  Reuses the exact same quadratic-fit logic as auto-recal.
# ─────────────────────────────────────────────
def _apply_manual_recal_pairs(data_df, pairs):
    """
    pairs: list of (observed_mz, reference_mz)
    Fits a quadratic TOF polynomial (same as auto-recal) and returns
    (corrected_df, fitparams) where fitparams is the np.polyfit result
    or None when a linear scale fallback was used.
    """
    if not pairs:
        return data_df.copy(), None

    data_np = data_df[['mz', 'intensity']].to_numpy().copy()

    if len(pairs) < 2:
        # Single-point fallback: uniform scale
        obs, ref = pairs[0]
        if obs == 0:
            return data_df.copy(), None
        factor = ref / obs
        out = data_df.copy()
        out['mz'] = out['mz'] * factor
        return out, None

    # Build TOF-time approximations for observed peaks
    # t_j = index such that data_np[t_j, 0] > obs_mz   (same as auto-recal)
    cal_tof_rows = []
    for obs_mz, ref_mz in pairs:
        for t in range(data_np.shape[0]):
            if data_np[t, 0] > obs_mz:
                t_val = (t - 1) * _AR_TIMESTEP
                cal_tof_rows.append((t_val, ref_mz))
                break

    if len(cal_tof_rows) < 2:
        # Still not enough - fall back to first pair
        obs, ref = pairs[0]
        factor = ref / obs if obs != 0 else 1.0
        out = data_df.copy(); out['mz'] = out['mz'] * factor
        return out, None

    t_arr   = np.array([r[0] for r in cal_tof_rows])
    ref_arr = np.array([r[1] for r in cal_tof_rows])
    deg     = min(2, len(cal_tof_rows) - 1)
    fitparams = np.polyfit(t_arr, ref_arr, deg)

    recal_np = data_np.copy()
    for j in range(len(recal_np)):
        t_j = j * _AR_TIMESTEP
        recal_np[j, 0] = np.polyval(fitparams, t_j)

    return pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]}), fitparams


# ═════════════════════════════════════════════════════════════════════════════
#  MANUAL RECALIBRATION WINDOW
# ═════════════════════════════════════════════════════════════════════════════

# ── Batch state (module-level) ────────────────────────────────────────────
_batch_manual_state = {
    "active":       False,
    "files":        [],       # list of absolute paths to process
    "output_folder": "",
    "done_count":   0,
    "total":        0,
}

def _save_batch_manual_state():
    settings.setValue("batch_manual_recal", json.dumps(_batch_manual_state))

def _load_batch_manual_state():
    raw = settings.value("batch_manual_recal", "")
    if not raw:
        return
    try:
        saved = json.loads(raw)
        _batch_manual_state.update(saved)
    except Exception:
        pass

_load_batch_manual_state()



def parse_peaks_text(text):
    if not text.strip(): return []
    try:
        return [float(v.strip()) for v in text.split(",") if v.strip()]
    except ValueError:
        return []


from droplet_pkg.ui.mixins import StayOnTopMixin  # noqa: E402


class ManualRecalWindow(QtWidgets.QWidget, StayOnTopMixin):
    """
    Non-modal window that shows peak rows with toggles.
    On "Detect Peaks" it pre-selects peaks on the main plot and
    opens the PeakReviewWindow.
    In batch mode it also tracks progress and auto-advances.
    """

    def __init__(self, parent=None, batch_mode=False):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.batch_mode = batch_mode
        self._review_win = None          # PeakReviewWindow instance (kept alive)
        self._detected   = []            # [(row_data, nominal_mz, det_mz, det_int), …]

        self.setWindowTitle("Manual Recalibration")
        self.resize(760, 500)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(4)
        root.setContentsMargins(6, 6, 6, 6)

        # ── Menu bar (File: Import / Export - identical to peaks window) ──
        mbar   = QtWidgets.QMenuBar()
        fmenu  = mbar.addMenu("File")
        self._import_act = QtWidgets.QAction("Import…", self)
        self._export_act = QtWidgets.QAction("Export…", self)
        fmenu.addAction(self._import_act)
        fmenu.addAction(self._export_act)
        root.setMenuBar(mbar)
        self._install_stay_on_top(mbar, settings)
        self._import_act.triggered.connect(self._import_peaks)
        self._export_act.triggered.connect(self._export_peaks)

        # ── Batch progress banner (only visible in batch mode) ──
        self._batch_banner = QtWidgets.QLabel()
        self._batch_banner.setStyleSheet(
            "background: #1e3a5f; color: white; padding: 4px 8px; "
            "border-radius: 4px; font-weight: bold;")
        self._batch_banner.setWordWrap(True)
        self._batch_banner.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._batch_banner.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred)
        self._batch_banner.setMinimumWidth(0)
        self._batch_banner.setVisible(False)
        root.addWidget(self._batch_banner)

        # ── Instruction label ──
        info_row = QtWidgets.QHBoxLayout()
        info = QtWidgets.QLabel(
            'Toggle the peak rows you want to use as calibration anchors, '
            'then click \u201cDetect Peaks\u201d.')
        info.setWordWrap(True)
        info.setStyleSheet("color: gray; font-size: 11px;")
        info_row.addWidget(info, stretch=1)

        info_row.addSpacing(12)
        info_row.addWidget(QtWidgets.QLabel("Min SNR:"))
        self._snr_spin = QtWidgets.QDoubleSpinBox()
        self._snr_spin.setRange(0.0, 100.0)
        self._snr_spin.setDecimals(1)
        self._snr_spin.setSingleStep(0.1)
        self._snr_spin.setValue(float(settings.value("manrecal_snr_threshold", 1.0)))
        self._snr_spin.setFixedWidth(70)
        self._snr_spin.setToolTip(
            "Minimum signal-to-noise ratio for a candidate to be accepted as a real peak.\n"
            "Lower values accept weaker peaks; higher values reject more noise.\n"
            "Set to 0 to accept all candidates regardless of noise.")
        info_row.addWidget(self._snr_spin)
        root.addLayout(info_row)

        # ── Scrollable peak-row area ──
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self._rows_container = QtWidgets.QWidget()
        self._rows_layout    = QtWidgets.QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(2)
        self._rows_layout.addStretch()
        scroll.setWidget(self._rows_container)
        root.addWidget(scroll)

        # ── Bottom button bar ──
        btn_row = QtWidgets.QHBoxLayout()
        self._detect_btn = QtWidgets.QPushButton("🔍  Detect Peaks")
        self._detect_btn.setFixedHeight(32)
        self._detect_btn.setToolTip(
            "Locate each enabled peak in the current spectrum and open the review window.")
        btn_row.addWidget(self._detect_btn)

        self._refresh_rows_btn = QtWidgets.QPushButton("↺  Refresh Peak List")
        self._refresh_rows_btn.setFixedHeight(32)
        self._refresh_rows_btn.setToolTip(
            "Reload peak rows from the main peaks window (useful after importing a new peak list).")
        self._refresh_rows_btn.clicked.connect(self._populate_rows)
        btn_row.addWidget(self._refresh_rows_btn)
        btn_row.addStretch()

        if batch_mode:
            self._prev_btn = QtWidgets.QPushButton("← Previous")
            self._prev_btn.setToolTip("Go back to the previous file (already processed or skipped).")
            btn_row.addWidget(self._prev_btn)
            self._prev_btn.clicked.connect(self._go_to_previous)

            self._skip_btn = QtWidgets.QPushButton("Skip File →")
            self._skip_btn.setToolTip("Skip this file and move to the next one in the batch.")
            btn_row.addWidget(self._skip_btn)
            self._skip_btn.clicked.connect(self._skip_file)

        self._close_btn = QtWidgets.QPushButton("Close")
        btn_row.addWidget(self._close_btn)
        root.addLayout(btn_row)

        self._add_overlay_chk = QtWidgets.QCheckBox("Add recalibrated spectrum as overlay")
        self._add_overlay_chk.setChecked(False)
        self._add_overlay_chk.setToolTip("If checked, the saved file will also appear as an overlay in the main window.")
        root.addWidget(self._add_overlay_chk)

        self._detect_btn.clicked.connect(self._detect_peaks)
        self._close_btn.clicked.connect(self._on_close)

        # ── Populate rows from current custom_peak_rows ──
        self._row_widgets = []   # list of dicts with toggle, label, color_swatch
        self._populate_rows()
        self._update_batch_banner()

    # ── Row population ────────────────────────────────────────────────────
    def _populate_rows(self):
        # Disconnect any existing peak checkbox sync connections
        for w in self._row_widgets:
            try:
                w['row_data']["checkbox"].stateChanged.disconnect(w['_peak_conn'])
            except Exception:
                pass

        for w in self._row_widgets:
            w['widget'].deleteLater()
        self._row_widgets.clear()

        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for i, row in enumerate(custom_peak_rows):
            rw = QtWidgets.QWidget()
            rw.setMinimumHeight(24)
            rl = QtWidgets.QHBoxLayout(rw)
            rl.setContentsMargins(2, 1, 2, 1)
            rl.setSpacing(6)

            # Color swatch
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(14, 14)
            color_name = row['color'][0].name()
            swatch.setStyleSheet(
                f"background-color: {color_name}; border: 1px solid gray; border-radius: 2px;")

            # Toggle - truncate long peak text so it doesn't force wide layout on Windows
            label_txt = row['label_input'].text().strip() or f"Row {i+1}"
            peaks_txt = row['peaks_input'].text().strip()
            toggle = QtWidgets.QCheckBox(f"{label_txt}   [{peaks_txt}]")

            # Sync initial state from peaks window checkbox
            toggle.setChecked(row["checkbox"].isChecked())

            # Bidirectional sync
            def _on_recal_toggle(state, r=row):
                r["checkbox"].blockSignals(True)
                r["checkbox"].setChecked(bool(state == 2))
                r["checkbox"].blockSignals(False)
                render_plot()

            def _on_peak_checkbox(state, t=toggle):
                t.blockSignals(True)
                t.setChecked(bool(state == 2))
                t.blockSignals(False)

            toggle.stateChanged.connect(_on_recal_toggle)
            row["checkbox"].stateChanged.connect(_on_peak_checkbox)

            ignore_edit = QtWidgets.QLineEdit()
            ignore_edit.setPlaceholderText("Ignore m/z (comma-sep)…")
            ignore_edit.setFixedWidth(180)
            ignore_edit.setToolTip(
                "Comma-separated m/z values to exclude from peak detection for this row.\n"
                "Does not modify the main peak list.")

            rl.addWidget(swatch)
            rl.addWidget(toggle)
            rl.addStretch()
            rl.addWidget(ignore_edit)

            self._rows_layout.addWidget(rw)
            self._row_widgets.append({
                'widget': rw, 'toggle': toggle, 'row_data': row,
                'index': i, '_peak_conn': _on_peak_checkbox,
                'ignore_edit': ignore_edit
            })

        self._rows_layout.addStretch()

    def _load_toggle_states(self):
        raw = settings.value("manrecal_toggle_states", "{}")
        try:
            return json.loads(raw) if isinstance(raw, str) else {}
        except Exception:
            return {}

    def _save_toggle_states(self):
        states = {str(w['index']): w['toggle'].isChecked()
                  for w in self._row_widgets}
        settings.setValue("manrecal_toggle_states", json.dumps(states))

    # ── Batch banner ──────────────────────────────────────────────────────
    def _update_batch_banner(self):
        if not self.batch_mode:
            self._batch_banner.setVisible(False)
            return
        done  = _batch_manual_state["done_count"]
        # Enable/disable Previous button based on position
        if hasattr(self, '_prev_btn'):
            self._prev_btn.setEnabled(done > 0)
        total = _batch_manual_state["total"]
        fname = ""
        idx   = done
        files = _batch_manual_state["files"]
        if 0 <= idx < len(files):
            fname = os.path.basename(files[idx])
        self._batch_banner.setText(
            f"Recalibrating file {done+1} / {total}<br>"
            f"<span style='color:#aac4ff'>Current:&nbsp;&nbsp;{fname}</span>")
        self._batch_banner.setVisible(True)
        short = (fname[:40] + "…") if len(fname) > 40 else fname
        self.setWindowTitle(f"Manual Recalibration  [{done+1}/{total}]  {short}")

    # ── Peak detection ────────────────────────────────────────────────────
    def _detect_peaks(self):
        if df is None:
            QtWidgets.QMessageBox.warning(self, "No spectrum", "Load a spectrum first.")
            return
    
        # self._save_toggle_states()
        self._detected.clear()
        _clear_manual_recal_scatter()
    
        active_mz_list = []
        for w in self._row_widgets:
            if not w['toggle'].isChecked(): continue
            row = w['row_data']
            peaks_txt = row['peaks_input'].text().strip()
            ignore_txt = w.get('ignore_edit', QtWidgets.QLineEdit()).text().strip()
            ignored = set()
            if ignore_txt:
                for val in ignore_txt.split(','):
                    try: ignored.add(float(val.strip()))
                    except ValueError: pass
            for nominal in parse_peaks_text(peaks_txt):
                if any(abs(nominal - ig) < 0.5 for ig in ignored):
                    continue
                active_mz_list.append((row, nominal))
    
        if not active_mz_list:
            QtWidgets.QMessageBox.information(self, "No peaks enabled", 
                "Enable at least one peak row with m/z values.")
            return
    
        # NEW: Noise filtering parameters
        SNR_THRESHOLD = self._snr_spin.value()      # Minimum signal-to-noise ratio
        settings.setValue("manrecal_snr_threshold", SNR_THRESHOLD)
        LOCAL_WINDOW = 5.0       # ±2.5 Da around peak for noise estimation
        
        detected_scatter_x = []
        detected_scatter_y = []
        
        for row_data, nominal in active_mz_list:
            result = _detect_peak_near(df, nominal, window=1.5)
            if result is None: continue
            
            det_mz, det_int = result
            
            # NEW: Calculate local SNR
            left = max(0, df.index[df['mz'] >= det_mz - LOCAL_WINDOW].min())
            right = min(len(df)-1, df.index[df['mz'] <= det_mz + LOCAL_WINDOW].max())
            
            if left is None or right is None or left >= right:
                # No local window - accept anyway
                self._detected.append((row_data, nominal, det_mz, det_int))
                detected_scatter_x.append(det_mz)
                detected_scatter_y.append(det_int)
                continue
                
            local_region = df.iloc[left:right+1]
            noise_level = local_region['intensity'].quantile(0.25)  # Q1 as noise estimate
            
            if noise_level == 0: noise_level = 1e-6  # Avoid div by zero
            snr = det_int / noise_level
            
            # NEW: Auto-filter noise peaks
            if snr >= SNR_THRESHOLD:
                self._detected.append((row_data, nominal, det_mz, det_int))
                detected_scatter_x.append(det_mz)
                detected_scatter_y.append(det_int)
            else:
                print(f"Filtered noise peak: {det_mz:.2f} (SNR={snr:.1f})")
    
        if not detected_scatter_x:
            QtWidgets.QMessageBox.warning(self, "No real peaks found",
                f"Found {len(active_mz_list)} candidates but all had SNR < {SNR_THRESHOLD}.\n"
                "Try:\n• Lowering expected m/z values closer to actual peaks\n"
                "• Adjusting spectrum zoom to see real peaks\n• Reducing SNR threshold")
            return
    
        # Draw triangles (only real peaks)
        _draw_manual_recal_scatter(np.array(detected_scatter_x), np.array(detected_scatter_y))
        
        # Open review window...


        # Open or update review window
        if self._review_win is not None and self._review_win.isVisible():
            # Update in place
            self._review_win._detected = self._detected
            self._review_win._add_as_overlay = self._add_overlay_chk.isChecked()
            self._review_win.batch_mode = self.batch_mode
            if _manual_recal_scatter_items:
                self._review_win._scatter = _manual_recal_scatter_items[-1]
            self._review_win._rebuild_rows()
        else:
            self._review_win = PeakReviewWindow(
                self._detected,
                parent_recal_win=self,
                batch_mode=self.batch_mode,
                add_as_overlay=self._add_overlay_chk.isChecked())
            if _manual_recal_scatter_items:
                self._review_win._scatter = _manual_recal_scatter_items[-1]
            # Position below the ManualRecalWindow instead of top-right corner
            geo = self.geometry()
            self._review_win.move(geo.left(), geo.top())
            self._review_win.show()

    # ── Import / Export (same JSON format as peaks window) ────────────────
    def _import_peaks(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Peak List", "", "JSON Files (*.json)")
        if not path: return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            _selected_peak_indices.clear(); _select_anchor[0] = None
            for row in custom_peak_rows[:]:
                peaks_rows_layout_remove(row["widget"])
                custom_peak_rows.remove(row)
            if isinstance(data, dict):
                _saved_grps = data.get("groups", [{"name": "Unclassified", "alpha": 255}])
                items = data.get("rows", [])
            else:
                _saved_grps = [{"name": "Unclassified", "alpha": 255}]
                items = data
            _peak_groups.clear(); _group_header_widgets.clear()
            for _g in _saved_grps:
                _peak_groups.append({"name": _g["name"], "alpha": _g.get("alpha", 255), "collapsed": False, "highlight": _g.get("highlight", True), "L_active": _g.get("L_active", True), "1L_active": _g.get("1L_active", True), "curve_active": _g.get("curve_active", True)})
            for item in items:
                _gn = item.get("group", "Unclassified")
                if not any(g["name"] == _gn for g in _peak_groups):
                    _peak_groups.append({"name": _gn, "alpha": 255, "collapsed": False, "highlight": True, "L_active": True, "1L_active": True, "curve_active": True})
                if item.get("mode") == "range":
                    _add_peak_row_base(
                        checked=item.get("checked", True),
                        color=QtGui.QColor(item.get("color", "#ff0000")),
                        label_text=item.get("label", ""),
                        group_name=_gn,
                        range_values=(
                            item.get("range_start", 0.0),
                            item.get("range_end",   0.0),
                            item.get("range_step",  1.0),
                        ))
                else:
                    _add_peak_row_base(
                        checked=item.get("checked", True),
                        color=QtGui.QColor(item.get("color", "#ff0000")),
                        peaks_text=item.get("peaks", ""),
                        label_text=item.get("label", ""),
                        group_name=_gn)
                _apply_row_legend_fields(custom_peak_rows[-1], item)
            _clear_highlight_cache()
            update_pick_row_combo()
            render_plot()
            _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
            self._populate_rows()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Import Failed", str(e))

    def _export_peaks(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Peak List", "", "JSON Files (*.json)")
        if not path: return
        data = [{"checked": r["checkbox"].isChecked(),
                 "color": r["color"][0].name(),
                 "peaks": r["peaks_input"].text(),
                 "label": r["label_input"].text(),
                 **_row_legend_fields(r)}
                for r in custom_peak_rows]
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    # ── Skip file (batch only) ─────────────────────────────────────────────
    def _skip_file(self):
        _batch_manual_state["done_count"] += 1
        _save_batch_manual_state()
        _advance_batch_manual(self)

    # ── Go to previous file (batch only) ──────────────────────────────────
    def _go_to_previous(self):
        done = _batch_manual_state["done_count"]
        if done <= 0:
            QtWidgets.QMessageBox.information(
                self, "No previous file", "This is the first file in the batch.")
            return
        _batch_manual_state["done_count"] = done - 1
        _save_batch_manual_state()
        files = _batch_manual_state["files"]
        prev_idx = _batch_manual_state["done_count"]
        prev_path = files[prev_idx] if 0 <= prev_idx < len(files) else None
        if prev_path and os.path.exists(prev_path):
            _load_file_for_batch(prev_path)
        self._update_batch_banner()
        self._populate_rows()

    # ── Close ──────────────────────────────────────────────────────────────
    def _on_close(self):
        _clear_manual_recal_scatter()
        if self._review_win is not None:
            try:
                self._review_win.close()
            except Exception:
                pass
        if self.batch_mode:
            # Ask before discarding batch state
            reply = QtWidgets.QMessageBox.question(
                self, "Close Batch",
                "Close the batch recalibration session?\n"
                "Progress so far is saved; you can resume next time.",
                QtWidgets.QMessageBox.StandardButton.Yes |
                QtWidgets.QMessageBox.StandardButton.No)
            if reply != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            _batch_manual_state["active"] = False
            _save_batch_manual_state()
        self.close()

    def closeEvent(self, event):
        _clear_manual_recal_scatter()
        # Disconnect peak checkbox sync connections
        for w in self._row_widgets:
            try:
                row = w['row_data']
                row["checkbox"].stateChanged.disconnect(w['_peak_conn'])
            except Exception:
                pass
        if self._review_win is not None:
            try:
                self._review_win.close()
            except Exception:
                pass
        super().closeEvent(event)



# ─────────────────────────────────────────────
#  Scatter overlay helpers (inverted triangles)
# ─────────────────────────────────────────────
def _clear_manual_recal_scatter():
    global _manual_recal_scatter_items
    for item in _manual_recal_scatter_items:
        try:
            plot.removeItem(item)
        except Exception:
            pass
    _manual_recal_scatter_items.clear()

def _on_manual_recal_hover(item, points, ev):
    if _active_peak_review_window is None:
        return
    
    if len(points) == 0:
        _active_peak_review_window.clear_row_highlight()
        return
    
    idx = points[0].data()
    _active_peak_review_window.highlight_row(idx)


def _draw_manual_recal_scatter(mz_arr, int_arr):
    global _manual_recal_scatter_items, _manual_recal_indices
    _clear_manual_recal_scatter()
    y_plot = (np.log10(np.clip(int_arr, 1e-10, None)) + 0.04) if _log_y else (int_arr * 1.04)

    data = list(range(len(mz_arr)))
    scatter = pg.ScatterPlotItem(
        x=mz_arr,
        y=y_plot,
        symbol='t1',
        size=14,
        pen=pg.mkPen('#cc0000', width=1.5),
        brush=pg.mkBrush(220, 40, 40, 200),
        hoverable=True,
        hoverPen=pg.mkPen('#ffff00', width=2),
        hoverBrush=pg.mkBrush(255, 255, 0, 180),
        data=data,
    )
    scatter.sigHovered.connect(_on_manual_recal_hover)
    plot.addItem(scatter)
    _manual_recal_scatter_items.append(scatter)

    # Small red dot exactly at the spin-box selected mz on the spectrum curve
    if df is not None and len(df) > 0:
        dot_x = []; dot_y = []
        for mz in mz_arr:
            tol = 0.5
            sub = df[np.abs(df['mz'] - mz) <= tol]
            if sub.empty:
                sub = df.iloc[(df['mz'] - mz).abs().argsort()[:1]]
            if not sub.empty:
                actual_mz  = float(sub.loc[sub['intensity'].idxmax(), 'mz'])
                actual_int = float(sub['intensity'].max())
                dot_x.append(actual_mz)
                dot_y.append((np.log10(actual_int) if actual_int > 0 else 0) if _log_y else actual_int)
        if dot_x:
            dot_scatter = pg.ScatterPlotItem(
                x=np.array(dot_x), y=np.array(dot_y),
                symbol='o', size=8,
                pen=pg.mkPen('#ff4444', width=1),
                brush=pg.mkBrush(255, 50, 50, 230))
            plot.addItem(dot_scatter)
            _manual_recal_scatter_items.append(dot_scatter)



# ═════════════════════════════════════════════════════════════════════════════
#  RESIDUAL PREVIEW DIALOG
# ═════════════════════════════════════════════════════════════════════════════

_RPD_COLORS = ['#4e9de0', '#e8944b', '#5cba6e', '#c46ee8',
               '#e8c84b', '#4be8d8', '#e84b7e', '#a0a0a0']


class ResidualPreviewDialog(QtWidgets.QDialog):
    """
    Non-modal live preview of per-anchor Δm/z residuals after manual recalibration.

    Shown before the recalibrated file is saved so the user can inspect the
    calibration quality and either confirm (proceed to save / next sample)
    or cancel (return to PeakReviewWindow to adjust peak assignments).
    """

    # Emitted with the row-index (into summary_df / pairs) of a clicked anchor
    anchor_clicked = QtCore.pyqtSignal(int)

    def __init__(self, summary_df, stem="", color_map=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Residuals Preview — {stem}" if stem else "Residuals Preview")
        self.resize(640, 420)
        self.setMinimumSize(480, 300)
        # Use Window (not Dialog) so it has its own place in the window stack,
        # is not forced above other application windows, and gets maximize/minimize.
        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )
        self.setModal(False)

        self._pg_widget = pg.GraphicsLayoutWidget()
        self._bar_plot  = self._pg_widget.addPlot(title="Calibration anchors — Δm/z per peak")
        self._bar_plot.setLabel('bottom', 'Original m/z')
        self._bar_plot.setLabel('left',   'Δ m/z')
        self._bar_plot.showGrid(x=True, y=True, alpha=0.3)
        self._bar_plot.getAxis('left').enableAutoSIPrefix(False)

        # Hover/click handlers — connected once, use self._anchor_pts set by _redraw
        self._hover_lbl  = pg.TextItem("", anchor=(1, 0), color='w')
        self._anchor_pts: list = []   # (x, y, pl_name, row_idx)
        self._rpd_proxy  = pg.SignalProxy(
            self._bar_plot.scene().sigMouseMoved,
            rateLimit=30, slot=self._on_mouse_move)
        self._bar_plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

        self._info_lbl = QtWidgets.QLabel("")
        self._info_lbl.setStyleSheet("font-size: 9pt; color: gray;")
        self._info_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        confirm_btn = QtWidgets.QPushButton("✔  Confirm & Save")
        confirm_btn.setFixedHeight(32)
        confirm_btn.setStyleSheet(
            "QPushButton { background: #1e5f28; color: white; "
            "border-radius: 4px; font-weight: bold; padding: 0 14px; }"
            "QPushButton:hover { background: #27802e; }")
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.setFixedHeight(32)
        confirm_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(confirm_btn)
        btn_row.addWidget(cancel_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(self._pg_widget, stretch=1)
        lay.addWidget(self._info_lbl)
        lay.addLayout(btn_row)

        self._redraw(summary_df, color_map)

    # ── Color resolution ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_colors(summary_df, color_map):
        pl_col = (summary_df["peak_list"].fillna("").astype(str).str.strip()
                  if "peak_list" in summary_df.columns
                  else pd.Series([""] * len(summary_df)))
        unique_pls = list(dict.fromkeys(pl_col))
        result = dict(color_map) if color_map else {}
        auto_idx = 0
        for n in unique_pls:
            if n not in result:
                result[n] = _RPD_COLORS[auto_idx % len(_RPD_COLORS)]
                auto_idx += 1
        return pl_col, result

    # ── Chart drawing ─────────────────────────────────────────────────────────

    def _redraw(self, summary_df, color_map=None):
        orig  = summary_df["original m/z"].to_numpy(dtype=float)
        delta = summary_df["Δ m/z"].to_numpy(dtype=float)
        pl_col, resolved = self._resolve_colors(summary_df, color_map)

        self._bar_plot.clear()
        self._bar_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                               style=QtCore.Qt.PenStyle.DashLine))

        # Re-parent hover label after clear()
        self._hover_lbl.setText("")
        self._hover_lbl.setParentItem(self._bar_plot.getViewBox())
        self._anchor_pts = []

        for row_idx, (x, d, pl) in enumerate(zip(orig, delta, pl_col)):
            color = resolved.get(pl, '#a0a0a0')
            self._bar_plot.addItem(pg.PlotDataItem([x, x], [0, d],
                                                  pen=pg.mkPen(color, width=4)))
            self._bar_plot.addItem(pg.ScatterPlotItem(
                x=[x], y=[d], size=10,
                pen=pg.mkPen('w', width=0.5),
                brush=pg.mkBrush(color)))
            self._anchor_pts.append((x, d, pl, row_idx))
            lbl = pg.TextItem(f"Δ{d:+.3f}", anchor=(0.5, 1.0), color='w')
            lbl.setPos(x, d)
            self._bar_plot.addItem(lbl)

        n_pts = len(orig)
        rms   = float(np.sqrt(np.mean(delta ** 2))) if n_pts else 0.0
        max_d = float(np.max(np.abs(delta))) if n_pts else 0.0
        self._info_lbl.setText(
            f"{n_pts} anchor(s)  |  RMS Δ = {rms:.4f}  |  max |Δ| = {max_d:.4f}")

    def update_data(self, summary_df, color_map=None):
        """Refresh the chart live without closing the dialog."""
        self._redraw(summary_df, color_map)

    # ── Hover label ───────────────────────────────────────────────────────────

    def _nearest_anchor(self, scene_pos):
        """Return (row_idx, pl_name) of the nearest anchor within threshold, or (-1, '')."""
        vb = self._bar_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return -1, ""
        mp  = vb.mapSceneToView(scene_pos)
        vr  = vb.viewRange()
        x_span = abs(vr[0][1] - vr[0][0]) or 1.0
        y_span = abs(vr[1][1] - vr[1][0]) or 1.0
        best_idx, best_name, best_dist = -1, "", float("inf")
        for ax, ay, apl, ridx in self._anchor_pts:
            dx = (mp.x() - ax) / x_span
            dy = (mp.y() - ay) / y_span
            dist = (dx*dx + dy*dy) ** 0.5
            if dist < 0.05 and dist < best_dist:
                best_dist = dist
                best_idx  = ridx
                best_name = apl
        return best_idx, best_name

    def _on_mouse_move(self, evt):
        pos = evt[0]
        vb  = self._bar_plot.getViewBox()
        _, name = self._nearest_anchor(pos)
        self._hover_lbl.setText(name)
        if name:
            vr = vb.viewRange()
            self._hover_lbl.setPos(vr[0][1], vr[1][1])

    def _on_scene_clicked(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        row_idx, _ = self._nearest_anchor(event.scenePos())
        if row_idx >= 0:
            self.anchor_clicked.emit(row_idx)
            event.accept()


# ═════════════════════════════════════════════════════════════════════════════
#  PEAK REVIEW WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class PeakReviewWindow(QtWidgets.QWidget, StayOnTopMixin):
    """
    Shows each pre-selected peak grouped by peak-list row.
    Each group has a header with a color swatch, label, and an all/none toggle.
    Within a group peaks are sorted by m/z ascending; untoggling one peak
    automatically untoggle all higher-mass peaks in the same group.
    """
    def __init__(self, detected, parent_recal_win, batch_mode=False, add_as_overlay=False):
        super().__init__(None, QtCore.Qt.WindowType.Window)
        global _active_peak_review_window
        _active_peak_review_window = self

        self._parent = parent_recal_win
        self._detected = detected
        self.batch_mode = batch_mode
        self._add_as_overlay = add_as_overlay
        self._scatter = None
        self._mz_spins     = []   # parallel to self._detected
        self._include_chks = []   # parallel to self._detected
        self._row_widgets  = []   # parallel to self._detected (hover highlight)
        self._peak_highlight_curve = None
        self._highlighted_row_idx  = None
        self._zoom_markers         = []   # InfiniteLine/PlotDataItem items for zoom annotation
        self._preview_dlg        = None   # live ResidualPreviewDialog (non-modal)
        self._pending_save_data  = None   # dict of latest computed recal results
        self._cascade_mode       = settings.value("prw_cascade_mode", False, type=bool)

        self.setWindowTitle("Review Detected Peaks")
        self.resize(700, 520)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        _mbar_prw = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar_prw, settings)

        _mode_menu = _mbar_prw.addMenu("Mode")
        self._cascade_action = QtWidgets.QAction(
            "Cascade untoggle (disable higher-mass peaks)", _mbar_prw)
        self._cascade_action.setCheckable(True)
        self._cascade_action.setChecked(self._cascade_mode)
        self._cascade_action.setToolTip(
            "When enabled: unchecking a peak also unchecks all higher-mass peaks\n"
            "in the same group.  When disabled: each peak is toggled independently.")
        self._cascade_action.toggled.connect(self._on_cascade_toggled)
        _mode_menu.addAction(self._cascade_action)

        root.setMenuBar(_mbar_prw)

        hdr_row = QtWidgets.QHBoxLayout()
        self._hdr_lbl = QtWidgets.QLabel()
        self._hdr_lbl.setWordWrap(True)
        self._hdr_lbl.setStyleSheet("color: gray; font-size: 11px;")
        self._update_header_label()
        hdr_row.addWidget(self._hdr_lbl, stretch=1)

        self._markers_btn = QtWidgets.QPushButton("⇹ Lines")
        self._markers_btn.setCheckable(True)
        self._markers_btn.setChecked(True)
        self._markers_btn.setFixedHeight(24)
        self._markers_btn.setToolTip(
            "Show/hide the vertical reference lines and arrow\n"
            "drawn when clicking a zoom (⊙) button.")
        self._markers_btn.toggled.connect(self._on_markers_toggled)
        hdr_row.addWidget(self._markers_btn, stretch=0)

        hdr_row.addWidget(QtWidgets.QLabel("if Δ ≥"))
        self._markers_thr = QtWidgets.QDoubleSpinBox()
        self._markers_thr.setRange(0.0, 1000.0)
        self._markers_thr.setDecimals(2)
        self._markers_thr.setSingleStep(0.1)
        self._markers_thr.setValue(float(settings.value("manrecal_markers_thr", 0.3)))
        self._markers_thr.setFixedWidth(65)
        self._markers_thr.setFixedHeight(24)
        self._markers_thr.setToolTip(
            "Lines and arrow are only drawn when |detected − reference| ≥ this value.\n"
            "Set to 0 to always show them.")
        self._markers_thr.valueChanged.connect(
            lambda v: settings.setValue("manrecal_markers_thr", v))
        hdr_row.addWidget(self._markers_thr, stretch=0)
        root.addLayout(hdr_row)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        self._scroll = scroll
        inner = QtWidgets.QWidget()
        self._inner_layout = QtWidgets.QVBoxLayout(inner)
        self._inner_layout.setContentsMargins(4, 4, 4, 4)
        self._inner_layout.setSpacing(6)
        scroll.setWidget(inner)
        root.addWidget(scroll)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        self._apply_btn = QtWidgets.QPushButton("✔  Apply Manual Recalibration")
        self._apply_btn.setFixedHeight(34)
        self._apply_btn.setStyleSheet(
            "QPushButton { background: #1e5f28; color: white; "
            "border-radius: 4px; font-weight: bold; padding: 0 12px; }"
            "QPushButton:hover { background: #27802e; }")
        btn_row.addWidget(self._apply_btn)
        self._cancel_btn = QtWidgets.QPushButton("Cancel")
        btn_row.addWidget(self._cancel_btn)
        root.addLayout(btn_row)

        self._apply_btn.clicked.connect(self._apply)
        self._cancel_btn.clicked.connect(self._cancel)

        self._build_grouped_ui()

    # ── helpers ───────────────────────────────────────────────────────────

    def _group_detected(self):
        """Return an ordered dict: row_data_id -> (row_data, [(global_idx, nominal, det_mz, det_int), ...])
        Entries within each group are sorted by nominal m/z ascending."""
        groups = {}
        order  = []
        for g_idx, (row_data, nominal, det_mz, det_int) in enumerate(self._detected):
            key = id(row_data)
            if key not in groups:
                groups[key] = (row_data, [])
                order.append(key)
            groups[key][1].append((g_idx, nominal, det_mz, det_int))
        # sort each group by nominal m/z
        for key in order:
            groups[key][1].sort(key=lambda t: t[1])
        return order, groups

    def _clear_zoom_markers(self):
        """Remove all vertical-line / arrow markers added by the zoom buttons."""
        for item in self._zoom_markers:
            try:
                plot.removeItem(item)
            except Exception:
                pass
        self._zoom_markers.clear()

    def _on_markers_toggled(self, checked):
        if not checked:
            self._clear_zoom_markers()

    _APPLY_BTN_GREEN = (
        "QPushButton { background: #1e5f28; color: white; "
        "border-radius: 4px; font-weight: bold; padding: 0 12px; }"
        "QPushButton:hover { background: #27802e; }")
    _APPLY_BTN_GREY = (
        "QPushButton { background: #555; color: #999; "
        "border-radius: 4px; font-weight: bold; padding: 0 12px; }")

    def _set_apply_btn_state(self, active: bool):
        self._apply_btn.setEnabled(active)
        self._apply_btn.setStyleSheet(
            self._APPLY_BTN_GREEN if active else self._APPLY_BTN_GREY)

    def _on_cascade_toggled(self, checked):
        self._cascade_mode = checked
        settings.setValue("prw_cascade_mode", checked)
        self._update_header_label()

    def _update_header_label(self):
        if self._cascade_mode:
            txt = ("Peaks are grouped by peak list.  Each group header has an all/none toggle.\n"
                   "Cascade mode: untoggling a peak also untoggle all higher-mass peaks in the same group.")
        else:
            txt = ("Peaks are grouped by peak list.  Each group header has an all/none toggle.\n"
                   "Each peak is toggled independently.")
        self._hdr_lbl.setText(txt)

    def _build_grouped_ui(self):
        """Populate (or repopulate) the inner scroll layout with grouped peak rows."""
        self._clear_zoom_markers()
        # Clear existing widgets and lists
        while self._inner_layout.count():
            item = self._inner_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._mz_spins.clear()
        self._include_chks.clear()
        self._row_widgets.clear()

        # Resize parallel lists to match self._detected
        self._mz_spins     = [None] * len(self._detected)
        self._include_chks = [None] * len(self._detected)
        self._row_widgets  = [None] * len(self._detected)

        order, groups = self._group_detected()

        all_chks = []   # (nominal, g_idx, inc_chk) across every group

        for key in order:
            row_data, entries = groups[key]
            color_name = row_data['color'][0].name()
            label_txt  = row_data['label_input'].text().strip() or "-"

            # ── Group box ──────────────────────────────────────────────
            group_box = QtWidgets.QGroupBox()
            group_box.setStyleSheet(
                f"QGroupBox {{ border: 1.5px solid {color_name}; border-radius: 5px; "
                f"margin-top: 6px; padding-top: 4px; }}"
            )
            group_vbox = QtWidgets.QVBoxLayout(group_box)
            group_vbox.setContentsMargins(6, 2, 6, 6)
            group_vbox.setSpacing(3)

            # ── Group header row ───────────────────────────────────────
            hdr_row = QtWidgets.QHBoxLayout()
            hdr_row.setSpacing(6)

            swatch_hdr = QtWidgets.QLabel()
            swatch_hdr.setFixedSize(14, 14)
            swatch_hdr.setStyleSheet(
                f"background-color: {color_name}; "
                "border: 1px solid gray; border-radius: 2px;")
            hdr_row.addWidget(swatch_hdr)

            lbl_hdr = QtWidgets.QLabel(f"<b>{label_txt}</b>")
            lbl_hdr.setStyleSheet(f"color: {color_name};")
            hdr_row.addWidget(lbl_hdr)
            hdr_row.addStretch()

            # All / None toggle for the group
            all_none_btn = QtWidgets.QPushButton("None")
            all_none_btn.setFixedSize(46, 20)
            all_none_btn.setCheckable(True)
            all_none_btn.setChecked(False)
            all_none_btn.setToolTip("Toggle all peaks in this group on or off")
            all_none_btn.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 0 4px; }"
                "QPushButton:checked { background: #444; color: #aaa; }")
            hdr_row.addWidget(all_none_btn)
            group_vbox.addLayout(hdr_row)

            # ── Column header labels ───────────────────────────────────
            col_hdr = QtWidgets.QHBoxLayout()
            col_hdr.setSpacing(4)
            col_hdr.addSpacing(20)   # indent
            for txt, w in [("Nominal m/z", 90), ("Detected m/z", 130), ("Reference m/z", 130)]:
                lbl = QtWidgets.QLabel(f"<small><i>{txt}</i></small>")
                lbl.setFixedWidth(w)
                lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                col_hdr.addWidget(lbl)
            col_hdr.addWidget(QtWidgets.QLabel("<small><i>Include</i></small>"))
            col_hdr.addStretch()
            group_vbox.addLayout(col_hdr)

            # ── One row per peak in this group ─────────────────────────
            group_chks = []   # include checkboxes within this group, sorted by m/z

            for (g_idx, nominal, det_mz, det_int) in entries:
                peak_row = QtWidgets.QWidget()
                peak_row.setMinimumHeight(28)
                pr_layout = QtWidgets.QHBoxLayout(peak_row)
                pr_layout.setContentsMargins(20, 0, 0, 0)  # indent
                pr_layout.setSpacing(4)

                nom_lbl = QtWidgets.QLabel(f"{nominal:.2f}")
                nom_lbl.setFixedWidth(90)
                nom_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                nom_lbl.setStyleSheet("color: gray; font-size: 11px;")

                spin = QtWidgets.QDoubleSpinBox()
                spin.setRange(0.0, 100000.0)
                spin.setDecimals(4)
                spin.setSingleStep(0.01)
                spin.setValue(det_mz)
                spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                spin.setToolTip("Auto-detected m/z - edit if the program picked the wrong peak.")
                spin.setFixedWidth(130)

                ref_spin = QtWidgets.QDoubleSpinBox()
                ref_spin.setRange(0.0, 100000.0)
                ref_spin.setDecimals(4)
                ref_spin.setSingleStep(0.01)
                ref_spin.setValue(nominal)
                ref_spin.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                ref_spin.setToolTip("Reference (true) m/z - adjust if needed.")
                ref_spin.setFixedWidth(130)
                spin._ref_spin = ref_spin

                inc_chk = QtWidgets.QCheckBox()
                inc_chk.setChecked(True)
                inc_chk.setToolTip("Uncheck to exclude this peak (also untoggle higher-mass peaks in this group)")

                # Slider: lets user nudge the detected peak by ±5 Da in 0.01 Da steps
                slide = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
                slide.setRange(-2000, 2000)   # steps of 0.01 Da → ±5 Da
                slide.setValue(0)
                slide.setFixedWidth(100)
                slide.setToolTip(
                    "Slide to nudge the detected m/z by up to ±5 Da.\n"
                    "Useful when auto-detection picked a neighbour peak at high mass.")
                _slide_base = [det_mz]   # captured original value

                def _make_slide_handler(_spin, _slide, _base):
                    def _on_slide(val):
                        _spin.blockSignals(True)
                        _spin.setValue(_base[0] + val * 0.001)
                        _spin.blockSignals(False)
                        self._rebuild_scatter_from_selection()
                    def _on_spin_changed(v):
                        _base[0] = v
                        _slide.blockSignals(True)
                        _slide.setValue(0)
                        _slide.blockSignals(False)
                    return _on_slide, _on_spin_changed

                _on_slide_fn, _on_spin_fn = _make_slide_handler(spin, slide, _slide_base)
                slide.valueChanged.connect(_on_slide_fn)
                spin.valueChanged.connect(_on_spin_fn)

                zoom_peak_btn = QtWidgets.QPushButton("⊙")
                zoom_peak_btn.setFixedSize(22, 22)
                zoom_peak_btn.setToolTip(
                    "Zoom the main plot to this peak.\n"
                    "Centers on the detected m/z with ±15 Da padding.")

                def _make_zoom_fn(_spin, _ref_spin, _det_int):
                    def _zoom():
                        mz      = _spin.value()
                        ref_mz  = _ref_spin.value()
                        pad     = 15.0
                        plot.vb.setXRange(mz - pad, mz + pad, padding=0)

                        self._clear_zoom_markers()

                        # Faint centre-line always shown at the detected peak
                        _cc = (255, 255, 255, 25) if current_display == 'dark' else (0, 0, 0, 25)
                        ln_centre = pg.InfiniteLine(
                            pos=mz, angle=90,
                            pen=pg.mkPen(QtGui.QColor(*_cc), width=2))
                        plot.addItem(ln_centre, ignoreBounds=True)
                        self._zoom_markers.append(ln_centre)

                        if not self._markers_btn.isChecked():
                            return
                        if abs(mz - ref_mz) < self._markers_thr.value():
                            return

                        col  = 'w' if current_display == 'dark' else 'k'
                        dash = QtCore.Qt.PenStyle.DashLine

                        # Vertical dashed line at detected peak
                        ln_det = pg.InfiniteLine(
                            pos=mz, angle=90,
                            pen=pg.mkPen("red", width=2, style=dash))
                        plot.addItem(ln_det, ignoreBounds=True)
                        self._zoom_markers.append(ln_det)

                        # Vertical dashed line at reference m/z
                        ln_ref = pg.InfiniteLine(
                            pos=ref_mz, angle=90,
                            pen=pg.mkPen("green", width=2, style=dash))
                        plot.addItem(ln_ref, ignoreBounds=True)
                        self._zoom_markers.append(ln_ref)

                        # y position matching scatter pre-transform convention
                        di = _det_int
                        arrow_y = (
                            np.log10(max(di, 1e-10)) + 0.04 + 0.5
                            if _log_y else di * 1.3
                        )

                        # Shaft: PlotCurveItem so it follows the same rendering
                        # path as ScatterPlotItem (pre-transformed y, no auto log)
                        arr_line = pg.PlotCurveItem(
                            [mz, ref_mz], [arrow_y, arrow_y],
                            pen=pg.mkPen(col, width=2))
                        plot.addItem(arr_line)
                        self._zoom_markers.append(arr_line)

                        # Triangle arrowhead at reference end
                        sym = 't2' if ref_mz >= mz else 't3'
                        arr_head = pg.ScatterPlotItem(
                            [ref_mz], [arrow_y],
                            symbol=sym, size=12,
                            brush=pg.mkBrush(col),
                            pen=pg.mkPen(col, width=1))
                        plot.addItem(arr_head)
                        self._zoom_markers.append(arr_head)

                    return _zoom

                zoom_peak_btn.clicked.connect(_make_zoom_fn(spin, ref_spin, det_int))

                pr_layout.addWidget(nom_lbl)
                pr_layout.addWidget(spin)
                pr_layout.addWidget(slide)
                pr_layout.addWidget(zoom_peak_btn)
                pr_layout.addWidget(ref_spin)
                pr_layout.addWidget(inc_chk)
                pr_layout.addStretch()
                group_vbox.addWidget(peak_row)

                # Store in parallel lists indexed by global detection index
                self._mz_spins[g_idx]     = spin
                self._include_chks[g_idx] = inc_chk
                self._row_widgets[g_idx]  = peak_row
                peak_row._peak_index      = g_idx
                peak_row.installEventFilter(self)

                group_chks.append((nominal, g_idx, inc_chk))
                all_chks.append((nominal, g_idx, inc_chk))
                spin.valueChanged.connect(
                    lambda val, i=g_idx: self._rebuild_scatter_from_selection())

            # ── Cascade logic: untoggle → also disable all higher-mass peaks ──
            # group_chks is already sorted by nominal m/z (ascending)
            def _make_cascade(chks):
                """chks: list of (nominal, g_idx, inc_chk) sorted ascending by nominal."""
                def _on_toggle(checked, source_nominal, source_idx):
                    if checked or not self._cascade_mode:
                        self._rebuild_scatter_from_selection()
                        return
                    # Uncheck all peaks with higher nominal m/z in this group
                    for nom, idx, chk in chks:
                        if nom > source_nominal:
                            chk.blockSignals(True)
                            chk.setChecked(False)
                            chk.blockSignals(False)
                    self._rebuild_scatter_from_selection()
                return _on_toggle

            cascade_fn = _make_cascade(group_chks)
            for nominal, g_idx, chk in group_chks:
                chk.toggled.connect(
                    lambda checked, n=nominal, i=g_idx, fn=cascade_fn: fn(checked, n, i))

            # ── All/None header button ──
            def _make_all_none(chks, btn):
                def _on_click(checked):
                    for _, idx, chk in chks:
                        chk.blockSignals(True)
                        chk.setChecked(not checked)
                        chk.blockSignals(False)
                    btn.setText("All" if checked else "None")
                    self._rebuild_scatter_from_selection()
                return _on_click

            all_none_btn.toggled.connect(_make_all_none(group_chks, all_none_btn))

            self._inner_layout.addWidget(group_box)

        # ── Peer-sync: same nominal across groups ──────────────────────────
        # Build nominal → list of (g_idx, chk) map
        nominal_peers: dict = {}
        for nom, g_idx, chk in all_chks:
            nominal_peers.setdefault(nom, []).append((g_idx, chk))

        def _make_peer_sync(nom, self_g_idx, peers_map):
            def _sync(checked):
                peers = peers_map.get(nom, [])
                if len(peers) <= 1:
                    return
                for g_idx_peer, chk_peer in peers:
                    if g_idx_peer == self_g_idx:
                        continue
                    chk_peer.blockSignals(True)
                    chk_peer.setChecked(checked)
                    chk_peer.blockSignals(False)
                self._rebuild_scatter_from_selection()
            return _sync

        for nom, g_idx, chk in all_chks:
            chk.toggled.connect(_make_peer_sync(nom, g_idx, nominal_peers))

        self._inner_layout.addStretch()

    def _rebuild_rows(self):
        """Refresh all rows when detections change (called from ManualRecalWindow)."""
        self._build_grouped_ui()
        if _manual_recal_scatter_items:
            self._scatter = _manual_recal_scatter_items[-1]

    # ── Recal computation (shared between _apply and live update) ────────────
    def _compute_recal(self):
        """
        Build pairs from current UI state and run recalibration.
        Returns (pairs, color_map, corrected_df, fitparams, summary_df)
        or None when there are no valid pairs.
        """
        pairs = []
        for i, (inc, spin) in enumerate(zip(self._include_chks, self._mz_spins)):
            if not inc.isChecked():
                continue
            obs = spin.value()
            ref = spin._ref_spin.value()
            if obs > 0 and ref > 0:
                row_data = self._detected[i][0]
                pl_name  = row_data["label_input"].text().strip() if row_data else ""
                pl_color = (row_data["color"][0].name()
                            if row_data and row_data.get("color") else None)
                # detect_idx = i so we can navigate back to the row widget
                pairs.append((obs, ref, pl_name, pl_color, i))

        if not pairs:
            return None

        color_map: dict = {}
        for _, _, pl_name, pl_color, *_ in pairs:
            if pl_name not in color_map and pl_color:
                color_map[pl_name] = pl_color

        recal_pairs  = [(obs, ref) for obs, ref, *_ in pairs]
        corrected_df, fitparams = _apply_manual_recal_pairs(df_raw, recal_pairs)

        summary_df = pd.DataFrame({
            "original m/z": [p[0] for p in pairs],
            "corrected to":  [p[1] for p in pairs],
            "Δ m/z":         [p[1] - p[0] for p in pairs],
            "peak_list":     [p[2] for p in pairs],
        })
        return pairs, color_map, corrected_df, fitparams, summary_df

    # ── Apply ──────────────────────────────────────────────────────────────
    def _apply(self):
        if df_raw is None:
            QtWidgets.QMessageBox.warning(
                self, "No spectrum", "No raw spectrum loaded."); return

        result = self._compute_recal()
        if result is None:
            QtWidgets.QMessageBox.warning(
                self, "No valid pairs",
                "All detected peaks were invalid (zero m/z). Cannot recalibrate."); return

        pairs, color_map, corrected_df, fitparams, summary_df = result
        src_path = combo.currentData() or combo.currentText()
        self._pending_save_data = {
            "pairs":        pairs,
            "color_map":    color_map,
            "corrected_df": corrected_df,
            "fitparams":    fitparams,
            "summary_df":   summary_df,
            "src_path":     src_path,
        }

        stem_preview = os.path.splitext(os.path.basename(src_path))[0]
        if self._preview_dlg is not None and self._preview_dlg.isVisible():
            self._preview_dlg.update_data(summary_df, color_map)
            self._preview_dlg.raise_()
            self._preview_dlg.activateWindow()
        else:
            self._preview_dlg = ResidualPreviewDialog(
                summary_df, stem=stem_preview,
                color_map=color_map, parent=None)
            self._preview_dlg.accepted.connect(self._do_save)
            self._preview_dlg.finished.connect(self._on_preview_closed)
            self._preview_dlg.anchor_clicked.connect(self._on_anchor_clicked)
            self._preview_dlg.show()
        self._set_apply_btn_state(False)

    # ── Live preview update ────────────────────────────────────────────────
    def _update_live_preview(self):
        if self._preview_dlg is None or not self._preview_dlg.isVisible():
            return
        if df_raw is None:
            return
        result = self._compute_recal()
        if result is None:
            return
        pairs, color_map, corrected_df, fitparams, summary_df = result
        src_path = combo.currentData() or combo.currentText()
        self._pending_save_data = {
            "pairs":        pairs,
            "color_map":    color_map,
            "corrected_df": corrected_df,
            "fitparams":    fitparams,
            "summary_df":   summary_df,
            "src_path":     src_path,
        }
        self._preview_dlg.update_data(summary_df, color_map)

    def _on_preview_closed(self, _result=None):
        self._preview_dlg = None
        self._set_apply_btn_state(True)

    def _on_anchor_clicked(self, row_idx):
        """Scroll to and briefly highlight the peak row that was clicked in the preview."""
        data = self._pending_save_data
        if data is None or row_idx >= len(data["pairs"]):
            return
        detect_idx = data["pairs"][row_idx][4]   # 5th element stored by _compute_recal
        if not (0 <= detect_idx < len(self._row_widgets)):
            return
        row_widget = self._row_widgets[detect_idx]
        if row_widget is None:
            return
        self._scroll.ensureWidgetVisible(row_widget)
        self.highlight_row(detect_idx)
        QtCore.QTimer.singleShot(1200, self.clear_row_highlight)
        self.raise_()
        self.activateWindow()

    # ── Save (called when user confirms in the preview dialog) ─────────────
    def _do_save(self):
        data = self._pending_save_data
        if data is None:
            return
        pairs        = data["pairs"]
        color_map    = data["color_map"]
        corrected_df = data["corrected_df"]
        fitparams    = data["fitparams"]
        summary_df   = data["summary_df"]
        src_path     = data["src_path"]

        # Determine output path
        if self.batch_mode and _batch_manual_state["output_folder"]:
            out_folder = _batch_manual_state["output_folder"]
            stem, ext  = os.path.splitext(os.path.basename(src_path))
            out_path   = os.path.join(out_folder, stem + "_manual_recalibrated" + (ext or ".txt"))
        else:
            stem, ext = os.path.splitext(src_path)
            default   = stem + "_manual_recalibrated" + (ext if ext else ".txt")
            out_path, _ = QtWidgets.QFileDialog.getSaveFileName(
                self, "Save Recalibrated Spectrum", default,
                "Text Files (*.txt *.csv *.tsv *.dat *.asc);;All Files (*)")
            if not out_path:
                return   # user cancelled save dialog

        # Build method parameter headers
        _manual_headers = ["#recalibration_method=manual"]
        if fitparams is not None:
            _deg = len(fitparams) - 1
            _manual_headers.append(f"#recal_polynomial_degree={_deg}")
            for _i, _c in enumerate(fitparams):
                _manual_headers.append(f"#recal_fitparam_a{_deg - _i}={_c:.10g}")
        else:
            _manual_headers.append("#recal_fallback=linear_scale")
        for _i, (_obs, _ref, *_) in enumerate(pairs):
            _manual_headers.append(
                f"#recal_pair_{_i+1}={_obs:.4f}->{_ref:.4f} (delta={_ref - _obs:+.4f})")

        try:
            save_spectrum_df(corrected_df, out_path,
                             src_path=src_path,
                             process_tag="manual_recalibrated",
                             extra_headers=_manual_headers)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Save Failed", f"Could not save:\n{out_path}\n\n{e}"); return

        # Save residuals
        _ts_host = self._parent if self._parent is not None else self
        if not hasattr(_ts_host, '_residuals_session_ts') or not _ts_host._residuals_session_ts:
            from datetime import datetime as _dt
            _ts_host._residuals_session_ts = _dt.now().strftime("%d.%m.%Y - %H.%M.%S")
        _session_ts = _ts_host._residuals_session_ts
        _res_save_folder = os.path.dirname(out_path)
        try:
            _save_residuals(_res_save_folder,
                            os.path.basename(out_path), summary_df,
                            ts=_session_ts, color_map=color_map)
        except Exception as e_res:
            QtWidgets.QMessageBox.warning(
                self, "Residuals",
                f"Spectrum saved, but residuals could not be written:\n{e_res}")

        if self._add_as_overlay:
            _add_processed_overlay(corrected_df, "Manual Recal", "_manual_recalibrated")
        _clear_manual_recal_scatter()

        if self.batch_mode:
            _batch_manual_state["done_count"] += 1
            _save_batch_manual_state()
            self.close()
            _advance_batch_manual(self._parent)
            if self._parent is not None:
                QtCore.QTimer.singleShot(50, lambda: (
                    self._parent.raise_(),
                    self._parent.activateWindow()
                ))
        else:
            self.close()
            if self._parent is not None:
                try:
                    self._parent.close()
                except Exception:
                    pass

    # ── Cancel ─────────────────────────────────────────────────────────────
    def _cancel(self):
        _clear_manual_recal_scatter()
        self.close()
        if self._parent is not None:
            QtCore.QTimer.singleShot(50, lambda: (
                self._parent.raise_(),
                self._parent.activateWindow()
            ))

    def _update_scatter_mz(self, idx, new_mz):
        if self._scatter is None or not hasattr(self._scatter, 'xData'):
            return
        x = np.array(self._scatter.xData)
        y = np.array(self._scatter.yData)
        if 0 <= idx < len(x):
            x[idx] = new_mz
            # Update height to new peak (still 10% above)
            if df is not None:
                near_idx = (df['mz'] - new_mz).abs().idxmin()
                new_int = df.loc[near_idx, 'intensity']
                y[idx] = new_int * 1.10
            self._scatter.setData(x=x, y=y)

    def _rebuild_scatter_from_selection(self):
        """Redraw red triangles only for currently included peaks."""
        if df is None:
            return
        mz_list = []
        int_list = []

        # self._detected holds (row_data, nominal, det_mz, det_int)
        for (row_data, nominal, det_mz, det_int), inc, spin in zip(
                self._detected, self._include_chks, self._mz_spins):
            if not inc.isChecked():
                continue
            # Use current spin value (in case user moved the peak)
            mz_list.append(spin.value())
            int_list.append(det_int)

        if not mz_list:
            # No included peaks -> clear triangles
            _clear_manual_recal_scatter()
        else:
            mz_arr = np.array(mz_list, dtype=float)
            int_arr = np.array(int_list, dtype=float)
            _draw_manual_recal_scatter(mz_arr, int_arr)

        self._update_live_preview()

    def highlight_row(self, idx):
        # reset all first
        for row in self._row_widgets:
            row.setStyleSheet("")
        if 0 <= idx < len(self._row_widgets):
            self._row_widgets[idx].setStyleSheet("background-color: rgb(255, 255, 150);")
    
    def clear_row_highlight(self):
        for row in self._row_widgets:
            row.setStyleSheet("")

    def _get_scatter(self):
        return self._scatter  # I already set this from ManualRecalWindow

    def _highlight_scatter_point(self, idx):
        scatter = self._get_scatter()
        if scatter is None:
            return
        spots = scatter.points()
        if 0 <= idx < len(spots):
            for i, spot in enumerate(spots):
                if i == idx:
                    spot.setBrush(pg.mkBrush(255, 255, 0, 180))
                    spot.setPen(pg.mkPen('#ffff00', width=2))
                else:
                    spot.resetBrush()
                    spot.resetPen()
    
    def _clear_scatter_highlight(self):
        scatter = self._get_scatter()
        if scatter is None:
            return
        for spot in scatter.points():
            spot.resetBrush()
            spot.resetPen()

    def highlight_row(self, row_idx):
        """Highlight row in yellow + draw yellow peak overlay"""
        self._highlighted_row_idx = row_idx
        
        # Highlight row widget
        for i, row_widget in enumerate(self._row_widgets):
            if i == row_idx:
                row_widget.setStyleSheet("""
                    background-color: #ffffaa; 
                    border: 2px solid #ffaa00; 
                    border-radius: 4px;
                    margin: 1px;
                """)
            else:
                row_widget.setStyleSheet("")  # Clear other highlights
        
        # Draw yellow peak overlay
        self._draw_peak_highlight(row_idx)
    
    def clear_row_highlight(self):
        """Clear all row highlights + remove peak overlay"""
        self._highlighted_row_idx = None
        
        # Clear row highlights
        for row_widget in self._row_widgets:
            row_widget.setStyleSheet("")
        
        # Remove peak overlay
        if self._peak_highlight_curve is not None:
            try:
                plot.removeItem(self._peak_highlight_curve)
            except:
                pass
            self._peak_highlight_curve = None


    def _find_real_peak(self, data_df, mz_centre, noise_floor, coarse_tol=0.4):
        """
        Return (mz, intensity) of the true local maximum nearest mz_centre,
        or None if no real peak is found.

        Uses scipy.signal.find_peaks so the result is a genuine local maximum,
        not just the argmax (which grabs tails of large neighbours or noise).

        prominence=noise_floor: the peak must rise above its surroundings by at
        least the noise floor — this is what rejects bumps on declining tails.
        """
        from scipy.signal import find_peaks as _fp

        mz_arr  = data_df['mz'].values
        int_arr = data_df['intensity'].values

        mask = (mz_arr >= mz_centre - coarse_tol) & (mz_arr <= mz_centre + coarse_tol)
        if mask.sum() < 3:
            return None

        idxs      = np.where(mask)[0]
        local_int = int_arr[idxs]

        peaks, _ = _fp(local_int, height=noise_floor, prominence=noise_floor)

        if len(peaks) > 0:
            best = peaks[local_int[peaks].argmax()]
        else:
            # No proper local maximum above noise in this window
            return None

        gi = idxs[best]
        return float(mz_arr[gi]), float(int_arr[gi])
    
    # def _draw_peak_highlight(self, row_idx):
    #     """Draw temporary yellow overlay around the selected peak"""
    #     if row_idx >= len(self._detected):
    #         return

    #     mz_spin = self._mz_spins[row_idx].value()

    #     if df is None or len(df) == 0:
    #         return

    #     # Use the same geometry function as highlight_peaks so the yellow overlay
    #     # always lands on exactly the same region as the colored highlight curve.
    #     geom = _get_highlight_geometry(df, [mz_spin])
    #     if not geom:
    #         return

    #     mz_arr, int_arr = geom[0][0], geom[0][1]

    #     if self._peak_highlight_curve is not None:
    #         try:
    #             plot.removeItem(self._peak_highlight_curve)
    #         except Exception:
    #             pass

    #     self._peak_highlight_curve = plot.plot(
    #         mz_arr, int_arr,
    #         pen=pg.mkPen('#ffff00', width=4),
    #         name="peak_highlight"
    #     )
    def _draw_peak_highlight(self, row_idx):
        if row_idx >= len(self._detected):
            return
        mz_detected = self._mz_spins[row_idx].value()
        if df is None or len(df) == 0:
            return

        mz_full  = df['mz'].values
        int_full = df['intensity'].values
        _thr     = mz_full >= 10.9
        noise_floor = (
          _estimate_noise_floor(int_full[_thr], n_sigma=3.0)
          if _thr.sum() >= 10 else _DYN_CLIP_FLOOR
        )

        result = self._find_real_peak(df, mz_detected, noise_floor, coarse_tol=0.4)
        if result is None:
            return
        real_mz, _ = result

        # Simple fixed slice around the confirmed peak — no valley detection needed
        tol  = get_tolerance(real_mz)
        mask = (mz_full >= real_mz - tol) & (mz_full <= real_mz + tol)
        mz_arr  = mz_full[mask]
        int_arr = int_full[mask]
        if len(mz_arr) < 2:
            return



    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Enter and hasattr(obj, "_peak_index"):
            self._highlight_scatter_point(obj._peak_index)
            self.highlight_row(obj._peak_index)
        elif event.type() == QtCore.QEvent.Type.Leave and hasattr(obj, "_peak_index"):
            self._clear_scatter_highlight()
            self.clear_row_highlight()
        return super().eventFilter(obj, event)


    def closeEvent(self, event):
        self.clear_row_highlight()
        self._clear_zoom_markers()
        if self._parent:
            self._parent._review_win = None
        global _active_peak_review_window
        if _active_peak_review_window == self:
            _active_peak_review_window = None
        super().closeEvent(event)



# ─────────────────────────────────────────────
#  Batch manual recalibration helpers
# ─────────────────────────────────────────────

# Module-level reference so the window isn't garbage-collected
_manual_recal_win_ref = None
_cluster_win_ref = None

def _advance_batch_manual(recal_win):
    """Load the next file in the batch into the main combo, update the banner."""
    files      = _batch_manual_state["files"]
    done_count = _batch_manual_state["done_count"]

    if done_count >= len(files):
        # Batch complete
        _batch_manual_state["active"] = False
        _save_batch_manual_state()
        QtWidgets.QMessageBox.information(
            main_win, "Batch Complete",
            f"All {_batch_manual_state['total']} files have been processed.\n"
            f"Outputs saved to:\n{_batch_manual_state['output_folder']}")
        if recal_win is not None:
            try:
                recal_win.close()
            except Exception:
                pass
        return

    next_path = files[done_count]
    if not os.path.exists(next_path):
        # Skip missing
        _batch_manual_state["done_count"] += 1
        _save_batch_manual_state()
        _advance_batch_manual(recal_win)
        return

    # Remove any processed overlays left from the previous file
    for ov in overlay_list[:]:
        if ov.get("is_processed"):
            overlay_list.remove(ov)
            try:
                overlay_rows_layout.removeWidget(ov["widget"])
                ov["widget"].deleteLater()
            except Exception:
                pass
    _invalidate_overlay_curves()

    # Load next file into main viewer
    _load_file_for_batch(next_path)

    # Update banner
    if recal_win is not None:
        recal_win._update_batch_banner()
        recal_win._populate_rows()


def _load_file_for_batch(path):
    """Load a file path into the main combo and trigger plot_file."""
    global all_txt_files, is_virtual, virtual_file_list
    if path not in all_txt_files:
        is_virtual = True
        virtual_file_list = list(all_txt_files) + [path]
        all_txt_files = list_all_txt_files()
        update_folder_label()
    files = get_txt_files(polarity_combo.currentText())
    _populate_main_combo(files if files else all_txt_files)
    idx = combo.findData(path)
    if idx < 0:
        idx = combo.findText(os.path.basename(path))
    if idx >= 0:
        combo.setCurrentIndex(idx)
    plot_file(combo.currentData() or path)


def show_manual_recalibrate():
    """Open the non-modal ManualRecalWindow for the current spectrum."""
    global _manual_recal_win_ref
    if df_raw is None:
        QtWidgets.QMessageBox.warning(
            main_win, "No spectrum", "Load a spectrum first."); return
    win = ManualRecalWindow(batch_mode=False)
    _manual_recal_win_ref = win
    win.setWindowFlags(win.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
    win.show()
    win.setWindowFlags(win.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint)
    win.show()
    win.raise_()
    win.activateWindow()
    QtWidgets.QApplication.setActiveWindow(win)
    QtCore.QTimer.singleShot(100, lambda: (win.raise_(), win.activateWindow(),
                                            QtWidgets.QApplication.setActiveWindow(win)))


def show_batch_manual_recalibrate():
    """
    Entry point for Batch Manual Recalibrate.
    Checks for an in-progress batch and offers to resume or start fresh.
    """
    global _manual_recal_win_ref

    # Check for resumable batch
    has_active = (
        _batch_manual_state.get("active", False) and
        bool(_batch_manual_state.get("files", [])) and
        _batch_manual_state["done_count"] < _batch_manual_state["total"]
    )

    if has_active:
        done  = _batch_manual_state["done_count"]
        total = _batch_manual_state["total"]
        reply = QtWidgets.QMessageBox.question(
            main_win, "Resume Batch?",
            f"A previous batch recalibration is in progress:\n"
            f"  {done} / {total} files done.\n\n"
            "Do you want to resume it?\n"
            "(Choose 'No' to start a new batch.)",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No |
            QtWidgets.QMessageBox.StandardButton.Cancel)

        if reply == QtWidgets.QMessageBox.StandardButton.Cancel:
            return

        if reply == QtWidgets.QMessageBox.StandardButton.Yes:
            # Resume: load next file and open window
            files = _batch_manual_state["files"]
            idx   = _batch_manual_state["done_count"]
            if 0 <= idx < len(files):
                _load_file_for_batch(files[idx])
            win = ManualRecalWindow(batch_mode=True)
            _manual_recal_win_ref = win
            win.setWindowFlags(win.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
            win.show()
            win.setWindowFlags(win.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint)
            win.show(); win.raise_(); win.activateWindow()
            QtWidgets.QApplication.setActiveWindow(win)
            QtCore.QTimer.singleShot(100, lambda: (win.raise_(), win.activateWindow()))
            return

    # ── Start new batch ────────────────────────────────────────────────────
    input_files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    if not input_files:
        input_files = all_txt_files
    if not input_files:
        QtWidgets.QMessageBox.warning(
            main_win, "No files",
            "No spectrum files are available in the current folder/list."); return

    out_folder = QtWidgets.QFileDialog.getExistingDirectory(
        main_win, "Select Output Folder for Recalibrated Files", base_dir or "")
    if not out_folder:
        return   # user cancelled

    _batch_manual_state["active"]        = True
    _batch_manual_state["files"]         = input_files
    _batch_manual_state["output_folder"] = out_folder
    _batch_manual_state["done_count"]    = 0
    _batch_manual_state["total"]         = len(input_files)
    _save_batch_manual_state()

    _load_file_for_batch(input_files[0])

    win = ManualRecalWindow(batch_mode=True)
    _manual_recal_win_ref = win
    win.setWindowFlags(win.windowFlags() | QtCore.Qt.WindowType.WindowStaysOnTopHint)
    win.show()
    win.setWindowFlags(win.windowFlags() & ~QtCore.Qt.WindowType.WindowStaysOnTopHint)
    win.show(); win.raise_(); win.activateWindow()
    QtWidgets.QApplication.setActiveWindow(win)
    QtCore.QTimer.singleShot(100, lambda: (win.raise_(), win.activateWindow()))


# Wire new actions
recal_action.triggered.connect(show_manual_recalibrate)
manual_recal_batch_action.triggered.connect(show_batch_manual_recalibrate)

def _open_peak_comparison():
    global _comparison_win_ref
    if _comparison_win_ref is not None:
        try:
            _comparison_win_ref.raise_()
            _comparison_win_ref.activateWindow()
            return
        except Exception:
            pass
    _comparison_win_ref = PeakComparisonWindow()
    _comparison_win_ref.show()
    _comparison_win_ref.raise_()
    _comparison_win_ref.activateWindow()

peak_comparison_action.triggered.connect(_open_peak_comparison)

# ─────────────────────────────────────────────
#  Processed overlay tracking
# ─────────────────────────────────────────────
_baseline_overlay = None
_recal_overlay    = None
_both_overlay     = None

def _remove_overlay_if_exists(ov_data_ref):
    if ov_data_ref is None: return
    if ov_data_ref in overlay_list:
        overlay_list.remove(ov_data_ref)
        try:
            overlay_rows_layout.removeWidget(ov_data_ref["widget"])
            ov_data_ref["widget"].deleteLater()
        except Exception:
            pass
    _invalidate_overlay_curves()

def _add_processed_overlay(processed_df, label, save_suffix):
    ov_data = add_overlay_row(label=label, show_save=True, df_override=processed_df)
    ov_data["toggle"].setChecked(True)
    ov_data["combo"].setEnabled(False)
    ov_data["polarity"].setEnabled(False)
    if "dt_combo" in ov_data and ov_data["dt_combo"] is not None:
        ov_data["dt_combo"].setEnabled(False)

    def do_save():
        src_path = combo.currentData() or ""
        stem, ext = os.path.splitext(src_path)
        default_path = stem + save_suffix + (ext if ext else ".txt")
        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            main_win, "Save Processed Spectrum", default_path,
            "Text Files (*.txt *.csv *.tsv *.dat *.asc);;All Files (*)")
        if save_path:
            save_spectrum_df(processed_df, save_path,
                             src_path=src_path or None,
                             process_tag=save_suffix.strip("_"))
            QtWidgets.QMessageBox.information(main_win, "Saved", f"Saved to:\n{save_path}")

    if ov_data["save_btn"] is not None:
        ov_data["save_btn"].clicked.connect(do_save)
    render_plot()
    return ov_data



def _ask_source_spectrum():
    """
    Pop a small dialog so the user can choose whether to operate on
    the main spectrum or one of the active overlays.
    Returns (data_df, label_for_overlay, source_name) or (None, None, None) on cancel.
    """
    choices = []
    if df_raw is not None:
        choices.append(("Main spectrum", df_raw, "main"))
    for i, ov in enumerate(overlay_list):
        if ov["toggle"].isChecked() and ov["df"] is not None:
            lbl = ov["toggle"].text() or f"Overlay {i+1}"
            choices.append((lbl, ov["df"], f"overlay_{i}"))

    if not choices:
        QtWidgets.QMessageBox.warning(main_win, "No spectrum", "No loaded spectrum found.")
        return None, None, None

    if len(choices) == 1:
        _, data, key = choices[0]
        return data, choices[0][0], key

    dlg = QtWidgets.QDialog(main_win)
    dlg.setWindowTitle("Select spectrum to process")
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(QtWidgets.QLabel("Apply to:"))
    combo_sel = QtWidgets.QComboBox()
    for lbl, _, _ in choices:
        combo_sel.addItem(lbl)
    lay.addWidget(combo_sel)
    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok |
        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    lay.addWidget(btns)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None, None, None
    _, data, key = choices[combo_sel.currentIndex()]
    return data, choices[combo_sel.currentIndex()][0], key




# ─────────────────────────────────────────────
#  Apply baseline → overlay
# ─────────────────────────────────────────────
def apply_baseline_to_current():
    global _baseline_overlay
    src_df, src_label, src_key = _ask_source_spectrum()
    if src_df is None:
        return
    progress = _make_progress_dialog("Baseline Correction", "Computing baseline…")

    def _do(raw_df, stop_flag=None):
        return apply_baseline(raw_df.copy(), stop_flag=stop_flag)

    worker = _Worker(_do, src_df)
    global _active_worker; _active_worker = worker

    def on_done(result):
        global _baseline_overlay
        progress.close()
        method = "airPLS" if airpls_action.isChecked() else "SNIP"
        _baseline_overlay = _add_processed_overlay(result, f"Baseline ({method})", "_baseline_corrected")

    def on_error(msg):
        progress.close()
        QtWidgets.QMessageBox.warning(main_win, "Baseline Error", msg)

    def on_cancelled():
        worker.stop(); progress.close()

    progress.canceled.connect(on_cancelled)
    worker.finished.connect(on_done)
    worker.error.connect(on_error)
    worker.start()

# ─────────────────────────────────────────────
#  AUTO-RECALIBRATION
# ─────────────────────────────────────────────
_IONS_NEG = {
    'integer mass': [17, 35, 43, 46, 53, 71, 89, 107, 125, 143, 161, 179, 197, 215],
    'exact mass':   [17.0033, 35.0139, 43.0189, 46.0060, 53.0244, 71.0350,
                     89.0455, 107.0561, 125.0667, 143.0772,
                     161.0878, 179.0984, 197.1089, 215.1195],
}
_IONS_POS = {
    'integer mass': [18, 19, 23, 30, 36, 37, 41, 43, 55, 59, 60,
                     73, 77, 91, 95, 109, 113, 127, 131, 145,
                     149, 163, 167, 181, 185, 199, 203, 217, 221, 235],
    'exact mass':   [18.0338, 19.0178, 22.9898, 30.0338, 36.0444, 37.0284,
                     41.0003, 43.0178, 55.0390, 59.0109, 60.0570,
                     73.0495, 77.0215, 91.0601, 95.0320, 109.0707,
                     113.0426, 127.0812, 131.0532, 145.0918,
                     149.0637, 163.1024, 167.0743, 181.1129,
                     185.0849, 199.1235, 203.0954, 217.1341, 221.1060, 235.1446],
}

def _ar_get_ions(ion_mode):
    return _IONS_POS if ion_mode == 'pos' else _IONS_NEG

def _ar_check_peak_validation(mass, max_diff):
    return abs(mass - round(mass)) <= max_diff

def _ar_get_local_percentile(data, peak_indices, prange, pct):
    n = data.shape[0]; out = []
    for idx in peak_indices:
        lo = max(0, idx - prange); hi = min(n, idx + prange + 1)
        out.append(float(np.percentile(data[lo:hi, 1], pct)))
    return np.array(out)

def _ar_automatic_threshold(data, local_perc, std_bot, std_top, std_factor):
    n = data.shape[0]; lo = min(std_bot, n-1); hi = min(std_top, n)
    return local_perc + std_factor * float(np.std(data[lo:hi, 1]))

def _ar_delete_false_peaks(to_delete, peaks, peak_indices, local_perc):
    to_delete = np.unique(to_delete).astype(int)
    mask = np.ones(len(peaks), dtype=bool)
    valid = to_delete[to_delete < len(peaks)]; mask[valid] = False
    return np.array([], dtype=int), peaks[mask], peak_indices[mask], local_perc[mask]

def _ar_calc_relative_intensities(peaks, local_perc):
    return peaks[:, 1] / np.maximum(local_perc, 1e-12)

_AR_MINIMUM_MASS=10.9; _AR_MAX_DATAPOINT=50000; _AR_MAX_MASSDIFF=0.5
_AR_PERCENTILE_RANGE=180; _AR_PERCENTILE=80; _AR_STD_FACTOR=0.2
_AR_STD_BOT=500; _AR_STD_TOP=2000; _AR_MASS_BIN_WIDTH=0.6; _AR_BINNING_ITERS=15
_AR_PW_MIN=1; _AR_PW_MAX=28; _AR_PW_STEP=4
_AR_REL_INT_THR=0.0006; _AR_REL_INT_ITERS=3; _AR_TIMESTEP=2e-3

def _ar_detect_peaks(data):
    import scipy.signal as ss
    chunk = data[:_AR_MAX_DATAPOINT, 1]
    widths = np.arange(_AR_PW_MIN, _AR_PW_MAX, _AR_PW_STEP)
    peak_indices = np.array(ss.find_peaks_cwt(chunk, widths), dtype=int)
    if peak_indices.size == 0:
        return np.empty((0, 2)), np.array([], dtype=int), np.array([])
    peaks      = np.column_stack([data[peak_indices, 0], data[peak_indices, 1]]).astype(float)
    local_perc = _ar_get_local_percentile(data, peak_indices, _AR_PERCENTILE_RANGE, _AR_PERCENTILE)
    dtd        = np.array([], dtype=int)
    for i in range(len(peak_indices)):
        if peaks[i, 0] < _AR_MINIMUM_MASS: dtd = np.append(dtd, i); continue
        if not _ar_check_peak_validation(peaks[i, 0], _AR_MAX_MASSDIFF):
            dtd = np.append(dtd, i); continue
        for _ in range(_AR_BINNING_ITERS):
            j = 0; new_idx = peak_indices[i]
            while (peak_indices[i] - j) >= 0 and \
                  data[peak_indices[i], 0] - data[peak_indices[i] - j, 0] < _AR_MASS_BIN_WIDTH:
                if peaks[i, 1] < data[peak_indices[i] - j, 1]:
                    peaks[i, 0] = data[peak_indices[i] - j, 0]
                    peaks[i, 1] = data[peak_indices[i] - j, 1]
                    new_idx = peak_indices[i] - j
                fwd = peak_indices[i] + j
                if fwd < data.shape[0] and peaks[i, 1] < data[fwd, 1]:
                    peaks[i, 0] = data[fwd, 0]; peaks[i, 1] = data[fwd, 1]; new_idx = fwd
                j += 1
            peak_indices[i] = new_idx
        if i > 0 and peak_indices[i] == peak_indices[i-1]: dtd = np.append(dtd, i); continue
        if i > 0 and peaks[i, 1] == peaks[i-1, 1] and peaks[i, 0] - peaks[i-1, 0] < 0.1:
            dtd = np.append(dtd, i); continue
        thr = _ar_automatic_threshold(data, local_perc[i], _AR_STD_BOT, _AR_STD_TOP, _AR_STD_FACTOR)
        if peaks[i, 1] < thr: dtd = np.append(dtd, i)
    dtd, peaks, peak_indices, local_perc = _ar_delete_false_peaks(dtd, peaks, peak_indices, local_perc)
    for _ in range(_AR_REL_INT_ITERS):
        if len(peaks) == 0: break
        rel = _ar_calc_relative_intensities(peaks, local_perc)
        bad = np.where(rel < _AR_REL_INT_THR)[0]
        dtd, peaks, peak_indices, local_perc = _ar_delete_false_peaks(bad, peaks, peak_indices, local_perc)
    return peaks, peak_indices, local_perc

def _ar_tof_m(t, params):
    return params[0]*t**2 + params[1]*t + params[2]

def _ar_lower_region(detected_peaks, ion_mode):
    ions = _ar_get_ions(ion_mode); int_m = ions['integer mass']; ex_m = ions['exact mass']
    out = []
    for row in detected_peaks[detected_peaks[:, 0] < 60]:
        for j, im in enumerate(int_m):
            if im == int(round(row[0])): r = row.copy(); r[3] = ex_m[j]; out.append(r); break
    if not out:
        for row in detected_peaks[detected_peaks[:, 0] < 60]:
            r = row.copy(); r[3] = round(row[0]); out.append(r)
    return np.array(out) if out else np.empty((0, 4))

def _ar_higher_region(detected_peaks, prev, ion_mode, rng):
    lo, hi = rng
    region = detected_peaks[(detected_peaks[:, 0] > lo) & (detected_peaks[:, 0] < hi)].copy()
    if region.size == 0: return prev
    ions = _ar_get_ions(ion_mode); int_m = ions['integer mass']; ex_m = ions['exact mass']
    peak_diff = float(prev[-1, 3] - prev[-1, 0]) if prev.size > 0 else 0.0
    corrected = []
    for i in range(len(region)):
        for j, im in enumerate(int_m):
            if im == int(round(region[i, 0] + peak_diff)):
                region[i, 3] = ex_m[j]; corrected.append(region[i]); break
        if region[i, 3] == 0:
            region[i, 3] = (np.floor(region[i, 0])
                            if region[i, 0] + peak_diff - np.floor(region[i, 0]) < 0.5
                            else np.ceil(region[i, 0]))
        peak_diff = region[i, 3] - region[i, 0]
    if not corrected: corrected.append(region[np.argmax(region[:, 1])])
    new_rows = np.array(corrected)
    return new_rows if prev.size == 0 else np.vstack([prev, new_rows])

# Auto-recalibration algorithm adapted from LILBID_GUI by M. Umair
# https://github.com/mumair5393/LILBID_GUI/blob/main/automated_recalibration.py
def _run_auto_recal_worker(data_np, filename, stop_flag=None):
    ion_mode = 'pos' if '_pos_' in os.path.basename(filename) else 'neg'
    if stop_flag is not None and stop_flag._stop_requested: raise InterruptedError
    peaks, peak_indices, local_perc = _ar_detect_peaks(data_np)
    if len(peaks) == 0:
        raise ValueError("No peaks detected - cannot auto-recalibrate.")
    if stop_flag is not None and stop_flag._stop_requested: raise InterruptedError
    detected = np.hstack([peaks, local_perc.reshape(-1, 1), np.zeros((len(peaks), 1))])
    cal = _ar_lower_region(detected, ion_mode)
    cal = _ar_higher_region(detected, cal, ion_mode, (60, 120))
    cal = _ar_higher_region(detected, cal, ion_mode, (120, 190))
    cal = _ar_higher_region(detected, cal, ion_mode, (190, 237))
    if len(cal) < 2:
        raise ValueError(f"Only {len(cal)} calibration peak(s) - need ≥ 2.")
    check_manually = len(cal) < 3
    if not check_manually:
        last = cal[-1]; prev2 = cal[-2]
        if 1.5 * abs(last[0]-last[3]) < abs(prev2[0]-prev2[3]): check_manually = True
    summary = pd.DataFrame({
        'original m/z': np.round(cal[:, 0], 4),
        'corrected to':  np.round(cal[:, 3], 4),
        'Δ m/z':         np.round(cal[:, 3] - cal[:, 0], 4),
    })
    cal_tof = cal.copy()
    for j in range(len(cal_tof)):
        for t in range(data_np.shape[0]):
            if data_np[t, 0] > cal_tof[j, 0]:
                cal_tof[j, 0] = (t-1)*_AR_TIMESTEP; break
    fitparams = np.polyfit(cal_tof[:, 0], cal_tof[:, 3], 2)
    recal = data_np.copy()
    for j in range(len(recal)):
        recal[j, 0] = _ar_tof_m(j*_AR_TIMESTEP, fitparams)
    return recal, check_manually, summary, fitparams

def _show_auto_recal_review_dialog(check_manually, summary, fitparams):
    dlg = QtWidgets.QDialog(main_win)
    dlg.setWindowTitle("Auto-Recalibration - Review"); dlg.resize(520, 400)
    layout = QtWidgets.QVBoxLayout(dlg)
    if check_manually:
        warn = QtWidgets.QLabel(
            "⚠  The algorithm flagged this result for manual review.\n"
            "    The calibration may be unreliable - please check the table below.")
        warn.setStyleSheet("color: #c8600a; font-weight: bold;"); warn.setWordWrap(True)
        layout.addWidget(warn)
    fit_lbl = QtWidgets.QLabel(
        f"Quadratic fit:  m(t) = {fitparams[0]:.6g}·t²  +  "
        f"{fitparams[1]:.6g}·t  +  {fitparams[2]:.6g}")
    fit_lbl.setStyleSheet("font-family: monospace;"); layout.addWidget(fit_lbl)
    table = QtWidgets.QTableWidget(len(summary), 3)
    table.setHorizontalHeaderLabels(["Original m/z", "Corrected to", "Δ m/z"])
    table.horizontalHeader().setStretchLastSection(True)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
    for row_i, (_, row) in enumerate(summary.iterrows()):
        for col_i, val in enumerate(row):
            item = QtWidgets.QTableWidgetItem(str(val))
            item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            table.setItem(row_i, col_i, item)
    layout.addWidget(table)
    note = QtWidgets.QLabel("Click Apply to add the recalibrated spectrum as an overlay.")
    note.setWordWrap(True); layout.addWidget(note)
    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Apply |
        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    btns.button(QtWidgets.QDialogButtonBox.StandardButton.Apply).clicked.connect(dlg.accept)
    btns.rejected.connect(dlg.reject); layout.addWidget(btns)
    return dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted

def show_auto_recalibrate():
    global _active_worker, _recal_overlay
    src_df, src_label, src_key = _ask_source_spectrum()
    if src_df is None:
        return
    data_np  = src_df[['mz', 'intensity']].to_numpy().copy()
    filename = combo.currentData() or combo.currentText()
    progress = _make_progress_dialog("Auto-Recalibrating",
                                     "Detecting peaks and fitting calibration polynomial…")
    def _wf(d, fn, stop_flag=None): return _run_auto_recal_worker(d, fn, stop_flag=stop_flag)
    worker = _Worker(_wf, data_np, filename); _active_worker = worker

    def on_done(result):
        global _recal_overlay
        progress.close()
        recal_np, check_manually, summary, fitparams = result
        if not _show_auto_recal_review_dialog(check_manually, summary, fitparams): return
        new_df = pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})
        _recal_overlay = _add_processed_overlay(new_df, "Recalibrated", "_recalibrated")
        src_path = combo.currentData() or ""
        if src_path and os.path.exists(os.path.dirname(str(src_path))):
            try:
                _save_residuals(
                    os.path.dirname(str(src_path)),
                    os.path.basename(str(src_path)),
                    summary,
                    orig_mz=data_np[:, 0],       # original m/z array
                    recal_mz=recal_np[:, 0])      # recalibrated m/z array
            except Exception as e_res:
                QtWidgets.QMessageBox.warning(
                    main_win, "Residuals",
                    f"Recalibration saved, but residuals could not be written:\n{e_res}")

    def on_error(msg):
        progress.close()
        QtWidgets.QMessageBox.warning(main_win, "Auto-Recalibration Failed", msg)

    def on_cancelled(): worker.stop(); progress.close()

    progress.canceled.connect(on_cancelled); worker.finished.connect(on_done)
    worker.error.connect(on_error); worker.start()


def apply_both_to_current():
    global _active_worker, _both_overlay
    src_df, src_label, src_key = _ask_source_spectrum()
    if src_df is None:
        return
    filename = combo.currentData() or combo.currentText()
    progress = _make_progress_dialog("Baseline + Recalibration",
                                     "Applying baseline then auto-recalibrating…")

    def _wf(raw_df, fn, stop_flag=None):
        bl = apply_baseline(raw_df.copy(), stop_flag=stop_flag)
        data_np = bl[['mz', 'intensity']].to_numpy().copy()
        recal_np, check_manually, summary, fitparams = _run_auto_recal_worker(data_np, fn, stop_flag=stop_flag)
        new_df = pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})
        return new_df, check_manually, summary, fitparams, data_np[:, 0], recal_np[:, 0]

    worker = _Worker(_wf, src_df, filename); _active_worker = worker

    def on_done(result):
        global _both_overlay
        progress.close()
        new_df, check_manually, summary, fitparams, orig_mz, recal_mz = result
        if not _show_auto_recal_review_dialog(check_manually, summary, fitparams): return
        _both_overlay = _add_processed_overlay(new_df, "Baseline+Recal", "_baseline+recalibrated")
        src_path = combo.currentData() or ""
        if src_path and os.path.exists(os.path.dirname(str(src_path))):
            try:
                _save_residuals(os.path.dirname(str(src_path)),
                                os.path.basename(str(src_path)), summary,
                                orig_mz=orig_mz, recal_mz=recal_mz)
            except Exception as e_res:
                QtWidgets.QMessageBox.warning(
                    main_win, "Residuals",
                    f"Processing saved, but residuals could not be written:\n{e_res}")

    def on_error(msg):
        progress.close()
        QtWidgets.QMessageBox.warning(main_win, "Processing Failed", msg)

    def on_cancelled(): worker.stop(); progress.close()

    progress.canceled.connect(on_cancelled); worker.finished.connect(on_done)
    worker.error.connect(on_error); worker.start()

# ─────────────────────────────────────────────
#  Recal transform helpers (used by baseline toggle + manual recal factor)
# ─────────────────────────────────────────────
def _compute_transforms(raw_df, factor, do_baseline, stop_flag=None):
    tmp = raw_df.copy(); tmp['mz'] = tmp['mz'] * factor
    if do_baseline: tmp = apply_baseline(tmp, stop_flag=stop_flag)
    return tmp

def reapply_transforms():
    global df, _active_worker
    if df_raw is None: return
    progress = _make_progress_dialog(
        "Processing",
        "Applying baseline correction…" if baseline_action.isChecked()
        else "Recalibrating spectrum…")
    worker = _Worker(_compute_transforms, df_raw, recal_factor, baseline_action.isChecked())
    _active_worker = worker

    def on_done(result):
        global df
        df = result
        _clear_all_caches()
        progress.close()
        render_plot()

    def on_error(msg):
        progress.close()
        QtWidgets.QMessageBox.warning(main_win, "Processing Error", msg)

    def on_cancelled():
        worker.stop(); progress.close()

    progress.canceled.connect(on_cancelled)
    worker.finished.connect(on_done)
    worker.error.connect(on_error)
    worker.start()

# ─────────────────────────────────────────────
#  Batch processing (auto)
# ─────────────────────────────────────────────
import concurrent.futures
import multiprocessing

# ─────────────────────────────────────────────
#  Batch processing - parallel worker (module-level so it's picklable)
# ─────────────────────────────────────────────
def _batch_process_file(args):
    """Top-level function (required for multiprocessing pickling)."""
    path, save_folder, suffix, sep, do_baseline, do_recal, airpls, extra_headers = args
    try:
        # Re-import inside worker process
        import pandas as pd
        import numpy as np
        from scipy.sparse import csc_matrix, eye, diags
        from scipy.sparse.linalg import spsolve

        # Read original header comments before detecting separator
        orig_headers = []
        with open(path, 'r', errors='replace') as fh:
            for line in fh:
                if line.startswith('#'):
                    orig_headers.append(line.rstrip('\n'))
                else:
                    break

        # Inline read
        import csv as _csv
        candidates = ['\t', ',', ';', ' ']
        if sep is None:
            with open(path, 'r', errors='replace') as fh:
                sample = [fh.readline() for _ in range(20)]
            scores = {c: 0 for c in candidates}
            for line in sample:
                if line.startswith('#'): continue
                for c in candidates:
                    parts = line.split(c)
                    if len(parts) >= 2: scores[c] += len(parts)
            sep = max(scores, key=scores.get)

        if sep == ' ':
            df = pd.read_csv(path, comment='#', sep=r'\s+', header=None,
                             engine='python', names=['mz', 'intensity'])
        else:
            df = pd.read_csv(path, comment='#', sep=sep, header=None,
                             names=['mz', 'intensity'])
        df = df.dropna()
        df['mz']        = pd.to_numeric(df['mz'],        errors='raise')
        df['intensity'] = pd.to_numeric(df['intensity'], errors='raise')

        if do_baseline:
            # Inline airPLS
            def _whittaker(x, w, lam, diff=1):
                m = len(x)
                E = eye(m, format='csc'); D = E[1:] - E[:-1]
                W = diags(w, 0, shape=(m, m))
                A = csc_matrix(W + (lam * D.T * D))
                B = csc_matrix(W * np.matrix(x).T)
                return np.array(spsolve(A, B))

            if airpls:
                y = df['intensity'].values.astype(float)
                m = len(y); w = np.ones(m)
                for i in range(1, 20):
                    z = _whittaker(y, w, 100)
                    d = y - z; dssn = np.abs(d[d < 0].sum())
                    if dssn < 0.001 * np.abs(y).sum(): break
                    w[d >= 0] = 0
                    w[d < 0]  = np.exp(i * np.abs(d[d < 0]) / dssn)
                    w[0] = np.exp(i * d[d < 0].max() / dssn); w[-1] = w[0]
                corrected = y - z
                corrected[corrected < 0] = 0.000001
                corrected = corrected + 0.007
            else:
                # SNIP
                y = np.clip(df['intensity'].values.astype(float), 0, None)
                p = np.sqrt(np.sqrt(y + 1)); n = len(p)
                for hw in range(1, 41):
                    left  = np.roll(p,  hw); left[:hw]   = p[:hw]
                    right = np.roll(p, -hw); right[-hw:]  = p[-hw:]
                    p = np.minimum(p, (left + right) / 2.0)
                baseline = np.clip((p ** 2) ** 2 - 1, 0, None)
                corrected = np.clip(y - baseline, 0, None)
            df['intensity'] = corrected

        if do_recal:
            import scipy.signal as ss
            data_np = df[['mz', 'intensity']].to_numpy().copy()
            ion_mode = 'pos' if '_pos_' in os.path.basename(path) else 'neg'

            # Minimal inline auto-recal (same logic as _run_auto_recal_worker)
            _IONS = {
                'neg': {
                    'integer': [17, 35, 43, 46, 53, 71, 89, 107, 125, 143,
                                161, 179, 197, 215],
                    'exact':   [17.0033, 35.0139, 43.0189, 46.0060, 53.0244,
                                71.0350, 89.0455, 107.0561, 125.0667, 143.0772,
                                161.0878, 179.0984, 197.1089, 215.1195],
                },
                'pos': {
                    'integer': [18, 19, 23, 30, 36, 37, 41, 43, 55, 59, 60,
                                73, 77, 91, 95, 109, 113, 127, 131, 145,
                                149, 163, 167, 181, 185, 199, 203, 217, 221, 235],
                    'exact':   [18.0338, 19.0178, 22.9898, 30.0338, 36.0444,
                                37.0284, 41.0003, 43.0178, 55.0390, 59.0109,
                                60.0570, 73.0495, 77.0215, 91.0601, 95.0320,
                                109.0707, 113.0426, 127.0812, 131.0532, 145.0918,
                                149.0637, 163.1024, 167.0743, 181.1129, 185.0849,
                                199.1235, 203.0954, 217.1341, 221.1060, 235.1446],
                },
            }
            ions = _IONS[ion_mode]
            MAX_DP = 50000; TS = 2e-3
            chunk = data_np[:MAX_DP, 1]
            widths = np.arange(1, 28, 4)
            peak_indices = np.array(ss.find_peaks_cwt(chunk, widths), dtype=int)
            if len(peak_indices) == 0:
                raise ValueError("No peaks detected for recalibration.")
            peaks = np.column_stack([data_np[peak_indices, 0],
                                     data_np[peak_indices, 1]]).astype(float)
            detected = np.hstack([peaks,
                                  np.zeros((len(peaks), 1)),
                                  np.zeros((len(peaks), 1))])

            def _lower(det):
                out = []
                for row in det[det[:, 0] < 60]:
                    for j, im in enumerate(ions['integer']):
                        if im == int(round(row[0])):
                            r = row.copy(); r[3] = ions['exact'][j]; out.append(r); break
                return np.array(out) if out else np.empty((0, 4))

            def _higher(det, prev, rng):
                lo, hi = rng
                region = det[(det[:, 0] > lo) & (det[:, 0] < hi)].copy()
                if region.size == 0: return prev
                pd_val = float(prev[-1, 3] - prev[-1, 0]) if prev.size > 0 else 0.0
                corrected = []
                for i in range(len(region)):
                    for j, im in enumerate(ions['integer']):
                        if im == int(round(region[i, 0] + pd_val)):
                            region[i, 3] = ions['exact'][j]; corrected.append(region[i]); break
                    if region[i, 3] == 0:
                        region[i, 3] = (np.floor(region[i, 0])
                                        if region[i, 0] + pd_val - np.floor(region[i, 0]) < 0.5
                                        else np.ceil(region[i, 0]))
                    pd_val = region[i, 3] - region[i, 0]
                if not corrected: corrected.append(region[np.argmax(region[:, 1])])
                new_rows = np.array(corrected)
                return new_rows if prev.size == 0 else np.vstack([prev, new_rows])

            cal = _lower(detected)
            cal = _higher(detected, cal, (60, 120))
            cal = _higher(detected, cal, (120, 190))
            cal = _higher(detected, cal, (190, 237))
            if len(cal) < 2:
                raise ValueError(f"Only {len(cal)} calibration peak(s).")

            cal_tof = cal.copy()
            for j in range(len(cal_tof)):
                for t in range(data_np.shape[0]):
                    if data_np[t, 0] > cal_tof[j, 0]:
                        cal_tof[j, 0] = (t - 1) * TS; break
            recal_fitparams = np.polyfit(cal_tof[:, 0], cal_tof[:, 3], 2)
            recal = data_np.copy()
            for j in range(len(recal)):
                recal[j, 0] = np.polyval(recal_fitparams, j * TS)
            df = pd.DataFrame({'mz': recal[:, 0], 'intensity': recal[:, 1]})
            recal_headers = [
                "#recalibration_method=auto",
                "#recal_polynomial_degree=2",
                f"#recal_fitparam_a2={recal_fitparams[0]:.10g}",
                f"#recal_fitparam_a1={recal_fitparams[1]:.10g}",
                f"#recal_fitparam_a0={recal_fitparams[2]:.10g}",
            ]
            for j in range(len(cal)):
                recal_headers.append(
                    f"#recal_pair_{j+1}={cal[j,0]:.4f}->{cal[j,3]:.4f}"
                    f" (delta={cal[j,3]-cal[j,0]:+.4f})")
            extra_headers = (extra_headers or []) + recal_headers

        stem, ext = os.path.splitext(os.path.basename(path))
        out_path = os.path.join(save_folder, stem + suffix + (ext or ".txt"))
        process_tag = suffix.strip("_")
        with open(out_path, 'w', encoding='utf-8') as fh:
            fh.write(f"#processed={process_tag}\n")
            if extra_headers:
                for h in extra_headers:
                    fh.write(h + "\n")
            for hline in orig_headers:
                fh.write(hline + "\n")
            fh.write("##########\n")
            for row in df.itertuples(index=False):
                fh.write(f"{row.mz}\t{row.intensity}\n")
        return None  # success

    except Exception as e:
        return f"{os.path.basename(path)}: {e}"

def _detect_file_sep(path):
    """Return the column separator used in a spectrum file."""
    try:
        with open(path, 'r', errors='replace') as fh:
            lines = [fh.readline() for _ in range(20)]
        return detect_separator(lines)
    except Exception:
        return "\t"


def _write_tof_transformed(data_df, out_path, src_path, sep,
                           unit, ref_times, ref_masses, a, b, r2):
    """
    Write a ToF→mass-transformed DataFrame to *out_path*, preserving the
    original file's header comments and appending a transformation-info block.

    Layout:
        #processed=tof_mass_transformed
        [original # header lines]
        ##########
        #tof2mass_unit=µs
        #tof2mass_ref_times=10.234,25.678
        #tof2mass_ref_masses=18.0000,100.0000
        #tof2mass_a=12.345678
        #tof2mass_b=-1.234567
        #tof2mass_r2=0.99987
        ##########
        [data rows separated by *sep*]
    """
    ref_t_str = ";".join(f"{t:.6g}" for t in ref_times)
    ref_m_str = ";".join(f"{m:.6g}" for m in ref_masses)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write("#processed=tof_mass_transformed\n")
        if src_path and os.path.isfile(str(src_path)):
            for hline in read_spectrum_headers(str(src_path)):
                fh.write(hline + "\n")
        fh.write("##########\n")
        fh.write(f"#tof2mass_unit={unit}\n")
        fh.write(f"#tof2mass_ref_times={ref_t_str}\n")
        fh.write(f"#tof2mass_ref_masses={ref_m_str}\n")
        fh.write(f"#tof2mass_a={a:.10g}\n")
        fh.write(f"#tof2mass_b={b:.10g}\n")
        fh.write(f"#tof2mass_r2={r2:.8f}\n")
        fh.write("##########\n")
        for row in data_df.itertuples(index=False):
            fh.write(f"{row.mz}{sep}{row.intensity}\n")


class TofToMassWindow(QtWidgets.QWidget, StayOnTopMixin):
    """
    Interactive ToF → Mass converter.

    The spectrum file is assumed to have *time* values on its X axis
    (stored in the 'mz' column by the reader).  The user supplies
    reference (time, mass) pairs; the tool fits  t = a·√m + b  by
    exact solution (2 pairs) or least-squares (≥ 3 pairs) and then
    rewrites the X axis in Da.

    Red inverted triangles mark the detected spectrum peak nearest to
    each reference time, with a nudge slider identical to the one used
    in the Peak Review window.
    """

    _UNIT_TO_US = {"s": 1e6, "ms": 1e3, "µs": 1.0, "ns": 1e-3}

    def __init__(self):
        super().__init__(None, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("ToF → Mass Converter")
        self.resize(640, 560)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        self._pair_rows  = []   # list of row dicts (see _add_pair_row)
        self._a = None
        self._b = None
        self._r2 = None

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        # ── Time unit ──────────────────────────────────────────────
        unit_row = QtWidgets.QHBoxLayout()
        unit_row.addWidget(QtWidgets.QLabel("Time unit:"))
        self._unit_combo = QtWidgets.QComboBox()
        self._unit_combo.addItems(["s", "ms", "µs", "ns"])
        saved_unit = settings.value("tof2mass/time_unit", "µs")
        idx = self._unit_combo.findText(saved_unit)
        if idx >= 0:
            self._unit_combo.setCurrentIndex(idx)
        unit_row.addWidget(self._unit_combo)
        unit_row.addStretch()
        root.addLayout(unit_row)
        self._unit_combo.currentTextChanged.connect(self._on_unit_changed)

        # ── Column headers ─────────────────────────────────────────
        hdr = QtWidgets.QHBoxLayout()
        lbl_time = QtWidgets.QLabel("Reference time")
        lbl_time.setToolTip("Time value at which this peak appears in the spectrum")
        lbl_nudge = QtWidgets.QLabel("Nudge")
        lbl_nudge.setToolTip("Slide to shift the reference time by ±2 units")
        lbl_mass = QtWidgets.QLabel("Known mass (Da)")
        lbl_mass.setToolTip("Theoretical / known mass of this peak")
        for lbl in (lbl_time, lbl_nudge, lbl_mass):
            hdr.addWidget(lbl)
            hdr.addStretch()
        root.addLayout(hdr)

        # ── Scrollable pair list ───────────────────────────────────
        self._pairs_layout = QtWidgets.QVBoxLayout()
        self._pairs_layout.setSpacing(4)
        pairs_container = QtWidgets.QWidget()
        pairs_container.setLayout(self._pairs_layout)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(pairs_container)
        scroll.setMinimumHeight(160)
        scroll.setMaximumHeight(300)
        root.addWidget(scroll)

        # Start with two empty rows
        self._add_pair_row()
        self._add_pair_row()

        add_btn = QtWidgets.QPushButton("+ Add reference pair")
        add_btn.clicked.connect(self._add_pair_row)
        root.addWidget(add_btn)

        menu_bar = QtWidgets.QMenuBar(self)
        file_menu = menu_bar.addMenu("File")
        import_act = QtWidgets.QAction("Import reference…", self)
        import_act.triggered.connect(self._import_reference)
        export_act = QtWidgets.QAction("Export reference…", self)
        export_act.triggered.connect(self._export_reference)
        file_menu.addAction(import_act)
        file_menu.addAction(export_act)
        self._install_stay_on_top(menu_bar, settings)
        root.setMenuBar(menu_bar)

        root.addWidget(_hsep())

        # ── Solve ──────────────────────────────────────────────────
        solve_btn = QtWidgets.QPushButton("Fit  a  and  b  from reference pairs")
        solve_btn.setToolTip(
            "Solves  t = a·√m + b  using the reference pairs above.\n"
            "2 pairs → exact solution; ≥3 pairs → least-squares fit.")
        solve_btn.clicked.connect(self._solve)
        root.addWidget(solve_btn)

        ab_row = QtWidgets.QFormLayout()
        self._a_spin = QtWidgets.QDoubleSpinBox()
        self._a_spin.setRange(-1e9, 1e9)
        self._a_spin.setDecimals(8)
        self._a_spin.setSingleStep(0.0001)
        self._a_spin.setToolTip("Coefficient a (auto-filled by fit; editable)")
        self._b_spin = QtWidgets.QDoubleSpinBox()
        self._b_spin.setRange(-1e9, 1e9)
        self._b_spin.setDecimals(8)
        self._b_spin.setSingleStep(0.0001)
        self._b_spin.setToolTip("Offset b (auto-filled by fit; editable)")
        self._r2_lbl = QtWidgets.QLabel("—")
        self._r2_lbl.setToolTip("Coefficient of determination of the fit")
        ab_row.addRow("a =", self._a_spin)
        ab_row.addRow("b =", self._b_spin)
        ab_row.addRow("R² =", self._r2_lbl)
        root.addLayout(ab_row)

        # Keep _a/_b in sync with spin boxes so manual edits are used on Apply
        self._a_spin.valueChanged.connect(lambda v: setattr(self, '_a', v))
        self._b_spin.valueChanged.connect(lambda v: setattr(self, '_b', v))

        root.addWidget(_hsep())

        # ── Output mode ────────────────────────────────────────────
        mode_grp = QtWidgets.QButtonGroup(self)
        self._rb_current = QtWidgets.QRadioButton(
            "Transform current file (uses reference pairs for fit)")
        self._rb_batch   = QtWidgets.QRadioButton(
            "Batch transform folder… (uses a and b values above for all files)")
        self._rb_current.setChecked(True)
        mode_grp.addButton(self._rb_current)
        mode_grp.addButton(self._rb_batch)
        root.addWidget(self._rb_current)
        root.addWidget(self._rb_batch)

        # ── Output folder ──────────────────────────────────────────
        in_folder_row = QtWidgets.QHBoxLayout()
        in_folder_row.addWidget(QtWidgets.QLabel("Input folder:"))
        self._in_folder_edit = QtWidgets.QLineEdit()
        self._in_folder_edit.setPlaceholderText("Select folder containing ToF files…")
        self._in_folder_edit.setText(settings.value("tof2mass/input_folder", ""))
        in_browse_btn = QtWidgets.QPushButton("Browse…")
        in_browse_btn.clicked.connect(self._browse_input_folder)
        in_folder_row.addWidget(self._in_folder_edit, 1)
        in_folder_row.addWidget(in_browse_btn)
        root.addLayout(in_folder_row)

        folder_row = QtWidgets.QHBoxLayout()
        folder_row.addWidget(QtWidgets.QLabel("Output folder:"))
        self._folder_edit = QtWidgets.QLineEdit()
        self._folder_edit.setPlaceholderText("Select output folder…")
        self._folder_edit.setText(settings.value("tof2mass/output_folder", ""))
        browse_btn = QtWidgets.QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(browse_btn)
        root.addLayout(folder_row)

        # ── Apply ──────────────────────────────────────────────────
        apply_btn = QtWidgets.QPushButton("Apply Transformation")
        apply_btn.setToolTip(
            "Writes the transformed spectrum to the output folder.\n"
            "Filename: <original>_mass_transformed.<ext>")
        apply_btn.clicked.connect(self._apply)
        root.addWidget(apply_btn)

    def _browse_input_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Input Folder",
            self._in_folder_edit.text() or settings.value("tof2mass/input_folder", ""))
        if folder:
            self._in_folder_edit.setText(folder)
            settings.setValue("tof2mass/input_folder", folder)

    # ── Pair row management ────────────────────────────────────────

    def _add_pair_row(self):
        row_w = QtWidgets.QWidget()
        row_l = QtWidgets.QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)

        unit = self._unit_combo.currentText()

        time_spin = QtWidgets.QDoubleSpinBox()
        time_spin.setRange(0.0, 1e12)
        time_spin.setDecimals(6)
        time_spin.setSingleStep(0.01)
        time_spin.setFixedWidth(120)
        time_spin.setToolTip(f"Reference time in {unit}")

        # Slider: ±2000 steps × 0.001 = ±2 display-units nudge
        slide = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slide.setRange(-2000, 2000)
        slide.setValue(0)
        slide.setFixedWidth(100)
        slide.setToolTip(
            "Nudge the reference time by up to ±2 time units.\n"
            "Resets to centre when you type a new value in the spin box.")

        mass_spin = QtWidgets.QDoubleSpinBox()
        mass_spin.setRange(0.0, 1e7)
        mass_spin.setDecimals(4)
        mass_spin.setSingleStep(1.0)
        mass_spin.setFixedWidth(120)
        mass_spin.setToolTip("Theoretical/known mass in Da")

        rm_btn = QtWidgets.QPushButton("✕")
        rm_btn.setFixedSize(24, 24)
        rm_btn.setToolTip("Remove this reference pair")

        row_dict = {
            "widget":    row_w,
            "time_spin": time_spin,
            "slide":     slide,
            "mass_spin": mass_spin,
            "_t_base":   [0.0],   # slider anchor (mutable so closures can share it)
            "scatter":   None,    # pg.ScatterPlotItem on main plot
        }
        self._pair_rows.append(row_dict)

        # ── Slider / spin interaction (mirrors PeakReviewWindow pattern) ──
        def _make_handlers(row):
            def _on_slide(val):
                row["time_spin"].blockSignals(True)
                row["time_spin"].setValue(row["_t_base"][0] + val * 0.001)
                row["time_spin"].blockSignals(False)
                self._update_triangle(row)

            def _on_spin(v):
                row["_t_base"][0] = v
                row["slide"].blockSignals(True)
                row["slide"].setValue(0)
                row["slide"].blockSignals(False)
                self._update_triangle(row)

            return _on_slide, _on_spin

        on_slide, on_spin = _make_handlers(row_dict)
        slide.valueChanged.connect(on_slide)
        time_spin.valueChanged.connect(on_spin)
        mass_spin.valueChanged.connect(lambda _v, r=row_dict: self._update_triangle(r))

        rm_btn.clicked.connect(lambda _c=False, r=row_dict: self._remove_pair_row(r))

        row_l.addWidget(time_spin)
        row_l.addWidget(slide)
        row_l.addWidget(QtWidgets.QLabel("→"))
        row_l.addWidget(mass_spin)
        row_l.addWidget(rm_btn)

        self._pairs_layout.addWidget(row_w)

    def _remove_pair_row(self, row_dict):
        if len(self._pair_rows) <= 2:
            QtWidgets.QMessageBox.information(
                self, "Minimum rows", "At least 2 reference pairs are required.")
            return
        if row_dict.get("scatter") is not None:
            try:
                plot.removeItem(row_dict["scatter"])
            except Exception:
                pass
        row_dict["widget"].deleteLater()
        self._pair_rows.remove(row_dict)

    # ── Triangle markers on the main plot ─────────────────────────

    def _update_triangle(self, row_dict):
        """Place/refresh the red inverted triangle for one reference pair."""
        if row_dict.get("scatter") is not None:
          try:
              plot.removeItem(row_dict["scatter"])
          except Exception:
              pass
          row_dict["scatter"] = None

        t_val = row_dict["time_spin"].value()
        if t_val <= 0:
          return

        # ── Get Y value from the DISPLAYED curve (same scale as the axis) ──
        # _main_curve is the global PlotDataItem; its getData() returns the
        # intensity that was actually passed to the plot (normalized if dyn is
        # on, raw otherwise).  Using its Y avoids mismatches between df (raw)
        # and the displayed scale.
        peak_x = t_val
        y_plot = None

        if _main_curve is not None:
            try:
                xd, yd = _main_curve.getData()
                if xd is not None and len(xd) > 0:
                    # Nearest data point to the exact t_val (tracks the slider)
                    idx    = int(np.argmin(np.abs(xd - t_val)))
                    peak_x = float(xd[idx])
                    peak_y = float(yd[idx])
                    if _log_y:
                        y_plot = (np.log10(peak_y) + 0.05*np.log10(peak_y)) if peak_y > 0 else 0.06
                    else:
                        y_plot = (peak_y + 0.05*peak_y) if peak_y > 0 else 5

            except Exception:
                pass

        if y_plot is None:
          # Fallback when the curve is not yet drawn
          if df is None or len(df) == 0:
              return
          idx    = int((df['mz'] - t_val).abs().argsort().iloc[0])
          peak_x = float(df.iloc[idx]['mz'])
          peak_y = float(df.iloc[idx]['intensity'])
          y_plot = (np.log10(peak_y) + 0.06) if peak_y > 0 else 0.06

        sc = pg.ScatterPlotItem(
          x=[peak_x], y=[y_plot],
          symbol='t1', size=14,
          pen=pg.mkPen('#cc0000', width=1.5),
          brush=pg.mkBrush(220, 40, 40, 200),
        )
        plot.addItem(sc)
        row_dict["scatter"] = sc

    def _update_all_triangles(self):
        for row in self._pair_rows:
            self._update_triangle(row)

    def _clear_triangles(self):
        for row in self._pair_rows:
            if row.get("scatter") is not None:
                try:
                    plot.removeItem(row["scatter"])
                except Exception:
                    pass
                row["scatter"] = None

    # ── Time-unit change ──────────────────────────────────────────

    def _on_unit_changed(self, unit):
        settings.setValue("tof2mass/time_unit", unit)
        for row in self._pair_rows:
            row["time_spin"].setToolTip(f"Reference time in {unit}")
        self._update_all_triangles()

    # ── Fit ───────────────────────────────────────────────────────

    def _get_pairs_us(self):
        """Collect valid (time_µs, mass) pairs from the UI rows."""
        unit = self._unit_combo.currentText()
        factor = self._UNIT_TO_US.get(unit, 1.0)
        pairs = []
        for row in self._pair_rows:
            t = row["time_spin"].value()
            m = row["mass_spin"].value()
            if t > 0 and m > 0:
                pairs.append((t * factor, m))
        return pairs

    def _export_reference(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export ToF Reference",
            settings.value("tof2mass/ref_dir", ""),
            "ToF reference files (*.tof2mass);;All files (*)")
        if not path:
            return
        if not path.lower().endswith(".tof2mass"):
            path += ".tof2mass"
        settings.setValue("tof2mass/ref_dir", os.path.dirname(path))

        unit = self._unit_combo.currentText()
        pairs = [(row["time_spin"].value(), row["mass_spin"].value()) for row in self._pair_rows]

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("# tof2mass_reference\n")
            fh.write(f"# time_unit={unit}\n")
            if self._a is not None:
                fh.write(f"# a={self._a_spin.value():.10g}\n")
                fh.write(f"# b={self._b_spin.value():.10g}\n")
            if self._r2 is not None:
                fh.write(f"# r2={self._r2:.8f}\n")
            for t, m in pairs:
                fh.write(f"{t}\t{m}\n")

    def _import_reference(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import ToF Reference",
            settings.value("tof2mass/ref_dir", ""),
            "ToF reference files (*.tof2mass);;All files (*)")
        if not path:
            return
        settings.setValue("tof2mass/ref_dir", os.path.dirname(path))

        unit = None
        pairs = []
        a = b = r2 = None
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("# time_unit="):
                        unit = line.split("=", 1)[1].strip()
                    elif line.startswith("# a="):
                        a = float(line.split("=", 1)[1].strip())
                    elif line.startswith("# b="):
                        b = float(line.split("=", 1)[1].strip())
                    elif line.startswith("# r2="):
                        r2 = float(line.split("=", 1)[1].strip())
                    elif line.startswith("#"):
                        continue
                    else:
                        parts = line.replace(",", "\t").split()
                        if len(parts) >= 2:
                            pairs.append((float(parts[0]), float(parts[1])))
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import failed", str(exc))
            return

        if not pairs:
            QtWidgets.QMessageBox.warning(self, "Empty file",
                "No valid (time, mass) pairs found in the file.")
            return

        # Apply time unit
        if unit is not None:
            idx = self._unit_combo.findText(unit)
            if idx >= 0:
                self._unit_combo.setCurrentIndex(idx)

        # Rebuild pair rows to match the file exactly
        # Remove all current rows (keep minimum of 2 in place, reuse them)
        while len(self._pair_rows) < len(pairs):
            self._add_pair_row()
        while len(self._pair_rows) > len(pairs):
            if len(self._pair_rows) <= 2:
                break
            self._remove_pair_row(self._pair_rows[-1])

        for row, (t, m) in zip(self._pair_rows, pairs):   
            row["time_spin"].setValue(t)                                                                                                                                                            
            row["mass_spin"].setValue(m)                  

        if a is not None and b is not None:                                                                                                                                                         
            self._a = a;  self._b = b;  self._r2 = r2
            self._a_spin.blockSignals(True); self._a_spin.setValue(a); self._a_spin.blockSignals(False)                                                                                             
            self._b_spin.blockSignals(True); self._b_spin.setValue(b); self._b_spin.blockSignals(False)
            self._r2_lbl.setText(f"{r2:.8f}" if r2 is not None else "—")

    @staticmethod
    def _compute_ab(t_arr, m_arr):
        """
        Fit  t = a·√m + b  from arrays of times (µs) and masses (Da).
        Returns (a, b, r2).  2 pairs → exact; ≥3 → least-squares.
        """
        sqrt_m = np.sqrt(m_arr)
        A = np.column_stack([sqrt_m, np.ones(len(t_arr))])
        if len(t_arr) == 2:
            try:
                ab = np.linalg.solve(A, t_arr)
            except np.linalg.LinAlgError:
                raise ValueError(
                    "Cannot solve: the two mass values produce a singular system.\n"
                    "Make sure the two reference masses are different.")
            a, b = float(ab[0]), float(ab[1])
        else:
            result = np.linalg.lstsq(A, t_arr, rcond=None)
            a, b = float(result[0][0]), float(result[0][1])

        t_pred  = a * sqrt_m + b
        ss_res  = float(np.sum((t_arr - t_pred) ** 2))
        ss_tot  = float(np.sum((t_arr - t_arr.mean()) ** 2))
        r2      = 1.0 - ss_res / ss_tot if ss_tot > 0 else 1.0
        return a, b, r2

    def _solve(self):
        pairs = self._get_pairs_us()
        if len(pairs) < 2:
            QtWidgets.QMessageBox.warning(
                self, "Not enough pairs",
                "Enter at least 2 valid (time, mass) pairs.")
            return
        t_arr = np.array([p[0] for p in pairs])
        m_arr = np.array([p[1] for p in pairs])
        try:
            a, b, r2 = self._compute_ab(t_arr, m_arr)
        except ValueError as exc:
            QtWidgets.QMessageBox.critical(self, "Fit error", str(exc))
            return
        self._a  = a;  self._b  = b;  self._r2 = r2
        self._a_spin.blockSignals(True); self._a_spin.setValue(a); self._a_spin.blockSignals(False)
        self._b_spin.blockSignals(True); self._b_spin.setValue(b); self._b_spin.blockSignals(False)
        self._r2_lbl.setText(f"{r2:.8f}")

    # ── Transformation ────────────────────────────────────────────

    @staticmethod
    def _tof_to_mass_df(data_df, a, b):
        """
        Replace the 'mz' column (time) with mass values computed from
            t = a·√m + b  →  m = ((t − b) / a)²
        Rows where the inversion would yield negative sqrt are dropped.
        """
        t_arr   = data_df['mz'].values.astype(float)
        sqrt_m  = (t_arr - b) / a
        m_arr   = np.where(sqrt_m > 0, sqrt_m ** 2, np.nan)
        out = data_df.copy()
        out['mz'] = m_arr
        out = out.dropna(subset=['mz'])
        out = out[out['mz'] > 0].reset_index(drop=True)
        return out

    # ── Output folder ─────────────────────────────────────────────

    def _browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Output Folder",
            self._folder_edit.text() or settings.value("tof2mass/output_folder", ""))
        if folder:
            self._folder_edit.setText(folder)
            settings.setValue("tof2mass/output_folder", folder)

    # ── Apply ─────────────────────────────────────────────────────

    def _apply(self):
        out_folder = self._folder_edit.text().strip()
        if not out_folder or not os.path.isdir(out_folder):
            QtWidgets.QMessageBox.warning(
                self, "No output folder", "Select a valid output folder first.")
            return

        if self._rb_current.isChecked():
            self._apply_current(out_folder)
        else:
            # Batch: require that a and b have been solved/set
            if self._a is None or self._b is None:
                QtWidgets.QMessageBox.warning(
                    self, "Not fitted",
                    "Fit (or manually enter) a and b before batch processing.")
                return
            self._apply_batch(out_folder)

    def _apply_current(self, out_folder):
        """Fit from reference pairs, apply to current spectrum, save."""
        if df is None or len(df) == 0:
            QtWidgets.QMessageBox.warning(
                self, "No spectrum", "Load a spectrum first.")
            return
        # Fit from the current reference pairs (individual fit for this spectrum)
        pairs = self._get_pairs_us()
        if len(pairs) < 2:
            QtWidgets.QMessageBox.warning(
                self, "Not enough pairs",
                "Enter at least 2 valid (time, mass) pairs.")
            return
        t_arr = np.array([p[0] for p in pairs])
        m_arr = np.array([p[1] for p in pairs])
        try:
            a, b, r2 = self._compute_ab(t_arr, m_arr)
        except ValueError as exc:
            QtWidgets.QMessageBox.critical(self, "Fit error", str(exc))
            return
        # Honour manual overrides in the spin boxes
        a  = self._a_spin.value() if self._a is not None else a
        b  = self._b_spin.value() if self._b is not None else b
        r2 = self._r2 if self._r2 is not None else r2

        try:
            result = self._tof_to_mass_df(df, a, b)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Transform error", str(exc))
            return

        src = combo.currentData() or combo.currentText() or ""
        stem, ext = os.path.splitext(os.path.basename(src)) if src else ("spectrum", ".txt")
        out_name = stem + "_mass_transformed" + (ext or ".txt")
        out_path = os.path.join(out_folder, out_name)
        src_sep  = _detect_file_sep(src) if src and os.path.isfile(src) else "\t"
        unit     = self._unit_combo.currentText()
        _write_tof_transformed(
            result, out_path, src_path=src, sep=src_sep,
            unit=unit, ref_times=t_arr, ref_masses=m_arr, a=a, b=b, r2=r2)

        # Update the displayed fit values
        self._a = a; self._b = b; self._r2 = r2
        self._a_spin.blockSignals(True); self._a_spin.setValue(a); self._a_spin.blockSignals(False)
        self._b_spin.blockSignals(True); self._b_spin.setValue(b); self._b_spin.blockSignals(False)
        self._r2_lbl.setText(f"{r2:.8f}")

        QtWidgets.QMessageBox.information(self, "Done", f"Saved to:\n{out_path}")

    def _apply_batch(self, out_folder):
        """Apply the fixed a and b (from UI) to every file in the batch folder."""
        a  = self._a_spin.value()
        b  = self._b_spin.value()
        r2 = self._r2 if self._r2 is not None else float('nan')

        pairs = self._get_pairs_us()
        t_arr = np.array([p[0] for p in pairs]) if pairs else np.array([])
        m_arr = np.array([p[1] for p in pairs]) if pairs else np.array([])
        unit  = self._unit_combo.currentText()

        in_folder = self._in_folder_edit.text().strip()
        if not in_folder or not os.path.isdir(in_folder):
            QtWidgets.QMessageBox.warning(
                self, "No input folder", "Select a valid input folder first.")
            return
        settings.setValue("tof2mass/input_folder", in_folder)
        _EXTS = (".txt", ".csv", ".tsv", ".dat", ".asc")
        files = sorted(
            os.path.join(root, f)
            for root, _dirs, fnames in os.walk(in_folder)
            for f in fnames
            if f.lower().endswith(_EXTS)
            and "_mass_transformed" not in f.lower())
        if not files:
            QtWidgets.QMessageBox.warning(
                self, "No files", f"No spectrum files found in:\n{in_folder}")
            return
        n = len(files)
        dlg = QtWidgets.QProgressDialog(
            f"Processing 0 / {n}…", "Cancel", 0, n, self)
        dlg.setWindowTitle("ToF → Mass Batch")
        dlg.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
        dlg.setMinimumWidth(340)
        dlg.show()
        errors  = []
        log_rows = []   # (out_filename, a_used, b_used)  — one per successful file
        for i, path in enumerate(files):
            if dlg.wasCanceled():
                break
            dlg.setLabelText(f"Processing {i+1} / {n}:  {os.path.basename(path)}")
            dlg.setValue(i)
            app.processEvents()
            try:
                raw  = read_spectrum_file(path, sep=get_sep_from_combo())
                res  = self._tof_to_mass_df(raw, a, b)
                stem, ext = os.path.splitext(os.path.basename(path))
                out_name  = stem + "_mass_transformed" + (ext or ".txt")
                out_path  = os.path.join(out_folder, out_name)
                src_sep   = _detect_file_sep(path)
                _write_tof_transformed(
                    res, out_path, src_path=path, sep=src_sep,
                    unit=unit, ref_times=t_arr, ref_masses=m_arr, a=a, b=b, r2=r2)
                log_rows.append((out_name, a, b))
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")

        # # ── Write batch log ────────────────────────────────────────
        # if log_rows:
        #     import datetime
        #     ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        #     log_name = f"tof2mass_batch_log_{ts}.txt"
        #     log_path = os.path.join(out_folder, log_name)
        #     ref_t_str = ",".join(f"{t:.6g}" for t in t_arr) if len(t_arr) else "—"
        #     ref_m_str = ",".join(f"{m:.6g}" for m in m_arr) if len(m_arr) else "—"
        #     with open(log_path, "w", encoding="utf-8") as fh:
        #         fh.write("# tof2mass_batch_log\n")
        #         fh.write(f"# date={datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        #         fh.write(f"# time_unit={unit}\n")
        #         fh.write(f"# ref_times={ref_t_str}\n")
        #         fh.write(f"# ref_masses={ref_m_str}\n")
        #         fh.write(f"# r2={r2:.8f}\n" if r2 is not None and not np.isnan(r2) else "# r2=—\n")
        #         fh.write("##########\n")
        #         fh.write("filename\ta\tb\n")
        #         for out_name, a_used, b_used in log_rows:
        #             fh.write(f"{out_name}\t{a_used:.10g}\t{b_used:.10g}\n")

        if errors:
            QtWidgets.QMessageBox.warning(
                self, "Batch Errors",
                f"{len(errors)} file(s) failed:\n" + "\n".join(errors[:10]))
        else:
            QtWidgets.QMessageBox.information(
                self, "Done",
                f"Done.  {n} file(s) saved to:\n{out_folder}\n\nLog: {log_name}")
        dlg.setValue(n)
        dlg.close()

    # ── Lifecycle ─────────────────────────────────────────────────

    def closeEvent(self, event):
        self._clear_triangles()
        super().closeEvent(event)


def _hsep():
    """Return a thin horizontal separator line widget."""
    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
    line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
    return line


def _batch_select_save_folder():
    folder = QtWidgets.QFileDialog.getExistingDirectory(
        main_win, "Select Output Folder", base_dir or "")
    return folder or None

def _batch_select_input_files():
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    return files if files else all_txt_files

def _batch_run(process_fn, files, save_folder, suffix, title, parallel=False, n_workers=1,
               extra_headers=None):
    if not files:
        QtWidgets.QMessageBox.warning(main_win, "Batch", "No files to process."); return
    n = len(files)
    dlg = QtWidgets.QProgressDialog(f"Processing 0 / {n}…", "Cancel", 0, n, main_win)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    dlg.setMinimumWidth(340); dlg.show()
    errors = []

    if not parallel or n_workers <= 1:
        # Serial path
        for i, path in enumerate(files):
            if dlg.wasCanceled(): break
            dlg.setLabelText(f"Processing {i+1} / {n}:  {os.path.basename(path)}")
            dlg.setValue(i); app.processEvents()
            try:
                raw = read_spectrum_file(path, sep=get_sep_from_combo())
                processed = process_fn(raw)
                stem, ext = os.path.splitext(os.path.basename(path))
                out_path = os.path.join(save_folder, stem + suffix + (ext or ".txt"))
                save_spectrum_df(processed, out_path,
                                 src_path=path,
                                 process_tag=suffix.strip("_"),
                                 extra_headers=extra_headers)
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
    else:
        # Parallel path - uses threads to avoid Windows spawn re-import issues.
        # NumPy/SciPy release the GIL so threading gives real parallelism here.
        sep = get_sep_from_combo()
        do_baseline = "_baseline" in suffix
        do_recal    = "_recal" in suffix
        airpls      = airpls_action.isChecked()
        args_list   = [(p, save_folder, suffix, sep, do_baseline, do_recal, airpls, extra_headers)
                       for p in files]
        completed = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_batch_process_file, a): a[0] for a in args_list}
            for future in concurrent.futures.as_completed(futures):
                if dlg.wasCanceled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                completed += 1
                path = futures[future]
                dlg.setLabelText(f"Done {completed} / {n}:  {os.path.basename(path)}")
                dlg.setValue(completed); app.processEvents()
                result = future.result()
                if result is not None:
                    errors.append(result)

    dlg.setValue(n); dlg.close()
    if errors:
        QtWidgets.QMessageBox.warning(main_win, f"{title} - Errors",
            f"{len(errors)} file(s) failed:\n" + "\n".join(errors[:10]))
    else:
        QtWidgets.QMessageBox.information(main_win, title,
            f"Done. {n} file(s) saved to:\n{save_folder}")


def _ask_parallel_options(title):
    """Ask user whether to parallelise and how many workers to use."""
    max_cpu = multiprocessing.cpu_count()
    dlg = QtWidgets.QDialog(main_win)
    dlg.setWindowTitle(title)
    layout = QtWidgets.QVBoxLayout(dlg)
    layout.addWidget(QtWidgets.QLabel("Processing options:"))

    parallel_chk = QtWidgets.QCheckBox("Parallelise across CPU cores")
    parallel_chk.setChecked(max_cpu > 1)
    layout.addWidget(parallel_chk)

    worker_row = QtWidgets.QHBoxLayout()
    worker_row.addWidget(QtWidgets.QLabel("Workers:"))
    worker_spin = QtWidgets.QSpinBox()
    worker_spin.setRange(1, max_cpu)
    worker_spin.setValue(max(1, max_cpu - 1))
    worker_spin.setToolTip(f"Your machine has {max_cpu} logical cores.")
    worker_row.addWidget(worker_spin)
    worker_row.addWidget(QtWidgets.QLabel(f"(max {max_cpu})"))
    worker_row.addStretch()
    layout.addLayout(worker_row)

    parallel_chk.toggled.connect(worker_spin.setEnabled)
    worker_spin.setEnabled(parallel_chk.isChecked())

    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok |
        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    btns.accepted.connect(dlg.accept)
    btns.rejected.connect(dlg.reject)
    layout.addWidget(btns)

    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None, None
    return parallel_chk.isChecked(), worker_spin.value()


def batch_baseline():
    folder = _batch_select_save_folder()
    if folder is None: return
    parallel, n_workers = _ask_parallel_options("Batch Baseline")
    if parallel is None: return
    if airpls_action.isChecked():
        method = 'airPLS'
        bl_headers = [
            "#baseline_method=airPLS",
            "#baseline_lambda=100",
            "#baseline_porder=1",
            "#baseline_itermax=20",
        ]
    else:
        method = 'SNIP'
        bl_headers = [
            "#baseline_method=SNIP",
            "#baseline_max_hwidth=40",
            "#baseline_smooth_iters=3",
        ]
    _batch_run(apply_baseline, _batch_select_input_files(), folder,
               "_baseline_corrected", f"Batch Baseline ({method})",
               parallel=parallel, n_workers=n_workers,
               extra_headers=bl_headers)

def _save_residuals(save_folder, filename, summary_df, ts=None,
                    orig_mz=None, recal_mz=None, color_map=None):
    """
    Save residuals inside a timestamped subfolder.

      *_residuals_table.csv    - calibration-point table; may include
                                 'peak_list' column when data comes from
                                 named peak-list rows.
      *_residuals_spectrum.txt - dense mz vs Δmz curve (one line per point).
                                 Header comments include '# peak_list:<name>'
                                 markers so the viewer can split by peak list,
                                 and '# peak_list_color:<name>=<hex>' lines so
                                 the viewer can restore the original colors.
    """
    from datetime import datetime as _dt
    if ts is None:
        ts = _dt.now().strftime("%d.%m.%Y - %H.%M.%S")
    res_folder = os.path.join(save_folder, f"Residuals {ts}")
    os.makedirs(res_folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(filename))[0]

    # ── CSV table ────────────────────────────────────────────
    csv_path = os.path.join(res_folder, stem + "_residuals_table.csv")
    summary_df.to_csv(csv_path, index=False)

    # ── Dense plottable spectrum ──────────────────────────────
    txt_path = os.path.join(res_folder, stem + "_residuals_spectrum.txt")
    has_pl_col = "peak_list" in summary_df.columns
    with open(txt_path, "w") as fh:
        # Color metadata headers — one per named peak list
        if color_map:
            for pl_name, hex_color in color_map.items():
                safe_name = str(pl_name).replace("\n", " ")
                fh.write(f"# peak_list_color:{safe_name}={hex_color}\n")
        fh.write("# original_mz\tdelta_mz\tpeak_list\n")
        if orig_mz is not None and recal_mz is not None:
            # Dense curve without per-point peak_list information
            for om, rm in zip(orig_mz, recal_mz):
                fh.write(f"{om:.6f}\t{rm - om:.6f}\t\n")
        else:
            cols = list(summary_df.columns)
            if len(cols) >= 3:
                for _, row in summary_df.iterrows():
                    pl_name = str(row["peak_list"]) if has_pl_col else ""
                    fh.write(f"{row[cols[0]]:.6f}\t{row[cols[2]]:.6f}\t{pl_name}\n")

    return ts



# ═════════════════════════════════════════════════════════════════════════════
#  RESIDUALS VIEWER
# ═════════════════════════════════════════════════════════════════════════════

# ResidualsViewerWindow — defined in droplet/ui/windows/residuals_viewer.py

def _batch_run_recal(files, save_folder, suffix, title, do_baseline=False,
                     parallel=False, n_workers=1):
    """
    Like _batch_run but for auto-recalibration: tracks check_manually flags
    and shows a summary of files that need manual review at the end.
    Only works in serial mode - parallel path falls back to _batch_run.
    """
    if not files:
        QtWidgets.QMessageBox.warning(main_win, "Batch", "No files to process."); return

    if parallel and n_workers > 1:
        # Parallel path falls back to serial so residuals are always written.
        # (True parallelism would require passing residuals back through the
        #  worker queue - simpler to just use the serial path for correctness.)
        pass   # fall through to the serial path below

    # Serial path with check_manually tracking
    n = len(files)
    dlg = QtWidgets.QProgressDialog(f"Processing 0 / {n}…", "Cancel", 0, n, main_win)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    dlg.setMinimumWidth(340); dlg.show()

    errors        = []
    needs_review  = []   # list of filenames flagged by check_manually
    batch_ts      = None # shared timestamp for the residuals folder

    # Build static baseline headers (same for every file if do_baseline)
    if do_baseline:
        if snip_action.isChecked():
            _bl_headers = [
                "#baseline_method=SNIP",
                "#baseline_max_hwidth=40",
                "#baseline_smooth_iters=3",
            ]
        else:
            _bl_headers = [
                "#baseline_method=airPLS",
                "#baseline_lambda=100",
                "#baseline_porder=1",
                "#baseline_itermax=20",
            ]
    else:
        _bl_headers = []

    for i, path in enumerate(files):
        if dlg.wasCanceled(): break
        dlg.setLabelText(f"Processing {i+1} / {n}:  {os.path.basename(path)}")
        dlg.setValue(i); app.processEvents()
        summary_df = None   # reset each iteration so a failed file never reuses old data
        recal_np   = None
        try:
            raw = read_spectrum_file(path, sep=get_sep_from_combo())
            if do_baseline:
                raw = apply_baseline(raw)
            data_np = raw[['mz', 'intensity']].to_numpy()
            recal_np, check_manually, summary_df, fitparams = _run_auto_recal_worker(data_np, path)
            processed = pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})
            stem, ext = os.path.splitext(os.path.basename(path))
            out_path = os.path.join(save_folder, stem + suffix + (ext or ".txt"))

            # Build per-file recalibration headers
            _recal_headers = [
                "#recalibration_method=auto",
                f"#recal_polynomial_degree={min(2, len(summary_df)-1)}",
                f"#recal_fitparam_a2={fitparams[0]:.10g}",
                f"#recal_fitparam_a1={fitparams[1]:.10g}",
                f"#recal_fitparam_a0={fitparams[2]:.10g}",
            ]
            for _j, (_, _row) in enumerate(summary_df.iterrows()):
                _recal_headers.append(
                    f"#recal_pair_{_j+1}={_row['original m/z']:.4f}"
                    f"->{_row['corrected to']:.4f}"
                    f" (delta={_row['Δ m/z']:+.4f})")

            save_spectrum_df(processed, out_path,
                             src_path=path,
                             process_tag=suffix.strip("_"),
                             extra_headers=_bl_headers + _recal_headers)
            if check_manually:
                needs_review.append(os.path.basename(path))
        except Exception as e:
            errors.append(f"{os.path.basename(path)}: {e}")
            continue   # ← skip residuals for failed files

        # Write residuals only if recalibration actually produced data
        if summary_df is not None and recal_np is not None:
            try:
                batch_ts = _save_residuals(
                    save_folder, path, summary_df,
                    ts=batch_ts,
                    orig_mz=data_np[:, 0],
                    recal_mz=recal_np[:, 0])
            except Exception as e_res:
                errors.append(f"{os.path.basename(path)} [residuals]: {e_res}")

    dlg.setValue(n); dlg.close()

    # Build result message
    msg_parts = [f"Done. {n} file(s) processed.\nSaved to:\n{save_folder}"]
    if errors:
        msg_parts.append(f"\n\n⚠  {len(errors)} file(s) failed:\n" +
                         "\n".join(errors[:10]))
    if needs_review:
        msg_parts.append(
            f"\n\n🔍  {len(needs_review)} file(s) flagged for manual review\n"
            "(too few calibration peaks, or suspicious last-peak delta):\n" +
            "\n".join(f"  • {f}" for f in needs_review))

    icon = (QtWidgets.QMessageBox.Icon.Warning
            if errors or needs_review
            else QtWidgets.QMessageBox.Icon.Information)
    box = QtWidgets.QMessageBox(main_win)
    box.setIcon(icon)
    box.setWindowTitle(title)
    box.setText("\n".join(msg_parts))

    # Add a "View Residuals" button if a residuals folder was actually written
    view_btn = None
    if batch_ts is not None:
        res_folder_path = os.path.join(save_folder, f"Residuals {batch_ts}")
        if os.path.isdir(res_folder_path):
            view_btn = box.addButton("View Residuals…",
                                     QtWidgets.QMessageBox.ButtonRole.ActionRole)
    box.addButton(QtWidgets.QMessageBox.StandardButton.Ok)

    box.exec()
    if view_btn is not None and box.clickedButton() is view_btn:
        _open_residuals_viewer(res_folder_path)

def batch_recalibrate():
    folder = _batch_select_save_folder()
    if folder is None: return
    parallel, n_workers = _ask_parallel_options("Batch Auto-Recalibration")
    if parallel is None: return
    _batch_run_recal(_batch_select_input_files(), folder,
                     "_recalibrated", "Batch Auto-Recalibration",
                     do_baseline=False,
                     parallel=parallel, n_workers=n_workers)

def batch_both():
    folder = _batch_select_save_folder()
    if folder is None: return
    parallel, n_workers = _ask_parallel_options("Batch Baseline + Recalibration")
    if parallel is None: return
    _batch_run_recal(_batch_select_input_files(), folder,
                     "_baseline+recalibrated", "Batch Baseline + Recalibration",
                     do_baseline=True,
                     parallel=parallel, n_workers=n_workers)

# ─────────────────────────────────────────────
#  Normalization to file
# ─────────────────────────────────────────────
def _ask_normalize_options(parent=main_win):
    """
    Ask user for normalization options:
      - Normalize to max (default)
      - Normalize so that a chosen m/z value = 1
    Returns (mode, peak_mz) where mode is 'max' or 'peak', or (None, None) on cancel.
    """
    dlg = QtWidgets.QDialog(parent)
    dlg.setWindowTitle("Normalization options")
    lay = QtWidgets.QVBoxLayout(dlg)
    lay.addWidget(QtWidgets.QLabel("Normalize intensities so that:"))

    grp   = QtWidgets.QButtonGroup(dlg)
    rb_max  = QtWidgets.QRadioButton("The highest peak = 1  (standard max normalization)")
    rb_peak = QtWidgets.QRadioButton("A specific m/z value = 1:")
    rb_max.setChecked(True)
    grp.addButton(rb_max); grp.addButton(rb_peak)
    lay.addWidget(rb_max); lay.addWidget(rb_peak)

    mz_spin = QtWidgets.QDoubleSpinBox()
    mz_spin.setRange(0.0, 100000.0)
    mz_spin.setDecimals(4)
    mz_spin.setSingleStep(0.1)
    mz_spin.setValue(18.0)
    mz_spin.setEnabled(False)
    mz_spin.setToolTip("The intensity at this m/z (nearest data point) will be set to 1.")
    lay.addWidget(mz_spin)

    rb_peak.toggled.connect(mz_spin.setEnabled)

    btns = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.StandardButton.Ok |
        QtWidgets.QDialogButtonBox.StandardButton.Cancel)
    btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
    lay.addWidget(btns)

    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return None, None
    if rb_peak.isChecked():
        return 'peak', mz_spin.value()
    return 'max', None


_NORM_LOG_FLOOR = 0.007   # log-Y safety floor - only applied when log Y is active

def _normalize_df(data_df, mode='max', peak_mz=None, apply_log_floor=False):
    """
    Normalize a spectrum DataFrame.
    mode='max'  → subtract the minimum (floor to 0), then divide by the maximum.
    mode='peak' → subtract the minimum, then divide by the intensity at peak_mz.
    apply_log_floor: clamp to _NORM_LOG_FLOOR - only pass True when log Y is active,
                     so log display never sees zeros. Leave False for normal display;
                     sigma-3 clipping handles noise suppression instead.
    Returns a new DataFrame with a common zero floor.
    """
    out = data_df.copy()
    int_arr = out['intensity'].values.astype(float)

    # Subtract the minimum so every spectrum shares the same zero floor
    int_arr = int_arr - int_arr.min()

    if mode == 'peak' and peak_mz is not None:
        idx = (out['mz'] - peak_mz).abs().idxmin()
        scale = float(int_arr[out.index.get_loc(idx)])
    else:
        scale = float(int_arr.max())

    if scale == 0:
        if apply_log_floor:
            out['intensity'] = np.clip(int_arr, _NORM_LOG_FLOOR, None)
        else:
            out['intensity'] = int_arr
        return out

    int_arr = int_arr / scale
    if apply_log_floor:
        int_arr = np.clip(int_arr, _NORM_LOG_FLOOR, None)
    out['intensity'] = int_arr
    return out


def normalize_current():
    src_df, src_label, _ = _ask_source_spectrum()
    if src_df is None: return
    mode, peak_mz = _ask_normalize_options()
    if mode is None: return
    result = _normalize_df(src_df, mode=mode, peak_mz=peak_mz, apply_log_floor=False)
    _add_processed_overlay(result, f"Normalized ({src_label})", "_normalized")


def batch_normalize():
    folder = _batch_select_save_folder()
    if folder is None: return
    mode, peak_mz = _ask_normalize_options()
    if mode is None: return
    def proc(raw):
        return _normalize_df(raw, mode=mode, peak_mz=peak_mz, apply_log_floor=False)
    _batch_run(proc, _batch_select_input_files(), folder,
               "_normalized", "Batch Normalize")


# ═════════════════════════════════════════════════════════════════════════════
#  CLUSTER DETECTION
# ═════════════════════════════════════════════════════════════════════════════

def _detect_all_peaks_for_clusters(data_df, min_snr=1.0, min_pct=0.5):
    """
    Detect all peaks in data_df above min_snr and min_pct of max intensity.
    Returns sorted numpy array of (mz, intensity) pairs.
    Awareness of existing peak lists: peaks near known m/z values are flagged
    but still included - the caller decides what to do with them.
    """
    from scipy.signal import find_peaks as _fp
    if data_df is None or len(data_df) == 0:
        return np.empty((0, 2))

    mz_arr  = data_df['mz'].values
    int_arr = data_df['intensity'].values
    max_int = int_arr.max()
    if max_int == 0:
        return np.empty((0, 2))

    min_height = (min_pct / 100.0) * max_int
    indices, _ = _fp(int_arr, distance=3, height=min_height)
    if len(indices) == 0:
        return np.empty((0, 2))

    # SNR filter: local Q1 in ±5 Da window
    LOCAL = 5.0
    kept = []
    for i in indices:
        mz = mz_arr[i]; intensity = int_arr[i]
        if mz < _AR_MINIMUM_MASS:
            continue
        mask = (mz_arr >= mz - LOCAL) & (mz_arr <= mz + LOCAL)
        noise = np.percentile(int_arr[mask], 25) if mask.sum() > 4 else 0.0
        if noise <= 0:
            noise = 1e-9
        if intensity / noise >= min_snr:
            kept.append((mz, intensity))

    if not kept:
        return np.empty((0, 2))
    arr = np.array(kept)
    return arr[np.argsort(arr[:, 0])]


def _known_peak_mzs():
    """Collect all m/z values from currently enabled peak list rows."""
    known = []
    for row in custom_peak_rows:
        for mz in parse_peaks_text(row['peaks_input'].text()):
            known.append(mz)
    return np.array(known) if known else np.array([])


def _build_chains(peaks_mz, peaks_int, spacing, tol):
    """
    Greedy chain builder: given detected peak positions sorted ascending,
    find all chains where consecutive members are within spacing±tol.
    Seed from highest-intensity peaks first. Each peak belongs to at most one chain.
    Returns list of lists of indices into peaks_mz.
    """
    used    = np.zeros(len(peaks_mz), dtype=bool)
    # Sort seeds by intensity descending
    seed_order = np.argsort(peaks_int)[::-1]
    chains = []

    for seed in seed_order:
        if used[seed]:
            continue
        chain = [seed]
        used[seed] = True

        # Walk forward (higher mass)
        cur = seed
        while True:
            target = peaks_mz[cur] + spacing
            cands  = np.where(
                (~used) &
                (peaks_mz >= target - tol) &
                (peaks_mz <= target + tol)
            )[0]
            if len(cands) == 0:
                break
            # Pick closest to exact target
            best = cands[np.argmin(np.abs(peaks_mz[cands] - target))]
            chain.append(best)
            used[best] = True
            cur = best

        # Walk backward (lower mass)
        cur = seed
        while True:
            target = peaks_mz[cur] - spacing
            cands  = np.where(
                (~used) &
                (peaks_mz >= target - tol) &
                (peaks_mz <= target + tol)
            )[0]
            if len(cands) == 0:
                break
            best = cands[np.argmin(np.abs(peaks_mz[cands] - target))]
            chain.insert(0, best)
            used[best] = True
            cur = best

        chains.append(chain)

    return chains


def _auto_find_spacings(peaks_mz, max_spacing=250.0, bin_width=0.1, min_count=2):
    if len(peaks_mz) < 2:
        return []
    # Vectorized pairwise differences - no Python loop
    mz = np.asarray(peaks_mz)
    diff_matrix = mz[:, None] - mz[None, :]   # all pairs
    diffs = diff_matrix[diff_matrix > 0]       # upper triangle equivalent
    diffs = diffs[(diffs >= 1.0) & (diffs <= max_spacing)]
    if len(diffs) == 0:
        return []
    bins = np.arange(1.0, max_spacing + bin_width, bin_width)
    counts, edges = np.histogram(diffs, bins=bins)
    from scipy.signal import find_peaks as _fp
    peak_idx, _ = _fp(counts, height=min_count, distance=max(1, int(0.5 / bin_width)))
    spacings_with_counts = [
        (round(float((edges[i] + edges[i+1]) / 2), 3), int(counts[i]))
        for i in peak_idx
    ]
    spacings_with_counts.sort(key=lambda t: -t[1])
    return [s for s, c in spacings_with_counts]


def run_cluster_detection(spacings_input, tol, min_chain, min_snr, min_pct,
                          peak_list_mzs=None):
    """
    peak_list_mzs: if provided (list/array of m/z floats), skip spectrum
    peak detection and use those values directly. Spacings are always
    auto-discovered from the peak list itself.
    """
    if df is None and peak_list_mzs is None:
        return []

    if peak_list_mzs is not None and len(peak_list_mzs) >= 2:
        # Peak-list mode: use supplied m/z values, fake equal intensities,
        # always auto-discover spacings from the list itself.
        peaks_mz  = np.array(sorted(peak_list_mzs), dtype=float)
        peaks_int = np.ones(len(peaks_mz), dtype=float)
        known_mz  = peaks_mz.copy()
        candidate_spacings = _auto_find_spacings(peaks_mz)
    else:
        if df is None:
            return []
        peaks = _detect_all_peaks_for_clusters(df, min_snr=min_snr, min_pct=min_pct)
        if len(peaks) == 0:
            return []
        peaks_mz  = peaks[:, 0]
        peaks_int = peaks[:, 1]
        known_mz  = _known_peak_mzs()

        if spacings_input.strip():
            try:
                candidate_spacings = [float(s.strip()) for s in spacings_input.split(',') if s.strip()]
            except ValueError:
                candidate_spacings = []
        else:
            candidate_spacings = _auto_find_spacings(peaks_mz)

    if not candidate_spacings:
        return []

    # Each spacing is independent - evaluate in parallel
    def _eval_spacing(spacing):
        chains = _build_chains(peaks_mz, peaks_int, spacing, tol)
        results = []
        for chain in chains:
            if len(chain) < min_chain:
                continue
            members = []
            for i in sorted(chain):
                mz = float(peaks_mz[i])
                intensity = float(peaks_int[i])
                is_known = (len(known_mz) > 0 and
                            np.any(np.abs(known_mz - mz) < tol * 2))
                members.append((mz, intensity, is_known))
            results.append({'spacing': spacing, 'members': members})
        return results

    n_workers = max(1, min(len(candidate_spacings), multiprocessing.cpu_count() - 1))
    all_clusters = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_eval_spacing, s): s for s in candidate_spacings}
        for future in concurrent.futures.as_completed(futures):
            try:
                all_clusters.extend(future.result())
            except Exception:
                pass

    # Deduplicate: remove clusters whose members heavily overlap with an earlier one
    # (can happen when two spacings find the same series at different offsets)
    global_used_mz = set()
    deduped = []
    for cluster in sorted(all_clusters, key=lambda c: -len(c['members'])):
        mz_set = {round(m[0], 2) for m in cluster['members']}
        overlap = len(mz_set & global_used_mz) / len(mz_set)
        if overlap < 0.6:   # less than 60% overlap → keep
            deduped.append(cluster)
            global_used_mz |= mz_set

    return deduped


# ClusterDetectionWindow — defined in droplet/ui/windows/cluster_detection.py

_comparison_win_ref = None  # window class in droplet/ui/windows/peak_comparison.py

# ═════════════════════════════════════════════════════════════════════════════
#  PEAK AREA MEASUREMENT WINDOW
_area_win_ref = None  # window class in droplet/ui/windows/peak_area.py

def _open_peak_area_window():
    global _area_win_ref
    if _area_win_ref is not None:
        try:
            _area_win_ref.raise_(); _area_win_ref.activateWindow(); return
        except Exception:
            pass
    _area_win_ref = PeakAreaWindow()
    _area_win_ref.show()
    _area_win_ref.raise_()
    _area_win_ref.activateWindow()
    
def show_cluster_detection():
    global _cluster_win_ref
    if _cluster_win_ref is not None:
        try:
            _cluster_win_ref.close()
        except Exception:
            pass
    win = ClusterDetectionWindow(parent=None)
    _cluster_win_ref = win
    win.show()
    win.raise_()
    win.activateWindow()
    QtCore.QTimer.singleShot(50, lambda: (win.raise_(), win.activateWindow()))
    
# ═════════════════════════════════════════════════════════════════════════════
#  PEAK RATIO WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class PeakRatioWindow(QtWidgets.QWidget):
    """Floating two-click tool: click two peak apices, get their intensity ratio."""

    _SNAP = 2.0   # ±Da search window for auto-apex detection

    def __init__(self, parent=None):
        super().__init__(parent,
                         QtCore.Qt.WindowType.Window |
                         QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle("Peak Ratio")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._step   = 0          # 0 = waiting P1, 1 = waiting P2
        self._apex1  = None       # (mz, intensity) of first peak
        self._marker1 = None      # pg.InfiniteLine P1
        self._marker2 = None      # pg.InfiniteLine P2
        self._dot1    = None      # pg.ScatterPlotItem P1
        self._dot2    = None      # pg.ScatterPlotItem P2

        # ── Layout ──────────────────────────────────────────────────────────
        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 10, 10, 10)

        self._status = QtWidgets.QLabel("→ Click the <b>first</b> peak on the plot")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("font-size: 10pt; color: #2277cc;")
        lay.addWidget(self._status)

        lay.addWidget(_make_h_line())

        grid = QtWidgets.QGridLayout()
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        grid.addWidget(QtWidgets.QLabel("P1  m/z:"), 0, 0)
        self._p1_mz  = QtWidgets.QLabel("—")
        grid.addWidget(self._p1_mz, 0, 1)
        grid.addWidget(QtWidgets.QLabel("I:"), 0, 2)
        self._p1_int = QtWidgets.QLabel("—")
        grid.addWidget(self._p1_int, 0, 3)

        grid.addWidget(QtWidgets.QLabel("P2  m/z:"), 1, 0)
        self._p2_mz  = QtWidgets.QLabel("—")
        grid.addWidget(self._p2_mz, 1, 1)
        grid.addWidget(QtWidgets.QLabel("I:"), 1, 2)
        self._p2_int = QtWidgets.QLabel("—")
        grid.addWidget(self._p2_int, 1, 3)

        lay.addLayout(grid)

        lay.addWidget(_make_h_line())

        res_grid = QtWidgets.QGridLayout()
        res_grid.setColumnStretch(1, 1)
        res_grid.addWidget(QtWidgets.QLabel("P1 / P2:"), 0, 0)
        self._r12 = QtWidgets.QLabel("—")
        self._r12.setStyleSheet("font-weight: bold;")
        res_grid.addWidget(self._r12, 0, 1)
        res_grid.addWidget(QtWidgets.QLabel("P2 / P1:"), 1, 0)
        self._r21 = QtWidgets.QLabel("—")
        self._r21.setStyleSheet("font-weight: bold;")
        res_grid.addWidget(self._r21, 1, 1)
        lay.addLayout(res_grid)

        lay.addWidget(_make_h_line())

        btn_row = QtWidgets.QHBoxLayout()
        self._reset_btn = QtWidgets.QPushButton("Reset")
        self._reset_btn.clicked.connect(self._reset)
        btn_row.addWidget(self._reset_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self.setFixedWidth(310)
        self.adjustSize()

        # Connect scene click handler
        plot.scene().sigMouseClicked.connect(self._on_click)

    # ── helpers ─────────────────────────────────────────────────────────────

    def _snap_apex(self, clicked_mz):
        if df is None or len(df) == 0:
            return None
        result = _detect_peak_near(df, clicked_mz, window=self._SNAP)
        if result is not None:
            return result
        idx = (df['mz'] - clicked_mz).abs().idxmin()
        return float(df.loc[idx, 'mz']), float(df.loc[idx, 'intensity'])

    def _remove_markers(self):
        for item in (self._marker1, self._marker2, self._dot1, self._dot2):
            if item is not None:
                try:
                    plot.removeItem(item)
                except Exception:
                    pass
        self._marker1 = self._marker2 = self._dot1 = self._dot2 = None

    def _make_line(self, mz, color, label_pos):
        pen = pg.mkPen(color, width=1, style=QtCore.Qt.PenStyle.DashLine)
        line = pg.InfiniteLine(pos=mz, angle=90, movable=False, pen=pen,
                               label=f'{mz:.4f}',
                               labelOpts={'color': color, 'position': label_pos})
        plot.addItem(line, ignoreBounds=True)
        return line

    def _make_dot(self, mz, intensity, color):
        dot = pg.ScatterPlotItem([mz], [intensity], symbol='o', size=10,
                                 pen=pg.mkPen(color, width=2),
                                 brush=pg.mkBrush(color))
        plot.addItem(dot)
        return dot

    def _reset(self):
        self._step  = 0
        self._apex1 = None
        self._remove_markers()
        self._p1_mz.setText("—");  self._p1_int.setText("—")
        self._p2_mz.setText("—");  self._p2_int.setText("—")
        self._r12.setText("—");    self._r21.setText("—")
        self._status.setText("→ Click the <b>first</b> peak on the plot")

    # ── click handler ────────────────────────────────────────────────────────

    def _on_click(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        pos = event.scenePos()
        if not plot.sceneBoundingRect().contains(pos):
            return
        event.accept()

        clicked_mz = plot.vb.mapSceneToView(pos).x()
        apex = self._snap_apex(clicked_mz)
        if apex is None:
            self._status.setText(
                "<span style='color:red'>No spectrum loaded — open a file first.</span>")
            return
        mz, intensity = apex

        if self._step == 0:
            # First peak
            self._remove_markers()
            self._apex1   = (mz, intensity)
            self._marker1 = self._make_line(mz, '#3b9ddd', 0.92)
            self._dot1    = self._make_dot(mz, intensity, '#3b9ddd')
            self._p1_mz.setText(f"{mz:.4f}")
            self._p1_int.setText(f"{intensity:.6g}")
            self._p2_mz.setText("—");  self._p2_int.setText("—")
            self._r12.setText("—");    self._r21.setText("—")
            self._status.setText("→ Click the <b>second</b> peak on the plot")
            self._step = 1

        else:
            # Second peak
            mz1, int1 = self._apex1
            self._marker2 = self._make_line(mz, '#e05c5c', 0.85)
            self._dot2    = self._make_dot(mz, intensity, '#e05c5c')
            self._p2_mz.setText(f"{mz:.4f}")
            self._p2_int.setText(f"{intensity:.6g}")

            if int1 > 0 and intensity > 0:
                self._r12.setText(f"{int1 / intensity:.4f}")
                self._r21.setText(f"{intensity / int1:.4f}")
            else:
                self._r12.setText("N/A (zero intensity)")
                self._r21.setText("N/A (zero intensity)")

            self._status.setText("✓ Done — click Reset to measure again")
            self._step = 0

    # ── cleanup ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        try:
            plot.scene().sigMouseClicked.disconnect(self._on_click)
        except Exception:
            pass
        self._remove_markers()
        global _ratio_win_ref
        _ratio_win_ref = None
        super().closeEvent(event)


def _make_h_line():
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.Shape.HLine)
    f.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
    return f


_ratio_win_ref = None


def _open_ratio_window():
    global _ratio_win_ref
    if _ratio_win_ref is not None:
        try:
            _ratio_win_ref.raise_()
            _ratio_win_ref.activateWindow()
            return
        except Exception:
            _ratio_win_ref = None
    _ratio_win_ref = PeakRatioWindow()
    _ratio_win_ref.show()
    _ratio_win_ref.raise_()
    _ratio_win_ref.activateWindow()


# Wire analysis menu
airpls_action.triggered.connect(lambda: reapply_transforms() if baseline_action.isChecked() else None)
snip_action.triggered.connect(  lambda: reapply_transforms() if baseline_action.isChecked() else None)
cluster_detect_action.triggered.connect(show_cluster_detection)
area_action.triggered.connect(_open_peak_area_window)
ratio_mode_action.triggered.connect(_open_ratio_window)

def _on_baseline_toggled(checked):
    global _baseline_overlay
    if checked:
        apply_baseline_to_current()
    else:
        _remove_overlay_if_exists(_baseline_overlay)
        _baseline_overlay = None
        reapply_transforms()
        render_plot()

baseline_action.toggled.connect(_on_baseline_toggled)
baseline_batch_action.triggered.connect(batch_baseline)
auto_recal_action.triggered.connect(show_auto_recalibrate)
recal_batch_action.triggered.connect(batch_recalibrate)
both_current_action.triggered.connect(apply_both_to_current)
both_batch_action.triggered.connect(batch_both)
normalize_current_action.triggered.connect(normalize_current)
normalize_batch_action.triggered.connect(batch_normalize)

_tof_to_mass_win_ref = [None]

def _open_tof_to_mass_window():
    global _tof_to_mass_win_ref
    win = _tof_to_mass_win_ref[0]
    if win is not None:
        try:
            if win.isVisible():
                win.raise_()
                win.activateWindow()
                return
        except RuntimeError:
            pass
    win = TofToMassWindow()
    _tof_to_mass_win_ref[0] = win
    win.show()
    win.raise_()

tof_to_mass_action.triggered.connect(_open_tof_to_mass_window)

_residuals_viewer_ref = None
def _open_residuals_viewer(res_folder=None):
    global _residuals_viewer_ref
    if _residuals_viewer_ref is not None:
        try:
            if _residuals_viewer_ref.isVisible():
                _residuals_viewer_ref.raise_()
                _residuals_viewer_ref.activateWindow()
                if res_folder:
                    _residuals_viewer_ref._load_folder(res_folder)
                return
        except Exception:
            pass
    _residuals_viewer_ref = ResidualsViewerWindow(res_folder=res_folder, parent=main_win)
    _residuals_viewer_ref.show()
    _residuals_viewer_ref.raise_()

view_residuals_action.triggered.connect(lambda: _open_residuals_viewer())

# ─────────────────────────────────────────────
#  OPT: Cache-aware highlight_peaks
# ─────────────────────────────────────────────
_OVERLAP_PEN_STYLES = [
    QtCore.Qt.PenStyle.SolidLine,
    QtCore.Qt.PenStyle.DashLine,
    QtCore.Qt.PenStyle.DotLine,
    QtCore.Qt.PenStyle.DashDotLine,
    QtCore.Qt.PenStyle.DashDotDotLine,
]

def highlight_peaks(data_df, peaks, color_str, peak_label="Peak", alpha=255, bg_color_str='w'):
    color    = apply_alpha(color_str, alpha)
    bg_color = pg.mkColor(bg_color_str)

    peaks_flat = []
    for group in peaks:
        if not isinstance(group, (list, tuple)): group = [group]
        peaks_flat.extend(group)

    geom_list = _get_highlight_geometry(data_df, peaks_flat)
    if geom_list:
        _nan = np.array([np.nan])
        _cmz  = np.concatenate([np.concatenate([g[0], _nan]) for g in geom_list])
        _cint = np.concatenate([np.concatenate([g[1], _nan]) for g in geom_list])
        _i1 = plot.plot(_cmz, _cint, pen=pg.mkPen(bg_color, width=3))
        _i2 = plot.plot(_cmz, _cint, pen=pg.mkPen(color, width=3))
        if _hl_row_id[0] is not None:
            _peak_hl_items_by_row.setdefault(_hl_row_id[0], []).extend([_i1, _i2])
    for (_, _, mz_min, mz_max, peak_mz, peak_int) in geom_list:
        highlighted_ranges.append(
            (mz_min, mz_max, peak_label, peak_mz, peak_int))

# ─────────────────────────────────────────────
#  Peak label/mass toggles  (defined before render_plot)
# ─────────────────────────────────────────────
peak_labels_toggle   = QtWidgets.QCheckBox("Show Labels")
peak_labels_toggle.setToolTip("Draw tilted row labels above each highlighted peak")
peak_masses_toggle      = QtWidgets.QCheckBox("Show highlighted masses")
peak_masses_toggle.setToolTip("Draw m/z value above all manually highlighted peaks")
peak_masses_all_toggle  = QtWidgets.QCheckBox("Show masses above threshold")
peak_masses_all_toggle.setToolTip("Draw m/z value above any peak exceeding the intensity threshold")
auto_peaks_toggle    = QtWidgets.QCheckBox("Auto-detect peaks")
auto_peaks_toggle.setToolTip("Mark all peaks above the intensity threshold automatically")
show_integers_toggle = QtWidgets.QCheckBox("Integer m/z")
show_integers_toggle.setToolTip("Show m/z labels as rounded integers instead of decimals")
show_integers_toggle.setChecked(settings.value("show_integers", False, type=bool))
mass_threshold_label = QtWidgets.QLabel("Threshold:")
mass_threshold_spin  = QtWidgets.QDoubleSpinBox()
mass_threshold_spin.setRange(0.0, 100.0); mass_threshold_spin.setValue(5.0)
mass_threshold_spin.setMinimumWidth(60)
threshold_mode_combo = QtWidgets.QComboBox()
threshold_mode_combo.addItems(["% of max intensity", "SNR"])
threshold_mode_combo.setMinimumWidth(40)

def _clear_peak_symbol_scatters():
    global _peak_symbol_scatter_items
    for item in _peak_symbol_scatter_items:
        try:
            if item.scene() is not None:
                plot.removeItem(item)
        except Exception:
            pass
    _peak_symbol_scatter_items.clear()


def _draw_peak_symbols_on_plot():
    """Draw legend symbols directly over each highlighted peak on the main plot."""
    global _peak_symbol_scatter_items
    _clear_peak_symbol_scatters()

    if not settings.value("legend/symbols_on_peaks", False, type=bool):
        return
    if not settings.value("legend/use_symbols", False, type=bool):
        return
    if not _legend_entries:
        return

    # Build label -> {symbol, color} map from active legend entries
    sym_map = {}
    for entry in _legend_entries:
        sym = entry.get("symbol")
        if sym and entry.get("shown", True):
            sym_map[entry.get("label", "")] = (sym, entry.get("color", QtGui.QColor("#888")))

    if not sym_map:
        return

    sym_y_offset = settings.value("legend/symbol_y_offset", 0.0, type=float)
    sym_size     = settings.value("legend/symbol_size", 10, type=int)

    # Group peaks by approximate m/z so overlapping symbols stack instead of overlap.
    # Each mz_group: [representative_mz, [(sym, color, peak_mz, peak_int), ...]]
    MERGE_TOL = 0.5
    mz_sym_groups: list = []
    for mz_min, mz_max, peak_label, peak_mz, peak_int in highlighted_ranges:
        if peak_label not in sym_map:
            continue
        sym, color = sym_map[peak_label]
        placed = False
        for grp in mz_sym_groups:
            if abs(grp[0] - peak_mz) <= MERGE_TOL:
                grp[1].append((sym, color, peak_mz, peak_int))
                placed = True
                break
        if not placed:
            mz_sym_groups.append([peak_mz, [(sym, color, peak_mz, peak_int)]])

    groups: dict[tuple, tuple[list, list]] = {}  # (sym, color_name) -> ([mz], [y])
    for _rep_mz, sym_entries in mz_sym_groups:
        for stack_n, (sym, color, peak_mz, peak_int) in enumerate(sym_entries):
            color_name = color.name() if isinstance(color, QtGui.QColor) else str(color)
            key = (sym, color_name)
            if key not in groups:
                groups[key] = ([], [])
            y = _symbol_y(peak_int, stack_n + 1, sym_y_offset, _log_y)
            groups[key][0].append(peak_mz)
            groups[key][1].append(y)

    for (sym, color_name), (xs, ys) in groups.items():
        scatter = pg.ScatterPlotItem(
            x=np.array(xs), y=np.array(ys),
            symbol=sym, size=sym_size,
            pen=pg.mkPen(color_name, width=1.2),
            brush=pg.mkBrush(color_name),
        )
        plot.addItem(scatter)
        _peak_symbol_scatter_items.append(scatter)


def _make_cluster_disp(label_mz_pairs):
    """Build a display-label lookup for cluster rows whose label contains '_n'.

    Collects all (label, peak_mz) pairs where the label has '_n', sorts each
    label's m/z values ascending, and assigns n = 1, 2, 3 … in that order.
    Returns a function  disp(label, peak_mz) -> str  that replaces '_n' with
    the corresponding index, or returns the label unchanged for non-cluster rows.
    The global legend label is never touched; only the per-peak plot text is
    expanded this way.
    """
    by_label: dict = {}
    for label, mz in label_mz_pairs:
        if "_n" in label:
            by_label.setdefault(label, []).append(float(mz))

    _sub = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")

    def _to_sub(n):
        return str(n).translate(_sub)

    table: dict = {}  # label -> [(mz, display_label), ...]
    for label, mzs in by_label.items():
        sorted_mzs = sorted(set(mzs))
        label_no_paren = re.sub(r'\s*\([^)]*\)\s*(?=_n)', '', label)
        rows = []
        for i, mz in enumerate(sorted_mzs):
            if i == 0:
                # First peak: no number — remove "_n" entirely
                disp_label = label_no_paren.replace("_n", "")
            else:
                # Subsequent peaks: use Python index i (so second peak → 1, third → 2 …)
                disp_label = label.replace("_n", _to_sub(i))
            rows.append((mz, disp_label))
        table[label] = rows

    def disp(label, peak_mz, tol=1.5):
        if "_n" not in label or label not in table:
            return label
        entries = table[label]
        best = min(entries, key=lambda e: abs(e[0] - peak_mz))
        return best[1] if abs(best[0] - peak_mz) <= tol else label

    return disp


def _draw_peak_labels(show_labels, show_masses, mass_threshold_abs,
                      colored=False, font_size=1.0):
    global _label_text_items
    for item in _label_text_items:
        try: plot.removeItem(item)
        except Exception: pass
    _label_text_items.clear()
    _clear_peak_symbol_scatters()
    _any_row_override = any(
        row.get("L_state", [0])[0] != 0 or row.get("1L_state", [0])[0] != 0
        for row in custom_peak_rows
    )
    if not show_labels and not show_masses and not _any_row_override:
        _draw_peak_symbols_on_plot()
        return

    # Build cluster-index display function for labels containing '_n'
    _cdisp = _make_cluster_disp(
        [(peak_label, peak_mz)
         for _, _, peak_label, peak_mz, _ in highlighted_ranges
         if "_n" in peak_label])

    BASE_PT   = settings.value("label_font_pt",  10, type=int)
    ANGLE     = settings.value("label_angle_deg", 60, type=int)
    MERGE_TOL = 0.5

    # ── Group overlapping entries ──────────────────────────────────────────
    # Each group: (representative mid_mz, representative peak_mz, peak_int, [labels], [colors])
    groups = []
    for entry in highlighted_ranges:
        mz_min, mz_max, peak_label, peak_mz, peak_int = entry
        mid_mz = (mz_min + mz_max) / 2.0

        # Find existing group within tolerance
        merged = False
        for g in groups:
            if abs(g["peak_mz"] - peak_mz) <= MERGE_TOL:
                if peak_label and peak_label not in g["labels"]:
                    g["labels"].append(peak_label)
                # Keep the highest-intensity representative for y-position
                if peak_int > g["peak_int"]:
                    g["peak_mz"]  = peak_mz
                    g["mid_mz"]   = mid_mz
                    g["peak_int"] = peak_int
                # Collect color for this label
                if colored and peak_label:
                    for row in custom_peak_rows:
                        if row["label_input"].text().strip() == peak_label:
                            g["colors"].append(row["color"][0].name())
                            break
                merged = True
                break

        if not merged:
            color = 'k'
            if colored and peak_label:
                for row in custom_peak_rows:
                    if row["label_input"].text().strip() == peak_label:
                        color = row["color"][0].name()
                        break
            groups.append({
                "mid_mz":   mid_mz,
                "peak_mz":  peak_mz,
                "peak_int": peak_int,
                "labels":   [peak_label] if peak_label else [],
                "colors":   [color] if (colored and peak_label) else [],
            })

    # ── Per-row L / 1L override table ────────────────────────────────────
    # label -> (show_mass, show_label) for all peaks in that row
    _row_all   = {}
    # label -> (show_mass, show_label) for the first (min-mz) peak only
    _row_first = {}
    _row_first_mz = {}   # label -> minimum peak_mz seen in highlighted_ranges

    for row in custom_peak_rows:
        if not row["checkbox"].isChecked():
            continue
        lbl    = row["label_input"].text().strip()
        state  = row.get("L_state",  [0])[0]
        fstate = row.get("1L_state", [0])[0]
        _gd_row = _get_group_data_for_row(row)
        _l_active  = _gd_row.get("L_active",  True) if _gd_row else True
        _1l_active = _gd_row.get("1L_active", True) if _gd_row else True
        if state  != 0 and _l_active:
            _row_all[lbl]   = (state  in (1, 3), state  in (2, 3))
        if fstate != 0 and _1l_active:
            _row_first[lbl] = (fstate in (1, 3), fstate in (2, 3))

    for _, _, peak_label, peak_mz, _ in highlighted_ranges:
        if peak_label in _row_first:
            if peak_label not in _row_first_mz or peak_mz < _row_first_mz[peak_label]:
                _row_first_mz[peak_label] = peak_mz

    # ── Pre-compute symbol stack counts per m/z (for label y-positioning) ───
    show_int      = getattr(show_integers_toggle, 'isChecked', lambda: False)()
    label_y_extra = settings.value("label_y_offset", 0.0, type=float)
    sym_y_offset  = settings.value("legend/symbol_y_offset", 0.0, type=float)

    _show_sym_on_peaks = (
        settings.value("legend/symbols_on_peaks", False, type=bool) and
        settings.value("legend/use_symbols",      False, type=bool) and
        bool(_legend_entries)
    )
    # mz_sym_groups: [(representative_mz, stack_count), ...]
    mz_sym_groups: list = []
    if _show_sym_on_peaks:
        sym_map_lbl = {e.get("label", ""): True for e in _legend_entries
                       if e.get("symbol") and e.get("shown", True)}
        for _, _, peak_label, peak_mz, _ in highlighted_ranges:
            if peak_label not in sym_map_lbl:
                continue
            placed = False
            for grp in mz_sym_groups:
                if abs(grp[0] - peak_mz) <= MERGE_TOL:
                    grp[1] += 1
                    placed = True
                    break
            if not placed:
                mz_sym_groups.append([peak_mz, 1])

    # ── Draw one TextItem per group ────────────────────────────────────────
    for g in groups:
        sm_list, sl_list, has_override = [], [], False
        for lbl in g["labels"]:
            if lbl in _row_all:
                sm, sl = _row_all[lbl]
                sm_list.append(sm); sl_list.append(sl); has_override = True
            if lbl in _row_first:
                fmz = _row_first_mz.get(lbl)
                if fmz is not None and abs(g["peak_mz"] - fmz) < 0.01:
                    sm, sl = _row_first[lbl]
                    sm_list.append(sm); sl_list.append(sl); has_override = True

        if has_override:
            show_mass_this  = any(sm_list)
            show_label_this = any(sl_list)
        else:
            show_mass_this  = show_masses
            show_label_this = show_labels

        texts = []
        if show_label_this and g["labels"]:
            _pmz = g["peak_mz"]
            _disp_lbls = [_cdisp(lbl, _pmz) for lbl in g["labels"]]
            if settings.value("label_stack_vertical", False, type=bool):
                texts.extend(_disp_lbls)
            else:
                texts.append(", ".join(_disp_lbls))
        if show_mass_this:
            mz_val = g["peak_mz"]
            texts.append(str(int(round(mz_val))) if show_int else f"{mz_val:.2f}")
        if not texts:
            continue

        # For colored mode use the first label's color; otherwise theme color
        if colored and g["colors"]:
            label_color = g["colors"][0]
        else:
            label_color = 'w' if current_display == 'dark' else 'k'

        pt = int(BASE_PT * font_size)
        font = QtGui.QFont()
        font.setPointSize(pt)

        text_item = pg.TextItem(text="\n".join(texts), anchor=(0, 0.5), angle=ANGLE,
                                  color=label_color)
        text_item.setFont(font)

        # Find how many symbols are stacked at this peak so the label sits
        # just above the highest symbol rather than above the raw peak tip.
        sym_count = 0
        for grp_mz, count in mz_sym_groups:
            if abs(grp_mz - g["peak_mz"]) <= MERGE_TOL:
                sym_count = count
                break

        _pi = g["peak_int"]
        if _log_y:
            _y = (np.log10(_pi) + 0.06 + label_y_extra
                  + sym_count * np.log10(1.0 + sym_y_offset)
                  if _pi > 0 else 0)
        else:
            _y = _pi * (1.04 + label_y_extra + sym_count * sym_y_offset)
        text_item.setPos(g["peak_mz"], _y)
        plot.addItem(text_item)
        _label_text_items.append(text_item)

    _draw_peak_symbols_on_plot()

def _draw_auto_peaks(data_df, show_auto, threshold_value, show_masses_all=False, mode="pct"):
    global _auto_peak_scatter
    if _auto_peak_scatter is not None:
        try: plot.removeItem(_auto_peak_scatter)
        except Exception: pass
        _auto_peak_scatter = None

    # ── "Show masses above threshold" independent of auto-detect scatter ──
    # We always need the peak list if show_masses_all is on,
    # even when auto_peaks_toggle is off.
    if show_masses_all and data_df is not None and len(data_df) > 0:
        pk_mz_all, pk_int_all = _get_auto_peaks(data_df, threshold_value, mode)
        for mz, intensity in zip(pk_mz_all, pk_int_all):
            _si = show_integers_toggle.isChecked()
            _angle = settings.value("label_angle_deg", 60, type=int)
            _pt    = settings.value("label_font_pt",   10, type=int)
            _font  = QtGui.QFont(); _font.setPointSize(_pt)
            text_item = pg.TextItem(
                text=str(int(round(mz))) if _si else f"{mz:.2f}",
                anchor=(0.5, 1.0), angle=_angle,
                color='w' if current_display == 'dark' else 'k')
            text_item.setFont(_font)
            y_log = (np.log10(intensity) + 0.04 if intensity > 0 else 0) if _log_y else (intensity * 1.04)
            text_item.setPos(mz, y_log)
            plot.addItem(text_item)
            _label_text_items.append(text_item)

    # ── Auto-detect scatter (red triangles) ──
    if not show_auto or data_df is None or len(data_df) == 0:
        return

    pk_mz, pk_int = _get_auto_peaks(data_df, threshold_value, mode)
    if len(pk_mz) == 0:
        return

    if _log_y:
      y_offset = np.where(pk_int > 0, np.log10(pk_int) + 0.06, pk_int)
    else:
      y_offset = pk_int * 1.06
    _auto_peak_scatter = pg.ScatterPlotItem(
        x=pk_mz, y=y_offset, symbol='t', size=10,
        pen=pg.mkPen('r', width=1), brush=pg.mkBrush(255, 80, 80, 180))
    plot.addItem(_auto_peak_scatter)

# ─────────────────────────────────────────────
#  OPT: Unified cache invalidation
# ─────────────────────────────────────────────
def _clear_all_caches():
    _clear_norm_cache()
    _clear_auto_peaks_cache()
    _clear_highlight_cache()
    global _main_curve
    _main_curve = None
    _invalidate_overlay_curves()

def _draw_envelope_lines(data_df):
    """
    For each peak row with envelope_btn checked and df available,
    connect the tops of the highlighted peaks with a smooth line
    (the 'isotopic envelope' shape).
    """
    if data_df is None or len(data_df) == 0:
        return
    for row in custom_peak_rows:
        if not _row_is_solo_visible(row): continue
        if not row.get("envelope_btn") or not row["envelope_btn"].isChecked(): continue
        _gd_env = _get_group_data_for_row(row)
        if _gd_env is not None and not _gd_env.get("curve_active", True): continue
        peaks = parse_peaks_text(row["peaks_input"].text())
        if len(peaks) < 2: continue

        color = row["color"][0].name()
        tops_mz  = []
        tops_int = []
        from scipy.signal import find_peaks as _fp
        mz_full  = data_df['mz'].values
        int_full = data_df['intensity'].values
        _thr = mz_full >= 10.9
        noise_floor = (
          _estimate_noise_floor(int_full[_thr], n_sigma=3.0)
          if _thr.sum() >= 10 else _DYN_CLIP_FLOOR
        )

        for mz_nom in sorted(peaks):
            shifted = mz_nom + peak_shift(mz_nom)
            coarse  = get_tolerance(shifted)
            mask    = (mz_full >= shifted - coarse) & (mz_full <= shifted + coarse)
            if mask.sum() < 3:
                continue
            idxs      = np.where(mask)[0]
            local_int = int_full[idxs]

            local_peaks, _ = _fp(local_int, height=noise_floor, prominence=noise_floor)
            best = (local_peaks[local_int[local_peaks].argmax()]
                  if len(local_peaks) > 0
                  else int(local_int.argmax()))

            gi = idxs[best]
            tops_mz.append(float(mz_full[gi]))
            tops_int.append(float(int_full[gi]))

        if len(tops_mz) < 2: continue
        tops_mz  = np.array(tops_mz)
        tops_int = np.array(tops_int)

        # Draw a simple connecting line (no fitting - intentional)
        plot.plot(tops_mz, tops_int,
                  pen=pg.mkPen(color, width=2,
                               style=QtCore.Qt.PenStyle.DashLine),
                  symbol='o', symbolSize=6,
                  symbolPen=pg.mkPen(color, width=1),
                  symbolBrush=pg.mkBrush(color))

def _draw_stacked_envelope_lines(sub_plot, data_df, mz_vals, int_vals):
    """
    Draw isotopic envelope curves on a single stacked sub-plot.
    Mirrors _draw_envelope_lines() but uses the zero-floor-normalised data so
    peak tops are found on the same signal that is visually displayed,
    and maps their y-positions to the doubly-normalised int_vals used for rendering.
    Returns a list of plot items for later removal.
    """
    if data_df is None or len(data_df) == 0:
        return []
    created = []
    from scipy.signal import find_peaks as _fp

    norm_df  = normalise_cached(data_df, zero_floor=True)
    norm_mz  = norm_df['mz'].values
    norm_int = norm_df['intensity'].values

    _thr = norm_mz >= 10.9
    noise_floor = (
        _estimate_noise_floor(norm_int[_thr], n_sigma=3.0)
        if _thr.sum() >= 10 else _DYN_CLIP_FLOOR
    )

    for row in custom_peak_rows:
        if not _row_is_solo_visible(row): continue
        if not row.get("envelope_btn") or not row["envelope_btn"].isChecked(): continue
        _gd_senv = _get_group_data_for_row(row)
        if _gd_senv is not None and not _gd_senv.get("curve_active", True): continue
        peaks = parse_peaks_text(row["peaks_input"].text())
        if len(peaks) < 2: continue

        color    = row["color"][0].name()
        tops_mz  = []
        tops_int = []

        for mz_nom in sorted(peaks):
            shifted = mz_nom + peak_shift(mz_nom)
            coarse  = get_tolerance(shifted)
            mask    = (norm_mz >= shifted - coarse) & (norm_mz <= shifted + coarse)
            if mask.sum() < 3: continue
            idxs      = np.where(mask)[0]
            local_int = norm_int[idxs]
            local_peaks, _ = _fp(local_int,
                                  height=noise_floor, prominence=noise_floor)
            best = (local_peaks[local_int[local_peaks].argmax()]
                    if len(local_peaks) > 0 else int(local_int.argmax()))
            gi         = idxs[best]
            peak_mz_r  = float(norm_mz[gi])
            # Map to the doubly-normalised y-axis used for this sub-plot
            bar_idx    = int(np.searchsorted(mz_vals, peak_mz_r).clip(0, len(int_vals) - 1))
            tops_mz.append(peak_mz_r)
            tops_int.append(float(int_vals[bar_idx]))

        if len(tops_mz) < 2: continue
        item = sub_plot.plot(
            np.array(tops_mz), np.array(tops_int),
            pen=pg.mkPen(color, width=2, style=QtCore.Qt.PenStyle.DashLine),
            symbol='o', symbolSize=6,
            symbolPen=pg.mkPen(color, width=1),
            symbolBrush=pg.mkBrush(color))
        created.append(item)
    return created


_stacked_sub_plots      = []
_stacked_mouse_handlers = []
_stacked_click_handlers = []
_stacked_spectra_data   = []   # (data_df, mz_vals, int_vals) per sub-plot, for peak redraws
_stacked_peak_items     = []   # peak overlay items per sub-plot, for peak-only redraws
_stacked_sub_vlines     = []   # one InfiniteLine (vertical) per sub-plot, for X crosshair sync

_stacked_zoom_history    = []   # list of (x_range, y_range) tuples for stacked mode
_stacked_in_zoom_restore = False

# Log-Y mode at the time of the last stacked build; used to decide whether
# the saved Y range is in the same units as the new build's axis.
_stacked_last_log_y  = [None]
_stacked_last_dyn_scale = [True]   # track dyn-scale changes for fast-update path

# Pixels reserved below the last subplot so the x-axis tick labels are never
# clipped by the window edge.  Also used as the layout's bottom content margin.
_STACKED_BOTTOM_PAD = 32

def _stacked_go_to_last_zoom():
    global _stacked_in_zoom_restore, _stacked_y_guard
    if len(_stacked_zoom_history) < 2 or not _stacked_sub_plots:
        return
    _stacked_in_zoom_restore = True
    _stacked_zoom_history.pop()
    xr, yr = _stacked_zoom_history[-1]
    master = _stacked_sub_plots[0]
    master.vb.setXRange(xr[0], xr[1], padding=0)
    _stacked_y_guard = True
    master.vb.setYRange(yr[0], yr[1], padding=0)
    _stacked_y_guard = False
    _stacked_in_zoom_restore = False

def _on_stacked_range_changed(vb, ranges):
    if _stacked_in_zoom_restore or _stacked_y_guard:
        return
    xr = list(ranges[0])
    yr = list(ranges[1])
    if _stacked_zoom_history:
        last_x, _ = _stacked_zoom_history[-1]
        if abs(xr[0] - last_x[0]) < 1e-6 and abs(xr[1] - last_x[1]) < 1e-6:
            return
    _stacked_zoom_history.append((xr, yr))
    if len(_stacked_zoom_history) > 50:
        _stacked_zoom_history.pop(0)

def _draw_stacked_peak_labels(sub_plot, data_df, mz_vals, int_vals, return_items=False, mirrored=False):
    """
    Draw peak highlights and labels onto a single stacked sub-plot.
    Mirrors _draw_peak_labels: respects per-row L / 1L button states and
    the global peak-labels / peak-masses toggles.
    """
    show_lbl    = peak_labels_toggle.isChecked()
    show_masses = peak_masses_toggle.isChecked()
    show_int    = show_integers_toggle.isChecked()
    pk_alpha    = highlight_alpha()
    MERGE_TOL   = 0.5
    BASE_PT     = settings.value("label_font_pt", 9, type=int)
    LABEL_ANGLE = settings.value("label_angle_deg", 60, type=int)
    VERT_STACK  = settings.value("label_stack_vertical", False, type=bool)
    label_y_extra = settings.value("label_y_offset", 0.0, type=float)
    sym_y_offset  = settings.value("legend/symbol_y_offset", 0.0, type=float)
    sym_size      = settings.value("legend/symbol_size", 10, type=int)

    _show_sym_on_peaks = (
        settings.value("legend/symbols_on_peaks", False, type=bool) and
        settings.value("legend/use_symbols",      False, type=bool) and
        bool(_legend_entries)
    )

    # Build symbol stacking list before drawing labels so that label y-positions
    # know the total stack height at each m/z.
    # Mirrors _draw_peak_symbols_on_plot: one entry per peak occurrence per row
    # (NOT deduplicated by label) so multiple peaks from the same row at the
    # same m/z each produce a separate stacked symbol.
    # _mz_sym_stack: [[rep_mz, [(sym, color_name), ...]], ...]
    _mz_sym_stack: list = []
    if _show_sym_on_peaks:
        _sym_map_pre = {e["label"]: (e["symbol"], e.get("color", QtGui.QColor("#888")))
                        for e in _legend_entries if e.get("symbol") and e.get("shown", True)}
        for _ri_pre, _row_pre in enumerate(custom_peak_rows):
            if not _row_is_solo_visible(_row_pre):
                continue
            _lbl_pre = _row_pre["label_input"].text().strip() or "Custom peaks"
            if _lbl_pre not in _sym_map_pre:
                continue
            _peaks_pre = parse_peaks_text(_row_pre["peaks_input"].text())
            if not _peaks_pre:
                continue
            _sym_pre, _col_pre = _sym_map_pre[_lbl_pre]
            _col_name_pre = (_col_pre.name() if isinstance(_col_pre, QtGui.QColor)
                             else str(_col_pre))
            _flat_pre = [p for _g in _peaks_pre
                         for p in (_g if isinstance(_g, (list, tuple)) else [_g])]
            for _pm_target in _flat_pre:
                _idx_pre = np.argmin(np.abs(mz_vals - _pm_target))
                _pm = float(mz_vals[_idx_pre])
                _placed_pre = False
                for _gs in _mz_sym_stack:
                    if abs(_gs[0] - _pm) <= MERGE_TOL:
                        _gs[1].append((_sym_pre, _col_name_pre))
                        _placed_pre = True
                        break
                if not _placed_pre:
                    _mz_sym_stack.append([_pm, [(_sym_pre, _col_name_pre)]])

    theme_color = 'w' if current_display == 'dark' else 'k'
    created_items = []

    # ── Per-row L / 1L override table (mirrors _draw_peak_labels) ──────────
    # State encoding: 0=off, 1=mass only, 2=label only, 3=mass+label
    _row_all   = {}   # lbl -> (show_mass, show_label)  for every peak in the row
    _row_first = {}   # lbl -> (show_mass, show_label)  for the first (lowest m/z) peak only
    for row in custom_peak_rows:
        if not _row_is_solo_visible(row):
            continue
        rlbl   = row["label_input"].text().strip() or "Custom peaks"
        state  = row.get("L_state",  [0])[0]
        fstate = row.get("1L_state", [0])[0]
        _gd_row = _get_group_data_for_row(row)
        _l_active  = _gd_row.get("L_active",  True) if _gd_row else True
        _1l_active = _gd_row.get("1L_active", True) if _gd_row else True
        if state  != 0 and _l_active:
            _row_all[rlbl]   = (state  in (1, 3), state  in (2, 3))
        if fstate != 0 and _1l_active:
            _row_first[rlbl] = (fstate in (1, 3), fstate in (2, 3))
    _any_override = bool(_row_all or _row_first)

    # Track lowest m/z seen per label (needed for 1L "first peak" logic)
    _first_mz: dict[str, float] = {}

    # Use the already-stacked-normalised int_vals for bound finding so that
    # the highlighted region matches what is visually displayed in the sub-plot.
    # Clip to [0, 1]: peaks below the threshold can exceed 1 after re-scaling and
    # would otherwise mislead find_peak_bounds.
    _bounds_int = np.clip(int_vals, 0, 1.0)
    _thr_mask   = mz_vals >= 10.9
    _stk_noise  = (
        _estimate_noise_floor(_bounds_int[_thr_mask], n_sigma=3.0)
        if _thr_mask.sum() >= 10 else _DYN_CLIP_FLOOR
    )

    # norm_mz is used for peak snapping (same mz grid, just for nearest-index lookup)
    norm_mz = mz_vals

    # Pre-pass: collect snapped (label, peak_mz) pairs for cluster rows so
    # _make_cluster_disp can assign n-indices in m/z-ascending order.
    _cn_pairs = []
    for _row_pre in custom_peak_rows:
        if not _row_is_solo_visible(_row_pre):
            continue
        _lbl_cn = _row_pre["label_input"].text().strip() or "Custom peaks"
        if "_n" not in _lbl_cn:
            continue
        _pks_cn = parse_peaks_text(_row_pre["peaks_input"].text())
        _flat_cn = [p for _g in _pks_cn
                    for p in (_g if isinstance(_g, (list, tuple)) else [_g])]
        for _pm_cn in _flat_cn:
            _idx_cn = np.argmin(np.abs(norm_mz - _pm_cn))
            _cn_pairs.append((_lbl_cn, float(norm_mz[_idx_cn])))
    _cdisp = _make_cluster_disp(_cn_pairs)

    groups = []  # list of {peak_mz, peak_int, labels, colors}

    _foc_idx = _conf_focused_row_idx[0]
    for _ri, row in enumerate(custom_peak_rows):
        if not _row_is_solo_visible(row):
            continue
        peaks = parse_peaks_text(row["peaks_input"].text())
        if not peaks:
            continue
        color_str = row["color"][0].name()
        lbl       = row["label_input"].text().strip() or "Custom peaks"
        _galpha   = _get_group_alpha_for_row(row)
        _ralpha   = _galpha if _foc_idx < 0 or _ri == _foc_idx else max(_galpha // 4, 15)

        peaks_flat = []
        for grp in peaks:
            if not isinstance(grp, (list, tuple)):
                grp = [grp]
            peaks_flat.extend(grp)

        bg_color = pg.mkColor(theme_color)
        hi_color = apply_alpha(color_str, _ralpha)
        _row_segs_mz  = []
        _row_segs_int = []

        for peak_mz_target in peaks_flat:
            idx      = np.argmin(np.abs(norm_mz - peak_mz_target))
            peak_mz  = float(norm_mz[idx])
            peak_int = float(int_vals[idx])

            # Track lowest m/z for 1L logic
            if lbl not in _first_mz or peak_mz < _first_mz[lbl]:
                _first_mz[lbl] = peak_mz

            # Compute highlight bounds from the stacked-normalised data so the
            # highlighted region matches the visually displayed peak width.
            shifted_p = peak_mz_target + peak_shift(peak_mz_target)
            coarse    = get_tolerance(shifted_p)
            bounds    = find_peak_bounds(mz_vals, _bounds_int, shifted_p,
                                         noise_floor=_stk_noise, coarse_tol=coarse, grace=3)

            if bounds is not None:
                mz_lo, mz_hi = bounds[0], bounds[1]
                mask     = (mz_vals >= mz_lo) & (mz_vals <= mz_hi)
                mz_arr   = mz_vals[mask]
                norm_arr = int_vals[mask]
            else:
                # Fallback: ±10-point window around the nearest sample
                n     = 10
                start = max(0, idx - n)
                end   = min(len(mz_vals), idx + n + 1)
                mz_arr   = mz_vals[start:end]
                norm_arr = int_vals[start:end]

            # Use the actual maximum within the highlighted region for symbol/label
            # placement. The initial peak_int = int_vals[idx] is only the nearest
            # sample, which may be below the true peak if the target m/z doesn't
            # land exactly on a spectral data point.
            if len(norm_arr) > 0:
                _max_i   = int(np.argmax(norm_arr))
                peak_int = float(norm_arr[_max_i])
                peak_mz  = float(mz_arr[_max_i])

            if len(mz_arr) >= 2:
                _row_segs_mz.append(mz_arr)
                _row_segs_int.append(norm_arr)

            # Merge nearby peaks into a single label group
            merged = False
            for g in groups:
                if abs(g["peak_mz"] - peak_mz) <= MERGE_TOL:
                    if lbl and lbl not in g["labels"]:
                        g["labels"].append(lbl)
                        g["colors"].append(color_str)
                    if peak_int > g["peak_int"]:
                        g["peak_mz"]  = peak_mz
                        g["peak_int"] = peak_int
                    merged = True
                    break
            if not merged:
                groups.append({
                    "peak_mz":  peak_mz,
                    "peak_int": peak_int,
                    "labels":   [lbl] if lbl else [],
                    "colors":   [color_str],
                })

        if _row_segs_mz:
            _nan = np.array([np.nan])
            _cmz  = np.concatenate([np.concatenate([s, _nan]) for s in _row_segs_mz])
            _cint = np.concatenate([np.concatenate([s, _nan]) for s in _row_segs_int])
            sub_plot.plot(_cmz, _cint, pen=pg.mkPen(bg_color, width=3))
            sub_plot.plot(_cmz, _cint, pen=pg.mkPen(hi_color, width=3))
            if return_items:
                created_items.extend(sub_plot.listDataItems()[-2:])

    # ── Draw one TextItem per group ────────────────────────────────────────
    for g in groups:
        texts = []
        g_mz  = g["peak_mz"]

        if not _any_override:
            # No per-row overrides — use global toggles only
            if show_lbl and g["labels"]:
                _dl = [_cdisp(lbl, g_mz) for lbl in g["labels"]]
                if VERT_STACK:
                    texts.extend(_dl)
                else:
                    texts.append(", ".join(_dl))
            if show_masses:
                texts.append(str(int(round(g_mz))) if show_int else f"{g_mz:.2f}")
        else:
            # Per-row L / 1L override logic (same as _draw_peak_labels)
            show_mass_this = False
            labels_this    = []
            for rlbl in g["labels"]:
                is_first = abs(g_mz - _first_mz.get(rlbl, g_mz)) < MERGE_TOL
                if rlbl in _row_all:
                    sm, sl = _row_all[rlbl]
                else:
                    sm, sl = show_masses, show_lbl
                if is_first and rlbl in _row_first:
                    fsm, fsl = _row_first[rlbl]
                    sm = sm or fsm
                    sl = sl or fsl
                if sm:
                    show_mass_this = True
                if sl:
                    labels_this.append(rlbl)
            if labels_this:
                _dl = [_cdisp(lbl, g_mz) for lbl in labels_this]
                if VERT_STACK:
                    texts.extend(_dl)
                else:
                    texts.append(", ".join(_dl))
            if show_mass_this:
                texts.append(str(int(round(g_mz))) if show_int else f"{g_mz:.2f}")

        if not texts:
            continue

        label_color = g["colors"][0] if g["colors"] else theme_color
        font = QtGui.QFont()
        font.setPointSize(BASE_PT)

        text_item = pg.TextItem(
            text="\n".join(texts),
            anchor=(0, 0.5),
            angle=(LABEL_ANGLE if not mirrored else -LABEL_ANGLE),
            color=label_color,
        )
        text_item.setFont(font)

        # Y position: mirrors _draw_peak_labels — respects label_y_offset and
        # pushes the label above the highest stacked symbol at this peak.
        sym_count_this = 0
        for _gs in _mz_sym_stack:
            if abs(_gs[0] - g["peak_mz"]) <= MERGE_TOL:
                sym_count_this = len(_gs[1])
                break
        _pi = g["peak_int"]
        if _log_y and _pi > 0:
            y_pos = (np.log10(_pi) + 0.06 + label_y_extra
                     + sym_count_this * np.log10(1.0 + sym_y_offset))
        else:
            y_pos = _pi * (1.04 + label_y_extra + sym_count_this * sym_y_offset)

        text_item.setPos(g_mz, y_pos)
        sub_plot.addItem(text_item)
        if return_items:
            created_items.append(text_item)

    # ── Legend symbols on this sub-plot ──────────────────────────────────────
    if _show_sym_on_peaks and _mz_sym_stack:
        sym_scatter_groups: dict = {}
        for _gs in _mz_sym_stack:
            # Use the label-group peak_int (actual max) as the base y so that
            # symbol positions are consistent with label positions.
            _base_int = 0.0
            for g in groups:
                if abs(g["peak_mz"] - _gs[0]) <= MERGE_TOL:
                    _base_int = g["peak_int"]
                    break
            if _base_int == 0.0:
                _idx_fb = np.argmin(np.abs(norm_mz - _gs[0]))
                _base_int = float(int_vals[_idx_fb])
            for stack_n, (sym, color_name) in enumerate(_gs[1]):
                y = _symbol_y(_base_int, stack_n + 1, sym_y_offset,
                              _log_y and _base_int > 0)
                key = (sym, color_name)
                if key not in sym_scatter_groups:
                    sym_scatter_groups[key] = ([], [])
                sym_scatter_groups[key][0].append(_gs[0])
                sym_scatter_groups[key][1].append(y)
        for (sym, color_name), (xs, ys) in sym_scatter_groups.items():
            sc = pg.ScatterPlotItem(
                x=np.array(xs), y=np.array(ys),
                symbol=sym, size=sym_size,
                pen=pg.mkPen(color_name, width=1.2),
                brush=pg.mkBrush(color_name),
            )
            sub_plot.addItem(sc)
            if return_items:
                created_items.append(sc)

    if return_items:
        return created_items

def _show_stacked_single_file_notice():
    """Show a temporary centred overlay on plot_widget for 4 s."""
    notice = QtWidgets.QLabel(
        "Stacked mode is active\n1 file loaded",
        plot_widget,
    )
    notice.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
    notice.setStyleSheet(
        "QLabel {"
        "  background: rgba(50, 50, 50, 210);"
        "  color: #f0f0f0;"
        "  border-radius: 10px;"
        "  padding: 12px 22px;"
        "  font-size: 12pt;"
        "}"
    )
    notice.adjustSize()
    pw_w, pw_h = plot_widget.width(), plot_widget.height()
    notice.move(
        (pw_w - notice.width()) // 2,
        (pw_h - notice.height()) // 2,
    )
    notice.show()
    notice.raise_()
    QtCore.QTimer.singleShot(4000, notice.deleteLater)

def _build_stacked_layout(spectra_list, restore_xrange=None, restore_yrange=None):
    """
    Hide the main plot, then add N sub-plots starting at row 1.
    Row 0 is collapsed to zero height so no empty axes appear at the top.
    spectra_list: list of (data_df, display_name, pen_color)
    restore_xrange: optional (xmin, xmax) to restore after build instead of auto-ranging.
    restore_yrange: optional (ymin, ymax) to restore after build instead of auto-ranging.
    """
    global _stacked_sub_plots, _stacked_spectra_data, _stacked_peak_items, _stacked_build_gen
    _stacked_build_gen += 1
    _my_gen = _stacked_build_gen

    # Suppress all intermediate paints for the duration of the build so neither
    # partial sub-plot trees nor mid-layout-pass geometry glitches are visible.
    plot_widget.setUpdatesEnabled(False)
    _stacked_peak_items = []
    _stacked_spectra_data = []
    _destroy_stacked_layout()
    # No processEvents() here — that would let the render timer fire
    # re-entrantly, tearing down the layout we are about to build.

    n = len(spectra_list)
    if n == 0:
        plot_widget.setUpdatesEnabled(True)
        return

    theme_color = 'k' if current_display == 'bright' else 'w'
    link_x = None
    link_y = None

    # Pre-compute global intensity max for raw (non-normalised) display
    _raw_y_max = None
    if not _stacked_dyn_scale:
        lo_thresh_pre = _view_mz_lower_spin.value()
        _raw_maxima = []
        for _df_pre, _, _ in spectra_list:
            _mz_pre  = _df_pre['mz'].values
            _int_pre = np.clip(_df_pre['intensity'].values.astype(float), 0, None)
            _mask    = _mz_pre >= lo_thresh_pre
            if _mask.any() and _int_pre[_mask].max() > 0:
                _raw_maxima.append(_int_pre[_mask].max())
        if _raw_maxima:
            _raw_y_max = max(_raw_maxima) * 1.05

    # Hide the main plot item entirely, then collapse its row to 0 px.
    # Both steps are needed: hiding suppresses painting; collapsing the row
    # prevents the layout from reserving space for the invisible item.
    plot.setVisible(False)
    try:
        plot_widget.ci.layout.setRowMaximumHeight(0, 0)
        plot_widget.ci.layout.setRowMinimumHeight(0, 0)
        plot_widget.ci.layout.setRowStretchFactor(0, 0)
        plot_widget.ci.layout.invalidate()
    except Exception:
        pass

    for i, (data_df, name, color) in enumerate(spectra_list):
        # Use rows 1..N so row 0 (the hidden main plot) is not disturbed
        sub = plot_widget.addPlot(row=i + 1, col=0)
        if i == n - 1:
            sub.setLabel('bottom', 'm/z')
        else:
            # Hide tick labels and the axis line, but keep the axis item so
            # pyqtgraph still draws the X grid lines for this row.
            ax = sub.getAxis('bottom')
            ax.setStyle(showValues=False)   # no tick numbers
            ax.setPen(pg.mkPen(None))       # invisible axis line
            ax.setTickPen(pg.mkPen(theme_color))  # grid lines use tickPen, not axis pen
            ax.setHeight(1)                 # 1 px — invisible but lets grid lines paint
        sub.setLabel('left', name, size='9pt')
        # Stronger gridlines: higher alpha, both axes; honour the current grid toggle
        _grid_on = grid_action.isChecked()
        sub.showGrid(x=_grid_on, y=_grid_on, alpha=0.5)
        sub.setLogMode(x=False, y=_log_y)
        sub.setMenuEnabled(True)   # keep menu so "View All" / right-click works

        # Replace pyqtgraph's default multi-section context menu with a lean one
        # that has "View All" and "Return to last zoom".
        lean_menu = QtWidgets.QMenu()
        view_all_act = QtWidgets.QAction("View All", lean_menu)

        def _make_view_all():
            def _do():
                global _stacked_y_guard
                if not _stacked_sub_plots:
                    return
                lo = _view_mz_lower_spin.value()
                all_mz = []
                per_sp_int = [[] for _ in _stacked_sub_plots]
                for si, sp in enumerate(_stacked_sub_plots):
                    for curve in sp.listDataItems():
                        xd, yd = curve.getData()
                        if xd is None or len(xd) == 0:
                            continue
                        mask = xd >= lo
                        if mask.any():
                            all_mz.append(xd[mask])
                            per_sp_int[si].append(yd[mask])
                if not all_mz:
                    # Fallback: no data above threshold, just auto-range
                    m = _stacked_sub_plots[0]
                    m.vb.enableAutoRange(axis=m.vb.XAxis, enable=True)
                    m.vb.updateAutoRange()
                    m.vb.enableAutoRange(axis=m.vb.XAxis, enable=False)
                    return
                mz_all = np.concatenate(all_mz)
                x_min, x_max = float(mz_all.min()), float(mz_all.max())
                pad = (x_max - x_min) * 0.02
                _stacked_sub_plots[0].vb.setXRange(x_min - pad, x_max + pad, padding=0)
                # Set each subplot's Y from its own data (skip only if Lock Y is active)
                if not _stacked_lock_y:
                    _stacked_y_guard = True
                    for si, sp in enumerate(_stacked_sub_plots):
                        if not per_sp_int[si]:
                            continue
                        int_cat = np.concatenate(per_sp_int[si])
                        if _log_y:
                            pos = int_cat[int_cat > 0]
                            if len(pos) == 0:
                                continue
                            y_lo = float(np.log10(pos.min()))
                            y_hi = float(np.log10(pos.max()))
                            pad_y = max((y_hi - y_lo) * 0.05, 0.3)
                            sp.vb.setYRange(y_lo - pad_y, y_hi + pad_y, padding=0)
                        else:
                            y_max = float(int_cat.max())
                            pad_y = y_max * 0.05
                            sp.vb.setYRange(0.0, y_max + pad_y, padding=0)
                    _stacked_y_guard = False
            return _do

        view_all_act.triggered.connect(_make_view_all())
        lean_menu.addAction(view_all_act)
        last_zoom_act = QtWidgets.QAction("Return to last zoom", lean_menu)
        last_zoom_act.triggered.connect(_stacked_go_to_last_zoom)
        lean_menu.addAction(last_zoom_act)
        sub.vb.menu = lean_menu

        # ── Per-subplot crosshair lines and mouse hover ──
        sub_vline = pg.InfiniteLine(angle=90, movable=False,
            pen=pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
        sub_hline = pg.InfiniteLine(angle=0,  movable=False,
            pen=pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
        sub_vline.setVisible(crosshair_action.isChecked())
        sub_hline.setVisible(crosshair_action.isChecked())
        sub.addItem(sub_vline, ignoreBounds=True)
        sub.addItem(sub_hline, ignoreBounds=True)
        _stacked_sub_vlines.append(sub_vline)

        sub_label = pg.LabelItem(justify='right')
        sub_label.setParentItem(sub.vb)
        sub_label.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-6, 4))
        sub_label.setText("")

        def _make_sub_mouse_handler(_sub, _vl, _hl, _lbl, _name):
            def _on_mouse(pos):
                if _sub.sceneBoundingRect().contains(pos):
                    mp = _sub.vb.mapSceneToView(pos)
                    x, y = mp.x(), mp.y()
                    if crosshair_action.isChecked():
                        # Propagate vertical line to ALL subplots at same X
                        for _svl in _stacked_sub_vlines:
                            _svl.setPos(x)
                        _hl.setPos(y)
                    _cif_pt = int(settings.value("cursor_info_font_pt", 9))
                    if _log_y:
                        y_disp = 10 ** y
                        _lbl.setText(f"<span style='font-size:{_cif_pt}pt'>m/z={x:.4f}  I={y_disp:.4f}</span>")
                    else:
                        _lbl.setText(f"<span style='font-size:{_cif_pt}pt'>m/z={x:.4f}  I={y:.4f}</span>")
                    # Floating m/z label near cursor
                    if mz_cursor_action.isChecked():
                        show_int = show_integers_toggle.isChecked()
                        mz_text = str(int(round(x))) if show_int else f"{x:.2f}"
                        _mz_cursor_label.setText(mz_text)
                        _mz_cursor_label.adjustSize()
                        scene_pt = QtCore.QPointF(pos)
                        widget_pt = (plot_widget.mapFromScene(scene_pt)
                                     if hasattr(plot_widget, 'mapFromScene')
                                     else QtCore.QPoint(int(pos.x()), int(pos.y())))
                        lx = int(widget_pt.x()) + 12
                        ly = int(widget_pt.y()) - _mz_cursor_label.height() - 4
                        lx = max(0, min(lx, plot_widget.width()  - _mz_cursor_label.width()))
                        ly = max(0, min(ly, plot_widget.height() - _mz_cursor_label.height()))
                        _mz_cursor_label.move(lx, ly)
                        _mz_cursor_label.setVisible(True)
                        _mz_cursor_label.raise_()
                    else:
                        _mz_cursor_label.setVisible(False)
                else:
                    _lbl.setText("")
            return _on_mouse

        _stacked_mouse_handlers.append(
            _make_sub_mouse_handler(sub, sub_vline, sub_hline, sub_label, name))
        sub.scene().sigMouseMoved.connect(_stacked_mouse_handlers[-1])

        def _stacked_click_handler(event):
            if not pick_mode_action.isChecked(): return
            if event.button() != QtCore.Qt.MouseButton.LeftButton: return
            pos = event.scenePos()
            for i, sub in enumerate(_stacked_sub_plots):
                if not sub.sceneBoundingRect().contains(pos): continue
                mp = sub.vb.mapSceneToView(pos)
                clicked_mz = mp.x()
                data_df = _stacked_spectra_data[i][0] if i < len(_stacked_spectra_data) else None
                row_idx = pick_row_combo.currentIndex()
                if row_idx < 0 or row_idx >= len(custom_peak_rows): return
                row = custom_peak_rows[row_idx]
                double = event.double() if hasattr(event, 'double') else False
                if double:
                    result = _find_nearest_peak_in_row(row, clicked_mz)
                    if result is None: return
                    push_peak_history()
                    peaks = parse_peaks_text(row["peaks_input"].text())
                    peaks.pop(result[0])
                    row["peaks_input"].setText(", ".join(f"{p:.4f}" for p in peaks))
                    _clear_highlight_cache()
                else:
                    snap_mz = clicked_mz
                    if data_df is not None and len(data_df) > 0:
                        idx = (data_df['mz'] - clicked_mz).abs().idxmin()
                        snap_mz = round(float(data_df.loc[idx, 'mz']), 4)
                    push_peak_history()
                    current = row["peaks_input"].text().strip()
                    new_val = f"{snap_mz:.4f}"
                    row["peaks_input"].setText(current + f", {new_val}" if current else new_val)
                    _clear_highlight_cache()
                return  # only handle the first matching sub-plot

        if _stacked_dyn_scale:
            norm = normalise_cached(data_df, zero_floor=True)
            mz_vals = norm['mz'].values
            int_vals = norm['intensity'].values
            # Re-normalise so the threshold-filtered region fills 0→1.
            lo_thresh = _view_mz_lower_spin.value()
            above_mask = mz_vals >= lo_thresh
            if above_mask.any():
                above_max = int_vals[above_mask].max()
                if above_max > 0:
                    int_vals = int_vals / above_max
            int_vals = np.clip(int_vals, 0, None)
        else:
            mz_vals  = data_df['mz'].values
            int_vals = np.clip(data_df['intensity'].values.astype(float), 0, None)

        if _sigma3_clip:
            sigma3_floor = _estimate_noise_floor(int_vals, n_sigma=_sigma3_n_sigma)
            sigma3_floor = max(sigma3_floor, 1e-6)
            int_vals = np.clip(int_vals, sigma3_floor, None)
        elif _log_y:
            sigma3_floor = 1e-6
            int_vals = np.clip(int_vals, sigma3_floor, None)
        else:
            sigma3_floor = None

        sub.plot(mz_vals, int_vals, pen=pg.mkPen(color, width=1), name=name)

        if link_x is None:
            link_x = sub
        else:
            sub.setXLink(link_x)

        sub.vb.setMouseEnabled(y=True)
        # Determine if this row should be mirrored (Y inverted)
        _mirror_this = False
        if _stacked_mirror:
            mirror_set = (0 if _stacked_mirror_odd else 1)  # which modulo to flip
            _mirror_this = (i % 2 == mirror_set)
        if _log_y:
            log_floor = np.log10(sigma3_floor) if sigma3_floor else -6
            if _mirror_this:
                sub.vb.setYRange(0, log_floor, padding=0)
                sub.getAxis('left').setStyle(tickTextOffset=2)
                sub.vb.invertY(True)
            else:
                sub.vb.setYRange(log_floor, 0, padding=0)
                sub.vb.invertY(False)
        else:
            y_top = (_raw_y_max if not _stacked_dyn_scale and _raw_y_max else 1.05)
            if _mirror_this:
                sub.vb.setYRange(0, y_top, padding=0)
                sub.vb.invertY(True)
            else:
                sub.vb.setYRange(0, y_top, padding=0)
                sub.vb.invertY(False)

        if link_y is None:
            link_y = sub
        else:
            sub.setYLink(link_y)
        sub.vb.sigRangeChanged.connect(_enforce_stacked_y)
        # Make all rows the same pixel height by setting a fixed row stretch
        plot_widget.ci.layout.setRowStretchFactor(i + 1, 1)

        # Store data for peak-only redraws (so we don't need to re-normalise)
        _stacked_spectra_data.append((data_df, mz_vals, int_vals))

        # ── Peak highlights, labels, and envelope curves for this sub-plot ──
        items = _draw_stacked_peak_labels(sub, data_df, mz_vals, int_vals,
                                          return_items=True, mirrored=_mirror_this)
        env_items = _draw_stacked_envelope_lines(sub, data_df, mz_vals, int_vals)
        _stacked_peak_items.append((items or []) + env_items)

        _stacked_sub_plots.append(sub)

    plot.scene().sigMouseClicked.connect(_stacked_click_handler)
    _stacked_click_handlers.append(_stacked_click_handler)

    # ── Zoom history tracking for "Return to last zoom" ───────────────
    _stacked_zoom_history.clear()
    if _stacked_sub_plots:
        _stacked_sub_plots[0].vb.sigRangeChanged.connect(_on_stacked_range_changed)

    # ── Peak-list legend on the top sub-plot ──────────────────────────
    # Called here so the legend appears on the stacked view immediately.
    # _rebuild_peak_legend_on_plot() detects stacked mode and anchors to
    # the first sub-plot's viewbox instead of the hidden main plot.vb.
    _rebuild_peak_legend_on_plot()

    _apply_stacked_y()


    # Give every sub-plot row an equal stretch factor and a small minimum,
    # but NO maximum — that lets Qt distribute available height freely on resize.
    # The bottom content margin reserves space so the last subplot's x-axis
    # tick labels and axis label are never clipped by the window edge.
    for i in range(n):
        plot_widget.ci.layout.setRowMinimumHeight(i + 1, 40)
        plot_widget.ci.layout.setRowMaximumHeight(i + 1, 16777215)
        plot_widget.ci.layout.setRowStretchFactor(i + 1, 1)
    plot_widget.ci.layout.setContentsMargins(0, 0, 0, _STACKED_BOTTOM_PAD)
    plot_widget.ci.layout.activate()
    # No processEvents() — updates are suppressed; layout will settle on the
    # first paint after setUpdatesEnabled(True) below.

    # Wire a resize handler that re-equalises row heights whenever the widget
    # changes size. We store it so _destroy_stacked_layout can disconnect it.
    def _on_stacked_resize(event):
        if not _stacked_sub_plots:
            return
        total_h = plot_widget.height()
        n_rows = len(_stacked_sub_plots)
        # Subtract the bottom-pad margin so rows never touch the window edge.
        effective_h = max(n_rows * 40, total_h - _STACKED_BOTTOM_PAD)
        rh = max(40, effective_h // n_rows)
        layout = plot_widget.ci.layout
        for idx in range(n_rows):
            layout.setRowMinimumHeight(idx + 1, rh)
            layout.setRowMaximumHeight(idx + 1, rh)
        # Do NOT call layout.activate() here — it resets column widths and
        # causes plots to collapse to a fraction of the horizontal space.
        # Qt's geometry pass triggered by resizeEvent handles the reflow correctly.
        plot_widget.ci.layout.invalidate()

    plot_widget._stacked_resize_handler = _on_stacked_resize
    _orig_resize = getattr(plot_widget, '_orig_resize_event', plot_widget.resizeEvent)
    plot_widget._orig_resize_event = _orig_resize
    _stacked_resize_timer = QtCore.QTimer()
    _stacked_resize_timer.setSingleShot(True)
    _stacked_resize_timer.setInterval(150)
    _stacked_resize_timer.timeout.connect(lambda: _on_stacked_resize(None))
    plot_widget._stacked_resize_timer = _stacked_resize_timer
    def _patched_resize(event):
        _orig_resize(event)
        _stacked_resize_timer.start()
    plot_widget.resizeEvent = _patched_resize

    _apply_stacked_y()

    # Pre-set the X range before the first paint so the view doesn't jump to
    # auto-range during the 100 ms window before _nudge_stacked fires.
    if _stacked_sub_plots and restore_xrange is not None:
        try:
            _stacked_sub_plots[0].vb.setXRange(restore_xrange[0], restore_xrange[1], padding=0)
        except Exception:
            pass

    # Re-enable painting — Qt now does a single clean paint of the fully-built
    # layout instead of a series of intermediate states.
    plot_widget.setUpdatesEnabled(True)
    plot_widget.update()

    def _nudge_stacked():
        # Bail out if a newer build has already superseded this one.
        if _stacked_build_gen != _my_gen:
            return

        # Capture the original resize event into a local variable now, before
        # we unhook anything. _patched_resize_local will close over this local
        # so it never needs to look it up on plot_widget again (where it may
        # already have been removed).
        _captured_orig = getattr(plot_widget, '_orig_resize_event', None)

        # Temporarily unhook the patched resize so the nudge resizes don't
        # trigger layout.activate() and wipe the zoom we're about to restore.
        if _captured_orig is not None:
            plot_widget.resizeEvent = _captured_orig

        sz = plot_widget.size()
        plot_widget.resize(sz.width() + 1, sz.height())
        app.processEvents()
        plot_widget.resize(sz.width() - 1, sz.height())
        plot_widget.updateGeometry()

        # Re-hook the patched resize now that the nudge is done.
        def _patched_resize_local(event):
            if _captured_orig is not None:
                _captured_orig(event)
            if hasattr(plot_widget, '_stacked_resize_timer'):
                plot_widget._stacked_resize_timer.start()
        plot_widget.resizeEvent = _patched_resize_local

        if _stacked_sub_plots:
            master = _stacked_sub_plots[0]
            if restore_xrange is not None:
                master.vb.setXRange(restore_xrange[0], restore_xrange[1], padding=0)
            else:
                master.vb.enableAutoRange(axis=master.vb.XAxis, enable=True)
                master.vb.updateAutoRange()
                master.vb.enableAutoRange(axis=master.vb.XAxis, enable=False)
            if restore_yrange is not None:
                master.vb.setYRange(restore_yrange[0], restore_yrange[1], padding=0)

    QtCore.QTimer.singleShot(100, _nudge_stacked)


def _destroy_stacked_layout():
    """Remove all stacked sub-plots and reset grid layout constraints."""
    global _stacked_sub_plots, _stacked_mouse_handlers, _stacked_click_handlers
    _stacked_zoom_history.clear()
    _stacked_mouse_handlers.clear()
    for handler in _stacked_click_handlers:
        try:
            plot.scene().sigMouseClicked.disconnect(handler)
        except Exception:
            pass
    _stacked_click_handlers.clear()
    try:
        plot.scene().sigMouseClicked.disconnect(plot_clicked)
    except Exception:
        pass
    plot.scene().sigMouseClicked.connect(plot_clicked)
    n = len(_stacked_sub_plots)
    for sub in _stacked_sub_plots:
        try:
            plot_widget.removeItem(sub)
        except Exception:
            pass
    _stacked_sub_plots.clear()
    _stacked_sub_vlines.clear()

    try:
        layout = plot_widget.ci.layout
        # Restore row 0 (main plot) to unconstrained height.
        # Use a very large value instead of -1: pyqtgraph's QGraphicsGridLayout
        # does not reliably honour -1 as "no maximum", so a large pixel cap is safer.
        layout.setRowMaximumHeight(0, 16777215)   # Qt QWIDGETSIZE_MAX
        layout.setRowMinimumHeight(0, 0)
        layout.setRowStretchFactor(0, 1)
        # Reset stacked rows completely
        for i in range(1, n + 1):
            layout.setRowMaximumHeight(i, 0)
            layout.setRowMinimumHeight(i, 0)
            layout.setRowStretchFactor(i, 0)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.invalidate()
        layout.activate()
    except Exception:
        pass

    # Restore the original resize handler so normal mode is unaffected.
    if hasattr(plot_widget, '_orig_resize_event'):
        plot_widget.resizeEvent = plot_widget._orig_resize_event
        del plot_widget._orig_resize_event
    if hasattr(plot_widget, '_stacked_resize_handler'):
        del plot_widget._stacked_resize_handler
    if hasattr(plot_widget, '_stacked_resize_timer'):
        plot_widget._stacked_resize_timer.stop()
        del plot_widget._stacked_resize_timer

    # Make the main plot visible and force a full geometry refresh.
    plot.setVisible(True)
    plot_widget.ci.layout.activate()
    app.processEvents()

    # Move the peak-list legend back to the main plot's viewbox now that
    # the stacked sub-plots are gone.
    _rebuild_peak_legend_on_plot()

    def _force_resize():
        sz = plot_widget.size()
        plot_widget.resize(sz.width(), sz.height() + 1)
        app.processEvents()
        plot_widget.resize(sz.width(), sz.height() - 1)
        plot_widget.updateGeometry()
    QtCore.QTimer.singleShot(100, _force_resize)


def _stacked_update_spectrum_colors():
    """Update spectrum curve pen colors in-place without rebuilding stacked subplots.

    Called when only a plot color changed (main or overlay).  Falls back to a
    full render_plot() if the number of visible spectra no longer matches the
    current subplots (e.g. a toggle happened at the same time).
    """
    if not _stacked_mode or not _stacked_sub_plots:
        render_plot()
        return
    colors = []
    if main_toggle.isChecked() and df is not None:
        colors.append(_main_spectrum_color[0].name())
    for ov_data in overlay_list:
        if ov_data["toggle"].isChecked() and ov_data["df"] is not None:
            colors.append(ov_data["color"])
    if len(colors) != len(_stacked_sub_plots):
        render_plot()
        return
    for sub, color in zip(_stacked_sub_plots, colors):
        items = sub.listDataItems()
        if items:
            items[0].setPen(pg.mkPen(color, width=1))


def _stacked_fast_update(spectra_info):
    """Re-compute and push new data into existing stacked subplots without rebuilding them.

    Handles file changes, sigma-clip changes, and log-Y toggles.
    The viewboxes are not touched (X range preserved) except when log-Y mode
    actually changed, in which case each subplot's Y range is reset to the
    appropriate log/linear defaults.
    Returns True on success, False if a full rebuild is required.
    """
    global _stacked_spectra_data
    if len(spectra_info) != len(_stacked_sub_plots):
        return False

    log_y_changed = (_log_y != _stacked_last_log_y[0])
    _stacked_last_log_y[0] = _log_y
    # Dyn-scale toggle requires a full rebuild (Y range initialisation changes)
    if _stacked_dyn_scale != _stacked_last_dyn_scale[0]:
        _stacked_last_dyn_scale[0] = _stacked_dyn_scale
        return False

    new_spectra_data = []
    for i, (sub, (data_df, name, color)) in enumerate(zip(_stacked_sub_plots, spectra_info)):
        if _stacked_dyn_scale:
            norm = normalise_cached(data_df, zero_floor=True)
            mz_vals = norm['mz'].values
            int_vals = norm['intensity'].values
            lo_thresh = _view_mz_lower_spin.value()
            above_mask = mz_vals >= lo_thresh
            if above_mask.any():
                above_max = int_vals[above_mask].max()
                if above_max > 0:
                    int_vals = int_vals / above_max
            int_vals = np.clip(int_vals, 0, None)
        else:
            mz_vals  = data_df['mz'].values
            int_vals = np.clip(data_df['intensity'].values.astype(float), 0, None)

        if _sigma3_clip:
            sigma3_floor = _estimate_noise_floor(int_vals, n_sigma=_sigma3_n_sigma)
            sigma3_floor = max(sigma3_floor, 1e-6)
            int_vals = np.clip(int_vals, sigma3_floor, None)
        elif _log_y:
            sigma3_floor = 1e-6
            int_vals = np.clip(int_vals, sigma3_floor, None)
        else:
            sigma3_floor = None

        items = sub.listDataItems()
        if items:
            items[0].setData(mz_vals, int_vals)
            items[0].setPen(pg.mkPen(color, width=1))

        sub.setLogMode(x=False, y=_log_y)
        new_spectra_data.append((data_df, mz_vals, int_vals))

        # Reset Y range only when log-Y mode changed (scale units differ)
        if log_y_changed:
            _mirror_this = False
            if _stacked_mirror:
                mirror_set = 0 if _stacked_mirror_odd else 1
                _mirror_this = (i % 2 == mirror_set)
            if _log_y:
                log_floor = np.log10(sigma3_floor) if sigma3_floor else -6
                if _mirror_this:
                    sub.vb.setYRange(0, log_floor, padding=0)
                    sub.vb.invertY(True)
                else:
                    sub.vb.setYRange(log_floor, 0, padding=0)
                    sub.vb.invertY(False)
            else:
                sub.vb.setYRange(0, 1.05, padding=0)
                sub.vb.invertY(_mirror_this)

    _stacked_spectra_data = new_spectra_data
    _clear_highlight_cache()
    _redraw_stacked_peaks_only()
    _apply_stacked_y()
    return True


# ─────────────────────────────────────────────
#  Fast per-row highlight update
# ─────────────────────────────────────────────
def _fast_update_one_row(changed_row):
    """Re-render only one changed row's highlights; spectrum curve untouched.

    Falls back to a full render when conditions make partial update unsafe
    (stacked mode, subtraction, active overlays, main hidden).
    """
    global highlighted_ranges

    # Unchecked row: register the edit but nothing is visible — no render needed
    if changed_row is not None and not changed_row["checkbox"].isChecked():
        return

    if (_stacked_mode or bool(_stacked_sub_plots)
            or df is None
            or changed_row is None
            or changed_row not in custom_peak_rows
            or subtract_action.isChecked()
            or not main_toggle.isChecked()
            or any(ov["toggle"].isChecked() and ov["df"] is not None
                   for ov in overlay_list)):
        _render_peaks_or_full()
        return

    row_id = id(changed_row)

    # Remove only this row's old plot items
    for item in _peak_hl_items_by_row.pop(row_id, []):
        try: plot.removeItem(item)
        except Exception: pass

    # Recompute hl_df (fast: normalise/cache path)
    dyn = dyn_scale_action.isChecked()
    if dyn:
        hl_df = normalise_cached(df, zero_floor=True)
    elif _sigma3_clip:
        hl_df = df.copy()
        _fl = _estimate_noise_floor(hl_df['intensity'].values, n_sigma=_sigma3_n_sigma)
        hl_df['intensity'] = np.where(hl_df['intensity'] < _fl, _fl, hl_df['intensity'])
    else:
        hl_df = df

    # Rebuild highlighted_ranges from cached geometry (no new plot items)
    highlighted_ranges = []
    _foc_idx = _conf_focused_row_idx[0]
    for _ri, row in enumerate(custom_peak_rows):
        if not _row_is_solo_visible(row): continue
        _peaks = parse_peaks_text(row["peaks_input"].text())
        if not _peaks: continue
        _lbl = row["label_input"].text().strip() or "Custom peaks"
        for (_, _, mz_min, mz_max, peak_mz, peak_int) in _get_highlight_geometry(hl_df, _peaks):
            highlighted_ranges.append((mz_min, mz_max, _lbl, peak_mz, peak_int))

    # Re-add plot items only for the changed row
    if _row_is_solo_visible(changed_row):
        _peaks = parse_peaks_text(changed_row["peaks_input"].text())
        if _peaks:
            _ri     = custom_peak_rows.index(changed_row)
            _galpha = _get_group_alpha_for_row(changed_row)
            _ralpha = _galpha if _foc_idx < 0 or _ri == _foc_idx else max(_galpha // 4, 15)
            _lbl    = changed_row["label_input"].text().strip() or "Custom peaks"
            _color  = changed_row["color"][0].name()
            _bgcol  = _main_spectrum_color[0].name()
            _hl_row_id[0] = row_id
            highlight_peaks(hl_df, [_peaks], _color, peak_label=_lbl,
                            alpha=_ralpha, bg_color_str=_bgcol)
            _hl_row_id[0] = None

    # Redraw labels (fast: only TextItems)
    _mx = hl_df['intensity'].max() if len(hl_df) > 0 else 1.0
    _draw_peak_labels(
        peak_labels_toggle.isChecked(),
        peak_masses_toggle.isChecked(),
        (mass_threshold_spin.value() / 100.0) * _mx)


# ─────────────────────────────────────────────
#  Core render  (OPT: debounced, cached)
# ─────────────────────────────────────────────
def _do_render_plot():
    global highlighted_ranges, locked_x_range, locked_y_range
    global _label_text_items, _main_curve, _overlay_curves

    if lock_axes_action.isChecked():
        locked_x_range = plot.vb.viewRange()[0]
        locked_y_range = plot.vb.viewRange()[1]

    highlighted_ranges = []
    _label_text_items.clear()
    _peak_hl_items_by_row.clear()

    # Clean up leftover stacked sub-plots only when leaving stacked mode.
    # When staying in stacked mode the section below either fast-updates the
    # existing subplots or lets _build_stacked_layout() destroy them itself
    # (after we have already saved their viewbox ranges).
    if _stacked_sub_plots and not _stacked_mode:
        _destroy_stacked_layout()

    plot.clear()
    plot.addItem(v_line, ignoreBounds=True)
    plot.addItem(h_line, ignoreBounds=True)

    _main_curve = None
    _overlay_curves = {}

    # Re-add manual recal scatter items after clear
    for item in _manual_recal_scatter_items:
        try:
            plot.addItem(item)
        except Exception:
            pass

    for item in _cluster_scatter_items:
        try:
            plot.addItem(item)
        except Exception:
            pass

    # Re-add comparison peak lines after clear
    if _comparison_win_ref is not None and _comparison_win_ref._active:
        for line in _comparison_win_ref._lines:
            try:
                plot.addItem(line, ignoreBounds=True)
            except Exception:
                pass

    # ── Re-add area measurement lines ──────────────────────────────
    for _aline in (_area_line_lo, _area_line_hi):
        if _aline is not None:
            try:
                plot.addItem(_aline, ignoreBounds=True)
            except Exception:
                pass

    dyn    = dyn_scale_action.isChecked()
    do_sub = subtract_action.isChecked()

    # ══════════════════════════════════════════════════════════════
    #  STACKED MODE  - separate sub-plots, linear Y per row
    # ══════════════════════════════════════════════════════════════
    if _stacked_mode:
        plot.setVisible(False)

        spectra_info = []
        main_color = _main_spectrum_color[0].name()
        if main_toggle.isChecked() and df is not None:
            spectra_info.append((df, "Main file", main_color))
        ov_index = 1
        for ov_data in overlay_list:
            if ov_data["toggle"].isChecked() and ov_data["df"] is not None:
                short_name = ov_data["toggle"].text() or f"Overlay {ov_index}"
                spectra_info.append((ov_data["df"], short_name, ov_data["color"]))
                ov_index += 1

        if not spectra_info:
            _destroy_stacked_layout()
            plot.setVisible(True)
            return

        # Fast path: subplot count unchanged → recompute and push new data into
        # existing subplots (covers file, sigma-clip, log-Y, color changes).
        # Viewboxes are completely untouched; no destroy/rebuild occurs.
        if _stacked_sub_plots and _stacked_fast_update(spectra_info):
            return

        # Full rebuild (first render or number of spectra changed).
        # Save the current zoom BEFORE _build_stacked_layout destroys the
        # existing subplots (it calls _destroy_stacked_layout internally).
        _saved_stacked_xrange = None
        _saved_stacked_yrange = None
        if _stacked_sub_plots:
            try:
                _saved_stacked_xrange = _stacked_sub_plots[0].vb.viewRange()[0]
                if _log_y == _stacked_last_log_y[0]:
                    _saved_stacked_yrange = _stacked_sub_plots[0].vb.viewRange()[1]
            except Exception:
                pass
        _stacked_last_log_y[0]      = _log_y
        _stacked_last_dyn_scale[0]  = _stacked_dyn_scale

        _build_stacked_layout(spectra_info, restore_xrange=_saved_stacked_xrange, restore_yrange=_saved_stacked_yrange)
        if len(spectra_info) == 1:
            QtCore.QTimer.singleShot(200, _show_stacked_single_file_notice)
        return
    # ══════════════════════════════════════════════════════════════

    # Normal (non-stacked) mode: make sure stacked sub-plots are gone
    # and the main plot is visible
    if _stacked_sub_plots:
        _destroy_stacked_layout()
    # Re-draw ToF→Mass triangles that were cleared by plot.clear()
    _w = _tof_to_mass_win_ref[0]
    if _w is not None:
      try:
          if _w.isVisible():
              _w._update_all_triangles()
      except Exception:
          pass
    plot.setVisible(True)
    plot.setLogMode(x=False, y=_log_y)

    draw_df = df
    if do_sub:
        sub_ov = next((o for o in overlay_list
                       if o["toggle"].isChecked() and o["df"] is not None), None)
        if sub_ov is not None:
            draw_df = compute_subtraction(df, sub_ov["df"], dynamic=subtract_dyn_action.isChecked())

    if draw_df is None: return

    if dyn:
        plot_df = normalise_cached(draw_df, zero_floor=True)
    elif _sigma3_clip:
        # Sigma-3 clip without normalising: preserve raw intensity scale,
        # only floor the noise baseline.
        plot_df = draw_df.copy()
        floor = _estimate_noise_floor(plot_df['intensity'].values, n_sigma=_sigma3_n_sigma)
        plot_df['intensity'] = np.where(
            plot_df['intensity'] < floor, floor, plot_df['intensity'])
    else:
        plot_df = draw_df

    _user_main_color = _main_spectrum_color[0].name()
    main_color_str = _user_main_color   # used for highlight bg_color_str below
    primary_name   = spectrum_display_name(combo.currentData() or combo.currentText())

    if main_toggle.isChecked():
        _main_curve = plot.plot(
            plot_df["mz"].values, plot_df["intensity"].values,
            pen=pg.mkPen(_user_main_color, width=1), name=primary_name)

    pk_alpha      = highlight_alpha()
    show_lbl      = peak_labels_toggle.isChecked()
    show_masses       = peak_masses_toggle.isChecked()
    show_masses_all   = peak_masses_all_toggle.isChecked()
    threshold_pct = mass_threshold_spin.value()
    mx_intensity  = plot_df['intensity'].max() if len(plot_df) > 0 else 1.0
    mass_threshold_abs = (threshold_pct / 100.0) * mx_intensity

    if not do_sub:
        # If main is hidden, highlight against the first active overlay instead
        if main_toggle.isChecked():
            _hl_df = plot_df
        else:
            _first_ov = next(
                (ov for ov in overlay_list
                 if ov["toggle"].isChecked() and ov["df"] is not None),
                None)
            if _first_ov is not None:
                if dyn:
                    _hl_df = normalise_cached(_first_ov["df"], zero_floor=True)
                elif _sigma3_clip:
                    _hl_df = _first_ov["df"].copy()
                    _floor = _estimate_noise_floor(_hl_df['intensity'].values, n_sigma=_sigma3_n_sigma)
                    _hl_df['intensity'] = np.where(_hl_df['intensity'] < _floor, _floor, _hl_df['intensity'])
                else:
                    _hl_df = _first_ov["df"]
            else:
                _hl_df = None

        if _hl_df is not None:
            _foc_idx = _conf_focused_row_idx[0]
            for _ri, row in enumerate(custom_peak_rows):
                if not _row_is_solo_visible(row): continue
                peaks = parse_peaks_text(row["peaks_input"].text())
                if not peaks: continue
                color = row["color"][0].name()
                lbl   = row["label_input"].text().strip() or "Custom peaks"
                _galpha = _get_group_alpha_for_row(row)
                _ralpha = _galpha if _foc_idx < 0 or _ri == _foc_idx else max(_galpha // 4, 15)
                _hl_row_id[0] = id(row)
                highlight_peaks(_hl_df, [peaks], color, peak_label=lbl,
                                alpha=_ralpha, bg_color_str=main_color_str)
                _hl_row_id[0] = None

    if not do_sub:
        for ov_data in overlay_list:
            if not ov_data["toggle"].isChecked() or ov_data["df"] is None: continue
            alpha    = overlay_alpha()
            if dyn:
                ov_df = normalise_cached(ov_data["df"], zero_floor=True)
            elif _sigma3_clip:
                ov_df = ov_data["df"].copy()
                _floor = _estimate_noise_floor(ov_df['intensity'].values, n_sigma=_sigma3_n_sigma)
                ov_df['intensity'] = np.where(ov_df['intensity'] < _floor, _floor, ov_df['intensity'])
            else:
                ov_df = ov_data["df"]
            ov_path  = ov_data["combo"].currentData() or ov_data["combo"].currentText()
            ov_name  = spectrum_display_name(ov_path) if not ov_data.get("is_processed") else ov_data["toggle"].text()
            ov_curve = plot.plot(
                ov_df["mz"].values, ov_df["intensity"].values,
                pen=overlay_pen(ov_data["color"], alpha), name=ov_name)
            _overlay_curves[id(ov_data)] = ov_curve
            _foc_idx = _conf_focused_row_idx[0]
            for _ri, row in enumerate(custom_peak_rows):
                if not _row_is_solo_visible(row): continue
                peaks = parse_peaks_text(row["peaks_input"].text())
                if not peaks: continue
                color = row["color"][0].name()
                lbl   = row["label_input"].text().strip() or "Custom peaks"
                _galpha = _get_group_alpha_for_row(row)
                _ralpha = _galpha if _foc_idx < 0 or _ri == _foc_idx else max(_galpha // 4, 15)
                _hl_row_id[0] = id(row)
                highlight_peaks(ov_df, [peaks], color, peak_label=lbl,
                                alpha=_ralpha, bg_color_str=main_color_str)
                _hl_row_id[0] = None
    else:
        hover_label.setText(
            f"<span style='color:orange'>Δ = {primary_name} − overlay 1</span>")

    _draw_envelope_lines(plot_df)
    _draw_peak_labels(show_lbl, show_masses, mass_threshold_abs)
    _thr_mode = "snr" if threshold_mode_combo.currentText() == "SNR" else "pct"
    _draw_auto_peaks(plot_df, auto_peaks_toggle.isChecked(), threshold_pct, show_masses_all, mode=_thr_mode)

    if lock_axes_action.isChecked() and locked_x_range and locked_y_range:
        plot.vb.setXRange(*locked_x_range, padding=0)
        plot.vb.setYRange(*locked_y_range, padding=0)

    # Update minimap with full-spectrum data
    if plot_df is not None and len(plot_df) > 0:
        _minimap.update_data(plot_df['mz'].values, plot_df['intensity'].values)
    else:
        _minimap.update_data(np.array([]), np.array([]))

    legend.setVisible(False)       # the spectrum-names legend is not used

# Wire debounce timer
_render_timer.timeout.connect(_do_render_plot)


def _finish_splash_after_first_plot():
    """Close the start-up screen once the window shows the plot (and the
    spectrum, when one is being loaded)."""
    if not splash_active() or not main_win.isVisible():
        return
    if df is None and (combo.currentData() or ""):
        return                              # the spectrum is still loading
    QtCore.QTimer.singleShot(0, finish_splash)
_render_timer.timeout.connect(_finish_splash_after_first_plot)

# ─────────────────────────────────────────────
#  Load helpers
# ─────────────────────────────────────────────
def safe_read(path, sep=None):
    try:
        return read_spectrum_file(path, sep=sep)
    except Exception as e:
        QtWidgets.QMessageBox.warning(main_win, "File Parse Error",
            f"Could not load:\n{path}\n\n{e}")
        return None

def plot_file(abs_path):
    global df, df_raw, recal_factor
    if not abs_path or not os.path.exists(str(abs_path)):
        df = df_raw = None
        _clear_all_caches()
        render_plot()
        return
    data = safe_read(abs_path, sep=get_sep_from_combo())
    if data is not None:
        df_raw = data; recal_factor = 1.0
        _clear_all_caches()
        _area_clear_markers()
        _area_pick_step = 0
        reapply_transforms()
        add_recent_file(abs_path); update_recent_files_menu()
    else:
        render_plot()

def load_overlay_data(ov_data):
    if ov_data.get("is_processed"):
        render_plot(); return
    path = ov_data["combo"].currentData() or ov_data["combo"].currentText()
    if not path or not os.path.exists(str(path)):
        ov_data["df"] = None; render_plot(); return
    ov_data["df"] = safe_read(path, sep=get_sep_from_combo())
    _clear_norm_cache()
    render_plot()

def refresh_current():
    path = combo.currentData() or combo.currentText()
    plot_file(path)
    for ov in overlay_list:
        if ov["toggle"].isChecked(): load_overlay_data(ov)

def _populate_main_combo(files):
    combo.blockSignals(True); combo.clear()
    if files:
        for p in files:
            combo.addItem(os.path.basename(p), p)
    else:
        pol = polarity_combo.currentText()
        dt  = dt_combo.currentText()
        if dt and dt != "All":
            combo.addItem(f'No data in "{pol}" mode  &  dt = {dt}', None)
        else:
            combo.addItem(f'No file in "{pol}" mode', None)
    combo.blockSignals(False)

# ─────────────────────────────────────────────
#  Polarity / folder helpers
# ─────────────────────────────────────────────
def on_polarity_changed():
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    _populate_main_combo(files)
    if files:
        combo.setCurrentIndex(0)
        plot_file(combo.currentData())
    else:
        plot_file(None)

def refresh_all_combos():
    global all_txt_files
    all_txt_files = list_all_txt_files()
    _refresh_dt_combo()        # repopulate dt filter
    _auto_select_polarity()    # auto-switch polarity if needed
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    _populate_main_combo(files)
    if files:
        combo.setCurrentIndex(0)
        plot_file(combo.currentData())
    else:
        plot_file(None)
    for ov in overlay_list:
        ov_files = get_txt_files(ov["polarity"].currentText())
        ov["combo"].blockSignals(True); ov["combo"].clear()
        if ov_files:
            for p in ov_files:
                ov["combo"].addItem(os.path.basename(p), p)
        else:
            ov["combo"].addItem(f'No file in "{ov["polarity"].currentText()}" mode', None)
        ov["combo"].blockSignals(False)
        if ov["toggle"].isChecked(): load_overlay_data(ov)

def open_folder(path=None):
    global base_dir, all_txt_files, is_virtual, virtual_file_list
    if path is None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            main_win, "Select Data Folder", base_dir or "")
    if not path: return
    if path.startswith(VIRTUAL_FOLDER_PREFIX):
        vf = load_virtual_folders(); file_list = vf.get(path, [])
        if not file_list:
            QtWidgets.QMessageBox.warning(main_win, "Virtual Folder",
                "This virtual folder no longer has associated files."); return
        is_virtual = True; virtual_file_list = file_list
        add_recent_folder(path); update_recent_folders_menu()
        update_folder_label(); refresh_all_combos(); return
    if not os.path.isdir(path): return
    is_virtual = False; virtual_file_list = []
    base_dir = path; settings.setValue("base_dir", base_dir)
    add_recent_folder(path); update_recent_folders_menu()
    update_folder_label(); refresh_all_combos()

def open_individual_files():
    global is_virtual, virtual_file_list, all_txt_files
    paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
        main_win, "Select Files", base_dir or "",
        "Spectrum Files (*.txt *.csv *.tsv *.dat *.asc);;All Files (*)")
    if not paths: return
    existing = set(all_txt_files)
    added = [p for p in paths if p not in existing]
    if not added:
        QtWidgets.QMessageBox.information(
            main_win, "Open Files", "All selected files are already in the current list.")
        return
    merged = list(all_txt_files) + added
    is_virtual = True; virtual_file_list = merged
    all_txt_files = list_all_txt_files()
    update_folder_label()
    files = get_txt_files(polarity_combo.currentText())
    _populate_main_combo(files)
    if files:
        idx = combo.findText(os.path.basename(added[0]))
        if idx >= 0: combo.setCurrentIndex(idx)
        plot_file(combo.currentData())
    else:
        _populate_main_combo(all_txt_files)
        combo.setCurrentIndex(0); plot_file(combo.currentData())
    for ov in overlay_list:
        ov_files = get_txt_files(ov["polarity"].currentText())
        ov["combo"].blockSignals(True); ov["combo"].clear()
        for p in ov_files: ov["combo"].addItem(os.path.basename(p), p)
        ov["combo"].blockSignals(False)

def update_recent_folders_menu():
    recent_folders_menu.clear()
    lst = load_recent_folders()
    for p in lst:
        a = QtWidgets.QAction(p, main_win)
        a.triggered.connect(lambda checked, path=p: open_folder(path))
        recent_folders_menu.addAction(a)
    if not lst:
        na = QtWidgets.QAction("(none)", main_win); na.setEnabled(False)
        recent_folders_menu.addAction(na)

def update_recent_files_menu():
    recent_files_menu.clear()
    lst = load_recent_files()
    for p in lst:
        a = QtWidgets.QAction(p, main_win)
        a.triggered.connect(lambda checked, path=p: open_single_recent_file(path))
        recent_files_menu.addAction(a)
    if not lst:
        na = QtWidgets.QAction("(none)", main_win); na.setEnabled(False)
        recent_files_menu.addAction(na)

def open_single_recent_file(path):
    global all_txt_files, is_virtual, virtual_file_list
    if not os.path.exists(path):
        QtWidgets.QMessageBox.warning(main_win, "File not found", f"Could not find:\n{path}"); return
    if path not in all_txt_files:
        is_virtual = True
        virtual_file_list = list(all_txt_files) + [path]
        all_txt_files = list_all_txt_files()
        update_folder_label()
    fname = os.path.basename(path)
    files = get_txt_files(polarity_combo.currentText())
    _populate_main_combo(files if files else all_txt_files)
    idx = combo.findText(fname)
    if idx >= 0: combo.setCurrentIndex(idx)
    else: combo.setCurrentIndex(0)
    plot_file(combo.currentData())

update_recent_folders_menu()
update_recent_files_menu()

# ─────────────────────────────────────────────
#  Session state save / restore
# ─────────────────────────────────────────────
def save_session_state():
    state = {}
    main_path = combo.currentData() or combo.currentText()
    if main_path and os.path.exists(str(main_path)):
        state["main_file"] = main_path
    state["polarity"]   = polarity_combo.currentText()
    state["base_dir"]   = base_dir
    state["is_virtual"] = is_virtual
    if is_virtual:
        state["virtual_files"] = [p for p in virtual_file_list if os.path.exists(p)]
    overlays = []
    for ov in overlay_list:
        if ov.get("is_processed"): continue
        if not ov["toggle"].isChecked(): continue
        ov_path = ov["combo"].currentData() or ov["combo"].currentText()
        if ov_path and os.path.exists(str(ov_path)):
            overlays.append({
                "path": ov_path,
                "polarity": ov["polarity"].currentText(),
            })
    state["overlays"] = overlays
    settings.setValue("session_state", json.dumps(state))
    _peak_session_timer.stop()
    _save_peak_session()

def restore_session_state():
    global base_dir, all_txt_files, is_virtual, virtual_file_list
    raw = settings.value("session_state", "")
    if not raw: return
    try: state = json.loads(raw)
    except Exception: return

    saved_is_virtual = state.get("is_virtual", False)
    saved_vfiles     = state.get("virtual_files", [])
    saved_base       = state.get("base_dir", "")

    if saved_is_virtual and saved_vfiles:
        existing = [p for p in saved_vfiles if os.path.exists(p)]
        if existing:
            is_virtual = True; virtual_file_list = existing
            all_txt_files = list_all_txt_files()
            update_folder_label()
    elif saved_base and os.path.isdir(saved_base):
        base_dir = saved_base; settings.setValue("base_dir", base_dir)
        is_virtual = False; virtual_file_list = []
        all_txt_files = list_all_txt_files()
        update_folder_label()

    pol = state.get("polarity", "neg")
    idx = polarity_combo.findText(pol)
    if idx >= 0: polarity_combo.setCurrentIndex(idx)

    files = get_txt_files(polarity_combo.currentText())
    _populate_main_combo(files)

    main_path = state.get("main_file", "")
    if main_path and os.path.exists(main_path):
        idx = combo.findData(main_path)
        if idx < 0: idx = combo.findText(os.path.basename(main_path))
        if idx >= 0: combo.setCurrentIndex(idx)
        plot_file(combo.currentData())
    else:
        # No saved file - respect current combo state (may be placeholder)
        plot_file(combo.currentData())

    for ov_state in state.get("overlays", []):
        ov_path = ov_state.get("path", "")
        if not ov_path or not os.path.exists(ov_path): continue
        ov_data = add_overlay_row()
        pidx = ov_data["polarity"].findText(ov_state.get("polarity", "neg"))
        if pidx >= 0: ov_data["polarity"].setCurrentIndex(pidx)
        cidx = ov_data["combo"].findData(ov_path)
        if cidx < 0:
            ov_data["combo"].addItem(os.path.basename(ov_path), ov_path)
            cidx = ov_data["combo"].count() - 1
        ov_data["combo"].setCurrentIndex(cidx)
        ov_data["toggle"].setChecked(True)

# ─────────────────────────────────────────────
#  Project save / open  (.drp)
# ─────────────────────────────────────────────
_current_project_path = [None]   # mutable container for the active project path

def _show_toast(message, duration_ms=2500):
    """Briefly show a non-modal status message in the main window status bar."""
    status_bar.showMessage(message, duration_ms)

def _write_project(path):
    """Core project serialization — path must already be validated."""
    if not path.lower().endswith(".drp"):
        path += ".drp"
    _current_project_path[0] = path
    settings.setValue("last_project_path", path)

def save_project():
    """Save to the current project path; if none, prompt for a path."""
    if _current_project_path[0] and os.path.isfile(_current_project_path[0]):
        path = _current_project_path[0]
    else:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            main_win, "Save Project", _get_dialog_dir("project"), "Droplet Project (*.drp)")
        if path: _set_dialog_dir("project", path)
        if not path:
            return
        if not path.lower().endswith(".drp"):
            path += ".drp"
        _current_project_path[0] = path

def save_project_as():
    """Always prompt for a new path."""
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Save Project As", _get_dialog_dir("project"), "Droplet Project (*.drp)")
    if path: _set_dialog_dir("project", path)
    if not path:
        return
    if not path.lower().endswith(".drp"):
        path += ".drp"
    _current_project_path[0] = path

    project = {}

    # ── Main file (absolute path) ──────────────────────────────────────
    main_path = combo.currentData() or combo.currentText()
    project["main_file"] = str(main_path) if main_path else ""

    # ── Folder / virtual folder ────────────────────────────────────────
    project["base_dir"]      = base_dir
    project["is_virtual"]    = is_virtual
    project["virtual_files"] = list(virtual_file_list) if is_virtual else []

    # ── Polarity / dt filter ───────────────────────────────────────────
    project["polarity"] = polarity_combo.currentText()
    project["dt"]       = dt_combo.currentText()

    # ── Overlays (absolute paths, skip processed/unsaved overlays) ─────
    overlays = []
    for ov in overlay_list:
        if ov.get("is_processed"):
            continue
        ov_path = ov["combo"].currentData() or ov["combo"].currentText()
        overlays.append({
            "path":     str(ov_path) if ov_path else "",
            "polarity": ov["polarity"].currentText(),
            "enabled":  ov["toggle"].isChecked(),
        })
    project["overlays"] = overlays

    # ── Peak lists + groups ───────────────────────────────────────────
    project["peak_groups"] = [
        {"name": gd["name"], "alpha": gd.get("alpha", 255), "highlight": gd.get("highlight", True)}
        for gd in _peak_groups
    ]
    peaks = []
    for r in custom_peak_rows:
        entry = {
            "checked":  r["checkbox"].isChecked(),
            "color":    r["color"][0].name(),
            "label":    r["label_input"].text(),
            "L_state":  r.get("L_state",  [0])[0],
            "1L_state": r.get("1L_state", [0])[0],
            "group":    r.get("group_name", "Unclassified"),
        }
        entry.update(_row_legend_fields(r))
        if r.get("mode_btn") and r["mode_btn"].isChecked():
            entry["mode"]        = "range"
            entry["range_start"] = r["range_start"].value()
            entry["range_step"]  = r["range_step"].value()
            entry["range_end"]   = r["range_end"].value()
        else:
            entry["mode"]  = "manual"
            entry["peaks"] = r["peaks_input"].text()
        peaks.append(entry)
    project["peak_lists"] = peaks

    # ── View / display state ──────────────────────────────────────────
    vr = plot.vb.viewRange()
    project["view"] = {
        "x_range":         vr[0],
        "y_range":         vr[1],
        "display_mode":    current_display,
        "dyn_scale":       dyn_scale_action.isChecked(),
        "subtract":        subtract_action.isChecked(),
        "subtract_dyn":    subtract_dyn_action.isChecked(),
        "crosshair":       crosshair_action.isChecked(),
        "peak_labels":     peak_labels_toggle.isChecked(),
        "peak_masses":     peak_masses_toggle.isChecked(),
        "auto_peaks":      auto_peaks_toggle.isChecked(),
        "mass_threshold":  mass_threshold_spin.value(),
        "threshold_mode":  threshold_mode_combo.currentText(),
        "highlight_alpha": highlight_slider.value(),
        "overlay_opacity": overlay_opacity_slider.value(),
        "sigma3_clip":     sigma3_clip_chk.isChecked(),
        "sigma3_n_sigma":  sigma3_slider.value() / 10.0,
        "stacked_mode":      stacked_mode_chk.isChecked(),
        "stacked_dyn_scale": stacked_dyn_scale_chk.isChecked(),
        "log_y":             stacked_logy_chk.isChecked(),
    }

    project["version"] = "1.0"

    # ── Peak list confirmation ────────────────────────────────────────
    _cp = _confirmation_panel_ref[0]
    if _cp is not None:
        project["peak_confirmation"] = _cp.to_project_dict()

    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(project, fh, indent=2)
        QtWidgets.QMessageBox.information(
            main_win, "Project Saved", f"Project saved to:\n{path}")
    except Exception as e:
        QtWidgets.QMessageBox.warning(
            main_win, "Save Failed", f"Could not save project:\n{e}")

def open_project():
    global base_dir, all_txt_files, is_virtual, virtual_file_list

    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        main_win, "Open Project", _get_dialog_dir("project"), "Droplet Project (*.drp)")
    if path: _set_dialog_dir("project", path)
    if not path:
        return

    try:
        with open(path, "r", encoding="utf-8") as fh:
            project = json.load(fh)
    except Exception as e:
        QtWidgets.QMessageBox.warning(
            main_win, "Open Failed", f"Could not read project file:\n{e}")
        return

    # ── Restore folder / virtual folder ──────────────────────────────
    saved_is_virtual = project.get("is_virtual", False)
    saved_vfiles     = project.get("virtual_files", [])
    saved_base       = project.get("base_dir", "")

    if saved_is_virtual and saved_vfiles:
        existing = [p for p in saved_vfiles if os.path.exists(p)]
        missing  = [p for p in saved_vfiles if not os.path.exists(p)]
        if missing:
            QtWidgets.QMessageBox.warning(
                main_win, "Missing Files",
                "Some files in this project could not be found and will be skipped:\n"
                + "\n".join(missing))
        if existing:
            is_virtual = True
            virtual_file_list = existing
            all_txt_files = list_all_txt_files()
            update_folder_label()
    elif saved_base and os.path.isdir(saved_base):
        base_dir = saved_base
        settings.setValue("base_dir", base_dir)
        is_virtual = False
        virtual_file_list = []
        all_txt_files = list_all_txt_files()
        update_folder_label()

    # ── Restore polarity / dt ─────────────────────────────────────────
    pol = project.get("polarity", "neg")
    idx = polarity_combo.findText(pol)
    if idx >= 0:
        polarity_combo.blockSignals(True)
        polarity_combo.setCurrentIndex(idx)
        polarity_combo.blockSignals(False)

    _refresh_dt_combo()
    dt_val = project.get("dt", "All")
    dt_idx = dt_combo.findText(dt_val)
    if dt_idx >= 0:
        dt_combo.blockSignals(True)
        dt_combo.setCurrentIndex(dt_idx)
        dt_combo.blockSignals(False)

    # ── Populate main combo and load main file ────────────────────────
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    _populate_main_combo(files if files else [])

    main_path = project.get("main_file", "")
    if main_path and os.path.exists(main_path):
        idx = combo.findData(main_path)
        if idx < 0:
            combo.addItem(os.path.basename(main_path), main_path)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
        plot_file(combo.currentData())
    else:
        plot_file(combo.currentData())

    # ── Restore overlays ──────────────────────────────────────────────
    # Remove existing overlays first
    for ov in overlay_list[:]:
        _remove_overlay_if_exists(ov)

    for ov_state in project.get("overlays", []):
        ov_path = ov_state.get("path", "")
        if not ov_path or not os.path.exists(ov_path):
            if ov_path:
                QtWidgets.QMessageBox.warning(
                    main_win, "Missing Overlay",
                    f"Overlay file not found, skipping:\n{ov_path}")
            continue
        ov_data = add_overlay_row()
        pidx = ov_data["polarity"].findText(ov_state.get("polarity", "neg"))
        if pidx >= 0:
            ov_data["polarity"].setCurrentIndex(pidx)
        cidx = ov_data["combo"].findData(ov_path)
        if cidx < 0:
            ov_data["combo"].addItem(os.path.basename(ov_path), ov_path)
            cidx = ov_data["combo"].count() - 1
        ov_data["combo"].setCurrentIndex(cidx)
        ov_data["toggle"].setChecked(ov_state.get("enabled", True))

    # ── Restore peak lists ────────────────────────────────────────────
    _selected_peak_indices.clear(); _select_anchor[0] = None
    for row in custom_peak_rows[:]:
        peaks_rows_layout_remove(row["widget"])
        custom_peak_rows.remove(row)
    _peak_groups.clear(); _group_header_widgets.clear()
    for _g in project.get("peak_groups", [{"name": "Unclassified", "alpha": 255}]):
        _peak_groups.append({"name": _g["name"], "alpha": _g.get("alpha", 255), "collapsed": False, "highlight": _g.get("highlight", True), "L_active": _g.get("L_active", True), "1L_active": _g.get("1L_active", True), "curve_active": _g.get("curve_active", True)})

    for item in project.get("peak_lists", []):
        _gn = item.get("group", "Unclassified")
        if not any(g["name"] == _gn for g in _peak_groups):
            _peak_groups.append({"name": _gn, "alpha": 255, "collapsed": False, "highlight": True, "L_active": True, "1L_active": True, "curve_active": True})
        if item.get("mode") == "range":
            _add_peak_row_base(
                checked=item.get("checked", True),
                color=QtGui.QColor(item.get("color", "#ff0000")),
                label_text=item.get("label", ""),
                group_name=_gn,
                range_values=(
                    item.get("range_start", 0.0),
                    item.get("range_end",   0.0),
                    item.get("range_step",  1.0),
                ))
        else:
            _add_peak_row_base(
                checked=item.get("checked", True),
                color=QtGui.QColor(item.get("color", "#ff0000")),
                peaks_text=item.get("peaks", ""),
                label_text=item.get("label", ""),
                group_name=_gn)
        rd = custom_peak_rows[-1]
        _apply_row_legend_fields(rd, item)
        for key in ("L_state", "1L_state"):
            v = item.get(key, 0)
            if v:
                rd[key][0] = v
                rd["refresh_L"]() if key == "L_state" else rd["refresh_1L"]()

    update_pick_row_combo()
    _clear_highlight_cache()
    _auto_sync_legend_entries()

    # ── Restore view state ────────────────────────────────────────────
    view = project.get("view", {})
    if view:
        set_display_mode(view.get("display_mode", "bright"))
        dyn_scale_action.setChecked(view.get("dyn_scale", False))
        subtract_action.setChecked(view.get("subtract", False))
        subtract_dyn_action.setChecked(view.get("subtract_dyn", False))
        crosshair_action.setChecked(view.get("crosshair", False))
        peak_labels_toggle.setChecked(view.get("peak_labels", False))
        peak_masses_toggle.setChecked(view.get("peak_masses", False))
        auto_peaks_toggle.setChecked(view.get("auto_peaks", False))
        mass_threshold_spin.setValue(view.get("mass_threshold", 5.0))
        thr_idx = threshold_mode_combo.findText(view.get("threshold_mode", "% of max intensity"))
        if thr_idx >= 0:
            threshold_mode_combo.setCurrentIndex(thr_idx)
        highlight_slider.setValue(view.get("highlight_alpha", 80))
        overlay_opacity_slider.setValue(view.get("overlay_opacity", 80))
        sigma3_clip_chk.setChecked(view.get("sigma3_clip", False))
        sigma3_slider.setValue(int(view.get("sigma3_n_sigma", 1.0) * 10))
        stacked_mode_chk.setChecked(view.get("stacked_mode", False))
        stacked_dyn_scale_chk.setChecked(view.get("stacked_dyn_scale", True))
        stacked_logy_chk.setChecked(view.get("log_y", False))

        # Restore zoom/pan
        xr = view.get("x_range")
        yr = view.get("y_range")
        if xr and yr:
            QtCore.QTimer.singleShot(200, lambda: (
                plot.vb.setXRange(xr[0], xr[1], padding=0),
                plot.vb.setYRange(yr[0], yr[1], padding=0)
            ))

    # (Older projects may contain "plots" from the removed Plotting Tool;
    #  they are ignored.)

    render_plot()

    # ── Restore peak list confirmation ────────────────────────────────
    _cp = _confirmation_panel_ref[0]
    _conf_data = project.get("peak_confirmation")
    if _cp is not None:
        if _conf_data:
            _cp.from_project_dict(_conf_data)
        _sync_confirmation_panel()

# ─────────────────────────────────────────────
#  Drag-and-drop
# ─────────────────────────────────────────────
class DropFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if obj is main_win:
            if event.type() == QtCore.QEvent.Type.DragEnter:
                if event.mimeData().hasUrls():
                    event.acceptProposedAction(); return True
            elif event.type() == QtCore.QEvent.Type.Drop:
                urls  = event.mimeData().urls()
                paths = [u.toLocalFile() for u in urls
                         if u.toLocalFile().lower().endswith(
                             (".txt",".csv",".tsv",".dat",".asc"))]
                if paths: handle_dropped_files(paths)
                return True
        return False

drop_filter = DropFilter(main_win)
main_win.installEventFilter(drop_filter)

def handle_dropped_files(paths):
    global is_virtual, virtual_file_list, all_txt_files
    existing = set(all_txt_files)
    new_paths = [p for p in paths if p not in existing]
    if not new_paths: return
    is_virtual = True
    virtual_file_list = list(all_txt_files) + new_paths
    all_txt_files = list_all_txt_files()
    update_folder_label(); refresh_all_combos()

# ─────────────────────────────────────────────
#  Confirmation panel sync helpers
# ─────────────────────────────────────────────
_confirmation_panel_ref = [None]  # set after panel is created
_conf_focused_row_idx   = [-1]    # peak-row index hovered in confirmation panel
_solo_edit_mode         = [False] # "Show edited peak list only" button state
_solo_edit_row          = [None]  # row data dict currently being edited (or None)

def _row_draws(row) -> bool:
    """True when the row currently puts peaks on the plot."""
    return _row_is_solo_visible(row) and bool(parse_peaks_text(row["peaks_input"].text()))

def _row_is_solo_visible(row):
    """Visibility gate used by all render loops.
    When solo-edit mode is inactive, or no row is being edited yet,
    behaves exactly like row["checkbox"].isChecked().
    When active and a row's text field is focused, only that row is shown.
    Also respects the per-group highlight toggle."""
    if not row["checkbox"].isChecked():
        return False
    gname = row.get("group_name", "Unclassified")
    for gd in _peak_groups:
        if gd["name"] == gname:
            if not gd.get("highlight", True):
                return False
            break
    if not _solo_edit_mode[0] or _solo_edit_row[0] is None:
        return True
    return row is _solo_edit_row[0]

def _sync_confirmation_panel():
    """Push the current peak rows into the confirmation panel (no-op until panel exists)."""
    panel = _confirmation_panel_ref[0]
    if panel is None:
        return
    from droplet_pkg.ui.windows.peak_confirmation import _row_dict_from_widget_row as _rdfwr
    panel.sync_with_peak_rows([_rdfwr(r) for r in custom_peak_rows])

# ─────────────────────────────────────────────
#  Peaks window – rows with drag-to-reorder
# ─────────────────────────────────────────────
custom_peak_rows = []
_last_clicked_peak_row = [-1]   # anchor index for Shift-click range toggle

# ── Peak groups ────────────────────────────────────────────────────────────────
_peak_groups = [{"name": "Unclassified", "alpha": 255, "collapsed": False, "highlight": True, "L_active": True, "1L_active": True, "curve_active": True}]
_group_header_widgets = {}   # group name → PeakGroupHeader instance


def _rebuild_group_selectors():
    """No-op — per-row group selectors were removed; group is assigned via drag-and-drop."""
    pass


def _get_group_alpha_for_row(row):
    """Return the highlight alpha (0–255) for the group this row belongs to."""
    gname = row.get("group_name", "Unclassified")
    for gd in _peak_groups:
        if gd["name"] == gname:
            return gd.get("alpha", highlight_alpha())
    return highlight_alpha()

def _get_group_data_for_row(row):
    """Return the group dict for the group this row belongs to, or None."""
    gname = row.get("group_name", "Unclassified")
    for gd in _peak_groups:
        if gd["name"] == gname:
            return gd
    return None


class PeakGroupHeader(QtWidgets.QWidget):
    """Collapsable, draggable header widget for a peak-list group."""

    def __init__(self, group_data):
        super().__init__()
        self._gd = group_data
        self._drag_start = [None]
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        # Drag handle — initiates group reorder
        self._gh = QtWidgets.QLabel("⠿")
        self._gh.setFixedWidth(12)
        self._gh.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
        self._gh.setToolTip("Drag to reorder group")
        self._gh.setStyleSheet("color:gray; font-size:14px;")

        self._hl_btn = QtWidgets.QPushButton("●")
        self._hl_btn.setFixedSize(18, 18)
        self._hl_btn.setCheckable(True)
        self._hl_btn.setChecked(group_data.get("highlight", True))
        self._hl_btn.setToolTip("Show / hide highlights for checked rows in this group")
        self._hl_btn.setStyleSheet(
            "font-size:10px; border:none; color:#4a90d9;" if group_data.get("highlight", True)
            else "font-size:10px; border:none; color:#555555;")

        self._cb = QtWidgets.QPushButton("▼")
        self._cb.setFixedSize(18, 18)
        self._cb.setCheckable(True)
        self._cb.setChecked(group_data.get("collapsed", False))
        self._cb.setText("▶" if group_data.get("collapsed") else "▼")
        self._cb.setStyleSheet("font-size:9px; border:none; color:gray;")
        self._cb.setToolTip("Collapse / expand this group")

        self._ne = QtWidgets.QLineEdit(group_data["name"])
        self._ne.setStyleSheet("font-weight:bold; font-size:11px;")
        self._ne.setMinimumWidth(60)
        self._ne.setToolTip("Group name")
        self._ne.setAcceptDrops(False)

        alpha_lbl = QtWidgets.QLabel("Intensity:")
        alpha_lbl.setStyleSheet("color:gray; font-size:10px;")
        self._alpha_slider = JumpSlider(QtCore.Qt.Orientation.Horizontal)
        self._alpha_slider.setMinimum(10)
        self._alpha_slider.setMaximum(255)
        self._alpha_slider.setValue(group_data.get("alpha", 255))
        self._alpha_slider.setFixedWidth(90)
        self._alpha_slider.setToolTip("Highlight band opacity for peaks in this group")

        _SS_CHAR_ON  = "border:1px solid #555; color:#aaa; font-size:10px;"
        _SS_CHAR_OFF = "border:1px solid #555; color:#444; background:#1a1a1a; font-size:10px;"

        self._L_btn = QtWidgets.QPushButton("L")
        self._L_btn.setFixedSize(22, 18)
        self._L_btn.setCheckable(True)
        self._L_btn.setChecked(group_data.get("L_active", True))
        self._L_btn.setToolTip("Enable / disable L (label/mass) annotations for all rows in this group\n(individual row L states are preserved)")
        self._L_btn.setStyleSheet(_SS_CHAR_ON if group_data.get("L_active", True) else _SS_CHAR_OFF)

        self._1L_btn = QtWidgets.QPushButton("1L")
        self._1L_btn.setFixedSize(26, 18)
        self._1L_btn.setCheckable(True)
        self._1L_btn.setChecked(group_data.get("1L_active", True))
        self._1L_btn.setToolTip("Enable / disable 1L (first-peak label/mass) annotations for all rows in this group\n(individual row 1L states are preserved)")
        self._1L_btn.setStyleSheet(_SS_CHAR_ON if group_data.get("1L_active", True) else _SS_CHAR_OFF)

        self._C_btn = QtWidgets.QPushButton("⌒")
        self._C_btn.setFixedSize(22, 18)
        self._C_btn.setCheckable(True)
        self._C_btn.setChecked(group_data.get("curve_active", True))
        self._C_btn.setToolTip("Enable / disable envelope curves for all rows in this group\n(individual row envelope states are preserved)")
        self._C_btn.setStyleSheet(_SS_CHAR_ON if group_data.get("curve_active", True) else _SS_CHAR_OFF)

        self._del_btn = QtWidgets.QPushButton("×")
        self._del_btn.setFixedSize(18, 18)
        self._del_btn.setToolTip("Delete this group")
        self._del_btn.setStyleSheet("font-size:12px; border:none; color:#c04040;")

        lay.addWidget(self._gh)
        lay.addWidget(self._hl_btn)
        lay.addWidget(self._cb)
        lay.addWidget(self._ne)
        lay.addStretch()
        lay.addWidget(self._L_btn)
        lay.addWidget(self._1L_btn)
        lay.addWidget(self._C_btn)
        lay.addWidget(alpha_lbl)
        lay.addWidget(self._alpha_slider)
        lay.addWidget(self._del_btn)

        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), QtGui.QColor("#2a3a4a"))
        self.setPalette(pal)

        self._hl_btn.toggled.connect(self._on_highlight)
        self._cb.toggled.connect(self._on_collapsed)
        self._ne.textChanged.connect(self._on_name)
        self._alpha_slider.valueChanged.connect(self._on_alpha)
        self._L_btn.toggled.connect(self._on_L_active)
        self._1L_btn.toggled.connect(self._on_1L_active)
        self._C_btn.toggled.connect(self._on_curve_active)
        self._del_btn.clicked.connect(self._on_delete)

        # Group drag via the ⠿ handle
        def _gh_press(event):
            if event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._drag_start[0] = event.pos()

        def _gh_move(event):
            if not (self._drag_start[0]
                    and event.buttons() & QtCore.Qt.MouseButton.LeftButton):
                return
            if (event.pos() - self._drag_start[0]).manhattanLength() < 8:
                return
            self._drag_start[0] = None
            idx = next((i for i, gd in enumerate(_peak_groups) if gd is self._gd), -1)
            if idx < 0:
                return
            drag = QtGui.QDrag(self._gh)
            mime = QtCore.QMimeData()
            mime.setText(f"group_move:{idx}")
            drag.setMimeData(mime)
            drag.exec(QtCore.Qt.DropAction.MoveAction)

        self._gh.mousePressEvent = _gh_press
        self._gh.mouseMoveEvent  = _gh_move

    def _on_highlight(self, checked):
        self._gd["highlight"] = checked
        _peak_session_timer.start()
        self._hl_btn.setStyleSheet(
            "font-size:10px; border:none; color:#4a90d9;" if checked
            else "font-size:10px; border:none; color:#555555;")
        _render_peaks_or_full()

    def _on_collapsed(self, v):
        self._gd["collapsed"] = v
        _peak_session_timer.start()
        self._cb.setText("▶" if v else "▼")
        peaks_rows_container.rebuild_group_layout()

    def _on_name(self, text):
        old = self._gd["name"]
        if old == text:
            return
        self._gd["name"] = text
        _peak_session_timer.start()
        if old in _group_header_widgets and _group_header_widgets[old] is self:
            del _group_header_widgets[old]
        _group_header_widgets[text] = self
        for row in custom_peak_rows:
            if row.get("group_name") == old:
                row["group_name"] = text
        _rebuild_group_selectors()

    def _on_alpha(self, v):
        self._gd["alpha"] = v
        _peak_session_timer.start()
        _render_peaks_or_full()

    def _on_L_active(self, checked):
        self._gd["L_active"] = checked
        _peak_session_timer.start()
        _SS_ON  = "border:1px solid #555; color:#aaa; font-size:10px;"
        _SS_OFF = "border:1px solid #555; color:#444; background:#1a1a1a; font-size:10px;"
        self._L_btn.setStyleSheet(_SS_ON if checked else _SS_OFF)
        _render_peaks_or_full()

    def _on_1L_active(self, checked):
        self._gd["1L_active"] = checked
        _peak_session_timer.start()
        _SS_ON  = "border:1px solid #555; color:#aaa; font-size:10px;"
        _SS_OFF = "border:1px solid #555; color:#444; background:#1a1a1a; font-size:10px;"
        self._1L_btn.setStyleSheet(_SS_ON if checked else _SS_OFF)
        _render_peaks_or_full()

    def _on_curve_active(self, checked):
        self._gd["curve_active"] = checked
        _peak_session_timer.start()
        _SS_ON  = "border:1px solid #555; color:#aaa; font-size:10px;"
        _SS_OFF = "border:1px solid #555; color:#444; background:#1a1a1a; font-size:10px;"
        self._C_btn.setStyleSheet(_SS_ON if checked else _SS_OFF)
        _render_peaks_or_full()

    def _on_delete(self):
        name = self._gd["name"]
        rows_in_group = [r for r in custom_peak_rows
                         if r.get("group_name", "Unclassified") == name]

        if rows_in_group:
            other_groups = [g["name"] for g in _peak_groups if g is not self._gd]

            dlg = QtWidgets.QDialog(peaks_win)
            dlg.setWindowTitle("Delete group")
            dlg.setModal(True)
            vlay = QtWidgets.QVBoxLayout(dlg)

            vlay.addWidget(QtWidgets.QLabel(
                f'Group "{name}" contains {len(rows_in_group)} line(s).\n'
                f'What should happen to them?'))

            # Move-to-group option (only shown when other groups exist)
            move_rb  = None
            grp_combo = None
            if other_groups:
                move_rb = QtWidgets.QRadioButton("Move lines to group:")
                grp_combo = QtWidgets.QComboBox()
                grp_combo.addItems(other_groups)
                move_row = QtWidgets.QHBoxLayout()
                move_row.addWidget(move_rb)
                move_row.addWidget(grp_combo)
                move_row.addStretch()
                vlay.addLayout(move_row)
                move_rb.setChecked(True)   # default when other groups exist

            del_rb = QtWidgets.QRadioButton("Delete lines")
            vlay.addWidget(del_rb)
            if not other_groups:
                del_rb.setChecked(True)

            # Enable/disable combo based on selection
            if move_rb and grp_combo:
                move_rb.toggled.connect(grp_combo.setEnabled)

            btns = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Ok |
                QtWidgets.QDialogButtonBox.StandardButton.Cancel)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)
            vlay.addWidget(btns)

            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return

            push_peak_history()
            if move_rb and move_rb.isChecked():
                target = grp_combo.currentText()
                for r in rows_in_group:
                    r["group_name"] = target
            else:
                for r in rows_in_group:
                    peaks_rows_layout_remove(r["widget"])
                    custom_peak_rows.remove(r)
                _selected_peak_indices.clear()
                _select_anchor[0] = None
        else:
            push_peak_history()

        # Remove group itself
        _peak_groups.remove(self._gd)
        if name in _group_header_widgets:
            del _group_header_widgets[name]
        self.hide()

        peaks_rows_container.rebuild_group_layout()
        update_pick_row_combo()
        _render_peaks_or_full()
        _auto_sync_legend_entries()
        _rebuild_peak_legend_on_plot()
        _sync_confirmation_panel()


splash_step(62, "Setting up the peak lists…")


class PeakRowsContainer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.setAcceptDrops(True)
        self._drag_insert_pos = None
        self._insert_line = QtWidgets.QFrame(self)
        self._insert_line.setFixedHeight(2)
        self._insert_line.setStyleSheet("background-color: #4a90d9;")
        self._insert_line.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._insert_line.hide()
        self._bulk_adding = False

        # Auto-scroll while dragging near the top/bottom edges of the scroll area
        self._autoscroll_timer = QtCore.QTimer(self)
        self._autoscroll_timer.setInterval(50)
        self._autoscroll_timer.timeout.connect(self._do_autoscroll)
        self._autoscroll_dir = 0  # -1 = up, +1 = down

    def add_row_widget(self, widget):
        if not self._bulk_adding:
            self.rebuild_group_layout()

    def remove_row_widget(self, widget):
        self._layout.removeWidget(widget)
        widget.hide()

    def rebuild_group_layout(self):
        """Clear and rebuild the layout: group headers interleaved with their rows."""
        self.setUpdatesEnabled(False)
        try:
            # Remove from end (O(1) each) rather than front (O(n) each)
            while self._layout.count():
                item = self._layout.takeAt(self._layout.count() - 1)
                if item and item.widget():
                    item.widget().hide()

            known = {gd["name"] for gd in _peak_groups}

            for gd in _peak_groups:
                name = gd["name"]
                if name not in _group_header_widgets:
                    hdr = PeakGroupHeader(gd)
                    hdr.setParent(self)
                    _group_header_widgets[name] = hdr
                hdr = _group_header_widgets[name]
                hdr.show()
                self._layout.addWidget(hdr)

                if not gd.get("collapsed", False):
                    for row in custom_peak_rows:
                        if row.get("group_name", "Unclassified") == name:
                            row["widget"].show()
                            self._layout.addWidget(row["widget"])
                # collapsed rows remain hidden (set above)

            # Safety: rows whose group no longer exists land at the bottom
            for row in custom_peak_rows:
                if row.get("group_name", "Unclassified") not in known:
                    row["widget"].show()
                    self._layout.addWidget(row["widget"])
        finally:
            self.setUpdatesEnabled(True)

    # ── Row-level helpers ──────────────────────────────────────────────────────

    def _get_insert_pos(self, y):
        visible = sorted(
            ((i, row) for i, row in enumerate(custom_peak_rows) if row["widget"].isVisible()),
            key=lambda ir: ir[1]["widget"].y())
        for i, row in visible:
            w = row["widget"]
            if y < w.y() + w.height() // 2:
                return i
        return len(custom_peak_rows)

    def _get_target_group(self, y):
        """Return name of the group whose header is at or above y."""
        result = _peak_groups[0]["name"] if _peak_groups else "Unclassified"
        for gd in _peak_groups:
            hdr = _group_header_widgets.get(gd["name"])
            if hdr and hdr.y() <= y:
                result = gd["name"]
        return result

    def _show_insert_line(self, pos, target_group=None):
        n = len(custom_peak_rows)
        if n == 0:
            self._insert_line.hide()
            return
        if pos == 0:
            y = custom_peak_rows[0]["widget"].y()
        elif pos >= n:
            w = custom_peak_rows[-1]["widget"]
            y = w.y() + w.height()
        else:
            y = custom_peak_rows[pos]["widget"].y()
        if target_group is not None:
            # Don't let the line cross into the next group's header area
            found = False
            for gd in _peak_groups:
                if found:
                    hdr = _group_header_widgets.get(gd["name"])
                    if hdr and hdr.isVisible():
                        y = min(y, hdr.y())
                    break
                if gd["name"] == target_group:
                    found = True
        self._insert_line.setGeometry(0, y - 1, self.width(), 2)
        self._insert_line.raise_()
        self._insert_line.show()

    # ── Group-level helpers ────────────────────────────────────────────────────

    def _get_group_insert_pos(self, y):
        """Return insertion index into _peak_groups based on y."""
        for i, gd in enumerate(_peak_groups):
            hdr = _group_header_widgets.get(gd["name"])
            if hdr and y < hdr.y() + hdr.height() // 2:
                return i
        return len(_peak_groups)

    def _show_group_insert_line(self, pos):
        n = len(_peak_groups)
        if n == 0:
            self._insert_line.hide()
            return
        if pos == 0:
            hdr = _group_header_widgets.get(_peak_groups[0]["name"])
            y = hdr.y() if hdr else 0
        elif pos >= n:
            hdr = _group_header_widgets.get(_peak_groups[-1]["name"])
            if hdr:
                y = hdr.y() + hdr.height()
                gname = _peak_groups[-1]["name"]
                for row in reversed(custom_peak_rows):
                    if row.get("group_name") == gname and row["widget"].isVisible():
                        y = row["widget"].y() + row["widget"].height()
                        break
            else:
                y = self.height()
        else:
            hdr = _group_header_widgets.get(_peak_groups[pos]["name"])
            y = hdr.y() if hdr else 0
        self._insert_line.setGeometry(0, y - 1, self.width(), 2)
        self._insert_line.raise_()
        self._insert_line.show()

    def _move_group(self, from_idx, insert_before):
        """Reorder _peak_groups and re-sort custom_peak_rows to match."""
        n = len(_peak_groups)
        if from_idx < 0 or from_idx >= n:
            return
        if insert_before == from_idx or insert_before == from_idx + 1:
            return
        gd = _peak_groups.pop(from_idx)
        if insert_before > from_idx:
            insert_before -= 1
        _peak_groups.insert(insert_before, gd)
        # Re-sort rows to match new group order
        ordered = []
        known = {g["name"] for g in _peak_groups}
        for g in _peak_groups:
            for row in custom_peak_rows:
                if row.get("group_name") == g["name"]:
                    ordered.append(row)
        for row in custom_peak_rows:
            if row.get("group_name") not in known:
                ordered.append(row)
        custom_peak_rows.clear()
        custom_peak_rows.extend(ordered)
        self.rebuild_group_layout()
        update_pick_row_combo()
        _render_peaks_or_full()

    # ── Qt drag/drop events ────────────────────────────────────────────────────

    def dragEnterEvent(self, event):
        if not event.mimeData().hasText():
            return
        event.acceptProposedAction()
        text = event.mimeData().text()
        y = int(event.position().y())
        if text.startswith("group_move:"):
            self._drag_insert_pos = self._get_group_insert_pos(y)
            self._show_group_insert_line(self._drag_insert_pos)
        else:
            self._drag_insert_pos = self._get_insert_pos(y)
            self._show_insert_line(self._drag_insert_pos, target_group=self._get_target_group(y))

    def dragMoveEvent(self, event):
        if not event.mimeData().hasText():
            return
        event.acceptProposedAction()
        text = event.mimeData().text()
        y = int(event.position().y())
        if text.startswith("group_move:"):
            self._drag_insert_pos = self._get_group_insert_pos(y)
            self._show_group_insert_line(self._drag_insert_pos)
        else:
            self._drag_insert_pos = self._get_insert_pos(y)
            self._show_insert_line(self._drag_insert_pos, target_group=self._get_target_group(y))

        # Auto-scroll when cursor is within 40 px of the viewport top/bottom edge
        try:
            vp  = peaks_rows_scroll.viewport()
            pos = vp.mapFromGlobal(QtGui.QCursor.pos())
            margin = 40
            if pos.y() < margin:
                self._autoscroll_dir = -1
                if not self._autoscroll_timer.isActive():
                    self._autoscroll_timer.start()
            elif pos.y() > vp.height() - margin:
                self._autoscroll_dir = 1
                if not self._autoscroll_timer.isActive():
                    self._autoscroll_timer.start()
            else:
                self._autoscroll_dir = 0
                self._autoscroll_timer.stop()
        except Exception:
            pass

    def _do_autoscroll(self):
        try:
            sb = peaks_rows_scroll.verticalScrollBar()
            step = 20 * self._autoscroll_dir
            sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() + step)))
        except Exception:
            self._autoscroll_timer.stop()

    def dragLeaveEvent(self, event):
        self._autoscroll_timer.stop()
        self._autoscroll_dir = 0
        self._insert_line.hide()
        self._drag_insert_pos = None

    def dropEvent(self, event):
        self._autoscroll_timer.stop()
        self._autoscroll_dir = 0
        self._insert_line.hide()
        if not event.mimeData().hasText():
            return
        text = event.mimeData().text()
        y = int(event.position().y())

        if text.startswith("group_move:"):
            try:
                from_idx = int(text[11:])
            except ValueError:
                return
            insert_pos = self._drag_insert_pos
            if insert_pos is None:
                insert_pos = self._get_group_insert_pos(y)
            self._move_group(from_idx, insert_pos)
            self._drag_insert_pos = None
            event.acceptProposedAction()
            return

        insert_pos = self._drag_insert_pos
        if insert_pos is None:
            insert_pos = self._get_insert_pos(y)
        target_group = self._get_target_group(y)
        if text.startswith("multi:"):
            try:
                indices = [int(x) for x in text[6:].split(",")]
            except ValueError:
                return
            self.move_rows(indices, insert_pos, target_group=target_group)
        else:
            try:
                self.move_rows([int(text)], insert_pos, target_group=target_group)
            except ValueError:
                return
        self._drag_insert_pos = None
        event.acceptProposedAction()

    def move_row(self, from_idx, to_idx):
        self.move_rows([from_idx], to_idx)

    def move_rows(self, from_indices, insert_before, target_group=None):
        from_indices = sorted(set(int(i) for i in from_indices))
        n = len(custom_peak_rows)
        from_indices = [i for i in from_indices if 0 <= i < n]
        if not from_indices:
            return
        from_set = set(from_indices)
        insert_before = max(0, min(int(insert_before), n))
        rows_to_move = [custom_peak_rows[i] for i in from_indices]
        remaining = [r for i, r in enumerate(custom_peak_rows) if i not in from_set]
        n_before = sum(1 for i in from_indices if i < insert_before)
        adj = max(0, min(insert_before - n_before, len(remaining)))
        new_order = remaining[:adj] + rows_to_move + remaining[adj:]
        custom_peak_rows.clear()
        custom_peak_rows.extend(new_order)
        if target_group is not None:
            for row in rows_to_move:
                row["group_name"] = target_group
        self.rebuild_group_layout()
        new_sel = {new_i for new_i, row in enumerate(custom_peak_rows) if row in rows_to_move}
        _selected_peak_indices.clear()
        _selected_peak_indices.update(new_sel)
        _update_selection_visuals()
        update_pick_row_combo()
        if any(_row_draws(r) for r in rows_to_move):   # e.g. a new blank row: no redraw
            _render_peaks_or_full()
        _auto_sync_legend_entries()
        _rebuild_peak_legend_on_plot()
        _sync_confirmation_panel()

peaks_rows_container = PeakRowsContainer()
peaks_rows_scroll    = QtWidgets.QScrollArea()
peaks_rows_scroll.setWidgetResizable(True)
peaks_rows_scroll.setWidget(peaks_rows_container)
# peaks_rows_scroll.setMaximumHeight(320)
peaks_layout.addWidget(peaks_rows_scroll)

def peaks_rows_layout_add(widget):    peaks_rows_container.add_row_widget(widget)
def peaks_rows_layout_remove(widget): peaks_rows_container.remove_row_widget(widget)

_selected_peak_indices = set()   # indices into custom_peak_rows that are selected
_select_anchor         = [None]  # anchor index for shift-click range selection
_peak_row_clipboard    = []      # snapshots for copy/paste
_last_edited_row       = [None]  # row whose peaks_input or label_input was last focused

def _update_selection_visuals():
    for i, row in enumerate(custom_peak_rows):
        sh = row.get("sel_handle")
        if sh is None:
            continue
        if i in _selected_peak_indices:
            sh.setStyleSheet(
                "min-width:12px;max-width:12px;border:1px solid #4a90d9;"
                "background:#4a90d9;border-radius:2px;margin:1px;")
        else:
            sh.setStyleSheet(
                "min-width:12px;max-width:12px;border:1px solid #555;"
                "background:transparent;border-radius:2px;margin:1px;")

class _PeaksWinSelClearFilter(QtCore.QObject):
    """Clear peak-row selection when the user clicks anywhere in the peaks
    window that is not a selection handle.
    Button clicks are intentional actions ON the selection and must not clear
    it — the app-level event filter fires before any button signal, so without
    this guard the selection would always be empty by the time a handler runs."""
    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.Type.MouseButtonPress:
            return False
        if not isinstance(obj, QtWidgets.QWidget):
            return False
        if isinstance(obj, QtWidgets.QAbstractButton):
            return False
        p = obj
        while p is not None:
            if p is peaks_win:
                sel_handles  = {r.get("sel_handle")  for r in custom_peak_rows}
                drag_handles = {r.get("drag_handle") for r in custom_peak_rows}
                if obj not in sel_handles and obj not in drag_handles:
                    _selected_peak_indices.clear()
                    _select_anchor[0] = None
                    _update_selection_visuals()
                break
            p = p.parentWidget()
        return False

_peaks_win_sel_filter = _PeaksWinSelClearFilter(app)
# Installed once the event loop runs: it only reacts to clicks, and every
# event passes through it, which slowed building the rows at start-up.
QtCore.QTimer.singleShot(0, lambda: app.installEventFilter(_peaks_win_sel_filter))

def _is_legend_candidate(row) -> bool:
    """A row can appear in the legend when it has a label and peaks."""
    return bool(row["label_input"].text().strip()
                and row["peaks_input"].text().strip())

def _rows_in_legend_order():
    """Peak rows by group (in group order), then in their order in the Peaks window."""
    group_pos = {g["name"]: i for i, g in enumerate(_peak_groups)}
    return [r for _, _, r in sorted(
        (group_pos.get(r.get("group_name", "Unclassified"), len(group_pos)), i, r)
        for i, r in enumerate(custom_peak_rows))]

def _fill_missing_row_symbols(reassign_all: bool = False):
    """Give every legend row without a symbol one that is not used yet.

    Symbols that are already set are never changed, unless reassign_all
    (the explicit "Reassign all symbols" button). Rows first adopt the symbol
    and column saved for their label by Droplet < 3.2.
    """
    rows = [r for r in _rows_in_legend_order() if _is_legend_candidate(r)]
    if reassign_all:
        for r in rows:
            r["legend_symbol"][0] = None
    # How often each symbol is used; a new row gets the least-used one (first
    # in list order on ties), so after the last symbol they cycle evenly.
    usage = {m: 0 for m in MARKER_SYMBOLS}
    for r in custom_peak_rows:
        if r["legend_symbol"][0] in usage:
            usage[r["legend_symbol"][0]] += 1
    for r in rows:
        if r["legend_symbol"][0] is not None:
            continue
        legacy = {} if reassign_all else _legacy_legend_map.get(r["label_input"].text().strip(), {})
        sym = legacy.get("symbol")
        if not sym:
            sym = min(MARKER_SYMBOLS, key=lambda m: usage[m])
        r["legend_symbol"][0] = sym
        if sym in usage:
            usage[sym] += 1
        if r["legend_col"][0] is None and legacy.get("col", 1) != 1:
            r["legend_col"][0] = legacy["col"]

# ── Peak-list session: line and group states survive a restart ──
# The peak list is reloaded from its file at startup; unsaved states are kept
# in a snapshot (QSettings "peak_session") and re-applied, as long as the file
# itself has not changed since:
#   lines  – tick, label modes (L / 1L), envelope, legend symbol and column
#   groups – opacity, highlight (●), collapsed, L / 1L / envelope (⌒) toggles
_GROUP_SESSION_KEYS = ("alpha", "collapsed", "highlight", "L_active", "1L_active", "curve_active")

def _peak_file_signature(path):
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None

def _save_peak_session():
    path = settings.value("last_peak_file", "")
    if not path or not os.path.isfile(path):
        return
    data = {"file": path, "signature": _peak_file_signature(path),
            "rows": [{"label": r["label_input"].text(), "peaks": r["peaks_input"].text(),
                      "checked": r["checkbox"].isChecked(),
                      "L_state": r.get("L_state", [0])[0],
                      "1L_state": r.get("1L_state", [0])[0],
                      "envelope": r["envelope_btn"].isChecked(),
                      **_row_legend_fields(r)}
                     for r in custom_peak_rows],
            "groups": [{"name": gd["name"], **{k: gd[k] for k in _GROUP_SESSION_KEYS if k in gd}}
                       for gd in _peak_groups]}
    settings.setValue("peak_session", json.dumps(data))

_peak_session_timer = QtCore.QTimer()
_peak_session_timer.setSingleShot(True)
_peak_session_timer.setInterval(1000)
_peak_session_timer.timeout.connect(_save_peak_session)

def _restore_peak_session(path):
    """Re-apply the last session's ticks / symbols / columns to the rows just
    loaded from `path`, if that file is unchanged since the snapshot."""
    try:
        data = json.loads(settings.value("peak_session", "") or "{}")
    except Exception:
        return
    if data.get("file") != path or data.get("signature") != _peak_file_signature(path):
        return
    saved = data.get("rows", [])
    key = lambda label, peaks: (label.strip(), peaks.strip())
    rows = list(custom_peak_rows)
    if len(saved) == len(rows) and all(
            key(s.get("label", ""), s.get("peaks", "")) == key(r["label_input"].text(), r["peaks_input"].text())
            for s, r in zip(saved, rows)):
        pairs = list(zip(rows, saved))
    else:                                    # match rows by label and peaks
        pool = {}
        for s in saved:
            pool.setdefault(key(s.get("label", ""), s.get("peaks", "")), []).append(s)
        pairs = []
        for r in rows:
            cands = pool.get(key(r["label_input"].text(), r["peaks_input"].text()))
            if cands:
                pairs.append((r, cands.pop(0)))
    for r, s in pairs:
        cb = r["checkbox"]
        cb.blockSignals(True); cb.setChecked(bool(s.get("checked", True))); cb.blockSignals(False)
        r["legend_symbol"][0], r["legend_col"][0] = None, None
        _apply_row_legend_fields(r, s)
        for k in ("L_state", "1L_state"):
            if k in s and r.get(k) is not None:
                r[k][0] = s[k]
                r["refresh_L"]() if k == "L_state" else r["refresh_1L"]()
        if "envelope" in s:
            eb = r["envelope_btn"]
            eb.blockSignals(True); eb.setChecked(bool(s["envelope"])); eb.blockSignals(False)
    saved_groups = {g.get("name"): g for g in data.get("groups", [])}
    groups_changed = False
    for gd in _peak_groups:
        sg = saved_groups.get(gd["name"])
        if sg:
            for k in _GROUP_SESSION_KEYS:
                if k in sg and gd.get(k) != sg[k]:
                    gd[k] = sg[k]; groups_changed = True
    if groups_changed:                      # headers read their values when created
        for hdr in _group_header_widgets.values():
            hdr.hide()
        _group_header_widgets.clear()
        peaks_rows_container.rebuild_group_layout()
    if pairs or groups_changed:
        _clear_highlight_cache()
        render_plot()
        _auto_sync_legend_entries()
        _rebuild_peak_legend_on_plot()
        _sync_confirmation_panel()

def _auto_sync_legend_entries():
    """Rebuild _legend_entries from the peak rows (the only source of truth).

    Rows sharing a label give one entry (the first row's colour, symbol and
    column). Unticked rows are kept, flagged "checked": False, so the
    on-screen legend can offer them; exports leave them out.
    """
    global _legend_entries, _legend_row_entries
    _fill_missing_row_symbols()
    all_rows, entries, seen = [], [], set()
    for row in _rows_in_legend_order():
        if not _is_legend_candidate(row):
            continue
        label = row["label_input"].text().strip()
        sym = row["legend_symbol"][0]
        e = {
            "label":   label,
            "color":   QtGui.QColor(row["color"][0]),
            "symbol":  sym or None,
            "col":     row["legend_col"][0] or 1,
            "row":     row,
            "checked": row["checkbox"].isChecked(),
            "shown":   True,
        }
        all_rows.append(e)
        if label not in seen:
            seen.add(label)
            entries.append(e)
    for extra in _legend_extra_entries:
        e = {**extra, "row": None, "extra": extra, "checked": True, "shown": True}
        all_rows.append(e)
        if extra["label"] not in seen:
            seen.add(extra["label"])
            entries.append(e)
    _legend_entries = entries
    _legend_row_entries = all_rows
    _peak_session_timer.start()
    for row in custom_peak_rows:
        if row.get("refresh_symbol"):
            row["refresh_symbol"]()
    for listener in list(_legend_listeners):
        try:
            listener()
        except RuntimeError:        # a dialog that has been deleted
            _legend_listeners.remove(listener)

def push_peak_history():
    global _peak_redo
    if _history_locked: return
    _peak_history.append(_snapshot_peaks())
    _peak_redo.clear()
    _update_undo_redo_buttons()


# Per-sub-plot peak item tracking for stacked mode peak-only redraws
_stacked_peak_items = []   # list of lists: one inner list of items per sub-plot

def _redraw_stacked_peaks_only():
    """
    In stacked mode: remove only the peak highlight/label items from each
    sub-plot and redraw them, without touching the spectrum curves or
    destroying/recreating any ViewBox. This preserves the current zoom.
    """
    global _stacked_peak_items
    # Remove previously drawn peak items from each sub-plot
    for sub_idx, items in enumerate(_stacked_peak_items):
        if sub_idx >= len(_stacked_sub_plots):
            break
        sub = _stacked_sub_plots[sub_idx]
        for item in items:
            try:
                sub.removeItem(item)
            except Exception:
                pass
    _stacked_peak_items = []

    # Redraw peaks and envelope curves on each sub-plot
    for sub_idx, sub in enumerate(_stacked_sub_plots):
        _mirror_this = False
        if _stacked_mirror:
            mirror_set = (0 if _stacked_mirror_odd else 1)
            _mirror_this = (sub_idx % 2 == mirror_set)
        data_df, mz_vals, int_vals = _stacked_spectra_data[sub_idx]
        items_this_sub = _draw_stacked_peak_labels(
            sub, data_df, mz_vals, int_vals, return_items=True, mirrored=_mirror_this)
        env_items = _draw_stacked_envelope_lines(sub, data_df, mz_vals, int_vals)
        _stacked_peak_items.append((items_this_sub or []) + env_items)

def _render_peaks_or_full():
    """Route peak row changes: peak-only redraw in stacked mode, full render otherwise."""
    if _stacked_mode and _stacked_sub_plots:
        _redraw_stacked_peaks_only()
    else:
        render_plot()



def _add_peak_row_base(checked=True, color=None, peaks_text="", label_text="", range_values=None, group_name="Unclassified"):
    global next_color_index
    if color is None:
        color = default_peak_colors[next_color_index]
        next_color_index = (next_color_index + 1) % len(default_peak_colors)

    row_widget = QtWidgets.QWidget()
    row_layout = QtWidgets.QHBoxLayout()
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_widget.setLayout(row_layout)

    sel_handle = QtWidgets.QLabel()
    sel_handle.setFixedWidth(14)
    sel_handle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    sel_handle.setToolTip(
        "Click to select  |  Ctrl: toggle  |  Shift: range  |  Ctrl+Shift: extend range")
    sel_handle.setStyleSheet(
        "min-width:12px;max-width:12px;border:1px solid #555;"
        "background:transparent;border-radius:2px;margin:1px;")

    _gn = group_name if any(g["name"] == group_name for g in _peak_groups) else (
        _peak_groups[0]["name"] if _peak_groups else "Unclassified")

    drag_handle = QtWidgets.QLabel("⠿")
    drag_handle.setFixedWidth(10)
    drag_handle.setCursor(QtCore.Qt.CursorShape.SizeVerCursor)
    drag_handle.setToolTip("Drag to reorder")
    drag_handle.setStyleSheet("color: gray; font-size: 14px;")

    checkbox  = QtWidgets.QCheckBox(); checkbox.setChecked(checked)
    btn       = QtWidgets.QPushButton(); btn.setFixedSize(22, 22)
    row_color = [QtGui.QColor(color)]

    def update_btn():
        btn.setStyleSheet(f"background-color: {row_color[0].name()}; border: 1px solid gray;")
    def set_color(chosen):
        row_color[0] = QtGui.QColor(chosen); update_btn(); _render_peaks_or_full(); _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
    def pick():
        chosen = QtWidgets.QColorDialog.getColor(row_color[0], peaks_win, "Pick a color")
        if chosen.isValid():
            set_color(chosen)
    btn.clicked.connect(pick); update_btn()

    # ── Mode toggle ──
    mode_btn = QtWidgets.QPushButton("≡")
    mode_btn.setFixedSize(22, 22)
    mode_btn.setToolTip("Switch between manual entry and range (start:step:end)")
    mode_btn.setCheckable(True)

    # ── Manual input ──
    peaks_input = QtWidgets.QLineEdit()
    peaks_input.setPlaceholderText("Peak m/z values (comma-separated)…")
    peaks_input.setMinimumWidth(50)
    peaks_input.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
    peaks_input.setText(peaks_text)
    peaks_input.setCursorPosition(0)

    # ── Range input ──
    range_widget = QtWidgets.QWidget()
    range_layout = QtWidgets.QHBoxLayout(range_widget)
    range_layout.setContentsMargins(0, 0, 0, 0)
    range_layout.setSpacing(2)

    range_start = _TrimmedDoubleSpinBox()
    range_start.setRange(0.0, 100000.0); range_start.setDecimals(4)
    range_start.setSingleStep(1.0); range_start.setMinimumWidth(50)
    range_start.setToolTip("Start m/z")

    range_step = _TrimmedDoubleSpinBox()
    range_step.setRange(0.0001, 10000.0); range_step.setDecimals(4)
    range_step.setSingleStep(1.0); range_step.setMinimumWidth(50)
    range_step.setValue(1.0); range_step.setToolTip("Step (interval)")

    range_end = _TrimmedDoubleSpinBox()
    range_end.setRange(0.0, 100000.0); range_end.setDecimals(4)
    range_end.setSingleStep(1.0); range_end.setMinimumWidth(50)
    range_end.setToolTip("End m/z")

    for sb in (range_start, range_step, range_end):
        sb.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                         QtWidgets.QSizePolicy.Policy.Fixed)

    range_layout.addWidget(QtWidgets.QLabel(" from"))
    range_layout.addWidget(range_start)
    range_layout.addWidget(QtWidgets.QLabel(" to"))
    range_layout.addWidget(range_end)
    range_layout.addWidget(QtWidgets.QLabel(" step"))
    range_layout.addWidget(range_step)
    range_widget.setVisible(False)
    range_layout.addStretch()

    def _range_to_peaks_text():
        start = range_start.value()
        step  = range_step.value()
        end   = range_end.value()
        if step <= 0 or start > end:
            return ""
        values = np.arange(start, end + step * 0.5, step)
        def _fmt(v):
            return f"{v:.4f}".rstrip('0').rstrip('.')
        return ", ".join(_fmt(v) for v in values)

    def _on_range_changed():
        peaks_input.blockSignals(True)
        peaks_input.setText(_range_to_peaks_text())
        peaks_input.blockSignals(False)
        _clear_highlight_cache()
        _peak_text_timer.start()

    def _on_mode_toggled(checked):
        peaks_input.setVisible(not checked)
        range_widget.setVisible(checked)
        mode_btn.setText("↔" if checked else "≡")
        mode_btn.setToolTip(
            "Switch to manual entry" if checked
            else "Switch to range (start:step:end)")
        if checked:
            # Pre-fill range from current manual text if possible
            vals = parse_peaks_text(peaks_input.text())
            if len(vals) >= 2:
                range_start.blockSignals(True)
                range_end.blockSignals(True)
                range_step.blockSignals(True)
                range_start.setValue(min(vals))
                range_end.setValue(max(vals))
                if len(vals) >= 2:
                    range_step.setValue(round(vals[1] - vals[0], 4) if vals[1] > vals[0] else 1.0)
                range_start.blockSignals(False)
                range_end.blockSignals(False)
                range_step.blockSignals(False)
            _on_range_changed()
        else:
            _clear_highlight_cache()
            _render_peaks_or_full()

    mode_btn.toggled.connect(_on_mode_toggled)
    range_start.valueChanged.connect(lambda _: _on_range_changed())
    range_step.valueChanged.connect(lambda _: _on_range_changed())
    range_end.valueChanged.connect(lambda _: _on_range_changed())

    if range_values is not None:
        range_start.blockSignals(True)
        range_step.blockSignals(True)
        range_end.blockSignals(True)
        range_start.setValue(range_values[0])
        range_end.setValue(range_values[1])
        range_step.setValue(range_values[2])
        range_start.blockSignals(False)
        range_step.blockSignals(False)
        range_end.blockSignals(False)
        mode_btn.setChecked(True)  # _on_mode_toggled is now connected, works correctly

    label_input = QtWidgets.QLineEdit()
    label_input.setPlaceholderText("Hover label…")
    label_input.setMinimumWidth(80)
    label_input.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
    label_input.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                              QtWidgets.QSizePolicy.Policy.Fixed)
    label_input.setText(label_text)
    label_input.setCursorPosition(0)

    remove_btn = QtWidgets.QPushButton("×"); remove_btn.setFixedSize(22, 22)

    envelope_btn = QtWidgets.QPushButton("⌒")
    envelope_btn.setFixedSize(22, 22)
    envelope_btn.setCheckable(True)
    envelope_btn.setChecked(False)
    envelope_btn.setToolTip(
        "Show isotopic envelope line through this cluster's peaks.\n"
        "Connects detected peak tops with a smooth line to help assess\n"
        "whether the cluster has one or multiple contributions.")

    row_layout.addWidget(sel_handle)
    row_layout.addWidget(drag_handle)
    row_layout.addWidget(checkbox); row_layout.addWidget(btn)

    # ── Legend symbol (same value as in Plot → Legend parameters) ──
    sym_btn = QtWidgets.QToolButton()
    sym_btn.setFixedSize(22, 22)
    sym_btn.setAutoRaise(True)
    sym_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
    sym_menu = QtWidgets.QMenu(sym_btn)
    sym_btn.setMenu(sym_menu)

    def _set_row_symbol(code):
        row_data["legend_symbol"][0] = code
        _legend_model_changed()

    def refresh_symbol():
        code = row_data["legend_symbol"][0]
        state = (code, row_color[0].rgba())
        if row_data.get("_sym_state") == state:
            return
        row_data["_sym_state"] = state
        name = MARKER_SYMBOL_NAMES.get(code, "none") if code else "none"
        sym_btn.setToolTip(f"Legend symbol: {name}\n"
                           "(shown when Plot → Legend parameters is in Color + symbol mode)")
        icon = _legend_symbol_icon(code, row_color[0])
        if icon is None:                       # startup, before drawing code exists
            row_data["_sym_state"] = None
            sym_btn.setText("·"); return
        sym_btn.setText("")
        sym_btn.setIcon(icon)

    def _build_symbol_menu():
        # Filled when opened (not per row up front: that made loading long
        # peak lists slow), and refilled when the row colour has changed.
        if not sym_menu.isEmpty() and sym_menu.property("color") == row_color[0].name():
            return
        sym_menu.clear()
        none_act = sym_menu.addAction("None")
        none_act.triggered.connect(lambda _=False: _set_row_symbol(""))
        for _code, _name in MARKER_SYMBOL_NAMES.items():
            act = sym_menu.addAction(_legend_symbol_icon(_code, row_color[0]), _name)
            act.triggered.connect(lambda _=False, c=_code: _set_row_symbol(c))
        sym_menu.setProperty("color", row_color[0].name())
    sym_menu.aboutToShow.connect(_build_symbol_menu)
    sym_btn.setText("·")
    row_layout.addWidget(sym_btn)
    row_layout.addWidget(mode_btn)
    row_layout.addWidget(peaks_input)
    row_layout.addWidget(range_widget)
    row_layout.addWidget(label_input)

    # ── Per-row label/mass cycle buttons ──
    # State: 0=off, 1=mass only, 2=label only, 3=mass+label
    _L_state  = [0]
    _1L_state = [0]
    _row_ref  = [None]   # filled in after row_data is created

    _L_STYLES = {
      0: ("L",   "border:1px solid gray; color:gray;"),
      1: ("m",   "border:1px solid gray; background:#2d6e3e; color:white;"),
      2: ("L",   "border:1px solid gray; background:#2d4a8a; color:white;"),
      3: ("mL",  "border:1px solid gray; background:#8a5c2d; color:white;"),
    }
    _1L_STYLES = {
      0: ("1L",  "border:1px solid gray; color:gray;"),
      1: ("1m",  "border:1px solid gray; background:#2d6e3e; color:white;"),
      2: ("1L",  "border:1px solid gray; background:#2d4a8a; color:white;"),
      3: ("1mL", "border:1px solid gray; background:#8a5c2d; color:white;"),
    }

    L_btn  = QtWidgets.QPushButton("L");  L_btn.setFixedSize(28, 22)
    lL_btn = QtWidgets.QPushButton("1L"); lL_btn.setFixedSize(32, 22)
    L_btn.setToolTip("Cycle: off → mass → label → mass+label  (all peaks in this row)\nShift+click: apply new state to all rows in this group")
    lL_btn.setToolTip("Cycle: off → mass → label → mass+label  (first/lowest-m/z peak only)\nShift+click: apply new state to all rows in this group")

    def _refresh_L():
        t, s = _L_STYLES[_L_state[0]];  L_btn.setText(t);  L_btn.setStyleSheet(s)
    def _refresh_1L():
        t, s = _1L_STYLES[_1L_state[0]]; lL_btn.setText(t); lL_btn.setStyleSheet(s)

    def _cycle_L():
        _L_state[0] = (_L_state[0] + 1) % 4; _refresh_L()
        if (QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                and _row_ref[0] is not None):
            new_st = _L_state[0]
            gname  = _row_ref[0].get("group_name", "Unclassified")
            for _r in custom_peak_rows:
                if _r is not _row_ref[0] and _r.get("group_name", "Unclassified") == gname:
                    _r["L_state"][0] = new_st; _r["refresh_L"]()
        _last_edited_row[0] = _row_ref[0]
        _peak_text_timer.start()

    def _cycle_1L():
        _1L_state[0] = (_1L_state[0] + 1) % 4; _refresh_1L()
        if (QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                and _row_ref[0] is not None):
            new_st = _1L_state[0]
            gname  = _row_ref[0].get("group_name", "Unclassified")
            for _r in custom_peak_rows:
                if _r is not _row_ref[0] and _r.get("group_name", "Unclassified") == gname:
                    _r["1L_state"][0] = new_st; _r["refresh_1L"]()
        _last_edited_row[0] = _row_ref[0]
        _peak_text_timer.start()

    L_btn.clicked.connect(_cycle_L)
    lL_btn.clicked.connect(_cycle_1L)
    _refresh_L(); _refresh_1L()

    zoom_btn = QtWidgets.QPushButton("⊙")
    zoom_btn.setFixedSize(22, 22)
    zoom_btn.setToolTip(
        "Zoom the plot to fit this peak list.\n"
        "Adjusts X range to span all peaks with padding.")

    row_layout.addWidget(L_btn)
    row_layout.addWidget(lL_btn)
    row_layout.addWidget(zoom_btn)
    row_layout.addWidget(envelope_btn)
    row_layout.addWidget(remove_btn)

    row_data = {"widget": row_widget, "checkbox": checkbox,
            "color": row_color, "peaks_input": peaks_input, "label_input": label_input,
            "mode_btn": mode_btn, "envelope_btn": envelope_btn, "zoom_btn": zoom_btn,
            "L_btn": L_btn, "lL_btn": lL_btn,
            "L_state":  _L_state, "1L_state": _1L_state,
            "refresh_L": _refresh_L, "refresh_1L": _refresh_1L,
            "range_start": range_start, "range_step": range_step, "range_end": range_end,
            "sel_handle": sel_handle, "drag_handle": drag_handle, "group_name": _gn,
            "legend_symbol": [None], "legend_col": [None], "legend_show": [True],
            "set_color": set_color, "sym_btn": sym_btn, "refresh_symbol": refresh_symbol}
    _row_ref[0] = row_data

    def _zoom_to_this_row(_checked=False, _rd=row_data):
        peaks = parse_peaks_text(_rd["peaks_input"].text())
        if not peaks: return
        lo, hi = min(peaks), max(peaks)
        pad = max((hi - lo) * 0.15, 5.0)
        if _stacked_mode and _stacked_sub_plots:
            _stacked_sub_plots[0].vb.setXRange(lo - pad, hi + pad, padding=0)
        else:
            plot.vb.setXRange(lo - pad, hi + pad, padding=0)

    zoom_btn.clicked.connect(_zoom_to_this_row)

    # ── Selection handle click ──
    def _sel_press(event, _rd=row_data):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        idx = custom_peak_rows.index(_rd) if _rd in custom_peak_rows else -1
        if idx < 0:
            return
        mods = QtWidgets.QApplication.keyboardModifiers()
        ctrl  = bool(mods & QtCore.Qt.KeyboardModifier.ControlModifier)
        shift = bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if ctrl and shift:
            # Extend selection: add range from anchor to here
            if _select_anchor[0] is not None:
                lo, hi = min(_select_anchor[0], idx), max(_select_anchor[0], idx)
                _selected_peak_indices.update(range(lo, hi + 1))
            else:
                _selected_peak_indices.add(idx)
                _select_anchor[0] = idx
        elif shift and _select_anchor[0] is not None:
            # Replace selection with range anchor→here
            lo, hi = min(_select_anchor[0], idx), max(_select_anchor[0], idx)
            _selected_peak_indices.clear()
            _selected_peak_indices.update(range(lo, hi + 1))
        elif ctrl:
            # Toggle this row
            if idx in _selected_peak_indices:
                _selected_peak_indices.discard(idx)
            else:
                _selected_peak_indices.add(idx)
                _select_anchor[0] = idx
        else:
            # Select only this row
            _selected_peak_indices.clear()
            _selected_peak_indices.add(idx)
            _select_anchor[0] = idx
        _update_selection_visuals()

    sel_handle.mousePressEvent = _sel_press

    # ── Drag reorder via drag_handle ──
    _drag_start = [None]

    def _handle_press(event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            _drag_start[0] = event.pos()

    def _handle_move(event):
        if not (_drag_start[0] and event.buttons() & QtCore.Qt.MouseButton.LeftButton): return
        if (event.pos() - _drag_start[0]).manhattanLength() < 8: return
        _drag_start[0] = None
        idx = custom_peak_rows.index(row_data) if row_data in custom_peak_rows else -1
        if idx < 0: return
        drag = QtGui.QDrag(row_widget)
        mime = QtCore.QMimeData()
        if idx in _selected_peak_indices and len(_selected_peak_indices) > 1:
            indices = sorted(_selected_peak_indices)
            mime.setText("multi:" + ",".join(str(i) for i in indices))
        else:
            mime.setText(str(idx))
        drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.MoveAction)

    drag_handle.mousePressEvent = _handle_press
    drag_handle.mouseMoveEvent  = _handle_move

    def on_remove():
        idx = custom_peak_rows.index(row_data) if row_data in custom_peak_rows else -1
        # Multi-selection: if this row is part of a multi-selection offer to delete all
        if idx >= 0 and idx in _selected_peak_indices and len(_selected_peak_indices) > 1:
            n = len(_selected_peak_indices)
            ans = QtWidgets.QMessageBox.question(
                peaks_win, "Remove lines",
                f"Remove {n} selected lines?",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
            if ans != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            push_peak_history()
            redraw = False
            for i in sorted(_selected_peak_indices, reverse=True):
                if 0 <= i < len(custom_peak_rows):
                    r = custom_peak_rows[i]
                    redraw = redraw or _row_draws(r)
                    peaks_rows_layout_remove(r["widget"])
                    del custom_peak_rows[i]
            # (the highlight cache is keyed by each row's peaks: no need to clear it)
            _selected_peak_indices.clear()
            _select_anchor[0] = None
            _update_selection_visuals()
            update_pick_row_combo()
            if redraw:                      # nothing drawn was removed → no redraw
                _render_peaks_or_full()
            _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
            _sync_confirmation_panel()
            return
        # Single-row delete
        redraw = _row_draws(row_data)
        custom_peak_rows.remove(row_data)
        peaks_rows_layout_remove(row_widget)
        if idx >= 0:
            _selected_peak_indices.discard(idx)
            adjusted = {i if i < idx else i - 1 for i in _selected_peak_indices}
            _selected_peak_indices.clear()
            _selected_peak_indices.update(adjusted)
            if _select_anchor[0] is not None and _select_anchor[0] >= idx:
                _select_anchor[0] = max(0, _select_anchor[0] - 1) if _select_anchor[0] > idx else None
            _update_selection_visuals()
        update_pick_row_combo()
        if redraw:                          # nothing drawn was removed → no redraw
            _render_peaks_or_full()
        _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
        _sync_confirmation_panel()

    remove_btn.clicked.connect(on_remove)
    checkbox.stateChanged.connect(lambda _: (_render_peaks_or_full(), _auto_sync_legend_entries(), _rebuild_peak_legend_on_plot()))

    def _on_checkbox_clicked(checked, _chk=checkbox):
        idx = next((i for i, r in enumerate(custom_peak_rows) if r.get("checkbox") is _chk), -1)
        if idx < 0:
            _last_clicked_peak_row[0] = -1
            return
        mods = QtWidgets.QApplication.keyboardModifiers()
        if (mods & QtCore.Qt.KeyboardModifier.ShiftModifier
                and _last_clicked_peak_row[0] >= 0
                and _last_clicked_peak_row[0] != idx):
            lo = min(_last_clicked_peak_row[0], idx)
            hi = max(_last_clicked_peak_row[0], idx)
            for i in range(lo, hi + 1):
                r = custom_peak_rows[i]
                r["checkbox"].blockSignals(True)
                r["checkbox"].setChecked(checked)
                r["checkbox"].blockSignals(False)
            _render_peaks_or_full()
            _auto_sync_legend_entries()
            _rebuild_peak_legend_on_plot()
        _last_clicked_peak_row[0] = idx

    checkbox.clicked.connect(_on_checkbox_clicked)
    envelope_btn.toggled.connect(lambda _: (
        _save_envelope_states_to_qsettings(),
        _peak_session_timer.start(),
        _render_peaks_or_full() if checkbox.isChecked() else None))
    peaks_input.editingFinished.connect(push_peak_history)
    peaks_input.textChanged.connect(lambda _: _peak_text_timer.start())
    label_input.textChanged.connect(lambda _: update_pick_row_combo())
    label_input.textChanged.connect(lambda _: _peak_text_timer.start())

    _orig_pi_focus = peaks_input.focusInEvent
    def _track_pi_focus(event, _rd=row_data):
        _last_edited_row[0] = _rd
        _orig_pi_focus(event)
    peaks_input.focusInEvent = _track_pi_focus

    _orig_li_focus = label_input.focusInEvent
    def _track_li_focus(event, _rd=row_data):
        _last_edited_row[0] = _rd
        _orig_li_focus(event)
    label_input.focusInEvent = _track_li_focus

    custom_peak_rows.append(row_data)
    peaks_rows_layout_add(row_widget)
    return row_data

def add_peak_row(checked=True, color=None, peaks_text="", label_text=""):
    _add_peak_row_base(checked=checked, color=color,
                       peaks_text=peaks_text, label_text=label_text)
    update_pick_row_combo()
    _sync_confirmation_panel()

_add_peak_row_base()

# ─────────────────────────────────────────────
#  Peaks window – toolbar
# ─────────────────────────────────────────────
# ── Peak-list file history (for prev/next navigation) ──
_peak_file_history  = []   # list of absolute paths, oldest first
_peak_file_hist_pos = [-1] # current position index, in a list for mutability

def _peak_history_load(path):
    """Load a peak list file and update file-history navigation state."""
    global _peak_file_history, _peak_file_hist_pos
    if not path or not os.path.exists(path): return
    # Remove duplicate then append
    if path in _peak_file_history:
        _peak_file_history.remove(path)
    _peak_file_history.append(path)
    _peak_file_hist_pos[0] = len(_peak_file_history) - 1
    _load_peak_file_into_rows(path)
    settings.setValue("last_peak_file", path)
    _update_peaks_win_title(path)
    _update_peak_nav_buttons()

def _load_peak_file_into_rows(path, push_history=True, progress=None):
    """progress(i, n), when given, is called as each of the n rows is added."""
    global _peak_groups
    if push_history:
        push_peak_history()
    _selected_peak_indices.clear(); _select_anchor[0] = None
    for row in custom_peak_rows[:]:
        peaks_rows_layout_remove(row["widget"]); custom_peak_rows.remove(row)
    with open(path, "r") as f: data = json.load(f)

    # New format: {"format_version": 2, "groups": [...], "rows": [...]}
    # Old format: flat list of row dicts
    if isinstance(data, dict):
        saved_groups = data.get("groups", [{"name": "Unclassified", "alpha": 255}])
        items = data.get("rows", [])
    else:
        saved_groups = [{"name": "Unclassified", "alpha": 255}]
        items = data

    _peak_groups.clear()
    _group_header_widgets.clear()
    for gd in saved_groups:
        _peak_groups.append({"name": gd["name"], "alpha": gd.get("alpha", 255), "collapsed": gd.get("collapsed", False), "highlight": gd.get("highlight", True), "L_active": gd.get("L_active", True), "1L_active": gd.get("1L_active", True), "curve_active": gd.get("curve_active", True)})

    peaks_rows_container._bulk_adding = True
    try:
        for i_item, item in enumerate(items):
            if progress is not None:
                progress(i_item, len(items))
            gname = item.get("group", "Unclassified")
            if not any(g["name"] == gname for g in _peak_groups):
                _peak_groups.append({"name": gname, "alpha": 255, "collapsed": False, "highlight": True, "L_active": True, "1L_active": True, "curve_active": True})
            if item.get("mode") == "range":
                _add_peak_row_base(
                    checked=item.get("checked", True),
                    color=QtGui.QColor(item.get("color", "#ff0000")),
                    label_text=item.get("label", ""),
                    group_name=gname,
                    range_values=(
                        item.get("range_start", 0.0),
                        item.get("range_end",   0.0),
                        item.get("range_step",  1.0),
                    ))
            else:
                _add_peak_row_base(
                    checked=item.get("checked", True),
                    color=QtGui.QColor(item.get("color", "#ff0000")),
                    peaks_text=item.get("peaks", ""),
                    label_text=item.get("label", ""),
                    group_name=gname)
            rd = custom_peak_rows[-1]
            _apply_row_legend_fields(rd, item)
            for key in ("L_state", "1L_state"):
                v = item.get(key, 0)
                if v:
                    rd[key][0] = v
                    rd["refresh_L"]() if key == "L_state" else rd["refresh_1L"]()
            if item.get("envelope_btn", False):
                rd["envelope_btn"].blockSignals(True)
                rd["envelope_btn"].setChecked(True)
                rd["envelope_btn"].blockSignals(False)
    finally:
        peaks_rows_container._bulk_adding = False
    peaks_rows_container.rebuild_group_layout()
    _save_envelope_states_to_qsettings()
    _clear_highlight_cache()
    update_pick_row_combo(); render_plot()
    _auto_sync_legend_entries()
    # Deferred: this also runs during startup, before the legend item exists.
    QtCore.QTimer.singleShot(0, lambda: _rebuild_peak_legend_on_plot())
    _sync_confirmation_panel()

def _update_peak_nav_buttons():
    pos = _peak_file_hist_pos[0]
    n   = len(_peak_file_history)
    pw_prev_file_btn.setEnabled(pos > 0)
    pw_next_file_btn.setEnabled(pos < n - 1)
    pw_prev_file_btn.setToolTip(
        _peak_file_history[pos - 1] if pos > 0 else "No previous file")
    pw_next_file_btn.setToolTip(
        _peak_file_history[pos + 1] if pos < n - 1 else "No next file")

pw_toolbar = QtWidgets.QHBoxLayout()

pw_prev_file_btn = QtWidgets.QPushButton("◀"); pw_prev_file_btn.setFixedWidth(26)
pw_prev_file_btn.setToolTip("Previous peak list file"); pw_prev_file_btn.setEnabled(False)
pw_next_file_btn = QtWidgets.QPushButton("▶"); pw_next_file_btn.setFixedWidth(26)
pw_next_file_btn.setToolTip("Next peak list file"); pw_next_file_btn.setEnabled(False)

def _pw_go_prev():
    pos = _peak_file_hist_pos[0]
    if pos > 0:
        _peak_file_hist_pos[0] = pos - 1
        _load_peak_file_into_rows(_peak_file_history[_peak_file_hist_pos[0]])
        _update_peaks_win_title(_peak_file_history[_peak_file_hist_pos[0]])
        _update_peak_nav_buttons()

def _pw_go_next():
    pos = _peak_file_hist_pos[0]
    if pos < len(_peak_file_history) - 1:
        _peak_file_hist_pos[0] = pos + 1
        _load_peak_file_into_rows(_peak_file_history[_peak_file_hist_pos[0]])
        _update_peaks_win_title(_peak_file_history[_peak_file_hist_pos[0]])
        _update_peak_nav_buttons()

pw_prev_file_btn.clicked.connect(_pw_go_prev)
pw_next_file_btn.clicked.connect(_pw_go_next)

undo_btn = QtWidgets.QPushButton("↩ Undo"); undo_btn.setFixedWidth(70)
undo_btn.setToolTip("Undo last peak text edit  (Ctrl+Z)")
undo_btn.setEnabled(False)

redo_btn = QtWidgets.QPushButton("↪ Redo"); redo_btn.setFixedWidth(70)
redo_btn.setToolTip("Redo  (Ctrl+Y / Ctrl+Shift+Z)")
redo_btn.setEnabled(False)

add_btn = QtWidgets.QPushButton("+ Add row"); add_btn.setFixedWidth(80)
add_btn.clicked.connect(lambda: add_peak_row())

def _add_row_relative(above: bool):
    push_peak_history()
    ref_row = _last_edited_row[0] if _last_edited_row[0] in custom_peak_rows else None
    if ref_row is not None:
        src_idx = custom_peak_rows.index(ref_row)
        ref_idx = src_idx if above else src_idx + 1
        grp     = ref_row.get("group_name", "Unclassified")
    elif _selected_peak_indices:
        src_idx = min(_selected_peak_indices) if above else max(_selected_peak_indices)
        ref_idx = min(_selected_peak_indices) if above else max(_selected_peak_indices) + 1
        grp     = custom_peak_rows[src_idx].get("group_name", "Unclassified")
    else:
        ref_idx = len(custom_peak_rows)
        grp     = _peak_groups[-1]["name"] if _peak_groups else "Unclassified"
    new_row = _add_peak_row_base(group_name=grp)
    new_idx = len(custom_peak_rows) - 1
    peaks_rows_container.move_rows([new_idx], ref_idx)
    QtCore.QTimer.singleShot(0, lambda: (
        new_row["peaks_input"].setFocus(),
        peaks_rows_scroll.ensureWidgetVisible(new_row["widget"])))

add_above_btn = QtWidgets.QPushButton("↑ Above"); add_above_btn.setFixedWidth(68)
add_above_btn.setToolTip("Insert a blank row above the selected row(s)")
add_above_btn.clicked.connect(lambda: _add_row_relative(above=True))

add_below_btn = QtWidgets.QPushButton("↓ Below"); add_below_btn.setFixedWidth(68)
add_below_btn.setToolTip("Insert a blank row below the selected row(s)")
add_below_btn.clicked.connect(lambda: _add_row_relative(above=False))

def _duplicate_row():
    push_peak_history()

    def _build_copy(ref_row):
        range_values = None
        if ref_row["mode_btn"].isChecked():
            range_values = (ref_row["range_start"].value(),
                            ref_row["range_end"].value(),
                            ref_row["range_step"].value())
        nr = _add_peak_row_base(
            checked      = ref_row["checkbox"].isChecked(),
            color        = QtGui.QColor(ref_row["color"][0]),
            peaks_text   = ref_row["peaks_input"].text(),
            label_text   = ref_row["label_input"].text(),
            range_values = range_values,
            group_name   = ref_row.get("group_name", "Unclassified"),
        )
        nr["L_state"][0]  = ref_row["L_state"][0]
        nr["1L_state"][0] = ref_row["1L_state"][0]
        nr["refresh_L"]()
        nr["refresh_1L"]()
        nr["envelope_btn"].setChecked(ref_row["envelope_btn"].isChecked())
        _copy_row_legend_fields(ref_row, nr)
        return nr

    # Determine sources and insertion point
    if _selected_peak_indices:
        sorted_sel   = sorted(_selected_peak_indices)
        insert_after = max(sorted_sel)
        sources      = [custom_peak_rows[i] for i in sorted_sel]
    else:
        ref = (_last_edited_row[0] if _last_edited_row[0] in custom_peak_rows
               else (custom_peak_rows[-1] if custom_peak_rows else None))
        if ref is None:
            return
        insert_after = custom_peak_rows.index(ref)
        sources      = [ref]

    # Build copies with _bulk_adding so _add_peak_row_base does NOT trigger
    # rebuild_group_layout for each row — we do exactly one rebuild at the end.
    old_n = len(custom_peak_rows)
    peaks_rows_container._bulk_adding = True
    try:
        new_rows = [_build_copy(r) for r in sources]
    finally:
        peaks_rows_container._bulk_adding = False

    # Splice new rows into custom_peak_rows right after insert_after
    from_set  = set(range(old_n, old_n + len(new_rows)))
    remaining = [r for i, r in enumerate(custom_peak_rows) if i not in from_set]
    adj       = insert_after + 1              # insertion index within remaining
    new_order = remaining[:adj] + new_rows + remaining[adj:]
    custom_peak_rows.clear()
    custom_peak_rows.extend(new_order)

    # One single rebuild + sync (no move_rows, no double rebuild)
    peaks_rows_container.rebuild_group_layout()
    _selected_peak_indices.clear()
    _selected_peak_indices.update(range(adj, adj + len(new_rows)))
    _update_selection_visuals()
    update_pick_row_combo()
    _render_peaks_or_full()
    _auto_sync_legend_entries()
    _rebuild_peak_legend_on_plot()
    _sync_confirmation_panel()

    last_new = new_rows[-1]
    QtCore.QTimer.singleShot(0, lambda: (
        last_new["peaks_input"].setFocus(),
        peaks_rows_scroll.ensureWidgetVisible(last_new["widget"])))

dup_btn = QtWidgets.QPushButton("⧉ Dup"); dup_btn.setFixedWidth(60)
dup_btn.setToolTip("Duplicate the selected row(s) and insert them below the last selected line")
dup_btn.clicked.connect(_duplicate_row)

add_group_btn = QtWidgets.QPushButton("+ Group"); add_group_btn.setFixedWidth(68)
add_group_btn.setToolTip("Add a new peak-list group")

def _add_peak_group():
    n = len(_peak_groups) + 1
    name = f"Group {n}"
    while any(g["name"] == name for g in _peak_groups):
        n += 1
        name = f"Group {n}"
    _peak_groups.append({"name": name, "alpha": 255, "collapsed": False, "highlight": True, "L_active": True, "1L_active": True, "curve_active": True})
    _rebuild_group_selectors()
    peaks_rows_container.rebuild_group_layout()

add_group_btn.clicked.connect(_add_peak_group)
peaks_help_btn = QtWidgets.QPushButton("?"); peaks_help_btn.setFixedWidth(24)

pw_select_all_btn  = QtWidgets.QPushButton("☑ All");  pw_select_all_btn.setFixedWidth(50)
pw_select_none_btn = QtWidgets.QPushButton("☐ None"); pw_select_none_btn.setFixedWidth(54)
pw_select_all_btn.setToolTip("Check all peak rows")
pw_select_none_btn.setToolTip("Uncheck all peak rows")

def _pw_select_all():
    for row in custom_peak_rows:
        row["checkbox"].setChecked(True)

def _pw_select_none():
    for row in custom_peak_rows:
        row["checkbox"].setChecked(False)

pw_select_all_btn.clicked.connect(_pw_select_all)
pw_select_none_btn.clicked.connect(_pw_select_none)

pw_toolbar.addWidget(pw_prev_file_btn); pw_toolbar.addWidget(pw_next_file_btn)
pw_toolbar.addSpacing(6)
pw_toolbar.addWidget(undo_btn); pw_toolbar.addWidget(redo_btn)
pw_toolbar.addSpacing(8); pw_toolbar.addWidget(add_btn); pw_toolbar.addWidget(add_above_btn); pw_toolbar.addWidget(add_below_btn); pw_toolbar.addWidget(dup_btn); pw_toolbar.addWidget(add_group_btn)
pw_toolbar.addSpacing(6)
pw_toolbar.addWidget(pw_select_all_btn); pw_toolbar.addWidget(pw_select_none_btn)
pw_toolbar.addSpacing(8)

pw_solo_edit_btn = QtWidgets.QPushButton("Show edited only")
pw_solo_edit_btn.setCheckable(True)
pw_solo_edit_btn.setChecked(False)
pw_solo_edit_btn.setFixedHeight(24)
pw_solo_edit_btn.setToolTip(
    "When active: clicking a peak row's text field temporarily hides all\n"
    "other peak lists and their envelopes on the plot.\n"
    "Clicking elsewhere restores the normal view.\n"
    "Does not affect peak row checkboxes.")

def _on_solo_edit_toggled(checked):
    _solo_edit_mode[0] = checked
    if not checked:
        _solo_edit_row[0] = None
    _render_peaks_or_full()

pw_solo_edit_btn.toggled.connect(_on_solo_edit_toggled)
pw_toolbar.addWidget(pw_solo_edit_btn)

def _on_global_focus_changed(_old, new):
    if not _solo_edit_mode[0]:
        return
    for row in custom_peak_rows:
        if new is row["peaks_input"] or new is row["label_input"]:
            if _solo_edit_row[0] is not row:
                _solo_edit_row[0] = row
                _render_peaks_or_full()
            return
    if _solo_edit_row[0] is not None:
        _solo_edit_row[0] = None
        _render_peaks_or_full()

app.focusChanged.connect(_on_global_focus_changed)

pw_export_csv_btn = QtWidgets.QPushButton("Export CSV")
pw_export_csv_btn.setFixedHeight(24)
pw_export_csv_btn.setToolTip("Export highlighted peak data as CSV")
pw_export_csv_btn.clicked.connect(lambda: export_peaks_csv())
pw_toolbar.addStretch()
pw_toolbar.addWidget(pw_export_csv_btn)
pw_toolbar.addWidget(peaks_help_btn)
peaks_layout.addLayout(pw_toolbar)

# ─────────────────────────────────────────────
#  Row copy / paste / aggregate  (toolbar 2)
# ─────────────────────────────────────────────

def _copy_selected_rows():
    if not _selected_peak_indices:
        return
    _peak_row_clipboard.clear()
    for i in sorted(_selected_peak_indices):
        if 0 <= i < len(custom_peak_rows):
            r = custom_peak_rows[i]
            _peak_row_clipboard.append({
                "checked":    r["checkbox"].isChecked(),
                "color":      r["color"][0].name(),
                "label":      r["label_input"].text(),
                "peaks":      r["peaks_input"].text(),
                "group_name": r.get("group_name", "Unclassified"),
                "L_state":    r.get("L_state", [0])[0],
                "1L_state":   r.get("1L_state", [0])[0],
                "envelope":   r["envelope_btn"].isChecked(),
                **_row_legend_fields(r),
            })
    pw_paste_btn.setEnabled(bool(_peak_row_clipboard))


def _paste_rows():
    if not _peak_row_clipboard:
        return
    push_peak_history()
    # Priority: currently focused editing row → last selected row → end
    if _last_edited_row[0] is not None and _last_edited_row[0] in custom_peak_rows:
        insert_after = custom_peak_rows.index(_last_edited_row[0])
    elif _selected_peak_indices:
        insert_after = max(_selected_peak_indices)
    else:
        insert_after = len(custom_peak_rows) - 1
    old_n = len(custom_peak_rows)
    peaks_rows_container._bulk_adding = True
    try:
        new_rows = []
        for d in _peak_row_clipboard:
            nr = _add_peak_row_base(
                checked    = d["checked"],
                color      = QtGui.QColor(d["color"]),
                peaks_text = d["peaks"],
                label_text = d["label"],
                group_name = d["group_name"],
            )
            nr["L_state"][0]  = d["L_state"]
            nr["1L_state"][0] = d["1L_state"]
            nr["refresh_L"]()
            nr["refresh_1L"]()
            nr["envelope_btn"].setChecked(d["envelope"])
            _apply_row_legend_fields(nr, d)
            new_rows.append(nr)
    finally:
        peaks_rows_container._bulk_adding = False
    from_set  = set(range(old_n, old_n + len(new_rows)))
    remaining = [r for i, r in enumerate(custom_peak_rows) if i not in from_set]
    adj       = insert_after + 1
    new_order = remaining[:adj] + new_rows + remaining[adj:]
    custom_peak_rows.clear()
    custom_peak_rows.extend(new_order)
    peaks_rows_container.rebuild_group_layout()
    _selected_peak_indices.clear()
    _selected_peak_indices.update(range(adj, adj + len(new_rows)))
    _update_selection_visuals()
    update_pick_row_combo()
    _render_peaks_or_full(); _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
    _sync_confirmation_panel()


def _aggregate_selected_rows():
    """Merge selected rows into one: all m/z values combined, deduplicated, sorted."""
    if len(_selected_peak_indices) < 2:
        return
    indices = sorted(_selected_peak_indices)
    all_mz = []
    for i in indices:
        if 0 <= i < len(custom_peak_rows):
            all_mz.extend(parse_peaks_text(custom_peak_rows[i]["peaks_input"].text()))
    if not all_mz:
        return
    seen, deduped = set(), []
    for v in sorted(all_mz):
        key = round(v, 4)
        if key not in seen:
            seen.add(key); deduped.append(v)
    def _fmt(v):
        return f"{v:.4f}".rstrip("0").rstrip(".")
    peaks_text = ", ".join(_fmt(v) for v in deduped)
    first  = custom_peak_rows[indices[0]]
    label  = first["label_input"].text()
    color  = QtGui.QColor(first["color"][0])
    group  = first.get("group_name", "Unclassified")
    push_peak_history()
    insert_after = indices[-1]
    old_n = len(custom_peak_rows)
    peaks_rows_container._bulk_adding = True
    try:
        nr = _add_peak_row_base(checked=True, color=color,
                                peaks_text=peaks_text, label_text=label,
                                group_name=group)
        _copy_row_legend_fields(first, nr)
    finally:
        peaks_rows_container._bulk_adding = False
    remaining = list(custom_peak_rows[:old_n])
    adj = insert_after + 1
    custom_peak_rows.clear()
    custom_peak_rows.extend(remaining[:adj] + [nr] + remaining[adj:])
    peaks_rows_container.rebuild_group_layout()
    _selected_peak_indices.clear()
    _selected_peak_indices.add(adj)
    _update_selection_visuals()
    _clear_highlight_cache()
    update_pick_row_combo()
    _render_peaks_or_full(); _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
    _sync_confirmation_panel()


# ─────────────────────────────────────────────
#  Peak List Overlap Check window
# ─────────────────────────────────────────────

class PeakListCheckWindow(QtWidgets.QWidget):
    """Non-modal popup showing selected peak rows that share overlapping m/z values.

    Controls (checkbox, envelope, L/1L) are bidirectionally synced with the
    main Peaks window: toggling in either window updates the other.
    """

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Peak List Overlap Check")
        self.resize(720, 480)
        self._connections = []   # (signal, slot) tuples — disconnected on close
        self._build_ui()
        self.run_analysis()

    # ── UI construction ────────────────────────────────────────────────────────

    def _build_ui(self):
        vlay = QtWidgets.QVBoxLayout(self)
        vlay.setContentsMargins(6, 6, 6, 6)
        vlay.setSpacing(4)

        # Toolbar
        tbar = QtWidgets.QHBoxLayout()
        self._info_lbl = QtWidgets.QLabel("")
        tbar.addWidget(self._info_lbl)
        tbar.addStretch()

        tol_lbl = QtWidgets.QLabel("Tolerance:")
        tol_lbl.setStyleSheet("color:gray;")
        self._tol_spin = QtWidgets.QDoubleSpinBox()
        self._tol_spin.setRange(0.001, 100.0)
        self._tol_spin.setValue(0.5)
        self._tol_spin.setDecimals(3)
        self._tol_spin.setSuffix(" Da")
        self._tol_spin.setFixedWidth(92)
        self._tol_spin.setToolTip("m/z tolerance for overlap detection")

        refresh_btn = QtWidgets.QPushButton("↺ Refresh")
        refresh_btn.setFixedHeight(24)
        refresh_btn.setToolTip(
            "Re-run the check using the currently selected peak list as reference.\n"
            "Select a row in the Peaks window first, then click here.")
        refresh_btn.clicked.connect(self.run_analysis)

        tbar.addWidget(tol_lbl)
        tbar.addWidget(self._tol_spin)
        tbar.addSpacing(4)
        tbar.addWidget(refresh_btn)
        vlay.addLayout(tbar)

        # Scroll area containing groups + mirror rows
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._container = QtWidgets.QWidget()
        self._cont_lay = QtWidgets.QVBoxLayout(self._container)
        self._cont_lay.setContentsMargins(0, 0, 0, 0)
        self._cont_lay.setSpacing(2)
        self._cont_lay.addStretch()   # pushed to the bottom; rows inserted above it
        self._scroll.setWidget(self._container)
        vlay.addWidget(self._scroll)

    # ── Overlap analysis ────────────────────────────────────────────────────────

    def run_analysis(self):
        """Find all peak rows that share ≥1 m/z value with the selected reference row."""
        self._disconnect_all()
        self._clear_rows()

        tol      = self._tol_spin.value()
        selected = sorted(_selected_peak_indices)

        if not selected:
            self._info_lbl.setText("Select 1 peak list as reference, then click Refresh.")
            return

        ref_idx = selected[0]
        if ref_idx >= len(custom_peak_rows):
            self._info_lbl.setText("Selected row no longer exists.")
            return

        ref_row = custom_peak_rows[ref_idx]
        ref_mz  = parse_peaks_text(ref_row["peaks_input"].text())
        ref_lbl = ref_row["label_input"].text().strip() or f"row {ref_idx + 1}"

        if not ref_mz:
            self._info_lbl.setText(f'"{ref_lbl}" has no m/z values to match against.')
            return

        # Check every OTHER row for at least one matching value
        match_rows = []
        for i, row in enumerate(custom_peak_rows):
            if i == ref_idx:
                continue
            other_mz = parse_peaks_text(row["peaks_input"].text())
            if any(abs(a - b) <= tol for a in ref_mz for b in other_mz):
                match_rows.append(row)

        n = len(match_rows)
        self._info_lbl.setText(
            f'{n} peak list{"s" if n != 1 else ""} share values with '
            f'"{ref_lbl}"  (tol ≤ {tol:.3g} Da)')

        if not match_rows:
            lbl = QtWidgets.QLabel(
                f'No other peak lists share values with "{ref_lbl}".')
            lbl.setStyleSheet("color:gray; padding:8px;")
            self._cont_lay.insertWidget(0, lbl)
            return

        # Collect group names, preserving global _peak_groups order
        grp_rows = {}
        for r in match_rows:
            gn = r.get("group_name", "Unclassified")
            grp_rows.setdefault(gn, []).append(r)

        pos = 0
        for gd in _peak_groups:
            if gd["name"] not in grp_rows:
                continue
            hdr = self._make_group_header(gd)
            self._cont_lay.insertWidget(pos, hdr); pos += 1
            for row in grp_rows[gd["name"]]:
                rw = self._make_mirror_row(row, ref_mz=ref_mz, tol=tol)
                self._cont_lay.insertWidget(pos, rw); pos += 1

    # ── Widget builders ─────────────────────────────────────────────────────────

    def _make_group_header(self, gd):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        cb = QtWidgets.QPushButton("▼")
        cb.setFixedSize(18, 18)
        cb.setCheckable(True)
        cb.setStyleSheet("font-size:9px; border:none; color:gray;")
        cb.setToolTip("Collapse / expand this group in this view")

        name_lbl = QtWidgets.QLabel(gd["name"])
        name_lbl.setStyleSheet("font-weight:bold; font-size:11px;")

        w.setAutoFillBackground(True)
        pal = w.palette()
        pal.setColor(w.backgroundRole(), QtGui.QColor("#2a3a4a"))
        w.setPalette(pal)

        lay.addWidget(cb)
        lay.addWidget(name_lbl)
        lay.addStretch()
        return w

    def _make_mirror_row(self, row, ref_mz=None, tol=0.5):
        """Build a read-mostly mirror row that stays in bidirectional sync with *row*."""
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        # ── Checkbox ──────────────────────────────────────────────────────────
        cb = QtWidgets.QCheckBox()
        cb.setChecked(row["checkbox"].isChecked())

        def _popup_cb_toggled(checked, _row=row):
            if _row["checkbox"].isChecked() != checked:
                _row["checkbox"].setChecked(checked)
                # stateChanged on the main checkbox fires render + legend sync

        def _main_cb_changed(state, _cb=cb):
            new = bool(state)
            if _cb.isChecked() != new:
                _cb.blockSignals(True)
                _cb.setChecked(new)
                _cb.blockSignals(False)

        cb.toggled.connect(_popup_cb_toggled)
        row["checkbox"].stateChanged.connect(_main_cb_changed)
        self._connections.append((row["checkbox"].stateChanged, _main_cb_changed))

        # ── Color swatch (display only) ───────────────────────────────────────
        color_btn = QtWidgets.QPushButton()
        color_btn.setFixedSize(22, 22)
        color_btn.setStyleSheet(
            f"background-color:{row['color'][0].name()}; border:1px solid gray;")
        color_btn.setEnabled(False)

        # ── Peaks text (read-only, stays in sync) ─────────────────────────────
        peaks_lbl = QtWidgets.QLineEdit(row["peaks_input"].text())
        peaks_lbl.setReadOnly(True)
        peaks_lbl.setMinimumWidth(80)
        peaks_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                QtWidgets.QSizePolicy.Policy.Fixed)

        def _sync_peaks(txt, _lbl=peaks_lbl):
            _lbl.setText(txt)

        row["peaks_input"].textChanged.connect(_sync_peaks)
        self._connections.append((row["peaks_input"].textChanged, _sync_peaks))

        # ── Label text (read-only, stays in sync) ─────────────────────────────
        label_lbl = QtWidgets.QLineEdit(row["label_input"].text())
        label_lbl.setReadOnly(True)
        label_lbl.setMinimumWidth(60)
        label_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                QtWidgets.QSizePolicy.Policy.Fixed)

        def _sync_label(txt, _lbl=label_lbl):
            _lbl.setText(txt)

        row["label_input"].textChanged.connect(_sync_label)
        self._connections.append((row["label_input"].textChanged, _sync_label))

        # ── L button (cycle, synced bidirectionally) ──────────────────────────
        _L_STYLES = {
            0: ("L",   "border:1px solid gray; color:gray;"),
            1: ("m",   "border:1px solid gray; background:#2d6e3e; color:white;"),
            2: ("L",   "border:1px solid gray; background:#2d4a8a; color:white;"),
            3: ("mL",  "border:1px solid gray; background:#8a5c2d; color:white;"),
        }
        L_btn_p = QtWidgets.QPushButton()
        L_btn_p.setFixedSize(28, 22)
        t, s = _L_STYLES[row["L_state"][0]]
        L_btn_p.setText(t); L_btn_p.setStyleSheet(s)
        L_btn_p.setToolTip("Cycle: off → mass → label → mass+label")

        def _popup_L_clicked(_row=row, _btn=L_btn_p, _styles=_L_STYLES):
            # Delegate to the main L button so all synced windows update too
            _row["L_btn"].click()
            t, s = _styles[_row["L_state"][0]]
            _btn.setText(t); _btn.setStyleSheet(s)

        def _sync_L_from_main(_row=row, _btn=L_btn_p, _styles=_L_STYLES):
            t, s = _styles[_row["L_state"][0]]
            _btn.setText(t); _btn.setStyleSheet(s)

        L_btn_p.clicked.connect(_popup_L_clicked)
        row["L_btn"].clicked.connect(_sync_L_from_main)
        self._connections.append((row["L_btn"].clicked, _sync_L_from_main))

        # ── 1L button (cycle, synced bidirectionally) ─────────────────────────
        _1L_STYLES = {
            0: ("1L",  "border:1px solid gray; color:gray;"),
            1: ("1m",  "border:1px solid gray; background:#2d6e3e; color:white;"),
            2: ("1L",  "border:1px solid gray; background:#2d4a8a; color:white;"),
            3: ("1mL", "border:1px solid gray; background:#8a5c2d; color:white;"),
        }
        lL_btn_p = QtWidgets.QPushButton()
        lL_btn_p.setFixedSize(32, 22)
        t, s = _1L_STYLES[row["1L_state"][0]]
        lL_btn_p.setText(t); lL_btn_p.setStyleSheet(s)
        lL_btn_p.setToolTip("Cycle: off → mass → label → mass+label (first peak only)")

        def _popup_1L_clicked(_row=row, _btn=lL_btn_p, _styles=_1L_STYLES):
            _row["lL_btn"].click()
            t, s = _styles[_row["1L_state"][0]]
            _btn.setText(t); _btn.setStyleSheet(s)

        def _sync_1L_from_main(_row=row, _btn=lL_btn_p, _styles=_1L_STYLES):
            t, s = _styles[_row["1L_state"][0]]
            _btn.setText(t); _btn.setStyleSheet(s)

        lL_btn_p.clicked.connect(_popup_1L_clicked)
        row["lL_btn"].clicked.connect(_sync_1L_from_main)
        self._connections.append((row["lL_btn"].clicked, _sync_1L_from_main))

        # ── Envelope button (synced bidirectionally) ──────────────────────────
        env_btn = QtWidgets.QPushButton("⌒")
        env_btn.setFixedSize(22, 22)
        env_btn.setCheckable(True)
        env_btn.setChecked(row["envelope_btn"].isChecked())
        env_btn.setToolTip("Toggle isotopic envelope curve")

        def _popup_env_toggled(checked, _row=row):
            if _row["envelope_btn"].isChecked() != checked:
                _row["envelope_btn"].setChecked(checked)
                # main envelope_btn.toggled fires save + render

        def _main_env_toggled(checked, _env=env_btn):
            if _env.isChecked() != checked:
                _env.blockSignals(True)
                _env.setChecked(checked)
                _env.blockSignals(False)

        env_btn.toggled.connect(_popup_env_toggled)
        row["envelope_btn"].toggled.connect(_main_env_toggled)
        self._connections.append((row["envelope_btn"].toggled, _main_env_toggled))

        # ── Zoom button ───────────────────────────────────────────────────────
        zoom_btn = QtWidgets.QPushButton("⊙")
        zoom_btn.setFixedSize(22, 22)
        zoom_btn.setToolTip("Zoom the plot to fit this peak list")

        def _zoom(_ch=False, _row=row):
            peaks = parse_peaks_text(_row["peaks_input"].text())
            if not peaks:
                return
            lo, hi = min(peaks), max(peaks)
            pad = max((hi - lo) * 0.15, 5.0)
            if _stacked_mode and _stacked_sub_plots:
                _stacked_sub_plots[0].vb.setXRange(lo - pad, hi + pad, padding=0)
            else:
                plot.vb.setXRange(lo - pad, hi + pad, padding=0)

        zoom_btn.clicked.connect(_zoom)

        lay.addWidget(cb)
        lay.addWidget(color_btn)
        lay.addWidget(peaks_lbl)
        lay.addWidget(label_lbl)
        lay.addWidget(L_btn_p)
        lay.addWidget(lL_btn_p)
        lay.addWidget(env_btn)
        lay.addWidget(zoom_btn)

        # ── Matching-values bar ───────────────────────────────────────────────
        def _fmt_v(v):
            return f"{v:.4f}".rstrip("0").rstrip(".")

        def _compute_matches(text, _ref=ref_mz or [], _tol=tol):
            other = parse_peaks_text(text)
            return sorted({b for b in other for a in _ref if abs(a - b) <= _tol})

        matches_lbl = QtWidgets.QLabel()
        matches_lbl.setStyleSheet(
            "color:#d08000; font-size:9px; padding-left:28px; padding-bottom:2px;")

        def _refresh_matches_lbl(text=None, _lbl=matches_lbl):
            txt = text if text is not None else row["peaks_input"].text()
            hits = _compute_matches(txt)
            if hits:
                _lbl.setText("Matches: " + ",  ".join(_fmt_v(v) for v in hits))
                _lbl.setVisible(True)
            else:
                _lbl.setVisible(False)

        _refresh_matches_lbl()
        row["peaks_input"].textChanged.connect(_refresh_matches_lbl)
        self._connections.append((row["peaks_input"].textChanged, _refresh_matches_lbl))

        # Wrap row + matches label in a single outer widget
        outer = QtWidgets.QWidget()
        outer_lay = QtWidgets.QVBoxLayout(outer)
        outer_lay.setContentsMargins(0, 0, 0, 0)
        outer_lay.setSpacing(0)
        outer_lay.addWidget(w)
        outer_lay.addWidget(matches_lbl)
        return outer

    # ── Cleanup ─────────────────────────────────────────────────────────────────

    def _disconnect_all(self):
        for signal, slot in self._connections:
            try:
                signal.disconnect(slot)
            except Exception:
                pass
        self._connections.clear()

    def _clear_rows(self):
        while self._cont_lay.count() > 1:   # keep the trailing stretch
            item = self._cont_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def closeEvent(self, event):
        self._disconnect_all()
        super().closeEvent(event)


_peak_check_win = [None]


def _open_peak_list_check():
    if _peak_check_win[0] is None:
        _peak_check_win[0] = PeakListCheckWindow()
    else:
        _peak_check_win[0].run_analysis()
    _peak_check_win[0].show()
    _peak_check_win[0].raise_()


pw_copy_btn      = QtWidgets.QPushButton("⎘ Copy rows");  pw_copy_btn.setFixedHeight(24)
pw_paste_btn     = QtWidgets.QPushButton("⎘ Paste rows"); pw_paste_btn.setFixedHeight(24); pw_paste_btn.setEnabled(False)
pw_aggregate_btn = QtWidgets.QPushButton("⊕ Aggregate");  pw_aggregate_btn.setFixedHeight(24)
pw_peak_check_btn = QtWidgets.QPushButton("⊛ Peak list check"); pw_peak_check_btn.setFixedHeight(24)
pw_copy_btn.setToolTip("Copy selected peak rows to the internal clipboard")
pw_paste_btn.setToolTip("Paste copied rows after the last selected row")
pw_aggregate_btn.setToolTip(
    "Create a new row below the last selected row with all their m/z values\n"
    "combined, deduplicated and sorted. Original rows are kept.\n"
    "Uses the label, color and group of the first selected row.")
pw_peak_check_btn.setToolTip(
    "Select 1 peak list as reference, then click to find all other\n"
    "peak lists that share at least one m/z value with it.\n"
    "Controls in the popup are synced with this window.")
pw_copy_btn.clicked.connect(_copy_selected_rows)
pw_paste_btn.clicked.connect(_paste_rows)
pw_aggregate_btn.clicked.connect(_aggregate_selected_rows)
pw_peak_check_btn.clicked.connect(_open_peak_list_check)

pw_toolbar2 = QtWidgets.QHBoxLayout()
pw_toolbar2.addWidget(pw_copy_btn)
pw_toolbar2.addWidget(pw_paste_btn)
pw_toolbar2.addSpacing(8)
pw_toolbar2.addWidget(pw_aggregate_btn)
pw_toolbar2.addSpacing(8)
pw_toolbar2.addWidget(pw_peak_check_btn)
pw_toolbar2.addStretch()
peaks_layout.addLayout(pw_toolbar2)

# "Pick into:" row inside peaks window - mirrors the one in main tools bar
pw_pick_into_widget = QtWidgets.QWidget()
pw_pick_into_layout = QtWidgets.QHBoxLayout(pw_pick_into_widget)
pw_pick_into_layout.setContentsMargins(0, 0, 0, 0)
pw_pick_into_layout.setSpacing(4)
pw_pick_into_layout.addWidget(QtWidgets.QLabel("Pick into:"))
pw_pick_row_label = QtWidgets.QLabel("-")
pw_pick_row_label.setStyleSheet("font-style: italic; color: gray;")
pw_pick_into_layout.addWidget(pw_pick_row_label)
pw_pick_into_layout.addStretch()
pw_pick_into_widget.setVisible(False)
peaks_layout.addWidget(pw_pick_into_widget)

# ── Label/mass toggles + font/angle ──────────────────────────────────────
pk_toggle_row1 = QtWidgets.QHBoxLayout()
pk_toggle_row1.addWidget(peak_labels_toggle)
pk_toggle_row1.addWidget(peak_masses_toggle)
pk_toggle_row1.addSpacing(10)

_lbl_font_lbl = QtWidgets.QLabel("Font:")
_lbl_font_lbl.setStyleSheet("color: gray;")
pk_toggle_row1.addWidget(_lbl_font_lbl)
pk_toggle_row1.addWidget(label_font_spin)
pk_toggle_row1.addSpacing(6)

_lbl_angle_lbl = QtWidgets.QLabel("Angle:")
_lbl_angle_lbl.setStyleSheet("color: gray;")
pk_toggle_row1.addWidget(_lbl_angle_lbl)
pk_toggle_row1.addWidget(label_angle_spin)
pk_toggle_row1.addSpacing(6)

_lbl_alt_lbl = QtWidgets.QLabel("Height:")
_lbl_alt_lbl.setStyleSheet("color: gray;")
_lbl_alt_lbl.setToolTip("Extra vertical offset above peak tip for labels.")
pk_toggle_row1.addWidget(_lbl_alt_lbl)
pk_toggle_row1.addWidget(label_altitude_spin)
pk_toggle_row1.addStretch()

reset_L_btn = QtWidgets.QPushButton("Reset L/1L")
reset_L_btn.setFixedHeight(22)
reset_L_btn.setToolTip("Set all L and 1L buttons back to off (state 0) for every peak row.")

def _reset_all_L_states():
    for row in custom_peak_rows:
        for key, refresh_key in (("L_state", "refresh_L"), ("1L_state", "refresh_1L")):
            if row.get(key) is not None:
                row[key][0] = 0
                row[refresh_key]()
    render_plot()

reset_L_btn.clicked.connect(_reset_all_L_states)
pk_toggle_row1.addWidget(reset_L_btn)
peaks_layout.addLayout(pk_toggle_row1)

line = QtWidgets.QFrame()
line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
line.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)

peaks_layout.addWidget(line)

pk_toggle_row2 = QtWidgets.QHBoxLayout()
pk_toggle_row2.addWidget(auto_peaks_toggle)
pk_toggle_row2.addSpacing(8)
pk_toggle_row2.addWidget(mass_threshold_label)
pk_toggle_row2.addWidget(mass_threshold_spin)
pk_toggle_row2.addWidget(threshold_mode_combo)


#toggle_row2.addStretch()
peaks_layout.addLayout(pk_toggle_row2)

pk_toggle_row3 = QtWidgets.QHBoxLayout()
pk_toggle_row3.addWidget(peak_masses_all_toggle)
pk_toggle_row3.addStretch()
peaks_layout.addLayout(pk_toggle_row3)

line2 = QtWidgets.QFrame()
line2.setFrameShape(QtWidgets.QFrame.Shape.HLine)
line2.setFrameShadow(QtWidgets.QFrame.Shadow.Sunken)
peaks_layout.addWidget(line2)

pk_toggle_row4 = QtWidgets.QHBoxLayout()
pk_toggle_row4.addWidget(show_integers_toggle)
pk_toggle_row4.addStretch()
peaks_layout.addLayout(pk_toggle_row4)

# ─────────────────────────────────────────────
#  Peak list search bar  (Ctrl+F)
# ─────────────────────────────────────────────
pw_search_menu = peaks_win_menu.addMenu("Search")

_search_matches = []   # [(row_idx, field_name), ...]  — all current hits
_search_cur_idx = [-1] # position in _search_matches

splash_step(67)
# ── Widget ────────────────────────────────────────────────────────────────────
pw_search_bar = QtWidgets.QFrame()
pw_search_bar.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
_sb_lay = QtWidgets.QHBoxLayout(pw_search_bar)
_sb_lay.setContentsMargins(4, 2, 4, 2)
_sb_lay.setSpacing(4)

_sb_close_btn = QtWidgets.QPushButton("×")
_sb_close_btn.setFixedSize(20, 20); _sb_close_btn.setFlat(True)
_sb_close_btn.setToolTip("Close search  (Esc)")

_sb_field = QtWidgets.QLineEdit()
_sb_field.setPlaceholderText("Search m/z values or labels…")
_sb_field.setMinimumWidth(150); _sb_field.setMaximumWidth(300)

_sb_prev_btn = QtWidgets.QPushButton("▲")
_sb_prev_btn.setFixedSize(24, 22)
_sb_prev_btn.setToolTip("Previous match  (Shift+Enter / Up)")

_sb_next_btn = QtWidgets.QPushButton("▼")
_sb_next_btn.setFixedSize(24, 22)
_sb_next_btn.setToolTip("Next match  (Enter / Down)")

_sb_hl_btn = QtWidgets.QPushButton("Highlight all")
_sb_hl_btn.setCheckable(True); _sb_hl_btn.setFixedHeight(22)
_sb_hl_btn.setToolTip("Colour the background of every matching row")

_sb_count_lbl = QtWidgets.QLabel("")
_sb_count_lbl.setMinimumWidth(78)
_sb_count_lbl.setStyleSheet("color: gray;")

_sb_lay.addWidget(_sb_close_btn)
_sb_lay.addWidget(QtWidgets.QLabel("Find:"))
_sb_lay.addWidget(_sb_field)
_sb_lay.addWidget(_sb_prev_btn)
_sb_lay.addWidget(_sb_next_btn)
_sb_lay.addWidget(_sb_hl_btn)
_sb_lay.addWidget(_sb_count_lbl)
_sb_lay.addStretch()

pw_search_bar.setVisible(False)
peaks_layout.addWidget(pw_search_bar)

# ── Highlight colours ─────────────────────────────────────────────────────────
_SB_HL_ALL = "background-color: rgba(255, 210,  50, 100);"  # dim yellow – all matches
_SB_HL_CUR = "background-color: rgba(255, 130,   0, 200);"  # bright orange – current

# ── Logic ─────────────────────────────────────────────────────────────────────
def _sb_clear_row_styles():
    for row in custom_peak_rows:
        row["widget"].setStyleSheet("")

def _sb_collect():
    """Rebuild _search_matches from the current query."""
    _search_matches.clear()
    q = _sb_field.text().strip().lower()
    if not q:
        return
    for i, row in enumerate(custom_peak_rows):
        if q in row["peaks_input"].text().lower():
            _search_matches.append((i, "peaks_input"))
        if q in row["label_input"].text().lower():
            _search_matches.append((i, "label_input"))

def _sb_update_count():
    n   = len(_search_matches)
    cur = _search_cur_idx[0]
    if not _sb_field.text().strip():
        _sb_count_lbl.setText(""); _sb_count_lbl.setStyleSheet("color: gray;")
    elif n == 0:
        _sb_count_lbl.setText("No matches"); _sb_count_lbl.setStyleSheet("color: red;")
    else:
        _sb_count_lbl.setText(f"{cur + 1} / {n} match{'es' if n > 1 else ''}")
        _sb_count_lbl.setStyleSheet("color: gray;")

def _sb_apply_highlights():
    """Repaint all row backgrounds according to match state."""
    _sb_clear_row_styles()
    if not _search_matches:
        return
    if _sb_hl_btn.isChecked():
        for i, _ in _search_matches:
            if i < len(custom_peak_rows):
                custom_peak_rows[i]["widget"].setStyleSheet(_SB_HL_ALL)
    cur = _search_cur_idx[0]
    if 0 <= cur < len(_search_matches):
        row_idx, _ = _search_matches[cur]
        if row_idx < len(custom_peak_rows):
            custom_peak_rows[row_idx]["widget"].setStyleSheet(_SB_HL_CUR)

def _sb_jump(idx):
    """Navigate to match at index idx (wraps)."""
    n = len(_search_matches)
    if n == 0:
        _search_cur_idx[0] = -1; _sb_update_count(); return
    _search_cur_idx[0] = idx % n
    row_idx, field_name = _search_matches[_search_cur_idx[0]]
    if row_idx >= len(custom_peak_rows):
        return
    row   = custom_peak_rows[row_idx]
    field = row[field_name]
    peaks_rows_scroll.ensureWidgetVisible(row["widget"])
    q   = _sb_field.text().strip()
    pos = field.text().lower().find(q.lower())
    field.setFocus()
    if pos >= 0:
        field.setSelection(pos, len(q))
    _sb_apply_highlights()
    _sb_update_count()

def _sb_refresh():
    """Recompute matches, try to keep position, repaint."""
    old = _search_cur_idx[0]
    _sb_collect()
    n   = len(_search_matches)
    _search_cur_idx[0] = min(old, n - 1) if n > 0 else -1
    _sb_apply_highlights()
    _sb_update_count()

def _sb_next():
    if _search_matches: _sb_jump(_search_cur_idx[0] + 1)

def _sb_prev():
    if _search_matches: _sb_jump(_search_cur_idx[0] - 1)

def _sb_close():
    pw_search_bar.setVisible(False)
    _search_matches.clear(); _search_cur_idx[0] = -1
    _sb_field.clear(); _sb_hl_btn.setChecked(False)
    _sb_clear_row_styles(); _sb_count_lbl.setText("")

def _sb_open():
    # Capture selected text from the focused input BEFORE moving focus away
    _pre = ""
    _fw  = QtWidgets.QApplication.focusWidget()
    if (isinstance(_fw, QtWidgets.QLineEdit)
            and _fw is not _sb_field
            and _fw.hasSelectedText()):
        _pre = _fw.selectedText()
    pw_search_bar.setVisible(True)
    if _pre:
        _sb_field.blockSignals(True)
        _sb_field.setText(_pre)
        _sb_field.blockSignals(False)
    _sb_field.setFocus()
    _sb_field.selectAll()
    _sb_refresh()

_sb_field.textChanged.connect(lambda _: _sb_refresh())
_sb_next_btn.clicked.connect(_sb_next)
_sb_prev_btn.clicked.connect(_sb_prev)
_sb_close_btn.clicked.connect(_sb_close)
_sb_hl_btn.toggled.connect(lambda _: _sb_apply_highlights())

# Key filter: Enter / Shift+Enter / Up / Down / Esc inside the search field
class _SbKeyFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        if obj is not _sb_field or event.type() != QtCore.QEvent.Type.KeyPress:
            return False
        key  = event.key()
        mods = event.modifiers()
        if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
            (_sb_prev if mods & QtCore.Qt.KeyboardModifier.ShiftModifier else _sb_next)()
            return True
        if key == QtCore.Qt.Key.Key_Up:   _sb_prev(); return True
        if key == QtCore.Qt.Key.Key_Down: _sb_next(); return True
        if key == QtCore.Qt.Key.Key_Escape: _sb_close(); return True
        return False

_sb_key_filter = _SbKeyFilter(peaks_win)
_sb_field.installEventFilter(_sb_key_filter)

# ── Menu + shortcut ───────────────────────────────────────────────────────────
_pw_find_action = QtWidgets.QAction("Find…  Ctrl+F", peaks_win)
_pw_find_action.triggered.connect(_sb_open)
pw_search_menu.addAction(_pw_find_action)

_pw_search_sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+F"), peaks_win)
_pw_search_sc.activated.connect(_sb_open)

def show_peaks_help():
    QtWidgets.QMessageBox.information(peaks_win, "Peaks window help",
        "Each row defines a set of m/z peaks to highlight.\n\n"
        "  ⠿  drag handle - grab and drag rows up/down to reorder\n"
        "  ☐  enable/disable the row\n"
        "  ■  pick a highlight colour\n"
        "  peaks field: comma-separated m/z values\n"
        "  label field: text shown on hover\n"
        "  ×  remove the row\n\n"
        "LABEL OVERLAYS\n"
        "  Show Labels: draw tilted label text above each highlighted peak.\n"
        "  Show Masses: draw m/z values above peaks ≥ threshold.\n"
        "  Auto-detect: mark peaks above threshold with red triangles.\n"
        "  Min intensity %: threshold for the above two features.\n\n"
        "PICKING\n"
        "  Pick Peaks mode (Peaks menu or P key):\n"
        "  Single-click → snap-add nearest m/z to the selected row.\n"
        "  Double-click → remove the nearest m/z from the selected row.\n\n"
        "UNDO / REDO (text edits only - row add/remove is not undoable)\n"
        "  Ctrl+Z: undo    Ctrl+Y / Ctrl+Shift+Z: redo\n\n"
        "OVERLAY OPACITY\n"
        "  Ctrl+Scroll or Ctrl+↑↓ nudges the overlay opacity slider by 5 %.")

peaks_help_btn.clicked.connect(show_peaks_help)

# ─────────────────────────────────────────────
#  Peak list confirmation panel (right side of peaks window splitter)
# ─────────────────────────────────────────────
from droplet_pkg.ui.windows.peak_confirmation import ConfirmationPanel as _ConfPanelCls

peaks_confirmation_panel = _ConfPanelCls()
peaks_confirmation_panel.set_get_available_files(
    lambda: get_txt_files(polarity_combo.currentText())
)
_pw_splitter.addWidget(peaks_confirmation_panel)
_pw_splitter.setSizes([500, 0])   # start collapsed
_pw_splitter.setCollapsible(1, True)
_confirmation_panel_ref[0] = peaks_confirmation_panel

# Toggle action in the File menu of the peaks window
pw_confirm_toggle_action = QtWidgets.QAction("Peak list confirmation", peaks_win, checkable=True)
pw_file_menu.addSeparator()
pw_file_menu.addAction(pw_confirm_toggle_action)

def _on_confirm_panel_toggle(checked):
    sizes = _pw_splitter.sizes()
    total = sum(sizes)
    if checked:
        _pw_splitter.setSizes([max(total - 360, 300), 360])
    else:
        _pw_splitter.setSizes([total, 0])

pw_confirm_toggle_action.toggled.connect(_on_confirm_panel_toggle)

def _conf_load_file_in_main(filepath):
    """Load a sample file in the main spectrum viewer (triggered by cell hover)."""
    if not filepath or not os.path.exists(filepath):
        return
    idx = combo.findData(filepath)
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.blockSignals(True)
        combo.addItem(os.path.basename(filepath), filepath)
        combo.setCurrentIndex(combo.count() - 1)
        combo.blockSignals(False)
        plot_file(filepath)

def _conf_focus_peak_row(row_idx):
    _conf_focused_row_idx[0] = row_idx
    render_plot()

def _conf_unfocus_peak_rows():
    if _conf_focused_row_idx[0] >= 0:
        _conf_focused_row_idx[0] = -1
        render_plot()

peaks_confirmation_panel.request_load_file.connect(_conf_load_file_in_main)
peaks_confirmation_panel.request_focus_row.connect(_conf_focus_peak_row)
peaks_confirmation_panel.request_unfocus.connect(_conf_unfocus_peak_rows)

# Initial sync so that any pre-loaded peak rows appear in the panel
_sync_confirmation_panel()

# ─────────────────────────────────────────────
#  Auto-load last peak list
# ─────────────────────────────────────────────
def auto_load_peaks():
    QtCore.QTimer.singleShot(0, _rebuild_unmount_menu)
    # Restore full navigation history from saved_peak_files
    saved_files = _get_saved_peak_files()
    last_peak_file = settings.value("last_peak_file", "")
    # Build history: all saved files in order, last_peak_file last
    hist = [p for p in saved_files if p != last_peak_file and os.path.exists(p)]
    if last_peak_file and os.path.exists(last_peak_file):
        hist.append(last_peak_file)
    _peak_file_history.clear()
    _peak_file_history.extend(hist)
    _peak_file_hist_pos[0] = len(_peak_file_history) - 1 if _peak_file_history else -1
    QtCore.QTimer.singleShot(10, _update_peak_nav_buttons)

    if last_peak_file and os.path.exists(last_peak_file):
        try:
            _update_peaks_win_title(last_peak_file)
            _load_peak_file_into_rows(
                last_peak_file, push_history=False,
                progress=lambda i, n: splash_step(72 + 7 * i // max(n, 1)))
            # Last session's ticks / symbols / columns (once everything exists)
            QtCore.QTimer.singleShot(0, lambda p=last_peak_file: _restore_peak_session(p))
        except Exception as e:
            QtWidgets.QMessageBox.warning(main_win, "Load Failed",
                f"Failed to auto-load peaks from {last_peak_file}:\n{e}")
    else:
        # No peak file — restore envelope states from QSettings if available
        try:
            raw = settings.value("envelope_btn_states", "[]")
            states = json.loads(raw) if isinstance(raw, str) else []
            for i, row in enumerate(custom_peak_rows):
                if i < len(states) and states[i]:
                    row["envelope_btn"].setChecked(True)
        except Exception:
            pass

# ─────────────────────────────────────────────
#  Peak list import / export
# ─────────────────────────────────────────────
def _update_peaks_win_title(path=None):
    if path:
        peaks_win.setWindowTitle(f"Peaks  —  {os.path.basename(path)}")
    else:
        peaks_win.setWindowTitle("Peaks")

def _write_peak_list_to(path):
    """Serialise current peak rows (and groups) to *path* (.json)."""
    rows = []
    for r in custom_peak_rows:
        entry = {
            "checked":      r["checkbox"].isChecked(),
            "color":        r["color"][0].name(),
            "label":        r["label_input"].text(),
            "L_state":      r.get("L_state",  [0])[0],
            "1L_state":     r.get("1L_state", [0])[0],
            "envelope_btn": r["envelope_btn"].isChecked(),
            "group":        r.get("group_name", "Unclassified"),
        }
        entry.update(_row_legend_fields(r))
        if r.get("mode_btn") and r["mode_btn"].isChecked():
            entry["mode"]        = "range"
            entry["range_start"] = r["range_start"].value()
            entry["range_step"]  = r["range_step"].value()
            entry["range_end"]   = r["range_end"].value()
        else:
            entry["mode"]  = "manual"
            entry["peaks"] = r["peaks_input"].text()
        rows.append(entry)
    data = {
        "format_version": 2,
        "groups": [{"name": gd["name"], "alpha": gd.get("alpha", 255),
                    "collapsed": gd.get("collapsed", False),
                    "highlight": gd.get("highlight", True),
                    "L_active": gd.get("L_active", True),
                    "1L_active": gd.get("1L_active", True),
                    "curve_active": gd.get("curve_active", True)}
                   for gd in _peak_groups],
        "rows": rows,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    settings.setValue("last_peak_file", path)
    _update_peaks_win_title(path)
    _save_envelope_states_to_qsettings()
    _save_peak_session()


def _save_envelope_states_to_qsettings():
    """Persist envelope-button checked state for all rows to QSettings."""
    states = [r["envelope_btn"].isChecked() for r in custom_peak_rows]
    settings.setValue("envelope_btn_states", json.dumps(states))


def export_peak_list():
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        peaks_win, "Export Peak List", _get_dialog_dir("peak_list"), "JSON Files (*.json)")
    if path: _set_dialog_dir("peak_list", path)
    if not path:
        return
    if not path.lower().endswith(".json"):
        path += ".json"
    _write_peak_list_to(path)


def save_peak_list():
    """Save in-place; fall back to Export dialog if no file has been saved yet."""
    path = settings.value("last_peak_file", "")
    if path and os.path.isfile(path):
        _write_peak_list_to(path)
    else:
        export_peak_list()

def import_peak_list():
    path, _ = QtWidgets.QFileDialog.getOpenFileName(
        peaks_win, "Import Peak List", _get_dialog_dir("peak_list"), "JSON Files (*.json)")
    if path: _set_dialog_dir("peak_list", path)
    if not path: return
    _add_to_saved_peak_files(path)
    _peak_history_load(path)

# ─────────────────────────────────────────────
#  Pick-row combo sync
# ─────────────────────────────────────────────
def update_pick_row_combo():
    pick_row_combo.blockSignals(True); pick_row_combo.clear()
    for i, row in enumerate(custom_peak_rows):
        lbl = row["label_input"].text().strip() or f"Row {i+1}"
        pick_row_combo.addItem(lbl)
    pick_row_combo.blockSignals(False)
    _sync_pw_pick_label()

def _sync_pw_pick_label():
    lbl = pick_row_combo.currentText()
    pw_pick_row_label.setText(f"→ {lbl}" if lbl else "-")

pick_row_combo.currentIndexChanged.connect(lambda _: _sync_pw_pick_label())

# Wire pw_pick_mode_action into the peaks window Peaks menu now that it exists
pw_peaks_menu.addAction(pw_pick_mode_action)

update_pick_row_combo()
splash_step(72, "Restoring your peak lists…")
auto_load_peaks()

# ─────────────────────────────────────────────
#  Peak undo/redo  (text edits only)
# ─────────────────────────────────────────────
def _snapshot_peaks():
    return {
        "rows": [{"checked":  r["checkbox"].isChecked(),
                  "color":    r["color"][0].name(),
                  "peaks":    r["peaks_input"].text(),
                  "label":    r["label_input"].text(),
                  "L_state":  r.get("L_state",  [0])[0],
                  "1L_state": r.get("1L_state", [0])[0],
                  **_row_legend_fields(r)}
                 for r in custom_peak_rows],
        "pick_row": pick_row_combo.currentIndex(),
    }

def _update_undo_redo_buttons():
    undo_btn.setEnabled(bool(_peak_history))
    redo_btn.setEnabled(bool(_peak_redo))

def _restore_snapshot(snapshot):
    global _history_locked
    _history_locked = True
    rows = snapshot["rows"] if isinstance(snapshot, dict) else snapshot
    for i, item in enumerate(rows):
        if i >= len(custom_peak_rows): break
        row = custom_peak_rows[i]
        row["checkbox"].setChecked(item.get("checked", True))
        c = QtGui.QColor(item.get("color", "#ff0000"))
        row["color"][0] = c
        row["peaks_input"].blockSignals(True)
        row["peaks_input"].setText(item.get("peaks", ""))
        row["peaks_input"].blockSignals(False)
        row["label_input"].blockSignals(True)
        row["label_input"].setText(item.get("label", ""))
        row["label_input"].blockSignals(False)
        for key in ("L_state", "1L_state"):
            v = item.get(key, 0)
            if row.get(key) is not None:
                row[key][0] = v
                row["refresh_L"]() if key == "L_state" else row["refresh_1L"]()
        row["legend_symbol"][0], row["legend_col"][0] = None, None
        _apply_row_legend_fields(row, item)
        for child in row["widget"].children():
            if isinstance(child, QtWidgets.QPushButton) and child.minimumWidth() == 22:
                child.setStyleSheet(f"background-color: {c.name()}; border: 1px solid gray;"); break
    _history_locked = False
    _clear_highlight_cache()
    update_pick_row_combo()
    if isinstance(snapshot, dict) and "pick_row" in snapshot:
        pick_row_combo.setCurrentIndex(snapshot["pick_row"])
    render_plot()
    _auto_sync_legend_entries(); _rebuild_peak_legend_on_plot()
    _sync_confirmation_panel()

def undo_peaks():
    if not _peak_history: return
    _peak_redo.append(_snapshot_peaks())
    _restore_snapshot(_peak_history.pop())
    _update_undo_redo_buttons()

def redo_peaks():
    if not _peak_redo: return
    _peak_history.append(_snapshot_peaks())
    _restore_snapshot(_peak_redo.pop())
    _update_undo_redo_buttons()

undo_btn.clicked.connect(undo_peaks)
redo_btn.clicked.connect(redo_peaks)

# ─────────────────────────────────────────────
#  Application-wide shortcuts
# ─────────────────────────────────────────────
undo_sc_main = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), main_win)
undo_sc_main.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
undo_sc_main.activated.connect(undo_peaks)

redo_sc_y = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), main_win)
redo_sc_y.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
redo_sc_y.activated.connect(redo_peaks)

redo_sc_sz = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), main_win)
redo_sc_sz.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
redo_sc_sz.activated.connect(redo_peaks)

undo_sc_pw = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Z"), peaks_win)
undo_sc_pw.activated.connect(undo_peaks)
redo_sc_pw1 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Y"), peaks_win)
redo_sc_pw1.activated.connect(redo_peaks)
redo_sc_pw2 = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+Z"), peaks_win)
redo_sc_pw2.activated.connect(redo_peaks)

# Ctrl+Shift+P - add peak row from anywhere
add_peak_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Shift+P"), main_win)
add_peak_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
add_peak_shortcut.activated.connect(lambda: (open_peaks_window(), add_peak_row()))

# Z - toggle zoom box mode
def _toggle_zoom():
    if zoom_mode_action.isChecked():
        pan_mode_action.setChecked(True)
    else:
        zoom_mode_action.setChecked(True)
    apply_mouse_mode()

zoom_shortcut = QtGui.QShortcut(QtGui.QKeySequence("Z"), main_win)
zoom_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
zoom_shortcut.activated.connect(_toggle_zoom)

# P - toggle pick peaks mode
def _toggle_pick():
    pick_mode_action.setChecked(not pick_mode_action.isChecked())
    apply_mouse_mode()

pick_shortcut = QtGui.QShortcut(QtGui.QKeySequence("P"), main_win)
pick_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
pick_shortcut.activated.connect(_toggle_pick)

# A - toggle area measurement mode
area_mode_action.toggled.connect(_area_mode_set)

area_shortcut = QtGui.QShortcut(QtGui.QKeySequence("A"), main_win)
area_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
area_shortcut.activated.connect(lambda: area_mode_action.setChecked(not area_mode_action.isChecked()))

# R - open peak ratio window
ratio_shortcut = QtGui.QShortcut(QtGui.QKeySequence("R"), main_win)
ratio_shortcut.setContext(QtCore.Qt.ShortcutContext.ApplicationShortcut)
ratio_shortcut.activated.connect(lambda: _open_ratio_window())

# ─────────────────────────────────────────────
#  Display mode
# ─────────────────────────────────────────────
def set_display_mode(mode):
    global current_display
    current_display = mode
    settings.setValue("display_mode", mode)
    if mode == "dark":
        plot_widget.setBackground('k')
        for ax in ('bottom', 'left'):
            plot.getAxis(ax).setPen('w'); plot.getAxis(ax).setTextPen('w')
        v_line.setPen(pg.mkPen('y', width=1, style=QtCore.Qt.PenStyle.DashLine))
        h_line.setPen(pg.mkPen('y', width=1, style=QtCore.Qt.PenStyle.DashLine))
    else:
        plot_widget.setBackground('w')
        for ax in ('bottom', 'left'):
            plot.getAxis(ax).setPen('k'); plot.getAxis(ax).setTextPen('k')
        v_line.setPen(pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
        h_line.setPen(pg.mkPen('#888', width=1, style=QtCore.Qt.PenStyle.DashLine))
    v_line.setVisible(True); h_line.setVisible(True)
    _clear_norm_cache()
    _clear_highlight_cache()
    if _stacked_sub_plots:
        _destroy_stacked_layout()
    render_plot()

# ─────────────────────────────────────────────
#  View menu: mouse mode
# ─────────────────────────────────────────────
def apply_mouse_mode():
    picking = pick_mode_action.isChecked()
    if zoom_mode_action.isChecked() or picking:
        plot.vb.setMouseMode(pg.ViewBox.RectMode)
    else:
        plot.vb.setMouseMode(pg.ViewBox.PanMode)
    pick_into_widget.setVisible(picking)
    pw_pick_into_widget.setVisible(picking)
    if pw_pick_mode_action.isChecked() != picking:
        pw_pick_mode_action.blockSignals(True)
        pw_pick_mode_action.setChecked(picking)
        pw_pick_mode_action.blockSignals(False)

pan_mode_action.triggered.connect(apply_mouse_mode)
zoom_mode_action.triggered.connect(apply_mouse_mode)
pick_mode_action.triggered.connect(apply_mouse_mode)

pw_pick_mode_action.triggered.connect(lambda checked: (
    pick_mode_action.setChecked(checked), apply_mouse_mode()))

# ─────────────────────────────────────────────
#  Click-to-pick and double-click-to-remove
# ─────────────────────────────────────────────
def _find_nearest_peak_in_row(row, clicked_mz, tol=0.5):
    peaks = parse_peaks_text(row["peaks_input"].text())
    if not peaks: return None
    dists = [abs(p - clicked_mz) for p in peaks]
    idx   = int(np.argmin(dists))
    if dists[idx] <= tol: return idx, peaks[idx]
    return None

def plot_clicked(event):
    if not pick_mode_action.isChecked(): return
    pos = event.scenePos()
    if not plot.sceneBoundingRect().contains(pos): return
    mp = plot.vb.mapSceneToView(pos)
    clicked_mz = mp.x()

    row_idx = pick_row_combo.currentIndex()
    if row_idx < 0 or row_idx >= len(custom_peak_rows): return
    row = custom_peak_rows[row_idx]

    double = (event.double() if hasattr(event, 'double') else False)

    if double:
        if event.button() != QtCore.Qt.MouseButton.LeftButton: return
        result = _find_nearest_peak_in_row(row, clicked_mz)
        if result is None: return
        push_peak_history()
        peaks = parse_peaks_text(row["peaks_input"].text())
        peaks.pop(result[0])
        row["peaks_input"].setText(", ".join(f"{p:.4f}" for p in peaks))
        _clear_highlight_cache()
    else:
        if event.button() != QtCore.Qt.MouseButton.LeftButton: return
        snap_mz = clicked_mz
        if df is not None and len(df) > 0:
            idx = (df['mz'] - clicked_mz).abs().idxmin()
            snap_mz = round(df.loc[idx, 'mz'], 4)
        push_peak_history()
        current = row["peaks_input"].text().strip()
        new_val = f"{snap_mz:.4f}"
        row["peaks_input"].setText(current + f", {new_val}" if current else new_val)
        _clear_highlight_cache()

plot.scene().sigMouseClicked.connect(plot_clicked)

# ─────────────────────────────────────────────
#  Hover label + cross-lines
# ─────────────────────────────────────────────

import math  # at top of file if not already

def format_sci(v):
    if v == 0:
        return "0"
    power = int(math.floor(math.log10(abs(v))))
    value = v / (10 ** power)
    return f"{value:.3f}*10^{power:d}"

# def mouse_moved(pos):
#     if plot.sceneBoundingRect().contains(pos):
#         mp = plot.vb.mapSceneToView(pos)
#         x = mp.x()
#         y = 10 ** mp.y()*10**3
#         if crosshair_action.isChecked():
#             v_line.setPos(x)
#             h_line.setPos(mp.y())
#         current_label = ""
#         for entry in highlighted_ranges:
#             mz_min, mz_max, peak_label = entry[0], entry[1], entry[2]
#             if mz_min - 0.5 <= x <= mz_max + 0.5:
#                 current_label = peak_label; break
#         hover_label.setText(
#             f"m/z={x:.4f}  I={y:.2e}" +
#             (f"<br>{current_label}" if current_label else ""))
def mouse_moved(pos):
    if _stacked_mode:
        # Stacked-mode per-subplot handlers manage crosshair and m/z label.
        # Only hide the label when the cursor is outside every sub-plot.
        if not any(sp.sceneBoundingRect().contains(pos) for sp in _stacked_sub_plots):
            _mz_cursor_label.setVisible(False)
        return
    if plot.sceneBoundingRect().contains(pos):
        mp = plot.vb.mapSceneToView(pos)
        x = mp.x()
        y = mp.y() # oiginal consideration with y = 10 ** mp.y() * 10**3 not needed anymore? Also broke with intensiies above 300.
        if crosshair_action.isChecked():
            v_line.setPos(x)
            h_line.setPos(mp.y())
        current_label = ""
        for entry in highlighted_ranges:
            mz_min, mz_max, peak_label = entry[0], entry[1], entry[2]
            if mz_min - 0.5 <= x <= mz_max + 0.5:
                current_label = peak_label; break
        _cif_pt = int(settings.value("cursor_info_font_pt", 9))
        hover_label.setText(
            f"<span style='font-size:{_cif_pt}pt'>m/z={x:.4f}  I={y:.4f}" +
            (f"<br>{current_label}" if current_label else "") + "</span>")

        # ── Floating m/z label near cursor ────────────────────────────
        if mz_cursor_action.isChecked():
            show_int = show_integers_toggle.isChecked()
            mz_text  = str(int(round(x))) if show_int else f"{x:.2f}"
            _mz_cursor_label.setText(mz_text)
            _mz_cursor_label.adjustSize()
            # pos is in scene coordinates; map to plot_widget widget coordinates
            scene_pt = QtCore.QPointF(pos)
            widget_pt = plot_widget.mapFromScene(scene_pt) if hasattr(plot_widget, 'mapFromScene') else QtCore.QPoint(int(pos.x()), int(pos.y()))
            lx = int(widget_pt.x()) + 12
            ly = int(widget_pt.y()) - _mz_cursor_label.height() - 4
            # Clamp so label stays inside the widget
            lx = max(0, min(lx, plot_widget.width()  - _mz_cursor_label.width()))
            ly = max(0, min(ly, plot_widget.height() - _mz_cursor_label.height()))
            _mz_cursor_label.move(lx, ly)
            _mz_cursor_label.setVisible(True)
            _mz_cursor_label.raise_()
        else:
            _mz_cursor_label.setVisible(False)

    else:
        # Mouse left the plot area - hide the cursor label
        _mz_cursor_label.setVisible(False)

plot.scene().sigMouseMoved.connect(mouse_moved)
def _toggle_crosshair(checked):
    settings.setValue("crosshair", checked)
    v_line.setVisible(checked)
    h_line.setVisible(checked)
    for sub in _stacked_sub_plots:
        for item in sub.items:
            if getattr(item, '_is_crosshair', False):
                item.setVisible(checked)

plot.vb.sigRangeChanged.connect(lambda *_: _minimap.schedule_rect_update())
crosshair_action.toggled.connect(_toggle_crosshair)
def _on_grid_toggled(checked):
    settings.setValue("main_grid", checked)
    plot.showGrid(x=checked, y=checked, alpha=0.3)
    for _gsub in _stacked_sub_plots:
        _gsub.showGrid(x=checked, y=checked, alpha=0.5)

grid_action.toggled.connect(_on_grid_toggled)



def _save_legend_entries():
    """Persist the legend-only labels. Row entries are saved with the peak list."""
    try:
        data = [{"label": e["label"],
                 "color": QtGui.QColor(e["color"]).name(),
                 "symbol": e.get("symbol") or "",
                 "col": e.get("col", 1)}
                for e in _legend_extra_entries]
        _LEGEND_EXTRAS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LEGEND_EXTRAS_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_legend_entries():
    """At startup: read the legend-only labels and the pre-3.2 symbol map."""
    global _legend_extra_entries, _legacy_legend_map
    try:
        if _LEGEND_EXTRAS_FILE.exists():
            data = json.loads(_LEGEND_EXTRAS_FILE.read_text(encoding="utf-8"))
            _legend_extra_entries = [
                {"label": e.get("label", ""),
                 "color": QtGui.QColor(e.get("color", "#888888")),
                 "symbol": e.get("symbol") or None,
                 "col": int(e.get("col", 1))}
                for e in data if e.get("label")]
    except Exception:
        pass
    try:
        if _LEGEND_ENTRIES_FILE.exists():
            data = json.loads(_LEGEND_ENTRIES_FILE.read_text(encoding="utf-8"))
            _legacy_legend_map = {
                e["label"]: {"symbol": e.get("symbol") or None,
                             "col": int(e.get("col", 1))}
                for e in data if e.get("label")}
    except Exception:
        pass


# True while the plot is copied or exported: the legend then shows only what
# belongs in a figure (ticked, shown entries; no greying, dimming or hover marks).
_legend_export_mode = False

# Legend position: which corner it hangs from (fractions of the plot and of the
# legend) and the offset in pixels. Default: top-right, 10 px in from the corner.
_LEGEND_DEFAULT_ANCHOR = (1.0, 0.0, -10.0, 10.0)

def _legend_anchor():
    try:
        vals = tuple(float(v) for v in str(settings.value("legend/anchor", "")).split(","))
        if len(vals) == 4:
            return vals
    except ValueError:
        pass
    return _LEGEND_DEFAULT_ANCHOR

def _legend_at_default_position() -> bool:
    return all(abs(a - b) < 0.5 for a, b in zip(_legend_anchor(), _LEGEND_DEFAULT_ANCHOR))

def _notify_legend_listeners():
    for listener in list(_legend_listeners):
        try:
            listener()
        except RuntimeError:
            _legend_listeners.remove(listener)

def _set_legend_anchor(vals):
    settings.setValue("legend/anchor", ",".join(f"{v:g}" for v in vals))
    _notify_legend_listeners()

def _legend_anchor_at(item, top_left, save=False):
    """Put the legend's top-left corner at top_left (parent coordinates),
    anchored to the nearest corner of the plot so it keeps its place when the
    window is resized; with save=True also remember it."""
    parent = item.parentItem()
    if parent is None:
        return
    pr = parent.boundingRect()
    ir = QtCore.QRectF(top_left, item.boundingRect().size())
    ax = 1.0 if ir.center().x() > pr.center().x() else 0.0
    ay = 1.0 if ir.center().y() > pr.center().y() else 0.0
    ox = (ir.left() + ax * ir.width()) - pr.right() * ax
    oy = (ir.top() + ay * ir.height()) - pr.bottom() * ay
    vals = (ax, ay, round(ox, 1), round(oy, 1))
    item.anchor(itemPos=(ax, ay), parentPos=(ax, ay), offset=(vals[2], vals[3]))
    if save:
        _set_legend_anchor(vals)

def reset_legend_position():
    _set_legend_anchor(_LEGEND_DEFAULT_ANCHOR)
    _rebuild_peak_legend_on_plot()

def _legend_params_from_settings() -> dict:
    return {
        "font_pt":     settings.value("legend/font_pt",     11,    type=int),
        "font_family": settings.value("legend/font_family", ""),
        "show_box":    settings.value("legend/show_box",    True,  type=bool),
        "shadow":      settings.value("legend/shadow",      False, type=bool),
        "rounded":     settings.value("legend/rounded",     False, type=bool),
        "ncols":       settings.value("legend/ncols",       1,     type=int),
        "use_symbols": settings.value("legend/use_symbols", False, type=bool),
        "width":       settings.value("legend/width",       0,     type=int),
        "height":      settings.value("legend/height",      0,     type=int),
        "symbol_size": settings.value("legend/legend_symbol_size", 12, type=int),
    }

def _legend_visible_entries(export: bool) -> list:
    show_unticked = settings.value("legend/show_unticked", False, type=bool)
    out = []
    for e in _legend_entries:
        if not e.get("shown", True):
            continue
        if not e.get("checked", True) and (export or not show_unticked):
            continue
        out.append(e)
    return out

def _legend_labels_in_view():
    """Labels with at least one highlighted peak in the visible m/z range
    (None = do not dim anything, e.g. in stacked mode or with no spectrum)."""
    if _stacked_mode or not highlighted_ranges:
        return None
    lo, hi = plot.vb.viewRange()[0]
    return {lbl for (_, _, lbl, mz, _) in highlighted_ranges if lo <= mz <= hi}

_legend_hover_items: list = []

def _legend_hover(entry):
    """Mark the hovered legend entry's peaks in the view with dashed lines."""
    target = _stacked_sub_plots[0] if (_stacked_mode and _stacked_sub_plots) else plot
    for item in _legend_hover_items:
        for p in [plot] + list(_stacked_sub_plots):
            try: p.removeItem(item)
            except Exception: pass
    _legend_hover_items.clear()
    row = entry.get("row") if entry else None
    if row is None or _legend_export_mode:
        return
    lo, hi = target.vb.viewRange()[0]
    pen = pg.mkPen(QtGui.QColor(entry["color"]), width=1.5,
                   style=QtCore.Qt.PenStyle.DashLine)
    for mz in parse_peaks_text(row["peaks_input"].text()):
        if lo <= mz <= hi and len(_legend_hover_items) < 300:
            line = pg.InfiniteLine(pos=mz, angle=90, pen=pen, movable=False)
            line.setZValue(-5)
            target.addItem(line, ignoreBounds=True)
            _legend_hover_items.append(line)

# Double click on a legend entry zooms to its peaks (a single click does
# nothing). pyqtgraph does not always flag double clicks — it can report two
# single clicks — so two clicks on the same entry within the double-click
# interval count as one, and the extra click after a flagged double is ignored.
_legend_last_click_row = [None]
_legend_last_click = QtCore.QElapsedTimer()
_legend_last_double = QtCore.QElapsedTimer()

def _legend_clicked(row, double: bool):
    interval = QtWidgets.QApplication.doubleClickInterval()
    if not double:
        if (_legend_last_click_row[0] is row and _legend_last_click.isValid()
                and _legend_last_click.elapsed() < interval):
            double = True
        else:
            _legend_last_click_row[0] = row
            _legend_last_click.start()
            return
    if _legend_last_double.isValid() and _legend_last_double.elapsed() < interval:
        return                                  # same double click, already handled
    _legend_last_double.start()
    _legend_last_click_row[0] = None
    if row in custom_peak_rows:
        row["zoom_btn"].click()

def _rebuild_peak_legend_on_plot():
    """
    Rebuild the on-screen peak-list legend from _legend_entries.
    In stacked mode the legend is anchored to the first sub-plot's viewbox
    so it appears at the top-right of the stacked layout rather than on the
    hidden main plot.  In normal mode it is anchored to plot.vb as usual.
    """
    global _peak_legend
    _legend_hover(None)
    if _peak_legend is not None:
        # Remove from whichever viewbox it currently lives in
        for vb in ([plot.vb] + [s.vb for s in _stacked_sub_plots]):
            try:
                vb.removeItem(_peak_legend)
            except Exception:
                pass
        _peak_legend = None

    if not settings.value("legend/show", True, type=bool):
        return
    entries = _legend_visible_entries(_legend_export_mode)
    if not entries:
        return

    params = {
        **_legend_params_from_settings(),
        "dark_bg":     current_display == "dark",
        "interactive": not _legend_export_mode,
        "in_view":     None if _legend_export_mode else _legend_labels_in_view(),
    }

    _peak_legend = PeakListLegendItem()
    _peak_legend.set_data(entries, params)

    # In stacked mode anchor to the first (top) sub-plot so the legend
    # floats over the entire stacked area; otherwise use the main plot.
    if _stacked_mode and _stacked_sub_plots:
        target_vb = _stacked_sub_plots[0].vb
    else:
        target_vb = plot.vb

    _peak_legend.setParentItem(target_vb)
    ax, ay, ox, oy = _legend_anchor()
    _peak_legend.anchor(itemPos=(ax, ay), parentPos=(ax, ay), offset=(ox, oy))

def _legend_model_changed():
    """Something the legend shows changed (symbol, column, visibility, …)."""
    _auto_sync_legend_entries()
    _rebuild_peak_legend_on_plot()
    _render_peaks_or_full()        # symbols over peaks

# Dim legend entries with no peaks in view, a moment after panning / zooming.
_legend_view_timer = QtCore.QTimer()
_legend_view_timer.setSingleShot(True)
_legend_view_timer.setInterval(150)
_legend_view_timer.timeout.connect(lambda: (
    _peak_legend.set_in_view(_legend_labels_in_view())
    if _peak_legend is not None and not _legend_export_mode else None))
plot.vb.sigRangeChanged.connect(lambda *_: _legend_view_timer.start())


class _SymbolCombo(QtWidgets.QComboBox):
    """Symbol picker that shows only the glyph when closed (and is only as wide
    as that), while its drop-down list shows the full names at their width."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.addItem("None", "")
        for code, name in MARKER_SYMBOL_NAMES.items():
            self.addItem(name, code)
        glyph_w = max(self.fontMetrics().horizontalAdvance(g)
                      for g in list(MARKER_SYMBOL_GLYPHS.values()) + ["–"])
        arrow_w = self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent)
        self.setFixedWidth(glyph_w + arrow_w + 16)

    def paintEvent(self, _event):
        painter = QtWidgets.QStylePainter(self)
        opt = QtWidgets.QStyleOptionComboBox()
        self.initStyleOption(opt)
        code = self.currentData() or ""
        if self.toolTip() != self.currentText():
            self.setToolTip(self.currentText())          # full name on hover
        opt.currentText = MARKER_SYMBOL_GLYPHS.get(code, "–")
        opt.currentIcon = QtGui.QIcon()
        painter.drawComplexControl(QtWidgets.QStyle.ComplexControl.CC_ComboBox, opt)
        painter.drawControl(QtWidgets.QStyle.ControlElement.CE_ComboBoxLabel, opt)

    def showPopup(self):
        view = self.view()
        view.setMinimumWidth(view.sizeHintForColumn(0) + 2 * view.frameWidth()
                             + self.style().pixelMetric(QtWidgets.QStyle.PixelMetric.PM_ScrollBarExtent))
        super().showPopup()


class LegendEntryWidget(QtWidgets.QWidget):
    """One row in the Legend parameters dialog.

    It edits its source directly: a peak row (label, colour, symbol, column,
    shown in legend) or a legend-only label. There is no copy to keep in sync.
    """

    def __init__(self, entry, show_symbol=False, parent=None, dialog=None):
        super().__init__(parent)
        self._entry = entry
        self._row   = entry.get("row")
        self._extra = entry.get("extra")
        self._dialog = dialog
        self._shown_state = {}           # what update_from() last displayed
        self.key = LegendParametersDialog._key(entry)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 1, 0, 1)
        lay.setSpacing(4)

        # Selection handle, as in the Peaks window
        self._sel_handle = QtWidgets.QLabel()
        self._sel_handle.setFixedWidth(14)
        self._sel_handle.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._sel_handle.setToolTip(
            "Click to select  |  Ctrl: toggle  |  Shift: range  |  Ctrl+Shift: extend range\n"
            "Symbol, column, tick and colour changes apply to all selected lines.")
        self._sel_handle.mousePressEvent = self._on_handle_pressed
        lay.addWidget(self._sel_handle)
        self.set_selected(False)

        # Shown in legend (peak rows) / remove (legend-only labels)
        if self._row is not None:
            self._show_cb = QtWidgets.QCheckBox()
            self._show_cb.setToolTip(
                "Ticked = shown (the same tick as in the Peaks window)")
            self._show_cb.toggled.connect(self._on_show)
            lay.addWidget(self._show_cb)
        else:
            self._show_cb = None
            rm = QtWidgets.QPushButton("✕")
            rm.setFixedSize(18, 18)
            rm.setFlat(True)
            rm.setToolTip("Remove this legend-only label")
            rm.clicked.connect(self._remove_extra)
            lay.addWidget(rm)

        # Up / Down reorder buttons
        up_btn = QtWidgets.QPushButton("▲")
        up_btn.setFixedSize(18, 18)
        up_btn.setFlat(True)
        up_btn.setToolTip("Move up (reorders the peak list within its group)")
        up_btn.clicked.connect(lambda: self._move(-1))
        down_btn = QtWidgets.QPushButton("▼")
        down_btn.setFixedSize(18, 18)
        down_btn.setFlat(True)
        down_btn.setToolTip("Move down (reorders the peak list within its group)")
        down_btn.clicked.connect(lambda: self._move(+1))
        lay.addWidget(up_btn)
        lay.addWidget(down_btn)

        # Color swatch button
        self._swatch = QtWidgets.QPushButton()
        self._swatch.setFixedSize(20, 20)
        self._swatch.setToolTip("Colour (the peak list's colour)")
        self._swatch.clicked.connect(self._pick_color)
        lay.addWidget(self._swatch)

        # Symbol combo
        self._sym_combo = _SymbolCombo()
        self._sym_combo.setVisible(show_symbol)
        self._sym_combo.currentIndexChanged.connect(self._on_symbol)
        lay.addWidget(self._sym_combo)

        # Label text
        self._label_edit = QtWidgets.QLineEdit()
        self._label_edit.setMinimumWidth(220)
        self._label_edit.setToolTip("Label (renames the peak list)" if self._row is not None
                                     else "Legend-only label")
        self._label_edit.editingFinished.connect(self._on_label)
        lay.addWidget(self._label_edit, 1)

        # Column assignment — "col:" label before the spin
        lay.addSpacing(6)
        col_lbl = QtWidgets.QLabel("col:")
        col_lbl.setStyleSheet("color: gray;")
        lay.addWidget(col_lbl)
        self._col_spin = QtWidgets.QSpinBox()
        self._col_spin.setRange(1, 10)
        self._col_spin.setFixedWidth(44)
        self._col_spin.setToolTip("Column this entry belongs to")
        self._col_spin.valueChanged.connect(self._on_col)
        lay.addWidget(self._col_spin)

        self.update_from(entry)

    # ── display ────────────────────────────────────────────────────────────

    def update_from(self, entry):
        """Show the current values of the source, without emitting edits.
        Only what changed since the last call is touched (this runs for every
        line after every legend sync)."""
        self._entry = entry
        new = {"color": QtGui.QColor(entry.get("color", "#888888")).name(),
               "symbol": entry.get("symbol") or "", "col": int(entry.get("col", 1)),
               "label": entry.get("label", ""), "checked": bool(entry.get("checked", True))}
        old = self._shown_state
        if new == old:
            return
        for w in (self._sym_combo, self._col_spin, self._label_edit) + (
                (self._show_cb,) if self._show_cb else ()):
            w.blockSignals(True)
        try:
            if new["color"] != old.get("color"):
                self._swatch.setStyleSheet(
                    f"background-color:{new['color']}; border:1px solid #666;")
            if new["symbol"] != old.get("symbol"):
                self._sym_combo.setCurrentIndex(max(self._sym_combo.findData(new["symbol"]), 0))
            if new["col"] != old.get("col"):
                self._col_spin.setValue(new["col"])
            if new["label"] != old.get("label") and not self._label_edit.hasFocus():
                self._label_edit.setText(new["label"])
            if self._show_cb is not None and new["checked"] != old.get("checked"):
                self._show_cb.setChecked(new["checked"])
        finally:
            for w in (self._sym_combo, self._col_spin, self._label_edit) + (
                    (self._show_cb,) if self._show_cb else ()):
                w.blockSignals(False)
        if new["checked"] != old.get("checked"):
            unticked = not new["checked"]
            self._label_edit.setStyleSheet("color: gray; font-style: italic;" if unticked else "")
            self._label_edit.setToolTip(
                "Unticked: not in the legend or in copied / exported plots" if unticked
                else ("Label (renames the peak list)" if self._row is not None
                      else "Legend-only label"))
        self._shown_state = new

    # ── selection ──────────────────────────────────────────────────────────

    def set_selected(self, selected: bool):
        self._sel_handle.setStyleSheet(
            "min-width:12px;max-width:12px;border:1px solid #555;border-radius:2px;margin:1px;"
            + ("background:#3d8ee0;" if selected else "background:transparent;"))

    def _on_handle_pressed(self, event):
        if self._dialog is not None:
            self._dialog._on_handle_clicked(self, event.modifiers())

    def _targets(self):
        """This line, or every selected line when this one is part of the selection."""
        return self._dialog._selected_widgets(self) if self._dialog is not None else [self]

    def set_symbol_visible(self, visible: bool):
        self._sym_combo.setVisible(visible)

    # ── edits go straight to the source ────────────────────────────────────

    def _changed(self):
        if any(w._extra is not None for w in self._targets()):
            _save_legend_entries()
        _legend_model_changed()

    def _on_show(self, checked):
        targets = [w for w in self._targets() if w._row is not None]
        if len(targets) == 1:
            # The Peaks window checkbox's own handlers update the plot and legend.
            self._row["checkbox"].setChecked(bool(checked))
            return
        for w in targets:                       # several: one update at the end
            cb = w._row["checkbox"]
            cb.blockSignals(True); cb.setChecked(bool(checked)); cb.blockSignals(False)
        _legend_model_changed()

    def _on_symbol(self, _i):
        code = self._sym_combo.currentData() or ""
        for w in self._targets():
            if w._row is not None:
                w._row["legend_symbol"][0] = code
            else:
                w._extra["symbol"] = code or None
        self._changed()

    def _on_col(self, value):
        for w in self._targets():
            if w._row is not None:
                w._row["legend_col"][0] = int(value)
            else:
                w._extra["col"] = int(value)
        self._changed()

    def _on_label(self):
        text = self._label_edit.text().strip()
        if not text:
            self._label_edit.setText(self._entry.get("label", ""))
            return
        if self._row is not None:
            if text != self._row["label_input"].text().strip():
                self._row["label_input"].setText(text)   # the row's own handlers follow
                _legend_model_changed()
        elif text != self._extra["label"]:
            self._extra["label"] = text
            self._changed()

    def _pick_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._entry.get("color", "#888888")), self)
        if not c.isValid():
            return
        for w in self._targets():
            if w._row is not None:
                w._row["set_color"](c)                  # updates peaks, legend, button
            else:
                w._extra["color"] = c
        if any(w._extra is not None for w in self._targets()):
            self._changed()

    def _move(self, step):
        if self._extra is not None:
            i = _legend_extra_entries.index(self._extra)
            j = i + step
            if 0 <= j < len(_legend_extra_entries):
                _legend_extra_entries[i], _legend_extra_entries[j] = \
                    _legend_extra_entries[j], _legend_extra_entries[i]
                self._changed()
            return
        # Move the peak row past its neighbour in the same group
        group = self._row.get("group_name", "Unclassified")
        same = [r for r in _rows_in_legend_order()
                if r.get("group_name", "Unclassified") == group and _is_legend_candidate(r)]
        k = same.index(self._row) if self._row in same else -1
        if k < 0 or not (0 <= k + step < len(same)):
            return
        neighbour = custom_peak_rows.index(same[k + step])
        src = custom_peak_rows.index(self._row)
        peaks_rows_container.move_rows([src], neighbour if step < 0 else neighbour + 1)

    def _remove_extra(self):
        if self._extra in _legend_extra_entries:
            _legend_extra_entries.remove(self._extra)
        self._changed()

class LegendPreviewWidget(QtWidgets.QWidget):
    """
    Live preview in the Legend parameters dialog. It draws with the on-plot
    legend item itself (PeakListLegendItem), so what it shows is exactly the
    legend that copies and exports get.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries = []
        self._params  = {}
        self.setMinimumSize(220, 120)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)

    def set_data(self, entries, use_symbols, font_pt, font_family,
                 show_box, shadow, rounded, ncols, width=0, height=0, symbol_size=12):
        self._entries = list(entries)
        self._params = {"use_symbols": use_symbols, "font_pt": font_pt,
                        "font_family": font_family, "show_box": show_box,
                        "shadow": shadow, "rounded": rounded, "ncols": ncols,
                        "width": width, "height": height, "symbol_size": symbol_size,
                        "dark_bg": False, "interactive": False, "in_view": None}
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#1e1e1e"))
        if not self._entries:
            painter.setPen(QtGui.QColor("#666"))
            painter.drawText(self.rect(), QtCore.Qt.AlignmentFlag.AlignCenter,
                             "No active peak-list entries")
            painter.end()
            return
        item = PeakListLegendItem()
        item.set_data(self._entries, self._params)
        w, h = item._w, item._h
        margin = 10
        scale = min(1.0, (self.width() - 2 * margin) / max(w + 5, 1),
                    (self.height() - 2 * margin) / max(h + 5, 1))
        painter.translate((self.width() - w * scale) / 2, (self.height() - h * scale) / 2)
        painter.scale(scale, scale)
        item.paint(painter, None)
        painter.end()

    @staticmethod
    def _draw_symbol(painter, cx, cy, r, symbol, color):
        import math
        c = color if isinstance(color, QtGui.QColor) else QtGui.QColor(color)
        painter.setBrush(QtGui.QBrush(c))
        painter.setPen(QtGui.QPen(c, 1.5))

        def poly(*pts):
            painter.drawPolygon([QtCore.QPointF(x, y) for x, y in pts])

        if symbol == 'o':
            painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)
        elif symbol == 's':
            painter.drawRect(int(cx - r), int(cy - r), int(2 * r), int(2 * r))
        elif symbol == 'd':
            poly((cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy))
        elif symbol == 't':
            poly((cx - r, cy - r), (cx, cy + r), (cx + r, cy - r))
        elif symbol == 't1':
            poly((cx - r, cy + r), (cx, cy - r), (cx + r, cy + r))
        elif symbol == 't2':
            poly((cx + r, cy), (cx - r, cy - r), (cx - r, cy + r))
        elif symbol == 't3':
            poly((cx - r, cy), (cx + r, cy - r), (cx + r, cy + r))
        elif symbol in ('+', 'crosshair'):
            painter.drawLine(QtCore.QPointF(cx - r, cy), QtCore.QPointF(cx + r, cy))
            painter.drawLine(QtCore.QPointF(cx, cy - r), QtCore.QPointF(cx, cy + r))
            if symbol == 'crosshair':
                painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                painter.drawEllipse(QtCore.QPointF(cx, cy), r * 0.45, r * 0.45)
        elif symbol == 'x':
            painter.drawLine(QtCore.QPointF(cx - r, cy - r), QtCore.QPointF(cx + r, cy + r))
            painter.drawLine(QtCore.QPointF(cx + r, cy - r), QtCore.QPointF(cx - r, cy + r))
        elif symbol == 'star':
            pts = []
            for i in range(5):
                a_out = math.radians(-90 + i * 72)
                a_in  = math.radians(-90 + i * 72 + 36)
                pts += [(cx + r * math.cos(a_out), cy + r * math.sin(a_out)),
                        (cx + r * 0.4 * math.cos(a_in), cy + r * 0.4 * math.sin(a_in))]
            poly(*pts)
        elif symbol == 'p':
            pts = [(cx + r * math.cos(math.radians(-90 + i * 72)),
                    cy + r * math.sin(math.radians(-90 + i * 72))) for i in range(5)]
            poly(*pts)
        elif symbol == 'h':
            pts = [(cx + r * math.cos(math.radians(i * 60)),
                    cy + r * math.sin(math.radians(i * 60))) for i in range(6)]
            poly(*pts)
        elif symbol == 'arrow_up':
            poly((cx, cy - r), (cx + r * 0.6, cy + r * 0.5), (cx - r * 0.6, cy + r * 0.5))
        elif symbol == 'arrow_down':
            poly((cx, cy + r), (cx + r * 0.6, cy - r * 0.5), (cx - r * 0.6, cy - r * 0.5))
        elif symbol == 'arrow_right':
            poly((cx + r, cy), (cx - r * 0.5, cy - r * 0.6), (cx - r * 0.5, cy + r * 0.6))
        elif symbol == 'arrow_left':
            poly((cx - r, cy), (cx + r * 0.5, cy - r * 0.6), (cx + r * 0.5, cy + r * 0.6))
        else:
            painter.drawEllipse(QtCore.QPointF(cx, cy), r, r)

def _fmt_legend_label(label: str) -> str:
    """Replace '_n' with subscript ₙ for legend display."""
    return label.replace("_n", "ₙ")


class PeakListLegendItem(pg.GraphicsWidget, pg.GraphicsWidgetAnchor):
    """
    On-screen peak-list legend rendered via QPainter.
    Exact visual match with LegendPreviewWidget; supports multi-column,
    custom font family, background, shadow, and rounded corners.
    Inherits anchor() from GraphicsWidgetAnchor, same as pg.LegendItem.
    """

    def __init__(self):
        pg.GraphicsWidget.__init__(self)
        pg.GraphicsWidgetAnchor.__init__(self)
        self._entries: list[dict] = []
        self._params:  dict       = {}
        self._w = 60
        self._h = 20
        self._hit: list = []          # [(QRectF, entry)] from the last paint
        self._hover = None
        self.setAcceptHoverEvents(True)

    def set_data(self, entries: list[dict], params: dict):
        self._entries = entries
        self._params  = params
        self._w, self._h = self._measure()
        self._hit = self._hit_rects()
        self.setGeometry(0, 0, self._w, self._h)
        self.update()

    def _hit_rects(self):
        """[(QRectF, entry)] for every entry cell, computed from the layout
        (not from painting, so clicks work before the first paint)."""
        if not self._entries:
            return []
        col_entries, col_widths, ROW_H, _, _ = self._layout()
        PAD, out, cx = 8, [], 8
        for ci, col in enumerate(col_entries):
            w = col_widths[ci] if ci < len(col_widths) else PAD
            for ri, entry in enumerate(col):
                out.append((QtCore.QRectF(cx - 3, PAD + ri * ROW_H, w, ROW_H), entry))
            cx += w
        return out

    def set_in_view(self, labels):
        """Labels with peaks in the visible range; others are drawn dimmed."""
        if labels != self._params.get("in_view"):
            self._params["in_view"] = labels
            self.update()

    # ── browsing: click to tick / untick, double-click to zoom, hover ──────

    def _entry_at(self, pos):
        for rect, entry in self._hit:
            if rect.contains(pos):
                return entry
        return None

    def hoverEvent(self, ev):
        if not self._params.get("interactive"):
            return
        entry = None if ev.isExit() else self._entry_at(ev.pos())
        if entry is self._hover:
            return
        self._hover = entry
        row = entry.get("row") if entry else None
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor if row is not None
                       else QtCore.Qt.CursorShape.ArrowCursor)
        if row is not None:
            n = len(parse_peaks_text(row["peaks_input"].text()))
            state = "ticked" if entry.get("checked", True) else "unticked"
            self.setToolTip(f"{entry['label']}  ({n} peaks, {state})\n"
                            "Double-click: zoom to its peaks   ·   Drag: move the legend")
        else:
            self.setToolTip("")
        _legend_hover(entry)
        self.update()

    def mouseDragEvent(self, ev):
        """Drag the legend to move it; the new place is remembered."""
        if not self._params.get("interactive") or ev.button() != QtCore.Qt.MouseButton.LeftButton:
            ev.ignore()
            return
        ev.accept()
        if ev.isStart():
            _legend_hover(None)
        # The anchor re-positions the item whenever its geometry changes, so a
        # plain setPos() would snap back: move by re-anchoring instead.
        new_top_left = self.pos() + self.mapToParent(ev.pos()) - self.mapToParent(ev.lastPos())
        _legend_anchor_at(self, new_top_left, save=ev.isFinish())

    def mouseClickEvent(self, ev):
        if not self._params.get("interactive") or ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        entry = self._entry_at(ev.pos())
        if entry is None or entry.get("row") is None:
            return
        ev.accept()
        _legend_clicked(entry["row"], ev.double())

    def boundingRect(self):
        return QtCore.QRectF(0, 0, self._w, self._h)

    # ── internal helpers ───────────────────────────────────────────────────

    def _font(self):
        fam = self._params.get("font_family", "")
        pt  = max(6, self._params.get("font_pt", 11))
        f   = QtGui.QFont(fam or "")
        f.setPointSize(pt)
        return f

    def _sym_radius(self):
        """Legend symbol radius (px) from the "Symbol size" setting (diameter)."""
        return max(2.0, float(self._params.get("symbol_size", 12)) / 2.0)

    def _layout(self, natural=False):
        """Return (col_entries, col_widths, ROW_H, fm, SWATCH).

        With a fixed legend width (Size W) larger than the content, the extra
        space is shared between the columns (unless natural=True)."""
        font  = self._font()
        fm    = QtGui.QFontMetrics(font)
        ncols = max(1, self._params.get("ncols", 1))
        PAD, GAP = 8, 5
        r = self._sym_radius() if self._params.get("use_symbols", False) else 0
        SWATCH = int(max(24, 2 * r + 6))
        ROW_H = int(max(fm.height() + 4, 18, 2 * r + 4))

        col_entries = [[] for _ in range(ncols)]
        for e in self._entries:
            c = min(max(e.get("col", 1), 1), ncols) - 1
            col_entries[c].append(e)

        col_widths = []
        for col in col_entries:
            max_text = max(
                (fm.horizontalAdvance(_fmt_legend_label(e.get("label", ""))) for e in col),
                default=40)
            col_widths.append(SWATCH + GAP + max_text + PAD)

        target_w = 0 if natural else int(self._params.get("width", 0) or 0)
        natural_w = PAD + sum(col_widths)
        if target_w > natural_w and col_widths:
            extra = (target_w - natural_w) / len(col_widths)
            col_widths = [w + extra for w in col_widths]

        return col_entries, col_widths, ROW_H, fm, SWATCH

    def natural_size(self):
        """Size the legend needs for its content (ignoring Size W × H)."""
        if not self._entries:
            return 60, 20
        col_entries, col_widths, ROW_H, _, _ = self._layout(natural=True)
        PAD = 8
        max_rows = max((len(c) for c in col_entries), default=1)
        return int(PAD + sum(col_widths)), int(PAD + max_rows * ROW_H + PAD)

    def _measure(self):
        w, h = self.natural_size()
        tw = int(self._params.get("width", 0) or 0)
        th = int(self._params.get("height", 0) or 0)
        return (tw or w), (th or h)

    # ── rendering ──────────────────────────────────────────────────────────

    def paint(self, painter, option, widget=None):
        if not self._entries:
            return

        p        = self._params
        show_box = p.get("show_box", True)
        shadow   = p.get("shadow",   False)
        rounded  = p.get("rounded",  False)
        use_sym  = p.get("use_symbols", False)
        dark_bg  = p.get("dark_bg",  False)

        font = self._font()
        painter.setFont(font)
        col_entries, col_widths, ROW_H, fm, SWATCH = self._layout()
        sym_r = self._sym_radius()

        PAD, GAP = 8, 5
        w, h = self._w, self._h
        rad  = 5 if rounded else 0

        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        # Shadow
        if shadow:
            painter.setBrush(QtGui.QColor(0, 0, 0, 70))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            sr = QtCore.QRectF(4, 4, w, h)
            if rad:
                painter.drawRoundedRect(sr, rad, rad)
            else:
                painter.drawRect(sr)

        # Background
        if show_box:
            painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 210)))
            painter.setPen(QtGui.QPen(QtGui.QColor(150, 150, 150, 200), 1))
            br = QtCore.QRectF(0, 0, w, h)
            if rad:
                painter.drawRoundedRect(br, rad, rad)
            else:
                painter.drawRect(br)

        text_color = (QtGui.QColor("#111111") if (show_box or not dark_bg)
                      else QtGui.QColor("#eeeeee"))

        # A fixed Size W × H smaller than the content cuts it off at the box.
        painter.save()
        painter.setClipRect(QtCore.QRectF(0, 0, w, h))
        in_view = p.get("in_view")
        cx = PAD
        for ci, col in enumerate(col_entries):
            for ri, entry in enumerate(col):
                color = entry.get("color", QtGui.QColor("#888"))
                if not isinstance(color, QtGui.QColor):
                    color = QtGui.QColor(color)
                label = _fmt_legend_label(entry.get("label", ""))
                sym   = entry.get("symbol") if use_sym else None

                row_y = PAD + ri * ROW_H
                mid_y = row_y + ROW_H // 2
                cell = QtCore.QRectF(cx - 3, row_y, (col_widths[ci] if ci < len(col_widths) else PAD), ROW_H)

                # Browsing only: hover background, greyed unticked entries,
                # dimmed entries without peaks in the current view.
                painter.setOpacity(1.0)
                if entry is self._hover:
                    painter.setPen(QtCore.Qt.PenStyle.NoPen)
                    painter.setBrush(QtGui.QColor(120, 120, 120, 45))
                    painter.drawRoundedRect(cell, 3, 3)
                unticked = not entry.get("checked", True)
                if unticked:
                    painter.setOpacity(0.35)
                elif (in_view is not None and entry.get("row") is not None
                      and entry.get("label") not in in_view):
                    painter.setOpacity(0.5)

                painter.setPen(QtGui.QPen(color, 2.5))
                painter.setBrush(QtGui.QBrush(color))
                if sym:
                    LegendPreviewWidget._draw_symbol(
                        painter, cx + SWATCH / 2, mid_y, sym_r, sym, color)
                else:
                    painter.drawLine(cx, mid_y, cx + SWATCH, mid_y)

                painter.setPen(QtGui.QPen(text_color))
                text_y = row_y + (ROW_H + fm.ascent() - fm.descent()) // 2
                if unticked:
                    f_strike = QtGui.QFont(font); f_strike.setStrikeOut(True)
                    painter.setFont(f_strike)
                from droplet_pkg.io.pgf_writer import text_anchor
                with text_anchor("left"):          # PGF: start right after the symbol
                    painter.drawText(cx + SWATCH + GAP, text_y, label)
                painter.setFont(font)
                painter.setOpacity(1.0)

            cx += col_widths[ci] if ci < len(col_widths) else PAD
        painter.restore()


class SavedLabelsImportDialog(QtWidgets.QDialog):
    """
    Modal dialog to select one or more saved labels to import into the legend.
    Shows colour swatch and symbol name for each saved entry.
    """

    def __init__(self, parent, labels_file: Path):
        super().__init__(parent)
        self.setWindowTitle("Import saved labels")
        self.setMinimumWidth(480)
        self.setMinimumHeight(340)
        self._file     = labels_file
        self._data     = []
        self._selected = []
        self._build_ui()
        self._load()

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "Select one or more labels to add as legend entries:"))

        self._table = QtWidgets.QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Colour", "Symbol", "Label"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 50)
        self._table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(1, 120)
        self._table.horizontalHeader().setSectionResizeMode(
            2, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        self._table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self._table)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _load(self):
        if not self._file.exists():
            return
        try:
            self._data = sorted(
                json.loads(self._file.read_text(encoding="utf-8")),
                key=lambda e: e.get("text", "").lower())
        except Exception:
            return

        self._table.setRowCount(len(self._data))
        for ri, entry in enumerate(self._data):
            color  = QtGui.QColor(entry.get("color", "#888888"))
            sym    = entry.get("symbol", "")
            text   = entry.get("text", "")

            swatch = QtWidgets.QTableWidgetItem()
            swatch.setBackground(QtGui.QBrush(color))
            self._table.setItem(ri, 0, swatch)

            sym_name = MARKER_SYMBOL_NAMES.get(sym, "—") if sym else "—"
            self._table.setItem(ri, 1, QtWidgets.QTableWidgetItem(sym_name))
            self._table.setItem(ri, 2, QtWidgets.QTableWidgetItem(text))

    def _on_accept(self):
        rows = sorted({i.row() for i in self._table.selectedItems()})
        self._selected = [self._data[r] for r in rows]
        self.accept()

    def get_selected(self) -> list[dict]:
        return self._selected


class _LegendGroupHeader(QtWidgets.QWidget):
    """Collapsible group section header inside the LegendParametersDialog entries list."""

    def __init__(self, name: str, toggle_cb, parent=None):
        super().__init__(parent)
        self._toggle_cb = toggle_cb
        self._children: list = []
        self._expanded = True
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 3, 2, 3)
        self._arrow = QtWidgets.QPushButton("▼")
        self._arrow.setFixedSize(18, 18)
        self._arrow.setFlat(True)
        self._arrow.clicked.connect(self._on_click)
        lay.addWidget(self._arrow)
        lay.addWidget(QtWidgets.QLabel(f"<b>{name}</b>"), 1)
        self.setStyleSheet("background: rgba(100,100,100,40); border-radius:3px;")

    def _on_click(self):
        self._expanded = not self._expanded
        self._arrow.setText("▼" if self._expanded else "▶")
        for w in self._children:
            try: w.setVisible(self._expanded)
            except RuntimeError: pass
        self._toggle_cb()

    def add_child(self, w):
        self._children.append(w)


class LegendParametersDialog(QtWidgets.QWidget, StayOnTopMixin):
    """
    Legend parameters: how the peak-list legend looks, and which symbol and
    column each peak list uses.

    The entries edit the peak rows directly (the rows are the legend's only
    source), so the dialog never needs refreshing: it follows the Peaks window
    live through _legend_listeners. Settings are saved as soon as they change.

    Labels can be saved to ~/.droplet/legend_labels.json and imported later:
    an imported label gives its symbol to the peak list with the same label,
    or becomes a legend-only entry if there is none.
    """

    _LABELS_FILE = Path.home() / ".droplet" / "legend_labels.json"
    _EXTRA_GROUP = "Legend-only labels"

    def __init__(self, parent, peak_rows, spectrum_legend, plot_ref, app_settings):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Legend parameters")
        self.setMinimumHeight(560)

        self._peak_rows       = peak_rows
        self._spectrum_legend = spectrum_legend   # pg.LegendItem for spectra
        self._plot            = plot_ref
        self._settings        = app_settings
        self._entry_widgets: list[LegendEntryWidget] = []
        self._keys: list = []
        self._group_headers: dict[str, _LegendGroupHeader] = {}
        self._collapsed: set[str] = set()
        self._selected_keys: set = set()
        self._anchor_key = None

        self._build_ui()
        self._sync_from_model()
        _legend_listeners.append(self._sync_from_model)

    def closeEvent(self, event):
        if self._sync_from_model in _legend_listeners:
            _legend_listeners.remove(self._sync_from_model)
        super().closeEvent(event)

    # ── UI construction ─────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Menu bar ──────────────────────────────────────────────────
        vbox = QtWidgets.QVBoxLayout(self)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        menu_bar = QtWidgets.QMenuBar(self)
        labels_menu = menu_bar.addMenu("Labels")
        import_act = QtWidgets.QAction("Import saved labels…", self)
        import_act.setToolTip(
            "Browse previously saved labels. A label matching a peak list\n"
            "gives it its symbol; other labels become legend-only entries.")
        import_act.triggered.connect(self._open_import_dialog)
        labels_menu.addAction(import_act)
        save_act = QtWidgets.QAction("Save current labels", self)
        save_act.setToolTip(
            "Append every current label to the persistent saved list\n"
            "(duplicates are ignored).")
        save_act.triggered.connect(self._save_current_labels)
        labels_menu.addAction(save_act)
        self._install_stay_on_top(menu_bar, self._settings)
        vbox.addWidget(menu_bar)

        # ── Content row: QSplitter so the user can resize panels ──────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        vbox.addWidget(splitter, 1)

        # ── Left panel ────────────────────────────────────────────────
        left_inner = QtWidgets.QWidget()
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_inner)
        left_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        splitter.addWidget(left_scroll)

        lay = QtWidgets.QVBoxLayout(left_inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)

        # ── Show on plot ──
        self.show_cb = QtWidgets.QCheckBox("Show peak-list legend on plot")
        self.show_cb.setChecked(
            self._settings.value("legend/show", True, type=bool))
        self.show_cb.toggled.connect(self._apply)
        lay.addWidget(self.show_cb)

        self.show_unticked_cb = QtWidgets.QCheckBox(
            "Keep unticked peak lists in the on-screen legend (greyed)")
        self.show_unticked_cb.setToolTip(
            "Unticked peak lists are shown faded in the on-screen legend.\n"
            "They never appear in copied or exported plots.")
        self.show_unticked_cb.setChecked(
            self._settings.value("legend/show_unticked", False, type=bool))
        self.show_unticked_cb.toggled.connect(self._apply)
        lay.addWidget(self.show_unticked_cb)

        # ── Mode ──
        mode_grp = QtWidgets.QGroupBox("Symbol mode")
        mode_lay = QtWidgets.QVBoxLayout(mode_grp)
        self.mode_color_rb  = QtWidgets.QRadioButton("Color only  (colored line swatch)")
        self.mode_symbol_rb = QtWidgets.QRadioButton("Color + symbol")
        self.mode_color_rb.setChecked(
            not self._settings.value("legend/use_symbols", False, type=bool))
        self.mode_symbol_rb.setChecked(
            self._settings.value("legend/use_symbols", False, type=bool))
        mode_lay.addWidget(self.mode_color_rb)
        mode_lay.addWidget(self.mode_symbol_rb)

        sym_btns = QtWidgets.QHBoxLayout()
        fill_btn = QtWidgets.QPushButton("⚡  Fill missing symbols")
        fill_btn.setToolTip(
            "Give a free symbol to every peak list that has none, and switch\n"
            "to Color + symbol mode. Symbols already set are never changed.\n"
            "(New peak lists get a symbol automatically.)")
        fill_btn.clicked.connect(self._fill_missing_symbols)
        reassign_btn = QtWidgets.QPushButton("Reassign all symbols…")
        reassign_btn.setToolTip("Replace every symbol, in legend order.")
        reassign_btn.clicked.connect(self._reassign_all_symbols)
        sym_btns.addWidget(fill_btn)
        sym_btns.addWidget(reassign_btn)
        mode_lay.addLayout(sym_btns)

        self.symbols_on_peaks_cb = QtWidgets.QCheckBox("Show symbols over peaks on plot")
        self.symbols_on_peaks_cb.setToolTip(
            "Draw the legend symbol directly on each highlighted peak in the main view.\n"
            "Requires 'Color + symbol' mode. Labels are shifted up automatically\n"
            "to avoid overlapping the symbols.")
        self.symbols_on_peaks_cb.setChecked(
            self._settings.value("legend/symbols_on_peaks", False, type=bool))
        self.symbols_on_peaks_cb.toggled.connect(self._apply)
        mode_lay.addWidget(self.symbols_on_peaks_cb)

        sym_pos_row = QtWidgets.QHBoxLayout()
        sym_pos_row.setContentsMargins(18, 0, 0, 0)

        _sym_h_lbl = QtWidgets.QLabel("Height offset:")
        _sym_h_lbl.setStyleSheet("color: gray;")
        sym_pos_row.addWidget(_sym_h_lbl)
        self.symbol_y_offset_spin = QtWidgets.QDoubleSpinBox()
        self.symbol_y_offset_spin.setRange(0.0, 1e9)       # no practical limit
        self.symbol_y_offset_spin.setSingleStep(1.0)
        self.symbol_y_offset_spin.setDecimals(1)
        self.symbol_y_offset_spin.setSuffix(" %")
        self.symbol_y_offset_spin.setValue(
            100.0 * self._settings.value("legend/symbol_y_offset", 0.0, type=float))
        self.symbol_y_offset_spin.setFixedWidth(96)
        self.symbol_y_offset_spin.setToolTip(
            "Height of the symbols above the peak tip, as a percentage of the peak\n"
            "height (100 % = twice the peak). Works the same on linear and log\n"
            "axes: on a log axis every peak gets the same visual gap.")
        self.symbol_y_offset_spin.valueChanged.connect(self._apply)
        sym_pos_row.addWidget(self.symbol_y_offset_spin)
        sym_pos_row.addSpacing(10)

        _sym_sz_lbl = QtWidgets.QLabel("Size:")
        _sym_sz_lbl.setStyleSheet("color: gray;")
        sym_pos_row.addWidget(_sym_sz_lbl)
        self.symbol_size_spin = QtWidgets.QSpinBox()
        self.symbol_size_spin.setRange(4, 40)
        self.symbol_size_spin.setValue(
            self._settings.value("legend/symbol_size", 10, type=int))
        self.symbol_size_spin.setSuffix(" px")
        self.symbol_size_spin.setFixedWidth(68)
        self.symbol_size_spin.setToolTip("Diameter of the symbols drawn over peaks (pixels).")
        self.symbol_size_spin.valueChanged.connect(self._apply)
        sym_pos_row.addWidget(self.symbol_size_spin)
        sym_pos_row.addStretch()
        mode_lay.addLayout(sym_pos_row)

        _use_sym_init = self._settings.value("legend/use_symbols", False, type=bool)
        self.symbols_on_peaks_cb.setEnabled(_use_sym_init)
        self.symbol_y_offset_spin.setEnabled(_use_sym_init)
        self.symbol_size_spin.setEnabled(_use_sym_init)

        self.mode_color_rb.toggled.connect(self._on_mode_changed)
        lay.addWidget(mode_grp)

        # ── Entries ──
        entries_grp = QtWidgets.QGroupBox("Legend entries")
        entries_grp_lay = QtWidgets.QVBoxLayout(entries_grp)
        hint = QtWidgets.QLabel(
            "One line per peak-list line, kept in sync with the Peaks window: "
            "tick = shown (same tick) · ▲▼ reorder · colour, symbol, label and "
            "column edit the peak list itself.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 10px;")
        entries_grp_lay.addWidget(hint)

        self._entries_container = QtWidgets.QWidget()
        self._entries_layout = QtWidgets.QVBoxLayout(self._entries_container)
        self._entries_layout.setContentsMargins(0, 0, 0, 0)
        self._entries_layout.setSpacing(2)
        entries_grp_lay.addWidget(self._entries_container)
        lay.addWidget(entries_grp)

        # ── Appearance ──
        app_grp = QtWidgets.QGroupBox("Appearance")
        app_form = QtWidgets.QFormLayout(app_grp)
        app_form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        font_row = QtWidgets.QHBoxLayout()
        self.font_combo = QtWidgets.QFontComboBox()
        saved_fam = self._settings.value("legend/font_family", "")
        if saved_fam:
            self.font_combo.setCurrentFont(QtGui.QFont(saved_fam))
        self.font_combo.currentFontChanged.connect(self._apply)
        self.font_pt_spin = QtWidgets.QSpinBox()
        self.font_pt_spin.setRange(6, 48)
        self.font_pt_spin.setValue(
            self._settings.value("legend/font_pt", 11, type=int))
        self.font_pt_spin.setSuffix(" pt")
        self.font_pt_spin.setFixedWidth(68)
        self.font_pt_spin.valueChanged.connect(self._apply)
        font_row.addWidget(self.font_combo, 1)
        font_row.addWidget(self.font_pt_spin)
        app_form.addRow("Font:", font_row)

        self.show_box_cb = QtWidgets.QCheckBox()
        self.show_box_cb.setChecked(
            self._settings.value("legend/show_box", True, type=bool))
        self.show_box_cb.toggled.connect(self._apply)
        app_form.addRow("Show legend box:", self.show_box_cb)

        self.shadow_cb = QtWidgets.QCheckBox()
        self.shadow_cb.setChecked(
            self._settings.value("legend/shadow", False, type=bool))
        self.shadow_cb.toggled.connect(self._apply)
        app_form.addRow("Shadow:", self.shadow_cb)

        self.rounded_cb = QtWidgets.QCheckBox()
        self.rounded_cb.setChecked(
            self._settings.value("legend/rounded", False, type=bool))
        self.rounded_cb.toggled.connect(self._apply)
        app_form.addRow("Rounded corners:", self.rounded_cb)

        self.ncols_spin = QtWidgets.QSpinBox()
        self.ncols_spin.setRange(1, 10)
        self.ncols_spin.setValue(
            self._settings.value("legend/ncols", 1, type=int))
        self.ncols_spin.valueChanged.connect(self._apply)
        app_form.addRow("Columns:", self.ncols_spin)

        self.legend_sym_size_spin = QtWidgets.QSpinBox()
        self.legend_sym_size_spin.setRange(4, 40)
        self.legend_sym_size_spin.setValue(
            self._settings.value("legend/legend_symbol_size", 12, type=int))
        self.legend_sym_size_spin.setSuffix(" px")
        self.legend_sym_size_spin.setFixedWidth(68)
        self.legend_sym_size_spin.setToolTip(
            "Size of the symbols in the legend (Color + symbol mode).\n"
            "The symbols drawn over peaks have their own size above.")
        self.legend_sym_size_spin.valueChanged.connect(self._apply)
        app_form.addRow("Symbol size:", self.legend_sym_size_spin)

        size_row = QtWidgets.QHBoxLayout()
        self._wh_updating = False
        self._wh_ratio = None
        self.width_spin = QtWidgets.QSpinBox()
        self.width_spin.setRange(0, 1200)
        self.width_spin.setValue(self._settings.value("legend/width", 0, type=int))
        self.width_spin.setSuffix(" px")
        self.width_spin.setSpecialValueText("Auto")
        self.width_spin.setFixedWidth(90)
        self.width_spin.valueChanged.connect(self._on_width_changed)
        self.link_wh_btn = QtWidgets.QToolButton()
        self.link_wh_btn.setText("🔗")
        self.link_wh_btn.setCheckable(True)
        self.link_wh_btn.setAutoRaise(True)
        self.link_wh_btn.setToolTip("Keep the width / height ratio")
        self.link_wh_btn.setChecked(self._settings.value("legend/wh_linked", False, type=bool))
        self.link_wh_btn.toggled.connect(self._on_link_toggled)
        self.height_spin = QtWidgets.QSpinBox()
        self.height_spin.setRange(0, 900)
        self.height_spin.setValue(self._settings.value("legend/height", 0, type=int))
        self.height_spin.setSuffix(" px")
        self.height_spin.setSpecialValueText("Auto")
        self.height_spin.setFixedWidth(90)
        self.height_spin.valueChanged.connect(self._on_height_changed)
        size_row.addWidget(self.width_spin)
        size_row.addWidget(self.link_wh_btn)
        size_row.addWidget(self.height_spin)
        size_row.addStretch()
        app_form.addRow("Size W × H:", size_row)

        pos_row = QtWidgets.QHBoxLayout()
        self.reset_pos_btn = QtWidgets.QPushButton("Reset position")
        self.reset_pos_btn.setToolTip(
            "Put the legend back in the top-right corner.\n"
            "Drag the legend on the plot to move it.")
        self.reset_pos_btn.clicked.connect(self._reset_position)
        pos_hint = QtWidgets.QLabel("drag the legend on the plot to move it")
        pos_hint.setStyleSheet("color: gray; font-size: 10px;")
        pos_row.addWidget(self.reset_pos_btn)
        pos_row.addWidget(pos_hint)
        pos_row.addStretch()
        app_form.addRow("Position:", pos_row)

        lay.addWidget(app_grp)
        lay.addStretch()

        # ── Right panel (preview) ──────────────────────────────────────
        right = QtWidgets.QWidget()
        right_lay = QtWidgets.QVBoxLayout(right)
        right_lay.setContentsMargins(4, 4, 4, 4)
        right_lay.setSpacing(4)
        right_lay.addWidget(QtWidgets.QLabel("Preview (as in exports):"))
        self._preview = LegendPreviewWidget()
        right_lay.addWidget(self._preview, 1)
        splitter.addWidget(right)
        splitter.setSizes([620, 380])

    # ── Entries: always the current peak rows ───────────────────────────────

    @staticmethod
    def _key(entry):
        return ("row", id(entry["row"])) if entry.get("row") is not None \
            else ("extra", id(entry.get("extra")))

    def _group_of(self, entry):
        row = entry.get("row")
        return row.get("group_name", "Unclassified") if row is not None else self._EXTRA_GROUP

    def _sync_from_model(self):
        """Show the current legend lines; called after every legend sync.

        Existing line widgets are kept and only updated where something changed;
        adding or deleting a line creates or removes just that widget."""
        try:
            entries = list(_legend_row_entries)
            keys = [(self._key(e), self._group_of(e)) for e in entries]
            if keys != self._keys:
                self._relayout_entry_widgets(entries)
                self._keys = keys
            for w, e in zip(self._entry_widgets, entries):
                w.update_from(e)
            self._update_preview()
            self.reset_pos_btn.setEnabled(not _legend_at_default_position())
        except RuntimeError:
            pass    # widgets already deleted while the dialog closes

    def _relayout_entry_widgets(self, entries):
        old = {w.key: w for w in self._entry_widgets}
        wanted = {self._key(e) for e in entries}
        for k, w in old.items():                    # lines that no longer exist
            if k not in wanted:
                w.hide(); w.setParent(None); w.deleteLater()
        self._selected_keys &= wanted
        while self._entries_layout.count():         # take out, without deleting
            self._entries_layout.takeAt(0)
        use_sym = self.mode_symbol_rb.isChecked()
        headers, widgets = {}, []
        for e in entries:
            gname = self._group_of(e)
            hdr = headers.get(gname)
            if hdr is None:
                hdr = self._group_headers.get(gname) or _LegendGroupHeader(
                    gname, lambda g=gname: self._on_group_toggled(g))
                hdr._children = []
                if gname in self._collapsed:
                    hdr._expanded = False
                    hdr._arrow.setText("▶")
                headers[gname] = hdr
                self._entries_layout.addWidget(hdr)
                hdr.show()
            w = old.get(self._key(e))
            if w is None or w.parent() is None:
                w = LegendEntryWidget(e, show_symbol=use_sym, dialog=self)
            self._entries_layout.addWidget(w)
            hdr.add_child(w)
            w.setVisible(gname not in self._collapsed)
            w.set_selected(w.key in self._selected_keys)
            widgets.append(w)
        for gname, hdr in self._group_headers.items():   # groups that disappeared
            if gname not in headers:
                hdr.hide(); hdr.setParent(None); hdr.deleteLater()
        self._group_headers = headers
        self._entry_widgets = widgets

    # Selecting several lines

    def _on_handle_clicked(self, widget, modifiers):
        """Click: select only this line (or deselect it) · Ctrl: toggle ·
        Shift: range from the last clicked line · Ctrl+Shift: add that range."""
        M = QtCore.Qt.KeyboardModifier
        order = [w.key for w in self._entry_widgets]
        ctrl, shift = bool(modifiers & M.ControlModifier), bool(modifiers & M.ShiftModifier)
        if shift and self._anchor_key in order:
            i, j = order.index(self._anchor_key), order.index(widget.key)
            rng = set(order[min(i, j):max(i, j) + 1])
            self._selected_keys = (self._selected_keys | rng) if ctrl else rng
        elif ctrl:
            self._selected_keys ^= {widget.key}
            self._anchor_key = widget.key
        else:
            self._selected_keys = set() if self._selected_keys == {widget.key} else {widget.key}
            self._anchor_key = widget.key
        for w in self._entry_widgets:
            w.set_selected(w.key in self._selected_keys)

    def _selected_widgets(self, widget):
        """The widgets an edit on `widget` applies to."""
        if widget.key in self._selected_keys and len(self._selected_keys) > 1:
            return [w for w in self._entry_widgets if w.key in self._selected_keys]
        return [widget]

    def _on_group_toggled(self, gname):
        """Collapsing a group only folds this list; the legend is unaffected."""
        hdr = self._group_headers.get(gname)
        if hdr is None:
            return
        if hdr._expanded:
            self._collapsed.discard(gname)
        else:
            self._collapsed.add(gname)

    # ── Settings ────────────────────────────────────────────────────────────

    def _apply(self, *_):
        """Save the settings and redraw the legend (and symbols over peaks)."""
        s = self._settings
        s.setValue("legend/show",        self.show_cb.isChecked())
        s.setValue("legend/show_unticked", self.show_unticked_cb.isChecked())
        s.setValue("legend/font_pt",     self.font_pt_spin.value())
        s.setValue("legend/font_family", self.font_combo.currentFont().family())
        s.setValue("legend/show_box",    self.show_box_cb.isChecked())
        s.setValue("legend/shadow",      self.shadow_cb.isChecked())
        s.setValue("legend/rounded",     self.rounded_cb.isChecked())
        s.setValue("legend/ncols",       self.ncols_spin.value())
        s.setValue("legend/width",       self.width_spin.value())
        s.setValue("legend/height",      self.height_spin.value())
        s.setValue("legend/wh_linked",   self.link_wh_btn.isChecked())
        s.setValue("legend/legend_symbol_size", self.legend_sym_size_spin.value())
        s.setValue("legend/use_symbols",      self.mode_symbol_rb.isChecked())
        s.setValue("legend/symbols_on_peaks", self.symbols_on_peaks_cb.isChecked())
        s.setValue("legend/symbol_y_offset",  self.symbol_y_offset_spin.value() / 100.0)
        s.setValue("legend/symbol_size",      self.symbol_size_spin.value())
        self._spectrum_legend.setLabelTextSize(f"{self.font_pt_spin.value()}pt")
        self._update_preview()
        _rebuild_peak_legend_on_plot()
        _render_peaks_or_full()

    # ── Size W × H (optionally linked) and position ──

    def _natural_size(self):
        item = PeakListLegendItem()
        item.set_data(_legend_visible_entries(export=True),
                      {**_legend_params_from_settings(), "width": 0, "height": 0,
                       "symbol_size": self.legend_sym_size_spin.value(),
                       "use_symbols": self.mode_symbol_rb.isChecked(),
                       "ncols": self.ncols_spin.value(),
                       "font_pt": self.font_pt_spin.value(),
                       "font_family": self.font_combo.currentFont().family()})
        return item.natural_size()

    def _current_ratio(self):
        w, h = self.width_spin.value(), self.height_spin.value()
        if w > 0 and h > 0:
            return w / h
        nw, nh = self._natural_size()
        return nw / max(nh, 1)

    def _on_link_toggled(self, checked):
        self._wh_ratio = self._current_ratio() if checked else None
        self._apply()

    def _on_width_changed(self, value):
        if self.link_wh_btn.isChecked() and not self._wh_updating:
            self._wh_updating = True
            try:
                if value == 0:
                    self.height_spin.setValue(0)
                else:
                    ratio = self._wh_ratio or self._current_ratio()
                    self._wh_ratio = ratio
                    self.height_spin.setValue(max(1, round(value / ratio)))
            finally:
                self._wh_updating = False
        self._apply()

    def _on_height_changed(self, value):
        if self.link_wh_btn.isChecked() and not self._wh_updating:
            self._wh_updating = True
            try:
                if value == 0:
                    self.width_spin.setValue(0)
                else:
                    ratio = self._wh_ratio or self._current_ratio()
                    self._wh_ratio = ratio
                    self.width_spin.setValue(max(1, round(value * ratio)))
            finally:
                self._wh_updating = False
        self._apply()

    def _reset_position(self):
        reset_legend_position()
        self.reset_pos_btn.setEnabled(not _legend_at_default_position())

    def _on_mode_changed(self):
        use_sym = self.mode_symbol_rb.isChecked()
        for w in self._entry_widgets:
            w.set_symbol_visible(use_sym)
        self.symbols_on_peaks_cb.setEnabled(use_sym)
        self.symbol_y_offset_spin.setEnabled(use_sym)
        self.symbol_size_spin.setEnabled(use_sym)
        self._apply()

    def _fill_missing_symbols(self):
        """Symbols for peak lists that have none; existing ones are kept."""
        for r in custom_peak_rows:
            if r["legend_symbol"][0] == "":
                r["legend_symbol"][0] = None       # "None" chosen by hand → fill too
        self.mode_symbol_rb.setChecked(True)       # also saves via _on_mode_changed
        _legend_model_changed()

    def _reassign_all_symbols(self):
        answer = QtWidgets.QMessageBox.question(
            self, "Reassign all symbols",
            "Replace the symbol of every peak list, in legend order?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        _fill_missing_row_symbols(reassign_all=True)
        self.mode_symbol_rb.setChecked(True)
        _legend_model_changed()

    # ── Preview ─────────────────────────────────────────────────────────────

    def _update_preview(self):
        self._preview.set_data(
            entries     = _legend_visible_entries(export=True),
            use_symbols = self.mode_symbol_rb.isChecked(),
            font_pt     = self.font_pt_spin.value(),
            font_family = self.font_combo.currentFont().family(),
            show_box    = self.show_box_cb.isChecked(),
            shadow      = self.shadow_cb.isChecked(),
            rounded     = self.rounded_cb.isChecked(),
            ncols       = self.ncols_spin.value(),
            width       = self.width_spin.value(),
            height      = self.height_spin.value(),
            symbol_size = self.legend_sym_size_spin.value(),
        )

    # ── Saved labels ────────────────────────────────────────────────────────

    def _save_current_labels(self):
        existing: list[dict] = []
        if self._LABELS_FILE.exists():
            try:
                existing = json.loads(
                    self._LABELS_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

        existing_texts = {e["text"] for e in existing}
        for entry in _legend_entries:
            text = entry["label"]
            if text and text not in existing_texts:
                existing.append({
                    "text":   text,
                    "color":  QtGui.QColor(entry["color"]).name(),
                    "symbol": entry.get("symbol") or "",
                })
                existing_texts.add(text)

        existing.sort(key=lambda e: e.get("text", "").lower())
        self._LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
        self._LABELS_FILE.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8")

    def _open_import_dialog(self):
        """Auto-save current labels, then open the import picker."""
        self._save_current_labels()   # make sure current labels appear in the list
        dlg = SavedLabelsImportDialog(self, self._LABELS_FILE)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        rows_by_label = {}
        for r in custom_peak_rows:
            rows_by_label.setdefault(r["label_input"].text().strip(), []).append(r)
        for entry in dlg.get_selected():
            label = entry.get("text", "").strip()
            sym   = entry.get("symbol") or None
            if not label:
                continue
            if label in rows_by_label:
                if sym in MARKER_SYMBOL_NAMES:
                    for r in rows_by_label[label]:
                        r["legend_symbol"][0] = sym
            elif not any(x["label"] == label for x in _legend_extra_entries):
                _legend_extra_entries.append({
                    "label": label, "color": QtGui.QColor(entry.get("color", "#888888")),
                    "symbol": sym, "col": 1})
        _save_legend_entries()
        _legend_model_changed()


# ─────────────────────────────────────────────
#  View / remaining action connections
# ─────────────────────────────────────────────
dyn_scale_action.toggled.connect(lambda _: render_plot())
subtract_action.toggled.connect(lambda _: render_plot())
subtract_dyn_action.toggled.connect(lambda _: render_plot())
highlight_slider.valueChanged.connect(lambda _: _render_peaks_or_full())
overlay_opacity_slider.valueChanged.connect(lambda _: render_plot())

# Apply saved legend font to spectrum legend on startup
_startup_legend_pt = settings.value("legend/font_pt", 11, type=int)
legend.setLabelTextSize(f"{_startup_legend_pt}pt")

# Load legend-only labels and the pre-3.2 symbol map, derive the legend from
# the peak rows restored at startup, then draw it
splash_step(80, "Preparing the legend…")
_load_legend_entries()
_auto_sync_legend_entries()
QtCore.QTimer.singleShot(0, _rebuild_peak_legend_on_plot)
main_toggle.stateChanged.connect(lambda _: render_plot())
peak_labels_toggle.stateChanged.connect(lambda _: render_plot())
peak_masses_toggle.stateChanged.connect(lambda _: render_plot())
auto_peaks_toggle.stateChanged.connect(lambda _: render_plot())
mass_threshold_spin.valueChanged.connect(lambda _: _render_peaks_or_full())
peak_masses_all_toggle.stateChanged.connect(lambda _: render_plot())
threshold_mode_combo.currentIndexChanged.connect(lambda _: (_clear_auto_peaks_cache(), render_plot()))
show_integers_toggle.stateChanged.connect(lambda v: (
    settings.setValue("show_integers", bool(v)), render_plot()))

_legend_params_dialog = None

def _open_legend_params():
    global _legend_params_dialog
    if _legend_params_dialog is not None:
        _legend_params_dialog.raise_()
        _legend_params_dialog.activateWindow()
        return
    _legend_params_dialog = LegendParametersDialog(
        parent          = main_win,
        peak_rows       = custom_peak_rows,
        spectrum_legend = legend,
        plot_ref        = plot,
        app_settings    = settings,
    )
    _legend_params_dialog.setAttribute(
        QtCore.Qt.WidgetAttribute.WA_DeleteOnClose)
    _legend_params_dialog.destroyed.connect(
        lambda: globals().update(_legend_params_dialog=None))
    _legend_params_dialog.show()

legend_params_action.triggered.connect(_open_legend_params)

# Restore saved mode
_saved_thr_mode = settings.value("threshold_mode", "% of max intensity")
_idx = threshold_mode_combo.findText(_saved_thr_mode)
if _idx >= 0:
    threshold_mode_combo.setCurrentIndex(_idx)
threshold_mode_combo.currentIndexChanged.connect(
    lambda _: settings.setValue("threshold_mode", threshold_mode_combo.currentText()))

def on_lock_axes_toggled(checked):
    global locked_x_range, locked_y_range
    if checked:
        locked_x_range = plot.vb.viewRange()[0]
        locked_y_range = plot.vb.viewRange()[1]
    else:
        locked_x_range = None; locked_y_range = None

lock_axes_action.toggled.connect(on_lock_axes_toggled)

_stacked_y_guard = False

def _stacked_y_target():
    """Return the target (ymin, ymax) for the active stacked Y option, or None."""
    if _log_y:
        return None
    if _stacked_fit_y:
        if not _stacked_spectra_data:
            return None
        lo = _view_mz_lower_spin.value()
        data_max = max(
            float(iv[mz >= lo].max()) if (mz >= lo).any() else 0.0
            for _, mz, iv in _stacked_spectra_data
        )
        return (0.0, max(data_max, 1e-6) * 1.05)
    if _stacked_lock_y:
        return (0.0, _stacked_lock_ymax)
    return None

def _enforce_stacked_y(vb, ranges):
    """Called on every range change in a stacked sub-plot."""
    global _stacked_y_guard
    if _stacked_y_guard:
        return
    target = _stacked_y_target()
    if target is None:
        return
    ymin, ymax = ranges[1]
    t_min, t_max = target
    if abs(ymin - t_min) > 1e-6 or abs(ymax - t_max) > 1e-4:
        _stacked_y_guard = True
        vb.setYRange(t_min, t_max, padding=0)
        _stacked_y_guard = False

def _apply_stacked_y():
    """Immediately apply the current Y constraint to all stacked sub-plots."""
    target = _stacked_y_target()
    if target is None:
        return
    for sub in _stacked_sub_plots:
        sub.vb.setYRange(target[0], target[1], padding=0)

def _on_stacked_fit_y(v):
    global _stacked_fit_y
    _stacked_fit_y = v
    settings.setValue("stacked_fit_y", v)
    if v and _stacked_lock_y:
        stacked_lock_y_action.setChecked(False)
    _apply_stacked_y()

def _on_stacked_lock_y(v):
    global _stacked_lock_y
    _stacked_lock_y = v
    settings.setValue("stacked_lock_y", v)
    _stacked_lock_ymax_spin.setEnabled(v)
    if v and _stacked_fit_y:
        stacked_fit_y_action.setChecked(False)
    _apply_stacked_y()

stacked_fit_y_action.toggled.connect(_on_stacked_fit_y)
stacked_lock_y_action.toggled.connect(_on_stacked_lock_y)
_stacked_lock_ymax_spin.valueChanged.connect(lambda v: (
    globals().update({"_stacked_lock_ymax": v}),
    settings.setValue("stacked_lock_ymax", v),
    _apply_stacked_y()))

# ─────────────────────────────────────────────
#  Ctrl+scroll / Ctrl+arrow → nudge overlay opacity
# ─────────────────────────────────────────────
class CtrlScrollFilter(QtCore.QObject):
    def eventFilter(self, obj, event):
        t = event.type()
        if t not in (QtCore.QEvent.Type.Wheel, QtCore.QEvent.Type.KeyPress):
            return False
        try:
            is_wheel = (t == QtCore.QEvent.Type.Wheel and
                        event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier)
            is_key   = (t == QtCore.QEvent.Type.KeyPress and
                        event.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier and
                        event.key() in (QtCore.Qt.Key.Key_Up, QtCore.Qt.Key.Key_Down))
            if not (is_wheel or is_key):
                return False

            if is_wheel:
                go_next = event.angleDelta().y() < 0
            else:
                go_next = event.key() == QtCore.Qt.Key.Key_Down

            if _stacked_lock_y:
                step = _stacked_lock_ymax_spin.singleStep()
                _stacked_lock_ymax_spin.setValue(
                    _stacked_lock_ymax_spin.value() + (-step if go_next else step))
                return True

            _cycle_overlay(go_next)
            return True

        except RuntimeError:
            pass
        return False


def _cycle_overlay(go_next):
    """Toggle overlays one at a time: scroll down = next, scroll up = previous."""
    if not overlay_list:
        return

    # Find currently active overlay index (-1 if none)
    current = -1
    for i, ov in enumerate(overlay_list):
        if ov["toggle"].isChecked():
            current = i
            break

    # Turn off current
    if current >= 0:
        overlay_list[current]["toggle"].setChecked(False)

    # Pick next/previous index with wraparound
    n = len(overlay_list)
    if go_next:
        nxt = (current + 1) % n
    else:
        nxt = (current - 1) % n

    overlay_list[nxt]["toggle"].setChecked(True)

ctrl_filter = CtrlScrollFilter(main_win)
app.installEventFilter(ctrl_filter)

# ─────────────────────────────────────────────
#  Plot export helpers
# ─────────────────────────────────────────────
# Every export format is made from the plot exactly as it is shown in the
# window, like Copy Plot to Clipboard (plot_widget.grab()): same size, zoom,
# fonts, labels, legend and minimap.  Batch export loads each file and uses
# the same functions.

def _for_capture(fn):
    """Run fn() with the window's browsing aids removed: the minimap is hidden
    and the peak-list legend switches to export mode (only ticked, shown
    entries; no greying, dimming or hover marks). No events are processed in
    between, so the window does not flicker."""
    global _legend_export_mode
    shown = _minimap.isVisible()
    _minimap.hide()
    _legend_export_mode = True
    _rebuild_peak_legend_on_plot()
    try:
        return fn()
    finally:
        _legend_export_mode = False
        _rebuild_peak_legend_on_plot()
        if shown:
            _minimap.show()

def _grab_plot_view():
    """The plot as shown in the window, without the browsing aids."""
    return _for_capture(plot_widget.grab)

def _render_plot_view(painter):
    """Paint plot_widget as on screen (without the browsing aids) for the
    vector exports; one widget pixel = one painter unit.

    Scatter symbols (symbols over peaks, auto-detected peaks, …) are normally
    stamped from pyqtgraph's bitmap cache, which would end up as embedded
    images; with the cache off pyqtgraph draws them as vector shapes, at the
    same positions and sizes."""
    scatters = [it for it in plot_widget.scene().items()
                if isinstance(it, pg.ScatterPlotItem) and it.opts.get("useCache", True)]
    for it in scatters:
        it.opts["useCache"] = False
    try:
        _for_capture(lambda: plot_widget.render(painter))
    finally:
        for it in scatters:
            it.opts["useCache"] = True

def save_plot_png(path):
    """The same image as Copy Plot to Clipboard, saved as PNG."""
    if not _grab_plot_view().save(path, "PNG"):
        raise OSError(f"Could not write {path}")

def save_plot_svg(path):
    """Vector copy of the plot as shown in the window."""
    from PyQt6 import QtSvg
    size = plot_widget.size()
    gen = QtSvg.QSvgGenerator()
    gen.setFileName(path)
    gen.setSize(size)
    gen.setViewBox(QtCore.QRect(QtCore.QPoint(0, 0), size))
    gen.setResolution(96)
    gen.setTitle(os.path.splitext(os.path.basename(path))[0])
    gen.setDescription(f"Droplet {APP_VERSION}")
    painter = QtGui.QPainter(gen)
    try:
        _render_plot_view(painter)
    finally:
        painter.end()

def save_plot_pdf(path):
    """Vector PDF of the plot as shown in the window, on a page of the same shape."""
    size = plot_widget.size()
    writer = QtGui.QPdfWriter(path)
    writer.setTitle(os.path.splitext(os.path.basename(path))[0])
    writer.setCreator(f"Droplet {APP_VERSION}")
    # At 96 dpi one widget pixel is one PDF device unit, so the widget is
    # rendered 1:1: scaling the painter would make the plot render cropped.
    writer.setResolution(96)
    page = QtGui.QPageSize(QtCore.QSizeF(size.width() * 72 / 96, size.height() * 72 / 96),
                           QtGui.QPageSize.Unit.Point, "Droplet plot",
                           QtGui.QPageSize.SizeMatchPolicy.ExactMatch)
    writer.setPageLayout(QtGui.QPageLayout(page, QtGui.QPageLayout.Orientation.Portrait,
                                           QtCore.QMarginsF(0, 0, 0, 0)))
    painter = QtGui.QPainter(writer)
    try:
        _render_plot_view(painter)
    finally:
        painter.end()

def save_plot_pgf(path):
    """PGF picture of the plot as shown in the window, for LaTeX \\input{}."""
    from droplet_pkg.io.pgf_writer import PgfPaintDevice
    size = plot_widget.size()
    stem = os.path.splitext(os.path.basename(path))[0]
    device = PgfPaintDevice(size.width(), size.height(), image_prefix=stem)
    painter = QtGui.QPainter(device)
    try:
        _render_plot_view(painter)
    finally:
        painter.end()
    device.save(path, comment=f"Droplet {APP_VERSION} — plot as shown in the window")

_PLOT_SAVERS = {"PNG": save_plot_png, "PDF": save_plot_pdf, "SVG": save_plot_svg,
                "PGF": save_plot_pgf}

def _load_file_and_render_now(fpath, timeout_ms=120_000):
    """plot_file() processes the spectrum in a worker thread and renders on an
    80 ms timer; wait for both so the window really shows `fpath`."""
    previous = _active_worker
    plot_file(fpath)
    worker = _active_worker
    if worker is not None and worker is not previous:
        clock = QtCore.QElapsedTimer(); clock.start()
        while not worker.isFinished() and clock.elapsed() < timeout_ms:
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.ProcessEventsFlag.AllEvents, 50)
            worker.wait(20)
    # Deliver the worker's queued result (sets df), then render immediately.
    QtWidgets.QApplication.processEvents()
    _render_timer.stop()
    _do_render_plot()
    QtWidgets.QApplication.processEvents()

def _export_plot(fmt, filter_text):
    ext = "." + fmt.lower()
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, f"Export Plot as {fmt}", _get_dialog_dir("export_plot"), filter_text)
    if not path: return
    _set_dialog_dir("export_plot", path)
    if not path.lower().endswith(ext): path += ext
    try:
        _PLOT_SAVERS[fmt](path)
    except Exception as e:
        QtWidgets.QMessageBox.warning(main_win, "Export failed",
                                      f"Could not export the plot:\n{e}")
        return
    status_bar.showMessage(f"Plot exported to {path}", 5000)

def export_plot_png(): _export_plot("PNG", "PNG Images (*.png)")
def export_plot_svg(): _export_plot("SVG", "SVG Files (*.svg)")
def export_plot_pdf(): _export_plot("PDF", "PDF Files (*.pdf)")
def export_plot_pgf(): _export_plot("PGF", "PGF for LaTeX (*.pgf)")

def batch_export_plots():
    """Export every file visible under the current mode/dt filters, one by one,
    exactly as each looks in the window (same exporters as the single exports)."""
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    if not files:
        QtWidgets.QMessageBox.warning(
            main_win, "No files",
            "No spectrum files match the current mode and dt filters.")
        return

    fmt, ok = QtWidgets.QInputDialog.getItem(
        main_win, "Batch Export — Format", "Format:", list(_PLOT_SAVERS), 0, False)
    if not ok:
        return

    out_folder = QtWidgets.QFileDialog.getExistingDirectory(
        main_win, "Select Output Folder", base_dir or "")
    if not out_folder:
        return

    # Save current view range and active file so we can restore them
    x_range = list(plot.vb.viewRange()[0])
    y_range = list(plot.vb.viewRange()[1])
    original_path = combo.currentData() or combo.currentText()

    progress = QtWidgets.QProgressDialog(
        "Scanning files…", "Cancel", 0, len(files), main_win)
    progress.setWindowTitle("Batch Export")
    progress.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)
    progress.setValue(0)
    QtWidgets.QApplication.processEvents()

    # ── Pre-scan: find global intensity max so no file gets Y-cropped ────────
    # Skipped when dynamic scale is on (all files are already normalised to 1).
    global_y_top = None
    if not dyn_scale_action.isChecked():
        raw_global_max = 0.0
        for j, fpath_scan in enumerate(files):
            if progress.wasCanceled():
                progress.setValue(len(files))
                return
            progress.setLabelText(f"Scanning ({j+1}/{len(files)})…")
            QtWidgets.QApplication.processEvents()
            try:
                data = safe_read(fpath_scan, sep=get_sep_from_combo())
                if data is not None and len(data) > 0:
                    raw_global_max = max(raw_global_max, float(data['intensity'].max()))
            except Exception:
                pass
        if raw_global_max > 0:
            if _log_y:
                # view Y range is in log space when log mode is active
                global_y_top = np.log10(raw_global_max) + 0.1
            else:
                global_y_top = raw_global_max * 1.05

    n_ok = 0
    errors = []

    for i, fpath in enumerate(files):
        if progress.wasCanceled():
            break
        stem = os.path.splitext(os.path.basename(fpath))[0]
        progress.setLabelText(f"({i+1}/{len(files)})  {stem}")
        progress.setValue(i)
        main_win.setWindowTitle(f"Droplet  {APP_VERSION}  —  Exporting {i+1}/{len(files)}: {stem}")
        QtWidgets.QApplication.processEvents()

        try:
            _load_file_and_render_now(fpath)

            # X range: keep user's zoom; Y range: global max so no file is cropped
            y_top = global_y_top if global_y_top is not None else y_range[1]
            plot.vb.setXRange(x_range[0], x_range[1], padding=0)
            plot.vb.setYRange(y_range[0], y_top, padding=0)
            QtWidgets.QApplication.processEvents()

            _PLOT_SAVERS[fmt](os.path.join(out_folder, f"{stem}.{fmt.lower()}"))
            n_ok += 1
        except Exception as e:
            errors.append(f"{stem}: {e}")

    progress.setValue(len(files))

    # Restore the original file, zoom, and window title
    main_win.setWindowTitle(f"Droplet  {APP_VERSION}")
    if original_path and os.path.exists(str(original_path)):
        _load_file_and_render_now(original_path)
        plot.vb.setXRange(x_range[0], x_range[1], padding=0)
        plot.vb.setYRange(y_range[0], y_range[1], padding=0)

    msg = f"Done. {n_ok}/{len(files)} plot(s) exported to:\n{out_folder}"
    if errors:
        msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:15])
    QtWidgets.QMessageBox.information(main_win, "Batch Export Complete", msg)

def export_peaks_csv():
    if not highlighted_ranges:
        QtWidgets.QMessageBox.information(
            main_win, "No peaks", "No highlighted peaks to export.\n"
                                  "Enable peak rows in the Peaks window first.")
        return
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Export Peak Data as CSV", _get_dialog_dir("export_csv"), "CSV Files (*.csv)")
    if path: _set_dialog_dir("export_csv", path)
    if not path: return
    if not path.lower().endswith(".csv"): path += ".csv"
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Label", "m/z", "Intensity"])
        for entry in highlighted_ranges:
            mz_min, mz_max, label, peak_mz, peak_int = entry
            writer.writerow([label, f"{peak_mz:.4f}", f"{peak_int:.6g}"])
    QtWidgets.QMessageBox.information(main_win, "Exported",
        f"Peak data saved to:\n{path}")

def print_plot():
    pixmap = plot_widget.grab()
    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
    # Default to landscape to match the plot shape
    printer.setPageOrientation(QtGui.QPageLayout.Orientation.Landscape)
    dlg = QtPrintSupport.QPrintDialog(printer, main_win)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
    painter = QtGui.QPainter(printer)
    page_rect = painter.viewport()
    img = pixmap.toImage()
    img_size = img.size().scaled(page_rect.size(),
                                 QtCore.Qt.AspectRatioMode.KeepAspectRatio)
    x_off = (page_rect.width()  - img_size.width())  // 2
    y_off = (page_rect.height() - img_size.height()) // 2
    painter.drawImage(QtCore.QRect(x_off, y_off, img_size.width(), img_size.height()), img)
    painter.end()

def copy_plot_to_clipboard():
    pixmap = _grab_plot_view()
    QtWidgets.QApplication.clipboard().setPixmap(pixmap)

def export_peaks_csv():
    if not highlighted_ranges:
        QtWidgets.QMessageBox.information(
            main_win, "No peaks", "No highlighted peaks to export.\n"
                                  "Enable peak rows in the Peaks window first.")
        return
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Export Peak Data as CSV", "", "CSV Files (*.csv)")
    if not path: return
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Label", "m/z", "Intensity"])
        for entry in highlighted_ranges:
            mz_min, mz_max, label, peak_mz, peak_int = entry
            writer.writerow([label, f"{peak_mz:.4f}", f"{peak_int:.6g}"])
    QtWidgets.QMessageBox.information(main_win, "Exported",
        f"Peak data saved to:\n{path}")

def print_plot():
    pixmap = plot_widget.grab()
    printer = QtPrintSupport.QPrinter(QtPrintSupport.QPrinter.PrinterMode.HighResolution)
    dlg = QtPrintSupport.QPrintDialog(printer, main_win)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted: return
    painter = QtGui.QPainter(printer)
    rect = painter.viewport()
    img  = pixmap.toImage()
    img  = img.scaled(rect.size(),
                      QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                      QtCore.Qt.TransformationMode.SmoothTransformation)
    painter.drawImage(0, 0, img)
    painter.end()

try:
    from PyQt6 import QtPrintSupport
    print_action.triggered.connect(print_plot)
except ImportError:
    print_action.setEnabled(False)
    print_action.setToolTip("QtPrintSupport not available")

export_png_action.triggered.connect(export_plot_png)
export_svg_action.triggered.connect(export_plot_svg)
export_pdf_action.triggered.connect(export_plot_pdf)
export_pgf_action.triggered.connect(export_plot_pgf)
batch_export_plots_action.triggered.connect(batch_export_plots)
copy_plot_action.triggered.connect(copy_plot_to_clipboard)
export_peaks_csv_action.triggered.connect(export_peaks_csv)

# ─────────────────────────────────────────────
#  Overlay help
# ─────────────────────────────────────────────
def show_overlay_help():
    QtWidgets.QMessageBox.information(main_win, "Files & Overlays",
        "FILES\n"
        "  Mode: neg / pos polarity filter.\n"
        "  Sep:  file column separator (auto-detect works for most).\n"
        "  ↺:    reload current file from disk (F5).\n\n"
        "OVERLAYS\n"
        "  + Add Overlay to load additional spectra.\n"
        "  Each has its own mode and file selector.\n"
        "  Overlay opacity slider: sets transparency of all overlays at once.\n"
        "  Ctrl+Scroll / Ctrl+↑↓ nudges overlay opacity up/down by 5 %.\n"
        "  Processed spectra (baseline / recalibrated) appear as overlays\n"
        "  with a 💾 Save button to export them.\n\n"
        "This section can be collapsed by clicking the ▾ header.")

ov_help_btn.clicked.connect(show_overlay_help)

# ─────────────────────────────────────────────
#  Signal connections
# ─────────────────────────────────────────────
combo.currentIndexChanged.connect(lambda _: plot_file(combo.currentData() or combo.currentText()))

def _on_filter_changed():
    pol = polarity_combo.currentText()
    _refresh_dt_combo()            # update dt values for this polarity
    dt    = dt_combo.currentText()
    files = get_txt_files_filtered(pol, dt)
    _populate_main_combo(files if files else [])
    if combo.count() > 0 and combo.currentData():
        plot_file(combo.currentData())

def _guard_polarity_change():
    """Intercept polarity changes to warn about unsaved confirmation data."""
    pol   = polarity_combo.currentText()
    _cp   = _confirmation_panel_ref[0]
    if _cp is not None and pol in ("neg", "pos") and pol != _cp._current_mode:
        if not _cp.check_dirty_before_mode_change(pol):
            # Revert the combo to the current panel mode
            polarity_combo.blockSignals(True)
            prev_idx = polarity_combo.findText(_cp._current_mode)
            if prev_idx >= 0:
                polarity_combo.setCurrentIndex(prev_idx)
            polarity_combo.blockSignals(False)
            return
        _cp.set_mode(pol)
    _on_filter_changed()

polarity_combo.currentIndexChanged.connect(lambda _: _guard_polarity_change())
dt_combo.currentIndexChanged.connect(lambda _: _on_filter_changed())
peaks_action.triggered.connect(open_peaks_window)
import_action.triggered.connect(import_peak_list)
export_action.triggered.connect(export_peak_list)
save_pl_action.triggered.connect(save_peak_list)
open_folder_action.triggered.connect(lambda: open_folder())
open_files_action.triggered.connect(open_individual_files)
refresh_action.triggered.connect(refresh_current)
refresh_btn.clicked.connect(refresh_current)
dark_action.triggered.connect(lambda: set_display_mode("dark"))
bright_action.triggered.connect(lambda: set_display_mode("bright"))


fmt_combo.currentIndexChanged.connect(
    lambda _: plot_file(combo.currentData() or combo.currentText()))

def _null_anchor(item):
    """Prevent GraphicsWidgetAnchor.__geometryChanged from crashing at shutdown.

    Sets the internal __parent reference to None so the guard at the top of
    __geometryChanged returns immediately instead of calling boundingRect() on
    an already-deleted C++ ViewBox object.
    Also tries to disconnect the geometryChanged signal for a clean teardown.
    """
    if item is None:
        return
    try:
        parent = item._GraphicsWidgetAnchor__parent
        if parent is not None:
            try:
                parent.geometryChanged.disconnect(
                    item._GraphicsWidgetAnchor__geometryChanged)
            except Exception:
                pass
        item._GraphicsWidgetAnchor__parent = None
    except AttributeError:
        pass
    try:
        item.setParentItem(None)
    except Exception:
        pass

def cleanup():
    global _peak_legend
    settings.setValue("base_dir", base_dir)
    save_session_state()
    settings.sync()   # flush all QSettings to disk before Qt tears down
    # Detach every GraphicsWidgetAnchor from its ViewBox parent before Qt
    # destroys the C++ ViewBox objects.  Without this, __geometryChanged fires
    # on an already-deleted ViewBox → RuntimeError at shutdown.
    # Items that need detaching:
    #   • hover_label  – LabelItem on plot.vb
    #   • legend       – pyqtgraph LegendItem on plot.vb
    #   • _area_result_label – LabelItem on plot.vb (may be None)
    #   • sub_label per stacked subplot – LabelItems on sub.vb
    #   • _peak_legend – PeakListLegendItem (handled below)
    _null_anchor(hover_label)
    _null_anchor(legend)
    _null_anchor(_area_result_label)
    # Sub-labels inside stacked sub-plots (present if closed while in stacked mode)
    for _sub in _stacked_sub_plots:
        try:
            for _child in list(_sub.vb.childItems()):
                if hasattr(_child, '_GraphicsWidgetAnchor__parent'):
                    _null_anchor(_child)
        except Exception:
            pass
    if _peak_legend is not None:
        for vb in ([plot.vb] + [s.vb for s in _stacked_sub_plots]):
            try:
                vb.removeItem(_peak_legend)
            except Exception:
                pass
        _null_anchor(_peak_legend)
        _peak_legend = None
    _clear_peak_symbol_scatters()

app.aboutToQuit.connect(cleanup)
def _save_project_and_toast():
    save_project()
    if _current_project_path[0]:
        _show_toast(f"Project saved: {os.path.basename(_current_project_path[0])}")

def _save_project_as_and_toast():
    save_project_as()
    if _current_project_path[0]:
        _show_toast(f"Project saved as: {os.path.basename(_current_project_path[0])}")

save_project_action.triggered.connect(_save_project_and_toast)
save_project_as_action.triggered.connect(_save_project_as_and_toast)
open_project_action.triggered.connect(open_project)


# ─────────────────────────────────────────────
#  Help system
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
#  Interactive Tutorial Overlay
# ─────────────────────────────────────────────

# TutorialOverlay — defined in droplet/ui/windows/tutorial.py
# ─────────────────────────────────────────────
#  Help topic dialogs
# ─────────────────────────────────────────────
def _help_dialog(title, text):
    dlg = QtWidgets.QDialog(main_win)
    dlg.setWindowTitle(f"Help - {title}")
    dlg.resize(500, 380)
    layout = QtWidgets.QVBoxLayout(dlg)
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    container = QtWidgets.QWidget()
    inner = QtWidgets.QVBoxLayout(container)
    lbl = QtWidgets.QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet("font-size: 11px;")
    lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
    lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)
    inner.addWidget(lbl)
    inner.addStretch()
    scroll.setWidget(container)
    layout.addWidget(scroll)
    btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
    btns.rejected.connect(dlg.accept)
    layout.addWidget(btns)
    dlg.exec()

def show_help_files():
    _help_dialog("Files & Overlays",
        "<b>Opening files</b><br>"
        "• <i>File → Open Folder</i> - loads all spectrum files (.txt .csv .dat .asc .tsv) from a directory.<br>"
        "• <i>File → Open Individual Files</i> - pick specific files regardless of folder.<br>"
        "• Drag-and-drop files directly onto the main window.<br>"
        "• Recent folders and files are listed under <i>File → Recent Folders / Recent Files</i>.<br><br>"
        "<b>Polarity filter</b><br>"
        "The <i>neg / pos / All</i> dropdown filters the file list by polarity tag in the filename. "
        "'All' shows all files regardless of polarity. "
        "When no files match, the selector shows \"No file in X mode\". "
        "The software auto-switches to 'pos' if only positive-mode files are found.<br><br>"
        "<b>dt filter</b><br>"
        "The <i>dt:</i> dropdown filters files by the dt value encoded in the filename "
        "(e.g. <i>_dt071</i>). Only dt values present in files matching the current polarity "
        "are shown. Select 'All' to disable filtering.<br><br>"
        "<b>Colour picker</b><br>"
        "The small colour square next to 'Main file' sets the colour of the main spectrum. "
        "Each overlay also has its own colour square.<br><br>"
        "<b>Separator</b><br>"
        "The <i>Sep</i> dropdown selects the column separator. Auto-detect works for most files.<br><br>"
        "<b>Overlays</b><br>"
        "• Click <i>+ Add Overlay</i> to add a spectrum on top of the main one.<br>"
        "• Each overlay has its own polarity filter, dt filter, and file selector.<br>"
        "• The checkbox enables/disables the overlay.<br>"
        "• <i>Ctrl+Scroll</i> or <i>Ctrl+↑↓</i> cycles through overlays one at a time.<br>"
        "• The <i>Overlay Opacity</i> slider controls transparency of all overlays.<br>"
        "• Processed spectra (baseline / recalibrated) appear as overlays with a 💾 Save button.<br>"
        "• The Files & Overlays section can be collapsed by clicking the ▾ header.")

def show_help_view():
    _help_dialog("View & Navigation",
        "• <i>Pan / Navigate</i> (default) - click and drag to pan; scroll to zoom.<br>"
        "• <i>Zoom Box [Z]</i> - draw a rectangle to zoom into a region. Press Z to toggle.<br>"
        "• Right-click on the plot for additional axis and view options.<br><br>"
        "<b>View options</b><br>"
        "• <i>Cross-lines</i> - crosshair cursor tracking your mouse, shows m/z and intensity.<br>"
        "• <i>Dynamic Scale</i> - normalises all spectra to the same peak height for easy comparison. "
        "Uses sigma clipping internally to suppress baseline noise during normalisation.<br>"
        "• <i>Lock Axes</i> - freezes the current zoom range when switching files.<br>"
        "• <i>Subtract Overlay</i> - displays the difference between main and first overlay spectrum.<br>"
        "• <i>Dynamic Subtraction</i> - normalises both before subtracting.<br>"
        "• <i>Show m/z at Cursor</i> - displays the current m/z value in a floating label "
        "next to the mouse cursor while hovering over the plot.<br><br>"
        "<b>Stacked Spectra Mode</b><br>"
        "Toggle in <i>View</i> menu or the <i>Stacked</i> checkbox in the toolbar. "
        "Each active spectrum is shown in its own row with a shared X axis, normalised to 0–1 "
        "so spectra are directly comparable in height. X axes are linked: panning or zooming "
        "one row affects all. Each spectrum is drawn in its own chosen colour.<br>"
        "<i>Log Y</i> (checkbox beside Stacked) switches all rows to a logarithmic Y scale. "
        "When σ Clip is also enabled, the noise floor from the slider is applied before log scaling. "
        "Without σ Clip, only a minimal floor (1e-6) is added to keep log display safe.<br>"
        "The Stacked state and Log Y state are remembered between sessions.<br><br>"
        "<b>Go to last zoom</b><br>"
        "Right-click the plot and choose <i>Go to last zoom</i> to step back through your zoom history.<br><br>"
        "<b>Display theme</b><br>"
        "Switch between Dark and Bright background in the Display menu.<br><br>"
        "<b>Sliders</b><br>"
        "• <i>Highlight intensity</i> - controls the colour saturation of highlighted peaks. "
        "Lower values blend toward white (bright) or black (dark).<br>"
        "• <i>Overlay opacity</i> - transparency of all overlay spectra (5–100 %). "
        "Also controllable via Ctrl+Scroll or Ctrl+↑↓.")

def show_help_peaks():
    _help_dialog("Peak Lists",
        "<b>Opening the peak list</b><br>"
        "Use <i>Peaks → Open Peaks List</i> or Ctrl+P.<br><br>"
        "<b>Each row defines a set of m/z values to highlight:</b><br>"
        "• ⠿ - drag handle to reorder rows up/down<br>"
        "• ☐ - enable/disable the row<br>"
        "• ■ - colour picker<br>"
        "• ≡ - toggle between <i>manual entry</i> (comma-separated m/z values) "
        "and <i>range mode</i> (start → end, step)<br>"
        "• Label field - text shown when hovering over a highlighted peak<br>"
        "• ⌒ - isotopic envelope toggle: when active, connects the tops of the peaks in this row "
        "with a dashed line. Useful for checking whether a cluster of peaks forms a single "
        "smooth distribution (isotopic envelope) or has multiple contributions.<br>"
        "• × - remove the row<br><br>"
        "<b>Label display options</b><br>"
        "• <i>Show Labels</i> - draws the row label above each highlighted peak.<br>"
        "• <i>Show highlighted masses</i> - draws the m/z value above every manually highlighted peak.<br>"
        "• <i>Show masses above threshold</i> - labels every peak that exceeds the threshold with its "
        "m/z value. Works independently of <i>Auto-detect peaks</i> - you do not need to run "
        "auto-detection first.<br>"
        "• <i>Auto-detect peaks</i> - marks peaks above the threshold with red triangles.<br><br>"
        "<b>Threshold controls</b><br>"
        "The threshold value and mode affect both <i>Show masses above threshold</i> and "
        "<i>Auto-detect peaks</i>.<br>"
        "• <i>% of max intensity</i> mode - the spinbox value is a percentage of the spectrum's "
        "highest peak. For example, 5 % means only peaks at least 5 % as tall as the tallest peak "
        "are shown or detected.<br>"
        "• <i>SNR</i> mode - the spinbox value is a minimum signal-to-noise ratio. Noise is estimated "
        "locally as the 25th percentile of intensity in a ±5 Da window around each candidate peak. "
        "A value of 3 means a peak must be at least 3× the local noise floor to be included.<br><br>"
        "<b>Picking peaks by clicking</b><br>"
        "Enable <i>Pick Peaks Mode</i> (Peaks menu or P key).<br>"
        "• Single-click → adds the nearest m/z to the selected row.<br>"
        "• Double-click → removes the nearest m/z from the selected row.<br><br>"
        "<b>Undo / Redo</b><br>"
        "Ctrl+Z to undo, Ctrl+Y or Ctrl+Shift+Z to redo text edits in the peak list.<br><br>"
        "<b>Import / Export</b><br>"
        "Peak lists are saved as JSON files and include range definitions.")

def show_help_analysis():
    _help_dialog("Processing & Analysis",
        "<b>Baseline Correction</b>  (Processing menu)<br>"
        "Removes the background signal from the spectrum.<br>"
        "• <i>airPLS</i> - adaptive iterative reweighted least squares (LILBID standard).<br>"
        "• <i>SNIP</i> - statistics-sensitive non-linear iterative peak-clipping (atomic MS classic).<br>"
        "The corrected spectrum appears as an overlay with a 💾 Save button. "
        "You can choose to apply to the main spectrum or any active overlay.<br><br>"
        "<b>Auto-Recalibration</b>  (Processing menu)<br>"
        "Detects peaks automatically using a continuous wavelet transform, matches them to known "
        "LILBID calibrant ions, and fits a quadratic TOF polynomial. A review dialog shows the "
        "calibration pairs and flags uncertain results for manual inspection before applying. "
        "Residuals (original m/z vs corrected) are saved as CSV files in a "
        "<i>Residuals dd.mm.yyyy - hh.mm.ss/</i> subfolder inside the output folder.<br><br>"
        "<b>Manual Recalibration</b>  (Processing menu)<br>"
        "Choose which peak-list rows to use as calibration anchors. The app locates the local "
        "intensity maximum near each nominal m/z, then opens a review window where you can "
        "adjust or exclude individual peaks. Clicking <i>Apply</i> fits the same quadratic TOF "
        "polynomial and saves the result. A batch mode lets you step through a whole folder "
        "file by file, with a ← Previous button to revisit already-processed files.<br><br>"
        "<b>Normalize Spectrum</b>  (Processing menu)<br>"
        "Divides all intensities so that either the highest peak = 1 (standard), or a specific "
        "m/z value = 1 (enter the m/z in the text box). The result appears as an overlay "
        "with a 💾 Save button. A batch version processes an entire folder.<br>"
        "No hard noise floor is imposed - use the <i>σ Clip</i> toggle for noise suppression.<br><br>"
        "<b>Baseline + Recalibration</b>  (Processing menu)<br>"
        "Applies baseline correction first, then auto-recalibration, in a single step.<br><br>"
        "<b>Batch processing</b>  (Processing menu)<br>"
        "All operations have batch versions that process an entire folder. "
        "Batch recalibration jobs report which files were flagged for manual review. "
        "Batch jobs offer a parallelisation option - select how many CPU cores to use.<br><br>"
        "<b>Cluster Detection</b>  (Analysis menu)<br>"
        "Finds repeating peak series separated by a fixed m/z spacing. "
        "Leave the spacing field empty to let the algorithm discover candidate spacings automatically, "
        "or enter one or more values (comma-separated) to search for specific clusters. "
        "Results are shown grouped by chain; detected clusters can be added directly to the peak lists.<br><br>"
        "<b>Compare Common / Unique Peaks</b>  (Analysis menu)<br>"
        "Detects peaks in all visible spectra and classifies them as common (present in all) "
        "or unique (present in only one). Common peaks are marked with green vertical lines, "
        "unique peaks with dashed per-spectrum coloured lines. Three opacity sliders control "
        "the visibility of background spectra, common lines, and unique lines independently.<br><br>"
        "<b>Measure Peak Area</b>  (Analysis menu)<br>"
        "Opens a window with three tools:<br>"
        "• <i>Interactive range</i> - click twice on the plot to define a range; area is computed "
        "immediately using the trapezoidal rule.<br>"
        "• <i>Total spectrum area</i> - integrates the entire spectrum for any loaded spectrum.<br>"
        "• <i>Peak-list ratios</i> - computes the area under each toggled peak list and shows "
        "relative ratios. Toggle peak lists in the window independently of the Peaks window.")

def show_help_noise():
    _help_dialog("Noise Clipping & Normalisation",
        "<b>Sigma noise clipping  (σ Clip)</b><br>"
        "The <i>σ Clip</i> checkbox in the toolbar suppresses baseline noise on the main plot "
        "without modifying the underlying data.<br><br>"
        "The slider next to it sets the threshold from <b>1.0 σ</b> (aggressive - clips more) "
        "to <b>4.0 σ</b> (conservative - keeps more of the baseline). "
        "The current value is shown as a live label (e.g. <i>2.0σ</i>).<br><br>"
        "<b>How it works</b><br>"
        "The algorithm estimates the noise level from the bottom 50 % of positive intensity values "
        "(the baseline region, not real peaks). It computes the median and standard deviation of "
        "that region, then sets the floor at:<br>"
        "&nbsp;&nbsp;&nbsp;<i>floor = noise_median + N × noise_std</i><br>"
        "Any data point below this floor is raised to the floor value. Real peaks are never clipped.<br><br>"
        "<b>Per-spectrum floors</b><br>"
        "Each spectrum gets its own floor computed from its own noise. "
        "A noisier spectrum will have a higher floor than a clean one - "
        "this is physically correct, not an error. "
        "Forcing a shared floor would hide real differences in data quality.<br><br>"
        "<b>σ Clip vs Dynamic Scale</b><br>"
        "• <i>Dynamic Scale</i> - normalises all spectra to the same peak height (0–1) and "
        "applies sigma clipping internally. Use this to visually compare spectra of different intensities.<br>"
        "• <i>σ Clip alone</i> - suppresses baseline noise but keeps your original intensity scale. "
        "Use this when you want noise suppression without rescaling.<br>"
        "Both can be active at the same time.<br><br>"
        "<b>Stacked mode + Log Y</b><br>"
        "When <i>Stacked</i> and <i>Log Y</i> are active together:<br>"
        "• If <i>σ Clip</i> is on, the slider-controlled floor is applied before log scaling "
        "and the Y range is derived from it automatically.<br>"
        "• If <i>σ Clip</i> is off, only a minimal safety floor (1 × 10⁻⁶) is added to "
        "prevent log(0) - no noise is clipped.<br><br>"
        "<b>Normalisation</b><br>"
        "Processing → <i>Normalize Spectrum</i> scales intensities so the tallest peak = 1, "
        "or so a chosen m/z = 1. No hard noise floor is applied to the output - "
        "the σ Clip toggle handles display-level noise suppression independently.")

def show_help_export():
    _help_dialog("Export & Print",
        "<b>Residuals files</b><br>"
        "Every recalibration (auto, manual, batch) writes a <i>Residuals dd.mm.yyyy - hh.mm.ss/</i> "
        "subfolder in the output folder containing:<br>"
        "• <i>*_residuals.csv</i> - table of original m/z, corrected m/z, and Δ m/z.<br>"
        "• <i>*_residuals.txt</i> - two-column file (m/z  Δ m/z) openable as a spectrum in Droplet.<br><br>"
        "<b>Plot menu</b><br>"
        "All plot exports show the plot exactly as it is in the window "
        "(size, zoom, labels, legend; the minimap is left out), like "
        "<i>Copy Plot to Clipboard</i>. "
        "Resize the window to change the image size.<br>"
        "• <i>Export as PNG</i> - the same image as Copy Plot to Clipboard.<br>"
        "• <i>Export as SVG</i> - the same view as a scalable vector image.<br>"
        "• <i>Export as PDF</i> - the same view as a vector PDF, page shaped like the plot.<br>"
        "• <i>Export as PGF</i> - the same view for LaTeX: <tt>\\usepackage{pgf}</tt> and "
        "<tt>\\input{plot.pgf}</tt>; text is typeset in the document font.<br>"
        "• <i>Batch Export Plots</i> - loads every file visible under the mode / dt filters "
        "and exports each as PNG, PDF, SVG or PGF with the current zoom (Y range fitted to the "
        "largest spectrum).<br>"
        "• <i>Copy Plot to Clipboard</i> (Ctrl+Shift+C) - copies a screenshot of the plot.<br>"
        "• <i>Export Peak Data as CSV</i> - table of all highlighted peak positions and intensities.<br>"
        "• <i>Print</i> (Ctrl+Shift+P) - sends to printer in landscape orientation.<br><br>"
        "File extensions (.png .svg .pdf .csv .json) are added automatically "
        "if not typed in the save dialog.")

def show_help_shortcuts():
    _help_dialog("Keyboard Shortcuts",
        "<b>Navigation</b><br>"
        "Z - toggle Zoom Box mode<br>"
        "F5 - reload current file<br><br>"
        "<b>Peaks</b><br>"
        "Ctrl+P - open Peaks window<br>"
        "P - toggle Pick Peaks mode<br>"
        "Ctrl+Shift+P - add a new peak row<br>"
        "Ctrl+Z - undo peak text edit<br>"
        "Ctrl+Y / Ctrl+Shift+Z - redo<br><br>"
        "<b>Analysis</b><br>"
        "A - toggle Peak Area measurement mode<br><br>"
        "<b>Overlays</b><br>"
        "Ctrl+Scroll - cycle overlays (next/previous)<br>"
        "Ctrl+↑ / Ctrl+↓ - cycle overlays up/down<br><br>"
        "<b>Export & Plot</b><br>"
        "Ctrl+Shift+C - copy plot to clipboard<br>"
        "Ctrl+Shift+P - print<br><br>"
        "<b>Application</b><br>"
        "Ctrl+Q - quit")

def show_about():
    QtWidgets.QMessageBox.about(main_win, f"About Droplet  {APP_VERSION}",
        f"<b>Droplet</b> &nbsp;<span style='color:gray;font-size:10px;'>{APP_VERSION}</span><br><br>"
        "Interactive viewer for LILBID mass spectrometry data.<br>"
        "Supports spectrum visualisation, peak annotation, baseline correction, "
        "recalibration, cluster detection, and peak area integration.<br><br>"
        "<b>Algorithms</b><br>"
        "Baseline correction (airPLS) and auto-recalibration adapted from "
        "<a href='https://github.com/mumair5393/LILBID_GUI'>LILBID_GUI</a> "
        "by M. Umair (MIT licence).<br><br>"
        "<b>Built with</b>  Python · PyQt6 · pyqtgraph · NumPy · SciPy · pandas")

def show_tutorial():
    overlay = TutorialOverlay(main_win)
    overlay.setGeometry(main_win.rect())
    overlay.show()
    overlay.raise_()

    def _resize_overlay():
        overlay.setGeometry(main_win.rect())
        # Use a timer so the resize fully settles before we update;
        # avoids re-entering resizeEvent during _refresh_parent_pixmap
        QtCore.QTimer.singleShot(0, overlay._update_step)

    main_win.resizeEvent = lambda e: (_resize_overlay(), QtWidgets.QWidget.resizeEvent(main_win, e))


def show_first_time_tutorial():
    already_seen = settings.value("tutorial_seen", False)
    if not already_seen:
        # Slight delay so the main window is fully painted first
        QtCore.QTimer.singleShot(300, show_tutorial)
        settings.setValue("tutorial_seen", True)


# ── Wire help menu ────────────────────────────
tutorial_action.triggered.connect(show_tutorial)
help_files_action.triggered.connect(show_help_files)
help_view_action.triggered.connect(show_help_view)
help_peaks_action.triggered.connect(show_help_peaks)
help_analysis_action.triggered.connect(show_help_analysis)
help_noise_action.triggered.connect(show_help_noise)
help_export_action.triggered.connect(show_help_export)
help_shortcuts_action.triggered.connect(show_help_shortcuts)
about_action.triggered.connect(show_about)


def open_updater():
    from droplet_pkg.ui.update_checker import open_updater as _open_updater
    try:
        if not _open_updater():
            QtWidgets.QMessageBox.information(
                main_win, "Droplet Updater", "The updater is already running.")
    except Exception as exc:
        QtWidgets.QMessageBox.warning(
            main_win, "Droplet Updater", f"Could not start the updater:\n{exc}")

check_updates_action.triggered.connect(open_updater)


_previous_versions_window = None

def open_previous_versions():
    global _previous_versions_window
    from droplet_pkg.ui.windows.previous_versions import PreviousVersionsWindow
    if _previous_versions_window is None:
        _previous_versions_window = PreviousVersionsWindow(main_win)
    _previous_versions_window.show()
    _previous_versions_window.raise_()
    _previous_versions_window.activateWindow()

previous_versions_action.triggered.connect(open_previous_versions)


_test_suite_process = None

def run_test_suite():
    """Tests → Run Test Suite…: open the test suite window in its own process
    (same Python), so it cannot disturb the running session."""
    global _test_suite_process
    if _test_suite_process is not None and _test_suite_process.poll() is None:
        QtWidgets.QMessageBox.information(
            main_win, "Test Suite", "The test suite is already running.")
        return
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = os.path.join(root, "assets", "test", "test_suite.py")
    if not os.path.isfile(script):
        QtWidgets.QMessageBox.warning(
            main_win, "Test Suite", f"The test suite was not found:\n{script}")
        return
    try:
        _test_suite_process = subprocess.Popen([sys.executable, script], cwd=root)
    except OSError as exc:
        QtWidgets.QMessageBox.warning(
            main_win, "Test Suite", f"Could not start the test suite:\n{exc}")

run_tests_action.triggered.connect(run_test_suite)

# A copy started from Help → Previous Versions must not update itself or
# manage other versions: those belong to the current installation.
if os.environ.get("DROPLET_PREVIOUS_VERSION"):
    check_updates_action.setVisible(False)
    previous_versions_action.setVisible(False)






# ─────────────────────────────────────────────
#  Initial plot + apply bright mode + restore session
# ─────────────────────────────────────────────
_init_display = settings.value("display_mode", "bright")
if _init_display == "dark":
    dark_action.setChecked(True)
set_display_mode(_init_display)
splash_step(86, "Restoring your last session…")
restore_session_state()
show_first_time_tutorial()

# ── "Go to last zoom" context menu entry ──────────────────────────────
def _go_to_last_zoom():
    global _in_zoom_restore
    # Pop the *current* range off first (it was just recorded as a new entry),
    # then restore the one before it.
    if len(_zoom_history) < 2:
        return
    _in_zoom_restore = True
    _zoom_history.pop()          # discard current view (it's already where we are)
    xr, yr = _zoom_history[-1]
    plot.vb.setXRange(xr[0], xr[1], padding=0)
    plot.vb.setYRange(yr[0], yr[1], padding=0)
    _in_zoom_restore = False

_goto_last_zoom_action = QtWidgets.QAction("Go to last zoom", main_win)
_goto_last_zoom_action.triggered.connect(_go_to_last_zoom)

# pyqtgraph adds its own actions to vb.menu; we insert ours right after "View All"

def _view_all_with_mz_threshold():
    """Re-range the main plot, ignoring data below _view_mz_lower_spin."""
    if df is None:
        plot.vb.autoRange()
        return
    lo = _view_mz_lower_spin.value()
    all_mz = []; all_int = []
    for curve in plot.listDataItems():
        xd, yd = curve.getData()
        if xd is None or len(xd) == 0:
            continue
        mask = xd >= lo
        if mask.any():
            all_mz.append(xd[mask]); all_int.append(yd[mask])
    if not all_mz:
        plot.vb.autoRange(); return
    mz_all  = np.concatenate(all_mz)
    int_all = np.concatenate(all_int)
    x_min, x_max = float(mz_all.min()), float(mz_all.max())
    y_min, y_max = float(int_all.min()), float(int_all.max())
    pad_x = (x_max - x_min) * 0.02
    pad_y = (y_max - y_min) * 0.05
    plot.vb.setXRange(x_min - pad_x, x_max + pad_x, padding=0)
    plot.vb.setYRange(y_min - pad_y, y_max + pad_y, padding=0)

def _inject_zoom_history_menu():
    vb_menu = plot.vb.menu
    actions = vb_menu.actions()
    insert_after = None
    for act in actions:
        if "view all" in act.text().lower():
            act.triggered.disconnect()
            act.triggered.connect(_view_all_with_mz_threshold)
            insert_after = act
            break
    if insert_after is not None:
        acts = vb_menu.actions()
        idx = acts.index(insert_after)
        if idx + 1 < len(acts):
            vb_menu.insertAction(acts[idx + 1], _goto_last_zoom_action)
        else:
            vb_menu.addAction(_goto_last_zoom_action)
    else:
        vb_menu.addAction(_goto_last_zoom_action)

QtCore.QTimer.singleShot(100, _inject_zoom_history_menu)


_refresh_dt_combo()   # populate dt filter from whatever files are already loaded

# Restore saved dt filter — done here, after all other init, so nothing overwrites it
_saved_dt = settings.value("dt_filter", "All")
if _saved_dt and _saved_dt != "All":
    _dt_idx = dt_combo.findText(_saved_dt)
    if _dt_idx >= 0:
        dt_combo.setCurrentIndex(_dt_idx)
        # Repopulate the main combo to reflect the restored filter
        _files_dt = get_txt_files_filtered(polarity_combo.currentText(), _saved_dt)
        _populate_main_combo(_files_dt if _files_dt else [])
        if _files_dt:
            combo.setCurrentIndex(0)
            plot_file(combo.currentData())

splash_step(92, "Loading the spectrum…")
if combo.count() == 0:
    initial_files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    if initial_files:
        _populate_main_combo(initial_files)
        combo.setCurrentIndex(0)
        plot_file(combo.currentData())
    else:
        _populate_main_combo([])  # shows "No file in X mode" placeholder
        df = df_raw = None

main_win.setWindowTitle(f"Droplet  {APP_VERSION}")
main_win.resize(1400, 780)
splash_step(96, "Drawing the plot…")
main_win.show()
QtCore.QTimer.singleShot(6000, finish_splash)   # never keep the start-up screen longer
# Connect after show so windowHandle() exists
QtCore.QTimer.singleShot(0, lambda:
    main_win.windowHandle().screenChanged.connect(_on_screen_changed))
# On Windows, pyqtgraph's scene geometry may not match the widget size on
# first paint when the primary screen has DPI scaling. Nudge after the event
# loop settles (250 ms) so the scene reflows to the correct dimensions.
def _startup_nudge():
    sz = main_win.size()
    main_win.resize(sz.width() + 1, sz.height())
    app.processEvents()
    main_win.resize(sz.width(), sz.height())
    plot_widget.updateGeometry()
QtCore.QTimer.singleShot(250, _startup_nudge)
# END startup nudge

# ── Give the window keyboard focus once it has opened ──
# Startup takes a few seconds; without this the window often opened behind
# or unfocused and needed a click or two before keys and shortcuts worked.
def _focus_main_window():
    main_win.raise_()
    main_win.activateWindow()
    if sys.platform == "win32":
        try:                                    # Windows: bring to the foreground
            import ctypes
            ctypes.windll.user32.SetForegroundWindow(int(main_win.winId()))
        except Exception:
            pass
    plot_widget.setFocus()
if splash_active():                     # focus once the start-up screen closes
    when_splash_finished(lambda: QtCore.QTimer.singleShot(50, _focus_main_window))
else:
    QtCore.QTimer.singleShot(400, _focus_main_window)

# ── Update check (separate process, 3 s delay so UI settles first) ──
# The updater window only appears if a newer version is available.
def _launch_update_check():
    try:
        from droplet_pkg.ui.update_checker import check_on_startup
        check_on_startup(settings)
    except Exception:
        pass   # never let update-check crash the app

QtCore.QTimer.singleShot(3000, _launch_update_check)

app.exec()