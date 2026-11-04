from __future__ import annotations

from core.constants import WB_STEP_DENSITY

from pathlib import Path
from io import BytesIO

from PyQt6.QtCore import QByteArray, QPoint, QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsItemGroup,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QInputDialog,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtSvgWidgets import QGraphicsSvgItem

from core.models import WesternBlotPanel
from core.models.layout_editor import LayoutObject,LayoutObjectStyle,LayoutSourceIdentity
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem
from uuid import uuid4
from core.plotting.statistics_plot import resolve_statistics_plot_style
from core.plotting_densitometry import build_wb_statistics_figure
from core.utils.i18n import tr
from core.utils.svg_text import apply_svg_text_overrides
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action
from ui.widgets.editable_text_item import (
    EditableMovableTextItem,
    clear_graphics_scene_for_cleanup,
    graphics_item_is_alive,
    register_graphics_scene_for_cleanup,
)
from ui.widgets.svg_text_editor import prompt_svg_text_edit
from ui.widgets.canvas_presentation import configure_canvas_layout
from ui.widgets.canvas_graphics_adapter import (
    CanvasGraphicsView,
    add_canvas_hint,
    event_viewport_point as _event_viewport_point,
    handle_canvas_pan_move,
    handle_canvas_pan_press,
    handle_canvas_pan_release,
    handle_canvas_space_press,
    handle_canvas_space_release,
    handle_canvas_wheel,
    handle_canvas_zoom_shortcut,
    is_canvas_text_editing,
)
from ui.widgets.canvas_view_controller import SceneCanvasOwnerMixin, attach_scene_canvas_controller
from ui.widgets.canvas_zoom import CanvasZoomControls
from ui.widgets.preview_canvas import dropped_preview_file_paths

WB_STRIP_MIME = "application/x-sciefig-wb-strip-index"


def _is_layout_annotation_item(item: QGraphicsItem) -> bool:
    return hasattr(item, "style_payload") or item.data(0) == "wb_layout_line"


class WBGraphicsView(CanvasGraphicsView):
    files_dropped = pyqtSignal(list)
    roi_selected = pyqtSignal(QRectF)
    lane_anchor_selected = pyqtSignal(float)
    marker_anchor_selected = pyqtSignal(float)
    delete_selected_crop_requested = pyqtSignal()
    reorder_selected_crop_requested = pyqtSignal(int)
    strip_dropped = pyqtSignal(int)
    context_action_requested = pyqtSignal(str)

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheNone)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.mode = "roi"
        self._press_scene_pos: QPointF | None = None
        self._roi_rect_item: QGraphicsRectItem | None = None
        self._space_pressed = False
        self._panning = False
        self._last_pan_pos = QPoint()

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._clear_live_roi()
        if mode == "move":
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        elif mode in {"roi", "text_select"}:
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(WB_STRIP_MIME):
            event.acceptProposedAction()
            return
        if dropped_preview_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(WB_STRIP_MIME):
            event.acceptProposedAction()
            return
        if dropped_preview_file_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(WB_STRIP_MIME):
            try:
                self.strip_dropped.emit(int(bytes(event.mimeData().data(WB_STRIP_MIME)).decode("utf-8")))
                event.acceptProposedAction()
                return
            except ValueError:
                pass
        paths = dropped_preview_file_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def mousePressEvent(self, event) -> None:
        if handle_canvas_pan_press(self, event):
            return
        if event.button() == Qt.MouseButton.LeftButton and self.mode in {"roi", "lane", "marker", "text_select"}:
            point = _event_viewport_point(event)
            if self.mode == "text_select" and not isinstance(self.itemAt(point), QGraphicsTextItem):
                self.scene().clearSelection()
            self._press_scene_pos = self.mapToScene(point)
            if self.mode in {"roi", "text_select"}:
                self._roi_rect_item = QGraphicsRectItem(QRectF(self._press_scene_pos, self._press_scene_pos))
                color = QColor("#4F8CFF") if self.mode == "text_select" else QColor("#E07A5F")
                self._roi_rect_item.setPen(QPen(color, 1.2, Qt.PenStyle.DashLine))
                self._roi_rect_item.setBrush(QColor(color.red(), color.green(), color.blue(), 36))
                self._roi_rect_item.setZValue(1000)
                self.scene().addItem(self._roi_rect_item)
                event.accept()
                return
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if handle_canvas_pan_move(self, event):
            return
        if self.mode in {"roi", "text_select"} and self._press_scene_pos is not None:
            current_pos = self.mapToScene(_event_viewport_point(event))
            if self._roi_rect_item is not None:
                self._roi_rect_item.setRect(QRectF(self._press_scene_pos, current_pos).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if handle_canvas_pan_release(self, event):
            return
        if event.button() == Qt.MouseButton.LeftButton and self._press_scene_pos is not None:
            release_pos = self.mapToScene(_event_viewport_point(event))
            if self.mode == "lane":
                self.lane_anchor_selected.emit(release_pos.x())
                self._press_scene_pos = None
                event.accept()
                return
            if self.mode == "marker":
                self.marker_anchor_selected.emit(release_pos.y())
                self._press_scene_pos = None
                event.accept()
                return
            if self.mode == "roi":
                selected = QRectF(self._press_scene_pos, release_pos).normalized()
                self._press_scene_pos = None
                self._clear_live_roi()
                if selected.width() > 3 and selected.height() > 3:
                    self.roi_selected.emit(selected)
                    event.accept()
                    return
            if self.mode == "text_select":
                selected = QRectF(self._press_scene_pos, release_pos).normalized()
                self._press_scene_pos = None
                self._clear_live_roi()
                if selected.width() > 3 and selected.height() > 3:
                    self._select_text_items_in_rect(selected)
                    event.accept()
                    return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        undo_action = menu.addAction(tr('v9_undo'))
        redo_action = menu.addAction(tr('redo'))
        menu.addSeparator()
        align_row_action = menu.addAction(tr('align_row'))
        align_column_action = menu.addAction(tr('align_column'))
        sync_lines_action = menu.addAction(tr('sync_lines'))
        recenter_action = menu.addAction(tr('recenter'))
        action = menu.exec(event.globalPos())
        mapping = {
            undo_action: "undo",
            redo_action: "redo",
            align_row_action: "align_row",
            align_column_action: "align_column",
            sync_lines_action: "sync_lines",
            recenter_action: "recenter_labels",
        }
        if action in mapping:
            self.context_action_requested.emit(mapping[action])
            event.accept()
            return
        super().contextMenuEvent(event)

    def _select_text_items_in_rect(self, rect: QRectF) -> None:
        self.scene().clearSelection()
        for item in self.scene().items():
            if isinstance(item, QGraphicsTextItem) and rect.intersects(item.sceneBoundingRect()):
                item.setSelected(True)

    def keyPressEvent(self, event) -> None:
        if is_canvas_text_editing(self):
            super().keyPressEvent(event)
            return
        if handle_canvas_space_press(self, event):
            return
        if handle_canvas_zoom_shortcut(self, event):
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self.delete_selected_crop_requested.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_A and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            for item in self.scene().items():
                if isinstance(item, QGraphicsTextItem):
                    item.setSelected(True)
            event.accept()
            return
        if event.key() == Qt.Key.Key_BracketLeft and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.reorder_selected_crop_requested.emit(-1)
            event.accept()
            return
        if event.key() == Qt.Key.Key_BracketRight and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.reorder_selected_crop_requested.emit(1)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if handle_canvas_space_release(self, event):
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event) -> None:
        if handle_canvas_wheel(self, event):
            return
        super().wheelEvent(event)

    def _clear_live_roi(self) -> None:
        if self._roi_rect_item is not None:
            self.scene().removeItem(self._roi_rect_item)
            self._roi_rect_item = None


