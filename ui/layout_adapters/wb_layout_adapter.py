from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.layout_export_items import EXPORT_ITEM_FULL_LAYOUT, EXPORT_ITEM_STATISTICS_CHART, LayoutExportItem
from core.models import ExternalImagePanel, WesternBlotPanel
from core.models.layout_editor import LayoutCanvasSpec, LayoutObject, LayoutObjectStyle, LayoutSourceIdentity
from core.utils.hashing import sha256_json
from ui.layout_adapters.chart_adapter import export_item_with_plot_payload


WB_LAYOUT_EXPORT_KEY = "wb_layout"
WB_STATS_PLOT_EXPORT_KEY = "wb_stats_plot"
WB_EXPORT_SOURCE_MODULE = "wb_export_item"
DEFAULT_WB_COMPOSER_EXPORT_DPI = 1200

_MM_PER_INCH = 25.4
_POINTS_PER_INCH = 72.0
_ADAPTER_SCHEMA_VERSION = 1


def wb_canvas_spec(panel: WesternBlotPanel, dpi: int = 300) -> LayoutCanvasSpec:
    image_data = dict(panel.data.get("image_data", {}))
    layout = dict(panel.data.get("wb_layout", {}))
    stats_plot = dict(layout.get("stats_plot", {}))
    width = int(image_data.get("width_px") or stats_plot.get("width_px") or 1)
    height = int(image_data.get("height_px") or stats_plot.get("height_px") or 1)
    return LayoutCanvasSpec(
        canvas_id=f"{panel.panel_uuid}:wb_canvas",
        width_px=max(1, width),
        height_px=max(1, height),
        dpi=int(dpi),
        background_color="#FFFFFF",
        unit="px",
        metadata={
            "source_module": panel.source_module or WesternBlotPanel.SOURCE_MODULE,
            "source_panel_uuid": panel.panel_uuid,
            "adapter_schema_version": _ADAPTER_SCHEMA_VERSION,
        },
    )


def wb_layout_objects(panel: WesternBlotPanel) -> list[LayoutObject]:
    layout = _wb_layout_payload(panel)
    panel_layout = deepcopy(panel.data.get("layout", {}))
    context_hash = _layout_context_hash(panel)
    objects: list[LayoutObject] = [_root_layout_object(panel, layout, panel_layout, context_hash)]
    objects.extend(_band_layout_objects(panel, layout, context_hash))
    objects.extend(_lane_label_objects(panel, layout, context_hash))
    objects.extend(_band_label_objects(panel, layout, context_hash))
    objects.extend(_hierarchy_objects(panel, layout, context_hash))
    stats_plot = dict(layout.get("stats_plot", {}))
    if stats_plot.get("enabled"):
        objects.append(_stats_plot_object(panel, stats_plot, context_hash))
        objects.extend(_stats_label_objects(panel, layout, stats_plot, context_hash))
    objects.extend(LayoutObject.from_dict(record) for record in layout.get('free_annotations',[]))
    return objects


def wb_layout_from_layout_objects(layout_objects: Iterable[LayoutObject]) -> dict[str, Any]:
    objects = list(layout_objects)
    root = next((obj for obj in objects if _metadata(obj).get("wb_kind") == "layout_root"), None)
    if root is None:
        raise ValueError("WB layout objects must include a layout_root object")
    layout = deepcopy(_metadata(root).get("wb_layout", {}))
    for obj in objects:
        kind = str(_metadata(obj).get("wb_kind", ""))
        if kind == "lane_label":
            _write_lane_label(layout, obj)
        elif kind == "protein_label":
            _write_band_text(layout, obj, "protein")
            _write_manual_position_if_needed(layout, obj)
            _write_text_style_if_changed(layout, obj)
        elif kind == "marker_label":
            _write_band_text(layout, obj, "mw_kda")
            _write_manual_position_if_needed(layout, obj)
            _write_text_style_if_changed(layout, obj)
        elif kind in {"condition_label", "condition_value", "group_label"}:
            _write_hierarchy_text(layout, obj)
            _write_manual_position_if_needed(layout, obj)
            _write_text_style_if_changed(layout, obj)
        elif kind == "stats_plot":
            _write_stats_plot_geometry(layout, obj)
        elif kind == "stats_label":
            _write_stats_label(layout, obj)
            _write_text_style_if_changed(layout, obj)
        elif kind == "layout_line":
            _write_line_state(layout, obj)
        elif kind == 'free_annotation':
            records=layout.setdefault('free_annotations',[])
            index=next((i for i,r in enumerate(records) if r.get('object_id')==obj.object_id),None)
            if index is None:records.append(obj.to_dict())
            else:records[index]=obj.to_dict()
    return layout


