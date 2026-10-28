from __future__ import annotations

from copy import deepcopy
from math import hypot, isfinite
from typing import Any, Mapping


DEFAULT_SCALE_BAR_LENGTH_UM = 50.0
DEFAULT_SCALE_BAR_FRACTION = 0.18
UM_PER_PX_SPIN_DECIMALS = 6


def positive_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or value is None:
        return float(default)
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return float(default)
    return resolved if isfinite(resolved) and resolved > 0 else float(default)


def default_scale_line(width_px: int, height_px: int, *, fraction: float = DEFAULT_SCALE_BAR_FRACTION) -> dict[str, float]:
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    line_px = max(8.0, width * max(0.01, float(fraction)))
    margin_x = max(8.0, width * 0.06)
    margin_y = max(8.0, height * 0.08)
    x2 = max(1.0, width - margin_x)
    x1 = max(0.0, x2 - line_px)
    y = max(0.0, height - margin_y)
    return {"x1": float(x1), "y1": float(y), "x2": float(x2), "y2": float(y)}


def _finite_coord(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(resolved):
        return None
    return resolved


def line_length_px(line: Mapping[str, Any] | None) -> float:
    if not isinstance(line, Mapping):
        return 0.0
    x1 = _finite_coord(line.get("x1"))
    y1 = _finite_coord(line.get("y1"))
    x2 = _finite_coord(line.get("x2"))
    y2 = _finite_coord(line.get("y2"))
    if None in (x1, y1, x2, y2):
        return 0.0
    return hypot(x2 - x1, y2 - y1)


def _axes_from_mapping(
    mapping: Mapping[str, Any],
    *,
    iso_key: str,
    x_key: str,
    y_key: str,
) -> tuple[float, float]:
    if not isinstance(mapping, Mapping):
        return 0.0, 0.0
    iso = positive_float(mapping.get(iso_key), 0.0)
    x = positive_float(mapping.get(x_key), 0.0)
    y = positive_float(mapping.get(y_key), 0.0)
    x_present = x_key in mapping
    y_present = y_key in mapping
    if x_present or y_present:
        x_out = x if x_present else (iso if iso > 0 else 0.0)
        y_out = y if y_present else 0.0
        return x_out, y_out
    if iso > 0:
        return iso, iso
    return 0.0, 0.0


def _calibration_result(um_per_px_x: float, um_per_px_y: float, source: str) -> dict[str, Any]:
    x = float(um_per_px_x) if um_per_px_x > 0 else 0.0
    y = float(um_per_px_y) if um_per_px_y > 0 else 0.0
    calibrated = source != "assumed" and (x > 0 or y > 0)
    if not calibrated:
        return {
            "um_per_px": 0.0,
            "um_per_px_x": 0.0,
            "um_per_px_y": 0.0,
            "calibration_source": "assumed",
            "assumed": True,
            "calibrated": False,
        }
    return {
        "um_per_px": x,
        "um_per_px_x": x,
        "um_per_px_y": y,
        "calibration_source": source,
        "assumed": False,
        "calibrated": True,
    }


def _assumed_calibration() -> dict[str, Any]:
    return _calibration_result(0.0, 0.0, "assumed")


def resolve_calibration(
    metadata: Mapping[str, Any] | None = None,
    saved_scale: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    meta = dict(metadata) if isinstance(metadata, Mapping) else {}
    saved = dict(saved_scale) if isinstance(saved_scale, Mapping) else {}
    requested_source = str(saved.get("calibration_source") or "")
    line = saved.get("line_px")
    line_px = line_length_px(line if isinstance(line, Mapping) else None)
    length_um = positive_float(saved.get("length_um"), 0.0)
    saved_x, saved_y = _axes_from_mapping(
        saved, iso_key="um_per_px", x_key="um_per_px_x", y_key="um_per_px_y"
    )
    meta_x, meta_y = _axes_from_mapping(
        meta, iso_key="pixel_size_um", x_key="pixel_size_x_um", y_key="pixel_size_y_um"
    )

    if requested_source == "manual_line":
        if line_px > 0 and length_um > 0:
            iso = float(length_um / line_px)
            return _calibration_result(iso, iso, "manual_line")
        return _assumed_calibration()

    if requested_source == "manual":
        if saved_x > 0 or saved_y > 0:
            return _calibration_result(saved_x, saved_y, "manual")
        return _assumed_calibration()

    if requested_source == "embedded":
        if meta_x > 0 or meta_y > 0:
            return _calibration_result(meta_x, meta_y, "embedded")
        return _assumed_calibration()

    if requested_source == "assumed":
        if meta_x > 0 or meta_y > 0:
            return _calibration_result(meta_x, meta_y, "embedded")
        return _assumed_calibration()

    if saved_x > 0 or saved_y > 0:
        return _calibration_result(saved_x, saved_y, "manual")
    if line_px > 0 and length_um > 0:
        iso = float(length_um / line_px)
        return _calibration_result(iso, iso, "manual_line")
    if meta_x > 0 or meta_y > 0:
        return _calibration_result(meta_x, meta_y, "embedded")
    return _assumed_calibration()


def _float_line(line: Mapping[str, Any] | None) -> dict[str, float]:
    if not isinstance(line, Mapping) or not line:
        return {}
    out: dict[str, float] = {}
    for key in ("x1", "y1", "x2", "y2"):
        try:
            val = float(line.get(key, 0.0))
        except (TypeError, ValueError):
            val = 0.0
        out[key] = val if isfinite(val) else 0.0
    return out


def _line_center(line: Mapping[str, Any] | None) -> tuple[float, float] | None:
    coords = _float_line(line)
    if not coords:
        return None
    if coords["x1"] == coords["x2"] and coords["y1"] == coords["y2"]:
        return None
    return ((coords["x1"] + coords["x2"]) / 2.0, (coords["y1"] + coords["y2"]) / 2.0)


def _horizontal_display_line(
    width_px: int,
    height_px: int,
    length_px: float,
    center: tuple[float, float] | None = None,
) -> dict[str, float]:
    length = float(length_px)
    if not isfinite(length) or length <= 0:
        return {}
    width = max(1, int(width_px))
    height = max(1, int(height_px))
    if center is not None:
        cx, cy = float(center[0]), float(center[1])
        if not isfinite(cx):
            cx = 0.0
        if not isfinite(cy):
            cy = 0.0
        return {
            "x1": cx - length / 2.0,
            "y1": cy,
            "x2": cx + length / 2.0,
            "y2": cy,
        }
    margin_x = max(8.0, width * 0.06)
    margin_y = max(8.0, height * 0.08)
    y = max(0.0, height - margin_y)
    x2 = max(1.0, width - margin_x)
    x1 = x2 - length
    if x1 < 0.0:
        x1 = 0.0
        x2 = length
    return {"x1": float(x1), "y1": float(y), "x2": float(x2), "y2": float(y)}


def resolve_measurement_scale(
    image_record: Mapping[str, Any],
    saved_scale: Mapping[str, Any] | None = None,
    *,
    default_length_um: float = DEFAULT_SCALE_BAR_LENGTH_UM,
) -> dict[str, Any]:
    metadata = dict(image_record.get("metadata", {})) if isinstance(image_record, Mapping) else {}
    width_px = int(positive_float(metadata.get("width_px") or metadata.get("width"), 1.0))
    height_px = int(positive_float(metadata.get("height_px") or metadata.get("height"), 1.0))
    saved = dict(saved_scale) if isinstance(saved_scale, Mapping) else {}
    length_um = positive_float(saved.get("length_um"), default_length_um)
    saved_for_cal = dict(saved)
    saved_for_cal["length_um"] = length_um
    cal = resolve_calibration(metadata, saved_for_cal)
    saved_line = dict(saved.get("line_px", {})) if isinstance(saved.get("line_px"), Mapping) else {}
    source = str(cal["calibration_source"])
    x_scale = float(cal["um_per_px_x"]) if cal["um_per_px_x"] > 0 else 0.0
    if source == "assumed" or not cal["calibrated"]:
        display_line: dict[str, float] = {}
        line_out = _float_line(saved_line)
    elif source == "manual_line":
        line_out = _float_line(saved_line)
        display_line = dict(line_out)
    elif x_scale > 0:
        length_px = float(length_um / x_scale)
        center = _line_center(saved.get("display_line_px") if isinstance(saved.get("display_line_px"), Mapping) else None)
        if center is None:
            center = _line_center(saved_line)
        display_line = _horizontal_display_line(width_px, height_px, length_px, center)
        line_out = _float_line(saved_line) if saved_line else dict(display_line)
    else:
        display_line = {}
        line_out = _float_line(saved_line)
    result = deepcopy(saved)
    result.update(
        {
            "enabled": bool(saved.get("enabled", True)),
            "length_um": float(length_um),
            "um_per_px": float(cal["um_per_px"]),
            "um_per_px_x": float(cal["um_per_px_x"]),
            "um_per_px_y": float(cal["um_per_px_y"]),
            "line_px": line_out,
            "display_line_px": display_line,
            "line_length_px": float(line_length_px(line_out)) if line_out else 0.0,
            "calibration_source": cal["calibration_source"],
            "assumed": bool(cal["assumed"]),
            "calibrated": bool(cal["calibrated"]),
        }
    )
    return result


def merge_measurement_scale_updates(
    current: Mapping[str, Any] | None,
    updates: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = deepcopy(dict(current)) if isinstance(current, Mapping) else {}
    incoming = deepcopy(dict(updates)) if isinstance(updates, Mapping) else {}
    has_scalar = "um_per_px" in incoming
    has_axes = "um_per_px_x" in incoming or "um_per_px_y" in incoming
    manual_line_intent = str(incoming.get("calibration_source") or "") == "manual_line"
    merged.update(incoming)
    if has_scalar and not has_axes and not manual_line_intent:
        scalar = positive_float(incoming.get("um_per_px"), 0.0)
        merged.pop("um_per_px_x", None)
        merged.pop("um_per_px_y", None)
        if scalar > 0:
            merged["um_per_px"] = float(scalar)
            merged["calibration_source"] = "manual"
        else:
            merged["um_per_px"] = 0.0
            merged["calibration_source"] = "embedded"
            merged["line_px"] = {}
            merged["display_line_px"] = {}
            merged["line_length_px"] = 0.0
    return merged


def _physical_axes(scale: Mapping[str, Any] | None) -> tuple[float, float]:
    if not isinstance(scale, Mapping):
        return 0.0, 0.0
    if bool(scale.get("assumed")) or str(scale.get("calibration_source") or "") == "assumed":
        return 0.0, 0.0
    if "calibrated" in scale and not scale.get("calibrated"):
        return 0.0, 0.0
    return _axes_from_mapping(scale, iso_key="um_per_px", x_key="um_per_px_x", y_key="um_per_px_y")


def apply_area_scale(area_px: float, scale: Mapping[str, Any] | None) -> float:
    x, y = _physical_axes(scale)
    if x > 0 and y > 0:
        return float(area_px) * x * y
    return 0.0


def apply_length_scale(dx_px: float, dy_px: float, scale: Mapping[str, Any] | None) -> float:
    x, y = _physical_axes(scale)
    dx = float(dx_px)
    dy = float(dy_px)
    if dx != 0.0 and x <= 0:
        return 0.0
    if dy != 0.0 and y <= 0:
        return 0.0
    return hypot(dx * x, dy * y)


def displayed_um_per_px_matches_auto(
    displayed_um_per_px: float,
    auto_um_per_px: float,
    *,
    decimals: int = UM_PER_PX_SPIN_DECIMALS,
) -> bool:
    return round(float(displayed_um_per_px), decimals) == round(float(auto_um_per_px), decimals)


def measurement_scale_apply_updates(
    saved_scale: Mapping[str, Any] | None,
    *,
    length_um: float,
    displayed_um_per_px: float,
    decimals: int = UM_PER_PX_SPIN_DECIMALS,
) -> dict[str, Any]:
    saved = dict(saved_scale or {})
    updates: dict[str, Any] = {"length_um": float(length_um)}
    auto_um_per_px = positive_float(saved.get("um_per_px"), 0.0)
    typed = positive_float(displayed_um_per_px, 0.0)
    if (
        str(saved.get("calibration_source") or "") == "manual_line"
        and auto_um_per_px > 0
        and displayed_um_per_px_matches_auto(displayed_um_per_px, auto_um_per_px, decimals=decimals)
    ):
        updates["calibration_source"] = "manual_line"
        return updates
    if typed > 0:
        updates["calibration_source"] = "manual"
        updates["um_per_px"] = float(typed)
        return updates
    updates["calibration_source"] = "embedded"
    updates["um_per_px"] = 0.0
    return updates
