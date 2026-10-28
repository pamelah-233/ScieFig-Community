from __future__ import annotations

import copy
import inspect
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from matplotlib import cbook, rcParams
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from core.plotting.chart_kinds import (
    DEFAULT_DISTRIBUTION_CHART_KIND,
    DEFAULT_GROUPED_CHART_KIND,
    DISTRIBUTION_CHART_KINDS,
    FAMILY_GROUPED,
    FAMILY_OBJECT_DISTRIBUTION,
    FAMILY_QPCR,
    GROUPED_CHART_KINDS,
    PLOT_PAYLOAD_FIELDS,
    PLOT_PAYLOAD_SCHEMA_VERSION,
    REASON_FREQUENCY_NOT_GROUPED,
    REASON_NO_VALUES,
    REASON_SUMMARY_ONLY,
    REASON_VIOLIN_DEGENERATE,
    build_plot_payload_figure,
    chart_kind_options,
    chart_kind_unavailable_reason,
    copy_plot_payload,
    draw_grouped_chart,
    normalize_chart_kind,
    validate_plot_payload,
)
from core.plotting.qpcr_plot import build_qpcr_figure
from core.plotting.statistics_plot import (
    build_distribution_comparison_figure,
    build_group_statistics_figure,
)
from core.plotting_densitometry import build_wb_statistics_figure


def _style() -> dict:
    return {
        "bar_width_fraction": 0.58,
        "bar_fill_color": "#FFFFFF",
        "bar_edge_color": "#272727",
        "bar_line_width": 0.9,
        "sem_line_width": 0.8,
        "sem_cap_size": 3.0,
        "sem_cap_thick": 0.8,
        "jitter_fraction": 0.08,
        "point_size": 16.0,
        "point_shape": "circle",
        "point_color": "#0F4D92",
        "point_edge_color": "#272727",
        "point_line_width": 0.35,
        "series_colors": ["#0F4D92", "#8BCF8B"],
        "line_width": 1.2,
        "font_size_pt": 7.0,
        "axis_label_size_pt": 8.0,
        "tick_label_size_pt": 7.0,
        "font_family": "Arial",
        "axis_line_width": 0.6,
        "bracket_step_fraction": 0.16,
        "bracket_height_fraction": 0.18,
        "bracket_label_offset_fraction": 0.22,
        "bracket_line_width": 0.7,
        "show_comparison_labels": True,
        "show_x_labels": True,
        "show_y_label": True,
        "show_top_spine": False,
        "show_right_spine": False,
    }


def _raw_groups() -> dict[str, list[float]]:
    return {
        "Control": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 30.0],
        "Drug": [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 11.0, 12.0],
    }


