from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from core.chart_payload import (
    attach_plot_payload,
    composer_can_rerender_plot,
    effective_composer_chart_kind,
    hydrate_legacy_plot_payload,
    is_image_like_composer_panel,
    is_statistical_composer_candidate,
    kind_availability,
    layout_output_chart_kind,
    panel_plot_payload,
    payload_family,
    qpcr_live_plot_payload,
    translate_unavailable_reason,
)
from core.plotting.chart_kinds import (
    DEFAULT_GROUPED_CHART_KIND,
    chart_kind_options,
    normalize_chart_kind,
)
from core.utils.i18n import tr

UNSUPPORTED_FLUORO_MARKERS = frozenset({"fluoro_profile_plot", "fluoro_coloc_plot"})
UNSUPPORTED_FLUORO_TYPES = frozenset({"line_profile", "colocalization"})
QPCR_PLOT_MARKER = "qpcr_layout_plot"
WB_STATS_MARKER = "wb_stats_plot"
FLUORO_STATS_MARKER = "fluoro_stats_plot"


@dataclass(frozen=True)
class ChartAdapterAPI:
    layout_chart_target: Callable[..., dict[str, Any] | None]
    set_layout_chart_kind: Callable[..., bool]
    plot_payload_for_export_item: Callable[..., dict[str, Any] | None] | None = None


@dataclass
class ChartKindSelection:
    visible: bool = False
    enabled: bool = False
    context: str = ""
    panel: Any = None
    object_ref: Any = None
    composer_ref: Any = None
    family: str = "grouped"
    chart_kind: str = DEFAULT_GROUPED_CHART_KIND
    options: tuple[str, ...] = field(default_factory=tuple)
    unavailable: dict[str, str] = field(default_factory=dict)
    reason: str = ""
    payload: dict[str, Any] | None = None


def try_load_chart_adapter() -> ChartAdapterAPI | None:
    try:
        from ui.layout_adapters.chart_adapter import layout_chart_target, set_layout_chart_kind
    except ImportError:
        return None
    if not callable(layout_chart_target) or not callable(set_layout_chart_kind):
        return None
    payload_fn = None
    try:
        from ui.layout_adapters.chart_adapter import plot_payload_for_export_item as payload_fn
    except ImportError:
        payload_fn = None
    if payload_fn is not None and not callable(payload_fn):
        payload_fn = None
    return ChartAdapterAPI(
        layout_chart_target=layout_chart_target,
        set_layout_chart_kind=set_layout_chart_kind,
        plot_payload_for_export_item=payload_fn,
    )


def layout_object_ref_from_item(item: Any) -> Any | None:
    current = item
    while current is not None:
        layout_object = getattr(current, "layout_object", None)
        if layout_object is not None:
            return layout_object
        marker = str(current.data(0) or "") if hasattr(current, "data") else ""
        uid = str(current.data(1) or "") if hasattr(current, "data") else ""
        plot_type = str(current.data(2) or "") if hasattr(current, "data") else ""
        if marker == QPCR_PLOT_MARKER and uid:
            return f"qpcr_plot:{uid}"
        if marker == WB_STATS_MARKER:
            return "wb_stats_plot"
        if marker in UNSUPPORTED_FLUORO_MARKERS or plot_type in UNSUPPORTED_FLUORO_TYPES:
            return None
        if marker == FLUORO_STATS_MARKER and uid:
            return f"fluorescence_plot:{uid}"
        parent = current.parentItem() if hasattr(current, "parentItem") else None
        current = parent
    return None


def selected_layout_items(canvas: Any) -> list[Any]:
    scene = getattr(canvas, "scene", None)
    if scene is None:
        return []
    selected = list(scene.selectedItems())
    if hasattr(canvas, "selected_layout_items"):
        try:
            selected = list(canvas.selected_layout_items()) or selected
        except Exception:
            pass
    return selected


def unique_layout_object_refs(items: Sequence[Any]) -> list[Any]:
    refs: list[Any] = []
    seen: set[str] = set()
    for item in items:
        ref = layout_object_ref_from_item(item)
        if ref is None:
            continue
        key = str(getattr(ref, "object_id", None) or ref)
        if key in seen:
            continue
        seen.add(key)
        refs.append(ref)
    return refs


