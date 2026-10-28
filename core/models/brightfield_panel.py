from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
from math import sqrt
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar
from uuid import uuid4

import numpy as np
from PyQt6.QtGui import QColor, QImage, QImageReader

from core.brightfield_semantics import (
    BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
    BRIGHTFIELD_LEGACY_UNKNOWN,
    BRIGHTFIELD_OBJECT_COUNT_SEMANTICS,
)
from core.image_analysis import (
    BRIGHTFIELD_HE_PROFILE,
    BatchThresholdState,
    analyze_brightfield_image,
    estimate_he_auto_parameters,
    evaluate_brightfield_reliability,
    get_task_profile,
    render_mask_overlay,
    validate_overlay_result,
)
from core.image_analysis.brightfield_guided import (
    BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION,
    BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
    BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION,
    BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS,
    BRIGHTFIELD_GUIDED_PREVIEW_STALE_STATUS,
    apply_brightfield_assisted_small_region_filter_bias,
    apply_brightfield_assisted_threshold_bias,
    apply_brightfield_foolproof_threshold_bias,
    build_brightfield_assisted_postprocess_slider_values,
    build_brightfield_foolproof_slider_values,
    neutral_brightfield_guided_slider_values,
    normalize_brightfield_assisted_small_region_filter_bias,
    normalize_brightfield_assisted_threshold_bias,
    normalize_brightfield_foolproof_threshold_bias,
    package_brightfield_guided_measurement_metadata,
)
from core.image_analysis.he_fiber_area import (
    HE_FIBER_AREA_ANALYSIS_VERSION,
    HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
    HE_FIBER_DEFAULT_METHOD,
    HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
    HE_FIBER_ORIGINAL_DEFAULT_MAX_AREA_PX,
    HE_FIBER_CELLPOSE_MODELS,
    analyze_he_fiber_area,
    build_auto_he_fiber_calibration,
    clamp_he_fiber_sensitivity,
    normalize_he_fiber_segmentation_method,
    render_he_fiber_area_overlay,
    uses_anti_oversegmentation_he_fiber_path,
)
from core.image_analysis.bright_round_cells import (
    BRIGHT_ROUND_CELL_ANALYSIS_VERSION,
    analyze_bright_round_cells,
    render_bright_round_cell_overlay,
)
from core.image_analysis.cell_calibration import calibrate_cell_analysis
from core.measurement_scale import apply_area_scale, merge_measurement_scale_updates, resolve_measurement_scale
from core.image_analysis.io_engine import read_microscopy_image, read_rgb_image
from core.constants import SOFTWARE_VERSION
from core.models.panel import BasePanel, register_panel_class
from core.utils.hashing import sha256_json
from core.utils.image_formats import BRIGHTFIELD_IMAGE_SUFFIXES, compound_suffix
from core.utils.rendering import fit_qimage_to_size, qimage_to_bytes, target_pixel_size
from core.utils.time import utc_now_iso

ANALYSIS_BACKEND_NAME = "sciefig.image_analysis"
BRIGHTFIELD_BATCH_THRESHOLD_PARAM = "a_min"
BRIGHTFIELD_BATCH_LOCK_SOURCE_MODE_KEY = "batch_lock_source_mode"
BRIGHTFIELD_FOOLPROOF_METADATA_KEYS = (
    "foolproof_threshold_bias",
    "foolproof_delta_a_min",
    "foolproof_threshold_bias_mapping_version",
    "foolproof_baseline_a_min",
    "foolproof_threshold_bias_active",
    "foolproof_threshold_bias_inactive_reason",
)
BRIGHTFIELD_ASSISTED_METADATA_KEYS = (
    "assisted_threshold_bias",
    "assisted_threshold_bias_active",
    "assisted_threshold_bias_inactive_reason",
    "assisted_small_region_filter_bias",
    "assisted_small_region_filter_mapping_version",
    "assisted_baseline_min_area_px",
    "assisted_effective_min_area_px",
)


BRIGHTFIELD_OTHER_STAIN_LABEL = "Other high-contrast stain"
BRIGHTFIELD_STAIN_TASK_IDS = {
    "H&E": BRIGHTFIELD_HE_PROFILE.task_id,
    "Special stain": "brightfield_special_stain_area",
    "Crystal violet": "brightfield_crystal_violet_area",
    "Oil Red O": "brightfield_oil_red_o_area",
    BRIGHTFIELD_OTHER_STAIN_LABEL: "brightfield_special_stain_area",
}

BRIGHTFIELD_STAIN_TYPES = [
    "H&E",
    "Special stain",
    "Crystal violet",
    "Oil Red O",
    BRIGHTFIELD_OTHER_STAIN_LABEL,
]

BRIGHTFIELD_STAIN_TYPE_ALIASES = {
    "he": "H&E",
    "h&e": "H&E",
    "H&e": "H&E",
    "h&E": "H&E",
    "special_stain": "Special stain",
    "crystal_violet": "Crystal violet",
    "oil_red_o": "Oil Red O",
    "Other": BRIGHTFIELD_OTHER_STAIN_LABEL,
}


def normalize_brightfield_stain_type(stain_type: str) -> str:
    value = str(stain_type).strip()
    if value in BRIGHTFIELD_STAIN_TYPES:
        return value
    if value in BRIGHTFIELD_STAIN_TYPE_ALIASES:
        return BRIGHTFIELD_STAIN_TYPE_ALIASES[value]
    lowered = value.lower()
    for alias_key, canonical in BRIGHTFIELD_STAIN_TYPE_ALIASES.items():
        if alias_key.lower() == lowered:
            return canonical
    for canonical in BRIGHTFIELD_STAIN_TYPES:
        if canonical.lower() == lowered:
            return canonical
    return BRIGHTFIELD_OTHER_STAIN_LABEL


def default_analysis_settings() -> dict[str, Any]:
    return {
        "segmentation_mode": "hsv",
        "h_min": 0.00,
        "h_max": 0.20,
        "s_min": 0.15,
        "s_max": 1.00,
        "v_min": 0.00,
        "v_max": 0.95,
        "l_min": 0.0,
        "l_max": 95.0,
        "a_min": -10.0,
        "a_max": 60.0,
        "b_min": 0.0,
        "b_max": 90.0,
        "gray_threshold": 0.55,
        "gray_invert": True,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 10,
        "max_object_area_px": 0,
        "he_fiber_foreground_sensitivity": HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
        "he_fiber_splitting_sensitivity": HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
        "he_fiber_segmentation_method": HE_FIBER_DEFAULT_METHOD,
    }


def default_guided_state() -> dict[str, Any]:
    return {
        "guided_mode": "foolproof",
        "task_profile": BRIGHTFIELD_HE_PROFILE.to_dict(),
        "foolproof_threshold_bias": 0,
        "assisted_threshold_bias": 0,
        "assisted_small_region_filter_bias": 0,
        "sensitivity": str(BRIGHTFIELD_HE_PROFILE.default_settings.get("sensitivity", "medium")),
        "cleanup_level": str(BRIGHTFIELD_HE_PROFILE.default_settings.get("cleanup_level", "low")),
        "overlay_opacity": float(BRIGHTFIELD_HE_PROFILE.default_settings.get("overlay_opacity", 0.35)),
        "batch_threshold_state": BatchThresholdState(
            task_id=BRIGHTFIELD_HE_PROFILE.task_id,
            locked=False,
        ).to_dict(),
    }


def _normalize_guided_mode(value: Any) -> str:
    mode = str(value or "foolproof").strip().lower()
    if mode == "guided":
        return "foolproof"
    if mode in {"foolproof", "assisted", "expert"}:
        return mode
    return "foolproof"


