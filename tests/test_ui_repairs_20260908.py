from __future__ import annotations

import os
import time
from pathlib import Path

from math import hypot

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPixmap
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget
from main import load_qss
from ui.panels.fluorescence_workspace import CurrentPageStackedWidget

from core.models import BrightfieldPanel, IHCPanel, Project
from core.utils.i18n import get_language, set_language, tr
from ui.main_window import MainWindow
from ui.panels.brightfield_workspace import measurement_preview_line_px
from ui.panels.ihc_workspace import IHCWorkspace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

ROOT = Path(__file__).resolve().parents[1]
RESOURCES = ROOT / "resources"
ARTIFACTS = ROOT / "build" / "ui_repairs_20260908"


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")
    return QApplication.instance() or QApplication([])


def _close(widget: QWidget, app: QApplication) -> None:
    if isinstance(widget, MainWindow):
        widget._capture_clean_project_baseline()
    widget.close()
    widget.deleteLater()
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


def _write_ihc_png(path: Path) -> Path:
    image = QImage(6, 6, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))
    for y in range(3):
        for x in range(3):
            image.setPixelColor(x, y, QColor(135, 75, 28))
    for y in range(3, 6):
        for x in range(3, 6):
            image.setPixelColor(x, y, QColor(80, 120, 190))
    assert image.save(str(path))
    return path


def _write_ihc_draw_png(path: Path) -> Path:
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor(245, 245, 245))
    for y in range(10, 40):
        for x in range(8, 50):
            image.setPixelColor(x, y, QColor(135, 75, 28))
    assert image.save(str(path))
    return path


def _write_fluoro_tiff(path: Path) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _write_he_fiber_png(path: Path) -> Path:
    image = QImage(120, 90, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 241, 244))
    for y in range(16, 44):
        for x in range(12, 42):
            image.setPixelColor(x, y, QColor(215, 74, 132))
    for y in range(20, 48):
        for x in range(56, 90):
            image.setPixelColor(x, y, QColor(220, 82, 138))
    assert image.save(str(path))
    return path


def _write_he_png(path: Path) -> Path:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(246, 246, 246))
    for y in range(1, 6):
        for x in range(2, 6):
            image.setPixelColor(x, y, QColor(235, 170, 190))
    assert image.save(str(path))
    return path


def _pixmap(color: str, width: int = 12, height: int = 10) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor(color))
    return pixmap


def _source_color(canvas) -> QColor:
    assert canvas._source_pixmap is not None
    return canvas._source_pixmap.toImage().pixelColor(0, 0)


def _save_grab(widget: QWidget, name: str) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / name
    widget.grab().save(str(path))
    return path


def _save_grab_rect(widget: QWidget, region: QRect, name: str) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    path = ARTIFACTS / name
    widget.grab(region).save(str(path))
    return path


def _vertical_gap(top: QWidget, bottom: QWidget, ancestor: QWidget) -> int:
    top_bottom = top.mapTo(ancestor, top.rect().bottomLeft()).y()
    bottom_top = bottom.mapTo(ancestor, bottom.rect().topLeft()).y()
    return int(bottom_top - top_bottom)


def _assert_toggle_usable(workspace, *, overlay_enabled: bool) -> None:
    assert workspace.preview_toggle_panel.isVisibleTo(workspace.workflow_panel)
    # Studio places these same controls in the canvas toolbar.
    assert workspace.original_preview_button.isVisibleTo(workspace.window())
    assert workspace.overlay_preview_button.isVisibleTo(workspace.window())
    assert workspace.overlay_preview_button.isEnabled() is overlay_enabled


def test_ihc_draw_scale_button_is_named_translated_and_next_to_apply() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    button = workspace.draw_scale_button
    assert button.objectName() == "IHCDrawScaleButton"
    assert button.accessibleName() == tr("draw_scale")
    assert button.text() == tr("draw_scale")
    assert button.property("i18nTextKey") == "draw_scale"
    apply_buttons = [
        child
        for child in workspace.calibration_controls.findChildren(QPushButton)
        if child.text() == tr("apply_scale")
    ]
    assert apply_buttons
    assert button.parent() is apply_buttons[0].parent()
    _close(workspace, app)


