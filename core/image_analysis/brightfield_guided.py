from __future__ import annotations

from typing import Any

from core.utils.hashing import sha256_json

BRIGHTFIELD_GUIDED_MAPPING_VERSION = "v7.2-b2a"
BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION = "v7.2-b2b-foolproof-sensitivity-1"
BRIGHTFIELD_ASSISTED_SENSITIVITY_MAPPING_VERSION = "v7.2-b2c-assisted-sensitivity-1"
BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION = "v7.2-b2c2-assisted-sensitivity-min-area-1"
BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION = "v7.2-b2c2-assisted-min-area-filtering-1"
BRIGHTFIELD_GUIDED_PREVIEW_STATUS = "not_previewed_b2b"
BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS = "preview_hash_matched_b2b"
BRIGHTFIELD_GUIDED_PREVIEW_STALE_STATUS = "preview_hash_mismatch_b2b"
BRIGHTFIELD_BATCH_THRESHOLD_PARAMETER = "a_min"
BRIGHTFIELD_PREVIEW_DEBOUNCE_MS = 300
BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MIN = -2
BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MAX = 2
BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_STEP_RATIO = 0.05
BRIGHTFIELD_FOOLPROOF_THRESHOLD_BIAS_MIN = BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MIN
BRIGHTFIELD_FOOLPROOF_THRESHOLD_BIAS_MAX = BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MAX
BRIGHTFIELD_FOOLPROOF_THRESHOLD_BIAS_STEP_RATIO = BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_STEP_RATIO
BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_BIAS_MIN = -2
BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_BIAS_MAX = 2
BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_STEP_RATIO = 0.25


def neutral_brightfield_guided_slider_values() -> dict[str, float]:
    return {
        "threshold_bias": 0.0,
        "cleanup_bias": 0.0,
        "tissue_mask_bias": 0.0,
    }


def normalize_brightfield_guided_threshold_bias(value: Any) -> int:
    if isinstance(value, str):
        normalized = value.strip().lower()
        legacy_map = {
            "very_low": -2,
            "low": -1,
            "medium": 0,
            "neutral": 0,
            "high": 1,
            "very_high": 2,
        }
        if normalized in legacy_map:
            return legacy_map[normalized]
    try:
        bias = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MIN, min(BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_MAX, bias))


def normalize_brightfield_foolproof_threshold_bias(value: Any) -> int:
    return normalize_brightfield_guided_threshold_bias(value)


def normalize_brightfield_assisted_threshold_bias(value: Any) -> int:
    return normalize_brightfield_guided_threshold_bias(value)


def normalize_brightfield_assisted_small_region_filter_bias(value: Any) -> int:
    try:
        bias = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_BIAS_MIN,
        min(BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_BIAS_MAX, bias),
    )


def apply_brightfield_guided_threshold_bias(
    baseline_a_min: Any,
    threshold_bias: Any,
) -> dict[str, float | int]:
    normalized_bias = normalize_brightfield_guided_threshold_bias(threshold_bias)
    baseline_value = float(baseline_a_min)
    proportional_delta = abs(baseline_value) * BRIGHTFIELD_GUIDED_THRESHOLD_BIAS_STEP_RATIO * float(normalized_bias)
    adjusted_a_min = max(-128.0, min(127.0, baseline_value - proportional_delta))
    return {
        "threshold_bias": normalized_bias,
        "baseline_a_min": baseline_value,
        "delta_a_min": float(adjusted_a_min - baseline_value),
        "adjusted_a_min": float(adjusted_a_min),
    }


def apply_brightfield_foolproof_threshold_bias(
    baseline_a_min: Any,
    threshold_bias: Any,
) -> dict[str, float | int]:
    return apply_brightfield_guided_threshold_bias(baseline_a_min, threshold_bias)


def apply_brightfield_assisted_threshold_bias(
    baseline_a_min: Any,
    threshold_bias: Any,
) -> dict[str, float | int]:
    return apply_brightfield_guided_threshold_bias(baseline_a_min, threshold_bias)


