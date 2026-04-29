"""TutorialOverlay - interactive step-by-step tutorial for first-time users."""

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui


def _get_app():
    import droplet.app as _m
    return _m


class TutorialOverlay(QtWidgets.QWidget):
    """
    Full-window semi-transparent overlay that spotlights a target widget
    and shows an instruction bubble next to it.
    """

    # STEPS uses lambdas so all widget references are resolved at runtime
    # against the live app module - no circular import issue.
    STEPS = [
        {
            "target":  lambda: _get_app().menu_bar,
            "title":   "Menu bar",
            "body":    "All main features are accessible from here.\n"
                       "File, View, Analysis, Peaks, Plot, Display and Help.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().folder_path_label,
            "title":   "Current folder",
            "body":    "This shows which folder or virtual file list is loaded.\n"
                       "Use  File → Open Folder  or drag-and-drop files to change it.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().files_section,
            "title":   "Files & Overlays",
            "body":    "Select the main spectrum file here.\n"
                       "Use  neg / pos  to filter by polarity.\n"
                       "Click  + Add Overlay  to load additional spectra on top.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().polarity_combo,
            "title":   "Polarity filter",
            "body":    "Switch between negative and positive mode.\n"
                       "Only files containing 'neg' or 'pos' in their name are shown.",
            "anchor":  "right",
        },
        {
            "target":  lambda: _get_app().combo,
            "title":   "File selector",
            "body":    "Choose which spectrum file to display as the main spectrum.\n"
                       "The plot updates immediately when you change selection.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().overlay_opacity_slider,
            "title":   "Overlay opacity",
            "body":    "Controls the transparency of all overlay spectra.\n"
                       "Ctrl+Scroll or Ctrl+↑↓ also cycles through overlays one at a time.",
            "anchor":  "above",
        },
        {
            "target":  lambda: _get_app().highlight_slider,
            "title":   "Highlight intensity",
            "body":    "Controls how strongly highlighted peaks stand out.\n"
                       "At maximum, peaks appear in their full chosen colour.",
            "anchor":  "above",
        },
        {
            "target":  lambda: _get_app().plot_widget,
            "title":   "Spectrum plot",
            "body":    "• Click and drag to pan.\n"
                       "• Scroll wheel to zoom.\n"
                       "• Press Z to toggle zoom-box mode.\n"
                       "• Right-click for axis options.\n"
                       "• Cross-lines follow your cursor showing m/z and intensity.",
            "anchor":  "above",
        },
        {
            "target":  lambda: (
                _get_app().peaks_action.associatedWidgets()[0]
                if _get_app().peaks_action.associatedWidgets()
                else _get_app().menu_bar
            ),
            "title":   "Peak list  (Ctrl+P)",
            "body":    "Open with  Peaks → Open Peaks List  or Ctrl+P.\n\n"
                       "Each row highlights a set of m/z values on the spectrum.\n"
                       "Rows can be defined by manual entry or as a range (start→end, step).\n"
                       "Press P to enable click-to-pick mode on the plot.\n\n"
                       "The Peaks window is a separate panel - open it to manage your peak lists.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().dt_combo,
            "title":   "dt filter",
            "body":    "Filter files by the dt value encoded in the filename (e.g. _dt071).\n"
                       "Only dt values present for the selected polarity are shown.\n"
                       "'All' disables the filter.\n"
                       "Each overlay has its own independent dt filter.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().menu_bar,
            "title":   "Processing menu",
            "body":    "Baseline Correction and Recalibration are in the Processing menu.\n\n"
                       "• airPLS / SNIP baseline correction removes background signal.\n"
                       "• Auto-Recalibrate fits a quadratic TOF polynomial automatically.\n"
                       "• Manual Recalibrate lets you choose anchor peaks yourself.\n"
                       "• Normalize Spectrum - output a file with intensities scaled to 1.\n"
                       "• Batch versions process entire folders.\n"
                       "• Residuals are saved in a subfolder alongside output files.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().menu_bar,
            "title":   "Analysis menu",
            "body":    "Advanced analysis tools are in the Analysis menu.\n\n"
                       "• Cluster Detection - finds repeating peak series (e.g. water clusters).\n"
                       "• Compare Common/Unique Peaks - highlights shared and unique peaks "
                       "across visible spectra with coloured vertical lines.\n"
                       "• Measure Peak Area - interactive range measurement, total spectrum "
                       "area, and peak-list area ratios.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().stacked_mode_chk,
            "title":   "Stacked mode",
            "body":    "Toggle 'Stacked' to view all visible spectra in separate sub-plots.\n\n"
                       "Each row is normalised to 0–1 so heights are directly comparable. "
                       "X axes are linked - panning one row moves all.\n\n"
                       "The 'Log Y' checkbox beside it switches all rows to a logarithmic Y scale.",
            "anchor":  "above",
        },
        {
            "target":  lambda: _get_app().sigma3_clip_chk,
            "title":   "Sigma noise clipping",
            "body":    "The 'σ Clip' checkbox suppresses baseline noise on the main plot.\n\n"
                       "The slider next to it (1.0 – 4.0 σ) sets how aggressively noise is clipped. "
                       "Lower values clip more; higher values keep more of the baseline.\n\n"
                       "This works independently of Dynamic Scale and is remembered between sessions.",
            "anchor":  "above",
        },
        {
            "target":  lambda: _get_app().menu_bar,
            "title":   "Normalisation (Processing menu)",
            "body":    "Processing → Normalize Spectrum scales intensities so the tallest peak = 1, "
                       "or so a specific m/z value = 1.\n\n"
                       "The result appears as an overlay with a 💾 Save button. "
                       "A batch version processes an entire folder at once.\n\n"
                       "No hard noise floor is applied - use σ Clip if you want to suppress baseline.",
            "anchor":  "below",
        },
        {
            "target":  lambda: _get_app().menu_bar,
            "title":   "You're all set!",
            "body":    "That covers the main features.\n\n"
                       "• Revisit any topic from  Help  in the menu bar.\n"
                       "• Hover over any button or slider for a tooltip.\n"
                       "• Ctrl+Q to quit.",
            "anchor":  "below",
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(QtCore.Qt.WindowType.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._step           = 0
        self._spotlight_rect = QtCore.QRect()
        self._parent_pixmap  = None

        self._bubble = QtWidgets.QFrame(self)
        self._bubble.setObjectName("tutorialBubble")
        self._bubble.setStyleSheet("""
            QFrame#tutorialBubble {
                background: #1e293b;
                border: 1.5px solid #3b82f6;
                border-radius: 10px;
            }
        """)
        bubble_layout = QtWidgets.QVBoxLayout(self._bubble)
        bubble_layout.setContentsMargins(14, 12, 14, 10)
        bubble_layout.setSpacing(6)

        self._title_lbl = QtWidgets.QLabel()
        self._title_lbl.setStyleSheet(
            "color: #60a5fa; font-size: 13px; font-weight: bold; background: transparent;")
        self._title_lbl.setWordWrap(True)
        bubble_layout.addWidget(self._title_lbl)

        self._body_lbl = QtWidgets.QLabel()
        self._body_lbl.setStyleSheet(
            "color: #e2e8f0; font-size: 11px; background: transparent;")
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setMinimumWidth(280)
        self._body_lbl.setMaximumWidth(360)
        bubble_layout.addWidget(self._body_lbl)

        ctrl_row = QtWidgets.QHBoxLayout()
        self._progress_lbl = QtWidgets.QLabel()
        self._progress_lbl.setStyleSheet(
            "color: #64748b; font-size: 10px; background: transparent;")
        ctrl_row.addWidget(self._progress_lbl)
        ctrl_row.addStretch()

        _btn_style_secondary = (
            "QPushButton { color: #94a3b8; background: transparent; "
            "border: 1px solid #475569; border-radius: 4px; padding: 0 8px; }"
            "QPushButton:hover { color: #e2e8f0; border-color: #94a3b8; }"
        )
        self._skip_btn = QtWidgets.QPushButton("Skip")
        self._skip_btn.setFixedHeight(26)
        self._skip_btn.setStyleSheet(_btn_style_secondary)
        self._skip_btn.clicked.connect(self.close_tutorial)

        self._back_btn = QtWidgets.QPushButton("← Back")
        self._back_btn.setFixedHeight(26)
        self._back_btn.setStyleSheet(_btn_style_secondary)
        self._back_btn.clicked.connect(self._go_back)

        self._next_btn = QtWidgets.QPushButton("Next →")
        self._next_btn.setFixedHeight(26)
        self._next_btn.setStyleSheet(
            "QPushButton { color: white; background: #3b82f6; "
            "border: none; border-radius: 4px; padding: 0 12px; font-weight: bold; }"
            "QPushButton:hover { background: #2563eb; }")
        self._next_btn.clicked.connect(self._go_next)

        ctrl_row.addWidget(self._skip_btn)
        ctrl_row.addWidget(self._back_btn)
        ctrl_row.addWidget(self._next_btn)
        bubble_layout.addLayout(ctrl_row)

        self._bubble.adjustSize()
        self._update_step()

    def _go_next(self):
        if self._step < len(self.STEPS) - 1:
            self._step += 1
            self._update_step()
        else:
            self.close_tutorial()

    def _go_back(self):
        if self._step > 0:
            self._step -= 1
            self._update_step()

    def _update_step(self):
        _a   = _get_app()
        step = self.STEPS[self._step]
        n    = len(self.STEPS)
        if "pre" in step:
            try:
                step["pre"](); _a.app.processEvents()
            except Exception:
                pass
        self._title_lbl.setText(step["title"])
        self._body_lbl.setText(step["body"])
        self._progress_lbl.setText(f"{self._step + 1} / {n}")
        self._back_btn.setEnabled(self._step > 0)
        is_last = self._step == n - 1
        self._next_btn.setText("Finish" if is_last else "Next →")
        self._skip_btn.setVisible(not is_last)
        try:
            target = step["target"]()
        except Exception:
            target = None
        self._compute_spotlight(target)
        self._position_bubble(target, step.get("anchor", "below"))
        self._refresh_parent_pixmap()
        self.update()

    def _compute_spotlight(self, target):
        if target is None or not target.isVisible():
            self._spotlight_rect = QtCore.QRect(); return
        tl = target.mapTo(self.parent(), QtCore.QPoint(0, 0))
        self._spotlight_rect = QtCore.QRect(tl, target.size()).adjusted(-6, -6, 6, 6)

    def _position_bubble(self, target, anchor):
        self._bubble.adjustSize()
        bw = self._bubble.width(); bh = self._bubble.height()
        pw = self.width();         ph = self.height()
        pad = 14
        if target is None or not target.isVisible() or self._spotlight_rect.isEmpty():
            self._bubble.move((pw - bw) // 2, (ph - bh) // 2); return
        sr = self._spotlight_rect
        if anchor == "below":
            x = max(pad, min(sr.left(), pw - bw - pad))
            y = min(sr.bottom() + pad, ph - bh - pad)
        elif anchor == "above":
            x = max(pad, min(sr.left(), pw - bw - pad))
            y = max(pad, sr.top() - bh - pad)
        elif anchor == "right":
            x = min(sr.right() + pad, pw - bw - pad)
            y = max(pad, min(sr.top(), ph - bh - pad))
        elif anchor == "left":
            x = max(pad, sr.left() - bw - pad)
            y = max(pad, min(sr.top(), ph - bh - pad))
        else:
            x = (pw - bw) // 2; y = (ph - bh) // 2
        self._bubble.move(x, y)

    def _refresh_parent_pixmap(self):
        if getattr(self, '_refreshing_pixmap', False): return
        self._refreshing_pixmap = True
        try:
            self.hide(); self._parent_pixmap = self.parent().grab(); self.show()
        finally:
            self._refreshing_pixmap = False

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor(0, 0, 0, 160))
        if not self._spotlight_rect.isEmpty() and self._parent_pixmap is not None:
            painter.drawPixmap(self._spotlight_rect, self._parent_pixmap, self._spotlight_rect)
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor("#3b82f6"), 2.0))
            painter.drawRoundedRect(self._spotlight_rect, 6, 6)
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not getattr(self, '_refreshing_pixmap', False):
            self._update_step()

    def close_tutorial(self):
        self.hide(); self.deleteLater()

    def mousePressEvent(self, event):
        if not self._bubble.geometry().contains(event.pos()):
            self._go_next()
        else:
            super().mousePressEvent(event)
