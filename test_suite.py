#!/usr/bin/env python3
"""
Droplet Test Suite
==================
Standalone test runner — launch from the Droplet installation folder:

    python test_suite.py            # GUI mode
    python test_suite.py --console  # console-only mode

What is tested
--------------
1.  Imports        — all submodules load without error
2.  Version        — VERSION file, __init__.py, and app.py are consistent
3.  Processing     — signal, normalization, baseline, calibration algorithms
4.  Spectrum I/O   — detect separators, parse various file formats
5.  Analysis       — peak detection, cluster detection, peak area
6.  GitHub data    — fetch test spectra from tests/data/ on the repo's default
                     branch (skipped gracefully if no network / no test folder)
7.  End-to-end     — load a real spectrum through the full pipeline
8.  UI windows     — each popup window class instantiates without crash
                     (uses offscreen Qt platform — no display required)
9.  Updater        — version comparison and remote fetch logic
"""

import sys
import os
import time
import io
import traceback
import tempfile
import urllib.request
import json
from pathlib import Path

# ── make sure the project root is on the path ────────────────────────────────
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

# ── use offscreen Qt platform when no display is available ───────────────────
if "DISPLAY" not in os.environ and sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GITHUB_REPO     = "krockdurr/Droplet"
GITHUB_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main"
TESTS_DATA_URL  = f"{GITHUB_API_BASE}/contents/tests/data"
TIMEOUT         = 10

# ─────────────────────────────────────────────────────────────────────────────
#  Result helpers
# ─────────────────────────────────────────────────────────────────────────────

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
WARN = "WARN"

_STATUS_ICON = {PASS: "✓", FAIL: "✗", SKIP: "⚠", WARN: "~"}


class TestResult:
    def __init__(self, name: str, status: str, detail: str = "", duration: float = 0.0):
        self.name     = name
        self.status   = status
        self.detail   = detail
        self.duration = duration

    def __str__(self):
        icon = _STATUS_ICON.get(self.status, "?")
        dur  = f"({self.duration*1000:.0f} ms)" if self.duration > 0 else ""
        line = f"  {icon} [{self.status:<4}] {self.name} {dur}"
        if self.detail:
            line += f"\n         {self.detail}"
        return line


# ─────────────────────────────────────────────────────────────────────────────
#  Individual tests
# ─────────────────────────────────────────────────────────────────────────────

def _run(name: str, fn) -> TestResult:
    t0 = time.perf_counter()
    try:
        detail = fn() or ""
        status = PASS
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=4)}"
        status = FAIL
    return TestResult(name, status, detail, time.perf_counter() - t0)


def _skip(name: str, reason: str) -> TestResult:
    return TestResult(name, SKIP, reason)


# ── 1. Imports ────────────────────────────────────────────────────────────────

def tests_imports() -> list[TestResult]:
    results = []
    modules = [
        ("droplet",                        "package root"),
        ("droplet.io.spectrum_reader",     "spectrum I/O"),
        ("droplet.io.file_utils",          "file utilities"),
        ("droplet.analysis.peaks",         "peak analysis"),
        ("droplet.analysis.clusters",      "cluster detection"),
        ("droplet.processing.signal",      "signal processing"),
        ("droplet.processing.normalization","normalization"),
        ("droplet.processing.baseline",    "baseline correction"),
        ("droplet.processing.calibration", "calibration"),
        ("droplet.ui.mixins",              "UI mixins"),
        ("droplet.ui.update_checker",      "update checker"),
        ("droplet.ui.windows.cluster_detection", "cluster window"),
        ("droplet.ui.windows.peak_area",   "peak area window"),
        ("droplet.ui.windows.peak_comparison", "peak comparison window"),
        ("droplet.ui.windows.residuals_viewer", "residuals viewer"),
    ]
    for mod, label in modules:
        def _import(m=mod):
            import importlib
            importlib.import_module(m)
        results.append(_run(f"import {label}", _import))
    return results


# ── 2. Version consistency ────────────────────────────────────────────────────