def _coerce_guided_mode(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text == "guided":
        return "foolproof"
    if text in {"foolproof", "assisted", "expert"}:
        return text
    return None


def _coerce_analysis_mode(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"expert", "guided_foolproof", "guided_assisted", "profile_guided_foolproof"}:
        return text
    return None


def _coerce_threshold_source(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if text in {"manual", "image_auto", "batch_locked", "profile_preset"}:
        return text
    return None


def _build_ai_results_with_per_instance(analysis: dict[str, Any]) -> dict[str, Any]:
    """Persist scalar AI results plus per-instance areas and centroids."""
    results = dict(analysis.get("results", {}))
    areas = analysis.get("ai_object_areas_px", results.get("he_fiber_areas_px", []))
    areas_um2 = analysis.get("ai_object_areas_um2", results.get("he_fiber_areas_um2", []))
    centroids = analysis.get("ai_object_centroids", results.get("he_fiber_centroids", []))
    results["ai_object_areas_px"] = [int(area) for area in areas]
    results["ai_object_areas_um2"] = [float(area) for area in areas_um2]
    results["ai_object_centroids"] = [[float(cy), float(cx)] for cy, cx in centroids]
    return results


def _rescale_ai_results_with_measurement_scale(results: dict[str, Any], measurement_scale: dict[str, Any]) -> dict[str, Any]:
    payload = dict(results)
    areas_px = [int(area) for area in payload.get("ai_object_areas_px", payload.get("he_fiber_areas_px", [])) if isinstance(area, (int, float))]
    areas_um2 = [float(apply_area_scale(area, measurement_scale)) for area in areas_px]
    total_area_px = int(payload.get("ai_object_total_area_px", payload.get("he_fiber_total_area_px", sum(areas_px))) or 0)
    payload["ai_object_areas_px"] = areas_px
    payload["ai_object_areas_um2"] = areas_um2
    payload["ai_object_total_area_um2"] = float(apply_area_scale(total_area_px, measurement_scale))
    payload["ai_object_mean_area_um2"] = float(np.mean(areas_um2)) if areas_um2 else 0.0
    payload["ai_object_median_area_um2"] = float(np.median(areas_um2)) if areas_um2 else 0.0
    if "he_fiber_areas_px" in payload:
        payload["he_fiber_areas_um2"] = areas_um2
        payload["he_fiber_total_area_um2"] = payload["ai_object_total_area_um2"]
        payload["he_fiber_mean_area_um2"] = payload["ai_object_mean_area_um2"]
        payload["he_fiber_median_area_um2"] = payload["ai_object_median_area_um2"]
    return payload


def _build_ai_analysis_metadata(analysis: dict[str, Any]) -> dict[str, Any]:
    """Merge analyzer metadata with mask availability flags."""
    metadata = dict(analysis.get("analysis_metadata", {}))
    mask = analysis.get("ai_object_mask")
    metadata["ai_object_mask_available"] = mask is not None
    metadata["ai_object_mask_persisted"] = False
    metadata["ai_object_mask_shape"] = [int(size) for size in np.asarray(mask).shape] if mask is not None else None
    return metadata


@register_panel_class
@dataclass
class BrightfieldPanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "brightfield"
    SOURCE_MODULE: ClassVar[str] = "brightfield"
    _ANALYSIS_MASK_CACHE_LIMIT: ClassVar[int] = 4
    _HE_FIBER_AREA_CACHE_LIMIT: ClassVar[int] = 4
    panel_type: str = field(default=PANEL_TYPE)
    _transient_preview_state: dict[str, Any] = field(default_factory=dict, init=False, repr=False, compare=False)
    _brightfield_analysis_mask_cache: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _transient_he_fiber_area_masks: OrderedDict[str, np.ndarray] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
        compare=False,
    )
    _transient_he_fiber_area_labels: OrderedDict[str, np.ndarray] = field(
        default_factory=OrderedDict,
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        self.style["preset"] = str(self.style.get("preset") or "Nature")
        self.data.setdefault("brightfield_images", [])
        self.data.setdefault("active_image_uid", None)
        self.data.setdefault("active_step", 0)
        self.data.setdefault("analysis_settings", default_analysis_settings())
        self.data["guided_state"] = self._merge_guided_state(self.data.get("guided_state"))
        self.data.setdefault("statistics", {})
        self.data.setdefault("composer_outputs", [])
        self.analysis.setdefault("brightfield_measurements", [])
        self.analysis.setdefault("brightfield_ai_object_count_measurements", [])
        self.analysis.setdefault("bright_round_cell_diameter_measurements", [])
        self._normalize_brightfield_measurement_collections()

    def import_image(
        self,
        path: str | Path,
        group: str = "Ungrouped",
        stain_type: str = "H&E",
    ) -> dict[str, Any]:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = compound_suffix(source)
        if suffix not in BRIGHTFIELD_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported Brightfield image format: {suffix}")

        imported = read_microscopy_image(source)
        return self.import_image_from_decoded(source, imported, group=group, stain_type=stain_type)

    def import_image_from_decoded(
        self,
        path: str | Path,
        imported: Any,
        group: str = "Ungrouped",
        stain_type: str = "H&E",
    ) -> dict[str, Any]:
        """Create the normal import record from one already-decoded image result."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = compound_suffix(source)
        if suffix not in BRIGHTFIELD_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported Brightfield image format: {suffix}")

        metadata = self._build_import_metadata(imported.metadata)
        record = {
            "uid": str(uuid4()),
            "source_path": str(source),
            "source_name": source.name,
            "source_hash": imported.source_hash,
            "group": str(group),
            "stain_type": self._normalize_stain_type(stain_type),
            "metadata": metadata,
            "measurement_scale_bar": resolve_measurement_scale({"metadata": metadata}),
            "preprocessing": {
                "segmentation_mode": "hsv",
                "params": {},
            },
            "roi": {
                "mode": "whole_image",
                "items": [],
                "active_item_uuid": None,
            },
            "measurements": [],
            "imported_at": utc_now_iso(),
            "non_destructive": True,
        }
        images = self.images()
        images.append(record)
        self.data["brightfield_images"] = images
        self.data["active_image_uid"] = record["uid"]
        self.data["raw_source"] = source.name
        self.data["raw_hash"] = record["source_hash"]
        self.data["raw_copy_path"] = f"data/brightfield/{self.panel_uuid}/{source.name}"
        self.append_audit("import_brightfield_image", f"{source.name}; group={group}; stain={record['stain_type']}")
        return record

    def images(self) -> list[dict[str, Any]]:
        return list(self.data.get("brightfield_images", []))

    def active_image(self) -> dict[str, Any] | None:
        active_uid = self.data.get("active_image_uid")
        images = self.images()
        for image in images:
            if image.get("uid") == active_uid:
                return image
        return images[0] if images else None

    def set_active_image(self, image_uid: str) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        self.data["active_image_uid"] = str(image_record["uid"])
        return image_record

    def set_image_group(self, image_uid: str, group: str) -> dict[str, Any]:
        image = self._resolve_image_record(image_uid)
        image["group"] = str(group).strip() or "Ungrouped"
        self.append_audit("set_brightfield_group", f"{image_uid}; {image['group']}")
        return image

    def set_image_stain_type(self, image_uid: str, stain_type: str) -> dict[str, Any]:
        image = self._resolve_image_record(image_uid)
        image["stain_type"] = self._normalize_stain_type(stain_type)
        self.append_audit("set_brightfield_stain_type", f"{image_uid}; {image['stain_type']}")
        return image

    def source_qimage(self, image_uid: str | None = None) -> QImage:
        image_record = self._resolve_image_record(image_uid)
        return self._read_raster(Path(str(image_record.get("source_path", ""))))

    def display_qimage(self, image_uid: str | None = None) -> QImage:
        from core.plotting.histology_display import white_balanced_qimage
        record=self._resolve_image_record(image_uid)
        return white_balanced_qimage(self.source_qimage(record['uid']),record.get('display_white_balance'))

    def set_image_roi(self, image_uid: str, roi_type: str, coordinates_px: dict[str, Any]) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        normalized = self._normalize_roi_geometry(roi_type, coordinates_px)
        roi_item = {
            "roi_uuid": str(uuid4()),
            "type": str(roi_type),
            "coordinates_px": normalized,
            "created_at": utc_now_iso(),
        }
        image_record["roi"] = {
            "mode": "selected_roi",
            "items": [roi_item],
            "active_item_uuid": roi_item["roi_uuid"],
        }
        self.append_audit("set_brightfield_roi", f"{image_uid}; {roi_type}")
        return roi_item

    def apply_roi_snapshot(self, image_uid: str, roi_snapshot: dict[str, Any] | None) -> None:
        image_record = self._resolve_image_record(image_uid)
        image_record["roi"] = self._resolve_roi_snapshot(image_record, roi_snapshot)
        self.append_audit("select_brightfield_measurement_roi", image_uid)

    def clear_image_roi(self, image_uid: str) -> None:
        image_record = self._resolve_image_record(image_uid)
        image_record["roi"] = {"mode": "whole_image", "items": [], "active_item_uuid": None}
        self.append_audit("clear_brightfield_roi", image_uid)

    def active_roi(self, image_uid: str | None = None) -> dict[str, Any] | None:
        image_record = self._resolve_image_record(image_uid)
        roi_state = dict(image_record.get("roi", {}))
        active_uuid = str(roi_state.get("active_item_uuid", "")).strip()
        for item in roi_state.get("items", []):
            if str(item.get("roi_uuid", "")) == active_uuid:
                return dict(item)
        items = list(roi_state.get("items", []))
        return dict(items[-1]) if items else None

    def render_preview(self, target_width_mm: float, target_height_mm: float, dpi: int = 150) -> QImage:
        image_record = self.active_image()
        if image_record is None:
            raise ValueError("Brightfield panel has no active image to render.")
        image = self._read_raster(Path(str(image_record.get("source_path", ""))))
        target_size = target_pixel_size(target_width_mm, target_height_mm, dpi)
        return fit_qimage_to_size(image, target_size, QColor("white"))

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        raster_format = format.lower().lstrip(".")
        if raster_format in {"pdf", "svg"}:
            raster_format = "png"
        image = self.render_preview(target_width_mm, target_height_mm, dpi=dpi)
        return qimage_to_bytes(image, raster_format)

    def create_measurement(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        merged_settings = self._merge_analysis_settings(settings)
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        task_profile = self._task_profile_for_stain_type(str(image_record.get("stain_type", "H&E")))
        started_at = perf_counter()
        analysis = analyze_brightfield_image(rgb, merged_settings, roi_mask=roi_mask)
        processing_time_ms = max(0, int(round((perf_counter() - started_at) * 1000.0)))
        now = utc_now_iso()
        measurement = {
            "measurement_uuid": str(uuid4()),
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record["source_name"]),
            "group": str(image_record.get("group", "Ungrouped")),
            "stain_type": str(image_record.get("stain_type", "H&E")),
            "type": "brightfield_measurement",
            "roi": roi_snapshot,
            "params": dict(merged_settings),
            "results": dict(analysis["results"]),
            "created_at": now,
            "processing_time_ms": processing_time_ms,
            "backend_name": ANALYSIS_BACKEND_NAME,
            "backend_version": SOFTWARE_VERSION,
            "model_name": self._resolve_analysis_model_name("manual", dict(merged_settings), None),
            "model_hash": self._build_analysis_model_hash("manual", dict(merged_settings), None),
            "include_in_statistics": True,
            "warnings": [],
            "user_overrides": {},
            "measurement_scale_bar": deepcopy(self.measurement_scale_bar(str(image_record["uid"]))),
            "guided_mode": "expert",
            "task_profile": task_profile.to_dict(),
            "analysis_metadata": {
                "task_id": str(task_profile.task_id),
                "analysis_kind": "brightfield_measurement",
                "threshold_source": "manual",
                "roi_mode": str(roi_snapshot.get("mode", "whole_image")),
                "segmentation_mode": str(merged_settings.get("segmentation_mode", "hsv")),
            },
        }
        measurement["analysis_metadata"].update(dict(analysis.get("analysis_metadata", {})))
        self._apply_measurement_reliability(measurement, rgb, analysis)
        self._apply_analysis_result_fields(measurement, threshold_source="manual")
        self._cache_brightfield_analysis_mask(measurement, analysis["mask"])

        images = self.images()
        for item in images:
            if item.get("uid") == image_record["uid"]:
                item.setdefault("measurements", []).append(measurement)
                item["preprocessing"] = {
                    "segmentation_mode": str(merged_settings.get("segmentation_mode", "hsv")),
                    "params": dict(merged_settings),
                }
                break
        self.data["brightfield_images"] = images
        self.data["analysis_settings"] = dict(merged_settings)
        measurements = list(self.analysis.get("brightfield_measurements", []))
        measurements.append(measurement)
        self.analysis["brightfield_measurements"] = measurements
        self.analysis.pop('brightfield_statistics',None)
        self.append_audit("measure_brightfield", f"{image_record['uid']}; mode={merged_settings.get('segmentation_mode', 'hsv')}")
        return measurement

    def create_ai_object_count_measurement(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Independent H&E Fiber Area path, kept in the legacy AI collection for compatibility."""
        image_record, rgb = self._load_image_rgb(image_uid)
        merged_settings = self._merge_analysis_settings(settings)
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        started_at = perf_counter()
        calibration = self.he_fiber_area_calibration(str(image_record["uid"]))
        if calibration is None:
            raise ValueError("Calibrate H&E Fiber Area for the active image before running measurement.")
        self.persist_he_fiber_analysis_settings(merged_settings)
        analysis = analyze_he_fiber_area(
            rgb,
            calibration,
            roi_mask=roi_mask,
            **self._he_fiber_analyze_kwargs(merged_settings),
        )
        processing_time_ms = max(0, int(round((perf_counter() - started_at) * 1000.0)))
        he_results = dict(analysis.get("results", {}))
        he_count = int(he_results.get("he_fiber_count", 0) or 0)
        he_area_values = [int(area) for area in he_results.get("he_fiber_areas_px", [])]
        measurement_scale = self.measurement_scale_bar(str(image_record["uid"]))
        he_area_values_um2 = [float(apply_area_scale(area, measurement_scale)) for area in he_area_values]
        he_total_area_um2 = float(apply_area_scale(int(he_results.get("he_fiber_total_area_px", 0) or 0), measurement_scale))
        he_mean_area_um2 = float(np.mean(he_area_values_um2)) if he_area_values_um2 else 0.0
        he_median_area_um2 = float(np.median(he_area_values_um2)) if he_area_values_um2 else 0.0
        analysis_for_persistence = {
            **analysis,
            "ai_object_areas_px": he_area_values,
            "ai_object_areas_um2": he_area_values_um2,
            "ai_object_centroids": list(he_results.get("he_fiber_centroids", [])),
            "results": {
                **he_results,
                "ai_object_count": he_count,
                "ai_object_total_area_px": int(he_results.get("he_fiber_total_area_px", 0) or 0),
                "ai_object_total_area_um2": he_total_area_um2,
                "ai_object_mean_area_px": float(he_results.get("he_fiber_mean_area_px", 0.0) or 0.0),
                "ai_object_mean_area_um2": he_mean_area_um2,
                "ai_object_median_area_px": float(he_results.get("he_fiber_median_area_px", 0.0) or 0.0),
                "ai_object_median_area_um2": he_median_area_um2,
                "he_fiber_areas_um2": he_area_values_um2,
                "he_fiber_total_area_um2": he_total_area_um2,
                "he_fiber_mean_area_um2": he_mean_area_um2,
                "he_fiber_median_area_um2": he_median_area_um2,
                "backend": "cellpose" if merged_settings.get('he_fiber_segmentation_method') in HE_FIBER_CELLPOSE_MODELS else "local_he_fiber_area",
            },
            "analysis_metadata": {
                **dict(analysis.get("analysis_metadata", {})),
                "analysis_kind": "he_fiber_area",
                "ui_label": "H&E Fiber Area",
                "overlay_mask_available": True,
                "calibration_snapshot": dict(calibration),
                "measurement_scale_bar": dict(measurement_scale),
            },
            "ai_object_mask": np.asarray(analysis.get("he_fiber_mask"), dtype=bool),
        }
        now = utc_now_iso()
        measurement = {
            "measurement_uuid": str(uuid4()),
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record["source_name"]),
            "group": str(image_record.get("group", "Ungrouped")),
            "stain_type": str(image_record.get("stain_type", "H&E")),
            "type": "brightfield_ai_object_count",
            "display_name": "H&E Fiber Area",
            "roi": roi_snapshot,
            "params": dict(merged_settings),
            "results": _build_ai_results_with_per_instance(analysis_for_persistence),
            "created_at": now,
            "processing_time_ms": processing_time_ms,
            "backend_name": ANALYSIS_BACKEND_NAME,
            "backend_version": SOFTWARE_VERSION,
            "model_name": 'cellpose:'+HE_FIBER_CELLPOSE_MODELS[merged_settings['he_fiber_segmentation_method']] if merged_settings.get('he_fiber_segmentation_method') in HE_FIBER_CELLPOSE_MODELS else "local:he_fiber_area:v1",
            "model_hash": '' if merged_settings.get('he_fiber_segmentation_method') in HE_FIBER_CELLPOSE_MODELS else HE_FIBER_AREA_ANALYSIS_VERSION,
            "include_in_statistics": not bool(analysis.get("requires_recalibration", False)),
            "warnings": list(analysis.get("analysis_warnings", [])),
            "user_overrides": {},
            "analysis_metadata": _build_ai_analysis_metadata(analysis_for_persistence),
            "measurement_scale_bar": dict(measurement_scale),
            "overlay_generated": True,
        }
        if merged_settings.get('he_fiber_segmentation_method') in HE_FIBER_CELLPOSE_MODELS:
            from core.image_analysis.cellpose_backend import pack_instance_labels
            measurement['instance_labels'] = pack_instance_labels(analysis['he_fiber_labels'])
            measurement['analysis_metadata']['ai_object_mask_persisted'] = True
        self._cache_he_fiber_area_data(
            measurement["measurement_uuid"],
            np.asarray(analysis.get("he_fiber_mask"), dtype=bool),
            np.asarray(analysis.get("he_fiber_labels"), dtype=np.int32),
        )
        measurements = list(self.analysis.get("brightfield_ai_object_count_measurements", []))
        measurements.append(measurement)
        self.analysis["brightfield_ai_object_count_measurements"] = measurements
        self.analysis.pop('brightfield_ai_object_count_plot_statistics',None)
        self.analysis.pop('brightfield_ai_object_count_statistics',None)
        self.append_audit(
            "measure_brightfield_he_fiber_area",
            f"{image_record['uid']}; count={measurement['results']['ai_object_count']}",
        )
        return measurement

    def measure_bright_round_cells(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Measure stable bright round cells using the saved image calibration."""

        image_record, rgb = self._load_image_rgb(image_uid)
        measurement_scale = self.measurement_scale_bar(str(image_record["uid"]))
        if bool(measurement_scale.get("assumed", False)):
            raise ValueError("Calibrate the Brightfield image before measuring bright round-cell diameters.")
        um_per_px_x = float(measurement_scale.get("um_per_px_x", 0.0) or 0.0)
        um_per_px_y = float(measurement_scale.get("um_per_px_y", 0.0) or 0.0)
        if um_per_px_x <= 0 or um_per_px_y <= 0:
            raise ValueError("Bright round-cell measurement requires complete positive X and Y calibration.")
        filter_um_per_px = float(sqrt(um_per_px_x * um_per_px_y))

        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        started_at = perf_counter()
        use_cellpose = (settings or {}).get('backend') == 'cellpose'
        analyzer = analyze_bright_round_cells
        if use_cellpose:
            from core.image_analysis.cellpose_cells import analyze_cellpose_cells
            analyzer = analyze_cellpose_cells
        analysis = analyzer(
            rgb,
            um_per_px=filter_um_per_px,
            settings=settings,
            roi_mask=roi_mask,
        )
        if um_per_px_x != um_per_px_y:
            analysis = calibrate_cell_analysis(analysis, measurement_scale)
        processing_time_ms = max(0, int(round((perf_counter() - started_at) * 1000.0)))
        now = utc_now_iso()
        measurement = {
            "measurement_uuid": str(uuid4()),
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record["source_name"]),
            "group": str(image_record.get("group", "Ungrouped")),
            "stain_type": str(image_record.get("stain_type", "H&E")),
            "type": "brightfield_bright_round_cell_diameter",
            "display_name": "Bright round-cell diameter",
            "roi": roi_snapshot,
            "params": dict(analysis.get("settings", {})),
            "results": list(analysis.get("results", [])),
            "summary": dict(analysis.get("summary", {})),
            "analysis": analysis,
            "created_at": now,
            "processing_time_ms": processing_time_ms,
            "backend_name": ANALYSIS_BACKEND_NAME,
            "backend_version": SOFTWARE_VERSION,
            "model_name": "local:cellpose:cyto3" if use_cellpose else "local:bright_round_cells:v1",
            "model_hash": analysis['analysis_version'],
            "measurement_scale_bar": dict(measurement_scale),
            "include_in_statistics": True,
            "warnings": [],
            "overlay_generated": True,
            "analysis_metadata": {
                "analysis_kind": "brightfield_bright_round_cell_diameter",
                "analysis_version": analysis['analysis_version'],
                "roi_mode": str(roi_snapshot.get("mode", "whole_image")),
                "calibration_snapshot": dict(measurement_scale),
                "settings": dict(analysis.get("settings", {})),
            },
        }

        # Cell rows use a different schema from traditional area measurements.
        measurements = list(self.analysis.get("bright_round_cell_diameter_measurements", []))
        measurements.append(measurement)
        self.analysis["bright_round_cell_diameter_measurements"] = measurements
        self.analysis["bright_round_cell_diameter"] = measurement
        self.append_audit(
            "measure_brightfield_bright_round_cells",
            f"{image_record['uid']}; count={measurement['summary'].get('count', 0)}",
        )
        return measurement

    def render_bright_round_cell_overlay(self, measurement: dict[str, Any] | None = None) -> np.ndarray:
        """Return an RGB overlay for a bright round-cell measurement."""

        selected = measurement
        if selected is None:
            selected = self.analysis.get("bright_round_cell_diameter")
        if not isinstance(selected, dict):
            raise ValueError("Brightfield panel has no bright round-cell measurement to render.")
        image_record, rgb = self._load_image_rgb(str(selected.get("image_uid", "")) or None)
        payload = selected.get("analysis")
        if not isinstance(payload, dict):
            scale = dict(selected.get("measurement_scale_bar", {}))
            payload = {
                "um_per_px": float(scale.get("um_per_px", 0.0) or 0.0),
                "settings": dict(selected.get("params", {})),
                "results": list(selected.get("results", [])),
            }
        return render_bright_round_cell_overlay(rgb, payload)

    def measurement_scale_bar(self, image_uid: str | None = None) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        return resolve_measurement_scale(image_record, dict(image_record.get("measurement_scale_bar", {})))

    def update_measurement_scale_bar(self, image_uid: str | None = None, **updates: Any) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        images = self.images()
        resolved: dict[str, Any] | None = None
        for image in images:
            if str(image.get("uid")) != str(image_record.get("uid")):
                continue
            current = dict(image.get("measurement_scale_bar", {}))
            merged = merge_measurement_scale_updates(current, updates)
            resolved = resolve_measurement_scale(image, merged)
            image["measurement_scale_bar"] = resolved
            break
        if resolved is None:
            raise KeyError(f"Brightfield image not found: {image_uid}")
        layout = self.data.setdefault("brightfield_layout", {})
        from core.layout_scale_bar import seed_layout_scale_presentation

        seed_layout_scale_presentation(
            layout,
            measurement_scale=resolved,
            target_ids=[str(image_record.get("uid", ""))],
            states_key="scale_bars",
            template_key="scale_bar_template",
        )
        self.data["brightfield_images"] = images
        self.append_audit("update_brightfield_measurement_scale_bar", f"{image_record.get('uid')}: {resolved.get('calibration_source')}")
        return dict(resolved)

    def rescale_ai_object_count_measurements(self, image_uid: str | None = None, measurement_scale: dict[str, Any] | None = None) -> None:
        target_uid = str(image_uid or "").strip()
        if not target_uid:
            return
        scale = dict(measurement_scale or self.measurement_scale_bar(target_uid))
        changed = False
        for measurement in self.analysis.get("brightfield_ai_object_count_measurements", []):
            if str(measurement.get("image_uid", "")).strip() != target_uid:
                continue
            measurement["measurement_scale_bar"] = dict(scale)
            measurement["results"] = _rescale_ai_results_with_measurement_scale(dict(measurement.get("results", {})), scale)
            metadata = dict(measurement.get("analysis_metadata", {}))
            metadata["measurement_scale_bar"] = dict(scale)
            measurement["analysis_metadata"] = metadata
            changed = True
        if changed:
            self.analysis.pop("brightfield_ai_object_count_plot_statistics", None)
            self.analysis.pop("brightfield_ai_object_count_statistics", None)

    def persist_he_fiber_analysis_settings(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = self._merge_analysis_settings(settings)
        stored = dict(self.data.get("analysis_settings", {}))
        stored["he_fiber_foreground_sensitivity"] = clamp_he_fiber_sensitivity(
            merged.get("he_fiber_foreground_sensitivity"),
            HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
        )
        stored["he_fiber_splitting_sensitivity"] = clamp_he_fiber_sensitivity(
            merged.get("he_fiber_splitting_sensitivity"),
            HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
        )
        stored["he_fiber_segmentation_method"] = normalize_he_fiber_segmentation_method(
            merged.get("he_fiber_segmentation_method", HE_FIBER_DEFAULT_METHOD)
        )
        stored['he_fiber_cellpose_diameter_px'] = float(merged.get('he_fiber_cellpose_diameter_px', 90.))
        self.data["analysis_settings"] = stored
        return stored

    def _he_fiber_analyze_kwargs(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(settings or {})
        method = normalize_he_fiber_segmentation_method(
            payload.get("he_fiber_segmentation_method", HE_FIBER_DEFAULT_METHOD)
        )
        requested_max = int(payload.get("max_object_area_px", 0) or 0)
        if uses_anti_oversegmentation_he_fiber_path(method) or method in HE_FIBER_CELLPOSE_MODELS:
            max_area_px = requested_max if method in HE_FIBER_CELLPOSE_MODELS else 0
        else:
            max_area_px = requested_max or HE_FIBER_ORIGINAL_DEFAULT_MAX_AREA_PX
        return {
            "foreground_sensitivity": clamp_he_fiber_sensitivity(
                payload.get("he_fiber_foreground_sensitivity"),
                HE_FIBER_DEFAULT_FOREGROUND_SENSITIVITY,
            ),
            "splitting_sensitivity": clamp_he_fiber_sensitivity(
                payload.get("he_fiber_splitting_sensitivity"),
                HE_FIBER_DEFAULT_SPLITTING_SENSITIVITY,
            ),
            "min_area_px": int(payload.get("min_object_area_px", 80) or 80),
            "max_area_px": max_area_px,
            "segmentation_method": method,
            "cellpose_diameter_px": float(payload.get('he_fiber_cellpose_diameter_px', 90.)),
        }

    def he_fiber_area_calibration(self, image_uid: str | None = None) -> dict[str, Any] | None:
        image_record = self._resolve_image_record(image_uid)
        calibrations = dict(self.data.get("he_fiber_area_calibrations", {}))
        calibration = calibrations.get(str(image_record["uid"]))
        return dict(calibration) if isinstance(calibration, dict) else None

    def calibrate_he_fiber_area(self, image_uid: str | None = None) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        calibration = build_auto_he_fiber_calibration(rgb)
        calibrations = dict(self.data.get("he_fiber_area_calibrations", {}))
        calibration_record = {
            **calibration,
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record.get("source_name", "")),
            "calibrated_at": utc_now_iso(),
            "calibration_mode": "auto",
        }
        calibrations[str(image_record["uid"])] = calibration_record
        self.data["he_fiber_area_calibrations"] = calibrations
        self.append_audit("calibrate_brightfield_he_fiber_area", str(image_record["uid"]))
        return calibration_record

    def create_profile_preset_measurement(
        self,
        image_uid: str | None = None,
        *,
        stain_type: str | None = None,
        guided_mode: str = "foolproof",
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        effective_stain_type = stain_type if stain_type is not None else str(image_record.get("stain_type", "H&E"))
        task_profile = self._task_profile_for_stain_type(effective_stain_type)
        profile_settings = self._profile_analysis_settings(task_profile)
        profile_metadata = self._profile_analysis_metadata(task_profile)
        normalized_guided_mode = _normalize_guided_mode(guided_mode)

        measurement = self._create_measurement_with_exact_settings(
            image_uid=str(image_record["uid"]),
            settings=profile_settings,
            roi=roi,
        )
        normalized_stain = self._normalize_stain_type(effective_stain_type)
        measurement["stain_type"] = normalized_stain
        analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        analysis_metadata["stain_type"] = normalized_stain
        measurement["analysis_metadata"] = analysis_metadata
        measurement["guided_mode"] = normalized_guided_mode
        measurement["task_profile"] = task_profile.to_dict()
        measurement["threshold_source"] = str(profile_metadata.get("threshold_source", "profile_preset"))
        self._apply_profile_preset_reliability(measurement, task_profile, effective_stain_type)
        self._apply_analysis_result_fields(
            measurement,
            threshold_source=measurement["threshold_source"],
            metadata=profile_metadata,
            task_profile=task_profile.to_dict(),
        )
        self._apply_guided_effective_settings_metadata(
            measurement,
            batch_state=self._guided_batch_state_for_hash_context(
                stain_type=measurement.get("stain_type"),
                threshold_source=measurement.get("threshold_source"),
                batch_state=dict(self._merge_guided_state(self.data.get("guided_state")).get("batch_threshold_state", {})),
            ),
        )
        self._finalize_guided_preview_status(measurement)

        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        guided_state["guided_mode"] = normalized_guided_mode
        guided_state["task_profile"] = task_profile.to_dict()
        self.data["guided_state"] = guided_state
        self.append_audit("auto_analyze_brightfield_profile", f"{image_record['uid']}; task={task_profile.task_id}")
        return measurement

    def _create_measurement_with_exact_settings(
        self,
        *,
        image_uid: str,
        settings: dict[str, Any],
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior_analysis_settings = dict(self.data.get("analysis_settings", {}))
        try:
            self.data["analysis_settings"] = default_analysis_settings()
            return self.create_measurement(
                image_uid=image_uid,
                settings=settings,
                roi=roi,
            )
        finally:
            self.data["analysis_settings"] = prior_analysis_settings

    def _exact_analysis_settings(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = default_analysis_settings()
        merged.update(dict(settings or {}))
        return merged

    def _resolve_he_auto_analysis_plan(
        self,
        image_uid: str | None = None,
        *,
        settings: dict[str, Any] | None = None,
        guided_mode: str = "foolproof",
    ) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        normalized_guided_mode = _normalize_guided_mode(guided_mode)
        incoming_settings = dict(settings or {})
        if normalized_guided_mode == "assisted":
            requested_threshold_bias = normalize_brightfield_assisted_threshold_bias(
                incoming_settings.get("threshold_bias", guided_state.get("assisted_threshold_bias", 0))
            )
            requested_small_region_filter_bias = normalize_brightfield_assisted_small_region_filter_bias(
                incoming_settings.get(
                    "small_region_filter_bias",
                    guided_state.get("assisted_small_region_filter_bias", 0),
                )
            )
        else:
            requested_threshold_bias = normalize_brightfield_foolproof_threshold_bias(
                incoming_settings.get("threshold_bias", guided_state.get("foolproof_threshold_bias", 0))
            )
            requested_small_region_filter_bias = 0

        if normalized_guided_mode == "assisted":
            sensitivity = str(incoming_settings.get("sensitivity", guided_state.get("sensitivity", "medium")))
            cleanup_level = str(incoming_settings.get("cleanup_level", guided_state.get("cleanup_level", "low")))
        else:
            sensitivity = "medium"
            cleanup_level = "low"

        auto_result = estimate_he_auto_parameters(
            rgb,
            sensitivity=sensitivity,
            cleanup_level=cleanup_level,
        )
        batch_state_payload = dict(guided_state.get("batch_threshold_state") or {})
        batch_state = BatchThresholdState.from_dict(batch_state_payload)
        if not batch_state.task_id:
            batch_state.task_id = BRIGHTFIELD_HE_PROFILE.task_id

        effective_thresholds = dict(auto_result.recommended_thresholds)
        threshold_source = "image_auto"
        threshold_method = str(auto_result.threshold_method)
        threshold_bias_active = normalized_guided_mode in {"foolproof", "assisted"}
        threshold_bias_inactive_reason: str | None = None
        threshold_bias_result = (
            apply_brightfield_assisted_threshold_bias(
                effective_thresholds.get(BRIGHTFIELD_BATCH_THRESHOLD_PARAM, 0.0),
                requested_threshold_bias,
            )
            if normalized_guided_mode == "assisted"
            else apply_brightfield_foolproof_threshold_bias(
                effective_thresholds.get(BRIGHTFIELD_BATCH_THRESHOLD_PARAM, 0.0),
                requested_threshold_bias,
            )
        )
        if batch_state.locked and batch_state.batch_threshold is not None and batch_state.task_id == BRIGHTFIELD_HE_PROFILE.task_id:
            effective_thresholds[BRIGHTFIELD_BATCH_THRESHOLD_PARAM] = float(batch_state.batch_threshold)
            threshold_source = "batch_locked"
            threshold_method = str(batch_state.batch_threshold_method or threshold_method)
            threshold_bias_active = False
            threshold_bias_inactive_reason = "batch_threshold_locked"
        elif normalized_guided_mode in {"foolproof", "assisted"}:
            effective_thresholds[BRIGHTFIELD_BATCH_THRESHOLD_PARAM] = float(threshold_bias_result["adjusted_a_min"])

        baseline_min_area_px = int(auto_result.recommended_min_area or 0)
        small_region_filter_result = (
            apply_brightfield_assisted_small_region_filter_bias(
                baseline_min_area_px,
                requested_small_region_filter_bias,
            )
            if normalized_guided_mode == "assisted"
            else {
                "small_region_filter_bias": 0,
                "baseline_min_area_px": baseline_min_area_px,
                "min_area_scale_factor": 1.0,
                "effective_min_area_px": baseline_min_area_px,
            }
        )
        effective_settings = self._merge_analysis_settings(
            {
                **effective_thresholds,
                "min_object_area_px": int(small_region_filter_result["effective_min_area_px"]),
                "tissue_mask_enabled": bool(auto_result.recommended_tissue_mask_settings.get("enabled", False)),
                "tissue_mask_value_threshold": auto_result.recommended_tissue_mask_settings.get("value_threshold"),
                "tissue_mask_saturation_threshold": auto_result.recommended_tissue_mask_settings.get("saturation_threshold"),
                "tissue_mask_min_area_px": auto_result.recommended_tissue_mask_settings.get("min_area_px"),
            }
        )
        slider_values = (
            build_brightfield_assisted_postprocess_slider_values(
                threshold_bias=requested_threshold_bias,
                active=threshold_bias_active,
                inactive_reason=threshold_bias_inactive_reason,
                small_region_filter_bias=requested_small_region_filter_bias,
                baseline_min_area_px=baseline_min_area_px,
                effective_min_area_px=small_region_filter_result["effective_min_area_px"],
            )
            if normalized_guided_mode == "assisted"
            else build_brightfield_foolproof_slider_values(
                threshold_bias=requested_threshold_bias,
                active=threshold_bias_active,
                inactive_reason=threshold_bias_inactive_reason,
            )
            if normalized_guided_mode == "foolproof"
            else neutral_brightfield_guided_slider_values()
        )
        return {
            "image_record": image_record,
            "rgb": rgb,
            "guided_state": guided_state,
            "normalized_guided_mode": normalized_guided_mode,
            "sensitivity": sensitivity,
            "cleanup_level": cleanup_level,
            "auto_result": auto_result,
            "batch_state_payload": batch_state_payload,
            "batch_state": batch_state,
            "effective_settings": effective_settings,
            "threshold_source": threshold_source,
            "threshold_method": threshold_method,
            "slider_values": slider_values,
            "threshold_bias_result": threshold_bias_result,
            "small_region_filter_result": small_region_filter_result,
        }

    def auto_analyze_he(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
        guided_mode: str = "foolproof",
    ) -> dict[str, Any]:
        plan = self._resolve_he_auto_analysis_plan(
            image_uid=image_uid,
            settings=settings,
            guided_mode=guided_mode,
        )
        image_record = dict(plan["image_record"])
        rgb = np.asarray(plan["rgb"])
        guided_state = dict(plan["guided_state"])
        normalized_guided_mode = str(plan["normalized_guided_mode"])
        sensitivity = str(plan["sensitivity"])
        cleanup_level = str(plan["cleanup_level"])
        auto_result = plan["auto_result"]
        batch_state_payload = dict(plan["batch_state_payload"])
        batch_state = plan["batch_state"]
        effective_settings = dict(plan["effective_settings"])
        threshold_source = str(plan["threshold_source"])
        threshold_method = str(plan["threshold_method"])
        slider_values = dict(plan["slider_values"])
        threshold_bias_result = dict(plan["threshold_bias_result"])
        small_region_filter_result = dict(plan["small_region_filter_result"])

        measurement = self.create_measurement(
            image_uid=str(image_record["uid"]),
            settings=effective_settings,
            roi=roi,
        )
        analysis_mask = self._cached_brightfield_analysis_mask(measurement)
        auto_result_payload = auto_result.to_dict()
        auto_result_payload["recommended_thresholds"][BRIGHTFIELD_BATCH_THRESHOLD_PARAM] = float(
            effective_settings[BRIGHTFIELD_BATCH_THRESHOLD_PARAM]
        )
        auto_result_payload["metadata"]["threshold_source"] = threshold_source
        auto_result_payload["metadata"]["applied_threshold_method"] = threshold_method
        if normalized_guided_mode == "foolproof":
            auto_result_payload["metadata"]["foolproof_threshold_bias_mapping_version"] = (
                BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION
            )
            auto_result_payload["metadata"]["foolproof_threshold_bias"] = int(slider_values.get("threshold_bias", 0))
            auto_result_payload["metadata"]["foolproof_threshold_bias_active"] = bool(
                slider_values.get("threshold_bias_active", False)
            )
            auto_result_payload["metadata"]["foolproof_threshold_bias_inactive_reason"] = slider_values.get(
                "threshold_bias_inactive_reason"
            )
            auto_result_payload["metadata"]["foolproof_baseline_a_min"] = float(threshold_bias_result["baseline_a_min"])
            auto_result_payload["metadata"]["foolproof_delta_a_min"] = float(threshold_bias_result["delta_a_min"])
        else:
            for key in BRIGHTFIELD_FOOLPROOF_METADATA_KEYS:
                auto_result_payload["metadata"].pop(key, None)
        if normalized_guided_mode == "assisted":
            auto_result_payload["metadata"]["assisted_threshold_bias"] = int(slider_values.get("threshold_bias", 0))
            auto_result_payload["metadata"]["assisted_threshold_bias_active"] = bool(
                slider_values.get("threshold_bias_active", False)
            )
            auto_result_payload["metadata"]["assisted_threshold_bias_inactive_reason"] = slider_values.get(
                "threshold_bias_inactive_reason"
            )
            auto_result_payload["metadata"]["assisted_small_region_filter_bias"] = int(
                small_region_filter_result["small_region_filter_bias"]
            )
            auto_result_payload["metadata"]["assisted_small_region_filter_mapping_version"] = (
                BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
            )
            auto_result_payload["metadata"]["assisted_baseline_min_area_px"] = int(
                small_region_filter_result["baseline_min_area_px"]
            )
            auto_result_payload["metadata"]["assisted_effective_min_area_px"] = int(
                small_region_filter_result["effective_min_area_px"]
            )
        else:
            for key in BRIGHTFIELD_ASSISTED_METADATA_KEYS:
                auto_result_payload["metadata"].pop(key, None)
        if threshold_source == "batch_locked" and batch_state.batch_threshold is not None:
            auto_result_payload["metadata"]["batch_threshold"] = float(batch_state.batch_threshold)
            auto_result_payload["metadata"]["batch_threshold_parameter"] = BRIGHTFIELD_BATCH_THRESHOLD_PARAM

        if normalized_guided_mode == "assisted":
            measurement["params"]["sensitivity"] = sensitivity
            measurement["params"]["cleanup_level"] = cleanup_level
        measurement["guided_mode"] = normalized_guided_mode
        measurement["task_profile"] = BRIGHTFIELD_HE_PROFILE.to_dict()
        measurement["threshold_source"] = threshold_source
        measurement["auto_parameter_result"] = auto_result_payload
        measurement["qc_messages"] = list(auto_result.qc_warnings)
        metadata = {
            "threshold_method": threshold_method,
            "confidence_label": auto_result.confidence_label,
            "confidence_score": auto_result.confidence_score,
        }
        if threshold_source == "batch_locked":
            batch_lock_source_mode = str(batch_state_payload.get(BRIGHTFIELD_BATCH_LOCK_SOURCE_MODE_KEY, "")).strip()
            metadata.update(
                {
                    "batch_lock_applied": True,
                    "batch_threshold_source_image": batch_state.batch_threshold_source_image,
                    "batch_threshold_method": str(batch_state.batch_threshold_method or threshold_method),
                    "batch_lock_source_mode": _normalize_guided_mode(batch_lock_source_mode) if batch_lock_source_mode else None,
                }
            )
        if normalized_guided_mode == "foolproof":
            metadata.update(
                {
                    "foolproof_threshold_bias_mapping_version": BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION,
                    "foolproof_threshold_bias": int(slider_values.get("threshold_bias", 0)),
                    "foolproof_threshold_bias_active": bool(slider_values.get("threshold_bias_active", False)),
                    "foolproof_threshold_bias_inactive_reason": slider_values.get("threshold_bias_inactive_reason"),
                    "foolproof_baseline_a_min": float(threshold_bias_result["baseline_a_min"]),
                    "foolproof_delta_a_min": float(threshold_bias_result["delta_a_min"]),
                }
            )
        else:
            for key in BRIGHTFIELD_FOOLPROOF_METADATA_KEYS:
                metadata.pop(key, None)
        if normalized_guided_mode == "assisted":
            metadata.update(
                {
                    "assisted_threshold_bias": int(slider_values.get("threshold_bias", 0)),
                    "assisted_threshold_bias_active": bool(slider_values.get("threshold_bias_active", False)),
                    "assisted_threshold_bias_inactive_reason": slider_values.get("threshold_bias_inactive_reason"),
                    "assisted_small_region_filter_bias": int(small_region_filter_result["small_region_filter_bias"]),
                    "assisted_small_region_filter_mapping_version": BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
                    "assisted_baseline_min_area_px": int(small_region_filter_result["baseline_min_area_px"]),
                    "assisted_effective_min_area_px": int(small_region_filter_result["effective_min_area_px"]),
                }
            )
        else:
            for key in BRIGHTFIELD_ASSISTED_METADATA_KEYS:
                metadata.pop(key, None)
        self._apply_analysis_result_fields(
            measurement,
            threshold_source=threshold_source,
            warnings=list(auto_result.qc_warnings),
            metadata=metadata,
        )
        resolved_batch_state = self._batch_threshold_state_payload(batch_state, batch_state_payload)
        self._apply_guided_effective_settings_metadata(
            measurement,
            batch_state=resolved_batch_state,
            slider_values=slider_values,
            mapping_version=(
                BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION
                if normalized_guided_mode == "foolproof"
                else BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
                if normalized_guided_mode == "assisted"
                else None
            ),
        )
        self._finalize_guided_preview_status(measurement)

        guided_state["guided_mode"] = normalized_guided_mode
        guided_state["task_profile"] = BRIGHTFIELD_HE_PROFILE.to_dict()
        if normalized_guided_mode == "foolproof":
            guided_state["foolproof_threshold_bias"] = int(slider_values.get("threshold_bias", 0))
        if normalized_guided_mode == "assisted":
            guided_state["assisted_threshold_bias"] = int(slider_values.get("threshold_bias", 0))
            guided_state["assisted_small_region_filter_bias"] = int(
                small_region_filter_result["small_region_filter_bias"]
            )
        if normalized_guided_mode == "assisted":
            guided_state["sensitivity"] = sensitivity
            guided_state["cleanup_level"] = cleanup_level
        guided_state["batch_threshold_state"] = resolved_batch_state
        self.data["guided_state"] = guided_state
        if threshold_source == "batch_locked":
            self.append_audit(
                "brightfield_batch_threshold_apply",
                f"measurement_uuid={measurement['measurement_uuid']}; "
                f"image_uid={image_record['uid']}; "
                f"source_image={batch_state.batch_threshold_source_image or 'unknown'}; "
                f"{BRIGHTFIELD_BATCH_THRESHOLD_PARAM}={float(batch_state.batch_threshold or 0.0):.3f}; "
                f"mode={normalized_guided_mode}",
            )
        if analysis_mask is not None:
            self._cache_brightfield_analysis_mask(measurement, analysis_mask)
        self.append_audit("auto_analyze_brightfield_he", f"{image_record['uid']}; mode=lab")
        return measurement

    def measurement_supports_batch_threshold(self, measurement: dict[str, Any] | None) -> bool:
        if not isinstance(measurement, dict):
            return False
        task_id = str(
            dict(measurement.get("task_profile", {})).get("task_id")
            or dict(measurement.get("analysis_metadata", {})).get("task_id", "")
        ).strip()
        if task_id != BRIGHTFIELD_HE_PROFILE.task_id:
            return False
        if self._normalize_stain_type(str(measurement.get("stain_type", ""))) != "H&E":
            return False
        params = dict(measurement.get("params", {}))
        if str(params.get("segmentation_mode", "")).strip().lower() != "lab":
            return False
        return BRIGHTFIELD_BATCH_THRESHOLD_PARAM in params

    def apply_batch_threshold_from_measurement(
        self,
        measurement_uuid: str | None = None,
        *,
        lock: bool | None = None,
    ) -> dict[str, Any]:
        if lock is not True:
            raise ValueError(
                "Batch threshold reference without lock is no longer supported. "
                "Use lock=True to lock the current H&E LAB-mode measurement."
            )
        measurement = self._resolve_measurement(measurement_uuid=measurement_uuid)
        active_image = self.active_image()
        if active_image is None:
            raise ValueError("Brightfield panel has no active image.")
        if str(measurement.get("image_uid", "")).strip() != str(active_image.get("uid", "")).strip():
            raise ValueError("Brightfield batch threshold can only use the selected measurement for the active image.")
        if not self.measurement_supports_batch_threshold(measurement):
            raise ValueError("Brightfield batch threshold requires an H&E LAB measurement for the active image.")

        params = dict(measurement.get("params", {}))
        threshold_value = float(params[BRIGHTFIELD_BATCH_THRESHOLD_PARAM])
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        existing_batch_state = dict(guided_state.get("batch_threshold_state") or {})
        batch_state = BatchThresholdState.from_dict(existing_batch_state)
        if not batch_state.task_id:
            batch_state.task_id = BRIGHTFIELD_HE_PROFILE.task_id
        batch_state.batch_threshold = threshold_value
        batch_state.batch_threshold_source_image = str(measurement.get("image_uid", "")).strip() or None
        batch_state.batch_threshold_method = str(
            dict(measurement.get("analysis_metadata", {})).get("threshold_method")
            or dict(measurement.get("auto_parameter_result", {})).get("threshold_method", "")
        ).strip() or None
        batch_state.locked = True
        batch_state_payload = self._batch_threshold_state_payload(
            batch_state,
            existing_batch_state,
            source_mode=str(measurement.get("guided_mode", guided_state.get("guided_mode", "foolproof"))),
        )
        guided_state["batch_threshold_state"] = batch_state_payload
        self.data["guided_state"] = guided_state

        self.append_audit(
            "brightfield_batch_threshold_lock",
            f"measurement_uuid={measurement['measurement_uuid']}; "
            f"image_uid={measurement['image_uid']}; "
            f"{BRIGHTFIELD_BATCH_THRESHOLD_PARAM}={threshold_value:.3f}",
        )
        return batch_state_payload

    def unlock_batch_threshold(self) -> dict[str, Any]:
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        existing_batch_state = dict(guided_state.get("batch_threshold_state") or {})
        batch_state = BatchThresholdState.from_dict(existing_batch_state)
        if not batch_state.task_id:
            batch_state.task_id = BRIGHTFIELD_HE_PROFILE.task_id
        prior_threshold = batch_state.batch_threshold
        prior_source_image = batch_state.batch_threshold_source_image
        prior_method = batch_state.batch_threshold_method
        batch_state.locked = False
        batch_state.batch_threshold = None
        batch_state.batch_threshold_source_image = None
        batch_state.batch_threshold_method = None
        batch_state_payload = self._batch_threshold_state_payload(batch_state, existing_batch_state)
        guided_state["batch_threshold_state"] = batch_state_payload
        self.data["guided_state"] = guided_state
        threshold_text = "none" if prior_threshold is None else f"{prior_threshold:.3f}"
        prior_method_text = str(prior_method or "none")
        self.append_audit(
            "brightfield_batch_threshold_unlock",
            f"image_uid={prior_source_image or 'none'}; "
            f"{BRIGHTFIELD_BATCH_THRESHOLD_PARAM}={threshold_text}; "
            f"method={prior_method_text}",
        )
        return batch_state_payload

    def render_measurement_overlay(
        self,
        image_uid: str | None = None,
        measurement_uuid: str | None = None,
    ) -> np.ndarray:
        measurement = self._resolve_measurement(image_uid=image_uid, measurement_uuid=measurement_uuid)
        target_image_uid = str(measurement["image_uid"])
        if str(dict(measurement.get("analysis_metadata", {})).get("analysis_kind", "")).strip() == "he_fiber_area":
            _image_record, rgb = self._load_image_rgb(target_image_uid)
            labels = self._he_fiber_area_labels_for_measurement(measurement)
            return render_he_fiber_area_overlay(rgb, labels, color=(0, 244, 255), alpha=0.35)
        cached_mask = self._cached_brightfield_analysis_mask(measurement)
        if cached_mask is not None:
            _image_record, rgb = self._load_image_rgb(target_image_uid)
            if cached_mask.shape == rgb.shape[:2]:
                return render_mask_overlay(rgb, cached_mask, color=(255, 0, 180), alpha=0.35)
            self._brightfield_analysis_mask_cache.pop(str(measurement.get("measurement_uuid", "")).strip(), None)
        image_record, rgb = self._load_image_rgb(target_image_uid)
        settings = self._merge_analysis_settings(dict(measurement.get('params', {})))
        roi = self._resolve_roi_snapshot(image_record, dict(measurement.get('roi', {})) or None)
        analysis = analyze_brightfield_image(rgb, settings, roi_mask=self._build_roi_mask(rgb.shape[:2], roi))
        self._cache_brightfield_analysis_mask(measurement, analysis['mask'])
        return render_mask_overlay(rgb, analysis['mask'], color=(255, 0, 180), alpha=0.35)

    def _brightfield_analysis_mask_cache_key(self, measurement: dict[str, Any]) -> str:
        image_uid = str(measurement.get("image_uid", "")).strip()
        image_record = self._resolve_image_record(image_uid)
        return sha256_json(
            {
                "measurement_uuid": str(measurement.get("measurement_uuid", "")).strip(),
                "source": {
                    "image_uid": image_uid,
                    "source_hash": str(image_record.get("source_hash", "")),
                    "source_path": str(image_record.get("source_path", "")),
                },
                "params": dict(measurement.get("params", {})),
                "roi": dict(measurement.get("roi", {})),
            }
        )

    def _cache_brightfield_analysis_mask(self, measurement: dict[str, Any], mask: Any) -> None:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        if not measurement_uuid:
            return
        cached_mask = np.asarray(mask, dtype=bool).copy()
        if cached_mask.ndim != 2:
            return
        self._brightfield_analysis_mask_cache.pop(measurement_uuid, None)
        self._brightfield_analysis_mask_cache[measurement_uuid] = {
            "cache_key": self._brightfield_analysis_mask_cache_key(measurement),
            "mask": cached_mask,
        }
        self._trim_brightfield_analysis_mask_cache()

    def _cached_brightfield_analysis_mask(self, measurement: dict[str, Any]) -> np.ndarray | None:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        if not measurement_uuid:
            return None
        cached = self._brightfield_analysis_mask_cache.get(measurement_uuid)
        if not cached or str(cached.get("cache_key", "")) != self._brightfield_analysis_mask_cache_key(measurement):
            self._brightfield_analysis_mask_cache.pop(measurement_uuid, None)
            return None
        mask = np.asarray(cached.get("mask"), dtype=bool)
        if mask.ndim != 2:
            self._brightfield_analysis_mask_cache.pop(measurement_uuid, None)
            return None
        self._brightfield_analysis_mask_cache.pop(measurement_uuid, None)
        self._brightfield_analysis_mask_cache[measurement_uuid] = cached
        return mask

    def _merge_brightfield_analysis_mask_cache(self, cache: dict[str, dict[str, Any]]) -> None:
        for measurement_uuid, entry in cache.items():
            key = str(measurement_uuid).strip()
            if not key or not isinstance(entry, dict):
                continue
            self._brightfield_analysis_mask_cache.pop(key, None)
            self._brightfield_analysis_mask_cache[key] = entry
        self._trim_brightfield_analysis_mask_cache()

    def _trim_brightfield_analysis_mask_cache(self) -> None:
        while len(self._brightfield_analysis_mask_cache) > self._ANALYSIS_MASK_CACHE_LIMIT:
            self._brightfield_analysis_mask_cache.pop(next(iter(self._brightfield_analysis_mask_cache)))

    def render_segmentation_overlay(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> np.ndarray:
        image_record, rgb = self._load_image_rgb(image_uid)
        merged_settings = self._merge_analysis_settings(settings)
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        analysis = analyze_brightfield_image(rgb, merged_settings, roi_mask=roi_mask)
        return render_mask_overlay(rgb, analysis["mask"], color=(255, 0, 180), alpha=0.35)

    def build_guided_preview(
        self,
        *,
        image_uid: str | None = None,
        stain_type: str | None = None,
        guided_mode: str = "foolproof",
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_guided_mode = _normalize_guided_mode(guided_mode)
        image_record = self._resolve_image_record(image_uid)
        effective_stain_type = self._normalize_stain_type(stain_type or str(image_record.get("stain_type", "H&E")))
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        if normalized_guided_mode == "expert":
            _resolved_image_record, rgb = self._load_image_rgb(str(image_record["uid"]))
            merged_settings = self._merge_analysis_settings(settings)
            roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
            analysis = analyze_brightfield_image(rgb, merged_settings, roi_mask=roi_mask)
            overlay = render_mask_overlay(rgb, analysis["mask"], color=(255, 0, 180), alpha=0.35)
            self._transient_preview_state = {}
            return {
                "image_uid": str(image_record["uid"]),
                "image_name": str(image_record.get("source_name", "")),
                "overlay": overlay,
                "roi": roi_snapshot,
                "params": merged_settings,
                "analysis": analysis,
                "guided_mode": normalized_guided_mode,
                "stain_type": effective_stain_type,
                "preview_hash": sha256_json(
                    {
                        "guided_mode": normalized_guided_mode,
                        "stain_type": effective_stain_type,
                        "params": merged_settings,
                        "roi": roi_snapshot,
                    }
                ),
            }
        if effective_stain_type == "H&E":
            plan = self._resolve_he_auto_analysis_plan(
                image_uid=str(image_record["uid"]),
                settings=settings,
                guided_mode=normalized_guided_mode,
            )
            rgb = np.asarray(plan["rgb"])
            effective_settings = dict(plan["effective_settings"])
            threshold_source = str(plan["threshold_source"])
            threshold_method = str(plan["threshold_method"])
            slider_values = dict(plan["slider_values"])
            batch_state_payload = dict(plan["batch_state_payload"])
            task_profile = BRIGHTFIELD_HE_PROFILE.to_dict()
            if normalized_guided_mode == "assisted":
                effective_settings["sensitivity"] = str(plan["sensitivity"])
                effective_settings["cleanup_level"] = str(plan["cleanup_level"])
        else:
            if normalized_guided_mode == "assisted":
                raise ValueError(
                    "Assisted Brightfield auto-analysis for non-H&E stains is not available in this phase."
                )
            _resolved_image_record, rgb = self._load_image_rgb(str(image_record["uid"]))
            task_profile_model = self._task_profile_for_stain_type(effective_stain_type)
            task_profile = task_profile_model.to_dict()
            effective_settings = self._exact_analysis_settings(self._profile_analysis_settings(task_profile_model))
            profile_metadata = self._profile_analysis_metadata(task_profile_model)
            threshold_source = "profile_preset"
            threshold_method = str(profile_metadata.get("threshold_method", "")).strip()
            slider_values = neutral_brightfield_guided_slider_values()
            batch_state_payload = self._guided_batch_state_for_hash_context(
                stain_type=effective_stain_type,
                threshold_source=threshold_source,
                batch_state=dict(self._merge_guided_state(self.data.get("guided_state")).get("batch_threshold_state", {})),
            )

        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        analysis = analyze_brightfield_image(rgb, effective_settings, roi_mask=roi_mask)
        overlay = render_mask_overlay(rgb, analysis["mask"], color=(255, 0, 180), alpha=0.35)
        analysis_metadata = {
            "task_id": str(task_profile.get("task_id", "")),
            "threshold_source": threshold_source,
            "threshold_method": threshold_method,
            "guided_mode": normalized_guided_mode,
            "stain_type": effective_stain_type,
        }
        analysis_metadata.update(dict(analysis.get("analysis_metadata", {})))
        guided_metadata = package_brightfield_guided_measurement_metadata(
            guided_mode=normalized_guided_mode,
            task_profile=task_profile,
            stain_type=effective_stain_type,
            image_context={
                "image_uid": image_record.get("uid"),
                "source_name": image_record.get("source_name"),
                "source_digest": image_record.get("source_hash"),
            },
            threshold_source=threshold_source,
            threshold_method=threshold_method,
            effective_settings=effective_settings,
            slider_values=slider_values,
            roi_snapshot=roi_snapshot,
            analysis_metadata=analysis_metadata,
            batch_state=batch_state_payload,
            baseline_source=threshold_source,
            preview_status=BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS,
            mapping_version=(
                BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION
                if normalized_guided_mode == "foolproof" and effective_stain_type == "H&E"
                else BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
                if normalized_guided_mode == "assisted" and effective_stain_type == "H&E"
                else None
            )
            or None,
        )
        self._transient_preview_state = {
            "image_uid": str(image_record["uid"]),
            "guided_mode": normalized_guided_mode,
            "stain_type": effective_stain_type,
            "guided_effective_settings_hash": guided_metadata["guided_effective_settings_hash"],
        }
        return {
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record.get("source_name", "")),
            "overlay": overlay,
            "roi": roi_snapshot,
            "params": effective_settings,
            "analysis": analysis,
            "guided_mode": normalized_guided_mode,
            "stain_type": effective_stain_type,
            "guided_metadata": guided_metadata,
            "preview_hash": guided_metadata["guided_effective_settings_hash"],
        }

    def measurement_by_uuid(self, measurement_uuid: str) -> dict[str, Any] | None:
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return None
        for measurement in self.analysis.get("brightfield_measurements", []):
            if str(measurement.get("measurement_uuid", "")).strip() == target_uuid:
                return measurement
        for measurement in self.analysis.get("brightfield_ai_object_count_measurements", []):
            if str(measurement.get("measurement_uuid", "")).strip() == target_uuid:
                return measurement
        for measurement in self.analysis.get("bright_round_cell_diameter_measurements", []):
            if str(measurement.get("measurement_uuid", "")).strip() == target_uuid:
                return measurement
        for image in self.images():
            for measurement in image.get("measurements", []):
                if str(measurement.get("measurement_uuid", "")).strip() == target_uuid:
                    return measurement
        return None

    def _read_raster(self, source: Path) -> QImage:
        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise ValueError(f"Could not load Brightfield raster image: {source}")
        return image

    def _resolve_image_record(self, image_uid: str | None = None) -> dict[str, Any]:
        if image_uid is None:
            image_record = self.active_image()
            if image_record is None:
                raise ValueError("Brightfield panel has no active image.")
            return image_record

        target_uid = str(image_uid)
        for image in self.images():
            if image.get("uid") == target_uid:
                return image
        raise ValueError(f"Brightfield image not found: {target_uid}")

    def _load_image_rgb(self, image_uid: str | None = None) -> tuple[dict[str, Any], np.ndarray]:
        image_record = self._resolve_image_record(image_uid)
        rgb, _ = read_rgb_image(Path(str(image_record.get("source_path", ""))))
        return image_record, rgb

    def _build_import_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        source_metadata = dict(metadata or {})
        magnification = source_metadata.get("objective")
        payload = {
            "width_px": int(source_metadata.get("width_px", 0) or 0),
            "height_px": int(source_metadata.get("height_px", 0) or 0),
            "pixel_size_um": source_metadata.get("pixel_size_um"),
            "magnification": str(magnification) if magnification else None,
            "source_format": str(source_metadata.get("source_format", "")),
            "n_channels": int(source_metadata.get("n_channels", 0) or 0),
            "pixel_size_unit": str(source_metadata.get("pixel_size_unit", "um") or "um"),
            "bit_depth": int(source_metadata.get("bit_depth", 0) or 0),
            "axes": str(source_metadata.get("axes", "CYX") or "CYX"),
            "work_data_downsample": int(source_metadata.get("work_data_downsample", 1) or 1),
            "io_warnings": [str(item) for item in source_metadata.get("io_warnings", [])],
        }
        if "pixel_size_x_um" in source_metadata:
            payload["pixel_size_x_um"] = source_metadata.get("pixel_size_x_um")
        if "pixel_size_y_um" in source_metadata:
            payload["pixel_size_y_um"] = source_metadata.get("pixel_size_y_um")
        return payload

    def _merge_analysis_settings(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = default_analysis_settings()
        merged.update(dict(self.data.get("analysis_settings", {})))
        if settings:
            merged.update(dict(settings))
        return merged

    def _merge_guided_state(self, guided_state: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = default_guided_state()
        existing = dict(guided_state or {})
        for key, value in existing.items():
            if key not in {"task_profile", "batch_threshold_state"}:
                merged[key] = value
        merged["guided_mode"] = _normalize_guided_mode(merged.get("guided_mode"))
        profile = dict(existing.get("task_profile", {}))
        if str(profile.get("task_id", "")).strip():
            merged["task_profile"] = dict(profile)
        existing_batch_state = dict(existing.get("batch_threshold_state") or {})
        batch_state = BatchThresholdState.from_dict(existing_batch_state or merged.get("batch_threshold_state"))
        if not batch_state.task_id:
            batch_state.task_id = BRIGHTFIELD_HE_PROFILE.task_id
        merged["batch_threshold_state"] = self._batch_threshold_state_payload(batch_state, existing_batch_state)
        return merged

    def _batch_threshold_state_payload(
        self,
        batch_state: BatchThresholdState,
        existing_payload: dict[str, Any] | None = None,
        *,
        source_mode: str | None = None,
    ) -> dict[str, Any]:
        payload = batch_state.to_dict()
        existing_mode = str(dict(existing_payload or {}).get(BRIGHTFIELD_BATCH_LOCK_SOURCE_MODE_KEY, "")).strip()
        if existing_mode:
            payload[BRIGHTFIELD_BATCH_LOCK_SOURCE_MODE_KEY] = _normalize_guided_mode(existing_mode)
        if source_mode is not None:
            normalized_source_mode = _normalize_guided_mode(source_mode)
            if normalized_source_mode:
                payload[BRIGHTFIELD_BATCH_LOCK_SOURCE_MODE_KEY] = normalized_source_mode
        return payload

    def _normalize_brightfield_measurement_collections(self) -> None:
        measurements = list(self.analysis.get("brightfield_measurements", []))
        normalized_by_uuid: dict[str, dict[str, Any]] = {}
        normalized_measurements: list[dict[str, Any]] = []
        for measurement in measurements:
            if not isinstance(measurement, dict):
                continue
            normalized = self._normalize_brightfield_measurement_semantics(measurement)
            normalized_measurements.append(normalized)
            measurement_uuid = str(normalized.get("measurement_uuid", "")).strip()
            if measurement_uuid:
                normalized_by_uuid[measurement_uuid] = normalized
        self.analysis["brightfield_measurements"] = normalized_measurements

        images = self.images()
        for image in images:
            rows: list[dict[str, Any]] = []
            for measurement in list(image.get("measurements", [])):
                if not isinstance(measurement, dict):
                    continue
                measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
                rows.append(
                    normalized_by_uuid.get(measurement_uuid)
                    or self._normalize_brightfield_measurement_semantics(measurement)
                )
            image["measurements"] = rows
        self.data["brightfield_images"] = images

    def _resolve_threshold_source_for_measurement(
        self,
        measurement: dict[str, Any],
        analysis_metadata: dict[str, Any],
    ) -> str:
        explicit_source = (
            _coerce_threshold_source(measurement.get("threshold_source"))
            or _coerce_threshold_source(analysis_metadata.get("threshold_source"))
        )
        if explicit_source:
            return explicit_source

        explicit_analysis_mode = _coerce_analysis_mode(analysis_metadata.get("analysis_mode"))
        explicit_guided_mode = _coerce_guided_mode(measurement.get("guided_mode")) or _coerce_guided_mode(
            analysis_metadata.get("guided_mode")
        )
        auto_metadata = dict(dict(measurement.get("auto_parameter_result", {})).get("metadata", {}))
        auto_source = _coerce_threshold_source(auto_metadata.get("threshold_source"))
        if auto_source:
            return auto_source
        if explicit_analysis_mode == "profile_guided_foolproof":
            return "profile_preset"
        if explicit_analysis_mode == "expert" or explicit_guided_mode == "expert":
            return "manual"
        if dict(measurement.get("auto_parameter_result", {})):
            if auto_metadata.get("batch_threshold") is not None:
                return "batch_locked"
            return "image_auto"
        if str(measurement.get("model_name", "")).strip().endswith("_manual"):
            return "manual"
        return BRIGHTFIELD_LEGACY_UNKNOWN

    def _resolve_guided_mode_for_measurement(
        self,
        measurement: dict[str, Any],
        analysis_metadata: dict[str, Any],
        *,
        threshold_source: str,
    ) -> str:
        if threshold_source == "manual":
            return "expert"
        if threshold_source == "profile_preset":
            return "foolproof"

        explicit_guided_mode = _coerce_guided_mode(measurement.get("guided_mode")) or _coerce_guided_mode(
            analysis_metadata.get("guided_mode")
        )
        explicit_analysis_mode = _coerce_analysis_mode(analysis_metadata.get("analysis_mode"))
        if explicit_guided_mode in {"foolproof", "assisted"}:
            return explicit_guided_mode
        if explicit_analysis_mode == "guided_assisted":
            return "assisted"
        if explicit_analysis_mode in {"guided_foolproof", "profile_guided_foolproof"}:
            return "foolproof"
        return BRIGHTFIELD_LEGACY_UNKNOWN

    def _resolve_analysis_mode_for_measurement(
        self,
        analysis_metadata: dict[str, Any],
        *,
        threshold_source: str,
        guided_mode: str,
    ) -> str:
        if threshold_source == "manual":
            return "expert"
        if threshold_source == "profile_preset":
            return "profile_guided_foolproof"
        if guided_mode == "assisted":
            return "guided_assisted"
        if guided_mode == "foolproof":
            return "guided_foolproof"
        explicit_analysis_mode = _coerce_analysis_mode(analysis_metadata.get("analysis_mode"))
        if explicit_analysis_mode in {"guided_assisted", "guided_foolproof", "profile_guided_foolproof"}:
            return explicit_analysis_mode
        return BRIGHTFIELD_LEGACY_UNKNOWN

    def _normalize_brightfield_measurement_semantics(self, measurement: dict[str, Any]) -> dict[str, Any]:
        analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        threshold_source = self._resolve_threshold_source_for_measurement(measurement, analysis_metadata)
        guided_mode = self._resolve_guided_mode_for_measurement(
            measurement,
            analysis_metadata,
            threshold_source=threshold_source,
        )
        analysis_mode = self._resolve_analysis_mode_for_measurement(
            analysis_metadata,
            threshold_source=threshold_source,
            guided_mode=guided_mode,
        )
        segmentation_mode = str(
            dict(measurement.get("params", {})).get("segmentation_mode")
            or dict(measurement.get("results", {})).get("segmentation_mode")
            or analysis_metadata.get("segmentation_mode")
            or BRIGHTFIELD_LEGACY_UNKNOWN
        ).strip() or BRIGHTFIELD_LEGACY_UNKNOWN
        if "tissue_mask_enabled" in dict(measurement.get("params", {})):
            tissue_mask_enabled: bool | str = bool(dict(measurement.get("params", {})).get("tissue_mask_enabled"))
        elif isinstance(analysis_metadata.get("tissue_mask_enabled"), bool):
            tissue_mask_enabled = bool(analysis_metadata.get("tissue_mask_enabled"))
        elif isinstance(analysis_metadata.get("tissue_mask_applied"), bool):
            tissue_mask_enabled = bool(analysis_metadata.get("tissue_mask_applied"))
        else:
            tissue_mask_enabled = BRIGHTFIELD_LEGACY_UNKNOWN

        normalized_stain = self._normalize_stain_type(str(measurement.get("stain_type", "H&E")))
        profile_payload = dict(measurement.get("task_profile", {}))
        if not str(profile_payload.get("task_id", "")).strip():
            profile_payload = self._task_profile_for_stain_type(normalized_stain).to_dict()
        task_id = (
            str(profile_payload.get("task_id", "")).strip()
            or str(analysis_metadata.get("task_id", "")).strip()
            or BRIGHTFIELD_HE_PROFILE.task_id
        )
        analysis_metadata.update(
            {
                "analysis_kind": "brightfield_measurement",
                "task_id": task_id,
                "profile_id": str(analysis_metadata.get("profile_id") or task_id),
                "analysis_mode": analysis_mode,
                "guided_mode": guided_mode,
                "stain_type": normalized_stain,
                "threshold_source": threshold_source,
                "roi_mode": str(dict(measurement.get("roi", {})).get("mode", analysis_metadata.get("roi_mode", "whole_image"))),
                "segmentation_mode": segmentation_mode,
                "tissue_mask_enabled": tissue_mask_enabled,
                "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
                "object_count_semantics": BRIGHTFIELD_OBJECT_COUNT_SEMANTICS,
                "measurement_uuid": str(
                    analysis_metadata.get("measurement_uuid") or measurement.get("measurement_uuid", "")
                ).strip(),
                "image_uid": str(analysis_metadata.get("image_uid") or measurement.get("image_uid", "")).strip(),
            }
        )
        measurement["guided_mode"] = guided_mode
        measurement["threshold_source"] = threshold_source
        measurement["stain_type"] = normalized_stain
        measurement["analysis_metadata"] = analysis_metadata
        return measurement

    def _apply_analysis_result_fields(
        self,
        measurement: dict[str, Any],
        *,
        threshold_source: str,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        task_profile: dict[str, Any] | None = None,
    ) -> None:
        source = str(threshold_source).strip() or "manual"
        profile_payload = dict(task_profile or measurement.get("task_profile", {}))
        normalized_stain = self._normalize_stain_type(str(measurement.get("stain_type", "H&E")))
        if not profile_payload:
            profile_payload = self._task_profile_for_stain_type(normalized_stain).to_dict()
        task_id = (
            str(profile_payload.get("task_id", "")).strip()
            or str(dict(measurement.get("analysis_metadata", {})).get("task_id", "")).strip()
            or BRIGHTFIELD_HE_PROFILE.task_id
        )
        analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        guided_mode = _normalize_guided_mode(
            measurement.get(
                "guided_mode",
                analysis_metadata.get("guided_mode", "expert" if source == "manual" else "foolproof"),
            )
        )
        threshold_method = str(
            analysis_metadata.get("threshold_method")
            or dict(measurement.get("auto_parameter_result", {})).get("metadata", {}).get("applied_threshold_method", "")
            or dict(measurement.get("auto_parameter_result", {})).get("threshold_method", "")
        ).strip()
        if guided_mode == "expert" or source == "manual":
            analysis_mode = "expert"
        elif guided_mode == "assisted":
            analysis_mode = "guided_assisted"
        elif guided_mode == "foolproof":
            analysis_mode = "guided_foolproof"
        else:
            analysis_mode = guided_mode or str(analysis_metadata.get("analysis_mode", "")).strip() or "guided"
        analysis_metadata.update(
            {
                "task_id": task_id,
                "profile_id": str(analysis_metadata.get("profile_id") or task_id),
                "analysis_kind": "brightfield_measurement",
                "analysis_mode": analysis_mode,
                "guided_mode": guided_mode,
                "stain_type": normalized_stain,
                "threshold_source": source,
                "threshold_method": threshold_method or None,
                "roi_mode": str(dict(measurement.get("roi", {})).get("mode", "whole_image")),
                "segmentation_mode": str(dict(measurement.get("params", {})).get("segmentation_mode", "hsv")),
                "measurement_uuid": str(measurement.get("measurement_uuid", "")).strip(),
                "image_uid": str(measurement.get("image_uid", "")).strip(),
            }
        )
        if metadata:
            analysis_metadata.update(dict(metadata))
        analysis_mode = self._resolve_analysis_mode_for_measurement(
            analysis_metadata,
            threshold_source=source,
            guided_mode=guided_mode,
        )
        analysis_metadata.update(
            {
                "analysis_mode": analysis_mode,
                "guided_mode": guided_mode,
                "threshold_source": source,
                "segmentation_mode": str(dict(measurement.get("params", {})).get("segmentation_mode", "hsv")),
                "tissue_mask_enabled": bool(dict(measurement.get("params", {})).get("tissue_mask_enabled", False)),
                "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
                "object_count_semantics": BRIGHTFIELD_OBJECT_COUNT_SEMANTICS,
            }
        )

        params = dict(measurement.get("params", {}))
        model_name = self._resolve_analysis_model_name(source, params, measurement.get("auto_parameter_result"))
        measurement["backend_name"] = ANALYSIS_BACKEND_NAME
        measurement["backend_version"] = SOFTWARE_VERSION
        measurement["model_name"] = model_name
        measurement["model_hash"] = self._build_analysis_model_hash(
            source,
            params,
            measurement.get("auto_parameter_result"),
            task_id=task_id,
        )
        measurement["include_in_statistics"] = bool(
            dict(measurement.get("reliability", {})).get("include_in_statistics", measurement.get("include_in_statistics", True))
        )
        measurement["warnings"] = self._dedupe_messages(
            list(warnings or [])
            + list(measurement.get("qc_messages", []))
            + list(dict(measurement.get("reliability", {})).get("messages", []))
        )
        measurement["user_overrides"] = dict(measurement.get("user_overrides", {}))
        measurement["guided_mode"] = guided_mode
        measurement["threshold_source"] = source
        measurement["analysis_metadata"] = analysis_metadata

    def _apply_guided_effective_settings_metadata(
        self,
        measurement: dict[str, Any],
        *,
        batch_state: dict[str, Any] | None = None,
        slider_values: dict[str, Any] | None = None,
        mapping_version: str | None = None,
    ) -> None:
        guided_mode = _normalize_guided_mode(measurement.get("guided_mode"))
        threshold_source = str(measurement.get("threshold_source", "")).strip().lower()
        if guided_mode == "expert" or threshold_source == "manual":
            return

        analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        auto_metadata = dict(dict(measurement.get("auto_parameter_result", {})).get("metadata", {}))
        threshold_method = (
            analysis_metadata.get("threshold_method")
            or auto_metadata.get("applied_threshold_method")
            or dict(measurement.get("auto_parameter_result", {})).get("threshold_method")
        )
        batch_state_for_hash = self._guided_batch_state_for_hash_context(
            stain_type=measurement.get("stain_type"),
            threshold_source=threshold_source,
            batch_state=batch_state,
        )
        image_context = {
            "image_uid": measurement.get("image_uid"),
            "source_name": measurement.get("image_name"),
        }
        measurement_image_uid = str(measurement.get("image_uid", "")).strip()
        if measurement_image_uid:
            try:
                image_record = self._resolve_image_record(measurement_image_uid)
            except ValueError:
                image_record = {}
            image_context.update(
                {
                    "source_name": image_record.get("source_name") or image_context["source_name"],
                    "source_digest": image_record.get("source_hash"),
                }
            )
        guided_metadata = package_brightfield_guided_measurement_metadata(
            guided_mode=guided_mode,
            task_profile=dict(measurement.get("task_profile", {})),
            stain_type=measurement.get("stain_type"),
            image_context=image_context,
            threshold_source=threshold_source,
            threshold_method=threshold_method,
            effective_settings=dict(measurement.get("params", {})),
            slider_values=slider_values,
            roi_snapshot=dict(measurement.get("roi", {})),
            analysis_metadata=analysis_metadata,
            batch_state=batch_state_for_hash,
            baseline_source=threshold_source,
            mapping_version=mapping_version or None,
        )
        measurement.update(guided_metadata)

    def _guided_batch_state_for_hash_context(
        self,
        *,
        stain_type: Any,
        threshold_source: Any,
        batch_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        normalized_stain_type = self._normalize_stain_type(str(stain_type or ""))
        normalized_threshold_source = str(threshold_source or "").strip().lower()
        if normalized_threshold_source == "profile_preset" and normalized_stain_type != "H&E":
            return None
        return dict(batch_state or {})

    def _finalize_guided_preview_status(self, measurement: dict[str, Any]) -> None:
        preview_state = dict(self._transient_preview_state or {})
        preview_hash = str(preview_state.get("guided_effective_settings_hash", "")).strip()
        measurement_hash = str(measurement.get("guided_effective_settings_hash", "")).strip()
        if not preview_hash or not measurement_hash:
            return
        if str(preview_state.get("image_uid", "")).strip() != str(measurement.get("image_uid", "")).strip():
            measurement["guided_preview_status"] = BRIGHTFIELD_GUIDED_PREVIEW_STALE_STATUS
            return
        if preview_hash == measurement_hash:
            measurement["guided_preview_status"] = BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS
            return
        measurement["guided_preview_status"] = BRIGHTFIELD_GUIDED_PREVIEW_STALE_STATUS

    def _apply_measurement_reliability(
        self,
        measurement: dict[str, Any],
        rgb: np.ndarray,
        analysis: dict[str, Any],
    ) -> None:
        overlay_generated = True
        overlay_error: Exception | None = None
        overlay = None
        try:
            overlay = render_mask_overlay(rgb, analysis["mask"], color=(255, 0, 180), alpha=0.35)
        except Exception as exc:
            overlay_generated = False
            overlay_error = exc

        overlay_validation = validate_overlay_result(
            rgb,
            analysis["mask"],
            overlay,
            error=overlay_error,
        )
        qc_messages = self._dedupe_messages(list(analysis.get("analysis_warnings", [])))
        reliability = evaluate_brightfield_reliability(
            dict(measurement.get("results", {})),
            qc_messages=qc_messages,
            overlay_generated=overlay_generated,
            threshold_source=str(measurement.get("threshold_source", "manual")),
            overlay_validation=overlay_validation,
            quality_summary=analysis.get("quality_summary"),
            artifact_summary=analysis.get("artifact_summary"),
        )
        measurement["overlay_generated"] = bool(overlay_generated)
        measurement["overlay_validation"] = dict(overlay_validation)
        measurement["qc_messages"] = qc_messages
        measurement["reliability"] = reliability.to_dict()
        measurement["reliability_status"] = str(reliability.status)
        measurement["include_in_statistics"] = bool(reliability.include_in_statistics)

    def _resolve_analysis_model_name(
        self,
        threshold_source: str,
        params: dict[str, Any],
        auto_parameter_result: Any,
    ) -> str:
        source = str(threshold_source).strip() or "manual"
        if source == "manual":
            segmentation_mode = str(params.get("segmentation_mode", "hsv")).strip().lower() or "hsv"
            return f"brightfield_{segmentation_mode}_manual"
        threshold_method = str(dict(auto_parameter_result or {}).get("threshold_method", "")).strip()
        if threshold_method:
            return f"brightfield_{threshold_method}"
        return "brightfield_auto"

    def _build_analysis_model_hash(
        self,
        threshold_source: str,
        params: dict[str, Any],
        auto_parameter_result: Any,
        *,
        task_id: str | None = None,
    ) -> str:
        source = str(threshold_source).strip() or "manual"
        auto_payload = dict(auto_parameter_result or {})
        return sha256_json(
            {
                "backend_name": ANALYSIS_BACKEND_NAME,
                "backend_version": SOFTWARE_VERSION,
                "task_id": str(task_id).strip() or BRIGHTFIELD_HE_PROFILE.task_id,
                "threshold_source": source,
                "model_name": self._resolve_analysis_model_name(source, params, auto_payload),
                "threshold_method": auto_payload.get("threshold_method"),
                "segmentation_mode": str(params.get("segmentation_mode", "hsv")),
                "auto_parameter_task_id": auto_payload.get("task_id"),
            }
        )

    def _dedupe_messages(self, messages: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for message in messages:
            text = str(message).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            result.append(text)
        return result

    def _resolve_measurement(
        self,
        image_uid: str | None = None,
        measurement_uuid: str | None = None,
    ) -> dict[str, Any]:
        if measurement_uuid is not None:
            target_uuid = str(measurement_uuid)
            for measurement in self.analysis.get("brightfield_measurements", []):
                if str(measurement.get("measurement_uuid")) == target_uuid:
                    return measurement
            for measurement in self.analysis.get("brightfield_ai_object_count_measurements", []):
                if str(measurement.get("measurement_uuid")) == target_uuid:
                    return measurement
            raise ValueError(f"Brightfield measurement not found: {target_uuid}")

        image_record = self._resolve_image_record(image_uid)
        measurements = list(image_record.get("measurements", []))
        if not measurements:
            raise ValueError(f"Brightfield image has no measurements: {image_record['uid']}")
        return measurements[-1]

    def _cache_he_fiber_area_data(self, measurement_uuid: str, mask: np.ndarray, labels: np.ndarray) -> None:
        key = str(measurement_uuid).strip()
        if not key:
            return
        self._transient_he_fiber_area_masks.pop(key, None)
        self._transient_he_fiber_area_labels.pop(key, None)
        self._transient_he_fiber_area_masks[key] = np.asarray(mask, dtype=bool).copy()
        self._transient_he_fiber_area_labels[key] = np.asarray(labels, dtype=np.int32).copy()
        self._trim_he_fiber_area_cache()

    def _touch_he_fiber_area_cache(self, measurement_uuid: str) -> None:
        key = str(measurement_uuid).strip()
        if key in self._transient_he_fiber_area_masks:
            self._transient_he_fiber_area_masks[key] = self._transient_he_fiber_area_masks.pop(key)
        if key in self._transient_he_fiber_area_labels:
            self._transient_he_fiber_area_labels[key] = self._transient_he_fiber_area_labels.pop(key)

    def _trim_he_fiber_area_cache(self) -> None:
        limit = max(0, int(self._HE_FIBER_AREA_CACHE_LIMIT))
        while len(self._transient_he_fiber_area_masks) > limit:
            self._transient_he_fiber_area_masks.popitem(last=False)
        while len(self._transient_he_fiber_area_labels) > limit:
            self._transient_he_fiber_area_labels.popitem(last=False)

    def _cached_he_fiber_area_mask(self, measurement_uuid: str) -> np.ndarray | None:
        key = str(measurement_uuid).strip()
        if not key or key not in self._transient_he_fiber_area_masks:
            return None
        self._touch_he_fiber_area_cache(key)
        return np.asarray(self._transient_he_fiber_area_masks[key], dtype=bool)

    def _cached_he_fiber_area_labels(self, measurement_uuid: str) -> np.ndarray | None:
        key = str(measurement_uuid).strip()
        if not key or key not in self._transient_he_fiber_area_labels:
            return None
        self._touch_he_fiber_area_cache(key)
        return np.asarray(self._transient_he_fiber_area_labels[key], dtype=np.int32)

    def _he_fiber_area_mask_for_measurement(self, measurement: dict[str, Any]) -> np.ndarray:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        cached_mask = self._cached_he_fiber_area_mask(measurement_uuid)
        if cached_mask is not None:
            return cached_mask
        labels = self._he_fiber_area_labels_for_measurement(measurement)
        mask = labels > 0
        if measurement_uuid:
            self._transient_he_fiber_area_masks.pop(measurement_uuid, None)
            self._transient_he_fiber_area_masks[measurement_uuid] = mask.copy()
            self._trim_he_fiber_area_cache()
        return mask

    def _he_fiber_area_labels_for_measurement(self, measurement: dict[str, Any]) -> np.ndarray:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        cached = self._cached_he_fiber_area_labels(measurement_uuid)
        if cached is not None:
            return cached

        image_record, rgb = self._load_image_rgb(str(measurement.get("image_uid", "")))
        if measurement.get('instance_labels') is not None:
            from core.image_analysis.cellpose_backend import unpack_instance_labels
            labels = unpack_instance_labels(measurement['instance_labels'], rgb.shape[:2])
            if measurement_uuid:
                self._cache_he_fiber_area_data(measurement_uuid, labels > 0, labels)
            return labels
        roi_snapshot = self._resolve_roi_snapshot(image_record, dict(measurement.get("roi", {})) or None)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        metadata = dict(measurement.get("analysis_metadata", {}))
        calibration = dict(metadata.get("calibration_snapshot", {}))
        if not calibration:
            calibration = self.he_fiber_area_calibration(str(image_record["uid"])) or {}
        if not calibration:
            raise ValueError("H&E Fiber Area overlay requires the saved calibration snapshot.")
        params = dict(measurement.get("params", {}))
        analysis = analyze_he_fiber_area(
            rgb,
            calibration,
            roi_mask=roi_mask,
            **self._he_fiber_analyze_kwargs(params),
        )
        labels = np.asarray(analysis.get("he_fiber_labels"), dtype=np.int32)
        if measurement_uuid:
            self._cache_he_fiber_area_data(measurement_uuid, labels > 0, labels)
        return labels

    def _resolve_roi_snapshot(
        self,
        image_record: dict[str, Any],
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if roi:
            if "items" in roi or "mode" in roi:
                snapshot = {
                    "mode": str(roi.get("mode", "selected_roi")),
                    "items": [dict(item) for item in roi.get("items", [])],
                    "active_item_uuid": roi.get("active_item_uuid"),
                }
                if snapshot["items"] or snapshot["mode"] == "whole_image":
                    return snapshot
            if "type" in roi and "coordinates_px" in roi:
                normalized = self._normalize_roi_geometry(str(roi["type"]), dict(roi["coordinates_px"]))
                roi_uuid = str(uuid4())
                return {
                    "mode": "selected_roi",
                    "items": [{"roi_uuid": roi_uuid, "type": str(roi["type"]), "coordinates_px": normalized}],
                    "active_item_uuid": roi_uuid,
                }
        image_roi = dict(image_record.get("roi", {}))
        return {
            "mode": str(image_roi.get("mode", "whole_image")),
            "items": [dict(item) for item in image_roi.get("items", [])],
            "active_item_uuid": image_roi.get("active_item_uuid"),
        }

    def _build_roi_mask(self, image_shape: tuple[int, int], roi_snapshot: dict[str, Any]) -> np.ndarray | None:
        if str(roi_snapshot.get("mode", "whole_image")) == "whole_image":
            return None
        active_uuid = str(roi_snapshot.get("active_item_uuid", "")).strip()
        items = list(roi_snapshot.get("items", []))
        active_item = None
        for item in items:
            if str(item.get("roi_uuid", "")) == active_uuid:
                active_item = dict(item)
                break
        if active_item is None and items:
            active_item = dict(items[-1])
        if not active_item:
            return None
        if str(active_item.get("type", "rectangle")) != "rectangle":
            return None
        coords = self._normalize_roi_geometry("rectangle", dict(active_item.get("coordinates_px", {})))
        height, width = int(image_shape[0]), int(image_shape[1])
        mask = np.zeros((height, width), dtype=bool)
        x0 = max(0, min(int(coords["x"]), width))
        y0 = max(0, min(int(coords["y"]), height))
        x1 = max(x0, min(int(coords["x"] + coords["w"]), width))
        y1 = max(y0, min(int(coords["y"] + coords["h"]), height))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
        return mask

    def _normalize_roi_geometry(self, roi_type: str, coordinates_px: dict[str, Any]) -> dict[str, int]:
        if str(roi_type) != "rectangle":
            raise ValueError(f"Unsupported Brightfield ROI type: {roi_type}")
        x = max(0, int(round(float(coordinates_px.get("x", 0)))))
        y = max(0, int(round(float(coordinates_px.get("y", 0)))))
        w = max(1, int(round(float(coordinates_px.get("w", 0)))))
        h = max(1, int(round(float(coordinates_px.get("h", 0)))))
        return {"x": x, "y": y, "w": w, "h": h}

    def _normalize_stain_type(self, stain_type: str) -> str:
        return normalize_brightfield_stain_type(stain_type)

    def _task_profile_for_stain_type(self, stain_type: str):
        normalized_stain = self._normalize_stain_type(stain_type)
        task_id = BRIGHTFIELD_STAIN_TASK_IDS.get(normalized_stain, BRIGHTFIELD_STAIN_TASK_IDS["Special stain"])
        return get_task_profile(task_id)

    def _profile_analysis_settings(self, task_profile) -> dict[str, Any]:
        settings = dict(task_profile.default_settings)
        settings.pop("profile_metadata", None)
        return settings

    def _profile_analysis_metadata(self, task_profile) -> dict[str, Any]:
        settings = dict(task_profile.default_settings)
        metadata = dict(settings.get("profile_metadata", {}))
        if not metadata:
            metadata["threshold_source"] = "profile_preset"
        metadata["analysis_mode"] = "profile_guided_foolproof"
        metadata["profile_id"] = str(task_profile.task_id)
        return metadata

    def _apply_profile_preset_reliability(
        self,
        measurement: dict[str, Any],
        task_profile,
        effective_stain_type: str | None = None,
    ) -> None:
        profile_name = str(task_profile.display_name).strip() or "Brightfield profile"
        normalized_stain = self._normalize_stain_type(effective_stain_type) if effective_stain_type is not None else ""
        is_other_fallback = (
            normalized_stain == BRIGHTFIELD_OTHER_STAIN_LABEL
            and task_profile.task_id == "brightfield_special_stain_area"
        )
        reliability = dict(measurement.get("reliability", {}))
        if str(reliability.get("status", "")).strip().upper() != "FAIL":
            reliability["status"] = "WARN"
            reliability["include_in_statistics"] = True
            messages = list(reliability.get("messages", []))
            messages.append(f"{profile_name} uses experimental preset thresholds. Review the overlay before using this result.")
            if is_other_fallback:
                messages.append(
                    "Stain type 'Other high-contrast stain' is using the Special stain "
                    "experimental fallback profile. Results may not match your actual stain."
                )
            reliability["messages"] = self._dedupe_messages(messages)
            measurement["reliability"] = reliability
            measurement["reliability_status"] = "WARN"
            measurement["include_in_statistics"] = True
        measurement["warnings"] = self._dedupe_messages(
            list(measurement.get("warnings", [])) + list(dict(measurement.get("reliability", {})).get("messages", []))
        )
