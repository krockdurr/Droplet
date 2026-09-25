#!/usr/bin/env python3
"""
Droplet Updater
===============
Run from the Droplet installation folder, with Droplet's own Python:

    .venv/bin/python -m droplet_pkg.updater            # check, ask, update
    .venv/bin/python -m droplet_pkg.updater --check    # only report
    .venv/bin/python -m droplet_pkg.updater --yes      # update without asking
    .venv/bin/python -m droplet_pkg.updater --console  # terminal, no window
    .venv/bin/python -m droplet_pkg.updater --if-outdated  # silent unless outdated

Run it with -m rather than as a file path: as a script, droplet_pkg/ would
be first on sys.path and its subpackages (io/, …) would shadow the stdlib.

What it does
------------
1. Reads the local version from assets/about/VERSION.
2. Fetches that file from the update branch on GitHub (the repository's
   default branch unless UPDATE_BRANCH is set).
3. Opens a window telling the user what it is about to do (nothing, or
   update X → Y) with Update / Cancel buttons.  Falls back to a terminal
   prompt if Qt cannot be imported.
4. Updates the code:
     • git clone  → fast-forwards the checked-out branch (no backup needed,
                    git history is the backup)
     • otherwise  → downloads the branch ZIP, backs up every file/folder it
                    is about to overwrite to droplet_backup_YYYYMMDD_HHMMSS/,
                    then copies the new files in.

Use from Droplet
----------------
Droplet runs this module as a separate process (droplet_pkg/ui/update_checker.py),
so the update never replaces code inside the running application:
  • on startup                → -m droplet_pkg.updater --if-outdated
  • Help → Check for Updates… → -m droplet_pkg.updater

The logic itself is split into two UI-independent steps:

    plan = check_for_update()      # network only, changes nothing
    plan.needed / plan.describe()  # show this to the user
    apply_update(plan, log=...)    # does the update, raises UpdateError
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

# ── configuration ─────────────────────────────────────────────────────────────

GITHUB_REPO   = "krockdurr/Droplet"
# "HEAD" means the repository's default branch; set a branch name to pin one.
UPDATE_BRANCH = "HEAD"
TIMEOUT       = 30
ROOT          = Path(__file__).resolve().parent.parent  # installation folder
VERSION_PATH  = "assets/about/VERSION"
VERSION_FILE  = ROOT / VERSION_PATH
# Where to look for VERSION on GitHub; older branches keep it at the
# repository root.
REMOTE_VERSION_PATHS = (VERSION_PATH, "VERSION")
BACKUP_PREFIX = "droplet_backup_"

# Top-level entries the ZIP update never touches.
PROTECTED = {".git", ".venv", "previous_versions"}
# Top-level folders replaced wholesale, so files removed upstream disappear.
# Every other folder is merged: new files are copied in, extra local files stay.
REPLACED_DIRS = {"droplet_pkg"}

Log = Callable[[str], None]


class UpdateError(Exception):
    """Raised when checking for or applying an update fails."""


# ── versions ──────────────────────────────────────────────────────────────────

def _to_tuple(v: str) -> tuple[int, ...]:
    """'2.6.5' → (2, 6, 5).  Non-numeric parts become 0."""
    parts = []
    for seg in v.strip().split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    """True if `remote` is a higher version than `local` ('3.0' == '3.0.0')."""
    r, l = _to_tuple(remote), _to_tuple(local)
    width = max(len(r), len(l))
    return r + (0,) * (width - len(r)) > l + (0,) * (width - len(l))


def local_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip() or "0.0.0"
    return "0.0.0"


# ── GitHub access ─────────────────────────────────────────────────────────────

def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Droplet-updater"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def resolve_branch() -> str:
    """Name of the branch updates come from ('HEAD' if it cannot be resolved)."""
    if UPDATE_BRANCH != "HEAD":
        return UPDATE_BRANCH
    try:
        with _urlopen(f"https://api.github.com/repos/{GITHUB_REPO}") as r:
            return json.load(r)["default_branch"]
    except Exception:
        # API unreachable or rate-limited: raw/archive URLs also accept HEAD.
        return "HEAD"


def fetch_remote_version(branch: str) -> str:
    for path in REMOTE_VERSION_PATHS:
        url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/{branch}/{path}"
        try:
            with _urlopen(url) as r:
                return r.read().decode().strip()
        except urllib.error.HTTPError as exc:
            if exc.code != 404 or path == REMOTE_VERSION_PATHS[-1]:
                raise


def zip_url(branch: str) -> str:
    return f"https://github.com/{GITHUB_REPO}/archive/{branch}.zip"


# ── step 1: check ─────────────────────────────────────────────────────────────

def is_git_repo() -> bool:
    return (ROOT / ".git").is_dir() and shutil.which("git") is not None


@dataclass
class UpdatePlan:
    local_version: str
    remote_version: str
    branch: str
    method: str  # "git" or "zip"
    # Set when an update is available but cannot be applied automatically.
    blocker: str | None = None

    @property
    def needed(self) -> bool:
        return is_newer(self.remote_version, self.local_version)

    def describe(self) -> str:
        lines = [
            f"Installed version: {self.local_version}",
            f"Latest version: {self.remote_version}  "
            f"(github.com/{GITHUB_REPO}, branch {self.branch})",
            "",
        ]
        if not self.needed:
            lines.append("Droplet is up to date. Nothing to do.")
        elif self.blocker:
            lines.append(f"An update is available but cannot be applied "
                         f"automatically: {self.blocker}")
        elif self.method == "git":
            lines.append(f"Droplet will be updated {self.local_version} → "
                         f"{self.remote_version} with 'git pull --ff-only'.")
        else:
            lines.append(f"Droplet will be updated {self.local_version} → "
                         f"{self.remote_version} by downloading the ZIP from "
                         f"GitHub.")
            lines.append(f"Files that get replaced are first backed up to a "
                         f"{BACKUP_PREFIX}<date>_<time> folder inside the "
                         f"Droplet folder.")
        return "\n".join(lines)


def check_for_update() -> UpdatePlan:
    """Compare the local and remote versions.  Changes nothing on disk."""
    branch = resolve_branch()
    try:
        remote = fetch_remote_version(branch)
    except Exception as exc:
        raise UpdateError(f"Could not read the remote version: {exc}") from exc
    if not remote:
        raise UpdateError("The remote VERSION file is empty.")

    method, blocker = "zip", None
    if is_git_repo():
        method = "git"
        current = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        if branch != "HEAD" and current != branch:
            blocker = (f"this clone is on branch '{current}', but updates come "
                       f"from '{branch}'. Switch branch with git yourself.")
    return UpdatePlan(local_version=local_version(), remote_version=remote,
                      branch=branch, method=method, blocker=blocker)


# ── step 2: apply ─────────────────────────────────────────────────────────────

def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args],
                          capture_output=True, text=True, timeout=120)


def update_via_git(log: Log):
    log("Running git pull --ff-only …")
    try:
        result = _git("pull", "--ff-only")
    except subprocess.TimeoutExpired as exc:
        raise UpdateError("git pull timed out.") from exc
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.returncode != 0:
        raise UpdateError(f"git pull failed:\n{result.stderr.strip()}")


def _download_release(branch: str, dest: Path, log: Log) -> Path:
    """Download and extract the branch ZIP into `dest`; return its root folder."""
    url = zip_url(branch)
    log(f"Downloading {url} …")
    zip_path = dest / "droplet_update.zip"
    try:
        with _urlopen(url) as r, open(zip_path, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception as exc:
        raise UpdateError(f"Download failed: {exc}") from exc

    log("Extracting …")
    extract_dir = dest / "extracted"
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)
    except zipfile.BadZipFile as exc:
        raise UpdateError(f"Downloaded file is not a valid ZIP: {exc}") from exc

    # GitHub archives contain a single top-level folder, e.g. Droplet-v3.0/
    roots = [p for p in extract_dir.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise UpdateError("Unexpected ZIP layout, aborting.")
    return roots[0]


def _entries_to_install(src_root: Path) -> list[Path]:
    return [item for item in src_root.iterdir()
            if item.name not in PROTECTED
            and not item.name.startswith(BACKUP_PREFIX)]


def backup_entries(names: list[str], log: Log) -> Path:
    """Copy the listed top-level entries of the install to a backup folder."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = ROOT / f"{BACKUP_PREFIX}{stamp}"
    log(f"Backing up files that will be replaced → {dest.name}/")
    dest.mkdir()
    for name in names:
        src = ROOT / name
        if src.is_dir():
            shutil.copytree(src, dest / name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        elif src.exists():
            shutil.copy2(src, dest / name)
    return dest


def _install(src_root: Path, entries: list[Path]):
    for item in entries:
        dest = ROOT / item.name
        if item.is_dir():
            if item.name in REPLACED_DIRS and dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)


