from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PyQt6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCursor, QFont
from PyQt6.QtWidgets import QApplication, QGraphicsView, QWidget

from ui.widgets.canvas_graphics_adapter import (
    as_viewport_point,
    configure_scene_view,
    canvas_content_bounds,
    canvas_hint_frame,
    cancel_canvas_pan,
    ensure_view_pan_room,
    event_viewport_point,
    keep_scene_point_at_view_pos,
    sync_canvas_hints,
)
from ui.widgets.canvas_presentation import content_frame_rect
from ui.widgets.canvas_zoom import (
    FIT_VIEWPORT_MARGIN_PX,
    ZOOM_STEP,
    CanvasZoomControls,
    fit_graphics_view_to_rect,
    graphics_view_zoom_factor,
    set_graphics_view_zoom,
    zoom_graphics_view_by,
)


class CanvasViewportBackend(Protocol):
    def host_widget(self) -> QWidget: ...
    def viewport_center(self) -> QPoint: ...
    def capture_anchor(self, anchor) -> object: ...
    def apply_scale(self, factor: float) -> float: ...
    def apply_fit(self, margin_px: int) -> float: ...
    def restore_scale(self, factor: float) -> float: ...
    def keep_anchor(self, captured) -> None: ...
    def prepare_pan(self) -> None: ...
    def pan_by(self, dx: int, dy: int) -> None: ...


class GraphicsViewBackend:
    def __init__(self, view: QGraphicsView, content_rect_provider: Callable[[], QRectF], *, enable_view_pan_room: bool = True, content_frame: bool = False) -> None:
        self.view = view
        self._content_rect_provider = content_rect_provider
        self.enable_view_pan_room = bool(enable_view_pan_room)
        self.content_frame = content_frame
        self._fixed_content_rect: QRectF | None = None

    def host_widget(self) -> QWidget:
        return self.view

    def content_rect(self) -> QRectF:
        if self.content_frame and self._fixed_content_rect is not None:
            return QRectF(self._fixed_content_rect)
        hint_frame = canvas_hint_frame(self.view.scene())
        if hint_frame is not None:
            return QRectF(hint_frame)
        if self.content_frame:
            bounds = canvas_content_bounds(self.view.scene(), self._content_rect_provider())
            frame = content_frame_rect(bounds)
            if not bounds.isEmpty():
                # Screen paper belongs to the content identity, not live item geometry.
                # Object movement/resizing and same-document rebuilds leave it alone.
                self._fixed_content_rect = QRectF(frame)
            return frame
        rect = self._content_rect_provider()
        return QRectF(rect) if rect is not None else QRectF()

    def reset_content_frame(self) -> None:
        self._fixed_content_rect = None

    def viewport_center(self) -> QPoint:
        viewport = self.view.viewport()
        return viewport.rect().center() if viewport is not None else QPoint(0, 0)

    def capture_anchor(self, anchor) -> object:
        point = as_viewport_point(anchor if anchor is not None else self.viewport_center())
        return point, self.view.mapToScene(point)

    def apply_scale(self, factor: float) -> float:
        return zoom_graphics_view_by(self.view, factor)

    def apply_fit(self, margin_px: int) -> float:
        rect = self.content_rect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return graphics_view_zoom_factor(self.view)
        zoom = fit_graphics_view_to_rect(self.view, rect, margin_px=margin_px)
        if self.enable_view_pan_room:
            ensure_view_pan_room(self.view, rect)
        self.view.centerOn(rect.center())
        sync_canvas_hints(self.view)
        return zoom

    def restore_scale(self, factor: float) -> float:
        viewport = self.view.viewport()
        center = self.view.mapToScene(viewport.rect().center()) if viewport is not None else None
        zoom = set_graphics_view_zoom(self.view, factor)
        if self.enable_view_pan_room:
            ensure_view_pan_room(self.view, self.content_rect())
        if center is not None:
            self.view.centerOn(center)
        sync_canvas_hints(self.view)
        return zoom

    def keep_anchor(self, captured) -> None:
        point, scene_point = captured
        if self.enable_view_pan_room:
            ensure_view_pan_room(self.view, self.content_rect())
        keep_scene_point_at_view_pos(self.view, scene_point, point)
        sync_canvas_hints(self.view)

    def prepare_pan(self) -> None:
        if self.enable_view_pan_room:
            ensure_view_pan_room(self.view, self.content_rect())

    def pan_by(self, dx: int, dy: int) -> None:
        if dx:
            self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().value() - dx)
        if dy:
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().value() - dy)


