from __future__ import annotations

from copy import deepcopy

import pytest

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_INDIVIDUAL_PLOT
from core.models import ExternalImagePanel, FluorescencePanel
from core.models.fluoro_panel import AI_ASSISTED_MEASUREMENTS_KEY, SMART_ASSISTED_MEASUREMENTS_KEY
from core.models.layout_editor import LayoutObject
from core.utils.hashing import sha256_json
from ui.layout_adapters.fluorescence_layout_adapter import (
    FLUORESCENCE_LAYOUT_EXPORT_KEY,
    FLUORESCENCE_LAYOUT_EXPORT_SOURCE_MODULE,
    FLUORESCENCE_PLOT_EXPORT_SOURCE_MODULE,
    apply_fluorescence_export_item_metadata,
    apply_fluorescence_layout_objects,
    fluorescence_export_item_for_scene_bounds,
    fluorescence_layout_from_layout_objects,
    fluorescence_layout_objects,
    fluorescence_layout_objects_with_scene_positions,
    fluorescence_plot_export_key,
)


def _image_payload(layout_mode: str = "custom") -> dict:
    return {
        "uid": "image-a",
        "source_name": "cells.tif",
        "metadata": {"width_px": 640, "height_px": 480, "n_channels": 2},
        "channels": [
            {"index": 0, "name": "DAPI", "visible": True},
            {"index": 1, "name": "GFP", "visible": True},
        ],
        "layout_mode": layout_mode,
        "layout_item_geometries": {
            "merge_split:merge": {"x": 310.0, "y": 22.0, "width": 260.0, "height": 190.0},
            "custom:merge": {"x": 18.0, "y": 28.0, "width": 300.0, "height": 210.0},
        },
        "layout_shapes": [
            {
                "shape_uuid": "shape-a",
                "shape_type": "rectangle",
                "x": 40.0,
                "y": 50.0,
                "width": 120.0,
                "height": 70.0,
                "stroke_width": 2.0,
                "stroke_color": "#FFFFFF",
                "fill_color": "#22D7E6",
            }
        ],
        "layout_texts": [
            {
                "text_uuid": "text-a",
                "text": "Merged",
                "x": 20.0,
                "y": 24.0,
                "font_family": "Arial",
                "font_size": 10.0,
                "bold": False,
                "italic": True,
                "color": "#FFFFFF",
            }
        ],
        "measurements": [{"measurement_uuid": "m-image", "metric": "mean", "value": 12.3}],
        "object_counts": [{"count_uuid": "c-image", "count": 4, "channel_index": 0}],
        SMART_ASSISTED_MEASUREMENTS_KEY: [{"measurement_uuid": "smart-image", "value": 1.0}],
        AI_ASSISTED_MEASUREMENTS_KEY: [{"measurement_uuid": "ai-image", "value": 2.0}],
    }


def _plot_payload() -> dict:
    return {
        "plot_uuid": "plot-a",
        "plot_type": "statistics",
        "enabled": True,
        "title": "Mean intensity",
        "rows": [{"group": "Control", "value": 1.0}],
        "statistics_result": {"metric": "mean", "plot_data": [{"group": "Control", "value": 1.0}]},
        "source": "measurements",
        "metric": "mean",
        "x": 480.0,
        "y": 65.0,
        "width_px": 320,
        "height_px": 220,
        "style": {"font_family": "Arial", "font_size_pt": 7.0, "bar_width_fraction": 0.55},
    }


def _panel(layout_mode: str = "custom") -> FluorescencePanel:
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-1")
    panel.data = {
        "active_image_uid": "image-a",
        "fluorescence_images": [_image_payload(layout_mode)],
        "raw_source": "cells.tif",
    }
    plot = _plot_payload()
    panel.analysis = {
        "measurements": [{"measurement_uuid": "m-panel", "metric": "mean", "value": 10.0}],
        "object_counts": [{"count_uuid": "c-panel", "count": 3, "channel_index": 1}],
        SMART_ASSISTED_MEASUREMENTS_KEY: [{"measurement_uuid": "smart-panel", "value": 3.0}],
        AI_ASSISTED_MEASUREMENTS_KEY: [{"measurement_uuid": "ai-panel", "value": 4.0}],
        "fluoro_layout_plots": [deepcopy(plot)],
        "fluoro_stats_plot": deepcopy(plot),
    }
    return panel


