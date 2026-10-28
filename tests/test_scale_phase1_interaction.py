from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.models import BrightfieldPanel, FluorescencePanel
from core.utils.i18n import TRANSLATIONS
from ui.panels.brightfield_workspace import BrightfieldPreviewCanvas, measurement_preview_line_px
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
import ui.main_window as main_window_module
from ui.main_window import MainWindow


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _write_png(path: Path, width: int = 1000, height: int = 80) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#102030"))
    assert image.save(str(path))
    return path


def _legacy_line() -> dict[str, float]:
    return {"x1": 0.0, "y1": 40.0, "x2": 800.0, "y2": 40.0}


def _display_line() -> dict[str, float]:
    return {"x1": 450.0, "y1": 40.0, "x2": 550.0, "y2": 40.0}


def _translated_display_line() -> dict[str, float]:
    return {"x1": 470.0, "y1": 48.0, "x2": 570.0, "y2": 48.0}


def _embedded_scale() -> dict[str, object]:
    return {
        "enabled": True,
        "length_um": 50.0,
        "um_per_px": 0.5,
        "um_per_px_x": 0.5,
        "um_per_px_y": 0.5,
        "calibration_source": "embedded",
        "assumed": False,
        "calibrated": True,
        "line_px": dict(_legacy_line()),
        "display_line_px": dict(_display_line()),
        "line_length_px": 800.0,
    }


def test_brightfield_hitbox_and_preview_follow_visible_display_line() -> None:
    app = _app()
    canvas = BrightfieldPreviewCanvas()
    pixmap = QPixmap(1000, 80)
    pixmap.fill(QColor("#102030"))
    canvas.resize(1000, 120)
    canvas.set_pixmap(pixmap)
    canvas.set_measurement_scale_bar(_embedded_scale())
    app.processEvents()
    label = canvas.image_label
    source = dict(label._source_line)
    assert source["x2"] - source["x1"] == pytest.approx(100.0)
    assert source != _legacy_line()
    points = label._source_line_label_points()
    assert points is not None
    p1, p2 = points
    mid = QPoint(int((p1.x() + p2.x()) / 2), int((p1.y() + p2.y()) / 2))
    assert label._hit_test_source_line(mid) == "move"


