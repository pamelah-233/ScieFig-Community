from __future__ import annotations

import os
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import numpy as np
import tifffile
from PyQt6.QtCore import QCoreApplication, QEvent, QThread, QTimer
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from core.models.brightfield_panel import BrightfieldPanel
from core.models.fluoro_panel import FluorescencePanel
from core.models.ihc_panel import IHCPanel
import core.image_analysis.io_engine as io_engine_module
import core.models.brightfield_panel as brightfield_panel_module
import core.models.fluoro_panel as fluoro_panel_module
from ui.controllers.analysis_job_service import AnalysisJobService
from ui.controllers.brightfield_traditional_controller import BrightfieldTraditionalAnalysisController
from ui.controllers.ihc_analysis_controller import IHCAnalysisController
import ui.main_window as main_window_module
from ui.main_window import MainWindow
from ui.panels.fluorescence_workspace import FluorescenceWorkspace


_QT_APP: QApplication | None = None


def _app() -> QApplication:
    global _QT_APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _QT_APP = QApplication.instance() or QApplication([])
    return _QT_APP


def test_app_helper_retains_qapplication_for_module_lifetime() -> None:
    app = _app()
    assert globals().get("_QT_APP") is app


def _wait_until(app: QApplication, predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        app.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for analysis completion")
        time.sleep(0.005)
    app.processEvents()


def _wait_for_import(app: QApplication, window: MainWindow, module_id: str, timeout: float = 15.0) -> None:
    _wait_until(app, lambda: not window._analysis_job_service.is_busy(f"import:{module_id}"), timeout=timeout)


def _wait_for_fluoro_analysis(
    app: QApplication,
    window: MainWindow,
    operation: str,
    timeout: float = 15.0,
) -> None:
    key = f"fluoro-analysis:{operation}"
    _wait_until(app, lambda: not window._analysis_job_service.is_busy(key), timeout=timeout)


def _close_window(app: QApplication, window: MainWindow) -> None:
    window._clean_project_fingerprint = window._project_fingerprint()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _image_file(path: Path) -> Path:
    image = QImage(12, 12, QImage.Format.Format_RGB32)
    for y in range(image.height()):
        for x in range(image.width()):
            image.setPixelColor(x, y, QColor(190 + (x % 3) * 10, 90 + (y % 4) * 8, 110 + ((x + y) % 3) * 7))
    assert image.save(str(path))
    return path


def _uint16_multichannel_file(path: Path) -> Path:
    data = np.zeros((2, 12, 16), dtype=np.uint16)
    data[0, 2:7, 3:9] = 1025
    data[1, 6:11, 8:14] = 49152
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _uint16_single_channel_file(path: Path, value: int) -> Path:
    data = np.zeros((1, 12, 16), dtype=np.uint16)
    data[0, 2:10, 3:13] = value
    tifffile.imwrite(path, data, metadata={"axes": "CYX"}, photometric="minisblack")
    return path


def _high_depth_rgb_file(path: Path, dtype) -> Path:
    ramp = np.arange(16 * 20, dtype=np.float64).reshape(16, 20) / float(16 * 20 - 1)
    if dtype is np.uint16:
        red = np.rint(ramp * np.iinfo(np.uint16).max).astype(np.uint16)
        green = np.full_like(red, 32768)
        blue = np.iinfo(np.uint16).max - red
    else:
        red = ramp.astype(np.float32)
        green = np.full_like(red, 0.5)
        blue = 1.0 - red
    data = np.stack([red, green, blue], axis=-1)
    tifffile.imwrite(path, data, metadata={"axes": "YXS"}, photometric="rgb")
    return path


def _ihc_panel() -> IHCPanel:
    panel = IHCPanel(panel_id="IHC")
    panel.data["ihc_images"] = [{"uid": "image-1", "source_name": "image.png", "measurements": []}]
    panel.data["active_image_uid"] = "image-1"
    return panel


def _brightfield_panel() -> BrightfieldPanel:
    panel = BrightfieldPanel(panel_id="Brightfield")
    panel.data["brightfield_images"] = [{"uid": "image-1", "source_name": "image.png", "stain_type": "H&E", "measurements": []}]
    panel.data["active_image_uid"] = "image-1"
    return panel


@pytest.mark.parametrize("kind", ["ihc", "brightfield"])
def test_async_import_decodes_once_and_hands_off_exact_preview(monkeypatch, tmp_path, kind: str) -> None:
    app = _app()
    image_path = _image_file(tmp_path / f"{kind}-preview.png")
    sync_panel = IHCPanel(panel_id="sync") if kind == "ihc" else BrightfieldPanel(panel_id="sync")
    sync_record = sync_panel.import_image(image_path)
    expected_preview = sync_panel.source_qimage(sync_record["uid"])

    decode_calls: list[Path] = []
    original_read = io_engine_module.read_microscopy_image

    def counted_read(path, *args, **kwargs):
        decode_calls.append(Path(path))
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(io_engine_module, "read_microscopy_image", counted_read)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module(kind)
    previews: list[QImage] = []
    refresh_name = "_refresh_ihc_preview" if kind == "ihc" else "_refresh_brightfield_preview"
    original_refresh = getattr(window, refresh_name)

    def capture_preview(*args, **kwargs):
        preview = kwargs.get("preview")
        if preview is not None:
            previews.append(preview)
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(window, refresh_name, capture_preview)
    window.import_ihc_images([str(image_path)]) if kind == "ihc" else window.import_brightfield_images([str(image_path)])
    _wait_for_import(app, window, kind)

    assert decode_calls == [image_path]
    assert previews
    actual_preview = previews[-1]
    assert (actual_preview.width(), actual_preview.height()) == (expected_preview.width(), expected_preview.height())
    for x, y in ((0, 0), (3, 4), (11, 11)):
        assert actual_preview.pixelColor(x, y).getRgb() == expected_preview.pixelColor(x, y).getRgb()

    live_panel = window._active_ihc_panel() if kind == "ihc" else window._active_brightfield_panel()
    live_record = live_panel.active_image()
    assert live_record is not None
    for key in sync_record:
        if key not in {"uid", "imported_at"}:
            assert live_record[key] == sync_record[key]
    _close_window(app, window)


def test_async_fluorescence_import_preserves_uint16_pixels_metadata_and_hash(tmp_path) -> None:
    app = _app()
    image_path = _uint16_multichannel_file(tmp_path / "async-fluoro.ome.tif")
    sync_panel = FluorescencePanel(panel_id="sync")
    sync_record = sync_panel.import_image(image_path)
    expected_pixels = sync_panel.image_data(sync_record["uid"]).copy()

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    window.import_fluorescence_images([str(image_path)])
    _wait_for_import(app, window, "fluoro")
    live_panel = window._active_fluoro_panel()
    live_record = live_panel.active_image()
    actual_pixels = live_panel.image_data(live_record["uid"])

    assert live_record is not None
    assert actual_pixels is not None
    assert actual_pixels.dtype == expected_pixels.dtype == np.uint16
    assert actual_pixels.shape == expected_pixels.shape == (2, 12, 16)
    assert np.array_equal(actual_pixels, expected_pixels)
    assert live_record["source_hash"] == sync_record["source_hash"]
    assert live_record["metadata"] == sync_record["metadata"]
    assert live_record["channels"] == sync_record["channels"]
    assert live_record["analysis_data_shape"] == sync_record["analysis_data_shape"]
    assert live_record["analysis_dtype"] == sync_record["analysis_dtype"]
    _close_window(app, window)


def test_async_fluorescence_import_preserves_cache_only_merge_and_adopts_new_cache_by_reference(
    monkeypatch,
    tmp_path,
) -> None:
    app = _app()
    first_path = _uint16_single_channel_file(tmp_path / "existing-dapi.tif", 1200)
    second_path = _uint16_single_channel_file(tmp_path / "existing-fitc.tif", 3600)
    new_path = _uint16_multichannel_file(tmp_path / "new-async-fluoro.ome.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    panel = window._active_fluoro_panel()
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])
    merged_cache = panel.image_data(merged["uid"])
    assert merged["source_path"] == ""
    assert merged_cache is not None
    expected_merged = merged_cache.copy()

    adopted: dict[str, np.ndarray] = {}
    original_adopt = FluorescencePanel.adopt_imported_cache

    def capture_adopt(self, source_panel, image_uids):
        imported_uid = image_uids[0]
        adopted["worker"] = source_panel._image_cache[imported_uid]
        original_adopt(self, source_panel, image_uids)
        adopted["live"] = self._image_cache[imported_uid]

    monkeypatch.setattr(FluorescencePanel, "adopt_imported_cache", capture_adopt)
    window.set_module("fluoro")
    window.import_fluorescence_images([str(new_path)])
    _wait_for_import(app, window, "fluoro")

    new_record = panel.active_image()
    preserved_merged = panel.image_data(merged["uid"])
    assert new_record is not None
    assert preserved_merged is merged_cache
    assert np.array_equal(preserved_merged, expected_merged)
    assert adopted["worker"] is adopted["live"]
    assert panel.image_data(new_record["uid"]) is adopted["worker"]
    _close_window(app, window)


