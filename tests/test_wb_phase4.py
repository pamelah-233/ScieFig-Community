from __future__ import annotations

import os
import pytest
from pathlib import Path
from zipfile import ZipFile

from PyQt6.QtGui import QColor, QImage, QKeyEvent, QPainter, QTransform
from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PyQt6.QtSvgWidgets import QGraphicsSvgItem
from PyQt6.QtWidgets import QGraphicsItem, QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsTextItem, QGraphicsView, QLabel, QScrollArea
from PyQt6.QtWidgets import QApplication

from core.constants import WB_STEP_CROP, WB_STEP_DENSITY, WB_STEP_EXPORT, WB_STEP_IMPORT, WB_STEP_LABELS, WB_STEP_LAYOUT, WB_STEP_STATS
from core.export import export_wb_figure_scene, export_wb_source_data
from core.models import ExternalImagePanel, WesternBlotPanel
from ui.main_window import MainWindow
from ui.dialogs.wb_export_dialog import WBExportDialog
from ui.widgets.wb_canvas import WBCanvas, _event_viewport_point


def _write_test_blot(path: Path, width: int = 240, height: int = 120) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFFEFEFEF)
    assert image.save(str(path))
    return path


def _write_blot_with_lanes(path: Path, lane_centers: list[int], width: int = 240, height: int = 120) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0xFFEFEFEF)
    painter = QPainter(image)
    painter.setBrush(QColor("#050505"))
    painter.setPen(QColor("#050505"))
    for center in lane_centers:
        painter.drawRect(center - 4, 54, 8, 12)
    painter.end()
    assert image.save(str(path))
    return path


def _write_wb_lane_strip(path: Path, lane_values: list[int], width: int = 120, height: int = 30) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#000000"))
    painter = QPainter(image)
    lane_width = width // len(lane_values)
    for index, value in enumerate(lane_values):
        color = QColor(value, value, value)
        painter.setBrush(color)
        painter.setPen(color)
        painter.drawRect(index * lane_width + 8, 8, lane_width - 16, height - 16)
    painter.end()
    assert image.save(str(path))
    return path


def test_wb_viewport_point_helper_accepts_qpoint_position_payload() -> None:
    class _FakeEvent:
        def position(self) -> QPoint:
            return QPoint(12, 34)

    point = _event_viewport_point(_FakeEvent())

    assert point == QPoint(12, 34)


def test_wb_panel_import_records_source_metadata_and_hash(tmp_path) -> None:
    blot_path = _write_test_blot(tmp_path / "wb_full.png")
    panel = WesternBlotPanel(panel_id="Figure1B")

    image_data = panel.import_full_blot(blot_path)

    assert image_data["original_source"] == "wb_full.png"
    assert image_data["original_hash"].startswith("sha256:")
    assert image_data["width_px"] == 240
    assert image_data["height_px"] == 120
    assert panel.data["raw_copy_path"] == "data/wb/wb_full.png"
    assert panel.analysis["integrity_badge"]["status"] in {"pass", "warning"}


def test_wb_panel_records_non_destructive_crop_annotations_and_display_adjustments(tmp_path) -> None:
    blot_path = _write_test_blot(tmp_path / "wb_crop.png")
    panel = WesternBlotPanel(panel_id="Figure1B")
    panel.import_full_blot(blot_path)

    panel.set_display_adjustments(brightness=12, contrast=20)
    panel.set_annotations(["Lane1", "Lane2"], ["SPNS1"], ["50 kDa"])
    crop = panel.set_crop_roi(10, 20, 100, 40, protein="SPNS1")

    assert crop["roi"] == {"x": 10, "y": 20, "w": 100, "h": 40}
    assert crop["non_destructive"] is True
    assert panel.data["adjustments"]["display_only"] is True
    assert panel.data["annotations"]["lane_labels"] == ["Lane1", "Lane2"]
    assert len(panel.audit_log) >= 4


def test_wb_panel_band_configuration_and_reset_are_non_destructive(tmp_path) -> None:
    blot_path = _write_test_blot(tmp_path / "wb_config.png")
    panel = WesternBlotPanel(panel_id="Figure1B")
    panel.import_full_blot(blot_path)
    panel.set_crop_roi(0, 0, 120, 40, protein="")
    panel.set_crop_roi(0, 50, 120, 40, protein="")

    layout = panel.set_band_configuration(
        [{"protein": "SPNS1", "mw_kda": "70"}, {"protein": "GAPDH", "mw_kda": "37"}],
        ["Ctrl", "KD-1", "KD-2"],
        loading_control_index=1,
    )

    assert layout["enabled"] is True
    assert panel.data["crops"][0]["protein"] == "SPNS1"
    assert panel.data["annotations"]["lane_labels"] == ["Ctrl", "KD-1", "KD-2"]
    assert panel.data["adjustments"]["display_only"] is True

    panel.reset_to_original()

    assert panel.data["crops"] == []
    assert panel.data["wb_layout"]["enabled"] is False
    assert panel.data["adjustments"]["rotation"] == 0.0


def test_wb_source_data_export_contains_full_blot_crops_and_audit(tmp_path) -> None:
    blot_path = _write_test_blot(tmp_path / "wb_export.png")
    panel = WesternBlotPanel(panel_id="Figure1B")
    panel.import_full_blot(blot_path)
    panel.set_crop_roi(0, 0, 120, 60, protein="SPNS1")

    output = export_wb_source_data(panel, tmp_path / "wb_source.zip")

    assert output.exists()
    with ZipFile(output) as archive:
        names = set(archive.namelist())
    assert "Figure1B_wb_fullblot.png" in names
    assert "Figure1B_wb_crops.json" in names
    assert "Figure1B_audit.log" in names
    assert "Figure1B_source_manifest.json" in names


def test_wb_ui_imports_dropped_external_image(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "dragged_wb.png")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    window.handle_external_files_dropped([str(blot_path)])

    panel = window._active_wb_panel()
    assert panel.data["image_data"]["original_source"] == "dragged_wb.png"
    assert window.data_panel.wb_w_spin.value() == 240
    assert window.data_panel.wb_h_spin.value() == 120
    assert any(isinstance(item, QGraphicsPixmapItem) for item in window.wb_canvas.scene.items())
    window.close()
    app.processEvents()


def test_wb_canvas_roi_selection_records_crop(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "roi_select.png")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))

    window.set_wb_roi_from_canvas(QRectF(20, 10, 80, 30))

    panel = window._active_wb_panel()
    assert panel.data["crops"][-1]["roi"] == {"x": 20, "y": 10, "w": 80, "h": 30}
    assert window.data_panel.wb_x_spin.value() == 20
    assert window.data_panel.wb_h_spin.value() == 30
    window.close()
    app.processEvents()


@pytest.mark.parametrize("count,batch", [(1, False), (3, False), (3, True)])
def test_wb_first_crop_after_import_uses_displayed_image(tmp_path, desktop_screen, count, batch) -> None:
    from PyQt6.QtTest import QTest

    app = QApplication.instance() or QApplication([])
    paths = [str(_write_test_blot(tmp_path / f"image_{i}.png", 480 + 240 * i, 240 + 60 * i))
             for i in range(count)]
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.show()

    if batch:
        window.import_wb_images(paths)
    else:
        for path in paths:
            window.import_wb_image(path)
    window.data_panel.set_wb_step(WB_STEP_CROP)
    QTest.qWait(200)
    panel = window._active_wb_panel()
    index = count - 1
    assert window.data_panel.selected_wb_strip_index() == index
    assert panel.data.get("selected_strip_index", 0) == index
    assert window.wb_canvas._strip_display_rects[0]["index"] == index
    # Restored projects may render before the staging table/ROI controls hydrate.
    window.data_panel.wb_strip_table.clearSelection()
    window._wb_selected_strip_index = 0
    for spin in (window.data_panel.wb_x_spin, window.data_panel.wb_y_spin,
                 window.data_panel.wb_w_spin, window.data_panel.wb_h_spin):
        spin.setRange(0, 0)
    # Both the first drag and a second drag after zooming need no reselection.
    for zoom in (1.0, 1.25):
        if zoom != 1.0:
            window.wb_canvas._view_controller.zoom_by(zoom)
        view = window.wb_canvas.view
        start = view.mapFromScene(QPointF(40, 30))
        end = view.mapFromScene(QPointF(200, 90))
        expected = view.mapToScene(start), view.mapToScene(end)
        QTest.mousePress(view.viewport(), Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(view.viewport(), end, delay=30)
        QTest.mouseRelease(view.viewport(), Qt.MouseButton.LeftButton, pos=end)
        roi = (panel.data["crops"][-1]["roi"] if count == 1
               else panel.data["band_strips"][index]["crop_roi"])
        for key, value in zip(("x", "y", "w", "h"),
                              (expected[0].x(), expected[0].y(),
                               expected[1].x() - expected[0].x(), expected[1].y() - expected[0].y())):
            assert abs(roi[key] - value) < 2
    if count > 1:
        assert panel.data["band_strips"][0]["crop_roi"] == {"x": 0, "y": 0, "w": 480, "h": 240}
        window.data_panel.wb_strip_table.selectRow(0)
        assert panel.data["selected_strip_index"] == 0
        assert window.wb_canvas._strip_display_rects[0]["index"] == 0
    window.close()
    app.processEvents()


def test_wb_roi_mode_disables_image_drag_until_move_mode(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "roi_mode.png")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))

    window.enable_wb_roi_mode()
    assert window.wb_canvas.view.mode == "roi"
    assert not bool(window.wb_canvas._group.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)

    window.enable_wb_move_mode()
    assert window.wb_canvas.view.mode == "move"
    assert bool(window.wb_canvas._group.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
    window.close()
    app.processEvents()


def test_wb_preview_draws_lane_protein_and_marker_annotations(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "annotated.png")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("L1,L2,L3")
    window.data_panel.wb_protein_edit.setText("SPNS1")
    window.data_panel.wb_marker_edit.setText("50 kDa,75 kDa")
    window.set_wb_roi_from_canvas(QRectF(0, 0, 180, 80))

    window.set_wb_lane_anchor_from_canvas(30)
    window.set_wb_lane_anchor_from_canvas(150)
    window.set_wb_marker_anchor_from_canvas(20)
    window.set_wb_marker_anchor_from_canvas(60)
    text_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem)]
    line_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsLineItem)]

    assert any(item.toPlainText() == "L1" for item in text_items)
    assert any(item.toPlainText() == "SPNS1" for item in text_items)
    assert any(item.toPlainText() == "50 kDa" for item in text_items)
    assert len(line_items) >= 5
    assert window._active_wb_panel().data["annotations"]["lane_labels"] == ["L1", "L2", "L3"]
    assert window._active_wb_panel().data["annotations"]["lane_anchors"] == [30.0, 90.0, 150.0]
    assert window._active_wb_panel().data["annotations"]["markers"] == ["50 kDa", "75 kDa"]
    window.close()
    app.processEvents()


