from __future__ import annotations

from copy import deepcopy

import pytest

from core.layout_export_items import EXPORT_ITEM_OVERLAY, EXPORT_ITEM_STATISTICS_CHART
from core.models import BrightfieldPanel, ExternalImagePanel
from core.models.layout_editor import LayoutObject
from core.utils.hashing import sha256_json
from ui.layout_adapters.brightfield_layout_adapter import (
    BRIGHTFIELD_LAYOUT_EXPORT_KEY,
    BRIGHTFIELD_OVERLAY_EXPORT_KEY,
    BRIGHTFIELD_STATISTICS_EXPORT_KEY,
    apply_brightfield_export_item_metadata,
    apply_brightfield_layout_objects,
    brightfield_export_item_for_scene_bounds,
    brightfield_layout_from_layout_objects,
    brightfield_layout_objects,
)


def _measurement() -> dict:
    return {
        "measurement_uuid": "brightfield-measurement-a",
        "image_uid": "image-a",
        "stain_type": "H&E",
        "reliability_status": "WARN",
        "reliability": {"status": "WARN", "include_in_statistics": True, "messages": ["Confirm segmentation."]},
        "warnings": ["Confirm segmentation."],
        "qc_messages": ["Mask preview uses active ROI."],
        "analysis_metadata": {"threshold_source": "manual", "segmentation_mode": "hsv"},
        "params": {"segmentation_mode": "hsv"},
        "results": {"segmentation_mode": "hsv", "positive_area_pct": 18.0},
    }


def _measurement_with_uuid(measurement_uuid: str) -> dict:
    measurement = _measurement()
    measurement["measurement_uuid"] = measurement_uuid
    return measurement


def _panel() -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-brightfield-1")
    panel.data = {
        "active_image_uid": "image-a",
        "brightfield_images": [{"uid": "image-a", "source_name": "brightfield.png", "stain_type": "H&E", "metadata": {"width_px": 640, "height_px": 480}, "measurements": [deepcopy(_measurement())]}],
        "brightfield_layout": {
            "texts": [{"text_uuid": "text-a", "text": "Brightfield", "x": 10.0, "y": 12.0, "font_size": 10.0, "color": "#111111"}],
            "shapes": [{"shape_uuid": "shape-a", "shape_type": "rectangle", "x": 20.0, "y": 24.0, "width": 80.0, "height": 40.0, "stroke_color": "#C0392B", "fill_color": "#FFFFFF", "stroke_width": 2.0}],
        },
    }
    panel.analysis = {
        "brightfield_measurements": [deepcopy(_measurement())],
        "brightfield_statistics": {"metric": "Positive Area %", "groups": [{"group": "A", "n": 1, "mean": 18.0, "sem": 0.0}]},
    }
    return panel


def test_brightfield_layout_adapter_maps_overlay_statistics_text_and_shape() -> None:
    panel = _panel()

    objects = brightfield_layout_objects(panel, measurement=_measurement())
    restored = brightfield_layout_from_layout_objects(objects)

    assert {item["export_item_key"] for item in restored["outputs"]} == {"measurement_overlay", "statistics_chart"}
    assert restored["outputs"][0]["source_record_id"] == "brightfield-measurement-a"
    assert restored["texts"][0]["text"] == "Brightfield"
    assert restored["shapes"][0]["shape_type"] == "rectangle"
    assert any(obj.object_type == "image" and obj.asset_key == "measurement_overlay" for obj in objects)
    assert any(obj.object_type == "plot" and obj.asset_key == "statistics_chart" for obj in objects)


