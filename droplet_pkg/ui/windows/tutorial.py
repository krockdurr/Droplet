"""TutorialOverlay - Dropli's quick tour of the main window.

A semi-transparent overlay dims the window, spotlights one area at a time,
and Dropli explains it in a speech bubble (same typewriter + talking mouth
as the Dropli chat).  Kept deliberately short: the goal is to show where
things are, Dropli's chat answers the "how do I…" questions afterwards.
"""

import os

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui.windows.dropli import (
    _Avatar, _Mouth, _load_frames, _plain_len, _truncate_html,
    _ACCENT, _CHARS_PER_SECOND,
)


def _get_app():
    import droplet_pkg.app as _m
    return _m


# ─────────────────────────────────────────────────────────────────────────────
#  Spotlight targets — resolved at runtime, each returns a QRect in the main
#  window's coordinates (or None to centre the bubble with no spotlight)
# ─────────────────────────────────────────────────────────────────────────────

def _widget_rect(w):
    if w is None or not w.isVisible():
        return None
    host = _get_app().main_win
    return QtCore.QRect(w.mapTo(host, QtCore.QPoint(0, 0)), w.size())


def _layout_rect(layout):
    """Union of the visible widgets of a layout (ignores trailing stretch)."""
    rect = QtCore.QRect()
    for i in range(layout.count()):
        w = layout.itemAt(i).widget()
        r = _widget_rect(w) if w is not None else None
        if r is not None:
            rect = rect.united(r)
    return rect if not rect.isEmpty() else None


def _menu_titles_rect(*menus):
    """The titles of some menus in the menu bar."""
    a = _get_app()
    bar = a.menu_bar
    rect = QtCore.QRect()
    for m in menus:
        g = bar.actionGeometry(m.menuAction())
        if not g.isEmpty():
            rect = rect.united(g)
    if rect.isEmpty():
        return _widget_rect(bar)
    return QtCore.QRect(bar.mapTo(a.main_win, rect.topLeft()), rect.size())


def _files_body():
    a = _get_app()
    text = ("Your spectra live here. Pick the one to show in the dropdown; "
            "<b>Mode</b> and <b>dt</b> filter the list using the file names.<br>"
            "<b>+ Add Overlay</b> puts more spectra on top to compare them.")
    try:
        showing_examples = (os.path.normpath(a.base_dir)
                            == os.path.normpath(a.EXAMPLE_DATA_DIR))
    except Exception:
        showing_examples = False
    if showing_examples:
        text += ("<br><br>These are example spectra I brought along. "
                 "To open yours: <b>File → Open Folder…</b>, or drag files "
                 "onto the window.")
    else:
        text += ("<br><br>Open more data with <b>File → Open Folder…</b>, "
                 "or drag files onto the window.")
    return text


