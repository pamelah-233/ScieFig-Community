from __future__ import annotations

import os

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QGraphicsItem, QGraphicsPixmapItem

from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle
from ui.widgets.layout_editing import (
    EditableLayoutRecord,
    align_records,
    bring_records_to_front,
    move_records,
    normalize_z_order,
    send_records_to_back,
    set_records_hidden,
    set_records_locked,
    visible_records,
)
from ui.widgets.layout_editor_canvas import LayoutEditorCanvas
from ui.widgets.layout_editor_items import DATA_ROLE_MARKER, LAYOUT_EDITOR_ITEM_MARKER, LAYOUT_EDITOR_RESIZE_HANDLE_MARKER, LayoutEditorImageProxyItem, SharedShapeItem
from ui.widgets.editable_text_item import EditableMovableTextItem


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _records() -> list[EditableLayoutRecord]:
    return [
        EditableLayoutRecord("shape", "a", {}, 30, 40, 20, 10, z_order=2),
        EditableLayoutRecord("shape", "b", {}, 80, 20, 30, 15, locked=True, z_order=1),
        EditableLayoutRecord("shape", "c", {}, 120, 60, 25, 25, hidden=True, z_order=3),
        EditableLayoutRecord("shape", "d", {}, 10, 90, 40, 20, z_order=0),
    ]


def test_layout_editing_records_respect_lock_hide_and_z_order() -> None:
    records = _records()

    assert [record.uid for record in visible_records(records)] == ["a", "b", "d"]
    assert move_records(records, 5, -10) is True
    assert records[0].x == 35
    assert records[1].x == 80
    assert records[2].x == 120
    assert records[3].x == 15

    assert align_records(records, "left") is True
    assert records[0].x == 15
    assert records[1].x == 80
    assert records[2].x == 120
    assert records[3].x == 15

    assert set_records_locked([records[0]], True) is True
    assert records[0].locked is True
    assert set_records_hidden([records[3]], True) is True
    assert records[3].hidden is True

    assert normalize_z_order(records) is True
    assert sorted(record.z_order for record in records) == [0, 1, 2, 3]
    assert bring_records_to_front(records, {"b"}) is False
    records[1].locked = False
    assert bring_records_to_front(records, {"b"}) is True
    assert records[1].z_order == max(record.z_order for record in records)
    assert send_records_to_back(records, {"b"}) is True
    assert records[1].z_order == min(record.z_order for record in records)


def test_editable_layout_record_positional_can_resize_compatibility() -> None:
    record = EditableLayoutRecord("text", "id", None, 0, 0, 10, 10, False, False)

    assert record.locked is False
    assert record.can_resize is False
    assert record.hidden is False


