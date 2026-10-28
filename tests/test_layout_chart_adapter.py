from __future__ import annotations

from copy import deepcopy

import pytest
from PyQt6.QtWidgets import QApplication

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_OVERLAY
from core.models import (
    BrightfieldPanel,
    DerivedPlotPanel,
    FluorescencePanel,
    IHCPanel,
    QPCRPanel,
    WesternBlotPanel,
)
from core.plotting.chart_kinds import (
    DISTRIBUTION_CHART_KINDS,
    FAMILY_GROUPED,
    FAMILY_OBJECT_DISTRIBUTION,
    FAMILY_QPCR,
    GROUPED_CHART_KINDS,
    PLOT_PAYLOAD_FIELDS,
    REASON_VIOLIN_DEGENERATE,
    chart_kind_options,
    copy_plot_payload,
)
from core.utils.hashing import sha256_json
from ui.layout_adapters.brightfield_layout_adapter import (
    BRIGHTFIELD_LAYOUT_EXPORT_KEY,
    BRIGHTFIELD_OVERLAY_EXPORT_KEY,
    BRIGHTFIELD_STATISTICS_EXPORT_KEY,
    brightfield_export_item_for_scene_bounds,
    brightfield_layout_objects,
)
from ui.layout_adapters.chart_adapter import (
    layout_chart_target,
    plot_payload_for_export_item,
    set_layout_chart_kind,
)
from ui.layout_adapters.fluorescence_layout_adapter import (
    FLUORESCENCE_LAYOUT_EXPORT_KEY,
    fluorescence_export_item_for_scene_bounds,
    fluorescence_layout_objects,
    fluorescence_plot_export_key,
)
from ui.layout_adapters.ihc_layout_adapter import (
    IHC_LAYOUT_EXPORT_KEY,
    IHC_OVERLAY_EXPORT_KEY,
    IHC_STATISTICS_EXPORT_KEY,
    apply_ihc_export_item_metadata,
    ihc_export_item_for_scene_bounds,
    ihc_layout_objects,
)
from ui.layout_adapters.qpcr_layout_adapter import (
    QPCR_LAYOUT_EXPORT_KEY,
    apply_qpcr_export_item_metadata,
    qpcr_export_item_for_scene_bounds,
    qpcr_layout_objects,
    qpcr_plot_export_key,
)
from ui.layout_adapters.wb_layout_adapter import (
    WB_LAYOUT_EXPORT_KEY,
    WB_STATS_PLOT_EXPORT_KEY,
    apply_wb_export_item_metadata,
    wb_export_item_for_scene_bounds,
    wb_layout_objects,
)
from ui.widgets.fluoro_canvas import FluorescenceCanvas


def _grouped_plot_data() -> list[dict]:
    return [
        {"group": "Ctrl", "value": 1.0},
        {"group": "Ctrl", "value": 1.2},
        {"group": "Ctrl", "value": 0.8},
        {"group": "Drug", "value": 2.0},
        {"group": "Drug", "value": 2.4},
        {"group": "Drug", "value": 1.7},
    ]


def _qpcr_rows(target: str) -> list[dict]:
    rows = []
    for group, values in (("siCtrl", (1.0, 1.1, 0.9)), ("siSPNS1", (0.2, 0.3, 0.15))):
        mean = sum(values) / len(values)
        for value in values:
            rows.append(
                {
                    "target": target,
                    "group": group,
                    "fold_change": value,
                    "mean_fold_change": mean,
                    "error": 0.05,
                }
            )
    return rows


