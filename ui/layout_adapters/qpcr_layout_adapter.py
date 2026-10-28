from __future__ import annotations
from ui.layout_adapters.annotation_records import write_shared_fields,restore_shared_fields

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_INDIVIDUAL_PLOT, LayoutExportItem
from core.models import ExternalImagePanel, QPCRPanel
from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle, LayoutSourceIdentity
from core.utils.hashing import sha256_json
from ui.layout_adapters.chart_adapter import export_item_with_plot_payload


QPCR_LAYOUT_EXPORT_KEY = "qpcr_layout"
QPCR_PLOT_EXPORT_KEY_PREFIX = "qpcr_plot"
QPCR_LAYOUT_EXPORT_SOURCE_MODULE = "qpcr_layout"
QPCR_PLOT_EXPORT_SOURCE_MODULE = "qpcr_plot"
DEFAULT_QPCR_COMPOSER_EXPORT_DPI = 1200

_MM_PER_INCH = 25.4
_POINTS_PER_INCH = 72.0
_ADAPTER_SCHEMA_VERSION = 1


def qpcr_plot_export_key(plot_uuid: str) -> str:
    return f"{QPCR_PLOT_EXPORT_KEY_PREFIX}:{str(plot_uuid or 'plot')}"


def qpcr_canvas_spec(panel: QPCRPanel, dpi: int = 300) -> LayoutCanvasSpec:
    layout = dict(panel.data.get("qpcr_layout", {}))
    objects = list(layout.get("plots", [])) + list(layout.get("texts", [])) + list(layout.get("shapes", []))
    right = max([float(item.get("x", 0.0)) + float(item.get("width_px", item.get("width", 0.0))) for item in objects if isinstance(item, Mapping)] or [720.0])
    bottom = max([float(item.get("y", 0.0)) + float(item.get("height_px", item.get("height", 0.0))) for item in objects if isinstance(item, Mapping)] or [420.0])
    return LayoutCanvasSpec(
        canvas_id=f"{panel.panel_uuid}:qpcr_canvas",
        width_px=max(1, int(round(right + 48.0))),
        height_px=max(1, int(round(bottom + 48.0))),
        dpi=int(dpi),
        background_color="#FFFFFF",
        unit="px",
        metadata={
            "source_module": panel.source_module or QPCRPanel.SOURCE_MODULE,
            "source_panel_uuid": panel.panel_uuid,
            "adapter_schema_version": _ADAPTER_SCHEMA_VERSION,
        },
    )


def qpcr_layout_objects(panel: QPCRPanel) -> list[LayoutObject]:
    layout = _layout_payload(panel)
    context_hash = _layout_context_hash(panel)
    objects = [_root_layout_object(panel, layout, context_hash)]
    objects.extend(_plot_objects(panel, list(layout.get("plots", [])), context_hash))
    objects.extend(_text_objects(panel, list(layout.get("texts", [])), context_hash))
    objects.extend(_shape_objects(panel, list(layout.get("shapes", [])), context_hash))
    return [restore_shared_fields(obj) for obj in objects]


def qpcr_layout_from_layout_objects(layout_objects: Iterable[LayoutObject]) -> dict[str, Any]:
    objects = list(layout_objects)
    root = next((obj for obj in objects if _metadata(obj).get("qpcr_kind") == "layout_root"), None)
    if root is None:
        raise ValueError("qPCR layout objects must include a layout_root object")
    layout = deepcopy(dict(_metadata(root).get("qpcr_layout", {})))
    for obj in objects:
        kind = str(_metadata(obj).get("qpcr_kind", ""))
        if kind == "layout_plot":
            _write_plot(layout, obj)
        elif kind == "layout_text":
            _write_text(layout, obj)
        elif kind == "layout_shape":
            _write_shape(layout, obj)
        if kind in {'layout_text','layout_shape'}:
            key='text_uuid' if kind=='layout_text' else 'shape_uuid'
            records=layout.get('texts' if kind=='layout_text' else 'shapes',[])
            record=next((r for r in records if r.get(key)==obj.metadata.get(key)),None)
            if record is not None:write_shared_fields(record,obj)
    return layout


