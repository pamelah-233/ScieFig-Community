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
from core.measurement_scale import apply_area_scale, apply_length_scale, resolve_calibration, resolve_measurement_scale
from core.models.fluoro_panel import AI_ASSISTED_MEASUREMENTS_KEY, FluorescencePanel, ImageMetadata


def _write_cyx(path: Path) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _manual_xy_scale() -> dict[str, object]:
    return {
        "enabled": True,
        "length_um": 50.0,
        "um_per_px": 0.5,
        "um_per_px_x": 0.5,
        "um_per_px_y": 0.4,
        "calibration_source": "manual",
        "assumed": False,
        "calibrated": True,
    }


def test_image_metadata_xy_roundtrip() -> None:
    original = ImageMetadata(
        pixel_size_um=0.5,
        pixel_size_x_um=0.5,
        pixel_size_y_um=0.4,
        width_px=14,
        height_px=12,
        n_channels=2,
    )
    restored = ImageMetadata.from_dict(original.to_dict())
    assert restored.pixel_size_um == 0.5
    assert restored.pixel_size_x_um == 0.5
    assert restored.pixel_size_y_um == 0.4
    axes_only = ImageMetadata.from_dict({"pixel_size_x_um": 0.2, "pixel_size_y_um": 0.3, "width_px": 8})
    assert axes_only.pixel_size_x_um == 0.2
    assert axes_only.pixel_size_y_um == 0.3
    assert axes_only.pixel_size_um is None


def test_image_metadata_scalar_only_roundtrip_does_not_insert_axis_keys() -> None:
    payload = ImageMetadata.from_dict({"pixel_size_um": 0.5, "width_px": 8, "height_px": 6}).to_dict()
    assert payload["pixel_size_um"] == 0.5
    assert "pixel_size_x_um" not in payload
    assert "pixel_size_y_um" not in payload
    cal = resolve_calibration(payload)
    assert cal["calibrated"] is True
    assert cal["um_per_px"] == pytest.approx(0.5)
    assert cal["um_per_px_x"] == pytest.approx(0.5)
    assert cal["um_per_px_y"] == pytest.approx(0.5)


def test_image_metadata_partial_axes_stay_partial() -> None:
    payload = ImageMetadata.from_dict({"pixel_size_x_um": 0.2, "width_px": 8, "height_px": 6}).to_dict()
    assert payload["pixel_size_x_um"] == 0.2
    assert "pixel_size_y_um" not in payload
    cal = resolve_calibration(payload)
    assert cal["um_per_px_x"] == pytest.approx(0.2)
    assert cal["um_per_px_y"] == 0.0
    restored = ImageMetadata.from_dict(payload).to_dict()
    assert "pixel_size_y_um" not in restored


def test_ome_save_writes_xy_and_omits_unknown(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "ome_xy.tif"))
    image["metadata"]["pixel_size_um"] = 0.5
    image["metadata"]["pixel_size_x_um"] = 0.5
    image["metadata"]["pixel_size_y_um"] = 0.4
    calibrated = tmp_path / "calibrated.ome.tif"
    panel.save_image_data_as_ome_tiff(str(image["uid"]), calibrated)
    with tifffile.TiffFile(calibrated) as handle:
        xml = handle.ome_metadata or ""
    assert "PhysicalSizeX" in xml
    assert "PhysicalSizeY" in xml
    assert "0.5" in xml
    assert "0.4" in xml

    uncal = FluorescencePanel(panel_id="Figure1D")
    blank = uncal.import_image(_write_cyx(tmp_path / "ome_blank.tif"))
    blank["metadata"]["pixel_size_um"] = None
    blank["metadata"]["pixel_size_x_um"] = None
    blank["metadata"]["pixel_size_y_um"] = None
    unknown = tmp_path / "unknown.ome.tif"
    uncal.save_image_data_as_ome_tiff(str(blank["uid"]), unknown)
    with tifffile.TiffFile(unknown) as handle:
        xml = handle.ome_metadata or ""
    assert "PhysicalSizeX" not in xml
    assert "PhysicalSizeY" not in xml