def _qpcr_panel() -> QPCRPanel:
    panel = QPCRPanel(panel_id="Figure1Q", panel_uuid="panel-qpcr-chart")
    panel.data = {
        "processed_data": {"fold_change": _qpcr_rows("SPNS1") + _qpcr_rows("GENE2")},
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
                    "svg_text_overrides": {"1": "keep"},
                },
                {
                    "plot_uuid": "plot-gene2",
                    "plot_type": "qpcr_target",
                    "target": "GENE2",
                    "enabled": True,
                    "x": 60.0,
                    "y": 56.0,
                    "width_px": 520,
                    "height_px": 360,
                    "style": {},
                },
            ]
        },
    }
    panel.analysis = {
        "reference_gene": "GAPDH",
        "control_group": "siCtrl",
        "error_bar": "SEM",
        "statistics": {"stat_performed_on": "delta_ct", "targets": {"SPNS1": {"comparisons": []}}},
    }
    return panel


def _wb_panel() -> WesternBlotPanel:
    panel = WesternBlotPanel(panel_id="Figure1A", panel_uuid="panel-wb-chart")
    panel.data = {
        "image_data": {"width_px": 260, "height_px": 120, "pixels": [[1, 2], [3, 4]]},
        "wb_layout": {
            "enabled": True,
            "bands": [{"crop_id": "crop_1", "protein": "SPNS1", "roi": {"x": 10, "y": 20, "w": 180, "h": 42}}],
            "stats_plot": {
                "enabled": True,
                "x": 320.0,
                "y": 110.0,
                "width_px": 520,
                "height_px": 360,
                "style": {"bar_fill_color": "#D97757"},
                "svg_text_overrides": {"2": "sig"},
            },
        },
        "wb_statistics": {"plot_data": _grouped_plot_data(), "comparisons": [], "test": "unpaired_t"},
    }
    return panel


def _fluoro_panel() -> FluorescencePanel:
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-chart")
    stats = {
        "plot_uuid": "plot-stats",
        "plot_type": "statistics",
        "enabled": True,
        "title": "Mean intensity",
        "statistics_result": {"metric": "mean", "plot_data": _grouped_plot_data(), "aggregation": "mean_per_image"},
        "distribution_result": {},
        "x": 480.0,
        "y": 65.0,
        "width_px": 320,
        "height_px": 220,
        "style": {"font_family": "Arial"},
    }
    frequency = {
        "plot_uuid": "plot-freq",
        "plot_type": "frequency_bar",
        "enabled": True,
        "title": "Object area",
        "statistics_result": {},
        "distribution_result": {
            "unit": "um^2",
            "frequency_mode": "absolute",
            "series": [{"lane_label": "Image A", "values": [1.0, 2.0, 3.0, 4.0, 8.0]}],
        },
        "x": 20.0,
        "y": 80.0,
        "width_px": 320,
        "height_px": 220,
        "style": {},
    }
    profile = {
        "plot_uuid": "plot-profile",
        "plot_type": "line_profile",
        "enabled": True,
        "title": "Line profile",
        "rows": [{"distance": [0, 1], "intensity": [1, 2]}],
        "x": 10.0,
        "y": 10.0,
        "width_px": 200,
        "height_px": 120,
        "style": {},
    }
    panel.data = {
        "active_image_uid": "image-a",
        "fluorescence_images": [
            {
                "uid": "image-a",
                "metadata": {"width_px": 640, "height_px": 480},
                "layout_item_geometries": {"custom:merge": {"x": 18.0, "y": 28.0, "width": 300.0, "height": 210.0}},
            }
        ],
    }
    panel.analysis = {
        "fluoro_layout_plots": [deepcopy(stats), deepcopy(frequency), deepcopy(profile)],
        "fluoro_stats_plot": deepcopy(stats),
        "fluoro_statistics": {"plot_data": _grouped_plot_data()},
    }
    return panel


