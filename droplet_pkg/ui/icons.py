"""Line icons drawn with QPainter, so they follow the theme's colours and stay
sharp on any screen scale without shipping image files.

    icon("folder")            -> QIcon in the current theme's icon colour
    icon("zoom", "#2f7de1")   -> QIcon in a given colour

All shapes are drawn on a 24 × 24 grid with a ~1.7 px round stroke.
"""

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui import theme

P = QtCore.QPointF
R = QtCore.QRectF


def _folder(p):
    path = QtGui.QPainterPath()
    path.moveTo(3, 7); path.lineTo(3, 18.5)
    path.quadTo(3, 20, 4.5, 20); path.lineTo(19.5, 20)
    path.quadTo(21, 20, 21, 18.5); path.lineTo(21, 9.5)
    path.quadTo(21, 8, 19.5, 8); path.lineTo(12, 8)
    path.lineTo(10, 5); path.lineTo(4.5, 5)
    path.quadTo(3, 5, 3, 6.5); path.closeSubpath()
    p.drawPath(path)


def _files(p):
    p.drawRoundedRect(R(7, 3, 12, 15), 2, 2)
    path = QtGui.QPainterPath()
    path.moveTo(5, 7); path.lineTo(5, 19); path.quadTo(5, 21, 7, 21); path.lineTo(15, 21)
    p.drawPath(path)
    p.drawLine(P(10, 8), P(16, 8)); p.drawLine(P(10, 11.5), P(16, 11.5))


def _refresh(p):
    p.drawArc(R(4.5, 4.5, 15, 15), 40 * 16, 290 * 16)
    p.drawPolyline(QtGui.QPolygonF([P(19.5, 3.5), P(19.5, 8.5), P(14.5, 8.5)]))


def _pan(p):
    p.drawLine(P(12, 3), P(12, 21)); p.drawLine(P(3, 12), P(21, 12))
    for a, b, c in (((9, 6), (12, 3), (15, 6)), ((9, 18), (12, 21), (15, 18)),
                    ((6, 9), (3, 12), (6, 15)), ((18, 9), (21, 12), (18, 15))):
        p.drawPolyline(QtGui.QPolygonF([P(*a), P(*b), P(*c)]))


def _zoom(p):
    p.drawEllipse(R(3.5, 3.5, 13, 13))
    p.drawLine(P(14.5, 14.5), P(20.5, 20.5))
    p.drawLine(P(10, 7), P(10, 13)); p.drawLine(P(7, 10), P(13, 10))


def _zoom_box(p):
    pen = p.pen(); dashed = QtGui.QPen(pen); dashed.setDashPattern([2, 2])
    p.setPen(dashed); p.drawRect(R(3, 3, 13, 11)); p.setPen(pen)
    p.drawEllipse(R(10.5, 10.5, 8, 8)); p.drawLine(P(17, 17), P(21, 21))


def _pick(p):
    p.drawEllipse(R(5, 5, 14, 14))
    for a, b in (((12, 2), (12, 7)), ((12, 17), (12, 22)), ((2, 12), (7, 12)), ((17, 12), (22, 12))):
        p.drawLine(P(*a), P(*b))
    p.setBrush(p.pen().color()); p.drawEllipse(P(12, 12), 1.4, 1.4)


def _log(p):
    path = QtGui.QPainterPath()
    path.moveTo(3, 20); path.cubicTo(5, 8, 9, 5, 21, 4)
    p.drawPath(path)
    p.drawLine(P(3, 3), P(3, 21)); p.drawLine(P(3, 21), P(21, 21))


def _stacked(p):
    for y in (4, 10, 16):
        path = QtGui.QPainterPath()
        path.moveTo(3, y + 4); path.lineTo(7, y + 4); path.lineTo(8.5, y)
        path.lineTo(10, y + 4); path.lineTo(14, y + 4); path.lineTo(15, y + 1.5)
        path.lineTo(16, y + 4); path.lineTo(21, y + 4)
        p.drawPath(path)


def _sigma(p):
    p.drawPolyline(QtGui.QPolygonF([P(18, 5), P(6, 5), P(12.5, 12), P(6, 19), P(18, 19)]))


def _grid(p):
    p.drawRoundedRect(R(3.5, 3.5, 17, 17), 2, 2)
    p.drawLine(P(9.2, 3.5), P(9.2, 20.5)); p.drawLine(P(14.8, 3.5), P(14.8, 20.5))
    p.drawLine(P(3.5, 9.2), P(20.5, 9.2)); p.drawLine(P(3.5, 14.8), P(20.5, 14.8))


