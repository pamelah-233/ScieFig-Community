from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from core import measurement_scale as _measurement_scale
from core.models.layout_editor import LayoutObject, LayoutObjectStyle, LayoutSourceIdentity


DEFAULT_SCALE_BAR_TEMPLATE: dict[str, Any] = {
    "label_length_um": 50.0,
    "label_unit": "um",
    "show_text": True,
    "font_family": "Arial",
    "font_size": 10.0,
    "bold": False,
    "italic": False,
    "text_color": "#FFFFFF",
    "line_color": "#FFFFFF",
    "line_thickness": 4.0,
    "text_position": "above",
    "label_gap": 4.0,
    "anchor": "bottom_right",
    "offset_x_fraction": 0.06,
    "offset_y_fraction": 0.08,
    "fallback_bar_fraction": 0.18,
}

_ANCHORS = {"top_left", "top_right", "bottom_left", "bottom_right"}
_TEXT_POSITIONS = {"above", "below"}


@dataclass(frozen=True)
class ScaleBarCalibration:
    source: str
    um_per_px: float | None
    um_per_px_x: float = 0.0
    um_per_px_y: float = 0.0
    assumed: bool = False
    calibrated: bool = False


def normalize_scale_bar_template(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = dict(payload or {})
    template = dict(DEFAULT_SCALE_BAR_TEMPLATE)

    label_length_um = _positive_float(payload.get("label_length_um"))
    if label_length_um is not None:
        template["label_length_um"] = label_length_um
    label_unit = str(payload.get("label_unit", template["label_unit"]) or template["label_unit"]).strip()
    template["label_unit"] = label_unit or template["label_unit"]
    template["show_text"] = bool(payload.get("show_text", template["show_text"]))
    for key in ("font_family", "text_color", "line_color"):
        value = str(payload.get(key, template[key]) or template[key]).strip()
        template[key] = value or template[key]
    for key in ("font_size", "line_thickness", "label_gap"):
        value = _positive_float(payload.get(key))
        if value is not None:
            template[key] = value
    for key in ("offset_x_fraction", "offset_y_fraction", "fallback_bar_fraction"):
        value = _non_negative_float(payload.get(key))
        if value is not None:
            template[key] = value
    template["bold"] = bool(payload.get("bold", template["bold"]))
    template["italic"] = bool(payload.get("italic", template["italic"]))
    anchor = str(payload.get("anchor", template["anchor"]) or template["anchor"]).strip()
    if anchor in _ANCHORS:
        template["anchor"] = anchor
    text_position = str(payload.get("text_position", template["text_position"]) or template["text_position"]).strip()
    if text_position in _TEXT_POSITIONS:
        template["text_position"] = text_position
    return template


def resolve_scale_bar_calibration(
    source_metadata: Mapping[str, Any] | None,
    *,
    manual_um_per_px: Any = None,
    saved_scale: Mapping[str, Any] | None = None,
) -> ScaleBarCalibration:
    metadata = source_metadata if isinstance(source_metadata, Mapping) else {}
    if manual_um_per_px is not None and manual_um_per_px != "":
        resolved = _measurement_scale.resolve_calibration(
            metadata,
            {"calibration_source": "manual", "um_per_px": manual_um_per_px},
        )
        calibration = _calibration_from_resolved(resolved)
        if not calibration.calibrated or calibration.source != "manual":
            raise ValueError("manual_um_per_px must be positive")
        return calibration
    resolved = _measurement_scale.resolve_calibration(metadata, saved_scale)
    return _calibration_from_resolved(resolved)


def scale_bar_layout_object(
    *,
    panel_uuid: str,
    module_kind: str,
    target_id: str,
    target_rect: Mapping[str, Any],
    source_metadata: Mapping[str, Any] | None,
    template: Mapping[str, Any] | None = None,
    state: Mapping[str, Any] | None = None,
    measurement_scale: Mapping[str, Any] | None = None,
    z_order: int = 450,
) -> LayoutObject:
    state_payload = dict(state or {})
    resolved_template = normalize_scale_bar_template({**dict(template or {}), **dict(state_payload.get("template") or {})})
    calibration = resolve_scale_bar_calibration(
        source_metadata,
        manual_um_per_px=state_payload.get("manual_um_per_px"),
        saved_scale=measurement_scale if isinstance(measurement_scale, Mapping) else None,
    )
    target = _rect(target_rect)
    source_payload = dict(source_metadata or {})
    width = _bar_width(
        label_length_um=float(resolved_template["label_length_um"]),
        calibration=calibration,
        source_metadata=source_payload,
        target_width=float(target["width"]),
        fallback_fraction=float(resolved_template["fallback_bar_fraction"]),
    )
    height = _bar_height(resolved_template)
    x, y = _anchored_position(target, width, height, resolved_template)
    label_text = _display_label(resolved_template, calibration, width, source_payload, float(target["width"]))
    object_id = f"{panel_uuid}:{module_kind}_scale_bar:{target_id}"
    namespace = f"{module_kind}_layout_scale_bar"
    return LayoutObject(
        object_id=object_id,
        object_type="scale_bar",
        x=x,
        y=y,
        width=width,
        height=height,
        z_order=int(z_order),
        source=LayoutSourceIdentity(module_kind, panel_uuid, str(target_id), namespace, ""),
        style=LayoutObjectStyle(
            stroke_color=str(resolved_template["line_color"]),
            fill_color="transparent",
            text_color=str(resolved_template["text_color"]),
            stroke_width=float(resolved_template["line_thickness"]),
            font_family=str(resolved_template["font_family"]),
            font_size_pt=float(resolved_template["font_size"]),
            bold=bool(resolved_template["bold"]),
            italic=bool(resolved_template["italic"]),
            metadata={
                "text_position": str(resolved_template["text_position"]),
                "label_gap": float(resolved_template["label_gap"]),
                "show_text": bool(resolved_template["show_text"]),
            },
        ),
        text=label_text if bool(resolved_template["show_text"]) else "",
        metadata={
            "scale_bar_kind": "layout_scale_bar",
            "module_kind": module_kind,
            "target_id": str(target_id),
            "target_rect": dict(target),
            "template": resolved_template,
            "state": state_payload,
            "calibration_source": calibration.source,
            "um_per_px": calibration.um_per_px,
            "assumed": calibration.assumed,
            "label_text": label_text,
        },
    )


_CALIBRATION_LAYOUT_KEYS = (
    "um_per_px",
    "um_per_px_x",
    "um_per_px_y",
    "line_px",
    "display_line_px",
    "line_length_px",
    "length_um",
    "assumed",
    "calibrated",
    "measurement_scale_bar",
)


def measurement_scale_enters_layout(measurement_scale: Mapping[str, Any] | None) -> bool:
    if not isinstance(measurement_scale, Mapping) or not measurement_scale:
        return False
    if measurement_scale.get("enabled") is False:
        return False
    source = str(measurement_scale.get("calibration_source") or "")
    if bool(measurement_scale.get("assumed")) or source == "assumed":
        return False
    if "calibrated" in measurement_scale and not bool(measurement_scale.get("calibrated")):
        return False
    if source in {"manual_line", "manual"}:
        return True
    line = measurement_scale.get("display_line_px")
    if not isinstance(line, Mapping) or not line:
        line = measurement_scale.get("line_px")
    if isinstance(line, Mapping) and line:
        x1 = line.get("x1")
        x2 = line.get("x2")
        y1 = line.get("y1")
        y2 = line.get("y2")
        if x1 != x2 or y1 != y2:
            return True
    return False


def presentation_layout_scale_state(payload: Mapping[str, Any] | None, *, target_id: str, enabled: bool = True) -> dict[str, Any]:
    state = dict(payload) if isinstance(payload, Mapping) else {}
    for key in _CALIBRATION_LAYOUT_KEYS:
        state.pop(key, None)
    state["target_id"] = str(target_id)
    state.setdefault("enabled", bool(enabled))
    return state


def ensure_layout_scale_bar_states(
    *,
    measurement_scale: Mapping[str, Any] | None,
    existing_states: Any = None,
    target_ids: Any = None,
) -> list[dict[str, Any]]:
    existing: list[dict[str, Any]] = []
    by_target: dict[str, dict[str, Any]] = {}
    for item in existing_states or []:
        if not isinstance(item, Mapping):
            continue
        target_id = str(item.get("target_id", "") or "")
        if not target_id:
            continue
        state = presentation_layout_scale_state(item, target_id=target_id)
        existing.append(state)
        by_target[target_id] = state
    if not measurement_scale_enters_layout(measurement_scale):
        return existing
    for raw_target in target_ids or []:
        target_id = str(raw_target or "")
        if not target_id or target_id in by_target:
            continue
        state = presentation_layout_scale_state(None, target_id=target_id, enabled=True)
        by_target[target_id] = state
        existing.append(state)
    return existing


def ensure_layout_scale_bar_template(
    measurement_scale: Mapping[str, Any] | None,
    existing_template: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(existing_template, Mapping) and existing_template:
        return normalize_scale_bar_template(existing_template)
    payload: dict[str, Any] = {}
    if isinstance(measurement_scale, Mapping):
        length = _positive_float(measurement_scale.get("length_um"))
        if length is not None:
            payload["label_length_um"] = length
    return normalize_scale_bar_template(payload)


def seed_layout_scale_presentation(
    container: dict[str, Any],
    *,
    measurement_scale: Mapping[str, Any] | None,
    target_ids: Any,
    states_key: str,
    template_key: str,
) -> None:
    if not isinstance(container, dict):
        return
    if not measurement_scale_enters_layout(measurement_scale):
        return
    container[template_key] = ensure_layout_scale_bar_template(measurement_scale, container.get(template_key))
    container[states_key] = ensure_layout_scale_bar_states(
        measurement_scale=measurement_scale,
        existing_states=container.get(states_key),
        target_ids=target_ids,
    )


def fluorescence_layout_scale_target_ids(image: Mapping[str, Any] | None) -> list[str]:
    payload = image if isinstance(image, Mapping) else {}
    geometries = payload.get("layout_item_geometries")
    ids = [str(key) for key in dict(geometries or {}) if str(key)]
    if ids:
        return ids
    layout_mode = str(payload.get("layout_mode") or "merge_split")
    return [f"{layout_mode}:merge"]


def has_enabled_layout_scale_bars(states: Any) -> bool:
    for item in states or []:
        if isinstance(item, Mapping) and bool(item.get("enabled", True)):
            return True
    return False


def layout_scale_state_for_target(states: Any, target_id: str) -> dict[str, Any] | None:
    target = str(target_id or "")
    if not target:
        return None
    for item in states or []:
        if isinstance(item, Mapping) and str(item.get("target_id", "") or "") == target:
            return dict(item)
    return None


def native_layout_scale_action(states: Any, target_id: str) -> str:
    state = layout_scale_state_for_target(states, target_id)
    if state is None:
        return "absent"
    if not bool(state.get("enabled", True)):
        return "hidden"
    return "paint"


def resolve_native_layout_scale_bar(
    *,
    target_id: str,
    native_width_px: float,
    native_height_px: float,
    source_metadata: Mapping[str, Any] | None = None,
    measurement_scale: Mapping[str, Any] | None = None,
    template: Mapping[str, Any] | None = None,
    states: Any = None,
) -> dict[str, Any]:
    action = native_layout_scale_action(states, target_id)
    if action != "paint":
        return {"action": action}
    state = layout_scale_state_for_target(states, target_id) or {}
    metadata = dict(source_metadata or {})
    width = max(1.0, float(native_width_px))
    height = max(1.0, float(native_height_px))
    if _positive_float(metadata.get("width_px")) is None and _positive_float(metadata.get("width")) is None:
        metadata["width_px"] = width
    if _positive_float(metadata.get("height_px")) is None and _positive_float(metadata.get("height")) is None:
        metadata["height_px"] = height
    obj = scale_bar_layout_object(
        panel_uuid="native",
        module_kind="native",
        target_id=str(target_id),
        target_rect={"x": 0.0, "y": 0.0, "width": width, "height": height},
        source_metadata=metadata,
        template=template,
        state=state,
        measurement_scale=measurement_scale,
    )
    return {
        "action": "paint",
        "x": float(obj.x),
        "y": float(obj.y),
        "width": float(obj.width),
        "height": float(obj.height),
        "text": str(obj.text or ""),
        "style": obj.style.to_dict(),
        "template": dict(obj.metadata.get("template") or normalize_scale_bar_template(template)),
        "label_text": str(obj.metadata.get("label_text") or obj.text or ""),
        "calibration_source": obj.metadata.get("calibration_source"),
        "um_per_px": obj.metadata.get("um_per_px"),
    }


def paint_layout_scale_bar_on_native_image(
    image: Any,
    *,
    target_id: str,
    native_width_px: float | None = None,
    native_height_px: float | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    measurement_scale: Mapping[str, Any] | None = None,
    template: Mapping[str, Any] | None = None,
    states: Any = None,
) -> tuple[Any, str]:
    width = float(native_width_px) if native_width_px else float(image.width())
    height = float(native_height_px) if native_height_px else float(image.height())
    spec = resolve_native_layout_scale_bar(
        target_id=target_id,
        native_width_px=width,
        native_height_px=height,
        source_metadata=source_metadata,
        measurement_scale=measurement_scale,
        template=template,
        states=states,
    )
    action = str(spec.get("action", "absent"))
    if action != "paint":
        return image, action
    from PyQt6.QtCore import QPointF, QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen

    painted = QImage(image)
    if painted.format() != QImage.Format.Format_ARGB32:
        painted = painted.convertToFormat(QImage.Format.Format_ARGB32)
    style = dict(spec.get("style") or {})
    style_metadata = dict(style.get("metadata") or {})
    line_width = max(1.0, float(style.get("stroke_width", 4.0) or 4.0))
    show_text = bool(style_metadata.get("show_text", True)) and bool(spec.get("text"))
    text_position = str(style_metadata.get("text_position", "above"))
    gap = max(0.0, float(style_metadata.get("label_gap", 4.0)))
    font_size = float(style.get("font_size_pt", 10.0) or 10.0)
    text_height = max(0.0, font_size * 1.4) if show_text else 0.0
    origin_x = float(spec["x"])
    origin_y = float(spec["y"])
    bar_width = float(spec["width"])
    bar_height = float(spec["height"])
    if show_text and text_position == "below":
        line_y = origin_y + line_width / 2.0
        text_rect = QRectF(origin_x, origin_y + line_width + gap, bar_width, text_height)
    elif show_text:
        line_y = origin_y + text_height + gap + line_width / 2.0
        text_rect = QRectF(origin_x, origin_y, bar_width, text_height)
    else:
        line_y = origin_y + bar_height / 2.0
        text_rect = QRectF()
    line_color = QColor(str(style.get("stroke_color") or "#FFFFFF"))
    if not line_color.isValid():
        line_color = QColor("#FFFFFF")
    painter = QPainter(painted)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    line_pen = QPen(line_color, line_width)
    line_pen.setCapStyle(Qt.PenCapStyle.SquareCap)
    painter.setPen(line_pen)
    painter.drawLine(QPointF(origin_x, line_y), QPointF(origin_x + bar_width, line_y))
    if show_text:
        font = QFont(str(style.get("font_family") or "Arial"), max(1, int(round(font_size))))
        font.setBold(bool(style.get("bold", False)))
        font.setItalic(bool(style.get("italic", False)))
        painter.setFont(font)
        text_color = QColor(str(style.get("text_color") or line_color.name()))
        if not text_color.isValid():
            text_color = line_color
        painter.setPen(QPen(text_color, 1.0))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, str(spec.get("text") or ""))
    painter.end()
    return painted, "paint"


def apply_native_image_scale_overlay(
    image: Any,
    *,
    target_id: str,
    native_width_px: float | None = None,
    native_height_px: float | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    measurement_scale: Mapping[str, Any] | None = None,
    template: Mapping[str, Any] | None = None,
    states: Any = None,
    fallback_painter: Any = None,
) -> Any:
    painted, action = paint_layout_scale_bar_on_native_image(
        image,
        target_id=target_id,
        native_width_px=native_width_px,
        native_height_px=native_height_px,
        source_metadata=source_metadata,
        measurement_scale=measurement_scale,
        template=template,
        states=states,
    )
    if action == "absent" and callable(fallback_painter):
        return fallback_painter(image)
    return painted


def layout_scale_manual_um_per_px(states: Any) -> float:
    for item in states or []:
        if not isinstance(item, Mapping) or not bool(item.get("enabled", True)):
            continue
        value = _positive_float(item.get("manual_um_per_px"))
        if value is not None:
            return value
    return 0.0


def _bar_width(
    *,
    label_length_um: float,
    calibration: ScaleBarCalibration,
    source_metadata: Mapping[str, Any],
    target_width: float,
    fallback_fraction: float,
) -> float:
    um_per_px = calibration.um_per_px_x if calibration.um_per_px_x and calibration.um_per_px_x > 0 else calibration.um_per_px
    if calibration.calibrated and um_per_px and um_per_px > 0:
        source_width_px = _positive_float(source_metadata.get("width_px")) or _positive_float(source_metadata.get("width")) or target_width
        source_bar_px = label_length_um / um_per_px
        return max(1.0, source_bar_px * target_width / max(1.0, source_width_px))
    return max(1.0, target_width * max(0.01, fallback_fraction))


def _bar_height(template: Mapping[str, Any]) -> float:
    line = float(template["line_thickness"])
    if not bool(template["show_text"]):
        return max(1.0, line)
    return max(1.0, line + float(template["label_gap"]) + float(template["font_size"]) * 1.4)


def _anchored_position(target: Mapping[str, float], width: float, height: float, template: Mapping[str, Any]) -> tuple[float, float]:
    dx = float(target["width"]) * float(template["offset_x_fraction"])
    dy = float(target["height"]) * float(template["offset_y_fraction"])
    anchor = str(template["anchor"])
    x = float(target["x"]) + dx if anchor.endswith("left") else float(target["x"]) + float(target["width"]) - dx - width
    y = float(target["y"]) + dy if anchor.startswith("top") else float(target["y"]) + float(target["height"]) - dy - height
    return x, y


def _rect(payload: Mapping[str, Any]) -> dict[str, float]:
    return {
        "x": float(payload.get("x", 0.0)),
        "y": float(payload.get("y", 0.0)),
        "width": max(1.0, float(payload.get("width", 1.0))),
        "height": max(1.0, float(payload.get("height", 1.0))),
    }


def _calibration_from_resolved(resolved: Mapping[str, Any] | None) -> ScaleBarCalibration:
    payload = dict(resolved or {})
    source = str(payload.get("calibration_source") or "assumed")
    assumed = bool(payload.get("assumed", source == "assumed"))
    um_x = _positive_float(payload.get("um_per_px_x")) or _positive_float(payload.get("um_per_px"))
    um_y = _positive_float(payload.get("um_per_px_y")) or 0.0
    if assumed or um_x is None:
        return ScaleBarCalibration("assumed", None, 0.0, 0.0, True, False)
    return ScaleBarCalibration(source, float(um_x), float(um_x), float(um_y), False, True)


def _display_label(
    template: Mapping[str, Any],
    calibration: ScaleBarCalibration,
    width: float,
    source_metadata: Mapping[str, Any],
    target_width: float,
) -> str:
    if calibration.calibrated and calibration.um_per_px:
        return _label_text(float(template["label_length_um"]), str(template["label_unit"]))
    return _label_text(_source_length_px(width, source_metadata, target_width), "px")


def _source_length_px(display_width: float, source_metadata: Mapping[str, Any], target_width: float) -> float:
    source_width_px = _positive_float(source_metadata.get("width_px")) or _positive_float(source_metadata.get("width"))
    if source_width_px is None or target_width <= 0:
        return display_width
    return display_width * source_width_px / target_width


def _label_text(value: float, unit: str) -> str:
    return f"{value:g} {unit}"


def _positive_float(value: Any) -> float | None:
    resolved = _optional_float(value)
    if resolved is None or resolved <= 0:
        return None
    return resolved


def _non_negative_float(value: Any) -> float | None:
    resolved = _optional_float(value)
    if resolved is None or resolved < 0:
        return None
    return resolved


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