def _drag_scale_line(workspace, app: QApplication) -> dict[str, float]:
    label = workspace.canvas.image_label
    assert label is not None
    pixmap_rect = label._pixmap_rect().intersected(label.rect())
    assert pixmap_rect.width() > 8 and pixmap_rect.height() > 4
    start = QPoint(pixmap_rect.left() + 6, pixmap_rect.center().y())
    end = QPoint(pixmap_rect.right() - 6, pixmap_rect.center().y())
    workspace.draw_scale_button.click()
    app.processEvents()
    QTest.mousePress(label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(label, end)
    QTest.mouseRelease(label, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    app.processEvents()
    line = measurement_preview_line_px(workspace.canvas._measurement_scale_bar)
    assert line and hypot(float(line.get("x2", 0)) - float(line.get("x1", 0)), float(line.get("y2", 0)) - float(line.get("y1", 0))) > 1
    return {key: float(line[key]) for key in ("x1", "y1", "x2", "y2")}


def test_ihc_draw_scale_line_updates_um_per_px_on_source_pixels(tmp_path: Path) -> None:
    app = _app()
    previous = get_language()
    set_language("zh")
    panel = IHCPanel(panel_id="Figure1IHCDrawScale")
    imported = panel.import_image(_write_ihc_draw_png(tmp_path / "ihc_draw_scale.png"))
    uid = str(imported["uid"])
    source_before = panel.source_qimage(uid).copy()
    history = list(panel.analysis.get("ihc_measurements", []))
    workspace = IHCWorkspace(QLabel("context"))
    workspace.set_panel(panel)
    workspace.show()
    workspace.resize(900, 700)
    workspace.canvas.resize(640, 480)
    workspace.canvas.set_pixmap(QPixmap.fromImage(panel.source_qimage(uid)), title=tr("histology_ihc_preview"), is_overlay=False)
    workspace.canvas.fit_to_canvas()
    app.processEvents()
    workspace.calibration_controls.length_spin.setValue(50.0)

    expected = _drag_scale_line(workspace, app)
    scale = panel.measurement_scale_bar(uid)
    length_px = hypot(expected["x2"] - expected["x1"], expected["y2"] - expected["y1"])
    assert scale["calibration_source"] == "manual_line"
    assert scale["um_per_px"] == pytest.approx(50.0 / length_px, rel=0.08)
    live = measurement_preview_line_px(workspace.canvas._measurement_scale_bar)
    assert hypot(live["x2"] - live["x1"], live["y2"] - live["y1"]) == pytest.approx(length_px, rel=0.08)
    label_points = workspace.canvas.image_label._source_line_label_points()
    assert label_points is not None
    assert workspace.canvas.image_label._pixmap_rect().contains(label_points[0])
    assert workspace.canvas.image_label._pixmap_rect().contains(label_points[1])
    _save_grab(workspace.canvas, "ihc_draw_scale_fit_zh.png")

    workspace.canvas.zoom_by(1.5)
    app.processEvents()
    zoomed = _drag_scale_line(workspace, app)
    zoom_scale = panel.measurement_scale_bar(uid)
    zoom_len = hypot(zoomed["x2"] - zoomed["x1"], zoomed["y2"] - zoomed["y1"])
    assert zoom_scale["um_per_px"] == pytest.approx(50.0 / zoom_len, rel=0.08)
    _save_grab(workspace.canvas, "ihc_draw_scale_zoom_zh.png")

    assert panel.analysis.get("ihc_measurements", []) == history
    assert panel.source_qimage(uid).pixel(0, 0) == source_before.pixel(0, 0)
    project = Project(title="ihc-scale")
    project.add_panel(panel)
    archive = tmp_path / "ihc_scale.sfig"
    project.save_sfig(archive)
    restored = Project.load_sfig(archive)
    restored_panel = next(item for item in restored.panels if isinstance(item, IHCPanel))
    restored_uid = str(restored_panel.images()[0]["uid"])
    assert restored_panel.measurement_scale_bar(restored_uid)["um_per_px"] == pytest.approx(zoom_scale["um_per_px"], rel=0.08)
    set_language(previous)
    _close(workspace, app)


def test_fluoro_roi_intensity_gap_is_compact_at_desktop_sizes(desktop_screen, tmp_path: Path, monkeypatch) -> None:
    app = _app()
    previous = get_language()
    window = MainWindow(resources_dir=RESOURCES)
    try:
        window.set_module("fluoro")
        window.import_fluorescence_images([str(_write_fluoro_tiff(tmp_path / "roi_gap.tif"))])
        _wait_busy(window, "import:fluoro")
        workspace = window.fluoro_workspace
        ancestor = workspace.workflow_panel
        for width, height, suffix in ((1280, 800, "1280x800"), (1920, 1080, "1920x1080")):
            window.resize(width, height)
            window.show()
            workspace.set_step(2)
            workspace._set_measure_mode("roi_intensity")
            app.processEvents()
            panel = workspace.panel
            assert panel is not None and panel.active_image() is not None
            panel.create_roi_intensity_measurement(
                str(panel.active_image()["uid"]),
                "rectangle",
                {"x": 3, "y": 2, "w": 5, "h": 4},
                [0],
            )
            workspace.refresh()
            workspace.open_roi_results_button.click()
            app.processEvents()
            assert workspace.measurement_table.rowCount() == 1
            _save_grab(ancestor, f"fluoro_roi_afterfix_measured_{suffix}.png")
            gap = _vertical_gap(workspace.clear_roi_button, workspace.results_drawer, ancestor)
            params_extra = workspace.measure_params_stack.height() - workspace.roi_intensity_params_panel.height()
            summary_extra = workspace.measure_summary_stack.height() - workspace.roi_intensity_summary_panel.height()
            summary_h = max(workspace.roi_intensity_summary_panel.height(), workspace.roi_intensity_summary_panel.sizeHint().height())
            assert workspace.clear_roi_button.isVisibleTo(ancestor)
            assert workspace.results_drawer.isVisibleTo(ancestor)
            assert workspace.fluoro_measurement_excel_button.isVisibleTo(ancestor)
            visible_clears = [
                button
                for button in (workspace.clear_roi_button, workspace.clear_roi_measurements_button)
                if button.isVisibleTo(ancestor)
            ]
            assert visible_clears == [workspace.clear_roi_button]
            assert params_extra <= 24, f"{suffix} params_extra={params_extra}"
            assert summary_extra <= 24, f"{suffix} summary_extra={summary_extra}"
            assert gap <= summary_h + 48, f"{suffix} gap={gap} summary_h={summary_h}"

            from PyQt6.QtWidgets import QMessageBox
            with monkeypatch.context() as confirm:
                confirm.setattr(QMessageBox, 'question', lambda *args: QMessageBox.StandardButton.Yes)
                workspace.clear_roi_button.click()
            app.processEvents()
            assert workspace.measurement_table.rowCount() == 0
            _save_grab(ancestor, f"fluoro_roi_afterfix_cleared_{suffix}.png")
            gap_after = _vertical_gap(workspace.clear_roi_button, workspace.results_drawer, ancestor)
            assert gap_after <= summary_h + 48, f"{suffix} after-clear gap={gap_after}"

            set_language("zh")
            workspace.retranslate()
            app.processEvents()
            zh_gap = _vertical_gap(workspace.clear_roi_button, workspace.results_drawer, ancestor)
            assert zh_gap <= summary_h + 64, f"{suffix} zh gap={zh_gap}"
            set_language("en")
            workspace.retranslate()
            app.processEvents()

            workspace._set_measure_mode("ai_assisted")
            app.processEvents()
            assert workspace.measure_params_stack.currentWidget() is workspace.ai_assisted_params_panel
            assert workspace.ai_results_table.isVisibleTo(ancestor)
            workspace._set_measure_mode("roi_intensity")
    finally:
        set_language(previous)
        _close(window, app)


def test_ihc_completed_analysis_keeps_original_overlay_toggle(tmp_path: Path, desktop_screen) -> None:
    app = _app()
    previous = get_language()
    set_language("zh")
    window = MainWindow(resources_dir=RESOURCES)
    try:
        image_path = _write_ihc_png(tmp_path / "ihc_toggle.png")
        window.set_module("ihc")
        window.import_ihc_images([str(image_path)])
        _wait_busy(window, "import:ihc")
        window.data_panel.ihc_min_area_spin.setValue(2)
        window.set_ihc_workflow_step(1)
        window.run_ihc_analysis()
        _wait_busy(window, "ihc")
        workspace = window.ihc_workspace
        panel = window._active_ihc_panel()
        measurement = panel.analysis["ihc_measurements"][-1]
        uuid = measurement["measurement_uuid"]
        renders = {"count": 0}
        original_render = panel.render_measurement_overlay

        def counting_render(**kwargs):
            renders["count"] += 1
            return original_render(**kwargs)

        panel.render_measurement_overlay = counting_render
        app.processEvents()
        _assert_toggle_usable(workspace, overlay_enabled=True)
        assert workspace.canvas.has_original_pixmap()
        assert workspace.canvas.has_overlay_pixmap()
        overlay_color = _source_color(workspace.canvas)
        baseline_renders = renders["count"]
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "original"
        original_color = _source_color(workspace.canvas)
        assert original_color != overlay_color
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "overlay"
        assert _source_color(workspace.canvas) == overlay_color
        assert renders["count"] == baseline_renders
        assert panel.analysis["ihc_measurements"][-1]["measurement_uuid"] == uuid
        assert window.data_panel.ihc_results_table.rowCount() == 1
        _save_grab(workspace.canvas, "ihc_toggle_overlay_zh.png")
        workspace.original_preview_button.click()
        app.processEvents()
        _save_grab(workspace.canvas, "ihc_toggle_original_zh.png")
        window.set_ihc_roi_from_canvas(QRect(0, 0, 2, 4))
        window.run_ihc_analysis()
        _wait_busy(window, "ihc")
        first_uuid = panel.analysis["ihc_measurements"][0]["measurement_uuid"]
        window.data_panel.ihc_results_table.selectRow(0)
        app.processEvents()
        assert window.data_panel.selected_ihc_measurement_uuid() == first_uuid
        history_renders = renders["count"]
        original_key = workspace.canvas._original_pixmap.cacheKey()
        overlay_key = workspace.canvas._overlay_pixmap.cacheKey()
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == original_key
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == overlay_key
        assert renders["count"] == history_renders
    finally:
        set_language(previous)
        _close(window, app)


def test_brightfield_completed_analysis_paths_keep_original_overlay_toggle(tmp_path: Path, desktop_screen) -> None:
    app = _app()
    previous = get_language()
    set_language("zh")
    window = MainWindow(resources_dir=RESOURCES)
    try:
        window.set_module("brightfield")
        window.import_brightfield_images([str(_write_he_png(tmp_path / "bf_he.png"))])
        _wait_busy(window, "import:brightfield")
        window.data_panel.brightfield_stain_type_combo.setCurrentText("H&E")
        window.update_brightfield_image_metadata()
        window.set_brightfield_workflow_step(2)
        window.run_brightfield_analysis()
        _wait_busy(window, "brightfield")
        workspace = window.brightfield_workspace
        panel = window._active_brightfield_panel()
        uuid = panel.analysis["brightfield_measurements"][-1]["measurement_uuid"]
        _assert_toggle_usable(workspace, overlay_enabled=True)
        assert workspace.canvas.has_original_pixmap()
        assert workspace.canvas.has_overlay_pixmap()
        original_key = workspace.canvas._original_pixmap.cacheKey()
        overlay_key = workspace.canvas._overlay_pixmap.cacheKey()
        assert original_key != overlay_key
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "original"
        assert workspace.canvas._source_pixmap.cacheKey() == original_key
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "overlay"
        assert workspace.canvas._source_pixmap.cacheKey() == overlay_key
        assert panel.analysis["brightfield_measurements"][-1]["measurement_uuid"] == uuid

        rgb = np.full((180, 260, 3), 120, np.uint8)
        import cv2

        cv2.circle(rgb, (60, 70), 12, (240, 240, 240), -1)
        cv2.ellipse(rgb, (160, 90), (35, 6), -25, 0, 360, (38, 38, 38), -1)
        cell_path = tmp_path / "cells.png"
        cv2.imwrite(str(cell_path), rgb)
        cell_panel = window._active_brightfield_panel()
        cell_panel.data["brightfield_images"] = [
            dict(
                uid="image-1",
                source_path=str(cell_path),
                source_name="image-1.png",
                metadata={"width_px": 260, "height_px": 180},
                measurements=[],
                measurement_scale_bar={"um_per_px": 0.5, "length_um": 100.0, "calibration_source": "manual"},
                roi={"mode": "whole_image", "items": [], "active_item_uuid": None},
            ),
            dict(
                uid="image-2",
                source_path=str(cell_path),
                source_name="image-2.png",
                metadata={"width_px": 260, "height_px": 180},
                measurements=[],
                measurement_scale_bar={"um_per_px": 0.5, "length_um": 100.0, "calibration_source": "manual"},
                roi={"mode": "whole_image", "items": [], "active_item_uuid": None},
            ),
        ]
        cell_panel.data["active_image_uid"] = "image-1"
        window.data_panel.brightfield_measure_type_combo.setCurrentIndex(0)
        window._refresh_brightfield_preview(cell_panel)
        window.data_panel.brightfield_bright_round_cell_measure_button.click()
        _wait_busy(window, "brightfield-cellpose")
        _assert_toggle_usable(workspace, overlay_enabled=True)
        assert workspace.canvas.has_original_pixmap()
        assert workspace.canvas.has_overlay_pixmap()
        cell_original = workspace.canvas._original_pixmap.cacheKey()
        cell_overlay = workspace.canvas._overlay_pixmap.cacheKey()
        assert cell_original != cell_overlay
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "original"
        assert workspace.canvas._source_pixmap.cacheKey() == cell_original
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.current_preview_mode() == "overlay"
        assert workspace.canvas._source_pixmap.cacheKey() == cell_overlay
        window.select_brightfield_image("image-2")
        app.processEvents()
        assert not workspace.canvas.has_overlay_pixmap()
        window.select_brightfield_image("image-1")
        app.processEvents()
        assert workspace.canvas.has_overlay_pixmap()

        fiber_path = _write_he_fiber_png(tmp_path / "he_fiber.png")
        window.import_brightfield_images([str(fiber_path)])
        _wait_busy(window, "import:brightfield")
        fiber_panel = window._active_brightfield_panel()
        fiber_uid = str(fiber_panel.active_image()["uid"])
        fiber_panel.calibrate_he_fiber_area(image_uid=fiber_uid)
        first_fiber = fiber_panel.create_ai_object_count_measurement(image_uid=fiber_uid)
        second_fiber = fiber_panel.create_ai_object_count_measurement(image_uid=fiber_uid)
        window._refresh_brightfield_preview(fiber_panel, measurement_uuid=str(second_fiber["measurement_uuid"]))
        app.processEvents()
        _assert_toggle_usable(workspace, overlay_enabled=True)
        fiber_renders = {"count": 0}
        original_fiber_render = fiber_panel.render_measurement_overlay

        def count_fiber(**kwargs):
            fiber_renders["count"] += 1
            return original_fiber_render(**kwargs)

        fiber_panel.render_measurement_overlay = count_fiber
        fiber_original = workspace.canvas._original_pixmap.cacheKey()
        fiber_overlay = workspace.canvas._overlay_pixmap.cacheKey()
        assert fiber_original != fiber_overlay
        baseline_fiber = fiber_renders["count"]
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == fiber_original
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == fiber_overlay
        assert fiber_renders["count"] == baseline_fiber
        window.select_brightfield_ai_measurement_result(str(first_fiber["measurement_uuid"]))
        app.processEvents()
        history_overlay = workspace.canvas._overlay_pixmap.cacheKey()
        history_original = workspace.canvas._original_pixmap.cacheKey()
        workspace.original_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == history_original
        workspace.overlay_preview_button.click()
        app.processEvents()
        assert workspace.canvas._source_pixmap.cacheKey() == history_overlay
    finally:
        set_language(previous)
        _close(window, app)


def test_ihc_source_without_overlay_shows_disabled_overlay_button() -> None:
    app = _app()
    workspace = IHCWorkspace(QLabel("context"))
    # MainWindow hosts this detached workflow panel beside the canvas. Supply
    # the same visible host in the standalone workspace test.
    workspace.layout().addWidget(workspace.workflow_panel)
    workspace.show()
    workspace.set_step(0)
    workspace.canvas.set_pixmap(_pixmap("#AA0000"), title="Histology / IHC preview")
    app.processEvents()
    _assert_toggle_usable(workspace, overlay_enabled=False)
    _close(workspace, app)


def _wrapped_page(text: str) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    label = QLabel(text)
    label.setWordWrap(True)
    layout.addWidget(label)
    return page


def test_noncompact_stacked_widget_height_for_width_matches_qstackedwidget() -> None:
    app = _app()
    short = "短标签"
    long = "这是一段会在窄宽度下换行的说明文字，用于验证非 compact 堆叠页仍走默认 QStackedWidget 高度。" * 12
    plain = QStackedWidget()
    plain.addWidget(_wrapped_page(short))
    plain.addWidget(_wrapped_page(long))
    plain.setCurrentIndex(0)
    noncompact = CurrentPageStackedWidget(compact=False)
    noncompact.addWidget(_wrapped_page(short))
    noncompact.addWidget(_wrapped_page(long))
    noncompact.setCurrentIndex(0)
    compact = CurrentPageStackedWidget(compact=True)
    compact.addWidget(_wrapped_page(short))
    compact.addWidget(_wrapped_page(long))
    compact.setCurrentIndex(0)
    app.processEvents()
    assert noncompact.hasHeightForWidth() == plain.hasHeightForWidth()
    for width in (120, 240, 400):
        assert noncompact.heightForWidth(width) == plain.heightForWidth(width)
    compact.setCurrentIndex(0)
    short_h = compact.heightForWidth(160)
    compact.setCurrentIndex(1)
    long_h = compact.heightForWidth(160)
    assert long_h > short_h
    _close(plain, app)
    _close(noncompact, app)
    _close(compact, app)


def _apply_app_look(app: QApplication) -> None:
    qss = load_qss(RESOURCES).replace('"IBM Plex Sans", "Segoe UI"', '"Microsoft YaHei", "Segoe UI"')
    app.setStyleSheet(qss)
    font = QFont("Microsoft YaHei")
    if not font.exactMatch():
        font = QFont("Segoe UI")
    font.setPointSize(12)
    app.setFont(font)


def _union_on(content: QWidget, widgets: list[QWidget]) -> QRect:
    rects = []
    for widget in widgets:
        top_left = widget.mapTo(content, QPoint(0, 0))
        rects.append(QRect(top_left, widget.size()))
    union = rects[0]
    for rect in rects[1:]:
        union = union.united(rect)
    return union.adjusted(-8, -8, 8, 8)


def test_qss_chinese_roi_and_ihc_sidepanel_screenshots(desktop_screen, tmp_path: Path) -> None:
    app = _app()
    previous = get_language()
    set_language("zh")
    _apply_app_look(app)
    window = MainWindow(resources_dir=RESOURCES)
    clipping: list[str] = []
    try:
        window.set_module("fluoro")
        window.import_fluorescence_images([str(_write_fluoro_tiff(tmp_path / "roi_qss.tif"))])
        _wait_busy(window, "import:fluoro")
        window.resize(1280, 800)
        window.show()
        workspace = window.fluoro_workspace
        workspace.set_step(2)
        workspace._set_measure_mode("roi_intensity")
        app.processEvents()
        panel = workspace.panel
        assert panel is not None and panel.active_image() is not None
        panel.create_roi_intensity_measurement(str(panel.active_image()["uid"]), "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
        workspace.refresh()
        workspace.open_roi_results_button.click()
        for _ in range(8):
            app.processEvents()
        scroll = workspace.workflow_panel.findChild(QScrollArea, "FluorescenceWorkflowRightScroll")
        assert scroll is not None
        content = scroll.widget()
        region = _union_on(
            content,
            [workspace.clear_roi_button, workspace.roi_intensity_summary_panel, workspace.results_drawer],
        )
        if workspace.clear_roi_button.height() < workspace.clear_roi_button.sizeHint().height():
            clipping.append("fluoro Clear ROI clipped")
        _save_grab_rect(content, region, "fluoro_roi_qss_zh_1280x800_clear_to_table.png")
        assert workspace.clear_roi_button.isVisibleTo(content)
        assert workspace.results_drawer.isVisibleTo(content)
        assert workspace.measurement_table.rowCount() == 1

        window.set_module("ihc")
        window.import_ihc_images([str(_write_ihc_png(tmp_path / "ihc_qss.png"))])
        _wait_busy(window, "import:ihc")
        window.data_panel.ihc_min_area_spin.setValue(2)
        window.set_ihc_workflow_step(1)
        window.run_ihc_analysis()
        _wait_busy(window, "ihc")
        ihc = window.ihc_workspace
        # Calibration lives at intake in the current workflow, without a drawer.
        window.set_ihc_workflow_step(0)
        app.processEvents()
        ihc_scroll = ihc.workflow_panel.findChild(QScrollArea, "IHCWorkflowRightScroll")
        assert ihc_scroll is not None
        ihc_content = ihc_scroll.widget()
        ihc_region = _union_on(
            ihc_content,
            [ihc.draw_scale_button, ihc.preview_toggle_panel, ihc.original_preview_button, ihc.overlay_preview_button],
        )
        _save_grab_rect(ihc_content, ihc_region, "ihc_sidepanel_qss_zh_drawscale_toggle.png")
        assert ihc.draw_scale_button.isVisibleTo(ihc_content)
        _assert_toggle_usable(ihc, overlay_enabled=False)
        if clipping:
            raise AssertionError("; ".join(clipping))
    finally:
        set_language(previous)
        app.setStyleSheet("")
        _close(window, app)


