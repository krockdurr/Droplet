"""Reusable Qt widget classes.

These widgets have no dependency on application state and can be imported
without a live QApplication (as long as one exists before instantiation).
"""

try:
    from PyQt6 import QtWidgets, QtCore, QtGui
    from PyQt6.QtGui import QAction
    QtWidgets.QAction = QAction
except ImportError:
    from pyqtgraph.Qt import QtWidgets, QtCore, QtGui


class CollapsibleSection(QtWidgets.QWidget):
    """A titled section that can be expanded or collapsed by clicking its header."""

    def __init__(self, title, parent=None, collapsed=False):
        super().__init__(parent)
        self._collapsed = collapsed
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._header = QtWidgets.QPushButton()
        self._header.setFlat(True)
        self._header.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._header.setStyleSheet(
            "QPushButton { text-align: left; padding: 2px 6px; "
            "font-weight: bold; font-size: 11px; border: none; "
            "border-bottom: 1px solid palette(mid); }")
        self._header.clicked.connect(self.toggle)
        outer.addWidget(self._header)
        self._content = QtWidgets.QWidget()
        self._content_layout = QtWidgets.QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(4, 2, 4, 2)
        self._content_layout.setSpacing(2)
        outer.addWidget(self._content)
        self._update_header(title)
        self._content.setVisible(not collapsed)

    def _update_header(self, title=None):
        if title:
            self._title = title
        arrow = "▸" if self._collapsed else "▾"
        self._header.setText(f"  {arrow}  {self._title}")

    def toggle(self):
        self._collapsed = not self._collapsed
        self._content.setVisible(not self._collapsed)
        self._update_header()

    def add_layout(self, layout):
        self._content_layout.addLayout(layout)

    def add_widget(self, widget):
        self._content_layout.addWidget(widget)


class ElidedComboBox(QtWidgets.QComboBox):
    """ComboBox that widens the popup so long filenames are never cropped."""

    def showPopup(self):
        fm = self.fontMetrics()
        max_w = max((fm.boundingRect(self.itemText(i)).width()
                     for i in range(self.count())), default=0)
        self.view().setMinimumWidth(max_w + 32)
        super().showPopup()


class JumpSlider(QtWidgets.QSlider):
    """Slider that jumps directly to the clicked position on a single click."""

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            val = QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(),
                event.pos().x(), self.width())
            self.setValue(val)
        super().mousePressEvent(event)


class CrosshairCursorFilter(QtCore.QObject):
    """Event filter that shows a crosshair cursor when the mouse enters a widget."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Enter:
            obj.setCursor(QtCore.Qt.CursorShape.CrossCursor)
        elif event.type() == QtCore.QEvent.Type.Leave:
            obj.unsetCursor()
        return False


class PeakRowsContainer(QtWidgets.QWidget):
    """Vertical container for peak rows that supports drag-to-reorder.

    The caller must connect on_reorder(from_idx, to_idx) via the
    reorder_callback parameter or by subclassing.
    """

    def __init__(self, parent=None, reorder_callback=None):
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self._reorder_callback = reorder_callback

    def add_row_widget(self, widget):
        self._layout.addWidget(widget)

    def remove_row_widget(self, widget):
        self._layout.removeWidget(widget)
        widget.hide()

    def move_row(self, from_idx, to_idx):
        if from_idx == to_idx:
            return
        item = self._layout.takeAt(from_idx)
        if item is None:
            return
        self._layout.insertItem(to_idx, item)
        if self._reorder_callback:
            self._reorder_callback(from_idx, to_idx)
