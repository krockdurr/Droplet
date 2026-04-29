"""PeakAreaWindow - interactive area measurement and per-peak-list area ratios."""

import numpy as np

try:
    from PyQt6 import QtWidgets, QtCore
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore

from droplet.analysis.peaks import parse_peaks_text
from droplet.processing.signal import get_tolerance


def _get_app():
    import droplet.app as _m
    return _m


class PeakAreaWindow(QtWidgets.QWidget):
    """
    Non-modal window combining:
      1. Interactive range-click area measurement (toggle-based)
      2. Total spectrum area computation
      3. Per-peak-list area ratios
    """

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Peak Area Measurement")
        self.resize(500, 520)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(10, 10, 10, 10)

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

        ratio_box = QtWidgets.QGroupBox("Peak-list area ratios")
        ratio_lay = QtWidgets.QVBoxLayout(ratio_box)
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
        self._ratio_lbl = QtWidgets.QLabel("-")
        self._ratio_lbl.setWordWrap(True)
        self._ratio_lbl.setStyleSheet("font-size: 12px; color: gray;")
        ratio_lay.addWidget(self._ratio_lbl)
        root.addWidget(ratio_box)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self._refresh_spec_combo()
        self._refresh_peak_list_rows()

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

    def _compute_total(self):
        self._refresh_spec_combo()
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            self._total_lbl.setText("No spectrum selected or loaded.")
            return
        mz_arr  = data['mz'].values
        int_arr = data['intensity'].values
        total   = float(np.trapz(int_arr, mz_arr))
        mz_lo, mz_hi = float(mz_arr.min()), float(mz_arr.max())
        self._total_lbl.setText(
            f"Total area: <b>{total:.4e}</b><br>"
            f"m/z range: {mz_lo:.2f} – {mz_hi:.2f}<br>"
            f"({len(data)} data points)")
        self._total_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    def _compute_ratios(self):
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            self._ratio_lbl.setText("No spectrum selected or loaded.")
            return
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
            for mz_nom in peaks:
                tol  = get_tolerance(mz_nom)
                mask = (data['mz'] >= mz_nom - tol) & (data['mz'] <= mz_nom + tol)
                sub  = data[mask]
                if len(sub) >= 2:
                    total_area += float(np.trapz(sub['intensity'].values, sub['mz'].values))
            results.append((label, color, total_area))

        if not results:
            self._ratio_lbl.setText("No peak lists selected.")
            return

        max_area = max(r[2] for r in results) or 1.0
        lines = ["<b>Relative areas (normalized to largest):</b>"]
        for label, color, area in results:
            ratio = area / max_area
            lines.append(
                f"<span style='color:{color}'>■ {label}</span>: "
                f"area = <b>{area:.4e}</b>  (ratio = <b>{ratio:.4f}</b>)")
        if len(results) == 2:
            r0, r1 = results[0][2], results[1][2]
            if r1 != 0:
                lines.append(
                    f"<br>{results[0][0]} / {results[1][0]} = <b>{r0/r1:.4f}</b>")
        self._ratio_lbl.setText("<br>".join(lines))
        self._ratio_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

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
