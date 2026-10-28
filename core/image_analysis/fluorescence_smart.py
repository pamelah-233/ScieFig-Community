"""GUI-free fluorescence smart-assisted object detection core."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from core.utils.hashing import sha256_json

FLUORESCENCE_SMART_ANALYSIS_VERSION = "v8_0_smart_fluorescence"
FLUORESCENCE_SMART_MODE = "smart_assisted"
FLUORESCENCE_SMART_FAMILY = "fluorescence"
FLUORESCENCE_SMART_OBJECT_SEMANTICS = "detected_fluorescent_objects"


@dataclass(slots=True, frozen=True)
class FluorescenceSmartSettings:
    background_correction_enabled: bool = True
    background_method: str = "rolling_percentile"
    background_radius_px: int = 12
    detection_sensitivity: float = 1.0
    threshold_method: str = "adaptive"
    min_object_area_px: int = 5
    max_object_area_px: int = 0
    deblend_level: float = 0.0
    noise_rejection_level: float = 1.0
    object_count_semantics: str = FLUORESCENCE_SMART_OBJECT_SEMANTICS
    analysis_version: str = FLUORESCENCE_SMART_ANALYSIS_VERSION


@dataclass(slots=True)
class FluorescenceSmartObject:
    object_id: int
    channel_index: int
    area_px: int
    centroid_y: float
    centroid_x: float
    mean_intensity: float
    integrated_intensity: float
    bbox_y_min: int
    bbox_x_min: int
    bbox_y_max: int
    bbox_x_max: int

    def to_dict(self) -> dict[str, int | float]:
        return {
            "object_id": int(self.object_id),
            "channel_index": int(self.channel_index),
            "area_px": int(self.area_px),
            "centroid_y": float(self.centroid_y),
            "centroid_x": float(self.centroid_x),
            "mean_intensity": float(self.mean_intensity),
            "integrated_intensity": float(self.integrated_intensity),
            "bbox_y_min": int(self.bbox_y_min),
            "bbox_x_min": int(self.bbox_x_min),
            "bbox_y_max": int(self.bbox_y_max),
            "bbox_x_max": int(self.bbox_x_max),
        }


@dataclass(slots=True)
class FluorescenceSmartChannelResult:
    channel_index: int
    objects: list[FluorescenceSmartObject] = field(default_factory=list)
    threshold_value: float = 0.0
    threshold_parameters: dict[str, Any] = field(default_factory=dict)
    label_mask: np.ndarray | None = None
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def object_count(self) -> int:
        return len(self.objects)

    def to_dict(self, *, include_label_mask: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "channel_index": int(self.channel_index),
            "object_count": int(self.object_count),
            "objects": [item.to_dict() for item in self.objects],
            "threshold_value": float(self.threshold_value),
            "threshold_parameters": dict(self.threshold_parameters),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
        if include_label_mask and self.label_mask is not None:
            payload["label_mask"] = np.asarray(self.label_mask, dtype=np.int32)
        return payload


@dataclass(slots=True)
class FluorescenceSmartResult:
    channel_results: dict[int, FluorescenceSmartChannelResult]
    metadata: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return int(sum(channel.object_count for channel in self.channel_results.values()))

    def to_dict(self, *, include_label_masks: bool = False) -> dict[str, Any]:
        return {
            "object_count": int(self.object_count),
            "channel_results": {
                int(index): result.to_dict(include_label_mask=include_label_masks)
                for index, result in self.channel_results.items()
            },
            "metadata": dict(self.metadata),
            "warnings": list(self.warnings),
        }


def analyze_fluorescence_smart_assisted(
    image: np.ndarray,
    *,
    channels: list[int] | None,
    settings: FluorescenceSmartSettings,
    roi_mask: np.ndarray | None = None,
) -> FluorescenceSmartResult:
    """Detect bright fluorescence objects in ``C, Y, X`` arrays.

    A 2D ``Y, X`` image is accepted as a single-channel convenience input and is
    exposed as channel 0. The input image is never modified.
    """
    image_cyx = _as_cyx_array(image)
    channel_indices = _resolve_channels(channels, n_channels=image_cyx.shape[0])
    roi = _resolve_roi_mask(roi_mask, image_shape=image_cyx.shape[1:])
    normalized_settings = _normalize_settings(settings)
    settings_payload = asdict(normalized_settings)
    effective_settings_hash = sha256_json(settings_payload)

    channel_results: dict[int, FluorescenceSmartChannelResult] = {}
    warnings: list[str] = []
    channel_thresholds: dict[int, float] = {}

    for channel_index in channel_indices:
        channel_result = _analyze_channel(
            image_cyx[int(channel_index)],
            channel_index=int(channel_index),
            settings=normalized_settings,
            roi_mask=roi,
            effective_settings_hash=effective_settings_hash,
        )
        channel_results[int(channel_index)] = channel_result
        channel_thresholds[int(channel_index)] = float(channel_result.threshold_value)
        warnings.extend(channel_result.warnings)

    deduped_warnings = _dedupe_preserve_order(warnings)
    metadata = _build_result_metadata(
        settings=normalized_settings,
        roi_used=roi is not None,
        warnings=deduped_warnings,
        effective_settings_hash=effective_settings_hash,
        channel_thresholds=channel_thresholds,
    )
    return FluorescenceSmartResult(
        channel_results=channel_results,
        metadata=metadata,
        warnings=deduped_warnings,
    )


def _analyze_channel(
    channel: np.ndarray,
    *,
    channel_index: int,
    settings: FluorescenceSmartSettings,
    roi_mask: np.ndarray | None,
    effective_settings_hash: str,
) -> FluorescenceSmartChannelResult:
    raw_channel = np.asarray(channel, dtype=np.float64)
    corrected, background_metadata = _background_correct(raw_channel, settings)
    threshold_value, threshold_parameters = _threshold_corrected_channel(
        corrected,
        settings=settings,
        roi_mask=roi_mask,
    )
    candidate_mask = corrected > float(threshold_value)
    if roi_mask is not None:
        candidate_mask &= roi_mask

    effective_min_area_px = _effective_min_object_area_px(settings)
    objects, label_mask = _measure_filtered_objects(
        candidate_mask,
        intensity_image=raw_channel,
        channel_index=channel_index,
        min_area_px=effective_min_area_px,
        max_area_px=max(0, int(settings.max_object_area_px)),
    )
    warnings = _channel_warnings(settings)
    metadata = {
        **background_metadata,
        "threshold_method": settings.threshold_method,
        "threshold_value": float(threshold_value),
        "threshold_parameters": dict(threshold_parameters),
        "blob_method": "connected_components",
        "min_object_area_px": int(settings.min_object_area_px),
        "effective_min_object_area_px": int(effective_min_area_px),
        "max_object_area_px": int(settings.max_object_area_px),
        "noise_rejection_level": float(settings.noise_rejection_level),
        "deblending_enabled": False,
        "deblending_method": "none_connected_components",
        "object_count_semantics": settings.object_count_semantics,
        "intensity_units": "raw_image_units",
        "effective_settings_hash": effective_settings_hash,
    }
    return FluorescenceSmartChannelResult(
        channel_index=channel_index,
        objects=objects,
        threshold_value=float(threshold_value),
        threshold_parameters=threshold_parameters,
        label_mask=label_mask,
        warnings=warnings,
        metadata=metadata,
    )


def _normalize_settings(settings: FluorescenceSmartSettings) -> FluorescenceSmartSettings:
    if not isinstance(settings, FluorescenceSmartSettings):
        raise TypeError("settings must be a FluorescenceSmartSettings instance.")

    background_method = str(settings.background_method or "rolling_percentile").strip().lower()
    threshold_method = str(settings.threshold_method or "adaptive").strip().lower()
    object_count_semantics = str(settings.object_count_semantics or FLUORESCENCE_SMART_OBJECT_SEMANTICS)
    analysis_version = str(settings.analysis_version or FLUORESCENCE_SMART_ANALYSIS_VERSION)
    return FluorescenceSmartSettings(
        background_correction_enabled=bool(settings.background_correction_enabled),
        background_method=background_method,
        background_radius_px=max(1, int(round(float(settings.background_radius_px)))),
        detection_sensitivity=max(0.0, float(settings.detection_sensitivity)),
        threshold_method=threshold_method,
        min_object_area_px=max(0, int(round(float(settings.min_object_area_px)))),
        max_object_area_px=max(0, int(round(float(settings.max_object_area_px)))),
        deblend_level=max(0.0, float(settings.deblend_level)),
        noise_rejection_level=max(0.0, float(settings.noise_rejection_level)),
        object_count_semantics=object_count_semantics,
        analysis_version=analysis_version,
    )


def _effective_min_object_area_px(settings: FluorescenceSmartSettings) -> int:
    min_area_px = max(0, int(settings.min_object_area_px))
    if min_area_px <= 0:
        return 0
    noise_rejection_level = max(1.0, float(settings.noise_rejection_level))
    return max(1, int(np.ceil(float(min_area_px) * noise_rejection_level)))


def _as_cyx_array(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image, dtype=np.float64)
    if array.ndim == 2:
        return array[np.newaxis, :, :]
    if array.ndim != 3:
        raise ValueError("image must have shape (C, Y, X) or (Y, X).")
    if array.shape[1] <= 0 or array.shape[2] <= 0:
        raise ValueError("image spatial dimensions must be non-empty.")
    return array


def _resolve_channels(channels: list[int] | None, *, n_channels: int) -> list[int]:
    if n_channels <= 0:
        raise ValueError("image must contain at least one channel.")
    if channels is None:
        return list(range(int(n_channels)))

    resolved: list[int] = []
    for channel in channels:
        index = int(channel)
        if index < 0 or index >= int(n_channels):
            raise ValueError(f"channel index {index} is out of range for {n_channels} channels.")
        if index not in resolved:
            resolved.append(index)
    if not resolved:
        raise ValueError("channels must contain at least one channel index.")
    return resolved


def _resolve_roi_mask(
    roi_mask: np.ndarray | None,
    *,
    image_shape: tuple[int, int],
) -> np.ndarray | None:
    if roi_mask is None:
        return None
    mask = np.asarray(roi_mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError("roi_mask must be a 2D boolean mask.")
    if mask.shape != image_shape:
        raise ValueError(f"roi_mask shape must be {image_shape}, got {mask.shape}.")
    return mask


def _background_correct(
    channel: np.ndarray,
    settings: FluorescenceSmartSettings,
) -> tuple[np.ndarray, dict[str, Any]]:
    from scipy import ndimage
    if not settings.background_correction_enabled or settings.background_method in {"none", "disabled"}:
        return channel.astype(np.float64, copy=True), {
            "background_method": "none",
            "background_radius_px": 0,
            "background_correction_enabled": False,
        }

    if settings.background_method not in {"rolling_percentile", "percentile"}:
        raise ValueError(f"Unsupported background_method: {settings.background_method}")

    radius_px = max(1, int(settings.background_radius_px))
    window_px = (radius_px * 2) + 1
    background = ndimage.percentile_filter(
        channel,
        percentile=20,
        size=window_px,
        mode="nearest",
    )
    corrected = channel - background
    corrected = np.maximum(corrected, 0.0)
    return corrected, {
        "background_method": "rolling_percentile",
        "background_radius_px": int(radius_px),
        "background_correction_enabled": True,
    }


def _threshold_corrected_channel(
    corrected: np.ndarray,
    *,
    settings: FluorescenceSmartSettings,
    roi_mask: np.ndarray | None,
) -> tuple[float, dict[str, Any]]:
    from skimage.filters import threshold_otsu
    selected_values = corrected[roi_mask] if roi_mask is not None else corrected.reshape(-1)
    if selected_values.size == 0:
        return 0.0, {"method": settings.threshold_method, "empty_selection": True}

    finite_values = selected_values[np.isfinite(selected_values)]
    if finite_values.size == 0:
        return 0.0, {"method": settings.threshold_method, "empty_selection": True}

    max_value = float(np.max(finite_values))
    min_value = float(np.min(finite_values))
    if max_value <= min_value:
        return max_value, {
            "method": settings.threshold_method,
            "min": min_value,
            "max": max_value,
            "constant_image": True,
        }

    if settings.threshold_method in {"otsu", "global_otsu"}:
        threshold_value = float(threshold_otsu(finite_values))
        return threshold_value, {
            "method": "otsu",
            "min": min_value,
            "max": max_value,
        }

    if settings.threshold_method not in {"adaptive", "adaptive_robust", "robust"}:
        raise ValueError(f"Unsupported threshold_method: {settings.threshold_method}")

    median = float(np.median(finite_values))
    mad = float(np.median(np.abs(finite_values - median)))
    robust_sigma = float(1.4826 * mad)
    std_sigma = float(np.std(finite_values))
    sigma = robust_sigma if robust_sigma > 0.0 else std_sigma
    sensitivity = max(0.0, float(settings.detection_sensitivity))
    sigma_multiplier = max(0.75, 3.5 - sensitivity)
    if sigma <= 0.0:
        threshold_value = median + (0.25 * (max_value - median))
    else:
        threshold_value = median + (sigma_multiplier * sigma)
    threshold_value = max(float(threshold_value), min_value)
    return threshold_value, {
        "method": "adaptive_robust",
        "median": median,
        "mad": mad,
        "sigma": sigma,
        "sigma_multiplier": float(sigma_multiplier),
        "min": min_value,
        "max": max_value,
    }


def _measure_filtered_objects(
    candidate_mask: np.ndarray,
    *,
    intensity_image: np.ndarray,
    channel_index: int,
    min_area_px: int,
    max_area_px: int,
) -> tuple[list[FluorescenceSmartObject], np.ndarray]:
    from skimage.measure import label
    from skimage.measure import regionprops
    labeled = label(np.asarray(candidate_mask, dtype=bool), connectivity=2)
    filtered_label_mask = np.zeros(labeled.shape, dtype=np.int32)
    objects: list[FluorescenceSmartObject] = []
    next_label = 1

    for region in regionprops(labeled, intensity_image=intensity_image):
        area_px = int(region.area)
        if min_area_px > 0 and area_px < min_area_px:
            continue
        if max_area_px > 0 and area_px > max_area_px:
            continue

        mask = labeled == int(region.label)
        filtered_label_mask[mask] = next_label
        centroid_y, centroid_x = _region_weighted_centroid(region)
        if not np.isfinite(centroid_y) or not np.isfinite(centroid_x):
            centroid_y, centroid_x = region.centroid
        y_min, x_min, y_max, x_max = region.bbox
        mean_intensity = _region_mean_intensity(region)
        objects.append(
            FluorescenceSmartObject(
                object_id=next_label,
                channel_index=channel_index,
                area_px=area_px,
                centroid_y=float(centroid_y),
                centroid_x=float(centroid_x),
                mean_intensity=float(mean_intensity),
                integrated_intensity=float(mean_intensity * area_px),
                bbox_y_min=int(y_min),
                bbox_x_min=int(x_min),
                bbox_y_max=int(y_max),
                bbox_x_max=int(x_max),
            )
        )
        next_label += 1

    return objects, filtered_label_mask


def _build_result_metadata(
    *,
    settings: FluorescenceSmartSettings,
    roi_used: bool,
    warnings: list[str],
    effective_settings_hash: str,
    channel_thresholds: dict[int, float],
) -> dict[str, Any]:
    return {
        "analysis_mode": FLUORESCENCE_SMART_MODE,
        "analysis_family": FLUORESCENCE_SMART_FAMILY,
        "analysis_version": settings.analysis_version,
        "background_method": settings.background_method,
        "background_radius_px": int(settings.background_radius_px),
        "threshold_method": settings.threshold_method,
        "threshold_parameters": {"per_channel_threshold_value": dict(channel_thresholds)},
        "blob_method": "connected_components",
        "min_object_area_px": int(settings.min_object_area_px),
        "effective_min_object_area_px": int(_effective_min_object_area_px(settings)),
        "max_object_area_px": int(settings.max_object_area_px),
        "noise_rejection_level": float(settings.noise_rejection_level),
        "deblending_enabled": False,
        "deblending_method": "none_connected_components",
        "object_count_semantics": settings.object_count_semantics,
        "intensity_units": "raw_image_units",
        "roi_used": bool(roi_used),
        "warnings": list(warnings),
        "effective_settings_hash": effective_settings_hash,
    }


def _channel_warnings(settings: FluorescenceSmartSettings) -> list[str]:
    warnings: list[str] = []
    if settings.deblend_level > 0.0:
        warnings.append("Deblending is not applied in V8.0-A; connected components are reported conservatively.")
    return warnings


def _region_weighted_centroid(region: Any) -> tuple[float, float]:
    try:
        return tuple(region.centroid_weighted)
    except AttributeError:  # pragma: no cover - compatibility with older scikit-image
        return tuple(region.weighted_centroid)


def _region_mean_intensity(region: Any) -> float:
    try:
        return float(region.intensity_mean)
    except AttributeError:  # pragma: no cover - compatibility with older scikit-image
        return float(region.mean_intensity)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