def test_wb_auto_detect_lanes_uses_projection_peaks(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_blot_with_lanes(tmp_path / "auto_lanes.png", [40, 100, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("A,B,C")

    window.auto_detect_wb_lanes()

    anchors = window._active_wb_panel().data["annotations"]["lane_anchors"]
    assert len(anchors) == 3
    assert abs(anchors[0] - 40) <= 6
    assert abs(anchors[1] - 100) <= 6
    assert abs(anchors[2] - 160) <= 6
    window.close()
    app.processEvents()


def test_wb_canvas_records_transform_and_drag_position(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "transform.png")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))

    window.data_panel.wb_rotation_spin.setValue(1.2)
    window.flip_wb_horizontal()
    window.enable_wb_move_mode()
    window.wb_canvas._group.setPos(35, 42)

    panel = window._active_wb_panel()
    assert panel.data["adjustments"]["rotation"] == 1.2
    assert panel.data["adjustments"]["flip_horizontal"] is True
    assert panel.data["layout"]["image_x"] == 35.0
    assert panel.data["layout"]["image_y"] == 42.0
    window.close()
    app.processEvents()


def test_wb_nature_layout_renders_multiple_bands_and_undo_redo(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "nature_layout.png", width=260, height=160)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 10, 180, 45))
    window.set_wb_roi_from_canvas(QRectF(10, 80, 180, 45))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("Ctrl,KD-1,KD-2")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.data_panel.wb_band_table.item(0, 2).setText("70")
    window.data_panel.wb_band_table.item(1, 1).setText("GAPDH")
    window.data_panel.wb_band_table.item(1, 2).setText("37")

    window.apply_wb_nature_layout()

    texts = [item.toPlainText() for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem)]
    assert window._active_wb_panel().data["wb_layout"]["enabled"] is True
    assert {"SPNS1", "GAPDH", "70", "37", "Ctrl"}.issubset(set(texts))

    window.undo_wb()
    assert window._active_wb_panel().data.get("wb_layout", {}).get("enabled") is not True

    window.redo_wb()
    assert window._active_wb_panel().data["wb_layout"]["enabled"] is True
    window.close()
    app.processEvents()


def test_wb_nature_lane_labels_use_band_centers_not_marker_position(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "lane_centers.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 180, 45))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("1,2,3")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.data_panel.wb_band_table.item(0, 2).setText("70")

    window.apply_wb_nature_layout()

    text_items = {item.toPlainText(): item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem)}
    expected_centers = [64 + 30, 64 + 90, 64 + 150]
    actual_centers = [text_items[label].sceneBoundingRect().center().x() for label in ["1", "2", "3"]]
    marker_center = text_items["70"].sceneBoundingRect().center().x()

    for actual, expected in zip(actual_centers, expected_centers, strict=True):
        assert abs(actual - expected) <= 2.5
    assert marker_center < actual_centers[0] - 20
    from ui.widgets.canvas_graphics_adapter import CanvasExportBackgroundItem
    from ui.widgets.shared_layout_shapes import SharedResizeHandle
    assert not any(isinstance(item, QGraphicsRectItem) and not isinstance(item, CanvasExportBackgroundItem)
                   and not isinstance(item, SharedResizeHandle) for item in window.wb_canvas.scene.items())
    window.close()
    app.processEvents()


def test_wb_layout_text_style_applies_to_all_same_kind(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "text_style.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 180, 45))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("Ctrl,KD")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()

    window.update_wb_layout_text_style("lane", 0, {"font_family": "Times New Roman", "font_size_pt": 12, "rotation": 45.0, "bold": True, "italic": True, "color": "#C0392B"}, True)

    lane_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "KD"}]
    assert len(lane_items) == 2
    assert all(item.font().family() == "Times New Roman" for item in lane_items)
    assert all(item.font().pointSize() == 12 for item in lane_items)
    assert all(item.rotation() == 45.0 for item in lane_items)
    assert all(item.font().bold() for item in lane_items)
    assert all(item.font().italic() for item in lane_items)
    assert all(item.defaultTextColor().name().lower() == "#c0392b" for item in lane_items)
    window.close()
    app.processEvents()


