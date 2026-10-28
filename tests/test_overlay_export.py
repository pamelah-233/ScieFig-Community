from __future__ import annotations

import os
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication, QMessageBox

import ui.main_window as main_window_module
from core.utils.i18n import set_language, tr
from ui.main_window import MainWindow


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _close(window: MainWindow, app: QApplication) -> None:
    window._capture_clean_project_baseline()
    window.close()
    window.deleteLater()
    app.processEvents()


def _wait_busy(window: MainWindow, key: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    app = QApplication.instance()
    assert app is not None
    while window._analysis_job_service.is_busy(key):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {key}")
        time.sleep(0.005)
    app.processEvents()


def _write_brightfield_png(path: Path) -> Path:
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))
    for y in range(3):
        for x in range(3):
            image.setPixelColor(x, y, QColor(210, 40, 40))
    for y in range(3, 6):
        for x in range(3, 6):
            image.setPixelColor(x, y, QColor(40, 80, 210))
    assert image.save(str(path))
    return path


def _write_fluoro_tiff(path: Path) -> Path:
    data = np.zeros((2, 40, 44), dtype=np.uint16)
    data[0, 8:14, 9:15] = 180
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _red_settings() -> dict[str, float | int | bool | str]:
    return {
        "segmentation_mode": "hsv",
        "h_min": 0.0,
        "h_max": 0.10,
        "s_min": 0.4,
        "s_max": 1.0,
        "v_min": 0.0,
        "v_max": 0.95,
        "fill_holes": True,
        "cleanup_min_area_px": 0,
        "min_object_area_px": 2,
        "max_object_area_px": 0,
    }


def _blue_settings() -> dict[str, float | int | bool | str]:
    settings = _red_settings()
    settings["h_min"] = 0.50
    settings["h_max"] = 0.80
    return settings


def _load_png(path: Path) -> QImage:
    image = QImage()
    assert image.loadFromData(path.read_bytes())
    return image


def _rgb(image: QImage, x: int, y: int) -> tuple[int, int, int]:
    color = image.pixelColor(x, y)
    return color.red(), color.green(), color.blue()


def _prepare_brightfield(window: MainWindow, tmp_path: Path):
    image_path = _write_brightfield_png(tmp_path / "overlay_source.png")
    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path)])
    _wait_busy(window, "import:brightfield")
    return window._active_brightfield_panel()


def _fluoro_record() -> dict:
    return {
        "ai_measurement_uuid": "overlay-ai-1",
        "channel_index": 0,
        "ai_object_count": 1,
        "object_table": [
            {
                "object_id": 1,
                "contour_points_px": [
                    {"x": 8, "y": 8},
                    {"x": 20, "y": 8},
                    {"x": 20, "y": 20},
                    {"x": 8, "y": 20},
                ],
                "bbox_x_min": 8,
                "bbox_y_min": 8,
                "bbox_x_max": 20,
                "bbox_y_max": 20,
            }
        ],
    }


def test_brightfield_export_image_allows_original_without_measurement(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = _prepare_brightfield(window, tmp_path)
        app.processEvents()
        assert window.brightfield_workspace.export_image_button.isEnabled() is True
        with pytest.raises(ValueError, match="before exporting"):
            window.export_brightfield_overlay_image_to_path(tmp_path / "missing_overlay.png")
        original = window.export_brightfield_original_image_to_path(tmp_path / "original_only.png")
        loaded = _load_png(original)
        source = panel.source_qimage(str(panel.active_image()["uid"]))
        assert (loaded.width(), loaded.height()) == (source.width(), source.height())
        captured: dict[str, object] = {}

        def choose(**kwargs):
            captured.update(kwargs)
            return "original"

        monkeypatch.setattr(window, "_choose_export_image_source", choose)
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "dialog_original.png"), "PNG (*.png)"),
        )
        window.export_brightfield_image()
        assert captured["has_original"] is True
        assert captured["has_overlay"] is False
        assert (tmp_path / "dialog_original.png").exists()
    finally:
        _close(window, app)


