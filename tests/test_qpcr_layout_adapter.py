from __future__ import annotations

from copy import deepcopy

import pytest

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_INDIVIDUAL_PLOT
from core.models import ExternalImagePanel, QPCRPanel
from core.models.layout_editor import LayoutObject
from core.utils.hashing import sha256_json
from core.utils.svg_text import svg_text_entries
from ui.layout_adapters.qpcr_layout_adapter import (
    QPCR_LAYOUT_EXPORT_KEY,
    QPCR_LAYOUT_EXPORT_SOURCE_MODULE,
    QPCR_PLOT_EXPORT_SOURCE_MODULE,
    apply_qpcr_export_item_metadata,
    apply_qpcr_layout_objects,
    qpcr_export_item_for_scene_bounds,
    qpcr_layout_from_layout_objects,
    qpcr_layout_objects,
    qpcr_plot_export_key,
)


def _panel() -> QPCRPanel:
    panel = QPCRPanel(panel_id="Figure1Q", panel_uuid="panel-qpcr-1")
    panel.data = {
        "processed_data": {
            "fold_change": [
                {"target": "SPNS1", "group": "siCtrl", "fold_change": 1.0},
                {"target": "SPNS1", "group": "siSPNS1", "fold_change": 0.2},
            ]
        },
        "raw_rows": [{"sample": "A", "target": "SPNS1", "ct": 24.1}],
        "qpcr_layout": {
            "plots": [
                {
                    "plot_uuid": "plot-spns1",
                    "plot_type": "qpcr_target",
                    "target": "SPNS1",
                    "enabled": True,
                    "x": 24.0,
                    "y": 32.0,
                    "width_px": 520,
                    "height_px": 360,
                    "style": {"bar_edge_color": "#1F4E79"},
                }
            ],
            "texts": [
                {
                    "text_uuid": "text-a",
                    "text": "qPCR note",
                    "x": 12.0,
                    "y": 18.0,
                    "font_family": "Arial",
                    "font_size": 10.0,
                    "bold": False,
                    "italic": True,
                    "color": "#111111",
                    "rotation": 5.0,
                }
            ],
            "shapes": [
                {
                    "shape_uuid": "shape-a",
                    "shape_type": "ellipse",
                    "x": 70.0,
                    "y": 90.0,
                    "width": 120.0,
                    "height": 70.0,
                    "stroke_width": 2.0,
                    "stroke_color": "#C0392B",
                    "fill_color": "#22D7E6",
                }
            ],
        },
    }
    panel.analysis = {
        "reference_gene": "GAPDH",
        "control_group": "siCtrl",
        "statistics": {"SPNS1": {"p_value": 0.01}},
    }
    return panel


def test_qpcr_layout_adapter_round_trips_existing_layout_without_loss() -> None:
    panel = _panel()
    original = deepcopy(panel.data["qpcr_layout"])

    objects = qpcr_layout_objects(panel)
    restored = qpcr_layout_from_layout_objects(objects)

    assert restored == original
    assert any(obj.metadata["qpcr_kind"] == "layout_plot" and obj.metadata["target"] == "SPNS1" for obj in objects)
    assert any(obj.metadata["qpcr_kind"] == "layout_text" and obj.text == "qPCR note" for obj in objects)
    assert any(obj.metadata["qpcr_kind"] == "layout_shape" and obj.metadata["shape_type"] == "ellipse" for obj in objects)


def test_qpcr_layout_plot_svg_applies_persisted_text_overrides() -> None:
    panel = _panel()
    for row in panel.data["processed_data"]["fold_change"]:
        row["mean_fold_change"] = float(row["fold_change"])
        row["error"] = 0.1
    record = panel.layout_plot_records()[0]
    svg = panel.render_layout_plot(record, format="svg")
    entries = svg_text_entries(svg)
    title = next(entry for entry in entries if entry.text == "SPNS1")

    record["svg_text_overrides"] = {str(title.index): "Edited target"}
    updated = panel.render_layout_plot(record, format="svg")

    assert any(entry.text == "Edited target" for entry in svg_text_entries(updated))


