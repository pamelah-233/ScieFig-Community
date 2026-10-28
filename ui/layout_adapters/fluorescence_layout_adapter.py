from __future__ import annotations
from ui.layout_adapters.annotation_records import restore_shared_fields,write_shared_fields

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_INDIVIDUAL_PLOT, LayoutExportItem
from ui.layout_adapters.chart_adapter import export_item_with_plot_payload
from core.layout_scale_bar import (
    ensure_layout_scale_bar_states,
    ensure_layout_scale_bar_template,
    fluorescence_layout_scale_target_ids,
    normalize_scale_bar_template,
    presentation_layout_scale_state,
    scale_bar_layout_object,
)
from core.models import ExternalImagePanel, FluorescencePanel
from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle, LayoutSourceIdentity
from core.utils.hashing import sha256_json


FLUORESCENCE_LAYOUT_EXPORT_KEY = "fluorescence_layout"
FLUORESCENCE_PLOT_EXPORT_KEY_PREFIX = "fluorescence_plot"
FLUORESCENCE_LAYOUT_EXPORT_SOURCE_MODULE = "fluorescence_layout"
FLUORESCENCE_PLOT_EXPORT_SOURCE_MODULE = "fluorescence_layout_plot"
DEFAULT_FLUORESCENCE_COMPOSER_EXPORT_DPI = 1200

_MM_PER_INCH = 25.4
_POINTS_PER_INCH = 72.0
_ADAPTER_SCHEMA_VERSION = 1


def fluorescence_plot_export_key(plot_uuid: str) -> str:
    plot_uuid = str(plot_uuid or "plot")
    return f"{FLUORESCENCE_PLOT_EXPORT_KEY_PREFIX}:{plot_uuid}"


def fluorescence_canvas_spec(panel: FluorescencePanel, image_uid: str | None = None, dpi: int = 300) -> LayoutCanvasSpec:
    image = _image_for_uid(panel, image_uid)
    metadata = dict(image.get("metadata", {})) if image else {}
    width = int(metadata.get("width_px") or metadata.get("width") or 1)
    height = int(metadata.get("height_px") or metadata.get("height") or 1)
    active_uid = str(image.get("uid", "")) if image else str(image_uid or panel.data.get("active_image_uid") or "")
    return LayoutCanvasSpec(
        canvas_id=f"{panel.panel_uuid}:fluorescence_canvas:{active_uid}",
        width_px=max(1, width),
        height_px=max(1, height),
        dpi=int(dpi),
        background_color="#FFFFFF",
        unit="px",
        metadata={
            "source_module": panel.source_module or FluorescencePanel.SOURCE_MODULE,
            "source_panel_uuid": panel.panel_uuid,
            "active_image_uid": active_uid,
            "adapter_schema_version": _ADAPTER_SCHEMA_VERSION,
        },
    )


def fluorescence_layout_objects(panel: FluorescencePanel) -> list[LayoutObject]:
    images_layout = _images_layout_payload(panel)
    plots = _layout_plot_payload(panel)
    context_hash = _layout_context_hash(panel)
    objects: list[LayoutObject] = [_root_layout_object(panel, images_layout, plots, context_hash)]
    for image in panel.images():
        image_uid = str(image.get("uid", ""))
        if not image_uid:
            continue
        objects.extend(_layout_image_objects(panel, image_uid, dict(image.get("layout_item_geometries", {})), context_hash))
        objects.extend(_scale_bar_objects(panel, image, context_hash))
        objects.extend(_shape_objects(panel, image_uid, list(image.get("layout_shapes", [])), context_hash))
        objects.extend(_text_objects(panel, image_uid, list(image.get("layout_texts", [])), context_hash))
    objects.extend(_plot_objects(panel, plots, context_hash))
    return [restore_shared_fields(obj) for obj in objects]


