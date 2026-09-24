"""Baseline correction algorithms (airPLS, SNIP, Whittaker smoother).

All functions are pure: they take/return numpy arrays or DataFrames and have
no dependency on Qt or application state.

airPLS adapted from LILBID_GUI by M. Umair:
https://github.com/mumair5393/LILBID_GUI/blob/main/baseline_correction.py
"""

import numpy as np
from scipy.sparse import csc_matrix, eye, diags
from scipy.sparse.linalg import spsolve


def _whittaker_smooth(x, w, lambda_, differences=1):
    m = len(x)
    E = eye(m, format='csc'); D = E[1:] - E[:-1]
    W = diags(w, 0, shape=(m, m))
    A = csc_matrix(W + (lambda_ * D.T * D))
    B = csc_matrix(W * np.matrix(x).T)
    return np.array(spsolve(A, B))


def airpls_baseline(intensity, lambda_=100, porder=1, itermax=20, stop_flag=None):
    x = np.array(intensity, dtype=float); m = x.shape[0]; w = np.ones(m)
    for i in range(1, itermax + 1):
        if stop_flag is not None and stop_flag._stop_requested:
            raise InterruptedError("airPLS cancelled")
        z = _whittaker_smooth(x, w, lambda_, porder)
        d = x - z; dssn = np.abs(d[d < 0].sum())
        if dssn < 0.001 * np.abs(x).sum(): break
        w[d >= 0] = 0
        w[d < 0]  = np.exp(i * np.abs(d[d < 0]) / dssn)
        w[0] = np.exp(i * d[d < 0].max() / dssn); w[-1] = w[0]
    return z


def snip_baseline(intensity, max_hwidth=40, smooth_iters=3, stop_flag=None):
    y = np.array(intensity, dtype=float); y = np.clip(y, 0, None)
    p = np.sqrt(np.sqrt(y + 1)); n = len(p)
    for hwidth in range(1, max_hwidth + 1):
        if stop_flag is not None and stop_flag._stop_requested:
            raise InterruptedError("SNIP cancelled")
        left  = np.roll(p,  hwidth); left[:hwidth]   = p[:hwidth]
        right = np.roll(p, -hwidth); right[-hwidth:]  = p[-hwidth:]
        p = np.minimum(p, (left + right) / 2.0)
    for _ in range(smooth_iters):
        left  = np.roll(p,  1); left[0]  = p[0]
        right = np.roll(p, -1); right[-1] = p[-1]
        p = (left + p + right) / 3.0
    return np.clip((p ** 2) ** 2 - 1, 0, None)


def apply_baseline(data_df, algorithm='airpls', stop_flag=None):
    """Apply baseline correction.

    algorithm: 'airpls' (default) or 'snip'
    The caller is responsible for passing the correct algorithm string based on
    the current UI toggle (snip_action.isChecked()).
    """
    y = data_df['intensity'].values
    if algorithm == 'snip':
        baseline = snip_baseline(y, stop_flag=stop_flag)
        corrected = np.clip(y - baseline, 0, None)
    else:
        baseline = airpls_baseline(y, stop_flag=stop_flag)
        corrected = y - baseline
        corrected[corrected < 0] = 0.000001
        corrected = corrected + 0.007
    out = data_df.copy(); out['intensity'] = corrected
    return out
