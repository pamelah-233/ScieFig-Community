from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np

from core.layout_export_items import (
    EXPORT_ITEM_FULL_LAYOUT,
    EXPORT_ITEM_OVERLAY,
    LayoutExportItem,
)
from core.models import (
    BrightfieldPanel,
    DerivedPlotPanel,
    FluorescencePanel,
    IHCPanel,
    QPCRPanel,
    WesternBlotPanel,
)
from core.models.layout_editor import LayoutObject
from core.plotting.chart_kinds import (
    DEFAULT_DISTRIBUTION_CHART_KIND,
    DEFAULT_GROUPED_CHART_KIND,
    DISTRIBUTION_CHART_KINDS,
    FAMILY_GROUPED,
    FAMILY_OBJECT_DISTRIBUTION,
    FAMILY_QPCR,
    GROUPED_CHART_KINDS,
    PLOT_PAYLOAD_SCHEMA_VERSION,
    REASON_FREQUENCY_NOT_GROUPED,
    REASON_NO_VALUES,
    chart_kind_options,
    chart_kind_unavailable_reason,
    copy_plot_payload,
    normalize_chart_kind,
)
from core.plotting.statistics_plot import resolve_statistics_plot_style
from core.utils.text_formatting import display_unit


QPCR_LAYOUT_PLOT_NAMESPACE = "qpcr_layout_plot"
WB_STATS_PLOT_NAMESPACE = "wb_stats_plot"
FLUORESCENCE_LAYOUT_PLOT_NAMESPACE = "fluorescence_layout_plot"
IHC_STATISTICS_CHART_NAMESPACE = "ihc_statistics_chart"
BRIGHTFIELD_STATISTICS_CHART_NAMESPACE = "brightfield_statistics_chart"

_CHART_NAMESPACES = {
    QPCR_LAYOUT_PLOT_NAMESPACE,
    WB_STATS_PLOT_NAMESPACE,
    FLUORESCENCE_LAYOUT_PLOT_NAMESPACE,
    IHC_STATISTICS_CHART_NAMESPACE,
    BRIGHTFIELD_STATISTICS_CHART_NAMESPACE,
}
_FREQUENCY_PLOT_TYPES = {"frequency_bar", "frequency_line"}
_NON_CHART_PLOT_TYPES = {"line_profile", "colocalization"}
_FREQUENCY_KINDS = frozenset(("frequency_bar", "frequency_line"))
_KNOWN_KINDS = frozenset(GROUPED_CHART_KINDS) | frozenset(DISTRIBUTION_CHART_KINDS)
_QPCR_PLOT_PREFIX = "qpcr_plot:"
_FLUORESCENCE_PLOT_PREFIX = "fluorescence_plot:"
_STATS_EXPORT_KEYS = {"wb_stats_plot", "statistics_chart"}
_FLUORO_AXIS_LABELS = {
    "mean": "Mean intensity",
    "integrated_density": "Integrated density",
    "area_px": "Area",
    "count": "Count",
    "mean_object_area_px": "Mean area",
    "mean_intensity": "Mean intensity",
}

__all__ = [
    "layout_chart_target",
    "set_layout_chart_kind",
    "plot_payload_for_export_item",
    "export_item_with_plot_payload",
]


@dataclass
class _Binding:
    object_id: str
    record_id: str
    namespace: str
    family: str
    stored_kind: Any
    plot_type: str
    style_override: dict[str, Any]
    preset_name: str
    y_label: str
    unit: str
    sample_unit: str
    source_module: str
    statistics_result: dict[str, Any] | None = None
    distribution_result: dict[str, Any] | None = None
    qpcr_target: str = ""
    fold_change_rows: list[dict[str, Any]] | None = None
    error_mode: str = ""
    statistics: dict[str, Any] | None = None
    title: str = ""
    x_label: str = ""
    frequency_mode: str = ""
    group_order: list[str] | None = None
    raw_groups: Any = None
    has_raw: bool = True
    panel: Any = None