def test_brightfield_layout_adapter_writes_layout_edits_without_touching_analysis_results() -> None:
    panel = _panel()
    analysis_hash = sha256_json(panel.analysis)
    image_measurements_hash = sha256_json(panel.active_image()["measurements"])
    edited = []
    for obj in brightfield_layout_objects(panel, measurement=_measurement()):
        payload = obj.to_dict()
        if obj.metadata.get("brightfield_kind") == "layout_output" and obj.metadata.get("export_item_key") == "measurement_overlay":
            payload.update({"x": 40.0, "y": 50.0, "width": 420.0, "height": 300.0})
        elif obj.metadata.get("brightfield_kind") == "layout_text":
            payload.update({"text": "edited brightfield"})
            payload["style"]["font_size_pt"] = 12.0
        elif obj.metadata.get("brightfield_kind") == "layout_shape":
            payload["style"]["stroke_color"] = "#1F4E79"
        edited.append(LayoutObject.from_dict(payload))

    apply_brightfield_layout_objects(panel, edited)

    assert panel.data["brightfield_layout"]["outputs"][0]["x"] == 40.0
    assert panel.data["brightfield_layout"]["texts"][0]["text"] == "edited brightfield"
    assert panel.data["brightfield_layout"]["shapes"][0]["stroke_color"] == "#1F4E79"
    assert sha256_json(panel.analysis) == analysis_hash
    assert sha256_json(panel.active_image()["measurements"]) == image_measurements_hash


def test_brightfield_layout_adapter_persists_svg_statistics_text_overrides() -> None:
    panel = _panel()
    edited = []
    for obj in brightfield_layout_objects(panel, measurement=_measurement()):
        payload = obj.to_dict()
        if obj.asset_key == BRIGHTFIELD_STATISTICS_EXPORT_KEY:
            payload["metadata"]["svg_text_overrides"] = {"2": "Edited fiber axis"}
        edited.append(LayoutObject.from_dict(payload))

    apply_brightfield_layout_objects(panel, edited)

    statistics_output = next(item for item in panel.data["brightfield_layout"]["outputs"] if item["export_item_key"] == BRIGHTFIELD_STATISTICS_EXPORT_KEY)
    assert statistics_output["svg_text_overrides"] == {"2": "Edited fiber axis"}


def test_brightfield_layout_adapter_persists_svg_statistics_text_position_overrides() -> None:
    panel = _panel()
    edited = []
    for obj in brightfield_layout_objects(panel, measurement=_measurement()):
        payload = obj.to_dict()
        if obj.asset_key == BRIGHTFIELD_STATISTICS_EXPORT_KEY:
            payload["metadata"]["svg_text_overrides"] = {"2": {"text": "Edited fiber axis", "x": 18.0, "y": 42.0}}
        edited.append(LayoutObject.from_dict(payload))

    apply_brightfield_layout_objects(panel, edited)

    statistics_output = next(item for item in panel.data["brightfield_layout"]["outputs"] if item["export_item_key"] == BRIGHTFIELD_STATISTICS_EXPORT_KEY)
    assert statistics_output["svg_text_overrides"] == {"2": {"text": "Edited fiber axis", "x": 18.0, "y": 42.0}}


def test_brightfield_layout_adapter_assumed_scale_bar_preserves_results() -> None:
    panel = _panel()
    panel.data["brightfield_layout"]["scale_bar_template"] = {
        "label_length_um": 50.0,
        "fallback_bar_fraction": 0.25,
        "anchor": "bottom_left",
        "offset_x_fraction": 0.1,
        "offset_y_fraction": 0.1,
    }
    panel.data["brightfield_layout"]["scale_bars"] = [{"target_id": "image-a", "enabled": True}]
    analysis_hash = sha256_json(panel.analysis)
    image_measurements_hash = sha256_json(panel.active_image()["measurements"])

    objects = brightfield_layout_objects(panel, measurement=_measurement())
    scale_bar = next(obj for obj in objects if obj.metadata.get("brightfield_kind") == "layout_scale_bar")

    assert scale_bar.object_type == "scale_bar"
    assert scale_bar.metadata["calibration_source"] == "assumed"
    assert scale_bar.metadata["label_text"] == "160 px"
    assert scale_bar.width == pytest.approx(105.0)
    assert scale_bar.text == "160 px"
    assert "um" not in scale_bar.metadata["label_text"]

    apply_brightfield_layout_objects(panel, objects)

    assert panel.data["brightfield_layout"]["scale_bars"][0]["calibration_source"] == "assumed"
    assert sha256_json(panel.analysis) == analysis_hash
    assert sha256_json(panel.active_image()["measurements"]) == image_measurements_hash


