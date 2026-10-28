from __future__ import annotations
from ui.layout_adapters.annotation_records import restore_shared_fields,update_annotation_extras,prune_removed_annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_OVERLAY, EXPORT_ITEM_STATISTICS_CHART, LayoutExportItem
from ui.layout_adapters.chart_adapter import export_item_with_plot_payload
from core.layout_scale_bar import (
    ensure_layout_scale_bar_states,
    ensure_layout_scale_bar_template,
    normalize_scale_bar_template,
    presentation_layout_scale_state,
    scale_bar_layout_object,
)
from core.models import BrightfieldPanel, ExternalImagePanel
from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle, LayoutSourceIdentity
from core.utils.hashing import sha256_json


BRIGHTFIELD_LAYOUT_EXPORT_KEY = "brightfield_layout"
BRIGHTFIELD_SOURCE_IMAGE_KEY = "source_image"
BRIGHTFIELD_OVERLAY_EXPORT_KEY = "measurement_overlay"
BRIGHTFIELD_STATISTICS_EXPORT_KEY = "statistics_chart"
BRIGHTFIELD_LAYOUT_EXPORT_SOURCE_MODULE = "brightfield_layout"
BRIGHTFIELD_OVERLAY_EXPORT_SOURCE_MODULE = "brightfield_overlay"
BRIGHTFIELD_STATISTICS_EXPORT_SOURCE_MODULE = "brightfield_statistics"
DEFAULT_BRIGHTFIELD_COMPOSER_EXPORT_DPI = 1200

_MM_PER_INCH = 25.4
_POINTS_PER_INCH = 72.0
_ADAPTER_SCHEMA_VERSION = 1


def brightfield_canvas_spec(panel: BrightfieldPanel, dpi: int = 300) -> LayoutCanvasSpec:
    layout = _layout_payload(panel)
    right = max([float(item.get("x", 0.0)) + float(item.get("width", 0.0)) for item in _layout_items(layout)] or [720.0])
    bottom = max([float(item.get("y", 0.0)) + float(item.get("height", 0.0)) for item in _layout_items(layout)] or [420.0])
    return LayoutCanvasSpec(
        canvas_id=f"{panel.panel_uuid}:brightfield_layout_canvas",
        width_px=max(1, int(round(right + 48.0))),
        height_px=max(1, int(round(bottom + 48.0))),
        dpi=int(dpi),
        background_color="#FFFFFF",
        unit="px",
        metadata={"source_module": panel.source_module or BrightfieldPanel.SOURCE_MODULE, "source_panel_uuid": panel.panel_uuid, "adapter_schema_version": _ADAPTER_SCHEMA_VERSION},
    )


def brightfield_layout_objects(panel: BrightfieldPanel, measurement: Mapping[str, Any] | None = None) -> list[LayoutObject]:
    layout = _layout_payload(panel, measurement=measurement)
    context_hash = _layout_context_hash(panel)
    objects = [_root_layout_object(panel, layout, context_hash)]
    objects.extend(_output_objects(panel, list(layout.get("outputs", [])), context_hash))
    objects.extend(_scale_bar_objects(panel, layout, context_hash))
    objects.extend(_text_objects(panel, list(layout.get("texts", [])), context_hash))
    objects.extend(_shape_objects(panel, list(layout.get("shapes", [])), context_hash))
    return [restore_shared_fields(obj) for obj in objects]


def brightfield_layout_from_layout_objects(layout_objects: Iterable[LayoutObject]) -> dict[str, Any]:
    objects = list(layout_objects)
    root = next((obj for obj in objects if _metadata(obj).get("brightfield_kind") == "layout_root"), None)
    if root is None:
        raise ValueError("Brightfield layout objects must include a layout_root object")
    layout = deepcopy(dict(_metadata(root).get("brightfield_layout", {})))
    prune_removed_annotations(layout,objects)
    for obj in objects:
        kind = str(_metadata(obj).get("brightfield_kind", ""))
        if kind == "layout_output":
            _write_output(layout, obj)
        elif kind == "layout_scale_bar":
            _write_scale_bar(layout, obj)
        elif kind == "layout_text":
            _write_text(layout, obj)
        elif kind == "layout_shape":
            _write_shape(layout, obj)
        if kind in {'layout_text','layout_shape'}:
            update_annotation_extras(layout,obj)
    return layout


