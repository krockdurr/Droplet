"""
Background update checker for Droplet.

Spawns a QThread that fetches the VERSION file from GitHub without
blocking the UI.  If a newer version is found a non-intrusive dialog
is shown once per session.  The user can suppress future checks for a
specific version via QSettings.
"""

import urllib.request
import urllib.error

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

GITHUB_REPO   = "krockdurr/Droplet"
VERSION_URL   = (
    f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
)
RELEASES_URL  = f"https://github.com/{GITHUB_REPO}/releases"
REPO_URL      = f"https://github.com/{GITHUB_REPO}"
TIMEOUT       = 8   # seconds — silent failure beyond this


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_tuple(version_str: str) -> tuple[int, ...]:
    """'2.6.5'  →  (2, 6, 5).  Non-numeric parts become 0."""
    parts = []
    for seg in version_str.strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    return _to_tuple(remote) > _to_tuple(local)


# ── background thread ─────────────────────────────────────────────────────────

class _FetchThread(QtCore.QThread):
    """Fetch the remote VERSION string in a background thread."""
    result = QtCore.pyqtSignal(str)   # emitted with the version string on success

    def run(self):
        try:
            with urllib.request.urlopen(VERSION_URL, timeout=TIMEOUT) as resp:
                version = resp.read().decode().strip()
            if version:
                self.result.emit(version)
        except Exception:
            pass   # network unavailable, repo private, etc. — silent


# ── update dialog ─────────────────────────────────────────────────────────────

class UpdateDialog(QtWidgets.QDialog):
    """
    Small, non-blocking dialog shown when a newer version is found.
    Offers: 'Open releases page'  |  'Skip this version'  |  'Later'
    """

    def __init__(self, local_ver: str, remote_ver: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Update available")
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, False)
        self.setMinimumWidth(380)
        self.setSizeGripEnabled(False)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setSpacing(12)

        icon_row = QtWidgets.QHBoxLayout()
        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setPixmap(
            self.style()
                .standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MessageBoxInformation)
                .pixmap(32, 32))
        icon_row.addWidget(icon_lbl)

        msg = QtWidgets.QLabel(
            f"<b>Droplet {remote_ver}</b> is available.<br>"
            f"<span style='color:gray;font-size:10px;'>You are running {local_ver}.</span>")
        msg.setWordWrap(True)
        icon_row.addWidget(msg, 1)
        lay.addLayout(icon_row)

        hint = QtWidgets.QLabel(
            "To update, close Droplet and run <code>updater.py</code> "
            "from the installation folder, or visit the releases page.")
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 10px; color: gray;")
        lay.addWidget(hint)

        btn_row = QtWidgets.QHBoxLayout()
        releases_btn = QtWidgets.QPushButton("Open releases page")
        releases_btn.clicked.connect(self._open_releases)
        skip_btn = QtWidgets.QPushButton("Skip this version")
        skip_btn.clicked.connect(self._skip)
        later_btn = QtWidgets.QPushButton("Later")
        later_btn.clicked.connect(self.reject)
        later_btn.setDefault(True)
        btn_row.addWidget(releases_btn)
        btn_row.addStretch()
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(later_btn)
        lay.addLayout(btn_row)

        self._remote_ver = remote_ver

    def _open_releases(self):
        QtGui.QDesktopServices.openUrl(QtCore.QUrl(RELEASES_URL))
        self.accept()

    def _skip(self):
        self.done(2)   # caller interprets 2 as "skip this version"


# ── public API ────────────────────────────────────────────────────────────────

_fetch_thread: _FetchThread | None = None


def start_update_check(local_version: str, parent_widget,
                        app_settings: "QtCore.QSettings"):
    """
    Launch a background thread to check for updates.
    Call once after the main window is shown.

    Parameters
    ----------
    local_version  : the current APP_VERSION string
    parent_widget  : parent for the dialog (usually main_win)
    app_settings   : QSettings instance for persisting "skip" state
    """
    global _fetch_thread

    # Respect the user's opt-out
    if not app_settings.value("updates/check_enabled", True, type=bool):
        return

    _fetch_thread = _FetchThread()

    def _on_result(remote_ver: str):
        if not is_newer(remote_ver, local_version):
            return
        # Don't nag about a version the user already chose to skip
        skipped = app_settings.value("updates/skipped_version", "")
        if skipped and _to_tuple(skipped) >= _to_tuple(remote_ver):
            return

        dlg = UpdateDialog(local_version, remote_ver, parent=parent_widget)
        code = dlg.exec()
        if code == 2:   # "Skip this version"
            app_settings.setValue("updates/skipped_version", remote_ver)

    _fetch_thread.result.connect(_on_result)
    _fetch_thread.start()
