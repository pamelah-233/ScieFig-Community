from __future__ import annotations

import copy
import warnings

from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
import pytest

from core.brightfield_statistics import (
    collect_brightfield_ai_object_area_distribution_rows,
    collect_brightfield_stat_values,
    compute_brightfield_ai_object_count_statistics,
    compute_brightfield_statistics,
    summarize_brightfield_ai_object_count_measurements,
)
from core.models import BrightfieldPanel
from core.plotting.statistics_plot import build_distribution_comparison_figure, build_group_statistics_figure
from core.plotting.statistics_plot import resolve_statistics_plot_style
from core.plotting import style_presets
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry
from core.style_preset_resolver import clear_runtime_active_style_preset, set_runtime_active_style_preset


def _measurement(
    image_uid: str,
    image_name: str,
    measurement_uuid: str,
    group: str,
    stain_type: str,
    results: dict,
) -> dict:
    return {
        "measurement_uuid": measurement_uuid,
        "image_uid": image_uid,
        "image_name": image_name,
        "group": group,
        "stain_type": stain_type,
        "type": "brightfield_measurement",
        "params": {"segmentation_mode": "hsv"},
        "results": dict(results),
        "created_at": "2026-05-14T00:00:00Z",
    }


def _ai_measurement(
    image_uid: str,
    image_name: str,
    measurement_uuid: str,
    group: str,
    stain_type: str,
    *,
    count: int,
    include_in_statistics: bool = True,
) -> dict:
    return {
        "measurement_uuid": measurement_uuid,
        "image_uid": image_uid,
        "image_name": image_name,
        "group": group,
        "stain_type": stain_type,
        "type": "brightfield_ai_object_count",
        "results": {
            "ai_object_count": count,
            "ai_object_mean_area_px": 14.0,
            "ai_object_total_area_px": float(count) * 14.0,
        },
        "include_in_statistics": include_in_statistics,
        "created_at": "2026-05-14T00:00:00Z",
    }


def _panel_with_measurements() -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Figure1BFStats")
    panel.data["brightfield_images"] = [
        {"uid": "img-control-a", "source_name": "control_a.png", "group": "Control", "stain_type": "H&E", "measurements": []},
        {"uid": "img-control-b", "source_name": "control_b.png", "group": "Control", "stain_type": "H&E", "measurements": []},
        {"uid": "img-treated-a", "source_name": "treated_a.png", "group": "Treatment", "stain_type": "Oil Red O", "measurements": []},
        {"uid": "img-treated-b", "source_name": "treated_b.png", "group": "Treatment", "stain_type": "Oil Red O", "measurements": []},
    ]
    measurements = [
        _measurement("img-control-a", "control_a.png", "m-control-a", "Control", "H&E", {"area_fraction_percent": 25.0, "object_count": 4, "mean_object_area_px": 11.0, "roi_mean_intensity": 0.31}),
        _measurement("img-control-b", "control_b.png", "m-control-b", "Control", "H&E", {"area_fraction_percent": 30.0, "object_count": 5, "mean_object_area_px": 12.0, "roi_mean_intensity": 0.34}),
        _measurement("img-treated-a", "treated_a.png", "m-treated-a", "Treatment", "Oil Red O", {"area_fraction_percent": 55.0, "object_count": 9, "mean_object_area_px": 18.0, "roi_mean_intensity": 0.52}),
        _measurement("img-treated-b", "treated_b.png", "m-treated-b", "Treatment", "Oil Red O", {"area_fraction_percent": 60.0, "object_count": 11, "mean_object_area_px": 19.0, "roi_mean_intensity": 0.57}),
        _measurement("img-treated-b", "treated_b.png", "m-missing", "Treatment", "Oil Red O", {"object_count": "bad"}),
    ]
    for image in panel.data["brightfield_images"]:
        image["measurements"] = [item for item in measurements if item["image_uid"] == image["uid"]]
    panel.analysis["brightfield_measurements"] = measurements
    return panel