def update_via_zip(plan: UpdatePlan, log: Log):
    with tempfile.TemporaryDirectory() as tmp:
        # Download first, so a network failure leaves the install untouched.
        src_root = _download_release(plan.branch, Path(tmp), log)
        entries = _entries_to_install(src_root)
        try:
            backup = backup_entries([e.name for e in entries], log)
        except Exception as exc:
            raise UpdateError(f"Backup failed ({exc}); nothing was changed.") from exc

        log("Copying new files …")
        try:
            _install(src_root, entries)
        except Exception as exc:
            raise UpdateError(
                f"Copying failed: {exc}\n"
                f"The previous files are in {backup.name}/ — copy them back "
                f"into {ROOT} to restore.") from exc


def apply_update(plan: UpdatePlan, log: Log = print):
    """Perform the update described by `plan`.  Raises UpdateError on failure."""
    if not plan.needed:
        log("Already up to date. Nothing to do.")
        return
    if plan.blocker:
        raise UpdateError(plan.blocker)
    if plan.method == "git":
        update_via_git(log)
    else:
        update_via_zip(plan, log)

    installed = local_version()
    if is_newer(plan.remote_version, installed):
        raise UpdateError(f"Update finished but VERSION still says {installed} "
                          f"(expected {plan.remote_version}).")
    log(f"Update complete. Droplet is now at version {installed}.")


