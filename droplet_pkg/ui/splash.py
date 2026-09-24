"""Start-up screen: Droplet's icon, a progress bar and plain-language messages.

The screen runs in its own small process.  A Qt window can only be drawn by
the thread that owns the application, and that is exactly the thread Droplet's
start-up keeps busy (loading libraries, building the window, restoring peak
lists).  In a separate process the icon keeps "breathing" smoothly however
busy Droplet is, and Droplet's own start-up runs exactly as without a splash.

Droplet side (never imports Qt, so it can run before anything else):
    show_splash()                 Droplet.py, first thing
    splash_step(percent, message) app.py, as start-up progresses
    finish_splash()               app.py, once the main window is drawn
    when_splash_finished(cb)      run cb once the screen is gone
When no splash was shown (test suite, `import droplet_pkg.app` from elsewhere)
every call does nothing.

Splash side: `python splash.py` reads "percent<TAB>message" lines on stdin,
and closes on "done" or when the pipe closes (Droplet exited or crashed).
"""

import math
import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ICON = _ROOT / "assets" / "icons" / "Droplet_Icon.png"
_VERSION_FILE = _ROOT / "assets" / "about" / "VERSION"
SAFETY_TIMEOUT_S = 90        # the splash closes itself at the latest after this

# ── Droplet side ──────────────────────────────────────────────────────────────

_proc: subprocess.Popen | None = None
_last_percent = -1
_finished_callbacks: list = []


def show_splash():
    """Start the start-up screen process (call before importing droplet_pkg.app)."""
    global _proc
    if _proc is not None:
        return
    env = dict(os.environ)
    # macOS: no second Dock icon for the splash process
    env["QT_MAC_DISABLE_FOREGROUND_APPLICATION_TRANSFORM"] = "1"
    kw = {"stdin": subprocess.PIPE, "env": env}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        _proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())], **kw)
    except OSError:
        _proc = None                     # no splash, Droplet starts as usual


def _send(line: str):
    global _proc
    if _proc is None:
        return
    try:
        _proc.stdin.write((line + "\n").encode("utf-8"))
        _proc.stdin.flush()
    except (OSError, ValueError):        # splash closed or crashed: carry on without it
        _proc = None


def splash_step(percent: int, message: str | None = None):
    """Advance the progress bar and optionally show a new message."""
    global _last_percent
    if _proc is None or (not message and int(percent) <= _last_percent):
        return
    _last_percent = max(_last_percent, int(percent))
    _send(f"{int(percent)}\t{(message or '').replace(chr(10), ' ')}")


def splash_active() -> bool:
    return _proc is not None


def when_splash_finished(callback):
    """Run callback when the start-up screen closes (right away if there is none)."""
    if _proc is None:
        callback()
    else:
        _finished_callbacks.append(callback)


def finish_splash():
    """Close the start-up screen (safe to call more than once)."""
    global _proc
    if _proc is None:
        return
    _send("done")
    if _proc is not None:
        try:
            _proc.stdin.close()
        except OSError:
            pass
    _proc = None
    for cb in _finished_callbacks:
        try:
            cb()
        except Exception:
            pass
    _finished_callbacks.clear()


# ── Splash side ───────────────────────────────────────────────────────────────