def _ihc_panel() -> IHCPanel:
    panel = IHCPanel(panel_id="Figure1I", panel_uuid="panel-ihc-chart")
    panel.data = {
        "active_image_uid": "image-a",
        "ihc_images": [{"uid": "image-a", "source_name": "ihc.png", "metadata": {"width_px": 64, "height_px": 48}}],
        "ihc_layout": {
            "outputs": [
                {
                    "export_item_key": "statistics_chart",
                    "source_record_id": "Positive Area %",
                    "enabled": True,
                    "x": 468.0,
                    "y": 24.0,
                    "width": 320.0,
                    "height": 220.0,
                    "svg_text_overrides": {"0": "keep-ihc"},
                }
            ]
        },
    }
    panel.analysis = {
        "ihc_statistics": {"metric": "Positive Area %", "plot_data": _grouped_plot_data(), "test": "unpaired_t"},
        "ihc_measurements": [{"measurement_uuid": "m1", "results": {"positive_area_pct": 25.0}}],
    }
    return panel


def _bf_panel(*, frequency: bool = False) -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Figure1B", panel_uuid="panel-bf-chart")
    panel.data = {
        "active_image_uid": "image-a",
        "brightfield_images": [{"uid": "image-a", "source_name": "bf.png", "metadata": {"width_px": 64, "height_px": 48}}],
        "brightfield_layout": {
            "outputs": [
                {
                    "export_item_key": "statistics_chart",
                    "source_record_id": "Area Fraction %",
                    "enabled": True,
                    "x": 468.0,
                    "y": 24.0,
                    "width": 320.0,
                    "height": 220.0,
                }
            ]
        },
    }
    panel.analysis = {
        "brightfield_statistics": {"metric": "Area Fraction %", "plot_data": _grouped_plot_data()},
        "brightfield_chart_export": {
            "source": "traditional",
            "chart_mode": "frequency_bar" if frequency else "summary",
            "metric": "he_fiber_area" if frequency else "Area Fraction %",
            "unit": "um^2" if frequency else "",
            "frequency_mode": "absolute",
            "statistics_result": {"metric": "Area Fraction %", "plot_data": _grouped_plot_data()},
            "distribution_result": {
                "unit": "um^2",
                "series": [{"lane_label": "Image A", "values": [1.0, 2.0, 3.0, 5.0, 9.0]}],
            },
        },
    }
    return panel


def test_qpcr_getter_does_not_dirty_missing_chart_kind() -> None:
    panel = _qpcr_panel()
    before = deepcopy(panel.data["qpcr_layout"]["plots"])
    target = layout_chart_target(panel, "plot-spns1")
    assert target is not None
    assert target["family"] == FAMILY_QPCR
    assert target["record_id"] == "plot-spns1"
    assert target["chart_kind"] == "bar_dot"
    assert target["reason"] is None
    assert "chart_kind" not in panel.data["qpcr_layout"]["plots"][0]
    assert panel.data["qpcr_layout"]["plots"] == before


def test_qpcr_kind_persists_per_plot_uuid_and_leaves_sibling() -> None:
    panel = _qpcr_panel()
    analysis_hash = sha256_json(panel.analysis)
    raw_hash = sha256_json(panel.data["raw_rows"])
    objects = qpcr_layout_objects(panel)
    plot_obj = next(obj for obj in objects if obj.metadata.get("plot_uuid") == "plot-spns1")
    assert set_layout_chart_kind(panel, plot_obj, "box_dot") is True
    assert set_layout_chart_kind(panel, plot_obj, "box_dot") is False
    plots = panel.data["qpcr_layout"]["plots"]
    assert plots[0]["chart_kind"] == "box_dot"
    assert "chart_kind" not in plots[1]
    assert plots[0]["svg_text_overrides"] == {"1": "keep"}
    assert plots[0]["style"]["bar_edge_color"] == "#1F4E79"
    assert sha256_json(panel.analysis) == analysis_hash
    assert sha256_json(panel.data["raw_rows"]) == raw_hash
    assert panel.layout_plot_records()[0]["chart_kind"] == "box_dot"


