"""Spectrum normalization utilities.

Pure functions: take DataFrames, return DataFrames.
The module-level cache (_norm_cache) lives here and is cleared by the app
when the spectrum changes.
"""

import numpy as np

_DYN_CLIP_FLOOR = 2.5e-3
_NORM_LOG_FLOOR = 0.007

_norm_cache: dict = {}


def clear_norm_cache():
    _norm_cache.clear()


def normalise(data_df, zero_floor=False):
    out = data_df.copy()
    if zero_floor:
        mn = out['intensity'].min()
        out['intensity'] = out['intensity'] - mn + 1e-6
    mx = out['intensity'].max()
    if mx == 0:
        return out
    out['intensity'] = out['intensity'] / mx
    return out


def normalise_cached(data_df, zero_floor=False):
    key = (id(data_df), zero_floor)
    if key not in _norm_cache:
        _norm_cache[key] = normalise(data_df, zero_floor)
    return _norm_cache[key]


def estimate_noise_floor(intensity_arr, n_sigma=3.0):
    """Estimate noise baseline via sigma-clipping.

    n_sigma=1 → gentle clipping (suppresses only flat baseline)
    n_sigma=3 → conservative area floor (classical 3-sigma detection limit)
    Falls back to _DYN_CLIP_FLOOR when there are too few points.
    """
    try:
        data = intensity_arr[intensity_arr > 0]
        if len(data) < 10:
            return _DYN_CLIP_FLOOR
        threshold = np.percentile(data, 50)
        noise = data[data <= threshold]
        if len(noise) < 5:
            return _DYN_CLIP_FLOOR
        med = np.median(noise)
        std = np.std(noise)
        noise = noise[np.abs(noise - med) < 3.0 * std]
        if len(noise) < 3:
            return _DYN_CLIP_FLOOR
        floor = float(np.median(noise) + n_sigma * np.std(noise))
        return min(floor, 0.05)
    except Exception:
        return _DYN_CLIP_FLOOR


def sigma3_floor(intensity_arr):
    return estimate_noise_floor(intensity_arr, n_sigma=1.0)


def normalise_and_clip(data_df):
    """Normalise to [0,1] with zero-floor, then suppress noise below sigma-clipped baseline."""
    out = normalise_cached(data_df, zero_floor=True)
    out = out.copy()
    floor = sigma3_floor(out['intensity'].values)
    out['intensity'] = np.where(
        out['intensity'] < floor,
        floor,
        out['intensity'])
    return out


def normalize_df(data_df, mode='max', peak_mz=None, apply_log_floor=False):
    """Normalize a spectrum DataFrame.

    mode='max'  → subtract minimum, divide by maximum.
    mode='peak' → subtract minimum, divide by intensity at peak_mz.
    apply_log_floor: clamp to _NORM_LOG_FLOOR - use only when log Y is active.
    """
    out = data_df.copy()
    int_arr = out['intensity'].values.astype(float)
    int_arr = int_arr - int_arr.min()

    if mode == 'peak' and peak_mz is not None:
        idx = (out['mz'] - peak_mz).abs().idxmin()
        scale = float(int_arr[out.index.get_loc(idx)])
    else:
        scale = float(int_arr.max())

    if scale == 0:
        if apply_log_floor:
            out['intensity'] = np.clip(int_arr, _NORM_LOG_FLOOR, None)
        else:
            out['intensity'] = int_arr
        return out

    int_arr = int_arr / scale
    if apply_log_floor:
        int_arr = np.clip(int_arr, _NORM_LOG_FLOOR, None)
    out['intensity'] = int_arr
    return out
