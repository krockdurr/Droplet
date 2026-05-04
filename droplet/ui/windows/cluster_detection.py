"""ClusterDetectionWindow - find regularly-spaced series of peaks."""

import multiprocessing
import numpy as np
import pyqtgraph as pg

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet.analysis.clusters import run_cluster_detection
from droplet.analysis.peaks import parse_peaks_text
from droplet.constants import OVERLAY_COLORS, CLUSTER_SYMBOLS


def _get_app():
    import droplet.app as _m
    return _m


class ClusterDetectionWindow(QtWidgets.QWidget):
    """Non-modal window: parameter controls on top, results grouped by cluster below."""

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Cluster Detection")
        self.resize(720, 580)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        _a = _get_app()

        self._clusters                 = []
        self._inc_chks                 = []
        self._scatter_items_by_cluster = []
        self._group_boxes              = []
        self._selected_cluster         = None
        self._scatter_click_handled    = False
        self._click_cycle              = []
        self._click_cycle_pos          = None

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        # ── Parameter bar ──────────────────────────────────────────────────
        param_box  = QtWidgets.QGroupBox("Detection parameters")
        param_grid = QtWidgets.QGridLayout(param_box)
        param_grid.setSpacing(6)

        param_grid.addWidget(QtWidgets.QLabel("Spacings (Da, comma-sep; empty = auto):"), 0, 0)
        self._spacing_edit = QtWidgets.QLineEdit()
        self._spacing_edit.setText(_a.settings.value("cluster_spacings", ""))
        self._spacing_edit.setToolTip(
            "Comma-separated candidate spacings in Da.\n"
            "Leave empty to let the algorithm discover spacings automatically.")
        param_grid.addWidget(self._spacing_edit, 0, 1, 1, 3)

        param_grid.addWidget(QtWidgets.QLabel("Tolerance (±Da):"), 1, 0)
        self._tol_spin = QtWidgets.QDoubleSpinBox()
        self._tol_spin.setRange(0.01, 5.0); self._tol_spin.setDecimals(3)
        self._tol_spin.setSingleStep(0.05)
        self._tol_spin.setValue(float(_a.settings.value("cluster_tol", 0.3)))
        param_grid.addWidget(self._tol_spin, 1, 1)

        param_grid.addWidget(QtWidgets.QLabel("Min chain length:"), 1, 2)
        self._chain_spin = QtWidgets.QSpinBox()
        self._chain_spin.setRange(2, 20)
        self._chain_spin.setValue(int(_a.settings.value("cluster_min_chain", 2)))
        param_grid.addWidget(self._chain_spin, 1, 3)

        param_grid.addWidget(QtWidgets.QLabel("Min SNR:"), 2, 0)
        self._snr_spin = QtWidgets.QDoubleSpinBox()
        self._snr_spin.setRange(0.0, 100.0); self._snr_spin.setDecimals(1)
        self._snr_spin.setSingleStep(0.1)
        self._snr_spin.setValue(float(_a.settings.value("cluster_snr", 1.5)))
        param_grid.addWidget(self._snr_spin, 2, 1)

        param_grid.addWidget(QtWidgets.QLabel("Min intensity %:"), 2, 2)
        self._pct_spin = QtWidgets.QDoubleSpinBox()
        self._pct_spin.setRange(0.0, 50.0); self._pct_spin.setDecimals(2)
        self._pct_spin.setSingleStep(0.1)
        self._pct_spin.setValue(float(_a.settings.value("cluster_pct", 5.0)))
        self._pct_spin.setSuffix(" %")
        param_grid.addWidget(self._pct_spin, 2, 3)

        detect_btn = QtWidgets.QPushButton("🔍  Detect Clusters")
        detect_btn.setFixedHeight(30)
        detect_btn.clicked.connect(self._run_detection)
        param_grid.addWidget(detect_btn, 3, 0, 1, 4)
        root.addWidget(param_box)

        # ── Peak-list mode ─────────────────────────────────────────────────
        self._pl_box = QtWidgets.QGroupBox("Use Peak List(s) instead of spectrum")
        self._pl_box.setCheckable(True)
        self._pl_box.setChecked(False)
        self._pl_box.setToolTip(
            "When enabled, detection runs only on the m/z values in the\n"
            "selected peak list(s). Spacings are discovered automatically.\n"
            "SNR / intensity % filters are ignored.")
        pl_layout = QtWidgets.QVBoxLayout(self._pl_box)
        pl_layout.setSpacing(3)
        self._pl_checks      = []
        self._pl_peak_checks = {}

        for row in _a.custom_peak_rows:
            lbl    = row["label_input"].text().strip() or "Unnamed"
            row_cb = QtWidgets.QCheckBox(lbl)
            row_cb.setStyleSheet("font-weight: bold;")
            pl_layout.addWidget(row_cb)
            peak_checks = []
            peaks = parse_peaks_text(row["peaks_input"].text())
            if peaks:
                peak_container = QtWidgets.QWidget()
                peak_layout    = QtWidgets.QHBoxLayout(peak_container)
                peak_layout.setContentsMargins(20, 0, 0, 0)
                peak_layout.setSpacing(4)
                for mz in peaks:
                    pk_cb = QtWidgets.QCheckBox(f"{mz:.4g}")
                    pk_cb.setChecked(True)
                    pk_cb.setToolTip(f"Include m/z {mz:.4g} in cluster detection")
                    peak_layout.addWidget(pk_cb)
                    peak_checks.append((pk_cb, mz))
                peak_layout.addStretch()
                pl_layout.addWidget(peak_container)
            self._pl_checks.append((row_cb, row))
            self._pl_peak_checks[id(row)] = peak_checks

        if not self._pl_checks:
            pl_layout.addWidget(QtWidgets.QLabel("(no peak lists defined)"))
        self._pl_box.toggled.connect(self._on_pl_mode_toggled)
        root.addWidget(self._pl_box)

        # ── Results scroll area ────────────────────────────────────────────
        self._scroll          = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._results_widget  = QtWidgets.QWidget()
        self._results_layout  = QtWidgets.QVBoxLayout(self._results_widget)
        self._results_layout.setContentsMargins(4, 4, 4, 4)
        self._results_layout.setSpacing(6)
        self._results_layout.addStretch()
        self._scroll.setWidget(self._results_widget)
        root.addWidget(self._scroll)

        self._status_lbl = QtWidgets.QLabel("Run detection to see results.")
        self._status_lbl.setStyleSheet("color: gray; font-size: 11px;")
        root.addWidget(self._status_lbl)

        btn_row = QtWidgets.QHBoxLayout()
        self._add_btn = QtWidgets.QPushButton("➕  Add selected clusters to Peak Lists")
        self._add_btn.setEnabled(False)
        self._add_btn.clicked.connect(self._add_to_peak_lists)
        btn_row.addWidget(self._add_btn)
        btn_row.addStretch()
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

    def _on_pl_mode_toggled(self, enabled):
        for widget in (self._spacing_edit, self._snr_spin, self._pct_spin):
            widget.setEnabled(not enabled)

    def _run_detection(self):
        _a = _get_app()
        if _a.df is None:
            QtWidgets.QMessageBox.warning(self, "No spectrum", "Load a spectrum first.")
            return

        progress = _a._make_progress_dialog(
            "Cluster Detection",
            f"Detecting clusters across "
            f"{max(1, min(multiprocessing.cpu_count()-1, 8))} threads…",
            cancellable=False)
        _a.app.processEvents()
        try:
            peak_list_mzs = None
            if self._pl_box.isChecked():
                mzs = []
                for cb, row in self._pl_checks:
                    if cb.isChecked():
                        pk_checks = self._pl_peak_checks.get(id(row), [])
                        if pk_checks:
                            mzs.extend(mz for pk_cb, mz in pk_checks if pk_cb.isChecked())
                peak_list_mzs = mzs if mzs else None

            known_mzs = []
            for row in _a.custom_peak_rows:
                for mz in parse_peaks_text(row["peaks_input"].text()):
                    known_mzs.append(mz)

            self._clusters = run_cluster_detection(
                spacings_input=self._spacing_edit.text(),
                tol=self._tol_spin.value(),
                min_chain=self._chain_spin.value(),
                min_snr=self._snr_spin.value(),
                min_pct=self._pct_spin.value(),
                data_df=_a.df,
                known_peak_mzs=known_mzs if known_mzs else None,
                peak_list_mzs=peak_list_mzs,
            )
        except Exception as e:
            progress.close()
            QtWidgets.QMessageBox.warning(self, "Detection Error", str(e))
            return
        progress.close()
        self._rebuild_results()

    def _rebuild_results(self):
        _a = _get_app()
        _a._clear_cluster_scatter()
        self._scatter_items_by_cluster.clear()
        self._group_boxes.clear()
        self._selected_cluster = None

        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._inc_chks.clear()

        if not self._clusters:
            self._status_lbl.setText(
                "No clusters found. Try lowering Min intensity % or Min chain length.")
            self._add_btn.setEnabled(False)
            self._results_layout.addStretch()
            return

        self._status_lbl.setText(
            f"{len(self._clusters)} cluster(s) detected.  "
            "✦ = peak overlaps a known peak list entry.")
        self._add_btn.setEnabled(True)

        for ci, cluster in enumerate(self._clusters):
            color_hex = OVERLAY_COLORS[ci % len(OVERLAY_COLORS)]
            symbol    = CLUSTER_SYMBOLS[ci % len(CLUSTER_SYMBOLS)]
            spacing   = cluster['spacing']
            members   = cluster['members']

            gb = QtWidgets.QGroupBox()
            gb.setStyleSheet(
                f"QGroupBox {{ border: 1.5px solid {color_hex}; border-radius: 5px; "
                f"margin-top: 6px; padding-top: 4px; }}")
            gb_vbox = QtWidgets.QVBoxLayout(gb)
            gb_vbox.setContentsMargins(6, 2, 6, 6)
            gb_vbox.setSpacing(3)

            hdr_row = QtWidgets.QHBoxLayout()
            swatch  = QtWidgets.QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {color_hex}; border: 1px solid gray; border-radius: 2px;")
            hdr_row.addWidget(swatch)
            hdr_lbl = QtWidgets.QLabel(
                f"<b>Cluster {ci+1}</b>  -  Δ = {spacing:.3f} Da  ({len(members)} peaks)")
            hdr_lbl.setStyleSheet(f"color: {color_hex};")
            hdr_row.addWidget(hdr_lbl)
            hdr_row.addStretch()

            all_none_btn = QtWidgets.QPushButton("None")
            all_none_btn.setFixedSize(46, 20)
            all_none_btn.setCheckable(True)
            all_none_btn.setStyleSheet(
                "QPushButton { font-size: 10px; padding: 0 4px; }"
                "QPushButton:checked { background: #444; color: #aaa; }")
            hdr_row.addWidget(all_none_btn)
            gb_vbox.addLayout(hdr_row)

            col_hdr = QtWidgets.QHBoxLayout()
            col_hdr.addSpacing(20)
            for txt, w in [("m/z", 100), ("Intensity", 110)]:
                lbl = QtWidgets.QLabel(f"<small><i>{txt}</i></small>")
                lbl.setFixedWidth(w)
                lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                col_hdr.addWidget(lbl)
            col_hdr.addWidget(QtWidgets.QLabel("<small><i>Include</i></small>"))
            col_hdr.addStretch()
            gb_vbox.addLayout(col_hdr)

            group_chks = []
            for mz, intensity, is_known in members:
                peak_row  = QtWidgets.QWidget()
                pr_layout = QtWidgets.QHBoxLayout(peak_row)
                pr_layout.setContentsMargins(20, 0, 0, 0); pr_layout.setSpacing(4)
                star    = " ✦" if is_known else ""
                mz_lbl  = QtWidgets.QLabel(f"{mz:.4f}{star}")
                mz_lbl.setFixedWidth(100)
                mz_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                if is_known:
                    mz_lbl.setStyleSheet("color: #f0a000;")
                    mz_lbl.setToolTip("This m/z overlaps a known peak list entry")
                int_lbl = QtWidgets.QLabel(f"{intensity:.3g}")
                int_lbl.setFixedWidth(110)
                int_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                int_lbl.setStyleSheet("color: gray; font-size: 11px;")
                chk = QtWidgets.QCheckBox(); chk.setChecked(True)
                pr_layout.addWidget(mz_lbl); pr_layout.addWidget(int_lbl)
                pr_layout.addWidget(chk); pr_layout.addStretch()
                gb_vbox.addWidget(peak_row)
                group_chks.append((mz, chk))

            self._inc_chks.append(group_chks)

            def _make_cascade(chks_list):
                def _on(checked, src_mz):
                    if checked: return
                    for mz, chk in chks_list:
                        if mz > src_mz:
                            chk.blockSignals(True); chk.setChecked(False); chk.blockSignals(False)
                return _on
            cascade = _make_cascade(group_chks)
            for mz, chk in group_chks:
                chk.toggled.connect(lambda checked, m=mz, fn=cascade: fn(checked, m))

            def _make_all_none(chks_list, btn):
                def _on(pressed):
                    for _, chk in chks_list:
                        chk.blockSignals(True); chk.setChecked(not pressed); chk.blockSignals(False)
                    btn.setText("All" if pressed else "None")
                return _on
            all_none_btn.toggled.connect(_make_all_none(group_chks, all_none_btn))

            self._group_boxes.append(gb)
            def _make_gb_click(idx):
                def _press(event):
                    if event.button() == QtCore.Qt.MouseButton.LeftButton:
                        self._select_cluster_direct(idx)
                    super(QtWidgets.QGroupBox, gb).mousePressEvent(event)
                return _press
            gb.mousePressEvent = _make_gb_click(ci)
            self._results_layout.addWidget(gb)

            mz_arr  = np.array([m[0] for m in members])
            int_arr = np.array([m[1] for m in members])
            y_arr   = np.where(int_arr > 0, np.log10(int_arr) + 0.05, 0.0)
            c       = pg.mkColor(color_hex); c.setAlpha(210)
            scatter = pg.ScatterPlotItem(
                x=mz_arr, y=y_arr, symbol=symbol, size=13,
                pen=pg.mkPen(color_hex, width=1.5), brush=pg.mkBrush(c))
            _a.plot.addItem(scatter)
            _a._cluster_scatter_items.append(scatter)
            self._scatter_items_by_cluster.append(scatter)
            scatter.sigClicked.connect(
                lambda item, pts, ev, i=ci: self._select_cluster(i, clicked_pts=pts))

        try:
            _a.plot.scene().sigMouseClicked.disconnect(self._on_plot_background_click)
        except Exception:
            pass
        _a.plot.scene().sigMouseClicked.connect(self._on_plot_background_click)
        self._results_layout.addStretch()

    def _add_to_peak_lists(self):
        _a = _get_app()
        added = 0
        for ci, (cluster, group_chks) in enumerate(zip(self._clusters, self._inc_chks)):
            included_mz = [mz for mz, chk in group_chks if chk.isChecked()]
            if not included_mz:
                continue
            peaks_text = ", ".join(f"{mz:.4f}" for mz in included_mz)
            color = QtGui.QColor(OVERLAY_COLORS[ci % len(OVERLAY_COLORS)])
            _a.add_peak_row(checked=True, color=color,
                            peaks_text=peaks_text, label_text=f"Cluster {ci+1}")
            added += 1
        if added:
            _a.open_peaks_window()
            self._status_lbl.setText(f"✓ Added {added} cluster(s) to Peak Lists.")
        else:
            self._status_lbl.setText("Nothing to add - all peaks are unchecked.")

    def _select_cluster(self, idx, clicked_pts=None):
        self._scatter_click_handled = True
        if clicked_pts is not None and len(clicked_pts) > 0:
            cx = clicked_pts[0].pos().x()
            cy = clicked_pts[0].pos().y()
            SNAP = 0.5
            if self._click_cycle_pos is not None:
                px, _ = self._click_cycle_pos
                same_spot = abs(cx - px) < SNAP
            else:
                same_spot = False
            if not same_spot or idx not in self._click_cycle:
                candidates = []
                for i, scatter in enumerate(self._scatter_items_by_cluster):
                    xs = scatter.getData()[0]
                    if xs is None: continue
                    if np.any(np.abs(xs - cx) < SNAP):
                        candidates.append(i)
                self._click_cycle     = candidates
                self._click_cycle_pos = (cx, cy)
                if idx in self._click_cycle:
                    self._click_cycle.remove(idx)
                    self._click_cycle.insert(0, idx)
        if not self._click_cycle:
            self._click_cycle     = [idx]
            self._click_cycle_pos = None
        if self._selected_cluster in self._click_cycle:
            current_pos = self._click_cycle.index(self._selected_cluster)
            next_idx    = self._click_cycle[(current_pos + 1) % len(self._click_cycle)]
        else:
            next_idx = self._click_cycle[0]
        if self._selected_cluster == next_idx and len(self._click_cycle) == 1:
            self._selected_cluster = None; self._apply_opacity(None)
        else:
            self._selected_cluster = next_idx; self._apply_opacity(next_idx)

    def _select_cluster_direct(self, idx):
        self._scatter_click_handled = True
        self._click_cycle           = []
        self._click_cycle_pos       = None
        if self._selected_cluster == idx:
            self._selected_cluster = None; self._apply_opacity(None)
        else:
            self._selected_cluster = idx; self._apply_opacity(idx)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            if self._scatter_click_handled:
                self._scatter_click_handled = False
            else:
                self._selected_cluster = None
                self._click_cycle      = []
                self._click_cycle_pos  = None
                self._apply_opacity(None)
        super().mousePressEvent(event)

    def _apply_opacity(self, selected_idx):
        DIM_ALPHA  = int(210 * 0.2)
        FULL_ALPHA = 210
        for i, scatter in enumerate(self._scatter_items_by_cluster):
            color_hex = OVERLAY_COLORS[i % len(OVERLAY_COLORS)]
            c = pg.mkColor(color_hex)
            if selected_idx is None or i == selected_idx:
                c.setAlpha(FULL_ALPHA)
                scatter.setPen(pg.mkPen(color_hex, width=1.5))
            else:
                c.setAlpha(DIM_ALPHA)
                dim = pg.mkColor(color_hex); dim.setAlpha(DIM_ALPHA)
                scatter.setPen(pg.mkPen(dim, width=1.5))
            scatter.setBrush(pg.mkBrush(c))
        for i, gb in enumerate(self._group_boxes):
            color_hex = OVERLAY_COLORS[i % len(OVERLAY_COLORS)]
            if selected_idx is None or i == selected_idx:
                gb.setStyleSheet(
                    f"QGroupBox {{ border: 1.5px solid {color_hex}; border-radius: 5px; "
                    f"margin-top: 6px; padding-top: 4px; }}")
                gb.setGraphicsEffect(None)
            else:
                gb.setStyleSheet(
                    f"QGroupBox {{ border: 1.5px solid {color_hex}; border-radius: 5px; "
                    f"margin-top: 6px; padding-top: 4px; opacity: 0.2; }}")
                effect = QtWidgets.QGraphicsOpacityEffect()
                effect.setOpacity(0.2)
                gb.setGraphicsEffect(effect)

    def _on_plot_background_click(self, event):
        if event.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        if self._scatter_click_handled:
            self._scatter_click_handled = False
            return
        self._click_cycle      = []
        self._click_cycle_pos  = None
        self._selected_cluster = None
        self._apply_opacity(None)

    def closeEvent(self, event):
        _a = _get_app()
        _a.settings.setValue("cluster_spacings",  self._spacing_edit.text())
        _a.settings.setValue("cluster_tol",       self._tol_spin.value())
        _a.settings.setValue("cluster_min_chain", self._chain_spin.value())
        _a.settings.setValue("cluster_snr",       self._snr_spin.value())
        _a.settings.setValue("cluster_pct",       self._pct_spin.value())
        try:
            _a.plot.scene().sigMouseClicked.disconnect(self._on_plot_background_click)
        except Exception:
            pass
        _a._clear_cluster_scatter()
        super().closeEvent(event)