def layout_chart_target(panel: Any, object_ref: Any) -> dict[str, Any] | None:
    binding = _binding_for(panel, object_ref)
    if binding is None:
        return None
    kind = _effective_kind(binding)
    return {
        "object_id": binding.object_id,
        "record_id": binding.record_id,
        "family": binding.family,
        "chart_kind": kind,
        "payload": _payload_from_binding(binding),
        "reason": _unavailable_reason(binding, kind),
    }


def set_layout_chart_kind(panel: Any, object_ref: Any, kind: Any) -> bool:
    binding = _binding_for(panel, object_ref)
    if binding is None:
        return False
    resolved = _kind_for_setter(kind, binding.family)
    reason = _unavailable_reason(binding, resolved)
    if reason:
        raise ValueError(reason)
    if binding.stored_kind == resolved:
        return False
    if not _write_chart_kind(binding, resolved):
        return False
    if hasattr(panel, "touch"):
        panel.touch()
    return True


def plot_payload_for_export_item(panel: Any, export_item: LayoutExportItem) -> dict[str, Any] | None:
    binding = _binding_for(panel, export_item)
    if binding is None:
        return None
    return _payload_from_binding(binding)


def export_item_with_plot_payload(panel: Any, export_item: LayoutExportItem) -> LayoutExportItem:
    payload = plot_payload_for_export_item(panel, export_item)
    if payload is None:
        return export_item
    metadata = dict(export_item.metadata)
    metadata["plot_payload"] = payload
    metadata["chart_kind"] = payload.get("chart_kind")
    dumped = export_item.to_dict()
    dumped["metadata"] = metadata
    return LayoutExportItem.from_dict(dumped)


def _binding_for(panel: Any, object_ref: Any) -> _Binding | None:
    if panel is None or isinstance(panel, DerivedPlotPanel):
        return None
    ref = _normalize_ref(panel, object_ref)
    if ref is None:
        return None
    source_panel_uuid = str(ref.get("source_panel_uuid") or "")
    if source_panel_uuid and source_panel_uuid != str(getattr(panel, "panel_uuid", "")):
        return None
    namespace = ref["namespace"]
    record_id = ref["record_id"]
    export_item_type = ref["export_item_type"]
    if export_item_type in {EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_OVERLAY}:
        return None
    if namespace not in _CHART_NAMESPACES and ref["export_item_key"] not in _STATS_EXPORT_KEYS:
        if not record_id:
            return None
    if isinstance(panel, QPCRPanel):
        return _qpcr_binding(panel, ref)
    if isinstance(panel, WesternBlotPanel):
        return _wb_binding(panel, ref)
    if isinstance(panel, FluorescencePanel):
        return _fluoro_binding(panel, ref)
    if isinstance(panel, IHCPanel):
        return _ihc_binding(panel, ref)
    if isinstance(panel, BrightfieldPanel):
        return _brightfield_binding(panel, ref)
    return None


