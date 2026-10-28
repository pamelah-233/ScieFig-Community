from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile

from core.image_browser import ScaleBarState
from core.models.fluoro_panel import (
    AI_ASSISTED_MEASUREMENTS_KEY,
    FluorescencePanel,
    ImageMetadata,
    _legacy_scale_bar_spec,
)


def _write_cyx(path: Path) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def test_uncalibrated_visual_ruler_labels_source_pixels_not_um() -> None:
    image = {
        "metadata": {"width_px": 64, "height_px": 48},
        "measurement_scale_bar": {"calibration_source": "assumed", "assumed": True, "calibrated": False},
        "scale_bar": {"length_um": 50.0, "length_px": 40.0, "show_text": True},
    }
    spec = _legacy_scale_bar_spec(image)
    assert spec is not None
    assert spec["length_px"] == pytest.approx(40.0)
    assert spec["unit"] == "px"
    assert "um" not in spec["label"].lower()
    assert spec["label"].endswith("px")


def test_manual_display_override_beats_shared_image_calibration(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "override.tif"))
    uid = str(image["uid"])
    image["metadata"] = ImageMetadata.from_dict({**dict(image["metadata"]), "pixel_size_um": 0.5}).to_dict()
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, calibration_source="manual")
    scale_before = deepcopy(image.get("measurement_scale_bar", {}))
    history = {
        "image_uid": uid,
        "total_object_area_um2": 3.0,
        "object_table": [{"object_id": 1, "area_px": 4, "area_um2": 3.0, "contour_points_px": [[0, 0]]}],
    }
    image[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    pixels_before = np.array(panel.image_data(uid), copy=True)
    image["rois"] = [{"roi_id": "roi-keep", "type": "rectangle"}]

    panel.update_scale_bar(uid, length_um=50.0, manual_um_per_px=0.25, color="#ABCDEF")
    spec = _legacy_scale_bar_spec(image)
    assert spec["um_per_px"] == pytest.approx(0.25)
    assert spec["length_px"] == pytest.approx(50.0 / 0.25)
    assert spec["unit"] == "um"
    assert image["scale_bar"]["length_px"] == pytest.approx(200.0)
    assert image["scale_bar"]["color"] == "#ABCDEF"
    assert image["measurement_scale_bar"] == scale_before
    assert panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 3.0
    assert image[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["object_id"] == 1
    assert image["rois"][0]["roi_id"] == "roi-keep"
    assert np.array_equal(panel.image_data(uid), pixels_before)

    panel.update_scale_bar(uid, manual_um_per_px=None)
    spec = _legacy_scale_bar_spec(image)
    assert spec["um_per_px"] == pytest.approx(0.5)
    assert spec["length_px"] == pytest.approx(50.0 / 0.5)
    assert image["measurement_scale_bar"] == scale_before


def test_scalar_calibration_change_refreshes_derived_width(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "width.tif"))
    uid = str(image["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, calibration_source="manual")
    panel.update_scale_bar(uid, length_um=50.0)
    assert image["scale_bar"]["length_px"] == pytest.approx(100.0)
    history = {"image_uid": uid, "total_object_area_um2": 9.0, "object_table": [{"object_id": 4, "contour_points_px": [[1, 1]]}]}
    image[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    pixels_before = np.array(panel.image_data(uid), copy=True)

    panel.update_measurement_scale_bar(uid, um_per_px=0.25, calibration_source="manual")
    panel.update_scale_bar(uid, color="#FFFFFF")
    assert image["scale_bar"]["length_px"] == pytest.approx(200.0)
    assert _legacy_scale_bar_spec(image)["length_px"] == pytest.approx(200.0)
    assert panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 9.0
    assert image[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["object_id"] == 4
    assert np.array_equal(panel.image_data(uid), pixels_before)


def test_unknown_calibration_does_not_fabricate_one_um_per_px(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "unknown.tif"))
    uid = str(image["uid"])
    image["metadata"] = ImageMetadata.from_dict({k: v for k, v in dict(image["metadata"]).items() if k not in {"pixel_size_um", "pixel_size_x_um", "pixel_size_y_um"}}).to_dict()
    image["measurement_scale_bar"] = {"calibration_source": "assumed", "assumed": True, "calibrated": False, "um_per_px": 0.0}
    image["scale_bar"] = {"length_um": 50.0, "length_px": 18.0, "show_text": True}
    panel.update_scale_bar(uid, length_um=50.0)
    assert image["scale_bar"]["length_px"] == pytest.approx(18.0)
    spec = _legacy_scale_bar_spec(image)
    assert spec["unit"] == "px"
    assert spec["length_px"] == pytest.approx(18.0)
    assert "um" not in spec["label"].lower()


def test_browser_overlay_priority_skips_legacy_bar(tmp_path: Path, monkeypatch) -> None:
    legacy_calls: list[int] = []
    browser_calls: list[int] = []

    def fake_legacy(self, image, record):
        legacy_calls.append(1)
        return image

    def fake_browser(self, image, record):
        browser_calls.append(1)
        return image

    monkeypatch.setattr(FluorescencePanel, "_draw_scale_bar_on_image", fake_legacy)
    monkeypatch.setattr(FluorescencePanel, "_draw_browser_scale_bar_on_image", fake_browser)
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "priority.tif"))
    uid = str(image["uid"])
    image["scale_bar"] = {"length_um": 50.0, "length_px": 40.0, "show_text": True}
    image["browser_scale_bar"] = asdict(ScaleBarState(visible=True, length_um=25.0, um_per_px=0.5, center_x_px=4.0, line_y_px=5.0))
    pixels_before = np.array(panel.image_data(uid), copy=True)
    panel.render_image_with_scale_bar(uid)
    assert legacy_calls == []
    assert browser_calls == [1]
    image["browser_scale_bar"]["visible"] = False
    panel.render_image_with_scale_bar(uid)
    assert legacy_calls == [1]
    assert np.array_equal(panel.image_data(uid), pixels_before)