def test_brightfield_export_explicit_original_and_overlay_native_size(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = _prepare_brightfield(window, tmp_path)
        measurement = panel.create_measurement(settings=_red_settings())
        window._refresh_brightfield_preview(panel, measurement_uuid=str(measurement["measurement_uuid"]))
        canvas = window.brightfield_workspace.canvas
        canvas._zoom_factor = 0.25
        canvas._apply_paper_zoom()
        app.processEvents()
        expected_overlay = window._brightfield_overlay_qimage(panel, measurement)
        expected_original = panel.source_qimage(str(measurement["image_uid"]))
        overlay_path = window.export_brightfield_overlay_image_to_path(tmp_path / "chosen_overlay.png")
        original_path = window.export_brightfield_original_image_to_path(tmp_path / "chosen_original.png")
        overlay_loaded = _load_png(overlay_path)
        original_loaded = _load_png(original_path)
        assert (overlay_loaded.width(), overlay_loaded.height()) == (expected_overlay.width(), expected_overlay.height())
        assert (original_loaded.width(), original_loaded.height()) == (expected_original.width(), expected_original.height())
        assert _rgb(overlay_loaded, 1, 1) == _rgb(expected_overlay, 1, 1)
        assert _rgb(original_loaded, 1, 1) == _rgb(expected_original, 1, 1)
        assert _rgb(overlay_loaded, 1, 1) != _rgb(original_loaded, 1, 1)
        window.brightfield_workspace.show_original_preview()
        chosen: list[str] = []

        def choose(**kwargs):
            chosen.append("overlay")
            assert kwargs["has_overlay"] is True
            return "overlay"

        monkeypatch.setattr(window, "_choose_export_image_source", choose)
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "from_original_view.png"), "PNG (*.png)"),
        )
        window.export_brightfield_image()
        assert chosen == ["overlay"]
        from_view = _load_png(tmp_path / "from_original_view.png")
        assert _rgb(from_view, 1, 1) == _rgb(expected_overlay, 1, 1)
    finally:
        _close(window, app)


def test_brightfield_overlay_export_uses_selected_measurement(tmp_path) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = _prepare_brightfield(window, tmp_path)
        first = panel.create_measurement(settings=_red_settings())
        second = panel.create_measurement(settings=_blue_settings())
        window.data_panel.populate_brightfield_measurements(
            [first, second],
            selected_measurement_uuid=str(first["measurement_uuid"]),
        )
        app.processEvents()
        exported = window.export_brightfield_overlay_image_to_path(tmp_path / "selected.png")
        loaded = _load_png(exported)
        first_image = window._brightfield_overlay_qimage(panel, first)
        second_image = window._brightfield_overlay_qimage(panel, second)
        assert _rgb(loaded, 1, 1) == _rgb(first_image, 1, 1)
        assert _rgb(loaded, 1, 1) != _rgb(second_image, 1, 1)
    finally:
        _close(window, app)


def test_brightfield_export_cancel_source_and_file_dialog(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        _prepare_brightfield(window, tmp_path)
        monkeypatch.setattr(window, "_choose_export_image_source", lambda **kwargs: None)
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: pytest.fail("file dialog should not open"),
        )
        window.export_brightfield_image()
        assert {path.name for path in tmp_path.glob("*.png")} == {"overlay_source.png"}
        monkeypatch.setattr(window, "_choose_export_image_source", lambda **kwargs: "original")
        monkeypatch.setattr(main_window_module.QFileDialog, "getSaveFileName", lambda *args, **kwargs: ("", ""))
        window.export_brightfield_image()
        assert {path.name for path in tmp_path.glob("*.png")} == {"overlay_source.png"}
    finally:
        _close(window, app)


def test_brightfield_export_unicode_no_mutation_and_write_failure(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = _prepare_brightfield(window, tmp_path)
        measurement = panel.create_measurement(settings=_red_settings())
        window._refresh_brightfield_preview(panel, measurement_uuid=str(measurement["measurement_uuid"]))
        before_measurements = deepcopy(list(panel.analysis.get("brightfield_measurements", [])))
        before_images = deepcopy(panel.images())
        unicode_path = tmp_path / "叠加图_测试.png"
        exported = window.export_brightfield_overlay_image_to_path(unicode_path)
        assert exported == unicode_path
        assert not _load_png(unicode_path).isNull()
        original_unicode = window.export_brightfield_original_image_to_path(tmp_path / "原始图_测试.png")
        assert not _load_png(original_unicode).isNull()
        assert list(panel.analysis.get("brightfield_measurements", [])) == before_measurements
        assert panel.images() == before_images
        remembered: list[object] = []
        messages: list[str] = []
        monkeypatch.setattr(window, "_choose_export_image_source", lambda **kwargs: "overlay")
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "bf_write_fail.png"), "PNG (*.png)"),
        )
        monkeypatch.setattr(main_window_module, "remember_file_directory", lambda path: remembered.append(path))
        monkeypatch.setattr(QMessageBox, "information", lambda _parent, title, text: messages.append(f"{title}: {text}"))
        monkeypatch.setattr(window, "_write_overlay_png", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("Permission denied")))
        window.export_brightfield_image()
        assert messages
        assert "Permission denied" in messages[-1]
        assert remembered == []
        assert "Exported image" not in window.statusBar().currentMessage()
        assert not (tmp_path / "bf_write_fail.png").exists()
    finally:
        _close(window, app)