AFTER_UPDATE = ("Run the installer for your system again so the dependencies "
                "and launcher match the new version, then restart Droplet.")


# ── Qt front-end ──────────────────────────────────────────────────────────────

def run_gui(check_only: bool = False, auto_confirm: bool = False,
            if_outdated: bool = False) -> int:
    """Show the updater window.  Raises ImportError if no Qt binding exists.

    With `if_outdated`, the check runs first without any window, and the
    window only appears if an update is available.
    """
    plan = None
    if if_outdated:
        try:
            plan = check_for_update()
        except UpdateError:
            return 0  # offline etc. — stay silent
        if not plan.needed:
            return 0

    try:
        from PyQt6 import QtCore, QtGui, QtWidgets
    except ImportError:
        from pyqtgraph.Qt import QtCore, QtGui, QtWidgets
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
            except UpdateError as exc:
                self.failed.emit(str(exc))
            except Exception as exc:
                self.failed.emit(f"Unexpected error: {exc}")

    class UpdaterDialog(QtWidgets.QDialog):
        def __init__(self, plan: UpdatePlan | None = None):
            super().__init__()
            self.setWindowTitle("Droplet Updater")
            icon = ROOT / "assets" / "icons" / "Droplet_Icon.png"
            if icon.exists():
                self.setWindowIcon(QtGui.QIcon(str(icon)))
            self.exit_code = 0
            self._plan: UpdatePlan | None = None
            self._task: _Task | None = None

            lay = QtWidgets.QVBoxLayout(self)
            lay.setSpacing(10)
            # Window always fits its content as messages change.
            lay.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)

            self._title = QtWidgets.QLabel()
            self._title.setStyleSheet("font-size: 14px; font-weight: bold;")
            lay.addWidget(self._title)

            self._message = QtWidgets.QLabel()
            self._message.setWordWrap(True)
            self._message.setMinimumWidth(500)
            self._message.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(self._message)

            self._log = QtWidgets.QPlainTextEdit()
            self._log.setReadOnly(True)
            self._log.setFont(QtGui.QFontDatabase.systemFont(
                QtGui.QFontDatabase.SystemFont.FixedFont))
            self._log.setMinimumSize(500, 160)
            self._log.hide()
            lay.addWidget(self._log)

            self._progress = QtWidgets.QProgressBar()
            self._progress.setRange(0, 0)  # indeterminate
            self._progress.setTextVisible(False)
            lay.addWidget(self._progress)

            buttons = QtWidgets.QHBoxLayout()
            buttons.addStretch()
            self._primary = QtWidgets.QPushButton()
            self._primary.clicked.connect(self._on_primary)
            self._close = QtWidgets.QPushButton("Cancel")
            self._close.clicked.connect(self.reject)
            buttons.addWidget(self._primary)
            buttons.addWidget(self._close)
            lay.addLayout(buttons)

            if plan is None:
                self._start_check()
            else:
                self._on_checked(plan)

        # ── state changes ──

        def _busy(self, busy: bool):
            self._progress.setVisible(busy)
            self._primary.setEnabled(not busy)
            self._close.setEnabled(not busy)

        def _run(self, fn, on_success, on_failure):
            self._task = _Task(fn, self)
            self._task.log.connect(self._append_log)
            self._task.succeeded.connect(on_success)
            self._task.failed.connect(on_failure)
            self._busy(True)
            self._task.start()

        def _start_check(self):
            self._title.setText("Checking for updates …")
            self._message.setText(f"Contacting github.com/{GITHUB_REPO} …")
            self._primary.hide()
            self._run(lambda log: check_for_update(),
                      self._on_checked, self._on_check_failed)

        def _on_checked(self, plan: UpdatePlan):
            self._busy(False)
            self._plan = plan
            self._message.setText(plan.describe())
            if not plan.needed:
                self._title.setText("Droplet is up to date")
                self._close.setText("Close")
            elif plan.blocker:
                self._title.setText("Update available")
                self._close.setText("Close")
                self.exit_code = 1
            elif check_only:
                self._title.setText("Update available")
                self._close.setText("Close")
            else:
                self._title.setText("Update available")
                self._primary.setText("Update")
                self._primary.show()
                self._primary.setDefault(True)
                self._close.setText("Cancel")
                if auto_confirm:
                    self._start_update()

        def _on_check_failed(self, msg: str):
            self._busy(False)
            self.exit_code = 1
            self._title.setText("Could not check for updates")
            self._message.setText(msg)
            self._primary.setText("Retry")
            self._primary.show()
            self._close.setText("Close")

        def _on_primary(self):
            if self._plan is not None and self._plan.needed:
                self._start_update()
            else:
                self.exit_code = 0
                self._start_check()

        def _start_update(self):
            self._title.setText(f"Updating to {self._plan.remote_version} …")
            self._log.show()
            self._primary.hide()
            self._run(lambda log: apply_update(self._plan, log),
                      self._on_updated, self._on_update_failed)

        def _on_updated(self, _):
            self._busy(False)
            self.exit_code = 0
            self._title.setText("Update complete")
            self._message.setText(AFTER_UPDATE)
            self._close.setText("Close")

        def _on_update_failed(self, msg: str):
            self._busy(False)
            self.exit_code = 1
            self._title.setText("Update failed")
            self._message.setText(msg)
            self._close.setText("Close")

        def _append_log(self, msg: str):
            self._log.appendPlainText(msg)

        def reject(self):
            # Never close in the middle of a check or an update.
            if self._task is not None and self._task.isRunning():
                return
            super().reject()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    dialog = UpdaterDialog(plan)
    dialog.exec()
    return dialog.exit_code