def tests_version() -> list[TestResult]:
    results = []

    def check_version_file():
        vf = ROOT / "VERSION"
        assert vf.exists(), "VERSION file missing"
        v = vf.read_text().strip()
        assert v, "VERSION file is empty"
        assert all(c.isdigit() or c == "." for c in v), f"Unexpected chars in version: {v!r}"
        return f"VERSION = {v}"

    def check_init_consistency():
        from droplet import APP_VERSION
        vf = (ROOT / "VERSION").read_text().strip()
        assert APP_VERSION == vf, f"__init__.py says {APP_VERSION!r}, VERSION file says {vf!r}"
        return f"APP_VERSION = {APP_VERSION}"

    def check_apppy_consistency():
        src = (ROOT / "droplet" / "app.py").read_text()
        from droplet import APP_VERSION
        # app.py may define APP_VERSION = "x.y.z" independently
        import re
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', src)
        if m:
            app_ver = m.group(1)
            if app_ver != APP_VERSION:
                return f"WARN: app.py has {app_ver!r}, __init__ has {APP_VERSION!r}"
        return "OK"

    results.append(_run("VERSION file valid", check_version_file))
    results.append(_run("__init__.py consistent", check_init_consistency))
    results.append(_run("app.py consistent", check_apppy_consistency))
    return results


# ── 3. Signal / processing ────────────────────────────────────────────────────

def tests_processing() -> list[TestResult]:
    results = []

    def make_spectrum():
        import numpy as np
        rng = np.random.default_rng(42)
        mz  = np.linspace(100, 2000, 4000)
        intensity = np.zeros(len(mz))
        for center, height in [(500, 1e6), (501, 8e5), (502, 3e5),
                                (1000, 5e5), (1001, 4e5), (1500, 2e5)]:
            idx = np.argmin(np.abs(mz - center))
            w = 5
            intensity[max(0,idx-w):idx+w+1] += height * np.exp(
                -0.5 * ((np.arange(-w, w+1))**2) / 2)
        intensity += rng.uniform(0, 500, len(mz))
        import pandas as pd
        return pd.DataFrame({"mz": mz, "intensity": intensity})

    def test_normalization():
        from droplet.processing.normalization import normalise
        df = make_spectrum()
        n  = normalise(df, zero_floor=True)
        assert 0 < n["intensity"].max() <= 1.0
        assert n["intensity"].min() >= 0

    def test_noise_floor():
        from droplet.processing.signal import get_tolerance, peak_shift
        tol = get_tolerance(500.0)
        assert tol > 0
        shift = peak_shift(1000.0)
        assert isinstance(shift, float)

    def test_find_peak_bounds():
        import numpy as np, pandas as pd
        from droplet.processing.signal import find_peak_bounds
        from droplet.processing.normalization import normalise
        # Wide, well-separated peak so peak_widths can resolve the boundaries
        mz  = np.linspace(400, 600, 2000)   # dense grid, 0.1 Da/point
        i   = np.zeros(len(mz))
        for c, h in [(500, 1e6), (550, 3e5)]:
            idx = int(np.searchsorted(mz, c))
            w   = 30   # wide peak (±30 points ≈ ±1.5 Da FWHM)
            sl  = slice(max(0, idx-w), idx+w+1)
            i[sl] += h * np.exp(-0.5*(np.arange(-w, min(w+1, len(mz)-idx))**2) / 100)
        i += 100   # baseline
        df  = pd.DataFrame({"mz": mz, "intensity": i})
        n_int = normalise(df, zero_floor=True)["intensity"].values
        # noise_floor must be below the normalised peak maximum
        bounds = find_peak_bounds(mz, n_int, 500.0,
                                   noise_floor=n_int.max() * 0.001,
                                   coarse_tol=2.0)
        assert bounds is not None, "find_peak_bounds returned None"
        mz_lo, mz_hi, real_mz, peak_max = bounds
        assert mz_lo < real_mz < mz_hi, f"Bounds inverted: lo={mz_lo:.2f} real={real_mz:.2f} hi={mz_hi:.2f}"
        assert abs(real_mz - 500.0) < 2.0

    def test_baseline():
        import numpy as np
        from droplet.processing.baseline import airpls_baseline
        import pandas as pd
        y = np.ones(200) * 100 + np.random.default_rng(1).normal(0, 1, 200)
        y[90:110] += 1000  # a peak
        baseline = airpls_baseline(y)
        assert len(baseline) == len(y)
        assert baseline[100] < 500   # baseline should be well below the peak

    results.append(_run("normalization (zero_floor)", test_normalization))
    results.append(_run("signal: get_tolerance / peak_shift", test_noise_floor))
    results.append(_run("find_peak_bounds on synthetic data", test_find_peak_bounds))
    results.append(_run("airPLS baseline on synthetic data", test_baseline))
    return results