def _panel_with_repeated_measurements() -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Figure1BFStatsRepeated")
    panel.data["brightfield_images"] = [
        {"uid": "img-control-a", "source_name": "control_a.png", "group": "Control", "stain_type": "H&E", "measurements": []},
        {"uid": "img-control-b", "source_name": "control_b.png", "group": "Control", "stain_type": "H&E", "measurements": []},
        {"uid": "img-treated-a", "source_name": "treated_a.png", "group": "Treatment", "stain_type": "Oil Red O", "measurements": []},
        {"uid": "img-treated-b", "source_name": "treated_b.png", "group": "Treatment", "stain_type": "Oil Red O", "measurements": []},
    ]
    measurements = [
        _measurement("img-control-a", "control_a.png", "m-control-a-1", "Control", "H&E", {"area_fraction_percent": 20.0, "object_count": 4, "mean_object_area_px": 10.0, "roi_mean_intensity": 0.30}),
        _measurement("img-control-a", "control_a.png", "m-control-a-2", "Control", "H&E", {"area_fraction_percent": 40.0, "object_count": 6, "mean_object_area_px": 14.0, "roi_mean_intensity": 0.36}),
        _measurement("img-control-b", "control_b.png", "m-control-b", "Control", "H&E", {"area_fraction_percent": 30.0, "object_count": 5, "mean_object_area_px": 12.0, "roi_mean_intensity": 0.34}),
        _measurement("img-treated-a", "treated_a.png", "m-treated-a", "Treatment", "Oil Red O", {"area_fraction_percent": 55.0, "object_count": 9, "mean_object_area_px": 18.0, "roi_mean_intensity": 0.52}),
        _measurement("img-treated-b", "treated_b.png", "m-treated-b", "Treatment", "Oil Red O", {"area_fraction_percent": 60.0, "object_count": 11, "mean_object_area_px": 19.0, "roi_mean_intensity": 0.57}),
    ]
    for image in panel.data["brightfield_images"]:
        image["measurements"] = [item for item in measurements if item["image_uid"] == image["uid"]]
    panel.analysis["brightfield_measurements"] = measurements
    return panel


def test_collect_brightfield_stat_values_uses_latest_recalculation_per_image() -> None:
    panel = _panel_with_repeated_measurements()

    rows = collect_brightfield_stat_values(panel, "Area Fraction %")

    assert len(rows) == 4
    assert [row["image_uid"] for row in rows] == [
        "img-control-a",
        "img-control-b",
        "img-treated-a",
        "img-treated-b",
    ]
    assert [row["value"] for row in rows] == [40.0, 30.0, 55.0, 60.0]
    assert [row["group"] for row in rows] == ["Control", "Control", "Treatment", "Treatment"]
    assert rows[0]["sample_key"] == "img-control-a"
    assert rows[0]["measurement_uuids"] == ["m-control-a-2"]
    assert rows[0]["n_measurements"] == 1
    assert rows[0]["aggregation"] == "mean_per_image"


def test_collect_brightfield_stat_values_returns_image_level_rows() -> None:
    panel = _panel_with_measurements()

    rows = collect_brightfield_stat_values(panel, "Area Fraction %")

    assert [row["value"] for row in rows] == [25.0, 30.0, 55.0, 60.0]
    assert [row["group"] for row in rows] == ["Control", "Control", "Treatment", "Treatment"]
    assert rows[0]["source"] == "brightfield_measurement"
    assert rows[0]["sample_key"] == "img-control-a"
    assert rows[0]["n_measurements"] == 1


def test_compute_brightfield_statistics_counts_images_not_measurements() -> None:
    panel = _panel_with_repeated_measurements()
    rows = collect_brightfield_stat_values(panel, "Mean Object Area")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}
    group_assignments.update({row["sample_key"]: row["group"] for row in rows})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = compute_brightfield_statistics(
            panel,
            metric="Mean Object Area",
            group_assignments=group_assignments,
            reference_group="Control",
            test_name="Welch t-test",
        )

    assert panel.analysis["brightfield_statistics"] is result
    assert result["source"] == "brightfield_measurement"
    assert result["metric"] == "Mean Object Area"
    assert {group["group"]: group["n"] for group in result["groups"]} == {"Control": 2, "Treatment": 2}
    assert len(result["plot_data"]) == 4
    assert [row["value"] for row in result["plot_data"] if row["group"] == "Control"] == [14.0, 12.0]
    from scipy.stats import ttest_ind
    assert result['comparisons'][0]['p_value']==pytest.approx(ttest_ind([14.,12.],[18.,19.],equal_var=False).pvalue)
    assert not [item for item in caught if "Precision loss" in str(item.message)]

    figure = build_group_statistics_figure(result, y_label="Mean Object Area")

    assert figure.axes[0].yaxis.label.get_text() == "Mean Object Area"