def test_async_fluorescence_oversize_uses_live_owned_spill_without_redecode(
    monkeypatch,
    tmp_path,
) -> None:
    app = _app()
    first_path = _uint16_single_channel_file(tmp_path / "spill-existing-dapi.tif", 1200)
    second_path = _uint16_single_channel_file(tmp_path / "spill-existing-fitc.tif", 3600)
    new_path = _uint16_multichannel_file(tmp_path / "spill-new-async.ome.tif")
    expected_new = np.zeros((2, 12, 16), dtype=np.uint16)
    expected_new[0, 2:7, 3:9] = 1025
    expected_new[1, 6:11, 8:14] = 49152

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    merged = panel.merge_images_as_channels([first["uid"], second["uid"]])
    expected_merged = panel.image_data(merged["uid"]).copy()
    panel.IMAGE_CACHE_BYTE_BUDGET = 1
    panel._trim_image_cache()
    merged_spill = panel._spilled_image_paths[merged["uid"]]
    assert merged_spill.is_file()

    gui_thread_id = int(QThread.currentThreadId())
    importer_threads: list[int] = []
    refresh_threads: list[int] = []
    original_import = fluoro_panel_module.import_fluorescence_image
    original_refresh = window.fluoro_workspace._refresh_canvas

    def counted_import(path):
        importer_threads.append(int(QThread.currentThreadId()))
        return original_import(path)

    def capture_refresh():
        refresh_threads.append(int(QThread.currentThreadId()))
        return original_refresh()

    monkeypatch.setattr(fluoro_panel_module, "import_fluorescence_image", counted_import)
    monkeypatch.setattr(window.fluoro_workspace, "_refresh_canvas", capture_refresh)
    window.import_fluorescence_images([str(new_path)])
    _wait_for_import(app, window, "fluoro")

    imported = panel.active_image()
    assert imported is not None
    imported_uid = str(imported["uid"])
    imported_spill = panel._spilled_image_paths[imported_uid]
    imported_pixels = panel.image_data(imported_uid)
    merged_pixels = panel.image_data(merged["uid"])
    window.fluoro_workspace._refresh_canvas()

    assert len(importer_threads) == 1
    assert importer_threads[0] != gui_thread_id
    assert refresh_threads and all(thread_id == gui_thread_id for thread_id in refresh_threads)
    assert isinstance(imported_pixels, np.memmap)
    assert imported_pixels.dtype == np.uint16
    assert imported_pixels.shape == expected_new.shape
    assert np.array_equal(imported_pixels, expected_new)
    assert np.array_equal(merged_pixels, expected_merged)
    assert merged_spill == panel._spilled_image_paths[merged["uid"]]
    assert merged_spill.is_file()
    assert imported_spill.is_file()
    assert imported_spill.parent == Path(str(panel._spill_directory.name))
    assert panel._mapped_image_data[imported_uid] is imported_pixels
    assert sum(int(array.nbytes) for array in panel._image_cache.values()) <= panel.IMAGE_CACHE_BYTE_BUDGET

    _close_window(app, window)
    assert not imported_spill.exists()
    assert not merged_spill.exists()


def test_async_import_keeps_heartbeat_alive_runs_off_gui_and_restores_controls(monkeypatch, tmp_path) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "slow-ihc.png")
    started = threading.Event()
    release = threading.Event()
    worker_thread_ids: list[int] = []
    calls: list[str] = []
    heartbeat: list[float] = []
    refresh_thread_ids: list[int] = []
    original_import = IHCPanel.import_image_from_decoded

    def slow_import(self, path, imported, group="Ungrouped"):
        calls.append(str(path))
        worker_thread_ids.append(int(QThread.currentThreadId()))
        started.set()
        release.wait(1.0)
        return original_import(self, path, imported, group=group)

    monkeypatch.setattr(IHCPanel, "import_image_from_decoded", slow_import)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("ihc")
    original_refresh = window._refresh_ihc_preview

    def capture_refresh(*args, **kwargs):
        refresh_thread_ids.append(int(QThread.currentThreadId()))
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(window, "_refresh_ihc_preview", capture_refresh)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
    timer.start()
    gui_thread_id = int(QThread.currentThreadId())

    window.import_ihc_images([str(image_path)])
    _wait_until(app, started.is_set)
    window.import_ihc_images([str(image_path)])
    assert not window.ihc_workspace.import_button.isEnabled()
    assert not window.ihc_workspace.remove_button.isEnabled()
    deadline = time.monotonic() + 0.12
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    release.set()
    _wait_for_import(app, window, "ihc")
    timer.stop()

    assert len(heartbeat) >= 5
    assert len(calls) == 1
    assert worker_thread_ids and worker_thread_ids[0] != gui_thread_id
    assert refresh_thread_ids and refresh_thread_ids[-1] == gui_thread_id
    assert window.ihc_workspace.import_button.isEnabled()
    assert window.ihc_workspace.remove_button.isEnabled()
    assert len(window._active_ihc_panel().images()) == 1
    _close_window(app, window)


