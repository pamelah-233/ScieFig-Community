from __future__ import annotations

import os
import time
import warnings
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pytest
import tifffile
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QGraphicsPathItem, QGraphicsPixmapItem, QGraphicsTextItem, QGroupBox

from core.export import export_fluoro_figure_scene
from core.models import FluorescencePanel, Project
from core.utils.fluoro_image_io import SUPPORTED_FLUORESCENCE_SUFFIXES, import_fluorescence_image
import ui.main_window as main_window_module
from ui.main_window import MainWindow
from ui.panels.fluorescence_workspace import FluorescenceWorkspace
import ui.widgets.fluoro_canvas as fluoro_canvas_module


def _write_multichannel_tiff(path: Path) -> Path:
    data = np.zeros((3, 48, 64), dtype=np.uint16)
    data[0, 8:20, 8:22] = 1200
    data[1, 20:36, 24:44] = 3600
    data[2, 10:30, 42:58] = 5200
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _write_single_channel_tiff(path: Path, value: int, *, shape: tuple[int, int] = (48, 64)) -> Path:
    data = np.zeros((1, shape[0], shape[1]), dtype=np.uint16)
    data[0, 8:24, 10:28] = value
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _write_rgb_single_signal_tiff(path: Path, channel_index: int, value: int, *, shape: tuple[int, int] = (48, 64)) -> Path:
    data = np.zeros((shape[0], shape[1], 3), dtype=np.uint8)
    data[8:24, 10:28, channel_index] = value
    tifffile.imwrite(path, data, metadata={"axes": "YXS"}, photometric="rgb")
    return path


def _close_window(window: MainWindow, app: QApplication) -> None:
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    app.processEvents()


def _wait_for_import(window: MainWindow, module_id: str, timeout: float = 15.0) -> None:
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout
    while window._analysis_job_service.is_busy(f"import:{module_id}"):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {module_id} import")
        time.sleep(0.005)
    app.processEvents()


def _wait_for_fluoro_analysis(window: MainWindow, operation: str, timeout: float = 15.0) -> None:
    app = QApplication.instance()
    assert app is not None
    deadline = time.monotonic() + timeout
    key = f"fluoro-analysis:{operation}"
    while window._analysis_job_service.is_busy(key):
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for {operation}")
        time.sleep(0.005)
    app.processEvents()


@pytest.fixture(autouse=True)
def _wait_for_ui_imports(monkeypatch):
    for method_name, module_id in (
        ("import_fluorescence_images", "fluoro"),
        ("import_ihc_images", "ihc"),
        ("import_brightfield_images", "brightfield"),
    ):
        original = getattr(MainWindow, method_name)

        def wait_after_import(window, paths, *, _original=original, _module_id=module_id):
            result = _original(window, paths)
            _wait_for_import(window, _module_id)
            return result

        monkeypatch.setattr(MainWindow, method_name, wait_after_import)


def test_multichannel_tiff_helper_writes_without_tifffile_deprecation_warning(tmp_path) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _write_multichannel_tiff(tmp_path / "warning_clean.tif")

    messages = [str(item.message) for item in caught]
    assert not any("Future versions will store such data as MINISBLACK" in message for message in messages)


def test_fluoro_import_reads_tiff_as_cyx_and_records_metadata(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "cells.tif")

    result = import_fluorescence_image(image_path)

    assert result.analysis_data.shape == (3, 48, 64)
    assert result.metadata["width_px"] == 64
    assert result.metadata["height_px"] == 48
    assert result.metadata["n_channels"] == 3
    assert result.source_hash.startswith("sha256:")


def test_fluoro_panel_import_channel_update_and_threshold_are_non_destructive(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "panel_cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")

    image = panel.import_image(image_path)
    uid = image["uid"]
    before = panel.image_data(uid).copy()
    panel.update_channel(uid, 1, display_min=100, display_max=2000, pseudocolor="#00D26A", visible=False)
    mask = panel.apply_threshold(uid, 1, "manual", 1000)

    assert np.array_equal(panel.image_data(uid), before)
    assert panel.active_image()["channels"][1]["display_min"] == 100
    assert mask.sum() > 0
    assert panel.analysis["threshold_masks"][f"{uid}:1"]["positive_pixels"] == int(mask.sum())
    assert any(entry.action == "update_fluorescence_channel" for entry in panel.audit_log)


