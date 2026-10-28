from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QCursor, QFont, QFontMetricsF, QKeyEvent
from PyQt6.QtWidgets import QFrame, QGraphicsObject, QGraphicsRectItem, QGraphicsTextItem, QGraphicsView
from ui.widgets.canvas_presentation import REFERENCE_PAPER_SIZE, configure_canvas_viewport, paint_canvas_paper, paint_canvas_surround


SCENE_CANVAS_PROPERTY = "sciefigSceneCanvas"


class CanvasHintItem(QGraphicsObject):
    """A fixed-size UI message, not image content or an editable annotation."""
    def __init__(self, text, title=''):
        super().__init__()
        self.frame_rect = QRectF(0,0,*REFERENCE_PAPER_SIZE)
        self._text = text
        self._title = title
        self._font = QFont()
        self._font.setPixelSize(11)
        self._title_font = QFont(self._font)
        self._title_font.setPixelSize(14)
        self._title_font.setBold(True)
        self._color = QColor("#888888")
        self._width = 480
        self._height = 40
        self.setFlag(self.GraphicsItemFlag.ItemIgnoresTransformations)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setPos(self.frame_rect.center())
        self._reflow()

    def _reflow(self, width=None):
        self.prepareGeometryChange()
        if width is not None:
            self._width = width
        rect = QFontMetricsF(self._font).boundingRect(
            QRectF(0,0,self._width,10000), int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter), self._text)
        self._height = max(24,rect.height()+8)
        self._title_height = 0
        if self._title:
            title_rect = QFontMetricsF(self._title_font).boundingRect(
                QRectF(0,0,self._width,10000), int(Qt.TextFlag.TextWordWrap | Qt.AlignmentFlag.AlignCenter), self._title)
            self._title_height = title_rect.height() + 8
            self._height += self._title_height
        self.update()

    def set_wrap_width(self, width):
        width = max(1,min(480,int(width)))
        if width != self._width:
            self._reflow(width)

    def boundingRect(self):
        return QRectF(-self._width/2,-self._height/2,self._width,self._height)

    def paint(self,painter,option,widget=None):
        rect = self.boundingRect()
        if self._title:
            painter.setFont(self._title_font)
            painter.setPen(QColor('#333333'))
            painter.drawText(QRectF(rect.x(),rect.y(),rect.width(),self._title_height-8),
                             Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._title)
            rect.setTop(rect.top()+self._title_height)
        painter.setFont(self._font)
        painter.setPen(self._color)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, self._text)

    def title(self):
        return self._title

    def setTitle(self, title):
        self._title = title
        self._reflow()

    def toPlainText(self):
        return self._text

    def setPlainText(self,text):
        self._text = text
        self._reflow()

    def font(self):
        return self._font


def add_canvas_hint(scene, text, *, title=''):
    item = CanvasHintItem(text, title)
    scene.addItem(item)
    scene.setSceneRect(item.frame_rect)
    return item


def canvas_hint_frame(scene):
    frame = None
    for item in scene.items():
        if not item.isVisible() or isinstance(item,CanvasExportBackgroundItem):
            continue
        if not isinstance(item,CanvasHintItem):
            return None
        frame = item.frame_rect
    return frame


def sync_canvas_hints(view):
    for item in view.scene().items():
        if isinstance(item,CanvasHintItem):
            item.set_wrap_width(view.viewport().width()-48)


class CanvasExportBackgroundItem(QGraphicsRectItem):
    """Legacy export backdrop is not the editor's screen frame."""
    def paint(self, painter, option, widget=None):
        if widget is None:
            super().paint(painter,option,widget)


def canvas_content_bounds(scene, text_fallback=None):
    bounds = QRectF()
    text_only = True
    for item in scene.items():
        if item.isVisible() and not isinstance(item, (CanvasExportBackgroundItem,CanvasHintItem)):
            bounds = bounds.united(item.sceneBoundingRect())
            text_only = text_only and isinstance(item,QGraphicsTextItem)
    if text_only and text_fallback is not None:
        return QRectF(text_fallback)
    return bounds


class CanvasGraphicsView(QGraphicsView):
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.scene() is not None:
            sync_canvas_hints(self)

    def drawBackground(self, painter, rect):
        paint_canvas_surround(painter,rect)
        controller = view_controller_of(self)
        if controller is None:
            return
        frame = controller.backend.content_rect()
        if frame.isEmpty():
            return
        owner = canvas_owner_of(self)
        spec = getattr(owner,"canvas_spec",None)
        color = getattr(spec,"background_color","#ffffff")
        paint_canvas_paper(painter,frame,abs(self.transform().m11()),color)


def as_viewport_point(anchor) -> QPoint:
    if isinstance(anchor, QPoint):
        return QPoint(anchor)
    if isinstance(anchor, QPointF):
        return anchor.toPoint()
    if hasattr(anchor, "x") and hasattr(anchor, "y"):
        return QPoint(int(anchor.x()), int(anchor.y()))
    x, y = anchor
    return QPoint(int(x), int(y))


def event_viewport_point(event) -> QPoint:
    position = getattr(event, "position", None)
    if callable(position):
        value = position()
        if isinstance(value, QPoint):
            return QPoint(value)
        to_point = getattr(value, "toPoint", None)
        if callable(to_point):
            return to_point()
    pos = getattr(event, "pos", None)
    if callable(pos):
        value = pos()
        if isinstance(value, QPoint):
            return QPoint(value)
        to_point = getattr(value, "toPoint", None)
        if callable(to_point):
            return to_point()
    raise AttributeError("Event does not provide a viewport QPoint-compatible position")


