from __future__ import annotations

from pathlib import Path

import pytest
from PyQt6.QtGui import QColor, QImage

from core.brightfield_statistics import (
    collect_brightfield_stat_values,
    compute_brightfield_statistics,
    summarize_brightfield_ai_object_count_measurements,
)
from core.models import BrightfieldPanel


EXPECTED_CYTO3_SHA = "sha256:70cd701c225490dc63e35595070354a8100b0e243135749e63a263aa93cdacb9"
EXPECTED_AI_WARNINGS = [
    "Research model: restricted commercial-use status",
    "AI-assisted object count uses 256x256 resized inference",
    "Non-square images use direct resize before ONNX inference",
]


def _write_synthetic_brightfield_png(path: Path) -> Path:
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))
    for y in range(2, 5):
        for x in range(1, 4):
            image.setPixelColor(x, y, QColor(210, 40, 40))
    assert image.save(str(path))
    return path


def _settings() -> dict[str, float | int | bool | str]:
    return {
        "segmentation_mode": "hsv",
        "h_min": 0.0,
        "h_max": 0.10,
        "s_min": 0.4,
        "s_max": 1.0,
        "v_min": 0.0,
        "v_max": 0.95,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 2,
        "max_object_area_px": 0,
    }


def _write_synthetic_he_fiber_png(path: Path) -> Path:
    image = QImage(120, 90, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 241, 244))
    for y in range(16, 44):
        for x in range(12, 42):
            image.setPixelColor(x, y, QColor(215, 74, 132))
    for y in range(20, 48):
        for x in range(56, 90):
            image.setPixelColor(x, y, QColor(220, 82, 138))
    for y in range(58, 72):
        for x in range(8, 108):
            image.setPixelColor(x, y, QColor(232, 184, 205))
    assert image.save(str(path))
    return path


def _fake_ai_analysis() -> dict:
    return {
        "ai_object_mask": [[0, 1], [0, 1]],
        "ai_object_count": 1,
        "ai_object_areas_px": [12],
        "ai_object_centroids": [(2.5, 3.5)],
        "results": {
            "ai_object_count": 1,
            "ai_object_total_area_px": 12,
            "ai_object_mean_area_px": 12.0,
            "backend": "cellpose_cyto3",
        },
        "analysis_metadata": {
            "analysis_kind": "brightfield_ai_object_count",
            "model_sha256": EXPECTED_CYTO3_SHA,
        },
        "analysis_warnings": list(EXPECTED_AI_WARNINGS),
    }