def apply_wb_layout_objects(panel: WesternBlotPanel, layout_objects: Iterable[LayoutObject]) -> dict[str, Any]:
    objects = list(layout_objects)
    _validate_layout_object_sources(panel, objects)
    layout = wb_layout_from_layout_objects(objects)
    panel.data["wb_layout"] = layout
    _sync_annotations_from_wb_layout(panel, layout)
    panel.append_audit("apply_wb_layout_objects", f"{len(objects)} object(s)")
    return layout


def wb_export_item_for_scene_bounds(
    panel: WesternBlotPanel,
    export_item_key: str,
    width_pt: float,
    height_pt: float,
    *,
    dpi: int = DEFAULT_WB_COMPOSER_EXPORT_DPI,
    image_path: str | Path = "",
    source_format: str = "",
) -> LayoutExportItem:
    width_pt = float(width_pt)
    height_pt = float(height_pt)
    if width_pt <= 0 or height_pt <= 0:
        raise ValueError("WB export item bounds must be positive")
    metadata: dict[str, Any] = {"adapter_schema_version": _ADAPTER_SCHEMA_VERSION}
    if export_item_key == WB_LAYOUT_EXPORT_KEY:
        metadata["contains_wb_hierarchy"] = bool(panel.data.get("wb_layout", {}).get("lane_hierarchy", {}).get("levels"))
    return export_item_with_plot_payload(
        panel,
        LayoutExportItem(
            export_item_id=f"{panel.panel_uuid}:{export_item_key}",
            export_item_type=_export_item_type(export_item_key),
            source_module=WB_EXPORT_SOURCE_MODULE,
            source_panel_uuid=panel.panel_uuid,
            export_item_key=export_item_key,
            source_dpi=int(dpi),
            preferred_width_mm=width_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            preferred_height_mm=height_pt / _POINTS_PER_INCH * _MM_PER_INCH,
            source_record_id=export_item_key,
            source_record_namespace=export_item_key,
            source_context_hash=_layout_context_hash(panel),
            image_path=str(image_path) if image_path else "",
            source_format=source_format,
            source_width_px=max(1, int(round(width_pt * int(dpi) / _POINTS_PER_INCH))),
            source_height_px=max(1, int(round(height_pt * int(dpi) / _POINTS_PER_INCH))),
            metadata=metadata,
        ),
    )


def apply_wb_export_item_metadata(asset_panel: ExternalImagePanel, export_item: LayoutExportItem) -> None:
    asset_panel.data.update(export_item.to_external_image_metadata())


def _root_layout_object(panel: WesternBlotPanel, layout: dict[str, Any], panel_layout: dict[str, Any], context_hash: str) -> LayoutObject:
    spec = wb_canvas_spec(panel)
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:wb_layout",
        object_type="wb_layout_root",
        x=float(panel_layout.get("image_x", 0.0)),
        y=float(panel_layout.get("image_y", 0.0)),
        width=float(spec.width_px),
        height=float(spec.height_px),
        z_order=0,
        source=_source(panel, WB_LAYOUT_EXPORT_KEY, "wb_layout", context_hash),
        metadata={
            "wb_kind": "layout_root",
            "wb_layout": layout,
            "panel_layout": panel_layout,
            "adapter_schema_version": _ADAPTER_SCHEMA_VERSION,
        },
    )