def _crosshair(p):
    pen = p.pen(); dashed = QtGui.QPen(pen); dashed.setDashPattern([2, 1.6])
    p.setPen(dashed)
    p.drawLine(P(12, 2), P(12, 22)); p.drawLine(P(2, 12), P(22, 12))
    p.setPen(pen); p.drawEllipse(P(12, 12), 3, 3)


def _peaks(p):
    for y in (6, 12, 18):
        p.setBrush(p.pen().color()); p.drawEllipse(P(5, y), 1.3, 1.3)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush); p.drawLine(P(9, y), P(20, y))


def _legend(p):
    p.drawRoundedRect(R(3, 4, 18, 16), 2.5, 2.5)
    for y in (9, 15):
        p.drawLine(P(6.5, y), P(9.5, y)); p.drawLine(P(12, y), P(18, y))


def _ratio(p):
    path = QtGui.QPainterPath()
    path.moveTo(2, 20); path.lineTo(6, 20); path.lineTo(8, 5); path.lineTo(10, 20)
    path.lineTo(14, 20); path.lineTo(16, 12); path.lineTo(18, 20); path.lineTo(22, 20)
    p.drawPath(path)
    dashed = QtGui.QPen(p.pen()); dashed.setDashPattern([1.5, 1.5]); p.setPen(dashed)
    p.drawLine(P(8, 5), P(20, 5)); p.drawLine(P(16, 12), P(20, 12))


def _area(p):
    path = QtGui.QPainterPath()
    path.moveTo(3, 20); path.cubicTo(8, 20, 9, 4, 12, 4); path.cubicTo(15, 4, 16, 20, 21, 20)
    p.drawPath(path)
    fill = QtGui.QColor(p.pen().color()); fill.setAlpha(90)
    p.fillPath(path, fill)


def _export(p):
    path = QtGui.QPainterPath()
    path.moveTo(4, 14); path.lineTo(4, 19); path.quadTo(4, 21, 6, 21)
    path.lineTo(18, 21); path.quadTo(20, 21, 20, 19); path.lineTo(20, 14)
    p.drawPath(path)
    p.drawLine(P(12, 3), P(12, 15))
    p.drawPolyline(QtGui.QPolygonF([P(7.5, 7.5), P(12, 3), P(16.5, 7.5)]))


def _copy(p):
    p.drawRoundedRect(R(8, 8, 13, 13), 2, 2)
    path = QtGui.QPainterPath()
    path.moveTo(16, 5); path.lineTo(16, 4.5); path.quadTo(16, 3, 14.5, 3)
    path.lineTo(4.5, 3); path.quadTo(3, 3, 3, 4.5); path.lineTo(3, 14.5)
    path.quadTo(3, 16, 4.5, 16); path.lineTo(5, 16)
    p.drawPath(path)


def _sun(p):
    p.drawEllipse(P(12, 12), 4, 4)
    import math
    for i in range(8):
        a = i * math.pi / 4
        p.drawLine(P(12 + 7 * math.cos(a), 12 + 7 * math.sin(a)),
                   P(12 + 9.5 * math.cos(a), 12 + 9.5 * math.sin(a)))


def _moon(p):
    path = QtGui.QPainterPath()
    path.addEllipse(R(3.5, 3.5, 17, 17))
    cut = QtGui.QPainterPath(); cut.addEllipse(R(9, 1, 14, 14))
    p.drawPath(path.subtracted(cut))


def _sidebar(p):
    p.drawRoundedRect(R(3, 4, 18, 16), 2.5, 2.5)
    p.drawLine(P(9, 4), P(9, 20))
    for y in (8, 11, 14):
        p.drawLine(P(5.2, y), P(6.8, y))


def _dock(p):
    p.drawRoundedRect(R(3, 4, 18, 16), 2.5, 2.5)
    p.drawLine(P(3, 14), P(21, 14))
    fill = QtGui.QColor(p.pen().color()); fill.setAlpha(70)
    p.fillRect(R(4, 14.8, 16, 4.4), fill)


def _search(p):
    p.drawEllipse(R(4, 4, 12, 12)); p.drawLine(P(14.5, 14.5), P(20, 20))


def _prev(p):
    p.drawPolyline(QtGui.QPolygonF([P(15, 5), P(8, 12), P(15, 19)]))


def _next(p):
    p.drawPolyline(QtGui.QPolygonF([P(9, 5), P(16, 12), P(9, 19)]))