def test_collect_brightfield_stat_values_skips_missing_or_non_numeric_metrics() -> None:
    panel = _panel_with_measurements()

    rows = collect_brightfield_stat_values(panel, "Object Count")

    assert [row["image_uid"] for row in rows] == [
        "img-control-a",
        "img-control-b",
        "img-treated-a",
        "img-treated-b",
    ]
    assert [row["value"] for row in rows] == [4.0, 5.0, 9.0, 11.0]
    assert [row["n_measurements"] for row in rows] == [1, 1, 1, 1]


def test_collect_brightfield_stat_values_accepts_segmented_region_count_aliases() -> None:
    panel = _panel_with_measurements()

    count_rows = collect_brightfield_stat_values(panel, "Segmented Region Count")
    regions_rows = collect_brightfield_stat_values(panel, "Segmented Regions")
    legacy_rows = collect_brightfield_stat_values(panel, "Object Count")

    assert [row["value"] for row in count_rows] == [4.0, 5.0, 9.0, 11.0]
    assert [row["value"] for row in regions_rows] == [4.0, 5.0, 9.0, 11.0]
    assert count_rows == regions_rows == legacy_rows


def test_traditional_brightfield_statistics_respect_explicit_exclusion() -> None:
    panel = _panel_with_measurements()
    panel.analysis["brightfield_measurements"][0]["include_in_statistics"] = False
    panel.analysis["brightfield_measurements"][0]["reliability"] = {"status": "FAIL", "include_in_statistics": False}

    rows = collect_brightfield_stat_values(panel, "Object Count")

    assert [row["image_uid"] for row in rows] == [
        "img-control-b",
        "img-treated-a",
        "img-treated-b",
    ]
    assert rows[0]["value"] == 5.0