def test_embedded_drag_preview_and_move_keep_source_and_axes(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    path = _write_png(tmp_path / "embedded.png")
    try:
        window.set_module("brightfield")
        panel = window._active_brightfield_panel()
        imported = panel.import_image(path)
        uid = str(imported["uid"])
        metadata = dict(imported.get("metadata") or {})
        metadata.update({"width_px": 1000, "height_px": 80, "pixel_size_um": 0.5, "pixel_size_x_um": 0.5, "pixel_size_y_um": 0.5})
        imported["metadata"] = metadata
        panel.update_measurement_scale_bar(
            uid,
            calibration_source="embedded",
            length_um=50.0,
            line_px=dict(_legacy_line()),
            display_line_px=dict(_display_line()),
        )
        before = panel.measurement_scale_bar(uid)
        assert before["calibration_source"] == "embedded"
        assert before["um_per_px"] == pytest.approx(0.5)
        display = measurement_preview_line_px(before)
        assert display["x2"] - display["x1"] == pytest.approx(100.0)
        assert display != _legacy_line()

        window.preview_brightfield_measurement_scale_line(_translated_display_line())
        previewed = window.brightfield_workspace.canvas._measurement_scale_bar
        assert measurement_preview_line_px(previewed)["x1"] == pytest.approx(470.0)
        assert measurement_preview_line_px(previewed)["x2"] - measurement_preview_line_px(previewed)["x1"] == pytest.approx(100.0)
        assert panel.measurement_scale_bar(uid)["calibration_source"] == "embedded"
        assert panel.measurement_scale_bar(uid)["um_per_px"] == pytest.approx(0.5)

        window.brightfield_workspace.canvas.set_line_selection_enabled(False)
        window.set_brightfield_measurement_scale_line(_translated_display_line())
        after = panel.measurement_scale_bar(uid)
        assert after["calibration_source"] == "embedded"
        assert after["um_per_px"] == pytest.approx(0.5)
        assert after["um_per_px_x"] == pytest.approx(0.5)
        assert after["um_per_px_y"] == pytest.approx(0.5)
        moved = measurement_preview_line_px(after)
        assert moved["x2"] - moved["x1"] == pytest.approx(100.0)
        assert (moved["x1"] + moved["x2"]) / 2.0 == pytest.approx(520.0)
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_manual_line_translation_keeps_legacy_line_and_calibration(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    path = _write_png(tmp_path / "manual.png")
    try:
        window.set_module("brightfield")
        panel = window._active_brightfield_panel()
        imported = panel.import_image(path)
        uid = str(imported["uid"])
        original = {"x1": 10.0, "y1": 20.0, "x2": 90.0, "y2": 20.0}
        translated = {"x1": 15.0, "y1": 25.0, "x2": 95.0, "y2": 25.0}
        window.set_brightfield_measurement_scale_line(dict(original))
        drawn = panel.measurement_scale_bar(uid)
        assert drawn["calibration_source"] == "manual_line"
        um = float(drawn["um_per_px"])
        window.preview_brightfield_measurement_scale_line(dict(translated))
        window.brightfield_workspace.canvas.set_line_selection_enabled(False)
        window.set_brightfield_measurement_scale_line(dict(translated))
        moved = panel.measurement_scale_bar(uid)
        assert moved["calibration_source"] == "manual_line"
        assert moved["um_per_px"] == pytest.approx(um)
        assert moved["line_px"] == translated
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_fluorescence_existing_line_move_does_not_recalibrate() -> None:
    app = _app()
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.data["fluorescence_images"] = [
        {
            "uid": "fluoro-1",
            "metadata": {"width_px": 1000, "height_px": 80, "pixel_size_um": 0.5, "pixel_size_x_um": 0.5, "pixel_size_y_um": 0.5},
            "measurement_scale_bar": _embedded_scale(),
            "channels": [{"index": 0, "name": "C", "visible": True}],
        }
    ]
    panel.data["active_image_uid"] = "fluoro-1"
    workspace = FluorescenceWorkspace()
    try:
        workspace.panel = panel
        workspace._add_drawn_roi("scale_line", {**_translated_display_line(), "move_existing": True})
        scale = panel.measurement_scale_bar("fluoro-1")
        assert scale["calibration_source"] == "embedded"
        assert scale["um_per_px"] == pytest.approx(0.5)
        moved = measurement_preview_line_px(scale)
        assert moved["x2"] - moved["x1"] == pytest.approx(100.0)
    finally:
        workspace.deleteLater()
        app.processEvents()


def test_history_translation_key_exists_and_apply_uses_it(tmp_path, monkeypatch) -> None:
    assert "scale_history_uses_previous_calibration" in TRANSLATIONS["en"]
    assert "scale_history_uses_previous_calibration" in TRANSLATIONS["zh"]
    assert "Refresh" in TRANSLATIONS["en"]["scale_history_uses_previous_calibration"]
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    monkeypatch.setattr(main_window_module.QMessageBox, "information", lambda *args, **kwargs: QMessageBox.StandardButton.Ok)
    path = _write_png(tmp_path / "status.png", width=32, height=32)
    try:
        window.set_module("brightfield")
        panel = window._active_brightfield_panel()
        panel.import_image(path)
        window.apply_brightfield_measurement_scale()
        message = window.statusBar().currentMessage()
        assert message == TRANSLATIONS["en"]["scale_history_uses_previous_calibration"]
        assert TRANSLATIONS["en"]["scale_history_uses_previous_calibration"] in window.data_panel.brightfield_he_fiber_scale_status_label.text()
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()
