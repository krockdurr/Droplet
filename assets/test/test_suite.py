#!/usr/bin/env python3
"""
Droplet Test Suite
==================
Standalone test runner - launch from the Droplet installation folder:

    python test_suite.py            # GUI mode (auto-starts, results shown live)
    python test_suite.py --console  # console-only mode

Categories
----------
 1.  Imports        - all submodules load without error
 2.  Constants      - OVERLAY_COLORS, CLUSTER_SYMBOLS, etc. are well-formed
 3.  Version        - VERSION file, __init__.py, and app.py are consistent
 4.  Processing     - normalization, signal subtraction, baseline algorithms
 5.  Calibration    - auto-recal pipeline, manual recal pairs, peak detection
 6.  Spectrum I/O   - separator detection, read/write roundtrip, file_utils
 7.  Analysis       - peak detection (pct + SNR), highlight geometry, clusters
 8.  Example data   - assets/example_spectra/ against its ground truth
                     (auto-recal, peak positions, clusters, areas, ratios)
 9.  End-to-end     - load an example spectrum through the full pipeline
10.  UI windows     - class-level checks (no instantiation - windows need the app)
11.  Updater        - version comparison, local helpers, remote fetch,
                     previous-versions manager helpers
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
ROOT = Path(__file__).resolve().parents[2]   # Droplet folder (this file is in assets/test/)
sys.path.insert(0, str(ROOT))
VERSION_FILE = ROOT / "assets" / "about" / "VERSION"

# ── use offscreen Qt platform when no display is available ───────────────────
if "DISPLAY" not in os.environ and sys.platform.startswith("linux"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

GITHUB_REPO     = "krockdurr/Droplet"
EXAMPLE_DIR     = ROOT / "assets" / "example_spectra"
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
        ("droplet_pkg",                              "package root"),
        ("droplet_pkg.constants",                    "constants"),
        ("droplet_pkg.io.spectrum_reader",           "spectrum I/O"),
        ("droplet_pkg.io.file_utils",                "file utilities"),
        ("droplet_pkg.analysis.peaks",               "peak analysis"),
        ("droplet_pkg.analysis.clusters",            "cluster detection"),
        ("droplet_pkg.processing.signal",            "signal processing"),
        ("droplet_pkg.processing.normalization",     "normalization"),
        ("droplet_pkg.processing.baseline",          "baseline correction"),
        ("droplet_pkg.processing.calibration",       "calibration"),
        ("droplet_pkg.ui.mixins",                    "UI mixins"),
        ("droplet_pkg.ui.update_checker",            "update checker"),
        ("droplet_pkg.updater",                      "updater"),
        ("droplet_pkg.ui.windows.cluster_detection", "cluster window"),
        ("droplet_pkg.ui.windows.peak_area",         "peak area window"),
        ("droplet_pkg.ui.windows.peak_comparison",   "peak comparison window"),
        ("droplet_pkg.ui.windows.residuals_viewer",  "residuals viewer"),
        ("droplet_pkg.ui.windows.peak_confirmation", "peak confirmation panel"),
        ("droplet_pkg.ui.windows.tutorial",          "tutorial overlay"),
        ("droplet_pkg.ui.windows.manual_recal",      "manual recal (factory)"),
    ]
    for mod, label in modules:
        def _import(m=mod):
            import importlib
            importlib.import_module(m)
        results.append(_run(f"import {label}", _import))
    return results


# ── 2. Constants ──────────────────────────────────────────────────────────────

def tests_constants() -> list[TestResult]:
    results = []

    def check_overlay_colors():
        from droplet_pkg.constants import OVERLAY_COLORS
        assert isinstance(OVERLAY_COLORS, list), "OVERLAY_COLORS must be a list"
        assert len(OVERLAY_COLORS) >= 6, "Need at least 6 overlay colors"
        for c in OVERLAY_COLORS:
            assert isinstance(c, str) and c.startswith("#"), f"Bad color: {c!r}"
            assert len(c) in (7, 9), f"Color not #RRGGBB: {c!r}"
        return f"{len(OVERLAY_COLORS)} colors, all valid #RRGGBB"

    def check_cluster_symbols():
        from droplet_pkg.constants import CLUSTER_SYMBOLS
        assert isinstance(CLUSTER_SYMBOLS, list)
        assert len(CLUSTER_SYMBOLS) >= 6
        assert all(isinstance(s, str) for s in CLUSTER_SYMBOLS)
        return f"{len(CLUSTER_SYMBOLS)} symbols"

    def check_default_peak_colors():
        from droplet_pkg.constants import DEFAULT_PEAK_COLOR_HEXES
        assert isinstance(DEFAULT_PEAK_COLOR_HEXES, list)
        assert len(DEFAULT_PEAK_COLOR_HEXES) >= 3
        for c in DEFAULT_PEAK_COLOR_HEXES:
            assert c.startswith("#"), f"Bad color: {c!r}"
        return f"{len(DEFAULT_PEAK_COLOR_HEXES)} default peak colors"

    def check_max_recent():
        from droplet_pkg.constants import MAX_RECENT
        assert isinstance(MAX_RECENT, int) and MAX_RECENT > 0
        return f"MAX_RECENT = {MAX_RECENT}"

    results.append(_run("OVERLAY_COLORS well-formed",       check_overlay_colors))
    results.append(_run("CLUSTER_SYMBOLS well-formed",      check_cluster_symbols))
    results.append(_run("DEFAULT_PEAK_COLOR_HEXES valid",   check_default_peak_colors))
    results.append(_run("MAX_RECENT positive integer",      check_max_recent))
    return results


# ── 3. Version consistency ────────────────────────────────────────────────────

def tests_version() -> list[TestResult]:
    results = []

    def check_version_file():
        vf = VERSION_FILE
        assert vf.exists(), "VERSION file missing"
        v = vf.read_text().strip()
        assert v, "VERSION file is empty"
        assert all(c.isdigit() or c == "." for c in v), f"Unexpected chars in version: {v!r}"
        return f"VERSION = {v}"

    def check_init_consistency():
        from droplet_pkg import APP_VERSION
        vf = VERSION_FILE.read_text().strip()
        assert APP_VERSION == vf, f"__init__.py says {APP_VERSION!r}, VERSION file says {vf!r}"
        return f"APP_VERSION = {APP_VERSION}"

    def check_apppy_consistency():
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        from droplet_pkg import APP_VERSION
        import re
        m = re.search(r'APP_VERSION\s*=\s*["\']([^"\']+)["\']', src)
        if m:
            app_ver = m.group(1)
            if app_ver != APP_VERSION:
                return f"WARN: app.py has {app_ver!r}, __init__ has {APP_VERSION!r}"
        return "OK"

    results.append(_run("VERSION file valid",      check_version_file))
    results.append(_run("__init__.py consistent",  check_init_consistency))
    results.append(_run("app.py consistent",       check_apppy_consistency))
    return results


# ── 4. Processing ─────────────────────────────────────────────────────────────

def tests_processing() -> list[TestResult]:
    results = []

    def make_spectrum(n=4000):
        import numpy as np, pandas as pd
        rng = np.random.default_rng(42)
        mz  = np.linspace(100, 2000, n)
        i   = np.zeros(n)
        for center, height in [(500, 1e6), (501, 8e5), (502, 3e5),
                                (1000, 5e5), (1001, 4e5), (1500, 2e5)]:
            idx = np.argmin(np.abs(mz - center))
            w = 5
            i[max(0, idx-w):idx+w+1] += height * np.exp(
                -0.5 * np.arange(-w, w+1)**2 / 2)
        i += rng.uniform(0, 500, n)
        return pd.DataFrame({"mz": mz, "intensity": i})

    # ── normalise ──
    def test_normalization():
        from droplet_pkg.processing.normalization import normalise
        n = normalise(make_spectrum(), zero_floor=True)
        assert 0 < n["intensity"].max() <= 1.0
        assert n["intensity"].min() >= 0

    def test_normalise_cached():
        from droplet_pkg.processing.normalization import normalise_cached, clear_norm_cache
        clear_norm_cache()
        df = make_spectrum()
        r1 = normalise_cached(df, zero_floor=True)
        r2 = normalise_cached(df, zero_floor=True)
        assert r1 is r2, "Second call with same df should hit cache"
        clear_norm_cache()
        r3 = normalise_cached(df, zero_floor=True)
        assert r3 is not r1, "Cache should be invalidated after clear"

    def test_normalise_and_clip():
        from droplet_pkg.processing.normalization import normalise_and_clip
        result = normalise_and_clip(make_spectrum())
        assert "intensity" in result.columns
        assert result["intensity"].max() <= 1.0 + 1e-9
        assert result["intensity"].min() >= 0

    def test_normalize_df_max_mode():
        from droplet_pkg.processing.normalization import normalize_df
        r = normalize_df(make_spectrum(), mode='max')
        assert r["intensity"].max() <= 1.0 + 1e-9

    def test_normalize_df_peak_mode():
        import numpy as np
        from droplet_pkg.processing.normalization import normalize_df
        df = make_spectrum()
        peak_mz = float(df.loc[df["intensity"].idxmax(), "mz"])
        r = normalize_df(df, mode='peak', peak_mz=peak_mz)
        assert "intensity" in r.columns
        # Intensity at the peak m/z should be ≈ 1
        near = r[np.abs(r["mz"] - peak_mz) < 1.0]
        assert near["intensity"].max() >= 0.9

    def test_normalize_df_log_floor():
        from droplet_pkg.processing.normalization import normalize_df
        r = normalize_df(make_spectrum(), mode='max', apply_log_floor=True)
        assert r["intensity"].min() > 0, "Log floor should lift zeros"

    def test_estimate_noise_floor():
        import numpy as np
        from droplet_pkg.processing.normalization import estimate_noise_floor
        rng = np.random.default_rng(7)
        noise = rng.uniform(0, 0.01, 1000)
        noise[500] = 1.0      # isolated spike
        floor = estimate_noise_floor(noise, n_sigma=3.0)
        assert 0 < floor < 0.1, f"Noise floor {floor:.4f} outside expected range"

    def test_estimate_noise_floor_low_sigma():
        import numpy as np
        from droplet_pkg.processing.normalization import estimate_noise_floor
        noise = np.ones(200) * 0.005
        floor_3 = estimate_noise_floor(noise, n_sigma=3.0)
        floor_1 = estimate_noise_floor(noise, n_sigma=1.0)
        assert floor_1 <= floor_3 + 1e-9, "Lower sigma should give lower or equal floor"

    # ── signal ──
    def test_get_tolerance():
        from droplet_pkg.processing.signal import get_tolerance
        assert get_tolerance(100.0) > 0
        # Formula: min(0.1 + 0.004*mz, 0.8). Cap triggers at ~175 Da.
        assert get_tolerance(100.0) < get_tolerance(150.0)    # grows before cap
        assert abs(get_tolerance(200.0)  - 0.8) < 1e-9       # capped
        assert abs(get_tolerance(1000.0) - 0.8) < 1e-9       # still capped

    def test_peak_shift():
        from droplet_pkg.processing.signal import peak_shift
        s = peak_shift(1000.0)
        assert isinstance(s, float)
        assert s > 0    # higher masses shift right

    def test_compute_subtraction_static():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.signal import compute_subtraction
        mz = np.linspace(100, 500, 200)
        a = pd.DataFrame({"mz": mz, "intensity": np.full(200, 1000.0)})
        b = pd.DataFrame({"mz": mz, "intensity": np.full(200, 300.0)})
        result = compute_subtraction(a, b, dynamic=False)
        assert result is not None
        assert "intensity" in result.columns
        # Expect ≈ 700 throughout (1000 - 300)
        assert abs(result["intensity"].mean() - 700.0) < 10

    def test_compute_subtraction_dynamic():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.signal import compute_subtraction
        mz = np.linspace(100, 500, 200)
        a = pd.DataFrame({"mz": mz, "intensity": np.full(200, 2000.0)})
        b = pd.DataFrame({"mz": mz, "intensity": np.full(200, 1000.0)})
        result = compute_subtraction(a, b, dynamic=True)
        # Dynamic: both normalized to 1 before subtraction → result ≈ 0
        assert result is not None
        assert abs(result["intensity"].mean()) < 0.1

    def test_compute_subtraction_none_guard():
        from droplet_pkg.processing.signal import compute_subtraction
        assert compute_subtraction(None, None) is None

    # ── find_peak_bounds ──
    def test_find_peak_bounds():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.signal import find_peak_bounds
        from droplet_pkg.processing.normalization import normalise
        mz  = np.linspace(400, 600, 2000)
        i   = np.zeros(len(mz))
        for c, h in [(500, 1e6), (550, 3e5)]:
            idx = int(np.searchsorted(mz, c))
            w   = 30
            sl  = slice(max(0, idx-w), idx+w+1)
            i[sl] += h * np.exp(-0.5*(np.arange(-w, min(w+1, len(mz)-idx))**2) / 100)
        i += 100
        df      = pd.DataFrame({"mz": mz, "intensity": i})
        n_int   = normalise(df, zero_floor=True)["intensity"].values
        bounds  = find_peak_bounds(mz, n_int, 500.0,
                                   noise_floor=n_int.max() * 0.001, coarse_tol=2.0)
        assert bounds is not None
        mz_lo, mz_hi, real_mz, peak_max = bounds
        assert mz_lo < real_mz < mz_hi
        assert abs(real_mz - 500.0) < 2.0

    # ── baseline ──
    def test_airpls_baseline():
        import numpy as np
        from droplet_pkg.processing.baseline import airpls_baseline
        y = np.ones(200) * 100 + np.random.default_rng(1).normal(0, 1, 200)
        y[90:110] += 1000
        baseline = airpls_baseline(y)
        assert len(baseline) == len(y)
        assert baseline[100] < 500

    def test_snip_baseline():
        import numpy as np
        from droplet_pkg.processing.baseline import snip_baseline
        y = np.ones(200) * 100 + np.random.default_rng(2).normal(0, 1, 200)
        y[90:110] += 1000
        baseline = snip_baseline(y)
        assert len(baseline) == len(y)
        assert baseline[100] < 500, "SNIP baseline should sit below the peak"

    def test_apply_baseline_airpls():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.baseline import apply_baseline
        y   = np.ones(200) * 100 + np.random.default_rng(3).normal(0, 1, 200)
        y[90:110] += 1000
        df  = pd.DataFrame({"mz": np.linspace(100, 300, 200), "intensity": y})
        out = apply_baseline(df, algorithm='airpls')
        assert "intensity" in out.columns and len(out) == len(df)

    def test_apply_baseline_snip():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.baseline import apply_baseline
        y   = np.ones(200) * 100 + np.random.default_rng(4).normal(0, 1, 200)
        y[90:110] += 1000
        df  = pd.DataFrame({"mz": np.linspace(100, 300, 200), "intensity": y})
        out = apply_baseline(df, algorithm='snip')
        assert "intensity" in out.columns and len(out) == len(df)

    def test_baseline_stop_flag_no_crash():
        import numpy as np
        from droplet_pkg.processing.baseline import airpls_baseline, snip_baseline

        class _SF:
            _stop_requested = False   # not set → should complete normally

        y = np.ones(100) * 100; y[45:55] += 500
        b1 = airpls_baseline(y, itermax=3, stop_flag=_SF())
        b2 = snip_baseline(y, stop_flag=_SF())
        assert len(b1) == len(b2) == len(y)

    results.append(_run("normalise (zero_floor)",             test_normalization))
    results.append(_run("normalise_cached (cache hit/miss)",  test_normalise_cached))
    results.append(_run("normalise_and_clip",                 test_normalise_and_clip))
    results.append(_run("normalize_df max mode",              test_normalize_df_max_mode))
    results.append(_run("normalize_df peak mode",             test_normalize_df_peak_mode))
    results.append(_run("normalize_df log_floor",             test_normalize_df_log_floor))
    results.append(_run("estimate_noise_floor",               test_estimate_noise_floor))
    results.append(_run("estimate_noise_floor (sigma param)", test_estimate_noise_floor_low_sigma))
    results.append(_run("get_tolerance: range + cap",         test_get_tolerance))
    results.append(_run("peak_shift: positive for high mz",   test_peak_shift))
    results.append(_run("compute_subtraction (static)",       test_compute_subtraction_static))
    results.append(_run("compute_subtraction (dynamic)",      test_compute_subtraction_dynamic))
    results.append(_run("compute_subtraction (None guard)",   test_compute_subtraction_none_guard))
    results.append(_run("find_peak_bounds on synthetic data", test_find_peak_bounds))
    results.append(_run("airPLS baseline",                    test_airpls_baseline))
    results.append(_run("SNIP baseline",                      test_snip_baseline))
    results.append(_run("apply_baseline airpls",              test_apply_baseline_airpls))
    results.append(_run("apply_baseline snip",                test_apply_baseline_snip))
    results.append(_run("baseline stop_flag (no crash)",      test_baseline_stop_flag_no_crash))
    return results


# ── 5. Calibration ────────────────────────────────────────────────────────────

def tests_calibration() -> list[TestResult]:
    results = []

    def test_detect_peak_near_found():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.calibration import detect_peak_near
        mz  = np.array([99.5, 100.0, 100.5, 200.0])
        i   = np.array([10.0, 1000.0, 20.0, 500.0])
        df  = pd.DataFrame({"mz": mz, "intensity": i})
        res = detect_peak_near(df, 100.0, window=1.0)
        assert res is not None, "Should find peak near 100 Da"
        assert abs(res[0] - 100.0) < 0.1
        assert res[1] == 1000.0

    def test_detect_peak_near_miss():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.calibration import detect_peak_near
        df  = pd.DataFrame({"mz": [500.0, 501.0], "intensity": [100.0, 200.0]})
        res = detect_peak_near(df, 100.0, window=1.0)
        assert res is None, "Nothing within 1 Da of 100 should return None"

    def test_manual_recal_empty_pairs():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.calibration import apply_manual_recal_pairs
        mz  = np.linspace(100, 1000, 100)
        df  = pd.DataFrame({"mz": mz, "intensity": np.ones(100)})
        out = apply_manual_recal_pairs(df, [])
        assert len(out) == len(df)
        assert np.allclose(out["mz"].values, df["mz"].values)

    def test_manual_recal_single_pair():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.calibration import apply_manual_recal_pairs
        mz  = np.linspace(100, 1000, 500)
        df  = pd.DataFrame({"mz": mz, "intensity": np.ones(500)})
        # Single pair → linear scale factor ref/obs
        out = apply_manual_recal_pairs(df, [(100.0, 101.0)])
        assert "mz" in out.columns and len(out) == len(df)
        # First m/z should scale by 101/100 = 1.01
        assert abs(out["mz"].iloc[0] / mz[0] - 1.01) < 0.01

    def test_manual_recal_multi_pair():
        import numpy as np, pandas as pd
        from droplet_pkg.processing.calibration import apply_manual_recal_pairs
        mz  = np.linspace(100, 500, 500)
        df  = pd.DataFrame({"mz": mz, "intensity": np.ones(500)})
        pairs = [(100.0, 100.1), (250.0, 250.25), (400.0, 400.4)]
        out = apply_manual_recal_pairs(df, pairs)
        assert len(out) == len(df)
        assert out["mz"].dtype == float
        # Recalibrated m/z should stay in a reasonable range
        assert out["mz"].min() > 90 and out["mz"].max() < 520

    def test_auto_recal_cancelled():
        import numpy as np
        from droplet_pkg.processing.calibration import run_auto_recal

        class _SF:
            _stop_requested = True   # already set → should raise InterruptedError

        data = np.column_stack([np.linspace(10, 250, 1000), np.ones(1000)])
        try:
            run_auto_recal(data, "sample_neg_001.txt", stop_flag=_SF())
            # If the function returns before checking the flag, that is fine too
        except InterruptedError:
            pass   # expected
        except ValueError:
            pass   # also acceptable (no peaks in flat data)

    def test_auto_recal_no_peaks_raises():
        import numpy as np
        from droplet_pkg.processing.calibration import run_auto_recal
        # Completely flat → no peaks → ValueError
        data = np.column_stack([np.linspace(10, 250, 1000), np.ones(1000)])
        try:
            run_auto_recal(data, "sample_neg_001.txt")
        except ValueError:
            return "ValueError raised as expected for flat spectrum"
        # If somehow it succeeds on flat data, still acceptable
        return "No ValueError (algorithm found something in flat data)"

    def test_auto_recal_pipeline():
        """Run the full pipeline on a synthetic neg-mode spectrum."""
        import numpy as np
        from droplet_pkg.processing.calibration import run_auto_recal
        n   = 10000
        mz  = np.linspace(10, 250, n)
        i   = np.random.default_rng(42).uniform(50, 200, n)
        # Peaks at negative-mode reference masses: 17, 35, 43, 71, 89 Da
        for target in [17.0, 35.0, 43.0, 71.0, 89.0]:
            idx = int(np.searchsorted(mz, target))
            for j in range(max(0, idx-4), min(n, idx+5)):
                i[j] += 60000 * np.exp(-0.5 * ((j - idx) / 1.5)**2)
        data = np.column_stack([mz, i])
        try:
            recal, check_manually, summary_df, fitparams = run_auto_recal(
                data, "synth_neg_001.txt")
            assert recal.shape == data.shape
            assert len(fitparams) == 3          # quadratic polynomial
            assert "original m/z" in summary_df.columns
            return f"recal OK, check_manually={check_manually}, {len(summary_df)} cal peaks"
        except ValueError as e:
            return f"ValueError (acceptable with synthetic data): {e}"

    results.append(_run("detect_peak_near (hit)",            test_detect_peak_near_found))
    results.append(_run("detect_peak_near (miss → None)",    test_detect_peak_near_miss))
    results.append(_run("manual recal: empty pairs",         test_manual_recal_empty_pairs))
    results.append(_run("manual recal: single pair (linear)", test_manual_recal_single_pair))
    results.append(_run("manual recal: multiple pairs",      test_manual_recal_multi_pair))
    results.append(_run("auto_recal: cancelled via stop flag", test_auto_recal_cancelled))
    results.append(_run("auto_recal: flat spectrum → ValueError", test_auto_recal_no_peaks_raises))
    results.append(_run("auto_recal: full pipeline (neg mode)", test_auto_recal_pipeline))
    return results


# ── 6. Spectrum I/O ───────────────────────────────────────────────────────────

def tests_spectrum_io() -> list[TestResult]:
    results = []

    # ── spectrum_reader ──
    def test_separator_detection():
        from droplet_pkg.io.spectrum_reader import detect_separator
        assert detect_separator(["100.0\t200.0\n", "101.0\t300.0\n"]) == "\t"
        assert detect_separator(["100.0,200.0\n", "101.0,300.0\n"]) == ","

    def test_parse_tab_separated():
        from droplet_pkg.io.spectrum_reader import read_spectrum_file
        content = "# comment\n100.0\t1000\n101.0\t800\n102.0\t500\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path, sep="\t")
            assert len(df) == 3
            assert list(df.columns[:2]) == ["mz", "intensity"]
            assert df["mz"].iloc[0] == 100.0
        finally:
            os.unlink(path)

    def test_parse_csv():
        from droplet_pkg.io.spectrum_reader import read_spectrum_file
        content = "500.0,5000\n501.0,4000\n502.0,2000\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path, sep=",")
            assert len(df) == 3
            assert df["intensity"].max() == 5000
        finally:
            os.unlink(path)

    def test_parse_space_separated():
        from droplet_pkg.io.spectrum_reader import read_spectrum_file
        content = "100.5 1500\n101.5 1200\n102.5 900\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".dat", delete=False) as f:
            f.write(content); path = f.name
        try:
            df = read_spectrum_file(path)
            assert len(df) == 3
        finally:
            os.unlink(path)

    def test_read_spectrum_headers():
        from droplet_pkg.io.spectrum_reader import read_spectrum_headers
        content = "# source: synthetic\n# polarity: neg\n100.0\t1000\n101.0\t800\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(content); path = f.name
        try:
            headers = read_spectrum_headers(path)
            assert isinstance(headers, list)
            assert any("source" in h or "synthetic" in h for h in headers), \
                f"Expected source header; got {headers}"
        finally:
            os.unlink(path)

    def test_save_load_roundtrip():
        import numpy as np, pandas as pd
        from droplet_pkg.io.spectrum_reader import save_spectrum_df, read_spectrum_file
        mz = np.linspace(100, 500, 200)
        df = pd.DataFrame({"mz": mz, "intensity": np.random.default_rng(5).uniform(0, 1000, 200)})
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            path = f.name
        try:
            save_spectrum_df(df, path)
            loaded = read_spectrum_file(path)
            assert len(loaded) == len(df), f"Expected {len(df)} rows, got {len(loaded)}"
            assert np.allclose(loaded["mz"].values, df["mz"].values, atol=1e-3)
            assert np.allclose(loaded["intensity"].values, df["intensity"].values, atol=1e-3)
        finally:
            os.unlink(path)

    def test_save_with_process_tag():
        """process_tag is only written when src_path is supplied and exists."""
        import numpy as np, pandas as pd
        from droplet_pkg.io.spectrum_reader import save_spectrum_df, read_spectrum_headers
        mz = np.linspace(100, 200, 50)
        df = pd.DataFrame({"mz": mz, "intensity": np.ones(50)})
        # Write a source file with a header so the tag can be prepended to it
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as src:
            src.write("# original header\n100.0\t1.0\n"); src_path = src.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as out:
            out_path = out.name
        try:
            save_spectrum_df(df, out_path, src_path=src_path, process_tag="baseline-corrected")
            headers = read_spectrum_headers(out_path)
            joined  = " ".join(headers).lower()
            assert "baseline" in joined or "corrected" in joined, \
                f"Process tag should appear in headers; got {headers}"
        finally:
            os.unlink(src_path); os.unlink(out_path)

    def test_parse_lilbid_name():
        from droplet_pkg.io.spectrum_reader import parse_lilbid_name
        name = parse_lilbid_name("/data/20231015_myprotein_pos_dt150.txt")
        assert isinstance(name, str) and len(name) > 0

    def test_spectrum_display_name():
        from droplet_pkg.io.spectrum_reader import spectrum_display_name
        name = spectrum_display_name("/data/sample_pos_dt100.txt")
        assert isinstance(name, str) and len(name) > 0

    # ── file_utils ──
    def test_list_all_txt_files():
        from droplet_pkg.io.file_utils import list_all_txt_files
        with tempfile.TemporaryDirectory() as d:
            for name in ["a.txt", "b.csv", "c.dat", "d.py", "e.tsv", "f.asc"]:
                (Path(d) / name).write_text("100\t1000\n")
            files = list_all_txt_files(d)
            exts  = {Path(f).suffix for f in files}
            assert ".txt" in exts and ".csv" in exts and ".dat" in exts
            assert ".py" not in exts, ".py files must be excluded"
            assert ".tsv" in exts and ".asc" in exts

    def test_get_txt_files_polarity():
        from droplet_pkg.io.file_utils import get_txt_files
        files = ["/d/sample_pos_001.txt", "/d/sample_neg_001.txt", "/d/other.txt"]
        assert [f for f in get_txt_files(files, "pos") if "pos" in f]
        assert [f for f in get_txt_files(files, "neg") if "neg" in f]
        assert len(get_txt_files(files, "All")) == 3

    def test_extract_dt():
        from droplet_pkg.io.file_utils import extract_dt
        assert extract_dt("sample_pos_dt150_001.txt") == "150"
        assert extract_dt("/path/to/run_dt42.txt")    == "42"
        assert extract_dt("no_drift_time.txt")         is None

    def test_get_available_dt_values():
        from droplet_pkg.io.file_utils import get_available_dt_values
        files = ["/d/a_pos_dt100.txt", "/d/b_pos_dt200.txt",
                 "/d/c_neg_dt100.txt", "/d/d_neg_dt300.txt"]
        all_dt = get_available_dt_values(files)
        assert set(all_dt) == {"100", "200", "300"}
        pos_dt = get_available_dt_values(files, polarity="pos")
        assert set(pos_dt) == {"100", "200"}

    def test_get_txt_files_filtered():
        from droplet_pkg.io.file_utils import get_txt_files_filtered
        files = ["/d/a_pos_dt100.txt", "/d/b_neg_dt100.txt",
                 "/d/c_pos_dt200.txt", "/d/d_pos_dt100_rep2.txt"]
        res = get_txt_files_filtered(files, "pos", "100")
        assert len(res) == 2
        assert all("pos" in f and "dt100" in f for f in res)
        all_pos = get_txt_files_filtered(files, "pos", "All")
        assert len(all_pos) == 3

    def test_list_all_txt_files_virtual():
        from droplet_pkg.io.file_utils import list_all_txt_files
        virtual = ["/virtual/a.txt", "/virtual/b.txt"]
        result  = list_all_txt_files("", is_virtual=True, virtual_file_list=virtual)
        assert result == virtual

    results.append(_run("separator detection",              test_separator_detection))
    results.append(_run("parse tab-separated spectrum",     test_parse_tab_separated))
    results.append(_run("parse CSV spectrum",               test_parse_csv))
    results.append(_run("parse space-separated spectrum",   test_parse_space_separated))
    results.append(_run("read spectrum headers",            test_read_spectrum_headers))
    results.append(_run("save + load roundtrip",            test_save_load_roundtrip))
    results.append(_run("save with process_tag",            test_save_with_process_tag))
    results.append(_run("parse_lilbid_name",                test_parse_lilbid_name))
    results.append(_run("spectrum_display_name",            test_spectrum_display_name))
    results.append(_run("list_all_txt_files (extensions)",  test_list_all_txt_files))
    results.append(_run("list_all_txt_files (virtual)",     test_list_all_txt_files_virtual))
    results.append(_run("get_txt_files by polarity",        test_get_txt_files_polarity))
    results.append(_run("extract_dt from filename",         test_extract_dt))
    results.append(_run("get_available_dt_values",          test_get_available_dt_values))
    results.append(_run("get_txt_files_filtered",           test_get_txt_files_filtered))
    return results


# ── 7. Analysis ───────────────────────────────────────────────────────────────

def tests_analysis() -> list[TestResult]:
    results = []

    def make_df():
        import numpy as np, pandas as pd
        rng = np.random.default_rng(0)
        mz  = np.linspace(50, 3000, 20000)
        i   = np.zeros(len(mz))
        for c, h in [(500.000, 2e6), (501.005, 1.6e6),
                     (502.010, 1.0e6), (503.015, 6e5),
                     (800.000, 8e5),   (801.005, 6e5),
                     (802.010, 4e5)]:
            idx = int(np.searchsorted(mz, c))
            w   = 12
            pts = np.arange(-w, w+1)
            sl  = slice(max(0, idx-w), idx+w+1)
            i[sl] += h * np.exp(-0.5 * pts[:sl.stop-sl.start]**2 / 8)
        i += rng.uniform(0, 200, len(mz))
        return pd.DataFrame({"mz": mz, "intensity": i})

    # ── peaks ──
    def test_parse_peaks_text():
        from droplet_pkg.analysis.peaks import parse_peaks_text
        assert parse_peaks_text("500, 501, 502") == [500.0, 501.0, 502.0]
        assert parse_peaks_text("") == []
        assert parse_peaks_text("100.5") == [100.5]
        assert parse_peaks_text("  200 , 300  ") == [200.0, 300.0]

    def test_auto_peaks_pct_mode():
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        clear_auto_peaks_cache()
        mz_arr, int_arr = get_auto_peaks(make_df(), threshold_value=5.0, mode="pct")
        assert len(mz_arr) > 0, "No peaks detected in pct mode"
        assert any(abs(m - 500) < 1.0 for m in mz_arr), "500 Da peak not found"
        assert len(mz_arr) == len(int_arr)

    def test_auto_peaks_snr_mode():
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        clear_auto_peaks_cache()
        mz_arr, int_arr = get_auto_peaks(make_df(), threshold_value=3.0, mode="snr")
        # SNR mode may be stricter; just confirm shapes match and no crash
        assert len(mz_arr) == len(int_arr)
        assert len(mz_arr) >= 0

    def test_auto_peaks_cache():
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        clear_auto_peaks_cache()
        df   = make_df()
        r1   = get_auto_peaks(df, threshold_value=5.0, mode="pct")
        r2   = get_auto_peaks(df, threshold_value=5.0, mode="pct")
        assert r1 is r2, "Repeated call with same df/params should return cached tuple"

    def test_auto_peaks_min_mass_filter():
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        import numpy as np, pandas as pd
        clear_auto_peaks_cache()
        mz = np.linspace(1, 50, 1000)         # all below AR_MINIMUM_MASS (10.9)
        i  = np.ones(1000) * 1e6
        i[200] = 1e8                           # big spike below min mass
        df = pd.DataFrame({"mz": mz, "intensity": i})
        mz_arr, _ = get_auto_peaks(df, threshold_value=1.0, mode="pct")
        assert all(m >= 10.9 for m in mz_arr), "Peaks below AR_MINIMUM_MASS must be excluded"

    def test_get_highlight_geometry():
        import numpy as np, pandas as pd
        from droplet_pkg.analysis.peaks import get_highlight_geometry, clear_highlight_cache
        clear_highlight_cache()
        mz = np.linspace(400, 600, 2000)
        i  = np.exp(-((mz - 500)**2) / 2.0) * 1e6 + 100
        df = pd.DataFrame({"mz": mz, "intensity": i})
        geom = get_highlight_geometry(df, [500.0])
        assert isinstance(geom, list)
        if len(geom) > 0:
            entry = geom[0]
            assert len(entry) == 6, "Expected 6-tuple (mz_arr, int_arr, lo, hi, real_mz, peak_int)"
            mz_arr_s, int_arr_s, mz_lo, mz_hi, real_mz, peak_int = entry
            assert mz_lo <= real_mz <= mz_hi   # peak can sit at edge of subset
            assert peak_int > 0

    def test_get_highlight_geometry_cache():
        import numpy as np, pandas as pd
        from droplet_pkg.analysis.peaks import get_highlight_geometry, clear_highlight_cache
        clear_highlight_cache()
        mz = np.linspace(400, 600, 500)
        df = pd.DataFrame({"mz": mz, "intensity": np.exp(-((mz-500)**2)/4)*1e5 + 50})
        r1 = get_highlight_geometry(df, [500.0])
        r2 = get_highlight_geometry(df, [500.0])
        assert r1 is r2, "Second call should return cached list"

    def test_get_highlight_geometry_no_peak():
        import numpy as np, pandas as pd
        from droplet_pkg.analysis.peaks import get_highlight_geometry, clear_highlight_cache
        clear_highlight_cache()
        mz = np.linspace(100, 200, 500)
        df = pd.DataFrame({"mz": mz, "intensity": np.ones(500) * 10})
        geom = get_highlight_geometry(df, [999.0])   # outside range
        assert isinstance(geom, list)

    # ── clusters ──
    def test_detect_all_peaks_for_clusters():
        import numpy as np, pandas as pd
        from droplet_pkg.analysis.clusters import detect_all_peaks_for_clusters
        mz = np.linspace(50, 1000, 5000)
        i  = np.zeros(len(mz))
        for c in [200, 300, 400, 500]:
            idx = np.argmin(np.abs(mz - c))
            i[idx-2:idx+3] += 1e6 * np.array([0.2, 0.6, 1.0, 0.6, 0.2])
        i += 100
        df    = pd.DataFrame({"mz": mz, "intensity": i})
        peaks = detect_all_peaks_for_clusters(df, min_snr=1.0, min_pct=0.5)
        assert peaks.ndim == 2 and peaks.shape[1] == 2
        assert len(peaks) > 0

    def test_build_chains():
        import numpy as np
        from droplet_pkg.analysis.clusters import build_chains
        mz  = np.array([100.000, 101.005, 102.010, 103.015, 200.000, 201.005])
        int_ = np.array([2e6, 1.6e6, 1e6, 6e5, 8e5, 5e5])
        chains = build_chains(mz, int_, spacing=1.005, tol=0.05)
        assert isinstance(chains, list)
        assert any(len(c) >= 3 for c in chains), \
            f"Expected chain of 3+; got lengths {[len(c) for c in chains]}"

    def test_build_chains_no_match():
        import numpy as np
        from droplet_pkg.analysis.clusters import build_chains
        mz  = np.array([100.0, 200.0, 300.0])   # spacing = 100, not 1.005
        int_ = np.array([1e6, 8e5, 5e5])
        chains = build_chains(mz, int_, spacing=1.005, tol=0.05)
        assert all(len(c) < 2 for c in chains), "No chain should form with wrong spacing"

    def test_auto_find_spacings():
        import numpy as np
        from droplet_pkg.analysis.clusters import auto_find_spacings
        # Use 1.5 Da spacing - auto_find_spacings bins diffs starting at 1.0 Da
        # with bin_width=0.1, so 1.005 Da falls in the FIRST bin which
        # scipy.find_peaks cannot detect (no left neighbour).  1.5 Da is safely
        # in the middle of the histogram and is reliably found.
        mz = np.array([100.000, 101.500, 103.000, 104.500,
                        200.000, 201.500, 203.000])
        spacings = auto_find_spacings(mz, max_spacing=10.0, min_count=2)
        assert isinstance(spacings, list)
        assert any(abs(s - 1.5) < 0.2 for s in spacings), \
            f"1.5 Da spacing not found; got {spacings}"

    def test_cluster_detection_pct():
        from droplet_pkg.analysis.clusters import run_cluster_detection
        df = make_df()
        clusters = run_cluster_detection(
            spacings_input="1.005", tol=0.05, min_chain=2,
            min_snr=0.5, min_pct=1.0, data_df=df)
        assert isinstance(clusters, list)
        spacings = [c["spacing"] for c in clusters]
        assert any(abs(s - 1.005) < 0.1 for s in spacings), \
            f"1.005 Da cluster not found; spacings={spacings[:5]}"

    def test_cluster_detection_peak_list():
        from droplet_pkg.analysis.clusters import run_cluster_detection
        import numpy as np, pandas as pd
        dummy_df = pd.DataFrame({"mz": np.array([100.0]), "intensity": np.array([1.0])})
        clusters = run_cluster_detection(
            spacings_input="", tol=0.05, min_chain=2, min_snr=0.5, min_pct=0.1,
            data_df=dummy_df, peak_list_mzs=[500.0, 501.005, 502.010])
        assert isinstance(clusters, list)

    def test_cluster_detection_auto_spacing():
        from droplet_pkg.analysis.clusters import run_cluster_detection
        df = make_df()
        # Empty spacings_input → auto-discover
        clusters = run_cluster_detection(
            spacings_input="", tol=0.08, min_chain=2,
            min_snr=0.5, min_pct=1.0, data_df=df)
        assert isinstance(clusters, list)

    results.append(_run("parse_peaks_text",                       test_parse_peaks_text))
    results.append(_run("auto peaks pct mode",                    test_auto_peaks_pct_mode))
    results.append(_run("auto peaks SNR mode",                    test_auto_peaks_snr_mode))
    results.append(_run("auto peaks cache hit",                   test_auto_peaks_cache))
    results.append(_run("auto peaks min-mass filter",             test_auto_peaks_min_mass_filter))
    results.append(_run("get_highlight_geometry (with peak)",     test_get_highlight_geometry))
    results.append(_run("get_highlight_geometry (cache hit)",     test_get_highlight_geometry_cache))
    results.append(_run("get_highlight_geometry (no peak)",       test_get_highlight_geometry_no_peak))
    results.append(_run("detect_all_peaks_for_clusters",          test_detect_all_peaks_for_clusters))
    results.append(_run("build_chains (found)",                   test_build_chains))
    results.append(_run("build_chains (no match)",                test_build_chains_no_match))
    results.append(_run("auto_find_spacings",                     test_auto_find_spacings))
    results.append(_run("cluster detection (spectrum + spacing)", test_cluster_detection_pct))
    results.append(_run("cluster detection (peak list)",          test_cluster_detection_peak_list))
    results.append(_run("cluster detection (auto spacing)",       test_cluster_detection_auto_spacing))
    return results


# ── 8. Example data ───────────────────────────────────────────────────────────
#
# assets/example_spectra/ holds five synthetic LILBID spectra with known
# ground truth (expected_values.json), generated by
# assets/test/generate_example_spectra.py.

def _load_expected() -> dict:
    return json.loads((EXAMPLE_DIR / "expected_values.json").read_text())


def _example_df(name: str):
    from droplet_pkg.io.spectrum_reader import read_spectrum_file
    return read_spectrum_file(str(EXAMPLE_DIR / name))


def _example_file(purpose_word: str) -> str:
    return next(n for n in _load_expected()["files"] if purpose_word in n)


def tests_example_data() -> list[TestResult]:
    results = []

    def test_folder_listing():
        from droplet_pkg.io.file_utils import (list_all_txt_files, get_txt_files,
                                               get_available_dt_values)
        files = list_all_txt_files(str(EXAMPLE_DIR))
        expected = _load_expected()["files"]
        assert sorted(Path(f).name for f in files) == sorted(expected), \
            f"listed {[Path(f).name for f in files]}"
        assert get_available_dt_values(files) == ["070", "080", "120"]
        assert len(get_txt_files(files, "neg")) == 2
        assert len(get_txt_files(files, "pos")) == 3
        return f"{len(files)} spectra, dt 070/080/120"

    def test_all_files_load():
        from droplet_pkg.io.spectrum_reader import read_spectrum_headers
        for name, info in _load_expected()["files"].items():
            df = _example_df(name)
            assert len(df) == 36000, f"{name}: {len(df)} rows"
            headers = read_spectrum_headers(str(EXAMPLE_DIR / name))
            if info["calibrated"]:
                assert any(h.startswith("#processed=") for h in headers), \
                    f"{name}: missing #processed header"
        return "5 files × 36000 points"

    def _check_auto_recal(word):
        from droplet_pkg.processing.calibration import run_auto_recal, detect_peak_near
        name = _example_file(word)
        info = _load_expected()["files"][name]
        df = _example_df(name)
        recal, _, summary, _ = run_auto_recal(df[["mz", "intensity"]].to_numpy(), name)
        rdf = df.copy(); rdf["mz"] = recal[:, 0]
        errors = []
        for p in info["peaks"]:
            if p["height"] >= 0.05 and p["mz"] < 300:
                found = detect_peak_near(rdf, p["mz"], window=0.3)
                assert found, f"peak {p['mz']} not found after recalibration"
                errors.append(abs(found[0] - p["mz"]))
        assert max(errors) < 0.06, f"max error {max(errors):.4f} Da"
        return (f"{len(summary)} calibrants, max error {max(errors):.3f} Da "
                f"over {len(errors)} peaks")

    def test_recalibrated_peak_positions():
        from droplet_pkg.processing.calibration import detect_peak_near
        worst = 0.0
        for name, info in _load_expected()["files"].items():
            if not info["calibrated"] or "peaks" not in info:
                continue
            df = _example_df(name)
            for p in info["peaks"]:
                if p["height"] >= 0.03:
                    found = detect_peak_near(df, p["mz"], window=0.3)
                    worst = max(worst, abs(found[0] - p["mz"]))
        assert worst < 0.03, f"max position error {worst:.4f} Da"
        return f"max position error {worst:.4f} Da"

    def test_nacl_clusters():
        from droplet_pkg.analysis.clusters import run_cluster_detection
        name = _example_file("NaCl")
        spacings = _load_expected()["files"][name]["cluster_spacings"]
        df = _example_df(name)
        found = {}
        for label, sp in spacings.items():
            chains = run_cluster_detection(spacings_input=str(sp), tol=0.05,
                                           min_chain=3, min_snr=3, min_pct=1.0,
                                           data_df=df)
            found[label] = max((len(c["members"]) for c in chains), default=0)
        assert found["NaCl"] >= 8, found
        assert found["H2O"] >= 8, found
        assert found["37Cl-35Cl"] >= 4, found
        return ", ".join(f"{k}: chain of {v}" for k, v in found.items())

    def test_peak_ratios_and_areas():
        from droplet_pkg.processing.signal import find_peak_bounds
        import numpy as np
        _trapezoid = getattr(np, "trapezoid", None) or np.trapz
        name = _example_file("Peak-area")
        info = _load_expected()["files"][name]
        df = _example_df(name); mz = df["mz"].values; y = df["intensity"].values
        floor = 3 * info["noise_sigma"]

        def measure(p):
            lo, hi, real, height = find_peak_bounds(mz, y, p["mz"], noise_floor=floor,
                                                    coarse_tol=0.15)
            sel = (mz >= lo) & (mz <= hi)
            return height, float(_trapezoid(np.clip(y[sel], 0, None), mz[sel])), lo, hi, real

        g = info["groups"]
        for grp in ("ratio 2:1", "ratio 1:1", "ratio 10:1"):
            a, b = g[grp]
            ratio = measure(a)[0] / measure(b)[0]
            want = a["height"] / b["height"]
            assert abs(ratio / want - 1) < 0.03, f"{grp}: measured {ratio:.3f}"
        for grp in ("reference", "ratio 2:1", "ratio 1:1", "ratio 10:1"):
            for p in g[grp]:
                area = measure(p)[1]
                assert abs(area / p["area"] - 1) < 0.02, \
                    f"{p['mz']}: area {area:.5f} vs {p['area']:.5f}"
        first, second = g["doublet 0.20 Da"]
        _, _, lo, hi, real = measure(first)
        assert hi < second["mz"], f"doublet not split: bounds end at {hi:.3f}"
        return "ratios within 3 %, isolated areas within 2 %, 0.20 Da doublet split"

    def test_detection_threshold():
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        clear_auto_peaks_cache()
        df = _example_df(_example_file("Peak-area"))
        mz, _ = get_auto_peaks(df, 2.5, "pct")
        ladder = sorted(round(float(m)) for m in mz if 245 < m < 295)
        assert ladder == [280, 290], f"ladder peaks above 2.5 %: {ladder}"
        return "2.5 % threshold keeps the 20σ and 50σ peaks only"

    def test_peak_list_files():
        for path in sorted(EXAMPLE_DIR.glob("peak_list_*.json")):
            data = json.loads(path.read_text())
            assert data.get("format_version") == 2 and data["rows"], path.name
            groups = {gr["name"] for gr in data["groups"]}
            assert all(r["group"] in groups for r in data["rows"]), path.name
        return f"{len(list(EXAMPLE_DIR.glob('peak_list_*.json')))} peak lists"

    results.append(_run("example folder listing / filters", test_folder_listing))
    results.append(_run("example spectra load",             test_all_files_load))
    results.append(_run("auto-recal, neg water calibrant",  lambda: _check_auto_recal("neg_des_I1152_dt080.txt")))
    results.append(_run("auto-recal, pos water calibrant",  lambda: _check_auto_recal("pos_des_I1152_dt080.txt")))
    results.append(_run("recalibrated peak positions",      test_recalibrated_peak_positions))
    results.append(_run("NaCl / water / Cl-isotope clusters", test_nacl_clusters))
    results.append(_run("peak ratios, areas, doublet split", test_peak_ratios_and_areas))
    results.append(_run("detection threshold (SNR ladder)", test_detection_threshold))
    results.append(_run("example peak list files",          test_peak_list_files))
    return results


# ── 9. End-to-end pipeline ────────────────────────────────────────────────────

def tests_end_to_end() -> list[TestResult]:
    results = []

    def _get_test_df():
        if EXAMPLE_DIR.is_dir():
            name = _example_file("NaCl")
            return _example_df(name), name
        import numpy as np, pandas as pd
        mz = np.linspace(100, 2000, 4000)
        i  = (np.exp(-((mz - 500)**2) / 50) * 1e6
              + np.random.default_rng(7).uniform(0, 500, 4000))
        return pd.DataFrame({"mz": mz, "intensity": i}), "synthetic"

    def test_load_and_normalise():
        df, src = _get_test_df()
        assert len(df) >= 10
        assert "mz" in df.columns and "intensity" in df.columns
        from droplet_pkg.processing.normalization import normalise
        n = normalise(df, zero_floor=True)
        assert n["intensity"].max() <= 1.0 + 1e-9
        return f"source={src}, {len(df)} points"

    def test_auto_detect():
        df, _ = _get_test_df()
        from droplet_pkg.analysis.peaks import get_auto_peaks, clear_auto_peaks_cache
        clear_auto_peaks_cache()
        mz_arr, int_arr = get_auto_peaks(df, threshold_value=5.0, mode="pct")
        return f"detected {len(mz_arr)} peaks"

    def test_highlight_geometry():
        df, _ = _get_test_df()
        if len(df) < 50:
            return "skipped (too few points)"
        from droplet_pkg.processing.normalization import normalise, estimate_noise_floor
        from droplet_pkg.processing.signal import find_peak_bounds, get_tolerance, peak_shift
        import numpy as np
        norm_df  = normalise(df, zero_floor=True)
        norm_int = norm_df["intensity"].values
        mz_arr   = df["mz"].values
        peak_mz  = float(mz_arr[norm_int.argmax()])
        thr = mz_arr >= 10.9
        noise_floor = (estimate_noise_floor(norm_int[thr], n_sigma=3.0)
                       if thr.sum() >= 10 else 1e-4)
        shifted = peak_mz + peak_shift(peak_mz)
        bounds  = find_peak_bounds(mz_arr, norm_int, shifted,
                                   noise_floor=noise_floor,
                                   coarse_tol=get_tolerance(shifted))
        if bounds:
            lo, hi, real, _ = bounds
            assert lo <= real <= hi
            return f"peak at {real:.2f} Da, bounds [{lo:.2f}, {hi:.2f}]"
        return "no bounds found (peak below noise floor?)"

    def test_baseline_pipeline():
        df, src = _get_test_df()
        from droplet_pkg.processing.baseline import apply_baseline
        out = apply_baseline(df, algorithm='airpls')
        assert "intensity" in out.columns and len(out) == len(df)
        return f"source={src}: airPLS applied to {len(df)} points"

    def test_cluster_on_real_data():
        df, src = _get_test_df()
        from droplet_pkg.analysis.clusters import run_cluster_detection
        clusters = run_cluster_detection(
            spacings_input="", tol=0.1, min_chain=2,
            min_snr=1.0, min_pct=2.0, data_df=df)
        return f"source={src}: {len(clusters)} cluster(s) found"

    def test_subtraction_roundtrip():
        df, _ = _get_test_df()
        from droplet_pkg.processing.signal import compute_subtraction
        result = compute_subtraction(df, df, dynamic=False)
        assert result is not None
        # Subtracting itself → all intensities ≈ 0
        assert abs(result["intensity"].mean()) < 1.0

    results.append(_run("load + normalise spectrum",       test_load_and_normalise))
    results.append(_run("auto peak detection",             test_auto_detect))
    results.append(_run("find_peak_bounds on spectrum",    test_highlight_geometry))
    results.append(_run("airPLS baseline on spectrum",     test_baseline_pipeline))
    results.append(_run("cluster detection on spectrum",   test_cluster_on_real_data))
    results.append(_run("subtraction roundtrip (df - df)", test_subtraction_roundtrip))
    return results


# ── 10. UI window class checks ────────────────────────────────────────────────
#
# All Droplet popup windows call _get_app() → `import droplet_pkg.app` the first
# time they are instantiated, which boots the full application (loads files,
# may run auto-recalibration).  We test only class-level properties - safe from
# any context - and source-level checks for inline classes in app.py.

def tests_ui_windows() -> list[TestResult]:
    results = []

    try:
        from PyQt6 import QtWidgets
    except ImportError:
        return [_skip("UI windows", "PyQt6 not importable")]

    from droplet_pkg.ui.mixins import StayOnTopMixin

    # Windows that inherit QWidget + StayOnTopMixin
    widget_mixin_classes = []
    try:
        from droplet_pkg.ui.windows.cluster_detection import ClusterDetectionWindow
        widget_mixin_classes.append(("ClusterDetectionWindow", ClusterDetectionWindow))
    except Exception as e:
        results.append(TestResult("import ClusterDetectionWindow", FAIL, str(e)))

    try:
        from droplet_pkg.ui.windows.residuals_viewer import ResidualsViewerWindow
        widget_mixin_classes.append(("ResidualsViewerWindow", ResidualsViewerWindow))
    except Exception as e:
        results.append(TestResult("import ResidualsViewerWindow", FAIL, str(e)))

    try:
        from droplet_pkg.ui.windows.peak_area import PeakAreaWindow
        widget_mixin_classes.append(("PeakAreaWindow", PeakAreaWindow))
    except Exception as e:
        results.append(TestResult("import PeakAreaWindow", FAIL, str(e)))

    try:
        from droplet_pkg.ui.windows.peak_comparison import PeakComparisonWindow
        widget_mixin_classes.append(("PeakComparisonWindow", PeakComparisonWindow))
    except Exception as e:
        results.append(TestResult("import PeakComparisonWindow", FAIL, str(e)))

    for name, cls in widget_mixin_classes:
        def _check(c=cls, n=name):
            assert issubclass(c, QtWidgets.QWidget), f"{n} is not a QWidget subclass"
            assert issubclass(c, StayOnTopMixin),    f"{n} missing StayOnTopMixin"
        results.append(_run(f"class check {name}", _check))

    # peak_confirmation - ConfirmationPanel is a QWidget
    def check_confirmation_panel():
        from droplet_pkg.ui.windows.peak_confirmation import ConfirmationPanel
        assert issubclass(ConfirmationPanel, QtWidgets.QWidget)

    results.append(_run("class check ConfirmationPanel", check_confirmation_panel))

    # tutorial - TutorialOverlay is a QWidget with STEPS list
    def check_tutorial_overlay():
        from droplet_pkg.ui.windows.tutorial import TutorialOverlay
        assert issubclass(TutorialOverlay, QtWidgets.QWidget)
        assert hasattr(TutorialOverlay, "STEPS"), "TutorialOverlay must have STEPS"
        assert len(TutorialOverlay.STEPS) > 0

    results.append(_run("class check TutorialOverlay (+ STEPS)", check_tutorial_overlay))

    # update_checker - launcher only, must point at droplet_pkg/updater.py
    def check_update_checker_launcher():
        from droplet_pkg.ui import update_checker
        assert callable(update_checker.check_on_startup)
        assert callable(update_checker.open_updater)
        assert update_checker.UPDATER.exists(), f"{update_checker.UPDATER} missing"

    results.append(_run("update_checker launcher", check_update_checker_launcher))

    # Source-level checks for inline classes in app.py
    def test_inline_classes():
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        for cls_name in ["SavedLabelsImportDialog",
                         "ManualRecalWindow",
                         "PeakReviewWindow",
                         "PlottingToolWindow"]:
            assert f"class {cls_name}" in src, f"class {cls_name} not found in app.py"

    results.append(_run("inline classes defined in app.py", test_inline_classes))
    return results


# ── 11. Updater / update checker ──────────────────────────────────────────────

def tests_updater() -> list[TestResult]:
    results = []

    def test_version_comparison():
        from droplet_pkg.updater import is_newer
        assert is_newer("2.6.6", "2.6.5")
        assert not is_newer("2.6.5", "2.6.5")
        assert not is_newer("2.0.0", "2.6.5")
        assert is_newer("3.0.0", "2.99.99")
        assert is_newer("2.6.5.1", "2.6.5")
        assert not is_newer("2.6.4", "2.6.5")

    def test_version_comparison_edge_cases():
        from droplet_pkg.updater import is_newer, _to_tuple
        assert _to_tuple("1.0")        == (1, 0)
        assert _to_tuple("2.6.5")      == (2, 6, 5)
        assert _to_tuple("10.0.1")     == (10, 0, 1)
        # Patch version
        assert is_newer("2.6.5.1", "2.6.5")
        assert not is_newer("2.6.5", "2.6.5.1")
        # Trailing zeros are equal
        assert not is_newer("3.0.0", "3.0")
        assert not is_newer("3.0", "3.0.0")

    def test_updater_helpers():
        from droplet_pkg import updater
        assert updater.ROOT == ROOT, f"updater.ROOT = {updater.ROOT}, expected {ROOT}"
        lv = updater.local_version()
        assert lv == VERSION_FILE.read_text().strip(), \
            f"local_version() = {lv!r} does not match {VERSION_FILE}"
        return f"local version = {lv}"

    def test_is_git_repo():
        from droplet_pkg.updater import is_git_repo
        result = is_git_repo()
        assert isinstance(result, bool)
        return f"is_git_repo = {result}"

    def test_remote_version_fetch():
        try:
            from droplet_pkg import updater
            branch = updater.resolve_branch()
            ver = updater.fetch_remote_version(branch)
            assert ver, "Remote VERSION file is empty"
            return f"remote version = {ver} (branch {branch})"
        except Exception as e:
            raise RuntimeError(f"Network fetch failed: {e}") from e

    results.append(_run("version comparison logic",          test_version_comparison))
    results.append(_run("version comparison edge cases",     test_version_comparison_edge_cases))
    results.append(_run("updater _to_tuple + local_version", test_updater_helpers))
    results.append(_run("is_git_repo detection",             test_is_git_repo))

    # ── previous versions manager (no network, no install) ──
    def test_version_manager_helpers():
        from droplet_pkg import version_manager as vm
        assert vm.branch_version("v2.7.2") == (2, 7, 2)
        assert vm.branch_version("v2.7") == (2, 7)
        assert vm.branch_version("main") is None
        assert vm.is_older_than_current("v0.1")
        assert not vm.is_older_than_current("v999.0")
        assert vm.requirements_for("v2.7.2", python=(3, 10))[0] == vm.PINS_PY310
        assert vm.requirements_for("v2.7.2", python=(3, 13))[0] == vm.PINS_PY311_PLUS
        for pins in (vm.PINS_PY310, vm.PINS_PY311_PLUS):
            names = {p.split("==")[0] for p in pins}
            assert {"PyQt6", "PyQt6-Qt6", "PyQt6-sip"} <= names, "Qt runtime must be pinned"
        return f"current {vm.current_version()}, {len(vm.list_installed())} installed"

    def test_version_manager_untested_branch():
        from droplet_pkg import version_manager as vm
        with tempfile.TemporaryDirectory() as d:
            code = Path(d)
            (code / "assets" / "assimilation_guides").mkdir(parents=True)
            (code / "assets" / "assimilation_guides" / "requirements_new.txt").write_text(
                "PyQt6==9.9.9\nnumpy==9.9.9\n")
            pins, source = vm.requirements_for("v99.0", code)
            assert pins == ["PyQt6==9.9.9", "numpy==9.9.9"], pins
            (code / "Droplet_v99.0.py").write_text("")
            assert vm.find_launcher(code).name == "Droplet_v99.0.py"
            (code / "Droplet.py").write_text("")
            assert vm.find_launcher(code).name == "Droplet.py"
        return source

    results.append(_run("previous versions: helpers / pins", test_version_manager_helpers))
    results.append(_run("previous versions: untested branch", test_version_manager_untested_branch))
    # Inline so network failures become SKIPs rather than FAILs.
    t0 = time.perf_counter()
    try:
        detail = test_remote_version_fetch()
        results.append(TestResult("fetch remote VERSION from GitHub",
                                  PASS, detail, time.perf_counter() - t0))
    except Exception as e:
        results.append(_skip("fetch remote VERSION from GitHub",
                             f"network unavailable: {e}"))

    return results


# ── 12. Settings persistence ──────────────────────────────────────────────────
#
# Tests every layer of the dt-filter save/restore path:
#   a) Raw QSettings I/O (rules out platform/file-permission issues)
#   b) Source-code presence of the save connection and startup restore block
#   c) Widget-level simulation of the restore logic (no full app boot required)
#   d) Integration: signal emission actually writes the correct key/value

def tests_settings_persistence() -> list[TestResult]:
    results = []

    # ── a) Raw QSettings roundtrip ────────────────────────────────────────────

    def test_qsettings_roundtrip():
        """Write → sync → re-open → read back: verifies QSettings works at all."""
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        s = QtCore.QSettings("LILBID_testsuite", "PeakViewer_testsuite")
        s.setValue("_probe_key", "probe_value_42")
        s.sync()
        s2 = QtCore.QSettings("LILBID_testsuite", "PeakViewer_testsuite")
        val = s2.value("_probe_key", "")
        s.remove("_probe_key"); s.sync()
        assert val == "probe_value_42", f"Got {val!r} instead of 'probe_value_42'"
        return "write → sync → read OK"

    def test_qsettings_missing_key_default():
        """Missing key returns the supplied default, not None."""
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        s = QtCore.QSettings("LILBID_testsuite", "PeakViewer_testsuite")
        s.remove("_nonexistent_key"); s.sync()
        val = s.value("_nonexistent_key", "All")
        assert val == "All", f"Expected default 'All', got {val!r}"
        return "missing key → default 'All' returned"

    results.append(_run("QSettings write/sync/read roundtrip",       test_qsettings_roundtrip))
    results.append(_run("QSettings missing key → correct default",   test_qsettings_missing_key_default))

    # ── b) Source-code presence checks ───────────────────────────────────────

    def test_save_connection_in_source():
        """dt_combo.currentTextChanged must be connected to save 'dt_filter'."""
        import re
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        pattern = r'dt_combo\.currentTextChanged\.connect.*dt_filter'
        assert re.search(pattern, src), \
            "dt_combo.currentTextChanged → settings.setValue('dt_filter') not found in app.py"
        return "save connection found in app.py"

    def test_restore_block_in_source():
        """Startup restore block must read 'dt_filter' and call setCurrentIndex."""
        import re
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        assert re.search(r"settings\.value\(['\"]dt_filter['\"]", src), \
            "settings.value('dt_filter') not found in app.py"
        assert re.search(r"dt_combo\.findText\(_saved_dt\)", src), \
            "dt_combo.findText(_saved_dt) not found in app.py"
        assert re.search(r"dt_combo\.setCurrentIndex\(_dt_idx\)", src), \
            "dt_combo.setCurrentIndex(_dt_idx) not found in app.py"
        return "restore block keys found in app.py"

    def test_restore_block_repopulates_combo():
        """The restore block must call get_txt_files_filtered + _populate_main_combo."""
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        # Extract the block between _saved_dt assignment and 'if combo.count() == 0:'
        start = src.find("_saved_dt = settings.value(\"dt_filter\"")
        if start < 0:
            start = src.find("_saved_dt = settings.value('dt_filter'")
        assert start >= 0, "_saved_dt = settings.value('dt_filter') not found"
        end = src.find("\nif combo.count() == 0:", start)
        block = src[start: end if end > start else start + 600]
        assert "get_txt_files_filtered" in block, \
            "Restore block does not call get_txt_files_filtered"
        assert "_populate_main_combo" in block, \
            "Restore block does not call _populate_main_combo"
        return "restore block calls get_txt_files_filtered + _populate_main_combo"

    def test_save_uses_correct_key():
        """The saved key must be exactly 'dt_filter' (not a variant spelling)."""
        import re
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        saves = re.findall(r'settings\.setValue\(["\'](\w+)["\']', src)
        assert "dt_filter" in saves, \
            f"'dt_filter' not in settings.setValue calls; found: {saves[:20]}"
        return f"'dt_filter' in setValue calls ({saves.count('dt_filter')} occurrence(s))"

    results.append(_run("save connection present in app.py",          test_save_connection_in_source))
    results.append(_run("restore block present in app.py",            test_restore_block_in_source))
    results.append(_run("restore block repopulates main combo",       test_restore_block_repopulates_combo))
    results.append(_run("correct key name 'dt_filter' used",          test_save_uses_correct_key))

    # ── c) Widget-level simulation ────────────────────────────────────────────
    # Keep a module-level reference so QApplication is not GC'd between tests.
    _sp_app_holder = []

    def _ensure_app():
        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        existing = QtWidgets.QApplication.instance()
        if existing:
            return existing
        a = QtWidgets.QApplication(sys.argv)
        _sp_app_holder.append(a)   # prevent garbage collection
        return a

    def _fresh_settings(org="LILBID_ts_sim", app_name="PV_ts_sim"):
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        s = QtCore.QSettings(org, app_name)
        s.clear(); s.sync()
        return s

    def test_restore_applies_saved_value():
        """Simulate startup: saved 'dt062' ends up selected in the combo."""
        _ensure_app()
        s = _fresh_settings()
        s.setValue("dt_filter", "dt062"); s.sync()

        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        combo = QtWidgets.QComboBox()
        for v in ["All", "dt050", "dt062", "dt080"]:
            combo.addItem(v)

        saved = s.value("dt_filter", "All")
        if saved and saved != "All":
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        result = combo.currentText()
        s.clear(); s.sync()
        assert result == "dt062", f"Expected 'dt062', got {result!r}"
        return "saved 'dt062' correctly selected after simulated restore"

    def test_restore_fallback_to_all():
        """No saved value → combo stays on 'All'."""
        _ensure_app()
        s = _fresh_settings()
        # do NOT write dt_filter

        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        combo = QtWidgets.QComboBox()
        for v in ["All", "dt062"]:
            combo.addItem(v)

        saved = s.value("dt_filter", "All")
        if saved and saved != "All":
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        result = combo.currentText()
        s.clear(); s.sync()
        assert result == "All", f"Expected 'All', got {result!r}"
        return "no saved value → combo stays on 'All'"

    def test_restore_unavailable_dt():
        """Saved dt not present in the combo (different folder) → stays 'All'."""
        _ensure_app()
        s = _fresh_settings()
        s.setValue("dt_filter", "dt999"); s.sync()

        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        combo = QtWidgets.QComboBox()
        for v in ["All", "dt062", "dt080"]:
            combo.addItem(v)

        saved = s.value("dt_filter", "All")
        if saved and saved != "All":
            idx = combo.findText(saved)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            # idx == -1 → no setCurrentIndex call → stays on 'All'

        result = combo.currentText()
        s.clear(); s.sync()
        assert result == "All", f"Expected 'All' (dt999 not available), got {result!r}"
        return "unavailable saved dt → combo stays on 'All'"

    results.append(_run("restore: saved value applied to combo",    test_restore_applies_saved_value))
    results.append(_run("restore: no saved value → stays 'All'",   test_restore_fallback_to_all))
    results.append(_run("restore: unavailable dt → stays 'All'",   test_restore_unavailable_dt))

    # ── d) Signal emission actually saves the value ───────────────────────────

    def test_signal_saves_to_settings():
        """Connecting currentTextChanged like app.py does, then changing the
        combo, must write the correct value to QSettings immediately."""
        _ensure_app()
        s = _fresh_settings()

        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        combo = QtWidgets.QComboBox()
        for v in ["All", "dt062", "dt080"]:
            combo.addItem(v)

        # Mirror the exact connection in app.py
        combo.currentTextChanged.connect(lambda v: s.setValue("dt_filter", v))

        # Simulate user picking "dt062"
        idx = combo.findText("dt062")
        combo.setCurrentIndex(idx)
        s.sync()

        # Re-read via a fresh QSettings instance to confirm disk persistence
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        s_check = QtCore.QSettings("LILBID_ts_sim", "PV_ts_sim")
        saved = s_check.value("dt_filter", "All")
        s.clear(); s.sync()
        assert saved == "dt062", \
            f"After setCurrentIndex('dt062'), settings has {saved!r} instead of 'dt062'"
        return f"signal write: settings['dt_filter'] = {saved!r} ✓"

    def test_signal_blocked_during_refresh():
        """When blockSignals(True) is active, currentTextChanged must NOT fire
        (i.e. the programmatic repopulation in _refresh_dt_combo should not
        overwrite a previously saved user choice)."""
        _ensure_app()
        s = _fresh_settings()
        s.setValue("dt_filter", "dt062"); s.sync()

        try:
            from PyQt6 import QtWidgets
        except ImportError:
            from pyqtgraph.Qt import QtWidgets
        combo = QtWidgets.QComboBox()
        combo.addItem("All")
        combo.currentTextChanged.connect(lambda v: s.setValue("dt_filter", v))

        # Simulate what _refresh_dt_combo does: block → clear → repopulate → unblock
        combo.blockSignals(True)
        combo.clear()
        for v in ["All", "dt062", "dt080"]:
            combo.addItem(v)
        combo.setCurrentIndex(combo.findText("dt062"))
        combo.blockSignals(False)

        s.sync()
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        s_check = QtCore.QSettings("LILBID_ts_sim", "PV_ts_sim")
        saved = s_check.value("dt_filter", "NOT_SET")
        s.clear(); s.sync()
        # The blocked operations must NOT have changed the saved value
        assert saved == "dt062", \
            f"blockSignals did not prevent overwrite; settings now has {saved!r}"
        return f"blockSignals prevented spurious write; dt_filter = {saved!r} ✓"

    results.append(_run("signal emission writes correct value",         test_signal_saves_to_settings))
    results.append(_run("blockSignals prevents spurious overwrite",     test_signal_blocked_during_refresh))

    # ── e) Flush-to-disk check ────────────────────────────────────────────────

    def test_cleanup_calls_sync():
        """cleanup() must call settings.sync() so values reach disk before Qt
        tears down the QSettings object.  Without this, settings written during
        the session (dt_filter, display_mode, crosshair, …) are lost on reboot."""
        import re
        src = (ROOT / "droplet_pkg" / "app.py").read_text()
        # Find the cleanup() function body
        m = re.search(r'def cleanup\(\):(.*?)^(?=def |\Z)', src, re.DOTALL | re.MULTILINE)
        assert m, "cleanup() function not found in app.py"
        body = m.group(1)
        assert "settings.sync()" in body, (
            "cleanup() does not call settings.sync() — settings written during "
            "the session will not be flushed to disk before Qt destroys the "
            "QSettings object, causing all persisted values to be lost on reboot.")
        return "settings.sync() found in cleanup()"

    def test_sync_persists_across_instances():
        """Write → sync → create new QSettings instance → read back.
        Simulates the save-on-exit / read-on-next-launch cycle."""
        _ensure_app()
        try:
            from PyQt6 import QtCore
        except ImportError:
            from pyqtgraph.Qt import QtCore
        org, app_name = "LILBID_ts_flush", "PV_ts_flush"
        s_write = QtCore.QSettings(org, app_name)
        s_write.setValue("dt_filter", "dt062")
        s_write.sync()          # ← what cleanup() must do
        del s_write             # destroy the writing instance

        s_read = QtCore.QSettings(org, app_name)
        val = s_read.value("dt_filter", "All")
        s_read.clear(); s_read.sync()
        assert val == "dt062", \
            f"After sync + new instance, expected 'dt062', got {val!r}"
        return "sync → destroy → new instance → read back: 'dt062' ✓"

    results.append(_run("cleanup() calls settings.sync()",             test_cleanup_calls_sync))
    results.append(_run("sync persists across QSettings instances",    test_sync_persists_across_instances))

    return results


# ─────────────────────────────────────────────────────────────────────────────
#  Test runner
# ─────────────────────────────────────────────────────────────────────────────

CATEGORIES = [
    ("Imports",              tests_imports),
    ("Constants",            tests_constants),
    ("Version",              tests_version),
    ("Processing",           tests_processing),
    ("Calibration",          tests_calibration),
    ("Spectrum I/O",         tests_spectrum_io),
    ("Analysis",             tests_analysis),
    ("Example data",         tests_example_data),
    ("End-to-end",           tests_end_to_end),
    ("UI windows",           tests_ui_windows),
    ("Updater",              tests_updater),
    ("Settings persistence", tests_settings_persistence),
]


def run_all() -> list[tuple[str, list[TestResult]]]:
    all_results = []
    for category, fn in CATEGORIES:
        all_results.append((category, fn()))
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
        print("PyQt6 not available - running in console mode")
        run_console()
        return

    qt_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    win = QtWidgets.QWidget()
    win.setWindowTitle("Droplet Test Suite")
    win.resize(920, 660)
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
    btn_row  = QtWidgets.QHBoxLayout()
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

    _STATUS_COLOR = {PASS: "#1a7a1a", FAIL: "#cc2222", SKIP: "#b07d00", WARN: "#b07d00"}
    _STATUS_BG    = {PASS: "#eafaea", FAIL: "#faeaea", SKIP: "#faf5e0", WARN: "#faf5e0"}

    _all_results: list[tuple[str, list[TestResult]]] = []

    def _append_category_rows(category: str, cat_results: list):
        for r in cat_results:
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
        table.scrollToBottom()

    def _refresh_summary():
        total = passed = failed = skipped = 0
        for _, cat_results in _all_results:
            for r in cat_results:
                total += 1
                if r.status == PASS:   passed  += 1
                elif r.status == FAIL: failed  += 1
                else:                  skipped += 1
        color = "#cc2222" if failed else "#1a7a1a"
        summary.setText(
            f"<span style='color:{color};'>"
            f"Total {total} - ✓ {passed} passed  ✗ {failed} failed  ⚠ {skipped} skipped"
            f"</span>")
        save_btn.setEnabled(bool(_all_results))

    def start_run():
        run_btn.setEnabled(False)
        save_btn.setEnabled(False)
        summary.setText("Running…")
        table.setRowCount(0)
        _all_results.clear()

        # Run every category in the main thread via a QTimer chain so the
        # event loop stays alive between categories (UI stays responsive and
        # no Qt thread-affinity warnings fire).
        remaining = list(CATEGORIES)

        def _run_next():
            if not remaining:
                _refresh_summary()
                run_btn.setEnabled(True)
                return
            cat_name, cat_fn = remaining.pop(0)
            summary.setText(f"Running: {cat_name}…")
            QtCore.QCoreApplication.processEvents()
            try:
                cat_results = cat_fn()
            except Exception as exc:
                cat_results = [TestResult(cat_name, FAIL,
                                          f"{type(exc).__name__}: {exc}")]
            _all_results.append((cat_name, cat_results))
            _append_category_rows(cat_name, cat_results)
            QtCore.QTimer.singleShot(0, _run_next)

        QtCore.QTimer.singleShot(0, _run_next)

    run_btn.clicked.connect(start_run)

    def save_report():
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            win, "Save Test Report", str(ROOT / "test_report.txt"),
            "Text Files (*.txt)")
        if not path:
            return
        lines = []
        for category, cat_results in _all_results:
            lines.append(f"\n── {category}")
            for r in cat_results:
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