def apply_brightfield_layout_objects(panel: BrightfieldPanel, layout_objects: Iterable[LayoutObject], *, append_audit: bool = True) -> dict[str, Any]:
    objects = list(layout_objects)
    _validate_layout_object_sources(panel, objects)
    layout = brightfield_layout_from_layout_objects(objects)
    panel.data["brightfield_layout"] = layout
    if append_audit:
        panel.append_audit("apply_brightfield_layout_objects", f"{len(objects)} object(s)")
    return layout


def brightfield_export_item_for_scene_bounds(
    panel: BrightfieldPanel,
    export_item_key: str,
    width_pt: float,
    height_pt: float,
    *,
    dpi: int = DEFAULT_BRIGHTFIELD_COMPOSER_EXPORT_DPI,
    image_path: str | Path = "",
    source_format: str = "",
    source_width_px: int | None = None,
    source_height_px: int | None = None,
    measurement: Mapping[str, Any] | None = None,
    metric: str = "",
) -> LayoutExportItem:
    width_pt = float(width_pt)
    height_pt = float(height_pt)
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("Brightfield export item bounds must be positive")
    metadata = {"adapter_schema_version": _ADAPTER_SCHEMA_VERSION}
    source_module = BRIGHTFIELD_LAYOUT_EXPORT_SOURCE_MODULE
    export_item_type = EXPORT_ITEM_FULL_LAYOUT
    source_record_id = BRIGHTFIELD_LAYOUT_EXPORT_KEY
    source_namespace = BRIGHTFIELD_LAYOUT_EXPORT_KEY
    if export_item_key == BRIGHTFIELD_OVERLAY_EXPORT_KEY:
        source_module = BRIGHTFIELD_OVERLAY_EXPORT_SOURCE_MODULE
        export_item_type = EXPORT_ITEM_OVERLAY
        source_record_id = str((measurement or {}).get("measurement_uuid", ""))
        source_namespace = "brightfield_measurement_overlay"
        metadata.update(_measurement_metadata(measurement or {}))
        metadata["plot_type"] = "measurement_overlay"
    elif export_item_key == BRIGHTFIELD_STATISTICS_EXPORT_KEY:
        source_module = BRIGHTFIELD_STATISTICS_EXPORT_SOURCE_MODULE
        export_item_type = EXPORT_ITEM_STATISTICS_CHART
        source_record_id = str(metric)
        source_namespace = "brightfield_statistics_chart"
        metadata["metric"] = str(metric)
        metadata["plot_type"] = "statistics_chart"
    return export_item_with_plot_payload(
        panel,
        LayoutExportItem(
            export_item_id=f"{panel.panel_uuid}:{export_item_key}:{source_record_id}",
            export_item_type=export_item_type,
            source_module=source_module,
            source_panel_uuid=panel.panel_uuid,
            export_item_key=export_item_key,
            source_dpi=int(dpi),
            preferred_width_mm=width_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            preferred_height_mm=height_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            source_record_id=source_record_id,
            source_record_namespace=source_namespace,
            source_context_hash=_layout_context_hash(panel),
            image_path=str(image_path) if image_path else "",
            source_format=source_format,
            source_width_px=source_width_px or max(1, int(round(width_pt * int(dpi) / _POINTS_PER_INCH))),
            source_height_px=source_height_px or max(1, int(round(height_pt * int(dpi) / _POINTS_PER_INCH))),
            metadata=metadata,
        ),
    )


def apply_brightfield_export_item_metadata(asset_panel: ExternalImagePanel, export_item: LayoutExportItem) -> None:
    asset_panel.data.update(export_item.to_external_image_metadata())


def _root_layout_object(panel: BrightfieldPanel, layout: Mapping[str, Any], context_hash: str) -> LayoutObject:
    spec = brightfield_canvas_spec(panel)
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:brightfield_layout_root",
        object_type="brightfield_layout_root",
        x=0.0,
        y=0.0,
        width=float(spec.width_px),
        height=float(spec.height_px),
        source=_source(panel, "brightfield_layout_root", "brightfield_layout_root", context_hash),
        metadata={"brightfield_kind": "layout_root", "brightfield_layout": deepcopy(dict(layout)), "adapter_schema_version": _ADAPTER_SCHEMA_VERSION},
    )