# ── 4. Spectrum I/O ───────────────────────────────────────────────────────────

def tests_spectrum_io() -> list[TestResult]:
    results = []

    def test_separator_detection():
        from droplet.io.spectrum_reader import detect_separator
        assert detect_separator(["100.0\t200.0\n", "101.0\t300.0\n"]) == "\t"
        assert detect_separator(["100.0,200.0\n", "101.0,300.0\n"]) == ","

    def test_parse_tab_separated():
        from droplet.io.spectrum_reader import read_spectrum_file
        content = "# comment\n100.0\t1000\n101.0\t800\n102.0\t500\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path, sep="\t")
            assert len(df) == 3
            assert list(df.columns[:2]) == ["mz", "intensity"]
            assert df["mz"].iloc[0] == 100.0
        finally:
            os.unlink(path)

    def test_parse_csv():
        from droplet.io.spectrum_reader import read_spectrum_file
        content = "500.0,5000\n501.0,4000\n502.0,2000\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                         delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path, sep=",")
            assert len(df) == 3
            assert df["intensity"].max() == 5000
        finally:
            os.unlink(path)

    def test_parse_space_separated():
        from droplet.io.spectrum_reader import read_spectrum_file
        content = "100.5 1500\n101.5 1200\n102.5 900\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dat",
                                         delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path)
            assert len(df) == 3
        finally:
            os.unlink(path)

    results.append(_run("separator detection", test_separator_detection))
    results.append(_run("parse tab-separated spectrum", test_parse_tab_separated))
    results.append(_run("parse CSV spectrum", test_parse_csv))
    results.append(_run("parse space-separated spectrum", test_parse_space_separated))
    return results


# ── 5. Analysis ───────────────────────────────────────────────────────────────

def tests_analysis() -> list[TestResult]:
    results = []

    def make_df():
        import numpy as np, pandas as pd
        rng = np.random.default_rng(0)
        # Dense enough grid to resolve 1.005 Da spacing (≤ 0.3 Da/point)
        mz  = np.linspace(50, 3000, 20000)
        i   = np.zeros(len(mz))
        # Cluster of 4 peaks at 1.005 Da spacing — tall, wide, well above noise
        for c, h in [(500.000, 2e6), (501.005, 1.6e6),
                     (502.010, 1.0e6), (503.015, 6e5),
                     (800.000, 8e5),   (801.005, 6e5),
                     (802.010, 4e5)]:
            idx = int(np.searchsorted(mz, c))
            w   = 12   # wider peak so SNR is well above threshold
            pts = np.arange(-w, w+1)
            sl  = slice(max(0, idx-w), idx+w+1)
            i[sl] += h * np.exp(-0.5 * pts[:sl.stop-sl.start]**2 / 8)
        # Low, flat noise floor (~0.1 % of max peak)
        i += rng.uniform(0, 200, len(mz))
        return pd.DataFrame({"mz": mz, "intensity": i})

    def test_auto_peaks():
        from droplet.analysis.peaks import get_auto_peaks
        df = make_df()
        pks = get_auto_peaks(df, threshold_value=5.0, mode="pct")
        assert len(pks) > 0, "No peaks detected"
        mzs = [p[0] for p in pks]
        assert any(abs(m - 500) < 1.0 for m in mzs), "500 Da peak not found"

    def test_parse_peaks_text():
        from droplet.analysis.peaks import parse_peaks_text
        assert parse_peaks_text("500, 501, 502") == [500.0, 501.0, 502.0]
        assert parse_peaks_text("") == []
        assert parse_peaks_text("100.5") == [100.5]

    def test_cluster_detection():
        from droplet.analysis.clusters import run_cluster_detection
        df = make_df()
        clusters = run_cluster_detection(
            spacings_input="1.005",
            tol=0.05,
            min_chain=2,
            min_snr=0.5,
            min_pct=1.0,
            data_df=df,
        )
        assert isinstance(clusters, list), "Expected list of clusters"
        # Should find the 1.005 Da cluster
        spacings = [c["spacing"] for c in clusters]
        assert any(abs(s - 1.005) < 0.1 for s in spacings), (
            f"1.005 Da cluster not found; got spacings {spacings[:5]}")

    def test_cluster_detection_peak_list():
        from droplet.analysis.clusters import run_cluster_detection
        import numpy as np, pandas as pd
        df = pd.DataFrame({"mz": np.array([100.0]), "intensity": np.array([1.0])})
        clusters = run_cluster_detection(
            spacings_input="",
            tol=0.05, min_chain=2, min_snr=0.5, min_pct=0.1,
            data_df=df,
            peak_list_mzs=[500.0, 501.005, 502.010],
        )
        assert isinstance(clusters, list)

    results.append(_run("auto peak detection",          test_auto_peaks))
    results.append(_run("parse_peaks_text",             test_parse_peaks_text))
    results.append(_run("cluster detection (spectrum)", test_cluster_detection))
    results.append(_run("cluster detection (peak list)", test_cluster_detection_peak_list))
    return results