def _normalize_ref(panel: Any, object_ref: Any) -> dict[str, str] | None:
    if object_ref is None:
        return None
    if isinstance(object_ref, LayoutExportItem):
        return {
            "object_id": str(object_ref.created_from_layout_object_id or object_ref.export_item_id),
            "source_panel_uuid": str(object_ref.source_panel_uuid or ""),
            "record_id": str(object_ref.source_record_id or ""),
            "namespace": str(object_ref.source_record_namespace or ""),
            "export_item_key": str(object_ref.export_item_key or ""),
            "export_item_type": str(object_ref.export_item_type or ""),
            "plot_uuid": str(dict(object_ref.metadata).get("plot_uuid") or object_ref.source_record_id or ""),
            "plot_type": str(dict(object_ref.metadata).get("plot_type") or ""),
        }
    if isinstance(object_ref, LayoutObject):
        source = object_ref.source
        metadata = dict(object_ref.metadata or {})
        source_panel_uuid = str(source.source_panel_uuid if source is not None else metadata.get("source_panel_uuid") or "")
        namespace = str(source.source_record_namespace if source is not None else "")
        record_id = str(source.source_record_id if source is not None else "")
        plot_uuid = str(metadata.get("plot_uuid") or record_id)
        export_item_key = str(object_ref.asset_key or metadata.get("export_item_key") or "")
        if str(metadata.get("qpcr_kind", "")) == "layout_plot":
            namespace = namespace or QPCR_LAYOUT_PLOT_NAMESPACE
        elif str(metadata.get("wb_kind", "")) == "stats_plot":
            namespace = namespace or WB_STATS_PLOT_NAMESPACE
            record_id = record_id or "wb_stats_plot"
            export_item_key = export_item_key or "wb_stats_plot"
        elif str(metadata.get("fluoro_kind", "")) == "layout_plot":
            namespace = namespace or FLUORESCENCE_LAYOUT_PLOT_NAMESPACE
        elif str(metadata.get("ihc_kind", "")) == "layout_output" and export_item_key == "statistics_chart":
            namespace = namespace or IHC_STATISTICS_CHART_NAMESPACE
        elif str(metadata.get("brightfield_kind", "")) == "layout_output" and export_item_key == "statistics_chart":
            namespace = namespace or BRIGHTFIELD_STATISTICS_CHART_NAMESPACE
        return {
            "object_id": str(object_ref.object_id),
            "source_panel_uuid": source_panel_uuid,
            "record_id": record_id,
            "namespace": namespace,
            "export_item_key": export_item_key,
            "export_item_type": "",
            "plot_uuid": plot_uuid,
            "plot_type": str(metadata.get("plot_type") or ""),
        }
    token = str(object_ref or "").strip()
    if not token:
        return None
    plot_uuid = token
    source_panel_uuid = ""
    namespace = ""
    record_id = token
    export_item_key = token
    if token.startswith(_QPCR_PLOT_PREFIX):
        plot_uuid = token[len(_QPCR_PLOT_PREFIX) :]
        record_id = plot_uuid
        namespace = QPCR_LAYOUT_PLOT_NAMESPACE
    elif token.startswith(_FLUORESCENCE_PLOT_PREFIX):
        plot_uuid = token[len(_FLUORESCENCE_PLOT_PREFIX) :]
        record_id = plot_uuid
        namespace = FLUORESCENCE_LAYOUT_PLOT_NAMESPACE
    elif ":qpcr_plot:" in token:
        source_panel_uuid, plot_uuid = token.split(":qpcr_plot:", 1)
        record_id = plot_uuid
        namespace = QPCR_LAYOUT_PLOT_NAMESPACE
    elif ":fluorescence_plot:" in token:
        source_panel_uuid, plot_uuid = token.split(":fluorescence_plot:", 1)
        record_id = plot_uuid
        namespace = FLUORESCENCE_LAYOUT_PLOT_NAMESPACE
    elif token.endswith(":wb_stats_plot") or token == "wb_stats_plot":
        if token != "wb_stats_plot":
            source_panel_uuid = token[: -len(":wb_stats_plot")]
        record_id = "wb_stats_plot"
        namespace = WB_STATS_PLOT_NAMESPACE
        export_item_key = "wb_stats_plot"
        plot_uuid = ""
    elif token.endswith(":ihc_output:statistics_chart") or token == "statistics_chart":
        if token != "statistics_chart":
            source_panel_uuid = token[: -len(":ihc_output:statistics_chart")]
        export_item_key = "statistics_chart"
        plot_uuid = ""
        if isinstance(panel, BrightfieldPanel):
            namespace = BRIGHTFIELD_STATISTICS_CHART_NAMESPACE
        else:
            namespace = IHC_STATISTICS_CHART_NAMESPACE
        record_id = token
    elif token.endswith(":brightfield_output:statistics_chart"):
        source_panel_uuid = token[: -len(":brightfield_output:statistics_chart")]
        export_item_key = "statistics_chart"
        namespace = BRIGHTFIELD_STATISTICS_CHART_NAMESPACE
        plot_uuid = ""
    return {
        "object_id": token,
        "source_panel_uuid": source_panel_uuid,
        "record_id": record_id,
        "namespace": namespace,
        "export_item_key": export_item_key,
        "export_item_type": "",
        "plot_uuid": plot_uuid,
        "plot_type": "",
    }


