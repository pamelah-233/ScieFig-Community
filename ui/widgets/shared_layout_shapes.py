"""Shared vector annotation shapes for layout scenes.

Geometry is expressed in the caller's scene units.  ``stroke_width`` is the
persisted style width (points by project convention); any scene-scale
conversion belongs to the adapter that creates the ``LayoutObject``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPainterPath, QPainterPathStroker, QPen
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsRectItem

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_edit_notifications import EditNotifications
from ui.widgets.layout_editing import RESIZE_HANDLE_ROLES, RESIZE_HANDLE_SIZE_PX


SHARED_SHAPE_ITEM_MARKER = "layout_editor_item"
SHARED_RESIZE_HANDLE_MARKER = "layout_editor_resize_handle"
DATA_ROLE_MARKER = 0
DATA_ROLE_OBJECT_ID = 1
DATA_ROLE_OBJECT_TYPE = 2


def _color(value: str, fallback: str = "#000000") -> QColor:
    if not value or value == "transparent":
        return QColor(Qt.GlobalColor.transparent)
    color = QColor(str(value))
    return color if color.isValid() else QColor(fallback)


def _metadata_value(layout_object: LayoutObject, key: str, default: Any = None) -> Any:
    metadata = dict(layout_object.metadata)
    if key in metadata:
        return metadata[key]
    style_metadata = dict(layout_object.style.metadata)
    return style_metadata.get(key, default)


@dataclass(frozen=True)
class ShapeGeometry:
    kind: str
    rect: QRectF
    start: QPointF
    end: QPointF
    annotation_kind: str
    shift_constrained: bool = False


def _resolve_kind(layout_object: LayoutObject) -> str:
    kind = str(_metadata_value(layout_object, "shape_kind", "rectangle") or "rectangle").lower()
    return kind if kind in {"rectangle", "ellipse", "line", "arrow"} else "rectangle"


def shape_geometry_from_object(
    layout_object: LayoutObject,
    *,
    modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
) -> ShapeGeometry:
    kind = _resolve_kind(layout_object)
    width = max(0.0, float(layout_object.width))
    height = max(0.0, float(layout_object.height))
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        size = max(width, height)
        width = height = size
    rect = QRectF(0.0, 0.0, width, height)
    start_x = _metadata_value(layout_object, "x1", None)
    start_y = _metadata_value(layout_object, "y1", None)
    start = QPointF(
        0.0 if start_x is None else float(start_x),
        0.0 if start_y is None else float(start_y),
    )
    end_x = _metadata_value(layout_object, "x2", None)
    end_y = _metadata_value(layout_object, "y2", None)
    end = QPointF(
        width if end_x is None else float(end_x),
        height if end_y is None else float(end_y),
    )
    if kind in {"line", "arrow"} and modifiers & Qt.KeyboardModifier.ShiftModifier:
        end = _constrain_line_endpoint(start, end)
    return ShapeGeometry(
        kind=kind,
        rect=rect,
        start=start,
        end=end,
        annotation_kind=str(_metadata_value(layout_object, "annotation_kind", "annotation") or "annotation"),
        shift_constrained=bool(modifiers & Qt.KeyboardModifier.ShiftModifier),
    )


def _constrain_line_endpoint(start: QPointF, end: QPointF) -> QPointF:
    dx = end.x() - start.x()
    dy = end.y() - start.y()
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return QPointF(start)
    angle = math.atan2(dy, dx)
    snapped = round(angle / (math.pi / 4.0)) * (math.pi / 4.0)
    return QPointF(start.x() + math.cos(snapped) * length, start.y() + math.sin(snapped) * length)


class SharedResizeHandle(QGraphicsRectItem):
    def __init__(self, parent: QGraphicsItem, role: str) -> None:
        super().__init__(
            -RESIZE_HANDLE_SIZE_PX / 2.0,
            -RESIZE_HANDLE_SIZE_PX / 2.0,
            RESIZE_HANDLE_SIZE_PX,
            RESIZE_HANDLE_SIZE_PX,
            parent,
        )
        if role not in RESIZE_HANDLE_ROLES and role not in {"start", "end"}:
            raise ValueError(f"Unsupported resize handle role: {role}")
        self.role = role
        self.setData(DATA_ROLE_MARKER, SHARED_RESIZE_HANDLE_MARKER)
        self.setBrush(QBrush(QColor("#FFFFFF")))
        self.setPen(QPen(QColor("#2F6BFF"), 1.0))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setZValue(10_000)
        self.setVisible(False)

    def mousePressEvent(self, event):
        parent=self.parentItem()
        if getattr(parent.layout_object,'locked',False):
            return
        self._start_object=parent.layout_object
        self._scene_transform=parent.sceneTransform()
        self._inverse=self._scene_transform.inverted()[0]
        self._start_point=self._inverse.map(event.scenePos())
        event.accept()

    def mouseMoveEvent(self, event):
        if not hasattr(self,'_start_object'):
            return
        parent=self.parentItem()
        obj=self._start_object
        point=self._inverse.map(event.scenePos())
        if self.role in {'start','end'} and hasattr(parent,'set_line_endpoint'):
            parent.set_line_endpoint(self.role,point,event.modifiers())
            event.accept()
            return
        delta=point-self._start_point
        left,top,right,bottom=0.0,0.0,obj.width,obj.height
        if 'e' in self.role:right=max(1.0,obj.width+delta.x())
        if 's' in self.role:bottom=max(1.0,obj.height+delta.y())
        if 'w' in self.role:left=min(obj.width-1.0,delta.x())
        if 'n' in self.role:top=min(obj.height-1.0,delta.y())
        width,height=right-left,bottom-top
        shift=bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        image=obj.object_type in {'image','plot'}
        aspect_locked=bool(obj.metadata.get('aspect_locked',True))
        views=parent.scene().views() if parent.scene() is not None else []
        tools=getattr(getattr(views[0],'object_interaction',None),'editing_tools',None) if views else None
        if image and tools is not None:aspect_locked=tools.image_aspect_locked(parent)
        if image and aspect_locked != shift or obj.object_type=='text' and shift:
            from ui.widgets.layout_editing import aspect_resize_size
            width,height=aspect_resize_size(obj.width,obj.height,width,height,self.role)
        elif not image and shift:
            width=height=max(width,height)
        if 'w' in self.role:left=obj.width-width
        if 'n' in self.role:top=obj.height-height
        if image and aspect_locked != shift:
            if self.role in {'e','w'}:top=(obj.height-height)/2
            if self.role in {'n','s'}:left=(obj.width-width)/2
        scene_origin=self._scene_transform.map(QPointF(left,top))
        grandparent=parent.parentItem()
        origin=grandparent.mapFromScene(scene_origin) if grandparent else scene_origin
        parent.update_geometry(origin.x(),origin.y(),width,height)
        event.accept()

    def mouseReleaseEvent(self,event):
        if hasattr(self,'_start_object'):
            del self._start_object
        event.accept()


def _item_flags(movable: bool = True) -> QGraphicsItem.GraphicsItemFlag:
    flags = (
        QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
    )
    if movable:
        flags |= QGraphicsItem.GraphicsItemFlag.ItemIsMovable
    return flags


GeometryChangedCallback = Callable[["SharedShapeItem", QRectF], None]
StyleChangedCallback = Callable[["SharedShapeItem", Mapping[str, Any]], None]


class SharedShapeItem(QGraphicsRectItem):
    """Vector rectangle/ellipse/line/arrow with shared selection affordances."""

    def __init__(
        self,
        layout_object: LayoutObject,
        geometry_changed_callback: GeometryChangedCallback | None = None,
        style_changed_callback: StyleChangedCallback | None = None,
        parent: QGraphicsItem | None = None,
    ) -> None:
        super().__init__(0.0, 0.0, float(layout_object.width), float(layout_object.height), parent)
        self.layout_object = layout_object
        self._geometry_changed_callback = geometry_changed_callback
        self._style_changed_callback = style_changed_callback
        self._edits = EditNotifications()
        self.setData(DATA_ROLE_MARKER, SHARED_SHAPE_ITEM_MARKER)
        self.setData(DATA_ROLE_OBJECT_ID, layout_object.object_id)
        self.setData(DATA_ROLE_OBJECT_TYPE, layout_object.object_type)
        self.setPos(float(layout_object.x), float(layout_object.y))
        self.setRotation(float(layout_object.rotation_deg))
        self.setZValue(float(layout_object.z_order))
        self.setVisible(not layout_object.hidden)
        self.handles = {role: SharedResizeHandle(self, role) for role in RESIZE_HANDLE_ROLES}
        kind = _resolve_kind(layout_object)
        if kind in {"line", "arrow"}:
            self.handles["start"] = SharedResizeHandle(self, "start")
            self.handles["end"] = SharedResizeHandle(self, "end")
        self.set_flags_for_locked(layout_object.locked)
        self.apply_style(layout_object.style)
        self.update_handles()
        self._edits.enabled = True

    @property
    def shape_kind(self) -> str:
        return _resolve_kind(self.layout_object)

    @property
    def annotation_kind(self) -> str:
        return str(_metadata_value(self.layout_object, "annotation_kind", "annotation") or "annotation")

    def begin_edit_transaction(self) -> None:
        self._edits.begin()

    def end_edit_transaction(self) -> None:
        self._edits.end()

    def set_flags_for_locked(self, locked: bool) -> None:
        payload = self.layout_object.to_dict()
        payload["locked"] = bool(locked)
        self.layout_object = LayoutObject.from_dict(payload)
        self.setFlags(_item_flags(movable=not locked))
        self.update_handles()

    def set_hidden(self, hidden: bool) -> None:
        payload = self.layout_object.to_dict()
        payload["hidden"] = bool(hidden)
        self.layout_object = LayoutObject.from_dict(payload)
        self.setVisible(not hidden)
        self.update_handles()

    def apply_style(self, style: LayoutObjectStyle) -> None:
        payload = self.layout_object.to_dict()
        payload["style"] = style.to_dict()
        self.layout_object = LayoutObject.from_dict(payload)
        self._apply_qt_style(style)
        self.update()
        self._edits.emit("style", self._style_changed_callback, self, style.to_dict())

    def apply_shape_style(self, delta: Mapping[str, Any]) -> None:
        """Apply only visual shape fields; geometry/endpoints are excluded."""

        payload = self.layout_object.to_dict()
        style = self.layout_object.style.to_dict()
        metadata = dict(payload.get("metadata", {}))
        for key in ("stroke_color", "stroke_width", "fill_color", "opacity"):
            if key in delta:
                style[key] = delta[key]
        if "stroke_width_pt" in delta and "stroke_width" not in delta:
            style["stroke_width"] = delta["stroke_width_pt"]
        for key in ("stroke_dash", "fill_alpha", "annotation_kind"):
            if key in delta:
                metadata[key] = delta[key]
        payload["style"] = LayoutObjectStyle.from_dict(style).to_dict()
        payload["metadata"] = metadata
        self.layout_object = LayoutObject.from_dict(payload)
        self._apply_qt_style(self.layout_object.style)
        self.update()
        self._edits.emit("style", self._style_changed_callback, self, dict(delta))

    def set_line_endpoint(self,role,point,modifiers=Qt.KeyboardModifier.NoModifier):
        self.prepareGeometryChange()
        geometry=shape_geometry_from_object(self.layout_object)
        other=geometry.end if role=='start' else geometry.start
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            point=_constrain_line_endpoint(other,point)
        payload=self.layout_object.to_dict()
        suffix='1' if role=='start' else '2'
        payload['metadata']['x'+suffix]=point.x()
        payload['metadata']['y'+suffix]=point.y()
        self.layout_object=LayoutObject.from_dict(payload)
        self.update_handles()
        self.update()
        self._edits.emit('geometry',self._geometry_changed_callback,self,self.rect())

    def boundingRect(self):
        base=super().boundingRect()
        if hasattr(self,'layout_object') and self.shape_kind in {'line','arrow'}:
            geometry=shape_geometry_from_object(self.layout_object)
            margin=max(10.0,self.pen().widthF()*4)
            return base.united(QRectF(geometry.start,geometry.end).normalized().adjusted(-margin,-margin,margin,margin))
        return base

    def _apply_qt_style(self, style: LayoutObjectStyle) -> None:
        pen = QPen(_color(style.stroke_color), max(0.0, float(style.stroke_width)))
        dash = str(_metadata_value(self.layout_object, "stroke_dash", "solid") or "solid").lower()
        pen.setStyle(
            {
                "dash": Qt.PenStyle.DashLine,
                "dot": Qt.PenStyle.DotLine,
                "dashdot": Qt.PenStyle.DashDotLine,
                "dash_dot": Qt.PenStyle.DashDotLine,
            }.get(dash, Qt.PenStyle.SolidLine)
        )
        fill = _color(style.fill_color)
        fill_alpha = _metadata_value(self.layout_object, "fill_alpha", None)
        if fill_alpha is not None:
            alpha = float(fill_alpha)
            if alpha <= 1.0:
                alpha *= 255.0
            fill.setAlpha(max(0, min(255, int(round(alpha)))))
        self.setPen(pen)
        self.setBrush(QBrush(fill))
        self.setOpacity(float(style.opacity))

    def update_geometry(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> None:
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            width = height = max(float(width), float(height))
        with self._edits.transaction():
            previous=shape_geometry_from_object(self.layout_object)
            old_width=max(1.0,self.layout_object.width)
            old_height=max(1.0,self.layout_object.height)
            self.prepareGeometryChange()
            self.setPos(float(x), float(y))
            self.setRect(0.0, 0.0, max(0.0, float(width)), max(0.0, float(height)))
            payload = self.layout_object.to_dict()
            payload.update({"x": float(x), "y": float(y), "width": float(width), "height": float(height)})
            if self.shape_kind in {"line", "arrow"}:
                metadata = dict(payload.get("metadata", {}))
                for suffix,point in [('1',previous.start),('2',previous.end)]:
                    metadata['x'+suffix]=point.x()*float(width)/old_width
                    metadata['y'+suffix]=point.y()*float(height)/old_height
                payload["metadata"] = metadata
            self.layout_object = LayoutObject.from_dict(payload)
            self.update_handles()
            self._edits.emit("geometry", self._geometry_changed_callback, self, self.rect())

    def update_handles(self) -> None:
        rect = self.rect()
        left, right, top, bottom = rect.left(), rect.right(), rect.top(), rect.bottom()
        center_x, center_y = (left + right) / 2.0, (top + bottom) / 2.0
        points = {
            "nw": QPointF(left, top),
            "n": QPointF(center_x, top),
            "ne": QPointF(right, top),
            "e": QPointF(right, center_y),
            "se": QPointF(right, bottom),
            "s": QPointF(center_x, bottom),
            "sw": QPointF(left, bottom),
            "w": QPointF(left, center_y),
        }
        geometry = shape_geometry_from_object(self.layout_object)
        if "start" in self.handles:
            points["start"] = geometry.start
            points["end"] = geometry.end
        for role, handle in self.handles.items():
            handle.setPos(points[role])
            handle.setVisible(self.isSelected() and self.isVisible() and not self.layout_object.locked)

    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value: Any):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            self.update_handles()
        elif change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            payload = self.layout_object.to_dict()
            payload["x"] = float(value.x())
            payload["y"] = float(value.y())
            self.layout_object = LayoutObject.from_dict(payload)
            self._edits.emit("geometry", self._geometry_changed_callback, self, self.rect())
        return super().itemChange(change, value)

    def shape(self) -> QPainterPath:
        geometry = shape_geometry_from_object(self.layout_object)
        if geometry.kind in {"line", "arrow"}:
            path = QPainterPath(geometry.start)
            path.lineTo(geometry.end)
            stroker = QPainterPathStroker()
            stroker.setWidth(max(6.0, self.pen().widthF() + 6.0))
            return stroker.createStroke(path)
        return super().shape()

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001, D102
        del option, widget
        geometry = shape_geometry_from_object(self.layout_object)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(self.pen())
        painter.setBrush(self.brush())
        if geometry.kind == "ellipse":
            painter.drawEllipse(geometry.rect)
        elif geometry.kind in {"line", "arrow"}:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawLine(geometry.start, geometry.end)
            if geometry.kind == "arrow":
                _paint_arrow_head(painter, geometry.start, geometry.end, self.pen())
        else:
            painter.drawRect(geometry.rect)


def _paint_arrow_head(painter: QPainter, start: QPointF, end: QPointF, pen: QPen) -> None:
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy)
    if length <= 0.0:
        return
    ux, uy = dx / length, dy / length
    size = max(6.0, pen.widthF() * 3.0)
    base = QPointF(end.x() - ux * size, end.y() - uy * size)
    normal = QPointF(-uy * size * 0.55, ux * size * 0.55)
    head = QPainterPath(end)
    head.lineTo(base + normal)
    head.lineTo(base - normal)
    head.closeSubpath()
    painter.save()
    painter.setBrush(pen.color())
    painter.drawPath(head)
    painter.restore()
