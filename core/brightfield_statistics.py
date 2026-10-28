from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
from core.measurement_history import latest_measurements
from core.measurement_scale import apply_area_scale


def physical_area_text_and_excel(value: object, scale: object, *, has_snapshot: bool) -> tuple[str, object]:
    if has_snapshot and apply_area_scale(1, scale if isinstance(scale, Mapping) else None) == 0:
        return "", None
    if isinstance(value, bool) or value is None:
        return "", None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "", None
    if number != number:
        return "", None
    return f"{number:.2f}", value


def _has_valid_area_scale(scale: object) -> bool:
    if not isinstance(scale, Mapping):
        return False
    return bool(scale.get("enabled")) and apply_area_scale(1, scale) != 0


BRIGHTFIELD_STAT_SOURCES = [
    ("Brightfield measurements", "brightfield_measurement"),
]

BRIGHTFIELD_SEGMENTED_REGIONS_LABEL = "Segmented Regions"
BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL = "Segmented Region Count"
BRIGHTFIELD_LEGACY_OBJECT_COUNT_LABEL = "Object Count"
BRIGHTFIELD_AI_OBJECT_COUNT_LABEL = "H&E Fiber Count"

BRIGHTFIELD_STAT_METRICS = {
    "Area Fraction %": "area_fraction_percent",
    BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL: "object_count",
    "Mean Object Area": "mean_object_area_px",
    "ROI Mean Intensity": "roi_mean_intensity",
}

BRIGHTFIELD_STAT_METRIC_ALIASES = {
    BRIGHTFIELD_LEGACY_OBJECT_COUNT_LABEL: BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL,
    BRIGHTFIELD_SEGMENTED_REGIONS_LABEL: BRIGHTFIELD_SEGMENTED_REGION_COUNT_LABEL,
}


def normalize_brightfield_stat_metric(metric: str) -> str:
    normalized = str(metric or "").strip()
    return BRIGHTFIELD_STAT_METRIC_ALIASES.get(normalized, normalized)


def summarize_brightfield_ai_object_count_measurements(panel: Any) -> dict[str, Any]:
    rows = collect_brightfield_ai_stat_values(panel)
    counts = [float(row["ai_object_count"]) for row in rows]
    mean_areas = [
        float(row["ai_object_mean_area_px"])
        for row in rows
        if isinstance(row.get("ai_object_mean_area_px"), (int, float))
    ]
    total_areas = [
        float(row["ai_object_total_area_px"])
        for row in rows
        if isinstance(row.get("ai_object_total_area_px"), (int, float))
    ]
    groups: list[dict[str, Any]] = []
    for group_name in sorted({str(row.get("group", "Ungrouped")) for row in rows}):
        group_rows = [row for row in rows if str(row.get("group", "Ungrouped")) == group_name]
        group_counts = [float(row["ai_object_count"]) for row in group_rows]
        group_areas = [
            float(row["ai_object_mean_area_px"])
            for row in group_rows
            if isinstance(row.get("ai_object_mean_area_px"), (int, float))
        ]
        groups.append(
            {
                "group": group_name,
                "n_measurements": len(group_rows),
                "mean_ai_object_count": float(np.mean(group_counts)) if group_counts else 0.0,
                "median_ai_object_count": float(np.median(group_counts)) if group_counts else 0.0,
                "mean_ai_object_area_px": float(np.mean(group_areas)) if group_areas else 0.0,
                "median_ai_object_area_px": float(np.median(group_areas)) if group_areas else 0.0,
            }
        )
    return {
        "source": "brightfield_ai_object_count",
        "rows": rows,
        "n_measurements": len(rows),
        "mean_ai_object_count": float(np.mean(counts)) if counts else 0.0,
        "median_ai_object_count": float(np.median(counts)) if counts else 0.0,
        "mean_ai_object_area_px": float(np.mean(mean_areas)) if mean_areas else 0.0,
        "median_ai_object_area_px": float(np.median(mean_areas)) if mean_areas else 0.0,
        "total_ai_object_area_px": float(np.sum(total_areas)) if total_areas else 0.0,
        "groups": groups,
    }


