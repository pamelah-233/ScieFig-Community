from __future__ import annotations

import csv
import json
from pathlib import Path
from shutil import copy2

from PyQt6.QtWidgets import QGraphicsScene

from core.export.wb_exporter import export_wb_figure_scene
from core.models import FluorescencePanel


def export_fluoro_figure_scene(scene: QGraphicsScene, path: str | Path, dpi: int = 1200) -> Path:
    selected_items = list(scene.selectedItems())
    hidden_handles = []
    export_items = [item for item in scene.items() if hasattr(item, "prepare_scene_export") and hasattr(item, "finish_scene_export")]
    try:
        scene.clearSelection()
        for item in scene.items():
            if str(item.data(0) or "") == "fluoro_resize_handle":
                hidden_handles.append((item, item.isVisible()))
                item.setVisible(False)
        for item in export_items:
            item.prepare_scene_export()
        return export_wb_figure_scene(scene, path, dpi=dpi)
    finally:
        for item in export_items:
            item.finish_scene_export()
        for item, visible in hidden_handles:
            if item.scene() is scene:
                item.setVisible(visible)
        for item in selected_items:
            if item.scene() is scene:
                item.setSelected(True)


def export_fluoro_metadata(panel: FluorescencePanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    metadata_path = target.with_name(f"{target.stem}_fluorescence_metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "panel_id": panel.panel_id,
                "panel_uuid": panel.panel_uuid,
                "images": panel.data.get("fluorescence_images", []),
                "analysis": panel.analysis,
                "audit_log": [entry.to_dict() for entry in panel.audit_log],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    panel.append_audit("export_fluorescence_metadata", metadata_path.name)
    return metadata_path


def export_fluoro_measurements(panel: FluorescencePanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    csv_path = target.with_name(f"{target.stem}_measurements.csv")
    rows = []
    for image in panel.data.get("fluorescence_images", []):
        for roi in image.get("rois", []):
            measurement = dict(roi.get("measurement", {}))
            measurement.update(
                {
                    "image_uid": image.get("uid", ""),
                    "image_name": image.get("source_name", ""),
                    "roi_id": roi.get("roi_id", ""),
                    "roi_type": roi.get("type", ""),
                }
            )
            rows.append(measurement)
    fieldnames = ["image_uid", "image_name", "roi_id", "roi_type", "channel_index", "area_px", "area_um2", "mean", "integrated_density"]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    panel.append_audit("export_fluorescence_measurements", csv_path.name)
    return csv_path


def export_fluoro_smart_assisted_measurements(panel: FluorescencePanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    csv_path = target.with_name(f"{target.stem}_smart_assisted_measurements.csv")
    images = {
        str(image.get("uid", "")): image
        for image in panel.data.get("fluorescence_images", [])
    }
    fieldnames = [
        "image_uid",
        "image_name",
        "channel_index",
        "channel_name",
        "analysis_mode",
        "analysis_version",
        "smart_measurement_uuid",
        "smart_object_count",
        "total_object_area_px",
        "mean_object_area_px",
        "mean_intensity",
        "integrated_intensity",
        "background_estimate",
        "effective_settings_hash",
        "smart_panel_context_hash",
        "core_effective_settings_hash",
        "input_preprocessing_context",
        "roi_or_geometry",
        "roi_reconstructable",
        "warnings",
    ]
    rows = []
    for record in panel.analysis.get("fluorescence_smart_assisted_measurements", []):
        image_uid = str(record.get("image_uid", record.get("image_uuid", "")))
        image = images.get(image_uid, {})
        metadata = dict(record.get("metadata") or {})
        effective_settings_hash = str(metadata.get("effective_settings_hash", ""))
        smart_panel_context_hash = str(record.get("smart_panel_context_hash") or metadata.get("smart_panel_context_hash") or "")
        input_preprocessing_context = record.get("input_preprocessing_context") or metadata.get("input_preprocessing_context") or {}
        roi_or_geometry = record.get("roi_or_geometry") or metadata.get("roi_context") or {}
        rows.append(
            {
                "image_uid": image_uid,
                "image_name": image.get("source_name", ""),
                "channel_index": _safe_int(record.get("channel_index")),
                "channel_name": record.get("channel_name", ""),
                "analysis_mode": record.get("analysis_mode", ""),
                "analysis_version": record.get("analysis_version", metadata.get("analysis_version", "")),
                "smart_measurement_uuid": record.get("smart_measurement_uuid", ""),
                "smart_object_count": _safe_int(record.get("smart_object_count")),
                "total_object_area_px": _safe_int(record.get("total_object_area_px")),
                "mean_object_area_px": _safe_float(record.get("mean_object_area_px")),
                "mean_intensity": _safe_float(record.get("mean_intensity")),
                "integrated_intensity": _safe_float(record.get("integrated_intensity")),
                "background_estimate": _json_cell(record.get("background_estimate"), {}),
                "effective_settings_hash": effective_settings_hash,
                "smart_panel_context_hash": smart_panel_context_hash,
                "core_effective_settings_hash": effective_settings_hash,
                "input_preprocessing_context": _json_cell(input_preprocessing_context, {}),
                "roi_or_geometry": _json_cell(roi_or_geometry, {}),
                "roi_reconstructable": _roi_reconstructable(roi_or_geometry),
                "warnings": _json_cell(record.get("warnings"), []),
            }
        )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    panel.append_audit("export_fluorescence_smart_assisted_measurements", csv_path.name)
    return csv_path


def export_fluoro_ai_assisted_measurements(panel: FluorescencePanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    csv_path = target.with_name(f"{target.stem}_ai_assisted_measurements.csv")
    images = {
        str(image.get("uid", "")): image
        for image in panel.data.get("fluorescence_images", [])
    }
    fieldnames = [
        "image_uid",
        "image_name",
        "channel_index",
        "channel_name",
        "analysis_mode",
        "analysis_version",
        "ai_measurement_uuid",
        "ai_object_count",
        "total_object_area_px",
        "mean_object_area_px",
        "mean_intensity",
        "integrated_intensity",
        "effective_settings_hash",
        "ai_panel_context_hash",
        "model_id",
        "model_version",
        "ai_pack_postprocess",
        "model_commercial_use_status",
        "model_license_summary",
        "model_execution_mode",
        "roi_or_geometry",
        "roi_reconstructable",
        "warnings",
    ]
    rows = []
    for record in panel.analysis.get("fluorescence_ai_assisted_measurements", []):
        image_uid = str(record.get("image_uid", record.get("image_uuid", "")))
        image = images.get(image_uid, {})
        metadata = dict(record.get("metadata") or {})
        effective_settings_hash = str(metadata.get("effective_settings_hash", ""))
        ai_panel_context_hash = str(record.get("ai_panel_context_hash") or metadata.get("ai_panel_context_hash") or "")
        roi_or_geometry = record.get("roi_or_geometry") or metadata.get("roi_context") or {}
        rows.append(
            {
                "image_uid": image_uid,
                "image_name": image.get("source_name", ""),
                "channel_index": _safe_int(record.get("channel_index")),
                "channel_name": record.get("channel_name", ""),
                "analysis_mode": record.get("analysis_mode", ""),
                "analysis_version": record.get("analysis_version", metadata.get("analysis_version", "")),
                "ai_measurement_uuid": record.get("ai_measurement_uuid", ""),
                "ai_object_count": _safe_int(record.get("ai_object_count")),
                "total_object_area_px": _safe_int(record.get("total_object_area_px")),
                "mean_object_area_px": _safe_float(record.get("mean_object_area_px")),
                "mean_intensity": _safe_float(record.get("mean_intensity")),
                "integrated_intensity": _safe_float(record.get("integrated_intensity")),
                "effective_settings_hash": effective_settings_hash,
                "ai_panel_context_hash": ai_panel_context_hash,
                "model_id": metadata.get("model_id", ""),
                "model_version": metadata.get("model_version", ""),
                "ai_pack_postprocess": metadata.get("ai_pack_postprocess", ""),
                "model_commercial_use_status": metadata.get("model_commercial_use_status", ""),
                "model_license_summary": metadata.get("model_license_summary", ""),
                "model_execution_mode": metadata.get("model_execution_mode", ""),
                "roi_or_geometry": _json_cell(roi_or_geometry, {}),
                "roi_reconstructable": _roi_reconstructable(roi_or_geometry),
                "warnings": _json_cell(record.get("warnings"), []),
            }
        )
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})
    panel.append_audit("export_fluorescence_ai_assisted_measurements", csv_path.name)
    return csv_path


def _safe_int(value: object) -> int | str:
    if value is None:
        return ""
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def _safe_float(value: object) -> float | str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return "" if number != number else number


def _json_cell(value: object, empty_value: object) -> str:
    payload = empty_value if value is None else value
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _roi_reconstructable(roi_or_geometry: object) -> bool | str:
    if not isinstance(roi_or_geometry, dict):
        return ""
    value = roi_or_geometry.get("reconstructable")
    return value if isinstance(value, bool) else ""


def export_fluoro_source_folder(panel: FluorescencePanel, figure_path: str | Path) -> Path:
    target = Path(figure_path)
    source_dir = target.with_name(f"{target.stem}_source")
    source_dir.mkdir(parents=True, exist_ok=True)
    for image in panel.data.get("fluorescence_images", []):
        source_path = Path(str(image.get("source_path", "")))
        if source_path.exists():
            copy2(source_path, source_dir / source_path.name)
    manifest_path = source_dir / f"{panel.panel_id}_fluorescence_source_manifest.json"
    manifest_path.write_text(json.dumps(panel.data.get("fluorescence_images", []), ensure_ascii=False, indent=2), encoding="utf-8")
    panel.append_audit("export_fluorescence_source_folder", source_dir.name)
    return source_dir
