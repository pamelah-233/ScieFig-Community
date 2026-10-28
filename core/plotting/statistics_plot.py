from __future__ import annotations

from core.plotting.style_presets import apply_plot_axes_style

from collections.abc import Mapping
from threading import Lock
from typing import TYPE_CHECKING, Any

import numpy as np
if TYPE_CHECKING:
    from matplotlib.figure import Figure

from core.plotting.chart_kinds import (
    chart_kind_unavailable_reason,
    draw_grouped_chart,
    grouped_chart_y_layout,
    normalize_chart_kind,
)
from core.plotting.style_presets import get_journal_preset, plot_style_from_preset
from core.style_preset_resolver import runtime_plot_style_override


_EDITABLE_TEXT_SAVE_RC = {
    "svg.fonttype": "none",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
}
_FIGURE_CLASS_LOCK = Lock()


def _statistics_figure_class():
    existing = globals().get('StatisticsFigure')
    if existing is not None:
        return existing
    with _FIGURE_CLASS_LOCK:
        existing = globals().get('StatisticsFigure')
        if existing is not None:
            return existing
        from matplotlib import rc_context
        from matplotlib.figure import Figure

        class StatisticsFigure(Figure):
            def savefig(self, fname: Any, *args: Any, **kwargs: Any) -> None:
                with rc_context(_EDITABLE_TEXT_SAVE_RC):
                    super().savefig(fname, *args, **kwargs)

        StatisticsFigure.__qualname__ = 'StatisticsFigure'
        globals()['StatisticsFigure'] = StatisticsFigure
        return StatisticsFigure


def __getattr__(name):
    if name == 'StatisticsFigure':
        return _statistics_figure_class()
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


def resolve_statistics_plot_style(
    preset_name: str | None = None,
    saved_style: dict | None = None,
    local_override: dict | None = None,
) -> dict:
    """Resolve group-statistics plot style from preset, saved state, then local overrides."""
    try:
        preset = get_journal_preset(preset_name or "Nature")
    except KeyError:
        preset = get_journal_preset("Nature")
    style = plot_style_from_preset(preset)
    style.update(runtime_plot_style_override())
    style.update(dict(saved_style or {}))
    style.update(dict(local_override or {}))
    return style


def build_group_statistics_figure(
    statistics_result: dict,
    style: dict | None = None,
    y_label: str = "Relative expression (fold change)",
    *,
    chart_kind: str | None = None,
) -> Figure:
    style = resolve_statistics_plot_style(local_override=style)
    rows = list(statistics_result.get("plot_data", []))
    summary_rows = [
        row
        for row in list(statistics_result.get("groups", []))
        if isinstance(row, Mapping) and str(row.get("group", ""))
    ]
    if not rows and not summary_rows:
        raise ValueError("No statistics plot data available.")
    groups = list(
        dict.fromkeys(
            str(row.get("group", ""))
            for row in (rows or summary_rows)
            if str(row.get("group", ""))
        )
    )
    fig = _statistics_figure_class()(figsize=(3.4, 2.6), dpi=180, facecolor="white")
    fig.subplots_adjust(left=0.28, right=0.96, bottom=0.22, top=0.86)
    ax = fig.add_subplot(111)
    x = np.arange(len(groups), dtype=float)
    values_by_group = {
        group: [float(row["value"]) for row in rows if str(row.get("group", "")) == group]
        for group in groups
    }
    if rows:
        means = [float(np.mean(values_by_group[group])) for group in groups]
        sems = [
            float(np.std(values_by_group[group], ddof=1) / np.sqrt(len(values_by_group[group])))
            if len(values_by_group[group]) > 1
            else 0.0
            for group in groups
        ]
    else:
        summary_by_group = {
            str(row.get("group")): row
            for row in summary_rows
        }
        means = [float(summary_by_group[group].get("mean", 0.0)) for group in groups]
        sems = [
            float(summary_by_group[group].get("error", summary_by_group[group].get("sem", 0.0)) or 0.0)
            for group in groups
        ]
    summaries = {group: {"mean": means[index], "error": sems[index]} for index, group in enumerate(groups)}
    drawn = draw_grouped_chart(
        ax,
        values_by_group,
        summaries,
        chart_kind=chart_kind,
        style=style,
        group_order=groups,
    )
    x = np.asarray([drawn["positions"][group] for group in groups], dtype=float)
    comparisons = []
    for comparison in statistics_result.get("comparisons", []):
        group1 = str(comparison.get("group1", ""))
        group2 = str(comparison.get("group2", ""))
        if group1 in groups and group2 in groups:
            comparisons.append(comparison)
    layout = grouped_chart_y_layout(
        drawn["data_extent"],
        chart_kind=drawn["chart_kind"],
        style=style,
        comparison_count=len(comparisons),
    )
    bracket_y = layout["bracket_base"]
    step = layout["bracket_step"]
    font_size = float(style.get("font_size_pt", 7.0))
    for comparison_index, comparison in enumerate(comparisons):
        group1 = str(comparison.get("group1", ""))
        group2 = str(comparison.get("group2", ""))
        x1 = groups.index(group1)
        x2 = groups.index(group2)
        y = bracket_y + step * comparison_index
        bracket_height = step * float(style.get("bracket_height_fraction", 0.18))
        ax.plot([x1, x1, x2, x2], [y, y + bracket_height, y + bracket_height, y], color=str(style.get("bar_edge_color", "#272727")), linewidth=float(style.get("bracket_line_width", 0.7)))
        if bool(style.get("show_comparison_labels", True)):
            ax.text((x1 + x2) / 2, y + step * float(style.get("bracket_label_offset_fraction", 0.22)), str(comparison.get("label", "n/a")), ha="center", va="bottom", fontsize=font_size, color=str(style.get("bar_edge_color", "#272727")), fontfamily=str(style.get("font_family", "Arial")))
    if bool(style.get("show_x_labels", True)):
        ax.set_xticks(x)
        ax.set_xticklabels(groups, fontsize=font_size, fontfamily=str(style.get("font_family", "Arial")))
    else:
        ax.set_xticks(x)
        ax.set_xticklabels(["" for _group in groups])
    if bool(style.get("show_y_label", True)):
        ax.set_ylabel(y_label, fontsize=float(style.get("axis_label_size_pt", font_size + 1)), labelpad=6, fontfamily=str(style.get("font_family", "Arial")))
    axis_line_width = float(style.get("axis_line_width", 0.6))
    ax.tick_params(axis="y", labelsize=float(style.get("tick_label_size_pt", font_size)), width=float(style.get("tick_width", axis_line_width)), length=float(style.get("tick_length", 3.0)))
    ax.tick_params(axis="x", width=float(style.get("tick_width", axis_line_width)), length=0)
    ax.spines["top"].set_visible(bool(style.get("show_top_spine", False)))
    ax.spines["right"].set_visible(bool(style.get("show_right_spine", False)))
    ax.spines["left"].set_linewidth(axis_line_width)
    ax.spines["bottom"].set_linewidth(axis_line_width)
    ax.set_ylim(bottom=layout["ylim_bottom"], top=layout["ylim_top"])
    apply_plot_axes_style(ax, style)
    return fig


