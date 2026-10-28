from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QAbstractItemView, QCheckBox, QLabel, QListWidget, QMessageBox

from core.utils.i18n import set_language
from core.image_browser import ScaleBarState
from ui.panels.image_browser_workspace import ImageBrowserWorkspace
from ui.widgets.image_browser_canvas import ImageBrowserCanvas


def _app() -> QApplication:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _rgb(width: int, height: int, color: tuple[int, int, int] = (12, 24, 36)) -> np.ndarray:
    array = np.empty((height, width, 3), dtype=np.uint8)
    array[..., 0] = color[0]
    array[..., 1] = color[1]
    array[..., 2] = color[2]
    return array


def _rgb_to_qimage(rgb: np.ndarray) -> QImage:
    contiguous = np.ascontiguousarray(rgb)
    height, width = contiguous.shape[:2]
    return QImage(contiguous.data, width, height, 3 * width, QImage.Format.Format_RGB888).copy()


@dataclass
class FakeBrowserImage:
    path: Path
    rgb: np.ndarray
    metadata: dict[str, object] = field(default_factory=dict)
    channel_names: list[str] = field(default_factory=lambda: ["R", "G", "B"])

    @property
    def size(self) -> tuple[int, int]:
        return int(self.rgb.shape[1]), int(self.rgb.shape[0])