def _summaries_from_raw(raw: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for group, values in raw.items():
        array = np.asarray(values, dtype=float)
        n = len(array)
        mean = float(np.mean(array))
        error = float(np.std(array, ddof=1) / np.sqrt(n)) if n > 1 else 0.0
        out[group] = {"mean": mean, "error": error}
    return out


def _draw(kind: str, raw=None, summaries=None, group_order=None):
    raw = _raw_groups() if raw is None else raw
    summaries = _summaries_from_raw(raw) if summaries is None else summaries
    fig = Figure(figsize=(3.4, 2.6), dpi=180)
    ax = fig.add_subplot(111)
    result = draw_grouped_chart(
        ax,
        raw,
        summaries,
        chart_kind=kind,
        style=_style(),
        group_order=group_order,
    )
    return fig, ax, result


def _png_svg_ok(figure: Figure) -> None:
    png = io.BytesIO()
    svg = io.BytesIO()
    figure.savefig(png, format="png")
    figure.savefig(svg, format="svg")
    assert png.getvalue().startswith(b"\x89PNG")
    assert b"<svg" in svg.getvalue().lower()


def test_normalize_chart_kind_defaults_and_aliases() -> None:
    assert PLOT_PAYLOAD_SCHEMA_VERSION == 1
    assert DEFAULT_GROUPED_CHART_KIND == "bar_dot"
    assert DEFAULT_DISTRIBUTION_CHART_KIND == "frequency_bar"
    assert normalize_chart_kind(None) == "bar_dot"
    assert normalize_chart_kind(" ") == "bar_dot"
    assert normalize_chart_kind("Bar-Dot") == "bar_dot"
    assert normalize_chart_kind("box+dot") == "box_dot"
    assert normalize_chart_kind("FREQUENCY-LINE", default="frequency_bar") == "frequency_line"
    assert normalize_chart_kind("not-a-kind") == "bar_dot"
    assert normalize_chart_kind("not-a-kind", default="frequency_bar") == "frequency_bar"


def test_chart_kind_options_are_stable_and_family_specific() -> None:
    assert chart_kind_options() == GROUPED_CHART_KINDS
    assert chart_kind_options("grouped") == (
        "bar",
        "bar_dot",
        "box",
        "box_dot",
        "violin",
        "violin_dot",
        "dot",
        "line",
    )
    assert chart_kind_options("qpcr") == GROUPED_CHART_KINDS
    assert chart_kind_options("object_distribution") == DISTRIBUTION_CHART_KINDS
    assert chart_kind_options("distribution") == (
        "frequency_bar",
        "frequency_line",
        "box",
        "box_dot",
        "violin",
        "violin_dot",
        "dot",
    )
    assert "box_dot" in DISTRIBUTION_CHART_KINDS
    assert "violin_dot" in DISTRIBUTION_CHART_KINDS
    with pytest.raises(ValueError):
        chart_kind_options("line_profile")


def test_unavailable_reason_summary_only_and_violin_degenerate() -> None:
    raw = _raw_groups()
    assert chart_kind_unavailable_reason("bar_dot", raw) is None
    assert chart_kind_unavailable_reason("bar", {}, has_raw=False) is None
    assert chart_kind_unavailable_reason("line", {}, has_raw=False) is None
    assert chart_kind_unavailable_reason("box", raw, has_raw=False) == REASON_SUMMARY_ONLY
    assert chart_kind_unavailable_reason("violin", {}, has_raw=True) == REASON_SUMMARY_ONLY
    assert chart_kind_unavailable_reason("frequency_bar", raw) == REASON_FREQUENCY_NOT_GROUPED
    constant = {"A": [3.0, 3.0, 3.0], "B": [1.0, 2.0, 3.0]}
    assert chart_kind_unavailable_reason("violin", constant) == REASON_VIOLIN_DEGENERATE
    short = {"A": [1.0], "B": [2.0, 3.0]}
    assert chart_kind_unavailable_reason("violin_dot", short) == REASON_VIOLIN_DEGENERATE
    assert chart_kind_unavailable_reason("bar", {}, has_raw=True) == REASON_NO_VALUES


def test_draw_grouped_chart_default_bar_dot_means_errors_jitter() -> None:
    raw = {"Control": [1.0, 1.2, 1.1], "Drug": [2.0, 2.4, 1.6]}
    summaries = {"Control": {"mean": 1.5, "error": 0.25}, "Drug": {"mean": 2.5, "error": 0.4}}
    fig, ax, result = _draw("bar_dot", raw=raw, summaries=summaries)
    bars = result["artists"]["bars"]
    heights = [float(patch.get_height()) for patch in bars.patches]
    assert heights == pytest.approx([1.5, 2.5])
    scatter = result["artists"]["scatter"]
    offsets = np.asarray(scatter.get_offsets())
    assert sorted(offsets[:, 1].tolist()) == pytest.approx(sorted([1.0, 1.2, 1.1, 2.0, 2.4, 1.6]))
    rng = np.random.default_rng(42)
    expected_x = []
    for index, group in enumerate(["Control", "Drug"]):
        jitter = rng.uniform(-0.08, 0.08, size=len(raw[group]))
        expected_x.extend((index + jitter).tolist())
    assert offsets[:, 0].tolist() == pytest.approx(expected_x)
    assert result["data_extent"]["ymin"] == pytest.approx(min(1.5 - 0.25, 2.5 - 0.4, 1.0, 1.2, 1.1, 2.0, 2.4, 1.6))
    assert result["data_extent"]["ymax"] == pytest.approx(max(1.5 + 0.25, 2.5 + 0.4, 2.4, 1.0))
    assert result["positions"] == {"Control": 0.0, "Drug": 1.0}
    _png_svg_ok(fig)


def test_draw_grouped_chart_does_not_mutate_inputs() -> None:
    raw = _raw_groups()
    summaries = _summaries_from_raw(raw)
    style = _style()
    order = ["Control", "Drug"]
    raw_snap, sum_snap, style_snap, order_snap = (
        copy.deepcopy(raw),
        copy.deepcopy(summaries),
        copy.deepcopy(style),
        list(order),
    )
    _draw("box_dot", raw=raw, summaries=summaries, group_order=order)
    assert raw == raw_snap
    assert summaries == sum_snap
    assert style == style_snap
    assert order == order_snap


def test_draw_grouped_chart_does_not_change_rcparams() -> None:
    snapshot = {key: rcParams[key] for key in ("svg.fonttype", "pdf.fonttype", "font.size", "axes.linewidth")}
    _draw("violin_dot")
    assert {key: rcParams[key] for key in snapshot} == snapshot


def test_draw_grouped_chart_box_quartiles_1_5_iqr() -> None:
    raw = {"Control": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 30.0]}
    _fig, _ax, result = _draw("box", raw=raw, summaries=_summaries_from_raw(raw), group_order=["Control"])
    expected = cbook.boxplot_stats([raw["Control"]], whis=1.5)[0]
    bp = result["artists"]["boxplot"]
    median = bp["medians"][0].get_ydata()
    assert median[0] == pytest.approx(expected["med"])
    fliers = np.asarray(bp["fliers"][0].get_ydata(), dtype=float)
    assert sorted(fliers.tolist()) == pytest.approx(sorted(np.asarray(expected["fliers"], dtype=float).tolist()))
    assert result["artists"]["scatter"] is None
    low = bp["whiskers"][0].get_ydata()
    high = bp["whiskers"][1].get_ydata()
    assert min(low) == pytest.approx(expected["whislo"]) or max(low) == pytest.approx(expected["whislo"])
    assert max(high) == pytest.approx(expected["whishi"]) or min(high) == pytest.approx(expected["whishi"])


