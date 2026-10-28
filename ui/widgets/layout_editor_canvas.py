from __future__ import annotations

from collections.abc import Callable
from typing import Iterable

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QVBoxLayout, QWidget

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

from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle
from ui.widgets.layout_editing import EditableLayoutRecord, align_records, distribute_records, make_records_same_size, move_records
from ui.widgets.layout_editor_items import (
    DATA_ROLE_MARKER,
    DATA_ROLE_OBJECT_ID,
    DATA_ROLE_OBJECT_TYPE,
    LAYOUT_EDITOR_ITEM_MARKER,
    LAYOUT_EDITOR_RESIZE_HANDLE_MARKER,
    SharedShapeItem,
    LayoutEditorTextItem,
    LayoutEditorImageProxyItem,
    layout_editor_item_for,
    layout_editor_items,
)
from ui.widgets.editable_text_item import clear_graphics_scene_for_cleanup, register_graphics_scene_for_cleanup


LayoutAssetProvider = Callable[[LayoutObject], QPixmap | QImage | bytes | None]
SvgTextChangedCallback = Callable[[], None]


class LayoutEditorGraphicsView(CanvasGraphicsView):
    def __init__(self, scene: QGraphicsScene, parent: QWidget | None = None) -> None:
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if is_canvas_text_editing(self):
            super().keyPressEvent(event)
            return
        if handle_canvas_space_press(self, event):
            return
        if handle_canvas_zoom_shortcut(self, event):
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if handle_canvas_space_release(self, event):
            return
        super().keyReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if handle_canvas_wheel(self, event):
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if handle_canvas_pan_press(self, event):
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if handle_canvas_pan_move(self, event):
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if handle_canvas_pan_release(self, event):
            return
        super().mouseReleaseEvent(event)
        parent = canvas_owner_of(self)
        callback = getattr(parent, "_layout_changed_callback", None)
        if callable(callback) and getattr(parent, "canvas_spec", None) is not None:
            callback()


