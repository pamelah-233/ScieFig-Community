from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

PLOT_PAYLOAD_SCHEMA_VERSION = 1
FAMILY_GROUPED = "grouped"
FAMILY_QPCR = "qpcr"
FAMILY_OBJECT_DISTRIBUTION = "object_distribution"
DEFAULT_GROUPED_CHART_KIND = "bar_dot"
DEFAULT_DISTRIBUTION_CHART_KIND = "frequency_bar"
GROUPED_CHART_KINDS = (
    "bar",
    "bar_dot",
    "box",
    "box_dot",
    "violin",
    "violin_dot",
    "dot",
    "line",
)
DISTRIBUTION_CHART_KINDS = (
    "frequency_bar",
    "frequency_line",
    "box",
    "box_dot",
    "violin",
    "violin_dot",
    "dot",
)
PLOT_PAYLOAD_FIELDS = (
    "schema_version",
    "family",
    "chart_kind",
    "style",
    "y_label",
    "unit",
    "source_module",
    "sample_unit",
    "statistics_result",
    "distribution_result",
    "target",
    "fold_change_rows",
    "error_mode",
    "statistics",
    "group_order",
    "title",
    "x_label",
    "frequency_mode",
)
REASON_SUMMARY_ONLY = "Summary-only data cannot draw raw-sample geometry."
REASON_VIOLIN_DEGENERATE = "Violin requires at least two non-constant raw values per group."
REASON_NO_GROUPS = "No plot groups available."
REASON_NO_VALUES = "No summary or raw values available."
REASON_FREQUENCY_NOT_GROUPED = "Frequency kinds belong to object-distribution plots."

_KNOWN_KINDS = frozenset(GROUPED_CHART_KINDS) | frozenset(DISTRIBUTION_CHART_KINDS)
_FREQUENCY_KINDS = frozenset(("frequency_bar", "frequency_line"))
_RAW_REQUIRED_KINDS = frozenset(("bar_dot", "box", "box_dot", "violin", "violin_dot", "dot"))
_SUMMARY_KINDS = frozenset(("bar", "line"))
_ZERO_BASELINE_KINDS = frozenset(("bar", "bar_dot", "line"))
_SUMMARY_EXTENT_KINDS = frozenset(("bar", "bar_dot", "line"))
_RAW_EXTENT_KINDS = frozenset(("bar_dot", "box", "box_dot", "violin", "violin_dot", "dot"))
_PAYLOAD_FAMILIES = frozenset((FAMILY_GROUPED, FAMILY_QPCR, FAMILY_OBJECT_DISTRIBUTION))

__all__ = [
    "PLOT_PAYLOAD_SCHEMA_VERSION",
    "FAMILY_GROUPED",
    "FAMILY_QPCR",
    "FAMILY_OBJECT_DISTRIBUTION",
    "DEFAULT_GROUPED_CHART_KIND",
    "DEFAULT_DISTRIBUTION_CHART_KIND",
    "GROUPED_CHART_KINDS",
    "DISTRIBUTION_CHART_KINDS",
    "PLOT_PAYLOAD_FIELDS",
    "REASON_SUMMARY_ONLY",
    "REASON_VIOLIN_DEGENERATE",
    "REASON_NO_GROUPS",
    "REASON_NO_VALUES",
    "REASON_FREQUENCY_NOT_GROUPED",
    "normalize_chart_kind",
    "chart_kind_options",
    "chart_kind_unavailable_reason",
    "draw_grouped_chart",
    "grouped_chart_y_layout",
    "build_plot_payload_figure",
    "copy_plot_payload",
    "validate_plot_payload",
]


def normalize_chart_kind(value: Any, default: str = DEFAULT_GROUPED_CHART_KIND) -> str:
    fallback = str(default)
    if value is None:
        return fallback
    token = str(value).strip().lower().replace("-", "_").replace("+", "_")
    if not token:
        return fallback
    if token in _KNOWN_KINDS:
        return token
    return fallback


def chart_kind_options(data_family: str = FAMILY_GROUPED) -> tuple[str, ...]:
    family = str(data_family or FAMILY_GROUPED).strip().lower()
    if family in {FAMILY_GROUPED, FAMILY_QPCR}:
        return GROUPED_CHART_KINDS
    if family in {FAMILY_OBJECT_DISTRIBUTION, "distribution"}:
        return DISTRIBUTION_CHART_KINDS
    raise ValueError(f"Unknown data family: {data_family!r}")


