"""Mass-spectrometry calibration algorithms.

Contains:
  - Auto-recalibration (_ar_*) adapted from LILBID_GUI by M. Umair:
    https://github.com/mumair5393/LILBID_GUI/blob/main/automated_recalibration.py
  - Manual recalibration helpers (_apply_manual_recal_pairs, _detect_peak_near)

All functions are pure (numpy/scipy only, no Qt / no application state).
"""

import os
import numpy as np
import pandas as pd

# ── Ion tables ────────────────────────────────────────────────────────────────
_IONS_NEG = {
    'integer mass': [17, 35, 43, 46, 53, 71, 89, 107, 125, 143, 161, 179, 197, 215],
    'exact mass':   [17.0033, 35.0139, 43.0189, 46.0060, 53.0244, 71.0350,
                     89.0455, 107.0561, 125.0667, 143.0772,
                     161.0878, 179.0984, 197.1089, 215.1195],
}
_IONS_POS = {
    'integer mass': [18, 19, 23, 30, 36, 37, 41, 43, 55, 59, 60,
                     73, 77, 91, 95, 109, 113, 127, 131, 145,
                     149, 163, 167, 181, 185, 199, 203, 217, 221, 235],
    'exact mass':   [18.0338, 19.0178, 22.9898, 30.0338, 36.0444, 37.0284,
                     41.0003, 43.0178, 55.0390, 59.0109, 60.0570,
                     73.0495, 77.0215, 91.0601, 95.0320, 109.0707,
                     113.0426, 127.0812, 131.0532, 145.0918,
                     149.0637, 163.1024, 167.0743, 181.1129,
                     185.0849, 199.1235, 203.0954, 217.1341, 221.1060, 235.1446],
}

# ── Algorithm constants ───────────────────────────────────────────────────────
AR_MINIMUM_MASS   = 10.9
AR_MAX_DATAPOINT  = 50000
AR_MAX_MASSDIFF   = 0.5
AR_PERCENTILE_RANGE = 180
AR_PERCENTILE     = 80
AR_STD_FACTOR     = 0.2
AR_STD_BOT        = 500
AR_STD_TOP        = 2000
AR_MASS_BIN_WIDTH = 0.6
AR_BINNING_ITERS  = 15
AR_PW_MIN         = 1
AR_PW_MAX         = 28
AR_PW_STEP        = 4
AR_REL_INT_THR    = 0.0006
AR_REL_INT_ITERS  = 3
AR_TIMESTEP       = 2e-3


# ── Auto-recalibration ────────────────────────────────────────────────────────

def _ar_get_ions(ion_mode):
    return _IONS_POS if ion_mode == 'pos' else _IONS_NEG


def _ar_check_peak_validation(mass, max_diff):
    return abs(mass - round(mass)) <= max_diff


def _ar_get_local_percentile(data, peak_indices, prange, pct):
    n = data.shape[0]; out = []
    for idx in peak_indices:
        lo = max(0, idx - prange); hi = min(n, idx + prange + 1)
        out.append(float(np.percentile(data[lo:hi, 1], pct)))
    return np.array(out)


def _ar_automatic_threshold(data, local_perc, std_bot, std_top, std_factor):
    n = data.shape[0]; lo = min(std_bot, n - 1); hi = min(std_top, n)
    return local_perc + std_factor * float(np.std(data[lo:hi, 1]))


def _ar_delete_false_peaks(to_delete, peaks, peak_indices, local_perc):
    to_delete = np.unique(to_delete).astype(int)
    mask = np.ones(len(peaks), dtype=bool)
    valid = to_delete[to_delete < len(peaks)]; mask[valid] = False
    return np.array([], dtype=int), peaks[mask], peak_indices[mask], local_perc[mask]


def _ar_calc_relative_intensities(peaks, local_perc):
    return peaks[:, 1] / np.maximum(local_perc, 1e-12)


