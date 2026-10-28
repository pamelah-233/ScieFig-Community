from __future__ import annotations

from typing import Any

import numpy as np

from . import clean_mask
from .profiles import AutoParameterResult, BRIGHTFIELD_HE_PROFILE, DAB_IHC_PROFILE
from .stain_engine import deconvolve_h_dab, rgb_to_dab_channel


SENSITIVITY_OFFSETS = {
    "low": 0.30,
    "medium": 0.00,
    "high": -0.30,
}

_CLEANUP_LEVELS = {
    "off": 0,
    "low": 2,
    "medium": 4,
    "high": 8,
}


def estimate_dab_auto_parameters(
    rgb: np.ndarray,
    sensitivity: str = "medium",
    cleanup_level: str = "medium",
) -> AutoParameterResult:
    tissue = estimate_tissue_background_mask(rgb)
    tissue_mask = np.asarray(tissue["mask"], dtype=bool)
    channel_source = "dab_od"
    stain_warnings: list[str] = []
    try:
        stain_deconvolution = deconvolve_h_dab(rgb, tissue_mask=tissue_mask)
        dab = np.asarray(stain_deconvolution["dab_od"], dtype=np.float64)
        stain_warnings.extend(str(item) for item in stain_deconvolution.get("warnings", []))
    except Exception as exc:
        dab = rgb_to_dab_channel(rgb)
        channel_source = "legacy_rgb_to_dab_fallback"
        stain_warnings.append(f"DAB deconvolution fallback: {exc}")

    tissue_values = dab[tissue_mask] if np.any(tissue_mask) else dab.reshape(-1)

    p25, p50, p75, p90, p95 = _quantiles(tissue_values, (0.25, 0.50, 0.75, 0.90, 0.95))
    mad = _median_absolute_deviation(tissue_values, center=p50)

    if p50 > 0.15:
        candidate_floor = max(0.05, p25)
    else:
        candidate_floor = p50 + max(0.02, 1.5 * mad, 0.10 * max(0.0, p90 - p50))
    candidate_floor = _clamp(candidate_floor, 0.02, 3.0)

    candidate_values = tissue_values[tissue_values >= candidate_floor]
    if candidate_values.size == 0:
        fallback_floor = _clamp(max(0.02, p75 * 0.8), 0.02, 3.0)
        candidate_values = tissue_values[tissue_values >= fallback_floor]
    if candidate_values.size == 0:
        candidate_values = tissue_values[tissue_values > 0.0]
    if candidate_values.size == 0:
        candidate_values = tissue_values

    candidate_dynamic_range = (
        float(np.quantile(candidate_values, 0.90)) - float(np.quantile(candidate_values, 0.10))
        if candidate_values.size
        else 0.0
    )
    estimated_threshold = _clamp(float(np.quantile(candidate_values, 0.20)), 0.05, 3.0)
    if candidate_dynamic_range < 0.04:
        estimated_threshold = _clamp(max(0.05, p95 * 0.45), 0.05, 3.0)
    effective_threshold = apply_sensitivity_to_threshold(estimated_threshold, sensitivity)
    min_area_px = cleanup_min_area_for_level(rgb.shape[:2], cleanup_level)

    qc_warnings: list[str] = []
    tissue_fraction = float(tissue.get("coverage_fraction", 0.0))
    if tissue_fraction < 0.08 or tissue_fraction > 0.98:
        qc_warnings.append(
            "The image contains too much background or the estimated tissue area is very small. Manual review is recommended."
        )
    if p95 < 0.40:
        qc_warnings.append(
            "DAB signal appears weak. The automatic threshold may underestimate weak positive staining. Consider increasing Sensitivity."
        )
    min_candidate_count = max(16, int(round(tissue_values.size * 0.02)))
    if candidate_values.size < min_candidate_count or candidate_dynamic_range < 0.04:
        qc_warnings.append(
            "The automatic DAB threshold may be unstable for this image. Review the overlay before using the result."
        )
    qc_warnings.extend(stain_warnings)

    confidence_score = _confidence_score(qc_warnings)
    confidence_label = _confidence_label(confidence_score)

    return AutoParameterResult(
        task_id=DAB_IHC_PROFILE.task_id,
        recommended_thresholds={
            "estimated_positive_threshold": estimated_threshold,
            "effective_positive_threshold": effective_threshold,
            "sensitivity": normalize_sensitivity(sensitivity),
        },
        recommended_tissue_mask_settings={
            "enabled": True,
            "mode": str(tissue["settings"]["mode"]),
            "value_threshold": float(tissue["settings"]["value_threshold"]),
            "saturation_threshold": float(tissue["settings"]["saturation_threshold"]),
            "min_area_px": int(tissue["settings"]["min_area_px"]),
        },
        recommended_min_area=min_area_px,
        recommended_max_area=None,
        recommended_watershed=False,
        confidence_label=confidence_label,
        confidence_score=confidence_score,
        qc_warnings=qc_warnings,
        explanation=(
            "The software estimated tissue/background separation first, then used robust DAB optical-density "
            "statistics to recommend a local threshold for positive-area measurement."
        ),
        threshold_method="robust_candidate_quantile",
        metadata={
            "tissue_fraction": tissue_fraction,
            "candidate_count": int(candidate_values.size),
            "dab_channel_source": channel_source,
            "dab_quantiles": {
                "p25": p25,
                "p50": p50,
                "p75": p75,
                "p90": p90,
                "p95": p95,
            },
        },
    )