def test_compute_brightfield_statistics_uses_segmented_region_count_label() -> None:
    panel = _panel_with_measurements()
    rows = collect_brightfield_stat_values(panel, "Segmented Region Count")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}
    group_assignments.update({row["sample_key"]: row["group"] for row in rows})

    result = compute_brightfield_statistics(
        panel,
        metric="Segmented Region Count",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert result["metric"] == "Segmented Region Count"
    assert result["series_label"] == "Segmented Region Count"
    assert [row["metric"] for row in result["rows"]] == ["Segmented Region Count"] * 4


def test_compute_brightfield_ai_object_count_statistics_reads_only_ai_measurements() -> None:
    panel = _panel_with_measurements()
    panel.analysis["brightfield_ai_object_count_measurements"] = [
        _ai_measurement(
            "img-control-a",
            "control_a.png",
            "ai-control-a",
            "Control",
            "H&E",
            count=7,
            include_in_statistics=True,
        ),
        _ai_measurement(
            "img-treated-a",
            "treated_a.png",
            "ai-treated-a",
            "Treatment",
            "Oil Red O",
            count=11,
            include_in_statistics=False,
        ),
    ]

    result = compute_brightfield_ai_object_count_statistics(
        panel,
        group_assignments={"ai-control-a": "Control"},
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert result["source"] == "brightfield_ai_object_count"
    assert result["metric"] == "H&E Fiber Count"
    assert [row["measurement_uuid"] for row in result["plot_data"]] == ["ai-control-a"]
    assert all(row["measurement_uuid"] != "m-control-a" for row in result["plot_data"])
    assert result["groups"] == [{"group": "Control", "n": 1, "mean": 7.0, "sem": 0.0}]


def test_collect_he_fiber_area_distribution_keeps_each_measurement_as_a_series() -> None:
    panel = _panel_with_measurements()
    panel.analysis["brightfield_ai_object_count_measurements"] = [
        {
            **_ai_measurement(
                "img-control-a", "control_a.png", "ai-control-a", "Control", "H&E", count=3, include_in_statistics=True
            ),
            "results": {"ai_object_count": 3, "ai_object_areas_px": [10, 20, 30]},
        },
        {
            **_ai_measurement(
                "img-treated-a", "treated_a.png", "ai-treated-a", "Treatment", "H&E", count=2, include_in_statistics=False
            ),
            "results": {"ai_object_count": 2, "ai_object_areas_px": [40, 50]},
        },
    ]

    rows = collect_brightfield_ai_object_area_distribution_rows(panel)

    assert [row["measurement_uuid"] for row in rows] == ["ai-control-a"]
    assert rows[0]["values"] == [10.0, 20.0, 30.0]
    assert rows[0]["sample_key"] == "ai-control-a"
    assert rows[0]["lane_label"] == "control_a.png (n=3)"
    assert rows[0]["unit"] == "px^2"


def test_collect_he_fiber_area_distribution_uses_um2_when_measurement_scale_is_calibrated() -> None:
    panel = _panel_with_measurements()
    panel.analysis["brightfield_ai_object_count_measurements"] = [
        {
            **_ai_measurement(
                "img-control-a", "control_a.png", "ai-control-a", "Control", "H&E", count=3, include_in_statistics=True
            ),
            "measurement_scale_bar": {"enabled": True, "um_per_px": 0.5, "calibration_source": "manual_line"},
            "results": {
                "ai_object_count": 3,
                "ai_object_areas_px": [10, 20, 30],
                "ai_object_areas_um2": [2.5, 5.0, 7.5],
            },
        },
    ]

    rows = collect_brightfield_ai_object_area_distribution_rows(panel)

    assert rows[0]["metric"] == "he_fiber_area_distribution_um2"
    assert rows[0]["unit"] == "um^2"
    assert rows[0]["values"] == [2.5, 5.0, 7.5]


def test_distribution_comparison_figure_can_normalize_each_image_to_percent() -> None:
    figure = build_distribution_comparison_figure(
        {"series": [{"lane_label": "Image A", "values": [10, 20, 30, 40]}]},
        frequency_mode="percent",
    )

    axis = figure.axes[0]
    assert axis.yaxis.label.get_text() == "Percent of objects"
    assert sum(float(patch.get_height()) for patch in axis.patches) == pytest.approx(100.0)


def test_distribution_comparison_bar_groups_do_not_overlap_between_bins() -> None:
    figure = build_distribution_comparison_figure(
        {
            "series": [
                {"lane_label": "Image A", "values": [0, 1, 2, 3, 4, 5]},
                {"lane_label": "Image B", "values": [0, 1, 2, 3, 4, 5]},
            ]
        },
        plot_kind="bar",
    )

    spans = sorted(
        (float(patch.get_x()), float(patch.get_x() + patch.get_width()))
        for patch in figure.axes[0].patches
        if patch.get_height() > 0
    )
    for previous, current in zip(spans, spans[1:]):
        assert previous[1] <= current[0] + 1e-9


def test_ai_summary_defaults_missing_include_flag_to_true_and_traditional_stats_ignore_ai() -> None:
    panel = _panel_with_measurements()
    ai_measurement = _ai_measurement(
        "img-control-a",
        "control_a.png",
        "ai-control-a",
        "Control",
        "H&E",
        count=7,
    )
    ai_measurement.pop("include_in_statistics", None)
    panel.analysis["brightfield_ai_object_count_measurements"] = [ai_measurement]

    summary = summarize_brightfield_ai_object_count_measurements(panel)

    assert summary["n_measurements"] == 1
    assert summary["rows"][0]["measurement_uuid"] == "ai-control-a"
    assert summary["rows"][0]["ai_object_count"] == 7.0

    traditional_rows = collect_brightfield_stat_values(panel, "Object Count")
    assert traditional_rows
    assert all(row["source"] == "brightfield_measurement" for row in traditional_rows)
    assert all("ai-control-a" not in row.get("measurement_uuids", []) for row in traditional_rows)


def test_brightfield_statistics_style_can_follow_panel_preset_without_changing_results(monkeypatch) -> None:
    def patched_preset(name: str = "Nature", path=None) -> dict:
        preset = copy.deepcopy(style_presets.get_journal_preset(name, path))
        if str(name).casefold() != "cell":
            return preset
        preset.setdefault("palette", {})
        preset["palette"].update(
            {
                "blue_main": "#1565C0",
                "neutral_black": "#6D4C41",
            }
        )
        preset.setdefault("semantic_roles", {})
        preset["semantic_roles"].update(
            {
                "treatment_primary": "blue_main",
                "bar_edge": "neutral_black",
                "point_edge": "neutral_black",
            }
        )
        return preset

    import core.plotting.statistics_plot as statistics_plot

    monkeypatch.setattr(statistics_plot, "get_journal_preset", patched_preset)

    panel = _panel_with_measurements()
    rows = collect_brightfield_stat_values(panel, "Area Fraction %")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}
    group_assignments.update({row["sample_key"]: row["group"] for row in rows})

    panel.style["preset"] = "Nature"
    nature_result = compute_brightfield_statistics(
        panel,
        metric="Area Fraction %",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    nature_figure = build_group_statistics_figure(
        nature_result,
        style=resolve_statistics_plot_style(panel.style["preset"]),
        y_label="Area Fraction %",
    )
    nature_ax = nature_figure.axes[0]
    nature_scatter = next(collection for collection in nature_ax.collections if isinstance(collection, PathCollection))
    nature_snapshot = copy.deepcopy(nature_result)

    panel.style["preset"] = "Cell"
    cell_result = compute_brightfield_statistics(
        panel,
        metric="Area Fraction %",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    cell_figure = build_group_statistics_figure(
        cell_result,
        style=resolve_statistics_plot_style(panel.style["preset"]),
        y_label="Area Fraction %",
    )
    cell_ax = cell_figure.axes[0]
    cell_scatter = next(collection for collection in cell_ax.collections if isinstance(collection, PathCollection))

    assert to_hex(nature_ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(nature_scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert to_hex(cell_ax.patches[0].get_edgecolor()).upper() == "#6D4C41"
    assert to_hex(cell_scatter.get_facecolors()[0]).upper() == "#1565C0"
    assert cell_result["plot_data"] == nature_snapshot["plot_data"]
    assert cell_result["groups"] == nature_snapshot["groups"]
    assert cell_result["comparisons"] == nature_snapshot["comparisons"]


def test_brightfield_runtime_saved_preset_changes_statistics_visual_tokens_without_changing_results(tmp_path) -> None:
    panel = _panel_with_measurements()
    rows = collect_brightfield_stat_values(panel, "Area Fraction %")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}
    group_assignments.update({row["sample_key"]: row["group"] for row in rows})
    result = compute_brightfield_statistics(
        panel,
        metric="Area Fraction %",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    result_snapshot = copy.deepcopy(result)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime Brightfield",
            source_type="user_modified",
            plot_style={"point_color": "#4455AA", "bar_edge_color": "#AA5544", "axis_line_width": 1.7},
            extraction_profile={},
            source_metadata={
                "seed_preset_name": "Nature",
                "source_kind": "manual",
                "source_path": "",
                "source_page_index": 0,
                "source_page_count": 1,
                "source_size_px": {"width": 0, "height": 0},
                "source_crop_rect": {"x": 0, "y": 0, "width": 0, "height": 0},
            },
            warnings=[],
        )
    )

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        figure = build_group_statistics_figure(
            result,
            style=resolve_statistics_plot_style("Nature"),
            y_label="Area Fraction %",
        )
        ax = figure.axes[0]
        scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

        assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#AA5544"
        assert to_hex(scatter.get_facecolors()[0]).upper() == "#4455AA"
        assert ax.spines["left"].get_linewidth() == 1.7
        assert result == result_snapshot
    finally:
        clear_runtime_active_style_preset()
