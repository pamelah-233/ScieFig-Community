from __future__ import annotations

import os

import pytest
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QGraphicsScene

from core.models.layout_editor import LayoutObject, LayoutObjectStyle
from ui.widgets.shared_layout_shapes import (
    SharedShapeItem,
    shape_geometry_from_object,
)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _shape_object(kind: str = "rectangle") -> LayoutObject:
    return LayoutObject(
        object_id=f"{kind}-1",
        object_type="shape",
        x=10.0,
        y=14.0,
        width=80.0,
        height=40.0,
        source=None,
        style=LayoutObjectStyle(stroke_color="#112233", fill_color="#44AA66", stroke_width=2.5, opacity=0.8),
        metadata={
            "shape_kind": kind,
            "stroke_dash": "dash" if kind == "ellipse" else "solid",
            "fill_alpha": 0.5,
            "annotation_kind": "measurement_roi" if kind == "ellipse" else "annotation",
            "x2": 80.0,
            "y2": 40.0,
        },
    )


def test_shape_geometry_uses_canonical_local_endpoints_and_preserves_annotation_kind() -> None:
    layout_object = _shape_object("line")

    geometry = shape_geometry_from_object(layout_object)

    assert geometry.kind == "line"
    assert geometry.start == QPointF(0.0, 0.0)
    assert geometry.end == QPointF(80.0, 40.0)
    assert geometry.annotation_kind == "annotation"


@pytest.mark.parametrize("kind", ["rectangle", "ellipse", "line", "arrow"])
def test_shared_shapes_render_as_vector_items(kind: str) -> None:
    layout_object = _shape_object(kind)
    item = SharedShapeItem(layout_object)
    scene = QGraphicsScene()
    scene.addItem(item)

    image = QImage(110, 75, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    item.paint(painter, None)
    painter.end()

    opaque = sum(1 for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x, y).alpha() > 0)
    assert opaque > 0
    assert item.pen().widthF() == pytest.approx(2.5)
    assert item.brush().color().alpha() == pytest.approx(round(255 * 0.5), abs=1)


def test_shape_style_updates_never_move_geometry_and_shift_constraint_is_available() -> None:
    item = SharedShapeItem(_shape_object("rectangle"))
    item.setPos(30.5, 42.25)
    original_pos = QPointF(item.pos())
    original_rect = item.rect()

    item.apply_style(LayoutObjectStyle(stroke_color="#AA0000", fill_color="#0000FF", stroke_width=4.0))

    assert item.pos() == original_pos
    assert item.rect() == original_rect
    assert item.layout_object.metadata["shape_kind"] == "rectangle"

    item.update_geometry(30.5, 42.25, 90.0, 90.0, modifiers=Qt.KeyboardModifier.ShiftModifier)
    assert item.rect().width() == pytest.approx(item.rect().height())


def test_shape_style_delta_updates_dash_and_alpha_without_geometry_fields() -> None:
    item = SharedShapeItem(_shape_object("rectangle"))
    old_pos = QPointF(item.pos())
    old_rect = item.rect()

    item.apply_shape_style({"stroke_color": "#AA0000", "stroke_width": 3.75, "stroke_dash": "dot", "fill_alpha": 0.25})

    assert item.pos() == old_pos
    assert item.rect() == old_rect
    assert item.layout_object.style.stroke_color == "#AA0000"
    assert item.layout_object.style.stroke_width == pytest.approx(3.75)
    assert item.layout_object.metadata["stroke_dash"] == "dot"
    assert item.layout_object.metadata["fill_alpha"] == pytest.approx(0.25)


def test_shape_geometry_transaction_coalesces_to_one_callback() -> None:
    callbacks: list[QRectF] = []
    item = SharedShapeItem(_shape_object("rectangle"), geometry_changed_callback=lambda _item, rect: callbacks.append(rect))

    item.begin_edit_transaction()
    item.update_geometry(20.0, 24.0, 60.0, 30.0)
    item.end_edit_transaction()

    assert len(callbacks) == 1
    assert callbacks[0].width() == pytest.approx(60.0)


def test_resize_handle_drag_updates_parent_geometry():
    from types import SimpleNamespace
    item=SharedShapeItem(_shape_object())
    handle=item.handles['se']
    event=lambda x,y: SimpleNamespace(scenePos=lambda:QPointF(x,y),modifiers=lambda:Qt.KeyboardModifier.NoModifier,accept=lambda:None)
    handle.mousePressEvent(event(90,54))
    handle.mouseMoveEvent(event(110,64))
    handle.mouseReleaseEvent(event(110,64))
    assert item.layout_object.width == pytest.approx(100)
    assert item.layout_object.height == pytest.approx(50)


def test_arrow_endpoint_remains_visible_outside_initial_box_and_resize_keeps_direction():
    item=SharedShapeItem(_shape_object('arrow'))
    item.set_line_endpoint('start',QPointF(80,0))
    item.set_line_endpoint('end',QPointF(-30,90))
    assert item.boundingRect().contains(QPointF(-30,90))
    item.update_geometry(10,14,160,80)
    geometry=shape_geometry_from_object(item.layout_object)
    assert geometry.start.x()==160
    assert geometry.end.x()==-60 and geometry.end.y()==180