class WBItemGroup(QGraphicsItemGroup):
    def __init__(self, moved_callback, parent=None) -> None:
        super().__init__(parent)
        self._moved_callback = moved_callback
        self._updating = False
        self._svg_text_source = b""
        self._svg_text_item: QGraphicsSvgItem | None = None
        self._svg_text_changed_callback = None
        self.setFiltersChildEvents(False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self._updating:
            self._moved_callback(float(value.x()), float(value.y()))
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        if event.modifiers() == Qt.KeyboardModifier.NoModifier and self.scene() is not None:
            self.scene().clearSelection()
            self.setSelected(True)
        super().mousePressEvent(event)

    def set_position_without_callback(self, x: float, y: float) -> None:
        self._updating = True
        self.setPos(x, y)
        self._updating = False

    def set_svg_text_editor(self, svg_bytes: bytes, svg_item: QGraphicsSvgItem, changed_callback) -> None:
        self._svg_text_source = bytes(svg_bytes)
        self._svg_text_item = svg_item
        self._svg_text_changed_callback = changed_callback

    def apply_svg_text_override(self, index: int, text: str) -> bool:
        if not self._svg_text_source or self._svg_text_item is None:
            return False
        updated = apply_svg_text_overrides(self._svg_text_source, {str(index): str(text)})
        renderer = QSvgRenderer(QByteArray(updated))
        if not renderer.isValid():
            return False
        self._svg_text_source = updated
        self._svg_text_item.setSharedRenderer(renderer)
        self._svg_text_item._sciefig_renderer = renderer
        if callable(self._svg_text_changed_callback):
            self._svg_text_changed_callback(int(index), str(text))
        self.update()
        return True

    def contextMenuEvent(self, event) -> None:
        if not self._svg_text_source:
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        edit_action = menu.addAction(tr('wb_edit_svg_text'))
        if menu.exec(event.screenPos()) != edit_action:
            return
        edit = prompt_svg_text_edit(None, self._svg_text_source, tr('wb_edit_plot_text'))
        if edit is not None:
            entry, replacement = edit
            self.apply_svg_text_override(entry.index, replacement)
        event.accept()

    def release_scene_resources(self) -> None:
        if self._svg_text_item is not None and graphics_item_is_alive(self._svg_text_item):
            self._svg_text_item.renderer().load(QByteArray(b'<svg xmlns="http://www.w3.org/2000/svg"/>'))
            self._svg_text_item._sciefig_renderer = self._svg_text_item.renderer()
        self._svg_text_item = None
        self._svg_text_changed_callback = None


class WBMovableLineItem(QGraphicsLineItem):
    def __init__(self, x1: float, y1: float, x2: float, y2: float, moved_callback=None, payload: tuple | None = None, parent=None) -> None:
        super().__init__(x1, y1, x2, y2, parent)
        self._moved_callback = moved_callback
        self._payload = payload
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def keyPressEvent(self, event) -> None:
        step = 5.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        delta = {
            Qt.Key.Key_Left: QPointF(-step, 0),
            Qt.Key.Key_Right: QPointF(step, 0),
            Qt.Key.Key_Up: QPointF(0, -step),
            Qt.Key.Key_Down: QPointF(0, step),
        }.get(event.key())
        if delta is not None:
            self.setPos(self.pos() + delta)
            event.accept()
            return
        super().keyPressEvent(event)

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self._moved_callback is not None
            and self._payload is not None
        ):
            self._moved_callback(*self._payload, float(value.x()), float(value.y()))
        return super().itemChange(change, value)


class WBMovablePathItem(QGraphicsPathItem):
    def __init__(self, path: QPainterPath, moved_callback=None, payload: tuple | None = None, parent=None) -> None:
        super().__init__(path, parent)
        self._moved_callback = moved_callback
        self._payload = payload
        self._suspend_callback = False
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setAcceptHoverEvents(True)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )

    def shape(self) -> QPainterPath:
        stroker = QPainterPathStroker()
        stroker.setWidth(max(16.0, self.pen().widthF() + 12.0))
        return stroker.createStroke(self.path())

    def hoverEnterEvent(self, event) -> None:
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverEnterEvent(event)

    def keyPressEvent(self, event) -> None:
        step = 5.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        delta = {
            Qt.Key.Key_Left: QPointF(-step, 0),
            Qt.Key.Key_Right: QPointF(step, 0),
            Qt.Key.Key_Up: QPointF(0, -step),
            Qt.Key.Key_Down: QPointF(0, step),
        }.get(event.key())
        if delta is not None:
            self.setPos(self.pos() + delta)
            event.accept()
            return
        super().keyPressEvent(event)

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged
            and self._moved_callback is not None
            and self._payload is not None
            and not self._suspend_callback
        ):
            self._moved_callback(self, *self._payload, float(value.x()), float(value.y()))
        return super().itemChange(change, value)

    def set_position_without_callback(self, x: float, y: float) -> None:
        self._suspend_callback = True
        self.setPos(x, y)
        self._suspend_callback = False


class BandCropRectItem(QGraphicsRectItem):
    HANDLE_SIZE = 7.0

    def __init__(self, index: int, rect: QRectF, changed_callback, delete_callback, reorder_callback, parent=None) -> None:
        super().__init__(0, 0, rect.width(), rect.height(), parent)
        self.index = index
        self._changed_callback = changed_callback
        self._delete_callback = delete_callback
        self._reorder_callback = reorder_callback
        self._resize_handle = ""
        self._press_pos = QPointF()
        self._press_rect = QRectF()
        self._press_item_pos = QPointF()
        self.setPos(rect.x(), rect.y())
        self.setZValue(200)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
        )
        self.setAcceptHoverEvents(True)

    def source_rect(self) -> QRectF:
        return QRectF(self.pos().x(), self.pos().y(), self.rect().width(), self.rect().height()).normalized()

    def paint(self, painter: QPainter, option, widget=None) -> None:
        del option, widget
        selected = self.isSelected()
        painter.setPen(QPen(QColor("#E07A5F" if selected else "#8795A1"), 1.4, Qt.PenStyle.SolidLine))
        painter.setBrush(QColor(224, 122, 95, 34))
        painter.drawRect(self.rect())
        painter.setBrush(QColor("#E07A5F" if selected else "#8795A1"))
        for handle in self._handle_rects().values():
            painter.drawRect(handle)
        painter.setPen(QPen(QColor("#111111"), 1.0))
        painter.setFont(QFont("Arial", 7))
        painter.drawText(QPointF(2, -4), f"{tr('band_row')} {self.index + 1}")

    def hoverMoveEvent(self, event) -> None:
        handle = self._handle_at(event.pos())
        if handle in {"tl", "br"}:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in {"tr", "bl"}:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in {"l", "r"}:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle in {"t", "b"}:
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(True)
            self.setFocus()
            self._resize_handle = self._handle_at(event.pos())
            self._press_pos = event.scenePos()
            self._press_rect = QRectF(self.rect())
            self._press_item_pos = QPointF(self.pos())
            if self._resize_handle:
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_handle:
            delta = event.scenePos() - self._press_pos
            rect = QRectF(self._press_rect)
            pos = QPointF(self._press_item_pos)
            if "l" in self._resize_handle:
                pos.setX(self._press_item_pos.x() + delta.x())
                rect.setWidth(max(4.0, self._press_rect.width() - delta.x()))
            if "r" in self._resize_handle:
                rect.setWidth(max(4.0, self._press_rect.width() + delta.x()))
            if "t" in self._resize_handle:
                pos.setY(self._press_item_pos.y() + delta.y())
                rect.setHeight(max(4.0, self._press_rect.height() - delta.y()))
            if "b" in self._resize_handle:
                rect.setHeight(max(4.0, self._press_rect.height() + delta.y()))
            self.prepareGeometryChange()
            self.setPos(pos)
            self.setRect(0, 0, rect.width(), rect.height())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._resize_handle = ""
        self._changed_callback(self.index, self.source_rect())
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event) -> None:
        step = 5.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        delta = {
            Qt.Key.Key_Left: QPointF(-step, 0),
            Qt.Key.Key_Right: QPointF(step, 0),
            Qt.Key.Key_Up: QPointF(0, -step),
            Qt.Key.Key_Down: QPointF(0, step),
        }.get(event.key())
        if delta is not None:
            self.setPos(self.pos() + delta)
            self._changed_callback(self.index, self.source_rect())
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Delete, Qt.Key.Key_Backspace}:
            self._delete_callback(self.index)
            event.accept()
            return
        super().keyPressEvent(event)

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        delete_action = menu.addAction(tr('delete'))
        up_action = menu.addAction(tr('up'))
        down_action = menu.addAction(tr('down'))
        action = menu.exec(event.screenPos())
        if action == delete_action:
            self._delete_callback(self.index)
        elif action == up_action:
            self._reorder_callback(self.index, -1)
        elif action == down_action:
            self._reorder_callback(self.index, 1)

    def _handle_rects(self) -> dict[str, QRectF]:
        rect = self.rect()
        s = self.HANDLE_SIZE
        h = s / 2
        cx = rect.center().x()
        cy = rect.center().y()
        return {
            "tl": QRectF(rect.left() - h, rect.top() - h, s, s),
            "t": QRectF(cx - h, rect.top() - h, s, s),
            "tr": QRectF(rect.right() - h, rect.top() - h, s, s),
            "r": QRectF(rect.right() - h, cy - h, s, s),
            "br": QRectF(rect.right() - h, rect.bottom() - h, s, s),
            "b": QRectF(cx - h, rect.bottom() - h, s, s),
            "bl": QRectF(rect.left() - h, rect.bottom() - h, s, s),
            "l": QRectF(rect.left() - h, cy - h, s, s),
        }

    def _handle_at(self, pos: QPointF) -> str:
        for key, rect in self._handle_rects().items():
            if rect.contains(pos):
                return key
        return ""


