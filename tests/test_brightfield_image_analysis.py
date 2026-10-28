from __future__ import annotations

import numpy as np
import pytest

from core.image_analysis import (
    analyze_brightfield_image,
    filter_labeled_objects,
    get_task_profile,
    rgb_to_grayscale_channel,
    segment_grayscale,
    segment_hsv,
    segment_lab,
)
from core.models.brightfield_panel import normalize_brightfield_stain_type
from core.image_analysis.segmentation_engine import (
    clean_binary_mask_cv2,
    cleanup_segmentation_mask,
    connected_components_with_stats,
    find_contours_summary,
    summarize_connected_components,
)


def _synthetic_rgb() -> np.ndarray:
    rgb = np.full((6, 6, 3), 245, dtype=np.uint8)
    rgb[0:3, 0:3] = [210, 40, 40]
    rgb[3:6, 3:6] = [40, 80, 210]
    return rgb


def _synthetic_he_packed_rings() -> np.ndarray:
    rgb = np.full((120, 160, 3), 248, dtype=np.uint8)
    for row in range(3):
        for col in range(5):
            x0 = 12 + col * 28
            y0 = 14 + row * 30
            w = 22
            h = 20
            cx = x0 + w / 2
            cy = y0 + h / 2
            for y in range(y0, y0 + h):
                for x in range(x0, x0 + w):
                    outer = ((x - cx) / (w / 2)) ** 2 + ((y - cy) / (h / 2)) ** 2 <= 1.0
                    inner = ((x - cx) / (w / 4.5)) ** 2 + ((y - cy) / (h / 4.5)) ** 2 <= 1.0
                    if outer and not inner:
                        rgb[y, x] = [232, 170, 192] if (x + y) % 4 else [165, 110, 160]
    return rgb


def test_brightfield_hsv_and_lab_segmentation_find_red_region() -> None:
    rgb = _synthetic_rgb()

    hsv_mask = segment_hsv(rgb, 0.0, 0.10, 0.4, 1.0, 0.0, 0.95)
    lab_mask = segment_lab(rgb, 0.0, 100.0, 20.0, 100.0, 10.0, 80.0)

    assert int(hsv_mask.sum()) == 9
    assert int(lab_mask.sum()) == 9
    assert hsv_mask[0, 0]
    assert lab_mask[1, 1]
    assert not hsv_mask[4, 4]
    assert not lab_mask[4, 4]


def test_brightfield_grayscale_segmentation_and_object_filtering_support_dark_objects() -> None:
    rgb = np.full((6, 6, 3), 235, dtype=np.uint8)
    rgb[0:2, 0:2] = [15, 15, 15]
    rgb[4, 4] = [20, 20, 20]

    gray_mask = segment_grayscale(rgb, 0.2, invert=True)
    objects = filter_labeled_objects(gray_mask, min_area_px=2, max_area_px=0)

    assert int(gray_mask.sum()) == 5
    assert int(objects["mask"].sum()) == 4
    assert objects["object_count"] == 1
    assert objects["total_object_area_px"] == 4
    assert objects["mean_object_area_px"] == pytest.approx(4.0)


def test_cleanup_segmentation_mask_fills_holes_and_removes_single_pixel_bridges() -> None:
    mask = np.zeros((7, 9), dtype=bool)
    mask[2:5, 1:4] = True
    mask[2:5, 5:8] = True
    mask[3, 2] = False
    mask[3, 4] = True

    cleaned = cleanup_segmentation_mask(
        mask,
        min_area_px=0,
        fill_holes=True,
        morphology_kernel_px=3,
    )
    summary = summarize_connected_components(cleaned)

    assert cleaned.dtype == np.bool_
    assert int(cleaned.sum()) == 18
    assert summary["object_count"] == 2
    assert summary["total_object_area_px"] == 18
    assert summary["mean_object_area_px"] == pytest.approx(9.0)
    assert summary["object_area_px_values"] == [9, 9]


def test_summarize_connected_components_filters_by_area() -> None:
    mask = np.zeros((6, 6), dtype=bool)
    mask[0:2, 0:2] = True
    mask[3:6, 3:6] = True

    summary = summarize_connected_components(mask, min_area_px=5, max_area_px=0)

    assert int(summary["mask"].sum()) == 9
    assert summary["object_count"] == 1
    assert summary["total_object_area_px"] == 9
    assert summary["mean_object_area_px"] == pytest.approx(9.0)
    assert summary["object_area_px_values"] == [9]


def test_segmentation_engine_exposes_v6_opencv_api_names() -> None:
    mask = np.zeros((7, 7), dtype=bool)
    mask[1:4, 1:4] = True
    mask[2, 2] = False
    mask[5:7, 0:2] = True
    mask[0, 6] = True

    cleaned = clean_binary_mask_cv2(mask, open_kernel=0, close_kernel=0, fill_holes=True)
    components = connected_components_with_stats(cleaned, min_area_px=2)
    contours = find_contours_summary(components["mask"])

    assert int(cleaned.sum()) == 14
    assert components["object_count"] == 2
    assert components["total_object_area_px"] == 13
    assert contours["contour_count"] == 2
    assert contours["total_contour_area_px"] > 0.0


