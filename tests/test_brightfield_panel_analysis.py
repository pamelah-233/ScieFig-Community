from __future__ import annotations

from pathlib import Path

import numpy as np
from PyQt6.QtGui import QColor, QImage
import pytest

import core.models.brightfield_panel as brightfield_panel_module
from core.constants import SOFTWARE_VERSION
from core.brightfield_semantics import (
    BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
    BRIGHTFIELD_LEGACY_UNKNOWN,
    BRIGHTFIELD_OBJECT_COUNT_SEMANTICS,
)
from core.image_analysis import analyze_brightfield_image
from core.image_analysis.brightfield_guided import (
    BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION,
    BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
    BRIGHTFIELD_ASSISTED_SENSITIVITY_MAPPING_VERSION,
    BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION,
    BRIGHTFIELD_GUIDED_MAPPING_VERSION,
    BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS,
    BRIGHTFIELD_GUIDED_PREVIEW_STATUS,
    apply_brightfield_assisted_small_region_filter_bias,
    apply_brightfield_assisted_threshold_bias,
    apply_brightfield_foolproof_threshold_bias,
    build_brightfield_guided_effective_settings_snapshot,
    neutral_brightfield_guided_slider_values,
)
from core.image_analysis.io_engine import ImageImportResult
from core.models import BrightfieldPanel
from core.utils.hashing import sha256_json


def _assert_step2_measurement_fields(
    measurement: dict[str, object],
    *,
    threshold_source: str,
    task_id: str,
) -> None:
    assert isinstance(measurement["processing_time_ms"], int)
    assert measurement["processing_time_ms"] >= 0
    assert measurement["backend_name"] == "sciefig.image_analysis"
    assert measurement["backend_version"] == SOFTWARE_VERSION
    assert isinstance(measurement["model_name"], str) and measurement["model_name"]
    assert isinstance(measurement["model_hash"], str) and measurement["model_hash"].startswith("sha256:")
    assert measurement["include_in_statistics"] is True
    assert isinstance(measurement["warnings"], list)
    assert isinstance(measurement["user_overrides"], dict)
    assert measurement["analysis_metadata"]["task_id"] == task_id
    assert measurement["analysis_metadata"]["threshold_source"] == threshold_source


def _assert_b1_semantics(
    measurement: dict[str, object],
    *,
    analysis_mode: str,
    guided_mode: str,
    threshold_source: str,
    segmentation_mode: str,
    tissue_mask_enabled: bool | str,
) -> None:
    metadata = measurement["analysis_metadata"]
    assert measurement["guided_mode"] == guided_mode
    assert measurement["threshold_source"] == threshold_source
    assert metadata["analysis_mode"] == analysis_mode
    assert metadata["guided_mode"] == guided_mode
    assert metadata["threshold_source"] == threshold_source
    assert metadata["segmentation_mode"] == segmentation_mode
    assert metadata["tissue_mask_enabled"] == tissue_mask_enabled
    assert metadata["area_fraction_denominator"] == BRIGHTFIELD_AREA_FRACTION_DENOMINATOR
    assert metadata["object_count_semantics"] == BRIGHTFIELD_OBJECT_COUNT_SEMANTICS


def _assert_neutral_guided_slider_values(slider_values: dict[str, object]) -> None:
    neutral = neutral_brightfield_guided_slider_values()
    for key, value in neutral.items():
        assert slider_values[key] == value


def _write_synthetic_brightfield_png(path: Path) -> Path:
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))

    for y in range(3):
        for x in range(3):
            image.setPixelColor(x, y, QColor(210, 40, 40))

    for y in range(3, 6):
        for x in range(3, 6):
            image.setPixelColor(x, y, QColor(40, 80, 210))

    assert image.save(str(path))
    return path


def _write_synthetic_pink_he_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    for y in range(1, 5):
        for x in range(2, 6):
            image.setPixelColor(x, y, QColor(235, 170, 190))

    assert image.save(str(path))
    return path


def _write_synthetic_muscle_like_he_png(path: Path) -> Path:
    image = QImage(160, 120, QImage.Format.Format_RGB32)
    image.fill(QColor(248, 248, 248))

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
                        image.setPixelColor(x, y, QColor(232, 170, 192) if (x + y) % 4 else QColor(165, 110, 160))

    assert image.save(str(path))
    return path


def _write_synthetic_threshold_edge_he_png(path: Path) -> Path:
    image = QImage(64, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    for y in range(4, 16):
        for x in range(4, 16):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    for y in range(4, 16):
        for x in range(20, 32):
            image.setPixelColor(x, y, QColor(200, 185, 160))

    for y in range(4, 6):
        for x in range(36, 40):
            image.setPixelColor(x, y, QColor(215, 210, 170))

    assert image.save(str(path))
    return path


def _write_synthetic_min_area_edge_he_png(path: Path) -> Path:
    image = QImage(64, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    for y in range(4, 6):
        for x in range(4, 7):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    for y in range(4, 7):
        for x in range(14, 17):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    for y in range(4, 7):
        for x in range(24, 29):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    assert image.save(str(path))
    return path


def _write_synthetic_zero_baseline_min_area_he_png(path: Path) -> Path:
    image = QImage(64, 32, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))

    image.setPixelColor(4, 4, QColor(232, 170, 192))

    for y in range(4, 6):
        for x in range(14, 16):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    for y in range(4, 7):
        for x in range(24, 27):
            image.setPixelColor(x, y, QColor(232, 170, 192))

    assert image.save(str(path))
    return path


def _read_rgb(path: Path) -> np.ndarray:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGB888)
    ptr = image.bits()
    ptr.setsize(image.sizeInBytes())
    data = np.frombuffer(ptr, dtype=np.uint8).reshape((image.height(), image.bytesPerLine()))
    return data[:, : image.width() * 3].reshape((image.height(), image.width(), 3)).copy()


def _recompute_brightfield_results(panel: BrightfieldPanel, measurement: dict[str, object]) -> dict[str, object]:
    _image_record, rgb = panel._load_image_rgb(str(measurement["image_uid"]))
    roi_snapshot = dict(measurement.get("roi", {}))
    roi_mask = panel._build_roi_mask(rgb.shape[:2], roi_snapshot)
    analysis = analyze_brightfield_image(rgb, dict(measurement.get("params", {})), roi_mask=roi_mask)
    return dict(analysis["results"])


