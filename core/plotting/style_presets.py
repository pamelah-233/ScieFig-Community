from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

DEFAULT_PRESET_NAME = "Nature"


def journal_preset_path() -> Path:
    return Path(__file__).resolve().parents[2] / "config" / "journal_presets.json"


def load_journal_presets(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    source = Path(path) if path is not None else journal_preset_path()
    if not source.exists():
        raise FileNotFoundError(f"Journal preset config not found: {source}")
    with source.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("Journal preset config must be a JSON object.")
    presets: dict[str, dict[str, Any]] = {}
    for name, preset in payload.items():
        if isinstance(preset, dict):
            presets[str(name)] = dict(preset)
    return presets


def journal_preset_names(path: str | Path | None = None) -> list[str]:
    return list(load_journal_presets(path).keys())


def get_journal_preset(name: str = DEFAULT_PRESET_NAME, path: str | Path | None = None) -> dict[str, Any]:
    presets = load_journal_presets(path)
    for preset_name, preset in presets.items():
        if preset_name.casefold() == name.casefold() or str(preset.get("id", "")).casefold() == name.casefold():
            return copy.deepcopy(preset)
    raise KeyError(f"Journal preset not found: {name}")


def resolve_semantic_color(preset: dict[str, Any], role: str, fallback: str) -> str:
    palette = dict(preset.get("palette", {}))
    roles = dict(preset.get("semantic_roles", {}))
    value = roles.get(role, fallback)
    if isinstance(value, str) and value.startswith("#"):
        return value
    if isinstance(value, str) and value in palette:
        return str(palette[value])
    return fallback


def apply_matplotlib_preset(preset: dict[str, Any] | None = None) -> None:
    from matplotlib import rcParams

    preset = preset or get_journal_preset(DEFAULT_PRESET_NAME)
    typography = dict(preset.get("typography", {}))
    export = dict(preset.get("export", {}))
    axes = dict(preset.get("axes", {}))
    font_family = str(typography.get("font_family", preset.get("figure_font", "Arial")))
    fallbacks = [str(item) for item in typography.get("fallbacks", ["DejaVu Sans", "Liberation Sans"])]
    rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": [font_family, *fallbacks],
            "font.size": float(typography.get("base_font_size_pt", 7.0)),
            "svg.fonttype": export.get("svg_fonttype", "none"),
            "pdf.fonttype": int(export.get("pdf_fonttype", 42)),
            "ps.fonttype": int(export.get("ps_fonttype", 42)),
            "axes.spines.top": bool(axes.get("show_top_spine", False)),
            "axes.spines.right": bool(axes.get("show_right_spine", False)),
            "axes.linewidth": float(axes.get("axis_line_width_pt", 0.6)),
            "xtick.major.width": float(axes.get("tick_width_pt", 0.6)),
            "ytick.major.width": float(axes.get("tick_width_pt", 0.6)),
            "xtick.major.size": float(axes.get("tick_length_pt", 3.0)),
            "ytick.major.size": float(axes.get("tick_length_pt", 3.0)),
            "legend.frameon": False,
        }
    )


