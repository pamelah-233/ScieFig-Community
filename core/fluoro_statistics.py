from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json

import numpy as np

from core.measurement_scale import apply_area_scale
from core.statistics import STAT_TESTS, compute_group_statistics


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


FLUOR_STAT_SOURCES = [
    ("ROI intensity", "roi_intensity"),
    ("Object count", "object_count"),
    ("AI-assisted", "ai_assisted"),
]

FLUOR_STAT_METRICS = {
    "roi_intensity": [
        ("Mean intensity", "mean"),
        ("Integrated density", "integrated_density"),
        ("Area", "area_px"),
    ],
    "object_count": [
        ("Count", "count"),
        ("Mean area", "mean_object_area_px"),
        ("Mean intensity", "mean_intensity"),
    ],
    "smart_assisted": [
        ("Smart object count", "smart_object_count"),
        ("Total object area", "total_object_area_px"),
        ("Mean object area", "mean_object_area_px"),
        ("Mean intensity", "mean_intensity"),
        ("Integrated intensity", "integrated_intensity"),
        ("Background estimate", "background_estimate"),
    ],
    "ai_assisted": [
        ("AI object count", "ai_object_count"),
        ("Total object area", "total_object_area_px"),
        ("Total object area (um²)", "total_object_area_um2"),
        ("Mean object area", "mean_object_area_px"),
        ("Mean object area (um²)", "mean_object_area_um2"),
        ("Mean intensity", "mean_intensity"),
        ("Integrated intensity", "integrated_intensity"),
    ],
}


def collect_fluoro_stat_values(panel: Any, source: str, metric: str) -> list[dict[str, Any]]:
    if source == "roi_intensity":
        return _collect_roi_intensity_values(panel, metric)
    if source == "object_count":
        return _collect_object_count_values(panel, metric)
    if source == "smart_assisted":
        return _collect_smart_assisted_values(panel, metric)
    if source == "ai_assisted":
        return _collect_ai_assisted_values(panel, metric)
    return []


def collect_fluoro_distribution_rows(panel: Any, source: str) -> list[dict[str, Any]]:
    if source == "ai_assisted":
        return _collect_ai_assisted_distribution_rows(panel)
    return []


def compute_fluoro_statistics(panel: Any, source: str, metric: str, group_assignments: dict[str, str], reference_group: str, test_name: str) -> dict[str, Any]:
    rows = collect_fluoro_stat_values(panel, source, metric)
    if group_assignments:
        rows=[row for row in rows if any(str(row.get(key,'')) in group_assignments for key in ('sample_group_key','sample_key','lane_label'))]
    from core.statistics_pairing import attach_pair_ids
    rows = attach_pair_ids(panel, source, rows)
    result = compute_group_statistics(rows, group_assignments, reference_group, test_name)
    result.update(
        {
            "source": source,
            "metric": metric,
            "group_assignments": dict(group_assignments),
        }
    )
    panel.analysis["fluoro_statistics"] = result
    return result


def _collect_roi_intensity_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int], dict[str, Any]] = {}
    images = _image_records(panel)
    for measurement in panel.analysis.get("measurements", []):
        if measurement.get("type") != "roi_intensity":
            continue
        for result in measurement.get("results_table", []):
            value = result.get(metric)
            if not isinstance(value, (int, float)) or value != value:
                continue
            image_uid = str(measurement.get("image_uuid", measurement.get("image_uid", "")))
            channel_index = int(result.get("channel_index", 0))
            channel_name = str(result.get("channel", f"Ch{int(result.get('channel_index', 0)) + 1}"))
            bucket = buckets.setdefault(
                (image_uid, channel_index),
                {
                    "source": "roi_intensity",
                    "metric": metric,
                    "image_uid": image_uid,
                    "channel_index": channel_index,
                    "channel_name": channel_name,
                    "measurement_uuids": [],
                    "values": [],
                },
            )
            bucket["values"].append(float(value))
            bucket["measurement_uuids"].append(str(measurement.get("measurement_uuid", "")))
    return _sample_rows_from_buckets(buckets, images)


