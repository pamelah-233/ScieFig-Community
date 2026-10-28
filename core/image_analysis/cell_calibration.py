from __future__ import annotations

from copy import deepcopy
from math import pi, sqrt
from typing import Any, Mapping

import cv2
import numpy as np

from core.measurement_scale import apply_length_scale


def _require_physical_axes(scale: Mapping[str, Any] | None) -> tuple[float, float]:
    um_per_px_x = float(apply_length_scale(1.0, 0.0, scale))
    um_per_px_y = float(apply_length_scale(0.0, 1.0, scale))
    if um_per_px_x <= 0.0 or um_per_px_y <= 0.0:
        raise ValueError("Cell calibration requires complete physical X and Y axes.")
    return um_per_px_x, um_per_px_y


def _contour_points(row: Mapping[str, Any]) -> np.ndarray:
    contour = row.get("contour_px", row.get("contour"))
    if contour is None:
        return np.zeros((0, 2), dtype=np.float64)
    points = np.asarray(contour, dtype=np.float64).reshape(-1, 2)
    if points.ndim != 2 or points.shape[-1] != 2:
        return np.zeros((0, 2), dtype=np.float64)
    return points


def _convex_hull(points: np.ndarray) -> np.ndarray:
    if len(points) < 3:
        return points
    hull = cv2.convexHull(np.asarray(points, dtype=np.float32))
    if hull is None or len(hull) == 0:
        return points
    return np.asarray(hull, dtype=np.float64).reshape(-1, 2)


def _max_feret_um(physical_hull: np.ndarray) -> float:
    if len(physical_hull) < 2:
        return 0.0
    deltas = physical_hull[:, None, :] - physical_hull[None, :, :]
    squared = np.sum(deltas * deltas, axis=2)
    return float(np.sqrt(float(np.max(squared))))


def _min_feret_um(physical_hull: np.ndarray) -> float:
    if len(physical_hull) < 3:
        return 0.0
    (_center), (width, height), _angle = cv2.minAreaRect(np.asarray(physical_hull, dtype=np.float32))
    return float(min(float(width), float(height)))


def _calibrate_row(row: dict[str, Any], um_per_px_x: float, um_per_px_y: float) -> None:
    try:
        area_px = float(row.get("area_px", 0.0) or 0.0)
    except (TypeError, ValueError):
        area_px = 0.0
    if area_px < 0.0:
        area_px = 0.0
    row["equivalent_diameter_um"] = float(sqrt(4.0 * area_px * um_per_px_x * um_per_px_y / pi))
    row['area_um2'] = area_px * um_per_px_x * um_per_px_y
    points = _contour_points(row)
    hull = _convex_hull(points)
    physical = np.empty_like(hull, dtype=np.float64)
    if len(hull):
        physical[:, 0] = hull[:, 0] * um_per_px_x
        physical[:, 1] = hull[:, 1] * um_per_px_y
    row["max_feret_um"] = _max_feret_um(physical)
    row["min_feret_um"] = _min_feret_um(physical)
    if 'max_width_px' in row:
        from .cell_width import contour_width_metrics
        try:row.update(contour_width_metrics(points,um_per_px_x,um_per_px_y,include_length='centerline_length_px' in row))
        except ValueError:
            row.update(max_width_px=None,max_width_um=None,width_method='requires_contour_review')
            if 'centerline_length_px' in row:
                row.update(centerline_length_px=None,centerline_length_um=None,centerline_points_px=[],length_method='requires_contour_review')


def calibrate_cell_analysis(analysis: Mapping[str, Any], scale: Mapping[str, Any] | None) -> dict[str, Any]:
    um_per_px_x, um_per_px_y = _require_physical_axes(scale)
    out = deepcopy(dict(analysis)) if isinstance(analysis, Mapping) else {}
    rows = out.get("results", [])
    if not isinstance(rows, list):
        rows = []
        out["results"] = rows
    for row in rows:
        if isinstance(row, dict):
            _calibrate_row(row, um_per_px_x, um_per_px_y)
    included = [row for row in rows if isinstance(row, dict) and row.get("include_in_statistics")]
    diameters = [float(row.get("equivalent_diameter_um", 0.0) or 0.0) for row in included]
    summary = out.get("summary")
    if not isinstance(summary, dict):
        summary = {}
        out["summary"] = summary
    summary["mean_equivalent_diameter_um"] = float(np.mean(diameters)) if diameters else 0.0
    summary["median_equivalent_diameter_um"] = float(np.median(diameters)) if diameters else 0.0
    summary["min_equivalent_diameter_um"] = float(min(diameters)) if diameters else 0.0
    summary["max_equivalent_diameter_um"] = float(max(diameters)) if diameters else 0.0
    summary["calibration_um_per_px"] = float(um_per_px_x)
    summary["calibration_um_per_px_x"] = float(um_per_px_x)
    summary["calibration_um_per_px_y"] = float(um_per_px_y)
    out["um_per_px"] = float(um_per_px_x)
    out["um_per_px_x"] = float(um_per_px_x)
    out["um_per_px_y"] = float(um_per_px_y)
    from .cell_width import update_cell_size_summary
    update_cell_size_summary(summary,rows)
    return out