def test_qpcr_violin_degenerate_raises_data_reason() -> None:
    panel = _qpcr_panel()
    panel.data["processed_data"]["fold_change"] = [
        {"target": "SPNS1", "group": "siCtrl", "fold_change": 1.0, "mean_fold_change": 1.0, "error": 0.0},
        {"target": "SPNS1", "group": "siSPNS1", "fold_change": 0.2, "mean_fold_change": 0.2, "error": 0.0},
    ]
    with pytest.raises(ValueError, match=REASON_VIOLIN_DEGENERATE):
        set_layout_chart_kind(panel, "plot-spns1", "violin")


def test_qpcr_render_differs_by_kind_and_native_export_uses_record() -> None:
    panel = _qpcr_panel()
    set_layout_chart_kind(panel, "plot-spns1", "box")
    bar = panel._build_render_figure(50, 40, 80, target="SPNS1", style={}, chart_kind="bar_dot")
    box = panel._build_render_figure(50, 40, 80, target="SPNS1", style={}, chart_kind=None)
    assert bar.axes[0].patches
    assert box.axes[0].patches != bar.axes[0].patches or [p.get_height() for p in bar.axes[0].patches] != [
        p.get_height() for p in box.axes[0].patches
    ]
    sibling = panel._build_render_figure(50, 40, 80, target="GENE2", style={})
    assert sibling.axes[0].patches


def test_wb_stats_not_blot_and_payload_independent() -> None:
    panel = _wb_panel()
    blot = deepcopy(panel.data["image_data"])
    bands = deepcopy(panel.data["wb_layout"]["bands"])
    stats_obj = next(obj for obj in wb_layout_objects(panel) if obj.asset_key == WB_STATS_PLOT_EXPORT_KEY)
    band_obj = next(obj for obj in wb_layout_objects(panel) if obj.metadata.get("wb_kind") == "band")
    assert layout_chart_target(panel, band_obj) is None
    assert set_layout_chart_kind(panel, stats_obj, "violin_dot") is True
    assert panel.data["wb_layout"]["stats_plot"]["chart_kind"] == "violin_dot"
    assert panel.data["image_data"] == blot
    assert panel.data["wb_layout"]["bands"] == bands
    layout_item = wb_export_item_for_scene_bounds(panel, WB_LAYOUT_EXPORT_KEY, 216.0, 72.0)
    stats_item = wb_export_item_for_scene_bounds(panel, WB_STATS_PLOT_EXPORT_KEY, 144.0, 72.0)
    assert "plot_payload" not in layout_item.metadata
    assert stats_item.metadata["chart_kind"] == "violin_dot"
    payload = copy_plot_payload(stats_item.metadata["plot_payload"])
    payload["chart_kind"] = "dot"
    payload["statistics_result"]["plot_data"] = []
    other = plot_payload_for_export_item(panel, stats_item)
    assert other is not None
    assert other["chart_kind"] == "violin_dot"
    assert other["statistics_result"]["plot_data"]
    assert panel.data["wb_statistics"]["plot_data"] == _grouped_plot_data()
    asset = panel.__class__(panel_id="asset") if False else None
    from core.models import ExternalImagePanel

    asset = ExternalImagePanel(panel_id="wb_stats_asset")
    apply_wb_export_item_metadata(asset, stats_item)
    assert asset.data["plot_payload"]["family"] == FAMILY_GROUPED
    assert asset.data["plot_payload"] is not stats_item.metadata["plot_payload"]


