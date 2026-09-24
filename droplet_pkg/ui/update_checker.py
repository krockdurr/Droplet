"""
Launches the Droplet updater (droplet_pkg/updater.py).

The updater runs as a separate process with the same Python interpreter,
so it has its own window and never replaces code inside the running
application.

  • check_on_startup() → python -m droplet_pkg.updater --if-outdated
                         (a window appears only if an update is available)
  • open_updater()     → python -m droplet_pkg.updater
                         (the window always appears and shows the result)
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT    = Path(__file__).resolve().parents[2]  # installation folder
UPDATER = ROOT / "droplet_pkg" / "updater.py"

_process: subprocess.Popen | None = None


def is_running() -> bool:
    return _process is not None and _process.poll() is None


def _launch(*args: str) -> bool:
    """Start the updater.  Returns False if one is already running."""
    global _process
    if is_running():
        return False
    if not UPDATER.exists():
        raise FileNotFoundError(f"{UPDATER} not found")
    # Run as a module from the installation folder (see updater.py docstring).
    _process = subprocess.Popen(
        [sys.executable, "-m", "droplet_pkg.updater", *args], cwd=str(ROOT))
    return True


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