def apply_brightfield_assisted_small_region_filter_bias(
    baseline_min_area_px: Any,
    small_region_filter_bias: Any,
) -> dict[str, float | int]:
    normalized_bias = normalize_brightfield_assisted_small_region_filter_bias(small_region_filter_bias)
    baseline_value = max(0, int(round(float(baseline_min_area_px))))
    min_area_scale_factor = 1.0 + (BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_STEP_RATIO * float(normalized_bias))
    if baseline_value <= 0:
        effective_min_area_px = 0
    else:
        effective_min_area_px = max(1, int(round(float(baseline_value) * min_area_scale_factor)))
    return {
        "small_region_filter_bias": normalized_bias,
        "baseline_min_area_px": baseline_value,
        "min_area_scale_factor": float(min_area_scale_factor),
        "effective_min_area_px": int(effective_min_area_px),
    }


def build_brightfield_guided_threshold_slider_values(
    *,
    threshold_bias: Any,
    active: bool,
    inactive_reason: Any = None,
) -> dict[str, Any]:
    return {
        "threshold_bias": normalize_brightfield_guided_threshold_bias(threshold_bias),
        "cleanup_bias": 0.0,
        "tissue_mask_bias": 0.0,
        "threshold_bias_active": bool(active),
        "threshold_bias_inactive_reason": _normalize_optional_str(inactive_reason),
    }


def build_brightfield_foolproof_slider_values(
    *,
    threshold_bias: Any,
    active: bool,
    inactive_reason: Any = None,
) -> dict[str, Any]:
    return build_brightfield_guided_threshold_slider_values(
        threshold_bias=threshold_bias,
        active=active,
        inactive_reason=inactive_reason,
    )


def build_brightfield_assisted_slider_values(
    *,
    threshold_bias: Any,
    active: bool,
    inactive_reason: Any = None,
) -> dict[str, Any]:
    slider_values = build_brightfield_guided_threshold_slider_values(
        threshold_bias=threshold_bias,
        active=active,
        inactive_reason=inactive_reason,
    )
    return slider_values


def build_brightfield_assisted_postprocess_slider_values(
    *,
    threshold_bias: Any,
    active: bool,
    inactive_reason: Any = None,
    small_region_filter_bias: Any = 0,
    baseline_min_area_px: Any = 0,
    effective_min_area_px: Any = 0,
) -> dict[str, Any]:
    slider_values = build_brightfield_assisted_slider_values(
        threshold_bias=threshold_bias,
        active=active,
        inactive_reason=inactive_reason,
    )
    slider_values.update(
        {
            "small_region_filter_bias": normalize_brightfield_assisted_small_region_filter_bias(
                small_region_filter_bias
            ),
            "small_region_filter_mapping_version": BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
            "small_region_filter_baseline_min_area_px": max(0, int(round(float(baseline_min_area_px)))),
            "small_region_filter_effective_min_area_px": max(0, int(round(float(effective_min_area_px)))),
        }
    )
    return slider_values


def build_brightfield_guided_effective_settings_snapshot(
    *,
    guided_mode: Any,
    task_profile: dict[str, Any] | None,
    stain_type: Any,
    threshold_source: Any,
    threshold_method: Any = None,
    image_context: dict[str, Any] | None = None,
    effective_settings: dict[str, Any] | None = None,
    slider_values: dict[str, Any] | None = None,
    roi_snapshot: dict[str, Any] | None = None,
    analysis_metadata: dict[str, Any] | None = None,
    batch_state: dict[str, Any] | None = None,
    mapping_version: str = BRIGHTFIELD_GUIDED_MAPPING_VERSION,
) -> dict[str, Any]:
    profile_payload = dict(task_profile or {})
    metadata = dict(analysis_metadata or {})
    roi_context = summarize_brightfield_roi_context(roi_snapshot)
    normalized_threshold_source = _normalize_optional_str(threshold_source)
    normalized_effective_settings = _canonicalize_json_value(dict(effective_settings or {}))
    normalized_slider_values = _canonicalize_json_value(
        dict(slider_values or neutral_brightfield_guided_slider_values())
    )
    normalized_image_context = summarize_brightfield_image_context(image_context, metadata)
    tissue_mask = summarize_brightfield_tissue_mask_context(normalized_effective_settings, metadata)
    artifact_handling = summarize_brightfield_artifact_context(metadata)
    analysis_region = summarize_brightfield_analysis_region(roi_context, tissue_mask, artifact_handling)
    batch_lock = summarize_brightfield_batch_lock_context(
        batch_state=batch_state,
        analysis_metadata=metadata,
        threshold_source=normalized_threshold_source,
    )

    task_id = (
        _normalize_optional_str(profile_payload.get("task_id"))
        or _normalize_optional_str(metadata.get("task_id"))
        or ""
    )
    profile_id = (
        _normalize_optional_str(metadata.get("profile_id"))
        or _normalize_optional_str(profile_payload.get("task_id"))
        or task_id
    )

    return {
        "mapping_version": _normalize_optional_str(mapping_version) or BRIGHTFIELD_GUIDED_MAPPING_VERSION,
        "guided_mode": _normalize_optional_str(guided_mode) or "",
        "task_id": task_id,
        "profile_id": profile_id,
        "stain_type": _normalize_optional_str(stain_type) or "",
        "image_context": normalized_image_context,
        "threshold_source": normalized_threshold_source,
        "threshold_method": _normalize_optional_str(threshold_method),
        "effective_settings": normalized_effective_settings,
        "guided_slider_values": normalized_slider_values,
        "roi_context": roi_context,
        "analysis_region": analysis_region,
        "artifact_handling": artifact_handling,
        "tissue_mask": tissue_mask,
        "batch_lock": batch_lock,
    }


