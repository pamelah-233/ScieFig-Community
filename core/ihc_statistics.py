from __future__ import annotations

from typing import Any
from core.measurement_history import latest_measurements

IHC_STAT_SOURCES = [
    ("IHC measurements", "ihc_measurement"),
]

IHC_STAT_METRICS = {
    "Positive Area %": "positive_area_percent",
    "Mean DAB Intensity": "mean_dab_intensity",
    "H-score": "h_score",
}


def collect_ihc_stat_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    metric_key = IHC_STAT_METRICS.get(metric, metric)
    images = _image_records(panel)
    rows: list[dict[str, Any]] = []

    for measurement in latest_measurements(panel.analysis.get("ihc_measurements", []),metric_key):
        if not _measurement_is_included(measurement):
            continue
        results = measurement.get("results", {})
        if not isinstance(results, dict):
            continue
        value = results.get(metric_key)
        if not isinstance(value, (int, float)) or value != value:
            continue

        image_uid = str(measurement.get("image_uid", ""))
        image = images.get(image_uid, {})
        image_name = str(
            measurement.get("image_name")
            or image.get("source_name")
            or image.get("name")
            or image_uid
        )
        group = str(measurement.get("group") or image.get("group") or "Ungrouped")
        measurement_uuid = str(measurement.get("measurement_uuid", ""))

        rows.append(
            {
                "source": "ihc_measurement",
                "metric": metric,
                "image_uid": image_uid,
                "image_name": image_name,
                "measurement_uuid": measurement_uuid,
                "lane_label": image_name,
                "sample_key": measurement_uuid or image_uid,
                "group": group,
                "value": float(value),
            }
        )

    return rows


def compute_ihc_statistics(
    panel: Any,
    metric: str,
    group_assignments: dict[str, str],
    reference_group: str,
    test_name: str,
) -> dict[str, Any]:
    from core.statistics import compute_group_statistics

    rows = collect_ihc_stat_values(panel, metric)
    normalized_assignments = {str(key): str(value) for key, value in group_assignments.items()}
    included_rows: list[dict[str, Any]] = []
    for row in rows:
        sample_key = str(row.get("sample_key", ""))
        lane_label = str(row.get("lane_label", ""))
        if normalized_assignments and sample_key not in normalized_assignments and lane_label not in normalized_assignments:
            continue
        included_rows.append(dict(row))

    effective_group_assignments: dict[str, str] = {}
    for row in included_rows:
        sample_key = str(row.get("sample_key", ""))
        lane_label = str(row.get("lane_label", ""))
        default_group = str(row.get("group", lane_label))
        effective_group_assignments[lane_label] = default_group
        if sample_key:
            effective_group_assignments[sample_key] = default_group
    effective_group_assignments.update(normalized_assignments)
    normalized_rows = []
    for row in included_rows:
        sample_key = str(row.get("sample_key", ""))
        normalized = dict(row)
        if sample_key:
            normalized["lane_label"] = sample_key
        normalized_rows.append(normalized)
    from core.statistics_pairing import attach_pair_ids
    normalized_rows = attach_pair_ids(panel, 'ihc_measurement', normalized_rows)
    result = compute_group_statistics(normalized_rows, effective_group_assignments, reference_group, test_name)
    result.update(
        {
            "source": "ihc_measurement",
            "metric": metric,
            "group_assignments": dict(group_assignments),
        }
    )
    panel.analysis["ihc_statistics"] = result
    return result


def _image_records(panel: Any) -> dict[str, dict[str, Any]]:
    images = panel.images() if hasattr(panel, "images") else panel.data.get("ihc_images", [])
    return {str(image.get("uid", "")): dict(image) for image in images}


def _measurement_is_included(measurement: dict[str, Any]) -> bool:
    reliability = dict(measurement.get("reliability", {}))
    if "include_in_statistics" in reliability:
        return bool(reliability.get("include_in_statistics"))
    return bool(measurement.get("include_in_statistics", True))
