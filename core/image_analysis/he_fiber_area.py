from __future__ import annotations

import inspect
import math
from typing import Any

import numpy as np


HE_FIBER_AREA_ANALYSIS_VERSION = "he_fiber_area_v1"
HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY = 50
HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY = 25
HE_FIBER_FOREGROUND_PRESETS = (35, 50, 65)
HE_FIBER_APPROVED_SEGMENTATION_PATH = "approved_auto"
HE_FIBER_SAMPLE_PROFILE_SEGMENTATION_PATH = "sample_profile"
HE_FIBER_METHOD_ORIGINAL = "original"
HE_FIBER_METHOD_ANTI_OVERSEGMENTATION = "anti_oversegmentation"
HE_FIBER_METHOD_CELLPOSE = "cellpose_cyto3"
HE_FIBER_METHOD_BOUNDARY = "cellpose_cyto3_he_fiber_boundary_v1"
HE_FIBER_CELLPOSE_MODELS = {
    HE_FIBER_METHOD_CELLPOSE: 'cyto3',
    HE_FIBER_METHOD_BOUNDARY: 'cyto3_he_fiber_boundary_v1',
}
HE_FIBER_DEFAULT_METHOD = HE_FIBER_METHOD_ORIGINAL
HE_FIBER_ORIGINAL_DEFAULT_MAX_AREA_PX = 12000


def build_auto_he_fiber_calibration(rgb: np.ndarray) -> dict[str, Any]:
    features = _he_feature_stack(rgb)
    positive, negative = _build_auto_calibration_masks(rgb)
    return build_he_fiber_calibration_from_masks(rgb, positive, negative, feature_stack=features)


def build_he_fiber_calibration_from_masks(
    rgb: np.ndarray,
    positive_mask: np.ndarray,
    negative_mask: np.ndarray,
    *,
    feature_stack: np.ndarray | None = None,
) -> dict[str, Any]:
    features = feature_stack if feature_stack is not None else _he_feature_stack(rgb)
    positive = np.asarray(positive_mask, dtype=bool)
    negative = np.asarray(negative_mask, dtype=bool)
    if positive.shape != features.shape[:2] or negative.shape != features.shape[:2]:
        raise ValueError("H&E fiber calibration masks must match the image shape.")
    if int(np.count_nonzero(positive)) == 0:
        raise ValueError("H&E fiber calibration requires positive fiber examples.")
    if int(np.count_nonzero(negative)) == 0:
        raise ValueError("H&E fiber calibration requires negative background or stroma examples.")
    positive_profile = _robust_feature_profile(features, positive)
    negative_profile = _robust_feature_profile(features, negative)
    positive_dist = _profile_distance(features, positive_profile)
    positive_radius = max(2.25, float(np.percentile(positive_dist[positive], 95)) + 0.85)
    return {
        "version": HE_FIBER_AREA_ANALYSIS_VERSION,
        "positive_profile": positive_profile,
        "negative_profile": negative_profile,
        "positive_radius": float(positive_radius),
        "positive_example_px": int(np.count_nonzero(positive)),
        "negative_example_px": int(np.count_nonzero(negative)),
    }


