from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, ClassVar
from uuid import uuid4

import numpy as np
from PyQt6.QtGui import QColor, QImage, QImageReader

from core.image_analysis import (
    BatchThresholdState,
    DAB_IHC_PROFILE,
    assess_dab_stain_compatibility,
    assess_image_quality,
    assess_white_balance_color_cast,
    build_tissue_background_mask,
    classify_dab_gmm,
    clean_mask,
    detect_artifact_mask,
    deconvolve_h_dab,
    estimate_dab_auto_parameters,
    evaluate_ihc_reliability,
    measure_area_fraction,
    measure_channel_intensity,
    recalibrate_dab_threshold_from_examples,
    render_mask_overlay,
    rgb_to_dab_channel,
    threshold_channel,
    validate_overlay_result,
)
from core.image_analysis.io_engine import read_microscopy_image, read_rgb_image
from core.measurement_scale import merge_measurement_scale_updates, resolve_measurement_scale
from core.constants import SOFTWARE_VERSION
from core.models.panel import BasePanel, register_panel_class
from core.utils.hashing import sha256_json
from core.utils.image_formats import IHC_IMAGE_SUFFIXES, compound_suffix
from core.utils.rendering import fit_qimage_to_size, qimage_to_bytes, target_pixel_size
from core.utils.time import utc_now_iso

ANALYSIS_BACKEND_NAME = "sciefig.image_analysis"


def default_analysis_settings() -> dict[str, Any]:
    return {
        "positive_threshold": 0.20,
        "weak_threshold": 0.20,
        "moderate_threshold": 0.50,
        "strong_threshold": 0.80,
        "tissue_mask_enabled": False,
        "tissue_mask_value_threshold": 0.90,
        "tissue_mask_saturation_threshold": 0.08,
        "tissue_mask_min_area_px": 100,
        "min_tissue_area_px": 100,
    }


def default_guided_state() -> dict[str, Any]:
    return {
        "guided_mode": "guided",
        "task_profile": DAB_IHC_PROFILE.to_dict(),
        "sensitivity": str(DAB_IHC_PROFILE.default_settings.get("sensitivity", "medium")),
        "cleanup_level": str(DAB_IHC_PROFILE.default_settings.get("cleanup_level", "medium")),
        "overlay_opacity": float(DAB_IHC_PROFILE.default_settings.get("overlay_opacity", 0.35)),
        "tissue_background_correction": bool(DAB_IHC_PROFILE.default_settings.get("tissue_background_correction", True)),
        "last_auto_measurement_uuid": None,
        "last_active_measurement_uuid": None,
        "last_active_measurement_uuid_by_image": {},
        "auto_measurement_uuid_by_image": {},
        "batch_threshold_state": BatchThresholdState(task_id=DAB_IHC_PROFILE.task_id, locked=True).to_dict(),
    }