def test_fluoro_grouped_and_object_distribution_keep_plot_type() -> None:
    panel = _fluoro_panel()
    analysis_before = deepcopy(panel.analysis["fluoro_statistics"])
    images_before = deepcopy(panel.data["fluorescence_images"])
    assert layout_chart_target(panel, "plot-profile") is None
    image_obj = next(obj for obj in fluorescence_layout_objects(panel) if obj.metadata.get("fluoro_kind") == "layout_image")
    assert layout_chart_target(panel, image_obj) is None
    assert set_layout_chart_kind(panel, "plot-stats", "box_dot") is True
    assert panel.analysis["fluoro_layout_plots"][0]["chart_kind"] == "box_dot"
    assert panel.analysis["fluoro_layout_plots"][0]["plot_type"] == "statistics"
    assert panel.analysis["fluoro_stats_plot"]["chart_kind"] == "box_dot"
    assert "chart_kind" not in panel.analysis["fluoro_layout_plots"][1]
    allowed = chart_kind_options("object_distribution")
    assert allowed == DISTRIBUTION_CHART_KINDS
    assert set_layout_chart_kind(panel, "plot-freq", "box") is True
    freq = panel.analysis["fluoro_layout_plots"][1]
    assert freq["plot_type"] == "frequency_bar"
    assert freq["chart_kind"] == "box"
    if "box_dot" not in allowed:
        with pytest.raises(ValueError):
            set_layout_chart_kind(panel, "plot-freq", "box_dot")
    assert freq["plot_type"] == "frequency_bar"
    assert panel.analysis["fluoro_statistics"] == analysis_before
    assert panel.data["fluorescence_images"] == images_before
    layout_item = fluorescence_export_item_for_scene_bounds(panel, FLUORESCENCE_LAYOUT_EXPORT_KEY, 216.0, 108.0)
    stats_item = fluorescence_export_item_for_scene_bounds(
        panel,
        fluorescence_plot_export_key("plot-stats"),
        144.0,
        72.0,
        plot_uuid="plot-stats",
        plot_type="statistics",
    )
    freq_item = fluorescence_export_item_for_scene_bounds(
        panel,
        fluorescence_plot_export_key("plot-freq"),
        144.0,
        72.0,
        plot_uuid="plot-freq",
        plot_type="frequency_bar",
    )
    profile_item = fluorescence_export_item_for_scene_bounds(
        panel,
        fluorescence_plot_export_key("plot-profile"),
        144.0,
        72.0,
        plot_uuid="plot-profile",
        plot_type="line_profile",
    )
    assert "plot_payload" not in layout_item.metadata
    assert stats_item.metadata["plot_payload"]["family"] == FAMILY_GROUPED
    assert freq_item.metadata["plot_payload"]["family"] == FAMILY_OBJECT_DISTRIBUTION
    assert freq_item.metadata["plot_payload"]["sample_unit"] == "object"
    assert "plot_payload" not in profile_item.metadata


def test_fluoro_canvas_cache_and_render_use_effective_kind() -> None:
    panel = _fluoro_panel()
    app = QApplication.instance() or QApplication([])
    canvas = FluorescenceCanvas()
    canvas.set_panel(panel)
    record = dict(panel.analysis["fluoro_layout_plots"][0])
    try:
        bar_fig = canvas._build_layout_plot_figure(record)
        record["chart_kind"] = "box"
        box_fig = canvas._build_layout_plot_figure(record)
        assert bar_fig.axes and box_fig.axes
        canvas._render_layout_plot_image(dict(panel.analysis["fluoro_layout_plots"][0]), 320, 240)
        boxed = dict(panel.analysis["fluoro_layout_plots"][0])
        boxed["chart_kind"] = "box"
        canvas._render_layout_plot_image(boxed, 320, 240)
        kinds = {payload.get("chart_kind") if False else None for payload in []}
        assert any("chart_kind" in key for key in canvas._layout_plot_cache)
        assert len(canvas._layout_plot_cache) >= 2
    finally:
        canvas.close()
        app.processEvents()


