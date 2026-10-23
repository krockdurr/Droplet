"""Sidebar file list for the main window.

``FileBrowser`` shows the items of the main file combo box as a searchable
list. It shares the combo's model, so whatever fills the combo (folder
changes, mode / dt filters, projects) shows up here with no extra wiring, and
picking a row simply sets the combo's index: the rest of the app keeps
working through the combo as before.

LILBID file names such as
    2026-01-15-101500_Water-calibrant_neg_des_I1152_dt080_baseline+recalibrated.txt
are shown as two lines: the sample name, then mode · dt · date · processing.
"""

import os
import re

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui

from droplet_pkg.ui import theme
from droplet_pkg.ui.icons import themed as _themed_icons

_DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})[-_]?(\d{2})(\d{2})(\d{2})?[-_]?')
_POL_RE  = re.compile(r'_(pos|neg)(?=_|$)', re.IGNORECASE)
_DT_RE   = re.compile(r'_dt(\d+)', re.IGNORECASE)
_DATA_EXTENSIONS = (".txt", ".csv", ".tsv", ".dat", ".asc", ".json")
_TAG_WORDS = ("baseline", "recalibrated", "recal", "normalized", "normalised",
              "corr", "tof", "mass", "processed", "subtracted")


def describe(name):
    """Split a file name into (title, polarity, dt, date, tags)."""
    # Only a real extension is dropped: names hold dots ("FL5.40J"), and
    # splitext() would cut a name given without extension there.
    stem, ext = os.path.splitext(name)
    if ext.lower() not in _DATA_EXTENSIONS:
        stem = name
    date = ""
    m = _DATE_RE.match(stem)
    if m:
        date = m.group(1) + f" {m.group(2)}:{m.group(3)}"
        stem = stem[m.end():]
    pol = ""
    pm = _POL_RE.search(stem)
    if pm:
        pol = pm.group(1).lower()
    dt = ""
    dm = _DT_RE.search(stem)
    tags = ""
    if dm:
        dt = dm.group(1)
        tail = stem[dm.end():].strip("_-+ ")
        if tail:
            tags = tail.replace("_", " ").replace("+", " + ")
    title = stem[:pm.start()] if pm else (stem[:dm.start()] if dm else stem)
    if not pm and not dm:
        low = stem.lower()
        cut = min((low.find(w) for w in _TAG_WORDS if low.find(w) > 0), default=-1)
        if cut > 0:
            tags = stem[cut:].replace("_", " ")
            title = stem[:cut]
    title = title.strip("_- ").replace("__", "_").replace("_", " ") or stem
    return title, pol, dt, date, tags


# Optional item data understood by the delegate (used by the residuals viewer):
HEADER_ROLE = QtCore.Qt.ItemDataRole.UserRole + 10   # True: a section title row
BADGE_ROLE  = QtCore.Qt.ItemDataRole.UserRole + 11   # short text on the right of line 1
BADGE_COLOR_ROLE = QtCore.Qt.ItemDataRole.UserRole + 12   # its colour (hex), else muted