def test_qpcr_layout_adapter_writes_geometry_style_and_text_edits_without_touching_analysis() -> None:
    panel = _panel()
    analysis_hash = sha256_json(panel.analysis)
    raw_rows_hash = sha256_json(panel.data["raw_rows"])
    edited = []
    for obj in qpcr_layout_objects(panel):
        payload = obj.to_dict()
        kind = obj.metadata.get("qpcr_kind")
        if kind == "layout_plot":
            payload.update({"x": 40.0, "y": 50.0, "width": 420.0, "height": 260.0})
        elif kind == "layout_text":
            payload.update({"text": "edited note", "rotation_deg": 15.0})
            payload["style"]["font_size_pt"] = 12.0
            payload["style"]["bold"] = True
        elif kind == "layout_shape":
            payload["style"]["stroke_width"] = 4.0
            payload["style"]["stroke_color"] = "#1F4E79"
        edited.append(LayoutObject.from_dict(payload))

    apply_qpcr_layout_objects(panel, edited)

    plot = panel.data["qpcr_layout"]["plots"][0]
    assert (plot["x"], plot["y"], plot["width_px"], plot["height_px"]) == (40.0, 50.0, 420, 260)
    text = panel.data["qpcr_layout"]["texts"][0]
    assert text["text"] == "edited note"
    assert text["font_size"] == 12.0
    assert text["bold"] is True
    assert text["rotation"] == 15.0
    assert panel.data["qpcr_layout"]["shapes"][0]["stroke_color"] == "#1F4E79"
    assert sha256_json(panel.analysis) == analysis_hash
    assert sha256_json(panel.data["raw_rows"]) == raw_rows_hash


def test_apply_qpcr_layout_objects_rejects_foreign_or_unsupported_source_namespace() -> None:
    panel = _panel()
    objects = qpcr_layout_objects(panel)
    foreign_payload = objects[0].to_dict()
    foreign_payload["source"]["source_panel_uuid"] = "other-panel"

    with pytest.raises(ValueError, match="source_panel_uuid"):
        apply_qpcr_layout_objects(panel, [LayoutObject.from_dict(foreign_payload), *objects[1:]])

    bad_payload = objects[1].to_dict()
    bad_payload["source"]["source_record_namespace"] = "qpcr_statistics"
    with pytest.raises(ValueError, match="Unsupported qPCR layout namespace"):
        apply_qpcr_layout_objects(panel, [objects[0], LayoutObject.from_dict(bad_payload), *objects[2:]])


def test_qpcr_export_items_preserve_existing_composer_metadata_shape() -> None:
    panel = _panel()

    layout_item = qpcr_export_item_for_scene_bounds(
        panel,
        QPCR_LAYOUT_EXPORT_KEY,
        216.0,
        108.0,
        image_path="layout.png",
        source_format="PNG",
        source_width_px=1800,
        source_height_px=900,
    )
    plot_item = qpcr_export_item_for_scene_bounds(
        panel,
        qpcr_plot_export_key("plot-spns1"),
        144.0,
        72.0,
        image_path="plot.svg",
        source_format="SVG",
        source_width_px=520,
        source_height_px=360,
        plot_uuid="plot-spns1",
        qpcr_target="SPNS1",
    )

    assert layout_item.export_item_type == EXPORT_ITEM_FULL_LAYOUT
    assert plot_item.export_item_type == EXPORT_ITEM_INDIVIDUAL_PLOT
    assert layout_item.source_module == QPCR_LAYOUT_EXPORT_SOURCE_MODULE
    assert plot_item.source_module == QPCR_PLOT_EXPORT_SOURCE_MODULE
    assert plot_item.to_external_image_metadata()["qpcr_target"] == "SPNS1"

    asset = ExternalImagePanel(panel_id="Figure1Q_layout")
    apply_qpcr_export_item_metadata(asset, layout_item)
    assert asset.data["source_module"] == "qpcr_layout"
    assert asset.data["export_item_key"] == "qpcr_layout"