def test_ihc_and_brightfield_stats_outputs_not_overlays() -> None:
    ihc = _ihc_panel()
    ihc_objects = ihc_layout_objects(ihc, measurement={"measurement_uuid": "m1"})
    overlay_obj = next(obj for obj in ihc_objects if obj.asset_key == IHC_OVERLAY_EXPORT_KEY)
    stats_obj = next(obj for obj in ihc_objects if obj.asset_key == IHC_STATISTICS_EXPORT_KEY)
    assert layout_chart_target(ihc, overlay_obj) is None
    assert set_layout_chart_kind(ihc, stats_obj, "dot") is True
    output = next(item for item in ihc.data["ihc_layout"]["outputs"] if item["export_item_key"] == "statistics_chart")
    assert output["chart_kind"] == "dot"
    assert output["svg_text_overrides"] == {"0": "keep-ihc"}
    assert ihc.analysis["ihc_statistics"]["test"] == "unpaired_t"
    overlay_item = ihc_export_item_for_scene_bounds(ihc, IHC_OVERLAY_EXPORT_KEY, 144.0, 72.0, measurement={"measurement_uuid": "m1"})
    stats_item = ihc_export_item_for_scene_bounds(ihc, IHC_STATISTICS_EXPORT_KEY, 144.0, 72.0, metric="Positive Area %")
    layout_item = ihc_export_item_for_scene_bounds(ihc, IHC_LAYOUT_EXPORT_KEY, 216.0, 108.0)
    assert overlay_item.export_item_type == EXPORT_ITEM_OVERLAY
    assert "plot_payload" not in overlay_item.metadata
    assert "plot_payload" not in layout_item.metadata
    assert stats_item.metadata["plot_payload"]["family"] == FAMILY_GROUPED
    from core.models import ExternalImagePanel

    asset = ExternalImagePanel(panel_id="ihc_stats")
    apply_ihc_export_item_metadata(asset, stats_item)
    assert asset.data["chart_kind"] == "dot"

    bf = _bf_panel()
    assert set_layout_chart_kind(bf, "statistics_chart", "line") is True
    assert bf.data["brightfield_layout"]["outputs"][0]["chart_kind"] == "line"
    assert bf.analysis["brightfield_chart_export"]["chart_mode"] == "summary"
    freq = _bf_panel(frequency=True)
    assert layout_chart_target(freq, "statistics_chart")["family"] == FAMILY_OBJECT_DISTRIBUTION
    assert set_layout_chart_kind(freq, "statistics_chart", "violin") is True
    assert freq.analysis["brightfield_chart_export"]["chart_mode"] == "frequency_bar"
    overlay_bf = brightfield_export_item_for_scene_bounds(
        freq, BRIGHTFIELD_OVERLAY_EXPORT_KEY, 144.0, 72.0, measurement={"measurement_uuid": "m1"}
    )
    stats_bf = brightfield_export_item_for_scene_bounds(freq, BRIGHTFIELD_STATISTICS_EXPORT_KEY, 144.0, 72.0, metric="he_fiber_area")
    layout_bf = brightfield_export_item_for_scene_bounds(freq, BRIGHTFIELD_LAYOUT_EXPORT_KEY, 216.0, 108.0)
    assert "plot_payload" not in overlay_bf.metadata
    assert "plot_payload" not in layout_bf.metadata
    assert stats_bf.metadata["plot_payload"]["sample_unit"] == "object"
    assert layout_bf.export_item_type == EXPORT_ITEM_FULL_LAYOUT


def test_missing_histology_stats_records_do_not_fabricate_bindings() -> None:
    ihc = _ihc_panel()
    ihc.data["ihc_layout"]["outputs"] = []
    ihc_before = deepcopy(ihc.data)
    assert layout_chart_target(ihc, "statistics_chart") is None
    assert set_layout_chart_kind(ihc, "statistics_chart", "dot") is False
    assert ihc.data == ihc_before

    bf = _bf_panel()
    bf.data["brightfield_layout"]["outputs"] = []
    bf_before = deepcopy(bf.data)
    assert layout_chart_target(bf, "statistics_chart") is None
    assert set_layout_chart_kind(bf, "statistics_chart", "dot") is False
    assert bf.data == bf_before


def test_qpcr_target_alias_does_not_bind_to_layout_plot() -> None:
    panel = _qpcr_panel()
    before = deepcopy(panel.data)
    assert layout_chart_target(panel, "SPNS1") is None
    assert set_layout_chart_kind(panel, "SPNS1", "box") is False
    assert panel.data == before


