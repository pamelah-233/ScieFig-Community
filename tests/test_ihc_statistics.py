from __future__ import annotations

import copy

from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex

from core.models import IHCPanel
from core.plotting.statistics_plot import build_group_statistics_figure
from core.plotting.statistics_plot import resolve_statistics_plot_style
from core.ihc_statistics import collect_ihc_stat_values, compute_ihc_statistics
from core.plotting import style_presets
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry
from core.style_preset_resolver import clear_runtime_active_style_preset, set_runtime_active_style_preset


def _measurement(
    image_uid: str,
    image_name: str,
    measurement_uuid: str,
    group: str,
    results: dict,
    reliability: dict | None = None,
) -> dict:
    return {
        "measurement_uuid": measurement_uuid,
        "image_uid": image_uid,
        "image_name": image_name,
        "group": group,
        "type": "dab_positive_area",
        "params": {"positive_threshold": 0.30},
        "results": dict(results),
        "reliability": dict(reliability or {"status": "PASS", "include_in_statistics": True}),
        "created_at": "2026-05-14T00:00:00Z",
    }


def _panel_with_measurements() -> IHCPanel:
    panel = IHCPanel(panel_id="Figure1IHCStats")
    panel.data["ihc_images"] = [
        {"uid": "img-control-a", "source_name": "control_a.png", "group": "Control", "measurements": []},
        {"uid": "img-control-b", "source_name": "control_b.png", "group": "Control", "measurements": []},
        {"uid": "img-treated-a", "source_name": "treated_a.png", "group": "Treatment", "measurements": []},
        {"uid": "img-treated-b", "source_name": "treated_b.png", "group": "Treatment", "measurements": []},
    ]
    measurements = [
        _measurement(
            "img-control-a",
            "control_a.png",
            "m-control-a",
            "Control",
            {"positive_area_percent": 25.0, "mean_dab_intensity": 0.35, "h_score": 80.0},
        ),
        _measurement(
            "img-control-b",
            "control_b.png",
            "m-control-b",
            "Control",
            {"positive_area_percent": 35.0, "mean_dab_intensity": 0.40, "h_score": 95.0},
        ),
        _measurement(
            "img-treated-a",
            "treated_a.png",
            "m-treated-a",
            "Treatment",
            {"positive_area_percent": 60.0, "mean_dab_intensity": 0.62, "h_score": 150.0},
        ),
        _measurement(
            "img-treated-b",
            "treated_b.png",
            "m-treated-b",
            "Treatment",
            {"positive_area_percent": 68.0, "mean_dab_intensity": 0.66, "h_score": 172.0},
        ),
        _measurement(
            "img-treated-b",
            "treated_b.png",
            "m-treated-missing",
            "Treatment",
            {"mean_dab_intensity": "not-a-number"},
        ),
    ]
    for image in panel.data["ihc_images"]:
        image["measurements"] = [item for item in measurements if item["image_uid"] == image["uid"]]
    panel.analysis["ihc_measurements"] = measurements
    return panel


def test_collect_ihc_stat_values_returns_measurement_rows() -> None:
    panel = _panel_with_measurements()

    rows = collect_ihc_stat_values(panel, "Positive Area %")

    assert [row["value"] for row in rows] == [25.0, 35.0, 60.0, 68.0]
    assert [row["group"] for row in rows] == ["Control", "Control", "Treatment", "Treatment"]
    assert [row["image_uid"] for row in rows] == [
        "img-control-a",
        "img-control-b",
        "img-treated-a",
        "img-treated-b",
    ]
    assert rows[0]["image_name"] == "control_a.png"
    assert rows[0]["measurement_uuid"] == "m-control-a"
    assert rows[0]["metric"] == "Positive Area %"
    assert rows[0]["source"] == "ihc_measurement"
    assert rows[0]["lane_label"] == "control_a.png"
    assert rows[0]["sample_key"] == "m-control-a"