def _output_objects(panel: BrightfieldPanel, outputs: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, output in enumerate(outputs):
        if not isinstance(output, Mapping):
            continue
        payload = dict(output)
        key = str(payload.get("export_item_key", ""))
        namespace = "brightfield_measurement_overlay" if key == BRIGHTFIELD_OVERLAY_EXPORT_KEY else ("brightfield_source_image" if key == BRIGHTFIELD_SOURCE_IMAGE_KEY else "brightfield_statistics_chart")
        is_image = key in {BRIGHTFIELD_OVERLAY_EXPORT_KEY, BRIGHTFIELD_SOURCE_IMAGE_KEY}
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:brightfield_output:{payload.get('instance_uid') or key}",
                object_type="image" if is_image else "plot",
                x=float(payload.get("x", 24.0 + index * 24.0)),
                y=float(payload.get("y", 24.0 + index * 24.0)),
                width=float(payload.get("width", 420.0 if key == BRIGHTFIELD_OVERLAY_EXPORT_KEY else 320.0)),
                height=float(payload.get("height", 300.0 if key == BRIGHTFIELD_OVERLAY_EXPORT_KEY else 220.0)),
                z_order=100 + index,
                hidden=not bool(payload.get("enabled", True)),
                source=_source(panel, str(payload.get("source_record_id", key)), namespace, context_hash),
                asset_key=key,
                metadata={
                    "brightfield_kind": "layout_output",
                    "export_item_key": key,
                    "index": index,
                    "output": deepcopy(payload),
                    "svg_text_overrides": deepcopy(dict(payload.get("svg_text_overrides", {}))),
                },
            )
        )
    return objects


def _scale_bar_objects(panel: BrightfieldPanel, layout: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    image = panel.active_image() or {}
    image_uid = str(image.get("uid", ""))
    overlay = _output_for_key(layout, BRIGHTFIELD_OVERLAY_EXPORT_KEY)
    source = _output_for_key(layout, BRIGHTFIELD_SOURCE_IMAGE_KEY)
    output = overlay or source
    if not image_uid or not output:
        return []
    target_rect = _output_rect(output)
    target_output_key = BRIGHTFIELD_OVERLAY_EXPORT_KEY if overlay else BRIGHTFIELD_SOURCE_IMAGE_KEY
    template = ensure_layout_scale_bar_template(image.get("measurement_scale_bar"), layout.get("scale_bar_template"))
    objects: list[LayoutObject] = []
    for state in ensure_layout_scale_bar_states(
        measurement_scale=image.get("measurement_scale_bar"),
        existing_states=layout.get("scale_bars"),
        target_ids=[image_uid],
    ):
        if not isinstance(state, Mapping) or not bool(state.get("enabled", True)):
            continue
        if str(state.get("target_id", image_uid)) != image_uid:
            continue
        obj = scale_bar_layout_object(
            panel_uuid=panel.panel_uuid,
            module_kind="brightfield",
            target_id=image_uid,
            target_rect=target_rect,
            source_metadata=dict(image.get("metadata", {})),
            template=template,
            state=dict(state),
            measurement_scale=image.get("measurement_scale_bar"),
            z_order=320,
        )
        payload = obj.to_dict()
        payload["source"]["source_context_hash"] = context_hash
        payload["metadata"]["brightfield_kind"] = "layout_scale_bar"
        payload["metadata"]["target_output_key"] = target_output_key
        objects.append(LayoutObject.from_dict(payload))
    return objects


def _text_objects(panel: BrightfieldPanel, texts: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, text in enumerate(texts):
        if not isinstance(text, Mapping):
            continue
        payload = dict(text)
        uid = str(payload.get("text_uuid") or f"text:{index}")
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:brightfield_text:{uid}",
                object_type="text",
                x=float(payload.get("x", 0.0)),
                y=float(payload.get("y", 0.0)),
                width=float(payload.get("width", 140.0)),
                height=float(payload.get("height", 24.0)),
                rotation_deg=float(payload.get("rotation", 0.0)),
                z_order=200 + index,
                source=_source(panel, uid, "brightfield_layout_text", context_hash),
                style=LayoutObjectStyle(text_color=str(payload.get("color", "#111111")), font_family=str(payload.get("font_family", "Arial")), font_size_pt=float(payload.get("font_size", 10.0)), bold=bool(payload.get("bold", False)), italic=bool(payload.get("italic", False))),
                text=str(payload.get("text", "")),
                metadata={"brightfield_kind": "layout_text", "text_uuid": uid, "index": index, "text_record": deepcopy(payload)},
            )
        )
    return objects