def _values_by_group(raw_groups: Any) -> tuple[list[str], dict[str, list[float]]]:
    order: list[str] = []
    mapping: dict[str, list[float]] = {}
    if raw_groups is None:
        return order, mapping
    if isinstance(raw_groups, Mapping):
        first_value = next(iter(raw_groups.values()), None) if raw_groups else None
        looks_like_rows = (
            "group" in raw_groups
            and "value" in raw_groups
            and not isinstance(raw_groups.get("value"), (list, tuple, np.ndarray))
        )
        if looks_like_rows:
            group = str(raw_groups.get("group", ""))
            if group:
                order.append(group)
                mapping[group] = [float(raw_groups["value"])]
            return order, mapping
        if isinstance(first_value, Mapping) and "value" in first_value and "mean" not in first_value:
            for group_name, row in raw_groups.items():
                group = str(row.get("group", group_name))
                if not group:
                    continue
                if group not in mapping:
                    order.append(group)
                    mapping[group] = []
                mapping[group].append(float(row["value"]))
            return order, mapping
        for key, values in raw_groups.items():
            group = str(key)
            if not group:
                continue
            if group not in mapping:
                order.append(group)
                mapping[group] = []
            if isinstance(values, (list, tuple, np.ndarray)):
                mapping[group].extend(float(item) for item in values)
            elif values is None:
                continue
            else:
                mapping[group].append(float(values))
        return order, mapping
    for row in raw_groups:
        group = str(row.get("group", ""))
        if not group:
            continue
        if group not in mapping:
            order.append(group)
            mapping[group] = []
        mapping[group].append(float(row["value"]))
    return order, mapping


def _summary_map(summaries: Any) -> tuple[list[str], dict[str, dict[str, float]]]:
    order: list[str] = []
    mapping: dict[str, dict[str, float]] = {}
    if not summaries:
        return order, mapping
    if isinstance(summaries, Mapping):
        first = next(iter(summaries.values()), None)
        if isinstance(first, Mapping) and "mean" in first:
            for key, row in summaries.items():
                group = str(key)
                if not group:
                    continue
                order.append(group)
                mapping[group] = {
                    "mean": float(row["mean"]),
                    "error": float(row.get("error", 0.0) or 0.0),
                }
            return order, mapping
    for row in summaries:
        group = str(row.get("group", ""))
        if not group:
            continue
        if group not in mapping:
            order.append(group)
        mapping[group] = {
            "mean": float(row["mean"]),
            "error": float(row.get("error", 0.0) or 0.0),
        }
    return order, mapping


def _has_finite_raw(mapping: Mapping[str, Sequence[float]]) -> bool:
    return any(np.isfinite(value) for values in mapping.values() for value in values)


def _finite_list(values: Sequence[float]) -> list[float]:
    return [float(value) for value in values if np.isfinite(value)]


def chart_kind_unavailable_reason(kind: Any, raw_groups: Any, *, has_raw: bool = True) -> str | None:
    resolved = normalize_chart_kind(kind)
    if resolved in _FREQUENCY_KINDS:
        return REASON_FREQUENCY_NOT_GROUPED
    _order, mapping = _values_by_group(raw_groups)
    finite_by_group = {group: _finite_list(values) for group, values in mapping.items()}
    any_finite = any(finite_by_group.values())
    if resolved in _RAW_REQUIRED_KINDS and (not has_raw or not any_finite):
        return REASON_SUMMARY_ONLY
    if resolved in _SUMMARY_KINDS and has_raw and not any_finite:
        return REASON_NO_VALUES
    if resolved in {"violin", "violin_dot"}:
        nonempty = [values for values in finite_by_group.values() if values]
        if not nonempty:
            return REASON_SUMMARY_ONLY
        for values in nonempty:
            if len(values) < 2 or max(values) == min(values):
                return REASON_VIOLIN_DEGENERATE
    return None


def _mean_error_for_group(
    group: str,
    raw_map: Mapping[str, Sequence[float]],
    summary_map: Mapping[str, Mapping[str, float]],
) -> tuple[float, float]:
    if group in summary_map:
        row = summary_map[group]
        return float(row["mean"]), float(row.get("error", 0.0) or 0.0)
    values = np.asarray(list(raw_map.get(group, [])), dtype=float)
    if values.size == 0:
        return 0.0, 0.0
    mean = float(np.mean(values))
    error = float(np.std(values, ddof=1) / np.sqrt(len(values))) if len(values) > 1 else 0.0
    return mean, error