def collect_brightfield_ai_stat_values(panel: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for measurement in latest_measurements(panel.analysis.get("brightfield_ai_object_count_measurements", []),'ai_object_count'):
        if not _measurement_is_included(measurement):
            continue
        results = measurement.get("results", {})
        if not isinstance(results, dict):
            continue
        count = results.get("ai_object_count")
        if not isinstance(count, (int, float)) or count != count:
            continue
        rows.append(
            {
                "source": "brightfield_ai_object_count",
                "measurement_uuid": str(measurement.get("measurement_uuid", "")),
                "image_uid": str(measurement.get("image_uid", "")),
                "image_name": str(measurement.get("image_name", "")),
                "group": str(measurement.get("group", "Ungrouped")) or "Ungrouped",
                "stain_type": str(measurement.get("stain_type", "")),
                "ai_object_count": float(count),
                "ai_object_mean_area_px": results.get("ai_object_mean_area_px"),
                "ai_object_total_area_px": results.get("ai_object_total_area_px"),
            }
        )
    return rows


def collect_brightfield_ai_plot_values(panel: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for measurement in latest_measurements(panel.analysis.get("brightfield_ai_object_count_measurements", []),'ai_object_count'):
        if not _measurement_is_included(measurement):
            continue
        results = measurement.get("results", {})
        if not isinstance(results, dict):
            continue
        value = results.get("ai_object_count")
        if not isinstance(value, (int, float)) or value != value:
            continue
        image_uid = str(measurement.get("image_uid", ""))
        measurement_uuid = str(measurement.get("measurement_uuid", ""))
        image_name = str(measurement.get("image_name", "") or image_uid or measurement_uuid)
        rows.append(
            {
                "source": "brightfield_ai_object_count",
                "metric": BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
                "measurement_uuid": measurement_uuid,
                "image_uid": image_uid,
                "image_name": image_name,
                "lane_label": measurement_uuid or image_uid or image_name,
                "sample_key": measurement_uuid or image_uid or image_name,
                "group": str(measurement.get("group", "Ungrouped")) or "Ungrouped",
                "value": float(value),
                "series_label": BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
            }
        )
    return rows


def collect_brightfield_ai_object_area_distribution_rows(panel: Any) -> list[dict[str, Any]]:
    """Return included H&E measurements as independent fiber-area series."""
    rows: list[dict[str, Any]] = []
    for measurement in latest_measurements(panel.analysis.get("brightfield_ai_object_count_measurements", []),'ai_object_count'):
        if not _measurement_is_included(measurement):
            continue
        results = measurement.get("results", {})
        if not isinstance(results, dict):
            continue
        scale = dict(measurement.get("measurement_scale_bar", {}))
        use_um2 = _has_valid_area_scale(scale)
        value_key = "ai_object_areas_um2" if use_um2 else "ai_object_areas_px"
        values = [
            float(value)
            for value in results.get(value_key, [])
            if isinstance(value, (int, float)) and np.isfinite(float(value)) and float(value) >= 0.0
        ]
        if not values:
            continue
        measurement_uuid = str(measurement.get("measurement_uuid", ""))
        image_uid = str(measurement.get("image_uid", ""))
        image_name = str(measurement.get("image_name", "") or image_uid or measurement_uuid)
        rows.append(
            {
                "source": "brightfield_ai_object_count",
                "metric": "he_fiber_area_distribution_um2" if use_um2 else "he_fiber_area_distribution_px",
                "unit": "um^2" if use_um2 else "px^2",
                "measurement_uuid": measurement_uuid,
                "image_uid": image_uid,
                "image_name": image_name,
                "lane_label": f"{image_name} (n={len(values)})",
                "sample_key": measurement_uuid or image_uid or image_name,
                "group": str(measurement.get("group", "Ungrouped")) or "Ungrouped",
                "values": values,
            }
        )
    return rows


def collect_brightfield_stat_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    normalized_metric = normalize_brightfield_stat_metric(metric)
    metric_key = BRIGHTFIELD_STAT_METRICS.get(normalized_metric, normalized_metric)
    images = _image_records(panel)
    buckets: dict[str, dict[str, Any]] = {}

    for measurement in latest_measurements(panel.analysis.get("brightfield_measurements", []),metric_key):
        if not _measurement_is_included(measurement):
            continue
        results = measurement.get("results", {})
        if not isinstance(results, dict):
            continue
        value = results.get(metric_key)
        if not isinstance(value, (int, float)) or value != value:
            continue

        image_uid = str(measurement.get("image_uid", ""))
        _image_index, image = images.get(image_uid, (len(images), {}))
        image_name = str(
            measurement.get("image_name")
            or image.get("source_name")
            or image.get("name")
            or image_uid
        )
        group = str(measurement.get("group") or image.get("group") or "Ungrouped")
        measurement_uuid = str(measurement.get("measurement_uuid", ""))
        bucket = buckets.setdefault(
            image_uid,
            {
                "source": "brightfield_measurement",
                "metric": normalized_metric,
                "image_uid": image_uid,
                "image_name": image_name,
                "measurement_uuids": [],
                "group": group,
                "values": [],
            },
        )
        bucket["values"].append(float(value))
        bucket["measurement_uuids"].append(measurement_uuid)

    rows: list[dict[str, Any]] = []
    for image_uid, bucket in sorted(
        buckets.items(),
        key=lambda item: images.get(item[0], (999999, {}))[0],
    ):
        values = [float(value) for value in bucket.get("values", [])]
        if not values:
            continue
        row = {
            key: value
            for key, value in bucket.items()
            if key != "values"
        }
        row.update(
            {
                "lane_label": str(bucket.get("image_name", image_uid)),
                "sample_key": image_uid,
                "value": float(np.mean(values)),
                "n_measurements": len(values),
                "aggregation": "mean_per_image",
            }
        )
        rows.append(row)

    return rows


def compute_brightfield_statistics(
    panel: Any,
    metric: str,
    group_assignments: dict[str, str],
    reference_group: str,
    test_name: str,
) -> dict[str, Any]:
    from core.statistics import compute_group_statistics

    normalized_metric = normalize_brightfield_stat_metric(metric)
    rows = collect_brightfield_stat_values(panel, normalized_metric)
    effective_group_assignments: dict[str, str] = {}
    normalized_assignments = {str(key): str(value) for key, value in group_assignments.items()}
    for row in rows:
        sample_key = str(row.get("sample_key", ""))
        lane_label = str(row.get("lane_label", ""))
        default_group = str(row.get("group", lane_label))
        effective_group_assignments[lane_label] = default_group
        if sample_key:
            effective_group_assignments[sample_key] = default_group
    effective_group_assignments.update(normalized_assignments)
    normalized_rows = []
    for row in rows:
        sample_key = str(row.get("sample_key", ""))
        normalized = dict(row)
        if sample_key:
            normalized["lane_label"] = sample_key
        normalized_rows.append(normalized)
    from core.statistics_pairing import attach_pair_ids
    normalized_rows = attach_pair_ids(panel, 'brightfield_measurement', normalized_rows)
    result = compute_group_statistics(normalized_rows, effective_group_assignments, reference_group, test_name)
    result.update(
        {
            "source": "brightfield_measurement",
            "metric": normalized_metric,
            "group_assignments": dict(group_assignments),
            "rows": rows,
            "series_label": normalized_metric,
        }
    )
    panel.analysis["brightfield_statistics"] = result
    return result


def compute_brightfield_ai_object_count_statistics(
    panel: Any,
    group_assignments: dict[str, str],
    reference_group: str,
    test_name: str,
) -> dict[str, Any]:
    from core.statistics import compute_group_statistics

    rows = collect_brightfield_ai_plot_values(panel)
    effective_group_assignments: dict[str, str] = {}
    normalized_assignments = {str(key): str(value) for key, value in group_assignments.items()}
    for row in rows:
        sample_key = str(row.get("sample_key", ""))
        lane_label = str(row.get("lane_label", ""))
        default_group = str(row.get("group", lane_label or "Ungrouped"))
        if lane_label:
            effective_group_assignments[lane_label] = default_group
        if sample_key:
            effective_group_assignments[sample_key] = default_group
    effective_group_assignments.update(normalized_assignments)
    normalized_rows = []
    for row in rows:
        normalized = dict(row)
        sample_key = str(row.get("sample_key", ""))
        if sample_key:
            normalized["lane_label"] = sample_key
        normalized_rows.append(normalized)
    from core.statistics_pairing import attach_pair_ids
    normalized_rows = attach_pair_ids(panel, 'brightfield_ai_object_count', normalized_rows)
    result = compute_group_statistics(normalized_rows, effective_group_assignments, reference_group, test_name)
    result.update(
        {
            "source": "brightfield_ai_object_count",
            "metric": BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
            "group_assignments": dict(group_assignments),
            "rows": rows,
            "series_label": BRIGHTFIELD_AI_OBJECT_COUNT_LABEL,
        }
    )
    panel.analysis["brightfield_ai_object_count_plot_statistics"] = result
    return result


def _image_records(panel: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    images = panel.images() if hasattr(panel, "images") else panel.data.get("brightfield_images", [])
    return {str(image.get("uid", "")): (index, dict(image)) for index, image in enumerate(images)}


def _measurement_is_included(measurement: dict[str, Any]) -> bool:
    reliability = dict(measurement.get("reliability", {}))
    if "include_in_statistics" in reliability:
        return bool(reliability.get("include_in_statistics"))
    return bool(measurement.get("include_in_statistics", True))
