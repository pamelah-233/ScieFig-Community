from __future__ import annotations

from pathlib import Path
import os

import numpy as np
from PyQt6.QtGui import QColor, QImage
import pytest

from core.constants import SOFTWARE_VERSION
from core.image_analysis import classify_dab_gmm, estimate_dab_auto_parameters
from core.image_analysis.io_engine import ImageImportResult
from core.models import IHCPanel


def _assert_step2_measurement_fields(measurement: dict[str, object], *, threshold_source: str) -> None:
    assert isinstance(measurement["processing_time_ms"], int)
    assert measurement["processing_time_ms"] >= 0
    assert measurement["backend_name"] == "sciefig.image_analysis"
    assert measurement["backend_version"] == SOFTWARE_VERSION
    assert isinstance(measurement["model_name"], str) and measurement["model_name"]
    assert isinstance(measurement["model_hash"], str) and measurement["model_hash"].startswith("sha256:")
    assert isinstance(measurement["include_in_statistics"], bool)
    assert isinstance(measurement["warnings"], list)
    assert isinstance(measurement["user_overrides"], dict)
    assert measurement["analysis_metadata"]["task_id"] == "ihc_dab_positive_area"
    assert measurement["analysis_metadata"]["threshold_source"] == threshold_source


def _write_synthetic_ihc_png(path: Path) -> Path:
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))

    for y in range(3):
        for x in range(3):
            image.setPixelColor(x, y, QColor(135, 75, 28))

    for y in range(3, 6):
        for x in range(3, 6):
            image.setPixelColor(x, y, QColor(80, 120, 190))

    assert image.save(str(path))
    return path


def _write_weak_dab_png(path: Path) -> Path:
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))

    for y in range(2, 5):
        for x in range(2, 5):
            image.setPixelColor(x, y, QColor(205, 175, 150))

    image.setPixelColor(0, 0, QColor(120, 70, 30))

    assert image.save(str(path))
    return path


def _write_example_guided_tissue_png(path: Path) -> Path:
    image = QImage(10, 10, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))

    for y in range(2, 5):
        for x in range(2, 5):
            image.setPixelColor(x, y, QColor(205, 175, 150))

    for y in range(6, 9):
        for x in range(6, 9):
            image.setPixelColor(x, y, QColor(220, 210, 200))

    assert image.save(str(path))
    return path


def _read_rgb(path: Path) -> np.ndarray:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    data = np.frombuffer(ptr, dtype=np.uint8).reshape((image.height(), image.bytesPerLine()))
    return data[:, : image.width() * 3].reshape((image.height(), image.width(), 3)).copy()


def _stub_tissue_background_mask(shape: tuple[int, int], *, coverage_fraction: float = 1.0) -> dict[str, object]:
    mask = np.zeros(shape, dtype=bool)
    keep = max(1, min(mask.size, int(round(mask.size * coverage_fraction))))
    mask.reshape(-1)[:keep] = True
    return {
        "mask": mask,
        "coverage_fraction": float(keep / mask.size) if mask.size else 0.0,
        "settings": {
            "enabled": True,
            "mode": "value_and_saturation",
            "value_threshold": 0.70,
            "saturation_threshold": 0.05,
            "min_area_px": 4,
        },
    }


def test_import_image_uses_io_engine_metadata(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / "delegated_import.png"
    image_path.write_bytes(b"stub")
    panel = IHCPanel(panel_id="Figure1IHCIOMetadata")
    expected = ImageImportResult(
        analysis_data=np.zeros((3, 6, 8), dtype=np.uint8),
        metadata={
            "width_px": 8,
            "height_px": 6,
            "pixel_size_um": 0.42,
            "pixel_size_unit": "um",
            "objective": "20x",
            "bit_depth": 16,
            "n_channels": 3,
            "source_format": "OME-TIFF",
            "axes": "CYX",
            "work_data_downsample": 1,
            "io_warnings": ["metadata warning"],
        },
        source_hash="sha256:" + "b" * 64,
        channel_names=["Red", "Green", "Blue"],
    )
    calls: list[Path] = []

    def _fake_read_microscopy_image(source: str | Path) -> ImageImportResult:
        calls.append(Path(source))
        return expected

    monkeypatch.setattr("core.models.ihc_panel.read_microscopy_image", _fake_read_microscopy_image)

    imported = panel.import_image(image_path, group="Tumor")

    assert calls == [image_path]
    assert imported["source_hash"] == expected.source_hash
    assert imported["metadata"] == {
        "width_px": 8,
        "height_px": 6,
        "pixel_size_um": 0.42,
        "magnification": "20x",
        "source_format": "OME-TIFF",
        "n_channels": 3,
        "pixel_size_unit": "um",
        "bit_depth": 16,
        "axes": "CYX",
        "work_data_downsample": 1,
        "io_warnings": ["metadata warning"],
    }


def test_measure_dab_positive_area_uses_io_engine_rgb_loader(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "delegated_measure.png")
    panel = IHCPanel(panel_id="Figure1IHCRGBDelegation")
    imported = panel.import_image(image_path, group="Tumor")
    expected_rgb = _read_rgb(image_path)
    calls: list[Path] = []

    def _fake_read_rgb_image(source: str | Path) -> tuple[np.ndarray, dict[str, object]]:
        calls.append(Path(source))
        return expected_rgb.copy(), {"source_format": "PNG"}

    monkeypatch.setattr("core.models.ihc_panel.read_rgb_image", _fake_read_rgb_image)
    monkeypatch.setattr(panel, "_read_raster", lambda source: (_ for _ in ()).throw(AssertionError("analysis should use io_engine")))

    measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={
            "positive_threshold": 0.30,
            "weak_threshold": 0.20,
            "moderate_threshold": 0.50,
            "strong_threshold": 0.80,
            "min_tissue_area_px": 2,
        },
    )

    assert calls == [image_path]
    assert measurement["results"]["positive_area_px"] == 9