class TutorialOverlay(QtWidgets.QWidget):
    """Full-window overlay: spotlight + Dropli explaining each step."""

    # "target" returns a QRect (see helpers above), "body" is rich text or a
    # callable returning it, "anchor" says where the bubble goes.
    STEPS = [
        {
            "target": lambda: _widget_rect(_get_app().files_section),
            "title":  "Your files",
            "body":   _files_body,
            "anchor": "below",
        },
        {
            "target": lambda: _widget_rect(_get_app().plot_widget),
            "title":  "The plot",
            "body":   "<b>Scroll</b> to zoom, <b>drag</b> to pan, and press "
                      "<b>Z</b> to draw a zoom box.<br>"
                      "Right-click → <b>Go to last zoom</b> steps back.",
            "anchor": "center",
        },
        {
            "target": lambda: _layout_rect(_get_app().tools_row),
            "title":  "Display switches",
            "body":   "Quick ways to look at your spectra: <b>Stacked</b> gives each "
                      "one its own row, <b>Log Y</b> a log scale, and <b>σ Clip</b> "
                      "hides the noise on screen.<br>"
                      "None of these change your data.",
            "anchor": "below",
        },
        {
            "target": lambda: _menu_titles_rect(_get_app().analysis_menu,
                                                _get_app().new_analysis_menu,
                                                _get_app().peaks_menu),
            "title":  "Where the work happens",
            "body":   "<b>Processing</b>: baseline, recalibration, normalisation, "
                      "for one spectrum or a whole folder.<br>"
                      "<b>Analysis</b>: clusters, common/unique peaks, peak areas.<br>"
                      "<b>Peaks</b>: your peak lists (<b>Ctrl+P</b>) and "
                      "click-to-pick (<b>P</b>).",
            "anchor": "below",
        },
        {
            "target": lambda: _menu_titles_rect(_get_app().file_menu,
                                                _get_app().plot_menu),
            "title":  "Keeping your work",
            "body":   "<b>Plot</b> exports the figure (PNG, SVG, PDF, LaTeX).<br>"
                      "<b>File → Save Project</b> keeps your whole session: files, "
                      "overlays, peak lists and view.",
            "anchor": "below",
        },
        {
            "target": lambda: _widget_rect(_get_app().dropli_button),
            "title":  "That's it!",
            "body":   "Whenever you wonder where something is, click me: I'll tell "
                      "you where to go, or open it for you. 💧<br>"
                      "You can replay this tour from <b>Help → Quick Tour</b>.",
            "anchor": "below",
        },
    ]

    BODY_WIDTH = 300

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self._step           = 0
        self._spotlight_rect = QtCore.QRect()
        self._parent_pixmap  = None
        self._refreshing_pixmap = False
        self._typing = None                   # [html, total, shown]
        self._closed = False

        self._mouth = _Mouth(self)
        self._build_bubble()

        self._type_timer = QtCore.QTimer(self)
        self._type_timer.setInterval(max(10, int(1000 / _CHARS_PER_SECOND * 2)))
        self._type_timer.timeout.connect(self._type_tick)

        parent.installEventFilter(self)
        self.setGeometry(parent.rect())

    def _build_bubble(self):
        self._bubble = QtWidgets.QFrame(self)
        self._bubble.setObjectName("tourBubble")
        self._bubble.setStyleSheet(
            f"#tourBubble {{ background: palette(window); border: 2px solid {_ACCENT};"
            " border-radius: 12px; }")
        outer = QtWidgets.QHBoxLayout(self._bubble)
        outer.setContentsMargins(12, 12, 14, 10)
        outer.setSpacing(12)

        self._avatar = _Avatar(_load_frames(), self._mouth, 4)
        outer.addWidget(self._avatar, alignment=QtCore.Qt.AlignmentFlag.AlignTop)

        col = QtWidgets.QVBoxLayout()
        col.setSpacing(6)
        self._title_lbl = QtWidgets.QLabel()
        self._title_lbl.setStyleSheet(
            f"color: {_ACCENT}; font-size: 13px; font-weight: bold;")
        col.addWidget(self._title_lbl)

        self._body_lbl = QtWidgets.QLabel()
        self._body_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setFixedWidth(self.BODY_WIDTH)
        self._body_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop
                                    | QtCore.Qt.AlignmentFlag.AlignLeft)
        col.addWidget(self._body_lbl)

        ctrl = QtWidgets.QHBoxLayout()
        ctrl.setSpacing(6)
        self._progress_lbl = QtWidgets.QLabel()
        self._progress_lbl.setStyleSheet("color: gray; font-size: 10px;")
        ctrl.addWidget(self._progress_lbl)
        ctrl.addStretch()

        secondary = ("QPushButton { color: gray; background: transparent; border: none;"
                     " padding: 0 6px; }"
                     "QPushButton:hover { color: palette(text); }"
                     "QPushButton:disabled { color: transparent; }")
        self._skip_btn = QtWidgets.QPushButton("Skip tour")
        self._back_btn = QtWidgets.QPushButton("← Back")
        for b in (self._skip_btn, self._back_btn):
            b.setStyleSheet(secondary)
            b.setFixedHeight(26)
            b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._next_btn = QtWidgets.QPushButton("Next →")
        self._next_btn.setFixedHeight(26)
        self._next_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._next_btn.setStyleSheet(
            f"QPushButton {{ color: white; background: {_ACCENT}; border: none;"
            " border-radius: 13px; padding: 0 14px; font-weight: bold; }"
            "QPushButton:hover { background: #2567c0; }")
        self._skip_btn.clicked.connect(self.close_tutorial)
        self._back_btn.clicked.connect(self._go_back)
        self._next_btn.clicked.connect(self._go_next)
        ctrl.addWidget(self._skip_btn)
        ctrl.addWidget(self._back_btn)
        ctrl.addWidget(self._next_btn)
        col.addLayout(ctrl)
        outer.addLayout(col)

    # ── Navigation ───────────────────────────────────────────────────────────

    def start(self):
        self.show()
        self.raise_()
        self.setFocus()
        self._update_step()

    def _go_next(self):
        if self._typing is not None:          # first click finishes the sentence
            self._finish_typing()
        elif self._step < len(self.STEPS) - 1:
            self._step += 1
            self._update_step()
        else:
            self.close_tutorial()

    def _go_back(self):
        if self._step > 0:
            self._step -= 1
            self._update_step()

    def _update_step(self, animate=True):
        step = self.STEPS[self._step]
        n    = len(self.STEPS)
        body = step["body"]() if callable(step["body"]) else step["body"]
        self._title_lbl.setText(step["title"])
        self._progress_lbl.setText(f"{self._step + 1} / {n}")
        self._back_btn.setEnabled(self._step > 0)
        is_last = self._step == n - 1
        self._next_btn.setText("Let's go!" if is_last else "Next →")
        self._skip_btn.setVisible(not is_last)

        # Size the bubble for the full text first, so it doesn't grow while typing
        self._body_lbl.ensurePolished()
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(self._body_lbl.font())
        doc.setDocumentMargin(0)
        doc.setHtml(body)
        doc.setTextWidth(self.BODY_WIDTH)
        self._body_lbl.setFixedHeight(int(doc.size().height()) + 4)
        self._bubble.layout().activate()
        self._bubble.resize(self._bubble.sizeHint())
        if animate:
            self._typing = [body, _plain_len(body), 0]
            self._body_lbl.setText("")
            self._mouth.start()
            self._type_timer.start()
        else:
            self._finish_typing()

        try:
            rect = step["target"]()
        except Exception:
            rect = None
        self._spotlight_rect = (rect.adjusted(-6, -6, 6, 6) if rect is not None
                                else QtCore.QRect())
        self._position_bubble(step.get("anchor", "below"))
        self._refresh_parent_pixmap()
        self.update()

    # ── Typewriter ───────────────────────────────────────────────────────────

    def _type_tick(self):
        if self._typing is None:
            self._type_timer.stop()
            return
        html, total, shown = self._typing
        shown = min(total, shown + 2)
        self._typing[2] = shown
        self._body_lbl.setText(_truncate_html(html, shown))
        if shown >= total:
            self._finish_typing()

    def _finish_typing(self):
        self._type_timer.stop()
        if self._typing is not None:
            self._body_lbl.setText(self._typing[0])
            self._typing = None
        self._mouth.stop()

    # ── Layout & painting ────────────────────────────────────────────────────

    def _position_bubble(self, anchor):
        bw = self._bubble.width(); bh = self._bubble.height()
        pw = self.width();         ph = self.height()
        pad = 14
        sr = self._spotlight_rect
        if sr.isEmpty() or anchor == "center":
            if sr.isEmpty():
                x, y = (pw - bw) // 2, (ph - bh) // 2
            else:
                x, y = sr.center().x() - bw // 2, sr.center().y() - bh // 2
        elif anchor == "above":
            x, y = sr.left(), sr.top() - bh - pad
        else:                                    # below (right-aligned if near the right edge)
            x = sr.left() if sr.left() + bw < pw - pad else sr.right() - bw
            y = sr.bottom() + pad
        x = max(pad, min(x, pw - bw - pad))
        y = max(pad, min(y, ph - bh - pad))
        self._bubble.move(x, y)

    def _refresh_parent_pixmap(self):
        if self._refreshing_pixmap:
            return
        self._refreshing_pixmap = True
        try:
            self.hide(); self._parent_pixmap = self.parent().grab(); self.show()
            self.raise_(); self.setFocus()
        finally:
            self._refreshing_pixmap = False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 150))
        if not self._spotlight_rect.isEmpty() and self._parent_pixmap is not None:
            path = QtGui.QPainterPath()
            path.addRoundedRect(QtCore.QRectF(self._spotlight_rect), 8, 8)
            painter.save()
            painter.setClipPath(path)
            dpr = self._parent_pixmap.devicePixelRatio()
            src = QtCore.QRectF(self._spotlight_rect)
            src = QtCore.QRectF(src.x() * dpr, src.y() * dpr,
                                src.width() * dpr, src.height() * dpr)
            painter.drawPixmap(QtCore.QRectF(self._spotlight_rect),
                               self._parent_pixmap, src)
            painter.restore()
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor(_ACCENT), 2.5))
            painter.drawPath(path)
        painter.end()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QtCore.QEvent.Type.Resize \
                and not self._refreshing_pixmap:
            self.setGeometry(self.parent().rect())
            QtCore.QTimer.singleShot(0, lambda: None if self._closed
                                     else self._update_step(animate=False))
        return False

    # ── Input ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if self._bubble.geometry().contains(event.position().toPoint()):
            self._finish_typing()               # click on the bubble: show it all
        else:
            self._go_next()
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        K = QtCore.Qt.Key
        if key == K.Key_Escape:
            self.close_tutorial()
        elif key in (K.Key_Right, K.Key_Return, K.Key_Enter, K.Key_Space):
            self._go_next()
        elif key == K.Key_Left:
            self._go_back()
        else:
            super().keyPressEvent(event)

    def close_tutorial(self):
        if self._closed:
            return
        self._closed = True
        self._finish_typing()
        self.parent().removeEventFilter(self)
        self.hide(); self.deleteLater()
