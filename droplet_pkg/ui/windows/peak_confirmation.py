"""Peak list confirmation side panel.

Provides a spreadsheet-style panel attached to the Peaks window where each
column represents a sample file and each row represents a peak-list entry.
Cells can be toggled to mark whether a given peak list is confirmed in a
given sample.

Public helpers used by app.py:
    extract_sample_info(filepath)        -> (sample_name, dt_str, header_str)
    make_row_key(row_dict)               -> stable str key for a peak-row dict
    _row_dict_from_widget_row(row_data)  -> serialisable dict from live row
    ConfirmationPanel                    -> the QWidget side panel
"""

import os
import re
import json

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
    _QT6 = True
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui
    _QT6 = False


# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def extract_sample_info(filepath):
    """Return (sample_name, dt_str, header_str) from a LILBID filename.

    Expected pattern: YYYY-MM-DD-HHMMSS_SampleName_..._dtXXX_...ext
    Falls back gracefully when the pattern is not matched.
    """
    stem = os.path.splitext(os.path.basename(filepath))[0]
    m = re.match(r'^\d{4}-\d{2}-\d{2}-\d{6}_([^_]+)', stem)
    sample_name = m.group(1) if m else stem.split('_')[0]
    dt_m = re.search(r'_dt(\d+)', stem)
    dt_str = dt_m.group(1) if dt_m else ""
    header = f"{sample_name}_dt{dt_str}" if dt_str else sample_name
    return sample_name, dt_str, header


def make_row_key(row_dict):
    """Return a stable string key for a peak-row dict."""
    label = row_dict.get("label", "").strip()
    if row_dict.get("mode") == "range":
        s  = row_dict.get("range_start", 0)
        st = row_dict.get("range_step", 1)
        e  = row_dict.get("range_end", 0)
        content = f"range:{s}:{st}:{e}"
    else:
        content = row_dict.get("peaks", "").strip()
    return f"{label}|{content}"


def _row_dict_from_widget_row(row_data):
    """Convert a live peak row (dict with Qt widgets) to a serialisable dict."""
    d = {
        "label":   row_data["label_input"].text().strip(),
        "checked": row_data["checkbox"].isChecked(),
        "color":   row_data["color"][0].name(),
    }
    if row_data.get("mode_btn") and row_data["mode_btn"].isChecked():
        d["mode"]        = "range"
        d["range_start"] = row_data["range_start"].value()
        d["range_step"]  = row_data["range_step"].value()
        d["range_end"]   = row_data["range_end"].value()
    else:
        d["mode"]  = "manual"
        d["peaks"] = row_data["peaks_input"].text().strip()
    return d


# ─────────────────────────────────────────────
#  Custom table with 500 ms hover signal
# ─────────────────────────────────────────────

class _ConfirmationTable(QtWidgets.QTableWidget):
    """QTableWidget that fires hover_started(row, col) after a short delay."""

    hover_started = QtCore.pyqtSignal(int, int)
    hover_ended   = QtCore.pyqtSignal()

    _DELAY_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover_timer = QtCore.QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(self._DELAY_MS)
        self._hover_timer.timeout.connect(self._fire_hover)
        self._pending = (-1, -1)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        item = self.itemAt(event.pos())
        if item is not None:
            rc = (item.row(), item.column())
            if rc != self._pending:
                self._pending = rc
                self._hover_timer.stop()
                self._hover_timer.start()
        else:
            self._cancel_hover()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._cancel_hover()

    def _cancel_hover(self):
        self._hover_timer.stop()
        if self._pending != (-1, -1):
            self._pending = (-1, -1)
            self.hover_ended.emit()

    def _fire_hover(self):
        r, c = self._pending
        if r >= 0 and c >= 0:
            self.hover_started.emit(r, c)


# ─────────────────────────────────────────────
#  Add-Column dialog
# ─────────────────────────────────────────────

