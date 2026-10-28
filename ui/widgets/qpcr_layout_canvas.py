from __future__ import annotations
from ui.widgets.shared_layout_text import SharedTextItem
from ui.widgets.shared_layout_shapes import SharedShapeItem,SharedResizeHandle
from ui.widgets.shared_layout_controls import apply_selection_style
from ui.layout_adapters.annotation_records import text_from_record,shape_from_record

from io import BytesIO
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QImage, QPainter, QPen, QPixmap, QTransform
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from core.export.wb_exporter import export_wb_figure_scene
from core.models import QPCRPanel
from core.utils.i18n import tr
from core.models.layout_editor import LayoutObject
from ui.widgets.canvas_presentation import configure_canvas_layout
from ui.widgets.canvas_graphics_adapter import (
    CanvasGraphicsView,
    add_canvas_hint,
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
    align_records,
    distribute_records,
    make_records_same_size,
)
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action
from ui.widgets.svg_text_editor import prompt_svg_text_edit
from core.utils.svg_text import apply_svg_text_overrides
from core.utils.svg_text import normalize_svg_text_overrides, strip_svg_text_layers, svg_text_entries, update_svg_text_overrides
from ui.widgets.editable_text_item import (
    clear_graphics_scene_for_cleanup,
    detach_graphics_item,
    graphics_item_is_alive,
    register_graphics_scene_for_cleanup,
    svg_bytes_to_pixmap,
)


QPCR_LAYOUT_MARKERS = {"qpcr_layout_plot", "qpcr_layout_shape", "qpcr_layout_text"}
QPCR_LAYOUT_RENDER_DPI = 1200




class QPCRLayoutTextItem(SharedTextItem):
    def __init__(self, canvas, record):
        self.canvas = canvas
        uid = str(record["text_uuid"])
        obj = text_from_record(record, f"{canvas.panel.panel_uuid}:qpcr_text:{uid}",
            {"qpcr_kind":"layout_text", "text_uuid":uid, "text_record":dict(record)})
        super().__init__(obj)
        self.setData(0, "qpcr_layout_text")
        self.setData(1, uid)

    def apply_geometry(self, x, y, width, height):
        self.update_geometry(x, y, width, height)


class QPCRLayoutPlotItem(QGraphicsRectItem):
    def __init__(self, source: QPixmap | bytes, record: dict, svg_text_changed_callback=None) -> None:
        self._svg_base = bytes(source) if isinstance(source, bytes) else b""
        self._svg_overrides = normalize_svg_text_overrides(dict(record.get("svg_text_overrides", {})))
        self._svg_text_changed_callback = svg_text_changed_callback
        self._svg_text_items: list[SharedTextItem] = []
        self._display_svg_bytes = b""
        self._export_svg_bytes = b""
        if isinstance(source, bytes):
            self._refresh_svg_payload()
            pixmap = svg_bytes_to_pixmap(self._display_svg_bytes or self._export_svg_bytes)
            source_width = max(pixmap.width(), 1)
            source_height = max(pixmap.height(), 1)
        else:
            source_width = max(source.width(), 1)
            source_height = max(source.height(), 1)
        width = float(record.get("width_px", source_width))
        height = float(record.get("height_px", source_height))
        super().__init__(0, 0, max(1.0, width), max(1.0, height))
        self._source_width = source_width
        self._source_height = source_height
        self._locked=bool(record.get('locked',False))
        pixmap = svg_bytes_to_pixmap(self._display_svg_bytes or self._export_svg_bytes) if isinstance(source, bytes) else source
        self._visual_item = QGraphicsPixmapItem(pixmap, self)
        self._visual_item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
        self._visual_item.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.setData(0, "qpcr_layout_plot")
        self.setData(1, str(record.get("plot_uuid", "")))
        self.setData(2, str(record.get("target", "")))
        self.setPos(float(record.get("x", 24.0)), float(record.get("y", 24.0)))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.setZValue(float(record.get('z_order',100)))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.handles = {role: SharedResizeHandle(self, role) for role in RESIZE_HANDLE_ROLES}
        self.set_flags_for_locked(self._locked)
        self._sync_pixmap_transform()
        self.update_handles()
        self._rebuild_svg_text_items()

    def current_geometry(self) -> tuple[float, float, float, float]:
        return (float(self.pos().x()), float(self.pos().y()), float(self.rect().width()), float(self.rect().height()))

    @property
    def layout_object(self):
        return LayoutObject(f'qpcr_plot:{self.data(1)}','plot',self.pos().x(),self.pos().y(),
            self.rect().width(),self.rect().height(),
            locked=self._locked,z_order=int(self.zValue()),
            metadata={'qpcr_kind':'layout_plot','plot_uuid':self.data(1)})

    def set_flags_for_locked(self,locked):
        self._locked=bool(locked)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable,not locked)
        if hasattr(self,'handles'):self.set_handles_visible(self.isSelected() and not locked)

    def update_geometry(self,x,y,width,height):
        self.apply_geometry(x,y,width,height)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.set_handles_visible(bool(value))
        elif change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged and self._svg_base:
            self._rebuild_svg_text_items()
        return super().itemChange(change, value)

    def set_handles_visible(self, visible: bool) -> None:
        for handle in self.handles.values():
            handle.setVisible(visible and not self._locked)

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
        self.setPos(float(x), float(y))
        self.setRect(0, 0, max(1.0, float(width)), max(1.0, float(height)))
        self._sync_pixmap_transform()
        self.update_handles()
        self._rebuild_svg_text_items()
        if repaint and self.scene() is not None:
            self.scene().update(self.sceneBoundingRect())

    def _sync_pixmap_transform(self) -> None:
        transform = QTransform()
        transform.scale(self.rect().width() / max(self._source_width, 1), self.rect().height() / max(self._source_height, 1))
        self._visual_item.setTransform(transform)

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
        if menu.exec(event.screenPos()) != edit_action:
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


