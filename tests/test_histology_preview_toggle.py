from __future__ import annotations

import os

from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel

from core.models import BrightfieldPanel, IHCPanel
from ui.widgets.shared_svg_text import SvgEditableTextItem
from ui.widgets.layout_editor_items import LayoutEditorTextItem
from ui.panels.brightfield_workspace import BrightfieldWorkspace
from ui.panels.ihc_workspace import IHCWorkspace


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _pixmap(color: str) -> QPixmap:
    pixmap = QPixmap(12, 10)
    pixmap.fill(QColor(color))
    return pixmap


def _source_color(workspace) -> QColor:
    assert workspace.canvas._source_pixmap is not None
    return workspace.canvas._source_pixmap.toImage().pixelColor(0, 0)


def test_ihc_workspace_switches_cached_original_and_overlay_pixmaps() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))

    workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")

    assert workspace.current_preview_mode() == "original"
    assert workspace.original_preview_button.isChecked()
    assert not workspace.overlay_preview_button.isEnabled()
    assert _source_color(workspace) == QColor("#AA0000")

    workspace.canvas.set_pixmap(_pixmap("#00AA00"), title="Histology / IHC analysis overlay")

    assert workspace.current_preview_mode() == "overlay"
    assert workspace.overlay_preview_button.isEnabled()
    assert workspace.overlay_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#00AA00")

    workspace.original_preview_button.click()
    app.processEvents()

    assert workspace.current_preview_mode() == "original"
    assert workspace.original_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#AA0000")

    workspace.overlay_preview_button.click()
    app.processEvents()

    assert workspace.current_preview_mode() == "overlay"
    assert workspace.overlay_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#00AA00")


def test_ihc_preview_toggle_remains_visible_after_measurement_step() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    workspace.show()
    workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")
    workspace.canvas.set_pixmap(_pixmap("#00AA00"), title="Histology / IHC analysis overlay")

    workspace.set_step(1)
    app.processEvents()

    assert workspace.original_preview_button.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isEnabled()
    workspace.close()
    app.processEvents()


def test_ihc_preview_toggle_is_hidden_during_import_step() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    workspace.show()
    workspace.set_step(0)
    workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")
    workspace.canvas.set_pixmap(_pixmap("#00AA00"), title="Histology / IHC analysis overlay")
    app.processEvents()

    assert workspace.preview_toggle_panel.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isEnabled()
    workspace.close()
    app.processEvents()


def test_ihc_preview_canvas_reuses_scaled_base_pixmap_for_roi_refreshes() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    workspace.show()
    workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")
    app.processEvents()

    workspace.canvas.set_roi_rect(None)
    cache_size = len(workspace.canvas._scaled_base_cache)
    workspace.canvas.set_roi_rect(None)

    assert len(workspace.canvas._scaled_base_cache) == cache_size
    workspace.close()
    app.processEvents()


def test_ihc_layout_tools_are_hidden_until_layout_canvas_is_open() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    workspace.show()
    app.processEvents()

    assert all(not button.isVisibleTo(workspace.workflow_panel) for button in workspace.layout_tool_buttons)

    workspace.show_preview()
    app.processEvents()

    assert all(not button.isVisibleTo(workspace.workflow_panel) for button in workspace.layout_tool_buttons)

    workspace.close()
    app.processEvents()