class WBCanvas(SceneCanvasOwnerMixin, QWidget):
    files_dropped = pyqtSignal(list)
    roi_selected = pyqtSignal(QRectF)
    lane_anchor_selected = pyqtSignal(float)
    marker_anchor_selected = pyqtSignal(float)
    crop_rect_changed = pyqtSignal(int, QRectF)
    crop_deleted = pyqtSignal(int)
    crop_reordered = pyqtSignal(int, int)
    strip_dropped = pyqtSignal(int)
    layout_text_changed = pyqtSignal(str, int, str)
    layout_item_moved = pyqtSignal(str, int, float, float)
    layout_style_changed = pyqtSignal(str, int, dict, bool)
    layout_line_moved = pyqtSignal(str, float, float)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()
    align_row_requested = pyqtSignal()
    align_column_requested = pyqtSignal()
    sync_lines_requested = pyqtSignal()
    recenter_labels_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasArea")
        self.scene = QGraphicsScene(self)
        register_graphics_scene_for_cleanup(self.scene)
        self.scene.setBackgroundBrush(QColor("#ffffff"))
        self.view = WBGraphicsView(self.scene, self)
        self.view.setObjectName("WBCanvasView")
        self.view.files_dropped.connect(self.files_dropped)
        self.view.roi_selected.connect(self.roi_selected)
        self.view.lane_anchor_selected.connect(self.lane_anchor_selected)
        self.view.marker_anchor_selected.connect(self.marker_anchor_selected)
        self.view.strip_dropped.connect(self.strip_dropped)
        self.view.delete_selected_crop_requested.connect(self.delete_selected_crop)
        self.view.reorder_selected_crop_requested.connect(self.reorder_selected_crop)
        self.view.context_action_requested.connect(self._handle_context_action)
        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        self.zoom_controls = CanvasZoomControls()
        self.toolbar_widget = QWidget()
        toolbar = QVBoxLayout(self.toolbar_widget)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        layout.addWidget(self.zoom_controls)
        self.undo_button = self._canvas_tool_button("Undo", "WBCanvasUndoButton", self.undo_requested)
        self.redo_button = self._canvas_tool_button("Redo", "WBCanvasRedoButton", self.redo_requested)
        self.align_row_button = self._canvas_tool_button("Align Row", "WBCanvasAlignRowButton", self.align_row_requested)
        self.align_column_button = self._canvas_tool_button("Align Column", "WBCanvasAlignColumnButton", self.align_column_requested)
        self.sync_lines_button = self._canvas_tool_button("Sync Lines", "WBCanvasSyncLinesButton", self.sync_lines_requested)
        self.recenter_labels_button = self._canvas_tool_button("Recenter", "WBCanvasRecenterLabelsButton", self.recenter_labels_requested)
        for button in [
            self.undo_button,
            self.redo_button,
            self.align_row_button,
            self.align_column_button,
            self.sync_lines_button,
            self.recenter_labels_button,
        ]:
            toolbar.addWidget(button)
        layout.addWidget(self.toolbar_widget, 0)
        layout.addWidget(self.view)
        self.panel: WesternBlotPanel | None = None
        self._group: WBItemGroup | None = None
        self._stats_group: WBItemGroup | None = None
        self._strip_display_rects: list[dict] = []
        self._density_overlay_result: dict | None = None
        self._density_overlay_items: list[QGraphicsItem] = []
        self._group_attached_items: dict[WBItemGroup, list[QGraphicsItem]] = {}
        self._attached_item_groups: dict[QGraphicsItem, WBItemGroup] = {}
        self._group_offsets: dict[WBItemGroup, QPointF] = {}
        attach_scene_canvas_controller(
            self,
            self.view,
            editable=lambda: self.panel is not None and self.panel.data.get('wb_layout',{}).get('enabled') and self.view.mode == 'move'
                and int(self.panel.data.get('wb_active_step',0)) != WB_STEP_DENSITY,
            content_rect_provider=lambda: QRectF(self.scene.sceneRect()),
            content_frame=True,
            zoom_controls=self.zoom_controls,
        )
        self.retranslate()

    def closeEvent(self, event) -> None:  # noqa: N802
        clear_graphics_scene_for_cleanup(self.scene)
        super().closeEvent(event)

    def _canvas_tool_button(self, text: str, object_name: str, signal) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.setToolTip(text)
        button.clicked.connect(signal.emit)
        return button

    def take_toolbar_widget(self) -> QWidget:
        layout = self.layout()
        if layout is not None:
            layout.removeWidget(self.toolbar_widget)
        self.toolbar_widget.setParent(None)
        return self.toolbar_widget

    def retranslate(self) -> None:
        self.zoom_controls.retranslate()
        labels = [
            (self.undo_button, "v9_undo"),
            (self.redo_button, "redo"),
            (self.align_row_button, "align_row"),
            (self.align_column_button, "align_column"),
            (self.sync_lines_button, "sync_lines"),
            (self.recenter_labels_button, "recenter"),
        ]
        for button, key in labels:
            text = tr(key)
            button.setText(text)
            button.setToolTip(text)
        for item in self.scene.items():
            key=getattr(item,'_wb_ui_i18n_key',None)
            if key:item.setPlainText(tr(key))
            title_key=getattr(item,'_wb_ui_title_key',None)
            if title_key:item.setTitle(tr(title_key))

    def _add_ui_text(self,key,*,canvas_hint=False):
        item=add_canvas_hint(self.scene,tr(key)) if canvas_hint else self.scene.addText(tr(key))
        item._wb_ui_i18n_key=key
        if canvas_hint:
            item._wb_ui_title_key='module_wb_canvas'
            item.setTitle(tr(item._wb_ui_title_key))
        return item

    def _handle_context_action(self, action: str) -> None:
        signals = {
            "undo": self.undo_requested,
            "redo": self.redo_requested,
            "align_row": self.align_row_requested,
            "align_column": self.align_column_requested,
            "sync_lines": self.sync_lines_requested,
            "recenter_labels": self.recenter_labels_requested,
        }
        signal = signals.get(action)
        if signal is not None:
            signal.emit()

    def set_mode(self, mode: str) -> None:
        self.view.set_mode(mode)
        self._set_group_move_enabled(mode == "move", selectable=False)

    def set_panel(self, panel: WesternBlotPanel) -> None:
        self.panel = panel
        self.render()

    def layout_objects(self) -> list:
        if self.panel is None:
            return []
        from ui.layout_adapters.wb_layout_adapter import wb_layout_objects

        return wb_layout_objects(self.panel)

    def apply_layout_objects(self, layout_objects: list) -> None:
        if self.panel is None:
            return
        from ui.layout_adapters.wb_layout_adapter import apply_wb_layout_objects

        apply_wb_layout_objects(self.panel, layout_objects)
        self.render()

    def selected_crop_index(self) -> int | None:
        for item in self.scene.selectedItems():
            if isinstance(item, BandCropRectItem):
                return item.index
        return None

    def selected_layout_items(self) -> list[QGraphicsItem]:
        return [item for item in self.scene.selectedItems() if _is_layout_annotation_item(item)]

    def move_selected_layout_items_by(self, delta: QPointF) -> None:
        for item in self.selected_layout_items():
            item.setPos(item.pos() + delta)

    def delete_selected_crop(self) -> None:
        index = self.selected_crop_index()
        if index is not None:
            self.crop_deleted.emit(index)

    def reorder_selected_crop(self, direction: int) -> None:
        index = self.selected_crop_index()
        if index is not None:
            self.crop_reordered.emit(index, direction)

    def render(self) -> None:
        data = self.panel.data if self.panel is not None else {}
        layout = data.get('wb_layout',{})
        step = int(data.get('wb_active_step',0))
        self._view_controller.set_content_identity(
            ("wb",self.panel.panel_uuid if self.panel else None,
             str(data.get("image_path") or ""),bool(data.get("image_data")),
             bool(layout.get('enabled')),step,
             bool(layout.get('stats_plot',{}).get('enabled') and data.get('wb_statistics',{}).get('plot_data')),
             tuple(data.get('placed_strip_ids',[])),int(data.get('selected_strip_index',0)) if step < 3 else None))
        self.scene.clear()
        self._group = None
        self._stats_group = None
        self._strip_display_rects = []
        self._density_overlay_items = []
        self._group_attached_items = {}
        self._attached_item_groups = {}
        self._group_offsets = {}
        if self.panel is not None and self.panel.data.get("wb_layout", {}).get("stats_plot", {}).get("enabled") and self.panel.data.get("wb_statistics") and not self.panel.data.get("image_data"):
            self._render_statistics_only_layout()
        elif self.panel is None or not self.panel.data.get("image_data"):
            self._add_ui_text('wb_canvas_import',canvas_hint=True)
        elif self.panel.data.get("wb_layout", {}).get("enabled") and step != WB_STEP_DENSITY:
            self._render_nature_layout()
        else:
            self._render_editor()
        self._draw_density_overlay()
        self._render_free_annotations()
        self._fit_to_content()

    def _render_free_annotations(self):
        if self.panel is None:return
        for data in self.panel.data.get('wb_layout',{}).get('free_annotations',[]):
            self._free_annotation_item(LayoutObject.from_dict(data))

    def _free_annotation_item(self,obj):
        item=SharedTextItem(obj) if obj.object_type=='text' else SharedShapeItem(obj)
        item.setData(0,'wb_free_annotation');item.setData(1,obj.object_id)
        self.scene.addItem(item)
        if isinstance(item,SharedTextItem):item._text_changed_callback=self._persist_free_annotation
        item._geometry_changed_callback=self._persist_free_annotation
        item._style_changed_callback=self._persist_free_annotation
        return item

    def _persist_free_annotation(self,item,*_args):
        obj=item.to_layout_object() if isinstance(item,SharedTextItem) else item.layout_object
        payload=obj.to_dict()
        payload.update(x=item.pos().x(),y=item.pos().y(),rotation_deg=item.rotation(),z_order=int(item.zValue()))
        records=self.panel.data.setdefault('wb_layout',{}).setdefault('free_annotations',[])
        for index,record in enumerate(records):
            if record['object_id']==obj.object_id:
                records[index]=payload
                return
        records.append(payload)

    def add_free_annotation(self,kind='text'):
        uid=f'{self.panel.panel_uuid}:wb_annotation:{uuid4().hex}'
        obj=LayoutObject(uid,'text' if kind=='text' else 'shape',24,24,140,45,
            z_order=400,text='Text' if kind=='text' else '',
            source=LayoutSourceIdentity(self.panel.source_module,self.panel.panel_uuid,uid,'wb_layout_annotation'),
            style=LayoutObjectStyle(font_size_pt=10,fill_color='transparent',stroke_color='#111111'),
            metadata={'wb_kind':'free_annotation','shape_kind':kind,'fill_alpha':0})
        item=self._free_annotation_item(obj)
        self._persist_free_annotation(item)
        self.scene.clearSelection();item.setSelected(True)
        return item

    def set_density_overlay(self, densitometry_result: dict | None) -> None:
        self._density_overlay_result = densitometry_result
        self._clear_density_overlay()
        self._draw_density_overlay()

    def clear_text_selection_on_blank_click(self) -> None:
        self.scene.clearSelection()

    def _clear_density_overlay(self) -> None:
        for item in self._density_overlay_items:
            if item.scene() is self.scene:
                self.scene.removeItem(item)
        self._density_overlay_items = []

    def _draw_density_overlay(self) -> None:
        if self._density_overlay_result is None or self._group is None:
            return
        if self._density_overlay_items:
            return
        by_strip_id = {str(item.get("strip_id", "")): item for item in self._density_overlay_result.get("measurements", [])}
        for entry in self._strip_display_rects:
            measurement = by_strip_id.get(str(entry.get("strip_id", "")))
            if measurement is None:
                continue
            display = QRectF(entry["display_rect"])
            for lane in measurement.get("lanes", []):
                roi = dict(lane.get("roi", {}))
                rect = QRectF(
                    display.x() + float(roi.get("x", 0)),
                    display.y() + float(roi.get("y", 0)),
                    float(roi.get("w", 1)),
                    float(roi.get("h", display.height())),
                )
                rect_item = QGraphicsRectItem(rect, self._group)
                rect_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                rect_item.setPen(QPen(QColor("#6496FF"), 1.0, Qt.PenStyle.SolidLine))
                rect_item.setBrush(QColor(100, 150, 255, 40))
                rect_item.setZValue(120)
                self._group.addToGroup(rect_item)
                self._density_overlay_items.append(rect_item)

                fold = lane.get("fold")
                text_value = "N/A"
                if isinstance(fold, (int, float)) and fold == fold:
                    text_value = f"{float(fold):.2f}"
                flags = list(lane.get("qc_flags", []))
                text = QGraphicsTextItem(text_value, self._group)
                text.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                text.setToolTip(", ".join(str(flag) for flag in flags))
                text.setDefaultTextColor(QColor("#E8A040" if flags else "#FFFFFF"))
                text.setFont(QFont("Arial", 7))
                text.setZValue(121)
                text.setPos(rect.center().x() - text.boundingRect().width() / 2, rect.top() - text.boundingRect().height() - 2)
                self._group.addToGroup(text)
                self._density_overlay_items.append(text)

    def scene_roi_to_source_roi(self, rect: QRectF) -> tuple[int, int, int, int]:
        if self._group is None:
            return (0, 0, 1, 1)
        local = self._group.mapFromScene(QRectF(rect)).boundingRect()
        return self._local_rect_to_source_roi(local)

    def scene_roi_to_strip_roi(self, rect: QRectF, strip_index: int) -> tuple[int, int, int, int]:
        if self._group is None:
            return (0, 0, 1, 1)
        local = self._group.mapFromScene(QRectF(rect)).boundingRect()
        for entry in self._strip_display_rects:
            if int(entry.get("index", -1)) != strip_index:
                continue
            display = entry["display_rect"]
            clipped = local.intersected(display)
            if clipped.isEmpty():
                clipped = local
            source_roi = entry["source_roi"]
            x = int(source_roi.get("x", 0) + clipped.x() - display.x())
            y = int(source_roi.get("y", 0) + clipped.y() - display.y())
            return (x, y, max(int(clipped.width()), 1), max(int(clipped.height()), 1))
        return self.scene_roi_to_source_roi(rect)

    def scene_lane_x_to_source_x(self, scene_x: float) -> float:
        if self._group is None:
            return scene_x
        local = self._group.mapFromScene(QPointF(scene_x, 0))
        return max(0.0, local.x())

    def scene_marker_y_to_source_y(self, scene_y: float) -> float:
        if self._group is None:
            return scene_y
        local = self._group.mapFromScene(QPointF(0, scene_y))
        return max(0.0, local.y())

    def detect_lane_centers(self, lane_count: int) -> list[float]:
        if self.panel is None or lane_count <= 0:
            return []
        image, offset_x = self._analysis_image_with_offset()
        if image.isNull() or image.width() < lane_count:
            return []
        width = image.width()
        height = image.height()
        projection: list[float] = []
        for x in range(width):
            score = 0.0
            for y in range(height):
                color = image.pixelColor(x, y)
                luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
                score += 255.0 - luminance
            projection.append(score / max(height, 1))

        smoothed = self._smooth_projection(projection, max(3, width // 120))
        candidates = self._projection_peak_candidates(smoothed)
        if not candidates:
            return []

        min_distance = max(4, width // max(lane_count * 3, 1))
        selected: list[int] = []
        for index in sorted(candidates, key=lambda item: smoothed[item], reverse=True):
            if all(abs(index - existing) >= min_distance for existing in selected):
                selected.append(index)
            if len(selected) == lane_count:
                break
        if len(selected) != lane_count:
            return []
        return [float(index + offset_x) for index in sorted(selected)]

    def _render_editor(self) -> None:
        if self.panel is not None and self.panel.data.get("band_strips") and not self.panel.data.get("crops"):
            if int(self.panel.data.get("wb_active_step", 0)) >= 3:
                self._render_strip_stack_editor()
            else:
                self._render_selected_strip_editor()
            return
        image = self._source_display_image(self.panel)
        if image.isNull():
            self._add_ui_text('wb_render_image_error')
            return
        pixmap = QPixmap.fromImage(image)
        self._group = WBItemGroup(self._record_group_position)
        self.scene.addItem(self._group)
        pixmap_item = QGraphicsPixmapItem(pixmap)
        pixmap_item.setZValue(0)
        self._group.addToGroup(pixmap_item)
        self._draw_editor_annotations(self._group, image.width(), image.height())
        self._draw_crop_rois(self._group)
        self._apply_group_transform(image.width(), image.height())

    def _render_nature_layout(self) -> None:
        source = self._source_display_image(self.panel)
        if source.isNull():
            self._add_ui_text('wb_render_layout_error')
            return
        layout = self.panel.data.get("wb_layout", {})
        crops = self._layout_band_sources()
        bands = layout.get("bands", [])
        if not crops:
            self._render_editor()
            return
        self._group = WBItemGroup(self._record_group_position)
        self.scene.addItem(self._group)
        font = QFont(str(layout.get("style", {}).get("font_family", "Arial")), int(layout.get("style", {}).get("tick_font_pt", 6)))
        protein_font = QFont(font)
        protein_font.setItalic(True)
        gap = float(layout.get("style", {}).get("strip_gap_px", layout.get("style", {}).get("band_gap_pt", 8)))
        padding_left = 64.0
        label_offset = float(layout.get("style", {}).get("label_offset_px", 10))
        band_width = max(int(crop["roi"]["w"]) for crop in crops)
        lane_labels = layout.get("lane_labels") or self.panel.data.get("annotations", {}).get("lane_labels", [])
        hierarchy = layout.get("lane_hierarchy", {})
        lane_count = int(hierarchy.get("total_lanes") or len(lane_labels) or 0)
        if lane_count and len(lane_labels) != lane_count:
            lane_labels = lane_labels[:lane_count] + [str(index + 1) for index in range(len(lane_labels), lane_count)]
        lane_positions = self._layout_lane_positions(band_width, lane_count, layout.get("group_gaps", []))
        content_width = self._layout_content_width(band_width, layout.get("group_gaps", []))
        hierarchy_height = self._lane_hierarchy_height(hierarchy, bool(lane_labels and layout.get("lane_label_position", "top") == "top"))
        padding_top = 28.0 + hierarchy_height
        y = padding_top
        if hierarchy.get("levels"):
            self._render_lane_hierarchy(self._group, hierarchy, padding_left, y, lane_positions, font)
        elif lane_labels and layout.get("lane_label_position", "top") == "top":
            for index, (label, x) in enumerate(zip(lane_labels, lane_positions, strict=False)):
                text = self._editable_text(str(label), ("lane", index))
                self._apply_text_style(text, "lane", index, font)
                self._position_text(text, "lane", index, padding_left + x - text.boundingRect().width() / 2, y - text.boundingRect().height() - label_offset)
                self._add_layout_text(self._group, text)
        for index, crop in enumerate(crops):
            roi = crop["roi"]
            band_source = self._source_display_image_for_path(crop.get("image_path"), adjustments=crop.get("adjustments")) if crop.get("image_path") else source
            band_image = band_source.copy(int(roi["x"]), int(roi["y"]), int(roi["w"]), int(roi["h"]))
            band_image = self._transform_cropped_strip(band_image, crop.get("adjustments", {}))
            display_scale = 1.0
            if band_image.width() > 0 and band_image.width() != band_width:
                # Keep the cropped source at native resolution.  The layout
                # uses an item transform to normalize strip widths instead of
                # replacing it with a downsampled pixmap.
                display_scale = band_width / band_image.width()
            display_height = max(1.0, band_image.height() * display_scale)
            pixmap_item = QGraphicsPixmapItem(QPixmap.fromImage(band_image))
            pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            pixmap_item.setScale(display_scale)
            pixmap_item.setPos(padding_left, y)
            pixmap_item.setZValue(0)
            self._group.addToGroup(pixmap_item)
            band = bands[index] if index < len(bands) else {}
            center_y = y + display_height / 2
            mw = str(band.get("mw_kda", "")).strip()
            if mw and layout.get("marker_label_side", "left") != "none":
                marker = self._editable_text(mw, ("marker", index))
                self._apply_text_style(marker, "marker", index, font)
                if layout.get("marker_label_side", "left") == "right":
                    self._position_text(marker, "marker", index, padding_left + content_width + 18, center_y - marker.boundingRect().height() / 2)
                    line = WBMovableLineItem(padding_left + content_width + 3, center_y, padding_left + content_width + 14, center_y)
                else:
                    self._position_text(marker, "marker", index, padding_left - marker.boundingRect().width() - 18, center_y - marker.boundingRect().height() / 2)
                    line = WBMovableLineItem(padding_left - 14, center_y, padding_left - 3, center_y)
                line.setPen(QPen(QColor("#111111"), 0.5))
                line.setZValue(50)
                self._add_layout_text(self._group, marker)
                self._group.addToGroup(line)
            protein = str(band.get("protein", crop.get("protein", ""))).strip()
            if protein and layout.get("protein_label_side", "right") != "none":
                label = self._editable_text(protein, ("protein", index))
                self._apply_text_style(label, "protein", index, protein_font)
                if layout.get("protein_label_side", "right") == "left":
                    self._position_text(label, "protein", index, padding_left - label.boundingRect().width() - 16, center_y - label.boundingRect().height() / 2)
                else:
                    self._position_text(label, "protein", index, padding_left + content_width + 16, center_y - label.boundingRect().height() / 2)
                self._add_layout_text(self._group, label)
            y += display_height + gap
        if lane_labels and not hierarchy.get("levels") and layout.get("lane_label_position", "top") == "bottom":
            label_y = y + 4
            for index, (label, x) in enumerate(zip(lane_labels, lane_positions, strict=False)):
                text = self._editable_text(str(label), ("lane", index))
                self._apply_text_style(text, "lane", index, font)
                self._position_text(text, "lane", index, padding_left + x - text.boundingRect().width() / 2, label_y)
                self._add_layout_text(self._group, text)
        stats_plot = dict(layout.get("stats_plot", {}))
        layout_pos = self.panel.data.get("layout", {})
        group_x = float(layout_pos.get("image_x", 0.0))
        group_y = float(layout_pos.get("image_y", 0.0))
        if stats_plot.get("enabled") and self.panel.data.get("wb_statistics"):
            self._render_statistics_plot_layout(group_x + padding_left + content_width + 120, group_y + padding_top)
        self._group.set_position_without_callback(group_x, group_y)
        self._apply_initial_group_offset(self._group, QPointF(group_x, group_y))
        self._set_group_move_enabled(self.view.mode == "move")

    def _render_statistics_only_layout(self) -> None:
        self._group = WBItemGroup(self._record_group_position)
        self.scene.addItem(self._group)
        if not self._render_statistics_plot_layout(24, 24):
            self._add_ui_text('wb_render_stats_error')
            return
        self._set_group_move_enabled(self.view.mode == "move")

    def _draw_crop_rois(self, parent: WBItemGroup) -> None:
        if self.panel is None:
            return
        for index, crop in enumerate(self.panel.data.get("crops", [])):
            roi = crop.get("roi", {})
            rect = QRectF(float(roi.get("x", 0)), float(roi.get("y", 0)), float(roi.get("w", 1)), float(roi.get("h", 1)))
            item = BandCropRectItem(index, rect, self.crop_rect_changed.emit, self.crop_deleted.emit, self.crop_reordered.emit, parent)
            item.setParentItem(parent)

    def _render_strip_stack_editor(self) -> None:
        if self.panel is None:
            return
        self._group = WBItemGroup(self._record_group_position)
        self.scene.addItem(self._group)
        y = 0.0
        max_width = 1
        strips = list(self.panel.data.get("band_strips", []))
        placed_ids = list(self.panel.data.get("placed_strip_ids", []))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in strips}
            strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        for index, strip in enumerate(strips):
            image = self._source_display_image_for_path(strip.get("image_path"), adjustments=strip.get("adjustments"))
            if image.isNull():
                continue
            roi = strip.get("crop_roi", {"x": 0, "y": 0, "w": image.width(), "h": image.height()})
            image = image.copy(int(roi.get("x", 0)), int(roi.get("y", 0)), int(roi.get("w", image.width())), int(roi.get("h", image.height())))
            image = self._transform_cropped_strip(image, strip.get("adjustments", {}))
            pixmap_item = QGraphicsPixmapItem(QPixmap.fromImage(image))
            pixmap_item.setPos(0, y)
            pixmap_item.setZValue(0)
            self._group.addToGroup(pixmap_item)
            self._strip_display_rects.append(
                {
                    "index": index,
                    "strip_id": str(strip.get("strip_id", f"strip_{index + 1}")),
                    "display_rect": QRectF(0, y, image.width(), image.height()),
                    "source_roi": dict(roi),
                }
            )
            label = QGraphicsTextItem(str(strip.get("protein_name", strip.get("original_source", ""))))
            label.setDefaultTextColor(QColor("#111111"))
            label.setPos(-label.boundingRect().width() - 12, y + image.height() / 2 - label.boundingRect().height() / 2)
            self._group.addToGroup(label)
            y += image.height() + 8
            max_width = max(max_width, image.width())
        drop_rect = QGraphicsRectItem(QRectF(0, y, max(max_width, 220), 52))
        drop_rect.setPen(QPen(QColor("#9AA3AF"), 1.0, Qt.PenStyle.DashLine))
        drop_rect.setZValue(10)
        self._group.addToGroup(drop_rect)
        drop_text = QGraphicsTextItem(tr('wb_drag_strip'))
        drop_text._wb_ui_i18n_key='wb_drag_strip'
        drop_text.setDefaultTextColor(QColor("#6B7280"))
        drop_text.setZValue(100)
        drop_text.setPos(12, y + 15)
        self._group.addToGroup(drop_text)
        position = self.panel.data.get('layout',{})
        offset = QPointF(float(position.get('image_x',0)),float(position.get('image_y',0)))
        self._group.set_position_without_callback(offset.x(),offset.y())
        self._apply_initial_group_offset(self._group,offset)
        self._set_group_move_enabled(self.view.mode == "move")

    def _render_selected_strip_editor(self) -> None:
        if self.panel is None:
            return
        strips = list(self.panel.data.get("band_strips", []))
        if not strips:
            self._add_ui_text('wb_strip_import')
            return
        index = max(0, min(int(self.panel.data.get("selected_strip_index", len(strips) - 1)), len(strips) - 1))
        strip = strips[index]
        image = self._source_display_image_for_path(strip.get("image_path"), adjustments=strip.get("adjustments"))
        if image.isNull():
            self._add_ui_text('wb_render_strip_error')
            return
        self._group = WBItemGroup(self._record_group_position)
        self.scene.addItem(self._group)
        pixmap_item = QGraphicsPixmapItem(QPixmap.fromImage(image))
        pixmap_item.setZValue(0)
        self._group.addToGroup(pixmap_item)
        self._strip_display_rects.append(
            {
                "index": index,
                "strip_id": str(strip.get("strip_id", f"strip_{index + 1}")),
                "display_rect": QRectF(0, 0, image.width(), image.height()),
                "source_roi": {"x": 0, "y": 0, "w": image.width(), "h": image.height()},
            }
        )
        roi = strip.get("crop_roi", {})
        if roi:
            rect = QGraphicsRectItem(QRectF(float(roi.get("x", 0)), float(roi.get("y", 0)), float(roi.get("w", 1)), float(roi.get("h", 1))))
            rect.setPen(QPen(QColor("#E07A5F"), 1.2, Qt.PenStyle.DashLine))
            rect.setBrush(QColor(224, 122, 95, 24))
            rect.setZValue(50)
            self._group.addToGroup(rect)
        self._set_group_move_enabled(self.view.mode == "move")

    def _apply_group_transform(self, width: int, height: int) -> None:
        if self.panel is None or self._group is None:
            return
        adjustments = self.panel.data.get("adjustments", {})
        transform = QTransform()
        if adjustments.get("flip_horizontal"):
            transform.scale(-1, 1)
            transform.translate(-width, 0)
        if adjustments.get("flip_vertical"):
            transform.scale(1, -1)
            transform.translate(0, -height)
        self._group.setTransform(transform)
        self._group.setRotation(float(adjustments.get("rotation", 0.0)))
        self._set_group_move_enabled(self.view.mode == "move")
        layout = self.panel.data.get("layout", {})
        group_x = float(layout.get("image_x", 0.0))
        group_y = float(layout.get("image_y", 0.0))
        self._group.set_position_without_callback(group_x, group_y)
        self._apply_initial_group_offset(self._group, QPointF(group_x, group_y))

    def _fit_to_content(self) -> None:
        # Export bounds stay independent; initialize screen paper only after all items exist.
        rect = self.scene.itemsBoundingRect().adjusted(-80, -80, 120, 120)
        self.scene.setSceneRect(rect)
        self._apply_canvas_zoom()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._view_controller.handle_resize()

    def _smooth_projection(self, projection: list[float], radius: int) -> list[float]:
        smoothed: list[float] = []
        for index in range(len(projection)):
            start = max(0, index - radius)
            end = min(len(projection), index + radius + 1)
            smoothed.append(sum(projection[start:end]) / max(end - start, 1))
        return smoothed

    def _projection_peak_candidates(self, projection: list[float]) -> list[int]:
        if len(projection) < 3:
            return []
        mean = sum(projection) / len(projection)
        variance = sum((value - mean) ** 2 for value in projection) / len(projection)
        threshold = mean + variance**0.5 * 0.35
        candidates = []
        for index in range(1, len(projection) - 1):
            if projection[index] >= threshold and projection[index] >= projection[index - 1] and projection[index] >= projection[index + 1]:
                candidates.append(index)
        return candidates

    def _local_rect_to_source_roi(self, rect: QRectF) -> tuple[int, int, int, int]:
        if self.panel is None:
            return (0, 0, 1, 1)
        image_data = self.panel.data.get("image_data", {})
        max_width = int(image_data.get("width_px", 1))
        max_height = int(image_data.get("height_px", 1))
        x = max(0, min(int(rect.x()), max_width - 1))
        y = max(0, min(int(rect.y()), max_height - 1))
        width = max(1, min(int(rect.width()), max_width - x))
        height = max(1, min(int(rect.height()), max_height - y))
        return x, y, width, height

    def _record_group_position(self, x: float, y: float) -> None:
        if self.panel is None or self._group is None:
            return
        old = self._group_offsets.get(self._group, QPointF())
        new = QPointF(float(x), float(y))
        delta = new - old
        if not delta.isNull():
            self._move_group_attachments(self._group, delta)
        self._group_offsets[self._group] = new
        self.panel.set_layout_position(x, y)

    def _record_stats_plot_position(self, x: float, y: float) -> None:
        if self.panel is None or self._stats_group is None:
            return
        old = self._group_offsets.get(self._stats_group, QPointF())
        new = QPointF(float(x), float(y))
        delta = new - old
        if not delta.isNull():
            self._move_group_attachments(self._stats_group, delta)
        self._group_offsets[self._stats_group] = new
        layout = dict(self.panel.data.get("wb_layout", {}))
        stats_plot = dict(layout.get("stats_plot", {}))
        stats_plot["x"] = float(x)
        stats_plot["y"] = float(y)
        layout["stats_plot"] = stats_plot
        self.panel.data["wb_layout"] = layout

    def _set_group_move_enabled(self, enabled: bool, selectable: bool | None = None) -> None:
        selectable = False if selectable is None else selectable
        if self.panel is not None and self.panel.data.get('wb_layout',{}).get('enabled') and int(self.panel.data.get('wb_active_step',0)) != WB_STEP_DENSITY:
            selectable = enabled
        for group in (self._group, self._stats_group):
            if group is None:
                continue
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, enabled)
            group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, enabled if group is self._stats_group else selectable)
            if group is self._group and selectable:
                group.setData(0,'wb_layout_image_group');group.setData(1,'wb-band-group')

    def _register_group_attachment(self, group: WBItemGroup, item: QGraphicsItem) -> None:
        if item.scene() is None:
            self.scene.addItem(item)
        self._group_attached_items.setdefault(group, []).append(item)
        self._attached_item_groups[item] = group

    def _emit_layout_item_moved(self, item: QGraphicsItem, kind: str, index: int, _x: float, _y: float) -> None:
        position = self.relative_position_for_item(item)
        self.layout_item_moved.emit(kind, index, float(position.x()), float(position.y()))

    def _emit_layout_line_moved(self, item: QGraphicsItem, uid: str, _x: float, _y: float) -> None:
        position = self.relative_position_for_item(item)
        self.layout_line_moved.emit(uid, float(position.x()), float(position.y()))

    def _move_group_attachments(self, group: WBItemGroup, delta: QPointF) -> None:
        for item in self._group_attached_items.get(group, []):
            new_pos = item.pos() + delta
            setter = getattr(item, "set_position_without_callback", None)
            if callable(setter):
                setter(float(new_pos.x()), float(new_pos.y()))
            else:
                item.setPos(new_pos)

    def _apply_initial_group_offset(self, group: WBItemGroup, offset: QPointF) -> None:
        current = self._group_offsets.get(group, QPointF())
        delta = offset - current
        if not delta.isNull():
            self._move_group_attachments(group, delta)
        self._group_offsets[group] = QPointF(offset)

    def relative_position_for_item(self, item: QGraphicsItem) -> QPointF:
        group = self._attached_item_groups.get(item)
        if group is None:
            return item.pos()
        return item.pos() - self._group_offsets.get(group, QPointF())

    def _stats_plot_geometry(self) -> dict | None:
        if self.panel is None:
            return None
        result = dict(self.panel.data.get("wb_statistics", {}))
        rows = list(result.get("plot_data", []))
        if not rows:
            return None
        groups = list(dict.fromkeys(str(row.get("group", "")) for row in rows if str(row.get("group", ""))))
        if not groups:
            return None
        stats_plot = dict(self.panel.data.get("wb_layout", {}).get("stats_plot", {}))
        width = float(stats_plot.get("width_px", 520))
        height = float(stats_plot.get("height_px", 360))
        left = 70.0
        top = 24.0
        right = 24.0
        bottom = 58.0
        plot_width = max(1.0, width - left - right)
        plot_height = max(1.0, height - top - bottom)
        return {
            "groups": groups,
            "result": result,
            "width": width,
            "height": height,
            "left": left,
            "top": top,
            "right": right,
            "bottom": bottom,
            "plot_width": plot_width,
            "plot_height": plot_height,
        }

    def stats_label_anchor(self, kind: str, index: int) -> QPointF | None:
        geometry = self._stats_plot_geometry()
        if geometry is None:
            return None
        groups = geometry["groups"]
        left = geometry["left"]
        top = geometry["top"]
        plot_width = geometry["plot_width"]
        plot_height = geometry["plot_height"]
        if kind == "stats_x_label":
            if not (0 <= index < len(groups)):
                return None
            center_x = left + (index + 0.5) * plot_width / len(groups)
            return QPointF(center_x, top + plot_height + 8)
        if kind == "stats_y_label":
            return QPointF(10, top + plot_height / 2)
        if kind == "stats_sig_label":
            comparisons = geometry["result"].get("comparisons", [])
            if not (0 <= index < len(comparisons)):
                return None
            comparison = comparisons[index]
            group1 = str(comparison.get("group1", ""))
            group2 = str(comparison.get("group2", ""))
            if group1 not in groups or group2 not in groups:
                return None
            x1 = left + (groups.index(group1) + 0.5) * plot_width / len(groups)
            x2 = left + (groups.index(group2) + 0.5) * plot_width / len(groups)
            bracket_y = max(top + 8.0, top + 22.0 + index * 20.0)
            return QPointF((x1 + x2) / 2, bracket_y - 18)
        return None

    def _stats_label_position(self, kind: str, index: int, default_x: float, default_y: float) -> tuple[float, float]:
        anchor = self.stats_label_anchor(kind, index)
        if anchor is None:
            return default_x, default_y
        stats_plot = self.panel.data.get("wb_layout", {}).get("stats_plot", {}) if self.panel is not None else {}
        offsets = dict(stats_plot.get("label_offsets", {}))
        kind_offsets = dict(offsets.get(kind, {}))
        saved = kind_offsets.get(str(index))
        if isinstance(saved, dict):
            dx = float(saved.get("dx", saved.get("x", 0.0)))
            dy = float(saved.get("dy", saved.get("y", 0.0)))
            return float(anchor.x() + dx), float(anchor.y() + dy)
        return default_x, default_y

    def _stats_rotation_saved(self, kind: str, index: int) -> bool:
        if self.panel is None:
            return False
        styles = self.panel.data.get("wb_layout", {}).get("text_styles", {})
        kind_styles = styles.get(kind, {})
        if "rotation" in kind_styles.get("default", {}):
            return True
        return "rotation" in kind_styles.get("items", {}).get(str(index), {})

    def _source_display_image(self, panel: WesternBlotPanel | None) -> QImage:
        if panel is None:
            return QImage()
        source_path = getattr(panel, "raw_source_path", None) or panel.data.get("image_path")
        return self._source_display_image_for_path(source_path, panel)

    def _source_display_image_for_path(self, source_path, panel: WesternBlotPanel | None = None, adjustments: dict | None = None) -> QImage:
        panel = panel or self.panel
        if not source_path or not Path(str(source_path)).exists():
            return QImage()
        image = QImage(str(source_path))
        if image.isNull():
            return image
        adjustments = adjustments or (panel.data.get("adjustments", {}) if panel is not None else {})
        brightness = int(adjustments.get("brightness", 0))
        contrast = int(adjustments.get("contrast", 0))
        if brightness or contrast:
            image = self._adjust_qimage_for_display(image, brightness, contrast)
        return image

    def _transform_cropped_strip(self, image: QImage, adjustments: dict) -> QImage:
        transform = QTransform()
        if adjustments.get("flip_horizontal"):
            transform.scale(-1, 1)
        if adjustments.get("flip_vertical"):
            transform.scale(1, -1)
        rotation = float(adjustments.get("rotation", 0.0))
        if rotation:
            transform.rotate(rotation)
        if transform.isIdentity():
            return image
        return image.transformed(transform, Qt.TransformationMode.SmoothTransformation)

    def _layout_band_sources(self) -> list[dict]:
        if self.panel is None:
            return []
        crops = list(self.panel.data.get("crops", []))
        if crops:
            return crops
        sources = []
        strips = list(self.panel.data.get("band_strips", []))
        placed_ids = list(self.panel.data.get("placed_strip_ids", []))
        if placed_ids:
            by_id = {str(strip.get("strip_id")): strip for strip in strips}
            strips = [by_id[strip_id] for strip_id in placed_ids if strip_id in by_id]
        for index, strip in enumerate(strips):
            image = self._source_display_image_for_path(strip.get("image_path"))
            roi = dict(strip.get("crop_roi", {}))
            if not roi and not image.isNull():
                roi = {"x": 0, "y": 0, "w": image.width(), "h": image.height()}
            sources.append(
                {
                    "crop_id": strip.get("strip_id", f"strip_{index + 1}"),
                    "strip_id": strip.get("strip_id", f"strip_{index + 1}"),
                    "protein": strip.get("protein_name", ""),
                    "roi": roi,
                    "image_path": strip.get("image_path", ""),
                    "adjustments": strip.get("adjustments", {}),
                }
            )
        return sources

    def _analysis_image_with_offset(self) -> tuple[QImage, int]:
        if self.panel is None:
            return QImage(), 0
        source = self._source_display_image(self.panel)
        crops = self.panel.data.get("crops", [])
        if not crops:
            return source, 0
        roi = crops[-1].get("roi", {})
        x = int(roi.get("x", 0))
        y = int(roi.get("y", 0))
        return source.copy(x, y, int(roi.get("w", source.width())), int(roi.get("h", source.height()))), x

    def _draw_editor_annotations(self, group: WBItemGroup, width: int, height: int) -> None:
        if self.panel is None:
            return
        annotations = self.panel.data.get("annotations", {})
        lane_labels = annotations.get("lane_labels", [])
        lane_anchors = annotations.get("lane_anchors", [])
        for index, label in enumerate(lane_labels):
            if index >= len(lane_anchors):
                continue
            x = float(lane_anchors[index])
            line = WBMovableLineItem(x, 0, x, height)
            line.setPen(QPen(QColor(224, 122, 95, 150), 0.8, Qt.PenStyle.DashLine))
            text = QGraphicsTextItem(str(label))
            text.setDefaultTextColor(QColor("#111111"))
            text.setPos(x - text.boundingRect().width() / 2, -26)
            group.addToGroup(line)
            group.addToGroup(text)

        markers = annotations.get("markers", [])
        marker_anchors = annotations.get("marker_anchors", [])
        for index, marker in enumerate(markers):
            if index >= len(marker_anchors):
                continue
            y = float(marker_anchors[index])
            line = WBMovableLineItem(-10, y, -3, y)
            line.setPen(QPen(QColor("#111111"), 0.5))
            text = QGraphicsTextItem(str(marker))
            text.setDefaultTextColor(QColor("#111111"))
            text.setPos(-text.boundingRect().width() - 14, y - text.boundingRect().height() / 2)
            group.addToGroup(line)
            group.addToGroup(text)

        proteins = annotations.get("protein_labels", [])
        if proteins:
            text = QGraphicsTextItem(str(proteins[-1]))
            font = QFont("Arial", 6)
            font.setItalic(True)
            text.setFont(font)
            text.setDefaultTextColor(QColor("#111111"))
            text.setPos(width + 10, height / 2 - text.boundingRect().height() / 2)
            group.addToGroup(text)

    def _lane_hierarchy_height(self, hierarchy: dict, has_simple_lane_labels: bool) -> float:
        if not hierarchy.get("levels"):
            return 18.0 if has_simple_lane_labels else 0.0
        group_levels = [level for level in hierarchy.get("levels", []) if level.get("type") != "condition_row"]
        condition_rows = [level for level in hierarchy.get("levels", []) if level.get("type") == "condition_row"]
        return len(group_levels) * 22.0 + len(condition_rows) * 15.0 + 8.0

    def _layout_content_width(self, band_width: int, group_gaps: list[dict]) -> float:
        return float(band_width) + sum(float(gap.get("gap_width_pt", 0.0)) for gap in group_gaps)

    def _layout_lane_positions(self, band_width: int, lane_count: int, group_gaps: list[dict] | None = None) -> list[float]:
        if lane_count <= 0:
            return []
        gaps = sorted(group_gaps or [], key=lambda gap: int(gap.get("after_lane", 0)))
        lane_spacing = float(band_width) / lane_count
        positions = []
        extra_gap = 0.0
        gap_index = 0
        for index in range(lane_count):
            lane_number = index + 1
            while gap_index < len(gaps) and int(gaps[gap_index].get("after_lane", 0)) < lane_number:
                extra_gap += float(gaps[gap_index].get("gap_width_pt", 0.0))
                gap_index += 1
            positions.append(lane_spacing * index + lane_spacing / 2 + extra_gap)
        return positions

    def _render_lane_hierarchy(
        self,
        group: WBItemGroup,
        hierarchy: dict,
        band_left: float,
        band_top: float,
        lane_positions: list[float],
        font: QFont,
    ) -> None:
        levels = hierarchy.get("levels", [])
        group_levels = sorted([level for level in levels if level.get("type") != "condition_row"], key=lambda level: int(level.get("level", 0)))
        condition_rows = sorted([level for level in levels if level.get("type") == "condition_row"], key=lambda level: int(level.get("level", 0)))
        current_y = band_top - 12.0
        for row_index, row in enumerate(reversed(condition_rows)):
            current_y -= 15.0
            row_label = self._editable_text(str(row.get("row_label", "")), ("condition_label", row_index))
            self._apply_text_style(row_label, "condition_label", row_index, font)
            self._position_text(row_label, "condition_label", row_index, band_left - row_label.boundingRect().width() - 18, current_y)
            self._add_layout_text(group, row_label)
            for value_index, value in enumerate(row.get("values", [])[: len(lane_positions)]):
                text = self._editable_text(str(value), ("condition", value_index))
                self._apply_text_style(text, "condition", value_index, font)
                self._position_text(text, "condition", value_index, band_left + lane_positions[value_index] - text.boundingRect().width() / 2, current_y)
                self._add_layout_text(group, text)
        for level in reversed(group_levels):
            current_y -= 22.0
            level_index = int(level.get("level", 0))
            for group_index, group_def in enumerate(level.get("groups", [])):
                start = max(1, int(group_def.get("lane_start", 1)))
                end = min(len(lane_positions), int(group_def.get("lane_end", start)))
                if not lane_positions or start > end:
                    continue
                x_start = band_left + lane_positions[start - 1]
                x_end = band_left + lane_positions[end - 1]
                x_center = (x_start + x_end) / 2
                kind = f"group_l{level_index}"
                payload_index = level_index * 1000 + group_index
                text = self._editable_text(str(group_def.get("label", "")), (kind, payload_index))
                self._apply_text_style(text, kind, payload_index, font)
                self._position_text(text, kind, payload_index, x_center - text.boundingRect().width() / 2, current_y)
                self._add_layout_text(group, text)
                if bool(group_def.get("show_bracket", True)):
                    self._add_bracket(group, x_start, x_end, current_y + text.boundingRect().height() + 4.0, kind, payload_index)

    def _add_bracket(self, group: WBItemGroup, x_start: float, x_end: float, y_bottom: float, kind: str, index: int) -> None:
        tick_len = 3.0
        uid = f"{kind}:{index}"
        path = QPainterPath()
        path.moveTo(x_start, y_bottom)
        path.lineTo(x_end, y_bottom)
        path.moveTo(x_start, y_bottom)
        path.lineTo(x_start, y_bottom + tick_len)
        path.moveTo(x_end, y_bottom)
        path.lineTo(x_end, y_bottom + tick_len)
        line = WBMovablePathItem(path, self._emit_layout_line_moved, (uid,))
        line.setData(0, "wb_layout_line")
        line.setData(1, uid)
        line.setZValue(50)
        self._apply_line_style(line, uid)
        self._register_group_attachment(group, line)

    def _apply_line_style(self, line: QGraphicsLineItem, uid: str) -> None:
        line_styles = self.panel.data.get("wb_layout", {}).get("line_styles", {}) if self.panel is not None else {}
        style = dict(line_styles.get(str(uid), {}))
        color = QColor(str(style.get("color", "#111111")))
        width = float(style.get("width", 0.75))
        line.setPen(QPen(color, width))
        saved_pos = style.get("pos")
        if isinstance(saved_pos, dict):
            pos_x = float(saved_pos.get("x", 0.0))
            pos_y = float(saved_pos.get("y", 0.0))
            setter = getattr(line, "set_position_without_callback", None)
            if callable(setter):
                setter(pos_x, pos_y)
            else:
                line.setPos(pos_x, pos_y)

    def _editable_text(self, text: str, payload: tuple[str, int]) -> EditableMovableTextItem:
        item = EditableMovableTextItem(text, self.layout_text_changed.emit, self._emit_layout_item_moved, self.layout_style_changed.emit, payload)
        item.setZValue(100)
        return item

    def _add_layout_text(self, group: WBItemGroup, text: EditableMovableTextItem) -> None:
        text.setSelected(False)
        text.setZValue(100)
        self._register_group_attachment(group, text)

    def _position_text(self, text: EditableMovableTextItem, kind: str, index: int, default_x: float, default_y: float) -> None:
        if kind.startswith("stats_"):
            pos_x, pos_y = self._stats_label_position(kind, index, default_x, default_y)
            text.set_position_without_callback(pos_x, pos_y)
            text.enable_position_tracking()
            return
        positions = self.panel.data.get("wb_layout", {}).get("manual_positions", {}) if self.panel is not None else {}
        saved = positions.get(kind, {}).get(str(index))
        if saved:
            text.set_position_without_callback(float(saved.get("x", default_x)), float(saved.get("y", default_y)))
        else:
            text.set_position_without_callback(default_x, default_y)
        text.enable_position_tracking()

    def _apply_text_style(self, text: EditableMovableTextItem, kind: str, index: int, base_font: QFont) -> None:
        styles = self.panel.data.get("wb_layout", {}).get("text_styles", {}) if self.panel is not None else {}
        kind_styles = styles.get(kind, {})
        style = dict(kind_styles.get("default", {}))
        style.update(kind_styles.get("items", {}).get(str(index), {}))
        font = QFont(base_font)
        if style.get("font_family"):
            font.setFamily(str(style["font_family"]))
        if style.get("font_size_pt"):
            font.setPointSizeF(float(style["font_size_pt"]))
        if "bold" in style:
            font.setBold(bool(style["bold"]))
        if "italic" in style:
            font.setItalic(bool(style["italic"]))
        text.setFont(font)
        text.setDefaultTextColor(QColor(str(style.get("color", "#111111"))))
        text.setRotation(float(style.get("rotation", 0.0)))
        if style.get('text_payload'):
            text.load_text_payload(style['text_payload'])
            text._fit_text_width()

    def _render_statistics_plot_layout(self, default_x: float, default_y: float) -> bool:
        if self.panel is None:
            return False
        result = dict(self.panel.data.get("wb_statistics", {}))
        rows = list(result.get("plot_data", []))
        if not rows:
            return False
        groups = list(dict.fromkeys(str(row.get("group", "")) for row in rows if str(row.get("group", ""))))
        if not groups:
            return False

        from matplotlib import rc_context
        stats_plot = dict(self.panel.data.get("wb_layout", {}).get("stats_plot", {}))
        saved_style = dict(stats_plot.get("style", {}))
        style = resolve_statistics_plot_style(str(self.panel.style.get("preset", "Nature")), saved_style=saved_style)
        width = float(stats_plot.get("width_px", 520))
        height = float(stats_plot.get("height_px", 360))
        x = float(stats_plot.get("x", default_x))
        y = float(stats_plot.get("y", default_y))
        left = 70.0
        top = 24.0
        right = 24.0
        bottom = 58.0
        plot_width = max(1.0, width - left - right)
        plot_height = max(1.0, height - top - bottom)
        values_by_group = {
            group: [float(row["value"]) for row in rows if str(row.get("group", "")) == group and isinstance(row.get("value"), (int, float))]
            for group in groups
        }
        means = [sum(values_by_group[group]) / max(len(values_by_group[group]), 1) for group in groups]
        ymax = max([*means, *(value for values in values_by_group.values() for value in values), 1.0]) * 1.25

        group = WBItemGroup(self._record_stats_plot_position)
        group.setData(0, "wb_stats_plot")
        group.setZValue(20)
        self._stats_group = group
        self.scene.addItem(group)

        slot_width = plot_width / max(len(groups), 1)
        render_style = dict(style)
        if "point_size" in saved_style:
            render_style["point_size"] = max(4.0, float(render_style["point_size"]) ** 2)
        render_style.update({"show_x_labels": False, "show_y_label": False, "show_comparison_labels": False})
        try:
            figure = build_wb_statistics_figure(result, style=render_style, chart_kind=stats_plot.get("chart_kind"))
            figure.set_size_inches(width / 72.0, height / 72.0)
            figure.subplots_adjust(
                left=max(0.02, min(0.45, left / max(width, 1.0))),
                right=max(0.55, min(0.98, 1.0 - right / max(width, 1.0))),
                bottom=max(0.06, min(0.45, bottom / max(height, 1.0))),
                top=max(0.55, min(0.98, 1.0 - top / max(height, 1.0))),
            )
            buffer = BytesIO()
            with rc_context({"svg.fonttype": "none"}):
                figure.savefig(buffer, format="svg", bbox_inches=None, pad_inches=0.0, facecolor="white")
            svg_bytes = apply_svg_text_overrides(buffer.getvalue(), dict(stats_plot.get("svg_text_overrides", {})))
            renderer = QSvgRenderer(QByteArray(svg_bytes))
        except Exception:
            renderer = None
        if renderer is not None and renderer.isValid():
            svg_item = QGraphicsSvgItem()
            svg_item.setSharedRenderer(renderer)
            svg_item._sciefig_renderer = renderer
            svg_item.setPos(0, 0)
            svg_item.setZValue(0)
            group.addToGroup(svg_item)
            group.set_svg_text_editor(
                svg_bytes,
                svg_item,
                lambda text_index, text: self._update_statistics_plot_svg_text(text_index, text),
            )

        for index, group_name in enumerate(groups):
            center_x = left + (index + 0.5) * plot_width / len(groups)
            label = self._editable_text(self._stats_plot_label("stats_x_label", index, group_name), ("stats_x_label", index))
            self._apply_text_style(label, "stats_x_label", index, QFont(str(style.get("font_family", "Arial")), int(float(style.get("font_size_pt", 7.0)))))
            self._position_text(label, "stats_x_label", index, center_x - label.boundingRect().width() / 2, top + plot_height + 8)
            self._add_layout_text(group, label)

        y_label = self._editable_text(self._stats_plot_label("stats_y_label", 0, "Relative expression (fold change)"), ("stats_y_label", 0))
        self._apply_text_style(y_label, "stats_y_label", 0, QFont(str(style.get("font_family", "Arial")), int(float(style.get("font_size_pt", 7.0)))))
        if not self._stats_rotation_saved("stats_y_label", 0):
            y_label.setRotation(-90.0)
        self._position_text(y_label, "stats_y_label", 0, 10, top + plot_height / 2 + y_label.boundingRect().width() / 2)
        self._add_layout_text(group, y_label)

        comparison_index = 0
        for comparison in result.get("comparisons", []):
            group1 = str(comparison.get("group1", ""))
            group2 = str(comparison.get("group2", ""))
            if group1 not in groups or group2 not in groups:
                continue
            x1 = left + (groups.index(group1) + 0.5) * plot_width / len(groups)
            x2 = left + (groups.index(group2) + 0.5) * plot_width / len(groups)
            bracket_y = max(top + 8.0, top + 22.0 + comparison_index * 20.0)
            label_text = self._stats_plot_label("stats_sig_label", comparison_index, str(comparison.get("label", "n/a")))
            label = self._editable_text(label_text, ("stats_sig_label", comparison_index))
            self._apply_text_style(label, "stats_sig_label", comparison_index, QFont(str(style.get("font_family", "Arial")), int(float(style.get("font_size_pt", 7.0)))))
            self._position_text(label, "stats_sig_label", comparison_index, (x1 + x2) / 2 - label.boundingRect().width() / 2, bracket_y - 18)
            self._add_layout_text(group, label)
            comparison_index += 1

        group.set_position_without_callback(x, y)
        self._apply_initial_group_offset(group, QPointF(x, y))
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, self.view.mode == "move")
        group.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, self.view.mode == "move")
        return True

    def _update_statistics_plot_svg_text(self, text_index: int, text: str) -> None:
        if self.panel is None:
            return
        layout = self.panel.data.setdefault("wb_layout", {})
        stats_plot = dict(layout.get("stats_plot", {}))
        overrides = {str(key): str(value) for key, value in dict(stats_plot.get("svg_text_overrides", {})).items()}
        overrides[str(text_index)] = str(text)
        stats_plot["svg_text_overrides"] = overrides
        layout["stats_plot"] = stats_plot
        self.panel.append_audit("update_wb_statistics_svg_text", f"{text_index}")

    def _stats_plot_label(self, kind: str, index: int, default: str) -> str:
        if self.panel is None:
            return default
        labels = self.panel.data.get("wb_layout", {}).get("stats_plot", {}).get("labels", {})
        return str(labels.get(kind, {}).get(str(index), default))

    def _adjust_qimage_for_display(self, image: QImage, brightness: int, contrast: int) -> QImage:
        # TODO: Replace this per-pixel loop with a LUT before applying it to large images.
        adjusted = image.convertToFormat(QImage.Format.Format_ARGB32)
        factor = (259 * (contrast + 255)) / (255 * (259 - contrast)) if contrast != 0 else 1.0
        for y in range(adjusted.height()):
            for x in range(adjusted.width()):
                color = adjusted.pixelColor(x, y)
                red = max(0, min(255, int(factor * (color.red() - 128) + 128 + brightness)))
                green = max(0, min(255, int(factor * (color.green() - 128) + 128 + brightness)))
                blue = max(0, min(255, int(factor * (color.blue() - 128) + 128 + brightness)))
                adjusted.setPixelColor(x, y, QColor(red, green, blue, color.alpha()))
        return adjusted