def test_measure_dab_positive_area_stores_panel_and_image_records(tmp_path) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_ihc.png")
    panel = IHCPanel(panel_id="Figure1IHC")
    imported = panel.import_image(image_path, group="Tumor")

    measurement = panel.measure_dab_positive_area(
        settings={
            "positive_threshold": 0.30,
            "weak_threshold": 0.20,
            "moderate_threshold": 0.50,
            "strong_threshold": 0.80,
            "min_tissue_area_px": 2,
        }
    )

    assert measurement["image_uid"] == imported["uid"]
    assert measurement["image_name"] == image_path.name
    assert measurement["group"] == "Tumor"
    assert measurement["type"] == "dab_positive_area"
    assert measurement["created_at"]
    assert measurement["measurement_uuid"]
    assert measurement["params"]["min_tissue_area_px"] == 2
    assert measurement["results"]["positive_area_px"] == 9
    assert measurement["results"]["total_area_px"] == 36
    assert measurement["results"]["positive_area_percent"] == pytest.approx(25.0)
    assert measurement["analysis_metadata"]["dab_channel_source"] == "dab_od"
    assert measurement["results"]["mean_dab_intensity"] > 1.0
    assert measurement["results"]["h_score"] == pytest.approx(75.0)
    assert measurement["results"]["strong_percent"] == pytest.approx(25.0)
    _assert_step2_measurement_fields(measurement, threshold_source="manual")
    assert measurement["analysis_metadata"]["stain_separation"]["method"] == "default_hdab_fallback"
    assert measurement["analysis_metadata"]["gmm_classification"]["classification_method"] == "fixed_bins_fallback"
    assert measurement["analysis_metadata"]["stain_engine_warnings"]
    assert measurement["reliability_status"] in {"PASS", "WARN", "FAIL"}
    assert isinstance(measurement["reliability"], dict)
    assert isinstance(measurement["overlay_generated"], bool)
    assert isinstance(measurement["overlay_validation"], dict)
    assert isinstance(measurement["qc_messages"], list)
    assert measurement["include_in_statistics"] is True
    assert measurement["warnings"] == panel._dedupe_messages(
        list(measurement["qc_messages"]) + list(measurement["reliability"].get("messages", []))
    )
    assert measurement["user_overrides"] == {}

    assert panel.analysis["ihc_measurements"][-1]["measurement_uuid"] == measurement["measurement_uuid"]
    assert panel.active_image()["measurements"][-1]["measurement_uuid"] == measurement["measurement_uuid"]
    assert panel.data["analysis_settings"]["min_tissue_area_px"] == 2


def test_render_measurement_overlay_and_round_trip_preserve_ihc_measurements(tmp_path) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_overlay.png")
    panel = IHCPanel(panel_id="Figure1IHCOverlay")
    panel.import_image(image_path, group="Tumor")
    measurement = panel.measure_dab_positive_area(settings={"min_tissue_area_px": 2})

    original = _read_rgb(image_path)
    overlay = panel.render_measurement_overlay(measurement_uuid=measurement["measurement_uuid"])

    assert overlay.dtype == np.uint8
    assert overlay.shape == original.shape
    assert overlay[0, 0].tolist() != original[0, 0].tolist()
    assert overlay[5, 5].tolist() == original[5, 5].tolist()

    restored = IHCPanel.from_dict(panel.to_dict())

    assert restored.analysis["ihc_measurements"][0]["measurement_uuid"] == measurement["measurement_uuid"]
    assert restored.analysis["ihc_measurements"][0]["params"]["min_tissue_area_px"] == 2
    assert restored.analysis["ihc_measurements"][0]["model_hash"] == measurement["model_hash"]
    assert restored.analysis["ihc_measurements"][0]["analysis_metadata"]["threshold_source"] == "manual"
    assert restored.active_image()["measurements"][0]["measurement_uuid"] == measurement["measurement_uuid"]
    assert restored.data["analysis_settings"]["min_tissue_area_px"] == 2


def test_measure_dab_positive_area_uses_stored_rectangle_roi(tmp_path) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_roi.png")
    panel = IHCPanel(panel_id="Figure1IHCROI")
    imported = panel.import_image(image_path, group="Tumor")

    whole_image = panel.measure_dab_positive_area(
        settings={
            "positive_threshold": 0.30,
            "weak_threshold": 0.20,
            "moderate_threshold": 0.50,
            "strong_threshold": 0.80,
            "min_tissue_area_px": 2,
        }
    )
    panel.set_image_roi(
        imported["uid"],
        "rectangle",
        {"x": 0, "y": 0, "w": 2, "h": 4},
    )

    roi_measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={
            "positive_threshold": 0.30,
            "weak_threshold": 0.20,
            "moderate_threshold": 0.50,
            "strong_threshold": 0.80,
            "min_tissue_area_px": 2,
        },
    )

    assert roi_measurement["image_uid"] == imported["uid"]
    assert roi_measurement["roi"]["mode"] == "selected_roi"
    assert roi_measurement["roi"]["active_item_uuid"]
    assert roi_measurement["roi"]["items"][-1]["type"] == "rectangle"
    assert roi_measurement["roi"]["items"][-1]["coordinates_px"] == {"x": 0, "y": 0, "w": 2, "h": 4}
    assert whole_image["results"]["positive_area_px"] == 9
    assert roi_measurement["results"]["positive_area_px"] == 6
    assert whole_image["results"]["total_area_px"] == 36
    assert roi_measurement["results"]["total_area_px"] == 8
    assert whole_image["results"]["positive_area_percent"] == pytest.approx(25.0)
    assert roi_measurement["results"]["positive_area_percent"] == pytest.approx(75.0)
    assert whole_image["results"]["h_score"] == pytest.approx(75.0)
    assert roi_measurement["results"]["h_score"] == pytest.approx(225.0)


