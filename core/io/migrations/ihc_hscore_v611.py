from __future__ import annotations

from math import isfinite
from typing import Any

from core.utils.time import utc_now_iso

_LEGACY_METHOD = "binary_simplified_3x_positive"


def migrate_ihc_hscore_v611(panel_data: dict) -> list[dict]:
    """Mutate V6.1 binary-3x IHC measurements in place.

    The function accepts either a full serialized IHC panel payload or the
    nested analysis/data payload used by tests and legacy helpers. It does not
    instantiate panels, call Qt, or update modified timestamps.
    """
    if not isinstance(panel_data, dict):
        return []

    entries: list[dict] = []
    audited_keys: set[str] = set()
    for measurement, audit_key in _iter_ihc_measurements(panel_data):
        if not isinstance(measurement, dict):
            continue
        migrated = _migrate_measurement(measurement)
        if not migrated:
            continue
        if audit_key in audited_keys:
            continue
        audited_keys.add(audit_key)
        entry = _audit_entry(measurement)
        audit_log = _audit_log(panel_data)
        audit_log.append(entry)
        entries.append(entry)
    return entries


def _iter_ihc_measurements(panel_data: dict) -> list[tuple[dict, str]]:
    measurements: list[tuple[dict, str]] = []

    def add_collection(items: list[Any], source_name: str, fallback_image_uid: str = "") -> None:
        ordinal_by_image_uid: dict[str, int] = {}
        fallback_index = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            audit_key = _measurement_audit_key(item, ordinal_by_image_uid, source_name, fallback_index, fallback_image_uid)
            measurements.append((item, audit_key))
            fallback_index += 1

    direct_measurements = panel_data.get("ihc_measurements", [])
    if isinstance(direct_measurements, list):
        add_collection(direct_measurements, "direct")

    analysis = panel_data.get("analysis", {})
    if isinstance(analysis, dict):
        analysis_measurements = analysis.get("ihc_measurements", [])
        if isinstance(analysis_measurements, list):
            add_collection(analysis_measurements, "analysis")

    data = panel_data.get("data", panel_data)
    if isinstance(data, dict):
        images = data.get("ihc_images", [])
        if isinstance(images, list):
            for image_index, image in enumerate(images):
                if not isinstance(image, dict):
                    continue
                image_measurements = image.get("measurements", [])
                if isinstance(image_measurements, list):
                    fallback_image_uid = str(image.get("uid", "")).strip()
                    add_collection(image_measurements, f"image:{image_index}", fallback_image_uid)

    return measurements


def _migrate_measurement(measurement: dict[str, Any]) -> bool:
    results = measurement.get("results")
    analysis_metadata = measurement.get("analysis_metadata")
    if not isinstance(results, dict) or not isinstance(analysis_metadata, dict):
        return False

    gmm_meta = analysis_metadata.get("gmm_classification")
    if not isinstance(gmm_meta, dict) or gmm_meta.get("h_score_method") != _LEGACY_METHOD:
        return False

    positive_percent = _float_or_none(results.get("positive_percent"))
    if positive_percent is None:
        return False

    # Preserve pre-migration values for audit and traceability.
    old_h_score = _float_or_zero(results.get("h_score", 0.0))
    old_strong = _float_or_zero(results.get("strong_percent", 0.0))
    old_moderate = _float_or_zero(results.get("moderate_percent", 0.0))
    old_weak = _float_or_zero(results.get("weak_percent", 0.0))

    # Force-write a V6.1.1 conservative top-level snapshot.
    # Do not trust legacy weak/moderate/strong values.
    results["h_score"] = positive_percent
    results["weak_percent"] = positive_percent
    results["moderate_percent"] = 0.0
    results["strong_percent"] = 0.0

    # Synchronize nested gmm_classification with top-level results.
    gmm_meta["h_score"] = positive_percent
    gmm_meta["weak_percent"] = positive_percent
    gmm_meta["moderate_percent"] = 0.0
    gmm_meta["strong_percent"] = 0.0
    gmm_meta["positive_percent"] = positive_percent

    nested_percentages = gmm_meta.get("percentages")
    if isinstance(nested_percentages, dict):
        nested_percentages["weak"] = positive_percent
        nested_percentages["moderate"] = 0.0
        nested_percentages["strong"] = 0.0
        nested_percentages["positive"] = positive_percent

    expected_old = 3.0 * positive_percent
    tolerance = max(1e-3, 0.01 * max(abs(old_h_score), 1.0))
    if abs(old_h_score - expected_old) > tolerance:
        gmm_meta["_migration_warning"] = (
            f"Pre-migration H-score ({old_h_score:.4f}) was not consistent with "
            f"3 * positive_percent ({expected_old:.4f}); new conservative H-score "
            f"was set from positive_percent."
        )

    gmm_meta["h_score_method"] = "gmm_2_component_positive_conservative"
    gmm_meta["_pre_migration_h_score"] = old_h_score
    gmm_meta["_pre_migration_h_score_method"] = _LEGACY_METHOD
    gmm_meta["_pre_migration_weak_percent"] = old_weak
    gmm_meta["_pre_migration_moderate_percent"] = old_moderate
    gmm_meta["_pre_migration_strong_percent"] = old_strong
    gmm_meta["_migration_source"] = "v61_binary_3x_to_conservative"
    gmm_meta["_migration_applied_at"] = utc_now_iso()
    gmm_meta["_migration_note"] = (
        "V6.1.1 conservative semantics applied: h_score = positive_percent; "
        "weak_percent = positive_percent; moderate_percent = strong_percent = 0; "
        "nested gmm_classification synchronized."
    )
    return True


def _audit_entry(measurement: dict[str, Any]) -> dict[str, str]:
    results = measurement.get("results", {})
    gmm_meta = measurement.get("analysis_metadata", {}).get("gmm_classification", {})
    old_h_score = _float_or_zero(gmm_meta.get("_pre_migration_h_score", 0.0))
    new_h_score = _float_or_zero(results.get("h_score", 0.0)) if isinstance(results, dict) else 0.0
    return {
        "action": "h_score_migration_v611",
        "timestamp": utc_now_iso(),
        "detail": (
            f"measurement_uuid={measurement.get('measurement_uuid', '')}; "
            f"image_uid={measurement.get('image_uid', '')}; "
            f"old_h_score={old_h_score:.4f}; new_h_score={new_h_score:.4f}"
        ),
    }


def _audit_log(panel_data: dict[str, Any]) -> list[dict]:
    audit_log = panel_data.get("audit_log")
    if not isinstance(audit_log, list):
        audit_log = []
        panel_data["audit_log"] = audit_log
    return audit_log


def _measurement_audit_key(
    measurement: dict[str, Any],
    ordinal_by_image_uid: dict[str, int],
    source_name: str,
    fallback_index: int,
    fallback_image_uid: str = "",
) -> str:
    measurement_uuid = str(measurement.get("measurement_uuid", "")).strip()
    if measurement_uuid:
        return f"uuid:{measurement_uuid}"

    image_uid = str(measurement.get("image_uid", "") or fallback_image_uid).strip()
    if image_uid:
        ordinal = ordinal_by_image_uid.get(image_uid, 0)
        ordinal_by_image_uid[image_uid] = ordinal + 1
        return f"image:{image_uid}:ordinal:{ordinal}"

    return f"fallback:{source_name}:{fallback_index}"


def _float_or_none(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(result):
        return None
    return result


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