def test_wb_hierarchy_renders_group_brackets_conditions_gap_and_sides(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "hierarchy.png", width=320, height=150)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(20, 80, 240, 45))
    window.data_panel.wb_lane_count_spin.setValue(8)
    window.data_panel.wb_lane_edit.setText("1,2,3,4,5,6,7,8")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("mAb-His")
    window.data_panel.wb_band_table.item(0, 2).setText("35")
    window.data_panel.wb_protein_side_combo.setCurrentText("left")
    window.data_panel.wb_marker_side_combo.setCurrentText("right")
    window.data_panel.show_wb_group_level_editor()
    window.data_panel.wb_hierarchy_total_lanes_spin.setValue(8)
    window.data_panel.wb_group_levels_edit.setPlainText("Level 1: WT=1-4; BF.7=5-8\nLevel 2: 76E1=1-2; COV44-62=3-4; 76E1=5-6; COV44-62=7-8")
    window.data_panel.show_wb_condition_row_editor()
    window.data_panel.wb_condition_rows_edit.setPlainText("ACE2: -, +, -, +, -, +, -, +\nAb: -, -, +, +, -, -, +, +")
    window.data_panel.wb_group_gap_edit.setText("4:12")

    window.apply_wb_nature_layout()

    texts = {item.toPlainText(): item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem)}
    assert {"WT", "BF.7", "76E1", "COV44-62", "ACE2", "Ab", "mAb-His", "35"}.issubset(set(texts))
    assert texts["mAb-His"].sceneBoundingRect().right() < 64
    assert texts["35"].sceneBoundingRect().left() > 64 + 240
    assert texts["BF.7"].sceneBoundingRect().center().x() - texts["WT"].sceneBoundingRect().center().x() > 120
    assert len([item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line"]) >= 6
    layout = window._active_wb_panel().data["wb_layout"]
    assert layout["lane_hierarchy"]["total_lanes"] == 8
    assert layout["group_gaps"] == [{"after_lane": 4, "gap_width_pt": 12.0}]
    window.close()
    app.processEvents()


def test_wb_figure_scene_exports_vector_and_raster_formats(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "export_scene.png", width=180, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 10, 120, 40))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("A,B")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()

    for suffix in [".pdf", ".svg", ".png", ".tiff"]:
        target = export_wb_figure_scene(window.wb_canvas.scene, tmp_path / f"wb_export{suffix}", dpi=300)
        assert target.exists()
        assert target.stat().st_size > 0
    window.close()
    app.processEvents()


def test_wb_round2_export_dialog_radio_indicators_are_explicit() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    dialog = WBExportDialog()
    style = dialog.styleSheet()
    assert "QRadioButton::indicator" in style
    assert "#4F8CFF" in style
    assert "QCheckBox::indicator" in style
    dialog.close()
    app.processEvents()


def test_global_dark_theme_has_visible_radio_and_checkbox_indicators() -> None:
    qss = (Path(__file__).resolve().parents[1] / "resources" / "sciefig.qss").read_text(encoding="utf-8")
    assert "QRadioButton::indicator" in qss
    assert "QCheckBox::indicator" in qss
    assert "QRadioButton::indicator:checked" in qss
    assert "QCheckBox::indicator:checked" in qss
    assert "#d97757" in qss


def test_wb_round2_hierarchy_buttons_append_multiple_rows() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.data_panel.wb_lane_count_spin.setValue(6)

    window.data_panel.show_wb_group_level_editor()
    window.data_panel.show_wb_group_level_editor()
    window.data_panel.show_wb_condition_row_editor()
    window.data_panel.show_wb_condition_row_editor()

    group_lines = [line for line in window.data_panel.wb_group_levels_edit.toPlainText().splitlines() if line.strip()]
    condition_lines = [line for line in window.data_panel.wb_condition_rows_edit.toPlainText().splitlines() if line.strip()]
    assert len(group_lines) == 2
    assert group_lines[0].startswith("Level 1:")
    assert group_lines[1].startswith("Level 2:")
    assert len(condition_lines) == 2
    assert condition_lines[0].startswith("Condition 1:")
    assert condition_lines[1].startswith("Condition 2:")
    window.close()
    app.processEvents()


def test_wb_round2_multi_strip_table_crop_adjust_move_delete(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first = _write_test_blot(tmp_path / "mAbHis.png", width=240, height=80)
    second = _write_test_blot(tmp_path / "Actin.png", width=220, height=70)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(first))
    window.import_wb_image(str(second))
    panel = window._active_wb_panel()

    assert len(panel.data["band_strips"]) == 2
    assert window.data_panel.wb_strip_table.rowCount() == 2
    assert window.data_panel.wb_band_table.rowCount() == 2

    window.data_panel.wb_strip_table.selectRow(0)
    window.data_panel.wb_protein_edit.setText("mAb-His")
    window.data_panel.wb_x_spin.setValue(10)
    window.data_panel.wb_y_spin.setValue(12)
    window.data_panel.wb_w_spin.setValue(120)
    window.data_panel.wb_h_spin.setValue(38)
    window.apply_wb_crop()
    assert panel.data["band_strips"][0]["crop_roi"] == {"x": 10, "y": 12, "w": 120, "h": 38}
    assert panel.data["band_strips"][0]["protein_name"] == "mAb-His"

    window.data_panel.wb_brightness_spin.setValue(12)
    window.data_panel.wb_rotation_spin.setValue(1.5)
    assert panel.data["band_strips"][0]["adjustments"]["brightness"] == 12
    assert panel.data["band_strips"][0]["adjustments"]["rotation"] == 1.5

    first_source = panel.data["band_strips"][0]["original_source"]
    window.move_wb_strip(1)
    assert panel.data["band_strips"][1]["original_source"] == first_source
    window.data_panel.wb_strip_table.selectRow(1)
    window.delete_wb_strip()
    assert len(panel.data["band_strips"]) == 1
    window.close()
    app.processEvents()


def test_wb_staging_area_and_step_context_are_used_for_wb(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first = _write_test_blot(tmp_path / "stage_1.png", width=180, height=60)
    second = _write_test_blot(tmp_path / "stage_2.png", width=180, height=60)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    assert window.canvas_stack.currentWidget() is window.wb_workspace
    assert window.wb_workspace.layout().count() == 1
    assert window.wb_workspace.layout().itemAt(0).widget() is window.wb_canvas
    assert window.wb_workflow_panel.parentWidget() is window.v9_shell.right_panel
    assert window.data_panel.wb_staging_panel.isVisibleTo(window.wb_workflow_panel)
    assert window.wb_staging_group.isVisibleTo(window.wb_workflow_panel)
    assert window.data_panel.wb_strip_label.isHidden()
    assert window.data_panel.wb_context_panel.isVisibleTo(window.wb_workflow_panel)
    assert window.data_panel.wb_staging_panel.parent() is not window.wb_workspace
    assert window.data_panel.wb_context_panel.parent() is not window.wb_workspace
    assert not window.data_panel.title.isVisible()
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_IMPORT

    window.import_wb_images([str(first), str(second)])
    panel = window._active_wb_panel()
    assert len(panel.data["band_strips"]) == 2
    assert window.data_panel.wb_strip_table.rowCount() == 2

    window.data_panel.set_wb_step(WB_STEP_CROP)
    assert window.wb_canvas.view.mode == "roi"
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_CROP
    assert window.wb_staging_group.isVisibleTo(window.wb_workflow_panel)

    window.data_panel.set_wb_step(WB_STEP_STATS)
    assert window.wb_staging_group.isHidden()

    window.data_panel.set_wb_step(WB_STEP_LAYOUT)
    assert window.wb_staging_group.isHidden()

    window.data_panel.set_wb_step(WB_STEP_EXPORT)
    assert window.wb_staging_group.isHidden()

    window.place_wb_strip_from_staging(0)
    assert panel.data["placed_strip_ids"] == ["strip_1"]
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_LAYOUT
    assert any(item.toPlainText() == "Drag strip here" for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem))
    status_item = window.data_panel.wb_strip_table.item(0, 0)
    assert status_item is not None
    assert "★" in status_item.text()
    window.close()
    app.processEvents()


def test_wb_phase2_layout_text_items_are_interactive_and_typed(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "text_interactive.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("A,B,C")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.data_panel.wb_band_table.item(0, 2).setText("70")
    window.apply_wb_nature_layout()

    text_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"A", "70", "SPNS1"}]
    assert len(text_items) == 3
    for item in text_items:
        flags = item.flags()
        assert bool(flags & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        assert bool(flags & QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        assert bool(flags & QGraphicsItem.GraphicsItemFlag.ItemIsFocusable)
        assert item.zValue() >= 100
        assert getattr(item, "label_type", None) in {"lane_label", "mw_marker", "protein_name"}
    window.close()
    app.processEvents()


def test_wb_labels_accept_chinese_commas_and_ctrl_a_styles_all_text(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "cn_comma.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("Ctrl，Drug1，Drug2")
    assert window.data_panel.wb_lane_labels_for_count() == ["Ctrl", "Drug1", "Drug2"]
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()

    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    window.wb_canvas.view.keyPressEvent(event)
    selected = window.selected_wb_text_items()
    assert len(selected) >= 3
    window.apply_wb_selected_text_style({"bold": True})
    bold_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "Drug1", "Drug2", "SPNS1"}]
    assert bold_items
    assert all(item.font().bold() for item in bold_items)
    window.close()
    app.processEvents()


def test_wb_layout_box_selects_multiple_text_items(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "box_select.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("Ctrl,Drug1,Drug2")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()

    lane_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "Drug1", "Drug2"}]
    select_rect = lane_items[0].sceneBoundingRect()
    for item in lane_items[1:]:
        select_rect = select_rect.united(item.sceneBoundingRect())
    window.enable_wb_text_box_select_mode()
    window.wb_canvas.view._select_text_items_in_rect(select_rect.adjusted(-4, -4, 4, 4))
    selected_text = {item.toPlainText() for item in window.selected_wb_text_items()}
    assert {"Ctrl", "Drug1", "Drug2"}.issubset(selected_text)
    window.apply_wb_selected_text_style({"italic": True})
    styled = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "Drug1", "Drug2"}]
    assert all(item.font().italic() for item in styled)
    window.close()
    app.processEvents()


def test_wb_layout_text_selection_clears_on_blank_click_after_style_change(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "blank_click_text.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("Ctrl,KD")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()
    lane_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "KD"}]
    for item in lane_items:
        item.setSelected(True)

    window.apply_wb_selected_text_style({"bold": True})
    window.wb_canvas.clear_text_selection_on_blank_click()

    assert window.selected_wb_text_items() == []
    window.wb_canvas.render()
    styled = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"Ctrl", "KD"}]
    assert styled
    assert all(item.font().bold() for item in styled)
    window.close()
    app.processEvents()


def test_wb_phase2_lane_labels_fill_and_strips_normalize_width(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first = _write_test_blot(tmp_path / "wide.png", width=260, height=80)
    second = _write_test_blot(tmp_path / "narrow.png", width=150, height=70)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(first), str(second)])
    panel = window._active_wb_panel()
    panel.update_band_strip_crop(0, 0, 0, 220, 36, protein="SPNS1")
    panel.update_band_strip_crop(1, 0, 0, 120, 36, protein="GAPDH")
    window.place_wb_strip_from_staging(0)
    window.place_wb_strip_from_staging(1)
    window.data_panel.wb_lane_count_spin.setValue(6)
    window.data_panel.wb_lane_edit.setText("L1,L2,L3,L4,L5")
    window.data_panel.populate_wb_bands(window._wb_band_sources_for_ui(panel), {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.data_panel.wb_band_table.item(0, 2).setText("55")
    window.data_panel.wb_band_table.item(1, 1).setText("GAPDH")
    window.data_panel.wb_band_table.item(1, 2).setText("37")
    window.apply_wb_nature_layout()

    label_items = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and item.toPlainText() in {"L1", "L2", "L3", "L4", "L5", "6"}]
    centers = sorted(round(item.sceneBoundingRect().center().x(), 1) for item in label_items)
    assert len(centers) == 6
    assert all(centers[index] < centers[index + 1] for index in range(5))
    gaps = [round(centers[index + 1] - centers[index], 1) for index in range(5)]
    assert max(gaps) - min(gaps) <= 1.5
    pixmaps = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPixmapItem)]
    strip_items = [item for item in pixmaps if item.pixmap().height() < 80]
    backing_widths = sorted(item.pixmap().width() for item in strip_items)
    display_widths = sorted(item.sceneBoundingRect().width() for item in strip_items)
    assert backing_widths == [120, 220]
    assert all(abs(width - 220.0) <= 0.1 for width in display_widths)
    window.close()
    app.processEvents()