def test_measure_dab_positive_area_preserves_multiple_measurements_per_image(tmp_path) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "synthetic_multi_roi.png")
    panel = IHCPanel(panel_id="Figure1IHCMultiROI")
    imported = panel.import_image(image_path, group="Tumor")

    panel.set_image_roi(imported["uid"], "rectangle", {"x": 0, "y": 0, "w": 2, "h": 4})
    first = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 2})

    panel.set_image_roi(imported["uid"], "rectangle", {"x": 0, "y": 0, "w": 6, "h": 6})
    second = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 2})

    image_measurements = panel.active_image()["measurements"]
    assert len(image_measurements) == 2
    assert len(panel.analysis["ihc_measurements"]) == 2
    assert first["measurement_uuid"] != second["measurement_uuid"]
    assert first["roi"]["items"][-1]["coordinates_px"] == {"x": 0, "y": 0, "w": 2, "h": 4}
    assert second["roi"]["items"][-1]["coordinates_px"] == {"x": 0, "y": 0, "w": 6, "h": 6}
    assert first["results"]["positive_area_px"] == 6
    assert second["results"]["positive_area_px"] == 9
    assert first["results"]["total_area_px"] == 8
    assert second["results"]["total_area_px"] == 36


def test_measure_dab_positive_area_real_tiff_smoke_uses_roi_when_available() -> None:
    fixture_path = os.environ.get("SCIEFIG_TEST_IHC_TIFF")
    if not fixture_path:
        pytest.skip("Set SCIEFIG_TEST_IHC_TIFF to opt in to a private local fixture.")
    image_path = Path(fixture_path)
    if not image_path.exists():
        pytest.skip("Real TIFF smoke image is not available on disk.")

    panel = IHCPanel(panel_id="Figure1IHCRealSmoke")
    try:
        imported = panel.import_image(image_path, group="Smoke")
    except Exception as exc:
        pytest.skip(f"Real TIFF smoke image is present but unreadable in this test environment: {exc}")

    qimage = panel.source_qimage(imported["uid"])
    roi_width = max(32, qimage.width() // 8)
    roi_height = max(32, qimage.height() // 8)
    roi_x = max(0, qimage.width() // 4)
    roi_y = max(0, qimage.height() // 4)
    panel.set_image_roi(imported["uid"], "rectangle", {"x": roi_x, "y": roi_y, "w": roi_width, "h": roi_height})

    measurement = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 2})

    assert measurement["roi"]["mode"] == "selected_roi"
    assert measurement["roi"]["items"][-1]["coordinates_px"] == {
        "x": roi_x,
        "y": roi_y,
        "w": roi_width,
        "h": roi_height,
    }
    assert measurement["results"]["positive_area_px"] <= measurement["results"]["total_area_px"]
    assert measurement["results"]["total_area_px"] <= roi_width * roi_height


def test_auto_analyze_dab_detects_weak_signal_when_manual_default_threshold_is_zero(tmp_path) -> None:
    image_path = _write_weak_dab_png(tmp_path / "weak_dab.png")
    panel = IHCPanel(panel_id="Figure1IHCWeakAuto")
    imported = panel.import_image(image_path, group="Weak")

    manual = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={
            "positive_threshold": 0.30,
            "weak_threshold": 0.20,
            "moderate_threshold": 0.50,
            "strong_threshold": 0.80,
            "min_tissue_area_px": 4,
        },
    )
    auto = panel.auto_analyze_dab(image_uid=imported["uid"])

    assert manual["analysis_metadata"]["dab_channel_source"] == "dab_od"
    assert manual["results"]["positive_area_px"] == 9
    assert auto["results"]["positive_area_px"] == 9
    assert auto["guided_mode"] == "guided"
    assert auto["task_profile"]["task_id"] == "ihc_dab_positive_area"
    assert auto["threshold_source"] == "batch_initialized"
    assert auto["params"]["positive_threshold"] < 0.30
    assert auto["auto_parameter_result"]["recommended_thresholds"]["estimated_positive_threshold"] < 0.30
    assert auto["analysis_metadata"]["dab_channel_source"] == "dab_od"
    assert "stain_separation" in auto["analysis_metadata"]
    assert "gmm_classification" in auto["analysis_metadata"]
    assert auto["analysis_metadata"]["gmm_classification"]["classification_method"] == "fixed_bins_fallback"
    assert auto["results"]["total_area_px"] == 9
    assert any("weak" in warning.lower() for warning in auto["qc_messages"])
    assert auto["reliability"]["status"] in {"PASS", "WARN"}
    assert auto["reliability_status"] == auto["reliability"]["status"]
    assert auto["overlay_generated"] is True
    _assert_step2_measurement_fields(auto, threshold_source="batch_initialized")
    assert auto["include_in_statistics"] == auto["reliability"]["include_in_statistics"]
    assert auto["user_overrides"] == {}


def test_auto_analyze_dab_reuses_single_dab_analysis_pass(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_weak_dab_png(tmp_path / "single_pass_auto.png")
    panel = IHCPanel(panel_id="Figure1IHCSinglePass")
    imported = panel.import_image(image_path)
    call_count = 0
    original_analyze = panel._analyze_dab_threshold

    def _counting_analyze(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(panel, "_analyze_dab_threshold", _counting_analyze)

    measurement = panel.auto_analyze_dab(image_uid=imported["uid"])

    assert call_count == 1
    assert measurement["threshold_source"] == "batch_initialized"
    assert measurement["analysis_metadata"]["threshold_source"] == "batch_initialized"


def test_render_ihc_overlay_reuses_cached_analysis_after_auto_analyze(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_weak_dab_png(tmp_path / "cached_overlay_auto.png")
    panel = IHCPanel(panel_id="Figure1IHCCachedOverlay")
    imported = panel.import_image(image_path)
    measurement = panel.auto_analyze_dab(image_uid=imported["uid"])
    call_count = 0
    original_analyze = panel._analyze_dab_threshold

    def _counting_analyze(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(panel, "_analyze_dab_threshold", _counting_analyze)

    overlay = panel.render_measurement_overlay(measurement_uuid=measurement["measurement_uuid"])

    assert call_count == 0
    assert overlay.shape[:2] == (10, 10)


def test_auto_analyze_dab_reuses_locked_batch_threshold_for_later_images(tmp_path) -> None:
    first_path = _write_weak_dab_png(tmp_path / "batch_first.png")
    second_path = _write_weak_dab_png(tmp_path / "batch_second.png")

    second_image = QImage(str(second_path))
    second_image.setPixelColor(6, 6, QColor(198, 168, 146))
    assert second_image.save(str(second_path))

    panel = IHCPanel(panel_id="Figure1IHCBatch")
    first = panel.import_image(first_path, group="Batch")
    second = panel.import_image(second_path, group="Batch")

    first_measurement = panel.auto_analyze_dab(image_uid=first["uid"])
    batch_state = panel.data["guided_state"]["batch_threshold_state"]
    second_measurement = panel.auto_analyze_dab(image_uid=second["uid"])

    assert batch_state["locked"] is True
    assert batch_state["batch_threshold"] == pytest.approx(first_measurement["params"]["positive_threshold"])
    assert batch_state["batch_threshold_source_image"] == first["uid"]
    assert second_measurement["threshold_source"] == "batch_locked"
    assert second_measurement["params"]["positive_threshold"] == pytest.approx(first_measurement["params"]["positive_threshold"])
    assert panel.data["guided_state"]["batch_threshold_state"]["batch_threshold"] == pytest.approx(
        first_measurement["params"]["positive_threshold"]
    )


def test_estimate_dab_auto_parameters_clamps_recommended_thresholds_to_3_od(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb = np.full((10, 10, 3), 220, dtype=np.uint8)
    high_od = np.full((10, 10), 10.0, dtype=np.float64)

    monkeypatch.setattr(
        "core.image_analysis.auto_params.estimate_tissue_background_mask",
        lambda image: _stub_tissue_background_mask(image.shape[:2]),
    )
    monkeypatch.setattr(
        "core.image_analysis.auto_params.deconvolve_h_dab",
        lambda image, tissue_mask=None: {
            "dab_od": high_od.copy(),
            "warnings": [],
            "stain_separation": {"method": "mock_high_od", "warnings": []},
        },
    )

    result = estimate_dab_auto_parameters(rgb, sensitivity="low")

    assert result.recommended_thresholds["estimated_positive_threshold"] == pytest.approx(3.0)
    assert result.recommended_thresholds["effective_positive_threshold"] == pytest.approx(3.0)
    assert result.recommended_thresholds["effective_positive_threshold"] <= 3.0


def test_estimate_dab_auto_parameters_weak_signal_warning_uses_strict_p95_below_point_40_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rgb = np.full((10, 10, 3), 220, dtype=np.uint8)
    monkeypatch.setattr(
        "core.image_analysis.auto_params.estimate_tissue_background_mask",
        lambda image: _stub_tissue_background_mask(image.shape[:2]),
    )

    # This locks the current public weak-signal gate exactly as implemented:
    # p95 < 0.40 warns, while p95 == 0.40 does not.
    monkeypatch.setattr(
        "core.image_analysis.auto_params.deconvolve_h_dab",
        lambda image, tissue_mask=None: {
            "dab_od": np.full((10, 10), 0.39, dtype=np.float64),
            "warnings": [],
            "stain_separation": {"method": "mock_weak_signal_below", "warnings": []},
        },
    )
    below_boundary = estimate_dab_auto_parameters(rgb)

    monkeypatch.setattr(
        "core.image_analysis.auto_params.deconvolve_h_dab",
        lambda image, tissue_mask=None: {
            "dab_od": np.full((10, 10), 0.40, dtype=np.float64),
            "warnings": [],
            "stain_separation": {"method": "mock_weak_signal_edge", "warnings": []},
        },
    )
    at_boundary = estimate_dab_auto_parameters(rgb)

    assert any("DAB signal appears weak." in warning for warning in below_boundary.qc_warnings)
    assert not any("DAB signal appears weak." in warning for warning in at_boundary.qc_warnings)


def test_auto_analyze_dab_overlay_failure_excludes_measurement_from_statistics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_weak_dab_png(tmp_path / "overlay_fail.png")
    panel = IHCPanel(panel_id="Figure1IHCFailGate")
    imported = panel.import_image(image_path, group="Weak")

    def _raise_overlay_error(*args, **kwargs):
        raise RuntimeError("overlay failed")

    monkeypatch.setattr("core.models.ihc_panel.render_mask_overlay", _raise_overlay_error)

    measurement = panel.auto_analyze_dab(image_uid=imported["uid"])

    assert measurement["overlay_generated"] is False
    assert measurement["reliability_status"] == "FAIL"
    assert measurement["reliability"]["include_in_statistics"] is False
    assert measurement["include_in_statistics"] is False


def test_manual_measurement_reliability_pass_includes_statistics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "manual_pass.png")
    panel = IHCPanel(panel_id="Figure1IHCManualPass")
    imported = panel.import_image(image_path, group="Manual")
    rgb = _read_rgb(image_path)
    analysis = {
        "mask": np.zeros((6, 6), dtype=bool),
        "analysis_mask": np.ones((6, 6), dtype=bool),
        "artifact_mask": np.zeros((6, 6), dtype=bool),
        "quality_summary": {"warnings": []},
        "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        "results": {
            "positive_area_px": 9,
            "total_area_px": 36,
            "positive_area_fraction": 0.25,
            "positive_area_percent": 25.0,
            "tissue_area_px": 36,
            "mean_dab_intensity": 0.6,
            "integrated_dab_density": 5.4,
            "negative_percent": 75.0,
            "weak_percent": 25.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 25.0,
            "h_score": 25.0,
        },
        "analysis_metadata": {
            "dab_channel_source": "dab_od",
            "stain_separation": {"method": "mock"},
            "gmm_classification": {
                "classification_method": "gmm_bic",
                "h_score_method": "gmm_2_component_positive_conservative",
                "class_mapping": {"0": "negative", "1": "positive"},
            },
            "stain_engine_warnings": [],
            "quality_summary": {"warnings": []},
            "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        },
        "analysis_warnings": [],
    }

    monkeypatch.setattr(panel, "_analyze_dab_threshold", lambda *args, **kwargs: analysis)
    monkeypatch.setattr("core.models.ihc_panel.rgb_to_dab_channel", lambda image: np.zeros((6, 6), dtype=np.float64))
    monkeypatch.setattr("core.models.ihc_panel.assess_white_balance_color_cast", lambda image: {"warning": None})
    monkeypatch.setattr("core.models.ihc_panel.assess_dab_stain_compatibility", lambda channel, tissue_mask=None: {"warning": None})
    monkeypatch.setattr("core.models.ihc_panel.render_mask_overlay", lambda image, mask, alpha=0.35: image.copy())
    monkeypatch.setattr(
        "core.models.ihc_panel.validate_overlay_result",
        lambda image, mask, overlay, error=None: {"status": "PASS", "messages": []},
    )

    measurement = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 0})

    assert measurement["reliability_status"] == "PASS"
    assert measurement["reliability"]["include_in_statistics"] is True
    assert measurement["include_in_statistics"] is True
    assert measurement["overlay_generated"] is True
    assert measurement["overlay_validation"]["status"] == "PASS"
    assert measurement["qc_messages"] == []
    assert measurement["warnings"] == []


def test_manual_measurement_reliability_warn_includes_statistics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "manual_warn.png")
    panel = IHCPanel(panel_id="Figure1IHCManualWarn")
    imported = panel.import_image(image_path, group="Manual")
    rgb = _read_rgb(image_path)
    analysis = {
        "mask": np.zeros((6, 6), dtype=bool),
        "analysis_mask": np.ones((6, 6), dtype=bool),
        "artifact_mask": np.zeros((6, 6), dtype=bool),
        "quality_summary": {"warnings": []},
        "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        "results": {
            "positive_area_px": 0,
            "total_area_px": 36,
            "positive_area_fraction": 0.0,
            "positive_area_percent": 0.0,
            "tissue_area_px": 36,
            "mean_dab_intensity": 0.0,
            "integrated_dab_density": 0.0,
            "negative_percent": 100.0,
            "weak_percent": 0.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 0.0,
            "h_score": 0.0,
        },
        "analysis_metadata": {
            "dab_channel_source": "dab_od",
            "stain_separation": {"method": "mock"},
            "gmm_classification": {
                "classification_method": "fixed_bins_fallback",
                "h_score_method": "fixed_bins_fallback",
                "class_mapping": {},
            },
            "stain_engine_warnings": [],
            "quality_summary": {"warnings": []},
            "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        },
        "analysis_warnings": [],
    }

    monkeypatch.setattr(panel, "_analyze_dab_threshold", lambda *args, **kwargs: analysis)
    monkeypatch.setattr("core.models.ihc_panel.rgb_to_dab_channel", lambda image: np.zeros((6, 6), dtype=np.float64))
    monkeypatch.setattr("core.models.ihc_panel.assess_white_balance_color_cast", lambda image: {"warning": None})
    monkeypatch.setattr("core.models.ihc_panel.assess_dab_stain_compatibility", lambda channel, tissue_mask=None: {"warning": None})
    monkeypatch.setattr("core.models.ihc_panel.render_mask_overlay", lambda image, mask, alpha=0.35: image.copy())
    monkeypatch.setattr(
        "core.models.ihc_panel.validate_overlay_result",
        lambda image, mask, overlay, error=None: {"status": "PASS", "messages": []},
    )

    measurement = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 0})

    assert measurement["reliability_status"] == "WARN"
    assert measurement["reliability"]["include_in_statistics"] is True
    assert measurement["include_in_statistics"] is True
    assert measurement["overlay_generated"] is True
    assert measurement["overlay_validation"]["status"] == "PASS"
    assert measurement["qc_messages"] == []
    assert any("Positive mask is empty." in warning for warning in measurement["warnings"])


