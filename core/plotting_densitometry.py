"""Generate publication-quality bar charts from WB densitometry results."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
if TYPE_CHECKING:
    from matplotlib.figure import Figure

from core.plotting.statistics_plot import build_group_statistics_figure
from core.plotting.style_presets import apply_matplotlib_preset, get_journal_preset, plot_style_from_preset
from core.style_preset_resolver import runtime_plot_style_override


def _resolved_plot_style(preset_name: str | None = None, style: dict | None = None) -> dict:
    try:
        preset = get_journal_preset(preset_name or "Nature")
    except KeyError:
        preset = get_journal_preset("Nature")
    resolved = plot_style_from_preset(preset)
    resolved.update(runtime_plot_style_override())
    resolved.update(dict(style or {}))
    return resolved


def build_densitometry_figure(
    densitometry_result: dict,
    exclude_loading_control: bool = True,
    preset_name: str | None = None,
    style: dict | None = None,
) -> Figure:
    """Build a bar chart from WB densitometry fold-change values."""
    from matplotlib import rc_context
    from matplotlib.figure import Figure

    with rc_context():
        try:
            apply_matplotlib_preset(get_journal_preset(preset_name or "Nature"))
        except KeyError:
            apply_matplotlib_preset(get_journal_preset("Nature"))
        style = _resolved_plot_style(preset_name, style)
        series_colors = list(style.get("series_colors", [])) or [str(style.get("point_color", "#0F4D92"))]
        measurements = list(densitometry_result.get("measurements", []))
        if exclude_loading_control:
            loading_id = str(densitometry_result.get("loading_control_strip_id", ""))
            measurements = [item for item in measurements if str(item.get("strip_id", "")) != loading_id]
        if not measurements:
            measurements = list(densitometry_result.get("measurements", []))
        if not measurements:
            raise ValueError("No densitometry measurements available for plotting.")

        lane_labels = [str(lane.get("lane_label", f"Lane {index + 1}")) for index, lane in enumerate(measurements[0].get("lanes", []))]
        x = np.arange(len(lane_labels), dtype=float)
        width = min(0.72 / max(len(measurements), 1), 0.58)

        fig = Figure(figsize=(3.5, 2.8), dpi=150, facecolor="white", constrained_layout=True)
        ax = fig.add_subplot(111)
        ymax = 1.0
        for strip_index, measurement in enumerate(measurements):
            offset = (strip_index - (len(measurements) - 1) / 2) * width
            values = []
            qc_flags = []
            for lane in measurement.get("lanes", []):
                fold = lane.get("fold")
                values.append(float(fold) if isinstance(fold, (int, float)) and not math.isnan(float(fold)) else np.nan)
                qc_flags.append(list(lane.get("qc_flags", [])))
            finite_values = [value for value in values if not math.isnan(value)]
            if finite_values:
                ymax = max(ymax, max(finite_values))
            ax.bar(
                x + offset,
                values,
                width=width,
                label=str(measurement.get("strip_label", "")),
                color=str(style.get("bar_fill_color", "#FFFFFF")),
                edgecolor=str(style.get("bar_edge_color") or series_colors[strip_index % len(series_colors)]),
                linewidth=float(style.get("bar_line_width", 0.9)),
                zorder=2,
            )
            ax.scatter(
                x + offset,
                values,
                s=float(style.get("point_size", 16.0)),
                color=str(style.get("point_color") or series_colors[strip_index % len(series_colors)]),
                edgecolor=str(style.get("point_edge_color", "#272727")),
                linewidth=float(style.get("point_line_width", 0.35)),
                zorder=3,
            )
        ax.set_xticks(x)
        ax.set_xticklabels(lane_labels, fontsize=float(style.get("tick_label_size_pt", 7.0)), fontfamily=str(style.get("font_family", "Arial")))
        ax.set_ylabel("Relative expression (fold change)", fontsize=float(style.get("axis_label_size_pt", 8.0)), fontfamily=str(style.get("font_family", "Arial")))
        axis_line_width = float(style.get("axis_line_width", 0.6))
        ax.tick_params(axis="y", labelsize=float(style.get("tick_label_size_pt", 7.0)), width=float(style.get("tick_width", axis_line_width)), length=float(style.get("tick_length", 3.0)))
        ax.tick_params(axis="x", width=float(style.get("tick_width", axis_line_width)), length=0)
        ax.spines["top"].set_visible(bool(style.get("show_top_spine", False)))
        ax.spines["right"].set_visible(bool(style.get("show_right_spine", False)))
        ax.spines["left"].set_linewidth(axis_line_width)
        ax.spines["bottom"].set_linewidth(axis_line_width)
        ax.set_ylim(bottom=0, top=max(ymax * 1.25, 1.2))
        if len(measurements) > 1:
            ax.legend(frameon=False, fontsize=float(style.get("legend_size_pt", 7.0)))
        return fig


def build_wb_statistics_figure(
    statistics_result: dict,
    style: dict | None = None,
    *,
    chart_kind: str | None = None,
) -> Figure:
    return build_group_statistics_figure(
        statistics_result,
        style=style,
        y_label="Relative expression (fold change)",
        chart_kind=chart_kind,
    )