def apply_qpcr_layout_objects(panel: QPCRPanel, layout_objects: Iterable[LayoutObject], *, append_audit: bool = True) -> dict[str, Any]:
    objects = list(layout_objects)
    _validate_layout_object_sources(panel, objects)
    layout = qpcr_layout_from_layout_objects(objects)
    panel.data["qpcr_layout"] = layout
    if append_audit:
        panel.append_audit("apply_qpcr_layout_objects", f"{len(objects)} object(s)")
    return layout


def qpcr_export_item_for_scene_bounds(
    panel: QPCRPanel,
    export_item_key: str,
    width_pt: float,
    height_pt: float,
    *,
    dpi: int = DEFAULT_QPCR_COMPOSER_EXPORT_DPI,
    image_path: str | Path = "",
    source_format: str = "",
    source_width_px: int | None = None,
    source_height_px: int | None = None,
    plot_uuid: str = "",
    qpcr_target: str = "",
) -> LayoutExportItem:
    width_pt = float(width_pt)
    height_pt = float(height_pt)
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("qPCR export item bounds must be positive")
    is_layout = export_item_key == QPCR_LAYOUT_EXPORT_KEY
    metadata: dict[str, Any] = {"adapter_schema_version": _ADAPTER_SCHEMA_VERSION}
    if not is_layout:
        metadata["plot_uuid"] = str(plot_uuid)
        metadata["qpcr_target"] = str(qpcr_target)
    return export_item_with_plot_payload(
        panel,
        LayoutExportItem(
            export_item_id=f"{panel.panel_uuid}:{export_item_key}",
            export_item_type=EXPORT_ITEM_FULL_LAYOUT if is_layout else EXPORT_ITEM_INDIVIDUAL_PLOT,
            source_module=QPCR_LAYOUT_EXPORT_SOURCE_MODULE if is_layout else QPCR_PLOT_EXPORT_SOURCE_MODULE,
            source_panel_uuid=panel.panel_uuid,
            export_item_key=export_item_key,
            source_dpi=int(dpi),
            preferred_width_mm=width_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            preferred_height_mm=height_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            source_record_id=QPCR_LAYOUT_EXPORT_KEY if is_layout else str(plot_uuid),
            source_record_namespace=QPCR_LAYOUT_EXPORT_KEY if is_layout else "qpcr_layout_plot",
            source_context_hash=_layout_context_hash(panel),
            image_path=str(image_path) if image_path else "",
            source_format=source_format,
            source_width_px=source_width_px or max(1, int(round(width_pt * int(dpi) / _POINTS_PER_INCH))),
            source_height_px=source_height_px or max(1, int(round(height_pt * int(dpi) / _POINTS_PER_INCH))),
            metadata=metadata,
        ),
    )


def apply_qpcr_export_item_metadata(asset_panel: ExternalImagePanel, export_item: LayoutExportItem) -> None:
    asset_panel.data.update(export_item.to_external_image_metadata())


def _root_layout_object(panel: QPCRPanel, layout: Mapping[str, Any], context_hash: str) -> LayoutObject:
    spec = qpcr_canvas_spec(panel)
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:qpcr_layout_root",
        object_type="qpcr_layout_root",
        x=0.0,
        y=0.0,
        width=float(spec.width_px),
        height=float(spec.height_px),
        z_order=0,
        source=_source(panel, "qpcr_layout_root", "qpcr_layout_root", context_hash),
        metadata={"qpcr_kind": "layout_root", "qpcr_layout": deepcopy(dict(layout)), "adapter_schema_version": _ADAPTER_SCHEMA_VERSION},
    )


def _plot_objects(panel: QPCRPanel, plots: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, plot in enumerate(plots):
        if not isinstance(plot, Mapping):
            continue
        payload = dict(plot)
        plot_uuid = str(payload.get("plot_uuid") or f"plot:{index}")
        style = dict(payload.get("style", {}))
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:qpcr_plot:{plot_uuid}",
                object_type="plot",
                x=float(payload.get("x", 24.0)),
                y=float(payload.get("y", 24.0)),
                width=float(payload.get("width_px", 520)),
                height=float(payload.get("height_px", 360)),
                z_order=int(payload.get('z_order',100+index)),
                locked=bool(payload.get('locked',False)),
                hidden=not bool(payload.get("enabled", True)),
                source=_source(panel, plot_uuid, "qpcr_layout_plot", context_hash),
                style=LayoutObjectStyle(font_family=str(style.get("font_family", "Arial")), font_size_pt=float(style.get("font_size_pt", 7.0))),
                text=str(payload.get("target", "")),
                asset_key=qpcr_plot_export_key(plot_uuid),
                metadata={
                    "qpcr_kind": "layout_plot",
                    "plot_uuid": plot_uuid,
                    "target": str(payload.get("target", "")),
                    "index": index,
                    "plot": deepcopy(payload),
                    "svg_text_overrides": deepcopy(dict(payload.get("svg_text_overrides", {}))),
                },
            )
        )
    return objects


