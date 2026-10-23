"""ResidualsViewerWindow - browse a residuals folder and visualise Δm/z data."""

import os
import re
import glob

import numpy as np
import pandas as pd
import pyqtgraph as pg

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui.mixins import StayOnTopMixin
from droplet_pkg.ui import theme
from droplet_pkg.ui.icons import themed as _themed_icons
from droplet_pkg.ui.file_browser import (_FileDelegate, describe, HEADER_ROLE,
                                         BADGE_ROLE, BADGE_COLOR_ROLE)

_PL_COLORS = ['#4e9de0', '#e8944b', '#5cba6e', '#c46ee8',
              '#e8c84b', '#4be8d8', '#e84b7e', '#a0a0a0']

DIM_OPACITY  = 0.12
FULL_OPACITY = 1.0

_SETTINGS = ("LILBID", "PeakViewer")
_SPEC_SUFFIX = "_residuals_spectrum.txt"
_TABLE_SUFFIX = "_residuals_table.csv"
_FOLDER_RE = re.compile(r"^Residuals (\d\d\.\d\d\.\d{4}) - (\d\d)\.(\d\d)\.(\d\d)$")


def _folder_heading(subdir):
    """'Residuals 15.01.2026 - 10.20.00' -> '15.01.2026  10:20' (else the path)."""
    if subdir == ".":
        return "This folder"
    m = _FOLDER_RE.match(os.path.basename(subdir))
    if m:
        parent = os.path.dirname(subdir)
        text = f"{m.group(1)}  {m.group(2)}:{m.group(3)}"
        return f"{parent} · {text}" if parent else text
    return subdir


def _stats(d):
    """(n, spread, max|Δ|, mean Δ); spread = highest minus lowest Δm/z."""
    d = np.asarray(d, dtype=float)
    d = d[np.isfinite(d)]
    if not d.size:
        return None
    return (d.size, float(np.max(d) - np.min(d)), float(np.max(np.abs(d))),
            float(np.mean(d)))


def _fit_deviations(orig, delta, dense=None):
    """How far each anchor is from the calibration fit.

    Called "fit deviation" in the window, not "residual", so it is not mixed
    up with the calibration residuals (the Δm/z) the files hold.

    Returns (fit_delta, deviation, curve, exact) or None:
      fit_delta  Δm/z of the fitted calibration at each anchor
      deviation  anchor Δ - fit Δ (the error the calibration leaves there)
      curve      (m/z, Δ) of the fit, to draw
      exact      True when the fit has as many parameters as anchors, so it
                 passes through all of them (deviations 0, not informative)

    The file's dense Δm/z curve is the fit itself when there is one. Otherwise
    the recalibration's model is fitted again to the anchors: corrected m/z
    quadratic in flight time (np.polyfit deg ≤ 2, as in manual and automatic
    recalibration), with flight time ∝ √(original m/z)."""
    orig = np.asarray(orig, dtype=float)
    delta = np.asarray(delta, dtype=float)
    ok = np.isfinite(orig) & np.isfinite(delta) & (orig > 0)
    if ok.sum() < 2:
        return None
    if dense is not None:
        dmz, dd = (np.asarray(a, dtype=float) for a in dense)
        order = np.argsort(dmz)
        dmz, dd = dmz[order], dd[order]
        fit_delta = np.interp(orig, dmz, dd)
        return fit_delta, delta - fit_delta, (dmz, dd), False
    deg = min(2, int(ok.sum()) - 1)
    s = np.sqrt(orig[ok])
    params = np.polyfit(s, orig[ok] + delta[ok], deg)
    fit_delta = np.full_like(orig, np.nan)
    fit_delta[ok] = np.polyval(params, s) - orig[ok]
    xs = np.linspace(orig[ok].min(), orig[ok].max(), 200)
    curve = (xs, np.polyval(params, np.sqrt(xs)) - xs)
    return fit_delta, delta - fit_delta, curve, int(ok.sum()) <= deg + 1


def _table_stats(table_path, spec_path=None):
    """(n, rms, max|Δ|, mean Δ) of a residuals table, or None. Without a table
    (older residuals, or a dense curve only) the spectrum file's Δm/z is used."""
    try:
        if table_path and os.path.isfile(table_path):
            tbl = pd.read_csv(table_path)
            return _stats(tbl[tbl.columns[2]].to_numpy(dtype=float))
        if spec_path and os.path.isfile(spec_path):
            spec = pd.read_csv(spec_path, sep='\t', comment='#', header=None, usecols=[0, 1])
            return _stats(spec[1].to_numpy(dtype=float))
    except Exception:
        pass
    return None


class _FlowLayout(QtWidgets.QLayout):
    """Lays its widgets out left to right, starting a new row when the next
    one does not fit (each widget keeps its own width)."""

    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return QtCore.Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QtCore.QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QtCore.QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QtCore.QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do_layout(self, rect, apply):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, row_h = r.x(), r.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            w = min(hint.width(), r.width())            # a very long name is cut, not the row
            if x > r.x() and x + w > r.right() + 1:     # does not fit: next row
                x = r.x()
                y += row_h + self._spacing
                row_h = 0
            if apply:
                it.setGeometry(QtCore.QRect(QtCore.QPoint(x, y), QtCore.QSize(w, hint.height())))
            x += w + self._spacing
            row_h = max(row_h, hint.height())
        return y + row_h - rect.y() + m.bottom()