def fluorescence_layout_objects_with_scene_positions(
    panel: FluorescencePanel,
    positions: Mapping[str, Any],
) -> list[LayoutObject]:
    objects = fluorescence_layout_objects(panel)
    context_hash = _layout_context_hash(panel)
    updated: list[LayoutObject] = []
    seen_image_geometry: set[tuple[str, str]] = set()
    for obj in objects:
        payload = obj.to_dict()
        metadata = _metadata(obj)
        kind = str(metadata.get("fluoro_kind", ""))
        if kind == "layout_image":
            image_uid = str(metadata.get("image_uid", ""))
            layout_key = str(metadata.get("layout_key", ""))
            seen_image_geometry.add((image_uid, layout_key))
            geometry = _scene_position(positions, "images", image_uid, layout_key)
            if geometry:
                _update_geometry_payload(payload, geometry, width_key="width", height_key="height")
        elif kind == "layout_shape":
            shape_uuid = str(metadata.get("shape_uuid", ""))
            geometry = _scene_position(positions, "shapes", shape_uuid)
            if geometry:
                _update_geometry_payload(payload, geometry, width_key="width", height_key="height")
                style = dict(payload.get("style", {}))
                if "stroke_color" in geometry:
                    style["stroke_color"] = str(geometry["stroke_color"])
                if "fill_color" in geometry:
                    style["fill_color"] = str(geometry["fill_color"])
                if "stroke_width" in geometry:
                    style["stroke_width"] = float(geometry["stroke_width"])
                payload["style"] = style
        elif kind == "layout_text":
            text_uuid = str(metadata.get("text_uuid", ""))
            geometry = _scene_position(positions, "texts", text_uuid)
            if geometry:
                _update_geometry_payload(payload, geometry, width_key="width", height_key="height")
                if "text" in geometry:
                    payload["text"] = str(geometry["text"])
                style = dict(payload.get("style", {}))
                if "font_family" in geometry:
                    style["font_family"] = str(geometry["font_family"])
                if "font_size" in geometry:
                    style["font_size_pt"] = float(geometry["font_size"])
                if "bold" in geometry:
                    style["bold"] = bool(geometry["bold"])
                if "italic" in geometry:
                    style["italic"] = bool(geometry["italic"])
                if "color" in geometry:
                    style["text_color"] = str(geometry["color"])
                payload["style"] = style
        elif kind == "layout_plot":
            plot_uuid = str(metadata.get("plot_uuid", ""))
            geometry = _scene_position(positions, "plots", plot_uuid)
            if geometry:
                _update_geometry_payload(payload, geometry, width_key="width_px", height_key="height_px")
        updated.append(LayoutObject.from_dict(payload))

    for image_uid, geometries in dict(positions.get("images", {})).items():
        if not isinstance(geometries, Mapping):
            continue
        for layout_key, geometry in geometries.items():
            key = (str(image_uid), str(layout_key))
            if key in seen_image_geometry or not isinstance(geometry, Mapping):
                continue
            updated.append(_layout_image_object(panel, str(image_uid), str(layout_key), dict(geometry), context_hash))
    return updated


def fluorescence_layout_from_layout_objects(layout_objects: Iterable[LayoutObject]) -> dict[str, Any]:
    objects = list(layout_objects)
    root = next((obj for obj in objects if _metadata(obj).get("fluoro_kind") == "layout_root"), None)
    if root is None:
        raise ValueError("Fluorescence layout objects must include a layout_root object")
    root_metadata = _metadata(root)
    images_layout = deepcopy(dict(root_metadata.get("images_layout", {})))
    plots = deepcopy(list(root_metadata.get("fluoro_layout_plots", [])))
    plots_key_present = bool(root_metadata.get("fluoro_layout_plots_key_present", False))
    for obj in objects:
        kind = str(_metadata(obj).get("fluoro_kind", ""))
        if kind == "layout_image":
            _write_layout_image(images_layout, obj)
        elif kind == "layout_scale_bar":
            _write_scale_bar(images_layout, obj)
        elif kind == "layout_shape":
            _write_layout_shape(images_layout, obj)
        elif kind == "layout_text":
            _write_layout_text(images_layout, obj)
        elif kind == "layout_plot":
            _write_layout_plot(plots, obj)
    return {
        "images_layout": images_layout,
        "fluoro_layout_plots": plots,
        "fluoro_layout_plots_key_present": plots_key_present,
    }