class CanvasViewController(QObject):
    view_changed = pyqtSignal(float)
    def __init__(
        self,
        backend: CanvasViewportBackend,
        *,
        zoom_controls: CanvasZoomControls | None = None,
        owner=None,
        viewport_margin: int = FIT_VIEWPORT_MARGIN_PX,
        after_view_change: Callable[..., None] | None = None,
        fit_delays: tuple[int, ...] = (0,),
    ) -> None:
        super().__init__(backend.host_widget())
        self.backend = backend
        self.owner = owner
        self.zoom_controls = zoom_controls
        self.viewport_margin = int(viewport_margin)
        self._after_view_change = after_view_change
        self.fit_delays = fit_delays
        self.fit_mode = True
        self.zoom_factor = 1.0
        self._updating = False
        self._content_identity = object()
        self._viewport = None
        host = backend.host_widget()
        if isinstance(backend, GraphicsViewBackend):
            host.installEventFilter(self)
        self._fit_timer = QTimer(host)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._apply_scheduled_fit)
        self._fit_timer_late = QTimer(host)
        self._fit_timer_late.setSingleShot(True)
        self._fit_timer_late.timeout.connect(self._apply_scheduled_fit)
        if zoom_controls is not None:
            zoom_controls.fit_requested.connect(self.fit)
            zoom_controls.zoom_out_requested.connect(self.zoom_out)
            zoom_controls.zoom_in_requested.connect(self.zoom_in)
        viewport_host = host if hasattr(host, 'viewport') else getattr(owner, 'scroll_area', None)
        self._viewport = viewport_host.viewport() if viewport_host is not None else None
        if self._viewport is not None:
            self._viewport.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.FocusOut:
            cancel_canvas_pan(watched)
        if watched is self._viewport and event.type() in (QEvent.Type.Resize, QEvent.Type.Show) and self.fit_mode:
            # The viewport now has its real layout size. Do not paint the stale
            # hidden-canvas fit and wait another event-loop turn to correct it.
            self.fit()
        return False

    def fit(self) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self.fit_mode = True
            self.zoom_factor = self.backend.apply_fit(self.viewport_margin)
            self._sync_controls()
            self._notify(fit=True)
        finally:
            self._updating = False

    def zoom_by(self, factor: float, anchor=None) -> None:
        if self._updating:
            return
        self._updating = True
        try:
            self.fit_mode = False
            captured = self.backend.capture_anchor(anchor)
            self.zoom_factor = self.backend.apply_scale(factor)
            self._sync_controls()
            self._notify(fit=False)
            self.backend.keep_anchor(captured)
        finally:
            self._updating = False

    def zoom_in(self) -> None:
        self.zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        self.zoom_by(1 / ZOOM_STEP)

    def begin_pan(self) -> None:
        self.fit_mode = False
        self.backend.prepare_pan()

    def pan_by(self, dx: int, dy: int) -> None:
        self.fit_mode = False
        self.backend.pan_by(dx, dy)

    def handle_wheel(self, event, anchor=None) -> bool:
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            delta = event.pixelDelta()
            if delta.isNull():
                angle = event.angleDelta()
                delta = QPoint(round(angle.x() / 2), round(angle.y() / 2))
            if delta.isNull():
                return False
            self.begin_pan()
            self.pan_by(delta.x(), delta.y())
            event.accept()
            return True
        delta = event.angleDelta().y()
        if delta == 0:
            event.accept()
            return True
        self.zoom_by(ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP, anchor=anchor if anchor is not None else event_viewport_point(event))
        event.accept()
        return True

    def handle_zoom_shortcut(self, event) -> bool:
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            return False
        key = event.key()
        if key in {Qt.Key.Key_Plus, Qt.Key.Key_Equal}:
            self.zoom_by(ZOOM_STEP)
            event.accept()
            return True
        if key == Qt.Key.Key_Minus:
            self.zoom_by(1 / ZOOM_STEP)
            event.accept()
            return True
        if key == Qt.Key.Key_0:
            self.fit()
            event.accept()
            return True
        return False

    def set_content_identity(self, identity) -> None:
        """New content starts fitted; presentation-only refreshes keep the view."""
        if identity != self._content_identity:
            self._content_identity = identity
            if isinstance(self.backend, GraphicsViewBackend):
                self.backend.reset_content_frame()
            self.fit_mode = True
            self.schedule_fit()

    def apply_after_content_change(self) -> None:
        if self._updating:
            return
        if self.fit_mode:
            self.fit()
            self.schedule_fit()
            return
        self.zoom_factor = self.backend.restore_scale(self.zoom_factor)
        self._sync_controls()
        self._notify(fit=False)

    def schedule_fit(self) -> None:
        if self._updating or not self.fit_mode:
            return
        delays = self.fit_delays or (0,)
        self._fit_timer.start(max(0, int(delays[0])))
        if len(delays) > 1:
            self._fit_timer_late.start(max(0, int(delays[1])))

    def handle_resize(self) -> None:
        if self.fit_mode:
            self.schedule_fit()

    def _apply_scheduled_fit(self) -> None:
        host = self.backend.host_widget()
        if host is None:
            return
        if self.fit_mode:
            self.fit()

    def _sync_controls(self) -> None:
        if self.zoom_controls is not None:
            self.zoom_controls.set_zoom_factor(self.zoom_factor)
        self.view_changed.emit(self.zoom_factor)

    def _notify(self, *, fit: bool) -> None:
        if self._after_view_change is not None:
            self._after_view_change(fit=fit)


