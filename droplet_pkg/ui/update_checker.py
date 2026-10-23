"""
Launches the Droplet updater (droplet_pkg/updater.py).

The updater runs as a separate process with the same Python interpreter,
so it has its own window and never replaces code inside the running
application.

  • check_on_startup() → python -m droplet_pkg.updater --if-outdated
                         (a window appears only if an update is available)
  • open_updater()     → python -m droplet_pkg.updater
                         (the window always appears and shows the result)

When the user clicks "Restart" after an update, the updater exits with
RESTART_EXIT_CODE; the handler set with on_restart_requested() then runs,
normally restart_droplet(), which closes this Droplet and starts the new one.
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    from PyQt6 import QtCore
except ImportError:
    from pyqtgraph.Qt import QtCore

# Imported now, before any update replaces updater.py on disk, so restarting
# uses the code this Droplet was started with.
from droplet_pkg import updater

ROOT    = Path(__file__).resolve().parents[2]  # installation folder
UPDATER = ROOT / "droplet_pkg" / "updater.py"

_process: subprocess.Popen | None = None
_watch: QtCore.QTimer | None = None
_restart_handler = None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def _launch(*args: str) -> bool:
    """Start the updater.  Returns False if one is already running."""
    global _process, _watch
    if is_running():
        return False
    if not UPDATER.exists():
        raise FileNotFoundError(f"{UPDATER} not found")
    # Run as a module from the installation folder (see updater.py docstring).
    _process = subprocess.Popen(
        [sys.executable, "-m", "droplet_pkg.updater", "--from-droplet", *args],
        cwd=str(ROOT))
    if _watch is None:
        _watch = QtCore.QTimer()
        _watch.setInterval(500)
        _watch.timeout.connect(_poll)
    _watch.start()
    return True


def _poll():
    if is_running():
        return
    _watch.stop()
    if _process.returncode == updater.RESTART_EXIT_CODE and _restart_handler:
        _restart_handler()


def on_restart_requested(fn) -> None:
    """Call fn() when the user clicks "Restart" in the updater."""
    global _restart_handler
    _restart_handler = fn


def restart_droplet(app, main_window) -> None:
    """Close Droplet normally, then start it again from the same folder."""
    app.aboutToQuit.connect(updater.launch_droplet)
    if not main_window.close():           # closing was refused: stay open
        app.aboutToQuit.disconnect(updater.launch_droplet)
        return
    app.quit()


def check_on_startup(app_settings) -> None:
    """Silent check; respects the "updates/check_enabled" setting.

    Skipped in a previous version launched from Help → Previous Versions, so
    an old copy is never updated in place.
    """
    if os.environ.get("DROPLET_PREVIOUS_VERSION"):
        return
    if app_settings.value("updates/check_enabled", True, type=bool):
        _launch("--if-outdated")


def open_updater() -> bool:
    """Open the updater window.  Returns False if one is already running."""
    return _launch()
