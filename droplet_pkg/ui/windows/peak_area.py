"""PeakAreaWindow - interactive area measurement and per-peak-list area ratios."""

import os
import csv
import numpy as np

# np.trapz was renamed np.trapezoid in numpy 2.0; support both
_trapezoid = getattr(np, "trapezoid", None) or np.trapz

try:
    import pyqtgraph as pg
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    import pyqtgraph as pg
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.analysis.peaks import parse_peaks_text
from droplet_pkg.processing.signal import find_peak_bounds as _find_peak_bounds_orig
from droplet_pkg.ui.mixins import StayOnTopMixin


# ─── Custom ViewBox for peak-bound drag editing ──────────────────────────────

class _EditorViewBox(pg.ViewBox):
    """ViewBox that intercepts left-click drag and emits view-space x coordinates.

    Right-click context menu and scroll-wheel zoom still work normally.
    """
    sig_left_press   = QtCore.pyqtSignal(float)
    sig_left_move    = QtCore.pyqtSignal(float)
    sig_left_release = QtCore.pyqtSignal(float)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._ed_left_down = False
        self.edit_active   = False  # set True by dialog while a peak is selected

    def mousePressEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and self.edit_active:
            self._ed_left_down = True
            self.sig_left_press.emit(float(self.mapToView(ev.pos()).x()))
            ev.accept()
        else:
            super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._ed_left_down:
            self.sig_left_move.emit(float(self.mapToView(ev.pos()).x()))
            ev.accept()
        else:
            super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if ev.button() == QtCore.Qt.MouseButton.LeftButton and self._ed_left_down:
            self._ed_left_down = False
            self.sig_left_release.emit(float(self.mapToView(ev.pos()).x()))
            ev.accept()
        else:
            super().mouseReleaseEvent(ev)


# ─── Peak Boundary Checker Dialog ────────────────────────────────────────────