class LayoutEditorCanvas(SceneCanvasOwnerMixin, QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasArea")
        self.scene = QGraphicsScene(self)
        register_graphics_scene_for_cleanup(self.scene)
        self.view = LayoutEditorGraphicsView(self.scene, self)
        self.canvas_spec: LayoutCanvasSpec | None = None
        self._items_by_object_id: dict[str, QGraphicsItem] = {}
        self._layout_changed_callback: SvgTextChangedCallback | None = None
        self.zoom_controls = CanvasZoomControls()
        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        layout.addWidget(self.zoom_controls)
        layout.addWidget(self.view)
        attach_scene_canvas_controller(
            self,
            self.view,
            content_rect_provider=lambda: QRectF(self.scene.sceneRect()),
            zoom_controls=self.zoom_controls,
            editable=True,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        clear_graphics_scene_for_cleanup(self.scene)
        super().closeEvent(event)

    def set_layout_objects(
        self,
        canvas_spec: LayoutCanvasSpec,
        layout_objects: Iterable[LayoutObject],
        pixmap_provider: LayoutAssetProvider | None = None,
        svg_text_changed_callback: SvgTextChangedCallback | None = None,
    ) -> None:
        self.canvas_spec = canvas_spec
        self._layout_changed_callback = svg_text_changed_callback
        self.scene.clear()
        self._items_by_object_id.clear()
        self.scene.setSceneRect(0.0, 0.0, float(canvas_spec.width_px), float(canvas_spec.height_px))
        for layout_object in sorted(layout_objects, key=lambda item: item.z_order):
            asset = self._asset_for(layout_object, pixmap_provider)
            item = layout_editor_item_for(
                layout_object,
                pixmap=asset if isinstance(asset, QPixmap) else None,
                svg_bytes=asset if isinstance(asset, bytes) else None,
                svg_text_changed_callback=svg_text_changed_callback,
            )
            self.scene.addItem(item)
            self._items_by_object_id[layout_object.object_id] = item
        pictures=[item for item in self._items_by_object_id.values() if isinstance(item,LayoutEditorImageProxyItem)
                  and not item.layout_object.metadata.get('output',{}).get('instance_uid')]
        for bar in self._items_by_object_id.values():
            obj=getattr(bar,'layout_object',None)
            if obj is None or obj.object_type!='scale_bar':continue
            key=obj.metadata.get('target_output_key')
            picture=next((item for item in pictures if key and item.layout_object.metadata.get('export_item_key')==key),None)
            if picture is not None:picture.attach_scale_bar(bar)
        self._apply_canvas_zoom()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._view_controller.handle_resize()

    def _asset_for(self, layout_object: LayoutObject, pixmap_provider: LayoutAssetProvider | None) -> QPixmap | bytes | None:
        if pixmap_provider is None:
            return None
        value = pixmap_provider(layout_object)
        if isinstance(value, QPixmap):
            return value
        if isinstance(value, QImage):
            return QPixmap.fromImage(value)
        if isinstance(value, bytes):
            return value
        return None

    def selected_layout_items(self) -> list[QGraphicsItem]:
        return layout_editor_items(self.scene.selectedItems())

    def layout_items(self) -> list[QGraphicsItem]:
        return layout_editor_items(self.scene.items())

    def item_for_object_id(self, object_id: str) -> QGraphicsItem | None:
        return self._items_by_object_id.get(object_id)

    def selected_layout_records(self) -> list[EditableLayoutRecord]:
        return [self._record_for_item(item) for item in self.selected_layout_items()]

    def _record_for_item(self, item: QGraphicsItem) -> EditableLayoutRecord:
        object_id = str(item.data(DATA_ROLE_OBJECT_ID) or "")
        rect = self._item_rect(item)
        layout_object = getattr(item, "layout_object", None)
        locked = bool(getattr(layout_object, "locked", False))
        hidden = not item.isVisible()
        return EditableLayoutRecord(
            kind=str(item.data(DATA_ROLE_OBJECT_TYPE) or "layout"),
            uid=object_id,
            model=item,
            x=float(item.pos().x()),
            y=float(item.pos().y()),
            width=float(rect.width()),
            height=float(rect.height()),
            locked=locked,
            hidden=hidden,
            can_resize=not isinstance(item, LayoutEditorTextItem),
            z_order=int(item.zValue()),
        )

    def _item_rect(self, item: QGraphicsItem) -> QRectF:
        if isinstance(item, SharedShapeItem):
            return item.rect()
        return item.boundingRect()

    def _apply_record_geometry(self, record: EditableLayoutRecord) -> None:
        item = record.model
        if hasattr(item, "update_geometry"):
            item.update_geometry(record.x, record.y, record.width, record.height)
        else:
            item.setPos(record.x, record.y)

    def move_selected(self, dx: float, dy: float) -> bool:
        records = self.selected_layout_records()
        changed = move_records(records, dx, dy)
        if changed:
            for record in records:
                self._apply_record_geometry(record)
        return changed

    def align_selected(self, mode: str, reference_mode: str = "extent") -> bool:
        records = self.selected_layout_records()
        changed = align_records(records, mode, reference_mode=reference_mode)
        if changed:
            for record in records:
                self._apply_record_geometry(record)
        return changed

    def distribute_selected(self, orientation: str) -> bool:
        records = self.selected_layout_records()
        changed = distribute_records(records, orientation)
        if changed:
            for record in records:
                self._apply_record_geometry(record)
        return changed

    def make_selected_same_size(self, reference_mode: str = "top_left") -> bool:
        records = self.selected_layout_records()
        changed = make_records_same_size(records, reference_mode=reference_mode)
        if changed:
            for record in records:
                self._apply_record_geometry(record)
        return changed

    def delete_selected(self) -> int:
        selected = [item for item in self.selected_layout_items() if not self._is_locked_item(item)]
        for item in selected:
            if item.layout_object.object_type in {'plot','image','scale_bar'}:
                item.set_hidden(True)
                continue
            object_id = str(item.data(DATA_ROLE_OBJECT_ID) or "")
            self._items_by_object_id.pop(object_id, None)
            self.scene.removeItem(item)
        return len(selected)

    def set_selected_locked(self, locked: bool) -> int:
        selected = self.selected_layout_items()
        for item in selected:
            if hasattr(item, "set_flags_for_locked"):
                item.set_flags_for_locked(bool(locked))
        return len(selected)

    def set_selected_hidden(self, hidden: bool) -> int:
        selected = self.selected_layout_items()
        for item in selected:
            if hasattr(item, "set_hidden"):
                item.set_hidden(bool(hidden))
            else:
                item.setVisible(not hidden)
        return len(selected)

    def show_all_layout_items(self) -> int:
        items = self.layout_items()
        for item in items:
            if hasattr(item, "set_hidden"):
                item.set_hidden(False)
            else:
                item.setVisible(True)
        return len(items)

    def apply_style_to_selected(self, style: LayoutObjectStyle) -> int:
        selected = [item for item in self.selected_layout_items() if not self._is_locked_item(item)]
        for item in selected:
            if hasattr(item, "apply_style"):
                item.apply_style(style)
        return len(selected)

    def apply_text_style_to_selected(self, style: dict[str, object]) -> int:
        selected = [item for item in self.selected_layout_items() if isinstance(item, LayoutEditorTextItem) and not self._is_locked_item(item)]
        for item in selected:
            current = item.layout_object.style.to_dict()
            if "font_family" in style:
                current["font_family"] = str(style["font_family"])
            if "font_size_pt" in style:
                current["font_size_pt"] = float(style["font_size_pt"])
            if "bold" in style:
                current["bold"] = bool(style["bold"])
            if "italic" in style:
                current["italic"] = bool(style["italic"])
            if "text_color" in style:
                current["text_color"] = str(style["text_color"])
            item.apply_style(LayoutObjectStyle.from_dict(current))
            if "rotation_deg" in style:
                item.setRotation(float(style["rotation_deg"]))
            if "x" in style or "y" in style:
                item.setPos(float(style.get("x", item.pos().x())), float(style.get("y", item.pos().y())))
        return len(selected)

    def selected_text_items(self) -> list[LayoutEditorTextItem]:
        return [item for item in self.selected_layout_items() if isinstance(item, LayoutEditorTextItem)]

    def layout_objects(self) -> list[LayoutObject]:
        objects = [self._layout_object_for_item(item) for item in self.layout_items()]
        return sorted(objects, key=lambda layout_object: layout_object.z_order)

    def _layout_object_for_item(self, item: QGraphicsItem) -> LayoutObject:
        original = getattr(item, "layout_object", None)
        if not isinstance(original, LayoutObject):
            raise ValueError("layout editor item has no LayoutObject")
        rect = self._item_rect(item)
        payload = original.to_dict()
        if isinstance(item, LayoutEditorTextItem):
            width = float(item.textWidth())
            height = float(original.height)
        else:
            width = float(rect.width())
            height = float(rect.height())
        payload.update(
            {
                "x": float(item.pos().x()),
                "y": float(item.pos().y()),
                "width": width,
                "height": height,
                "rotation_deg": float(item.rotation()),
                "z_order": int(item.zValue()),
                "locked": self._is_locked_item(item),
                "hidden": not item.isVisible(),
            }
        )
        if isinstance(item, LayoutEditorTextItem):
            payload["text"] = item.toPlainText()
        return LayoutObject.from_dict(payload)

    def _is_locked_item(self, item: QGraphicsItem) -> bool:
        layout_object = getattr(item, "layout_object", None)
        return bool(getattr(layout_object, "locked", False))

    def _editor_affordance_items(self) -> list[QGraphicsItem]:
        return [item for item in self.scene.items() if item.data(DATA_ROLE_MARKER) == LAYOUT_EDITOR_RESIZE_HANDLE_MARKER]

    def _scene_export_items(self) -> list[QGraphicsItem]:
        return [item for item in self.scene.items() if hasattr(item, "prepare_scene_export") and hasattr(item, "finish_scene_export")]

    def _render_scene_region(self, source: QRectF, width: int, height: int, fill: QColor | Qt.GlobalColor) -> QImage:
        selected = list(self.scene.selectedItems())
        affordances = self._editor_affordance_items()
        affordance_visibility = {item: item.isVisible() for item in affordances}
        export_items = self._scene_export_items()
        image = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_ARGB32)
        image.fill(fill)
        try:
            for item in selected:
                item.setSelected(False)
            for item in affordances:
                item.setVisible(False)
            for item in export_items:
                item.prepare_scene_export()
            painter = QPainter(image)
            try:
                self.scene.render(painter, target=QRectF(image.rect()), source=source)
            finally:
                painter.end()
        finally:
            for item in export_items:
                item.finish_scene_export()
            for item in selected:
                item.setSelected(True)
            for item, visible in affordance_visibility.items():
                item.setVisible(visible)
        return image

    def render_full_layout(self, dpi: int | None = None) -> QImage:
        if self.canvas_spec is None:
            raise RuntimeError("LayoutEditorCanvas has no canvas spec")
        width = self.canvas_spec.width_px
        height = self.canvas_spec.height_px
        if dpi is not None:
            resolved_dpi = max(1, int(dpi))
            scale = resolved_dpi / 72.0
            width = max(1, int(round(self.canvas_spec.width_px * scale)))
            height = max(1, int(round(self.canvas_spec.height_px * scale)))
        image = self._render_scene_region(
            self.scene.sceneRect(),
            width,
            height,
            QColor(self.canvas_spec.background_color),
        )
        if dpi is not None:
            dots_per_meter = int(round(max(1, int(dpi)) / 0.0254))
            image.setDotsPerMeterX(dots_per_meter)
            image.setDotsPerMeterY(dots_per_meter)
        return image

    def render_selected_item(self, margin: int = 8) -> QImage:
        selected = self.selected_layout_items()
        if not selected:
            raise RuntimeError("No selected layout item to render")
        rect = selected[0].sceneBoundingRect()
        for item in selected[1:]:
            rect = rect.united(item.sceneBoundingRect())
        rect = rect.adjusted(-margin, -margin, margin, margin)
        return self._render_scene_region(rect, int(rect.width()), int(rect.height()), Qt.GlobalColor.transparent)