def package_brightfield_guided_measurement_metadata(
    *,
    guided_mode: Any,
    task_profile: dict[str, Any] | None,
    stain_type: Any,
    threshold_source: Any,
    threshold_method: Any = None,
    image_context: dict[str, Any] | None = None,
    effective_settings: dict[str, Any] | None = None,
    slider_values: dict[str, Any] | None = None,
    roi_snapshot: dict[str, Any] | None = None,
    analysis_metadata: dict[str, Any] | None = None,
    batch_state: dict[str, Any] | None = None,
    baseline_source: Any = None,
    preview_status: str = BRIGHTFIELD_GUIDED_PREVIEW_STATUS,
    mapping_version: str = BRIGHTFIELD_GUIDED_MAPPING_VERSION,
) -> dict[str, Any]:
    snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode=guided_mode,
        task_profile=task_profile,
        stain_type=stain_type,
        image_context=image_context,
        threshold_source=threshold_source,
        threshold_method=threshold_method,
        effective_settings=effective_settings,
        slider_values=slider_values,
        roi_snapshot=roi_snapshot,
        analysis_metadata=analysis_metadata,
        batch_state=batch_state,
        mapping_version=mapping_version,
    )
    return {
        "guided_effective_settings": snapshot,
        "guided_effective_settings_hash": sha256_json(snapshot),
        "guided_mapping_version": snapshot["mapping_version"],
        "guided_slider_values": dict(snapshot["guided_slider_values"]),
        "guided_baseline_source": _normalize_optional_str(baseline_source) or _normalize_optional_str(threshold_source),
        "guided_preview_status": _normalize_optional_str(preview_status) or BRIGHTFIELD_GUIDED_PREVIEW_STATUS,
    }