class CanvasNavigationFilter(QObject):
    """Navigation on widget-backed canvases, ahead of their scientific tools."""
    def __init__(self, controller: CanvasViewController, viewport: QWidget) -> None:
        super().__init__(viewport)
        self._controller = controller
        self.viewport = viewport
        self.space = False
        self.panning = False
        self.last_pos = QPoint()
        self._cursor = QCursor()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel:
            anchor = self.viewport.mapFromGlobal(watched.mapToGlobal(event_viewport_point(event)))
            return self._controller.handle_wheel(event, anchor=anchor)
        if event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Space:
                if not self.space:
                    self._cursor = QCursor(watched.cursor())
                self.space = True
                watched.setCursor(Qt.CursorShape.OpenHandCursor)
                return True
            return self._controller.handle_zoom_shortcut(event)
        if event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Space:
            self.space = False
            self.panning = False
            watched.setCursor(self._cursor)
            return True
        if event.type() == QEvent.Type.MouseButtonPress and self.space and event.button() == Qt.MouseButton.LeftButton:
            self.panning = True
            self.last_pos = event.globalPosition().toPoint()
            self._controller.begin_pan()
            watched.setCursor(Qt.CursorShape.ClosedHandCursor)
            return True
        if event.type() == QEvent.Type.MouseMove and self.panning:
            pos = event.globalPosition().toPoint()
            delta = pos - self.last_pos
            self.last_pos = pos
            self._controller.pan_by(delta.x(), delta.y())
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and self.panning and event.button() == Qt.MouseButton.LeftButton:
            self.panning = False
            watched.setCursor(Qt.CursorShape.OpenHandCursor if self.space else self._cursor)
            return True
        if event.type() == QEvent.Type.FocusOut:
            focus = QApplication.focusWidget()
            if focus is not None and focus is not watched and (focus is self.viewport or self.viewport.isAncestorOf(focus)):
                if self.space:
                    watched.setCursor(self._cursor)
                    self._cursor = QCursor(focus.cursor())
                    focus.setCursor(Qt.CursorShape.OpenHandCursor)
                return False
            if self.space:
                watched.setCursor(self._cursor)
            self.space = self.panning = False
        return False


class CanvasFontAction(QObject):
    """A signal receiver owned by its canvas, disconnected with that canvas."""
    def __init__(self, signal, canvas, callback):
        super().__init__(canvas)
        self.callback = callback
        signal.connect(self.invoke)

    @pyqtSlot(QFont)
    def invoke(self, font):
        self.callback(font)


class SceneCanvasOwnerMixin:
    def fit_to_canvas(self) -> None:
        self._view_controller.fit()

    def zoom_by(self, factor: float, anchor=None) -> None:
        self._view_controller.zoom_by(factor, anchor=anchor)

    def _apply_canvas_zoom(self) -> None:
        self._view_controller.apply_after_content_change()

    @property
    def _fit_mode(self) -> bool:
        return self._view_controller.fit_mode

    @_fit_mode.setter
    def _fit_mode(self, value: bool) -> None:
        self._view_controller.fit_mode = bool(value)

    @property
    def _zoom_factor(self) -> float:
        return self._view_controller.zoom_factor

    @_zoom_factor.setter
    def _zoom_factor(self, value: float) -> None:
        self._view_controller.zoom_factor = float(value)


def attach_canvas_controller(
    owner,
    backend: CanvasViewportBackend,
    *,
    zoom_controls: CanvasZoomControls | None = None,
    after_view_change: Callable[..., None] | None = None,
    fit_delays: tuple[int, ...] = (0,),
    viewport_margin: int = FIT_VIEWPORT_MARGIN_PX,
) -> CanvasViewController:
    controller = CanvasViewController(
        backend,
        zoom_controls=zoom_controls,
        owner=owner,
        viewport_margin=viewport_margin,
        after_view_change=after_view_change,
        fit_delays=fit_delays,
    )
    owner._view_controller = controller
    return controller


def attach_scene_canvas_controller(
    owner,
    view: QGraphicsView,
    *,
    content_rect_provider: Callable[[], QRectF],
    zoom_controls: CanvasZoomControls | None = None,
    after_view_change: Callable[..., None] | None = None,
    fit_delays: tuple[int, ...] = (0,),
    viewport_margin: int = FIT_VIEWPORT_MARGIN_PX,
    enable_view_pan_room: bool = True,
    content_frame: bool = False,
    editable=False,
) -> CanvasViewController:
    configure_scene_view(view, owner=owner)
    backend = GraphicsViewBackend(view, content_rect_provider, enable_view_pan_room=enable_view_pan_room, content_frame=content_frame)
    controller = attach_canvas_controller(
        owner,
        backend,
        zoom_controls=zoom_controls,
        after_view_change=after_view_change,
        fit_delays=fit_delays,
        viewport_margin=viewport_margin,
    )
    if editable:
        from ui.widgets.canvas_object_interaction import CanvasObjectInteraction
        view.object_interaction = CanvasObjectInteraction(view, editable)
        if zoom_controls is not None:
            zoom_controls.enable_object_controls(view.object_interaction)
            controller.view_changed.connect(lambda _zoom: zoom_controls.object_controls.setEnabled(view.object_interaction.enabled()))
    return controller
