from __future__ import annotations

from dataclasses import replace

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QPainter

from core.image_browser import ScaleBarState, draw_scale_bar, scale_bar_layout
from ui.widgets.preview_canvas import PreviewCanvas, SelectableImageLabel
from ui.widgets.image_navigation import ImageNavigationButton
from core.utils.i18n import tr


class ImageBrowserScaleBarLabel(SelectableImageLabel):
    """Image Browser-only image label with a draggable scale-bar overlay."""

    scale_bar_moved = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.scale_bar_state = ScaleBarState()
        self._scale_bar_dragging = False
        self._scale_bar_drag_start_pos: QPoint | None = None
        self._scale_bar_drag_start_state: ScaleBarState | None = None

    def set_scale_bar_state(self, state: ScaleBarState) -> None:
        self.scale_bar_state = replace(state)
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if not self.scale_bar_state.visible or self.pixmap() is None or self.pixmap().isNull() or self._source_size.isEmpty():
            return
        target = QRectF(self._pixmap_rect())
        painter = QPainter(self)
        painter.setClipRect(target)
        draw_scale_bar(painter, target, self._source_size, self.scale_bar_state)
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.line_selection_enabled and event.button() == Qt.MouseButton.LeftButton and self._start_scale_bar_drag(event.position().toPoint()):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._scale_bar_dragging:
            self._update_scale_bar_drag(event.position().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._scale_bar_dragging and event.button() == Qt.MouseButton.LeftButton:
            self._update_scale_bar_drag(event.position().toPoint())
            self._scale_bar_dragging = False
            self._scale_bar_drag_start_pos = None
            self._scale_bar_drag_start_state = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _start_scale_bar_drag(self, point: QPoint) -> bool:
        if not self.scale_bar_state.visible or self.pixmap() is None or self.pixmap().isNull() or self._source_size.isEmpty():
            return False
        target = QRectF(self._pixmap_rect())
        try:
            layout = scale_bar_layout(target, self._source_size, self.scale_bar_state)
        except ValueError:
            return False
        if not layout.bounds.contains(QPointF(point)):
            return False
        mapped = self._map_label_point_to_source(point)
        if mapped is None:
            return False
        source_target = QRectF(0.0, 0.0, float(self._source_size.width()), float(self._source_size.height()))
        source_layout = scale_bar_layout(source_target, self._source_size, self.scale_bar_state)
        self._scale_bar_dragging = True
        self._scale_bar_drag_start_pos = mapped
        self._scale_bar_drag_start_state = replace(
            self.scale_bar_state,
            # ``source_layout`` is already expressed in native pixels.  Do
            # not divide its center by the display scale a second time: that
            # would make the drag origin drift whenever the image is fitted or
            # zoomed.
            center_x_px=float(source_layout.line_rect.center().x() - source_target.left()),
            line_y_px=float(source_layout.line_rect.center().y() - source_target.top()),
        )
        return True

    def _update_scale_bar_drag(self, point: QPoint) -> None:
        if not self._scale_bar_dragging or self._scale_bar_drag_start_pos is None or self._scale_bar_drag_start_state is None:
            return
        mapped = self._map_label_point_to_source(self._clamp_to_pixmap(point))
        if mapped is None:
            return
        start_pos = self._scale_bar_drag_start_pos
        candidate = replace(
            self._scale_bar_drag_start_state,
            center_x_px=float(self._scale_bar_drag_start_state.center_x_px + mapped.x() - start_pos.x()),
            line_y_px=float(self._scale_bar_drag_start_state.line_y_px + mapped.y() - start_pos.y()),
        )
        target = QRectF(0.0, 0.0, float(self._source_size.width()), float(self._source_size.height()))
        layout = scale_bar_layout(target, self._source_size, candidate)
        candidate.center_x_px = float(layout.line_rect.center().x())
        candidate.line_y_px = float(layout.line_rect.center().y())
        self.set_scale_bar_state(candidate)
        self.scale_bar_moved.emit(replace(candidate))

    def _clamp_to_pixmap(self, point: QPoint) -> QPoint:
        rect = self._pixmap_rect()
        if rect.isEmpty():
            return point
        return QPoint(
            min(max(point.x(), rect.left()), rect.right()),
            min(max(point.y(), rect.top()), rect.bottom()),
        )


class ImageBrowserCanvas(PreviewCanvas):
    """PreviewCanvas variant that owns the Image Browser scale-bar label."""

    scale_bar_moved = pyqtSignal(object)
    previous_requested = pyqtSignal()
    next_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        self.scale_bar_state = ScaleBarState()
        super().__init__(parent)
        viewport=self.scroll_area.viewport()
        self._navigation_viewport=viewport
        self.previous_button=ImageNavigationButton(-1,viewport)
        self.next_button=ImageNavigationButton(1,viewport)
        self.previous_button.setObjectName('ImageBrowserPrevious')
        self.next_button.setObjectName('ImageBrowserNext')
        self.previous_button.clicked.connect(lambda:self.previous_requested.emit())
        self.next_button.clicked.connect(lambda:self.next_requested.emit())
        self._navigation_visible=False
        viewport.installEventFilter(self)
        self.set_navigation_state(False,False,visible=False)
        self.retranslate()

    def set_navigation_state(self,previous,next_,*,visible=True):
        self._navigation_visible=bool(visible)
        self.previous_button.setEnabled(bool(previous));self.next_button.setEnabled(bool(next_))
        drawing=self.image_label is not None and self.image_label.line_selection_enabled
        for button in (self.previous_button,self.next_button):
            button.setVisible(self._navigation_visible and not drawing)
            button.raise_()
        self._position_navigation()

    def _position_navigation(self):
        viewport=self.scroll_area.viewport()
        y=max(0,(viewport.height()-self.previous_button.height())//2)
        self.previous_button.move(10,y)
        self.next_button.move(max(10,viewport.width()-self.next_button.width()-10),y)

    def eventFilter(self,watched,event):
        if watched is self._navigation_viewport and event.type()==QEvent.Type.Resize:
            self._position_navigation()
        return super().eventFilter(watched,event)

    def set_line_selection_enabled(self,enabled):
        super().set_line_selection_enabled(enabled)
        if self.image_label is not None:
            self.image_label.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)
        for button in (self.previous_button,self.next_button):
            button.setVisible(self._navigation_visible and not enabled)

    def retranslate(self):
        super().retranslate()
        if not hasattr(self,'previous_button'):return
        for button,key in ((self.previous_button,'viewer_previous_image'),(self.next_button,'viewer_next_image')):
            button.setToolTip(tr(key));button.setAccessibleName(tr(key))

    def _make_image_label(self) -> SelectableImageLabel:
        label = ImageBrowserScaleBarLabel()
        label.set_scale_bar_state(self.scale_bar_state)
        label.scale_bar_moved.connect(self._on_scale_bar_moved)
        return label

    def set_scale_bar_state(self, state: ScaleBarState) -> None:
        self.scale_bar_state = replace(state)
        if isinstance(self.image_label, ImageBrowserScaleBarLabel):
            self.image_label.set_scale_bar_state(self.scale_bar_state)

    def _on_scale_bar_moved(self, state: ScaleBarState) -> None:
        self.scale_bar_state = replace(state)
        self.scale_bar_moved.emit(replace(self.scale_bar_state))