class _AddColumnDialog(QtWidgets.QDialog):
    """Let the user pick one or more sample files to add as columns."""

    def __init__(self, available_files, existing_filenames, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Column(s)")
        self.resize(500, 340)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel("Select sample file(s) to add as column(s):"))
        self._list = QtWidgets.QListWidget()
        self._list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
                                    if _QT6 else
                                    QtWidgets.QAbstractItemView.ExtendedSelection)
        for path in available_files:
            if path in existing_filenames:
                continue
            _, _, header = extract_sample_info(path)
            item = QtWidgets.QListWidgetItem(header)
            item.setData(QtCore.Qt.ItemDataRole.UserRole if _QT6 else QtCore.Qt.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)
        layout.addWidget(self._list)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            if _QT6 else
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_files(self):
        role = QtCore.Qt.ItemDataRole.UserRole if _QT6 else QtCore.Qt.UserRole
        return [item.data(role) for item in self._list.selectedItems()]


# ─────────────────────────────────────────────
#  Main panel
# ─────────────────────────────────────────────

class ConfirmationPanel(QtWidgets.QWidget):
    """Collapsible side panel — 'Peak list confirmation'.

    Signals
    -------
    request_load_file(str)  — ask main app to load this filepath in the viewer
    request_focus_row(int)  — ask main app to visually focus this peak-row index
    request_unfocus()       — restore normal peak-row rendering
    """

    request_load_file = QtCore.pyqtSignal(str)
    request_focus_row = QtCore.pyqtSignal(int)
    request_unfocus   = QtCore.pyqtSignal()

    # ── Construction ────────────────────────────────────────────────────

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(200)
        self._data = {
            "neg": {"rows": [], "columns": []},
            "pos": {"rows": [], "columns": []},
        }
        self._current_mode   = "neg"
        self._dirty          = False
        self._peak_rows_cache = []
        self._get_available_files = None

        self._setup_ui()

    # ── Public API ──────────────────────────────────────────────────────

    def set_get_available_files(self, callback):
        """Register a callback () -> [filepath, ...] for the file picker."""
        self._get_available_files = callback

    def set_mode(self, mode):
        """Switch display to 'neg' or 'pos' data."""
        if mode not in ("neg", "pos") or mode == self._current_mode:
            return
        self._current_mode = mode
        self._refresh_table()

    def sync_with_peak_rows(self, peak_rows_dicts):
        """Called whenever the peak-list rows change.

        Inserts new rows at their correct positions (matching the peak-list
        order) and marks rows no longer present as orphans (shown in yellow).
        """
        self._peak_rows_cache = list(peak_rows_dicts)
        for mode in ("neg", "pos"):
            self._sync_mode_rows(mode, peak_rows_dicts)
        self._refresh_table()

    def check_dirty_before_mode_change(self, new_mode):
        """Return True if it is safe to switch polarity mode.

        Shows a Save/Discard/Cancel dialog if there is unsaved data.
        """
        if not self._dirty:
            return True
        reply = QtWidgets.QMessageBox.question(
            self,
            "Unsaved confirmation data",
            f"The confirmation data for '{self._current_mode}' mode has unsaved "
            "changes.\nExport before switching mode?",
            QtWidgets.QMessageBox.StandardButton.Save |
            QtWidgets.QMessageBox.StandardButton.Discard |
            QtWidgets.QMessageBox.StandardButton.Cancel
            if _QT6 else
            QtWidgets.QMessageBox.Save |
            QtWidgets.QMessageBox.Discard |
            QtWidgets.QMessageBox.Cancel,
        )
        Save    = QtWidgets.QMessageBox.StandardButton.Save    if _QT6 else QtWidgets.QMessageBox.Save
        Discard = QtWidgets.QMessageBox.StandardButton.Discard if _QT6 else QtWidgets.QMessageBox.Discard
        if reply == Save:
            self._do_export()
            return True
        if reply == Discard:
            return True
        return False

    def to_project_dict(self):
        """Serialise full state for .drp project saving."""
        return {
            "neg":          self._serialise_mode("neg"),
            "pos":          self._serialise_mode("pos"),
            "current_mode": self._current_mode,
        }

    def from_project_dict(self, d):
        """Restore state from .drp project data."""
        for mode in ("neg", "pos"):
            if mode in d:
                md = d[mode]
                self._data[mode] = {
                    "rows":    [dict(r) for r in md.get("rows", [])],
                    "columns": [
                        {"filename": c["filename"],
                         "header":   c.get("header", os.path.basename(c["filename"])),
                         "cells":    dict(c.get("cells", {}))}
                        for c in md.get("columns", [])
                    ],
                }
                for row in self._data[mode]["rows"]:
                    row.setdefault("orphan", False)
        self._current_mode = d.get("current_mode", "neg")
        self._refresh_table()
        self._set_dirty(False)

    # ── UI setup ────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Title bar
        title_row = QtWidgets.QHBoxLayout()
        title_lbl = QtWidgets.QLabel("Peak list confirmation")
        f = title_lbl.font(); f.setBold(True); title_lbl.setFont(f)
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        self._dirty_dot = QtWidgets.QLabel("●")
        self._dirty_dot.setStyleSheet("color: orange;")
        self._dirty_dot.setToolTip("Unsaved changes")
        self._dirty_dot.setVisible(False)
        title_row.addWidget(self._dirty_dot)
        outer.addLayout(title_row)

        # Toolbar — row 1: add/remove columns
        tb1 = QtWidgets.QHBoxLayout()
        tb1.setSpacing(3)
        self._add_col_btn = self._btn("+ Column", "Add a column for a specific sample")
        self._add_all_btn = self._btn("+ All",    "Add all filtered files as columns")
        self._rem_col_btn = self._btn("− Column", "Remove one or more existing columns")
        self._rem_all_btn = self._btn("− All",    "Remove all columns")
        for b in (self._add_col_btn, self._add_all_btn, self._rem_col_btn, self._rem_all_btn):
            tb1.addWidget(b)
        tb1.addStretch()
        outer.addLayout(tb1)

        # Toolbar — row 2: import/export/sync
        tb2 = QtWidgets.QHBoxLayout()
        tb2.setSpacing(3)
        self._import_btn    = self._btn("Import", "Import confirmation data from JSON")
        self._export_btn    = self._btn("Export", "Export current mode data to JSON")
        self._change_pl_btn = self._btn("⇄ Sync", "Re-sync rows with current peak list")
        for b in (self._import_btn, self._export_btn, self._change_pl_btn):
            tb2.addWidget(b)
        tb2.addStretch()
        outer.addLayout(tb2)

        # Table
        self._table = _ConfirmationTable()
        self._table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers if _QT6 else
            QtWidgets.QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionsMovable(False)
        self._table.verticalHeader().setSectionsMovable(False)
        self._table.horizontalHeader().setMinimumSectionSize(90)
        self._table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection if _QT6 else
            QtWidgets.QAbstractItemView.SingleSelection)
        outer.addWidget(self._table)

        # Connections
        self._add_col_btn.clicked.connect(self._on_add_column)
        self._add_all_btn.clicked.connect(self._on_add_all)
        self._rem_col_btn.clicked.connect(self._on_remove_column)
        self._rem_all_btn.clicked.connect(self._on_remove_all_columns)
        self._import_btn.clicked.connect(self._do_import)
        self._export_btn.clicked.connect(self._do_export)
        self._change_pl_btn.clicked.connect(self._on_change_peak_list)
        self._table.cellClicked.connect(self._on_cell_clicked)
        self._table.hover_started.connect(self._on_hover_started)
        self._table.hover_ended.connect(self.request_unfocus)

    @staticmethod
    def _btn(text, tip=""):
        b = QtWidgets.QPushButton(text)
        b.setFixedHeight(22)
        if tip:
            b.setToolTip(tip)
        return b

    # ── Table refresh ────────────────────────────────────────────────────

    def _refresh_table(self):
        mode_data = self._data[self._current_mode]
        rows    = mode_data["rows"]
        columns = mode_data["columns"]

        self._table.blockSignals(True)
        self._table.setRowCount(len(rows))
        self._table.setColumnCount(len(columns))

        _ORPHAN_HDR  = QtGui.QColor("#fff176")
        _ORPHAN_CELL = QtGui.QColor("#fff9c4")
        _UNSET_CELL  = QtGui.QColor("#f5f5f5")

        UserRole = QtCore.Qt.ItemDataRole.UserRole if _QT6 else QtCore.Qt.UserRole
        Checked   = QtCore.Qt.CheckState.Checked   if _QT6 else QtCore.Qt.Checked
        Unchecked = QtCore.Qt.CheckState.Unchecked if _QT6 else QtCore.Qt.Unchecked
        ItemFlags = (QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsUserCheckable
                     if _QT6 else
                     QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)

        # Vertical headers (row labels)
        for i, row in enumerate(rows):
            lbl  = row.get("label") or row["key"]
            item = QtWidgets.QTableWidgetItem(lbl)
            if row.get("orphan"):
                item.setBackground(_ORPHAN_HDR)
                item.setToolTip("Not in current peak list")
            self._table.setVerticalHeaderItem(i, item)

        # Horizontal headers (sample / column headers)
        for j, col in enumerate(columns):
            item = QtWidgets.QTableWidgetItem(col["header"])
            item.setToolTip(col["filename"])
            self._table.setHorizontalHeaderItem(j, item)

        # Cells
        for i, row in enumerate(rows):
            is_orphan = row.get("orphan", False)
            for j, col in enumerate(columns):
                val  = col["cells"].get(row["key"])
                item = QtWidgets.QTableWidgetItem()
                item.setFlags(ItemFlags)
                item.setCheckState(Checked if val is True else Unchecked)
                if is_orphan:
                    item.setBackground(_ORPHAN_CELL)
                elif val is None:
                    item.setBackground(_UNSET_CELL)
                self._table.setItem(i, j, item)

        self._table.blockSignals(False)

    # ── Cell interactions ────────────────────────────────────────────────

    def _on_cell_clicked(self, row_idx, col_idx):
        mode_data = self._data[self._current_mode]
        if row_idx >= len(mode_data["rows"]) or col_idx >= len(mode_data["columns"]):
            return
        row_key = mode_data["rows"][row_idx]["key"]
        col     = mode_data["columns"][col_idx]
        new_val = not bool(col["cells"].get(row_key, False))
        col["cells"][row_key] = new_val

        Checked   = QtCore.Qt.CheckState.Checked   if _QT6 else QtCore.Qt.Checked
        Unchecked = QtCore.Qt.CheckState.Unchecked if _QT6 else QtCore.Qt.Unchecked
        _UNSET_CELL = QtGui.QColor("#f5f5f5")

        item = self._table.item(row_idx, col_idx)
        if item:
            item.setCheckState(Checked if new_val else Unchecked)
            if not mode_data["rows"][row_idx].get("orphan"):
                item.setBackground(QtGui.QColor() if new_val else _UNSET_CELL)
        self._set_dirty(True)

    def _on_hover_started(self, row_idx, col_idx):
        mode_data = self._data[self._current_mode]
        if 0 <= col_idx < len(mode_data["columns"]):
            fp = mode_data["columns"][col_idx]["filename"]
            if os.path.exists(fp):
                self.request_load_file.emit(fp)
        if 0 <= row_idx < len(mode_data["rows"]):
            row_key = mode_data["rows"][row_idx]["key"]
            for i, pr in enumerate(self._peak_rows_cache):
                if make_row_key(pr) == row_key:
                    self.request_focus_row.emit(i)
                    return

    # ── Column management ────────────────────────────────────────────────

    def _on_add_column(self):
        files = (self._get_available_files() if self._get_available_files else [])
        if not files:
            QtWidgets.QMessageBox.information(self, "No files", "No sample files available.")
            return
        mode_data = self._data[self._current_mode]
        existing  = {c["filename"] for c in mode_data["columns"]}
        dlg = _AddColumnDialog(files, existing, self)
        accepted = (QtWidgets.QDialog.DialogCode.Accepted if _QT6
                    else QtWidgets.QDialog.Accepted)
        if dlg.exec() == accepted:
            for path in dlg.selected_files():
                self._add_column_for_file(path)
            self._refresh_table()

    def _on_add_all(self):
        files = (self._get_available_files() if self._get_available_files else [])
        for path in files:
            self._add_column_for_file(path)
        self._refresh_table()

    def _on_remove_column(self):
        mode_data = self._data[self._current_mode]
        columns = mode_data["columns"]
        if not columns:
            QtWidgets.QMessageBox.information(self, "No columns", "There are no columns to remove.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Remove Column(s)")
        dlg.resize(420, 280)
        layout = QtWidgets.QVBoxLayout(dlg)
        layout.addWidget(QtWidgets.QLabel("Select column(s) to remove:"))
        lst = QtWidgets.QListWidget()
        lst.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection if _QT6 else
            QtWidgets.QAbstractItemView.ExtendedSelection)
        UserRole = QtCore.Qt.ItemDataRole.UserRole if _QT6 else QtCore.Qt.UserRole
        for i, col in enumerate(columns):
            item = QtWidgets.QListWidgetItem(col["header"])
            item.setData(UserRole, i)
            item.setToolTip(col["filename"])
            lst.addItem(item)
        layout.addWidget(lst)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
            if _QT6 else
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        accepted = QtWidgets.QDialog.DialogCode.Accepted if _QT6 else QtWidgets.QDialog.Accepted
        if dlg.exec() != accepted:
            return
        indices = sorted(
            {item.data(UserRole) for item in lst.selectedItems()},
            reverse=True)
        if not indices:
            return
        for idx in indices:
            mode_data["columns"].pop(idx)
        self._set_dirty(True)
        self._refresh_table()

    def _on_remove_all_columns(self):
        mode_data = self._data[self._current_mode]
        if not mode_data["columns"]:
            return
        Yes = QtWidgets.QMessageBox.StandardButton.Yes if _QT6 else QtWidgets.QMessageBox.Yes
        No  = QtWidgets.QMessageBox.StandardButton.No  if _QT6 else QtWidgets.QMessageBox.No
        reply = QtWidgets.QMessageBox.question(
            self, "Remove all columns",
            f"Remove all {len(mode_data['columns'])} column(s) from the "
            f"'{self._current_mode}' confirmation data?",
            Yes | No)
        if reply != Yes:
            return
        mode_data["columns"].clear()
        self._set_dirty(True)
        self._refresh_table()

    def _add_column_for_file(self, filepath):
        mode_data = self._data[self._current_mode]
        if any(c["filename"] == filepath for c in mode_data["columns"]):
            return
        _, _, header = extract_sample_info(filepath)
        cells = {r["key"]: None for r in mode_data["rows"]}
        mode_data["columns"].append({
            "filename": filepath,
            "header":   header,
            "cells":    cells,
        })
        self._set_dirty(True)

    # ── Peak list sync ────────────────────────────────────────────────────

    def _on_change_peak_list(self):
        """Re-sync the panel with the current live peak list."""
        self.sync_with_peak_rows(self._peak_rows_cache)
        QtWidgets.QMessageBox.information(
            self, "Peak list synced",
            "The confirmation grid has been synchronised with the current peak list.\n"
            "Rows highlighted in yellow are no longer in the peak list.")

    def _sync_mode_rows(self, mode, peak_rows_dicts):
        """Merge peak_rows_dicts into mode_data, preserving existing cell data."""
        mode_data = self._data[mode]
        new_keys  = [make_row_key(r) for r in peak_rows_dicts]
        new_labels = {make_row_key(r): (r.get("label", "").strip() or make_row_key(r))
                      for r in peak_rows_dicts}
        new_key_set = set(new_keys)

        # Update/mark existing rows
        existing_by_key = {}
        for row in mode_data["rows"]:
            key = row["key"]
            existing_by_key[key] = row
            row["orphan"] = key not in new_key_set
            if key in new_labels:
                row["label"] = new_labels[key]

        existing_key_set = set(existing_by_key)

        # Insert new keys at their correct position
        for pl_idx, key in enumerate(new_keys):
            if key in existing_key_set:
                continue

            # Find the last predecessor key (in peak list) that is in data
            insert_after = -1
            for prev_idx in range(pl_idx - 1, -1, -1):
                pred = new_keys[prev_idx]
                if pred in existing_key_set:
                    for data_pos, row in enumerate(mode_data["rows"]):
                        if row["key"] == pred:
                            insert_after = data_pos
                            break
                    break

            new_row = {"key": key, "label": new_labels[key], "orphan": False}
            mode_data["rows"].insert(insert_after + 1, new_row)
            existing_key_set.add(key)
            existing_by_key[key] = new_row
            for col in mode_data["columns"]:
                col["cells"].setdefault(key, None)

    # ── Import / Export ──────────────────────────────────────────────────

    def _do_import(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import Confirmation Data", "",
            "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self._load_from_export(data)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import Error", str(exc))

    def _do_export(self, _checked=False):
        suggested = f"confirmation_{self._current_mode}.json"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Confirmation Data", suggested,
            "JSON Files (*.json);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(self._build_export_dict(), fh, indent=2)
            self._set_dirty(False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Error", str(exc))

    def _build_export_dict(self):
        mode_data = self._data[self._current_mode]
        peak_list_snapshot = []
        for r in self._peak_rows_cache:
            entry = {
                "key":   make_row_key(r),
                "label": r.get("label", ""),
                "mode":  r.get("mode", "manual"),
            }
            if r.get("mode") == "range":
                entry["range_start"] = r.get("range_start")
                entry["range_step"]  = r.get("range_step")
                entry["range_end"]   = r.get("range_end")
            else:
                entry["peaks"] = r.get("peaks", "")
            peak_list_snapshot.append(entry)
        return {
            "mode":      self._current_mode,
            "peak_list": peak_list_snapshot,
            "rows":      [{"key": r["key"], "label": r["label"],
                           "orphan": r.get("orphan", False)}
                          for r in mode_data["rows"]],
            "columns":   [{"filename": c["filename"], "header": c["header"],
                           "cells":    dict(c["cells"])}
                          for c in mode_data["columns"]],
        }

    def _load_from_export(self, data):
        mode = data.get("mode", self._current_mode)
        self._data[mode] = {
            "rows":    [{"key": r["key"], "label": r.get("label", r["key"]),
                         "orphan": r.get("orphan", False)}
                        for r in data.get("rows", [])],
            "columns": [{"filename": c["filename"],
                         "header":   c.get("header", os.path.basename(c["filename"])),
                         "cells":    dict(c.get("cells", {}))}
                        for c in data.get("columns", [])],
        }
        if mode == self._current_mode:
            self._refresh_table()
        self._set_dirty(False)

    # ── Serialisation helpers ─────────────────────────────────────────────

    def _serialise_mode(self, mode):
        md = self._data[mode]
        return {
            "rows":    [{"key": r["key"], "label": r["label"],
                         "orphan": r.get("orphan", False)}
                        for r in md["rows"]],
            "columns": [{"filename": c["filename"], "header": c["header"],
                         "cells":    dict(c["cells"])}
                        for c in md["columns"]],
        }

    # ── Dirty indicator ──────────────────────────────────────────────────

    def _set_dirty(self, dirty):
        self._dirty = dirty
        self._dirty_dot.setVisible(dirty)
