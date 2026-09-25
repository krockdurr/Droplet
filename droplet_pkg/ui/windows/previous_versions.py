"""Help → Previous Versions…: install, launch and remove older Droplet releases.

The work itself is done by droplet_pkg.version_manager; this window only
shows it. Listing GitHub and installing run in a background thread so the
main window stays responsive.
"""

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg import version_manager as vm

Signal = getattr(QtCore, "pyqtSignal", None) or QtCore.Signal


class _Task(QtCore.QThread):
    """Runs fn(log) off the GUI thread; results come back as signals."""
    log       = Signal(str)
    succeeded = Signal(object)
    failed    = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.succeeded.emit(self._fn(self.log.emit))
        except vm.VersionError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:
            self.failed.emit(f"Unexpected error: {exc}")


class PreviousVersionsWindow(QtWidgets.QDialog):
    COL_VERSION, COL_STATUS, COL_SIZE = range(3)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Previous Versions")
        self.setMinimumSize(560, 420)
        self._remote: list[str] = []
        self._task: _Task | None = None
        self._installing = False

        lay = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            f"You are running Droplet <b>{vm.current_version()}</b>. Older versions can "
            "be installed next to it and opened in their own window; this version "
            "is not changed.<br><span style='color:gray;font-size:10px;'>Each version "
            "gets its own Python environment (about 600 MB) in "
            f"<code>{vm.VERSIONS_DIR.name}/</code>. Settings such as the last opened "
            "folder are shared between versions.</span>")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        self._table = QtWidgets.QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Version", "Status", "Size"])
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(self.COL_VERSION, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(self.COL_STATUS, QtWidgets.QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(self.COL_SIZE, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self._update_buttons)
        self._table.itemDoubleClicked.connect(lambda _item: self._on_launch())
        lay.addWidget(self._table, 1)

        self._status = QtWidgets.QLabel()
        self._status.setWordWrap(True)
        lay.addWidget(self._status)

        self._log = QtWidgets.QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QtGui.QFontDatabase.systemFont(
            QtGui.QFontDatabase.SystemFont.FixedFont))
        self._log.setMaximumBlockCount(2000)
        self._log.setMinimumHeight(120)
        self._log.hide()
        lay.addWidget(self._log)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.hide()
        lay.addWidget(self._progress)

        row = QtWidgets.QHBoxLayout()
        self._launch_btn  = QtWidgets.QPushButton("Launch")
        self._install_btn = QtWidgets.QPushButton("Install")
        self._remove_btn  = QtWidgets.QPushButton("Remove")
        self._refresh_btn = QtWidgets.QPushButton("Refresh")
        self._close_btn   = QtWidgets.QPushButton("Close")
        self._launch_btn.clicked.connect(self._on_launch)
        self._install_btn.clicked.connect(self._on_install)
        self._remove_btn.clicked.connect(self._on_remove)
        self._refresh_btn.clicked.connect(self._refresh)
        self._close_btn.clicked.connect(self.close)
        for b in (self._launch_btn, self._install_btn, self._remove_btn):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self._refresh_btn)
        row.addWidget(self._close_btn)
        lay.addLayout(row)

        self._fill_table()
        self._refresh()

    # ── table ──

    def _selected_branch(self) -> str | None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        return self._table.item(rows[0].row(), self.COL_VERSION).text()

    def _fill_table(self):
        keep = self._selected_branch()
        installed = vm.list_installed()
        branches = sorted(set(self._remote) | set(installed),
                          key=lambda b: vm._padded(vm.branch_version(b)), reverse=True)
        self._table.setRowCount(len(branches))
        for r, b in enumerate(branches):
            info = installed.get(b)
            if info:
                status = f"Installed {info['installed'][:10]}"
                if self._remote and b not in self._remote:
                    status += " (no longer on GitHub)"
                size = f"{info.get('size_bytes', 0) / 1e6:.0f} MB"
            else:
                status, size = "Not installed", ""
            for c, text in enumerate((b, status, size)):
                item = QtWidgets.QTableWidgetItem(text)
                if info and c == self.COL_STATUS:
                    item.setForeground(QtGui.QBrush(QtGui.QColor("#2e7d32")))
                self._table.setItem(r, c, item)
            if b == keep:
                self._table.selectRow(r)
        self._update_buttons()

    def _update_buttons(self):
        busy = self._task is not None and self._task.isRunning()
        b = self._selected_branch()
        self._close_btn.setEnabled(not self._installing)
        installed = bool(b and vm.installed_info(b))
        self._launch_btn.setEnabled(not busy and installed)
        self._remove_btn.setEnabled(not busy and installed)
        self._install_btn.setEnabled(not busy and bool(b) and not installed)
        self._refresh_btn.setEnabled(not busy)

    # ── background work ──

    def _run(self, fn, on_success, on_failure, show_log=False):
        self._task = _Task(fn, self)
        self._task.log.connect(self._log.appendPlainText)
        self._task.succeeded.connect(on_success)
        self._task.failed.connect(on_failure)
        self._task.finished.connect(self._task_finished)
        if show_log:
            self._log.clear(); self._log.show()
        self._progress.show()
        QtWidgets.QApplication.setOverrideCursor(
            QtGui.QCursor(QtCore.Qt.CursorShape.BusyCursor))
        self._task.start()
        self._update_buttons()

    def _task_finished(self):
        QtWidgets.QApplication.restoreOverrideCursor()
        self._installing = False
        self._progress.hide()
        self._update_buttons()

    def _refresh(self):
        self._status.setText(f"Looking for versions on github.com/{vm.GITHUB_REPO} …")
        self._run(lambda log: vm.list_remote_versions(), self._on_listed, self._on_list_failed)

    def _on_listed(self, remote):
        self._remote = remote
        self._fill_table()
        n = len(vm.list_installed())
        self._status.setText(
            f"{len(remote)} older version(s) on GitHub, {n} installed. "
            "Select one, then Install or Launch (double-click launches).")

    def _on_list_failed(self, msg):
        self._fill_table()
        self._status.setText(f"{msg}\nInstalled versions can still be launched.")

    # ── actions ──

    def _on_install(self):
        b = self._selected_branch()
        if not b:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Install previous version",
            f"Download Droplet {b} and create its own Python environment?\n\n"
            "This needs an internet connection, downloads about 150 MB and uses "
            "about 600 MB of disk space.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._status.setText(f"Installing {b} …")
        self._installing = True
        self._run(lambda log: vm.install(b, log), lambda _i: self._on_installed(b),
                  self._on_install_failed, show_log=True)

    def _on_installed(self, b):
        self._fill_table()
        self._status.setText(f"{b} is installed. Click Launch to open it.")

    def _on_install_failed(self, msg):
        self._fill_table()
        self._status.setText(f"Installation failed: {msg}")

    def _on_launch(self):
        b = self._selected_branch()
        if not b or not vm.installed_info(b):
            return
        try:
            vm.launch(b)
        except vm.VersionError as exc:
            QtWidgets.QMessageBox.warning(self, "Previous Versions", str(exc))
            return
        self._status.setText(f"Starting Droplet {b} in a new window …")

    def _on_remove(self):
        b = self._selected_branch()
        if not b:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Remove previous version",
            f"Delete Droplet {b} and its environment from\n{vm.version_dir(b)}?\n\n"
            "Your spectra, peak lists and settings are not affected.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No)
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            vm.remove(b)
        except (vm.VersionError, OSError) as exc:
            QtWidgets.QMessageBox.warning(self, "Previous Versions", f"Could not remove {b}:\n{exc}")
        self._fill_table()
        self._status.setText(f"{b} removed.")

    def closeEvent(self, event):
        # Never close in the middle of an install (a half-built environment).
        if self._installing:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self):
        if self._installing:
            return
        super().reject()