def summarize_brightfield_image_context(
    image_context: dict[str, Any] | None,
    analysis_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    context = dict(image_context or {})
    metadata = dict(analysis_metadata or {})
    return {
        "image_uid": _normalize_optional_str(context.get("image_uid")) or _normalize_optional_str(metadata.get("image_uid")),
        "image_context_id": _normalize_optional_str(context.get("image_context_id"))
        or _normalize_optional_str(metadata.get("image_context_id")),
        "source_name": _normalize_optional_str(context.get("source_name")) or _normalize_optional_str(metadata.get("source_name")),
        "source_digest": _normalize_optional_str(context.get("source_digest"))
        or _normalize_optional_str(metadata.get("source_digest")),
    }


def summarize_brightfield_roi_context(roi_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(roi_snapshot or {})
    mode = _normalize_optional_str(snapshot.get("mode")) or "whole_image"
    items = list(snapshot.get("items", []))
    active_item_uuid = _normalize_optional_str(snapshot.get("active_item_uuid"))
    active_item: dict[str, Any] | None = None
    for item in items:
        if _normalize_optional_str(dict(item).get("roi_uuid")) == active_item_uuid:
            active_item = dict(item)
            break
    if active_item is None and items:
        active_item = dict(items[-1])
    coordinates = dict(active_item.get("coordinates_px", {})) if active_item else {}
    active_geometry = {
        "roi_uuid": _normalize_optional_str(active_item.get("roi_uuid")) if active_item else None,
        "type": _normalize_optional_str(active_item.get("type")) if active_item else None,
        "x": _coerce_int_or_none(coordinates.get("x")),
        "y": _coerce_int_or_none(coordinates.get("y")),
        "w": _coerce_int_or_none(coordinates.get("w")),
        "h": _coerce_int_or_none(coordinates.get("h")),
    }
    return {
        "mode": mode,
        "item_count": len(items),
        "has_active_selection": active_item is not None,
        "active_geometry": active_geometry,
    }


def summarize_brightfield_analysis_region(
    roi_context: dict[str, Any],
    tissue_mask: dict[str, Any],
    artifact_handling: dict[str, Any],
) -> dict[str, Any]:
    roi_mode = _normalize_optional_str(dict(roi_context).get("mode")) or "whole_image"
    return {
        "region_kind": "whole_image" if roi_mode == "whole_image" else "selected_roi",
        "roi_limited": roi_mode != "whole_image",
        "artifact_exclusion_enabled": bool(dict(artifact_handling).get("artifact_exclusion_enabled", True)),
        "tissue_mask_enabled": bool(dict(tissue_mask).get("enabled", False)),
    }


def summarize_brightfield_artifact_context(analysis_metadata: dict[str, Any] | None) -> dict[str, Any]:
    metadata = dict(analysis_metadata or {})
    artifact_summary = dict(metadata.get("artifact_summary", {}))
    artifact_types = list(artifact_summary.get("artifact_types_detected", []))
    return {
        "artifact_exclusion_enabled": True,
        "area_fraction_denominator": _normalize_optional_str(metadata.get("area_fraction_denominator"))
        or "selected_area_minus_artifacts",
        "artifact_fraction": _coerce_float_or_none(artifact_summary.get("artifact_fraction")),
        "artifact_types_signature": ",".join(sorted(_normalize_optional_str(item) or "" for item in artifact_types if item)),
        "warning_count": len(list(artifact_summary.get("warnings", []))),
    }


def summarize_brightfield_tissue_mask_context(
    effective_settings: dict[str, Any],
    analysis_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = dict(analysis_metadata or {})
    enabled = bool(
        effective_settings.get(
            "tissue_mask_enabled",
            metadata.get("tissue_mask_enabled", metadata.get("tissue_mask_applied", False)),
        )
    )
    return {
        "enabled": enabled,
        "applied": bool(metadata.get("tissue_mask_applied", enabled)),
        "value_threshold": _coerce_float_or_none(effective_settings.get("tissue_mask_value_threshold")),
        "saturation_threshold": _coerce_float_or_none(effective_settings.get("tissue_mask_saturation_threshold")),
        "min_area_px": _coerce_int_or_none(effective_settings.get("tissue_mask_min_area_px")),
    }


def summarize_brightfield_batch_lock_context(
    *,
    batch_state: dict[str, Any] | None,
    analysis_metadata: dict[str, Any] | None,
    threshold_source: str | None,
) -> dict[str, Any]:
    state = dict(batch_state or {})
    metadata = dict(analysis_metadata or {})
    batch_threshold = state.get("batch_threshold")
    source_mode = _normalize_optional_str(state.get("batch_lock_source_mode")) or _normalize_optional_str(
        metadata.get("batch_lock_source_mode")
    )
    source_image_uid = _normalize_optional_str(state.get("batch_threshold_source_image")) or _normalize_optional_str(
        metadata.get("batch_threshold_source_image")
    )
    threshold_method = _normalize_optional_str(state.get("batch_threshold_method")) or _normalize_optional_str(
        metadata.get("batch_threshold_method")
    )
    return {
        "locked": bool(state.get("locked", False)),
        "applied_to_measurement": bool(
            threshold_source == "batch_locked" or metadata.get("batch_lock_applied", False)
        ),
        "task_id": _normalize_optional_str(state.get("task_id")) or _normalize_optional_str(metadata.get("task_id")),
        "threshold_parameter": BRIGHTFIELD_BATCH_THRESHOLD_PARAMETER if batch_threshold is not None else None,
        "threshold_value": _coerce_float_or_none(batch_threshold),
        "threshold_method": threshold_method,
        "source_mode": source_mode,
        "source_image_uid": source_image_uid,
    }


def _canonicalize_json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return float(value)
    if isinstance(value, dict):
        return {
            str(key): _canonicalize_json_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, (list, tuple)):
        return [_canonicalize_json_value(item) for item in value]
    if hasattr(value, "item"):
        return _canonicalize_json_value(value.item())
    raise TypeError(f"Unsupported guided settings payload value: {type(value)!r}")


def _normalize_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _coerce_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
