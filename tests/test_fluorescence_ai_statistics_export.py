from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import tifffile

from core.export import export_fluoro_measurements, fluoro_exporter
from core.fluoro_statistics import (
    FLUOR_STAT_METRICS,
    FLUOR_STAT_SOURCES,
    collect_fluoro_distribution_rows,
    collect_fluoro_stat_values,
)
from core.image_analysis.fluorescence_ai import FluorescenceAIAssistedSettings, FluorescenceAIModelBoundary
from core.image_analysis.fluorescence_smart import FluorescenceSmartSettings
from core.models import FluorescencePanel


def _write_ai_tiff(path: Path, *, ch0_value: int = 180, ch1_value: int = 260) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = ch0_value
    data[0, 25:31, 29:35] = max(1, ch0_value - 20)
    data[1, 18:25, 20:27] = ch1_value
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _panel_with_ai_image(tmp_path, filename: str = "ai_cells.tif") -> tuple[FluorescencePanel, dict]:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_ai_tiff(tmp_path / filename))
    return panel, image


def _labels_for_image(panel: FluorescencePanel, uid: str, *, channels: list[int] = [0]) -> dict[int, np.ndarray]:
    data = panel.image_data(uid)
    assert data is not None
    labels: dict[int, np.ndarray] = {}
    if 0 in channels:
        mask = np.zeros(data.shape[1:], dtype=np.int32)
        mask[8:14, 9:15] = 1
        mask[25:31, 29:35] = 2
        labels[0] = mask
    if 1 in channels:
        mask = np.zeros(data.shape[1:], dtype=np.int32)
        mask[18:25, 20:27] = 1
        labels[1] = mask
    return labels


def _mock_boundary(panel: FluorescencePanel, uid: str, *, channels: list[int] = [0]) -> FluorescenceAIModelBoundary:
    return FluorescenceAIModelBoundary.mock(
        _labels_for_image(panel, uid, channels=channels),
        model_commercial_use_status="restricted",
        model_license_summary="restricted mock boundary",
    )


def _ai_settings() -> FluorescenceAIAssistedSettings:
    return FluorescenceAIAssistedSettings(min_object_area_px=4, max_object_area_px=120)


def _smart_settings() -> FluorescenceSmartSettings:
    return FluorescenceSmartSettings(
        background_radius_px=7,
        min_object_area_px=4,
        max_object_area_px=120,
        noise_rejection_level=1.0,
    )