def test_wb_phase2_labels_panel_is_reorganized_with_alignment_in_crop() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    window.data_panel.set_wb_step(WB_STEP_CROP)
    crop_page = window.data_panel.wb_context_stack.currentWidget()
    crop_groups = {group.title() for group in crop_page.findChildren(type(window.data_panel.wb_context_stack))}
    assert crop_groups == set()
    from PyQt6.QtWidgets import QGroupBox

    crop_titles = {group.title() for group in crop_page.findChildren(QGroupBox)}
    assert "Alignment" in crop_titles

    window.data_panel.set_wb_step(WB_STEP_LABELS)
    labels_page = window.data_panel.wb_context_stack.currentWidget()
    label_titles = {group.title() for group in labels_page.findChildren(QGroupBox)}
    assert {"Strip Metadata", "Lane Setup", "Options"}.issubset(label_titles)
    assert window.data_panel.wb_protein_edit.parentWidget() is not labels_page
    assert window.data_panel.wb_marker_edit.parentWidget() is not labels_page
    assert window.data_panel.wb_band_table.columnCount() == 4
    assert window.data_panel.wb_band_table.horizontalHeaderItem(3).text() == "Loading Ctrl"
    window.close()
    app.processEvents()


def test_wb_density_step_is_inserted_between_labels_and_layout() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    labels = [button.text().replace("○ ", "").replace("● ", "").replace("✓ ", "") for button in window.data_panel.wb_step_buttons]
    assert labels == ["Import", "Crop", "Labels", "Density", "Stats", "Layout", "Export"]
    assert window.data_panel.wb_context_stack.count() == 7

    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_DENSITY
    assert hasattr(window.data_panel, "wb_density_measure_button")

    window.data_panel.set_wb_step(WB_STEP_STATS)
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_STATS

    window.data_panel.set_wb_step(WB_STEP_EXPORT)
    assert window.data_panel.wb_context_stack.currentIndex() == WB_STEP_EXPORT
    window.close()
    app.processEvents()


def test_wb_stats_step_sits_between_density_and_layout_and_density_has_no_composer_action() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    labels = [button.text().replace("○ ", "").replace("● ", "").replace("✓ ", "") for button in window.data_panel.wb_step_buttons]
    assert labels == ["Import", "Crop", "Labels", "Density", "Stats", "Layout", "Export"]
    assert window.data_panel.wb_context_stack.count() == 7
    assert not window.data_panel.wb_density_to_composer_button.isVisible()
    assert hasattr(window.data_panel, "wb_stats_run_button")
    assert hasattr(window.data_panel, "wb_export_item_table")
    window.close()
    app.processEvents()


def test_wb_layout_exposes_one_visible_apply_button() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    assert window.data_panel.wb_apply_layout_button.text() == "Apply"
    assert not window.data_panel.wb_apply_layout_button.isHidden()
    assert window.data_panel.wb_apply_text_type_button.isHidden()
    assert window.data_panel.wb_apply_line_style_button.isHidden()
    assert window.data_panel.wb_stats_apply_style_button.isHidden()
    window.close()
    app.processEvents()


