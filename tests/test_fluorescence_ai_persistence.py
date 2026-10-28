from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import tifffile

from core.image_analysis.fluorescence_ai import FluorescenceAIAssistedSettings, FluorescenceAIModelBoundary
from core.image_analysis.fluorescence_smart import FluorescenceSmartSettings
from core.models import FluorescencePanel, Project


def _write_ai_tiff(path: Path) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = 180
    data[0, 25:31, 29:35] = 160
    data[1, 18:25, 20:27] = 260
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _panel_with_image(tmp_path) -> tuple[FluorescencePanel, dict]:
    image_path = _write_ai_tiff(tmp_path / "ai_cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
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


def test_ai_assisted_default_without_model_boundary_uses_local_heuristic_and_persists_records(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)

    records = panel.run_ai_assisted_analysis(str(image["uid"]), channel_indices=[0], settings=_ai_settings())

    assert len(records) == 1
    assert records[0]["ai_object_count"] == 2
    assert records[0]["metadata"]["model_execution_mode"] == "local_heuristic"
    assert panel.analysis.get("fluorescence_ai_assisted_measurements", []) == records
    assert image.get("fluorescence_ai_assisted_measurements", []) == records


def test_ai_assisted_explicit_mock_boundary_writes_independent_channel(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])

    records = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_ai_settings(),
        model_boundary=_mock_boundary(panel, uid, channels=[0]),
    )

    assert len(records) == 1
    record = records[0]
    assert record["type"] == "fluorescence_ai_assisted_measurement"
    assert record["analysis_mode"] == "ai_assisted"
    assert record["analysis_family"] == "fluorescence"
    assert record["analysis_version"] == "v8_1_ai_fluorescence"
    assert record["channel_index"] == 0
    assert record["ai_object_count"] == 2
    assert record["ai_measurement_uuid"]
    assert record["ai_panel_context_hash"].startswith("sha256:")
    assert record["metadata"]["model_execution_mode"] == "mock"
    assert record["metadata"]["model_commercial_use_status"] == "restricted"
    assert panel.analysis["fluorescence_ai_assisted_measurements"] == records
    assert image["fluorescence_ai_assisted_measurements"] == records


def test_ai_assisted_results_do_not_enter_traditional_object_counts(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])

    panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))

    assert panel.analysis.get("object_counts", []) == []
    assert image.get("object_counts", []) == []


def test_ai_assisted_results_do_not_enter_roi_measurements(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])

    panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))

    assert panel.analysis.get("measurements", []) == []
    assert image.get("measurements", []) == []


def test_ai_assisted_results_do_not_enter_smart_assisted_measurements(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])

    panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))

    assert panel.analysis.get("fluorescence_smart_assisted_measurements", []) == []
    assert image.get("fluorescence_smart_assisted_measurements", []) == []


def test_ai_assisted_results_round_trip_through_panel_dict(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    records = panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))

    restored = FluorescencePanel.from_dict(panel.to_dict())

    assert restored.analysis["fluorescence_ai_assisted_measurements"] == records
    assert restored.data["fluorescence_images"][0]["fluorescence_ai_assisted_measurements"] == records


def test_ai_assisted_results_round_trip_through_sfig(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    records = panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))
    project = Project(title="AI Fluoro Archive")
    project.add_panel(panel)
    target = tmp_path / "ai_fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    restored_panel = restored.panels[0]
    assert isinstance(restored_panel, FluorescencePanel)
    assert restored_panel.analysis["fluorescence_ai_assisted_measurements"] == records
    assert restored_panel.data["fluorescence_images"][0]["fluorescence_ai_assisted_measurements"] == records


def test_old_sfig_without_ai_assisted_results_loads(tmp_path) -> None:
    panel, _image = _panel_with_image(tmp_path)
    project = Project(title="Old Fluoro Archive")
    project.add_panel(panel)
    target = tmp_path / "old_fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    assert isinstance(restored.panels[0], FluorescencePanel)
    assert restored.panels[0].analysis.get("fluorescence_ai_assisted_measurements", []) == []


