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
    .venv/bin/python -m droplet_pkg.updater --from-droplet # started by Droplet

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
5. If requirements.txt / requirements_new.txt changed, installs the new
   packages into the .venv the same way the installers do.  The launchers
   (menu shortcut, Droplet.app, desktop shortcut) only point at the .venv
   and Droplet.py, so they keep working and the installer need not be rerun.
6. Offers to restart Droplet.  Started with --from-droplet, the updater just
   exits with RESTART_EXIT_CODE and Droplet restarts itself (so it can close
   its windows properly); otherwise the updater starts Droplet itself.

Use from Droplet
----------------
Droplet runs this module as a separate process (droplet_pkg/ui/update_checker.py),
so the update never replaces code inside the running application:
  • on startup                → -m droplet_pkg.updater --if-outdated --from-droplet
  • Help → Check for Updates… → -m droplet_pkg.updater --from-droplet

The logic itself is split into two UI-independent steps:

    plan = check_for_update()      # network only, changes nothing
    plan.needed / plan.describe()  # show this to the user
    apply_update(plan, log=..., step=...)  # does the update, raises UpdateError
"""

import argparse
import json
import shutil
import ssl
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
REQ_DIR       = ROOT / "assets" / "assimilation_guides"
# Same order as the installers: pinned set first, newer pins as fallback.
REQ_FILES     = ("requirements.txt", "requirements_new.txt")
# Exit code telling Droplet (which started the updater) to restart itself.
RESTART_EXIT_CODE = 75

# Top-level entries the ZIP update never touches.
PROTECTED = {".git", ".venv", "previous_versions"}
# Top-level folders replaced wholesale, so files removed upstream disappear.
# Every other folder is merged: new files are copied in, extra local files stay.
REPLACED_DIRS = {"droplet_pkg"}

Log = Callable[[str], None]


def _no_step(_: str):
    pass


class UpdateError(Exception):
    """Raised when checking for or applying an update fails.

    `user_message`, when set, is what the window shows instead of the
    generic "nothing was changed" text (str(exc) goes into the details).
    """

    def __init__(self, message: str, user_message: str | None = None):
        super().__init__(message)
        self.user_message = user_message


class DependencyError(UpdateError):
    """The code was updated, but installing the new Python packages failed."""


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

def _ssl_context() -> ssl.SSLContext:
    """System certificates plus certifi's bundle when available.

    python.org's macOS Python ships without trusted certificates unless
    "Install Certificates.command" was run, so every HTTPS request fails with
    CERTIFICATE_VERIFY_FAILED.  pip vendors certifi, so it is always in the venv.
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        try:
            from pip._vendor import certifi
        except ImportError:
            return ctx
    try:
        ctx.load_verify_locations(certifi.where())
    except (OSError, ssl.SSLError):
        pass
    return ctx


def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Droplet-updater"})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context())


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
            # extractall drops Unix permissions: restore the executable bit,
            # or the .command/.sh installers can no longer be double-clicked.
            for info in zf.infolist():
                exec_bits = (info.external_attr >> 16) & 0o111
                if exec_bits and not info.is_dir():
                    path = extract_dir / info.filename
                    path.chmod(path.stat().st_mode | exec_bits)
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


def update_via_zip(plan: UpdatePlan, log: Log, step: Log = _no_step):
    with tempfile.TemporaryDirectory() as tmp:
        # Download first, so a network failure leaves the install untouched.
        step("Downloading the new version …")
        src_root = _download_release(plan.branch, Path(tmp), log)
        entries = _entries_to_install(src_root)
        step("Installing …")
        try:
            backup = backup_entries([e.name for e in entries], log)
        except Exception as exc:
            raise UpdateError(f"Backup failed ({exc}); nothing was changed.") from exc

        log("Copying new files …")
        try:
            _install(src_root, entries)
        except Exception as exc:
            restore = (f"The previous files are in {backup.name}/ — copy "
                       f"them back into {ROOT} to restore.")
            raise UpdateError(
                f"Copying failed: {exc}\n{restore}",
                user_message=f"Droplet could not finish installing the new "
                             f"files. {restore}") from exc


# ── step 3: Python packages ───────────────────────────────────────────────────

def _requirements() -> dict[str, bytes | None]:
    return {name: (REQ_DIR / name).read_bytes() if (REQ_DIR / name).exists()
            else None for name in REQ_FILES}


def venv_python() -> Path | None:
    """Python of the .venv the installers create, or None if there is none."""
    for rel in (".venv/bin/python", ".venv/Scripts/python.exe"):
        if (ROOT / rel).exists():
            return ROOT / rel
    return None