class _FileDelegate(QtWidgets.QStyledItemDelegate):
    ROW_H = 42
    HEADER_H = 26

    def sizeHint(self, option, index):
        h = self.HEADER_H if index.data(HEADER_ROLE) else self.ROW_H
        return QtCore.QSize(option.rect.width(), h)

    def _paint_header(self, painter, option, index, t):
        painter.save()
        f = QtGui.QFont(option.font)
        f.setPointSizeF(max(6.5, f.pointSizeF() - 2)); f.setBold(True)
        f.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 0.8)
        painter.setFont(f)
        painter.setPen(QtGui.QColor(t["muted"]))
        r = option.rect.adjusted(10, 6, -6, 0)
        text = (index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "").upper()
        painter.drawText(r, QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                         QtGui.QFontMetrics(f).elidedText(text, QtCore.Qt.TextElideMode.ElideRight,
                                                          r.width()))
        painter.restore()

    def paint(self, painter, option, index):
        t = theme.tokens()
        if index.data(HEADER_ROLE):
            self._paint_header(painter, option, index, t)
            return
        name = index.data(QtCore.Qt.ItemDataRole.DisplayRole) or ""
        path = index.data(QtCore.Qt.ItemDataRole.UserRole)
        r = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QtWidgets.QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if selected or hovered:
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(QtGui.QColor(t["accent_soft"] if selected else t["hover"]))
            painter.drawRoundedRect(QtCore.QRectF(r), 6, 6)
        if selected:
            painter.setBrush(QtGui.QColor(t["accent"]))
            painter.drawRoundedRect(QtCore.QRectF(r.left(), r.top() + 8, 3, r.height() - 16), 1.5, 1.5)

        font = QtGui.QFont(option.font)
        if path is None:                          # "No file in … mode" placeholder
            font.setItalic(True)
            painter.setFont(font)
            painter.setPen(QtGui.QColor(t["muted"]))
            painter.drawText(r.adjusted(10, 0, -6, 0),
                             QtCore.Qt.AlignmentFlag.AlignVCenter, name)
            painter.restore()
            return

        title, pol, dt, date, tags = describe(name)
        x = r.left() + 10
        w = r.width() - 16

        f1 = QtGui.QFont(font); f1.setWeight(QtGui.QFont.Weight.DemiBold)
        fm1 = QtGui.QFontMetrics(f1)
        badge = index.data(BADGE_ROLE)
        title_w = w
        if badge:                                 # e.g. "RMS 0.0156", right of the title
            fb = QtGui.QFont(font); fb.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
            fmb = QtGui.QFontMetrics(fb)
            bw = fmb.horizontalAdvance(badge) + 4
            painter.setFont(fb)
            painter.setPen(QtGui.QColor(index.data(BADGE_COLOR_ROLE) or t["muted"]))
            painter.drawText(QtCore.QRect(x + w - bw, r.top() + 4, bw, fm1.height()),
                             QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter,
                             badge)
            title_w = w - bw - 6
        painter.setFont(f1)
        painter.setPen(QtGui.QColor(t["text"]))
        painter.drawText(QtCore.QRect(x, r.top() + 4, title_w, fm1.height()),
                         QtCore.Qt.AlignmentFlag.AlignLeft,
                         fm1.elidedText(title, QtCore.Qt.TextElideMode.ElideRight, title_w))

        f2 = QtGui.QFont(font); f2.setPointSizeF(max(6.5, font.pointSizeF() - 1.5))
        painter.setFont(f2)
        fm2 = QtGui.QFontMetrics(f2)
        y2 = r.top() + 6 + fm1.height()
        cx = x
        if pol:                                   # coloured mode pill
            pill_w = fm2.horizontalAdvance(pol) + 10
            col = QtGui.QColor(t["pos"] if pol == "pos" else t["neg"])
            bg = QtGui.QColor(col); bg.setAlpha(40)
            painter.setPen(QtCore.Qt.PenStyle.NoPen); painter.setBrush(bg)
            painter.drawRoundedRect(QtCore.QRectF(cx, y2, pill_w, fm2.height() + 1), 6, 6)
            painter.setPen(col)
            painter.drawText(QtCore.QRect(int(cx), y2, pill_w, fm2.height() + 1),
                             QtCore.Qt.AlignmentFlag.AlignCenter, pol)
            cx += pill_w + 6
        meta = "  ·  ".join(s for s in (f"dt {dt}" if dt else "", date, tags) if s)
        painter.setPen(QtGui.QColor(t["muted"]))
        rest = int(x + w - cx)
        painter.drawText(QtCore.QRect(int(cx), y2, rest, fm2.height() + 1),
                         QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
                         fm2.elidedText(meta, QtCore.Qt.TextElideMode.ElideRight, rest))
        painter.restore()

    def helpEvent(self, event, view, option, index):
        if event.type() == QtCore.QEvent.Type.ToolTip:
            QtWidgets.QToolTip.showText(event.globalPos(),
                                        index.data(QtCore.Qt.ItemDataRole.DisplayRole) or "", view)
            return True
        return super().helpEvent(event, view, option, index)