def _qpcr_binding(panel: QPCRPanel, ref: Mapping[str, str]) -> _Binding | None:
    namespace = str(ref.get("namespace") or "")
    if namespace and namespace != QPCR_LAYOUT_PLOT_NAMESPACE:
        return None
    plot_uuid = str(ref.get("plot_uuid") or ref.get("record_id") or "")
    record = _qpcr_plot_record(panel, plot_uuid)
    if record is None:
        return None
    if str(record.get("plot_type") or "qpcr_target") != "qpcr_target":
        return None
    target = str(record.get("target") or "")
    rows = [
        dict(row)
        for row in list(panel.data.get("processed_data", {}).get("fold_change", []) or [])
        if isinstance(row, Mapping) and str(row.get("target", "")) == target
    ]
    raw_groups: dict[str, list[float]] = {}
    group_order: list[str] = []
    for row in rows:
        group = str(row.get("group", ""))
        if not group:
            continue
        if group not in raw_groups:
            group_order.append(group)
            raw_groups[group] = []
        try:
            raw_groups[group].append(float(row.get("fold_change")))
        except (TypeError, ValueError):
            continue
    object_id = ref["object_id"] if ":qpcr_plot:" in ref["object_id"] else f"{panel.panel_uuid}:qpcr_plot:{record['plot_uuid']}"
    return _Binding(
        object_id=object_id,
        record_id=str(record.get("plot_uuid") or ""),
        namespace=QPCR_LAYOUT_PLOT_NAMESPACE,
        family=FAMILY_QPCR,
        stored_kind=record.get("chart_kind"),
        plot_type=str(record.get("plot_type") or "qpcr_target"),
        style_override=dict(record.get("style") or {}),
        preset_name=str(panel.style.get("preset", "Nature")),
        y_label="Relative mRNA expression",
        unit="",
        sample_unit="biological_replicate",
        source_module=panel.source_module or QPCRPanel.SOURCE_MODULE,
        qpcr_target=target,
        fold_change_rows=rows,
        error_mode=_qpcr_error_mode(panel),
        statistics=dict(panel.analysis.get("statistics") or {}),
        title=target,
        group_order=group_order,
        raw_groups=raw_groups,
        has_raw=_has_finite(raw_groups),
        panel=panel,
    )


def _wb_binding(panel: WesternBlotPanel, ref: Mapping[str, str]) -> _Binding | None:
    namespace = str(ref.get("namespace") or "")
    key = str(ref.get("export_item_key") or "")
    record_id = str(ref.get("record_id") or "")
    object_id = str(ref.get("object_id") or "")
    if namespace not in {"", WB_STATS_PLOT_NAMESPACE}:
        return None
    if (
        namespace != WB_STATS_PLOT_NAMESPACE
        and key != "wb_stats_plot"
        and record_id != "wb_stats_plot"
        and not object_id.endswith("wb_stats_plot")
    ):
        return None
    stats_plot = dict(panel.data.get("wb_layout", {}).get("stats_plot") or {})
    if not stats_plot:
        return None
    result = dict(panel.data.get("wb_statistics") or {})
    raw_groups, group_order, has_raw = _raw_from_plot_data(result.get("plot_data"))
    return _Binding(
        object_id=f"{panel.panel_uuid}:wb_stats_plot",
        record_id="wb_stats_plot",
        namespace=WB_STATS_PLOT_NAMESPACE,
        family=FAMILY_GROUPED,
        stored_kind=stats_plot.get("chart_kind"),
        plot_type="statistics",
        style_override=dict(stats_plot.get("style") or {}),
        preset_name=str(panel.style.get("preset", "Nature")),
        y_label="Relative expression (fold change)",
        unit="",
        sample_unit="biological_replicate",
        source_module=panel.source_module or WesternBlotPanel.SOURCE_MODULE,
        statistics_result=result,
        group_order=group_order,
        raw_groups=raw_groups,
        has_raw=has_raw,
        panel=panel,
    )