def _collect_object_count_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    from core.image_analysis.fluorescence_counting import count_provenance
    buckets: dict[tuple[str, int, str], dict[str, Any]] = {}
    images = _image_records(panel)
    for result in panel.analysis.get("object_counts", []):
        value = result.get(metric)
        if not isinstance(value, (int, float)) or value != value:
            continue
        image_uid = str(result.get("image_uid", ""))
        channel_index = int(result.get("channel_index", 0))
        channel_name = str(result.get("channel_name", f"Ch{int(result.get('channel_index', 0)) + 1}"))
        method_name,parameters=count_provenance(result)
        context=json.dumps({'method':method_name,'parameters':parameters,
            'background_correction':result.get('background_correction',{})},sort_keys=True,ensure_ascii=False)
        configuration_index=1+sum(key[0]==image_uid and key[1]==channel_index for key in buckets)
        bucket = buckets.setdefault(
            (image_uid, channel_index, context),
            {
                "source": "object_count",
                "sample_key_context": context,
                "lane_label_context": f'{method_name} [config {configuration_index}]',
                "counting_method": method_name,
                "metric": metric,
                "image_uid": image_uid,
                "channel_index": channel_index,
                "channel_name": channel_name,
                "count_uuids": [],
                "values": [],
            },
        )
        bucket["values"]=[float(value)]
        bucket["count_uuids"]=[str(result.get("count_uuid", ""))]
    return _sample_rows_from_buckets(buckets, images)


def _collect_smart_assisted_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int, str], dict[str, Any]] = {}
    images = _image_records(panel)
    for record in panel.analysis.get("fluorescence_smart_assisted_measurements", []):
        value = _smart_metric_value(record, metric)
        if not isinstance(value, (int, float)) or value != value:
            continue
        image_uid = str(record.get("image_uid", record.get("image_uuid", "")))
        channel_index = int(record.get("channel_index", 0))
        channel_name = str(record.get("channel_name", f"Ch{channel_index + 1}"))
        metadata = dict(record.get("metadata", {}))
        effective_settings_hash = str(metadata.get("effective_settings_hash", ""))
        smart_panel_context_hash = str(
            record.get("smart_panel_context_hash")
            or metadata.get("smart_panel_context_hash")
            or "legacy_unknown_context"
        )
        input_preprocessing_context = dict(
            record.get("input_preprocessing_context")
            or metadata.get("input_preprocessing_context")
            or {}
        )
        roi_or_geometry = dict(record.get("roi_or_geometry") or metadata.get("roi_context") or {})
        bucket = buckets.setdefault(
            (image_uid, channel_index, smart_panel_context_hash),
            {
                "source": "smart_assisted",
                "metric": metric,
                "image_uid": image_uid,
                "channel_index": channel_index,
                "channel_name": channel_name,
                "smart_panel_context_hash": smart_panel_context_hash,
                "context_missing": smart_panel_context_hash == "legacy_unknown_context",
                "effective_settings_hash": effective_settings_hash,
                "core_effective_settings_hash": effective_settings_hash,
                "input_preprocessing_context": input_preprocessing_context,
                "roi_or_geometry": roi_or_geometry,
                "sample_key_context": f"smart_panel_context_hash={smart_panel_context_hash}",
                "lane_label_context": _short_context_label(smart_panel_context_hash),
                "smart_measurement_uuids": [],
                "analysis_versions": [],
                "effective_settings_hashes": [],
                "values": [],
            },
        )
        bucket["values"]=[float(value)]
        bucket["smart_measurement_uuids"]=[str(record.get("smart_measurement_uuid", ""))]
        analysis_version = str(record.get("analysis_version", ""))
        if analysis_version and analysis_version not in bucket["analysis_versions"]:
            bucket["analysis_versions"].append(analysis_version)
        if effective_settings_hash and effective_settings_hash not in bucket["effective_settings_hashes"]:
            bucket["effective_settings_hashes"].append(effective_settings_hash)
    return _sample_rows_from_buckets(buckets, images)