def test_roi_and_line_profile_use_anisotropic_live_scale_and_snapshot(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "aniso.tif"))
    uid = str(image["uid"])
    image["metadata"]["pixel_size_um"] = 0.5
    image["metadata"]["pixel_size_x_um"] = 0.5
    image["metadata"]["pixel_size_y_um"] = 0.4
    panel.update_measurement_scale_bar(uid, **_manual_xy_scale())
    live = panel.measurement_scale_bar(uid)
    assert live.get("um_per_px_x") == pytest.approx(0.5)
    assert live.get("um_per_px_y") == pytest.approx(0.4)

    measurement = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
    row = measurement["results_table"][0]
    assert row["area_px"] == 20
    assert row["area_um2"] == pytest.approx(apply_area_scale(20, live))
    assert row["area_um2"] == pytest.approx(4.0)
    assert measurement["params"]["pixel_size_um"] != 1.0
    assert measurement["measurement_scale_bar"]["um_per_px_x"] == pytest.approx(0.5)
    assert measurement["measurement_scale_bar"]["um_per_px_y"] == pytest.approx(0.4)

    profile = panel.line_profile(uid, [0], {"x1": 0, "y1": 2, "x2": 10, "y2": 2}, samples=11)
    assert profile[0]["distance_px"][-1] == pytest.approx(10.0)
    assert profile[0]["distance_um"][-1] == pytest.approx(apply_length_scale(10.0, 0.0, live))
    assert profile[0]["distance_unit"] == "um"
    assert profile[0]["measurement_scale_bar"]["um_per_px_x"] == pytest.approx(0.5)

    vertical = panel.line_profile(uid, [0], {"x1": 3, "y1": 0, "x2": 3, "y2": 8}, samples=9)
    assert vertical[0]["distance_um"][-1] == pytest.approx(apply_length_scale(0.0, 8.0, live))


def test_uncalibrated_physical_fields_are_none_with_pixels_retained(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "none.tif"))
    uid = str(image["uid"])
    image["metadata"]["pixel_size_um"] = None
    image["metadata"]["pixel_size_x_um"] = None
    image["metadata"]["pixel_size_y_um"] = None
    live = resolve_measurement_scale(image, dict(image.get("measurement_scale_bar", {})))
    assert live.get("calibrated") is False or live.get("assumed") is True

    measurement = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
    row = measurement["results_table"][0]
    assert row["area_px"] == 20
    assert row["area_um2"] is None
    assert measurement["params"]["pixel_size_um"] is None

    roi = panel.measure_roi(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, 0)
    assert roi["area_px"] == 20
    assert roi["area_um2"] is None

    profile = panel.line_profile(uid, [0], {"x1": 0, "y1": 2, "x2": 10, "y2": 2}, samples=11)
    assert len(profile[0]["distance_px"]) == 11
    assert profile[0]["distance_px"][-1] == pytest.approx(10.0)
    assert profile[0]["distance_um"] in (None, [])
    assert profile[0]["distance_unit"] == "px"


def test_update_measurement_scale_bar_does_not_rewrite_history_until_explicit_refresh() -> None:
    uid = "hist-1"
    contour = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]]
    record = {
        "image_uid": uid,
        "channel_name": "DAPI",
        "object_area_distribution_px": [10],
        "object_area_distribution_um2": [1.0],
        "total_object_area_px": 10,
        "total_object_area_um2": 1.0,
        "mean_object_area_um2": 1.0,
        "median_object_area_um2": 1.0,
        "object_table": [
            {"object_id": 7, "area_px": 10, "area_um2": 1.0, "contour_points_px": deepcopy(contour), "channel_name": "DAPI"}
        ],
        "measurement_scale_bar": {"um_per_px": 0.316227766, "calibration_source": "manual"},
    }
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.data["fluorescence_images"] = [
        {
            "uid": uid,
            "source_name": "keep-name.tif",
            "metadata": {"width_px": 32, "height_px": 32},
            "measurement_scale_bar": {"um_per_px": 0.316227766, "calibration_source": "manual"},
            "rois": [{"roi_id": "roi-keep", "type": "rectangle"}],
            AI_ASSISTED_MEASUREMENTS_KEY: [deepcopy(record)],
        }
    ]
    panel.data["active_image_uid"] = uid
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(record)]
    before = deepcopy(panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0])

    panel.update_measurement_scale_bar(uid, um_per_px=2.0, um_per_px_x=2.0, um_per_px_y=2.0, calibration_source="manual")
    after = panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]
    assert after["total_object_area_um2"] == before["total_object_area_um2"]
    assert after["object_table"][0]["area_um2"] == before["object_table"][0]["area_um2"]
    assert after["measurement_scale_bar"] == before["measurement_scale_bar"]
    assert after["object_table"][0]["contour_points_px"] == contour
    assert after["object_table"][0]["object_id"] == 7
    assert panel.images()[0]["rois"][0]["roi_id"] == "roi-keep"
    assert panel.images()[0]["source_name"] == "keep-name.tif"
    assert panel.images()[0][AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 1.0

    panel.refresh_ai_assisted_measurements_for_scale(uid)
    refreshed = panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]
    live = panel.measurement_scale_bar(uid)
    assert refreshed["object_table"][0]["area_um2"] == pytest.approx(apply_area_scale(10, live))
    assert refreshed["total_object_area_um2"] == pytest.approx(apply_area_scale(10, live))
    assert refreshed["object_table"][0]["object_id"] == 7
    assert refreshed["object_table"][0]["contour_points_px"] == contour
    assert refreshed["object_table"][0]["channel_name"] == "DAPI"
    assert refreshed["channel_name"] == "DAPI"
    assert panel.images()[0]["rois"][0]["roi_id"] == "roi-keep"
    image_refreshed = panel.images()[0][AI_ASSISTED_MEASUREMENTS_KEY][0]
    assert image_refreshed["object_table"][0]["contour_points_px"] == contour
    assert image_refreshed["object_table"][0]["object_id"] == 7