def _fluoro_binding(panel: FluorescencePanel, ref: Mapping[str, str]) -> _Binding | None:
    namespace = str(ref.get("namespace") or "")
    if namespace and namespace != FLUORESCENCE_LAYOUT_PLOT_NAMESPACE:
        return None
    plot_uuid = str(ref.get("plot_uuid") or ref.get("record_id") or "")
    record = _fluoro_plot_record(panel, plot_uuid)
    if record is None:
        return None
    plot_type = str(record.get("plot_type") or "statistics")
    if plot_type in _NON_CHART_PLOT_TYPES:
        return None
    if plot_type in _FREQUENCY_PLOT_TYPES:
        distribution = dict(record.get("distribution_result") or {})
        raw_groups, group_order, has_raw = _raw_from_distribution(distribution)
        unit = str(distribution.get("unit") or record.get("unit") or "px^2")
        return _Binding(
            object_id=f"{panel.panel_uuid}:fluorescence_plot:{record['plot_uuid']}",
            record_id=str(record.get("plot_uuid") or ""),
            namespace=FLUORESCENCE_LAYOUT_PLOT_NAMESPACE,
            family=FAMILY_OBJECT_DISTRIBUTION,
            stored_kind=record.get("chart_kind"),
            plot_type=plot_type,
            style_override=dict(record.get("style") or {}),
            preset_name=str(panel.style.get("preset", "Nature")),
            y_label=f"Cell area ({display_unit(unit)})",
            unit=unit,
            sample_unit="object",
            source_module=panel.source_module or FluorescencePanel.SOURCE_MODULE,
            distribution_result=distribution,
            title=str(record.get("title") or ""),
            x_label=f"Cell area ({display_unit(unit)})",
            frequency_mode=str(distribution.get("frequency_mode") or "absolute"),
            group_order=group_order,
            raw_groups=raw_groups,
            has_raw=has_raw,
            panel=panel,
        )
    if plot_type != "statistics":
        return None
    result = dict(record.get("statistics_result") or {})
    raw_groups, group_order, has_raw = _raw_from_plot_data(result.get("plot_data") or record.get("rows"))
    metric = str(result.get("metric") or record.get("metric") or "value")
    return _Binding(
        object_id=f"{panel.panel_uuid}:fluorescence_plot:{record['plot_uuid']}",
        record_id=str(record.get("plot_uuid") or ""),
        namespace=FLUORESCENCE_LAYOUT_PLOT_NAMESPACE,
        family=FAMILY_GROUPED,
        stored_kind=record.get("chart_kind"),
        plot_type=plot_type,
        style_override=dict(record.get("style") or {}),
        preset_name=str(panel.style.get("preset", "Nature")),
        y_label=_fluoro_axis_label(metric),
        unit=str(result.get("unit") or ""),
        sample_unit="image",
        source_module=panel.source_module or FluorescencePanel.SOURCE_MODULE,
        statistics_result=result,
        title=str(record.get("title") or ""),
        group_order=group_order,
        raw_groups=raw_groups,
        has_raw=has_raw,
        panel=panel,
    )


def _ihc_binding(panel: IHCPanel, ref: Mapping[str, str]) -> _Binding | None:
    if not _is_statistics_output_ref(ref, IHC_STATISTICS_CHART_NAMESPACE):
        return None
    output = _statistics_output(panel.data.get("ihc_layout", {}))
    if output is None:
        return None
    result = dict(panel.analysis.get("ihc_statistics") or {})
    raw_groups, group_order, has_raw = _raw_from_plot_data(result.get("plot_data"))
    metric = str(result.get("metric") or output.get("source_record_id") or "Value")
    return _Binding(
        object_id=f"{panel.panel_uuid}:ihc_output:statistics_chart",
        record_id=str(output.get("source_record_id") or metric),
        namespace=IHC_STATISTICS_CHART_NAMESPACE,
        family=FAMILY_GROUPED,
        stored_kind=output.get("chart_kind"),
        plot_type="statistics_chart",
        style_override=dict(output.get("style") or {}),
        preset_name=str(panel.style.get("preset", "Nature")),
        y_label=metric,
        unit=str(result.get("unit") or ""),
        sample_unit="image",
        source_module=panel.source_module or IHCPanel.SOURCE_MODULE,
        statistics_result=result,
        group_order=group_order,
        raw_groups=raw_groups,
        has_raw=has_raw,
        panel=panel,
    )


