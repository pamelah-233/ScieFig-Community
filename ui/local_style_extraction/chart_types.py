from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
import numpy as np

from .demo_common import STYLE_KEYS, NEUTRAL_RESOLVED, PRODUCTION_UNSUPPORTED_KEYS, SCHEMA, abs_path, unique_run_dir

LEGACY_CHARTS = ("multipanel", "bar", "line", "scatter")
RAW_CHARTS = ("bar_dot", "box", "box_dot", "violin", "violin_dot", "dot")
CHARTS = LEGACY_CHARTS + RAW_CHARTS
DEFAULT_CHART = "multipanel"
CHART_LABELS = {
    "multipanel": "组合面板",
    "bar": "柱状",
    "line": "折线",
    "scatter": "XY散点",
    "bar_dot": "柱状+散点",
    "box": "箱线",
    "box_dot": "箱线+散点",
    "violin": "小提琴",
    "violin_dot": "小提琴+散点",
    "dot": "分组散点",
}
FUTURE_PRODUCTION_HOOKS = {
    "statistics": "core.plotting.statistics_plot.build_group_statistics_figure",
    "distribution": "core.plotting.statistics_plot.build_distribution_comparison_figure",
    "qpcr": "core.plotting.qpcr_plot.build_qpcr_figure",
    "wb": "core.plotting_densitometry.build_wb_statistics_figure",
    "style": "core.plotting.statistics_plot.resolve_statistics_plot_style",
}
DEMO_GROUPED_RAW: dict[str, Any] = {
    "kind": "grouped_raw",
    "label": "demo_synthetic_not_from_paper",
    "groups": [
        {"label": "A", "values": [1.0, 1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 7.5]},
        {"label": "B", "values": [2.0, 2.1, 2.3, 2.4, 2.6, 2.7, 2.9, 3.0, 3.2, 3.3, 3.5, 3.8]},
        {"label": "C", "values": [0.6, 0.8, 0.9, 1.0, 1.1, 1.3, 1.4, 1.5, 1.7, 1.8, 2.0, 2.2]},
    ],
}
FIGSIZE = (8.0, 5.2)
DPI = 120
FALLBACK_FONT = "DejaVu Sans"
PALETTE_PER_ROW = 16
JITTER_SEED = 20260909
JITTER_HALF_WIDTH = 0.12
VIOLIN_MIN_N = 3
PDF_FAMILY_ALIASES = {
    "Times": "Times New Roman",
    "Times-Roman": "Times New Roman",
    "Times Roman": "Times New Roman",
    "Helvetica": "Arial",
    "Courier": "Courier New",
}
_SUMMARY_KEYS = {"mean", "sd", "sem", "std", "n", "error"}
_SUMMARY_KINDS = {"summary", "summary_only", "mean_sem", "mean_sd"}


class ChartDataError(ValueError):
    pass


class SummaryOnlyError(ChartDataError):
    def __init__(self, detail: str = "") -> None:
        msg = "Summary-only mean/SD/SEM input cannot render a claimed distribution"
        if detail:
            msg = msg + ": " + detail
        super().__init__(msg)


class ViolinUnsupportedError(ChartDataError):
    def __init__(self, reason: str) -> None:
        super().__init__("Violin unsupported: " + reason)


class _FontResolver:
    def __init__(self) -> None:
        from matplotlib import font_manager
        self.substitutions: dict[str, str] = {}
        self._names = {str(item.name) for item in font_manager.fontManager.ttflist}
        self._fallback_path = font_manager.findfont(font_manager.FontProperties(family=FALLBACK_FONT))

    def actual(self, requested: str) -> str:
        name = str(requested or FALLBACK_FONT)
        if name in self._names:
            return name
        alias = PDF_FAMILY_ALIASES.get(name)
        if not alias:
            key = name.replace("_", " ").strip()
            alias = PDF_FAMILY_ALIASES.get(key)
        if alias and alias in self._names:
            self.substitutions[name] = alias
            return alias
        self.substitutions[name] = FALLBACK_FONT
        return FALLBACK_FONT

    def properties(self, style: dict[str, Any], size: float) -> font_manager.FontProperties:
        from matplotlib import font_manager
        requested = str(style.get("font_family") or FALLBACK_FONT)
        actual = self.actual(requested)
        return font_manager.FontProperties(family=actual, size=size, weight=style.get('font_weight', 'normal'))