class QPCRLayoutShapeItem(SharedShapeItem):
    def __init__(self, shape):
        uid = str(shape["shape_uuid"])
        super().__init__(shape_from_record(shape, f"qpcr_shape:{uid}",
            {"qpcr_kind":"layout_shape", "shape_uuid":uid, "shape":dict(shape)}))
        self.setData(0, "qpcr_layout_shape")
        self.setData(1, uid)

    @property
    def shape_type(self):
        return self.shape_kind

    def current_geometry(self):
        return self.pos().x(), self.pos().y(), self.rect().width(), self.rect().height()

    def apply_geometry(self, x, y, width, height):
        self.update_geometry(x, y, width, height)


class QPCRGraphicsView(CanvasGraphicsView):
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

    def wheelEvent(self, event) -> None:
        if handle_canvas_wheel(self, event):
            return
        super().wheelEvent(event)

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

class QPCRLayoutCanvas(SceneCanvasOwnerMixin, QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("CanvasArea")
        self.scene = QGraphicsScene(self)
        register_graphics_scene_for_cleanup(self.scene)
        self.scene.setBackgroundBrush(QColor("#FFFFFF"))
        self.view = QPCRGraphicsView(self.scene, self)
        self.view.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.panel: QPCRPanel | None = None
        self.zoom_controls = CanvasZoomControls()

        layout = QVBoxLayout(self)
        configure_canvas_layout(layout)
        layout.addWidget(self.zoom_controls)
        layout.addWidget(self.view, 1)
        attach_scene_canvas_controller(
            self,
            self.view,
            content_rect_provider=lambda: QRectF(self.scene.sceneRect()),
            content_frame=True,
            zoom_controls=self.zoom_controls,
            editable=True,
        )

    def closeEvent(self, event) -> None:  # noqa: N802
        clear_graphics_scene_for_cleanup(self.scene)
        super().closeEvent(event)

    def set_panel(self, panel: QPCRPanel) -> None:
        self.panel = panel
        self.refresh()

    def layout_objects(self) -> list:
        if self.panel is None:
            return []
        from core.models.layout_editor import LayoutObject, LayoutSourceIdentity
        from ui.layout_adapters.qpcr_layout_adapter import qpcr_layout_objects
        bases = qpcr_layout_objects(self.panel)
        items = [i for i in self.scene.items() if i.data(0) in {"qpcr_layout_plot","qpcr_layout_text","qpcr_layout_shape"}]
        matched = set()
        result = []
        for base in bases:
            meta = base.metadata
            kind = meta.get("qpcr_kind")
            key = {"layout_plot":"plot_uuid","layout_text":"text_uuid","layout_shape":"shape_uuid"}.get(kind)
            marker = {"layout_plot":"qpcr_layout_plot","layout_text":"qpcr_layout_text","layout_shape":"qpcr_layout_shape"}.get(kind)
            item = next((i for i in items if str(i.data(1) or "") == str(meta.get(key, "")) and i.data(0) == marker), None)
            payload = base.to_dict()
            if item is not None:
                matched.add(id(item))
                width,height = self._item_size(item)
                payload.update(x=item.pos().x(), y=item.pos().y(), width=width, height=height, rotation_deg=item.rotation(), z_order=int(item.zValue()), hidden=not item.isVisible())
                if item.data(0)=='qpcr_layout_plot':payload['locked']=item.layout_object.locked
                if isinstance(item,(SharedTextItem,SharedShapeItem)):
                    current = item.to_layout_object() if isinstance(item,SharedTextItem) else item.layout_object
                    payload.update(text=current.text, style=current.style.to_dict(), locked=current.locked)
                    payload["metadata"].update(current.to_dict()["metadata"])
            result.append(LayoutObject.from_dict(payload))
        for item in items:
            if id(item) in matched or not isinstance(item,(SharedTextItem,SharedShapeItem)):
                continue
            text = isinstance(item,SharedTextItem)
            current = item.to_layout_object() if text else item.layout_object
            payload = current.to_dict()
            uid = str(item.data(1))
            namespace = "qpcr_layout_text" if text else "qpcr_layout_shape"
            label = "text" if text else "shape"
            payload["object_id"] = f"{self.panel.panel_uuid}:qpcr_{label}:{uid}"
            payload["source"] = LayoutSourceIdentity("qpcr",self.panel.panel_uuid,uid,namespace).to_dict()
            payload.update(x=item.pos().x(),y=item.pos().y(),rotation_deg=item.rotation(),z_order=int(item.zValue()))
            result.append(LayoutObject.from_dict(payload))
        return result

    def apply_layout_objects(self, layout_objects: list, *, refresh: bool = True, append_audit: bool = True) -> None:
        if self.panel is None:
            return
        from ui.layout_adapters.qpcr_layout_adapter import apply_qpcr_layout_objects

        apply_qpcr_layout_objects(self.panel, layout_objects, append_audit=append_audit)
        if refresh:
            self.refresh()

    def refresh(self) -> None:
        self.scene.clear()
        self.scene.setBackgroundBrush(QColor("#FFFFFF"))
        if self.panel is None:
            self._add_hint()
            return
        records = self.panel.layout_plot_records()
        if not records and not self.panel.data.get('qpcr_layout',{}).get('texts') and not self.panel.data.get('qpcr_layout',{}).get('shapes'):
            self._add_hint()
            return
        self._view_controller.set_content_identity(("qpcr-layout",self.panel.panel_uuid,
            tuple(str(record.get('plot_uuid','')) for record in records if record.get('enabled',True))))
        for record in records:
            if not record.get("enabled", True):
                continue
            svg = self.panel.render_layout_plot(
                record,
                dpi=QPCR_LAYOUT_RENDER_DPI,
                format="svg",
                include_svg_text_overrides=False,
            )
            self.scene.addItem(
                QPCRLayoutPlotItem(
                    svg,
                    record,
                    svg_text_changed_callback=lambda text_index, text, plot_uuid=str(record.get("plot_uuid", "")): self._update_layout_plot_svg_text(plot_uuid, text_index, text),
                )
            )
        layout = self.panel.data.setdefault("qpcr_layout", {})
        for index,shape in enumerate(layout.get("shapes", [])):
            record=dict(shape)
            record.setdefault('shape_uuid',f'shape:{index}')
            item=QPCRLayoutShapeItem(record)
            self.scene.addItem(item)
            item._geometry_changed_callback=lambda *_:self.persist_layout_edits()
            item._style_changed_callback=lambda *_:self.persist_layout_edits()
        for index,text in enumerate(layout.get("texts", [])):
            record=dict(text)
            record.setdefault('text_uuid',f'text:{index}')
            self._add_text_item(record)
        self.scene.setSceneRect(self.scene.itemsBoundingRect().adjusted(-40, -40, 40, 40))
        self._apply_canvas_zoom()

    def retranslate(self) -> None:
        self.zoom_controls.retranslate()
        for item in self.scene.items():
            if item.data(0) == 'qpcr_empty_hint':
                item.setPlainText(tr('canvas_qpcr_layout_empty'))
                item.setTitle(tr('module_qpcr_canvas'))

    def _add_hint(self) -> None:
        self._view_controller.set_content_identity(("qpcr-empty",self.panel.panel_uuid if self.panel else None))
        item = add_canvas_hint(self.scene,tr('canvas_qpcr_layout_empty'),title=tr('module_qpcr_canvas'))
        item.setData(0, 'qpcr_empty_hint')
        self._apply_canvas_zoom()

    def _update_layout_plot_svg_text(self, plot_uuid: str, text_index: int, text: str | dict[str, float | str]) -> None:
        if self.panel is None:
            return
        layout = self.panel.data.setdefault("qpcr_layout", {})
        for plot in layout.get("plots", []):
            if not isinstance(plot, dict) or str(plot.get("plot_uuid", "")) != str(plot_uuid):
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
            self.panel.append_audit("update_qpcr_plot_svg_text", f"{plot_uuid}:{text_index}")
            return

    def add_text(self, text: str = "Text") -> QGraphicsTextItem:
        record = {
            "text_uuid": f"qpcr_text_{uuid4().hex}",
            "text": text,
            "x": 24.0,
            "y": 24.0,
            "font_family": "Arial",
            "font_size": 10.0,
            "color": "#111111",
        }
        return self._add_text_item(record, select=True)

    def _add_text_item(self, record: dict, select: bool = False) -> QGraphicsTextItem:
        if select:self.scene.clearSelection()
        item = QPCRLayoutTextItem(self, record)
        self.scene.addItem(item)
        item._text_changed_callback = lambda *_:self.persist_layout_edits()
        item._geometry_changed_callback = lambda *_:self.persist_layout_edits()
        item._style_changed_callback = lambda *_:self.persist_layout_edits()
        item.setSelected(select)
        if select:self.persist_layout_edits()
        return item

    def add_shape(self, shape_type: str = "rectangle", stroke_color: str = "#000000", fill_color: str = "#FFFFFF") -> QPCRLayoutShapeItem:
        self.scene.clearSelection()
        shape = {
            "shape_uuid": f"qpcr_shape_{uuid4().hex}",
            "shape_type": shape_type.lower(),
            "x": 48.0,
            "y": 48.0,
            "width": 120.0,
            "height": 70.0,
            "stroke_width": 2.0,
            "stroke_color": stroke_color,
            "fill_color": fill_color,
        }
        item = QPCRLayoutShapeItem(shape)
        self.scene.addItem(item)
        item._geometry_changed_callback=lambda *_:self.persist_layout_edits()
        item._style_changed_callback=lambda *_:self.persist_layout_edits()
        item.setSelected(True)
        self.persist_layout_edits()
        return item

    def selected_layout_items(self) -> list[QGraphicsItem]:
        return [item for item in self.scene.selectedItems() if str(item.data(0) or "") in QPCR_LAYOUT_MARKERS]

    def layout_plot_items(self) -> list[QGraphicsItem]:
        return [item for item in self.scene.items() if str(item.data(0) or "") == "qpcr_layout_plot"]

    def _record_for_item(self, item: QGraphicsItem) -> EditableLayoutRecord:
        width, height = self._item_size(item)
        return EditableLayoutRecord(
            kind=str(item.data(0) or ""),
            uid=str(item.data(1) or ""),
            model=item,
            x=float(item.pos().x()),
            y=float(item.pos().y()),
            width=width,
            height=height,
        )

    def align_selected(self, axis: str) -> None:
        records = [self._record_for_item(item) for item in self.selected_layout_items()]
        if align_records(records, "hcenter" if axis == "column" else "vcenter", reference_mode="first"):
            self._apply_records(records)

    def make_selected_same_size(self) -> None:
        records = [self._record_for_item(item) for item in self.selected_layout_items()]
        if make_records_same_size(records, reference_mode="largest"):
            self._apply_records(records, resize=True)

    def distribute_selected(self, orientation: str) -> None:
        records = [self._record_for_item(item) for item in self.selected_layout_items()]
        if distribute_records(records, orientation):
            self._apply_records(records)

    def _apply_records(self, records: list[EditableLayoutRecord], resize: bool = False) -> None:
        for record in records:
            item = record.model
            if not isinstance(item, QGraphicsItem):
                continue
            if resize and hasattr(item, "apply_geometry"):
                item.apply_geometry(record.x, record.y, record.width, record.height)
            else:
                item.setPos(record.x, record.y)
        self.persist_layout_edits()

    def delete_selected(self) -> None:
        selected = self.selected_layout_items()
        if not selected or self.panel is None:
            return
        plot_uids = {str(item.data(1) or "") for item in selected if item.data(0) == "qpcr_layout_plot"}
        text_uids = {str(item.data(1) or "") for item in selected if item.data(0) == "qpcr_layout_text"}
        shape_uids = {str(item.data(1) or "") for item in selected if item.data(0) == "qpcr_layout_shape"}
        layout = self.panel.data.setdefault("qpcr_layout", {})
        if plot_uids:
            for plot in layout.get("plots", []):
                if str(plot.get("plot_uuid", "")) in plot_uids:
                    plot["enabled"] = False
        if text_uids:
            layout["texts"] = [item for item in layout.get("texts", []) if str(item.get("text_uuid", "")) not in text_uids]
        if shape_uids:
            layout["shapes"] = [item for item in layout.get("shapes", []) if str(item.get("shape_uuid", "")) not in shape_uids]
        self.refresh()

    def apply_plot_style_to_selection(self, style: dict, width_px: int | None = None, height_px: int | None = None) -> None:
        if self.panel is None:
            return
        self.persist_layout_edits()
        selected_uids = {str(item.data(1) or "") for item in self.selected_layout_items() if item.data(0) == "qpcr_layout_plot"}
        layout = self.panel.data.setdefault("qpcr_layout", {})
        plots = []
        for plot in self.panel.layout_plot_records():
            updated = dict(plot)
            if not updated.get('locked',False) and (not selected_uids or str(updated.get("plot_uuid", "")) in selected_uids):
                if width_px is not None:
                    updated["width_px"] = int(width_px)
                if height_px is not None:
                    updated["height_px"] = int(height_px)
                current_style = dict(updated.get("style", {}))
                current_style.update(style)
                updated["style"] = current_style
            plots.append(updated)
        layout["plots"] = plots
        self.panel.data["qpcr_layout"] = layout
        self.refresh()

    def apply_text_style_to_selection(self, style: dict) -> None:
        apply_selection_style(self.selected_layout_items(), style)
        for item in self.selected_layout_items():
            if isinstance(item,SharedTextItem) and not item.layout_object.locked:
                if 'rotation_deg' in style:item.setRotation(float(style['rotation_deg']))
                if 'x' in style or 'y' in style:item.setPos(float(style.get('x',item.x())),float(style.get('y',item.y())))
        self.persist_layout_edits()

    def apply_shape_style_to_selection(self, style: dict) -> None:
        apply_selection_style(self.selected_layout_items(), style)
        for item in self.selected_layout_items():
            if isinstance(item,SharedShapeItem) and not item.layout_object.locked and ('x' in style or 'y' in style):
                item.setPos(float(style.get('x',item.x())),float(style.get('y',item.y())))
        self.persist_layout_edits()

    def persist_layout_edits(self) -> None:
        if self.panel is None:
            return
        layout_objects = self.layout_objects()
        if layout_objects:
            self.apply_layout_objects(layout_objects, refresh=False, append_audit=False)

    def export_figure(self, path: str | Path, dpi: int = QPCR_LAYOUT_RENDER_DPI) -> Path:
        self.persist_layout_edits()
        selected = list(self.scene.selectedItems())
        hidden = []
        export_items = [item for item in self.scene.items() if hasattr(item, "prepare_scene_export") and hasattr(item, "finish_scene_export")]
        try:
            self.scene.clearSelection()
            for item in self.scene.items():
                if str(item.data(0) or "") == "qpcr_resize_handle":
                    hidden.append((item, item.isVisible()))
                    item.setVisible(False)
            for item in export_items:
                item.prepare_scene_export()
            return export_wb_figure_scene(self.scene, path, dpi=dpi)
        finally:
            for item in export_items:
                item.finish_scene_export()
            for item, visible in hidden:
                if item.scene() is self.scene:
                    item.setVisible(visible)
            for item in selected:
                if item.scene() is self.scene:
                    item.setSelected(True)

    def _item_size(self, item: QGraphicsItem) -> tuple[float, float]:
        if isinstance(item, (QPCRLayoutPlotItem, QPCRLayoutShapeItem)):
            return float(item.rect().width()), float(item.rect().height())
        rect = item.sceneBoundingRect()
        return float(rect.width()), float(rect.height())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._view_controller.handle_resize()


def _qimage_from_png_bytes(data: bytes) -> QImage:
    image = QImage()
    image.loadFromData(data, "PNG")
    return image
