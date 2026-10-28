from __future__ import annotations

from copy import deepcopy
from math import hypot, pi, sqrt
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtGui import QColor, QImage

from core.image_analysis.io_engine import ImageImportResult
from core.models import BrightfieldPanel, IHCPanel


def _write_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(210, 70, 70))
    assert image.save(str(path))
    return path


def _import_result(**metadata: object) -> ImageImportResult:
    payload = {
        "width_px": 8,
        "height_px": 6,
        "pixel_size_unit": "um",
        "objective": "20x",
        "bit_depth": 8,
        "n_channels": 3,
        "source_format": "PNG",
        "axes": "CYX",
        "work_data_downsample": 1,
        "io_warnings": [],
    }
    payload.update(metadata)
    return ImageImportResult(
        analysis_data=np.zeros((3, 6, 8), dtype=np.uint8),
        metadata=payload,
        source_hash="sha256:" + "a" * 64,
        channel_names=["Red", "Green", "Blue"],
    )


def _patch_import(monkeypatch: pytest.MonkeyPatch, module_path: str, result: ImageImportResult) -> None:
    monkeypatch.setattr(module_path, lambda source: result)


@pytest.mark.parametrize(
    ("panel_cls", "reader_path", "import_kwargs"),
    [
        (BrightfieldPanel, "core.models.brightfield_panel.read_microscopy_image", {"stain_type": "H&E"}),
        (IHCPanel, "core.models.ihc_panel.read_microscopy_image", {}),
    ],
)
def test_import_preserves_anisotropic_pixel_size_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    panel_cls,
    reader_path: str,
    import_kwargs: dict[str, str],
) -> None:
    image_path = tmp_path / "axis_meta.png"
    image_path.write_bytes(b"stub")
    _patch_import(
        monkeypatch,
        reader_path,
        _import_result(pixel_size_um=0.25, pixel_size_x_um=0.25, pixel_size_y_um=0.50),
    )
    panel = panel_cls(panel_id="Figure1HistologyAxisMeta")
    imported = panel.import_image(image_path, group="Tumor", **import_kwargs)

    assert imported["metadata"]["pixel_size_um"] == 0.25
    assert imported["metadata"]["pixel_size_x_um"] == 0.25
    assert imported["metadata"]["pixel_size_y_um"] == 0.50
    stored = panel.images()[0]["metadata"]
    assert stored["pixel_size_x_um"] == 0.25
    assert stored["pixel_size_y_um"] == 0.50


def test_histology_shared_helper_rereads_embedded_axes_from_imported_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from core.measurement_scale import resolve_calibration

    image_path = tmp_path / "helper_meta.png"
    image_path.write_bytes(b"stub")
    _patch_import(
        monkeypatch,
        "core.models.brightfield_panel.read_microscopy_image",
        _import_result(pixel_size_um=0.25, pixel_size_x_um=0.25, pixel_size_y_um=0.50),
    )
    panel = BrightfieldPanel(panel_id="Figure1HistologyHelper")
    imported = panel.import_image(image_path, stain_type="H&E")
    helper = resolve_calibration(imported["metadata"], imported.get("measurement_scale_bar"))
    scale = panel.measurement_scale_bar(imported["uid"])

    assert helper["um_per_px_x"] == pytest.approx(0.25)
    assert helper["um_per_px_y"] == pytest.approx(0.50)
    assert helper["calibration_source"] == "embedded"
    assert helper["assumed"] is False
    assert helper["calibrated"] is True
    assert scale["um_per_px_x"] == pytest.approx(0.25)
    assert scale["um_per_px_y"] == pytest.approx(0.50)
    assert scale["calibration_source"] == "embedded"
    assert scale["assumed"] is False
    assert scale["calibrated"] is True


