"""Hover labels for tool buttons: the tool's name, its shortcut and what it
does, 250 ms after the pointer stops on the button.

The label is drawn inside the window (a child widget), not as a separate
tooltip window, so it shows on every platform and window system, Wayland
included. The buttons' native tooltip is suppressed to avoid showing both.

    hover_tips.add(button, "Zoom box", "Drag a rectangle to zoom.", "Z")
    hover_tips.add(button)            # uses the button's toolTip() text
"""

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui import theme

DELAY_MS = 250


class _TipLabel(QtWidgets.QLabel):
    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("HoverTip")
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setMaximumWidth(320)
        self.hide()

    def restyle(self):
        t = theme.tokens()
        self.setStyleSheet(
            f"#HoverTip {{ background: {t['surface']}; color: {t['text']};"
            f" border: 1px solid {t['border']}; border-radius: 7px; padding: 6px 9px; }}")


class HoverTips(QtCore.QObject):
    def __init__(self):
        super().__init__()
        self._labels = []               # every label made (one per window)
        self._target = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DELAY_MS)
        self._timer.timeout.connect(self._show)
        theme.on_theme_changed(lambda _t: self._restyle_all())

    def _restyle_all(self):
        for lbl in list(self._labels):
            try:
                lbl.restyle()
            except RuntimeError:              # its window was closed
                self._labels.remove(lbl)

    def add(self, widget, title=None, text=None, shortcut=None):
        # Kept on the widget itself: PyQt may hand out a new Python wrapper
        # for the same button later, so a dict keyed by id() would miss it.
        widget.setProperty("hover_title", title or "")
        widget.setProperty("hover_text", text or "")
        widget.setProperty("hover_shortcut", shortcut or "")
        widget.installEventFilter(self)
        return widget

    def _html(self, widget):
        title = widget.property("hover_title") or ""
        text = widget.property("hover_text") or ""
        shortcut = widget.property("hover_shortcut") or ""
        if not title:                             # fall back on the tooltip
            tip = widget.toolTip()
            if not tip:
                return ""
            return tip.replace("\n", "<br>")
        muted = theme.tokens()["muted"]
        html = f"<b>{title}</b>"
        if shortcut:
            html += f"&nbsp;&nbsp;<span style='color:{muted}'>{shortcut}</span>"
        if text:
            html += f"<br>{text}"
        return html

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QtCore.QEvent.Type.ToolTip:
            return True                           # ours instead of the native one
        if t == QtCore.QEvent.Type.Enter:
            self._target = obj
            self._timer.start()
        elif t in (QtCore.QEvent.Type.Leave, QtCore.QEvent.Type.MouseButtonPress,
                   QtCore.QEvent.Type.Hide, QtCore.QEvent.Type.Wheel):
            self._timer.stop()
            if obj is self._target:
                self._target = None
            self._hide_all()
        return False

    def _hide_all(self):
        for lbl in list(self._labels):
            try:
                lbl.hide()
            except RuntimeError:
                self._labels.remove(lbl)

    def _show(self):
        w = self._target
        if w is None:
            return
        try:
            if not w.isVisible() or not w.underMouse():
                return
            html = self._html(w)
            if not html:
                return
            host = w.window()
            lbl = host.findChild(_TipLabel, "HoverTip",
                                 QtCore.Qt.FindChildOption.FindDirectChildrenOnly)
            if lbl is None:
                lbl = _TipLabel(host)
                lbl.restyle()
                self._labels.append(lbl)
            lbl.setText(html)
            lbl.adjustSize()
            # Below the button, kept inside the window
            p = w.mapTo(host, QtCore.QPoint(0, w.height() + 6))
            x = max(4, min(p.x(), host.width() - lbl.width() - 4))
            y = p.y()
            if y + lbl.height() > host.height() - 4:          # no room: above it
                y = w.mapTo(host, QtCore.QPoint(0, 0)).y() - lbl.height() - 6
            lbl.move(x, max(4, y))
            lbl.raise_()
            lbl.show()
        except RuntimeError:
            pass                                   # widget deleted meanwhile


hover_tips = HoverTips()