class FakeBrowserCore:
    def __init__(self, images: dict[Path, FakeBrowserImage], *, failing: set[Path] | None = None) -> None:
        self.images = images
        self.failing = set(failing or ())
        self.load_calls: list[Path] = []
        self.thumbnail_calls: list[Path] = []
        self.discover_calls: list[Path] = []

    def discover_image_files(self, folder: str | Path) -> list[Path]:
        root = Path(folder)
        self.discover_calls.append(root)
        return sorted((path for path in self.images if path.parent == root), key=lambda path: path.name.casefold())

    def load_browser_image(self, path: str | Path) -> FakeBrowserImage:
        resolved = Path(path)
        self.load_calls.append(resolved)
        if resolved in self.failing:
            raise OSError("thumbnail failed")
        return self.images[resolved]

    def thumbnail_qimage(self, image: FakeBrowserImage, max_size: QSize = QSize(176, 132)) -> QImage:
        if image.path in self.failing:
            raise OSError("thumbnail failed")
        return _rgb_to_qimage(image.rgb).scaled(
            max_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    def load_browser_thumbnail(self, path: str | Path, max_size: QSize = QSize(176, 132)) -> QImage:
        resolved = Path(path)
        self.thumbnail_calls.append(resolved)
        if resolved in self.failing:
            raise OSError("thumbnail failed")
        return self.thumbnail_qimage(self.images[resolved], max_size)

    def rgb_to_qimage(self, rgb: np.ndarray) -> QImage:
        return _rgb_to_qimage(rgb)

    def render_snapshot(
        self,
        image: FakeBrowserImage,
        show_scale_bar: bool,
        length_um: float,
        manual_um_per_px: float | None = None,
        *,
        scale_bar_state: ScaleBarState | None = None,
    ) -> QImage:
        snapshot = _rgb_to_qimage(image.rgb)
        if not show_scale_bar:
            return snapshot
        scale = (
            scale_bar_state.um_per_px
            if scale_bar_state is not None and scale_bar_state.um_per_px and scale_bar_state.um_per_px > 0
            else manual_um_per_px if manual_um_per_px and manual_um_per_px > 0 else image.metadata.get("pixel_size_um")
        )
        try:
            scale_value = float(scale)
        except (TypeError, ValueError):
            scale_value = 0.0
        if scale_value <= 0:
            raise ValueError("Scale bar needs µm/px calibration")
        painter = QPainter(snapshot)
        painter.fillRect(snapshot.width() - 120, snapshot.height() - 24, 100, 8, QColor("#ffffff"))
        painter.end()
        del length_um
        return snapshot


def _workspace(core: FakeBrowserCore, *, thread_pool=None) -> ImageBrowserWorkspace:
    return ImageBrowserWorkspace(
        loader=core.load_browser_image,
        discover=core.discover_image_files,
        thumbnail_loader=core.load_browser_thumbnail,
        snapshot=core.render_snapshot,
        to_qimage=core.rgb_to_qimage,
        thread_pool=thread_pool,
    )


def _flush(workspace: ImageBrowserWorkspace, app: QApplication) -> None:
    workspace.thread_pool.waitForDone(2000)
    app.processEvents()
    workspace.thread_pool.waitForDone(2000)
    app.processEvents()


def _write_dummy(path: Path, payload: bytes = b"source") -> Path:
    path.write_bytes(payload)
    return path


def test_empty_center_text_is_chinese_prompt() -> None:
    app = _app()
    set_language("zh")
    workspace = _workspace(FakeBrowserCore({}))
    try:
        workspace.retranslate()
        labels = [label.text() for label in workspace.canvas.findChildren(QLabel)]
        assert "打开文件夹以浏览图片" in labels
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
        set_language("en")


def test_open_folder_populates_sidebar_and_keeps_it_visible(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("en")
    first = _write_dummy(tmp_path / "a.png")
    second = _write_dummy(tmp_path / "b.png")
    core = FakeBrowserCore(
        {
            first: FakeBrowserImage(first, _rgb(64, 48, (40, 80, 120))),
            second: FakeBrowserImage(second, _rgb(80, 60, (200, 10, 10)), metadata={"pixel_size_um": 0.5}),
        }
    )
    workspace = _workspace(core)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: pytest_fail("modal error"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest_fail("modal error"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: pytest_fail("modal error"))
    try:
        workspace.show()
        workspace.side_panel.show()
        app.processEvents()
        workspace.open_folder(tmp_path)
        assert workspace.thumbnail_list.count() == 2
        assert [workspace.thumbnail_list.item(index).text() for index in range(2)] == ["a.png", "b.png"]
        assert workspace.side_panel.isVisible()
        assert workspace.thumbnail_list.viewMode() == QListWidget.ViewMode.IconMode

        workspace.thumbnail_list.setCurrentRow(1)
        _flush(workspace, app)

        assert workspace.side_panel.isVisible()
        assert workspace.canvas._source_pixmap is not None
        assert workspace.canvas._source_pixmap.size() == QSize(80, 60)
        assert "b.png" in workspace.metadata_label.text()
        assert "80" in workspace.metadata_label.text()
        assert "60" in workspace.metadata_label.text()
        assert "0.5" in workspace.calibration_label.text()

        load_count = len(core.load_calls)
        workspace.select_image(second)
        _flush(workspace, app)
        assert len(core.load_calls) == load_count
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
        set_language("en")


def test_open_folder_uses_thumbnail_loader_without_full_image_reads(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png")
    second = _write_dummy(tmp_path / "b.png")
    core = FakeBrowserCore(
        {
            first: FakeBrowserImage(first, _rgb(64, 48)),
            second: FakeBrowserImage(second, _rgb(80, 60)),
        }
    )
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        _flush(workspace, app)

        assert core.load_calls == []
        assert sorted(core.thumbnail_calls, key=lambda path: path.name) == [first, second]

        workspace.select_image(second)
        _flush(workspace, app)
        assert core.load_calls == [second]
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


class _RecordingThreadPool:
    def __init__(self, max_threads: int = 7) -> None:
        self.started: list[tuple[str, int]] = []
        self.runnables: list[object] = []
        self._max_threads = max_threads

    def start(self, runnable, priority: int = 0) -> None:
        self.started.append((runnable._kind, priority))
        self.runnables.append(runnable)

    def maxThreadCount(self) -> int:
        return self._max_threads


def test_workspace_caps_owned_pool_and_prioritizes_selected_image(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "a.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(16, 12))})
    workspace = _workspace(core)
    try:
        assert workspace.thread_pool.maxThreadCount() == 2
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()

    external = _RecordingThreadPool()
    workspace = _workspace(core, thread_pool=external)
    try:
        workspace.open_folder(tmp_path)
        workspace.select_image(source)

        thumbnail_priorities = [priority for kind, priority in external.started if kind == "thumbnail"]
        image_priorities = [priority for kind, priority in external.started if kind == "image"]
        assert thumbnail_priorities
        assert image_priorities
        assert max(image_priorities) > max(thumbnail_priorities)
        assert external.maxThreadCount() == 7
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def pytest_fail(message: str) -> None:
    raise AssertionError(message)


def test_failed_thumbnail_keeps_other_items_and_does_not_mutate_sources(tmp_path) -> None:
    app = _app()
    set_language("en")
    good = _write_dummy(tmp_path / "good.png", b"good-bytes")
    bad = _write_dummy(tmp_path / "bad.png", b"bad-bytes")
    before = {good: good.read_bytes(), bad: bad.read_bytes()}
    core = FakeBrowserCore(
        {
            bad: FakeBrowserImage(bad, _rgb(32, 32)),
            good: FakeBrowserImage(good, _rgb(40, 30, (1, 2, 3))),
        },
        failing={bad},
    )
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        _flush(workspace, app)
        assert workspace.thumbnail_list.count() == 2
        names = [workspace.thumbnail_list.item(index).text() for index in range(2)]
        assert names == ["bad.png", "good.png"]
        failed_item = workspace.thumbnail_list.item(0)
        good_item = workspace.thumbnail_list.item(1)
        assert not failed_item.icon().isNull()
        assert not good_item.icon().isNull()

        workspace.thumbnail_list.setCurrentRow(1)
        _flush(workspace, app)
        assert good.read_bytes() == before[good]
        assert bad.read_bytes() == before[bad]
        assert workspace.canvas._source_pixmap is not None
        assert workspace.canvas._source_pixmap.size() == QSize(40, 30)
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_export_snapshot_writes_native_png_and_optional_scale_bar(tmp_path) -> None:
    app = _app()
    set_language("en")
    source = _write_dummy(tmp_path / "calibrated.png")
    image = FakeBrowserImage(source, _rgb(160, 120, (9, 18, 27)), metadata={"pixel_size_um": 0.25})
    core = FakeBrowserCore({source: image})
    workspace = _workspace(core)
    export_path = tmp_path / "snapshot.png"
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.export_snapshot(export_path)
        saved = QImage(str(export_path))
        assert saved.size() == QSize(160, 120)

        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(50.0)
        workspace.um_per_px_spin.setValue(0.0)
        bar_path = tmp_path / "with-bar.png"
        workspace.export_snapshot(bar_path)
        with_bar = QImage(str(bar_path))
        assert with_bar.size() == QSize(160, 120)
        source_corner = saved.pixelColor(saved.width() - 70, saved.height() - 20)
        bar_corner = with_bar.pixelColor(with_bar.width() - 70, with_bar.height() - 20)
        assert bar_corner != source_corner
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_empty_folder_shows_empty_state_without_modal(tmp_path, monkeypatch) -> None:
    app = _app()
    set_language("zh")
    core = FakeBrowserCore({})
    workspace = _workspace(core)
    monkeypatch.setattr(QMessageBox, "critical", lambda *args, **kwargs: pytest_fail("modal error"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: pytest_fail("modal error"))
    monkeypatch.setattr(QMessageBox, "information", lambda *args, **kwargs: pytest_fail("modal error"))
    try:
        workspace.show()
        workspace.side_panel.show()
        workspace.retranslate()
        workspace.open_folder(tmp_path)
        app.processEvents()
        assert workspace.thumbnail_list.count() == 0
        assert workspace.error_label.isVisible()
        assert "支持" in workspace.error_label.text() or "没有" in workspace.error_label.text()
        assert isinstance(workspace.scale_bar_checkbox, QCheckBox)
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
        set_language("en")


def test_switching_to_new_folder_clears_previous_image(tmp_path) -> None:
    app = _app()
    first_folder = tmp_path / "first"
    second_folder = tmp_path / "second"
    first_folder.mkdir()
    second_folder.mkdir()
    first = _write_dummy(first_folder / "first.png")
    second = _write_dummy(second_folder / "second.png")
    core = FakeBrowserCore(
        {
            first: FakeBrowserImage(first, _rgb(24, 18)),
            second: FakeBrowserImage(second, _rgb(30, 20)),
        }
    )
    workspace = _workspace(core)
    try:
        workspace.open_folder(first_folder)
        workspace.select_image(first)
        _flush(workspace, app)
        assert workspace.canvas._source_pixmap is not None

        workspace.open_folder(second_folder)

        assert workspace.canvas._source_pixmap is None
        assert not workspace.canvas.placeholder.isHidden()
        assert workspace.canvas.title.text() == "Open a folder to browse images"
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_thumbnail_selection_does_not_double_start_full_read(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "a.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(16, 12))})
    external = _RecordingThreadPool()
    workspace = _workspace(core, thread_pool=external)
    try:
        workspace.open_folder(tmp_path)
        item = workspace.thumbnail_list.item(0)
        workspace.thumbnail_list.setCurrentItem(item)
        workspace.thumbnail_list.itemClicked.emit(item)

        image_jobs = [kind for kind, _priority in external.started if kind == "image"]
        assert image_jobs == ["image"]
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_rapid_selection_ignores_stale_full_read_result(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png")
    second = _write_dummy(tmp_path / "b.png")
    image_a = FakeBrowserImage(first, _rgb(64, 48, (1, 2, 3)))
    image_b = FakeBrowserImage(second, _rgb(80, 60, (9, 8, 7)))
    core = FakeBrowserCore({first: image_a, second: image_b})
    external = _RecordingThreadPool()
    workspace = _workspace(core, thread_pool=external)
    try:
        workspace.open_folder(tmp_path)
        workspace.select_image(first)
        workspace.select_image(second)

        generation = workspace._generation
        workspace._on_worker_succeeded(generation, "image", str(second), image_b)
        assert workspace.canvas._source_pixmap is not None
        assert workspace.canvas._source_pixmap.size() == QSize(80, 60)
        assert "b.png" in workspace.metadata_label.text()

        workspace._on_worker_succeeded(generation, "image", str(first), image_a)
        assert workspace.canvas._source_pixmap.size() == QSize(80, 60)
        assert "b.png" in workspace.metadata_label.text()
        assert "a.png" not in workspace.metadata_label.text()
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_retranslate_keeps_loaded_image_filename_title(tmp_path) -> None:
    app = _app()
    set_language("en")
    source = _write_dummy(tmp_path / "cells.png")
    core = FakeBrowserCore({source: FakeBrowserImage(source, _rgb(16, 12))})
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.select_image(source)
        _flush(workspace, app)
        assert workspace.canvas.title.text() == "cells.png"

        set_language("zh")
        workspace.retranslate()
        assert workspace.canvas.title.text() == "cells.png"
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
        set_language("en")


def test_workspace_scale_bar_controls_update_live_state_without_rereading_image(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "calibrated.png")
    image = FakeBrowserImage(source, _rgb(160, 120), metadata={"pixel_size_um": 0.25})
    core = FakeBrowserCore({source: image})
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        assert isinstance(workspace.canvas, ImageBrowserCanvas)
        assert workspace.thumbnail_list.selectionMode() == QAbstractItemView.SelectionMode.ExtendedSelection

        load_count = len(core.load_calls)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(24.0)
        workspace.line_width_spin.setValue(7.0)
        workspace.font_size_spin.setValue(23)
        workspace.bold_checkbox.setChecked(True)
        workspace.italic_checkbox.setChecked(True)
        workspace.set_line_color("#123456")
        workspace.set_text_color("#654321")
        app.processEvents()

        state = workspace.scale_bar_state_for(source)
        assert state.visible is True
        assert state.length_um == 24.0
        assert state.line_thickness_px == 7.0
        assert state.font_size_px == 23.0
        assert state.bold is True
        assert state.italic is True
        assert state.line_color == "#123456"
        assert state.text_color == "#654321"
        assert workspace.canvas.scale_bar_state == state
        assert len(core.load_calls) == load_count
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_workspace_clicking_scale_bar_uses_editable_default_when_metadata_is_missing(tmp_path) -> None:
    app = _app()
    source = _write_dummy(tmp_path / "uncalibrated.png")
    image = FakeBrowserImage(source, _rgb(160, 120), metadata={})
    core = FakeBrowserCore({source: image})
    workspace = _workspace(core)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)

        workspace.scale_bar_checkbox.setChecked(True)
        app.processEvents()

        state = workspace.scale_bar_state_for(source)
        assert workspace.um_per_px_spin.value() == 0.0
        assert state.um_per_px is None
        assert state.visible is False
        assert workspace.canvas.scale_bar_state.visible is False
        assert not workspace.error_label.isHidden()
        assert workspace.error_label.text()
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_workspace_scale_bar_state_is_per_image_and_export_passes_same_state(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png")
    second = _write_dummy(tmp_path / "b.png")
    images = {
        first: FakeBrowserImage(first, _rgb(80, 60), metadata={"pixel_size_um": 0.5}),
        second: FakeBrowserImage(second, _rgb(90, 70), metadata={"pixel_size_um": 0.25}),
    }
    core = FakeBrowserCore(images)
    seen_states: list[ScaleBarState] = []

    def snapshot(image, show_scale_bar, length_um, manual_um_per_px=None, *, scale_bar_state=None):
        del show_scale_bar, length_um, manual_um_per_px
        seen_states.append(scale_bar_state)
        return _rgb_to_qimage(image.rgb)

    workspace = ImageBrowserWorkspace(
        loader=core.load_browser_image,
        discover=core.discover_image_files,
        thumbnail_loader=core.load_browser_thumbnail,
        snapshot=snapshot,
        to_qimage=core.rgb_to_qimage,
    )
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(18.0)
        first_state = workspace.scale_bar_state_for(first)
        workspace.thumbnail_list.setCurrentRow(1)
        _flush(workspace, app)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.length_spin.setValue(42.0)
        second_state = workspace.scale_bar_state_for(second)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)

        assert workspace.scale_bar_state_for(first).length_um == 18.0
        assert workspace.scale_bar_state_for(second).length_um == 42.0
        assert workspace.canvas.scale_bar_state.length_um == 18.0
        assert workspace.export_snapshot(tmp_path / "same-state.png") is True
        assert seen_states and seen_states[-1] is workspace.scale_bar_state_for(first)
        assert seen_states[-1] is not second_state
        assert first_state.length_um == 18.0
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_workspace_sends_selected_candidates_in_order_and_derives_visible_scale_bar(tmp_path) -> None:
    app = _app()
    first = _write_dummy(tmp_path / "a.png", b"a-source")
    second = _write_dummy(tmp_path / "b.png", b"b-source")
    images = {
        first: FakeBrowserImage(first, _rgb(80, 60), metadata={"pixel_size_um": 0.5}),
        second: FakeBrowserImage(second, _rgb(90, 70), metadata={"pixel_size_um": 0.25}),
    }
    core = FakeBrowserCore(images)
    workspace = _workspace(core)
    received: list[list[str]] = []
    structured: list[list[dict]] = []
    workspace.send_to_fluorescence_requested.connect(received.append)
    workspace.send_to_fluorescence_transfer_requested.connect(structured.append)
    try:
        workspace.open_folder(tmp_path)
        workspace.thumbnail_list.setCurrentRow(0)
        _flush(workspace, app)
        workspace.thumbnail_list.item(0).setSelected(True)
        workspace.thumbnail_list.setCurrentRow(1)
        _flush(workspace, app)
        workspace.scale_bar_checkbox.setChecked(True)
        workspace.thumbnail_list.item(1).setSelected(True)
        workspace.send_selected_to_fluorescence()
        _flush(workspace, app)

        assert received and [Path(path) for path in received[-1]] == [first, second]
        assert not any(Path(path).parent == workspace.transfer_dir for path in received[-1])
        assert first.read_bytes() == b"a-source"
        assert second.read_bytes() == b"b-source"
        payloads = structured[-1]
        assert [item["path"] for item in payloads] == [str(first), str(second)]
        assert "calibration" in payloads[0] and "display" in payloads[0]
        assert "center_x_px" not in payloads[0]["calibration"]
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()


def test_workspace_blocks_folder_change_only_while_candidate_snapshot_is_pending(tmp_path) -> None:
    app = _app()
    source_folder = tmp_path / "source"
    other_folder = tmp_path / "other"
    source_folder.mkdir()
    other_folder.mkdir()
    source = _write_dummy(source_folder / "a.png")
    core = FakeBrowserCore(
        {source: FakeBrowserImage(source, _rgb(80, 60), metadata={"pixel_size_um": 0.5})}
    )
    pool = _RecordingThreadPool()
    workspace = _workspace(core, thread_pool=pool)
    received: list[list[str]] = []
    structured: list[list[dict]] = []
    workspace.send_to_fluorescence_requested.connect(received.append)
    workspace.send_to_fluorescence_transfer_requested.connect(structured.append)
    try:
        workspace.open_folder(source_folder)
        workspace.thumbnail_list.item(0).setSelected(True)
        state = workspace.scale_bar_state_for(source)
        state.visible = True
        state.um_per_px = 0.5

        workspace._candidate_transfer_busy = True
        workspace.choose_folder_button.setEnabled(False)
        workspace.open_folder(other_folder)
        assert workspace._folder == source_folder

        workspace._candidate_transfer_busy = False
        workspace.choose_folder_button.setEnabled(True)
        workspace.send_selected_to_fluorescence()
        app.processEvents()

        assert workspace._candidate_transfer_busy is False
        assert workspace.choose_folder_button.isEnabled() is True
        assert not any(getattr(runnable, "_kind", "") == "candidate" for runnable in pool.runnables)
        assert received and Path(received[-1][0]) == source
        assert Path(received[-1][0]).parent != workspace.transfer_dir
        assert structured and Path(structured[-1][0]["path"]) == source
        workspace.open_folder(other_folder)
        assert workspace._folder == other_folder
    finally:
        workspace.close()
        workspace.deleteLater()
        app.processEvents()
