from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

from .demo_common import STYLE_KEYS, NEUTRAL_RESOLVED, PRODUCTION_UNSUPPORTED_KEYS, SCHEMA, abs_path, unique_run_dir
from .chart_types import CHARTS, RAW_CHARTS, draw_grouped_on_ax
FIGSIZE = (8.0, 5.2)
DPI = 120
FALLBACK_FONT = "DejaVu Sans"
PALETTE_PER_ROW = 16
PDF_FAMILY_ALIASES = {
    "Times": "Times New Roman",
    "Times-Roman": "Times New Roman",
    "Times Roman": "Times New Roman",
    "Helvetica": "Arial",
    "Courier": "Courier New",
}

BAR_X = ["G1", "G2", "G3", "G4"]
BAR_A = [3.2, 4.1, 2.7, 5.0]
BAR_B = [2.4, 3.5, 3.0, 4.2]
LINE_X = [0, 1, 2, 3, 4, 5, 6, 7]
LINE_A = [0.4, 0.7, 1.1, 1.0, 1.6, 1.8, 2.1, 2.4]
LINE_B = [0.6, 0.5, 0.9, 1.3, 1.2, 1.7, 1.9, 2.2]
SCATTER_AX = [0.2, 0.4, 0.7, 1.1, 1.3, 1.6, 1.9, 2.2, 2.5, 2.8, 3.1, 3.4]
SCATTER_AY = [0.8, 1.1, 0.9, 1.5, 1.3, 1.8, 1.6, 2.1, 2.0, 2.4, 2.2, 2.7]
SCATTER_BX = [0.3, 0.6, 0.9, 1.2, 1.5, 1.8, 2.1, 2.4, 2.7, 3.0, 3.3, 3.6]
SCATTER_BY = [1.4, 1.2, 1.7, 1.5, 2.0, 1.8, 2.3, 2.1, 2.6, 2.4, 2.8, 2.5]


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


def _draw_bar(ax, style: dict[str, Any], fonts: _FontResolver) -> None:
    colors = _colors(style)
    xs = list(range(len(BAR_X)))
    width = 0.36
    title_fp = fonts.properties(style, float(style.get("font_size_pt") or 10.0))
    edge = style.get('bar_edge_color', style.get('label_color', '#222222'))
    line_width = float(style.get('bar_line_width', style.get('axis_line_width', .8)))
    fills = colors if style.get('bar_use_series_colors', True) else [style.get('bar_fill_color', '#FFFFFF')]
    ax.bar([x - width / 2 for x in xs], BAR_A, width=width, color=fills[0], edgecolor=edge, linewidth=line_width, label="A")
    ax.bar([x + width / 2 for x in xs], BAR_B, width=width, color=fills[min(1, len(fills) - 1)], edgecolor=edge, linewidth=line_width, label="B")
    ax.set_xticks(xs, BAR_X)
    ax.set_ylim(0, 6)
    ax.legend(frameon=False, loc="upper left")
    _apply_axes(ax, style, fonts, xlabel="group", ylabel="value")
    ax.set_title("bar", fontproperties=title_fp, pad=10)


def _draw_line(ax, style: dict[str, Any], fonts: _FontResolver) -> None:
    colors = _colors(style)
    lw = float(style.get("line_width") or 1.25)
    title_fp = fonts.properties(style, float(style.get("font_size_pt") or 10.0))
    ax.plot(LINE_X, LINE_A, color=colors[0], linewidth=lw, label="A")
    ax.plot(LINE_X, LINE_B, color=colors[min(1, len(colors) - 1)], linewidth=lw, label="B")
    ax.set_xlim(0, 7)
    ax.set_ylim(0, 3)
    ax.legend(frameon=False, loc="upper left")
    _apply_axes(ax, style, fonts, xlabel="x", ylabel="y")
    ax.set_title("line", fontproperties=title_fp, pad=10)


def _draw_scatter(ax, style: dict[str, Any], fonts: _FontResolver) -> None:
    colors = _colors(style)
    marker = _marker(str(style.get("point_shape") or "circle"))
    size = float(style.get("point_size") or 8.0)
    title_fp = fonts.properties(style, float(style.get("font_size_pt") or 10.0))
    outline = dict(edgecolors=style.get('bar_edge_color', '#222222'), linewidths=float(style.get('point_line_width', .35)))
    ax.scatter(SCATTER_AX, SCATTER_AY, s=size ** 2, marker=marker, color=colors[0], label="A", **outline)
    ax.scatter(SCATTER_BX, SCATTER_BY, s=size ** 2, marker=marker, color=colors[min(1, len(colors) - 1)], label="B", **outline)
    ax.set_xlim(0, 4)
    ax.set_ylim(0.5, 3.2)
    ax.legend(frameon=False, loc="upper left")
    _apply_axes(ax, style, fonts, xlabel="x", ylabel="y")
    ax.set_title("scatter", fontproperties=title_fp, pad=10)