def test_brightfield_overlay_export_includes_committed_scale_not_draft(tmp_path) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        panel = _prepare_brightfield(window, tmp_path)
        measurement = panel.create_measurement(settings=_red_settings())
        image_uid = str(measurement["image_uid"])
        panel.update_measurement_scale_bar(
            image_uid,
            length_um=50.0,
            um_per_px=1.0,
            line_px={"x1": 0.0, "y1": 5.0, "x2": 5.0, "y2": 5.0},
            calibration_source="manual_line",
        )
        panel.data["brightfield_layout"] = {}
        window._refresh_brightfield_preview(panel, measurement_uuid=str(measurement["measurement_uuid"]))
        if window.brightfield_workspace.canvas.image_label is not None:
            window.brightfield_workspace.canvas.image_label._draft_source_line = {
                "x1": 0.0,
                "y1": 0.0,
                "x2": 5.0,
                "y2": 0.0,
            }
        exported = window.export_brightfield_overlay_image_to_path(tmp_path / "scale.png")
        loaded = _load_png(exported)
        plain = window._brightfield_overlay_qimage(panel, measurement)
        assert _rgb(loaded, 2, 5) != _rgb(plain, 2, 5)
        assert _rgb(loaded, 2, 0) == _rgb(plain, 2, 0)
    finally:
        _close(window, app)


def test_export_image_button_translates_and_ihc_button_not_duplicated() -> None:
    app = _app()
    set_language("en")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        assert window.brightfield_workspace.export_image_button.text() == "Export image"
        assert window.fluoro_workspace.export_image_button.text() == "Export image"
        assert window.data_panel.ihc_export_overlay_button.text() == tr("export_overlay_image")
        window.switch_language("zh")
        app.processEvents()
        assert window.brightfield_workspace.export_image_button.text() == "导出图片"
        assert window.fluoro_workspace.export_image_button.text() == "导出图片"
        assert window.data_panel.ihc_export_overlay_button.text() == "导出叠加图"
        assert hasattr(window, "export_ihc_overlay_image_to_path")
        assert hasattr(window, "export_ihc_original_image_to_path")
    finally:
        window.switch_language("en")
        _close(window, app)
        set_language("en")


def test_ihc_export_original_without_measurement_and_overlay_api_remains(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        image_path = _write_brightfield_png(tmp_path / "ihc_source.png")
        window.set_module("ihc")
        window.import_ihc_images([str(image_path)])
        _wait_busy(window, "import:ihc")
        panel = window._active_ihc_panel()
        assert window.data_panel.ihc_export_overlay_button.isEnabled() is True
        with pytest.raises(ValueError, match="before exporting"):
            window.export_ihc_overlay_image_to_path(tmp_path / "ihc_missing_overlay.png")
        original = window.export_ihc_original_image_to_path(tmp_path / "ihc_original.png")
        loaded = _load_png(original)
        source = panel.source_qimage(str(panel.active_image()["uid"]))
        assert (loaded.width(), loaded.height()) == (source.width(), source.height())
        captured: dict[str, object] = {}

        def choose(**kwargs):
            captured.update(kwargs)
            return "original"

        monkeypatch.setattr(window, "_choose_export_image_source", choose)
        monkeypatch.setattr(
            main_window_module.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "ihc_dialog_original.png"), "PNG (*.png)"),
        )
        window.export_ihc_overlay_image()
        assert captured["has_original"] is True
        assert captured["has_overlay"] is False
        assert (tmp_path / "ihc_dialog_original.png").exists()
    finally:
        _close(window, app)