def plot_style_from_preset(preset: dict[str, Any] | None = None) -> dict[str, Any]:
    preset = preset or get_journal_preset(DEFAULT_PRESET_NAME)
    typography = dict(preset.get("typography", {}))
    axes = dict(preset.get("axes", {}))
    bars = dict(preset.get("bar_scatter_sem", {}))
    bracket = dict(preset.get("significance_bracket", {}))
    palette = dict(preset.get("palette", {}))
    return {
        "font_family": str(typography.get("font_family", preset.get("figure_font", "Arial"))),
        "font_size_pt": float(typography.get("base_font_size_pt", 7.0)),
        "axis_label_size_pt": float(typography.get("axis_label_size_pt", 8.0)),
        "tick_label_size_pt": float(typography.get("tick_label_size_pt", 7.0)),
        "legend_size_pt": float(typography.get("legend_size_pt", 7.0)),
        "panel_label_size_pt": float(typography.get("panel_label_size_pt", 8.0)),
        "panel_label_case": str(typography.get("panel_label_case", "lowercase")),
        "bar_fill_color": str(bars.get("bar_fill_color", "#FFFFFF")),
        "bar_edge_color": resolve_semantic_color(preset, "bar_edge", "#272727"),
        "bar_width_fraction": float(bars.get("bar_width_fraction", 0.58)),
        "bar_line_width": float(bars.get("bar_line_width_pt", 0.9)),
        "axis_line_width": float(axes.get("axis_line_width_pt", 0.6)),
        "tick_width": float(axes.get("tick_width_pt", 0.6)),
        "tick_length": float(axes.get("tick_length_pt", 3.0)),
        "point_shape": "circle",
        "point_size": float(bars.get("point_size_pt", 16.0)),
        "point_color": resolve_semantic_color(preset, "treatment_primary", "#0F4D92"),
        "point_edge_color": resolve_semantic_color(preset, "point_edge", "#272727"),
        "point_line_width": float(bars.get("point_line_width_pt", 0.35)),
        "jitter_fraction": float(bars.get("jitter_fraction", 0.08)),
        "sem_line_width": float(bars.get("sem_line_width_pt", 0.8)),
        "sem_cap_size": float(bars.get("sem_cap_size_pt", 3.0)),
        "sem_cap_thick": float(bars.get("sem_cap_thick_pt", 0.8)),
        "bracket_line_width": float(bracket.get("line_width_pt", 0.7)),
        "bracket_step_fraction": float(bracket.get("step_fraction", 0.16)),
        "bracket_height_fraction": float(bracket.get("height_fraction", 0.18)),
        "bracket_label_offset_fraction": float(bracket.get("label_offset_fraction", 0.22)),
        "show_top_spine": bool(axes.get("show_top_spine", False)),
        "show_right_spine": bool(axes.get("show_right_spine", False)),
        "series_colors": [
            str(palette.get("blue_main", "#0F4D92")),
            str(palette.get("green_3", "#8BCF8B")),
            str(palette.get("red_strong", "#B64342")),
            str(palette.get("teal", "#42949E")),
            str(palette.get("violet", "#9A4D8E")),
            str(palette.get("neutral_light", "#CFCECE")),
        ],
    }


def apply_plot_axes_style(ax, style: dict[str, Any]) -> None:
    """Apply shared text/axes tokens after a chart has created its labels."""
    if "background_color" in style:
        ax.figure.set_facecolor(style["background_color"])
        ax.set_facecolor(style["background_color"])
    if style.get('axes_box_aspect') is not None:
        ax.set_box_aspect(float(style['axes_box_aspect']))
    if 'x_tick_length' in style:
        ax.tick_params(axis='x',length=float(style['x_tick_length']))
    if 'x_tick_rotation' in style:
        angle = float(style['x_tick_rotation'])
        for label in ax.get_xticklabels():
            label.set_rotation(angle)
            label.set_ha('right' if angle > 0 else ('left' if angle < 0 else 'center'))
    family = str(style.get("font_family", "Arial"))
    weight = str(style.get("font_weight", "normal"))
    tick_size = float(style.get("tick_label_size_pt", style.get("font_size_pt", 7)))
    for label in ax.get_xticklabels() + ax.get_yticklabels() + [ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text()]:
        label.set_fontfamily(family)
        label.set_fontweight(weight)
        label.set_fontsize(tick_size)
    for label in [ax.xaxis.label, ax.yaxis.label, ax.title, *ax.texts]:
        label.set_fontfamily(family)
        label.set_fontweight(weight)
    for name, spine in ax.spines.items():
        spine.set_linewidth(float(style.get("axis_line_width", .6)))
        if name in ("top", "right"):
            spine.set_visible(bool(style.get(f"show_{name}_spine", False)))
    if "label_color" in style:
        color = style["label_color"]
        ax.tick_params(colors=color)
        for label in [ax.xaxis.label, ax.yaxis.label, ax.title, ax.xaxis.get_offset_text(), ax.yaxis.get_offset_text(), *ax.texts]:
            label.set_color(color)
        for spine in ax.spines.values():
            spine.set_color(color)
    if "gridline_visible" in style:
        ax.set_axisbelow(True)
        if style["gridline_visible"]:
            ax.grid(True, color=style.get("gridline_color", "#D0D0D0"), linewidth=.6)
        else:
            ax.grid(False)


def image_plate_style_from_preset(preset: dict[str, Any] | None = None) -> dict[str, Any]:
    preset = preset or get_journal_preset(DEFAULT_PRESET_NAME)
    image_plate = dict(preset.get("image_plate", {}))
    return {
        "background_color": str(image_plate.get("background_color", "#000000")),
        "context_color": str(image_plate.get("context_color", "#B8B8B8")),
        "channel_colors": [
            str(image_plate.get("channel_cyan", "#22D7E6")),
            str(image_plate.get("channel_magenta", "#FF2AD4")),
            str(image_plate.get("label_color", "#FFFFFF")),
            str(image_plate.get("context_color", "#B8B8B8")),
        ],
        "scale_bar_color": str(image_plate.get("scale_bar_color", "#FFFFFF")),
        "label_color": str(image_plate.get("label_color", "#FFFFFF")),
    }