def _text_objects(panel: QPCRPanel, texts: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, text in enumerate(texts):
        if not isinstance(text, Mapping):
            continue
        payload = dict(text)
        text_uuid = str(payload.get("text_uuid") or f"text:{index}")
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:qpcr_text:{text_uuid}",
                object_type="text",
                x=float(payload.get("x", 0.0)),
                y=float(payload.get("y", 0.0)),
                width=float(payload.get("width", 120.0)),
                height=float(payload.get("height", 24.0)),
                rotation_deg=float(payload.get("rotation", 0.0)),
                z_order=200 + index,
                source=_source(panel, text_uuid, "qpcr_layout_text", context_hash),
                style=LayoutObjectStyle(
                    text_color=str(payload.get("color", "#111111")),
                    font_family=str(payload.get("font_family", "Arial")),
                    font_size_pt=float(payload.get("font_size", 10.0)),
                    bold=bool(payload.get("bold", False)),
                    italic=bool(payload.get("italic", False)),
                ),
                text=str(payload.get("text", "")),
                metadata={"qpcr_kind": "layout_text", "text_uuid": text_uuid, "index": index, "text_record": deepcopy(payload)},
            )
        )
    return objects


def _shape_objects(panel: QPCRPanel, shapes: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, shape in enumerate(shapes):
        if not isinstance(shape, Mapping):
            continue
        payload = dict(shape)
        shape_uuid = str(payload.get("shape_uuid") or f"shape:{index}")
        shape_type = str(payload.get("shape_type", "rectangle"))
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:qpcr_shape:{shape_uuid}",
                object_type="line" if shape_type == "line" else "shape",
                x=float(payload.get("x", 0.0)),
                y=float(payload.get("y", 0.0)),
                width=float(payload.get("width", 0.0)),
                height=float(payload.get("height", 0.0)),
                z_order=160 + index,
                source=_source(panel, shape_uuid, "qpcr_layout_shape", context_hash),
                style=LayoutObjectStyle(
                    stroke_color=str(payload.get("stroke_color", "#000000")),
                    fill_color=str(payload.get("fill_color", "#FFFFFF")),
                    stroke_width=float(payload.get("stroke_width", 2.0)),
                ),
                metadata={"qpcr_kind": "layout_shape", "shape_uuid": shape_uuid, "shape_type": shape_type, "index": index, "shape": deepcopy(payload)},
            )
        )
    return objects


def _write_plot(layout: dict[str, Any], obj: LayoutObject) -> None:
    plot_uuid = str(_metadata(obj).get("plot_uuid", ""))
    plots = list(layout.get("plots", []))
    for index, plot in enumerate(plots):
        payload = dict(plot)
        if str(payload.get("plot_uuid", "")) != plot_uuid:
            continue
        payload.update({"x": float(obj.x), "y": float(obj.y), "width_px": int(round(obj.width)), "height_px": int(round(obj.height)), "enabled": not bool(obj.hidden)})
        if obj.locked or 'locked' in payload:payload['locked']=obj.locked
        if obj.z_order!=int(payload.get('z_order',100+index)) or 'z_order' in payload:payload['z_order']=obj.z_order
        svg_text_overrides = deepcopy(dict(_metadata(obj).get("svg_text_overrides", {})))
        if svg_text_overrides:
            payload["svg_text_overrides"] = svg_text_overrides
        else:
            payload.pop("svg_text_overrides", None)
        plots[index] = payload
        layout["plots"] = plots
        return


