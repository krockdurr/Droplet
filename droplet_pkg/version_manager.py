#!/usr/bin/env python3
"""
Droplet previous-versions manager
=================================
Installs older Droplet releases next to the current one and launches them.
The current installation is never modified.

Layout (created on first install):

    <Droplet folder>/previous_versions/
        v2.7.2/
            code/          the release, downloaded from its GitHub branch
            .venv/         its own Python environment with pinned libraries
            version.json   what was installed, when, and with which packages

Each previous version runs in its own environment because old releases need
specific library versions (e.g. every 2.x release calls np.trapz, which
NumPy 2.4 removed). The pins below were tested on every branch from v2.5.2
to v3.0: loading, baselines, auto/manual recalibration, peak and cluster
detection, and every menu action.

From a terminal, inside the Droplet folder:

    .venv/bin/python -m droplet_pkg.version_manager list
    .venv/bin/python -m droplet_pkg.version_manager install v2.7.2
    .venv/bin/python -m droplet_pkg.version_manager launch  v2.7.2
    .venv/bin/python -m droplet_pkg.version_manager remove  v2.7.2

In Droplet: Help → Previous Versions…

A launched previous version gets DROPLET_PREVIOUS_VERSION=<branch> in its
environment; versions that know this flag (3.1 onwards) then hide the
updater and this manager, so an old copy never updates itself.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Callable

# ── configuration ─────────────────────────────────────────────────────────────

GITHUB_REPO  = "krockdurr/Droplet"
TIMEOUT      = 30
ROOT         = Path(__file__).resolve().parent.parent      # installation folder
VERSIONS_DIR = ROOT / "previous_versions"
ENV_FLAG     = "DROPLET_PREVIOUS_VERSION"
VERSION_FILE = ROOT / "assets" / "about" / "VERSION"

# Tested library sets. PyQt6-Qt6 and PyQt6-sip are pinned too: PyQt6 only
# asks for "PyQt6-Qt6 >= x", which otherwise pulls a newer, incompatible Qt.
PINS_PY311_PLUS = [
    "PyQt6==6.11.0", "PyQt6-Qt6==6.11.2", "PyQt6-sip==13.12.0",
    "pyqtgraph==0.14.0", "numpy==2.3.5", "scipy==1.16.1",
    "pandas==2.3.3", "matplotlib==3.10.7",
]
PINS_PY310 = [   # NumPy 2.3 / SciPy 1.16 no longer support Python 3.10
    "PyQt6==6.11.0", "PyQt6-Qt6==6.11.2", "PyQt6-sip==13.12.0",
    "pyqtgraph==0.14.0", "numpy==2.2.6", "scipy==1.15.3",
    "pandas==2.3.3", "matplotlib==3.10.7",
]
# Branches the pins above were tested on. A newer branch uses its own pinned
# requirements file if it has one (see requirements_for).
TESTED_BRANCHES = {"v2.5.2", "v2.6.2", "v2.6.3", "v2.6.4",
                   "v2.7", "v2.7.1", "v2.7.2", "v3.0"}
OWN_REQUIREMENT_FILES = ("assets/assimilation_guides/requirements_new.txt",
                         "assets/assimilation_guides/requirements.txt",
                         "requirements.txt")

Log = Callable[[str], None]


class VersionError(Exception):
    """Raised when listing, installing, launching or removing fails."""


def running_as_previous_version() -> bool:
    return bool(os.environ.get(ENV_FLAG))


# ── versions ──────────────────────────────────────────────────────────────────

_BRANCH_RE = re.compile(r"^v(\d+(?:\.\d+)*)$")


def branch_version(branch: str) -> tuple[int, ...] | None:
    """'v2.7.2' → (2, 7, 2); None for branches that are not releases."""
    m = _BRANCH_RE.match(branch)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def _padded(v: tuple[int, ...], n: int = 4) -> tuple[int, ...]:
    return v + (0,) * (n - len(v))


def current_version() -> str:
    try:
        return VERSION_FILE.read_text().strip() or "0"
    except OSError:
        return "0"


def is_older_than_current(branch: str) -> bool:
    v = branch_version(branch)
    cur = branch_version("v" + current_version())
    return v is not None and cur is not None and _padded(v) < _padded(cur)


def _urlopen(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Droplet-version-manager"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def list_remote_versions() -> list[str]:
    """Release branches on GitHub that are older than this installation, newest first."""
    try:
        with _urlopen(f"https://api.github.com/repos/{GITHUB_REPO}/branches?per_page=100") as r:
            names = [b["name"] for b in json.load(r)]
    except Exception as exc:
        raise VersionError(f"Could not list versions on GitHub: {exc}") from exc
    older = [n for n in names if is_older_than_current(n)]
    return sorted(older, key=lambda n: _padded(branch_version(n)), reverse=True)


# ── installed versions ────────────────────────────────────────────────────────

def version_dir(branch: str) -> Path:
    if branch_version(branch) is None:
        raise VersionError(f"'{branch}' is not a release branch name (expected e.g. v2.7.2).")
    return VERSIONS_DIR / branch


def _venv_python(venv: Path, gui: bool = False) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / ("pythonw.exe" if gui else "python.exe")
    return venv / "bin" / "python"


def installed_info(branch: str) -> dict | None:
    """Contents of version.json, or None if the version is not (fully) installed."""
    d = version_dir(branch)
    try:
        info = json.loads((d / "version.json").read_text())
    except (OSError, ValueError):
        return None
    return info if _venv_python(d / ".venv").exists() else None


def list_installed() -> dict[str, dict]:
    if not VERSIONS_DIR.is_dir():
        return {}
    out = {}
    for d in VERSIONS_DIR.iterdir():
        if d.is_dir() and branch_version(d.name) is not None:
            info = installed_info(d.name)
            if info:
                out[d.name] = info
    return dict(sorted(out.items(), key=lambda kv: _padded(branch_version(kv[0])),
                       reverse=True))


# ── requirements ──────────────────────────────────────────────────────────────

def _pinned_lines(path: Path) -> list[str] | None:
    try:
        lines = [l.split("#")[0].strip() for l in path.read_text().splitlines()]
    except OSError:
        return None
    lines = [l for l in lines if l]
    return lines if lines and all("==" in l for l in lines) else None


def requirements_for(branch: str, code_dir: Path | None = None,
                     python: tuple[int, int] | None = None) -> tuple[list[str], str]:
    """Packages to install for `branch`, and where that list came from."""
    python = python or sys.version_info[:2]
    tested = PINS_PY310 if python < (3, 11) else PINS_PY311_PLUS
    if branch in TESTED_BRANCHES or code_dir is None:
        return tested, f"tested pins for Python {python[0]}.{python[1]}"
    for rel in OWN_REQUIREMENT_FILES:
        pins = _pinned_lines(code_dir / rel)
        if pins:
            return pins, f"{branch}'s own {rel}"
    return tested, f"tested pins for Python {python[0]}.{python[1]} (no pinned file in {branch})"


# ── install ───────────────────────────────────────────────────────────────────

def _base_python() -> str:
    """A console interpreter for creating environments (pythonw has no output)."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return str(exe)


