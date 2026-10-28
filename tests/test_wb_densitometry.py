from __future__ import annotations

import copy
from pathlib import Path

import pytest
from matplotlib.collections import PathCollection
from matplotlib.colors import to_hex
from matplotlib.figure import Figure
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QPainter

from core.densitometry import measure_densitometry
from core.models import WesternBlotPanel
from core.plotting import style_presets
from core.plotting_densitometry import build_densitometry_figure
from core.plotting_densitometry import build_wb_statistics_figure
from core.plotting.statistics_plot import build_group_statistics_figure, resolve_statistics_plot_style
from core.style_preset_registry import StylePresetRecord, StylePresetRegistry
from core.style_preset_resolver import (
    clear_runtime_active_style_preset,
    last_runtime_style_preset_status,
    set_runtime_active_style_preset,
)
from ui.main_window import MainWindow
from ui.widgets.wb_canvas import WBCanvas


def _write_lane_strip(path: Path, lane_values: list[int], width: int = 120, height: int = 30) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(0, 0, 0))
    painter = QPainter(image)
    lane_width = width // len(lane_values)
    for index, value in enumerate(lane_values):
        painter.setBrush(QColor(value, value, value))
        painter.setPen(QColor(value, value, value))
        painter.drawRect(index * lane_width + 8, 8, lane_width - 16, height - 16)
    painter.end()
    assert image.save(str(path))
    return path


def _write_dark_band_strip(path: Path, lane_values: list[int], width: int = 120, height: int = 30) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(230, 230, 230))
    painter = QPainter(image)
    lane_width = width // len(lane_values)
    for index, value in enumerate(lane_values):
        painter.setBrush(QColor(value, value, value))
        painter.setPen(QColor(value, value, value))
        painter.drawRect(index * lane_width + 8, 8, lane_width - 16, height - 16)
    painter.end()
    assert image.save(str(path))
    return path


def _write_clipped_dark_band_strip(path: Path, lane_values: list[int], band_height: int, width: int = 120, height: int = 60) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(230, 230, 230))
    painter = QPainter(image)
    lane_width = width // len(lane_values)
    y = height // 2 - band_height // 2
    for index, value in enumerate(lane_values):
        painter.setBrush(QColor(value, value, value))
        painter.setPen(QColor(value, value, value))
        painter.drawRect(index * lane_width + 8, y, lane_width - 16, band_height)
    painter.end()
    assert image.save(str(path))
    return path


def _panel_data(target_path: Path, loading_path: Path, adjustments: dict | None = None) -> dict:
    adjustments = adjustments or {"brightness": 0, "contrast": 0, "rotation": 0.0, "flip_horizontal": False, "flip_vertical": False}
    return {
        "placed_strip_ids": ["strip_1", "strip_2"],
        "annotations": {
            "lane_labels": ["Control", "KO", "Rescue"],
            "lane_anchors": [20.0, 60.0, 100.0],
        },
        "band_strips": [
            {
                "strip_id": "strip_1",
                "protein_name": "SPNS1",
                "image_path": str(target_path),
                "crop_roi": {"x": 0, "y": 0, "w": 120, "h": 30},
                "adjustments": dict(adjustments),
            },
            {
                "strip_id": "strip_2",
                "protein_name": "GAPDH",
                "image_path": str(loading_path),
                "crop_roi": {"x": 0, "y": 0, "w": 120, "h": 30},
                "adjustments": dict(adjustments),
            },
        ],
    }


def test_densitometry_uses_raw_pixels_not_display_brightness_or_contrast(tmp_path) -> None:
    target = _write_lane_strip(tmp_path / "target.png", [120, 220, 180])
    loading = _write_lane_strip(tmp_path / "loading.png", [160, 160, 160])

    baseline = measure_densitometry(
        _panel_data(target, loading),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )
    display_adjusted = measure_densitometry(
        _panel_data(target, loading, {"brightness": 80, "contrast": 90, "rotation": 0.0, "flip_horizontal": False, "flip_vertical": False}),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )

    assert baseline["measurement_basis"] == "raw_cropped"
    assert baseline["loading_control_strip_id"] == "strip_2"
    assert baseline["reference_lane_index"] == 0
    assert baseline["measurements"][0]["strip_id"] == "strip_1"
    assert baseline["measurements"][0]["lanes"][1]["lane_label"] == "KO"
    assert display_adjusted["measurements"][0]["lanes"][1]["fold"] == pytest.approx(
        baseline["measurements"][0]["lanes"][1]["fold"],
        rel=0,
        abs=1e-4,
    )