def test_fluoro_import_uses_nature_image_plate_channel_defaults(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "nature_plate_cells.tif")
    panel = FluorescencePanel(panel_id="Figure1C")

    image = panel.import_image(image_path)

    assert [channel["pseudocolor"] for channel in image["channels"][:3]] == ["#22D7E6", "#FF2AD4", "#FFFFFF"]
    assert image["scale_bar"]["color"] == "#FFFFFF"


def test_fluoro_panel_auto_detects_single_signal_rgb_and_requires_force_split(tmp_path) -> None:
    image_path = _write_rgb_single_signal_tiff(tmp_path / "single_signal_ch01.tif", 0, 186)
    panel = FluorescencePanel(panel_id="Figure1C")

    image = panel.import_image(image_path)
    uid = image["uid"]

    assert panel.image_data(uid).shape == (3, 48, 64)
    assert len(image["channels"]) == 1
    assert image["metadata"]["n_channels"] == 1
    assert image["view_mode"] == "merge"
    assert image["layout_mode"] == "merge_only"
    assert image["channel_interpretation"]["mode"] == "rgb_composite"
    assert image["channel_interpretation"]["source_sample_count"] == 3

    panel._image_cache.pop(uid)
    assert panel.image_data(uid).shape == (3, 48, 64)
    try:
        panel.set_view_mode(uid, "split")
    except ValueError as exc:
        assert "cannot be split" in str(exc)
    else:
        raise AssertionError("Expected ordinary RGB composite mode to reject split view")

    forced = panel.force_split_rgb_samples(uid)

    assert panel.image_data(uid).shape[0] == 3
    assert [channel["name"] for channel in forced["channels"]] == ["Red", "Green", "Blue"]
    assert forced["metadata"]["n_channels"] == 3
    assert forced["view_mode"] == "split"
    assert forced["channel_interpretation"]["mode"] == "forced_rgb_split"

    restored = panel.restore_auto_detected_rgb_signal(uid)

    assert panel.image_data(uid).shape == (3, 48, 64)
    assert len(restored["channels"]) == 1
    assert restored["metadata"]["n_channels"] == 1
    assert restored["view_mode"] == "merge"
    assert restored["layout_mode"] == "merge_only"
    assert restored["channel_interpretation"]["mode"] == "rgb_composite"


def test_fluoro_full_resolution_source_cache_is_byte_bounded_lru_and_reloads_exactly(tmp_path) -> None:
    first_path = _write_single_channel_tiff(tmp_path / "lru_first.tif", 1200)
    second_path = _write_single_channel_tiff(tmp_path / "lru_second.tif", 3600)
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.IMAGE_CACHE_BYTE_BUDGET = 48 * 64 * np.dtype(np.uint16).itemsize

    first = panel.import_image(first_path)
    expected_first = panel.image_data(first["uid"]).copy()
    second = panel.import_image(second_path)

    assert first["uid"] not in panel._image_cache
    assert second["uid"] in panel._image_cache
    reloaded_first = panel.image_data(first["uid"])
    assert reloaded_first is not None
    assert reloaded_first.shape == expected_first.shape
    assert reloaded_first.dtype == expected_first.dtype
    assert np.array_equal(reloaded_first, expected_first)
    assert first["uid"] in panel._image_cache
    assert second["uid"] not in panel._image_cache