@pytest.mark.parametrize("mutation", ["edit", "project"])
def test_async_import_rejects_stale_edit_or_project(monkeypatch, tmp_path, mutation: str) -> None:
    app = _app()
    image_path = _image_file(tmp_path / f"stale-{mutation}.png")
    started = threading.Event()
    release = threading.Event()
    original_import = IHCPanel.import_image_from_decoded

    def slow_import(self, path, imported, group="Ungrouped"):
        started.set()
        release.wait(1.0)
        return original_import(self, path, imported, group=group)

    monkeypatch.setattr(IHCPanel, "import_image_from_decoded", slow_import)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("ihc")
    original_panel = window._active_ihc_panel()
    window.import_ihc_images([str(image_path)])
    _wait_until(app, started.is_set)
    if mutation == "edit":
        original_panel.data["edited_while_running"] = True
    else:
        window.new_project()
    release.set()
    _wait_for_import(app, window, "ihc")

    assert original_panel.images() == []
    assert window._active_ihc_panel().images() == []
    assert "Discarded" in window.statusBar().currentMessage()
    _close_window(app, window)


def test_async_import_survives_visible_module_switch_when_project_and_panel_are_unchanged(
    monkeypatch,
    tmp_path,
) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "module-switch-ihc.png")
    started = threading.Event()
    release = threading.Event()
    original_import = IHCPanel.import_image_from_decoded

    def slow_import(self, path, imported, group="Ungrouped"):
        started.set()
        assert release.wait(10.0)
        return original_import(self, path, imported, group=group)

    monkeypatch.setattr(IHCPanel, "import_image_from_decoded", slow_import)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("ihc")
    panel = window._active_ihc_panel()
    window._active_brightfield_panel()
    project = window.project
    project_fingerprint = window._microscopy_import_project_fingerprint(project)

    window.import_ihc_images([str(image_path)])
    _wait_until(app, started.is_set)
    window.set_module("brightfield")
    assert window.project is project
    assert window._microscopy_import_project_fingerprint(project) == project_fingerprint
    release.set()
    _wait_for_import(app, window, "ihc")

    assert window.active_module == "brightfield"
    assert [record["source_name"] for record in panel.images()] == [image_path.name]
    assert "Discarded" not in window.statusBar().currentMessage()
    _close_window(app, window)


def test_async_import_partial_success_preserves_prior_records_and_reports_failure(monkeypatch, tmp_path) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "partial-success.png")
    missing_path = tmp_path / "missing.png"
    messages: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: messages.append(str(message)),
    )
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("brightfield")
    window.import_brightfield_images([str(image_path), str(missing_path)])
    _wait_for_import(app, window, "brightfield")

    panel = window._active_brightfield_panel()
    assert [record["source_name"] for record in panel.images()] == [image_path.name]
    assert messages
    assert str(missing_path) in messages[-1]
    _close_window(app, window)


@pytest.mark.parametrize("dtype", [np.uint8, np.uint16])
def test_microscopy_preview_preserves_gray_alpha_and_rgba_channels(dtype) -> None:
    gray = np.array([[1, 2], [3, 4]], dtype=dtype)
    alpha = np.array([[5, 6], [7, 8]], dtype=dtype)
    red = np.array([[11, 12], [13, 14]], dtype=dtype)
    green = np.array([[21, 22], [23, 24]], dtype=dtype)
    blue = np.array([[31, 32], [33, 34]], dtype=dtype)
    imported_two = SimpleNamespace(analysis_data=np.stack([gray, alpha], axis=0))
    imported_four = SimpleNamespace(analysis_data=np.stack([red, green, blue, alpha], axis=0))

    gray_alpha_image = MainWindow._qimage_from_microscopy_result(imported_two)
    rgba_image = MainWindow._qimage_from_microscopy_result(imported_four)
    dtype_max = float(np.iinfo(dtype).max)

    assert (gray_alpha_image.width(), gray_alpha_image.height()) == (2, 2)
    assert (rgba_image.width(), rgba_image.height()) == (2, 2)
    assert gray_alpha_image.pixelColor(0, 1).alphaF() == pytest.approx(float(alpha[1, 0]) / dtype_max, abs=1e-5)
    assert gray_alpha_image.pixelColor(0, 1).redF() == pytest.approx(float(gray[1, 0]) / dtype_max, abs=1e-5)
    assert rgba_image.pixelColor(0, 1).alphaF() == pytest.approx(float(alpha[1, 0]) / dtype_max, abs=1e-5)
    assert rgba_image.pixelColor(0, 1).redF() == pytest.approx(float(red[1, 0]) / dtype_max, abs=1e-5)
    assert rgba_image.pixelColor(0, 1).greenF() == pytest.approx(float(green[1, 0]) / dtype_max, abs=1e-5)
    assert rgba_image.pixelColor(0, 1).blueF() == pytest.approx(float(blue[1, 0]) / dtype_max, abs=1e-5)
    assert np.array_equal(imported_four.analysis_data[3], alpha)


def test_microscopy_float_preview_conversion_is_deterministic_and_non_destructive() -> None:
    source = np.array([[[0.0, 0.25], [0.5, 1.0]]], dtype=np.float32)
    original = source.copy()
    imported = SimpleNamespace(analysis_data=source)
    first = MainWindow._qimage_from_microscopy_result(imported)
    second = MainWindow._qimage_from_microscopy_result(imported)

    assert first.size() == second.size()
    assert first.bits().asstring(first.sizeInBytes()) == second.bits().asstring(second.sizeInBytes())
    assert np.array_equal(source, original)


@pytest.mark.parametrize("dtype", [np.uint16, np.float32])
def test_microscopy_high_depth_preview_matches_source_qimage_without_mutating_decode(tmp_path, dtype) -> None:
    image_path = _high_depth_rgb_file(tmp_path / f"preview-{np.dtype(dtype).name}.tif", dtype)
    panel = IHCPanel(panel_id="sync-preview")
    record = panel.import_image(image_path)
    expected = panel.source_qimage(record["uid"])
    imported = io_engine_module.read_microscopy_image(image_path)
    decoded_before = imported.analysis_data.copy()

    actual = MainWindow._qimage_from_microscopy_result(imported)

    assert actual.size() == expected.size()
    for x, y in ((0, 0), (10, 8), (19, 15)):
        assert actual.pixelColor(x, y).getRgbF() == pytest.approx(
            expected.pixelColor(x, y).getRgbF(),
            abs=2.0 / np.iinfo(np.uint16).max,
        )
    assert np.array_equal(imported.analysis_data, decoded_before)


