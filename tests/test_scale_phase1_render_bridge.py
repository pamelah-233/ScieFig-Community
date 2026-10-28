from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import tifffile
from PyQt6.QtWidgets import QApplication

from core.models.fluoro_panel import AI_ASSISTED_MEASUREMENTS_KEY, FluorescencePanel, ImageMetadata
from ui.panels.fluorescence_workspace import FluorescenceWorkspace


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write_cyx(path: Path, *, width: int = 100, height: int = 80) -> Path:
    data = np.zeros((2, height, width), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _scale_bar_items(workspace: FluorescenceWorkspace, role: str | None = None) -> list:
    items = []
    for scene_item in workspace.canvas.scene.items():
        for item in [scene_item, *scene_item.childItems()]:
            if str(item.data(0) or "") != "fluoro_scale_bar":
                continue
            if role is not None and item.data(3) != role:
                continue
            items.append(item)
    return items


def test_manual_display_override_beats_metadata_on_canvas(tmp_path: Path) -> None:
    app = _app()
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-render-bridge-manual")
    image = panel.import_image(_write_cyx(tmp_path / "manual_override.tif"))
    uid = str(image["uid"])
    image["metadata"] = ImageMetadata.from_dict({**dict(image["metadata"]), "pixel_size_um": 0.5}).to_dict()
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, calibration_source="manual")
    scale_before = deepcopy(image.get("measurement_scale_bar", {}))
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_length_spin.setValue(50.0)
    workspace.scale_bar_manual_um_per_px_spin.setValue(0.25)
    workspace.scale_bar_apply_current_button.click()
    app.processEvents()

    image = panel.active_image()
    assert 'manual_um_per_px' not in image['scale_bar']
    assert image["layout_scale_bars"][0]["manual_um_per_px"] == pytest.approx(0.25)
    assert image["measurement_scale_bar"] == scale_before

    line = _scale_bar_items(workspace, "line")[0]
    text = _scale_bar_items(workspace, "text")[0]
    parent = line.parentItem()
    display_width = float(parent.rect().width() if hasattr(parent, "rect") else parent.boundingRect().width())
    source_width = float(image["metadata"]["width_px"])
    expected_source_px = 50.0 / 0.25
    metadata_source_px = 50.0 / 0.5
    assert line.line().length() == pytest.approx(expected_source_px * display_width / source_width, rel=1e-4)
    assert line.line().length() != pytest.approx(metadata_source_px * display_width / source_width, rel=1e-4)
    label = text.toPlainText()
    assert "50" in label
    assert "um" in label.lower()
    assert "px" not in label.lower()


def test_unknown_scale_bar_label_is_source_px_not_um(tmp_path: Path) -> None:
    app = _app()
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-render-bridge-unknown")
    image = panel.import_image(_write_cyx(tmp_path / "unknown_ruler.tif"))
    image["metadata"] = ImageMetadata.from_dict(
        {key: value for key, value in dict(image["metadata"]).items() if key not in {"pixel_size_um", "pixel_size_x_um", "pixel_size_y_um"}}
    ).to_dict()
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)
    workspace.scale_bar_length_spin.setValue(50.0)
    workspace.scale_bar_apply_current_button.click()
    app.processEvents()

    image = panel.active_image()
    assert image['scale_bar']['length_px']==0
    template=image['layout_scale_bars'][0]['template']
    source_px = float(image['metadata']['width_px'])*template['fallback_bar_fraction']
    assert source_px > 0
    text = _scale_bar_items(workspace, "text")[0]
    line = _scale_bar_items(workspace, "line")[0]
    label = text.toPlainText()
    assert "px" in label.lower()
    assert "um" not in label.lower()
    assert f"{source_px:g}" in label
    assert float(label.split()[0]) == pytest.approx(source_px)
    assert float(label.split()[0]) != pytest.approx(line.line().length())


def test_layout_scale_bar_apply_leaves_old_history_unchanged(tmp_path: Path) -> None:
    app = _app()
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-render-bridge-history")
    image = panel.import_image(_write_cyx(tmp_path / "history.tif"))
    uid = str(image["uid"])
    image["metadata"] = ImageMetadata.from_dict({**dict(image["metadata"]), "pixel_size_um": 0.5}).to_dict()
    panel.update_measurement_scale_bar(uid, um_per_px=0.5, calibration_source="manual")
    scale_before = deepcopy(image.get("measurement_scale_bar", {}))
    history = {
        "image_uid": uid,
        "total_object_area_um2": 3.0,
        "mean_object_area_um2": 3.0,
        "object_table": [{"object_id": 1, "area_px": 4, "area_um2": 3.0, "contour_points_px": [[0, 0]]}],
    }
    image[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY] = [deepcopy(history)]
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)
    workspace.scale_bar_length_spin.setValue(50.0)
    workspace.scale_bar_manual_um_per_px_spin.setValue(0.25)
    workspace.scale_bar_apply_current_button.click()
    app.processEvents()

    image = panel.active_image()
    assert image["measurement_scale_bar"] == scale_before
    assert image[AI_ASSISTED_MEASUREMENTS_KEY][0]["total_object_area_um2"] == 3.0
    assert image[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["object_id"] == 1
    assert image[AI_ASSISTED_MEASUREMENTS_KEY][0]["object_table"][0]["area_um2"] == 3.0
    assert panel.analysis[AI_ASSISTED_MEASUREMENTS_KEY][0]["mean_object_area_um2"] == 3.0
    assert image['layout_scale_bars'][0]['manual_um_per_px']==pytest.approx(.25)
    assert 'manual_um_per_px' not in image['scale_bar']