def test_brightfield_layout_and_composer_refreshes_active_measurement_source_without_moving_outputs() -> None:
    panel = _panel()
    panel.data["brightfield_layout"]["outputs"] = [
        {"export_item_key": BRIGHTFIELD_OVERLAY_EXPORT_KEY, "source_record_id": "old-measurement", "enabled": False, "x": 77.0, "y": 88.0, "width": 222.0, "height": 111.0},
        {"export_item_key": BRIGHTFIELD_STATISTICS_EXPORT_KEY, "source_record_id": "Old Metric", "enabled": True, "x": 300.0, "y": 44.0, "width": 250.0, "height": 160.0},
    ]
    panel.analysis["brightfield_statistics"]["metric"] = "New Positive Area %"

    objects = brightfield_layout_objects(panel, measurement=_measurement_with_uuid("new-measurement"))
    restored = brightfield_layout_from_layout_objects(objects)

    overlay_object = next(obj for obj in objects if obj.asset_key == BRIGHTFIELD_OVERLAY_EXPORT_KEY)
    statistics_object = next(obj for obj in objects if obj.asset_key == BRIGHTFIELD_STATISTICS_EXPORT_KEY)
    assert overlay_object.source is not None
    assert statistics_object.source is not None
    assert overlay_object.source.source_record_id == "new-measurement"
    assert statistics_object.source.source_record_id == "New Positive Area %"

    overlay = next(item for item in restored["outputs"] if item["export_item_key"] == BRIGHTFIELD_OVERLAY_EXPORT_KEY)
    statistics = next(item for item in restored["outputs"] if item["export_item_key"] == BRIGHTFIELD_STATISTICS_EXPORT_KEY)
    assert overlay["source_record_id"] == "new-measurement"
    assert overlay["enabled"] is False
    assert (overlay["x"], overlay["y"], overlay["width"], overlay["height"]) == (77.0, 88.0, 222.0, 111.0)
    assert statistics["source_record_id"] == "New Positive Area %"
    assert (statistics["x"], statistics["y"], statistics["width"], statistics["height"]) == (300.0, 44.0, 250.0, 160.0)


def test_brightfield_layout_and_composer_writeback_preserves_unknown_text_and_shape_fields() -> None:
    panel = _panel()
    panel.data["brightfield_layout"]["texts"][0]["custom_note"] = "keep text metadata"
    panel.data["brightfield_layout"]["shapes"][0]["custom_note"] = "keep shape metadata"
    edited = []
    for obj in brightfield_layout_objects(panel, measurement=_measurement()):
        payload = obj.to_dict()
        if obj.metadata.get("brightfield_kind") == "layout_text":
            payload["text"] = "edited label"
        elif obj.metadata.get("brightfield_kind") == "layout_shape":
            payload["style"]["fill_color"] = "#EEEEEE"
        edited.append(LayoutObject.from_dict(payload))

    apply_brightfield_layout_objects(panel, edited)

    assert panel.data["brightfield_layout"]["texts"][0]["text"] == "edited label"
    assert panel.data["brightfield_layout"]["texts"][0]["custom_note"] == "keep text metadata"
    assert panel.data["brightfield_layout"]["shapes"][0]["fill_color"] == "#EEEEEE"
    assert panel.data["brightfield_layout"]["shapes"][0]["custom_note"] == "keep shape metadata"


def test_brightfield_export_metadata_retains_reliability_stain_and_segmentation_without_recalculation() -> None:
    panel = _panel()
    measurement = _measurement()

    overlay_item = brightfield_export_item_for_scene_bounds(panel, BRIGHTFIELD_OVERLAY_EXPORT_KEY, 144.0, 72.0, image_path="overlay.png", source_format="PNG", measurement=measurement)
    stats_item = brightfield_export_item_for_scene_bounds(panel, BRIGHTFIELD_STATISTICS_EXPORT_KEY, 144.0, 72.0, image_path="stats.png", source_format="PNG", metric="Positive Area %")

    assert overlay_item.export_item_type == EXPORT_ITEM_OVERLAY
    assert stats_item.export_item_type == EXPORT_ITEM_STATISTICS_CHART
    overlay_metadata = overlay_item.to_external_image_metadata()
    assert overlay_metadata["measurement_uuid"] == "brightfield-measurement-a"
    assert overlay_metadata["reliability_status"] == "WARN"
    assert overlay_metadata["warnings"] == ["Confirm segmentation."]
    assert overlay_metadata["qc_messages"] == ["Mask preview uses active ROI."]
    assert overlay_metadata["stain_type"] == "H&E"
    assert overlay_metadata["segmentation_mode"] == "hsv"

    asset = ExternalImagePanel(panel_id="Figure1B_overlay")
    apply_brightfield_export_item_metadata(asset, overlay_item)
    assert asset.data["source_module"] == "brightfield_overlay"
    assert asset.data["plot_type"] == "measurement_overlay"