def estimate_he_auto_parameters(
    rgb: np.ndarray,
    sensitivity: str = "medium",
    cleanup_level: str = "low",
) -> AutoParameterResult:
    from skimage.color import rgb2lab
    tissue = estimate_tissue_background_mask(rgb)
    mask = np.asarray(tissue["mask"], dtype=bool)
    lab = rgb2lab(_as_rgb_unit(rgb))
    l_channel = lab[..., 0]
    a_channel = lab[..., 1]
    b_channel = lab[..., 2]

    selected = mask if np.any(mask) else np.ones(a_channel.shape, dtype=bool)
    a_values = a_channel[selected]
    b_values = b_channel[selected]
    l_values = l_channel[selected]

    sensitivity_name = normalize_sensitivity(sensitivity)
    sensitivity_scale = {
        "low": 1.10,
        "medium": 1.00,
        "high": 0.90,
    }[sensitivity_name]

    a_min = float(np.quantile(a_values, 0.05) * sensitivity_scale - 6.0)
    a_max = float(np.quantile(a_values, 0.995) + 8.0)
    b_min = float(np.quantile(b_values, 0.05) - 10.0)
    b_max = float(np.quantile(b_values, 0.995) + 10.0)
    l_min = float(max(0.0, np.quantile(l_values, 0.01) - 8.0))
    l_max = float(min(100.0, np.quantile(l_values, 0.995) + 4.0))
    min_area_px = cleanup_min_area_for_level(rgb.shape[:2], cleanup_level)

    qc_warnings: list[str] = []
    if float(tissue.get("coverage_fraction", 0.0)) < 0.05:
        qc_warnings.append(
            "The image appears background-dominant. Review the H&E overlay before using the automatic result."
        )
    if float(np.quantile(a_values, 0.80)) < 8.0:
        qc_warnings.append(
            "Eosin/pink signal appears weak. The recommended settings may miss very pale staining."
        )

    confidence_score = _confidence_score(qc_warnings)
    confidence_label = _confidence_label(confidence_score)

    return AutoParameterResult(
        task_id=BRIGHTFIELD_HE_PROFILE.task_id,
        recommended_thresholds={
            "segmentation_mode": "lab",
            "l_min": _clamp(l_min, 0.0, 100.0),
            "l_max": _clamp(l_max, 0.0, 100.0),
            "a_min": _clamp(a_min, -128.0, 127.0),
            "a_max": _clamp(a_max, -128.0, 127.0),
            "b_min": _clamp(b_min, -128.0, 127.0),
            "b_max": _clamp(b_max, -128.0, 127.0),
        },
        recommended_tissue_mask_settings={
            "enabled": True,
            "mode": str(tissue["settings"]["mode"]),
            "value_threshold": float(tissue["settings"]["value_threshold"]),
            "saturation_threshold": float(tissue["settings"]["saturation_threshold"]),
            "min_area_px": int(tissue["settings"]["min_area_px"]),
        },
        recommended_min_area=min_area_px,
        recommended_max_area=None,
        recommended_watershed=False,
        confidence_label=confidence_label,
        confidence_score=confidence_score,
        qc_warnings=qc_warnings,
        explanation=(
            "The software detected eosin-like pink signal and recommended LAB-based brightfield settings "
            "that are more tolerant of pink H&E than the default HSV preset."
        ),
        threshold_method="lab_eosin_quantiles",
        metadata={
            "tissue_fraction": float(tissue.get("coverage_fraction", 0.0)),
            "sensitivity": sensitivity_name,
        },
    )