def test_layout_editor_canvas_builds_module_neutral_items_and_operations() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="shared-test", width_px=420, height_px=320, dpi=300)
    objects = [
        LayoutObject(
            object_id="shape-a",
            object_type="shape",
            x=20,
            y=30,
            width=80,
            height=40,
            z_order=1,
            style=LayoutObjectStyle(fill_color="#C0392B", stroke_color="#111111"),
        ),
        LayoutObject(
            object_id="shape-b",
            object_type="shape",
            x=160,
            y=75,
            width=50,
            height=30,
            z_order=2,
            style=LayoutObjectStyle(fill_color="#22D7E6", stroke_color="#111111"),
        ),
        LayoutObject(
            object_id="text-a",
            object_type="text",
            x=30,
            y=150,
            width=140,
            height=24,
            text="Shared label",
            style=LayoutObjectStyle(text_color="#111111", font_size_pt=11),
        ),
        LayoutObject(
            object_id="plot-a",
            object_type="plot",
            x=220,
            y=130,
            width=90,
            height=70,
            asset_key="adapter-owned-plot",
        ),
        LayoutObject(object_id="group-a", object_type="group", x=10, y=10, width=340, height=240, z_order=0),
    ]

    def provider(layout_object: LayoutObject) -> QImage | None:
        if layout_object.object_id != "plot-a":
            return None
        image = QImage(90, 70, QImage.Format.Format_RGB32)
        image.fill(QColor("#F2C94C"))
        return image

    canvas.set_layout_objects(spec, objects, pixmap_provider=provider)
    app.processEvents()

    assert len(canvas.layout_items()) == 5
    assert all(item.data(0) == LAYOUT_EDITOR_ITEM_MARKER for item in canvas.layout_items())
    shape_a = canvas.item_for_object_id("shape-a")
    shape_b = canvas.item_for_object_id("shape-b")
    assert isinstance(shape_a, SharedShapeItem)
    assert isinstance(shape_b, SharedShapeItem)
    assert len(shape_a.handles) == 8

    shape_a.setSelected(True)
    shape_b.setSelected(True)
    assert canvas.align_selected("top") is True
    assert shape_a.pos().y() == shape_b.pos().y()
    assert canvas.move_selected(5, 7) is True
    assert canvas.make_selected_same_size(reference_mode="largest") is True
    assert shape_a.rect().width() == shape_b.rect().width()
    snapshot = {layout_object.object_id: layout_object for layout_object in canvas.layout_objects()}
    assert snapshot["shape-a"].x == shape_a.pos().x()
    assert snapshot["shape-a"].y == shape_a.pos().y()
    assert snapshot["shape-a"].width == shape_a.rect().width()
    assert snapshot["shape-b"].height == shape_b.rect().height()
    plot = canvas.item_for_object_id("plot-a")
    assert isinstance(plot, SharedShapeItem)
    pixmap_item = next(child for child in plot.childItems() if child.data(DATA_ROLE_MARKER) == "layout_editor_pixmap_proxy")
    assert pixmap_item.scale() == 1.0
    assert pixmap_item.pos().x() == 0.0
    assert pixmap_item.pos().y() == 0.0

    assert canvas.set_selected_locked(True) == 2
    assert not bool(shape_a.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
    assert canvas.set_selected_hidden(True) == 2
    assert not shape_a.isVisible()
    assert canvas.show_all_layout_items() == 5
    assert shape_a.isVisible()

    shape_a.setSelected(True)
    shape_b.setSelected(True)
    assert canvas.set_selected_locked(False) == 2
    assert canvas.apply_style_to_selected(LayoutObjectStyle(fill_color="#00FF00", stroke_color="#000000")) == 2
    assert shape_a.brush().color().name().lower() == "#00ff00"

    full = canvas.render_full_layout()
    assert not full.isNull()
    assert full.width() == 420
    assert full.height() == 320
    selected = canvas.render_selected_item()
    assert not selected.isNull()
    canvas.close()
    app.processEvents()


def test_layout_editor_canvas_render_hides_editor_affordances() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="render-test", width_px=100, height_px=80)
    canvas.set_layout_objects(
        spec,
        [
            LayoutObject(
                object_id="shape-a",
                object_type="shape",
                x=20,
                y=20,
                width=40,
                height=30,
                style=LayoutObjectStyle(fill_color="#00FF00", stroke_color="transparent", stroke_width=0),
            ),
        ],
    )
    item = canvas.item_for_object_id("shape-a")
    assert isinstance(item, SharedShapeItem)
    item.setSelected(True)
    handles = [child for child in item.childItems() if child.data(DATA_ROLE_MARKER) == LAYOUT_EDITOR_RESIZE_HANDLE_MARKER]
    assert handles
    assert any(handle.isVisible() for handle in handles)

    image = canvas.render_full_layout()

    assert any(handle.isVisible() for handle in handles)
    assert image.pixelColor(22, 22).name().lower() == "#00ff00"
    canvas.close()
    app.processEvents()


def test_layout_editor_canvas_full_layout_can_render_at_export_dpi() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="high-dpi-render-test", width_px=144, height_px=72)
    canvas.set_layout_objects(
        spec,
        [
            LayoutObject(
                object_id="shape-a",
                object_type="shape",
                x=18,
                y=12,
                width=48,
                height=24,
                style=LayoutObjectStyle(fill_color="#00FF00", stroke_color="transparent", stroke_width=0),
            ),
        ],
    )

    image = canvas.render_full_layout(dpi=1200)

    assert image.width() == 2400
    assert image.height() == 1200
    assert image.dotsPerMeterX() > 47000
    assert image.pixelColor(330, 230).name().lower() == "#00ff00"
    canvas.close()
    app.processEvents()


def test_layout_editor_canvas_renders_scale_bar_item() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="scale-bar-render-test", width_px=180, height_px=120)
    scale_bar = LayoutObject(
        object_id="scale-bar-a",
        object_type="scale_bar",
        x=40,
        y=50,
        width=80,
        height=26,
        z_order=1,
        text="50 um",
        style=LayoutObjectStyle(
            stroke_color="#000000",
            text_color="#000000",
            stroke_width=4,
            font_family="Arial",
            font_size_pt=10,
            metadata={"text_position": "above", "label_gap": 2.0, "show_text": True},
        ),
    )
    canvas.set_layout_objects(spec, [scale_bar])

    image = canvas.render_full_layout()
    line_y = 68
    dark_line_pixels = 0
    for x in range(42, 118):
        color = image.pixelColor(x, line_y)
        if color.red() < 80 and color.green() < 80 and color.blue() < 80:
            dark_line_pixels += 1

    assert dark_line_pixels > 70
    canvas.close()
    app.processEvents()