def test_dark_bands_on_light_background_are_positive_signal_without_negative_density_warnings(tmp_path) -> None:
    target = _write_dark_band_strip(tmp_path / "target_dark.png", [150, 70, 110])
    loading = _write_dark_band_strip(tmp_path / "loading_dark.png", [120, 120, 120])

    result = measure_densitometry(
        _panel_data(target, loading),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="local_edge",
        polarity_mode="dark_on_light",
    )

    spns1_lanes = result["measurements"][0]["lanes"]
    assert spns1_lanes[1]["fold"] > spns1_lanes[0]["fold"]
    assert all("negative_density" not in lane["qc_flags"] for measurement in result["measurements"] for lane in measurement["lanes"])
    assert all("roi_too_small" not in lane["qc_flags"] for measurement in result["measurements"] for lane in measurement["lanes"])
    assert all("saturated" not in lane["qc_flags"] for measurement in result["measurements"] for lane in measurement["lanes"])


def test_small_fraction_of_clipped_pixels_is_not_marked_saturated(tmp_path) -> None:
    target = _write_clipped_dark_band_strip(tmp_path / "target_clipped.png", [0, 0, 0], band_height=4)
    loading = _write_clipped_dark_band_strip(tmp_path / "loading_clipped.png", [90, 90, 90], band_height=4)

    result = measure_densitometry(
        _panel_data(target, loading),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )

    assert all("saturated" not in lane["qc_flags"] for measurement in result["measurements"] for lane in measurement["lanes"])


def test_density_qc_thresholds_are_deliberately_conservative(tmp_path) -> None:
    target = _write_clipped_dark_band_strip(tmp_path / "target_conservative.png", [0, 0, 0], band_height=15)
    loading = _write_clipped_dark_band_strip(tmp_path / "loading_conservative.png", [230, 230, 230], band_height=15)

    result = measure_densitometry(
        _panel_data(target, loading),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )

    flags = [flag for measurement in result["measurements"] for lane in measurement["lanes"] for flag in lane["qc_flags"]]
    assert "saturated" not in flags
    assert "low_signal" not in flags


def test_densitometry_chart_reports_qc_without_drawing_exclamation_marks() -> None:
    figure = build_densitometry_figure(
        {
            "loading_control_strip_id": "strip_2",
            "measurements": [
                {
                    "strip_id": "strip_1",
                    "strip_label": "SPNS1",
                    "lanes": [
                        {"lane_label": "Control", "fold": 1.0, "qc_flags": ["saturated"]},
                        {"lane_label": "Drug", "fold": 1.4, "qc_flags": ["low_signal"]},
                    ],
                }
            ],
        }
    )

    assert "!" not in [text.get_text() for text in figure.axes[0].texts]


def test_wb_statistics_preview_uses_margins_that_keep_axis_labels_visible() -> None:
    figure = build_wb_statistics_figure(
        {
            "plot_data": [{"group": "1", "value": 1.0}, {"group": "2", "value": 1.3}],
            "comparisons": [{"group1": "1", "group2": "2", "label": "ns"}],
        }
    )

    assert figure.subplotpars.left >= 0.24
    assert figure.subplotpars.bottom >= 0.18
    assert figure.axes[0].yaxis.label.get_fontsize() <= 8