def test_import_image_uses_io_engine_metadata(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = tmp_path / "delegated_import.png"
    image_path.write_bytes(b"stub")
    panel = BrightfieldPanel(panel_id="Figure1BFIOMetadata")
    expected = ImageImportResult(
        analysis_data=np.zeros((3, 6, 8), dtype=np.uint8),
        metadata={
            "width_px": 8,
            "height_px": 6,
            "pixel_size_um": 0.75,
            "pixel_size_unit": "um",
            "objective": "10x",
            "bit_depth": 8,
            "n_channels": 3,
            "source_format": "PNG",
            "axes": "CYX",
            "work_data_downsample": 1,
            "io_warnings": ["rgb warning"],
        },
        source_hash="sha256:" + "c" * 64,
        channel_names=["Red", "Green", "Blue"],
    )
    calls: list[Path] = []

    def _fake_read_microscopy_image(source: str | Path) -> ImageImportResult:
        calls.append(Path(source))
        return expected

    monkeypatch.setattr("core.models.brightfield_panel.read_microscopy_image", _fake_read_microscopy_image)

    imported = panel.import_image(image_path, group="Tumor", stain_type="Oil Red O")

    assert calls == [image_path]
    assert imported["source_hash"] == expected.source_hash
    assert imported["metadata"] == {
        "width_px": 8,
        "height_px": 6,
        "pixel_size_um": 0.75,
        "magnification": "10x",
        "source_format": "PNG",
        "n_channels": 3,
        "pixel_size_unit": "um",
        "bit_depth": 8,
        "axes": "CYX",
        "work_data_downsample": 1,
        "io_warnings": ["rgb warning"],
    }


def test_create_measurement_uses_io_engine_rgb_loader(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "delegated_measure.png")
    panel = BrightfieldPanel(panel_id="Figure1BFRGBDelegation")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Oil Red O")
    expected_rgb = _read_rgb(image_path)
    calls: list[Path] = []

    def _fake_read_rgb_image(source: str | Path) -> tuple[np.ndarray, dict[str, object]]:
        calls.append(Path(source))
        return expected_rgb.copy(), {"source_format": "PNG"}

    monkeypatch.setattr("core.models.brightfield_panel.read_rgb_image", _fake_read_rgb_image)
    monkeypatch.setattr(panel, "_read_raster", lambda source: (_ for _ in ()).throw(AssertionError("analysis should use io_engine")))

    measurement = panel.create_measurement(image_uid=imported["uid"], settings=_settings())

    assert calls == [image_path]
    assert measurement["results"]["stained_area_px"] == 9


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


def test_create_measurement_stores_panel_and_image_records(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_brightfield.png")
    panel = BrightfieldPanel(panel_id="Figure1BF")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Oil Red O")

    measurement = panel.create_measurement(settings=_settings())

    assert measurement["image_uid"] == imported["uid"]
    assert measurement["image_name"] == image_path.name
    assert measurement["group"] == "Tumor"
    assert measurement["stain_type"] == "Oil Red O"
    assert measurement["measurement_uuid"]
    assert measurement["results"]["segmentation_mode"] == "hsv"
    assert measurement["results"]["stained_area_px"] == 9
    assert measurement["results"]["roi_area_px"] == 36
    assert measurement["results"]["area_fraction_percent"] == pytest.approx(25.0)
    assert measurement["results"]["object_count"] == 1
    assert measurement["results"]["mean_object_area_px"] == pytest.approx(9.0)
    _assert_step2_measurement_fields(
        measurement,
        threshold_source="manual",
        task_id="brightfield_oil_red_o_area",
    )
    assert measurement["warnings"] == []
    assert measurement["user_overrides"] == {}
    assert panel.analysis["brightfield_measurements"][-1]["measurement_uuid"] == measurement["measurement_uuid"]
    assert panel.active_image()["measurements"][-1]["measurement_uuid"] == measurement["measurement_uuid"]


def test_render_measurement_overlay_and_round_trip_preserve_measurements(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_overlay.png")
    panel = BrightfieldPanel(panel_id="Figure1BFOverlay")
    panel.import_image(image_path, group="Tumor")
    measurement = panel.create_measurement(settings=_settings())

    original = _read_rgb(image_path)
    overlay = panel.render_measurement_overlay(measurement_uuid=measurement["measurement_uuid"])

    assert overlay.dtype == np.uint8
    assert overlay.shape == original.shape
    assert overlay[0, 0].tolist() != original[0, 0].tolist()
    assert overlay[5, 5].tolist() == original[5, 5].tolist()

    restored = BrightfieldPanel.from_dict(panel.to_dict())

    assert restored.analysis["brightfield_measurements"][0]["measurement_uuid"] == measurement["measurement_uuid"]
    assert restored.analysis["brightfield_measurements"][0]["model_hash"] == measurement["model_hash"]
    assert restored.analysis["brightfield_measurements"][0]["analysis_metadata"]["threshold_source"] == "manual"
    assert restored.analysis["brightfield_measurements"][0]["results"] == measurement["results"]
    assert restored.active_image()["measurements"][0]["measurement_uuid"] == measurement["measurement_uuid"]
    assert restored.data["analysis_settings"]["segmentation_mode"] == "hsv"


def test_render_measurement_overlay_invalidates_mismatched_analysis_mask_cache(tmp_path, monkeypatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_overlay_cache_mismatch.png")
    panel = BrightfieldPanel(panel_id="Figure1BFOverlayCacheMismatch")
    panel.import_image(image_path, group="Tumor")
    analyze_settings: list[dict[str, object]] = []
    original_analyze = brightfield_panel_module.analyze_brightfield_image

    def counting_analyze(rgb, settings, *, roi_mask=None):
        analyze_settings.append(dict(settings))
        return original_analyze(rgb, settings, roi_mask=roi_mask)

    monkeypatch.setattr(brightfield_panel_module, "analyze_brightfield_image", counting_analyze)
    measurement = panel.create_measurement(settings=_settings())
    assert len(analyze_settings) == 1

    measurement["params"]["h_min"] = 0.5
    measurement["params"]["h_max"] = 0.6
    overlay = panel.render_measurement_overlay(measurement_uuid=measurement["measurement_uuid"])

    assert overlay.shape == (6, 6, 3)
    assert len(analyze_settings) == 2
    assert analyze_settings[-1]["h_min"] == 0.5
    assert measurement["measurement_uuid"] not in panel._brightfield_analysis_mask_cache


def test_brightfield_analysis_mask_cache_is_transient_bounded_and_lru() -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFOverlayCacheBound")
    panel.data["brightfield_images"] = [
        {
            "uid": "image-1",
            "source_path": "source.png",
            "source_hash": "sha256:source",
            "measurements": [],
        }
    ]
    panel.data["active_image_uid"] = "image-1"

    def measurement(index: int) -> dict[str, object]:
        return {
            "measurement_uuid": f"measurement-{index}",
            "image_uid": "image-1",
            "params": {"threshold": index},
            "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None},
        }

    for index in range(panel._ANALYSIS_MASK_CACHE_LIMIT):
        panel._cache_brightfield_analysis_mask(measurement(index), np.ones((2, 3), dtype=np.uint8))

    assert panel._cached_brightfield_analysis_mask(measurement(0)) is not None
    panel._cache_brightfield_analysis_mask(
        measurement(panel._ANALYSIS_MASK_CACHE_LIMIT),
        np.ones((2, 3), dtype=np.uint8),
    )

    assert len(panel._brightfield_analysis_mask_cache) == panel._ANALYSIS_MASK_CACHE_LIMIT
    assert "measurement-0" in panel._brightfield_analysis_mask_cache
    assert "measurement-1" not in panel._brightfield_analysis_mask_cache
    assert all(
        entry["mask"].dtype == np.bool_
        for entry in panel._brightfield_analysis_mask_cache.values()
    )
    payload = panel.to_dict()
    assert "_brightfield_analysis_mask_cache" not in payload
    assert BrightfieldPanel.from_dict(payload)._brightfield_analysis_mask_cache == {}


def test_brightfield_he_fiber_caches_are_bounded_and_evicted_overlay_regenerates_exactly(tmp_path) -> None:
    image_path = _write_synthetic_muscle_like_he_png(tmp_path / "he_fiber_cache_bound.png")
    panel = BrightfieldPanel(panel_id="Figure1BFHEFiberCacheBound")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])

    first = panel.create_ai_object_count_measurement(image_uid=imported["uid"])
    first_uuid = str(first["measurement_uuid"])
    first_labels = panel._transient_he_fiber_area_labels[first_uuid].copy()
    for _ in range(panel._HE_FIBER_AREA_CACHE_LIMIT):
        panel.create_ai_object_count_measurement(image_uid=imported["uid"])

    assert len(panel._transient_he_fiber_area_masks) == panel._HE_FIBER_AREA_CACHE_LIMIT
    assert len(panel._transient_he_fiber_area_labels) == panel._HE_FIBER_AREA_CACHE_LIMIT
    assert first_uuid not in panel._transient_he_fiber_area_masks
    assert first_uuid not in panel._transient_he_fiber_area_labels

    _image_record, rgb = panel._load_image_rgb(imported["uid"])
    expected_overlay = brightfield_panel_module.render_he_fiber_area_overlay(
        rgb,
        first_labels,
        color=(0, 244, 255),
        alpha=0.35,
    )
    regenerated_overlay = panel.render_measurement_overlay(measurement_uuid=first_uuid)

    assert regenerated_overlay.shape == rgb.shape
    assert regenerated_overlay.shape == first_labels.shape + (3,)
    assert np.array_equal(regenerated_overlay, expected_overlay)
    assert first_uuid in panel._transient_he_fiber_area_masks
    assert first_uuid in panel._transient_he_fiber_area_labels
    payload = panel.to_dict()
    assert "_transient_he_fiber_area_masks" not in payload
    assert "_transient_he_fiber_area_labels" not in payload


def test_measurement_uses_stored_rectangle_roi_and_preserves_multiple_measurements(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_roi.png")
    panel = BrightfieldPanel(panel_id="Figure1BFROI")
    imported = panel.import_image(image_path, group="Tumor")

    whole_image = panel.create_measurement(settings=_settings())
    panel.set_image_roi(imported["uid"], "rectangle", {"x": 0, "y": 0, "w": 3, "h": 3})
    roi_measurement = panel.create_measurement(image_uid=imported["uid"], settings=_settings())

    panel.clear_image_roi(imported["uid"])
    cleared = panel.create_measurement(image_uid=imported["uid"], settings=_settings())

    assert roi_measurement["roi"]["items"][-1]["coordinates_px"] == {"x": 0, "y": 0, "w": 3, "h": 3}
    assert whole_image["results"]["stained_area_px"] == 9
    assert roi_measurement["results"]["stained_area_px"] == 9
    assert roi_measurement["results"]["roi_area_px"] == 9
    assert roi_measurement["results"]["area_fraction_percent"] == pytest.approx(100.0)
    assert panel.active_roi(imported["uid"]) is None
    assert cleared["roi"]["mode"] == "whole_image"
    assert cleared["roi"]["items"] == []
    assert cleared["results"]["stained_area_px"] == 9
    assert cleared["results"]["roi_area_px"] == 36
    assert cleared["results"]["area_fraction_percent"] == pytest.approx(25.0)
    assert len(panel.active_image()["measurements"]) == 3
    assert len(panel.analysis["brightfield_measurements"]) == 3
    assert cleared["measurement_uuid"] != roi_measurement["measurement_uuid"]


def test_group_and_stain_updates_apply_to_active_image(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_meta.png")
    panel = BrightfieldPanel(panel_id="Figure1BFMeta")
    imported = panel.import_image(image_path)

    panel.set_image_group(imported["uid"], "Control")
    panel.set_image_stain_type(imported["uid"], "Crystal violet")

    assert panel.active_image()["group"] == "Control"
    assert panel.active_image()["stain_type"] == "Crystal violet"


def test_other_stain_label_normalizes_to_high_contrast_bucket(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_other_stain.png")
    panel = BrightfieldPanel(panel_id="Figure1BFOther")

    imported = panel.import_image(image_path, stain_type="Other")
    panel.set_image_stain_type(imported["uid"], "Other high-contrast stain")

    assert imported["stain_type"] == "Other high-contrast stain"
    assert panel.active_image()["stain_type"] == "Other high-contrast stain"


def test_auto_analyze_he_detects_pink_signal_when_default_hsv_measurement_is_zero(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "synthetic_pink_he.png")
    panel = BrightfieldPanel(panel_id="Figure1BFHEAuto")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    manual = panel.create_measurement(image_uid=imported["uid"])
    auto = panel.auto_analyze_he(image_uid=imported["uid"])

    assert manual["results"]["stained_area_px"] == 0
    assert auto["results"]["stained_area_px"] > 0
    assert auto["guided_mode"] == "foolproof"
    assert auto["task_profile"]["task_id"] == "brightfield_he_eosin_area"
    assert auto["threshold_source"] == "image_auto"
    assert auto["params"]["segmentation_mode"] == "lab"
    _assert_b1_semantics(
        auto,
        analysis_mode="guided_foolproof",
        guided_mode="foolproof",
        threshold_source="image_auto",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )
    assert auto["auto_parameter_result"]["recommended_thresholds"]["segmentation_mode"] == "lab"
    assert auto["qc_messages"] == []
    _assert_step2_measurement_fields(
        auto,
        threshold_source="image_auto",
        task_id="brightfield_he_eosin_area",
    )
    assert auto["user_overrides"] == {}


def test_b2a_foolproof_whole_image_guided_metadata_is_persisted_without_changing_results(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "b2a_foolproof_whole_image.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2AFoolproofWhole")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    measurement = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof")
    recomputed_results = _recompute_brightfield_results(panel, measurement)
    restored = BrightfieldPanel.from_dict(panel.to_dict())
    restored_measurement = restored.analysis["brightfield_measurements"][-1]

    assert measurement["results"] == recomputed_results
    assert measurement["results"]["area_fraction_percent"] == pytest.approx(25.0)
    assert measurement["results"]["object_count"] == 1
    assert measurement["results"]["mean_object_area_px"] == pytest.approx(16.0)
    assert measurement["results"]["roi_area_px"] == 64
    assert measurement["params"]["a_min"] == pytest.approx(20.727378845214844)
    assert measurement["guided_mapping_version"] == BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION
    assert measurement["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_STATUS
    assert measurement["guided_baseline_source"] == "image_auto"
    assert measurement["guided_slider_values"] == {
        **neutral_brightfield_guided_slider_values(),
        "threshold_bias_active": True,
        "threshold_bias_inactive_reason": None,
    }
    assert measurement["guided_effective_settings_hash"] == sha256_json(measurement["guided_effective_settings"])
    assert measurement["guided_effective_settings"]["image_context"] == {
        "image_uid": imported["uid"],
        "image_context_id": None,
        "source_name": image_path.name,
        "source_digest": imported["source_hash"],
    }
    assert measurement["guided_effective_settings"]["effective_settings"] == measurement["params"]
    assert measurement["guided_effective_settings"]["guided_mode"] == "foolproof"
    assert measurement["guided_effective_settings"]["task_id"] == "brightfield_he_eosin_area"
    assert measurement["guided_effective_settings"]["threshold_source"] == "image_auto"
    assert measurement["guided_effective_settings"]["roi_context"]["mode"] == "whole_image"
    assert measurement["guided_effective_settings"]["analysis_region"]["region_kind"] == "whole_image"
    assert restored_measurement["guided_effective_settings"] == measurement["guided_effective_settings"]
    assert restored_measurement["guided_effective_settings_hash"] == measurement["guided_effective_settings_hash"]
    assert restored_measurement["guided_slider_values"] == {
        **neutral_brightfield_guided_slider_values(),
        "threshold_bias_active": True,
        "threshold_bias_inactive_reason": None,
    }


def test_b2a_foolproof_roi_guided_metadata_is_persisted_without_changing_results(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "b2a_foolproof_roi.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2AFoolproofROI")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    panel.set_image_roi(imported["uid"], "rectangle", {"x": 2, "y": 1, "w": 4, "h": 4})

    measurement = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof")
    recomputed_results = _recompute_brightfield_results(panel, measurement)

    assert measurement["results"] == recomputed_results
    assert measurement["results"]["area_fraction_percent"] == pytest.approx(100.0)
    assert measurement["results"]["object_count"] == 1
    assert measurement["results"]["mean_object_area_px"] == pytest.approx(16.0)
    assert measurement["results"]["roi_area_px"] == 16
    assert measurement["params"]["a_min"] == pytest.approx(20.727378845214844)
    assert measurement["guided_slider_values"] == {
        **neutral_brightfield_guided_slider_values(),
        "threshold_bias_active": True,
        "threshold_bias_inactive_reason": None,
    }
    assert measurement["guided_effective_settings"]["image_context"] == {
        "image_uid": imported["uid"],
        "image_context_id": None,
        "source_name": image_path.name,
        "source_digest": imported["source_hash"],
    }
    assert measurement["guided_effective_settings"]["roi_context"]["mode"] == "selected_roi"
    assert measurement["guided_effective_settings"]["roi_context"]["active_geometry"] == {
        "roi_uuid": measurement["roi"]["active_item_uuid"],
        "type": "rectangle",
        "x": 2,
        "y": 1,
        "w": 4,
        "h": 4,
    }
    assert measurement["guided_effective_settings"]["analysis_region"]["region_kind"] == "selected_roi"
    assert measurement["guided_effective_settings"]["analysis_region"]["roi_limited"] is True


def test_b2c1_assisted_threshold_bias_zero_preserves_current_baseline_and_persists_metadata(tmp_path) -> None:
    image_path = _write_synthetic_muscle_like_he_png(tmp_path / "b2a_assisted.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2AAssisted")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high"},
    )
    measurement = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 0},
    )
    recomputed_results = _recompute_brightfield_results(panel, measurement)
    restored = BrightfieldPanel.from_dict(panel.to_dict())
    restored_measurement = restored.analysis["brightfield_measurements"][-1]

    assert measurement["params"] == baseline["params"]
    assert measurement["results"] == baseline["results"]
    assert measurement["results"] == recomputed_results
    assert BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION == "v7.2-b2c2-assisted-sensitivity-min-area-1"
    assert measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_STATUS
    assert measurement["guided_baseline_source"] == "image_auto"
    assert measurement["guided_slider_values"] == {
        **neutral_brightfield_guided_slider_values(),
        "threshold_bias_active": True,
        "threshold_bias_inactive_reason": None,
        "small_region_filter_bias": 0,
        "small_region_filter_mapping_version": BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION,
        "small_region_filter_baseline_min_area_px": 8,
        "small_region_filter_effective_min_area_px": 8,
    }
    assert measurement["guided_effective_settings"]["guided_mode"] == "assisted"
    assert measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["guided_effective_settings"]["effective_settings"]["sensitivity"] == "low"
    assert measurement["guided_effective_settings"]["effective_settings"]["cleanup_level"] == "high"
    assert measurement["guided_effective_settings"]["threshold_method"] == "lab_eosin_quantiles"
    assert measurement["analysis_metadata"]["assisted_threshold_bias"] == 0
    assert measurement["analysis_metadata"]["assisted_threshold_bias_active"] is True
    assert measurement["analysis_metadata"]["assisted_threshold_bias_inactive_reason"] is None
    assert measurement["auto_parameter_result"]["metadata"]["assisted_threshold_bias"] == 0
    assert measurement["auto_parameter_result"]["metadata"]["assisted_threshold_bias_active"] is True
    assert measurement["auto_parameter_result"]["metadata"]["assisted_threshold_bias_inactive_reason"] is None
    assert "foolproof_threshold_bias" not in measurement["analysis_metadata"]
    assert "foolproof_threshold_bias" not in measurement["auto_parameter_result"]["metadata"]
    assert restored_measurement["guided_effective_settings_hash"] == measurement["guided_effective_settings_hash"]
    assert restored_measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["analysis_metadata"]["assisted_threshold_bias"] == 0
    assert restored_measurement["guided_slider_values"] == measurement["guided_slider_values"]


def test_b2c2_assisted_small_region_filter_bias_zero_preserves_b2c1_baseline_and_persists_metadata(tmp_path) -> None:
    image_path = _write_synthetic_muscle_like_he_png(tmp_path / "b2c2_assisted_filter_zero.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedZero")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high"},
    )
    measurement = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={
            "sensitivity": "low",
            "cleanup_level": "high",
            "threshold_bias": 0,
            "small_region_filter_bias": 0,
        },
    )
    restored = BrightfieldPanel.from_dict(panel.to_dict())
    restored_measurement = restored.analysis["brightfield_measurements"][-1]

    assert measurement["params"] == baseline["params"]
    assert measurement["results"] == baseline["results"]
    assert measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["analysis_metadata"]["assisted_small_region_filter_bias"] == 0
    assert measurement["analysis_metadata"]["assisted_small_region_filter_mapping_version"] == (
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
    )
    assert measurement["analysis_metadata"]["assisted_baseline_min_area_px"] == baseline["params"]["min_object_area_px"]
    assert measurement["analysis_metadata"]["assisted_effective_min_area_px"] == baseline["params"]["min_object_area_px"]
    assert measurement["auto_parameter_result"]["metadata"]["assisted_small_region_filter_bias"] == 0
    assert measurement["auto_parameter_result"]["metadata"]["assisted_small_region_filter_mapping_version"] == (
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
    )
    assert measurement["guided_slider_values"]["small_region_filter_bias"] == 0
    assert measurement["guided_slider_values"]["small_region_filter_mapping_version"] == (
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
    )
    assert measurement["guided_slider_values"]["small_region_filter_baseline_min_area_px"] == baseline["params"]["min_object_area_px"]
    assert measurement["guided_slider_values"]["small_region_filter_effective_min_area_px"] == baseline["params"]["min_object_area_px"]
    assert restored_measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert restored_measurement["analysis_metadata"]["assisted_small_region_filter_bias"] == 0
    assert restored_measurement["analysis_metadata"]["assisted_small_region_filter_mapping_version"] == (
        BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION
    )
    assert restored_measurement["guided_slider_values"] == measurement["guided_slider_values"]
    assert restored.data["guided_state"]["assisted_small_region_filter_bias"] == 0


def test_b2b_foolproof_threshold_bias_zero_preserves_b2a_output(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "b2b_bias_zero.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2BZero")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof")
    zero_bias = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="foolproof",
        settings={"threshold_bias": 0},
    )

    assert zero_bias["params"] == baseline["params"]
    assert zero_bias["results"] == baseline["results"]
    assert zero_bias["guided_mapping_version"] == BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION
    assert zero_bias["guided_slider_values"]["threshold_bias"] == 0
    assert zero_bias["guided_slider_values"]["threshold_bias_active"] is True