def test_fluorescence_export_original_and_overlay_cancel_write_failure(tmp_path, monkeypatch) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        image_path = _write_fluoro_tiff(tmp_path / "fluoro_source.tif")
        window.set_module("fluoro")
        window.import_fluorescence_images([str(image_path)])
        _wait_busy(window, "import:fluoro")
        panel = window._active_fluoro_panel()
        image = panel.active_image()
        assert image is not None
        window.fluoro_workspace._sync_ai_overlay_buttons(has_overlay=False, show_overlay=False)
        app.processEvents()
        assert window.fluoro_workspace.export_image_button.isEnabled() is True
        original = window.export_fluorescence_original_image_to_path(tmp_path / "fluoro_original.png")
        native = panel.render_image_with_scale_bar(str(image.get("uid", "")))
        loaded_original = _load_png(original)
        assert (loaded_original.width(), loaded_original.height()) == (native.width(), native.height())
        with pytest.raises(ValueError, match="before exporting"):
            window.export_fluorescence_overlay_image_to_path(tmp_path / "fluoro_missing_overlay.png")
        image["fluorescence_ai_assisted_measurements"] = [_fluoro_record()]
        panel.analysis["fluorescence_ai_assisted_measurements"] = [_fluoro_record()]
        window.fluoro_workspace.canvas._zoom_factor = 0.25
        overlay = window.export_fluorescence_overlay_image_to_path(tmp_path / "叠加荧光.png")
        loaded_overlay = _load_png(overlay)
        assert (loaded_overlay.width(), loaded_overlay.height()) == (native.width(), native.height())
        from ui.panels import fluorescence_image_export as fluoro_export

        monkeypatch.setattr(fluoro_export, "choose_fluorescence_image_export", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            fluoro_export.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: pytest.fail("file dialog should not open"),
        )
        window.export_fluorescence_image()
        remembered: list[object] = []
        messages: list[str] = []
        monkeypatch.setattr(
            fluoro_export,
            "choose_fluorescence_image_export",
            lambda *args, **kwargs: (fluoro_export.SOURCE_OVERLAY, fluoro_export.COMPOSITION_MERGE),
        )
        monkeypatch.setattr(
            fluoro_export.QFileDialog,
            "getSaveFileName",
            lambda *args, **kwargs: (str(tmp_path / "fluoro_write_fail.png"), "PNG (*.png)"),
        )
        monkeypatch.setattr(fluoro_export, "remember_file_directory", lambda path: remembered.append(path))
        monkeypatch.setattr(
            fluoro_export.QMessageBox,
            "warning",
            lambda _parent, title, text: messages.append(f"{title}: {text}"),
        )
        monkeypatch.setattr(
            fluoro_export,
            "write_fluorescence_png",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("Permission denied")),
        )
        window.export_fluorescence_image()
        assert messages
        assert "Permission denied" in messages[-1]
        assert remembered == []
        assert not (tmp_path / "fluoro_write_fail.png").exists()
    finally:
        _close(window, app)


def _write_large_png(path: Path, width: int = 80, height: int = 60) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(18, 18, 18))
    for y in range(8, 20):
        for x in range(8, 24):
            image.setPixelColor(x, y, QColor(210, 40, 40))
    assert image.save(str(path))
    return path


