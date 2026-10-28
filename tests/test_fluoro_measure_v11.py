from __future__ import annotations

from pathlib import Path
import time

import numpy as np
import pytest
import tifffile
from PyQt6.QtCore import QEvent, QPointF, QRectF, Qt
from PyQt6.QtGui import QImage, QKeyEvent, QPainterPath, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsPixmapItem, QGraphicsScene, QPushButton

from core.export import export_fluoro_figure_scene
from core.models import FluorescencePanel
from core.utils.i18n import tr
from core.utils.svg_text import svg_text_entries
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
from ui.widgets import fluoro_canvas
from ui.widgets.editable_text_item import EditableMovableTextItem
from ui.main_window import MainWindow


def _write_measure_tiff(path: Path) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = 100
    data[1, 2:6, 3:8] = 250
    data[0, 8:10, 10:12] = 10
    data[1, 8:10, 10:12] = 40
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _write_measure_tiff_with_values(path: Path, ch0_roi: int, ch0_second_roi: int, ch1_roi: int = 250) -> Path:
    data = np.zeros((2, 12, 14), dtype=np.uint16)
    data[0, 2:6, 3:8] = ch0_roi
    data[1, 2:6, 3:8] = ch1_roi
    data[0, 8:10, 10:12] = ch0_second_roi
    data[1, 8:10, 10:12] = 40
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _write_background_tiff(path: Path) -> Path:
    data = np.full((1, 16, 18), 40, dtype=np.uint16)
    data[0, 2:5, 3:6] = 140
    data[0, 10:13, 12:15] = 180
    tifffile.imwrite(path, data, metadata={"axes": "CYX"})
    return path


def _select_fluoro_image(workspace: FluorescenceWorkspace, image_uid: str) -> None:
    index = workspace.active_image_combo.findData(image_uid)
    assert index >= 0
    workspace.active_image_combo.setCurrentIndex(index)


def _fluoro_scale_bar_items(workspace: FluorescenceWorkspace, role: str | None = None) -> list:
    items = []
    for scene_item in workspace.canvas.scene.items():
        for item in [scene_item, *scene_item.childItems()]:
            if str(item.data(0) or "") != "fluoro_scale_bar":
                continue
            if role is not None and item.data(3) != role:
                continue
            items.append(item)
    return items


def _close_window(window: MainWindow, app: QApplication) -> None:
    window.close()
    window.deleteLater()
    app.processEvents()