def apply_fluorescence_layout_objects(
    panel: FluorescencePanel,
    layout_objects: Iterable[LayoutObject],
    *,
    append_audit: bool = True,
) -> dict[str, Any]:
    objects = list(layout_objects)
    _validate_layout_object_sources(panel, objects)
    restored = fluorescence_layout_from_layout_objects(objects)
    images_layout = dict(restored.get("images_layout", {}))
    images = panel.images()
    for image in images:
        image_uid = str(image.get("uid", ""))
        payload = dict(images_layout.get(image_uid, {}))
        for key in ("layout_mode", "layout_item_geometries", "layout_shapes", "layout_texts", "layout_scale_bar_template", "layout_scale_bars"):
            if key in payload:
                image[key] = deepcopy(payload[key])
    panel.data["fluorescence_images"] = images
    plots = list(restored.get("fluoro_layout_plots", []))
    if bool(restored.get("fluoro_layout_plots_key_present", False)) or plots:
        panel.analysis["fluoro_layout_plots"] = deepcopy(plots)
    _sync_latest_statistics_plot(panel, plots)
    if append_audit:
        panel.append_audit("apply_fluorescence_layout_objects", f"{len(objects)} object(s)")
    return restored


def fluorescence_export_item_for_scene_bounds(
    panel: FluorescencePanel,
    export_item_key: str,
    width_pt: float,
    height_pt: float,
    *,
    dpi: int = DEFAULT_FLUORESCENCE_COMPOSER_EXPORT_DPI,
    image_path: str | Path = "",
    source_format: str = "",
    source_width_px: int | None = None,
    source_height_px: int | None = None,
    plot_uuid: str = "",
    plot_type: str = "",
) -> LayoutExportItem:
    width_pt = float(width_pt)
    height_pt = float(height_pt)
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("Fluorescence export item bounds must be positive")
    dpi = int(dpi)
    is_layout = export_item_key == FLUORESCENCE_LAYOUT_EXPORT_KEY
    metadata: dict[str, Any] = {"adapter_schema_version": _ADAPTER_SCHEMA_VERSION}
    source_module = FLUORESCENCE_LAYOUT_EXPORT_SOURCE_MODULE if is_layout else FLUORESCENCE_PLOT_EXPORT_SOURCE_MODULE
    if is_layout:
        active = panel.active_image() or {}
        metadata["layout_mode"] = str(active.get("layout_mode", ""))
    else:
        metadata["plot_uuid"] = str(plot_uuid)
        metadata["plot_type"] = str(plot_type or "plot")
    return export_item_with_plot_payload(
        panel,
        LayoutExportItem(
            export_item_id=f"{panel.panel_uuid}:{export_item_key}",
            export_item_type=EXPORT_ITEM_FULL_LAYOUT if is_layout else EXPORT_ITEM_INDIVIDUAL_PLOT,
            source_module=source_module,
            source_panel_uuid=panel.panel_uuid,
            export_item_key=export_item_key,
            source_dpi=dpi,
            preferred_width_mm=width_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            preferred_height_mm=height_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            source_record_id=FLUORESCENCE_LAYOUT_EXPORT_KEY if is_layout else str(plot_uuid),
            source_record_namespace=FLUORESCENCE_LAYOUT_EXPORT_KEY if is_layout else "fluorescence_layout_plot",
            source_context_hash=_layout_context_hash(panel),
            image_path=str(image_path) if image_path else "",
            source_format=source_format,
            source_width_px=source_width_px or max(1, int(round(width_pt * dpi / _POINTS_PER_INCH))),
            source_height_px=source_height_px or max(1, int(round(height_pt * dpi / _POINTS_PER_INCH))),
            metadata=metadata,
        ),
    )


def apply_fluorescence_export_item_metadata(asset_panel: ExternalImagePanel, export_item: LayoutExportItem) -> None:
    asset_panel.data.update(export_item.to_external_image_metadata())


def _root_layout_object(
    panel: FluorescencePanel,
    images_layout: Mapping[str, Any],
    plots: list[dict[str, Any]],
    context_hash: str,
) -> LayoutObject:
    spec = fluorescence_canvas_spec(panel)
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:fluorescence_layout_root",
        object_type="fluorescence_layout_root",
        x=0.0,
        y=0.0,
        width=float(spec.width_px),
        height=float(spec.height_px),
        z_order=0,
        source=_source(panel, "fluorescence_layout_root", "fluorescence_layout_root", context_hash),
        metadata={
            "fluoro_kind": "layout_root",
            "images_layout": deepcopy(dict(images_layout)),
            "fluoro_layout_plots": deepcopy(plots),
            "fluoro_layout_plots_key_present": "fluoro_layout_plots" in panel.analysis,
            "adapter_schema_version": _ADAPTER_SCHEMA_VERSION,
        },
    )