def test_layout_editor_image_proxy_fits_inside_and_centers_pixmap() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="proxy-fit-test", width_px=160, height_px=140)
    plot = LayoutObject(object_id="plot-a", object_type="plot", x=0, y=0, width=100, height=100)

    def provider(_layout_object: LayoutObject) -> QImage:
        image = QImage(100, 50, QImage.Format.Format_RGB32)
        image.fill(QColor("#F2C94C"))
        return image

    canvas.set_layout_objects(spec, [plot], pixmap_provider=provider)
    plot_item = canvas.item_for_object_id("plot-a")
    assert isinstance(plot_item, SharedShapeItem)
    pixmap_item = next(child for child in plot_item.childItems() if child.data(DATA_ROLE_MARKER) == "layout_editor_pixmap_proxy")

    assert pixmap_item.scale() == 1.0
    assert pixmap_item.pos().x() == 0.0
    assert pixmap_item.pos().y() == 25.0
    canvas.close()
    app.processEvents()


def test_layout_editor_svg_plot_persists_text_override_and_notifies_owner() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="svg-edit-test", width_px=240, height_px=160)
    plot = LayoutObject(object_id="plot-a", object_type="plot", x=0, y=0, width=160, height=100)
    changes: list[bool] = []
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><text x="10" y="20">Axis</text></svg>'''

    canvas.set_layout_objects(
        spec,
        [plot],
        pixmap_provider=lambda _layout_object: svg,
        svg_text_changed_callback=lambda: changes.append(True),
    )
    plot_item = canvas.item_for_object_id("plot-a")
    assert isinstance(plot_item, LayoutEditorImageProxyItem)
    assert any(isinstance(child, QGraphicsPixmapItem) for child in plot_item.childItems())

    plot_item.apply_svg_text_override(0, "Edited axis")

    assert changes == [True]
    assert canvas.layout_objects()[0].metadata["svg_text_overrides"] == {"0": "Edited axis"}
    canvas.close()
    app.processEvents()


def test_layout_editor_svg_plot_persists_text_position_override() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="svg-move-test", width_px=240, height_px=160)
    plot = LayoutObject(object_id="plot-a", object_type="plot", x=0, y=0, width=160, height=100)
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><text x="10" y="20">Axis</text></svg>'''

    canvas.set_layout_objects(
        spec,
        [plot],
        pixmap_provider=lambda _layout_object: svg,
    )
    plot_item = canvas.item_for_object_id("plot-a")
    assert isinstance(plot_item, LayoutEditorImageProxyItem)

    plot_item.apply_svg_text_override(0, dx=16.0, dy=14.0)

    assert canvas.layout_objects()[0].metadata["svg_text_overrides"] == {"0": {"dx": 16.0, "dy": 14.0}}
    canvas.close()
    app.processEvents()


