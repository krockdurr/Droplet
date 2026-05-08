"""PeakAreaWindow - interactive area measurement and per-peak-list area ratios."""

import os
import csv
import numpy as np

try:
    from PyQt6 import QtWidgets, QtCore
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore

from droplet.analysis.peaks import parse_peaks_text
from droplet.processing.signal import find_peak_bounds
from droplet.ui.mixins import StayOnTopMixin


def _get_app():
    import droplet.app as _m
    return _m


_area_win_ref = None


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
        self._ratio_mode_combo.addItem("Between peak lists (normalized to largest)", "between")
        self._ratio_mode_combo.addItem("Normalized by global spectrum area", "global")
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
        ratio_lay.addWidget(self._ratio_lbl)
        root.addWidget(ratio_box)

        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        root.addWidget(close_btn)

        self._last_ratio_results = []   # list of (label, color, area, ratio_value)
        self._last_ratio_mode    = "between"

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
        _a = _get_app()
        current_key = self._spec_combo.currentData()
        self._refresh_spec_combo()
        # Restore previously selected spectrum after refresh
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
        total = float(np.trapz(corr_int, mz_arr))
        mz_lo, mz_hi = float(mz_arr.min()), float(mz_arr.max())
        self._total_lbl.setText(
            f"Total area (m/z ≥ {MZ_THRESHOLD}): <b>{total:.4e}</b><br>"
            f"m/z range: {mz_lo:.2f} – {mz_hi:.2f}<br>"
            f"({len(data_thr)} data points above threshold)"
            f"noise floor ≈ {floor:.4f}<br>")
        self._total_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    def _compute_ratios(self):
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            self._ratio_lbl.setText("No spectrum selected or loaded.")
            return
        raw_results = self._compute_peak_list_areas(data)
        if not raw_results:
            self._ratio_lbl.setText("No peak lists selected.")
            return

        ratio_mode = self._ratio_mode_combo.currentData()
        MZ_THRESHOLD = 10.9

        if ratio_mode == "global":
            mask = data['mz'] >= MZ_THRESHOLD
            data_thr = data[mask]

            if len(data_thr) >= 2:
                mz_arr, corr_int, _ = self._correct_spectrum(data_thr)
                global_area = float(np.trapz(corr_int, mz_arr))
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

        # Also show pairwise ratios if exactly 2 lists and "between" mode
        if ratio_mode == "between" and len(raw_results) == 2:
            r0, r1 = raw_results[0][4], raw_results[1][4]
            if r1 != 0:
                lines.append(
                    f"<br>{raw_results[0][0]} / {raw_results[1][0]} = "
                    f"<b>{r0/r1:.4f}</b>")

        self._last_ratio_mode = ratio_mode
        self._ratio_lbl.setText("<br>".join(lines))
        self._ratio_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)

    def _correct_spectrum(self, data):
        """Returns: mz (np.array), corrected_intensity (np.array), floor (float).

        Floor is 3-sigma clipped from the m/z >= 10.9 region only,
        matching the interactive area tool in app.py.
        """
        _a = _get_app()
        MZ_THRESHOLD = 10.9
        mz = data['mz'].values
        intensity = data['intensity'].values
        mask_thr = mz >= MZ_THRESHOLD
        if mask_thr.sum() >= 10:
            floor = _a._estimate_noise_floor(intensity[mask_thr], n_sigma=3.0)
        else:
            floor = _a._DYN_CLIP_FLOOR
        corrected = intensity - floor
        corrected[corrected < 0] = 0.0
        return mz, corrected, floor

    def _compute_peak_list_areas(self, data):
        """Return list of (label, color, mz_list, peak_details, area) for toggled peak lists."""
        results = []
        mz_all, corr_all, floor = self._correct_spectrum(data)
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
                bounds = find_peak_bounds(mz_all, corr_all, mz_nom, noise_floor=floor)
                if bounds is None:
                    peak_details.append((mz_nom, 0.0, 0.0, mz_nom, mz_nom, mz_nom))
                    continue
                mz_lo_bound, mz_hi_bound, real_mz, peak_max = bounds
                mask    = (mz_all >= mz_lo_bound) & (mz_all <= mz_hi_bound)
                mz_sub  = mz_all[mask]
                int_sub = corr_all[mask]
                if len(mz_sub) >= 2:
                    peak_area = float(np.trapz(int_sub, mz_sub))
                    total_area += peak_area
                    peak_details.append((mz_nom, float(peak_max), peak_area,
                                         real_mz, mz_lo_bound, mz_hi_bound))
                else:
                    peak_details.append((mz_nom, 0.0, 0.0, real_mz, mz_lo_bound, mz_hi_bound))
            results.append((label, color, peaks, peak_details, total_area))
        return results



        #         tol = get_tolerance(mz_nom)
        #         mz_lo_bound = mz_nom - tol
        #         mz_hi_bound = mz_nom + tol
        #         mask = (data['mz'] >= mz_lo_bound) & (data['mz'] <= mz_hi_bound)
        #         mz_sub  = mz_all[mask]
        #         int_sub = corr_all[mask]
        #         if len(mz_sub) >= 2:
        #             peak_area = float(np.trapz(int_sub, mz_sub))
        #             peak_max  = float(int_sub.max())
        #             real_mz   = float(mz_sub[int_sub.argmax()])
        #             total_area += peak_area
        #             peak_details.append((mz_nom, peak_max, peak_area, real_mz, mz_lo_bound, mz_hi_bound, tol))
        #         else:
        #             peak_details.append((mz_nom, 0.0, 0.0, mz_nom, mz_lo_bound, mz_hi_bound, tol))
        #     results.append((label, color, peaks, peak_details, total_area))
        # return results

    # def _save_peak_areas_csv(self):
    #     """Save pure peak areas (no ratios) for each peak in toggled peak lists."""
    #     data = self._get_selected_df()
    #     if data is None or len(data) == 0:
    #         QtWidgets.QMessageBox.warning(self, "No spectrum", "No spectrum selected or loaded.")
    #         return
    #     results = self._compute_peak_list_areas(data)
    #     if not results:
    #         QtWidgets.QMessageBox.warning(self, "No data", "No peak lists selected.")
    #         return
    #     path, _ = QtWidgets.QFileDialog.getSaveFileName(
    #         self, "Save Peak Areas", "", "CSV Files (*.csv)")
    #     if not path:
    #         return
    #     with open(path, 'w', newline='') as fh:
    #         writer = csv.writer(fh)
    #         writer.writerow(["peak_list_label", "peak_mz", "peak_max_intensity", "peak_area"])
    #         for label, color, peaks, peak_details, total_area in results:
    #             for mz_nom, peak_max, peak_area in peak_details:
    #                 writer.writerow([label, f"{mz_nom:.6f}", f"{peak_max:.6f}", f"{peak_area:.6f}"])
    #     QtWidgets.QMessageBox.information(self, "Saved", f"Peak areas saved to:\n{path}")

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

    def _export_ratios_csv(self):
        """Export ratio results for the current spectrum to a CSV file."""
        data = self._get_selected_df()
        if data is None or len(data) == 0:
            QtWidgets.QMessageBox.warning(self, "No spectrum", "No spectrum selected or loaded.")
            return
        results = self._compute_peak_list_areas(data)
        if not results:
            QtWidgets.QMessageBox.warning(self, "No data", "No peak lists selected.")
            return
        ratio_mode = self._ratio_mode_combo.currentData()
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Ratios", "", "CSV Files (*.csv)")
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        data_key = self._spec_combo.currentData()
        spec_headers = self._get_spectrum_headers(data_key)
        self._write_ratios_csv(path, results, ratio_mode, spec_headers, data)
        QtWidgets.QMessageBox.information(self, "Saved", f"Ratios saved to:\n{path}")

    def _write_ratios_csv(self, path, results, ratio_mode, spec_headers, data, global_area=None):
        """Write a ratio CSV file with spectrum headers, ratio kind, and ratioed peak list data."""
        MZ_THRESHOLD = 10.9

        # Compute noise floor the same way _correct_spectrum does, for the header.
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
                    global_area = float(np.trapz(corr_int, mz_arr))
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
                "peak_list_label", "Reference mass", "Real peak mass", "window_lo", "window_hi",
                "peak_max_intensity", "peak_area",
                "peak list ratio", "individual peak / mode area (full spectra or higher peak list area)",
                "individual peak / peak list area",
            ])
            for label, color, peaks, peak_details, total_area in results:
                ratio_val = (total_area / global_area) if global_area > 0 else 0.0
                for mz_nom, peak_max, peak_area, real_mz, mz_lo, mz_hi in peak_details:
                    indiv_mode = (peak_area / global_area) if global_area > 0 else 0.0
                    indiv_pl   = (peak_area / total_area)  if total_area  > 0 else 0.0
                    writer.writerow([
                        label, f"{mz_nom:.6f}", f"{real_mz:.6f}", f"{mz_lo:.6f}", f"{mz_hi:.6f}",
                        f"{peak_max:.6f}", f"{peak_area:.6f}",
                        f"{ratio_val:.6f}", f"{indiv_mode:.6f}", f"{indiv_pl:.6f}",
                    ])

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
        sep = _a.get_sep_from_combo() if callable(getattr(_a, 'get_sep_from_combo', None)) else None
        n_ok = 0
        errors = []

        for fpath in files:
            try:
                fdata = _a.read_spectrum_file(fpath, sep=sep)
                mz_all, corr_all, floor = self._correct_spectrum(fdata)
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
                        bounds = find_peak_bounds(mz_all, corr_all, mz_nom, noise_floor=floor)
                        if bounds is None:
                            peak_details.append((mz_nom, 0.0, 0.0, mz_nom, mz_nom, mz_nom))
                            continue
                        mz_lo_bound, mz_hi_bound, real_mz, peak_max = bounds
                        mask    = (mz_all >= mz_lo_bound) & (mz_all <= mz_hi_bound)
                        mz_sub  = mz_all[mask]
                        int_sub = corr_all[mask]
                        if len(mz_sub) >= 2:
                            peak_area = float(np.trapz(int_sub, mz_sub))
                            total_area += peak_area
                            peak_details.append((mz_nom, float(peak_max), peak_area,
                                                 real_mz, mz_lo_bound, mz_hi_bound))
                        else:
                            peak_details.append((mz_nom, 0.0, 0.0, real_mz, mz_lo_bound, mz_hi_bound))
                    results.append((label, color, peaks, peak_details, total_area))
                if not results:
                    continue
                spec_headers = []
                with open(fpath, 'r', errors='replace') as fh:
                    for line in fh:
                        if line.startswith('#'):
                            spec_headers.append(line.rstrip('\n').rstrip('\r'))
                        else:
                            break
                stem = os.path.splitext(os.path.basename(fpath))[0]
                out_path = os.path.join(out_folder, stem + "_ratios.csv")
                self._write_ratios_csv(
                    out_path,
                    results,
                    ratio_mode,
                    spec_headers,
                    fdata,
                    global_area=self._last_global_area
                )
                n_ok += 1
            except Exception as e:
                errors.append(f"{os.path.basename(fpath)}: {e}")
        msg = f"Done. {n_ok} file(s) exported to:\n{out_folder}"
        if errors:
            msg += f"\n\nErrors ({len(errors)}):\n" + "\n".join(errors[:10])
        QtWidgets.QMessageBox.information(self, "Batch Export Complete", msg)

    # def _export_pure_areas(self):
    #     data = self._get_selected_df()
    #     if data is None or len(data) == 0:
    #         QtWidgets.QMessageBox.warning(self, "No data", "No spectrum selected.")
    #         return
    #     rows = []
    #     for chk, row in self._pl_chks:
    #         if not chk.isChecked(): continue
    #         label  = row["label_input"].text().strip() or "Unnamed"
    #         peaks  = parse_peaks_text(row["peaks_input"].text())
    #         for mz_nom in peaks:
    #             tol  = get_tolerance(mz_nom)
    #             mask = (data['mz'] >= mz_nom - tol) & (data['mz'] <= mz_nom + tol)
    #             sub  = data[mask]
    #             if len(sub) < 2: continue
    #             peak_max_int = float(sub['intensity'].max())
    #             peak_area    = float(np.trapz(sub['intensity'].values, sub['mz'].values))
    #             rows.append((label, mz_nom, peak_max_int, peak_area))
    #     if not rows:
    #         QtWidgets.QMessageBox.information(self, "No peaks", "No peaks found.")
    #         return
    #     path, _ = QtWidgets.QFileDialog.getSaveFileName(
    #         self, "Export Peak Areas", "", "CSV Files (*.csv)")
    #     if not path: return
    #     with open(path, "w", newline="") as fh:
    #         writer = csv.writer(fh)
    #         writer.writerow(["Peak list label", "m/z (nominal)", "Peak max intensity", "Peak area"])
    #         for r in rows:
    #             writer.writerow([r[0], f"{r[1]:.4f}", f"{r[2]:.6g}", f"{r[3]:.6g}"])
    #     QtWidgets.QMessageBox.information(self, "Exported", f"Saved to:\n{path}")

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