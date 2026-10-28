"""Minimal pure-function image analysis helpers."""

from __future__ import annotations

from typing import Iterable

import numpy as np


def threshold_channel(
    channel: np.ndarray,
    threshold: float,
    polarity: str = "high_positive",
) -> np.ndarray:
    """Threshold a 2D channel using high- or low-positive polarity."""
    channel_2d = _as_2d_array(channel, name="channel")
    threshold_value = float(threshold)
    if polarity == "high_positive":
        return channel_2d >= threshold_value
    if polarity == "low_positive":
        return channel_2d <= threshold_value
    raise ValueError(f"Unsupported polarity: {polarity}")


def clean_mask(
    mask: np.ndarray,
    min_area_px: int = 0,
    fill_holes: bool = False,
) -> np.ndarray:
    """Remove small objects and optionally fill holes in a boolean mask."""
    cleaned = _as_bool_mask(mask, name="mask")
    if int(min_area_px) > 0:
        from skimage.morphology import remove_small_objects
        cleaned = remove_small_objects(cleaned, max_size=int(min_area_px) - 1)
    if fill_holes:
        from scipy import ndimage
        cleaned = ndimage.binary_fill_holes(cleaned)
    return np.asarray(cleaned, dtype=bool)


def measure_area_fraction(
    mask: np.ndarray,
    roi_mask: np.ndarray | None = None,
) -> dict[str, float | int]:
    """Measure positive area fraction within an optional ROI."""
    positive_mask = _as_bool_mask(mask, name="mask")
    selected = _resolve_selection(positive_mask.shape, roi_mask=roi_mask)

    positive_area_px = int(np.count_nonzero(positive_mask & selected))
    total_area_px = int(np.count_nonzero(selected))
    positive_area_fraction = (positive_area_px / total_area_px) if total_area_px else 0.0

    return {
        "positive_area_px": positive_area_px,
        "total_area_px": total_area_px,
        "positive_area_fraction": float(positive_area_fraction),
        "positive_area_percent": float(positive_area_fraction * 100.0),
    }


def measure_channel_intensity(
    channel: np.ndarray,
    mask: np.ndarray | None = None,
    roi_mask: np.ndarray | None = None,
) -> dict[str, float | int]:
    """Measure mean intensity and integrated density in an optional selection."""
    channel_2d = _as_2d_array(channel, name="channel")
    selected = _resolve_selection(channel_2d.shape, roi_mask=roi_mask, mask=mask)

    values = channel_2d[selected]
    area_px = int(values.size)
    integrated_density = float(np.sum(values, dtype=np.float64)) if area_px else 0.0
    mean_intensity = float(integrated_density / area_px) if area_px else 0.0

    return {
        "area_px": area_px,
        "mean_intensity": mean_intensity,
        "integrated_density": integrated_density,
    }


def compute_hscore(
    channel: np.ndarray,
    roi_mask: np.ndarray,
    weak_threshold: float,
    moderate_threshold: float,
    strong_threshold: float,
) -> dict[str, float | int]:
    """Compute H-score percentages and score within an ROI."""
    if not (
        float(weak_threshold) <= float(moderate_threshold) <= float(strong_threshold)
    ):
        raise ValueError("Thresholds must satisfy weak <= moderate <= strong.")

    channel_2d = _as_2d_array(channel, name="channel")
    selected = _resolve_selection(channel_2d.shape, roi_mask=roi_mask)
    values = channel_2d[selected]
    total_area_px = int(values.size)

    if total_area_px == 0:
        return {
            "total_area_px": 0,
            "negative_percent": 0.0,
            "weak_percent": 0.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 0.0,
            "h_score": 0.0,
        }

    weak_threshold = float(weak_threshold)
    moderate_threshold = float(moderate_threshold)
    strong_threshold = float(strong_threshold)

    negative_count = int(np.count_nonzero(values < weak_threshold))
    weak_count = int(np.count_nonzero((values >= weak_threshold) & (values < moderate_threshold)))
    moderate_count = int(np.count_nonzero((values >= moderate_threshold) & (values < strong_threshold)))
    strong_count = int(np.count_nonzero(values >= strong_threshold))

    negative_percent = 100.0 * negative_count / total_area_px
    weak_percent = 100.0 * weak_count / total_area_px
    moderate_percent = 100.0 * moderate_count / total_area_px
    strong_percent = 100.0 * strong_count / total_area_px
    positive_percent = weak_percent + moderate_percent + strong_percent

    return {
        "total_area_px": total_area_px,
        "negative_percent": float(negative_percent),
        "weak_percent": float(weak_percent),
        "moderate_percent": float(moderate_percent),
        "strong_percent": float(strong_percent),
        "positive_percent": float(positive_percent),
        "h_score": float(weak_percent + 2.0 * moderate_percent + 3.0 * strong_percent),
    }