def _brightfield_binding(panel: BrightfieldPanel, ref: Mapping[str, str]) -> _Binding | None:
    if not _is_statistics_output_ref(ref, BRIGHTFIELD_STATISTICS_CHART_NAMESPACE):
        return None
    output = _statistics_output(panel.data.get("brightfield_layout", {}))
    if output is None:
        return None
    chart_export = dict(panel.analysis.get("brightfield_chart_export") or {})
    chart_mode = str(chart_export.get("chart_mode") or "summary")
    if chart_mode in _FREQUENCY_PLOT_TYPES:
        distribution = dict(chart_export.get("distribution_result") or {})
        raw_groups, group_order, has_raw = _raw_from_distribution(distribution)
        unit = str(chart_export.get("unit") or distribution.get("unit") or "px^2")
        return _Binding(
            object_id=f"{panel.panel_uuid}:brightfield_output:statistics_chart",
            record_id=str(output.get("source_record_id") or chart_export.get("metric") or ""),
            namespace=BRIGHTFIELD_STATISTICS_CHART_NAMESPACE,
            family=FAMILY_OBJECT_DISTRIBUTION,
            stored_kind=output.get("chart_kind"),
            plot_type=chart_mode,
            style_override=dict(output.get("style") or {}),
            preset_name=str(panel.style.get("preset", "Nature")),
            y_label=f"Fiber area ({display_unit(unit)})",
            unit=unit,
            sample_unit="object",
            source_module=panel.source_module or BrightfieldPanel.SOURCE_MODULE,
            distribution_result=distribution,
            x_label=f"Fiber area ({display_unit(unit)})",
            frequency_mode=str(chart_export.get("frequency_mode") or distribution.get("frequency_mode") or "absolute"),
            group_order=group_order,
            raw_groups=raw_groups,
            has_raw=has_raw,
            panel=panel,
        )
    result = dict(chart_export.get("statistics_result") or panel.analysis.get("brightfield_statistics") or {})
    raw_groups, group_order, has_raw = _raw_from_plot_data(result.get("plot_data"))
    metric = str(chart_export.get("metric") or result.get("metric") or "Value")
    return _Binding(
        object_id=f"{panel.panel_uuid}:brightfield_output:statistics_chart",
        record_id=str(output.get("source_record_id") or metric),
        namespace=BRIGHTFIELD_STATISTICS_CHART_NAMESPACE,
        family=FAMILY_GROUPED,
        stored_kind=output.get("chart_kind"),
        plot_type="statistics_chart",
        style_override=dict(output.get("style") or {}),
        preset_name=str(panel.style.get("preset", "Nature")),
        y_label=metric,
        unit=str(chart_export.get("unit") or result.get("unit") or ""),
        sample_unit="image",
        source_module=panel.source_module or BrightfieldPanel.SOURCE_MODULE,
        statistics_result=result,
        group_order=group_order,
        raw_groups=raw_groups,
        has_raw=has_raw,
        panel=panel,
    )


def _is_statistics_output_ref(ref: Mapping[str, str], namespace: str) -> bool:
    ref_namespace = str(ref.get("namespace") or "")
    key = str(ref.get("export_item_key") or "")
    object_id = str(ref.get("object_id") or "")
    if ref_namespace in {namespace, ""} and key in {"", "statistics_chart"}:
        if ref_namespace == namespace or key == "statistics_chart" or object_id.endswith("statistics_chart"):
            return True
    if object_id.endswith(":ihc_output:statistics_chart") and namespace == IHC_STATISTICS_CHART_NAMESPACE:
        return True
    if object_id.endswith(":brightfield_output:statistics_chart") and namespace == BRIGHTFIELD_STATISTICS_CHART_NAMESPACE:
        return True
    if ref_namespace == namespace:
        return True
    return False


def _qpcr_plot_record(panel: QPCRPanel, plot_uuid: str) -> dict[str, Any] | None:
    plots = [dict(plot) for plot in panel.data.get("qpcr_layout", {}).get("plots", []) if isinstance(plot, Mapping)]
    for plot in plots:
        if str(plot.get("plot_uuid", "")) == str(plot_uuid):
            return plot
    return None


def _fluoro_plot_record(panel: FluorescencePanel, plot_uuid: str) -> dict[str, Any] | None:
    for plot in panel.analysis.get("fluoro_layout_plots", []):
        if isinstance(plot, Mapping) and str(plot.get("plot_uuid", "")) == str(plot_uuid):
            return dict(plot)
    latest = dict(panel.analysis.get("fluoro_stats_plot") or {})
    if latest and str(latest.get("plot_uuid", "")) == str(plot_uuid):
        return latest
    return None