class _FlowArea(QtWidgets.QScrollArea):
    """Holds a flow layout: as tall as its rows, up to `max_rows`, then it
    scrolls (so dozens of peak lists do not squeeze the plots)."""

    def __init__(self, max_rows=4, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.inner = QtWidgets.QWidget()
        self.inner.setAutoFillBackground(False)
        self.viewport().setAutoFillBackground(False)
        self.flow = _FlowLayout(self.inner)
        self.setWidget(self.inner)
        self._max_rows = max_rows

    def refit(self):
        if not self.flow.count():
            self.setFixedHeight(0)
            return
        row_h = self.flow.itemAt(0).sizeHint().height()
        cap = self._max_rows * row_h + (self._max_rows - 1) * 4
        width = self.viewport().width() - (self.verticalScrollBar().width()
                                           if self.verticalScrollBar().isVisible() else 0)
        need = self.flow.heightForWidth(max(width, 50))
        self.setFixedHeight(min(need, cap))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QtCore.QTimer.singleShot(0, self.refit)


class _LegendEntry(QtWidgets.QFrame):
    """Hoverable swatch + label for one peak list; hovering highlights it."""

    def __init__(self, pl_name, color, detail, on_enter, on_leave, parent=None):
        super().__init__(parent)
        self.setObjectName("ResLegendEntry")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)
        swatch = QtWidgets.QLabel()
        pm = QtGui.QPixmap(12, 12)
        pm.fill(QtGui.QColor(color))
        swatch.setPixmap(pm)
        lbl = QtWidgets.QLabel(pl_name or "(no peak list)")
        lay.addWidget(swatch)
        lay.addWidget(lbl)
        if detail:
            d = QtWidgets.QLabel(detail)
            d.setProperty("role", "muted")
            lay.addWidget(d)
        self._on_enter = on_enter
        self._on_leave = on_leave
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{pl_name or '(no peak list)'}\n"
                        "Hover to highlight this peak list in both plots")
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Fixed)

    def enterEvent(self, event):
        self._on_enter()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._on_leave()
        super().leaveEvent(event)


def _chip(text, tip=""):
    lbl = QtWidgets.QLabel(text)
    lbl.setProperty("role", "chip")
    lbl.setToolTip(tip)
    return lbl


