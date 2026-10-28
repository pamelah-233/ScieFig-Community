from __future__ import annotations

from pathlib import Path

import numpy as np
import tifffile

from core.image_analysis.fluorescence_smart import FluorescenceSmartSettings
from core.models import FluorescencePanel, Project


def _write_smart_tiff(path: Path) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = 180
    data[0, 25:31, 29:35] = 160
    data[1, 18:25, 20:27] = 260
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _panel_with_image(tmp_path) -> tuple[FluorescencePanel, dict]:
    image_path = _write_smart_tiff(tmp_path / "smart_cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
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


def test_smart_assisted_panel_method_writes_independent_channel(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    records = _run_smart(panel, str(image["uid"]), [0])

    assert len(records) == 1
    record = records[0]
    assert record["type"] == "fluorescence_smart_assisted_measurement"
    assert record["analysis_mode"] == "smart_assisted"
    assert record["analysis_family"] == "fluorescence"
    assert record["analysis_version"] == "v8_0_smart_fluorescence"
    assert record["channel_index"] == 0
    assert record["channel_name"] == "DAPI"
    assert record["smart_object_count"] == 2
    assert record["total_object_area_px"] > 0
    assert record["mean_object_area_px"] > 0
    assert record["mean_intensity"] > 0
    assert record["integrated_intensity"] > 0
    assert record["smart_measurement_uuid"]
    assert record["metadata"]["effective_settings_hash"].startswith("sha256:")
    assert any(entry.action == "run_fluorescence_smart_assisted_analysis" for entry in panel.audit_log)
    assert panel.analysis["fluorescence_smart_assisted_measurements"] == records


def test_smart_assisted_results_do_not_enter_traditional_object_counts(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    _run_smart(panel, str(image["uid"]), [0])

    assert panel.analysis.get("object_counts", []) == []
    assert image.get("object_counts", []) == []


def test_smart_assisted_results_do_not_enter_roi_measurements(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    _run_smart(panel, str(image["uid"]), [0])

    assert panel.analysis.get("measurements", []) == []
    assert image.get("measurements", []) == []


def test_smart_assisted_image_local_storage_matches_panel_storage(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    records = _run_smart(panel, str(image["uid"]), [0, 1])

    assert panel.analysis["fluorescence_smart_assisted_measurements"] == records
    assert image["fluorescence_smart_assisted_measurements"] == records
    assert {record["channel_index"] for record in records} == {0, 1}


def test_smart_assisted_black_balance_changes_panel_context_hash_not_core_hash(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    panel.set_black_balance_roi(uid, {"x": 0, "y": 0, "w": 4, "h": 4})

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

    raw_metadata = raw_record["metadata"]
    balanced_metadata = balanced_record["metadata"]
    assert raw_metadata["effective_settings_hash"] == balanced_metadata["effective_settings_hash"]
    assert raw_metadata["smart_panel_context_hash"] != balanced_metadata["smart_panel_context_hash"]
    assert raw_metadata["input_preprocessing_context"]["use_black_balance"] is False
    assert balanced_metadata["input_preprocessing_context"]["use_black_balance"] is True
    assert balanced_metadata["input_preprocessing_context"]["image_background_correction"]["mode"] == "black_balance"


def test_smart_assisted_geometry_roi_persists_coordinates(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    geometry = {"x": 8, "y": 8, "w": 8, "h": 8}
    records = panel.run_smart_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_smart_settings(),
        roi_type="rectangle",
        geometry=geometry,
    )
    project = Project(title="Smart Fluoro ROI Archive")
    project.add_panel(panel)
    target = tmp_path / "smart_fluoro_roi.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    assert records[0]["roi_or_geometry"]["type"] == "rectangle"
    assert records[0]["roi_or_geometry"]["coordinates_px"] == geometry
    assert records[0]["roi_or_geometry"]["mask_path"] is None
    assert records[0]["roi_or_geometry"]["area_px"] > 0
    restored_roi = restored.panels[0].analysis["fluorescence_smart_assisted_measurements"][0]["roi_or_geometry"]
    assert restored_roi["coordinates_px"] == geometry


def test_smart_assisted_external_roi_mask_records_non_reconstructable_warning(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    data = panel.image_data(uid)
    assert data is not None
    roi_mask = np.zeros(data.shape[1:], dtype=bool)
    roi_mask[8:14, 9:15] = True

    record = panel.run_smart_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_smart_settings(),
        roi_mask=roi_mask,
    )[0]

    assert "external_roi_mask_not_persisted" in record["warnings"]
    assert record["roi_or_geometry"]["type"] == "mask"
    assert record["roi_or_geometry"]["mask_path"] is None
    assert "mask" not in record["roi_or_geometry"]
    assert record["roi_or_geometry"]["reconstructable"] is False
    assert record["roi_or_geometry"]["transient"] is True
    assert record["metadata"]["input_preprocessing_context"]["roi_or_geometry"]["reconstructable"] is False


def test_smart_assisted_duplicate_channel_indices_are_deduped(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    records = _run_smart(panel, str(image["uid"]), [0, 0])

    assert len(records) == 1
    assert [record["channel_index"] for record in records] == [0]


def test_clear_traditional_object_counts_does_not_clear_smart_results(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    smart_records = _run_smart(panel, uid, [0])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)

    assert panel.clear_object_counts(uid) == 1

    assert panel.analysis["object_counts"] == []
    assert panel.analysis["fluorescence_smart_assisted_measurements"] == smart_records
    assert image["fluorescence_smart_assisted_measurements"] == smart_records


def test_clear_roi_measurements_does_not_clear_smart_results(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    smart_records = _run_smart(panel, uid, [0])
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])

    assert panel.clear_measurements(uid, measurement_type="roi_intensity") == 1

    assert panel.analysis["measurements"] == []
    assert panel.analysis["fluorescence_smart_assisted_measurements"] == smart_records
    assert image["fluorescence_smart_assisted_measurements"] == smart_records


def test_clear_smart_assisted_measurements_does_not_clear_traditional_results(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    _run_smart(panel, uid, [0])

    assert panel.clear_smart_assisted_measurements(uid) == 1

    assert panel.analysis["fluorescence_smart_assisted_measurements"] == []
    assert image["fluorescence_smart_assisted_measurements"] == []
    assert len(panel.analysis["object_counts"]) == 1
    assert len(panel.analysis["measurements"]) == 1


def test_delete_smart_assisted_measurement_removes_only_smart_storage(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    smart_record = _run_smart(panel, uid, [0])[0]
    panel.analysis["fluoro_statistics"] = {"source": "smart_assisted", "metric": "smart_object_count"}

    removed = panel.delete_smart_assisted_measurement(str(smart_record["smart_measurement_uuid"]))

    assert removed is True
    assert panel.analysis["fluorescence_smart_assisted_measurements"] == []
    assert image["fluorescence_smart_assisted_measurements"] == []
    assert len(panel.analysis["object_counts"]) == 1
    assert len(panel.analysis["measurements"]) == 1
    assert "fluoro_statistics" not in panel.analysis


def test_smart_assisted_results_save_load_roundtrip(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    records = _run_smart(panel, uid, [0, 1])
    project = Project(title="Smart Fluoro Archive")
    project.add_panel(panel)
    target = tmp_path / "smart_fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    restored_panel = restored.panels[0]
    assert isinstance(restored_panel, FluorescencePanel)
    assert restored_panel.analysis["fluorescence_smart_assisted_measurements"] == records
    restored_image = restored_panel.data["fluorescence_images"][0]
    assert restored_image["fluorescence_smart_assisted_measurements"] == records
    assert Path(str(restored_image["source_path"])).exists()


def test_old_fluorescence_panel_without_smart_fields_loads(tmp_path) -> None:
    panel, _image = _panel_with_image(tmp_path)
    project = Project(title="Old Fluoro Archive")
    project.add_panel(panel)
    target = tmp_path / "old_fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    restored_panel = restored.panels[0]
    assert isinstance(restored_panel, FluorescencePanel)
    assert restored_panel.analysis.get("fluorescence_smart_assisted_measurements", []) == []
    assert restored_panel.clear_smart_assisted_measurements() == 0


def test_smart_assisted_missing_optional_fields_do_not_crash_load(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    _run_smart(panel, uid, [0])
    for record in panel.analysis["fluorescence_smart_assisted_measurements"]:
        record.pop("metadata", None)
        record.pop("object_table", None)
        record.pop("warnings", None)
    for record in image["fluorescence_smart_assisted_measurements"]:
        record.pop("metadata", None)
        record.pop("object_table", None)
        record.pop("warnings", None)
    project = Project(title="Partial Smart Fluoro Archive")
    project.add_panel(panel)
    target = tmp_path / "partial_smart_fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    restored_panel = restored.panels[0]
    assert isinstance(restored_panel, FluorescencePanel)
    assert restored_panel.analysis["fluorescence_smart_assisted_measurements"][0]["smart_measurement_uuid"]
    assert restored_panel.clear_smart_assisted_measurements(uid) == 1