def _run(cmd: list[str], log: Log, cwd: Path | None = None):
    """Run a command, forwarding its output line by line."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, errors="replace", creationflags=flags)
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            log(line)
    if proc.wait() != 0:
        raise VersionError(f"Command failed ({proc.returncode}): {' '.join(cmd[:4])} …")


def _folder_size(path: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def _rmtree(path: Path):
    def retry(func, p, _exc):          # read-only files on Windows
        os.chmod(p, 0o700); func(p)
    if not path.exists():
        return
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:
        shutil.rmtree(path, onerror=retry)


def install(branch: str, log: Log = print) -> dict:
    """Download `branch`, create its environment and install its libraries."""
    if running_as_previous_version():
        raise VersionError("Install previous versions from the current Droplet, "
                           "not from a previous version.")
    target = version_dir(branch)
    if installed_info(branch):
        raise VersionError(f"{branch} is already installed.")
    if sys.version_info < (3, 10):
        raise VersionError("Python 3.10 or newer is needed to install previous versions.")

    VERSIONS_DIR.mkdir(exist_ok=True)
    staging = VERSIONS_DIR / f".{branch}.installing"
    _rmtree(staging)
    _rmtree(target)                      # leftovers of an interrupted install
    staging.mkdir()
    try:
        url = f"https://github.com/{GITHUB_REPO}/archive/refs/heads/{branch}.zip"
        log(f"Downloading {url} …")
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "release.zip"
            try:
                with _urlopen(url) as r, open(zip_path, "wb") as f:
                    shutil.copyfileobj(r, f)
                with zipfile.ZipFile(zip_path) as zf:
                    zf.extractall(tmp)
            except Exception as exc:
                raise VersionError(f"Download failed: {exc}") from exc
            roots = [p for p in Path(tmp).iterdir() if p.is_dir()]
            if len(roots) != 1:
                raise VersionError("Unexpected ZIP layout.")
            shutil.move(str(roots[0]), str(staging / "code"))
        # Compiled caches from the repository belong to other interpreters.
        for cache in (staging / "code").rglob("__pycache__"):
            _rmtree(cache)

        pins, source = requirements_for(branch, staging / "code")
        log(f"Creating the Python environment ({Path(_base_python()).name} "
            f"{sys.version.split()[0]}) …")
        _run([_base_python(), "-m", "venv", str(staging / ".venv")], log)
        py = str(_venv_python(staging / ".venv"))
        log(f"Installing libraries ({source}) — this downloads about 150 MB …")
        _run([py, "-m", "pip", "install", "--disable-pip-version-check",
              "--no-input", "--only-binary=:all:", *pins], log)

        info = {"branch": branch,
                "installed": datetime.now().isoformat(timespec="seconds"),
                "python": sys.version.split()[0],
                "requirements": pins, "requirements_source": source,
                "size_bytes": _folder_size(staging)}
        (staging / "version.json").write_text(json.dumps(info, indent=1))
        staging.rename(target)
    except Exception:
        _rmtree(staging)
        raise
    log(f"{branch} installed ({info['size_bytes'] / 1e6:.0f} MB).")
    return info


# ── launch / remove ───────────────────────────────────────────────────────────

def find_launcher(code_dir: Path) -> Path:
    """Droplet.py (3.x) or Droplet_v2.x.y.py (2.x)."""
    if (code_dir / "Droplet.py").exists():
        return code_dir / "Droplet.py"
    candidates = sorted(code_dir.glob("Droplet*.py"))
    if not candidates:
        raise VersionError(f"No Droplet launcher found in {code_dir}.")
    return candidates[-1]


def launch(branch: str) -> subprocess.Popen:
    """Start a previous version in its own process and environment."""
    if not installed_info(branch):
        raise VersionError(f"{branch} is not installed.")
    d = version_dir(branch)
    code = d / "code"
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env[ENV_FLAG] = branch
    kw = {"cwd": str(code), "env": env}      # 2.x reads VERSION from the cwd
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return subprocess.Popen([str(_venv_python(d / ".venv", gui=True)),
                             str(find_launcher(code))], **kw)


def remove(branch: str):
    d = version_dir(branch)
    if not d.exists():
        raise VersionError(f"{branch} is not installed.")
    _rmtree(d)


# ── command line ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install and launch previous Droplet versions.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="show installed and available versions")
    for name in ("install", "launch", "remove"):
        sub.add_parser(name).add_argument("branch", help="e.g. v2.7.2")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "list":
            installed = list_installed()
            print(f"Current version: {current_version()}  ({ROOT})\n")
            print("Installed previous versions:")
            for b, info in installed.items():
                print(f"  {b:8s} {info['size_bytes'] / 1e6:6.0f} MB   installed {info['installed']}")
            if not installed:
                print("  (none)")
            try:
                remote = list_remote_versions()
                available = [b for b in remote if b not in installed]
                print("\nAvailable on GitHub:")
                print("  " + (", ".join(available) or "(none)"))
            except VersionError as exc:
                print(f"\n{exc}")
        elif args.cmd == "install":
            install(args.branch)
        elif args.cmd == "launch":
            launch(args.branch)
            print(f"Started {args.branch}.")
        elif args.cmd == "remove":
            remove(args.branch)
            print(f"Removed {args.branch}.")
    except VersionError as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
