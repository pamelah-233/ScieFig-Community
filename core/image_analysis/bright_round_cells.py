"""Small, deterministic analyzer for bright round/elongated objects in Brightfield images.

The analyzer is intentionally conservative: it keeps only objects that remain
stable when the local-brightness threshold is nudged.  All returned values are
plain Python values so a result can be stored in a panel JSON payload.
"""

from __future__ import annotations

from math import hypot, sqrt
from typing import Any, Mapping, Sequence

import cv2
import numpy as np


BRIGHT_ROUND_CELL_ANALYSIS_VERSION = "bright_round_cells.v1"

DEFAULT_BRIGHT_ROUND_CELL_SETTINGS: dict[str, Any] = {
    # The first two values are the controls most likely to need adjustment
    # between exposures.  They intentionally match the validated demo.
    "residual_threshold": 18.0,
    "min_mean_intensity": 165.0,
    "min_circularity": 0.55,
    # Used for shape classification only; it is not a rejection threshold.
    "shape_aspect_ratio_threshold": 0.70,
    "min_elongated_circularity": 0.18,
    "min_dark_elongated_circularity": 0.04,
    "min_solidity": 0.78,
    "min_area_px": 35.0,
    "max_area_px": 900.0,
    "min_diameter_px": 8.0,
    "max_diameter_px": 40.0,
    # None means use the pixel limits above.  The panel can provide these
    # values when it exposes diameter limits in calibrated micrometres.
    "min_diameter_um": None,
    "max_diameter_um": None,
    "background_sigma": 12.0,
    "morphology_kernel": 5,
    "threshold_offsets": (0.0, 4.0, 8.0),
    "max_threshold_relative_spread": 0.25,
    # Elongated differentiated cells often have a dark body rather than a
    # bright residual.  This second path uses a black-hat contrast image.
    "elongated_contrast_threshold": 16.0,
    "elongated_blackhat_kernel": 15,
    "elongated_morphology_kernel": 3,
    "max_dark_elongated_aspect_ratio": 0.35,
    "min_elongated_solidity": 0.22,
    "min_elongated_area_px": 150.0,
    "max_elongated_area_px": 6000.0,
    "min_elongated_feret_px": 45.0,
    "max_elongated_feret_px": 320.0,
    "min_elongated_minor_feret_px": 4.0,
    "elongated_threshold_offsets": (0.0, 2.0, 4.0),
    "max_elongated_threshold_relative_spread": 0.40,
    "border_margin_px": 1.0,
    "exclude_scale_bar": True,
}


def _positive_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if number > 0 else float(default)


def _finite_float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if np.isfinite(number) else float(default)