def _statistics_output(layout: Mapping[str, Any]) -> dict[str, Any] | None:
    for output in layout.get("outputs", []):
        if isinstance(output, Mapping) and str(output.get("export_item_key", "")) == "statistics_chart":
            return dict(output)
    return None


def _family_default(binding: _Binding) -> str:
    if binding.family == FAMILY_OBJECT_DISTRIBUTION:
        if binding.plot_type == "frequency_line":
            return "frequency_line"
        return DEFAULT_DISTRIBUTION_CHART_KIND
    return DEFAULT_GROUPED_CHART_KIND


def _effective_kind(binding: _Binding) -> str:
    stored = binding.stored_kind
    default = _family_default(binding)
    if stored is None or str(stored).strip() == "":
        return default
    return normalize_chart_kind(stored, default=default)


def _kind_for_setter(value: Any, family: str) -> str:
    default = DEFAULT_DISTRIBUTION_CHART_KIND if family == FAMILY_OBJECT_DISTRIBUTION else DEFAULT_GROUPED_CHART_KIND
    if value is None or str(value).strip() == "":
        return default
    token = str(value).strip().lower().replace("-", "_").replace("+", "_")
    if token not in _KNOWN_KINDS:
        raise ValueError(f"Unsupported chart kind: {value!r}")
    options = chart_kind_options(family)
    if token not in options:
        if token in _FREQUENCY_KINDS:
            raise ValueError(REASON_FREQUENCY_NOT_GROUPED)
        raise ValueError(f"Unsupported chart kind: {value!r}")
    return token


def _unavailable_reason(binding: _Binding, kind: str) -> str | None:
    options = chart_kind_options(binding.family)
    if kind not in options:
        if kind in _FREQUENCY_KINDS:
            return REASON_FREQUENCY_NOT_GROUPED
        return f"Unsupported chart kind: {kind!r}"
    if binding.family == FAMILY_OBJECT_DISTRIBUTION and kind in _FREQUENCY_KINDS:
        return None if binding.has_raw else REASON_NO_VALUES
    return chart_kind_unavailable_reason(kind, binding.raw_groups, has_raw=binding.has_raw)


def _payload_from_binding(binding: _Binding) -> dict[str, Any]:
    kind = _effective_kind(binding)
    payload: dict[str, Any] = {
        "schema_version": PLOT_PAYLOAD_SCHEMA_VERSION,
        "family": binding.family,
        "chart_kind": kind,
        "style": _effective_style(binding),
        "y_label": binding.y_label,
        "unit": binding.unit,
        "source_module": binding.source_module,
        "sample_unit": binding.sample_unit,
    }
    if binding.family == FAMILY_QPCR:
        payload["target"] = binding.qpcr_target
        payload["fold_change_rows"] = list(binding.fold_change_rows or [])
        payload["error_mode"] = binding.error_mode
        payload["statistics"] = dict(binding.statistics or {})
        if binding.title:
            payload["title"] = binding.title
    elif binding.family == FAMILY_OBJECT_DISTRIBUTION:
        payload["distribution_result"] = dict(binding.distribution_result or {})
        if binding.x_label:
            payload["x_label"] = binding.x_label
        if binding.frequency_mode:
            payload["frequency_mode"] = binding.frequency_mode
    else:
        payload["statistics_result"] = dict(binding.statistics_result or {})
    if binding.group_order:
        payload["group_order"] = list(binding.group_order)
    return copy_plot_payload(payload)


def _effective_style(binding: _Binding) -> dict[str, Any]:
    return dict(
        resolve_statistics_plot_style(
            binding.preset_name,
            saved_style=dict(binding.style_override or {}),
        )
    )


