from __future__ import annotations

from copy import deepcopy
from math import hypot, pi, sqrt

import cv2
import numpy as np
import pytest

from core.image_analysis.cell_calibration import calibrate_cell_analysis


def _rect_contour(width: float, height: float) -> list[list[float]]:
    return [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]


def _rotated_rect_contour(width: float, height: float, angle_deg: float) -> list[list[float]]:
    points = cv2.boxPoints(((0.0, 0.0), (width, height), angle_deg))
    return [[float(x), float(y)] for x, y in points]


def _farthest_pair(points: list[tuple[float, float]] | np.ndarray) -> float:
    pts = [tuple(map(float, point)) for point in np.asarray(points, dtype=np.float64).reshape(-1, 2)]
    best = 0.0
    for index, (x1, y1) in enumerate(pts):
        for x2, y2 in pts[index + 1 :]:
            best = max(best, hypot(x2 - x1, y2 - y1))
    return best


def _row(**overrides: object) -> dict[str, object]:
    width, height = 20.0, 10.0
    area = width * height
    row: dict[str, object] = {
        "id": 1,
        "center_x_px": 10.0,
        "center_y_px": 5.0,
        "contour_px": _rect_contour(width, height),
        "feret_start_px": [0, 0],
        "feret_end_px": [20, 10],
        "area_px": area,
        "circularity": 0.8,
        "aspect_ratio": 0.5,
        "solidity": 1.0,
        "mean_intensity": 200.0,
        "equivalent_diameter_px": float(sqrt(4.0 * area / pi)),
        "equivalent_diameter_um": 99.0,
        "max_feret_px": float(hypot(width, height)),
        "max_feret_um": 99.0,
        "min_feret_px": height,
        "min_feret_um": 99.0,
        "shape_class": "round",
        "detection_source": "bright_residual",
        "border_truncated": False,
        "include_in_statistics": True,
    }
    row.update(overrides)
    return row


def _analysis(rows: list[dict[str, object]], **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "analysis_version": "bright_round_cells.v1",
        "um_per_px": 0.5,
        "settings": {"backend": "local", "min_diameter_um": 4.0},
        "results": rows,
        "summary": {
            "count": len(rows),
            "round_count": sum(row["shape_class"] == "round" for row in rows),
            "elongated_count": sum(row["shape_class"] == "elongated" for row in rows),
            "border_truncated_count": sum(bool(row.get("border_truncated")) for row in rows),
            "calibration_um_per_px": 0.5,
            "mean_equivalent_diameter_um": 99.0,
            "median_equivalent_diameter_um": 99.0,
            "min_equivalent_diameter_um": 99.0,
            "max_equivalent_diameter_um": 99.0,
            "unrelated_field": "keep-me",
        },
        "excluded_regions": [{"x": 1, "y": 2, "w": 3, "h": 4}],
        "image_size_px": [220, 160],
    }
    payload.update(overrides)
    return payload


def _manual_xy(x: float, y: float) -> dict[str, object]:
    return {
        "enabled": True,
        "length_um": 50.0,
        "um_per_px": x,
        "um_per_px_x": x,
        "um_per_px_y": y,
        "calibration_source": "manual",
        "assumed": False,
        "calibrated": True,
    }


def test_anisotropic_rectangle_uses_xy_physical_metrics() -> None:
    analysis = _analysis([_row()])
    scale = _manual_xy(2.0, 0.5)
    calibrated = calibrate_cell_analysis(analysis, scale)
    row = calibrated["results"][0]
    expected_eq = sqrt(4.0 * 200.0 * 2.0 * 0.5 / pi)
    assert row["id"] == 1
    assert row["contour_px"] == _rect_contour(20.0, 10.0)
    assert row["feret_start_px"] == [0, 0]
    assert row["feret_end_px"] == [20, 10]
    assert row["area_px"] == 200.0
    assert row["equivalent_diameter_px"] == pytest.approx(sqrt(4.0 * 200.0 / pi))
    assert row["max_feret_px"] == pytest.approx(hypot(20.0, 10.0))
    assert row["min_feret_px"] == pytest.approx(10.0)
    assert row["equivalent_diameter_um"] == pytest.approx(expected_eq)
    assert row["max_feret_um"] == pytest.approx(hypot(40.0, 5.0))
    assert row["min_feret_um"] == pytest.approx(5.0)
    assert calibrated["um_per_px"] == pytest.approx(2.0)
    assert calibrated["um_per_px_x"] == pytest.approx(2.0)
    assert calibrated["um_per_px_y"] == pytest.approx(0.5)
    assert calibrated["summary"]["calibration_um_per_px"] == pytest.approx(2.0)
    assert calibrated["summary"]["calibration_um_per_px_x"] == pytest.approx(2.0)
    assert calibrated["summary"]["calibration_um_per_px_y"] == pytest.approx(0.5)
    assert calibrated["summary"]["mean_equivalent_diameter_um"] == pytest.approx(expected_eq)
    assert calibrated["summary"]["unrelated_field"] == "keep-me"
    assert calibrated["summary"]["count"] == 1


