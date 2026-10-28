from __future__ import annotations

from typing import Any

import numpy as np


def detect_artifact_mask(
    rgb: np.ndarray,
    tissue_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    from skimage.color import rgb2gray
    from skimage.color import rgb2hsv
    rgb_unit = _as_rgb_unit(rgb)
    gray = np.asarray(rgb2gray(rgb_unit), dtype=np.float64)
    hsv = np.asarray(rgb2hsv(rgb_unit), dtype=np.float64)
    saturation = hsv[..., 1]
    selected = _resolve_selected_mask(gray.shape, tissue_mask=tissue_mask)

    dark_debris_mask = selected & (gray <= 0.08)
    overexposed_blank_mask = selected & (gray >= 0.98) & (saturation <= 0.03)
    artifact_mask = np.asarray(dark_debris_mask | overexposed_blank_mask, dtype=bool)

    artifact_types_detected: list[str] = []
    warnings: list[str] = []
    if np.any(dark_debris_mask):
        artifact_types_detected.append("dark_debris")
        warnings.append("Dark debris-like regions were excluded from analysis.")
    if np.any(overexposed_blank_mask):
        artifact_types_detected.append("overexposed_blank")
        warnings.append("Bright overexposed or blank-like regions were excluded from analysis.")

    selected_area = int(np.count_nonzero(selected))
    artifact_fraction = float(np.count_nonzero(artifact_mask) / selected_area) if selected_area else 0.0

    return {
        "artifact_mask": artifact_mask,
        "artifact_fraction": artifact_fraction,
        "artifact_types_detected": artifact_types_detected,
        "warnings": warnings,
    }


def _resolve_selected_mask(
    shape: tuple[int, int],
    tissue_mask: np.ndarray | None = None,
) -> np.ndarray:
    if tissue_mask is None:
        return np.ones(shape, dtype=bool)
    selected = np.asarray(tissue_mask, dtype=bool)
    if selected.ndim != 2 or selected.shape != shape:
        raise ValueError(f"tissue_mask shape must be {shape}, got {selected.shape}.")
    return selected


def _as_rgb_unit(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if np.max(array) <= 1.0:
        return np.clip(array, 0.0, 1.0)
    return np.clip(array, 0.0, 255.0) / 255.0