class ChartKindController:
    def __init__(self, adapter: ChartAdapterAPI | None | object = None) -> None:
        if adapter is False:
            self.adapter = None
        elif isinstance(adapter, ChartAdapterAPI):
            self.adapter = adapter
        elif adapter is None:
            self.adapter = try_load_chart_adapter()
        else:
            self.adapter = adapter  # type: ignore[assignment]

    @property
    def adapter_ready(self) -> bool:
        return self.adapter is not None and callable(getattr(self.adapter, "layout_chart_target", None))

    def inspect_layout(self, panel: Any, object_ref: Any) -> ChartKindSelection:
        hidden = ChartKindSelection()
        if panel is None or object_ref is None or not self.adapter_ready:
            return hidden
        try:
            target = self.adapter.layout_chart_target(panel, object_ref)
        except Exception:
            return hidden
        if not target:
            return hidden
        payload = target.get("payload") if isinstance(target, dict) else None
        family = str((target or {}).get("family") or payload_family(payload if isinstance(payload, dict) else None))
        options = chart_kind_options(family)
        current = normalize_chart_kind(
            (target or {}).get("chart_kind"),
            default=DEFAULT_GROUPED_CHART_KIND if family != "object_distribution" else "frequency_bar",
        )
        unavailable: dict[str, str] = {}
        if isinstance(payload, dict):
            options, unavailable = kind_availability(payload)
            current = normalize_chart_kind(current, default=options[0] if options else DEFAULT_GROUPED_CHART_KIND)
        reason = ""
        if isinstance(target, dict) and target.get("reason"):
            reason = str(target.get("reason"))
        if current in unavailable:
            reason = reason or unavailable[current]
        return ChartKindSelection(
            visible=True,
            enabled=True,
            context="layout",
            panel=panel,
            object_ref=object_ref,
            family=family,
            chart_kind=current,
            options=tuple(options),
            unavailable=unavailable,
            reason=reason,
            payload=payload if isinstance(payload, dict) else None,
        )

    def inspect_composer(
        self,
        panel: Any,
        ref: Any,
        *,
        project: Any = None,
        source_context_hash: str | None = None,
    ) -> ChartKindSelection:
        hidden = ChartKindSelection()
        if panel is None:
            return hidden
        panel_type = str(getattr(panel, "panel_type", "") or "")
        if panel_type == "derived_plot" or is_image_like_composer_panel(panel):
            return hidden
        payload = None
        if panel_type in {"qPCR_bar_scatter", "qpcr"}:
            payload = qpcr_live_plot_payload(panel, chart_kind=getattr(ref, "chart_kind", None))
            if payload is None:
                return hidden
        elif is_statistical_composer_candidate(panel):
            payload = panel_plot_payload(panel)
            if payload is None:
                source_panel = None
                if project is not None:
                    source_uuid = str(getattr(panel, "data", {}).get("source_panel_uuid") or "")
                    if source_uuid and hasattr(project, "get_panel"):
                        source_panel = project.get_panel(source_uuid)
                payload_fn = getattr(self.adapter, "plot_payload_for_export_item", None) if self.adapter else None
                payload = hydrate_legacy_plot_payload(
                    panel,
                    source_panel=source_panel,
                    current_source_context_hash=str(source_context_hash or ""),
                    plot_payload_for_export_item=payload_fn,
                )
            if payload is None:
                if is_statistical_composer_candidate(panel):
                    return ChartKindSelection(
                        visible=True,
                        enabled=False,
                        context="composer",
                        panel=panel,
                        composer_ref=ref,
                        reason=tr("chart_kind_reason_requires_source"),
                    )
                return hidden
        else:
            return hidden
        options, unavailable = kind_availability(payload)
        current = effective_composer_chart_kind(panel, ref, payload)
        reason = unavailable.get(current, "")
        return ChartKindSelection(
            visible=True,
            enabled=True,
            context="composer",
            panel=panel,
            composer_ref=ref,
            family=payload_family(payload),
            chart_kind=current,
            options=tuple(options),
            unavailable=unavailable,
            reason=reason,
            payload=payload,
        )

    def apply_layout_kind(self, panel: Any, object_ref: Any, kind: str) -> bool:
        if not self.adapter_ready:
            return False
        selection = self.inspect_layout(panel, object_ref)
        if not selection.visible or kind in selection.unavailable:
            return False
        try:
            return bool(self.adapter.set_layout_chart_kind(panel, object_ref, kind))
        except ValueError as exc:
            raise ValueError(translate_unavailable_reason(str(exc)) or str(exc)) from exc

    def apply_composer_kind(self, panel: Any, ref: Any, kind: str) -> bool:
        selection = self.inspect_composer(panel, ref)
        if not selection.visible or not selection.enabled:
            return False
        if kind in selection.unavailable:
            return False
        if ref is None or not hasattr(ref, "chart_kind"):
            return False
        family = selection.family
        token = normalize_chart_kind(kind, default=selection.chart_kind)
        if token not in selection.options:
            return False
        if str(getattr(ref, "chart_kind", "") or "") == token:
            return False
        ref.chart_kind = token
        return True

    def ensure_export_payload(self, source_panel: Any, export_item: Any, asset_panel: Any) -> dict[str, Any] | None:
        existing = panel_plot_payload(asset_panel)
        if existing is not None:
            return existing
        payload_fn = getattr(self.adapter, "plot_payload_for_export_item", None) if self.adapter else None
        if payload_fn is None or source_panel is None or export_item is None:
            return None
        try:
            payload = payload_fn(source_panel, export_item)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return attach_plot_payload(asset_panel, payload)

    def effective_layout_kind(self, panel: Any, layout_object: Any) -> str | None:
        if self.adapter_ready:
            try:
                target = self.adapter.layout_chart_target(panel, layout_object)
            except Exception:
                target = None
            if isinstance(target, dict) and target.get("chart_kind"):
                return str(target.get("chart_kind"))
        return layout_output_chart_kind(panel, layout_object)