def test_rotated_rectangle_measures_transformed_contour() -> None:
    contour = _rotated_rect_contour(20.0, 10.0, 30.0)
    row = _row(contour_px=contour, feret_start_px=[1, 2], feret_end_px=[3, 4], shape_class="elongated")
    analysis = _analysis([row])
    iso = calibrate_cell_analysis(analysis, _manual_xy(0.5, 0.5))
    iso_row = iso["results"][0]
    assert iso_row["min_feret_um"] == pytest.approx(5.0)
    assert iso_row["max_feret_um"] == pytest.approx(0.5 * hypot(20.0, 10.0))
    assert iso_row["feret_start_px"] == [1, 2]
    assert iso_row["feret_end_px"] == [3, 4]
    assert iso_row["contour_px"] == contour
    assert iso_row["shape_class"] == "elongated"

    anisotropic = calibrate_cell_analysis(analysis, _manual_xy(0.5, 2.0))
    physical = [(float(x) * 0.5, float(y) * 2.0) for x, y in contour]
    (_center), sides, _angle = cv2.minAreaRect(np.asarray(physical, dtype=np.float32))
    out = anisotropic["results"][0]
    assert out["max_feret_um"] == pytest.approx(_farthest_pair(physical))
    assert out["min_feret_um"] == pytest.approx(min(float(sides[0]), float(sides[1])))
    assert out["min_feret_um"] != pytest.approx(10.0 * 0.5)
    assert out["min_feret_um"] != pytest.approx(10.0 * 2.0)
    assert out["contour_px"] == contour
    assert out["id"] == 1


def test_isotropic_scalar_legacy_matches_historical_um() -> None:
    um = 0.5
    row = _row()
    analysis = _analysis([row], um_per_px=um)
    calibrated = calibrate_cell_analysis(
        analysis,
        {"um_per_px": um, "calibration_source": "manual", "assumed": False, "calibrated": True},
    )
    out = calibrated["results"][0]
    historical_eq = float(row["equivalent_diameter_px"]) * um
    assert out["equivalent_diameter_um"] == pytest.approx(historical_eq)
    assert out["max_feret_um"] == pytest.approx(float(row["max_feret_px"]) * um)
    assert out["min_feret_um"] == pytest.approx(float(row["min_feret_px"]) * um)
    resolved = calibrate_cell_analysis(analysis, _manual_xy(um, um))
    assert resolved["results"][0]["equivalent_diameter_um"] == pytest.approx(historical_eq)
    assert resolved["results"][0]["max_feret_um"] == pytest.approx(float(row["max_feret_px"]) * um)
    assert resolved["results"][0]["min_feret_um"] == pytest.approx(float(row["min_feret_px"]) * um)


def test_excluded_rows_are_omitted_from_diameter_summary() -> None:
    included = _row(id=1, area_px=200.0)
    excluded = _row(
        id=2,
        area_px=50.0,
        equivalent_diameter_px=sqrt(4.0 * 50.0 / pi),
        include_in_statistics=False,
        border_truncated=True,
        shape_class="elongated",
        contour_px=_rect_contour(10.0, 5.0),
    )
    analysis = _analysis(
        [included, excluded],
        summary={
            "count": 2,
            "round_count": 1,
            "elongated_count": 1,
            "border_truncated_count": 1,
            "calibration_um_per_px": 0.5,
            "mean_equivalent_diameter_um": 99.0,
            "median_equivalent_diameter_um": 99.0,
            "min_equivalent_diameter_um": 99.0,
            "max_equivalent_diameter_um": 99.0,
            "unrelated_field": "keep-me",
        },
    )
    calibrated = calibrate_cell_analysis(analysis, _manual_xy(2.0, 0.5))
    expected = sqrt(4.0 * 200.0 * 2.0 * 0.5 / pi)
    excluded_eq = sqrt(4.0 * 50.0 * 2.0 * 0.5 / pi)
    assert calibrated["results"][0]["include_in_statistics"] is True
    assert calibrated["results"][1]["include_in_statistics"] is False
    assert calibrated["results"][1]["id"] == 2
    assert calibrated["results"][1]["equivalent_diameter_um"] == pytest.approx(excluded_eq)
    assert calibrated["summary"]["count"] == 2
    assert calibrated["summary"]["round_count"] == 1
    assert calibrated["summary"]["elongated_count"] == 1
    assert calibrated["summary"]["border_truncated_count"] == 1
    assert calibrated["summary"]["unrelated_field"] == "keep-me"
    assert calibrated["summary"]["mean_equivalent_diameter_um"] == pytest.approx(expected)
    assert calibrated["summary"]["median_equivalent_diameter_um"] == pytest.approx(expected)
    assert calibrated["summary"]["min_equivalent_diameter_um"] == pytest.approx(expected)
    assert calibrated["summary"]["max_equivalent_diameter_um"] == pytest.approx(expected)


def test_source_analysis_and_scale_are_not_mutated() -> None:
    analysis = _analysis([_row()])
    scale = _manual_xy(2.0, 0.5)
    snapshot = deepcopy(analysis)
    scale_snapshot = deepcopy(scale)
    calibrated = calibrate_cell_analysis(analysis, scale)
    assert analysis == snapshot
    assert scale == scale_snapshot
    calibrated["results"][0]["contour_px"][0][0] = -99
    calibrated["results"][0]["id"] = 9
    calibrated["summary"]["count"] = 0
    assert analysis == snapshot
    assert analysis["results"][0]["contour_px"][0][0] == 0.0
    assert analysis["results"][0]["equivalent_diameter_um"] == 99.0


@pytest.mark.parametrize(
    "scale",
    [
        {"um_per_px": 0.27, "um_per_px_x": 0.27, "um_per_px_y": 0.27, "calibration_source": "assumed", "assumed": True},
        {"um_per_px_x": 0.5, "um_per_px_y": 0.0, "um_per_px": 0.5, "calibration_source": "manual", "calibrated": True, "assumed": False},
        {"um_per_px_x": 0.5, "calibration_source": "manual", "calibrated": True, "assumed": False},
        {"um_per_px": 0.5, "um_per_px_x": 0.5, "um_per_px_y": 0.4, "calibrated": False, "assumed": False},
        None,
        {},
    ],
)
def test_incomplete_or_assumed_axes_raise(scale: dict[str, object] | None) -> None:
    with pytest.raises(ValueError):
        calibrate_cell_analysis(_analysis([_row()]), scale)