def test_manual_measurement_reliability_fail_excludes_statistics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "manual_fail.png")
    panel = IHCPanel(panel_id="Figure1IHCManualFail")
    imported = panel.import_image(image_path, group="Manual")
    rgb = _read_rgb(image_path)
    analysis = {
        "mask": np.zeros((6, 6), dtype=bool),
        "analysis_mask": np.ones((6, 6), dtype=bool),
        "artifact_mask": np.zeros((6, 6), dtype=bool),
        "quality_summary": {"warnings": []},
        "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        "results": {
            "positive_area_px": 9,
            "total_area_px": 36,
            "positive_area_fraction": 0.25,
            "positive_area_percent": 25.0,
            "tissue_area_px": 36,
            "mean_dab_intensity": 0.6,
            "integrated_dab_density": 5.4,
            "negative_percent": 75.0,
            "weak_percent": 25.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 25.0,
            "h_score": 25.0,
        },
        "analysis_metadata": {
            "dab_channel_source": "dab_od",
            "stain_separation": {"method": "mock"},
            "gmm_classification": {
                "classification_method": "gmm_bic",
                "h_score_method": "gmm_2_component_positive_conservative",
                "class_mapping": {"0": "negative", "1": "positive"},
            },
            "stain_engine_warnings": [],
            "quality_summary": {"warnings": []},
            "artifact_summary": {"artifact_fraction": 0.0, "warnings": [], "artifact_types_detected": []},
        },
        "analysis_warnings": [],
    }

    monkeypatch.setattr(panel, "_analyze_dab_threshold", lambda *args, **kwargs: analysis)
    monkeypatch.setattr("core.models.ihc_panel.rgb_to_dab_channel", lambda image: np.zeros((6, 6), dtype=np.float64))
    monkeypatch.setattr("core.models.ihc_panel.assess_white_balance_color_cast", lambda image: {"warning": None})
    monkeypatch.setattr("core.models.ihc_panel.assess_dab_stain_compatibility", lambda channel, tissue_mask=None: {"warning": None})
    monkeypatch.setattr(
        "core.models.ihc_panel.render_mask_overlay",
        lambda image, mask, alpha=0.35: (_ for _ in ()).throw(RuntimeError("overlay failed")),
    )

    measurement = panel.measure_dab_positive_area(image_uid=imported["uid"], settings={"min_tissue_area_px": 0})

    assert measurement["reliability_status"] == "FAIL"
    assert measurement["reliability"]["include_in_statistics"] is False
    assert measurement["include_in_statistics"] is False
    assert measurement["overlay_generated"] is False
    assert measurement["overlay_validation"]["status"] == "FAIL"
    assert any("Overlay generation failed or has not been confirmed." in warning for warning in measurement["warnings"])