def test_ihc_layout_scale_bar_controls_write_current_image_payload() -> None:
    app = _app()
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-ui")
    panel.data["active_image_uid"] = "image-a"
    panel.data["ihc_images"] = [{"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480, "pixel_size_um": 0.5}, "measurements": []}]
    workspace = IHCWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.set_step(3)

    assert workspace.scale_bar_length_spin.value() == 50.0
    assert workspace.scale_bar_text_position_combo.currentText() in {"Above", "Below"}
    assert workspace.scale_bar_apply_current_button.text()
    assert workspace.scale_bar_apply_all_button.text()
    assert workspace.scale_bar_remove_current_button.text()
    assert workspace.scale_bar_remove_all_button.text()

    workspace.scale_bar_manual_um_per_px_spin.setValue(0.25)
    workspace.scale_bar_line_width_spin.setValue(5.0)
    workspace.scale_bar_text_position_combo.setCurrentText("Below")
    workspace.scale_bar_line_color_combo.setCurrentIndex(workspace.scale_bar_line_color_combo.findData("#111111"))
    workspace.scale_bar_text_color_combo.setCurrentIndex(workspace.scale_bar_text_color_combo.findData("#222222"))
    workspace.scale_bar_apply_current_button.click()

    layout = panel.data["ihc_layout"]
    state = layout["scale_bars"][0]
    assert state["template"]["label_length_um"] == 50.0
    assert state["template"]["line_thickness"] == 5.0
    assert state["template"]["text_position"] == "below"
    assert state["template"]["line_color"] == "#111111"
    assert state["template"]["text_color"] == "#222222"
    assert len(layout["scale_bars"]) == 1
    assert state["target_id"] == "image-a" and state["enabled"] is True
    assert state["manual_um_per_px"] == 0.25

    workspace.scale_bar_remove_current_button.click()
    assert layout["scale_bars"][0]["enabled"] is False
    workspace.close()
    app.processEvents()


def test_ihc_layout_scale_bar_apply_keeps_measurement_overlay_visible() -> None:
    app = _app()
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-ui-overlay")
    panel.data["active_image_uid"] = "image-a"
    panel.data["ihc_images"] = [{"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480, "pixel_size_um": 0.5}, "measurements": []}]
    measurement = {"measurement_uuid": "m1", "image_uid": "image-a"}
    workspace = IHCWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.prepare_layout(measurement=measurement)

    assert any(obj.asset_key == "measurement_overlay" for obj in workspace.layout_canvas.layout_objects())
    workspace.scale_bar_apply_current_button.click()

    objects = workspace.layout_canvas.layout_objects()
    assert any(obj.asset_key == "measurement_overlay" for obj in objects)
    assert any(obj.object_type == "scale_bar" for obj in objects)
    workspace.close()
    app.processEvents()


def test_brightfield_workspace_switches_cached_original_and_overlay_pixmaps() -> None:
    app = _app()
    workspace = BrightfieldWorkspace(QLabel("context"))

    workspace.canvas.set_display_state(_pixmap("#1111AA"), title="Brightfield preview")

    assert workspace.current_preview_mode() == "original"
    assert workspace.original_preview_button.isChecked()
    assert not workspace.overlay_preview_button.isEnabled()
    assert _source_color(workspace) == QColor("#1111AA")

    workspace.canvas.set_display_state(_pixmap("#AA11AA"), title="Brightfield segmentation overlay")

    assert workspace.current_preview_mode() == "overlay"
    assert workspace.overlay_preview_button.isEnabled()
    assert workspace.overlay_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#AA11AA")

    workspace.original_preview_button.click()
    app.processEvents()

    assert workspace.current_preview_mode() == "original"
    assert workspace.original_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#1111AA")

    workspace.overlay_preview_button.click()
    app.processEvents()

    assert workspace.current_preview_mode() == "overlay"
    assert workspace.overlay_preview_button.isChecked()
    assert _source_color(workspace) == QColor("#AA11AA")


def test_brightfield_preview_toggle_remains_visible_after_measurement_step() -> None:
    app = _app()
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.show()
    workspace.canvas.set_display_state(_pixmap("#1111AA"), title="Brightfield preview")
    workspace.canvas.set_display_state(_pixmap("#AA11AA"), title="Brightfield segmentation overlay")

    workspace.set_step(2)
    app.processEvents()

    assert workspace.original_preview_button.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isEnabled()
    workspace.close()
    app.processEvents()


def test_brightfield_preview_toggle_is_hidden_during_import_step() -> None:
    app = _app()
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.show()
    workspace.set_step(0)
    workspace.canvas.set_display_state(_pixmap("#1111AA"), title="Brightfield preview")
    workspace.canvas.set_display_state(_pixmap("#AA11AA"), title="Brightfield segmentation overlay")
    app.processEvents()

    assert workspace.preview_toggle_panel.isVisibleTo(workspace.workflow_panel)
    assert workspace.overlay_preview_button.isEnabled()
    workspace.close()
    app.processEvents()


def test_brightfield_layout_tools_are_hidden_until_layout_canvas_is_open() -> None:
    app = _app()
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.show()
    app.processEvents()

    assert all(not button.isVisibleTo(workspace.workflow_panel) for button in workspace.layout_tool_buttons)

    workspace.show_preview()
    app.processEvents()

    assert all(not button.isVisibleTo(workspace.workflow_panel) for button in workspace.layout_tool_buttons)

    workspace.close()
    app.processEvents()


def test_brightfield_layout_scale_bar_controls_write_all_images_payload() -> None:
    app = _app()
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-ui")
    panel.data["active_image_uid"] = "image-a"
    panel.data["brightfield_images"] = [
        {"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480}, "measurements": []},
        {"uid": "image-b", "metadata": {"width_px": 800, "height_px": 600, "pixel_size_um": 1.0}, "measurements": []},
    ]
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_length_spin.setValue(75.0)
    workspace.scale_bar_line_color_combo.setCurrentIndex(workspace.scale_bar_line_color_combo.findData("#111111"))
    workspace.scale_bar_text_color_combo.setCurrentIndex(workspace.scale_bar_text_color_combo.findData("#222222"))
    workspace.scale_bar_apply_all_button.click()

    layout = panel.data["brightfield_layout"]
    assert [item["target_id"] for item in layout["scale_bars"]] == ["image-a", "image-b"]
    for state in layout["scale_bars"]:
        assert state["template"]["label_length_um"] == 75.0
        assert state["template"]["line_color"] == "#111111"
        assert state["template"]["text_color"] == "#222222"
        assert state["enabled"] is True
        assert "manual_um_per_px" not in state

    workspace.scale_bar_remove_all_button.click()
    assert [item["enabled"] for item in layout["scale_bars"]] == [False, False]
    workspace.close()
    app.processEvents()


def test_brightfield_layout_scale_bar_apply_keeps_measurement_overlay_visible() -> None:
    app = _app()
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-ui-overlay")
    panel.data["active_image_uid"] = "image-a"
    panel.data["brightfield_images"] = [{"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480}, "measurements": []}]
    measurement = {"measurement_uuid": "m1", "image_uid": "image-a"}
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.set_step(4)
    workspace.prepare_layout(measurement=measurement)

    assert any(obj.asset_key == "measurement_overlay" for obj in workspace.layout_canvas.layout_objects())
    workspace.scale_bar_apply_current_button.click()

    objects = workspace.layout_canvas.layout_objects()
    assert any(obj.asset_key == "measurement_overlay" for obj in objects)
    assert any(obj.object_type == "scale_bar" for obj in objects)
    workspace.close()
    app.processEvents()


def test_brightfield_layout_statistics_plot_text_uses_direct_editable_overlay() -> None:
    app = _app()
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-stats-ui")
    panel.data["active_image_uid"] = "image-a"
    panel.data["brightfield_images"] = [{"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480}, "measurements": []}]
    panel.data["brightfield_layout"] = {
        "outputs": [
            {
                "export_item_key": "statistics_chart",
                "source_record_id": "Positive Area %",
                "enabled": True,
                "x": 100.0,
                "y": 80.0,
                "width": 320.0,
                "height": 220.0,
            }
        ]
    }
    panel.analysis["brightfield_chart_export"] = {
        "metric": "Positive Area %",
        "svg_bytes": b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><text x="12" y="18">Positive Area %</text></svg>',
    }
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><text x="12" y="18">Positive Area %</text></svg>'
    workspace.prepare_layout(
        measurement={"measurement_uuid": "m1", "image_uid": "image-a"},
        pixmap_provider=lambda layout_object: svg if layout_object.asset_key == "statistics_chart" else QPixmap(),
    )

    text_item = next(item for item in workspace.layout_canvas.scene.items() if isinstance(item, SvgEditableTextItem))
    text_item.setPos(text_item.pos().x() + 20.0, text_item.pos().y() + 10.0)
    workspace.persist_layout_edits()

    stats_output = next(item for item in panel.data["brightfield_layout"]["outputs"] if item["export_item_key"] == "statistics_chart")
    assert stats_output["svg_text_overrides"]["0"]["x"] == 32.0
    assert stats_output["svg_text_overrides"]["0"]["y"] == 28.0
    workspace.close()
    app.processEvents()


def test_ihc_layout_statistics_plot_text_uses_direct_editable_overlay() -> None:
    app = _app()
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-stats-ui")
    panel.data["active_image_uid"] = "image-a"
    panel.data["ihc_images"] = [{"uid": "image-a", "metadata": {"width_px": 640, "height_px": 480}, "measurements": []}]
    panel.data["ihc_layout"] = {
        "outputs": [
            {
                "export_item_key": "statistics_chart",
                "source_record_id": "Positive Area %",
                "enabled": True,
                "x": 100.0,
                "y": 80.0,
                "width": 320.0,
                "height": 220.0,
            }
        ]
    }
    panel.analysis["ihc_statistics"] = {"metric": "Positive Area %"}
    workspace = IHCWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="220"><text x="14" y="20">Positive Area %</text></svg>'
    workspace.prepare_layout(
        measurement={"measurement_uuid": "m1", "image_uid": "image-a"},
        pixmap_provider=lambda layout_object: svg if layout_object.asset_key == "statistics_chart" else QPixmap(),
    )

    text_item = next(item for item in workspace.layout_canvas.scene.items() if isinstance(item, SvgEditableTextItem))
    text_item.setPos(text_item.pos().x() + 16.0, text_item.pos().y() + 8.0)
    workspace.persist_layout_edits()

    stats_output = next(item for item in panel.data["ihc_layout"]["outputs"] if item["export_item_key"] == "statistics_chart")
    assert stats_output["svg_text_overrides"]["0"]["x"] == 30.0
    assert stats_output["svg_text_overrides"]["0"]["y"] == 28.0
    workspace.close()
    app.processEvents()


def test_histology_layout_text_formatting_panel_updates_selected_text() -> None:
    app = _app()
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-text-format")
    panel.data["active_image_uid"] = "image-a"
    panel.data["ihc_images"] = [{"uid": "image-a", "metadata": {"width_px": 320, "height_px": 240}, "measurements": []}]
    workspace = IHCWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.prepare_layout(measurement={"measurement_uuid": "m1", "image_uid": "image-a"}, pixmap_provider=lambda _obj: QPixmap())
    workspace.add_layout_text()
    text_item = next(item for item in workspace.layout_canvas.scene.items() if isinstance(item, LayoutEditorTextItem) and item.toPlainText() == "Text")

    text_item.setSelected(True)
    workspace.sync_text_controls_from_selection()
    workspace.text_x_spin.setValue(40.0)
    workspace.text_y_spin.setValue(50.0)
    workspace.text_rotation_spin.setValue(-90.0)
    workspace.apply_text_style_to_selected()

    layout_text = next(obj for obj in workspace.layout_canvas.layout_objects() if obj.object_type == "text")
    assert layout_text.x == 40.0
    assert layout_text.y == 50.0
    assert layout_text.rotation_deg == -90.0
    workspace.close()
    app.processEvents()


def test_brightfield_layout_text_formatting_panel_updates_selected_text() -> None:
    app = _app()
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-text-format")
    panel.data["active_image_uid"] = "image-a"
    panel.data["brightfield_images"] = [{"uid": "image-a", "metadata": {"width_px": 320, "height_px": 240}, "measurements": []}]
    workspace = BrightfieldWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.prepare_layout(measurement={"measurement_uuid": "m1", "image_uid": "image-a"}, pixmap_provider=lambda _obj: QPixmap())
    workspace.add_layout_text()
    text_item = next(item for item in workspace.layout_canvas.scene.items() if isinstance(item, LayoutEditorTextItem) and item.toPlainText() == "Text")

    text_item.setSelected(True)
    workspace.sync_text_controls_from_selection()
    workspace.text_x_spin.setValue(42.0)
    workspace.text_y_spin.setValue(52.0)
    workspace.text_rotation_spin.setValue(-45.0)
    workspace.apply_text_style_to_selected()

    layout_text = next(obj for obj in workspace.layout_canvas.layout_objects() if obj.object_type == "text")
    assert layout_text.x == 42.0
    assert layout_text.y == 52.0
    assert layout_text.rotation_deg == -45.0
    workspace.close()
    app.processEvents()
