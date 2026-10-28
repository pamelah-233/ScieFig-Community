from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import tifffile

from core.export import export_fluoro_measurements, fluoro_exporter
from core.fluoro_statistics import FLUOR_STAT_METRICS, FLUOR_STAT_SOURCES, collect_fluoro_stat_values, compute_fluoro_statistics
from core.image_analysis.fluorescence_smart import FluorescenceSmartSettings
from core.models import FluorescencePanel


def _write_smart_tiff(path: Path, *, ch0_value: int = 180, ch1_value: int = 260) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = ch0_value
    data[0, 25:31, 29:35] = max(1, ch0_value - 20)
    data[1, 18:25, 20:27] = ch1_value
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _panel_with_smart_image(tmp_path, filename: str = "smart_cells.tif") -> tuple[FluorescencePanel, dict]:
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_smart_tiff(tmp_path / filename))
    return panel, image


def _smart_settings() -> FluorescenceSmartSettings:
    return FluorescenceSmartSettings(
        background_radius_px=7,
        min_object_area_px=4,
        max_object_area_px=120,
        noise_rejection_level=1.0,
    )


def _run_smart(panel: FluorescencePanel, image_uid: str, channels: list[int] | None = None) -> list[dict]:
    return panel.run_smart_assisted_analysis(
        image_uid,
        channel_indices=channels or [0],
        settings=_smart_settings(),
    )


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def test_smart_fluorescence_statistics_collects_only_smart_channel(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    smart_record = _run_smart(panel, uid, [0])[0]
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

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="smart_object_count")

    assert len(rows) == 1
    assert rows[0]["source"] == "smart_assisted"
    assert rows[0]["value"] == float(smart_record["smart_object_count"])
    assert rows[0]["smart_measurement_uuids"] == [smart_record["smart_measurement_uuid"]]
    assert "smart_assisted" in FLUOR_STAT_METRICS
    assert dict(FLUOR_STAT_SOURCES) == {
        "ROI intensity": "roi_intensity",
        "Object count": "object_count",
        "AI-assisted": "ai_assisted",
    }


def test_traditional_object_count_statistics_ignore_smart_channel(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    _run_smart(panel, uid, [0])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)

    rows = collect_fluoro_stat_values(panel, source="object_count", metric="count")

    assert len(rows) == 1
    assert rows[0]["source"] == "object_count"
    assert rows[0]["value"] == 2.0
    assert rows[0]["count_uuids"]
    assert "smart_measurement_uuids" not in rows[0]


def test_roi_intensity_statistics_ignore_smart_channel(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    _run_smart(panel, uid, [0])
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 9, "y": 8, "w": 6, "h": 6}, [0])

    rows = collect_fluoro_stat_values(panel, source="roi_intensity", metric="mean")

    assert len(rows) == 1
    assert rows[0]["source"] == "roi_intensity"
    assert rows[0]["value"] == 180.0
    assert rows[0]["measurement_uuids"]
    assert "smart_measurement_uuids" not in rows[0]


def test_smart_fluorescence_statistics_aggregates_per_image_channel(tmp_path) -> None:
    panel, first_image = _panel_with_smart_image(tmp_path, "smart_cells_a.tif")
    first_uid = str(first_image["uid"])
    _run_smart(panel, first_uid, [0, 1])
    _run_smart(panel, first_uid, [0])
    second_image = panel.import_image(_write_smart_tiff(tmp_path / "smart_cells_b.tif", ch0_value=220, ch1_value=280))
    second_uid = str(second_image["uid"])
    _run_smart(panel, second_uid, [0])

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="smart_object_count")

    assert len(rows) == 3
    assert [(row["image_uid"], row["channel_index"]) for row in rows] == [(first_uid, 0), (first_uid, 1), (second_uid, 0)]
    assert [row["n_measurements"] for row in rows] == [1, 1, 1]
    assert all(row["aggregation"] == "mean_per_image" for row in rows)
    assert rows[0]["sample_key"] == f"{first_uid}:smart_assisted:smart_object_count:ch0:smart_panel_context_hash={rows[0]['smart_panel_context_hash']}"


def test_smart_fluorescence_statistics_separates_panel_context_hashes(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    panel.set_black_balance_roi(uid, {"x": 9, "y": 8, "w": 6, "h": 6})
    raw_record = panel.run_smart_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_smart_settings(),
        use_black_balance=False,
    )[0]
    balanced_record = panel.run_smart_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_smart_settings(),
        use_black_balance=True,
    )[0]

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="smart_object_count")

    assert raw_record["metadata"]["effective_settings_hash"] == balanced_record["metadata"]["effective_settings_hash"]
    assert raw_record["metadata"]["smart_panel_context_hash"] != balanced_record["metadata"]["smart_panel_context_hash"]
    assert len(rows) == 2
    assert {row["smart_panel_context_hash"] for row in rows} == {
        raw_record["metadata"]["smart_panel_context_hash"],
        balanced_record["metadata"]["smart_panel_context_hash"],
    }
    assert all(row["n_measurements"] == 1 for row in rows)
    assert all(row["effective_settings_hash"] == raw_record["metadata"]["effective_settings_hash"] for row in rows)
    assert all("smart_panel_context_hash" in row["sample_key"] for row in rows)
    assert len({row["lane_label"] for row in rows}) == 2
    assert any(row["input_preprocessing_context"]["use_black_balance"] is True for row in rows)
    assert any(row["input_preprocessing_context"]["use_black_balance"] is False for row in rows)