def _ar_detect_peaks(data):
    import scipy.signal as ss
    chunk = data[:AR_MAX_DATAPOINT, 1]
    widths = np.arange(AR_PW_MIN, AR_PW_MAX, AR_PW_STEP)
    peak_indices = np.array(ss.find_peaks_cwt(chunk, widths), dtype=int)
    if peak_indices.size == 0:
        return np.empty((0, 2)), np.array([], dtype=int), np.array([])
    peaks      = np.column_stack([data[peak_indices, 0], data[peak_indices, 1]]).astype(float)
    local_perc = _ar_get_local_percentile(data, peak_indices, AR_PERCENTILE_RANGE, AR_PERCENTILE)
    dtd        = np.array([], dtype=int)
    for i in range(len(peak_indices)):
        if peaks[i, 0] < AR_MINIMUM_MASS: dtd = np.append(dtd, i); continue
        if not _ar_check_peak_validation(peaks[i, 0], AR_MAX_MASSDIFF):
            dtd = np.append(dtd, i); continue
        for _ in range(AR_BINNING_ITERS):
            j = 0; new_idx = peak_indices[i]
            while (peak_indices[i] - j) >= 0 and \
                  data[peak_indices[i], 0] - data[peak_indices[i] - j, 0] < AR_MASS_BIN_WIDTH:
                if peaks[i, 1] < data[peak_indices[i] - j, 1]:
                    peaks[i, 0] = data[peak_indices[i] - j, 0]
                    peaks[i, 1] = data[peak_indices[i] - j, 1]
                    new_idx = peak_indices[i] - j
                fwd = peak_indices[i] + j
                if fwd < data.shape[0] and peaks[i, 1] < data[fwd, 1]:
                    peaks[i, 0] = data[fwd, 0]; peaks[i, 1] = data[fwd, 1]; new_idx = fwd
                j += 1
            peak_indices[i] = new_idx
        if i > 0 and peak_indices[i] == peak_indices[i-1]: dtd = np.append(dtd, i); continue
        if i > 0 and peaks[i, 1] == peaks[i-1, 1] and peaks[i, 0] - peaks[i-1, 0] < 0.1:
            dtd = np.append(dtd, i); continue
        thr = _ar_automatic_threshold(data, local_perc[i], AR_STD_BOT, AR_STD_TOP, AR_STD_FACTOR)
        if peaks[i, 1] < thr: dtd = np.append(dtd, i)
    dtd, peaks, peak_indices, local_perc = _ar_delete_false_peaks(dtd, peaks, peak_indices, local_perc)
    for _ in range(AR_REL_INT_ITERS):
        if len(peaks) == 0: break
        rel = _ar_calc_relative_intensities(peaks, local_perc)
        bad = np.where(rel < AR_REL_INT_THR)[0]
        dtd, peaks, peak_indices, local_perc = _ar_delete_false_peaks(bad, peaks, peak_indices, local_perc)
    return peaks, peak_indices, local_perc


def _ar_tof_m(t, params):
    return params[0] * t**2 + params[1] * t + params[2]


def _ar_lower_region(detected_peaks, ion_mode):
    ions = _ar_get_ions(ion_mode); int_m = ions['integer mass']; ex_m = ions['exact mass']
    out = []
    for row in detected_peaks[detected_peaks[:, 0] < 60]:
        for j, im in enumerate(int_m):
            if im == int(round(row[0])): r = row.copy(); r[3] = ex_m[j]; out.append(r); break
    if not out:
        for row in detected_peaks[detected_peaks[:, 0] < 60]:
            r = row.copy(); r[3] = round(row[0]); out.append(r)
    return np.array(out) if out else np.empty((0, 4))


def _ar_higher_region(detected_peaks, prev, ion_mode, rng):
    lo, hi = rng
    region = detected_peaks[(detected_peaks[:, 0] > lo) & (detected_peaks[:, 0] < hi)].copy()
    if region.size == 0: return prev
    ions = _ar_get_ions(ion_mode); int_m = ions['integer mass']; ex_m = ions['exact mass']
    peak_diff = float(prev[-1, 3] - prev[-1, 0]) if prev.size > 0 else 0.0
    corrected = []
    for i in range(len(region)):
        for j, im in enumerate(int_m):
            if im == int(round(region[i, 0] + peak_diff)):
                region[i, 3] = ex_m[j]; corrected.append(region[i]); break
        if region[i, 3] == 0:
            region[i, 3] = (np.floor(region[i, 0])
                            if region[i, 0] + peak_diff - np.floor(region[i, 0]) < 0.5
                            else np.ceil(region[i, 0]))
        peak_diff = region[i, 3] - region[i, 0]
    if not corrected: corrected.append(region[np.argmax(region[:, 1])])
    new_rows = np.array(corrected)
    return new_rows if prev.size == 0 else np.vstack([prev, new_rows])


