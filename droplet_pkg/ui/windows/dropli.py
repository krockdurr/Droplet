"""Dropli — Droplet's clickable guide.

Not an AI: Dropli walks the user through a fixed tree of choices
(droplet_pkg/ui/dropli_script.py) and ends by saying where to go, with
optional buttons that open the right tool directly.

Widgets:
  DropliButton — the animated sprite in the main window's top-right corner
  DropliChat   — the chat panel that drops down below it

Both are children of the main window (not separate windows), so they stay
put with it on every platform, including Wayland.
"""

import os
import re

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui import dropli_script as script

_FRAMES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "assets", "frames")
_FRAME_FILES = {
    "closed":  "Dropli_mouth_closed.png",
    "partial": "Dropli_mouth_partially_open.png",
    "open":    "Dropli_mouth_fully_open.png",
}
# Mouth cycle while talking
_TALK_CYCLE = ["partial", "open", "partial", "closed"]
_TALK_FRAME_MS = 85

_CHARS_PER_SECOND = 110       # typewriter speed
_BUBBLE_PAUSE_MS  = 250       # pause between two of Dropli's bubbles
_ACCENT = "#2f7de1"


def _load_frames():
    frames = {}
    for key, name in _FRAME_FILES.items():
        pm = QtGui.QPixmap(os.path.join(_FRAMES_DIR, name))
        frames[key] = pm
    return frames


def _scaled(pm, scale):
    """Integer upscale with nearest-neighbour so the pixel art stays crisp."""
    if pm.isNull():
        return pm
    return pm.scaled(pm.width() * scale, pm.height() * scale,
                     QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                     QtCore.Qt.TransformationMode.FastTransformation)


# ─────────────────────────────────────────────────────────────────────────────
#  Mouth animation (shared by the corner sprite and the chat avatars)
# ─────────────────────────────────────────────────────────────────────────────

class _Mouth(QtCore.QObject):
    frame_changed = QtCore.pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.frame = "closed"
        self._step = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_TALK_FRAME_MS)
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._step = 0
            self._timer.start()

    def stop(self):
        self._timer.stop()
        self._set("closed")

    def _tick(self):
        self._set(_TALK_CYCLE[self._step % len(_TALK_CYCLE)])
        self._step += 1

    def _set(self, frame):
        if frame != self.frame:
            self.frame = frame
            self.frame_changed.emit(frame)


class _Avatar(QtWidgets.QLabel):
    """A Dropli picture at a given scale that follows the shared mouth."""

    def __init__(self, frames, mouth, scale, parent=None):
        super().__init__(parent)
        self._pix = {k: _scaled(pm, scale) for k, pm in frames.items()}
        any_pm = self._pix["closed"]
        self.setFixedSize(any_pm.size())
        self._mouth = mouth
        self._override = None
        mouth.frame_changed.connect(self._refresh)
        self._refresh()

    def freeze(self):
        """Stop following the mouth (older avatars in the transcript)."""
        try:
            self._mouth.frame_changed.disconnect(self._refresh)
        except TypeError:
            pass
        self.setPixmap(self._pix["closed"])

    def set_override(self, frame):
        """Force a frame (e.g. on hover) while the mouth is idle; None to clear."""
        self._override = frame
        self._refresh()

    def _refresh(self, *_):
        frame = self._mouth.frame
        if frame == "closed" and self._override:
            frame = self._override
        self.setPixmap(self._pix[frame])


# ─────────────────────────────────────────────────────────────────────────────
#  Corner sprite
# ─────────────────────────────────────────────────────────────────────────────

class DropliButton(_Avatar):
    """Clickable Dropli in the top-right corner of ``host``."""

    clicked = QtCore.pyqtSignal()

    MARGIN = 6

    def __init__(self, host, frames, mouth, scale=4):
        super().__init__(frames, mouth, scale, host)
        self._host = host
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Ask Dropli — I can tell you where to find things")
        host.installEventFilter(self)
        self.reposition()

    def reposition(self):
        self.move(self._host.width() - self.width() - self.MARGIN, self.MARGIN)
        self.raise_()

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() in (QtCore.QEvent.Type.Resize,
                                                  QtCore.QEvent.Type.Show):
            self.reposition()
        return False

    def enterEvent(self, event):
        self.set_override("partial")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.set_override(None)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton \
                and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  Chat panel