def _build_distribution_descriptive_figure(
    distribution_result: dict,
    *,
    style: dict,
    chart_kind: str,
    x_label: str,
    y_label: str | None,
) -> Figure:
    series = _finite_distribution_series(distribution_result)
    if not series:
        raise ValueError("No distribution plot data available.")
    raw_groups: dict[str, list[float]] = {}
    group_order: list[str] = []
    for index, row in enumerate(series):
        label = str(row.get("lane_label", f"Sample {index + 1}"))
        if label in raw_groups:
            label = f"{label} ({index + 1})"
        group_order.append(label)
        raw_groups[label] = [float(value) for value in row.get("values", [])]
    reason = chart_kind_unavailable_reason(chart_kind, raw_groups, has_raw=True)
    if reason:
        raise ValueError(reason)
    fig = _statistics_figure_class()(figsize=(3.6, 2.6), dpi=180, facecolor="white")
    fig.subplots_adjust(left=0.24, right=0.96, bottom=0.24, top=0.86)
    ax = fig.add_subplot(111)
    drawn = draw_grouped_chart(
        ax,
        raw_groups,
        None,
        chart_kind=chart_kind,
        style=style,
        group_order=group_order,
    )
    font_size = float(style.get("font_size_pt", 7.0))
    positions = [drawn["positions"][group] for group in drawn["group_order"]]
    ax.set_xticks(positions)
    ax.set_xticklabels(drawn["group_order"], fontsize=font_size, fontfamily=str(style.get("font_family", "Arial")))
    ax.set_ylabel(
        y_label or x_label,
        fontsize=font_size + 1,
        fontfamily=str(style.get("font_family", "Arial")),
    )
    ax.tick_params(axis="both", labelsize=float(style.get("tick_label_size_pt", 7.0)), width=float(style.get("axis_line_width", 0.6)), length=3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(float(style.get("axis_line_width", 0.6)))
    ax.spines["bottom"].set_linewidth(float(style.get("axis_line_width", 0.6)))
    layout = grouped_chart_y_layout(
        drawn["data_extent"],
        chart_kind=drawn["chart_kind"],
        style=style,
        comparison_count=0,
    )
    ax.set_ylim(bottom=layout["ylim_bottom"], top=layout["ylim_top"])
    return fig


def build_distribution_comparison_figure(
    distribution_result: dict,
    *,
    style: dict | None = None,
    plot_kind: str = "bar",
    x_label: str = "Object area (px²)",
    y_label: str | None = None,
    frequency_mode: str = "absolute",
    chart_kind: str | None = None,
) -> Figure:
    style = resolve_statistics_plot_style(local_override=style)
    if chart_kind is None:
        resolved_kind = "frequency_line" if str(plot_kind) == "line" else "frequency_bar"
    else:
        resolved_kind = normalize_chart_kind(chart_kind, default="frequency_bar")
    if resolved_kind in {"box", "box_dot", "violin", "violin_dot", "dot"}:
        return _build_distribution_descriptive_figure(
            distribution_result,
            style=style,
            chart_kind=resolved_kind,
            x_label=x_label,
            y_label=y_label,
        )
    if resolved_kind not in {"frequency_bar", "frequency_line"}:
        raise ValueError(f"Unsupported distribution chart kind: {resolved_kind}")
    plot_kind = "line" if resolved_kind == "frequency_line" else "bar"
    series = _finite_distribution_series(distribution_result)
    if not series:
        raise ValueError("No distribution plot data available.")
    pooled = np.asarray([float(value) for row in series for value in row.get("values", [])], dtype=float)
    if pooled.size == 0:
        raise ValueError("No distribution values available.")
    bin_count = min(12, max(5, int(round(np.sqrt(pooled.size)))))
    edges = np.histogram_bin_edges(pooled, bins=bin_count)
    centers = (edges[:-1] + edges[1:]) / 2.0
    fig = _statistics_figure_class()(figsize=(3.6, 2.6), dpi=180, facecolor="white")
    fig.subplots_adjust(left=0.24, right=0.96, bottom=0.24, top=0.86)
    ax = fig.add_subplot(111)
    colors = list(style.get("series_colors", [])) or [str(style.get("point_color", "#0F4D92"))]
    normalized_frequency_mode = "percent" if str(frequency_mode).strip().lower() == "percent" else "absolute"

    def _bin_values(row: dict) -> np.ndarray:
        counts, _ = np.histogram(np.asarray(row.get("values", []), dtype=float), bins=edges)
        values = counts.astype(float)
        if normalized_frequency_mode == "percent" and values.sum() > 0.0:
            values *= 100.0 / values.sum()
        return values

    if plot_kind == "line":
        for index, row in enumerate(series):
            counts = _bin_values(row)
            ax.plot(
                centers,
                counts,
                marker="o",
                linewidth=float(style.get("line_width", 1.2)),
                markersize=float(style.get("point_size", 16.0)) * 0.25,
                color=str(colors[index % len(colors)]),
                label=str(row.get("lane_label", f"Sample {index + 1}")),
            )
    else:
        bin_width = float(edges[1] - edges[0])
        width = bin_width * 0.8 / max(1, len(series))
        offsets = (np.arange(max(1, len(series))) - (max(1, len(series)) - 1) / 2.0) * width
        for index, row in enumerate(series):
            counts = _bin_values(row)
            ax.bar(
                centers + offsets[index],
                counts,
                width=width,
                color=str(colors[index % len(colors)]),
                edgecolor=str(style.get("bar_edge_color", "#272727")),
                linewidth=float(style.get("bar_line_width", 0.8)),
                alpha=0.85,
                label=str(row.get("lane_label", f"Sample {index + 1}")),
            )
    ax.set_xlabel(x_label, fontsize=float(style.get("font_size_pt", 7.0)) + 1, fontfamily=str(style.get("font_family", "Arial")))
    resolved_y_label = y_label or ("Percent of objects" if normalized_frequency_mode == "percent" else "Frequency")
    ax.set_ylabel(resolved_y_label, fontsize=float(style.get("font_size_pt", 7.0)) + 1, fontfamily=str(style.get("font_family", "Arial")))
    ax.tick_params(axis="both", labelsize=float(style.get("tick_label_size_pt", 7.0)), width=float(style.get("axis_line_width", 0.6)), length=3)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_linewidth(float(style.get("axis_line_width", 0.6)))
    ax.spines["bottom"].set_linewidth(float(style.get("axis_line_width", 0.6)))
    ax.legend(frameon=False, fontsize=float(style.get("legend_size_pt", 6.5)))
    return fig


def _finite_distribution_series(distribution_result: dict) -> list[dict[str, Any]]:
    """Return copied distribution rows with only finite observations."""
    series: list[dict[str, Any]] = []
    for row in distribution_result.get("series", []):
        if not isinstance(row, Mapping):
            continue
        values: list[float] = []
        raw_values = row.get("values", [])
        if raw_values is None:
            raw_values = []
        for value in raw_values:
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                continue
            if np.isfinite(numeric):
                values.append(numeric)
        if values:
            copied = dict(row)
            copied["values"] = values
            series.append(copied)
    return series
