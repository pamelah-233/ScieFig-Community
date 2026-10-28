from __future__ import annotations

from collections.abc import Callable
from typing import Iterable
from PyQt6 import sip

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsPixmapItem, QMenu

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from core.utils.svg_text import apply_svg_text_overrides, normalize_svg_text_overrides, strip_svg_text_layers, svg_text_entries, update_svg_text_overrides
from ui.widgets.svg_text_editor import prompt_svg_text_edit
from ui.widgets.text_digit_transform import add_digit_transform_actions, apply_digit_transform_action
from ui.widgets.editable_text_item import detach_graphics_item, graphics_item_is_alive, svg_bytes_to_pixmap
from ui.widgets.shared_layout_shapes import (
    DATA_ROLE_MARKER,
    DATA_ROLE_OBJECT_ID,
    DATA_ROLE_OBJECT_TYPE,
    SHARED_RESIZE_HANDLE_MARKER,
    SHARED_SHAPE_ITEM_MARKER,
    SharedShapeItem,
    _color,
)
from ui.widgets.shared_layout_text import SharedTextItem


LAYOUT_EDITOR_ITEM_MARKER = SHARED_SHAPE_ITEM_MARKER
LAYOUT_EDITOR_RESIZE_HANDLE_MARKER = SHARED_RESIZE_HANDLE_MARKER


class LayoutEditorGroupShellItem(SharedShapeItem):
    def apply_style(self, style: LayoutObjectStyle) -> None:
        super().apply_style(style)
        pen = self.pen()
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))


class LayoutEditorScaleBarItem(SharedShapeItem):
    def apply_style(self, style: LayoutObjectStyle) -> None:
        self.layout_object = LayoutObject.from_dict({**self.layout_object.to_dict(), "style": style.to_dict()})
        self.setPen(QPen(Qt.GlobalColor.transparent, 0.0))
        self.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        self.setOpacity(float(style.opacity))
        self.update()

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001, D102
        del option, widget
        style = self.layout_object.style
        metadata = dict(style.metadata)
        rect = self.rect()
        line_width = max(1.0, float(style.stroke_width))
        gap = max(0.0, float(metadata.get("label_gap", 4.0)))
        show_text = bool(metadata.get("show_text", True)) and bool(self.layout_object.text)
        text_position = str(metadata.get("text_position", "above"))
        font = QFont(style.font_family)
        font.setPointSizeF(float(style.font_size_pt))
        font.setBold(style.bold)
        font.setItalic(style.italic)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(font)

        text_height = max(0.0, float(style.font_size_pt) * 1.4) if show_text else 0.0
        if show_text and text_position == "below":
            line_y = line_width / 2.0
            text_rect = QRectF(rect.left(), line_width + gap, rect.width(), text_height)
        elif show_text:
            line_y = text_height + gap + line_width / 2.0
            text_rect = QRectF(rect.left(), 0.0, rect.width(), text_height)
        else:
            line_y = rect.center().y()
            text_rect = QRectF()

        line_pen = QPen(_color(style.stroke_color), line_width)
        line_pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        painter.setPen(line_pen)
        painter.drawLine(QPointF(rect.left(), line_y), QPointF(rect.right(), line_y))

        if show_text:
            painter.setPen(QPen(_color(style.text_color), 1.0))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.layout_object.text)