def _shape_objects(panel: BrightfieldPanel, shapes: list[Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, shape in enumerate(shapes):
        if not isinstance(shape, Mapping):
            continue
        payload = dict(shape)
        uid = str(payload.get("shape_uuid") or f"shape:{index}")
        shape_type = str(payload.get("shape_type", "rectangle"))
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:brightfield_shape:{uid}",
                object_type="line" if shape_type == "line" else "shape",
                x=float(payload.get("x", 0.0)),
                y=float(payload.get("y", 0.0)),
                width=float(payload.get("width", 0.0)),
                height=float(payload.get("height", 0.0)),
                z_order=180 + index,
                source=_source(panel, uid, "brightfield_layout_shape", context_hash),
                style=LayoutObjectStyle(stroke_color=str(payload.get("stroke_color", "#111111")), fill_color=str(payload.get("fill_color", "#FFFFFF")), stroke_width=float(payload.get("stroke_width", 2.0))),
                metadata={"brightfield_kind": "layout_shape", "shape_uuid": uid, "shape_type": shape_type, "index": index, "shape": deepcopy(payload)},
            )
        )
    return objects


def _write_output(layout: dict[str, Any], obj: LayoutObject) -> None:
    key = str(_metadata(obj).get("export_item_key", ""))
    instance_uid = str(_metadata(obj).get("output", {}).get("instance_uid", ""))
    svg_text_overrides = deepcopy(dict(_metadata(obj).get("svg_text_overrides", {})))
    outputs = list(layout.get("outputs", []))
    for index, output in enumerate(outputs):
        payload = dict(output)
        if str(payload.get("export_item_key", "")) == key and str(payload.get("instance_uid", "")) == instance_uid:
            payload.update({"x": float(obj.x), "y": float(obj.y), "width": float(obj.width), "height": float(obj.height), "enabled": not bool(obj.hidden)})
            if svg_text_overrides:
                payload["svg_text_overrides"] = svg_text_overrides
            else:
                payload.pop("svg_text_overrides", None)
            outputs[index] = payload
            layout["outputs"] = outputs
            return