def test_clear_ai_assisted_measurements_does_not_clear_traditional_or_smart_results(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    panel.run_smart_assisted_analysis(uid, channel_indices=[0], settings=_smart_settings())
    panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))

    assert panel.clear_ai_assisted_measurements(uid) == 1

    assert panel.analysis["fluorescence_ai_assisted_measurements"] == []
    assert len(panel.analysis["object_counts"]) == 1
    assert len(panel.analysis["measurements"]) == 1
    assert len(panel.analysis["fluorescence_smart_assisted_measurements"]) == 1


def test_delete_ai_assisted_measurement_removes_only_ai_storage(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    panel.run_smart_assisted_analysis(uid, channel_indices=[0], settings=_smart_settings())
    ai_record = panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))[0]

    removed = panel.delete_ai_assisted_measurement(str(ai_record["ai_measurement_uuid"]))

    assert removed is True
    assert panel.analysis["fluorescence_ai_assisted_measurements"] == []
    assert image["fluorescence_ai_assisted_measurements"] == []
    assert len(panel.analysis["object_counts"]) == 1
    assert len(panel.analysis["measurements"]) == 1
    assert len(panel.analysis["fluorescence_smart_assisted_measurements"]) == 1


def test_external_roi_mask_without_geometry_is_marked_transient(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    data = panel.image_data(uid)
    assert data is not None
    roi_mask = np.zeros(data.shape[1:], dtype=bool)
    roi_mask[8:14, 9:15] = True

    record = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_ai_settings(),
        roi_mask=roi_mask,
        model_boundary=_mock_boundary(panel, uid),
    )[0]

    assert "external_roi_mask_not_persisted" in record["warnings"]
    assert record["roi_or_geometry"]["type"] == "mask"
    assert record["roi_or_geometry"]["reconstructable"] is False
    assert record["roi_or_geometry"]["transient"] is True


def test_ai_panel_context_hash_changes_with_roi_or_preprocessing_context(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    full_record = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_ai_settings(),
        model_boundary=_mock_boundary(panel, uid),
    )[0]
    roi_record = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=_ai_settings(),
        roi_type="rectangle",
        geometry={"x": 8, "y": 8, "w": 8, "h": 8},
        model_boundary=_mock_boundary(panel, uid),
    )[0]

    assert full_record["metadata"]["effective_settings_hash"] == roi_record["metadata"]["effective_settings_hash"]
    assert full_record["ai_panel_context_hash"] != roi_record["ai_panel_context_hash"]


def test_traditional_smart_and_ai_results_coexist_through_sfig_roundtrip(tmp_path) -> None:
    panel, image = _panel_with_image(tmp_path)
    uid = str(image["uid"])
    roi_record = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    object_record = panel.count_channel_objects(uid, [0], method="manual", manual_value=50, min_area_px=4)[0]
    smart_record = panel.run_smart_assisted_analysis(uid, channel_indices=[0], settings=_smart_settings())[0]
    ai_record = panel.run_ai_assisted_analysis(uid, channel_indices=[0], settings=_ai_settings(), model_boundary=_mock_boundary(panel, uid))[0]
    project = Project(title="All Fluoro Channels")
    project.add_panel(panel)
    target = tmp_path / "all_channels.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target).panels[0]

    assert restored.analysis["measurements"] == [roi_record]
    assert restored.analysis["object_counts"] == [object_record]
    assert restored.analysis["fluorescence_smart_assisted_measurements"] == [smart_record]
    assert restored.analysis["fluorescence_ai_assisted_measurements"] == [ai_record]


def test_importing_fluorescence_panel_does_not_import_onnxruntime() -> None:
    code = "import sys; from core.models import FluorescencePanel; assert 'onnxruntime' not in sys.modules"
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_constructing_fluorescence_panel_does_not_require_ai_runtime() -> None:
    panel = FluorescencePanel(panel_id="Figure1C")

    assert panel.panel_type == "fluorescence"
