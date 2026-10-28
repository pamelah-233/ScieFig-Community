from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any

from core.layout_export_items import (
    EXPORT_ITEM_FULL_LAYOUT,
    EXPORT_ITEM_INDIVIDUAL_PLOT,
    EXPORT_ITEM_OVERLAY,
    EXPORT_ITEM_STATISTICS_CHART,
    LayoutExportItem,
)
from core.plotting.chart_kinds import (
    DEFAULT_DISTRIBUTION_CHART_KIND,
    DEFAULT_GROUPED_CHART_KIND,
    FAMILY_GROUPED,
    FAMILY_OBJECT_DISTRIBUTION,
    FAMILY_QPCR,
    PLOT_PAYLOAD_SCHEMA_VERSION,
    REASON_FREQUENCY_NOT_GROUPED,
    REASON_NO_GROUPS,
    REASON_NO_VALUES,
    REASON_SUMMARY_ONLY,
    REASON_VIOLIN_DEGENERATE,
    chart_kind_options,
    chart_kind_unavailable_reason,
    copy_plot_payload,
    normalize_chart_kind,
    validate_plot_payload,
)
from core.utils.hashing import sha256_json
from core.utils.i18n import tr
from core.utils.svg_text import apply_svg_text_overrides, normalize_svg_text_overrides, svg_text_entries

PLOT_PAYLOAD_DATA_KEY = "plot_payload"
CHART_KIND_DATA_KEY = "chart_kind"
UNSUPPORTED_PLOT_TYPES = frozenset({"line_profile", "colocalization", "measurement_overlay"})
STATISTICAL_EXPORT_TYPES = frozenset({EXPORT_ITEM_INDIVIDUAL_PLOT, EXPORT_ITEM_STATISTICS_CHART})
IMAGE_EXPORT_TYPES = frozenset({EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_OVERLAY})

_NUMERIC_TICK_RE = re.compile(r"^[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?%?$")
_P_VALUE_RE = re.compile(r"^p\s*[=<>≤≥]\s*[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+\-]?\d+)?$", re.I)
_BACKEND_REASON_KEYS = {
    REASON_SUMMARY_ONLY: "chart_kind_reason_summary_only",
    REASON_VIOLIN_DEGENERATE: "chart_kind_reason_violin_degenerate",
    REASON_NO_GROUPS: "chart_kind_reason_no_groups",
    REASON_NO_VALUES: "chart_kind_reason_no_values",
    REASON_FREQUENCY_NOT_GROUPED: "chart_kind_reason_frequency_not_grouped",
}


def chart_kind_label_key(kind: str) -> str:
    return f"chart_kind_{normalize_chart_kind(kind)}"


def translate_unavailable_reason(reason: str | None) -> str:
    if not reason:
        return ""
    key = _BACKEND_REASON_KEYS.get(str(reason), "")
    if key:
        return tr(key)
    if str(reason) in {tr("chart_kind_reason_requires_source"), "chart_kind_reason_requires_source"}:
        return tr("chart_kind_reason_requires_source")
    return tr(str(reason)) if False else str(reason)


def family_default_kind(family: str) -> str:
    token = str(family or FAMILY_GROUPED).strip().lower()
    if token in {FAMILY_OBJECT_DISTRIBUTION, "distribution"}:
        return DEFAULT_DISTRIBUTION_CHART_KIND
    return DEFAULT_GROUPED_CHART_KIND