def _make_window_class():
    from PyQt6 import QtCore, QtGui, QtWidgets

    class SplashWindow(QtWidgets.QWidget):
        MAX_MESSAGES = 3
        PULSE_MS     = 2800     # one full 100 % → 50 % → 100 % breath of the icon
        PULSE_LOW    = 0.5

        def __init__(self, version: str):
            super().__init__(None, QtCore.Qt.WindowType.SplashScreen
                             | QtCore.Qt.WindowType.FramelessWindowHint
                             | QtCore.Qt.WindowType.WindowStaysOnTopHint
                             | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus)
            # Never take the focus: Droplet's window gets it when it appears.
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedSize(440, 330)
            self._messages: list[str] = []
            self._seen: set[str] = set()

            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(36, 28, 36, 22)
            lay.setSpacing(8)

            icon = QtWidgets.QLabel()
            icon.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            pm = QtGui.QPixmap(str(_ICON))
            if not pm.isNull():
                dpr = self.devicePixelRatioF()
                pm = pm.scaled(int(120 * dpr), int(120 * dpr),
                               QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                               QtCore.Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
                icon.setPixmap(pm)
            lay.addWidget(icon, 0, QtCore.Qt.AlignmentFlag.AlignHCenter)

            # Slow "breathing" of the icon (cosine: equally smooth at 100 % and 50 %)
            self._icon_fx = QtWidgets.QGraphicsOpacityEffect(icon)
            self._icon_fx.setOpacity(1.0)
            icon.setGraphicsEffect(self._icon_fx)
            self._pulse = QtCore.QVariantAnimation(self)
            self._pulse.setDuration(self.PULSE_MS)
            self._pulse.setStartValue(0.0)
            self._pulse.setEndValue(1.0)
            self._pulse.setLoopCount(-1)
            self._pulse.valueChanged.connect(self._set_phase)
            self._pulse.start()

            title = QtWidgets.QLabel(
                f"Droplet <span style='color:#888;font-size:12px;'>{version}</span>")
            title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("font-size: 20px; font-weight: 600; color: #1f2d3d;")
            lay.addWidget(title)
            lay.addSpacing(6)

            self._bar = QtWidgets.QProgressBar()
            self._bar.setRange(0, 100)
            self._bar.setTextVisible(False)
            self._bar.setFixedHeight(8)
            self._bar.setStyleSheet(
                "QProgressBar { background: #e3e8ef; border: none; border-radius: 4px; }"
                "QProgressBar::chunk { background: #2f80d1; border-radius: 4px; }")
            lay.addWidget(self._bar)

            self._lines = []
            for _ in range(self.MAX_MESSAGES):
                lbl = QtWidgets.QLabel("")
                lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self._lines.append(lbl)
                lay.addWidget(lbl)
            lay.addStretch()

            screen = QtGui.QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                self.move(geo.center() - self.rect().center())

        def _set_phase(self, t: float):
            self._icon_fx.setOpacity(
                self.PULSE_LOW + (1 - self.PULSE_LOW) * (1 + math.cos(2 * math.pi * t)) / 2)

        def paintEvent(self, _event):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setPen(QtGui.QPen(QtGui.QColor("#cfd6df"), 1))
            p.setBrush(QtGui.QColor("#fbfcfd"))
            p.drawRoundedRect(QtCore.QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
            p.end()

        def step(self, percent: int, message: str | None):
            self._bar.setValue(max(self._bar.value(), int(percent)))
            if message and message not in self._seen:
                self._seen.add(message)
                self._messages = (self._messages + [message])[-self.MAX_MESSAGES:]
                # newest at the bottom in full colour, older ones fading above it
                shown = [""] * (self.MAX_MESSAGES - len(self._messages)) + self._messages
                for i, (lbl, text) in enumerate(zip(self._lines, shown)):
                    newest = i == self.MAX_MESSAGES - 1
                    lbl.setText(text)
                    lbl.setStyleSheet("font-size: 12px; color: %s;" % (
                        "#2b3a4a" if newest else "#9aa5b1"))

    return SplashWindow


def _run_splash():
    """Entry point of the splash process."""
    import threading
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    from PyQt6 import QtCore, QtWidgets

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("Droplet")
    app.setDesktopFileName("Droplet")
    try:
        version = _VERSION_FILE.read_text().strip()
    except OSError:
        version = ""
    win = _make_window_class()(version)

    def on_line(text):
        if text in ("done", "\0eof"):      # finished, or Droplet closed the pipe
            win.step(100, "Ready")
            QtCore.QTimer.singleShot(120, app.quit)
            return
        pct, _, msg = text.partition("\t")
        try:
            win.step(int(pct), msg or None)
        except ValueError:
            pass

    class Bridge(QtCore.QObject):
        line = QtCore.pyqtSignal(str)      # emitted by the reader thread, handled in the GUI thread

    bridge = Bridge()
    bridge.line.connect(on_line)

    def reader():
        for raw in sys.stdin.buffer:
            bridge.line.emit(raw.decode("utf-8", "replace").rstrip("\r\n"))
        bridge.line.emit("\0eof")

    threading.Thread(target=reader, daemon=True).start()
    QtCore.QTimer.singleShot(SAFETY_TIMEOUT_S * 1000, app.quit)
    win.step(3, "Starting Droplet…")
    win.show()
    app.exec()


if __name__ == "__main__":
    _run_splash()
