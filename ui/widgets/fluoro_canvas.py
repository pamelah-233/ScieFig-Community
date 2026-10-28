from __future__ import annotations
from core.models.layout_editor import LayoutObject
from uuid import uuid4
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem
from ui.widgets.shared_layout_controls import apply_selection_style
from ui.layout_adapters.annotation_records import text_from_record,shape_from_record,write_shared_fields

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from PyQt6.QtCore import QLineF, QMimeData, QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.models import FluorescencePanel
from core.plotting.statistics_plot import build_distribution_comparison_figure, build_group_statistics_figure, resolve_statistics_plot_style
from core.plotting.fluoro_render import (
    FORCED_RGB_SPLIT_MODE,
    RGB_COMPOSITE_MODE,
    render_fluorescence_qimage,
    render_merge_qimage,
    render_single_qimage,
    rgb_interpretation_mode,
)
from core.style_preset_resolver import (
    resolve_image_plate_style,
    resolve_runtime_image_plate_style_if_active,
    runtime_style_cache_token,
)
from core.utils.fluoro_image_io import SUPPORTED_FLUORESCENCE_SUFFIXES
from core.utils.i18n import tr, trf
from core.utils.image_formats import dropped_image_paths
from core.utils.text_formatting import display_unit
from core.utils.svg_text import apply_svg_text_overrides, normalize_svg_text_overrides, strip_svg_text_layers, svg_text_entries, update_svg_text_overrides
from ui.widgets.canvas_presentation import configure_canvas_layout
from ui.widgets.canvas_graphics_adapter import (
    CanvasGraphicsView,
    add_canvas_hint,
    CanvasExportBackgroundItem,
    canvas_owner_of,
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
from ui.widgets.scale_line_drawing import constrained_scale_line_end, scale_line_is_valid, scale_line_preview_pen, ScaleLinePreviewItem
from ui.widgets.layout_editing import EditableLayoutRecord, align_records, distribute_records, make_records_same_size
from ui.widgets.svg_text_editor import prompt_svg_text_edit
from ui.widgets.editable_text_item import (
    EditableMovableTextItem,
    clear_graphics_scene_for_cleanup,
    detach_graphics_item,
    graphics_item_is_alive,
    register_graphics_scene_for_cleanup,
    svg_bytes_to_pixmap,
)

PREVIEW_TARGET_WIDTH = 820.0
LAYOUT_TARGET_WIDTH = 720.0
FLUOR_HANDLE_SIZE_PX = 8.0
FLUOR_RESIZE_HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
FLUOR_LAYOUT_PLOT_MARKERS = {"fluoro_stats_plot", "fluoro_profile_plot", "fluoro_coloc_plot"}
FLUOR_LAYOUT_ARRANGE_MARKERS = {
    "fluoro_image_channel",
    "fluoro_image_merge",
    "fluoro_layout_shape",
    "fluoro_layout_text",
    *FLUOR_LAYOUT_PLOT_MARKERS,
}
FLUOR_LAYOUT_DELETE_MARKERS = {"fluoro_image_channel", "fluoro_image_merge", "fluoro_layout_shape", "fluoro_layout_text", "fluoro_scale_bar", *FLUOR_LAYOUT_PLOT_MARKERS}
FLUOR_LAYOUT_SNAPSHOT_IMAGE_KEYS = (
    "layout_mode",
    "layout_item_geometries",
    "layout_shapes",
    "layout_texts",
    "layout_scale_bar_template",
    "layout_scale_bars",
    "scale_bar",
)
FLUOR_LAYOUT_SNAPSHOT_ANALYSIS_KEYS = ("fluoro_layout_plots", "fluoro_stats_plot")


def fluorescence_dropped_paths(mime_data: QMimeData) -> list[str]:
    return dropped_image_paths(mime_data, SUPPORTED_FLUORESCENCE_SUFFIXES)


def _line_profile_distance_series(row: dict[str, Any]) -> tuple[list[float], str]:
    um = row.get("distance_um")
    if isinstance(um, (list, tuple)) and um:
        return [float(value) for value in um], "um"
    px = row.get("distance_px")
    if isinstance(px, (list, tuple)) and px:
        return [float(value) for value in px], "px"
    return [], "px"


class ImageCoordinateMapper:
    """Map QGraphicsScene coordinates to the active analysis image pixels."""

    def __init__(self, pixmap_item: QGraphicsPixmapItem, image_shape: tuple[int, int]) -> None:
        self.pixmap_item = pixmap_item
        self.image_height = max(1, int(image_shape[0]))
        self.image_width = max(1, int(image_shape[1]))

    def scene_pos_to_image_px(self, scene_pos: QPointF) -> tuple[float, float]:
        local = self.pixmap_item.mapFromScene(scene_pos)
        rect = self.pixmap_item.boundingRect()
        x = local.x() * self.image_width / max(rect.width(), 1.0)
        y = local.y() * self.image_height / max(rect.height(), 1.0)
        return self.clamp_to_image_bounds(x, y)

    def image_px_to_scene_pos(self, image_xy: tuple[float, float]) -> QPointF:
        return self.pixmap_item.mapToScene(self.image_px_to_local_pos(image_xy))

    def image_px_to_local_pos(self, image_xy: tuple[float, float]) -> QPointF:
        x, y = self.clamp_to_image_bounds(float(image_xy[0]), float(image_xy[1]))
        rect = self.pixmap_item.boundingRect()
        local_x = x * max(rect.width(), 1.0) / self.image_width
        local_y = y * max(rect.height(), 1.0) / self.image_height
        return QPointF(local_x, local_y)

    def roi_path_scene_to_image_mask(self, path: QPainterPath, image_shape: tuple[int, int]) -> np.ndarray:
        height, width = int(image_shape[0]), int(image_shape[1])
        mask = np.zeros((height, width), dtype=bool)
        for y in range(height):
            for x in range(width):
                if path.contains(self.image_px_to_scene_pos((x + 0.5, y + 0.5))):
                    mask[y, x] = True
        return mask

    def line_scene_to_image_px(self, start_scene: QPointF, end_scene: QPointF) -> dict[str, float]:
        x1, y1 = self.scene_pos_to_image_px(start_scene)
        x2, y2 = self.scene_pos_to_image_px(end_scene)
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    def clamp_to_image_bounds(self, x: float, y: float) -> tuple[float, float]:
        return (
            max(0.0, min(float(x), float(self.image_width - 1))),
            max(0.0, min(float(y), float(self.image_height - 1))),
        )


class FluoroResizeHandle(QGraphicsRectItem):
    def __init__(self, parent_item: "FluoroLayoutPixmapItem", role: str) -> None:
        half = FLUOR_HANDLE_SIZE_PX / 2.0
        super().__init__(-half, -half, FLUOR_HANDLE_SIZE_PX, FLUOR_HANDLE_SIZE_PX, parent_item)
        self.parent_item = parent_item
        self.role = role
        self.start_scene_pos = QPointF()
        self.start_geometry = (0.0, 0.0, 0.0, 0.0)
        self.setData(0, "fluoro_resize_handle")
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#d97757"), 1.2))
        self.setZValue(1000)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event) -> None:
        self.parent_item.canvas._record_layout_snapshot()
        self.start_scene_pos = event.scenePos()
        rect = self.parent_item.rect()
        pos = self.parent_item.pos()
        self.start_geometry = (pos.x(), pos.y(), rect.width(), rect.height())
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        dx = event.scenePos().x() - self.start_scene_pos.x()
        dy = event.scenePos().y() - self.start_scene_pos.y()
        x, y, width, height = self.start_geometry
        new_x, new_y, new_width, new_height = x, y, width, height
        min_size = 16.0

        if "e" in self.role:
            new_width = max(min_size, width + dx)
        if "s" in self.role:
            new_height = max(min_size, height + dy)
        if "w" in self.role:
            new_width = max(min_size, width - dx)
            new_x = x + (width - new_width)
        if "n" in self.role:
            new_height = max(min_size, height - dy)
            new_y = y + (height - new_height)

        tools=getattr(self.parent_item.canvas.view.object_interaction,'editing_tools',None)
        keep_ratio=tools.image_aspect_locked(self.parent_item) if tools is not None else True
        if keep_ratio != bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            from ui.widgets.layout_editing import aspect_resize_size
            new_width,new_height=aspect_resize_size(width,height,new_width,new_height,self.role)
            if 'w' in self.role:new_x=x+width-new_width
            if 'n' in self.role:new_y=y+height-new_height
            if self.role in {'e','w'}:new_y=y+(height-new_height)/2
            if self.role in {'n','s'}:new_x=x+(width-new_width)/2

        self.parent_item.apply_geometry(new_x, new_y, new_width, new_height, repaint=False)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.parent_item.apply_geometry(
            self.parent_item.pos().x(),
            self.parent_item.pos().y(),
            self.parent_item.rect().width(),
            self.parent_item.rect().height(),
        )
        event.accept()


class FluoroLayoutPixmapItem(QGraphicsRectItem):
    def __init__(
        self,
        canvas: "FluorescenceCanvas",
        source: QPixmap | bytes,
        marker: str,
        uid: str,
        x: float,
        y: float,
        width: float,
        height: float,
        plot_type: str = "",
        svg_text_overrides: dict[str, Any] | None = None,
        svg_text_changed_callback=None,
    ) -> None:
        super().__init__(0, 0, max(1.0, float(width)), max(1.0, float(height)))
        self.canvas = canvas
        self.scale_bar_attachments = []
        self._svg_base = bytes(source) if isinstance(source, bytes) else b""
        self._svg_overrides = normalize_svg_text_overrides(dict(svg_text_overrides or {}))
        self._svg_text_changed_callback = svg_text_changed_callback
        self._svg_text_items: list[SharedTextItem] = []
        self._display_svg_bytes = b""
        self._export_svg_bytes = b""
        if isinstance(source, bytes):
            self._refresh_svg_payload()
            pixmap = svg_bytes_to_pixmap(self._display_svg_bytes or self._export_svg_bytes)
            self._source_width = max(1, pixmap.width())
            self._source_height = max(1, pixmap.height())
            self._visual_item = QGraphicsPixmapItem(pixmap, self)
            self._visual_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._visual_item.setData(0, "fluoro_layout_plot_svg")
            self._visual_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        else:
            self._source_width = max(1, source.width())
            self._source_height = max(1, source.height())
            self._visual_item = QGraphicsPixmapItem(source, self)
            self._visual_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            # The source pixmap remains full resolution.  Device-coordinate
            # caching can retain a low-resolution version after zooming.
            self._visual_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.setData(0, marker)
        self.setData(1, uid)
        self.setData(2, plot_type)
        self.setPos(float(x), float(y))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(250 if marker in FLUOR_LAYOUT_PLOT_MARKERS else 100)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.handles = {role: FluoroResizeHandle(self, role) for role in FLUOR_RESIZE_HANDLES}
        self._sync_pixmap_transform()
        self.update_handles()
        self._rebuild_svg_text_items()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.set_handles_visible(bool(value))
        elif change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged and self._svg_base:
            self._rebuild_svg_text_items()
        return super().itemChange(change, value)

    def set_handles_visible(self, visible: bool) -> None:
        for handle in self.handles.values():
            handle.setVisible(visible)

    def update_handles(self) -> None:
        rect = self.rect()
        points = {
            "nw": rect.topLeft(),
            "n": QPointF(rect.center().x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), rect.center().y()),
            "se": rect.bottomRight(),
            "s": QPointF(rect.center().x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), rect.center().y()),
        }
        for role, point in points.items():
            self.handles[role].setPos(point)

    def apply_geometry(self, x: float, y: float, width: float, height: float, repaint: bool = True) -> None:
        previous = self.rect()
        self.setPos(float(x), float(y))
        self.setRect(0, 0, max(1.0, float(width)), max(1.0, float(height)))
        self._sync_pixmap_transform()
        if self.rect().size() != previous.size():
            for attachment in self.scale_bar_attachments:
                attachment.resize_with_image(self.rect().width()/previous.width(),self.rect().height()/previous.height())
        self.update_handles()
        self._rebuild_svg_text_items()
        if repaint and self.scene() is not None:
            self.scene().update(self.sceneBoundingRect())

    def _sync_pixmap_transform(self) -> None:
        rect = self.rect()
        transform = QTransform()
        transform.scale(rect.width() / self._source_width, rect.height() / self._source_height)
        self._visual_item.setTransform(transform)
        self._visual_item.setPos(0, 0)

    def layout_scene_rect(self) -> QRectF:
        return self.mapToScene(self.rect()).boundingRect()

    def apply_svg_text_override(self, index: int, text: str) -> bool:
        if not self._svg_base:
            return False
        self._svg_overrides = update_svg_text_overrides(self._svg_overrides, int(index), text=str(text))
        self._refresh_svg_payload()
        if isinstance(self._visual_item, QGraphicsPixmapItem):
            self._visual_item.setPixmap(svg_bytes_to_pixmap(self._display_svg_bytes or self._export_svg_bytes))
        if callable(self._svg_text_changed_callback):
            self._svg_text_changed_callback(int(index), self._svg_overrides[str(int(index))])
        self._rebuild_svg_text_items()
        self.update()
        return True


    def _rendered_svg_bytes(self) -> bytes:
        return apply_svg_text_overrides(self._svg_base, self._svg_overrides) if self._svg_base else b""

    def _refresh_svg_payload(self) -> None:
        canonical = self._rendered_svg_bytes()
        self._export_svg_bytes = canonical
        self._display_svg_bytes = strip_svg_text_layers(canonical) if canonical else b""





    def _rebuild_svg_text_items(self) -> None:
        from ui.widgets.shared_svg_text import SvgTextBinding
        if not hasattr(self, "_svg_binding"):
            for item in self._svg_text_items:
                detach_graphics_item(item)
            self._svg_binding = SvgTextBinding(
                self, self._visual_item, self._rendered_svg_bytes,
                lambda: self._svg_overrides, self._store_shared_svg_text)
        self._svg_binding.rebuild()
        self._svg_text_items = self._svg_binding.items

    def _store_shared_svg_text(self, index, value):
        self._svg_overrides[str(index)] = value
        if callable(self._svg_text_changed_callback):
            self._svg_text_changed_callback(index, value)

    def contextMenuEvent(self, event) -> None:
        if not self._svg_base:
            super().contextMenuEvent(event)
            return
        menu = QMenu()
        edit_action = menu.addAction("Edit SVG Text...")
        action = menu.exec(event.screenPos())
        if action != edit_action:
            return
        edit = prompt_svg_text_edit(None, self._rendered_svg_bytes(), "Edit Plot Text")
        if edit is not None:
            entry, replacement = edit
            self.apply_svg_text_override(entry.index, replacement)
        event.accept()

    def prepare_scene_export(self) -> None:
        return

    def finish_scene_export(self) -> None:
        return

    def release_scene_resources(self) -> None:
        items, self._svg_text_items = self._svg_text_items, []
        for item in items:
            detach_graphics_item(item)
        if self._svg_base and isinstance(self._visual_item, QGraphicsPixmapItem) and graphics_item_is_alive(self._visual_item):
            self._visual_item.setPixmap(QPixmap())