def test_ihc_measurement_scale_bar_api_matches_brightfield(tmp_path: Path) -> None:
    panel = IHCPanel(panel_id="Figure1IHCScaleApi")
    imported = panel.import_image(_write_png(tmp_path / "ihc_scale.png"))
    uid = str(imported["uid"])
    line = {"x1": 0.0, "y1": 4.0, "x2": 20.0, "y2": 4.0}

    assert "measurement_scale_bar" in imported
    resolved = panel.update_measurement_scale_bar(
        uid,
        um_per_px=0.4,
        length_um=50.0,
        line_px=dict(line),
        calibration_source="manual",
    )

    assert resolved["um_per_px"] == pytest.approx(0.4)
    assert resolved["calibration_source"] == "manual"
    assert resolved["line_px"] == line
    assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(0.4)
    stored = panel.images()[0]["measurement_scale_bar"]
    assert stored["um_per_px"] == pytest.approx(0.4)
    assert stored["line_px"] == line


def test_bf_update_measurement_scale_bar_does_not_implicitly_rescale_history(tmp_path: Path) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFScaleHistory")
    imported = panel.import_image(_write_png(tmp_path / "bf_history.png"), stain_type="H&E")
    uid = str(imported["uid"])
    historical_scale = {
        "enabled": True,
        "um_per_px": 0.5,
        "length_um": 50.0,
        "calibration_source": "manual",
        "assumed": False,
    }
    traditional = {
        "measurement_uuid": "trad-1",
        "image_uid": uid,
        "stain_type": "H&E",
        "roi": {
            "mode": "selected_roi",
            "items": [{"roi_uuid": "roi-keep", "type": "rectangle"}],
            "active_item_uuid": "roi-keep",
        },
        "results": {"positive_area_px": 10},
        "measurement_scale_bar": deepcopy(historical_scale),
    }
    ai = {
        "measurement_uuid": "ai-1",
        "image_uid": uid,
        "results": {
            "ai_object_areas_px": [10],
            "ai_object_areas_um2": [2.5],
            "ai_object_total_area_px": 10,
            "ai_object_total_area_um2": 2.5,
        },
        "analysis_metadata": {"measurement_scale_bar": deepcopy(historical_scale)},
        "measurement_scale_bar": deepcopy(historical_scale),
    }
    panel.analysis["brightfield_measurements"] = [traditional]
    panel.analysis["brightfield_ai_object_count_measurements"] = [ai]

    resolved = panel.update_measurement_scale_bar(
        uid,
        um_per_px=2.0,
        length_um=50.0,
        calibration_source="manual",
    )

    assert resolved["um_per_px"] == pytest.approx(2.0)
    assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(2.0)
    assert traditional["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert traditional["stain_type"] == "H&E"
    assert traditional["roi"]["items"][0]["roi_uuid"] == "roi-keep"
    assert ai["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert ai["results"]["ai_object_areas_um2"] == [2.5]

    panel.rescale_ai_object_count_measurements(uid)

    assert ai["measurement_scale_bar"]["um_per_px"] == pytest.approx(2.0)
    assert ai["results"]["ai_object_areas_um2"][0] == pytest.approx(40.0)
    assert traditional["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert traditional["roi"]["items"][0]["roi_uuid"] == "roi-keep"


def test_ihc_update_measurement_scale_bar_does_not_rewrite_history(tmp_path: Path) -> None:
    panel = IHCPanel(panel_id="Figure1IHCScaleHistory")
    imported = panel.import_image(_write_png(tmp_path / "ihc_history.png"))
    uid = str(imported["uid"])
    historical_scale = {
        "enabled": True,
        "um_per_px": 0.5,
        "length_um": 50.0,
        "calibration_source": "manual",
        "assumed": False,
    }
    stain_qc = {"compatible": True, "warning": None}
    traditional = {
        "measurement_uuid": "ihc-1",
        "image_uid": uid,
        "stain_qc": deepcopy(stain_qc),
        "color_qc": {"cast": "none"},
        "roi": {
            "mode": "selected_roi",
            "items": [{"roi_uuid": "ihc-roi-keep", "type": "rectangle"}],
            "active_item_uuid": "ihc-roi-keep",
        },
        "results": {"positive_area_px": 12},
        "measurement_scale_bar": deepcopy(historical_scale),
    }
    panel.analysis["ihc_measurements"] = [traditional]
    imported.setdefault("measurements", []).append(traditional)

    panel.update_measurement_scale_bar(uid, um_per_px=2.0, length_um=50.0, calibration_source="manual")

    assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(2.0)
    assert traditional["measurement_uuid"] == "ihc-1"
    assert traditional["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert traditional["stain_qc"] == stain_qc
    assert traditional["roi"]["items"][0]["roi_uuid"] == "ihc-roi-keep"
    assert traditional["results"]["positive_area_px"] == 12


def test_bf_traditional_measurement_snapshots_scale_without_touching_stain_or_roi(tmp_path: Path) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFScaleSnap")
    imported = panel.import_image(_write_png(tmp_path / "bf_snap.png"), stain_type="H&E")
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, length_um=50.0, calibration_source="manual")
    roi = panel.set_image_roi(uid, "rectangle", {"x": 0, "y": 0, "w": 4, "h": 4})

    first = panel.create_measurement(uid)
    first_scale = deepcopy(first["measurement_scale_bar"])
    first_uuid = first["measurement_uuid"]

    assert first["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert first["stain_type"] == "H&E"
    assert first["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]

    panel.update_measurement_scale_bar(uid, um_per_px=2.0, length_um=50.0, calibration_source="manual")
    second = panel.create_measurement(uid)

    assert first["measurement_uuid"] == first_uuid
    assert first["measurement_scale_bar"] == first_scale
    assert first["stain_type"] == "H&E"
    assert first["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]
    assert second["measurement_uuid"] != first_uuid
    assert second["measurement_scale_bar"]["um_per_px"] == pytest.approx(2.0)
    assert second["stain_type"] == "H&E"
    assert second["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]


def test_ihc_traditional_measurement_snapshots_scale_without_touching_stain_or_roi(tmp_path: Path) -> None:
    panel = IHCPanel(panel_id="Figure1IHCScaleSnap")
    imported = panel.import_image(_write_png(tmp_path / "ihc_snap.png"))
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, length_um=50.0, calibration_source="manual")
    roi = panel.set_image_roi(uid, "rectangle", {"x": 0, "y": 0, "w": 4, "h": 4})

    first = panel.measure_dab_positive_area(uid)
    first_scale = deepcopy(first["measurement_scale_bar"])
    first_uuid = first["measurement_uuid"]
    first_stain_qc = deepcopy(first.get("stain_qc"))
    first_color_qc = deepcopy(first.get("color_qc"))

    assert first["measurement_scale_bar"]["um_per_px"] == pytest.approx(0.5)
    assert first["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]
    assert "stain_qc" in first
    assert "color_qc" in first

    panel.update_measurement_scale_bar(uid, um_per_px=2.0, length_um=50.0, calibration_source="manual")
    second = panel.measure_dab_positive_area(uid)

    assert first["measurement_uuid"] == first_uuid
    assert first["measurement_scale_bar"] == first_scale
    assert first["stain_qc"] == first_stain_qc
    assert first["color_qc"] == first_color_qc
    assert first["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]
    assert second["measurement_uuid"] != first_uuid
    assert second["measurement_scale_bar"]["um_per_px"] == pytest.approx(2.0)
    assert second["roi"]["items"][0]["roi_uuid"] == roi["roi_uuid"]
    assert "stain_qc" in second
    assert second["stain_qc"] != second["measurement_scale_bar"]


@pytest.mark.parametrize(
    ("panel_cls", "reader_path", "import_kwargs"),
    [
        (BrightfieldPanel, "core.models.brightfield_panel.read_microscopy_image", {"stain_type": "H&E"}),
        (IHCPanel, "core.models.ihc_panel.read_microscopy_image", {}),
    ],
)
def test_assumed_scale_is_not_promoted_to_physical_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    panel_cls,
    reader_path: str,
    import_kwargs: dict[str, str],
) -> None:
    image_path = tmp_path / "assumed.png"
    image_path.write_bytes(b"stub")
    _patch_import(
        monkeypatch,
        reader_path,
        _import_result(pixel_size_um=None),
    )
    panel = panel_cls(panel_id="Figure1HistologyAssumed")
    imported = panel.import_image(image_path, **import_kwargs)
    uid = str(imported["uid"])

    scale = panel.measurement_scale_bar(uid)
    assert scale["calibration_source"] == "assumed"
    assert scale["assumed"] is True
    assert scale["calibrated"] is False
    reread = panel.measurement_scale_bar(uid)
    assert reread["calibration_source"] == "assumed"
    assert reread["assumed"] is True
    assert reread["calibrated"] is False
    assert imported["metadata"].get("pixel_size_um") in {None, 0, 0.0}


@pytest.mark.parametrize(
    ("panel_cls", "reader_path", "import_kwargs"),
    [
        (BrightfieldPanel, "core.models.brightfield_panel.read_microscopy_image", {"stain_type": "H&E"}),
        (IHCPanel, "core.models.ihc_panel.read_microscopy_image", {}),
    ],
)
def test_manual_scalar_update_overwrites_cached_axes_via_merge_and_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    panel_cls,
    reader_path: str,
    import_kwargs: dict[str, str],
) -> None:
    image_path = tmp_path / "scalar_overwrite.png"
    image_path.write_bytes(b"stub")
    _patch_import(
        monkeypatch,
        reader_path,
        _import_result(pixel_size_um=0.25, pixel_size_x_um=0.25, pixel_size_y_um=0.50),
    )
    panel = panel_cls(panel_id="Figure1HistologyScalarOverwrite")
    imported = panel.import_image(image_path, **import_kwargs)
    uid = str(imported["uid"])

    first = panel.update_measurement_scale_bar(uid, um_per_px=0.5, length_um=50.0, calibration_source="manual")
    assert first["um_per_px"] == pytest.approx(0.5)
    assert first["um_per_px_x"] == pytest.approx(0.5)
    assert first["um_per_px_y"] == pytest.approx(0.5)

    second = panel.update_measurement_scale_bar(uid, um_per_px=2.0, length_um=50.0, calibration_source="manual")
    assert second["um_per_px"] == pytest.approx(2.0)
    assert second["um_per_px_x"] == pytest.approx(2.0)
    assert second["um_per_px_y"] == pytest.approx(2.0)
    assert second["calibration_source"] == "manual"
    assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(2.0)


@pytest.mark.parametrize(
    ("panel_cls", "reader_path", "import_kwargs"),
    [
        (BrightfieldPanel, "core.models.brightfield_panel.read_microscopy_image", {"stain_type": "H&E"}),
        (IHCPanel, "core.models.ihc_panel.read_microscopy_image", {}),
    ],
)
def test_reset_zero_restores_embedded_metadata_via_merge_and_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    panel_cls,
    reader_path: str,
    import_kwargs: dict[str, str],
) -> None:
    image_path = tmp_path / "reset_embedded.png"
    image_path.write_bytes(b"stub")
    _patch_import(
        monkeypatch,
        reader_path,
        _import_result(pixel_size_um=0.25, pixel_size_x_um=0.25, pixel_size_y_um=0.50),
    )
    panel = panel_cls(panel_id="Figure1HistologyResetEmbedded")
    imported = panel.import_image(image_path, **import_kwargs)
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px=2.0, length_um=50.0, calibration_source="manual")

    restored = panel.update_measurement_scale_bar(uid, length_um=50.0, um_per_px=0.0, line_px={})

    assert restored["calibration_source"] == "embedded"
    assert restored["um_per_px_x"] == pytest.approx(0.25)
    assert restored["um_per_px_y"] == pytest.approx(0.50)
    assert restored["assumed"] is False
    assert restored["calibrated"] is True
    assert panel.images()[0]["metadata"]["pixel_size_y_um"] == 0.50


def test_bf_explicit_rescale_applies_area_xy(tmp_path: Path) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFAreaXY")
    imported = panel.import_image(_write_png(tmp_path / "bf_area_xy.png"), stain_type="H&E")
    uid = str(imported["uid"])
    ai = {
        "measurement_uuid": "ai-xy",
        "image_uid": uid,
        "results": {
            "ai_object_areas_px": [10],
            "ai_object_areas_um2": [1.0],
            "ai_object_total_area_px": 10,
            "ai_object_total_area_um2": 1.0,
        },
        "analysis_metadata": {},
        "measurement_scale_bar": {"um_per_px": 0.5, "calibration_source": "manual"},
    }
    panel.analysis["brightfield_ai_object_count_measurements"] = [ai]
    panel.update_measurement_scale_bar(
        uid,
        um_per_px_x=0.5,
        um_per_px_y=2.0,
        calibration_source="manual",
    )

    panel.rescale_ai_object_count_measurements(uid)

    assert ai["results"]["ai_object_areas_um2"][0] == pytest.approx(10.0)
    assert ai["measurement_scale_bar"]["um_per_px_x"] == pytest.approx(0.5)
    assert ai["measurement_scale_bar"]["um_per_px_y"] == pytest.approx(2.0)


def test_bf_preview_uses_display_line_px_including_empty_and_skips_unknown_um_label() -> None:
    from ui.panels.brightfield_workspace import measurement_preview_length_label, measurement_preview_line_px

    drawn = {"x1": 1.0, "y1": 2.0, "x2": 9.0, "y2": 2.0}
    legacy = {"x1": 0.0, "y1": 0.0, "x2": 40.0, "y2": 0.0}
    assert measurement_preview_line_px({"display_line_px": drawn, "line_px": legacy}) == drawn
    assert measurement_preview_line_px({"display_line_px": {}, "line_px": legacy}) == {}
    assert measurement_preview_line_px({"line_px": legacy}) == legacy
    assert measurement_preview_length_label({"assumed": True, "length_um": 50.0, "line_px": legacy}) == ""
    assert measurement_preview_length_label({"calibrated": False, "length_um": 50.0, "um_per_px": 0.5}) == ""
    assert measurement_preview_length_label({"calibrated": True, "um_per_px": 0.5, "length_um": 50.0}) == "50 um"


def _rectangle_cell_analysis(um_per_px: float) -> dict[str, object]:
    width, height = 20.0, 10.0
    area = width * height
    equivalent_px = float(sqrt(4.0 * area / pi))
    contour = [[0.0, 0.0], [width, 0.0], [width, height], [0.0, height]]
    return {
        "analysis_version": "bright_round_cells.v1",
        "um_per_px": float(um_per_px),
        "settings": {},
        "results": [
            {
                "id": 1,
                "center_x_px": 10.0,
                "center_y_px": 5.0,
                "contour_px": contour,
                "feret_start_px": [0, 0],
                "feret_end_px": [20, 10],
                "area_px": area,
                "equivalent_diameter_px": equivalent_px,
                "equivalent_diameter_um": float(equivalent_px * um_per_px),
                "max_feret_px": float(hypot(width, height)),
                "max_feret_um": float(hypot(width, height) * um_per_px),
                "min_feret_px": height,
                "min_feret_um": float(height * um_per_px),
                "shape_class": "round",
                "include_in_statistics": True,
            }
        ],
        "summary": {
            "count": 1,
            "mean_equivalent_diameter_um": float(equivalent_px * um_per_px),
        },
    }


def test_bf_anisotropic_round_cell_calibrates_rectangle_and_keeps_ids_contours(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFAnisoRect")
    imported = panel.import_image(_write_png(tmp_path / "bf_aniso_rect.png"), stain_type="H&E")
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px_x=2.0, um_per_px_y=0.5, calibration_source="manual")
    captured: dict[str, float] = {}

    def _fake_analyzer(*args: object, **kwargs: object) -> dict[str, object]:
        um_per_px = float(kwargs.get("um_per_px", 0.0) or 0.0)
        captured["um_per_px"] = um_per_px
        return _rectangle_cell_analysis(um_per_px)

    monkeypatch.setattr("core.models.brightfield_panel.analyze_bright_round_cells", _fake_analyzer)
    calibrate_calls: list[object] = []
    real_calibrate = __import__(
        "core.image_analysis.cell_calibration",
        fromlist=["calibrate_cell_analysis"],
    ).calibrate_cell_analysis

    def _tracking_calibrate(analysis: object, scale: object) -> dict[str, object]:
        calibrate_calls.append(scale)
        return real_calibrate(analysis, scale)

    monkeypatch.setattr("core.models.brightfield_panel.calibrate_cell_analysis", _tracking_calibrate)

    first = panel.measure_bright_round_cells(uid)
    first_row = first["results"][0]
    first_ids = [row["id"] for row in first["results"]]
    first_contours = [row["contour_px"] for row in first["results"]]
    expected_eq = sqrt(4.0 * 200.0 * 2.0 * 0.5 / pi)

    assert captured["um_per_px"] == pytest.approx(sqrt(2.0 * 0.5))
    assert calibrate_calls
    assert first_row["id"] == 1
    assert first_row["contour_px"] == [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]]
    assert first_row["feret_start_px"] == [0, 0]
    assert first_row["feret_end_px"] == [20, 10]
    assert first_row["equivalent_diameter_um"] == pytest.approx(expected_eq)
    assert first_row["max_feret_um"] == pytest.approx(hypot(40.0, 5.0))
    assert first_row["min_feret_um"] == pytest.approx(5.0)
    assert first["analysis"]["um_per_px"] == pytest.approx(2.0)
    assert first["analysis"]["um_per_px_x"] == pytest.approx(2.0)
    assert first["analysis"]["um_per_px_y"] == pytest.approx(0.5)

    snapshot = deepcopy(first["results"])
    panel.update_measurement_scale_bar(uid, um_per_px_x=0.25, um_per_px_y=0.50, calibration_source="manual")
    stored = panel.analysis["bright_round_cell_diameter_measurements"][0]
    assert stored["results"] == snapshot
    assert stored["results"][0]["id"] == first_ids[0]
    assert stored["results"][0]["contour_px"] == first_contours[0]


def test_bf_isotropic_round_cell_skips_post_calibrate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFIsoCell")
    imported = panel.import_image(_write_png(tmp_path / "bf_iso_cell.png"), stain_type="H&E")
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, length_um=50.0, calibration_source="manual")
    captured: dict[str, float] = {}

    def _fake_analyzer(*args: object, **kwargs: object) -> dict[str, object]:
        um_per_px = float(kwargs.get("um_per_px", 0.0) or 0.0)
        captured["um_per_px"] = um_per_px
        return _rectangle_cell_analysis(um_per_px)

    monkeypatch.setattr("core.models.brightfield_panel.analyze_bright_round_cells", _fake_analyzer)
    calibrate_calls: list[object] = []
    monkeypatch.setattr(
        "core.models.brightfield_panel.calibrate_cell_analysis",
        lambda *args, **kwargs: calibrate_calls.append(args) or args[0],
    )

    measurement = panel.measure_bright_round_cells(uid)
    row = measurement["results"][0]
    expected_eq = sqrt(4.0 * 200.0 / pi) * 0.5

    assert captured["um_per_px"] == pytest.approx(0.5)
    assert calibrate_calls == []
    assert row["id"] == 1
    assert row["contour_px"] == [[0.0, 0.0], [20.0, 0.0], [20.0, 10.0], [0.0, 10.0]]
    assert row["equivalent_diameter_um"] == pytest.approx(expected_eq)
    assert row["max_feret_um"] == pytest.approx(hypot(20.0, 10.0) * 0.5)
    assert row["min_feret_um"] == pytest.approx(5.0)


def test_bf_incomplete_axis_round_cell_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = BrightfieldPanel(panel_id="Figure1BFIncompleteAxis")
    imported = panel.import_image(_write_png(tmp_path / "bf_incomplete.png"), stain_type="H&E")
    uid = str(imported["uid"])
    panel.update_measurement_scale_bar(uid, um_per_px_x=0.5, calibration_source="manual")
    called: list[object] = []
    monkeypatch.setattr(
        "core.models.brightfield_panel.analyze_bright_round_cells",
        lambda *args, **kwargs: called.append(kwargs) or _rectangle_cell_analysis(0.5),
    )

    with pytest.raises(ValueError, match="complete positive"):
        panel.measure_bright_round_cells(uid)
    assert called == []