def test_draw_grouped_chart_box_dot_raw_points_without_duplicate_fliers() -> None:
    raw = _raw_groups()
    _fig, _ax, result = _draw("box_dot", raw=raw)
    bp = result["artists"]["boxplot"]
    fliers = [float(value) for item in bp["fliers"] for value in np.asarray(item.get_ydata(), dtype=float)]
    assert fliers == []
    scatter = result["artists"]["scatter"]
    ys = np.asarray(scatter.get_offsets())[:, 1]
    expected = [value for values in raw.values() for value in values]
    assert sorted(ys.tolist()) == pytest.approx(sorted(expected))
    assert len(ys) == len(expected)


def test_draw_grouped_chart_violin_rejects_constant_and_summary_only() -> None:
    with pytest.raises(ValueError, match=REASON_SUMMARY_ONLY):
        _draw("violin", raw={}, summaries={"A": {"mean": 1.0, "error": 0.1}})
    with pytest.raises(ValueError, match=REASON_VIOLIN_DEGENERATE):
        _draw("violin", raw={"A": [2.0, 2.0, 2.0]}, summaries={"A": {"mean": 2.0, "error": 0.0}})
    fig, _ax, result = _draw("violin")
    assert result["artists"]["violin"] is not None
    assert result["artists"]["scatter"] is None
    _png_svg_ok(fig)


def test_draw_grouped_chart_violin_validates_only_selected_groups() -> None:
    raw = {"good": [1.0, 2.0], "bad": [3.0, 3.0]}
    summaries = _summaries_from_raw(raw)

    _fig, _ax, result = _draw("violin", raw=raw, summaries=summaries, group_order=["good"])

    assert result["group_order"] == ["good"]
    assert result["positions"] == {"good": 0.0}
    with pytest.raises(ValueError, match=REASON_VIOLIN_DEGENERATE):
        _draw("violin", raw=raw, summaries=summaries, group_order=["bad"])


def test_bar_allows_summary_only_and_line_uses_ordered_summaries() -> None:
    summaries = {"B": {"mean": 3.0, "error": 0.2}, "A": {"mean": 1.0, "error": 0.1}}
    _fig, _ax, bar_result = _draw("bar", raw={}, summaries=summaries, group_order=["A", "B"])
    heights = [float(patch.get_height()) for patch in bar_result["artists"]["bars"].patches]
    assert heights == pytest.approx([1.0, 3.0])
    assert bar_result["artists"]["scatter"] is None
    _fig2, _ax2, line_result = _draw("line", raw={}, summaries=summaries, group_order=["A", "B"])
    assert line_result["group_order"] == ["A", "B"]
    assert line_result["artists"]["line"] is not None
    assert line_result["artists"]["scatter"] is None


def test_all_grouped_geometries_render_png_and_svg() -> None:
    for kind in GROUPED_CHART_KINDS:
        fig, _ax, result = _draw(kind)
        assert result["chart_kind"] == kind
        assert set(result["positions"]) == {"Control", "Drug"}
        _png_svg_ok(fig)