class ResidualsViewerWindow(QtWidgets.QDialog, StayOnTopMixin):
    """
    Browse a Residuals folder and display:
      • Left plot  – dense residuals curve (original m/z vs Δm/z)
      • Right plot – calibration anchors (per-peak displacement, as lollipops)
    The two plots share their m/z axis (Link m/z), so zooming one zooms both.

    Preview mode (``preview_df`` given) is used during manual recalibration:
    the unsaved residuals are shown live, the file list only holds residuals
    saved earlier in the current batch (``batch_files``; ``None`` means an
    individual recalibration), and Confirm & Save / Cancel map to
    accept() / reject().
    """

    # Emitted with the row-index (into the preview summary_df) of a clicked anchor
    anchor_clicked = QtCore.pyqtSignal(int)

    def __init__(self, res_folder=None, parent=None, preview_df=None,
                 preview_stem="", preview_colors=None, batch_files=None):
        super().__init__(parent)
        self._preview_mode = preview_df is not None
        self._settings = QtCore.QSettings(*_SETTINGS)
        self.setWindowTitle(
            (f"Residuals Preview — {preview_stem}" if preview_stem else "Residuals Preview")
            if self._preview_mode else "Residuals Viewer")
        self.resize(1180, 680)
        self.setMinimumSize(760, 460)

        # Normal top-level window (not a Dialog type) so the window manager
        # treats it independently: no forced stacking above Droplet, and it
        # can be maximised / snapped to screen edges.
        self.setWindowFlags(
            QtCore.Qt.WindowType.Window
            | QtCore.Qt.WindowType.WindowTitleHint
            | QtCore.Qt.WindowType.WindowSystemMenuHint
            | QtCore.Qt.WindowType.WindowMinMaxButtonsHint
            | QtCore.Qt.WindowType.WindowCloseButtonHint
        )

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.setContentsMargins(10, 6, 10, 10)
        main_lay.setSpacing(8)
        _mbar = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar)
        main_lay.setMenuBar(_mbar)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        main_lay.addWidget(split, 1)
        split.addWidget(self._build_files_panel())
        split.addWidget(self._build_plot_panel())
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([300, 880])

        # ── Buttons (preview only: the window's own close button is enough
        #    for browsing) ────────────────────────────────────────────────────
        if self._preview_mode:
            btn_row = QtWidgets.QHBoxLayout()
            hint = QtWidgets.QLabel("Click an anchor to find its peak in the recalibration list.")
            hint.setProperty("role", "muted")
            btn_row.addWidget(hint)
            btn_row.addStretch()
            cancel_btn = QtWidgets.QPushButton("Cancel")
            confirm_btn = QtWidgets.QPushButton("Confirm && Save")
            confirm_btn.setProperty("role", "primary")
            confirm_btn.setDefault(True)
            confirm_btn.clicked.connect(self.accept)
            cancel_btn.clicked.connect(self.reject)
            btn_row.addWidget(cancel_btn)
            btn_row.addWidget(confirm_btn)
            main_lay.addLayout(btn_row)

        self._res_folder   = None
        self._file_entries = []   # list of (display_name, spec_path, table_path)
        self._items_by_pl  = {}   # pl_name → list of pyqtgraph items (both plots)
        self._last_render  = None # arguments of the last _render, to redraw on theme change

        # Preview-mode state
        self._preview_df     = preview_df
        self._preview_stem   = preview_stem
        self._preview_colors = preview_colors
        self._showing_live   = False

        self._apply_plot_theme()
        self._theme_listener = lambda _t: self._on_theme_changed()
        theme.on_theme_changed(self._theme_listener)
        self.destroyed.connect(lambda *_, f=self._theme_listener: theme.off_theme_changed(f))

        if self._preview_mode:
            # Own place in the window stack, not forced above other app windows
            self.setWindowFlags(
                QtCore.Qt.WindowType.Window
                | QtCore.Qt.WindowType.WindowMaximizeButtonHint
                | QtCore.Qt.WindowType.WindowMinimizeButtonHint
                | QtCore.Qt.WindowType.WindowCloseButtonHint
            )
            self.setModal(False)
            self._load_preview_list(batch_files)
        else:
            folder = res_folder or self._settings.value("residuals/last_folder", "", type=str)
            if folder and os.path.isdir(folder):
                self._load_folder(folder)
            else:
                self._show_empty("Choose a folder of recalibration residuals.\n"
                                 "Droplet saves them in “Residuals <date>” folders next to "
                                 "the recalibrated files.")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_files_panel(self):
        panel = QtWidgets.QFrame()
        panel.setObjectName("Card")
        panel.setMinimumWidth(240)
        lay = QtWidgets.QVBoxLayout(panel)
        lay.setContentsMargins(12, 8, 10, 10)
        lay.setSpacing(6)

        head = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("THIS BATCH" if self._preview_mode else "RESIDUALS")
        title.setObjectName("CardTitle")
        head.addWidget(title)
        head.addStretch()
        self._browse_btn = QtWidgets.QToolButton()
        self._browse_btn.setAutoRaise(True)
        self._browse_btn.setIconSize(QtCore.QSize(18, 18))
        self._browse_btn.setToolTip("Open a residuals folder (its subfolders are included)")
        _themed_icons.set(self._browse_btn, "folder")
        self._browse_btn.clicked.connect(self._browse)
        head.addWidget(self._browse_btn)
        lay.addLayout(head)

        self._folder_name = QtWidgets.QLabel("No folder open")
        self._folder_name.setObjectName("FolderName")
        self._folder_path = QtWidgets.QLabel("")
        self._folder_path.setProperty("role", "muted")
        self._folder_path.setStyleSheet("font-size: 8pt;")
        lay.addWidget(self._folder_name)
        lay.addWidget(self._folder_path)

        self._filter = QtWidgets.QLineEdit()
        self._filter.setPlaceholderText("Filter files…")
        self._filter.setClearButtonEnabled(True)
        self._filter.textChanged.connect(self._apply_filter)
        lay.addWidget(self._filter)

        self._file_list = QtWidgets.QListWidget()
        self._file_list.setObjectName("FileList")
        self._file_list.setItemDelegate(_FileDelegate(self._file_list))
        self._file_list.setMouseTracking(True)
        self._file_list.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._file_list.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._file_list.currentRowChanged.connect(self._on_select)
        lay.addWidget(self._file_list, 1)

        # Shown instead of the file list for an individual manual recalibration
        self._no_files_lbl = QtWidgets.QLabel(
            "No other files can be displayed, as this is an individual "
            "manual recalibration.")
        self._no_files_lbl.setWordWrap(True)
        self._no_files_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._no_files_lbl.setProperty("role", "muted")
        self._no_files_lbl.setVisible(False)
        lay.addWidget(self._no_files_lbl, 1)

        nav = QtWidgets.QHBoxLayout()
        self._prev_btn = QtWidgets.QToolButton(); self._prev_btn.setAutoRaise(True)
        self._next_btn = QtWidgets.QToolButton(); self._next_btn.setAutoRaise(True)
        _themed_icons.set(self._prev_btn, "prev"); _themed_icons.set(self._next_btn, "next")
        self._prev_btn.setToolTip("Previous file  (↑)"); self._next_btn.setToolTip("Next file  (↓)")
        self._prev_btn.clicked.connect(lambda: self._step(-1))
        self._next_btn.clicked.connect(lambda: self._step(+1))
        self._pos_lbl = QtWidgets.QLabel("")
        self._pos_lbl.setProperty("role", "muted")
        nav.addWidget(self._prev_btn)
        nav.addWidget(self._pos_lbl, 1, QtCore.Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self._next_btn)
        lay.addLayout(nav)

        if self._preview_mode:
            for w in (self._browse_btn, self._folder_name, self._folder_path, self._filter):
                w.setVisible(False)
        return panel

    def _build_plot_panel(self):
        panel = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(panel)
        lay.setContentsMargins(4, 0, 0, 0)
        lay.setSpacing(6)

        # Title + numbers of the file shown
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(6)
        self._title_lbl = QtWidgets.QLabel("")
        self._title_lbl.setObjectName("FolderName")
        # Long names and paths must not set the panel's minimum width (that
        # stopped the files / plots splitter from moving in a narrow window)
        self._title_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self._title_lbl.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        head.addWidget(self._title_lbl, 1)
        lay.addLayout(head)
        # Chips and options wrap onto more rows in a narrow window instead of
        # setting a minimum width (which blocked the files / plots splitter)
        chips_box = QtWidgets.QWidget()
        self._chips = _FlowLayout(chips_box, spacing=6)
        lay.addWidget(chips_box)
        self._subtitle_lbl = QtWidgets.QLabel("")
        self._subtitle_lbl.setProperty("role", "muted")
        self._subtitle_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                         QtWidgets.QSizePolicy.Policy.Preferred)
        lay.addWidget(self._subtitle_lbl)

        opts_box = QtWidgets.QWidget()
        opts = _FlowLayout(opts_box, spacing=14)
        self._values_cb = QtWidgets.QCheckBox("Values on anchors")
        self._values_cb.setToolTip("Write each anchor's Δm/z next to it (hover an anchor for details)")
        self._values_cb.setChecked(self._settings.value("residuals/show_values", True, type=bool))
        self._values_cb.toggled.connect(self._on_option)
        self._link_cb = QtWidgets.QCheckBox("Link m/z axes")
        self._link_cb.setToolTip("Zoom and pan both plots together along m/z")
        self._link_cb.setChecked(self._settings.value("residuals/link_x", True, type=bool))
        self._link_cb.toggled.connect(self._on_option)
        self._anchor_plot_cb = QtWidgets.QCheckBox("Anchor plot")
        self._anchor_plot_cb.setToolTip("Show the calibration anchors next to the Δm/z curve")
        self._anchor_plot_cb.setChecked(self._settings.value("residuals/anchor_plot", True, type=bool))
        self._anchor_plot_cb.toggled.connect(self._on_option)
        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItem("Δ m/z", "delta")
        self._mode_combo.addItem("Deviation from the fit", "deviation")
        self._mode_combo.setToolTip(
            "What the anchor plot shows:\n"
            "Δ m/z: the correction each anchor needed, with the fitted calibration (dashed)\n"
            "Deviation from the fit: anchor Δ minus the fitted Δ, i.e. the error the\n"
            "calibration still leaves at each anchor (a wrong peak stands out)")
        i = self._mode_combo.findData(self._settings.value("residuals/anchor_mode", "delta", type=str))
        self._mode_combo.setCurrentIndex(max(i, 0))
        self._mode_combo.currentIndexChanged.connect(self._on_option)
        opts.addWidget(self._anchor_plot_cb)
        opts.addWidget(self._mode_combo)
        opts.addWidget(self._values_cb)
        opts.addWidget(self._link_cb)
        lay.addWidget(opts_box)
        self._hover_lbl = QtWidgets.QLabel(" ")
        self._hover_lbl.setObjectName("StatusCursor")
        self._hover_lbl.setSizePolicy(QtWidgets.QSizePolicy.Policy.Ignored,
                                      QtWidgets.QSizePolicy.Policy.Preferred)
        self._hover_lbl.setToolTip("The anchor under the pointer")
        lay.addWidget(self._hover_lbl)

        # ── Plot area: two plot widgets side by side in a splitter (each its
        #    own widget, so hiding the anchor plot is an ordinary Qt layout
        #    change; inside one pyqtgraph layout it only re-laid out on resize)
        self._curve_w = pg.PlotWidget()
        self._bar_w = pg.PlotWidget()
        self._curve_plot = self._curve_w.getPlotItem()
        self._bar_plot = self._bar_w.getPlotItem()
        self._pg_widget = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._pg_widget.setChildrenCollapsible(False)
        for w in (self._curve_w, self._bar_w):
            w.setMinimumWidth(140)
            self._pg_widget.addWidget(w)
        for plot, title in ((self._curve_plot, "Δm/z along the spectrum"),
                            (self._bar_plot, "Calibration anchors")):
            plot.setTitle(title)
            plot.setLabel('bottom', 'Original m/z')
            plot.setLabel('left', 'Δ m/z')
            plot.showGrid(x=True, y=True, alpha=0.25)
            plot.getAxis('left').enableAutoSIPrefix(False)
        self._set_link(self._link_cb.isChecked())
        lay.addWidget(self._pg_widget, 1)

        self._empty_lbl = QtWidgets.QLabel("")
        self._empty_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._empty_lbl.setWordWrap(True)
        self._empty_lbl.setProperty("role", "muted")
        self._empty_lbl.setVisible(False)
        lay.addWidget(self._empty_lbl, 1)

        self._bar_anchor_pts: list = []   # (x, y, pl_name, row_idx) for proximity detection
        self._hover_dot = None
        self._bar_mouse_proxy = pg.SignalProxy(
            self._bar_plot.scene().sigMouseMoved,
            rateLimit=30, slot=self._on_bar_mouse_move)
        self._bar_plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

        # ── Legend: one entry per peak list ──────────────────────────────────
        # Entries wrap onto several rows, each as wide as its name
        self._legend_container = _FlowArea(max_rows=4)
        self._legend_layout = self._legend_container.flow
        lay.addWidget(self._legend_container)

        self._info_lbl = QtWidgets.QLabel("")     # errors while reading files
        self._info_lbl.setProperty("role", "muted")
        self._info_lbl.setWordWrap(True)
        lay.addWidget(self._info_lbl)
        return panel

    def _show_empty(self, text):
        self._pg_widget.setVisible(False)
        self._empty_lbl.setText(text)
        self._empty_lbl.setVisible(True)
        self._title_lbl.setText("")
        self._subtitle_lbl.setText("")
        self._set_chips(None)
        self._rebuild_legend({})

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _plot_colors(self):
        t = theme.tokens()
        dark = theme.current_mode() == "dark"
        return {"bg": "k" if dark else "w", "axis": t["muted"], "text": t["text"],
                "zero": t["muted"], "edge": "#000000" if dark else "#ffffff"}

    def _apply_plot_theme(self):
        c = self._plot_colors()
        self._curve_w.setBackground(c["bg"])
        self._bar_w.setBackground(c["bg"])
        for plot in (self._curve_plot, self._bar_plot):
            for ax in ("left", "bottom"):
                plot.getAxis(ax).setPen(c["axis"])
                plot.getAxis(ax).setTextPen(c["text"])
            plot.titleLabel.setText(plot.titleLabel.text, color=c["text"])

    def _on_theme_changed(self):
        try:
            self._apply_plot_theme()
            if self._last_render is not None:
                self._render(*self._last_render)
        except RuntimeError:
            pass            # window already deleted

    # ── Options ───────────────────────────────────────────────────────────────

    def _set_link(self, on):
        self._curve_plot.setXLink(self._bar_plot if on else None)

    def _on_option(self, *_):
        self._settings.setValue("residuals/show_values", self._values_cb.isChecked())
        self._settings.setValue("residuals/link_x", self._link_cb.isChecked())
        self._settings.setValue("residuals/anchor_plot", self._anchor_plot_cb.isChecked())
        self._settings.setValue("residuals/anchor_mode", self._mode_combo.currentData())
        self._set_link(self._link_cb.isChecked())
        if self._last_render is not None:
            self._render(*self._last_render)

    # ── Preview mode (manual recalibration) ───────────────────────────────────

    def _add_header(self, text):
        item = QtWidgets.QListWidgetItem(text)
        item.setData(HEADER_ROLE, True)
        item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        self._file_list.addItem(item)

    def _add_file_item(self, stem, path, table_path=None, label=None):
        item = QtWidgets.QListWidgetItem(label or stem)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, path)
        item.setToolTip(stem)
        stats = _table_stats(table_path, path) if (table_path or path) else None
        if stats is not None:
            item.setData(BADGE_ROLE, f"Δ range {stats[1]:.4f}")
            item.setData(QtCore.Qt.ItemDataRole.UserRole + 20, stats[1])
        self._file_list.addItem(item)
        return item

    def _mark_outliers(self):
        """Badges of files whose Δ range is more than twice the folder's
        median are drawn in the warning colour."""
        rms = [self._file_list.item(i).data(QtCore.Qt.ItemDataRole.UserRole + 20)
               for i in range(self._file_list.count())]
        vals = [r for r in rms if r is not None]
        if len(vals) < 3:
            return
        med = float(np.median(vals))
        warn = theme.tokens()["pos"]
        for i, r in enumerate(rms):
            if r is not None and med > 0 and r > 2 * med:
                it = self._file_list.item(i)
                it.setData(BADGE_COLOR_ROLE, warn)
                it.setToolTip(it.toolTip() + f"\nΔ range more than twice the folder's median ({med:.4f})")

    def _load_preview_list(self, batch_files):
        """Fill the file list with the live entry + residuals saved in this batch."""
        self._file_list.clear()
        self._file_entries.clear()

        if batch_files is None:
            self._file_list.setVisible(False)
            for w in (self._prev_btn, self._next_btn, self._pos_lbl):
                w.setVisible(False)
            self._no_files_lbl.setVisible(True)
            self._draw_live()
            return

        self._file_entries.append(None)   # None = live (unsaved) entry
        self._add_header("Being recalibrated")
        self._add_file_item(self._preview_stem or "Current", "",
                            label=f"{self._preview_stem or 'Current'}_unsaved")

        saved = [p for p in batch_files if os.path.isfile(p)]
        self._add_header("Saved in this batch")
        if saved:
            for spec_path in reversed(saved):   # most recent first
                stem = os.path.basename(spec_path).replace(_SPEC_SUFFIX, "")
                table_path = os.path.join(os.path.dirname(spec_path), stem + _TABLE_SUFFIX)
                self._file_entries.append((stem, spec_path, table_path))
                self._add_file_item(stem, spec_path, table_path)
        else:
            empty_item = QtWidgets.QListWidgetItem("No residuals saved yet in this batch.")
            empty_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)   # placeholder look
            self._file_list.addItem(empty_item)

        self._select_first()

    def update_data(self, summary_df, color_map=None):
        """Refresh the live preview without closing the window."""
        self._preview_df     = summary_df
        self._preview_colors = color_map
        first = self._first_selectable_row()
        if self._file_list.isVisible() and self._file_list.currentRow() != first:
            self._file_list.setCurrentRow(first)   # triggers _draw_live
        else:
            self._draw_live()

    def _draw_live(self):
        df = self._preview_df
        cols = list(df.columns)
        pl = (df["peak_list"].fillna("").astype(str).str.strip()
              if "peak_list" in df.columns else pd.Series([""] * len(df)))
        spec_df = pd.DataFrame({
            "mz":        df[cols[0]].to_numpy(dtype=float),
            "delta":     df[cols[2]].to_numpy(dtype=float),
            "peak_list": pl.to_numpy(),
        })
        color_map = dict(self._preview_colors) if self._preview_colors else {}
        auto_idx = 0
        for n in dict.fromkeys(pl):
            if n not in color_map:
                color_map[n] = _PL_COLORS[auto_idx % len(_PL_COLORS)]
                auto_idx += 1
        self._showing_live = True
        self._render(spec_df, df, color_map, self._preview_stem or "Current",
                     "Not saved yet: confirm to save these residuals")

    def _on_scene_clicked(self, event):
        if not (self._preview_mode and self._showing_live):
            return
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        row_idx = self._nearest_anchor_idx(event.scenePos())
        if row_idx >= 0:
            self.anchor_clicked.emit(row_idx)
            event.accept()

    # ── Folder loading ────────────────────────────────────────────────────────

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select a Residuals folder", self._res_folder or "")
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder):
        self._res_folder = folder
        self._settings.setValue("residuals/last_folder", folder)
        self._folder_name.setText(os.path.basename(os.path.normpath(folder)) or folder)
        fm = self._folder_path.fontMetrics()
        self._folder_path.setText(fm.elidedText(folder, QtCore.Qt.TextElideMode.ElideMiddle, 250))
        self._folder_path.setToolTip(folder)
        self._file_list.clear()
        self._file_entries.clear()

        # Recursive search for all *_residuals_spectrum.txt files
        spectra = sorted(
            glob.glob(os.path.join(folder, "**", "*" + _SPEC_SUFFIX), recursive=True),
            key=lambda p: (os.path.dirname(p), os.path.basename(p))
        )

        current_subdir = None
        for spec_path in spectra:
            subdir     = os.path.relpath(os.path.dirname(spec_path), folder)
            stem       = os.path.basename(spec_path).replace(_SPEC_SUFFIX, "")
            table_path = os.path.join(os.path.dirname(spec_path), stem + _TABLE_SUFFIX)
            display    = stem if subdir == "." else f"{stem}  [{subdir}]"
            if subdir != current_subdir:
                current_subdir = subdir
                self._add_header(_folder_heading(subdir))
            self._file_entries.append((display, spec_path, table_path))
            self._add_file_item(stem, spec_path, table_path)
        self._mark_outliers()
        self._apply_filter(self._filter.text())

        if self._file_entries:
            self._pg_widget.setVisible(True)
            self._empty_lbl.setVisible(False)
            self._select_first()
        else:
            self._show_empty("No residuals files in this folder or its subfolders.\n"
                             "They are named “…_residuals_spectrum.txt”.")
        self._update_pos()

    def _apply_filter(self, text):
        """Hide the files whose name does not contain `text`, and the
        headings left with no file under them."""
        text = text.strip().lower()
        header_row, header_has = None, False
        for i in range(self._file_list.count()):
            it = self._file_list.item(i)
            if it.data(HEADER_ROLE):
                if header_row is not None:
                    self._file_list.setRowHidden(header_row, not header_has)
                header_row, header_has = i, False
                continue
            hide = bool(text) and text not in it.text().lower()
            self._file_list.setRowHidden(i, hide)
            header_has = header_has or not hide
        if header_row is not None:
            self._file_list.setRowHidden(header_row, not header_has)
        self._update_pos()

    # ── Selection / navigation ────────────────────────────────────────────────

    def _selectable_rows(self):
        return [i for i in range(self._file_list.count())
                if self._file_list.item(i).flags() & QtCore.Qt.ItemFlag.ItemIsSelectable
                and not self._file_list.isRowHidden(i)]

    def _first_selectable_row(self):
        rows = self._selectable_rows()
        return rows[0] if rows else -1

    def _select_first(self):
        row = self._first_selectable_row()
        if row >= 0:
            self._file_list.setCurrentRow(row)

    def _step(self, delta):
        rows = self._selectable_rows()
        if not rows:
            return
        cur = self._file_list.currentRow()
        if cur in rows:
            k = max(0, min(len(rows) - 1, rows.index(cur) + delta))
        else:
            k = 0
        self._file_list.setCurrentRow(rows[k])

    def _update_pos(self):
        rows = self._selectable_rows()
        cur = self._file_list.currentRow()
        self._pos_lbl.setText(f"{rows.index(cur) + 1} / {len(rows)}" if cur in rows
                              else (f"– / {len(rows)}" if rows else ""))

    def _on_select(self, row):
        self._update_pos()
        if row < 0:
            return
        item = self._file_list.item(row)
        if item is None or not (item.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable):
            return
        entry_idx = sum(
            1 for i in range(row)
            if self._file_list.item(i).flags() & QtCore.Qt.ItemFlag.ItemIsSelectable
        )
        if entry_idx >= len(self._file_entries):
            return
        if self._file_entries[entry_idx] is None:
            self._draw_live()
            return
        display, spec_path, table_path = self._file_entries[entry_idx]
        stem = os.path.basename(spec_path).replace(_SPEC_SUFFIX, "")
        self._draw(spec_path, table_path, stem)

    # ── Shared color map ──────────────────────────────────────────────────────

    def _build_color_map(self, spec_path, table_path):
        """Return an ordered dict: pl_name → hex color, merging both files.

        Colors are read from '# peak_list_color:<name>=<hex>' headers written
        by _save_residuals.  Any name not found in those headers falls back to
        the _PL_COLORS palette so old files still display correctly.
        """
        saved_colors: dict = {}
        names: list = []

        if os.path.isfile(spec_path):
            try:
                with open(spec_path) as fh:
                    for line in fh:
                        line = line.rstrip("\n")
                        if not line.startswith("#"):
                            break
                        prefix = "# peak_list_color:"
                        if line.startswith(prefix):
                            rest = line[len(prefix):]
                            eq   = rest.rfind("=")
                            if eq > 0:
                                saved_colors[rest[:eq]] = rest[eq + 1:]
            except Exception:
                pass
            try:
                df = pd.read_csv(spec_path, sep='\t', comment='#',
                                 header=None, names=['mz', 'delta', 'peak_list'])
                df['peak_list'] = df['peak_list'].fillna('').astype(str).str.strip()
                for n in df['peak_list'].unique():
                    if n not in names:
                        names.append(n)
            except Exception:
                pass
        if os.path.isfile(table_path):
            try:
                tbl = pd.read_csv(table_path)
                if 'peak_list' in tbl.columns:
                    for n in tbl['peak_list'].fillna('').astype(str).str.strip().unique():
                        if n not in names:
                            names.append(n)
            except Exception:
                pass

        auto_idx = 0
        color_map: dict = {}
        for n in names:
            if n in saved_colors:
                color_map[n] = saved_colors[n]
            else:
                color_map[n] = _PL_COLORS[auto_idx % len(_PL_COLORS)]
                auto_idx += 1
        return color_map

    # ── Draw ─────────────────────────────────────────────────────────────────

    def _draw(self, spec_path, table_path, stem):
        self._showing_live = False
        color_map = self._build_color_map(spec_path, table_path)
        spec_df, tbl, errors = None, None, []
        if os.path.isfile(spec_path):
            try:
                spec_df = pd.read_csv(spec_path, sep='\t', comment='#',
                                      header=None, names=['mz', 'delta', 'peak_list'])
            except Exception as e:
                errors.append(f"Could not read spectrum file: {e}")
        if os.path.isfile(table_path):
            try:
                tbl = pd.read_csv(table_path)
            except Exception as e:
                errors.append(f"Could not read table file: {e}")
        self._render(spec_df, tbl, color_map, stem, os.path.dirname(spec_path))
        if errors:
            self._info_lbl.setText("  |  ".join(errors))

    def _show_bar_plot(self, show):
        self._bar_w.setVisible(show)

    def _set_chips(self, stats, unit="anchor", tip=""):
        while self._chips.count():
            w = self._chips.takeAt(0).widget()
            if w:
                w.deleteLater()
        if stats is None:
            return
        n, spread, max_d, mean_d = stats
        self._chips.addWidget(_chip(f"{n} {unit}{'s' if n != 1 else ''}", tip))
        self._chips.addWidget(_chip(f"Δ range {spread:.4f}",
                                    "Highest minus lowest Δm/z: how much the correction "
                                    "varies along the spectrum"))
        self._chips.addWidget(_chip(f"max |Δ| {max_d:.4f}", "Largest Δm/z, in absolute value"))
        self._chips.addWidget(_chip(f"mean Δ {mean_d:+.4f}",
                                    "Average Δm/z: far from 0 means the whole spectrum was shifted"))

    def _render(self, spec_df, tbl, color_map, stem, where=""):
        """Draw a dense curve (spec_df: mz/delta/peak_list) and anchor table."""
        self._last_render = (spec_df, tbl, color_map, stem, where)
        self._pg_widget.setVisible(True)
        self._empty_lbl.setVisible(False)
        c = self._plot_colors()
        zero_pen = pg.mkPen(c["zero"], width=1, style=QtCore.Qt.PenStyle.DashLine)
        self._curve_plot.clear()
        self._bar_plot.clear()
        self._curve_plot.addLine(y=0, pen=zero_pen)
        self._bar_plot.addLine(y=0, pen=zero_pen)
        self._hover_dot = None
        self._hover_lbl.setText(" ")
        self._bar_anchor_pts = []
        self._info_lbl.setText("")
        self._items_by_pl = {}

        title, pol, dt, date, tags = describe(stem)
        self._title_lbl.setText(title)
        self._title_lbl.setToolTip(stem)
        self._subtitle_lbl.setText("  ·  ".join(x for x in (
            pol, f"dt {dt}" if dt else "", date, tags, where) if x))

        def _track(pl_name, item):
            self._items_by_pl.setdefault(pl_name, []).append(item)

        # ── Dense curve ───────────────────────────────────────────────────────
        if spec_df is not None:
            try:
                spec_df = spec_df.sort_values('mz')
                spec_df['peak_list'] = spec_df['peak_list'].fillna('').astype(str).str.strip()
                for pl_name, grp in spec_df.groupby('peak_list', sort=False):
                    color = color_map.get(pl_name, '#a0a0a0')
                    x = grp['mz'].values.astype(float)
                    # Dots too when the curve is only a few points (a list with
                    # a single anchor would otherwise not show at all)
                    few = len(x) <= 60
                    curve = self._curve_plot.plot(
                        x, grp['delta'].values.astype(float),
                        pen=pg.mkPen(color, width=1.5),
                        symbol='o' if few else None, symbolSize=5,
                        symbolBrush=pg.mkBrush(color), symbolPen=None)
                    _track(pl_name, curve)
            except Exception as e:
                self._info_lbl.setText(f"Could not plot spectrum data: {e}")

        # ── Anchors (lollipops) ───────────────────────────────────────────────
        # In "Deviation from the fit" mode the lollipops show anchor Δ minus the
        # fitted Δ instead of the Δ itself.
        per_list = {}            # peak list → fit deviations of its anchors
        stats = None
        fit_dev = None
        exact_fit = False
        deviation_mode = self._mode_combo.currentData() == "deviation"
        if tbl is not None:
            try:
                cols = list(tbl.columns)
                if len(cols) >= 3:
                    orig   = tbl[cols[0]].values.astype(float)
                    delta  = tbl[cols[2]].values.astype(float)
                    pl_col = (tbl["peak_list"].fillna('').astype(str).str.strip()
                              if "peak_list" in tbl.columns
                              else pd.Series([''] * len(orig)))
                    # A spectrum file much longer than the table is the fitted
                    # curve itself (automatic recalibration saves it)
                    dense = None
                    if spec_df is not None and len(spec_df) > 3 * max(len(orig), 1):
                        dense = (spec_df['mz'].to_numpy(dtype=float),
                                 spec_df['delta'].to_numpy(dtype=float))
                    fit = _fit_deviations(orig, delta, dense)
                    devs = fit[1] if fit is not None else np.full_like(delta, np.nan)
                    exact_fit = bool(fit and fit[3])
                    if fit is not None and not exact_fit:
                        r = devs[np.isfinite(devs)]
                        fit_dev = float(np.sqrt(np.mean(r ** 2))) if r.size else None
                    if fit is not None and not deviation_mode:      # the fit, dashed
                        fit_line = self._bar_plot.plot(
                            *fit[2], pen=pg.mkPen(c["text"], width=1,
                                                  style=QtCore.Qt.PenStyle.DashLine))
                        fit_line.setOpacity(0.55)
                    self._bar_plot.setLabel('left', 'Deviation from the fit (m/z)'
                                            if deviation_mode else 'Δ m/z')
                    show_values = self._values_cb.isChecked()
                    small = QtGui.QFont(); small.setPointSizeF(7.5)
                    for row_idx, (x, d, pl, rs) in enumerate(zip(orig, delta, pl_col, devs)):
                        y = rs if deviation_mode else d
                        if not np.isfinite(y):
                            continue
                        color = color_map.get(pl, '#a0a0a0')
                        bar = pg.PlotDataItem([x, x], [0, y], pen=pg.mkPen(color, width=3))
                        self._bar_plot.addItem(bar)
                        _track(pl, bar)
                        sc = pg.ScatterPlotItem(x=[x], y=[y], size=10,
                                                pen=pg.mkPen(c["edge"], width=1),
                                                brush=pg.mkBrush(color))
                        self._bar_plot.addItem(sc)
                        _track(pl, sc)
                        self._bar_anchor_pts.append((x, y, pl, row_idx, d, rs))
                        if np.isfinite(rs):
                            per_list.setdefault(pl, []).append(rs)
                        else:
                            per_list.setdefault(pl, [])
                        if show_values:
                            lbl = pg.TextItem(f"{y:+.4f}", anchor=(0.5, 1.15 if y >= 0 else -0.15),
                                              color=c["text"])
                            lbl.setFont(small)
                            lbl.setPos(x, y)
                            self._bar_plot.addItem(lbl)
                            _track(pl, lbl)
                    stats = _stats(delta)
            except Exception as e:
                self._info_lbl.setText(f"Could not plot table data: {e}")
        # No anchor table (older residuals, or a dense curve only): no anchor
        # plot, and nothing is linked to it - linked to an empty plot, the
        # curve was held to its 0-1 range, far from the data.
        has_anchors = bool(self._bar_anchor_pts)
        self._anchor_plot_cb.setEnabled(has_anchors)
        self._mode_combo.setEnabled(has_anchors and self._anchor_plot_cb.isChecked())
        show_bar = has_anchors and self._anchor_plot_cb.isChecked()
        self._show_bar_plot(show_bar)
        # Δ and deviation plots have different y scales: only m/z is shared
        self._set_link(self._link_cb.isChecked() and show_bar)
        self._link_cb.setEnabled(show_bar)
        self._values_cb.setEnabled(show_bar)
        for plot in (self._curve_plot, self._bar_plot):
            plot.enableAutoRange()
        if stats is None and spec_df is not None and len(spec_df):
            stats = _stats(spec_df['delta'].to_numpy(dtype=float))
            self._set_chips(stats, unit="point",
                            tip="This file has no anchor table: the numbers are those "
                                "of the Δm/z curve")
        else:
            self._set_chips(stats)
        if fit_dev is not None:
            self._chips.addWidget(_chip(
                f"fit deviation {fit_dev:.4f}",
                "RMS of the anchors' distance to the fitted calibration: how well the "
                "calibration fits them"))
        elif exact_fit:
            self._chips.addWidget(_chip("fit deviation –",
                                        "Too few anchors: the fit passes through all of them"))

        # Per peak list: its anchors and how well the fit matches them
        details = {}
        for pl, r in per_list.items():
            n = sum(1 for p in self._bar_anchor_pts if p[2] == pl)
            txt = f"{n} anchor{'s' if n != 1 else ''}"
            if r and not exact_fit:
                txt += f" · fit deviation {np.sqrt(np.mean(np.square(r))):.4f}"
            details[pl] = txt
        self._rebuild_legend(color_map, details)

    # ── Legend ────────────────────────────────────────────────────────────────

    def _rebuild_legend(self, color_map, details=None):
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        details = details or {}
        if len(color_map) <= 1 and not any(color_map):
            self._legend_container.setVisible(False)       # nothing to tell apart
            return
        self._legend_container.setVisible(True)
        for pl_name, color in color_map.items():
            self._legend_layout.addWidget(_LegendEntry(
                pl_name, color, details.get(pl_name, ""),
                on_enter=lambda n=pl_name: self._dim_others(n),
                on_leave=self._restore_all))
        self._legend_container.inner.updateGeometry()
        QtCore.QTimer.singleShot(0, self._legend_container.refit)

    # ── Anchor hover ──────────────────────────────────────────────────────────

    def _nearest_anchor(self, scene_pos):
        """Return (row_idx, pl_name) of the nearest anchor within threshold, or (-1, '')."""
        a = self._nearest_anchor_point(scene_pos)
        return (a[3], a[2]) if a else (-1, "")

    def _nearest_anchor_point(self, scene_pos):
        vb = self._bar_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return None
        mp = vb.mapSceneToView(scene_pos)
        vr = vb.viewRange()
        x_span = abs(vr[0][1] - vr[0][0]) or 1.0
        y_span = abs(vr[1][1] - vr[1][0]) or 1.0
        threshold = 0.05
        best, best_dist = None, float("inf")
        for pt in self._bar_anchor_pts:
            dx = (mp.x() - pt[0]) / x_span
            dy = (mp.y() - pt[1]) / y_span
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < threshold and dist < best_dist:
                best, best_dist = pt, dist
        return best

    def _nearest_anchor_idx(self, scene_pos):
        return self._nearest_anchor(scene_pos)[0]

    def _on_bar_mouse_move(self, evt):
        pt = self._nearest_anchor_point(evt[0])
        if self._hover_dot is not None:
            self._bar_plot.removeItem(self._hover_dot)
            self._hover_dot = None
        if pt is None:
            self._hover_lbl.setText(" ")
            return
        x, y, pl, _row, d, rs = pt
        res_txt = f"    fit deviation {rs:+.4f}" if np.isfinite(rs) else ""
        self._hover_lbl.setText(f"{pl or '(no peak list)'}    m/z {x:.4f}    Δ {d:+.4f}{res_txt}")
        self._hover_dot = pg.ScatterPlotItem(
            x=[x], y=[y], size=16, pen=pg.mkPen(self._plot_colors()["text"], width=1.5),
            brush=pg.mkBrush(0, 0, 0, 0))
        self._bar_plot.addItem(self._hover_dot)

    # ── Hover opacity ─────────────────────────────────────────────────────────

    def _dim_others(self, active_pl):
        for pl_name, items in self._items_by_pl.items():
            opacity = FULL_OPACITY if pl_name == active_pl else DIM_OPACITY
            for item in items:
                try:
                    item.setOpacity(opacity)
                except Exception:
                    pass

    def _restore_all(self):
        for items in self._items_by_pl.values():
            for item in items:
                try:
                    item.setOpacity(FULL_OPACITY)
                except Exception:
                    pass