def run_auto_recal(data_np, filename, stop_flag=None):
    """Full auto-recalibration pipeline.

    Returns (recal_np, check_manually, summary_df, fitparams).
    Raises ValueError when calibration cannot proceed.
    Raises InterruptedError on cancellation.
    """
    ion_mode = 'pos' if '_pos_' in os.path.basename(filename) else 'neg'
    if stop_flag is not None and stop_flag._stop_requested: raise InterruptedError
    peaks, peak_indices, local_perc = _ar_detect_peaks(data_np)
    if len(peaks) == 0:
        raise ValueError("No peaks detected - cannot auto-recalibrate.")
    if stop_flag is not None and stop_flag._stop_requested: raise InterruptedError
    detected = np.hstack([peaks, local_perc.reshape(-1, 1), np.zeros((len(peaks), 1))])
    cal = _ar_lower_region(detected, ion_mode)
    cal = _ar_higher_region(detected, cal, ion_mode, (60, 120))
    cal = _ar_higher_region(detected, cal, ion_mode, (120, 190))
    cal = _ar_higher_region(detected, cal, ion_mode, (190, 237))
    if len(cal) < 2:
        raise ValueError(f"Only {len(cal)} calibration peak(s) - need ≥ 2.")
    check_manually = len(cal) < 3
    if not check_manually:
        last = cal[-1]; prev2 = cal[-2]
        if 1.5 * abs(last[0] - last[3]) < abs(prev2[0] - prev2[3]): check_manually = True
    summary = pd.DataFrame({
        'original m/z': np.round(cal[:, 0], 4),
        'corrected to':  np.round(cal[:, 3], 4),
        'Δ m/z':         np.round(cal[:, 3] - cal[:, 0], 4),
    })
    cal_tof = cal.copy()
    for j in range(len(cal_tof)):
        for t in range(data_np.shape[0]):
            if data_np[t, 0] > cal_tof[j, 0]:
                cal_tof[j, 0] = (t - 1) * AR_TIMESTEP; break
    fitparams = np.polyfit(cal_tof[:, 0], cal_tof[:, 3], 2)
    recal = data_np.copy()
    for j in range(len(recal)):
        recal[j, 0] = _ar_tof_m(j * AR_TIMESTEP, fitparams)
    return recal, check_manually, summary, fitparams


# ── Manual recalibration helpers ──────────────────────────────────────────────

def detect_peak_near(data_df, nominal_mz, window=1.5):
    """Find the highest-intensity point within [nominal_mz±window].

    Returns (detected_mz, detected_intensity) or None.
    """
    mask = ((data_df['mz'] >= max(nominal_mz - window, AR_MINIMUM_MASS)) &
            (data_df['mz'] <= nominal_mz + window))
    sub = data_df[mask]
    if sub.empty:
        return None
    idx = sub['intensity'].idxmax()
    return float(sub.loc[idx, 'mz']), float(sub.loc[idx, 'intensity'])


def apply_manual_recal_pairs(data_df, pairs):
    """Apply manual recalibration from a list of (observed_mz, reference_mz) pairs.

    Uses the same quadratic TOF-polynomial fit as auto-recal.
    Falls back to a linear scale factor for < 2 pairs.
    """
    if not pairs:
        return data_df.copy()

    data_np = data_df[['mz', 'intensity']].to_numpy().copy()

    if len(pairs) < 2:
        obs, ref = pairs[0]
        if obs == 0:
            return data_df.copy()
        factor = ref / obs
        out = data_df.copy()
        out['mz'] = out['mz'] * factor
        return out

    cal_tof_rows = []
    for obs_mz, ref_mz in pairs:
        for t in range(data_np.shape[0]):
            if data_np[t, 0] > obs_mz:
                t_val = (t - 1) * AR_TIMESTEP
                cal_tof_rows.append((t_val, ref_mz))
                break

    if len(cal_tof_rows) < 2:
        obs, ref = pairs[0]
        factor = ref / obs if obs != 0 else 1.0
        out = data_df.copy(); out['mz'] = out['mz'] * factor
        return out

    t_arr   = np.array([r[0] for r in cal_tof_rows])
    ref_arr = np.array([r[1] for r in cal_tof_rows])
    deg     = min(2, len(cal_tof_rows) - 1)
    fitparams = np.polyfit(t_arr, ref_arr, deg)

    recal_np = data_np.copy()
    for j in range(len(recal_np)):
        t_j = j * AR_TIMESTEP
        recal_np[j, 0] = np.polyval(fitparams, t_j)

    return pd.DataFrame({'mz': recal_np[:, 0], 'intensity': recal_np[:, 1]})