def test_build_group_statistics_figure_default_bar_dot_limits_fonts_brackets() -> None:
    payload = {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Control", "value": 1.2},
            {"group": "Drug", "value": 2.0},
            {"group": "Drug", "value": 2.4},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
    }
    snap = copy.deepcopy(payload)
    default_fig = build_group_statistics_figure(payload, style=_style(), y_label="Mean intensity (a.u.)")
    explicit = build_group_statistics_figure(
        payload, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="bar_dot"
    )
    assert payload == snap
    ax = default_fig.axes[0]
    assert [float(patch.get_height()) for patch in ax.patches[:2]] == pytest.approx([1.1, 2.2])
    assert ax.get_ylim()[0] == 0.0
    assert ax.get_ylabel() == "Mean intensity (a.u.)"
    assert "*" in [text.get_text() for text in ax.texts]
    assert ax.yaxis.label.get_fontsize() == 8.0
    assert default_fig.dpi == 180
    assert default_fig.get_size_inches().tolist() == [3.4, 2.6]
    assert default_fig.subplotpars.left >= 0.24
    assert explicit.axes[0].get_ylim() == ax.get_ylim()
    _png_svg_ok(default_fig)
    box_fig = build_group_statistics_figure(payload, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="box")
    _png_svg_ok(box_fig)


def test_qpcr_default_uses_supplied_summary_and_fold_change_dots() -> None:
    frame = pd.DataFrame(
        [
            {"target": "GENE", "group": "Ctrl", "fold_change": 0.9, "mean_fold_change": 1.25, "error": 0.2},
            {"target": "GENE", "group": "Ctrl", "fold_change": 1.1, "mean_fold_change": 1.25, "error": 0.2},
            {"target": "GENE", "group": "KD", "fold_change": 0.4, "mean_fold_change": 0.55, "error": 0.1},
            {"target": "GENE", "group": "KD", "fold_change": 0.6, "mean_fold_change": 0.55, "error": 0.1},
        ]
    )
    statistics = {
        "stat_performed_on": "delta_ct",
        "targets": {"GENE": {"comparisons": [{"group1": "Ctrl", "group2": "KD", "label": "***"}]}},
    }
    original = frame.copy(deep=True)
    stats_snap = copy.deepcopy(statistics)
    figure = build_qpcr_figure(frame, statistics, style=_style())
    ax = figure.axes[0]
    assert [float(patch.get_height()) for patch in ax.patches[:2]] == pytest.approx([1.25, 0.55])
    scatter = next(col for col in ax.collections if col.get_offsets().shape[0] == 4)
    ys = sorted(float(y) for y in np.asarray(scatter.get_offsets())[:, 1])
    assert ys == pytest.approx([0.4, 0.6, 0.9, 1.1])
    assert "***" in [text.get_text() for text in ax.texts]
    assert frame.equals(original)
    assert statistics == stats_snap
    box = build_qpcr_figure(frame, statistics, style=_style(), chart_kind="box")
    box_ax = box.axes[0]
    assert "***" in [text.get_text() for text in box_ax.texts]
    box_ys = []
    for artist in box_ax.lines:
        data = artist.get_ydata()
        box_ys.extend(float(v) for v in data)
    assert max(box_ys) <= 1.1 + 1e-9 or 1.1 in set(np.round(box_ys, 6))
    _png_svg_ok(figure)
    _png_svg_ok(box)


def test_qpcr_figure_uses_supplied_error_extent_without_clipping() -> None:
    frame = pd.DataFrame(
        [
            {"target": "GENE", "group": "Ctrl", "fold_change": 0.9, "mean_fold_change": 1.0, "error": 3.0},
            {"target": "GENE", "group": "Ctrl", "fold_change": 1.1, "mean_fold_change": 1.0, "error": 3.0},
            {"target": "GENE", "group": "KD", "fold_change": -0.4, "mean_fold_change": -0.2, "error": 1.5},
            {"target": "GENE", "group": "KD", "fold_change": 0.1, "mean_fold_change": -0.2, "error": 1.5},
        ]
    )
    statistics = {
        "stat_performed_on": "delta_ct",
        "targets": {"GENE": {"comparisons": [{"group1": "Ctrl", "group2": "KD", "label": "***"}]}},
    }
    original = frame.copy(deep=True)
    stats_snap = copy.deepcopy(statistics)
    fig = build_qpcr_figure(frame, statistics, style=_style(), chart_kind="bar")
    ax = fig.axes[0]
    low, high = ax.get_ylim()
    assert low <= -1.7 + 1e-12
    assert high >= 4.0 - 1e-12
    assert [float(patch.get_height()) for patch in ax.patches[:2]] == pytest.approx([1.0, -0.2])
    assert "***" in [text.get_text() for text in ax.texts]
    assert frame.equals(original)
    assert statistics == stats_snap
    _png_svg_ok(fig)