def test_wb_statistics_default_style_comes_from_nature_preset() -> None:
    figure = build_wb_statistics_figure(
        {
            "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.3}],
            "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        }
    )
    ax = figure.axes[0]
    scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

    assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert ax.spines["left"].get_linewidth() == 0.6


def test_group_statistics_figure_is_shared_and_accepts_metric_label() -> None:
    figure = build_group_statistics_figure(
        {
            "plot_data": [{"group": "Control", "value": 12.0}, {"group": "Drug", "value": 18.0}],
            "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
        },
        y_label="Mean intensity (a.u.)",
    )

    assert figure.axes[0].yaxis.label.get_text() == "Mean intensity (a.u.)"
    assert figure.subplotpars.left >= 0.24


def test_statistics_plot_style_priority_is_local_then_saved_then_preset_default() -> None:
    style = resolve_statistics_plot_style(
        "Nature",
        saved_style={"point_color": "#111111", "axis_line_width": 1.3},
        local_override={"axis_line_width": 2.0},
    )

    assert style["point_color"] == "#111111"
    assert style["axis_line_width"] == 2.0
    assert style["bar_edge_color"] == "#272727"


def test_densitometry_chart_accepts_nature_style_override() -> None:
    figure = build_densitometry_figure(
        {
            "loading_control_strip_id": "",
            "measurements": [
                {
                    "strip_id": "strip_1",
                    "strip_label": "SPNS1",
                    "lanes": [
                        {"lane_label": "Control", "fold": 1.0, "qc_flags": []},
                        {"lane_label": "Drug", "fold": 1.4, "qc_flags": []},
                    ],
                }
            ],
        },
        style={"point_color": "#B64342", "bar_edge_color": "#0F4D92", "axis_line_width": 1.1},
    )
    ax = figure.axes[0]
    scatter = next(collection for collection in ax.collections if isinstance(collection, PathCollection))

    assert to_hex(ax.patches[0].get_edgecolor()).upper() == "#0F4D92"
    assert to_hex(scatter.get_facecolors()[0]).upper() == "#B64342"
    assert ax.spines["left"].get_linewidth() == 1.1


def test_wb_runtime_saved_preset_changes_density_and_stats_visual_tokens_without_changing_results(tmp_path) -> None:
    densitometry_result = {
        "loading_control_strip_id": "",
        "measurements": [
            {
                "strip_id": "strip_1",
                "strip_label": "SPNS1",
                "lanes": [
                    {"lane_label": "Control", "fold": 1.0, "qc_flags": []},
                    {"lane_label": "Drug", "fold": 1.4, "qc_flags": []},
                ],
            }
        ],
    }
    statistics_result = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.4}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    density_snapshot = copy.deepcopy(densitometry_result)
    stats_snapshot = copy.deepcopy(statistics_result)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime WB",
            source_type="user_modified",
            plot_style={"point_color": "#CC5500", "bar_edge_color": "#0055CC", "axis_line_width": 1.6},
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
        density_figure = build_densitometry_figure(densitometry_result, preset_name="Nature")
        density_ax = density_figure.axes[0]
        density_scatter = next(collection for collection in density_ax.collections if isinstance(collection, PathCollection))
        stats_figure = build_wb_statistics_figure(
            statistics_result,
            style=resolve_statistics_plot_style("Nature"),
        )
        stats_ax = stats_figure.axes[0]
        stats_scatter = next(collection for collection in stats_ax.collections if isinstance(collection, PathCollection))

        assert to_hex(density_ax.patches[0].get_edgecolor()).upper() == "#0055CC"
        assert to_hex(density_scatter.get_facecolors()[0]).upper() == "#CC5500"
        assert density_ax.spines["left"].get_linewidth() == 1.6
        assert to_hex(stats_ax.patches[0].get_edgecolor()).upper() == "#0055CC"
        assert to_hex(stats_scatter.get_facecolors()[0]).upper() == "#CC5500"
        assert densitometry_result == density_snapshot
        assert statistics_result == stats_snapshot
    finally:
        clear_runtime_active_style_preset()


def test_wb_invalid_runtime_series_color_fails_closed_and_renderers_use_builtin(tmp_path) -> None:
    densitometry_result = {
        "loading_control_strip_id": "",
        "measurements": [
            {
                "strip_id": "strip_1",
                "strip_label": "SPNS1",
                "lanes": [
                    {"lane_label": "Control", "fold": 1.0, "qc_flags": []},
                    {"lane_label": "Drug", "fold": 1.4, "qc_flags": []},
                ],
            }
        ],
    }
    statistics_result = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.4}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Invalid WB runtime",
            source_type="user_modified",
            plot_style={"series_colors": ["#0055CC", "blue"], "bar_edge_color": "#0055CC"},
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
    original_text = registry.storage_path.read_text(encoding="utf-8")

    try:
        status = set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        density_figure = build_densitometry_figure(densitometry_result, preset_name="Nature")
        stats_figure = build_wb_statistics_figure(
            statistics_result,
            style=resolve_statistics_plot_style("Nature"),
        )

        assert status.ok is False
        assert "series_colors[1] must be a #RRGGBB color" in status.message
        assert last_runtime_style_preset_status().source == "builtin"
        assert "series_colors[1] must be a #RRGGBB color" in last_runtime_style_preset_status().message
        assert density_figure.axes
        assert stats_figure.axes
        assert registry.storage_path.read_text(encoding="utf-8") == original_text
    finally:
        clear_runtime_active_style_preset()