def test_apply_brightfield_layout_objects_rejects_foreign_or_unsupported_source_namespace() -> None:
    panel = _panel()
    objects = brightfield_layout_objects(panel, measurement=_measurement())
    foreign = objects[0].to_dict()
    foreign["source"]["source_panel_uuid"] = "other"
    with pytest.raises(ValueError, match="source_panel_uuid"):
        apply_brightfield_layout_objects(panel, [LayoutObject.from_dict(foreign), *objects[1:]])

    unsupported = objects[1].to_dict()
    unsupported["source"]["source_record_namespace"] = "brightfield_measurements"
    with pytest.raises(ValueError, match="Unsupported Brightfield layout namespace"):
        apply_brightfield_layout_objects(panel, [objects[0], LayoutObject.from_dict(unsupported), *objects[2:]])


def test_brightfield_layout_and_composer_button_route_supplies_active_measurement() -> None:
    from ui.main_window import MainWindow

    panel = _panel()
    measurement = _measurement_with_uuid("button-measurement")

    class WorkspaceDouble:
        def __init__(self) -> None:
            self.panel = None
            self.objects = []

        def set_panel(self, value) -> None:
            self.panel = value

        def prepare_layout(self, *, measurement=None, pixmap_provider=None) -> None:
            self.objects = brightfield_layout_objects(self.panel, measurement=measurement)

    workspace = WorkspaceDouble()
    window = type("WindowDouble", (), {})()
    window._sync_chart_kind_controls = lambda: None
    window.brightfield_workspace = workspace
    window._active_brightfield_panel = lambda: panel
    window._active_brightfield_measurement = lambda value: measurement
    window._brightfield_layout_pixmap_provider = lambda value, active_measurement: object()

    MainWindow.show_brightfield_layout(window)

    overlay = next(obj for obj in workspace.objects if obj.asset_key == BRIGHTFIELD_OVERLAY_EXPORT_KEY)
    assert overlay.source is not None
    assert overlay.source.source_record_id == "button-measurement"


def test_brightfield_layout_and_composer_tracking_record_includes_full_layout_asset() -> None:
    from ui.main_window import MainWindow

    panel = _panel()
    panel.data["composer_outputs"] = [{"panel_uuid": "existing-overlay", "source_module": "brightfield_overlay"}]
    layout_panel = ExternalImagePanel(panel_id="Figure1B_brightfield_layout")
    layout_panel.data.update(
        {
            "source_module": "brightfield_layout",
            "source_panel_uuid": panel.panel_uuid,
            "image_path": "generated/brightfield_layout.png",
            "export_item_key": BRIGHTFIELD_LAYOUT_EXPORT_KEY,
        }
    )
    window = type("WindowDouble", (), {})()

    composer_outputs = MainWindow._append_composer_output_tracking(window, panel, layout_panel)

    assert composer_outputs[0]["source_module"] == "brightfield_overlay"
    assert composer_outputs[-1]["panel_uuid"] == layout_panel.panel_uuid
    assert composer_outputs[-1]["source_module"] == "brightfield_layout"
    assert composer_outputs[-1]["image_path"] == "generated/brightfield_layout.png"
    assert composer_outputs[-1]["export_item_key"] == BRIGHTFIELD_LAYOUT_EXPORT_KEY
    assert composer_outputs[-1]["source_panel_uuid"] == panel.panel_uuid
