"""Shared UI mixins for Droplet popup windows."""

try:
    from PyQt6 import QtWidgets, QtCore
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore


class StayOnTopMixin:
    """
    Mix into any QWidget / QDialog popup window.
    Call  self._install_stay_on_top(menu_bar)  once during UI construction
    to append a 'Window ▶ Pin on top' checkable action to any QMenuBar.

    Cross-platform:
      Linux/X11  — tries wmctrl first (zero flicker); falls back to Qt.
      Windows    — Qt sets HWND_TOPMOST via SetWindowPos; brief redraw.
      macOS      — Qt sets NSWindow level; brief redraw.

    The critical point: check isVisible() BEFORE calling setWindowFlag(),
    because Qt hides the window internally during the flag change.

    Preference is persisted per window title in QSettings.
    """

    _SOT_KEY = "window/{}/stay_on_top"

    def _install_stay_on_top(self, menu_bar: "QtWidgets.QMenuBar",
                              app_settings=None):
        self._sot_settings = app_settings
        window_menu = menu_bar.addMenu("Window")
        self._sot_action = QtWidgets.QAction("Pin on top", self, checkable=True)
        self._sot_action.setToolTip(
            "Keep this window above all other windows.\n"
            "On Linux the window manager usually offers this natively;\n"
            "this button works on Windows and macOS too.")
        if app_settings is not None:
            key = self._SOT_KEY.format(self.windowTitle() or type(self).__name__)
            self._sot_action.setChecked(
                app_settings.value(key, False, type=bool))
        self._sot_action.toggled.connect(self._apply_stay_on_top)
        window_menu.addAction(self._sot_action)
        if self._sot_action.isChecked():
            self._apply_stay_on_top(True)

    def _apply_stay_on_top(self, on: bool):
        import sys
        import subprocess

        was_visible = self.isVisible()
        pos         = self.pos() if was_visible else None

        if was_visible and sys.platform.startswith("linux"):
            try:
                win_id = hex(int(self.winId()))
                action = "add" if on else "remove"
                r = subprocess.run(
                    ["wmctrl", "-ir", win_id, "-b", f"{action},above"],
                    capture_output=True, timeout=2)
                if r.returncode == 0:
                    self._sot_save(on)
                    return
            except Exception:
                pass

        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint, on)
        if was_visible:
            self.show()
            if pos:
                self.move(pos)
            self.raise_()
            self.activateWindow()

        self._sot_save(on)

    def _sot_save(self, on: bool):
        if getattr(self, "_sot_settings", None) is not None:
            key = self._SOT_KEY.format(self.windowTitle() or type(self).__name__)
            self._sot_settings.setValue(key, on)
