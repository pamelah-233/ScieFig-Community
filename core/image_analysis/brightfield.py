from __future__ import annotations

from typing import Any

import numpy as np

from .artifact_engine import detect_artifact_mask
from .auto_params import build_tissue_background_mask
from . import measure_area_fraction, measure_channel_intensity
from .quality_engine import assess_image_quality
from .segmentation_engine import cleanup_segmentation_mask, summarize_connected_components


def rgb_to_grayscale_channel(rgb: np.ndarray) -> np.ndarray:
    from skimage.color import rgb2gray
    return np.asarray(rgb2gray(_as_rgb_unit(rgb)), dtype=np.float64)


def segment_hsv(
    rgb: np.ndarray,
    h_min: float,
    h_max: float,
    s_min: float,
    s_max: float,
    v_min: float,
    v_max: float,
) -> np.ndarray:
    from skimage.color import rgb2hsv
    hsv = rgb2hsv(_as_rgb_unit(rgb))
    hue = hsv[..., 0]
    if float(h_min) <= float(h_max):
        hue_mask = (hue >= float(h_min)) & (hue <= float(h_max))
    else:
        hue_mask = (hue >= float(h_min)) | (hue <= float(h_max))
    return (
        hue_mask
        & (hsv[..., 1] >= float(s_min))
        & (hsv[..., 1] <= float(s_max))
        & (hsv[..., 2] >= float(v_min))
        & (hsv[..., 2] <= float(v_max))
    )


def segment_lab(
    rgb: np.ndarray,
    l_min: float,
    l_max: float,
    a_min: float,
    a_max: float,
    b_min: float,
    b_max: float,
) -> np.ndarray:
    from skimage.color import rgb2lab
    lab = rgb2lab(_as_rgb_unit(rgb))
    return (
        (lab[..., 0] >= float(l_min))
        & (lab[..., 0] <= float(l_max))
        & (lab[..., 1] >= float(a_min))
        & (lab[..., 1] <= float(a_max))
        & (lab[..., 2] >= float(b_min))
        & (lab[..., 2] <= float(b_max))
    )


def segment_grayscale(
    rgb: np.ndarray,
    threshold: float,
    invert: bool = False,
) -> np.ndarray:
    gray = rgb_to_grayscale_channel(rgb)
    threshold_value = float(threshold)
    return gray <= threshold_value if invert else gray >= threshold_value


def filter_labeled_objects(
    mask: np.ndarray,
    min_area_px: int = 0,
    max_area_px: int = 0,
) -> dict[str, Any]:
    return summarize_connected_components(
        mask,
        min_area_px=int(min_area_px),
        max_area_px=int(max_area_px),
    )


