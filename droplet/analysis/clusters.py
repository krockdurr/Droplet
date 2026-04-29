"""Cluster detection: find regularly-spaced series of peaks in a spectrum.

Pure functions (numpy/scipy only). The entry point `run_cluster_detection`
accepts the spectrum DataFrame and known-peak m/z values as explicit parameters
rather than reading application globals.
"""

import concurrent.futures
import multiprocessing
import numpy as np
from droplet.processing.calibration import AR_MINIMUM_MASS


def detect_all_peaks_for_clusters(data_df, min_snr=1.0, min_pct=0.5):
    """Detect all peaks above min_snr and min_pct of max intensity.

    Returns sorted numpy array of (mz, intensity) pairs.
    """
    from scipy.signal import find_peaks as _fp
    if data_df is None or len(data_df) == 0:
        return np.empty((0, 2))

    mz_arr  = data_df['mz'].values
    int_arr = data_df['intensity'].values
    max_int = int_arr.max()
    if max_int == 0:
        return np.empty((0, 2))

    min_height = (min_pct / 100.0) * max_int
    indices, _ = _fp(int_arr, distance=3, height=min_height)
    if len(indices) == 0:
        return np.empty((0, 2))

    LOCAL = 5.0
    kept = []
    for i in indices:
        mz = mz_arr[i]; intensity = int_arr[i]
        if mz < AR_MINIMUM_MASS:
            continue
        mask = (mz_arr >= mz - LOCAL) & (mz_arr <= mz + LOCAL)
        noise = np.percentile(int_arr[mask], 25) if mask.sum() > 4 else 0.0
        if noise <= 0:
            noise = 1e-9
        if intensity / noise >= min_snr:
            kept.append((mz, intensity))

    if not kept:
        return np.empty((0, 2))
    arr = np.array(kept)
    return arr[np.argsort(arr[:, 0])]


def build_chains(peaks_mz, peaks_int, spacing, tol):
    """Find all chains where consecutive members are spacing±tol apart.

    Seeds from highest-intensity peaks. Each peak belongs to at most one chain.
    Returns list of lists of indices into peaks_mz.
    """
    used       = np.zeros(len(peaks_mz), dtype=bool)
    seed_order = np.argsort(peaks_int)[::-1]
    chains     = []

    for seed in seed_order:
        if used[seed]:
            continue
        chain = [seed]
        used[seed] = True

        cur = seed
        while True:
            target = peaks_mz[cur] + spacing
            cands  = np.where(
                (~used) &
                (peaks_mz >= target - tol) &
                (peaks_mz <= target + tol)
            )[0]
            if len(cands) == 0:
                break
            best = cands[np.argmin(np.abs(peaks_mz[cands] - target))]
            chain.append(best)
            used[best] = True
            cur = best

        cur = seed
        while True:
            target = peaks_mz[cur] - spacing
            cands  = np.where(
                (~used) &
                (peaks_mz >= target - tol) &
                (peaks_mz <= target + tol)
            )[0]
            if len(cands) == 0:
                break
            best = cands[np.argmin(np.abs(peaks_mz[cands] - target))]
            chain.insert(0, best)
            used[best] = True
            cur = best

        chains.append(chain)

    return chains


def auto_find_spacings(peaks_mz, max_spacing=250.0, bin_width=0.1, min_count=2):
    if len(peaks_mz) < 2:
        return []
    mz = np.asarray(peaks_mz)
    diff_matrix = mz[:, None] - mz[None, :]
    diffs = diff_matrix[diff_matrix > 0]
    diffs = diffs[(diffs >= 1.0) & (diffs <= max_spacing)]
    if len(diffs) == 0:
        return []
    bins = np.arange(1.0, max_spacing + bin_width, bin_width)
    counts, edges = np.histogram(diffs, bins=bins)
    from scipy.signal import find_peaks as _fp
    peak_idx, _ = _fp(counts, height=min_count, distance=max(1, int(0.5 / bin_width)))
    spacings_with_counts = [
        (round(float((edges[i] + edges[i+1]) / 2), 3), int(counts[i]))
        for i in peak_idx
    ]
    spacings_with_counts.sort(key=lambda t: -t[1])
    return [s for s, c in spacings_with_counts]


def run_cluster_detection(spacings_input, tol, min_chain, min_snr, min_pct,
                          data_df=None, known_peak_mzs=None, peak_list_mzs=None):
    """Detect regularly-spaced clusters.

    data_df:        current spectrum DataFrame (used when peak_list_mzs is None)
    known_peak_mzs: m/z array of user-defined peaks (for overlap flagging)
    peak_list_mzs:  if provided, skip spectrum detection and use these m/z values
    """
    if data_df is None and peak_list_mzs is None:
        return []

    if peak_list_mzs is not None and len(peak_list_mzs) >= 2:
        peaks_mz  = np.array(sorted(peak_list_mzs), dtype=float)
        peaks_int = np.ones(len(peaks_mz), dtype=float)
        known_mz  = peaks_mz.copy()
        candidate_spacings = auto_find_spacings(peaks_mz)
    else:
        if data_df is None:
            return []
        peaks = detect_all_peaks_for_clusters(data_df, min_snr=min_snr, min_pct=min_pct)
        if len(peaks) == 0:
            return []
        peaks_mz  = peaks[:, 0]
        peaks_int = peaks[:, 1]
        known_mz  = np.array(known_peak_mzs) if known_peak_mzs is not None else np.array([])

        if spacings_input.strip():
            try:
                candidate_spacings = [float(s.strip())
                                      for s in spacings_input.split(',') if s.strip()]
            except ValueError:
                candidate_spacings = []
        else:
            candidate_spacings = auto_find_spacings(peaks_mz)

    if not candidate_spacings:
        return []

    def _eval_spacing(spacing):
        chains = build_chains(peaks_mz, peaks_int, spacing, tol)
        results = []
        for chain in chains:
            if len(chain) < min_chain:
                continue
            members = []
            for i in sorted(chain):
                mz = float(peaks_mz[i])
                intensity = float(peaks_int[i])
                is_known = (len(known_mz) > 0 and
                            np.any(np.abs(known_mz - mz) < tol * 2))
                members.append((mz, intensity, is_known))
            results.append({'spacing': spacing, 'members': members})
        return results

    n_workers = max(1, min(len(candidate_spacings), multiprocessing.cpu_count() - 1))
    all_clusters = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_eval_spacing, s): s for s in candidate_spacings}
        for future in concurrent.futures.as_completed(futures):
            try:
                all_clusters.extend(future.result())
            except Exception:
                pass

    global_used_mz = set()
    deduped = []
    for cluster in sorted(all_clusters, key=lambda c: -len(c['members'])):
        mz_set  = {round(m[0], 2) for m in cluster['members']}
        overlap = len(mz_set & global_used_mz) / len(mz_set)
        if overlap < 0.6:
            deduped.append(cluster)
            global_used_mz |= mz_set

    return deduped
