from __future__ import annotations

from copy import deepcopy

import pytest

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_STATISTICS_CHART
from core.models import ExternalImagePanel, WesternBlotPanel
from core.models.layout_editor import LayoutObject
from ui.layout_adapters.wb_layout_adapter import (
    WB_EXPORT_SOURCE_MODULE,
    WB_LAYOUT_EXPORT_KEY,
    WB_STATS_PLOT_EXPORT_KEY,
    apply_wb_export_item_metadata,
    apply_wb_layout_objects,
    wb_export_item_for_scene_bounds,
    wb_layout_from_layout_objects,
    wb_layout_objects,
)


def _layout_payload() -> dict:
    return {
        "enabled": True,
        "bands": [
            {
                "band_index": 0,
                "crop_id": "crop_1",
                "strip_id": "strip_1",
                "image_path": "target.png",
                "protein": "SPNS1",
                "mw_kda": "70",
                "is_loading_ctrl": False,
                "roi": {"x": 10, "y": 20, "w": 180, "h": 42},
            },
            {
                "band_index": 1,
                "crop_id": "crop_2",
                "strip_id": "strip_2",
                "image_path": "loading.png",
                "protein": "GAPDH",
                "mw_kda": "37",
                "is_loading_ctrl": True,
                "roi": {"x": 10, "y": 72, "w": 180, "h": 36},
            },
        ],
        "lane_labels": ["Ctrl", "Drug"],
        "lane_label_position": "top",
        "protein_label_side": "right",
        "marker_label_side": "left",
        "loading_control_index": 1,
        "lane_hierarchy": {
            "total_lanes": 2,
            "levels": [
                {"level": 1, "groups": [{"label": "Treatment", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
                {"level": 2, "type": "condition_row", "row_label": "Dose", "values": ["0", "10"]},
            ],
        },
        "group_gaps": [{"after_lane": 1, "gap_width_pt": 12.0}],
        "style": {
            "font_family": "Arial",
            "tick_font_pt": 6,
            "protein_italic": True,
            "marker_line_pt": 0.5,
            "strip_gap_px": 25,
            "label_offset_px": 15,
            "padding_pt": 10,
        },
        "manual_positions": {
            "lane": {"1": {"x": 24.0, "y": 8.0}},
            "protein": {"0": {"x": 210.0, "y": 44.0}},
        },
        "text_styles": {
            "lane": {
                "default": {"font_family": "Times New Roman", "font_size_pt": 8, "color": "#C0392B"},
                "items": {"1": {"bold": True, "rotation": 15.0}},
            },
            "protein": {"default": {"italic": True, "color": "#111111"}},
            "stats_x_label": {"items": {"1": {"font_size_pt": 9, "color": "#1F4E79"}}},
        },
        "line_styles": {"group_l1:1000": {"color": "#1F4E79", "width": 2.5, "pos": {"x": 3.0, "y": 4.0}}},
        "stats_plot": {
            "enabled": True,
            "x": 320.0,
            "y": 110.0,
            "width_px": 520,
            "height_px": 360,
            "style": {"bar_fill_color": "#D97757", "axis_line_width": 2.2},
            "labels": {"stats_x_label": {"0": "Ctrl", "1": "Drug"}, "stats_y_label": {"0": "Relative expression"}},
            "label_offsets": {"stats_x_label": {"1": {"x": 2.0, "y": 3.0}}},
        },
    }


def _panel() -> WesternBlotPanel:
    panel = WesternBlotPanel(panel_id="Figure1A", panel_uuid="panel-wb-1")
    panel.data = {
        "image_data": {"width_px": 260, "height_px": 120, "original_source": "full_blot.png"},
        "layout": {"image_x": 12.5, "image_y": 7.0},
        "annotations": {"lane_labels": ["Ctrl", "Drug"], "protein_labels": ["SPNS1", "GAPDH"], "markers": ["70", "37"]},
        "wb_layout": _layout_payload(),
        "wb_statistics": {
            "plot_data": [{"group": "Ctrl", "value": 1.0}, {"group": "Drug", "value": 2.0}],
            "comparisons": [],
            "summary": [],
        },
    }
    return panel


def test_wb_layout_adapter_round_trips_existing_layout_without_loss() -> None:
    panel = _panel()
    original = deepcopy(panel.data["wb_layout"])

    objects = wb_layout_objects(panel)
    restored = wb_layout_from_layout_objects(objects)

    assert restored == original
    assert any(obj.object_type == "wb_layout_root" for obj in objects)
    assert any(obj.text == "Ctrl" and obj.metadata["wb_kind"] == "lane_label" for obj in objects)
    assert any(obj.text == "SPNS1" and obj.metadata["wb_kind"] == "protein_label" for obj in objects)
    assert any(obj.text == "70" and obj.metadata["wb_kind"] == "marker_label" for obj in objects)
    assert any(obj.object_type == "line" and obj.metadata["line_uid"] == "group_l1:1000" for obj in objects)
    stats_label = next(obj for obj in objects if obj.metadata["wb_kind"] == "stats_label" and obj.metadata["index"] == 1)
    assert stats_label.style.text_color == "#1F4E79"
    assert stats_label.x == 2.0
    assert stats_label.y == 3.0
    stats = next(obj for obj in objects if obj.asset_key == WB_STATS_PLOT_EXPORT_KEY)
    assert (stats.x, stats.y, stats.width, stats.height) == (320.0, 110.0, 520.0, 360.0)


def test_wb_layout_adapter_writes_label_and_position_edits_back_to_existing_keys() -> None:
    panel = _panel()
    objects = []
    for obj in wb_layout_objects(panel):
        if obj.metadata.get("wb_kind") == "lane_label" and obj.metadata.get("index") == 1:
            payload = obj.to_dict()
            payload.update({"text": "Drug edited", "x": 30.0, "y": 12.0})
            objects.append(LayoutObject.from_dict(payload))
        elif obj.metadata.get("wb_kind") == "stats_label" and obj.metadata.get("index") == 1:
            payload = obj.to_dict()
            payload.update({"text": "Drug group", "x": 4.0, "y": 5.0})
            objects.append(LayoutObject.from_dict(payload))
        else:
            objects.append(obj)

    restored = wb_layout_from_layout_objects(objects)

    assert restored["lane_labels"] == ["Ctrl", "Drug edited"]
    assert restored["manual_positions"]["lane"]["1"] == {"x": 30.0, "y": 12.0}
    assert restored["stats_plot"]["labels"]["stats_x_label"]["1"] == "Drug group"
    assert restored["stats_plot"]["label_offsets"]["stats_x_label"]["1"] == {"x": 4.0, "y": 5.0}
    assert "wb_layout_objects" not in restored


def test_apply_wb_layout_objects_syncs_annotations_and_preserves_unrelated_annotation_fields() -> None:
    panel = _panel()
    panel.data["annotations"]["lane_anchors"] = [10.0, 20.0]
    panel.data["annotations"]["marker_anchors"] = [5.0]
    panel.data["annotations"]["lane_calibration"] = {"mode": "manual"}
    objects = []
    for obj in wb_layout_objects(panel):
        if obj.metadata.get("wb_kind") == "lane_label" and obj.metadata.get("index") == 1:
            payload = obj.to_dict()
            payload["text"] = "Drug edited"
            objects.append(LayoutObject.from_dict(payload))
        elif obj.metadata.get("wb_kind") == "protein_label" and obj.metadata.get("index") == 0:
            payload = obj.to_dict()
            payload["text"] = "SPNS1 edited"
            objects.append(LayoutObject.from_dict(payload))
        elif obj.metadata.get("wb_kind") == "marker_label" and obj.metadata.get("index") == 0:
            payload = obj.to_dict()
            payload["text"] = "75"
            objects.append(LayoutObject.from_dict(payload))
        else:
            objects.append(obj)

    apply_wb_layout_objects(panel, objects)

    assert panel.data["annotations"]["lane_labels"] == ["Ctrl", "Drug edited"]
    assert panel.data["annotations"]["protein_labels"] == ["SPNS1 edited", "GAPDH"]
    assert panel.data["annotations"]["markers"] == ["75", "37"]
    assert panel.data["annotations"]["lane_anchors"] == [10.0, 20.0]
    assert panel.data["annotations"]["marker_anchors"] == [5.0]
    assert panel.data["annotations"]["lane_calibration"] == {"mode": "manual"}
    assert panel.audit_log[-1].action == "apply_wb_layout_objects"


def test_wb_layout_adapter_writes_text_style_edits_to_item_overrides_only_when_changed() -> None:
    panel = _panel()
    original = deepcopy(panel.data["wb_layout"])
    assert wb_layout_from_layout_objects(wb_layout_objects(panel)) == original

    objects = []
    for obj in wb_layout_objects(panel):
        if obj.metadata.get("wb_kind") == "lane_label" and obj.metadata.get("index") == 0:
            payload = obj.to_dict()
            payload["style"]["text_color"] = "#00AA00"
            payload["style"]["font_size_pt"] = 11.0
            payload["style"]["bold"] = True
            payload["rotation_deg"] = 30.0
            objects.append(LayoutObject.from_dict(payload))
        else:
            objects.append(obj)

    restored = wb_layout_from_layout_objects(objects)

    item_style = restored["text_styles"]["lane"]["items"]["0"]
    assert item_style["color"] == "#00AA00"
    assert item_style["font_size_pt"] == 11.0
    assert item_style["bold"] is True
    assert item_style["rotation"] == 30.0


def test_apply_wb_layout_objects_rejects_layout_object_from_another_panel() -> None:
    panel = _panel()
    objects = wb_layout_objects(panel)
    foreign_payload = objects[0].to_dict()
    foreign_payload["source"]["source_panel_uuid"] = "other-panel"
    foreign_root = LayoutObject.from_dict(foreign_payload)

    with pytest.raises(ValueError, match="source_panel_uuid"):
        apply_wb_layout_objects(panel, [foreign_root, *objects[1:]])


def test_wb_export_item_descriptors_match_existing_composer_metadata_shape() -> None:
    panel = _panel()

    layout_item = wb_export_item_for_scene_bounds(
        panel,
        WB_LAYOUT_EXPORT_KEY,
        216.0,
        72.0,
        image_path="layout.svg",
        source_format="SVG",
    )
    stats_item = wb_export_item_for_scene_bounds(
        panel,
        WB_STATS_PLOT_EXPORT_KEY,
        144.0,
        72.0,
        image_path="stats.png",
        source_format="PNG",
    )

    assert layout_item.export_item_type == EXPORT_ITEM_FULL_LAYOUT
    assert stats_item.export_item_type == EXPORT_ITEM_STATISTICS_CHART
    assert layout_item.source_module == WB_EXPORT_SOURCE_MODULE
    assert layout_item.source_width_px == 3600
    assert layout_item.source_height_px == 1200
    assert round(layout_item.preferred_width_mm, 3) == 76.2
    metadata = layout_item.to_external_image_metadata()
    assert metadata["source_panel_uuid"] == panel.panel_uuid
    assert metadata["export_item_key"] == "wb_layout"
    assert metadata["source_format"] == "SVG"
    assert metadata["contains_wb_hierarchy"] is True

    asset = ExternalImagePanel(panel_id="Figure1A_wb_layout")
    apply_wb_export_item_metadata(asset, layout_item)
    assert asset.data["source_module"] == WB_EXPORT_SOURCE_MODULE
    assert asset.data["source_dpi"] == 1200
    assert asset.data["preferred_height_mm"] == layout_item.preferred_height_mm