class PeakBoundaryCheckerDialog(QtWidgets.QDialog):
    """
    Modal dialog for inspecting and adjusting peak integration bounds before export.

    Shows the baseline-corrected spectrum with one coloured fill per peak.
    Interaction:
      1. Click a fill → peak selected (highlighted)
      2. Click-drag anywhere on the plot → redefine that peak's bounds
      3. Release → bounds set, peak deselected (click fill again to re-edit)

    Keyboard shortcuts: Ctrl+Z undo, Ctrl+Y redo, Ctrl+R reset, Enter confirm,
                        Escape cancel.

    Attributes (set before/after exec()):
      result_bounds  – input structure with updated mz_lo/mz_hi (None unless Accepted)
      skipped        – True when user clicked 'Skip file' (batch mode only)
      cancel_all     – True when user clicked Cancel / pressed Escape
    """

    def __init__(self, mz_arr, corr_int, noise_floor_raw,
                 peak_entries, title="", batch_mode=False, parent=None):
        """
        Parameters
        ----------
        mz_arr          : 1-D ndarray  m/z axis
        corr_int        : 1-D ndarray  baseline-corrected intensities (floor subtracted)
        noise_floor_raw : float         raw noise floor value (for info label only)
        peak_entries    : list of dicts
                          {'label': str, 'color': str,
                           'peaks': [(mz_nom, mz_lo, mz_hi), ...]}
        title           : str  window title suffix (file stem / "N of M")
        batch_mode      : bool show 'Skip file' button
        """
        super().__init__(parent)
        flags = (QtCore.Qt.WindowType.Window |
                 QtCore.Qt.WindowType.WindowMaximizeButtonHint |
                 QtCore.Qt.WindowType.WindowMinimizeButtonHint)
        self.setWindowFlags(flags)
        self.setWindowTitle(
            f"Peak Boundary Check — {title}" if title else "Peak Boundary Check")
        self.resize(960, 580)
        self.setMinimumSize(640, 420)

        self._mz_arr  = np.asarray(mz_arr,  dtype=float)
        self._corr    = np.asarray(corr_int, dtype=float)
        self._nf_raw  = float(noise_floor_raw)
        self._entries = peak_entries          # original entries (never mutated)
        self._batch   = batch_mode

        # Working bounds: _bounds[ei][pi] = [mz_lo, mz_hi]
        self._bounds = [
            [[float(p[1]), float(p[2])] for p in e['peaks']]
            for e in peak_entries
        ]

        self.result_bounds = None
        self.skipped       = False
        self.cancel_all    = False

        # State machine: 'idle' | 'peak_selected' | 'dragging'
        self._state        = 'idle'
        self._active_ei    = -1
        self._active_pi    = -1
        self._drag_start_x = None

        # Undo/redo
        self._history  = [self._copy_bounds()]
        self._hist_pos = 0

        # Plot item caches
        self._fill_items   = []   # list[list[PlotCurveItem]]
        self._preview_fill = None
        self._dot_item     = None
        self._show_dots    = True

        self._build_ui()
        self._draw_all()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        info_txt = (
            "Click a coloured fill to select a peak, then click-drag to redefine its bounds.  "
            f"Display: zero-floor normalised (same scale as main plot).  "
            f"Noise floor ≈ {self._nf_raw:.4g}.  Integration uses raw baseline-corrected values.")
        self._info_lbl = QtWidgets.QLabel(info_txt)
        self._info_lbl.setStyleSheet("color:#aaa; font-size:9pt;")
        self._info_lbl.setWordWrap(True)
        lay.addWidget(self._info_lbl)

        self._glw = pg.GraphicsLayoutWidget()
        self._vb  = _EditorViewBox()
        self._plt = self._glw.addPlot(viewBox=self._vb)
        self._plt.setLabel('bottom', 'm/z')
        self._plt.setLabel('left',   'Intensity (baseline-corrected)')
        self._plt.showGrid(x=True, y=True, alpha=0.18)
        self._plt.getAxis('left').enableAutoSIPrefix(False)
        lay.addWidget(self._glw, stretch=1)

        self._status_lbl = QtWidgets.QLabel("")
        self._status_lbl.setStyleSheet("font-size:9pt; color:#888;")
        lay.addWidget(self._status_lbl)

        btn_row = QtWidgets.QHBoxLayout()

        reset_btn = QtWidgets.QPushButton("Reset all")
        reset_btn.setToolTip("Restore auto-detected bounds for all peaks  (Ctrl+R)")
        reset_btn.clicked.connect(self._reset_all)
        btn_row.addWidget(reset_btn)

        self._dots_chk = QtWidgets.QCheckBox("Peak dots")
        self._dots_chk.setChecked(True)
        self._dots_chk.setToolTip("Show/hide red dots at each peak maximum")
        self._dots_chk.toggled.connect(self._on_toggle_dots)
        btn_row.addWidget(self._dots_chk)

        hint = QtWidgets.QLabel("   Ctrl+Z undo   Ctrl+Y redo")
        hint.setStyleSheet("color:#555; font-size:9pt;")
        btn_row.addWidget(hint)
        btn_row.addStretch()

        if self._batch:
            skip_btn = QtWidgets.QPushButton("Skip file")
            skip_btn.setToolTip("Skip this file and continue to the next")
            skip_btn.clicked.connect(self._on_skip)
            btn_row.addWidget(skip_btn)

        confirm_btn = QtWidgets.QPushButton("✔  Confirm & Export")
        confirm_btn.setFixedHeight(34)
        confirm_btn.setStyleSheet(
            "QPushButton { background:#1e5f28; color:white; border-radius:4px;"
            " font-weight:bold; padding:0 14px; }"
            "QPushButton:hover { background:#27802e; }")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)

        cancel_lbl = "Cancel all" if self._batch else "Cancel"
        cancel_btn = QtWidgets.QPushButton(cancel_lbl)
        cancel_btn.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel_btn)

        lay.addLayout(btn_row)

        for seq, slot in [
            ("Ctrl+Z",       self._undo),
            ("Ctrl+Y",       self._redo),
            ("Ctrl+Shift+Z", self._redo),
            ("Ctrl+R",       self._reset_all),
            ("Return",       self._on_confirm),
            ("Escape",       self._on_cancel),
        ]:
            QtGui.QShortcut(QtGui.QKeySequence(seq), self, activated=slot)

        # Idle-state peak selection: use scene click (fires only on tap, not pan-drag).
        self._plt.scene().sigMouseClicked.connect(self._on_scene_click)
        # Bound-drag signals (only active when edit_active=True on the ViewBox).
        self._vb.sig_left_press.connect(self._on_press)
        self._vb.sig_left_move.connect(self._on_move)
        self._vb.sig_left_release.connect(self._on_release)

    # ── Drawing ───────────────────────────────────────────────────────────────

    def _draw_all(self):
        """Rebuild the whole plot (called on init, undo, redo, reset)."""
        self._plt.clear()
        self._preview_fill = None

        self._plt.plot(self._mz_arr, self._corr,
                       pen=pg.mkPen('w', width=1), antialias=True)

        self._fill_items = []
        for ei, entry in enumerate(self._entries):
            row = []
            for pi in range(len(entry['peaks'])):
                item = self._make_fill(ei, pi, active=False)
                self._plt.addItem(item)
                row.append(item)
            self._fill_items.append(row)

        self._dot_item = None   # cleared by plt.clear() above
        self._update_dots()

    def _compute_dot_positions(self):
        """Return (xs, ys) of each peak's intensity maximum within its current bounds."""
        xs, ys = [], []
        for ei in range(len(self._entries)):
            for pi in range(len(self._entries[ei]['peaks'])):
                lo, hi = self._bounds[ei][pi]
                mask = (self._mz_arr >= lo) & (self._mz_arr <= hi)
                if mask.any():
                    sub_mz = self._mz_arr[mask]
                    sub_y  = self._corr[mask]
                    idx = int(sub_y.argmax())
                    xs.append(float(sub_mz[idx]))
                    ys.append(float(sub_y[idx]) * 1.05)
        return xs, ys

    def _update_dots(self):
        """Remove and redraw the red peak-maximum scatter dots."""
        if self._dot_item is not None:
            self._plt.removeItem(self._dot_item)
            self._dot_item = None
        if not self._show_dots:
            return
        xs, ys = self._compute_dot_positions()
        if not xs:
            return
        self._dot_item = pg.ScatterPlotItem(
            x=xs, y=ys,
            symbol='o', size=9,
            pen=pg.mkPen('r', width=1),
            brush=pg.mkBrush('r'))
        self._plt.addItem(self._dot_item)

    def _on_toggle_dots(self, checked):
        self._show_dots = checked
        self._update_dots()

    def _make_fill(self, ei, pi, active=False, preview_bounds=None):
        """Return a PlotCurveItem filled from y=0 up to the spectrum slice."""
        if preview_bounds is not None:
            mz_lo, mz_hi = preview_bounds
        else:
            mz_lo, mz_hi = self._bounds[ei][pi]

        mask = (self._mz_arr >= mz_lo) & (self._mz_arr <= mz_hi)
        x = self._mz_arr[mask]
        y = self._corr[mask]
        if len(x) < 2:
            x = np.array([mz_lo, mz_hi])
            y = np.zeros(2)

        color = self._entries[ei]['color']
        qc = QtGui.QColor(color)

        if preview_bounds is not None:
            qc.setAlpha(90);  pen_w, pen_a = 2, 180
        elif active:
            qc.setAlpha(160); pen_w, pen_a = 2, 240
        else:
            qc.setAlpha(55);  pen_w, pen_a = 1, 120

        pc = QtGui.QColor(color)
        pc.setAlpha(pen_a)
        return pg.PlotCurveItem(
            x=x, y=y,
            pen=pg.mkPen(pc, width=pen_w),
            fillLevel=0.0,
            brush=pg.mkBrush(qc))

    def _refresh_fill(self, ei, pi, active=False):
        old = self._fill_items[ei][pi]
        self._plt.removeItem(old)
        new = self._make_fill(ei, pi, active=active)
        self._plt.addItem(new)
        self._fill_items[ei][pi] = new

    def _set_preview(self, x0, x1, ei, pi):
        if self._preview_fill is not None:
            self._plt.removeItem(self._preview_fill)
        lo, hi = min(x0, x1), max(x0, x1)
        self._preview_fill = self._make_fill(ei, pi, preview_bounds=(lo, hi))
        self._plt.addItem(self._preview_fill)

    def _clear_preview(self):
        if self._preview_fill is not None:
            self._plt.removeItem(self._preview_fill)
            self._preview_fill = None

    # ── Mouse state machine ───────────────────────────────────────────────────

    def _peak_at(self, x):
        """Return (ei, pi) of the peak whose current bounds contain x, else (-1, -1)."""
        for ei in range(len(self._entries)):
            for pi in range(len(self._entries[ei]['peaks'])):
                lo, hi = self._bounds[ei][pi]
                if lo <= x <= hi:
                    return ei, pi
        return -1, -1

    def _on_scene_click(self, ev):
        """Handle idle-state peak selection via a plain tap (not a pan-drag)."""
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if self._state != 'idle':
            return
        if not self._vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        x = float(self._vb.mapSceneToView(ev.scenePos()).x())
        ei, pi = self._peak_at(x)
        if ei >= 0:
            self._active_ei, self._active_pi = ei, pi
            self._state = 'peak_selected'
            self._vb.edit_active = True
            self._refresh_fill(ei, pi, active=True)
            nom = self._entries[ei]['peaks'][pi][0]
            self._status_lbl.setText(
                f"Peak @ {nom:.3f} selected — click-drag to set new bounds")

    def _on_press(self, x):
        """Start the bound-drag (only reached when edit_active=True, i.e. peak selected)."""
        if self._state == 'peak_selected':
            self._drag_start_x = x
            self._state = 'dragging'

    def _on_move(self, x):
        if self._state == 'dragging' and self._drag_start_x is not None:
            self._set_preview(self._drag_start_x, x, self._active_ei, self._active_pi)

    def _on_release(self, x):
        if self._state != 'dragging':
            return
        lo = min(self._drag_start_x, x)
        hi = max(self._drag_start_x, x)
        self._clear_preview()

        if hi - lo > 1e-6:
            # Update bounds BEFORE refreshing the fill so the redraw uses the new range.
            self._bounds[self._active_ei][self._active_pi] = [lo, hi]
            self._push_history()
            self._refresh_fill(self._active_ei, self._active_pi, active=False)
            self._update_dots()
            nom = self._entries[self._active_ei]['peaks'][self._active_pi][0]
            self._status_lbl.setText(
                f"Peak @ {nom:.3f} → [{lo:.4f} – {hi:.4f}].  "
                "Click fill to re-edit.")
        else:
            self._refresh_fill(self._active_ei, self._active_pi, active=False)
            self._status_lbl.setText("Drag too short — bounds unchanged.")

        self._active_ei = self._active_pi = -1
        self._drag_start_x = None
        self._vb.edit_active = False
        self._state = 'idle'

    # ── History ───────────────────────────────────────────────────────────────

    def _copy_bounds(self):
        return [[[lo, hi] for lo, hi in eb] for eb in self._bounds]

    def _push_history(self):
        self._history = self._history[:self._hist_pos + 1]
        self._history.append(self._copy_bounds())
        self._hist_pos = len(self._history) - 1

    def _apply_history(self, pos):
        self._bounds = [[[lo, hi] for lo, hi in eb]
                        for eb in self._history[pos]]
        self._hist_pos = pos
        self._vb.edit_active = False
        self._state = 'idle'
        self._active_ei = self._active_pi = -1
        self._clear_preview()
        self._draw_all()

    def _undo(self):
        if self._hist_pos > 0:
            self._apply_history(self._hist_pos - 1)
            self._status_lbl.setText("Undo.")

    def _redo(self):
        if self._hist_pos < len(self._history) - 1:
            self._apply_history(self._hist_pos + 1)
            self._status_lbl.setText("Redo.")

    def _reset_all(self):
        self._bounds = [
            [[float(p[1]), float(p[2])] for p in e['peaks']]
            for e in self._entries
        ]
        self._push_history()
        self._vb.edit_active = False
        self._state = 'idle'
        self._active_ei = self._active_pi = -1
        self._clear_preview()
        self._draw_all()
        self._status_lbl.setText("All bounds reset to auto-detected values.")

    # ── Confirm / skip / cancel ───────────────────────────────────────────────

    def _on_confirm(self):
        self.result_bounds = [
            {
                'label': e['label'],
                'color': e['color'],
                'peaks': [
                    (e['peaks'][pi][0],
                     self._bounds[ei][pi][0],
                     self._bounds[ei][pi][1])
                    for pi in range(len(e['peaks']))
                ],
            }
            for ei, e in enumerate(self._entries)
        ]
        self.accept()

    def _on_skip(self):
        self.skipped = True
        self.reject()

    def _on_cancel(self):
        self.cancel_all = True
        self.reject()

    # ── In-place data swap (batch reuse) ─────────────────────────────────────

    def update_data(self, mz_arr, corr_int, noise_floor_raw, peak_entries, title=""):
        """Swap plot content in-place without recreating the widget.

        Preserves window geometry (size / position).  Call before exec() for each
        subsequent file in a batch loop.
        """
        self._mz_arr  = np.asarray(mz_arr,  dtype=float)
        self._corr    = np.asarray(corr_int, dtype=float)
        self._nf_raw  = float(noise_floor_raw)
        self._entries = peak_entries

        self._bounds = [
            [[float(p[1]), float(p[2])] for p in e['peaks']]
            for e in peak_entries
        ]

        self.result_bounds = None
        self.skipped       = False
        self.cancel_all    = False

        self._state        = 'idle'
        self._active_ei    = -1
        self._active_pi    = -1
        self._drag_start_x = None
        self._vb.edit_active = False

        self._history  = [self._copy_bounds()]
        self._hist_pos = 0

        self.setWindowTitle(
            f"Peak Boundary Check — {title}" if title else "Peak Boundary Check")
        self._info_lbl.setText(
            "Click a coloured fill to select a peak, then click-drag to redefine its bounds.  "
            f"Display: zero-floor normalised (same scale as main plot).  "
            f"Noise floor ≈ {self._nf_raw:.4g}.  Integration uses raw baseline-corrected values.")
        self._status_lbl.setText("")

        self._draw_all()


