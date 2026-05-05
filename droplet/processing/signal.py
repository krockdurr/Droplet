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