def analyze_brightfield_image(
    rgb: np.ndarray,
    settings: dict[str, Any],
    roi_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    quality = assess_image_quality(rgb)
    selection_mask = np.ones(rgb.shape[:2], dtype=bool)
    if roi_mask is not None:
        selection_mask &= np.asarray(roi_mask, dtype=bool)
    tissue_limited_mask = np.array(selection_mask, dtype=bool, copy=True)
    if bool(settings.get("tissue_mask_enabled", False)):
        tissue_mask = build_tissue_background_mask(
            rgb,
            {
                "enabled": True,
                "tissue_mask_value_threshold": settings.get("tissue_mask_value_threshold"),
                "tissue_mask_saturation_threshold": settings.get("tissue_mask_saturation_threshold"),
                "tissue_mask_min_area_px": settings.get("tissue_mask_min_area_px"),
            },
        )
        tissue_limited_mask &= np.asarray(tissue_mask, dtype=bool)
    artifact = detect_artifact_mask(rgb, tissue_mask=tissue_limited_mask)
    artifact_mask = np.asarray(artifact["artifact_mask"], dtype=bool)
    analysis_mask = tissue_limited_mask & ~artifact_mask
    measurement_mask = selection_mask & ~artifact_mask

    segmentation_mode = str(settings.get("segmentation_mode", "hsv")).strip().lower() or "hsv"
    if segmentation_mode == "hsv":
        raw_mask = segment_hsv(
            rgb,
            settings.get("h_min", 0.0),
            settings.get("h_max", 1.0),
            settings.get("s_min", 0.0),
            settings.get("s_max", 1.0),
            settings.get("v_min", 0.0),
            settings.get("v_max", 1.0),
        )
    elif segmentation_mode == "lab":
        raw_mask = segment_lab(
            rgb,
            settings.get("l_min", 0.0),
            settings.get("l_max", 100.0),
            settings.get("a_min", -128.0),
            settings.get("a_max", 127.0),
            settings.get("b_min", -128.0),
            settings.get("b_max", 127.0),
        )
    elif segmentation_mode == "grayscale":
        raw_mask = segment_grayscale(
            rgb,
            settings.get("gray_threshold", 0.5),
            invert=bool(settings.get("gray_invert", False)),
        )
    else:
        raise ValueError(f"Unsupported Brightfield segmentation mode: {segmentation_mode}")

    threshold_mask = np.asarray(raw_mask, dtype=bool) & analysis_mask
    cleaned_mask = cleanup_segmentation_mask(
        raw_mask,
        min_area_px=int(settings.get("cleanup_min_area_px", 0)),
        fill_holes=bool(settings.get("fill_holes", True)),
        morphology_kernel_px=int(settings.get("morphology_kernel_px", 0)),
    )
    selected_mask = cleaned_mask & analysis_mask
    object_summary = filter_labeled_objects(
        selected_mask,
        min_area_px=int(settings.get("min_object_area_px", 0)),
        max_area_px=int(settings.get("max_object_area_px", 0)),
    )
    final_mask = np.asarray(object_summary["mask"], dtype=bool) & analysis_mask

    area = measure_area_fraction(final_mask, roi_mask=measurement_mask)
    grayscale = rgb_to_grayscale_channel(rgb)
    roi_intensity = measure_channel_intensity(grayscale, roi_mask=measurement_mask)
    masked_intensity = measure_channel_intensity(grayscale, mask=final_mask, roi_mask=measurement_mask)

    return {
        "mask": final_mask,
        "grayscale": grayscale,
        "analysis_mask": analysis_mask,
        "artifact_mask": artifact_mask,
        "quality_summary": quality,
        "artifact_summary": artifact,
        "results": {
            "segmentation_mode": segmentation_mode,
            "stained_area_px": int(area["positive_area_px"]),
            "roi_area_px": int(area["total_area_px"]),
            "area_fraction": float(area["positive_area_fraction"]),
            "area_fraction_percent": float(area["positive_area_percent"]),
            "object_count": int(object_summary["object_count"]),
            "total_object_area_px": int(object_summary["total_object_area_px"]),
            "mean_object_area_px": float(object_summary["mean_object_area_px"]),
            "roi_mean_intensity": float(roi_intensity["mean_intensity"]),
            "roi_integrated_intensity": float(roi_intensity["integrated_density"]),
            "masked_mean_intensity": float(masked_intensity["mean_intensity"]),
            "masked_integrated_intensity": float(masked_intensity["integrated_density"]),
        },
        "analysis_metadata": {
            "tissue_mask_applied": bool(settings.get("tissue_mask_enabled", False)),
            "target_mask_area_px_before_object_filter": int(np.count_nonzero(threshold_mask)),
            "final_mask_area_px": int(np.count_nonzero(final_mask)),
            "quality_summary": {
                key: value
                for key, value in quality.items()
                if key not in {"tissue_mask", "background_mask"}
            },
            "artifact_summary": {
                key: value
                for key, value in artifact.items()
                if key != "artifact_mask"
            },
        },
        "analysis_warnings": list(quality.get("warnings", [])) + list(artifact.get("warnings", [])),
    }


def _as_rgb_unit(rgb: np.ndarray) -> np.ndarray:
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    if np.max(array) <= 1.0:
        return np.clip(array, 0.0, 1.0)
    return np.clip(array, 0.0, 255.0) / 255.0


def analyze_brightfield_ai_object_count(
    rgb: np.ndarray,
    settings: dict[str, Any],
    roi_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    """AI-assisted object count, independent of analyze_brightfield_image's pipeline."""
    from .brightfield_ai_backend import BrightfieldAIBackend, get_ai_object_instances

    rgb = np.asarray(rgb)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("rgb must have shape (H, W, 3).")
    manifest_path = str(settings.get("ai_manifest_path", "config/ai_pack/manifest.json"))
    cache_root = settings.get("ai_cache_root", None)

    rgb_for_ai = rgb.copy()
    if roi_mask is not None:
        bool_roi = np.asarray(roi_mask, dtype=bool)
        if bool_roi.shape != rgb.shape[:2]:
            raise ValueError(f"roi_mask shape {bool_roi.shape} != image shape {rgb.shape[:2]}")
        rgb_for_ai[~bool_roi] = 0

    instances = get_ai_object_instances(
        rgb_for_ai,
        backend=BrightfieldAIBackend.CELLPOSE_CYTO3,
        manifest_path=manifest_path,
        cache_root=cache_root,
    )

    return {
        "ai_object_mask": instances.label_mask_orig,
        "ai_object_count": int(instances.object_count),
        "ai_object_areas_px": list(instances.object_areas_px),
        "ai_object_centroids": list(instances.object_centroids),
        "results": {
            "ai_object_count": int(instances.object_count),
            "ai_object_total_area_px": int(sum(instances.object_areas_px)),
            "ai_object_mean_area_px": float(sum(instances.object_areas_px)) / max(1, instances.object_count),
            "backend": instances.backend.value,
        },
        "analysis_metadata": {
            "analysis_kind": "brightfield_ai_object_count",
            "ai_backend": instances.backend.value,
            "roi_applied_pre_inference": roi_mask is not None,
            "roi_mask_post_clipped": False,
            **instances.backend_metadata,
        },
        "analysis_warnings": list(instances.warnings),
    }
