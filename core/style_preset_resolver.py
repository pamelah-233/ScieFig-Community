from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from core.style_preset_registry import StylePresetRegistry, StylePresetRegistryError, StylePresetRecord

DEFAULT_PRESET_NAME = "Nature"


NUMERIC_STYLE_TOKENS = {
    "axes_box_aspect",
    "x_tick_length",
    "x_tick_rotation",
    "font_size_pt",
    "axis_label_size_pt",
    "tick_label_size_pt",
    "legend_size_pt",
    "panel_label_size_pt",
    "bar_width_fraction",
    "bar_line_width",
    "axis_line_width",
    "tick_width",
    "tick_length",
    "point_size",
    "point_line_width",
    "jitter_fraction",
    "sem_line_width",
    "sem_cap_size",
    "sem_cap_thick",
    "bracket_line_width",
    "bracket_step_fraction",
    "bracket_height_fraction",
    "bracket_label_offset_fraction",
    "line_width",
    "scale_bar_line_width",
    "frame_line_width",
}

BOOLEAN_STYLE_TOKENS = {
    "bar_use_series_colors",
    "gridline_visible",
    "use_series_colors",
    "show_top_spine",
    "show_right_spine",
    "show_comparison_labels",
    "show_x_labels",
    "show_y_label",
}

STRING_STYLE_TOKENS = {
    "font_weight",
    "font_family",
    "panel_label_case",
    "point_shape",
}

COLOR_STYLE_TOKENS = {
    "bar_fill_color",
    "bar_edge_color",
    "point_color",
    "point_edge_color",
    "background_color",
    "panel_background_color",
    "gridline_color",
    "scale_bar_color",
    "label_color",
    "frame_color",
    "context_color",
}

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


@dataclass(frozen=True, slots=True)
class RuntimeStylePresetSelection:
    preset_id: str
    registry_path: Path


@dataclass(frozen=True, slots=True)
class RuntimeStylePresetStatus:
    ok: bool
    source: str
    message: str
    preset_id: str | None = None
    display_name: str | None = None
    registry_path: str | None = None


_runtime_selection: RuntimeStylePresetSelection | None = None
_last_status = RuntimeStylePresetStatus(
    ok=True,
    source="builtin",
    message="No saved style is active.",
)


def clear_runtime_active_style_preset() -> RuntimeStylePresetStatus:
    global _runtime_selection, _last_status
    _runtime_selection = None
    _last_status = RuntimeStylePresetStatus(
        ok=True,
        source="builtin",
        message="Saved style cleared; builtin preset fallback is active.",
    )
    return _last_status


def runtime_active_style_preset_selection() -> RuntimeStylePresetSelection | None:
    if _runtime_selection is None:
        return None
    return RuntimeStylePresetSelection(
        preset_id=_runtime_selection.preset_id,
        registry_path=Path(_runtime_selection.registry_path),
    )


def last_runtime_style_preset_status() -> RuntimeStylePresetStatus:
    return _last_status


def set_runtime_active_style_preset(
    preset_id: str | None,
    registry_path: str | Path | None,
) -> RuntimeStylePresetStatus:
    global _runtime_selection, _last_status
    normalized_id = str(preset_id or "").strip()
    if not normalized_id:
        return clear_runtime_active_style_preset()
    if registry_path is None:
        _runtime_selection = None
        _last_status = RuntimeStylePresetStatus(
            ok=False,
            source="builtin",
            message="Saved style activation failed: storage path is not configured.",
            preset_id=normalized_id,
        )
        return _last_status

    selection = RuntimeStylePresetSelection(preset_id=normalized_id, registry_path=Path(registry_path))
    record, status = _load_runtime_record(selection)
    if record is None:
        _runtime_selection = None
        _last_status = status
        return status

    _runtime_selection = selection
    _last_status = RuntimeStylePresetStatus(
        ok=True,
        source="runtime_registry",
        message=f"Saved style active: {record.display_name}",
        preset_id=record.preset_id,
        display_name=record.display_name,
        registry_path=str(selection.registry_path),
    )
    return _last_status


def resolve_builtin_journal_preset(preset_name: str | None = None) -> dict[str, Any]:
    from core.plotting.style_presets import get_journal_preset

    try:
        return get_journal_preset(preset_name or DEFAULT_PRESET_NAME)
    except KeyError:
        return get_journal_preset(DEFAULT_PRESET_NAME)