def _apply_layout(fig, chart: str, bottom_extra: float = 0.0) -> None:
    extra = max(float(bottom_extra), 0.0)
    if chart == "multipanel":
        fig.subplots_adjust(left=0.08, right=0.98, top=0.86, bottom=0.08 + extra, wspace=0.42, hspace=0.70)
    else:
        fig.subplots_adjust(left=0.12, right=0.97, top=0.88, bottom=0.14 + extra)


def _draw_chart(fig, style: dict[str, Any], chart: str, fonts: _FontResolver, reserve_rows: int = 1) -> None:
    extra = _palette_reserve(reserve_rows)
    fig.patch.set_facecolor(str(style.get("background_color") or "#FFFFFF"))
    if chart == "bar":
        ax = fig.add_subplot(1, 1, 1)
        _draw_bar(ax, style, fonts)
        _apply_layout(fig, chart, extra)
        _draw_palette_strip(fig, style, fonts, reserve_rows)
        return
    if chart == "line":
        ax = fig.add_subplot(1, 1, 1)
        _draw_line(ax, style, fonts)
        _apply_layout(fig, chart, extra)
        _draw_palette_strip(fig, style, fonts, reserve_rows)
        return
    if chart == "scatter":
        ax = fig.add_subplot(1, 1, 1)
        _draw_scatter(ax, style, fonts)
        _apply_layout(fig, chart, extra)
        _draw_palette_strip(fig, style, fonts, reserve_rows)
        return
    if chart in RAW_CHARTS:
        ax = fig.add_subplot(1, 1, 1)
        draw_grouped_on_ax(ax, chart, style, fonts)
        _apply_layout(fig, chart, extra)
        _draw_palette_strip(fig, style, fonts, reserve_rows)
        return
    gs = fig.add_gridspec(2, 2, left=0.08, right=0.98, top=0.84, bottom=0.08 + extra, wspace=0.45, hspace=0.72)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])
    _draw_bar(ax1, style, fonts)
    _draw_line(ax2, style, fonts)
    _draw_scatter(ax3, style, fonts)
    _draw_bar(ax4, style, fonts)
    title_fp = fonts.properties(style, float(style.get("font_size_pt") or 10.0))
    fig.suptitle("multipanel", fontproperties=title_fp, color=str(style.get("label_color") or "#222222"), y=0.98)
    _draw_palette_strip(fig, style, fonts, reserve_rows)


def _save_fig(fig, png_path: Path, svg_path: Path | None) -> None:
    fig.savefig(png_path, dpi=DPI, facecolor=fig.get_facecolor(), bbox_inches="tight")
    if svg_path is not None:
        fig.savefig(svg_path, dpi=DPI, facecolor=fig.get_facecolor(), bbox_inches="tight", format="svg")


def render_comparison(draft: dict, output_dir: str, chart: str = "multipanel") -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if chart not in CHARTS:
        raise ValueError(f"Unknown chart: {chart}")
    if not isinstance(draft, dict) or draft.get("schema") != SCHEMA:
        raise ValueError("Style JSON is not a local-style-demo draft")
    run_dir = unique_run_dir(output_dir)
    baseline_png = abs_path(run_dir / "baseline.png")
    styled_png = abs_path(run_dir / "styled.png")
    styled_svg = abs_path(run_dir / "styled.svg")
    baseline_style = copy.deepcopy(NEUTRAL_RESOLVED)
    styled_style = _style_from_draft(draft)
    fonts = _FontResolver()
    reserve_rows = max(_palette_rows(len(_colors(baseline_style))), _palette_rows(len(_colors(styled_style))))
    fig_a = plt.figure(figsize=FIGSIZE, dpi=DPI)
    _draw_chart(fig_a, baseline_style, chart, fonts, reserve_rows=reserve_rows)
    _save_fig(fig_a, Path(baseline_png), None)
    plt.close(fig_a)
    fig_b = plt.figure(figsize=FIGSIZE, dpi=DPI)
    _draw_chart(fig_b, styled_style, chart, fonts, reserve_rows=reserve_rows)
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
    unsupported = [key for key in PRODUCTION_UNSUPPORTED_KEYS]
    return {
        "baseline_png": baseline_png,
        "styled_png": styled_png,
        "styled_svg": styled_svg,
        "applied_keys": applied,
        "unsupported_keys": unsupported,
        "font_substitutions": dict(fonts.substitutions),
    }