def test_analyze_brightfield_image_reports_area_fraction_object_metrics_and_roi_intensity() -> None:
    rgb = _synthetic_rgb()
    roi_mask = np.zeros((6, 6), dtype=bool)
    roi_mask[0:3, 0:4] = True

    result = analyze_brightfield_image(
        rgb,
        {
            "segmentation_mode": "hsv",
            "h_min": 0.0,
            "h_max": 0.10,
            "s_min": 0.4,
            "s_max": 1.0,
            "v_min": 0.0,
            "v_max": 0.95,
            "cleanup_min_area_px": 0,
            "fill_holes": True,
            "min_object_area_px": 2,
            "max_object_area_px": 0,
        },
        roi_mask=roi_mask,
    )

    assert result["mask"].shape == (6, 6)
    assert result["results"]["segmentation_mode"] == "hsv"
    assert result["results"]["stained_area_px"] == 9
    assert result["results"]["roi_area_px"] == 12
    assert result["results"]["area_fraction_percent"] == pytest.approx(75.0)
    assert result["results"]["object_count"] == 1
    assert result["results"]["total_object_area_px"] == 9
    assert result["results"]["mean_object_area_px"] == pytest.approx(9.0)
    assert result["results"]["roi_mean_intensity"] >= 0.0
    assert result["results"]["roi_integrated_intensity"] > 0.0


def test_analyze_brightfield_image_step4_cleanup_splits_bridged_high_contrast_objects() -> None:
    rgb = np.full((8, 8, 3), 245, dtype=np.uint8)
    rgb[1:4, 1:4] = [210, 40, 40]
    rgb[1:4, 5:8] = [210, 40, 40]
    rgb[2, 4] = [210, 40, 40]

    result = analyze_brightfield_image(
        rgb,
        {
            "segmentation_mode": "hsv",
            "h_min": 0.0,
            "h_max": 0.10,
            "s_min": 0.4,
            "s_max": 1.0,
            "v_min": 0.0,
            "v_max": 0.95,
            "cleanup_min_area_px": 0,
            "fill_holes": True,
            "morphology_kernel_px": 3,
            "min_object_area_px": 2,
            "max_object_area_px": 0,
        },
    )

    assert result["results"]["stained_area_px"] == 18
    assert result["results"]["area_fraction_percent"] == pytest.approx(28.125)
    assert result["results"]["object_count"] == 2
    assert result["results"]["total_object_area_px"] == 18
    assert result["results"]["mean_object_area_px"] == pytest.approx(9.0)


def test_analyze_brightfield_image_he_tissue_mask_preserves_white_gaps_and_multi_object_count() -> None:
    from core.image_analysis.auto_params import estimate_he_auto_parameters

    rgb = _synthetic_he_packed_rings()
    auto = estimate_he_auto_parameters(rgb)

    result = analyze_brightfield_image(
        rgb,
        {
            **auto.recommended_thresholds,
            "cleanup_min_area_px": int(auto.recommended_min_area or 0),
            "min_object_area_px": int(auto.recommended_min_area or 0),
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": auto.recommended_tissue_mask_settings.get("value_threshold"),
            "tissue_mask_saturation_threshold": auto.recommended_tissue_mask_settings.get("saturation_threshold"),
            "tissue_mask_min_area_px": auto.recommended_tissue_mask_settings.get("min_area_px"),
        },
    )

    assert result["analysis_metadata"]["tissue_mask_applied"] is True
    assert result["results"]["area_fraction_percent"] < 95.0
    assert result["results"]["object_count"] > 1


def test_grayscale_channel_normalizes_to_unit_range() -> None:
    gray = rgb_to_grayscale_channel(_synthetic_rgb())

    assert gray.shape == (6, 6)
    assert float(gray.min()) >= 0.0
    assert float(gray.max()) <= 1.0


def test_brightfield_non_he_profiles_are_registered() -> None:
    assert get_task_profile("brightfield_special_stain_area").default_settings["profile_metadata"]["threshold_source"] == "profile_preset"
    assert get_task_profile("brightfield_crystal_violet_area").task_id == "brightfield_crystal_violet_area"
    assert get_task_profile("brightfield_oil_red_o_area").default_settings["profile_metadata"]["validation_status"] == "synthetic_test_only"


def test_brightfield_stain_type_aliases_recognize_code_values() -> None:
    assert normalize_brightfield_stain_type("special_stain") == "Special stain"
    assert normalize_brightfield_stain_type("crystal_violet") == "Crystal violet"
    assert normalize_brightfield_stain_type("oil_red_o") == "Oil Red O"

    assert normalize_brightfield_stain_type("he") == "H&E"
    assert normalize_brightfield_stain_type("H&E") == "H&E"
    assert normalize_brightfield_stain_type("h&e") == "H&E"

    assert normalize_brightfield_stain_type("unknown_stain") == "Other high-contrast stain"
