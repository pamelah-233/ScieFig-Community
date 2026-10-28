from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from matplotlib import rc_context
from matplotlib.figure import Figure

from core.plotting.chart_kinds import draw_grouped_chart, grouped_chart_y_layout
from core.plotting.style_presets import apply_matplotlib_preset, apply_plot_axes_style, get_journal_preset, plot_style_from_preset
from core.style_preset_resolver import runtime_plot_style_override


def _apply_qpcr_matplotlib_preset(preset_name: str | None = None) -> None:
    try:
        apply_matplotlib_preset(get_journal_preset(preset_name or "Nature"))
    except KeyError:
        apply_matplotlib_preset(get_journal_preset("Nature"))


def save_qpcr_figure(figure: Figure, destination: Any, preset_name: str | None = None, **savefig_kwargs: Any) -> None:
    with rc_context({"svg.fonttype": "none"}):
        _apply_qpcr_matplotlib_preset(preset_name)
        figure.savefig(destination, **savefig_kwargs)


def _resolved_plot_style(preset_name: str | None = None, style: dict | None = None) -> dict[str, Any]:
    try:
        preset = get_journal_preset(preset_name or "Nature")
    except KeyError:
        preset = get_journal_preset("Nature")
    resolved = plot_style_from_preset(preset)
    resolved.update(runtime_plot_style_override())
    resolved.update(dict(style or {}))
    return resolved


def _target_name(plot_data: pd.DataFrame, target: str | None) -> str:
    targets = list(dict.fromkeys(plot_data["target"].astype(str).tolist()))
    if not targets:
        raise ValueError("No qPCR targets available for plotting.")
    if target and target in targets:
        return target
    return targets[0]


def _comparison_label(statistics: dict[str, Any], target: str, group1: str, group2: str) -> str | None:
    target_stats = statistics.get("targets", {}).get(target, {})
    for comparison in target_stats.get("comparisons", []):
        pair = {comparison.get("group1"), comparison.get("group2")}
        if pair == {group1, group2}:
            return str(comparison.get("label", "n/a"))
    return None


def build_qpcr_figure(
    plot_data: pd.DataFrame,
    statistics: dict[str, Any],
    target: str | None = None,
    preset_name: str | None = None,
    y_label: str = "Relative mRNA expression",
    title: str | None = None,
    x_rotation: int | None = None,
    style: dict | None = None,
    *,
    chart_kind: str | None = None,
) -> Figure:
    with rc_context():
        _apply_qpcr_matplotlib_preset(preset_name)
        style = _resolved_plot_style(preset_name, style)
        if x_rotation is not None:
            style['x_tick_rotation'] = x_rotation
        x_rotation = float(style.get('x_tick_rotation', 0))
        resolved_target = _target_name(plot_data, target)
        target_df = plot_data[plot_data["target"] == resolved_target].copy()
        groups = list(dict.fromkeys(target_df["group"].astype(str).tolist()))
        if not groups:
            raise ValueError("No qPCR groups available for plotting.")

        fig = Figure(figsize=(3.5, 2.8), dpi=150, facecolor="white", constrained_layout=True)
        ax = fig.add_subplot(111)

        group_summary = target_df.groupby("group", as_index=False).agg(
            mean_fold_change=("mean_fold_change", "first"),
            error=("error", "first"),
        )
        summary_by_group = group_summary.set_index("group")
        summaries = {
            group: {
                "mean": float(summary_by_group.loc[group, "mean_fold_change"]),
                "error": float(summary_by_group.loc[group, "error"]),
            }
            for group in groups
        }
        raw_groups = {
            group: target_df[target_df["group"] == group]["fold_change"].to_numpy(dtype=float).tolist()
            for group in groups
        }
        drawn = draw_grouped_chart(
            ax,
            raw_groups,
            summaries,
            chart_kind=chart_kind,
            style=style,
            group_order=groups,
        )
        x = np.asarray([drawn["positions"][group] for group in groups], dtype=float)
        font_size = float(style.get("font_size_pt", 7.0))
        labeled: list[tuple[int, int, str]] = []
        for comparison in statistics.get("targets", {}).get(resolved_target, {}).get("comparisons", []):
            first, second = comparison.get("group1"), comparison.get("group2")
            if first in groups and second in groups and first != second:
                labeled.append((groups.index(first), groups.index(second), str(comparison.get("label", "n/a"))))
        layout = grouped_chart_y_layout(
            drawn["data_extent"],
            chart_kind=drawn["chart_kind"],
            style=style,
            comparison_count=len(labeled),
        )
        bracket_base = layout["bracket_base"]
        bracket_step = layout["bracket_step"]
        for bracket_index, (first_index, index, label) in enumerate(labeled):
            y = bracket_base + bracket_step * bracket_index
            bracket_height = bracket_step * float(style.get("bracket_height_fraction", 0.18))
            ax.plot([first_index, first_index, index, index], [y, y + bracket_height, y + bracket_height, y], color=str(style.get("bar_edge_color", "#272727")), linewidth=float(style.get("bracket_line_width", 0.7)))
            ax.text((first_index + index) / 2, y + bracket_step * float(style.get("bracket_label_offset_fraction", 0.22)), label, ha="center", va="bottom", fontsize=font_size, color=str(style.get("bar_edge_color", "#272727")), fontfamily=str(style.get("font_family", "Arial")))

        ax.set_ylim(bottom=layout["ylim_bottom"], top=layout["ylim_top"])
        ax.set_xticks(x)
        ax.set_xticklabels(groups, rotation=x_rotation, ha="right" if x_rotation else "center", fontsize=float(style.get("tick_label_size_pt", font_size)), fontfamily=str(style.get("font_family", "Arial")))
        ax.set_ylabel(y_label, fontsize=float(style.get("axis_label_size_pt", font_size + 1)), fontfamily=str(style.get("font_family", "Arial")))
        ax.set_title(title or resolved_target, fontsize=float(style.get("axis_label_size_pt", font_size + 1)), pad=6, fontfamily=str(style.get("font_family", "Arial")))
        axis_line_width = float(style.get("axis_line_width", 0.6))
        ax.tick_params(axis="y", labelsize=float(style.get("tick_label_size_pt", font_size)), width=float(style.get("tick_width", axis_line_width)), length=float(style.get("tick_length", 3.0)))
        ax.tick_params(axis="x", width=float(style.get("tick_width", axis_line_width)), length=0)
        ax.spines["top"].set_visible(bool(style.get("show_top_spine", False)))
        ax.spines["right"].set_visible(bool(style.get("show_right_spine", False)))
        ax.spines["left"].set_linewidth(axis_line_width)
        ax.spines["bottom"].set_linewidth(axis_line_width)
        apply_plot_axes_style(ax, style)
        return fig