def _layout_payload(panel: FluorescencePanel) -> dict:
    image = panel.active_image()
    assert image is not None
    return {
        "layout_mode": image.get("layout_mode"),
        "layout_item_geometries": deepcopy(image.get("layout_item_geometries", {})),
        "layout_shapes": deepcopy(image.get("layout_shapes", [])),
        "layout_texts": deepcopy(image.get("layout_texts", [])),
        "fluoro_layout_plots": deepcopy(panel.analysis.get("fluoro_layout_plots", [])),
    }


def _protected_payload(panel: FluorescencePanel) -> dict:
    image = panel.active_image()
    assert image is not None
    return {
        "analysis_measurements": deepcopy(panel.analysis.get("measurements", [])),
        "analysis_object_counts": deepcopy(panel.analysis.get("object_counts", [])),
        "analysis_smart": deepcopy(panel.analysis.get(SMART_ASSISTED_MEASUREMENTS_KEY, [])),
        "analysis_ai": deepcopy(panel.analysis.get(AI_ASSISTED_MEASUREMENTS_KEY, [])),
        "image_measurements": deepcopy(image.get("measurements", [])),
        "image_object_counts": deepcopy(image.get("object_counts", [])),
        "image_smart": deepcopy(image.get(SMART_ASSISTED_MEASUREMENTS_KEY, [])),
        "image_ai": deepcopy(image.get(AI_ASSISTED_MEASUREMENTS_KEY, [])),
    }


def _record_counts_and_keys(payload: dict) -> dict:
    return {
        key: {
            "count": len(rows),
            "keys": [sorted(row) for row in rows],
        }
        for key, rows in payload.items()
    }


def test_fluorescence_layout_adapter_round_trips_existing_layout_namespaces_without_loss() -> None:
    panel = _panel()
    original = _layout_payload(panel)

    objects = fluorescence_layout_objects(panel)
    restored = fluorescence_layout_from_layout_objects(objects)

    assert restored["images_layout"]["image-a"]["layout_mode"] == original["layout_mode"]
    assert restored["images_layout"]["image-a"]["layout_item_geometries"] == original["layout_item_geometries"]
    assert restored["images_layout"]["image-a"]["layout_shapes"] == original["layout_shapes"]
    assert restored["images_layout"]["image-a"]["layout_texts"] == original["layout_texts"]
    assert restored["fluoro_layout_plots"] == original["fluoro_layout_plots"]
    assert any(obj.metadata["fluoro_kind"] == "layout_image" and obj.asset_key == "merge_split:merge" for obj in objects)
    assert any(obj.metadata["fluoro_kind"] == "layout_shape" and obj.metadata["shape_uuid"] == "shape-a" for obj in objects)
    assert any(obj.metadata["fluoro_kind"] == "layout_text" and obj.text == "Merged" for obj in objects)
    assert any(obj.metadata["fluoro_kind"] == "layout_plot" and obj.metadata["plot_uuid"] == "plot-a" for obj in objects)


@pytest.mark.parametrize("layout_mode", ["merge_split", "merge_only", "batch_grid", "custom"])
def test_fluorescence_layout_adapter_preserves_layout_modes(layout_mode: str) -> None:
    panel = _panel(layout_mode)

    apply_fluorescence_layout_objects(panel, fluorescence_layout_objects(panel))

    assert panel.active_image()["layout_mode"] == layout_mode


