"""Signal-level utilities: subtraction, pen helpers, tolerance/shift formulas.

Pure functions with no Qt or application-state dependencies,
except apply_alpha / overlay_pen which need pyqtgraph/Qt colour objects
(they are imported lazily so the module can be imported without a QApplication).
"""

import numpy as np


def compute_subtraction(df_a, df_b, dynamic=False):
    if df_a is None or df_b is None:
        return None
    a = df_a.copy(); b = df_b.copy()
    if dynamic:
        mx_a = a['intensity'].max(); mx_b = b['intensity'].max()
        if mx_a > 0: a['intensity'] /= mx_a
        if mx_b > 0: b['intensity'] /= mx_b
    b_interp = np.interp(a['mz'].values, b['mz'].values, b['intensity'].values,
                         left=0, right=0)
    result = a.copy(); result['intensity'] = a['intensity'].values - b_interp
    return result


def get_tolerance(mz):
    return min(0.1 + 0.004 * mz, 0.8)

def find_peak_bounds(mz_arr, int_arr, mz_nom, noise_floor=0.0,
                        coarse_tol=0.5, max_hw=2.0):
    """
    Find integration bounds for the peak nearest to mz_nom using
    scipy.signal.peak_widths at rel_height=1.0 (valley-to-valley width).

    Requires int_arr to be noise-floor corrected (baseline clipped to 0).
    At rel_height=1.0 scipy measures the width at the prominence base,
    which equals the surrounding valley level — exactly 0 on corrected data.
    This gives valley-to-valley bounds without any manual threshold tuning.

    Parameters
    ----------
    mz_arr, int_arr : full-spectrum arrays; int_arr already clipped to 0.
    mz_nom          : nominal m/z from the peak list.
    noise_floor     : corrected intensity below which a peak is ignored.
    coarse_tol      : ±Da window used to locate the maximum (default 0.5 Da).
    max_hw          : hard half-width cap in Da to prevent runaway bounds (default 2.0).

    Returns (mz_lo, mz_hi, real_mz, peak_max_int)  or  None.
    """
    from scipy.signal import peak_widths

    mask = (mz_arr >= mz_nom - coarse_tol) & (mz_arr <= mz_nom + coarse_tol)
    if mask.sum() < 2:
        return None

    idxs     = np.where(mask)[0]
    peak_loc = int(int_arr[idxs].argmax())
    peak_g   = idxs[peak_loc]       # index into the full arrays
    real_mz  = mz_arr[peak_g]
    peak_max = int_arr[peak_g]

    if peak_max <= noise_floor:
        return None                  # nothing above noise — skip

    try:
        _, _, left_ips, right_ips = peak_widths(int_arr, [peak_g], rel_height=1.0)
    except Exception:
        return None

    # peak_widths returns fractional sample indices; map to m/z
    idx_axis = np.arange(len(mz_arr), dtype=float)
    mz_lo = float(np.interp(left_ips[0],  idx_axis, mz_arr))
    mz_hi = float(np.interp(right_ips[0], idx_axis, mz_arr))

    # Hard cap so a very isolated peak doesn't consume ±half-spectrum
    mz_lo = max(mz_lo, real_mz - max_hw)
    mz_hi = min(mz_hi, real_mz + max_hw)

    return mz_lo, mz_hi, real_mz, peak_max

def peak_shift(mz):
    return 0.0002 * mz


def apply_alpha(color_str, alpha, current_display='bright'):
    """Return a QColor with the given alpha blended against the display background."""
    import pyqtgraph as pg
    try:
        from PyQt6 import QtGui
    except ImportError:
        from pyqtgraph.Qt import QtGui
    c = pg.mkColor(color_str); factor = alpha / 255.0
    if current_display == "dark":
        r = int(c.red() * factor); g = int(c.green() * factor); b = int(c.blue() * factor)
    else:
        r = int(c.red()   + (255 - c.red())   * (1.0 - factor))
        g = int(c.green() + (255 - c.green()) * (1.0 - factor))
        b = int(c.blue()  + (255 - c.blue())  * (1.0 - factor))
    return QtGui.QColor(r, g, b, 255)


def overlay_pen(color_str, alpha):
    import pyqtgraph as pg
    c = pg.mkColor(color_str)
    c.setAlpha(alpha)
    return pg.mkPen(c, width=1)