def clamp_he_fiber_sensitivity(value: Any, default: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = int(default)
    return max(0, min(100, number))


def normalize_he_fiber_segmentation_method(value: Any) -> str:
    text = str(value or HE_FIBER_DEFAULT_METHOD).strip().lower()
    if text in HE_FIBER_CELLPOSE_MODELS:
        return text
    if text in {HE_FIBER_METHOD_ANTI_OVERSEGMENTATION, "anti", "approved_auto"}:
        return HE_FIBER_METHOD_ANTI_OVERSEGMENTATION
    return HE_FIBER_METHOD_ORIGINAL


def uses_anti_oversegmentation_he_fiber_path(value: Any) -> bool:
    return normalize_he_fiber_segmentation_method(value) == HE_FIBER_METHOD_ANTI_OVERSEGMENTATION


def analyze_he_fiber_area(
    rgb: np.ndarray,
    calibration: dict[str, Any],
    *,
    roi_mask: np.ndarray | None = None,
    minimum_candidate_fraction: float = 0.01,
    marker_min_distance: int = 18,
    min_area_px: int = 80,
    max_area_px: int = HE_FIBER_ORIGINAL_DEFAULT_MAX_AREA_PX,
    foreground_sensitivity: int = HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
    splitting_sensitivity: int = HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
    segmentation_method: str = HE_FIBER_DEFAULT_METHOD,
    preserve_fiber_boundaries: bool = False,
    cellpose_diameter_px: float = 90.,
) -> dict[str, Any]:
    image = _as_rgb_uint8(rgb)
    method = normalize_he_fiber_segmentation_method(segmentation_method)
    if method in HE_FIBER_CELLPOSE_MODELS:
        return _analyze_cellpose_fibers(image, roi_mask, cellpose_diameter_px, min_area_px, max_area_px, method)
    anti_path = uses_anti_oversegmentation_he_fiber_path(method)
    foreground = clamp_he_fiber_sensitivity(foreground_sensitivity, HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY)
    splitting = clamp_he_fiber_sensitivity(splitting_sensitivity, HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY)
    features = _he_feature_stack(image)
    positive_dist = _profile_distance(features, dict(calibration["positive_profile"]))
    negative_dist = _profile_distance(features, dict(calibration["negative_profile"]))
    positive_radius = float(calibration.get("positive_radius", 1.0))
    candidate = (positive_dist <= positive_radius) & (positive_dist + 0.15 < negative_dist)
    roi = None
    if roi_mask is not None:
        roi = np.asarray(roi_mask, dtype=bool)
        if roi.shape != image.shape[:2]:
            raise ValueError(f"roi_mask shape {roi.shape} != image shape {image.shape[:2]}")
        candidate &= roi
        denominator = max(1, int(np.count_nonzero(roi)))
    else:
        denominator = int(candidate.size)
    candidate_fraction = float(np.count_nonzero(candidate)) / float(max(1, denominator))
    applicability_score = min(1.0, candidate_fraction / max(float(minimum_candidate_fraction) * 25.0, 1e-6))
    segmentation_path = HE_FIBER_APPROVED_SEGMENTATION_PATH if anti_path else HE_FIBER_SAMPLE_PROFILE_SEGMENTATION_PATH
    original_max_area = int(max_area_px) if int(max_area_px) > 0 else HE_FIBER_ORIGINAL_DEFAULT_MAX_AREA_PX
    if candidate_fraction < float(minimum_candidate_fraction):
        empty = np.zeros(image.shape[:2], dtype=bool)
        return {
            "status": "calibration_mismatch",
            "requires_recalibration": True,
            "applicability_score": float(applicability_score),
            "candidate_fraction": float(candidate_fraction),
            "he_fiber_mask": empty,
            "he_fiber_labels": np.zeros(image.shape[:2], dtype=np.int32),
            "he_fiber_contours": [],
            "results": _empty_results(applicability_score, candidate_fraction),
            "analysis_metadata": _analysis_metadata(
                calibration,
                marker_min_distance,
                min_area_px,
                0 if anti_path else original_max_area,
                foreground_sensitivity=foreground,
                splitting_sensitivity=splitting,
                segmentation_path=segmentation_path,
                segmentation_method=method,
            ),
            "analysis_warnings": ["Calibration mismatch requires recalibration before H&E Fiber Area measurement."],
        }

    if anti_path:
        score, scale = _prepare_approved_he_fiber_score(image)
        boundary_mask = None
        if preserve_fiber_boundaries:
            from scipy import ndimage as ndi
            red_excess = image[:, :, 0].astype(float) - image[:, :, 2]
            score = ndi.gaussian_filter(red_excess, .65)
            boundary_mask = red_excess > (28.0 - .32 * foreground)
        filtered_labels = _segment_approved_he_fibers(
            score,
            scale,
            foreground_sensitivity=foreground,
            splitting_sensitivity=splitting,
            roi_mask=roi,
            **({'boundary_mask': boundary_mask} if preserve_fiber_boundaries else {}),
        )
        areas, centroids = _areas_and_centroids(filtered_labels)
        rejected = 0
        effective_min_area = int(220 * scale * scale)
        effective_max_area = 0
    else:
        mask = _remove_small_objects(mask=candidate, min_size=20)
        mask = _remove_small_holes(mask=mask, area_threshold=300)
        labels = _watershed_labels(mask, marker_min_distance=marker_min_distance)
        filtered_labels, areas, centroids, rejected = _filter_labels(
            labels,
            min_area_px=max(0, int(min_area_px)),
            max_area_px=original_max_area,
        )
        effective_min_area = max(0, int(min_area_px))
        effective_max_area = original_max_area
        scale = float(image.shape[1]) / 1600.0
    contours = _contours_for_labels(filtered_labels)
    return {
        "status": "active",
        "requires_recalibration": False,
        "applicability_score": float(applicability_score),
        "candidate_fraction": float(candidate_fraction),
        "he_fiber_mask": filtered_labels > 0,
        "he_fiber_labels": filtered_labels,
        "he_fiber_contours": contours,
        "results": _results_from_areas(areas, centroids, applicability_score, candidate_fraction),
        "analysis_metadata": {
            **_analysis_metadata(
                calibration,
                marker_min_distance,
                effective_min_area,
                effective_max_area,
                foreground_sensitivity=foreground,
                splitting_sensitivity=splitting,
                segmentation_path=segmentation_path,
                segmentation_method=method,
                scale=scale,
            ),
            "rejected_shape_or_size_count": int(rejected),
            "preserve_fiber_boundaries": bool(preserve_fiber_boundaries),
        },
        "analysis_warnings": [],
    }


def render_he_fiber_area_overlay(
    rgb: np.ndarray,
    labels: np.ndarray,
    *,
    color: tuple[int, int, int] = (0, 244, 255),
    alpha: float = 0.35,
) -> np.ndarray:
    """Render only included fiber labels with fill and per-fiber boundaries."""
    from skimage import segmentation
    image = _as_rgb_uint8(rgb)
    label_array = np.asarray(labels, dtype=np.int32)
    if label_array.shape != image.shape[:2]:
        raise ValueError("labels shape must match the first two RGB dimensions.")
    if not 0.0 <= float(alpha) <= 1.0:
        raise ValueError("alpha must be between 0 and 1.")

    selected = label_array > 0
    rendered = image.astype(np.float32, copy=True)
    color_array = np.asarray(color, dtype=np.float32)
    if color_array.shape != (3,):
        raise ValueError("color must contain exactly three RGB components.")
    rendered[selected] = (
        (1.0 - float(alpha)) * rendered[selected]
        + float(alpha) * color_array
    )
    boundaries = segmentation.find_boundaries(label_array, mode="thick")
    rendered[boundaries] = color_array
    return np.ascontiguousarray(np.clip(np.rint(rendered), 0, 255).astype(np.uint8))


def _prepare_approved_he_fiber_score(rgb: np.ndarray) -> tuple[np.ndarray, float]:
    from scipy import ndimage as ndi
    image = _as_rgb_uint8(rgb).astype(np.float32)
    scale = float(image.shape[1]) / 1600.0
    red_excess = image[:, :, 0] - image[:, :, 2]
    return ndi.gaussian_filter(red_excess, 3.2 * scale), scale


def _segment_approved_he_fibers(
    score: np.ndarray,
    scale: float,
    *,
    foreground_sensitivity: float,
    splitting_sensitivity: float,
    roi_mask: np.ndarray | None = None,
    boundary_mask: np.ndarray | None = None,
) -> np.ndarray:
    from scipy import ndimage as ndi
    from skimage import morphology
    from skimage import segmentation
    threshold = 28.0 - 0.32 * float(foreground_sensitivity)
    candidate = score > threshold
    if boundary_mask is None:
        candidate = _call_skimage_size_filter(morphology.remove_small_holes, candidate, int(1400 * scale * scale))
    else:
        candidate &= boundary_mask
        components, _ = ndi.label(candidate)
        for identifier, slices in enumerate(ndi.find_objects(components), 1):
            if slices is not None:
                local = components[slices] == identifier
                filled = ndi.binary_fill_holes(local)
                candidate[slices] |= filled & ((components[slices] == 0) | local)
    candidate = _call_skimage_size_filter(morphology.remove_small_objects, candidate, int(160 * scale * scale))
    if roi_mask is not None:
        candidate = candidate & np.asarray(roi_mask, dtype=bool)
    if not np.any(candidate):
        return np.zeros(score.shape, dtype=np.int32)
    distance = ndi.distance_transform_edt(candidate)
    smooth_distance = ndi.gaussian_filter(distance, .7 if boundary_mask is not None else 1.8 * scale)
    prominence = max(0.5, (12.0 - 0.105 * float(splitting_sensitivity)) * scale)
    maxima = morphology.h_maxima(smooth_distance, prominence) & candidate
    markers, _count = ndi.label(maxima)
    elevation = -smooth_distance + np.maximum(0.0, 25.0 - score) * 0.12
    labels = np.asarray(segmentation.watershed(elevation, markers, mask=candidate), dtype=np.int32)
    min_area = int(220 * scale * scale)
    sizes = np.bincount(labels.ravel())
    keep = sizes >= min_area
    if keep.size:
        keep[0] = False
    remap = np.zeros(len(sizes), dtype=np.int32)
    kept = int(np.count_nonzero(keep))
    if kept:
        remap[keep] = np.arange(1, kept + 1, dtype=np.int32)
    return remap[labels]


def _call_skimage_size_filter(func, mask: np.ndarray, size: int) -> np.ndarray:
    array = np.asarray(mask, dtype=bool)
    threshold = max(0, int(size))
    parameters = inspect.signature(func).parameters
    kwargs: dict[str, Any] = {}
    if "max_size" in parameters:
        kwargs["max_size"] = threshold
    elif "min_size" in parameters:
        kwargs["min_size"] = threshold
    elif "area_threshold" in parameters:
        kwargs["area_threshold"] = threshold
    else:
        return np.asarray(func(array, threshold), dtype=bool)
    return np.asarray(func(array, **kwargs), dtype=bool)


def _analyze_cellpose_fibers(image, roi_mask, diameter, min_area, max_area, method=HE_FIBER_METHOD_CELLPOSE):
    from .cellpose_backend import run_cellpose_labels
    from skimage.measure import regionprops

    roi = np.ones(image.shape[:2], bool) if roi_mask is None else np.asarray(roi_mask, bool)
    if roi.shape != image.shape[:2] or not roi.any():
        raise ValueError('Cellpose fiber ROI must match the source image and not be empty.')
    model = HE_FIBER_CELLPOSE_MODELS[method]
    labels, metadata = run_cellpose_labels(image, model=model, diameter=diameter, min_size=min_area)
    labels = labels.copy()
    labels[~roi] = 0
    for region in regionprops(labels):
        if region.area < min_area or (max_area > 0 and region.area > max_area):
            labels[labels == region.label] = 0
    areas, centroids = _areas_and_centroids(labels)
    fraction = float(np.count_nonzero(labels)) / int(roi.sum())
    return {
        'status': 'active', 'requires_recalibration': False,
        'applicability_score': None, 'candidate_fraction': fraction,
        'he_fiber_mask': labels > 0, 'he_fiber_labels': labels,
        'he_fiber_contours': _contours_for_labels(labels),
        'results': _results_from_areas(areas, centroids, 0., fraction),
        'analysis_metadata': {'analysis_kind': 'he_fiber_area',
            'analysis_version': HE_FIBER_AREA_ANALYSIS_VERSION,
            'he_fiber_segmentation_method': method,
            'segmentation_path': 'cellpose', 'backend': 'cellpose', 'model': model,
            'cellpose': metadata, 'min_area_px': int(min_area), 'max_area_px': int(max_area),
            'coordinate_space': 'source_pixels', 'colour_calibration_used': False},
        'analysis_warnings': [],
    }


def _areas_and_centroids(labels: np.ndarray) -> tuple[list[int], list[list[float]]]:
    from skimage import measure
    areas: list[int] = []
    centroids: list[list[float]] = []
    for region in measure.regionprops(np.asarray(labels, dtype=np.int32)):
        areas.append(int(region.area))
        cy, cx = region.centroid
        centroids.append([float(cy), float(cx)])
    return areas, centroids


def _as_rgb_uint8(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if array.dtype == np.uint8:
        return array
    if float(np.nanmax(array)) <= 1.0:
        return np.clip(array * 255.0, 0.0, 255.0).astype(np.uint8)
    return np.clip(array, 0.0, 255.0).astype(np.uint8)


def _he_feature_stack(rgb: np.ndarray) -> np.ndarray:
    from skimage import color
    rgb_unit = _as_rgb_uint8(rgb).astype(np.float32) / 255.0
    lab = color.rgb2lab(rgb_unit)
    hsv = color.rgb2hsv(rgb_unit)
    return np.dstack(
        [
            lab[..., 0],
            lab[..., 1],
            lab[..., 2],
            hsv[..., 1] * 100.0,
            hsv[..., 2] * 100.0,
        ]
    ).astype(np.float32)


def _build_auto_calibration_masks(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    from skimage import color
    from skimage import morphology
    rgb_unit = _as_rgb_uint8(rgb).astype(np.float32) / 255.0
    lab = color.rgb2lab(rgb_unit)
    hsv = color.rgb2hsv(rgb_unit)
    score = lab[..., 1] + (hsv[..., 1] * 55.0) - (np.maximum(lab[..., 0] - 88.0, 0.0) * 0.8)
    tissue_like = (lab[..., 0] < 98.0) & (hsv[..., 1] > 0.04)
    positive_threshold = float(np.percentile(score[tissue_like], 82)) if np.any(tissue_like) else float(np.percentile(score, 90))
    positive = tissue_like & (score >= positive_threshold) & (hsv[..., 1] > 0.16)
    positive = morphology.erosion(positive, morphology.disk(2))
    positive = _remove_small_objects(mask=positive, min_size=120)
    background = (lab[..., 0] > 90.0) & (hsv[..., 1] < 0.12)
    pale_stroma = tissue_like & (score <= float(np.percentile(score[tissue_like], 45))) if np.any(tissue_like) else np.zeros(score.shape, dtype=bool)
    negative = (background | pale_stroma) & ~morphology.dilation(positive, morphology.disk(4))
    negative = _remove_small_objects(mask=negative, min_size=120)
    if int(np.count_nonzero(positive)) == 0:
        positive = tissue_like & (score >= float(np.percentile(score, 90)))
    if int(np.count_nonzero(negative)) == 0:
        negative = ~positive
    return np.asarray(positive, dtype=bool), np.asarray(negative, dtype=bool)


def _robust_feature_profile(features: np.ndarray, mask: np.ndarray) -> dict[str, Any]:
    values = np.asarray(features[np.asarray(mask, dtype=bool)], dtype=np.float32)
    center = np.median(values, axis=0)
    q25 = np.percentile(values, 25, axis=0)
    q75 = np.percentile(values, 75, axis=0)
    scale = np.maximum((q75 - q25) / 1.349, 1.0)
    return {
        "center": [float(item) for item in center.tolist()],
        "scale": [float(item) for item in scale.tolist()],
    }


def _profile_distance(features: np.ndarray, profile: dict[str, Any]) -> np.ndarray:
    center = np.asarray(profile["center"], dtype=np.float32)
    scale = np.asarray(profile["scale"], dtype=np.float32)
    normalized = (np.asarray(features, dtype=np.float32) - center) / np.maximum(scale, 1e-6)
    return np.sqrt(np.mean(normalized * normalized, axis=2))


def _remove_small_objects(*, mask: np.ndarray, min_size: int) -> np.ndarray:
    from skimage import morphology
    threshold = max(0, int(min_size) - 1)
    return morphology.remove_small_objects(np.asarray(mask, dtype=bool), max_size=threshold)


def _remove_small_holes(*, mask: np.ndarray, area_threshold: int) -> np.ndarray:
    from skimage import morphology
    threshold = max(0, int(area_threshold) - 1)
    return morphology.remove_small_holes(np.asarray(mask, dtype=bool), max_size=threshold)


def _watershed_labels(mask: np.ndarray, *, marker_min_distance: int) -> np.ndarray:
    from scipy import ndimage as ndi
    from skimage import feature
    from skimage import measure
    from skimage import segmentation
    candidate = _remove_small_objects(mask=np.asarray(mask, dtype=bool), min_size=80)
    candidate = _remove_small_holes(mask=candidate, area_threshold=80)
    if not np.any(candidate):
        return np.zeros(candidate.shape, dtype=np.int32)
    distance = ndi.distance_transform_edt(candidate)
    coords = feature.peak_local_max(
        distance,
        min_distance=int(marker_min_distance),
        threshold_abs=6.0,
        labels=candidate,
    )
    markers = np.zeros(candidate.shape, dtype=np.int32)
    for idx, (row, col) in enumerate(coords, start=1):
        markers[row, col] = idx
    markers = ndi.label(markers > 0)[0]
    if int(np.max(markers)) == 0:
        markers = measure.label(candidate)
    return segmentation.watershed(-distance, markers, mask=candidate)


def _filter_labels(
    labels: np.ndarray,
    *,
    min_area_px: int,
    max_area_px: int,
) -> tuple[np.ndarray, list[int], list[list[float]], int]:
    from skimage import measure
    out = np.zeros_like(labels, dtype=np.int32)
    areas: list[int] = []
    centroids: list[list[float]] = []
    rejected = 0
    next_label = 1
    for region in measure.regionprops(labels):
        area = int(region.area)
        if area < int(min_area_px) or (int(max_area_px) > 0 and area > int(max_area_px)):
            rejected += 1
            continue
        perimeter = max(float(region.perimeter), 1.0)
        compactness = float((4.0 * math.pi * area) / (perimeter * perimeter))
        if compactness < 0.06:
            rejected += 1
            continue
        out[labels == region.label] = next_label
        areas.append(area)
        cy, cx = region.centroid
        centroids.append([float(cy), float(cx)])
        next_label += 1
    return out, areas, centroids, rejected


def _contours_for_labels(labels: np.ndarray) -> list[dict[str, Any]]:
    from skimage import measure
    contours: list[dict[str, Any]] = []
    for region in measure.regionprops(labels):
        region_mask = labels == region.label
        paths = measure.find_contours(region_mask.astype(np.uint8), 0.5)
        if not paths:
            continue
        path = max(paths, key=lambda item: item.shape[0])
        contours.append(
            {
                "label": int(region.label),
                "points": [[float(row), float(col)] for row, col in path.tolist()],
            }
        )
    return contours


def _empty_results(applicability_score: float, candidate_fraction: float) -> dict[str, Any]:
    return {
        "he_fiber_count": 0,
        "he_fiber_areas_px": [],
        "he_fiber_centroids": [],
        "he_fiber_total_area_px": 0,
        "he_fiber_mean_area_px": 0.0,
        "he_fiber_median_area_px": 0.0,
        "he_fiber_min_area_px": 0,
        "he_fiber_max_area_px": 0,
        "applicability_score": float(applicability_score),
        "candidate_fraction": float(candidate_fraction),
    }


def _results_from_areas(
    areas: list[int],
    centroids: list[list[float]],
    applicability_score: float,
    candidate_fraction: float,
) -> dict[str, Any]:
    if not areas:
        return _empty_results(applicability_score, candidate_fraction)
    values = np.asarray(areas, dtype=np.float64)
    return {
        "he_fiber_count": int(values.size),
        "he_fiber_areas_px": [int(item) for item in areas],
        "he_fiber_centroids": [[float(cy), float(cx)] for cy, cx in centroids],
        "he_fiber_total_area_px": int(np.sum(values)),
        "he_fiber_mean_area_px": float(np.mean(values)),
        "he_fiber_median_area_px": float(np.median(values)),
        "he_fiber_min_area_px": int(np.min(values)),
        "he_fiber_max_area_px": int(np.max(values)),
        "applicability_score": float(applicability_score),
        "candidate_fraction": float(candidate_fraction),
    }


def _analysis_metadata(
    calibration: dict[str, Any],
    marker_min_distance: int,
    min_area_px: int,
    max_area_px: int,
    *,
    foreground_sensitivity: int = HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
    splitting_sensitivity: int = HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
    segmentation_path: str = HE_FIBER_SAMPLE_PROFILE_SEGMENTATION_PATH,
    segmentation_method: str = HE_FIBER_DEFAULT_METHOD,
    scale: float | None = None,
) -> dict[str, Any]:
    payload = {
        "analysis_kind": "he_fiber_area",
        "analysis_version": HE_FIBER_AREA_ANALYSIS_VERSION,
        "calibration_version": str(calibration.get("version", "")),
        "calibration_mode": str(calibration.get("calibration_mode", "auto") or "auto"),
        "positive_example_px": int(calibration.get("positive_example_px", 0) or 0),
        "negative_example_px": int(calibration.get("negative_example_px", 0) or 0),
        "marker_min_distance": int(marker_min_distance),
        "min_area_px": int(min_area_px),
        "max_area_px": int(max_area_px),
        "he_fiber_foreground_sensitivity": int(foreground_sensitivity),
        "he_fiber_splitting_sensitivity": int(splitting_sensitivity),
        "he_fiber_segmentation_method": normalize_he_fiber_segmentation_method(segmentation_method),
        "segmentation_path": str(segmentation_path),
    }
    if scale is not None:
        payload["approved_scale"] = float(scale)
    return payload