class LayoutEditorImageProxyItem(SharedShapeItem):
    def __init__(
        self,
        layout_object: LayoutObject,
        pixmap: QPixmap | None = None,
        svg_bytes: bytes | None = None,
        svg_text_changed_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(layout_object)
        self.scale_bar_attachments = []
        self._attachment_origin = QPointF(self.pos())
        self._svg_base = bytes(svg_bytes or b"")
        self._svg_text_changed_callback = svg_text_changed_callback
        self._svg_text_items: list[SharedTextItem] = []
        self._display_svg_bytes = b""
        self._export_svg_bytes = b""
        self.pixmap_item = QGraphicsPixmapItem(self)
        self.pixmap_item.setData(DATA_ROLE_MARKER, "layout_editor_pixmap_proxy")
        self.svg_item = self.pixmap_item
        if self._svg_base:
            self.set_svg(self._svg_base)
        else:
            self.set_pixmap(pixmap)

    def set_pixmap(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self.pixmap_item.setPixmap(QPixmap())
            self.pixmap_item.setPos(0.0, 0.0)
            return
        self.pixmap_item.setVisible(True)
        self.pixmap_item.setPixmap(pixmap)
        rect = self.rect()
        if pixmap.width() > 0 and pixmap.height() > 0:
            scale = min(rect.width() / pixmap.width(), rect.height() / pixmap.height())
            self.pixmap_item.setScale(scale)
            self.pixmap_item.setPos(
                (rect.width() - pixmap.width() * scale) / 2.0,
                (rect.height() - pixmap.height() * scale) / 2.0,
            )

    def set_svg(self, svg_bytes: bytes) -> None:
        self._refresh_svg_payload(svg_bytes)
        self.pixmap_item.setVisible(True)
        self.set_pixmap(svg_bytes_to_pixmap(self._display_svg_bytes or self._export_svg_bytes))
        self._rebuild_svg_text_items()

    def _refresh_svg_payload(self, svg_bytes: bytes | None = None) -> None:
        canonical = bytes(svg_bytes if svg_bytes is not None else self._rendered_svg_bytes())
        self._export_svg_bytes = canonical
        self._display_svg_bytes = strip_svg_text_layers(canonical) if canonical else b""

    def _update_svg_geometry(self) -> None:
        self.set_pixmap(self.pixmap_item.pixmap())

    def _svg_overrides(self) -> dict[str, str | dict[str, object]]:
        return normalize_svg_text_overrides(dict(self.layout_object.metadata).get("svg_text_overrides", {}))

    def _rendered_svg_bytes(self) -> bytes:
        return apply_svg_text_overrides(self._svg_base, self._svg_overrides()) if self._svg_base else b""




    def _rebuild_svg_text_items(self) -> None:
        from ui.widgets.shared_svg_text import SvgTextBinding
        if not hasattr(self, "_svg_binding"):
            for item in self._svg_text_items:
                detach_graphics_item(item)
            self._svg_binding = SvgTextBinding(
                self, self.pixmap_item, self._rendered_svg_bytes,
                self._svg_overrides, self._store_shared_svg_text)
        self._svg_binding.rebuild()
        self._svg_text_items = self._svg_binding.items

    def _store_shared_svg_text(self, index, value):
        payload = self.layout_object.to_dict()
        overrides = self._svg_overrides()
        overrides[str(index)] = value
        payload["metadata"]["svg_text_overrides"] = overrides
        self.layout_object = LayoutObject.from_dict(payload)
        if self._svg_text_changed_callback is not None:
            self._svg_text_changed_callback()

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):  # noqa: N802
        result = super().itemChange(change, value)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and hasattr(self, 'scale_bar_attachments'):
            delta = QPointF(value)-self._attachment_origin
            self._attachment_origin = QPointF(value)
            for bar in self.scale_bar_attachments:
                if not sip.isdeleted(bar):bar.setPos(bar.pos()+delta)
        if change == QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged and self._svg_base:
            self._rebuild_svg_text_items()
        return result





    def apply_svg_text_override(
        self,
        text_index: int,
        text: str | None = None,
        *,
        dx: float | None = None,
        dy: float | None = None,
    ) -> None:
        if not self._svg_base:
            return
        if text is None and dx is None and dy is None:
            return
        metadata = dict(self.layout_object.metadata)
        overrides = self._svg_overrides()
        total_dx = dx
        total_dy = dy
        if dx is not None or dy is not None:
            base_entries = svg_text_entries(self._svg_base)
            current_entries = svg_text_entries(self._rendered_svg_bytes())
            if text_index < len(base_entries) and text_index < len(current_entries):
                base_entry = base_entries[text_index]
                current_entry = current_entries[text_index]
                if dx is not None and base_entry.x is not None and current_entry.x is not None:
                    total_dx = (current_entry.x - base_entry.x) + dx
                if dy is not None and base_entry.y is not None and current_entry.y is not None:
                    total_dy = (current_entry.y - base_entry.y) + dy
        overrides = update_svg_text_overrides(
            overrides,
            int(text_index),
            text=text,
            dx=total_dx,
            dy=total_dy,
        )
        metadata["svg_text_overrides"] = overrides
        payload = self.layout_object.to_dict()
        payload["metadata"] = metadata
        self.layout_object = LayoutObject.from_dict(payload)
        self.set_svg(apply_svg_text_overrides(self._svg_base, overrides))
        if self._svg_text_changed_callback is not None:
            self._svg_text_changed_callback()

    def update_geometry(self, x: float, y: float, width: float, height: float) -> None:
        previous = self.rect()
        super().update_geometry(x, y, width, height)
        if previous.size() != self.rect().size():
            sx,sy = self.rect().width()/max(1,previous.width()),self.rect().height()/max(1,previous.height())
            for bar in self.scale_bar_attachments:
                if sip.isdeleted(bar):continue
                relative=bar.pos()-self.pos()
                style=bar.layout_object.style.to_dict()
                style['font_size_pt']*=sy
                style['stroke_width']*=sy
                style['metadata']['label_gap']=float(style['metadata'].get('label_gap',4))*sy
                bar.begin_edit_transaction()
                try:
                    bar.apply_style(LayoutObjectStyle.from_dict(style))
                    bar.update_geometry(self.pos().x()+relative.x()*sx,self.pos().y()+relative.y()*sy,
                                        bar.rect().width()*sx,bar.rect().height()*sy)
                finally:bar.end_edit_transaction()
        if self._svg_base:
            self._update_svg_geometry()
            self._rebuild_svg_text_items()
        else:
            self.set_pixmap(self.pixmap_item.pixmap())

    def attach_scale_bar(self, bar):
        self.scale_bar_attachments.append(bar)
        bar.image_attachment_parent = self

    def contextMenuEvent(self, event) -> None:
        if not self._svg_base:
            return super().contextMenuEvent(event)
        current_svg = self._rendered_svg_bytes()
        menu = QMenu()
        edit_action = menu.addAction("Edit SVG Text...")
        action = menu.exec(event.screenPos())
        if action == edit_action:
            selection = prompt_svg_text_edit(self, current_svg, "Edit SVG Text")
            if selection is None:
                event.ignore()
                return
            entry, text = selection
            self.apply_svg_text_override(entry.index, text=text)
            event.accept()
            return
        event.ignore()

    def prepare_scene_export(self) -> None:
        return

    def finish_scene_export(self) -> None:
        return

    def release_scene_resources(self) -> None:
        items, self._svg_text_items = self._svg_text_items, []
        for item in items:
            detach_graphics_item(item)
        if self._svg_base and graphics_item_is_alive(self.pixmap_item):
            self.pixmap_item.setPixmap(QPixmap())


class LayoutEditorTextItem(SharedTextItem):
    """Shared text with the existing scientific digit-conversion menu."""

    def _sync_layout_text(self) -> None:
        self._sync_layout_object_from_document()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu()
        actions = add_digit_transform_actions(menu, self, self._sync_layout_text)
        action = menu.exec(event.screenPos())
        if apply_digit_transform_action(action, actions):
            event.accept()
            return
        super().contextMenuEvent(event)


def layout_editor_item_for(
    layout_object: LayoutObject,
    pixmap: QPixmap | None = None,
    svg_bytes: bytes | None = None,
    svg_text_changed_callback: Callable[[], None] | None = None,
) -> QGraphicsItem:
    def notify_layout_changed(*_args: object) -> None:
        if svg_text_changed_callback is not None:
            svg_text_changed_callback()

    def install_shared_change_callbacks(item: QGraphicsItem) -> QGraphicsItem:
        # Install only after construction.  Shared items set their initial
        # position/style while being built; those changes must not overwrite
        # persisted records during canvas loading.
        if svg_text_changed_callback is not None:
            if hasattr(item, "_text_changed_callback"):
                item._text_changed_callback = notify_layout_changed
            if hasattr(item, "_geometry_changed_callback"):
                item._geometry_changed_callback = notify_layout_changed
            if hasattr(item, "_style_changed_callback"):
                item._style_changed_callback = notify_layout_changed
        return item

    if layout_object.object_type == "text":
        return install_shared_change_callbacks(LayoutEditorTextItem(layout_object))
    if layout_object.object_type == "scale_bar":
        return LayoutEditorScaleBarItem(layout_object)
    if layout_object.object_type in {"image", "plot"}:
        return LayoutEditorImageProxyItem(
            layout_object,
            pixmap=pixmap,
            svg_bytes=svg_bytes,
            svg_text_changed_callback=svg_text_changed_callback,
        )
    if layout_object.object_type == "group":
        return LayoutEditorGroupShellItem(layout_object)
    return install_shared_change_callbacks(SharedShapeItem(layout_object))


def layout_editor_items(items: Iterable[QGraphicsItem]) -> list[QGraphicsItem]:
    return [item for item in items if item.data(DATA_ROLE_MARKER) == LAYOUT_EDITOR_ITEM_MARKER]