def test_wb_canvas_statistics_layout_renderer_uses_runtime_saved_preset_under_saved_plot_style(tmp_path, monkeypatch) -> None:
    captured_styles: list[dict] = []

    def fake_build_wb_statistics_figure(result: dict, style: dict | None = None, **_kwargs) -> Figure:
        captured_styles.append(dict(style or {}))
        return Figure(figsize=(2, 1.5), dpi=100)

    monkeypatch.setattr("ui.widgets.wb_canvas.build_wb_statistics_figure", fake_build_wb_statistics_figure)
    registry = StylePresetRegistry(tmp_path / "style_presets.json")
    saved = registry.save_preset(
        StylePresetRecord(
            display_name="Runtime WB canvas",
            source_type="user_modified",
            plot_style={"point_color": "#CC5500", "bar_edge_color": "#0055CC", "axis_line_width": 1.6},
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
    panel = WesternBlotPanel(panel_id="WBRuntimeCanvas")
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.4}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    panel.data["wb_layout"] = {
        "enabled": True,
        "stats_plot": {
            "enabled": True,
            "style": {"point_color": "#111111"},
        },
    }
    app = QApplication.instance() or QApplication([])
    canvas = WBCanvas()

    try:
        set_runtime_active_style_preset(saved.preset_id, registry.storage_path)
        canvas.set_panel(panel)

        assert captured_styles
        assert captured_styles[-1]["bar_edge_color"] == "#0055CC"
        assert captured_styles[-1]["point_color"] == "#111111"
        assert captured_styles[-1]["axis_line_width"] == 1.6
        assert "runtime_style_preset" not in str(panel.data)
    finally:
        clear_runtime_active_style_preset()
        canvas.close()
        app.processEvents()


def test_wb_density_preview_uses_active_panel_preset_without_changing_densitometry_values(tmp_path, monkeypatch) -> None:
    def patched_preset(name: str = "Nature", path=None) -> dict:
        preset = copy.deepcopy(style_presets.get_journal_preset(name, path))
        if str(name).casefold() != "cell":
            return preset
        preset.setdefault("palette", {})
        preset["palette"].update(
            {
                "blue_main": "#7B1FA2",
                "neutral_black": "#4E342E",
                "green_3": "#AB47BC",
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

    import core.plotting_densitometry as plotting_densitometry

    monkeypatch.setattr(plotting_densitometry, "get_journal_preset", patched_preset)

    target = _write_lane_strip(tmp_path / "target.png", [120, 220, 180])
    loading = _write_lane_strip(tmp_path / "loading.png", [160, 160, 160])
    result = measure_densitometry(
        _panel_data(target, loading),
        loading_control_strip_id="strip_2",
        reference_lane_index=0,
        background_method="none",
        polarity_mode="dark_on_light",
    )
    result_snapshot = copy.deepcopy(result)

    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()

    panel.style["preset"] = "Nature"
    window._refresh_wb_density_chart(result)
    nature_chart = window.data_panel.wb_density_chart_layout.itemAt(0).widget()
    nature_ax = nature_chart.figure.axes[0]
    nature_scatter = next(collection for collection in nature_ax.collections if isinstance(collection, PathCollection))

    panel.style["preset"] = "Cell"
    window._refresh_wb_density_chart(result)
    cell_chart = window.data_panel.wb_density_chart_layout.itemAt(0).widget()
    cell_ax = cell_chart.figure.axes[0]
    cell_scatter = next(collection for collection in cell_ax.collections if isinstance(collection, PathCollection))

    assert to_hex(nature_ax.patches[0].get_edgecolor()).upper() == "#272727"
    assert to_hex(nature_scatter.get_facecolors()[0]).upper() == "#0F4D92"
    assert to_hex(cell_ax.patches[0].get_edgecolor()).upper() == "#4E342E"
    assert to_hex(cell_scatter.get_facecolors()[0]).upper() == "#7B1FA2"
    assert result == result_snapshot
    assert result["measurements"][0]["lanes"][1]["fold"] == pytest.approx(result_snapshot["measurements"][0]["lanes"][1]["fold"])

    window.close()
    app.processEvents()