def test_fluoro_threshold_async_source_cache_miss_keeps_heartbeat_and_adopts_decode(
    monkeypatch,
    tmp_path,
) -> None:
    app = _app()
    image_path = _uint16_multichannel_file(tmp_path / "threshold-source.ome.tif")
    sync_panel = FluorescencePanel(panel_id="sync-threshold")
    sync_record = sync_panel.import_image(image_path)
    sync_panel.apply_threshold(str(sync_record["uid"]), 0, "manual", 1000.0)

    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    record = panel.import_image(image_path)
    image_uid = str(record["uid"])
    panel.remove_cached_image_data(image_uid)
    window.fluoro_workspace.set_panel(panel)
    workspace = window.fluoro_workspace
    workspace.threshold_method_combo.setCurrentText("manual")
    workspace.threshold_value_spin.setValue(1000.0)

    gui_thread = int(QThread.currentThreadId())
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    decode_threads: list[int] = []
    refresh_threads: list[int] = []
    heartbeat: list[float] = []
    original_apply = FluorescencePanel.apply_threshold
    original_import = fluoro_panel_module.import_fluorescence_image
    original_refresh = workspace.refresh

    def slow_apply(self, *args, **kwargs):
        worker_threads.append(int(QThread.currentThreadId()))
        started.set()
        assert release.wait(10.0)
        return original_apply(self, *args, **kwargs)

    def counted_import(path):
        decode_threads.append(int(QThread.currentThreadId()))
        return original_import(path)

    def counted_refresh(*args, **kwargs):
        refresh_threads.append(int(QThread.currentThreadId()))
        return original_refresh(*args, **kwargs)

    monkeypatch.setattr(FluorescencePanel, "apply_threshold", slow_apply)
    monkeypatch.setattr(fluoro_panel_module, "import_fluorescence_image", counted_import)
    monkeypatch.setattr(workspace, "refresh", counted_refresh)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: heartbeat.append(time.monotonic()))
    timer.start()

    workspace._apply_threshold()
    _wait_until(app, started.is_set)
    workspace._apply_threshold()
    assert not workspace.apply_threshold_button.isEnabled()
    deadline = time.monotonic() + 0.12
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    release.set()
    _wait_for_fluoro_analysis(app, window, "threshold")
    timer.stop()

    live_record = panel.active_image()
    assert live_record is not None
    assert len(worker_threads) == 1
    assert worker_threads[0] != gui_thread
    assert decode_threads == [worker_threads[0]]
    assert len(heartbeat) >= 5
    assert refresh_threads == [gui_thread]
    assert workspace.apply_threshold_button.isEnabled()
    assert live_record["thresholds"] == sync_panel.active_image()["thresholds"]
    assert panel.analysis["threshold_masks"][f"{image_uid}:0"] == next(
        iter(sync_panel.analysis["threshold_masks"].values())
    )
    assert panel.image_data(image_uid) is not None
    assert len(decode_threads) == 1
    sync_panel.dispose()
    _close_window(app, window)


def test_fluoro_async_stale_edit_discards_and_exception_restores_controls(monkeypatch, tmp_path) -> None:
    app = _app()
    image_path = _uint16_multichannel_file(tmp_path / "stale-count.ome.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    panel.import_image(image_path)
    workspace = window.fluoro_workspace
    workspace.set_panel(panel)
    started = threading.Event()
    release = threading.Event()
    original_count = FluorescencePanel.count_channel_objects

    def slow_count(self, *args, **kwargs):
        started.set()
        assert release.wait(10.0)
        return original_count(self, *args, **kwargs)

    monkeypatch.setattr(FluorescencePanel, "count_channel_objects", slow_count)
    workspace._count_spots()
    _wait_until(app, started.is_set)
    panel.data["edited_while_counting"] = True
    release.set()
    _wait_for_fluoro_analysis(app, window, "count")

    assert panel.analysis.get("object_counts", []) == []
    assert panel.data["edited_while_counting"] is True
    assert "Discarded fluorescence analysis" in window.statusBar().currentMessage()
    assert workspace.count_button.isEnabled()

    messages: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "critical",
        lambda _parent, _title, message: messages.append(str(message)),
    )

    def failed_count(self, *args, **kwargs):
        raise RuntimeError("controlled fluorescence failure")

    monkeypatch.setattr(FluorescencePanel, "count_channel_objects", failed_count)
    workspace._count_spots()
    _wait_for_fluoro_analysis(app, window, "count")
    assert messages and "controlled fluorescence failure" in messages[-1]
    assert workspace.count_button.isEnabled()
    assert panel.analysis.get("object_counts", []) == []
    _close_window(app, window)


def test_fluoro_async_cache_only_merged_spill_matches_sync_and_preserves_live_spill(
    monkeypatch,
    tmp_path,
) -> None:
    app = _app()
    first_path = _uint16_single_channel_file(tmp_path / "analysis-merge-a.tif", 1200)
    second_path = _uint16_single_channel_file(tmp_path / "analysis-merge-b.tif", 3600)
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    panel.IMAGE_CACHE_BYTE_BUDGET = 1
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    merged = panel.merge_images_as_channels([str(first["uid"]), str(second["uid"])])
    merged_uid = str(merged["uid"])
    live_spill = panel._spilled_image_paths[merged_uid]
    expected_pixels = panel.image_data(merged_uid).copy()

    baseline = FluorescencePanel.from_dict(deepcopy(panel.to_dict()))
    baseline.IMAGE_CACHE_BYTE_BUDGET = 1
    baseline.borrow_readonly_image_data(panel)
    expected = baseline.create_roi_intensity_measurement(
        merged_uid,
        "rectangle",
        {"x": 2, "y": 2, "w": 8, "h": 7},
        [0],
    )
    baseline.dispose()

    workspace = window.fluoro_workspace
    workspace.set_panel(panel)
    workspace.roi_x.setValue(2)
    workspace.roi_y.setValue(2)
    workspace.roi_w.setValue(8)
    workspace.roi_h.setValue(7)
    snapshot_spills: list[Path] = []
    worker_threads: list[int] = []
    gui_thread = int(QThread.currentThreadId())
    original_borrow = FluorescencePanel.borrow_readonly_image_data
    original_measure = FluorescencePanel.create_roi_intensity_measurement

    def capture_borrow(self, source):
        original_borrow(self, source)
        snapshot_spills.append(self._spilled_image_paths[merged_uid])

    def capture_measure(self, *args, **kwargs):
        worker_threads.append(int(QThread.currentThreadId()))
        return original_measure(self, *args, **kwargs)

    monkeypatch.setattr(FluorescencePanel, "borrow_readonly_image_data", capture_borrow)
    monkeypatch.setattr(FluorescencePanel, "create_roi_intensity_measurement", capture_measure)
    monkeypatch.setattr(
        fluoro_panel_module,
        "import_fluorescence_image",
        lambda _path: (_ for _ in ()).throw(AssertionError("cache-only analysis decoded a source")),
    )
    workspace._add_roi()
    _wait_for_fluoro_analysis(app, window, "roi-intensity")

    actual = panel.analysis["measurements"][-1]
    assert len(worker_threads) == 1
    assert worker_threads[0] != gui_thread
    assert actual["results_table"] == expected["results_table"]
    assert actual["params"] == expected["params"]
    assert actual["results_summary"] == expected["results_summary"]
    assert np.array_equal(panel.image_data(merged_uid), expected_pixels)
    assert panel._spilled_image_paths[merged_uid] == live_spill
    assert live_spill.is_file()
    assert snapshot_spills and snapshot_spills[0] != live_spill
    assert not snapshot_spills[0].exists()
    _close_window(app, window)
    assert not live_spill.exists()