# ─────────────────────────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]*>|&[a-zA-Z#0-9]+;|.", re.DOTALL)


def _plain_len(html):
    return sum(1 for t in _TAG_RE.findall(html) if not t.startswith("<"))


def _truncate_html(html, n):
    """First ``n`` visible characters of ``html``, tags kept (Qt closes them)."""
    out, seen = [], 0
    for tok in _TAG_RE.findall(html):
        if tok.startswith("<"):
            out.append(tok)
            continue
        if seen >= n:
            break
        out.append(tok)
        seen += 1
    return "".join(out)


class _Bubble(QtWidgets.QLabel):
    def __init__(self, html, from_user, parent=None):
        super().__init__(parent)
        self.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                           QtWidgets.QSizePolicy.Policy.Minimum)
        if from_user:
            self.setStyleSheet(
                f"QLabel {{ background: {_ACCENT}; color: white; padding: 7px 11px;"
                " border-radius: 12px; border-bottom-right-radius: 3px; }")
        else:
            self.setStyleSheet(
                "QLabel { background: rgba(47,125,225,0.13); color: palette(text);"
                " padding: 7px 11px; border-radius: 12px;"
                " border-top-left-radius: 3px; }")
        self.setText(html)

    def fit_to(self, html, max_width):
        """Fix the width to what the full ``html`` needs (capped), so short
        messages don't wrap early and the bubble doesn't grow while typing."""
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(self.font())
        doc.setHtml(html)
        doc.setDocumentMargin(0)
        pad = 24                                 # 11 px padding each side + border
        self.setFixedWidth(int(min(doc.idealWidth() + pad + 2, max_width)))