def test_example_guided_dab_recalibration_records_positive_and_negative_samples(tmp_path) -> None:
    image_path = _write_example_guided_tissue_png(tmp_path / "example_guided.png")
    panel = IHCPanel(panel_id="Figure1IHCExampleGuided")
    imported = panel.import_image(image_path, group="Weak")

    auto = panel.auto_analyze_dab(image_uid=imported["uid"])
    recalibrated = panel.recalibrate_dab_from_examples(
        image_uid=imported["uid"],
        examples=[
            {"kind": "positive", "x": 3, "y": 3, "patch_radius": 1},
            {"kind": "negative", "x": 8, "y": 8, "patch_radius": 1},
        ],
    )

    assert recalibrated["threshold_source"] == "example_guided"
    assert recalibrated["guided_mode"] == "assisted"
    assert recalibrated["params"]["positive_threshold"] != pytest.approx(auto["params"]["positive_threshold"])
    assert recalibrated["example_guided_result"]["positive_sample_count"] == 1
    assert recalibrated["example_guided_result"]["negative_sample_count"] == 1
    assert recalibrated["example_guided_result"]["channel_source"] == "dab_od"
    assert recalibrated["example_guided_result"]["examples"][0]["kind"] == "positive"
    assert recalibrated["example_guided_result"]["examples"][1]["kind"] == "negative"
    assert recalibrated["analysis_metadata"]["example_guided_channel_source"] == "dab_od"
    assert recalibrated["reliability"]["status"] in {"PASS", "WARN", "FAIL"}
    _assert_step2_measurement_fields(recalibrated, threshold_source="example_guided")
    assert recalibrated["include_in_statistics"] == recalibrated["reliability"]["include_in_statistics"]
    assert recalibrated["user_overrides"]["example_guided"] is True
    assert recalibrated["user_overrides"]["example_count"] == 2


