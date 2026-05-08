#!/usr/bin/env python3
"""
Droplet Updater
===============
Run from the Droplet installation folder:

    python updater.py

What it does
------------
1. Reads the local version from VERSION.
2. Fetches the remote VERSION from GitHub; aborts if already up-to-date.
3. Backs up the current installation to  droplet_backup_YYYYMMDD_HHMMSS/.
4. Updates the code:
     • If the folder is a git repository → git pull
     • Otherwise              → download the ZIP from GitHub and extract
5. Offers to restart Droplet after a successful update.

A simple PyQt6 window shows progress; falls back to console-only if Qt
is not importable (e.g. called from a minimal environment).
"""

import os
import sys
import shutil
import subprocess
import urllib.request
import urllib.error
import zipfile
import tempfile
from pathlib import Path
from datetime import datetime

# ── constants ─────────────────────────────────────────────────────────────────

GITHUB_REPO    = "krockdurr/Droplet"
VERSION_URL    = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/VERSION"
ZIP_URL        = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/main.zip"
TIMEOUT        = 30
ROOT           = Path(__file__).parent.resolve()
VERSION_FILE   = ROOT / "VERSION"
DROPLET_PKG    = ROOT / "droplet"
LAUNCHER_GLOB  = "Droplet_v*.py"

# Files / directories that must never be overwritten or deleted
PROTECTED = {
    "updater.py",
    ".git",
    ".gitignore",
    "LICENSE",
}


# ── helpers ───────────────────────────────────────────────────────────────────

def _to_tuple(v: str) -> tuple[int, ...]:
    parts = []
    for seg in v.strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    # fallback: read from package __init__
    init = DROPLET_PKG / "__init__.py"
    if init.exists():
        for line in init.read_text().splitlines():
            if "APP_VERSION" in line and "=" in line:
                return line.split("=")[1].strip().strip('"').strip("'")
    return "0.0.0"


def fetch_remote_version() -> str:
    with urllib.request.urlopen(VERSION_URL, timeout=TIMEOUT) as r:
        return r.read().decode().strip()


def is_git_repo() -> bool:
    return (ROOT / ".git").is_dir()


def backup_current(log) -> Path:
    stamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest   = ROOT / f"droplet_backup_{stamp}"
    log(f"Backing up current installation → {dest.name}/")
    shutil.copytree(DROPLET_PKG, dest / "droplet")
    if VERSION_FILE.exists():
        shutil.copy2(VERSION_FILE, dest / "VERSION")
    # copy launcher(s)
    for lp in ROOT.glob(LAUNCHER_GLOB):
        shutil.copy2(lp, dest / lp.name)
    log("Backup complete.")
    return dest


def update_via_git(log) -> bool:
    log("Git repository detected — running git pull …")
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "pull", "--ff-only"],
            capture_output=True, text=True, timeout=60)
        log(result.stdout.strip() or "(no output)")
        if result.returncode != 0:
            log(f"git pull failed:\n{result.stderr.strip()}")
            return False
        log("git pull succeeded.")
        return True
    except FileNotFoundError:
        log("git executable not found — falling back to ZIP download.")
        return False
    except subprocess.TimeoutExpired:
        log("git pull timed out.")
        return False


def update_via_zip(log) -> bool:
    log(f"Downloading {ZIP_URL} …")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "droplet_update.zip"
        try:
            urllib.request.urlretrieve(ZIP_URL, zip_path)
        except Exception as exc:
            log(f"Download failed: {exc}")
            return False

        log("Extracting …")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)

        # The zip contains a single top-level folder: Droplet-main/
        extracted_roots = [
            p for p in Path(tmp).iterdir()
            if p.is_dir() and p.name != "__MACOSX"
        ]
        if not extracted_roots:
            log("Could not find extracted folder — aborting.")
            return False
        src_root = extracted_roots[0]

        log("Copying new files …")
        # Copy everything except protected entries
        for item in src_root.iterdir():
            if item.name in PROTECTED:
                continue
            dest = ROOT / item.name
            if item.is_dir():
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)

        log("File copy complete.")
    return True


def find_launcher() -> Path | None:
    launchers = sorted(ROOT.glob(LAUNCHER_GLOB))
    return launchers[-1] if launchers else None


