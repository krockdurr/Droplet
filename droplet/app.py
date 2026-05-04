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
import json
import re
import csv
import io
import concurrent.futures
import multiprocessing
import pyqtgraph as pg
import pandas as pd
import numpy as np
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
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
from scipy.sparse import csc_matrix, eye, diags
from scipy.sparse.linalg import spsolve

# ── Plotting tool deps ─────────────────────────
import matplotlib
matplotlib.use("QtAgg")                           # must be before pyplot import
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qtagg import NavigationToolbar2QT as NavToolbar
from matplotlib.patches import FancyArrowPatch

import warnings
warnings.filterwarnings("ignore", message="The figure layout has changed to tight")


APP_VERSION = "2.6.3"

from droplet.ui.windows.residuals_viewer import ResidualsViewerWindow
from droplet.ui.windows.cluster_detection import ClusterDetectionWindow
from droplet.ui.windows.peak_comparison import PeakComparisonWindow
from droplet.ui.windows.peak_area import PeakAreaWindow
from droplet.ui.windows.tutorial import TutorialOverlay



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

last_base_dir = settings.value("base_dir", "")
base_dir = last_base_dir if (last_base_dir and os.path.exists(last_base_dir)) else ""

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
        target = "neg" if neg_files else ("pos" if pos_files else "neg")
    elif default_pol == "neg":
        # prefer neg, but switch to pos if neg has nothing
        target = "neg" if neg_files else ("pos" if pos_files else "neg")
    elif default_pol == "pos":
        # prefer pos, but switch to neg if pos has nothing
        target = "pos" if pos_files else ("neg" if neg_files else "pos")
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
    try:
        app.setAttribute(QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
    except AttributeError:
        pass  # removed in PyQt6 6.0+
    # AA_Use96Dpi removed: it overrides Qt6's per-monitor DPI scaling,
    # forcing 96 DPI on all screens. Combined with QT_ENABLE_HIGHDPI_SCALING
    # being active (default in Qt6), this caused pyqtgraph's scene to be
    # the wrong size on any screen with scaling ≠ 100%, making axes appear
    # too short. Qt6 handles DPI correctly on its own without this attribute.

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
    # NEW ────────────────────────
    if _plot_tool_win_ref is not None:
        try: _plot_tool_win_ref.close()
        except Exception: pass
    # ────────────────────────────
    event.accept()

quit_shortcut.activated.connect(lambda: (
    _plot_tool_win_ref.close() if _plot_tool_win_ref else None,
    main_win.close()
))

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
pw_file_menu.addAction(import_action)
pw_file_menu.addAction(export_action)
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
_stacked_log_y   = settings.value("stacked_log_y",      False, type=bool)
_stacked_mirror  = settings.value("stacked_mirror",     False, type=bool)
_stacked_mirror_odd = settings.value("stacked_mirror_odd", False, type=bool)
_sigma3_clip        = settings.value("sigma3_clip",        False, type=bool)
_sigma3_n_sigma     = settings.value("sigma3_n_sigma",     1.0,   type=float)
_stacked_fit_y      = settings.value("stacked_fit_y",      False, type=bool)
_stacked_lock_y     = settings.value("stacked_lock_y",     False, type=bool)
_stacked_lock_ymax  = settings.value("stacked_lock_ymax",  1.05,  type=float)

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
stacked_logy_action = QtWidgets.QAction("  Stacked: Log Y axis", main_win, checkable=True)
stacked_logy_action.setChecked(_stacked_log_y)
stacked_logy_action.toggled.connect(lambda v: (
    globals().update({"_stacked_log_y": v}),
    settings.setValue("stacked_log_y", v),
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

crosshair_action.setChecked(True)

# ── Analysis ──────────────────────────────────
analysis_menu = menu_bar.addMenu("Processing")

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
area_mode_action = QtWidgets.QAction("Measure Peak Area  [A]", main_win, checkable=True)
# area_mode_action is now only accessible from the popup window, not directly in menu

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

# ── NEW: Plotting Tool (first entry) ──────────
open_plot_tool_action = QtWidgets.QAction("Open Plotting Tool…", main_win)
open_plot_tool_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+T"))
plot_menu.addAction(open_plot_tool_action)
plot_menu.addSeparator()
# ──────────────────────────────────────────────

export_png_action       = QtWidgets.QAction("Export as PNG…",            main_win)
export_svg_action       = QtWidgets.QAction("Export as SVG…",            main_win)
export_pdf_action       = QtWidgets.QAction("Export as PDF…",            main_win)
copy_plot_action        = QtWidgets.QAction("Copy Plot to Clipboard",     main_win)
copy_plot_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+C"))
export_peaks_csv_action = QtWidgets.QAction("Export Peak Data as CSV…",  main_win)
print_action            = QtWidgets.QAction("Print…",                    main_win)
print_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+P"))
plot_menu.addAction(export_png_action)
plot_menu.addAction(export_svg_action)
plot_menu.addAction(export_pdf_action)
plot_menu.addSeparator()
plot_menu.addAction(copy_plot_action)
plot_menu.addSeparator()
plot_menu.addAction(print_action)
plot_menu.addSeparator()

# ── Legend font size - inline widget in Plot menu ──
legend_font_spin = QtWidgets.QSpinBox()
legend_font_spin.setRange(6, 48)
legend_font_spin.setValue(int(settings.value("legend_font_pt", 11)))
legend_font_spin.setSuffix(" pt")
legend_font_spin.setFixedWidth(72)
legend_font_spin.setToolTip(
    "Font size of spectrum names in the on-screen legend.\n"
    "Export always uses a proportionally scaled version of this value.")
_legend_font_container = QtWidgets.QWidget()
_legend_font_layout = QtWidgets.QHBoxLayout(_legend_font_container)
_legend_font_layout.setContentsMargins(8, 2, 8, 2)
_legend_font_layout.addWidget(QtWidgets.QLabel("Legend font:"))
_legend_font_layout.addWidget(legend_font_spin)
_legend_font_layout.addStretch()
_legend_font_action = QtWidgets.QWidgetAction(main_win)
_legend_font_action.setDefaultWidget(_legend_font_container)
plot_menu.addAction(_legend_font_action)

# ── Display ───────────────────────────────────
display_menu  = menu_bar.addMenu("Display")
dark_action   = QtWidgets.QAction("Dark",   main_win, checkable=True)
bright_action = QtWidgets.QAction("Bright", main_win, checkable=True)
display_group = QtWidgets.QActionGroup(main_win)
display_group.addAction(dark_action); display_group.addAction(bright_action)
display_menu.addAction(dark_action);  display_menu.addAction(bright_action)
bright_action.setChecked(True)

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

main_layout.setMenuBar(menu_bar)

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
        render_plot()

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
            render_plot()

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
stacked_mode_chk = QtWidgets.QCheckBox("Stacked")
stacked_mode_chk.setChecked(_stacked_mode)
stacked_mode_chk.setToolTip("Show spectra stacked in rows instead of overlapping")
tools_row.addWidget(stacked_mode_chk)

stacked_logy_chk = QtWidgets.QCheckBox("Log Y")
stacked_logy_chk.setChecked(_stacked_log_y)
stacked_logy_chk.setToolTip("Use logarithmic Y axis in stacked mode")
stacked_logy_chk.setEnabled(_stacked_mode)   # only meaningful when stacked is on
tools_row.addWidget(stacked_logy_chk)

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

def _set_stacked_mode(val):
    global _stacked_mode
    _stacked_mode = val
    settings.setValue("stacked_mode", val)
    stacked_mode_action.blockSignals(True); stacked_mode_chk.blockSignals(True)
    stacked_mode_action.setChecked(val); stacked_mode_chk.setChecked(val)
    stacked_mode_action.blockSignals(False); stacked_mode_chk.blockSignals(False)
    stacked_logy_chk.setEnabled(val)   # grey out Log Y when not stacked
    try:
        plot.scene().sigMouseClicked.disconnect(plot_clicked)
    except Exception:
        pass
    if not val:
        plot.scene().sigMouseClicked.connect(plot_clicked)
    render_plot()

def _set_stacked_logy(val):
    global _stacked_log_y
    _stacked_log_y = val
    settings.setValue("stacked_log_y", val)
    stacked_logy_action.blockSignals(True); stacked_logy_chk.blockSignals(True)
    stacked_logy_action.setChecked(val); stacked_logy_chk.setChecked(val)
    stacked_logy_action.blockSignals(False); stacked_logy_chk.blockSignals(False)
    render_plot()

stacked_mode_action.toggled.connect(_set_stacked_mode)
stacked_mode_chk.stateChanged.connect(lambda v: _set_stacked_mode(bool(v)))

# disconnect old lambda on stacked_logy_action (it was set inline before)
stacked_logy_action.triggered.disconnect()
stacked_logy_action.toggled.connect(_set_stacked_logy)
stacked_logy_chk.stateChanged.connect(lambda v: _set_stacked_logy(bool(v)))

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
        render_plot()

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
plot.setLogMode(x=False, y=True)
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
_mz_cursor_label.setStyleSheet(
    "background: transparent; color: #3b9ddd; font-size: 11px; font-weight: bold;")
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
    Uses np.trapz on the raw intensity values, then subtracts the
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

    raw_area = float(np.trapz(int_arr, mz_arr))

    # Estimate the noise floor from the full spectrum (not just the window),
    # using the classical 3-sigma detection threshold.  The floor is then
    # treated as a flat baseline across the selected Δm/z interval.
    full_intensity = df['intensity'].values  # df = currently displayed spectrum
    noise_floor    = _estimate_noise_floor(full_intensity, n_sigma=3.0)
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
        full_noise_floor = _estimate_noise_floor(full_int, n_sigma=3.0)
        full_mz_thr  = full_mz[thr_mask]
        full_int_thr = full_int[thr_mask]
        full_raw   = float(np.trapz(full_int_thr, full_mz_thr))
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

def _sigma3_floor(intensity_arr):
    """Convenience wrapper - display clipping uses 1σ above noise median."""
    return _estimate_noise_floor(intensity_arr, n_sigma=1.0)

def _normalise_and_clip(data_df):
    """
    Normalise to [0,1] with zero-floor, then suppress noise below the
    sigma-clipped baseline estimate. Gentle: only flat baseline is clipped.
    """
    out = normalise_cached(data_df, zero_floor=True)
    out = out.copy()
    floor = _sigma3_floor(out['intensity'].values)
    out['intensity'] = np.where(
        out['intensity'] < floor,
        floor,
        out['intensity'])
    return out

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

    results = []
    for p in peaks_flat:
        shifted_p = p + peak_shift(p)
        tol = get_tolerance(shifted_p); backward = 0.2
        idx = ((data_df["mz"] >= shifted_p - backward) &
               (data_df["mz"] <= shifted_p + tol))
        if not idx.any():
            continue
        candidates  = data_df[idx]
        max_idx     = candidates["intensity"].idxmax()
        all_indices = candidates.index.to_numpy()
        max_pos     = np.where(all_indices == max_idx)[0][0]
        n = 10
        start = max(0, max_pos - n); end = min(len(candidates), max_pos + n + 1)
        subset   = candidates.iloc[start:end]
        mz_arr   = subset["mz"].to_numpy()
        int_arr  = subset["intensity"].to_numpy()
        peak_mz  = candidates.loc[max_idx, "mz"]
        peak_int = candidates.loc[max_idx, "intensity"]
        results.append((mz_arr, int_arr,
                        subset["mz"].min(), subset["mz"].max(),
                        peak_mz, peak_int))
    _highlight_cache[key] = results
    return results

# ─────────────────────────────────────────────
#  OPT: Render debounce timer (80 ms)
# ─────────────────────────────────────────────
_render_timer = QtCore.QTimer()
_render_timer.setSingleShot(True)
_render_timer.setInterval(80)

def render_plot():
    """Schedule a render; coalesces rapid-fire calls into one."""
    _render_timer.start()

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
def save_spectrum_df(data_df, path, src_path=None, process_tag=None):
    """Write spectrum to path, prepending original headers if src_path given."""
    with open(path, 'w', encoding='utf-8') as fh:
        if src_path and os.path.isfile(str(src_path)):
            if process_tag:
                fh.write(f"#processed={process_tag}\n")
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

def get_tolerance(mz): return min(0.1 + 0.004 * mz, 0.8)
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
    a new DataFrame with corrected m/z values.
    If fewer than 2 pairs, falls back to a linear scale factor.
    """
    if not pairs:
        return data_df.copy()

    data_np = data_df[['mz', 'intensity']].to_numpy().copy()

    if len(pairs) < 2:
        # Single-point fallback: uniform scale
        obs, ref = pairs[0]
        if obs == 0:
            return data_df.copy()
        factor = ref / obs
        out = data_df.copy()
        out['mz'] = out['mz'] * factor
        return out

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
        return out

    t_arr   = np.array([r[0] for r in cal_tof_rows])
    ref_arr = np.array([r[1] for r in cal_tof_rows])
    deg     = min(2, len(cal_tof_rows) - 1)
    fitparams = np.polyfit(t_arr, ref_arr, deg)

    recal_np = data_np.copy()
    for j in range(len(recal_np)):
        t_j = j * _AR_TIMESTEP
        recal_np[j, 0] = np.polyval(fitparams, t_j)

    return pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})


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



class ManualRecalWindow(QtWidgets.QWidget):
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
            for row in custom_peak_rows[:]:
                peaks_rows_layout_remove(row["widget"])
                custom_peak_rows.remove(row)
            for item in data:
                if item.get("mode") == "range":
                    _add_peak_row_base(
                        checked=item.get("checked", True),
                        color=QtGui.QColor(item.get("color", "#ff0000")),
                        label_text=item.get("label", ""),
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
                        label_text=item.get("label", ""))
            _clear_highlight_cache()
            update_pick_row_combo()
            render_plot()
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
                 "label": r["label_input"].text()}
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
    y_plot = np.log10(int_arr) + 0.04  # triangles float just above peak

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
                dot_y.append(np.log10(actual_int) if actual_int > 0 else 0)
        if dot_x:
            dot_scatter = pg.ScatterPlotItem(
                x=np.array(dot_x), y=np.array(dot_y),
                symbol='o', size=8,
                pen=pg.mkPen('#ff4444', width=1),
                brush=pg.mkBrush(255, 50, 50, 230))
            plot.addItem(dot_scatter)
            _manual_recal_scatter_items.append(dot_scatter)



# ═════════════════════════════════════════════════════════════════════════════
#  PEAK REVIEW WINDOW
# ═════════════════════════════════════════════════════════════════════════════

class PeakReviewWindow(QtWidgets.QWidget):
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

        self.setWindowTitle("Review Detected Peaks")
        self.resize(700, 520)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        hdr = QtWidgets.QLabel(
            "Peaks are grouped by peak list.  Each group header has an all/none toggle.\n"
            "Untoggling a peak also untoggle all higher-mass peaks in the same group.")
        hdr.setWordWrap(True)
        hdr.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(hdr)

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

    def _build_grouped_ui(self):
        """Populate (or repopulate) the inner scroll layout with grouped peak rows."""
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

                def _make_zoom_fn(_spin):
                    def _zoom():
                        mz = _spin.value()
                        pad = 15.0
                        plot.vb.setXRange(mz - pad, mz + pad, padding=0)
                    return _zoom

                zoom_peak_btn.clicked.connect(_make_zoom_fn(spin))

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
                spin.valueChanged.connect(
                    lambda val, i=g_idx: self._rebuild_scatter_from_selection())

            # ── Cascade logic: untoggle → also disable all higher-mass peaks ──
            # group_chks is already sorted by nominal m/z (ascending)
            def _make_cascade(chks):
                """chks: list of (nominal, g_idx, inc_chk) sorted ascending by nominal."""
                def _on_toggle(checked, source_nominal, source_idx):
                    if checked:
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

        self._inner_layout.addStretch()

    def _rebuild_rows(self):
        """Refresh all rows when detections change (called from ManualRecalWindow)."""
        self._build_grouped_ui()
        if _manual_recal_scatter_items:
            self._scatter = _manual_recal_scatter_items[-1]

    # ── Apply ──────────────────────────────────────────────────────────────
    def _apply(self):
        if df_raw is None:
            QtWidgets.QMessageBox.warning(
                self, "No spectrum", "No raw spectrum loaded."); return

        pairs = []
        for i, (inc, spin) in enumerate(zip(self._include_chks, self._mz_spins)):
            if not inc.isChecked():
                continue
            obs = self._mz_spins[i].value()
            ref = self._mz_spins[i]._ref_spin.value()
            if obs > 0 and ref > 0:
                row_data = self._detected[i][0]
                pl_name  = row_data["label_input"].text().strip() if row_data else ""
                pairs.append((obs, ref, pl_name))

        # Strip peak_list names before passing to the recalibration function
        # which expects plain (obs_mz, ref_mz) 2-tuples
        recal_pairs = [(obs, ref) for obs, ref, *_ in pairs]

        if not pairs:
            QtWidgets.QMessageBox.warning(
                self, "No valid pairs",
                "All detected peaks were invalid (zero m/z). Cannot recalibrate."); return

        # Run recalibration (needs plain 2-tuples)
        corrected_df = _apply_manual_recal_pairs(df_raw, recal_pairs)

        # Build residuals summary (observed vs reference, delta, peak list name)
        _residuals_summary = pd.DataFrame({
            "original m/z": [p[0] for p in pairs],
            "corrected to":  [p[1] for p in pairs],
            "Δ m/z":         [p[1] - p[0] for p in pairs],
            "peak_list":     [p[2] for p in pairs],
        })

        # Save output
        src_path = combo.currentData() or combo.currentText()
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
                return   # user cancelled

        _src_path = combo.currentData() or combo.currentText()
        try:
            save_spectrum_df(corrected_df, out_path,
                             src_path=_src_path,
                             process_tag="manual_recalibrated")
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Save Failed", f"Could not save:\n{out_path}\n\n{e}"); return

        # Save residuals into a shared session folder (timestamp lives on the
        # ManualRecalWindow parent so it survives PeakReviewWindow recreation
        # across batch files).
        _ts_host = self._parent if self._parent is not None else self
        if not hasattr(_ts_host, '_residuals_session_ts') or not _ts_host._residuals_session_ts:
            from datetime import datetime as _dt
            _ts_host._residuals_session_ts = _dt.now().strftime("%d.%m.%Y - %H.%M.%S")
        _session_ts = _ts_host._residuals_session_ts
        # Non-batch: the save folder may differ per file — use the output folder
        # but keep the timestamp constant so all files share one Residuals dir.
        _res_save_folder = os.path.dirname(out_path)
        try:
            _save_residuals(_res_save_folder,
                            os.path.basename(out_path), _residuals_summary,
                            ts=_session_ts)
        except Exception as e_res:
            QtWidgets.QMessageBox.warning(
                self, "Residuals",
                f"Spectrum saved, but residuals could not be written:\n{e_res}")

        # Add as overlay only if the user opted in
        if self._add_as_overlay:
            _add_processed_overlay(corrected_df, "Manual Recal", "_manual_recalibrated")
        _clear_manual_recal_scatter()

        if self.batch_mode:
            _batch_manual_state["done_count"] += 1
            _save_batch_manual_state()
            self.close()
            _advance_batch_manual(self._parent)
            # Re-raise the first popup above the main window (Windows focus fix)
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
            return
    
        mz_arr = np.array(mz_list, dtype=float)
        int_arr = np.array(int_list, dtype=float)
        _draw_manual_recal_scatter(mz_arr, int_arr)

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
    
    def _draw_peak_highlight(self, row_idx):
        """Draw temporary yellow overlay around the selected peak"""
        if row_idx >= len(self._detected):
            return
        
        # Get current detected peak values
        _, _, det_mz, det_int = self._detected[row_idx]
        mz_spin = self._mz_spins[row_idx].value()  # Current edited value
        
        # Get ~20 points around peak (±10 points each side)
        if df is None or len(df) == 0:
            return
        
        # Find peak in spectrum data
        tol = 0.5  # ±0.5 Da window
        mask = (df['mz'] >= mz_spin - tol) & (df['mz'] <= mz_spin + tol)
        peak_data = df[mask]
        
        if peak_data.empty:
            return
        
        # Extract ~20 points around peak (or all if fewer)
        n_points = min(20, len(peak_data))
        center_idx = len(peak_data) // 2
        start = max(0, center_idx - n_points // 2)
        end = min(len(peak_data), start + n_points)
        
        mz_subset = peak_data.iloc[start:end]['mz'].values
        int_subset = peak_data.iloc[start:end]['intensity'].values * 1.05  # 5% above
        
        # Remove existing highlight
        if self._peak_highlight_curve is not None:
            try:
                plot.removeItem(self._peak_highlight_curve)
            except:
                pass
        
        # Draw thick yellow highlight (peak) + thin white outline
        self._peak_highlight_curve = plot.plot(
            mz_subset, int_subset,
            pen=pg.mkPen('#ffff00', width=4),  # Thick yellow
            name="peak_highlight"
        )
        # white_outline = plot.plot(
        #     mz_subset, int_subset,
        #     pen=pg.mkPen('w', width=2),  # Thin white outline on top
        #     name="peak_outline"
        # )
        
        # # Store white outline to remove later
        # self._peak_highlight_curve._outline = white_outline



    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Enter and hasattr(obj, "_peak_index"):
            self._highlight_scatter_point(obj._peak_index)
            self.highlight_row(obj._peak_index)
        elif event.type() == QtCore.QEvent.Type.Leave and hasattr(obj, "_peak_index"):
            self._clear_scatter_highlight()
            self.clear_row_highlight()
        return super().eventFilter(obj, event)


    def closeEvent(self, event):
        self.clear_row_highlight()  # Clean up peak overlay
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
            save_spectrum_df(processed_df, save_path)
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
    path, save_folder, suffix, sep, do_baseline, do_recal, airpls = args
    try:
        # Re-import inside worker process
        import pandas as pd
        import numpy as np
        from scipy.sparse import csc_matrix, eye, diags
        from scipy.sparse.linalg import spsolve

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
            fitparams = np.polyfit(cal_tof[:, 0], cal_tof[:, 3], 2)
            recal = data_np.copy()
            for j in range(len(recal)):
                recal[j, 0] = np.polyval(fitparams, j * TS)
            df = pd.DataFrame({'mz': recal[:, 0], 'intensity': recal[:, 1]})

        stem, ext = os.path.splitext(os.path.basename(path))
        out_path = os.path.join(save_folder, stem + suffix + (ext or ".txt"))
        df.to_csv(out_path, sep='\t', index=False, header=False)
        return None  # success

    except Exception as e:
        return f"{os.path.basename(path)}: {e}"

def _batch_select_save_folder():
    folder = QtWidgets.QFileDialog.getExistingDirectory(
        main_win, "Select Output Folder", base_dir or "")
    return folder or None

def _batch_select_input_files():
    files = get_txt_files_filtered(polarity_combo.currentText(), dt_combo.currentText())
    return files if files else all_txt_files

def _batch_run(process_fn, files, save_folder, suffix, title, parallel=False, n_workers=1):
    if not files:
        QtWidgets.QMessageBox.warning(main_win, "Batch", "No files to process."); return
    n = len(files)
    dlg = QtWidgets.QProgressDialog(f"Processing 0 / {n}…", "Cancel", 0, n, main_win)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(QtCore.Qt.WindowModality.ApplicationModal)
    dlg.setMinimumWidth(340); dlg.show()
    errors = []

    if not parallel or n_workers <= 1:
        # Serial path (unchanged)
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
                                 process_tag=suffix.strip("_"))
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
    else:
        # Parallel path - uses threads to avoid Windows spawn re-import issues.
        # NumPy/SciPy release the GIL so threading gives real parallelism here.
        sep = get_sep_from_combo()
        do_baseline = "_baseline" in suffix
        do_recal    = "_recal" in suffix
        airpls      = airpls_action.isChecked()
        args_list   = [(p, save_folder, suffix, sep, do_baseline, do_recal, airpls)
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
    method = 'airPLS' if airpls_action.isChecked() else 'SNIP'
    _batch_run(apply_baseline, _batch_select_input_files(), folder,
               "_baseline_corrected", f"Batch Baseline ({method})",
               parallel=parallel, n_workers=n_workers)

def _save_residuals(save_folder, filename, summary_df, ts=None,
                    orig_mz=None, recal_mz=None):
    """
    Save residuals inside a timestamped subfolder.

      *_residuals_table.csv    - calibration-point table; may include
                                 'peak_list' column when data comes from
                                 named peak-list rows.
      *_residuals_spectrum.txt - dense mz vs Δmz curve (one line per point).
                                 Header comments include '# peak_list:<name>'
                                 markers so the viewer can split by peak list.
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
            recal_np, check_manually, summary_df, _ = _run_auto_recal_worker(data_np, path)
            processed = pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})
            stem, ext = os.path.splitext(os.path.basename(path))
            out_path = os.path.join(save_folder, stem + suffix + (ext or ".txt"))
            save_spectrum_df(processed, out_path)
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
    
# Wire analysis menu
airpls_action.triggered.connect(lambda: reapply_transforms() if baseline_action.isChecked() else None)
snip_action.triggered.connect(  lambda: reapply_transforms() if baseline_action.isChecked() else None)
cluster_detect_action.triggered.connect(show_cluster_detection)
area_action.triggered.connect(_open_peak_area_window)

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
def highlight_peaks(data_df, peaks, color_str, peak_label="Peak", alpha=255, bg_color_str='w'):
    color    = apply_alpha(color_str, alpha)
    bg_color = pg.mkColor(bg_color_str)

    peaks_flat = []
    for group in peaks:
        if not isinstance(group, (list, tuple)): group = [group]
        peaks_flat.extend(group)

    geom_list = _get_highlight_geometry(data_df, peaks_flat)
    for (mz_arr, int_arr, mz_min, mz_max, peak_mz, peak_int) in geom_list:
        plot.plot(mz_arr, int_arr, pen=pg.mkPen(bg_color, width=3))
        plot.plot(mz_arr, int_arr, pen=pg.mkPen(color,    width=3))
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

def _draw_peak_labels(show_labels, show_masses, mass_threshold_abs,
                      colored=False, font_size=1.0):
    global _label_text_items
    for item in _label_text_items:
        try: plot.removeItem(item)
        except Exception: pass
    _label_text_items.clear()
    if not show_labels and not show_masses: return

    BASE_PT = 10
    MERGE_TOL = 0.5   # m/z units - peaks closer than this are merged

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

    # ── Draw one TextItem per group ────────────────────────────────────────
    show_int = getattr(show_integers_toggle, 'isChecked', lambda: False)()
    for g in groups:
        texts = []
        if show_labels and g["labels"]:
            if settings.value("label_stack_vertical", False, type=bool):
                texts.extend(g["labels"])
            else:
                texts.append(", ".join(g["labels"]))
        if show_masses:
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

        text_item = pg.TextItem(text="\n".join(texts), anchor=(0, 0.5), angle=60,
                                color=label_color)
        text_item.setFont(font)
        text_item.setPos(g["mid_mz"], np.log10(g["peak_int"]) + 0.06 if g["peak_int"] > 0 else 0)
        plot.addItem(text_item)
        _label_text_items.append(text_item)

def _export_with_colored_labels(export_fn, export_scale=1.0):
    """Redraw peak labels in row colors at export scale, add export-only peaks legend, call export_fn(), then restore."""
    show_lbl    = peak_labels_toggle.isChecked()
    show_masses = peak_masses_toggle.isChecked()
    mx = df['intensity'].max() if df is not None and len(df) > 0 else 1.0
    threshold_abs = (mass_threshold_spin.value() / 100.0) * mx

    # ── Build export-only peak-rows legend ──────────────────────────
    # Collect active peak rows that have a label and at least one peak
    active_rows = [r for r in custom_peak_rows
                   if r["checkbox"].isChecked()
                   and r["label_input"].text().strip()
                   and parse_peaks_text(r["peaks_input"].text())]

    peak_legend = None
    _dummy_curves = []
    if active_rows:
        screen_pt = legend_font_spin.value()
        export_pt = max(8, int(screen_pt * export_scale / 10))  # scale relative to label scale
        peak_legend = pg.LegendItem(offset=(-10, 10))   # top-right corner
        peak_legend.setParentItem(plot.vb)
        peak_legend.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-10, 10))
        peak_legend.setLabelTextSize(f"{export_pt}pt")
        for row in active_rows:
            color = row["color"][0].name()
            label = row["label_input"].text().strip()
            # Use a dummy PlotDataItem so LegendItem renders a colored line swatch
            dummy = pg.PlotDataItem(pen=pg.mkPen(color, width=3))
            peak_legend.addItem(dummy, label)
            _dummy_curves.append(dummy)

    # Redraw labels in color at export size
    _draw_peak_labels(show_lbl, show_masses, threshold_abs,
                      colored=True, font_size=export_scale)
    try:
        export_fn()
    finally:
        # Remove export-only peak legend
        if peak_legend is not None:
            try:
                plot.vb.removeItem(peak_legend)
            except Exception:
                pass
        # Restore normal labels
        _draw_peak_labels(show_lbl, show_masses, threshold_abs,
                          colored=False, font_size=1.0)

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
            text_item = pg.TextItem(
                text=str(int(round(mz))) if _si else f"{mz:.2f}",
                anchor=(0.5, 1.0), angle=60,
                color='w' if current_display == 'dark' else 'k')
            y_log = np.log10(intensity) + 0.04 if intensity > 0 else 0
            text_item.setPos(mz, y_log)
            plot.addItem(text_item)
            _label_text_items.append(text_item)

    # ── Auto-detect scatter (red triangles) ──
    if not show_auto or data_df is None or len(data_df) == 0:
        return

    pk_mz, pk_int = _get_auto_peaks(data_df, threshold_value, mode)
    if len(pk_mz) == 0:
        return

    y_offset = np.where(pk_int > 0, np.log10(pk_int) + 0.06, pk_int)
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
        if not row["checkbox"].isChecked(): continue
        if not row.get("envelope_btn") or not row["envelope_btn"].isChecked(): continue
        peaks = parse_peaks_text(row["peaks_input"].text())
        if len(peaks) < 2: continue

        color = row["color"][0].name()
        tops_mz  = []
        tops_int = []
        for mz_nom in sorted(peaks):
            # Find local maximum within ±1 Da of the nominal m/z
            mask = (data_df['mz'] >= mz_nom - 1.0) & (data_df['mz'] <= mz_nom + 1.0)
            sub = data_df[mask]
            if sub.empty: continue
            idx = sub['intensity'].idxmax()
            tops_mz.append(float(sub.loc[idx, 'mz']))
            tops_int.append(float(sub.loc[idx, 'intensity']))

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