class FileBrowser(QtWidgets.QWidget):
    """Searchable list view over ``combo``'s items, kept in sync with it."""

    def __init__(self, combo, parent=None):
        super().__init__(parent)
        self._combo = combo
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter files…")
        self.search.setClearButtonEnabled(True)
        self._search_action = self.search.addAction(
            QtGui.QIcon(), QtWidgets.QLineEdit.ActionPosition.LeadingPosition)
        _themed_icons.set(self._search_action, "search", "muted")
        lay.addWidget(self.search)

        self._proxy = QtCore.QSortFilterProxyModel(self)
        self._proxy.setSourceModel(combo.model())
        self._proxy.setFilterCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
        self.search.textChanged.connect(self._on_filter)

        self.view = QtWidgets.QListView()
        self.view.setObjectName("FileList")
        self.view.setModel(self._proxy)
        self.view.setItemDelegate(_FileDelegate(self.view))
        self.view.setMouseTracking(True)
        self.view.setUniformItemSizes(True)
        self.view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setMinimumHeight(2 * _FileDelegate.ROW_H + 8)
        self.view.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                QtWidgets.QSizePolicy.Policy.Expanding)
        lay.addWidget(self.view, 1)

        # Pick with a click, or move with the arrow keys
        self.view.clicked.connect(self._activate)
        self.view.selectionModel().currentChanged.connect(
            lambda cur, _prev: self._activate(cur) if self.view.hasFocus() else None)

        # Follow the combo: its index changes, and its list being rebuilt
        combo.currentIndexChanged.connect(lambda _i: self._sync())
        m = combo.model()
        for sig in (m.modelReset, m.rowsInserted, m.rowsRemoved):
            sig.connect(lambda *_: QtCore.QTimer.singleShot(0, self._sync))
        self._sync()

    def _on_filter(self, text):
        self._proxy.setFilterFixedString(text)
        self._sync()

    def _activate(self, proxy_index):
        if not proxy_index.isValid():
            return
        row = self._proxy.mapToSource(proxy_index).row()
        if row != self._combo.currentIndex():
            self._combo.setCurrentIndex(row)

    def _sync(self):
        idx = self._current_proxy_index()
        sm = self.view.selectionModel()
        sm.blockSignals(True)
        if idx.isValid():
            sm.setCurrentIndex(idx, QtCore.QItemSelectionModel.SelectionFlag.ClearAndSelect)
            self.view.scrollTo(idx, QtWidgets.QAbstractItemView.ScrollHint.EnsureVisible)
        else:
            sm.clearSelection()
        sm.blockSignals(False)
        self.view.viewport().update()

    def _current_proxy_index(self):
        """The current file's row in the filtered list (invalid if filtered out)."""
        src = self._combo.model().index(self._combo.currentIndex(), 0)
        return self._proxy.mapFromSource(src)

    def step(self, delta):
        """Select the next (+1) or previous (-1) file of the filtered list."""
        n = self._proxy.rowCount()
        if n == 0:
            return
        cur = self._current_proxy_index()
        row = cur.row() + delta if cur.isValid() else 0
        row = max(0, min(n - 1, row))
        self._activate(self._proxy.index(row, 0))

    def position_text(self):
        n = self._proxy.rowCount()
        if n == 0 or self._combo.itemData(self._combo.currentIndex()) is None:
            return "no file"
        cur = self._current_proxy_index()
        return f"{cur.row() + 1} / {n}" if cur.isValid() else f"– / {n}"