def _bottom_red_count(image: QImage) -> int:
    count = 0
    y0 = max(0, image.height() * 2 // 3)
    for y in range(y0, image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.red() > 200 and color.green() < 80 and color.blue() < 80:
                count += 1
    return count


def _red_layout_template() -> dict[str, object]:
    return {
        "label_length_um": 20.0,
        "line_color": "#FF0000",
        "text_color": "#FF0000",
        "line_thickness": 6.0,
        "show_text": False,
        "anchor": "bottom_right",
        "offset_x_fraction": 0.06,
        "offset_y_fraction": 0.08,
    }


def test_native_export_layout_style_hidden_and_fallback(tmp_path) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        image_path = _write_large_png(tmp_path / "style_source.png")
        window.set_module("brightfield")
        window.import_brightfield_images([str(image_path)])
        _wait_busy(window, "import:brightfield")
        panel = window._active_brightfield_panel()
        measurement = panel.create_measurement(settings=_red_settings())
        image_uid = str(measurement["image_uid"])
        panel.update_measurement_scale_bar(
            image_uid,
            length_um=20.0,
            um_per_px=0.5,
            line_px={"x1": 4.0, "y1": 50.0, "x2": 24.0, "y2": 50.0},
            calibration_source="manual_line",
        )
        calibration = deepcopy(panel.measurement_scale_bar(image_uid))
        layout = panel.data.setdefault("brightfield_layout", {})
        layout["scale_bar_template"] = _red_layout_template()
        layout["scale_bars"] = [{"target_id": image_uid, "enabled": True}]
        original = _load_png(window.export_brightfield_original_image_to_path(tmp_path / "bf_style_original.png"))
        overlay = _load_png(window.export_brightfield_overlay_image_to_path(tmp_path / "bf_style_overlay.png"))
        source = panel.source_qimage(image_uid)
        assert (original.width(), original.height()) == (source.width(), source.height())
        assert (overlay.width(), overlay.height()) == (source.width(), source.height())
        assert _bottom_red_count(original) > 20
        assert _bottom_red_count(overlay) > 20
        assert panel.measurement_scale_bar(image_uid) == calibration

        layout["scale_bars"] = [{"target_id": image_uid, "enabled": False}]
        hidden = _load_png(window.export_brightfield_original_image_to_path(tmp_path / "bf_style_hidden.png"))
        assert _bottom_red_count(hidden) == 0
        assert panel.measurement_scale_bar(image_uid)["um_per_px"] == pytest.approx(float(calibration["um_per_px"]))

        panel.data["brightfield_layout"] = {}
        fallback = _load_png(window.export_brightfield_original_image_to_path(tmp_path / "bf_style_fallback.png"))
        plain = panel.source_qimage(image_uid)
        assert _rgb(fallback, 14, 50) != _rgb(plain, 14, 50)
        assert panel.measurement_scale_bar(image_uid) == calibration

        window.set_module("ihc")
        window.import_ihc_images([str(image_path)])
        _wait_busy(window, "import:ihc")
        ihc = window._active_ihc_panel()
        ihc_uid = str(ihc.active_image()["uid"])
        ihc.update_measurement_scale_bar(
            ihc_uid,
            length_um=20.0,
            um_per_px=0.5,
            line_px={"x1": 4.0, "y1": 50.0, "x2": 24.0, "y2": 50.0},
            calibration_source="manual_line",
        )
        ihc_cal = deepcopy(ihc.measurement_scale_bar(ihc_uid))
        ihc_layout = ihc.data.setdefault("ihc_layout", {})
        ihc_layout["scale_bar_template"] = _red_layout_template()
        ihc_layout["scale_bars"] = [{"target_id": ihc_uid, "enabled": True}]
        ihc_original = _load_png(window.export_ihc_original_image_to_path(tmp_path / "ihc_style_original.png"))
        assert (ihc_original.width(), ihc_original.height()) == (ihc.source_qimage(ihc_uid).width(), ihc.source_qimage(ihc_uid).height())
        assert _bottom_red_count(ihc_original) > 20
        assert ihc.measurement_scale_bar(ihc_uid) == ihc_cal
        ihc_layout["scale_bars"] = [{"target_id": ihc_uid, "enabled": False}]
        ihc_hidden = _load_png(window.export_ihc_original_image_to_path(tmp_path / "ihc_style_hidden.png"))
        assert _bottom_red_count(ihc_hidden) == 0
    finally:
        _close(window, app)


def test_fluorescence_native_export_layout_style_and_hidden(tmp_path) -> None:
    app = _app()
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    try:
        image_path = _write_fluoro_tiff(tmp_path / "fluoro_style.tif")
        window.set_module("fluoro")
        window.import_fluorescence_images([str(image_path)])
        _wait_busy(window, "import:fluoro")
        panel = window._active_fluoro_panel()
        image = panel.active_image()
        assert image is not None
        uid = str(image.get("uid", ""))
        panel.update_measurement_scale_bar(
            uid,
            length_um=20.0,
            um_per_px=0.5,
            line_px={"x1": 4.0, "y1": 30.0, "x2": 24.0, "y2": 30.0},
            calibration_source="manual_line",
        )
        image = panel.active_image()
        assert image is not None
        calibration = deepcopy(image.get("measurement_scale_bar") or {})
        image["layout_scale_bar_template"] = _red_layout_template()
        target_id = str((image.get("layout_scale_bars") or [{}])[0].get("target_id") or uid)
        image["layout_scale_bars"] = [{"target_id": target_id, "enabled": True}]
        original = _load_png(window.export_fluorescence_original_image_to_path(tmp_path / "fluoro_style_original.png"))
        raw = panel._render_image_pixels(image)
        assert (original.width(), original.height()) == (raw.width(), raw.height())
        assert _bottom_red_count(original) > 10
        assert panel.active_image()["measurement_scale_bar"] == calibration
        image["layout_scale_bars"] = [{"target_id": target_id, "enabled": False}]
        hidden = _load_png(window.export_fluorescence_original_image_to_path(tmp_path / "fluoro_style_hidden.png"))
        assert _bottom_red_count(hidden) == 0
        assert panel.active_image()["measurement_scale_bar"]["um_per_px"] == pytest.approx(calibration.get("um_per_px", 0.5))
    finally:
        _close(window, app)