def _smart_metric_value(record: dict[str, Any], metric: str) -> Any:
    if metric != "background_estimate":
        return record.get(metric)
    background = record.get("background_estimate")
    if not isinstance(background, dict):
        return background
    correction = background.get("image_background_correction")
    if isinstance(correction, dict):
        channel_backgrounds = correction.get("channel_backgrounds")
        if isinstance(channel_backgrounds, dict):
            channel_index = int(record.get("channel_index", 0))
            for key in (channel_index, str(channel_index)):
                value = channel_backgrounds.get(key)
                if isinstance(value, (int, float)):
                    return value
    for key in ("value", "mean", "background_value", "background_mean", "estimated_background"):
        value = background.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def _collect_ai_assisted_values(panel: Any, metric: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int, str], dict[str, Any]] = {}
    images = _image_records(panel)
    for record in panel.analysis.get("fluorescence_ai_assisted_measurements", []):
        value = record.get(metric)
        if not isinstance(value, (int, float)) or value != value:
            continue
        image_uid = str(record.get("image_uid", record.get("image_uuid", "")))
        channel_index = int(record.get("channel_index", 0))
        channel_name = str(record.get("channel_name", f"Ch{channel_index + 1}"))
        metadata = dict(record.get("metadata", {}))
        effective_settings_hash = str(metadata.get("effective_settings_hash", ""))
        ai_panel_context_hash = str(
            record.get("ai_panel_context_hash")
            or metadata.get("ai_panel_context_hash")
            or "legacy_unknown_context"
        )
        input_preprocessing_context = dict(
            record.get("input_preprocessing_context")
            or metadata.get("input_preprocessing_context")
            or {}
        )
        roi_or_geometry = dict(record.get("roi_or_geometry") or metadata.get("roi_context") or {})
        bucket = buckets.setdefault(
            (image_uid, channel_index, ai_panel_context_hash),
            {
                "source": "ai_assisted",
                "metric": metric,
                "image_uid": image_uid,
                "channel_index": channel_index,
                "channel_name": channel_name,
                "ai_panel_context_hash": ai_panel_context_hash,
                "context_missing": ai_panel_context_hash == "legacy_unknown_context",
                "effective_settings_hash": effective_settings_hash,
                "core_effective_settings_hash": effective_settings_hash,
                "model_id": str(metadata.get("model_id", "")),
                "model_version": str(metadata.get("model_version", "")),
                "ai_pack_postprocess": str(metadata.get("ai_pack_postprocess", "")),
                "model_commercial_use_status": str(metadata.get("model_commercial_use_status", "")),
                "model_execution_mode": str(metadata.get("model_execution_mode", "")),
                "input_preprocessing_context": input_preprocessing_context,
                "roi_or_geometry": roi_or_geometry,
                "sample_key_context": f"ai_panel_context_hash={ai_panel_context_hash}",
                "lane_label_context": _short_context_label(ai_panel_context_hash),
                "ai_measurement_uuids": [],
                "analysis_versions": [],
                "effective_settings_hashes": [],
                "values": [],
            },
        )
        bucket["values"]=[float(value)]
        bucket["ai_measurement_uuids"]=[str(record.get("ai_measurement_uuid", ""))]
        analysis_version = str(record.get("analysis_version", ""))
        if analysis_version and analysis_version not in bucket["analysis_versions"]:
            bucket["analysis_versions"].append(analysis_version)
        if effective_settings_hash and effective_settings_hash not in bucket["effective_settings_hashes"]:
            bucket["effective_settings_hashes"].append(effective_settings_hash)
    return _sample_rows_from_buckets(buckets, images)