_stacked_sub_plots      = []
_stacked_mouse_handlers = []
_stacked_click_handlers = []
_stacked_spectra_data   = []   # (data_df, mz_vals, int_vals) per sub-plot, for peak redraws
_stacked_peak_items     = []   # peak overlay items per sub-plot, for peak-only redraws

def _draw_stacked_peak_labels(sub_plot, data_df, mz_vals, int_vals, return_items=False):
    """
    Draw peak highlights and labels onto a single stacked sub-plot.
    Mirrors what render_plot does for the main plot, but targets sub_plot.
    """
    show_lbl    = peak_labels_toggle.isChecked()
    show_masses = peak_masses_toggle.isChecked()
    show_int    = show_integers_toggle.isChecked()
    pk_alpha    = highlight_alpha()
    MERGE_TOL   = 0.5
    BASE_PT     = 9

    theme_color = 'w' if current_display == 'dark' else 'k'
    created_items = []

    # Normalise intensity to [0,1] so positions match the sub-plot's Y axis
    norm = normalise_cached(data_df, zero_floor=True)
    norm_mz  = norm['mz'].values
    norm_int = norm['intensity'].values

    groups = []  # {mid_mz, peak_mz, peak_int, labels, colors}

    _foc_idx = _conf_focused_row_idx[0]
    for _ri, row in enumerate(custom_peak_rows):
        if not row["checkbox"].isChecked():
            continue
        peaks = parse_peaks_text(row["peaks_input"].text())
        if not peaks:
            continue
        color_str = row["color"][0].name()
        lbl = row["label_input"].text().strip() or "Custom peaks"
        _ralpha = pk_alpha if _foc_idx < 0 or _ri == _foc_idx else max(pk_alpha // 4, 15)

        peaks_flat = []
        for group in peaks:
            if not isinstance(group, (list, tuple)):
                group = [group]
            peaks_flat.extend(group)

        # Find the nearest m/z point in the normalised spectrum for each peak
        for peak_mz_target in peaks_flat:
            idx = np.argmin(np.abs(norm_mz - peak_mz_target))
            peak_mz  = float(norm_mz[idx])
            peak_int = float(int_vals[idx])

            # Draw a highlight bar on the sub-plot
            geom_list = _get_highlight_geometry(data_df, [peak_mz_target])
            for (mz_arr, int_arr, mz_min, mz_max, _pmz, _pint) in geom_list:
                bg_color = pg.mkColor(theme_color)
                hi_color = apply_alpha(color_str, _ralpha)
                bar_idx = np.searchsorted(mz_vals, mz_arr).clip(0, len(int_vals) - 1)
                norm_arr = int_vals[bar_idx]
                sub_plot.plot(mz_arr, norm_arr, pen=pg.mkPen(bg_color, width=3))
                sub_plot.plot(mz_arr, norm_arr, pen=pg.mkPen(hi_color, width=3))
            if return_items:
                created_items.extend(sub_plot.listDataItems()[-2:])

            # Merge nearby labels (same tolerance as the main plot)
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

    # Draw one TextItem per group on this sub-plot
    for g in groups:
        texts = []
        if show_lbl and g["labels"]:
            if settings.value("label_stack_vertical", False, type=bool):
                texts.extend(g["labels"])
            else:
                texts.append(", ".join(g["labels"]))
        if show_masses:
            mz_val = g["peak_mz"]
            texts.append(str(int(round(mz_val))) if show_int else f"{mz_val:.2f}")
        if not texts:
            continue

        label_color = g["colors"][0] if g["colors"] else theme_color
        font = QtGui.QFont()
        font.setPointSize(BASE_PT)

        text_item = pg.TextItem(
            text="\n".join(texts),
            anchor=(0, 0.5),
            angle=60,
            color=label_color,
        )
        text_item.setFont(font)
        # Y position: slightly above the normalised peak intensity
        y_pos = g["peak_int"] + 0.05
        text_item.setPos(g["peak_mz"], y_pos)
        sub_plot.addItem(text_item)
        if return_items:
            created_items.append(text_item)

    if return_items:
        return created_items

def _build_stacked_layout(spectra_list, restore_xrange=None, restore_yrange=None):
    """
    Hide the main plot, then add N sub-plots starting at row 1.
    Row 0 is collapsed to zero height so no empty axes appear at the top.
    spectra_list: list of (data_df, display_name, pen_color)
    restore_xrange: optional (xmin, xmax) to restore after build instead of auto-ranging.
    restore_yrange: optional (ymin, ymax) to restore after build instead of auto-ranging.
    """
    global _stacked_sub_plots, _stacked_spectra_data, _stacked_peak_items
    _stacked_peak_items = []
    _stacked_spectra_data = []   # list of (data_df, mz_vals, int_vals) per sub-plot
    _destroy_stacked_layout()
    app.processEvents()

    n = len(spectra_list)
    if n == 0:
        return

    theme_color = 'k' if current_display == 'bright' else 'w'
    link_x = None
    link_y = None

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
            ax.setHeight(0)                 # take no vertical space
        sub.setLabel('left', name, size='9pt')
        # Stronger gridlines: higher alpha, both axes
        sub.showGrid(x=True, y=True, alpha=0.5)
        sub.setLogMode(x=False, y=_stacked_log_y)
        sub.setMenuEnabled(True)   # keep menu so "View All" / right-click works

        # Replace pyqtgraph's default multi-section context menu with a lean one
        # that only has "View All" (re-ranging via the master plot so all
        # X-linked sub-plots snap back together).
        lean_menu = QtWidgets.QMenu()
        view_all_act = QtWidgets.QAction("View All", lean_menu)

        def _make_view_all():
            def _do():
                if not _stacked_sub_plots:
                    return
                lo = _view_mz_lower_spin.value()
                all_mz = []; all_int = []
                for sp in _stacked_sub_plots:
                    for curve in sp.listDataItems():
                        xd, yd = curve.getData()
                        if xd is None or len(xd) == 0:
                            continue
                        mask = xd >= lo
                        if mask.any():
                            all_mz.append(xd[mask])
                            all_int.append(yd[mask])
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
                if not _stacked_log_y and all_int:
                    int_all = np.concatenate(all_int)
                    y_max = float(int_all.max())
                    pad_y = y_max * 0.05
                    y_floor = 0.0 if _stacked_lock_y else float(int_all.min()) - pad_y
                    for sp in _stacked_sub_plots:
                        sp.vb.setYRange(y_floor, y_max + pad_y, padding=0)
            return _do

        view_all_act.triggered.connect(_make_view_all())
        lean_menu.addAction(view_all_act)
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
                        _vl.setPos(x); _hl.setPos(y)
                    if _stacked_log_y:
                        y_disp = 10 ** y
                        _lbl.setText(f"<span style='font-size:9pt'>m/z={x:.2f}  I={y_disp:.3e}</span>")
                    else:
                        _lbl.setText(f"<span style='font-size:9pt'>m/z={x:.2f}  I={y:.4f}</span>")
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

        norm = normalise_cached(data_df, zero_floor=True)
        mz_vals = norm['mz'].values
        int_vals = norm['intensity'].values

        # Re-normalise so the threshold-filtered region fills 0→1.
        # This prevents below-threshold peaks from dominating the Y scale.
        lo_thresh = _view_mz_lower_spin.value()
        above_mask = mz_vals >= lo_thresh
        if above_mask.any():
            above_max = int_vals[above_mask].max()
            if above_max > 0:
                int_vals = int_vals / above_max
        int_vals = np.clip(int_vals, 0, None)

        if _sigma3_clip:
            sigma3_floor = _estimate_noise_floor(int_vals, n_sigma=_sigma3_n_sigma)
            sigma3_floor = max(sigma3_floor, 1e-6)
            int_vals = np.clip(int_vals, sigma3_floor, None)
        elif _stacked_log_y:
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
        if _stacked_log_y:
            log_floor = np.log10(sigma3_floor) if sigma3_floor else -6
            if _mirror_this:
                sub.vb.setYRange(0, log_floor, padding=0)
                sub.getAxis('left').setStyle(tickTextOffset=2)
                sub.vb.invertY(True)
            else:
                sub.vb.setYRange(log_floor, 0, padding=0)
                sub.vb.invertY(False)
        else:
            if _mirror_this:
                sub.vb.setYRange(0, 1.05, padding=0)
                sub.vb.invertY(True)
            else:
                sub.vb.setYRange(0, 1.05, padding=0)
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

        # ── Peak highlights and labels for this sub-plot ────────────
        items = _draw_stacked_peak_labels(sub, data_df, mz_vals, int_vals,
                                          return_items=True)
        _stacked_peak_items.append(items or [])

        _stacked_sub_plots.append(sub)

    plot.scene().sigMouseClicked.connect(_stacked_click_handler)
    _stacked_click_handlers.append(_stacked_click_handler)

    _apply_stacked_y()


    # Give every sub-plot row an equal stretch factor and a small minimum,
    # but NO maximum — that lets Qt distribute available height freely on resize.
    for i in range(n):
        plot_widget.ci.layout.setRowMinimumHeight(i + 1, 40)
        plot_widget.ci.layout.setRowMaximumHeight(i + 1, 16777215)
        plot_widget.ci.layout.setRowStretchFactor(i + 1, 1)
    plot_widget.ci.layout.activate()
    app.processEvents()

    # Wire a resize handler that re-equalises row heights whenever the widget
    # changes size. We store it so _destroy_stacked_layout can disconnect it.
    def _on_stacked_resize(event):
        if not _stacked_sub_plots:
            return
        total_h = plot_widget.height()
        rh = max(40, total_h // len(_stacked_sub_plots))
        layout = plot_widget.ci.layout
        for idx in range(len(_stacked_sub_plots)):
            layout.setRowMinimumHeight(idx + 1, rh)
            layout.setRowMaximumHeight(idx + 1, rh)
        # Do NOT call layout.activate() here — it resets column widths and
        # causes plots to collapse to a fraction of the horizontal space.
        # Qt's geometry pass triggered by resizeEvent handles the reflow correctly.
        plot_widget.ci.layout.invalidate()

    plot_widget._stacked_resize_handler = _on_stacked_resize
    _orig_resize = getattr(plot_widget, '_orig_resize_event', plot_widget.resizeEvent)
    plot_widget._orig_resize_event = _orig_resize
    def _patched_resize(event):
        _orig_resize(event)
        _on_stacked_resize(event)
    plot_widget.resizeEvent = _patched_resize

    def _nudge_stacked():
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
            if hasattr(plot_widget, '_stacked_resize_handler'):
                plot_widget._stacked_resize_handler(event)
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

    _apply_stacked_y()
    QtCore.QTimer.singleShot(100, _nudge_stacked)


def _destroy_stacked_layout():
    """Remove all stacked sub-plots and reset grid layout constraints."""
    global _stacked_sub_plots, _stacked_mouse_handlers, _stacked_click_handlers
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

    # Make the main plot visible and force a full geometry refresh.
    plot.setVisible(True)
    plot_widget.ci.layout.activate()
    app.processEvents()
    def _force_resize():
        sz = plot_widget.size()
        plot_widget.resize(sz.width(), sz.height() + 1)
        app.processEvents()
        plot_widget.resize(sz.width(), sz.height() - 1)
        plot_widget.updateGeometry()
    QtCore.QTimer.singleShot(100, _force_resize)

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

    # Clean up any leftover stacked sub-plots from a previous render
    if _stacked_sub_plots:
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

        # Save the current X and Y zoom so we can restore them after the rebuild.
        # Only save if sub-plots already exist (i.e. this is a re-render, not
        # the first build) and the user has actually zoomed somewhere.
        _saved_stacked_xrange = None
        _saved_stacked_yrange = None
        if _stacked_sub_plots:
            try:
                _saved_stacked_xrange = _stacked_sub_plots[0].vb.viewRange()[0]
                _saved_stacked_yrange = _stacked_sub_plots[0].vb.viewRange()[1]
            except Exception:
                pass

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

        _build_stacked_layout(spectra_info, restore_xrange=_saved_stacked_xrange, restore_yrange=_saved_stacked_yrange)
        return
    # ══════════════════════════════════════════════════════════════

    # Normal (non-stacked) mode: make sure stacked sub-plots are gone
    # and the main plot is visible
    if _stacked_sub_plots:
        _destroy_stacked_layout()
    plot.setVisible(True)

    draw_df = df
    if do_sub:
        sub_ov = next((o for o in overlay_list
                       if o["toggle"].isChecked() and o["df"] is not None), None)
        if sub_ov is not None:
            draw_df = compute_subtraction(df, sub_ov["df"], dynamic=subtract_dyn_action.isChecked())

    if draw_df is None: return

    if dyn:
        plot_df = _normalise_and_clip(draw_df)
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
                    _hl_df = _normalise_and_clip(_first_ov["df"])
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
                if not row["checkbox"].isChecked(): continue
                peaks = parse_peaks_text(row["peaks_input"].text())
                if not peaks: continue
                color = row["color"][0].name()
                lbl   = row["label_input"].text().strip() or "Custom peaks"
                _ralpha = pk_alpha if _foc_idx < 0 or _ri == _foc_idx else max(pk_alpha // 4, 15)
                highlight_peaks(_hl_df, [peaks], color, peak_label=lbl,
                                alpha=_ralpha, bg_color_str=main_color_str)

    if not do_sub:
        for ov_data in overlay_list:
            if not ov_data["toggle"].isChecked() or ov_data["df"] is None: continue
            alpha    = overlay_alpha()
            if dyn:
                ov_df = _normalise_and_clip(ov_data["df"])
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
                if not row["checkbox"].isChecked(): continue
                peaks = parse_peaks_text(row["peaks_input"].text())
                if not peaks: continue
                color = row["color"][0].name()
                lbl   = row["label_input"].text().strip() or "Custom peaks"
                _ralpha = pk_alpha if _foc_idx < 0 or _ri == _foc_idx else max(pk_alpha // 4, 15)
                highlight_peaks(ov_df, [peaks], color, peak_label=lbl,
                                alpha=_ralpha, bg_color_str=main_color_str)
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

# Wire debounce timer
_render_timer.timeout.connect(_do_render_plot)

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

    # ── Peak lists ────────────────────────────────────────────────────
    peaks = []
    for r in custom_peak_rows:
        entry = {
            "checked": r["checkbox"].isChecked(),
            "color":   r["color"][0].name(),
            "label":   r["label_input"].text(),
        }
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
        "stacked_mode":    stacked_mode_chk.isChecked(),
        "stacked_log_y":   stacked_logy_chk.isChecked(),
    }

    project["version"] = "1.0"

    # ── Plotting tool plots ───────────────────────────────────────────
    plots_to_save = []
    if _plot_tool_win_ref is not None:
        try:
            plots_to_save = _plot_tool_win_ref._get_all_plots_data()
        except Exception:
            pass
    project["plots"] = plots_to_save

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
    for row in custom_peak_rows[:]:
        peaks_rows_layout_remove(row["widget"])
        custom_peak_rows.remove(row)

    for item in project.get("peak_lists", []):
        if item.get("mode") == "range":
            _add_peak_row_base(
                checked=item.get("checked", True),
                color=QtGui.QColor(item.get("color", "#ff0000")),
                label_text=item.get("label", ""),
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
                label_text=item.get("label", ""))

    update_pick_row_combo()
    _clear_highlight_cache()

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
        stacked_logy_chk.setChecked(view.get("stacked_log_y", False))

        # Restore zoom/pan
        xr = view.get("x_range")
        yr = view.get("y_range")
        if xr and yr:
            QtCore.QTimer.singleShot(200, lambda: (
                plot.vb.setXRange(xr[0], xr[1], padding=0),
                plot.vb.setYRange(yr[0], yr[1], padding=0)
            ))

    # ── Restore plotting tool plots ───────────────────────────────────
    saved_plots = project.get("plots", [])
    if saved_plots:
        global _plot_tool_win_ref
        if _plot_tool_win_ref is None:
            _plot_tool_win_ref = PlottingToolWindow(parent=main_win)
            _plot_tool_win_ref.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
        _plot_tool_win_ref._restore_all_plots_data(saved_plots)
        _plot_tool_win_ref.show()
        _plot_tool_win_ref.raise_()

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

def _sync_confirmation_panel():
    """Push the current peak rows into the confirmation panel (no-op until panel exists)."""
    panel = _confirmation_panel_ref[0]
    if panel is None:
        return
    from droplet.ui.windows.peak_confirmation import _row_dict_from_widget_row as _rdfwr
    panel.sync_with_peak_rows([_rdfwr(r) for r in custom_peak_rows])

# ─────────────────────────────────────────────
#  Peaks window – rows with drag-to-reorder
# ─────────────────────────────────────────────
custom_peak_rows = []

class PeakRowsContainer(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

    def add_row_widget(self, widget):
        self._layout.addWidget(widget)

    def remove_row_widget(self, widget):
        self._layout.removeWidget(widget)
        widget.hide()

    def move_row(self, from_idx, to_idx):
        if from_idx == to_idx: return
        item = self._layout.takeAt(from_idx)
        if item is None: return
        self._layout.insertItem(to_idx, item)
        row = custom_peak_rows.pop(from_idx)
        custom_peak_rows.insert(to_idx, row)
        update_pick_row_combo()
        render_plot()
        _sync_confirmation_panel()

peaks_rows_container = PeakRowsContainer()
peaks_rows_scroll    = QtWidgets.QScrollArea()
peaks_rows_scroll.setWidgetResizable(True)
peaks_rows_scroll.setWidget(peaks_rows_container)
peaks_rows_scroll.setMaximumHeight(320)
peaks_layout.addWidget(peaks_rows_scroll)

def peaks_rows_layout_add(widget):    peaks_rows_container.add_row_widget(widget)
def peaks_rows_layout_remove(widget): peaks_rows_container.remove_row_widget(widget)

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

    # Redraw peaks on each sub-plot, collecting the new items
    for sub_idx, sub in enumerate(_stacked_sub_plots):
        items_this_sub = _draw_stacked_peak_labels(sub,
            _stacked_spectra_data[sub_idx][0],
            _stacked_spectra_data[sub_idx][1],
            _stacked_spectra_data[sub_idx][2],
            return_items=True)
        _stacked_peak_items.append(items_this_sub or [])

def _render_peaks_or_full():
    """Route peak row changes: peak-only redraw in stacked mode, full render otherwise."""
    if _stacked_mode and _stacked_sub_plots:
        _redraw_stacked_peaks_only()
    else:
        render_plot()



def _add_peak_row_base(checked=True, color=None, peaks_text="", label_text="", range_values=None):
    global next_color_index
    if color is None:
        color = default_peak_colors[next_color_index]
        next_color_index = (next_color_index + 1) % len(default_peak_colors)

    row_widget = QtWidgets.QWidget()
    row_widget.setAcceptDrops(True)
    row_layout = QtWidgets.QHBoxLayout()
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_widget.setLayout(row_layout)

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
    def pick():
        chosen = QtWidgets.QColorDialog.getColor(row_color[0], peaks_win, "Pick a color")
        if chosen.isValid():
            row_color[0] = chosen; update_btn(); render_plot()
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

    range_start = QtWidgets.QDoubleSpinBox()
    range_start.setRange(0.0, 100000.0); range_start.setDecimals(0)
    range_start.setSingleStep(1.0); range_start.setMinimumWidth(50)
    range_start.setToolTip("Start m/z")

    range_step = QtWidgets.QDoubleSpinBox()
    range_step.setRange(1.0, 10000.0); range_step.setDecimals(0)
    range_step.setSingleStep(1.0); range_step.setMinimumWidth(50)
    range_step.setValue(1.0); range_step.setToolTip("Step (interval)")

    range_end = QtWidgets.QDoubleSpinBox()
    range_end.setRange(0.0, 100000.0); range_end.setDecimals(0)
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
            return str(int(round(v))) if v == round(v) else f"{v:.4f}"
        return ", ".join(_fmt(v) for v in values)

    def _on_range_changed():
        peaks_input.blockSignals(True)
        peaks_input.setText(_range_to_peaks_text())
        peaks_input.blockSignals(False)
        _clear_highlight_cache()
        render_plot()

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
            render_plot()

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

    row_layout.addWidget(drag_handle)
    row_layout.addWidget(checkbox); row_layout.addWidget(btn)
    row_layout.addWidget(mode_btn)
    row_layout.addWidget(peaks_input)
    row_layout.addWidget(range_widget)
    row_layout.addWidget(label_input)
    zoom_btn = QtWidgets.QPushButton("⊙")
    zoom_btn.setFixedSize(22, 22)
    zoom_btn.setToolTip(
        "Zoom the plot to fit this peak list.\n"
        "Adjusts X range to span all peaks with padding.")

    row_layout.addWidget(zoom_btn)
    row_layout.addWidget(envelope_btn)
    row_layout.addWidget(remove_btn)

    row_data = {"widget": row_widget, "checkbox": checkbox,
            "color": row_color, "peaks_input": peaks_input, "label_input": label_input,
            "mode_btn": mode_btn, "envelope_btn": envelope_btn, "zoom_btn": zoom_btn,
            "range_start": range_start, "range_step": range_step, "range_end": range_end}

    def _zoom_to_this_row(_checked=False, _rd=row_data):
        peaks = parse_peaks_text(_rd["peaks_input"].text())
        if not peaks: return
        lo, hi = min(peaks), max(peaks)
        pad = max((hi - lo) * 0.15, 5.0)
        plot.vb.setXRange(lo - pad, hi + pad, padding=0)

    zoom_btn.clicked.connect(_zoom_to_this_row)

    # ── Drag reorder via drag_handle ──
    _drag_start = [None]

    def _handle_press(event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            _drag_start[0] = event.pos()

    def _handle_move(event):
        if not (_drag_start[0] and event.buttons() & QtCore.Qt.MouseButton.LeftButton): return
        if (event.pos() - _drag_start[0]).manhattanLength() < 8: return
        drag = QtGui.QDrag(row_widget)
        mime = QtCore.QMimeData()
        idx  = custom_peak_rows.index(row_data) if row_data in custom_peak_rows else -1
        mime.setText(str(idx)); drag.setMimeData(mime)
        drag.exec(QtCore.Qt.DropAction.MoveAction)

    def _widget_drag_enter(event):
        if event.mimeData().hasText(): event.acceptProposedAction()

    def _widget_drop(event):
        try: from_idx = int(event.mimeData().text())
        except ValueError: return
        to_idx = custom_peak_rows.index(row_data) if row_data in custom_peak_rows else -1
        if from_idx >= 0 and to_idx >= 0 and from_idx != to_idx:
            peaks_rows_container.move_row(from_idx, to_idx)
        event.acceptProposedAction()

    drag_handle.mousePressEvent = _handle_press
    drag_handle.mouseMoveEvent  = _handle_move
    row_widget.dragEnterEvent   = _widget_drag_enter
    row_widget.dropEvent        = _widget_drop

    def on_remove():
        custom_peak_rows.remove(row_data)
        peaks_rows_layout_remove(row_widget)
        _clear_highlight_cache()
        update_pick_row_combo(); render_plot()
        _sync_confirmation_panel()

    remove_btn.clicked.connect(on_remove)
    checkbox.stateChanged.connect(lambda _: _render_peaks_or_full())
    envelope_btn.toggled.connect(lambda _: render_plot())
    peaks_input.editingFinished.connect(push_peak_history)
    peaks_input.textChanged.connect(lambda _: _render_peaks_or_full())
    label_input.textChanged.connect(lambda _: update_pick_row_combo())
    label_input.textChanged.connect(lambda _: _render_peaks_or_full())
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

def _load_peak_file_into_rows(path):
    push_peak_history()
    for row in custom_peak_rows[:]:
        peaks_rows_layout_remove(row["widget"]); custom_peak_rows.remove(row)
    with open(path, "r") as f: data = json.load(f)
    for item in data:
        if item.get("mode") == "range":
            _add_peak_row_base(
                checked=item.get("checked", True),
                color=QtGui.QColor(item.get("color", "#ff0000")),
                label_text=item.get("label", ""),
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
                label_text=item.get("label", ""))
    _clear_highlight_cache()
    update_pick_row_combo(); render_plot()
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
pw_toolbar.addSpacing(8); pw_toolbar.addWidget(add_btn)
pw_toolbar.addSpacing(6)
pw_toolbar.addWidget(pw_select_all_btn); pw_toolbar.addWidget(pw_select_none_btn)
pw_export_csv_btn = QtWidgets.QPushButton("Export CSV")
pw_export_csv_btn.setFixedHeight(24)
pw_export_csv_btn.setToolTip("Export highlighted peak data as CSV")
pw_export_csv_btn.clicked.connect(lambda: export_peaks_csv())
pw_toolbar.addStretch()
pw_toolbar.addWidget(pw_export_csv_btn)
pw_toolbar.addWidget(peaks_help_btn)
peaks_layout.addLayout(pw_toolbar)

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

# ── Label/mass toggles - two compact rows ──
pk_toggle_row1 = QtWidgets.QHBoxLayout()
pk_toggle_row1.addWidget(peak_labels_toggle)
pk_toggle_row1.addWidget(peak_masses_toggle)
pk_toggle_row1.addStretch()
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
from droplet.ui.windows.peak_confirmation import ConfirmationPanel as _ConfPanelCls

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
            for row in custom_peak_rows[:]:
                peaks_rows_layout_remove(row["widget"])
                custom_peak_rows.remove(row)
            with open(last_peak_file, "r") as f: data = json.load(f)
            for item in data:
                if item.get("mode") == "range":
                    _add_peak_row_base(
                        checked=item.get("checked", True),
                        color=QtGui.QColor(item.get("color", "#ff0000")),
                        label_text=item.get("label", ""),
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
                        label_text=item.get("label", ""))
            update_pick_row_combo()
        except Exception as e:
            QtWidgets.QMessageBox.warning(main_win, "Load Failed",
                f"Failed to auto-load peaks from {last_peak_file}:\n{e}")

# ─────────────────────────────────────────────
#  Peak list import / export
# ─────────────────────────────────────────────
def _update_peaks_win_title(path=None):
    if path:
        peaks_win.setWindowTitle(f"Peaks  —  {os.path.basename(path)}")
    else:
        peaks_win.setWindowTitle("Peaks")

def export_peak_list():
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        peaks_win, "Export Peak List", _get_dialog_dir("peak_list"), "JSON Files (*.json)")
    if path: _set_dialog_dir("peak_list", path)

    if not path:
        return

    # Ensure .json extension
    if not path.lower().endswith(".json"):
        path += ".json"

    settings.setValue("last_peak_file", path)
    _update_peaks_win_title(path)

    data = []
    for r in custom_peak_rows:
        entry = {
            "checked": r["checkbox"].isChecked(),
            "color":   r["color"][0].name(),
            "label":   r["label_input"].text(),
        }
        if r.get("mode_btn") and r["mode_btn"].isChecked():
            entry["mode"]        = "range"
            entry["range_start"] = r["range_start"].value()
            entry["range_step"]  = r["range_step"].value()
            entry["range_end"]   = r["range_end"].value()
        else:
            entry["mode"]  = "manual"
            entry["peaks"] = r["peaks_input"].text()
        data.append(entry)

    with open(path, "w") as f:
        json.dump(data, f, indent=2)

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
auto_load_peaks()

# ─────────────────────────────────────────────
#  Peak undo/redo  (text edits only)
# ─────────────────────────────────────────────
def _snapshot_peaks():
    return {
        "rows": [{"checked": r["checkbox"].isChecked(),
                  "color":   r["color"][0].name(),
                  "peaks":   r["peaks_input"].text(),
                  "label":   r["label_input"].text()}
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
        for child in row["widget"].children():
            if isinstance(child, QtWidgets.QPushButton) and child.minimumWidth() == 22:
                child.setStyleSheet(f"background-color: {c.name()}; border: 1px solid gray;"); break
    _history_locked = False
    _clear_highlight_cache()
    update_pick_row_combo()
    if isinstance(snapshot, dict) and "pick_row" in snapshot:
        pick_row_combo.setCurrentIndex(snapshot["pick_row"])
    render_plot()
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

# ─────────────────────────────────────────────
#  Display mode
# ─────────────────────────────────────────────
def set_display_mode(mode):
    global current_display
    current_display = mode
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
    if plot.sceneBoundingRect().contains(pos):
        mp = plot.vb.mapSceneToView(pos)
        x = mp.x()
        y = 10 ** mp.y() * 10**3
        if crosshair_action.isChecked():
            v_line.setPos(x)
            h_line.setPos(mp.y())
        current_label = ""
        for entry in highlighted_ranges:
            mz_min, mz_max, peak_label = entry[0], entry[1], entry[2]
            if mz_min - 0.5 <= x <= mz_max + 0.5:
                current_label = peak_label; break
        hover_label.setText(
            f"m/z={x:.4f}  I={y:.2e}" +
            (f"<br>{current_label}" if current_label else ""))

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
    v_line.setVisible(checked)
    h_line.setVisible(checked)
    for sub in _stacked_sub_plots:
        for item in sub.items:
            if getattr(item, '_is_crosshair', False):
                item.setVisible(checked)

plot.vb.sigRangeChanged.connect(lambda *_: _minimap.schedule_rect_update())
crosshair_action.toggled.connect(_toggle_crosshair)
grid_action.toggled.connect(lambda checked: (
    settings.setValue("main_grid", checked),
    plot.showGrid(x=checked, y=checked, alpha=0.3)))

# ─────────────────────────────────────────────
#  View / remaining action connections
# ─────────────────────────────────────────────
dyn_scale_action.toggled.connect(lambda _: render_plot())
subtract_action.toggled.connect(lambda _: render_plot())
subtract_dyn_action.toggled.connect(lambda _: render_plot())
highlight_slider.valueChanged.connect(lambda _: render_plot())
overlay_opacity_slider.valueChanged.connect(lambda _: render_plot())

def _apply_legend_font(pt=None):
    if pt is None:
        pt = legend_font_spin.value()
    legend.setLabelTextSize(f"{pt}pt")
    settings.setValue("legend_font_pt", pt)

legend_font_spin.valueChanged.connect(_apply_legend_font)
# Apply saved value immediately (called after legend exists)
QtCore.QTimer.singleShot(0, _apply_legend_font)
main_toggle.stateChanged.connect(lambda _: render_plot())
peak_labels_toggle.stateChanged.connect(lambda _: render_plot())
peak_masses_toggle.stateChanged.connect(lambda _: render_plot())
auto_peaks_toggle.stateChanged.connect(lambda _: render_plot())
mass_threshold_spin.valueChanged.connect(lambda _: render_plot())
peak_masses_all_toggle.stateChanged.connect(lambda _: render_plot())
threshold_mode_combo.currentIndexChanged.connect(lambda _: (_clear_auto_peaks_cache(), render_plot()))
show_integers_toggle.stateChanged.connect(lambda v: (
    settings.setValue("show_integers", bool(v)), render_plot()))

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
    if _stacked_log_y:
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
def export_plot_png():
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Export Plot as PNG", _get_dialog_dir("export_plot"), "PNG Images (*.png)")
    if path: _set_dialog_dir("export_plot", path)
    if not path: return
    if not path.lower().endswith(".png"): path += ".png"
    from pyqtgraph.exporters import ImageExporter
    EXPORT_WIDTH = 4000
    screen_pt    = legend_font_spin.value()
    export_pt    = max(8, int(screen_pt * EXPORT_WIDTH / max(plot_widget.width(), 1)))
    legend.setLabelTextSize(f"{export_pt}pt")
    def _do():
        try:
            exp = ImageExporter(plot)
            exp.parameters()['width'] = EXPORT_WIDTH
            exp.export(path)
        finally:
            legend.setLabelTextSize(f"{screen_pt}pt")
    _export_with_colored_labels(_do, export_scale=70.0)

def export_plot_svg():
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Export Plot as SVG", _get_dialog_dir("export_plot"), "SVG Files (*.svg)")
    if path: _set_dialog_dir("export_plot", path)
    if not path: return
    if not path.lower().endswith(".svg"): path += ".svg"
    from pyqtgraph.exporters import SVGExporter
    screen_pt = legend_font_spin.value()
    export_pt = max(8, int(screen_pt * 4.0))   # SVG is resolution-independent; 4× is readable
    legend.setLabelTextSize(f"{export_pt}pt")
    def _do():
        try:
            exp = SVGExporter(plot)
            exp.export(path)
        finally:
            legend.setLabelTextSize(f"{screen_pt}pt")
    _export_with_colored_labels(_do, export_scale=70.0)

def export_plot_pdf():
    path, _ = QtWidgets.QFileDialog.getSaveFileName(
        main_win, "Export Plot as PDF", _get_dialog_dir("export_plot"), "PDF Files (*.pdf)")
    if path: _set_dialog_dir("export_plot", path)
    if not path: return
    if not path.lower().endswith(".pdf"): path += ".pdf"
    from pyqtgraph.exporters import ImageExporter
    screen_pt = legend_font_spin.value()
    export_pt = max(8, int(screen_pt * 1920 / max(plot_widget.width(), 1)))
    legend.setLabelTextSize(f"{export_pt}pt")
    def _do():
        try:
            exp = ImageExporter(plot)
            exp.parameters()['width'] = 1920
            img = exp.export(toBytes=True)
            if img is None:
                img = plot_widget.grab().toImage()
            if not isinstance(img, QtGui.QImage):
                img = QtGui.QImage(img)
            writer = QtGui.QPdfWriter(path)
            writer.setPageOrientation(QtGui.QPageLayout.Orientation.Landscape)
            writer.setPageSize(QtGui.QPageSize(QtGui.QPageSize.PageSizeId.A4))
            writer.setPageMargins(QtCore.QMarginsF(10, 10, 10, 10),
                                  QtGui.QPageLayout.Unit.Millimeter)
            writer.setResolution(150)
            painter = QtGui.QPainter(writer)
            page_rect = painter.viewport()
            img_size  = img.size().scaled(page_rect.size(),
                                          QtCore.Qt.AspectRatioMode.KeepAspectRatio)
            x_off = (page_rect.width()  - img_size.width())  // 2
            y_off = (page_rect.height() - img_size.height()) // 2
            painter.drawImage(QtCore.QRect(x_off, y_off, img_size.width(), img_size.height()), img)
            painter.end()
        finally:
            legend.setLabelTextSize(f"{screen_pt}pt")
    _export_with_colored_labels(_do, export_scale=70.0)

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
    pixmap = plot_widget.grab()
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
open_folder_action.triggered.connect(lambda: open_folder())
open_files_action.triggered.connect(open_individual_files)
refresh_action.triggered.connect(refresh_current)
refresh_btn.clicked.connect(refresh_current)
dark_action.triggered.connect(lambda: set_display_mode("dark"))
bright_action.triggered.connect(lambda: set_display_mode("bright"))
fmt_combo.currentIndexChanged.connect(
    lambda _: plot_file(combo.currentData() or combo.currentText()))

def cleanup():
    settings.setValue("base_dir", base_dir)
    save_session_state()

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


# ═══════════════════════════════════════════════════════════════
#  Plotting tool - built-in visual themes
# ═══════════════════════════════════════════════════════════════
_PT_THEMES = {
    "Default (matplotlib)": {},   # no overrides - pure matplotlib defaults

    "Clean White": {
        "fig_bg":        "#ffffff",
        "ax_bg":         "#ffffff",
        "spine_color":   "#444444",
        "spine_width":   1.2,
        "spine_visible": ("bottom", "left"),   # hide top & right
        "tick_color":    "#444444",
        "tick_direction":"out",
        "tick_length":   4,
        "tick_width":    0.9,
        "grid":          True,
        "grid_color":    "#e0e0e0",
        "grid_alpha":    0.8,
        "grid_style":    "-",
        "grid_which":    "major",
        "label_color":   "#222222",
        "title_color":   "#222222",
    },

    "Publication (Nature)": {
        "fig_bg":        "#ffffff",
        "ax_bg":         "#ffffff",
        "spine_color":   "#000000",
        "spine_width":   1.5,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#000000",
        "tick_direction":"out",
        "tick_length":   5,
        "tick_width":    1.2,
        "grid":          False,
        "label_color":   "#000000",
        "title_color":   "#000000",
        "pad_inches":    0.05,
    },

    "Dark Lab": {
        "fig_bg":        "#1a1a2e",
        "ax_bg":         "#16213e",
        "spine_color":   "#4a9eff",
        "spine_width":   1.0,
        "spine_visible": ("bottom", "left", "top", "right"),
        "tick_color":    "#c0c8d8",
        "tick_direction":"in",
        "tick_length":   4,
        "tick_width":    0.8,
        "grid":          True,
        "grid_color":    "#2a3a5e",
        "grid_alpha":    1.0,
        "grid_style":    "-",
        "grid_which":    "major",
        "label_color":   "#c0c8d8",
        "title_color":   "#e0e8ff",
    },

    "Seaborn Minimal": {
        "fig_bg":        "#f8f8f8",
        "ax_bg":         "#f8f8f8",
        "spine_color":   "#bbbbbb",
        "spine_width":   0.8,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#888888",
        "tick_direction":"out",
        "tick_length":   3,
        "tick_width":    0.7,
        "grid":          True,
        "grid_color":    "#dddddd",
        "grid_alpha":    1.0,
        "grid_style":    "-",
        "grid_which":    "major",
        "label_color":   "#444444",
        "title_color":   "#333333",
        "despine_offset": 6,   # offset spines outward (seaborn style)
    },

    "Blueprint": {
        "fig_bg":        "#0d2137",
        "ax_bg":         "#0d2137",
        "spine_color":   "#4fc3f7",
        "spine_width":   0.8,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#b0c4de",
        "tick_direction":"out",
        "tick_length":   4,
        "tick_width":    0.8,
        "grid":          True,
        "grid_color":    "#1a3a5c",
        "grid_alpha":    1.0,
        "grid_style":    "--",
        "grid_which":    "both",
        "label_color":   "#b0c4de",
        "title_color":   "#e0f0ff",
    },

    "Warm Parchment": {
        "fig_bg":        "#fdf6e3",
        "ax_bg":         "#fdf6e3",
        "spine_color":   "#8b7355",
        "spine_width":   1.2,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#8b7355",
        "tick_direction":"out",
        "tick_length":   4,
        "tick_width":    0.9,
        "grid":          True,
        "grid_color":    "#e8dcc8",
        "grid_alpha":    1.0,
        "grid_style":    "-",
        "grid_which":    "major",
        "label_color":   "#5c4a2a",
        "title_color":   "#4a3520",
    },

    # ── Thick-frame, no grid - heavyweight print look
    "Bold Print": {
        "fig_bg":        "#ffffff",
        "ax_bg":         "#ffffff",
        "spine_color":   "#000000",
        "spine_width":   2.5,
        "spine_visible": ("bottom", "left", "top", "right"),
        "tick_color":    "#000000",
        "tick_direction":"in",
        "tick_length":   7,
        "tick_width":    1.8,
        "grid":          False,
        "label_color":   "#000000",
        "title_color":   "#000000",
    },

    # ── Light gray axes, fine dotted grid - clean analytical look
    "Analyst": {
        "fig_bg":        "#f4f4f4",
        "ax_bg":         "#ffffff",
        "spine_color":   "#999999",
        "spine_width":   0.7,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#666666",
        "tick_direction":"out",
        "tick_length":   3,
        "tick_width":    0.6,
        "grid":          True,
        "grid_color":    "#bbbbbb",
        "grid_alpha":    0.6,
        "grid_style":    ":",
        "grid_which":    "both",
        "label_color":   "#333333",
        "title_color":   "#222222",
        "despine_offset": 4,
    },

    # ── High-contrast black axes on white, outward ticks, no grid - poster/slide ready
    "High Contrast": {
        "fig_bg":        "#ffffff",
        "ax_bg":         "#ffffff",
        "spine_color":   "#000000",
        "spine_width":   2.0,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#000000",
        "tick_direction":"out",
        "tick_length":   6,
        "tick_width":    1.5,
        "grid":          False,
        "label_color":   "#000000",
        "title_color":   "#000000",
    },

    # ── Muted green-teal tones, inward ticks, subtle dashed grid
    "Forest": {
        "fig_bg":        "#f0f4f1",
        "ax_bg":         "#f0f4f1",
        "spine_color":   "#2e6b4f",
        "spine_width":   1.3,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#2e6b4f",
        "tick_direction":"in",
        "tick_length":   5,
        "tick_width":    1.0,
        "grid":          True,
        "grid_color":    "#b5d0c0",
        "grid_alpha":    0.7,
        "grid_style":    "--",
        "grid_which":    "major",
        "label_color":   "#1a3d2b",
        "title_color":   "#12291c",
    },

    # ── Deep charcoal, warm off-white text, no grid - slide presentation
    "Slate": {
        "fig_bg":        "#2b2b2b",
        "ax_bg":         "#2b2b2b",
        "spine_color":   "#d0d0d0",
        "spine_width":   1.2,
        "spine_visible": ("bottom", "left"),
        "tick_color":    "#d0d0d0",
        "tick_direction":"out",
        "tick_length":   4,
        "tick_width":    0.9,
        "grid":          False,
        "label_color":   "#eeeeee",
        "title_color":   "#ffffff",
    },
}



# ═══════════════════════════════════════════════════════════════
#  PlottingToolWindow
#  Full-featured, standalone plotting environment embedded in a
#  QWidget the same size as the main window.
# ═══════════════════════════════════════════════════════════════
class PlottingToolWindow(QtWidgets.QWidget):
    """
    Opens as a separate top-level window (same initial geometry as main_win).
    Left panel  = canvas (matplotlib figure).
    Right panel = tabbed control dock (Appearance / Peaks / Annotations / Legend).
    Bottom bar  = recurrent header-variable editor.
    """

    # ── project file format version ──────────────────────────
    PROJECT_VERSION = 1

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Droplet - Plotting Tool")

        # Match main window geometry
        geo = main_win.geometry()
        self.setGeometry(geo)

        # ── internal state ───────────────────────────────────
        self._spectra       = []          # list of dicts (see _load_current_spectra)
        self._peak_labels   = []          # list of PlotLabel objects
        self._annotations   = []          # text / arrow / symbol / zone items
        self._area_fills    = []          # FillBetween patches
        self._label_offset  = 0.0        # global up/down offset for all labels (in data units)
        self._header_vars   = {}          # {key: value} from bottom bar
        self._saved_plots      = []       # list of {"name": str, "data": dict}
        self._current_plot_idx = -1       # -1 = unsaved / new

        self._build_ui()
        self._restore_ui_settings()   # ← restore before first draw
        self._load_current_spectra()
        self._force_autoscale = True
        self._draw()
        # Trigger a deferred resize so matplotlib fills the canvas correctly
        QtCore.QTimer.singleShot(80, lambda: (
            self.canvas.figure.tight_layout(),
            self.canvas.draw_idle()
        ))

    @staticmethod
    def _section(text, color="#3a6ea5"):
        """Full-width coloured header for QFormLayout sections (Appearance tab)."""
        lbl = QtWidgets.QLabel(f"  {text}")
        lbl.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)
        lbl.setStyleSheet(
            f"QLabel {{ background-color:{color}; color:white; "
            f"font-weight:bold; font-size:11px; "
            f"padding:3px 6px; border-radius:3px; }}")
        return lbl

    @staticmethod
    def _style_group(grp, color):
        """
        Apply a coloured title bar to a QGroupBox.
        Call after constructing the group, before adding widgets.
        Colours per tab:
          Appearance : #3a6ea5  (blue)
          Peaks      : #2e7d32  (green)
          Annotations: #7b3f9e  (purple)
          Legend     : #b25000  (orange)
        """
        grp.setStyleSheet(
            f"QGroupBox {{ "
            f"  border: 1px solid {color}; "
            f"  border-radius: 4px; "
            f"  margin-top: 8px; "
            f"  font-weight: bold; "
            f"}} "
            f"QGroupBox::title {{ "
            f"  subcontrol-origin: margin; "
            f"  subcontrol-position: top left; "
            f"  padding: 2px 6px; "
            f"  background-color: {color}; "
            f"  color: white; "
            f"  border-radius: 3px; "
            f"}}")

    @staticmethod
    def _group_header(text, color, style="solid"):
        """
        Return a styled header widget for QGroupBox titles.

        style='solid'    → plain filled bar            (Appearance tab)
        style='grid'     → cross-hatched pattern fill  (Peaks tab)
        style='hlines'   → horizontal stripes          (Annotations tab)
        style='vlines'   → vertical stripes            (Legend tab)
        """
        lbl = QtWidgets.QLabel(f"  {text}")
        lbl.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed)

        # Convert hex color to rgba for background-image tricks
        if style == "solid":
            css = (f"QLabel {{ background-color:{color}; color:white; "
                   f"font-weight:bold; font-size:11px; "
                   f"padding:3px 6px; border-radius:3px; }}")

        elif style == "grid":
            # Checkerboard/grid via repeating-linear-gradient (Qt supports this)
            css = (f"QLabel {{ "
                   f"  background-color: {color}; "
                   f"  background-image: repeating-linear-gradient("
                   f"    0deg, rgba(255,255,255,0.15) 0px, rgba(255,255,255,0.15) 2px,"
                   f"    transparent 2px, transparent 8px),"
                   f"    repeating-linear-gradient("
                   f"    90deg, rgba(255,255,255,0.15) 0px, rgba(255,255,255,0.15) 2px,"
                   f"    transparent 2px, transparent 8px); "
                   f"  color: white; font-weight: bold; font-size: 11px; "
                   f"  padding: 3px 6px; border-radius: 3px; }}")

        elif style == "hlines":
            css = (f"QLabel {{ "
                   f"  background-color: {color}; "
                   f"  background-image: repeating-linear-gradient("
                   f"    0deg, rgba(255,255,255,0.20) 0px, rgba(255,255,255,0.20) 2px,"
                   f"    transparent 2px, transparent 6px); "
                   f"  color: white; font-weight: bold; font-size: 11px; "
                   f"  padding: 3px 6px; border-radius: 3px; }}")

        elif style == "vlines":
            css = (f"QLabel {{ "
                   f"  background-color: {color}; "
                   f"  background-image: repeating-linear-gradient("
                   f"    90deg, rgba(255,255,255,0.20) 0px, rgba(255,255,255,0.20) 2px,"
                   f"    transparent 2px, transparent 6px); "
                   f"  color: white; font-weight: bold; font-size: 11px; "
                   f"  padding: 3px 6px; border-radius: 3px; }}")
        else:
            css = (f"QLabel {{ background-color:{color}; color:white; "
                   f"font-weight:bold; font-size:11px; padding:3px 6px; }}")

        lbl.setStyleSheet(css)
        return lbl

    def _save_ui_settings(self):
        """Persist all UI settings (except manual annotations) between sessions."""
        s = settings
        # ── Appearance ─────────────────────────────────────────
        s.setValue("pt/xlabel",         self.xlabel_edit.text())
        s.setValue("pt/ylabel",         self.ylabel_edit.text())
        s.setValue("pt/axes_label_size",self.axes_label_size.value())
        s.setValue("pt/tick_size",      self.tick_size.value())
        s.setValue("pt/font",           self.font_combo.currentFont().family())
        s.setValue("pt/grid",           self.grid_cb.isChecked())
        s.setValue("pt/grid_alpha",     self.grid_alpha_spin.value())
        s.setValue("pt/grid_style",     self.grid_style_combo.currentText())
        s.setValue("pt/logy",           self.logy_cb.isChecked())
        s.setValue("pt/xmin",           self.xmin_edit.text())
        s.setValue("pt/xmax",           self.xmax_edit.text())
        s.setValue("pt/ymin",           self.ymin_edit.text())
        s.setValue("pt/ymax",           self.ymax_edit.text())
        s.setValue("pt/spine_top",      self.spine_top_cb.isChecked())
        s.setValue("pt/spine_right",    self.spine_right_cb.isChecked())
        s.setValue("pt/spine_bottom",   self.spine_bottom_cb.isChecked())
        s.setValue("pt/spine_left",     self.spine_left_cb.isChecked())
        s.setValue("pt/minor_ticks",    self.minor_ticks_cb.isChecked())
        s.setValue("pt/minor_tick_size",self.minor_tick_size.value())
        s.setValue("pt/title",          self.title_edit.text())
        s.setValue("pt/title_size",     self.title_size.value())
        s.setValue("pt/fig_w",          self.fig_w_spin.value())
        s.setValue("pt/fig_h",          self.fig_h_spin.value())
        s.setValue("pt/export_dpi",     self.export_dpi_spin.value())
        s.setValue("pt/offset",         self.offset_spin.value())
        s.setValue("pt/norm",           self.norm_combo.currentText())
        s.setValue("pt/mirror_pairs",   self.mirror_pairs_cb.isChecked())
        s.setValue("pt/mirror_odd",     self.mirror_odd_cb.isChecked())
        s.setValue("pt/bg_color",       self._bg_color)
        s.setValue("pt/clip_to_axes",   self.clip_to_axes_cb.isChecked())
        s.setValue("pt/sigma_clip",     self.sigma_clip_cb.isChecked())
        s.setValue("pt/sigma_n_sigma",  self.sigma_slider.value())
        s.setValue("pt/watermark",      self.watermark_edit.text())
        s.setValue("pt/watermark_alpha",self.watermark_alpha_spin.value())
        # ── Peaks ──────────────────────────────────────────────
        s.setValue("pt/label_font",     self.label_font_combo.currentFont().family())
        s.setValue("pt/label_fontsize", self.label_fontsize_spin.value())
        s.setValue("pt/label_angle",    self.label_angle_spin.value())
        s.setValue("pt/label_color",         self._label_color)
        s.setValue("pt/label_use_row_color",  self.label_use_row_color_cb.isChecked())
        s.setValue("pt/label_mass_black",     self.label_mass_black_cb.isChecked())
        s.setValue("pt/label_use_black_masses", self.label_use_black_masses_cb.isChecked())
        s.setValue("pt/auto_label",     self.auto_label_cb.isChecked())
        s.setValue("pt/label_thr_mode", self.label_thr_mode_combo.currentText())
        s.setValue("pt/label_thr",      self.label_threshold_spin.value())
        s.setValue("pt/label_int",      self.label_integer_cb.isChecked())
        s.setValue("pt/label_highest",  self.label_highest_cb.isChecked())
        s.setValue("pt/show_spans",            self.show_peak_spans_cb.isChecked())
        s.setValue("pt/peak_list_labels",      self.peak_list_labels_cb.isChecked())
        s.setValue("pt/label_overlap_stack",   self.label_overlap_stack_cb.isChecked())
        s.setValue("pt/span_width",     self.peak_span_width_spin.value())
        # ── Legend ─────────────────────────────────────────────
        s.setValue("pt/legend",         self.legend_cb.isChecked())
        s.setValue("pt/legend_pos",     self.legend_pos.currentText())
        s.setValue("pt/legend_fontsize",self.legend_fontsize.value())
        s.setValue("pt/legend_frame",   self.legend_frame_cb.isChecked())
        s.setValue("pt/legend_ncol",        self.legend_ncol_spin.value())
        s.setValue("pt/legend_title",       self.legend_title_edit.text())
        s.setValue("pt/legend_title_size",  self.legend_title_size.value())
        s.setValue("pt/legend_fancybox",    self.legend_fancybox_cb.isChecked())
        s.setValue("pt/legend_shadow",      self.legend_shadow_cb.isChecked())
        s.setValue("pt/legend_alpha",       self.legend_alpha_spin.value())
        s.setValue("pt/legend_edge_color",  self._legend_edge_color)
        s.setValue("pt/legend_labelspacing",self.legend_labelspacing_spin.value())
        s.setValue("pt/legend_handlelength",self.legend_handlelength_spin.value())
        s.setValue("pt/legend_borderpad",   self.legend_borderpad_spin.value())

        s.setValue("pt/theme", self.theme_combo.currentText())

    def _restore_ui_settings(self):
        """Reload all persisted settings after widgets are built."""
        s = settings
        # ── Appearance ─────────────────────────────────────────
        self.xlabel_edit.setText(        s.value("pt/xlabel",          "m/z"))
        self.ylabel_edit.setText(        s.value("pt/ylabel",          "Intensity"))
        self.axes_label_size.setValue(   s.value("pt/axes_label_size", 13,    type=int))
        self.tick_size.setValue(         s.value("pt/tick_size",       11,    type=int))
        _saved_font = s.value("pt/font", "DejaVu Sans")
        if _saved_font not in QtGui.QFontDatabase.families():
            _saved_font = "DejaVu Sans"
            s.setValue("pt/font", _saved_font)
        self.font_combo.setCurrentFont(QtGui.QFont(_saved_font))
        self.grid_cb.setChecked(         s.value("pt/grid",            False, type=bool))
        self.grid_alpha_spin.setValue(   s.value("pt/grid_alpha",      0.4,   type=float))
        idx = self.grid_style_combo.findText(s.value("pt/grid_style", "--  dashed"))
        if idx >= 0: self.grid_style_combo.setCurrentIndex(idx)
        self.logy_cb.setChecked(         s.value("pt/logy",            False, type=bool))
        self.xmin_edit.setText(          s.value("pt/xmin",            ""))
        self.xmax_edit.setText(          s.value("pt/xmax",            ""))
        self.ymin_edit.setText(          s.value("pt/ymin",            ""))
        self.ymax_edit.setText(          s.value("pt/ymax",            ""))
        self.spine_top_cb.setChecked(    s.value("pt/spine_top",       False, type=bool))
        self.spine_right_cb.setChecked(  s.value("pt/spine_right",     False, type=bool))
        self.spine_bottom_cb.setChecked( s.value("pt/spine_bottom",    True,  type=bool))
        self.spine_left_cb.setChecked(   s.value("pt/spine_left",      True,  type=bool))
        self.minor_ticks_cb.setChecked(  s.value("pt/minor_ticks",     False, type=bool))
        self.minor_tick_size.setValue(   s.value("pt/minor_tick_size", 3,     type=int))
        self.title_edit.setText(         s.value("pt/title",           ""))
        self.title_size.setValue(        s.value("pt/title_size",      13,    type=int))
        self.fig_w_spin.setValue(        s.value("pt/fig_w",           1800,  type=int))
        self.fig_h_spin.setValue(        s.value("pt/fig_h",           1200,  type=int))
        self.export_dpi_spin.setValue(   s.value("pt/export_dpi",      500,   type=int))
        self.offset_spin.setValue(       s.value("pt/offset",          0.0,   type=float))
        norm_idx = self.norm_combo.findText(s.value("pt/norm", "None"))
        if norm_idx >= 0: self.norm_combo.setCurrentIndex(norm_idx)
        self.mirror_pairs_cb.setChecked( s.value("pt/mirror_pairs",    False, type=bool))
        self.mirror_odd_cb.setChecked(   s.value("pt/mirror_odd",      False, type=bool))
        self._bg_color = s.value("pt/bg_color", "#ffffff")
        self.clip_to_axes_cb.setChecked( s.value("pt/clip_to_axes",    False, type=bool))
        _sc = s.value("pt/sigma_clip", False, type=bool)
        self.sigma_clip_cb.setChecked(_sc)
        self.sigma_slider.setValue(      s.value("pt/sigma_n_sigma",   10,    type=int))
        self.sigma_slider.setEnabled(_sc)
        self.sigma_label.setEnabled(_sc)
        self.watermark_edit.setText(     s.value("pt/watermark",       ""))
        self.watermark_alpha_spin.setValue(s.value("pt/watermark_alpha", 0.12, type=float))
        # ── Peaks ──────────────────────────────────────────────
        _saved_label_font = s.value("pt/label_font", "DejaVu Sans")
        if _saved_label_font not in QtGui.QFontDatabase.families():
            _saved_label_font = "DejaVu Sans"
            s.setValue("pt/label_font", _saved_label_font)
        self.label_font_combo.setCurrentFont(QtGui.QFont(_saved_label_font))
        self.label_fontsize_spin.setValue(s.value("pt/label_fontsize", 9,     type=int))
        self.label_angle_spin.setValue(  s.value("pt/label_angle",     90,    type=int))
        self._label_color = s.value("pt/label_color", "#222222")
        if hasattr(self, "label_color_btn"):
            self.label_color_btn.setStyleSheet(f"background:{self._label_color};")
        if hasattr(self, "label_use_row_color_cb"):
            self.label_use_row_color_cb.setChecked(
                s.value("pt/label_use_row_color", True, type=bool))
        if hasattr(self, "label_mass_black_cb"):
            self.label_mass_black_cb.setChecked(
                s.value("pt/label_mass_black", False, type=bool))
        if hasattr(self, "label_use_black_masses_cb"):
            self.label_use_black_masses_cb.setChecked(
                s.value("pt/label_use_black_masses", False, type=bool))
        self.auto_label_cb.setChecked(   s.value("pt/auto_label",      False, type=bool))
        m_idx = self.label_thr_mode_combo.findText(s.value("pt/label_thr_mode", "% of max intensity"))
        if m_idx >= 0: self.label_thr_mode_combo.setCurrentIndex(m_idx)
        self.label_threshold_spin.setValue(s.value("pt/label_thr",     5.0,   type=float))
        self.label_integer_cb.setChecked(s.value("pt/label_int",       True,  type=bool))
        self.label_highest_cb.setChecked(s.value("pt/label_highest",   True,  type=bool))
        self.show_peak_spans_cb.setChecked(       s.value("pt/show_spans",           True,  type=bool))
        self.peak_list_labels_cb.setChecked(      s.value("pt/peak_list_labels",     False, type=bool))
        self.label_overlap_stack_cb.setChecked(   s.value("pt/label_overlap_stack",  False, type=bool))
        self.peak_span_width_spin.setValue( s.value("pt/span_width",   1.0,   type=float))
        # ── Legend ─────────────────────────────────────────────
        self.legend_cb.setChecked(       s.value("pt/legend",          False, type=bool))
        leg_idx = self.legend_pos.findText(s.value("pt/legend_pos", "upper right"))
        if leg_idx >= 0: self.legend_pos.setCurrentIndex(leg_idx)
        self.legend_fontsize.setValue(   s.value("pt/legend_fontsize", 11,    type=int))
        self.legend_frame_cb.setChecked( s.value("pt/legend_frame",    True,  type=bool))
        self.legend_ncol_spin.setValue(       s.value("pt/legend_ncol",         1,       type=int))
        self.legend_title_edit.setText(       s.value("pt/legend_title",        ""))
        self.legend_title_size.setValue(      s.value("pt/legend_title_size",   11,      type=int))
        self.legend_fancybox_cb.setChecked(   s.value("pt/legend_fancybox",     True,    type=bool))
        self.legend_shadow_cb.setChecked(     s.value("pt/legend_shadow",       False,   type=bool))
        self.legend_alpha_spin.setValue(      s.value("pt/legend_alpha",        0.92,    type=float))
        self._legend_edge_color = s.value("pt/legend_edge_color", "#cccccc")
        self.legend_edge_btn.setStyleSheet(   f"background-color:{self._legend_edge_color};")
        self.legend_labelspacing_spin.setValue(s.value("pt/legend_labelspacing", 0.5,   type=float))
        self.legend_handlelength_spin.setValue(s.value("pt/legend_handlelength", 1.5,   type=float))
        self.legend_borderpad_spin.setValue(  s.value("pt/legend_borderpad",    0.5,    type=float))

        t_idx = self.theme_combo.findText(s.value("pt/theme", "Clean White"))
        if t_idx >= 0: self.theme_combo.setCurrentIndex(t_idx)

    def _build_ui(self):
        # Size to match main window exactly
        geo = main_win.geometry()
        self.resize(geo.width(), geo.height())

        # ── Menu bar ─────────────────────────────────────────
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        mb = self._build_menubar()
        outer.addWidget(mb)
        self._plot_switcher_bar = self._build_plot_switcher_bar()
        outer.addWidget(self._plot_switcher_bar)

        # ── Content row below menu bar ────────────────────────
        content = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        outer.addWidget(content, stretch=1)

        # ── LEFT: canvas ─────────────────────────────────────
        canvas_panel = QtWidgets.QWidget()
        cv_lay = QtWidgets.QVBoxLayout(canvas_panel)
        cv_lay.setContentsMargins(0, 0, 0, 0)

        self.fig, self.ax = plt.subplots(figsize=(9, 6), dpi=100)
        self.fig.patch.set_alpha(0)
        self.canvas = FigureCanvas(self.fig)
        self.canvas.setMinimumWidth(400)
        self.canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)

        # matplotlib navigation toolbar + refresh button on same row
        tb_row = QtWidgets.QWidget()
        tb_lay = QtWidgets.QHBoxLayout(tb_row)
        tb_lay.setContentsMargins(0, 0, 0, 0)
        tb_lay.setSpacing(4)
        self.mpl_toolbar = NavToolbar(self.canvas, tb_row)
        tb_lay.addWidget(self.mpl_toolbar, stretch=1)

        refresh_btn = QtWidgets.QPushButton("⟳  Refresh from main window")
        refresh_btn.setToolTip(
            "Reload all spectra and overlays currently visible in the main window.")
        refresh_btn.setFixedHeight(self.mpl_toolbar.sizeHint().height())
        refresh_btn.clicked.connect(self._refresh_spectra)
        tb_lay.addWidget(refresh_btn)

        cv_lay.addWidget(tb_row)
        cv_lay.addWidget(self.canvas, stretch=1)

        # ── cursor readout strip ──────────────────────────────
        self._cursor_label = QtWidgets.QLabel("  x = -    y = -")
        self._cursor_label.setStyleSheet(
            "font-family: monospace; font-size: 11px; color: #555; padding: 1px 6px;")
        cv_lay.addWidget(self._cursor_label)

        # ── bottom recurrent bar ──────────────────────────────
        cv_lay.addWidget(self._build_recurrent_bar())

        # ── RIGHT: control dock - each tab wrapped in a QScrollArea ──
        dock = QtWidgets.QTabWidget()
        dock.setMinimumWidth(260)
        dock.setMaximumWidth(16777215)   # no upper cap - user can drag it wider
        dock.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Expanding)

        def _scrolled(tab_widget):
            """Wrap a tab widget in a scroll area so tall content never forces window height."""
            sa = QtWidgets.QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            sa.setHorizontalScrollBarPolicy(
                QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sa.setWidget(tab_widget)
            return sa

        dock.addTab(_scrolled(self._build_appearance_tab()),   "Appearance")
        dock.addTab(_scrolled(self._build_peaks_tab()),         "Peaks")
        dock.addTab(_scrolled(self._build_annotations_tab()),   "Annotations")
        dock.addTab(_scrolled(self._build_legend_tab()),         "Legend")

        # ── Splitter: canvas left, dock right, border draggable ──────
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(canvas_panel)
        splitter.addWidget(dock)
        splitter.setStretchFactor(0, 3)   # canvas expands
        splitter.setStretchFactor(1, 0)   # dock stays compact unless dragged

        # Restore saved splitter position, or default to 420px dock width
        saved_sizes = settings.value("pt/splitter_sizes")
        if saved_sizes:
            try:
                splitter.setSizes([int(x) for x in saved_sizes])
            except Exception:
                splitter.setSizes([900, 420])
        else:
            splitter.setSizes([900, 420])

        # Save splitter position when user drags it
        splitter.splitterMoved.connect(
            lambda: settings.setValue("pt/splitter_sizes",
                                      [str(x) for x in splitter.sizes()]))

        root.addWidget(splitter, stretch=1)

        # canvas mouse events
        self.canvas.mpl_connect("button_press_event",   self._on_canvas_click)
        self.canvas.mpl_connect("button_release_event", self._on_canvas_release)
        self.canvas.mpl_connect("motion_notify_event",  self._on_canvas_motion)
        self._dragging_label = None
        self._dragging_ann  = None
        self._drag_start    = None
        self._last_ann_artists = []


    def _refresh_spectra(self):
        """Reload all spectra from the current state of the main window,
        preserving the current zoom level."""
        had_data = bool(self._spectra)
        if had_data:
            saved_xlim = self.ax.get_xlim()
            saved_ylim = self.ax.get_ylim()

        self._load_current_spectra()
        if not had_data:
            self._force_autoscale = True
        self._draw()

        if had_data:
            self.ax.set_xlim(saved_xlim)
            self.ax.set_ylim(saved_ylim)
            self.canvas.draw_idle()

    def _build_appearance_tab(self):
        """
        Controls:
          • Axes labels (X, Y) + font size
          • Tick label size
          • Line width per spectrum
          • Per-spectrum color button
          • Background color
          • Font family chooser (system fonts)
          • Grid on/off
          • Line style (solid / dashed / dotted)
        """
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        def _grp(title, color):
            """Appearance-tab group: solid filled title bar (no hatch)."""
            g = QtWidgets.QGroupBox(title)
            g.setStyleSheet(
                f"QGroupBox {{ border:1px solid {color}; border-radius:4px; "
                f"  margin-top:14px; padding-top:6px; font-weight:bold; }}"
                f"QGroupBox::title {{ subcontrol-origin:margin; "
                f"  subcontrol-position:top left; padding:2px 6px; "
                f"  background-color:{color}; color:white; border-radius:3px; }}")
            return g

        def _form(grp):
            """Create a QFormLayout on grp with top spacing after the title bar."""
            fl = QtWidgets.QFormLayout(grp)
            fl.setContentsMargins(6, 10, 6, 6)
            fl.setSpacing(5)
            return fl

        # ── Theme ─────────────────────────────────────────────
        grp_theme = _grp("Visual theme", "#2c2c54")
        lay_theme = _form(grp_theme)

        self.theme_combo = QtWidgets.QComboBox()
        self.theme_combo.addItems(list(_PT_THEMES.keys()))
        self.theme_combo.setCurrentText("Clean White")
        self.theme_combo.setToolTip(
            "Choose a pre-built visual style.\n"
            "The theme controls backgrounds, spines, ticks, and grid.\n"
            "Your per-spectrum colours and fonts are always preserved.")
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        lay_theme.addRow("Theme:", self.theme_combo)

        outer.addWidget(grp_theme)

        # ── Axes & Font ────────────────────────────────────────
        grp_axes = _grp("Axes && Font", "#3a6ea5")
        lay_axes = QtWidgets.QFormLayout(grp_axes)
        lay_axes.setSpacing(5)

        self.xlabel_edit = QtWidgets.QLineEdit("m/z")
        self.ylabel_edit = QtWidgets.QLineEdit("Intensity")
        self.xlabel_edit.textChanged.connect(self._draw)
        self.ylabel_edit.textChanged.connect(self._draw)
        lay_axes.addRow("X label:", self.xlabel_edit)
        lay_axes.addRow("Y label:", self.ylabel_edit)

        self.axes_label_size = QtWidgets.QSpinBox()
        self.axes_label_size.setRange(6, 48); self.axes_label_size.setValue(13)
        self.axes_label_size.valueChanged.connect(self._draw)
        lay_axes.addRow("Label size:", self.axes_label_size)

        self.tick_size = QtWidgets.QSpinBox()
        self.tick_size.setRange(6, 36); self.tick_size.setValue(11)
        self.tick_size.valueChanged.connect(self._draw)
        lay_axes.addRow("Tick size:", self.tick_size)

        self.font_combo = QtWidgets.QFontComboBox()
        self.font_combo.setCurrentFont(QtGui.QFont("DejaVu Sans"))
        self.font_combo.currentFontChanged.connect(self._draw)
        lay_axes.addRow("Font:", self.font_combo)

        self.logy_cb = QtWidgets.QCheckBox("Logarithmic Y axis")
        self.logy_cb.setToolTip("Switch Y axis to log scale. Values ≤ 0 are clipped.")
        self.logy_cb.toggled.connect(self._draw)
        lay_axes.addRow("", self.logy_cb)

        outer.addWidget(grp_axes)

        # ── Grid ──────────────────────────────────────────────
        grp_grid = _grp("Grid", "#c07d2a")
        lay_grid = QtWidgets.QFormLayout(grp_grid)
        lay_grid.setSpacing(5)

        self.grid_cb = QtWidgets.QCheckBox("Show grid")
        self.grid_cb.toggled.connect(self._draw)
        lay_grid.addRow("", self.grid_cb)

        self.grid_alpha_spin = QtWidgets.QDoubleSpinBox()
        self.grid_alpha_spin.setRange(0.05, 1.0)
        self.grid_alpha_spin.setSingleStep(0.05)
        self.grid_alpha_spin.setValue(0.4)
        self.grid_alpha_spin.setToolTip("Grid line opacity")
        self.grid_alpha_spin.valueChanged.connect(self._draw)
        lay_grid.addRow("Opacity:", self.grid_alpha_spin)

        self.grid_style_combo = QtWidgets.QComboBox()
        self.grid_style_combo.addItems(["--  dashed", ":  dotted", "-  solid", "-.  dash-dot"])
        self.grid_style_combo.currentIndexChanged.connect(self._draw)
        lay_grid.addRow("Style:", self.grid_style_combo)

        outer.addWidget(grp_grid)

        # ── Axis ranges ───────────────────────────────────────
        grp_ranges = _grp("Axis ranges", "#2e7d5e")
        lay_ranges = QtWidgets.QFormLayout(grp_ranges)
        lay_ranges.setSpacing(5)

        xrange_w = QtWidgets.QWidget()
        xrange_l = QtWidgets.QHBoxLayout(xrange_w)
        xrange_l.setContentsMargins(0, 0, 0, 0)
        self.xmin_edit = QtWidgets.QLineEdit(); self.xmin_edit.setPlaceholderText("auto")
        self.xmax_edit = QtWidgets.QLineEdit(); self.xmax_edit.setPlaceholderText("auto")
        self.xmin_edit.setFixedWidth(62); self.xmax_edit.setFixedWidth(62)
        self.xmin_edit.editingFinished.connect(self._draw)
        self.xmax_edit.editingFinished.connect(self._draw)
        xrange_l.addWidget(QtWidgets.QLabel("min:")); xrange_l.addWidget(self.xmin_edit)
        xrange_l.addSpacing(6)
        xrange_l.addWidget(QtWidgets.QLabel("max:")); xrange_l.addWidget(self.xmax_edit)
        lay_ranges.addRow("X:", xrange_w)

        yrange_w = QtWidgets.QWidget()
        yrange_l = QtWidgets.QHBoxLayout(yrange_w)
        yrange_l.setContentsMargins(0, 0, 0, 0)
        self.ymin_edit = QtWidgets.QLineEdit(); self.ymin_edit.setPlaceholderText("auto")
        self.ymax_edit = QtWidgets.QLineEdit(); self.ymax_edit.setPlaceholderText("auto")
        self.ymin_edit.setFixedWidth(62); self.ymax_edit.setFixedWidth(62)
        self.ymin_edit.editingFinished.connect(self._draw)
        self.ymax_edit.editingFinished.connect(self._draw)
        yrange_l.addWidget(QtWidgets.QLabel("min:")); yrange_l.addWidget(self.ymin_edit)
        yrange_l.addSpacing(6)
        yrange_l.addWidget(QtWidgets.QLabel("max:")); yrange_l.addWidget(self.ymax_edit)
        lay_ranges.addRow("Y:", yrange_w)

        outer.addWidget(grp_ranges)

        # ── Spines & Ticks ────────────────────────────────────
        grp_spines = _grp("Spines && Ticks", "#f28e2b")
        lay_spines = QtWidgets.QFormLayout(grp_spines)
        lay_spines.setSpacing(5)

        spines_w = QtWidgets.QWidget()
        spines_l = QtWidgets.QHBoxLayout(spines_w)
        spines_l.setContentsMargins(0, 0, 0, 0)
        self.spine_top_cb    = QtWidgets.QCheckBox("Top");    self.spine_top_cb.setChecked(False)
        self.spine_right_cb  = QtWidgets.QCheckBox("Right");  self.spine_right_cb.setChecked(False)
        self.spine_bottom_cb = QtWidgets.QCheckBox("Bottom"); self.spine_bottom_cb.setChecked(True)
        self.spine_left_cb   = QtWidgets.QCheckBox("Left");   self.spine_left_cb.setChecked(True)
        for cb in (self.spine_top_cb, self.spine_right_cb,
                   self.spine_bottom_cb, self.spine_left_cb):
            cb.toggled.connect(self._draw)
            spines_l.addWidget(cb)
        lay_spines.addRow("Spines:", spines_w)

        self.minor_ticks_cb = QtWidgets.QCheckBox("Minor ticks")
        self.minor_ticks_cb.toggled.connect(self._draw)
        lay_spines.addRow("", self.minor_ticks_cb)

        self.minor_tick_size = QtWidgets.QSpinBox()
        self.minor_tick_size.setRange(1, 20); self.minor_tick_size.setValue(3)
        self.minor_tick_size.valueChanged.connect(self._draw)
        lay_spines.addRow("Minor tick size:", self.minor_tick_size)

        outer.addWidget(grp_spines)

        # ── Figure title ──────────────────────────────────────
        grp_title = _grp("Figure title", "#e15759")
        lay_title = QtWidgets.QFormLayout(grp_title)
        lay_title.setSpacing(5)

        self.title_edit = QtWidgets.QLineEdit()
        self.title_edit.setPlaceholderText("(none)")
        self.title_edit.textChanged.connect(self._draw)
        lay_title.addRow("Title:", self.title_edit)

        self.title_size = QtWidgets.QSpinBox()
        self.title_size.setRange(6, 48); self.title_size.setValue(13)
        self.title_size.valueChanged.connect(self._draw)
        lay_title.addRow("Title size:", self.title_size)

        outer.addWidget(grp_title)

        # ── Export size ───────────────────────────────────────
        grp_export = _grp("Export size", "#76b7b2")
        lay_export = QtWidgets.QFormLayout(grp_export)
        lay_export.setSpacing(5)

        figsize_w = QtWidgets.QWidget()
        figsize_l = QtWidgets.QHBoxLayout(figsize_w)
        figsize_l.setContentsMargins(0, 0, 0, 0)
        self.fig_w_spin = QtWidgets.QSpinBox()
        self.fig_h_spin = QtWidgets.QSpinBox()
        for sp_ in (self.fig_w_spin, self.fig_h_spin):
            sp_.setRange(100, 8000); sp_.setSingleStep(50)
        self.fig_w_spin.setValue(1800); self.fig_h_spin.setValue(1200)
        self.fig_w_spin.setSuffix(" px"); self.fig_h_spin.setSuffix(" px")
        figsize_l.addWidget(QtWidgets.QLabel("W:")); figsize_l.addWidget(self.fig_w_spin)
        figsize_l.addSpacing(6)
        figsize_l.addWidget(QtWidgets.QLabel("H:")); figsize_l.addWidget(self.fig_h_spin)
        lay_export.addRow(figsize_w)

        self.export_dpi_spin = QtWidgets.QSpinBox()
        self.export_dpi_spin.setRange(72, 1200); self.export_dpi_spin.setValue(500)
        self.export_dpi_spin.setSuffix(" dpi")
        lay_export.addRow("DPI:", self.export_dpi_spin)

        self.aspect_lock_cb = QtWidgets.QCheckBox("Lock W:H aspect ratio")
        self.aspect_lock_cb.setToolTip(
            "When checked, changing W auto-adjusts H to keep the same ratio.")
        self.fig_w_spin.valueChanged.connect(self._on_figsize_w_changed)
        lay_export.addRow("", self.aspect_lock_cb)

        def _capture_ratio(checked):
            if checked and self.fig_w_spin.value() > 0:
                self._aspect_ratio = self.fig_h_spin.value() / self.fig_w_spin.value()
        self.aspect_lock_cb.toggled.connect(_capture_ratio)
        self._aspect_ratio = self.fig_h_spin.value() / self.fig_w_spin.value()

        outer.addWidget(grp_export)

        # ── Spectrum stacking ─────────────────────────────────
        grp_stack = _grp("Spectrum stacking", "#59a14f")
        lay_stack = QtWidgets.QFormLayout(grp_stack)
        lay_stack.setSpacing(5)

        self.offset_spin = QtWidgets.QDoubleSpinBox()
        self.offset_spin.setRange(0.0, 10.0)
        self.offset_spin.setSingleStep(0.05)
        self.offset_spin.setValue(0.0)
        self.offset_spin.setToolTip(
            "Shift each spectrum upward by this fraction of the max intensity.\n"
            "0 = all overlapping (normal). 0.2 = each shifted up by 20 % of max.")
        self.offset_spin.valueChanged.connect(self._draw)
        lay_stack.addRow("Stack offset:", self.offset_spin)

        self.norm_combo = QtWidgets.QComboBox()
        self.norm_combo.addItems(["None", "0–1 (per spectrum)", "to highest overall"])
        self.norm_combo.setToolTip(
            "None: raw intensities.\n"
            "0–1: each spectrum normalised to its own maximum.\n"
            "to highest overall: all normalised to the single tallest peak.")
        self.norm_combo.currentIndexChanged.connect(self._draw)
        lay_stack.addRow("Normalise:", self.norm_combo)

        self._bg_color = "#ffffff"
        bg_btn = QtWidgets.QPushButton("Choose…")
        bg_btn.clicked.connect(self._pick_bg_color)
        lay_stack.addRow("Background:", bg_btn)

        self.clip_to_axes_cb = QtWidgets.QCheckBox("Clip spectra to axes")
        self.clip_to_axes_cb.setToolTip(
            "No whitespace margin - spectra start exactly at the axes frame.")
        self.clip_to_axes_cb.toggled.connect(self._draw)
        lay_stack.addRow("", self.clip_to_axes_cb)

        self.mirror_pairs_cb = QtWidgets.QCheckBox("Mirror every 2nd spectrum")
        self.mirror_pairs_cb.setChecked(False)
        self.mirror_pairs_cb.setToolTip(
            "Negate the Y axis of every other spectrum so paired spectra\n"
            "face each other (easier to compare).\n"
            "Use 'Mirror odd' to switch which set is flipped.")
        self.mirror_pairs_cb.toggled.connect(self._draw)
        lay_stack.addRow("", self.mirror_pairs_cb)

        self.mirror_odd_cb = QtWidgets.QCheckBox("  Mirror odd spectra instead")
        self.mirror_odd_cb.setChecked(False)
        self.mirror_odd_cb.setToolTip(
            "Flip spectra 0, 2, 4… instead of 1, 3, 5…")
        self.mirror_odd_cb.toggled.connect(self._draw)
        lay_stack.addRow("", self.mirror_odd_cb)

        # ── Sigma clipping ────────────────────────────────────
        self.sigma_clip_cb = QtWidgets.QCheckBox("σ noise clipping")
        self.sigma_clip_cb.setToolTip(
            "Suppress baseline noise below the N-sigma noise floor.\n"
            "Uses the same algorithm as the main window.")
        self.sigma_clip_cb.toggled.connect(self._on_sigma_clip_toggled)
        lay_stack.addRow("", self.sigma_clip_cb)

        sigma_row = QtWidgets.QWidget()
        sigma_row_lay = QtWidgets.QHBoxLayout(sigma_row)
        sigma_row_lay.setContentsMargins(0, 0, 0, 0)
        sigma_row_lay.setSpacing(4)

        self.sigma_slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.sigma_slider.setMinimum(10)   # 1.0 σ
        self.sigma_slider.setMaximum(40)   # 4.0 σ
        self.sigma_slider.setValue(10)
        self.sigma_slider.setEnabled(False)
        self.sigma_slider.setToolTip("Noise clipping threshold (1.0 – 4.0 σ)")
        self.sigma_slider.valueChanged.connect(self._on_sigma_slider_changed)

        self.sigma_label = QtWidgets.QLabel("1.0σ")
        self.sigma_label.setFixedWidth(32)
        self.sigma_label.setEnabled(False)

        sigma_row_lay.addWidget(self.sigma_slider, stretch=1)
        sigma_row_lay.addWidget(self.sigma_label)
        lay_stack.addRow("Threshold:", sigma_row)

        outer.addWidget(grp_stack)

        # ── Per-spectrum ──────────────────────────────────────
        grp_per = _grp("Per-spectrum", "#7b3f9e")
        per_outer = QtWidgets.QVBoxLayout(grp_per)
        per_outer.setContentsMargins(4, 4, 4, 4)
        self._per_spectrum_container = QtWidgets.QWidget()
        self._per_spectrum_layout    = QtWidgets.QVBoxLayout(self._per_spectrum_container)
        self._per_spectrum_layout.setContentsMargins(0, 0, 0, 0)
        self._per_spectrum_layout.setSpacing(4)
        per_outer.addWidget(self._per_spectrum_container)

        outer.addWidget(grp_per)

        # ── Watermark ─────────────────────────────────────────
        grp_wm = _grp("Watermark", "#b07aa1")
        lay_wm = QtWidgets.QFormLayout(grp_wm)
        lay_wm.setSpacing(5)

        self.watermark_edit = QtWidgets.QLineEdit()
        self.watermark_edit.setPlaceholderText("e.g. DRAFT")
        self.watermark_edit.textChanged.connect(self._draw)
        lay_wm.addRow("Text:", self.watermark_edit)

        self.watermark_alpha_spin = QtWidgets.QDoubleSpinBox()
        self.watermark_alpha_spin.setRange(0.02, 1.0)
        self.watermark_alpha_spin.setSingleStep(0.05)
        self.watermark_alpha_spin.setValue(0.12)
        self.watermark_alpha_spin.valueChanged.connect(self._draw)
        lay_wm.addRow("Opacity:", self.watermark_alpha_spin)

        outer.addWidget(grp_wm)
        outer.addStretch()

        reset_app_btn = QtWidgets.QPushButton("↺  Reset all to defaults")
        reset_app_btn.setToolTip("Restore every Appearance setting to its factory default.")
        reset_app_btn.setStyleSheet(
            "QPushButton { color: #c0392b; border: 1px solid #c0392b; "
            "border-radius: 4px; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #fdecea; }")
        reset_app_btn.clicked.connect(self._reset_appearance_defaults)
        outer.addWidget(reset_app_btn)
        return w

    def _on_sigma_clip_toggled(self, checked):
        self.sigma_slider.setEnabled(checked)
        self.sigma_label.setEnabled(checked)
        self._draw()

    def _on_sigma_slider_changed(self, int_val):
        n_sigma = int_val / 10.0
        self.sigma_label.setText(f"{n_sigma:.1f}σ")
        if self.sigma_clip_cb.isChecked():
            self._draw()

    def _pick_bg_color(self):
        c = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._bg_color), self, "Background color")
        if c.isValid():
            self._bg_color = c.name()
            self._draw()

    def _on_theme_changed(self, name):
        """When theme changes, push its background into the bg_color field so
        it is used as the figure facecolor baseline, then redraw."""
        t = _PT_THEMES.get(name, {})
        if t.get("fig_bg"):
            self._bg_color = t["fig_bg"]
        self._draw()

    def _apply_theme(self, ax, fig):
        """
        Stamp the active theme's chrome onto ax/fig.
        Called at the end of _draw(), after all data and labels are rendered.
        The empty "Default (matplotlib)" theme is a no-op.
        """
        name = self.theme_combo.currentText()
        t    = _PT_THEMES.get(name, {})
        if not t:
            return   # Default theme - leave matplotlib as-is

        # ── backgrounds ──────────────────────────────────────
        fig.set_facecolor(t.get("fig_bg", self._bg_color))
        ax.set_facecolor(t.get("ax_bg",  self._bg_color))

        # ── spines ───────────────────────────────────────────
        all_spines = ("top", "right", "bottom", "left")
        visible    = t.get("spine_visible", all_spines)
        sc         = t.get("spine_color",   "#444444")
        sw         = t.get("spine_width",   1.0)
        offset     = t.get("despine_offset", 0)
        for sp_name in all_spines:
            sp = ax.spines[sp_name]
            sp.set_visible(sp_name in visible)
            if sp_name in visible:
                sp.set_edgecolor(sc)
                sp.set_linewidth(sw)
                if offset:
                    sp.set_position(("outward", offset))

        # ── ticks ────────────────────────────────────────────
        tc  = t.get("tick_color",     "#444444")
        td  = t.get("tick_direction", "out")
        tl  = t.get("tick_length",    4)
        tw  = t.get("tick_width",     0.9)
        ax.tick_params(axis="both", which="major",
                       colors=tc, direction=td,
                       length=tl, width=tw)
        ax.tick_params(axis="both", which="minor",
                       colors=tc, direction=td,
                       length=max(1, tl - 2), width=tw * 0.7)
        try:
            for lbl in ax.get_xticklabels() + ax.get_yticklabels():
                lbl.set_color(t.get("label_color", tc))
        except (ValueError, TypeError):
            pass

        # ── axis labels & title ───────────────────────────────
        lc = t.get("label_color", "#222222")
        ax.xaxis.label.set_color(lc)
        ax.yaxis.label.set_color(lc)
        title_obj = ax.title
        if title_obj.get_text():
            title_obj.set_color(t.get("title_color", lc))

        # ── grid override (only if theme requests one) ────────
        if "grid" in t:
            if t["grid"]:
                ax.grid(True,
                        which=t.get("grid_which", "major"),
                        color=t.get("grid_color", "#cccccc"),
                        alpha=t.get("grid_alpha", 0.6),
                        linestyle=t.get("grid_style", "-"),
                        linewidth=0.7,
                        zorder=0)
            else:
                ax.grid(False, which="both")

        # ── figure border ─────────────────────────────────────
        for side in fig.patches:
            try:
                side.set_edgecolor("none")
            except Exception:
                pass

    def _reset_appearance_defaults(self):
        """Reset all Appearance tab controls to their factory defaults."""
        self.xlabel_edit.setText("m/z")
        self.ylabel_edit.setText("Intensity")
        self.axes_label_size.setValue(13)
        self.tick_size.setValue(11)
        self.font_combo.setCurrentFont(QtGui.QFont("DejaVu Sans"))
        self.logy_cb.setChecked(False)
        self.grid_cb.setChecked(False)
        self.grid_alpha_spin.setValue(0.4)
        self.grid_style_combo.setCurrentIndex(0)   # "--  dashed"
        self.xmin_edit.clear(); self.xmax_edit.clear()
        self.ymin_edit.clear(); self.ymax_edit.clear()
        self.spine_top_cb.setChecked(False)
        self.spine_right_cb.setChecked(False)
        self.spine_bottom_cb.setChecked(True)
        self.spine_left_cb.setChecked(True)
        self.minor_ticks_cb.setChecked(False)
        self.minor_tick_size.setValue(3)
        self.title_edit.clear()
        self.title_size.setValue(13)
        self.fig_w_spin.setValue(1800)
        self.fig_h_spin.setValue(1200)
        self.export_dpi_spin.setValue(500)
        self.aspect_lock_cb.setChecked(False)
        self.offset_spin.setValue(0.0)
        self.norm_combo.setCurrentIndex(0)          # "None"
        self._bg_color = "#ffffff"
        self.clip_to_axes_cb.setChecked(False)
        self.watermark_edit.clear()
        self.watermark_alpha_spin.setValue(0.12)
        self.theme_combo.setCurrentText("Clean White")
        self._draw()

    def _reset_peaks_defaults(self):
        """Reset all Peaks tab controls to their factory defaults."""
        self.show_peak_spans_cb.setChecked(True)
        self.auto_label_cb.setChecked(False)
        self.label_thr_mode_combo.setCurrentIndex(0)   # "% of max intensity"
        self.label_threshold_spin.setValue(5.0)
        self.label_integer_cb.setChecked(True)
        self.label_highest_cb.setChecked(True)
        self.label_font_combo.setCurrentFont(QtGui.QFont("DejaVu Sans"))
        self.label_fontsize_spin.setValue(9)
        self.label_angle_spin.setValue(90)
        self._label_color = "#222222"
        self.label_color_btn.setStyleSheet(f"background:{self._label_color};")
        self._label_offset = 0.0
        self.label_step_spin.setValue(0.02)
        self.area_mode_cb.setChecked(False)
        self.area_alpha_spin.setValue(0.25)
        self._draw()

    def _rebuild_per_spectrum_rows(self):
        """Called after _load_current_spectra; creates one row per spectrum."""
        # clear old rows
        while self._per_spectrum_layout.count():
            item = self._per_spectrum_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()

        for sp in self._spectra:
            row_w  = QtWidgets.QWidget()
            row_lay = QtWidgets.QHBoxLayout(row_w)
            row_lay.setContentsMargins(0, 0, 0, 0)

            # color swatch button
            color_btn = QtWidgets.QPushButton()
            color_btn.setFixedSize(22, 22)
            color_btn.setStyleSheet(
                f"background-color:{sp['color']}; border:1px solid #888;")
            color_btn.clicked.connect(lambda _, s=sp, b=color_btn: self._pick_spectrum_color(s, b))

            # line width
            lw_spin = QtWidgets.QDoubleSpinBox()
            lw_spin.setRange(0.5, 8.0); lw_spin.setSingleStep(0.5)
            lw_spin.setValue(sp.get("linewidth", 1.0))
            lw_spin.valueChanged.connect(lambda v, s=sp: (s.update({"linewidth": v}), self._draw()))

            # line style
            ls_combo = QtWidgets.QComboBox()
            ls_combo.addItems(["solid", "dashed", "dotted", "dashdot"])
            ls_combo.setCurrentText(sp.get("linestyle", "solid"))
            ls_combo.currentTextChanged.connect(lambda v, s=sp: (s.update({"linestyle": v}), self._draw()))

            lbl = QtWidgets.QLabel(sp["name"][:18])
            lbl.setToolTip(sp["name"])

            row_lay.addWidget(color_btn)
            row_lay.addWidget(QtWidgets.QLabel("W:"))
            row_lay.addWidget(lw_spin)
            row_lay.addWidget(ls_combo)
            row_lay.addWidget(lbl, stretch=1)
            self._per_spectrum_layout.addWidget(row_w)

    def _pick_spectrum_color(self, sp_dict, btn):
        c = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(sp_dict["color"]), self, "Spectrum color")
        if c.isValid():
            sp_dict["color"] = c.name()
            btn.setStyleSheet(f"background-color:{c.name()}; border:1px solid #888;")
            self._draw()
    
    def _build_legend_tab(self):
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        def _grp(title, color="#b25000"):
            g = QtWidgets.QGroupBox(title)
            g.setStyleSheet(
                f"QGroupBox {{ border:1px solid {color}; border-radius:4px; "
                f"  margin-top:8px; padding-top:6px; font-weight:bold; }}"
                f"QGroupBox::title {{ subcontrol-origin:margin; "
                f"  subcontrol-position:top left; padding:2px 6px; "
                f"  background-color:{color}; color:white; border-radius:3px; "
                f"  background-image: repeating-linear-gradient("
                f"    90deg,rgba(255,255,255,.20) 0,rgba(255,255,255,.20) 2px,"
                f"    transparent 2px,transparent 6px); }}")
            return g

        # ── Visibility & position ─────────────────────────────
        grp_vis = _grp("Visibility && position")
        lay_vis = QtWidgets.QFormLayout(grp_vis)
        lay_vis.setContentsMargins(6, 10, 6, 6); lay_vis.setSpacing(5)

        self.legend_cb = QtWidgets.QCheckBox("Show legend")
        self.legend_cb.setChecked(True)
        self.legend_cb.toggled.connect(self._draw)
        lay_vis.addRow("", self.legend_cb)

        self.legend_pos = QtWidgets.QComboBox()
        self.legend_pos.addItems([
            "upper right", "upper left", "lower right", "lower left",
            "center right", "center left", "lower center", "upper center",
            "best", "outside right", "outside bottom"])
        self.legend_pos.setCurrentText("upper right")
        self.legend_pos.currentTextChanged.connect(self._draw)
        lay_vis.addRow("Position:", self.legend_pos)

        self.legend_ncol_spin = QtWidgets.QSpinBox()
        self.legend_ncol_spin.setRange(1, 8); self.legend_ncol_spin.setValue(1)
        self.legend_ncol_spin.setToolTip("Number of columns in the legend.")
        self.legend_ncol_spin.valueChanged.connect(self._draw)
        lay_vis.addRow("Columns:", self.legend_ncol_spin)

        outer.addWidget(grp_vis)

        # ── Text ──────────────────────────────────────────────
        grp_txt = _grp("Text", "#7b5c00")
        lay_txt = QtWidgets.QFormLayout(grp_txt)
        lay_txt.setContentsMargins(6, 10, 6, 6); lay_txt.setSpacing(5)

        self.legend_fontsize = QtWidgets.QSpinBox()
        self.legend_fontsize.setRange(6, 36); self.legend_fontsize.setValue(11)
        self.legend_fontsize.valueChanged.connect(self._draw)
        lay_txt.addRow("Font size:", self.legend_fontsize)

        self.legend_title_edit = QtWidgets.QLineEdit()
        self.legend_title_edit.setPlaceholderText("(none)")
        self.legend_title_edit.textChanged.connect(self._draw)
        lay_txt.addRow("Title:", self.legend_title_edit)

        self.legend_title_size = QtWidgets.QSpinBox()
        self.legend_title_size.setRange(6, 36); self.legend_title_size.setValue(11)
        self.legend_title_size.valueChanged.connect(self._draw)
        lay_txt.addRow("Title size:", self.legend_title_size)

        outer.addWidget(grp_txt)

        # ── Frame & background ────────────────────────────────
        grp_frame = _grp("Frame && background", "#5a3e00")
        lay_frame = QtWidgets.QFormLayout(grp_frame)
        lay_frame.setContentsMargins(6, 10, 6, 6); lay_frame.setSpacing(5)

        self.legend_frame_cb = QtWidgets.QCheckBox("Show frame")
        self.legend_frame_cb.setChecked(True)
        self.legend_frame_cb.toggled.connect(self._draw)
        lay_frame.addRow("", self.legend_frame_cb)

        self.legend_fancybox_cb = QtWidgets.QCheckBox("Rounded corners (fancybox)")
        self.legend_fancybox_cb.setChecked(True)
        self.legend_fancybox_cb.toggled.connect(self._draw)
        lay_frame.addRow("", self.legend_fancybox_cb)

        self.legend_shadow_cb = QtWidgets.QCheckBox("Drop shadow")
        self.legend_shadow_cb.setChecked(False)
        self.legend_shadow_cb.toggled.connect(self._draw)
        lay_frame.addRow("", self.legend_shadow_cb)

        self.legend_alpha_spin = QtWidgets.QDoubleSpinBox()
        self.legend_alpha_spin.setRange(0.0, 1.0)
        self.legend_alpha_spin.setSingleStep(0.05)
        self.legend_alpha_spin.setValue(0.92)
        self.legend_alpha_spin.setToolTip("Background opacity (0 = transparent, 1 = solid).")
        self.legend_alpha_spin.valueChanged.connect(self._draw)
        lay_frame.addRow("BG opacity:", self.legend_alpha_spin)

        # edge color picker
        self._legend_edge_color = "#cccccc"
        self.legend_edge_btn = QtWidgets.QPushButton("  Edge color…")
        self.legend_edge_btn.setStyleSheet(
            f"background-color:{self._legend_edge_color};")
        self.legend_edge_btn.clicked.connect(self._pick_legend_edge_color)
        lay_frame.addRow("", self.legend_edge_btn)

        outer.addWidget(grp_frame)

        # ── Spacing ───────────────────────────────────────────
        grp_sp = _grp("Spacing", "#3d4a00")
        lay_sp = QtWidgets.QFormLayout(grp_sp)
        lay_sp.setContentsMargins(6, 10, 6, 6); lay_sp.setSpacing(5)

        self.legend_labelspacing_spin = QtWidgets.QDoubleSpinBox()
        self.legend_labelspacing_spin.setRange(0.0, 3.0)
        self.legend_labelspacing_spin.setSingleStep(0.1)
        self.legend_labelspacing_spin.setValue(0.5)
        self.legend_labelspacing_spin.setToolTip(
            "Vertical space between legend entries (in font-size units).")
        self.legend_labelspacing_spin.valueChanged.connect(self._draw)
        lay_sp.addRow("Entry spacing:", self.legend_labelspacing_spin)

        self.legend_handlelength_spin = QtWidgets.QDoubleSpinBox()
        self.legend_handlelength_spin.setRange(0.5, 6.0)
        self.legend_handlelength_spin.setSingleStep(0.25)
        self.legend_handlelength_spin.setValue(1.5)
        self.legend_handlelength_spin.setToolTip(
            "Length of the colour line handle (in font-size units).")
        self.legend_handlelength_spin.valueChanged.connect(self._draw)
        lay_sp.addRow("Handle length:", self.legend_handlelength_spin)

        self.legend_borderpad_spin = QtWidgets.QDoubleSpinBox()
        self.legend_borderpad_spin.setRange(0.0, 2.0)
        self.legend_borderpad_spin.setSingleStep(0.1)
        self.legend_borderpad_spin.setValue(0.5)
        self.legend_borderpad_spin.setToolTip(
            "Padding between the frame border and content.")
        self.legend_borderpad_spin.valueChanged.connect(self._draw)
        lay_sp.addRow("Border pad:", self.legend_borderpad_spin)

        outer.addWidget(grp_sp)

        # ── Custom labels ─────────────────────────────────────
        grp_lbl = _grp("Custom labels", "#b25000")
        lbl_outer = QtWidgets.QVBoxLayout(grp_lbl)
        lbl_outer.setContentsMargins(6, 10, 6, 6)
        tip = QtWidgets.QLabel("Edit the label shown for each spectrum:")
        tip.setStyleSheet("color:gray; font-size:10px;")
        lbl_outer.addWidget(tip)
        self._legend_label_container = QtWidgets.QWidget()
        self._legend_label_layout    = QtWidgets.QVBoxLayout(self._legend_label_container)
        self._legend_label_layout.setContentsMargins(0, 0, 0, 0)
        lbl_outer.addWidget(self._legend_label_container)
        outer.addWidget(grp_lbl)

        outer.addStretch()
        return w

    def _pick_legend_edge_color(self):
        c = QtWidgets.QColorDialog.getColor(
            QtGui.QColor(self._legend_edge_color), self, "Legend edge color")
        if c.isValid():
            self._legend_edge_color = c.name()
            self.legend_edge_btn.setStyleSheet(
                f"background-color:{self._legend_edge_color};")
            self._draw()

    def _rebuild_legend_rows(self):
        while self._legend_label_layout.count():
            item = self._legend_label_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for sp in self._spectra:
            edit = QtWidgets.QLineEdit(sp["label"])
            edit.textChanged.connect(lambda v, s=sp: (s.update({"label": v}), self._draw()))
            self._legend_label_layout.addWidget(edit)

    def _build_peaks_tab(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        def _grp(title, color="#2e7d32"):
                    """Peaks-tab group: grid-hatched header."""
                    g = QtWidgets.QGroupBox(title)
                    g.setStyleSheet(
                        f"QGroupBox {{ border:1px solid {color}; border-radius:4px; margin-top:8px; padding-top:6px; font-weight:bold; }}"
                        f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left; "
                        f"padding:2px 6px; background-color:{color}; color:white; border-radius:3px; "
                        f"background-image: repeating-linear-gradient(0deg,rgba(255,255,255,.18) 0,rgba(255,255,255,.18) 2px,transparent 2px,transparent 7px),"
                        f"repeating-linear-gradient(90deg,rgba(255,255,255,.18) 0,rgba(255,255,255,.18) 2px,transparent 2px,transparent 7px); }}")
                    return g

        self.show_peak_spans_cb = QtWidgets.QCheckBox("Show peak list highlight bands")
        self.show_peak_spans_cb.setChecked(True)
        self.show_peak_spans_cb.setToolTip(
            "Show/hide the coloured vertical bands marking each peak list's positions.")
        self.show_peak_spans_cb.toggled.connect(self._draw)
        lay.addWidget(self.show_peak_spans_cb)

        span_width_row = QtWidgets.QFormLayout()
        self.peak_span_width_spin = QtWidgets.QDoubleSpinBox()
        self.peak_span_width_spin.setRange(0.01, 20.0)
        self.peak_span_width_spin.setSingleStep(0.1)
        self.peak_span_width_spin.setValue(1.0)
        self.peak_span_width_spin.setDecimals(2)
        self.peak_span_width_spin.setSuffix(" Da")
        self.peak_span_width_spin.setToolTip(
            "Total width of each peak band in Da (peak ± half this value).")
        self.peak_span_width_spin.valueChanged.connect(self._draw)
        span_width_row.addRow("Band width:", self.peak_span_width_spin)
        lay.addLayout(span_width_row)

        self.peak_list_labels_cb = QtWidgets.QCheckBox("Show peak list value labels")
        self.peak_list_labels_cb.setChecked(False)
        self.peak_list_labels_cb.setToolTip(
            "Draw an m/z label above each peak in the active peak lists.\n"
            "Uses the same font, size, angle and color as other peak labels.")
        self.peak_list_labels_cb.toggled.connect(self._draw)
        lay.addWidget(self.peak_list_labels_cb)

        self.label_overlap_stack_cb = QtWidgets.QCheckBox("  Stack overlapping labels vertically")
        self.label_overlap_stack_cb.setChecked(False)
        self.label_overlap_stack_cb.setToolTip(
            "When multiple peak lists share the same m/z position:\n"
            "  Unchecked → labels merged on one line, joined by commas\n"
            "  Checked   → each label drawn at a progressively higher offset")
        self.label_overlap_stack_cb.toggled.connect(self._draw)
        lay.addWidget(self.label_overlap_stack_cb)

        # ── Auto-label controls ───────────────────────────────
        grp_auto   = _grp("Auto peak labels")
        g_lay = QtWidgets.QFormLayout(grp_auto)

        self.auto_label_cb = QtWidgets.QCheckBox("Show auto labels")
        self.auto_label_cb.toggled.connect(self._update_auto_labels)
        g_lay.addRow("", self.auto_label_cb)

        # Mode selector: % of max  OR  SNR
        self.label_thr_mode_combo = QtWidgets.QComboBox()
        self.label_thr_mode_combo.addItems(["% of max intensity", "SNR"])
        self.label_thr_mode_combo.setToolTip(
            "% of max: label peaks above X % of the spectrum maximum.\n"
            "SNR: label peaks whose signal-to-noise ratio exceeds the threshold.")
        self.label_thr_mode_combo.currentIndexChanged.connect(self._on_label_thr_mode_changed)
        g_lay.addRow("Mode:", self.label_thr_mode_combo)

        self.label_threshold_spin = QtWidgets.QDoubleSpinBox()
        self.label_threshold_spin.setRange(0, 100); self.label_threshold_spin.setValue(5)
        self.label_threshold_spin.setSingleStep(1.0)
        self.label_threshold_spin.setSuffix(" % of max")
        self.label_threshold_spin.valueChanged.connect(self._update_auto_labels)
        g_lay.addRow("Threshold:", self.label_threshold_spin)

        self.label_integer_cb = QtWidgets.QCheckBox("Show integers only")
        self.label_integer_cb.setChecked(True)
        self.label_integer_cb.toggled.connect(self._draw)
        g_lay.addRow("", self.label_integer_cb)

        # Higher-peak-only for multiple spectra
        self.label_highest_cb = QtWidgets.QCheckBox("Label only on highest spectrum")
        self.label_highest_cb.setChecked(True)
        self.label_highest_cb.toggled.connect(self._draw)
        g_lay.addRow("", self.label_highest_cb)

        lay.addWidget(grp_auto)

        # ── Label appearance ──────────────────────────────────
        grp_lbl    = _grp("Label appearance")
        la_lay = QtWidgets.QFormLayout(grp_lbl)

        self.label_font_combo = QtWidgets.QFontComboBox()
        self.label_font_combo.setCurrentFont(QtGui.QFont("DejaVu Sans"))
        self.label_font_combo.currentFontChanged.connect(self._draw)
        la_lay.addRow("Font:", self.label_font_combo)

        self.label_fontsize_spin = QtWidgets.QSpinBox()
        self.label_fontsize_spin.setRange(6, 36); self.label_fontsize_spin.setValue(9)
        self.label_fontsize_spin.valueChanged.connect(self._draw)
        la_lay.addRow("Font size:", self.label_fontsize_spin)

        self.label_angle_spin = QtWidgets.QSpinBox()
        self.label_angle_spin.setRange(0, 90); self.label_angle_spin.setValue(90)
        self.label_angle_spin.valueChanged.connect(self._draw)
        la_lay.addRow("Angle:", self.label_angle_spin)

        self.label_use_row_color_cb = QtWidgets.QCheckBox("Use peak list color for labels")
        self.label_use_row_color_cb.setChecked(True)
        self.label_use_row_color_cb.setToolTip(
            "When checked, each peak list label is drawn in that list's own color.\n"
            "When unchecked, all labels use the color chosen below.")
        self.label_use_row_color_cb.toggled.connect(self._draw)
        la_lay.addRow("", self.label_use_row_color_cb)

        self.label_mass_black_cb = QtWidgets.QCheckBox("Use black for mass labels")
        self.label_mass_black_cb.setChecked(False)
        self.label_mass_black_cb.setToolTip(
            "Force all peak-list m/z value labels to black,\n"
            "regardless of the peak list color setting above.")
        self.label_mass_black_cb.toggled.connect(self._draw)
        la_lay.addRow("", self.label_mass_black_cb)

        self.label_use_black_masses_cb = QtWidgets.QCheckBox("Use black for masses labels")
        self.label_use_black_masses_cb.setChecked(False)
        self.label_use_black_masses_cb.setToolTip(
            "When checked, all m/z mass labels on peaks are drawn in black,\n"
            "regardless of the peak list color setting above.")
        self.label_use_black_masses_cb.toggled.connect(self._draw)
        la_lay.addRow("", self.label_use_black_masses_cb)

        self.label_color_btn = QtWidgets.QPushButton()
        self._label_color = "#222222"
        self.label_color_btn.setFixedHeight(22)
        self.label_color_btn.setStyleSheet(f"background:{self._label_color};")
        self.label_color_btn.clicked.connect(self._pick_label_color)
        la_lay.addRow("Color:", self.label_color_btn)

        lay.addWidget(grp_lbl)

        # ── Move all labels ───────────────────────────────────
        grp_move   = _grp("Move all labels")
        mv_lay = QtWidgets.QHBoxLayout(grp_move)

        up_btn   = QtWidgets.QPushButton("▲ Up")
        down_btn = QtWidgets.QPushButton("▼ Down")
        self.label_step_spin = QtWidgets.QDoubleSpinBox()
        self.label_step_spin.setRange(0.001, 1.0)
        self.label_step_spin.setValue(0.02)
        self.label_step_spin.setSingleStep(0.005)
        self.label_step_spin.setToolTip("Step as fraction of Y range")

        up_btn.clicked.connect(lambda: self._shift_all_labels(+1))
        down_btn.clicked.connect(lambda: self._shift_all_labels(-1))

        mv_lay.addWidget(down_btn)
        mv_lay.addWidget(self.label_step_spin)
        mv_lay.addWidget(up_btn)
        lay.addWidget(grp_move)

        # ── Manual peak mode ──────────────────────────────────
        grp_manual = _grp("Manual peaks (double-click on plot)")
        mn_lay = QtWidgets.QVBoxLayout(grp_manual)

        self.manual_peak_mode_cb = QtWidgets.QCheckBox("Enable manual peak mode")
        mn_lay.addWidget(self.manual_peak_mode_cb)

        clear_manual_btn = QtWidgets.QPushButton("Clear all manual labels")
        clear_manual_btn.clicked.connect(self._clear_manual_labels)
        mn_lay.addWidget(clear_manual_btn)

        tip = QtWidgets.QLabel(
            "Double-click: add nearest local max.\n"
            "Right-click on label: remove it.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:gray; font-size:10px;")
        mn_lay.addWidget(tip)
        lay.addWidget(grp_manual)

        # ── Area under peak ───────────────────────────────────
        grp_area   = _grp("Area under peak")
        ar_lay = QtWidgets.QVBoxLayout(grp_area)

        self.area_mode_cb = QtWidgets.QCheckBox("Area fill mode (click L, then R bound)")
        ar_lay.addWidget(self.area_mode_cb)

        self.area_fill_color_btn = QtWidgets.QPushButton("Fill color…")
        self._area_fill_color = "#4488ff"
        self.area_fill_color_btn.setStyleSheet(f"background-color:{self._area_fill_color};")
        self.area_fill_color_btn.clicked.connect(self._pick_area_color)
        ar_lay.addWidget(self.area_fill_color_btn)

        self.area_alpha_spin = QtWidgets.QDoubleSpinBox()
        self.area_alpha_spin.setRange(0.05, 1.0); self.area_alpha_spin.setValue(0.25)
        self.area_alpha_spin.setSingleStep(0.05)
        ar_lay.addWidget(QtWidgets.QLabel("Fill opacity:"))
        ar_lay.addWidget(self.area_alpha_spin)

        self.area_result_label = QtWidgets.QLabel("Area: -")
        self.area_result_label.setWordWrap(True)
        ar_lay.addWidget(self.area_result_label)

        clear_areas_btn = QtWidgets.QPushButton("Clear all fills")
        clear_areas_btn.clicked.connect(self._clear_area_fills)
        ar_lay.addWidget(clear_areas_btn)
        lay.addWidget(grp_area)

        # ── Peak list color legend ────────────────────────────
        grp_legend = _grp("Peak list colors (legend)")
        self._peak_list_legend_layout = QtWidgets.QVBoxLayout(grp_legend)
        self._rebuild_peak_list_legend()
        lay.addWidget(grp_legend)

        lay.addStretch()

        reset_peaks_btn = QtWidgets.QPushButton("↺  Reset all to defaults")
        reset_peaks_btn.setToolTip("Restore every Peaks setting to its factory default.")
        reset_peaks_btn.setStyleSheet(
            "QPushButton { color: #c0392b; border: 1px solid #c0392b; "
            "border-radius: 4px; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #fdecea; }")
        reset_peaks_btn.clicked.connect(self._reset_peaks_defaults)
        lay.addWidget(reset_peaks_btn)

        # internal state for area measurement
        self._area_click_x = []
        return w

    def _on_label_thr_mode_changed(self):
        mode = self.label_thr_mode_combo.currentText()
        if mode == "SNR":
            self.label_threshold_spin.setRange(0.0, 50.0)
            self.label_threshold_spin.setValue(3.0)
            self.label_threshold_spin.setSingleStep(0.1)
            self.label_threshold_spin.setSuffix("  SNR")
        else:
            self.label_threshold_spin.setRange(0.0, 100.0)
            self.label_threshold_spin.setValue(5.0)
            self.label_threshold_spin.setSingleStep(1.0)
            self.label_threshold_spin.setSuffix(" % of max")
        self._update_auto_labels()

    def _rebuild_peak_list_legend(self):
        while self._peak_list_legend_layout.count():
            item = self._peak_list_legend_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        for row in custom_peak_rows:   # custom_peak_rows from main app
            name  = row["label_input"].text() or "Unnamed"
            color = row.get("color", QtGui.QColor("#888888"))
            hex_c = color.name() if isinstance(color, QtGui.QColor) else str(color)
            r_w   = QtWidgets.QWidget()
            r_l   = QtWidgets.QHBoxLayout(r_w)
            r_l.setContentsMargins(0, 0, 0, 0)
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color:{hex_c}; border:1px solid #555;")
            r_l.addWidget(swatch)
            r_l.addWidget(QtWidgets.QLabel(name))
            self._peak_list_legend_layout.addWidget(r_w)

    def _on_figsize_w_changed(self, new_w):
        if hasattr(self, "aspect_lock_cb") and self.aspect_lock_cb.isChecked():
            self.fig_h_spin.blockSignals(True)
            self.fig_h_spin.setValue(round(new_w * self._aspect_ratio))
            self.fig_h_spin.blockSignals(False)

    def _copy_to_clipboard(self):
        import io
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        buf.seek(0)
        img = QtGui.QImage()
        img.loadFromData(buf.getvalue(), "PNG")
        QtWidgets.QApplication.clipboard().setImage(img)

    def _build_annotations_tab(self):
        w  = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(6)

        def _grp(title, color="#7b3f9e"):
            """Annotations-tab group: horizontal-lines header."""
            g = QtWidgets.QGroupBox(title)
            g.setStyleSheet(
                f"QGroupBox {{ border:1px solid {color}; border-radius:4px; margin-top:8px; padding-top:6px; font-weight:bold; }}"
                f"QGroupBox::title {{ subcontrol-origin:margin; subcontrol-position:top left; "
                f"padding:2px 6px; background-color:{color}; color:white; border-radius:3px; "
                f"background-image: repeating-linear-gradient(0deg,rgba(255,255,255,.22) 0,rgba(255,255,255,.22) 2px,transparent 2px,transparent 6px); }}")
            return g

        def _row(label_text, widget):
            """Helper: horizontal label + widget pair that wraps."""
            rw = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(rw)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.addWidget(QtWidgets.QLabel(label_text))
            rl.addWidget(widget, stretch=1)
            return rw

        # ── Free text ─────────────────────────────────────────
        grp_txt = _grp("📝  Text annotation")
        t_lay   = QtWidgets.QVBoxLayout(grp_txt)
        self.ann_text_edit = QtWidgets.QLineEdit("Label")
        self.ann_text_fontsize = QtWidgets.QSpinBox()
        self.ann_text_fontsize.setRange(6, 48); self.ann_text_fontsize.setValue(11)
        add_text_btn = QtWidgets.QPushButton("Click on plot to place")
        add_text_btn.setCheckable(True)
        add_text_btn.toggled.connect(lambda v: setattr(self, "_placing_text", v))
        t_lay.addWidget(_row("Text:", self.ann_text_edit))
        t_lay.addWidget(_row("Size:", self.ann_text_fontsize))
        t_lay.addWidget(add_text_btn)
        tip = QtWidgets.QLabel(
            "Right-click to delete.\n"
            "Left-click and drag to move the annotation.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:gray; font-size:10px;")
        t_lay.addWidget(tip)
        lay.addWidget(grp_txt)

        # ── Symbol over peak ──────────────────────────────────
        grp_sym = _grp("★  Symbol over peak")
        s_lay   = QtWidgets.QVBoxLayout(grp_sym)
        self.sym_combo = QtWidgets.QComboBox()
        self.sym_combo.addItems(["★ Star", "● Circle", "▲ Triangle", "■ Square", "✦ Diamond"])
        self.sym_size_spin = QtWidgets.QSpinBox()
        self.sym_size_spin.setRange(4, 40); self.sym_size_spin.setValue(12)
        self.sym_color_btn = QtWidgets.QPushButton("  Pick color…")
        self._sym_color = "#ff3b30"
        self.sym_color_btn.setStyleSheet(f"background-color:{self._sym_color};")
        self.sym_color_btn.clicked.connect(self._pick_sym_color)
        add_sym_btn = QtWidgets.QPushButton("Click on plot to place")
        add_sym_btn.setCheckable(True)
        add_sym_btn.toggled.connect(lambda v: setattr(self, "_placing_symbol", v))
        s_lay.addWidget(_row("Symbol:", self.sym_combo))
        s_lay.addWidget(_row("Size:", self.sym_size_spin))
        s_lay.addWidget(self.sym_color_btn)
        s_lay.addWidget(add_sym_btn)
        tip = QtWidgets.QLabel(
            "Right-click to delete.\n"
            "Left-click and drag to move the annotation.")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:gray; font-size:10px;")
        s_lay.addWidget(tip)
        lay.addWidget(grp_sym)

        # ── Highlight zone ────────────────────────────────────
        grp_zone = _grp("▭  Highlight zone")
        z_lay    = QtWidgets.QVBoxLayout(grp_zone)
        self.zone_color_btn = QtWidgets.QPushButton("  Pick color…")
        self._zone_color = "#ffcc00"
        self.zone_color_btn.setStyleSheet(f"background-color:{self._zone_color};")
        self.zone_color_btn.clicked.connect(self._pick_zone_color)
        self.zone_alpha_spin = QtWidgets.QDoubleSpinBox()
        self.zone_alpha_spin.setRange(0.05, 1.0); self.zone_alpha_spin.setValue(0.2)
        self.zone_alpha_spin.setSingleStep(0.05)
        add_zone_btn = QtWidgets.QPushButton("Click L then R bound on plot")
        add_zone_btn.setCheckable(True)
        add_zone_btn.toggled.connect(lambda v: setattr(self, "_placing_zone", v))
        z_lay.addWidget(self.zone_color_btn)
        z_lay.addWidget(_row("Opacity:", self.zone_alpha_spin))
        z_lay.addWidget(add_zone_btn)
        lay.addWidget(grp_zone)

        # ── Arrow ─────────────────────────────────────────────
        grp_arrow = _grp("→  Arrow annotation")
        ar_lay    = QtWidgets.QVBoxLayout(grp_arrow)
        self.arrow_color_btn = QtWidgets.QPushButton("  Pick color…")
        self._arrow_color = "#222222"
        self.arrow_color_btn.setStyleSheet(f"background-color:{self._arrow_color};")
        self.arrow_color_btn.clicked.connect(self._pick_arrow_color)
        self.arrow_width_spin = QtWidgets.QDoubleSpinBox()
        self.arrow_width_spin.setRange(0.5, 6.0); self.arrow_width_spin.setValue(1.5)
        self.arrow_width_spin.setSingleStep(0.5)
        add_arrow_btn = QtWidgets.QPushButton("Click tail then head on plot")
        add_arrow_btn.setCheckable(True)
        add_arrow_btn.toggled.connect(lambda v: (
            setattr(self, "_placing_arrow", v),
            setattr(self, "_arrow_tail", None)))
        ar_lay.addWidget(self.arrow_color_btn)
        ar_lay.addWidget(_row("Width:", self.arrow_width_spin))
        ar_lay.addWidget(add_arrow_btn)
        lay.addWidget(grp_arrow)

        # ── Remove last annotation ────────────────────────────

        # ── Clipboard / undo ─────────────────────────────────
        copy_btn = QtWidgets.QPushButton("📋  Copy figure to clipboard")
        copy_btn.clicked.connect(self._copy_to_clipboard)
        lay.addWidget(copy_btn)

        undo_ann_btn = QtWidgets.QPushButton("⟵ Remove last annotation")
        undo_ann_btn.clicked.connect(self._remove_last_annotation)
        lay.addWidget(undo_ann_btn)

        clear_ann_btn = QtWidgets.QPushButton("Clear all annotations")
        clear_ann_btn.clicked.connect(self._clear_annotations)
        lay.addWidget(clear_ann_btn)

        lay.addStretch()

        # internal flags
        self._placing_text   = False
        self._placing_symbol = False
        self._placing_zone   = False
        self._placing_arrow  = False
        self._arrow_tail     = None
        self._zone_click_x   = None
        # store button refs so we can uncheck them after placing
        self._add_text_btn   = add_text_btn
        self._add_sym_btn    = add_sym_btn
        self._add_zone_btn   = add_zone_btn
        self._add_arrow_btn  = add_arrow_btn
        return w
    def _pick_arrow_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._arrow_color), self)
        if c.isValid():
            self._arrow_color = c.name()
            self.arrow_color_btn.setStyleSheet(f"background-color:{self._arrow_color};")

    def _build_recurrent_bar(self):
        """
        Bottom bar: shows auto-parsed header variables (date, sample, mode, dt …)
        with editable QLineEdit widgets; changes live-update the plot subtitle.
        """
        bar = QtWidgets.QWidget()
        bar.setFixedHeight(48)
        bar.setStyleSheet("background:#f0f0f0; border-top:1px solid #ccc;")
        h   = QtWidgets.QHBoxLayout(bar)
        h.setContentsMargins(8, 4, 8, 4)
        h.setSpacing(8)

        h.addWidget(QtWidgets.QLabel("Header vars:"))

        # ── key=value pairs parsed from filename ──────────────
        self._header_edits = {}       # {key: QLineEdit}
        default_vars = self._parse_header_vars()
        for k, v in default_vars.items():
            h.addWidget(QtWidgets.QLabel(f"{k}:"))
            edit = QtWidgets.QLineEdit(str(v))
            edit.setFixedWidth(90)
            edit.textChanged.connect(lambda _, k=k, e=edit:
                (self._header_vars.update({k: e.text()}), self._draw()))
            self._header_edits[k] = edit
            h.addWidget(edit)
            self._header_vars[k] = v

        # ── free subtitle text ────────────────────────────────
        h.addWidget(QtWidgets.QLabel("  Subtitle:"))
        self.subtitle_edit = QtWidgets.QLineEdit("")
        self.subtitle_edit.setMinimumWidth(180)
        self.subtitle_edit.textChanged.connect(self._draw)
        h.addWidget(self.subtitle_edit, stretch=1)

        return bar

    def _parse_header_vars(self):
        """
        Extract known tokens from the current file name.
        Returns dict like {'date':'2026-02-25','sample':'Water_H2O','mode':'neg','dt':'071'}.
        """
        path = combo.currentData() or ""
        name = os.path.splitext(os.path.basename(path))[0]
        out  = {}
        # date  2026-02-25
        m = re.match(r'(\d{4}-\d{2}-\d{2})', name)
        if m: out["date"] = m.group(1)
        # mode
        if "_neg_" in name: out["mode"] = "neg"
        elif "_pos_" in name: out["mode"] = "pos"
        # dt
        md = re.search(r'_dt(\d+)', name, re.IGNORECASE)
        if md: out["dt"] = md.group(1)
        # sample - everything between date and mode
        parts = name.split("_")
        if len(parts) > 3:
            out["sample"] = "_".join(parts[1:max(2, len(parts)-4)])
        return out

    def _build_menubar(self):
        """Called from __init__ before _build_ui(); adds an internal menu bar."""
        mb = QtWidgets.QMenuBar(self)
        fm_ = mb.addMenu("File")

        save_plot_action   = QtWidgets.QAction("Save Plot to Project…", self)
        rename_plot_action = QtWidgets.QAction("Rename Current Plot…",  self)
        delete_plot_action = QtWidgets.QAction("Delete Current Plot",   self)
        export_action_pt   = QtWidgets.QAction("Export figure…",        self)

        save_plot_action.triggered.connect(self._save_plot_to_project)
        rename_plot_action.triggered.connect(self._rename_current_plot)
        delete_plot_action.triggered.connect(self._delete_current_plot)
        export_action_pt.triggered.connect(self._export_figure)

        fm_.addAction(save_plot_action)
        fm_.addAction(rename_plot_action)
        fm_.addAction(delete_plot_action)
        fm_.addSeparator()
        fm_.addAction(export_action_pt)

        # ── Plots switcher menu (populated dynamically) ──
        self._plots_menu = mb.addMenu("Plots")
        self._plots_menu.aboutToShow.connect(self._populate_plots_menu)

        return mb

    def _build_plot_switcher_bar(self):
        """A thin toolbar below the menu bar for navigating saved plots."""
        bar = QtWidgets.QWidget()
        bar.setFixedHeight(28)
        lay = QtWidgets.QHBoxLayout(bar)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(4)

        lay.addWidget(QtWidgets.QLabel("Plot:"))

        self._plot_switcher_combo = QtWidgets.QComboBox()
        self._plot_switcher_combo.setMinimumWidth(160)
        self._plot_switcher_combo.setToolTip("Switch between saved plots")
        self._plot_switcher_combo.activated.connect(self._on_switcher_combo_activated)
        lay.addWidget(self._plot_switcher_combo, stretch=1)

        prev_btn = QtWidgets.QPushButton("◀")
        prev_btn.setFixedWidth(26)
        prev_btn.setToolTip("Previous plot")
        prev_btn.clicked.connect(self._switcher_prev)
        lay.addWidget(prev_btn)

        next_btn = QtWidgets.QPushButton("▶")
        next_btn.setFixedWidth(26)
        next_btn.setToolTip("Next plot")
        next_btn.clicked.connect(self._switcher_next)
        lay.addWidget(next_btn)

        new_btn = QtWidgets.QPushButton("＋ New")
        new_btn.setFixedWidth(54)
        new_btn.setToolTip("Save current state as a new plot")
        new_btn.clicked.connect(self._new_plot)
        lay.addWidget(new_btn)

        return bar

    def _refresh_plot_switcher(self):
        """Sync the switcher combo with self._saved_plots."""
        c = self._plot_switcher_combo
        c.blockSignals(True)
        c.clear()
        c.addItem("- unsaved -")          # index 0 = no saved slot active
        for entry in self._saved_plots:
            c.addItem(entry["name"])
        # +1 because index 0 is the "unsaved" placeholder
        target = self._current_plot_idx + 1 if self._current_plot_idx >= 0 else 0
        c.setCurrentIndex(target)
        c.blockSignals(False)

    def _on_switcher_combo_activated(self, combo_idx):
        if combo_idx == 0:
            return   # "unsaved" placeholder, ignore
        self._switch_to_plot(combo_idx - 1)
        self._refresh_plot_switcher()

    def _switcher_prev(self):
        if not self._saved_plots:
            return
        idx = (self._current_plot_idx - 1) % len(self._saved_plots)
        self._switch_to_plot(idx)
        self._refresh_plot_switcher()

    def _switcher_next(self):
        if not self._saved_plots:
            return
        idx = (self._current_plot_idx + 1) % len(self._saved_plots)
        self._switch_to_plot(idx)
        self._refresh_plot_switcher()

    # ─────────────────────────────────────────────────────────
    #  Multi-plot helpers
    # ─────────────────────────────────────────────────────────

    def _current_plot_data(self):
        """Serialize the current plot state into a dict."""
        return {
            "version":     self.PROJECT_VERSION,
            "spectra":     [{"path":      s["path"],
                             "name":      s["name"],
                             "label":     s["label"],
                             "color":     s["color"],
                             "linewidth": s.get("linewidth", 0.5),
                             "linestyle": s.get("linestyle", "solid")}
                            for s in self._spectra],
            "peak_lists":  [{"name":    r["label_input"].text(),
                             "toggled": r["checkbox"].isChecked()}
                            for r in custom_peak_rows],
            "manual_labels": [{"mz":       lb["mz"],
                               "text":     lb["text"],
                               "x_offset": lb.get("x_offset", 0),
                               "y_offset": lb.get("y_offset", 0)}
                              for lb in self._peak_labels if lb.get("manual")],
            "area_fills":  [{"x0": f["x0"], "x1": f["x1"],
                             "color": f["color"], "alpha": f["alpha"]}
                            for f in self._area_fills],
            "annotations": [{"type":   a["type"],
                             "x":      a["x"], "y": a["y"],
                             "x2":     a.get("x2"),
                             "y2":     a.get("y2"),
                             "text":   a.get("text", ""),
                             "symbol": a.get("symbol", ""),
                             "color":  a.get("color", "#000000"),
                             "size":   a.get("size", 11),
                             "width":  a.get("width", 1.5)}
                            for a in self._annotations],
             "appearance": {
                "xlabel":          self.xlabel_edit.text(),
                "ylabel":          self.ylabel_edit.text(),
                "axes_label_size": self.axes_label_size.value(),
                "tick_size":       self.tick_size.value(),
                "font":            self.font_combo.currentFont().family(),
                "bg_color":        self._bg_color,
                "grid":            self.grid_cb.isChecked(),
                "grid_alpha":      self.grid_alpha_spin.value(),
                "grid_style":      self.grid_style_combo.currentText(),
                "logy":            self.logy_cb.isChecked(),
                "xmin":            self.xmin_edit.text(),
                "xmax":            self.xmax_edit.text(),
                "ymin":            self.ymin_edit.text(),
                "ymax":            self.ymax_edit.text(),
                "spine_top":       self.spine_top_cb.isChecked(),
                "spine_right":     self.spine_right_cb.isChecked(),
                "spine_bottom":    self.spine_bottom_cb.isChecked(),
                "spine_left":      self.spine_left_cb.isChecked(),
                "minor_ticks":     self.minor_ticks_cb.isChecked(),
                "minor_tick_size": self.minor_tick_size.value(),
                "title":           self.title_edit.text(),
                "title_size":      self.title_size.value(),
                "fig_w":           self.fig_w_spin.value(),
                "fig_h":           self.fig_h_spin.value(),
                "export_dpi":      self.export_dpi_spin.value(),
                "offset":          self.offset_spin.value(),
                "norm":            self.norm_combo.currentText(),
                "watermark":       self.watermark_edit.text(),
                "watermark_alpha": self.watermark_alpha_spin.value(),
                "clip_to_axes":    self.clip_to_axes_cb.isChecked(),
                "sigma_clip":      self.sigma_clip_cb.isChecked(),
                "sigma_n_sigma":   self.sigma_slider.value(),
                "span_width":      self.peak_span_width_spin.value(),
                "show_spans":      self.show_peak_spans_cb.isChecked(),
                "peak_list_labels": self.peak_list_labels_cb.isChecked(),
                "label_font":      self.label_font_combo.currentFont().family(),
                "label_fontsize":  self.label_fontsize_spin.value(),
                "label_angle":     self.label_angle_spin.value(),
                "label_color":     self._label_color,
                "label_offset":    self._label_offset,
                "auto_label":      self.auto_label_cb.isChecked(),
                "label_thr_mode":  self.label_thr_mode_combo.currentText(),
                "label_thr":       self.label_threshold_spin.value(),
                "label_int":       self.label_integer_cb.isChecked(),
                "label_highest":   self.label_highest_cb.isChecked(),
                "theme":           self.theme_combo.currentText(),
                "legend":          self.legend_cb.isChecked(),
                "legend_pos":      self.legend_pos.currentText(),
                "legend_fontsize": self.legend_fontsize.value(),
                "legend_frame":    self.legend_frame_cb.isChecked(),
                "legend_ncol":     self.legend_ncol_spin.value(),
                "legend_title":    self.legend_title_edit.text(),
                "legend_title_size": self.legend_title_size.value(),
                "legend_fancybox": self.legend_fancybox_cb.isChecked(),
                "legend_shadow":   self.legend_shadow_cb.isChecked(),
                "legend_alpha":    self.legend_alpha_spin.value(),
                "legend_edge_color": self._legend_edge_color,
                "legend_labelspacing": self.legend_labelspacing_spin.value(),
                "legend_handlelength": self.legend_handlelength_spin.value(),
                "legend_borderpad":    self.legend_borderpad_spin.value(),
            },
            "header_vars":   self._header_vars,
            "subtitle":      self.subtitle_edit.text(),
            "zoom": {
                "xlim": list(self.ax.get_xlim()),
                "ylim": list(self.ax.get_ylim()),
            },
            "aspect_lock":       self.aspect_lock_cb.isChecked(),
            "aspect_ratio":      self._aspect_ratio,
        }

    def _apply_plot_data(self, data):
        """Restore a plot state dict into the UI."""
        app_d = data.get("appearance", {})
        self.xlabel_edit.setText(app_d.get("xlabel", "m/z"))
        self.ylabel_edit.setText(app_d.get("ylabel", "Intensity"))
        self.axes_label_size.setValue(app_d.get("axes_label_size", 13))
        self.tick_size.setValue(app_d.get("tick_size", 11))
        self.font_combo.setCurrentFont(QtGui.QFont(app_d.get("font", "DejaVu Sans")))
        self._bg_color = app_d.get("bg_color", "#ffffff")
        self.grid_cb.setChecked(app_d.get("grid", False))
        self.grid_alpha_spin.setValue(app_d.get("grid_alpha", 0.4))
        idx = self.grid_style_combo.findText(app_d.get("grid_style", "--  dashed"))
        if idx >= 0: self.grid_style_combo.setCurrentIndex(idx)
        self.logy_cb.setChecked(app_d.get("logy", False))
        self.xmin_edit.setText(app_d.get("xmin", ""))
        self.xmax_edit.setText(app_d.get("xmax", ""))
        self.ymin_edit.setText(app_d.get("ymin", ""))
        self.ymax_edit.setText(app_d.get("ymax", ""))
        self.spine_top_cb.setChecked(app_d.get("spine_top", False))
        self.spine_right_cb.setChecked(app_d.get("spine_right", False))
        self.spine_bottom_cb.setChecked(app_d.get("spine_bottom", True))
        self.spine_left_cb.setChecked(app_d.get("spine_left", True))
        self.minor_ticks_cb.setChecked(app_d.get("minor_ticks", False))
        self.minor_tick_size.setValue(app_d.get("minor_tick_size", 3))
        self.title_edit.setText(app_d.get("title", ""))
        self.title_size.setValue(app_d.get("title_size", 13))
        self.fig_w_spin.setValue(app_d.get("fig_w", 1800))
        self.fig_h_spin.setValue(app_d.get("fig_h", 1200))
        self.export_dpi_spin.setValue(app_d.get("export_dpi", 500))
        self.offset_spin.setValue(app_d.get("offset", 0.0))
        norm_idx = self.norm_combo.findText(app_d.get("norm", "None"))
        if norm_idx >= 0: self.norm_combo.setCurrentIndex(norm_idx)
        self.watermark_edit.setText(app_d.get("watermark", ""))
        self.watermark_alpha_spin.setValue(app_d.get("watermark_alpha", 0.12))
        self.clip_to_axes_cb.setChecked(app_d.get("clip_to_axes", False))
        _sc = app_d.get("sigma_clip", False)
        self.sigma_clip_cb.setChecked(_sc)
        self.sigma_slider.setValue(app_d.get("sigma_n_sigma", 10))
        self.sigma_slider.setEnabled(_sc)
        self.sigma_label.setEnabled(_sc)
        self.peak_span_width_spin.setValue(app_d.get("span_width", 1.0))
        self.show_peak_spans_cb.setChecked(app_d.get("show_spans", True))
        self.peak_list_labels_cb.setChecked(app_d.get("peak_list_labels", False))
        self.label_font_combo.setCurrentFont(
            QtGui.QFont(app_d.get("label_font", "DejaVu Sans")))
        self.label_fontsize_spin.setValue(app_d.get("label_fontsize", 9))
        self.label_angle_spin.setValue(app_d.get("label_angle", 90))
        self._label_color  = app_d.get("label_color", "#222222")
        self._label_offset = app_d.get("label_offset", 0.0)
        self.auto_label_cb.setChecked(app_d.get("auto_label", False))
        thr_idx = self.label_thr_mode_combo.findText(app_d.get("label_thr_mode", "% of max intensity"))
        if thr_idx >= 0: self.label_thr_mode_combo.setCurrentIndex(thr_idx)
        self.label_threshold_spin.setValue(app_d.get("label_thr", 5.0))
        self.label_integer_cb.setChecked(app_d.get("label_int", True))
        self.label_highest_cb.setChecked(app_d.get("label_highest", True))
        theme_idx = self.theme_combo.findText(app_d.get("theme", "Clean White"))
        if theme_idx >= 0: self.theme_combo.setCurrentIndex(theme_idx)
        self.legend_cb.setChecked(app_d.get("legend", True))
        leg_pos_idx = self.legend_pos.findText(app_d.get("legend_pos", "upper right"))
        if leg_pos_idx >= 0: self.legend_pos.setCurrentIndex(leg_pos_idx)
        self.legend_fontsize.setValue(app_d.get("legend_fontsize", 11))
        self.legend_frame_cb.setChecked(app_d.get("legend_frame", True))
        self.legend_ncol_spin.setValue(app_d.get("legend_ncol", 1))
        self.legend_title_edit.setText(app_d.get("legend_title", ""))
        self.legend_title_size.setValue(app_d.get("legend_title_size", 10))
        self.legend_fancybox_cb.setChecked(app_d.get("legend_fancybox", False))
        self.legend_shadow_cb.setChecked(app_d.get("legend_shadow", False))
        self.legend_alpha_spin.setValue(app_d.get("legend_alpha", 0.8))
        self._legend_edge_color = app_d.get("legend_edge_color", "#888888")
        self.legend_labelspacing_spin.setValue(app_d.get("legend_labelspacing", 0.5))
        self.legend_handlelength_spin.setValue(app_d.get("legend_handlelength", 2.0))
        self.legend_borderpad_spin.setValue(app_d.get("legend_borderpad", 0.4))

        # Build a name→df lookup from the currently active main-window overlays
        # so spectra with no saved path (overlays) can be restored from live data.
        _live_ov = {}
        if df is not None:
            _live_ov[combo.currentText()] = df
        for ov in overlay_list:
            if ov["toggle"].isChecked() and ov["df"] is not None:
                _live_ov[ov["toggle"].text()] = ov["df"]

        self._spectra = []
        for sp_data in data.get("spectra", []):
            p    = sp_data.get("path", "")
            name = sp_data.get("name", os.path.basename(p) if p else "")
            sp_df = None

            if p and os.path.exists(p):
                try:
                    sp_df = read_spectrum_file(p)
                except Exception:
                    pass
            elif name in _live_ov:
                # No file path - restore from the currently loaded overlay
                sp_df = _live_ov[name].copy()

            if sp_df is not None:
                self._spectra.append({
                    "path":      p,
                    "name":      name,
                    "label":     sp_data.get("label", name),
                    "df":        sp_df,
                    "color":     sp_data.get("color", "#1f77b4"),
                    "linewidth": sp_data.get("linewidth", 0.5),
                    "linestyle": sp_data.get("linestyle", "solid"),
                })

        if self._spectra:
            self._rebuild_per_spectrum_rows()
            self._rebuild_legend_rows()

        for saved, row in zip(data.get("peak_lists", []), custom_peak_rows):
            row["checkbox"].setChecked(saved.get("toggled", True))

        self._peak_labels = [l for l in self._peak_labels if not l.get("manual")]
        for lb in data.get("manual_labels", []):
            self._peak_labels.append({**lb, "manual": True})

        self._area_fills  = data.get("area_fills", [])
        self._annotations = data.get("annotations", [])
        self._header_vars = data.get("header_vars", {})
        self.subtitle_edit.setText(data.get("subtitle", ""))
        # Restore zoom if saved, otherwise autoscale
        saved_zoom = data.get("zoom")
        if saved_zoom:
            self._force_autoscale = False
            self._draw()
            self.ax.set_xlim(saved_zoom["xlim"])
            self.ax.set_ylim(saved_zoom["ylim"])
            self.canvas.draw_idle()
        else:
            self._force_autoscale = True
            self._draw()

        self.aspect_lock_cb.setChecked(data.get("aspect_lock", False))
        self._aspect_ratio = data.get("aspect_ratio", self._aspect_ratio)

    def _save_plot_to_project(self):
        """Save or overwrite the current plot into the in-memory plot list."""
        if self._current_plot_idx >= 0:
            self._saved_plots[self._current_plot_idx]["data"] = self._current_plot_data()
            name = self._saved_plots[self._current_plot_idx]["name"]
            QtWidgets.QMessageBox.information(
                self, "Plot Saved", f"Plot \"{name}\" updated in project.")
        else:
            name, ok = QtWidgets.QInputDialog.getText(
                self, "Save Plot", "Plot name:",
                text=f"Plot {len(self._saved_plots) + 1}")
            if not ok or not name.strip():
                return
            name = name.strip()
            self._saved_plots.append({"name": name, "data": self._current_plot_data()})
            self._current_plot_idx = len(self._saved_plots) - 1
            QtWidgets.QMessageBox.information(
                self, "Plot Saved",
                f"Plot \"{name}\" added to project.\n"
                "Save the main project (File → Save Project) to persist it.")
        self._refresh_plot_switcher()

    def _new_plot(self):
        """Always create a new plot slot from the current state."""
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Plot", "Plot name:",
            text=f"Plot {len(self._saved_plots) + 1}")
        if not ok or not name.strip():
            return
        # Auto-save the current slot before switching away
        if self._current_plot_idx >= 0:
            self._saved_plots[self._current_plot_idx]["data"] = self._current_plot_data()
        name = name.strip()
        self._saved_plots.append({"name": name, "data": self._current_plot_data()})
        self._current_plot_idx = len(self._saved_plots) - 1
        self._refresh_plot_switcher()

    def _rename_current_plot(self):
        if self._current_plot_idx < 0:
            QtWidgets.QMessageBox.information(
                self, "No plot", "Save this plot to the project first.")
            return
        old = self._saved_plots[self._current_plot_idx]["name"]
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Plot", "New name:", text=old)
        if ok and name.strip():
            self._saved_plots[self._current_plot_idx]["name"] = name.strip()
            self._refresh_plot_switcher()

    def _delete_current_plot(self):
        if self._current_plot_idx < 0:
            QtWidgets.QMessageBox.information(
                self, "No plot", "No saved plot is currently active.")
            return
        name = self._saved_plots[self._current_plot_idx]["name"]
        btn = QtWidgets.QMessageBox.question(
            self, "Delete Plot", f"Delete plot \"{name}\" from the project?")
        if btn != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._saved_plots.pop(self._current_plot_idx)
        self._current_plot_idx = -1
        self._refresh_plot_switcher()

    def _switch_to_plot(self, idx):
        """Switch the UI to the plot at index idx, auto-saving the current one first."""
        if idx < 0 or idx >= len(self._saved_plots):
            return
        if self._current_plot_idx >= 0:
            self._saved_plots[self._current_plot_idx]["data"] = self._current_plot_data()
        self._current_plot_idx = idx
        self._apply_plot_data(self._saved_plots[idx]["data"])

    def _populate_plots_menu(self):
        """Rebuild the Plots menu just before it opens."""
        self._plots_menu.clear()
        if not self._saved_plots:
            empty = QtWidgets.QAction("(no plots saved yet)", self)
            empty.setEnabled(False)
            self._plots_menu.addAction(empty)
            return
        for i, entry in enumerate(self._saved_plots):
            action = QtWidgets.QAction(entry["name"], self)
            if i == self._current_plot_idx:
                action.setCheckable(True)
                action.setChecked(True)
            action.triggered.connect(lambda checked, idx=i: self._switch_to_plot(idx))
            self._plots_menu.addAction(action)

    def _get_all_plots_data(self):
        """Return all saved plots for embedding in the main .drp file.
        Auto-saves current state into the active slot first."""
        if self._current_plot_idx >= 0:
            self._saved_plots[self._current_plot_idx]["data"] = self._current_plot_data()
        elif self._spectra or self._annotations or self._area_fills:
            self._saved_plots.append({
                "name": "Unsaved plot",
                "data": self._current_plot_data()
            })
            self._current_plot_idx = len(self._saved_plots) - 1
        return [{"name": e["name"], "data": e["data"]} for e in self._saved_plots]

    def _restore_all_plots_data(self, plots_list):
        """Restore all plots from the main .drp file. Opens the first plot."""
        self._saved_plots = [{"name": e["name"], "data": e["data"]} for e in plots_list]
        self._current_plot_idx = -1
        if self._saved_plots:
            self._switch_to_plot(0)
        self._refresh_plot_switcher()

        # ─────────────────────────────────────────────────────────
    #  Data loading
    # ─────────────────────────────────────────────────────────
    def _load_current_spectra(self):
        """
        Pulls the main spectrum + all active overlays from the running
        main application and populates self._spectra.
        Preserves per-spectrum style (color, linewidth, linestyle, label)
        for spectra that were already loaded, matched by name.
        """
        # Build a lookup of existing style settings keyed by spectrum name
        _existing = {sp["name"]: sp for sp in self._spectra}

        self._spectra = []
        palette = ["#1f77b4","#ff7f0e","#2ca02c","#d62728","#9467bd",
                   "#8c564b","#e377c2","#7f7f7f","#bcbd22","#17becf"]
        idx = 0

        # main spectrum
        if df is not None:
            name = combo.currentText()
            prev = _existing.get(name, {})
            self._spectra.append({
                "path":      combo.currentData() or "",
                "name":      name,
                "label":     prev.get("label", "Main file"),
                "df":        df.copy(),
                "color":     prev.get("color", palette[idx % len(palette)]),
                "linewidth": prev.get("linewidth", 0.5),
                "linestyle": prev.get("linestyle", "solid"),
            })
            idx += 1

        # overlays
        for ov in overlay_list:
            if ov["toggle"].isChecked() and ov["df"] is not None:
                name = ov["toggle"].text() or f"Overlay {idx}"
                prev = _existing.get(name, {})
                # default color: use overlay row color, then palette
                hex_c = palette[idx % len(palette)]
                if "color_btn" in ov:
                    try:
                        style = ov["color_btn"].styleSheet()
                        m2    = re.search(r'background:\s*(#[0-9a-fA-F]{6})', style)
                        if m2: hex_c = m2.group(1)
                    except Exception:
                        pass
                self._spectra.append({
                    "path":      "",
                    "name":      name,
                    "label":     prev.get("label", name),
                    "df":        ov["df"].copy(),
                    "color":     prev.get("color", hex_c),
                    "linewidth": prev.get("linewidth", 0.5),
                    "linestyle": prev.get("linestyle", "solid"),
                })
                idx += 1

        self._rebuild_per_spectrum_rows()
        self._rebuild_legend_rows()
        self._update_auto_labels()

    # ─────────────────────────────────────────────────────────
    #  Master draw
    # ─────────────────────────────────────────────────────────
    def _draw(self):
        ax  = self.ax
        fig = self.fig

        # Preserve zoom across redraws unless _force_autoscale is set.
        # _force_autoscale is True on first draw and after loading a new plot.
        _xl = ax.get_xlim()
        _yl = ax.get_ylim()
        _has_zoom = self._spectra and not getattr(self, "_force_autoscale", True)
        self._force_autoscale = False   # consumed - next draw will preserve zoom

        ax.clear()

        # ── background ────────────────────────────────────────
        ax.set_facecolor(self._bg_color)
        fig.set_facecolor(self._bg_color)

        # ── font setup ────────────────────────────────────────
        font_family = self.font_combo.currentFont().family()
        matplotlib.rcParams["font.family"] = font_family

        # ── normalisation ─────────────────────────────────────
        norm_mode = self.norm_combo.currentText()
        global_max = 1.0
        if norm_mode == "to highest overall" and self._spectra:
            global_max = max(
                sp["df"]["intensity"].max()
                for sp in self._spectra if not sp["df"].empty)

        def _normalise(series):
            if norm_mode == "None":
                return series
            if norm_mode == "0–1 (per spectrum)":
                mx = series.max()
                return series / mx if mx != 0 else series
            if norm_mode == "to highest overall":
                return series / global_max if global_max != 0 else series
            return series

        # ── sigma clipping ────────────────────────────────────
        _do_sigma = self.sigma_clip_cb.isChecked()
        _n_sigma  = self.sigma_slider.value() / 10.0

        def _apply_sigma_clip(series):
            if not _do_sigma:
                return series
            floor = _estimate_noise_floor(series.values, n_sigma=_n_sigma)
            return series.clip(lower=floor)

        # ── draw spectra with optional stacking ───────────────
        ls_map   = {"solid": "-", "dashed": "--", "dotted": ":", "dashdot": "-."}
        offset_f = self.offset_spin.value()   # fraction of global max per step

        # compute a common reference amplitude for offset
        if self._spectra and offset_f > 0:
            amp_ref = global_max if norm_mode == "to highest overall" else max(
                sp["df"]["intensity"].max()
                for sp in self._spectra if not sp["df"].empty)
        else:
            amp_ref = 1.0

        _do_mirror  = self.mirror_pairs_cb.isChecked()
        _mirror_odd = self.mirror_odd_cb.isChecked()
        for i, sp in enumerate(self._spectra):
            d        = sp["df"]
            y        = _apply_sigma_clip(_normalise(d["intensity"]))
            offset   = i * offset_f * amp_ref
            if _do_mirror:
                _flip_set = (0 if _mirror_odd else 1)
                if i % 2 == _flip_set:
                    # Flip so the baseline (floor) sits at the pair's offset,
                    # not at 0.  y was in [0..1]; flipped it spans [-1..0],
                    # then shift by +1 so the floor is at offset (not offset-1).
                    y = -y + 1.0
            ax.plot(d["mz"], y + offset,
                    color=sp["color"],
                    linewidth=sp.get("linewidth", 0.5),
                    linestyle=ls_map.get(sp.get("linestyle", "solid"), "-"),
                    label=sp["label"])

        # ── area fills ────────────────────────────────────────
        for fill in self._area_fills:
            if not self._spectra: continue
            main_df = self._spectra[0]["df"]
            mask = (main_df["mz"] >= fill["x0"]) & (main_df["mz"] <= fill["x1"])
            y_fill = _normalise(main_df["intensity"][mask])
            ax.fill_between(main_df["mz"][mask], 0, y_fill,
                            color=fill["color"], alpha=fill["alpha"])

        # ── peak list highlights ─ draw without disturbing autoscale ──
        if self.show_peak_spans_cb.isChecked():
            # Lock current data limits before adding spans so axvspan
            # doesn't expand the view to include out-of-range peak positions.
            ax.autoscale(enable=False)
            self._render_peak_list_highlights(ax, _normalise)
            ax.autoscale(enable=True)

        # ── peak labels (auto + manual) ───────────────────────
        self._render_peak_labels(ax)

        # ── annotations ───────────────────────────────────────
        self._render_annotations(ax)

        # ── axes cosmetics ────────────────────────────────────
        fsize = self.axes_label_size.value()
        ax.set_xlabel(self.xlabel_edit.text(), fontsize=fsize, fontfamily=font_family)
        ax.set_ylabel(self.ylabel_edit.text(), fontsize=fsize, fontfamily=font_family)
        ax.tick_params(axis='both', labelsize=self.tick_size.value())
        for lbl in (ax.get_xticklabels() + ax.get_yticklabels()):
            lbl.set_fontfamily(font_family)

        # ── Y scale ──────────────────────────────────────────
        if self.logy_cb.isChecked():
            ax.set_yscale("log")
        else:
            ax.set_yscale("linear")

        # ── clip to axes / padding ────────────────────────────
        if self.clip_to_axes_cb.isChecked():
            ax.margins(0)               # no auto-padding on either axis
            ax.autoscale_view(tight=True)
        else:
            ax.margins(x=0.02, y=0.05) # small default breathing room

        # ── axis range locks ──────────────────────────────────
        def _parse_range(edit):
            try:    return float(edit.text())
            except: return None
        xmin = _parse_range(self.xmin_edit); xmax = _parse_range(self.xmax_edit)
        ymin = _parse_range(self.ymin_edit); ymax = _parse_range(self.ymax_edit)

        # Restore saved zoom first (overridden below by any explicit lock values)
        if _has_zoom:
            ax.set_xlim(_xl)
            ax.set_ylim(_yl)

        if xmin is not None or xmax is not None:
            cur_xl = ax.get_xlim()
            ax.set_xlim(xmin if xmin is not None else cur_xl[0],
                        xmax if xmax is not None else cur_xl[1])
        if ymin is not None or ymax is not None:
            cur_yl = ax.get_ylim()
            ax.set_ylim(ymin if ymin is not None else cur_yl[0],
                        ymax if ymax is not None else cur_yl[1])

        # ── spines ───────────────────────────────────────────
        ax.spines["top"].set_visible(self.spine_top_cb.isChecked())
        ax.spines["right"].set_visible(self.spine_right_cb.isChecked())
        ax.spines["bottom"].set_visible(self.spine_bottom_cb.isChecked())
        ax.spines["left"].set_visible(self.spine_left_cb.isChecked())

        # ── minor ticks ───────────────────────────────────────
        if self.minor_ticks_cb.isChecked():
            ax.minorticks_on()
            ax.tick_params(axis='both', which='minor',
                           length=self.minor_tick_size.value())
        else:
            ax.minorticks_off()

        # ── grid ─────────────────────────────────────────────
        if self.grid_cb.isChecked():
            _gs_map = {"--  dashed": "--", ":  dotted": ":", "-  solid": "-", "-.  dash-dot": "-."}
            _gs  = _gs_map.get(self.grid_style_combo.currentText(), "--")
            _ga  = self.grid_alpha_spin.value()
            ax.grid(True, which='major', linestyle=_gs, alpha=_ga)
            if self.minor_ticks_cb.isChecked():
                ax.grid(True, which='minor', linestyle=_gs,
                        alpha=_ga * 0.5)   # minor grid at half opacity
        else:
            ax.grid(False, which='both')

        # ── title ────────────────────────────────────────────
        title_text = self.title_edit.text().strip()
        subtitle_parts = [f"{k}={v}" for k, v in self._header_vars.items() if v]
        custom_sub = self.subtitle_edit.text()
        if custom_sub: subtitle_parts.append(custom_sub)
        subtitle_text = "  |  ".join(subtitle_parts) if subtitle_parts else ""

        if title_text and subtitle_text:
            ax.set_title(f"{title_text}\n{subtitle_text}",
                         fontsize=self.title_size.value(),
                         fontfamily=font_family, pad=4)
        elif title_text:
            ax.set_title(title_text, fontsize=self.title_size.value(),
                         fontfamily=font_family, pad=4)
        elif subtitle_text:
            ax.set_title(subtitle_text, fontsize=9,
                         color="gray", fontfamily=font_family, pad=4)

        # ── legend ───────────────────────────────────────────
        if self.legend_cb.isChecked() and self._spectra:
            loc      = self.legend_pos.currentText()
            font_fam = self.label_font_combo.currentFont().family()
            fsize    = self.legend_fontsize.value()
            show_frame   = self.legend_frame_cb.isChecked()
            leg_title    = self.legend_title_edit.text().strip() or None
            leg_title_sz = self.legend_title_size.value()

            kw = dict(
                prop        = {"family": font_fam, "size": fsize},
                frameon     = show_frame,
                fancybox    = self.legend_fancybox_cb.isChecked(),
                shadow      = self.legend_shadow_cb.isChecked(),
                framealpha  = self.legend_alpha_spin.value(),
                edgecolor   = self._legend_edge_color if show_frame else "none",
                labelspacing    = self.legend_labelspacing_spin.value(),
                handlelength    = self.legend_handlelength_spin.value(),
                borderpad       = self.legend_borderpad_spin.value(),
                ncol        = self.legend_ncol_spin.value(),
                title       = leg_title,
                title_fontsize  = leg_title_sz,
            )

            if loc == "outside right":
                leg = ax.legend(loc="upper left",
                                bbox_to_anchor=(1.01, 1),
                                bbox_transform=ax.transAxes,
                                borderaxespad=0, **kw)
            elif loc == "outside bottom":
                leg = ax.legend(loc="upper center",
                                bbox_to_anchor=(0.5, -0.18),
                                bbox_transform=ax.transAxes,
                                borderaxespad=0, **kw)
            else:
                leg = ax.legend(loc=loc, **kw)

            # Make the frame line slightly thicker and the background
            # slightly off-white for a more polished look
            if show_frame and leg.get_frame() is not None:
                leg.get_frame().set_linewidth(0.8)

        # ── watermark ────────────────────────────────────────
        wm_text = self.watermark_edit.text().strip()
        if wm_text:
            ax.text(0.5, 0.5, wm_text,
                    transform=ax.transAxes,
                    fontsize=40, color="gray",
                    alpha=self.watermark_alpha_spin.value(),
                    ha="center", va="center",
                    rotation=30, zorder=0,
                    fontfamily=font_family)

        # ── theme chrome (applied last so it overrides matplotlib defaults) ──
        self._apply_theme(ax, fig)

        try:
            fig.set_layout_engine("constrained")
        except Exception:
            pass
        self.canvas.draw_idle()


    def _render_peak_list_highlights(self, ax, normalise_fn):
        """
        Draw vertical coloured spans for each toggled peak list,
        mirroring what the main window does with pyqtgraph highlights.
        Each peak ± 0.5 Da is shaded in the list's colour.
        """
        if not self._spectra:
            return
        ref_df = self._spectra[0]["df"]
        ymax   = ref_df["intensity"].max() if not ref_df.empty else 1.0

        # ── Collect spans + label info across all rows ───────────────────────
        _span_entries = []  # (mz, label_text, hex_color)
        half_w = self.peak_span_width_spin.value() / 2.0
        for row in custom_peak_rows:
            if not row["checkbox"].isChecked():
                continue
            color_raw = row.get("color", QtGui.QColor("#888888"))
            color_q   = color_raw[0] if isinstance(color_raw, list) else color_raw
            hex_c     = color_q.name() if isinstance(color_q, QtGui.QColor) else str(color_q)
            row_label = row["label_input"].text() or "Unnamed"
            peaks = parse_peaks_text(row["peaks_input"].text())
            if not peaks:
                continue
            first = True
            for mz in peaks:
                ax.axvspan(mz - half_w, mz + half_w,
                           color=hex_c, alpha=0.18,
                           label=row_label if first else "_nolegend_",
                           zorder=0)
                lbl_text = str(int(round(mz))) if self.label_integer_cb.isChecked() else f"{mz:.2f}"
                _span_entries.append((mz, lbl_text, hex_c))
                first = False

        if self.peak_list_labels_cb.isChecked() and _span_entries:
            lf   = self.label_font_combo.currentFont().family()
            lsz  = self.label_fontsize_spin.value()
            lang = self.label_angle_spin.value()
            stack_mode = self.label_overlap_stack_cb.isChecked()
            use_row_color = self.label_use_row_color_cb.isChecked()
            MERGE_TOL  = 0.6  # Da

            use_black_masses = self.label_use_black_masses_cb.isChecked()

            def _lbl_color(hex_c):
                if use_black_masses:
                    return "#000000"
                return hex_c if use_row_color else self._label_color

            if stack_mode:
                # Group by rounded mz, then step each label upward
                from collections import defaultdict as _dd
                _groups = _dd(list)
                for mz, lbl_text, hex_c in _span_entries:
                    _groups[round(mz)].append((mz, lbl_text, hex_c))
                for _key, entries in _groups.items():
                    ax_lo, ax_hi = ax.get_ylim()
                    step_frac = (ax_hi - ax_lo) * 0.06
                    for step_i, (mz, lbl_text, hex_c) in enumerate(entries):
                        y_val = self._get_peak_y(mz)
                        if y_val is None:
                            continue
                        y_pos = y_val + self._label_offset + step_i * step_frac
                        ax.annotate(lbl_text, xy=(mz, y_val), xytext=(mz, y_pos),
                                    fontsize=lsz, color=_lbl_color(hex_c), fontfamily=lf,
                                    rotation=lang, va="bottom", ha="center",
                                    annotation_clip=True)
            else:
                # Merge nearby mz values; join labels with commas
                merged = []
                for mz, lbl_text, hex_c in _span_entries:
                    placed = False
                    for grp in merged:
                        if abs(grp["mz"] - mz) <= MERGE_TOL:
                            grp["labels"].append((lbl_text, hex_c))
                            placed = True
                            break
                    if not placed:
                        merged.append({"mz": mz, "labels": [(lbl_text, hex_c)]})
                for grp in merged:
                    mz    = grp["mz"]
                    y_val = self._get_peak_y(mz)
                    if y_val is None:
                        continue
                    combined = ", ".join(t for t, _ in grp["labels"])
                    hex_c    = grp["labels"][0][1]
                    ax.annotate(combined, xy=(mz, y_val),
                                xytext=(mz, y_val + self._label_offset),
                                fontsize=lsz, color=_lbl_color(hex_c), fontfamily=lf,
                                rotation=lang, va="bottom", ha="center",
                                annotation_clip=True)

    # ─────────────────────────────────────────────────────────
    #  Peak label rendering
    # ─────────────────────────────────────────────────────────
    def _render_peak_labels(self, ax):
        """
        Renders all entries in self._peak_labels onto ax.
        Each entry: {mz, text, y_offset (fraction of ymax), manual: bool}
        """
        if not self._spectra: return
        ylo, yhi = ax.get_ylim() if ax.get_ylim()[1] != 1.0 else (0, 1)

        lf   = self.label_font_combo.currentFont().family()
        lsz  = self.label_fontsize_spin.value()
        lang = self.label_angle_spin.value()
        lcol = self._label_color

        for lb in self._peak_labels:
            mz   = lb["mz"]
            text = lb["text"]
            # find y value from highest spectrum at this mz
            y_val = self._get_peak_y(mz)
            if y_val is None: continue
            y_pos = y_val + self._label_offset + lb.get("y_offset", 0.0)
            ax.annotate(text,
                        xy=(mz, y_val),
                        xytext=(mz + lb.get("x_offset", 0), y_pos),
                        fontsize=lsz, color=lcol, fontfamily=lf,
                        rotation=lang, va="bottom", ha="center",
                        annotation_clip=True)

    def _get_peak_y(self, mz, tol=1.0):
        """Return the maximum intensity across all spectra within ±tol of mz."""
        best = None
        for sp in self._spectra:
            d   = sp["df"]
            sub = d[abs(d["mz"] - mz) <= tol]
            if sub.empty: continue
            v = sub["intensity"].max()
            if best is None or v > best: best = v
        return best

    # ─────────────────────────────────────────────────────────
    #  Auto label update
    # ─────────────────────────────────────────────────────────
    def _update_auto_labels(self):
        """
        Re-compute automatic peak labels:
        • threshold = label_threshold_spin.value() % of max intensity
        • if label_highest_cb: for spectra sharing a peak zone, label only
          the one with the highest peak there
        • if label_integer_cb: round mz to nearest int
        Adds results to self._peak_labels (removing previous auto ones).
        """
        # remove old auto labels
        self._peak_labels = [l for l in self._peak_labels if l.get("manual")]

        if not self.auto_label_cb.isChecked() or not self._spectra:
            self._draw()
            return

        thr_val     = self.label_threshold_spin.value()
        use_snr     = self.label_thr_mode_combo.currentText() == "SNR"
        use_highest = self.label_highest_cb.isChecked()
        use_int     = self.label_integer_cb.isChecked()

        from scipy.signal import find_peaks as _find_peaks

        # collect peaks per spectrum - ignore anything below _AR_MINIMUM_MASS
        all_peaks = []
        for si, sp in enumerate(self._spectra):
            d    = sp["df"]
            int_arr = d["intensity"].values
            ymax = int_arr.max()

            if use_snr:
                # Use a simple noise floor estimate (bottom 50 % of values)
                noise_floor = _estimate_noise_floor(int_arr, n_sigma=1.0)
                noise_floor = max(noise_floor, 1e-12)
                height_thr  = noise_floor * thr_val   # absolute height == SNR * noise
            else:
                height_thr  = ymax * (thr_val / 100.0)

            idxs, _ = _find_peaks(int_arr, height=height_thr, distance=3)
            for i in idxs:
                mz_val = d["mz"].iloc[i]
                if mz_val < _AR_MINIMUM_MASS:
                    continue
                all_peaks.append((mz_val, int_arr[i], si))

        # group peaks within 1 Da
        used = set()
        for mz, inten, si in sorted(all_peaks, key=lambda x: -x[1]):
            key = round(mz)
            if key in used: continue
            # if highest-only: check no other spectrum has a higher peak here
            if use_highest:
                competitors = [(m, iv, s) for m, iv, s in all_peaks
                               if abs(m - mz) <= 1.5 and s != si]
                if any(iv > inten for _, iv, _ in competitors):
                    continue
            used.add(key)
            display_mz = str(round(mz)) if use_int else f"{mz:.2f}"
            self._peak_labels.append({
                "mz":      mz,
                "text":    display_mz,
                "y_offset": 0.0,
                "manual":  False,
            })

        self._draw()

    # ─────────────────────────────────────────────────────────
    #  Shift all labels
    # ─────────────────────────────────────────────────────────
    def _shift_all_labels(self, direction):
        """Shift _label_offset by label_step_spin fraction of current y range."""
        ax = self.ax
        ylo, yhi = ax.get_ylim()
        step = self.label_step_spin.value() * (yhi - ylo)
        self._label_offset += direction * step
        self._draw()

    # ─────────────────────────────────────────────────────────
    #  Canvas mouse events
    # ─────────────────────────────────────────────────────────
    def _on_canvas_click(self, event):
        if event.inaxes != self.ax or event.xdata is None: return
        x, y = event.xdata, event.ydata

        # ── right-click on annotation → delete it ─────────────
        if event.button == 3 and not event.dblclick:
            i, ann = self._ann_hit_test(event)
            if ann is not None:
                self._annotations.remove(ann)
                self._draw()
                return

        # ── left-click on annotation → arm drag (drag only starts on motion) ──
        if event.button == 1 and not event.dblclick:
            i, ann = self._ann_hit_test(event)
            if ann is not None and ann.get("type") in ("text", "symbol"):
                self._armed_ann  = ann       # armed, not yet dragging
                self._drag_start = (x, y)
                return

        # ── double-click → manual peak ────────────────────────
        if event.dblclick and self.manual_peak_mode_cb.isChecked():
            self._add_manual_peak(x)
            return

        # ── area fill mode ────────────────────────────────────
        if self.area_mode_cb.isChecked():
            self._area_click_x.append(x)
            if len(self._area_click_x) == 2:
                x0, x1 = sorted(self._area_click_x)
                self._area_click_x = []
                area = self._compute_area(x0, x1)
                self._area_fills.append({
                    "x0": x0, "x1": x1,
                    "color": self._area_fill_color,
                    "alpha": self.area_alpha_spin.value()})
                self.area_result_label.setText(
                    f"Area [{x0:.1f}–{x1:.1f}]: {area:.4g}")
                self._draw()
            return

        if self._placing_text:
            self._annotations.append({
                "type": "text", "x": x, "y": y,
                "text": self.ann_text_edit.text(),
                "size": self.ann_text_fontsize.value(),
                "color": "#000000",
            })
            self._draw()
            return

        if self._placing_symbol:
            sym_map = {"★ Star":"*","● Circle":"o","▲ Triangle":"^",
                       "■ Square":"s","✦ Diamond":"D"}
            sym = sym_map.get(self.sym_combo.currentText(), "*")
            self._annotations.append({
                "type": "symbol", "x": x, "y": y,
                "symbol": sym,
                "size":   self.sym_size_spin.value(),
                "color":  self._sym_color,
            })
            self._draw()
            return

        if self._placing_arrow:
            if self._arrow_tail is None:
                self._arrow_tail = (x, y)
            else:
                x0, y0 = self._arrow_tail
                self._annotations.append({
                    "type":  "arrow",
                    "x":     x0, "y":  y0,
                    "x2":    x,  "y2": y,
                    "color": self._arrow_color,
                    "width": self.arrow_width_spin.value(),
                })
                self._arrow_tail = None
                self._placing_arrow = False
                self._add_arrow_btn.blockSignals(True)
                self._add_arrow_btn.setChecked(False)
                self._add_arrow_btn.blockSignals(False)
                self._draw()
            return

        if self._placing_zone:
            if self._zone_click_x is None:
                self._zone_click_x = x
            else:
                x0, x1 = sorted([self._zone_click_x, x])
                self._zone_click_x = None
                self._annotations.append({
                    "type": "zone", "x": x0, "y": x1,
                    "color": self._zone_color,
                    "alpha": self.zone_alpha_spin.value(),
                })
                self._placing_zone = False
                self._add_zone_btn.blockSignals(True)
                self._add_zone_btn.setChecked(False)
                self._add_zone_btn.blockSignals(False)
                self._draw()
            return

    # ── annotation hit-testing ─────────────────────────────────────────────
    def _ann_hit_test(self, event):
        """
        Return (index, annotation_dict) for the annotation whose rendered
        artist is closest to the click, or (None, None) if nothing is within
        12 pixels.  Supports text, symbol, and arrow annotations.
        """
        if not hasattr(self, "_last_ann_artists"):
            return None, None
        for i, (ann_dict, artist) in enumerate(self._last_ann_artists):
            if ann_dict.get("type") == "arrow":
                # Distance from click to the arrow line segment, in pixels
                try:
                    ax = self.ax
                    fig = self.canvas.figure
                    # Convert data coords → display (pixel) coords
                    def to_px(xd, yd):
                        return ax.transData.transform((xd, yd))
                    px, py   = event.x, event.y          # click in display coords
                    x1, y1   = to_px(ann_dict["x"],  ann_dict["y"])
                    x2, y2   = to_px(ann_dict["x2"], ann_dict["y2"])
                    # Point-to-segment distance
                    dx, dy   = x2 - x1, y2 - y1
                    seg_len2 = dx*dx + dy*dy
                    if seg_len2 == 0:
                        dist = ((px - x1)**2 + (py - y1)**2) ** 0.5
                    else:
                        t = max(0.0, min(1.0, ((px-x1)*dx + (py-y1)*dy) / seg_len2))
                        dist = ((px - (x1 + t*dx))**2 + (py - (y1 + t*dy))**2) ** 0.5
                    if dist <= 12:
                        return i, ann_dict
                except Exception:
                    pass
            else:
                try:
                    contains, _ = artist.contains(event)
                    if contains:
                        return i, ann_dict
                except Exception:
                    pass
        return None, None

    def _on_canvas_release(self, event):
        self._dragging_ann = None
        self._armed_ann    = None
        self._drag_start   = None

    def _on_canvas_motion(self, event):
        if event.inaxes == self.ax and event.xdata is not None:
            self._cursor_label.setText(
                f"  x = {event.xdata:.4g}    y = {event.ydata:.4g}")
            # promote armed → dragging on first motion with button held
            if event.button == 1:
                if self._dragging_ann is None and self._armed_ann is not None:
                    self._dragging_ann = self._armed_ann
                if self._dragging_ann is not None and self._drag_start is not None:
                    dx = event.xdata - self._drag_start[0]
                    dy = event.ydata - self._drag_start[1]
                    self._dragging_ann["x"] += dx
                    self._dragging_ann["y"] += dy
                    self._drag_start = (event.xdata, event.ydata)
                    self._draw()
        else:
            self._cursor_label.setText("  x = -    y = -")

    # ─────────────────────────────────────────────────────────
    #  Manual peak add
    # ─────────────────────────────────────────────────────────
    def _add_manual_peak(self, x):
        """Find local maximum nearest to x (within ±2 Da) in highest spectrum."""
        best_mz, best_y = None, -1
        for sp in self._spectra:
            d    = sp["df"]
            mask = (d["mz"] >= x - 2) & (d["mz"] <= x + 2)
            sub  = d[mask]
            if sub.empty: continue
            idx  = sub["intensity"].idxmax()
            if d.loc[idx, "intensity"] > best_y:
                best_y  = d.loc[idx, "intensity"]
                best_mz = d.loc[idx, "mz"]
        if best_mz is None: return
        use_int = self.label_integer_cb.isChecked()
        text    = str(round(best_mz)) if use_int else f"{best_mz:.2f}"
        self._peak_labels.append({
            "mz":      best_mz,
            "text":    text,
            "y_offset": 0.0,
            "manual":  True,
        })
        self._draw()

    def _clear_manual_labels(self):
        self._peak_labels = [l for l in self._peak_labels if not l.get("manual")]
        self._draw()

    # ─────────────────────────────────────────────────────────
    #  Area helpers
    # ─────────────────────────────────────────────────────────
    def _compute_area(self, x0, x1):
        if not self._spectra: return 0.0
        d    = self._spectra[0]["df"]
        mask = (d["mz"] >= x0) & (d["mz"] <= x1)
        sub  = d[mask]
        if len(sub) < 2: return 0.0
        return float(np.trapz(sub["intensity"].values, sub["mz"].values))

    def _clear_area_fills(self):
        self._area_fills = []
        self.area_result_label.setText("Area: -")
        self._draw()

    # ─────────────────────────────────────────────────────────
    #  Annotation rendering
    # ─────────────────────────────────────────────────────────
    def _render_annotations(self, ax):
        font_family = self.font_combo.currentFont().family()
        self._last_ann_artists = []   # [(ann_dict, artist), …] for hit-testing
        for ann in self._annotations:
            t = ann["type"]
            if t == "arrow":
                if ann.get("x2") is None or ann.get("y2") is None:
                    continue
                artist = ax.annotate("",
                    xy=(ann["x2"], ann["y2"]),
                    xytext=(ann["x"], ann["y"]),
                    arrowprops=dict(
                        arrowstyle="->",
                        color=ann.get("color", "#222222"),
                        lw=ann.get("width", 1.5)),
                    annotation_clip=True)
                self._last_ann_artists.append((ann, artist))
                continue
            if t == "text":
                artist = ax.text(ann["x"], ann["y"], ann["text"],
                        fontsize=ann.get("size", 11),
                        color=ann.get("color","#000000"),
                        fontfamily=font_family,
                        clip_on=True)
                self._last_ann_artists.append((ann, artist))
            elif t == "symbol":
                artist, = ax.plot(ann["x"], ann["y"],
                        marker=ann.get("symbol","*"),
                        markersize=ann.get("size", 12),
                        color=ann.get("color","#ff3b30"),
                        linestyle="none", clip_on=True,
                        picker=8)   # 8 px pick radius
                self._last_ann_artists.append((ann, artist))
            elif t == "zone":
                x0, x1 = ann["x"], ann["y"]
                ax.axvspan(x0, x1,
                           color=ann.get("color","#ffcc00"),
                           alpha=ann.get("alpha", 0.2))

    def _remove_last_annotation(self):
        if self._annotations:
            self._annotations.pop()
            self._draw()

    def _clear_annotations(self):
        self._annotations = []
        self._draw()

    # ─────────────────────────────────────────────────────────
    #  Color pickers
    # ─────────────────────────────────────────────────────────
    def _pick_label_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._label_color), self)
        if c.isValid():
            self._label_color = c.name()
            self.label_color_btn.setStyleSheet(f"background:{self._label_color};")
            self._draw()

    def _pick_area_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._area_fill_color), self)
        if c.isValid():
            self._area_fill_color = c.name()
            self.area_fill_color_btn.setStyleSheet(f"background-color:{self._area_fill_color};")

    def _pick_sym_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._sym_color), self)
        if c.isValid():
            self._sym_color = c.name()
            self.sym_color_btn.setStyleSheet(f"background-color:{self._sym_color};")

    def _pick_zone_color(self):
        c = QtWidgets.QColorDialog.getColor(QtGui.QColor(self._zone_color), self)
        if c.isValid():
            self._zone_color = c.name()
            self.zone_color_btn.setStyleSheet(f"background-color:{self._zone_color};")

    # ─────────────────────────────────────────────────────────
    #  Export
    # ─────────────────────────────────────────────────────────
    def _export_figure(self):
        path, filt = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Figure", _get_dialog_dir("export_plot"),
            "PNG (*.png);;SVG (*.svg);;PDF (*.pdf)")
        if not path: return
        _set_dialog_dir("export_plot", path)
        ext = os.path.splitext(path)[1].lower()
        if not ext: path += ".png"
        # temporarily resize figure to export dimensions, then restore
        orig_size = self.fig.get_size_inches()
        dpi = self.export_dpi_spin.value()
        w_in = self.fig_w_spin.value() / dpi
        h_in = self.fig_h_spin.value() / dpi
        self.fig.set_size_inches(w_in, h_in)
        # Ensure the figure patch is fully opaque with the chosen background color
        # before saving (fig.patch.set_alpha(0) is set for the on-screen canvas).
        self.fig.patch.set_alpha(1.0)
        self.fig.patch.set_facecolor(self._bg_color)
        self.fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.02,
                         facecolor=self.fig.get_facecolor())
        # Restore transparent patch for the on-screen canvas
        self.fig.patch.set_alpha(0)
        self.fig.set_size_inches(orig_size)
        self.canvas.draw_idle()
        QtWidgets.QMessageBox.information(self, "Exported", f"Saved to:\n{path}")

    def closeEvent(self, event):
        self._save_ui_settings()
        super().closeEvent(event)






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
        "<b>Plotting Tool</b>  (Plot → Open Plotting Tool  /  Ctrl+Shift+T)<br>"
        "Full matplotlib figure editor with Appearance, Peaks, Annotations and Legend tabs. "
        "Save and reopen complete projects (spectra + annotations) as <i>.dplot</i> files.<br><br>"
        "<b>Residuals files</b><br>"
        "Every recalibration (auto, manual, batch) writes a <i>Residuals dd.mm.yyyy - hh.mm.ss/</i> "
        "subfolder in the output folder containing:<br>"
        "• <i>*_residuals.csv</i> - table of original m/z, corrected m/z, and Δ m/z.<br>"
        "• <i>*_residuals.txt</i> - two-column file (m/z  Δ m/z) openable as a spectrum in Droplet.<br><br>"
        "<b>Plot menu</b><br>"
        "A full matplotlib-based figure editor. Features:<br>"
        "• Appearance tab - axis labels, fonts, tick sizes, grid, spines, log Y, axis range locks, "
        "background color, per-spectrum color/width/style, watermark, export size & DPI, "
        "spectrum offset stacking, normalisation.<br>"
        "• Peaks tab - auto peak labels (threshold, integer display, highest-spectrum-only), "
        "label font/size/angle/color, move all labels up/down, manual peak mode (double-click), "
        "area-under-peak fill & measurement, peak list color legend.<br>"
        "• Annotations tab - free text, symbols over peaks (★●▲■✦), highlight zones, arrows. "
        "Click the place button then click on the canvas; click again to place another.<br>"
        "• Legend tab - show/hide, position, font size, frame, per-spectrum label overrides.<br>"
        "Projects can be saved as <i>.dplot</i> files (File menu inside the tool) "
        "and reloaded with all spectra, annotations, and settings restored.<br>"
        "Residuals from recalibration are saved as both a CSV table and a plottable "
        "two-column <i>_residuals.txt</i> file (mass vs Δ m/z) inside a timestamped "
        "<i>Residuals dd.mm.yyyy - hh.mm.ss/</i> subfolder in the output folder.<br><br>"
        "<b>Plot menu</b><br>"
        "• <i>Export as PNG</i> - high-resolution raster image (5760 px wide).<br>"
        "• <i>Export as SVG</i> - scalable vector image.<br>"
        "• <i>Export as PDF</i> - landscape PDF preserving the plot aspect ratio.<br>"
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
        "Ctrl+Shift+T - open Plotting Tool<br>"
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




# ── wire plotting tool ──
_plot_tool_win_ref = None

def _open_plot_tool():
    global _plot_tool_win_ref
    if _plot_tool_win_ref is not None:
        try:
            if _plot_tool_win_ref.isVisible():
                _plot_tool_win_ref.raise_()
                _plot_tool_win_ref.activateWindow()
                return
        except Exception:
            pass
        _plot_tool_win_ref = None          # clear stale ref
    _plot_tool_win_ref = PlottingToolWindow(parent=main_win)
    _plot_tool_win_ref.setAttribute(
        QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, False)
    _plot_tool_win_ref.show()
    _plot_tool_win_ref.raise_()
    _plot_tool_win_ref.activateWindow()


open_plot_tool_action.triggered.connect(_open_plot_tool)


# ─────────────────────────────────────────────
#  Initial plot + apply bright mode + restore session
# ─────────────────────────────────────────────
set_display_mode("bright")
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
main_win.show()
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
app.exec()