def _write_chart_kind(binding: _Binding, kind: str) -> bool:
    panel = binding.panel
    if isinstance(panel, QPCRPanel):
        layout = dict(panel.data.get("qpcr_layout") or {})
        plots = [dict(plot) if isinstance(plot, Mapping) else plot for plot in layout.get("plots", [])]
        changed = False
        for index, plot in enumerate(plots):
            if not isinstance(plot, dict):
                continue
            if str(plot.get("plot_uuid", "")) != binding.record_id:
                continue
            plot["chart_kind"] = kind
            plots[index] = plot
            changed = True
            break
        if not changed:
            return False
        layout["plots"] = plots
        panel.data["qpcr_layout"] = layout
        return True
    if isinstance(panel, WesternBlotPanel):
        layout = dict(panel.data.get("wb_layout") or {})
        stats_plot = dict(layout.get("stats_plot") or {})
        if not stats_plot:
            return False
        stats_plot["chart_kind"] = kind
        layout["stats_plot"] = stats_plot
        panel.data["wb_layout"] = layout
        return True
    if isinstance(panel, FluorescencePanel):
        plots = [dict(plot) if isinstance(plot, Mapping) else plot for plot in panel.analysis.get("fluoro_layout_plots", [])]
        changed = False
        for index, plot in enumerate(plots):
            if not isinstance(plot, dict):
                continue
            if str(plot.get("plot_uuid", "")) != binding.record_id:
                continue
            plot["chart_kind"] = kind
            plots[index] = plot
            changed = True
            break
        if not changed:
            return False
        panel.analysis["fluoro_layout_plots"] = plots
        latest = dict(panel.analysis.get("fluoro_stats_plot") or {})
        if str(latest.get("plot_uuid", "")) == binding.record_id:
            latest["chart_kind"] = kind
            panel.analysis["fluoro_stats_plot"] = latest
        return True
    if isinstance(panel, IHCPanel):
        return _write_statistics_output_kind(panel, "ihc_layout", kind)
    if isinstance(panel, BrightfieldPanel):
        return _write_statistics_output_kind(panel, "brightfield_layout", kind)
    return False


def _write_statistics_output_kind(panel: Any, layout_key: str, kind: str) -> bool:
    layout = dict(panel.data.get(layout_key) or {})
    outputs = [dict(item) if isinstance(item, Mapping) else item for item in layout.get("outputs", [])]
    changed = False
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            continue
        if str(output.get("export_item_key", "")) != "statistics_chart":
            continue
        output["chart_kind"] = kind
        outputs[index] = output
        changed = True
        break
    if not changed:
        return False
    layout["outputs"] = outputs
    panel.data[layout_key] = layout
    return True


def _qpcr_error_mode(panel: QPCRPanel) -> str:
    token = str(panel.analysis.get("error_bar") or "").strip().lower()
    if token == "sem":
        return "sem"
    if token == "sd":
        return "sd"
    return token if token in {"sem", "sd"} else ""


def _fluoro_axis_label(metric: str) -> str:
    return _FLUORO_AXIS_LABELS.get(metric, metric.replace("_", " ").title())


def _raw_from_plot_data(plot_data: Any) -> tuple[dict[str, list[float]], list[str], bool]:
    mapping: dict[str, list[float]] = {}
    order: list[str] = []
    for row in list(plot_data or []):
        if not isinstance(row, Mapping):
            continue
        group = str(row.get("group", ""))
        if not group:
            continue
        if group not in mapping:
            order.append(group)
            mapping[group] = []
        if "value" not in row:
            continue
        try:
            mapping[group].append(float(row.get("value")))
        except (TypeError, ValueError):
            continue
    return mapping, order, _has_finite(mapping)


def _raw_from_distribution(result: Mapping[str, Any]) -> tuple[dict[str, list[float]], list[str], bool]:
    mapping: dict[str, list[float]] = {}
    order: list[str] = []
    for series in list(result.get("series") or []):
        if not isinstance(series, Mapping):
            continue
        label = str(series.get("lane_label") or series.get("group") or "")
        if not label:
            continue
        if label not in mapping:
            order.append(label)
            mapping[label] = []
        for value in list(series.get("values") or []):
            try:
                mapping[label].append(float(value))
            except (TypeError, ValueError):
                continue
    return mapping, order, _has_finite(mapping)


def _has_finite(raw_groups: Mapping[str, list[float]]) -> bool:
    return any(
        isinstance(value, (int, float)) and np.isfinite(value)
        for values in raw_groups.values()
        for value in values
    )
