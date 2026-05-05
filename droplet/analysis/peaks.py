"""Peak detection and highlight-geometry helpers.

Functions are pure except for the caches (module-level dicts) which the app
resets when the spectrum changes. The caches depend on DataFrame identity
(id(df)) so they are safe to use across multiple calls with the same object.
"""

import numpy as np
from droplet.processing.signal import get_tolerance, peak_shift
from droplet.processing.calibration import AR_MINIMUM_MASS

# ── Caches ────────────────────────────────────────────────────────────────────
_auto_peaks_cache: dict = {}
_highlight_cache:  dict = {}


def clear_auto_peaks_cache():
    _auto_peaks_cache.clear()


def clear_highlight_cache():
    _highlight_cache.clear()


def clear_all_peak_caches():
    clear_auto_peaks_cache()
    clear_highlight_cache()


# ── Parsing ───────────────────────────────────────────────────────────────────

def parse_peaks_text(text):
    """Parse a comma-separated string of m/z values into a list of floats."""
    if not text.strip():
        return []
    try:
        return [float(v.strip()) for v in text.split(",") if v.strip()]
    except ValueError:
        return []


# ── Peak detection ────────────────────────────────────────────────────────────

def get_auto_peaks(data_df, threshold_value, mode="pct"):
    """Detect peaks above a threshold, with caching.

    mode='pct' → threshold_value is % of max intensity (0-100)
    mode='snr' → threshold_value is a minimum signal-to-noise ratio
    Returns (mz_array, intensity_array).
    """
    key = (id(data_df), threshold_value, mode)
    if key not in _auto_peaks_cache:
        from scipy.signal import find_peaks as _fp
        int_arr = data_df['intensity'].values
        mz_arr  = data_df['mz'].values
        mx = int_arr.max()
        if mx == 0:
            _auto_peaks_cache[key] = (np.array([]), np.array([]))
        else:
            if mode == "snr":
                rough_threshold = 0.001 * mx
                indices, _ = _fp(int_arr, distance=5, height=rough_threshold)
                if len(indices) == 0:
                    _auto_peaks_cache[key] = (np.array([]), np.array([]))
                else:
                    LOCAL = 5.0
                    kept_mz  = []
                    kept_int = []
                    for i in indices:
                        mz = mz_arr[i]; intensity = int_arr[i]
                        if mz < AR_MINIMUM_MASS:
                            continue
                        mask = (mz_arr >= mz - LOCAL) & (mz_arr <= mz + LOCAL)
                        noise = np.percentile(int_arr[mask], 25) if mask.sum() > 4 else 0.0
                        if noise <= 0:
                            noise = 1e-9
                        if intensity / noise >= threshold_value:
                            kept_mz.append(mz)
                            kept_int.append(intensity)
                    _auto_peaks_cache[key] = (np.array(kept_mz), np.array(kept_int))
            else:
                threshold = (threshold_value / 100.0) * mx
                indices, _ = _fp(int_arr, distance=5, height=threshold)
                if len(indices) == 0:
                    _auto_peaks_cache[key] = (np.array([]), np.array([]))
                else:
                    mask = mz_arr[indices] >= AR_MINIMUM_MASS
                    _auto_peaks_cache[key] = (mz_arr[indices][mask], int_arr[indices][mask])
    return _auto_peaks_cache[key]


# ── Highlight geometry ────────────────────────────────────────────────────────

def get_highlight_geometry(data_df, peaks_flat):
    """Return highlight region data for each peak in peaks_flat, with caching."""
    key = (id(data_df), tuple(peaks_flat))
    if key in _highlight_cache:
        return _highlight_cache[key]

    results = []
    for p in peaks_flat:
        shifted_p = p + peak_shift(p)
        tol = get_tolerance(shifted_p); backward = 0.2
        idx = ((data_df["mz"] >= shifted_p - backward) &
               (data_df["mz"] <= shifted_p + tol))
        if not idx.any():
            continue
        candidates  = data_df[idx]
        max_idx     = candidates["intensity"].idxmax()
        all_indices = candidates.index.to_numpy()
        max_pos     = np.where(all_indices == max_idx)[0][0]
        n = 10
        start = max(0, max_pos - n); end = min(len(candidates), max_pos + n + 1)
        subset   = candidates.iloc[start:end]
        mz_arr   = subset["mz"].to_numpy()
        int_arr  = subset["intensity"].to_numpy()
        peak_mz  = candidates.loc[max_idx, "mz"]
        peak_int = candidates.loc[max_idx, "intensity"]
        results.append((mz_arr, int_arr,
                        subset["mz"].min(), subset["mz"].max(),
                        peak_mz, peak_int))
    _highlight_cache[key] = results
    return results