def test_wb_density_step_populates_stable_strip_and_lane_controls(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first = _write_test_blot(tmp_path / "SPNS1.png", width=120, height=30)
    second = _write_test_blot(tmp_path / "GAPDH.png", width=120, height=30)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(first), str(second)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.set_annotations(["Control", "KO", "Rescue"], [], [], lane_anchors=[20.0, 60.0, 100.0])
    panel.update_band_strip_protein(0, "SPNS1")
    panel.update_band_strip_protein(1, "GAPDH")

    window.data_panel.set_wb_step(WB_STEP_DENSITY)

    loading_items = [
        (window.data_panel.wb_density_loading_ctrl_combo.itemText(index), window.data_panel.wb_density_loading_ctrl_combo.itemData(index))
        for index in range(window.data_panel.wb_density_loading_ctrl_combo.count())
    ]
    reference_items = [
        (window.data_panel.wb_density_reference_lane_combo.itemText(index), window.data_panel.wb_density_reference_lane_combo.itemData(index))
        for index in range(window.data_panel.wb_density_reference_lane_combo.count())
    ]
    assert loading_items == [("SPNS1  (strip_1)", "strip_1"), ("GAPDH  (strip_2)", "strip_2")]
    assert reference_items == [("Control  (Lane 1)", 0), ("KO  (Lane 2)", 1), ("Rescue  (Lane 3)", 2)]
    window.close()
    app.processEvents()


def test_wb_density_measure_button_persists_results_and_populates_table(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    target = _write_wb_lane_strip(tmp_path / "SPNS1.png", [120, 220, 180])
    loading = _write_wb_lane_strip(tmp_path / "GAPDH.png", [160, 160, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(target), str(loading)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.set_annotations(["Control", "KO", "Rescue"], [], [], lane_anchors=[20.0, 60.0, 100.0])
    panel.update_band_strip_protein(0, "SPNS1")
    panel.update_band_strip_protein(1, "GAPDH")
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(
        window.data_panel.wb_density_loading_ctrl_combo.findData("strip_2")
    )
    window.data_panel.wb_density_bg_method_combo.setCurrentText("None")
    window.data_panel.wb_density_polarity_combo.setCurrentText("Dark bands on light bg")

    window.data_panel.wb_density_measure_button.click()

    result = panel.data["densitometry"]
    assert result["measurement_basis"] == "raw_cropped"
    assert result["loading_control_strip_id"] == "strip_2"
    assert window.data_panel.wb_density_result_table.rowCount() == 2
    assert window.data_panel.wb_density_result_table.columnCount() == 3
    assert window.data_panel.wb_density_result_table.verticalHeaderItem(0).text() == "SPNS1"
    assert window.data_panel.wb_density_result_table.item(0, 1).text() != ""
    window.close()
    app.processEvents()


def test_wb_density_uses_labels_step_inputs_without_apply_layout(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    target = _write_wb_lane_strip(tmp_path / "target_no_layout.png", [120, 220, 180])
    loading = _write_wb_lane_strip(tmp_path / "loading_no_layout.png", [160, 160, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(target), str(loading)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.data["wb_layout"]["enabled"] = False
    window.data_panel.wb_lane_count_spin.setValue(3)
    window.data_panel.wb_lane_edit.setText("DMSO,Star,Rescue")
    window.data_panel.populate_wb_bands(window._wb_band_sources_for_ui(panel), {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.data_panel.wb_band_table.item(1, 1).setText("GAPDH")

    window.data_panel.set_wb_step(WB_STEP_DENSITY)

    assert panel.data["wb_layout"]["enabled"] is False
    assert window.data_panel.wb_density_reference_lane_combo.itemText(0) == "DMSO  (Lane 1)"
    assert window.data_panel.wb_density_loading_ctrl_combo.itemText(1) == "GAPDH  (strip_2)"
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(1)
    window.data_panel.wb_density_bg_method_combo.setCurrentText("None")
    window.data_panel.wb_density_polarity_combo.setCurrentText("Bright bands on dark bg")
    window.data_panel.wb_density_measure_button.click()

    assert panel.data["densitometry"]["reference_lane_label"] == "DMSO"
    assert panel.data["densitometry"]["loading_control_strip_label"] == "GAPDH"
    window.close()
    app.processEvents()


def test_wb_density_copy_full_table_puts_complete_tsv_on_clipboard(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    target = _write_wb_lane_strip(tmp_path / "SPNS1.png", [120, 220, 180])
    loading = _write_wb_lane_strip(tmp_path / "GAPDH.png", [160, 160, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(target), str(loading)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.set_annotations(["Control", "KO", "Rescue"], [], [], lane_anchors=[20.0, 60.0, 100.0])
    panel.update_band_strip_protein(0, "SPNS1")
    panel.update_band_strip_protein(1, "GAPDH")
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(
        window.data_panel.wb_density_loading_ctrl_combo.findData("strip_2")
    )
    window.data_panel.wb_density_bg_method_combo.setCurrentText("None")
    window.data_panel.wb_density_polarity_combo.setCurrentText("Dark bands on light bg")
    window.data_panel.wb_density_measure_button.click()

    window.data_panel.wb_density_copy_button.click()

    copied = QApplication.clipboard().text()
    assert copied.splitlines()[0] == "strip_id\tstrip_label\tlane_index\tlane_label\traw_intensity\tbackground\tbg_subtracted\tnormalized\tfold\tqc_flags"
    assert "\tSPNS1\t1\tKO\t" in copied
    assert "\tGAPDH\t2\tRescue\t" in copied
    window.close()
    app.processEvents()


def test_wb_density_does_not_send_chart_directly_to_composer(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    target = _write_wb_lane_strip(tmp_path / "SPNS1.png", [120, 220, 180])
    loading = _write_wb_lane_strip(tmp_path / "GAPDH.png", [160, 160, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(target), str(loading)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.set_annotations(["Control", "KO", "Rescue"], [], [], lane_anchors=[20.0, 60.0, 100.0])
    panel.update_band_strip_protein(0, "SPNS1")
    panel.update_band_strip_protein(1, "GAPDH")
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(
        window.data_panel.wb_density_loading_ctrl_combo.findData("strip_2")
    )
    window.data_panel.wb_density_bg_method_combo.setCurrentText("None")
    window.data_panel.wb_density_polarity_combo.setCurrentText("Dark bands on light bg")
    window.data_panel.wb_density_measure_button.click()

    assert not window.data_panel.wb_density_to_composer_button.isVisible()
    assert not [
        item
        for item in window.project.panels
        if isinstance(item, ExternalImagePanel) and item.data.get("plot_type") == "wb_densitometry_bar"
    ]
    window.close()
    app.processEvents()


def test_wb_stats_groups_replicates_and_adds_editable_plot_to_layout(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["densitometry_runs"] = [
        {
            "measurements": [
                {
                    "strip_id": "strip_1",
                    "strip_label": "SPNS1",
                    "lanes": [
                        {"lane_index": 0, "lane_label": "DMSO", "fold": 1.0, "qc_flags": []},
                        {"lane_index": 1, "lane_label": "Drug", "fold": 2.0, "qc_flags": []},
                    ],
                }
            ]
        },
        {
            "measurements": [
                {
                    "strip_id": "strip_1",
                    "strip_label": "SPNS1",
                    "lanes": [
                        {"lane_index": 0, "lane_label": "DMSO", "fold": 1.1, "qc_flags": []},
                        {"lane_index": 1, "lane_label": "Drug", "fold": 2.4, "qc_flags": []},
                    ],
                }
            ]
        },
    ]

    window.data_panel.set_wb_step(WB_STEP_STATS)
    window.data_panel.wb_stats_group_table.item(0, 1).setText("Control")
    window.data_panel.wb_stats_group_table.item(1, 1).setText("Treatment")
    window.data_panel.wb_stats_reference_group_combo.setCurrentText("Control")
    window.data_panel.wb_stats_test_combo.setCurrentText("Welch t-test")
    window.data_panel.wb_stats_run_button.click()
    assert window.data_panel.wb_stats_chart_layout.count() == 1
    window.data_panel.wb_stats_add_to_layout_button.click()
    window.data_panel.set_wb_step(WB_STEP_LAYOUT)

    assert panel.data["wb_statistics"]["groups"][0]["group"] == "Control"
    assert panel.data["wb_statistics"]["comparisons"][0]["label"] in {"*", "**", "***", "ns", "n/a"}
    assert panel.data["wb_layout"]["stats_plot"]["enabled"] is True
    stats_items = [item for item in window.wb_canvas.scene.items() if item.data(0) == "wb_stats_plot"]
    assert stats_items
    assert all(item.parentItem() is not window.wb_canvas._group for item in stats_items)
    stats_text_items = [
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem)
        and hasattr(item, "style_payload")
        and item.style_payload()[0].startswith("stats_")
    ]
    assert stats_text_items
    assert all(item.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsFocusable for item in stats_text_items)
    window.close()
    app.processEvents()


def test_wb_stats_plot_style_controls_update_independent_layout_plot(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"] = {"enabled": True, "stats_plot": {"enabled": True}}

    window.data_panel.wb_stats_bar_fill_combo.setCurrentText("#D97757")
    window.data_panel.wb_stats_bar_edge_combo.setCurrentText("#1F4E79")
    window.data_panel.wb_stats_bar_width_spin.setValue(0.42)
    window.data_panel.wb_stats_bar_line_width_spin.setValue(2.0)
    window.data_panel.wb_stats_point_shape_combo.setCurrentText("square")
    window.data_panel.wb_stats_point_size_spin.setValue(8.0)
    window.data_panel.wb_stats_point_color_combo.setCurrentText("#C0392B")
    window.apply_wb_stats_plot_style()

    style = panel.data["wb_layout"]["stats_plot"]["style"]
    assert style["bar_fill_color"] == "#D97757"
    assert style["bar_edge_color"] == "#1F4E79"
    assert style["bar_width_fraction"] == 0.42
    assert style["bar_line_width"] == 2.0
    assert style["point_shape"] == "square"
    assert style["point_size"] == 8.0
    assert style["point_color"] == "#C0392B"
    assert window.wb_canvas._stats_group is not None
    assert window.wb_canvas._stats_group.parentItem() is None
    window.close()
    app.processEvents()


def test_wb_color_controls_show_chinese_labels_but_apply_hex_values(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "wb_color_labels.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2"], [], [])
    panel.set_crop_roi(10, 45, 160, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2"],
        lane_hierarchy={
            "total_lanes": 2,
            "levels": [{"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]}],
        },
    )
    window.wb_canvas.set_panel(panel)

    fill_combo = window.data_panel.wb_stats_bar_fill_combo
    assert "陶土橙" in fill_combo.itemText(1)
    assert "(#D97757)" in fill_combo.itemText(1)
    fill_combo.setCurrentIndex(fill_combo.findData("#D97757"))
    window.data_panel.wb_stats_bar_edge_combo.setCurrentIndex(window.data_panel.wb_stats_bar_edge_combo.findData("#1F4E79"))
    window.data_panel.wb_stats_point_color_combo.setCurrentIndex(window.data_panel.wb_stats_point_color_combo.findData("#C0392B"))
    style = window.data_panel.wb_stats_plot_style()
    assert style["bar_fill_color"] == "#D97757"
    assert style["bar_edge_color"] == "#1F4E79"
    assert style["point_color"] == "#C0392B"

    line = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line")
    line.setSelected(True)
    line_combo = window.data_panel.wb_line_color_combo
    assert "暗红" in line_combo.itemText(line_combo.findData("#C0392B"))
    line_combo.setCurrentIndex(line_combo.findData("#C0392B"))
    window.apply_wb_selected_line_style()
    assert line.pen().color().name().upper() == "#C0392B"

    text = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload"))
    text.setSelected(True)
    text_combo = window.data_panel.wb_text_color_combo
    assert "深蓝" in text_combo.itemText(text_combo.findData("#1F4E79"))
    text_combo.setCurrentIndex(text_combo.findData("#1F4E79"))
    window.apply_wb_selected_text_style({"color": window.data_panel.wb_color_combo_value(text_combo)})
    assert text.defaultTextColor().name().upper() == "#1F4E79"
    window.close()
    app.processEvents()


def test_wb_stats_style_controls_update_layout_immediately_without_apply(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [],
        "summary": [],
    }
    panel.data["wb_layout"] = {"enabled": True, "stats_plot": {"enabled": True}}
    window.wb_canvas.set_panel(panel)

    window.data_panel.wb_stats_bar_fill_combo.setCurrentText("#D97757")
    app.processEvents()

    assert panel.data["wb_layout"]["stats_plot"]["style"]["bar_fill_color"] == "#D97757"
    assert window.wb_canvas._stats_group is not None
    assert any(isinstance(item, QGraphicsSvgItem) for item in window.wb_canvas._stats_group.childItems())
    assert b"#d97757" in window.wb_canvas._stats_group._svg_text_source.lower()
    window.close()
    app.processEvents()


def test_wb_stats_plot_width_height_and_axis_line_are_editable(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [],
        "summary": [],
    }
    panel.data["wb_layout"] = {"enabled": True, "stats_plot": {"enabled": True}}
    window.wb_canvas.set_panel(panel)

    window.data_panel.wb_stats_plot_width_spin.setValue(680)
    window.data_panel.wb_stats_plot_height_spin.setValue(420)
    window.data_panel.wb_stats_axis_line_width_spin.setValue(2.2)
    app.processEvents()

    stats_plot = panel.data["wb_layout"]["stats_plot"]
    assert stats_plot["width_px"] == 680
    assert stats_plot["height_px"] == 420
    assert stats_plot["style"]["axis_line_width"] == 2.2
    window.close()
    app.processEvents()


def test_wb_stats_plot_style_controls_update_canvas_bars_and_dots(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [
            {"group": "Control", "value": 1.0},
            {"group": "Control", "value": 1.2},
            {"group": "Drug", "value": 2.0},
            {"group": "Drug", "value": 2.2},
        ],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"] = {"enabled": True, "stats_plot": {"enabled": True}}

    window.data_panel.wb_stats_bar_fill_combo.setCurrentText("#D97757")
    window.data_panel.wb_stats_bar_edge_combo.setCurrentText("#1F4E79")
    window.data_panel.wb_stats_bar_line_width_spin.setValue(2.0)
    window.data_panel.wb_stats_point_shape_combo.setCurrentText("square")
    window.data_panel.wb_stats_point_size_spin.setValue(8.0)
    window.data_panel.wb_stats_point_color_combo.setCurrentText("#C0392B")
    window.apply_wb_stats_plot_style()

    assert window.wb_canvas._stats_group is not None
    assert any(isinstance(item, QGraphicsSvgItem) for item in window.wb_canvas._stats_group.childItems())
    svg = window.wb_canvas._stats_group._svg_text_source.lower()
    assert b"#d97757" in svg
    assert b"#c0392b" in svg
    window.close()
    app.processEvents()


def test_wb_layout_stats_plot_uses_styled_preview_renderer_not_manual_line_plot(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"] = {
        "enabled": True,
        "stats_plot": {
            "enabled": True,
            "style": {
                "bar_fill_color": "#D97757",
                "bar_edge_color": "#1F4E79",
                "point_shape": "square",
                "point_color": "#C0392B",
            },
        },
    }

    window.wb_canvas.set_panel(panel)

    assert window.wb_canvas._stats_group is not None
    assert any(isinstance(item, QGraphicsSvgItem) for item in window.wb_canvas._stats_group.childItems())
    manual_bars = [
        item
        for item in window.wb_canvas._stats_group.childItems()
        if isinstance(item, QGraphicsRectItem) and item.brush().color().isValid() and item.rect().height() > 20
    ]
    assert manual_bars == []
    window.close()
    app.processEvents()


def test_wb_stats_labels_use_group_local_offsets_and_ignore_legacy_manual_positions(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"] = {
        "enabled": True,
        "stats_plot": {"enabled": True, "x": 300.0, "y": 120.0, "width_px": 520, "height_px": 360},
        "manual_positions": {"stats_sig_label": {"0": {"x": 5000.0, "y": 4000.0}}},
    }

    window.wb_canvas.set_panel(panel)

    assert window.wb_canvas._stats_group is not None
    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload() == ("stats_sig_label", 0)
    )
    assert label.parentItem() is None
    assert window.wb_canvas._attached_item_groups[label] is window.wb_canvas._stats_group
    anchor = window.wb_canvas.stats_label_anchor("stats_sig_label", 0)
    assert anchor is not None
    relative = window.wb_canvas.relative_position_for_item(label)
    assert abs(relative.x() - anchor.x()) < 100.0
    assert abs(relative.y() - anchor.y()) < 30.0

    label.setPos(window.wb_canvas._stats_group.pos() + QPointF(anchor.x() + 14.0, anchor.y() + 9.0))

    saved = panel.data["wb_layout"]["stats_plot"]["label_offsets"]["stats_sig_label"]["0"]
    assert saved == {"dx": 14.0, "dy": 9.0}
    window.close()
    app.processEvents()


def test_wb_layout_text_and_bracket_hit_areas_support_direct_mouse_selection(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "annotation_hit_areas.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)

    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload()[0] == "group_l1"
    )
    bracket = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line")

    assert label.shape().boundingRect().width() >= label.boundingRect().width() + 8.0
    assert label.shape().boundingRect().height() >= label.boundingRect().height() + 6.0
    near_line = bracket.sceneBoundingRect().center() + QPointF(0.0, 4.0)
    assert bracket.contains(bracket.mapFromScene(near_line))
    assert window.wb_canvas.scene.itemAt(near_line, QTransform()) is bracket
    bracket.setSelected(True)
    window.sync_wb_text_toolbar_from_selection()
    window.data_panel.wb_line_width_spin.setValue(3.25)
    window.data_panel.wb_line_x_spin.setValue(bracket.pos().x() + 6.0)
    app.processEvents()
    assert bracket.pen().widthF() == 3.25
    assert bracket.pos().x() == window.data_panel.wb_line_x_spin.value()
    window.close()
    app.processEvents()


def test_wb_stats_text_can_be_selected_and_moved_with_layout_controls(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"] = {
        "enabled": True,
        "stats_plot": {"enabled": True, "x": 300.0, "y": 120.0, "width_px": 520, "height_px": 360},
    }
    window.wb_canvas.set_panel(panel)
    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload() == ("stats_sig_label", 0)
    )
    label.setSelected(True)
    window.sync_wb_text_toolbar_from_selection()

    window.data_panel.wb_text_x_spin.setValue(label.pos().x() + 18.0)
    window.data_panel.wb_text_y_spin.setValue(label.pos().y() + 12.0)
    app.processEvents()

    assert label.pos().x() == window.data_panel.wb_text_x_spin.value()
    assert label.pos().y() == window.data_panel.wb_text_y_spin.value()
    saved = panel.data["wb_layout"]["stats_plot"]["label_offsets"]["stats_sig_label"]["0"]
    assert saved["dx"] != 0.0
    assert saved["dy"] != 0.0
    window.close()
    app.processEvents()


def test_wb_canvas_exposes_alignment_and_history_tools_only_in_layout_step() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")

    button_names = {
        window.wb_canvas.undo_button.objectName(),
        window.wb_canvas.redo_button.objectName(),
        window.wb_canvas.align_row_button.objectName(),
        window.wb_canvas.align_column_button.objectName(),
        window.wb_canvas.sync_lines_button.objectName(),
        window.wb_canvas.recenter_labels_button.objectName(),
    }

    assert button_names == {
        "WBCanvasUndoButton",
        "WBCanvasRedoButton",
        "WBCanvasAlignRowButton",
        "WBCanvasAlignColumnButton",
        "WBCanvasSyncLinesButton",
        "WBCanvasRecenterLabelsButton",
    }
    assert not window.wb_canvas_toolbar_panel.isVisibleTo(window.wb_workflow_panel)
    assert not window.wb_canvas_toolbar_panel.isVisibleTo(window.wb_canvas)

    window.data_panel.set_wb_step(WB_STEP_LAYOUT)
    app.processEvents()

    assert window.wb_canvas_toolbar_panel.isVisibleTo(window.wb_workflow_panel)
    assert window.wb_canvas_tools_group.isVisibleTo(window.wb_workflow_panel)
    window.close()
    app.processEvents()


def test_wb_align_selected_row_uses_reference_text_and_saves_positions(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "align_row.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "type": "condition_row", "row_label": "Condition", "values": ["-", "+", "-", "+"]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    values = [
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload()[0] == "condition"
    ]
    assert len(values) >= 2
    reference = values[0]
    target = values[1]
    target.set_position_without_callback(target.pos().x(), target.pos().y() + 7.0)
    reference.setSelected(True)

    window.align_wb_selected_row()

    assert abs(target.sceneBoundingRect().center().y() - reference.sceneBoundingRect().center().y()) < 0.01
    saved = panel.data["wb_layout"]["manual_positions"]["condition"][str(target.style_payload()[1])]
    assert saved["y"] == window.wb_canvas.relative_position_for_item(target).y()
    window.close()
    app.processEvents()


def test_wb_sync_selected_line_level_applies_style_position_and_recenters_labels(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "sync_lines.png", width=320, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4", "5", "6"], [], [])
    panel.set_crop_roi(10, 45, 260, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4", "5", "6"],
        lane_hierarchy={
            "total_lanes": 6,
            "levels": [
                {
                    "level": 1,
                    "groups": [
                        {"label": "Group 1A", "lane_start": 1, "lane_end": 2, "show_bracket": True},
                        {"label": "Group 1B", "lane_start": 4, "lane_end": 6, "show_bracket": True},
                    ],
                },
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    lines = sorted(
        [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line"],
        key=lambda item: str(item.data(1)),
    )
    assert len(lines) == 2
    reference = lines[0]
    peer = lines[1]
    reference.setSelected(True)
    reference.setPen(reference.pen().__class__(QColor("#C0392B"), 3.5))
    reference.setPos(reference.pos().x(), reference.pos().y() + 11.0)

    window.sync_wb_selected_line_level()

    assert peer.pen().widthF() == 3.5
    assert peer.pen().color().name().lower() == "#c0392b"
    assert peer.pos().y() == reference.pos().y()
    peer_uid = str(peer.data(1))
    assert panel.data["wb_layout"]["line_styles"][peer_uid]["width"] == 3.5
    peer_kind, peer_index = peer_uid.split(":", 1)
    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload() == (peer_kind, int(peer_index))
    )
    assert abs(label.sceneBoundingRect().center().x() - peer.sceneBoundingRect().center().x()) < 0.01
    window.close()
    app.processEvents()


def test_wb_selected_annotations_can_move_together_and_persist(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "multi_move.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.style_payload()[0] == "group_l1"
    )
    line = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line")
    label.setSelected(True)
    line.setSelected(True)
    label_before = QPointF(label.pos())
    line_before = QPointF(line.pos())

    window.wb_canvas.move_selected_layout_items_by(QPointF(9.0, 6.0))

    assert label.pos() == label_before + QPointF(9.0, 6.0)
    assert line.pos() == line_before + QPointF(9.0, 6.0)
    kind, index = label.style_payload()
    assert panel.data["wb_layout"]["manual_positions"][kind][str(index)] == {
        "x": window.wb_canvas.relative_position_for_item(label).x(),
        "y": window.wb_canvas.relative_position_for_item(label).y(),
    }
    uid = str(line.data(1))
    assert panel.data["wb_layout"]["line_styles"][uid]["pos"] == {
        "x": window.wb_canvas.relative_position_for_item(line).x(),
        "y": window.wb_canvas.relative_position_for_item(line).y(),
    }
    window.close()
    app.processEvents()


def test_wb_layout_svg_export_renders_item_bounds_without_scene_export_fallback(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "tight_svg_export.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    stray = QGraphicsRectItem(0, 0, 50, 50)
    stray.setPos(9000, 9000)
    window.wb_canvas.scene.addItem(stray)

    def fail_scene_export(*_args, **_kwargs):
        raise AssertionError("WB layout export must not use whole-scene export fallback")

    monkeypatch.setattr("ui.main_window.export_wb_figure_scene", fail_scene_export)
    target = tmp_path / "layout.svg"

    exported = window._export_wb_scene_item_svg(window.wb_canvas._group, target)

    assert exported == target
    svg_text = target.read_text(encoding="utf-8")
    assert "9000" not in svg_text
    assert "10000" not in svg_text
    window.close()
    app.processEvents()


def test_wb_dead_mixed_stats_rendering_paths_are_removed() -> None:
    assert not hasattr(WBCanvas, "_normalized_saved_position")
    assert not hasattr(WBCanvas, "_statistics_plot_image")
    assert not hasattr(MainWindow, "_wb_stats_plot_qimage")
    assert not hasattr(MainWindow, "_wb_layout_qimage")


def test_wb_layout_and_stats_plot_move_independently(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "move_independent.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["Control", "Drug"], [], [])
    panel.set_crop_roi(10, 20, 160, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["Control", "Drug"],
    )
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [],
        "summary": [],
    }
    panel.data.setdefault("wb_layout", {})["stats_plot"] = {"enabled": True}
    window.wb_canvas.set_panel(panel)

    assert window.wb_canvas._group is not None
    assert window.wb_canvas._stats_group is not None
    stats_before = window.wb_canvas._stats_group.pos()
    window.wb_canvas._group.setPos(40, 25)
    assert window.wb_canvas._stats_group.pos() == stats_before
    wb_before = window.wb_canvas._group.pos()
    window.wb_canvas._stats_group.setPos(220, 120)
    assert window.wb_canvas._group.pos() == wb_before
    window.close()
    app.processEvents()


def test_wb_layout_group_move_carries_labels_and_brackets_but_preserves_local_text_adjustment(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "group_move_children.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
                {"level": 2, "type": "condition_row", "row_label": "Condition", "values": ["-", "+", "-", "+"]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)

    assert window.wb_canvas._group is not None
    protein_label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem)
        and hasattr(item, "style_payload")
        and item.style_payload()[0] == "protein"
    )
    bracket = next(item for item in window.wb_canvas.scene.items() if item.data(0) == "wb_layout_line")
    assert protein_label.parentItem() is None
    assert bracket.parentItem() is None
    label_scene_before = protein_label.scenePos()
    bracket_scene_before = bracket.scenePos()

    window.wb_canvas._group.setPos(30, 20)

    assert protein_label.scenePos() == label_scene_before + QPointF(30, 20)
    assert bracket.scenePos() == bracket_scene_before + QPointF(30, 20)
    protein_label.setPos(protein_label.pos() + QPointF(7, 5))
    saved = panel.data["wb_layout"]["manual_positions"]["protein"]["0"]
    relative = window.wb_canvas.relative_position_for_item(protein_label)
    assert saved["x"] == relative.x()
    assert saved["y"] == relative.y()
    adjusted_scene = protein_label.scenePos()
    window.wb_canvas._group.setPos(42, 26)
    assert protein_label.scenePos() == adjusted_scene + QPointF(12, 6)
    window.close()
    app.processEvents()


def test_wb_layout_selected_text_position_controls_move_text_immediately(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "text_position_controls.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("Ctrl,KD")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()
    label = next(
        item
        for item in window.wb_canvas.scene.items()
        if isinstance(item, QGraphicsTextItem) and hasattr(item, "style_payload") and item.toPlainText() == "Ctrl"
    )
    label.setSelected(True)
    window.sync_wb_text_toolbar_from_selection()

    window.data_panel.wb_text_x_spin.setValue(label.pos().x() + 12.0)
    window.data_panel.wb_text_y_spin.setValue(label.pos().y() + 8.0)
    app.processEvents()

    saved = window._active_wb_panel().data["wb_layout"]["manual_positions"]["lane"]["0"]
    relative = window.wb_canvas.relative_position_for_item(label)
    assert saved["x"] == relative.x()
    assert saved["y"] == relative.y()
    window.close()
    app.processEvents()


def test_wb_graphics_views_use_full_viewport_updates_to_avoid_drag_trails(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    assert window.wb_canvas.view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    assert window.composer_canvas.view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    assert window.fluoro_workspace.canvas.view.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    window.close()
    app.processEvents()


def test_wb_group_and_condition_buttons_do_not_create_fixed_height_overlay(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    before_min = window.data_panel.minimumHeight()
    before_max = window.data_panel.maximumHeight()

    window.data_panel.show_wb_group_level_editor()
    window.data_panel.show_wb_condition_row_editor()

    assert window.data_panel.minimumHeight() == before_min
    assert window.data_panel.maximumHeight() == before_max
    window.close()
    app.processEvents()


def test_wb_layout_condition_and_group_lines_are_selectable_and_styleable(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "condition_lines.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
                {"level": 2, "type": "condition_row", "row_label": "Condition", "values": ["-", "+", "-", "+"]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    lines = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line"]
    assert lines
    for line in lines:
        assert line.flags() & QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
    lines[0].setSelected(True)
    window.data_panel.wb_line_width_spin.setValue(2.5)
    window.data_panel.wb_line_color_combo.setCurrentText("#C0392B")
    window.apply_wb_selected_line_style()
    assert lines[0].pen().widthF() == 2.5
    assert lines[0].pen().color().name().lower() == "#c0392b"
    window.close()
    app.processEvents()


def test_wb_layout_bracket_line_styles_and_position_apply_to_whole_bracket(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "whole_bracket_style.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    brackets = [item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line"]
    assert len(brackets) == 1
    bracket = brackets[0]
    bracket.setSelected(True)
    window.data_panel.wb_line_width_spin.setValue(2.75)
    window.data_panel.wb_line_color_combo.setCurrentText("#C0392B")
    window.apply_wb_selected_line_style()
    assert bracket.pen().widthF() == 2.75
    assert bracket.pen().color().name().lower() == "#c0392b"
    bracket.setPos(9, 11)
    uid = str(bracket.data(1))
    saved = panel.data["wb_layout"]["line_styles"][uid]["pos"]
    assert saved == {"x": 9.0, "y": 11.0}
    window.wb_canvas.set_panel(panel)
    bracket_after = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(1) == uid)
    assert bracket_after.pos().x() == 9.0
    assert bracket_after.pos().y() == 11.0
    assert bracket_after.pen().widthF() == 2.75
    window.close()
    app.processEvents()


def test_wb_layout_lines_restore_saved_group_local_positions_directly(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "local_line_positions.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={"total_lanes": 4, "levels": [{"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]}]},
    )
    panel.set_layout_position(280.0, 90.0)
    panel.data["wb_layout"]["line_styles"] = {"group_l1:1000": {"pos": {"x": 31.0, "y": 12.0}}}

    window.wb_canvas.set_panel(panel)

    bracket = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(1) == "group_l1:1000")
    relative = window.wb_canvas.relative_position_for_item(bracket)
    assert relative.x() == 31.0
    assert relative.y() == 12.0
    window.close()
    app.processEvents()


def test_wb_layout_selected_line_position_controls_move_line_immediately(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "line_position_controls.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    bracket = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line")
    bracket.setSelected(True)
    window.sync_wb_text_toolbar_from_selection()

    window.data_panel.wb_line_x_spin.setValue(13.0)
    window.data_panel.wb_line_y_spin.setValue(17.0)
    app.processEvents()

    assert bracket.pos().x() == 13.0
    assert bracket.pos().y() == 17.0
    uid = str(bracket.data(1))
    relative = window.wb_canvas.relative_position_for_item(bracket)
    assert panel.data["wb_layout"]["line_styles"][uid]["pos"] == {"x": relative.x(), "y": relative.y()}
    window.close()
    app.processEvents()


def test_wb_export_items_send_selected_outputs_as_separate_high_res_composer_assets(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    blot_path = _write_test_blot(tmp_path / "export_items.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("Control,Drug")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 2.0}],
        "comparisons": [],
        "summary": [],
    }
    panel.data.setdefault("wb_layout", {})["stats_plot"] = {"enabled": True}
    window.wb_canvas.set_panel(panel)
    assert window.wb_canvas._stats_group is not None
    assert window.wb_canvas._stats_group.apply_svg_text_override(0, "Edited tick")
    assert panel.data["wb_layout"]["stats_plot"]["svg_text_overrides"] == {"0": "Edited tick"}
    window.data_panel.set_wb_step(WB_STEP_EXPORT)
    window.data_panel.populate_wb_export_items(panel.data)
    window.data_panel.set_wb_export_item_checked("wb_layout", True)
    window.data_panel.set_wb_export_item_checked("wb_stats_plot", True)

    window.export_selected_wb_items_to_composer()

    assets = [
        item
        for item in window.project.panels
        if isinstance(item, ExternalImagePanel) and item.data.get("source_module") == "wb_export_item"
    ]
    assert {item.data.get("export_item_key") for item in assets} == {"wb_layout", "wb_stats_plot"}
    for asset in assets:
        assert Path(asset.data["image_path"]).exists()
        assert asset.data["source_dpi"] >= 1200
        assert asset.data["preferred_width_mm"] > 50
        assert asset.data["preferred_height_mm"] > 10
    layout_asset = next(item for item in assets if item.data.get("export_item_key") == "wb_layout")
    stats_asset = next(item for item in assets if item.data.get("export_item_key") == "wb_stats_plot")
    assert layout_asset.data["source_format"] == "SVG"
    assert layout_asset.data["source_width_px"] >= 2700
    assert stats_asset.data["source_format"] == "SVG"
    assert Path(stats_asset.data["image_path"]).suffix.lower() == ".svg"
    assert b"<image" not in Path(stats_asset.data["image_path"]).read_bytes()
    window.close()
    app.processEvents()


def test_wb_layout_export_item_includes_group_and_condition_annotations(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    blot_path = _write_test_blot(tmp_path / "hierarchy_export.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1A", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
                {"level": 2, "type": "condition_row", "row_label": "Condition 1", "values": ["-", "+", "-", "+"]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    window.data_panel.set_wb_step(WB_STEP_EXPORT)
    window.data_panel.populate_wb_export_items(panel.data)
    window.data_panel.set_wb_export_item_checked("wb_layout", True)

    window.export_selected_wb_items_to_composer()

    layout_asset = next(
        item
        for item in window.project.panels
        if isinstance(item, ExternalImagePanel) and item.data.get("export_item_key") == "wb_layout"
    )
    assert layout_asset.data["source_dpi"] >= 1200
    assert layout_asset.data["contains_wb_hierarchy"] is True
    assert layout_asset.data["source_format"] == "SVG"
    assert layout_asset.data["source_width_px"] >= 3400
    window.close()
    app.processEvents()


def test_wb_layout_export_to_composer_uses_current_canvas_styles(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    blot_path = _write_test_blot(tmp_path / "styled_canvas_export.png", width=260, height=120)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    panel = window._active_wb_panel()
    panel.import_full_blot(blot_path)
    panel.set_annotations(["1", "2", "3", "4"], [], [])
    panel.set_crop_roi(10, 45, 200, 35, protein="SPNS1")
    panel.set_band_configuration(
        [{"crop_id": panel.data["crops"][0]["crop_id"], "protein": "SPNS1", "mw_kda": ""}],
        lane_labels=["1", "2", "3", "4"],
        lane_hierarchy={
            "total_lanes": 4,
            "levels": [
                {"level": 1, "groups": [{"label": "Group 1", "lane_start": 1, "lane_end": 2, "show_bracket": True}]},
            ],
        },
    )
    window.wb_canvas.set_panel(panel)
    bracket = next(item for item in window.wb_canvas.scene.items() if isinstance(item, QGraphicsPathItem) and item.data(0) == "wb_layout_line")
    bracket.setSelected(True)
    window.data_panel.wb_line_width_spin.setValue(5.0)
    window.data_panel.wb_line_color_combo.setCurrentText("#C0392B")
    window.apply_wb_selected_line_style()
    window.data_panel.set_wb_step(WB_STEP_EXPORT)
    window.data_panel.populate_wb_export_items(panel.data)
    window.data_panel.set_wb_export_item_checked("wb_layout", True)

    window.export_selected_wb_items_to_composer()

    layout_asset = next(
        item
        for item in window.project.panels
        if isinstance(item, ExternalImagePanel) and item.data.get("export_item_key") == "wb_layout"
    )
    assert layout_asset.data["source_dpi"] == 1200
    assert layout_asset.data["source_format"] == "SVG"
    assert layout_asset.data["source_width_px"] >= 3400
    image = layout_asset.render_preview(layout_asset.data["preferred_width_mm"], layout_asset.data["preferred_height_mm"], dpi=300)
    red_pixels = 0
    for y in range(0, image.height(), max(1, image.height() // 240)):
        for x in range(0, image.width(), max(1, image.width() // 240)):
            color = image.pixelColor(x, y)
            if color.red() > 170 and color.green() < 90 and color.blue() < 90:
                red_pixels += 1
    assert red_pixels > 0
    window.close()
    app.processEvents()


def test_wb_composer_uses_full_1200dpi_source_pixmap_without_2400dpi_preview(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    from ui.widgets.composer_canvas import COMPOSER_MAX_PREVIEW_DPI

    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    source = QImage(3600, 900, QImage.Format.Format_RGB32)
    source.fill(QColor("#f2f2f2"))
    source_path = tmp_path / "full_1200_source.png"
    assert source.save(str(source_path))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    asset = ExternalImagePanel(panel_id="full_1200")
    asset.import_image(source_path)
    asset.data["source_dpi"] = 1200
    asset.data["source_module"] = "wb_export_item"
    window.project.add_panel(asset)
    window.set_module("composer")

    pixmap = window.composer_canvas._panel_pixmap(asset, 300, 90)

    assert COMPOSER_MAX_PREVIEW_DPI == 1200
    assert pixmap is not None
    assert pixmap.width() == 3600
    assert pixmap.height() == 900
    window.close()
    app.processEvents()


def test_wb_composer_placement_keeps_high_resolution_pixmap(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    source = QImage(3600, 900, QImage.Format.Format_RGB32)
    source.fill(QColor("#f2f2f2"))
    painter = QPainter(source)
    painter.setPen(QColor("#111111"))
    for index in range(12):
        painter.drawRect(160 + index * 260, 360, 180, 70)
    painter.end()
    source_path = tmp_path / "high_res_wb_layout.png"
    assert source.save(str(source_path))
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    asset = ExternalImagePanel(panel_id="high_res_wb")
    asset.import_image(source_path)
    asset.data["preferred_width_mm"] = 76.2
    asset.data["preferred_height_mm"] = 19.05
    asset.data["source_dpi"] = 1200
    window.project.add_panel(asset)
    window.set_module("composer")

    window.composer_canvas.place_panel(asset.panel_uuid)

    panel_item = next(item for item in window.composer_canvas.scene.items() if item.data(0) == "panel_ref")
    pixmap_items = [item for item in panel_item.childItems() if isinstance(item, QGraphicsPixmapItem)]
    assert pixmap_items
    scene_content_width = int(panel_item.rect().width() - 8)
    assert pixmap_items[0].pixmap().width() > scene_content_width * 4
    assert pixmap_items[0].scale() < 1.0
    window.close()
    app.processEvents()


def test_wb_apply_layout_preserves_existing_statistics_plot_and_styles(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "apply_preserve_stats.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(2)
    window.data_panel.wb_lane_edit.setText("Control,Drug")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")
    window.apply_wb_nature_layout()
    panel = window._active_wb_panel()
    panel.data["wb_statistics"] = {
        "plot_data": [{"group": "Control", "value": 1.0}, {"group": "Drug", "value": 1.7}],
        "comparisons": [{"group1": "Control", "group2": "Drug", "label": "ns"}],
        "summary": [],
    }
    panel.data["wb_layout"]["stats_plot"] = {"enabled": True, "x": 330.0, "y": 44.0, "style": {"bar_fill_color": "#D97757"}}
    panel.data["wb_layout"]["text_styles"] = {"protein": {"default": {"font_size_pt": 11.0}}}
    panel.data["wb_layout"]["line_styles"] = {"condition-0-0-1": {"color": "#C0392B", "width": 2.0}}

    window.apply_wb_nature_layout()

    layout = panel.data["wb_layout"]
    assert layout["stats_plot"]["enabled"] is True
    assert layout["stats_plot"]["style"]["bar_fill_color"] == "#D97757"
    assert layout["text_styles"]["protein"]["default"]["font_size_pt"] == 11.0
    assert layout["line_styles"]["condition-0-0-1"]["width"] == 2.0
    assert [item for item in window.wb_canvas.scene.items() if item.data(0) == "wb_stats_plot"]
    window.close()
    app.processEvents()


def test_wb_hierarchy_controls_and_apply_layout_do_not_create_top_level_prompt_windows(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    blot_path = _write_test_blot(tmp_path / "no_prompt_windows.png", width=220, height=90)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_image(str(blot_path))
    window.set_wb_roi_from_canvas(QRectF(10, 20, 160, 35))
    window.data_panel.wb_lane_count_spin.setValue(4)
    window.data_panel.wb_lane_edit.setText("1,2,3,4")
    window.data_panel.populate_wb_bands(window._active_wb_panel().data["crops"], {})
    window.data_panel.wb_band_table.item(0, 1).setText("SPNS1")

    window.data_panel.show_wb_group_level_editor()
    window.data_panel.show_wb_condition_row_editor()
    window.apply_wb_nature_layout()
    app.processEvents()

    stray_texts = {
        widget.text()
        for widget in app.topLevelWidgets()
        if isinstance(widget, QLabel) and widget.isVisible() and widget.window() is widget
    }
    assert "Total lanes" not in stray_texts
    assert "Gap after lane" not in stray_texts
    window.close()
    app.processEvents()


def test_wb_layout_step_expands_in_main_right_scroll_without_inner_scroll_box() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    layout_page = window.data_panel.wb_context_stack.widget(WB_STEP_LAYOUT)

    assert not isinstance(layout_page, QScrollArea)
    assert window.wb_workflow_panel.findChild(QScrollArea, "WBWorkflowStepScroll") is None
    window.close()
    app.processEvents()


def test_wb_density_overlay_is_visible_only_on_density_step(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    target = _write_wb_lane_strip(tmp_path / "SPNS1.png", [120, 220, 180])
    loading = _write_wb_lane_strip(tmp_path / "GAPDH.png", [160, 160, 160])
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("wb")
    window.import_wb_images([str(target), str(loading)])
    panel = window._active_wb_panel()
    panel.data["placed_strip_ids"] = ["strip_1", "strip_2"]
    panel.set_annotations(["Control", "KO", "Rescue"], [], [], lane_anchors=[20.0, 60.0, 100.0])
    panel.update_band_strip_protein(0, "SPNS1")
    panel.update_band_strip_protein(1, "GAPDH")
    window.data_panel.set_wb_step(WB_STEP_DENSITY)
    window.data_panel.wb_density_loading_ctrl_combo.setCurrentIndex(
        window.data_panel.wb_density_loading_ctrl_combo.findData("strip_2")
    )
    window.data_panel.wb_density_bg_method_combo.setCurrentText("None")
    window.data_panel.wb_density_polarity_combo.setCurrentText("Dark bands on light bg")
    window.data_panel.wb_density_measure_button.click()

    assert len(window.wb_canvas._density_overlay_items) >= 6
    assert not [
        item
        for item in window.wb_canvas._density_overlay_items
        if isinstance(item, QGraphicsTextItem) and item.toPlainText().strip().startswith("!")
    ]

    window.data_panel.set_wb_step(WB_STEP_LAYOUT)
    assert window.wb_canvas._density_overlay_items == []
    window.close()
    app.processEvents()