def test_apply_browser_scale_transfer_deep_copies_without_mutating(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "transfer.tif"))
    uid = str(image["uid"])
    image["rois"] = [{"roi_id": "roi-keep", "type": "rectangle"}]
    history = {
        "image_uid": uid,
        "total_object_area_um2": 3.0,
        "object_table": [{"object_id": 1, "area_px": 4, "area_um2": 3.0, "contour_points_px": [[0, 0]]}],
    }
    image[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    pixels_before = np.array(panel.image_data(uid), copy=True)
    metadata_before = deepcopy(image["metadata"])
    name_before = image["source_name"]
    calibration = {
        "enabled": True,
        "length_um": 25.0,
        "um_per_px": 0.2,
        "um_per_px_x": 0.2,
        "um_per_px_y": 0.3,
        "calibration_source": "manual",
        "preset_id": "keep-preset",
        "preset_name": "Keep Me",
    }
    display = asdict(ScaleBarState(visible=True, length_um=25.0, um_per_px=0.2, center_x_px=12.0, line_y_px=34.0, line_color="#00FF00"))
    payload = {"calibration": calibration, "display": display}

    panel.apply_browser_scale_transfer(uid, payload)
    calibration["um_per_px"] = 9.9
    display["center_x_px"] = 999.0
    payload["calibration"]["length_um"] = 1.0
    stored = image["measurement_scale_bar"]
    stored["length_um"] = 8.0
    image["browser_scale_bar"]["line_y_px"] = 1.0

    assert payload["calibration"]["um_per_px"] == 9.9
    assert payload["display"]["center_x_px"] == 999.0
    reloaded = next(item for item in panel.images() if item["uid"] == uid)
    assert reloaded["measurement_scale_bar"]["um_per_px"] == 0.2
    assert reloaded["measurement_scale_bar"]["length_um"] == 8.0
    assert reloaded["measurement_scale_bar"]["preset_id"] == "keep-preset"
    assert calibration["length_um"] == 1.0
    assert reloaded["browser_scale_bar"]["center_x_px"] == 12.0
    assert display["line_y_px"] == 34.0
    assert np.array_equal(panel.image_data(uid), pixels_before)
    assert reloaded["metadata"] == metadata_before
    assert reloaded["source_name"] == name_before
    assert reloaded["rois"][0]["roi_id"] == "roi-keep"
    assert panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 3.0
    assert reloaded[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["contour_points_px"] == [[0, 0]]


def test_update_scale_bar_length_uses_shared_x_and_keeps_style(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "legacy_bar.tif"))
    uid = str(image["uid"])
    image["scale_bar"] = {
        "length_um": 10.0,
        "position": "top-left",
        "color": "#ABCDEF",
        "height_px": 9,
        "show_text": False,
        "length_px": 20.0,
        "font_size": 11.0,
    }
    panel.update_measurement_scale_bar(uid, **_manual_xy_scale())
    panel.update_scale_bar(uid, length_um=50.0)
    bar = image["scale_bar"]
    assert bar["position"] == "top-left"
    assert bar["color"] == "#ABCDEF"
    assert bar["height_px"] == 9
    assert bar["show_text"] is False
    assert bar["font_size"] == 11.0
    assert bar["length_um"] == 50.0
    assert bar["length_px"] == pytest.approx(50.0 / 0.5)


def test_typed_scalar_update_clears_stale_axes_via_merge_then_resolve(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "merge_axes.tif"))
    uid = str(image["uid"])
    panel.update_measurement_scale_bar(uid, **_manual_xy_scale())
    assert panel.measurement_scale_bar(uid)["um_per_px_y"] == pytest.approx(0.4)

    panel.update_measurement_scale_bar(uid, um_per_px=0.25, calibration_source="manual")
    live = panel.measurement_scale_bar(uid)
    assert live["um_per_px"] == pytest.approx(0.25)
    assert live["um_per_px_x"] == pytest.approx(0.25)
    assert live["um_per_px_y"] == pytest.approx(0.25)
    assert live["calibration_source"] == "manual"

    image["metadata"]["pixel_size_um"] = 0.8
    image["metadata"]["pixel_size_x_um"] = 0.8
    image["metadata"]["pixel_size_y_um"] = 0.6
    panel.update_measurement_scale_bar(uid, um_per_px=0, calibration_source="embedded")
    reset = panel.measurement_scale_bar(uid)
    assert reset["calibration_source"] == "embedded"
    assert reset["um_per_px_x"] == pytest.approx(0.8)
    assert reset["um_per_px_y"] == pytest.approx(0.6)


