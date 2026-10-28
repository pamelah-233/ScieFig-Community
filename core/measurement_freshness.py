"""Compare saved analysis inputs to current widgets. Not numeric QC."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from core.utils.hashing import sha256_json


STATUS_I18N_KEYS = {
    "not_calculated": "result_status_not_calculated",
    "calculating": "result_status_calculating",
    "current": "result_status_current",
    "stale": "result_status_stale",
    "failed": "result_status_failed",
    "unknown": "result_status_unknown",
}

_CALIBRATION_KEYS = ("um_per_px", "um_per_px_x", "um_per_px_y", "assumed", "calibrated", "calibration_source")
_ROI_SCALAR_KEYS = ("mode", "type", "roi_uuid", "active_item_uuid", "x", "y", "w", "h")
_TOP_COSMETIC = ("overlay_opacity", "style", "display_name")


def _is_raw_array(value: Any) -> bool:
    return hasattr(value, "shape") and hasattr(value, "dtype") and not isinstance(value, (bytes, str, dict))


def _drop_raw_arrays(value: Any) -> Any:
    if _is_raw_array(value):
        return None
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if _is_raw_array(item):
                continue
            nested = _drop_raw_arrays(item)
            cleaned[str(key)] = nested
        return cleaned
    if isinstance(value, (list, tuple)):
        if value and _is_raw_array(value[0]):
            return None
        return [_drop_raw_arrays(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def snapshot_calibration(calibration: Any) -> dict[str, Any] | None:
    if not isinstance(calibration, dict):
        return None
    snapshot = {}
    for key in _CALIBRATION_KEYS:
        if key in calibration and not _is_raw_array(calibration[key]):
            snapshot[key] = calibration[key]
    return snapshot or None


def snapshot_roi(roi: Any) -> dict[str, Any] | None:
    if not isinstance(roi, dict):
        return None
    snapshot = {}
    for key in _ROI_SCALAR_KEYS:
        if key in roi and not _is_raw_array(roi[key]):
            snapshot[key] = roi[key]
    items = roi.get("items")
    if isinstance(items, list):
        snapshot["items"] = [item for item in (_roi_item(entry) for entry in items) if item is not None]
    coords = roi.get("coordinates_px") or roi.get("geometry")
    if isinstance(coords, dict):
        snapshot["coordinates_px"] = {str(key): value for key, value in coords.items() if not _is_raw_array(value)}
    return snapshot or None


def _roi_item(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    item = {}
    for key, value in entry.items():
        if _is_raw_array(value) or key == "mask":
            continue
        item[str(key)] = _drop_raw_arrays(value)
    return item or None


def normalize_analysis_inputs(payload: Any) -> Any:
    cleaned = _drop_raw_arrays(payload)
    if not isinstance(cleaned, dict):
        return cleaned
    for key in _TOP_COSMETIC:
        cleaned.pop(key, None)
    if "roi" in cleaned:
        cleaned["roi"] = snapshot_roi(cleaned.get("roi"))
    if "calibration" in cleaned:
        cleaned["calibration"] = snapshot_calibration(cleaned.get("calibration"))
    return cleaned


def build_analysis_inputs(
    *,
    method: str,
    settings: dict[str, Any] | None,
    image_uid: str | None = None,
    roi: dict[str, Any] | None = None,
    calibration: dict[str, Any] | None = None,
    channel_indices: list[int] | None = None,
    channel_index: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "method": str(method),
        "settings": deepcopy(settings) if settings is not None else {},
    }
    if image_uid is not None:
        payload["image_uid"] = str(image_uid)
    if roi is not None:
        payload["roi"] = snapshot_roi(roi)
    if calibration is not None:
        payload["calibration"] = snapshot_calibration(calibration)
    if channel_indices is not None:
        payload["channel_indices"] = [int(index) for index in channel_indices]
    if channel_index is not None:
        payload["channel_index"] = int(channel_index)
    return normalize_analysis_inputs(payload)


def result_freshness(saved_inputs, current_inputs):
    if saved_inputs is None:
        return "unknown"
    return "current" if saved_inputs == current_inputs else "stale"


def saved_inputs_from_record(record):
    if not isinstance(record, dict):
        return None
    captured = record.get("analysis_inputs")
    if captured is None:
        return None
    return normalize_analysis_inputs(deepcopy(captured))


def attach_analysis_inputs(record, inputs):
    if not isinstance(record, dict):
        return
    record["analysis_inputs"] = normalize_analysis_inputs(deepcopy(inputs))


def display_status(*, has_result, saved_inputs, current_inputs, attempt=None):
    if attempt == "running":
        return "calculating"
    if not has_result:
        return "failed" if attempt == "failed" else "not_calculated"
    freshness = result_freshness(saved_inputs, current_inputs)
    if attempt == "failed":
        return ("failed", freshness)
    return freshness


def analysis_context_payload(panel) -> dict[str, Any]:
    payload = deepcopy(panel.to_dict())
    for key in ("style", "export", "modified_at"):
        payload.pop(key, None)
    data = payload.get("data")
    if isinstance(data, dict):
        data.pop("analysis_ui_state_by_image", None)
        guided = data.get("guided_state")
        if isinstance(guided, dict):
            guided.pop("overlay_opacity", None)
    return payload


def analysis_job_fingerprint(panel) -> str:
    payload = analysis_context_payload(panel)
    transient = getattr(panel, "_transient_preview_state", None)
    if transient is not None:
        preview = deepcopy(transient)
        if isinstance(preview, dict):
            preview.pop("overlay_opacity", None)
        payload["_transient_preview_state"] = preview
    return sha256_json(payload)


def record_image_uid(record) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("image_uid") or record.get("image_uuid") or "").strip()