def test_revert_dab_to_auto_returns_previous_auto_measurement(tmp_path) -> None:
    image_path = _write_example_guided_tissue_png(tmp_path / "revert_auto.png")
    panel = IHCPanel(panel_id="Figure1IHCRevertAuto")
    imported = panel.import_image(image_path, group="Weak")

    auto = panel.auto_analyze_dab(image_uid=imported["uid"])
    panel.recalibrate_dab_from_examples(
        image_uid=imported["uid"],
        examples=[
            {"kind": "positive", "x": 3, "y": 3},
            {"kind": "negative", "x": 8, "y": 8},
        ],
    )
    restored = panel.revert_dab_to_auto(image_uid=imported["uid"])

    assert restored["measurement_uuid"] == auto["measurement_uuid"]
    assert panel.data["guided_state"]["last_active_measurement_uuid"] == auto["measurement_uuid"]
    assert restored["threshold_source"] == "batch_initialized"


def test_dab_measurement_uses_deconvolved_dab_od_as_primary_channel(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "primary_dab_od.png")
    panel = IHCPanel(panel_id="Figure1IHCDABODPrimary")
    imported = panel.import_image(image_path, group="Tumor")
    fake_dab_od = np.zeros((6, 6), dtype=np.float64)
    fake_dab_od[:2, :2] = 0.6

    monkeypatch.setattr("core.models.ihc_panel.rgb_to_dab_channel", lambda rgb: np.zeros((6, 6), dtype=np.float64))
    monkeypatch.setattr(
        "core.models.ihc_panel.deconvolve_h_dab",
        lambda rgb, tissue_mask=None: {
            "dab_od": fake_dab_od.copy(),
            "stain_separation": {"method": "mock_dab_od", "warnings": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "core.models.ihc_panel.classify_dab_gmm",
        lambda dab_od, tissue_mask=None: {
            "classification_method": "fixed_bins_fallback",
            "h_score_method": "fixed_bins_fallback",
            "gmm_n_components": None,
            "gmm_bic": None,
            "gmm_bic_candidates": {},
            "component_means": [],
            "negative_percent": 88.8888888889,
            "weak_percent": 11.1111111111,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 11.1111111111,
            "h_score": 11.1111111111,
            "percentages": {
                "negative": 88.8888888889,
                "weak": 11.1111111111,
                "moderate": 0.0,
                "strong": 0.0,
                "positive": 11.1111111111,
            },
            "warnings": [],
        },
    )

    measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={"positive_threshold": 0.30, "min_tissue_area_px": 0},
    )

    assert measurement["analysis_metadata"]["dab_channel_source"] == "dab_od"
    assert measurement["results"]["positive_area_px"] == 4
    assert measurement["results"]["mean_dab_intensity"] == pytest.approx(0.6)
    assert measurement["results"]["positive_percent"] == pytest.approx(11.1111111111)


def test_gmm_classification_drives_final_hscore_and_class_percentages(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "gmm_drives_hscore.png")
    panel = IHCPanel(panel_id="Figure1IHCGMMFinal")
    imported = panel.import_image(image_path, group="Tumor")

    monkeypatch.setattr(
        "core.models.ihc_panel.deconvolve_h_dab",
        lambda rgb, tissue_mask=None: {
            "dab_od": np.full((6, 6), 0.65, dtype=np.float64),
            "stain_separation": {"method": "mock_gmm_drive", "warnings": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "core.models.ihc_panel.classify_dab_gmm",
        lambda dab_od, tissue_mask=None: {
            "classification_method": "gmm_bic",
            "h_score_method": "gmm_component_ranked_hscore",
            "gmm_n_components": 4,
            "gmm_bic": 12.5,
            "gmm_bic_candidates": {"2": 14.0, "3": 13.0, "4": 12.5},
            "component_means": [0.05, 0.2, 0.45, 0.8],
            "negative_percent": 10.0,
            "weak_percent": 20.0,
            "moderate_percent": 30.0,
            "strong_percent": 40.0,
            "positive_percent": 90.0,
            "h_score": 210.0,
            "percentages": {
                "negative": 10.0,
                "weak": 20.0,
                "moderate": 30.0,
                "strong": 40.0,
                "positive": 90.0,
            },
            "warnings": [],
        },
    )

    measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={"positive_threshold": 0.30, "min_tissue_area_px": 0},
    )

    assert measurement["analysis_metadata"]["gmm_classification"]["classification_method"] == "gmm_bic"
    assert measurement["results"]["weak_percent"] == pytest.approx(20.0)
    assert measurement["results"]["moderate_percent"] == pytest.approx(30.0)
    assert measurement["results"]["strong_percent"] == pytest.approx(40.0)
    assert measurement["results"]["intensity_positive_percent"] == pytest.approx(90.0)
    assert measurement["results"]["h_score"] == pytest.approx(210.0)


def test_manual_measurement_records_two_component_conservative_gmm_methods_and_summary_fields(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = _write_synthetic_ihc_png(tmp_path / "gmm_two_component_traceability.png")
    panel = IHCPanel(panel_id="Figure1IHCGMMTwoComponentTraceability")
    imported = panel.import_image(image_path, group="Traceability")
    fake_dab_od = np.zeros((6, 6), dtype=np.float64)
    fake_dab_od[:3, :3] = 0.60

    monkeypatch.setattr(
        "core.models.ihc_panel.deconvolve_h_dab",
        lambda rgb, tissue_mask=None: {
            "dab_od": fake_dab_od.copy(),
            "stain_separation": {"method": "mock_two_component_traceability", "warnings": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "core.models.ihc_panel.classify_dab_gmm",
        lambda dab_od, tissue_mask=None: {
            "classification_method": "gmm_bic",
            "h_score_method": "gmm_2_component_positive_conservative",
            "gmm_n_components": 2,
            "gmm_bic": 8.5,
            "gmm_bic_candidates": {"2": 8.5, "3": 12.0, "4": 15.0},
            "component_means": [0.04, 0.60],
            "class_mapping": {"0": "negative", "1": "positive"},
            "negative_percent": 75.0,
            "weak_percent": 25.0,
            "moderate_percent": 0.0,
            "strong_percent": 0.0,
            "positive_percent": 25.0,
            "h_score": 25.0,
            "percentages": {
                "negative": 75.0,
                "weak": 25.0,
                "moderate": 0.0,
                "strong": 0.0,
                "positive": 25.0,
            },
            "warnings": [],
        },
    )

    measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={"positive_threshold": 0.30, "min_tissue_area_px": 0},
    )

    gmm_metadata = measurement["analysis_metadata"]["gmm_classification"]

    assert measurement["analysis_metadata"]["threshold_source"] == "manual"
    assert measurement["params"]["positive_threshold"] == pytest.approx(0.30)
    assert measurement["results"]["positive_area_percent"] == pytest.approx(25.0)
    assert measurement["results"]["mean_dab_intensity"] == pytest.approx(0.60)
    assert measurement["results"]["positive_percent"] == pytest.approx(25.0)
    assert measurement["results"]["intensity_positive_percent"] == pytest.approx(25.0)
    assert measurement["results"]["weak_percent"] == pytest.approx(measurement["results"]["positive_percent"])
    assert measurement["results"]["moderate_percent"] == pytest.approx(0.0)
    assert measurement["results"]["strong_percent"] == pytest.approx(0.0)
    assert measurement["results"]["h_score"] == pytest.approx(1.0 * measurement["results"]["positive_percent"])
    assert gmm_metadata["classification_method"] == "gmm_bic"
    assert gmm_metadata["h_score_method"] == "gmm_2_component_positive_conservative"
    assert gmm_metadata["class_mapping"] == {"0": "negative", "1": "positive"}


def test_classify_dab_gmm_four_component_case_keeps_classic_hscore_formula(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeGaussianMixture:
        def __init__(self, *, n_components: int, **kwargs) -> None:
            self.n_components = n_components

        def fit(self, samples: np.ndarray) -> "_FakeGaussianMixture":
            if self.n_components == 4:
                self.means_ = np.array([[0.05], [0.25], [0.55], [0.85]], dtype=np.float64)
                self.weights_ = np.full(4, 0.25, dtype=np.float64)
                self.covariances_ = np.full((4, 1, 1), 0.01, dtype=np.float64)
            else:
                self.means_ = np.linspace(0.05, 0.85, self.n_components, dtype=np.float64).reshape(-1, 1)
                self.weights_ = np.full(self.n_components, 1.0 / self.n_components, dtype=np.float64)
                self.covariances_ = np.full((self.n_components, 1, 1), 0.02, dtype=np.float64)
            return self

        def bic(self, samples: np.ndarray) -> float:
            return 0.0 if self.n_components == 4 else float(100 + self.n_components)

        def predict(self, samples: np.ndarray) -> np.ndarray:
            if self.n_components != 4:
                return np.zeros(samples.shape[0], dtype=int)
            values = samples[:, 0]
            return np.select(
                [values < 0.15, values < 0.40, values < 0.70],
                [0, 1, 2],
                default=3,
            ).astype(int)

    class _FakeMixtureModule:
        GaussianMixture = _FakeGaussianMixture

    monkeypatch.setattr(
        "core.image_analysis.stain_engine.importlib.import_module",
        lambda name: _FakeMixtureModule(),
    )

    dab_od = np.concatenate(
        [
            np.full(100, 0.05, dtype=np.float64),
            np.full(100, 0.25, dtype=np.float64),
            np.full(100, 0.55, dtype=np.float64),
            np.full(100, 0.85, dtype=np.float64),
        ]
    ).reshape(20, 20)

    result = classify_dab_gmm(dab_od)

    expected_h_score = (
        result["weak_percent"] + (2.0 * result["moderate_percent"]) + (3.0 * result["strong_percent"])
    )

    assert result["classification_method"] == "gmm_bic"
    assert result["gmm_n_components"] == 4
    assert result["class_mapping"] == {"0": "negative", "1": "weak", "2": "moderate", "3": "strong"}
    assert result["weak_percent"] == pytest.approx(25.0)
    assert result["moderate_percent"] == pytest.approx(25.0)
    assert result["strong_percent"] == pytest.approx(25.0)
    assert result["h_score_method"] == "gmm_component_ranked_hscore"
    assert result["h_score"] == pytest.approx(expected_h_score)


def test_classify_dab_gmm_fixed_bins_fallback_keeps_classic_hscore_formula_at_threshold_edges() -> None:
    dab_od = np.array([[0.14, 0.15, 0.49, 0.50, 0.99, 1.00]], dtype=np.float64)

    result = classify_dab_gmm(dab_od)

    expected_h_score = (
        result["weak_percent"] + (2.0 * result["moderate_percent"]) + (3.0 * result["strong_percent"])
    )

    assert result["classification_method"] == "fixed_bins_fallback"
    assert result["h_score_method"] == "fixed_bins_fallback"
    assert result["negative_percent"] == pytest.approx(100.0 / 6.0)
    assert result["weak_percent"] == pytest.approx(200.0 / 6.0)
    assert result["moderate_percent"] == pytest.approx(200.0 / 6.0)
    assert result["strong_percent"] == pytest.approx(100.0 / 6.0)
    assert result["positive_percent"] == pytest.approx(500.0 / 6.0)
    assert result["h_score"] == pytest.approx(expected_h_score)
    assert any("fewer than 64 pixels" in warning.lower() for warning in result["warnings"])


def test_bright_blank_artifact_regions_are_excluded_from_measurement(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(160, 110, 70))
    for y in range(2, 4):
        for x in range(2, 4):
            image.setPixelColor(x, y, QColor(255, 255, 255))
    image_path = tmp_path / "bright_blank_exclusion.png"
    assert image.save(str(image_path))

    panel = IHCPanel(panel_id="Figure1IHCBrightBlank")
    imported = panel.import_image(image_path, group="Artifact")

    monkeypatch.setattr(
        "core.models.ihc_panel.deconvolve_h_dab",
        lambda rgb, tissue_mask=None: {
            "dab_od": np.full((8, 8), 0.75, dtype=np.float64),
            "stain_separation": {"method": "mock_artifact_exclusion", "warnings": []},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        "core.models.ihc_panel.classify_dab_gmm",
        lambda dab_od, tissue_mask=None: {
            "classification_method": "fixed_bins_fallback",
            "h_score_method": "fixed_bins_fallback",
            "gmm_n_components": None,
            "gmm_bic": None,
            "gmm_bic_candidates": {},
            "component_means": [],
            "negative_percent": 0.0,
            "weak_percent": 0.0,
            "moderate_percent": 100.0,
            "strong_percent": 0.0,
            "positive_percent": 100.0,
            "h_score": 200.0,
            "percentages": {
                "negative": 0.0,
                "weak": 0.0,
                "moderate": 100.0,
                "strong": 0.0,
                "positive": 100.0,
            },
            "warnings": [],
        },
    )

    measurement = panel.measure_dab_positive_area(
        image_uid=imported["uid"],
        settings={"positive_threshold": 0.30, "min_tissue_area_px": 0},
    )

    artifact_summary = measurement["analysis_metadata"]["artifact_summary"]
    assert artifact_summary["artifact_types_detected"] == ["overexposed_blank"]
    assert artifact_summary["artifact_fraction"] == pytest.approx(4.0 / 64.0)
    assert measurement["results"]["total_area_px"] == 60
    assert measurement["results"]["positive_area_px"] == 60