def test_compute_ihc_statistics_persists_result_and_supports_group_plot() -> None:
    panel = _panel_with_measurements()
    rows = collect_ihc_stat_values(panel, "H-score")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}

    result = compute_ihc_statistics(
        panel,
        metric="H-score",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert panel.analysis["ihc_statistics"] is result
    assert result["source"] == "ihc_measurement"
    assert result["metric"] == "H-score"
    assert result["group_assignments"] == group_assignments
    assert {group["group"]: group["n"] for group in result["groups"]} == {"Control": 2, "Treatment": 2}
    assert len(result["plot_data"]) == 4

    figure = build_group_statistics_figure(result, y_label="H-score")

    assert figure.axes[0].yaxis.label.get_text() == "H-score"


def test_collect_ihc_stat_values_skips_missing_or_non_numeric_metrics() -> None:
    panel = _panel_with_measurements()

    rows = collect_ihc_stat_values(panel, "Mean DAB Intensity")

    assert [row["measurement_uuid"] for row in rows] == [
        "m-control-a",
        "m-control-b",
        "m-treated-a",
        "m-treated-b",
    ]
    assert [row["value"] for row in rows] == [0.35, 0.40, 0.62, 0.66]


def test_compute_ihc_statistics_supports_per_measurement_group_assignments() -> None:
    panel = _panel_with_measurements()
    rows = collect_ihc_stat_values(panel, "Positive Area %")
    custom_assignments = {
        "m-control-a": "Control",
        "m-control-b": "Control",
        "m-treated-a": "Treatment",
        "m-treated-b": "Treatment",
    }

    result = compute_ihc_statistics(
        panel,
        metric="Positive Area %",
        group_assignments=custom_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert {group["group"]: group["n"] for group in result["groups"]} == {"Control": 2, "Treatment": 2}
    assert [row["group"] for row in result["plot_data"]] == ["Control", "Control", "Treatment", "Treatment"]
    assert [row["image_name"] for row in result["plot_data"]] == [
        "control_a.png",
        "control_b.png",
        "treated_a.png",
        "treated_b.png",
    ]


def test_compute_ihc_statistics_excludes_default_gated_measurements_when_not_assigned() -> None:
    panel = _panel_with_measurements()
    panel.analysis["ihc_measurements"][3]["reliability"] = {"status": "FAIL", "include_in_statistics": False}

    result = compute_ihc_statistics(
        panel,
        metric="Positive Area %",
        group_assignments={
            "m-control-a": "Control",
            "m-control-b": "Control",
            "m-treated-a": "Treatment",
        },
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert [row["measurement_uuid"] for row in result["plot_data"]] == [
        "m-control-a",
        "m-control-b",
        "m-treated-a",
    ]
    assert {group["group"]: group["n"] for group in result["groups"]} == {"Control": 2, "Treatment": 1}


def test_ihc_statistics_style_can_follow_panel_preset_without_changing_results(monkeypatch) -> None:
    def patched_preset(name: str = "Nature", path=None) -> dict:
        preset = copy.deepcopy(style_presets.get_journal_preset(name, path))
        if str(name).casefold() != "science":
            return preset
        preset.setdefault("palette", {})
        preset["palette"].update(
            {
                "blue_main": "#C62828",
                "neutral_black": "#37474F",
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
    rows = collect_ihc_stat_values(panel, "H-score")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}

    panel.style["preset"] = "Nature"
    nature_result = compute_ihc_statistics(
        panel,
        metric="H-score",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    nature_figure = build_group_statistics_figure(
        nature_result,
        style=resolve_statistics_plot_style(panel.style["preset"]),
        y_label="H-score",
    )
    nature_ax = nature_figure.axes[0]
    nature_scatter = next(collection for collection in nature_ax.collections if isinstance(collection, PathCollection))
    nature_snapshot = copy.deepcopy(nature_result)

    panel.style["preset"] = "Science"
    science_result = compute_ihc_statistics(
        panel,
        metric="H-score",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    science_figure = build_group_statistics_figure(
        science_result,
        style=resolve_statistics_plot_style(panel.style["preset"]),
        y_label="H-score",
    )
    science_ax = science_figure.axes[0]
    science_scatter = next(collection for collection in science_ax.collections if isinstance(collection, PathCollection))

    assert to_hex(nature_ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(nature_scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert to_hex(science_ax.patches[0].get_edgecolor()).upper() == "#37474F"
    assert to_hex(science_scatter.get_facecolors()[0]).upper() == "#C62828"
    assert science_result["plot_data"] == nature_snapshot["plot_data"]
    assert science_result["groups"] == nature_snapshot["groups"]
    assert science_result["comparisons"] == nature_snapshot["comparisons"]


def test_ihc_runtime_saved_preset_changes_statistics_visual_tokens_without_changing_results(tmp_path) -> None:
    panel = _panel_with_measurements()
    rows = collect_ihc_stat_values(panel, "H-score")
    group_assignments = {row["lane_label"]: row["group"] for row in rows}
    result = compute_ihc_statistics(
        panel,
        metric="H-score",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )
    result_snapshot = copy.deepcopy(result)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime IHC",
            source_type="user_modified",
            plot_style={"point_color": "#118844", "bar_edge_color": "#884411", "axis_line_width": 1.9},
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
            y_label="H-score",
        )
        ax = figure.axes[0]
        scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

        assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#884411"
        assert to_hex(scatter.get_facecolors()[0]).upper() == "#118844"
        assert ax.spines["left"].get_linewidth() == 1.9
        assert result == result_snapshot
    finally:
        clear_runtime_active_style_preset()