def _style_from_draft(draft: dict[str, Any] | None) -> dict[str, Any]:
    if draft is None:
        return copy.deepcopy(NEUTRAL_RESOLVED)
    if not isinstance(draft, dict) or draft.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    resolved = copy.deepcopy(NEUTRAL_RESOLVED)
    incoming = draft.get("resolved_style") or {}
    for key in STYLE_KEYS:
        if key in incoming and incoming[key] is not None:
            resolved[key] = copy.deepcopy(incoming[key])
    return resolved


def _marker(shape: str) -> str:
    mapping = {"circle": "o", "square": "s", "diamond": "D", "triangle": "^"}
    return mapping.get(str(shape).lower(), "o")


def _colors(style: dict[str, Any]) -> list[str]:
    colors = style.get("series_colors") or NEUTRAL_RESOLVED["series_colors"]
    if not isinstance(colors, list) or not colors:
        return list(NEUTRAL_RESOLVED["series_colors"])
    return [str(c) for c in colors]


def _palette_rows(n: int) -> int:
    if n <= 0:
        return 0
    return (n + PALETTE_PER_ROW - 1) // PALETTE_PER_ROW


def _palette_reserve(rows: int) -> float:
    if rows <= 0:
        return 0.0
    return 0.016 + 0.034 * rows


def _draw_palette_strip(fig, style: dict[str, Any], fonts: _FontResolver, reserve_rows: int) -> None:
    from matplotlib.patches import Rectangle
    colors = _colors(style)
    n = len(colors)
    if n <= 0 or reserve_rows <= 0:
        return
    rows = max(_palette_rows(n), 1)
    height = max(_palette_reserve(reserve_rows) - 0.006, 0.022)
    ax = fig.add_axes([0.12, 0.008, 0.82, height])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    label_fp = fonts.properties(style, 7.0)
    ax.text(
        0.0,
        0.5,
        f"{n} colors",
        ha="left",
        va="center",
        fontproperties=label_fp,
        color=str(style.get("label_color") or "#222222"),
        clip_on=False,
    )
    left = 0.16
    usable = 1.0 - left
    per = PALETTE_PER_ROW
    row_h = 1.0 / max(rows, 1)
    for i, color in enumerate(colors):
        r = i // per
        c = i % per
        cell_w = usable / per
        w = cell_w * 0.88
        x = left + c * cell_w
        y = 1.0 - (r + 1) * row_h + row_h * 0.14
        h = row_h * 0.72
        ax.add_patch(
            Rectangle(
                (x, y),
                w,
                h,
                facecolor=str(color),
                edgecolor="#333333",
                linewidth=0.35,
                clip_on=False,
            )
        )


def _apply_axes(ax, style: dict[str, Any], fonts: _FontResolver, *, xlabel: str, ylabel: str) -> None:
    face = str(style.get("background_color") or "#FFFFFF")
    label = str(style.get("label_color") or "#222222")
    tick_fp = fonts.properties(style, float(style.get("tick_label_size_pt") or 8.0))
    axis_fp = fonts.properties(style, float(style.get("axis_label_size_pt") or 9.0))
    legend_fp = fonts.properties(style, float(style.get("legend_size_pt") or 8.0))
    ax.set_facecolor(face)
    if style.get('axes_box_aspect') is not None:
        ax.set_box_aspect(float(style['axes_box_aspect']))
    ax.tick_params(colors=label, labelsize=float(style.get("tick_label_size_pt") or 8.0), width=float(style.get("axis_line_width") or 0.8))
    ax.xaxis.label.set_color(label)
    ax.yaxis.label.set_color(label)
    ax.title.set_color(label)
    ax.set_xlabel(xlabel, fontproperties=axis_fp)
    ax.set_ylabel(ylabel, fontproperties=axis_fp)
    for spine_name, visible in (("top", bool(style.get("show_top_spine"))), ("right", bool(style.get("show_right_spine"))), ("left", True), ("bottom", True)):
        ax.spines[spine_name].set_visible(visible)
        ax.spines[spine_name].set_color(label)
        ax.spines[spine_name].set_linewidth(float(style.get("axis_line_width") or 0.8))
    if bool(style.get("gridline_visible")):
        ax.grid(True, color=str(style.get("gridline_color") or "#D0D0D0"), linewidth=0.6)
    else:
        ax.grid(False)
    ax.set_axisbelow(True)
    angle = float(style.get('x_tick_rotation', 0.))
    for tick_text in ax.get_xticklabels():
        tick_text.set_rotation(angle)
        tick_text.set_ha('right' if angle > 0 else ('left' if angle < 0 else 'center'))
    ax.tick_params(axis='y', length=float(style.get('tick_length', 3.)), width=float(style.get('tick_width', .8)))
    ax.tick_params(axis='x', length=float(style.get('x_tick_length', 0.)), width=float(style.get('tick_width', .8)))
    legend = ax.get_legend()
    if legend is not None:
        for text in legend.get_texts():
            text.set_fontproperties(legend_fp)
            text.set_color(label)
        legend.get_frame().set_facecolor(face)
    for item in ax.get_xticklabels() + ax.get_yticklabels():
        item.set_fontproperties(tick_fp)
        item.set_color(label)