def test_b2b_foolproof_threshold_bias_formula_is_exact_and_versioned() -> None:
    applied = apply_brightfield_foolproof_threshold_bias(20.0, 2)

    assert BRIGHTFIELD_FOOLPROOF_SENSITIVITY_MAPPING_VERSION == "v7.2-b2b-foolproof-sensitivity-1"
    assert applied == {
        "threshold_bias": 2,
        "baseline_a_min": 20.0,
        "delta_a_min": -2.0,
        "adjusted_a_min": 18.0,
    }


def test_b2c1_assisted_threshold_bias_formula_is_exact_and_versioned() -> None:
    applied = apply_brightfield_assisted_threshold_bias(20.0, 2)

    assert BRIGHTFIELD_ASSISTED_SENSITIVITY_MAPPING_VERSION == "v7.2-b2c-assisted-sensitivity-1"
    assert applied == {
        "threshold_bias": 2,
        "baseline_a_min": 20.0,
        "delta_a_min": -2.0,
        "adjusted_a_min": 18.0,
    }


def test_b2c2_assisted_small_region_filter_formula_is_exact_and_versioned() -> None:
    applied = apply_brightfield_assisted_small_region_filter_bias(8, 2)

    assert BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION == "v7.2-b2c2-assisted-min-area-filtering-1"
    assert applied == {
        "small_region_filter_bias": 2,
        "baseline_min_area_px": 8,
        "min_area_scale_factor": 1.5,
        "effective_min_area_px": 12,
    }