class DropliChat(QtWidgets.QFrame):
    """Chat panel: Dropli's bubbles, the user's choices, and action buttons."""

    WIDTH = 390
    MAX_HEIGHT = 580

    def __init__(self, host, anchor, frames, mouth, actions):
        """
        host    — main window (parent)
        anchor  — the DropliButton; the panel opens just below it
        actions — {action_key: callable}; keys missing here are not offered
        """
        super().__init__(host)
        self._host, self._anchor = host, anchor
        self._frames, self._mouth = frames, mouth
        self._actions = actions
        self._history = []          # node ids visited, for "Back"
        self._queue = []            # pending (html) bubbles to type out
        self._typing = None         # (label, html, total, shown)
        self._after_typing = None   # callable run once the queue is empty
        self._last_sender = None    # "dropli" / "user" — groups avatars
        self._live_avatar = None    # newest transcript avatar (the one that talks)

        self.setObjectName("DropliChat")
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet(
            "#DropliChat { background: palette(window); border: 1px solid palette(mid);"
            " border-radius: 10px; }")
        shadow = QtWidgets.QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24); shadow.setOffset(0, 4)
        shadow.setColor(QtGui.QColor(0, 0, 0, 90))
        self.setGraphicsEffect(shadow)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(6)

        # ── Header ──
        head = QtWidgets.QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(_Avatar(frames, mouth, 2))
        title = QtWidgets.QLabel("<b>Dropli</b><br>"
                                 "<span style='color:gray; font-size:9pt;'>"
                                 "Your Droplet guide</span>")
        head.addWidget(title, stretch=1)
        close_btn = QtWidgets.QToolButton()
        close_btn.setText("✕")
        close_btn.setAutoRaise(True)
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self.hide)
        head.addWidget(close_btn, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        root.addLayout(head)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        sep.setStyleSheet("color: palette(mid);")
        root.addWidget(sep)

        # ── Transcript ──
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setObjectName("DropliViewport")
        self._scroll.viewport().setStyleSheet("#DropliViewport { background: transparent; }")
        body = QtWidgets.QWidget()
        body.setObjectName("DropliBody")
        body.setStyleSheet("#DropliBody { background: transparent; }")
        self._msgs = QtWidgets.QVBoxLayout(body)
        self._msgs.setContentsMargins(0, 2, 4, 2)
        self._msgs.setSpacing(4)
        self._msgs.addStretch()
        self._scroll.setWidget(body)
        self._scroll.verticalScrollBar().rangeChanged.connect(
            lambda _lo, hi: self._scroll.verticalScrollBar().setValue(hi))
        body.installEventFilter(self)       # click = skip typing
        root.addWidget(self._scroll, stretch=1)

        # ── Choices ──
        self._choices = QtWidgets.QVBoxLayout()
        self._choices.setSpacing(4)
        root.addLayout(self._choices)

        nav = QtWidgets.QHBoxLayout()
        self._back_btn = self._nav_button("← Back", self._go_back)
        self._restart_btn = self._nav_button("↺ Start over", self.restart)
        nav.addWidget(self._back_btn)
        nav.addStretch()
        nav.addWidget(self._restart_btn)
        root.addLayout(nav)

        self._type_timer = QtCore.QTimer(self)
        self._type_timer.setInterval(max(10, int(1000 / _CHARS_PER_SECOND * 2)))
        self._type_timer.timeout.connect(self._type_tick)

        esc = QtGui.QShortcut(QtGui.QKeySequence("Escape"), self)
        esc.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        esc.activated.connect(self.hide)

        host.installEventFilter(self)
        self.hide()

    # ── Open / close / position ──────────────────────────────────────────────

    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.open()

    def open(self, node=None):
        self._reposition()
        self.show()
        self.raise_()
        self._anchor.raise_()
        if node is not None:
            self._go(node)
        elif not self._history:
            self._go(script.START)
        self.setFocus()

    def hideEvent(self, event):
        self._finish_typing()
        super().hideEvent(event)

    def _reposition(self):
        host_w, host_h = self._host.width(), self._host.height()
        top = self._anchor.geometry().bottom() + 4
        h = max(260, min(self.MAX_HEIGHT, host_h - top - 8))
        w = min(self.WIDTH, host_w - 16)
        self.setGeometry(host_w - w - 8, top, w, h)

    def eventFilter(self, obj, event):
        if obj is self._host and event.type() == QtCore.QEvent.Type.Resize \
                and self.isVisible():
            self._reposition()
        elif event.type() == QtCore.QEvent.Type.MouseButtonPress and obj is not self._host:
            self._skip_typing()
        return False

    # ── Conversation ─────────────────────────────────────────────────────────

    def restart(self):
        self._finish_typing()
        while self._msgs.count() > 1:          # keep the top stretch
            item = self._msgs.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._history.clear()
        self._last_sender = None
        self._live_avatar = None
        self._go(script.START)

    def _go(self, node_id, user_text=None, record=True):
        node = script.NODES[node_id]
        if user_text:
            self._add_user(user_text)
        if record:
            self._history.append(node_id)
        self._clear_choices()
        for html in node.get("say", []):
            self._queue.append(html)
        self._after_typing = lambda: self._show_choices(node_id)
        self._next_bubble()

    def _go_back(self):
        if len(self._history) < 2:
            return
        self._history.pop()
        self._go(self._history[-1], user_text="← Back", record=False)

    def _show_choices(self, node_id):
        node = script.NODES[node_id]
        for label, key in node.get("do", []):
            if key in self._actions:
                self._add_choice(f"▶  {label}",
                                 lambda _=False, k=key, t=label: self._run_action(k, t),
                                 primary=True)
        options = node.get("options")
        if options is None:                     # an answer: offer to go on
            options = [("I have another question", script.START),
                       ("That's all, thanks!", script.BYE)]
        for label, target in options:
            self._add_choice(label, lambda _=False, n=target, t=label: self._choose(n, t))
        if node_id == script.BYE:
            QtCore.QTimer.singleShot(1400, self._say_goodbye)
        self._back_btn.setEnabled(len(self._history) > 1 and node_id != script.BYE)

    def _choose(self, node_id, label):
        if node_id == script.START:
            self._history.clear()
        self._go(node_id, user_text=label)

    def _say_goodbye(self):
        self.hide()
        self.restart_silently()

    def restart_silently(self):
        """Clear the transcript so the next opening starts fresh."""
        self._finish_typing()
        while self._msgs.count() > 1:
            item = self._msgs.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self._history.clear()
        self._last_sender = None
        self._live_avatar = None
        self._clear_choices()

    def _run_action(self, key, label):
        self._add_user(label)
        self._clear_choices()
        self._queue.append("There you go! ✨")
        node_id = self._history[-1] if self._history else script.START

        def _after():
            self._show_choices(node_id)
            # Get out of the way so the tool (or the plot) is visible
            QtCore.QTimer.singleShot(500, self._do_action(key))
        self._after_typing = _after
        self._next_bubble()

    def _do_action(self, key):
        def run():
            self.hide()
            try:
                self._actions[key]()
            except Exception as e:
                QtWidgets.QMessageBox.warning(self._host, "Dropli",
                                              f"Sorry, that didn't work:\n{e}")
        return run

    # ── Bubbles ──────────────────────────────────────────────────────────────

    def _row(self, widget, from_user, html):
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(6)
        max_w = int(self.width() * 0.78)
        widget.fit_to(html, max_w)
        if from_user:
            row.addStretch()
            row.addWidget(widget)
        else:
            if self._last_sender != "dropli":
                if self._live_avatar is not None:
                    self._live_avatar.freeze()
                av = _Avatar(self._frames, self._mouth, 2)
                self._live_avatar = av
                row.addWidget(av, alignment=QtCore.Qt.AlignmentFlag.AlignTop)
            else:
                spacer = QtWidgets.QWidget()
                spacer.setFixedWidth(22)
                row.addWidget(spacer)
            row.addWidget(widget)
            row.addStretch()
        holder = QtWidgets.QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self._msgs.addWidget(holder)
        self._last_sender = "user" if from_user else "dropli"

    def _add_user(self, text):
        self._row(_Bubble(text, True), True, text)

    def _next_bubble(self):
        if not self._queue:
            self._mouth.stop()
            cb, self._after_typing = self._after_typing, None
            if cb:
                cb()
            return
        html = self._queue.pop(0)
        bubble = _Bubble("", False)
        self._row(bubble, False, html)
        self._typing = [bubble, html, _plain_len(html), 0]
        self._mouth.start()
        self._type_timer.start()

    def _type_tick(self):
        if self._typing is None:
            self._type_timer.stop()
            return
        bubble, html, total, shown = self._typing
        shown = min(total, shown + 2)
        self._typing[3] = shown
        bubble.setText(_truncate_html(html, shown))
        if shown >= total:
            bubble.setText(html)
            self._type_timer.stop()
            self._typing = None
            if self._queue:
                self._mouth.stop()
                QtCore.QTimer.singleShot(_BUBBLE_PAUSE_MS, self._next_bubble)
            else:
                self._next_bubble()

    def _skip_typing(self):
        """Click on the transcript: show the current bubble(s) in full."""
        if self._typing is None and not self._queue:
            return
        self._type_timer.stop()
        if self._typing is not None:
            self._typing[0].setText(self._typing[1])
            self._typing = None
        while self._queue:
            html = self._queue.pop(0)
            self._row(_Bubble(html, False), False, html)
        self._next_bubble()

    def _finish_typing(self):
        """Complete any running animation (used when hiding / restarting)."""
        if self._typing is not None or self._queue:
            self._skip_typing()
        self._mouth.stop()

    # ── Choice buttons ───────────────────────────────────────────────────────

    def _add_choice(self, text, slot, primary=False):
        btn = QtWidgets.QPushButton(text)
        btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        if primary:
            btn.setStyleSheet(
                f"QPushButton {{ background: {_ACCENT}; color: white; border: none;"
                " border-radius: 13px; padding: 5px 12px; text-align: left;"
                " font-weight: bold; }"
                "QPushButton:hover { background: #2567c0; }")
        else:
            btn.setStyleSheet(
                f"QPushButton {{ background: transparent; color: {_ACCENT};"
                f" border: 1px solid {_ACCENT}; border-radius: 13px;"
                " padding: 5px 12px; text-align: left; }"
                "QPushButton:hover { background: rgba(47,125,225,0.15); }")
        btn.clicked.connect(slot)
        self._choices.addWidget(btn)

    def _clear_choices(self):
        while self._choices.count():
            item = self._choices.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _nav_button(self, text, slot):
        btn = QtWidgets.QToolButton()
        btn.setText(text)
        btn.setAutoRaise(True)
        btn.setStyleSheet("QToolButton { color: gray; font-size: 9pt; }")
        btn.clicked.connect(slot)
        return btn


# ─────────────────────────────────────────────────────────────────────────────
#  Entry point used by app.py
# ─────────────────────────────────────────────────────────────────────────────

def install_dropli(host, actions):
    """Create Dropli in ``host``'s top-right corner.  Returns (button, chat)."""
    frames = _load_frames()
    mouth = _Mouth(host)
    button = DropliButton(host, frames, mouth)
    chat = DropliChat(host, button, frames, mouth, actions)
    button.clicked.connect(chat.toggle)
    return button, chat
