from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import numpy as np
import tifffile
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QGraphicsPathItem

from core.image_analysis.fluorescence_ai import FluorescenceAIAssistedSettings, FluorescenceAIModelBoundary
from core.models import FluorescencePanel, Project
from ui.panels.fluorescence_workspace import FluorescenceWorkspace


def _write_ai_tiff(path: Path) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = 180
    data[0, 25:31, 29:35] = 160
    data[1, 18:25, 20:27] = 260
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _labels_for_image(panel: FluorescencePanel, uid: str) -> dict[int, np.ndarray]:
    data = panel.image_data(uid)
    assert data is not None
    mask = np.zeros(data.shape[1:], dtype=np.int32)
    mask[8:14, 9:15] = 1
    mask[25:31, 29:35] = 2
    return {0: mask}


def test_calibrated_save_roundtrip_result_click_preserves_roi_contour_identity(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(_write_ai_tiff(tmp_path / "preserve_cells.tif"))
    uid = str(image["uid"])
    panel.update_measurement_scale_bar(uid, enabled=True, um_per_px=0.5, calibration_source="manual")
    roi = panel.add_roi(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, channel_index=0)
    measurement = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 8, "y": 8, "w": 8, "h": 8}, [0])
    records = panel.run_ai_assisted_analysis(
        uid,
        channel_indices=[0],
        settings=FluorescenceAIAssistedSettings(min_object_area_px=4, max_object_area_px=120),
        model_boundary=FluorescenceAIModelBoundary.mock(
            _labels_for_image(panel, uid),
            model_commercial_use_status="restricted",
            model_license_summary="restricted mock boundary",
        ),
    )
    original_roi_id = roi["roi_id"]
    original_roi_geometry = deepcopy(roi["geometry"])
    original_measurement_uuid = measurement["measurement_uuid"]
    original_objects = [
        {
            "object_id": detected["object_id"],
            "contour_points_px": deepcopy(detected["contour_points_px"]),
        }
        for record in records
        for detected in record.get("object_table", [])
    ]
    assert original_objects
    assert all(item["contour_points_px"] for item in original_objects)

    project = Project(title="Scale Phase1 Preserve")
    project.add_panel(panel)
    target = tmp_path / "preserve.sfig"
    project.save_sfig(target)
    restored_project = Project.load_sfig(target)
    restored = restored_project.panels[0]
    assert isinstance(restored, FluorescencePanel)
    restored_image = restored.images()[0]
    restored_roi = restored_image["rois"][0]
    restored_records = restored.analysis["fluorescence_ai_assisted_measurements"]
    restored_objects = [
        {
            "object_id": detected["object_id"],
            "contour_points_px": detected["contour_points_px"],
        }
        for record in restored_records
        for detected in record.get("object_table", [])
    ]
    assert restored_roi["roi_id"] == original_roi_id
    assert restored_roi["geometry"] == original_roi_geometry
    assert restored.analysis["measurements"][0]["measurement_uuid"] == original_measurement_uuid
    assert restored_objects == original_objects

    workspace = FluorescenceWorkspace()
    try:
        workspace.set_panel(restored)
        workspace.set_step(2)
        app.processEvents()
        assert workspace.measurement_table.rowCount() >= 1
        selected = False
        for row in range(workspace.measurement_table.rowCount()):
            item = workspace.measurement_table.item(row, 0)
            if item is not None and str(item.data(Qt.ItemDataRole.UserRole) or "") == original_measurement_uuid:
                workspace.measurement_table.selectRow(row)
                selected = True
                break
        assert selected
        app.processEvents()
        assert workspace.canvas.active_measurement_overlay_uuid == original_measurement_uuid

        workspace._set_measure_mode("ai_assisted")
        app.processEvents()
        assert workspace.ai_results_table.rowCount() == len(original_objects)
        workspace.ai_results_table.selectRow(0)
        app.processEvents()
        overlay_ids = [
            item.data(2)
            for item in workspace.canvas.scene.items()
            if isinstance(item, QGraphicsPathItem) and item.data(0) == "fluoro_ai_object_overlay"
        ]
        assert sorted(str(value) for value in overlay_ids) == sorted(str(item["object_id"]) for item in original_objects)
        clicked = restored_image["fluorescence_ai_assisted_measurements"][0]["object_table"]
        assert [row["object_id"] for row in clicked] == [item["object_id"] for item in original_objects]
        assert [row["contour_points_px"] for row in clicked] == [item["contour_points_px"] for item in original_objects]
        assert restored_image["rois"][0]["roi_id"] == original_roi_id
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