def test_render_image_with_scale_bar_overlays_browser_display_not_pixels(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_draw(painter, target_rect, source_size, state) -> None:
        calls.append(
            {
                "visible": bool(state.visible),
                "um_per_px": state.um_per_px,
                "center_x_px": float(state.center_x_px),
                "line_y_px": float(state.line_y_px),
            }
        )

    monkeypatch.setattr("core.models.fluoro_panel.draw_scale_bar", fake_draw)
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_cyx(tmp_path / "overlay.tif"))
    uid = str(image["uid"])
    image["scale_bar"] = {"length_px": 0.0, "length_um": 50.0, "show_text": False}
    pixels_before = np.array(panel.image_data(uid), copy=True)
    display = asdict(
        ScaleBarState(visible=True, length_um=25.0, um_per_px=0.2, center_x_px=7.0, line_y_px=9.0, line_color="#FF0000")
    )
    panel.apply_browser_scale_transfer(uid, {"calibration": _manual_xy_scale(), "display": display})
    painted = panel.render_image_with_scale_bar(uid)
    assert not painted.isNull()
    assert calls == [{"visible": True, "um_per_px": 0.5, "center_x_px": 7.0, "line_y_px": 9.0}]
    panel.update_measurement_scale_bar(uid, um_per_px=0.25, um_per_px_x=0.25, um_per_px_y=0.25, calibration_source="manual")
    calls.clear()
    panel.render_image_with_scale_bar(uid)
    assert calls == [{"visible": True, "um_per_px": 0.25, "center_x_px": 7.0, "line_y_px": 9.0}]
    image["browser_scale_bar"]["visible"] = False
    calls.clear()
    panel.render_image_with_scale_bar(uid)
    assert calls == []
    assert np.array_equal(panel.image_data(uid), pixels_before)


def test_assumed_transfer_on_non_active_keeps_file_calibration(tmp_path: Path) -> None:
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(_write_cyx(tmp_path / "file_cal.tif"))
    second = panel.import_image(_write_cyx(tmp_path / "other.tif"))
    first_uid = str(first["uid"])
    first["metadata"] = ImageMetadata.from_dict({**dict(first["metadata"]), "pixel_size_um": 0.8}).to_dict()
    assert "pixel_size_x_um" not in first["metadata"]
    first["rois"] = [{"roi_id": "roi-keep", "type": "rectangle"}]
    history = {
        "image_uid": first_uid,
        "total_object_area_um2": 3.0,
        "object_table": [{"object_id": 1, "area_px": 4, "area_um2": 3.0, "contour_points_px": [[0, 0]]}],
    }
    first[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    pixels_before = np.array(panel.image_data(first_uid), copy=True)
    channels_before = deepcopy(first["channels"])
    path_before = first["source_path"]
    display = asdict(ScaleBarState(visible=True, length_um=25.0, um_per_px=9.9, center_x_px=12.0, line_y_px=34.0))
    panel.apply_browser_scale_transfer(
        first_uid,
        {
            "calibration": {"calibration_source": "assumed", "assumed": True, "um_per_px": 0.0},
            "display": display,
        },
    )
    assert panel.data["active_image_uid"] == second["uid"]
    stored = first["measurement_scale_bar"]
    assert stored.get("calibrated") is True
    assert stored.get("calibration_source") == "embedded"
    assert stored.get("um_per_px_x") == pytest.approx(0.8)
    assert stored.get("um_per_px_y") == pytest.approx(0.8)
    assert first["browser_scale_bar"]["um_per_px"] == pytest.approx(0.8)
    assert first["browser_scale_bar"]["center_x_px"] == 12.0
    assert first["browser_scale_bar"]["line_y_px"] == 34.0
    assert np.array_equal(panel.image_data(first_uid), pixels_before)
    # Browser transfer deliberately preserves its preview range/color; raw
    # pixels, embedded calibration and stored analytical results stay intact.
    from core.image_analysis.io_engine import preview_display_max
    assert first['channels'][0]['display_min'] == 0.0
    assert first['channels'][0]['display_max'] == preview_display_max(pixels_before)
    assert first['channels'][0]['auto_contrast_min']==channels_before[0]['auto_contrast_min']
    assert first["source_path"] == path_before
    assert first["rois"][0]["roi_id"] == "roi-keep"
    assert panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 3.0
    assert first[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["contour_points_px"] == [[0, 0]]