def test_smart_fluorescence_statistics_aggregates_identical_panel_context_only(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    first_record = _run_smart(panel, uid, [0])[0]
    second_record = _run_smart(panel, uid, [0])[0]

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="smart_object_count")

    assert first_record["metadata"]["smart_panel_context_hash"] == second_record["metadata"]["smart_panel_context_hash"]
    assert len(rows) == 1
    assert rows[0]["smart_panel_context_hash"] == first_record["metadata"]["smart_panel_context_hash"]
    assert rows[0]["n_measurements"] == 1
    assert rows[0]["smart_measurement_uuids"] == [
        second_record["smart_measurement_uuid"],
    ]


def test_smart_fluorescence_statistics_collects_background_estimate_from_black_balance(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    panel.set_black_balance_roi(uid, {"x": 9, "y": 8, "w": 6, "h": 6})
    panel.run_smart_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_smart_settings(),
        use_black_balance=True,
    )

    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="background_estimate")

    assert len(rows) == 1
    assert rows[0]["source"] == "smart_assisted"
    assert rows[0]["metric"] == "background_estimate"
    assert rows[0]["value"] == 180.0


def test_smart_fluorescence_statistics_persists_result_without_polluting_traditional_source(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    _run_smart(panel, uid, [0, 1])
    rows = collect_fluoro_stat_values(panel, source="smart_assisted", metric="mean_intensity")
    group_assignments = {rows[0]["lane_label"]: "Control", rows[1]["lane_label"]: "Treatment"}

    result = compute_fluoro_statistics(
        panel,
        source="smart_assisted",
        metric="mean_intensity",
        group_assignments=group_assignments,
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert panel.analysis["fluoro_statistics"] is result
    assert result["source"] == "smart_assisted"
    assert result["metric"] == "mean_intensity"
    assert panel.analysis.get("object_counts", []) == []
    assert panel.analysis.get("measurements", []) == []


def test_smart_fluorescence_export_contains_mode_channel_count_area_intensity(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    smart_record = _run_smart(panel, uid, [0])[0]

    csv_path = fluoro_exporter.export_fluoro_smart_assisted_measurements(panel, tmp_path / "figure.png")
    fieldnames, rows = _read_csv(csv_path)

    assert fieldnames == [
        "image_uid",
        "image_name",
        "channel_index",
        "channel_name",
        "analysis_mode",
        "analysis_version",
        "smart_measurement_uuid",
        "smart_object_count",
        "total_object_area_px",
        "mean_object_area_px",
        "mean_intensity",
        "integrated_intensity",
        "background_estimate",
        "effective_settings_hash",
        "smart_panel_context_hash",
        "core_effective_settings_hash",
        "input_preprocessing_context",
        "roi_or_geometry",
        "roi_reconstructable",
        "warnings",
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["image_uid"] == uid
    assert row["image_name"] == image["source_name"]
    assert row["channel_index"] == "0"
    assert row["analysis_mode"] == "smart_assisted"
    assert row["analysis_version"] == "v8_0_smart_fluorescence"
    assert row["smart_measurement_uuid"] == smart_record["smart_measurement_uuid"]
    assert int(row["smart_object_count"]) == smart_record["smart_object_count"]
    assert float(row["total_object_area_px"]) == float(smart_record["total_object_area_px"])
    assert json.loads(row["background_estimate"])["method"] == "rolling_percentile"
    assert row["effective_settings_hash"].startswith("sha256:")
    assert row["core_effective_settings_hash"] == row["effective_settings_hash"]
    assert row["smart_panel_context_hash"] == smart_record["metadata"]["smart_panel_context_hash"]
    assert json.loads(row["input_preprocessing_context"])["core_effective_settings_hash"] == row["effective_settings_hash"]
    assert json.loads(row["roi_or_geometry"])["type"] == "full_image"
    assert row["roi_reconstructable"] == "True"


def test_smart_fluorescence_export_handles_missing_optional_numeric_fields(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    panel.analysis["fluorescence_smart_assisted_measurements"] = [
        {
            "image_uid": uid,
            "channel_index": None,
            "channel_name": "DAPI",
            "analysis_mode": "smart_assisted",
            "analysis_version": "v8_0_smart_fluorescence",
            "smart_measurement_uuid": "partial-smart-record",
            "smart_object_count": None,
            "total_object_area_px": None,
            "mean_object_area_px": None,
            "mean_intensity": None,
            "integrated_intensity": None,
            "background_estimate": None,
            "metadata": {},
            "warnings": None,
        }
    ]

    csv_path = fluoro_exporter.export_fluoro_smart_assisted_measurements(panel, tmp_path / "figure.png")
    _fieldnames, rows = _read_csv(csv_path)

    assert len(rows) == 1
    row = rows[0]
    assert row["channel_index"] == ""
    assert row["smart_object_count"] == ""
    assert row["total_object_area_px"] == ""
    assert row["mean_object_area_px"] == ""
    assert row["mean_intensity"] == ""
    assert row["integrated_intensity"] == ""
    assert row["background_estimate"] == "{}"
    assert row["input_preprocessing_context"] == "{}"
    assert row["roi_or_geometry"] == "{}"
    assert row["warnings"] == "[]"


def test_smart_fluorescence_export_does_not_modify_existing_roi_measurement_export(tmp_path) -> None:
    panel, image = _panel_with_smart_image(tmp_path)
    uid = str(image["uid"])
    panel.add_rectangle_roi(uid, 9, 8, 6, 6, channel_index=0)
    _run_smart(panel, uid, [0])

    csv_path = export_fluoro_measurements(panel, tmp_path / "figure.png")
    fieldnames, rows = _read_csv(csv_path)

    assert fieldnames == ["image_uid", "image_name", "roi_id", "roi_type", "channel_index", "area_px", "area_um2", "mean", "integrated_density"]
    assert len(rows) == 1
    assert rows[0]["roi_type"] == "rectangle"
    assert "smart_measurement_uuid" not in rows[0]
