from __future__ import annotations

import numpy as np

from core.image_analysis.he_fiber_area import (
    analyze_he_fiber_area,
    build_auto_he_fiber_calibration,
    render_he_fiber_area_overlay,
)


def _synthetic_he_image() -> np.ndarray:
    rgb = np.full((90, 120, 3), [246, 241, 244], dtype=np.uint8)
    rgb[16:44, 12:42] = [215, 74, 132]
    rgb[20:48, 56:90] = [220, 82, 138]
    rgb[58:72, 8:108] = [232, 184, 205]
    rgb[28:33, 24:29] = [246, 241, 244]
    return rgb


def test_he_fiber_area_auto_calibration_counts_fibers_and_area_list() -> None:
    rgb = _synthetic_he_image()
    calibration = build_auto_he_fiber_calibration(rgb)

    result = analyze_he_fiber_area(rgb, calibration)

    assert result["status"] == "active"
    assert result["requires_recalibration"] is False
    assert result["results"]["he_fiber_count"] >= 2
    assert len(result["results"]["he_fiber_areas_px"]) == result["results"]["he_fiber_count"]
    assert result["results"]["he_fiber_mean_area_px"] > 0.0
    assert result["he_fiber_mask"].shape == rgb.shape[:2]
    assert result["he_fiber_mask"][30, 26]
    assert result["he_fiber_mask"][30, 72]
    assert result["he_fiber_mask"][30, 26] == result["he_fiber_mask"][30, 28]
    assert result["analysis_metadata"]["he_fiber_segmentation_method"] == "original"
    assert result["analysis_metadata"]["segmentation_path"] == "sample_profile"
    assert result["analysis_metadata"]["max_area_px"] == 12000
    assert result["he_fiber_contours"]


def test_he_fiber_area_rejects_mismatched_image_without_false_measurement() -> None:
    reference = _synthetic_he_image()
    calibration = build_auto_he_fiber_calibration(reference)
    mismatched = np.full(reference.shape, [22, 38, 74], dtype=np.uint8)
    mismatched[16:44, 12:42] = [44, 92, 170]

    result = analyze_he_fiber_area(mismatched, calibration)

    assert result["status"] == "calibration_mismatch"
    assert result["requires_recalibration"] is True
    assert result["results"]["he_fiber_count"] == 0
    assert result["results"]["he_fiber_areas_px"] == []
    assert int(result["he_fiber_mask"].sum()) == 0
    assert any("recalibration" in warning.lower() for warning in result["analysis_warnings"])


def test_he_fiber_area_overlay_draws_each_label_boundary() -> None:
    rgb = np.full((12, 16, 3), [30, 30, 30], dtype=np.uint8)
    labels = np.zeros((12, 16), dtype=np.int32)
    labels[2:10, 2:7] = 1
    labels[2:10, 7:13] = 2

    overlay = render_he_fiber_area_overlay(rgb, labels)

    assert overlay[5, 4].tolist() != [30, 30, 30]
    assert overlay[5, 7].tolist() == [0, 244, 255]
    assert overlay[0, 0].tolist() == [30, 30, 30]


def _connected_fiber_image() -> np.ndarray:
    rgb = np.full((160, 320, 3), [246, 241, 244], dtype=np.uint8)
    rgb[30:130, 20:150] = [215, 74, 132]
    rgb[30:130, 170:300] = [220, 82, 138]
    rgb[70:90, 140:180] = [218, 78, 135]
    rgb[40:70, 40:90] = [230, 58, 118]
    rgb[40:70, 230:280] = [230, 58, 118]
    return rgb