class FluoroGraphicsView(CanvasGraphicsView):
    files_dropped = pyqtSignal(list)
    scene_roi_drawn = pyqtSignal(str, object)
    display_point_cancelled = pyqtSignal()

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.roi_mode = ""
        self._press_scene_pos: QPointF | None = None
        self._temp_roi_item = None
        self._freehand_points: list[QPointF] = []
        self._scale_drag_item: QGraphicsLineItem | None = None
        self._scale_drag_role = ""
        self._scale_drag_start_scene_pos = QPointF()
        self._scale_drag_start_line = QLineF()

    def set_roi_mode(self, mode: str) -> None:
        if (self.roi_mode == "scale_line" and mode != "scale_line") or (self.roi_mode in {"display_black_point", "display_white_point"} and mode != self.roi_mode):
            self._clear_scale_line_draft()
        self.roi_mode = mode
        canvas = canvas_owner_of(self)
        if isinstance(canvas, FluorescenceCanvas) and canvas.current_step == 4 and not mode:
            self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        elif mode:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.CrossCursor if mode in {"rectangle", "square", "ellipse", "circle", "freehand", "sample_spot", "profile_line", "scale_line", "text_select", "black_balance", "display_black_point", "display_white_point"} else Qt.CursorShape.ArrowCursor)

    def dragEnterEvent(self, event) -> None:
        if fluorescence_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if fluorescence_dropped_paths(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = fluorescence_dropped_paths(event.mimeData())
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def keyPressEvent(self, event) -> None:
        if is_canvas_text_editing(self):
            super().keyPressEvent(event)
            return
        if handle_canvas_space_press(self, event):
            return
        if handle_canvas_zoom_shortcut(self, event):
            return
        if event.key() == Qt.Key.Key_Escape and self.roi_mode in {"display_black_point", "display_white_point"}:
            self.set_roi_mode("")
            self.display_point_cancelled.emit()
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.roi_mode == "scale_line":
            self._clear_scale_line_draft()
            event.accept()
            return
        if event.matches(QKeySequence.StandardKey.SelectAll):
            canvas = canvas_owner_of(self)
            if isinstance(canvas, FluorescenceCanvas) and canvas.current_step != 4:
                for item in self.scene().items():
                    if isinstance(item, EditableMovableTextItem):
                        item.setSelected(True)
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

    def mousePressEvent(self, event) -> None:
        if handle_canvas_pan_press(self, event):
            return
        if event.button() == Qt.MouseButton.LeftButton and not self.roi_mode and getattr(self, 'scale_editing_enabled', True):
            scale_item = self._measurement_scale_item_at(event.position().toPoint())
            if scale_item is not None:
                self._scale_drag_item = scale_item
                self._scale_drag_role = self._measurement_scale_drag_role(scale_item, self.mapToScene(event.position().toPoint()))
                self._scale_drag_start_scene_pos = self.mapToScene(event.position().toPoint())
                self._scale_drag_start_line = QLineF(scale_item.line())
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton and self.roi_mode in {"rectangle", "square", "ellipse", "circle", "freehand", "sample_spot", "profile_line", "scale_line", "text_select", "black_balance", "display_black_point", "display_white_point"}:
            if self.roi_mode == "text_select" and not isinstance(self.itemAt(event.position().toPoint()), QGraphicsTextItem):
                self.scene().clearSelection()
            self._press_scene_pos = self.mapToScene(event.position().toPoint())
            self._freehand_points = [self._press_scene_pos]
            if self.roi_mode == "scale_line":
                self.setFocus(Qt.FocusReason.MouseFocusReason)
                pen = scale_line_preview_pen()
            else:
                pen = QPen(QColor("#4F8CFF") if self.roi_mode == "text_select" else QColor("#d97757"), 1.5, Qt.PenStyle.DashLine)
            if self.roi_mode in {"ellipse", "circle"}:
                self._temp_roi_item = QGraphicsEllipseItem(QRectF(self._press_scene_pos, self._press_scene_pos))
            elif self.roi_mode == "freehand":
                self._temp_roi_item = QGraphicsPathItem()
                path = QPainterPath(self._press_scene_pos)
                self._temp_roi_item.setPath(path)
            elif self.roi_mode in {"profile_line", "scale_line"}:
                line_type=ScaleLinePreviewItem if self.roi_mode=='scale_line' else QGraphicsLineItem
                self._temp_roi_item = line_type(self._press_scene_pos.x(), self._press_scene_pos.y(), self._press_scene_pos.x(), self._press_scene_pos.y())
            else:
                self._temp_roi_item = QGraphicsRectItem(QRectF(self._press_scene_pos, self._press_scene_pos))
            self._temp_roi_item.setPen(pen)
            self._temp_roi_item.setZValue(400)
            self.scene().addItem(self._temp_roi_item)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if handle_canvas_pan_move(self, event):
            return
        if self._scale_drag_item is not None and self._scale_drag_role:
            self._update_measurement_scale_drag(self.mapToScene(event.position().toPoint()))
            event.accept()
            return
        if self._press_scene_pos is not None and self._temp_roi_item is not None and self.roi_mode:
            current = self.mapToScene(event.position().toPoint())
            if self.roi_mode in {"rectangle", "square", "ellipse", "circle", "sample_spot", "text_select", "black_balance", "display_black_point", "display_white_point"}:
                self._temp_roi_item.setRect(self._normalized_roi_rect(current))
            elif self.roi_mode == "freehand":
                self._freehand_points.append(current)
                path = QPainterPath(self._freehand_points[0])
                for point in self._freehand_points[1:]:
                    path.lineTo(point)
                self._temp_roi_item.setPath(path)
            elif self.roi_mode in {"profile_line", "scale_line"}:
                end = self._line_end_for_current_mode(current, event.modifiers())
                self._temp_roi_item.setLine(self._press_scene_pos.x(), self._press_scene_pos.y(), end.x(), end.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if handle_canvas_pan_release(self, event):
            return
        if event.button() == Qt.MouseButton.LeftButton and self._scale_drag_item is not None and self._scale_drag_role:
            self._update_measurement_scale_drag(self.mapToScene(event.position().toPoint()))
            line = self._scale_drag_item.line()
            p1 = self._scale_drag_item.mapToScene(line.p1())
            p2 = self._scale_drag_item.mapToScene(line.p2())
            self.scene_roi_drawn.emit(
                "scale_line",
                {"x1": p1.x(), "y1": p1.y(), "x2": p2.x(), "y2": p2.y(), "move_existing": True},
            )
            self._scale_drag_item = None
            self._scale_drag_role = ""
            self._scale_drag_start_line = QLineF()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._press_scene_pos is not None and self._temp_roi_item is not None and self.roi_mode:
            current = self.mapToScene(event.position().toPoint())
            if self.roi_mode in {"rectangle", "square", "ellipse", "circle", "sample_spot", "text_select", "black_balance", "display_black_point", "display_white_point"}:
                payload = self._normalized_roi_rect(current)
            elif self.roi_mode in {"profile_line", "scale_line"}:
                end = self._line_end_for_current_mode(current, event.modifiers())
                payload = {
                    "x1": self._press_scene_pos.x(),
                    "y1": self._press_scene_pos.y(),
                    "x2": end.x(),
                    "y2": end.y(),
                }
                if self.roi_mode == "scale_line":
                    self._clear_scale_line_draft()
                    if not scale_line_is_valid(payload):
                        event.accept()
                        return
                    self.scene_roi_drawn.emit("scale_line", payload)
                    event.accept()
                    return
            else:
                payload = [(point.x(), point.y()) for point in self._freehand_points]
            self.scene_roi_drawn.emit(self.roi_mode, payload)
            self._press_scene_pos = None
            self._temp_roi_item = None
            self._freehand_points = []
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _line_end_for_current_mode(self, current: QPointF, modifiers) -> QPointF:
        start = self._press_scene_pos
        if start is None or self.roi_mode != "scale_line":
            return QPointF(current)
        x2, y2 = constrained_scale_line_end(
            start.x(),
            start.y(),
            current.x(),
            current.y(),
            free_angle=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
        )
        return QPointF(x2, y2)

    def _clear_scale_line_draft(self) -> None:
        if self._temp_roi_item is not None:
            scene = self._temp_roi_item.scene()
            if scene is not None:
                scene.removeItem(self._temp_roi_item)
        self._temp_roi_item = None
        self._press_scene_pos = None
        self._freehand_points = []

    def _normalized_roi_rect(self, current: QPointF) -> QRectF:
        if self._press_scene_pos is None:
            return QRectF(current, current)
        if self.roi_mode in {"square", "circle"}:
            dx = current.x() - self._press_scene_pos.x()
            dy = current.y() - self._press_scene_pos.y()
            side = max(abs(dx), abs(dy))
            x = self._press_scene_pos.x() + (side if dx >= 0 else -side)
            y = self._press_scene_pos.y() + (side if dy >= 0 else -side)
            return QRectF(self._press_scene_pos, QPointF(x, y)).normalized()
        return QRectF(self._press_scene_pos, current).normalized()

    def _measurement_scale_item_at(self, viewport_pos) -> QGraphicsLineItem | None:
        item = self.itemAt(viewport_pos)
        while item is not None:
            if isinstance(item, QGraphicsLineItem) and item.data(0) == "fluoro_measurement_scale_line":
                return item
            item = item.parentItem()
        return None

    def _measurement_scale_drag_role(self, item: QGraphicsLineItem, scene_pos: QPointF) -> str:
        line = item.line()
        start = item.mapToScene(line.p1())
        end = item.mapToScene(line.p2())
        threshold = 10.0
        if QLineF(scene_pos, start).length() <= threshold:
            return "start"
        if QLineF(scene_pos, end).length() <= threshold:
            return "end"
        return "move"

    def _update_measurement_scale_drag(self, current_scene_pos: QPointF) -> None:
        item = self._scale_drag_item
        if item is None:
            return
        if self._scale_drag_role == "start":
            line = QLineF(self._scale_drag_start_line)
            line.setP1(item.mapFromScene(current_scene_pos))
            item.setLine(line)
            return
        if self._scale_drag_role == "end":
            line = QLineF(self._scale_drag_start_line)
            line.setP2(item.mapFromScene(current_scene_pos))
            item.setLine(line)
            return
        delta = current_scene_pos - self._scale_drag_start_scene_pos
        start = item.mapToScene(self._scale_drag_start_line.p1()) + delta
        end = item.mapToScene(self._scale_drag_start_line.p2()) + delta
        item.setLine(QLineF(item.mapFromScene(start), item.mapFromScene(end)))


class FluorescenceCanvas(SceneCanvasOwnerMixin, QWidget):
    files_dropped = pyqtSignal(list)
    roi_drawn = pyqtSignal(str, object)
    refreshed = pyqtSignal()
    measurement_overlay_changed = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasArea")
        self.scene = QGraphicsScene(self)
        register_graphics_scene_for_cleanup(self.scene)
        self.scene.setBackgroundBrush(QColor("#FFFFFF"))
        self.view = FluoroGraphicsView(self.scene, self)
        self.view.files_dropped.connect(self.files_dropped.emit)
        self.view.scene_roi_drawn.connect(self._handle_scene_roi)
        self.panel: FluorescencePanel | None = None
        self.current_step = 0
        self.single_channel_index = 0
        self._image_items: list[QGraphicsPixmapItem] = []
        self._white_canvas_item: QGraphicsRectItem | None = None
        self._measurement_overlay_item = None
        self._measurement_overlay_items: list[QGraphicsItem] = []
        self.active_measurement_overlay_uuid: str | None = None
        self._measurement_overlay_visible = True
        self._layout_undo_stack: list[dict[str, Any]] = []
        self._layout_redo_stack: list[dict[str, Any]] = []
        self._layout_plot_cache: dict[str, QImage] = {}

        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        self.zoom_controls = CanvasZoomControls()
        layout.addWidget(self.zoom_controls)
        layout.addWidget(self.view)
        attach_scene_canvas_controller(
            self,
            self.view,
            editable=lambda: self.current_step == 4 and not self.view.roi_mode,
            content_rect_provider=lambda: QRectF(self.scene.sceneRect()),
            content_frame=True,
            zoom_controls=self.zoom_controls,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        clear_graphics_scene_for_cleanup(self.scene)
        super().closeEvent(event)

    def set_panel(self, panel: FluorescencePanel, *, refresh: bool | None = None) -> None:
        panel_changed = self.panel is not panel
        self.panel = panel
        should_refresh = panel_changed if refresh is None else refresh
        if should_refresh:
            self.refresh()

    def layout_objects(self) -> list:
        if self.panel is None:
            return []
        from ui.layout_adapters.fluorescence_layout_adapter import fluorescence_layout_objects_with_scene_positions

        return fluorescence_layout_objects_with_scene_positions(self.panel, self.layout_item_positions())

    def apply_layout_objects(self, layout_objects: list, *, refresh: bool = True, append_audit: bool = True) -> None:
        if self.panel is None:
            return
        from ui.layout_adapters.fluorescence_layout_adapter import apply_fluorescence_layout_objects

        apply_fluorescence_layout_objects(self.panel, layout_objects, append_audit=append_audit)
        if refresh:
            self.refresh()

    def set_step(self, index: int, *, refresh: bool = True) -> None:
        self.current_step = index
        self.view.set_roi_mode("")
        if index == 4:
            self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        else:
            self.view.setDragMode(QGraphicsView.DragMode.NoDrag)
        if refresh:
            self.refresh()

    def set_roi_mode(self, mode: str) -> None:
        self.view.set_roi_mode(mode)

    def refresh_channel_pixels(self) -> None:
        """Display controls change pixels, not objects, annotations or navigation."""
        image = self.panel.active_image() if self.panel is not None else None
        if self.current_step == 4 or not image or not self._image_items:
            self.refresh()
            return
        channels = self._styled_channels_for_image(image)
        view_mode = str(image.get("view_mode", "merge"))
        if view_mode == "split":
            indices = [int(c.get("index", 0)) for c in channels if c.get("visible", True)] + [None]
        elif view_mode == "single":
            indices = [int(channels[max(0, min(self.single_channel_index, len(channels)-1))].get("index", 0))]
        else:
            indices = [None]
        # Visibility changes in split view change the actual set of objects.
        if indices != [item.data(1) for item in self._image_items]:
            self.refresh()
            return
        data = self.panel.image_data(str(image.get("uid")))
        if data is None:
            self.refresh()
            return
        by_index = {int(c.get("index", 0)): c for c in channels}
        for item, index in zip(self._image_items, indices):
            qimage = self._render_fluoro_qimage(image, data,
                view_mode="merge" if index is None else "single", channel=by_index.get(index))
            item.setPixmap(QPixmap.fromImage(qimage))

    def refresh(self) -> None:
        self.scene.clear()
        self._image_items = []
        self._measurement_overlay_item = None
        self._measurement_overlay_items = []
        self.active_measurement_overlay_uuid = None
        self.scene.setBackgroundBrush(QColor("#FFFFFF"))
        if self.panel is None:
            self._view_controller.set_content_identity(("fluoro",None))
            self._add_hint(tr('fluoro_canvas_import_hint'), translation_key='fluoro_canvas_import_hint')
            return
        image = self.panel.active_image()
        try:
            data = self.panel.image_data(str(image.get("uid"))) if image else None
        except (OSError, ValueError):
            # A persisted layout can outlive its source raster. Keep the
            # canvas in its normal reload-hint state while plot-only layout
            # controls remain usable.
            data = None
        presentation = None if image is None else (
            ("layout",image.get("layout_mode"),
             tuple(str(record.get('plot_uuid','')) for record in self._layout_plot_records() if record.get('enabled',True))) if self.current_step == 4
            else ("preview",image.get("view_mode")))
        self._view_controller.set_content_identity(
            ("fluoro",self.panel.panel_uuid,image.get("uid") if image else None,
             data is not None,presentation))
        if image is None or data is None:
            key = 'fluoro_canvas_import_hint' if image is None else 'fluoro_canvas_reload_hint'
            self._add_hint(tr(key), translation_key=key)
            return
        if self.current_step == 4:
            self._draw_layout(image, data)
        else:
            self._draw_preview(image, data)
            self._draw_measurement_scale_bar(image, data)
            self._draw_browser_scale_bar_overlay(image, data)
            if self.current_step != 0:
                for item in self.scene.items():
                    if item.data(0) in ('fluoro_scale_bar', 'fluoro_measurement_scale_line') or (
                            isinstance(item, QGraphicsTextItem) and getattr(item, '_payload', None) == ('fluoro_measurement_scale_bar', 0)):
                        item.setEnabled(False)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20))
        self._ensure_white_canvas()
        self._apply_canvas_zoom()
        self.refreshed.emit()

    def retranslate(self) -> None:
        self.zoom_controls.retranslate()
        for item in self.scene.items():
            key = item.data(1000)
            if key and item.toPlainText() == item.data(1001):
                value = tr(str(key))
                item.setPlainText(value)
                item.setData(1001, value)
                from ui.widgets.canvas_graphics_adapter import CanvasHintItem
                if isinstance(item,CanvasHintItem):
                    item.setTitle(tr('module_fluoro_canvas'))

    def _add_hint(self, text: str, *, translation_key: str | None = None) -> None:
        item = add_canvas_hint(self.scene,text, title=tr('module_fluoro_canvas'))
        item.setData(1000, translation_key)
        item.setData(1001, text)
        self._apply_canvas_zoom()

    def _draw_preview(self, image: dict[str, Any], data) -> None:
        view_mode = str(image.get("view_mode", "merge"))
        channels = self._styled_channels_for_image(image)
        if view_mode == "split":
            self._draw_split(image, data, layout=False)
        elif view_mode == "single":
            channel = channels[max(0, min(self.single_channel_index, len(channels) - 1))]
            item = self._add_pixmap(self._render_fluoro_qimage(image, data, view_mode="single", channel=channel), 0, 0, channel.get("name", trf('fluoro_canvas_channel', index=int(channel.get('index',0))+1)), PREVIEW_TARGET_WIDTH, source_channel_index=int(channel.get("index", 0)))
            self._draw_scale_bar(image, item, layout_key=f"merge_split:channel:{int(channel.get('index', 0))}", allow_legacy=False)
        else:
            item = self._add_pixmap(self._render_fluoro_qimage(image, data, view_mode="merge"), 0, 0, "Merge", PREVIEW_TARGET_WIDTH, source_channel_index=None, label_translation_key='fluoro_canvas_merge')
            self._draw_scale_bar(image, item, layout_key=f"{image.get('layout_mode', 'merge_split')}:merge", allow_legacy=False)

    def _draw_measurement_scale_bar(self, image: dict[str, Any], data) -> None:
        if self.view.roi_mode != "scale_line" and any(s.get("template") for s in image.get("layout_scale_bars", [])):
            return
        if self.view.roi_mode != "scale_line" and (
            self._browser_display_is_visible(image)
            or any(item.data(0) == 'fluoro_scale_bar' for item in self.scene.items())
        ):
            return
        scale = dict(image.get("measurement_scale_bar", {}))
        if not scale:
            return
        if "display_line_px" in scale:
            raw_line = scale.get("display_line_px")
            line = dict(raw_line) if isinstance(raw_line, dict) else {}
        else:
            raw_line = scale.get("line_px")
            line = dict(raw_line) if isinstance(raw_line, dict) else {}
        unknown = (
            bool(scale.get("assumed"))
            or str(scale.get("calibration_source") or "") == "assumed"
            or ("calibrated" in scale and not bool(scale.get("calibrated")))
        )
        if unknown or not line or not self._image_items:
            return
        item = self._image_items[0]
        pixmap = item.pixmap()
        height = max(1, int(data.shape[1]))
        width = max(1, int(data.shape[2]))
        sx = pixmap.width() / width
        sy = pixmap.height() / height
        x1 = float(line.get("x1", 0.0)) * sx
        y1 = float(line.get("y1", 0.0)) * sy
        x2 = float(line.get("x2", 0.0)) * sx
        y2 = float(line.get("y2", 0.0)) * sy
        scale_line = QGraphicsLineItem(x1, y1, x2, y2, item)
        scale_pen = QPen(QColor("#FFD13B"), 3)
        scale_pen.setCosmetic(True)
        scale_line.setPen(scale_pen)
        scale_line.setData(0, "fluoro_measurement_scale_line")
        scale_line.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        scale_line.setZValue(500)
        label = EditableMovableTextItem(f"{float(scale.get('length_um', 50.0)):g} um", _noop_text, _noop_move, _noop_style, ("fluoro_measurement_scale_bar", 0))
        label.setDefaultTextColor(QColor("#FFFFFF"))
        label.setFont(QFont("Arial", 10))
        label.setParentItem(item)
        display_scale = max(float(item.scale()), 1e-6)
        label.setScale(1.0 / display_scale)
        label.setPos(min(x1, x2), min(y1, y2) - 18.0 / display_scale)
        label.setZValue(501)
        from ui.widgets.scale_bar_attachment import ScaleBarAttachment
        def store_reference_position(pair):
            moved = pair.line.line()
            p1 = pair.line.pos() + moved.p1()
            p2 = pair.line.pos() + moved.p2()
            image.setdefault('measurement_scale_bar', {}).update(display_line_px={
                'x1':p1.x()/sx, 'y1':p1.y()/sy, 'x2':p2.x()/sx, 'y2':p2.y()/sy})
        scale_line.scale_bar_attachment = ScaleBarAttachment(scale_line, label,
            text_position='above', gap=4, changed=store_reference_position)

    def _browser_display_payload(self, image: dict[str, Any]) -> dict[str, Any] | None:
        payload = image.get("browser_scale_bar")
        if not isinstance(payload, dict):
            return None
        display = payload.get("display") if isinstance(payload.get("display"), dict) else payload
        return display if isinstance(display, dict) else None

    def _browser_display_is_visible(self, image: dict[str, Any]) -> bool:
        display = self._browser_display_payload(image)
        return bool(display and display.get("visible"))

    def _browser_bar_owns_target(self, image: dict[str, Any], target: str | None) -> bool:
        """An imported presentation outranks auto-seeded bars, not explicit edits."""
        if not self._browser_display_is_visible(image):
            return False
        from core.layout_scale_bar import layout_scale_state_for_target
        state = layout_scale_state_for_target(image.get('layout_scale_bars'), target or '')
        return state is None or (bool(state.get('enabled', True)) and not state.get('template'))

    def _shared_horizontal_um_per_px(self, image: dict[str, Any]) -> float:
        from core.measurement_scale import resolve_calibration

        calibration = resolve_calibration(
            dict(image.get("metadata") or {}) if isinstance(image.get("metadata"), dict) else {},
            dict(image.get("measurement_scale_bar") or {}) if isinstance(image.get("measurement_scale_bar"), dict) else {},
        )
        if not calibration.get("calibrated"):
            return 0.0
        try:
            value = float(calibration.get("um_per_px") or calibration.get("um_per_px_x") or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return value if value > 0 else 0.0

    def _browser_display_scale_bar_state(self, image: dict[str, Any]):
        from core.image_browser import ScaleBarState

        display = self._browser_display_payload(image)
        if not display or not display.get("visible"):
            return None
        um_value = self._shared_horizontal_um_per_px(image)
        if um_value <= 0:
            try:
                um_value = float(display.get("um_per_px")) if display.get("um_per_px") not in (None, "") else 0.0
            except (TypeError, ValueError):
                um_value = 0.0
        if um_value <= 0:
            payload = image.get("browser_scale_bar")
            cal = payload.get("calibration") if isinstance(payload, dict) and isinstance(payload.get("calibration"), dict) else {}
            try:
                um_value = float(cal.get("um_per_px") or cal.get("um_per_px_x") or 0.0)
            except (TypeError, ValueError):
                um_value = 0.0
        if um_value <= 0:
            return None
        return ScaleBarState(
            visible=True,
            length_um=float(display.get("length_um") or 50.0),
            um_per_px=um_value,
            center_x_px=float(display.get("center_x_px") or 0.0),
            line_y_px=float(display.get("line_y_px") or 0.0),
            line_color=str(display.get("line_color") or "#FFFFFF"),
            text_color=str(display.get("text_color") or "#FFFFFF"),
            line_thickness_px=float(display.get("line_thickness_px") or 4.0),
            font_family=str(display.get("font_family") or "Arial"),
            font_size_px=float(display.get("font_size_px") or 18.0),
            bold=bool(display.get("bold", False)),
            italic=bool(display.get("italic", False)),
            text_position=str(display.get('text_position') or 'above'),
        )

    def _draw_browser_scale_bar_overlay(self, image: dict[str, Any], data) -> None:
        from core.image_browser import draw_scale_bar

        state = self._browser_display_scale_bar_state(image)
        if state is None or not self._image_items or data is None:
            return
        item = self._image_items[0]
        channel = item.data(1)
        target = (f'merge_split:channel:{channel}' if channel is not None
                  else f"{image.get('layout_mode', 'merge_split')}:merge")
        if self.view.roi_mode == 'scale_line' or not self._browser_bar_owns_target(image, target):
            return
        pixmap = item.pixmap()
        if pixmap.isNull():
            return
        overlay = QImage(pixmap.size(), QImage.Format.Format_ARGB32_Premultiplied)
        overlay.fill(Qt.GlobalColor.transparent)
        painter = QPainter(overlay)
        try:
            source_size = QSize(max(1, int(data.shape[2])), max(1, int(data.shape[1])))
            draw_scale_bar(
                painter,
                QRectF(0.0, 0.0, float(overlay.width()), float(overlay.height())),
                source_size,
                state,
            )
        finally:
            painter.end()
        overlay_item = QGraphicsPixmapItem(QPixmap.fromImage(overlay), item)
        overlay_item.setZValue(110)
        overlay_item.setData(0, "fluoro_browser_scale_bar")
        overlay_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def _draw_layout(self, image: dict[str, Any], data) -> None:
        layout_mode = str(image.get("layout_mode", "merge_split"))
        if layout_mode == "merge_only":
            item = self._add_pixmap(
                self._render_fluoro_qimage(image, data, view_mode="merge"),
                0,
                0,
                "Merge",
                LAYOUT_TARGET_WIDTH,
                layout_image=image,
                layout_key="merge_only:merge",
                label_translation_key='fluoro_canvas_merge',
            )
            self._draw_scale_bar(image, item, layout_key="merge_only:merge", allow_legacy=True)
        elif layout_mode == "batch_grid":
            self._draw_batch_grid()
        elif layout_mode == "custom":
            self._draw_custom_layout()
        else:
            self._draw_split(image, data, layout=True)
        self._draw_layout_image_instances(image, data)
        self._draw_layout_shapes(image)
        self._draw_layout_texts(image)
        self._draw_layout_plots()

    def _draw_layout_image_instances(self, active_image, active_data):
        images = self.panel.images() if active_image.get('layout_mode') in {'custom','batch_grid'} else [active_image]
        for image in images:
            instances = [(key, geometry) for key, geometry in image.get('layout_item_geometries',{}).items()
                         if isinstance(geometry,dict) and geometry.get('source_layout_key') and geometry.get('enabled',True)]
            if not instances:
                continue
            data = active_data if image.get('uid') == active_image.get('uid') else self.panel.image_data(str(image.get('uid')))
            if data is None:
                continue
            channels = self._styled_channels_for_image(image)
            for key, geometry in instances:
                index = geometry.get('source_channel_index')
                channel = next((c for c in channels if c.get('index') == index),None) if index is not None else None
                if index is not None and channel is None:
                    continue
                qimage = self._render_fluoro_qimage(image,data,view_mode='single' if channel is not None else 'merge',channel=channel)
                self._add_pixmap(qimage,geometry['x'],geometry['y'],'',source_channel_index=index,layout_image=image,layout_key=key)

    def _draw_custom_layout(self) -> None:
        if self.panel is None:
            return
        x_offset = 0.0
        y_offset = 0.0
        for index, image in enumerate(self.panel.images()):
            data = self.panel.image_data(str(image.get("uid")))
            if data is None:
                continue
            item = self._add_pixmap(
                self._render_fluoro_qimage(image, data, view_mode="merge"),
                x_offset,
                y_offset,
                str(image.get("source_name", f"Image {index + 1}")),
                260.0,
                layout_image=image,
                layout_key="custom:merge",
            )
            self._draw_scale_bar(image, item, layout_key="custom:merge", allow_legacy=True)
            x_offset += item.boundingRect().width() + 24
            if x_offset > 620:
                x_offset = 0.0
                y_offset += item.boundingRect().height() + 58

    def _draw_batch_grid(self) -> None:
        if self.panel is None:
            return
        x_offset = 0.0
        y_offset = 0.0
        col = 0
        max_row_height = 0.0
        for image in self.panel.images():
            data = self.panel.image_data(str(image.get("uid")))
            if data is None:
                continue
            item = self._add_pixmap(
                self._render_fluoro_qimage(image, data, view_mode="merge"),
                x_offset,
                y_offset,
                str(image.get("source_name", "Image")),
                240.0,
                layout_image=image,
                layout_key="batch_grid:merge",
            )
            self._draw_scale_bar(image, item, layout_key="batch_grid:merge", allow_legacy=True)
            max_row_height = max(max_row_height, item.boundingRect().height() + 44)
            col += 1
            if col >= 2:
                col = 0
                x_offset = 0.0
                y_offset += max_row_height + 16
                max_row_height = 0.0
            else:
                x_offset += item.boundingRect().width() + 18

    def _draw_split(self, image: dict[str, Any], data, layout: bool) -> None:
        channels = [channel for channel in self._styled_channels_for_image(image) if channel.get("visible", True)]
        x_offset = 0.0
        max_height = 0.0
        scale_width = 260.0
        for channel in channels:
            channel_index = int(channel.get("index", 0))
            pixmap_item = self._add_pixmap(
                self._render_fluoro_qimage(image, data, view_mode="single", channel=channel),
                x_offset,
                0,
                str(channel.get("name", trf('fluoro_canvas_channel', index=channel_index+1))),
                scale_width,
                source_channel_index=channel_index,
                layout_image=image if layout else None,
                layout_key=f"merge_split:channel:{channel_index}" if layout else None,
            )
            self._draw_scale_bar(image, pixmap_item, layout_key=f"merge_split:channel:{channel_index}", allow_legacy=False)
            display_rect = pixmap_item.sceneBoundingRect()
            max_height = max(max_height, display_rect.height())
            x_offset += display_rect.width() + 10
        merge = self._add_pixmap(
            self._render_fluoro_qimage(image, data, view_mode="merge"),
            x_offset,
            0,
            "Merge",
            scale_width,
            source_channel_index=None,
            layout_image=image if layout else None,
            layout_key="merge_split:merge" if layout else None,
            label_translation_key='fluoro_canvas_merge',
        )
        merge_key = "merge_split:merge" if layout else f"{image.get('layout_mode', 'merge_split')}:merge"
        self._draw_scale_bar(image, merge, layout_key=merge_key, allow_legacy=layout)
        merge_rect = merge.sceneBoundingRect()
        max_height = max(max_height, merge_rect.height())
        self.scene.setSceneRect(QRectF(0, 0, x_offset + merge_rect.width(), max_height + 35))

    def _add_pixmap(
        self,
        qimage,
        x: float,
        y: float,
        label: str,
        target_width: float | None = None,
        source_channel_index: int | None = None,
        layout_image: dict[str, Any] | None = None,
        layout_key: str | None = None,
        label_translation_key: str | None = None,
    ) -> QGraphicsItem:
        plate_style = self._image_plate_style()
        display_image = qimage
        if layout_image is not None:
            has_layout_bars = any(isinstance(item, dict) for item in layout_image.get("layout_scale_bars", []))
            if not has_layout_bars and self.panel is not None:
                drawer = getattr(self.panel, "_draw_browser_scale_bar_on_image", None)
                if callable(drawer):
                    display_image = drawer(qimage, layout_image)
        pixmap = QPixmap.fromImage(display_image)
        display_width = float(pixmap.width())
        display_height = float(pixmap.height())
        if target_width and pixmap.width() > 0:
            display_width = float(target_width)
            display_height = float(pixmap.height()) * display_width / float(pixmap.width())
        marker = "fluoro_image_channel" if source_channel_index is not None else "fluoro_image_merge"
        if layout_image is not None and layout_key:
            geometry = dict(layout_image.get("layout_item_geometries", {}).get(layout_key, {}))
            item = FluoroLayoutPixmapItem(
                self,
                pixmap,
                marker,
                layout_key,
                float(geometry.get("x", x)),
                float(geometry.get("y", y + 22)),
                float(geometry.get("width", display_width)),
                float(geometry.get("height", display_height)),
                plot_type="layout_image",
            )
            item.setData(2, source_channel_index)
            item.setData(3, str(layout_image.get("uid", "")))
            frame_color = str(plate_style.get("frame_color", "")).strip()
            frame_width = float(plate_style.get("frame_line_width", 0.0) or 0.0)
            if frame_color and frame_width > 0:
                frame_pen = QPen(QColor(frame_color), frame_width)
                frame_pen.setCosmetic(True)
                item.setPen(frame_pen)
            self.scene.addItem(item)
            item.setVisible(bool(geometry.get('enabled',True)))
        else:
            pixmap_item = self.scene.addPixmap(pixmap)
            pixmap_item.setPos(x, y + 22)
            if pixmap.width() > 0:
                pixmap_item.setScale(display_width / float(pixmap.width()))
            pixmap_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            pixmap_item.setFlag(pixmap_item.GraphicsItemFlag.ItemIsMovable, True)
            pixmap_item.setFlag(pixmap_item.GraphicsItemFlag.ItemIsSelectable, True)
            pixmap_item.setFlag(pixmap_item.GraphicsItemFlag.ItemSendsGeometryChanges, True)
            pixmap_item.setData(0, marker)
            pixmap_item.setData(1, source_channel_index)
            pixmap_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
            self._image_items.append(pixmap_item)
            item = pixmap_item
        label = tr(label_translation_key) if label_translation_key else label
        text = EditableMovableTextItem(label, _noop_text, _noop_move, _noop_style, ("fluoro_channel", 0))
        text.setData(1000, label_translation_key)
        text.setData(1001, label)
        text.setDefaultTextColor(QColor(str(plate_style.get("label_color", "#FFFFFF"))))
        text.setFont(QFont(str(plate_style.get("label_font_family", "Arial")), int(float(plate_style.get("label_font_size_pt", 8)))))
        text.setParentItem(item)
        if layout_image is None and target_width and pixmap.width() > 0:
            # Children inherit the image transform.  Counter-scale labels so
            # their on-screen type size stays stable while source pixels do not
            # get destructively resampled.
            display_scale = display_width / float(pixmap.width())
            text.setScale(1.0 / display_scale)
            text.setPos(4.0 / display_scale, 4.0 / display_scale)
        else:
            text.setPos(4, 4)
        text.setZValue(100)
        return item

    def _image_plate_style(self) -> dict[str, Any]:
        preset_name = str(self.panel.style.get("preset", "Nature")) if self.panel is not None else "Nature"
        return resolve_image_plate_style(preset_name)

    def _render_fluoro_qimage(self, image: dict[str, Any], data, *, view_mode: str | None = None, channel: dict[str, Any] | None = None):
        return render_fluorescence_qimage(
            data,
            image,
            self._styled_channels_for_image(image),
            view_mode=view_mode,
            channel=channel,
            merge_fn=render_merge_qimage,
            single_fn=render_single_qimage,
        )

    def _styled_channels_for_image(self, image: dict[str, Any]) -> list[dict[str, Any]]:
        channels = list(image.get("channels", []))
        if rgb_interpretation_mode(image) in {RGB_COMPOSITE_MODE, FORCED_RGB_SPLIT_MODE}:
            return [dict(channel) for channel in channels]
        return self._styled_channels(channels)

    def _styled_channels(self, channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if self.panel is not None:
            return self.panel._styled_channels_for_render(channels)
        return [dict(channel) for channel in channels]

    def _draw_layout_plots(self) -> None:
        if self.panel is None:
            return
        records = self._layout_plot_records()
        if not records:
            return
        bounds = self.scene.itemsBoundingRect()
        default_x = bounds.right() + 28.0 if not bounds.isNull() else 0.0
        default_y = bounds.top() + 22.0 if not bounds.isNull() else 22.0
        for index, record in enumerate(records):
            if not record.get("enabled", True):
                continue
            width = float(record.get("width_px", 300))
            height = float(record.get("height_px", 220))
            column = index % 2
            row = index // 2
            x = float(record.get("x", default_x + column * (width + 24.0)))
            y = float(record.get("y", default_y + row * (height + 24.0)))
            svg = self.render_layout_plot_record_svg(record, width, height, include_svg_text_overrides=False)
            if not svg:
                continue
            pixmap_item = FluoroLayoutPixmapItem(
                self,
                svg,
                self._plot_item_marker(str(record.get("plot_type", "statistics"))),
                str(record.get("plot_uuid", "")),
                x,
                y,
                width,
                height,
                plot_type=str(record.get("plot_type", "statistics")),
                svg_text_overrides=dict(record.get("svg_text_overrides", {})),
                svg_text_changed_callback=lambda text_index, text, plot_uuid=str(record.get("plot_uuid", "")): self._update_layout_plot_svg_text(plot_uuid, text_index, text),
            )
            pixmap_item.setData(3, dict(record))
            self.scene.addItem(pixmap_item)
            pixmap_item._rebuild_svg_text_items()

    def _layout_plot_records(self) -> list[dict[str, Any]]:
        if self.panel is None:
            return []
        records = [dict(plot) for plot in self.panel.analysis.get("fluoro_layout_plots", []) if isinstance(plot, dict)]
        if records:
            return records
        legacy = dict(self.panel.analysis.get("fluoro_stats_plot", {}))
        result = dict(self.panel.analysis.get("fluoro_statistics", {}))
        if legacy.get("enabled") and result.get("plot_data"):
            legacy.setdefault("plot_uuid", "legacy_fluoro_stats_plot")
            legacy.setdefault("plot_type", "statistics")
            legacy.setdefault("statistics_result", result)
            legacy.setdefault("metric", result.get("metric", ""))
            legacy.setdefault("source", result.get("source", ""))
            return [legacy]
        return []

    def _update_layout_plot_svg_text(self, plot_uuid: str, text_index: int, text: str | dict[str, float | str]) -> None:
        if self.panel is None:
            return
        plots = [plot for plot in self.panel.analysis.get("fluoro_layout_plots", []) if isinstance(plot, dict)]
        for plot in plots:
            if str(plot.get("plot_uuid", "")) != str(plot_uuid):
                continue
            if isinstance(text, dict):
                plot["svg_text_overrides"] = update_svg_text_overrides(
                    plot.get("svg_text_overrides", {}),
                    int(text_index),
                    text=str(text.get("text", "")) if text.get("text") is not None else None,
                    x=float(text["x"]) if text.get("x") is not None else None,
                    y=float(text["y"]) if text.get("y") is not None else None,
                    style=text.get("style"),
                    text_payload=text.get("text_payload"),
                )
            else:
                plot["svg_text_overrides"] = update_svg_text_overrides(
                    plot.get("svg_text_overrides", {}),
                    int(text_index),
                    text=str(text),
                )
            break
        self.panel.append_audit("update_fluorescence_plot_svg_text", f"{plot_uuid}:{text_index}")

    def _plot_item_marker(self, plot_type: str) -> str:
        return {
            "statistics": "fluoro_stats_plot",
            "frequency_bar": "fluoro_stats_plot",
            "frequency_line": "fluoro_stats_plot",
            "line_profile": "fluoro_profile_plot",
            "colocalization": "fluoro_coloc_plot",
        }.get(plot_type, "fluoro_stats_plot")

    def render_layout_plot_record_qimage(self, record: dict[str, Any], width_pt: float, height_pt: float, *, dpi: int) -> QImage:
        """Render a layout plot at export resolution using point-sized bounds."""
        return self._render_layout_plot_image(
            record,
            width_pt,
            height_pt,
            dpi=dpi,
            use_cache=False,
            units_per_inch=72.0,
        )

    def render_layout_plot_record_svg(
        self,
        record: dict[str, Any],
        width_pt: float,
        height_pt: float,
        *,
        include_svg_text_overrides: bool = True,
    ) -> bytes:
        """Render an editable statistics plot at its Layout point dimensions."""
        from matplotlib import rc_context
        try:
            figure = self._build_layout_plot_figure(record)
            figure.set_size_inches(width_pt / 72.0, height_pt / 72.0)
            buffer = BytesIO()
            with rc_context({"svg.fonttype": "none"}):
                figure.savefig(buffer, format="svg", bbox_inches=None, pad_inches=0.0, facecolor="white")
            svg_bytes = buffer.getvalue()
            if include_svg_text_overrides:
                return apply_svg_text_overrides(svg_bytes, dict(record.get("svg_text_overrides", {})))
            return svg_bytes
        except Exception:
            return b""

    def _render_layout_plot_image(
        self,
        record: dict[str, Any],
        width: float,
        height: float,
        *,
        dpi: int = 100,
        use_cache: bool = True,
        units_per_inch: float | None = None,
    ) -> QImage:
        try:
            render_dpi = max(72, int(dpi))
            logical_units_per_inch = float(units_per_inch or render_dpi)
            cache_payload = {
                "plot_type": record.get("plot_type", "statistics"),
                "plot_uuid": record.get("plot_uuid", ""),
                "chart_kind": record.get("chart_kind"),
                "width": int(round(width)),
                "height": int(round(height)),
                "dpi": render_dpi,
                "units_per_inch": logical_units_per_inch,
                "style": record.get("style", {}),
                "rows": record.get("rows", []),
                "statistics_result": record.get("statistics_result", {}),
                "distribution_result": record.get("distribution_result", {}),
                "preset": self.panel.style.get("preset", "Nature") if self.panel is not None else "Nature",
                "runtime_style_cache_token": runtime_style_cache_token(),
            }
            cache_key = json.dumps(cache_payload, sort_keys=True, default=str)
            if use_cache and cache_key in self._layout_plot_cache:
                return QImage(self._layout_plot_cache[cache_key])
            figure = self._build_layout_plot_figure(record)
            figure.set_size_inches(width / logical_units_per_inch, height / logical_units_per_inch)
            image = self._figure_to_qimage(figure, render_dpi)
            if use_cache and not image.isNull():
                self._layout_plot_cache[cache_key] = QImage(image)
                if len(self._layout_plot_cache) > 64:
                    self._layout_plot_cache.pop(next(iter(self._layout_plot_cache)))
            return image
        except Exception:
            return QImage()

    def _build_layout_plot_figure(self, record: dict[str, Any]) -> Figure:
        plot_type = str(record.get("plot_type", "statistics"))
        style = resolve_statistics_plot_style(
            str(self.panel.style.get("preset", "Nature")) if self.panel is not None else "Nature",
            saved_style=dict(record.get("style", {})),
        )
        if plot_type == "line_profile":
            return self._build_line_profile_figure(list(record.get("rows", [])), style)
        if plot_type == "colocalization":
            return self._build_colocalization_figure(list(record.get("rows", [])), style)
        if plot_type in {"frequency_bar", "frequency_line"}:
            distribution_result = dict(record.get("distribution_result", {}))
            return build_distribution_comparison_figure(
                distribution_result,
                style=style,
                plot_kind="bar" if plot_type == "frequency_bar" else "line",
                x_label=f"Cell area ({display_unit(str(distribution_result.get('unit', 'px^2')))})",
                frequency_mode=str(distribution_result.get("frequency_mode", "absolute")),
                chart_kind=record.get("chart_kind"),
            )
        result = dict(record.get("statistics_result", {}))
        return build_group_statistics_figure(result, style=style, y_label=self._stats_axis_label(result), chart_kind=record.get("chart_kind"))

    def _figure_to_qimage(self, figure: Figure, dpi: int) -> QImage:
        buffer = BytesIO()
        figure.savefig(buffer, format="png", dpi=dpi, bbox_inches=None, pad_inches=0.0, facecolor="white")
        image = QImage()
        image.loadFromData(buffer.getvalue(), "PNG")
        return image

    def _build_line_profile_figure(self, rows: list[dict[str, Any]], style: dict[str, Any]) -> Figure:
        from matplotlib.figure import Figure
        if not rows:
            raise ValueError("No line profile rows available.")
        figure = Figure(figsize=(3.4, 2.4), dpi=180, facecolor="white")
        figure.subplots_adjust(left=0.24, right=0.92, bottom=0.24, top=0.88)
        ax = figure.add_subplot(111)
        colors = ["#22D7E6", "#FF2AD4", "#FFFFFF", "#FFD13B", "#00D26A"]
        axis_unit = "px"
        for index, row in enumerate(rows):
            distances, row_unit = _line_profile_distance_series(row)
            values = list(row.get("intensity", []) or [])
            if distances and values:
                if row_unit == "um":
                    axis_unit = "um"
                ax.plot(distances, values, color=colors[index % len(colors)], linewidth=float(style.get("line_width", 1.2)), label=str(row.get("channel_name", f"Ch{index + 1}")))
        self._style_simple_axes(ax, style, f"Distance ({axis_unit})", "Intensity (a.u.)")
        if len(rows) > 1:
            ax.legend(frameon=False, fontsize=float(style.get("legend_size_pt", 7.0)))
        return figure

    def _build_colocalization_figure(self, rows: list[dict[str, Any]], style: dict[str, Any]) -> Figure:
        from matplotlib.figure import Figure
        if not rows:
            raise ValueError("No colocalization rows available.")
        figure = Figure(figsize=(3.4, 2.4), dpi=180, facecolor="white")
        figure.subplots_adjust(left=0.24, right=0.96, bottom=0.32, top=0.88)
        ax = figure.add_subplot(111)
        labels = [f"{row.get('base_channel_name', '')}/{row.get('compare_channel_name', '')}" for row in rows]
        values = [float(row.get("overlap_percent_of_base", 0.0)) for row in rows]
        x = np.arange(len(values), dtype=float)
        ax.bar(x, values, color=str(style.get("bar_fill_color", "#FFFFFF")), edgecolor=str(style.get("bar_edge_color", "#272727")), linewidth=float(style.get("bar_line_width", 0.9)))
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=35, ha="right", fontsize=float(style.get("tick_label_size_pt", 7.0)))
        self._style_simple_axes(ax, style, "", "Overlap (% base)")
        return figure

    def _style_simple_axes(self, ax, style: dict[str, Any], x_label: str, y_label: str) -> None:
        font_family = str(style.get("font_family", "Arial"))
        font_size = float(style.get("font_size_pt", 7.0))
        axis_line_width = float(style.get("axis_line_width", 0.6))
        if x_label:
            ax.set_xlabel(x_label, fontsize=font_size + 1, fontfamily=font_family)
        ax.set_ylabel(y_label, fontsize=font_size + 1, fontfamily=font_family)
        ax.tick_params(axis="both", labelsize=font_size, width=axis_line_width, length=3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_linewidth(axis_line_width)
        ax.spines["bottom"].set_linewidth(axis_line_width)

    def _draw_layout_shapes(self, image: dict[str, Any]) -> None:
        for shape in image.get("layout_shapes", []):
            if not isinstance(shape, dict):
                continue
            self.add_layout_shape(
                str(shape.get("shape_type", "rectangle")),
                stroke_width=float(shape.get("stroke_width", 2.0)),
                stroke_color=str(shape.get("stroke_color", "#FFFFFF")),
                fill_color=str(shape.get("fill_color", "#FFFFFF")),
                x=float(shape.get("x", 40.0)),
                y=float(shape.get("y", 40.0)),
                width=float(shape.get("width", 120.0)),
                height=float(shape.get("height", 70.0)),
                shape_uuid=str(shape.get("shape_uuid", "")),
                record_snapshot=False,
                record=shape,
            )

    def _draw_layout_texts(self, image: dict[str, Any]) -> None:
        for text in image.get("layout_texts", []):
            if not isinstance(text, dict):
                continue
            self.add_layout_text(
                str(text.get("text", "Text")),
                text_uuid=str(text.get("text_uuid", "")),
                x=float(text.get("x", 20.0)),
                y=float(text.get("y", 20.0)),
                font_family=str(text.get("font_family", "Arial")),
                font_size=float(text.get("font_size", 10.0)),
                bold=bool(text.get("bold", False)),
                italic=bool(text.get("italic", False)),
                color=str(text.get("color", "#FFFFFF")),
                select=False,
                record=text,
            )

    def add_layout_shape(self, shape_type, stroke_width=2.0, stroke_color="#FFFFFF",
                         fill_color="#FFFFFF", x=None, y=None, width=120.0, height=70.0,
                         shape_uuid="", record_snapshot=True, record=None):
        if record_snapshot:self._record_layout_snapshot()
        image = self.panel.active_image() if self.panel is not None else None
        image_uid = str(image.get("uid","")) if image else ""
        bounds = self.scene.itemsBoundingRect()
        data = dict(record) if record is not None else {
            "shape_type":shape_type.lower(), "stroke_width":stroke_width,
            "stroke_color":stroke_color, "fill_color":fill_color, "fill_alpha":80/255,
            "x":float(x if x is not None else bounds.left()+40),
            "y":float(y if y is not None else bounds.top()+40), "width":width,"height":height}
        uid = str(shape_uuid or data.get("shape_uuid") or uuid4().hex)
        data["shape_uuid"] = uid
        data.setdefault('fill_alpha',80/255)
        if data.get('shape_type')=='line':
            data.setdefault('y1',0.0);data.setdefault('y2',0.0)
        obj = shape_from_record(data,f"fluoro_shape:{image_uid}:{uid}",
            {"fluoro_kind":"layout_shape","image_uid":image_uid,"shape_uuid":uid,"shape":data})
        item = SharedShapeItem(obj)
        item.setData(0,"fluoro_layout_shape"); item.setData(1,uid)
        self.scene.addItem(item)
        item._geometry_changed_callback = self._persist_shared_annotation
        item._style_changed_callback = self._persist_shared_annotation
        if record_snapshot:self._persist_shared_annotation(item)
        return item

    def apply_shape_style_to_selection(self, stroke_width, stroke_color, fill_color):
        self._record_layout_snapshot()
        apply_selection_style(self.scene.selectedItems(), {"stroke_width":stroke_width,"stroke_color":stroke_color,"fill_color":fill_color})

    def _selected_layout_items(self, markers: set[str] | None = None) -> list[QGraphicsItem]:
        allowed = markers or FLUOR_LAYOUT_ARRANGE_MARKERS
        return [item for item in self.scene.selectedItems() if str(item.data(0) or "") in allowed]

    def _layout_record_for_item(self, item: QGraphicsItem) -> EditableLayoutRecord:
        marker = str(item.data(0) or "")
        uid = str(item.data(1) or "")
        width, height = self._layout_item_size(item)
        return EditableLayoutRecord(
            kind=marker,
            uid=uid,
            model=item,
            x=float(item.pos().x()),
            y=float(item.pos().y()),
            width=float(width),
            height=float(height),
            locked=False,
            can_resize=width > 0 and height > 0,
        )

    def _layout_records_for_items(self, items: list[QGraphicsItem]) -> list[EditableLayoutRecord]:
        return [self._layout_record_for_item(item) for item in items]

    def _commit_layout_records_to_items(self, records: list[EditableLayoutRecord], resize: bool = False) -> None:
        for record in records:
            item = record.model
            if not isinstance(item, QGraphicsItem):
                continue
            item.setPos(float(record.x), float(record.y))
            if resize:
                self._resize_layout_item(item, float(record.width), float(record.height))

    def align_selected_layout_items(self, axis: str) -> None:
        selected = self._selected_layout_items()
        if len(selected) < 2:
            return
        self._record_layout_snapshot()
        records = self._layout_records_for_items(selected)
        align_records(records, "hcenter" if axis == "column" else "vcenter", reference_mode="first")
        self._commit_layout_records_to_items(records)

    def make_selected_layout_items_same_size(self) -> None:
        selected = [item for item in self._selected_layout_items() if self._layout_item_size(item)[0] > 0 and self._layout_item_size(item)[1] > 0]
        if len(selected) < 2:
            return
        self._record_layout_snapshot()
        records = self._layout_records_for_items(selected)
        make_records_same_size(records, reference_mode="largest")
        self._commit_layout_records_to_items(records, resize=True)

    def distribute_selected_layout_items(self, orientation: str) -> None:
        selected = self._selected_layout_items()
        if len(selected) < 3:
            return
        self._record_layout_snapshot()
        records = self._layout_records_for_items(selected)
        distribute_records(records, orientation)
        self._commit_layout_records_to_items(records)

    def delete_selected_layout_items(self) -> None:
        selected = self._selected_layout_items(FLUOR_LAYOUT_DELETE_MARKERS)
        selected_scale_bar_items = [
            item
            for item in self.scene.items()
            if item.isSelected() and item.data(0) == "fluoro_scale_bar" and str(item.data(1) or "")
        ]
        if selected_scale_bar_items:
            selected.extend(item for item in selected_scale_bar_items if item not in selected)
        if not selected:
            return
        self._record_layout_snapshot()
        plot_uids = {str(item.data(1) or "") for item in selected if str(item.data(0) or "") in FLUOR_LAYOUT_PLOT_MARKERS}
        shape_uids = {str(item.data(1) or "") for item in selected if item.data(0) == "fluoro_layout_shape"}
        text_uids = {str(item.data(1) or "") for item in selected if item.data(0) == "fluoro_layout_text"}
        scale_bar_targets = {
            (str(item.data(2) or ""), str(item.data(1) or ""))
            for item in selected
            if item.data(0) == "fluoro_scale_bar" and str(item.data(1) or "")
        }
        if self.panel is not None:
            for item in selected:
                if item.data(0) not in {'fluoro_image_channel','fluoro_image_merge'}:
                    continue
                image_record=next((i for i in self.panel.images() if i.get('uid')==item.data(3)),None)
                if image_record is not None:
                    geometry=image_record.setdefault('layout_item_geometries',{}).setdefault(str(item.data(1)),{})
                    geometry.update(x=item.pos().x(),y=item.pos().y(),width=item.rect().width(),height=item.rect().height(),enabled=False)
            if plot_uids:
                self.panel.analysis["fluoro_layout_plots"] = [
                    plot for plot in self.panel.analysis.get("fluoro_layout_plots", []) if str(plot.get("plot_uuid", "")) not in plot_uids
                ]
                latest = dict(self.panel.analysis.get("fluoro_stats_plot", {}))
                if str(latest.get("plot_uuid", "")) in plot_uids:
                    self.panel.analysis.pop("fluoro_stats_plot", None)
            image = self.panel.active_image()
            if image is not None:
                if shape_uids:
                    image["layout_shapes"] = [shape for shape in image.get("layout_shapes", []) if str(shape.get("shape_uuid", "")) not in shape_uids]
                if text_uids:
                    image["layout_texts"] = [text for text in image.get("layout_texts", []) if str(text.get("text_uuid", "")) not in text_uids]
            if scale_bar_targets:
                for image_record in self.panel.images():
                    image_uid = str(image_record.get("uid", ""))
                    target_ids = {target_id for uid, target_id in scale_bar_targets if not uid or uid == image_uid}
                    if not target_ids:
                        continue
                    states = [dict(item) for item in image_record.get("layout_scale_bars", []) if isinstance(item, dict)]
                    by_target = {str(item.get("target_id", "")): item for item in states}
                    for target_id in target_ids:
                        state = dict(by_target.get(target_id, {"target_id": target_id}))
                        state["enabled"] = False
                        by_target[target_id] = state
                    image_record["layout_scale_bars"] = list(by_target.values())
        self.refresh()

    def _layout_item_size(self, item: QGraphicsItem) -> tuple[float, float]:
        if isinstance(item, FluoroLayoutPixmapItem):
            rect = item.rect()
            return float(rect.width()), float(rect.height())
        if isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            rect = item.rect()
            return float(rect.width()), float(rect.height())
        if isinstance(item, QGraphicsLineItem):
            line = item.line()
            return float(abs(line.x2() - line.x1())), max(1.0, float(item.pen().widthF()))
        rect = item.sceneBoundingRect()
        return float(rect.width()), float(rect.height())

    def _resize_layout_item(self, item: QGraphicsItem, width: float, height: float) -> None:
        if isinstance(item, FluoroLayoutPixmapItem):
            item.apply_geometry(item.pos().x(), item.pos().y(), width, height)
        elif isinstance(item, QGraphicsLineItem):
            item.setLine(0.0, 0.0, float(width), 0.0)
        elif isinstance(item, (QGraphicsRectItem, QGraphicsEllipseItem)):
            item.setRect(0.0, 0.0, float(width), float(height))

    def _capture_layout_snapshot(self) -> dict[str, Any] | None:
        if self.panel is None:
            return None
        from ui.layout_adapters.fluorescence_layout_adapter import fluorescence_layout_from_layout_objects

        layout_objects = self.layout_objects()
        restored = fluorescence_layout_from_layout_objects(layout_objects) if layout_objects else {}
        images_layout = dict(restored.get("images_layout", {}))
        image_snapshots: dict[str, dict[str, Any]] = {}
        for image in self.panel.images():
            image_uid = str(image.get("uid", ""))
            if not image_uid:
                continue
            snapshot = {
                key: deepcopy(image[key])
                for key in FLUOR_LAYOUT_SNAPSHOT_IMAGE_KEYS
                if key in image
            }
            for key, value in dict(images_layout.get(image_uid, {})).items():
                if key in FLUOR_LAYOUT_SNAPSHOT_IMAGE_KEYS:
                    snapshot[key] = deepcopy(value)
            image_snapshots[image_uid] = snapshot
        analysis_snapshot = {
            key: deepcopy(self.panel.analysis[key])
            for key in FLUOR_LAYOUT_SNAPSHOT_ANALYSIS_KEYS
            if key in self.panel.analysis
        }
        if restored.get("fluoro_layout_plots_key_present") or restored.get("fluoro_layout_plots"):
            analysis_snapshot["fluoro_layout_plots"] = deepcopy(restored.get("fluoro_layout_plots", []))
        return {"images": image_snapshots, "analysis": analysis_snapshot,
                "editor_state":deepcopy(self.panel.data.get('fluoro_layout',{}))}

    def _restore_layout_snapshot(self, snapshot: dict[str, Any]) -> None:
        if self.panel is None:
            return
        self.panel.data['fluoro_layout']=deepcopy(snapshot.get('editor_state',{}))
        image_snapshots = dict(snapshot.get("images", {}))
        images = self.panel.images()
        for image in images:
            image_uid = str(image.get("uid", ""))
            if image_uid not in image_snapshots:
                continue
            state = dict(image_snapshots.get(image_uid, {}))
            for key in FLUOR_LAYOUT_SNAPSHOT_IMAGE_KEYS:
                if key in state:
                    image[key] = deepcopy(state[key])
                else:
                    image.pop(key, None)
        self.panel.data["fluorescence_images"] = images
        analysis_snapshot = dict(snapshot.get("analysis", {}))
        for key in FLUOR_LAYOUT_SNAPSHOT_ANALYSIS_KEYS:
            if key in analysis_snapshot:
                self.panel.analysis[key] = deepcopy(analysis_snapshot[key])
            else:
                self.panel.analysis.pop(key, None)
        self.refresh()

    def _record_layout_snapshot(self) -> None:
        if getattr(self, '_layout_edit_transaction', False):
            return
        snapshot = self._capture_layout_snapshot()
        if snapshot is not None:
            self._layout_undo_stack.append(snapshot)
            self._layout_redo_stack.clear()

    def undo_layout_edit(self) -> None:
        if not self._layout_undo_stack:
            return
        snapshot = self._layout_undo_stack.pop()
        current = self._capture_layout_snapshot()
        if current is not None:
            self._layout_redo_stack.append(current)
        self._restore_layout_snapshot(snapshot)

    def redo_layout_edit(self) -> None:
        if not self._layout_redo_stack:
            return
        snapshot = self._layout_redo_stack.pop()
        current = self._capture_layout_snapshot()
        if current is not None:
            self._layout_undo_stack.append(current)
        self._restore_layout_snapshot(snapshot)

    def layout_plot_items(self) -> list[QGraphicsItem]:
        return [
            item
            for item in self.scene.items()
            if str(item.data(0)) in FLUOR_LAYOUT_PLOT_MARKERS
        ]

    def layout_item_positions(self) -> dict[str, dict[str, dict[str, float]]]:
        positions = {"plots": {}, "shapes": {}, "texts": {}, "images": {}}
        for item in self.scene.items():
            marker = str(item.data(0) or "")
            uid = str(item.data(1) or "")
            if not uid:
                continue
            width, height = self._layout_item_size(item)
            if marker in FLUOR_LAYOUT_PLOT_MARKERS:
                positions["plots"][uid] = {"x": float(item.pos().x()), "y": float(item.pos().y()), "width_px": int(round(width)), "height_px": int(round(height))}
            elif marker == "fluoro_layout_shape":
                pen = item.pen() if hasattr(item, "pen") else QPen(QColor("#FFFFFF"), 2.0)
                brush = item.brush() if hasattr(item, "brush") else QBrush(QColor("#FFFFFF"))
                positions["shapes"][uid] = {
                    "x": float(item.pos().x()),
                    "y": float(item.pos().y()),
                    "width": float(width),
                    "height": float(height),
                    "stroke_width": float(pen.widthF()),
                    "stroke_color": pen.color().name().upper(),
                    "fill_color": brush.color().name().upper(),
                }
            elif marker == "fluoro_layout_text" and isinstance(item, QGraphicsTextItem):
                font = item.font()
                positions["texts"][uid] = {
                    "x": float(item.pos().x()),
                    "y": float(item.pos().y()),
                    "text": item.toPlainText(),
                    "font_family": font.family(),
                    "font_size": float(font.pointSizeF() if font.pointSizeF() > 0 else font.pointSize()),
                    "bold": bool(font.bold()),
                    "italic": bool(font.italic()),
                    "color": item.defaultTextColor().name().upper(),
                }
            elif marker in {"fluoro_image_channel", "fluoro_image_merge"}:
                image_uid = str(item.data(3) or "")
                if image_uid:
                    positions["images"].setdefault(image_uid, {})[uid] = {
                        "x": float(item.pos().x()),
                        "y": float(item.pos().y()),
                        "width": float(width),
                        "height": float(height),
                    }
        return positions

    def _stats_axis_label(self, result: dict) -> str:
        metric = str(result.get("metric", "value"))
        return {
            "mean": "Mean intensity",
            "integrated_density": "Integrated density",
            "area_px": "Area",
            "count": "Count",
            "mean_object_area_px": "Mean area",
            "mean_intensity": "Mean intensity",
        }.get(metric, metric.replace("_", " ").title())

    def show_measurement_overlay(self, measurement: dict[str, Any]) -> None:
        self.clear_measurement_overlay()
        geometry = measurement.get("roi_or_geometry", {}).get("coordinates_px", {})
        roi_type = str(measurement.get("roi_or_geometry", {}).get("type", "rectangle"))
        channel_index = self._measurement_channel_index(measurement)
        item = self._item_for_channel(channel_index)
        if item is None:
            return
        mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
        pen = QPen(QColor("#FFD13B"), 2.4)
        pen.setCosmetic(True)
        overlay = None
        if roi_type in {"rectangle", "square", "sample_spot"}:
            x = float(geometry.get("x", 0.0))
            y = float(geometry.get("y", 0.0))
            w = float(geometry.get("w", 0.0))
            h = float(geometry.get("h", 0.0))
            top_left = mapper.image_px_to_local_pos((x, y))
            bottom_right = mapper.image_px_to_local_pos((x + w, y + h))
            overlay = QGraphicsRectItem(QRectF(top_left, bottom_right).normalized())
        elif roi_type in {"ellipse", "circle"}:
            cx = float(geometry.get("cx", 0.0))
            cy = float(geometry.get("cy", 0.0))
            rx = float(geometry.get("rx", 0.0))
            ry = float(geometry.get("ry", 0.0))
            top_left = mapper.image_px_to_local_pos((cx - rx, cy - ry))
            bottom_right = mapper.image_px_to_local_pos((cx + rx, cy + ry))
            overlay = QGraphicsEllipseItem(QRectF(top_left, bottom_right).normalized())
        elif roi_type in {"polygon", "freehand"}:
            points = list(geometry.get("points", []))
            if points:
                path = QPainterPath(mapper.image_px_to_local_pos(tuple(points[0])))
                for point in points[1:]:
                    path.lineTo(mapper.image_px_to_local_pos(tuple(point)))
                path.closeSubpath()
                overlay = QGraphicsPathItem(path)
        if overlay is None:
            return
        overlay.setPen(pen)
        overlay.setZValue(500)
        overlay.setData(0, "fluoro_measurement_overlay")
        overlay.setData(1, measurement.get("measurement_uuid"))
        overlay.setParentItem(item)
        overlay.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self._measurement_overlay_item = overlay
        self._measurement_overlay_items = [overlay]
        self.active_measurement_overlay_uuid = str(measurement.get("measurement_uuid"))
        self.measurement_overlay_changed.emit(True)

    def show_profile_overlay(self, result):
        self.clear_measurement_overlay()
        item=self._item_for_channel(int(result.get('channel_index',0)))
        if item is None:return
        mapper=ImageCoordinateMapper(item,self._analysis_image_shape())
        line=result['line']
        start=mapper.image_px_to_local_pos((line['x1'],line['y1']))
        end=mapper.image_px_to_local_pos((line['x2'],line['y2']))
        overlay=QGraphicsLineItem(start.x(),start.y(),end.x(),end.y(),item)
        pen=QPen(QColor('#FFD13B'),2.4);pen.setCosmetic(True);overlay.setPen(pen)
        self._measurement_overlay_items=[overlay];self._measurement_overlay_item=overlay
        self.active_measurement_overlay_uuid='line_profile:'+result['measurement_uuid']
        self.set_measurement_overlay_visible(True);self.measurement_overlay_changed.emit(True)

    def show_count_overlay(self, result: dict[str, Any]) -> None:
        self.clear_measurement_overlay()
        channel_index = int(result.get("channel_index", 0))
        item = self._item_for_channel(channel_index)
        if item is None:
            return
        mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
        overlays: list[QGraphicsItem] = []
        sample_geometry = dict(result.get("roi_or_geometry", {}).get("coordinates_px", {}))
        if sample_geometry:
            sample = self._rect_overlay_from_image_geometry(mapper, sample_geometry, QColor("#d97757"), "fluoro_count_sample_overlay")
            overlays.append(sample)
        for spot in result.get("detected_spots", []):
            if spot.get('review_contours_px'):
                spot_item = self._ai_object_overlay_item(mapper, dict(spot), QColor('#FFD13B'))
                spot_item.setData(0, 'fluoro_count_spot_overlay')
            else:
                spot_item = self._rect_overlay_from_image_geometry(mapper, dict(spot), QColor("#FFD13B"), "fluoro_count_spot_overlay")
            overlays.append(spot_item)
        self._measurement_overlay_items = overlays
        self._measurement_overlay_item = overlays[0] if overlays else None
        self.active_measurement_overlay_uuid = str(result.get("count_uuid", ""))
        self.set_measurement_overlay_visible(True)
        self.measurement_overlay_changed.emit(bool(overlays))

    def show_ai_assisted_overlay(self, records: list[dict[str, Any]]) -> None:
        self.clear_measurement_overlay()
        overlays: list[QGraphicsItem] = []
        color_cycle = [
            QColor("#FFD13B"),
            QColor("#22D7E6"),
            QColor("#FF2AD4"),
            QColor("#00D26A"),
            QColor("#FF3333"),
        ]
        for record_index, record in enumerate(records):
            channel_index = int(record.get("channel_index", 0))
            item = self._item_for_channel(channel_index)
            if item is None:
                continue
            mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
            color = color_cycle[record_index % len(color_cycle)]
            for detected_object in record.get("object_table", []):
                if not isinstance(detected_object, dict):
                    continue
                object_item = self._ai_object_overlay_item(mapper, detected_object, QColor(detected_object.get('overlay_color', color)))
                object_item.setData(1, record.get("ai_measurement_uuid", ""))
                object_item.setData(2, detected_object.get("object_id", ""))
                overlays.append(object_item)
                # Muscle-tube metrics use source-pixel geometry just like the
                # outline, so zooming or moving its parent image stays aligned.
                for points,marker,line_color in (
                    ([detected_object.get('width_start_px'),detected_object.get('width_end_px')]
                     if detected_object.get('max_width_px') is not None else [],'fluoro_myotube_width_overlay',QColor('#00F0FF')),
                    (detected_object.get('centerline_points_px',[]),'fluoro_myotube_length_overlay',QColor('#FF46DC'))):
                    if len(points)<2:continue
                    path=QPainterPath(mapper.image_px_to_local_pos(tuple(points[0])))
                    for point in points[1:]:path.lineTo(mapper.image_px_to_local_pos(tuple(point)))
                    line_item=QGraphicsPathItem(path,mapper.pixmap_item)
                    line_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                    pen=QPen(line_color,2.);pen.setCosmetic(True);line_item.setPen(pen)
                    line_item.setZValue(511);line_item.setData(0,marker)
                    line_item.setData(1,record.get('ai_measurement_uuid',''))
                    line_item.setData(2,detected_object.get('object_id',''))
                    overlays.append(line_item)
        self._measurement_overlay_items = overlays
        self._measurement_overlay_item = overlays[0] if overlays else None
        self.active_measurement_overlay_uuid = "fluorescence_ai_assisted" if overlays else None
        self.set_measurement_overlay_visible(True)
        self.measurement_overlay_changed.emit(bool(overlays))

    def _ai_object_overlay_item(self, mapper: ImageCoordinateMapper, detected_object: dict[str, Any], color: QColor) -> QGraphicsItem:
        if detected_object.get('review_contours_px'):
            path = QPainterPath()
            for points in detected_object['review_contours_px']:
                if len(points) < 3:
                    continue
                path.moveTo(mapper.image_px_to_local_pos(tuple(points[0])))
                for point in points[1:]:
                    path.lineTo(mapper.image_px_to_local_pos(tuple(point)))
                path.closeSubpath()
            item = QGraphicsPathItem(path, mapper.pixmap_item)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            pen = QPen(color, 2.0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(510)
            item.setData(0, 'fluoro_ai_object_overlay')
            return item
        contour_points = [point for point in detected_object.get("contour_points_px", []) if isinstance(point, dict)]
        if len(contour_points) >= 3:
            first = contour_points[0]
            path = QPainterPath(mapper.image_px_to_local_pos((float(first.get("x", 0.0)), float(first.get("y", 0.0)))))
            for point in contour_points[1:]:
                path.lineTo(mapper.image_px_to_local_pos((float(point.get("x", 0.0)), float(point.get("y", 0.0)))))
            path.closeSubpath()
            item = QGraphicsPathItem(path, mapper.pixmap_item)
            item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            pen = QPen(color, 2.0)
            pen.setCosmetic(True)
            item.setPen(pen)
            item.setZValue(510)
            item.setData(0, "fluoro_ai_object_overlay")
            return item
        geometry = {
            "x": int(detected_object.get("bbox_x_min", 0)),
            "y": int(detected_object.get("bbox_y_min", 0)),
            "w": max(1, int(detected_object.get("bbox_x_max", 0)) - int(detected_object.get("bbox_x_min", 0))),
            "h": max(1, int(detected_object.get("bbox_y_max", 0)) - int(detected_object.get("bbox_y_min", 0))),
        }
        return self._rect_overlay_from_image_geometry(
            mapper,
            geometry,
            color,
            "fluoro_ai_object_overlay",
        )

    def _rect_overlay_from_image_geometry(self, mapper: ImageCoordinateMapper, geometry: dict[str, Any], color: QColor, marker: str) -> QGraphicsRectItem:
        x = float(geometry.get("x", 0.0))
        y = float(geometry.get("y", 0.0))
        w = float(geometry.get("w", 0.0))
        h = float(geometry.get("h", 0.0))
        top_left = mapper.image_px_to_local_pos((x, y))
        bottom_right = mapper.image_px_to_local_pos((x + w, y + h))
        item = QGraphicsRectItem(QRectF(top_left, bottom_right).normalized(), mapper.pixmap_item)
        item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        pen = QPen(color, 2.0)
        pen.setCosmetic(True)
        item.setPen(pen)
        item.setZValue(510)
        item.setData(0, marker)
        return item

    def set_measurement_overlay_visible(self, visible: bool) -> None:
        self._measurement_overlay_visible = bool(visible)
        for item in list(self._measurement_overlay_items):
            item.setVisible(bool(visible))
        if self._measurement_overlay_item is not None:
            self._measurement_overlay_item.setVisible(bool(visible))

    def clear_measurement_overlay(self) -> None:
        for item in list(self._measurement_overlay_items):
            if item.scene() is self.scene:
                self.scene.removeItem(item)
        if self._measurement_overlay_item is not None and self._measurement_overlay_item.scene() is self.scene:
            self.scene.removeItem(self._measurement_overlay_item)
        self._measurement_overlay_item = None
        self._measurement_overlay_items = []
        self.active_measurement_overlay_uuid = None

    def _measurement_channel_index(self, measurement: dict[str, Any]) -> int | None:
        rows = measurement.get("results_table") or []
        if rows:
            try:
                return int(rows[0].get("channel_index"))
            except (TypeError, ValueError):
                return None
        return None

    def _item_for_channel(self, channel_index: int | None) -> QGraphicsPixmapItem | None:
        for item in self._image_items:
            if item.data(0) == "fluoro_image_channel" and item.data(1) == channel_index:
                return item
        return next((item for item in self._image_items if item.data(0) == 'fluoro_image_merge'), None)

    def add_layout_text(self, text="Text", text_uuid="", x=None, y=None, font_family="Arial",
                        font_size=10.0, bold=False, italic=False, color="#FFFFFF", select=True, record=None):
        image = self.panel.active_image() if self.panel is not None else None
        image_uid = str(image.get("uid","")) if image else ""
        bounds = self.scene.itemsBoundingRect()
        data = dict(record) if record is not None else {"text":text,"font_family":font_family,
            "font_size":font_size,"bold":bold,"italic":italic,"color":color,
            "x":float(x if x is not None else bounds.left()+20),
            "y":float(y if y is not None else bounds.top()+20)}
        uid = str(text_uuid or data.get("text_uuid") or uuid4().hex)
        data["text_uuid"] = uid
        item = SharedTextItem(text_from_record(data,f"fluoro_text:{image_uid}:{uid}",
            {"fluoro_kind":"layout_text","image_uid":image_uid,"text_uuid":uid,"text_record":data}))
        item.setData(0,"fluoro_layout_text"); item.setData(1,uid)
        self.scene.addItem(item)
        item._text_changed_callback = self._persist_shared_annotation
        item._geometry_changed_callback = self._persist_shared_annotation
        item._style_changed_callback = self._persist_shared_annotation
        item.setSelected(select)
        if select:self._persist_shared_annotation(item)
        return item

    def _persist_shared_annotation(self,item,*_args):
        if self.panel is None:return
        obj = item.to_layout_object() if isinstance(item,SharedTextItem) else item.layout_object
        payload=obj.to_dict();payload.update(rotation_deg=item.rotation(),z_order=int(item.zValue()))
        obj=LayoutObject.from_dict(payload)
        image_uid = obj.metadata.get("image_uid")
        image = next((i for i in self.panel.images() if i.get("uid")==image_uid),None)
        if image is None:return
        text = isinstance(item,SharedTextItem)
        key = "text_uuid" if text else "shape_uuid"
        uid = str(item.data(1))
        records = image.setdefault("layout_texts" if text else "layout_shapes",[])
        record = next((r for r in records if r.get(key)==uid),None)
        if record is None:
            record={key:uid}
            records.append(record)
        record.update(x=item.pos().x(),y=item.pos().y())
        if text:
            record.update(text=obj.text,font_family=obj.style.font_family,font_size=obj.style.font_size_pt,
                          bold=obj.style.bold,italic=obj.style.italic,color=obj.style.text_color)
        else:
            record.update(shape_type=item.shape_kind,stroke_color=obj.style.stroke_color,
                          stroke_width=obj.style.stroke_width,fill_color=obj.style.fill_color)
        write_shared_fields(record,obj)

    def selected_text_items(self):
        return [item for item in self.scene.selectedItems() if isinstance(item,SharedTextItem)]

    def clear_text_selection_on_blank_click(self) -> None:
        self.scene.clearSelection()

    def apply_text_style_to_selection(self, font_family=None,font_size=None,bold=None,italic=None,color=None):
        delta={k:v for k,v in {"font_family":font_family,"font_size_pt":font_size,"bold":bold,"italic":italic,"text_color":color}.items() if v is not None}
        apply_selection_style(self.selected_text_items(),delta)

    def draw_overlap_chart(self, rows: list[dict[str, Any]]) -> None:
        if rows and "distance_um" in rows[0]:
            self.draw_line_profile_chart(rows)
            return
        self.draw_line_profile_chart(rows)

    def draw_line_profile_chart(self, rows: list[dict[str, Any]]) -> None:
        self._view_controller.set_content_identity(
            ("fluoro-profile",self.panel.panel_uuid if self.panel else None,bool(rows)))
        self.scene.clear()
        self.scene.setBackgroundBrush(QColor("#FFFFFF"))
        self._image_items = []
        if not rows:
            self._add_hint(tr('no_colocalization_result'), translation_key='no_colocalization_result')
            return
        width = 720.0
        height = 500.0
        left = 105.0
        top = 70.0
        plot_w = 430.0
        plot_h = 270.0
        axis_pen = QPen(QColor("#000000"), 2.2)
        self.scene.addLine(left, top + plot_h, left + plot_w, top + plot_h, axis_pen)
        self.scene.addLine(left, top, left, top + plot_h, axis_pen)
        series = [_line_profile_distance_series(row) for row in rows]
        axis_unit = "um" if any(unit == "um" for _distances, unit in series) else "px"
        x_title = self.scene.addText(f"Distance ({axis_unit})")
        x_title.setDefaultTextColor(QColor("#000000"))
        x_title.setFont(QFont("Arial", 22, QFont.Weight.Bold))
        x_title.setPos(left + 135, top + plot_h + 58)
        y_title = self.scene.addText("Intensity (a.u.)")
        y_title.setDefaultTextColor(QColor("#000000"))
        y_title.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        y_title.setRotation(-90)
        y_title.setPos(left - 82, top + 205)
        colors = ["#D62F2F", "#20E34B", "#0066FF", "#FF40FF", "#FFD13B"]
        max_distance = max((max(distances) for distances, _unit in series if distances), default=0.0)
        max_distance = max(1.0, float(max_distance) if max_distance else float(max(1, len(rows) - 1)))
        max_intensity = max(max(row.get("intensity", [0]) or [0]) for row in rows) if rows and "intensity" in rows[0] else max(1.0, max(float(row.get("overlap_percent_of_base", 0.0)) for row in rows))
        max_intensity = max(1.0, max_intensity)
        for series_index, row in enumerate(rows):
            distances, _unit = _line_profile_distance_series(row)
            values = row.get("intensity", []) or []
            if not distances or not values:
                distances = [float(series_index)]
                values = [float(row.get("overlap_percent_of_base", 0.0))]
            path = QPainterPath()
            for x_index, (distance, value) in enumerate(zip(distances, values, strict=False)):
                x = left + plot_w * float(distance) / max_distance
                y = top + plot_h - plot_h * float(value) / max_intensity
                if x_index == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            item = QGraphicsPathItem(path)
            item.setPen(QPen(QColor(colors[series_index % len(colors)]), 1.8))
            self.scene.addItem(item)
            legend_name = str(row.get("channel_name", row.get("base_channel_name", f"Ch{series_index + 1}")))
            legend = self.scene.addText(legend_name)
            legend.setDefaultTextColor(QColor(colors[series_index % len(colors)]))
            legend.setFont(QFont("Arial", 20, QFont.Weight.Bold))
            legend.setPos(left + plot_w + 55, top + 10 + series_index * 38)
            sample_line = self.scene.addLine(left + plot_w + 10, top + 25 + series_index * 38, left + plot_w + 42, top + 25 + series_index * 38)
            sample_line.setPen(QPen(QColor(colors[series_index % len(colors)]), 1.8))
        x_ticks = 5
        for tick_index in range(x_ticks + 1):
            value = max_distance * tick_index / x_ticks
            x = left + plot_w * tick_index / x_ticks
            self.scene.addLine(x, top + plot_h, x, top + plot_h + 8, axis_pen)
            label = self.scene.addText(f"{value:.0f}")
            label.setDefaultTextColor(QColor("#000000"))
            label.setFont(QFont("Arial", 15, QFont.Weight.Bold))
            label.setPos(x - 12, top + plot_h + 12)
        for tick_index in range(4):
            value = max_intensity * tick_index / 3
            y = top + plot_h - plot_h * value / max_intensity
            self.scene.addLine(left - 4, y, left, y, axis_pen)
            label = self.scene.addText(f"{value:.0f}")
            label.setDefaultTextColor(QColor("#000000"))
            label.setFont(QFont("Arial", 15, QFont.Weight.Bold))
            label.setPos(left - 46, y - 12)
        self.scene.setSceneRect(QRectF(0, 0, width, height))
        self._ensure_white_canvas()
        self._apply_canvas_zoom()

    def _draw_scale_bar(
        self,
        image: dict[str, Any],
        pixmap_item: QGraphicsItem,
        *,
        layout_key: str | None = None,
        allow_legacy: bool = True,
    ) -> None:
        has_layout_scale_bars = any(isinstance(item, dict) for item in image.get("layout_scale_bars", []))
        scale_bar_state = self._scale_bar_state_for_layout_target(image, layout_key)
        if has_layout_scale_bars and scale_bar_state is None:
            return
        if scale_bar_state is None and not allow_legacy:
            return
        browser_owned = self._browser_bar_owns_target(image, layout_key)
        if self.view.roi_mode == 'scale_line' or (browser_owned and (self.current_step != 4 or scale_bar_state is None)):
            return
        browser_state = self._browser_display_scale_bar_state(image) if browser_owned else None
        config = dict(image.get("scale_bar", {}))
        template = dict(image.get("layout_scale_bar_template", {})) if scale_bar_state is not None else {}
        if scale_bar_state is not None:
            template.update(scale_bar_state.get("template") or {})
        if browser_state is not None:
            template.update(label_length_um=browser_state.length_um, show_text=True,
                line_thickness=browser_state.line_thickness_px, line_color=browser_state.line_color,
                text_color=browser_state.text_color, font_family=browser_state.font_family,
                bold=browser_state.bold, italic=browser_state.italic, text_position=browser_state.text_position)
        metadata = dict(image.get("metadata") or {}) if isinstance(image.get("metadata"), dict) else {}
        saved_scale = dict(image.get("measurement_scale_bar") or {}) if isinstance(image.get("measurement_scale_bar"), dict) else None
        raw_manual = scale_bar_state.get("manual_um_per_px") if scale_bar_state is not None else config.get("manual_um_per_px")
        try:
            manual_override = float(raw_manual) if raw_manual not in (None, "") else None
        except (TypeError, ValueError):
            manual_override = None
        if manual_override is not None and manual_override <= 0:
            manual_override = None
        from core.layout_scale_bar import resolve_scale_bar_calibration

        calibration = resolve_scale_bar_calibration(
            metadata,
            manual_um_per_px=manual_override,
            saved_scale=saved_scale,
        )
        try:
            requested_length_um = float((template.get("label_length_um") if template else None) or config.get("length_um", 50.0) or 50.0)
        except (TypeError, ValueError):
            requested_length_um = 50.0
        um_x = 0.0
        if calibration.calibrated:
            try:
                um_x = float(calibration.um_per_px_x or calibration.um_per_px or 0.0)
            except (TypeError, ValueError):
                um_x = 0.0
        try:
            source_width_px = float(metadata.get("width_px") or metadata.get("width") or 0.0)
        except (TypeError, ValueError):
            source_width_px = 0.0
        local_rect = pixmap_item.rect() if hasattr(pixmap_item, "rect") else pixmap_item.boundingRect()
        scale = local_rect.width() / max(source_width_px, 1.0)
        if browser_state is not None:
            template['font_size'] = browser_state.font_size_px * scale * 72.0 / self.logicalDpiY()
        if calibration.calibrated and um_x > 0 and requested_length_um > 0:
            source_length_px = requested_length_um / um_x
            label_text = f"{requested_length_um:g} um"
            bar_width = source_length_px * scale
        else:
            try:
                source_length_px = float(config.get("length_px", 0.0) or 0.0)
            except (TypeError, ValueError):
                source_length_px = 0.0
            if source_length_px <= 0:
                if scale_bar_state is None:return
                from core.layout_scale_bar import scale_bar_layout_object
                spec=scale_bar_layout_object(panel_uuid=self.panel.panel_uuid,module_kind='fluorescence',
                    target_id=layout_key or '',target_rect=dict(x=local_rect.x(),y=local_rect.y(),
                        width=local_rect.width(),height=local_rect.height()),source_metadata=metadata,
                    template=template,state=scale_bar_state,measurement_scale=saved_scale)
                label_text=spec.metadata['label_text'];bar_width=spec.width
            else:
                label_text = f"{source_length_px:g} px"
                bar_width = source_length_px * scale
        if bar_width <= 0:
            return
        plate_style = self._image_plate_style()
        bar_height = max(1.0, float(template.get("line_thickness", config.get("height_px", 6))) * scale)
        if scale_bar_state is None:
            bar_height = max(bar_height, float(plate_style.get("scale_bar_line_width", 0.0) or 0.0))
        offset_x = local_rect.width() * float(template.get("offset_x_fraction", 0.06)) if template else 14.0
        offset_y = local_rect.height() * float(template.get("offset_y_fraction", 0.08)) if template else 14.0
        anchor = str(template.get("anchor", "bottom_right"))
        x = local_rect.left() + offset_x if anchor.endswith("left") else local_rect.right() - bar_width - offset_x
        line_y = local_rect.top() + offset_y if anchor.startswith("top") else local_rect.bottom() - offset_y
        if browser_state is not None:
            from core.image_browser import scale_bar_layout
            geometry = scale_bar_layout(local_rect, QSize(round(source_width_px),
                int(metadata.get('height_px') or metadata.get('height') or 1)), browser_state)
            x = geometry.line_rect.left()
            line_y = geometry.line_rect.center().y()
        color_value = str(template.get("line_color", config.get("color", "#FFFFFF"))) if scale_bar_state is not None else str(plate_style.get("scale_bar_color", config.get("color", "#FFFFFF")))
        color = QColor(color_value)
        from ui.widgets.scale_bar_attachment import AttachedScaleBarLine, ScaleBarAttachment
        line = AttachedScaleBarLine(x, line_y, x + bar_width, line_y, pixmap_item)
        line.setData(0, "fluoro_scale_bar")
        line.setData(1, layout_key or "")
        line.setData(2, str(image.get("uid", "")))
        line.setData(3, "line")
        line.setPen(QPen(color, bar_height, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap))
        line.setZValue(120)
        line.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        text = None
        if template.get("show_text", config.get("show_text", True)):
            text = EditableMovableTextItem(label_text, _noop_text, _noop_move, _noop_style, ("fluoro_scale_bar", 0))
            text.setParentItem(pixmap_item)
            text.setData(0, "fluoro_scale_bar")
            text.setData(1, layout_key or "")
            text.setData(2, str(image.get("uid", "")))
            text.setData(3, "text")
            text_color = QColor(str(template.get("text_color", color_value))) if scale_bar_state is not None else color
            text.setDefaultTextColor(text_color)
            font=QFont(str(template.get("font_family",plate_style.get("label_font_family","Arial"))))
            font.setPointSizeF(float(template.get("font_size",plate_style.get("label_font_size_pt",config.get("font_size",7)))))
            font.setBold(bool(template.get('bold',False)));font.setItalic(bool(template.get('italic',False)))
            text.setFont(font)
            text_width = float(text.boundingRect().width())
            text_height = float(text.boundingRect().height())
            gap = float(template.get("label_gap", 4.0)) if template else 4.0
            if str(template.get("text_position", "above")) == "below":
                line_y = min(line_y, local_rect.bottom() - text_height - gap)
                text_y = line_y + gap
            else:
                text_y = line_y - text_height - gap
            text.setPos(x + (bar_width - text_width) / 2.0, text_y)
            text.setZValue(121)
            text.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        line.setLine(x, line_y, x + bar_width, line_y)
        attachment=ScaleBarAttachment(line,text,text_position=template.get('text_position','above'),
            gap=template.get('label_gap',4),
            changed=lambda pair:self._store_scale_bar_attachment(image,pixmap_item,layout_key,pair,
                manual_override=manual_override,label_length_um=requested_length_um))
        if isinstance(pixmap_item,FluoroLayoutPixmapItem):
            pixmap_item.scale_bar_attachments.append(attachment)
        else:
            line.scale_bar_attachment=attachment

    def _store_scale_bar_attachment(self,image,picture,target,pair,*,manual_override,label_length_um):
        from core.layout_scale_bar import normalize_scale_bar_template
        states=image.setdefault('layout_scale_bars',[])
        state=next((item for item in states if item.get('target_id')==target),None)
        if state is None:
            state={'target_id':target,'enabled':True,'template':{'label_length_um':label_length_um}}
            if manual_override is not None:state['manual_um_per_px']=manual_override
            states.append(state)
        template=normalize_scale_bar_template({**image.get('layout_scale_bar_template',{}),**state.get('template',{})})
        rect=picture.rect() if hasattr(picture,'rect') else picture.boundingRect()
        line=pair.line.line();origin=pair.line.pos()+line.p1()
        anchor=template['anchor']
        template['offset_x_fraction']=(rect.right()-origin.x()-line.length())/rect.width() if anchor.endswith('right') else (origin.x()-rect.left())/rect.width()
        template['offset_y_fraction']=(rect.bottom()-origin.y())/rect.height() if anchor.startswith('bottom') else (origin.y()-rect.top())/rect.height()
        source_width=getattr(picture,'_source_width',None) or image.get('metadata',{}).get('width_px') or rect.width()
        template['line_thickness']=pair.line.pen().widthF()/(rect.width()/source_width)
        line_color=pair.line.pen().color().name()
        if str(template['line_color']).lower()!=line_color.lower():template['line_color']=line_color
        template['label_gap']=pair.gap
        template['show_text']=pair.text is not None
        if pair.text is not None:
            font=pair.text.font()
            template.update(font_family=font.family(),font_size=font.pointSizeF(),bold=font.bold(),
                            italic=font.italic())
            text_color=pair.text.defaultTextColor().name()
            if str(template['text_color']).lower()!=text_color.lower():template['text_color']=text_color
        state['template']=template

    def _scale_bar_state_for_layout_target(self, image: dict[str, Any], layout_key: str | None) -> dict[str, Any] | None:
        states = [dict(item) for item in image.get("layout_scale_bars", []) if isinstance(item, dict)]
        if not states:
            return None
        for state in states:
            if str(state.get("target_id", "")) == str(layout_key or "") and bool(state.get("enabled", True)):
                return state
        return None

    def _handle_scene_roi(self, roi_type: str, payload: object) -> None:
        if roi_type in {"rectangle", "square", "ellipse", "circle", "sample_spot", "text_select", "black_balance", "display_black_point", "display_white_point"} and isinstance(payload, QRectF):
            if roi_type == "text_select":
                self.select_text_in_rect(payload)
                return
            mapped = self._map_rect_to_image_geometry(roi_type, payload)
            emit_type = roi_type
        elif roi_type in {"profile_line", "scale_line"} and isinstance(payload, dict):
            mapped = self._map_line_to_image_geometry(payload)
            emit_type = roi_type
        elif roi_type == "freehand" and isinstance(payload, list):
            mapped = self._map_points_to_image_geometry(payload)
            emit_type = roi_type
        else:
            mapped = None
        if mapped is not None or roi_type in {"display_black_point", "display_white_point"}:
            self.roi_drawn.emit(emit_type, mapped or {})

    def select_text_in_rect(self, rect: QRectF) -> None:
        self.scene.clearSelection()
        for item in self.scene.items():
            if isinstance(item, QGraphicsTextItem) and rect.intersects(item.sceneBoundingRect()):
                item.setSelected(True)

    def _map_rect_to_image_geometry(self, roi_type: str, rect: QRectF) -> dict[str, float] | None:
        item = self._item_for_scene_rect(rect)
        if item is None:
            return None
        mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
        x0, y0 = mapper.scene_pos_to_image_px(rect.normalized().topLeft())
        x1, y1 = mapper.scene_pos_to_image_px(rect.normalized().bottomRight())
        x = min(x0, x1)
        y = min(y0, y1)
        w = abs(x1 - x0)
        h = abs(y1 - y0)
        if roi_type in {"ellipse", "circle"}:
            rx = w / 2.0
            ry = h / 2.0
            if roi_type == "circle":
                radius = min(rx, ry)
                rx = radius
                ry = radius
            geometry = {"cx": x + w / 2.0, "cy": y + h / 2.0, "rx": rx, "ry": ry}
            return self._with_source_channel(geometry, item)
        if roi_type == "square":
            side = min(w, h)
            return self._with_source_channel({"x": x, "y": y, "w": side, "h": side}, item)
        return self._with_source_channel({"x": x, "y": y, "w": w, "h": h}, item)

    def _map_points_to_image_geometry(self, points: list[tuple[float, float]]) -> dict[str, list[tuple[float, float]]] | None:
        if len(points) < 3:
            return None
        first = QPointF(points[0][0], points[0][1])
        item = self._item_for_scene_point(first)
        if item is None:
            return None
        mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
        mapped = []
        for x, y in points:
            mapped.append(mapper.scene_pos_to_image_px(QPointF(x, y)))
        return self._with_source_channel({"points": mapped}, item)

    def _map_line_to_image_geometry(self, payload: dict[str, float]) -> dict[str, float] | None:
        start = QPointF(float(payload.get("x1", 0.0)), float(payload.get("y1", 0.0)))
        end = QPointF(float(payload.get("x2", start.x())), float(payload.get("y2", start.y())))
        item = self._item_for_scene_point(start) or self._item_for_scene_point(end)
        if item is None:
            return None
        mapper = ImageCoordinateMapper(item, self._analysis_image_shape())
        mapped = self._with_source_channel(mapper.line_scene_to_image_px(start, end), item)
        if payload.get("move_existing"):
            mapped["move_existing"] = True
        return mapped

    def _with_source_channel(self, geometry: dict, item: QGraphicsPixmapItem) -> dict:
        if item.data(0) == "fluoro_image_channel" and item.data(1) is not None:
            geometry["source_channel_index"] = int(item.data(1))
        return geometry

    def _analysis_image_shape(self) -> tuple[int, int]:
        if self.panel is not None:
            image = self.panel.active_image()
            data = self.panel.image_data(str(image.get("uid"))) if image else None
            if data is not None:
                return int(data.shape[1]), int(data.shape[2])
        item = self._image_items[0] if self._image_items else None
        if item is not None:
            return int(item.pixmap().height()), int(item.pixmap().width())
        return 1, 1

    def _item_for_scene_rect(self, rect: QRectF) -> QGraphicsPixmapItem | None:
        for item in self._image_items:
            if item.sceneBoundingRect().intersects(rect):
                return item
        return None

    def _item_for_scene_point(self, point: QPointF) -> QGraphicsPixmapItem | None:
        for item in self._image_items:
            if item.sceneBoundingRect().contains(point):
                return item
        return self._image_items[0] if self._image_items else None

    def _ensure_white_canvas(self) -> None:
        """Keep the exported paper unchanged, independently of the screen frame."""
        rect = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            rect = QRectF(0, 0, 760, 420)
        self._white_canvas_item = CanvasExportBackgroundItem(rect)
        self._white_canvas_item.setBrush(QColor("#FFFFFF"))
        self._white_canvas_item.setPen(QPen(QColor("#E5E2DD"), 0.8))
        self._white_canvas_item.setZValue(-1000)
        self.scene.addItem(self._white_canvas_item)
        self.scene.setSceneRect(rect)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._view_controller.handle_resize()


def _noop_text(*_args) -> None:
    return None


def _noop_move(*_args) -> None:
    return None


def _noop_style(*_args) -> None:
    return None