def test_fluorescence_layout_adapter_writes_geometry_and_style_edits_back_to_legacy_keys() -> None:
    panel = _panel()
    protected_before = _protected_payload(panel)
    before_hash = sha256_json(protected_before)
    before_counts_and_keys = _record_counts_and_keys(protected_before)
    edited = []
    for obj in fluorescence_layout_objects(panel):
        payload = obj.to_dict()
        kind = obj.metadata.get("fluoro_kind")
        if kind == "layout_image" and obj.metadata.get("layout_key") == "merge_split:merge":
            payload.update({"x": 333.0, "y": 44.0, "width": 280.0, "height": 200.0})
        elif kind == "layout_shape":
            payload.update({"x": 52.0, "y": 66.0, "width": 140.0, "height": 90.0})
            payload["style"]["stroke_width"] = 5.0
            payload["style"]["stroke_color"] = "#FF3333"
            payload["style"]["fill_color"] = "#22D7E6"
        elif kind == "layout_text":
            payload.update({"x": 34.0, "y": 48.0, "text": "Edited"})
            payload["style"]["font_size_pt"] = 14.0
            payload["style"]["bold"] = True
            payload["style"]["italic"] = False
            payload["style"]["text_color"] = "#22D7E6"
        elif kind == "layout_plot":
            payload.update({"x": 500.0, "y": 80.0, "width": 420.0, "height": 260.0})
        edited.append(LayoutObject.from_dict(payload))

    apply_fluorescence_layout_objects(panel, edited)

    image = panel.active_image()
    assert image["layout_item_geometries"]["merge_split:merge"] == {"x": 333.0, "y": 44.0, "width": 280.0, "height": 200.0}
    assert image["layout_shapes"][0]["x"] == 52.0
    assert image["layout_shapes"][0]["stroke_width"] == 5.0
    assert image["layout_shapes"][0]["stroke_color"] == "#FF3333"
    assert image["layout_shapes"][0]["fill_color"] == "#22D7E6"
    assert image["layout_texts"][0]["text"] == "Edited"
    assert image["layout_texts"][0]["font_size"] == 14.0
    assert image["layout_texts"][0]["bold"] is True
    assert image["layout_texts"][0]["italic"] is False
    assert image["layout_texts"][0]["color"] == "#22D7E6"
    assert panel.analysis["fluoro_layout_plots"][0]["x"] == 500.0
    assert panel.analysis["fluoro_layout_plots"][0]["width_px"] == 420
    assert panel.analysis["fluoro_stats_plot"]["height_px"] == 260
    protected_after = _protected_payload(panel)
    assert sha256_json(protected_after) == before_hash
    assert _record_counts_and_keys(protected_after) == before_counts_and_keys


def test_fluorescence_layout_adapter_maps_scale_bars_with_shared_relative_position() -> None:
    panel = _panel()
    image = panel.active_image()
    assert image is not None
    image["metadata"]["pixel_size_um"] = 1.0
    image["layout_scale_bar_template"] = {
        "label_length_um": 50.0,
        "anchor": "bottom_right",
        "offset_x_fraction": 0.1,
        "offset_y_fraction": 0.2,
        "line_thickness": 3.0,
    }
    image["layout_scale_bars"] = [
        {"target_id": "merge_split:merge", "enabled": True},
        {"target_id": "custom:merge", "enabled": True},
    ]
    protected_before = _protected_payload(panel)

    objects = fluorescence_layout_objects(panel)
    scale_bars = [obj for obj in objects if obj.metadata.get("fluoro_kind") == "layout_scale_bar"]

    assert {obj.metadata["target_id"] for obj in scale_bars} == {"merge_split:merge", "custom:merge"}
    assert {obj.metadata["template"]["anchor"] for obj in scale_bars} == {"bottom_right"}
    assert {obj.metadata["template"]["offset_x_fraction"] for obj in scale_bars} == {0.1}
    assert {obj.metadata["template"]["offset_y_fraction"] for obj in scale_bars} == {0.2}
    assert all(obj.metadata["calibration_source"] == "embedded" for obj in scale_bars)

    edited = []
    for obj in objects:
        payload = obj.to_dict()
        if obj.metadata.get("fluoro_kind") == "layout_scale_bar" and obj.metadata.get("target_id") == "custom:merge":
            payload["hidden"] = True
            payload["style"]["metadata"]["text_position"] = "below"
        edited.append(LayoutObject.from_dict(payload))
    apply_fluorescence_layout_objects(panel, edited)

    state_by_target = {item["target_id"]: item for item in panel.active_image()["layout_scale_bars"]}
    assert state_by_target["merge_split:merge"]["enabled"] is True
    assert state_by_target["custom:merge"]["enabled"] is False
    assert state_by_target["custom:merge"]["template"]["text_position"] == "below"
    assert state_by_target["merge_split:merge"]["template"].get("text_position", "above") == "above"
    protected_after = _protected_payload(panel)
    assert sha256_json(protected_after) == sha256_json(protected_before)
    assert _record_counts_and_keys(protected_after) == _record_counts_and_keys(protected_before)