@register_panel_class
@dataclass
class IHCPanel(BasePanel):
    PANEL_TYPE: ClassVar[str] = "ihc"
    SOURCE_MODULE: ClassVar[str] = "ihc"
    panel_type: str = field(default=PANEL_TYPE)
    _dab_analysis_cache: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.style["preset"] = str(self.style.get("preset") or "Nature")
        self.data.setdefault("ihc_images", [])
        self.data.setdefault("active_image_uid", None)
        self.data.setdefault("active_step", 0)
        self.data.setdefault("analysis_settings", default_analysis_settings())
        self.data["guided_state"] = self._merge_guided_state(self.data.get("guided_state"))
        self.data.setdefault("statistics", {})
        self.data.setdefault("composer_outputs", [])
        self.analysis.setdefault("ihc_measurements", [])

    def import_image(self, path: str | Path, group: str = "Ungrouped") -> dict[str, Any]:
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = compound_suffix(source)
        if suffix not in IHC_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported IHC image format: {suffix}")

        imported = read_microscopy_image(source)
        return self.import_image_from_decoded(source, imported, group=group)

    def import_image_from_decoded(
        self,
        path: str | Path,
        imported: Any,
        group: str = "Ungrouped",
    ) -> dict[str, Any]:
        """Create the normal import record from one already-decoded image result."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(source)
        suffix = compound_suffix(source)
        if suffix not in IHC_IMAGE_SUFFIXES:
            raise ValueError(f"Unsupported IHC image format: {suffix}")

        metadata = self._build_import_metadata(imported.metadata)
        record = {
            "uid": str(uuid4()),
            "source_path": str(source),
            "source_name": source.name,
            "source_hash": imported.source_hash,
            "group": str(group),
            "metadata": metadata,
            "measurement_scale_bar": resolve_measurement_scale({"metadata": metadata}),
            "preprocessing": {
                "mode": "h_dab_deconvolution",
                "channel": "dab",
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
        self.data["ihc_images"] = images
        self.data["active_image_uid"] = record["uid"]
        self.data["raw_source"] = source.name
        self.data["raw_hash"] = record["source_hash"]
        self.data["raw_copy_path"] = f"data/ihc/{self.panel_uuid}/{source.name}"
        self.append_audit("import_ihc_image", f"{source.name}; group={group}")
        return record

    def images(self) -> list[dict[str, Any]]:
        return list(self.data.get("ihc_images", []))

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
        self.append_audit("set_ihc_roi", f"{image_uid}; {roi_type}")
        return roi_item

    def apply_roi_snapshot(self, image_uid: str, roi_snapshot: dict[str, Any] | None) -> None:
        image_record = self._resolve_image_record(image_uid)
        snapshot = self._resolve_roi_snapshot(image_record, roi_snapshot)
        image_record["roi"] = snapshot
        self.append_audit("select_ihc_measurement_roi", f"{image_uid}; mode={snapshot.get('mode', 'whole_image')}")

    def clear_image_roi(self, image_uid: str) -> None:
        image_record = self._resolve_image_record(image_uid)
        image_record["roi"] = {
            "mode": "whole_image",
            "items": [],
            "active_item_uuid": None,
        }
        self.append_audit("clear_ihc_roi", image_uid)

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
            raise ValueError("IHC panel has no active image to render.")
        source = Path(str(image_record.get("source_path", "")))
        image = self._read_raster(source)
        target_size = target_pixel_size(target_width_mm, target_height_mm, dpi)
        return fit_qimage_to_size(image, target_size, QColor("white"))

    def render_export(self, target_width_mm: float, target_height_mm: float, dpi: int = 300, format: str = "pdf") -> bytes:
        raster_format = format.lower().lstrip(".")
        if raster_format in {"pdf", "svg"}:
            raster_format = "png"
        image = self.render_preview(target_width_mm, target_height_mm, dpi=dpi)
        return qimage_to_bytes(image, raster_format)

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
            raise KeyError(f"IHC image not found: {image_uid}")
        layout = self.data.setdefault("ihc_layout", {})
        from core.layout_scale_bar import seed_layout_scale_presentation

        seed_layout_scale_presentation(
            layout,
            measurement_scale=resolved,
            target_ids=[str(image_record.get("uid", ""))],
            states_key="scale_bars",
            template_key="scale_bar_template",
        )
        self.data["ihc_images"] = images
        self.append_audit("update_ihc_measurement_scale_bar", f"{image_record.get('uid')}: {resolved.get('calibration_source')}")
        return dict(resolved)

    def measure_dab_positive_area(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        merged_settings = self._merge_analysis_settings(settings)
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        started_at = perf_counter()
        analysis = self._analyze_dab_threshold(rgb, merged_settings, roi_mask=roi_mask)
        processing_time_ms = max(0, int(round((perf_counter() - started_at) * 1000.0)))
        measurement = self._new_dab_measurement(
            image_record,
            roi_snapshot,
            merged_settings,
            analysis,
            processing_time_ms=processing_time_ms,
            threshold_source="manual",
        )
        self._apply_dab_measurement_metadata(
            measurement,
            rgb,
            roi_snapshot,
            threshold_source="manual",
            analysis=analysis,
        )
        self._apply_analysis_result_fields(
            measurement,
            threshold_source="manual",
        )
        self._cache_dab_analysis(measurement, analysis)
        self._append_dab_measurement(image_record, measurement)
        self.data["analysis_settings"] = dict(merged_settings)
        self.append_audit("measure_ihc_dab_positive_area", f"{image_record['uid']}; threshold={merged_settings['positive_threshold']}")
        return measurement

    def _new_dab_measurement(
        self,
        image_record: dict[str, Any],
        roi_snapshot: dict[str, Any],
        settings: dict[str, Any],
        analysis: dict[str, Any],
        *,
        processing_time_ms: int,
        threshold_source: str,
        auto_parameter_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        source = str(threshold_source).strip() or "manual"
        measurement = {
            "measurement_uuid": str(uuid4()),
            "image_uid": str(image_record["uid"]),
            "image_name": str(image_record["source_name"]),
            "group": str(image_record.get("group", "Ungrouped")),
            "type": "dab_positive_area",
            "roi": roi_snapshot,
            "params": dict(settings),
            "results": dict(analysis["results"]),
            "created_at": utc_now_iso(),
            "processing_time_ms": max(0, int(processing_time_ms)),
            "backend_name": ANALYSIS_BACKEND_NAME,
            "backend_version": SOFTWARE_VERSION,
            "model_name": self._resolve_analysis_model_name(source, auto_parameter_result),
            "model_hash": self._build_analysis_model_hash(source, auto_parameter_result),
            "include_in_statistics": True,
            "warnings": [],
            "user_overrides": {},
            "measurement_scale_bar": deepcopy(self.measurement_scale_bar(str(image_record["uid"]))),
            "analysis_metadata": {
                "task_id": DAB_IHC_PROFILE.task_id,
                "analysis_kind": "dab_positive_area",
                "threshold_source": source,
                "roi_mode": str(roi_snapshot.get("mode", "whole_image")),
            },
        }
        measurement["analysis_metadata"].update(dict(analysis.get("analysis_metadata", {})))
        if auto_parameter_result is not None:
            measurement["auto_parameter_result"] = dict(auto_parameter_result)
        return measurement

    def _append_dab_measurement(self, image_record: dict[str, Any], measurement: dict[str, Any]) -> None:
        images = self.images()
        for item in images:
            if item.get("uid") == image_record["uid"]:
                item.setdefault("measurements", []).append(measurement)
                break
        self.data["ihc_images"] = images
        measurements = list(self.analysis.get("ihc_measurements", []))
        measurements.append(measurement)
        self.analysis["ihc_measurements"] = measurements
        self.analysis.pop('ihc_statistics',None)

    def auto_analyze_dab(
        self,
        image_uid: str | None = None,
        settings: dict[str, Any] | None = None,
        roi: dict[str, Any] | None = None,
        use_batch_threshold: bool = True,
        override_batch_threshold: bool = False,
        lock_batch_threshold: bool = True,
        guided_mode: str = "guided",
    ) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        incoming_settings = dict(settings or {})
        sensitivity = str(incoming_settings.get("sensitivity", guided_state.get("sensitivity", "medium")))
        cleanup_level = str(incoming_settings.get("cleanup_level", guided_state.get("cleanup_level", "medium")))
        tissue_background_correction = bool(
            incoming_settings.get(
                "tissue_background_correction",
                guided_state.get("tissue_background_correction", True),
            )
        )

        auto_result = estimate_dab_auto_parameters(
            rgb,
            sensitivity=sensitivity,
            cleanup_level=cleanup_level,
        )
        batch_state = BatchThresholdState.from_dict(guided_state.get("batch_threshold_state"))
        if not batch_state.task_id:
            batch_state.task_id = DAB_IHC_PROFILE.task_id
        batch_state.example_guided_overrides.pop(str(image_record["uid"]), None)

        applied_threshold = float(auto_result.recommended_thresholds["effective_positive_threshold"])
        threshold_source = "image_auto"
        threshold_method = auto_result.threshold_method

        if use_batch_threshold and batch_state.locked and batch_state.batch_threshold is not None and not override_batch_threshold:
            applied_threshold = float(batch_state.batch_threshold)
            threshold_source = "batch_locked"
            threshold_method = str(batch_state.batch_threshold_method or threshold_method)
        elif use_batch_threshold and not override_batch_threshold:
            batch_state.batch_threshold = applied_threshold
            batch_state.batch_threshold_source_image = str(image_record["uid"])
            batch_state.batch_threshold_method = threshold_method
            batch_state.locked = bool(lock_batch_threshold)
            threshold_source = "batch_initialized"
        elif override_batch_threshold and batch_state.batch_threshold is not None:
            batch_state.per_image_overrides[str(image_record["uid"])] = applied_threshold
            override_warning = f"{image_record['uid']} used an image-specific auto threshold override."
            if override_warning not in batch_state.override_warnings:
                batch_state.override_warnings.append(override_warning)
            threshold_source = "image_override"

        effective_settings = self._merge_analysis_settings(
            {
                "positive_threshold": applied_threshold,
                "min_tissue_area_px": int(auto_result.recommended_min_area or 0),
                "tissue_mask_enabled": tissue_background_correction
                and bool(auto_result.recommended_tissue_mask_settings.get("enabled", False)),
                "tissue_mask_value_threshold": auto_result.recommended_tissue_mask_settings.get("value_threshold"),
                "tissue_mask_saturation_threshold": auto_result.recommended_tissue_mask_settings.get("saturation_threshold"),
                "tissue_mask_min_area_px": auto_result.recommended_tissue_mask_settings.get("min_area_px"),
            }
        )
        roi_snapshot = self._resolve_roi_snapshot(image_record, roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        started_at = perf_counter()
        analysis = self._analyze_dab_threshold(rgb, effective_settings, roi_mask=roi_mask)
        processing_time_ms = max(0, int(round((perf_counter() - started_at) * 1000.0)))

        auto_result_payload = auto_result.to_dict()
        auto_result_payload["recommended_thresholds"]["applied_positive_threshold"] = applied_threshold
        auto_result_payload["recommended_thresholds"]["effective_positive_threshold"] = applied_threshold
        auto_result_payload["metadata"]["threshold_source"] = threshold_source
        auto_result_payload["metadata"]["applied_threshold_method"] = threshold_method
        if batch_state.batch_threshold is not None:
            auto_result_payload["metadata"]["batch_threshold"] = float(batch_state.batch_threshold)

        measurement = self._new_dab_measurement(
            image_record,
            roi_snapshot,
            effective_settings,
            analysis,
            processing_time_ms=processing_time_ms,
            threshold_source=threshold_source,
            auto_parameter_result=auto_result_payload,
        )
        measurement["params"]["sensitivity"] = sensitivity
        measurement["params"]["cleanup_level"] = cleanup_level
        measurement["params"]["tissue_background_correction"] = tissue_background_correction
        measurement["guided_mode"] = str(guided_mode)
        measurement["task_profile"] = DAB_IHC_PROFILE.to_dict()
        measurement["threshold_source"] = threshold_source
        self._apply_dab_measurement_metadata(
            measurement,
            rgb,
            roi_snapshot,
            threshold_source=threshold_source,
            qc_messages=list(auto_result.qc_warnings),
            analysis=analysis,
        )
        self._apply_analysis_result_fields(
            measurement,
            threshold_source=threshold_source,
            warnings=list(auto_result.qc_warnings),
            user_overrides={"batch_threshold_override": True} if threshold_source == "image_override" else None,
            metadata={
                "threshold_method": threshold_method,
                "confidence_label": auto_result.confidence_label,
                "confidence_score": auto_result.confidence_score,
            },
        )
        self._cache_dab_analysis(measurement, analysis)
        self._append_dab_measurement(image_record, measurement)
        self.data["analysis_settings"] = dict(effective_settings)

        guided_state["guided_mode"] = str(guided_mode)
        guided_state["task_profile"] = DAB_IHC_PROFILE.to_dict()
        guided_state["sensitivity"] = sensitivity
        guided_state["cleanup_level"] = cleanup_level
        guided_state["tissue_background_correction"] = tissue_background_correction
        guided_state["last_auto_measurement_uuid"] = str(measurement["measurement_uuid"])
        guided_state["last_active_measurement_uuid"] = str(measurement["measurement_uuid"])
        last_active_by_image = dict(guided_state.get("last_active_measurement_uuid_by_image", {}))
        last_active_by_image[str(image_record["uid"])] = str(measurement["measurement_uuid"])
        guided_state["last_active_measurement_uuid_by_image"] = last_active_by_image
        auto_measurements_by_image = dict(guided_state.get("auto_measurement_uuid_by_image", {}))
        auto_measurements_by_image[str(image_record["uid"])] = str(measurement["measurement_uuid"])
        guided_state["auto_measurement_uuid_by_image"] = auto_measurements_by_image
        guided_state["batch_threshold_state"] = batch_state.to_dict()
        self.data["guided_state"] = guided_state
        self.append_audit("auto_analyze_ihc_dab", f"{image_record['uid']}; source={threshold_source}")
        return measurement

    def recalibrate_dab_from_examples(
        self,
        image_uid: str | None = None,
        examples: list[dict[str, Any]] | None = None,
        roi: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_record, rgb = self._load_image_rgb(image_uid)
        resolved_image_uid = str(image_record["uid"])
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        batch_state = BatchThresholdState.from_dict(guided_state.get("batch_threshold_state"))
        if not batch_state.task_id:
            batch_state.task_id = DAB_IHC_PROFILE.task_id

        active_measurement = self._resolve_last_active_measurement_for_image(resolved_image_uid)
        auto_measurement = self._resolve_saved_auto_measurement(resolved_image_uid)
        if active_measurement is None:
            active_measurement = auto_measurement
        base_settings = self._merge_analysis_settings(
            dict(active_measurement.get("params", {})) if active_measurement is not None else None
        )
        sensitivity = str(base_settings.get("sensitivity", guided_state.get("sensitivity", "medium")))
        cleanup_level = str(base_settings.get("cleanup_level", guided_state.get("cleanup_level", "medium")))
        tissue_background_correction = bool(
            base_settings.get(
                "tissue_background_correction",
                guided_state.get("tissue_background_correction", True),
            )
        )

        base_roi = roi
        if base_roi is None and active_measurement is not None:
            base_roi = dict(active_measurement.get("roi", {})) or None
        roi_snapshot = self._resolve_roi_snapshot(image_record, base_roi)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        analysis_context = self._resolve_dab_analysis_context(rgb, base_settings, roi_mask=roi_mask)
        example_sampling_mask = np.asarray(analysis_context["analysis_mask"], dtype=bool)
        dab = np.asarray(analysis_context["dab_channel"], dtype=np.float64)
        current_threshold = float(base_settings.get("positive_threshold", default_analysis_settings()["positive_threshold"]))
        example_result = recalibrate_dab_threshold_from_examples(
            dab,
            list(examples or []),
            current_threshold=current_threshold,
            tissue_mask=example_sampling_mask,
        )

        effective_settings = dict(base_settings)
        effective_settings["positive_threshold"] = float(example_result.threshold)

        measurement = self.measure_dab_positive_area(
            image_uid=resolved_image_uid,
            settings=effective_settings,
            roi=roi_snapshot,
        )

        prior_auto_measurement_uuid = ""
        if auto_measurement is not None:
            prior_auto_measurement_uuid = str(auto_measurement.get("measurement_uuid", "")).strip()
        elif active_measurement is not None:
            prior_auto_measurement_uuid = str(active_measurement.get("prior_auto_measurement_uuid", "")).strip()
            if not prior_auto_measurement_uuid and str(active_measurement.get("threshold_source", "")).strip() != "example_guided":
                prior_auto_measurement_uuid = str(active_measurement.get("measurement_uuid", "")).strip()

        auto_result_payload = {}
        if auto_measurement is not None:
            auto_result_payload = dict(auto_measurement.get("auto_parameter_result", {}))
        elif active_measurement is not None:
            auto_result_payload = dict(active_measurement.get("auto_parameter_result", {}))
        if auto_result_payload:
            thresholds = dict(auto_result_payload.get("recommended_thresholds", {}))
            thresholds["applied_positive_threshold"] = float(example_result.threshold)
            thresholds["effective_positive_threshold"] = float(example_result.threshold)
            auto_result_payload["recommended_thresholds"] = thresholds
            metadata = dict(auto_result_payload.get("metadata", {}))
            metadata["threshold_source"] = "example_guided"
            metadata["applied_threshold_method"] = "example_guided_local_patches"
            if prior_auto_measurement_uuid:
                metadata["prior_auto_measurement_uuid"] = prior_auto_measurement_uuid
            auto_result_payload["metadata"] = metadata

        measurement["params"]["sensitivity"] = sensitivity
        measurement["params"]["cleanup_level"] = cleanup_level
        measurement["params"]["tissue_background_correction"] = tissue_background_correction
        measurement["guided_mode"] = "assisted"
        measurement["task_profile"] = DAB_IHC_PROFILE.to_dict()
        measurement["threshold_source"] = "example_guided"
        if auto_result_payload:
            measurement["auto_parameter_result"] = auto_result_payload
        if prior_auto_measurement_uuid:
            measurement["prior_auto_measurement_uuid"] = prior_auto_measurement_uuid
        measurement["example_guided_result"] = example_result.to_dict()
        measurement["example_guided_result"]["channel_source"] = str(analysis_context["dab_channel_source"])
        base_qc_messages = []
        if auto_measurement is not None:
            base_qc_messages = list(auto_measurement.get("auto_parameter_result", {}).get("qc_warnings", []))
        elif active_measurement is not None:
            base_qc_messages = list(active_measurement.get("auto_parameter_result", {}).get("qc_warnings", []))
        self._apply_dab_measurement_metadata(
            measurement,
            rgb,
            roi_snapshot,
            threshold_source="example_guided",
            qc_messages=base_qc_messages + list(example_result.warnings),
            example_guided_result=measurement["example_guided_result"],
        )
        self._apply_analysis_result_fields(
            measurement,
            threshold_source="example_guided",
            warnings=base_qc_messages + list(example_result.warnings),
            user_overrides={
                "example_guided": True,
                "example_count": len(list(examples or [])),
            },
            metadata={
                "threshold_method": "example_guided_local_patches",
                "positive_example_count": int(example_result.positive_sample_count),
                "negative_example_count": int(example_result.negative_sample_count),
                "example_guided_channel_source": str(analysis_context["dab_channel_source"]),
            },
        )

        batch_state.example_guided_overrides[resolved_image_uid] = float(example_result.threshold)
        guided_state["guided_mode"] = "assisted"
        guided_state["task_profile"] = DAB_IHC_PROFILE.to_dict()
        guided_state["sensitivity"] = sensitivity
        guided_state["cleanup_level"] = cleanup_level
        guided_state["tissue_background_correction"] = tissue_background_correction
        guided_state["last_active_measurement_uuid"] = str(measurement["measurement_uuid"])
        last_active_by_image = dict(guided_state.get("last_active_measurement_uuid_by_image", {}))
        last_active_by_image[resolved_image_uid] = str(measurement["measurement_uuid"])
        guided_state["last_active_measurement_uuid_by_image"] = last_active_by_image
        auto_measurements_by_image = dict(guided_state.get("auto_measurement_uuid_by_image", {}))
        if prior_auto_measurement_uuid:
            auto_measurements_by_image[resolved_image_uid] = prior_auto_measurement_uuid
        guided_state["auto_measurement_uuid_by_image"] = auto_measurements_by_image
        guided_state["batch_threshold_state"] = batch_state.to_dict()
        self.data["guided_state"] = guided_state
        self.append_audit(
            "recalibrate_ihc_dab_from_examples",
            f"{resolved_image_uid}; threshold={measurement['params']['positive_threshold']}",
        )
        return measurement

    def revert_dab_to_auto(self, image_uid: str | None = None) -> dict[str, Any]:
        image_record = self._resolve_image_record(image_uid)
        resolved_image_uid = str(image_record["uid"])
        auto_measurement = self._resolve_saved_auto_measurement(resolved_image_uid)
        if auto_measurement is None:
            raise ValueError(f"No prior auto DAB measurement is available for image {resolved_image_uid}.")

        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        batch_state = BatchThresholdState.from_dict(guided_state.get("batch_threshold_state"))
        if not batch_state.task_id:
            batch_state.task_id = DAB_IHC_PROFILE.task_id
        batch_state.example_guided_overrides.pop(resolved_image_uid, None)

        params = dict(auto_measurement.get("params", {}))
        guided_state["guided_mode"] = str(auto_measurement.get("guided_mode", "guided")) or "guided"
        guided_state["task_profile"] = DAB_IHC_PROFILE.to_dict()
        guided_state["sensitivity"] = str(params.get("sensitivity", guided_state.get("sensitivity", "medium")))
        guided_state["cleanup_level"] = str(params.get("cleanup_level", guided_state.get("cleanup_level", "medium")))
        guided_state["tissue_background_correction"] = bool(
            params.get(
                "tissue_background_correction",
                guided_state.get("tissue_background_correction", True),
            )
        )
        guided_state["last_auto_measurement_uuid"] = str(auto_measurement["measurement_uuid"])
        guided_state["last_active_measurement_uuid"] = str(auto_measurement["measurement_uuid"])
        last_active_by_image = dict(guided_state.get("last_active_measurement_uuid_by_image", {}))
        last_active_by_image[resolved_image_uid] = str(auto_measurement["measurement_uuid"])
        guided_state["last_active_measurement_uuid_by_image"] = last_active_by_image
        auto_measurements_by_image = dict(guided_state.get("auto_measurement_uuid_by_image", {}))
        auto_measurements_by_image[resolved_image_uid] = str(auto_measurement["measurement_uuid"])
        guided_state["auto_measurement_uuid_by_image"] = auto_measurements_by_image
        guided_state["batch_threshold_state"] = batch_state.to_dict()
        self.data["guided_state"] = guided_state
        self.data["active_image_uid"] = resolved_image_uid
        self.append_audit("revert_ihc_dab_to_auto", resolved_image_uid)
        return auto_measurement

    def render_measurement_overlay(
        self,
        image_uid: str | None = None,
        measurement_uuid: str | None = None,
    ) -> np.ndarray:
        measurement = self._resolve_measurement(image_uid=image_uid, measurement_uuid=measurement_uuid)
        target_image_uid = str(measurement["image_uid"])
        _, rgb = self._load_image_rgb(target_image_uid)
        roi_snapshot = self._resolve_roi_snapshot(self._resolve_image_record(target_image_uid), dict(measurement.get("roi", {})) or None)
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        analysis = self._cached_dab_analysis(measurement)
        if analysis is None:
            settings = dict(measurement.get('params', {}))
            context = self._resolve_dab_analysis_context(rgb, settings, roi_mask=roi_mask)
            analysis = {'mask': self._dab_threshold_mask(context['dab_channel'], settings) & context['analysis_mask']}
            self._cache_dab_analysis(measurement, analysis)
        overlay_opacity = float(
            measurement.get("params", {}).get(
                "overlay_opacity",
                self._merge_guided_state(self.data.get("guided_state")).get("overlay_opacity", 0.35),
            )
        )
        return render_mask_overlay(rgb, analysis["mask"], alpha=overlay_opacity)

    def _dab_analysis_cache_key(self, measurement: dict[str, Any]) -> str:
        return sha256_json(
            {
                "image_uid": str(measurement.get("image_uid", "")),
                "params": dict(measurement.get("params", {})),
                "roi": dict(measurement.get("roi", {})),
            }
        )

    def _cache_dab_analysis(self, measurement: dict[str, Any], analysis: dict[str, Any]) -> None:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        if not measurement_uuid:
            return
        self._dab_analysis_cache[measurement_uuid] = {
            "cache_key": self._dab_analysis_cache_key(measurement),
            "analysis": analysis,
        }
        if len(self._dab_analysis_cache) > 16:
            self._dab_analysis_cache.pop(next(iter(self._dab_analysis_cache)))

    def _cached_dab_analysis(self, measurement: dict[str, Any]) -> dict[str, Any] | None:
        measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
        if not measurement_uuid:
            return None
        cached = self._dab_analysis_cache.get(measurement_uuid)
        if not cached or str(cached.get("cache_key", "")) != self._dab_analysis_cache_key(measurement):
            return None
        analysis = cached.get("analysis")
        return analysis if isinstance(analysis, dict) else None

    def measurement_by_uuid(self, measurement_uuid: str) -> dict[str, Any] | None:
        target_uuid = str(measurement_uuid).strip()
        if not target_uuid:
            return None
        for measurement in self.analysis.get("ihc_measurements", []):
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
            raise ValueError(f"Could not load IHC raster image: {source}")
        return image

    def _resolve_image_record(self, image_uid: str | None = None) -> dict[str, Any]:
        if image_uid is None:
            image_record = self.active_image()
            if image_record is None:
                raise ValueError("IHC panel has no active image.")
            return image_record

        target_uid = str(image_uid)
        for image in self.images():
            if image.get("uid") == target_uid:
                return image
        raise ValueError(f"IHC image not found: {target_uid}")

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
        profile = dict(existing.get("task_profile", {}))
        if str(profile.get("task_id", "")).strip():
            merged["task_profile"] = dict(profile)
        batch_state = BatchThresholdState.from_dict(existing.get("batch_threshold_state"))
        if not batch_state.task_id:
            batch_state.task_id = DAB_IHC_PROFILE.task_id
        merged["batch_threshold_state"] = batch_state.to_dict()
        return merged

    def _apply_dab_measurement_metadata(
        self,
        measurement: dict[str, Any],
        rgb: np.ndarray,
        roi_snapshot: dict[str, Any],
        *,
        threshold_source: str,
        qc_messages: list[str] | None = None,
        example_guided_result: dict[str, Any] | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> None:
        roi_mask = self._build_roi_mask(rgb.shape[:2], roi_snapshot)
        if analysis is None:
            analysis = self._analyze_dab_threshold(rgb, dict(measurement.get("params", {})), roi_mask=roi_mask)
        existing_analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        existing_analysis_metadata.update(dict(analysis.get("analysis_metadata", {})))
        measurement["analysis_metadata"] = existing_analysis_metadata
        dab = rgb_to_dab_channel(rgb)
        selected_tissue_mask = self._build_selected_tissue_mask(
            rgb,
            dict(measurement.get("params", {})),
            roi_mask=roi_mask,
        )

        combined_qc_messages = list(qc_messages or []) + list(analysis.get("analysis_warnings", []))
        white_balance = assess_white_balance_color_cast(rgb)
        dab_compatibility = assess_dab_stain_compatibility(dab, tissue_mask=selected_tissue_mask)
        for warning in (white_balance.get("warning"), dab_compatibility.get("warning")):
            warning_text = str(warning or "").strip()
            if warning_text:
                combined_qc_messages.append(warning_text)

        overlay_generated = True
        overlay_opacity = float(
            measurement.get("params", {}).get(
                "overlay_opacity",
                self._merge_guided_state(self.data.get("guided_state")).get("overlay_opacity", 0.35),
            )
        )
        overlay_error: Exception | None = None
        overlay = None
        try:
            overlay = render_mask_overlay(rgb, analysis["mask"], alpha=overlay_opacity)
        except Exception as exc:
            overlay_generated = False
            overlay_error = exc

        overlay_validation = validate_overlay_result(
            rgb,
            analysis["mask"],
            overlay,
            error=overlay_error,
        )

        normalized_qc_messages = self._dedupe_messages(combined_qc_messages)
        reliability = evaluate_ihc_reliability(
            dict(measurement.get("results", {})),
            qc_messages=normalized_qc_messages,
            overlay_generated=overlay_generated,
            threshold_source=threshold_source,
            example_guided_result=example_guided_result,
            overlay_validation=overlay_validation,
            quality_summary=analysis.get("quality_summary"),
            artifact_summary=analysis.get("artifact_summary"),
        )
        measurement["color_qc"] = dict(white_balance)
        measurement["stain_qc"] = dict(dab_compatibility)
        measurement["overlay_generated"] = bool(overlay_generated)
        measurement["overlay_validation"] = dict(overlay_validation)
        measurement["qc_messages"] = normalized_qc_messages
        measurement["reliability"] = reliability.to_dict()
        measurement["reliability_status"] = str(measurement["reliability"]["status"])
        measurement["include_in_statistics"] = bool(measurement["reliability"].get("include_in_statistics", True))
        measurement["warnings"] = self._dedupe_messages(
            normalized_qc_messages + list(measurement["reliability"].get("messages", []))
        )

    def _apply_analysis_result_fields(
        self,
        measurement: dict[str, Any],
        *,
        threshold_source: str,
        warnings: list[str] | None = None,
        user_overrides: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        source = str(threshold_source).strip() or "manual"
        analysis_metadata = dict(measurement.get("analysis_metadata", {}))
        analysis_metadata.update(
            {
                "task_id": DAB_IHC_PROFILE.task_id,
                "analysis_kind": "dab_positive_area",
                "threshold_source": source,
                "roi_mode": str(dict(measurement.get("roi", {})).get("mode", "whole_image")),
            }
        )
        if metadata:
            analysis_metadata.update(dict(metadata))

        combined_warnings = self._dedupe_messages(
            list(warnings or [])
            + list(measurement.get("qc_messages", []))
            + list(dict(measurement.get("reliability", {})).get("messages", []))
        )
        existing_overrides = dict(measurement.get("user_overrides", {}))
        if user_overrides:
            existing_overrides.update(dict(user_overrides))
        model_name = self._resolve_analysis_model_name(source, measurement.get("auto_parameter_result"))
        measurement["backend_name"] = ANALYSIS_BACKEND_NAME
        measurement["backend_version"] = SOFTWARE_VERSION
        measurement["model_name"] = model_name
        measurement["model_hash"] = self._build_analysis_model_hash(source, measurement.get("auto_parameter_result"))
        measurement["include_in_statistics"] = bool(
            dict(measurement.get("reliability", {})).get("include_in_statistics", measurement.get("include_in_statistics", True))
        )
        measurement["warnings"] = combined_warnings
        measurement["user_overrides"] = existing_overrides
        measurement["analysis_metadata"] = analysis_metadata

    def _resolve_analysis_model_name(
        self,
        threshold_source: str,
        auto_parameter_result: Any,
    ) -> str:
        source = str(threshold_source).strip() or "manual"
        if source == "manual":
            return "ihc_dab_threshold_manual"
        metadata = dict(dict(auto_parameter_result or {}).get("metadata", {}))
        threshold_method = str(
            metadata.get("applied_threshold_method")
            or dict(auto_parameter_result or {}).get("threshold_method", "")
        ).strip()
        if source == "example_guided":
            return "ihc_dab_example_guided_local_patches"
        if threshold_method:
            return f"ihc_dab_{threshold_method}"
        return "ihc_dab_auto"

    def _build_analysis_model_hash(
        self,
        threshold_source: str,
        auto_parameter_result: Any,
    ) -> str:
        source = str(threshold_source).strip() or "manual"
        auto_payload = dict(auto_parameter_result or {})
        metadata = dict(auto_payload.get("metadata", {}))
        return sha256_json(
            {
                "backend_name": ANALYSIS_BACKEND_NAME,
                "backend_version": SOFTWARE_VERSION,
                "task_id": DAB_IHC_PROFILE.task_id,
                "threshold_source": source,
                "model_name": self._resolve_analysis_model_name(source, auto_payload),
                "threshold_method": metadata.get("applied_threshold_method") or auto_payload.get("threshold_method"),
                "auto_parameter_task_id": auto_payload.get("task_id"),
            }
        )

    def _build_selected_tissue_mask(
        self,
        rgb: np.ndarray,
        settings: dict[str, Any],
        *,
        roi_mask: np.ndarray | None = None,
    ) -> np.ndarray:
        selected_tissue = np.ones(rgb.shape[:2], dtype=bool)
        if bool(settings.get("tissue_mask_enabled", False)):
            selected_tissue = build_tissue_background_mask(
                rgb,
                {
                    "enabled": True,
                    "tissue_mask_value_threshold": settings.get("tissue_mask_value_threshold"),
                    "tissue_mask_saturation_threshold": settings.get("tissue_mask_saturation_threshold"),
                    "tissue_mask_min_area_px": settings.get("tissue_mask_min_area_px"),
                },
            )
        if roi_mask is not None:
            selected_tissue = np.asarray(selected_tissue, dtype=bool) & np.asarray(roi_mask, dtype=bool)
        return np.asarray(selected_tissue, dtype=bool)

    def _resolve_last_active_measurement_for_image(self, image_uid: str) -> dict[str, Any] | None:
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        last_active_by_image = dict(guided_state.get("last_active_measurement_uuid_by_image", {}))
        candidate_uuids = [
            last_active_by_image.get(image_uid),
            guided_state.get("last_active_measurement_uuid"),
        ]
        for candidate_uuid in candidate_uuids:
            measurement = self.measurement_by_uuid(str(candidate_uuid).strip()) if candidate_uuid else None
            if measurement is not None and str(measurement.get("image_uid", "")).strip() == image_uid:
                return measurement
        image_record = self._resolve_image_record(image_uid)
        measurements = list(image_record.get("measurements", []))
        return dict(measurements[-1]) if measurements else None

    def _resolve_saved_auto_measurement(self, image_uid: str) -> dict[str, Any] | None:
        guided_state = self._merge_guided_state(self.data.get("guided_state"))
        auto_measurements_by_image = dict(guided_state.get("auto_measurement_uuid_by_image", {}))
        auto_uuid = str(auto_measurements_by_image.get(image_uid, "")).strip()
        if auto_uuid:
            measurement = self.measurement_by_uuid(auto_uuid)
            if measurement is not None and str(measurement.get("image_uid", "")).strip() == image_uid:
                return measurement

        image_record = self._resolve_image_record(image_uid)
        for measurement in reversed(list(image_record.get("measurements", []))):
            if str(measurement.get("image_uid", "")).strip() != image_uid:
                continue
            if str(measurement.get("threshold_source", "")).strip() == "example_guided":
                continue
            return measurement
        return None

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

    def _analyze_dab_threshold(
        self,
        rgb: np.ndarray,
        settings: dict[str, Any],
        roi_mask: np.ndarray | None = None,
    ) -> dict[str, Any]:
        analysis_context = self._resolve_dab_analysis_context(rgb, settings, roi_mask=roi_mask)
        dab = np.asarray(analysis_context["dab_channel"], dtype=np.float64)
        quality = dict(analysis_context["quality_summary"])
        artifact = dict(analysis_context["artifact_summary"])
        analysis_mask = np.asarray(analysis_context["analysis_mask"], dtype=bool)
        stain_deconvolution = dict(analysis_context["stain_deconvolution"])

        cleaned_mask = self._dab_threshold_mask(dab, settings)
        selected_mask = cleaned_mask & analysis_mask
        area = measure_area_fraction(cleaned_mask, roi_mask=analysis_mask)
        intensity = measure_channel_intensity(dab, mask=cleaned_mask, roi_mask=analysis_mask)
        gmm_classification = classify_dab_gmm(
            dab,
            tissue_mask=analysis_mask,
        )
        analysis_warnings = self._dedupe_messages(
            list(analysis_context["dab_channel_warnings"])
            + list(gmm_classification.get("warnings", []))
            + list(quality.get("warnings", []))
            + list(artifact.get("warnings", []))
        )
        return {
            "mask": selected_mask,
            "analysis_mask": analysis_mask,
            "artifact_mask": np.asarray(artifact["artifact_mask"], dtype=bool),
            "quality_summary": quality,
            "artifact_summary": artifact,
            "results": {
                **area,
                "tissue_area_px": int(area["total_area_px"]),
                "mean_dab_intensity": float(intensity["mean_intensity"]),
                "integrated_dab_density": float(intensity["integrated_density"]),
                "negative_percent": float(gmm_classification["negative_percent"]),
                "weak_percent": float(gmm_classification["weak_percent"]),
                "moderate_percent": float(gmm_classification["moderate_percent"]),
                "strong_percent": float(gmm_classification["strong_percent"]),
                "positive_percent": float(gmm_classification["positive_percent"]),
                "intensity_positive_percent": float(
                    gmm_classification["weak_percent"]
                    + gmm_classification["moderate_percent"]
                    + gmm_classification["strong_percent"]
                ),
                "h_score": float(gmm_classification["h_score"]),
            },
            "analysis_metadata": {
                "dab_channel_source": str(analysis_context["dab_channel_source"]),
                "stain_separation": dict(stain_deconvolution.get("stain_separation", {})),
                "gmm_classification": {
                    key: value
                    for key, value in gmm_classification.items()
                    if key != "warnings"
                },
                "positive_percent_semantics": "Legacy positive_percent follows the classifier payload; intensity_positive_percent is weak+moderate+strong.",
                "stain_engine_warnings": list(analysis_warnings),
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
            "analysis_warnings": analysis_warnings,
        }

    @staticmethod
    def _dab_threshold_mask(dab, settings):
        return clean_mask(threshold_channel(dab, settings['positive_threshold']),
                          min_area_px=int(settings.get('min_tissue_area_px', 0)), fill_holes=True)

    def _resolve_dab_analysis_context(
        self,
        rgb: np.ndarray,
        settings: dict[str, Any],
        *,
        roi_mask: np.ndarray | None = None,
    ) -> dict[str, Any]:
        quality = assess_image_quality(rgb)
        selection_mask = None if roi_mask is None else np.asarray(roi_mask, dtype=bool)
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
            selection_mask = tissue_mask if selection_mask is None else (selection_mask & tissue_mask)
        base_tissue_mask = selection_mask if selection_mask is not None else np.ones(rgb.shape[:2], dtype=bool)
        artifact = detect_artifact_mask(rgb, tissue_mask=base_tissue_mask)
        analysis_mask = base_tissue_mask & ~np.asarray(artifact["artifact_mask"], dtype=bool)

        dab_channel_source = "dab_od"
        dab_channel_warnings: list[str] = []
        try:
            stain_deconvolution = deconvolve_h_dab(rgb, tissue_mask=analysis_mask)
            dab_channel = np.asarray(stain_deconvolution["dab_od"], dtype=np.float64)
            dab_channel_warnings.extend(str(item) for item in stain_deconvolution.get("warnings", []))
        except Exception as exc:
            dab_channel_source = "legacy_rgb_to_dab_fallback"
            stain_deconvolution = {
                "stain_separation": {
                    "method": "legacy_rgb_to_dab_fallback",
                    "warnings": [f"DAB deconvolution fallback: {exc}"],
                    "tissue_pixel_count": int(np.count_nonzero(analysis_mask)),
                    "signal_p95": None,
                    "dynamic_range": None,
                    "singular_values": [],
                    "vector_angle_deg": None,
                }
            }
            dab_channel = rgb_to_dab_channel(rgb)
            dab_channel_warnings.append(f"DAB deconvolution fallback: {exc}")

        return {
            "analysis_mask": np.asarray(analysis_mask, dtype=bool),
            "artifact_summary": artifact,
            "quality_summary": quality,
            "stain_deconvolution": stain_deconvolution,
            "dab_channel": dab_channel,
            "dab_channel_source": dab_channel_source,
            "dab_channel_warnings": dab_channel_warnings,
        }

    def _resolve_measurement(
        self,
        image_uid: str | None = None,
        measurement_uuid: str | None = None,
    ) -> dict[str, Any]:
        if measurement_uuid is not None:
            target_uuid = str(measurement_uuid)
            for measurement in self.analysis.get("ihc_measurements", []):
                if str(measurement.get("measurement_uuid")) == target_uuid:
                    return measurement
            raise ValueError(f"IHC measurement not found: {target_uuid}")

        image_record = self._resolve_image_record(image_uid)
        measurements = list(image_record.get("measurements", []))
        if not measurements:
            raise ValueError(f"IHC image has no measurements: {image_record['uid']}")
        return measurements[-1]

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
        roi_type = str(active_item.get("type", "rectangle"))
        if roi_type != "rectangle":
            return None
        coords = self._normalize_roi_geometry(roi_type, dict(active_item.get("coordinates_px", {})))
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
            raise ValueError(f"Unsupported IHC ROI type: {roi_type}")
        x = max(0, int(round(float(coordinates_px.get("x", 0)))))
        y = max(0, int(round(float(coordinates_px.get("y", 0)))))
        w = max(1, int(round(float(coordinates_px.get("w", 0)))))
        h = max(1, int(round(float(coordinates_px.get("h", 0)))))
        return {"x": x, "y": y, "w": w, "h": h}
