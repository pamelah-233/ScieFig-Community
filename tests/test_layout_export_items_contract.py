from __future__ import annotations

import pytest

from core.layout_export_items import (
    EXPORT_ITEM_EXTERNAL_IMAGE,
    EXPORT_ITEM_FULL_LAYOUT,
    EXPORT_ITEM_INDIVIDUAL_PLOT,
    EXPORT_ITEM_OVERLAY,
    EXPORT_ITEM_STATISTICS_CHART,
    LayoutExportItem,
    export_item_from_layout_object,
    layout_export_item_hash,
)
from core.models.layout_editor import LayoutObject, LayoutSourceIdentity


def test_export_item_round_trips_with_required_module_metadata() -> None:
    item = LayoutExportItem(
        export_item_id="wb-layout-item",
        export_item_type=EXPORT_ITEM_FULL_LAYOUT,
        source_module="wb_export_item",
        source_panel_uuid="panel-wb",
        export_item_key="wb_layout",
        source_dpi=1200,
        preferred_width_mm=76.2,
        preferred_height_mm=19.05,
        source_record_id="wb-layout",
        source_record_namespace="visual_layout_asset",
        source_context_hash="sha256:" + "b" * 64,
        created_from_layout_object_id="layout-object-1",
        image_path="assets/wb_layout.svg",
        source_format="SVG",
        source_width_px=3600,
        source_height_px=900,
        metadata={"contains_wb_hierarchy": True},
    )

    restored = LayoutExportItem.from_dict(item.to_dict())

    assert restored == item
    assert restored.stable_hash() == layout_export_item_hash(item)
    metadata = restored.to_external_image_metadata()
    assert metadata["source_module"] == "wb_export_item"
    assert metadata["source_panel_uuid"] == "panel-wb"
    assert metadata["export_item_key"] == "wb_layout"
    assert metadata["source_dpi"] == 1200
    assert metadata["preferred_width_mm"] == pytest.approx(76.2)
    assert metadata["preferred_height_mm"] == pytest.approx(19.05)
    assert metadata["export_item_type"] == EXPORT_ITEM_FULL_LAYOUT
    assert metadata["source_record_id"] == "wb-layout"
    assert metadata["source_record_namespace"] == "visual_layout_asset"
    assert metadata["source_context_hash"].startswith("sha256:")
    assert metadata["created_from_layout_object_id"] == "layout-object-1"
    assert metadata["contains_wb_hierarchy"] is True


def test_export_item_types_cover_current_module_outputs() -> None:
    examples = [
        (EXPORT_ITEM_FULL_LAYOUT, "fluorescence_layout", "full_layout"),
        (EXPORT_ITEM_INDIVIDUAL_PLOT, "qpcr_plot", "SPNS1"),
        (EXPORT_ITEM_OVERLAY, "ihc_overlay", "measurement_overlay"),
        (EXPORT_ITEM_OVERLAY, "brightfield_overlay", "measurement_overlay"),
        (EXPORT_ITEM_STATISTICS_CHART, "ihc_statistics", "statistics_chart"),
        (EXPORT_ITEM_EXTERNAL_IMAGE, "external", "imported_image"),
    ]

    for index, (item_type, source_module, key) in enumerate(examples):
        item = LayoutExportItem(
            export_item_id=f"item-{index}",
            export_item_type=item_type,
            source_module=source_module,
            source_panel_uuid=f"panel-{index}",
            export_item_key=key,
            source_dpi=600,
            preferred_width_mm=40.0,
            preferred_height_mm=30.0,
        )
        assert item.to_dict()["export_item_type"] == item_type


def test_export_item_rejects_missing_or_invalid_required_metadata() -> None:
    with pytest.raises(ValueError, match="source_module is required"):
        LayoutExportItem(
            export_item_id="bad",
            export_item_type=EXPORT_ITEM_FULL_LAYOUT,
            source_module="",
            source_panel_uuid="panel",
            export_item_key="layout",
            source_dpi=600,
            preferred_width_mm=10.0,
            preferred_height_mm=10.0,
        )

    with pytest.raises(ValueError, match="unsupported export_item_type"):
        LayoutExportItem(
            export_item_id="bad",
            export_item_type="module_specific_private_type",
            source_module="wb",
            source_panel_uuid="panel",
            export_item_key="layout",
            source_dpi=600,
            preferred_width_mm=10.0,
            preferred_height_mm=10.0,
        )