def _band_layout_objects(panel: WesternBlotPanel, layout: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, band in enumerate(list(layout.get("bands", []))):
        band_payload = dict(band)
        roi = dict(band_payload.get("roi", {}))
        record_id = str(band_payload.get("crop_id") or band_payload.get("strip_id") or f"band:{index}")
        objects.append(
            LayoutObject(
                object_id=f"{panel.panel_uuid}:wb_band:{index}",
                object_type="image",
                x=float(roi.get("x", 0.0)),
                y=float(roi.get("y", 0.0)),
                width=float(roi.get("w", 0.0)),
                height=float(roi.get("h", 0.0)),
                z_order=10 + index,
                source=_source(panel, record_id, "wb_layout_band", context_hash),
                text=str(band_payload.get("protein", "")),
                asset_key=str(band_payload.get("image_path") or band_payload.get("strip_id") or record_id),
                metadata={"wb_kind": "band", "index": index, "band": deepcopy(band_payload)},
            )
        )
    return objects


def _lane_label_objects(panel: WesternBlotPanel, layout: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    return [
        _text_object(
            panel,
            context_hash,
            kind="lane",
            object_kind="lane_label",
            index=index,
            text=str(label),
            namespace="wb_layout_lane_label",
            z_order=100 + index,
            layout=layout,
        )
        for index, label in enumerate(list(layout.get("lane_labels", [])))
    ]


def _band_label_objects(panel: WesternBlotPanel, layout: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    objects: list[LayoutObject] = []
    for index, band in enumerate(list(layout.get("bands", []))):
        protein = str(dict(band).get("protein", "")).strip()
        if protein:
            objects.append(
                _text_object(
                    panel,
                    context_hash,
                    kind="protein",
                    object_kind="protein_label",
                    index=index,
                    text=protein,
                    namespace="wb_layout_protein_label",
                    z_order=200 + index,
                    layout=layout,
                )
            )
        marker = str(dict(band).get("mw_kda", "")).strip()
        if marker:
            objects.append(
                _text_object(
                    panel,
                    context_hash,
                    kind="marker",
                    object_kind="marker_label",
                    index=index,
                    text=marker,
                    namespace="wb_layout_marker_label",
                    z_order=300 + index,
                    layout=layout,
                )
            )
    return objects


def _hierarchy_objects(panel: WesternBlotPanel, layout: Mapping[str, Any], context_hash: str) -> list[LayoutObject]:
    levels = list(dict(layout.get("lane_hierarchy", {})).get("levels", []))
    objects: list[LayoutObject] = []
    condition_row_index = 0
    for level_index, level in enumerate(levels):
        level_payload = dict(level)
        if level_payload.get("type") == "condition_row":
            objects.append(
                _text_object(
                    panel,
                    context_hash,
                    kind="condition_label",
                    object_kind="condition_label",
                    index=condition_row_index,
                    text=str(level_payload.get("row_label", "")),
                    namespace="wb_layout_condition_label",
                    z_order=400 + condition_row_index,
                    layout=layout,
                    extra_metadata={"level_index": level_index, "condition_row_index": condition_row_index},
                )
            )
            for value_index, value in enumerate(list(level_payload.get("values", []))):
                objects.append(
                    _text_object(
                        panel,
                        context_hash,
                        kind="condition",
                        object_kind="condition_value",
                        index=value_index,
                        text=str(value),
                        namespace="wb_layout_condition_value",
                        z_order=420 + value_index,
                        layout=layout,
                        extra_metadata={"level_index": level_index, "condition_row_index": condition_row_index},
                    )
                )
            condition_row_index += 1
            continue
        level_number = int(level_payload.get("level", level_index + 1))
        for group_index, group_def in enumerate(list(level_payload.get("groups", []))):
            payload_index = level_number * 1000 + group_index
            kind = f"group_l{level_number}"
            objects.append(
                _text_object(
                    panel,
                    context_hash,
                    kind=kind,
                    object_kind="group_label",
                    index=payload_index,
                    text=str(dict(group_def).get("label", "")),
                    namespace="wb_layout_group_label",
                    z_order=500 + payload_index,
                    layout=layout,
                    extra_metadata={"level_index": level_index, "group_index": group_index},
                )
            )
            if bool(dict(group_def).get("show_bracket", True)):
                uid = f"{kind}:{payload_index}"
                objects.append(_line_object(panel, layout, uid, context_hash, 600 + payload_index))
    return objects


def _stats_plot_object(panel: WesternBlotPanel, stats_plot: Mapping[str, Any], context_hash: str) -> LayoutObject:
    geometry_keys = {key for key in ("x", "y", "width_px", "height_px") if key in stats_plot}
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:wb_stats_plot",
        object_type="plot",
        x=float(stats_plot.get("x", 0.0)),
        y=float(stats_plot.get("y", 0.0)),
        width=float(stats_plot.get("width_px", 520)),
        height=float(stats_plot.get("height_px", 360)),
        z_order=800,
        source=_source(panel, WB_STATS_PLOT_EXPORT_KEY, "wb_stats_plot", context_hash),
        asset_key=WB_STATS_PLOT_EXPORT_KEY,
        metadata={
            "wb_kind": "stats_plot",
            "stats_plot": deepcopy(dict(stats_plot)),
            "geometry_keys_present": sorted(geometry_keys),
            "default_position": {"x": float(stats_plot.get("x", 0.0)), "y": float(stats_plot.get("y", 0.0))},
        },
    )


def _stats_label_objects(
    panel: WesternBlotPanel,
    layout: Mapping[str, Any],
    stats_plot: Mapping[str, Any],
    context_hash: str,
) -> list[LayoutObject]:
    labels = dict(stats_plot.get("labels", {}))
    objects: list[LayoutObject] = []
    for kind, values in labels.items():
        if not isinstance(values, Mapping):
            continue
        for raw_index, text in values.items():
            try:
                index = int(raw_index)
            except (TypeError, ValueError):
                continue
            objects.append(
                _text_object(
                    panel,
                    context_hash,
                    kind=str(kind),
                    object_kind="stats_label",
                    index=index,
                    text=str(text),
                    namespace="wb_stats_plot_label",
                    z_order=900 + index,
                    layout={
                        "manual_positions": _stats_label_offsets_as_manual_positions(stats_plot, str(kind)),
                        "text_styles": dict(layout.get("text_styles", {})),
                        "style": dict(layout.get("style", {})),
                    },
                    extra_metadata={"stats_label_kind": str(kind)},
                )
            )
    return objects


def _text_object(
    panel: WesternBlotPanel,
    context_hash: str,
    *,
    kind: str,
    object_kind: str,
    index: int,
    text: str,
    namespace: str,
    z_order: int,
    layout: Mapping[str, Any],
    extra_metadata: Mapping[str, Any] | None = None,
) -> LayoutObject:
    position = _manual_position(layout, kind, index)
    default_position = {"x": float(position.get("x", 0.0)), "y": float(position.get("y", 0.0))} if position else {"x": 0.0, "y": 0.0}
    raw_style = _text_style_payload(layout, kind, index)
    metadata = {
        "wb_kind": object_kind,
        "text_kind": kind,
        "index": int(index),
        "manual_position": deepcopy(position) if position else None,
        "default_position": default_position,
        "text_style": raw_style,
    }
    metadata.update(dict(extra_metadata or {}))
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:{object_kind}:{kind}:{index}",
        object_type="text",
        x=float(default_position["x"]),
        y=float(default_position["y"]),
        width=0.0,
        height=0.0,
        rotation_deg=float(raw_style.get("rotation", 0.0)),
        z_order=int(z_order),
        source=_source(panel, f"{kind}:{index}", namespace, context_hash),
        style=_layout_style_from_text_payload(raw_style),
        text=text,
        metadata=metadata,
    )


def _line_object(panel: WesternBlotPanel, layout: Mapping[str, Any], uid: str, context_hash: str, z_order: int) -> LayoutObject:
    line_styles = dict(layout.get("line_styles", {}))
    style_payload = dict(line_styles.get(uid, {}))
    pos = dict(style_payload.get("pos", {})) if isinstance(style_payload.get("pos"), Mapping) else {}
    default_position = {"x": float(pos.get("x", 0.0)), "y": float(pos.get("y", 0.0))}
    return LayoutObject(
        object_id=f"{panel.panel_uuid}:layout_line:{uid}",
        object_type="line",
        x=float(default_position["x"]),
        y=float(default_position["y"]),
        width=0.0,
        height=0.0,
        z_order=int(z_order),
        source=_source(panel, uid, "wb_layout_line", context_hash),
        style=LayoutObjectStyle(
            stroke_color=str(style_payload.get("color", "#111111")),
            stroke_width=float(style_payload.get("width", 0.75)),
        ),
        metadata={
            "wb_kind": "layout_line",
            "line_uid": uid,
            "line_style": style_payload,
            "manual_position": deepcopy(pos) if pos else None,
            "default_position": default_position,
        },
    )


def _write_lane_label(layout: dict[str, Any], obj: LayoutObject) -> None:
    index = int(_metadata(obj).get("index", -1))
    labels = list(layout.get("lane_labels", []))
    if 0 <= index < len(labels):
        labels[index] = obj.text
        layout["lane_labels"] = labels
    _write_manual_position_if_needed(layout, obj)
    _write_text_style_if_changed(layout, obj)


def _write_band_text(layout: dict[str, Any], obj: LayoutObject, key: str) -> None:
    index = int(_metadata(obj).get("index", -1))
    bands = list(layout.get("bands", []))
    if 0 <= index < len(bands):
        band = dict(bands[index])
        band[key] = obj.text
        bands[index] = band
        layout["bands"] = bands


def _write_hierarchy_text(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    levels = list(dict(layout.get("lane_hierarchy", {})).get("levels", []))
    level_index = int(metadata.get("level_index", -1))
    if level_index < 0 or level_index >= len(levels):
        return
    level = dict(levels[level_index])
    if metadata.get("wb_kind") == "condition_label":
        level["row_label"] = obj.text
    elif metadata.get("wb_kind") == "condition_value":
        values = list(level.get("values", []))
        index = int(metadata.get("index", -1))
        if 0 <= index < len(values):
            values[index] = obj.text
            level["values"] = values
    elif metadata.get("wb_kind") == "group_label":
        groups = list(level.get("groups", []))
        group_index = int(metadata.get("group_index", -1))
        if 0 <= group_index < len(groups):
            group = dict(groups[group_index])
            group["label"] = obj.text
            groups[group_index] = group
            level["groups"] = groups
    levels[level_index] = level
    hierarchy = dict(layout.get("lane_hierarchy", {}))
    hierarchy["levels"] = levels
    layout["lane_hierarchy"] = hierarchy


def _write_stats_plot_geometry(layout: dict[str, Any], obj: LayoutObject) -> None:
    stats_plot = dict(layout.get("stats_plot", {}))
    keys_present = set(_metadata(obj).get("geometry_keys_present", []))
    default = dict(_metadata(obj).get("default_position", {}))
    if "x" in keys_present or float(obj.x) != float(default.get("x", 0.0)):
        stats_plot["x"] = float(obj.x)
    if "y" in keys_present or float(obj.y) != float(default.get("y", 0.0)):
        stats_plot["y"] = float(obj.y)
    if "width_px" in keys_present or float(obj.width) != float(_metadata(obj).get("stats_plot", {}).get("width_px", 520)):
        stats_plot["width_px"] = int(round(float(obj.width)))
    if "height_px" in keys_present or float(obj.height) != float(_metadata(obj).get("stats_plot", {}).get("height_px", 360)):
        stats_plot["height_px"] = int(round(float(obj.height)))
    layout["stats_plot"] = stats_plot


def _write_stats_label(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    stats_plot = dict(layout.get("stats_plot", {}))
    labels = dict(stats_plot.get("labels", {}))
    kind = str(metadata.get("stats_label_kind") or metadata.get("text_kind", ""))
    index = str(int(metadata.get("index", 0)))
    kind_labels = dict(labels.get(kind, {}))
    kind_labels[index] = obj.text
    labels[kind] = kind_labels
    stats_plot["labels"] = labels
    default = dict(metadata.get("default_position", {}))
    if metadata.get("manual_position") is not None or float(obj.x) != float(default.get("x", 0.0)) or float(obj.y) != float(default.get("y", 0.0)):
        label_offsets = dict(stats_plot.get("label_offsets", {}))
        kind_offsets = dict(label_offsets.get(kind, {}))
        kind_offsets[index] = {"x": float(obj.x), "y": float(obj.y)}
        label_offsets[kind] = kind_offsets
        stats_plot["label_offsets"] = label_offsets
    layout["stats_plot"] = stats_plot


def _write_line_state(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    uid = str(metadata.get("line_uid", ""))
    if not uid:
        return
    style_payload = dict(metadata.get("line_style", {}))
    default = dict(metadata.get("default_position", {}))
    if metadata.get("manual_position") is not None or float(obj.x) != float(default.get("x", 0.0)) or float(obj.y) != float(default.get("y", 0.0)):
        style_payload["pos"] = {"x": float(obj.x), "y": float(obj.y)}
    if obj.style.stroke_color != str(style_payload.get("color", obj.style.stroke_color)):
        style_payload["color"] = obj.style.stroke_color
    if float(obj.style.stroke_width) != float(style_payload.get("width", obj.style.stroke_width)):
        style_payload["width"] = float(obj.style.stroke_width)
    if style_payload:
        line_styles = dict(layout.get("line_styles", {}))
        line_styles[uid] = style_payload
        layout["line_styles"] = line_styles


def _write_manual_position_if_needed(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    kind = str(metadata.get("text_kind", ""))
    index = str(int(metadata.get("index", 0)))
    default = dict(metadata.get("default_position", {}))
    if metadata.get("manual_position") is None and float(obj.x) == float(default.get("x", 0.0)) and float(obj.y) == float(default.get("y", 0.0)):
        return
    positions = dict(layout.get("manual_positions", {}))
    kind_positions = dict(positions.get(kind, {}))
    kind_positions[index] = {"x": float(obj.x), "y": float(obj.y)}
    positions[kind] = kind_positions
    layout["manual_positions"] = positions


def _write_text_style_if_changed(layout: dict[str, Any], obj: LayoutObject) -> None:
    metadata = _metadata(obj)
    kind = str(metadata.get("text_kind", ""))
    if not kind:
        return
    index = str(int(metadata.get("index", 0)))
    original = dict(metadata.get("text_style", {}))
    current = {
        "font_family": obj.style.font_family,
        "font_size_pt": float(obj.style.font_size_pt),
        "color": obj.style.text_color,
        "bold": bool(obj.style.bold),
        "italic": bool(obj.style.italic),
        "rotation": float(obj.rotation_deg),
    }
    changes = {
        key: value
        for key, value in current.items()
        if _style_value_changed(value, original.get(key))
    }
    if not changes:
        return
    styles = dict(layout.get("text_styles", {}))
    kind_styles = dict(styles.get(kind, {}))
    items = dict(kind_styles.get("items", {}))
    item_style = dict(items.get(index, {}))
    item_style.update(changes)
    items[index] = item_style
    kind_styles["items"] = items
    styles[kind] = kind_styles
    layout["text_styles"] = styles


def _layout_style_from_text_payload(payload: Mapping[str, Any]) -> LayoutObjectStyle:
    color = str(payload.get("color", "#111111"))
    return LayoutObjectStyle(
        stroke_color=color,
        fill_color="transparent",
        text_color=color,
        stroke_width=0.0,
        opacity=1.0,
        font_family=str(payload.get("font_family", "Arial")),
        font_size_pt=float(payload.get("font_size_pt", payload.get("tick_font_pt", 6.0))),
        bold=bool(payload.get("bold", False)),
        italic=bool(payload.get("italic", False)),
        metadata={"wb_text_style": deepcopy(dict(payload))},
    )


def _style_value_changed(current: Any, original: Any) -> bool:
    if isinstance(current, float) or isinstance(original, float | int):
        try:
            return abs(float(current) - float(original)) > 1e-9
        except (TypeError, ValueError):
            return True
    return current != original


def _text_style_payload(layout: Mapping[str, Any], kind: str, index: int) -> dict[str, Any]:
    styles = dict(layout.get("text_styles", {}))
    kind_styles = dict(styles.get(kind, {}))
    payload = dict(kind_styles.get("default", {}))
    payload.update(dict(dict(kind_styles.get("items", {})).get(str(index), {})))
    base_style = dict(layout.get("style", {}))
    payload.setdefault("font_family", base_style.get("font_family", "Arial"))
    payload.setdefault("font_size_pt", base_style.get("tick_font_pt", 6.0))
    payload.setdefault("color", "#111111")
    payload.setdefault("bold", False)
    payload.setdefault("italic", False)
    payload.setdefault("rotation", 0.0)
    return payload


def _manual_position(layout: Mapping[str, Any], kind: str, index: int) -> dict[str, Any] | None:
    saved = dict(dict(layout.get("manual_positions", {})).get(kind, {})).get(str(index))
    return dict(saved) if isinstance(saved, Mapping) else None


def _stats_label_offsets_as_manual_positions(stats_plot: Mapping[str, Any], kind: str) -> dict[str, Any]:
    label_offsets = dict(stats_plot.get("label_offsets", {}))
    kind_offsets = dict(label_offsets.get(kind, {}))
    return {kind: deepcopy(kind_offsets)} if kind_offsets else {}


def _wb_layout_payload(panel: WesternBlotPanel) -> dict[str, Any]:
    return deepcopy(dict(panel.data.get("wb_layout", {})))


def layout_context_hash(panel: WesternBlotPanel) -> str:
    return _layout_context_hash(panel)


def _layout_context_hash(panel: WesternBlotPanel) -> str:
    return sha256_json(
        {
            "panel_uuid": panel.panel_uuid,
            "layout": panel.data.get("layout", {}),
            "wb_layout": panel.data.get("wb_layout", {}),
            "annotations": panel.data.get("annotations", {}),
        }
    )


def _source(panel: WesternBlotPanel, record_id: str, namespace: str, context_hash: str) -> LayoutSourceIdentity:
    return LayoutSourceIdentity(
        source_module=panel.source_module or WesternBlotPanel.SOURCE_MODULE,
        source_panel_uuid=panel.panel_uuid,
        source_record_id=str(record_id),
        source_record_namespace=namespace,
        source_context_hash=context_hash,
    )


def _validate_layout_object_sources(panel: WesternBlotPanel, layout_objects: Iterable[LayoutObject]) -> None:
    for obj in layout_objects:
        metadata = _metadata(obj)
        if metadata.get("wb_kind") == "layout_root" and (
            obj.source is None or obj.source.source_panel_uuid != panel.panel_uuid
        ):
            raise ValueError("WB layout root source_panel_uuid does not match panel")
        if obj.source is not None and obj.source.source_panel_uuid != panel.panel_uuid:
            raise ValueError("WB layout object source_panel_uuid does not match panel")


def _sync_annotations_from_wb_layout(panel: WesternBlotPanel, layout: Mapping[str, Any]) -> None:
    annotations = dict(panel.data.get("annotations", {}))
    bands = list(layout.get("bands", []))
    annotations["lane_labels"] = list(layout.get("lane_labels", []))
    annotations["protein_labels"] = [str(band.get("protein", "")) for band in bands if str(band.get("protein", "")).strip()]
    annotations["markers"] = [str(band.get("mw_kda", "")) for band in bands if str(band.get("mw_kda", "")).strip()]
    panel.data["annotations"] = annotations


def _metadata(obj: LayoutObject) -> dict[str, Any]:
    return _json_copy(obj.metadata)


def _export_item_type(export_item_key: str) -> str:
    if export_item_key == WB_LAYOUT_EXPORT_KEY:
        return EXPORT_ITEM_FULL_LAYOUT
    if export_item_key == WB_STATS_PLOT_EXPORT_KEY:
        return EXPORT_ITEM_STATISTICS_CHART
    raise ValueError(f"unsupported WB export item key: {export_item_key}")


def _json_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_copy(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_copy(item) for item in value]
    return value