def _layout_image_objects(
    panel: FluorescencePanel,
    image_uid: str,
    geometries: Mapping[str, Any],
    context_hash: str,
) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for layout_key, geometry in geometries.items():
        if isinstance(geometry, Mapping):
            objects.append(_layout_image_object(panel, image_uid, str(layout_key), dict(geometry), context_hash))
    return objects


def _layout_image_object(
    panel: FluorescencePanel,
    image_uid: str,
    layout_key: str,
    geometry: Mapping[str, Any],
    context_hash: str,
) -> LayoutObject:
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:fluorescence_layout_image:{image_uid}:{layout_key}",
        object_type="image",
        x=float(geometry.get("x", 0.0)),
        y=float(geometry.get("y", 0.0)),
        width=float(geometry.get("width", 0.0)),
        height=float(geometry.get("height", 0.0)),
        z_order=100,
        source=_source(panel, f"{image_uid}:{layout_key}", "fluorescence_layout_item_geometry", context_hash),
        asset_key=layout_key,
        hidden=not bool(geometry.get('enabled', True)),
        metadata={
            "fluoro_kind": "layout_image",
            "image_uid": image_uid,
            "layout_key": layout_key,
            "geometry": deepcopy(dict(geometry)),
        },
    )


def _scale_bar_objects(panel: FluorescencePanel, image: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    image_uid = str(image.get("uid", ""))
    geometries = dict(image.get("layout_item_geometries", {}))
    template = ensure_layout_scale_bar_template(image.get("measurement_scale_bar"), image.get("layout_scale_bar_template"))
    objects: list[LayoutObject] = []
    for state in ensure_layout_scale_bar_states(
        measurement_scale=image.get("measurement_scale_bar"),
        existing_states=image.get("layout_scale_bars"),
        target_ids=fluorescence_layout_scale_target_ids(image),
    ):
        if not isinstance(state, Mapping) or not bool(state.get("enabled", True)):
            continue
        target_id = str(state.get("target_id", ""))
        geometry = dict(geometries.get(target_id, {})) if target_id else {}
        if not target_id or not geometry:
            continue
        obj = scale_bar_layout_object(
            panel_uuid=panel.panel_uuid,
            module_kind="fluorescence",
            target_id=f"{image_uid}:{target_id}",
            target_rect=geometry,
            source_metadata=dict(image.get("metadata", {})),
            template=template,
            state=dict(state),
            measurement_scale=image.get("measurement_scale_bar"),
            z_order=360,
        )
        payload = obj.to_dict()
        payload["source"]["source_context_hash"] = context_hash
        payload["metadata"]["fluoro_kind"] = "layout_scale_bar"
        payload["metadata"]["image_uid"] = image_uid
        payload["metadata"]["target_id"] = target_id
        payload["metadata"]["target_rect"] = deepcopy(geometry)
        objects.append(LayoutObject.from_dict(payload))
    return objects


def _shape_objects(
    panel: FluorescencePanel,
    image_uid: str,
    shapes: list[Any],
    context_hash: str,
) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, shape in enumerate(shapes):
        if not isinstance(shape, Mapping):
            continue
        shape_payload = dict(shape)
        shape_uuid = str(shape_payload.get("shape_uuid") or f"shape:{index}")
        shape_type = str(shape_payload.get("shape_type", "rectangle"))
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:fluorescence_shape:{image_uid}:{shape_uuid}",
                object_type="line" if shape_type == "line" else "shape",
                x=float(shape_payload.get("x", 0.0)),
                y=float(shape_payload.get("y", 0.0)),
                width=float(shape_payload.get("width", 0.0)),
                height=float(shape_payload.get("height", 0.0)),
                z_order=275 + index,
                source=_source(panel, f"{image_uid}:{shape_uuid}", "fluorescence_layout_shape", context_hash),
                style=LayoutObjectStyle(
                    stroke_color=str(shape_payload.get("stroke_color", "#FFFFFF")),
                    fill_color=str(shape_payload.get("fill_color", "#FFFFFF")),
                    stroke_width=float(shape_payload.get("stroke_width", 2.0)),
                ),
                metadata={
                    "fluoro_kind": "layout_shape",
                    "image_uid": image_uid,
                    "shape_uuid": shape_uuid,
                    "shape_type": shape_type,
                    "index": index,
                    "shape": deepcopy(shape_payload),
                },
            )
        )
    return objects


