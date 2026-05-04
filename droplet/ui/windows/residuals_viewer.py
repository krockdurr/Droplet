"""ResidualsViewerWindow - browse a residuals folder and visualise Δm/z data."""

import os
import glob

import numpy as np
import pandas as pd
import pyqtgraph as pg

try:
    from PyQt6 import QtWidgets, QtCore
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore


class ResidualsViewerWindow(QtWidgets.QDialog):
    """
    Browse a Residuals folder and display:
      • Left panel  – dense residuals spectrum (original m/z vs Δm/z curve)
      • Right panel – calibration anchor scatter (per-peak displacement bar chart)
    """

    def __init__(self, res_folder=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Residuals Viewer")
        self.resize(1050, 600)
        self.setMinimumSize(700, 420)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QtWidgets.QHBoxLayout()
        self._folder_lbl = QtWidgets.QLabel("No folder loaded")
        self._folder_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        self._folder_lbl.setWordWrap(False)
        browse_btn = QtWidgets.QPushButton("Browse Residuals Folder…")
        browse_btn.clicked.connect(self._browse)
        top.addWidget(browse_btn)
        top.addWidget(self._folder_lbl, stretch=1)

        # ── File list ────────────────────────────────────────────────────────
        self._file_list = QtWidgets.QListWidget()
        self._file_list.setMinimumWidth(200)
        self._file_list.setMaximumWidth(280)
        self._file_list.currentRowChanged.connect(self._on_select)

        # ── Plot area (two pyqtgraph plots side by side) ─────────────────────
        self._pg_widget = pg.GraphicsLayoutWidget()

        self._curve_plot = self._pg_widget.addPlot(row=0, col=0,
                                                    title="Δm/z curve (dense)")
        self._curve_plot.setLabel('bottom', 'Original m/z')
        self._curve_plot.setLabel('left',   'Δ m/z')
        self._curve_plot.showGrid(x=True, y=True, alpha=0.3)
        self._curve_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                  style=QtCore.Qt.PenStyle.DashLine))

        self._bar_plot = self._pg_widget.addPlot(row=0, col=1,
                                                  title="Calibration anchors")
        self._bar_plot.setLabel('bottom', 'Original m/z')
        self._bar_plot.setLabel('left',   'Δ m/z')
        self._bar_plot.showGrid(x=True, y=True, alpha=0.3)
        self._bar_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                style=QtCore.Qt.PenStyle.DashLine))
        self._bar_plot.addLegend(offset=(10, 10))

        # ── Info label ───────────────────────────────────────────────────────
        self._info_lbl = QtWidgets.QLabel("")
        self._info_lbl.setStyleSheet("font-size: 9pt; color: gray;")

        # ── Layout ───────────────────────────────────────────────────────────
        left_col = QtWidgets.QVBoxLayout()
        left_col.addWidget(QtWidgets.QLabel("<b>Files in folder</b>"))
        left_col.addWidget(self._file_list)

        right_col = QtWidgets.QVBoxLayout()
        right_col.addLayout(top)
        right_col.addWidget(self._pg_widget, stretch=1)
        right_col.addWidget(self._info_lbl)

        body = QtWidgets.QHBoxLayout()
        body.addLayout(left_col)
        body.addLayout(right_col, stretch=1)

        main_lay = QtWidgets.QVBoxLayout(self)
        main_lay.addLayout(body)

        # ── Close button ─────────────────────────────────────────────────────
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(); btn_row.addWidget(close_btn)
        main_lay.addLayout(btn_row)

        self._res_folder = None
        self._file_stems = []   # parallel list: stem names for listed items

        if res_folder:
            self._load_folder(res_folder)

    # ── Folder loading ────────────────────────────────────────────────────────

    def _browse(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select a Residuals folder", "")
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder):
        self._res_folder = folder
        self._folder_lbl.setText(folder)
        self._file_list.clear()
        self._file_stems.clear()

        # Find all *_residuals_spectrum.txt files
        spectra = sorted(glob.glob(os.path.join(folder, "*_residuals_spectrum.txt")))
        for path in spectra:
            stem = os.path.basename(path).replace("_residuals_spectrum.txt", "")
            self._file_stems.append(stem)
            self._file_list.addItem(stem)

        if self._file_list.count() > 0:
            self._file_list.setCurrentRow(0)
        else:
            self._info_lbl.setText("No residuals files found in this folder.")

    # ── Selection / plotting ──────────────────────────────────────────────────

    def _on_select(self, row):
        if row < 0 or row >= len(self._file_stems):
            return
        stem = self._file_stems[row]
        spec_path  = os.path.join(self._res_folder, stem + "_residuals_spectrum.txt")
        table_path = os.path.join(self._res_folder, stem + "_residuals_table.csv")
        self._draw(spec_path, table_path, stem)

    def _draw(self, spec_path, table_path, stem):
        self._curve_plot.clear()
        self._bar_plot.clear()
        self._curve_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                  style=QtCore.Qt.PenStyle.DashLine))
        self._bar_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                style=QtCore.Qt.PenStyle.DashLine))

        # Palette for per-peak-list coloring
        _PL_COLORS = ['#4e9de0', '#e8944b', '#5cba6e', '#c46ee8',
                      '#e8c84b', '#4be8d8', '#e84b7e', '#a0a0a0']

        # ── Dense curve — split by peak_list column if present ───────────────
        if os.path.isfile(spec_path):
            try:
                spec_df = pd.read_csv(spec_path, sep='\t', comment='#',
                                      header=None, names=['mz', 'delta', 'peak_list'])
                spec_df = spec_df.sort_values('mz')
                spec_df['peak_list'] = spec_df['peak_list'].fillna('').astype(str).str.strip()
                pl_groups = spec_df.groupby('peak_list', sort=False)
                for ci, (pl_name, grp) in enumerate(pl_groups):
                    color = _PL_COLORS[ci % len(_PL_COLORS)]
                    label = pl_name if pl_name else f"Series {ci+1}"
                    self._curve_plot.plot(
                        grp['mz'].values.astype(float),
                        grp['delta'].values.astype(float),
                        pen=pg.mkPen(color, width=1.5),
                        name=label)
            except Exception as e:
                self._info_lbl.setText(f"Could not read spectrum file: {e}")

        # ── Anchor bar chart — colored by peak_list ───────────────────────────
        if os.path.isfile(table_path):
            try:
                tbl  = pd.read_csv(table_path)
                cols = list(tbl.columns)
                if len(cols) >= 3:
                    orig   = tbl[cols[0]].values.astype(float)
                    delta  = tbl[cols[2]].values.astype(float)
                    pl_col = tbl["peak_list"].fillna('').astype(str).str.strip() \
                             if "peak_list" in tbl.columns \
                             else pd.Series([''] * len(orig))
                    pl_names  = list(dict.fromkeys(pl_col))  # ordered unique
                    pl_ci_map = {n: i for i, n in enumerate(pl_names)}
                    for x, d, pl in zip(orig, delta, pl_col):
                        ci    = pl_ci_map.get(pl, 0)
                        color = _PL_COLORS[ci % len(_PL_COLORS)]
                        bar   = pg.PlotDataItem([x, x], [0, d],
                                               pen=pg.mkPen(color, width=3))
                        self._bar_plot.addItem(bar)
                    _legend_added = set()
                    for x, d, pl in zip(orig, delta, pl_col):
                        ci    = pl_ci_map.get(pl, 0)
                        color = _PL_COLORS[ci % len(_PL_COLORS)]
                        legend_label = pl if pl else f"Series {ci+1}"
                        sc = pg.ScatterPlotItem(x=[x], y=[d], size=10,
                                               pen=pg.mkPen('w', width=0.5),
                                               brush=pg.mkBrush(color),
                                               name=legend_label if legend_label not in _legend_added else None)
                        self._bar_plot.addItem(sc)
                        lbl = pg.TextItem(f"Δ{d:+.2f}",
                                          anchor=(0.5, 1.0), color='w')
                        lbl.setPos(x, d)
                        self._bar_plot.addItem(lbl)
                        if legend_label not in _legend_added:
                            _legend_added.add(legend_label)
                    n_pts = len(orig)
                    rms   = float(np.sqrt(np.mean(delta**2)))
                    max_d = float(np.max(np.abs(delta)))
                    self._info_lbl.setText(
                        f"{stem}  —  {n_pts} anchor(s)  |  "
                        f"RMS Δ = {rms:.4f}  |  max |Δ| = {max_d:.4f}")
            except Exception as e:
                self._info_lbl.setText(f"Could not read table file: {e}")
