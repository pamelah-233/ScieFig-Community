from __future__ import annotations

from typing import Any

import numpy as np


def assess_image_quality(rgb: np.ndarray) -> dict[str, Any]:
    from scipy import ndimage
    from skimage.color import rgb2gray
    rgb_unit = _as_rgb_unit(rgb)
    gray = np.asarray(rgb2gray(rgb_unit), dtype=np.float64)
    saturation = np.max(rgb_unit, axis=2) - np.min(rgb_unit, axis=2)
    tissue_mask = (gray < 0.92) | (saturation > 0.08)
    background_mask = ~tissue_mask

    exposure_mean = float(np.mean(gray, dtype=np.float64))
    if exposure_mean >= 0.92:
        exposure_status = "overexposed"
    elif exposure_mean <= 0.12:
        exposure_status = "underexposed"
    else:
        exposure_status = "ok"

    white_balance_pixels = rgb_unit[background_mask] if np.any(background_mask) else rgb_unit.reshape(-1, 3)
    channel_means = np.mean(white_balance_pixels, axis=0)
    white_balance_delta = float(np.max(np.abs(channel_means - np.mean(channel_means))))
    white_balance_status = "balanced" if white_balance_delta <= 0.08 else "cast_warning"

    blur_score = float(np.var(ndimage.laplace(gray), dtype=np.float64))
    tissue_fraction = float(np.mean(tissue_mask, dtype=np.float64))
    background_fraction = float(np.mean(background_mask, dtype=np.float64))
    stain_signal_score = float(np.mean(saturation[tissue_mask], dtype=np.float64)) if np.any(tissue_mask) else 0.0

    warnings: list[str] = []
    if exposure_status == "overexposed":
        warnings.append("Image appears overexposed or background-dominant.")
    elif exposure_status == "underexposed":
        warnings.append("Image appears underexposed.")
    if white_balance_status != "balanced":
        warnings.append("White balance or color cast may be shifted.")
    if np.any(tissue_mask) and blur_score < 0.0025:
        warnings.append("Image appears soft or blurred.")
    if tissue_fraction <= 0.05:
        warnings.append("Very little tissue was detected in the field of view.")
    if stain_signal_score <= 0.03 and tissue_fraction > 0.0:
        warnings.append("Stain signal appears weak relative to the detected tissue.")

    return {
        "exposure_status": exposure_status,
        "white_balance_status": white_balance_status,
        "blur_score": blur_score,
        "tissue_fraction": tissue_fraction,
        "background_fraction": background_fraction,
        "stain_signal_score": stain_signal_score,
        "warnings": warnings,
        "tissue_mask": np.asarray(tissue_mask, dtype=bool),
        "background_mask": np.asarray(background_mask, dtype=bool),
    }


def _as_rgb_unit(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if np.max(array) <= 1.0:
        return np.clip(array, 0.0, 1.0)
    return np.clip(array, 0.0, 255.0) / 255.0