def _save_fig(fig, png_path: Path, svg_path: Path | None) -> None:
    fig.savefig(png_path, dpi=DPI, facecolor=fig.get_facecolor(), bbox_inches="tight")
    if svg_path is not None:
        fig.savefig(svg_path, dpi=DPI, facecolor=fig.get_facecolor(), bbox_inches="tight", format="svg")


def x_jitter(n: int, group_index: int) -> np.ndarray:
    count = int(n)
    if count <= 0:
        return np.zeros(0, dtype=float)
    rng = np.random.default_rng(JITTER_SEED + int(group_index) * 1009)
    return rng.uniform(-JITTER_HALF_WIDTH, JITTER_HALF_WIDTH, size=count)


def box_quartile_stats(values: np.ndarray | list[float]) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        raise ChartDataError("Empty raw sample group")
    q1, q2, q3 = np.percentile(arr, [25, 50, 75], method="linear")
    iqr = float(q3 - q1)
    fence_lo = float(q1) - 1.5 * iqr
    fence_hi = float(q3) + 1.5 * iqr
    inside = arr[(arr >= fence_lo) & (arr <= fence_hi)]
    fliers = arr[(arr < fence_lo) | (arr > fence_hi)]
    if inside.size == 0:
        whislo = float(q1)
        whishi = float(q3)
    else:
        whislo = float(inside.min())
        whishi = float(inside.max())
    return {
        "q1": float(q1),
        "q2": float(q2),
        "q3": float(q3),
        "iqr": iqr,
        "whislo": whislo,
        "whishi": whishi,
        "fliers": [float(v) for v in fliers],
    }


def _looks_summary_group(item: dict[str, Any]) -> bool:
    if "values" in item:
        return False
    return any(key in item for key in _SUMMARY_KEYS)