def _run_ai(panel: FluorescencePanel, image_uid: str, channels: list[int] | None = None) -> list[dict]:
    return panel.run_ai_assisted_analysis(
        image_uid,
        channel_indices=channels or [0],
        settings=_ai_settings(),
        model_boundary=_mock_boundary(panel, image_uid, channels=channels or [0]),
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def test_ai_assisted_statistics_collects_only_ai_storage(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    ai_record = _run_ai(panel, uid, [0])[0]
    panel.analysis["object_counts"] = [
        {"count_uuid": "traditional-count", "image_uid": uid, "channel_index": 0, "channel_name": "DAPI", "count": 99}
    ]
    panel.analysis["measurements"] = [
        {
            "measurement_uuid": "traditional-roi",
            "type": "roi_intensity",
            "image_uuid": uid,
            "results_table": [{"channel_index": 0, "channel": "DAPI", "mean": 999.0}],
        }
    ]
    panel.analysis["fluorescence_smart_assisted_measurements"] = [
        {"smart_measurement_uuid": "smart", "image_uid": uid, "channel_index": 0, "smart_object_count": 77}
    ]

    rows = collect_fluoro_stat_values(panel, source="ai_assisted", metric="ai_object_count")

    assert len(rows) == 1
    assert rows[0]["source"] == "ai_assisted"
    assert rows[0]["value"] == float(ai_record["ai_object_count"])
    assert rows[0]["ai_measurement_uuids"] == [ai_record["ai_measurement_uuid"]]
    assert "ai_assisted" in FLUOR_STAT_METRICS
    assert "ai_assisted" in dict(FLUOR_STAT_SOURCES).values()


def test_ai_assisted_statistics_do_not_read_traditional_object_counts(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    panel.analysis["object_counts"] = [
        {"count_uuid": "traditional-count", "image_uid": uid, "channel_index": 0, "channel_name": "DAPI", "count": 99}
    ]

    rows = collect_fluoro_stat_values(panel, source="ai_assisted", metric="ai_object_count")

    assert rows == []


def test_ai_assisted_statistics_do_not_read_roi_measurements(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    panel.analysis["measurements"] = [
        {
            "measurement_uuid": "traditional-roi",
            "type": "roi_intensity",
            "image_uuid": uid,
            "results_table": [{"channel_index": 0, "channel": "DAPI", "mean": 999.0}],
        }
    ]

    rows = collect_fluoro_stat_values(panel, source="ai_assisted", metric="mean_intensity")

    assert rows == []


def test_ai_assisted_statistics_do_not_read_smart_assisted_measurements(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    panel.analysis["fluorescence_smart_assisted_measurements"] = [
        {"smart_measurement_uuid": "smart", "image_uid": uid, "channel_index": 0, "smart_object_count": 77}
    ]

    rows = collect_fluoro_stat_values(panel, source="ai_assisted", metric="ai_object_count")

    assert rows == []


def test_traditional_object_count_statistics_ignore_ai_channel(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    _run_ai(panel, uid, [0])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)

    rows = collect_fluoro_stat_values(panel, source="object_count", metric="count")

    assert len(rows) == 1
    assert rows[0]["source"] == "object_count"
    assert "ai_measurement_uuids" not in rows[0]


def test_roi_intensity_statistics_ignore_ai_channel(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    _run_ai(panel, uid, [0])
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 9, "y": 8, "w": 6, "h": 6}, [0])

    rows = collect_fluoro_stat_values(panel, source="roi_intensity", metric="mean")

    assert len(rows) == 1
    assert rows[0]["source"] == "roi_intensity"
    assert "ai_measurement_uuids" not in rows[0]


def test_smart_assisted_statistics_ignore_ai_channel(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    _run_ai(panel, uid, [0])
    panel.run_smart_assisted_analysis(uid, channel_indices=[0], settings=_smart_settings())

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="smart_object_count")

    assert len(rows) == 1
    assert rows[0]["source"] == "smart_assisted"
    assert "ai_measurement_uuids" not in rows[0]


def test_ai_assisted_statistics_separate_context_hashes(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    full_record = _run_ai(panel, uid, [0])[0]
    roi_record = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_ai_settings(),
        roi_type="rectangle",
        geometry={"x": 8, "y": 8, "w": 8, "h": 8},
        model_boundary=_mock_boundary(panel, uid),
    )[0]

    rows = collect_fluoro_stat_values(panel, source="ai_assisted", metric="ai_object_count")

    assert full_record["metadata"]["effective_settings_hash"] == roi_record["metadata"]["effective_settings_hash"]
    assert full_record["ai_panel_context_hash"] != roi_record["ai_panel_context_hash"]
    assert len(rows) == 2
    assert {row["ai_panel_context_hash"] for row in rows} == {
        full_record["ai_panel_context_hash"],
        roi_record["ai_panel_context_hash"],
    }
    assert all("ai_panel_context_hash" in row["sample_key"] for row in rows)


def test_ai_assisted_distribution_rows_use_assumed_um2_series_per_sample(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    ai_record = _run_ai(panel, uid, [0])[0]

    rows = collect_fluoro_distribution_rows(panel, source="ai_assisted")

    scale = ai_record["measurement_scale_bar"]
    assert scale["enabled"] is True
    assert scale["calibration_source"] == "assumed"
    assert scale.get("assumed") is True or scale.get("calibrated") is False
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "ai_assisted"
    assert row["metric"] == "object_area_distribution_px"
    assert row["unit"] == "px^2"
    assert row["values"] == ai_record["object_area_distribution_px"]
    assert row["lane_label"].startswith(image["source_name"])


def test_ai_assisted_distribution_rows_use_calibrated_um2_series_per_sample(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    scale = panel.update_measurement_scale_bar(uid, enabled=True, um_per_px=0.5, calibration_source="manual")
    ai_record = _run_ai(panel, uid, [0])[0]

    rows = collect_fluoro_distribution_rows(panel, source="ai_assisted")

    assert scale["enabled"] is True
    assert scale["calibration_source"] == "manual"
    assert scale["um_per_px"] == 0.5
    assert len(rows) == 1
    row = rows[0]
    assert row["metric"] == "object_area_distribution_um2"
    assert row["unit"] == "um^2"
    assert row["values"] == ai_record["object_area_distribution_um2"]


def test_ai_assisted_distribution_rows_use_px2_series_when_scale_is_disabled(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    scale = panel.update_measurement_scale_bar(uid, enabled=False, um_per_px=0.5, calibration_source="manual")
    ai_record = _run_ai(panel, uid, [0])[0]

    rows = collect_fluoro_distribution_rows(panel, source="ai_assisted")

    assert scale["enabled"] is False
    assert scale["um_per_px"] == 0.5
    assert len(rows) == 1
    row = rows[0]
    assert row["metric"] == "object_area_distribution_px"
    assert row["unit"] == "px^2"
    assert row["values"] == ai_record["object_area_distribution_px"]


def test_ai_assisted_export_writes_required_model_boundary_fields(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    ai_record = _run_ai(panel, uid, [0])[0]

    csv_path = fluoro_exporter.export_fluoro_ai_assisted_measurements(panel, tmp_path / "figure.png")
    fieldnames, rows = _read_csv(csv_path)

    assert fieldnames == [
        "image_uid",
        "image_name",
        "channel_index",
        "channel_name",
        "analysis_mode",
        "analysis_version",
        "ai_measurement_uuid",
        "ai_object_count",
        "total_object_area_px",
        "mean_object_area_px",
        "mean_intensity",
        "integrated_intensity",
        "effective_settings_hash",
        "ai_panel_context_hash",
        "model_id",
        "model_version",
        "ai_pack_postprocess",
        "model_commercial_use_status",
        "model_license_summary",
        "model_execution_mode",
        "roi_or_geometry",
        "roi_reconstructable",
        "warnings",
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["image_uid"] == uid
    assert row["analysis_mode"] == "ai_assisted"
    assert row["analysis_version"] == "v8_1_ai_fluorescence"
    assert row["ai_measurement_uuid"] == ai_record["ai_measurement_uuid"]
    assert int(row["ai_object_count"]) == ai_record["ai_object_count"]
    assert row["effective_settings_hash"].startswith("sha256:")
    assert row["ai_panel_context_hash"] == ai_record["ai_panel_context_hash"]
    assert row["model_id"] == "cellpose_cyto3"
    assert row["model_commercial_use_status"] == "restricted"
    assert row["model_license_summary"] == "restricted mock boundary"
    assert row["model_execution_mode"] == "mock"
    assert json.loads(row["roi_or_geometry"])["type"] == "full_image"
    assert row["roi_reconstructable"] == "True"


def test_ai_assisted_export_does_not_modify_existing_roi_export(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    _run_ai(panel, uid, [0])
    panel.add_rectangle_roi(uid, 9, 8, 6, 6, channel_index=0)

    csv_path = export_fluoro_measurements(panel, tmp_path / "figure.png")
    fieldnames, rows = _read_csv(csv_path)

    assert fieldnames == ["image_uid", "image_name", "roi_id", "roi_type", "channel_index", "area_px", "area_um2", "mean", "integrated_density"]
    assert len(rows) == 1
    assert "ai_measurement_uuid" not in rows[0]


def test_ai_assisted_export_does_not_modify_existing_smart_export(tmp_path) -> None:
    panel, image = _panel_with_ai_image(tmp_path)
    uid = str(image["uid"])
    _run_ai(panel, uid, [0])
    panel.run_smart_assisted_analysis(uid, channel_indices=[0], settings=_smart_settings())

    csv_path = fluoro_exporter.export_fluoro_smart_assisted_measurements(panel, tmp_path / "figure.png")
    fieldnames, rows = _read_csv(csv_path)

    assert "smart_measurement_uuid" in fieldnames
    assert "ai_measurement_uuid" not in fieldnames
    assert len(rows) == 1
