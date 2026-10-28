from __future__ import annotations

import numpy as np
import pytest

from core.image_analysis.artifact_engine import detect_artifact_mask
from core.image_analysis.quality_engine import assess_image_quality
from core.image_analysis.reliability import (
    evaluate_analysis_reliability,
    evaluate_brightfield_reliability,
    evaluate_ihc_reliability,
)


def test_assess_image_quality_flags_background_dominant_overexposed_image() -> None:
    rgb = np.full((8, 8, 3), 255, dtype=np.uint8)

    quality = assess_image_quality(rgb)

    assert quality["exposure_status"] == "overexposed"
    assert quality["white_balance_status"] == "balanced"
    assert quality["blur_score"] >= 0.0
    assert quality["tissue_fraction"] == pytest.approx(0.0)
    assert quality["background_fraction"] == pytest.approx(1.0)
    assert quality["stain_signal_score"] == pytest.approx(0.0)
    assert quality["warnings"]


def test_detect_artifact_mask_flags_dark_debris_inside_tissue() -> None:
    rgb = np.full((8, 8, 3), 235, dtype=np.uint8)
    rgb[2:4, 2:4] = 0

    artifact = detect_artifact_mask(rgb, tissue_mask=np.ones((8, 8), dtype=bool))

    assert artifact["artifact_mask"].shape == (8, 8)
    assert artifact["artifact_mask"].dtype == np.bool_
    assert int(artifact["artifact_mask"].sum()) == 4
    assert artifact["artifact_fraction"] == pytest.approx(4.0 / 64.0)
    assert artifact["artifact_types_detected"] == ["dark_debris"]
    assert artifact["warnings"]


def test_detect_artifact_mask_flags_bright_blank_regions_inside_tissue() -> None:
    rgb = np.full((8, 8, 3), [170, 120, 80], dtype=np.uint8)
    rgb[2:4, 2:4] = 255

    artifact = detect_artifact_mask(rgb, tissue_mask=np.ones((8, 8), dtype=bool))

    assert int(artifact["artifact_mask"].sum()) == 4
    assert artifact["artifact_fraction"] == pytest.approx(4.0 / 64.0)
    assert artifact["artifact_types_detected"] == ["overexposed_blank"]
    assert any("blank-like" in warning.lower() for warning in artifact["warnings"])


def test_evaluate_analysis_reliability_supports_pass_warn_and_fail() -> None:
    passing = evaluate_analysis_reliability({})
    warning = evaluate_analysis_reliability({}, qc_messages=["Review the selection."])
    failing = evaluate_analysis_reliability({}, overlay_generated=False)

    assert passing.status == "PASS"
    assert passing.include_in_statistics is True
    assert warning.status == "WARN"
    assert warning.include_in_statistics is True
    assert "Review the selection." in warning.messages
    assert failing.status == "FAIL"
    assert failing.include_in_statistics is False
    assert any("Overlay generation failed" in message for message in failing.messages)


def test_domain_reliability_helpers_preserve_warn_and_fail_semantics() -> None:
    ihc_warn = evaluate_ihc_reliability({"tissue_area_px": 25, "positive_area_percent": 0.0})
    brightfield_warn = evaluate_brightfield_reliability({"roi_area_px": 25, "object_count": 0})
    brightfield_fail = evaluate_brightfield_reliability({"roi_area_px": 25}, overlay_generated=False)

    assert ihc_warn.status == "WARN"
    assert ihc_warn.include_in_statistics is True
    assert brightfield_warn.status == "WARN"
    assert brightfield_warn.include_in_statistics is True
    assert brightfield_fail.status == "FAIL"
    assert brightfield_fail.include_in_statistics is False