# ── console front-end ─────────────────────────────────────────────────────────

def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_console(check_only: bool = False, auto_confirm: bool = False,
                if_outdated: bool = False) -> int:
    if not if_outdated:
        print("Checking for updates …")
    try:
        plan = check_for_update()
    except UpdateError as exc:
        if if_outdated:
            return 0
        print(exc)
        return 1
    if if_outdated and not plan.needed:
        return 0

    print()
    print(plan.describe())
    print()
    if plan.needed and plan.blocker:
        return 1
    if not plan.needed or check_only:
        return 0
    if not auto_confirm and not _confirm("Proceed with the update?"):
        print("Update cancelled. Nothing was changed.")
        return 0

    try:
        apply_update(plan)
    except UpdateError as exc:
        print(f"\nUpdate FAILED: {exc}")
        return 1

    print(f"\n{AFTER_UPDATE}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update Droplet from GitHub.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true",
                       help="only report whether an update is available")
    group.add_argument("--yes", "-y", action="store_true",
                       help="update without asking for confirmation")
    parser.add_argument("--console", action="store_true",
                        help="use the terminal instead of a window")
    parser.add_argument("--if-outdated", action="store_true",
                        help="stay silent unless an update is available "
                             "(used when Droplet starts)")
    args = parser.parse_args(argv)
    options = dict(check_only=args.check, auto_confirm=args.yes,
                   if_outdated=args.if_outdated)

    if not args.console:
        try:
            return run_gui(**options)
        except ImportError:
            print("Qt is not available, falling back to the terminal.\n")
    return run_console(**options)


if __name__ == "__main__":
    sys.exit(main())