def _down(p):
    p.drawPolyline(QtGui.QPolygonF([P(5, 9), P(12, 16), P(19, 9)]))


def _plus(p):
    p.drawLine(P(12, 5), P(12, 19)); p.drawLine(P(5, 12), P(19, 12))


def _close(p):
    p.drawLine(P(6.5, 6.5), P(17.5, 17.5)); p.drawLine(P(17.5, 6.5), P(6.5, 17.5))


def _help(p):
    p.drawEllipse(R(3, 3, 18, 18))
    path = QtGui.QPainterPath()
    path.moveTo(9.2, 9.5); path.cubicTo(9.2, 6, 14.8, 6, 14.8, 9.5)
    path.cubicTo(14.8, 12, 12, 11.8, 12, 14)
    p.drawPath(path)
    p.setBrush(p.pen().color()); p.drawEllipse(P(12, 17.2), 0.9, 0.9)


def _clusters(p):
    for x, y, r in ((6, 17, 2.2), (11, 11, 2.2), (17, 6, 2.2)):
        p.drawEllipse(P(x, y), r, r)
    dashed = QtGui.QPen(p.pen()); dashed.setDashPattern([1.5, 1.5]); p.setPen(dashed)
    p.drawLine(P(7.6, 15.4), P(9.4, 12.6)); p.drawLine(P(12.6, 9.4), P(15.4, 7.6))


def _spectrum(p, base=19, peaks=((7, 6), (12, 11), (16, 4)), x0=3, x1=21):
    """A small stick spectrum: baseline at y=base with peaks (x, top)."""
    path = QtGui.QPainterPath()
    path.moveTo(x0, base)
    for x, top in peaks:
        path.lineTo(x - 1.2, base); path.lineTo(x, top); path.lineTo(x + 1.2, base)
    path.lineTo(x1, base)
    p.drawPath(path)


def _drift(p, bottom=20):
    """A shaded baseline hump with peaks standing on it (what baseline
    correction removes)."""
    hump = QtGui.QPainterPath()
    hump.moveTo(3, bottom); hump.quadTo(11, bottom - 9, 21, bottom - 3)
    area = QtGui.QPainterPath(hump)
    area.lineTo(21, bottom); area.closeSubpath()
    fill = QtGui.QColor(p.pen().color()); fill.setAlpha(70)
    p.fillPath(area, fill)
    p.drawPath(hump)
    for x, top in ((8, 3.5), (14, 7)):
        # foot of the peak on the hump (the quadratic curve at x)
        t = (x - 3) / 18.0
        foot = (1 - t) ** 2 * bottom + 2 * (1 - t) * t * (bottom - 9) + t ** 2 * (bottom - 3)
        p.drawPolyline(QtGui.QPolygonF([P(x - 1.3, foot), P(x, top), P(x + 1.3, foot)]))


def _baseline(p):
    _drift(p, bottom=21)
    p.drawLine(P(3, 21), P(21, 21))


def _ruler(p, y=21.5):
    p.drawLine(P(3, y), P(21, y))
    for x in (5, 9, 13, 17, 21):
        p.drawLine(P(x, y), P(x, y - 1.8))


def _recal_auto(p):
    _spectrum(p, base=17, peaks=((7, 8), (13, 5)), x0=3, x1=16)
    _ruler(p)
    # sparkle: done for you
    p.drawLine(P(19, 3), P(19, 9)); p.drawLine(P(16, 6), P(22, 6))


def _recal_manual(p):
    _spectrum(p, base=17, peaks=((9, 6), (15, 9)), x0=3, x1=21)
    _ruler(p)
    # a small double arrow: move the peak by hand
    p.drawLine(P(4, 3.5), P(13, 3.5))
    p.drawPolyline(QtGui.QPolygonF([P(6, 1.8), P(4, 3.5), P(6, 5.2)]))
    p.drawPolyline(QtGui.QPolygonF([P(11, 1.8), P(13, 3.5), P(11, 5.2)]))


def _baseline_recal(p):
    _drift(p, bottom=17.5)
    _ruler(p)


def _normalize(p):
    dashed = QtGui.QPen(p.pen()); dashed.setDashPattern([1.6, 1.6])
    pen = p.pen()
    p.setPen(dashed); p.drawLine(P(2, 5), P(22, 5)); p.setPen(pen)
    for x, top in ((6, 5), (12, 5), (18, 5)):
        p.drawLine(P(x, 20), P(x, top + 1.5))
    p.drawLine(P(3, 20.5), P(21, 20.5))