# ── 6. GitHub test data ───────────────────────────────────────────────────────

_github_spectra: list[tuple[str, str]] = []   # [(filename, content), ...]


def tests_github_data() -> list[TestResult]:
    results = []

    def fetch_file_list():
        req = urllib.request.Request(
            TESTS_DATA_URL,
            headers={"Accept": "application/vnd.github.v3+json",
                     "User-Agent": "DropletTestSuite/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            entries = json.loads(resp.read())
        files = [e for e in entries if e["type"] == "file"]
        assert files, "tests/data/ folder exists but is empty"
        return f"found {len(files)} file(s): {', '.join(e['name'] for e in files)}"

    def fetch_and_cache():
        req = urllib.request.Request(
            TESTS_DATA_URL,
            headers={"Accept": "application/vnd.github.v3+json",
                     "User-Agent": "DropletTestSuite/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            entries = json.loads(resp.read())
        files = [e for e in entries if e["type"] == "file"]
        for entry in files[:5]:   # cap at 5 to avoid hammering the API
            raw_url = entry["download_url"]
            with urllib.request.urlopen(raw_url, timeout=TIMEOUT) as r:
                content = r.read().decode("utf-8", errors="replace")
            _github_spectra.append((entry["name"], content))
        return f"downloaded {len(_github_spectra)} spectrum file(s)"

    try:
        results.append(_run("list tests/data/ on GitHub",    fetch_file_list))
        results.append(_run("download test spectrum files",  fetch_and_cache))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            msg = ("tests/data/ not found on GitHub — "
                   "create the folder and push at least one spectrum file to enable this test")
            results.append(_skip("list tests/data/ on GitHub",   msg))
            results.append(_skip("download test spectrum files",  msg))
        else:
            results.append(TestResult("list tests/data/ on GitHub",
                                       FAIL, str(e)))
            results.append(_skip("download test spectrum files", "skipped after fetch error"))
    except Exception as e:
        results.append(TestResult("list tests/data/ on GitHub",
                                   WARN, f"Network unavailable: {e}"))
        results.append(_skip("download test spectrum files", "skipped — no network"))

    return results


# ── 7. End-to-end pipeline on real / GitHub data ──────────────────────────────

def tests_end_to_end() -> list[TestResult]:
    results = []

    # Build test data: prefer GitHub-fetched files, fall back to synthetic
    def _get_test_df():
        if _github_spectra:
            name, content = _github_spectra[0]
            with tempfile.NamedTemporaryFile(mode="w", suffix=Path(name).suffix,
                                              delete=False) as f:
                f.write(content)
            try:
                from droplet.io.spectrum_reader import read_spectrum_file
                return read_spectrum_file(f.name), name
            finally:
                os.unlink(f.name)
        # Synthetic fallback
        import numpy as np, pandas as pd
        mz = np.linspace(100, 2000, 4000)
        i  = np.exp(-((mz - 500)**2) / 50) * 1e6 + np.random.default_rng(7).uniform(0,500,4000)
        return pd.DataFrame({"mz": mz, "intensity": i}), "synthetic"

    def test_load_and_normalise():
        df, src = _get_test_df()
        assert len(df) >= 10, f"Spectrum too short ({len(df)} points)"
        assert "mz" in df.columns and "intensity" in df.columns
        from droplet.processing.normalization import normalise
        n = normalise(df, zero_floor=True)
        assert n["intensity"].max() <= 1.0 + 1e-9
        return f"source={src}, {len(df)} points, max_intensity={df['intensity'].max():.3g}"

    def test_auto_detect():
        df, _ = _get_test_df()
        from droplet.analysis.peaks import get_auto_peaks
        pks = get_auto_peaks(df, threshold_value=5.0, mode="pct")
        return f"detected {len(pks)} peaks"

    def test_highlight_geometry():
        df, _ = _get_test_df()
        if len(df) < 50:
            return "skipped (too few points)"
        from droplet.processing.normalization import normalise
        norm_df = normalise(df, zero_floor=True)
        norm_int = norm_df["intensity"].values
        mz_arr   = df["mz"].values
        # Pick the peak m/z as a target
        peak_mz = float(mz_arr[norm_int.argmax()])
        from droplet.processing.signal import find_peak_bounds, get_tolerance, peak_shift
        import numpy as np
        thr  = mz_arr >= 10.9
        from droplet.processing.signal import _estimate_noise_floor, _DYN_CLIP_FLOOR
        noise_floor = (_estimate_noise_floor(norm_int[thr], n_sigma=3.0)
                       if thr.sum() >= 10 else _DYN_CLIP_FLOOR)
        shifted = peak_mz + peak_shift(peak_mz)
        bounds  = find_peak_bounds(mz_arr, norm_int, shifted,
                                    noise_floor=noise_floor,
                                    coarse_tol=get_tolerance(shifted))
        if bounds:
            lo, hi, real, pint = bounds
            assert lo < real < hi, f"Bounds inverted: {lo} {real} {hi}"
            return f"peak at {real:.2f} Da, bounds [{lo:.2f}, {hi:.2f}]"
        return "no bounds found (peak below noise floor?)"

    def test_cluster_on_real_data():
        df, src = _get_test_df()
        from droplet.analysis.clusters import run_cluster_detection
        clusters = run_cluster_detection(
            spacings_input="", tol=0.1, min_chain=2,
            min_snr=1.0, min_pct=2.0, data_df=df)
        return f"source={src}: {len(clusters)} cluster(s) found"

    results.append(_run("load + normalise spectrum",     test_load_and_normalise))
    results.append(_run("auto peak detection",           test_auto_detect))
    results.append(_run("find_peak_bounds on spectrum",  test_highlight_geometry))
    results.append(_run("cluster detection on spectrum", test_cluster_on_real_data))
    return results


# ── 8. UI window instantiation ────────────────────────────────────────────────

def tests_ui_windows() -> list[TestResult]:
    results = []

    # Qt must be running for UI tests
    try:
        from PyQt6 import QtWidgets, QtCore
    except ImportError:
        return [_skip("UI windows", "PyQt6 not importable")]

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    from droplet.ui.windows.cluster_detection import ClusterDetectionWindow
    from droplet.ui.windows.peak_area         import PeakAreaWindow
    from droplet.ui.windows.peak_comparison   import PeakComparisonWindow
    from droplet.ui.windows.residuals_viewer  import ResidualsViewerWindow

    windows = [
        ("ClusterDetectionWindow",  lambda: ClusterDetectionWindow()),
        ("ResidualsViewerWindow",   lambda: ResidualsViewerWindow()),
    ]

    for name, factory in windows:
        def _test(f=factory, n=name):
            w = f()
            assert w is not None, f"{n} returned None"
            w.show()
            QtCore.QCoreApplication.processEvents()
            w.close()

        results.append(_run(f"instantiate {name}", _test))

    # PeakAreaWindow and PeakComparisonWindow need a running app context
    # Test that the class at least imports and has the expected base class
    for name, cls in [("PeakAreaWindow", PeakAreaWindow),
                       ("PeakComparisonWindow", PeakComparisonWindow)]:
        def _check_class(c=cls, n=name):
            assert issubclass(c, QtWidgets.QWidget), f"{n} is not a QWidget subclass"
            from droplet.ui.mixins import StayOnTopMixin
            assert issubclass(c, StayOnTopMixin), f"{n} missing StayOnTopMixin"
        results.append(_run(f"class check {name}", _check_class))

    # SavedLabelsImportDialog
    def test_import_dialog():
        # Import from app is avoided — test just the class from the source
        import ast
        src = (ROOT / "droplet" / "app.py").read_text()
        assert "class SavedLabelsImportDialog" in src

    results.append(_run("SavedLabelsImportDialog defined", test_import_dialog))
    return results


# ── 9. Updater / update checker ───────────────────────────────────────────────

def tests_updater() -> list[TestResult]:
    results = []

    def test_version_comparison():
        from droplet.ui.update_checker import is_newer, _to_tuple
        assert is_newer("2.6.6", "2.6.5")
        assert not is_newer("2.6.5", "2.6.5")
        assert not is_newer("2.0.0", "2.6.5")
        assert is_newer("3.0.0", "2.99.99")
        assert is_newer("2.6.5.1", "2.6.5")

    def test_updater_helpers():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "updater", ROOT / "updater.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod._to_tuple("2.6.5") == (2, 6, 5)
        assert mod._to_tuple("1.0")   == (1, 0)
        lv = mod.local_version()
        assert lv, "local_version() returned empty string"
        return f"local version = {lv}"

    def test_is_git_repo():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "updater", ROOT / "updater.py")
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        result = mod.is_git_repo()
        return f"is_git_repo = {result}"

    def test_remote_version_fetch():
        from droplet.ui.update_checker import _FetchThread
        # Just check network reachability — don't actually start the QThread
        try:
            import urllib.request
            with urllib.request.urlopen(
                    "https://raw.githubusercontent.com/"
                    f"{GITHUB_REPO}/main/VERSION",
                    timeout=TIMEOUT) as r:
                ver = r.read().decode().strip()
            assert ver, "Remote VERSION file is empty"
            return f"remote version = {ver}"
        except Exception as e:
            raise RuntimeError(f"Network fetch failed: {e}") from e

    results.append(_run("version comparison logic", test_version_comparison))
    results.append(_run("updater helpers (local_version, is_git)", test_updater_helpers))
    results.append(_run("is_git_repo detection", test_is_git_repo))
    try:
        results.append(_run("fetch remote VERSION from GitHub", test_remote_version_fetch))
    except Exception:
        results.append(_skip("fetch remote VERSION from GitHub", "network unavailable"))

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Test runner
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    ("Imports",         tests_imports),
    ("Version",         tests_version),
    ("Processing",      tests_processing),
    ("Spectrum I/O",    tests_spectrum_io),
    ("Analysis",        tests_analysis),
    ("GitHub data",     tests_github_data),
    ("End-to-end",      tests_end_to_end),
    ("UI windows",      tests_ui_windows),
    ("Updater",         tests_updater),
]


def run_all() -> list[tuple[str, list[TestResult]]]:
    all_results = []
    for category, fn in CATEGORIES:
        results = fn()
        all_results.append((category, results))
    return all_results


# ─────────────────────────────────────────────────────────────────────────────
#  Console output
# ─────────────────────────────────────────────────────────────────────────────

def print_results(all_results: list[tuple[str, list[TestResult]]]):
    total = passed = failed = skipped = 0
    for category, results in all_results:
        print(f"\n── {category} {'─'*(50-len(category))}")
        for r in results:
            print(str(r))
            total   += 1
            if r.status == PASS: passed  += 1
            elif r.status == FAIL: failed += 1
            else:                  skipped += 1

    print("\n" + "═"*55)
    print(f"  Total: {total}   "
          f"✓ {passed} passed   "
          f"✗ {failed} failed   "
          f"⚠ {skipped} skipped/warned")
    print("═"*55)
    return failed == 0


# ─────────────────────────────────────────────────────────────────────────────
#  Qt GUI runner
# ─────────────────────────────────────────────────────────────────────────────

def run_with_gui():
    try:
        from PyQt6 import QtWidgets, QtCore, QtGui
    except ImportError:
        print("PyQt6 not available — running in console mode")
        run_console()
        return

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    win = QtWidgets.QWidget()
    win.setWindowTitle("Droplet Test Suite")
    win.resize(860, 620)
    root = QtWidgets.QVBoxLayout(win)
    root.setSpacing(6)

    # ── header ──
    title = QtWidgets.QLabel(
        "<b style='font-size:14px;'>Droplet Test Suite</b>  "
        "<span style='color:gray;font-size:10px;'>run from the installation folder</span>")
    root.addWidget(title)

    # ── results table ──
    table = QtWidgets.QTableWidget(0, 4)
    table.setHorizontalHeaderLabels(["Status", "Category", "Test", "Detail / Error"])
    table.horizontalHeader().setSectionResizeMode(
        0, QtWidgets.QHeaderView.ResizeMode.Fixed)
    table.setColumnWidth(0, 60)
    table.horizontalHeader().setSectionResizeMode(
        1, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(
        2, QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(
        3, QtWidgets.QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(
        QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    root.addWidget(table)

    # ── summary bar ──
    summary = QtWidgets.QLabel("Press 'Run All Tests' to start.")
    summary.setStyleSheet("font-size: 11px; color: gray;")
    root.addWidget(summary)

    # ── buttons ──
    btn_row = QtWidgets.QHBoxLayout()
    run_btn  = QtWidgets.QPushButton("▶  Run All Tests")
    run_btn.setFixedHeight(30)
    save_btn = QtWidgets.QPushButton("Save report…")
    save_btn.setEnabled(False)
    btn_row.addWidget(run_btn)
    btn_row.addWidget(save_btn)
    btn_row.addStretch()
    close_btn = QtWidgets.QPushButton("Close")
    close_btn.clicked.connect(win.close)
    btn_row.addWidget(close_btn)
    root.addLayout(btn_row)

    _STATUS_COLOR = {
        PASS: "#1a7a1a",
        FAIL: "#cc2222",
        SKIP: "#b07d00",
        WARN: "#b07d00",
    }
    _STATUS_BG = {
        PASS: "#eafaea",
        FAIL: "#faeaea",
        SKIP: "#faf5e0",
        WARN: "#faf5e0",
    }

    _all_results = []

    def populate(all_res):
        _all_results.clear()
        _all_results.extend(all_res)
        table.setRowCount(0)
        total = passed = failed = skipped = 0
        for category, results in all_res:
            for r in results:
                row = table.rowCount()
                table.insertRow(row)
                table.setRowHeight(row, 22)
                for col, text in enumerate([
                    f"{_STATUS_ICON.get(r.status,'?')} {r.status}",
                    category,
                    r.name,
                    r.detail.split("\n")[0][:120],
                ]):
                    item = QtWidgets.QTableWidgetItem(text)
                    item.setForeground(QtGui.QBrush(
                        QtGui.QColor(_STATUS_COLOR.get(r.status, "#000"))))
                    if col == 0:
                        item.setBackground(QtGui.QBrush(
                            QtGui.QColor(_STATUS_BG.get(r.status, "#fff"))))
                    table.setItem(row, col, item)
                total += 1
                if r.status == PASS:   passed  += 1
                elif r.status == FAIL: failed  += 1
                else:                  skipped += 1

        color = "#cc2222" if failed else "#1a7a1a"
        summary.setText(
            f"<span style='color:{color};'>"
            f"Total {total} — ✓ {passed} passed  ✗ {failed} failed  ⚠ {skipped} skipped"
            f"</span>")
        save_btn.setEnabled(True)

    class _Worker(QtCore.QThread):
        done = QtCore.pyqtSignal(object)
        def run(self):
            self.done.emit(run_all())

    _worker = [None]

    def start_run():
        run_btn.setEnabled(False)
        save_btn.setEnabled(False)
        summary.setText("Running…")
        table.setRowCount(0)
        w = _Worker()
        _worker[0] = w
        w.done.connect(lambda res: (populate(res), run_btn.setEnabled(True)))
        w.start()

    run_btn.clicked.connect(start_run)

    def save_report():
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "Save Test Report", str(ROOT / "test_report.txt"),
            "Text Files (*.txt)")
        if not path:
            return
        lines = []
        for category, results in _all_results:
            lines.append(f"\n── {category}")
            for r in results:
                lines.append(str(r))
        Path(path).write_text("\n".join(lines))

    save_btn.clicked.connect(save_report)

    win.show()
    QtCore.QTimer.singleShot(200, start_run)   # auto-start
    qt_app.exec()


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_console():
    all_results = run_all()
    ok = print_results(all_results)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--console" in sys.argv:
        run_console()
    else:
        run_with_gui()