def test_fluoro_readonly_borrow_never_adopts_under_budget_copy_over_live_spill(tmp_path) -> None:
    first_path = _uint16_single_channel_file(tmp_path / "borrow-a.tif", 1200)
    second_path = _uint16_single_channel_file(tmp_path / "borrow-b.tif", 3600)
    third_path = _uint16_single_channel_file(tmp_path / "borrow-c.tif", 7200)
    panel = FluorescencePanel(panel_id="borrow-live")
    panel.IMAGE_CACHE_BYTE_BUDGET = 800
    first = panel.import_image(first_path)
    second = panel.import_image(second_path)
    merged = panel.merge_images_as_channels([str(first["uid"]), str(second["uid"])])
    merged_uid = str(merged["uid"])
    panel.import_image(third_path)
    live_spill = panel._spilled_image_paths[merged_uid]

    snapshot = FluorescencePanel.from_dict(deepcopy(panel.to_dict()))
    snapshot.IMAGE_CACHE_BYTE_BUDGET = panel.IMAGE_CACHE_BYTE_BUDGET
    snapshot.borrow_readonly_image_data(panel)
    snapshot_link = snapshot._spilled_image_paths[merged_uid]
    borrowed_pixels = snapshot.image_data(merged_uid)

    assert borrowed_pixels is not None
    assert merged_uid not in snapshot.worker_owned_cached_image_uids()
    assert panel._spilled_image_paths[merged_uid] == live_spill
    assert live_spill.is_file()
    assert not snapshot_link.exists()
    snapshot.dispose()
    assert live_spill.is_file()
    panel.dispose()
    assert not live_spill.exists()


def test_fluoro_async_overlap_no_selection_uses_effective_fallback_channels(tmp_path) -> None:
    app = _app()
    image_path = _uint16_multichannel_file(tmp_path / "overlap-fallback.ome.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    record = panel.import_image(image_path)
    image_uid = str(record["uid"])
    workspace = window.fluoro_workspace
    workspace.set_panel(panel)
    workspace.overlap_channel_list.clearSelection()
    workspace.threshold_method_combo.setCurrentText("manual")
    workspace.threshold_value_spin.setValue(1000.0)

    baseline = FluorescencePanel.from_dict(deepcopy(panel.to_dict()))
    baseline.borrow_readonly_image_data(panel)
    expected_overlap = baseline.compute_channel_overlap(image_uid, [0, 1], "manual", 1000.0)
    expected_profile = baseline.line_profile(image_uid, [0, 1])
    baseline.dispose()
    chart_threads: list[int] = []
    original_chart = workspace.canvas.draw_line_profile_chart

    def capture_chart(rows):
        chart_threads.append(int(QThread.currentThreadId()))
        return original_chart(rows)

    workspace.canvas.draw_line_profile_chart = capture_chart
    workspace._plot_overlap()
    _wait_for_fluoro_analysis(app, window, "overlap-profile")

    assert panel.analysis["channel_overlap"] == expected_overlap
    assert panel.analysis["line_profile"] == expected_profile
    assert chart_threads == [int(QThread.currentThreadId())]
    assert "Discarded" not in window.statusBar().currentMessage()
    _close_window(app, window)


def test_fluoro_ai_count_sample_and_drawn_profile_all_run_off_gui_thread(monkeypatch, tmp_path) -> None:
    app = _app()
    image_path = _uint16_multichannel_file(tmp_path / "operation-affinity.ome.tif")
    window = MainWindow(resources_dir=Path(__file__).resolve().parents[1] / "resources")
    window.set_module("fluoro")
    panel = window._active_fluoro_panel()
    panel.import_image(image_path)
    workspace = window.fluoro_workspace
    workspace.set_panel(panel)
    gui_thread = int(QThread.currentThreadId())
    calls: dict[str, list[int]] = {name: [] for name in ("ai", "count", "sample", "profile", "review")}

    def wrap_method(name, original):
        def wrapped(self, *args, **kwargs):
            calls[name].append(int(QThread.currentThreadId()))
            return original(self, *args, **kwargs)

        return wrapped

    monkeypatch.setattr(
        FluorescencePanel,
        "run_ai_assisted_analysis",
        wrap_method("ai", FluorescencePanel.run_ai_assisted_analysis),
    )
    monkeypatch.setattr(
        FluorescencePanel,
        "count_channel_objects",
        wrap_method("count", FluorescencePanel.count_channel_objects),
    )
    monkeypatch.setattr(
        FluorescencePanel,
        "count_like_sample",
        wrap_method("sample", FluorescencePanel.count_like_sample),
    )
    monkeypatch.setattr(
        FluorescencePanel,
        "line_profile",
        wrap_method("profile", FluorescencePanel.line_profile),
    )
    original_review = workspace._count_review_image_for_panel

    def capture_review(panel_arg, image_uid, rows):
        calls["review"].append(int(QThread.currentThreadId()))
        return original_review(panel_arg, image_uid, rows)

    monkeypatch.setattr(FluorescenceWorkspace, "_count_review_image_for_panel", staticmethod(capture_review))
    monkeypatch.setattr(workspace, "_show_count_preview_dialog", lambda *_args, **_kwargs: None)

    workspace._run_ai_assisted()
    _wait_for_fluoro_analysis(app, window, "ai")
    workspace._count_spots()
    _wait_for_fluoro_analysis(app, window, "count")
    workspace._add_drawn_roi(
        "sample_spot",
        {"x": 3, "y": 2, "w": 5, "h": 4, "source_channel_index": 0},
    )
    _wait_for_fluoro_analysis(app, window, "sample-count")
    workspace.overlap_channel_list.clearSelection()
    workspace._add_drawn_roi(
        "profile_line",
        {"x1": 0, "y1": 2, "x2": 15, "y2": 2},
    )
    _wait_for_fluoro_analysis(app, window, "line-profile")

    assert all(calls[name] for name in calls)
    assert all(thread_id != gui_thread for values in calls.values() for thread_id in values)
    _close_window(app, window)


def _apply_snapshot(live_panel: Any, result_panel: Any) -> None:
    live_panel.data = result_panel.data
    live_panel.analysis = result_panel.analysis
    live_panel.audit_log = result_panel.audit_log
    live_panel.modified_at = result_panel.modified_at
    if hasattr(result_panel, "_dab_analysis_cache"):
        live_panel._dab_analysis_cache = result_panel._dab_analysis_cache
    if hasattr(result_panel, "_brightfield_analysis_mask_cache"):
        live_panel._merge_brightfield_analysis_mask_cache(result_panel._brightfield_analysis_mask_cache)
    if hasattr(result_panel, "_transient_preview_state"):
        live_panel._transient_preview_state = result_panel._transient_preview_state


def _ihc_controller(panel_ref: list[IHCPanel], service: AnalysisJobService, applied: list[Any], errors: list[Exception], notices: list[str]):
    return IHCAnalysisController(
        get_panel=lambda: panel_ref[0],
        get_expert_settings=lambda: {},
        get_assisted_settings=lambda: {},
        get_foolproof_settings=lambda: {},
        get_current_mode=lambda: "foolproof",
        get_overlay_opacity=lambda: 0.35,
        get_current_task_type=lambda: "ihc_dab",
        get_examples_for_image=lambda _uid: [],
        clear_examples_for_image=lambda _uid: None,
        clear_example_click_mode=lambda: None,
        refresh_measurement_views=lambda _panel, measurement, **_kwargs: applied.append(
            (measurement, QThread.currentThreadId())
        ),
        notify_user=lambda _title, message: notices.append(message),
        report_failure=lambda _title, _context, exc: errors.append(exc),
        run_async=service.start,
        apply_panel_snapshot=_apply_snapshot,
    )


def _brightfield_controller(
    panel_ref: list[BrightfieldPanel],
    service: AnalysisJobService,
    applied: list[Any],
    errors: list[Exception],
    notices: list[str],
    refresh_measurement_views=None,
):
    refresh = refresh_measurement_views or (
        lambda _panel, measurement, **_kwargs: applied.append(
            (measurement, QThread.currentThreadId())
        )
    )
    return BrightfieldTraditionalAnalysisController(
        get_panel=lambda: panel_ref[0],
        cancel_preview_debounce=lambda: None,
        get_current_mode=lambda: "foolproof",
        get_current_stain_type=lambda: "H&E",
        get_expert_settings=lambda: {},
        get_assisted_settings=lambda: {},
        get_foolproof_settings=lambda: {},
        refresh_measurement_views=refresh,
        notify_user=lambda _title, message: notices.append(message),
        report_failure=lambda _title, _context, exc: errors.append(exc),
        run_async=service.start,
        apply_panel_snapshot=_apply_snapshot,
    )


def test_analysis_worker_keeps_qt_event_loop_heartbeat_alive(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    started = threading.Event()
    release = threading.Event()
    completed: list[Any] = []
    ticks: list[float] = []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))

    def slow_work():
        started.set()
        release.wait(0.35)
        return "done"

    assert service.start(
        key="ihc",
        work=slow_work,
        on_success=completed.append,
        on_error=lambda exc: pytest.fail(str(exc)),
        is_current=lambda: True,
    )
    timer.start()
    _wait_until(app, started.is_set)
    heartbeat_deadline = time.monotonic() + 0.12
    while time.monotonic() < heartbeat_deadline:
        app.processEvents()
        time.sleep(0.005)
    assert len(ticks) >= 3
    release.set()
    _wait_until(app, lambda: completed == ["done"])
    timer.stop()
    service.shutdown()