def test_distribution_frequency_preserved_and_descriptive_uses_series_values() -> None:
    distribution = {
        "series": [
            {"lane_label": "Image A", "values": [10.0, 20.0, 30.0, 40.0, 80.0]},
            {"lane_label": "Image B", "values": [12.0, 18.0, 22.0, 28.0, 35.0]},
        ]
    }
    snap = copy.deepcopy(distribution)
    freq = build_distribution_comparison_figure(
        distribution,
        plot_kind="bar",
        x_label="Object area (µm²)",
        frequency_mode="percent",
    )
    assert distribution == snap
    assert freq.axes[0].yaxis.label.get_text() == "Percent of objects"
    assert sum(float(patch.get_height()) for patch in freq.axes[0].patches) == pytest.approx(200.0)
    line = build_distribution_comparison_figure(distribution, plot_kind="line", x_label="Object area (µm²)")
    assert line.axes[0].yaxis.label.get_text() == "Frequency"
    box = build_distribution_comparison_figure(
        distribution,
        chart_kind="box",
        x_label="Object area (µm²)",
    )
    assert distribution == snap
    ax = box.axes[0]
    labels = [tick.get_text() for tick in ax.get_xticklabels()]
    assert labels == ["Image A", "Image B"]
    assert ax.get_ylabel() == "Object area (µm²)"
    assert not any("p=" in text.get_text().lower() or text.get_text() in {"*", "**", "***"} for text in ax.texts)
    expected = cbook.boxplot_stats([distribution["series"][0]["values"]], whis=1.5)[0]
    fliers = []
    for collection in ax.collections:
        offsets = np.asarray(collection.get_offsets())
        if offsets.size:
            fliers.extend(offsets[:, 1].tolist())
    assert expected["fliers"] is not None
    _png_svg_ok(freq)
    _png_svg_ok(box)
    violin = build_distribution_comparison_figure(distribution, chart_kind="violin", x_label="Object area (µm²)")
    dot = build_distribution_comparison_figure(distribution, chart_kind="dot", x_label="Object area (µm²)")
    _png_svg_ok(violin)
    _png_svg_ok(dot)
    scatter = next(col for col in dot.axes[0].collections if np.asarray(col.get_offsets()).shape[0] == 10)
    assert len(np.asarray(scatter.get_offsets())) == 10


def _all_finite_values(raw: dict[str, list[float]]) -> list[float]:
    return [float(value) for values in raw.values() for value in values]


def _assert_ylim_does_not_clip(ax, values: list[float]) -> None:
    data_min = float(min(values))
    data_max = float(max(values))
    low, high = ax.get_ylim()
    assert low <= data_min + 1e-12
    assert high >= data_max - 1e-12


def _assert_ylim_tracks_raw_span(ax, values: list[float]) -> None:
    data_min = float(min(values))
    data_max = float(max(values))
    span = data_max - data_min
    if span <= 0.0:
        span = max(abs(data_max), abs(data_min), 1e-12)
    low, high = ax.get_ylim()
    _assert_ylim_does_not_clip(ax, values)
    assert low >= data_min - span - 1e-12
    assert high <= data_max + span + 1e-12


def test_draw_grouped_chart_negative_values_are_in_extent() -> None:
    raw = {"Control": [-2.0, -1.0, 0.5, 1.0], "Drug": [-3.0, -0.5, 0.2, 0.4]}
    summaries = {
        "Control": {"mean": -0.375, "error": 0.7},
        "Drug": {"mean": -0.725, "error": 0.8},
    }
    values = _all_finite_values(raw)
    for kind in ("box", "violin", "dot", "bar_dot"):
        _fig, _ax, result = _draw(kind, raw=raw, summaries=summaries)
        assert result["data_extent"]["ymin"] <= min(values)
        assert result["data_extent"]["ymax"] >= max(values)
        if kind == "bar_dot":
            assert result["data_extent"]["ymin"] <= -0.375 - 0.7
            assert result["data_extent"]["ymax"] >= -0.725 + 0.8


def test_draw_grouped_chart_large_supplied_error_in_extent() -> None:
    raw = {"A": [0.9, 1.0, 1.1]}
    summaries = {"A": {"mean": 1.0, "error": 5.0}}
    for kind in ("bar", "bar_dot", "line"):
        _fig, _ax, result = _draw(kind, raw=raw, summaries=summaries, group_order=["A"])
        assert result["data_extent"]["ymin"] == pytest.approx(-4.0)
        assert result["data_extent"]["ymax"] == pytest.approx(6.0)