def canvas_owner_of(view):
    owner = getattr(view, "canvas_owner", None)
    return owner if owner is not None else view.parent()


def view_controller_of(view):
    owner = canvas_owner_of(view)
    return getattr(owner, "_view_controller", None) if owner is not None else None


def is_canvas_text_editing(view) -> bool:
    scene = view.scene() if callable(getattr(view, "scene", None)) else None
    focused = scene.focusItem() if scene is not None else None
    if focused is None:
        return False
    if bool(getattr(focused, "is_editing", False)):
        return True
    flags = getattr(focused, "textInteractionFlags", None)
    if callable(flags) and isinstance(focused, QGraphicsTextItem):
        return bool(flags() & Qt.TextInteractionFlag.TextEditorInteraction)
    return False


def keep_scene_point_at_view_pos(view, scene_point, view_pos: QPoint) -> None:
    mapped = view.mapFromScene(scene_point)
    dx = mapped.x() - view_pos.x()
    dy = mapped.y() - view_pos.y()
    if abs(dx) >= 0.5:
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + int(round(dx)))
    if abs(dy) >= 0.5:
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() + int(round(dy)))


def ensure_view_pan_room(view, content: QRectF) -> None:
    if content is None or content.isNull() or content.width() <= 0 or content.height() <= 0:
        return
    viewport = view.viewport()
    scale = abs(float(view.transform().m11())) or 1.0
    extra_w = max(240.0, float(viewport.width()) / scale if viewport is not None else 240.0)
    extra_h = max(240.0, float(viewport.height()) / scale if viewport is not None else 240.0)
    expanded = content.adjusted(-extra_w, -extra_h, extra_w, extra_h)
    current = QRectF(view.sceneRect())
    if (
        abs(current.x() - expanded.x()) < 1.0
        and abs(current.y() - expanded.y()) < 1.0
        and abs(current.width() - expanded.width()) < 1.0
        and abs(current.height() - expanded.height()) < 1.0
    ):
        return
    view.setSceneRect(expanded)


def configure_scene_view(view: QGraphicsView, *, owner=None) -> None:
    if owner is not None:
        view.canvas_owner = owner
    view.setProperty(SCENE_CANVAS_PROPERTY, True)
    configure_canvas_viewport(view)
    view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    view.setFrameShape(QFrame.Shape.NoFrame)
    # The screen frame follows live content; it must not reuse stale background pixels.
    view.setCacheMode(QGraphicsView.CacheModeFlag.CacheNone)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
    view.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
    view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
    if not hasattr(view, "_space_pressed"):
        view._space_pressed = False
        view._panning = False
        view._last_pan_pos = QPoint()
        view._saved_cursor = None
    style = view.style()
    style.unpolish(view)
    style.polish(view)


def handle_canvas_wheel(view, event) -> bool:
    controller = view_controller_of(view)
    if controller is None:
        return False
    return controller.handle_wheel(event)


def handle_canvas_zoom_shortcut(view, event: QKeyEvent) -> bool:
    if is_canvas_text_editing(view):
        return False
    controller = view_controller_of(view)
    if controller is None:
        return False
    return controller.handle_zoom_shortcut(event)


def _remember_cursor(view) -> None:
    if getattr(view, "_saved_cursor", None) is None:
        view._saved_cursor = QCursor(view.cursor())


def _restore_cursor(view) -> None:
    saved = getattr(view, "_saved_cursor", None)
    view._saved_cursor = None
    if saved is not None:
        view.setCursor(saved)
    else:
        view.unsetCursor()


def handle_canvas_space_press(view, event) -> bool:
    if is_canvas_text_editing(view):
        return False
    if event.key() != Qt.Key.Key_Space:
        return False
    view._space_pressed = True
    _remember_cursor(view)
    view.setCursor(Qt.CursorShape.OpenHandCursor)
    event.accept()
    return True


def handle_canvas_space_release(view, event) -> bool:
    if event.key() != Qt.Key.Key_Space:
        return False
    view._space_pressed = False
    view._panning = False
    _restore_cursor(view)
    event.accept()
    return True


def cancel_canvas_pan(view) -> None:
    view._space_pressed = False
    view._panning = False
    _restore_cursor(view)


def handle_canvas_pan_press(view, event) -> bool:
    if event.button() != Qt.MouseButton.LeftButton or not getattr(view, "_space_pressed", False):
        return False
    view._panning = True
    view._last_pan_pos = event_viewport_point(event)
    view.setCursor(Qt.CursorShape.ClosedHandCursor)
    controller = view_controller_of(view)
    if controller is not None:
        controller.begin_pan()
    event.accept()
    return True


def handle_canvas_pan_move(view, event) -> bool:
    if not getattr(view, "_panning", False):
        return False
    current = event_viewport_point(event)
    delta = current - view._last_pan_pos
    view._last_pan_pos = current
    controller = view_controller_of(view)
    if controller is not None:
        controller.pan_by(delta.x(), delta.y())
    else:
        view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() - delta.x())
        view.verticalScrollBar().setValue(view.verticalScrollBar().value() - delta.y())
    event.accept()
    return True


def handle_canvas_pan_release(view, event) -> bool:
    if event.button() != Qt.MouseButton.LeftButton or not getattr(view, "_panning", False):
        return False
    view._panning = False
    if getattr(view, "_space_pressed", False):
        view.setCursor(Qt.CursorShape.OpenHandCursor)
    else:
        _restore_cursor(view)
    event.accept()
    return True