def _finite_extent_values(
    kind: str,
    means: Sequence[float],
    errors: Sequence[float],
    finite_raw: Sequence[float],
) -> list[float]:
    values: list[float] = []
    if kind in _SUMMARY_EXTENT_KINDS:
        for mean, error in zip(means, errors):
            mean_f = float(mean)
            error_f = float(error) if np.isfinite(error) else 0.0
            if np.isfinite(mean_f):
                values.append(mean_f - error_f)
                values.append(mean_f + error_f)
    if kind in _RAW_EXTENT_KINDS:
        values.extend(float(value) for value in finite_raw if np.isfinite(value))
    return values


def _extent_from_values(values: Sequence[float]) -> tuple[float, float]:
    finite = [float(value) for value in values if np.isfinite(value)]
    if not finite:
        return 0.0, 1.0
    return float(np.min(finite)), float(np.max(finite))


def grouped_chart_y_layout(
    data_extent: Mapping[str, float],
    *,
    chart_kind: str,
    style: Mapping[str, Any] | None = None,
    comparison_count: int = 0,
) -> dict[str, float]:
    style = style or {}
    kind = normalize_chart_kind(chart_kind)
    data_ymin = float(data_extent["ymin"])
    data_ymax = float(data_extent["ymax"])
    data_span = data_ymax - data_ymin
    if not np.isfinite(data_span) or data_span <= 0.0:
        data_span = max(abs(data_ymax), abs(data_ymin), 1e-12)
    zero_baseline = kind in _ZERO_BASELINE_KINDS and data_ymin >= 0.0
    if zero_baseline:
        ylim_bottom = 0.0
        layout_span = data_ymax - 0.0
        if not np.isfinite(layout_span) or layout_span <= 0.0:
            layout_span = data_span
    else:
        layout_span = data_span
        ylim_bottom = data_ymin - layout_span * 0.12
    bracket_base = data_ymax + layout_span * 0.12
    step = layout_span * float(style.get("bracket_step_fraction", 0.16))
    ylim_top = max(
        data_ymax + layout_span * 0.25,
        bracket_base + step * (float(comparison_count) + 0.5),
    )
    return {
        "ylim_bottom": float(ylim_bottom),
        "ylim_top": float(ylim_top),
        "bracket_base": float(bracket_base),
        "bracket_step": float(step),
    }


def _point_marker(style: Mapping[str, Any]) -> str:
    return {"circle": "o", "o": "o", "square": "s", "diamond": "D"}.get(str(style.get("point_shape", "circle")), "o")


def _series_colors(style: Mapping[str, Any]) -> list[str]:
    colors = list(style.get("series_colors", []) or [])
    if colors:
        return [str(color) for color in colors]
    return [str(style.get("point_color", "#0F4D92"))]


def _scatter_raw(ax: Axes, x: np.ndarray, groups: Sequence[str], raw_map: Mapping[str, Sequence[float]], style: Mapping[str, Any]):
    rng = np.random.default_rng(42)
    marker = _point_marker(style)
    series_colors = _series_colors(style)
    jitter_fraction = float(style.get("jitter_fraction", 0.08))
    xs: list[float] = []
    ys: list[float] = []
    colors: list[str] = []
    for index, group in enumerate(groups):
        values = np.asarray(_finite_list(raw_map.get(group, [])), dtype=float)
        jitter = rng.uniform(-jitter_fraction, jitter_fraction, size=len(values))
        xs.extend((float(x[index]) + jitter).tolist())
        ys.extend(values.tolist())
        point_color = series_colors[index % len(series_colors)] if style.get("use_series_colors") else (style.get("point_color") or series_colors[index % len(series_colors)])
        colors.extend([str(point_color)] * len(values))
    if not xs:
        return None
    return ax.scatter(
        xs,
        ys,
        s=float(style.get("point_size", 16.0)),
        marker=marker,
        c=colors,
        edgecolor=str(style.get("point_edge_color", "#272727")),
        linewidth=float(style.get("point_line_width", 0.35)),
        zorder=3,
    )