def _text_objects(
    panel: FluorescencePanel,
    image_uid: str,
    texts: list[Any],
    context_hash: str,
) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, text in enumerate(texts):
        if not isinstance(text, Mapping):
            continue
        text_payload = dict(text)
        text_uuid = str(text_payload.get("text_uuid") or f"text:{index}")
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:fluorescence_text:{image_uid}:{text_uuid}",
                object_type="text",
                x=float(text_payload.get("x", 0.0)),
                y=float(text_payload.get("y", 0.0)),
                width=float(text_payload.get("width", 0.0)),
                height=float(text_payload.get("height", 0.0)),
                z_order=300 + index,
                source=_source(panel, f"{image_uid}:{text_uuid}", "fluorescence_layout_text", context_hash),
                style=LayoutObjectStyle(
                    text_color=str(text_payload.get("color", "#FFFFFF")),
                    font_family=str(text_payload.get("font_family", "Arial")),
                    font_size_pt=float(text_payload.get("font_size", 10.0)),
                    bold=bool(text_payload.get("bold", False)),
                    italic=bool(text_payload.get("italic", False)),
                ),
                text=str(text_payload.get("text", "")),
                metadata={
                    "fluoro_kind": "layout_text",
                    "image_uid": image_uid,
                    "text_uuid": text_uuid,
                    "index": index,
                    "text_record": deepcopy(text_payload),
                },
            )
        )
    return objects


def _plot_objects(panel: FluorescencePanel, plots: list[dict[str, Any]], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, plot in enumerate(plots):
        plot_uuid = str(plot.get("plot_uuid") or f"plot:{index}")
        style = dict(plot.get("style", {}))
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:fluorescence_plot:{plot_uuid}",
                object_type="plot",
                x=float(plot.get("x", 0.0)),
                y=float(plot.get("y", 0.0)),
                width=float(plot.get("width_px", 300.0)),
                height=float(plot.get("height_px", 220.0)),
                z_order=250 + index,
                hidden=not bool(plot.get("enabled", True)),
                source=_source(panel, plot_uuid, "fluorescence_layout_plot", context_hash),
                style=LayoutObjectStyle(
                    font_family=str(style.get("font_family", "Arial")),
                    font_size_pt=float(style.get("font_size_pt", 7.0)),
                ),
                text=str(plot.get("title", "")),
                asset_key=fluorescence_plot_export_key(plot_uuid),
                metadata={
                    "fluoro_kind": "layout_plot",
                    "plot_uuid": plot_uuid,
                    "plot_type": str(plot.get("plot_type", "statistics")),
                    "index": index,
                    "plot": deepcopy(plot),
                    "svg_text_overrides": deepcopy(dict(plot.get("svg_text_overrides", {}))),
                },
            )
        )
    return objects