def panel_plot_payload(panel: Any) -> dict[str, Any] | None:
    data = getattr(panel, "data", None)
    if not isinstance(data, dict):
        return None
    raw = data.get(PLOT_PAYLOAD_DATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    try:
        return validate_plot_payload(raw)
    except ValueError:
        try:
            return copy_plot_payload(raw)
        except ValueError:
            return None


def attach_plot_payload(panel: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    copied = copy_plot_payload(payload)
    if "schema_version" not in copied:
        copied["schema_version"] = PLOT_PAYLOAD_SCHEMA_VERSION
    validated = validate_plot_payload(copied)
    data = getattr(panel, "data", None)
    if not isinstance(data, dict):
        raise ValueError("Panel has no data mapping for plot payload.")
    data[PLOT_PAYLOAD_DATA_KEY] = validated
    kind = str(validated.get("chart_kind") or "").strip()
    if kind and CHART_KIND_DATA_KEY not in data:
        data[CHART_KIND_DATA_KEY] = kind
    return validated


def payload_family(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return FAMILY_GROUPED
    family = str(payload.get("family") or FAMILY_GROUPED).strip().lower()
    if family in {FAMILY_OBJECT_DISTRIBUTION, "distribution"}:
        return FAMILY_OBJECT_DISTRIBUTION
    if family == FAMILY_QPCR:
        return FAMILY_QPCR
    return FAMILY_GROUPED


def effective_composer_chart_kind(panel: Any, ref: Any = None, payload: Mapping[str, Any] | None = None) -> str:
    payload = payload if payload is not None else panel_plot_payload(panel)
    family = payload_family(payload)
    default = family_default_kind(family)
    override = str(getattr(ref, "chart_kind", "") or "").strip()
    if override:
        return normalize_chart_kind(override, default=default)
    if payload:
        return normalize_chart_kind(payload.get("chart_kind"), default=default)
    data = getattr(panel, "data", {}) if panel is not None else {}
    if isinstance(data, dict) and data.get(CHART_KIND_DATA_KEY):
        return normalize_chart_kind(data.get(CHART_KIND_DATA_KEY), default=default)
    return default


def export_item_type_of(panel: Any) -> str:
    data = getattr(panel, "data", {}) if panel is not None else {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("export_item_type") or "").strip()


def plot_type_of(panel: Any) -> str:
    data = getattr(panel, "data", {}) if panel is not None else {}
    if not isinstance(data, dict):
        return ""
    return str(data.get("plot_type") or "").strip()


def is_image_like_composer_panel(panel: Any) -> bool:
    panel_type = str(getattr(panel, "panel_type", "") or "")
    if panel_type == "derived_plot":
        return True
    export_type = export_item_type_of(panel)
    if export_type in IMAGE_EXPORT_TYPES:
        return True
    if plot_type_of(panel) in UNSUPPORTED_PLOT_TYPES:
        return True
    return False


def is_statistical_composer_candidate(panel: Any) -> bool:
    panel_type = str(getattr(panel, "panel_type", "") or "")
    if panel_type in {"qPCR_bar_scatter", "qpcr"}:
        return True
    if panel_type == "derived_plot":
        return False
    export_type = export_item_type_of(panel)
    if export_type in IMAGE_EXPORT_TYPES:
        return False
    if plot_type_of(panel) in UNSUPPORTED_PLOT_TYPES:
        return False
    if export_type in STATISTICAL_EXPORT_TYPES:
        return True
    return panel_plot_payload(panel) is not None


def source_context_matches(panel: Any, source_panel: Any, current_hash: str | None) -> bool:
    data = getattr(panel, "data", {}) if panel is not None else {}
    if not isinstance(data, dict) or source_panel is None:
        return False
    saved_panel = str(data.get("source_panel_uuid") or "").strip()
    if saved_panel and saved_panel != str(getattr(source_panel, "panel_uuid", "")):
        return False
    saved_hash = str(data.get("source_context_hash") or "").strip()
    current = str(current_hash or "").strip()
    return bool(saved_hash) and bool(current) and saved_hash == current


def layout_export_item_from_panel(panel: Any) -> LayoutExportItem | None:
    data = getattr(panel, "data", None)
    if not isinstance(data, dict):
        return None
    export_type = str(data.get("export_item_type") or "").strip()
    export_key = str(data.get("export_item_key") or "").strip()
    source_module = str(data.get("source_module") or "").strip()
    source_panel_uuid = str(data.get("source_panel_uuid") or "").strip()
    if not export_type or not export_key or not source_module or not source_panel_uuid:
        return None
    try:
        width = float(data.get("preferred_width_mm") or 40.0)
        height = float(data.get("preferred_height_mm") or 30.0)
        dpi = int(data.get("source_dpi") or 300)
        return LayoutExportItem(
            export_item_id=str(data.get("export_item_id") or f"{source_panel_uuid}:{export_key}"),
            export_item_type=export_type,
            source_module=source_module,
            source_panel_uuid=source_panel_uuid,
            export_item_key=export_key,
            source_dpi=max(1, dpi),
            preferred_width_mm=max(0.1, width),
            preferred_height_mm=max(0.1, height),
            source_record_id=str(data.get("source_record_id") or ""),
            source_record_namespace=str(data.get("source_record_namespace") or "visual_layout_asset"),
            source_context_hash=str(data.get("source_context_hash") or ""),
            created_from_layout_object_id=str(data.get("created_from_layout_object_id") or ""),
            image_path=str(data.get("image_path") or ""),
            source_format=str(data.get("source_format") or ""),
            source_width_px=data.get("source_width_px"),
            source_height_px=data.get("source_height_px"),
        )
    except ValueError:
        return None


def hydrate_legacy_plot_payload(
    panel: Any,
    *,
    source_panel: Any,
    current_source_context_hash: str,
    plot_payload_for_export_item,
) -> dict[str, Any] | None:
    existing = panel_plot_payload(panel)
    if existing is not None:
        return existing
    if plot_payload_for_export_item is None or source_panel is None:
        return None
    if not source_context_matches(panel, source_panel, current_source_context_hash):
        return None
    export_item = layout_export_item_from_panel(panel)
    if export_item is None:
        return None
    payload = plot_payload_for_export_item(source_panel, export_item)
    if not isinstance(payload, Mapping):
        return None
    attached = attach_plot_payload(panel, payload)
    return attached


def qpcr_live_plot_payload(panel: Any, *, chart_kind: str | None = None) -> dict[str, Any] | None:
    data = getattr(panel, "data", {}) if panel is not None else {}
    analysis = getattr(panel, "analysis", {}) if panel is not None else {}
    style = getattr(panel, "style", {}) if panel is not None else {}
    if not isinstance(data, dict):
        return None
    rows = list(data.get("processed_data", {}).get("fold_change", []) or [])
    if not rows:
        return None
    records = []
    if hasattr(panel, "layout_plot_records"):
        try:
            records = list(panel.layout_plot_records() or [])
        except Exception:
            records = list(data.get("qpcr_layout", {}).get("plots", []) or [])
    # Live Composer panels historically render the first processed target,
    # independently of which source Layout plots are enabled.
    target = next((str(row.get("target") or "") for row in rows if isinstance(row, Mapping) and row.get("target")), "")
    record_style: dict[str, Any] = {}
    record_kind = ""
    for record in records:
        if isinstance(record, Mapping) and str(record.get("target") or "") == target:
            record_style = dict(record.get("style") or {})
            record_kind = str(record.get("chart_kind") or "")
            break
    if not target:
        target = next(
            (
                str(row.get("target") or "")
                for row in rows
                if isinstance(row, Mapping) and str(row.get("target") or "")
            ),
            "",
        )
    if target:
        rows = [
            row
            for row in rows
            if isinstance(row, Mapping) and str(row.get("target") or "") == target
        ]
    merged_style = dict(style or {})
    merged_style.update(record_style)
    kind = chart_kind or record_kind or DEFAULT_GROUPED_CHART_KIND
    error_mode = str(analysis.get("error_bar") or analysis.get("error_mode") or "sem").strip().lower()
    payload = {
        "schema_version": PLOT_PAYLOAD_SCHEMA_VERSION,
        "family": FAMILY_QPCR,
        "chart_kind": kind,
        "style": merged_style,
        "y_label": "Relative mRNA expression",
        "unit": "fold",
        "source_module": str(getattr(panel, "source_module", "") or "qpcr"),
        "sample_unit": "biological_replicate",
        "target": target,
        "fold_change_rows": rows,
        "error_mode": error_mode,
        "statistics": dict(analysis.get("statistics") or {}),
    }
    return validate_plot_payload(payload)


def raw_groups_from_payload(payload: Mapping[str, Any]) -> Any:
    family = payload_family(payload)
    if family == FAMILY_QPCR:
        target = str(payload.get("target") or "")
        rows = []
        for row in list(payload.get("fold_change_rows") or []):
            if not isinstance(row, Mapping):
                continue
            if target and str(row.get("target") or "") != target:
                continue
            group = str(row.get("group") or "")
            if not group:
                continue
            value = row.get("fold_change")
            if value is None:
                continue
            rows.append({"group": group, "value": value})
        return rows
    if family == FAMILY_OBJECT_DISTRIBUTION:
        mapping: dict[str, list[float]] = {}
        for series in list(payload.get("distribution_result", {}).get("series") or []):
            if not isinstance(series, Mapping):
                continue
            label = str(series.get("lane_label") or series.get("group") or "")
            if not label:
                continue
            values = series.get("values")
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes)):
                mapping[label] = [float(item) for item in values]
        return mapping
    return list(payload.get("statistics_result", {}).get("plot_data") or [])


def payload_has_raw_values(payload: Mapping[str, Any]) -> bool:
    groups = raw_groups_from_payload(payload)
    if isinstance(groups, Mapping):
        return any(bool(values) for values in groups.values())
    if isinstance(groups, Sequence):
        return any(isinstance(row, Mapping) and row.get("value") is not None for row in groups)
    return False


def payload_kind_unavailable_reason(payload: Mapping[str, Any], kind: str) -> str | None:
    family = payload_family(payload)
    default = family_default_kind(family)
    token = normalize_chart_kind(kind, default=default)
    options = chart_kind_options(family)
    if token not in options:
        return REASON_FREQUENCY_NOT_GROUPED if family != FAMILY_OBJECT_DISTRIBUTION else REASON_NO_VALUES
    if family == FAMILY_OBJECT_DISTRIBUTION and token in {"frequency_bar", "frequency_line"}:
        series = list(payload.get("distribution_result", {}).get("series") or [])
        if not series:
            return REASON_NO_GROUPS
        has_values = False
        for item in series:
            if isinstance(item, Mapping):
                values = item.get("values")
                if isinstance(values, Sequence) and not isinstance(values, (str, bytes)) and list(values):
                    has_values = True
                    break
        return None if has_values else REASON_NO_VALUES
    has_raw = payload_has_raw_values(payload)
    return chart_kind_unavailable_reason(token, raw_groups_from_payload(payload), has_raw=has_raw)


def kind_availability(payload: Mapping[str, Any]) -> tuple[tuple[str, ...], dict[str, str]]:
    family = payload_family(payload)
    options = chart_kind_options(family)
    reasons: dict[str, str] = {}
    for kind in options:
        reason = payload_kind_unavailable_reason(payload, kind)
        if reason:
            reasons[kind] = reason
    return options, reasons


def composer_can_rerender_plot(panel: Any, ref: Any = None, payload: Mapping[str, Any] | None = None) -> bool:
    if payload is None:
        if str(getattr(panel, "panel_type", "") or "") in {"qPCR_bar_scatter", "qpcr"}:
            payload = qpcr_live_plot_payload(panel, chart_kind=getattr(ref, "chart_kind", None))
        else:
            payload = panel_plot_payload(panel)
    return payload is not None and not is_image_like_composer_panel(panel)


def plot_payload_cache_key(
    panel: Any,
    ref: Any,
    payload: Mapping[str, Any],
    *,
    chart_kind: str,
    width: int,
    height: int,
    dpi: int,
    svg_text_overrides: Mapping[str, Any] | None = None,
) -> tuple[Any, ...]:
    if svg_text_overrides is None:
        overrides = {}
        data = getattr(panel, "data", {})
        if isinstance(data, dict):
            overrides = normalize_svg_text_overrides(data.get("svg_text_overrides", {}))
    else:
        overrides = normalize_svg_text_overrides(svg_text_overrides)
    return (
        str(getattr(panel, "panel_uuid", "")),
        str(getattr(ref, "ref_uid", "") or ""),
        str(chart_kind),
        sha256_json(copy_plot_payload(payload)),
        sha256_json(overrides),
        int(width),
        int(height),
        int(dpi),
    )


def _is_tick_like(text: str) -> bool:
    token = str(text or "").strip().replace(",", "")
    if not token:
        return False
    if _NUMERIC_TICK_RE.match(token):
        return True
    if _P_VALUE_RE.match(token):
        return True
    return False


def filter_svg_text_overrides_for_rerender(overrides: Mapping[str, Any] | None, new_svg: bytes, *, source_svg: bytes | None = None) -> dict[str, Any]:
    if source_svg is not None:
        old_entries = svg_text_entries(source_svg)
        new_entries = svg_text_entries(new_svg)
        positions: dict[str, list[int]] = {}
        for entry in new_entries:
            positions.setdefault(entry.text, []).append(entry.index)
        occurrences: dict[str, int] = {}
        mapped = {}
        normalized = normalize_svg_text_overrides(overrides or {})
        for entry in old_entries:
            occurrence = occurrences.get(entry.text, 0)
            occurrences[entry.text] = occurrence + 1
            matches = positions.get(entry.text, [])
            if str(entry.index) in normalized and occurrence < len(matches):
                mapped[str(matches[occurrence])] = normalized[str(entry.index)]
        return mapped
    normalized = normalize_svg_text_overrides(overrides)
    if not normalized:
        return {}
    try:
        entries = svg_text_entries(new_svg)
    except Exception:
        return {}
    kept: dict[str, Any] = {}
    for key, value in normalized.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= len(entries):
            continue
        new_text = str(entries[index].text or "")
        override_text = None
        if isinstance(value, str):
            override_text = value
        elif isinstance(value, Mapping):
            if "text" in value:
                override_text = None if value.get("text") is None else str(value.get("text"))
        if override_text is None:
            kept[key] = value
            continue
        if _is_tick_like(override_text) and _is_tick_like(new_text) and override_text.strip() != new_text.strip():
            if isinstance(value, Mapping) and any(name in value for name in ("dx", "dy", "x", "y")):
                position = {name: value[name] for name in ("dx", "dy", "x", "y") if name in value}
                if position:
                    kept[key] = position
            continue
        if not _is_tick_like(override_text) and _is_tick_like(new_text):
            continue
        kept[key] = value
    return kept


def render_plot_payload_bytes(
    payload: Mapping[str, Any],
    *,
    fmt: str,
    chart_kind: str | None = None,
    width_mm: float = 40.0,
    height_mm: float = 30.0,
    dpi: int = 180,
    svg_text_overrides: Mapping[str, Any] | None = None,
) -> bytes:
    from matplotlib import pyplot as plt
    from matplotlib import rc_context

    from core.plotting.chart_kinds import build_plot_payload_figure

    figure = build_plot_payload_figure(payload, chart_kind=chart_kind)
    figure.set_size_inches(max(0.1, float(width_mm) / 25.4), max(0.1, float(height_mm) / 25.4), forward=True)
    figure.set_dpi(max(72, int(dpi)))
    export_format = str(fmt).lower().lstrip(".")
    if export_format in {"tif", "tiff"}:
        export_format = "tiff"
    buffer = BytesIO()
    save_kwargs: dict[str, Any] = {"format": export_format, "bbox_inches": "tight", "facecolor": "white"}
    if export_format in {"png", "tiff"}:
        save_kwargs["dpi"] = max(72, int(dpi))
    try:
        if export_format == "svg":
            with rc_context({"svg.fonttype": "none"}):
                figure.savefig(buffer, **save_kwargs)
        else:
            figure.savefig(buffer, **save_kwargs)
    finally:
        plt.close(figure)
    rendered = buffer.getvalue()
    if export_format == "svg" and svg_text_overrides:
        original = render_plot_payload_bytes(payload, fmt="svg", chart_kind=payload.get("chart_kind"), width_mm=width_mm, height_mm=height_mm, dpi=dpi)
        filtered = filter_svg_text_overrides_for_rerender(svg_text_overrides, rendered, source_svg=original)
        rendered = apply_svg_text_overrides(rendered, filtered)
    return rendered


def layout_output_chart_kind(panel: Any, layout_object: Any) -> str | None:
    metadata = dict(getattr(layout_object, "metadata", {}) or {})
    output = dict(metadata.get("output") or {})
    kind = output.get("chart_kind") or metadata.get("chart_kind")
    if kind:
        return str(kind)
    asset_key = str(getattr(layout_object, "asset_key", "") or metadata.get("export_item_key") or "")
    data = getattr(panel, "data", {}) if panel is not None else {}
    if not isinstance(data, dict) or not asset_key:
        return None
    for layout_key in ("ihc_layout", "brightfield_layout"):
        outputs = list(dict(data.get(layout_key) or {}).get("outputs") or [])
        for item in outputs:
            if isinstance(item, Mapping) and str(item.get("export_item_key") or "") == asset_key:
                value = item.get("chart_kind")
                if value:
                    return str(value)
    stats_plot = dict(dict(data.get("wb_layout") or {}).get("stats_plot") or {})
    if asset_key in {"wb_stats_plot", "statistics_chart"} and stats_plot.get("chart_kind"):
        return str(stats_plot.get("chart_kind"))
    return None
