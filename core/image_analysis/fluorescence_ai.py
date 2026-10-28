"""GUI-free fluorescence AI-assisted object detection adapter.

V8.1-A intentionally implements an AI Pack contract and mockable adapter layer,
not a real fluorescence-trained model. Default execution never fabricates
detections when an explicit model boundary is absent.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from core.utils.hashing import sha256_json

FLUORESCENCE_AI_ANALYSIS_VERSION = "v8_1_ai_fluorescence"
FLUORESCENCE_AI_MODE = "ai_assisted"
FLUORESCENCE_AI_FAMILY = "fluorescence"
FLUORESCENCE_AI_OBJECT_SEMANTICS = "ai_detected_fluorescent_objects"
AI_ASSISTED_UNAVAILABLE_WARNING = "ai_model_unavailable_no_fabricated_detections"
LOCAL_HEURISTIC_EXECUTION_MODE = "local_heuristic"
LOCAL_HEURISTIC_MODEL_ID = "fluorescence_membrane_local_heuristic"
LOCAL_HEURISTIC_LICENSE_SUMMARY = "local heuristic segmentation; no external model artifact executed"
CELLPOSE_FLUORESCENCE_MODELS = {
    'cellpose_cyto3': 'cyto3',
    'cellpose_cyto3_laminin_final_v1': 'cyto3_laminin_final_v1',
}


@dataclass(slots=True, frozen=True)
class FluorescenceAIAssistedSettings:
    backend: str = 'local_heuristic'
    cellpose_diameter_px: float = 40.
    model_id: str = "cellpose_cyto3"
    model_version: str = "0.1.0"
    ai_pack_postprocess: str = "cellpose_flow_v1_minimal"
    detection_sensitivity: float = 1.0
    min_object_area_px: int = 5
    max_object_area_px: int = 0
    object_count_semantics: str = FLUORESCENCE_AI_OBJECT_SEMANTICS
    allow_restricted_model: bool = False
    analysis_version: str = FLUORESCENCE_AI_ANALYSIS_VERSION


@dataclass(slots=True)
class FluorescenceAIAssistedObject:
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
class FluorescenceAIAssistedChannelResult:
    channel_index: int
    objects: list[FluorescenceAIAssistedObject] = field(default_factory=list)
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
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }
        if include_label_mask and self.label_mask is not None:
            payload["label_mask"] = np.asarray(self.label_mask, dtype=np.int32)
        return payload


@dataclass(slots=True)
class FluorescenceAIAssistedResult:
    channel_results: dict[int, FluorescenceAIAssistedChannelResult]
    metadata: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def object_count(self) -> int:
        return int(sum(channel.object_count for channel in self.channel_results.values()))

    @property
    def is_available(self) -> bool:
        return str(self.metadata.get("model_execution_mode", "")) != "unavailable"

    @property
    def is_mock(self) -> bool:
        return str(self.metadata.get("model_execution_mode", "")) == "mock"

    @property
    def is_runtime_available(self) -> bool:
        return str(self.metadata.get("model_execution_mode", "")) == "runtime"

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


@dataclass(slots=True)
class FluorescenceAIModelBoundary:
    model_execution_mode: str = "unavailable"
    backend_available: bool = False
    model_available: bool = False
    model_commercial_use_status: str = "restricted"
    model_license_summary: str = ""
    mock_label_masks: dict[int, np.ndarray] | None = None
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def mock(
        cls,
        label_masks: dict[int, np.ndarray],
        *,
        model_commercial_use_status: str = "restricted",
        model_license_summary: str = "mock boundary; no real model artifact executed",
    ) -> "FluorescenceAIModelBoundary":
        return cls(
            model_execution_mode="mock",
            backend_available=True,
            model_available=True,
            model_commercial_use_status=model_commercial_use_status,
            model_license_summary=model_license_summary,
            mock_label_masks={int(key): np.asarray(value, dtype=np.int32) for key, value in label_masks.items()},
        )


def analyze_fluorescence_ai_assisted(
    image: np.ndarray,
    *,
    channels: list[int] | None,
    settings: FluorescenceAIAssistedSettings,
    roi_mask: np.ndarray | None = None,
    model_boundary: FluorescenceAIModelBoundary | None = None,
) -> FluorescenceAIAssistedResult:
    image_cyx = _as_cyx_array(image)
    channel_indices = _resolve_channels(channels, n_channels=image_cyx.shape[0])
    roi = _resolve_roi_mask(roi_mask, image_shape=image_cyx.shape[1:])
    normalized_settings = _normalize_settings(settings)
    effective_settings_hash = _effective_settings_hash(normalized_settings)
    boundary = model_boundary or FluorescenceAIModelBoundary()
    mode, availability_warnings = _resolve_execution_mode(boundary, normalized_settings)

    channel_results: dict[int, FluorescenceAIAssistedChannelResult] = {}
    warnings = list(availability_warnings)
    for channel_index in channel_indices:
        if mode == "mock":
            label_mask = _mock_label_mask_for_channel(boundary, int(channel_index), image_shape=image_cyx.shape[1:])
        else:
            label_mask = np.zeros(image_cyx.shape[1:], dtype=np.int32)
        objects, filtered_mask = _measure_ai_objects(
            label_mask,
            intensity_image=image_cyx[int(channel_index)],
            channel_index=int(channel_index),
            roi_mask=roi,
            min_area_px=max(1, int(normalized_settings.min_object_area_px)),
            max_area_px=max(0, int(normalized_settings.max_object_area_px)),
        )
        channel_metadata = _base_metadata(
            settings=normalized_settings,
            boundary=boundary,
            model_execution_mode=mode,
            roi_used=roi is not None,
            effective_settings_hash=effective_settings_hash,
            warnings=warnings,
        )
        channel_results[int(channel_index)] = FluorescenceAIAssistedChannelResult(
            channel_index=int(channel_index),
            objects=objects,
            label_mask=filtered_mask,
            warnings=list(warnings),
            metadata=channel_metadata,
        )

    deduped_warnings = _dedupe_preserve_order(warnings)
    metadata = _base_metadata(
        settings=normalized_settings,
        boundary=boundary,
        model_execution_mode=mode,
        roi_used=roi is not None,
        effective_settings_hash=effective_settings_hash,
        warnings=deduped_warnings,
    )
    return FluorescenceAIAssistedResult(
        channel_results=channel_results,
        metadata=metadata,
        warnings=deduped_warnings,
    )


def analyze_fluorescence_ai_assisted_local_heuristic(
    image: np.ndarray,
    *,
    channels: list[int] | None,
    settings: FluorescenceAIAssistedSettings,
    roi_mask: np.ndarray | None = None,
) -> FluorescenceAIAssistedResult:
    image_cyx = _as_cyx_array(image)
    channel_indices = _resolve_channels(channels, n_channels=image_cyx.shape[0])
    roi = _resolve_roi_mask(roi_mask, image_shape=image_cyx.shape[1:])
    normalized_settings = _normalize_settings(settings)
    effective_settings_hash = _effective_settings_hash(normalized_settings)

    channel_results: dict[int, FluorescenceAIAssistedChannelResult] = {}
    warnings: list[str] = []
    for channel_index in channel_indices:
        objects, filtered_mask, path_kind = _measure_local_heuristic_objects(
            image_cyx[int(channel_index)],
            channel_index=int(channel_index),
            roi_mask=roi,
            min_area_px=max(1, int(normalized_settings.min_object_area_px)),
            max_area_px=max(0, int(normalized_settings.max_object_area_px)),
            detection_sensitivity=float(normalized_settings.detection_sensitivity),
        )
        channel_metadata = {
            "analysis_mode": FLUORESCENCE_AI_MODE,
            "analysis_family": FLUORESCENCE_AI_FAMILY,
            "analysis_version": normalized_settings.analysis_version,
            "model_id": LOCAL_HEURISTIC_MODEL_ID,
            "model_version": normalized_settings.model_version,
            "ai_pack_postprocess": "local_membrane_region_postprocess",
            "model_commercial_use_status": "not_applicable",
            "model_license_summary": LOCAL_HEURISTIC_LICENSE_SUMMARY,
            "ai_backend_available": True,
            "model_available": True,
            "model_execution_mode": LOCAL_HEURISTIC_EXECUTION_MODE,
            "object_count_semantics": normalized_settings.object_count_semantics,
            "intensity_units": "raw_image_units",
            "roi_used": bool(roi is not None),
            "warnings": [],
            "effective_settings_hash": effective_settings_hash,
            "effective_settings_fields": sorted(asdict(normalized_settings).keys()),
            "local_detection_path": path_kind,
        }
        channel_results[int(channel_index)] = FluorescenceAIAssistedChannelResult(
            channel_index=int(channel_index),
            objects=objects,
            label_mask=filtered_mask,
            warnings=[],
            metadata=channel_metadata,
        )

    metadata = {
        "analysis_mode": FLUORESCENCE_AI_MODE,
        "analysis_family": FLUORESCENCE_AI_FAMILY,
        "analysis_version": normalized_settings.analysis_version,
        "model_id": LOCAL_HEURISTIC_MODEL_ID,
        "model_version": normalized_settings.model_version,
        "ai_pack_postprocess": "local_membrane_region_postprocess",
        "model_commercial_use_status": "not_applicable",
        "model_license_summary": LOCAL_HEURISTIC_LICENSE_SUMMARY,
        "ai_backend_available": True,
        "model_available": True,
        "model_execution_mode": LOCAL_HEURISTIC_EXECUTION_MODE,
        "object_count_semantics": normalized_settings.object_count_semantics,
        "intensity_units": "raw_image_units",
        "roi_used": bool(roi is not None),
        "warnings": warnings,
        "effective_settings_hash": effective_settings_hash,
        "effective_settings_fields": sorted(asdict(normalized_settings).keys()),
    }
    return FluorescenceAIAssistedResult(
        channel_results=channel_results,
        metadata=metadata,
        warnings=warnings,
    )


def _as_cyx_array(image: np.ndarray) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim == 2:
        array = array[np.newaxis, :, :]
    if array.ndim != 3:
        raise ValueError(f"Fluorescence AI-assisted analysis expects C,Y,X or Y,X input, got shape {array.shape}")
    if array.shape[1] <= 0 or array.shape[2] <= 0:
        raise ValueError("Fluorescence AI-assisted image dimensions must be non-empty")
    return array.astype(np.float64, copy=False)


def _resolve_channels(channels: list[int] | None, *, n_channels: int) -> list[int]:
    requested = list(range(n_channels)) if channels is None else [int(value) for value in channels]
    resolved: list[int] = []
    seen: set[int] = set()
    for channel in requested:
        if channel < 0 or channel >= n_channels:
            raise IndexError(f"Channel index out of range: {channel}")
        if channel in seen:
            continue
        seen.add(channel)
        resolved.append(channel)
    return resolved


def _resolve_roi_mask(roi_mask: np.ndarray | None, *, image_shape: tuple[int, int]) -> np.ndarray | None:
    if roi_mask is None:
        return None
    roi = np.asarray(roi_mask, dtype=bool)
    if roi.shape != image_shape:
        raise ValueError(f"roi_mask shape {roi.shape} does not match image shape {image_shape}")
    return roi


def analyze_fluorescence_cellpose(image, *, channels, settings, roi_mask=None):
    """Infer each selected raw channel, then reuse the existing area/intensity contract."""
    from .cellpose_backend import run_cellpose_labels
    image_cyx = _as_cyx_array(image)
    indices = _resolve_channels(channels, n_channels=image_cyx.shape[0])
    roi = _resolve_roi_mask(roi_mask, image_shape=image_cyx.shape[1:])
    settings = _normalize_settings(settings)
    model = CELLPOSE_FLUORESCENCE_MODELS[settings.backend]
    warnings = ['cellpose_model_license_review_required']
    boundary = FluorescenceAIModelBoundary(model_execution_mode='runtime', backend_available=True,
        model_available=True, model_commercial_use_status='unverified',
        model_license_summary='Bundled Cellpose weights; verify applicable model licensing.')
    results = {}
    for index in indices:
        labels, provenance = run_cellpose_labels(image_cyx[index], model=model,
            diameter=settings.cellpose_diameter_px, min_size=settings.min_object_area_px)
        objects, filtered = _measure_ai_objects(labels, intensity_image=image_cyx[index],
            channel_index=index, roi_mask=roi, min_area_px=settings.min_object_area_px,
            max_area_px=settings.max_object_area_px)
        metadata = _base_metadata(settings=settings, boundary=boundary, model_execution_mode='runtime',
            roi_used=roi is not None, effective_settings_hash=_effective_settings_hash(settings), warnings=warnings)
        metadata.update(model_id=settings.backend, model_version=str(provenance.get('cellpose_version', '')),
            ai_pack_postprocess='native_cellpose_instances', backend=settings.backend,
            cellpose=provenance, settings=asdict(settings), coordinate_space='source_pixels')
        results[index] = FluorescenceAIAssistedChannelResult(index, objects, filtered, list(warnings), metadata)
    return FluorescenceAIAssistedResult(results, dict(next(iter(results.values())).metadata), list(warnings))


def _normalize_settings(settings: FluorescenceAIAssistedSettings) -> FluorescenceAIAssistedSettings:
    if not isinstance(settings, FluorescenceAIAssistedSettings):
        raise TypeError("settings must be FluorescenceAIAssistedSettings")
    return FluorescenceAIAssistedSettings(
        backend=str(settings.backend),
        cellpose_diameter_px=float(settings.cellpose_diameter_px),
        model_id=str(settings.backend if settings.backend in CELLPOSE_FLUORESCENCE_MODELS else settings.model_id or "cellpose_cyto3"),
        model_version=str(settings.model_version or "0.1.0"),
        ai_pack_postprocess=str(settings.ai_pack_postprocess or "cellpose_flow_v1_minimal"),
        detection_sensitivity=float(settings.detection_sensitivity),
        min_object_area_px=max(1, int(settings.min_object_area_px)),
        max_object_area_px=max(0, int(settings.max_object_area_px)),
        object_count_semantics=str(settings.object_count_semantics or FLUORESCENCE_AI_OBJECT_SEMANTICS),
        allow_restricted_model=bool(settings.allow_restricted_model),
        analysis_version=str(settings.analysis_version or FLUORESCENCE_AI_ANALYSIS_VERSION),
    )


def _effective_settings_hash(settings: FluorescenceAIAssistedSettings) -> str:
    payload = {
        "analysis_version": settings.analysis_version,
        "model_id": settings.model_id,
        "model_version": settings.model_version,
        "ai_pack_postprocess": settings.ai_pack_postprocess,
        "detection_sensitivity": float(settings.detection_sensitivity),
        "min_object_area_px": int(settings.min_object_area_px),
        "max_object_area_px": int(settings.max_object_area_px),
        "object_count_semantics": settings.object_count_semantics,
        "allow_restricted_model": bool(settings.allow_restricted_model),
    }
    if settings.backend in CELLPOSE_FLUORESCENCE_MODELS:
        payload.update(backend=settings.backend, cellpose_diameter_px=settings.cellpose_diameter_px)
    return sha256_json(payload)


def _resolve_execution_mode(
    boundary: FluorescenceAIModelBoundary,
    settings: FluorescenceAIAssistedSettings,
) -> tuple[str, list[str]]:
    warnings = list(boundary.warnings)
    mode = str(boundary.model_execution_mode or "unavailable")
    restricted = str(boundary.model_commercial_use_status or "restricted") != "allowed"
    if mode == "mock":
        if boundary.mock_label_masks is None:
            return "unavailable", _dedupe_preserve_order([*warnings, "mock_model_boundary_missing_label_masks", AI_ASSISTED_UNAVAILABLE_WARNING])
        return "mock", _dedupe_preserve_order(warnings)
    if not boundary.backend_available:
        warnings.append("ai_backend_unavailable")
    if not boundary.model_available:
        warnings.append("ai_model_artifact_unavailable")
    if restricted and not settings.allow_restricted_model:
        warnings.append("restricted_model_execution_not_allowed")
    if mode == "runtime" and boundary.backend_available and boundary.model_available and (not restricted or settings.allow_restricted_model):
        warnings.append("runtime_execution_not_implemented_in_v8_1_a")
    warnings.append(AI_ASSISTED_UNAVAILABLE_WARNING)
    return "unavailable", _dedupe_preserve_order(warnings)


def _mock_label_mask_for_channel(
    boundary: FluorescenceAIModelBoundary,
    channel_index: int,
    *,
    image_shape: tuple[int, int],
) -> np.ndarray:
    masks = boundary.mock_label_masks or {}
    label_mask = np.asarray(masks.get(int(channel_index), np.zeros(image_shape, dtype=np.int32)), dtype=np.int32)
    if label_mask.shape != image_shape:
        raise ValueError(f"mock label mask shape {label_mask.shape} does not match image shape {image_shape}")
    return label_mask


def _measure_local_heuristic_objects(
    channel_image: np.ndarray,
    *,
    channel_index: int,
    roi_mask: np.ndarray | None,
    min_area_px: int,
    max_area_px: int,
    detection_sensitivity: float,
) -> tuple[list[FluorescenceAIAssistedObject], np.ndarray, str]:
    from scipy import ndimage as ndi
    from skimage.filters import threshold_otsu
    from skimage.morphology import area_closing
    from skimage.morphology import area_opening
    from skimage.morphology import closing
    from skimage.morphology import dilation
    from skimage.morphology import disk
    from skimage.morphology import erosion
    raw_channel = np.asarray(channel_image, dtype=np.float64)
    if roi_mask is not None:
        working = np.where(np.asarray(roi_mask, dtype=bool), raw_channel, 0.0)
    else:
        working = raw_channel
    positives = working[working > 0]
    if positives.size == 0:
        return [], np.zeros(raw_channel.shape, dtype=np.int32), "empty"

    otsu_input = positives if np.unique(positives).size > 1 else working.ravel()
    otsu_value = float(threshold_otsu(otsu_input)) if np.unique(otsu_input).size > 1 else float(np.max(positives))
    sensitivity_scale = float(np.clip(0.95 - 0.18 * (detection_sensitivity - 1.0), 0.55, 1.10))
    bright_threshold = max(float(np.min(positives)), otsu_value * sensitivity_scale)
    bright_mask = working >= bright_threshold
    if roi_mask is not None:
        bright_mask &= np.asarray(roi_mask, dtype=bool)
    bright_mask = closing(bright_mask, disk(1))
    bright_mask = area_opening(bright_mask, area_threshold=max(1, min_area_px))

    direct_objects, direct_mask = _objects_from_binary_mask(
        bright_mask,
        intensity_image=raw_channel,
        channel_index=channel_index,
        roi_mask=roi_mask,
        min_area_px=min_area_px,
        max_area_px=max_area_px,
        exclude_edge_touch=False,
    )

    low_threshold = max(float(np.min(positives)), bright_threshold * 0.18)
    tissue_mask = working >= low_threshold
    if roi_mask is not None:
        tissue_mask &= np.asarray(roi_mask, dtype=bool)
    tissue_mask = closing(tissue_mask, disk(2))
    tissue_mask = ndi.binary_fill_holes(tissue_mask)
    tissue_mask = area_opening(tissue_mask, area_threshold=max(32, min_area_px * 4))
    membrane_mask = dilation(bright_mask, disk(1))
    membrane_mask = closing(membrane_mask, disk(1))
    tissue_eroded = erosion(tissue_mask, disk(2))
    enclosed_mask = tissue_mask & ~membrane_mask
    enclosed_mask = area_opening(enclosed_mask, area_threshold=max(16, min_area_px))
    enclosed_mask = area_closing(enclosed_mask, area_threshold=max(16, min_area_px))
    enclosed_objects, enclosed_label_mask = _objects_from_binary_mask(
        enclosed_mask,
        intensity_image=raw_channel,
        channel_index=channel_index,
        roi_mask=roi_mask,
        min_area_px=min_area_px,
        max_area_px=max_area_px,
        exclude_edge_touch=True,
        require_inside_mask=tissue_eroded,
    )

    if enclosed_objects and (
        not direct_objects
        or len(enclosed_objects) >= len(direct_objects)
        or (
            np.median([item.area_px for item in enclosed_objects])
            > np.median([item.area_px for item in direct_objects]) * 1.5
        )
    ):
        return enclosed_objects, enclosed_label_mask, "membrane_enclosed_regions"
    return direct_objects, direct_mask, "direct_bright_objects"


def _measure_ai_objects(
    label_mask: np.ndarray,
    *,
    intensity_image: np.ndarray,
    channel_index: int,
    roi_mask: np.ndarray | None,
    min_area_px: int,
    max_area_px: int,
) -> tuple[list[FluorescenceAIAssistedObject], np.ndarray]:
    from skimage.measure import regionprops
    working = np.asarray(label_mask, dtype=np.int32).copy()
    if roi_mask is not None:
        working[~roi_mask] = 0
    relabeled = working.astype(np.int32, copy=False)
    objects: list[FluorescenceAIAssistedObject] = []
    filtered = np.zeros_like(relabeled, dtype=np.int32)
    next_id = 1
    raw_channel = np.asarray(intensity_image, dtype=np.float64)
    for region in regionprops(relabeled, intensity_image=raw_channel):
        area = int(region.area)
        if area < min_area_px:
            continue
        if max_area_px > 0 and area > max_area_px:
            continue
        coords = region.coords
        mean_intensity = float(region.intensity_mean if hasattr(region, "intensity_mean") else region.mean_intensity)
        integrated_intensity = float(raw_channel[coords[:, 0], coords[:, 1]].sum()) if coords.size else 0.0
        min_y, min_x, max_y, max_x = region.bbox
        filtered[relabeled == int(region.label)] = next_id
        objects.append(
            FluorescenceAIAssistedObject(
                object_id=next_id,
                channel_index=int(channel_index),
                area_px=area,
                centroid_y=float(region.centroid[0]),
                centroid_x=float(region.centroid[1]),
                mean_intensity=mean_intensity,
                integrated_intensity=integrated_intensity,
                bbox_y_min=int(min_y),
                bbox_x_min=int(min_x),
                bbox_y_max=int(max_y),
                bbox_x_max=int(max_x),
            )
        )
        next_id += 1
    return objects, filtered


def _objects_from_binary_mask(
    binary_mask: np.ndarray,
    *,
    intensity_image: np.ndarray,
    channel_index: int,
    roi_mask: np.ndarray | None,
    min_area_px: int,
    max_area_px: int,
    exclude_edge_touch: bool,
    require_inside_mask: np.ndarray | None = None,
) -> tuple[list[FluorescenceAIAssistedObject], np.ndarray]:
    from scipy import ndimage as ndi
    from skimage.measure import regionprops
    labels = np.asarray(ndi.label(np.asarray(binary_mask, dtype=bool))[0], dtype=np.int32)
    objects: list[FluorescenceAIAssistedObject] = []
    filtered = np.zeros_like(labels, dtype=np.int32)
    next_id = 1
    raw_channel = np.asarray(intensity_image, dtype=np.float64)
    for region in regionprops(labels, intensity_image=raw_channel):
        area = int(region.area)
        if area < int(min_area_px):
            continue
        if max_area_px > 0 and area > int(max_area_px):
            continue
        min_y, min_x, max_y, max_x = region.bbox
        if exclude_edge_touch and (
            min_y <= 0 or min_x <= 0 or max_y >= labels.shape[0] or max_x >= labels.shape[1]
        ):
            continue
        region_mask = labels == int(region.label)
        if require_inside_mask is not None and np.any(region_mask & ~np.asarray(require_inside_mask, dtype=bool)):
            continue
        if roi_mask is not None and not np.any(region_mask & np.asarray(roi_mask, dtype=bool)):
            continue
        coords = region.coords
        mean_intensity = float(region.intensity_mean if hasattr(region, "intensity_mean") else region.mean_intensity)
        integrated_intensity = float(raw_channel[coords[:, 0], coords[:, 1]].sum()) if coords.size else 0.0
        filtered[region_mask] = next_id
        objects.append(
            FluorescenceAIAssistedObject(
                object_id=next_id,
                channel_index=int(channel_index),
                area_px=area,
                centroid_y=float(region.centroid[0]),
                centroid_x=float(region.centroid[1]),
                mean_intensity=mean_intensity,
                integrated_intensity=integrated_intensity,
                bbox_y_min=int(min_y),
                bbox_x_min=int(min_x),
                bbox_y_max=int(max_y),
                bbox_x_max=int(max_x),
            )
        )
        next_id += 1
    return objects, filtered


def _base_metadata(
    *,
    settings: FluorescenceAIAssistedSettings,
    boundary: FluorescenceAIModelBoundary,
    model_execution_mode: str,
    roi_used: bool,
    effective_settings_hash: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "analysis_mode": FLUORESCENCE_AI_MODE,
        "analysis_family": FLUORESCENCE_AI_FAMILY,
        "analysis_version": settings.analysis_version,
        "model_id": settings.model_id,
        "model_version": settings.model_version,
        "ai_pack_postprocess": settings.ai_pack_postprocess,
        "model_commercial_use_status": str(boundary.model_commercial_use_status or "restricted"),
        "model_license_summary": str(boundary.model_license_summary or ""),
        "ai_backend_available": bool(boundary.backend_available),
        "model_available": bool(boundary.model_available),
        "model_execution_mode": model_execution_mode,
        "object_count_semantics": settings.object_count_semantics,
        "intensity_units": "raw_image_units",
        "roi_used": bool(roi_used),
        "warnings": list(warnings),
        "effective_settings_hash": effective_settings_hash,
        "effective_settings_fields": sorted(asdict(settings).keys()),
        "mock_label_mask_reserved_settings_note": (
            "detection_sensitivity, model_id, model_version, and ai_pack_postprocess are reserved "
            "for runtime/model postprocess behavior and do not alter explicit mock label masks in V8.1-A."
        ),
    }


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