def _write_text(layout: dict[str, Any], obj: LayoutObject) -> None:
    text_uuid = str(_metadata(obj).get("text_uuid", ""))
    texts = list(layout.get("texts", []))
    for index, text in enumerate(texts):
        payload = dict(text)
        if str(payload.get("text_uuid") or f'text:{index}') != text_uuid:
            continue
        payload['text_uuid']=text_uuid
        payload.update(
            {
                "text": obj.text,
                "x": float(obj.x),
                "y": float(obj.y),
                "font_family": obj.style.font_family,
                "font_size": float(obj.style.font_size_pt),
                "bold": bool(obj.style.bold),
                "italic": bool(obj.style.italic),
                "color": obj.style.text_color,
                "rotation": float(obj.rotation_deg),
            }
        )
        texts[index] = payload
        layout["texts"] = texts
        return
    texts.append(
        {
            "text_uuid": text_uuid,
            "text": obj.text,
            "x": float(obj.x),
            "y": float(obj.y),
            "font_family": obj.style.font_family,
            "font_size": float(obj.style.font_size_pt),
            "bold": bool(obj.style.bold),
            "italic": bool(obj.style.italic),
            "color": obj.style.text_color,
            "rotation": float(obj.rotation_deg),
        }
    )
    layout["texts"] = texts


def _write_shape(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    shape_uuid = str(metadata.get("shape_uuid", ""))
    shapes = list(layout.get("shapes", []))
    for index, shape in enumerate(shapes):
        payload = dict(shape)
        if str(payload.get("shape_uuid") or f'shape:{index}') != shape_uuid:
            continue
        payload['shape_uuid']=shape_uuid
        payload.update(
            {
                "shape_type": str(metadata.get("shape_type", payload.get("shape_type", "rectangle"))),
                "x": float(obj.x),
                "y": float(obj.y),
                "width": float(obj.width),
                "height": float(obj.height),
                "stroke_width": float(obj.style.stroke_width),
                "stroke_color": obj.style.stroke_color,
                "fill_color": obj.style.fill_color,
            }
        )
        shapes[index] = payload
        layout["shapes"] = shapes
        return
    shapes.append(
        {
            "shape_uuid": shape_uuid,
            "shape_type": str(metadata.get("shape_type", "rectangle")),
            "x": float(obj.x),
            "y": float(obj.y),
            "width": float(obj.width),
            "height": float(obj.height),
            "stroke_width": float(obj.style.stroke_width),
            "stroke_color": obj.style.stroke_color,
            "fill_color": obj.style.fill_color,
        }
    )
    layout["shapes"] = shapes


def _layout_payload(panel: QPCRPanel) -> dict[str, Any]:
    panel.layout_plot_records()
    return deepcopy(dict(panel.data.get("qpcr_layout", {})))


def layout_context_hash(panel: QPCRPanel) -> str:
    return _layout_context_hash(panel)


def _layout_context_hash(panel: QPCRPanel) -> str:
    return sha256_json({"panel_uuid": panel.panel_uuid, "qpcr_layout": panel.data.get("qpcr_layout", {})})


def _source(panel: QPCRPanel, record_id: str, namespace: str, context_hash: str) -> LayoutSourceIdentity:
    return LayoutSourceIdentity(
        source_module=panel.source_module or QPCRPanel.SOURCE_MODULE,
        source_panel_uuid=panel.panel_uuid,
        source_record_id=str(record_id),
        source_record_namespace=namespace,
        source_context_hash=context_hash,
    )


def _validate_layout_object_sources(panel: QPCRPanel, layout_objects: Iterable[LayoutObject]) -> None:
    for obj in layout_objects:
        if obj.source is not None and obj.source.source_panel_uuid != panel.panel_uuid:
            raise ValueError("qPCR layout object source_panel_uuid does not match panel")
        namespace = obj.source.source_record_namespace if obj.source is not None else ""
        if namespace and namespace not in {"qpcr_layout_root", "qpcr_layout_plot", "qpcr_layout_text", "qpcr_layout_shape"}:
            raise ValueError(f"Unsupported qPCR layout namespace: {namespace}")


def _metadata(obj: LayoutObject) -> dict[str, Any]:
    return _json_copy(obj.metadata)


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_copy(item) for item in value]
    return value