def render_mask_overlay(
    rgb: np.ndarray,
    mask: np.ndarray,
    color: Iterable[float] = (255, 0, 0),
    alpha: float = 0.35,
) -> np.ndarray:
    """Blend a solid color onto masked pixels only and return uint8 RGB."""
    rgb_array = _as_rgb_array(rgb)
    mask_2d = _as_bool_mask(mask, name="mask")
    if mask_2d.shape != rgb_array.shape[:2]:
        raise ValueError("mask shape must match the first two RGB dimensions.")

    color_array = np.asarray(tuple(color), dtype=np.float32)
    if color_array.shape != (3,):
        raise ValueError("color must contain exactly three RGB components.")

    alpha_value = float(alpha)
    blended = rgb_array.copy()
    blended[mask_2d] = (
        (1.0 - alpha_value) * blended[mask_2d] + alpha_value * color_array
    )
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8)


def _resolve_selection(
    shape: tuple[int, int],
    roi_mask: np.ndarray | None = None,
    mask: np.ndarray | None = None,
) -> np.ndarray:
    selected = np.ones(shape, dtype=bool)
    if roi_mask is not None:
        selected &= _as_bool_mask(roi_mask, name="roi_mask", expected_shape=shape)
    if mask is not None:
        selected &= _as_bool_mask(mask, name="mask", expected_shape=shape)
    return selected


def _as_rgb_array(rgb: np.ndarray, scale: str = "byte") -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if scale not in {"byte", "unit"}:
        raise ValueError(f"Unsupported RGB scale: {scale}")

    if np.max(array) <= 1.0:
        unit_array = np.clip(array, 0.0, 1.0)
    else:
        unit_array = np.clip(array, 0.0, 255.0) / 255.0

    if scale == "unit":
        return unit_array
    return unit_array * 255.0


def _as_2d_array(array: np.ndarray, name: str) -> np.ndarray:
    result = np.asarray(array, dtype=np.float64)
    if result.ndim != 2:
        raise ValueError(f"{name} must be a 2D array.")
    return result


def _as_bool_mask(
    mask: np.ndarray,
    name: str,
    expected_shape: tuple[int, int] | None = None,
) -> np.ndarray:
    bool_mask = np.asarray(mask, dtype=bool)
    if bool_mask.ndim != 2:
        raise ValueError(f"{name} must be a 2D mask.")
    if expected_shape is not None and bool_mask.shape != expected_shape:
        raise ValueError(f"{name} shape must be {expected_shape}, got {bool_mask.shape}.")
    return bool_mask