def parse_grouped_raw(data: dict[str, Any] | None = None) -> list[tuple[str, np.ndarray]]:
    payload = DEMO_GROUPED_RAW if data is None else data
    if not isinstance(payload, dict):
        raise ChartDataError("Grouped raw sample dict required")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind in _SUMMARY_KINDS:
        raise SummaryOnlyError("kind=" + kind)
    groups = payload.get("groups")
    if not isinstance(groups, list) or not groups:
        raise ChartDataError("Grouped raw samples require a non-empty groups list")
    parsed: list[tuple[str, np.ndarray]] = []
    summary_hits = 0
    for index, item in enumerate(groups):
        if not isinstance(item, dict):
            raise ChartDataError("Each group must be a dict")
        if _looks_summary_group(item):
            summary_hits += 1
            continue
        if "values" not in item:
            raise ChartDataError("Group is missing raw values")
        raw = item.get("values")
        if raw is None:
            raise SummaryOnlyError("values is null")
        arr = np.asarray(raw, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            raise ChartDataError("Group has no finite raw values")
        label = str(item.get("label") or f"G{index + 1}")
        parsed.append((label, arr))
    if summary_hits and not parsed:
        raise SummaryOnlyError("groups contain mean/SD/SEM without raw values")
    if not parsed:
        raise ChartDataError("No grouped raw samples")
    return parsed


def _assert_violin_supported(label: str, arr: np.ndarray) -> None:
    if arr.size < VIOLIN_MIN_N:
        raise ViolinUnsupportedError(
            f"group {label} has insufficient raw values (n={int(arr.size)} < {VIOLIN_MIN_N}); distribution is not invented"
        )
    if int(np.unique(arr).size) < 2:
        raise ViolinUnsupportedError(
            f"group {label} has constant raw values; distribution is not invented"
        )


def _empty_geo(chart: str, labels: list[str], arrays: list[np.ndarray]) -> dict[str, Any]:
    return {
        "chart": chart,
        "labels": list(labels),
        "n": [int(a.size) for a in arrays],
        "raw_points": {},
        "fliers_drawn": {},
        "box_stats": {},
        "jitter_x": {},
        "n_raw_points_drawn": 0,
    }


def _scatter_raw(
    ax,
    labels: list[str],
    arrays: list[np.ndarray],
    positions: np.ndarray,
    style: dict[str, Any],
    colors: list[str],
    geo: dict[str, Any],
    *,
    named: bool,
) -> None:
    marker = _marker(str(style.get("point_shape") or "circle"))
    size = float(style.get("point_size") or 8.0)
    edge = str(style.get("label_color") or "#222222")
    total = 0
    for i, (label, arr) in enumerate(zip(labels, arrays)):
        jitter = x_jitter(int(arr.size), i)
        xs = positions[i] + jitter
        ys = np.asarray(arr, dtype=float)
        ax.scatter(
            xs,
            ys,
            s=size ** 2,
            marker=marker,
            color=colors[i % len(colors)],
            edgecolors=edge,
            linewidths=float(style.get('point_line_width', .35)),
            zorder=4,
            label=label if named else None,
        )
        geo["jitter_x"][label] = [float(v) for v in jitter]
        geo["raw_points"][label] = [[float(x), float(y)] for x, y in zip(xs, ys)]
        total += int(arr.size)
    geo["n_raw_points_drawn"] = total


def draw_grouped_on_ax(
    ax,
    chart: str,
    style: dict[str, Any],
    fonts: _FontResolver | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if chart not in RAW_CHARTS:
        raise ValueError(f"Unknown raw chart: {chart}")
    fonts = fonts or _FontResolver()
    groups = parse_grouped_raw(data)
    labels = [item[0] for item in groups]
    arrays = [item[1] for item in groups]
    if chart in {"violin", "violin_dot"}:
        for label, arr in zip(labels, arrays):
            _assert_violin_supported(label, arr)
    positions = np.arange(1, len(groups) + 1, dtype=float)
    colors = _colors(style)
    edge = str(style.get("label_color") or "#222222")
    axis_lw = float(style.get("axis_line_width") or 0.8)
    title_fp = fonts.properties(style, float(style.get("font_size_pt") or 10.0))
    geo = _empty_geo(chart, labels, arrays)
    if chart == "bar_dot":
        means = [float(np.mean(arr)) for arr in arrays]
        ax.bar(
            positions,
            means,
            width=float(style.get('bar_width_fraction', .55)),
            color=([colors[i % len(colors)] for i in range(len(arrays))]
                   if style.get('bar_use_series_colors', True) else style.get('bar_fill_color', '#FFFFFF')),
            edgecolor=style.get('bar_edge_color', edge),
            linewidth=float(style.get('bar_line_width', axis_lw)),
            yerr=[float(np.std(a, ddof=1)/np.sqrt(len(a))) if len(a)>1 else 0. for a in arrays],
            error_kw={'elinewidth':float(style.get('sem_line_width', .8)),
                      'capsize':float(style.get('sem_cap_size', 3.)),
                      'capthick':float(style.get('sem_cap_thick', .8)),
                      'ecolor':style.get('bar_edge_color', edge)},
            zorder=2,
        )
        _scatter_raw(ax, labels, arrays, positions, style, colors, geo, named=True)
    elif chart in {"box", "box_dot"}:
        show_fliers = chart == "box"
        bp = ax.boxplot(
            arrays,
            positions=positions,
            widths=0.45,
            patch_artist=True,
            showfliers=show_fliers,
            whis=1.5,
            medianprops={"color": edge, "linewidth": axis_lw},
            whiskerprops={"color": edge, "linewidth": axis_lw},
            capprops={"color": edge, "linewidth": axis_lw},
            flierprops={
                "marker": _marker(str(style.get("point_shape") or "circle")),
                "markerfacecolor": edge,
                "markeredgecolor": edge,
                "markersize": float(style.get("point_size") or 8.0) * 0.45,
                "linestyle": "none",
            },
            zorder=2,
        )
        for i, box in enumerate(bp["boxes"]):
            box.set_facecolor(colors[i % len(colors)])
            box.set_edgecolor(edge)
            box.set_linewidth(axis_lw)
            box.set_alpha(0.65)
            box.set_label(labels[i])
        for i, (label, arr) in enumerate(zip(labels, arrays)):
            stats = box_quartile_stats(arr)
            geo["box_stats"][label] = stats
            drawn = 0
            if show_fliers and i < len(bp.get("fliers", [])):
                drawn = int(np.asarray(bp["fliers"][i].get_ydata()).size)
            geo["fliers_drawn"][label] = drawn
        if chart == "box_dot":
            _scatter_raw(ax, labels, arrays, positions, style, colors, geo, named=False)
    elif chart in {"violin", "violin_dot"}:
        try:
            parts = ax.violinplot(
                arrays,
                positions=positions,
                widths=0.7,
                showmeans=False,
                showextrema=False,
                showmedians=False,
            )
        except ValueError as exc:
            raise ViolinUnsupportedError("raw values cannot form a violin; distribution is not invented") from exc
        for i, body in enumerate(parts.get("bodies", [])):
            body.set_facecolor(colors[i % len(colors)])
            body.set_edgecolor(edge)
            body.set_alpha(0.55)
            body.set_linewidth(axis_lw)
            body.set_label(labels[i])
        if chart == "violin_dot":
            _scatter_raw(ax, labels, arrays, positions, style, colors, geo, named=False)
    elif chart == "dot":
        _scatter_raw(ax, labels, arrays, positions, style, colors, geo, named=True)
    ax.set_xticks(positions, labels)
    ax.legend(frameon=False, loc="upper left")
    _apply_axes(ax, style, fonts, xlabel="group", ylabel="value")
    ax.set_title(chart, fontproperties=title_fp, pad=10)
    return geo


def render_raw_comparison(
    draft: dict,
    output_dir: str,
    chart: str = "box_dot",
    data: dict[str, Any] | None = None,
) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if chart not in RAW_CHARTS:
        raise ValueError(f"Unknown raw chart: {chart}")
    if not isinstance(draft, dict) or draft.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    payload = DEMO_GROUPED_RAW if data is None else data
    parse_grouped_raw(payload)
    if chart in {"violin", "violin_dot"}:
        for label, arr in parse_grouped_raw(payload):
            _assert_violin_supported(label, arr)
    run_dir = unique_run_dir(output_dir)
    baseline_png = abs_path(run_dir / "baseline.png")
    styled_png = abs_path(run_dir / "styled.png")
    styled_svg = abs_path(run_dir / "styled.svg")
    baseline_style = copy.deepcopy(NEUTRAL_RESOLVED)
    styled_style = _style_from_draft(draft)
    fonts = _FontResolver()
    reserve_rows = max(_palette_rows(len(_colors(baseline_style))), _palette_rows(len(_colors(styled_style))))
    extra = _palette_reserve(reserve_rows)
    fig_a = plt.figure(figsize=FIGSIZE, dpi=DPI)
    fig_a.patch.set_facecolor(str(baseline_style.get("background_color") or "#FFFFFF"))
    ax_a = fig_a.add_subplot(1, 1, 1)
    geo_a = draw_grouped_on_ax(ax_a, chart, baseline_style, fonts, data=payload)
    fig_a.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.14 + extra)
    _draw_palette_strip(fig_a, baseline_style, fonts, reserve_rows)
    _save_fig(fig_a, Path(baseline_png), None)
    plt.close(fig_a)
    fig_b = plt.figure(figsize=FIGSIZE, dpi=DPI)
    fig_b.patch.set_facecolor(str(styled_style.get("background_color") or "#FFFFFF"))
    ax_b = fig_b.add_subplot(1, 1, 1)
    geo_b = draw_grouped_on_ax(ax_b, chart, styled_style, fonts, data=payload)
    fig_b.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.14 + extra)
    _draw_palette_strip(fig_b, styled_style, fonts, reserve_rows)
    _save_fig(fig_b, Path(styled_png), Path(styled_svg))
    plt.close(fig_b)
    applied = []
    for key in STYLE_KEYS:
        token = (draft.get("tokens") or {}).get(key) or {}
        if token.get("status") in {"detected", "estimated", "user_edited"} and token.get("value") is not None:
            applied.append(key)
        elif key not in (draft.get("fallback_keys") or []) and styled_style.get(key) is not None:
            applied.append(key)
    applied = list(dict.fromkeys(applied))
    return {
        "baseline_png": baseline_png,
        "styled_png": styled_png,
        "styled_svg": styled_svg,
        "applied_keys": applied,
        "unsupported_keys": [key for key in PRODUCTION_UNSUPPORTED_KEYS],
        "font_substitutions": dict(fonts.substitutions),
        "chart": chart,
        "geometry_baseline": geo_a,
        "geometry_styled": geo_b,
    }