def _write_scale_bar(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    target_id = str(metadata.get("target_id", ""))
    if not target_id:
        return
    template = _template_from_scale_bar_object(layout, obj)
    states = [dict(item) for item in layout.get("scale_bars", []) if isinstance(item, Mapping)]
    state = presentation_layout_scale_state(metadata.get("state", {}), target_id=target_id)
    state["template"] = template
    state["enabled"] = not bool(obj.hidden)
    state["calibration_source"] = str(metadata.get("calibration_source", "assumed"))
    replaced = False
    for index, item in enumerate(states):
        if str(item.get("target_id", "")) == target_id:
            states[index] = {**item, **state}
            replaced = True
            break
    if not replaced:
        states.append(state)
    layout["scale_bars"] = states


def _write_text(layout: dict[str, Any], obj: LayoutObject) -> None:
    uid = str(_metadata(obj).get("text_uuid", ""))
    texts = list(layout.get("texts", []))
    for index, text in enumerate(texts):
        payload = dict(text)
        if str(payload.get("text_uuid", "")) != uid:
            continue
        payload.update({"text_uuid": uid, "text": obj.text, "x": float(obj.x), "y": float(obj.y), "width": float(obj.width), "height": float(obj.height), "font_family": obj.style.font_family, "font_size": float(obj.style.font_size_pt), "bold": bool(obj.style.bold), "italic": bool(obj.style.italic), "color": obj.style.text_color, "rotation": float(obj.rotation_deg)})
        texts[index] = payload
        layout["texts"] = texts
        return
    record = {"text_uuid": uid, "text": obj.text, "x": float(obj.x), "y": float(obj.y), "width": float(obj.width), "height": float(obj.height), "font_family": obj.style.font_family, "font_size": float(obj.style.font_size_pt), "bold": bool(obj.style.bold), "italic": bool(obj.style.italic), "color": obj.style.text_color, "rotation": float(obj.rotation_deg)}
    texts.append(record)
    layout["texts"] = texts


def _write_shape(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    uid = str(metadata.get("shape_uuid", ""))
    shapes = list(layout.get("shapes", []))
    for index, shape in enumerate(shapes):
        payload = dict(shape)
        if str(payload.get("shape_uuid", "")) != uid:
            continue
        payload.update({"shape_uuid": uid, "shape_type": str(metadata.get("shape_type", payload.get("shape_type", "rectangle"))), "x": float(obj.x), "y": float(obj.y), "width": float(obj.width), "height": float(obj.height), "stroke_width": float(obj.style.stroke_width), "stroke_color": obj.style.stroke_color, "fill_color": obj.style.fill_color})
        shapes[index] = payload
        layout["shapes"] = shapes
        return
    record = {"shape_uuid": uid, "shape_type": str(metadata.get("shape_type", "rectangle")), "x": float(obj.x), "y": float(obj.y), "width": float(obj.width), "height": float(obj.height), "stroke_width": float(obj.style.stroke_width), "stroke_color": obj.style.stroke_color, "fill_color": obj.style.fill_color}
    shapes.append(record)
    layout["shapes"] = shapes


def _layout_payload(panel: BrightfieldPanel, measurement: Mapping[str, Any] | None = None) -> dict[str, Any]:
    layout = deepcopy(dict(panel.data.get("brightfield_layout", {})))
    outputs = [dict(item) for item in layout.get("outputs", []) if isinstance(item, Mapping)]
    instances = [item for item in outputs if item.get("instance_uid")]
    by_key = {str(item.get("export_item_key", "")): item for item in outputs if not item.get("instance_uid")}
    if measurement:
        _upsert_output_source(by_key, BRIGHTFIELD_OVERLAY_EXPORT_KEY, str(measurement.get("measurement_uuid", "")), 24.0, 24.0, 420.0, 300.0)
        by_key.pop(BRIGHTFIELD_SOURCE_IMAGE_KEY, None)
    elif panel.active_image() is not None:
        _upsert_output_source(by_key, BRIGHTFIELD_SOURCE_IMAGE_KEY, str(panel.active_image().get("uid", "")), 24.0, 24.0, 420.0, 300.0)
    chart_export = dict(panel.analysis.get("brightfield_chart_export", {}))
    statistics = dict(panel.analysis.get("brightfield_statistics", {}))
    chart_metric = str(chart_export.get("metric", "") or statistics.get("metric", ""))
    if chart_metric:
        _upsert_output_source(by_key, BRIGHTFIELD_STATISTICS_EXPORT_KEY, chart_metric, 468.0, 24.0, 320.0, 220.0)
    layout["outputs"] = list(by_key.values()) + instances
    layout.setdefault("texts", [])
    layout.setdefault("shapes", [])
    return layout


def _upsert_output_source(by_key: dict[str, dict[str, Any]], key: str, source_record_id: str, x: float, y: float, width: float, height: float) -> None:
    if key in by_key:
        by_key[key] = dict(by_key[key])
        by_key[key]["export_item_key"] = key
        by_key[key]["source_record_id"] = source_record_id
        return
    by_key[key] = _default_output(key, source_record_id, x, y, width, height)


def _default_output(key: str, source_record_id: str, x: float, y: float, width: float, height: float) -> dict[str, Any]:
    return {"export_item_key": key, "source_record_id": source_record_id, "enabled": True, "x": x, "y": y, "width": width, "height": height}


def _layout_items(layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for key in ("outputs", "texts", "shapes") for item in layout.get(key, []) if isinstance(item, Mapping)]


def _output_for_key(layout: Mapping[str, Any], key: str) -> dict[str, Any]:
    for output in layout.get("outputs", []):
        if isinstance(output, Mapping) and str(output.get("export_item_key", "")) == key:
            return dict(output)
    return {}


def _output_rect(output: Mapping[str, Any]) -> dict[str, float]:
    return {
        "x": float(output.get("x", 0.0)),
        "y": float(output.get("y", 0.0)),
        "width": float(output.get("width", 1.0)),
        "height": float(output.get("height", 1.0)),
    }


def _template_from_scale_bar_object(layout: Mapping[str, Any], obj: LayoutObject) -> dict[str, Any]:
    metadata = _metadata(obj)
    template = normalize_scale_bar_template(metadata.get("template"))
    style_metadata = dict(obj.style.metadata)
    template.update(
        {
            "line_color": obj.style.stroke_color,
            "text_color": obj.style.text_color,
            "line_thickness": float(obj.style.stroke_width),
            "font_family": obj.style.font_family,
            "font_size": float(obj.style.font_size_pt),
            "bold": bool(obj.style.bold),
            "italic": bool(obj.style.italic),
            "text_position": str(style_metadata.get("text_position", template["text_position"])),
            "label_gap": float(style_metadata.get("label_gap", template["label_gap"])),
            "show_text": bool(style_metadata.get("show_text", template["show_text"])),
        }
    )
    target_output = _output_for_key(layout, str(metadata.get("target_output_key", BRIGHTFIELD_OVERLAY_EXPORT_KEY)))
    if target_output:
        rect = _output_rect(target_output)
        if str(template["anchor"]).endswith("right"):
            template["offset_x_fraction"] = max(0.0, (rect["x"] + rect["width"] - float(obj.x) - float(obj.width)) / max(1.0, rect["width"]))
        else:
            template["offset_x_fraction"] = max(0.0, (float(obj.x) - rect["x"]) / max(1.0, rect["width"]))
        if str(template["anchor"]).startswith("bottom"):
            template["offset_y_fraction"] = max(0.0, (rect["y"] + rect["height"] - float(obj.y) - float(obj.height)) / max(1.0, rect["height"]))
        else:
            template["offset_y_fraction"] = max(0.0, (float(obj.y) - rect["y"]) / max(1.0, rect["height"]))
    return template


def _measurement_metadata(measurement: Mapping[str, Any]) -> dict[str, Any]:
    reliability = dict(measurement.get("reliability", {}))
    status = str(measurement.get("reliability_status") or reliability.get("status", "")).strip()
    params = dict(measurement.get("params", {}))
    results = dict(measurement.get("results", {}))
    analysis_metadata = dict(measurement.get("analysis_metadata", {}))
    return {
        "measurement_uuid": str(measurement.get("measurement_uuid", "")),
        "reliability_status": status,
        "reliability": deepcopy(reliability),
        "warnings": [str(message).strip() for message in measurement.get("warnings", []) if str(message).strip()],
        "qc_messages": [str(message).strip() for message in measurement.get("qc_messages", []) if str(message).strip()],
        "threshold_source": str(measurement.get("threshold_source") or analysis_metadata.get("threshold_source", "")).strip(),
        "stain_type": str(measurement.get("stain_type", "")),
        "segmentation_mode": str(params.get("segmentation_mode") or results.get("segmentation_mode") or analysis_metadata.get("segmentation_mode", "")),
    }


def layout_context_hash(panel: BrightfieldPanel) -> str:
    return _layout_context_hash(panel)


def _layout_context_hash(panel: BrightfieldPanel) -> str:
    return sha256_json({"panel_uuid": panel.panel_uuid, "brightfield_layout": panel.data.get("brightfield_layout", {})})


def _source(panel: BrightfieldPanel, record_id: str, namespace: str, context_hash: str) -> LayoutSourceIdentity:
    return LayoutSourceIdentity(panel.source_module or BrightfieldPanel.SOURCE_MODULE, panel.panel_uuid, str(record_id), namespace, context_hash)


def _validate_layout_object_sources(panel: BrightfieldPanel, layout_objects: Iterable[LayoutObject]) -> None:
    allowed = {"brightfield_layout_root", "brightfield_source_image", "brightfield_measurement_overlay", "brightfield_statistics_chart", "brightfield_layout_text", "brightfield_layout_shape", "brightfield_layout_scale_bar"}
    for obj in layout_objects:
        if obj.source is not None and obj.source.source_panel_uuid != panel.panel_uuid:
            raise ValueError("Brightfield layout object source_panel_uuid does not match panel")
        namespace = obj.source.source_record_namespace if obj.source is not None else ""
        if namespace and namespace not in allowed:
            raise ValueError(f"Unsupported Brightfield layout namespace: {namespace}")


def _metadata(obj: LayoutObject) -> dict[str, Any]:
    return _json_copy(obj.metadata)


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_copy(item) for item in value]
    return value