# ─── Module-level helpers ─────────────────────────────────────────────────────

def _get_app():
    import droplet_pkg.app as _m
    return _m


_area_win_ref = None


def find_peak_bounds(mz_arr, int_arr, target_mz, *, noise_floor, coarse_tol=None, grace=3):
    """Wraps the original find_peak_bounds, tolerating up to `grace` consecutive
    rising points mid-descent so small noise blips don't cut a peak short."""
    kwargs = dict(noise_floor=noise_floor)
    if coarse_tol is not None:
        kwargs["coarse_tol"] = coarse_tol
    bounds = _find_peak_bounds_orig(mz_arr, int_arr, target_mz, **kwargs)
    if bounds is None or grace == 0:
        return bounds

    mz_lo, mz_hi, peak_mz, peak_int = bounds

    # Walk LEFT with grace
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

    # Walk RIGHT with grace
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


# ─── Main window ──────────────────────────────────────────────────────────────

class PeakAreaWindow(QtWidgets.QWidget, StayOnTopMixin):
    """
    Non-modal window combining:
      1. Interactive range-click area measurement (toggle-based)
      2. Total spectrum area computation
      3. Per-peak-list area ratios
    """

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Peak List Area Tools")
        self.resize(500, 640)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._last_global_area = None

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        _mbar = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar)
        root.setMenuBar(_mbar)

        # ── Section 1: Interactive range measurement ──────────────────
        range_box = QtWidgets.QGroupBox("Interactive range measurement  [A]")
        range_lay = QtWidgets.QVBoxLayout(range_box)
        _a = _get_app()
        self._range_toggle = QtWidgets.QCheckBox(
            "Activate click-to-measure mode (click twice on the plot to define a range)")
        self._range_toggle.setChecked(_a.area_mode_action.isChecked())
        self._range_toggle.toggled.connect(lambda v: _get_app()._area_mode_set(v))
        _a.area_mode_action.toggled.connect(
            lambda v: self._range_toggle.blockSignals(True) or
                      self._range_toggle.setChecked(v) or
                      self._range_toggle.blockSignals(False))
        range_lay.addWidget(self._range_toggle)
        root.addWidget(range_box)

        # ── Section 2: Spectrum selector + total area ─────────────────
        total_box = QtWidgets.QGroupBox("Total spectrum area")
        total_lay = QtWidgets.QGridLayout(total_box)

        total_lay.addWidget(QtWidgets.QLabel("Spectrum:"), 0, 0)
        self._spec_combo = QtWidgets.QComboBox()
        total_lay.addWidget(self._spec_combo, 0, 1)

        self._total_btn = QtWidgets.QPushButton("Compute total area")
        self._total_btn.clicked.connect(self._compute_total)
        total_lay.addWidget(self._total_btn, 1, 0, 1, 2)

        self._total_lbl = QtWidgets.QLabel("-")
        self._total_lbl.setWordWrap(True)
        self._total_lbl.setStyleSheet("font-size: 12px; color: gray;")
        total_lay.addWidget(self._total_lbl, 2, 0, 1, 2)
        root.addWidget(total_box)

        # ── Section 3: Peak-list area ratios ─────────────────────────
        ratio_box = QtWidgets.QGroupBox("Peak-list area ratios")
        ratio_lay = QtWidgets.QVBoxLayout(ratio_box)

        # Ratio mode selector
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Ratio mode:"))
        self._ratio_mode_combo = QtWidgets.QComboBox()
        self._ratio_mode_combo.addItem("Normalized by global spectrum area", "global")
        self._ratio_mode_combo.addItem("Between peak lists (normalized to largest)", "between")
        _saved_mode = _get_app().settings.value("peak_area_ratio_mode", "global")
        _idx = self._ratio_mode_combo.findData(_saved_mode)
        if _idx >= 0:
            self._ratio_mode_combo.setCurrentIndex(_idx)
        self._ratio_mode_combo.currentIndexChanged.connect(
            lambda: _get_app().settings.setValue(
                "peak_area_ratio_mode", self._ratio_mode_combo.currentData()))
        mode_row.addWidget(self._ratio_mode_combo, stretch=1)
        ratio_lay.addLayout(mode_row)

        ratio_lay.addWidget(QtWidgets.QLabel(
            "Toggle peak lists below, then click 'Compute ratios'.\n"
            "Areas are summed over highlighted peaks for each toggled list."))

        self._pl_scroll = QtWidgets.QScrollArea()
        self._pl_scroll.setWidgetResizable(True)
        self._pl_container = QtWidgets.QWidget()
        self._pl_layout = QtWidgets.QVBoxLayout(self._pl_container)
        self._pl_layout.setContentsMargins(0, 0, 0, 0)
        self._pl_layout.setSpacing(2)
        self._pl_scroll.setWidget(self._pl_container)
        self._pl_scroll.setMaximumHeight(160)
        ratio_lay.addWidget(self._pl_scroll)

        self._ratio_btn = QtWidgets.QPushButton("Compute ratios")
        self._ratio_btn.clicked.connect(self._compute_ratios)
        ratio_lay.addWidget(self._ratio_btn)

        self._export_ratios_btn = QtWidgets.QPushButton("Export as CSV…")
        self._export_ratios_btn.setToolTip("Export the computed ratios to a CSV file.")
        self._export_ratios_btn.clicked.connect(self._export_ratios_csv)
        ratio_lay.addWidget(self._export_ratios_btn)
        self._last_ratio_results = []   # cache for export

        self._batch_export_btn = QtWidgets.QPushButton("Batch export ratios for entire folder…")
        self._batch_export_btn.clicked.connect(self._batch_export_folder_ratios)
        ratio_lay.addWidget(self._batch_export_btn)

        self._ratio_lbl = QtWidgets.QLabel("-")
        self._ratio_lbl.setWordWrap(True)
        self._ratio_lbl.setStyleSheet("font-size: 12px; color: gray;")
        self._ratio_lbl.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignTop | QtCore.Qt.AlignmentFlag.AlignLeft)

        _ratio_scroll = QtWidgets.QScrollArea()
        _ratio_scroll.setWidgetResizable(True)
        _ratio_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        _ratio_scroll.setMinimumHeight(60)
        _ratio_scroll.setMaximumHeight(200)
        _ratio_scroll.setWidget(self._ratio_lbl)
        ratio_lay.addWidget(_ratio_scroll)
        root.addWidget(ratio_box)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self._last_ratio_results = []
        self._last_ratio_mode    = self._ratio_mode_combo.currentData()

        self._refresh_spec_combo()
        self._refresh_peak_list_rows()

    # ── Spectrum / peak-list helpers ──────────────────────────────────────────

    def _refresh_spec_combo(self):
        _a = _get_app()
        self._spec_combo.clear()
        if _a.df is not None:
            self._spec_combo.addItem("Main spectrum", "main")
        for i, ov in enumerate(_a.overlay_list):
            if ov["toggle"].isChecked() and ov["df"] is not None:
                lbl = ov["toggle"].text() or f"Overlay {i+1}"
                self._spec_combo.addItem(lbl, i)

    def _get_selected_df(self):
        _a = _get_app()
        key = self._spec_combo.currentData()
        if key == "main":
            return _a.df
        if isinstance(key, int) and 0 <= key < len(_a.overlay_list):
            return _a.overlay_list[key]["df"]
        return None

    def _refresh_peak_list_rows(self):
        _a = _get_app()
        while self._pl_layout.count():
            item = self._pl_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._pl_chks = []
        for row in _a.custom_peak_rows:
            lbl   = row["label_input"].text().strip() or "Unnamed"
            color = row["color"][0].name()
            chk   = QtWidgets.QCheckBox()
            chk.setChecked(row["checkbox"].isChecked())
            swatch = QtWidgets.QLabel()
            swatch.setFixedSize(12, 12)
            swatch.setStyleSheet(
                f"background:{color}; border:1px solid gray; border-radius:2px;")
            name_lbl = QtWidgets.QLabel(lbl)
            row_w = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(row_w)
            rl.setContentsMargins(2, 0, 2, 0); rl.setSpacing(4)
            rl.addWidget(chk); rl.addWidget(swatch); rl.addWidget(name_lbl)
            rl.addStretch()
            self._pl_layout.addWidget(row_w)
            self._pl_chks.append((chk, row))
        self._pl_layout.addStretch()

    # ── Total area ────────────────────────────────────────────────────────────

    def _compute_total(self):
        _a = _get_app()
        current_key = self._spec_combo.currentData()
        self._refresh_spec_combo()
        if current_key is not None:
            for i in range(self._spec_combo.count()):
                if self._spec_combo.itemData(i) == current_key:
                    self._spec_combo.setCurrentIndex(i)
                    break
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            self._total_lbl.setText("No spectrum selected or loaded.")
            return
        MZ_THRESHOLD = 10.9
        mask = data['mz'] >= MZ_THRESHOLD
        data_thr = data[mask]
        if len(data_thr) < 2:
            self._total_lbl.setText(f"Not enough data points above m/z {MZ_THRESHOLD}.")
            return
        mz_arr, corr_int, floor = self._correct_spectrum(data_thr)
        total = float(_trapezoid(corr_int, mz_arr))
        mz_lo, mz_hi = float(mz_arr.min()), float(mz_arr.max())
        self._total_lbl.setText(
            f"Total area (m/z ≥ {MZ_THRESHOLD}): <b>{total:.4e}</b><br>"
            f"m/z range: {mz_lo:.2f} – {mz_hi:.2f}<br>"
            f"({len(data_thr)} data points above threshold)"
            f"noise floor ≈ {floor:.4f}<br>")
        self._total_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    # ── Compute ratios (with boundary checker) ────────────────────────────────

    def _compute_ratios(self):
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            self._ratio_lbl.setText("No spectrum selected or loaded.")
            return

        entries, mz_arr, corr_int, nf_raw = self._get_peak_bounds(data)
        if not entries:
            self._ratio_lbl.setText("No peak lists selected.")
            return

        title = self._spec_combo.currentText()
        dlg = PeakBoundaryCheckerDialog(
            mz_arr, corr_int, nf_raw, entries,
            title=title, batch_mode=False, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        raw_results = self._compute_areas_from_custom_bounds(data, dlg.result_bounds)
        if not raw_results:
            self._ratio_lbl.setText("No peak lists selected.")
            return

        ratio_mode  = self._ratio_mode_combo.currentData()
        MZ_THRESHOLD = 10.9

        if ratio_mode == "global":
            mask = data['mz'] >= MZ_THRESHOLD
            data_thr = data[mask]
            if len(data_thr) >= 2:
                mz_a, corr_a, _ = self._correct_spectrum(data_thr)
                global_area = float(_trapezoid(corr_a, mz_a))
            else:
                global_area = 1.0
            denom = global_area if global_area > 0 else 1.0
            self._last_global_area = global_area
            lines = [f"<b>Areas normalized by global spectrum area "
                     f"(m/z ≥ {MZ_THRESHOLD}, area={global_area:.4f}):</b>"]
        else:
            areas = [r[4] for r in raw_results]
            denom = max(areas) if areas else 1.0
            lines = ["<b>Relative areas (normalized to largest):</b>"]

        self._last_ratio_results = []
        for label, color, peaks, peak_details, total_area in raw_results:
            ratio = total_area / denom if denom > 0 else 0.0
            self._last_ratio_results.append({
                "label": label,
                "color": color,
                "peak_area": total_area,
                "ratio": ratio,
                "global_area": self._last_global_area if ratio_mode == "global" else None
            })
            lines.append(
                f"<span style='color:{color}'>■ {label}</span>: "
                f"area = <b>{total_area:.4f}</b>  "
                f"(ratio = <b>{ratio:.4f}</b>)")

        if ratio_mode == "between" and len(raw_results) == 2:
            r0, r1 = raw_results[0][4], raw_results[1][4]
            if r1 != 0:
                lines.append(
                    f"<br>{raw_results[0][0]} / {raw_results[1][0]} = "
                    f"<b>{r0/r1:.4f}</b>")

        self._last_ratio_mode = ratio_mode
        self._ratio_lbl.setText("<br>".join(lines))
        self._ratio_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    # ── Spectrum correction (unchanged) ───────────────────────────────────────

    def _correct_spectrum(self, data):
        """Returns: mz (np.array), corrected_intensity (np.array), floor (float)."""
        _a = _get_app()
        MZ_THRESHOLD = 10.9
        mz = data['mz'].values.copy()
        intensity = data['intensity'].values.copy()
        mask_thr = mz >= MZ_THRESHOLD
        if mask_thr.sum() >= 10:
            floor = _a._estimate_noise_floor(intensity[mask_thr], n_sigma=3.0)
        else:
            floor = _a._DYN_CLIP_FLOOR
        corrected = intensity - floor
        corrected[corrected < 0] = 0.0
        return mz, corrected, floor

    # ── Peak-list area computation (original, used internally) ────────────────

    def _compute_peak_list_areas(self, data):
        """Return list of (label, color, mz_list, peak_details, area) for toggled peak lists."""
        from droplet_pkg.processing.signal import get_tolerance
        import droplet_pkg.app as _app_mod
        from scipy.signal import find_peaks as _fp

        _a = _get_app()

        mz_full  = data["mz"].values.copy()
        int_full = data["intensity"].values.copy()

        norm_df  = _app_mod.normalise_cached(data, zero_floor=True)
        norm_int = norm_df["intensity"].values.copy()

        _thr = mz_full >= 10.9
        noise_floor = (
            _a._estimate_noise_floor(norm_int[_thr], n_sigma=3.0)
            if _thr.sum() >= 10 else _a._DYN_CLIP_FLOOR
        )

        corr_full = int_full - _a._estimate_noise_floor(int_full[_thr], n_sigma=3.0) \
            if _thr.sum() >= 10 else int_full - _a._DYN_CLIP_FLOOR
        corr_full[corr_full < 0] = 0.0

        results = []
        for chk, row in self._pl_chks:
            if not chk.isChecked():
                continue
            peaks = parse_peaks_text(row["peaks_input"].text())
            if not peaks:
                continue
            label = row["label_input"].text().strip() or "Unnamed"
            color = row["color"][0].name()
            total_area = 0.0
            peak_details = []

            for mz_nom in peaks:
                shifted_p = mz_nom + _app_mod.peak_shift(mz_nom)
                coarse    = get_tolerance(shifted_p)

                bounds = find_peak_bounds(mz_full, norm_int, shifted_p,
                                          noise_floor=noise_floor, coarse_tol=coarse, grace=3)

                if bounds is not None:
                    mz_lo_bound, mz_hi_bound, real_mz, _norm_peak_int = bounds
                    mask    = (mz_full >= mz_lo_bound) & (mz_full <= mz_hi_bound)
                    mz_sub  = mz_full[mask]
                    int_sub = corr_full[mask]
                else:
                    backward = 0.2
                    idx_bool = ((mz_full >= shifted_p - backward) &
                                (mz_full <= shifted_p + coarse))
                    if not idx_bool.any():
                        peak_details.append((mz_nom, 0.0, 0.0, mz_nom, mz_nom, mz_nom))
                        continue
                    cand_mz   = mz_full[idx_bool]
                    cand_norm = norm_int[idx_bool]
                    cand_corr = corr_full[idx_bool]
                    local_peaks, _ = _fp(cand_norm,
                                         height=noise_floor, prominence=noise_floor)
                    best = (local_peaks[cand_norm[local_peaks].argmax()]
                            if len(local_peaks) > 0 else int(cand_norm.argmax()))
                    n     = 10
                    start = max(0, best - n)
                    end   = min(len(cand_mz), best + n + 1)
                    mz_sub      = cand_mz[start:end]
                    int_sub     = cand_corr[start:end]
                    real_mz     = float(cand_mz[best])
                    mz_lo_bound = float(mz_sub.min())
                    mz_hi_bound = float(mz_sub.max())

                if len(mz_sub) >= 2:
                    peak_max  = float(int_sub.max())
                    peak_area = float(_trapezoid(int_sub, mz_sub))
                    total_area += peak_area
                    peak_details.append((mz_nom, peak_max, peak_area,
                                         real_mz, mz_lo_bound, mz_hi_bound))
                else:
                    peak_details.append((mz_nom, 0.0, 0.0,
                                         real_mz if bounds else mz_nom,
                                         mz_lo_bound if bounds else mz_nom,
                                         mz_hi_bound if bounds else mz_nom))

            results.append((label, color, peaks, peak_details, total_area))
        return results

    # ── Boundary checker support ──────────────────────────────────────────────

    def _get_peak_bounds(self, data):
        """Auto-detect peak bounds for all toggled peak lists.

        Returns (entries, mz_full, corr_int, noise_floor_raw).
        entries is the format expected by PeakBoundaryCheckerDialog.
        """
        from droplet_pkg.processing.signal import get_tolerance
        import droplet_pkg.app as _app_mod

        _a = _get_app()

        mz_full  = data["mz"].values.copy()
        int_full = data["intensity"].values.copy()

        norm_df  = _app_mod.normalise_cached(data, zero_floor=True)
        norm_int = norm_df["intensity"].values.copy()

        _thr = mz_full >= 10.9
        nf_norm = (
            _a._estimate_noise_floor(norm_int[_thr], n_sigma=3.0)
            if _thr.sum() >= 10 else _a._DYN_CLIP_FLOOR
        )
        entries = []
        for chk, row in self._pl_chks:
            if not chk.isChecked():
                continue
            peaks_list = parse_peaks_text(row["peaks_input"].text())
            if not peaks_list:
                continue
            label = row["label_input"].text().strip() or "Unnamed"
            color = row["color"][0].name()

            peak_bounds = []
            for mz_nom in peaks_list:
                shifted_p = mz_nom + _app_mod.peak_shift(mz_nom)
                coarse    = get_tolerance(shifted_p)

                bounds = find_peak_bounds(
                    mz_full, norm_int, shifted_p,
                    noise_floor=nf_norm, coarse_tol=coarse, grace=3)

                if bounds is not None:
                    mz_lo_b, mz_hi_b = bounds[0], bounds[1]
                else:
                    backward = 0.2
                    idx_bool = ((mz_full >= shifted_p - backward) &
                                (mz_full <= shifted_p + coarse))
                    if idx_bool.any():
                        cand = mz_full[idx_bool]
                        mz_lo_b, mz_hi_b = float(cand.min()), float(cand.max())
                    else:
                        mz_lo_b = shifted_p - coarse
                        mz_hi_b = shifted_p + coarse

                peak_bounds.append((mz_nom, float(mz_lo_b), float(mz_hi_b)))

            if peak_bounds:
                entries.append({'label': label, 'color': color, 'peaks': peak_bounds})

        # Pass norm_int (zero-floor normalised, matches the main plot view) to the
        # checker for display only.  Area integration still uses raw corrected values
        # inside _compute_areas_from_custom_bounds.
        return entries, mz_full, norm_int, float(nf_norm)

    def _compute_areas_from_custom_bounds(self, data, custom_entries):
        """Integrate peak areas using externally supplied bounds.

        custom_entries: list of {'label', 'color', 'peaks': [(mz_nom, mz_lo, mz_hi), ...]}
        Returns the same tuple format as _compute_peak_list_areas.
        """
        _a = _get_app()

        mz_full  = data["mz"].values.copy()
        int_full = data["intensity"].values.copy()

        _thr = mz_full >= 10.9
        nf_raw = (
            _a._estimate_noise_floor(int_full[_thr], n_sigma=3.0)
            if _thr.sum() >= 10 else _a._DYN_CLIP_FLOOR
        )
        corr_full = int_full - nf_raw
        corr_full[corr_full < 0] = 0.0

        results = []
        for entry in custom_entries:
            label = entry['label']
            color = entry['color']
            peaks = [p[0] for p in entry['peaks']]
            total_area  = 0.0
            peak_details = []

            for mz_nom, mz_lo_b, mz_hi_b in entry['peaks']:
                mask    = (mz_full >= mz_lo_b) & (mz_full <= mz_hi_b)
                mz_sub  = mz_full[mask]
                int_sub = corr_full[mask]

                if len(mz_sub) >= 2:
                    peak_max  = float(int_sub.max())
                    peak_area = float(_trapezoid(int_sub, mz_sub))
                    real_mz   = float(mz_sub[int_sub.argmax()])
                    total_area += peak_area
                    peak_details.append(
                        (mz_nom, peak_max, peak_area, real_mz, mz_lo_b, mz_hi_b))
                else:
                    peak_details.append((mz_nom, 0.0, 0.0, mz_nom, mz_lo_b, mz_hi_b))

            results.append((label, color, peaks, peak_details, total_area))
        return results

    # ── Export as CSV (with boundary checker) ─────────────────────────────────

    def _export_ratios_csv(self):
        """Export ratio results for the current spectrum to a CSV file."""
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            QtWidgets.QMessageBox.warning(self, "No spectrum",
                "No spectrum selected or loaded.")
            return

        entries, mz_arr, corr_int, nf_raw = self._get_peak_bounds(data)
        if not entries:
            QtWidgets.QMessageBox.warning(self, "No data",
                "No peak lists selected.")
            return

        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Ratios", "", "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"

        title = self._spec_combo.currentText()
        dlg = PeakBoundaryCheckerDialog(
            mz_arr, corr_int, nf_raw, entries,
            title=title, batch_mode=False, parent=self)
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return

        ratio_mode   = self._last_ratio_mode
        results      = self._compute_areas_from_custom_bounds(data, dlg.result_bounds)
        data_key     = self._spec_combo.currentData()
        spec_headers = self._get_spectrum_headers(data_key)
        self._write_ratios_csv(path, results, ratio_mode, spec_headers, data,
                               global_area=self._last_global_area)
        QtWidgets.QMessageBox.information(self, "Saved", f"Ratios saved to:\n{path}")

    # ── Spectrum headers helper (unchanged) ───────────────────────────────────

    def _get_spectrum_headers(self, data_key):
        """Read the '#'-prefixed header lines from the currently selected spectrum file."""
        _a = _get_app()
        headers = []
        path = None
        if data_key == "main":
            try:
                path = _a.combo.currentData()
            except Exception:
                pass
        elif isinstance(data_key, int) and 0 <= data_key < len(_a.overlay_list):
            try:
                path = _a.overlay_list[data_key].get("path", None)
            except Exception:
                pass
        if path and os.path.isfile(path):
            with open(path, 'r', errors='replace') as fh:
                for line in fh:
                    if line.startswith('#'):
                        headers.append(line.rstrip('\n').rstrip('\r'))
                    else:
                        break
        return headers

    # ── CSV writer (unchanged) ────────────────────────────────────────────────

    def _write_ratios_csv(self, path, results, ratio_mode, spec_headers, data, global_area=None):
        """Write a ratio CSV file with spectrum headers, ratio kind, and per-peak data."""
        MZ_THRESHOLD = 10.9

        mask_thr = data['mz'] >= MZ_THRESHOLD
        data_thr_full = data[mask_thr]
        if len(data_thr_full) >= 2:
            _, _, noise_floor = self._correct_spectrum(data_thr_full)
        else:
            noise_floor = _get_app()._DYN_CLIP_FLOOR

        if ratio_mode == "global":
            if global_area is None:
                if len(data_thr_full) >= 2:
                    mz_arr, corr_int, _ = self._correct_spectrum(data_thr_full)
                    global_area = float(_trapezoid(corr_int, mz_arr))
                else:
                    global_area = 1.0
            ratio_kind = "normalized_by_global_spectrum_area"
        else:
            areas = [r[4] for r in results]
            global_area = max(areas) if areas else 1.0
            ratio_kind = "between_peak_lists"

        self._last_global_area = global_area

        with open(path, 'w', newline='') as fh:
            for hline in spec_headers:
                fh.write(hline + '\n')
            fh.write(f"#3_sigma_clip_noise_floor={noise_floor:.6f}\n")
            fh.write(f"#ratio_kind={ratio_kind}\n")
            if ratio_mode == "global":
                fh.write("#mode_denom=global_spectrum_area\n")
                fh.write(f"#global_spectrum_area={global_area}\n")
            else:
                fh.write("#mode_denom=max_peak_list_area\n")
                fh.write(f"#max_peak_list_area={global_area}\n")
            fh.write("##########\n")
            writer = csv.writer(fh)
            writer.writerow([
                "peak_list_label", "Reference mass", "Real peak mass",
                "window_lo", "window_hi",
                "peak_max_intensity", "peak_area",
                "peak list ratio",
                "individual peak / mode area (full spectra or higher peak list area)",
                "individual peak / peak list area",
            ])
            for label, color, peaks, peak_details, total_area in results:
                ratio_val = (total_area / global_area) if global_area > 0 else 0.0
                for mz_nom, peak_max, peak_area, real_mz, mz_lo, mz_hi in peak_details:
                    indiv_mode = (peak_area / global_area) if global_area > 0 else 0.0
                    indiv_pl   = (peak_area / total_area)  if total_area  > 0 else 0.0
                    writer.writerow([
                        label,
                        f"{mz_nom:.6f}", f"{real_mz:.6f}",
                        f"{mz_lo:.6f}",  f"{mz_hi:.6f}",
                        f"{peak_max:.6f}", f"{peak_area:.6f}",
                        f"{ratio_val:.6f}", f"{indiv_mode:.6f}", f"{indiv_pl:.6f}",
                    ])

    # ── Batch export (with per-file boundary checker) ─────────────────────────

    def _batch_export_folder_ratios(self):
        """Compute and export ratios for every file in the current folder."""
        _a = _get_app()
        files = _a.list_all_txt_files()
        if not files:
            QtWidgets.QMessageBox.warning(self, "No files",
                "No spectrum files are available in the current folder.")
            return
        toggled_rows = [(chk, row) for chk, row in self._pl_chks if chk.isChecked()]
        if not toggled_rows:
            QtWidgets.QMessageBox.warning(self, "No peak lists",
                "Please toggle at least one peak list before batch export.")
            return
        ratio_mode = self._ratio_mode_combo.currentData()
        out_folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Output Folder", _a.base_dir or "")
        if not out_folder:
            return
        sep    = _a.get_sep_from_combo() if callable(getattr(_a, 'get_sep_from_combo', None)) else None
        n_ok   = 0
        n_skip = 0
        errors = []
        n_total = len(files)

        # Single dialog instance — geometry (size / position) persists across files.
        dlg = None

        for i, fpath in enumerate(files):
            try:
                fdata = _a.read_spectrum_file(fpath, sep=sep)

                entries, mz_arr, corr_int, nf_raw = self._get_peak_bounds(fdata)
                if not entries:
                    continue

                stem  = os.path.splitext(os.path.basename(fpath))[0]
                title = f"{stem}  [{i + 1} / {n_total}]"

                if dlg is None:
                    dlg = PeakBoundaryCheckerDialog(
                        mz_arr, corr_int, nf_raw, entries,
                        title=title, batch_mode=True, parent=self)
                else:
                    dlg.update_data(mz_arr, corr_int, nf_raw, entries, title=title)

                ret = dlg.exec()

                if dlg.cancel_all:
                    break
                if dlg.skipped or ret != QtWidgets.QDialog.DialogCode.Accepted:
                    n_skip += 1
                    continue

                results = self._compute_areas_from_custom_bounds(fdata, dlg.result_bounds)
                if not results:
                    continue

                spec_headers = []
                with open(fpath, 'r', errors='replace') as fh:
                    for line in fh:
                        if line.startswith('#'):
                            spec_headers.append(line.rstrip('\n').rstrip('\r'))
                        else:
                            break

                out_path = os.path.join(out_folder, stem + "_ratios.csv")
                self._write_ratios_csv(
                    out_path, results, ratio_mode, spec_headers, fdata, global_area=None)
                n_ok += 1
            except Exception as e:
                errors.append(f"{os.path.basename(fpath)}: {e}")

        msg = f"Done. {n_ok} file(s) exported to:\n{out_folder}"
        if n_skip:
            msg += f"\n{n_skip} file(s) skipped."
        if errors:
            msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:10])
        QtWidgets.QMessageBox.information(self, "Batch Export Complete", msg)

    # ── Window events ─────────────────────────────────────────────────────────

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_peak_list_rows()
        self._refresh_spec_combo()

    def closeEvent(self, event):
        _a = _get_app()
        if _a.area_mode_action.isChecked():
            _a._area_mode_set(False)
        _a._area_win_ref = None
        super().closeEvent(event)
