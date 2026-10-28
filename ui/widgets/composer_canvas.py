from __future__ import annotations

import json
from shutil import copy2
from pathlib import Path
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, UTC

import numpy as np
from PyQt6.QtCore import QByteArray, QLineF, QMarginsF, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtCore import QSizeF
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetricsF, QImage, QImageReader, QPageLayout, QPageSize, QPainter, QPainterPath, QPdfWriter, QPen, QPixmap
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtSvg import QSvgGenerator, QSvgRenderer
from PyQt6.QtWidgets import (
    QGraphicsItem,
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.models import ExternalImagePanel, Project, QPCRPanel
from core.models.composer import AnnotationObject, ComposerFigure, ComposerLayout, ComposerCell, PanelReference, default_panel_label
from core.composer_labels import sync_panel_labels, label_for_reference, label_is_visible
from ui.layout_adapters.composer_layout_adapter import annotation_layout_object, update_annotation_from_layout
from ui.widgets.shared_layout_text import SharedTextItem, SharedTextLayout
from ui.widgets.shared_layout_shapes import SharedShapeItem
from core.chart_payload import (
    effective_composer_chart_kind,
    hydrate_legacy_plot_payload,
    is_statistical_composer_candidate,
    is_image_like_composer_panel,
    panel_plot_payload,
    plot_payload_cache_key,
    qpcr_live_plot_payload,
    render_plot_payload_bytes,
)
from core.plotting.chart_kinds import normalize_chart_kind
from core.utils.svg_text import apply_svg_text_overrides, build_svg_text_override, normalize_svg_text_overrides, svg_text_entries, strip_svg_text_layers
from core.utils.i18n import tr
from core.utils.image_formats import COMMON_RASTER_SUFFIXES, compound_suffix
from core.utils.time import utc_now_iso
from ui.widgets.canvas_presentation import configure_canvas_layout
from ui.widgets.canvas_graphics_adapter import (
    CanvasGraphicsView,
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
from ui.widgets.layout_editing import (
    EditableLayoutRecord,
    RESIZE_HANDLE_ROLES,
    RESIZE_HANDLE_SIZE_PX,
    align_records,
    distribute_records,
    make_records_same_size,
)
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action
from ui.widgets.svg_text_editor import prompt_svg_text_edit, prompt_svg_text_position_edit


MM_TO_PX = 4.0
MM_PER_INCH = 25.4
SVG_DPI = 72.0
COMPOSER_PREVIEW_DPI = 600
COMPOSER_MAX_PREVIEW_DPI = 1200
OME_RASTER_SUFFIXES = {".ome.tif", ".ome.tiff"}
COMPOSER_NATIVE_RASTER_SUFFIXES = COMMON_RASTER_SUFFIXES | OME_RASTER_SUFFIXES
PANEL_UUID_MIME = "application/x-sciefig-panel-uuid"
HANDLE_SIZE_PX = RESIZE_HANDLE_SIZE_PX
RESIZE_HANDLES = RESIZE_HANDLE_ROLES


class ComposerResizeHandle(QGraphicsRectItem):
    def __init__(self, parent_item, role: str) -> None:
        half = HANDLE_SIZE_PX / 2
        super().__init__(-half, -half, HANDLE_SIZE_PX, HANDLE_SIZE_PX, parent_item)
        self.parent_item = parent_item
        self.role = role
        self.start_scene_pos = QPointF()
        self.start_geometry = (0.0, 0.0, 0.0, 0.0)
        self.setData(0, "resize_handle")
        self.setBrush(QBrush(QColor("#ffffff")))
        self.setPen(QPen(QColor("#d97757"), 1.4))
        self.setZValue(50)
        self.setVisible(False)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)

    def mousePressEvent(self, event) -> None:
        self.start_scene_pos = event.scenePos()
        self.start_geometry = self.parent_item.current_geometry()
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        dx_mm = (event.scenePos().x() - self.start_scene_pos.x()) / MM_TO_PX
        dy_mm = (event.scenePos().y() - self.start_scene_pos.y()) / MM_TO_PX
        x, y, width, height = self.start_geometry
        new_x, new_y, new_width, new_height = x, y, width, height
        min_size = 5.0

        if "e" in self.role:
            new_width = max(min_size, width + dx_mm)
        if "s" in self.role:
            new_height = max(min_size, height + dy_mm)
        if "w" in self.role:
            new_width = max(min_size, width - dx_mm)
            new_x = x + (width - new_width)
        if "n" in self.role:
            new_height = max(min_size, height - dy_mm)
            new_y = y + (height - new_height)

        if self.parent_item.aspect_locked() != bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            from ui.widgets.layout_editing import aspect_resize_size
            new_width,new_height=aspect_resize_size(width,height,new_width,new_height,self.role)
            if "w" in self.role:
                new_x = x + (width - new_width)
            if "n" in self.role:
                new_y = y + (height - new_height)
            if self.role in {'e','w'}:new_y=y+(height-new_height)/2
            if self.role in {'n','s'}:new_x=x+(width-new_width)/2

        self.parent_item.apply_geometry(new_x, new_y, new_width, new_height, repaint=False)
        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self.parent_item.canvas.reference_changed()
        self.parent_item.canvas.render()
        event.accept()


class ComposerPanelItem(QGraphicsRectItem):
    def __init__(self, canvas: "ComposerCanvas", ref: PanelReference, panel, pixmap: QPixmap | None) -> None:
        super().__init__(0, 0, ref.width_mm * MM_TO_PX, ref.height_mm * MM_TO_PX)
        self.canvas = canvas
        self.ref = ref
        self.panel = panel
        self._svg_binding = None
        self._svg_text_items = []
        self._visual_item = None
        self.setData(0, "panel_ref")
        self.setData(1, ref.ref_uid)
        self.setPos(ref.x_mm * MM_TO_PX, ref.y_mm * MM_TO_PX)
        flags = QGraphicsItem.GraphicsItemFlag.ItemIsSelectable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        if not ref.locked:
            flags |= QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        self.setFlags(flags)
        border_pen = QPen(QColor(ref.border_color), ref.border_width_pt) if ref.border else QPen(Qt.PenStyle.NoPen)
        self.setPen(border_pen)
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setZValue(ref.layer_index)
        self.handles = {role: ComposerResizeHandle(self, role) for role in RESIZE_HANDLES}

        content_width = max(1, int((ref.width_mm * MM_TO_PX) - 8))
        content_height = max(1, int((ref.height_mm * MM_TO_PX) - 16))
        if pixmap is not None and not pixmap.isNull():
            scale = min(content_width / max(pixmap.width(), 1), content_height / max(pixmap.height(), 1))
            display_width = pixmap.width() * scale
            display_height = pixmap.height() * scale
            image_item = QGraphicsPixmapItem(pixmap, self)
            self._visual_item = image_item
            image_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            image_item.setScale(scale)
            image_item.setPos((ref.width_mm * MM_TO_PX - display_width) / 2, 12 + max(0.0, (content_height - display_height) / 2))
        else:
            placeholder = QGraphicsTextItem(f"{panel.panel_id}\n{panel.panel_type}", self)
            placeholder.setDefaultTextColor(QColor("#333333"))
            placeholder.setTextWidth(max(1, ref.width_mm * MM_TO_PX - 8))
            placeholder.setPos(4, max(14, ref.height_mm * MM_TO_PX / 2 - 20))

        self.update_handles()

    def _rendered_svg_bytes(self):
        return self.canvas._plot_payload_bytes_for_ref(
            self.panel, self.ref, "svg", max(1, int(self.rect().width()-12)),
            max(1, int(self.rect().height()-12)), COMPOSER_PREVIEW_DPI) or b""

    def _svg_override_key(self):
        payload = self.canvas._plot_payload_for_ref(self.panel, self.ref)
        return effective_composer_chart_kind(self.panel, self.ref, payload) if payload else "svg"

    def _svg_overrides(self):
        return self.ref.chart_text_overrides.get(self._svg_override_key(), {})

    def _store_shared_svg_text(self, index, value):
        self.ref.chart_text_overrides.setdefault(self._svg_override_key(), {})[str(index)] = value
        self.canvas.reference_changed()

    def _refresh_svg_payload(self):
        self._export_svg_bytes = self._rendered_svg_bytes()

    def _rebuild_svg_text_items(self):
        if self._visual_item is None or not self.scene():
            return
        svg = self._rendered_svg_bytes()
        if not svg:
            return
        from ui.widgets.shared_svg_text import SvgTextBinding
        if self._svg_binding is None:
            # Keep the existing full-resolution preview size; only remove its
            # text layer, which is now drawn by editable vector text objects.
            clean = QPixmap(self._visual_item.pixmap().size())
            clean.fill(Qt.GlobalColor.transparent)
            painter = QPainter(clean)
            QSvgRenderer(strip_svg_text_layers(svg)).render(painter)
            painter.end()
            self._visual_item.setPixmap(clean)
            self._svg_binding = SvgTextBinding(self, self._visual_item,
                self._rendered_svg_bytes, self._svg_overrides, self._store_shared_svg_text)
        self._svg_binding.rebuild()
        self._svg_text_items = self._svg_binding.items

    def current_geometry(self) -> tuple[float, float, float, float]:
        return (self.ref.x_mm, self.ref.y_mm, self.ref.width_mm, self.ref.height_mm)

    def aspect_locked(self) -> bool:
        return bool(self.ref.aspect_locked)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged:
            self._rebuild_svg_text_items()
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self.canvas.updating_scene:
            self.ref.x_mm = float(value.x() / MM_TO_PX)
            self.ref.y_mm = float(value.y() / MM_TO_PX)
            self.canvas.reference_changed()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.set_handles_visible(bool(value) and not self.ref.locked)
        return super().itemChange(change, value)

    def apply_svg_text_override(self, index, text=None, *, dx=None, dy=None) -> bool:
        if not self._rendered_svg_bytes():
            return False
        previous = self._svg_overrides().get(str(index), {})
        previous = previous if isinstance(previous, dict) else {"text": previous}
        value = build_svg_text_override(previous, text=text,
            dx=float(previous.get("dx", 0)) + dx if dx is not None else None,
            dy=float(previous.get("dy", 0)) + dy if dy is not None else None)
        self._store_shared_svg_text(index, value)
        self._refresh_svg_payload()
        self._rebuild_svg_text_items()
        return True

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        edit_action = menu.addAction("Edit SVG Text...") if isinstance(self.panel, ExternalImagePanel) else None
        move_action = menu.addAction("Move SVG Text...") if isinstance(self.panel, ExternalImagePanel) else None
        action = menu.exec(event.screenPos())
        if action not in {edit_action, move_action}:
            return
        source = self.canvas._external_source_path(self.panel)
        if source is None or not source.exists():
            return
        current_svg = self.canvas._plot_payload_bytes_for_ref(
            self.panel, self.ref, "svg", max(1, int(self.ref.width_mm * MM_TO_PX)),
            max(1, int(self.ref.height_mm * MM_TO_PX)), COMPOSER_PREVIEW_DPI,
        ) or source.read_bytes()
        if action == edit_action:
            edit = prompt_svg_text_edit(None, current_svg, "Edit SVG Text")
            if edit is not None:
                entry, replacement = edit
                self.apply_svg_text_override(entry.index, text=replacement)
        elif action == move_action:
            move = prompt_svg_text_position_edit(None, current_svg, "Move SVG Text")
            if move is not None:
                entry, dx, dy = move
                self.apply_svg_text_override(entry.index, dx=dx, dy=dy)
        event.accept()

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

    def apply_geometry(self, x_mm: float, y_mm: float, width_mm: float, height_mm: float, repaint: bool = True) -> None:
        if self.ref.locked:
            return
        self.canvas.updating_scene = True
        try:
            self.ref.x_mm = float(x_mm)
            self.ref.y_mm = float(y_mm)
            self.ref.width_mm = float(width_mm)
            self.ref.height_mm = float(height_mm)
            self.setPos(self.ref.x_mm * MM_TO_PX, self.ref.y_mm * MM_TO_PX)
            self.setRect(0, 0, self.ref.width_mm * MM_TO_PX, self.ref.height_mm * MM_TO_PX)
            self.update_handles()
        finally:
            self.canvas.updating_scene = False
        self.canvas.reference_changed()
        if repaint:
            self.canvas.render()


class ComposerTextItem(SharedTextItem):
    def __init__(self, canvas: "ComposerCanvas", annotation: AnnotationObject) -> None:
        self.canvas = canvas
        self.annotation = annotation
        super().__init__(annotation_layout_object(annotation, MM_TO_PX))
        self.setData(0, "annotation")
        self.setData(1, annotation.annotation_uid)
        self.setData(2, "text")
        self._text_changed_callback = self._sync_text
        self._style_changed_callback = self._sync_text
        self._geometry_changed_callback = self._sync_text

    def _apply_style(self) -> None:
        obj = annotation_layout_object(self.annotation, MM_TO_PX)
        self.layout_object = obj
        self.apply_style(obj.style)
        self.setRotation(obj.rotation_deg)

    def _sync_text(self, *_args) -> None:
        if self.canvas.updating_scene:
            return
        previous=(self.annotation.x_mm,self.annotation.y_mm,self.annotation.properties.get('content',''))
        update_annotation_from_layout(self.annotation, self.to_layout_object(), MM_TO_PX)
        props=self.annotation.properties
        if props.get('role')=='panel_label':
            if previous[:2]!=(self.annotation.x_mm,self.annotation.y_mm):
                props['follow_panel']=False
            if previous[2]!=props.get('content',''):
                props['label_auto']=False
            ref=next((r for r in self.canvas.current_figure().panel_refs if r.ref_uid==props.get('panel_ref_uid')),None)
            if ref is not None:
                ref.figure_label=props.get('content','')
                ref.label_auto=bool(props.get('label_auto',False))
        self.canvas.annotation_changed()


class ComposerShapeItem(SharedShapeItem):
    """Composer model/units adapter; editing and painting are shared."""
    def __init__(self, canvas, annotation):
        self.canvas, self.annotation = canvas, annotation
        super().__init__(annotation_layout_object(annotation, MM_TO_PX))
        self.setData(0, "annotation")
        self.setData(1, annotation.annotation_uid)
        self.setData(2, "shape")
        for handle in self.handles.values():
            handle.setData(0, "resize_handle")
        self._geometry_changed_callback = self._sync_annotation
        self._style_changed_callback = self._sync_annotation

    def _sync_annotation(self, *_args):
        if self.canvas.updating_scene:
            return
        update_annotation_from_layout(self.annotation, self.layout_object, MM_TO_PX)
        self.canvas.annotation_changed()

    def current_geometry(self):
        obj = self.annotation
        return obj.x_mm, obj.y_mm, obj.properties["width_mm"], obj.properties["height_mm"]

    def aspect_locked(self):
        return bool(self.annotation.properties.get("aspect_locked", False))

    def set_handles_visible(self, visible):
        for handle in self.handles.values():
            handle.setVisible(visible and not self.annotation.locked)

    def apply_geometry(self, x_mm, y_mm, width_mm, height_mm, repaint=True):
        if self.annotation.locked:
            return
        self.update_geometry(x_mm*MM_TO_PX, y_mm*MM_TO_PX, width_mm*MM_TO_PX, height_mm*MM_TO_PX)
        if repaint:
            self.canvas.render()


class ComposerGraphicsView(CanvasGraphicsView):
    panel_dropped = pyqtSignal(str, QPointF)

    def __init__(self, scene: QGraphicsScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setAcceptDrops(True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheNone)

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:
        painter.save()
        super().drawBackground(painter, rect)
        grid_step = 24.0
        left = int(rect.left() // grid_step) * grid_step
        top = int(rect.top() // grid_step) * grid_step
        right = rect.right()
        bottom = rect.bottom()
        lines: list[QLineF] = []
        x = float(left)
        while x <= right:
            lines.append(QLineF(x, rect.top(), x, bottom))
            x += grid_step
        y = float(top)
        while y <= bottom:
            lines.append(QLineF(rect.left(), y, right, y))
            y += grid_step
        painter.setPen(QPen(QColor(255, 255, 255, 12), 0))
        painter.drawLines(lines)
        painter.restore()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(PANEL_UUID_MIME):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(PANEL_UUID_MIME):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasFormat(PANEL_UUID_MIME):
            panel_uuid = bytes(event.mimeData().data(PANEL_UUID_MIME)).decode("utf-8")
            self.panel_dropped.emit(panel_uuid, self.mapToScene(event.position().toPoint()))
            event.acceptProposedAction()
            return
        super().dropEvent(event)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        canvas = canvas_owner_of(self)
        if isinstance(canvas, ComposerCanvas):
            canvas.handle_first_view_paint()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        canvas = canvas_owner_of(self)
        if isinstance(canvas, ComposerCanvas):
            canvas.schedule_recenter_after_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        canvas = canvas_owner_of(self)
        if isinstance(canvas, ComposerCanvas):
            canvas.schedule_recenter_after_layout()

    def wheelEvent(self, event) -> None:
        if handle_canvas_wheel(self, event):
            return
        super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        if is_canvas_text_editing(self):
            super().keyPressEvent(event)
            return
        if handle_canvas_space_press(self, event):
            return
        if handle_canvas_zoom_shortcut(self, event):
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if handle_canvas_space_release(self, event):
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        if handle_canvas_pan_press(self, event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if handle_canvas_pan_move(self, event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if handle_canvas_pan_release(self, event):
            return
        super().mouseReleaseEvent(event)


class ComposerCanvas(SceneCanvasOwnerMixin, QWidget):
    pages_changed = pyqtSignal()
    document_changed = pyqtSignal()
    preflight_requested = pyqtSignal()
    export_requested = pyqtSignal()
    FULL_RASTER_CACHE_LIMIT = 4
    PREVIEW_CACHE_LIMIT = 16

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ComposerCanvas")
        self.scene = QGraphicsScene(self)
        self.view = ComposerGraphicsView(self.scene, self)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.view.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.view.setObjectName("ComposerView")
        self.zoom_controls = CanvasZoomControls()
        zoom_layout = self.zoom_controls.layout()
        if zoom_layout is not None:
            zoom_layout.setContentsMargins(0, 0, 0, 0)
        self.zoom_controls.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._guides_visible = True
        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        layout.addWidget(self.zoom_controls, 0, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.view, 1)
        self.project: Project | None = None
        self.layout_model = ComposerLayout.create("2x2")
        self.paper_rect = QRectF(0, 0, self.layout_model.width_mm * MM_TO_PX, self.layout_model.height_mm * MM_TO_PX)
        self.updating_scene = False
        self._full_raster_cache: OrderedDict[tuple[str, int, int], QPixmap] = OrderedDict()
        self._pixmap_cache: OrderedDict[tuple[object, ...], QPixmap] = OrderedDict()
        self._plot_payload_bytes_cache: OrderedDict[tuple[object, ...], bytes] = OrderedDict()
        self._first_view_paint_pending = True
        attach_scene_canvas_controller(
            self,
            self.view,
            content_rect_provider=self._fit_target_rect,
            zoom_controls=self.zoom_controls,
            after_view_change=self._on_canvas_view_change,
            fit_delays=(0, 80),
            enable_view_pan_room=False,
            editable=True,
        )



    @property
    def guides_visible(self) -> bool:
        return self._guides_visible

    def set_guides_visible(self, visible: bool) -> None:
        visible = bool(visible)
        if self._guides_visible == visible:
            return
        self._guides_visible = visible
        self.render()

    def set_project(self, project: Project) -> None:
        self.project = project
        figure = project.ensure_active_composer_figure()
        preset = figure.canvas.get('grid_preset', project.get_composer_layout().preset)
        self.layout_model = figure.to_layout(preset)
        project.composer_layout = self.layout_model.to_dict()
        self._refresh_pages_strip()
        self.render()
        self.schedule_recenter_after_layout()

    def set_layout_preset(self, preset: str) -> None:
        if self.project is None:
            return
        self.current_figure().canvas["grid_preset"] = preset
        self.layout_model = self.current_figure().to_layout(preset)
        self.project.composer_layout = self.layout_model.to_dict()
        self.project.touch()
        self.document_changed.emit()
        self._refresh_pages_strip()
        self.render()

    def add_page(self) -> None:
        if self.project is None:
            return
        active = self.current_figure()
        figure = ComposerFigure(
            figure_id=self._next_page_name(),
            canvas=dict(active.canvas),
            global_style=dict(active.global_style),
            journal_preset=str(active.journal_preset),
            export_settings=dict(active.export_settings),
        )
        self.project.composer_figures.append(figure)
        self.project.active_figure_uid = figure.figure_uid
        self.project.composer_layout = figure.to_layout(self.layout_model.preset).to_dict()
        self.project.append_audit("composer_add_page", figure.figure_id)
        self.layout_model = figure.to_layout(self.layout_model.preset)
        self._refresh_pages_strip()
        self.render()
        self.schedule_recenter_after_layout()

    def delete_current_page(self) -> None:
        if self.project is None:
            return
        self.project.ensure_active_composer_figure()
        figures = list(self.project.composer_figures)
        if len(figures) <= 1:
            return
        active_uid = str(self.project.active_figure_uid or figures[0].figure_uid)
        active_index = next((index for index, figure in enumerate(figures) if figure.figure_uid == active_uid), 0)
        removed = figures.pop(active_index)
        next_index = min(active_index, len(figures) - 1)
        self.project.composer_figures = figures
        self.project.active_figure_uid = figures[next_index].figure_uid
        self.project.composer_layout = figures[next_index].to_layout(self.layout_model.preset).to_dict()
        self.project.append_audit("composer_delete_page", removed.figure_id)
        self.layout_model = figures[next_index].to_layout(self.layout_model.preset)
        self._refresh_pages_strip()
        self.render()
        self.schedule_recenter_after_layout()

    def set_active_page(self, figure_uid: str) -> None:
        if self.project is None:
            return
        for figure in self.project.composer_figures:
            if str(figure.figure_uid) == str(figure_uid):
                self.project.active_figure_uid = figure.figure_uid
                self.layout_model = figure.to_layout(figure.canvas.get('grid_preset', self.layout_model.preset))
                self.project.composer_layout = self.layout_model.to_dict()
                self.project.append_audit("composer_switch_page", figure.figure_id)
                self._refresh_pages_strip()
                self.render()
                self.schedule_recenter_after_layout()
                return

    def _next_page_name(self) -> str:
        if self.project is None:
            return "Figure 1"
        existing = {figure.figure_id for figure in self.project.composer_figures}
        index = len(existing) + 1
        while True:
            candidate = f"Figure {index}"
            if candidate not in existing:
                return candidate
            index += 1

    def _refresh_pages_strip(self) -> None:
        self.pages_changed.emit()

    def retranslate(self) -> None:
        self.zoom_controls.retranslate()
        for item in self.scene.items():
            if item.data(0)=='composer_guide_hint':item.setPlainText(tr('drop_panel_object'))
        self._refresh_pages_strip()

    def place_panel(self, panel_uuid: str, scene_pos: QPointF | None = None, reset_existing: bool = False) -> None:
        if self.project is None:
            return
        figure = self.current_figure()
        if reset_existing:
            self.layout_model.remove_panel(panel_uuid)
            figure.panel_refs = [ref for ref in figure.panel_refs if ref.panel_uid != panel_uuid]
        panel = self.project.get_panel(panel_uuid)
        if panel is None:
            raise KeyError(f"Panel not found: {panel_uuid}")
        empty=next((cell for cell in self.layout_model.cells if not cell.panel_uuid),None)
        if empty is not None:
            cell=ComposerCell.from_dict(empty.to_dict())
        else:
            offset=8.0+len(figure.panel_refs)%5*4
            cell=ComposerCell(row=0,col=0,x_mm=offset,y_mm=offset,
                width_mm=max(15,self.layout_model.width_mm/self.layout_model.cols-6),
                height_mm=max(15,self.layout_model.height_mm/self.layout_model.rows-6))
        if scene_pos is not None:
            cell.x_mm = float(scene_pos.x() / MM_TO_PX)
            cell.y_mm = float(scene_pos.y() / MM_TO_PX)
        preferred_width = float(panel.data.get("preferred_width_mm", 0.0) or 0.0) if hasattr(panel, "data") else 0.0
        preferred_height = float(panel.data.get("preferred_height_mm", 0.0) or 0.0) if hasattr(panel, "data") else 0.0
        if preferred_width > 0 and preferred_height > 0:
            max_width = max(5.0, self.layout_model.width_mm - cell.x_mm)
            max_height = max(5.0, self.layout_model.height_mm - cell.y_mm)
            scale = min(1.0, max_width / preferred_width, max_height / preferred_height)
            cell.width_mm = max(5.0, preferred_width * scale)
            cell.height_mm = max(5.0, preferred_height * scale)
        next_layer = self._next_layer_index()
        figure.panel_refs.append(
            PanelReference(
                panel_uid=panel_uuid,
                source_module=self._panel_source_module(panel),
                x_mm=float(cell.x_mm),
                y_mm=float(cell.y_mm),
                width_mm=float(cell.width_mm),
                height_mm=float(cell.height_mm),
                figure_label=cell.panel_letter or default_panel_label(len(figure.panel_refs), figure.global_style.get("panel_label_case")),
                layer_index=next_layer,
            )
        )
        self._renumber_auto_labels()
        figure.append_audit("place_panel_reference", panel_uuid)
        self.reference_changed()
        self.render()

    def is_panel_placed(self, panel_uuid: str) -> bool:
        if self.project is None:
            return False
        return any(ref.panel_uid == panel_uuid for ref in self.current_figure().panel_refs)

    def current_figure(self) -> ComposerFigure:
        if self.project is None:
            raise RuntimeError("ComposerCanvas has no project.")
        return self.project.ensure_active_composer_figure()

    def reference_changed(self) -> None:
        if self.project is None:
            return
        figure = self.current_figure()
        sync_panel_labels(figure,MM_TO_PX)
        for item in self.scene.items():
            if isinstance(item,ComposerTextItem) and item.annotation.properties.get('role')=='panel_label' and item.annotation.properties.get('follow_panel',True):
                item.set_position_without_callback(item.annotation.x_mm*MM_TO_PX,item.annotation.y_mm*MM_TO_PX)
        figure.touch()
        self.layout_model = figure.to_layout(self.layout_model.preset)
        self.project.composer_layout = self.layout_model.to_dict()
        self.project.touch()
        self.document_changed.emit()

    def annotation_changed(self) -> None:
        if self.project is None:
            return
        self.current_figure().touch()
        self.project.touch()
        self.document_changed.emit()

    def label_for_reference(self,ref_uid):
        return label_for_reference(self.current_figure(),ref_uid)

    def set_label_follow(self,label_uid,follow):
        figure=self.current_figure()
        label=next((a for a in figure.annotations if a.annotation_uid==label_uid and a.properties.get('role')=='panel_label'),None)
        if label is None:
            return False
        if follow and not any(r.ref_uid==label.properties.get('panel_ref_uid') for r in figure.panel_refs):
            return False
        label.properties['follow_panel']=bool(follow)
        sync_panel_labels(figure,MM_TO_PX)
        self.annotation_changed()
        self.render()
        return True

    def _renumber_auto_labels(self) -> None:
        refs = sorted(self.current_figure().panel_refs, key=lambda ref: (ref.y_mm, ref.x_mm, ref.layer_index))
        label_case = self.current_figure().global_style.get("panel_label_case", "lowercase")
        for index, ref in enumerate(refs):
            if ref.label_auto and not ref.label_locked:
                ref.figure_label = default_panel_label(index, label_case)

    def add_text_annotation(self, text: str = "Text", scene_pos: QPointF | None = None) -> AnnotationObject:
        figure = self.current_figure()
        x_mm = float((scene_pos.x() / MM_TO_PX) if scene_pos is not None else 8.0)
        y_mm = float((scene_pos.y() / MM_TO_PX) if scene_pos is not None else 8.0)
        annotation = AnnotationObject(
            type="text",
            x_mm=x_mm,
            y_mm=y_mm,
            properties={
                "content": text,
                "font_family": "Arial",
                "font_size_pt": 8.0,
                "font_bold": False,
                "font_italic": False,
                "color": "#000000",
                "rotation_deg": 0,
            },
            layer_index=self._next_layer_index(),
        )
        figure.annotations.append(annotation)
        figure.append_audit("add_text_annotation", text)
        self.annotation_changed()
        self.render()
        return annotation

    def add_shape_annotation(
        self,
        shape_type: str = "rectangle",
        scene_pos: QPointF | None = None,
        stroke_width_pt: float = 1.0,
        stroke_color: str = "#000000",
        fill_color: str = "#FFFFFF",
        width_mm: float = 18.0,
        height_mm: float = 12.0,
    ) -> AnnotationObject:
        figure = self.current_figure()
        x_mm = float((scene_pos.x() / MM_TO_PX) if scene_pos is not None else 10.0)
        y_mm = float((scene_pos.y() / MM_TO_PX) if scene_pos is not None else 10.0)
        resolved_shape = shape_type.lower()
        annotation = AnnotationObject(
            type="shape",
            x_mm=x_mm,
            y_mm=y_mm,
            properties={
                "shape_type": resolved_shape,
                "width_mm": float(width_mm),
                "height_mm": float(height_mm if resolved_shape != "line" else max(2.0, height_mm)),
                "stroke_width_pt": float(stroke_width_pt),
                "stroke_color": stroke_color,
                "fill_color": fill_color,
                "fill_alpha": 255,
            },
            layer_index=self._next_layer_index(),
        )
        figure.annotations.append(annotation)
        figure.append_audit("add_shape_annotation", resolved_shape)
        self.annotation_changed()
        self.render()
        return annotation

    def delete_selected(self) -> None:
        if self.project is None:
            return
        selected = self.scene.selectedItems()
        if not selected:
            return
        ref_uids = {str(item.data(1)) for item in selected if item.data(0) == "panel_ref"}
        annotation_uids = {str(item.data(1)) for item in selected if item.data(0) == "annotation"}
        figure = self.current_figure()
        if ref_uids:
            figure.panel_refs = [ref for ref in figure.panel_refs if ref.ref_uid not in ref_uids or ref.locked]
            remaining={r.ref_uid for r in figure.panel_refs}
            figure.annotations=[a for a in figure.annotations if not(a.properties.get('role')=='panel_label' and a.properties.get('follow_panel',True) and a.properties.get('panel_ref_uid') not in remaining)]
            self._renumber_auto_labels()
        if annotation_uids:
            for annotation in figure.annotations:
                if annotation.annotation_uid in annotation_uids and not annotation.locked and annotation.properties.get('role')=='panel_label':
                    ref=next((r for r in figure.panel_refs if r.ref_uid==annotation.properties.get('panel_ref_uid')),None)
                    if ref is not None:ref.label_visible=False
            figure.annotations = [annotation for annotation in figure.annotations if annotation.annotation_uid not in annotation_uids or annotation.locked]
        figure.append_audit("delete_composer_selection", f"panel_refs={len(ref_uids)}; annotations={len(annotation_uids)}")
        self.reference_changed()
        self.render()

    def resize_selected(self, factor: float) -> None:
        if self.project is None:
            return
        changed = False
        for item in self.scene.selectedItems():
            if item.data(0) != "panel_ref":
                continue
            ref_uid = str(item.data(1))
            ref = next((candidate for candidate in self.current_figure().panel_refs if candidate.ref_uid == ref_uid), None)
            if ref is None or ref.locked:
                continue
            ref.width_mm = max(5.0, ref.width_mm * factor)
            ref.height_mm = max(5.0, ref.height_mm * factor)
            changed = True
        if changed:
            self.reference_changed()
            self.render()

    def _next_layer_index(self) -> int:
        figure = self.current_figure()
        layers = [ref.layer_index for ref in figure.panel_refs] + [annotation.layer_index for annotation in figure.annotations]
        return max(layers, default=-1) + 1

    def _selected_records(self) -> list[EditableLayoutRecord]:
        if self.project is None:
            return []
        records: list[EditableLayoutRecord] = []
        figure = self.current_figure()
        refs = {ref.ref_uid: ref for ref in figure.panel_refs}
        annotations = {annotation.annotation_uid: annotation for annotation in figure.annotations}
        for item in self.scene.selectedItems():
            kind = item.data(0)
            uid = str(item.data(1))
            if kind == "panel_ref" and uid in refs:
                ref = refs[uid]
                records.append(
                    EditableLayoutRecord(
                        kind="panel_ref",
                        uid=uid,
                        model=ref,
                        x=ref.x_mm,
                        y=ref.y_mm,
                        width=ref.width_mm,
                        height=ref.height_mm,
                        locked=ref.locked,
                        can_resize=True,
                    )
                )
            elif kind == "annotation" and uid in annotations:
                annotation = annotations[uid]
                if annotation.type == "shape":
                    width = float(annotation.properties.get("width_mm", item.sceneBoundingRect().width() / MM_TO_PX))
                    height = float(annotation.properties.get("height_mm", item.sceneBoundingRect().height() / MM_TO_PX))
                    can_resize = True
                else:
                    rect = item.sceneBoundingRect()
                    width = rect.width() / MM_TO_PX
                    height = rect.height() / MM_TO_PX
                    can_resize = False
                records.append(
                    EditableLayoutRecord(
                        kind=annotation.type,
                        uid=uid,
                        model=annotation,
                        x=annotation.x_mm,
                        y=annotation.y_mm,
                        width=width,
                        height=height,
                        locked=annotation.locked,
                        can_resize=can_resize,
                    )
                )
        return records

    def _commit_record_geometry(self, record: EditableLayoutRecord) -> None:
        model = record.model
        if getattr(model, "locked", False):
            return
        model.x_mm = float(record.x)
        model.y_mm = float(record.y)
        if isinstance(model, PanelReference):
            model.width_mm = max(5.0, float(record.width))
            model.height_mm = max(5.0, float(record.height))
        elif isinstance(model, AnnotationObject) and model.type == "shape":
            model.properties["width_mm"] = max(5.0, float(record.width))
            model.properties["height_mm"] = max(5.0, float(record.height))

    def _commit_records_geometry(self, records: list[EditableLayoutRecord]) -> None:
        for record in records:
            self._commit_record_geometry(record)

    def align_selected(self, mode: str) -> None:
        records = self._selected_records()
        if not align_records(records, mode):
            return
        self._commit_records_geometry(records)
        self.current_figure().append_audit("align_composer_selection", mode)
        self.reference_changed()
        self.render()

    def distribute_selected(self, orientation: str) -> None:
        records = self._selected_records()
        if not distribute_records(records, orientation):
            return
        self._commit_records_geometry(records)
        self.current_figure().append_audit("distribute_composer_selection", orientation)
        self.reference_changed()
        self.render()

    def make_selected_same_size(self) -> None:
        records = self._selected_records()
        if not make_records_same_size(records):
            return
        self._commit_records_geometry(records)
        self.current_figure().append_audit("same_size_composer_selection", f"{len(records)} object(s)")
        self.reference_changed()
        self.render()

    def layer_selected(self, action: str) -> None:
        selected = [record.model for record in self._selected_records()]
        if not selected:
            return
        figure = self.current_figure()
        all_items = list(figure.panel_refs) + list(figure.annotations)
        if action == "front":
            top = max((item.layer_index for item in all_items), default=0)
            for offset, item in enumerate(selected, start=1):
                item.layer_index = top + offset
        elif action == "back":
            bottom = min((item.layer_index for item in all_items), default=0)
            for offset, item in enumerate(selected, start=1):
                item.layer_index = bottom - len(selected) + offset - 1
        elif action == "forward":
            for item in selected:
                item.layer_index += 1
        elif action == "backward":
            for item in selected:
                item.layer_index -= 1
        else:
            raise ValueError(f"Unsupported layer action: {action}")
        self._normalize_layers()
        figure.append_audit("layer_composer_selection", action)
        self.reference_changed()
        self.render()

    def _normalize_layers(self) -> None:
        figure = self.current_figure()
        all_items = sorted(list(figure.panel_refs) + list(figure.annotations), key=lambda item: item.layer_index)
        for index, item in enumerate(all_items):
            item.layer_index = index

    def toggle_lock_selected(self) -> None:
        records = self._selected_records()
        if not records:
            return
        should_lock = any(not getattr(record.model, "locked", False) for record in records)
        for record in records:
            record.model.locked = should_lock
        self.current_figure().append_audit("set_composer_selection_lock", str(should_lock))
        self.reference_changed()
        self.render()

    def hide_selected(self) -> None:
        records = self._selected_records()
        if not records:
            return
        for record in records:
            if not getattr(record.model, "locked", False):
                record.model.visible = False
        self.current_figure().append_audit("hide_composer_selection", str(len(records)))
        self.reference_changed()
        self.render()

    def show_all(self) -> None:
        figure = self.current_figure()
        for ref in figure.panel_refs:
            ref.visible = True
        for annotation in figure.annotations:
            annotation.visible = True
        figure.append_audit("show_all_composer_objects", "")
        self.reference_changed()
        self.render()

    def render(self, show_guides: bool = True, preserve_selection: bool = True) -> None:
        selected_uids = set()
        if preserve_selection:
            selected_uids = {
                str(item.data(1))
                for item in self.scene.selectedItems()
                if item.data(0) in {"panel_ref", "annotation"}
            }
        self.updating_scene = True
        self.scene.clear()
        try:
            figure = self.current_figure() if self.project is not None else None
            if figure is not None:
                sync_panel_labels(figure,MM_TO_PX)
            canvas = figure.canvas if figure is not None else {}
            width_mm = float(canvas.get("width_mm", self.layout_model.width_mm))
            height_mm = float(canvas.get("height_mm", self.layout_model.height_mm))
            self.paper_rect = QRectF(0, 0, width_mm * MM_TO_PX, height_mm * MM_TO_PX)
            paper = self.scene.addRect(self.paper_rect, QPen(QColor("#e2e4e8")), QBrush(QColor("white")))
            paper.setZValue(-10)

            if show_guides and self._guides_visible:
                self._render_layout_guides()

            if figure is not None:
                for ref in sorted(figure.panel_refs, key=lambda item: item.layer_index):
                    if not ref.visible:
                        continue
                    panel = self.project.get_panel(ref.panel_uid) if self.project is not None else None
                    if panel is None:
                        continue
                    pixmap = self._panel_pixmap(panel, int(ref.width_mm * MM_TO_PX), int(ref.height_mm * MM_TO_PX), ref=ref)
                    item = ComposerPanelItem(self, ref, panel, pixmap)
                    self.scene.addItem(item)
                    if ref.ref_uid in selected_uids:
                        item.setSelected(True)
                for annotation in sorted(figure.annotations, key=lambda item: item.layer_index):
                    if not label_is_visible(figure,annotation):
                        continue
                    if annotation.type == "text":
                        item = ComposerTextItem(self, annotation)
                    elif annotation.type == "shape":
                        item = ComposerShapeItem(self, annotation)
                    else:
                        continue
                    self.scene.addItem(item)
                    if annotation.annotation_uid in selected_uids:
                        item.setSelected(True)

            preview_margin = max(240.0,self.view.width()/max(.1,self._zoom_factor))
            self.scene.setSceneRect(self.paper_rect.adjusted(-preview_margin, -preview_margin, preview_margin, preview_margin))
            self._apply_canvas_zoom()
        finally:
            self.updating_scene = False

    def _render_layout_guides(self) -> None:
        for cell in self.layout_model.cells:
            rect = QRectF(
                cell.x_mm * MM_TO_PX,
                cell.y_mm * MM_TO_PX,
                cell.width_mm * MM_TO_PX,
                cell.height_mm * MM_TO_PX,
            )
            cell_rect = self.scene.addRect(rect, QPen(QColor("#d5d9df"), 0.8, Qt.PenStyle.DashLine), QBrush(Qt.BrushStyle.NoBrush))
            cell_rect.setZValue(-5)
            if not cell.panel_uuid:
                hint = self.scene.addText(tr("drop_panel_object"))
                hint.setData(0,'composer_guide_hint')
                hint.setDefaultTextColor(QColor("#888888"))
                hint.setTextWidth(rect.width())
                hint.setPos(rect.x(), rect.center().y() - 12)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.schedule_recenter_after_layout()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.schedule_recenter_after_layout()

    def _fit_target_rect(self) -> QRectF:
        return QRectF(self.paper_rect)

    def handle_first_view_paint(self) -> None:
        if not self._first_view_paint_pending:
            return
        self._first_view_paint_pending = False
        self.schedule_recenter_after_layout()

    def recenter_canvas(self) -> None:
        if self.paper_rect.isNull():
            return
        if self._fit_mode:
            self.fit_to_canvas()

    def schedule_recenter_after_layout(self) -> None:
        self._view_controller.schedule_fit()

    def _on_canvas_view_change(self, *, fit: bool) -> None:
        self._expand_preview_scene_rect()
        if fit:
            self._center_paper_in_view()

    def _expand_preview_scene_rect(self) -> None:
        margin = max(240.0, self.view.width() / max(0.1, self._zoom_factor))
        expanded = self.paper_rect.adjusted(-margin, -margin, margin, margin)
        current = QRectF(self.scene.sceneRect())
        if (
            abs(current.x() - expanded.x()) < 1.0
            and abs(current.y() - expanded.y()) < 1.0
            and abs(current.width() - expanded.width()) < 1.0
            and abs(current.height() - expanded.height()) < 1.0
        ):
            return
        self.scene.setSceneRect(expanded)

    def _center_paper_in_view(self) -> None:
        self.view.centerOn(self.paper_rect.center())
        for _ in range(3):
            mapped = self.view.mapFromScene(self.paper_rect).boundingRect()
            visible = self._visible_viewport_rect()
            dx = mapped.center().x() - visible.center().x()
            dy = mapped.center().y() - visible.center().y()
            if abs(dx) <= 1 and abs(dy) <= 1:
                return
            self.view.horizontalScrollBar().setValue(self.view.horizontalScrollBar().value() + int(round(dx)))
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().value() + int(round(dy)))

    def _visible_viewport_rect(self) -> QRect:
        viewport = self.view.viewport()
        rect = viewport.visibleRegion().boundingRect()
        if rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            rect = viewport.rect()
        return rect

    def _panel_item(self, panel, rect: QRectF, ref: PanelReference | None = None) -> QGraphicsItem:
        pixmap = self._panel_pixmap(panel, int(rect.width() - 12), int(rect.height() - 12), ref=ref)
        if pixmap is not None:
            item = QGraphicsPixmapItem(pixmap)
            scale = min((rect.width() - 12) / pixmap.width(), (rect.height() - 12) / pixmap.height())
            item.setScale(scale)
            item.setPos(rect.x() + 6, rect.y() + 18)
            return item

        placeholder = QGraphicsTextItem(f"{panel.panel_id}\n{panel.panel_type}")
        placeholder.setDefaultTextColor(QColor("#333333"))
        placeholder.setTextWidth(rect.width() - 12)
        placeholder.setPos(rect.x() + 6, rect.y() + rect.height() / 2 - 24)
        return placeholder

    def _panel_pixmap(self, panel, width: int, height: int, *, ref: PanelReference | None = None) -> QPixmap | None:
        try:
            plot_bytes = self._plot_payload_bytes_for_ref(
                panel,
                ref,
                "svg",
                max(width, 1),
                max(height, 1),
                COMPOSER_PREVIEW_DPI,
            )
            if plot_bytes:
                renderer = QSvgRenderer(QByteArray(plot_bytes))
                if renderer.isValid():
                    size = renderer.defaultSize()
                    scale = COMPOSER_PREVIEW_DPI / 72.0
                    image = QImage(max(1, round(size.width() * scale)), max(1, round(size.height() * scale)), QImage.Format.Format_ARGB32)
                    image.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(image)
                    renderer.render(painter)
                    painter.end()
                    return QPixmap.fromImage(image)
            if isinstance(panel, ExternalImagePanel):
                source_path = self._external_source_path(panel)
                suffix = compound_suffix(source_path) if source_path is not None else ""
                if source_path is not None and source_path.exists() and suffix in COMPOSER_NATIVE_RASTER_SUFFIXES:
                    # Canvas panels retain native source dimensions; OME inputs
                    # use ExternalImagePanel's existing multichannel merge.
                    return self._native_external_pixmap(panel, source_path, suffix)
            source_dpi = int(panel.data.get("source_dpi", 0) or 0) if hasattr(panel, "data") else 0
            if isinstance(panel, ExternalImagePanel) and bool(panel.data.get("is_vector")):
                preview_dpi = COMPOSER_PREVIEW_DPI
            else:
                preview_dpi = min(COMPOSER_MAX_PREVIEW_DPI, max(COMPOSER_PREVIEW_DPI, source_dpi))
            image = panel.render_preview(max(width, 1) / MM_TO_PX, max(height, 1) / MM_TO_PX, dpi=preview_dpi)
        except (FileNotFoundError, NotImplementedError, ValueError):
            return None
        if image is None or image.isNull():
            return None
        return QPixmap.fromImage(image)

    def _plot_payload_for_ref(self, panel, ref: PanelReference | None) -> dict | None:
        if is_image_like_composer_panel(panel):
            return None
        if isinstance(panel, QPCRPanel):
            return qpcr_live_plot_payload(panel, chart_kind=getattr(ref, "chart_kind", None))
        payload = panel_plot_payload(panel)
        if payload is not None:
            return payload
        if not is_statistical_composer_candidate(panel) or self.project is None:
            return None
        source_uuid = str(getattr(panel, "data", {}).get("source_panel_uuid") or "")
        source_panel = self.project.get_panel(source_uuid) if source_uuid else None
        if source_panel is None:
            return None
        try:
            from ui.layout_adapters.chart_adapter import plot_payload_for_export_item
            from core.chart_payload import layout_export_item_from_panel

            export_item = layout_export_item_from_panel(panel)
            current_hash = self._source_panel_context_hash(panel, source_panel)
            return hydrate_legacy_plot_payload(
                panel,
                source_panel=source_panel,
                current_source_context_hash=current_hash,
                plot_payload_for_export_item=plot_payload_for_export_item if export_item is not None else None,
            )
        except (ImportError, TypeError, ValueError):
            return None

    def _plot_svg_text_overrides_for_ref(self, panel, ref: PanelReference | None) -> dict:
        data = getattr(panel, "data", {})
        if isinstance(data, dict) and isinstance(data.get("svg_text_overrides"), dict):
            return normalize_svg_text_overrides(data.get("svg_text_overrides", {}))
        if isinstance(panel, QPCRPanel):
            target = str((qpcr_live_plot_payload(panel, chart_kind=getattr(ref, "chart_kind", None)) or {}).get("target", ""))
            for record in panel.layout_plot_records():
                if str(record.get("target", "")) == target:
                    return normalize_svg_text_overrides(record.get("svg_text_overrides", {}))
        return {}

    def _plot_payload_bytes_for_ref(
        self,
        panel,
        ref: PanelReference | None,
        fmt: str,
        width: int,
        height: int,
        dpi: int,
    ) -> bytes | None:
        payload = self._plot_payload_for_ref(panel, ref)
        if payload is None:
            if isinstance(panel, ExternalImagePanel) and fmt == "svg":
                source = self._external_source_path(panel)
                if source is not None and source.suffix.lower() == ".svg" and source.exists():
                    return apply_svg_text_overrides(source.read_bytes(),
                        ref.chart_text_overrides.get("svg", {}) if ref is not None else {})
            return None
        kind = effective_composer_chart_kind(panel, ref, payload)
        kind = normalize_chart_kind(kind, default=kind)
        svg_text_overrides = self._plot_svg_text_overrides_for_ref(panel, ref)
        key = plot_payload_cache_key(
            panel,
            ref,
            payload,
            chart_kind=kind,
            width=max(1, int(width)),
            height=max(1, int(height)),
            dpi=max(72, int(dpi)),
            svg_text_overrides=svg_text_overrides,
        ) + (str(fmt).lower().lstrip("."), repr(getattr(ref, "chart_text_overrides", {}).get(kind, {})))
        cached = self._plot_payload_bytes_cache.get(key)
        if cached is not None:
            self._plot_payload_bytes_cache.move_to_end(key)
            return cached
        try:
            rendered = render_plot_payload_bytes(
                payload,
                fmt=fmt,
                chart_kind=kind,
                width_mm=max(0.1, float(width) / MM_TO_PX),
                height_mm=max(0.1, float(height) / MM_TO_PX),
                dpi=max(72, int(dpi)),
                svg_text_overrides=svg_text_overrides,
            )
        except (TypeError, ValueError, KeyError, RuntimeError):
            return None
        if fmt == "svg" and ref is not None:
            rendered = apply_svg_text_overrides(rendered, ref.chart_text_overrides.get(kind, {}))
        self._plot_payload_bytes_cache[key] = rendered
        self._plot_payload_bytes_cache.move_to_end(key)
        while len(self._plot_payload_bytes_cache) > self.PREVIEW_CACHE_LIMIT:
            self._plot_payload_bytes_cache.popitem(last=False)
        return rendered

    def panel_thumbnail(self, panel, width: int = 96, height: int = 64) -> QPixmap | None:
        # Shelf thumbnails are intentionally bounded.  They are not canvas
        # assets and must not retain an additional full-resolution copy.
        thumbnail_key = None
        if not isinstance(panel, ExternalImagePanel):
            thumbnail_key = ('panel-thumbnail', panel.panel_uuid, width, height,
                             repr((panel.panel_id, panel.data, panel.analysis, panel.style)))
            cached = self._pixmap_cache.get(thumbnail_key)
            if cached is not None:
                self._pixmap_cache.move_to_end(thumbnail_key)
                return cached
        if isinstance(panel, ExternalImagePanel):
            source_path = self._external_source_path(panel)
            if source_path is None or not source_path.exists():
                pixmap = None
            else:
                suffix = compound_suffix(source_path)
                if suffix in OME_RASTER_SUFFIXES:
                    pixmap = self._native_external_pixmap(panel, source_path, suffix)
                else:
                    pixmap = self._external_pixmap(source_path, width, height)
        else:
            try:
                image = panel.render_preview(max(width, 1) / MM_TO_PX, max(height, 1) / MM_TO_PX, dpi=COMPOSER_PREVIEW_DPI)
            except (FileNotFoundError, NotImplementedError, ValueError):
                image = None
            pixmap = QPixmap.fromImage(image) if image is not None and not image.isNull() else None
        if pixmap is None or pixmap.isNull():
            return None
        thumbnail = pixmap.scaled(width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        if thumbnail_key is not None:
            self._store_cached_pixmap(thumbnail_key, thumbnail)
        return thumbnail

    def _external_pixmap(self, path: Path, width: int, height: int) -> QPixmap | None:
        suffix = compound_suffix(path)
        if suffix == ".svg":
            renderer = QSvgRenderer(str(path))
            if not renderer.isValid():
                return None
            image = QImage(QSize(width, height), QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            renderer.render(painter)
            painter.end()
            return QPixmap.fromImage(image)
        if suffix == ".pdf":
            document = QPdfDocument(None)
            if document.load(str(path)) != QPdfDocument.Error.None_:
                return None
            page_size = document.pagePointSize(0)
            aspect = page_size.width() / page_size.height() if page_size.height() else 1.0
            render_width = max(width, 64)
            render_height = max(int(render_width / aspect), 64)
            image = document.render(0, QSize(render_width, render_height))
            return QPixmap.fromImage(image)
        if suffix in COMMON_RASTER_SUFFIXES:
            return self._cached_raster_pixmap(path, width, height)
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap(str(path))
        if pixmap.isNull():
            return None
        return pixmap

    def _preview_bucket(self, width: int, height: int) -> int:
        requested = max(width, height, 1024)
        for bucket in (1024, 2048):
            if requested <= bucket:
                return bucket
        return 2048

    def _file_cache_identity(self, path: Path) -> tuple[str, int, int]:
        stat = path.stat()
        return (str(path.resolve()), int(stat.st_size), int(stat.st_mtime_ns))

    def _cached_raster_pixmap(self, path: Path, width: int, height: int) -> QPixmap | None:
        try:
            identity = self._file_cache_identity(path)
        except OSError:
            return None
        bucket = self._preview_bucket(width, height)
        reusable = [
            (key, pixmap)
            for key, pixmap in self._pixmap_cache.items()
            if key[:3] == identity and key[3] >= bucket and not pixmap.isNull()
        ]
        if reusable:
            key, pixmap = min(reusable, key=lambda item: item[0][3])
            self._pixmap_cache.move_to_end(key)
            return pixmap.scaled(max(width, 1), max(height, 1), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        suffix = path.suffix.lower()
        if suffix in {".tif", ".tiff"}:
            pixmap = self._tiff_preview_pixmap(path, bucket, bucket)
        else:
            pixmap = self._pillow_preview_pixmap(path, bucket, bucket)
        if pixmap is None or pixmap.isNull():
            return None
        self._store_cached_pixmap((*identity, bucket), pixmap)
        return pixmap.scaled(max(width, 1), max(height, 1), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _native_external_pixmap(self, panel: ExternalImagePanel, path: Path, suffix: str) -> QPixmap | None:
        if suffix in COMMON_RASTER_SUFFIXES:
            return self._full_raster_pixmap(path)
        if suffix not in OME_RASTER_SUFFIXES:
            return None

        def load_merged_microscopy() -> QPixmap:
            try:
                image = panel.render_native_microscopy()
            except (FileNotFoundError, ValueError):
                return QPixmap()
            return QPixmap.fromImage(image) if not image.isNull() else QPixmap()

        return self._full_raster_pixmap(path, loader=load_merged_microscopy)

    def _full_raster_pixmap(self, path: Path, loader: Callable[[], QPixmap] | None = None) -> QPixmap | None:
        try:
            identity = self._file_cache_identity(path)
        except OSError:
            return None
        cached = self._full_raster_cache.get(identity)
        if cached is not None and not cached.isNull():
            self._full_raster_cache.move_to_end(identity)
            return cached
        pixmap = loader() if loader is not None else self._read_native_raster_pixmap(path)
        if pixmap.isNull():
            return None
        self._store_full_raster_pixmap(identity, pixmap)
        return pixmap

    def _read_native_raster_pixmap(self, path: Path) -> QPixmap:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            return QPixmap(str(path))
        return QPixmap.fromImage(image)

    def _store_full_raster_pixmap(self, key: tuple[str, int, int], pixmap: QPixmap) -> None:
        self._full_raster_cache[key] = pixmap
        self._full_raster_cache.move_to_end(key)
        while len(self._full_raster_cache) > self.FULL_RASTER_CACHE_LIMIT:
            self._full_raster_cache.popitem(last=False)

    def _store_cached_pixmap(self, key: tuple[object, ...], pixmap: QPixmap) -> None:
        self._pixmap_cache[key] = pixmap
        self._pixmap_cache.move_to_end(key)
        while len(self._pixmap_cache) > self.PREVIEW_CACHE_LIMIT:
            self._pixmap_cache.popitem(last=False)

    def _tiff_preview_pixmap(self, path: Path, width: int, height: int) -> QPixmap | None:
        try:
            import tifffile

            array = tifffile.memmap(path)
            if array.ndim < 2:
                return None
            if array.ndim > 3:
                array = np.asarray(array).reshape((-1, array.shape[-2], array.shape[-1]))[0]
            elif array.ndim == 3 and array.shape[-1] not in {3, 4}:
                array = np.asarray(array)[0]
            stride = max(1, int(max(array.shape[0] / max(height, 1), array.shape[1] / max(width, 1))))
            sample = np.asarray(array[::stride, ::stride])
            return self._numpy_preview_pixmap(sample, width, height)
        except Exception:
            return self._pillow_preview_pixmap(path, width, height)

    def _pillow_preview_pixmap(self, path: Path, width: int, height: int) -> QPixmap | None:
        try:
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as image:
                image.thumbnail((max(width, 1), max(height, 1)), Image.Resampling.LANCZOS)
                image = image.convert("RGBA")
                data = image.tobytes("raw", "RGBA")
                qimage = QImage(data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888).copy()
                pixmap = QPixmap.fromImage(qimage)
                return None if pixmap.isNull() else pixmap
        except Exception:
            return None

    def _numpy_preview_pixmap(self, array: np.ndarray, width: int, height: int) -> QPixmap | None:
        if array.size == 0:
            return None
        sample = np.asarray(array)
        if sample.ndim == 2:
            rgb = self._normalize_channel(sample)
            rgba = np.stack([rgb, rgb, rgb, np.full_like(rgb, 255)], axis=-1)
        elif sample.ndim == 3:
            if sample.shape[-1] >= 4:
                rgba = sample[..., :4]
            elif sample.shape[-1] == 3:
                alpha = np.full(sample.shape[:2] + (1,), 255, dtype=np.uint8)
                rgba = np.concatenate([sample[..., :3], alpha], axis=-1)
            else:
                rgb = self._normalize_channel(sample[..., 0])
                rgba = np.stack([rgb, rgb, rgb, np.full_like(rgb, 255)], axis=-1)
        else:
            rgb = self._normalize_channel(sample.reshape(sample.shape[-2], sample.shape[-1]))
            rgba = np.stack([rgb, rgb, rgb, np.full_like(rgb, 255)], axis=-1)
        if rgba.dtype != np.uint8:
            rgba = self._normalize_rgba(rgba)
        rgba = np.ascontiguousarray(rgba)
        qimage = QImage(rgba.data, rgba.shape[1], rgba.shape[0], rgba.strides[0], QImage.Format.Format_RGBA8888).copy()
        pixmap = QPixmap.fromImage(qimage)
        if pixmap.isNull():
            return None
        return pixmap.scaled(max(width, 1), max(height, 1), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

    def _normalize_channel(self, channel: np.ndarray) -> np.ndarray:
        if channel.dtype == np.uint8:
            return np.asarray(channel)
        values = channel.astype(np.float32, copy=False)
        minimum = float(np.nanmin(values))
        maximum = float(np.nanmax(values))
        if maximum <= minimum:
            return np.zeros(channel.shape, dtype=np.uint8)
        return np.clip((values - minimum) / (maximum - minimum) * 255.0, 0, 255).astype(np.uint8)

    def _normalize_rgba(self, rgba: np.ndarray) -> np.ndarray:
        channels = []
        for index in range(min(4, rgba.shape[-1])):
            channels.append(self._normalize_channel(rgba[..., index]))
        while len(channels) < 4:
            channels.append(np.full(rgba.shape[:2], 255, dtype=np.uint8))
        return np.stack(channels[:4], axis=-1)

    def preflight_export(self, dpi: int = 300) -> list[dict[str, str]]:
        if self.project is None:
            return [{"severity": "error", "message": "No active project."}]
        figure = self.current_figure()
        canvas_width = float(figure.canvas.get("width_mm", self.layout_model.width_mm))
        canvas_height = float(figure.canvas.get("height_mm", self.layout_model.height_mm))
        page_bounds=QRectF(0,0,canvas_width*MM_TO_PX,canvas_height*MM_TO_PX)
        annotation_items={str(item.data(1)):item for item in self.scene.items()
                          if item.data(0)=='annotation' and item.parentItem() is None}
        messages: list[dict[str, str]] = []
        visible_labels: list[str] = []
        hidden_count = 0
        for ref in figure.panel_refs:
            first_message=len(messages)
            if not ref.visible:
                hidden_count += 1
                continue
            if ref.x_mm < 0 or ref.y_mm < 0 or ref.x_mm + ref.width_mm > canvas_width or ref.y_mm + ref.height_mm > canvas_height:
                messages.append({"severity": "warning", "message": f"{ref.figure_label or ref.panel_uid} extends outside the canvas."})
            if ref.label_visible:
                if ref.figure_label:
                    visible_labels.append(ref.figure_label)
                else:
                    messages.append({"severity": "warning", "message": f"{ref.panel_uid} has no figure label."})
            panel = self.project.get_panel(ref.panel_uid)
            if isinstance(panel, ExternalImagePanel):
                source_path = self._external_source_path(panel)
                if source_path is None or not source_path.exists():
                    messages.append({"severity": "error", "message": f"Missing external source for {panel.panel_id}."})
                elif source_path.suffix.lower() not in {".svg", ".pdf"}:
                    width_px, height_px = self._source_pixel_size(source_path)
                    if width_px and height_px:
                        export_width_in = max(ref.width_mm / MM_PER_INCH, 0.001)
                        export_height_in = max(ref.height_mm / MM_PER_INCH, 0.001)
                        effective_dpi = min(width_px / export_width_in, height_px / export_height_in)
                        if effective_dpi < dpi:
                            messages.append({"severity": "warning", "message": f"{panel.panel_id} effective resolution is {effective_dpi:.0f} DPI."})
                source_panel_uuid = str(panel.data.get("source_panel_uuid", "")).strip()
                if source_panel_uuid:
                    source_panel = self.project.get_panel(source_panel_uuid)
                    if source_panel is None:
                        messages.append({"severity": "warning", "message": f"{panel.panel_id} source panel is no longer in the project."})
                    elif self._external_panel_is_stale(panel, source_panel, source_path):
                        messages.append({"severity": "warning", "message": f"{panel.panel_id} generated asset may be stale relative to its source panel."})
            for message in messages[first_message:]:message['object_uid']=ref.ref_uid
        duplicates = sorted({label for label in visible_labels if visible_labels.count(label) > 1})
        for label in duplicates:
            messages.append({"severity": "warning", "message": f"Duplicate panel label: {label}."})
        if hidden_count:
            messages.append({"severity": "info", "message": f"{hidden_count} hidden object(s) will not be exported."})
        for annotation in figure.annotations:
            if not annotation.visible:
                hidden_count += 1
                continue
            if annotation.type == "text":
                font_size = float(annotation.properties.get("font_size_pt", 8.0))
                if font_size < 5.0:
                    messages.append({"severity": "warning", "message": f"Text annotation below 5 pt: {annotation.properties.get('content', '')}.",
                                     "object_uid":annotation.annotation_uid})
            if annotation.type in {'text','shape'}:
                item=annotation_items.get(annotation.annotation_uid)
                if item is None:
                    obj=annotation_layout_object(annotation,MM_TO_PX)
                    item=SharedTextItem(obj) if annotation.type=='text' else SharedShapeItem(obj)
                # Own bounds include rotation, text height, stroke and arrowheads,
                # but never child editor resize handles.
                if not page_bounds.contains(item.sceneBoundingRect()):
                    label='Text' if annotation.type=='text' else 'Shape'
                    detail=annotation.properties.get('content',annotation.properties.get('shape_type',''))
                    messages.append({"severity":"warning","message":f"{label} annotation outside canvas: {detail}.",
                                     "object_uid":annotation.annotation_uid})
        figure.export_settings["last_preflight"] = {"dpi": int(dpi), "messages": messages, "timestamp": utc_now_iso()}
        return messages

    def export_figure(self, path: str | Path, dpi: int = 300, include_source_data: bool = False) -> Path:
        target = Path(path)
        suffix = target.suffix.lower()
        if self.project is None:
            raise RuntimeError("ComposerCanvas has no project.")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.preflight_export(dpi=dpi)
        try:
            if suffix == ".pdf":
                self._export_pdf(target, dpi)
            elif suffix == ".svg":
                width_scene, height_scene = self._export_scene_size()
                generator = QSvgGenerator()
                generator.setFileName(str(target))
                generator.setResolution(int(SVG_DPI))
                generator.setSize(QSize(round(width_scene), round(height_scene)))
                generator.setViewBox(QRectF(0, 0, width_scene, height_scene))
                generator.setTitle("ScieFig Composer Export")
                painter = QPainter(generator)
                self._paint_export(painter, dpi)
                painter.end()
                if self.current_figure().canvas.get('unit_contract')=='physical_mm':
                    import xml.etree.ElementTree as ET
                    document=ET.parse(target)
                    width_mm,height_mm=self._export_page_size_mm()
                    document.getroot().set('width',f'{width_mm:g}mm')
                    document.getroot().set('height',f'{height_mm:g}mm')
                    document.write(target,encoding='utf-8',xml_declaration=True)
            elif suffix in {".png", ".tif", ".tiff"}:
                self._export_raster(target, dpi)
            else:
                raise ValueError(f"Unsupported Composer export format: {suffix}")
        finally:
            self.render(show_guides=True)
        self._record_export(target, dpi)
        if include_source_data:
            self.export_source_data_package(target)
        return target

    def _canvas_size_mm(self) -> tuple[float, float]:
        figure = self.current_figure()
        return (
            float(figure.canvas.get("width_mm", self.layout_model.width_mm)),
            float(figure.canvas.get("height_mm", self.layout_model.height_mm)),
        )

    def _export_scene_size(self) -> tuple[float, float]:
        width_mm, height_mm = self._canvas_size_mm()
        return width_mm * MM_TO_PX, height_mm * MM_TO_PX

    def _export_page_size_mm(self) -> tuple[float, float]:
        if self.current_figure().canvas.get('unit_contract')=='physical_mm':
            return self._canvas_size_mm()
        width_scene, height_scene = self._export_scene_size()
        return width_scene / SVG_DPI * MM_PER_INCH, height_scene / SVG_DPI * MM_PER_INCH

    def _export_painter_scale(self,dpi):
        return dpi/(MM_PER_INCH*MM_TO_PX) if self.current_figure().canvas.get('unit_contract')=='physical_mm' else dpi/SVG_DPI

    def _export_pdf(self, target: Path, dpi: int) -> None:
        width_mm, height_mm = self._export_page_size_mm()
        writer = QPdfWriter(str(target))
        writer.setResolution(int(dpi))
        writer.setPageSize(QPageSize(QSizeF(width_mm, height_mm), QPageSize.Unit.Millimeter))
        writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
        painter = QPainter(writer)
        painter.scale(self._export_painter_scale(dpi),self._export_painter_scale(dpi))
        self._paint_export(painter, dpi)
        painter.end()

    def _export_raster(self, target: Path, dpi: int) -> None:
        width_mm, height_mm = self._export_page_size_mm()
        width_px = max(1, int(round(width_mm / MM_PER_INCH * dpi)))
        height_px = max(1, int(round(height_mm / MM_PER_INCH * dpi)))
        image = QImage(width_px, height_px, QImage.Format.Format_ARGB32)
        dots_per_meter = int(round(dpi / 0.0254))
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.scale(self._export_painter_scale(dpi),self._export_painter_scale(dpi))
        self._paint_export(painter, dpi)
        painter.end()
        if not image.save(str(target)):
            raise ValueError(f"Could not save Composer export: {target}")

    def _paint_export(self, painter: QPainter, dpi: int) -> None:
        figure = self.current_figure()
        width_scene, height_scene = self._export_scene_size()
        painter.fillRect(QRectF(0, 0, width_scene, height_scene), QColor("white"))
        panel_layers = [(ref.layer_index, "panel", ref) for ref in figure.panel_refs]
        annotation_layers = [(annotation.layer_index, "annotation", annotation) for annotation in figure.annotations]
        for _layer, kind, item in sorted(panel_layers + annotation_layers, key=lambda value: value[0]):
            if kind == "panel":
                self._paint_panel_ref(painter, item, dpi)
            else:
                self._paint_annotation(painter, item)

    def _paint_panel_ref(self, painter: QPainter, ref: PanelReference, dpi: int) -> None:
        if not ref.visible or self.project is None:
            return
        panel = self.project.get_panel(ref.panel_uid)
        if panel is None:
            return
        rect = QRectF(ref.x_mm * MM_TO_PX, ref.y_mm * MM_TO_PX, ref.width_mm * MM_TO_PX, ref.height_mm * MM_TO_PX)
        if isinstance(panel, ExternalImagePanel):
            self._paint_external_panel(painter, panel, rect, dpi, ref=ref)
        elif isinstance(panel, QPCRPanel):
            self._paint_qpcr_panel(painter, panel, rect, dpi, ref=ref)
        else:
            self._paint_raster_panel(painter, panel, rect, dpi)
        if ref.border:
            painter.save()
            painter.setPen(QPen(QColor(ref.border_color), ref.border_width_pt))
            painter.drawRect(rect)
            painter.restore()

    def _paint_external_panel(
        self,
        painter: QPainter,
        panel: ExternalImagePanel,
        rect: QRectF,
        dpi: int,
        *,
        ref: PanelReference | None = None,
    ) -> None:
        plot_bytes = self._plot_payload_bytes_for_ref(
            panel,
            ref,
            "svg",
            max(1, int(rect.width())),
            max(1, int(rect.height())),
            dpi,
        )
        if plot_bytes:
            renderer = QSvgRenderer(QByteArray(plot_bytes))
            if renderer.isValid():
                renderer.render(painter, self._panel_content_rect(rect))
            return
        source_path = self._external_source_path(panel)
        if source_path is None or not source_path.exists():
            return
        suffix = compound_suffix(source_path)
        if suffix == ".svg":
            renderer = QSvgRenderer(str(source_path))
            if renderer.isValid():
                renderer.render(painter, self._panel_content_rect(rect))
            return
        if suffix in COMPOSER_NATIVE_RASTER_SUFFIXES:
            pixmap = self._native_external_pixmap(panel, source_path, suffix)
            if pixmap is None or pixmap.isNull():
                return
            content_rect = self._panel_content_rect(rect)
            target_rect = self._fit_rect(content_rect, pixmap.width() / max(pixmap.height(), 1))
            painter.drawPixmap(target_rect, pixmap, QRectF(pixmap.rect()))
            return
        try:
            image = panel.render_preview(rect.width() / MM_TO_PX, rect.height() / MM_TO_PX, dpi=dpi)
        except (FileNotFoundError, ValueError):
            return
        if image.isNull():
            return
        painter.drawImage(self._panel_content_rect(rect), image)

    def _paint_qpcr_panel(
        self,
        painter: QPainter,
        panel: QPCRPanel,
        rect: QRectF,
        dpi: int,
        *,
        ref: PanelReference | None = None,
    ) -> None:
        content_rect = self._panel_content_rect(rect)
        plot_bytes = self._plot_payload_bytes_for_ref(
            panel,
            ref,
            "svg",
            max(1, int(content_rect.width())),
            max(1, int(content_rect.height())),
            dpi,
        )
        if plot_bytes:
            renderer = QSvgRenderer(QByteArray(plot_bytes))
            if renderer.isValid():
                renderer.render(painter, content_rect)
            return
        try:
            source = panel.render_export(rect.width() / MM_TO_PX, rect.height() / MM_TO_PX, dpi=dpi, format="svg")
        except (NotImplementedError, ValueError):
            return
        if not isinstance(source, bytes):
            return
        renderer = QSvgRenderer(QByteArray(source))
        if renderer.isValid():
            renderer.render(painter, content_rect)

    def _paint_raster_panel(self, painter: QPainter, panel, rect: QRectF, dpi: int) -> None:
        content_rect = self._panel_content_rect(rect)
        try:
            image = panel.render_preview(rect.width() / MM_TO_PX, rect.height() / MM_TO_PX, dpi=dpi)
        except (FileNotFoundError, NotImplementedError, ValueError):
            return
        if image is None or image.isNull():
            return
        painter.drawImage(content_rect, image)

    def _paint_annotation(self, painter: QPainter, annotation: AnnotationObject) -> None:
        if not label_is_visible(self.current_figure(),annotation):
            return
        if annotation.type == "shape":
            self._paint_shape_annotation(painter, annotation)
            return
        if annotation.type != "text":
            return
        properties = annotation.properties
        painter.save()
        painter.translate(annotation.x_mm * MM_TO_PX, annotation.y_mm * MM_TO_PX)
        painter.rotate(float(properties.get("rotation_deg", 0.0)))
        SharedTextLayout.render(painter, annotation_layout_object(annotation, MM_TO_PX))
        painter.restore()

    def _paint_shape_annotation(self, painter: QPainter, annotation: AnnotationObject) -> None:
        painter.save()
        try:
            painter.translate(annotation.x_mm * MM_TO_PX, annotation.y_mm * MM_TO_PX)
            painter.rotate(float(annotation.properties.get("rotation_deg", 0.0)))
            item = SharedShapeItem(annotation_layout_object(annotation, MM_TO_PX))
            painter.setOpacity(painter.opacity() * item.opacity())
            item.paint(painter, None)
        finally:
            painter.restore()

    def _panel_content_rect(self, rect: QRectF) -> QRectF:
        return QRectF(rect.x() + 4.0, rect.y() + 12.0, max(1.0, rect.width() - 8.0), max(1.0, rect.height() - 16.0))

    def _draw_text_path(self, painter: QPainter, pos: QPointF, text: str, font: QFont, color: QColor) -> None:
        path = QPainterPath()
        path.addText(pos, font, text)
        painter.fillPath(path, color)

    def _fit_rect(self, rect: QRectF, aspect: float) -> QRectF:
        if aspect <= 0:
            return rect
        rect_aspect = rect.width() / rect.height() if rect.height() else aspect
        if rect_aspect > aspect:
            width = rect.height() * aspect
            return QRectF(rect.x() + (rect.width() - width) / 2, rect.y(), width, rect.height())
        height = rect.width() / aspect
        return QRectF(rect.x(), rect.y() + (rect.height() - height) / 2, rect.width(), height)

    def _external_source_path(self, panel: ExternalImagePanel) -> Path | None:
        image_path = panel.data.get("image_path") or getattr(panel, "raw_source_path", None)
        if image_path:
            return Path(str(image_path))
        return None

    def _panel_source_module(self, panel) -> str:
        if isinstance(panel, ExternalImagePanel):
            return panel.normalized_source_module()
        return str(getattr(panel, "source_module", "") or getattr(panel, "panel_type", "unknown"))

    def _panel_source_metadata(self, panel) -> dict:
        if isinstance(panel, ExternalImagePanel):
            return panel.normalized_source_metadata()
        return {
            "source_module": str(getattr(panel, "source_module", "") or getattr(panel, "panel_type", "unknown")),
            "source_panel_uuid": str(getattr(panel, "panel_uuid", "")),
        }

    def _external_panel_is_stale(self, panel: ExternalImagePanel, source_panel, source_path: Path | None) -> bool:
        if source_path is None or not source_path.exists():
            return False
        expected_hash = str(panel.data.get("source_context_hash", "")).strip()
        if not expected_hash:
            return False
        current_hash = self._source_panel_context_hash(panel, source_panel)
        if current_hash:
            return current_hash != expected_hash
        source_modified = self._parse_utc_timestamp(str(getattr(source_panel, "modified_at", "")))
        if source_modified is None:
            return False
        asset_modified = datetime.fromtimestamp(source_path.stat().st_mtime, UTC)
        return source_modified > asset_modified

    def _source_panel_context_hash(self, panel: ExternalImagePanel, source_panel) -> str:
        candidates = [
            panel.data.get("source_module", ""),
            panel.data.get("source_record_namespace", ""),
            getattr(source_panel, "source_module", ""),
            getattr(source_panel, "panel_type", ""),
        ]
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate or "").strip()
            if not key or key in seen:
                continue
            seen.add(key)
            calculator = self._source_context_hash_calculator(key)
            if calculator is None:
                continue
            try:
                return str(calculator(source_panel)).strip()
            except Exception:
                return ""
        return ""

    def _source_context_hash_calculator(self, source_key: str):
        key = source_key.strip().lower()
        if key == "wb_export_item" or key.startswith("wb_") or key == "wb":
            from ui.layout_adapters.wb_layout_adapter import layout_context_hash

            return layout_context_hash
        if key in {"ihc", "ihc_layout", "ihc_overlay", "ihc_statistics"} or key.startswith("ihc_"):
            from ui.layout_adapters.ihc_layout_adapter import layout_context_hash

            return layout_context_hash
        if key in {"brightfield", "brightfield_layout", "brightfield_overlay", "brightfield_statistics"} or key.startswith("brightfield_"):
            from ui.layout_adapters.brightfield_layout_adapter import layout_context_hash

            return layout_context_hash
        if key in {"qpcr", "qpcr_layout", "qpcr_plot"} or key.startswith("qpcr_"):
            from ui.layout_adapters.qpcr_layout_adapter import layout_context_hash

            return layout_context_hash
        if key in {"fluorescence", "fluorescence_layout", "fluorescence_layout_plot", "fluorescence_plot"} or key.startswith("fluorescence_"):
            from ui.layout_adapters.fluorescence_layout_adapter import layout_context_hash

            return layout_context_hash
        return None

    def _parse_utc_timestamp(self, value: str) -> datetime | None:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _source_pixel_size(self, path: Path) -> tuple[int, int]:
        suffix = path.suffix.lower()
        try:
            if suffix in {".tif", ".tiff"}:
                import tifffile

                with tifffile.TiffFile(path) as tif:
                    shape = tif.series[0].shape
                    return int(shape[1]), int(shape[0])
            from PIL import Image

            Image.MAX_IMAGE_PIXELS = None
            with Image.open(path) as image:
                return int(image.width), int(image.height)
        except Exception:
            return 0, 0

    def _export_source_qimage(self, path: Path, width: int, height: int) -> QImage | None:
        suffix = path.suffix.lower()
        if suffix in {".tif", ".tiff"}:
            pixmap = self._tiff_preview_pixmap(path, width, height)
            return pixmap.toImage() if pixmap is not None and not pixmap.isNull() else None
        pixmap = self._pillow_preview_pixmap(path, width, height)
        return pixmap.toImage() if pixmap is not None and not pixmap.isNull() else None

    def _record_export(self, target: Path, dpi: int) -> None:
        if self.project is None:
            return
        figure = self.current_figure()
        record = {
            "timestamp": utc_now_iso(),
            "path": str(target),
            "format": target.suffix.lower().lstrip("."),
            "dpi": int(dpi),
        }
        figure.export_settings["last_export"] = record
        figure.export_settings.setdefault("history", []).append(record)
        figure.append_audit("export_composer_figure", f"{target.name}; {dpi}dpi")
        self.project.append_audit("export_composer_figure", target.name)

    def export_source_data_package(self, figure_path: str | Path) -> Path:
        if self.project is None:
            raise RuntimeError("ComposerCanvas has no project.")
        target = Path(figure_path)
        figure = self.current_figure()
        source_dir = target.with_name(f"{target.stem}_source")
        external_dir = source_dir / "external_source_files"
        external_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / f"{target.stem}_composer.json").write_text(json.dumps(figure.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        panel_payloads = []
        source_metadata_payloads = []
        for ref in figure.panel_refs:
            panel = self.project.get_panel(ref.panel_uid)
            if panel is None:
                continue
            panel_payloads.append(panel.to_dict())
            source_metadata_payload = {
                "ref_uid": ref.ref_uid,
                "panel_uid": ref.panel_uid,
                "panel_id": panel.panel_id,
                "source_module": self._panel_source_module(panel),
                "source_metadata": self._panel_source_metadata(panel),
            }
            if isinstance(panel, ExternalImagePanel):
                source_path = self._external_source_path(panel)
                if source_path and source_path.exists():
                    panel_external_dir = external_dir / panel.panel_uuid
                    panel_external_dir.mkdir(parents=True, exist_ok=True)
                    copied_path = panel_external_dir / source_path.name
                    copy2(source_path, copied_path)
                    legacy_path = external_dir / source_path.name
                    if not legacy_path.exists():
                        copy2(source_path, legacy_path)
                    source_metadata_payload["copied_source_path"] = copied_path.relative_to(source_dir).as_posix()
            source_metadata_payloads.append(source_metadata_payload)
        (source_dir / f"{target.stem}_panels.json").write_text(json.dumps(panel_payloads, ensure_ascii=False, indent=2), encoding="utf-8")
        (source_dir / f"{target.stem}_source_metadata.json").write_text(json.dumps(source_metadata_payloads, ensure_ascii=False, indent=2), encoding="utf-8")
        audit_lines = [json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in figure.audit_log]
        audit_lines.extend(json.dumps(entry.to_dict(), ensure_ascii=False, sort_keys=True) for entry in self.project.audit_log)
        (source_dir / f"{target.stem}_audit.log").write_text("\n".join(audit_lines) + ("\n" if audit_lines else ""), encoding="utf-8")
        (source_dir / f"{target.stem}_preflight.json").write_text(json.dumps(figure.export_settings.get("last_preflight", {}), ensure_ascii=False, indent=2), encoding="utf-8")
        figure.append_audit("export_source_data_package", source_dir.name)
        return source_dir