def _residuals(p):
    p.drawLine(P(3, 12), P(21, 12))
    p.setBrush(p.pen().color())
    for x, y in ((5, 9), (8, 15), (11, 10.5), (14, 13.5), (17, 8), (20, 14)):
        p.drawEllipse(P(x, y), 1.2, 1.2)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)


def _tof(p):
    p.drawEllipse(R(2.5, 6.5, 10, 10))
    p.drawLine(P(7.5, 11.5), P(7.5, 8.6)); p.drawLine(P(7.5, 11.5), P(9.8, 12.8))
    p.drawLine(P(14, 11.5), P(21, 11.5))
    p.drawPolyline(QtGui.QPolygonF([P(18.5, 9), P(21, 11.5), P(18.5, 14)]))


def _batch(p):
    p.drawRoundedRect(R(8, 3, 12, 14), 2, 2)
    path = QtGui.QPainterPath()
    path.moveTo(5.5, 6); path.lineTo(5.5, 19); path.quadTo(5.5, 20, 6.5, 20); path.lineTo(16, 20)
    path.moveTo(3, 9); path.lineTo(3, 21.5); path.lineTo(13, 21.5)
    p.drawPath(path)
    p.drawLine(P(11, 8), P(17, 8)); p.drawLine(P(11, 11.5), P(17, 11.5))


def _toolbar(p):
    p.drawRoundedRect(R(3, 4, 18, 6), 2, 2)
    for x in (6.5, 10, 13.5):
        p.drawLine(P(x, 6.3), P(x, 7.7))
    p.drawLine(P(5, 15), P(19, 15)); p.drawLine(P(5, 19), P(14, 19))


def _settings(p):
    # three sliders: the line stops on both sides of each knob
    for y, x in ((7, 15), (12, 8), (17, 13)):
        p.drawLine(P(4, y), P(x - 2.2, y)); p.drawLine(P(x + 2.2, y), P(20, y))
        p.drawEllipse(P(x, y), 2, 2)


_DRAW = {
    "folder": _folder, "files": _files, "refresh": _refresh, "pan": _pan,
    "zoom": _zoom, "zoom_box": _zoom_box, "pick": _pick, "log": _log,
    "stacked": _stacked, "sigma": _sigma, "grid": _grid, "crosshair": _crosshair,
    "peaks": _peaks, "legend": _legend, "ratio": _ratio, "area": _area,
    "export": _export, "copy": _copy, "sun": _sun, "moon": _moon,
    "sidebar": _sidebar, "dock": _dock, "search": _search, "prev": _prev,
    "next": _next, "down": _down, "plus": _plus, "close": _close, "help": _help,
    "clusters": _clusters,
    "baseline": _baseline, "recal_auto": _recal_auto, "recal_manual": _recal_manual,
    "baseline_recal": _baseline_recal, "normalize": _normalize, "residuals": _residuals,
    "tof": _tof, "batch": _batch, "toolbar": _toolbar, "settings": _settings,
}


def pixmap(name, color=None, size=20, dpr=2.0):
    color = QtGui.QColor(color or theme.tokens()["icon"])
    pm = QtGui.QPixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.scale(size / 24.0, size / 24.0)
    pen = QtGui.QPen(color, 1.7)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    _DRAW[name](p)
    p.end()
    return pm


def icon(name, color=None, size=20):
    """QIcon for ``name``; the disabled state is drawn in the muted colour."""
    ic = QtGui.QIcon()
    ic.addPixmap(pixmap(name, color, size), QtGui.QIcon.Mode.Normal)
    ic.addPixmap(pixmap(name, theme.tokens()["muted"], size), QtGui.QIcon.Mode.Disabled)
    ic.addPixmap(pixmap(name, theme.tokens()["accent"], size), QtGui.QIcon.Mode.Normal,
                 QtGui.QIcon.State.On)
    return ic


class ThemedIcons:
    """Keeps a set of widgets/actions in sync with the theme: call ``set(obj,
    name)`` once and the icon is redrawn in the right colours after every
    theme change."""

    def __init__(self):
        self._items = []
        theme.on_theme_changed(lambda _t: self.refresh())

    def set(self, obj, name, color_key=None):
        self._items.append((obj, name, color_key))
        self._apply(obj, name, color_key)
        return obj

    def _apply(self, obj, name, color_key):
        color = theme.tokens()[color_key] if color_key else None
        try:
            obj.setIcon(icon(name, color))
        except RuntimeError:
            pass        # widget already deleted

    def refresh(self):
        for obj, name, key in self._items:
            self._apply(obj, name, key)


themed = ThemedIcons()
