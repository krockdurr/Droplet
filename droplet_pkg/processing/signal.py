"""Signal-level utilities: subtraction, pen helpers, tolerance/shift formulas.

Pure functions with no Qt or application-state dependencies,
except apply_alpha / overlay_pen which need pyqtgraph/Qt colour objects
(they are imported lazily so the module can be imported without a QApplication).
"""

import warnings
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
    Find integration bounds for the peak nearest to mz_nom.

    Algorithm (pure numpy, no scipy — works identically on all platforms):
    1. Locate all strict local maxima within ±coarse_tol of mz_nom that
       are above noise_floor.  If the window contains multiple peaks, pick
       the one whose m/z is closest to mz_nom.
    2. Walk left and right from that maximum, stopping at the first valley
       on each side — defined as the first sample where the signal starts
       rising again.  This guarantees a single rise-then-fall shape and
       prevents the bounds from crossing into a neighbouring peak.
    3. Apply the hard ±max_hw cap in Da.

    Parameters
    ----------
    mz_arr, int_arr : full-spectrum arrays; int_arr already baseline-clipped.
    mz_nom          : nominal m/z from the peak list.
    noise_floor     : intensity threshold below which a peak is ignored.
    coarse_tol      : ±Da window used to locate candidates (default 0.5 Da).
    max_hw          : hard half-width cap in Da (default 2.0).

    Returns (mz_lo, mz_hi, real_mz, peak_max_int) or None.
    """
    mask = (mz_arr >= mz_nom - coarse_tol) & (mz_arr <= mz_nom + coarse_tol)
    if mask.sum() < 2:
        return None

    idxs    = np.where(mask)[0]
    sub_int = int_arr[idxs]
    n_sub   = len(sub_int)

    # ── Find strict local maxima within the window ────────────────────────
    local_max_js = [j for j in range(1, n_sub - 1)
                    if sub_int[j] > sub_int[j - 1]
                    and sub_int[j] > sub_int[j + 1]
                    and sub_int[j] > noise_floor]
    # Include endpoints when they look like a peak tip
    if n_sub >= 1 and sub_int[0] > noise_floor and (n_sub == 1 or sub_int[0] > sub_int[1]):
        local_max_js.append(0)
    if n_sub >= 2 and sub_int[-1] > noise_floor and sub_int[-1] > sub_int[-2]:
        local_max_js.append(n_sub - 1)

    if local_max_js:
        # Multiple peaks in window → pick the one closest to mz_nom
        best_j = min(local_max_js, key=lambda j: abs(mz_arr[idxs[j]] - mz_nom))
    else:
        # No strict local max (e.g. flat top) → fall back to global argmax
        best_j = int(sub_int.argmax())

    peak_g   = idxs[best_j]
    real_mz  = float(mz_arr[peak_g])
    peak_max = float(int_arr[peak_g])

    if peak_max <= noise_floor:
        return None

    n = len(int_arr)

    # ── Walk left: stop at the first valley (signal starts rising again) ──
    left_idx = peak_g
    for i in range(peak_g - 1, -1, -1):
        if int_arr[i] <= int_arr[i + 1]:
            left_idx = i
            if int_arr[i] <= noise_floor:
                break
        else:
            left_idx = i + 1   # the rising point is the neighbouring peak
            break

    # ── Walk right ────────────────────────────────────────────────────────
    right_idx = peak_g
    for i in range(peak_g + 1, n):
        if int_arr[i] <= int_arr[i - 1]:
            right_idx = i
            if int_arr[i] <= noise_floor:
                break
        else:
            right_idx = i - 1
            break

    mz_lo = max(float(mz_arr[left_idx]),  real_mz - max_hw)
    mz_hi = min(float(mz_arr[right_idx]), real_mz + max_hw)

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