def update_dependencies(log: Log, step: Log = _no_step):
    """Install requirements into the .venv, exactly like the installers do."""
    python = venv_python()
    if python is None:
        log("No .venv in the Droplet folder, Python packages left as they are.")
        return
    step("Updating Python packages … (this can take a few minutes)")
    # Without this, every pip call opens a console window on Windows.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def pip_install(*args: str) -> bool:
        log("pip install " + " ".join(args))
        try:
            result = subprocess.run(
                [str(python), "-m", "pip", "install",
                 "--disable-pip-version-check", *args],
                capture_output=True, text=True, timeout=1800, creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as exc:
            log(str(exc))
            return False
        for out in (result.stdout, result.stderr):
            if out.strip():
                log(out.strip())
        return result.returncode == 0

    primary, newer = (REQ_DIR / name for name in REQ_FILES)
    # --only-binary fails fast when a pin has no wheel for this Python, so the
    # newer pins get their turn instead of a long source build.
    if primary.exists() and pip_install("--only-binary=:all:", "-r", str(primary)):
        return
    if newer.exists() and pip_install("-r", str(newer)):
        return
    raise DependencyError(
        "pip could not install requirements.txt or requirements_new.txt.",
        user_message="Droplet was updated, but the Python packages it needs "
                     "could not be installed. Check your internet connection "
                     "and try again.")


def apply_update(plan: UpdatePlan, log: Log = print, step: Log = _no_step):
    """Perform the update described by `plan`.  Raises UpdateError on failure.

    `log` gets technical progress lines, `step` short ones meant for anyone.
    """
    if not plan.needed:
        log("Already up to date. Nothing to do.")
        return
    if plan.blocker:
        raise UpdateError(plan.blocker)
    before = _requirements()
    if plan.method == "git":
        step("Downloading the new version …")
        update_via_git(log)
    else:
        update_via_zip(plan, log, step)

    installed = local_version()
    if is_newer(plan.remote_version, installed):
        raise UpdateError(f"Update finished but VERSION still says {installed} "
                          f"(expected {plan.remote_version}).")
    if _requirements() != before:
        update_dependencies(log, step)
    else:
        log("Python packages unchanged.")
    log(f"Update complete. Droplet is now at version {installed}.")


# ── step 4: restart ───────────────────────────────────────────────────────────

def _mac_app() -> Path | None:
    """~/Applications/Droplet.app, if its launcher opens this folder."""
    app = Path.home() / "Applications" / "Droplet.app"
    try:
        launcher = (app / "Contents" / "MacOS" / "Droplet").read_text()
    except OSError:
        return None
    return app if f'DROPLET_DIR="{ROOT}"' in launcher else None


def launch_droplet():
    """Start a new Droplet, detached from this process, the way its launcher
    would (on macOS through Droplet.app, so its log file keeps working)."""
    app = _mac_app() if sys.platform == "darwin" else None
    if app is not None:
        # -n: a new instance even though the closing one is still running.
        subprocess.Popen(["open", "-n", str(app)])
        return
    python = venv_python() or Path(sys.executable)
    cmd = [str(python), str(ROOT / "Droplet.py")]
    if sys.platform == "win32":
        pythonw = python.with_name("pythonw.exe")
        if pythonw.exists():
            cmd[0] = str(pythonw)
        subprocess.Popen(cmd, cwd=str(ROOT), close_fds=True,
                         creationflags=subprocess.DETACHED_PROCESS
                         | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        subprocess.Popen(cmd, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                         start_new_session=True)


# ── Qt front-end ──────────────────────────────────────────────────────────────

NOTHING_CHANGED = ("Nothing was changed, so your current version still works. "
                   "Check your internet connection and try again.")


def run_gui(check_only: bool = False, auto_confirm: bool = False,
            if_outdated: bool = False, from_droplet: bool = False) -> int:
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
        """Runs fn(log, step) off the GUI thread; results come back as signals."""
        log       = Signal(str)
        step      = Signal(str)
        succeeded = Signal(object)
        failed    = Signal(object)  # the exception

        def __init__(self, fn, parent=None):
            super().__init__(parent)
            self._fn = fn

        def run(self):
            try:
                self.succeeded.emit(self._fn(self.log.emit, self.step.emit))
            except UpdateError as exc:
                self.failed.emit(exc)
            except Exception as exc:
                self.failed.emit(UpdateError(f"Unexpected error: {exc}"))

    class UpdaterDialog(QtWidgets.QDialog):
        """One screen at a time: an icon, a title, one or two plain sentences,
        and at most two buttons.  Technical output sits behind "Show details"."""

        def __init__(self, plan: UpdatePlan | None = None):
            super().__init__()
            self.setWindowTitle("Droplet Update")
            icon_path = ROOT / "assets" / "icons" / "Droplet_Icon.png"
            if icon_path.exists():
                self.setWindowIcon(QtGui.QIcon(str(icon_path)))
            self.exit_code = 0
            self._plan: UpdatePlan | None = None
            self._task: _Task | None = None
            self._on_primary = self._on_secondary = None

            outer = QtWidgets.QVBoxLayout(self)
            outer.setContentsMargins(24, 22, 24, 18)
            outer.setSpacing(16)
            # Window always fits its content as messages change.
            outer.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetFixedSize)

            top = QtWidgets.QHBoxLayout()
            top.setSpacing(18)
            icon = QtWidgets.QLabel()
            if icon_path.exists():
                pix = QtGui.QPixmap(str(icon_path))
                dpr = self.devicePixelRatioF()
                pix = pix.scaled(int(56 * dpr), int(56 * dpr),
                                 QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                 QtCore.Qt.TransformationMode.SmoothTransformation)
                pix.setDevicePixelRatio(dpr)
                icon.setPixmap(pix)
            icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
            top.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignTop)

            text = QtWidgets.QVBoxLayout()
            text.setSpacing(8)
            self._title = QtWidgets.QLabel()
            font = self._title.font()
            font.setPointSizeF(font.pointSizeF() * 1.35)
            font.setBold(True)
            self._title.setFont(font)
            self._title.setWordWrap(True)
            text.addWidget(self._title)

            self._message = QtWidgets.QLabel()
            self._message.setWordWrap(True)
            self._message.setFixedWidth(340)
            self._message.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
            text.addWidget(self._message)

            self._progress = QtWidgets.QProgressBar()
            self._progress.setRange(0, 0)  # indeterminate
            self._progress.setTextVisible(False)
            self._progress.setFixedHeight(6)
            text.addWidget(self._progress)
            top.addLayout(text)
            outer.addLayout(top)

            self._log = QtWidgets.QPlainTextEdit()
            self._log.setReadOnly(True)
            self._log.setFont(QtGui.QFontDatabase.systemFont(
                QtGui.QFontDatabase.SystemFont.FixedFont))
            self._log.setFixedSize(414, 150)
            self._log.hide()
            outer.addWidget(self._log)

            buttons = QtWidgets.QHBoxLayout()
            self._details = QtWidgets.QPushButton("Show details")
            self._details.setFlat(True)
            self._details.clicked.connect(self._toggle_details)
            self._details.hide()
            buttons.addWidget(self._details)
            buttons.addStretch()
            self._secondary = QtWidgets.QPushButton()
            self._secondary.clicked.connect(lambda: self._on_secondary())
            self._primary = QtWidgets.QPushButton()
            self._primary.setProperty("role", "primary")
            self._primary.clicked.connect(lambda: self._on_primary())
            for b in (self._secondary, self._primary):
                b.setMinimumWidth(96)
                buttons.addWidget(b)
            outer.addLayout(buttons)

            if plan is None:
                self._start_check()
            else:
                self._on_checked(plan)

        # ── layout of one screen ──

        def _show(self, title: str, message: str, primary=None, secondary=None,
                  busy: bool = False):
            """primary / secondary: (label, callback) or None to hide."""
            self._title.setText(title)
            self._message.setText(message)
            self._progress.setVisible(busy)
            for button, spec, attr in ((self._primary, primary, "_on_primary"),
                                       (self._secondary, secondary, "_on_secondary")):
                button.setVisible(spec is not None)
                if spec is not None:
                    button.setText(spec[0])
                    setattr(self, attr, spec[1])
            if primary is not None:
                self._primary.setDefault(True)
                self._primary.setFocus()
            elif secondary is not None:
                self._secondary.setDefault(True)
                self._secondary.setFocus()

        def _close_with(self, code: int):
            self.exit_code = code
            self.accept()

        def _run(self, fn, on_success, on_failure):
            self._task = _Task(fn, self)
            self._task.log.connect(self._append_log)
            self._task.step.connect(self._message.setText)
            self._task.succeeded.connect(on_success)
            self._task.failed.connect(on_failure)
            self._task.start()

        # ── checking ──

        def _start_check(self):
            self._show("Checking for updates …", "This only takes a moment.",
                       busy=True)
            self._run(lambda log, step: check_for_update(),
                      self._on_checked, self._on_check_failed)

        def _on_checked(self, plan: UpdatePlan):
            self._plan = plan
            new, old = plan.remote_version, plan.local_version
            if not plan.needed:
                self._show("Droplet is up to date",
                           f"You have the latest version ({old}).",
                           primary=("OK", self.accept))
            elif plan.blocker:
                self.exit_code = 1
                self._show("A new version is available",
                           f"Version {new} is out, but this copy of Droplet "
                           f"can't update itself: {plan.blocker}",
                           secondary=("Close", self.reject))
            elif check_only:
                self._show("A new version is available",
                           f"Version {new} is ready to install. "
                           f"You have version {old}.",
                           secondary=("Close", self.reject))
            else:
                self._show("A new version is available",
                           f"Version {new} is ready to install. You have "
                           f"version {old}.\n\nYour settings are kept.",
                           primary=("Update", self._start_update),
                           secondary=("Not now", self.reject))
                if auto_confirm:
                    self._start_update()

        def _on_check_failed(self, exc: UpdateError):
            self.exit_code = 1
            self._append_log(str(exc))
            self._show("Couldn't check for updates",
                       "Make sure you're connected to the internet, "
                       "then try again.",
                       primary=("Try again", self._retry_check),
                       secondary=("Close", self.reject))

        def _retry_check(self):
            self.exit_code = 0
            self._start_check()

        # ── updating ──

        def _start_update(self):
            self._show(f"Updating Droplet to {self._plan.remote_version} …",
                       "Starting …", busy=True)
            self._run(lambda log, step: apply_update(self._plan, log, step),
                      self._on_updated, self._on_update_failed)

        def _retry_packages(self):
            self._show("Finishing the update …", "Starting …", busy=True)
            self._run(update_dependencies,
                      self._on_updated, self._on_update_failed)

        def _on_updated(self, _):
            self.exit_code = 0
            version = local_version()
            if from_droplet:
                self._show("Droplet has been updated",
                           f"Restart Droplet to start using version {version}.",
                           primary=("Restart", lambda: self._close_with(
                               RESTART_EXIT_CODE)),
                           secondary=("Later", self.accept))
            else:
                self._show("Droplet has been updated",
                           f"Version {version} is installed and ready to use.",
                           primary=("Open Droplet", self._open_droplet),
                           secondary=("Close", self.accept))

        def _open_droplet(self):
            try:
                launch_droplet()
            except OSError as exc:
                self._append_log(f"Could not start Droplet: {exc}")
                self._details.show()
                return
            self.accept()

        def _on_update_failed(self, exc: UpdateError):
            self.exit_code = 1
            self._append_log(str(exc))
            if isinstance(exc, DependencyError):
                retry = self._retry_packages
            else:
                retry = self._retry_check
            self._show("The update didn't finish",
                       exc.user_message or NOTHING_CHANGED,
                       primary=("Try again", retry),
                       secondary=("Close", self.reject))

        # ── details ──

        def _append_log(self, msg: str):
            self._log.appendPlainText(msg)
            self._details.show()

        def _toggle_details(self):
            shown = not self._log.isVisible()
            self._log.setVisible(shown)
            self._details.setText("Hide details" if shown else "Show details")

        def reject(self):
            # Never close in the middle of a check or an update.
            if self._task is not None and self._task.isRunning():
                return
            super().reject()

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    dialog = UpdaterDialog(plan)
    try:                                   # Droplet's look, following its dark / bright
        from droplet_pkg.ui.theme import follow_droplet_theme
        follow_droplet_theme(app, dialog)
    except Exception:
        pass                               # never let styling stop an update
    dialog.exec()
    return dialog.exit_code


# ── console front-end ─────────────────────────────────────────────────────────

def _confirm(question: str) -> bool:
    try:
        return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def run_console(check_only: bool = False, auto_confirm: bool = False,
                if_outdated: bool = False, from_droplet: bool = False) -> int:
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
        apply_update(plan, step=print)
    except UpdateError as exc:
        print(f"\nUpdate FAILED: {exc}")
        return 1

    print("\nRestart Droplet to start using the new version.")
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
    parser.add_argument("--from-droplet", action="store_true",
                        help="started by a running Droplet: 'Restart' exits "
                             f"with code {RESTART_EXIT_CODE} so Droplet "
                             "restarts itself")
    args = parser.parse_args(argv)
    options = dict(check_only=args.check, auto_confirm=args.yes,
                   if_outdated=args.if_outdated, from_droplet=args.from_droplet)

    if not args.console:
        try:
            return run_gui(**options)
        except ImportError:
            print("Qt is not available, falling back to the terminal.\n")
    return run_console(**options)


if __name__ == "__main__":
    sys.exit(main())
