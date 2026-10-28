"""Isolated, sequential microscopy analysis with append-only result application."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable
from uuid import uuid4

from core.measurement_scale import positive_float
from core.utils.time import utc_now_iso


# Public method -> panel type, panel result collection, image result collection.
METHODS = {
    "count_channel_objects": ("fluorescence", "object_counts", "object_counts"),
    "run_ai_assisted_analysis": ("fluorescence", "fluorescence_ai_assisted_measurements", "fluorescence_ai_assisted_measurements"),
    "run_smart_assisted_analysis": ("fluorescence", "fluorescence_smart_assisted_measurements", "fluorescence_smart_assisted_measurements"),
    "measure_bright_round_cells": ("brightfield", "bright_round_cell_diameter_measurements", None),
    "create_measurement": ("brightfield", "brightfield_measurements", "measurements"),
    "auto_analyze_he": ("brightfield", "brightfield_measurements", "measurements"),
    "create_profile_preset_measurement": ("brightfield", "brightfield_measurements", "measurements"),
    "create_ai_object_count_measurement": ("brightfield", "brightfield_ai_object_count_measurements", None),
    "measure_dab_positive_area": ("ihc", "ihc_measurements", "measurements"),
    "auto_analyze_dab": ("ihc", "ihc_measurements", "measurements"),
}


def measurement_id(record: dict) -> str:
    for key in ("measurement_uuid", "ai_measurement_uuid", "smart_measurement_uuid", "count_uuid"):
        if record.get(key):
            return str(record[key])
    return ""


def _preset_updates(preset: dict | None) -> dict:
    if preset is None:
        return {}
    x, y = positive_float(preset.get("um_per_px_x")), positive_float(preset.get("um_per_px_y"))
    if not x or not y or not preset.get("id") or not str(preset.get("name", "")).strip():
        raise ValueError("A named calibration preset requires positive X and Y pixel sizes.")
    return dict(um_per_px=x, um_per_px_x=x, um_per_px_y=y, calibration_source="manual",
                preset_id=preset["id"], preset_name=preset["name"])


@dataclass
class BatchImageResult:
    image_uid: str
    status: str
    provenance: dict[str, Any]
    measurements: list[dict[str, Any]] = field(default_factory=list)
    calibration: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class BatchAnalysisJob:
    """Create on the GUI thread; run in a worker; apply each result on the GUI thread.

    The optional runner has the same contract as the dispatch below and is useful
    for callers that already own an analysis boundary. Cancellation only occurs
    between images: a completed image is never discarded.
    """

    def __init__(self, panel, image_uids, method: str, options: dict | None = None, *,
                 calibration_preset: dict | None = None, runner: Callable | None = None):
        if method not in METHODS or METHODS[method][0] != panel.panel_type:
            raise ValueError(f"Unsupported batch analysis method: {method}")
        self.image_uids = list(dict.fromkeys(str(uid) for uid in image_uids))
        if not self.image_uids:
            raise ValueError("Select at least one image for batch analysis.")
        known = {str(image["uid"]) for image in panel.images()}
        if any(uid not in known for uid in self.image_uids):
            raise KeyError("A selected image is no longer available.")
        self.options = deepcopy(dict(options or {}))
        if "image_uid" in self.options:
            raise ValueError("Batch image identifiers must come from the image selection.")
        self.method = method
        self.calibration_preset = deepcopy(calibration_preset)
        self.calibration_updates = _preset_updates(calibration_preset)
        self.runner = runner
        self.batch_id = str(uuid4())
        self.started_at = utc_now_iso()
        self._cancelled = Event()
        self._source = self._snapshot(panel, self.image_uids)

    @staticmethod
    def _snapshot(panel, image_uids):
        payload = deepcopy(panel.to_dict())
        image_key = f"{panel.panel_type}_images"
        payload["data"][image_key] = [image for image in payload["data"].get(image_key, [])
                                      if str(image["uid"]) in image_uids]
        snapshot = type(panel).from_dict(payload)
        if hasattr(snapshot, "borrow_readonly_image_data"):
            snapshot.IMAGE_CACHE_BYTE_BUDGET = panel.IMAGE_CACHE_BYTE_BUDGET
            try:
                snapshot.borrow_readonly_image_data(panel)
            except Exception:
                snapshot.dispose()
                raise
        return snapshot

    def cancel(self) -> None:
        self._cancelled.set()

    def run(self, *, on_result: Callable | None = None, on_progress: Callable | None = None) -> list[BatchImageResult]:
        results = []
        images = {str(image["uid"]): image for image in self._source.images()}
        try:
            for index, uid in enumerate(self.image_uids):
                image = images[uid]
                provenance = dict(batch_id=self.batch_id, started_at=self.started_at,
                    panel_uuid=self._source.panel_uuid, image_uid=uid,
                    source_name=str(image.get("source_name", "")), source_path=str(image.get("source_path", "")),
                    source_hash=str(image.get("source_hash", "")), source_metadata=deepcopy(image.get("metadata", {})),
                    method=self.method, settings=deepcopy(self.options), calibration_preset=deepcopy(self.calibration_preset))
                if self._cancelled.is_set():
                    item = BatchImageResult(uid, "cancelled", provenance)
                else:
                    item = self._run_image(uid, provenance)
                results.append(item)
                if on_result is not None:
                    on_result(item)
                if on_progress is not None:
                    on_progress(index + 1, len(self.image_uids), uid)
        finally:
            if hasattr(self._source, "dispose"):
                self._source.dispose()
        return results

    def _run_image(self, uid: str, provenance: dict) -> BatchImageResult:
        snapshot = None
        calibration = {}
        try:
            snapshot = self._snapshot(self._source, [uid])
            if self.calibration_updates:
                snapshot.update_measurement_scale_bar(uid, **self.calibration_updates)
            calibration = snapshot.measurement_scale_bar(uid)
            provenance["calibration"] = deepcopy(calibration)
            options = deepcopy(self.options)
            output = (self.runner(snapshot, uid, self.method, options) if self.runner is not None
                      else getattr(snapshot, self.method)(uid, **options))
            records = [output] if isinstance(output, dict) else list(output or [])
            if not records:
                raise ValueError("Analysis returned no measurement records; the backend may be unavailable.")
            if any(not isinstance(record, dict) or not measurement_id(record) for record in records):
                raise ValueError("Analysis did not return identifiable measurement records.")
            provenance["completed_at"] = utc_now_iso()
            records = deepcopy(records)
            for record in records:
                record["batch_provenance"] = deepcopy(provenance)
            return BatchImageResult(uid, "success", provenance, records, calibration)
        except Exception as exc:
            provenance["completed_at"] = utc_now_iso()
            return BatchImageResult(uid, "failed", provenance, calibration=calibration, error=str(exc) or type(exc).__name__)
        finally:
            if snapshot is not None and hasattr(snapshot, "dispose"):
                snapshot.dispose()


def apply_batch_image_result(panel, result: BatchImageResult) -> bool:
    """Append successful measurements only; never replace a panel or old result."""
    if result.status != "success":
        return False
    if result.provenance["panel_uuid"] != panel.panel_uuid:
        raise ValueError("The batch result belongs to another panel.")
    image = next((image for image in panel.images() if str(image["uid"]) == result.image_uid), None)
    if image is None:
        raise KeyError("The batch image is no longer available.")
    method = result.provenance["method"]
    _, analysis_key, image_key = METHODS[method]
    known = {measurement_id(row) for row in panel.analysis.get(analysis_key, [])}
    rows = [deepcopy(row) for row in result.measurements if measurement_id(row) not in known]
    if not rows:
        return False
    updates = _preset_updates(result.provenance.get("calibration_preset"))
    if updates:
        panel.update_measurement_scale_bar(result.image_uid, **updates)
    panel.analysis.setdefault(analysis_key, []).extend(rows)
    if image_key is not None:
        image.setdefault(image_key, []).extend(rows)
    if method == "measure_bright_round_cells":
        panel.analysis["bright_round_cell_diameter"] = rows[-1]
    statistics_key = "fluoro_statistics" if panel.panel_type == "fluorescence" else f"{panel.panel_type}_statistics"
    panel.analysis.pop(statistics_key, None)
    if method == "create_ai_object_count_measurement":
        panel.analysis.pop("brightfield_ai_object_count_statistics", None)
        panel.analysis.pop("brightfield_ai_object_count_plot_statistics", None)
    panel.append_audit("batch_analysis", f"{result.provenance['batch_id']}; {result.image_uid}; {method}")
    return True
