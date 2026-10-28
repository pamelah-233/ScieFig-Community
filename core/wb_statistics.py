from __future__ import annotations

from typing import Any

from core.statistics import STAT_TESTS, compute_group_statistics, significance_label


def collect_wb_stat_values(densitometry_runs: list[dict[str, Any]], target_strip_label: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for run_index, run in enumerate(densitometry_runs, start=1):
        measurements = list(run.get("measurements", []))
        if target_strip_label:
            measurements = [item for item in measurements if str(item.get("strip_label", "")) == target_strip_label]
        if not measurements:
            continue
        measurement = measurements[0]
        for lane in measurement.get("lanes", []):
            fold = lane.get("fold")
            if not isinstance(fold, (int, float)) or fold != fold:
                continue
            rows.append(
                {
                    "run_index": run_index,
                    "strip_id": measurement.get("strip_id", ""),
                    "strip_label": measurement.get("strip_label", ""),
                    "lane_index": lane.get("lane_index", 0),
                    "lane_label": lane.get("lane_label", ""),
                    "group": lane.get("lane_label", ""),
                    "value": float(fold),
                }
            )
    latest={}
    for row in rows:
        key=(row['strip_id'] or ('unknown',row['run_index']),row['lane_index'])
        latest[key]=row
    return list(latest.values())