def test_analysis_service_rejects_duplicate_same_key_and_restores_busy_state() -> None:
    app = _app()
    service = AnalysisJobService()
    started = threading.Event()
    release = threading.Event()
    completed: list[Any] = []
    busy_states: list[bool] = []
    service.busy_changed.connect(lambda _key, busy: busy_states.append(busy))

    def slow_work():
        started.set()
        release.wait(1.0)
        return "only-result"

    assert service.start(
        key="brightfield",
        work=slow_work,
        on_success=completed.append,
        on_error=lambda exc: pytest.fail(str(exc)),
        is_current=lambda: True,
    )
    assert not service.start(
        key="brightfield",
        work=lambda: "duplicate",
        on_success=completed.append,
        on_error=lambda exc: pytest.fail(str(exc)),
        is_current=lambda: True,
    )
    assert service.is_busy("brightfield")
    _wait_until(app, started.is_set)
    release.set()
    _wait_until(app, lambda: completed == ["only-result"])

    assert not service.is_busy("brightfield")
    assert busy_states == [True, False]
    service.shutdown()


@pytest.mark.parametrize("kind", ["ihc", "brightfield"])
def test_async_analysis_applies_expected_result_on_gui_thread(monkeypatch, kind: str) -> None:
    app = _app()
    service = AnalysisJobService()
    panel = _ihc_panel() if kind == "ihc" else _brightfield_panel()
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    main_thread_id = QThread.currentThreadId()

    def fake_ihc(self, settings=None, guided_mode="foolproof"):
        measurement = {"measurement_uuid": "ihc-result", "image_uid": "image-1", "results": {"positive": 7}}
        self.analysis["ihc_measurements"] = [measurement]
        return measurement

    def fake_brightfield(self, settings=None, guided_mode="foolproof"):
        measurement = {"measurement_uuid": "bf-result", "image_uid": "image-1", "results": {"area": 11}}
        self.analysis["brightfield_measurements"] = [measurement]
        return measurement

    if kind == "ihc":
        monkeypatch.setattr(IHCPanel, "auto_analyze_dab", fake_ihc)
        controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    else:
        monkeypatch.setattr(BrightfieldPanel, "auto_analyze_he", fake_brightfield)
        monkeypatch.setattr(
            BrightfieldPanel,
            "render_measurement_overlay",
            lambda self, **_kwargs: np.zeros((2, 2, 3), dtype=np.uint8),
        )
        controller = _brightfield_controller(panel_ref, service, applied, errors, notices)

    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices))

    assert not errors
    assert not notices
    assert applied[0][0]["results"] in ({"positive": 7}, {"area": 11})
    assert int(applied[0][1]) == int(main_thread_id)
    assert panel.analysis["ihc_measurements" if kind == "ihc" else "brightfield_measurements"]
    service.shutdown()