def _write_layout_image(images_layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    image_uid = str(metadata.get("image_uid", ""))
    layout_key = str(metadata.get("layout_key", ""))
    if not image_uid or not layout_key:
        return
    image_layout = dict(images_layout.get(image_uid, {}))
    geometries = dict(image_layout.get("layout_item_geometries", {}))
    geometries[layout_key] = {**deepcopy(dict(metadata.get('geometry', {}))),
        "x": float(obj.x),
        "y": float(obj.y),
        "width": float(obj.width),
        "height": float(obj.height),
    }
    image_layout["layout_item_geometries"] = geometries
    images_layout[image_uid] = image_layout


def _write_scale_bar(images_layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    image_uid = str(metadata.get("image_uid", ""))
    target_id = str(metadata.get("target_id", ""))
    if not image_uid or not target_id:
        return
    image_layout = dict(images_layout.get(image_uid, {}))
    template = _template_from_scale_bar_object(obj)
    states = [dict(item) for item in image_layout.get("layout_scale_bars", []) if isinstance(item, Mapping)]
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
    image_layout["layout_scale_bars"] = states
    images_layout[image_uid] = image_layout


def _write_layout_shape(images_layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    image_uid = str(metadata.get("image_uid", ""))
    shape_uuid = str(metadata.get("shape_uuid", ""))
    if not image_uid or not shape_uuid:
        return
    image_layout = dict(images_layout.get(image_uid, {}))
    shapes = list(image_layout.get("layout_shapes", []))
    for index, shape in enumerate(shapes):
        shape_payload = dict(shape)
        if str(shape_payload.get("shape_uuid", "")) != shape_uuid:
            continue
        shape_payload.update(
            {
                "shape_type": str(metadata.get("shape_type", shape_payload.get("shape_type", "rectangle"))),
                "x": float(obj.x),
                "y": float(obj.y),
                "width": float(obj.width),
                "height": float(obj.height),
                "stroke_width": float(obj.style.stroke_width),
                "stroke_color": obj.style.stroke_color,
                "fill_color": obj.style.fill_color,
            }
        )
        write_shared_fields(shape_payload,obj)
        shapes[index] = shape_payload
        image_layout["layout_shapes"] = shapes
        images_layout[image_uid] = image_layout
        return


def _write_layout_text(images_layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    image_uid = str(metadata.get("image_uid", ""))
    text_uuid = str(metadata.get("text_uuid", ""))
    if not image_uid or not text_uuid:
        return
    image_layout = dict(images_layout.get(image_uid, {}))
    texts = list(image_layout.get("layout_texts", []))
    for index, text in enumerate(texts):
        text_payload = dict(text)
        if str(text_payload.get("text_uuid", "")) != text_uuid:
            continue
        text_payload.update(
            {
                "text": obj.text,
                "x": float(obj.x),
                "y": float(obj.y),
                "font_family": obj.style.font_family,
                "font_size": float(obj.style.font_size_pt),
                "bold": bool(obj.style.bold),
                "italic": bool(obj.style.italic),
                "color": obj.style.text_color,
            }
        )
        write_shared_fields(text_payload,obj)
        texts[index] = text_payload
        image_layout["layout_texts"] = texts
        images_layout[image_uid] = image_layout
        return


def _write_layout_plot(plots: list[dict[str, Any]], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    plot_uuid = str(metadata.get("plot_uuid", ""))
    if not plot_uuid:
        return
    for index, plot in enumerate(plots):
        plot_payload = dict(plot)
        if str(plot_payload.get("plot_uuid", "")) != plot_uuid:
            continue
        plot_payload.update(
            {
                "x": float(obj.x),
                "y": float(obj.y),
                "width_px": int(round(float(obj.width))),
                "height_px": int(round(float(obj.height))),
                "enabled": not bool(obj.hidden),
            }
        )
        svg_text_overrides = deepcopy(dict(metadata.get("svg_text_overrides", {})))
        if svg_text_overrides:
            plot_payload["svg_text_overrides"] = svg_text_overrides
        else:
            plot_payload.pop("svg_text_overrides", None)
        plots[index] = plot_payload
        return


def _sync_latest_statistics_plot(panel: FluorescencePanel, plots: list[dict[str, Any]]) -> None:
    latest = dict(panel.analysis.get("fluoro_stats_plot", {}))
    latest_uuid = str(latest.get("plot_uuid", ""))
    if not latest_uuid:
        return
    for plot in plots:
        if str(plot.get("plot_uuid", "")) == latest_uuid:
            panel.analysis["fluoro_stats_plot"] = deepcopy(plot)
            return


def _image_for_uid(panel: FluorescencePanel, image_uid: str | None = None) -> dict[str, Any] | None:
    uid = str(image_uid or panel.data.get("active_image_uid") or "")
    if uid:
        for image in panel.images():
            if str(image.get("uid", "")) == uid:
                return image
    return panel.active_image()


def _images_layout_payload(panel: FluorescencePanel) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for image in panel.images():
        image_uid = str(image.get("uid", ""))
        if not image_uid:
            continue
        layout: dict[str, Any] = {}
        for key in ("layout_mode", "layout_item_geometries", "layout_shapes", "layout_texts", "layout_scale_bar_template", "layout_scale_bars"):
            if key in image:
                layout[key] = deepcopy(image.get(key))
        payload[image_uid] = layout
    return payload


def _layout_plot_payload(panel: FluorescencePanel) -> list[dict[str, Any]]:
    return [deepcopy(dict(plot)) for plot in panel.analysis.get("fluoro_layout_plots", []) if isinstance(plot, Mapping)]


def layout_context_hash(panel: FluorescencePanel) -> str:
    return _layout_context_hash(panel)


def _layout_context_hash(panel: FluorescencePanel) -> str:
    return sha256_json(
        {
            "panel_uuid": panel.panel_uuid,
            "active_image_uid": panel.data.get("active_image_uid", ""),
            "images_layout": _images_layout_payload(panel),
            "fluoro_layout_plots": _layout_plot_payload(panel),
        }
    )


def _source(panel: FluorescencePanel, record_id: str, namespace: str, context_hash: str) -> LayoutSourceIdentity:
    return LayoutSourceIdentity(
        source_module=panel.source_module or FluorescencePanel.SOURCE_MODULE,
        source_panel_uuid=panel.panel_uuid,
        source_record_id=str(record_id),
        source_record_namespace=namespace,
        source_context_hash=context_hash,
    )


def _validate_layout_object_sources(panel: FluorescencePanel, layout_objects: Iterable[LayoutObject]) -> None:
    for obj in layout_objects:
        metadata = _metadata(obj)
        if metadata.get("fluoro_kind") == "layout_root" and (
            obj.source is None or obj.source.source_panel_uuid != panel.panel_uuid
        ):
            raise ValueError("Fluorescence layout root source_panel_uuid does not match panel")
        if obj.source is not None and obj.source.source_panel_uuid != panel.panel_uuid:
            raise ValueError("Fluorescence layout object source_panel_uuid does not match panel")


def _metadata(obj: LayoutObject) -> dict[str, Any]:
    return _json_copy(obj.metadata)


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_copy(item) for item in value]
    return value


def _scene_position(positions: Mapping[str, Any], category: str, uid: str, nested_key: str | None = None) -> dict[str, Any]:
    category_payload = positions.get(category, {})
    if not isinstance(category_payload, Mapping):
        return {}
    payload = category_payload.get(uid, {})
    if nested_key is not None and isinstance(payload, Mapping):
        payload = payload.get(nested_key, {})
    return dict(payload) if isinstance(payload, Mapping) else {}


def _update_geometry_payload(payload: dict[str, Any], geometry: Mapping[str, Any], *, width_key: str, height_key: str) -> None:
    if "x" in geometry:
        payload["x"] = float(geometry["x"])
    if "y" in geometry:
        payload["y"] = float(geometry["y"])
    if width_key in geometry:
        payload["width"] = float(geometry[width_key])
    elif "width" in geometry:
        payload["width"] = float(geometry["width"])
    if height_key in geometry:
        payload["height"] = float(geometry[height_key])
    elif "height" in geometry:
        payload["height"] = float(geometry["height"])


def _template_from_scale_bar_object(obj: LayoutObject) -> dict[str, Any]:
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
    rect = dict(metadata.get("target_rect", {}))
    if rect:
        target_x = float(rect.get("x", 0.0))
        target_y = float(rect.get("y", 0.0))
        target_width = max(1.0, float(rect.get("width", 1.0)))
        target_height = max(1.0, float(rect.get("height", 1.0)))
        if str(template["anchor"]).endswith("right"):
            template["offset_x_fraction"] = max(0.0, (target_x + target_width - float(obj.x) - float(obj.width)) / target_width)
        else:
            template["offset_x_fraction"] = max(0.0, (float(obj.x) - target_x) / target_width)
        if str(template["anchor"]).startswith("bottom"):
            template["offset_y_fraction"] = max(0.0, (target_y + target_height - float(obj.y) - float(obj.height)) / target_height)
        else:
            template["offset_y_fraction"] = max(0.0, (float(obj.y) - target_y) / target_height)
    return template