def test_create_ai_object_count_measurement_returns_correct_type(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_count.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAI")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr("core.image_analysis.brightfield.analyze_brightfield_ai_object_count", lambda *args, **kwargs: _fake_ai_analysis())

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert measurement["type"] == "brightfield_ai_object_count"
    assert measurement["display_name"] == "H&E Fiber Area"
    assert measurement["model_name"] == "local:he_fiber_area:v1"
    assert measurement["include_in_statistics"] is True


def test_create_he_fiber_area_measurement_replaces_ai_backend_with_calibrated_local_analysis(tmp_path: Path) -> None:
    image_path = _write_synthetic_he_fiber_png(tmp_path / "he_fiber_area.png")
    panel = BrightfieldPanel(panel_id="Figure1HEFiber")
    imported = panel.import_image(image_path, group="Muscle", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert measurement["type"] == "brightfield_ai_object_count"
    assert measurement["display_name"] == "H&E Fiber Area"
    assert measurement["model_name"] == "local:he_fiber_area:v1"
    assert measurement["analysis_metadata"]["analysis_kind"] == "he_fiber_area"
    assert measurement["results"]["ai_object_count"] == measurement["results"]["he_fiber_count"]
    assert measurement["results"]["ai_object_areas_px"] == measurement["results"]["he_fiber_areas_px"]
    assert len(measurement["results"]["he_fiber_areas_px"]) == measurement["results"]["he_fiber_count"]
    assert measurement["results"]["he_fiber_mean_area_px"] > 0.0
    assert measurement["overlay_generated"] is True


def test_he_fiber_area_results_rescale_when_measurement_scale_changes(tmp_path: Path) -> None:
    image_path = _write_synthetic_he_fiber_png(tmp_path / "he_fiber_rescale.png")
    panel = BrightfieldPanel(panel_id="Figure1HEFiberRescale")
    imported = panel.import_image(image_path, group="Muscle", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())
    first_px = float(measurement["results"]["ai_object_areas_px"][0])
    original_um2 = list(measurement["results"]["ai_object_areas_um2"])
    original_scale_um = measurement["measurement_scale_bar"]["um_per_px"]

    panel.update_measurement_scale_bar(imported["uid"], um_per_px=2.0, length_um=50.0, calibration_source="manual")
    refreshed = panel.analysis["brightfield_ai_object_count_measurements"][-1]

    assert refreshed["measurement_uuid"] == measurement["measurement_uuid"]
    assert refreshed["measurement_scale_bar"]["um_per_px"] == original_scale_um
    assert refreshed["results"]["ai_object_areas_um2"] == original_um2
    assert refreshed["results"]["he_fiber_areas_um2"] == original_um2

    panel.rescale_ai_object_count_measurements(imported["uid"])
    refreshed = panel.analysis["brightfield_ai_object_count_measurements"][-1]

    assert refreshed["measurement_uuid"] == measurement["measurement_uuid"]
    assert refreshed["measurement_scale_bar"]["um_per_px"] == 2.0
    assert refreshed["results"]["ai_object_areas_um2"][0] == first_px * 4.0
    assert refreshed["results"]["he_fiber_areas_um2"][0] == first_px * 4.0


def test_he_fiber_area_measurement_requires_explicit_calibration(tmp_path: Path) -> None:
    image_path = _write_synthetic_he_fiber_png(tmp_path / "he_fiber_requires_calibration.png")
    panel = BrightfieldPanel(panel_id="Figure1HEFiberCalibration")
    imported = panel.import_image(image_path, group="Muscle", stain_type="H&E")

    with pytest.raises(ValueError, match="Calibrate H&E Fiber Area"):
        panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert panel.he_fiber_area_calibration(imported["uid"]) is None
    assert panel.analysis["brightfield_ai_object_count_measurements"] == []


def test_he_fiber_area_overlay_uses_saved_object_mask(tmp_path: Path) -> None:
    image_path = _write_synthetic_he_fiber_png(tmp_path / "he_fiber_overlay.png")
    panel = BrightfieldPanel(panel_id="Figure1HEFiberOverlay")
    imported = panel.import_image(image_path, group="Muscle", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    overlay = panel.render_measurement_overlay(measurement_uuid=measurement["measurement_uuid"])

    assert overlay.shape[:2] == (90, 120)
    assert overlay[30, 26].tolist() != [215, 74, 132]
    assert overlay[5, 5].tolist() == [246, 241, 244]


def test_he_fiber_area_warnings_do_not_include_research_model_strings(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_warning.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAIWarn")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr("core.image_analysis.brightfield.analyze_brightfield_ai_object_count", lambda *args, **kwargs: _fake_ai_analysis())

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    for warning in EXPECTED_AI_WARNINGS:
        assert warning not in measurement["warnings"]


def test_ai_count_stored_in_separate_list(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_separate.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAISeparate")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr("core.image_analysis.brightfield.analyze_brightfield_ai_object_count", lambda *args, **kwargs: _fake_ai_analysis())

    panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert len(panel.analysis["brightfield_ai_object_count_measurements"]) == 1
    assert panel.analysis.get("brightfield_measurements") == []


def test_ai_count_measurement_preserves_per_instance_metrics(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_per_instance.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAIperinst")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr(
        "core.image_analysis.brightfield.analyze_brightfield_ai_object_count",
        lambda *args, **kwargs: _fake_ai_analysis(),
    )

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert "ai_object_areas_px" in measurement["results"]
    assert measurement["results"]["ai_object_areas_px"] == measurement["results"]["he_fiber_areas_px"]
    assert measurement["results"]["ai_object_areas_px"]
    assert "ai_object_centroids" in measurement["results"]
    assert measurement["results"]["ai_object_centroids"] == measurement["results"]["he_fiber_centroids"]
    assert all(isinstance(centroid, list) for centroid in measurement["results"]["ai_object_centroids"])


def test_ai_count_measurement_records_mask_metadata(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_mask_meta.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAImaskmeta")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr(
        "core.image_analysis.brightfield.analyze_brightfield_ai_object_count",
        lambda *args, **kwargs: _fake_ai_analysis(),
    )

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    analysis_metadata = measurement["analysis_metadata"]
    assert analysis_metadata["ai_object_mask_available"] is True
    assert analysis_metadata["ai_object_mask_persisted"] is False
    assert analysis_metadata["ai_object_mask_shape"] == [6, 6]
    assert "ai_object_mask" not in measurement
    assert "ai_object_mask" not in measurement.get("results", {})


def test_he_fiber_area_measurement_records_mask_available_without_ai_backend(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_no_mask.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAInomask")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])

    monkeypatch.setattr("core.image_analysis.brightfield.analyze_brightfield_ai_object_count", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("legacy AI backend must not run")))

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    analysis_metadata = measurement["analysis_metadata"]
    assert analysis_metadata["ai_object_mask_available"] is True
    assert analysis_metadata["ai_object_mask_persisted"] is False
    assert analysis_metadata["ai_object_mask_shape"] == [6, 6]


def test_traditional_measurement_unaffected_by_ai_measurement(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_and_traditional.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAITraditional")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr("core.image_analysis.brightfield.analyze_brightfield_ai_object_count", lambda *args, **kwargs: _fake_ai_analysis())

    traditional = panel.create_measurement(image_uid=imported["uid"], settings=_settings())
    ai_measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())

    assert panel.analysis["brightfield_measurements"][-1]["measurement_uuid"] == traditional["measurement_uuid"]
    assert panel.analysis["brightfield_ai_object_count_measurements"][-1]["measurement_uuid"] == ai_measurement["measurement_uuid"]
    assert ai_measurement["include_in_statistics"] is True

    ai_statistics = summarize_brightfield_ai_object_count_measurements(panel)
    assert ai_statistics["source"] == "brightfield_ai_object_count"
    assert ai_statistics["n_measurements"] == 1
    assert ai_statistics["rows"][0]["measurement_uuid"] == ai_measurement["measurement_uuid"]
    assert ai_statistics["rows"][0]["ai_object_count"] == 1.0

    rows = collect_brightfield_stat_values(panel, "Object Count")
    assert rows
    assert all(ai_measurement["measurement_uuid"] not in row.get("measurement_uuids", []) for row in rows)

    brightfield_statistics = compute_brightfield_statistics(
        panel,
        metric="Object Count",
        group_assignments={},
        reference_group="Tumor",
        test_name="Welch t-test",
    )
    assert brightfield_statistics["source"] == "brightfield_measurement"
    assert all(
        ai_measurement["measurement_uuid"] not in row.get("measurement_uuids", [])
        for row in brightfield_statistics.get("plot_data", [])
    )


def test_ai_count_measurement_persistence_fields_do_not_pollute_traditional_results(tmp_path: Path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "ai_persistence_fields.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAIPersist")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    monkeypatch.setattr(
        "core.image_analysis.brightfield.analyze_brightfield_ai_object_count",
        lambda *args, **kwargs: _fake_ai_analysis(),
    )

    measurement = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())
    restored = BrightfieldPanel.from_dict(panel.to_dict())
    restored_measurements = restored.analysis["brightfield_ai_object_count_measurements"]
    persisted = restored_measurements[-1]

    assert len(restored_measurements) == 1
    assert persisted["type"] == "brightfield_ai_object_count"
    assert persisted["display_name"] == "H&E Fiber Area"
    assert persisted["model_name"] == "local:he_fiber_area:v1"
    assert persisted["model_hash"]
    assert persisted["stain_type"] == "H&E"
    assert persisted["image_uid"] == imported["uid"]
    assert persisted["measurement_uuid"] == measurement["measurement_uuid"]
    assert persisted["include_in_statistics"] is True
    for warning in EXPECTED_AI_WARNINGS:
        assert warning not in persisted["warnings"]

    results = persisted["results"]
    assert results["ai_object_count"] == results["he_fiber_count"]
    assert results["ai_object_areas_px"] == results["he_fiber_areas_px"]
    assert results["ai_object_centroids"] == results["he_fiber_centroids"]

    analysis_metadata = persisted["analysis_metadata"]
    assert analysis_metadata["ai_object_mask_available"] is True
    assert analysis_metadata["ai_object_mask_persisted"] is False
    assert analysis_metadata["ai_object_mask_shape"] == [6, 6]

    assert "ai_object_mask" not in persisted
    assert "ai_object_mask" not in results
    assert "ai_object_mask" not in analysis_metadata
    assert restored.analysis.get("brightfield_measurements") == []
    assert collect_brightfield_stat_values(restored, "Object Count") == []
    restored_summary = summarize_brightfield_ai_object_count_measurements(restored)
    assert restored_summary["source"] == "brightfield_ai_object_count"
    assert restored_summary["n_measurements"] == 1
    assert restored_summary["rows"][0]["measurement_uuid"] == measurement["measurement_uuid"]


def test_he_fiber_sensitivity_settings_persist_and_refresh_latest_results(tmp_path: Path) -> None:
    image_path = _write_synthetic_he_fiber_png(tmp_path / "he_fiber_persist.png")
    panel = BrightfieldPanel(panel_id="Figure1HEFiberPersist")
    imported = panel.import_image(image_path, group="Muscle", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    baseline = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=_settings())
    assert baseline["params"].get("he_fiber_segmentation_method", "original") == "original"
    assert baseline["analysis_metadata"]["he_fiber_segmentation_method"] == "original"
    assert baseline["analysis_metadata"]["segmentation_path"] == "sample_profile"

    first_settings = dict(_settings())
    first_settings["he_fiber_foreground_sensitivity"] = 35
    first_settings["he_fiber_splitting_sensitivity"] = 0
    first_settings["he_fiber_segmentation_method"] = "anti_oversegmentation"
    first = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=first_settings)

    assert panel.data["analysis_settings"]["he_fiber_foreground_sensitivity"] == 35
    assert panel.data["analysis_settings"]["he_fiber_splitting_sensitivity"] == 0
    assert panel.data["analysis_settings"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    assert first["params"]["he_fiber_foreground_sensitivity"] == 35
    assert first["params"]["he_fiber_splitting_sensitivity"] == 0
    assert first["params"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    assert first["analysis_metadata"]["he_fiber_foreground_sensitivity"] == 35
    assert first["analysis_metadata"]["segmentation_path"] == "approved_auto"

    second_settings = dict(_settings())
    second_settings["he_fiber_foreground_sensitivity"] = 65
    second_settings["he_fiber_splitting_sensitivity"] = 80
    second_settings["he_fiber_segmentation_method"] = "anti_oversegmentation"
    second = panel.create_ai_object_count_measurement(image_uid=imported["uid"], settings=second_settings)
    measurements = panel.analysis["brightfield_ai_object_count_measurements"]
    latest = measurements[-1]

    assert latest["measurement_uuid"] == second["measurement_uuid"]
    assert latest["measurement_uuid"] != first["measurement_uuid"]
    assert latest["params"]["he_fiber_foreground_sensitivity"] == 65
    assert latest["params"]["he_fiber_splitting_sensitivity"] == 80
    assert latest["params"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    assert latest["results"]["he_fiber_count"] == latest["results"]["ai_object_count"]
    assert panel.data["analysis_settings"]["he_fiber_foreground_sensitivity"] == 65
    assert panel.data["analysis_settings"]["he_fiber_splitting_sensitivity"] == 80
    reused = panel.create_ai_object_count_measurement(image_uid=imported["uid"])
    assert reused["params"]["he_fiber_foreground_sensitivity"] == 65
    assert reused["params"]["he_fiber_splitting_sensitivity"] == 80
    assert reused["params"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    restored = BrightfieldPanel.from_dict(panel.to_dict())
    assert restored.data["analysis_settings"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
    assert restored.data["analysis_settings"]["he_fiber_splitting_sensitivity"] == 80