def test_he_fiber_area_lower_splitting_does_not_increase_fragments() -> None:
    rgb = _connected_fiber_image()
    calibration = build_auto_he_fiber_calibration(rgb)

    low = analyze_he_fiber_area(
        rgb, calibration, splitting_sensitivity=0, foreground_sensitivity=50, segmentation_method="anti_oversegmentation"
    )
    default = analyze_he_fiber_area(
        rgb, calibration, splitting_sensitivity=25, foreground_sensitivity=50, segmentation_method="anti_oversegmentation"
    )
    high = analyze_he_fiber_area(
        rgb, calibration, splitting_sensitivity=100, foreground_sensitivity=50, segmentation_method="anti_oversegmentation"
    )

    assert low["status"] == "active"
    assert default["status"] == "active"
    assert high["status"] == "active"
    assert low["results"]["he_fiber_count"] <= high["results"]["he_fiber_count"]
    assert default["analysis_metadata"]["he_fiber_splitting_sensitivity"] == 25
    assert high["analysis_metadata"]["he_fiber_splitting_sensitivity"] == 100
    assert np.max(low["he_fiber_labels"]) == low["results"]["he_fiber_count"]
    assert np.max(high["he_fiber_labels"]) == high["results"]["he_fiber_count"]


def test_he_fiber_area_keeps_large_fibers_and_respects_roi() -> None:
    rgb = np.full((200, 200, 3), [246, 241, 244], dtype=np.uint8)
    rgb[10:180, 10:180] = [215, 74, 132]
    calibration = build_auto_he_fiber_calibration(rgb)
    original = analyze_he_fiber_area(rgb, calibration, segmentation_method="original")
    full = analyze_he_fiber_area(rgb, calibration, segmentation_method="anti_oversegmentation")
    roi = np.zeros(rgb.shape[:2], dtype=bool)
    roi[:, :100] = True
    cropped = analyze_he_fiber_area(rgb, calibration, roi_mask=roi, segmentation_method="anti_oversegmentation")

    assert original["status"] == "active"
    assert original["analysis_metadata"]["max_area_px"] == 12000
    assert original["analysis_metadata"]["he_fiber_segmentation_method"] == "original"
    assert max(original["results"]["he_fiber_areas_px"] or [0]) <= 12000
    assert full["status"] == "active"
    assert max(full["results"]["he_fiber_areas_px"]) > 12000
    assert full["analysis_metadata"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    assert full["analysis_metadata"]["segmentation_path"] == "approved_auto"
    assert cropped["status"] == "active"
    assert int(np.count_nonzero(cropped["he_fiber_mask"][:, 100:])) == 0
    assert int(np.count_nonzero(cropped["he_fiber_mask"][:, :100])) > 0


def test_he_fiber_area_manual_profiles_keep_sample_path() -> None:
    rgb = _synthetic_he_image()
    calibration = build_auto_he_fiber_calibration(rgb)
    calibration["calibration_mode"] = "manual"

    original = analyze_he_fiber_area(rgb, calibration, segmentation_method="original")
    anti = analyze_he_fiber_area(rgb, calibration, segmentation_method="anti_oversegmentation")

    assert original["status"] == "active"
    assert original["analysis_metadata"]["segmentation_path"] == "sample_profile"
    assert original["analysis_metadata"]["he_fiber_segmentation_method"] == "original"
    assert anti["status"] == "active"
    assert anti["analysis_metadata"]["segmentation_path"] == "approved_auto"
    assert anti["analysis_metadata"]["he_fiber_segmentation_method"] == "anti_oversegmentation"


def test_he_fiber_area_anti_mode_uses_approved_algorithm() -> None:
    rgb = _synthetic_he_image()
    calibration = build_auto_he_fiber_calibration(rgb)
    default = analyze_he_fiber_area(rgb, calibration)
    anti = analyze_he_fiber_area(rgb, calibration, segmentation_method="anti_oversegmentation")

    assert default["analysis_metadata"]["he_fiber_segmentation_method"] == "original"
    assert anti["status"] == "active"
    assert anti["analysis_metadata"]["segmentation_path"] == "approved_auto"
    assert anti["analysis_metadata"]["max_area_px"] == 0
    assert anti["he_fiber_mask"][22, 18]
    assert anti["he_fiber_mask"][30, 72]