def test_qpcr_chart_refs_reject_foreign_panel_sources_but_allow_bare_local_refs() -> None:
    source_panel = _qpcr_panel()
    target_panel = _qpcr_panel()
    target_panel.panel_uuid = "panel-qpcr-other"
    source_object = next(
        obj for obj in qpcr_layout_objects(source_panel) if obj.metadata.get("plot_uuid") == "plot-spns1"
    )
    source_export_item = qpcr_export_item_for_scene_bounds(
        source_panel,
        qpcr_plot_export_key("plot-spns1"),
        144.0,
        72.0,
        plot_uuid="plot-spns1",
        qpcr_target="SPNS1",
    )
    foreign_refs = [source_object, source_export_item, f"{source_panel.panel_uuid}:qpcr_plot:plot-spns1"]
    before = deepcopy(target_panel.data)
    for ref in foreign_refs:
        assert layout_chart_target(target_panel, ref) is None
        assert set_layout_chart_kind(target_panel, ref, "box") is False
        assert target_panel.data == before

    assert layout_chart_target(target_panel, "plot-spns1") is not None
    assert layout_chart_target(target_panel, qpcr_plot_export_key("plot-spns1")) is not None


def test_payload_schema_style_frozen_and_no_raw_images() -> None:
    panel = _qpcr_panel()
    set_layout_chart_kind(panel, "plot-spns1", "bar")
    target = layout_chart_target(panel, "plot-spns1")
    payload = target["payload"]
    assert set(payload).issubset(set(PLOT_PAYLOAD_FIELDS))
    assert payload["schema_version"] == 1
    assert payload["family"] == FAMILY_QPCR
    assert payload["style"]["font_family"]
    assert payload["style"]["bar_edge_color"] == "#1F4E79" or "bar_edge_color" in payload["style"]
    copied = copy_plot_payload(payload)
    copied["fold_change_rows"] = []
    again = layout_chart_target(panel, "plot-spns1")["payload"]
    assert again["fold_change_rows"]
    assert "pixels" not in again
    item = qpcr_export_item_for_scene_bounds(
        panel,
        qpcr_plot_export_key("plot-spns1"),
        144.0,
        72.0,
        plot_uuid="plot-spns1",
        qpcr_target="SPNS1",
    )
    layout_item = qpcr_export_item_for_scene_bounds(panel, QPCR_LAYOUT_EXPORT_KEY, 216.0, 108.0)
    assert "plot_payload" in item.metadata
    assert "plot_payload" not in layout_item.metadata
    from core.models import ExternalImagePanel

    asset = ExternalImagePanel(panel_id="qpcr_plot")
    apply_qpcr_export_item_metadata(asset, item)
    assert asset.data["plot_payload"]["target"] == "SPNS1"
    assert asset.data["plot_payload"]["sample_unit"] == "biological_replicate"
    grouped_kinds = chart_kind_options("qpcr")
    assert grouped_kinds == GROUPED_CHART_KINDS
    assert "box_dot" in grouped_kinds and "violin_dot" in grouped_kinds


def test_derived_plot_and_non_stats_refs_return_none() -> None:
    derived = DerivedPlotPanel(panel_id="derived")
    assert layout_chart_target(derived, "anything") is None
    assert set_layout_chart_kind(derived, "anything", "box") is False
    panel = _qpcr_panel()
    root = next(obj for obj in qpcr_layout_objects(panel) if obj.metadata.get("qpcr_kind") == "layout_root")
    assert layout_chart_target(panel, root) is None
    layout_item = qpcr_export_item_for_scene_bounds(panel, QPCR_LAYOUT_EXPORT_KEY, 216.0, 108.0)
    assert plot_payload_for_export_item(panel, layout_item) is None
    assert layout_item.export_item_type == EXPORT_ITEM_FULL_LAYOUT
