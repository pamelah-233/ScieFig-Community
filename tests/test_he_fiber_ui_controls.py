from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from core.models import BrightfieldPanel
from ui.main_window import MainWindow


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _close(window: MainWindow, app: QApplication) -> None:
    window._capture_clean_project_baseline()
    window.close()
    window.deleteLater()
    app.processEvents()


def _write_he_png(path: Path) -> Path:
    image = QImage(120, 90, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 241, 244))
    for y in range(16, 44):
        for x in range(12, 42):
            image.setPixelColor(x, y, QColor(215, 74, 132))
    assert image.save(str(path))
    return path


def test_he_fiber_splitting_zero_survives_setter_and_project_roundtrip() -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = window._active_brightfield_panel()
        assert window.data_panel.brightfield_he_fiber_method_combo.currentData() == "original"
        panel.persist_he_fiber_analysis_settings(
            {
                "he_fiber_foreground_sensitivity": 50,
                "he_fiber_splitting_sensitivity": 0,
                "he_fiber_segmentation_method": "anti_oversegmentation",
            }
        )
        window.data_panel.set_brightfield_he_fiber_analysis_settings(dict(panel.data.get("analysis_settings", {})))
        assert window.data_panel.brightfield_he_fiber_method_combo.currentData() == "anti_oversegmentation"
        assert window.data_panel.brightfield_he_fiber_splitting_slider.value() == 0
        assert window.data_panel.brightfield_analysis_settings()["he_fiber_splitting_sensitivity"] == 0

        restored = BrightfieldPanel.from_dict(panel.to_dict())
        assert restored.data["analysis_settings"]["he_fiber_splitting_sensitivity"] == 0
        assert restored.data["analysis_settings"]["he_fiber_segmentation_method"] == "anti_oversegmentation"
        window.data_panel.set_brightfield_he_fiber_analysis_settings(dict(restored.data.get("analysis_settings", {})))
        assert window.data_panel.brightfield_he_fiber_splitting_slider.value() == 0
        assert window.data_panel.brightfield_analysis_settings()["he_fiber_splitting_sensitivity"] == 0
        assert window.data_panel.brightfield_analysis_settings()["he_fiber_segmentation_method"] == "anti_oversegmentation"
    finally:
        _close(window, app)


def test_he_fiber_slider_keyboard_value_changed_persists_without_analysis(monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = window._active_brightfield_panel()
        monkeypatch.setattr(
            panel,
            "create_ai_object_count_measurement",
            lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("analysis must not run")),
        )
        method = window.data_panel.brightfield_he_fiber_method_combo
        method.setCurrentIndex(method.findData("anti_oversegmentation"))
        app.processEvents()
        slider = window.data_panel.brightfield_he_fiber_splitting_slider
        assert slider.isEnabled()
        slider.setValue(40)
        app.processEvents()
        slider.setFocus()
        QTest.keyClick(slider, Qt.Key.Key_Home)
        app.processEvents()
        assert slider.value() == 0
        assert panel.data["analysis_settings"]["he_fiber_splitting_sensitivity"] == 0
    finally:
        _close(window, app)


def test_he_fiber_method_combo_enables_tuning_only_for_anti_and_stays_selectable(tmp_path: Path) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        window.set_module("brightfield")
        window.show()
        app.processEvents()
        combo = window.data_panel.brightfield_measure_type_combo
        group = window.data_panel.brightfield_he_fiber_tuning_group
        stack = window.data_panel.brightfield_measure_mode_stack

        combo.setCurrentIndex(combo.findData("cells"))
        app.processEvents()
        assert not group.isVisibleTo(stack)
        combo.setCurrentIndex(combo.findData("area"))
        app.processEvents()
        assert not group.isVisibleTo(stack)
        combo.setCurrentIndex(combo.findData("fiber"))
        app.processEvents()
        assert stack.currentIndex() == combo.currentIndex()
        assert group.isVisibleTo(stack)
        method = window.data_panel.brightfield_he_fiber_method_combo
        assert method.currentData() == "original"
        assert method.isEnabled()
        assert not window.data_panel.brightfield_he_fiber_splitting_slider.isEnabled()
        method.setCurrentIndex(method.findData("anti_oversegmentation"))
        app.processEvents()
        assert window.data_panel.brightfield_he_fiber_splitting_slider.isEnabled()
        assert window.data_panel.brightfield_he_fiber_foreground_combo.isEnabled()
        assert method.isEnabled()

        image_path = _write_he_png(tmp_path / "he_fiber_manual.png")
        window.import_brightfield_images([str(image_path)])
        deadline = time.monotonic() + 15.0
        while window._analysis_job_service.is_busy("import:brightfield"):
            app.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("timed out waiting for brightfield import")
            time.sleep(0.005)
        app.processEvents()
        panel = window._active_brightfield_panel()
        imported = panel.active_image()
        assert imported is not None
        panel.calibrate_he_fiber_area(image_uid=str(imported["uid"]))
        calibrations = dict(panel.data.get("he_fiber_area_calibrations", {}))
        calibrations[str(imported["uid"])]["calibration_mode"] = "manual"
        panel.data["he_fiber_area_calibrations"] = calibrations
        window._update_brightfield_he_fiber_calibration_status(panel)
        assert method.isEnabled()
        method.setCurrentIndex(method.findData("anti_oversegmentation"))
        app.processEvents()
        assert method.isEnabled()
        assert window.data_panel.brightfield_he_fiber_splitting_slider.isEnabled()
        tip = window.data_panel.brightfield_he_fiber_method_combo.toolTip() + window.data_panel.brightfield_he_fiber_tuning_group.toolTip()
        assert "manual" in tip.lower() or "自动" in tip or "automatic" in tip.lower()
        method.setCurrentIndex(method.findData("original"))
        app.processEvents()
        assert method.isEnabled()
        assert not window.data_panel.brightfield_he_fiber_splitting_slider.isEnabled()
    finally:
        _close(window, app)