def _collect_ai_assisted_distribution_rows(panel: Any) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int, str], dict[str, Any]] = {}
    images = _image_records(panel)
    for record in panel.analysis.get("fluorescence_ai_assisted_measurements", []):
        scale = dict(record.get("measurement_scale_bar", {}))
        use_um2 = _has_valid_area_scale(scale)
        value_key = "object_area_distribution_um2" if use_um2 else "object_area_distribution_px"
        values = [
            float(value)
            for value in record.get(value_key, [])
            if isinstance(value, (int, float)) and np.isfinite(float(value)) and float(value) >= 0.0
        ]
        image_uid = str(record.get("image_uid", record.get("image_uuid", "")))
        channel_index = int(record.get("channel_index", 0))
        channel_name = str(record.get("channel_name", f"Ch{channel_index + 1}"))
        metadata = dict(record.get("metadata", {}))
        ai_panel_context_hash = str(
            record.get("ai_panel_context_hash")
            or metadata.get("ai_panel_context_hash")
            or "legacy_unknown_context"
        )
        bucket = buckets.setdefault(
            (image_uid, channel_index, ai_panel_context_hash),
            {
                "source": "ai_assisted",
                "metric": "object_area_distribution_um2" if use_um2 else "object_area_distribution_px",
                "unit": "um^2" if use_um2 else "px^2",
                "image_uid": image_uid,
                "channel_index": channel_index,
                "channel_name": channel_name,
                "ai_panel_context_hash": ai_panel_context_hash,
                "sample_key_context": f"ai_panel_context_hash={ai_panel_context_hash}",
                "lane_label_context": _short_context_label(ai_panel_context_hash),
                "ai_measurement_uuids": [],
                "values": [],
            },
        )
        bucket["values"]=values
        bucket["ai_measurement_uuids"]=[str(record.get("ai_measurement_uuid", ""))]
    rows: list[dict[str, Any]] = []
    for bucket_key, bucket in sorted(
        buckets.items(),
        key=lambda item: (images.get(str(item[0][0]), (999999, {}))[0], int(item[0][1]), str(item[0][2])),
    ):
        image_uid = str(bucket_key[0])
        channel_index = int(bucket_key[1])
        image_index, image = images.get(image_uid, (len(images), {}))
        image_name = str(image.get("source_name") or f"Image {image_index + 1}")
        channel_name = str(bucket.get("channel_name", f"Ch{channel_index + 1}"))
        label_context = str(bucket.get("lane_label_context", ""))
        n_measurements = len(bucket["values"])
        if not n_measurements:
            continue
        label = f"{image_name} {channel_name} {label_context} (n={n_measurements})" if label_context else f"{image_name} {channel_name} (n={n_measurements})"
        row = {
            key: value
            for key, value in bucket.items()
            if key != "values"
        }
        row.update(
            {
                "image_uid": image_uid,
                "image_name": image_name,
                "sample_key": _sample_key(image_uid, bucket, channel_index),
                "sample_group_key": _sample_group_key(image_uid, bucket, channel_index),
                "lane_label": label,
                "group": label,
                "values": list(bucket["values"]),
                "unit": str(bucket.get("unit", "px^2")),
            }
        )
        rows.append(row)
    return rows


def _image_records(panel: Any) -> dict[str, tuple[int, dict[str, Any]]]:
    images = panel.images() if hasattr(panel, "images") else panel.data.get("fluorescence_images", [])
    return {str(image.get("uid", "")): (index, dict(image)) for index, image in enumerate(images)}


def _sample_rows_from_buckets(
    buckets: dict[tuple[Any, ...], dict[str, Any]],
    images: dict[str, tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bucket_key, bucket in sorted(
        buckets.items(),
        key=lambda item: (images.get(str(item[0][0]), (999999, {}))[0], int(item[0][1]), tuple(str(part) for part in item[0][2:])),
    ):
        image_uid = str(bucket_key[0])
        channel_index = int(bucket_key[1])
        values = [float(value) for value in bucket.get("values", [])]
        if not values:
            continue
        image_index, image = images.get(image_uid, (len(images), {}))
        channel_name = str(bucket.get("channel_name", f"Ch{channel_index + 1}"))
        image_name = str(image.get("source_name") or f"Image {image_index + 1}")
        n_measurements = len(values)
        label_context = str(bucket.get("lane_label_context", ""))
        label = f"{image_name} {channel_name} {label_context} (n={n_measurements})" if label_context else f"{image_name} {channel_name} (n={n_measurements})"
        row = {
            key: value
            for key, value in bucket.items()
            if key not in {"values"}
        }
        row.update(
            {
                "image_uid": image_uid,
                "image_name": image_name,
                "sample_key": _sample_key(image_uid, bucket, channel_index),
                "sample_group_key": _sample_group_key(image_uid, bucket, channel_index),
                "lane_label": label,
                "group": label,
                "value": float(np.mean(values)),
                "n_measurements": n_measurements,
                "aggregation": "mean_per_image",
            }
        )
        rows.append(row)
    return rows


def _sample_key(image_uid: str, bucket: dict[str, Any], channel_index: int) -> str:
    key = f"{image_uid}:{bucket.get('source', '')}:{bucket.get('metric', '')}:ch{channel_index}"
    context = bucket.get("sample_key_context")
    if context:
        key = f"{key}:{context}"
    return key


def _sample_group_key(image_uid: str, bucket: dict[str, Any], channel_index: int) -> str:
    key = f"{image_uid}:{bucket.get('source', '')}:ch{channel_index}"
    context = bucket.get("sample_key_context")
    if context:
        key = f"{key}:{context}"
    return key


def _short_context_label(context_hash: str) -> str:
    if not context_hash:
        return ""
    if context_hash == "legacy_unknown_context":
        return "[legacy_unknown_context]"
    if context_hash.startswith("sha256:"):
        return f"[ctx {context_hash.removeprefix('sha256:')[:12]}]"
    return f"[ctx {context_hash[:12]}]"