# UI callers may request constants/types without loading every analysis engine.
_LAZY_EXPORTS = {
    "AutoParameterResult": "profiles",
    "BatchThresholdState": "profiles",
    "BRIGHTFIELD_CRYSTAL_VIOLET_PROFILE": "profiles",
    "BRIGHTFIELD_HE_PROFILE": "profiles",
    "BRIGHTFIELD_OIL_RED_O_PROFILE": "profiles",
    "BRIGHTFIELD_SPECIAL_STAIN_PROFILE": "profiles",
    "DAB_IHC_PROFILE": "profiles",
    "TaskProfile": "profiles",
    "get_task_profile": "profiles",
    "apply_sensitivity_to_threshold": "auto_params",
    "build_tissue_background_mask": "auto_params",
    "estimate_dab_auto_parameters": "auto_params",
    "estimate_he_auto_parameters": "auto_params",
    "estimate_tissue_background_mask": "auto_params",
    "normalize_cleanup_level": "auto_params",
    "normalize_sensitivity": "auto_params",
    "detect_artifact_mask": "artifact_engine",
    "assess_dab_stain_compatibility": "color_qc",
    "assess_white_balance_color_cast": "color_qc",
    "OVERLAY_FAILURE_MESSAGE": "overlay_engine",
    "validate_overlay_result": "overlay_engine",
    "assess_image_quality": "quality_engine",
    "ExampleGuidedResult": "example_guided",
    "recalibrate_dab_threshold_from_examples": "example_guided",
    "ReliabilityResult": "reliability",
    "evaluate_analysis_reliability": "reliability",
    "evaluate_brightfield_reliability": "reliability",
    "evaluate_ihc_reliability": "reliability",
    "AnalysisResult": "result_model",
    "classify_dab_gmm": "stain_engine",
    "deconvolve_h_dab": "stain_engine",
    "estimate_stain_vectors_macenko": "stain_engine",
    "rgb_to_dab_channel": "stain_engine",
    "rgb_to_hematoxylin_channel": "stain_engine",
    "rgb_to_od": "stain_engine",
    "analyze_brightfield_image": "brightfield",
    "filter_labeled_objects": "brightfield",
    "rgb_to_grayscale_channel": "brightfield",
    "segment_grayscale": "brightfield",
    "segment_hsv": "brightfield",
    "segment_lab": "brightfield",
}


def __getattr__(name):
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module
    value = getattr(import_module(f".{module_name}", __name__), name)
    globals()[name] = value
    return value

__all__ = [
    "apply_sensitivity_to_threshold",
    "analyze_brightfield_image",
    "AnalysisResult",
    "AutoParameterResult",
    "assess_dab_stain_compatibility",
    "assess_image_quality",
    "assess_white_balance_color_cast",
    "BatchThresholdState",
    "BRIGHTFIELD_CRYSTAL_VIOLET_PROFILE",
    "BRIGHTFIELD_HE_PROFILE",
    "BRIGHTFIELD_OIL_RED_O_PROFILE",
    "BRIGHTFIELD_SPECIAL_STAIN_PROFILE",
    "build_tissue_background_mask",
    "clean_mask",
    "compute_hscore",
    "DAB_IHC_PROFILE",
    "detect_artifact_mask",
    "estimate_dab_auto_parameters",
    "estimate_he_auto_parameters",
    "estimate_tissue_background_mask",
    "evaluate_analysis_reliability",
    "evaluate_brightfield_reliability",
    "evaluate_ihc_reliability",
    "ExampleGuidedResult",
    "classify_dab_gmm",
    "deconvolve_h_dab",
    "estimate_stain_vectors_macenko",
    "filter_labeled_objects",
    "get_task_profile",
    "measure_area_fraction",
    "measure_channel_intensity",
    "normalize_cleanup_level",
    "normalize_sensitivity",
    "recalibrate_dab_threshold_from_examples",
    "ReliabilityResult",
    "render_mask_overlay",
    "rgb_to_dab_channel",
    "rgb_to_grayscale_channel",
    "rgb_to_hematoxylin_channel",
    "rgb_to_od",
    "segment_grayscale",
    "segment_hsv",
    "segment_lab",
    "TaskProfile",
    "threshold_channel",
    "validate_overlay_result",
    "OVERLAY_FAILURE_MESSAGE",
]