def test_b2c2_assisted_small_region_filter_zero_baseline_stays_zero_for_all_biases() -> None:
    assert BRIGHTFIELD_ASSISTED_SMALL_REGION_FILTER_MAPPING_VERSION == "v7.2-b2c2-assisted-min-area-filtering-1"

    for bias, expected_scale in ((-2, 0.5), (0, 1.0), (2, 1.5)):
        applied = apply_brightfield_assisted_small_region_filter_bias(0, bias)
        assert applied == {
            "small_region_filter_bias": bias,
            "baseline_min_area_px": 0,
            "min_area_scale_factor": expected_scale,
            "effective_min_area_px": 0,
        }


def test_b2b_foolproof_threshold_bias_monotonic_target_mask_area_before_object_filter(tmp_path) -> None:
    image_path = _write_synthetic_muscle_like_he_png(tmp_path / "b2b_bias_monotonic.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2BMonotonic")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    low = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": -2})
    neutral = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": 0})
    high = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": 2})

    low_area = low["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    neutral_area = neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    high_area = high["analysis_metadata"]["target_mask_area_px_before_object_filter"]

    assert low_area <= neutral_area <= high_area
    assert low["params"]["a_min"] > neutral["params"]["a_min"] > high["params"]["a_min"]


def test_b2b_foolproof_threshold_edge_fixture_changes_target_mask_area(tmp_path) -> None:
    image_path = _write_synthetic_threshold_edge_he_png(tmp_path / "b2b_threshold_edge.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2BThresholdEdge")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof")
    low = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": -2})
    neutral = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": 0})
    high = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": 2})

    low_area = low["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    neutral_area = neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    high_area = high["analysis_metadata"]["target_mask_area_px_before_object_filter"]

    assert low["params"]["a_min"] > neutral["params"]["a_min"] > high["params"]["a_min"]
    assert low_area <= neutral_area <= high_area
    assert low_area < neutral_area or neutral_area < high_area
    assert neutral["results"] == baseline["results"]


def test_b2c1_assisted_threshold_edge_fixture_changes_target_mask_area(tmp_path) -> None:
    image_path = _write_synthetic_threshold_edge_he_png(tmp_path / "b2c1_assisted_threshold_edge.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C1AssistedThresholdEdge")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high"},
    )
    low = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": -2},
    )
    neutral = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 0},
    )
    high = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 2},
    )

    low_area = low["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    neutral_area = neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    high_area = high["analysis_metadata"]["target_mask_area_px_before_object_filter"]

    assert neutral["results"] == baseline["results"]
    assert neutral["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert low["params"]["a_min"] > neutral["params"]["a_min"] > high["params"]["a_min"]
    assert low_area <= neutral_area <= high_area
    assert low_area < neutral_area or neutral_area < high_area


def test_b2c1_assisted_batch_lock_disables_threshold_bias_and_records_inactive_metadata(tmp_path) -> None:
    first_path = _write_synthetic_pink_he_png(tmp_path / "b2c1_assisted_batch_first.png")
    second_path = _write_synthetic_pink_he_png(tmp_path / "b2c1_assisted_batch_second.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C1AssistedBatchInactive")
    first = panel.import_image(first_path, group="Batch", stain_type="H&E")
    second = panel.import_image(second_path, group="Batch", stain_type="H&E")

    first_measurement = panel.auto_analyze_he(
        image_uid=first["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 2},
    )
    panel.set_active_image(first["uid"])
    panel.apply_batch_threshold_from_measurement(first_measurement["measurement_uuid"], lock=True)

    locked = panel.auto_analyze_he(
        image_uid=second["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": -2},
    )

    assert locked["threshold_source"] == "batch_locked"
    assert locked["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert locked["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert locked["guided_slider_values"]["threshold_bias"] == -2
    assert locked["guided_slider_values"]["threshold_bias_active"] is False
    assert locked["guided_slider_values"]["threshold_bias_inactive_reason"] == "batch_threshold_locked"
    assert locked["analysis_metadata"]["assisted_threshold_bias"] == -2
    assert locked["analysis_metadata"]["assisted_threshold_bias_active"] is False
    assert locked["analysis_metadata"]["assisted_threshold_bias_inactive_reason"] == "batch_threshold_locked"


def test_b2c1_assisted_guided_preview_is_transient_and_final_hash_matches_when_preview_is_fresh(tmp_path) -> None:
    image_path = _write_synthetic_threshold_edge_he_png(tmp_path / "b2c1_assisted_preview_hash.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C1AssistedPreview")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    preview = panel.build_guided_preview(
        image_uid=imported["uid"],
        guided_mode="assisted",
        stain_type="H&E",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 1},
    )

    assert panel.analysis["brightfield_measurements"] == []
    assert panel.analysis["brightfield_ai_object_count_measurements"] == []
    assert preview["guided_metadata"]["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert preview["guided_metadata"]["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION

    measurement = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 1},
    )

    assert measurement["guided_mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["guided_effective_settings"]["mapping_version"] == BRIGHTFIELD_ASSISTED_COMBINED_MAPPING_VERSION
    assert measurement["guided_effective_settings_hash"] == preview["preview_hash"]
    assert measurement["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS
    assert measurement["results"] == preview["analysis"]["results"]


def test_b2c2_assisted_min_area_edge_fixture_keeps_threshold_invariant_and_changes_retained_regions(tmp_path) -> None:
    image_path = _write_synthetic_min_area_edge_he_png(tmp_path / "b2c2_assisted_min_area_edge.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedMinAreaEdge")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high"},
    )
    low = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 0, "small_region_filter_bias": -2},
    )
    neutral = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 0, "small_region_filter_bias": 0},
    )
    high = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 0, "small_region_filter_bias": 2},
    )

    assert neutral["results"] == baseline["results"]
    assert low["params"]["a_min"] == pytest.approx(neutral["params"]["a_min"])
    assert high["params"]["a_min"] == pytest.approx(neutral["params"]["a_min"])
    assert low["params"]["cleanup_min_area_px"] == neutral["params"]["cleanup_min_area_px"] == high["params"]["cleanup_min_area_px"] == 0
    assert [low["params"]["min_object_area_px"], neutral["params"]["min_object_area_px"], high["params"]["min_object_area_px"]] == [4, 8, 12]
    assert low["analysis_metadata"]["target_mask_area_px_before_object_filter"] == neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    assert high["analysis_metadata"]["target_mask_area_px_before_object_filter"] == neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    assert low["analysis_metadata"]["assisted_effective_min_area_px"] < neutral["analysis_metadata"]["assisted_effective_min_area_px"] < high["analysis_metadata"]["assisted_effective_min_area_px"]
    assert [low["analysis_metadata"]["assisted_effective_min_area_px"], neutral["analysis_metadata"]["assisted_effective_min_area_px"], high["analysis_metadata"]["assisted_effective_min_area_px"]] == [4, 8, 12]
    assert [low["results"]["object_count"], neutral["results"]["object_count"], high["results"]["object_count"]] == [3, 2, 1]
    assert low["analysis_metadata"]["final_mask_area_px"] > neutral["analysis_metadata"]["final_mask_area_px"] > high["analysis_metadata"]["final_mask_area_px"]
    assert low["results"]["mean_object_area_px"] < neutral["results"]["mean_object_area_px"] < high["results"]["mean_object_area_px"]