def test_layout_editor_svg_plot_exposes_direct_editable_text_items_and_persists_move() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="svg-overlay-test", width_px=240, height_px=160)
    plot = LayoutObject(object_id="plot-a", object_type="plot", x=0, y=0, width=160, height=100, style=LayoutObjectStyle(font_family="Arial", font_size_pt=10.0, text_color="#111111"))
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><text x="10" y="20">Axis</text></svg>'''

    canvas.set_layout_objects(
        spec,
        [plot],
        pixmap_provider=lambda _layout_object: svg,
    )
    plot_item = canvas.item_for_object_id("plot-a")
    assert isinstance(plot_item, LayoutEditorImageProxyItem)
    from ui.widgets.shared_svg_text import SvgEditableTextItem
    text_items = [item for item in canvas.scene.items() if isinstance(item, SvgEditableTextItem)]

    assert len(text_items) == 1
    assert text_items[0].toPlainText() == "Axis"
    assert text_items[0].parentItem() is plot_item.svg_item
    assert text_items[0].pos().x() == 10.0
    assert text_items[0].pos().y() < 20.0  # SVG y is a baseline, not a top edge.
    assert b"<text" not in plot_item._display_svg_bytes

    text_items[0].setPos(text_items[0].pos().x() + 18.0, text_items[0].pos().y() + 12.0)

    overrides = canvas.layout_objects()[0].metadata["svg_text_overrides"]
    override = dict(overrides["0"])
    assert override["x"] == 28.0
    assert override["y"] == 32.0

    plot_item.apply_svg_text_override(0, "Edited axis")

    override = dict(canvas.layout_objects()[0].metadata["svg_text_overrides"]["0"])
    assert override["text"] == "Edited axis"
    assert override["x"] == 28.0
    assert override["y"] == 32.0
    canvas.close()
    app.processEvents()


def test_layout_editor_svg_plot_rebuild_keeps_single_overlay_layer() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="svg-rebuild-test", width_px=240, height_px=160)
    plot = LayoutObject(object_id="plot-a", object_type="plot", x=0, y=0, width=160, height=100)
    svg = b'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="100"><text x="10" y="20">Axis</text></svg>'''

    canvas.set_layout_objects(
        spec,
        [plot],
        pixmap_provider=lambda _layout_object: svg,
    )
    first_plot_item = canvas.item_for_object_id("plot-a")
    assert isinstance(first_plot_item, LayoutEditorImageProxyItem)
    assert b"<text" not in first_plot_item._display_svg_bytes

    canvas.set_layout_objects(
        spec,
        canvas.layout_objects(),
        pixmap_provider=lambda _layout_object: svg,
    )
    app.processEvents()

    second_plot_item = canvas.item_for_object_id("plot-a")
    from ui.widgets.shared_svg_text import SvgEditableTextItem
    overlay_items = [item for item in canvas.scene.items() if isinstance(item, SvgEditableTextItem)]

    assert isinstance(second_plot_item, LayoutEditorImageProxyItem)
    assert b"<text" not in second_plot_item._display_svg_bytes
    assert len(overlay_items) == 1
    assert overlay_items[0].toPlainText() == "Axis"
    canvas.close()
    app.processEvents()


def test_layout_editor_canvas_text_snapshot_preserves_persisted_height() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="text-snapshot-test", width_px=240, height_px=160)
    text = LayoutObject(
        object_id="text-a",
        object_type="text",
        x=10,
        y=20,
        width=140,
        height=80,
        text="Label",
        style=LayoutObjectStyle(text_color="#111111", font_size_pt=11),
    )

    canvas.set_layout_objects(spec, [text])
    snapshot = canvas.layout_objects()[0]

    assert snapshot.height == 80
    assert snapshot.to_dict() == text.to_dict()
    canvas.close()
    app.processEvents()


def test_layout_editor_canvas_delete_and_style_skip_locked_items() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="delete-test", width_px=200, height_px=160)
    canvas.set_layout_objects(
        spec,
        [
            LayoutObject(object_id="shape-a", object_type="shape", x=0, y=0, width=20, height=20, locked=True),
            LayoutObject(object_id="shape-b", object_type="shape", x=40, y=0, width=20, height=20),
        ],
    )
    locked = canvas.item_for_object_id("shape-a")
    unlocked = canvas.item_for_object_id("shape-b")
    assert isinstance(locked, SharedShapeItem)
    assert isinstance(unlocked, SharedShapeItem)
    locked.setSelected(True)
    unlocked.setSelected(True)

    assert canvas.apply_style_to_selected(LayoutObjectStyle(fill_color="#00FF00")) == 1
    assert locked.brush().color().name().lower() != "#00ff00"
    assert unlocked.brush().color().name().lower() == "#00ff00"

    assert canvas.delete_selected() == 1
    assert canvas.item_for_object_id("shape-a") is not None
    assert canvas.item_for_object_id("shape-b") is None
    canvas.close()
    app.processEvents()


def test_layout_editor_shared_text_and_shape_callbacks_skip_initial_load() -> None:
    app = _app()
    canvas = LayoutEditorCanvas()
    spec = LayoutCanvasSpec(canvas_id="shared-callback-test", width_px=220, height_px=140)
    text = LayoutObject(object_id="text-a", object_type="text", x=10, y=12, width=100, height=30, text="A")
    shape = LayoutObject(object_id="shape-a", object_type="shape", x=40, y=60, width=40, height=30)
    changes: list[bool] = []

    canvas.set_layout_objects(spec, [text, shape], svg_text_changed_callback=lambda: changes.append(True))
    assert changes == []

    text_item = canvas.item_for_object_id("text-a")
    shape_item = canvas.item_for_object_id("shape-a")
    assert text_item is not None
    assert shape_item is not None
    text_item.begin_edit()
    text_item.setPlainText("Edited")
    text_item.finish_edit()
    assert changes

    before_shape_move = len(changes)
    shape_item.setPos(55, 62)
    assert len(changes) > before_shape_move
    before_shape_style = len(changes)
    shape_item.apply_shape_style({"stroke_color": "#AA0000"})
    assert len(changes) > before_shape_style
    canvas.close()
    app.processEvents()