def _optional_positive_float(value: Any) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def _normalise_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_BRIGHT_ROUND_CELL_SETTINGS)
    if settings:
        merged.update(dict(settings))

    numeric_defaults = {
        "residual_threshold": 18.0,
        "min_mean_intensity": 165.0,
        "min_circularity": 0.55,
        "shape_aspect_ratio_threshold": 0.70,
        "min_elongated_circularity": 0.18,
        "min_dark_elongated_circularity": 0.04,
        "min_solidity": 0.78,
        "min_area_px": 35.0,
        "max_area_px": 900.0,
        "min_diameter_px": 8.0,
        "max_diameter_px": 40.0,
        "background_sigma": 12.0,
        "max_threshold_relative_spread": 0.25,
        "elongated_contrast_threshold": 16.0,
        "max_dark_elongated_aspect_ratio": 0.35,
        "min_elongated_solidity": 0.22,
        "min_elongated_area_px": 150.0,
        "max_elongated_area_px": 6000.0,
        "min_elongated_feret_px": 45.0,
        "max_elongated_feret_px": 320.0,
        "min_elongated_minor_feret_px": 4.0,
        "max_elongated_threshold_relative_spread": 0.40,
        "border_margin_px": 1.0,
    }
    for key, default in numeric_defaults.items():
        merged[key] = _finite_float(merged.get(key), default)
    merged["min_diameter_um"] = _optional_positive_float(merged.get("min_diameter_um"))
    merged["max_diameter_um"] = _optional_positive_float(merged.get("max_diameter_um"))

    kernel = int(round(_positive_float(merged.get("morphology_kernel"), 5)))
    merged["morphology_kernel"] = max(1, kernel | 1)
    blackhat_kernel = int(round(_positive_float(merged.get("elongated_blackhat_kernel"), 15)))
    merged["elongated_blackhat_kernel"] = max(3, blackhat_kernel | 1)
    elongated_kernel = int(round(_positive_float(merged.get("elongated_morphology_kernel"), 3)))
    merged["elongated_morphology_kernel"] = max(1, elongated_kernel | 1)
    raw_offsets = merged.get("threshold_offsets", (0.0, 4.0, 8.0))
    if isinstance(raw_offsets, (str, bytes)):
        raw_offsets = (0.0, 4.0, 8.0)
    try:
        offsets = sorted({max(0.0, float(value)) for value in raw_offsets})
    except (TypeError, ValueError):
        offsets = [0.0, 4.0, 8.0]
    if not offsets or offsets[0] != 0.0:
        offsets.insert(0, 0.0)
    merged["threshold_offsets"] = [float(value) for value in offsets]
    raw_elongated_offsets = merged.get("elongated_threshold_offsets", (0.0, 2.0, 4.0))
    if isinstance(raw_elongated_offsets, (str, bytes)):
        raw_elongated_offsets = (0.0, 2.0, 4.0)
    try:
        elongated_offsets = sorted({max(0.0, float(value)) for value in raw_elongated_offsets})
    except (TypeError, ValueError):
        elongated_offsets = [0.0, 2.0, 4.0]
    if not elongated_offsets or elongated_offsets[0] != 0.0:
        elongated_offsets.insert(0, 0.0)
    merged["elongated_threshold_offsets"] = [float(value) for value in elongated_offsets]
    merged["exclude_scale_bar"] = bool(merged.get("exclude_scale_bar", True))
    return merged


def _as_rgb_uint8(rgb: np.ndarray) -> np.ndarray:
    image = np.asarray(rgb)
    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("Bright round-cell analysis requires an RGB image with shape (H, W, 3).")
    if image.dtype == np.uint8:
        return image.copy()
    return np.clip(image, 0, 255).astype(np.uint8)


def _detect_scale_bar_region(gray: np.ndarray) -> dict[str, int] | None:
    """Find a long, bright horizontal line in the lower-right image corner."""

    height, width = gray.shape[:2]
    x_start = int(width * 0.40)
    y_start = int(height * 0.70)
    bright = np.zeros_like(gray, dtype=np.uint8)
    bright[y_start:, x_start:] = (gray[y_start:, x_start:] >= 220).astype(np.uint8) * 255
    kernel_width = max(8, int(round(width * 0.025)))
    horizontal_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
    horizontal = cv2.morphologyEx(bright, cv2.MORPH_OPEN, horizontal_kernel)
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    candidates: list[tuple[int, int, int, int]] = []
    minimum_width = max(20, int(round(width * 0.08)))
    for contour in contours:
        x, y, box_width, box_height = cv2.boundingRect(contour)
        if box_width < minimum_width or box_width / max(1.0, float(box_height)) < 8.0:
            continue
        if x < x_start or y < y_start:
            continue
        candidates.append((x, y, box_width, box_height))
    if not candidates:
        return None
    x, y, box_width, box_height = max(candidates, key=lambda item: item[2])
    padding_x = max(12, int(round(width * 0.012)))
    padding_top = max(6, int(round(height * 0.008)))
    return {
        "x": max(0, x - padding_x),
        "y": max(0, y - padding_top),
        "w": min(width, x + box_width + padding_x) - max(0, x - padding_x),
        "h": height - max(0, y - padding_top),
    }


