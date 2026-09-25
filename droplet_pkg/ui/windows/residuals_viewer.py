"""ResidualsViewerWindow - browse a residuals folder and visualise Δm/z data."""

import os
import glob

import numpy as np
import pandas as pd
import pyqtgraph as pg

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui.mixins import StayOnTopMixin

_PL_COLORS = ['#4e9de0', '#e8944b', '#5cba6e', '#c46ee8',
              '#e8c84b', '#4be8d8', '#e84b7e', '#a0a0a0']

DIM_OPACITY  = 0.12
FULL_OPACITY = 1.0


class _LegendEntry(QtWidgets.QWidget):
    """Hoverable swatch + label for one peak-list entry in the custom legend."""

    def __init__(self, pl_name, color, on_enter, on_leave, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(5)

        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(
            f"background:{color}; border-radius:2px; border:1px solid rgba(0,0,0,40);")
        lbl = QtWidgets.QLabel(pl_name or "(unnamed)")
        lbl.setStyleSheet("font-size: 9pt;")

        lay.addWidget(swatch)
        lay.addWidget(lbl)

        self._on_enter = on_enter
        self._on_leave = on_leave
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self._on_enter()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._on_leave()
        super().leaveEvent(event)


class ResidualsViewerWindow(QtWidgets.QDialog, StayOnTopMixin):
    """
    Browse a Residuals folder and display:
      • Left panel  – dense residuals spectrum (original m/z vs Δm/z curve)
      • Right panel – calibration anchor scatter (per-peak displacement bar chart)

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
        self.setWindowTitle(
            (f"Residuals Preview — {preview_stem}" if preview_stem else "Residuals Preview")
            if self._preview_mode else "Residuals Viewer")
        self.resize(1050, 600)
        self.setMinimumSize(700, 420)

        # Allow the window to be maximised / snapped to screen edges
        self.setWindowFlags(
            self.windowFlags()
            | QtCore.Qt.WindowType.WindowMaximizeButtonHint
            | QtCore.Qt.WindowType.WindowMinimizeButtonHint
        )

        # ── Top bar ──────────────────────────────────────────────────────────
        top = QtWidgets.QHBoxLayout()
        self._folder_lbl = QtWidgets.QLabel("No folder loaded")
        self._folder_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        self._folder_lbl.setWordWrap(False)
        browse_btn = QtWidgets.QPushButton("Browse Residuals Folder…")
        browse_btn.clicked.connect(self._browse)
        top.addWidget(browse_btn)
        top.addWidget(self._folder_lbl, stretch=1)
        if self._preview_mode:
            browse_btn.setVisible(False)
            self._folder_lbl.setVisible(False)

        # ── File list ────────────────────────────────────────────────────────
        self._file_list = QtWidgets.QListWidget()
        self._file_list.setMinimumWidth(200)
        self._file_list.setMaximumWidth(280)
        self._file_list.currentRowChanged.connect(self._on_select)

        # Shown instead of the file list for an individual manual recalibration
        self._no_files_lbl = QtWidgets.QLabel(
            "No other files can be displayed, as this is an individual "
            "manual recalibration.")
        self._no_files_lbl.setWordWrap(True)
        self._no_files_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._no_files_lbl.setStyleSheet("color: gray; font-size: 9pt; padding: 8px;")
        self._no_files_lbl.setMinimumWidth(200)
        self._no_files_lbl.setMaximumWidth(280)
        self._no_files_lbl.setVisible(False)

        # ── Plot area (two pyqtgraph plots side by side) ─────────────────────
        self._pg_widget = pg.GraphicsLayoutWidget()

        self._curve_plot = self._pg_widget.addPlot(row=0, col=0,
                                                    title="Δm/z curve (dense)")
        self._curve_plot.setLabel('bottom', 'Original m/z')
        self._curve_plot.setLabel('left',   'Δ m/z')
        self._curve_plot.showGrid(x=True, y=True, alpha=0.3)
        self._curve_plot.getAxis('left').enableAutoSIPrefix(False)
        self._curve_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                  style=QtCore.Qt.PenStyle.DashLine))

        self._bar_plot = self._pg_widget.addPlot(row=0, col=1,
                                                  title="Calibration anchors")
        self._bar_plot.setLabel('bottom', 'Original m/z')
        self._bar_plot.setLabel('left',   'Δ m/z')
        self._bar_plot.showGrid(x=True, y=True, alpha=0.3)
        self._bar_plot.getAxis('left').enableAutoSIPrefix(False)
        self._bar_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                style=QtCore.Qt.PenStyle.DashLine))

        # Hover label anchored to the top-right of the anchor plot
        self._bar_hover_lbl = pg.TextItem("", anchor=(1, 0), color='w')
        self._bar_hover_lbl.setParentItem(self._bar_plot.getViewBox())
        self._bar_anchor_pts: list = []   # (x, y, pl_name) for proximity detection
        self._bar_mouse_proxy = pg.SignalProxy(
            self._bar_plot.scene().sigMouseMoved,
            rateLimit=30, slot=self._on_bar_mouse_move)
        self._bar_plot.scene().sigMouseClicked.connect(self._on_scene_clicked)

        # ── Custom shared legend (replaces pyqtgraph built-in) ───────────────
        self._legend_container = QtWidgets.QWidget()
        self._legend_layout    = QtWidgets.QVBoxLayout(self._legend_container)
        self._legend_layout.setContentsMargins(4, 2, 4, 2)
        self._legend_layout.setSpacing(2)

        # ── Info label ───────────────────────────────────────────────────────
        self._info_lbl = QtWidgets.QLabel("")
        self._info_lbl.setStyleSheet("font-size: 9pt; color: gray;")

        # ── Layout ───────────────────────────────────────────────────────────
        left_col = QtWidgets.QVBoxLayout()
        left_col.addWidget(QtWidgets.QLabel(
            "<b>This batch</b>" if self._preview_mode else "<b>Files (incl. subfolders)</b>"))
        left_col.addWidget(self._file_list)
        left_col.addWidget(self._no_files_lbl, stretch=1)

        right_col = QtWidgets.QVBoxLayout()
        right_col.addLayout(top)
        right_col.addWidget(self._pg_widget, stretch=1)
        right_col.addWidget(self._legend_container)
        right_col.addWidget(self._info_lbl)

        body = QtWidgets.QHBoxLayout()
        body.addLayout(left_col)
        body.addLayout(right_col, stretch=1)

        main_lay = QtWidgets.QVBoxLayout(self)

        _mbar = QtWidgets.QMenuBar()
        self._install_stay_on_top(_mbar)
        main_lay.setMenuBar(_mbar)

        main_lay.addLayout(body)

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        if self._preview_mode:
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
            btn_row.addWidget(confirm_btn)
            btn_row.addWidget(cancel_btn)
        else:
            close_btn = QtWidgets.QPushButton("Close")
            close_btn.clicked.connect(self.close)
            btn_row.addWidget(close_btn)
        main_lay.addLayout(btn_row)

        self._res_folder   = None
        self._file_entries = []   # list of (display_name, spec_path, table_path)
        self._items_by_pl  = {}   # pl_name → list of pyqtgraph items (both plots)

        # Preview-mode state
        self._preview_df     = preview_df
        self._preview_stem   = preview_stem
        self._preview_colors = preview_colors
        self._showing_live   = False

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
        elif res_folder:
            self._load_folder(res_folder)

    # ── Preview mode (manual recalibration) ───────────────────────────────────

    def _load_preview_list(self, batch_files):
        """Fill the file list with the live entry + residuals saved in this batch."""
        self._file_list.clear()
        self._file_entries.clear()

        if batch_files is None:
            self._file_list.setVisible(False)
            self._no_files_lbl.setVisible(True)
            self._draw_live()
            return

        self._file_entries.append(None)   # None = live (unsaved) entry
        self._file_list.addItem(f"● {self._preview_stem or 'Current'}  (unsaved)")

        saved = [p for p in batch_files if os.path.isfile(p)]
        if saved:
            sep_item = QtWidgets.QListWidgetItem("📁 Saved in this batch")
            sep_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            sep_item.setForeground(
                self._file_list.palette().color(
                    self._file_list.foregroundRole()).lighter(140))
            self._file_list.addItem(sep_item)
            for spec_path in reversed(saved):   # most recent first
                stem = os.path.basename(spec_path).replace("_residuals_spectrum.txt", "")
                table_path = os.path.join(os.path.dirname(spec_path),
                                          stem + "_residuals_table.csv")
                self._file_entries.append((stem, spec_path, table_path))
                self._file_list.addItem(stem)
        else:
            empty_item = QtWidgets.QListWidgetItem("No residuals saved yet in this batch.")
            empty_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            empty_item.setForeground(
                self._file_list.palette().color(
                    self._file_list.foregroundRole()).lighter(140))
            self._file_list.addItem(empty_item)

        self._file_list.setCurrentRow(0)

    def update_data(self, summary_df, color_map=None):
        """Refresh the live preview without closing the window."""
        self._preview_df     = summary_df
        self._preview_colors = color_map
        if self._file_list.isVisible() and self._file_list.currentRow() != 0:
            self._file_list.setCurrentRow(0)   # triggers _draw_live
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
        self._render(spec_df, df, color_map, self._preview_stem or "Current")

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
            self, "Select a Residuals folder", "")
        if folder:
            self._load_folder(folder)

    def _load_folder(self, folder):
        self._res_folder = folder
        self._folder_lbl.setText(folder)
        self._file_list.clear()
        self._file_entries.clear()

        # Recursive search for all *_residuals_spectrum.txt files
        spectra = sorted(
            glob.glob(os.path.join(folder, "**", "*_residuals_spectrum.txt"), recursive=True),
            key=lambda p: (os.path.dirname(p), os.path.basename(p))
        )

        current_subdir = None
        for spec_path in spectra:
            subdir     = os.path.relpath(os.path.dirname(spec_path), folder)
            stem       = os.path.basename(spec_path).replace("_residuals_spectrum.txt", "")
            table_path = os.path.join(os.path.dirname(spec_path),
                                      stem + "_residuals_table.csv")
            display    = stem if subdir == "." else f"{stem}  [{subdir}]"

            if subdir != current_subdir:
                current_subdir = subdir
                sep_text = "📁 (root)" if subdir == "." else f"📁 {subdir}"
                sep_item = QtWidgets.QListWidgetItem(sep_text)
                sep_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
                sep_item.setForeground(
                    self._file_list.palette().color(
                        self._file_list.foregroundRole()).lighter(140))
                self._file_list.addItem(sep_item)

            self._file_entries.append((display, spec_path, table_path))
            self._file_list.addItem(display)

        if self._file_entries:
            for i in range(self._file_list.count()):
                item = self._file_list.item(i)
                if item.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable:
                    self._file_list.setCurrentItem(item)
                    break
        else:
            self._info_lbl.setText("No residuals files found in this folder or its subfolders.")

    # ── Selection / plotting ──────────────────────────────────────────────────

    def _on_select(self, row):
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
        stem = os.path.basename(spec_path).replace("_residuals_spectrum.txt", "")
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
        self._render(spec_df, tbl, color_map, stem)
        if errors:
            self._info_lbl.setText("  |  ".join(errors))

    def _render(self, spec_df, tbl, color_map, stem):
        """Draw a dense curve (spec_df: mz/delta/peak_list) and anchor table."""
        self._curve_plot.clear()
        self._bar_plot.clear()
        self._curve_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                  style=QtCore.Qt.PenStyle.DashLine))
        self._bar_plot.addLine(y=0, pen=pg.mkPen('r', width=1,
                                style=QtCore.Qt.PenStyle.DashLine))

        # Re-add hover label (cleared by bar_plot.clear())
        self._bar_hover_lbl = pg.TextItem("", anchor=(1, 0), color='w')
        self._bar_hover_lbl.setParentItem(self._bar_plot.getViewBox())
        self._bar_anchor_pts = []
        self._info_lbl.setText("")

        self._items_by_pl = {}

        def _track(pl_name, item):
            self._items_by_pl.setdefault(pl_name, []).append(item)

        # ── Dense curve ───────────────────────────────────────────────────────
        if spec_df is not None:
            try:
                spec_df = spec_df.sort_values('mz')
                spec_df['peak_list'] = spec_df['peak_list'].fillna('').astype(str).str.strip()
                for pl_name, grp in spec_df.groupby('peak_list', sort=False):
                    color = color_map.get(pl_name, '#a0a0a0')
                    curve = self._curve_plot.plot(
                        grp['mz'].values.astype(float),
                        grp['delta'].values.astype(float),
                        pen=pg.mkPen(color, width=1.5))
                    _track(pl_name, curve)
            except Exception as e:
                self._info_lbl.setText(f"Could not plot spectrum data: {e}")

        # ── Anchor bar chart ──────────────────────────────────────────────────
        if tbl is not None:
            try:
                cols = list(tbl.columns)
                if len(cols) >= 3:
                    orig   = tbl[cols[0]].values.astype(float)
                    delta  = tbl[cols[2]].values.astype(float)
                    pl_col = (tbl["peak_list"].fillna('').astype(str).str.strip()
                              if "peak_list" in tbl.columns
                              else pd.Series([''] * len(orig)))

                    for x, d, pl in zip(orig, delta, pl_col):
                        color = color_map.get(pl, '#a0a0a0')
                        bar   = pg.PlotDataItem([x, x], [0, d],
                                               pen=pg.mkPen(color, width=3))
                        self._bar_plot.addItem(bar)
                        _track(pl, bar)

                    for row_idx, (x, d, pl) in enumerate(zip(orig, delta, pl_col)):
                        color = color_map.get(pl, '#a0a0a0')
                        sc = pg.ScatterPlotItem(x=[x], y=[d], size=10,
                                               pen=pg.mkPen('w', width=0.5),
                                               brush=pg.mkBrush(color))
                        self._bar_plot.addItem(sc)
                        _track(pl, sc)
                        self._bar_anchor_pts.append((x, d, pl, row_idx))

                        lbl = pg.TextItem(f"Δ{d:+.2f}", anchor=(0.5, 1.0), color='w')
                        lbl.setPos(x, d)
                        self._bar_plot.addItem(lbl)
                        _track(pl, lbl)

                    n_pts = len(orig)
                    rms   = float(np.sqrt(np.mean(delta**2))) if n_pts else 0.0
                    max_d = float(np.max(np.abs(delta))) if n_pts else 0.0
                    self._info_lbl.setText(
                        f"{stem}  —  {n_pts} anchor(s)  |  "
                        f"RMS Δ = {rms:.4f}  |  max |Δ| = {max_d:.4f}")
            except Exception as e:
                self._info_lbl.setText(f"Could not plot table data: {e}")

        self._rebuild_legend(color_map)

    # ── Legend ────────────────────────────────────────────────────────────────

    def _rebuild_legend(self, color_map):
        # Clear previous rows (each child is a QWidget row)
        while self._legend_layout.count():
            item = self._legend_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        entries = list(color_map.items())
        ROW_SIZE = 10
        for row_start in range(0, max(len(entries), 1), ROW_SIZE):
            row_entries = entries[row_start:row_start + ROW_SIZE]
            if not row_entries:
                break
            row_widget = QtWidgets.QWidget()
            row_layout = QtWidgets.QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            for pl_name, color in row_entries:
                entry = _LegendEntry(
                    pl_name or "(unnamed)", color,
                    on_enter=lambda n=pl_name: self._dim_others(n),
                    on_leave=self._restore_all,
                )
                row_layout.addWidget(entry)
            row_layout.addStretch()
            self._legend_layout.addWidget(row_widget)

    # ── Anchor hover label ────────────────────────────────────────────────────

    def _nearest_anchor(self, scene_pos):
        """Return (row_idx, pl_name) of the nearest anchor within threshold, or (-1, '')."""
        vb = self._bar_plot.getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return -1, ""
        mp = vb.mapSceneToView(scene_pos)
        vr = vb.viewRange()
        x_span = abs(vr[0][1] - vr[0][0]) or 1.0
        y_span = abs(vr[1][1] - vr[1][0]) or 1.0
        threshold = 0.05
        best_idx, best_name, best_dist = -1, "", float("inf")
        for ax, ay, apl, ridx in self._bar_anchor_pts:
            dx = (mp.x() - ax) / x_span
            dy = (mp.y() - ay) / y_span
            dist = (dx*dx + dy*dy) ** 0.5
            if dist < threshold and dist < best_dist:
                best_dist = dist
                best_idx  = ridx
                best_name = apl
        return best_idx, best_name

    def _nearest_anchor_idx(self, scene_pos):
        return self._nearest_anchor(scene_pos)[0]

    def _on_bar_mouse_move(self, evt):
        pos = evt[0]
        vb  = self._bar_plot.getViewBox()
        _, name = self._nearest_anchor(pos)
        self._bar_hover_lbl.setText(name or "")
        if name:
            vr = vb.viewRange()
            self._bar_hover_lbl.setPos(vr[0][1], vr[1][1])

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