def test_fluoro_derived_cache_spills_losslessly_and_cleans_up(tmp_path) -> None:
    first_path = _write_single_channel_tiff(tmp_path / "spill_first.tif", 1200)
    second_path = _write_single_channel_tiff(tmp_path / "spill_second.tif", 3600)
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.IMAGE_CACHE_BYTE_BUDGET = 1
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)

    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])
    expected = np.stack(
        [
            panel.image_data(first["uid"])[0],
            panel.image_data(second["uid"])[0],
        ],
        axis=0,
    )
    spill_path = panel._spilled_image_paths[merged["uid"]]

    assert merged["uid"] not in panel._image_cache
    assert spill_path.is_file()
    restored = panel.image_data(merged["uid"])
    assert restored is not None
    assert restored.shape == expected.shape
    assert restored.dtype == expected.dtype
    assert np.array_equal(restored, expected)

    panel.remove_cached_image_data(merged["uid"])
    assert not spill_path.exists()
    panel.dispose()


def test_fluoro_panel_merges_single_channel_images_as_multichannel_record(tmp_path) -> None:
    first_path = _write_single_channel_tiff(tmp_path / "dapi_ch1.tif", 1200)
    second_path = _write_single_channel_tiff(tmp_path / "fitc_ch1.tif", 3600)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)

    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])

    assert merged["metadata"]["source_format"] == "merged_channels"
    assert merged["metadata"]["n_channels"] == 2
    assert merged["view_mode"] == "merge"
    assert merged["merged_from_images"][0]["uid"] == first["uid"]
    assert [channel["name"] for channel in merged["channels"]] == ["dapi", "fitc"]
    data = panel.image_data(merged["uid"])
    assert data.shape == (2, 48, 64)
    assert int(data[0].max()) == 1200
    assert int(data[1].max()) == 3600


def test_fluoro_panel_merges_rgb_display_images_as_one_channel_each(tmp_path) -> None:
    first_path = _write_rgb_single_signal_tiff(tmp_path / "sma_red_display.tif", 0, 180)
    second_path = _write_rgb_single_signal_tiff(tmp_path / "sma_green_display.tif", 1, 220)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    assert panel.image_data(first["uid"]).shape[0] == 3
    assert first["channel_interpretation"]["mode"] == "rgb_composite"

    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])

    # Display RGB components share the archive raster, but are not analysis
    # channels; scientific measurements still receive one signal per source.
    data = panel.segmentation_input_data(merged["uid"])
    assert data.shape == (2, 48, 64)
    assert int(data[0].max()) == 180
    assert int(data[1].max()) == 220
    assert merged["metadata"]["n_channels"] == 2
    assert merged["merged_from_images"][0]["source_channel_mode"] == "rgb_max_projection"


def test_fluoro_panel_saves_merged_image_as_ome_tiff_with_channel_names(tmp_path) -> None:
    first_path = _write_rgb_single_signal_tiff(tmp_path / "sma_red_display.tif", 0, 180)
    second_path = _write_rgb_single_signal_tiff(tmp_path / "sma_green_display.tif", 1, 220)
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])

    target = panel.save_image_data_as_ome_tiff(merged["uid"], tmp_path / "merged_sma.ome.tif")
    imported = import_fluorescence_image(target)

    assert target.exists()
    assert imported.analysis_data.shape == (2, 48, 64)
    assert imported.metadata["source_format"] == "OME-TIFF"
    assert imported.channel_names == ["sma_red_display", "sma_green_display"]
    assert int(imported.analysis_data[0].max()) == 180
    assert int(imported.analysis_data[1].max()) == 220


def test_fluoro_panel_rejects_true_multichannel_cyx_source_for_merge_as_channels(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "true_multichannel.tif")
    other_path = _write_single_channel_tiff(tmp_path / "single.tif", 1200)
    panel = FluorescencePanel(panel_id="Figure1C")
    true_multichannel = panel.import_image(image_path)
    single = panel.import_image(other_path)

    try:
        panel.merge_images_as_channels([true_multichannel["uid"], single["uid"]])
    except ValueError as exc:
        assert "single-channel source images or RGB single-signal images" in str(exc)
    else:
        raise AssertionError("Expected merge to reject true multichannel CYX source")


