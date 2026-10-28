from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from core.image_analysis.bright_round_cells import (
    analyze_bright_round_cells,
    render_bright_round_cell_overlay,
)
from core.models.brightfield_panel import BrightfieldPanel


def _image_with_bright_cell_and_scale_bar(include_elongated: bool = False) -> np.ndarray:
    image = np.full((160, 220, 3), 80, dtype=np.uint8)
    cv2.circle(image, (80, 70), 12, (235, 235, 235), -1)
    if include_elongated:
        cv2.ellipse(image, (145, 70), (22, 5), 0, 0, 360, (235, 235, 235), -1)
    cv2.rectangle(image, (170, 143), (208, 146), (255, 255, 255), -1)
    cv2.putText(image, "100 um", (167, 157), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (255, 255, 255), 1)
    return image


def _image_with_dark_elongated_cell() -> np.ndarray:
    """A grey-field image whose elongated object has a dark body."""

    image = np.full((180, 260, 3), 120, dtype=np.uint8)
    cv2.ellipse(image, (145, 86), (34, 6), -24, 0, 360, (38, 38, 38), -1)
    return image


def test_analyzer_returns_serializable_round_cell_rows_and_excludes_scale_bar() -> None:
    image = _image_with_bright_cell_and_scale_bar()
    analysis = analyze_bright_round_cells(
        image,
        um_per_px=0.5,
        settings={"residual_threshold": 12, "min_mean_intensity": 150},
    )

    assert analysis["summary"]["count"] == 1
    row = analysis["results"][0]
    assert 10.0 < row["equivalent_diameter_um"] < 14.0
    assert isinstance(row["contour_px"], list)
    json.dumps(analysis)


def test_overlay_preserves_rgb_image_shape() -> None:
    image = _image_with_bright_cell_and_scale_bar()
    analysis = analyze_bright_round_cells(image, um_per_px=0.5)

    overlay = render_bright_round_cell_overlay(image, analysis)

    assert overlay.shape == image.shape
    assert overlay.dtype == np.uint8


def test_analyzer_keeps_bright_elongated_objects_and_reports_both_feret_axes() -> None:
    image = _image_with_bright_cell_and_scale_bar(include_elongated=True)
    analysis = analyze_bright_round_cells(image, um_per_px=0.5)

    assert analysis["summary"]["count"] == 2
    assert analysis["summary"]["round_count"] == 1
    assert analysis["summary"]["elongated_count"] == 1
    elongated = next(row for row in analysis["results"] if row["shape_class"] == "elongated")
    assert elongated["max_feret_um"] > elongated["min_feret_um"] > 0.0


def test_analyzer_detects_dark_body_elongated_objects_with_dedicated_path() -> None:
    image = _image_with_dark_elongated_cell()
    analysis = analyze_bright_round_cells(image, um_per_px=0.5)

    elongated = [row for row in analysis["results"] if row["shape_class"] == "elongated"]
    assert elongated
    assert any(row["detection_source"] == "dark_background" for row in elongated)
    row = next(row for row in elongated if row["detection_source"] == "dark_background")
    assert row["max_feret_um"] > row["min_feret_um"] > 0.0


def test_brightfield_panel_persists_calibrated_measurement_and_overlay(tmp_path) -> None:
    image = _image_with_bright_cell_and_scale_bar()
    source = tmp_path / "brightfield.png"
    assert cv2.imwrite(str(source), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    panel = BrightfieldPanel(panel_id="bright-round-cell-test")
    panel.data["brightfield_images"] = [
        {
            "uid": "image-1",
            "source_path": str(source),
            "source_name": source.name,
            "metadata": {"width_px": image.shape[1], "height_px": image.shape[0]},
            "measurement_scale_bar": {
                "um_per_px": 0.5,
                "length_um": 100.0,
                "calibration_source": "manual",
            },
            "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None},
            "measurements": [],
        }
    ]
    panel.data["active_image_uid"] = "image-1"

    measurement = panel.measure_bright_round_cells(settings={"residual_threshold": 12})
    overlay = panel.render_bright_round_cell_overlay(measurement)

    assert measurement["summary"]["count"] == 1
    assert panel.analysis["bright_round_cell_diameter_measurements"]
    assert overlay.shape == image.shape
    json.dumps(panel.to_dict())


def test_brightfield_panel_rejects_assumed_calibration(tmp_path) -> None:
    image = _image_with_bright_cell_and_scale_bar()
    source = tmp_path / "brightfield.png"
    assert cv2.imwrite(str(source), cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    panel = BrightfieldPanel(panel_id="bright-round-cell-test")
    panel.data["brightfield_images"] = [
        {
            "uid": "image-1",
            "source_path": str(source),
            "source_name": source.name,
            "metadata": {"width_px": image.shape[1], "height_px": image.shape[0]},
            "measurement_scale_bar": {},
            "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None},
            "measurements": [],
        }
    ]
    panel.data["active_image_uid"] = "image-1"

    with pytest.raises(ValueError, match="Calibrate"):
        panel.measure_bright_round_cells()