def estimate_tissue_background_mask(
    rgb: np.ndarray,
    *,
    value_threshold: float | None = None,
    saturation_threshold: float | None = None,
    min_area_px: int | None = None,
) -> dict[str, Any]:
    from skimage.color import rgb2gray
    from skimage.color import rgb2hsv
    rgb_unit = _as_rgb_unit(rgb)
    hsv = rgb2hsv(rgb_unit)
    gray = rgb2gray(rgb_unit)

    value = hsv[..., 2]
    saturation = hsv[..., 1]

    resolved_value_threshold = float(
        value_threshold if value_threshold is not None else max(0.68, np.quantile(value, 0.85) - 0.05)
    )
    resolved_saturation_threshold = float(
        saturation_threshold if saturation_threshold is not None else max(0.05, np.quantile(saturation, 0.70))
    )
    resolved_min_area_px = int(
        min_area_px if min_area_px is not None else max(4, int(round((rgb.shape[0] * rgb.shape[1]) * 0.0005)))
    )

    tissue_mask = (
        (value <= resolved_value_threshold)
        | (saturation >= resolved_saturation_threshold + 0.02)
        | (gray <= min(0.95, resolved_value_threshold - 0.02))
    )
    cleaned = clean_mask(tissue_mask, min_area_px=resolved_min_area_px, fill_holes=True)

    return {
        "mask": np.asarray(cleaned, dtype=bool),
        "coverage_fraction": float(np.count_nonzero(cleaned) / cleaned.size) if cleaned.size else 0.0,
        "settings": {
            "enabled": True,
            "mode": "value_and_saturation",
            "value_threshold": resolved_value_threshold,
            "saturation_threshold": resolved_saturation_threshold,
            "min_area_px": resolved_min_area_px,
        },
    }


def build_tissue_background_mask(
    rgb: np.ndarray,
    settings: dict[str, Any] | None = None,
) -> np.ndarray:
    data = dict(settings or {})
    if not bool(data.get("enabled", data.get("tissue_mask_enabled", True))):
        return np.ones(rgb.shape[:2], dtype=bool)
    result = estimate_tissue_background_mask(
        rgb,
        value_threshold=data.get("value_threshold", data.get("tissue_mask_value_threshold")),
        saturation_threshold=data.get("saturation_threshold", data.get("tissue_mask_saturation_threshold")),
        min_area_px=data.get("min_area_px", data.get("tissue_mask_min_area_px")),
    )
    return np.asarray(result["mask"], dtype=bool)


def cleanup_min_area_for_level(image_shape: tuple[int, int], cleanup_level: str) -> int:
    level = normalize_cleanup_level(cleanup_level)
    base = _CLEANUP_LEVELS[level]
    if base == 0:
        return 0
    scale = max(1, int(round(np.sqrt(float(image_shape[0] * image_shape[1])) / 256.0)))
    return int(base * scale)


def apply_sensitivity_to_threshold(base_threshold: float, sensitivity: str) -> float:
    level = normalize_sensitivity(sensitivity)
    offset = SENSITIVITY_OFFSETS[level]
    return _clamp(float(base_threshold) * (1.0 + offset), 0.02, 3.0)


def normalize_sensitivity(sensitivity: str) -> str:
    value = str(sensitivity).strip().lower()
    if value in SENSITIVITY_OFFSETS:
        return value
    return "medium"


def normalize_cleanup_level(cleanup_level: str) -> str:
    value = str(cleanup_level).strip().lower()
    if value in _CLEANUP_LEVELS:
        return value
    return "medium"


def _confidence_score(qc_warnings: list[str]) -> float:
    return _clamp(0.92 - 0.18 * len(qc_warnings), 0.20, 0.92)


def _confidence_label(score: float) -> str:
    if score >= 0.75:
        return "high"
    if score >= 0.50:
        return "medium"
    return "low"


def _median_absolute_deviation(values: np.ndarray, *, center: float) -> float:
    if values.size == 0:
        return 0.0
    return float(np.median(np.abs(values - center)))


def _quantiles(values: np.ndarray, quantiles: tuple[float, ...]) -> tuple[float, ...]:
    if values.size == 0:
        return tuple(0.0 for _ in quantiles)
    return tuple(float(np.quantile(values, q)) for q in quantiles)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return float(min(max(value, minimum), maximum))


def _as_rgb_unit(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if np.max(array) <= 1.0:
        return np.clip(array, 0.0, 1.0)
    return np.clip(array, 0.0, 255.0) / 255.0