def _wait_for_fluoro_analysis(app: QApplication, window: MainWindow, operation: str, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    key = f"fluoro-analysis:{operation}"
    while window._analysis_job_service.is_busy(key):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {key}")
        time.sleep(0.005)
    app.processEvents()


def _wait_for_fluoro_import(app: QApplication, window: MainWindow, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while window._analysis_job_service.is_busy("import:fluoro"):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for fluorescence import")
        time.sleep(0.005)
    app.processEvents()


def test_image_coordinate_mapper_converts_scene_coordinates_after_item_scaling() -> None:
    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    item = scene.addPixmap(QPixmap.fromImage(image))
    item.setPos(10, 20)
    item.setScale(2.0)
    mapper = fluoro_canvas.ImageCoordinateMapper(item, image_shape=(100, 200))

    assert mapper.scene_pos_to_image_px(QPointF(210, 120)) == (100.0, 50.0)
    assert mapper.image_px_to_scene_pos((100, 50)) == QPointF(210, 120)

    path = QPainterPath()
    path.addRect(QRectF(10, 20, 200, 100))
    mask = mapper.roi_path_scene_to_image_mask(path, (100, 200))

    assert mask.shape == (100, 200)
    assert int(mask.sum()) == 5000
    app.processEvents()


def test_roi_intensity_measurement_object_records_per_channel_background(tmp_path) -> None:
    image_path = _write_measure_tiff(tmp_path / "measure_cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = 0.5

    measurement = panel.create_roi_intensity_measurement(
        str(image["uid"]),
        roi_type="rectangle",
        geometry={"x": 3, "y": 2, "w": 5, "h": 4},
        channel_indices=[0, 1],
        background_roi={"type": "rectangle", "geometry": {"x": 10, "y": 8, "w": 2, "h": 2}},
    )

    assert measurement["type"] == "roi_intensity"
    assert measurement["image_uuid"] == image["uid"]
    assert measurement["roi_or_geometry"]["type"] == "rectangle"
    assert measurement["params"]["background_correction"] == "local_background_roi"
    assert measurement["params"]["background_mean_by_channel"] == {0: 10.0, 1: 40.0}
    assert measurement["results_summary"]["n_rows"] == 2
    assert measurement["results_summary"]["primary_result"] == "1 ROI measured"

    rows = measurement["results_table"]
    assert [row["channel_index"] for row in rows] == [0, 1]
    assert rows[0]["area_px"] == 20
    assert rows[0]["area_um2"] == 5.0
    assert rows[0]["mean"] == 100.0
    assert rows[0]["integrated_density"] == 2000.0
    assert rows[0]["background_mean"] == 10.0
    assert rows[0]["corrected_integrated_density"] == 1800.0
    assert panel.analysis["measurements"][0]["measurement_uuid"] == measurement["measurement_uuid"]


def test_fluoro_result_management_deletes_and_clears_roi_measurements(tmp_path) -> None:
    image_path = _write_measure_tiff(tmp_path / "delete_roi_rows.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])
    first = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
    second = panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 10, "y": 8, "w": 2, "h": 2}, [1])
    panel.analysis["fluoro_statistics"] = {"source": "roi_intensity", "metric": "mean"}

    assert panel.delete_measurement(str(first["measurement_uuid"])) is True

    remaining_analysis = [row["measurement_uuid"] for row in panel.analysis["measurements"]]
    remaining_image = [row["measurement_uuid"] for row in panel.active_image()["measurements"]]
    assert remaining_analysis == [second["measurement_uuid"]]
    assert remaining_image == [second["measurement_uuid"]]
    assert "fluoro_statistics" not in panel.analysis
    assert panel.delete_measurement(str(first["measurement_uuid"])) is False

    removed = panel.clear_measurements(uid, measurement_type="roi_intensity")

    assert removed == 1
    assert panel.analysis["measurements"] == []
    assert panel.active_image()["measurements"] == []


def test_fluoro_result_management_deletes_and_clears_object_counts(tmp_path) -> None:
    image_path = _write_measure_tiff(tmp_path / "delete_count_rows.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])
    first = panel.count_channel_objects(uid, [0], method="manual", manual_value=5, min_area_px=1)[0]
    second = panel.count_channel_objects(uid, [1], method="manual", manual_value=5, min_area_px=1)[0]
    panel.analysis["fluoro_statistics"] = {"source": "object_count", "metric": "count"}

    assert panel.delete_object_count(str(first["count_uuid"])) is True

    remaining_analysis = [row["count_uuid"] for row in panel.analysis["object_counts"]]
    remaining_image = [row["count_uuid"] for row in panel.active_image()["object_counts"]]
    assert remaining_analysis == [second["count_uuid"]]
    assert remaining_image == [second["count_uuid"]]
    assert "fluoro_statistics" not in panel.analysis
    assert panel.delete_object_count(str(first["count_uuid"])) is False

    removed = panel.clear_object_counts(uid)

    assert removed == 1
    assert panel.analysis["object_counts"] == []
    assert panel.active_image()["object_counts"] == []


def test_fluoro_result_management_clears_profile_and_overlap(tmp_path) -> None:
    image_path = _write_measure_tiff(tmp_path / "clear_profile_overlap.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])
    panel.line_profile(uid, [0, 1], {"x1": 0, "y1": 2, "x2": 13, "y2": 2}, samples=8)
    panel.compute_channel_overlap(uid, [0, 1], method="manual", manual_value=5)
    panel.analysis["fluoro_statistics"] = {"source": "roi_intensity", "metric": "mean"}

    panel.clear_line_profile(uid)

    assert "line_profile" not in panel.analysis
    assert "line_profile" not in panel.active_image()
    assert "fluoro_statistics" not in panel.analysis

    panel.analysis["fluoro_statistics"] = {"source": "object_count", "metric": "count"}
    panel.clear_channel_overlap(uid)

    assert "channel_overlap" not in panel.analysis
    assert "channel_overlap" not in panel.active_image()
    assert "fluoro_statistics" not in panel.analysis


def test_fluoro_statistics_collects_values_and_persists_result(tmp_path) -> None:
    from core.fluoro_statistics import collect_fluoro_stat_values, compute_fluoro_statistics

    image_path = _write_measure_tiff(tmp_path / "fluoro_stats_values.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
    panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [1])
    panel.count_channel_objects(uid, [0], method="manual", manual_value=5, min_area_px=1)

    roi_rows = collect_fluoro_stat_values(panel, source="roi_intensity", metric="mean")
    count_rows = collect_fluoro_stat_values(panel, source="object_count", metric="count")

    assert [row["value"] for row in roi_rows] == [100.0, 250.0]
    assert [row["source"] for row in roi_rows] == ["roi_intensity", "roi_intensity"]
    assert roi_rows[0]["channel_name"] == "DAPI"
    assert roi_rows[0]["n_measurements"] == 1
    assert count_rows[0]["value"] == 2.0
    assert count_rows[0]["channel_name"] == "DAPI"

    result = compute_fluoro_statistics(
        panel,
        source="roi_intensity",
        metric="mean",
        group_assignments={
            roi_rows[0]["lane_label"]: "Control",
            roi_rows[1]["lane_label"]: "Treatment",
        },
        reference_group="Control",
        test_name="Welch t-test",
    )

    assert panel.analysis["fluoro_statistics"] is result
    assert result["source"] == "roi_intensity"
    assert result["metric"] == "mean"
    assert [group["group"] for group in result["groups"]] == ["Control", "Treatment"]


def test_fluoro_statistics_aggregates_roi_repeats_per_image_sample(tmp_path) -> None:
    from core.fluoro_statistics import collect_fluoro_stat_values, compute_fluoro_statistics

    panel = FluorescencePanel(panel_id="Figure1C")
    sample_specs = [
        ("control_a.tif", 100, 120, "Control"),
        ("control_b.tif", 90, 110, "Control"),
        ("treated_a.tif", 180, 200, "Treatment"),
    ]
    expected_values = []
    expected_groups = {}
    for filename, first_value, second_value, group in sample_specs:
        image = panel.import_image(_write_measure_tiff_with_values(tmp_path / filename, first_value, second_value))
        uid = str(image["uid"])
        panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
        panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 10, "y": 8, "w": 2, "h": 2}, [0])
        expected_values.append((first_value + second_value) / 2.0)

    rows = collect_fluoro_stat_values(panel, source="roi_intensity", metric="mean")

    assert len(rows) == 3
    assert [row["value"] for row in rows] == expected_values
    assert [row["n_measurements"] for row in rows] == [2, 2, 2]
    assert all(row["aggregation"] == "mean_per_image" for row in rows)
    assert all(str(row["sample_key"]).startswith(str(row["image_uid"])) for row in rows)
    for row, (_, _, _, group) in zip(rows, sample_specs):
        expected_groups[row["lane_label"]] = group

    result = compute_fluoro_statistics(panel, "roi_intensity", "mean", expected_groups, "Control", "Welch t-test")

    assert len(result["plot_data"]) == 3
    assert {row["group"]: row["n"] for row in result["groups"]} == {"Control": 2, "Treatment": 1}


def test_fluorescence_measure_step_uses_five_exclusive_modes() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    workspace.set_step(2)

    assert set(workspace.measure_mode_buttons) == {"roi_intensity", "spot_count", "line_profile", "colocalization", "ai_assisted"}
    assert workspace.measure_params_stack.count() == 5
    assert workspace.measure_summary_stack.count() == 5
    assert workspace.measure_mode == "roi_intensity"
    assert workspace.measure_mode_buttons["roi_intensity"].isChecked()

    workspace.measure_mode_buttons["line_profile"].click()
    assert workspace.measure_mode == "line_profile"
    assert workspace.measure_params_stack.currentWidget() is workspace.line_profile_params_panel
    assert workspace.measure_summary_stack.currentWidget() is workspace.line_profile_summary_panel
    assert workspace.canvas.view.roi_mode == "profile_line"
    assert "Draw a line" in workspace.step_hint.text()

    workspace.measure_mode_buttons["spot_count"].click()
    assert workspace.measure_mode == "spot_count"
    assert workspace.measure_params_stack.currentWidget() is workspace.spot_count_params_panel
    assert workspace.canvas.view.roi_mode == ""
    assert "Adjust threshold" in workspace.step_hint.text()

    workspace.measure_mode_buttons["ai_assisted"].click()
    assert workspace.measure_mode == "ai_assisted"
    assert workspace.measure_params_stack.currentWidget() is workspace.ai_assisted_params_panel
    assert workspace.measure_summary_stack.currentWidget() is workspace.ai_assisted_summary_panel
    assert workspace.canvas.view.roi_mode == ""
    assert "AI-assisted" in workspace.step_hint.text()
    app.processEvents()


def test_fluorescence_step_nav_inserts_stats_between_measure_and_layout() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()

    assert [button.text() for button in workspace.step_buttons] == ["Import", "Channels", "Measure", "Stats", "Layout", "Export"]
    assert workspace.context_stack.count() == 6

    workspace.set_step(3)
    assert "Stats" in workspace.step_hint.text()
    assert workspace.context_stack.currentIndex() == 3

    workspace.set_step(4)
    assert "Layout" in workspace.step_hint.text()
    assert workspace.canvas.current_step == 4
    app.processEvents()


def test_ai_assisted_measurements_feed_fluoro_statistics_controls(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ai_stats_source.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()

    workspace.ai_run_button.click()
    app.processEvents()

    assert workspace.ai_results_table.rowCount() == 2

    workspace.set_step(3)
    source_index = workspace.fluoro_stats_source_combo.findData("ai_assisted")
    assert source_index >= 0
    workspace.fluoro_stats_source_combo.setCurrentIndex(source_index)
    app.processEvents()

    assert workspace.fluoro_stats_group_table.rowCount() == 2
    workspace.run_fluoro_stats_button.click()
    app.processEvents()

    assert workspace.fluoro_stats_result_table.rowCount() == 2


def test_ai_assisted_distribution_plots_can_be_added_to_layout(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "ai_dist_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "ai_dist_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()

    _select_fluoro_image(workspace, str(first["uid"]))
    workspace.ai_run_button.click()
    _select_fluoro_image(workspace, str(second["uid"]))
    workspace.ai_run_button.click()
    app.processEvents()

    workspace.set_step(3)
    source_index = workspace.fluoro_stats_source_combo.findData("ai_assisted")
    workspace.fluoro_stats_source_combo.setCurrentIndex(source_index)
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_bar"))
    workspace._add_fluoro_distribution_bar_plot_to_layout()
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_line"))
    workspace._add_fluoro_distribution_line_plot_to_layout()

    plots = panel.analysis.get("fluoro_layout_plots", [])
    assert plots[-2]["plot_type"] == "frequency_bar"
    assert plots[-1]["plot_type"] == "frequency_line"
    assert len(plots[-2]["distribution_result"]["series"]) >= 2


def test_fluorescence_image_switcher_stays_available_across_workflow_steps(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "switch_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "switch_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    for step in (0, 1, 2, 3, 4):
        workspace.set_step(step)
        assert not workspace.active_image_group.isHidden()
        assert not workspace.active_image_combo.isHidden()

    workspace.set_step(3)
    _select_fluoro_image(workspace, str(second["uid"]))
    app.processEvents()

    assert panel.active_image()["uid"] == second["uid"]
    assert panel.active_image()["uid"] != first["uid"]


def test_fluorescence_ai_channel_list_caps_visible_rows(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ai_channel_rows.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    imported = panel.import_image(image_path)
    imported["channels"] = [
        {**dict(imported["channels"][0]), "index": index, "name": f"Ch{index + 1}"}
        for index in range(5)
    ]
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()

    assert workspace.ai_channel_list.count() >= 3
    assert workspace.ai_channel_list.maximumHeight() <= 112
    assert "Scale:" in workspace.ai_scale_status_label.text()
    assert workspace.ai_draw_scale_line_button.text() == tr("draw_scale")
    assert workspace.ai_draw_scale_line_button.property("i18nTextKey") == "draw_scale"


def test_fluorescence_measurement_scale_line_can_be_repositioned(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ai_scale_drag.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()

    workspace._add_drawn_roi("scale_line", {"x1": 1.0, "y1": 2.0, "x2": 8.0, "y2": 2.0})
    workspace._add_drawn_roi("scale_line", {"x1": 3.0, "y1": 4.0, "x2": 10.0, "y2": 4.0})

    scale = panel.measurement_scale_bar(str(image["uid"]))
    assert scale["line_px"] == {"x1": 3.0, "y1": 4.0, "x2": 10.0, "y2": 4.0}
    assert scale["calibration_source"] == "manual_line"
    app.processEvents()


def test_fluorescence_frequency_plot_generation_selects_visible_layout_plot(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "visible_frequency_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "visible_frequency_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    _select_fluoro_image(workspace, str(first["uid"]))
    workspace.ai_run_button.click()
    _select_fluoro_image(workspace, str(second["uid"]))
    workspace.ai_run_button.click()

    workspace.set_step(3)
    assert workspace.fluoro_stats_source_combo.currentData() != "ai_assisted"
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_bar"))
    app.processEvents()
    assert workspace.fluoro_stats_chart_layout.count() == 1
    workspace._add_fluoro_distribution_bar_plot_to_layout()
    app.processEvents()

    assert panel.analysis["fluoro_stats_plot"]["plot_type"] == "frequency_bar"
    assert any(item.data(0) == "fluoro_stats_plot" for item in workspace.canvas.scene.items())


def test_fluorescence_add_plot_to_layout_respects_frequency_line_chart_mode(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "line_frequency_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "line_frequency_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    _select_fluoro_image(workspace, str(first["uid"]))
    workspace.ai_run_button.click()
    _select_fluoro_image(workspace, str(second["uid"]))
    workspace.ai_run_button.click()

    workspace.set_step(3)
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_line"))
    workspace.fluoro_stats_add_to_layout_button.click()
    app.processEvents()

    assert panel.analysis["fluoro_stats_plot"]["plot_type"] == "frequency_line"
    assert panel.analysis["fluoro_layout_plots"][-1]["plot_type"] == "frequency_line"
    assert len(panel.analysis["fluoro_layout_plots"][-1]["distribution_result"]["series"]) >= 2
    plot_items = [item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot"]
    assert plot_items
    assert any(item.data(2) == "frequency_line" for item in plot_items)


def test_fluorescence_frequency_chart_mode_prioritizes_chart_preview(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "visible_frequency_ui_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "visible_frequency_ui_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    _select_fluoro_image(workspace, str(first["uid"]))
    workspace.ai_run_button.click()
    _select_fluoro_image(workspace, str(second["uid"]))
    workspace.ai_run_button.click()

    workspace.set_step(3)
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_line"))
    app.processEvents()

    assert workspace.fluoro_stats_chart_layout.count() == 1
    assert workspace.fluoro_stats_result_table.isHidden()
    assert workspace.run_fluoro_stats_button.isHidden()
    assert workspace.fluoro_stats_add_to_layout_button.text() == "Add Frequency Line to Layout"


def test_fluorescence_frequency_chart_uses_sample_key_after_ai_statistics_run(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    first_path = _write_measure_tiff(tmp_path / "ai_stats_frequency_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "ai_stats_frequency_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    _select_fluoro_image(workspace, str(first["uid"]))
    workspace.ai_channel_list.clearSelection()
    workspace.ai_channel_list.item(0).setSelected(True)
    workspace.ai_run_button.click()
    _select_fluoro_image(workspace, str(second["uid"]))
    workspace.ai_channel_list.clearSelection()
    workspace.ai_channel_list.item(0).setSelected(True)
    workspace.ai_run_button.click()

    workspace.set_step(3)
    workspace.fluoro_stats_source_combo.setCurrentIndex(workspace.fluoro_stats_source_combo.findData("ai_assisted"))
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(workspace.fluoro_stats_chart_mode_combo.findData("frequency_line"))
    app.processEvents()

    assert workspace.fluoro_stats_chart_layout.count() == 1
    assert len(workspace._current_distribution_result()["series"]) == 2


def test_fluorescence_refresh_results_rescales_existing_ai_area_measurements(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ai_scale_refresh.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["ai_assisted"].click()
    workspace.ai_channel_list.clearSelection()
    workspace.ai_channel_list.item(0).setSelected(True)
    workspace.ai_run_button.click()

    record = panel.analysis["fluorescence_ai_assisted_measurements"][0]
    mean_before = record.get("mean_object_area_um2")
    object_before = record["object_table"][0].get("area_um2")
    hist_um_before = (record.get("measurement_scale_bar") or {}).get("um_per_px")

    workspace.ai_scale_um_per_px_spin.setValue(2.0)
    workspace.ai_apply_scale_button.click()
    app.processEvents()

    record = panel.analysis["fluorescence_ai_assisted_measurements"][0]
    assert record.get("mean_object_area_um2") == mean_before
    assert record["object_table"][0].get("area_um2") == object_before
    assert (record.get("measurement_scale_bar") or {}).get("um_per_px") == hist_um_before

    workspace.ai_refresh_results_button.click()
    app.processEvents()

    record = panel.analysis["fluorescence_ai_assisted_measurements"][0]
    assert record["measurement_scale_bar"]["um_per_px"] == 2.0
    assert float(record["mean_object_area_um2"]) == pytest.approx(
        float(record["mean_object_area_px"]) * 4.0
    )
    assert float(record["object_table"][0]["area_um2"]) == float(record["object_table"][0]["area_px"]) * 4.0
    assert workspace.ai_summary_stats_table.item(0, 5).text() == f"{float(record['mean_object_area_um2']):.2f}"


def test_fluorescence_frequency_plot_to_composer_uses_high_dpi_record_render(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    first_path = _write_measure_tiff(tmp_path / "composer_frequency_first.tif")
    second_path = _write_measure_tiff_with_values(tmp_path / "composer_frequency_second.tif", ch0_roi=140, ch0_second_roi=60, ch1_roi=280)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    panel = window._active_fluoro_panel()
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    window.fluoro_workspace.set_panel(panel)
    window.fluoro_workspace.set_step(2)
    window.fluoro_workspace.measure_mode_buttons["ai_assisted"].click()
    _select_fluoro_image(window.fluoro_workspace, str(first["uid"]))
    window.fluoro_workspace.ai_run_button.click()
    _wait_for_fluoro_analysis(app, window, "ai")
    _select_fluoro_image(window.fluoro_workspace, str(second["uid"]))
    window.fluoro_workspace.ai_run_button.click()
    _wait_for_fluoro_analysis(app, window, "ai")

    window.fluoro_workspace.set_step(3)
    window.fluoro_workspace.fluoro_stats_chart_mode_combo.setCurrentIndex(
        window.fluoro_workspace.fluoro_stats_chart_mode_combo.findData("frequency_line")
    )
    window.fluoro_workspace.fluoro_stats_add_to_layout_button.click()
    app.processEvents()
    window.send_fluorescence_layout_to_composer()

    plot_panels = [
        item for item in window.project.panels
        if getattr(item, "panel_type", "") == "external_image" and item.data.get("plot_type") == "frequency_line"
    ]
    assert plot_panels
    plot_asset = plot_panels[-1]
    assert plot_asset.data["source_format"] == "SVG"
    assert Path(str(plot_asset.data["image_path"])).suffix.lower() == ".svg"
    assert b"<svg" in Path(str(plot_asset.data["image_path"])).read_bytes()[:512]
    _close_window(window, app)


def test_fluorescence_layout_frequency_plot_uses_svg_graphics_item(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "vector_frequency_layout.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    panel.analysis["fluoro_layout_plots"] = [
        {
            "plot_uuid": "frequency-vector",
            "plot_type": "frequency_line",
            "enabled": True,
            "x": 10.0,
            "y": 20.0,
            "width_px": 360,
            "height_px": 260,
            "distribution_result": {
                "unit": "um^2",
                "frequency_mode": "absolute",
                "series": [
                    {"lane_label": "Control", "values": [10.0, 20.0, 35.0]},
                    {"lane_label": "Treatment", "values": [14.0, 28.0, 42.0]},
                ],
            },
        }
    ]
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    plot_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot")
    assert any(isinstance(child, QGraphicsPixmapItem) for child in plot_item.childItems())
    record = panel.analysis["fluoro_layout_plots"][0]
    svg = workspace.canvas.render_layout_plot_record_svg(
        record,
        360.0,
        260.0,
    )
    entries = svg_text_entries(svg)
    axis_entry = next(entry for entry in entries if entry.text == "Cell area (um²)")
    assert plot_item.apply_svg_text_override(axis_entry.index, "Custom axis")
    assert record["svg_text_overrides"] == {str(axis_entry.index): "Custom axis"}
    updated_svg = workspace.canvas.render_layout_plot_record_svg(record, 360.0, 260.0)
    assert any(entry.text == "Custom axis" for entry in svg_text_entries(updated_svg))
    app.processEvents()


def test_fluoro_layout_plot_text_is_directly_movable_without_dialog(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(_write_measure_tiff(tmp_path / "layout_plot_text_move.tif"))
    panel.analysis["fluoro_layout_plots"] = [
        {
            "plot_uuid": "frequency-vector",
            "plot_type": "frequency_line",
            "enabled": True,
            "x": 10.0,
            "y": 20.0,
            "width_px": 360,
            "height_px": 260,
            "distribution_result": {
                "unit": "um^2",
                "frequency_mode": "absolute",
                "series": [
                    {"lane_label": "Control", "values": [10.0, 20.0, 35.0]},
                    {"lane_label": "Treatment", "values": [14.0, 28.0, 42.0]},
                ],
            },
        }
    ]
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    plot_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot")
    text_item = plot_item._svg_text_items[0]
    plot_visual_item = next(child for child in plot_item.childItems() if isinstance(child, QGraphicsPixmapItem))
    start_scene = text_item.scenePos()
    plot_start = plot_item.pos()

    assert text_item.parentItem() is plot_visual_item
    assert b"<text" not in plot_item._display_svg_bytes

    plot_item.setPos(plot_start + QPointF(24.0, 16.0))

    assert text_item.scenePos() == start_scene + QPointF(24.0, 16.0)

    moved_index = int(text_item.data(1))
    text_item.setPos(text_item.pos().x() + 15.0, text_item.pos().y() + 9.0)

    override = panel.analysis["fluoro_layout_plots"][0]["svg_text_overrides"][str(moved_index)]
    assert isinstance(override, dict)
    assert override["x"] > 0
    assert override["y"] > 0

    assert plot_item.apply_svg_text_override(moved_index, "Edited axis")

    override = panel.analysis["fluoro_layout_plots"][0]["svg_text_overrides"][str(moved_index)]
    assert isinstance(override, dict)
    assert override["text"] == "Edited axis"
    assert override["x"] > 0
    assert override["y"] > 0

    exported = export_fluoro_figure_scene(workspace.canvas.scene, tmp_path / "fluoro_layout_text.svg", dpi=300)
    svg_text = exported.read_text(encoding="utf-8")

    assert svg_text.count("Edited axis") == 1
    app.processEvents()


def test_roi_intensity_has_independent_channel_selector(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "channels.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    assert workspace.roi_channel_combo is not workspace.measure_channel_combo
    assert workspace.roi_channel_combo.count() == 2
    assert workspace.roi_channel_combo.itemText(0) == "DAPI"
    assert workspace.roi_channel_combo.itemText(1) == "GFP"
    assert workspace.measure_params_stack.currentWidget() is workspace.roi_intensity_params_panel
    assert workspace.roi_channel_combo.parent() is not None
    app.processEvents()


def test_split_roi_uses_channel_from_clicked_image(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "split_source.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    panel.set_view_mode(str(image["uid"]), "split")
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    channel_items = [item for item in workspace.canvas._image_items if item.data(0) == "fluoro_image_channel"]
    assert [item.data(1) for item in channel_items] == [0, 1]

    second_rect = channel_items[1].sceneBoundingRect().adjusted(4, 4, -4, -4)
    geometry = workspace.canvas._map_rect_to_image_geometry("rectangle", second_rect)

    assert geometry is not None
    assert geometry["source_channel_index"] == 1
    workspace._add_drawn_roi("rectangle", geometry)

    measurement = panel.analysis["measurements"][-1]
    assert measurement["source_channels"] == ["GFP"]
    assert measurement["results_table"][0]["channel"] == "GFP"
    app.processEvents()


def test_split_sample_spot_count_uses_channel_from_clicked_image(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "split_count.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    panel.set_view_mode(str(image["uid"]), "split")
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["spot_count"].click()

    channel_items = [item for item in workspace.canvas._image_items if item.data(0) == "fluoro_image_channel"]
    second_rect = channel_items[1].sceneBoundingRect().adjusted(3, 3, -3, -3)
    geometry = workspace.canvas._map_rect_to_image_geometry("sample_spot", second_rect)
    assert geometry is not None

    workspace._add_drawn_roi("sample_spot", geometry)

    rows = panel.analysis["object_counts"]
    assert len(rows) == 1
    assert rows[0]["channel_index"] == 1
    assert rows[0]["channel_name"] == "GFP"
    app.processEvents()


def test_black_balance_roi_is_non_destructive_and_used_for_counting(tmp_path) -> None:
    image_path = _write_background_tiff(tmp_path / "black_balance.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    raw_before = panel.image_data(str(image["uid"])).copy()

    correction = panel.set_black_balance_roi(str(image["uid"]), {"x": 0, "y": 0, "w": 3, "h": 3})
    corrected = panel.black_balanced_image_data(str(image["uid"]))
    counts = panel.count_channel_objects(str(image["uid"]), [0], method="manual", manual_value=50, min_area_px=3)

    assert correction["channel_backgrounds"] == {0: 40.0}
    assert np.array_equal(panel.image_data(str(image["uid"])), raw_before)
    assert corrected[0, 0, 0] == 0
    assert corrected[0, 2, 3] == 100
    assert counts[0]["background_correction"]["mode"] == "black_balance"
    assert counts[0]["count"] == 2


def test_black_balance_is_used_for_roi_intensity_and_recorded(tmp_path) -> None:
    image_path = _write_background_tiff(tmp_path / "roi_black_balance.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    raw_before = panel.image_data(str(image["uid"])).copy()

    panel.set_black_balance_roi(str(image["uid"]), {"x": 0, "y": 0, "w": 3, "h": 3})
    measurement = panel.create_roi_intensity_measurement(
        str(image["uid"]),
        "rectangle",
        {"x": 3, "y": 2, "w": 3, "h": 3},
        [0],
    )

    assert np.array_equal(panel.image_data(str(image["uid"])), raw_before)
    assert measurement["params"]["image_background_correction"]["mode"] == "black_balance"
    assert measurement["results_table"][0]["mean"] == 100.0
    assert measurement["results_table"][0]["integrated_density"] == 900.0


def test_black_balance_ui_is_available_in_roi_and_shows_active_hint(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_background_tiff(tmp_path / "roi_black_balance_ui.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    assert workspace.roi_black_balance_button.text() == "Black Balance ROI"
    assert "not set" in workspace.black_balance_status_label.text().lower()

    workspace.roi_black_balance_button.click()
    workspace._add_drawn_roi("black_balance", {"x": 0, "y": 0, "w": 3, "h": 3, "source_channel_index": 0})

    assert "active" in workspace.black_balance_status_label.text().lower()
    assert "Black balance active" in workspace.step_hint.text()
    app.processEvents()


def test_black_balance_can_apply_only_to_selected_split_channel(tmp_path) -> None:
    image_path = _write_measure_tiff(tmp_path / "selected_channel_black_balance.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])

    correction = panel.set_black_balance_roi(
        uid,
        {"x": 10, "y": 8, "w": 2, "h": 2, "source_channel_index": 1},
        scope="selected_channel",
    )
    corrected = panel.black_balanced_image_data(uid)

    assert correction["scope"] == "selected_channel"
    assert correction["channel_backgrounds"] == {1: 40.0}
    assert corrected[0, 8, 10] == 10
    assert corrected[1, 8, 10] == 0


def test_black_balance_scope_control_drives_roi_tool_scope(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "scope_ui.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    panel.set_view_mode(str(image["uid"]), "split")
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    assert workspace.black_balance_scope_combo.currentData() == "all_channels"
    workspace.black_balance_scope_combo.setCurrentText("Selected channel")
    workspace._add_drawn_roi("black_balance", {"x": 10, "y": 8, "w": 2, "h": 2, "source_channel_index": 1})

    correction = panel.active_image()["background_correction"]
    assert correction["scope"] == "selected_channel"
    assert correction["channel_backgrounds"] == {1: 40.0}
    assert "Selected channel" in workspace.black_balance_status_label.text()
    app.processEvents()


def test_roi_and_count_black_balance_controls_are_independent_and_visible() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    workspace.set_step(2)

    workspace.measure_mode_buttons["roi_intensity"].click()
    assert workspace.roi_black_balance_scope_combo is not workspace.count_black_balance_scope_combo
    assert not workspace.roi_advanced_section.expanded
    workspace.roi_advanced_section.setExpanded(True)
    assert workspace.roi_black_balance_scope_combo.isVisibleTo(workspace.roi_intensity_params_panel)
    assert workspace.roi_black_balance_scope_combo.count() == 2
    assert workspace.roi_black_balance_status_label.isVisibleTo(workspace.roi_intensity_params_panel)

    workspace.measure_mode_buttons["spot_count"].click()
    assert not workspace.count_threshold_section.expanded
    workspace.count_threshold_section.setExpanded(True)
    assert workspace.count_black_balance_scope_combo.isVisibleTo(workspace.spot_count_params_panel)
    assert workspace.count_black_balance_scope_combo.count() == 2
    assert workspace.count_black_balance_status_label.isVisibleTo(workspace.spot_count_params_panel)
    app.processEvents()


@pytest.mark.parametrize('modifiers', [Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier])
def test_count_results_accumulate_and_selection_restores_overlay(tmp_path, modifiers) -> None:
    from PyQt6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "count_history.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["spot_count"].click()

    workspace._add_drawn_roi("sample_spot", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    first_uuid = panel.analysis["object_counts"][0]["count_uuid"]
    workspace._add_drawn_roi("sample_spot", {"x": 10, "y": 8, "w": 2, "h": 2, "source_channel_index": 1})
    second_uuid = panel.analysis["object_counts"][1]["count_uuid"]

    assert len(panel.analysis["object_counts"]) == 2
    assert workspace.count_table.rowCount() == 2
    assert first_uuid != second_uuid

    if modifiers:
        QTest.keyPress(workspace.count_table, Qt.Key.Key_Control, modifiers)
    try:
        workspace.count_table.selectRow(0)
        app.processEvents()
        assert workspace.count_table.currentRow() == 0
        if modifiers:
            assert {item.row() for item in workspace.count_table.selectionModel().selectedRows()} == {0, 1}
        assert workspace.canvas.active_measurement_overlay_uuid == first_uuid
    finally:
        if modifiers:
            QTest.keyRelease(workspace.count_table, Qt.Key.Key_Control)
    overlay_items = [
        item
        for item in workspace.canvas.scene.items()
        if item.data(0) in {"fluoro_count_sample_overlay", "fluoro_count_spot_overlay"}
    ]
    assert overlay_items
    app.processEvents()


def test_measure_result_controls_delete_and_clear_roi_rows_in_ui(tmp_path, monkeypatch) -> None:
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_delete_roi_rows.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    first_uuid = panel.analysis["measurements"][0]["measurement_uuid"]
    workspace._add_drawn_roi("rectangle", {"x": 10, "y": 8, "w": 2, "h": 2, "source_channel_index": 1})

    workspace.set_step(3)
    assert workspace.fluoro_stats_group_table.rowCount() == 2
    workspace.set_step(2)
    workspace.measurement_table.selectRow(0)
    workspace.delete_selected_measurement_button.click()

    assert [row["measurement_uuid"] for row in panel.analysis["measurements"]] != [first_uuid]
    assert workspace.measurement_table.rowCount() == 1
    assert workspace.canvas.active_measurement_overlay_uuid is None

    workspace.clear_roi_measurements_button.click()

    assert panel.analysis["measurements"] == []
    assert panel.active_image()["measurements"] == []
    assert workspace.measurement_table.rowCount() == 0
    workspace.set_step(3)
    assert workspace.fluoro_stats_group_table.rowCount() == 0
    app.processEvents()


def test_measure_result_controls_delete_and_clear_count_rows_in_ui(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_delete_count_rows.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["spot_count"].click()
    workspace._add_drawn_roi("sample_spot", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    first_uuid = panel.analysis["object_counts"][0]["count_uuid"]
    workspace._add_drawn_roi("sample_spot", {"x": 10, "y": 8, "w": 2, "h": 2, "source_channel_index": 1})

    workspace.count_table.selectRow(0)
    workspace.delete_selected_count_button.click()

    assert [row["count_uuid"] for row in panel.analysis["object_counts"]] != [first_uuid]
    assert workspace.count_table.rowCount() == 1
    assert workspace.canvas.active_measurement_overlay_uuid is None

    workspace.clear_counts_button.click()

    assert panel.analysis["object_counts"] == []
    assert panel.active_image()["object_counts"] == []
    assert workspace.count_table.rowCount() == 0
    app.processEvents()


def test_roi_overlay_is_replaced_then_restored_from_results_selection(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "overlay.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    workspace._add_drawn_roi("rectangle", {"x": 2, "y": 2, "w": 4, "h": 4, "source_channel_index": 0})
    first_uuid = panel.analysis["measurements"][-1]["measurement_uuid"]
    assert workspace.canvas.active_measurement_overlay_uuid == first_uuid

    workspace._add_drawn_roi("rectangle", {"x": 6, "y": 3, "w": 3, "h": 3, "source_channel_index": 1})
    second_uuid = panel.analysis["measurements"][-1]["measurement_uuid"]
    assert workspace.canvas.active_measurement_overlay_uuid == second_uuid

    workspace.measurement_table.selectRow(0)
    app.processEvents()

    assert workspace.canvas.active_measurement_overlay_uuid == first_uuid
    assert workspace.results_drawer.isChecked()
    app.processEvents()


def test_switching_to_count_clears_roi_overlay(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "clear_overlay.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)

    workspace._add_drawn_roi("rectangle", {"x": 2, "y": 2, "w": 4, "h": 4, "source_channel_index": 0})
    assert workspace.canvas.active_measurement_overlay_uuid is not None

    workspace.measure_mode_buttons["spot_count"].click()

    assert workspace.canvas.active_measurement_overlay_uuid is None
    app.processEvents()


def test_sample_spot_count_opens_review_image_dialog(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "count_preview.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(2)
    workspace.measure_mode_buttons["spot_count"].click()

    workspace._add_drawn_roi("sample_spot", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})

    dialog = workspace.last_count_preview_dialog
    assert dialog is not None
    assert not hasattr(dialog, "results_table")
    assert dialog.review_image_label.pixmap() is not None
    assert not dialog.review_image_label.pixmap().isNull()
    assert "Review" in dialog.reliability_label.text()
    assert dialog.isVisible()
    rows = panel.analysis["object_counts"]
    assert rows[0]["channel_name"] == "GFP"
    assert rows[0]["detected_spots"]
    dialog.close()
    app.processEvents()


def test_stats_page_empty_state_and_run_persists_fluoro_statistics(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_stats_run.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(3)

    assert workspace.fluoro_stats_group_table.rowCount() == 0
    workspace.run_fluoro_stats_button.click()
    assert workspace.fluoro_stats_result_table.rowCount() == 0
    from core.utils.i18n import trf
    assert workspace.fluoro_stats_source_combo.currentData() == 'roi_intensity'
    assert workspace.fluoro_stats_summary_label.text() == trf('no_source_measurements', source=workspace.fluoro_stats_source_combo.currentText())

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()

    assert panel.analysis["fluoro_statistics"]["source"] == "roi_intensity"
    assert panel.analysis["fluoro_statistics"]["metric"] == "mean"
    assert workspace.fluoro_stats_result_table.rowCount() == 2
    assert "Control vs Treatment" in workspace.fluoro_stats_summary_label.text()
    app.processEvents()


def test_fluoro_stats_ui_groups_image_level_samples_not_each_roi(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    panel = FluorescencePanel(panel_id="Figure1C")
    for filename, first_value, second_value in [
        ("sample_a.tif", 100, 120),
        ("sample_b.tif", 180, 200),
    ]:
        image = panel.import_image(_write_measure_tiff_with_values(tmp_path / filename, first_value, second_value))
        uid = str(image["uid"])
        panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 3, "y": 2, "w": 5, "h": 4}, [0])
        panel.create_roi_intensity_measurement(uid, "rectangle", {"x": 10, "y": 8, "w": 2, "h": 2}, [0])
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(3)

    assert workspace.fluoro_stats_group_table.rowCount() == 2
    assert "sample_a.tif" in workspace.fluoro_stats_group_table.item(0, 0).text()
    assert "n=2" in workspace.fluoro_stats_group_table.item(0, 0).text()
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")

    workspace.run_fluoro_stats_button.click()

    assert {row["group"]: row["n"] for row in panel.analysis["fluoro_statistics"]["groups"]} == {"Control": 1, "Treatment": 1}
    assert len(panel.analysis["fluoro_statistics"]["plot_data"]) == 2
    app.processEvents()


def test_fluoro_stats_chart_preview_and_add_plot_to_layout(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_stats_plot.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")

    workspace.run_fluoro_stats_button.click()

    assert workspace.fluoro_stats_chart_layout.count() == 1

    workspace.fluoro_stats_add_to_layout_button.click()

    assert panel.analysis["fluoro_stats_plot"]["enabled"] is True
    assert workspace.context_stack.currentIndex() == 4
    assert any(item.data(0) == "fluoro_stats_plot" for item in workspace.canvas.scene.items())
    app.processEvents()


def test_fluoro_stats_adds_multiple_layout_plots_without_overwriting(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_stats_multi_plot.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()

    metric_index = workspace.fluoro_stats_metric_combo.findData("integrated_density")
    workspace.fluoro_stats_metric_combo.setCurrentIndex(metric_index)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()

    assert len(panel.analysis["fluoro_layout_plots"]) == 2
    assert [plot["metric"] for plot in panel.analysis["fluoro_layout_plots"]] == ["mean", "integrated_density"]
    assert panel.analysis["fluoro_stats_plot"]["enabled"] is True
    assert sum(1 for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot") == 2
    app.processEvents()


def test_stats_page_adds_line_profile_and_colocalization_plots_to_layout(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_stats_profile_coloc.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = str(image["uid"])
    panel.line_profile(uid, [0, 1], {"x1": 0, "y1": 2, "x2": 13, "y2": 2}, samples=8)
    panel.compute_channel_overlap(uid, [0, 1], method="manual", manual_value=50)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(3)

    workspace.fluoro_add_line_profile_plot_button.click()
    workspace.fluoro_add_colocalization_plot_button.click()

    assert [plot["plot_type"] for plot in panel.analysis["fluoro_layout_plots"]] == ["line_profile", "colocalization"]
    assert workspace.context_stack.currentIndex() == 4
    markers = [item.data(0) for item in workspace.canvas.scene.items()]
    assert "fluoro_profile_plot" in markers
    assert "fluoro_coloc_plot" in markers
    app.processEvents()


def test_fluorescence_layout_has_wb_like_toolbar_and_selectable_shapes() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    workspace.set_step(4)

    assert workspace.layout_undo_button.text() == "Undo"
    assert workspace.layout_redo_button.text() == "Redo"
    assert workspace.layout_align_row_button.text() == "Align Row"
    assert workspace.layout_align_column_button.text() == "Align Column"
    assert workspace.layout_sync_lines_button.text() == "Sync Lines"
    assert workspace.layout_recenter_button.text() == "Recenter"

    workspace.layout_shape_combo.setCurrentText("Line")
    workspace.layout_shape_stroke_width_spin.setValue(3.0)
    workspace.layout_shape_stroke_color_combo.setCurrentText("#FF3333 (绾㈣壊 / RFP, mCherry)")
    workspace.layout_shape_fill_combo.setCurrentText("#22D7E6 (Nature cyan / fluorescence)")
    workspace.layout_add_shape_button.click()

    shape_items = [item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_layout_shape"]
    assert len(shape_items) == 1
    assert shape_items[0].flags() & shape_items[0].GraphicsItemFlag.ItemIsMovable
    assert shape_items[0].flags() & shape_items[0].GraphicsItemFlag.ItemIsSelectable
    app.processEvents()


def test_fluoro_stats_plot_style_controls_update_selected_layout_plot(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_stats_style_controls.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()

    workspace.fluoro_stats_font_size_spin.setValue(9.0)
    workspace.fluoro_stats_plot_width_spin.setValue(640)
    workspace.fluoro_stats_plot_height_spin.setValue(420)
    workspace.fluoro_stats_bar_width_spin.setValue(0.42)
    workspace.fluoro_stats_bar_line_width_spin.setValue(1.4)
    workspace.fluoro_stats_axis_line_width_spin.setValue(1.2)
    workspace.fluoro_stats_point_shape_combo.setCurrentText("diamond")
    workspace.fluoro_stats_point_size_spin.setValue(22.0)
    workspace.fluoro_stats_apply_style_button.click()

    plot = panel.analysis["fluoro_layout_plots"][0]
    assert plot["width_px"] == 640
    assert plot["height_px"] == 420
    assert plot["style"]["font_size_pt"] == 9.0
    assert plot["style"]["bar_width_fraction"] == 0.42
    assert plot["style"]["bar_line_width"] == 1.4
    assert plot["style"]["axis_line_width"] == 1.2
    assert plot["style"]["point_shape"] == "diamond"
    assert plot["style"]["point_size"] == 22.0
    app.processEvents()


def test_fluoro_layout_plot_and_shape_positions_persist_across_refresh(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "ui_layout_position_persist.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()
    workspace.layout_shape_combo.setCurrentText("Rectangle")
    workspace.layout_add_shape_button.click()

    plot_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot")
    shape_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_layout_shape")
    plot_item.moveBy(25.0, 35.0)
    shape_item.moveBy(15.0, 20.0)
    plot_x, plot_y = plot_item.pos().x(), plot_item.pos().y()
    shape_x, shape_y = shape_item.pos().x(), shape_item.pos().y()

    workspace.set_step(3)
    workspace.set_step(4)

    assert panel.analysis["fluoro_layout_plots"][0]["x"] == plot_x
    assert panel.analysis["fluoro_layout_plots"][0]["y"] == plot_y
    shape = panel.active_image()["layout_shapes"][0]
    assert shape["x"] == shape_x
    assert shape["y"] == shape_y
    app.processEvents()


def test_fluorescence_layout_to_composer_splits_base_layout_and_stats_plots(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_split_to_composer.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    _wait_for_fluoro_import(app, window)
    workspace = window.fluoro_workspace

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    _wait_for_fluoro_analysis(app, window, "roi-intensity")
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    _wait_for_fluoro_analysis(app, window, "roi-intensity")
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()

    workspace.send_layout_to_composer_button.click()

    base_assets = [
        panel
        for panel in window.project.panels
        if panel.panel_type == "external_image" and panel.data.get("source_module") == "fluorescence_layout"
    ]
    plot_assets = [
        panel
        for panel in window.project.panels
        if panel.panel_type == "external_image" and panel.data.get("source_module") == "fluorescence_layout_plot"
    ]
    assert len(base_assets) == 1
    assert len(plot_assets) == 1
    assert base_assets[0].data["image_path"] != plot_assets[0].data["image_path"]
    assert plot_assets[0].data["plot_type"] == "statistics"
    _close_window(window, app)


def test_fluoro_layout_plot_resize_handles_persist_to_composer_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_resize_to_composer.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    _wait_for_fluoro_import(app, window)
    workspace = window.fluoro_workspace

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    _wait_for_fluoro_analysis(app, window, "roi-intensity")
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    _wait_for_fluoro_analysis(app, window, "roi-intensity")
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()

    plot_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot")
    plot_item.setSelected(True)
    handles = [child for child in plot_item.childItems() if child.data(0) == "fluoro_resize_handle"]
    assert len(handles) == 8
    plot_item.apply_geometry(plot_item.pos().x(), plot_item.pos().y(), 520.0, 260.0)

    workspace.send_layout_to_composer_button.click()

    plot = window._active_fluoro_panel().analysis["fluoro_layout_plots"][0]
    assert plot["width_px"] == 520
    assert plot["height_px"] == 260
    plot_assets = [
        panel
        for panel in window.project.panels
        if panel.panel_type == "external_image" and panel.data.get("source_module") == "fluorescence_layout_plot"
    ]
    assert len(plot_assets) == 1
    exported = QImage(str(plot_assets[0].data["image_path"]))
    assert not exported.isNull()
    assert 1.8 < exported.width() / exported.height() < 2.2
    _close_window(window, app)


def test_fluoro_layout_same_size_align_distribute_and_delete_selected_items(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_arrange_delete.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)

    workspace.set_step(2)
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0})
    workspace._add_drawn_roi("rectangle", {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 1})
    workspace.set_step(3)
    workspace.fluoro_stats_group_table.item(0, 1).setText("Control")
    workspace.fluoro_stats_group_table.item(1, 1).setText("Treatment")
    workspace.fluoro_stats_reference_group_combo.setCurrentText("Control")
    workspace.run_fluoro_stats_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()
    workspace.fluoro_stats_add_to_layout_button.click()
    workspace.layout_add_text_edit.setText("delete me")
    workspace._add_layout_text()
    workspace.layout_shape_combo.setCurrentText("Rectangle")
    workspace.layout_add_shape_button.click()

    plot_items = [item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_stats_plot"]
    assert len(plot_items) == 2
    plot_items[0].apply_geometry(plot_items[0].pos().x(), plot_items[0].pos().y(), 420.0, 220.0)
    plot_items[1].apply_geometry(plot_items[1].pos().x(), plot_items[1].pos().y(), 260.0, 180.0)
    plot_items[0].setSelected(True)
    plot_items[1].setSelected(True)

    workspace.layout_same_size_button.click()
    workspace.layout_align_row_button.click()
    workspace.layout_distribute_h_button.click()

    workspace.persist_layout_edits_from_scene()
    widths = {plot["width_px"] for plot in panel.analysis["fluoro_layout_plots"]}
    heights = {plot["height_px"] for plot in panel.analysis["fluoro_layout_plots"]}
    ys = {round(plot["y"], 2) for plot in panel.analysis["fluoro_layout_plots"]}
    assert widths == {420}
    assert heights == {220}
    assert len(ys) == 1

    for item in list(workspace.canvas.scene.items()):
        if item.data(0) in {"fluoro_stats_plot", "fluoro_layout_text", "fluoro_layout_shape"}:
            item.setSelected(True)
    workspace.layout_delete_selected_button.click()

    assert panel.analysis["fluoro_layout_plots"] == []
    assert panel.active_image().get("layout_texts", []) == []
    assert panel.active_image().get("layout_shapes", []) == []
    markers = [item.data(0) for item in workspace.canvas.scene.items()]
    assert "fluoro_stats_plot" not in markers
    assert "fluoro_layout_text" not in markers
    assert "fluoro_layout_shape" not in markers
    app.processEvents()


def test_fluoro_layout_text_and_shape_edits_persist_across_refresh_without_apply(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_text_shape_no_reset.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.layout_add_text_edit.setText("keeps edits")
    workspace._add_layout_text()
    text_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_layout_text")
    text_item.setSelected(True)
    text_item.setPos(QPointF(123.0, 45.0))
    workspace.layout_text_size_spin.setValue(18.0)
    workspace.layout_text_bold.setChecked(True)
    color_index = workspace.layout_text_color_combo.findData("#22D7E6")
    workspace.layout_text_color_combo.setCurrentIndex(color_index)

    workspace.layout_shape_combo.setCurrentText("Rectangle")
    workspace.layout_add_shape_button.click()
    shape_item = next(item for item in workspace.canvas.scene.items() if item.data(0) == "fluoro_layout_shape")
    shape_item.setSelected(True)
    shape_item.setPos(QPointF(80.0, 90.0))
    workspace.layout_shape_stroke_width_spin.setValue(5.0)
    stroke_index = workspace.layout_shape_stroke_color_combo.findData("#FF3333")
    fill_index = workspace.layout_shape_fill_combo.findData("#22D7E6")
    workspace.layout_shape_stroke_color_combo.setCurrentIndex(stroke_index)
    workspace.layout_shape_fill_combo.setCurrentIndex(fill_index)

    workspace.canvas.refresh()

    text_record = panel.active_image()["layout_texts"][0]
    shape_record = panel.active_image()["layout_shapes"][0]
    assert text_record["x"] == 123.0
    assert text_record["y"] == 45.0
    assert text_record["font_size"] == 18.0
    assert text_record["bold"] is True
    assert text_record["color"] == "#22D7E6"
    assert shape_record["x"] == 80.0
    assert shape_record["y"] == 90.0
    assert shape_record["stroke_width"] == 5.0
    assert shape_record["stroke_color"] == "#FF3333"
    assert shape_record["fill_color"] == "#22D7E6"
    app.processEvents()


def test_fluoro_layout_exposes_one_visible_apply_button() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()

    assert workspace.layout_apply_button.text() == "Apply"
    assert not workspace.layout_apply_button.isHidden()
    assert workspace.layout_apply_text_button.isHidden()
    assert workspace.layout_apply_shape_button.isHidden()
    assert workspace.fluoro_stats_apply_style_button.isHidden()
    app.processEvents()


def test_fluoro_layout_scale_bar_controls_apply_to_all_layout_targets(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_scale_bar_controls.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-ui")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = 0.5
    image["layout_item_geometries"] = {
        "merge_split:merge": {"x": 10.0, "y": 10.0, "width": 300.0, "height": 200.0},
        "custom:ch0": {"x": 340.0, "y": 10.0, "width": 180.0, "height": 120.0},
    }
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_length_spin.setValue(50.0)
    workspace.scale_bar_manual_um_per_px_spin.setValue(0.25)
    workspace.scale_bar_text_position_combo.setCurrentText("Below")
    workspace.scale_bar_apply_all_button.click()

    image = panel.active_image()
    states={s['target_id']:s for s in image['layout_scale_bars']}
    assert set(states)=={'merge_split:merge','merge_split:channel:0','merge_split:channel:1'}
    for state in states.values():
        assert state['template']['label_length_um']==50
        assert state['template']['text_position']=='below'
        assert state['template']['anchor']=='bottom_right'
        assert state['manual_um_per_px']==.25 and state['enabled']

    workspace.scale_bar_remove_current_button.click()
    assert image["layout_scale_bars"][0]["enabled"] is False
    assert image["layout_scale_bars"][1]["enabled"] is True
    app.processEvents()


def test_fluoro_apply_scale_bar_button_adds_visible_assumed_scale_bar(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_assumed_scale_bar.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-assumed")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = None
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_length_spin.setValue(50.0)
    workspace.scale_bar_apply_current_button.click()

    image = panel.active_image()
    assert image["scale_bar"]["length_px"] == 0  # Presentation must not fabricate source calibration.
    assert any(str(item.get("target_id", "")).endswith(":merge") and item.get("enabled") for item in image["layout_scale_bars"])
    scale_items = [
        item
        for scene_item in workspace.canvas.scene.items()
        for item in [scene_item, *scene_item.childItems()]
        if str(item.data(0) or "") == "fluoro_scale_bar"
    ]
    assert scale_items
    labels = [item.toPlainText() for item in scale_items if item.data(3) == "text" and hasattr(item, "toPlainText")]
    assert labels
    assert all("px" in label.lower() and "um" not in label.lower() for label in labels)
    app.processEvents()


def test_fluoro_apply_all_scale_bars_split_channels_but_not_statistics_plot(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_scale_bar_all_split.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-scale-all")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = None
    panel.analysis["fluoro_layout_plots"] = [
        {
            "plot_uuid": "plot-a",
            "plot_type": "statistics",
            "enabled": True,
            "title": "Stats",
            "rows": [{"group": "A", "value": 1.0}],
            "x": 0,
            "y": 250,
            "width_px": 180,
            "height_px": 140,
        }
    ]
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_apply_all_button.click()

    target_ids = {str(item.get("target_id", "")) for item in image["layout_scale_bars"] if item.get("enabled")}
    assert target_ids == {"merge_split:channel:0", "merge_split:channel:1", "merge_split:merge"}
    assert all("plot" not in target_id for target_id in target_ids)
    scale_lines = {
        item
        for scene_item in workspace.canvas.scene.items()
        for item in [scene_item, *scene_item.childItems()]
        if str(item.data(0) or "") == "fluoro_scale_bar" and item.data(3) == "line"
    }
    assert len(scale_lines) == 3
    app.processEvents()


def test_fluoro_scale_bar_text_is_centered_over_line_and_delete_disables_target(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_scale_bar_center_delete.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-scale-delete")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = None
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)
    workspace.scale_bar_apply_current_button.click()

    line = next(
        item
        for scene_item in workspace.canvas.scene.items()
        for item in [scene_item, *scene_item.childItems()]
        if str(item.data(0) or "") == "fluoro_scale_bar" and item.data(3) == "line"
    )
    text = next(
        item
        for scene_item in workspace.canvas.scene.items()
        for item in [scene_item, *scene_item.childItems()]
        if str(item.data(0) or "") == "fluoro_scale_bar" and item.data(3) == "text"
    )

    line_rect = line.sceneBoundingRect()
    text_rect = text.sceneBoundingRect()
    assert text_rect.center().x() == pytest.approx(line_rect.center().x(), abs=1.5)

    line.setSelected(True)
    workspace.canvas.delete_selected_layout_items()

    assert image["layout_scale_bars"][0]["enabled"] is False
    remaining_lines = {
        item
        for scene_item in workspace.canvas.scene.items()
        for item in [scene_item, *scene_item.childItems()]
        if str(item.data(0) or "") == "fluoro_scale_bar" and item.data(3) == "line"
    }
    assert remaining_lines == set()
    app.processEvents()


def test_fluoro_scale_bar_apply_remove_support_undo_redo(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_scale_bar_undo_redo.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-scale-undo")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = None
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    workspace.scale_bar_apply_current_button.click()
    assert any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert _fluoro_scale_bar_items(workspace, "line")

    workspace.canvas.undo_layout_edit()
    assert not any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert not _fluoro_scale_bar_items(workspace, "line")

    workspace.canvas.redo_layout_edit()
    assert any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert _fluoro_scale_bar_items(workspace, "line")

    workspace.scale_bar_remove_current_button.click()
    assert not any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert not _fluoro_scale_bar_items(workspace, "line")

    workspace.canvas.undo_layout_edit()
    assert any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert _fluoro_scale_bar_items(workspace, "line")

    workspace.canvas.redo_layout_edit()
    assert not any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert not _fluoro_scale_bar_items(workspace, "line")
    app.processEvents()


def test_fluoro_scale_bar_delete_key_supports_undo_redo(tmp_path) -> None:
    from PyQt6.QtWidgets import QMainWindow
    from ui.controllers.layout_edit_controller import LayoutEditController
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_scale_bar_delete_undo_redo.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-scale-key-delete")
    image = panel.import_image(image_path)
    image["metadata"]["pixel_size_um"] = None
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)
    workspace.scale_bar_apply_current_button.click()
    host=QMainWindow();controller=LayoutEditController(host)
    controller.set_target('fluoro',panel,workspace.canvas)

    line = _fluoro_scale_bar_items(workspace, "line")[0]
    line.setSelected(True)
    delete_event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
    app.sendEvent(workspace.canvas.view,delete_event)

    assert delete_event.isAccepted()
    assert not any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert not _fluoro_scale_bar_items(workspace, "line")

    controller.undo()
    assert any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert _fluoro_scale_bar_items(workspace, "line")

    controller.redo()
    assert not any(item.get("enabled") for item in image.get("layout_scale_bars", []))
    assert not _fluoro_scale_bar_items(workspace, "line")
    controller.set_target();host.close()
    app.processEvents()


def test_fluoro_layout_item_move_still_supports_undo_redo(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_item_move_undo_redo.tif")
    panel = FluorescencePanel(panel_id="Figure1C", panel_uuid="panel-fluoro-layout-move-undo")
    panel.import_image(image_path)
    workspace = FluorescenceWorkspace()
    workspace.set_panel(panel)
    workspace.set_step(4)

    item = next(
        scene_item
        for scene_item in workspace.canvas.scene.items()
        if str(scene_item.data(0) or "") == "fluoro_image_merge"
    )
    original = QPointF(item.pos())
    moved = QPointF(original.x() + 25.0, original.y() + 10.0)

    workspace.canvas._record_layout_snapshot()
    item.setPos(moved)

    workspace.canvas.undo_layout_edit()
    restored = next(
        scene_item
        for scene_item in workspace.canvas.scene.items()
        if str(scene_item.data(0) or "") == "fluoro_image_merge"
    )
    assert restored.pos().x() == pytest.approx(original.x())
    assert restored.pos().y() == pytest.approx(original.y())

    workspace.canvas.redo_layout_edit()
    restored = next(
        scene_item
        for scene_item in workspace.canvas.scene.items()
        if str(scene_item.data(0) or "") == "fluoro_image_merge"
    )
    assert restored.pos().x() == pytest.approx(moved.x())
    assert restored.pos().y() == pytest.approx(moved.y())
    app.processEvents()


def test_roi_measurement_has_no_direct_composer_action(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    workspace.set_step(2)

    assert not hasattr(workspace, "send_roi_result_to_composer_button")
    button_labels = [button.text() for button in workspace.roi_intensity_summary_panel.findChildren(QPushButton)]
    assert all("Composer" not in label for label in button_labels)
    app.processEvents()


def test_fluorescence_layout_can_be_sent_to_composer_as_external_asset(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SCIEFIG_ASSET_DIR", str(tmp_path / "assets"))
    app = QApplication.instance() or QApplication([])
    image_path = _write_measure_tiff(tmp_path / "layout_to_composer.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    _wait_for_fluoro_import(app, window)
    window.fluoro_workspace.set_step(4)
    window.fluoro_workspace.layout_mode_combo.setCurrentText("merge_split")
    window.fluoro_workspace.layout_add_text_edit.setText("Merged")
    window.fluoro_workspace._add_layout_text()

    window.fluoro_workspace.send_layout_to_composer_button.click()

    assets = [
        panel
        for panel in window.project.panels
        if panel.panel_type == "external_image" and panel.data.get("source_module") == "fluorescence_layout"
    ]
    assert len(assets) == 1
    asset_path = Path(str(assets[0].data["image_path"]))
    assert asset_path.exists()
    assert asset_path.parent == tmp_path / "assets" / "fluorescence_layouts"
    window.set_module("composer")
    shelf=window.composer_workspace.assets
    labels = [shelf.item(index).text() for index in range(shelf.count())]
    assert any("fluorescence_layout" in label for label in labels)
    _close_window(window, app)


def test_fluorescence_layout_text_selection_clears_on_blank_click_after_style_change() -> None:
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    workspace.set_step(4)
    workspace.layout_add_text_edit.setText("Merged")
    workspace._add_layout_text()
    item = workspace.canvas.selected_text_items()[0]

    workspace.layout_text_bold.setChecked(True)
    workspace.canvas.clear_text_selection_on_blank_click()

    assert workspace.canvas.selected_text_items() == []
    assert item.font().bold()
    app.processEvents()
