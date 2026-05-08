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
from droplet.ui.mixins import StayOnTopMixin


def _get_app():
    import droplet.app as _m
    return _m


class ClusterDetectionWindow(QtWidgets.QWidget, StayOnTopMixin):
    """Non-modal window: parameter controls on top, results grouped by cluster below."""

    def __init__(self, parent=None):
        super().__init__(parent, QtCore.Qt.WindowType.Window)
        self.setWindowTitle("Cluster Detection")
        self.resize(720, 580)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)

        _a = _get_app()

        self._clusters                 = []
        self._inc_chks                 = []   # kept for _add_to_peak_lists compat
        self._peak_included            = {}   # {ci: {pi: bool}}
        self._results_table            = None
        self._scatter_items_by_cluster = []
        self._group_boxes              = []   # kept for _apply_opacity compat
        self._selected_cluster         = None
        self._scatter_click_handled    = False
        self._click_cycle              = []
        self._click_cycle_pos          = None

        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(6)
        root.setContentsMargins(8, 8, 8, 8)

        _mbar = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar, _a.settings)
        root.setMenuBar(_mbar)

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
        pl_outer = QtWidgets.QVBoxLayout(self._pl_box)
        pl_outer.setContentsMargins(4, 4, 4, 4)
        pl_outer.setSpacing(2)

        # ── Compact table: one row per peak list, peaks as toggle columns ──
        self._pl_checks      = []
        self._pl_peak_checks = {}

        rows_data = []
        for row in _a.custom_peak_rows:
            peaks = parse_peaks_text(row["peaks_input"].text())
            if not peaks:
                continue
            rows_data.append((row, peaks))

        if rows_data:
            max_pl_peaks = max(len(p) for _, p in rows_data)
            pl_table = QtWidgets.QTableWidget(len(rows_data), 2 + max_pl_peaks)
            pl_table.setHorizontalHeaderLabels(
                ["", "Peak list"] + [""] * max_pl_peaks)
            pl_table.horizontalHeader().setSectionResizeMode(
                0, QtWidgets.QHeaderView.ResizeMode.Fixed)
            pl_table.setColumnWidth(0, 24)
            pl_table.horizontalHeader().setSectionResizeMode(
                1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            for c in range(2, 2 + max_pl_peaks):
                pl_table.horizontalHeader().setSectionResizeMode(
                    c, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
            pl_table.verticalHeader().setVisible(False)
            pl_table.setShowGrid(True)
            pl_table.setAlternatingRowColors(True)
            pl_table.setEditTriggers(
                QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            pl_table.setSelectionMode(
                QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
            pl_table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            ROW_H_PL = 24
            pl_table.setMaximumHeight(
                pl_table.horizontalHeader().sizeHint().height() +
                min(5, len(rows_data)) * ROW_H_PL + 6)

            for ri, (row, peaks) in enumerate(rows_data):
                pl_table.setRowHeight(ri, ROW_H_PL)
                color = row["color"][0]
                hex_c = color.name() if hasattr(color, "name") else str(color)
                lbl   = row["label_input"].text().strip() or "Unnamed"

                # Col 0: color swatch
                swatch_item = QtWidgets.QTableWidgetItem()
                swatch_item.setBackground(
                    QtGui.QBrush(QtGui.QColor(hex_c)))
                pl_table.setItem(ri, 0, swatch_item)

                # Col 1: row-level include checkbox + label
                row_cb = QtWidgets.QCheckBox(lbl)
                row_cb.setChecked(False)
                row_cb.setStyleSheet("font-weight: bold; padding-left: 2px;")
                pl_table.setCellWidget(ri, 1, row_cb)

                # Col 2+: individual peak toggle buttons
                peak_checks = []
                for pi, mz in enumerate(peaks):
                    pk_cb = QtWidgets.QCheckBox(f"{mz:.4g}")
                    pk_cb.setChecked(True)
                    pk_cb.setToolTip(f"Include m/z {mz:.4g} in detection")
                    cell_w = QtWidgets.QWidget()
                    cell_l = QtWidgets.QHBoxLayout(cell_w)
                    cell_l.addWidget(pk_cb)
                    cell_l.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                    cell_l.setContentsMargins(2, 0, 2, 0)
                    pl_table.setCellWidget(ri, 2 + pi, cell_w)
                    peak_checks.append((pk_cb, mz))

                self._pl_checks.append((row_cb, row))
                self._pl_peak_checks[id(row)] = peak_checks

            pl_outer.addWidget(pl_table)
        else:
            pl_outer.addWidget(QtWidgets.QLabel("(no peak lists with peaks defined)"))

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

    def showEvent(self, event):
        super().showEvent(event)
        screen_h = QtWidgets.QApplication.primaryScreen().availableGeometry().height()
        self.setMaximumHeight(screen_h - 60)

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

    # unicode approximations for pyqtgraph symbol codes
    _SYM_UNICODE = {
        'o': '●', 's': '■', 't': '▲', 'd': '◆',
        'star': '★', 'p': '⬠', 'h': '⬡',
        't2': '▶', 't3': '◀', 'x': '✕',
    }

    def _rebuild_results(self):
        _a = _get_app()
        _a._clear_cluster_scatter()
        self._scatter_items_by_cluster.clear()
        self._group_boxes.clear()
        self._selected_cluster = None
        self._peak_included.clear()
        self._inc_chks.clear()
        self._results_table = None

        while self._results_layout.count():
            item = self._results_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not self._clusters:
            self._status_lbl.setText(
                "No clusters found. Try lowering Min intensity % or Min chain length.")
            self._add_btn.setEnabled(False)
            self._results_layout.addStretch()
            return

        self._status_lbl.setText(
            f"{len(self._clusters)} cluster(s) detected.  "
            "Click a peak cell to exclude it.  ✦ = overlaps a known peak list entry.")
        self._add_btn.setEnabled(True)

        max_peaks = max(len(c['members']) for c in self._clusters)

        # ── Single table: one row per cluster ─────────────────────────────
        table = QtWidgets.QTableWidget(len(self._clusters), 2 + max_peaks)
        self._results_table = table

        # Column headers
        table.setHorizontalHeaderLabels(
            ["", "Cluster"] + [f"#{i+1}" for i in range(max_peaks)])
        table.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.Fixed)
        table.setColumnWidth(0, 34)
        table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        for c in range(2, 2 + max_peaks):
            table.horizontalHeader().setSectionResizeMode(
                c, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)

        table.verticalHeader().setVisible(False)
        table.setShowGrid(True)
        table.setAlternatingRowColors(False)
        table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        table.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        table.setHorizontalScrollMode(
            QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)

        ROW_H = 28
        for ri, cluster in enumerate(self._clusters):
            table.setRowHeight(ri, ROW_H)
            color_hex = OVERLAY_COLORS[ri % len(OVERLAY_COLORS)]
            symbol    = CLUSTER_SYMBOLS[ri % len(CLUSTER_SYMBOLS)]
            spacing   = cluster['spacing']
            members   = cluster['members']

            self._peak_included[ri] = {pi: True for pi in range(len(members))}

            # Col 0: coloured swatch with unicode symbol
            sym_char = self._SYM_UNICODE.get(symbol, '●')
            swatch_lbl = QtWidgets.QLabel(sym_char)
            swatch_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            swatch_lbl.setStyleSheet(
                f"color: {color_hex}; font-size: 16px; font-weight: bold;"
                f"background-color: {QtGui.QColor(color_hex).lighter(185).name()};"
                f"border: 1px solid {color_hex}; border-radius: 3px;")
            swatch_lbl.setToolTip(f"Cluster {ri+1} — click to highlight on plot")
            swatch_lbl.mousePressEvent = (
                lambda e, i=ri: self._select_cluster_direct(i))
            table.setCellWidget(ri, 0, swatch_lbl)

            # Col 1: cluster info
            info_item = QtWidgets.QTableWidgetItem(
                f"Cluster {ri+1}   Δ = {spacing:.3f} Da   ({len(members)} peaks)")
            info_item.setForeground(QtGui.QBrush(QtGui.QColor(color_hex)))
            info_item.setFont(QtGui.QFont("", -1, QtGui.QFont.Weight.Bold))
            info_item.setToolTip("Click to highlight this cluster on the plot")
            table.setItem(ri, 1, info_item)

            # Col 2+: individual peak cells (click to toggle inclusion)
            for pi, (mz, intensity, is_known) in enumerate(members):
                star = "✦ " if is_known else ""
                cell = QtWidgets.QTableWidgetItem(f"{star}{mz:.4f}")
                cell.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                tt = f"m/z {mz:.4f}\nIntensity: {intensity:.3g}"
                if is_known:
                    tt += "\n✦ Overlaps a known peak list entry"
                cell.setToolTip(tt)
                # Store state in the item
                cell.setData(QtCore.Qt.ItemDataRole.UserRole,
                             {"ci": ri, "pi": pi, "mz": mz,
                              "color": color_hex, "is_known": is_known})
                self._style_peak_cell(cell, included=True,
                                      color_hex=color_hex, is_known=is_known)
                table.setItem(ri, 2 + pi, cell)

            # Scatter on main plot
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
            self._group_boxes.append(None)   # keep list aligned with cluster indices
            scatter.sigClicked.connect(
                lambda item, pts, ev, i=ri: self._select_cluster(i, clicked_pts=pts))

        def _on_cell_clicked(row, col):
            if col <= 1:
                # Click on swatch or info → select/highlight cluster
                self._select_cluster_direct(row)
                return
            cell = table.item(row, col)
            if cell is None:
                return
            data = cell.data(QtCore.Qt.ItemDataRole.UserRole)
            if data is None:
                return
            ci, pi = data["ci"], data["pi"]
            new_state = not self._peak_included[ci][pi]
            self._peak_included[ci][pi] = new_state
            self._style_peak_cell(cell, included=new_state,
                                  color_hex=data["color"],
                                  is_known=data["is_known"])

        table.cellClicked.connect(_on_cell_clicked)
        self._results_layout.addWidget(table)

        try:
            _a.plot.scene().sigMouseClicked.disconnect(self._on_plot_background_click)
        except Exception:
            pass
        _a.plot.scene().sigMouseClicked.connect(self._on_plot_background_click)

    @staticmethod
    def _style_peak_cell(cell, *, included: bool, color_hex: str, is_known: bool):
        """Apply visual state (included / excluded) to a peak QTableWidgetItem."""
        if included:
            bg = QtGui.QColor(color_hex).lighter(185)
            fg = QtGui.QColor("#f0a000") if is_known else QtGui.QColor("#111111")
            cell.setBackground(QtGui.QBrush(bg))
            cell.setForeground(QtGui.QBrush(fg))
            font = cell.font(); font.setStrikeOut(False); cell.setFont(font)
        else:
            cell.setBackground(QtGui.QBrush(QtGui.QColor("#d8d8d8")))
            cell.setForeground(QtGui.QBrush(QtGui.QColor("#aaaaaa")))
            font = cell.font(); font.setStrikeOut(True); cell.setFont(font)

    def _add_to_peak_lists(self):
        _a = _get_app()
        added = 0
        for ci, cluster in enumerate(self._clusters):
            members   = cluster['members']
            inclusion = self._peak_included.get(ci, {})
            included_mz = [
                mz for pi, (mz, intensity, is_known) in enumerate(members)
                if inclusion.get(pi, True)
            ]
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
            self._status_lbl.setText("Nothing to add — all peaks are excluded.")

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

        # ── Scatter items on the plot ──────────────────────────────────
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

        # ── Table rows ─────────────────────────────────────────────────
        t = self._results_table
        if t is None:
            return
        for ri in range(t.rowCount()):
            dim = (selected_idx is not None and ri != selected_idx)
            opacity = 0.25 if dim else 1.0
            effect = QtWidgets.QGraphicsOpacityEffect()
            effect.setOpacity(opacity)
            # Apply to the row's cell widget (swatch) and items
            sw = t.cellWidget(ri, 0)
            if sw:
                sw.setGraphicsEffect(
                    QtWidgets.QGraphicsOpacityEffect() if not dim else effect)
                sw.graphicsEffect().setOpacity(opacity) if sw.graphicsEffect() else None
            for ci in range(1, t.columnCount()):
                item = t.item(ri, ci)
                if item:
                    alpha = 60 if dim else 255
                    fg = item.foreground().color()
                    fg.setAlpha(alpha)
                    item.setForeground(QtGui.QBrush(fg))

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