def test_negative_and_low_magnitude_distribution_figure_limits_do_not_clip() -> None:
    negative = {
        "plot_data": [
            {"group": "Control", "value": -2.0},
            {"group": "Control", "value": -1.0},
            {"group": "Control", "value": 0.4},
            {"group": "Drug", "value": -3.0},
            {"group": "Drug", "value": -0.2},
            {"group": "Drug", "value": 0.5},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
    }
    tiny = {
        "plot_data": [
            {"group": "Control", "value": 0.0010},
            {"group": "Control", "value": 0.0011},
            {"group": "Control", "value": 0.0012},
            {"group": "Drug", "value": 0.0013},
            {"group": "Drug", "value": 0.00125},
            {"group": "Drug", "value": 0.0014},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    clustered = {
        "plot_data": [
            {"group": "Control", "value": 100.00},
            {"group": "Control", "value": 100.05},
            {"group": "Control", "value": 100.08},
            {"group": "Drug", "value": 100.10},
            {"group": "Drug", "value": 100.12},
            {"group": "Drug", "value": 100.15},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    large_error = {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Control", "value": 9.0},
            {"group": "Drug", "value": -2.0},
            {"group": "Drug", "value": 4.0},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
    }
    neg_fig = build_group_statistics_figure(negative, style=_style(), y_label="Background-corrected (a.u.)", chart_kind="box")
    neg_values = [row["value"] for row in negative["plot_data"]]
    _assert_ylim_tracks_raw_span(neg_fig.axes[0], neg_values)
    assert neg_fig.axes[0].get_ylim()[0] < 0.0
    assert "*" in [text.get_text() for text in neg_fig.axes[0].texts]
    tiny_box = build_group_statistics_figure(tiny, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="box")
    tiny_violin = build_group_statistics_figure(tiny, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="violin")
    tiny_values = [row["value"] for row in tiny["plot_data"]]
    _assert_ylim_tracks_raw_span(tiny_box.axes[0], tiny_values)
    _assert_ylim_does_not_clip(tiny_violin.axes[0], tiny_values)
    assert tiny_violin.axes[0].get_ylim()[1] < 0.05
    clustered_fig = build_group_statistics_figure(clustered, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="dot")
    _assert_ylim_tracks_raw_span(clustered_fig.axes[0], [row["value"] for row in clustered["plot_data"]])
    assert clustered_fig.axes[0].get_ylim()[0] > 50.0
    error_fig = build_group_statistics_figure(large_error, style=_style(), y_label="Mean intensity (a.u.)", chart_kind="bar")
    means = [5.0, 1.0]
    sems = [
        float(np.std([1.0, 9.0], ddof=1) / np.sqrt(2)),
        float(np.std([-2.0, 4.0], ddof=1) / np.sqrt(2)),
    ]
    low = min(mean - sem for mean, sem in zip(means, sems))
    high = max(mean + sem for mean, sem in zip(means, sems))
    ylim = error_fig.axes[0].get_ylim()
    assert ylim[0] <= low + 1e-12
    assert ylim[1] >= high - 1e-12
    _png_svg_ok(neg_fig)
    _png_svg_ok(tiny_box)
    _png_svg_ok(tiny_violin)
    _png_svg_ok(clustered_fig)
    _png_svg_ok(error_fig)


def test_positive_default_bar_dot_zero_baseline_regression() -> None:
    payload = {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Control", "value": 1.2},
            {"group": "Drug", "value": 2.0},
            {"group": "Drug", "value": 2.4},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "*"}],
    }
    snap = copy.deepcopy(payload)
    fig = build_group_statistics_figure(payload, style=_style(), y_label="Mean intensity (a.u.)")
    assert payload == snap
    ax = fig.axes[0]
    assert [float(patch.get_height()) for patch in ax.patches[:2]] == pytest.approx([1.1, 2.2])
    assert ax.get_ylim()[0] == 0.0
    assert ax.get_ylim()[1] >= 2.2
    scatter = next(col for col in ax.collections if np.asarray(col.get_offsets()).shape[0] == 4)
    assert sorted(float(y) for y in np.asarray(scatter.get_offsets())[:, 1]) == pytest.approx([1.0, 1.2, 2.0, 2.4])
    assert "*" in [text.get_text() for text in ax.texts]


def test_object_distribution_box_dot_fliers_once_and_violin_dot() -> None:
    distribution = {
        "series": [
            {"lane_label": "Image A", "values": [10.0, 20.0, 30.0, 40.0, 80.0]},
            {"lane_label": "Image B", "values": [12.0, 18.0, 22.0, 28.0, 35.0]},
        ]
    }
    snap = copy.deepcopy(distribution)
    box_dot = build_distribution_comparison_figure(
        distribution,
        chart_kind="box_dot",
        x_label="Object area (µm²)",
    )
    assert distribution == snap
    ax = box_dot.axes[0]
    assert [tick.get_text() for tick in ax.get_xticklabels()] == ["Image A", "Image B"]
    assert ax.get_ylabel() == "Object area (µm²)"
    assert not any("p=" in text.get_text().lower() or text.get_text() in {"*", "**", "***"} for text in ax.texts)
    expected_a = cbook.boxplot_stats([distribution["series"][0]["values"]], whis=1.5)[0]
    scatter = next(col for col in ax.collections if np.asarray(col.get_offsets()).shape[0] == 10)
    ys = np.asarray(scatter.get_offsets())[:, 1]
    expected = [value for row in distribution["series"] for value in row["values"]]
    assert sorted(ys.tolist()) == pytest.approx(sorted(expected))
    flier_ys = []
    for line in ax.lines:
        marker = line.get_marker()
        if marker not in {None, "None", "none", ""}:
            flier_ys.extend(float(value) for value in np.asarray(line.get_ydata(), dtype=float))
    assert flier_ys == []
    assert expected_a["fliers"] is not None
    assert 80.0 in set(np.round(ys, 6))
    violin_dot = build_distribution_comparison_figure(
        distribution,
        chart_kind="violin_dot",
        x_label="Object area (µm²)",
    )
    assert distribution == snap
    vax = violin_dot.axes[0]
    assert vax.get_ylabel() == "Object area (µm²)"
    assert [tick.get_text() for tick in vax.get_xticklabels()] == ["Image A", "Image B"]
    assert not any("p=" in text.get_text().lower() or text.get_text() in {"*", "**", "***"} for text in vax.texts)
    vscatter = next(col for col in vax.collections if np.asarray(col.get_offsets()).shape[0] == 10)
    assert len(np.asarray(vscatter.get_offsets())) == 10
    _png_svg_ok(box_dot)
    _png_svg_ok(violin_dot)


def test_object_distribution_payload_keeps_sample_unit_labels_and_data() -> None:
    payload = {
        "schema_version": 1,
        "family": FAMILY_OBJECT_DISTRIBUTION,
        "chart_kind": "box_dot",
        "style": {},
        "y_label": "Object area (µm²)",
        "unit": "um^2",
        "source_module": "brightfield",
        "sample_unit": "object",
        "x_label": "Object area (µm²)",
        "distribution_result": {
            "series": [
                {"lane_label": "Image A", "values": [1.0, 2.0, 3.0, 4.0, 10.0]},
                {"lane_label": "Image B", "values": [1.5, 2.5, 3.5, 4.5, 5.0]},
            ]
        },
    }
    snap = copy.deepcopy(payload)
    copied = validate_plot_payload(payload)
    assert copied["sample_unit"] == "object"
    assert copied["unit"] == "um^2"
    fig = build_plot_payload_figure(payload, chart_kind="violin_dot")
    assert payload == snap
    assert fig.axes[0].get_ylabel() == "Object area (µm²)"
    assert [tick.get_text() for tick in fig.axes[0].get_xticklabels()] == ["Image A", "Image B"]
    assert not any("p=" in text.get_text().lower() or text.get_text() in {"*", "**", "***"} for text in fig.axes[0].texts)
    scatter = next(col for col in fig.axes[0].collections if np.asarray(col.get_offsets()).shape[0] == 10)
    assert len(np.asarray(scatter.get_offsets())) == 10
    _png_svg_ok(fig)


def test_distribution_summary_only_rejected() -> None:
    with pytest.raises(ValueError):
        build_distribution_comparison_figure(
            {"series": [{"lane_label": "Image A", "mean": 10.0, "sem": 1.0}]},
            chart_kind="box",
        )
    with pytest.raises(ValueError):
        build_distribution_comparison_figure(
            {"series": [{"lane_label": "Image A", "values": [1.0, 1.0, 1.0]}]},
            chart_kind="violin",
        )


def test_payload_schema1_roundtrip_and_no_mutation() -> None:
    payload = {
        "schema_version": 1,
        "family": FAMILY_GROUPED,
        "chart_kind": "box",
        "style": {"point_color": "#0F4D92"},
        "y_label": "Relative expression (fold change)",
        "unit": "fold",
        "source_module": "wb",
        "sample_unit": "biological_replicate",
        "statistics_result": {
            "plot_data": [
                {"group": "Control", "value": 1.0},
                {"group": "Control", "value": 1.4},
                {"group": "Drug", "value": 2.0},
                {"group": "Drug", "value": 2.6},
            ],
            "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        },
        "image_array": [[1, 2], [3, 4]],
    }
    snap = copy.deepcopy(payload)
    copied = copy_plot_payload(payload)
    assert "image_array" not in copied
    assert set(copied).issubset(set(PLOT_PAYLOAD_FIELDS))
    figure = build_plot_payload_figure(payload, chart_kind="dot")
    assert payload == snap
    assert figure.axes
    _png_svg_ok(figure)
    qpcr_payload = {
        "schema_version": 1,
        "family": FAMILY_QPCR,
        "chart_kind": "bar_dot",
        "style": {},
        "y_label": "Relative mRNA expression",
        "unit": "fold",
        "source_module": "qpcr",
        "sample_unit": "biological_replicate",
        "target": "GENE",
        "error_mode": "sem",
        "fold_change_rows": [
            {"target": "GENE", "group": "Ctrl", "fold_change": 0.9, "mean_fold_change": 1.25, "error": 0.2},
            {"target": "GENE", "group": "Ctrl", "fold_change": 1.1, "mean_fold_change": 1.25, "error": 0.2},
            {"target": "GENE", "group": "KD", "fold_change": 0.4, "mean_fold_change": 0.55, "error": 0.1},
            {"target": "GENE", "group": "KD", "fold_change": 0.6, "mean_fold_change": 0.55, "error": 0.1},
        ],
        "statistics": {
            "stat_performed_on": "delta_ct",
            "targets": {"GENE": {"comparisons": [{"group1": "Ctrl", "group2": "KD", "label": "***"}]}},
        },
    }
    qpcr_fig = build_plot_payload_figure(qpcr_payload)
    assert [float(patch.get_height()) for patch in qpcr_fig.axes[0].patches[:2]] == pytest.approx([1.25, 0.55])
    dist_payload = {
        "schema_version": 1,
        "family": FAMILY_OBJECT_DISTRIBUTION,
        "chart_kind": "box",
        "style": {},
        "y_label": "Object area (µm²)",
        "unit": "um^2",
        "source_module": "brightfield",
        "sample_unit": "object",
        "x_label": "Object area (µm²)",
        "distribution_result": {
            "series": [{"lane_label": "Image A", "values": [1.0, 2.0, 3.0, 4.0, 10.0]}]
        },
    }
    dist_fig = build_plot_payload_figure(dist_payload)
    assert dist_fig.axes[0].get_ylabel() == "Object area (µm²)"
    assert [tick.get_text() for tick in dist_fig.axes[0].get_xticklabels()] == ["Image A"]


def test_payload_rejects_unknown_schema_or_family() -> None:
    with pytest.raises(ValueError):
        validate_plot_payload({"schema_version": 2, "family": "grouped"})
    with pytest.raises(ValueError):
        validate_plot_payload(
            {
                "schema_version": 1,
                "family": "overlay",
                "chart_kind": "bar_dot",
                "style": {},
                "y_label": "x",
                "unit": "",
                "source_module": "x",
                "sample_unit": "x",
            }
        )


def test_chart_kinds_module_has_no_ui_or_demo_imports() -> None:
    import core.plotting.chart_kinds as module

    source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
    assert "from ui" not in source
    assert "import ui" not in source
    assert "demos" not in source
    assert "chart_types" not in source
    assert "ocr" not in source.lower()


def test_wb_statistics_figure_passes_chart_kind() -> None:
    result = {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Control", "value": 1.5},
            {"group": "Drug", "value": 2.0},
            {"group": "Drug", "value": 2.5},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
    }
    figure = build_wb_statistics_figure(result, style=_style(), chart_kind="box")
    assert figure.axes[0].yaxis.label.get_text() == "Relative expression (fold change)"
    canvas = FigureCanvasAgg(figure)
    canvas.draw()
    _png_svg_ok(figure)


def test_json_payload_copy_is_independent() -> None:
    payload = {
        "schema_version": 1,
        "family": "grouped",
        "chart_kind": "bar_dot",
        "style": {},
        "y_label": "y",
        "unit": "",
        "source_module": "wb",
        "sample_unit": "image",
        "statistics_result": {"plot_data": [{"group": "A", "value": 1.0}], "comparisons": []},
    }
    copied = copy_plot_payload(payload)
    copied["statistics_result"]["plot_data"][0]["value"] = 99
    assert payload["statistics_result"]["plot_data"][0]["value"] == 1.0
    assert json.loads(json.dumps(copied))["family"] == "grouped"
