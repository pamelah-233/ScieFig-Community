import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path

import cv2
import numpy as np
import pytest
from PyQt6.QtWidgets import QApplication

from core.utils.i18n import get_language, set_language


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_cellpose_switch_disables_only_traditional_thresholds(app):
    from ui.main_window import MainWindow
    window = MainWindow(Path(__file__).resolve().parents[1] / "resources")
    try:
        data = window.data_panel
        toggle = data.brightfield_cellpose_checkbox
        assert not toggle.isChecked()
        toggle.setChecked(True)
        assert data.bright_round_cell_analysis_settings()['backend'] == 'cellpose'
        assert not data.brightfield_bright_round_cell_threshold_spin.isEnabled()
        assert not data.brightfield_bright_round_cell_min_circularity_spin.isEnabled()
        assert data.brightfield_bright_round_cell_min_diameter_um_spin.isEnabled()
        data.set_cell_analysis_settings({'backend': 'traditional'})
        assert not toggle.isChecked()
        assert data.brightfield_bright_round_cell_threshold_spin.isEnabled()
    finally:
        window.close()


def test_cellpose_uses_background_snapshot(app, tmp_path, monkeypatch):
    from ui.main_window import MainWindow
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'critical', lambda *a: None)
    from core.models import BrightfieldPanel
    window = MainWindow(Path(__file__).resolve().parents[1] / 'resources')
    try:
        window.set_module('brightfield')
        panel = window._active_brightfield_panel()
        path = tmp_path / 'cells.png'
        cv2.imwrite(str(path), np.full((80,80,3),120,np.uint8))
        panel.data['brightfield_images'] = [dict(uid='one', source_path=str(path), source_name='cells.png',
            metadata={'width_px':80,'height_px':80}, measurements=[],
            measurement_scale_bar={'um_per_px':.5,'length_um':100,'calibration_source':'manual'},
            roi={'mode':'whole_image','items':[],'active_item_uuid':None})]
        panel.data['active_image_uid'] = 'one'
        window.data_panel.brightfield_cellpose_checkbox.setChecked(True)
        submitted = {}
        def start(**kwargs):
            submitted.update(kwargs)
            return True
        monkeypatch.setattr(window._analysis_job_service, 'start', start)
        window.run_bright_round_cell_diameter()
        assert submitted['key'] == 'brightfield-cellpose'
        assert 'bright_round_cell_diameter' not in panel.analysis
        submitted['on_error'](RuntimeError('test inference error'))
        assert window.data_panel.brightfield_bright_round_cell_measure_button.isEnabled()
    finally:
        window.close()


def test_one_click_measures_both_shapes_and_restores_per_image(app, tmp_path):
    from ui.main_window import MainWindow
    from PyQt6.QtTest import QTest
    import time
    previous = get_language()
    set_language("zh")
    window = MainWindow(Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("brightfield")
        assert window.data_panel.brightfield_bright_round_cell_group.title() == "细胞尺寸（圆形 / 长条）"
        panel = window._active_brightfield_panel()
        rgb = np.full((180, 260, 3), 120, np.uint8)
        cv2.circle(rgb, (60, 70), 12, (240, 240, 240), -1)
        cv2.ellipse(rgb, (160, 90), (35, 6), -25, 0, 360, (38, 38, 38), -1)
        path = tmp_path / "cells.png"
        cv2.imwrite(str(path), rgb)
        panel.data["brightfield_images"] = [dict(
            uid=uid, source_path=str(path), source_name=uid + ".png",
            metadata={"width_px":260, "height_px":180}, measurements=[],
            measurement_scale_bar={"um_per_px":0.5, "length_um":100., "calibration_source":"manual"},
            roi={"mode":"whole_image", "items":[], "active_item_uuid":None}
        ) for uid in ("image-1", "image-2")]
        panel.data["active_image_uid"] = "image-1"
        window._refresh_brightfield_preview(panel)
        window.data_panel.brightfield_bright_round_cell_measure_button.click()
        deadline = time.monotonic() + 10
        while window._analysis_job_service.is_busy('brightfield-cellpose') and time.monotonic() < deadline:
            QTest.qWait(20)
        assert not window._analysis_job_service.is_busy('brightfield-cellpose')
        measurement = panel.analysis["bright_round_cell_diameter"]
        assert measurement["summary"]["round_count"] == 1
        assert measurement["summary"]["elongated_count"] == 1
        assert measurement["params"]["elongated_contrast_threshold"] == 16
        table = window.data_panel.brightfield_bright_round_cell_results_table
        assert table.rowCount() == 2
        assert table.columnCount() == 12
        assert table.item(0, 9).text() == "image-1.png"
        assert window.brightfield_workspace.canvas.has_original_pixmap()
        assert window.brightfield_workspace.canvas.has_overlay_pixmap()
        window.data_panel.brightfield_measure_type_combo.setCurrentIndex(1)
        assert not window.brightfield_workspace.canvas.has_overlay_pixmap()
        window.data_panel.brightfield_measure_type_combo.setCurrentIndex(0)
        assert window.brightfield_workspace.canvas.has_overlay_pixmap()
        assert not panel.active_image()["measurements"]  # dedicated rows must not enter area-analysis path
        panel.data["active_image_uid"] = "image-2"
        window._refresh_brightfield_preview(panel)
        assert table.rowCount() == 0
        panel.data["active_image_uid"] = "image-1"
        window._refresh_brightfield_preview(panel)
        assert table.rowCount() == 2
        assert window.brightfield_workspace.canvas.has_overlay_pixmap()
        panel.analysis["brightfield_measurements"] = [{"measurement_uuid":"old-area", "image_uid":"image-1", "results":{}}]
        def wrong_area_overlay(**kwargs):
            pytest.fail("Cell-size mode must not display a previous area-analysis overlay")
        panel.render_measurement_overlay = wrong_area_overlay
        window.set_module("brightfield")
        assert window.brightfield_workspace.canvas.has_overlay_pixmap()
    finally:
        set_language(previous)
        window.close()
