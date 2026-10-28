from __future__ import annotations

from typing import Any

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover - exercised through fallback behavior
    cv2 = None


def cleanup_segmentation_mask(
    mask: np.ndarray,
    *,
    min_area_px: int = 0,
    fill_holes: bool = False,
    morphology_kernel_px: int = 0,
) -> np.ndarray:
    cleaned = _as_bool_mask(mask)
    kernel_px = _normalize_kernel_px(morphology_kernel_px)

    if kernel_px > 1:
        cleaned = _apply_morphology(cleaned, kernel_px=kernel_px, fill_holes=fill_holes)
    elif fill_holes:
        cleaned = _fill_holes(cleaned)

    if int(min_area_px) > 0:
        cleaned = np.asarray(
            summarize_connected_components(cleaned, min_area_px=int(min_area_px), max_area_px=0)["mask"],
            dtype=bool,
        )

    return np.asarray(cleaned, dtype=bool)


def clean_binary_mask_cv2(
    mask: np.ndarray,
    open_kernel: int = 3,
    close_kernel: int = 3,
    fill_holes: bool = True,
) -> np.ndarray:
    from scipy import ndimage
    cleaned = _as_bool_mask(mask)
    if fill_holes:
        cleaned = _fill_holes(cleaned)

    if cv2 is not None:
        binary = _to_cv2_mask(cleaned)
        for operation, kernel_px in (
            (cv2.MORPH_OPEN, _normalize_kernel_px(open_kernel)),
            (cv2.MORPH_CLOSE, _normalize_kernel_px(close_kernel)),
        ):
            if kernel_px > 1:
                kernel = np.ones((kernel_px, kernel_px), dtype=np.uint8)
                binary = cv2.morphologyEx(binary, operation, kernel)
        result = binary > 0
    else:
        result = cleaned
        for operation, kernel_px in (
            (ndimage.binary_opening, _normalize_kernel_px(open_kernel)),
            (ndimage.binary_closing, _normalize_kernel_px(close_kernel)),
        ):
            if kernel_px > 1:
                structure = np.ones((kernel_px, kernel_px), dtype=bool)
                result = operation(result, structure=structure)

    if fill_holes:
        result = _fill_holes(result)
    return np.asarray(result, dtype=bool)


def connected_components_with_stats(
    mask: np.ndarray,
    min_area_px: int = 0,
    max_area_px: int = 0,
) -> dict[str, Any]:
    return summarize_connected_components(
        mask,
        min_area_px=int(min_area_px),
        max_area_px=int(max_area_px),
    )


def find_contours_summary(mask: np.ndarray) -> dict[str, Any]:
    from skimage.measure import label
    from skimage.measure import regionprops
    binary = _as_bool_mask(mask)
    if cv2 is None:
        labels = label(binary)
        areas = [float(region.area) for region in regionprops(labels)]
        return {
            "contour_count": len(areas),
            "contour_area_px_values": areas,
            "total_contour_area_px": float(sum(areas)),
        }

    contours, _hierarchy = cv2.findContours(
        _to_cv2_mask(binary),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    areas = [float(cv2.contourArea(contour)) for contour in contours]
    return {
        "contour_count": len(contours),
        "contour_area_px_values": areas,
        "total_contour_area_px": float(sum(areas)),
    }


def summarize_connected_components(
    mask: np.ndarray,
    min_area_px: int = 0,
    max_area_px: int = 0,
) -> dict[str, Any]:
    binary = _as_bool_mask(mask)
    if cv2 is not None:
        object_summary = _summarize_components_cv2(
            binary,
            min_area_px=int(min_area_px),
            max_area_px=int(max_area_px),
        )
    else:
        object_summary = _summarize_components_fallback(
            binary,
            min_area_px=int(min_area_px),
            max_area_px=int(max_area_px),
        )

    total_object_area_px = int(sum(object_summary["object_area_px_values"]))
    object_count = int(len(object_summary["object_area_px_values"]))
    mean_object_area_px = float(total_object_area_px / object_count) if object_count else 0.0

    return {
        "mask": np.asarray(object_summary["mask"], dtype=bool),
        "object_count": object_count,
        "total_object_area_px": total_object_area_px,
        "mean_object_area_px": mean_object_area_px,
        "object_area_px_values": list(object_summary["object_area_px_values"]),
    }


def _apply_morphology(mask: np.ndarray, *, kernel_px: int, fill_holes: bool) -> np.ndarray:
    from scipy import ndimage
    base_mask = _fill_holes(mask) if fill_holes else _as_bool_mask(mask)
    if cv2 is not None:
        binary = _to_cv2_mask(base_mask)
        kernel = np.ones((kernel_px, kernel_px), dtype=np.uint8)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        return _rebuild_mask_from_contours(binary > 0)

    structure = np.ones((kernel_px, kernel_px), dtype=bool)
    return np.asarray(ndimage.binary_opening(base_mask, structure=structure), dtype=bool)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    from scipy import ndimage
    return np.asarray(ndimage.binary_fill_holes(_as_bool_mask(mask)), dtype=bool)


def _rebuild_mask_from_contours(mask: np.ndarray) -> np.ndarray:
    binary = _as_bool_mask(mask)
    if cv2 is None:
        return binary

    contours, _hierarchy = cv2.findContours(
        _to_cv2_mask(binary),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return binary

    rebuilt = np.zeros(binary.shape, dtype=np.uint8)
    cv2.drawContours(rebuilt, contours, contourIdx=-1, color=255, thickness=cv2.FILLED)
    return rebuilt > 0


def _summarize_components_cv2(
    mask: np.ndarray,
    *,
    min_area_px: int,
    max_area_px: int,
) -> dict[str, Any]:
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        np.asarray(mask, dtype=np.uint8),
        connectivity=8,
    )
    filtered = np.zeros(mask.shape, dtype=bool)
    areas: list[int] = []

    for component_label in range(1, int(component_count)):
        area_px = int(stats[component_label, cv2.CC_STAT_AREA])
        if min_area_px > 0 and area_px < min_area_px:
            continue
        if max_area_px > 0 and area_px > max_area_px:
            continue
        filtered[labels == component_label] = True
        areas.append(area_px)

    return {"mask": filtered, "object_area_px_values": areas}


def _summarize_components_fallback(
    mask: np.ndarray,
    *,
    min_area_px: int,
    max_area_px: int,
) -> dict[str, Any]:
    from skimage.measure import label
    from skimage.measure import regionprops
    labels = label(mask)
    filtered = np.zeros(mask.shape, dtype=bool)
    areas: list[int] = []

    for region in regionprops(labels):
        area_px = int(region.area)
        if min_area_px > 0 and area_px < min_area_px:
            continue
        if max_area_px > 0 and area_px > max_area_px:
            continue
        filtered[labels == region.label] = True
        areas.append(area_px)

    return {"mask": filtered, "object_area_px_values": areas}


def _normalize_kernel_px(kernel_px: int) -> int:
    value = max(0, int(kernel_px))
    if value <= 1:
        return 0
    return value if value % 2 == 1 else value + 1


def _to_cv2_mask(mask: np.ndarray) -> np.ndarray:
    return np.asarray(mask, dtype=np.uint8) * 255


def _as_bool_mask(mask: np.ndarray) -> np.ndarray:
    bool_mask = np.asarray(mask, dtype=bool)
    if bool_mask.ndim != 2:
        raise ValueError("mask must be a 2D mask.")
    return bool_mask