def restart_app(log):
    launcher = find_launcher()
    if launcher is None:
        log("No launcher script found — please restart Droplet manually.")
        return
    log(f"Restarting {launcher.name} …")
    os.execv(sys.executable, [sys.executable, str(launcher)])


# ── Qt GUI updater ─────────────────────────────────────────────────────────────

def run_with_qt():
    try:
        from PyQt6 import QtWidgets, QtCore, QtGui
    except ImportError:
        from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    win = QtWidgets.QDialog()
    win.setWindowTitle("Droplet Updater")
    win.setMinimumWidth(520)
    win.setMinimumHeight(340)
    win.setWindowFlag(QtCore.Qt.WindowType.WindowContextHelpButtonHint, False)

    lay = QtWidgets.QVBoxLayout(win)
    lay.setSpacing(8)

    title = QtWidgets.QLabel("<b style='font-size:14px;'>Droplet Updater</b>")
    lay.addWidget(title)

    log_box = QtWidgets.QPlainTextEdit()
    log_box.setReadOnly(True)
    log_box.setFont(QtGui.QFont("Monospace", 9))
    log_box.setMinimumHeight(180)
    lay.addWidget(log_box)

    progress = QtWidgets.QProgressBar()
    progress.setRange(0, 0)   # indeterminate
    progress.setVisible(False)
    lay.addWidget(progress)

    btn_row = QtWidgets.QHBoxLayout()
    update_btn  = QtWidgets.QPushButton("Check & Update")
    restart_btn = QtWidgets.QPushButton("Restart Droplet")
    restart_btn.setEnabled(False)
    close_btn   = QtWidgets.QPushButton("Close")
    btn_row.addWidget(update_btn)
    btn_row.addWidget(restart_btn)
    btn_row.addStretch()
    btn_row.addWidget(close_btn)
    lay.addLayout(btn_row)

    close_btn.clicked.connect(win.close)
    restart_btn.clicked.connect(lambda: (win.close(), restart_app(log)))

    _success = [False]

    def log(msg: str):
        log_box.appendPlainText(msg)
        qt_app.processEvents()

    class _Worker(QtCore.QThread):
        done = QtCore.pyqtSignal(bool)

        def run(self):
            ok = _do_update(log)
            self.done.emit(ok)

    _worker = [None]

    def _start():
        update_btn.setEnabled(False)
        progress.setVisible(True)

        w = _Worker()
        _worker[0] = w

        def _finished(ok):
            progress.setVisible(False)
            _success[0] = ok
            if ok:
                restart_btn.setEnabled(True)

        w.done.connect(_finished)
        w.start()

    update_btn.clicked.connect(_start)

    # Auto-start the check
    QtCore.QTimer.singleShot(200, _start)

    win.exec()


# ── core logic (shared between Qt and console) ────────────────────────────────

def _do_update(log) -> bool:
    lv = local_version()
    log(f"Local version  : {lv}")

    log("Checking remote version …")
    try:
        rv = fetch_remote_version()
    except Exception as exc:
        log(f"Could not reach GitHub: {exc}")
        return False
    log(f"Remote version : {rv}")

    if _to_tuple(rv) <= _to_tuple(lv):
        log("Already up-to-date. Nothing to do.")
        return True

    log(f"New version available: {rv}  (you have {lv})")

    try:
        backup_current(log)
    except Exception as exc:
        log(f"Backup failed: {exc}")
        log("Aborting update to be safe.")
        return False

    if is_git_repo():
        ok = update_via_git(log)
        if not ok:
            ok = update_via_zip(log)
    else:
        ok = update_via_zip(log)

    if ok:
        new_lv = local_version()
        log(f"\nUpdate complete!  New version: {new_lv}")
    else:
        log("\nUpdate FAILED. Your backup is still intact.")

    return ok


# ── console fallback ──────────────────────────────────────────────────────────

def run_console():
    def log(msg: str):
        print(msg)

    ok = _do_update(log)
    if ok:
        ans = input("\nRestart Droplet now? [y/N] ").strip().lower()
        if ans == "y":
            restart_app(log)
    sys.exit(0 if ok else 1)


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_with_qt()
    except Exception:
        # Qt not available or crashed — fall back to console
        run_console()