def resolve_plot_style(
    preset_name: str | None = None,
    *,
    saved_style: dict[str, Any] | None = None,
    local_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from core.plotting.style_presets import plot_style_from_preset

    global _last_status
    style = plot_style_from_preset(resolve_builtin_journal_preset(preset_name))
    runtime_style = _runtime_plot_style()
    if runtime_style:
        style.update(runtime_style)
    style.update(deepcopy(dict(saved_style or {})))
    style.update(deepcopy(dict(local_override or {})))
    return style


def runtime_plot_style_override() -> dict[str, Any]:
    return _runtime_plot_style()


def resolve_image_plate_style(preset_name: str | None = None) -> dict[str, Any]:
    from core.plotting.style_presets import image_plate_style_from_preset

    style = image_plate_style_from_preset(resolve_builtin_journal_preset(preset_name))
    runtime_style = _runtime_plot_style()
    if not runtime_style:
        return style
    return _apply_runtime_image_plate_style(style, runtime_style)


def resolve_runtime_image_plate_style_if_active(preset_name: str | None = None) -> dict[str, Any] | None:
    if _runtime_selection is None:
        return None
    from core.plotting.style_presets import image_plate_style_from_preset

    runtime_style = _runtime_plot_style()
    if not runtime_style:
        return None
    style = image_plate_style_from_preset(resolve_builtin_journal_preset(preset_name))
    return _apply_runtime_image_plate_style(style, runtime_style)


def _apply_runtime_image_plate_style(style: dict[str, Any], runtime_style: dict[str, Any]) -> dict[str, Any]:
    channel_colors = [str(item) for item in runtime_style.get("series_colors", []) if str(item).strip()]
    point_color = str(runtime_style.get("point_color", "")).strip()
    if point_color:
        channel_colors = [point_color, *channel_colors[1:]]
    if channel_colors:
        merged = list(style.get("channel_colors", []))
        for index, color in enumerate(channel_colors[: len(merged)]):
            merged[index] = color
        style["channel_colors"] = merged
    background_color = str(runtime_style.get("background_color", "")).strip()
    if background_color:
        style["background_color"] = background_color
    scale_color = str(runtime_style.get("bar_edge_color", "") or runtime_style.get("point_edge_color", "")).strip()
    if scale_color:
        style["scale_bar_color"] = scale_color
        style["label_color"] = scale_color
        style["frame_color"] = scale_color
    for color_key in ("scale_bar_color", "label_color", "frame_color", "context_color"):
        color_value = str(runtime_style.get(color_key, "")).strip()
        if color_value:
            style[color_key] = color_value
    font_family = str(runtime_style.get("font_family", "")).strip()
    if font_family:
        style["label_font_family"] = font_family
    for source_key, target_key in (
        ("font_size_pt", "label_font_size_pt"),
        ("bar_line_width", "scale_bar_line_width"),
        ("scale_bar_line_width", "scale_bar_line_width"),
        ("axis_line_width", "frame_line_width"),
        ("frame_line_width", "frame_line_width"),
    ):
        if source_key not in runtime_style:
            continue
        try:
            value = float(runtime_style[source_key])
        except (TypeError, ValueError):
            continue
        if value > 0:
            style[target_key] = value
    return style


def _runtime_plot_style() -> dict[str, Any]:
    global _last_status
    if _runtime_selection is None:
        return {}
    record, status = _load_runtime_record(_runtime_selection)
    _last_status = status
    if record is None:
        return {}
    return deepcopy(dict(record.plot_style))


def runtime_style_cache_token() -> str:
    if _runtime_selection is None:
        return ""
    record, status = _load_runtime_record(_runtime_selection)
    if record is None:
        return f"fallback:{status.message}"
    style_text = json.dumps(record.plot_style, sort_keys=True, ensure_ascii=True, default=str)
    style_hash = hashlib.sha256(style_text.encode("utf-8")).hexdigest()
    return f"{record.preset_id}:{record.updated_at}:{style_hash}"


def _load_runtime_record(
    selection: RuntimeStylePresetSelection,
) -> tuple[StylePresetRecord | None, RuntimeStylePresetStatus]:
    registry_path = Path(selection.registry_path)
    if not registry_path.exists():
        return None, RuntimeStylePresetStatus(
            ok=False,
            source="builtin",
            message=f"Saved style not found: storage file does not exist at {registry_path}",
            preset_id=selection.preset_id,
            registry_path=str(registry_path),
        )
    try:
        record = StylePresetRegistry(registry_path).get_preset(selection.preset_id)
    except (StylePresetRegistryError, ValueError) as exc:
        return None, RuntimeStylePresetStatus(
            ok=False,
            source="builtin",
            message=f"Saved style unavailable: {exc}",
            preset_id=selection.preset_id,
            registry_path=str(registry_path),
        )
    if record is None:
        return None, RuntimeStylePresetStatus(
            ok=False,
            source="builtin",
            message=f"Saved style not found: {selection.preset_id}",
            preset_id=selection.preset_id,
            registry_path=str(registry_path),
        )
    invalid_message = _runtime_style_validation_error(record.plot_style)
    if invalid_message:
        return None, RuntimeStylePresetStatus(
            ok=False,
            source="builtin",
            message=f"Saved style has invalid style token: {invalid_message}",
            preset_id=selection.preset_id,
            display_name=record.display_name,
            registry_path=str(registry_path),
        )
    return record, RuntimeStylePresetStatus(
        ok=True,
        source="runtime_registry",
        message=f"Saved style active: {record.display_name}",
        preset_id=record.preset_id,
        display_name=record.display_name,
        registry_path=str(registry_path),
    )


def _runtime_style_validation_error(plot_style: dict[str, Any]) -> str:
    if not isinstance(plot_style, dict):
        return "plot_style must be a JSON object"
    for key, value in plot_style.items():
        token = str(key)
        if token in NUMERIC_STYLE_TOKENS:
            try:
                float(value)
            except (TypeError, ValueError):
                return f"{token} must be numeric"
        elif token in BOOLEAN_STYLE_TOKENS:
            if not isinstance(value, bool):
                return f"{token} must be boolean"
        elif token in STRING_STYLE_TOKENS:
            if not isinstance(value, str):
                return f"{token} must be a string"
        elif token in COLOR_STYLE_TOKENS:
            if not isinstance(value, str):
                return f"{token} must be a string"
            if not HEX_COLOR_RE.fullmatch(value):
                return f"{token} must be a #RRGGBB color"
        elif token in {"series_colors", "channel_colors"}:
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                return f"{token} must be a list of strings"
            for index, color in enumerate(value):
                if not HEX_COLOR_RE.fullmatch(color):
                    return f"{token}[{index}] must be a #RRGGBB color"
    return ""