def test_fluoro_panel_rejects_merge_when_source_sizes_differ(tmp_path) -> None:
    first_path = _write_single_channel_tiff(tmp_path / "first.tif", 1200, shape=(48, 64))
    second_path = _write_single_channel_tiff(tmp_path / "second.tif", 3600, shape=(50, 64))
    panel = FluorescencePanel(panel_id="Figure1C")
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)

    try:
        panel.merge_images_as_channels([first["uid"], second["uid"]])
    except ValueError as exc:
        assert "same width and height" in str(exc)
    else:
        raise AssertionError("Expected merge to reject mismatched image sizes")


def test_fluoro_roi_shapes_measure_against_analysis_data(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "roi_shapes.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = image["uid"]

    square = panel.add_roi(uid, "square", {"x": 8, "y": 8, "w": 12, "h": 12}, channel_index=0)
    circle = panel.add_roi(uid, "circle", {"cx": 34, "cy": 28, "rx": 8, "ry": 8}, channel_index=1)
    freehand = panel.add_roi(uid, "freehand", {"points": [(42, 10), (58, 10), (58, 30), (42, 30)]}, channel_index=2)

    assert square["measurement"]["area_px"] > 0
    assert circle["measurement"]["integrated_density"] > 0
    assert freehand["measurement"]["mean"] > 0
    assert {roi["type"] for roi in panel.active_image()["rois"]} == {"square", "circle", "freehand"}


def test_fluoro_object_counts_and_overlap_statistics(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "counts.tif")
    panel = FluorescencePanel(panel_id="Figure1C")
    image = panel.import_image(image_path)
    uid = image["uid"]

    counts = panel.count_channel_objects(uid, [0, 1, 2], method="manual", manual_value=100, min_area_px=3)
    sample_counts = panel.count_like_sample(uid, [0], {"x": 8, "y": 8, "w": 14, "h": 12})
    overlap = panel.compute_channel_overlap(uid, [0, 1, 2], method="manual", manual_value=100)
    profile = panel.line_profile(uid, [0, 1], {"x1": 0, "y1": 12, "x2": 63, "y2": 12}, samples=64)

    assert {row["channel_name"] for row in counts} == {"DAPI", "GFP", "RFP"}
    assert sample_counts[0]["method"] == "sample_like"
    assert all(row["count"] >= 1 for row in counts)
    assert len(overlap) == 9
    assert len(profile) == 2
    assert len(profile[0]["distance_px"]) == 64
    assert profile[0]["distance_um"] in (None, [])
    assert profile[0]["distance_unit"] == "px"
    assert any(row["base_channel_name"] == "DAPI" and row["compare_channel_name"] == "DAPI" and row["overlap_percent_of_base"] == 100.0 for row in overlap)


def test_fluoro_sfig_roundtrip_preserves_records_and_source_archive(tmp_path) -> None:
    image_path = _write_multichannel_tiff(tmp_path / "archive_cells.tif")
    project = Project(title="Fluoro Archive")
    panel = FluorescencePanel(panel_id="Figure1C")
    panel.import_image(image_path)
    project.add_panel(panel)
    target = tmp_path / "fluoro.sfig"

    project.save_sfig(target)
    restored = Project.load_sfig(target)

    assert len(restored.panels) == 1
    restored_panel = restored.panels[0]
    assert isinstance(restored_panel, FluorescencePanel)
    assert len(restored_panel.data["fluorescence_images"]) == 1
    assert restored_panel.data["fluorescence_images"][0]["source_name"] == "archive_cells.tif"
    with ZipFile(target) as archive:
        names = set(archive.namelist())
    assert any(name.endswith("/archive_cells.tif") and name.startswith("data/fluorescence/") for name in names)


def test_fluoro_ui_imports_and_exports_scene(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = _write_multichannel_tiff(tmp_path / "ui_cells.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")

    window.import_fluorescence_images([str(image_path)])

    panel = window._active_fluoro_panel()
    assert len(panel.data["fluorescence_images"]) == 1
    assert any(isinstance(item, QGraphicsPixmapItem) for item in window.fluoro_workspace.canvas.scene.items())
    first_color_combo = window.fluoro_workspace._channel_widgets[0][3]
    assert "蓝色" in first_color_combo.itemText(0)
    window.fluoro_workspace.threshold_method_combo.setCurrentText("otsu")
    window.fluoro_workspace._apply_threshold()
    _wait_for_fluoro_analysis(window, "threshold")
    threshold_key = f"{panel.active_image()['uid']}:0"
    assert panel.analysis["threshold_masks"][threshold_key]["method"] == "otsu"
    window.fluoro_workspace.layout_mode_combo.setCurrentText("batch_grid")
    assert panel.active_image()["layout_mode"] == "batch_grid"
    window.fluoro_workspace._plot_overlap()
    _wait_for_fluoro_analysis(window, "overlap-profile")
    assert any(isinstance(item, QGraphicsPathItem) for item in window.fluoro_workspace.canvas.scene.items())
    assert window.fluoro_workspace.width() >= 500
    assert window.fluoro_workspace.canvas.scene.backgroundBrush().color().name().lower() == "#ffffff"
    window.fluoro_workspace.layout_mode_combo.setCurrentText("custom")
    window.fluoro_workspace.layout_add_text_edit.setText("Merged")
    window.fluoro_workspace._add_layout_text()
    text_items = [item for item in window.fluoro_workspace.canvas.selected_text_items() if item.toPlainText() == "Merged"]
    assert text_items
    window.fluoro_workspace.layout_text_size_spin.setValue(14)
    window.fluoro_workspace.layout_text_bold.setChecked(True)
    window.fluoro_workspace.layout_font_combo.setCurrentFont(text_items[0].font())
    window.fluoro_workspace._apply_layout_text_style()
    assert text_items[0].font().bold()
    assert int(text_items[0].font().pointSizeF()) == 14
    target = export_fluoro_figure_scene(window.fluoro_workspace.canvas.scene, tmp_path / "fluoro_export.svg", dpi=300)
    assert target.exists()
    assert target.stat().st_size > 0
    _close_window(window, app)


def test_fluoro_workspace_set_panel_refreshes_canvas_once(monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    workspace = FluorescenceWorkspace()
    panel = FluorescencePanel(panel_id="Figure1C")
    refresh_calls: list[int] = []
    original_refresh = workspace.canvas.refresh

    def capture_refresh() -> None:
        refresh_calls.append(1)
        original_refresh()

    monkeypatch.setattr(workspace.canvas, "refresh", capture_refresh)
    workspace.set_panel(panel)
    assert len(refresh_calls) == 1

    workspace.set_panel(panel)
    assert len(refresh_calls) == 1
    workspace.set_panel(panel, refresh=True)
    assert len(refresh_calls) == 2

    workspace.close()
    workspace.deleteLater()
    app.processEvents()


def test_fluoro_multi_file_import_renders_once_when_panel_already_bound(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first_path = _write_single_channel_tiff(tmp_path / "batch_first.tif", 1000)
    second_path = _write_single_channel_tiff(tmp_path / "batch_second.tif", 2000)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    workspace = window.fluoro_workspace
    refresh_calls: list[int] = []

    def fail_critical(_parent, title, message):
        raise AssertionError(f"{title}: {message}")

    def capture_refresh() -> None:
        refresh_calls.append(1)

    monkeypatch.setattr(main_window_module.QMessageBox, "critical", fail_critical)
    monkeypatch.setattr(workspace.canvas, "refresh", capture_refresh)
    window.import_fluorescence_images([str(first_path), str(second_path)])

    assert len(refresh_calls) == 1
    assert len(window._active_fluoro_panel().images()) == 2
    assert workspace.canvas.current_step == 1
    _close_window(window, app)


def test_fluoro_channel_display_updates_coalesce_without_changing_render_input(tmp_path, monkeypatch) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = _write_multichannel_tiff(tmp_path / "debounced_channels.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    workspace = window.fluoro_workspace
    panel = window._active_fluoro_panel()
    image = panel.active_image()
    assert image is not None
    image_uid = str(image["uid"])
    first_visible, first_min, first_max, color_combo = workspace._channel_widgets[0]
    refresh_calls: list[int] = []
    render_inputs: list[object] = []
    original_refresh = workspace.canvas.refresh
    original_render_merge = fluoro_canvas_module.render_merge_qimage

    def capture_refresh() -> None:
        refresh_calls.append(1)
        original_refresh()

    def capture_render(data, channels):
        render_inputs.append(data)
        return original_render_merge(data, channels)

    monkeypatch.setattr(workspace.canvas, "refresh", capture_refresh)
    monkeypatch.setattr(fluoro_canvas_module, "render_merge_qimage", capture_render)

    for value in (10.0, 20.0, 30.0):
        first_min.setValue(value)
        assert panel.active_image()["channels"][0]["display_min"] == value
    for value in (1000.0, 2000.0, 3000.0):
        first_max.setValue(value)
        assert panel.active_image()["channels"][0]["display_max"] == value
    color_combo.setCurrentIndex(color_combo.findData("#FF3333"))
    assert panel.active_image()["channels"][0]["pseudocolor"] == "#FF3333"
    first_visible.setChecked(False)
    assert panel.active_image()["channels"][0]["visible"] is False
    assert refresh_calls == []

    QTest.qWait(workspace.CHANNEL_REFRESH_DEBOUNCE_MS + 25)

    assert refresh_calls == []  # Display edits must not rebuild/reposition the scene.
    assert len(render_inputs) == 1
    assert render_inputs[-1] is panel.image_data(image_uid)
    _close_window(window, app)


def test_fluoro_ui_merges_selected_images_as_channels(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first_path = _write_single_channel_tiff(tmp_path / "red_ch1.tif", 1000)
    second_path = _write_single_channel_tiff(tmp_path / "green_ch1.tif", 2000)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("fluoro")
    window.import_fluorescence_images([str(first_path), str(second_path)])
    workspace = window.fluoro_workspace
    assert workspace.merge_image_list.count() == 2
    for index in range(workspace.merge_image_list.count()):
        workspace.merge_image_list.item(index).setSelected(True)
    assert workspace.merge_selected_button.isEnabled()

    workspace.merge_selected_button.click()
    app.processEvents()

    panel = window._active_fluoro_panel()
    assert len(panel.images()) == 3
    merged = panel.active_image()
    assert merged["metadata"]["source_format"] == "merged_channels"
    assert merged["metadata"]["n_channels"] == 2
    assert workspace.context_stack.currentIndex() == 1
    assert workspace.active_image_combo.currentData() == merged["uid"]
    assert panel.image_data(merged["uid"]).shape == (2, 48, 64)
    _close_window(window, app)


def test_fluoro_ui_removes_spilled_derived_image_and_preserves_active_selection(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first_path = _write_single_channel_tiff(tmp_path / "ui_remove_first.tif", 1200)
    second_path = _write_single_channel_tiff(tmp_path / "ui_remove_second.tif", 3600)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    panel.IMAGE_CACHE_BYTE_BUDGET = 1
    window.import_fluorescence_images([str(first_path), str(second_path)])

    workspace = window.fluoro_workspace
    for index in range(workspace.merge_image_list.count()):
        workspace.merge_image_list.item(index).setSelected(True)
    workspace.merge_selected_button.click()
    app.processEvents()

    merged = panel.active_image()
    assert merged is not None
    merged_uid = str(merged["uid"])
    spill_path = panel._spilled_image_paths[merged_uid]
    assert spill_path.is_file()
    assert workspace.active_image_combo.currentData() == merged_uid

    workspace.remove_button.click()
    app.processEvents()

    assert not spill_path.exists()
    assert all(str(image.get("uid", "")) != merged_uid for image in panel.images())
    assert panel.data["active_image_uid"] == panel.images()[0]["uid"]
    assert workspace.active_image_combo.currentData() == panel.images()[0]["uid"]
    assert any(entry.action == "remove_fluorescence_image" and entry.detail == merged_uid for entry in panel.audit_log)
    _close_window(window, app)


def test_fluoro_ui_force_split_button_restores_rgb_sample_channels(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = _write_rgb_single_signal_tiff(tmp_path / "single_signal_ch01.tif", 0, 186)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    workspace = window.fluoro_workspace
    panel = window._active_fluoro_panel()

    assert len(panel.active_image()["channels"]) == 1
    assert workspace._channel_widgets == []
    assert not workspace.view_mode_combo.isEnabled()
    assert workspace.force_split_rgb_button.isEnabled()

    workspace.force_split_rgb_button.click()
    app.processEvents()

    assert len(panel.active_image()["channels"]) == 3
    assert len(workspace._channel_widgets) == 3
    assert workspace.view_mode_combo.isEnabled()
    assert workspace.view_mode_combo.currentText() == "split"
    assert workspace.force_split_rgb_button.isEnabled()
    assert workspace.force_split_rgb_button.text() == "Restore Original Composite"

    workspace.force_split_rgb_button.click()
    app.processEvents()

    assert len(panel.active_image()["channels"]) == 1
    assert workspace._channel_widgets == []
    assert not workspace.view_mode_combo.isEnabled()
    assert workspace.view_mode_combo.currentText() == "merge"
    assert workspace.force_split_rgb_button.isEnabled()
    assert workspace.force_split_rgb_button.text() == "Force Split RGB Channels"
    _close_window(window, app)


def test_fluoro_ui_saves_active_merged_image_and_preserves_channel_controls(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    first_path = _write_rgb_single_signal_tiff(tmp_path / "sma_red_display.tif", 0, 180)
    second_path = _write_rgb_single_signal_tiff(tmp_path / "sma_green_display.tif", 1, 220)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")

    window.set_module("fluoro")
    window.import_fluorescence_images([str(first_path), str(second_path)])
    workspace = window.fluoro_workspace
    for index in range(workspace.merge_image_list.count()):
        workspace.merge_image_list.item(index).setSelected(True)
    workspace.merge_selected_button.click()
    app.processEvents()

    panel = window._active_fluoro_panel()
    merged = panel.active_image()
    assert merged is not None
    assert len(merged["channels"]) == 2
    channel_titles = [
        workspace.channel_layout.itemAt(index).widget().title()
        for index in range(workspace.channel_layout.count())
        if isinstance(workspace.channel_layout.itemAt(index).widget(), QGroupBox)
    ]
    assert channel_titles == [
        "sma_red_display",
        "sma_green_display",
    ]
    target = window.save_active_fluorescence_image(str(tmp_path / "active_merged.ome.tif"))
    imported = import_fluorescence_image(target)

    assert target is not None
    assert imported.analysis_data.shape == (2, 48, 64)
    assert imported.channel_names == ["sma_red_display", "sma_green_display"]
    _close_window(window, app)


def test_fluoro_channel_pseudocolor_selection_updates_canvas_preview(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = _write_multichannel_tiff(tmp_path / "channel_color_preview.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])

    first_visible, first_min, first_max, color_combo = window.fluoro_workspace._channel_widgets[0]
    first_min.setValue(0)
    first_max.setValue(1200)
    for visible, _min_spin, _max_spin, _color_combo in window.fluoro_workspace._channel_widgets[1:]:
        visible.setChecked(False)
    color_combo.setCurrentIndex(color_combo.findData("#FF3333"))

    panel = window._active_fluoro_panel()
    assert panel.active_image()["channels"][0]["pseudocolor"] == "#FF3333"
    deadline = time.monotonic() + 1.0
    colors: set[str] = set()
    while time.monotonic() < deadline:
        app.processEvents()
        if window.fluoro_workspace.canvas._image_items:
            preview = window.fluoro_workspace.canvas._image_items[0].pixmap().toImage()
            colors = {preview.pixelColor(x, y).name().upper() for y in range(preview.height()) for x in range(preview.width())}
            if "#FF3333" in colors:
                break
        QTest.qWait(10)
    assert "#FF3333" in colors
    assert "#22D7E6" not in colors

    _close_window(window, app)


def test_fluoro_ai_assist_mode_runs_local_heuristic_and_populates_results(tmp_path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    image_path = _write_multichannel_tiff(tmp_path / "ai_assist_entry.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])

    workspace = window.fluoro_workspace
    workspace._set_measure_mode("ai_assisted")

    assert workspace.measure_params_stack.currentWidget() is workspace.ai_assisted_params_panel
    assert "Cell Area" in workspace.ai_assisted_group.title()
    assert workspace.ai_run_button.isEnabled()
    assert workspace.ai_channel_list.selectedItems()

    workspace.ai_run_button.click()
    _wait_for_fluoro_analysis(window, "ai")

    panel = window._active_fluoro_panel()
    records = panel.analysis.get("fluorescence_ai_assisted_measurements", [])
    assert len(records) == 3
    assert all(record["metadata"]["model_execution_mode"] == "local_heuristic" for record in records)
    assert "completed" in workspace.ai_status_label.text().lower()
    total_objects = sum(int(record["ai_object_count"]) for record in records)
    assert workspace.ai_results_table.rowCount() == total_objects
    detail_headers = [
        workspace.ai_results_table.horizontalHeaderItem(column).text()
        for column in range(workspace.ai_results_table.columnCount())
    ]
    from core.utils.i18n import tr
    assert detail_headers[:7] == [tr(key) for key in ('measurement_object_id', 'measurement_channel', 'area_px', 'area_um2', 'measurement_centroid_x', 'measurement_centroid_y', 'mean_intensity')]
    detail_areas = [
        int(workspace.ai_results_table.item(row, detail_headers.index(tr('area_px'))).text())
        for row in range(workspace.ai_results_table.rowCount())
    ]
    expected_areas = [
        int(area)
        for record in records
        for area in record.get("object_area_distribution_px", [])
    ]
    assert sorted(detail_areas) == sorted(expected_areas)
    assert all(
        detected_object.get("contour_points_px")
        for record in records
        for detected_object in record.get("object_table", [])
    )

    assert workspace.ai_summary_stats_table.rowCount() == len(records)
    summary_headers = [
        workspace.ai_summary_stats_table.horizontalHeaderItem(column).text()
        for column in range(workspace.ai_summary_stats_table.columnCount())
    ]
    assert tr('median_area_px') in summary_headers
    assert tr('measurement_median_area_um2') in summary_headers
    assert all(
        workspace.ai_summary_stats_table.item(row, summary_headers.index(tr('median_area_px'))).text()
        for row in range(workspace.ai_summary_stats_table.rowCount())
    )

    overlay_items = [
        item for item in workspace.canvas.scene.items()
        if isinstance(item, QGraphicsPathItem) and item.data(0) == "fluoro_ai_object_overlay"
    ]
    assert len(overlay_items) == total_objects
    assert all(not item.path().boundingRect().isNull() for item in overlay_items)
    assert workspace.ai_overlay_preview_button.isEnabled()
    assert workspace.ai_overlay_preview_button.isChecked()
    workspace.ai_original_preview_button.click()
    app.processEvents()
    assert all(not item.isVisible() for item in overlay_items)
    workspace.ai_overlay_preview_button.click()
    app.processEvents()
    assert all(item.isVisible() for item in overlay_items)

    window.switch_language("en")
    app.processEvents()
    translated_overlay_items = [
        item for item in workspace.canvas.scene.items()
        if isinstance(item, QGraphicsPathItem) and item.data(0) == "fluoro_ai_object_overlay"
    ]
    assert len(translated_overlay_items) == total_objects
    assert workspace.ai_overlay_preview_button.isEnabled()
    assert workspace.ai_overlay_preview_button.isChecked()

    _close_window(window, app)


def test_fluoro_czi_nd2_suffixes_are_supported() -> None:
    import czifile  # noqa: F401

    assert ".czi" in SUPPORTED_FLUORESCENCE_SUFFIXES
    assert ".nd2" in SUPPORTED_FLUORESCENCE_SUFFIXES