def test_stale_edit_rejects_result_without_mutating_live_panel(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    panel = _ihc_panel()
    panel_ref = [panel]
    release = threading.Event()
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []

    def slow_ihc(self, settings=None, guided_mode="foolproof"):
        release.wait(1.0)
        measurement = {"measurement_uuid": "stale-result", "image_uid": "image-1", "results": {"positive": 9}}
        self.analysis["ihc_measurements"] = [measurement]
        return measurement

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", slow_ihc)
    controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    panel.data["edited_while_running"] = True
    release.set()
    _wait_until(app, lambda: bool(notices) or bool(errors))

    assert not errors
    assert not applied
    assert panel.analysis.get("ihc_measurements", []) == []
    assert notices
    service.shutdown()


def test_stale_panel_switch_rejects_brightfield_result(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    first_panel = _brightfield_panel()
    second_panel = _brightfield_panel()
    panel_ref = [first_panel]
    release = threading.Event()
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []

    def slow_brightfield(self, settings=None, guided_mode="foolproof"):
        release.wait(1.0)
        measurement = {"measurement_uuid": "stale-result", "image_uid": "image-1", "results": {"area": 13}}
        self.analysis["brightfield_measurements"] = [measurement]
        return measurement

    monkeypatch.setattr(BrightfieldPanel, "auto_analyze_he", slow_brightfield)
    monkeypatch.setattr(
        BrightfieldPanel,
        "render_measurement_overlay",
        lambda self, **_kwargs: np.zeros((2, 2, 3), dtype=np.uint8),
    )
    controller = _brightfield_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    panel_ref[0] = second_panel
    release.set()
    _wait_until(app, lambda: bool(notices) or bool(errors))

    assert not errors
    assert not applied
    assert first_panel.analysis.get("brightfield_measurements", []) == []
    assert second_panel.analysis.get("brightfield_measurements", []) == []
    assert notices
    service.shutdown()


def test_async_brightfield_overlay_is_rendered_in_worker_and_passed_to_refresh(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    panel = _brightfield_panel()
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    render_thread_ids: list[Any] = []

    def fake_brightfield(self, settings=None, guided_mode="foolproof"):
        measurement = {"measurement_uuid": "bf-overlay", "image_uid": "image-1", "results": {"area": 17}}
        self.analysis["brightfield_measurements"] = [measurement]
        return measurement

    def worker_overlay(self, **_kwargs):
        render_thread_ids.append(QThread.currentThreadId())
        return np.zeros((2, 2, 3), dtype=np.uint8)

    monkeypatch.setattr(BrightfieldPanel, "auto_analyze_he", fake_brightfield)
    monkeypatch.setattr(BrightfieldPanel, "render_measurement_overlay", worker_overlay)
    controller = _brightfield_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices))

    assert not errors
    assert not notices
    assert render_thread_ids
    assert int(render_thread_ids[0]) != int(QThread.currentThreadId())
    assert applied[0][0]["measurement_uuid"] == "bf-overlay"
    service.shutdown()


def test_mainwindow_brightfield_preview_payload_avoids_gui_reanalysis(monkeypatch) -> None:
    panel = _brightfield_panel()
    measurement = {
        "measurement_uuid": "preview-only",
        "image_uid": "image-1",
        "image_name": "image.png",
        "results": {},
        "params": {},
        "roi": {},
    }
    panel.data["brightfield_images"][0]["measurements"] = [measurement]
    panel.analysis["brightfield_measurements"] = [measurement]

    class _Label:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value

    class _Button:
        def isChecked(self) -> bool:
            return False

    class _Canvas:
        def __init__(self) -> None:
            self.displayed = None

        def set_display_state(self, pixmap, **kwargs) -> None:
            self.displayed = (pixmap, kwargs)

    class _Workspace:
        current_step = 2

        def __init__(self) -> None:
            self.canvas = _Canvas()

    class _Host:
        def __init__(self) -> None:
            self.brightfield_workspace = _Workspace()
            self.cell_table_updates = []
            self.cell_settings_updates = []
            self.data_panel = type(
                "DataPanel",
                (),
                {
                    "brightfield_roi_status_label": _Label(),
                    "brightfield_roi_draw_button": _Button(),
                    "populate_bright_round_cell_results": lambda _data, result: self.cell_table_updates.append(result),
                    "set_cell_analysis_settings": lambda _data, settings: self.cell_settings_updates.append(settings),
                },
            )()
            self.status_label = _Label()
            self._brightfield_roi_rect_from_snapshot = MainWindow._brightfield_roi_rect_from_snapshot.__get__(self)

    host = _Host()

    class _Image:
        class Format:
            Format_RGB888 = object()

        def __init__(self, *_args) -> None:
            pass

        def copy(self):
            return self

    class _Pixmap:
        @staticmethod
        def fromImage(image):
            return image

    monkeypatch.setattr(main_window_module, "QImage", _Image)
    monkeypatch.setattr(main_window_module, "QPixmap", _Pixmap)

    def fail_if_gui_reanalyzes(*_args, **_kwargs):
        raise AssertionError("completion refresh re-ran Brightfield segmentation on the GUI thread")

    monkeypatch.setattr(panel, "render_measurement_overlay", fail_if_gui_reanalyzes)
    MainWindow._refresh_brightfield_preview(
        host,
        panel,
        measurement_uuid=str(measurement["measurement_uuid"]),
        preview_payload={"overlay": np.zeros((2, 2, 3), dtype=np.uint8)},
    )

    assert host.brightfield_workspace.canvas.displayed is not None
    assert host.cell_table_updates == [None]
    assert host.cell_settings_updates == [{}]


def test_brightfield_completion_callback_uses_worker_overlay_without_gui_reanalysis(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    panel = _brightfield_panel()
    panel_ref = [panel]
    errors: list[Exception] = []
    notices: list[str] = []
    refresh_calls: list[tuple[Any, dict[str, Any], Any]] = []
    render_thread_ids: list[Any] = []
    gui_thread_id = QThread.currentThreadId()

    class _Label:
        def __init__(self) -> None:
            self.value = ""

        def setText(self, value: str) -> None:
            self.value = value

    class _Button:
        def isChecked(self) -> bool:
            return False

    class _Canvas:
        def __init__(self) -> None:
            self.displayed = None

        def set_display_state(self, pixmap, **kwargs) -> None:
            self.displayed = (pixmap, kwargs)

    class _Workspace:
        current_step = 2

        def __init__(self) -> None:
            self.canvas = _Canvas()

    class _Host:
        def __init__(self) -> None:
            self.brightfield_workspace = _Workspace()
            self.cell_table_updates = []
            self.cell_settings_updates = []
            self.data_panel = type(
                "DataPanel",
                (),
                {
                    "brightfield_roi_status_label": _Label(),
                    "brightfield_roi_draw_button": _Button(),
                    "populate_bright_round_cell_results": lambda _data, result: self.cell_table_updates.append(result),
                    "set_cell_analysis_settings": lambda _data, settings: self.cell_settings_updates.append(settings),
                },
            )()
            self.status_label = _Label()
            self._brightfield_roi_rect_from_snapshot = MainWindow._brightfield_roi_rect_from_snapshot.__get__(self)

    host = _Host()

    class _Image:
        class Format:
            Format_RGB888 = object()

        def __init__(self, *_args) -> None:
            pass

        def copy(self):
            return self

    class _Pixmap:
        @staticmethod
        def fromImage(image):
            return image

    monkeypatch.setattr(main_window_module, "QImage", _Image)
    monkeypatch.setattr(main_window_module, "QPixmap", _Pixmap)

    def fake_brightfield(self, settings=None, guided_mode="foolproof"):
        measurement = {
            "measurement_uuid": "bf-completion-overlay",
            "image_uid": "image-1",
            "results": {"area": 19},
        }
        self.analysis["brightfield_measurements"] = [measurement]
        return measurement

    def worker_overlay(self, **_kwargs):
        thread_id = QThread.currentThreadId()
        render_thread_ids.append(thread_id)
        if int(thread_id) == int(gui_thread_id):
            raise AssertionError("completion refresh re-ran Brightfield segmentation on the GUI thread")
        return np.zeros((2, 2, 3), dtype=np.uint8)

    monkeypatch.setattr(BrightfieldPanel, "auto_analyze_he", fake_brightfield)
    monkeypatch.setattr(BrightfieldPanel, "render_measurement_overlay", worker_overlay)

    def refresh_measurement_views(refresh_panel, measurement, **kwargs):
        refresh_calls.append((measurement, kwargs, QThread.currentThreadId()))
        MainWindow._refresh_brightfield_preview(
            host,
            refresh_panel,
            measurement_uuid=str(measurement["measurement_uuid"]),
            preview_payload=kwargs["preview_payload"],
        )

    controller = _brightfield_controller(
        panel_ref,
        service,
        [],
        errors,
        notices,
        refresh_measurement_views=refresh_measurement_views,
    )
    controller.run_analysis()
    _wait_until(app, lambda: bool(refresh_calls) or bool(errors) or bool(notices))

    assert not errors
    assert not notices
    assert len(render_thread_ids) == 1
    assert len(refresh_calls) == 1
    assert int(refresh_calls[0][2]) == int(gui_thread_id)
    assert refresh_calls[0][1]["preview_payload"]["overlay"].shape == (2, 2, 3)
    assert host.brightfield_workspace.canvas.displayed is not None
    assert host.cell_table_updates == [None]
    assert host.cell_settings_updates == [{}]
    service.shutdown()


def test_worker_exception_restores_busy_state_without_project_mutation(monkeypatch) -> None:
    app = _app()
    service = AnalysisJobService()
    panel = _ihc_panel()
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    busy_states: list[bool] = []
    service.busy_changed.connect(lambda _key, busy: busy_states.append(busy))

    def failing_ihc(self, settings=None, guided_mode="foolproof"):
        raise RuntimeError("controlled analysis failure")

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", failing_ihc)
    controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(errors))

    assert str(errors[0]) == "controlled analysis failure"
    assert busy_states[:1] == [True]
    assert busy_states[-1:] == [False]
    assert not applied
    assert panel.analysis.get("ihc_measurements", []) == []
    service.shutdown()


def test_async_ihc_matches_existing_synchronous_numeric_results(tmp_path) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "ihc-equivalence.png")
    sync_panel = IHCPanel(panel_id="sync")
    async_panel = IHCPanel(panel_id="async")
    sync_panel.import_image(str(image_path))
    async_panel.import_image(str(image_path))
    sync_result = sync_panel.auto_analyze_dab(settings={}, guided_mode="foolproof")

    service = AnalysisJobService()
    panel_ref = [async_panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices), timeout=15.0)

    assert not errors
    assert not notices
    async_result = applied[0][0]
    assert async_result["results"] == sync_result["results"]
    assert async_result["params"] == sync_result["params"]
    assert async_result["roi"] == sync_result["roi"]
    service.shutdown()