def _maximum_feret(hull: np.ndarray) -> tuple[tuple[int, int], tuple[int, int], float]:
    points = hull.reshape(-1, 2).astype(np.float32)
    if len(points) == 0:
        return (0, 0), (0, 0), 0.0
    deltas = points[:, None, :] - points[None, :, :]
    squared = np.sum(deltas * deltas, axis=2)
    start_index, end_index = np.unravel_index(int(np.argmax(squared)), squared.shape)
    start = tuple(int(value) for value in points[start_index])
    end = tuple(int(value) for value in points[end_index])
    return start, end, float(sqrt(float(squared[start_index, end_index])))


def _diameter_limits_px(settings: Mapping[str, Any], um_per_px: float) -> tuple[float, float]:
    minimum = float(settings.get("min_diameter_px", 8.0))
    maximum = float(settings.get("max_diameter_px", 40.0))
    min_um = _optional_positive_float(settings.get("min_diameter_um"))
    max_um = _optional_positive_float(settings.get("max_diameter_um"))
    if min_um is not None:
        minimum = min_um / um_per_px
    if max_um is not None:
        maximum = max_um / um_per_px
    return max(0.0, minimum), max(max(0.0, minimum), maximum)


def _candidate_rows(
    gray: np.ndarray,
    residual: np.ndarray,
    threshold: float,
    settings: Mapping[str, Any],
    allowed_mask: np.ndarray,
    um_per_px: float,
) -> list[dict[str, Any]]:
    mask = (residual > float(threshold)).astype(np.uint8) * 255
    mask[~allowed_mask] = 0
    kernel_size = int(settings.get("morphology_kernel", 5))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    minimum_diameter, maximum_diameter = _diameter_limits_px(settings, um_per_px)
    rows: list[dict[str, Any]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if not float(settings.get("min_area_px", 35.0)) <= area <= float(settings.get("max_area_px", 900.0)):
            continue
        if perimeter <= 0:
            continue
        circularity = float(4.0 * np.pi * area / (perimeter * perimeter))
        (_, _), (box_width, box_height), _ = cv2.minAreaRect(contour)
        longest = max(float(box_width), float(box_height))
        aspect_ratio = float(min(float(box_width), float(box_height)) / longest) if longest > 0 else 0.0
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        solidity = float(area / max(hull_area, 1.0))
        equivalent_diameter = float(sqrt(4.0 * area / np.pi))
        if not minimum_diameter <= equivalent_diameter <= maximum_diameter:
            continue

        shape_threshold = float(settings.get("shape_aspect_ratio_threshold", 0.70))
        shape_class = "round" if aspect_ratio >= shape_threshold else "elongated"
        minimum_circularity = (
            float(settings.get("min_circularity", 0.55))
            if shape_class == "round"
            else float(settings.get("min_elongated_circularity", 0.18))
        )

        object_mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(object_mask, [contour], -1, 255, -1)
        pixels = gray[object_mask > 0]
        mean_intensity = float(pixels.mean()) if pixels.size else 0.0
        if (
            circularity < minimum_circularity
            or solidity < float(settings.get("min_solidity", 0.78))
            or mean_intensity < float(settings.get("min_mean_intensity", 165.0))
        ):
            continue

        moments = cv2.moments(contour)
        if abs(float(moments.get("m00", 0.0))) <= 1e-9:
            continue
        center_x = float(moments["m10"] / moments["m00"])
        center_y = float(moments["m01"] / moments["m00"])
        border_margin = float(settings.get("border_margin_px", 1.0))
        radius = equivalent_diameter / 2.0
        if (
            center_x - radius < border_margin
            or center_y - radius < border_margin
            or center_x + radius >= gray.shape[1] - border_margin
            or center_y + radius >= gray.shape[0] - border_margin
        ):
            continue
        feret_start, feret_end, max_feret = _maximum_feret(hull)
        rows.append(
            {
                "contour": contour,
                "center_x_px": center_x,
                "center_y_px": center_y,
                "area_px": area,
                "circularity": circularity,
                "aspect_ratio": aspect_ratio,
                "solidity": solidity,
                "mean_intensity": mean_intensity,
                "equivalent_diameter_px": equivalent_diameter,
                "max_feret_px": max_feret,
                "min_feret_px": float(min(float(box_width), float(box_height))),
                "feret_start_px": feret_start,
                "feret_end_px": feret_end,
                "shape_class": shape_class,
                "detection_source": "bright_residual",
            }
        )
    return rows


def _elongated_candidate_rows(
    gray: np.ndarray,
    blackhat: np.ndarray,
    threshold: float,
    settings: Mapping[str, Any],
    allowed_mask: np.ndarray,
) -> list[dict[str, Any]]:
    """Find elongated dark-body objects in a black-hat contrast image.

    This deliberately has separate size/solidity limits from the bright
    residual path.  Differentiated cells can be large, irregular and dark in
    the middle, so applying the round-cell filters here would discard them.
    """

    mask = (blackhat >= float(threshold)).astype(np.uint8) * 255
    mask[~allowed_mask] = 0
    kernel_size = int(settings.get("elongated_morphology_kernel", 3))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask[~allowed_mask] = 0
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    shape_threshold = float(settings.get("max_dark_elongated_aspect_ratio", 0.35))
    minimum_area = float(settings.get("min_elongated_area_px", 150.0))
    maximum_area = float(settings.get("max_elongated_area_px", 6000.0))
    minimum_feret = float(settings.get("min_elongated_feret_px", 45.0))
    maximum_feret = float(settings.get("max_elongated_feret_px", 320.0))
    minimum_minor_feret = float(settings.get("min_elongated_minor_feret_px", 4.0))
    minimum_solidity = float(settings.get("min_elongated_solidity", 0.22))
    minimum_circularity = float(settings.get("min_dark_elongated_circularity", 0.04))
    minimum_contrast = float(settings.get("elongated_contrast_threshold", 16.0))

    rows: list[dict[str, Any]] = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if not minimum_area <= area <= maximum_area:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            continue
        circularity = float(4.0 * np.pi * area / (perimeter * perimeter))
        (_, _), (box_width, box_height), _ = cv2.minAreaRect(contour)
        longest = max(float(box_width), float(box_height))
        shortest = min(float(box_width), float(box_height))
        aspect_ratio = float(shortest / longest) if longest > 0 else 0.0
        if aspect_ratio >= shape_threshold or shortest < minimum_minor_feret:
            continue
        hull = cv2.convexHull(contour)
        hull_area = float(cv2.contourArea(hull))
        solidity = float(area / max(hull_area, 1.0))
        if solidity < minimum_solidity or circularity < minimum_circularity:
            continue

        object_mask = np.zeros(gray.shape, dtype=np.uint8)
        cv2.drawContours(object_mask, [contour], -1, 255, -1)
        object_pixels = object_mask > 0
        contrast = float(blackhat[object_pixels].mean()) if np.any(object_pixels) else 0.0
        if contrast < minimum_contrast:
            continue
        mean_intensity = float(gray[object_pixels].mean()) if np.any(object_pixels) else 0.0
        moments = cv2.moments(contour)
        if abs(float(moments.get("m00", 0.0))) <= 1e-9:
            continue
        center_x = float(moments["m10"] / moments["m00"])
        center_y = float(moments["m01"] / moments["m00"])
        feret_start, feret_end, max_feret = _maximum_feret(hull)
        if not minimum_feret <= max_feret <= maximum_feret:
            continue
        equivalent_diameter = float(sqrt(4.0 * area / np.pi))
        rows.append(
            {
                "contour": contour,
                "center_x_px": center_x,
                "center_y_px": center_y,
                "area_px": area,
                "circularity": circularity,
                "aspect_ratio": aspect_ratio,
                "solidity": solidity,
                "mean_intensity": mean_intensity,
                "contrast_mean": contrast,
                "equivalent_diameter_px": equivalent_diameter,
                "max_feret_px": max_feret,
                "min_feret_px": shortest,
                "feret_start_px": feret_start,
                "feret_end_px": feret_end,
                "shape_class": "elongated",
                "detection_source": "dark_background",
            }
        )
    return rows


def _stable_rows(
    detections: Mapping[float, list[dict[str, Any]]],
    thresholds: Sequence[float],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if not thresholds:
        return []
    base_threshold = float(thresholds[0])
    base_rows = list(detections.get(base_threshold, []))
    stable: list[dict[str, Any]] = []
    for base in base_rows:
        matched: list[dict[str, Any]] = []
        base_x = float(base["center_x_px"])
        base_y = float(base["center_y_px"])
        base_diameter = float(base["equivalent_diameter_px"])
        for threshold in thresholds[1:]:
            candidates = detections.get(float(threshold), [])
            if not candidates:
                break
            candidate = min(
                candidates,
                key=lambda item: (base_x - float(item["center_x_px"])) ** 2
                + (base_y - float(item["center_y_px"])) ** 2,
            )
            distance = hypot(base_x - float(candidate["center_x_px"]), base_y - float(candidate["center_y_px"]))
            if distance > max(6.0, 0.3 * base_diameter):
                break
            matched.append(candidate)
        if len(matched) != max(0, len(thresholds) - 1):
            continue
        diameters = np.asarray(
            [base_diameter, *(float(item["equivalent_diameter_px"]) for item in matched)],
            dtype=float,
        )
        spread = float((diameters.max() - diameters.min()) / max(float(np.median(diameters)), 1e-9))
        if spread > float(settings.get("max_threshold_relative_spread", 0.25)):
            continue
        base["threshold_relative_spread"] = spread
        stable.append(base)
    return sorted(stable, key=lambda item: (float(item["center_y_px"]), float(item["center_x_px"])))


def _stable_elongated_rows(
    detections: Mapping[float, list[dict[str, Any]]],
    thresholds: Sequence[float],
    settings: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Keep dark elongated objects that persist across contrast thresholds."""

    if not thresholds:
        return []
    base_threshold = float(thresholds[0])
    base_rows = list(detections.get(base_threshold, []))
    stable: list[dict[str, Any]] = []
    for base in base_rows:
        matched: list[dict[str, Any]] = []
        base_x = float(base["center_x_px"])
        base_y = float(base["center_y_px"])
        base_feret = float(base["max_feret_px"])
        for threshold in thresholds[1:]:
            candidates = detections.get(float(threshold), [])
            if not candidates:
                break
            candidate = min(
                candidates,
                key=lambda item: (base_x - float(item["center_x_px"])) ** 2
                + (base_y - float(item["center_y_px"])) ** 2,
            )
            distance = hypot(base_x - float(candidate["center_x_px"]), base_y - float(candidate["center_y_px"]))
            if distance > max(12.0, 0.30 * base_feret):
                break
            matched.append(candidate)
        if len(matched) != max(0, len(thresholds) - 1):
            continue
        ferets = np.asarray(
            [base_feret, *(float(item["max_feret_px"]) for item in matched)],
            dtype=float,
        )
        spread = float((ferets.max() - ferets.min()) / max(float(np.median(ferets)), 1e-9))
        if spread > float(settings.get("max_elongated_threshold_relative_spread", 0.40)):
            continue
        base["threshold_relative_spread"] = spread
        stable.append(base)
    return sorted(stable, key=lambda item: (float(item["center_y_px"]), float(item["center_x_px"])))


def _merge_rows(
    bright_rows: Sequence[Mapping[str, Any]],
    dark_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Add dark detections without replacing the established bright rows."""

    merged = [dict(row) for row in bright_rows]
    for dark in dark_rows:
        dark_x = float(dark.get("center_x_px", 0.0))
        dark_y = float(dark.get("center_y_px", 0.0))
        dark_feret = float(dark.get("max_feret_px", 0.0))
        duplicate = False
        for bright in merged:
            bright_x = float(bright.get("center_x_px", 0.0))
            bright_y = float(bright.get("center_y_px", 0.0))
            bright_feret = float(bright.get("max_feret_px", 0.0))
            distance = hypot(dark_x - bright_x, dark_y - bright_y)
            if distance <= max(12.0, 0.35 * min(max(dark_feret, 1.0), max(bright_feret, 1.0))):
                duplicate = True
                break
        if not duplicate:
            merged.append(dict(dark))
    return sorted(merged, key=lambda item: (float(item.get("center_y_px", 0.0)), float(item.get("center_x_px", 0.0))))


def _serialise_row(row: Mapping[str, Any], identifier: int, um_per_px: float) -> dict[str, Any]:
    contour = np.asarray(row.get("contour"), dtype=np.int32).reshape(-1, 2)
    equivalent_px = float(row.get("equivalent_diameter_px", 0.0))
    max_feret_px = float(row.get("max_feret_px", 0.0))
    result = {
        "id": int(identifier),
        "center_x_px": float(row.get("center_x_px", 0.0)),
        "center_y_px": float(row.get("center_y_px", 0.0)),
        "contour_px": [[int(point[0]), int(point[1])] for point in contour],
        "feret_start_px": [int(value) for value in row.get("feret_start_px", (0, 0))],
        "feret_end_px": [int(value) for value in row.get("feret_end_px", (0, 0))],
        "area_px": float(row.get("area_px", 0.0)),
        "area_um2": float(row.get("area_px", 0.0)) * um_per_px**2,
        "circularity": float(row.get("circularity", 0.0)),
        "aspect_ratio": float(row.get("aspect_ratio", 0.0)),
        "solidity": float(row.get("solidity", 0.0)),
        "mean_intensity": float(row.get("mean_intensity", 0.0)),
        "contrast_mean": float(row.get("contrast_mean", 0.0)),
        "equivalent_diameter_px": equivalent_px,
        "equivalent_diameter_um": float(equivalent_px * um_per_px),
        "max_feret_px": max_feret_px,
        "max_feret_um": float(max_feret_px * um_per_px),
        "min_feret_px": float(row.get("min_feret_px", 0.0)),
        "min_feret_um": float(row.get("min_feret_px", 0.0)) * um_per_px,
        "shape_class": str(row.get("shape_class", "round")),
        "detection_source": str(row.get("detection_source", "bright_residual")),
        "threshold_relative_spread": float(row.get("threshold_relative_spread", 0.0)),
    }
    if result['shape_class']=='elongated':
        from .cell_width import contour_width_metrics
        try:result.update(contour_width_metrics(result['contour_px'],um_per_px,um_per_px))
        except ValueError:result.update(max_width_px=None,max_width_um=None,width_method='requires_contour_review')
    return result


def analyze_bright_round_cells(
    rgb: np.ndarray,
    um_per_px: float,
    settings: Mapping[str, Any] | None = None,
    roi_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """Measure stable, bright objects and classify them as round or elongated."""

    calibration = _positive_float(um_per_px, 0.0)
    if calibration <= 0:
        raise ValueError("Bright round-cell measurement requires a positive um_per_px calibration.")
    image = _as_rgb_uint8(rgb)
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    effective_settings = _normalise_settings(settings)

    if roi_mask is None:
        allowed_mask = np.ones((height, width), dtype=bool)
    else:
        mask = np.asarray(roi_mask)
        if mask.shape != (height, width):
            raise ValueError("ROI mask must have the same height and width as the RGB image.")
        allowed_mask = mask.astype(bool, copy=True)

    excluded_regions: list[dict[str, int]] = []
    if bool(effective_settings.get("exclude_scale_bar", True)):
        scale_bar_region = _detect_scale_bar_region(gray)
        if scale_bar_region is not None:
            excluded_regions.append(scale_bar_region)
            x = scale_bar_region["x"]
            y = scale_bar_region["y"]
            w = scale_bar_region["w"]
            h = scale_bar_region["h"]
            allowed_mask[max(0, y) : min(height, y + h), max(0, x) : min(width, x + w)] = False

    sigma = max(0.1, float(effective_settings.get("background_sigma", 12.0)))
    background = cv2.GaussianBlur(gray, (0, 0), sigma)
    residual = cv2.subtract(gray, background)
    base_threshold = float(effective_settings.get("residual_threshold", 18.0))
    thresholds = [base_threshold + float(offset) for offset in effective_settings["threshold_offsets"]]
    detections = {
        float(threshold): _candidate_rows(
            gray,
            residual,
            threshold,
            effective_settings,
            allowed_mask,
            calibration,
        )
        for threshold in thresholds
    }
    bright_rows = _stable_rows(detections, thresholds, effective_settings)

    elongated_kernel_size = int(effective_settings.get("elongated_blackhat_kernel", 15))
    elongated_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (elongated_kernel_size, elongated_kernel_size),
    )
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, elongated_kernel)
    elongated_base_threshold = float(effective_settings.get("elongated_contrast_threshold", 16.0))
    elongated_thresholds = [
        elongated_base_threshold + float(offset)
        for offset in effective_settings["elongated_threshold_offsets"]
    ]
    elongated_detections = {
        float(threshold): _elongated_candidate_rows(
            gray,
            blackhat,
            threshold,
            effective_settings,
            allowed_mask,
        )
        for threshold in elongated_thresholds
    }
    dark_rows = _stable_elongated_rows(elongated_detections, elongated_thresholds, effective_settings)
    rows = _merge_rows(bright_rows, dark_rows)
    serialised = [_serialise_row(row, index, calibration) for index, row in enumerate(rows, start=1)]
    for row in serialised:
        points = np.asarray(row["contour_px"])
        margin = float(effective_settings["border_margin_px"])
        truncated = bool(
            np.any(points[:, 0] <= margin) or np.any(points[:, 1] <= margin)
            or np.any(points[:, 0] >= width - 1 - margin)
            or np.any(points[:, 1] >= height - 1 - margin)
        )
        row["border_truncated"] = truncated
        row["include_in_statistics"] = not truncated
    measured = [row for row in serialised if row["include_in_statistics"]]
    diameters = [float(row["equivalent_diameter_um"]) for row in measured]
    round_count = sum(row.get("shape_class") == "round" for row in serialised)
    elongated_count = sum(row.get("shape_class") == "elongated" for row in serialised)
    summary = {
        "count": len(serialised),
        "round_count": int(round_count),
        "elongated_count": int(elongated_count),
        "border_truncated_count": len(serialised) - len(measured),
        "complete_elongated_count": sum(row["shape_class"] == "elongated" for row in measured),
        "dark_elongated_count": int(
            sum(row.get("detection_source") == "dark_background" for row in serialised)
        ),
        "calibration_um_per_px": float(calibration),
        "mean_equivalent_diameter_um": float(np.mean(diameters)) if diameters else 0.0,
        "median_equivalent_diameter_um": float(np.median(diameters)) if diameters else 0.0,
        "min_equivalent_diameter_um": float(min(diameters)) if diameters else 0.0,
        "max_equivalent_diameter_um": float(max(diameters)) if diameters else 0.0,
        "median_threshold_relative_spread": float(
            np.median([float(row["threshold_relative_spread"]) for row in serialised])
        )
        if serialised
        else 0.0,
        "candidate_counts_by_threshold": {
            str(float(threshold)): len(detections[float(threshold)]) for threshold in thresholds
        },
        "elongated_candidate_counts_by_threshold": {
            str(float(threshold)): len(elongated_detections[float(threshold)])
            for threshold in elongated_thresholds
        },
    }
    from .cell_width import update_cell_size_summary
    update_cell_size_summary(summary,serialised)
    return {
        "analysis_version": BRIGHT_ROUND_CELL_ANALYSIS_VERSION,
        "um_per_px": float(calibration),
        "settings": effective_settings,
        "results": serialised,
        "summary": summary,
        "excluded_regions": excluded_regions,
        "image_size_px": [int(width), int(height)],
    }


def render_bright_round_cell_overlay(rgb: np.ndarray, analysis: Mapping[str, Any]) -> np.ndarray:
    """Draw measured contours and diameters over an RGB image."""

    image = _as_rgb_uint8(rgb)
    rows = list(analysis.get("results", [])) if isinstance(analysis, Mapping) else []
    um_per_px = _positive_float(dict(analysis).get("um_per_px"), 0.0) if isinstance(analysis, Mapping) else 0.0
    overlay = image.copy()
    for row in rows:
        paths = row.get('review_contours_px') or [row.get('contour_px', [])]
        for points in paths:
            contour_points = np.asarray(points, dtype=np.int32).reshape(-1, 1, 2)
            if len(contour_points) < 2:
                continue
            contour_color = (57, 255, 20) if str(row.get("shape_class", "round")) == "round" else (255, 150, 30)
            if row.get("border_truncated"):
                contour_color = (80, 170, 255)
            cv2.drawContours(overlay, [contour_points], -1, contour_color, 2, cv2.LINE_AA)
        start = tuple(int(value) for value in row.get("feret_start_px", (0, 0)))
        end = tuple(int(value) for value in row.get("feret_end_px", (0, 0)))
        if 'max_width_px' not in row:
            cv2.line(overlay, start, end, (0, 215, 255), 1, cv2.LINE_AA)
        x = int(round(float(row.get("center_x_px", 0.0))))
        y = int(round(float(row.get("center_y_px", 0.0))))
        diameter = row.get("equivalent_diameter_um")
        unit = 'um'
        if diameter is None and um_per_px > 0:
            diameter = float(row.get("equivalent_diameter_px", 0.0)) * um_per_px
        elif diameter is None:
            diameter = float(row.get('equivalent_diameter_px', 0.0))
            unit = 'px'
        label = f"{int(row.get('id', 0))}: {float(diameter):.1f} {unit}"
        if row.get("shape_class") == "elongated" and row.get('max_feret_um') is not None and row.get('min_feret_um') is not None:
            label = f"{int(row['id'])}: L {row['max_feret_um']:.1f} W {row['min_feret_um']:.1f} um"
        if row.get('max_width_px') is not None:
            start=tuple(int(round(v)) for v in row['width_start_px'])
            end=tuple(int(round(v)) for v in row['width_end_px'])
            cv2.line(overlay,start,end,(0,240,255),2,cv2.LINE_AA)
            width=row.get('max_width_um')
            label=f"{int(row['id'])}: Wmax {width:.1f} um" if width is not None else f"{int(row['id'])}: Wmax {row['max_width_px']:.1f} px"
        elif 'max_width_px' in row:
            label=f"{int(row['id'])}: width needs review"
        centerline=np.asarray(row.get('centerline_points_px',[]),dtype=float).reshape(-1,2)
        if len(centerline)>1:
            cv2.polylines(overlay,[np.rint(centerline).astype(np.int32)],False,(255,70,220),1,cv2.LINE_AA)
            length=row.get('centerline_length_um')
            pixel_length=row.get('centerline_length_px')
            if length is not None:label+=f" / L {length:.1f} um"
            elif pixel_length is not None:label+=f" / L {pixel_length:.1f} px"
        if row.get("border_truncated"):
            label = f"{int(row['id'])}: EDGE / incomplete"
        elif row.get('geometry_review_required'):
            label = f"{int(row['id'])}: BRANCH / review"
        origin = (x + 8, y - 8)
        text_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)[0][0]
        origin = (max(0, min(origin[0], image.shape[1] - text_width - 4)), max(14, origin[1]))
        cv2.putText(overlay, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(overlay, label, origin, cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 255, 255), 1, cv2.LINE_AA)
    return overlay


__all__ = [
    "BRIGHT_ROUND_CELL_ANALYSIS_VERSION",
    "DEFAULT_BRIGHT_ROUND_CELL_SETTINGS",
    "analyze_bright_round_cells",
    "render_bright_round_cell_overlay",
]