def test_b2c2_assisted_batch_lock_keeps_threshold_fixed_while_small_region_filter_remains_active(tmp_path) -> None:
    first_path = _write_synthetic_min_area_edge_he_png(tmp_path / "b2c2_assisted_batch_first.png")
    second_path = _write_synthetic_min_area_edge_he_png(tmp_path / "b2c2_assisted_batch_second.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedBatchLock")
    first = panel.import_image(first_path, group="Batch", stain_type="H&E")
    second = panel.import_image(second_path, group="Batch", stain_type="H&E")

    first_measurement = panel.auto_analyze_he(
        image_uid=first["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 2, "small_region_filter_bias": 0},
    )
    panel.set_active_image(first["uid"])
    panel.apply_batch_threshold_from_measurement(first_measurement["measurement_uuid"], lock=True)

    low = panel.auto_analyze_he(
        image_uid=second["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": -2, "small_region_filter_bias": -2},
    )
    high = panel.auto_analyze_he(
        image_uid=second["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": -2, "small_region_filter_bias": 2},
    )

    assert low["threshold_source"] == "batch_locked"
    assert high["threshold_source"] == "batch_locked"
    assert low["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert high["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert low["params"]["cleanup_min_area_px"] == high["params"]["cleanup_min_area_px"] == 0
    assert [low["params"]["min_object_area_px"], high["params"]["min_object_area_px"]] == [4, 12]
    assert low["analysis_metadata"]["target_mask_area_px_before_object_filter"] == high["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    assert low["analysis_metadata"]["assisted_threshold_bias_active"] is False
    assert high["analysis_metadata"]["assisted_threshold_bias_active"] is False
    assert low["analysis_metadata"]["assisted_threshold_bias_inactive_reason"] == "batch_threshold_locked"
    assert high["analysis_metadata"]["assisted_threshold_bias_inactive_reason"] == "batch_threshold_locked"
    assert low["analysis_metadata"]["assisted_effective_min_area_px"] < high["analysis_metadata"]["assisted_effective_min_area_px"]
    assert low["results"]["object_count"] > high["results"]["object_count"]


def test_b2c2_assisted_zero_baseline_small_region_filter_biases_remain_no_filter(tmp_path) -> None:
    image_path = _write_synthetic_zero_baseline_min_area_he_png(tmp_path / "b2c2_assisted_zero_baseline.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedZeroBaseline")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    baseline = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "off"},
    )
    low = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "off", "threshold_bias": 0, "small_region_filter_bias": -2},
    )
    neutral = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "off", "threshold_bias": 0, "small_region_filter_bias": 0},
    )
    high = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "off", "threshold_bias": 0, "small_region_filter_bias": 2},
    )

    assert baseline["params"]["cleanup_min_area_px"] == 0
    assert baseline["params"]["min_object_area_px"] == 0
    assert [low["analysis_metadata"]["assisted_effective_min_area_px"], neutral["analysis_metadata"]["assisted_effective_min_area_px"], high["analysis_metadata"]["assisted_effective_min_area_px"]] == [0, 0, 0]
    assert [low["params"]["cleanup_min_area_px"], neutral["params"]["cleanup_min_area_px"], high["params"]["cleanup_min_area_px"]] == [0, 0, 0]
    assert [low["params"]["min_object_area_px"], neutral["params"]["min_object_area_px"], high["params"]["min_object_area_px"]] == [0, 0, 0]
    assert low["params"]["a_min"] == pytest.approx(neutral["params"]["a_min"])
    assert high["params"]["a_min"] == pytest.approx(neutral["params"]["a_min"])
    assert low["analysis_metadata"]["target_mask_area_px_before_object_filter"] == neutral["analysis_metadata"]["target_mask_area_px_before_object_filter"] == high["analysis_metadata"]["target_mask_area_px_before_object_filter"]
    assert low["results"] == baseline["results"] == neutral["results"] == high["results"]


