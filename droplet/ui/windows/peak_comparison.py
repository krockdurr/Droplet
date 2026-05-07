"""PeakComparisonWindow - find common and unique peaks across visible spectra."""

import numpy as np
import pyqtgraph as pg

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet.analysis.peaks import get_auto_peaks
from droplet.io.spectrum_reader import spectrum_display_name
from droplet.ui.mixins import StayOnTopMixin


def _get_app():
    """Deferred import of the app module to avoid circular imports."""
    import droplet.app as _m
    return _m


_comparison_win_ref = None


class PeakComparisonWindow(QtWidgets.QWidget, StayOnTopMixin):
    """
    Non-modal window.  Detects peaks in each visible spectrum, classifies
    them as common (present in ALL spectra) or unique (present in only one),
    then draws coloured InfiniteLines on the main plot.

    Three opacity sliders control:
        • background   – the spectrum curves themselves
        • common peaks – green vertical lines
        • unique peaks – per-spectrum coloured lines
    """

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Compare Common / Unique Peaks")
        self.resize(560, 500)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self._lines  = []
        self._active = False

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

        _mbar = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar)
        root.setMenuBar(_mbar)

        # ── Detection parameters ───────────────────────────────────────────
        param_box = QtWidgets.QGroupBox("Detection parameters")
        pg_layout = QtWidgets.QGridLayout(param_box)
        pg_layout.setSpacing(6)

        pg_layout.addWidget(QtWidgets.QLabel("Threshold mode:"), 0, 0)
        self._mode_combo = QtWidgets.QComboBox()
        self._mode_combo.addItems(["% of max intensity", "SNR"])
        pg_layout.addWidget(self._mode_combo, 0, 1)

        pg_layout.addWidget(QtWidgets.QLabel("Threshold value:"), 1, 0)
        self._thr_spin = QtWidgets.QDoubleSpinBox()
        self._thr_spin.setRange(0.0, 1000.0)
        self._thr_spin.setDecimals(1)
        self._thr_spin.setSingleStep(0.1)
        self._thr_spin.setValue(5.0)
        self._thr_spin.setSuffix(" %")
        pg_layout.addWidget(self._thr_spin, 1, 1)

        pg_layout.addWidget(QtWidgets.QLabel("Match tolerance (±Da):"), 2, 0)
        self._tol_spin = QtWidgets.QDoubleSpinBox()
        self._tol_spin.setRange(0.01, 5.0)
        self._tol_spin.setDecimals(2)
        self._tol_spin.setValue(0.3)
        pg_layout.addWidget(self._tol_spin, 2, 1)

        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        root.addWidget(param_box)

        # ── Opacity sliders ────────────────────────────────────────────────
        op_box = QtWidgets.QGroupBox("Opacity")
        op_layout = QtWidgets.QGridLayout(op_box)
        op_layout.setSpacing(6)

        def _make_slider(default):
            s = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
            s.setRange(0, 100); s.setValue(default)
            return s

        op_layout.addWidget(QtWidgets.QLabel("Background spectra:"), 0, 0)
        self._bg_slider = _make_slider(40)
        op_layout.addWidget(self._bg_slider, 0, 1)
        self._bg_lbl = QtWidgets.QLabel("40 %")
        op_layout.addWidget(self._bg_lbl, 0, 2)

        op_layout.addWidget(QtWidgets.QLabel("Common peaks:"), 1, 0)
        self._common_slider = _make_slider(90)
        op_layout.addWidget(self._common_slider, 1, 1)
        self._common_lbl = QtWidgets.QLabel("90 %")
        op_layout.addWidget(self._common_lbl, 1, 2)

        op_layout.addWidget(QtWidgets.QLabel("Unique peaks:"), 2, 0)
        self._unique_slider = _make_slider(70)
        op_layout.addWidget(self._unique_slider, 2, 1)
        self._unique_lbl = QtWidgets.QLabel("70 %")
        op_layout.addWidget(self._unique_lbl, 2, 2)

        self._bg_slider.valueChanged.connect(
            lambda v: (self._bg_lbl.setText(f"{v} %"), self._apply_opacities()))
        self._common_slider.valueChanged.connect(
            lambda v: (self._common_lbl.setText(f"{v} %"), self._apply_opacities()))
        self._unique_slider.valueChanged.connect(
            lambda v: (self._unique_lbl.setText(f"{v} %"), self._apply_opacities()))
        root.addWidget(op_box)

        self._summary_lbl = QtWidgets.QLabel("Run detection to see results.")
        self._summary_lbl.setWordWrap(True)
        self._summary_lbl.setStyleSheet("color: gray; font-size: 12px;")
        self._summary_lbl.setMinimumHeight(80)
        root.addWidget(self._summary_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        self._run_btn = QtWidgets.QPushButton("🔍  Detect Peaks")
        self._run_btn.setFixedHeight(30)
        self._run_btn.clicked.connect(self._run)
        btn_row.addWidget(self._run_btn)
        btn_row.addStretch()
        self._clear_btn = QtWidgets.QPushButton("Clear")
        self._clear_btn.clicked.connect(self._clear_lines)
        btn_row.addWidget(self._clear_btn)
        root.addLayout(btn_row)

    def _on_mode_changed(self):
        if self._mode_combo.currentText() == "SNR":
            self._thr_spin.setSuffix("")
            self._thr_spin.setValue(3.0)
            self._thr_spin.setToolTip("Minimum signal-to-noise ratio")
        else:
            self._thr_spin.setSuffix(" %")
            self._thr_spin.setValue(5.0)
            self._thr_spin.setToolTip("% of spectrum maximum intensity")

    def _collect_spectra(self):
        _a = _get_app()
        result = []
        main_color = '#888888' if _a.current_display == 'bright' else '#aaaaaa'
        if _a.main_toggle.isChecked() and _a.df is not None:
            name = spectrum_display_name(_a.combo.currentData() or _a.combo.currentText())
            result.append((_a.df, main_color, name))
        for ov in _a.overlay_list:
            if ov["toggle"].isChecked() and ov["df"] is not None:
                ov_path = ov["combo"].currentData() or ov["combo"].currentText()
                name = (spectrum_display_name(ov_path)
                        if not ov.get("is_processed") else ov["toggle"].text())
                result.append((ov["df"], ov["color"], name))
        return result

    def _detect_peaks_in(self, data_df):
        thr  = self._thr_spin.value()
        mode = "snr" if self._mode_combo.currentText() == "SNR" else "pct"
        pk_mz, _ = get_auto_peaks(data_df, thr, mode)
        return np.sort(pk_mz)

    def _classify(self, all_peaks_list, tol):
        if not all_peaks_list:
            return np.array([]), []
        n = len(all_peaks_list)
        if n == 1:
            return np.array([]), [all_peaks_list[0]]
        common = []
        for mz in all_peaks_list[0]:
            if all(np.any(np.abs(other - mz) <= tol) for other in all_peaks_list[1:]):
                common.append(mz)
        common_arr = np.array(common)
        unique = []
        for peaks in all_peaks_list:
            if len(common_arr) == 0:
                unique.append(peaks.copy())
            else:
                mask = np.all(np.abs(peaks[:, None] - common_arr[None, :]) > tol, axis=1)
                unique.append(peaks[mask])
        return common_arr, unique

    def _clear_lines(self):
        _a = _get_app()
        for line in self._lines:
            try:
                _a.plot.removeItem(line)
            except Exception:
                pass
        self._lines.clear()
        self._active = False
        _a.overlay_opacity_slider.setValue(_a.overlay_opacity_slider.value())
        _a.render_plot()

    def _run(self):
        self._clear_lines()
        _a = _get_app()
        spectra = self._collect_spectra()
        if len(spectra) < 2:
            self._summary_lbl.setText(
                "⚠  Need at least 2 visible spectra (main + at least one overlay).")
            return

        tol = self._tol_spin.value()
        all_peaks = [self._detect_peaks_in(s[0]) for s in spectra]
        total_detected = sum(len(p) for p in all_peaks)

        if total_detected == 0:
            self._summary_lbl.setText("No peaks detected. Try lowering the threshold.")
            return

        common_mzs, unique_lists = self._classify(all_peaks, tol)

        common_alpha = int(self._common_slider.value() / 100 * 255)
        for mz in common_mzs:
            pen = pg.mkPen(QtGui.QColor(0, 200, 80, common_alpha), width=1,
                           style=QtCore.Qt.PenStyle.SolidLine)
            line = pg.InfiniteLine(pos=mz, angle=90, movable=False, pen=pen)
            _a.plot.addItem(line, ignoreBounds=True)
            self._lines.append(line)

        unique_alpha = int(self._unique_slider.value() / 100 * 255)
        for (data_df, color_hex, name), unique_mzs in zip(spectra, unique_lists):
            base_color = pg.mkColor(color_hex)
            for mz in unique_mzs:
                c = QtGui.QColor(base_color)
                c.setAlpha(unique_alpha)
                pen = pg.mkPen(c, width=1, style=QtCore.Qt.PenStyle.DashLine)
                line = pg.InfiniteLine(pos=mz, angle=90, movable=False, pen=pen)
                _a.plot.addItem(line, ignoreBounds=True)
                self._lines.append(line)

        self._active = True
        self._apply_opacities()

        lines = [
            f"<b style='font-size:13px'>Comparison summary</b><br>"
            f"<span style='color:gray'>{len(spectra)} spectra · tolerance ±{tol} Da</span>",
            f"<br><b style='color:#00c850'>Common peaks: {len(common_mzs)}</b>"
            f"  <span style='color:gray;font-size:11px'>(present in all spectra - green lines)</span>",
        ]
        if 0 < len(common_mzs) <= 20:
            mz_strs = ",  ".join(f"{m:.2f}" for m in sorted(common_mzs))
            lines.append(f"<span style='font-size:11px;color:gray'>m/z: {mz_strs}</span>")
        lines.append("<br><b>Unique peaks per spectrum:</b>")
        for (_, color_hex, name), unique_mzs in zip(spectra, unique_lists):
            lines.append(
                f"&nbsp;&nbsp;<span style='color:{color_hex}'>■ {name}</span>: "
                f"<b>{len(unique_mzs)}</b> unique"
                + (f"  <span style='font-size:11px;color:gray'>"
                   f"(m/z: {', '.join(f'{m:.2f}' for m in sorted(unique_mzs)[:8])}"
                   f"{'…' if len(unique_mzs) > 8 else ''})</span>"
                   if 0 < len(unique_mzs) <= 30 else "")
            )
        self._summary_lbl.setText("<br>".join(lines))
        self._summary_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    def _apply_opacities(self):
        if not self._active:
            return
        _a = _get_app()
        bg_alpha = int(self._bg_slider.value() / 100 * 255)
        if _a._main_curve is not None:
            c = pg.mkColor('#888888' if _a.current_display == 'bright' else '#aaaaaa')
            c.setAlpha(bg_alpha)
            _a._main_curve.setPen(pg.mkPen(c, width=1))
        for ov in _a.overlay_list:
            ov_id = id(ov)
            if ov_id in _a._overlay_curves:
                c = pg.mkColor(ov["color"])
                c.setAlpha(bg_alpha)
                _a._overlay_curves[ov_id].setPen(pg.mkPen(c, width=1))
        common_alpha = int(self._common_slider.value() / 100 * 255)
        unique_alpha = int(self._unique_slider.value() / 100 * 255)
        for line in self._lines:
            pen = line.pen
            c   = pen.color()
            is_common = (c.green() > 100 and c.red() < 80)
            new_alpha = common_alpha if is_common else unique_alpha
            c.setAlpha(new_alpha)
            pen.setColor(c)
            line.setPen(pen)

    def closeEvent(self, event):
        self._clear_lines()
        _a = _get_app()
        _a._comparison_win_ref = None
        super().closeEvent(event)