def _style_box_artists(boxplot: dict[str, Any], style: Mapping[str, Any]) -> None:
    fill = str(style.get("bar_fill_color", "#FFFFFF"))
    edge = str(style.get("bar_edge_color", "#272727"))
    width = float(style.get("bar_line_width", 0.9))
    palette = _series_colors(style)
    for index, box in enumerate(boxplot.get("boxes", [])):
        box.set_facecolor(palette[index % len(palette)] if style.get("use_series_colors") else fill)
        box.set_edgecolor(edge)
        box.set_linewidth(width)
        box.set_zorder(2)
    for key in ("whiskers", "caps", "medians"):
        for line in boxplot.get(key, []):
            line.set_color(edge)
            line.set_linewidth(width)
            line.set_zorder(2)
    for flier in boxplot.get("fliers", []):
        flier.set_markeredgecolor(edge)
        flier.set_zorder(2)


def draw_grouped_chart(
    ax: Axes,
    raw_groups: Any,
    summaries: Any,
    *,
    chart_kind: Any,
    style: Mapping[str, Any] | None,
    group_order: Sequence[str] | None = None,
) -> dict[str, Any]:
    style = style or {}
    kind = normalize_chart_kind(chart_kind)
    raw_order, raw_map = _values_by_group(raw_groups)
    summary_order, summary_map = _summary_map(summaries)
    if group_order is None:
        groups = list(dict.fromkeys([*summary_order, *raw_order]))
    else:
        groups = [str(group) for group in group_order if str(group) and (str(group) in raw_map or str(group) in summary_map)]
    if not groups:
        raise ValueError(REASON_NO_GROUPS)
    selected_raw_map = {group: raw_map.get(group, []) for group in groups}
    has_raw = _has_finite_raw(selected_raw_map)
    reason = chart_kind_unavailable_reason(kind, selected_raw_map, has_raw=has_raw)
    if reason:
        raise ValueError(reason)
    if kind in {"box", "box_dot", "violin", "violin_dot", "dot"}:
        # Raw-only geometries cannot render an empty Matplotlib dataset. Empty
        # groups carry no observations and are therefore omitted; this also
        # keeps violin's explicit "empty groups are ignored" capability rule.
        groups = [group for group in groups if _finite_list(raw_map.get(group, []))]
        if not groups:
            raise ValueError(REASON_NO_GROUPS)
    x = np.arange(len(groups), dtype=float)
    means = []
    errors = []
    for group in groups:
        mean, error = _mean_error_for_group(group, raw_map, summary_map)
        means.append(mean)
        errors.append(error)
    finite_raw = [float(value) for group in groups for value in raw_map.get(group, []) if np.isfinite(value)]
    ymin, ymax = _extent_from_values(_finite_extent_values(kind, means, errors, finite_raw))
    artists: dict[str, Any] = {
        "bars": None,
        "scatter": None,
        "boxplot": None,
        "violin": None,
        "line": None,
    }
    bar_kwargs = {
        "width": float(style.get("bar_width_fraction", 0.58)),
        "color": str(style.get("bar_fill_color", "#FFFFFF")),
        "edgecolor": str(style.get("bar_edge_color", "#272727")),
        "linewidth": float(style.get("bar_line_width", 0.9)),
        "error_kw": {
            "elinewidth": float(style.get("sem_line_width", 0.8)),
            "ecolor": str(style.get("bar_edge_color", "#272727")),
            "capsize": float(style.get("sem_cap_size", 3.0)),
            "capthick": float(style.get("sem_cap_thick", 0.8)),
        },
        "zorder": 2,
    }
    if kind in {"bar", "bar_dot"}:
        if style.get("bar_use_series_colors", style.get("use_series_colors")):
            palette = _series_colors(style)
            bar_kwargs["color"] = [palette[index % len(palette)] for index in range(len(x))]
        artists["bars"] = ax.bar(x, means, yerr=errors, **bar_kwargs)
    if kind == "line":
        artists["line"] = ax.errorbar(
            x,
            means,
            yerr=errors,
            fmt="-o",
            color=str(style.get("bar_edge_color", "#272727")),
            linewidth=float(style.get("line_width", 1.2)),
            elinewidth=float(style.get("sem_line_width", 0.8)),
            capsize=float(style.get("sem_cap_size", 3.0)),
            capthick=float(style.get("sem_cap_thick", 0.8)),
            markersize=max(float(style.get("point_size", 16.0)) ** 0.5, 2.0),
            markerfacecolor=str(style.get("point_color") or _series_colors(style)[0]),
            markeredgecolor=str(style.get("point_edge_color", "#272727")),
            zorder=2,
        )
    box_data = [np.asarray(_finite_list(raw_map.get(group, [])), dtype=float) for group in groups]
    if kind in {"box", "box_dot"}:
        artists["boxplot"] = ax.boxplot(
            box_data,
            positions=list(x),
            widths=float(style.get("bar_width_fraction", 0.58)),
            whis=1.5,
            showfliers=(kind == "box"),
            patch_artist=True,
            manage_ticks=False,
            zorder=2,
        )
        _style_box_artists(artists["boxplot"], style)
    if kind in {"violin", "violin_dot"}:
        parts = ax.violinplot(
            box_data,
            positions=list(x),
            widths=float(style.get("bar_width_fraction", 0.58)),
            showmeans=False,
            showmedians=False,
            showextrema=(kind == "violin"),
        )
        edge = str(style.get("bar_edge_color", "#272727"))
        fill = str(style.get("bar_fill_color", "#FFFFFF"))
        palette = _series_colors(style)
        for index, body in enumerate(parts.get("bodies", [])):
            body.set_facecolor(palette[index % len(palette)] if style.get("use_series_colors") else fill)
            body.set_edgecolor(edge)
            body.set_alpha(0.85)
            body.set_linewidth(float(style.get("bar_line_width", 0.9)))
            body.set_zorder(2)
            vertices = body.get_paths()[0].vertices if body.get_paths() else None
            if vertices is not None and len(vertices):
                vertex_ys = np.asarray(vertices[:, 1], dtype=float)
                vertex_ys = vertex_ys[np.isfinite(vertex_ys)]
                if vertex_ys.size:
                    ymin = min(ymin, float(np.min(vertex_ys)))
                    ymax = max(ymax, float(np.max(vertex_ys)))
        for key in ("cmaxes", "cmins", "cbars"):
            artist = parts.get(key)
            if artist is not None:
                artist.set_color(edge)
                artist.set_linewidth(float(style.get("bar_line_width", 0.9)))
        artists["violin"] = parts
    if kind in {"bar_dot", "box_dot", "violin_dot", "dot"}:
        artists["scatter"] = _scatter_raw(ax, x, groups, raw_map, style)
    return {
        "positions": {group: float(x[index]) for index, group in enumerate(groups)},
        "artists": artists,
        "data_extent": {"ymin": float(ymin), "ymax": float(ymax)},
        "group_order": list(groups),
        "chart_kind": kind,
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not np.isfinite(number):
            raise ValueError("Plot payload contains a non-JSON value.")
        return number
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    raise ValueError("Plot payload contains a non-JSON value.")


def copy_plot_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Plot payload must be an object.")
    copied: dict[str, Any] = {}
    for field in PLOT_PAYLOAD_FIELDS:
        if field not in payload:
            continue
        try:
            copied[field] = json.loads(json.dumps(_json_safe(payload[field])))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Plot payload field {field!r} is not JSON-safe.") from exc
    return copied


def _require_mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Plot payload field {field!r} must be an object.")
    return value


def validate_plot_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy_plot_payload(payload)
    if type(copied.get("schema_version")) is not int or copied.get("schema_version") != PLOT_PAYLOAD_SCHEMA_VERSION:
        raise ValueError("Unsupported plot payload schema_version.")
    family = copied.get("family")
    if not isinstance(family, str) or family not in _PAYLOAD_FAMILIES:
        raise ValueError(f"Unknown plot payload family: {family!r}")
    required = ("chart_kind", "style", "y_label", "unit", "source_module", "sample_unit")
    missing = [field for field in required if field not in copied]
    if missing:
        raise ValueError(f"Plot payload missing fields: {missing}")
    for field in ("chart_kind", "y_label", "unit", "source_module", "sample_unit"):
        if not isinstance(copied.get(field), str):
            raise ValueError(f"Plot payload field {field!r} must be a string.")
    if not isinstance(copied.get("style"), dict):
        raise ValueError("Plot payload field 'style' must be an object.")
    copied["chart_kind"] = normalize_chart_kind(
        copied.get("chart_kind"),
        default=_family_default_kind(str(family)),
    )
    if str(copied.get("chart_kind")) not in chart_kind_options(str(family)):
        raise ValueError(f"Unsupported chart kind {copied.get('chart_kind')!r} for family {family!r}.")
    if "group_order" in copied:
        if not isinstance(copied["group_order"], list) or not all(isinstance(item, str) for item in copied["group_order"]):
            raise ValueError("Plot payload field 'group_order' must be an array of strings.")
    if "frequency_mode" in copied and copied["frequency_mode"] not in {"absolute", "percent"}:
        raise ValueError("Plot payload field 'frequency_mode' must be 'absolute' or 'percent'.")
    if family == FAMILY_GROUPED:
        _require_mapping(copied.get("statistics_result"), "statistics_result")
    elif family == FAMILY_OBJECT_DISTRIBUTION:
        _require_mapping(copied.get("distribution_result"), "distribution_result")
    else:
        if not isinstance(copied.get("fold_change_rows"), list):
            raise ValueError("Plot payload field 'fold_change_rows' must be an array.")
        if "target" not in copied or "error_mode" not in copied:
            raise ValueError("qPCR plot payload requires target and error_mode.")
        if not isinstance(copied.get("target"), str) or not isinstance(copied.get("error_mode"), str):
            raise ValueError("qPCR plot payload target and error_mode must be strings.")
        if copied.get("error_mode") not in {"sem", "sd", ""}:
            raise ValueError("Plot payload field 'error_mode' must be 'sem', 'sd', or ''.")
        _require_mapping(copied.get("statistics"), "statistics")
    return copied


def _family_default_kind(family: str) -> str:
    if family == FAMILY_OBJECT_DISTRIBUTION:
        return DEFAULT_DISTRIBUTION_CHART_KIND
    return DEFAULT_GROUPED_CHART_KIND


def _reorder_plot_data(plot_data: Any, group_order: Sequence[str]) -> list[dict[str, Any]]:
    rows = [dict(row) for row in list(plot_data or [])]
    ordered: list[dict[str, Any]] = []
    seen: set[int] = set()
    for group in group_order:
        for index, row in enumerate(rows):
            if index in seen:
                continue
            if str(row.get("group", "")) == str(group):
                ordered.append(row)
                seen.add(index)
    ordered.extend(row for index, row in enumerate(rows) if index not in seen)
    return ordered


def build_plot_payload_figure(
    payload: Mapping[str, Any],
    *,
    chart_kind: Any = None,
    style_override: Mapping[str, Any] | None = None,
) -> Figure:
    copied = validate_plot_payload(payload)
    family = str(copied["family"])
    family_default = _family_default_kind(family)
    payload_kind = normalize_chart_kind(copied.get("chart_kind"), default=family_default)
    kind = payload_kind if chart_kind is None else normalize_chart_kind(chart_kind, default=payload_kind)
    style = dict(copied.get("style") or {})
    style.update(dict(style_override or {}))
    y_label = str(copied.get("y_label") or "")
    if family == FAMILY_GROUPED:
        from core.plotting.statistics_plot import build_group_statistics_figure

        statistics_result = dict(copied["statistics_result"])
        group_order = copied.get("group_order")
        if group_order:
            statistics_result["plot_data"] = _reorder_plot_data(statistics_result.get("plot_data"), group_order)
        return build_group_statistics_figure(
            statistics_result,
            style=style,
            y_label=y_label,
            chart_kind=kind,
        )
    if family == FAMILY_QPCR:
        import pandas as pd

        from core.plotting.qpcr_plot import build_qpcr_figure

        frame = pd.DataFrame(list(copied.get("fold_change_rows") or []))
        return build_qpcr_figure(
            frame,
            dict(copied.get("statistics") or {}),
            target=copied.get("target"),
            y_label=y_label or "Relative mRNA expression",
            title=copied.get("title"),
            style=style,
            chart_kind=kind,
        )
    from core.plotting.statistics_plot import build_distribution_comparison_figure

    return build_distribution_comparison_figure(
        dict(copied["distribution_result"]),
        style=style,
        x_label=str(copied.get("x_label") or y_label or "Object area (px²)"),
        y_label=y_label or None,
        frequency_mode=str(copied.get("frequency_mode") or "absolute"),
        chart_kind=kind,
    )