def test_async_brightfield_matches_existing_synchronous_numeric_results(tmp_path) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "brightfield-equivalence.png")
    sync_panel = BrightfieldPanel(panel_id="sync")
    async_panel = BrightfieldPanel(panel_id="async")
    sync_panel.import_image(str(image_path), stain_type="H&E")
    async_panel.import_image(str(image_path), stain_type="H&E")
    sync_result = sync_panel.auto_analyze_he(settings={}, guided_mode="foolproof")

    service = AnalysisJobService()
    panel_ref = [async_panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    controller = _brightfield_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices), timeout=15.0)

    assert not errors
    assert not notices
    async_result = applied[0][0]
    assert async_result["results"] == sync_result["results"]
    assert async_result["params"] == sync_result["params"]
    assert async_result["roi"] == sync_result["roi"]
    service.shutdown()


def test_async_brightfield_worker_and_first_live_overlay_reuse_analysis_mask(tmp_path, monkeypatch) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "brightfield-mask-cache.png")
    panel = BrightfieldPanel(panel_id="async-cache")
    panel.import_image(str(image_path), stain_type="H&E")
    service = AnalysisJobService()
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    analyze_calls: list[dict[str, Any]] = []
    original_analyze = brightfield_panel_module.analyze_brightfield_image

    def counting_analyze(rgb, settings, *, roi_mask=None):
        analyze_calls.append(dict(settings))
        return original_analyze(rgb, settings, roi_mask=roi_mask)

    monkeypatch.setattr(brightfield_panel_module, "analyze_brightfield_image", counting_analyze)
    controller = _brightfield_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices), timeout=15.0)

    assert not errors
    assert not notices
    assert len(analyze_calls) == 1
    measurement = applied[0][0]
    overlay = panel.render_measurement_overlay(measurement_uuid=str(measurement["measurement_uuid"]))
    assert overlay.shape[:2] == (12, 12)
    assert len(analyze_calls) == 1
    service.shutdown()


def test_ihc_worker_cache_is_available_for_first_overlay_render(tmp_path, monkeypatch) -> None:
    app = _app()
    image_path = _image_file(tmp_path / "ihc-cache.png")
    panel = IHCPanel(panel_id="cache")
    panel.import_image(str(image_path))
    service = AnalysisJobService()
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    controller.run_analysis()
    _wait_until(app, lambda: bool(applied) or bool(errors) or bool(notices), timeout=15.0)
    assert not errors
    measurement = applied[0][0]

    def fail_if_recomputed(*_args, **_kwargs):
        raise AssertionError("IHC overlay recomputed instead of using the worker cache")

    monkeypatch.setattr(panel, "_analyze_dab_threshold", fail_if_recomputed)
    overlay = panel.render_measurement_overlay(measurement_uuid=str(measurement["measurement_uuid"]))
    assert overlay.shape[:2] == (12, 12)
    service.shutdown()


def test_mainwindow_repeated_ihc_async_cache_merge_remains_bounded() -> None:
    live_panel = IHCPanel(panel_id="live-cache")

    for index in range(20):
        result_panel = IHCPanel(panel_id=f"result-cache-{index}")
        result_panel._dab_analysis_cache[f"measurement-{index}"] = {
            "cache_key": f"cache-{index}",
            "analysis": {"mask": np.ones((2, 2), dtype=bool)},
        }
        MainWindow._apply_analysis_panel_snapshot(live_panel, result_panel)

    assert len(live_panel._dab_analysis_cache) == 16
    assert list(live_panel._dab_analysis_cache) == [f"measurement-{index}" for index in range(4, 20)]


def test_success_attaches_captured_inputs_error_keeps_prior_result(monkeypatch) -> None:
    from core.measurement_freshness import build_analysis_inputs, saved_inputs_from_record

    app = _app()
    service = AnalysisJobService()
    panel = _ihc_panel()
    prior = {"measurement_uuid": "prior", "image_uid": "image-1", "results": {"positive": 1}}
    panel.analysis["ihc_measurements"] = [prior]
    panel_ref = [panel]
    applied: list[Any] = []
    errors: list[Exception] = []
    notices: list[str] = []
    captured = build_analysis_inputs(method="ihc", settings={"mode": "foolproof", "sensitivity": "medium"}, image_uid="image-1")

    def boom(self, settings=None, guided_mode="foolproof"):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(IHCPanel, "auto_analyze_dab", boom)
    controller = _ihc_controller(panel_ref, service, applied, errors, notices)
    controller._capture_analysis_inputs = lambda: captured
    controller.run_analysis()
    _wait_until(app, lambda: bool(errors) or bool(notices) or bool(applied))
    assert errors
    assert not applied
    assert panel.analysis["ihc_measurements"] == [prior]
    assert saved_inputs_from_record(prior) is None
    service.shutdown()