def test_export_item_metadata_is_immutable_and_cannot_override_reserved_fields() -> None:
    item = LayoutExportItem(
        export_item_id="immutable-export",
        export_item_type=EXPORT_ITEM_FULL_LAYOUT,
        source_module="fluorescence_layout",
        source_panel_uuid="panel-fluoro",
        export_item_key="full_layout",
        source_dpi=600,
        preferred_width_mm=50.0,
        preferred_height_mm=40.0,
        metadata={"custom": {"a": 1}},
    )

    with pytest.raises(TypeError):
        item.metadata["custom"]["a"] = 2  # type: ignore[index]

    collision = LayoutExportItem(
        export_item_id="collision-export",
        export_item_type=EXPORT_ITEM_FULL_LAYOUT,
        source_module="fluorescence_layout",
        source_panel_uuid="panel-fluoro",
        export_item_key="full_layout",
        source_dpi=600,
        preferred_width_mm=50.0,
        preferred_height_mm=40.0,
        metadata={"source_record_namespace": "ai_assisted"},
    )
    with pytest.raises(ValueError, match="cannot override reserved field"):
        collision.to_external_image_metadata()

    optional_collision = LayoutExportItem(
        export_item_id="collision-image-path",
        export_item_type=EXPORT_ITEM_FULL_LAYOUT,
        source_module="fluorescence_layout",
        source_panel_uuid="panel-fluoro",
        export_item_key="full_layout",
        source_dpi=600,
        preferred_width_mm=50.0,
        preferred_height_mm=40.0,
        metadata={"image_path": "fake.png", "raw_source": "fake.png"},
    )
    with pytest.raises(ValueError, match="cannot override reserved field"):
        optional_collision.to_external_image_metadata()


def test_export_item_from_layout_object_preserves_source_namespace_without_mixing() -> None:
    layout_object = LayoutObject(
        object_id="fluoro-plot-object",
        object_type="plot",
        x=0,
        y=0,
        width=512,
        height=512,
        source=LayoutSourceIdentity(
            source_module="fluorescence",
            source_panel_uuid="panel-fluoro",
            source_record_id="layout-plot-1",
            source_record_namespace="visual_layout_asset",
            source_context_hash="sha256:" + "c" * 64,
        ),
    )

    item = export_item_from_layout_object(
        export_item_id="fluoro-plot-item",
        export_item_type=EXPORT_ITEM_INDIVIDUAL_PLOT,
        source_module="fluorescence_layout_plot",
        source_panel_uuid="panel-fluoro",
        export_item_key="merge_split_plot",
        source_dpi=600,
        preferred_width_mm=50.0,
        preferred_height_mm=50.0,
        layout_object=layout_object,
    )

    assert item.source_record_id == "layout-plot-1"
    assert item.source_record_namespace == "visual_layout_asset"
    assert item.source_context_hash.startswith("sha256:")
    assert item.created_from_layout_object_id == "fluoro-plot-object"


def test_export_item_from_layout_object_rejects_panel_uuid_mismatch() -> None:
    layout_object = LayoutObject(
        object_id="plot-object",
        object_type="plot",
        x=0,
        y=0,
        width=100,
        height=100,
        source=LayoutSourceIdentity(source_module="qpcr", source_panel_uuid="panel-A"),
    )

    with pytest.raises(ValueError, match="source_panel_uuid does not match"):
        export_item_from_layout_object(
            export_item_id="bad-panel",
            export_item_type=EXPORT_ITEM_INDIVIDUAL_PLOT,
            source_module="qpcr_plot",
            source_panel_uuid="panel-B",
            export_item_key="plot",
            source_dpi=600,
            preferred_width_mm=40.0,
            preferred_height_mm=30.0,
            layout_object=layout_object,
        )