def test_b2c2_assisted_guided_preview_hash_tracks_small_region_filter_bias_and_stays_transient(tmp_path) -> None:
    image_path = _write_synthetic_min_area_edge_he_png(tmp_path / "b2c2_assisted_preview_hash.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedPreview")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    preview = panel.build_guided_preview(
        image_uid=imported["uid"],
        guided_mode="assisted",
        stain_type="H&E",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 1, "small_region_filter_bias": 2},
    )

    assert panel.analysis["brightfield_measurements"] == []
    assert preview["guided_metadata"]["guided_slider_values"]["small_region_filter_bias"] == 2

    matched = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 1, "small_region_filter_bias": 2},
    )
    stale = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high", "threshold_bias": 1, "small_region_filter_bias": -2},
    )

    assert matched["guided_effective_settings_hash"] == preview["preview_hash"]
    assert matched["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS
    assert matched["results"] == preview["analysis"]["results"]
    assert stale["guided_effective_settings_hash"] != preview["preview_hash"]
    assert stale["guided_preview_status"] != BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS


def test_b2c2_assisted_small_region_filter_metadata_is_isolated_from_foolproof_manual_and_ai(tmp_path) -> None:
    image_path = _write_synthetic_min_area_edge_he_png(tmp_path / "b2c2_assisted_isolation.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2C2AssistedIsolation")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    foolproof = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof", settings={"threshold_bias": 1})
    manual = panel.create_measurement(image_uid=imported["uid"], settings=_settings())
    panel.calibrate_he_fiber_area(image_uid=imported["uid"])
    ai = panel.create_ai_object_count_measurement(image_uid=imported["uid"])

    for key in (
        "assisted_small_region_filter_bias",
        "assisted_small_region_filter_mapping_version",
        "assisted_baseline_min_area_px",
        "assisted_effective_min_area_px",
    ):
        assert key not in foolproof["analysis_metadata"]
        assert key not in foolproof["auto_parameter_result"]["metadata"]
        assert key not in manual["analysis_metadata"]
        assert key not in ai["analysis_metadata"]


def test_profile_preset_measurement_for_oil_red_o_marks_result_warn_with_experimental_metadata(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "synthetic_oil_red_o.png")
    panel = BrightfieldPanel(panel_id="Figure1BFOilRedOProfile")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Oil Red O")

    measurement = panel.create_profile_preset_measurement(image_uid=imported["uid"], guided_mode="foolproof")

    assert measurement["guided_mode"] == "foolproof"
    assert measurement["task_profile"]["task_id"] == "brightfield_oil_red_o_area"
    assert measurement["threshold_source"] == "profile_preset"
    assert measurement["stain_type"] == "Oil Red O"
    assert measurement["analysis_metadata"]["task_id"] == "brightfield_oil_red_o_area"
    assert measurement["analysis_metadata"]["analysis_mode"] == "profile_guided_foolproof"
    assert measurement["analysis_metadata"]["profile_id"] == "brightfield_oil_red_o_area"
    assert measurement["analysis_metadata"]["threshold_source"] == "profile_preset"
    assert measurement["analysis_metadata"]["profile_version"] == "0.1-experimental"
    assert measurement["analysis_metadata"]["validation_status"] == "synthetic_test_only"
    assert measurement["analysis_metadata"]["overlay_review_required"] is True
    assert measurement["guided_mapping_version"] == BRIGHTFIELD_GUIDED_MAPPING_VERSION
    assert measurement["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_STATUS
    assert measurement["guided_baseline_source"] == "profile_preset"
    _assert_neutral_guided_slider_values(measurement["guided_slider_values"])
    assert measurement["guided_effective_settings_hash"] == sha256_json(measurement["guided_effective_settings"])
    assert measurement["guided_effective_settings"]["guided_mode"] == "foolproof"
    assert measurement["guided_effective_settings"]["threshold_source"] == "profile_preset"
    assert measurement["reliability_status"] == "WARN"
    assert measurement["include_in_statistics"] is True
    assert any("experimental" in warning.lower() for warning in measurement["warnings"])


def test_b2a_profile_preset_hash_ignores_unrelated_he_batch_lock_state(tmp_path) -> None:
    he_path = _write_synthetic_pink_he_png(tmp_path / "profile_lock_he.png")
    oil_path = _write_synthetic_brightfield_png(tmp_path / "profile_lock_oil.png")
    panel = BrightfieldPanel(panel_id="Figure1BFProfileLockIsolation")
    he_image = panel.import_image(he_path, group="Tumor", stain_type="H&E")
    oil_image = panel.import_image(oil_path, group="Tumor", stain_type="Oil Red O")

    preview_before = panel.build_guided_preview(
        image_uid=oil_image["uid"],
        guided_mode="foolproof",
        stain_type="Oil Red O",
    )
    measurement_before = panel.create_profile_preset_measurement(
        image_uid=oil_image["uid"],
        guided_mode="foolproof",
    )

    he_measurement = panel.auto_analyze_he(image_uid=he_image["uid"], guided_mode="foolproof")
    panel.set_active_image(he_image["uid"])
    panel.apply_batch_threshold_from_measurement(he_measurement["measurement_uuid"], lock=True)

    preview_after = panel.build_guided_preview(
        image_uid=oil_image["uid"],
        guided_mode="foolproof",
        stain_type="Oil Red O",
    )
    measurement_after = panel.create_profile_preset_measurement(
        image_uid=oil_image["uid"],
        guided_mode="foolproof",
    )

    assert preview_before["preview_hash"] == preview_after["preview_hash"]
    assert measurement_before["guided_effective_settings_hash"] == measurement_after["guided_effective_settings_hash"]
    assert measurement_after["guided_effective_settings_hash"] == preview_after["preview_hash"]
    assert measurement_after["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS


def test_profile_preset_measurement_records_combo_stain_type_not_image_metadata(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "profile_stain_sync.png")
    panel = BrightfieldPanel(panel_id="Figure1BFStainSync")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    measurement = panel.create_profile_preset_measurement(
        image_uid=imported["uid"],
        stain_type="Oil Red O",
        guided_mode="foolproof",
    )

    assert measurement["stain_type"] == "Oil Red O"
    assert measurement["analysis_metadata"]["stain_type"] == "Oil Red O"
    assert measurement["task_profile"]["task_id"] == "brightfield_oil_red_o_area"
    assert panel.images()[0]["stain_type"] == "H&E"


def test_profile_preset_measurement_records_analysis_mode_and_profile_id(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "analysis_mode_test.png")
    panel = BrightfieldPanel(panel_id="Figure1BFAnalysisMode")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Crystal violet")

    measurement = panel.create_profile_preset_measurement(image_uid=imported["uid"], guided_mode="foolproof")

    metadata = measurement["analysis_metadata"]
    assert metadata["analysis_mode"] == "profile_guided_foolproof"
    assert metadata["profile_id"] == "brightfield_crystal_violet_area"
    _assert_b1_semantics(
        measurement,
        analysis_mode="profile_guided_foolproof",
        guided_mode="foolproof",
        threshold_source="profile_preset",
        segmentation_mode="hsv",
        tissue_mask_enabled=False,
    )


def test_expert_measurement_metadata_includes_traceability_fields(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "expert_traceability.png")
    panel = BrightfieldPanel(panel_id="Figure1BFExpertTrace")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Crystal violet")

    measurement = panel.create_measurement(image_uid=imported["uid"], settings=_settings())

    metadata = measurement["analysis_metadata"]
    assert metadata["stain_type"] == "Crystal violet"
    assert metadata["profile_id"] == "brightfield_crystal_violet_area"
    assert metadata["roi_mode"] == "whole_image"
    assert metadata["measurement_uuid"] == measurement["measurement_uuid"]
    assert metadata["image_uid"] == imported["uid"]
    assert "guided_slider_values" not in measurement
    assert "guided_effective_settings" not in measurement
    assert "guided_effective_settings_hash" not in measurement
    _assert_b1_semantics(
        measurement,
        analysis_mode="expert",
        guided_mode="expert",
        threshold_source="manual",
        segmentation_mode="hsv",
        tissue_mask_enabled=False,
    )


def test_brightfield_batch_threshold_lock_unlock_updates_guided_state_and_audit(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "batch_state_he.png")
    panel = BrightfieldPanel(panel_id="Figure1BFBatchState")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")
    measurement = panel.auto_analyze_he(image_uid=imported["uid"])

    locked = panel.apply_batch_threshold_from_measurement(measurement["measurement_uuid"], lock=True)
    unlocked = panel.unlock_batch_threshold()

    assert locked["locked"] is True
    assert unlocked["locked"] is False
    assert panel.data["guided_state"]["batch_threshold_state"]["batch_threshold"] is None
    assert panel.data["guided_state"]["batch_threshold_state"]["batch_threshold_source_image"] is None
    assert panel.data["guided_state"]["batch_threshold_state"]["batch_threshold_method"] is None
    assert [entry.action for entry in panel.audit_log if "brightfield_batch_threshold" in entry.action] == [
        "brightfield_batch_threshold_lock",
        "brightfield_batch_threshold_unlock",
    ]
    unlock_entry = [entry for entry in panel.audit_log if entry.action == "brightfield_batch_threshold_unlock"][0]
    assert f"a_min={measurement['params']['a_min']:.3f}" in unlock_entry.detail
    assert f"image_uid={imported['uid']}" in unlock_entry.detail
    assert "method=lab_eosin_quantiles" in unlock_entry.detail


def test_auto_analyze_he_reuses_locked_batch_threshold_for_later_images(tmp_path) -> None:
    first_path = _write_synthetic_pink_he_png(tmp_path / "batch_first_he.png")
    second_path = _write_synthetic_pink_he_png(tmp_path / "batch_second_he.png")

    second_image = QImage(str(second_path))
    second_image.setPixelColor(4, 4, QColor(220, 145, 170))
    assert second_image.save(str(second_path))

    panel = BrightfieldPanel(panel_id="Figure1BFBatchLock")
    first = panel.import_image(first_path, group="Batch", stain_type="H&E")
    second = panel.import_image(second_path, group="Batch", stain_type="H&E")

    first_measurement = panel.auto_analyze_he(image_uid=first["uid"], guided_mode="assisted")
    panel.set_active_image(first["uid"])
    batch_state = panel.apply_batch_threshold_from_measurement(first_measurement["measurement_uuid"], lock=True)
    second_measurement = panel.auto_analyze_he(image_uid=second["uid"])

    assert panel.data["guided_state"]["batch_threshold_state"]["locked"] is True
    assert panel.data["guided_state"]["batch_threshold_state"]["batch_threshold"] == pytest.approx(first_measurement["params"]["a_min"])
    assert batch_state["batch_lock_source_mode"] == "assisted"
    assert second_measurement["threshold_source"] == "batch_locked"
    assert second_measurement["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert second_measurement["analysis_metadata"]["batch_lock_applied"] is True
    assert second_measurement["analysis_metadata"]["batch_threshold_source_image"] == first["uid"]
    assert second_measurement["analysis_metadata"]["batch_threshold_method"] == first_measurement["analysis_metadata"]["threshold_method"]
    assert second_measurement["analysis_metadata"]["batch_lock_source_mode"] == "assisted"
    _assert_b1_semantics(
        first_measurement,
        analysis_mode="guided_assisted",
        guided_mode="assisted",
        threshold_source="image_auto",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )
    _assert_b1_semantics(
        second_measurement,
        analysis_mode="guided_foolproof",
        guided_mode="foolproof",
        threshold_source="batch_locked",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )
    apply_audits = [entry for entry in panel.audit_log if entry.action == "brightfield_batch_threshold_apply"]
    assert len(apply_audits) >= 1
    _assert_step2_measurement_fields(
        second_measurement,
        threshold_source="batch_locked",
        task_id="brightfield_he_eosin_area",
    )

    restored = BrightfieldPanel.from_dict(panel.to_dict())
    restored_measurement = restored.analysis["brightfield_measurements"][-1]
    assert restored_measurement["analysis_metadata"]["batch_lock_applied"] is True
    assert restored_measurement["analysis_metadata"]["batch_threshold_source_image"] == first["uid"]
    assert restored_measurement["analysis_metadata"]["batch_threshold_method"] == first_measurement["analysis_metadata"]["threshold_method"]
    assert restored_measurement["analysis_metadata"]["batch_lock_source_mode"] == "assisted"
    _assert_b1_semantics(
        restored_measurement,
        analysis_mode="guided_foolproof",
        guided_mode="foolproof",
        threshold_source="batch_locked",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )


def test_b2b_batch_lock_disables_foolproof_threshold_bias_and_records_inactive_metadata(tmp_path) -> None:
    first_path = _write_synthetic_pink_he_png(tmp_path / "b2b_batch_bias_first.png")
    second_path = _write_synthetic_pink_he_png(tmp_path / "b2b_batch_bias_second.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2BBatchInactive")
    first = panel.import_image(first_path, group="Batch", stain_type="H&E")
    second = panel.import_image(second_path, group="Batch", stain_type="H&E")

    first_measurement = panel.auto_analyze_he(
        image_uid=first["uid"],
        guided_mode="foolproof",
        settings={"threshold_bias": 2},
    )
    panel.set_active_image(first["uid"])
    panel.apply_batch_threshold_from_measurement(first_measurement["measurement_uuid"], lock=True)

    locked = panel.auto_analyze_he(
        image_uid=second["uid"],
        guided_mode="foolproof",
        settings={"threshold_bias": 2},
    )

    assert locked["threshold_source"] == "batch_locked"
    assert locked["params"]["a_min"] == pytest.approx(first_measurement["params"]["a_min"])
    assert locked["guided_slider_values"]["threshold_bias"] == 2
    assert locked["guided_slider_values"]["threshold_bias_active"] is False
    assert locked["guided_slider_values"]["threshold_bias_inactive_reason"] == "batch_threshold_locked"
    assert locked["analysis_metadata"]["foolproof_threshold_bias_active"] is False
    assert locked["analysis_metadata"]["foolproof_threshold_bias_inactive_reason"] == "batch_threshold_locked"


def test_b2b_guided_preview_is_transient_and_final_hash_matches_when_preview_is_fresh(tmp_path) -> None:
    image_path = _write_synthetic_pink_he_png(tmp_path / "b2b_preview_hash.png")
    panel = BrightfieldPanel(panel_id="Figure1BFB2BPreview")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    preview = panel.build_guided_preview(
        image_uid=imported["uid"],
        guided_mode="foolproof",
        stain_type="H&E",
        settings={"threshold_bias": 1},
    )

    assert panel.analysis["brightfield_measurements"] == []
    assert panel.analysis["brightfield_ai_object_count_measurements"] == []
    assert preview["analysis"]["results"]["stained_area_px"] > 0

    measurement = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="foolproof",
        settings={"threshold_bias": 1},
    )

    assert measurement["guided_effective_settings_hash"] == preview["preview_hash"]
    assert measurement["guided_preview_status"] == BRIGHTFIELD_GUIDED_PREVIEW_MATCH_STATUS
    assert measurement["results"] == preview["analysis"]["results"]


def test_brightfield_batch_threshold_rejects_incompatible_measurement(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "batch_incompatible.png")
    panel = BrightfieldPanel(panel_id="Figure1BFBatchReject")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Oil Red O")
    measurement = panel.create_measurement(image_uid=imported["uid"], settings=_settings())

    with pytest.raises(ValueError, match="H&E LAB measurement"):
        panel.apply_batch_threshold_from_measurement(measurement["measurement_uuid"], lock=True)


def test_brightfield_batch_locked_apply_writes_apply_audit(tmp_path) -> None:
    first_path = _write_synthetic_pink_he_png(tmp_path / "apply_audit_first.png")
    second_path = _write_synthetic_pink_he_png(tmp_path / "apply_audit_second.png")

    panel = BrightfieldPanel(panel_id="Figure1BFApplyAudit")
    first = panel.import_image(first_path, group="Batch", stain_type="H&E")
    second = panel.import_image(second_path, group="Batch", stain_type="H&E")

    first_measurement = panel.auto_analyze_he(image_uid=first["uid"])
    panel.set_active_image(first["uid"])
    panel.apply_batch_threshold_from_measurement(first_measurement["measurement_uuid"], lock=True)

    audit_before = [entry.action for entry in panel.audit_log]
    assert "brightfield_batch_threshold_apply" not in audit_before

    second_measurement = panel.auto_analyze_he(image_uid=second["uid"])

    assert second_measurement["threshold_source"] == "batch_locked"

    apply_entries = [entry for entry in panel.audit_log if entry.action == "brightfield_batch_threshold_apply"]
    assert len(apply_entries) == 1
    detail = apply_entries[0].detail
    assert "measurement_uuid=" in detail
    assert second["uid"] in detail
    assert "source_image=" in detail
    assert "a_min=" in detail


def test_auto_analyze_he_uses_tissue_mask_to_avoid_full_field_false_positive_in_foolproof_and_assisted_modes(tmp_path) -> None:
    image_path = _write_synthetic_muscle_like_he_png(tmp_path / "he_tissue_mask_guard.png")
    panel = BrightfieldPanel(panel_id="Figure1BFHETissueMask")
    imported = panel.import_image(image_path, group="Tumor", stain_type="H&E")

    foolproof = panel.auto_analyze_he(image_uid=imported["uid"], guided_mode="foolproof")
    assisted = panel.auto_analyze_he(
        image_uid=imported["uid"],
        guided_mode="assisted",
        settings={"sensitivity": "low", "cleanup_level": "high"},
    )

    for measurement in (foolproof, assisted):
        metadata = measurement["analysis_metadata"]
        assert measurement["results"]["area_fraction_percent"] < 95.0
        assert measurement["results"]["object_count"] > 1
        assert measurement["params"]["tissue_mask_enabled"] is True
        assert metadata["stain_type"] == "H&E"
        assert metadata["profile_id"] == "brightfield_he_eosin_area"
        assert metadata["threshold_method"] == "lab_eosin_quantiles"
    _assert_b1_semantics(
        foolproof,
        analysis_mode="guided_foolproof",
        guided_mode="foolproof",
        threshold_source="image_auto",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )
    _assert_b1_semantics(
        assisted,
        analysis_mode="guided_assisted",
        guided_mode="assisted",
        threshold_source="image_auto",
        segmentation_mode="lab",
        tissue_mask_enabled=True,
    )


def test_brightfield_other_stain_warns_about_special_fallback(tmp_path) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "other_fallback.png")
    panel = BrightfieldPanel(panel_id="Figure1BFOtherFallback")
    imported = panel.import_image(image_path, group="Tumor", stain_type="Other high-contrast stain")

    measurement = panel.create_profile_preset_measurement(image_uid=imported["uid"], guided_mode="foolproof")

    assert measurement["reliability_status"] == "WARN"
    warnings_text = " ".join(measurement["warnings"]).lower()
    assert "other" in warnings_text and "fallback" in warnings_text
    assert "special stain" in warnings_text


def test_brightfield_default_guided_state_normalizes_guided_to_foolproof() -> None:
    guided_state = BrightfieldPanel(panel_id="Figure1BFGuidedState").data["guided_state"]

    assert guided_state["guided_mode"] == "foolproof"


def test_brightfield_guided_state_normalization_does_not_change_modified_at() -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFGuidedCompat")
    original_modified_at = panel.modified_at
    original_audit_count = len(panel.audit_log)

    merged = panel._merge_guided_state(
        {
            "guided_mode": "guided",
            "task_profile": {"task_id": "brightfield_he_eosin_area"},
        }
    )

    assert merged["guided_mode"] == "foolproof"
    assert panel.modified_at == original_modified_at
    assert len(panel.audit_log) == original_audit_count


def test_legacy_brightfield_manual_measurement_load_infers_explicit_b1_semantics() -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFLegacyManual")
    payload = panel.to_dict()
    payload["data"]["brightfield_images"] = [
        {
            "uid": "legacy-image",
            "source_name": "legacy.png",
            "group": "Tumor",
            "stain_type": "Crystal violet",
            "measurements": [],
        }
    ]
    legacy_measurement = {
        "measurement_uuid": "legacy-manual",
        "image_uid": "legacy-image",
        "image_name": "legacy.png",
        "group": "Tumor",
        "stain_type": "Crystal violet",
        "type": "brightfield_measurement",
        "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None},
        "params": {"segmentation_mode": "hsv"},
        "results": {
            "segmentation_mode": "hsv",
            "stained_area_px": 9,
            "roi_area_px": 36,
            "area_fraction": 0.25,
            "area_fraction_percent": 25.0,
            "object_count": 1,
            "mean_object_area_px": 9.0,
        },
        "model_name": "brightfield_hsv_manual",
        "analysis_metadata": {},
    }
    payload["analysis"]["brightfield_measurements"] = [legacy_measurement]
    payload["data"]["brightfield_images"][0]["measurements"] = [dict(legacy_measurement)]

    restored = BrightfieldPanel.from_dict(payload)
    measurement = restored.analysis["brightfield_measurements"][0]

    _assert_b1_semantics(
        measurement,
        analysis_mode="expert",
        guided_mode="expert",
        threshold_source="manual",
        segmentation_mode="hsv",
        tissue_mask_enabled=BRIGHTFIELD_LEGACY_UNKNOWN,
    )
    assert "guided_slider_values" not in measurement
    assert "guided_effective_settings" not in measurement
    assert "guided_effective_settings_hash" not in measurement
    assert measurement["results"]["object_count"] == 1
    assert measurement["results"]["area_fraction_percent"] == pytest.approx(25.0)


def test_legacy_brightfield_ambiguous_measurement_load_uses_legacy_unknown_only_for_unknown_route_fields() -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFLegacyAmbiguous")
    payload = panel.to_dict()
    payload["data"]["brightfield_images"] = [
        {
            "uid": "legacy-auto-image",
            "source_name": "legacy_auto.png",
            "group": "Tumor",
            "stain_type": "H&E",
            "measurements": [],
        }
    ]
    legacy_measurement = {
        "measurement_uuid": "legacy-auto",
        "image_uid": "legacy-auto-image",
        "image_name": "legacy_auto.png",
        "group": "Tumor",
        "stain_type": "H&E",
        "type": "brightfield_measurement",
        "roi": {"mode": "whole_image", "items": [], "active_item_uuid": None},
        "params": {"segmentation_mode": "lab"},
        "results": {
            "segmentation_mode": "lab",
            "stained_area_px": 12,
            "roi_area_px": 50,
            "area_fraction": 0.24,
            "area_fraction_percent": 24.0,
            "object_count": 3,
            "mean_object_area_px": 4.0,
        },
        "analysis_metadata": {"task_id": "brightfield_he_eosin_area"},
    }
    payload["analysis"]["brightfield_measurements"] = [legacy_measurement]
    payload["data"]["brightfield_images"][0]["measurements"] = [dict(legacy_measurement)]

    restored = BrightfieldPanel.from_dict(payload)
    measurement = restored.analysis["brightfield_measurements"][0]
    metadata = measurement["analysis_metadata"]

    assert measurement["threshold_source"] == BRIGHTFIELD_LEGACY_UNKNOWN
    assert measurement["guided_mode"] == BRIGHTFIELD_LEGACY_UNKNOWN
    assert metadata["analysis_mode"] == BRIGHTFIELD_LEGACY_UNKNOWN
    assert metadata["segmentation_mode"] == "lab"
    assert metadata["area_fraction_denominator"] == BRIGHTFIELD_AREA_FRACTION_DENOMINATOR
    assert metadata["object_count_semantics"] == BRIGHTFIELD_OBJECT_COUNT_SEMANTICS
    assert metadata["tissue_mask_enabled"] == BRIGHTFIELD_LEGACY_UNKNOWN
    assert "guided_slider_values" not in measurement
    assert "guided_effective_settings" not in measurement
    assert "guided_effective_settings_hash" not in measurement
    assert measurement["results"]["object_count"] == 3
    assert measurement["results"]["area_fraction_percent"] == pytest.approx(24.0)


def test_b2a_guided_effective_settings_hash_is_deterministic_and_changes_with_context() -> None:
    base_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="image_auto",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-a", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 12.5,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={"mode": "whole_image", "items": [], "active_item_uuid": None},
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": False,
            "batch_threshold": None,
            "batch_threshold_source_image": None,
            "batch_threshold_method": None,
        },
    )
    same_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="image_auto",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-a", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 12.5,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={"mode": "whole_image", "items": [], "active_item_uuid": None},
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": False,
            "batch_threshold": None,
            "batch_threshold_source_image": None,
            "batch_threshold_method": None,
        },
    )
    roi_changed_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="image_auto",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-a", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 12.5,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={
            "mode": "selected_roi",
            "items": [{"roi_uuid": "roi-1", "type": "rectangle", "coordinates_px": {"x": 1, "y": 2, "w": 10, "h": 8}}],
            "active_item_uuid": "roi-1",
        },
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": False,
            "batch_threshold": None,
            "batch_threshold_source_image": None,
            "batch_threshold_method": None,
        },
    )
    batch_changed_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="batch_locked",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-a", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 12.5,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={"mode": "whole_image", "items": [], "active_item_uuid": None},
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
            "batch_lock_applied": True,
            "batch_threshold_source_image": "img-1",
            "batch_threshold_method": "lab_eosin_quantiles",
            "batch_lock_source_mode": "assisted",
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": True,
            "batch_threshold": 12.5,
            "batch_threshold_source_image": "img-1",
            "batch_threshold_method": "lab_eosin_quantiles",
            "batch_lock_source_mode": "assisted",
        },
    )
    settings_changed_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="image_auto",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-a", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 14.0,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={"mode": "whole_image", "items": [], "active_item_uuid": None},
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": False,
            "batch_threshold": None,
            "batch_threshold_source_image": None,
            "batch_threshold_method": None,
        },
    )
    image_changed_snapshot = build_brightfield_guided_effective_settings_snapshot(
        guided_mode="foolproof",
        task_profile={"task_id": "brightfield_he_eosin_area"},
        stain_type="H&E",
        threshold_source="image_auto",
        threshold_method="lab_eosin_quantiles",
        image_context={"image_uid": "img-b", "source_name": "same.png"},
        effective_settings={
            "segmentation_mode": "lab",
            "a_min": 12.5,
            "cleanup_min_area_px": 5,
            "tissue_mask_enabled": True,
            "tissue_mask_value_threshold": 0.92,
        },
        roi_snapshot={"mode": "whole_image", "items": [], "active_item_uuid": None},
        analysis_metadata={
            "tissue_mask_applied": True,
            "artifact_summary": {
                "artifact_fraction": 0.05,
                "artifact_types_detected": ["dark_debris"],
                "warnings": [],
            },
            "area_fraction_denominator": BRIGHTFIELD_AREA_FRACTION_DENOMINATOR,
        },
        batch_state={
            "task_id": "brightfield_he_eosin_area",
            "locked": False,
            "batch_threshold": None,
            "batch_threshold_source_image": None,
            "batch_threshold_method": None,
        },
    )

    assert base_snapshot == same_snapshot
    assert sha256_json(base_snapshot) == sha256_json(same_snapshot)
    assert base_snapshot["image_context"]["image_uid"] == "img-a"
    assert image_changed_snapshot["image_context"]["image_uid"] == "img-b"
    assert sha256_json(base_snapshot) != sha256_json(image_changed_snapshot)
    assert sha256_json(base_snapshot) != sha256_json(roi_changed_snapshot)
    assert sha256_json(base_snapshot) != sha256_json(batch_changed_snapshot)
    assert sha256_json(base_snapshot) != sha256_json(settings_changed_snapshot)


def test_create_measurement_overlay_failure_excludes_measurement_from_statistics(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    image_path = _write_synthetic_brightfield_png(tmp_path / "overlay_fail.png")
    panel = BrightfieldPanel(panel_id="Figure1BFFailGate")
    panel.import_image(image_path, group="Tumor")

    def _raise_overlay_error(*args, **kwargs):
        raise RuntimeError("overlay failed")

    monkeypatch.setattr("core.models.brightfield_panel.render_mask_overlay", _raise_overlay_error)

    measurement = panel.create_measurement(settings=_settings())

    assert measurement["overlay_generated"] is False
    assert measurement["reliability_status"] == "FAIL"
    assert measurement["reliability"]["include_in_statistics"] is False
    assert measurement["include_in_statistics"] is False