def test_fluorescence_scene_snapshot_adds_new_layout_item_geometry_without_touching_analysis_namespaces() -> None:
    panel = _panel()
    image = panel.active_image()
    image["layout_item_geometries"] = {}
    protected_before = _protected_payload(panel)

    objects = fluorescence_layout_objects_with_scene_positions(
        panel,
        {"images": {"image-a": {"merge_split:merge": {"x": 12.0, "y": 20.0, "width": 250.0, "height": 175.0}}}},
    )
    apply_fluorescence_layout_objects(panel, objects)

    assert panel.active_image()["layout_item_geometries"]["merge_split:merge"] == {
        "x": 12.0,
        "y": 20.0,
        "width": 250.0,
        "height": 175.0,
    }
    protected_after = _protected_payload(panel)
    assert sha256_json(protected_after) == sha256_json(protected_before)
    assert _record_counts_and_keys(protected_after) == _record_counts_and_keys(protected_before)


def test_apply_fluorescence_layout_objects_rejects_layout_object_from_another_panel() -> None:
    panel = _panel()
    objects = fluorescence_layout_objects(panel)
    foreign_payload = objects[0].to_dict()
    foreign_payload["source"]["source_panel_uuid"] = "other-panel"
    foreign_root = LayoutObject.from_dict(foreign_payload)

    with pytest.raises(ValueError, match="source_panel_uuid"):
        apply_fluorescence_layout_objects(panel, [foreign_root, *objects[1:]])


def test_fluorescence_export_item_descriptors_preserve_composer_metadata_shape() -> None:
    panel = _panel()

    layout_item = fluorescence_export_item_for_scene_bounds(
        panel,
        FLUORESCENCE_LAYOUT_EXPORT_KEY,
        216.0,
        108.0,
        image_path="layout.png",
        source_format="PNG",
        source_width_px=1800,
        source_height_px=900,
    )
    plot_item = fluorescence_export_item_for_scene_bounds(
        panel,
        fluorescence_plot_export_key("plot-a"),
        144.0,
        72.0,
        image_path="plot.png",
        source_format="PNG",
        source_width_px=1200,
        source_height_px=600,
        plot_uuid="plot-a",
        plot_type="statistics",
    )

    assert layout_item.export_item_type == EXPORT_ITEM_FULL_LAYOUT
    assert plot_item.export_item_type == EXPORT_ITEM_INDIVIDUAL_PLOT
    assert layout_item.source_module == FLUORESCENCE_LAYOUT_EXPORT_SOURCE_MODULE
    assert plot_item.source_module == FLUORESCENCE_PLOT_EXPORT_SOURCE_MODULE
    layout_metadata = layout_item.to_external_image_metadata()
    plot_metadata = plot_item.to_external_image_metadata()
    assert layout_metadata["export_item_key"] == "fluorescence_layout"
    assert layout_metadata["layout_mode"] == "custom"
    assert layout_metadata["source_width_px"] == 1800
    assert plot_metadata["plot_uuid"] == "plot-a"
    assert plot_metadata["plot_type"] == "statistics"
    assert plot_metadata["source_record_namespace"] == "fluorescence_layout_plot"

    asset = ExternalImagePanel(panel_id="Figure1C_layout")
    apply_fluorescence_export_item_metadata(asset, layout_item)
    assert asset.data["source_module"] == FLUORESCENCE_LAYOUT_EXPORT_SOURCE_MODULE
    assert asset.data["export_item_type"] == EXPORT_ITEM_FULL_LAYOUT
